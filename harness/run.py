import argparse
import ctypes
import hashlib
import json
import os
import struct
import subprocess
import sys

RT_STACK_TOP = 0x001FFF00
RT_TIB = 0x00020000
GDT = 0x1FF00000
LOW = 0x4000
SCRATCH = 0x00300000
OBJ_A, OBJ_B = SCRATCH, SCRATCH + 0x400
IN_A, IN_B, OUT = SCRATCH + 0x1000, SCRATCH + 0x2000, SCRATCH + 0x3000
ARGS = SCRATCH + 0x4000
SENTINEL = 0xFE000F00
GUARD = 16
FPCW = 0x027F

EXPORTS = {
    "uni_ctor": "??0CUniformRandomStream@@QAE@XZ",
    "uni_seed": "?SetSeed@CUniformRandomStream@@UAEXH@Z",
    "uni_int": "?RandomInt@CUniformRandomStream@@UAEHHH@Z",
    "uni_float": "?RandomFloat@CUniformRandomStream@@UAEMMM@Z",
    "gauss_ctor": "??0CGaussianRandomStream@@QAE@PAVIUniformRandomStream@@@Z",
    "gauss_float": "?RandomFloat@CGaussianRandomStream@@QAEMMM@Z",
    "RandomSeed": "RandomSeed",
    "RandomInt": "RandomInt",
    "RandomFloat": "RandomFloat",
}


def load_lib(path):
    L = ctypes.CDLL(path)
    L.rt_arena_new.restype = ctypes.c_void_p
    L.rt_arena_use.argtypes = [ctypes.c_void_p]
    L.rt_get_reg.restype = ctypes.c_uint32
    L.rt_set_reg.argtypes = [ctypes.c_int, ctypes.c_uint32]
    L.rt_call.argtypes = [ctypes.c_uint32]
    L.rt_fp_st.restype = ctypes.c_double
    L.rt_fp_cw.restype = ctypes.c_uint16
    L.rt_module_info.restype = ctypes.c_uint32
    L.rt_shim_call.restype = ctypes.c_uint32
    L.rt_shim_call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32 * 12)]
    L.rt_last_error.restype = ctypes.c_uint32
    L.rt_set_extra_path.argtypes = [ctypes.c_char_p]
    return L


def f80_of_double(d):
    if d == 0.0:
        return (0, 0x8000 if struct.pack("<d", d)[7] & 0x80 else 0)
    bits = struct.unpack("<Q", struct.pack("<d", d))[0]
    sign = 0x8000 if bits >> 63 else 0
    e = (bits >> 52) & 0x7FF
    m = bits & ((1 << 52) - 1)
    if e == 0x7FF:
        return ((1 << 63) | (m << 11), sign | 0x7FFF)
    if e == 0:
        shift = m.bit_length()
        return (m << (64 - shift), sign | (shift - 1074 + 16383 - 1))
    return (((1 << 52) | m) << 11, sign | (e - 1023 + 16383))


def descriptor(base, limit, code, dpl):
    gran = limit > 0xFFFFF
    if gran:
        limit >>= 12
    return ((limit & 0xFFFF) | (base & 0xFFFFFF) << 16 | (0xB if code else 3) << 40 | 1 << 44 | dpl << 45 |
            1 << 47 | (limit >> 16) << 48 | 1 << 54 | gran << 55 | (base >> 24) << 56)


class World:
    def __init__(self, L):
        self.L = L
        self.arena = L.rt_arena_new()
        L.rt_arena_use(self.arena)
        L.rt_load()
        self.base = L.rt_module_info(0)
        self.entry = L.rt_module_info(2)

    def write(self, va, data):
        ctypes.memmove(self.arena + va, data, len(data))

    def read(self, va, n):
        return ctypes.string_at(self.arena + va, n)

    def u32(self, va, v):
        self.write(va, struct.pack("<I", v & 0xFFFFFFFF))

    def stack(self, args):
        esp = RT_STACK_TOP - 4 * len(args) - 4
        self.write(esp, struct.pack(f"<{len(args) + 1}I", SENTINEL, *[a & 0xFFFFFFFF for a in args]))
        return esp


