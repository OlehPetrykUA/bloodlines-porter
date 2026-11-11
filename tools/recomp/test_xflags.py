import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "third_party", "xboxrecomp"))
sys.path.insert(0, os.path.join(HERE, ".."))

import devpaths

from capstone import Cs, CS_ARCH_X86, CS_MODE_32

import pe as pefile
import test_plan
import xflags

ECX = "8b4c2404"
CONS = "7c03 31c0 c3 b801000000 c3"
BODIES = {
    "cons_l": CONS,
    "p_jmp": f"{ECX} 83f901 e9 @cons_l",
    "p_jcc": f"{ECX} 83f901 0f85 @cons_l b807000000 c3",
    "p_call": f"{ECX} 83f901 e8 @cons_l c3",
    "fall": f"{ECX} 83f901 {CONS}",
    "cut": f"{ECX} 83f902 e9 @fall+7",
    "helper": "83f905 c3",
    "p_ret": f"{ECX} e8 @helper 7403 31c0 c3 b801000000 c3",
    "helper2": "83f906 c3",
    "p_callc": f"{ECX} e8 @helper2 e8 @cons_l c3",
    "cons_sbb": "1bc0 c3",
    "p_sbb": f"{ECX} 83f901 e9 @cons_sbb",
    "cons_inc": "40 7203 31c0 c3 b801000000 c3",
    "p_inc": f"{ECX} 83f901 e9 @cons_inc",
    "plain": "b802000000 c3",
    "p_plain": f"{ECX} 83f901 e9 @plain",
}


def assemble(rvas, bodies=BODIES):
    out = {}
    for name, text in bodies.items():
        code = bytearray()
        for tok in text.split():
            if tok.startswith("@"):
                to, _, off = tok[1:].partition("+")
                rel = rvas[to] + int(off or 0) - (rvas[name] + len(code) + 4)
                code += (rel & 0xFFFFFFFF).to_bytes(4, "little")
            else:
                code += bytes.fromhex(tok)
        out[name] = bytes(code)
    return out


TAIL = "31c0 c3 b801000000 c3"
SCAN_BODIES = {
    "cmc": f"f5 7203 {TAIL}",
    "inc_cf": f"40 7203 {TAIL}",
    "inc_zf": f"40 7403 {TAIL}",
    "dec_cf": f"48 7203 {TAIL}",
    "dec_sf": f"48 7803 {TAIL}",
    "bt_zf": f"0fbae001 7403 {TAIL}",
    "bt_cf": f"0fbae001 7203 {TAIL}",
    "cmps": f"a6 7403 {TAIL}",
    "repe_cmps": f"f3a6 7403 {TAIL}",
    "scas": f"ae 7403 {TAIL}",
    "repne_scas": f"f2ae 7403 {TAIL}",
    "lock_xadd": f"f00fc108 7403 {TAIL}",
    "lock_cmpxchg": f"f00fb10a 7403 {TAIL}",
    "lock_add": f"f00108 7403 {TAIL}",
    "stc_zf": f"f9 7403 {TAIL}",
    "stc_cf": f"f9 7203 {TAIL}",
    "bts_zf": f"0fbae801 7403 {TAIL}",
    "rol_zf": f"d1c0 7403 {TAIL}",
    "rol_1_cf": f"d1c0 7203 {TAIL}",
    "rol_1_of": f"d1c0 7003 {TAIL}",
    "rol_cl_cf": f"d3c0 7203 {TAIL}",
    "rol_cl_of": f"d3c0 7003 {TAIL}",
    "shl_cl_zf": f"d3e0 7403 {TAIL}",
    "shl_1_zf": f"d1e0 7403 {TAIL}",
    "shl_32_zf": f"c1e020 7403 {TAIL}",
    "shl_3_zf": f"c1e003 7403 {TAIL}",
    "shrd_cl_zf": f"0fadc8 7403 {TAIL}",
    "rol_sf": f"d1c0 7803 {TAIL}",
    "rcl_zf": f"d1d0 7403 {TAIL}",
    "rcr_cf": f"d1d8 7203 {TAIL}",
    "setter": "83f905 c3",
    "reader": "7c03 31c0 c3 b801000000 c3",
    "both": "e8 @setter 7206 e9 @reader c3",
}
SCAN_BASE = 0x10000


class Image:
    def __init__(self, blob):
        self.blob, self.cs = blob, Cs(CS_ARCH_X86, CS_MODE_32)
        self.cs.detail = True

    def decode(self, va):
        o = va - SCAN_BASE
        return next(self.cs.disasm(self.blob[o:o + 16], va), None)


