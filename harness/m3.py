import argparse
import ctypes
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "tools", "recomp"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import devpaths

devpaths.export_dxvk_lib()
devpaths.export_game()

import pe as pefile
import test_plan

BASE = 0x10000000
D = 0x00032000
S = 0x00033000
RT_STACK_TOP = 0x001FFF00
CRASHES = os.path.expanduser("~/Library/Logs/DiagnosticReports")
IMPORTS = {"KERNEL32.dll": ["CreateThread", "Sleep", "TlsAlloc", "TlsGetValue", "TlsSetValue", "WaitForSingleObject"]}
TLS = {"data": b"M3TLSOK!" + bytes(range(8)), "zero_fill": 16}
FUNCS = ("CountMain", "Writer", "Spin", "TlsThread", "TlsCase", "StaticTls", "RegWorker", "RegCase")
REG_N = 20000000


def u32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


def code(iat, va, tls_index):
    call = {n: b"\xff\x15" + u32(iat[n]) for n in IMPORTS["KERNEL32.dll"]}
    static = lambda dst: (b"\x64\xa1\x2c\x00\x00\x00" + b"\x8b\x0d" + u32(tls_index) + b"\x8b\x04\x88" +
                          b"\x8b\x08" + b"\x89\x0d" + u32(dst) + b"\x8b\x48\x04" + b"\x89\x0d" + u32(dst + 4))
    loop = b"\x83\x3d" + u32(D) + b"\x00" + b"\x74\xf7"
    start_worker = (b"\x6a\x00" * 2 + b"\x68" + u32(D + 0x40) + b"\x68" + u32(va["RegWorker"]) + b"\x6a\x00" * 2 +
                    call["CreateThread"])
    return {
        "RegWorker": (b"\x8b\x4c\x24\x04\xba" + u32(REG_N) + b"\xff\x01\x4a\x75\xfb\x33\xc0\xc2\x04\x00"),
        "RegCase": (start_worker + b"\xa3" + u32(D + 0x44) + start_worker + b"\xa3" + u32(D + 0x48) +
                    b"\x6a\xff\xff\x35" + u32(D + 0x44) + call["WaitForSingleObject"] +
                    b"\x6a\xff\xff\x35" + u32(D + 0x48) + call["WaitForSingleObject"] +
                    b"\xa1" + u32(D + 0x40) + b"\xc3"),
        "CountMain": b"\xff\x05" + u32(D + 0x10) + b"\xb8\x01\x00\x00\x00\xc2\x0c\x00",
        "Writer": b"\x6a\x32" + call["Sleep"] + b"\xc7\x05" + u32(D) + u32(1) + b"\x33\xc0\xc2\x04\x00",
        "Spin": (b"\xc7\x05" + u32(D) + u32(0) + b"\x6a\x00" * 3 + b"\x68" + u32(va["Writer"]) + b"\x6a\x00" * 2 +
                 call["CreateThread"] + loop + b"\xb8\x07\x00\x00\x00\xc3"),
        "TlsThread": (b"\xff\x35" + u32(D + 0x20) + call["TlsGetValue"] + b"\xa3" + u32(D + 4) +
                      b"\x68" + u32(0x2222) + b"\xff\x35" + u32(D + 0x20) + call["TlsSetValue"] +
                      b"\xff\x35" + u32(D + 0x20) + call["TlsGetValue"] + b"\xa3" + u32(D + 8) +
                      static(D + 0x28) + b"\x33\xc0\xc2\x04\x00"),
        "TlsCase": (call["TlsAlloc"] + b"\xa3" + u32(D + 0x20) + b"\x68" + u32(0x1111) + b"\x50" + call["TlsSetValue"] +
                    b"\x6a\x00" * 3 + b"\x68" + u32(va["TlsThread"]) + b"\x6a\x00" * 2 + call["CreateThread"] +
                    b"\x6a\xff\x50" + call["WaitForSingleObject"] + b"\xff\x35" + u32(D + 0x20) +
                    call["TlsGetValue"] + b"\xc3"),
        "StaticTls": static(D + 0x30) + b"\xc3",
    }