class Native(World):
    def call(self, va, args=(), ecx=0):
        L = self.L
        L.rt_arena_use(self.arena)
        L.rt_reset_cpu()
        L.rt_set_reg(4, self.stack(args))
        L.rt_set_reg(1, ecx)
        L.rt_call(va)
        st0 = f80_of_double(L.rt_fp_st(0)) if L.rt_fp_depth() else None
        return L.rt_get_reg(0), L.rt_get_reg(2), st0


class Oracle(World):
    def __init__(self, L, log):
        super().__init__(L)
        from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32, UC_PROT_ALL, Uc
        from unicorn import x86_const as X
        self.X = X
        self.uc = uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.log = log
        log.append(f"unicorn reset FPCW = 0x{uc.reg_read(X.UC_X86_REG_FPCW):04X}")
        uc.mem_map_ptr(LOW, GDT - LOW, UC_PROT_ALL, self.arena + LOW)
        uc.mem_map(GDT, 0x1000)
        uc.mem_map_ptr(0xFE000000, 0x1000, UC_PROT_ALL, self.arena + 0xFE000000)
        for index, base, limit, code, dpl in ((14, 0, 0xFFFFF000, 1, 3), (15, 0, 0xFFFFF000, 0, 3),
                                              (16, RT_TIB, 0xFFF, 0, 3), (17, 0, 0xFFFFF000, 0, 0)):
            uc.mem_write(GDT + 8 * index, struct.pack("<Q", descriptor(base, limit, code, dpl)))
        uc.reg_write(X.UC_X86_REG_GDTR, (0, GDT, 31 * 8 - 1, 0))
        for reg, sel in ((X.UC_X86_REG_SS, 0x88), (X.UC_X86_REG_CS, 0x73), (X.UC_X86_REG_DS, 0x7B),
                         (X.UC_X86_REG_ES, 0x7B), (X.UC_X86_REG_FS, 0x83)):
            uc.reg_write(reg, sel)
        uc.hook_add(UC_HOOK_CODE, self._shim, begin=0xFE000000, end=SENTINEL - 1)
        self.gprs = [X.UC_X86_REG_EAX, X.UC_X86_REG_ECX, X.UC_X86_REG_EDX, X.UC_X86_REG_EBX,
                     X.UC_X86_REG_ESP, X.UC_X86_REG_EBP, X.UC_X86_REG_ESI, X.UC_X86_REG_EDI]
        self.runs = self.fpcw_ok = 0

    def _shim(self, uc, address, size, user):
        L = self.L
        for i, r in enumerate(self.gprs):
            L.rt_set_reg(i, uc.reg_read(r))
        L.rt_arena_use(self.arena)
        ret = struct.unpack("<I", self.read(uc.reg_read(self.X.UC_X86_REG_ESP), 4))[0]
        L.rt_call(address)
        for i, r in enumerate(self.gprs):
            uc.reg_write(r, L.rt_get_reg(i))
        uc.reg_write(self.X.UC_X86_REG_EIP, ret)

    def call(self, va, args=(), ecx=0):
        X, uc = self.X, self.uc
        self.L.rt_arena_use(self.arena)
        for r in self.gprs:
            uc.reg_write(r, 0)
        uc.reg_write(X.UC_X86_REG_ESP, self.stack(args))
        uc.reg_write(X.UC_X86_REG_ECX, ecx)
        uc.reg_write(X.UC_X86_REG_EFLAGS, 0x202)
        uc.reg_write(X.UC_X86_REG_FPSW, 0)
        uc.reg_write(X.UC_X86_REG_FPTAG, 0xFFFF)
        uc.reg_write(X.UC_X86_REG_FPCW, FPCW)
        self.runs += 1
        self.fpcw_ok += uc.reg_read(X.UC_X86_REG_FPCW) == FPCW
        uc.emu_start(va, SENTINEL)
        top = (uc.reg_read(X.UC_X86_REG_FPSW) >> 11) & 7
        st0 = None
        if top != 0:
            m, e = uc.reg_read(X.UC_X86_REG_FP0 + top)
            st0 = (m, e)
        return uc.reg_read(X.UC_X86_REG_EAX), uc.reg_read(X.UC_X86_REG_EDX), st0


