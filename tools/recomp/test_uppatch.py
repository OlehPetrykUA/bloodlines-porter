import copy
import glob
import hashlib
import math
import os
import re
import struct
import sys
import tempfile
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_all
import pe as pefile
import uppatch

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
GAME = build_all.GAME
LAYOUT = os.path.join(REPO, "layout.toml")
PATCHES = os.path.join(REPO, "tools", "up", "patches.toml")
FIXES_C = os.path.join(REPO, "runtime", "up_fixes.c")
ASPECT = os.path.join(REPO, "tools", "m9", "aspect.toml")
G2B_C = os.path.join(REPO, "runtime", "g2b_engine.c")
G2B_ROW = re.compile(r'\{"(g2b_\w+)",\s*"(\w+)",\s*(0x[0-9a-f]+),\s*G_(\w+),\s*"([0-9a-f]*)",\s*"([0-9a-f]*)",\s*(\d+),\s*(-?\d+),\s*&?(\w+),'
                     r'\s*(0x[0-9a-f]+),\s*(0x[0-9a-f]+|\d+)\},')
G2B_SHIM = re.compile(r'static const rt_shim (\w+) = \{"m9", "\w+", (\d+),')
MODES = [(640, 480), (800, 600), (1024, 768), (1280, 1024), (1600, 1200), (1920, 1080), (2560, 1080), (3440, 1440), (5120, 1440), (600, 400)]
REQUESTS = [(3440, 1440), (5120, 1440), (1920, 1080), (1280, 1024)]
KIND = {"K_BYTES": "bytes", "K_JMP": "jmp", "K_CALL": "call", "K_PTR": "ptr"}
ROW = re.compile(r'\{"(\w+)", "([^"]+)", (?:"([^"]+)"|NULL), "(\w+)", (0x[0-9a-f]+), (K_\w+), "([0-9a-f]*)", "([0-9a-f]*)", (\d+), (-?\d+), [^,}]*(?:, (0x[0-9a-f]+))?\},')

fails = 0


def check(ok, name, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f": {detail}"))
    fails += not ok


def c_rows():
    return {m[0]: m for m in ROW.findall(open(FIXES_C).read())}


def parity(rows):
    c = c_rows()
    check(set(c) == {r["id"] for r in rows}, "patches.toml and up_fixes.c have the same row ids",
          f"toml-only {sorted({r['id'] for r in rows} - set(c))} c-only {sorted(set(c) - {r['id'] for r in rows})}")
    for r in rows:
        m = c.get(r["id"])
        if not m:
            continue
        sw = [s for s in (m[1], m[2]) if s]
        ok = (sw == r["switch"] and m[3] == r["module"] and int(m[4], 16) == r["rva"] and KIND[m[5]] == r["kind"]
              and m[6] == r["old"] and (m[7] == r.get("new", "")) and int(m[8]) == r.get("cell", 0) and int(m[9]) == r.get("abs", -1)
              and (int(m[10], 16) if m[10] else None) == r.get("target"))
        check(ok, f"row {r['id']} matches between toml and C", f"toml {r} c {m}")


SHIM_POP = re.compile(r'(?:static )?(?:const )?rt_shim (\w+) = \{"up", "\w+", (\d+),')
ROW_SHIM = re.compile(r'\{"(\w+)", "[^"]+", (?:"[^"]+"|NULL), "\w+", 0x[0-9a-f]+, K_\w+, "[0-9a-f]*", "[0-9a-f]*", \d+, -?\d+, &?(\w+)(?:, 0x[0-9a-f]+)?\}')


def stack_balance(rows):
    src = open(FIXES_C).read() + "".join(open(f).read() for f in sorted(glob.glob(os.path.join(REPO, "runtime", "up_plugins*.c"))))
    pops = {m[0]: int(m[1]) for m in SHIM_POP.findall(src)}
    shim_of = dict(ROW_SHIM.findall(open(FIXES_C).read()))
    for r in rows:
        if "{call}" not in r.get("thunk", "") and "{jmp}" not in r.get("thunk", ""):
            continue
        pop = pops.get(shim_of.get(r["id"]))
        check(pop == r["pop"], f"row {r['id']} pop matches its shim", f"toml {r['pop']} shim {pop}")
        if "{call}" not in r["thunk"] or "{disp}" in r["thunk"] or r["thunk"].endswith("c3"):
            continue
        pre, post = r["thunk"].split("{call}")
        pre = re.sub(r"\{[^}]*\}", "", pre)
        depth = sum(1 for i in range(0, len(pre), 2) if pre[i:i + 2] in ("50", "51", "52", "53", "54", "55", "56", "57"))
        pairs = re.sub(r"\{[^}]*\}", "|", post).split("|")[0]
        pairs = [pairs[i:i + 2] for i in range(0, len(pairs), 2)]
        depth -= pop
        for b in pairs:
            if b in ("0f", "e9"):
                break
            depth -= 1 if b in ("58", "59", "5a", "5b", "5c", "5d", "5e", "5f") else 0
        check(depth == 0, f"row {r['id']} thunk leaves the stack balanced after the shim pop", f"depth {depth}")


TRAMP = re.compile(r'rt_up_tramp\("(\w+)", (0x[0-9a-f]+), (\d+)\)')