def build():
    iat = {n: 0 for n in IMPORTS["KERNEL32.dll"]}
    va = {n: BASE + 0x1000 for n in FUNCS}
    for _ in range(3):
        funcs = code(iat, va, 0)
        data = test_plan.make_pe(funcs, imports=IMPORTS, tls=TLS)
        p = pefile.parse(data)
        iat = {sym: BASE + rva for dll, sym, rva in p.imports}
        va = {n: BASE + r for n, r in p.exports.items()}
        tls_index = p.tls["index"]
    funcs = code(iat, va, tls_index)
    data = bytearray(test_plan.make_pe(funcs, imports=IMPORTS, tls=TLS))
    p = pefile.parse(bytes(data))
    assert {n: BASE + r for n, r in p.exports.items()} == va
    text = 0x400
    rel = va["CountMain"] - (BASE + 0x1000 + 5)
    data[text:text + 8] = b"\xe9" + u32(rel) + b"\x90\x90\x90"
    return bytes(data)


def write(out):
    os.makedirs(out, exist_ok=True)
    data = build()
    path = os.path.join(out, "m3.dll")
    if not os.path.exists(path) or open(path, "rb").read() != data:
        open(path, "wb").write(data)
    layout = os.path.join(out, "m3-layout.toml")
    text = f"[modules.m3]\nbase = 0x{BASE:08X}\n\n[runtime]\ndata_cells = 0x00031000\n"
    if not os.path.exists(layout) or open(layout).read() != text:
        open(layout, "w").write(text)
    return 0


class Lib:
    def __init__(self, path, game=None, overlay=None):
        L = self.L = ctypes.CDLL(path)
        L.rt_arena_new.restype = ctypes.c_void_p
        L.rt_arena_use.argtypes = [ctypes.c_void_p]
        L.rt_shim_call.restype = ctypes.c_uint32
        L.rt_shim_call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32)]
        L.rt_harness_call.restype = ctypes.c_uint32
        L.rt_harness_call.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32)]
        L.rt_harness_begin.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        L.rt_module_load.restype = ctypes.c_uint32
        L.rt_module_load.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32)]
        L.rt_get_last_error.restype = ctypes.c_uint32
        L.xbox_ReadTimeStampCounter.restype = ctypes.c_uint64
        L.rt_cpu_swaps.restype = ctypes.c_uint64
        self.arena = L.rt_arena_new()
        L.rt_arena_use(self.arena)
        L.rt_load()
        rc = L.rt_harness_begin(game.encode() if game else None, overlay.encode() if overlay else None)
        assert rc == 0, rc
        self.strings = S

    def write(self, va, data):
        ctypes.memmove(self.arena + va, data, len(data))

    def read(self, va, n):
        return ctypes.string_at(self.arena + va, n)

    def word(self, va):
        return struct.unpack("<I", self.read(va, 4))[0]

    def put(self, data):
        if isinstance(data, str):
            data = data.encode() + b"\0"
        va = self.strings
        self.write(va, data)
        self.strings += (len(data) + 15) & ~15
        return va

    def shim(self, dll, name, *args):
        a = (ctypes.c_uint32 * 16)(*[x & 0xFFFFFFFF for x in args])
        return self.L.rt_shim_call(dll.encode(), name.encode(), a)

    def call(self, va, *args):
        a = (ctypes.c_uint32 * 8)(*args)
        return self.L.rt_harness_call(va, len(args), a)

    def err(self):
        return self.L.rt_get_last_error()


class Report:
    def __init__(self):
        self.ok = True

    def check(self, label, good, detail=""):
        self.ok &= bool(good)
        print(f"{'PASS' if good else 'FAIL'} {label}{': ' + str(detail) if detail and not good else ''}", flush=True)


def k32(L, name, *a):
    return L.shim("kernel32.dll", name, *a)


