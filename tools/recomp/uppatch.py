import argparse
import hashlib
import os
import struct
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pe as pefile

CELL_BASE = 0x00400800
KINDS = ("bytes", "jmp", "call", "ptr")
G2B = "m9-g2b"


class PatchError(Exception):
    pass


PLUGIN_INIS = {"vtmb-tweaks", "model-loader-fix", "protean-improved", "container-stack-fix", "griffith-frenzy-werewolf-fix",
               "history-stat-reset-fix", "subtitle-pause-fix", "console-pause-fix"}
LOADER_SECTIONS ={"loader", "modern os", "bugs", "mods", "widescreen", "ui fixes", "tweaks", "localization"}


def read_ini(path, vals=None):
    vals = {} if vals is None else vals
    if not path:
        return vals
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    loader = stem not in PLUGIN_INIS
    sec = ""
    if stem == "loader":
        vals["_vtms"] = {os.path.splitext(f)[0].lower() for f in os.listdir(os.path.dirname(os.path.abspath(path)))
                         if f.lower().endswith(".vtm")}
    for line in open(path, encoding="latin-1"):
        line = line.strip()
        if line.startswith("["):
            sec = line[1:line.find("]")].lower()
        elif "=" in line and not line.startswith(";"):
            k, v = line.split("=", 1)
            try:
                vals[f"{sec if loader else stem}/{k.strip().lower()}"] = float(v.strip().split()[0])
            except (ValueError, IndexError):
                pass
    return vals


def switch_on(ini, switch):
    sec, key = switch.split("/", 1)
    sec = sec.lower()
    if sec == "m9":
        return True
    if sec in LOADER_SECTIONS:
        return ini.get(f"{sec}/{key.lower()}", 1.0) != 0
    if "_vtms" in ini and sec not in ini["_vtms"]:
        return False
    return ini.get(f"{sec}/enabled", 1.0) != 0 and ini.get(f"{sec}/{key.lower()}", 1.0) != 0


def load_rows(path):
    paths = path if isinstance(path, list) else [path]
    rows = [r for p in paths for r in tomllib.load(open(p, "rb"))["row"]]
    rows = [r for r in rows if r.get("owner") != G2B] + [r for r in rows if r.get("owner") == G2B]
    seen = set()
    for r in rows:
        if r["id"] in seen:
            raise PatchError(f"duplicate row {r['id']}")
        seen.add(r["id"])
        if r["kind"] not in KINDS:
            raise PatchError(f"{r['id']}: kind {r['kind']}")
        if r["kind"] == "bytes" and len(bytes.fromhex(r["old"])) != len(bytes.fromhex(r["new"])):
            raise PatchError(f"{r['id']}: old and new differ in length")
        if r["kind"] != "bytes" and "cell" not in r:
            raise PatchError(f"{r['id']}: hook row without a cell")
        if "guard" in r and (len(r["guard"]) != 2 or r["guard"][1] <= 0):
            raise PatchError(f"{r['id']}: guard is [rva, len]")
        if r["kind"] == "ptr" and (len(bytes.fromhex(r["old"])) != 4 or r.get("abs") != 0 or "target" not in r
                                   or any(t in r["thunk"] for t in ("{disp}", "{resume}"))):
            raise PatchError(f"{r['id']}: a ptr row needs 4 old bytes, abs 0, a target and no {{disp}} or {{resume}}")
    cells = [r["cell"] for r in rows if "cell" in r]
    if len(cells) != len(set(cells)):
        raise PatchError("two rows share a cell")
    return rows


