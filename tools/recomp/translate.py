import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "third_party", "xboxrecomp"))

import discover
import lifts
import pe as pefile
import xflags
from recomp import config as rconfig
import recomp.translator as rtranslator
from recomp.translator import BatchTranslator, write_if_changed

SHIM_BASE = 0xFE000000
INTRINSICS = {"__ftol": "ftol", "__chkstk": "chkstk", "__alloca_probe": "chkstk", "_stricmp": "stricmp"}
KIND = {"code": 0, "data": 1, "stub": 2, "shim": 2, "cell": 3}


def load_names(path, delta):
    names = {}
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            a, kind, name, size = line.rstrip("\n").split("\t")
            names[(int(a, 16) + delta) & 0xFFFFFFFF] = (name, kind, int(size))
    return names


def load_intrinsics(path, module, img, image_base):
    rows = {}
    if not path or not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8").read().splitlines()[1:]:
        m, rva, name, size, digest = line.split("\t")
        if m != module:
            continue
        rva, size = int(rva, 16), int(size)
        if hashlib.sha256(img[rva:rva + size]).hexdigest() != digest:
            print(f"translate error: intrinsics.tsv row {module} {name} 0x{rva:X} does not match the image")
            raise SystemExit(2)
        rows[rva] = name
    return rows


def load_extra(path):
    if not path or not os.path.exists(path):
        return set()
    return {int(x, 16) for x in open(path).read().split()}


def bind_imports(p, img, base, cells):
    img = bytearray(img)
    table = []
    for i, (dll, sym, iat) in enumerate(p.imports):
        kind = "cell" if "@@3" in sym else "shim"
        va = cells + 4 * i if kind == "cell" else SHIM_BASE + 16 * i
        img[iat:iat + 4] = va.to_bytes(4, "little")
        table.append({"dll": dll, "name": sym, "iat": base + iat, "va": va, "bind": kind})
    return bytes(img), table


def bind_plan(img, base, slots):
    img = bytearray(img)
    for s in slots:
        img[s["iat"] - base:s["iat"] - base + 4] = s["va"].to_bytes(4, "little")
    return bytes(img)


CHUNK = re.compile(r"recomp_(\d{4}|stubs_unresolved)\.c")
LOCAL_EBP = re.compile(r"^\s*(?:volatile\s+)?u?int32_t\s+[^=;()\n]*\bebp\b", re.M)


def global_ebp(text, path):
    text = re.sub(r"^(\s*)uint32_t ebp(?: = 0)?;", r"\1", text, flags=re.M)
    text = re.sub(r"^(\s*)ebp = g_ebp;", r"\1", text, flags=re.M)
    text = re.sub(r"^(\s*)g_ebp = ebp;", r"\1", text, flags=re.M)
    text = re.sub(r"^(\s*)ebp = g_seh_ebp;", r"\1", text, flags=re.M)
    if LOCAL_EBP.search(text):
        raise SystemExit(f"local ebp survived in {path}")
    return text


