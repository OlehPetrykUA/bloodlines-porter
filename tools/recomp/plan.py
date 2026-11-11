import argparse
import hashlib
import heapq
import json
import os
import re
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "third_party", "xboxrecomp"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import devpaths
import discover
import pe as pefile
from recomp.translator import write_if_changed

ALIGN = 0x10000
TOP = 0x100000000
STUB_UNITS = ("binkw32", "mss32")


def rt_defines():
    text = open(os.path.join(REPO, "runtime", "rt.h")).read()
    return {k: int(v, 16) for k, v in re.findall(r"#define (RT_\w+) (0x[0-9A-Fa-f]+)u", text)}


def reserved():
    d = rt_defines()
    return [("runtime regions", 0, d["RT_VIRT_HI"]), ("harness GDT", d["RT_GDT"], d["RT_GDT"] + d["RT_GDT_SIZE"]),
            ("allocation region", d["RT_ALLOC_LO"], d["RT_ALLOC_HI"]),
            ("guest allocations, shim page and arena tail", d["RT_GUEST_TOP"], TOP)]


def span(size):
    return (size + ALIGN - 1) // ALIGN * ALIGN


def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def check(mods):
    errs, placed = [], []
    for name, m in mods.items():
        r = (m["base"], m["base"] + span(m["size"]))
        if m["base"] % ALIGN:
            errs.append(f"{name} base 0x{m['base']:08X} is not 64 KiB-aligned")
        for other, o in placed:
            if overlaps(r, o):
                errs.append(f"{name} [0x{r[0]:08X}, 0x{r[1]:08X}) overlaps {other} [0x{o[0]:08X}, 0x{o[1]:08X})")
        for what, lo, hi in reserved():
            if overlaps(r, (lo, hi)):
                errs.append(f"{name} [0x{r[0]:08X}, 0x{r[1]:08X}) overlaps the {what} [0x{lo:08X}, 0x{hi:08X})")
        if m["base"] != m["preferred"] and not m["relocs"]:
            errs.append(f"{name} moves from 0x{m['preferred']:08X} but has no .reloc directory")
        placed.append((name, r))
    return errs


def suggest(mods, order):
    taken = [(lo, hi) for _, lo, hi in reserved()]
    out = {}

    def free(r):
        return r[1] <= TOP and not any(overlaps(r, t) for t in taken)

    for name in order:
        m = mods[name]
        r = (m["preferred"], m["preferred"] + span(m["size"]))
        if free(r):
            out[name] = r[0]
            taken.append(r)
    for name in order:
        if name in out:
            continue
        m = mods[name]
        b = (m["preferred"] + ALIGN - 1) // ALIGN * ALIGN
        while not free((b, b + span(mods[name]["size"]))):
            b += ALIGN
            if b + span(m["size"]) > rt_defines()["RT_GUEST_TOP"]:
                raise SystemExit(f"layout: no free base below 0x80000000 for {name}")
        out[name] = b
        taken.append((b, b + span(m["size"])))
    return out


def load(layout_path, game):
    layout = tomllib.load(open(layout_path, "rb"))
    mods = {}
    for name, m in layout["modules"].items():
        path = os.path.join(game, m["path"])
        data = open(path, "rb").read()
        p = pefile.parse(data)
        mods[name] = {"path": path, "rel": m["path"], "base": m["base"], "preferred": p.image_base,
                      "size": p.size_of_image, "relocs": bool(p.relocs), "pe": p,
                      "sha256": hashlib.sha256(data).hexdigest()}
    return layout, mods


def dll_key(name):
    return name.lower()


def resolve(mods, by_dll, dll, sym, depth=0):
    if depth > 8:
        raise SystemExit(f"forwarder chain too long at {dll}!{sym}")
    name = by_dll.get(dll_key(dll))
    if name is None:
        return None
    p = mods[name]["pe"]
    if sym.startswith("#") and sym in p.ordinals:
        sym = p.ordinals[sym]
    if sym in p.forwarders:
        target = p.forwarders[sym]
        tdll, tsym = target.rsplit(".", 1)
        tdll += ".dll"
        r = resolve(mods, by_dll, tdll, tsym, depth + 1)
        return r if r is not None else ("forward", tdll, tsym)
    if sym not in p.exports:
        raise SystemExit(f"{dll}!{sym} is not exported by {name}")
    rva = p.exports[sym]
    return ("code" if p.is_exec_rva(rva) else "data", name, mods[name]["base"] + rva)


def ret_pops(path):
    p = pefile.parse(open(path, "rb").read())
    im = discover.Image(p, p.image_base)
    out = {}
    for name, rva in p.exports.items():
        va = p.image_base + rva
        if not im.is_code(va):
            continue
        limit = min(hi for lo, hi in im.ranges if lo <= va < hi)
        seen = discover.descend(im, va, limit)[0]
        pops = set()
        for a in seen:
            insn = im.decode(a)
            if insn.mnemonic == "ret":
                pops.add(insn.operands[0].imm if insn.operands else 0)
        if len(pops) == 1:
            out[name] = pops.pop()
    return out