def case_threads(L, mod, r):
    t0 = time.time()

    def hung():
        print("FAIL threads spin-wait did not finish within 5 s (back-edge yield broken?)", flush=True)
        os._exit(1)

    guard = threading.Timer(5.0, hung)
    guard.start()
    ret = L.call(mod["exports"]["Spin"])
    guard.cancel()
    dt = time.time() - t0
    r.check(f"threads spin-wait on a flag set by another thread after Sleep(50) ({dt:.3f} s)",
            ret == 7 and L.word(D) == 1 and dt < 1.0, f"ret {ret} flag {L.word(D)} {dt:.3f}s")
    ret = L.call(mod["exports"]["TlsCase"])
    r.check("threads TlsAlloc slot starts NULL in a new thread", L.word(D + 4) == 0, hex(L.word(D + 4)))
    r.check("threads TlsAlloc slot holds a value per thread", ret == 0x1111 and L.word(D + 8) == 0x2222,
            f"main 0x{ret:X} thread 0x{L.word(D + 8):X}")
    L.call(mod["exports"]["StaticTls"])
    tmpl = TLS["data"][:8]
    r.check("threads static TLS copy equals the template (worker)", L.read(D + 0x28, 8) == tmpl, L.read(D + 0x28, 8))
    r.check("threads static TLS copy equals the template (primary)", L.read(D + 0x30, 8) == tmpl, L.read(D + 0x30, 8))
    before = L.L.rt_cpu_swaps()
    ret = L.call(mod["exports"]["RegCase"])
    swaps = L.L.rt_cpu_swaps() - before
    r.check(f"threads registers survive preemption: 2 x {REG_N} plain increments, {swaps} swaps",
            ret == 2 * REG_N and swaps >= 4, f"count {ret} swaps {swaps}")


def case_files(L, game, overlay, r):
    k = lambda *a: k32(L, *a)
    gfile = os.path.join(game, "Vampire", "pack000.vpk")
    h = k("CreateFileA", L.put("C:/VTMB/Vampire/PACK000.VPK"), 0x80000000, 1, 0, 3, 0x80, 0)
    buf, got = L.put(bytes(4096)), L.put(bytes(4))
    ok = h != 0xFFFFFFFF and k("ReadFile", h, buf, 4096, got, 0) == 1
    r.check("files C:/VTMB/Vampire/PACK000.VPK reads the game file's bytes",
            ok and L.read(buf, L.word(got)) == open(gfile, "rb").read(4096), f"handle 0x{h:X}")
    h2 = k("CreateFileA", L.put("C:/VTMB/Vampire/PACK000.VPK"), 0x80000000, 1, 0, 3, 0x80, 0)
    rc = k("ReadFile", h2, 0x7FFFF000, 0x10000, got, 0)
    r.check("files ReadFile into a buffer that runs past the guest arena fails with 998", rc == 0 and L.err() == 998,
            f"rc {rc} error {L.err()}")
    k("CloseHandle", h2)
    k("CloseHandle", h)
    h = k("CreateFileA", L.put("C:\\vtmb\\VAMPIRE\\cfg\\M3TEST.CFG"), 0x40000000, 0, 0, 2, 0x80, 0)
    data = L.put(b"m3 test\n")
    ok = h != 0xFFFFFFFF and k("WriteFile", h, data, 8, got, 0) == 1
    k("CloseHandle", h)
    target = os.path.join(overlay, "files", "Vampire", "cfg", "M3TEST.CFG")
    r.check("files a write lands in the overlay as files/Vampire/cfg/M3TEST.CFG",
            ok and os.path.exists(target) and open(target, "rb").read() == b"m3 test\n", target)
    gdummy = os.path.join(game, "Vampire", "cfg", "dummy.txt")
    before = hashlib.sha256(open(gdummy, "rb").read()).hexdigest()
    h = k("CreateFileA", L.put("C:\\VTMB\\Vampire\\cfg\\dummy.txt"), 0xC0000000, 0, 0, 3, 0x80, 0)
    k("CloseHandle", h)
    after = hashlib.sha256(open(gdummy, "rb").read()).hexdigest()
    copy = os.path.join(overlay, "files", "Vampire", "cfg", "dummy.txt")
    r.check("files a write-open of a game file makes an overlay copy and leaves the game file unchanged",
            h != 0xFFFFFFFF and os.path.exists(copy) and before == after, f"handle 0x{h:X}")
    os.remove(copy)
    ok = k("DeleteFileA", L.put("C:\\VTMB\\Vampire\\cfg\\dummy.txt")) == 0 and L.err() == 5
    r.check("files deleting a game-only file fails with error 5", ok, L.err())
    h = k("CreateFileA", L.put("C:\\VTMB\\..\\x"), 0x80000000, 1, 0, 3, 0x80, 0)
    r.check("files C:\\VTMB\\..\\x fails with error 3", h == 0xFFFFFFFF and L.err() == 3, f"0x{h:X} error {L.err()}")