def c_str(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def write_module_c(path, name, img, base, p, slots, intrinsics, ranges, standalone, mod, fnames):
    out = ['#include "rt.h"',
           f"rt_fn rt_lookup_{name}(uint32_t va);",
           f"int rt_dispatch_init_{name}(void);",
           f'static const char image_sha256[] = "{hashlib.sha256(img).hexdigest()}";',
           "static const unsigned char image[] = {",
           '#embed "../image.bin"',
           "};",
           "static const rt_slot slots[] = {"]
    out += [f'    {{0x{s["iat"]:08X}u, 0x{s["va"]:08X}u, {KIND[s["bind"]]}, "{c_str(s["dll"])}", "{c_str(s["name"])}"}},'
            for s in slots] or ["    {0, 0, 0, 0, 0},"]
    out.append("};")
    out.append("static const rt_intrinsic intrinsics[] = {")
    out += [f'    {{0x{va:08X}u, "{n}"}},' for va, n in sorted(intrinsics.items())] or ["    {0, 0},"]
    out.append("};")
    for va, n in sorted(intrinsics.items()):
        out.append(f"void rt_intrinsic_{n}(void);")
        out.append(f"void sub_{va:08X}(void) {{ rt_intrinsic_{n}(); }}")
    deps = mod["imports_from"] if mod else []
    out.append("static const char *const deps[] = {")
    out += [f'    "{d}",' for d in deps] or ["    0,"]
    out.append("};")
    out.append("static const rt_fname fnames[] = {")
    out += [f'    {{0x{va:08X}u, {chr(34) + c_str(n) + chr(34) if n else "0"}}},' for va, n in fnames] or ["    {0, 0},"]
    out.append("};")
    tls = (mod or {}).get("tls") or {}
    if not mod and p.tls:
        tls = {k: p.tls[k] + base - p.image_base for k in ("start", "end", "index")} | {"zero_fill": p.tls["zero_fill"]}
    out += [f"const rt_module rt_mod_{name} = {{",
            f'    "{name}", 0x{base:08X}u, 0x{len(img):X}u, 0x{base + p.entry_rva:08X}u,',
            f"    0x{min(r[0] for r in ranges):08X}u, 0x{max(r[1] for r in ranges):08X}u,",
            f"    image, image_sha256, rt_lookup_{name}, rt_dispatch_init_{name},",
            f"    intrinsics, {len(intrinsics)}, slots, {len(slots)},",
            f'    "{c_str(mod["path"]) if mod else name + ".dll"}", deps, {len(deps)}, fnames, {len(fnames)},',
            f"    0x{tls.get('start', 0):08X}u, 0x{tls.get('end', 0):08X}u, 0x{tls.get('index', 0):08X}u, "
            f"0x{tls.get('zero_fill', 0):X}u,",
            "};"]
    if standalone:
        out.append("static const rt_import imports[] = {")
        out += [f'    {{0x{s["va"]:08X}u, 0x{s["iat"]:08X}u, "{c_str(s["dll"])}", "{c_str(s["name"])}", -1}},'
                for s in slots]
        out.append("};")
        out.append("const rt_import *const rt_imports[] = {")
        out += [f"    &imports[{i}]," for i in range(len(slots))]
        out.append("};")
        out.append(f"const uint32_t rt_nimports = {len(slots)};")
        out.append(f"const rt_module *const rt_modules[] = {{&rt_mod_{name}}};")
        out.append("const uint32_t rt_nmodules = 1;")
    write_if_changed(path, "\n".join(out) + "\n")


def stamp(paths, extra):
    h = hashlib.sha256()
    for p in paths:
        walk = os.walk(p) if os.path.isdir(p) else [(os.path.dirname(p), [], [os.path.basename(p)])]
        for root, dirs, files in walk:
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                fp = os.path.join(root, f)
                if os.path.isfile(fp) and not f.endswith(".pyc"):
                    h.update(fp.encode() + b"\0" + open(fp, "rb").read())
    h.update(extra.encode())
    return h.hexdigest()


def classify(funcs, sites, module):
    untranslated, per_func = [], {}
    for va, (text, cat) in sorted(sites.items()):
        f = discover.owner(funcs, va)
        if f is None or va not in funcs[f]["insns"]:
            continue
        untranslated.append({"module": module, "va": f"0x{va:08X}", "mnemonic": text,
                             "function": f"sub_{f:08X}", "category": cat})
        per_func.setdefault(f, {}).setdefault(cat, []).append(f"{text.split(' ')[0]}@0x{va:08X}")
    return untranslated, per_func


def status_of(f, funcs, intrinsics, owned, cats):
    if f in intrinsics:
        return "translated", f"runtime intrinsic {intrinsics[f]}"
    if f in owned:
        return "skipped", "merged into the body of the function that owns it"
    if funcs[f]["unbounded_tables"]:
        return "skipped", "jump table without a readable bound at " + ", ".join(
            f"0x{v:08X}" for v in funcs[f]["unbounded_tables"])
    if "unimplemented" in cats:
        return "skipped", "unimplemented instructions: " + ", ".join(cats["unimplemented"][:4])
    notes = []
    if "fault-as-original" in cats:
        notes.append("software interrupt becomes a runtime abort, as the original raises an exception: "
                     + ", ".join(cats["fault-as-original"]))
    if "simd-trap" in cats:
        notes.append(f"{len(cats['simd-trap'])} SIMD instructions trap: " + ", ".join(cats["simd-trap"][:4]))
    return "translated", "; ".join(notes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("--layout")
    ap.add_argument("--plan")
    ap.add_argument("--module", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--names")
    ap.add_argument("--intrinsics", default=os.path.join(HERE, "intrinsics.tsv"))
    ap.add_argument("--extra-entries")
    ap.add_argument("--chunk", type=int, default=40)
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    stamp_path = os.path.join(a.out, "inputs.sha256")
    mod = None
    if a.plan:
        mod = json.load(open(a.plan))["modules"][a.module]
        key = stamp([a.dll, HERE, os.path.join(REPO, "third_party", "xboxrecomp"), a.names or "",
                     a.intrinsics, a.extra_entries or ""], json.dumps([mod, a.chunk], sort_keys=True))
        if os.path.exists(stamp_path) and open(stamp_path).read() == key:
            print(f"{a.module}: unchanged")
            return 0
        base = mod["base"]
    else:
        layout = tomllib.load(open(a.layout, "rb"))
        base = layout["modules"][a.module]["base"]
    p = pefile.parse(open(a.dll, "rb").read())
    im = discover.Image(p, base)
    funcs, interior, tables, reloc_targets = discover.discover(im, load_extra(a.extra_entries))
    if mod:
        slots = mod["iat"]
        img = bind_plan(im.img, base, slots)
    else:
        img, slots = bind_imports(p, im.img, base, layout["runtime"]["data_cells"])
    names = load_names(a.names, base - p.image_base)
    rows_in = load_intrinsics(a.intrinsics, a.module, p.mapped(p.image_base), p.image_base)
    intrinsic_names = {base + rva: n for rva, n in rows_in.items() if base + rva in funcs}
    intrinsics = {va: INTRINSICS[n] for va, n in intrinsic_names.items()}

    gen = os.path.join(a.out, "gen")
    os.makedirs(gen, exist_ok=True)
    image_path = os.path.join(a.out, "image.bin")
    if not os.path.exists(image_path) or open(image_path, "rb").read() != img:
        open(image_path, "wb").write(img)

    sections = [rconfig.Section(s.name, base + s.rva, max(s.vsize, s.raw_size), s.rva,
                                max(s.vsize, s.raw_size), s.executable) for s in p.sections]
    rconfig._install(sections, base + p.entry_rva, 0, a.dll)

    fjson = [{"start": f"0x{f:08X}", "end": f"0x{info['end']:08X}", "name": f"sub_{f:08X}",
              "size": info["end"] - f, "detection_method": "call_target"}
             for f, info in sorted(funcs.items())]
    fpath = os.path.join(a.out, "functions.json")
    write_if_changed(fpath, json.dumps(fjson, indent=1))

    written = set()

    def write_chunk(path, text):
        written.add(os.path.basename(path))
        return write_if_changed(path, global_ebp(text, path) if CHUNK.fullmatch(os.path.basename(path)) else text)

    rtranslator.write_if_changed = write_chunk
    sites = lifts.install()
    log = io.StringIO()
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        bt = BatchTranslator(image_path, fpath, output_dir=gen, seh_prolog=None, seh_epilog=None,
                             back_edge_yield=True)
        xflags.install(bt.translator.lifter, xflags.Scan(im, funcs), funcs)
        func_list = sorted(bt.func_db.items())
        stats = bt.translate_batch_split(func_list, gen, chunk_size=a.chunk, manual=set(intrinsics))
    write_if_changed(os.path.join(a.out, "translate.log"), log.getvalue())

    chunks = sorted(n for n in written if CHUNK.fullmatch(n))
    write_if_changed(os.path.join(gen, "chunks.txt"), "\n".join(chunks) + "\n")
    cross = xflags.report([open(os.path.join(gen, n)).read() for n in chunks])
    if cross["missing"] or cross["fallback_mismatch"]:
        raise SystemExit("; ".join(cross["missing"][:5] + [f"fallback count differs in {f}"
                                                           for f in cross["fallback_mismatch"][:5]]))

    for mnem, addrs in (getattr(bt.translator.lifter, "unimplemented", {}) or {}).items():
        for va in addrs:
            sites.setdefault(va, (mnem, "fault-as-original" if mnem == "int" else "unimplemented"))
    untranslated, per_func = classify(funcs, sites, a.module)
    owned = bt.translator.owned_function_starts
    exports = {base + r: n for n, r in p.exports.items()}
    rows = []
    for f, info in sorted(funcs.items()):
        status, reason = status_of(f, funcs, intrinsics, owned, per_func.get(f, {}))
        rows.append({"va": f"0x{f:08X}", "c_name": f"sub_{f:08X}", "status": status, "reason": reason,
                     "seed": "+".join(info["kinds"]), "export": exports.get(f, ""),
                     "ghidra": names.get(f, ("", "", 0))[0]})
    ghidra_only = []
    for va, (name, kind, size) in sorted(names.items()):
        if va in funcs:
            continue
        own = discover.owner(funcs, va)
        why = (f"inside the body of sub_{own:08X}" if own else
               "no relocation, direct call, jump or export reaches it")
        ghidra_only.append({"va": f"0x{va:08X}", "ghidra": name, "reason": why})
    counts, seeds = {}, {}
    for u in untranslated:
        counts[u["category"]] = counts.get(u["category"], 0) + 1
    for info in funcs.values():
        for k in info["kinds"]:
            seeds[k] = seeds.get(k, 0) + 1
    words = [int.from_bytes(img[r:r + 4], "little") for r in p.relocs]
    report = {
        "module": a.module, "base": f"0x{base:08X}", "functions": len(rows),
        "translated": sum(r["status"] == "translated" for r in rows),
        "intrinsic": len(intrinsics),
        "skipped": sum(r["status"] == "skipped" for r in rows),
        "seeds": seeds,
        "reloc_targets_in_code": len(reloc_targets),
        "reloc_targets_not_function_starts": {f"0x{k:08X}": v for k, v in interior.items()},
        "relocated_words_outside_image": sum(not (base <= w < base + p.size_of_image) for w in words),
        "jump_tables": len(tables),
        "unbounded_jump_tables": [f"0x{v:08X}" for f in sorted(funcs) for v in funcs[f]["unbounded_tables"]],
        "writes_into_code": [f"0x{v:08X}" for f in sorted(funcs) for v in funcs[f]["text_writes"]],
        "untranslated_counts": counts, "untranslated": untranslated,
        "rows": rows, "ghidra_not_function_starts": ghidra_only,
        "imports": slots, "stats": {k: v for k, v in stats.items() if isinstance(v, (int, str))},
        "lift_counts": getattr(lifts, "COUNTS", {}),
        "cross_flags": cross,
    }
    if mod:
        data_vas = {v["va"] for v in mod["exports"].values() if v["kind"] == "data"}
        report |= {"data_imports": [s for s in slots if s["bind"] == "data"], "tls": mod["tls"],
                   "ordinal_imports": mod["ordinal_imports"], "forwarders": mod["forwarders"],
                   "forwarded_imports": mod["forwarded_imports"],
                   "data_exports_seeded": sorted(f"0x{v:08X}" for v in data_vas & set(funcs))}
    write_if_changed(os.path.join(a.out, "report.json"), json.dumps(report, indent=1))
    fnames = [(f, exports.get(f) or intrinsic_names.get(f, "")) for f in sorted(funcs)]
    write_module_c(os.path.join(gen, "module.c"), a.module, img, base, p, slots, intrinsics, im.ranges, not mod, mod,
                   fnames)
    rt_layout = {"base": base, "size": p.size_of_image, "entry": base + p.entry_rva,
                 "exports": {n: base + r for n, r in p.exports.items()}, "imports": slots,
                 "code": [[lo, hi] for lo, hi in im.ranges], "tls": mod["tls"] if mod else None}
    write_if_changed(os.path.join(a.out, "module.json"), json.dumps(rt_layout, indent=1))
    if mod:
        write_if_changed(stamp_path, key)
    print(f"{a.module}: {report['functions']} functions, {report['translated']} translated, "
          f"{report['skipped']} skipped, untranslated {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