def run_random(w, inputs, out):
    e = w.exports
    t = inputs["tables"]
    for raw in inputs["random"]:
        case = {"seed": raw["seed"]}
        for key, table in (("int", "int"), ("float", "float"), ("gauss", "gauss"), ("gint", "int"), ("gfloat", "float")):
            case[key] = [t[table][int(ch, 16)] for ch in raw[key]]
        res = []
        w.call(e[EXPORTS["uni_ctor"]], ecx=OBJ_A)
        w.call(e[EXPORTS["uni_seed"]], [case["seed"]], ecx=OBJ_A)
        for lo, hi in case["int"]:
            res.append(w.call(e[EXPORTS["uni_int"]], [lo, hi], ecx=OBJ_A)[0])
        for lo, hi in case["float"]:
            res.append(w.call(e[EXPORTS["uni_float"]], [lo, hi], ecx=OBJ_A)[2])
        w.call(e[EXPORTS["uni_ctor"]], ecx=OBJ_B)
        w.call(e[EXPORTS["uni_seed"]], [case["seed"]], ecx=OBJ_B)
        w.call(e[EXPORTS["gauss_ctor"]], [OBJ_B], ecx=OBJ_A)
        for m, s in case["gauss"]:
            res.append(w.call(e[EXPORTS["gauss_float"]], [m, s], ecx=OBJ_A)[2])
        w.call(e[EXPORTS["RandomSeed"]], [case["seed"]])
        for lo, hi in case["gint"]:
            res.append(w.call(e[EXPORTS["RandomInt"]], [lo, hi])[0])
        for lo, hi in case["gfloat"]:
            res.append(w.call(e[EXPORTS["RandomFloat"]], [lo, hi])[2])
        out.setdefault("random", []).append(res)


def put(w, va, data, size):
    w.write(va - GUARD, b"\xCC" * (size + 2 * GUARD))
    w.write(va, data[:size])


def window(w, va, size):
    return w.read(va - GUARD, size + 2 * GUARD).hex()


def cstr(w, va, s):
    w.write(va, s + b"\0")


def run_strings(w, inputs, out):
    e = w.exports
    res = {}
    for name, cases in inputs["strings"].items():
        va = e[name]
        rows = []
        for c in cases:
            if name in ("Q_strncpy", "Q_FileBase", "Q_StripExtension", "Q_trimspace"):
                s, size = bytes.fromhex(c["s"]), c["size"]
                cstr(w, IN_A, s)
                put(w, OUT, b"", max(size, 1))
                r = w.call(va, [OUT, IN_A, size] if name == "Q_strncpy" else [IN_A, OUT, size])
                rows.append([r[0], window(w, OUT, max(size, 1))])
            elif name in ("Q_strcasecmp", "Q_strncasecmp", "Q_stristr"):
                cstr(w, IN_A, bytes.fromhex(c["a"]))
                cstr(w, IN_B, bytes.fromhex(c["b"]))
                args = [IN_A, IN_B] + ([c["n"]] if name == "Q_strncasecmp" else [])
                rows.append([w.call(va, args)[0]])
            elif name in ("Q_atoi", "Q_atof"):
                cstr(w, IN_A, bytes.fromhex(c["s"]))
                r = w.call(va, [IN_A])
                rows.append([r[0]] if name == "Q_atoi" else [r[2]])
            elif name == "Q_snprintf":
                cstr(w, IN_A, bytes.fromhex(c["fmt"]))
                args, sva = [OUT, c["size"], IN_A], ARGS
                for kind, v in c["args"]:
                    if kind == "i":
                        args.append(v)
                    elif kind == "d":
                        args += [v & 0xFFFFFFFF, v >> 32]
                    else:
                        cstr(w, sva, bytes.fromhex(v))
                        args.append(sva)
                        sva += 0x100
                put(w, OUT, b"", c["size"])
                r = w.call(va, args)
                rows.append([r[0], window(w, OUT, c["size"])])
            else:
                s, size = bytes.fromhex(c["s"]), c["size"]
                put(w, OUT, s + b"\0", size)
                extra = [c["delim"]] if name == "Q_FixSlashes" else [c["n"]] if name == "Q_strnlwr" else []
                r = w.call(va, [OUT] + extra)
                rows.append([r[0], window(w, OUT, size)])
        res[name] = rows
    out["strings"] = res