def pop_of(dll, sym, scans):
    m = re.fullmatch(r"_?[A-Za-z_]\w*@(\d+)", sym)
    if m:
        return int(m.group(1)), "decorated name"
    s = scans.get(dll_key(dll), {})
    if sym in s:
        return s[sym], "ret scan of the shipped DLL"
    return None, "unknown"


def c_id(dll):
    return re.sub(r"\W", "_", dll_key(dll).rsplit(".", 1)[0])


def kahn(order, deps):
    indeg = {n: len(deps[n]) for n in order}
    users = {n: [m for m in order if n in deps[m]] for n in order}
    rank = {n: i for i, n in enumerate(order)}
    ready = [(rank[n], n) for n in order if not indeg[n]]
    heapq.heapify(ready)
    out = []
    while ready:
        _, n = heapq.heappop(ready)
        out.append(n)
        for u in users[n]:
            indeg[u] -= 1
            if not indeg[u]:
                heapq.heappush(ready, (rank[u], u))
    return out, sorted(n for n in order if n not in out)


def plan(layout_path, game, out):
    layout, mods = load(layout_path, game)
    errs = check(mods)
    if errs:
        raise SystemExit("layout: " + "; ".join(errs))
    order = list(mods)
    by_dll = {dll_key(os.path.basename(m["path"])): n for n, m in mods.items()}
    scans = {dll_key(os.path.basename(s)): ret_pops(os.path.join(game, s)) for s in layout["stub_dlls"]["paths"]}
    cells = layout["runtime"]["data_cells"]
    stubs, cell_names, slots, deps, forwards = {}, {}, {}, {n: set() for n in order}, {}
    for name in order:
        p, base = mods[name]["pe"], mods[name]["base"]
        rows = []
        for dll, sym, iat in p.imports:
            r = resolve(mods, by_dll, dll, sym)
            if r is not None and r[0] != "forward":
                kind, exporter, va = r
                deps[name].add(exporter)
                rows.append({"iat": base + iat, "dll": dll, "name": sym, "bind": kind, "va": va, "exporter": exporter})
                continue
            if r is not None:
                forwards.setdefault(name, []).append(f"{dll}!{sym} -> {r[1]}!{r[2]}")
                dll, sym = r[1], r[2]
            key = (dll_key(dll), sym)
            if "@@3" in sym:
                cell_names.setdefault(key, dll)
                rows.append({"iat": base + iat, "dll": dll, "name": sym, "bind": "cell", "key": key})
            else:
                stubs.setdefault(key, {"dll": dll, "importers": set()})["importers"].add(name)
                rows.append({"iat": base + iat, "dll": dll, "name": sym, "bind": "stub", "key": key})
        slots[name] = rows
    stub_keys = sorted(stubs)
    stub_va = {k: 0xFE000000 + 16 * i for i, k in enumerate(stub_keys)}
    cell_va = {k: cells + 4 * j for j, k in enumerate(sorted(cell_names))}
    for rows in slots.values():
        for r in rows:
            if "key" in r:
                r["va"] = (stub_va if r["bind"] == "stub" else cell_va)[r.pop("key")]
    stub_list = []
    for k in stub_keys:
        pop, src = pop_of(stubs[k]["dll"], k[1], scans)
        stub_list.append({"dll": stubs[k]["dll"], "name": k[1], "va": stub_va[k], "pop": pop, "pop_source": src,
                          "unit": c_id(k[0]) if c_id(k[0]) in STUB_UNITS else "win32",
                          "importers": sorted(stubs[k]["importers"])})
    init, cycle = kahn(order, deps)
    modules = {}
    for name in order:
        m, p = mods[name], mods[name]["pe"]
        delta = m["base"] - m["preferred"]
        tls = None
        if p.tls:
            tls = {k: v + delta for k, v in p.tls.items() if k in ("start", "end", "index")}
            tls |= {"callbacks": [c + delta for c in p.tls["callbacks"]], "zero_fill": p.tls["zero_fill"]}
        exports = {n: {"va": m["base"] + r, "kind": "code" if p.is_exec_rva(r) else "data"} for n, r in p.exports.items()}
        modules[name] = {"path": m["rel"], "sha256": m["sha256"], "base": m["base"], "preferred": m["preferred"],
                         "size": m["size"], "code_size": sum(hi - lo for lo, hi in p.exec_ranges()), "exports": exports, "ordinals": p.ordinals, "forwarders": p.forwarders,
                         "forwarded_imports": forwards.get(name, []), "tls": tls, "imports_from": sorted(deps[name]),
                         "ordinal_imports": sum(s.startswith("#") for _, s, _ in p.imports), "iat": slots[name]}
    result = {"layout": os.path.abspath(layout_path), "modules": modules, "stubs": stub_list,
              "cells": [{"dll": cell_names[k], "name": k[1], "va": cell_va[k]} for k in sorted(cell_names)],
              "init_order": init, "cycle": cycle,
              "build": {k: layout.get("build", {}).get(k, []) for k in ("optimization1", "chunk1", "o0")}}
    os.makedirs(out, exist_ok=True)
    write_if_changed(os.path.join(out, "plan.json"), json.dumps(result, indent=1))
    write_link(out, result)
    return result