def case_registry(L, r):
    a = lambda *x: L.shim("advapi32.dll", *x)
    hk, disp = L.put(bytes(4)), L.put(bytes(4))
    rc = a("RegCreateKeyExA", 0x80000001, L.put("Software\\M3Test"), 0, 0, 0, 0xF003F, 0, hk, disp)
    key = L.word(hk)
    dword = L.put(u32(0x1234ABCD))
    rc |= a("RegSetValueExA", key, L.put("Dword"), 0, 4, dword, 4)
    s = L.put("hello registry")
    rc |= a("RegSetValueExA", key, L.put("Text"), 0, 1, s, 15)
    out, size, typ = L.put(bytes(64)), L.put(u32(64)), L.put(bytes(4))
    rc2 = a("RegQueryValueExA", key, L.put("Dword"), 0, typ, out, size)
    r.check("registry DWORD round-trip", rc == 0 and rc2 == 0 and L.word(out) == 0x1234ABCD and L.word(typ) == 4,
            f"{rc} {rc2} 0x{L.word(out):X}")
    L.write(size, u32(64))
    rc2 = a("RegQueryValueExA", key, L.put("Text"), 0, typ, out, size)
    r.check("registry string round-trip", rc2 == 0 and L.read(out, 15) == b"hello registry\0" and L.word(size) == 15,
            f"{rc2} {L.read(out, 15)}")
    L.write(size, u32(64))
    r.check("registry missing value gives 2", a("RegQueryValueExA", key, L.put("Nope"), 0, typ, out, size) == 2)
    L.write(size, u32(4))
    rc2 = a("RegQueryValueExA", key, L.put("Text"), 0, typ, out, size)
    r.check("registry short buffer gives 234 and the needed size", rc2 == 234 and L.word(size) == 15,
            f"{rc2} size {L.word(size)}")
    a("RegCloseKey", key)


def case_modules(L, mod, r):
    err = ctypes.c_uint32()
    exports = mod["exports"]
    h1 = L.L.rt_module_load(b"m3.dll", 1, ctypes.byref(err))
    h2 = L.L.rt_module_load(b"C:\\VTMB\\Bin\\M3.DLL", 1, ctypes.byref(err))
    r.check("modules LoadLibrary returns the base, any path and case", h1 == mod["base"] == h2, f"0x{h1:X} 0x{h2:X}")
    r.check("modules DllMain runs once across repeated loads", L.word(D + 0x10) == 1, L.word(D + 0x10))
    by_name = k32(L, "GetProcAddress", h1, L.put("Spin"))
    r.check("modules GetProcAddress by name gives the export VA", by_name == exports["Spin"], f"0x{by_name:X}")
    names = sorted(exports)
    ordinal = 1 + names.index("Spin")
    by_ord = k32(L, "GetProcAddress", h1, ordinal)
    r.check("modules GetProcAddress by ordinal gives the export VA", by_ord == exports["Spin"], f"0x{by_ord:X}")
    h = k32(L, "LoadLibraryA", L.put("nope.dll"))
    r.check("modules unknown module gives NULL and 126", h == 0 and L.err() == 126, f"0x{h:X} {L.err()}")
    p = k32(L, "GetProcAddress", h1, L.put("NoSuchExport"))
    r.check("modules unknown name gives NULL and 127", p == 0 and L.err() == 127, f"0x{p:X} {L.err()}")