def dllmain(w):
    return w.call(w.entry, [w.base, 1, 0])[0]


def attach(w, module):
    w.exports = {n: v for n, v in module["exports"].items()}


def record(a):
    L = load_lib(a.lib)
    module = json.load(open(a.module))
    inputs = json.load(open(a.inputs))
    log = []
    w = Oracle(L, log)
    attach(w, module)
    out = {"dllmain": dllmain(w)}
    run_random(w, inputs, out)
    run_strings(w, inputs, out)
    log.append(f"oracle runs {w.runs}, FPCW set to 0x{FPCW:04X} and read back as 0x{FPCW:04X} on {w.fpcw_ok}")
    json.dump(out, open(a.recordings, "w"), separators=(",", ":"))
    open(a.recordings + ".log", "w").write("\n".join(log) + "\n")
    print("\n".join(log))
    return 0 if w.runs == w.fpcw_ok else 1


def compare(name, want, got):
    bad = [i for i, (x, y) in enumerate(zip(want, got)) if json.dumps(x) != json.dumps(y)]
    if len(want) != len(got):
        bad.append(min(len(want), len(got)))
    status = "PASS" if not bad else "FAIL"
    print(f"{status} {name}: {len(want) - len(bad)}/{len(want)} match the oracle")
    for i in bad[:3]:
        print(f"    input {i}: expected {want[i] if i < len(want) else None} got {got[i] if i < len(got) else None}")
    return not bad


def check(a):
    L = load_lib(a.lib)
    module = json.load(open(a.module))
    inputs = json.load(open(a.inputs))
    want = json.load(open(a.recordings))
    w = Native(L)
    attach(w, module)
    before = (ctypes.c_uint64 * 4)()
    L.rt_counters(before)
    got = {"dllmain": dllmain(w)}
    run_random(w, inputs, got)
    run_strings(w, inputs, got)
    after = (ctypes.c_uint64 * 4)()
    L.rt_counters(after)
    got = json.loads(json.dumps(got))
    ok = True
    print(f"{'PASS' if got['dllmain'] == 1 == want['dllmain'] else 'FAIL'} DllMain(DLL_PROCESS_ATTACH): native {got['dllmain']}, oracle {want['dllmain']}")
    ok &= got["dllmain"] == 1 == want["dllmain"]
    labels = ["uniform RandomInt", "uniform RandomFloat", "gaussian RandomFloat", "global RandomInt", "global RandomFloat"]
    total = 0
    for k, label in enumerate(labels):
        wv = [x for case in want["random"] for x in case[1000 * k:1000 * (k + 1)]]
        gv = [x for case in got["random"] for x in case[1000 * k:1000 * (k + 1)]]
        total += len(wv)
        ok &= compare(label, wv, gv)
    print(f"random-stream results compared: {total}")
    for name in inputs["strings"]:
        ok &= compare(name, want["strings"][name], got["strings"][name])
    icalls = after[0] - before[0]
    shims = after[1] - before[1]
    misses = after[2] - before[2]
    extra = a.extra if os.path.exists(a.extra) else None
    extra_n = len(open(extra).read().split()) if extra else 0
    print(f"{'PASS' if icalls - shims > 0 and misses == 0 and extra_n == 0 else 'FAIL'} indirect calls: "
          f"{icalls} total, {icalls - shims} to guest code, {shims} to shims, {misses} misses, extra-entries {extra_n}")
    ok &= icalls - shims > 0 and misses == 0 and extra_n == 0
    L.rt_cpuid.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32 * 4)]
    regs = (ctypes.c_uint32 * 4)()
    L.rt_cpuid(1, ctypes.byref(regs))
    out = list(regs)
    simd = (out[3] >> 23 & 1, out[3] >> 25 & 1, out[3] >> 26 & 1, out[2] & 1)
    pf = {n: shim(L, "IsProcessorFeaturePresent", v) for n, v in
          (("MMX", 3), ("SSE", 6), ("SSE2", 10), ("3DNow", 7), ("SSE3", 13))}
    nosimd = simd == (0, 0, 0, 0) and not any(pf.values())
    print(f"{'PASS' if nosimd else 'FAIL'} no SIMD: cpuid.1 MMX/SSE/SSE2/SSE3 bits {simd}, IsProcessorFeaturePresent {pf}")
    ok &= nosimd
    text = json.dumps(got, sort_keys=True, separators=(",", ":"))
    open(a.results, "w").write(text)
    print(f"results {a.results} sha256 {hashlib.sha256(text.encode()).hexdigest()}")
    return 0 if ok else 1