def write_link(out, result):
    sd = os.path.join(out, "stubs")
    os.makedirs(sd, exist_ok=True)
    units = {}
    for i, s in enumerate(result["stubs"]):
        units.setdefault(c_id(s["dll"]), []).append((i, s))
    files = {"win32": []}
    for dll, rows in sorted(units.items()):
        lines = ['#include "rt.h"', f"const rt_import rt_stubs_{dll}[] = {{"]
        for _, s in rows:
            pop = -1 if s["pop"] is None else s["pop"]
            lines.append(f'    {{0x{s["va"]:08X}u, 0, "{s["dll"]}", "{s["name"]}", {pop}}},')
        lines.append("};")
        write_if_changed(os.path.join(sd, f"{dll}.c"), "\n".join(lines) + "\n")
        files.setdefault(dll if dll in STUB_UNITS else "win32", []).append(f"{dll}.c")
    idx = ['#include "rt.h"']
    idx += [f"extern const rt_import rt_stubs_{d}[];" for d in sorted(units)]
    idx.append("const rt_import *const rt_imports[] = {")
    pos = {d: 0 for d in units}
    for s in result["stubs"]:
        d = c_id(s["dll"])
        idx.append(f"    &rt_stubs_{d}[{pos[d]}],")
        pos[d] += 1
    idx.append("};")
    idx.append(f"const uint32_t rt_nimports = {len(result['stubs'])};")
    write_if_changed(os.path.join(sd, "index.c"), "\n".join(idx) + "\n")
    files["win32"].append("index.c")
    for u, fs in files.items():
        write_if_changed(os.path.join(sd, f"{u}.txt"), "\n".join(fs) + "\n")
    names = list(result["modules"])
    write_if_changed(os.path.join(out, "modules.txt"), "\n".join(names) + "\n")
    for k in ("optimization1", "o0"):
        write_if_changed(os.path.join(out, f"{k}.txt"), "".join(f"{v}\n" for v in result["build"][k]) or "\n")
    link = ['#include "rt.h"'] + [f"extern const rt_module rt_mod_{n};" for n in names]
    link.append("const rt_module *const rt_modules[] = {")
    link += [f"    &rt_mod_{n}," for n in names]
    link.append("};")
    link.append(f"const uint32_t rt_nmodules = {len(names)};")
    write_if_changed(os.path.join(out, "link.c"), "\n".join(link) + "\n")
    tsv = ["dll!name\tva\tpop\tpop_source\tunit\timporters"]
    for s in result["stubs"]:
        pop = "?" if s["pop"] is None else str(s["pop"])
        tsv.append(f'{s["dll"]}!{s["name"]}\t0x{s["va"]:08X}\t{pop}\t{s["pop_source"]}\t{s["unit"]}\t{",".join(s["importers"])}')
    write_if_changed(os.path.join(out, "stubs", "plan.tsv"), "\n".join(tsv) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("layout")
    ap.add_argument("--game", default=devpaths.GAME)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--suggest", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    layout, mods = load(a.layout, a.game)
    if a.suggest:
        order = ["vstdlib"] + [n for n in mods if n != "vstdlib"]
        bases = suggest(mods, order)
        for name in order:
            b = bases[name]
            print(f'[modules.{name}]\npath = "{mods[name]["rel"]}"\nbase = 0x{b:08X}\n')
        return 0
    errs = check(mods)
    if errs:
        for e in errs:
            print(f"layout: {e}")
        return 2
    hi = max(m["base"] + span(m["size"]) for m in mods.values())
    print(f"layout: {len(mods)} modules, 0 overlaps, 0 reserved-range overlaps, highest end 0x{hi:08X}")
    if a.check:
        return 0
    r = plan(a.layout, a.game, a.out)
    n = {}
    for m in r["modules"].values():
        for s in m["iat"]:
            n[s["bind"]] = n.get(s["bind"], 0) + 1
    print(f"plan: slots {n}, {len(r['stubs'])} stubs, {len(r['cells'])} cells, init order {' '.join(r['init_order'])}"
          + (f", cycle {r['cycle']}" if r["cycle"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