def tramp_skips(rows):
    src = "".join(open(f).read() for f in sorted(glob.glob(os.path.join(REPO, "runtime", "up_plugins*.c"))))
    seen = {}
    for m, rva, skip in TRAMP.findall(src):
        seen.setdefault((m, int(rva, 16)), set()).add(int(skip))
    for r in rows:
        if "{entry}" not in r.get("thunk", ""):
            continue
        pre = r["thunk"].split("{entry}")[0]
        n = pre.count("{call}") * 6 + sum(len(x) // 2 for x in re.split(r"\{[^}]*\}", pre))
        check(seen.get((r["module"], r["rva"])) == {n}, f"row {r['id']} tramp skip matches its thunk layout", f"{seen.get((r['module'], r['rva']))} want {n}")


def span(data, lo, n):
    return bytes(data[lo:lo + n])


def real_dll_cases(rows):
    if not os.path.isdir(GAME):
        print("note: vanilla game folder missing, DLL cases skipped")
        return
    mods = tomllib.load(open(LAYOUT, "rb"))["modules"]
    with tempfile.TemporaryDirectory() as out:
        before = {n: hashlib.sha256(open(os.path.join(GAME, m["path"]), "rb").read()).hexdigest() for n, m in mods.items()
                  if n in {r["module"] for r in rows}}
        res = uppatch.patch_all(GAME, LAYOUT, rows, None, out)
        after = {n: hashlib.sha256(open(os.path.join(GAME, mods[n]["path"]), "rb").read()).hexdigest() for n in before}
        check(before == after, "the vanilla DLLs are not touched")
        check(os.path.exists(os.path.join(out, "up.sha256")), "up.sha256 is written")
        for r in rows:
            name = r["module"]
            van = pefile.parse(open(os.path.join(GAME, mods[name]["path"]), "rb").read())
            pat = pefile.parse(open(res[name]["path"], "rb").read())
            off = van.rva_to_off(r["rva"])
            old = bytes.fromhex(r["old"])
            got = span(pat.data, off, len(old))
            if r["kind"] == "bytes":
                check(got == bytes.fromhex(r["new"]), f"{r['id']}: new bytes in the patched DLL", got.hex())
                continue
            if r["kind"] == "ptr":
                cell = struct.pack("<I", uppatch.CELL_BASE + 4 * r["cell"])
                thunk_rva = struct.unpack("<I", got)[0] - pat.image_base
                sec = next(s for s in pat.sections if s.executable)
                tcode = span(pat.data, pat.rva_to_off(thunk_rva), 6)
                extra = {int(x, 16) for x in open(os.path.join(out, name, "extra-entries.txt")).read().split()}
                check(struct.unpack("<I", old)[0] == van.image_base + r["target"] and sec.rva + sec.vsize <= thunk_rva < sec.rva + sec.raw_size
                      and tcode == b"\xff\x25" + cell and r["rva"] in pat.relocs and mods[name]["base"] + thunk_rva in extra
                      and mods[name]["base"] + r["target"] in extra,
                      f"{r['id']}: data pointer retargeted to a relocated slack thunk that jumps through its cell",
                      f"word {got.hex()} thunk 0x{thunk_rva:x} {tcode.hex()}")
                continue
            op = 0xE9 if r["kind"] == "jmp" else 0xE8
            rel = struct.unpack_from("<i", got, 1)[0]
            thunk_rva = r["rva"] + 5 + rel
            toff = pat.rva_to_off(thunk_rva)
            cell = struct.pack("<I", uppatch.CELL_BASE + 4 * r["cell"])
            tcode = span(pat.data, toff, 32)
            sec = next(s for s in pat.sections if s.executable)
            in_slack = sec.rva + sec.vsize <= thunk_rva < sec.rva + sec.raw_size
            hdr = b"\xff\x25" + cell if r["thunk"] == "{jmp}" else None
            ok = got[0] == op and got[5:] == b"\x90" * (len(old) - 5) and in_slack and (hdr is None or tcode.startswith(hdr))
            check(ok, f"{r['id']}: site retargeted into slack", f"site {got.hex()} thunk 0x{thunk_rva:x} {tcode.hex()}")
            if r["id"] == "tw_stack_skip":
                full = span(pat.data, toff, 40)
                pos = full.find(b"\x0f\x84")
                tgt = thunk_rva + pos + 6 + struct.unpack_from("<i", full, pos + 2)[0]
                check(pos == 8 and tgt == 0x32d16f, f"{r['id']}: the skip branch lands on the original code after the displaced call", hex(tgt))
                extra_rel = {int(x, 16) for x in open(os.path.join(out, name, "extra-entries.txt")).read().split()}
                check(mods[name]["base"] + 0x32d16f in extra_rel, f"{r['id']}: the skip target is an extra entry", "")
            if r["id"] == "hist_stat_b":
                full = span(pat.data, toff, 40)
                pos = [i for i in range(len(full) - 5) if full[i] == 0x0F and full[i + 1] == 0x84]
                tgt = [thunk_rva + i + 6 + struct.unpack_from("<i", full, i + 2)[0] for i in pos]
                check(tgt[:2] == [0x206ad9, 0x206bcf], f"{r['id']}: rel targets resolve to the original code", str([hex(t) for t in tgt]))
                extra_rel = {int(x, 16) for x in open(os.path.join(out, name, "extra-entries.txt")).read().split()}
                check({mods[name]["base"] + 0x206ad9, mods[name]["base"] + 0x206bcf} <= extra_rel, f"{r['id']}: rel targets are extra entries", "")
            if r["id"] == "cs_give":
                full = span(pat.data, toff, 40)
                tgt = [thunk_rva + 18 + struct.unpack_from("<i", full, 14)[0], thunk_rva + 34 + struct.unpack_from("<i", full, 30)[0],
                       thunk_rva + 39 + struct.unpack_from("<i", full, 35)[0]]
                check(full[:12] == old and full[12:14] == b"\x0f\x85" and full[18] == 0x60 and full[25:30] == b"\x85\xc0\x61\x0f\x85" and full[34] == 0xE9 and
                      tgt == [0x16dfb6, 0x16dfb6, 0x16e6b7], f"{r['id']}: both keep-open exits and the close jump land on the original code",
                      str([hex(t) for t in tgt]))
            if r["id"] == "cs_hide":
                full = span(pat.data, toff, 28)
                tgt = [thunk_rva + 6 + struct.unpack_from("<i", full, 2)[0], thunk_rva + 28 + struct.unpack_from("<i", full, 24)[0]]
                check(full[:2] == b"\x0f\x84" and full[6:23] == b"\x60\xff\x15" + cell + bytes.fromhex("85c0617405c644241000") and full[23] == 0xE9
                      and tgt == [0xda457, 0xda3d1], f"{r['id']}: the thunk keeps the 7405 skip over the 5-byte store and resumes after the branch",
                      full.hex())
            if r["id"] == "cs_owned":
                full = span(pat.data, toff, 34)
                tgt = [thunk_rva + 29 + struct.unpack_from("<i", full, 25)[0], thunk_rva + 34 + struct.unpack_from("<i", full, 30)[0]]
                check(full[:23] == b"\x60\xff\x15" + cell + bytes.fromhex("85c0617405c6442413018a44241384c0") and full[23:25] == b"\x0f\x85"
                      and full[29] == 0xE9 and tgt == [0x16ff00, 0x16fe56],
                      f"{r['id']}: the thunk keeps the 7405 skip over the 5-byte store and resumes after the displaced test",
                      full.hex())
            if r["id"] == "tw_altf4":
                coff = pat.rva_to_off(r["cave"])
                cave = span(pat.data, coff, 10)
                back = r["cave"] + 10 + struct.unpack_from("<i", cave, 6)[0]
                extra_rel = {int(x, 16) for x in open(os.path.join(out, name, "extra-entries.txt")).read().split()}
                check(tcode[:8] == b"\xff\x15" + cell + b"\xff\xe0" and cave[:5] == old and cave[5] == 0xE9 and back == r["rva"] + 5
                      and span(pat.data, coff + 10, 5) == b"\x90" * 5 and thunk_rva + 8 <= sec.rva + sec.raw_size
                      and {mods[name]["base"] + x for x in (r["cave"], 0xa3bc7, r["rva"] + 5)} <= extra_rel,
                      f"{r['id']}: an 8-byte slack thunk jumps to the shim's pick, the cave reruns the load and resumes, both exits are entries",
                      f"thunk {tcode[:8].hex()} cave {cave.hex()} back 0x{back:x}")
            if "{call}" in r["thunk"]:
                check(b"\xff\x15" + cell in tcode, f"{r['id']}: thunk calls its cell", tcode.hex())
            extra = {int(x, 16) for x in open(os.path.join(out, name, "extra-entries.txt")).read().split()}
            base = mods[name]["base"]
            want = {base + thunk_rva}
            if "{resume}" in r["thunk"]:
                want.add(base + r["rva"] + len(old))
            check(want <= extra, f"{r['id']}: thunk and resume are extra entries", f"{sorted(map(hex, want - extra))}")
            if len(old) == 5 and old[0] in (0xE8, 0xE9):
                site_tgt = base + ((r["rva"] + 5 + struct.unpack("<i", old[1:])[0]) & 0xFFFFFFFF)
                check(site_tgt in extra, f"{r['id']}: the displaced E8/E9 target is an extra entry", hex(site_tgt))
            if "{disp}" in r["thunk"]:
                delta = (base - van.image_base) & 0xFFFFFFFF
                mine = [x for x in van.relocs if r["rva"] <= x < r["rva"] + len(old)]
                if mine:
                    o = mine[0] - r["rva"]
                    want_abs = (struct.unpack_from("<I", old, o)[0] + delta) & 0xFFFFFFFF
                    check(struct.pack("<I", want_abs) in span(pat.data, toff, 40),
                          f"{r['id']}: displaced absolute rebased to the layout base", f"want 0x{want_abs:x}")
                    check(not any(r["rva"] <= x < r["rva"] + len(old) for x in pat.relocs),
                          f"{r['id']}: site relocations dropped", "")
        out2 = os.path.join(out, "off")
        ini = os.path.join(out, "off.ini")
        open(ini, "w").write("[Bugs]\nFix Paths = 0\n")
        r2 = uppatch.patch_all(GAME, LAYOUT, rows, ini, out2)
        pat = pefile.parse(open(r2["FileSystem_Stdio"]["path"], "rb").read())
        van = pefile.parse(open(os.path.join(GAME, mods["FileSystem_Stdio"]["path"]), "rb").read())
        check(pat.data == van.data, "a switch off leaves the module as vanilla")
        for i, row in enumerate(rows):
            bad = copy.deepcopy(rows)
            bad[i]["old"] = f"{int(row['old'][:2], 16) ^ 0xFF:02x}{row['old'][2:]}"
            try:
                uppatch.patch_all(GAME, LAYOUT, bad, None, os.path.join(out, "bad"))
                check(False, f"{row['id']}: a wrong old byte stops the patch")
            except uppatch.PatchError as e:
                check(row["id"] in str(e), f"{row['id']}: a wrong old byte stops the patch and names the row", str(e))
        big = copy.deepcopy(rows)
        for r in big:
            if r["id"] == "histories":
                r["thunk"] = "90" * 0x500 + "{resume}"
        try:
            uppatch.patch_all(GAME, LAYOUT, big, None, os.path.join(out, "big"))
            check(False, "a thunk larger than the slack stops the patch")
        except uppatch.PatchError as e:
            check("slack" in str(e), "a thunk larger than the slack stops the patch", str(e))
        for tag, tgt in (("an unmapped", "0x7ffff000"), ("a non-executable", "0x10")):
            badrel = copy.deepcopy(rows)
            for r in badrel:
                if r["id"] == "hist_stat_b":
                    r["thunk"] = r["thunk"].replace("{rel:0x206bcf}", "{rel:" + tgt + "}")
            try:
                uppatch.patch_all(GAME, LAYOUT, badrel, None, os.path.join(out, "badrel"))
                check(False, f"{tag} rel target stops the patch")
            except uppatch.PatchError as e:
                check("rel target" in str(e) and "hist_stat_b" in str(e), f"{tag} rel target stops the patch and names the row", str(e))
        badptr = copy.deepcopy(rows)
        for r in badptr:
            if r["kind"] == "ptr":
                r["target"] += 5
        try:
            uppatch.patch_all(GAME, LAYOUT, badptr, None, os.path.join(out, "badptr"))
            check(False, "a ptr row whose target misses the old pointer stops the patch")
        except uppatch.PatchError as e:
            check("old pointer" in str(e), "a ptr row whose target misses the old pointer stops the patch", str(e))
        badcave = copy.deepcopy(rows)
        for r in badcave:
            if "cave" in r:
                r["cave"] -= 1
        try:
            uppatch.patch_all(GAME, LAYOUT, badcave, None, os.path.join(out, "badcave"))
            check(False, "a cave that overlaps code stops the patch")
        except uppatch.PatchError as e:
            check("cave" in str(e) and "tw_altf4" in str(e), "a cave that overlaps code stops the patch and names the row", str(e))
        out3 = os.path.join(out, "again")
        res3 = uppatch.patch_all(GAME, LAYOUT, rows, None, out3)
        seeded = any(os.path.getsize(os.path.join(out3, n, "extra-entries.txt")) for n in res3)
        stale = os.path.join(out3, "vampire", "gen", "recomp_stubs_unresolved.c")
        os.makedirs(os.path.dirname(stale), exist_ok=True)
        open(stale, "w").close()
        build_all.clear_patch_state(out3, res3)
        check(not os.path.exists(stale), "a patch-set build's unresolved-stub file is dropped by the next plain build")
        left = [n for n in res3 if os.path.exists(os.path.join(out3, n, "extra-entries.txt"))
                and os.path.getsize(os.path.join(out3, n, "extra-entries.txt"))]
        check(seeded and not left and not os.path.exists(os.path.join(out3, "patched")),
              "a plain build after a patch-set build starts with no patch entries", f"seeded {seeded} left {left}")


def aspect_parity(rows):
    src = open(G2B_C).read()
    c = {m[0]: m for m in G2B_ROW.findall(src)}
    pops = {m[0]: int(m[1]) for m in G2B_SHIM.findall(src)}
    check(set(c) - {"g2b_hud"} == {r["id"] for r in rows} and "g2b_hud" in c, "aspect.toml and g2b_engine.c have the same code rows",
          f"toml {sorted(r['id'] for r in rows)} c {sorted(c)}")
    check(c.get("g2b_hud", [""] * 11)[1:6] == ("client", "0x227ec8", "DATA", "000000000000503f", ""),
          "g2b_hud is the run-time write of the 1/1024 double at client+0x227ec8", str(c.get("g2b_hud")))
    for r in rows:
        m = c.get(r["id"])
        if not m:
            continue
        kind = {"BYTES": "bytes", "CELL": "bytes", "JMP": "jmp", "CALL": "call"}.get(m[3])
        ok = (r["owner"] == uppatch.G2B and r["switch"] == ["M9/Engine Aspect"] and m[1] == r["module"] and int(m[2], 16) == r["rva"]
              and kind == r["kind"] and m[4] == r["old"] and m[5] == r.get("new", "") and int(m[6]) == r.get("cell", 0)
              and int(m[7]) == r.get("abs", -1) and [int(m[9], 16), int(m[10], 0)] == r["guard"] and "asm" in r
              and (m[3] == "CELL") == (r["kind"] == "bytes" and "cell" in r)
              and (m[3] == "BYTES" or pops.get(m[8]) == r["pop"]))
        if m[3] == "CELL":
            new = bytes.fromhex(r["new"])
            check(new[:2] == b"\xff\x15" and struct.unpack_from("<I", new, r["abs"])[0] == uppatch.CELL_BASE + 4 * r["cell"],
                  f"row {r['id']} calls through its cell at its abs offset", r["new"][:12])
        check(ok, f"row {r['id']} matches between aspect.toml and g2b_engine.c and cites its disassembly", f"toml {r} c {m}")
        if "{call}" in r.get("thunk", ""):
            pre, post = r["thunk"].split("{call}")
            pushes = sum(1 for i in range(0, len(re.sub(r"\{[^}]*\}", "", pre)), 2) if re.sub(r"\{[^}]*\}", "", pre)[i:i + 2] in ("50", "55", "56"))
            body = re.sub(r"\{[^}]*\}", "", post)
            pops_after = sum(1 for i in range(0, len(body), 2) if body[i:i + 2] == "58")
            check(pushes - r["pop"] - pops_after == 0, f"row {r['id']} thunk leaves the stack balanced", r["thunk"])
    check(sorted(r["cell"] for r in rows if "cell" in r) == [32, 34, 35, 36, 37, 38, 40, 41, 42, 43, 44, 45, 46, 47, 50, 51, 52, 53, 54, 55, 56],
          "aspect rows use cells 32, 34 to 38, 40 to 47 and 50 to 56", "")


def mode_filter(path, modes, requests):
    try:
        import unicorn
        from unicorn import x86_const as X
    except ImportError:
        return None
    p = pefile.parse(open(path, "rb").read())
    base, img = p.image_base, p.mapped()
    uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
    uc.mem_map(base, (len(img) + 0xFFF) & ~0xFFF)
    uc.mem_write(base, img)
    heap, stack = 0x10000000, 0x0F000000
    uc.mem_map(heap, 0x10000)
    uc.mem_map(stack, 0x10000)
    this, ms, vt, fn = heap, heap + 0x8000, heap + 0x8100, heap + 0x8200
    uc.mem_write(fn, b"\xc2\x0c\x00")
    uc.mem_write(vt + 0x30, struct.pack("<I", fn))
    uc.mem_write(ms, struct.pack("<I", vt))
    uc.mem_write(base + 0xA6C014, struct.pack("<I", ms))

    def info(u, addr, size, _):
        esp = u.reg_read(X.UC_X86_REG_ESP)
        _, _, i, out = struct.unpack("<IIII", u.mem_read(esp, 16))
        u.mem_write(out, struct.pack("<II", *modes[i]))

    uc.hook_add(unicorn.UC_HOOK_CODE, info, begin=fn, end=fn)
    esp = stack + 0x8000
    uc.mem_write(esp, bytes(0x40))
    uc.mem_write(esp + 0x20, struct.pack("<I", 0xFFFFFFFF))
    uc.mem_write(this + 0x5830, struct.pack("<I", len(modes)))
    for reg, v in ((X.UC_X86_REG_ESP, esp), (X.UC_X86_REG_EBP, this), (X.UC_X86_REG_ESI, this + 8), (X.UC_X86_REG_EDI, 0xFFFFFFFF),
                   (X.UC_X86_REG_EBX, 0)):
        uc.reg_write(reg, v)
    uc.emu_start(base + 0xFC867, base + 0xFC8F8)
    n = uc.reg_read(X.UC_X86_REG_EBX)
    listed = [struct.unpack("<II", uc.mem_read(this + 4 + 0x2C * k, 8)) for k in range(n)]
    uc.mem_write(this + 0x5830, struct.pack("<I", n))
    chosen = []
    for w, h in requests:
        uc.mem_write(esp, bytes(0x40))
        uc.mem_write(esp + 0x18, struct.pack("<II", w, h))
        for reg, v in ((X.UC_X86_REG_ESP, esp), (X.UC_X86_REG_EBP, this), (X.UC_X86_REG_ESI, 0xFFFFFFFF)):
            uc.reg_write(reg, v)
        uc.emu_start(base + 0xFCA0F, base + 0xFCA4B)
        k = uc.reg_read(X.UC_X86_REG_EAX)
        chosen.append(listed[k] if k < len(listed) else None)
    for reg, v in ((X.UC_X86_REG_ESP, esp), (X.UC_X86_REG_EBP, this), (X.UC_X86_REG_EBX, n)):
        uc.reg_write(reg, v)
    uc.emu_start(base + 0xFC8F8, base + 0xFC94D)
    m = struct.unpack("<I", uc.mem_read(this + 0x5830, 4))[0]
    kept = [struct.unpack("<II", uc.mem_read(this + 4 + 0x2C * k, 8)) for k in range(m)]
    return listed, chosen, kept


def view_tangent(path, base, fov_in, ratio, widen):
    try:
        import unicorn
        from unicorn import x86_const as X
    except ImportError:
        return None
    img = pefile.parse(open(path, "rb").read()).mapped(base)
    uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
    uc.mem_map(base, (len(img) + 0xFFF) & ~0xFFF)
    uc.mem_write(base, img)
    cells, heap, stack = uppatch.CELL_BASE & ~0xFFF, 0x0E000000, 0x0F000000
    uc.mem_map(cells, 0x1000)
    uc.mem_map(heap, 0x10000)
    uc.mem_map(stack, 0x10000)
    view, stub = heap, heap + 0x8000
    uc.mem_write(stub, b"\xc2\x04\x00")
    uc.mem_write(uppatch.CELL_BASE + 4 * 34, struct.pack("<I", stub))
    uc.mem_write(view + 0x38, struct.pack("<ff", fov_in, fov_in))

    def shim(u, addr, size, _):
        v = struct.unpack("<I", u.mem_read(u.reg_read(X.UC_X86_REG_ESP) + 4, 4))[0]
        for o in (0x38, 0x3C):
            f = struct.unpack("<f", u.mem_read(v + o, 4))[0]
            u.mem_write(v + o, struct.pack("<f", widen(f, ratio)))

    uc.hook_add(unicorn.UC_HOOK_CODE, shim, begin=stub, end=stub)
    uc.reg_write(X.UC_X86_REG_ESP, stack + 0x8000)
    uc.reg_write(X.UC_X86_REG_EBP, view)
    uc.emu_start(base + 0x192069, base + 0x192095)
    return struct.unpack("<ffff", uc.mem_read(view + 0x38, 16))


def hor_plus(f, r):
    return math.degrees(2 * math.atan(math.tan(math.radians(f) / 2) * 0.75 * r))


def pillar(w, h):
    if w * 3 <= h * 4:
        return w, 0
    wp = (h * 4 + 1) // 3
    return wp, (w - wp) // 2


def aspect_cases(up_rows, aspect_rows):
    if not os.path.isdir(GAME):
        if os.environ.get("BP_NO_GAME") == "1":
            print("note: BP_NO_GAME=1, aspect DLL cases skipped")
        else:
            check(False, "the vanilla game folder is present for the aspect DLL proof (BP_NO_GAME=1 skips it)", GAME)
        return
    mods = tomllib.load(open(LAYOUT, "rb"))["modules"]
    with tempfile.TemporaryDirectory() as out:
        res = uppatch.patch_all(GAME, LAYOUT, aspect_rows, None, out)
        check(not os.path.exists(os.path.join(out, "up.sha256")), "an aspect-only patch writes no up.sha256", "")
        check(sorted(res["engine"]["applied"] + res["client"]["applied"]) == sorted(r["id"] for r in aspect_rows),
              "every aspect row applies on vanilla", str(res["engine"]["applied"] + res["client"]["applied"]))
        stock = mode_filter(os.path.join(GAME, mods["engine"]["path"]), MODES, REQUESTS)
        if stock is None:
            check(False, "unicorn is importable for the mode-filter emulation", "pip install unicorn in the venv")
        else:
            listed, chosen, kept = mode_filter(res["engine"]["path"], MODES, REQUESTS)
            check(sorted(kept) == sorted(m for m in listed if m != (640, 480)) and len(kept) == len(listed) - 1
                  and sorted(stock[2]) == sorted(m for m in stock[0] if m != (640, 480)),
                  "the engine drops 640x480 from its mode list (engine+0xfc8f8), stock and G2b, so the runtime floor is 800x600", str(kept))
            print(f"engine-modes stock listed {stock[0]} chosen {stock[1]}")
            print(f"engine-modes g2b listed {listed} chosen {chosen}")
            check(listed == MODES[:-1], "the G2b mode filter lists every bridge mode of at least 640x480 and drops 600x400", str(listed))
            check(chosen == REQUESTS, "each -w/-h request is chosen as asked", str(chosen))
            check([m for m in listed if m in MODES[:5]] == MODES[:5], "the five stock sizes keep their stock order", str(listed))
            check(stock[0] == MODES[:5] and stock[1][0] == (640, 480),
                  "mutation baseline: the stock filter keeps the five table sizes and picks mode 0 for 3440x1440", str(stock))
        for w, h in ((1024, 768), (1280, 1024), (1920, 1080), (3440, 1440), (5120, 1440)):
            for fov in (75.0, 45.0):
                t = view_tangent(res["client"]["path"], mods["client"]["base"], fov, w / h, hor_plus)
                if t is None:
                    check(False, "unicorn is importable for the view set-up emulation", f"{w}x{h} fov {fov}")
                    break
                want = math.tan(math.radians(t[0]) / 2)
                print(f"engine-fov tangent size={w}x{h} in={fov} view={t[0]:.4f} tan40={t[2]:.7f} tan44={t[3]:.7f} want={want:.7f}")
                check(abs(t[0] - hor_plus(fov, w / h)) <= 0.01 and abs(t[2] - want) <= 1e-6 and abs(t[3] - want) <= 1e-6,
                      f"the patched view set-up stores tan(fov_out/2) at view+0x40 and +0x44 at {w}x{h} fov {fov}", str(t))
        pc = pefile.parse(open(res["client"]["path"], "rb").read())
        img = pc.mapped(mods["client"]["base"])
        keeps = [r for r in aspect_rows if r.get("new") == "d80d7cb92210"]
        check(sorted(r["rva"] for r in keeps) == [0x109BAE], "the gamma ramp is the one stock-scale row", str([r["id"] for r in keeps]))
        for site in (0x167392, 0x1673D0, 0x1676F5, 0x167722, 0x167751, 0x167780):
            check(img[site:site + 2] == b"\xdc\x0d" and struct.unpack_from("<I", img, site + 2)[0] == mods["client"]["base"] + 0x227EC8,
                  f"the loading-panel read at client+0x{site:x} stays on the HUD double, so it scales with the pillarbox panel", img[site:site + 6].hex())
        for site, cell, tgt in ((0x16728D, 36, 0x1A98F0), (0x167298, 37, 0x1A9890)):
            rel = struct.unpack_from("<i", img, site + 1)[0]
            thunk = site + 5 + rel
            check(img[site] == 0xE8 and img[thunk:thunk + 6] == b"\xff\x25" + struct.pack("<I", uppatch.CELL_BASE + 4 * cell),
                  f"the loading-panel call at client+0x{site:x} goes through a jmp on cell {cell}", img[thunk:thunk + 6].hex())
            check(tgt == (site + 5 + struct.unpack_from("<i", bytes.fromhex("e85e260400" if site == 0x16728D else "e8f3250400"), 1)[0]),
                  f"the cell {cell} shim replaces the call to client+0x{tgt:x}", "")
        ui_rows = [r for r in aspect_rows if r["id"].startswith("g2b_ui_") and r["kind"] == "call"]
        check(len(ui_rows) == 16, "sixteen per-site panel rows", str([r["id"] for r in ui_rows]))
        for r in ui_rows:
            site = r["rva"]
            old = bytes.fromhex(r["old"])
            tgt = (site + 5 + struct.unpack_from("<i", old, 1)[0]) & 0xFFFFFFFF
            want_tgt = 0x5F590 if r["id"] == "g2b_ui_money_text" else 0x5C3B0 if r["id"] == "g2b_ui_barter_sel_paint" else 0x59620 if "_paint" in r["id"] else 0x1A9950 if r["id"].endswith(("_frame", "_ctor", "_show")) else 0xCD100
            check(old[0] == 0xE8 and tgt == want_tgt, f"{r['id']} replaces the call to client+0x{want_tgt:x}", f"0x{tgt:x}")
            check(bytes(img[site:site + 5]) != old and img[site] == 0xE8, f"{r['id']} is patched on vanilla", img[site:site + 5].hex())
            rel = struct.unpack_from("<i", img, site + 1)[0]
            thunk = site + 5 + rel
            check(img[thunk:thunk + 6] == b"\xff\x25" + struct.pack("<I", uppatch.CELL_BASE + 4 * r["cell"]),
                  f"{r['id']} goes through a jmp on cell {r['cell']}", img[thunk:thunk + 6].hex())
            clash = [u["id"] for u in up_rows if u["module"] == "client" and u["rva"] < site + 5 and site < u["rva"] + max(len(u.get("old", "")) // 2, 1)]
            check(not clash, f"{r['id']} overlaps no Unofficial Patch row", str(clash))
        for w, h in ((640, 480), (800, 600), (1024, 768), (1600, 1200), (1280, 1024), (1920, 1080), (2560, 1080), (3440, 1440), (5120, 1440)):
            wp, off = pillar(w, h)
            if w * 3 <= h * 4:
                check((wp, off) == (w, 0), f"the loading pillarbox at {w}x{h} is the stock full-screen panel", f"{wp} {off}")
            else:
                check(abs(wp * 3 - h * 4) <= 2 and abs(w - wp - 2 * off) <= 1 and abs(wp / 1024 - w / (768 * (w / h))) < 1 / 1024,
                      f"the loading pillarbox at {w}x{h} is 4:3, centred, on the HUD scale", f"{wp} {off}")
        for r in keeps:
            disp = struct.unpack_from("<I", img, r["rva"] + 2)[0]
            check(img[r["rva"]:r["rva"] + 2] == b"\xd8\x0d" and r["rva"] + 2 in pc.relocs and disp == mods["client"]["base"] + 0x22B97C
                  and struct.unpack_from("<f", img, 0x22B97C)[0] == 1 / 1024,
                  f"{r['id']} reads the 1/1024 float at client+0x22b97c through a kept relocation", f"0x{disp:x}")
        t = view_tangent(res["client"]["path"], mods["client"]["base"], 75.0, 3440 / 1440, lambda f, r: f)
        if t is not None:
            check(abs(t[2] - math.tan(math.radians(hor_plus(75.0, 3440 / 1440)) / 2)) > 1e-3,
                  "mutation: a shim that leaves the FOV at 75 fails the 3440x1440 tangent", str(t))
        up = os.path.join(out, "up")
        r1 = uppatch.patch_all(GAME, LAYOUT, up_rows, None, up)
        both = os.path.join(out, "both")
        r2 = uppatch.patch_all(GAME, LAYOUT, uppatch.load_rows([PATCHES, ASPECT]), None, both)
        h1, h2 = (open(os.path.join(d, "up.sha256")).read() for d in (up, both))
        check(h1 == h2, "up.sha256 hashes only the rows whose owner is not m9-g2b", f"{h1} {h2}")
        check(set(r1["engine"]["applied"]) <= set(r2["engine"]["applied"]) and "g2b_modes" in r2["engine"]["applied"],
              "the UP and aspect sets apply together", "")
        fake = dict(id="up_auto_ar", owner="up", switch=["Widescreen/Auto AR"], module="engine", rva=0x7A44E, kind="bytes",
                    old="398ee33f", new="0000803f")
        rows_y = uppatch.load_rows([ASPECT])
        rows_y.insert(0, fake)
        yr = uppatch.patch_all(GAME, LAYOUT, rows_y, None, os.path.join(out, "yield"))
        ap = yr["engine"]["applied"]
        check("g2b_aspect" not in ap and "g2b_modes" in ap and "up_auto_ar" in ap,
              "an UP row on engine+0x7a44e makes g2b_aspect yield and g2b_modes apply", str(ap))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "dup.toml")
        open(p, "w").write(open(ASPECT).read().replace("cell = 32", "cell = 30"))
        try:
            uppatch.load_rows([PATCHES, p])
            check(False, "a cell shared across patch sets is refused")
        except uppatch.PatchError as e:
            check("cell" in str(e), "a cell shared across patch sets is refused", str(e))


def plugin_cases():
    with tempfile.TemporaryDirectory() as d:
        for f in ("vtmb-tweaks.vtm", "protean-improved.vtm"):
            open(os.path.join(d, f), "w").close()
        open(os.path.join(d, "loader.ini"), "w").write("[Tweaks]\nNon-Solid Ragdolls = 0\nNo Bullet Tracers = 0\n")
        open(os.path.join(d, "vtmb-tweaks.ini"), "w").write("[MAIN]\nbNonSolidRagdolls = 1\nbNoTracers = 0\nbFixDoorsAtHighFPS = 0\n")
        ini = {}
        for n in ("loader.ini", "vtmb-tweaks.ini"):
            uppatch.read_ini(os.path.join(d, n), ini)
        on = lambda s: uppatch.switch_on(ini, s)
        check(on("vtmb-tweaks/bNonSolidRagdolls") and not on("vtmb-tweaks/bNoTracers") and not on("vtmb-tweaks/bFixDoorsAtHighFPS"),
              "a plug-in key follows its own ini", "")
        check(on("protean-improved/Enabled") and not on("model-loader-fix/Enabled"),
              "a plug-in is enabled when its .vtm is beside loader.ini", "")
        open(os.path.join(d, "protean-improved.ini"), "w").write("[Protean]\nWarform Frenzy = 0\n")
        pro = {}
        for n in ("loader.ini", "protean-improved.ini"):
            uppatch.read_ini(os.path.join(d, n), pro)
        check(not uppatch.switch_on(pro, "protean-improved/Warform Frenzy"), "a plug-in ini key of 0 turns its row off", "")
        rows = [r for r in uppatch.load_rows(PATCHES) if r["id"] in ("ragdolls", "no_tracers")]
        check(all(len(r["switch"]) == 2 for r in rows), "the doubly keyed rows list both switches", "")
        any_on = [any(on(s) for s in r["switch"]) for r in rows]
        check(any_on == [True, False], "a bytes row is applied when either switch is on", str(any_on))


def main():
    rows = uppatch.load_rows(PATCHES)
    check(len(rows) == 41 and rows[-1]["id"] == "cs_barter", "patches.toml has 41 rows", len(rows))
    by_id = {r["id"]: r for r in rows}
    cs =[by_id[k] for k in ("cs_hide", "cs_give", "cs_owned", "cs_keys", "cs_barter")]
    check([(r["module"], r["rva"], r["kind"], r["pop"]) for r in cs] ==
          [("vampire", 0xda3cb, "jmp", 0), ("client", 0x16dfa4, "jmp", 0), ("client", 0x16fe4a, "jmp", 0), ("client", 0x24aa90, "ptr", 1),
           ("client", 0x102150, "jmp", 0)] and cs[3]["target"] == 0x1b4360 and cs[4]["abs"] == 3 and
          all(r["switch"] == ["container-stack-fix/Enabled"] for r in cs) and
          all(r["thunk"].count("60{call}85c061") == 1 for r in cs[:3]),
          "the five container-stack-fix rows ship together, the stub hooks saving every register around the shim", "")
    by_id = {r["id"]: r for r in rows}
    check(by_id["protean_warform_b"]["switch"] == ["protean-improved/Warform Frenzy"], "the protean row is keyed by Warform Frenzy",
          str(by_id["protean_warform_b"]["switch"]))
    check(by_id["no_tracers"]["new"] == "e9b000000000" and by_id["door_fps_b"]["new"] == "b9020000009090",
          "no_tracers and door_fps_b write the recorded bytes", by_id["no_tracers"]["new"])
    check(by_id["door_fps_a"]["kind"] == "jmp" and by_id["door_fps_a"]["old"] == "e9293d1b00" and
          by_id["door_fps_b"]["switch"] == by_id["door_fps_a"]["switch"] == ["vtmb-tweaks/bFixDoorsAtHighFPS"],
          "both door halves share the bFixDoorsAtHighFPS key", "")
    hb = by_id["hist_stat_b"]["thunk"]
    check(hb.count("{rel:") == 3 and "{resume}" not in hb, "hist_stat_b branches through three rel targets", hb)
    ml = by_id["model_loader"]
    check(ml["thunk"] == "{call}c20400{entry}{disp}{resume}" and ml["old"] == "53558b6c240c56" and ml["pop"] == 0,
          "model_loader thunk returns the shim result popping the name and keeps the displaced prologue as the original entry", ml["thunk"])
    con = [by_id[k] for k in ("console_open", "console_cmd", "console_close", "console_menu")]
    check([r["thunk"] for r in con] == ["{call}c3{entry}{disp}{resume}", "{call}c20400{entry}{disp}{resume}",
                                        "{call}c20400{entry}{disp}{resume}", "{call}c20400{entry}{disp}{resume}"] and
          [r.get("abs") for r in con] == [2, None, None, 5] and all(r["switch"] == ["console-pause-fix/Enabled"] for r in con),
          "console rows run the original through an entry after the hook call, returning with the original's pop", "")
    tw = {k: by_id[k] for k in ("tw_nodoor", "tw_drop", "tw_stack_find", "tw_stack_pick", "tw_stack_skip")}
    check(tw["tw_nodoor"]["thunk"] == "{call}5ec3" and tw["tw_nodoor"]["switch"] == ["vtmb-tweaks/bNoDoorAutoClose"] and
          [tw[k]["kind"] for k in ("tw_drop", "tw_stack_find", "tw_stack_pick")] == ["call"] * 3 and
          [tw[k]["pop"] for k in ("tw_drop", "tw_stack_find", "tw_stack_pick")] == [0, 2, 1] and
          tw["tw_stack_skip"]["old"] == "8b078bcfff9008060000" and all(r["switch"] and r["switch"][0].startswith("vtmb-tweaks/") for r in tw.values()),
          "vtmb-tweaks rows keep the recorded sites, pops and plug-in keys", "")
    cor = [by_id[k] for k in ("tw_corpse_think", "tw_corpse_fade", "tw_corpse_mark", "tw_corpse_clear")]
    check([r["kind"] for r in cor] == ["call", "jmp", "call", "ptr"] and [r["pop"] for r in cor] == [0, 0, 3, 0] and
          cor[1]["thunk"] == "{call}85c07401c3{disp}{resume}" and cor[3]["target"] == 0xb802 and cor[3]["abs"] == 0 and
          all(r["switch"] == ["vtmb-tweaks/bNoCorpseDespawn"] for r in cor),
          "the four corpse rows ship together under bNoCorpseDespawn, the level-change hook as a data pointer", "")
    tsrc = open(os.path.join(REPO, "runtime", "up_plugins_tweaks.c")).read()
    cave_c = re.search(r"E_KEY_CAVE = (0x[0-9a-f]+)", tsrc)
    epi_c = re.search(r"E_KEY_EPILOGUE = (0x[0-9a-f]+)", tsrc)
    af = by_id["tw_altf4"]
    check(cave_c and epi_c and int(cave_c[1], 16) == af["cave"] and f"{{mark:{epi_c[1]}}}" in af["thunk"] and af["cave_thunk"] == "{disp}{resume}",
          "the alt+f4 shim returns the cave and epilogue the row lays out", af["thunk"])
    with tempfile.TemporaryDirectory() as d:
        for label, body in (("a ptr row without a target", "target = 0xb802\n"), ("a ptr row with {resume}", 'thunk = "{jmp}"\n')):
            p = os.path.join(d, "ptr.toml")
            src = open(PATCHES).read()
            cut = src.index('id = "tw_corpse_clear"')
            tail = src[cut:].replace(body, "" if "target" in body else 'thunk = "{jmp}{resume}"\n')
            open(p, "w").write(src[:cut] + tail)
            try:
                uppatch.load_rows(p)
                check(False, f"{label} is refused")
            except uppatch.PatchError as e:
                check("ptr row" in str(e), f"{label} is refused", str(e))
    parity(rows)
    stack_balance(rows)
    tramp_skips(rows)
    plugin_cases()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "dup.toml")
        open(p, "w").write(open(PATCHES).read().replace("cell = 2", "cell = 1"))
        try:
            uppatch.load_rows(p)
            check(False, "two rows on one cell are refused")
        except uppatch.PatchError as e:
            check("cell" in str(e), "two rows on one cell are refused", str(e))
    aspect = uppatch.load_rows(ASPECT)
    check(len(aspect) == 24 and all(r["owner"] == uppatch.G2B for r in aspect), "aspect.toml has twenty-four m9-g2b rows", len(aspect))
    aspect_parity(aspect)
    real_dll_cases(uppatch.load_rows([PATCHES, ASPECT]))
    aspect_cases(rows, aspect)
    print(f"test_uppatch: {fails} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