def shim(L, name, *args, dll="kernel32.dll"):
    return L.rt_shim_call(dll.encode(), name.encode(), ctypes.byref((ctypes.c_uint32 * 12)(*args)))


def cp1252_char(b):
    return chr(b) if b in (0x81, 0x8D, 0x8F, 0x90, 0x9D) else bytes([b]).decode("cp1252")


def ctype1_expected(b):
    import unicodedata
    c = cp1252_char(b)
    cat = unicodedata.category(c)
    t = 0
    if cat == "Lu":
        t |= 0x101
    elif cat == "Ll":
        t |= 0x102
    elif cat[0] == "L":
        t |= 0x100
    elif cat == "Nd":
        t |= 0x4
    elif cat == "Zs":
        t |= 0x48
    elif cat == "Cc":
        t |= 0x20 | (0x8 if 9 <= b <= 13 else 0) | (0x40 if b == 9 else 0)
    else:
        t |= 0x10
    if c in "0123456789abcdefABCDEF":
        t |= 0x80
    return t


def case_expected(b, upper):
    c = cp1252_char(b)
    m = c.upper() if upper else c.lower()
    try:
        return m.encode("cp1252")[0] if len(m) == 1 else b
    except UnicodeEncodeError:
        return b


def shims(a):
    L = load_lib(a.lib)
    w = Native(L)
    L.rt_arena_use(w.arena)
    ok = True

    def check(label, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"{'PASS' if good else 'FAIL'} shim {label}{': ' + detail if detail and not good else ''}")

    w.write(IN_A, bytes(range(256)))
    put(w, OUT, b"", 512)
    r = shim(L, "GetStringTypeA", 0x409, 1, IN_A, 256, OUT)
    got = struct.unpack("<256H", w.read(OUT, 512))
    bad = [f"0x{b:02X}: 0x{got[b]:03X} != 0x{ctype1_expected(b):03X}" for b in range(256) if got[b] != ctype1_expected(b)]
    check("GetStringTypeA CT_CTYPE1 0x00-0xFF", r == 1 and not bad, "; ".join(bad[:6]))
    for flag, upper in ((0x200, True), (0x100, False)):
        put(w, OUT, b"", 256)
        n = shim(L, "LCMapStringA", 0x409, flag, IN_A, 256, OUT, 256)
        want = bytes(case_expected(b, upper) for b in range(256))
        got = w.read(OUT, 256)
        bad = [f"0x{b:02X}->0x{got[b]:02X} want 0x{want[b]:02X}" for b in range(256) if got[b] != want[b]]
        check(f"LCMapStringA 0x{flag:03X}", n == 256 and not bad, "; ".join(bad[:6]))
    check("LCMapStringA cchDest=0 returns the needed size", shim(L, "LCMapStringA", 0x409, 0x200, IN_A, 10, 0, 0) == 10)
    check("LCMapStringA short buffer fails with 122",
          shim(L, "LCMapStringA", 0x409, 0x200, IN_A, 10, OUT, 5) == 0 and L.rt_last_error() == 122)
    put(w, OUT, b"\xEE" * 20, 20)
    r = shim(L, "GetCPInfo", 1252, OUT)
    check("GetCPInfo(1252) MaxCharSize 1, DefaultChar '?', no lead bytes",
          r == 1 and w.read(OUT, 20) == struct.pack("<I", 1) + b"?\0" + bytes(12) + b"\xEE\xEE")
    h = shim(L, "HeapCreate", 0, 0x1000, 0)
    p = shim(L, "HeapAlloc", h, 8, 100)
    check("HeapAlloc HEAP_ZERO_MEMORY", p and p % 8 == 0 and w.read(p, 100) == bytes(100) and shim(L, "HeapSize", h, 0, p) == 100)
    w.write(p, bytes(range(100)))
    check("HeapReAlloc IN_PLACE_ONLY grow fails, block unchanged",
          shim(L, "HeapReAlloc", h, 0x10, p, 200) == 0 and w.read(p, 100) == bytes(range(100)) and shim(L, "HeapSize", h, 0, p) == 100)
    check("HeapReAlloc IN_PLACE_ONLY shrink keeps the block",
          shim(L, "HeapReAlloc", h, 0x10, p, 50) == p and shim(L, "HeapSize", h, 0, p) == 50)
    q = shim(L, "HeapReAlloc", h, 8, p, 300)
    check("HeapReAlloc HEAP_ZERO_MEMORY grow copies and zero-fills",
          q and w.read(q, 50) == bytes(range(50)) and w.read(q + 50, 250) == bytes(250) and shim(L, "HeapSize", h, 0, q) == 300)
    check("HeapReAlloc too large returns NULL", shim(L, "HeapReAlloc", h, 0, q, 0xFFFFFFF0) == 0)
    check("HeapAlloc too large returns NULL", shim(L, "HeapAlloc", h, 0, 0xFFFFFFF0) == 0)
    check("HeapFree", shim(L, "HeapFree", h, 0, q) != 0)
    v = shim(L, "VirtualAlloc", 0, 0x1234, 0x3000, 4)
    v2 = shim(L, "VirtualAlloc", 0, 0x10000, 0x3000, 4)
    check("VirtualAlloc reserves 64 KiB-aligned zeroed regions",
          v and v % 0x10000 == 0 and v2 == v + 0x10000 and w.read(v, 0x1234) == bytes(0x1234))
    check("VirtualAlloc commit inside a reservation rounds down to the page",
          shim(L, "VirtualAlloc", v + 0x1010, 0x10, 0x1000, 4) == v + 0x1000)
    check("VirtualAlloc too large returns NULL with an error",
          shim(L, "VirtualAlloc", 0, 0xFFFFF000, 0x3000, 4) == 0 and L.rt_last_error() == 8)
    check("VirtualAlloc size 0 returns NULL", shim(L, "VirtualAlloc", 0, 0, 0x3000, 4) == 0)
    return 0 if ok else 1