def case_exceptions(L, args, r):
    info = L.put(struct.pack("<IIII", 0x1000, L.put("m3 worker"), 0xFFFFFFFF, 0))
    ret = k32(L, "RaiseException", 0x406D1388, 0, 4, info)
    r.check("exceptions RaiseException(0x406D1388) returns to the caller", ret == 0)
    p = subprocess.run([sys.executable, "-I", "-B", __file__, "raise", "--lib", args.lib, "--module", args.module],
                       capture_output=True, text=True)
    o = p.stdout + p.stderr
    r.check("exceptions thread name is logged", 'named "m3 worker"' in o, o[-300:])
    r.check("exceptions another code stops with exit 12, the code and the frame chain",
            p.returncode == 12 and "0xE06D7363" in o and "SEH chain" in o, f"exit {p.returncode}: {o[-400:]}")


def case_time(L, r):
    tick = lambda: k32(L, "GetTickCount")
    tgt = lambda: L.shim("winmm.dll", "timeGetTime")
    q = L.put(bytes(8))

    def qpc():
        k32(L, "QueryPerformanceCounter", q)
        return struct.unpack("<Q", L.read(q, 8))[0]

    f = L.put(bytes(8))
    k32(L, "QueryPerformanceFrequency", f)
    freq = struct.unpack("<Q", L.read(f, 8))[0]
    a = (tick(), tgt(), qpc(), L.L.xbox_ReadTimeStampCounter())
    k32(L, "Sleep", 1000)
    b = (tick(), tgt(), qpc(), L.L.xbox_ReadTimeStampCounter())
    secs = [((b[0] - a[0]) & 0xFFFFFFFF) / 1000, ((b[1] - a[1]) & 0xFFFFFFFF) / 1000, (b[2] - a[2]) / freq,
            (b[3] - a[3]) / 2e9]
    spread = (max(secs) - min(secs)) / min(secs)
    r.check(f"time sources agree within 1 % over Sleep(1000): {[round(s, 4) for s in secs]}",
            spread < 0.01 and min(secs) >= 0.999, f"spread {spread:.4f}")
    last, back = qpc(), 0
    for _ in range(10000):
        v = qpc()
        back += v < last
        last = v
    r.check("time QPC never goes backwards over 10,000 reads", back == 0, back)


def cases(args):
    mod = json.load(open(args.module))
    r = Report()
    overlay = args.overlay or tempfile.mkdtemp(prefix="m3-overlay-")
    os.makedirs(os.path.join(overlay, "files"), exist_ok=True)
    reg = os.path.join(overlay, "registry.reg")
    if not os.path.exists(reg):
        open(reg, "w").write("REGEDIT4\n")
    L = Lib(args.lib, args.game, overlay)
    groups = {"threads": lambda: case_threads(L, mod, r), "files": lambda: case_files(L, args.game, overlay, r),
              "registry": lambda: case_registry(L, r), "modules": lambda: case_modules(L, mod, r),
              "exceptions": lambda: case_exceptions(L, args, r), "time": lambda: case_time(L, r)}
    for name, fn in groups.items():
        if not args.only or args.only == name:
            fn()
    if not args.overlay:
        shutil.rmtree(overlay, ignore_errors=True)
    return 0 if r.ok else 1


def raise_mode(args):
    L = Lib(args.lib)
    info = L.put(struct.pack("<IIII", 0x1000, L.put("m3 worker"), 0xFFFFFFFF, 0))
    k32(L, "RaiseException", 0x406D1388, 0, 4, info)
    k32(L, "RaiseException", 0xE06D7363, 1, 3, L.put(bytes(12)))
    return 0