def reloc_blocks(p):
    data = p.data
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    opt = pe_off + 24
    ndirs = struct.unpack_from("<I", data, opt + 92)[0]
    if ndirs <= 5:
        return
    rva, size = struct.unpack_from("<II", data, opt + 96 + 8 * 5)
    end = rva + size
    while rva < end:
        page, block = struct.unpack_from("<II", data, p.rva_to_off(rva))
        if block < 8:
            break
        for j in range((block - 8) // 2):
            yield page, p.rva_to_off(rva + 8 + 2 * j)
        rva += block


def drop_relocs(p, data, lo, hi):
    n = 0
    for page, off in reloc_blocks(p):
        e = struct.unpack_from("<H", data, off)[0]
        if e >> 12 == 3 and lo <= page + (e & 0xFFF) < hi:
            struct.pack_into("<H", data, off, 0)
            n += 1
    return n


def expand(row, p, base, thunk_rva, old, t=None):
    cell = struct.pack("<I", CELL_BASE + 4 * row["cell"])
    delta = (base - p.image_base) & 0xFFFFFFFF
    disp = bytearray(old)
    for r in p.relocs:
        if row["rva"] <= r < row["rva"] + len(old):
            o = r - row["rva"]
            v = struct.unpack_from("<I", disp, o)[0]
            struct.pack_into("<I", disp, o, (v + delta) & 0xFFFFFFFF)
    out = bytearray()
    t = row["thunk"] if t is None else t
    i = 0
    resume = None
    targets = []
    while i < len(t):
        if t[i] == "{":
            j = t.index("}", i)
            tok = t[i + 1:j]
            i = j + 1
            if tok == "call":
                out += b"\xff\x15" + cell
            elif tok == "jmp":
                out += b"\xff\x25" + cell
            elif tok == "disp":
                out += disp
            elif tok == "resume":
                resume = row["rva"] + len(old)
                rel = (resume - (thunk_rva + len(out) + 5)) & 0xFFFFFFFF
                out += b"\xe9" + struct.pack("<I", rel)
            elif tok == "entry":
                targets.append(thunk_rva + len(out))
            elif tok.startswith("rel:") or tok.startswith("mark:"):
                target = int(tok.split(":")[1], 16)
                if p.rva_to_off(target) is None or not p.is_exec_rva(target):
                    raise PatchError(f"{row['id']}: rel target 0x{target:x} is not in an executable section")
                targets.append(target)
                if tok.startswith("rel:"):
                    out += struct.pack("<I", (target - (thunk_rva + len(out) + 4)) & 0xFFFFFFFF)
            else:
                raise PatchError(f"{row['id']}: unknown thunk token {tok}")
        else:
            out += bytes.fromhex(t[i:i + 2])
            i += 2
    return bytes(out), resume, targets


def patch_module(name, mod_rows, src, base, ini):
    p = pefile.parse(open(src, "rb").read())
    data = bytearray(p.data)
    sec = next(s for s in p.sections if s.executable and s.vsize < s.raw_size)
    slack_lo = sec.rva + sec.vsize
    slack_hi = sec.rva + sec.raw_size
    cursor = slack_lo
    extra = []
    applied = []
    by_id = {r["id"]: r for r in mod_rows}
    for row in mod_rows:
        old = bytes.fromhex(row["old"])
        if row.get("owner") == G2B:
            lo, n = row.get("guard", [row["rva"], len(old)])
            spans = [(by_id[i]["rva"], by_id[i]["rva"] + len(bytes.fromhex(by_id[i]["old"])), i) for i in applied
                     if by_id[i].get("owner") != G2B]
            hit = next((i for a, b, i in spans if (a < lo + n and lo < b) or (a < row["rva"] + len(old) and row["rva"] < b)), None)
            if hit:
                print(f"uppatch: {row['id']} yields to {hit}")
                continue
        off = p.rva_to_off(row["rva"])
        if off is None or bytes(data[off:off + len(old)]) != old:
            raise PatchError(f"{row['id']}: old bytes at {name}+0x{row['rva']:x} are "
                             f"{bytes(data[off:off + len(old)]).hex() if off else 'unmapped'}, want {row['old']}")
        inside = sorted(r - row["rva"] for r in p.relocs if row["rva"] <= r < row["rva"] + len(old))
        if (row["kind"] != "bytes" or row.get("keep_reloc")) and inside != ([row["abs"]] if "abs" in row else []):
            raise PatchError(f"{row['id']}: relocations at {inside} in the site, abs is {row.get('abs')}")
        if row["kind"] == "bytes":
            if not any(switch_on(ini, s) for s in row["switch"]):
                continue
            if row.get("owner") == G2B and not row.get("keep_reloc"):
                drop_relocs(p, data, row["rva"], row["rva"] + len(old))
            data[off:off + len(old)] = bytes.fromhex(row["new"])
            applied.append(row["id"])
            continue
        if row["kind"] == "ptr" and struct.unpack("<I", old)[0] != p.image_base + row["target"]:
            raise PatchError(f"{row['id']}: old pointer is not the image base plus target 0x{row['target']:x}")
        if row["kind"] == "call" and (len(old) != 5 or old[0] != 0xE8):
            raise PatchError(f"{row['id']}: a call row needs a 5-byte E8 site")
        if len(old) < 5 and row["kind"] != "ptr":
            raise PatchError(f"{row['id']}: a hook site needs 5 bytes")
        code, resume, targets = expand(row, p, base, cursor, old)
        toff = p.rva_to_off(cursor)
        if cursor + len(code) > slack_hi or any(data[toff:toff + len(code)]):
            raise PatchError(f"{row['id']}: no zero slack for a {len(code)}-byte thunk in {name}")
        data[toff:toff + len(code)] = code
        if "cave" in row:
            ccode, cres, ctargets = expand(row, p, base, row["cave"], old, row["cave_thunk"])
            coff = p.rva_to_off(row["cave"])
            if (coff is None or not p.is_exec_rva(row["cave"]) or any(b not in (0x90, 0xCC) for b in data[coff:coff + len(ccode)])
                    or any(row["cave"] <= r < row["cave"] + len(ccode) for r in p.relocs)):
                raise PatchError(f"{row['id']}: cave at 0x{row['cave']:x} is not {len(ccode)} bytes of unrelocated padding")
            data[coff:coff + len(ccode)] = ccode
            extra.append(base + row["cave"])
            extra.extend(base + t for t in ctargets)
            if cres is not None:
                extra.append(base + cres)
        if row["kind"] == "ptr":
            data[off:off + 4] = struct.pack("<I", p.image_base + cursor)
            extra.append(base + cursor)
            extra.append(base + row["target"])
            extra.extend(base + t for t in targets)
            cursor += len(code)
            applied.append(row["id"])
            continue
        rel = (cursor - (row["rva"] + 5)) & 0xFFFFFFFF
        site = (b"\xe9" if row["kind"] == "jmp" else b"\xe8") + struct.pack("<I", rel)
        site += b"\x90" * (len(old) - 5)
        drop_relocs(p, data, row["rva"], row["rva"] + len(old))
        data[off:off + len(old)] = site
        extra.append(base + cursor)
        if len(old) == 5 and old[0] in (0xE8, 0xE9):
            extra.append(base + ((row["rva"] + 5 + struct.unpack("<i", old[1:])[0]) & 0xFFFFFFFF))
        if resume is not None:
            extra.append(base + resume)
        extra.extend(base + t for t in targets)
        cursor += len(code)
        applied.append(row["id"])
    return bytes(data), extra, applied


def patch_all(game, layout, rows, ini_path, out):
    ini = {}
    for p in ini_path if isinstance(ini_path, list) else [ini_path]:
        read_ini(p, ini)
    mods = tomllib.load(open(layout, "rb"))["modules"]
    by_mod = {}
    for r in rows:
        if r["module"] not in mods:
            raise PatchError(f"{r['id']}: module {r['module']} is not in the layout")
        by_mod.setdefault(r["module"], []).append(r)
    h = hashlib.sha256()
    result = {}
    up = [r for r in rows if r.get("owner") != G2B]
    for name in sorted(by_mod):
        src = os.path.join(game, mods[name]["path"])
        raw = open(src, "rb").read()
        if any(r["module"] == name for r in up):
            h.update(hashlib.sha256(raw).digest())
        data, extra, applied = patch_module(name, by_mod[name], src, mods[name]["base"], ini)
        dst = os.path.join(out, "patched", mods[name]["path"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "wb").write(data)
        mdir = os.path.join(out, name)
        os.makedirs(mdir, exist_ok=True)
        open(os.path.join(mdir, "extra-entries.txt"), "w").write("".join(f"{v:x}\n" for v in sorted(set(extra))))
        result[name] = {"path": dst, "extra": sorted(set(extra)), "applied": applied}
    for r in up:
        h.update(json_bytes(r))
    h.update(bytes(int(switch_on(ini, s)) for r in up if r["kind"] == "bytes" for s in r["switch"]))
    if up:
        open(os.path.join(out, "up.sha256"), "w").write(h.hexdigest() + "\n")
    return result


def json_bytes(r):
    return repr(sorted(r.items())).encode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch-set", required=True, action="append")
    ap.add_argument("--up-ini", action="append")
    ap.add_argument("--game", required=True)
    ap.add_argument("--layout", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        res = patch_all(a.game, a.layout, load_rows(a.patch_set), a.up_ini, a.out)
    except PatchError as e:
        print(f"uppatch error: {e}")
        return 2
    for name, r in sorted(res.items()):
        print(f"uppatch {name}: {len(r['applied'])} rows, {len(r['extra'])} extra entries -> {r['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
