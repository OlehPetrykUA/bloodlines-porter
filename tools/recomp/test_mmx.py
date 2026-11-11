import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILL = 0xAAAAAAAAAAAAAAAA
PAIRS = [(0x7FFF8000FF7F0180, 0x7FFF800180017F81), (0x7FFF800180017F81, 0x7FFF8000FF7F0180),
         (0x0123456789ABCDEF, 0xFEDCBA9876543210), (0x8000800080008000, 0x8000800080008000)]
COUNTS = [(0x8000FFFF7FFF0001, n) for n in (0, 3, 8, 15, 16, 31, 32, 33, 63, 64, 0x100000001)]


def lanes(x, w, signed=False):
    out = [(x >> (i * w)) & ((1 << w) - 1) for i in range(64 // w)]
    return [v - (1 << w) if signed and v >> (w - 1) else v for v in out]


def pack(vals, w):
    return sum((v & ((1 << w) - 1)) << (i * w) for i, v in enumerate(vals))


def sat(v, lo, hi):
    return max(lo, min(hi, v))


def lane(fn, w, signed=False):
    return lambda a, b: pack([fn(x, y) for x, y in zip(lanes(a, w, signed), lanes(b, w, signed))], w)


def ssat(w):
    return lambda v: sat(v, -(1 << (w - 1)), (1 << (w - 1)) - 1)


def usat(w):
    return lambda v: sat(v, 0, (1 << w) - 1)


def interleave(w, hi):
    def fn(a, b):
        n = 32 // w
        x, y = lanes(a, w)[n * hi:n * hi + n], lanes(b, w)[n * hi:n * hi + n]
        return pack([v for p in zip(x, y) for v in p], w)
    return fn


def packs(w, clamp):
    return lambda a, b: pack([clamp(v) for v in lanes(a, w, True) + lanes(b, w, True)], w // 2)


def shl(w):
    return lambda a, n: 0 if n >= w else pack([v << n for v in lanes(a, w)], w)


def shr(w):
    return lambda a, n: 0 if n >= w else pack([v >> n for v in lanes(a, w)], w)


def sar(w):
    return lambda a, n: pack([v >> min(n, w - 1) for v in lanes(a, w, True)], w)


def pmaddwd(a, b):
    x, y = lanes(a, 16, True), lanes(b, 16, True)
    return pack([x[0] * y[0] + x[1] * y[1], x[2] * y[2] + x[3] * y[3]], 32)


BINARY = {
    "paddb": (0xFC, lane(lambda x, y: x + y, 8)),
    "paddw": (0xFD, lane(lambda x, y: x + y, 16)),
    "paddd": (0xFE, lane(lambda x, y: x + y, 32)),
    "psubb": (0xF8, lane(lambda x, y: x - y, 8)),
    "psubw": (0xF9, lane(lambda x, y: x - y, 16)),
    "psubd": (0xFA, lane(lambda x, y: x - y, 32)),
    "paddsb": (0xEC, lane(lambda x, y: ssat(8)(x + y), 8, True)),
    "paddsw": (0xED, lane(lambda x, y: ssat(16)(x + y), 16, True)),
    "psubsb": (0xE8, lane(lambda x, y: ssat(8)(x - y), 8, True)),
    "psubsw": (0xE9, lane(lambda x, y: ssat(16)(x - y), 16, True)),
    "paddusb": (0xDC, lane(lambda x, y: usat(8)(x + y), 8)),
    "paddusw": (0xDD, lane(lambda x, y: usat(16)(x + y), 16)),
    "psubusb": (0xD8, lane(lambda x, y: usat(8)(x - y), 8)),
    "psubusw": (0xD9, lane(lambda x, y: usat(16)(x - y), 16)),
    "pmullw": (0xD5, lane(lambda x, y: x * y, 16, True)),
    "pmulhw": (0xE5, lane(lambda x, y: (x * y) >> 16, 16, True)),
    "pmaddwd": (0xF5, pmaddwd),
    "pand": (0xDB, lambda a, b: a & b),
    "pandn": (0xDF, lambda a, b: ~a & b),
    "por": (0xEB, lambda a, b: a | b),
    "pxor": (0xEF, lambda a, b: a ^ b),
    "pcmpeqb": (0x74, lane(lambda x, y: -(x == y), 8)),
    "pcmpeqw": (0x75, lane(lambda x, y: -(x == y), 16)),
    "pcmpeqd": (0x76, lane(lambda x, y: -(x == y), 32)),
    "pcmpgtb": (0x64, lane(lambda x, y: -(x > y), 8, True)),
    "pcmpgtw": (0x65, lane(lambda x, y: -(x > y), 16, True)),
    "pcmpgtd": (0x66, lane(lambda x, y: -(x > y), 32, True)),
    "punpcklbw": (0x60, interleave(8, 0)),
    "punpcklwd": (0x61, interleave(16, 0)),
    "punpckldq": (0x62, interleave(32, 0)),
    "punpckhbw": (0x68, interleave(8, 1)),
    "punpckhwd": (0x69, interleave(16, 1)),
    "punpckhdq": (0x6A, interleave(32, 1)),
    "packsswb": (0x63, packs(16, ssat(8))),
    "packssdw": (0x6B, packs(32, ssat(16))),
    "packuswb": (0x67, packs(16, usat(8))),
}
SHIFTS = {
    "psrlw": (0xD1, 0x71, 2, shr(16)), "psraw": (0xE1, 0x71, 4, sar(16)), "psllw": (0xF1, 0x71, 6, shl(16)),
    "psrld": (0xD2, 0x72, 2, shr(32)), "psrad": (0xE2, 0x72, 4, sar(32)), "pslld": (0xF2, 0x72, 6, shl(32)),
    "psrlq": (0xD3, 0x73, 2, shr(64)), "psllq": (0xF3, 0x73, 6, shl(64)),
}
M64 = 0xFFFFFFFFFFFFFFFF
PRE = ("8b4c2404 0f6f01 0f6f4908",
       ["mov ecx, dword ptr [esp + 4]", "movq mm0, qword ptr [ecx]", "movq mm1, qword ptr [ecx + 8]"])
POST = ("0f7f4110 0f77 c3", ["movq qword ptr [ecx + 0x10], mm0", "emms", "ret"])


def imm(n):
    return str(n) if n < 10 else hex(n)


def body(hexcode, texts):
    return (f"{PRE[0]} {hexcode} {POST[0]}", PRE[1] + texts + POST[1])


def cases():
    out = {}
    for name, (op, fn) in BINARY.items():
        size = "dword" if name.startswith("punpckl") else "qword"
        out[f"mmx_{name}"] = body(f"0f{op:02x}c1", [f"{name} mm0, mm1"]) + (PAIRS, lambda a, b, f=fn: f(a, b) & M64)
        out[f"mmx_{name}_m"] = body(f"0f{op:02x}4108", [f"{name} mm0, {size} ptr [ecx + 8]"]) + (
            PAIRS, lambda a, b, f=fn: f(a, b) & M64)
    for name, (op, grp, reg, fn) in SHIFTS.items():
        out[f"mmx_{name}"] = body(f"0f{op:02x}c1", [f"{name} mm0, mm1"]) + (COUNTS, lambda a, b, f=fn: f(a, b) & M64)
        for n in (0, 7, 15, 16, 31, 32, 63, 64, 255):
            out[f"mmx_{name}_i{n}"] = body(f"0f{grp:02x}{0xC0 | reg << 3:02x}{n:02x}", [f"{name} mm0, {imm(n)}"]) + (
                PAIRS[:2], lambda a, b, f=fn, n=n: f(a, n) & M64)
    hi = FILL & 0xFFFFFFFF00000000
    out["mmx_movd_load"] = (
        "8b4c2404 0f6f01 0f6e4108 0f7f4110 0f77 c3",
        ["mov ecx, dword ptr [esp + 4]", "movq mm0, qword ptr [ecx]", "movd mm0, dword ptr [ecx + 8]",
         "movq qword ptr [ecx + 0x10], mm0", "emms", "ret"], PAIRS, lambda a, b: b & 0xFFFFFFFF)
    out["mmx_movd_from_gpr"] = (
        "8b4c2404 0f6f01 8b4108 0f6ec0 0f7f4110 0f77 c3",
        ["mov ecx, dword ptr [esp + 4]", "movq mm0, qword ptr [ecx]", "mov eax, dword ptr [ecx + 8]",
         "movd mm0, eax", "movq qword ptr [ecx + 0x10], mm0", "emms", "ret"], PAIRS, lambda a, b: b & 0xFFFFFFFF)
    out["mmx_movd_store"] = (
        "8b4c2404 0f6f01 0f7e4110 0f77 c3",
        ["mov ecx, dword ptr [esp + 4]", "movq mm0, qword ptr [ecx]", "movd dword ptr [ecx + 0x10], mm0", "emms",
         "ret"], PAIRS, lambda a, b: hi | a & 0xFFFFFFFF)
    out["mmx_movd_to_gpr"] = (
        "8b4c2404 0f6f01 0f7ec0 894110 0f77 c3",
        ["mov ecx, dword ptr [esp + 4]", "movq mm0, qword ptr [ecx]", "movd eax, mm0",
         "mov dword ptr [ecx + 0x10], eax", "emms", "ret"], PAIRS, lambda a, b: hi | a & 0xFFFFFFFF)
    out["mmx_movq_load_form"] = (
        "8b4c2404 0f6f01 0f6fc8 0f7f4910 0f77 c3",
        ["mov ecx, dword ptr [esp + 4]", "movq mm0, qword ptr [ecx]", "movq mm1, mm0",
         "movq qword ptr [ecx + 0x10], mm1", "emms", "ret"], PAIRS, lambda a, b: a)
    out["mmx_movq_store_form"] = (
        "8b4c2404 0f6f01 0f7fc1 0f7f4910 0f77 c3",
        ["mov ecx, dword ptr [esp + 4]", "movq mm0, qword ptr [ecx]", "movq mm1, mm0",
         "movq qword ptr [ecx + 0x10], mm1", "emms", "ret"], PAIRS, lambda a, b: a)
    out["mmx_top"] = (
        "8b4c2404 d9e8 0f6f01 dfe0 894110 0f77 c3",
        ["mov ecx, dword ptr [esp + 4]", "fld1", "movq mm0, qword ptr [ecx]", "fnstsw ax",
         "mov dword ptr [ecx + 0x10], eax", "emms", "ret"], PAIRS, lambda a, b: hi)
    out["mmx_emms"] = (
        "8b4c2404 0f6f01 0f77 d9e8 dd5910 c3",
        ["mov ecx, dword ptr [esp + 4]", "movq mm0, qword ptr [ecx]", "emms", "fld1",
         "fstp qword ptr [ecx + 0x10]", "ret"], PAIRS, lambda a, b: struct.unpack("<Q", struct.pack("<d", 1.0))[0])
    out["mmx_emms_tags"] = (
        "8b4c2404 d9e8 0f77 d97118 668b4120 894110 c3",
        ["mov ecx, dword ptr [esp + 4]", "fld1", "emms", "fnstenv [ecx + 0x18]", "mov ax, word ptr [ecx + 0x20]",
         "mov dword ptr [ecx + 0x10], eax", "ret"], PAIRS[:1], lambda a, b: hi | 0xFFFF)
    return out


CASES = cases()


def run_case(w, H, va, a, b):
    H.put(w, H.OUT, struct.pack("<4Q", a, b, FILL, 0), 32)
    eax = w.call(va, [H.OUT])[0]
    return struct.unpack("<Q", w.read(H.OUT + 0x10, 8))[0], eax


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["mmx"])
    ap.add_argument("--lib", required=True)
    ap.add_argument("--module", required=True)
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(HERE, "..", "..", "harness"))
    import run as H
    L = H.load_lib(a.lib)
    exports = json.load(open(a.module))["exports"]
    native, oracle = H.Native(L), H.Oracle(L, [])
    bad = 0
    for name, (_, _, inputs, golden) in CASES.items():
        fails = []
        for x, y in inputs:
            want = golden(x, y)
            got, geax = run_case(native, H, exports[name], x, y)
            ora, oeax = run_case(oracle, H, exports[name], x, y)
            if got != want or ora != want or (name == "mmx_movd_to_gpr" and geax != oeax):
                fails.append(f"in {x:016X},{y:016X} sdm {want:016X} native {got:016X} oracle {ora:016X}")
        bad += bool(fails)
        print(f"{'FAIL' if fails else 'PASS'} {name}: {len(inputs)} inputs" + ("; " + "; ".join(fails[:3]) if fails else ""))
    print(f"{'PASS' if not bad else 'FAIL'} mmx: {len(CASES) - bad} of {len(CASES)} cases match the SDM golden and unicorn")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