def run_exe(exe, overlay, extra, timeout=120):
    before = set(glob.glob(os.path.join(CRASHES, "bloodlines-native*")))
    t0 = time.time()
    p = subprocess.run([exe, "--run", "--overlay", overlay, *extra], capture_output=True, text=True, timeout=timeout)
    dt = time.time() - t0
    crashes = sorted(set(glob.glob(os.path.join(CRASHES, "bloodlines-native*"))) - before)
    tsv = sorted(glob.glob(os.path.join(overlay, "logs", "first-calls-*.tsv")))
    rows = [l.rstrip("\n").split("\t") for l in open(tsv[-1])][1:] if tsv else []
    return p.returncode, p.stderr, dt, crashes, rows


def stage_check(log, r, marker="M3_CONSOLE_READY"):
    lines = log.splitlines()
    stages = [(int(m.group(1)), l) for l in lines for m in [re.match(r"rt: stage (\d) ", l)] if m]
    order = [n for n, _ in stages]
    four = [l for n, l in stages if n == 4]
    others = [n for n in order if n != 4]
    nonzero = all(not l.endswith(" = 0") for l in four)
    r.check("AC1 stages 1-8 once each and in order, one stage 4 line per module, each non-zero",
            others == [1, 2, 3, 5, 6, 7, 8] and order[:3] == [1, 2, 3] and len(four) == len(set(four)) and four
            and nonzero and order.index(5) > max(i for i, n in enumerate(order) if n == 4), order)
    s5 = next((l for n, l in stages if n == 5), "")
    r.check("AC2 stage 5 has four argument values", len(re.findall(r" 0x[0-9A-F]{8}", s5.split("args", 1)[-1])) >= 3
            and re.search(r" 1 \"", s5) is not None, s5)
    i7 = next((i for i, l in enumerate(lines) if l.startswith("rt: stage 7 ")), None)
    r.check("AC3 stage 7 names an engine writer and the next line is a guest line",
            i7 is not None and "writer engine+0x" in lines[i7] and lines[i7 + 1].startswith("guest: "),
            lines[i7] if i7 is not None else "no stage 7")
    i8 = next((i for i, l in enumerate(lines) if l.startswith("rt: stage 8")), None)
    hit = [i for i, l in enumerate(lines) if l.startswith("guest: ") and l[7:].rstrip() == marker]
    r.check(f"AC4 guest line {marker} after stage 7 triggers stage 8",
            i7 is not None and i8 is not None and hit and i7 < hit[0] < i8, (i7, hit[:1], i8))
    return stages


def boot(args):
    r = Report()
    marker = "M3_CONSOLE_READY"
    if "+echo" in args.args:
        marker = args.args[args.args.index("+echo") + 1]
    probe = os.path.join(args.game, "m3-overlay-probe")
    dotdot = "/tmp/m3-nope/.." + os.path.join(args.game, "m3-overlay-probe")
    for bad, cwd in ((args.game, None), (probe, None), ("m3-overlay-probe", args.game), (dotdot, None)):
        p = subprocess.run([args.exe, "--run", "--overlay", bad], capture_output=True, text=True, timeout=60, cwd=cwd)
        r.check(f"overlay {bad}{' from ' + cwd if cwd else ''} is refused with exit 1 and nothing created",
                p.returncode == 1 and "run refused" in p.stderr and not os.path.exists(probe),
                f"exit {p.returncode}: {p.stderr[-200:]}")
    rc, log, dt, crashes, rows = run_exe(args.exe, args.overlay, ["--until", "console", "--"] + args.args)
    r.check(f"AC1 exit 0 ({dt:.1f} s)", rc == 0, rc)
    stage_check(log, r, marker)
    kinds = {}
    for row in rows:
        kinds[row[2]] = kinds.get(row[2], 0) + 1
    r.check(f"AC9 first-call rows per kind {kinds}, 0 stub rows", rows and kinds.get("stub", 0) == 0, kinds)
    for row in rows:
        if row[2] != "shim":
            print(f"  {row[2]} {row[1]} from {row[3]} {row[4]} calls {row[5]}")
    launcher = [row for row in rows if row[3] == "launcher"]
    r.check("AC2 a first-call row has caller module launcher", bool(launcher))
    creates = sum(int(row[5]) for row in rows if row[1] == "KERNEL32.dll!CreateThread")
    lines = len(re.findall(r"^rt: thread 0x[0-9A-F]+ created by", log, re.M))
    r.check(f"AC10 one rt: thread line per CreateThread call ({creates})", creates == lines, f"{creates} vs {lines}")
    r.check("AC5 stage 6 window line and window id", re.search(r"^rt: stage 6 window \d+x\d+$", log, re.M) and
            re.search(r"^rt: window id \d+$", log, re.M))
    r.check("AC8 no crash report", not crashes, crashes)
    reg = os.path.join(args.overlay, "registry.reg")
    r.check("AC12 registry.reg parses as REGEDIT4", os.path.exists(reg) and open(reg).readline().strip() == "REGEDIT4")
    hits = re.findall(r"^rt: reg query (.*) (hit|miss)$", log, re.M)
    print(f"  registry queries: {len(hits)}, hits {sum(h == 'hit' for _, h in hits)}")
    open(os.path.join(args.overlay, "boot-summary.json"), "w").write(json.dumps(
        {"exit": rc, "seconds": round(dt, 1), "kinds": kinds, "non_shim_rows": [row for row in rows if row[2] != "shim"],
         "registry_hits": [k for k, h in hits if h == "hit"], "registry_misses": [k for k, h in hits if h == "miss"]},
        indent=1))
    return 0 if r.ok else 1