def null(a):
    L = load_lib(a.lib)
    w = Native(L)
    attach(w, json.load(open(a.module)))
    w.call(w.exports["_Q_strlen"], [0])
    print("returned without a fault")
    return 0


def miss(a):
    L = load_lib(a.lib)
    L.rt_set_extra_path(a.extra.encode())
    w = Native(L)
    attach(w, json.load(open(a.module)))
    w.call(w.entry + 1)
    return 0


def expect_abort(a, mode, needle):
    p = subprocess.run([sys.executable, "-I", "-B", __file__, mode, *sys.argv[2:]], capture_output=True, text=True)
    msg = (p.stdout + p.stderr).strip()
    good = p.returncode != 0 and needle in msg
    print(f"{'PASS' if good else 'FAIL'} {mode}: exit {p.returncode}, {msg}")
    return good


def aborts(a):
    module = json.load(open(a.module))
    ok = expect_abort(a, "null", "guest address 0x00000000")
    probe = f"0x{module['entry'] + 1:08X}"
    before = open(a.extra).read() if os.path.exists(a.extra) else ""
    ok &= expect_abort(a, "miss", probe)
    after = open(a.extra).read() if os.path.exists(a.extra) else ""
    appended = after[len(before):].split()
    print(f"{'PASS' if appended == [probe] else 'FAIL'} miss appended {appended} to {a.extra}")
    ok &= appended == [probe]
    open(a.extra, "w").write(before)
    return 0 if ok else 1