def scan_cases():
    sizes = {n: len(b) for n, b in assemble({n: 0 for n in SCAN_BODIES}, SCAN_BODIES).items()}
    rvas, at = {}, 0
    for n in SCAN_BODIES:
        rvas[n], at = at, at + sizes[n]
    bodies = assemble(rvas, SCAN_BODIES)
    img = Image(b"".join(bodies[n] for n in SCAN_BODIES))
    va = {n: SCAN_BASE + r for n, r in rvas.items()}
    funcs = {}
    for n, b in bodies.items():
        insns = {i.address: i for i in img.cs.disasm(b, va[n])}
        funcs[va[n]] = {"insns": insns, "end": va[n] + len(b)}
    sc = xflags.Scan(img, funcs)
    got = {n: sorted(sc.consumers.get(va[n], ())) for n in SCAN_BODIES}
    want = {"cmc": ["CF"], "inc_cf": ["CF"], "dec_cf": ["CF"], "bt_zf": ["ZF"], "reader": ["OF", "SF"],
            "repe_cmps": ["ZF"], "repne_scas": ["ZF"], "stc_zf": ["ZF"], "rol_zf": ["ZF"], "bts_zf": ["ZF"],
            "rol_sf": ["SF"], "stc_cf": [], "rol_cl_cf": ["CF"], "rol_cl_of": ["OF"], "shl_cl_zf": ["ZF"], "shl_32_zf": ["ZF"],
            "shrd_cl_zf": ["ZF"], "rcl_zf": ["CF", "ZF"], "rcr_cf": ["CF"]}
    assert got == {n: want.get(n, []) for n in SCAN_BODIES}, got
    assert sorted(sc.flag_ret[va["setter"]]) == ["CF", "OF", "SF"], sc.flag_ret
    assert va["setter"] in sc.direct_flag_ret


def functions(gen):
    out, cur = {}, None
    for path in sorted(glob.glob(os.path.join(gen, "recomp_*.c"))):
        for line in open(path):
            mt = re.match(r"void sub_([0-9A-F]{8})\(void\)", line)
            if mt:
                cur = int(mt.group(1), 16)
                out[cur] = []
            elif cur is not None:
                out[cur].append(line.rstrip())
    return out


def before(lines, first, then):
    a = next((i for i, l in enumerate(lines) if first in l), None)
    b = next((i for i, l in enumerate(lines) if then in l), None)
    return a is not None and b is not None and a < b


def main(out):
    scan_cases()
    os.makedirs(out, exist_ok=True)
    rvas = {n: 0 for n in BODIES}
    for _ in range(2):
        data = test_plan.make_pe(assemble(rvas))
        rvas = pefile.parse(data).exports
    dll = os.path.join(out, "xflagtest.dll")
    open(dll, "wb").write(data)
    layout = os.path.join(out, "layout.toml")
    open(layout, "w").write(f"[modules.xflagtest]\nbase = 0x{test_plan.BASE:08X}\n\n[runtime]\ndata_cells = 0x00031000\n")
    rc = subprocess.run([sys.executable, "-I", "-B", os.path.join(HERE, "translate.py"), dll, "--layout", layout,
                         "--module", "xflagtest", "--out", os.path.join(out, "xflagtest")]).returncode
    assert rc == 0, rc
    va = {n: test_plan.BASE + r for n, r in rvas.items()}
    x = va["fall"] + 7
    fn = functions(os.path.join(out, "xflagtest", "gen"))
    s = lambda n: f"sub_{va[n]:08X}"
    assert before(fn[va["p_jmp"]], f"/* flags for {s('cons_l')} */", f"{s('cons_l')}();"), fn[va["p_jmp"]]
    assert before(fn[va["p_jcc"]], f"/* flags for {s('cons_l')} */", "/* jne"), fn[va["p_jcc"]]
    assert before(fn[va["p_call"]], f"/* flags for call {s('cons_l')} */", f"/* call 0x{va['cons_l']:08X} */"), fn[va["p_call"]]
    assert before(fn[va["fall"]], f"/* flags for sub_{x:08X} */", f"/* fallthrough 0x{x:08X} */"), fn[va["fall"]]
    assert before(fn[va["cut"]], f"/* flags for sub_{x:08X} */", f"sub_{x:08X}();"), fn[va["cut"]]
    for c in (va["cons_l"], x):
        assert any("/* eflags */" in l and "/* jl" in l for l in fn[c]), fn[c]
    assert before(fn[va["helper"]], "/* flags for ret */", "return;"), fn[va["helper"]]
    assert any("/* eflags */" in l and "/* je" in l for l in fn[va["p_ret"]]), fn[va["p_ret"]]
    assert "_cf = (int)(g_eflags & 1u); /* cross-fragment CF */" in "\n".join(fn[va["cons_sbb"]]), fn[va["cons_sbb"]]
    assert before(fn[va["p_sbb"]], f"/* flags for {s('cons_sbb')} */", f"{s('cons_sbb')}();"), fn[va["p_sbb"]]
    assert not any("g_eflags =" in l for l in fn[va["p_plain"]] + fn[va["plain"]]), fn[va["p_plain"]]
    fallbacks = [l for lines in fn.values() for l in lines if re.search(r"_flags /\* j|_cf /\* j", l)]
    assert not fallbacks, fallbacks
    cross = json.load(open(os.path.join(out, "xflagtest", "report.json")))["cross_flags"]
    assert set(cross["consumers"]) == {f"0x{v:08X}" for v in (va["cons_l"], x, va["cons_sbb"], va["cons_inc"])}, \
        cross["consumers"]
    assert cross["consumers"][f"0x{va['cons_inc']:08X}"] == ["CF"], cross["consumers"]
    assert before(fn[va["p_inc"]], f"/* flags for {s('cons_inc')} */", f"{s('cons_inc')}();"), fn[va["p_inc"]]
    assert cross["flag_ret"] == {f"0x{va['helper']:08X}": ["ZF"], f"0x{va['helper2']:08X}": ["OF", "SF"]}, cross
    assert before(fn[va["helper2"]], "/* flags for ret */", "return;"), fn[va["helper2"]]
    assert not any("g_eflags =" in l for l in fn[va["p_callc"]]), fn[va["p_callc"]]
    assert not cross["missing"] and not cross["fallbacks"], cross


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else devpaths.build("xf", "xflagtest"))
    print("ok")