def close(args):
    r = Report()
    rc, log, dt, crashes, rows = run_exe(args.exe, args.overlay, ["--close-after", "5"], timeout=180)
    i8 = log.find("rt: stage 8")
    t8 = None
    r.check(f"AC6 close-after 5 exits 0 ({dt:.1f} s)", rc == 0, rc)
    r.check("AC6 WM_CLOSE reached the guest", "rt: message WM_CLOSE to 0x" in log and i8 >= 0)
    m = re.search(r"^rt: max pump gap (\d+) ms", log, re.M)
    print(f"  {m.group(0) if m else 'no pump gap line'}; forced quit: {'forced quit' in log}")
    r.check("AC6 exit within 15 s of the close request", "forced quit" not in log, t8)
    r.check("AC8 no crash report", not crashes, crashes)
    return 0 if r.ok else 1


def pump(args):
    r = Report()
    rc, log, dt, crashes, rows = run_exe(args.exe, args.overlay, ["--close-after", "60"], timeout=300)
    m = re.search(r"^rt: max pump gap (\d+) ms", log, re.M)
    gap = int(m.group(1)) if m else None
    r.check(f"AC7 max pump gap {gap} ms <= 100 over a 60 s run (exit {rc}, {dt:.0f} s)",
            gap is not None and gap <= 100 and rc == 0, gap)
    return 0 if r.ok else 1


def repeat(args):
    r = Report()
    base = args.overlay or tempfile.mkdtemp(prefix="m3-repeat-")
    good = 0
    for i in range(args.n):
        o = os.path.join(base, f"run{i + 1}")
        rc, log, dt, crashes, rows = run_exe(args.exe, o, ["--until", "console"])
        faults = log.count("rt: FAULT")
        ok = rc == 0 and not crashes and faults == 0
        good += ok
        r.check(f"AC8 run {i + 1}: exit {rc}, {faults} fault reports, {len(crashes)} crash reports, {dt:.1f} s",
                ok or (faults == 1 and not crashes), log[-500:])
    r.check(f"AC8 {good} of {args.n} exit 0", good == args.n)
    return 0 if r.ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["write", "cases", "raise", "boot", "close", "pump", "repeat"])
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--out")
    ap.add_argument("--lib")
    ap.add_argument("--module")
    ap.add_argument("--only")
    ap.add_argument("--game", default=devpaths.GAME)
    ap.add_argument("--overlay")
    ap.add_argument("--exe")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    if a.mode == "write":
        return write(a.out)
    return {"cases": cases, "raise": raise_mode, "boot": boot, "close": close, "pump": pump, "repeat": repeat}[a.mode](a)


if __name__ == "__main__":
    sys.exit(main())