CONFORM = {
    "fnclex": (b"", [(0, 2)]),
    "fninit": (struct.pack("<H", 0x0C7F), [(4, 6)]),
    "ffree": (b"", []),
    "fnstenv": (b"", [(0, 12), (28, 30)]),
    "fldenv": (struct.pack("<IIIIIII", 0x0E7F, 0x4100, 0xFFFF, 0, 0, 0, 0), [(32, 34)]),
    "fnsave": (bytes(120) + struct.pack("<d", 2.5), [(0, 12), (28, 58), (128, 152)]),
    "lock": (struct.pack("<III", 5, 1, 1), [(0, 12)]),
    "pause": (b"", []),
    "fcom_ze": (b"", []),
    "fxam_ze": (b"", []),
}
LOCK_DATA = b"\x7f" * 0x20 + bytes(0x20) + b"\x81" * 4
for op in ("add", "sub", "and", "or", "xor", "inc", "dec"):
    for bits in (8, 16, 32):
        CONFORM[f"lock_{op}{bits}"] = (LOCK_DATA, [(0, 4), (0x20, 0x25)])
for bits in (8, 16, 32):
    CONFORM[f"xchg{bits}"] = (LOCK_DATA, [(0, 4), (0x20, 0x24)])
CONFORM["lock_split"] = (LOCK_DATA, [(0xC, 0x14), (0x20, 0x25)])
SW_EXCEPTIONS = 0x80FF
SW_NOT_CC = 0xFFFF & ~0x4700
ZE, ES = 0x0004, 0x0080


def conform_case(w, va, data, ranges):
    put(w, OUT, data, 160)
    eax, _, st0 = w.call(va, [OUT])
    return [eax & 0xFFFF, st0] + [w.read(OUT + lo, hi - lo).hex() for lo, hi in ranges]


def conform(a):
    L = load_lib(a.lib)
    exports = json.load(open(a.module))["exports"]
    log = []
    oracle, native = Oracle(L, log), Native(L)
    ok = True
    for name, (data, ranges) in CONFORM.items():
        want = conform_case(oracle, exports[name], data, ranges)
        got = conform_case(native, exports[name], data, ranges)
        if name == "fnclex":
            good = fnclex_words(want, got)
        else:
            good = json.dumps(want) == json.dumps(got) and (name != "pause" or got[0] == 7)
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} conform {name}: oracle {want} native {got}")
    good = oracle.runs == oracle.fpcw_ok
    print(f"{'PASS' if good else 'FAIL'} conform FPCW set to 0x{FPCW:04X} and read back on {oracle.fpcw_ok} of {oracle.runs} oracle runs")
    return 0 if ok and good else 1


XF_PAIRS = [(0, 0), (1, 2), (2, 1), (0x80000000, 1), (0x7FFFFFFF, 0xFFFFFFFF), (3, 0), (1, 0), (0xFFFFFFFF, 1)]


def xflags_case(w, va, pair):
    put(w, OUT, struct.pack("<II", *pair), 8)
    return w.call(va, [OUT])[0]


def xflags(a):
    L = load_lib(a.lib)
    exports = json.load(open(a.module))["exports"]
    names = sorted(n for n in exports if n.startswith("xf_") and n != "xf_helper" and not n.endswith("_t"))
    names = [n for n in names if not a.only or any(o in n for o in a.only.split(","))]
    if not names:
        print(f"FAIL xflags: no case matches --only {a.only}")
        return 1
    log = []
    oracle, native = Oracle(L, log), Native(L)
    failed = []
    for name in names:
        want = [xflags_case(oracle, exports[name], p) for p in XF_PAIRS]
        got = [xflags_case(native, exports[name], p) for p in XF_PAIRS]
        bad = [i for i, (x, y) in enumerate(zip(want, got)) if x != y]
        if bad:
            failed.append(name)
        print(f"{'FAIL' if bad else 'PASS'} xflags {name}: {len(XF_PAIRS) - len(bad)}/{len(XF_PAIRS)} match the oracle")
        for i in bad:
            print(f"    input {i} a=0x{XF_PAIRS[i][0]:08X} b=0x{XF_PAIRS[i][1]:08X}: "
                  f"oracle 0x{want[i]:08X} native 0x{got[i]:08X}")
    good = oracle.runs == oracle.fpcw_ok
    print(f"{'PASS' if good else 'FAIL'} xflags FPCW set to 0x{FPCW:04X} and read back on {oracle.fpcw_ok} of {oracle.runs} oracle runs")
    print(f"xflags: {len(names) - len(failed)} of {len(names)} cases match the oracle")
    if failed:
        print("xflags failed: " + " ".join(failed))
    return 0 if not failed and good else 1


def fnclex_words(want, got):
    before = [int.from_bytes(bytes.fromhex(r[2]), "little") for r in (want, got)]
    after = [r[0] for r in (want, got)]
    print(f"    fnclex status word before: oracle 0x{before[0]:04X} native 0x{before[1]:04X}; "
          f"after: oracle 0x{after[0]:04X} native 0x{after[1]:04X}; compared under mask 0x{SW_NOT_CC:04X} (not C0-C3), "
          f"ES {'set' if before[0] & ES else 'clear'} before in the oracle")
    return (all(b & ZE for b in before) and all(not x & SW_EXCEPTIONS for x in after)
            and before[0] & SW_NOT_CC == before[1] & SW_NOT_CC and after[0] & SW_NOT_CC == after[1] & SW_NOT_CC
            and want[1] == got[1])


def trapcall(a):
    L = load_lib(a.lib)
    w = Native(L)
    attach(w, json.load(open(a.module)))
    w.call(w.exports["simd"], [0])
    print("returned without a trap")
    return 0


def trap(a):
    va = json.load(open(a.module))["exports"]["simd"]
    p = subprocess.run([sys.executable, "-I", "-B", __file__, "trapcall", *sys.argv[2:]], capture_output=True, text=True)
    msg = (p.stdout + p.stderr).strip()
    good = p.returncode == 9 and "movss xmm0, dword ptr [esp + 4]" in msg and f"0x{va:08X}" in msg
    print(f"{'PASS' if good else 'FAIL'} trap: exit {p.returncode}, {msg}")
    return 0 if good else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["record", "check", "null", "miss", "aborts", "shims", "conform", "trap", "trapcall",
                                         "xflags"])
    ap.add_argument("--lib", required=True)
    ap.add_argument("--module", required=True)
    ap.add_argument("--inputs")
    ap.add_argument("--recordings")
    ap.add_argument("--results")
    ap.add_argument("--extra")
    ap.add_argument("--only")
    a = ap.parse_args()
    return {"record": record, "check": check, "null": null, "miss": miss, "aborts": aborts, "shims": shims,
            "conform": conform, "trap": trap, "trapcall": trapcall, "xflags": xflags}[a.mode](a)


if __name__ == "__main__":
    sys.exit(main())
