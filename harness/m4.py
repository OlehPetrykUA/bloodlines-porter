#!/usr/bin/env python3
import argparse
import base64
import glob
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import devpaths

devpaths.export_dxvk_lib()
devpaths.export_game()

CRASHES = os.path.expanduser("~/Library/Logs/DiagnosticReports")
X_DEFAULT = devpaths.build("all", "meson", "bloodlines-native")
REFDIR = devpaths.refdir()
DOC_DEFAULT = os.path.join(HERE, "..", "docs", "recomp.md")
DXVK_ORDER = [1, 2, 3, 5, 6, 9, 7, 8, 10, 11]
NULL_ORDER = [1, 2, 3, 5, 6, 7, 8]
GDI32_SHIMS = {"CreateFontA", "DeleteObject", "GetStockObject", "SelectObject", "SetBkMode", "SetTextColor"}
DXVK_COMMIT = "dd3a05b"
PROBE_DEFAULT = devpaths.build("probe", "b", "probe")
ROLES = {"linked", "loaded", "build-only"}
EXIT_NAMES = {1: "refused", 5: "off-main-thread or game-folder write", 6: "host crash", 10: "stub reached",
              12: "unhandled guest exception", 15: "gfx image or device-lost failure"}
DEF = {"tol": 16, "pct": 0.99, "mad": 2.0, "luma": 1.0, "p99": 33.3, "pump": 100, "colours": 1000, "block": 32, "block_frac": 0.5,
       "frames_per_60s": 600, "soak_seconds": 120, "size": (1280, 1024)}

RE_FRAMES = re.compile(r"^rt: frames n (\d+) p50 ([\d.]+) p95 ([\d.]+) p99 ([\d.]+) max ([\d.]+)\s*$", re.M)
RE_PUMP = re.compile(r"^rt: max pump gap (\d+) ms", re.M)
RE_PUMP_STEADY = re.compile(r"^rt: max pump gap steady (\d+) ms", re.M)
RE_SHADERS = re.compile(r"^rt: shaders vs_1_1 (\d+) vs_2_0 (\d+) ps_1_1 (\d+) ps_1_4 (\d+) ps_2_0 (\d+) failed (\d+)\s*$", re.M)
RE_LOCKS = re.compile(r"^rt: gfx locks (\d+) out-of-arena (\d+)(?: late-writes (\d+))?\s*$", re.M)
RE_LOST = re.compile(r"^rt: gfx lost (\d+)(?: ms)?\s*$", re.M)
RE_BACK = re.compile(r"^rt: gfx frame backbuffer (\d+)x(\d+) (\S+) depth (\d+)x(\d+) (\S+)\s*$", re.M)
RE_IMAGE = re.compile(r"^rt: gfx image (\S.*?)\s*$", re.M)
RE_DEVICE = re.compile(r'^rt: stage 9 device dxvk "([^"]*)" vendor 0x([0-9A-Fa-f]+) (\d+)x(\d+) windowed ([01]) '
                       r'format (\d+)(?: flags 0x([0-9A-Fa-f]+))?(?: backbuffers (\d+) interval 0x([0-9A-Fa-f]+))?\s*$', re.M)
RE_DISPLAY = re.compile(r'^rt: display (\d+) "([^"]*)" scale ([\d.]+) density ([\d.]+)\s*$', re.M)
RE_MARKER = re.compile(r"^guest: main menu rendering particles on frame (\d+)", re.M)
RE_REFUSED = re.compile(r"^(?:rt: )?run refused: display (\d+) not found", re.M)
CRITICAL = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
SIG = b"\x89PNG\r\n\x1a\n"


class PngError(Exception):
    pass


def png_chunks(data):
    if data[:8] != SIG:
        raise PngError("not a PNG signature")
    pos, out = 8, []
    while pos + 12 <= len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        if n > 0x7FFFFFFF:
            raise PngError(f"chunk length {n}")
        if not typ[0] & 0x20 and typ not in CRITICAL:
            raise PngError(f"unknown critical chunk {typ!r}")
        body = data[pos + 8:pos + 8 + n]
        tail = data[pos + 8 + n:pos + 12 + n]
        if len(body) != n or len(tail) != 4 or zlib.crc32(typ + body) != struct.unpack(">I", tail)[0]:
            raise PngError(f"bad chunk {typ!r}")
        out.append((typ, body))
        pos += 12 + n
        if typ == b"IEND":
            idat = [i for i, (t, _) in enumerate(out) if t == b"IDAT"]
            if not idat or idat[-1] - idat[0] != len(idat) - 1:
                raise PngError("IDAT chunks missing or not consecutive")
            return out
    raise PngError("no IEND")


def png_size(data):
    if data[:8] != SIG or data[12:16] != b"IHDR":
        raise PngError("no IHDR")
    return struct.unpack(">II", data[16:24])


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def unfilter(ft, cur, prev, bpp):
    if ft == 0:
        return cur
    if ft == 2:
        return bytearray((x + y) & 255 for x, y in zip(cur, prev))
    for i in range(len(cur)):
        a = cur[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        pred = a if ft == 1 else (a + b) >> 1 if ft == 3 else paeth(a, b, c)
        if ft not in (1, 3, 4):
            raise PngError(f"filter type {ft}")
        cur[i] = (cur[i] + pred) & 255
    return cur


def read_png(data):
    ch = png_chunks(data)
    ihdr = ch[0][1] if ch and ch[0][0] == b"IHDR" else None
    if ihdr is None or len(ihdr) != 13:
        raise PngError("no IHDR")
    w, h, depth, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", ihdr)
    if w == 0 or h == 0:
        raise PngError("zero width or height")
    if depth != 8 or ctype not in (2, 6) or comp or filt or inter:
        raise PngError(f"unsupported depth {depth} colour type {ctype} interlace {inter}")
    bpp = 3 if ctype == 2 else 4
    try:
        raw = zlib.decompress(b"".join(b for t, b in ch if t == b"IDAT"))
    except zlib.error as e:
        raise PngError(f"IDAT stream: {e}")
    stride = w * bpp
    if len(raw) != h * (stride + 1):
        raise PngError("IDAT size mismatch")
    rows, prev = [], bytearray(stride)
    for y in range(h):
        off = y * (stride + 1)
        prev = unfilter(raw[off], bytearray(raw[off + 1:off + 1 + stride]), prev, bpp)
        rows.append(bytes(prev) if bpp == 3 else bytes(b for i in range(w) for b in prev[i * 4:i * 4 + 3]))
    return w, h, rows


def write_png(w, h, rows, alpha=False, filters=(0,)):
    bpp = 4 if alpha else 3
    out, prev = bytearray(), bytes(w * bpp)
    for y, row in enumerate(rows):
        if alpha:
            row = bytes(b for i in range(w) for b in (row[i * 3:i * 3 + 3] + b"\xff"))
        ft = filters[y % len(filters)]
        f = bytearray()
        for i in range(len(row)):
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            pred = (0, a, b, (a + b) >> 1, paeth(a, b, c))[ft]
            f.append((row[i] - pred) & 255)
        out += bytes([ft]) + f
        prev = row

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    return (SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(out))) + chunk(b"IEND", b""))


def colours(rows):
    seen = set()
    for r in rows:
        seen.update(zip(r[0::3], r[1::3], r[2::3]))
    return len(seen)


def load_mask(spec, w, h):
    if spec.get("width", w) != w or spec.get("height", h) != h:
        raise ValueError(f"mask is {spec.get('width')}x{spec.get('height')}, picture {w}x{h}")
    rows = [bytearray(w) for _ in range(h)]
    if not spec.get("rects"):
        raise ValueError("mask has no rects")
    for x, y, rw, rh in spec["rects"]:
        if rw <= 0 or rh <= 0 or x < 0 or y < 0 or x + rw > w or y + rh > h:
            raise ValueError(f"rect {[x, y, rw, rh]} outside {w}x{h}")
        for yy in range(y, y + rh):
            rows[yy][x:x + rw] = b"\x01" * rw
    return rows


def compare(a, b, spec, tol=DEF["tol"], pct=DEF["pct"], mad=DEF["mad"], luma=DEF["luma"], block=DEF["block"],
            block_frac=DEF["block_frac"]):
    (w, h, ra), (w2, h2, rb) = a, b
    if (w, h) != (w2, h2):
        return False, {"error": f"sizes differ: {w}x{h} vs {w2}x{h2}"}
    mrows = load_mask(spec, w, h)
    n = good = sabs = outn = 0
    cells, badcells = {}, {}
    sc = [0, 0, 0]
    for y in range(h):
        for m in re.finditer(rb"\x01+", bytes(mrows[y])):
            s, e = m.span()
            pa, pb = ra[y][3 * s:3 * e], rb[y][3 * s:3 * e]
            n += e - s
            x = s
            while x < e:
                nx = min(e, (x // (block or w) + 1) * (block or w))
                k = (y // (block or h), x // (block or w))
                cells[k] = cells.get(k, 0) + nx - x
                x = nx
            if pa == pb:
                good += e - s
                continue
            d = [abs(p - q) for p, q in zip(pa, pb)]
            sabs += sum(d)
            for i, v in enumerate(map(max, d[0::3], d[1::3], d[2::3])):
                if v > tol:
                    k = (y // (block or h), (s + i) // (block or w))
                    badcells[k] = badcells.get(k, 0) + 1
                else:
                    good += 1
        for m in re.finditer(rb"\x00+", bytes(mrows[y])):
            s, e = m.span()
            seg = ra[y][3 * s:3 * e]
            outn += e - s
            for c in range(3):
                sc[c] += sum(seg[c::3])
    frac = good / n
    mean_abs = sabs / (3 * n)
    out_luma = (0.299 * sc[0] + 0.587 * sc[1] + 0.114 * sc[2]) / outn if outn else None
    worst = max((badcells[k] / cells[k] for k in badcells), default=0.0)
    ok = frac >= pct and mean_abs <= mad and (out_luma is None or out_luma > luma) and (not block or worst <= block_frac)
    return ok, {"worst_block": round(worst, 3), "inside_px": n, "within_tol": round(frac, 5), "mean_abs_diff": round(mean_abs, 4),
                "outside_luma": None if out_luma is None else round(out_luma, 2)}


class Rep:
    def __init__(self, quiet=False):
        self.ok = True
        self.quiet = quiet
        self.failed = []

    def check(self, label, good, detail=""):
        self.ok &= bool(good)
        if not good:
            self.failed.append(label)
        if not self.quiet:
            print(f"{'PASS' if good else 'FAIL'} {label}{': ' + str(detail) if detail and not good else ''}", flush=True)


def percentile(vals, p):
    s = sorted(vals)
    return s[max(0, -(-len(s) * p // 100) - 1)]


def frames_line(frames):
    return "rt: frames n %d p50 %.1f p95 %.1f p99 %.1f max %.1f" % (
        len(frames), percentile(frames, 50), percentile(frames, 95), percentile(frames, 99), max(frames))


def check_stages(r, log, mode="dxvk", need_quiet=False):
    st = [(int(m[1]), m[2] or "") for m in re.finditer(r"^rt: stage (\d+)(?: (.*))?$", log, re.M)]
    order = [n for n, _ in st]
    rest = [n for n in order if n != 4]
    if rest and rest[-1] == 12: rest.pop()
    s1 = next((l for n, l in st if n == 1), "")
    quiet = mode == "dxvk" and "guest-quiet" in s1.split()
    want = DXVK_ORDER[:-1] if quiet else DXVK_ORDER if mode == "dxvk" else NULL_ORDER
    label = "1-4,5,6,9,7,8,10" + ("" if quiet else ",11") if mode == "dxvk" else "1-8 (11 optional last)"
    r.check(f"stages {label} once each and in order",
            rest == want or (mode == "null" and rest == want + [11]), order)
    if need_quiet:
        r.check("stage 1 line has guest-quiet", quiet, s1)
    four = [l for n, l in st if n == 4]
    pos = [i for i, n in enumerate(order) if n == 4]
    lo, hi = (order.index(3) if 3 in order else 9e9), (order.index(5) if 5 in order else -1)
    r.check("stage 4 once per module, after 3 and before 5, each non-zero",
            bool(four) and len(four) == len(set(four)) and all(lo < i < hi for i in pos) and
            all(not l.endswith(" = 0") for l in four), four[:3])
    r.check(f"stage 1 line names gfx {mode}" + (" and -dxlevel 90" if mode == "dxvk" else ""),
            re.search(rf"\bgfx {mode}\b", s1) and (mode != "dxvk" or "-dxlevel 90" in s1), s1)
    if mode == "dxvk":
        s10 = next((l for n, l in st if n == 10), "")
        s11 = next((l for n, l in st if n == 11), "")
        r.check("stage 10 is first-present" + ("" if quiet else " and stage 11 is menu"),
                s10.startswith("first-present") and (quiet or s11.startswith("menu")), (s10, s11))
        r.check("stage 9 device line matches the format", RE_DEVICE.search(log) is not None,
                next((l for n, l in st if n == 9), "no stage 9"))
    return st


def check_stats(r, log, p99=DEF["p99"], pump=DEF["pump"], min_frames=None):
    f = RE_FRAMES.findall(log)
    if not f:
        r.check("stats rt: frames line present", False, "missing")
    else:
        n, p50, p95, q99, mx = int(f[-1][0]), *map(float, f[-1][1:])
        r.check(f"stats p99 {q99} ms <= {p99} (n {n}, p50 {p50}, p95 {p95}, max {mx})", q99 <= p99, q99)
        if min_frames is not None:
            r.check(f"stats frames {n} >= {min_frames}", n >= min_frames, n)
    g = RE_PUMP.findall(log)
    s1 = re.search(r"^rt: stage 1 (.*)$", log, re.M)
    if s1 and re.search(r"\bgfx dxvk\b", s1[1]):
        r.check("stats stage 10 line present", re.search(r"^rt: stage 10\b", log, re.M) is not None, "missing")
        sg = RE_PUMP_STEADY.findall(log)
        r.check(f"stats max pump gap steady {sg[-1] if sg else '?'} ms <= {pump}", bool(sg) and int(sg[-1]) <= pump, sg)
        r.check(f"stats max pump gap whole run {g[-1] if g else '?'} ms (informational, known first-menu-frame stall)", True, g)
    else:
        r.check(f"stats max pump gap {g[-1] if g else '?'} ms <= {pump}", bool(g) and int(g[-1]) <= pump, g)
    lost = RE_LOST.findall(log)
    r.check("stats gfx lost 0", bool(lost) and int(lost[-1]) == 0, lost)
    sh = RE_SHADERS.findall(log)
    r.check("stats shaders failed 0", bool(sh) and int(sh[-1][5]) == 0, sh)
    lk = RE_LOCKS.findall(log)
    r.check("stats gfx locks out-of-arena 0", bool(lk) and int(lk[-1][1]) == 0, lk)


BRIDGE_C = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shims", "d3d9_bridge.c")


def dxvk_config(extra, src=BRIDGE_C):
    m = re.search(r'^#define DXVK_CONFIG_VALUE "([^"]*)"', open(src).read(), re.M)
    if not m:
        raise ValueError("DXVK_CONFIG_VALUE not found in " + src)
    return m[1] + "; " + extra


def apply_dxvk_env(a):
    if getattr(a, "dxvk_config_extra", None):
        os.environ["DXVK_CONFIG"] = dxvk_config(a.dxvk_config_extra)
    if getattr(a, "dxvk_cache", None):
        os.environ["DXVK_SHADER_CACHE_PATH"] = os.path.abspath(a.dxvk_cache)


def check_faults(r, log, crashes, rc=None):
    faults = len(re.findall(r"^rt: FAULT", log, re.M))
    r.check(f"{faults} rt: FAULT blocks", faults == 0, faults)
    r.check("no new DiagnosticReports", not crashes, crashes)
    hang = re.findall(r"gfx hang: (\S.*)", log)
    dxl = re.findall(r"^.*(?:GPU likely hung|VK_ERROR_DEVICE_LOST).*$", log, re.M)
    ld = re.findall(r"load (\d+(?:\.\d+)?)\)", log)
    load = ld[-1] if ld else f"{os.getloadavg()[0]:.2f}"
    bad = bool(hang or dxl)
    r.check(f"no gfx hang stop line and no dxvk GPU-hung line: {(hang or dxl or ['none'])[0].strip()} "
            f"(load average {load}{'; rerun on a quiet machine' if bad else ''})", not bad, (hang, dxl))
    if rc is not None:
        r.check(f"exit 0 (got {rc}{' ' + EXIT_NAMES[rc] if rc in EXIT_NAMES else ''})", rc == 0, rc)


def check_images(r, log):
    base = [os.path.basename(p) for p in RE_IMAGE.findall(log)]
    count = lambda pat: sum(1 for b in base if re.match(pat, b))
    r.check("gfx images: one libSDL3, one Vulkan loader, one libdxvk_d3d9",
            count(r"libSDL3") == 1 and count(r"libvulkan(\.\d+)*\.dylib$") == 1 and count(r"libdxvk_d3d9") == 1, base)
    r.check("gfx images: libvulkan_kosmickrisp loaded, libvulkan_lvp not",
            count(r"libvulkan_kosmickrisp") >= 1 and count(r"libvulkan_lvp") == 0, base)


def check_boot(r, log, rows, dxvk_log, mode="dxvk"):
    st = check_stages(r, log, mode)
    kinds = {}
    for row in rows:
        kinds[row[2]] = kinds.get(row[2], 0) + 1
    r.check(f"first-call rows per kind {kinds}, 0 stub", bool(rows) and kinds.get("stub", 0) == 0, kinds)
    if mode != "dxvk":
        return
    d3 = [row for row in rows if row[1].lower().startswith("d3d9.dll!")]
    r.check(f"all {len(d3)} d3d9 rows are kind bridge", bool(d3) and all(x[2] == "bridge" for x in d3),
            [x[1] for x in d3 if x[2] != "bridge"][:5])
    r.check("CreateDevice reached and returned D3D_OK: first-call row plus the stage 9 device line, which is "
            "printed only after CreateDevice succeeds (the TSV has no return column)",
            any(x[1].endswith("IDirect3D9::CreateDevice") for x in d3) and RE_DEVICE.search(log) is not None)
    g = {row[1].split("!", 1)[1] for row in rows if row[1].lower().startswith("gdi32.dll!") and row[2] == "shim"}
    gall = [row for row in rows if row[1].lower().startswith("gdi32.dll!")]
    r.check("gdi32 rows are exactly M3's six shims, 0 stub", g == GDI32_SHIMS and len(g) == len(gall), sorted(g))
    r.check("no guest line Couldn't create bitmap font", "guest: Couldn't create bitmap font" not in log)
    dev = RE_DEVICE.search(log)
    r.check("stage 9 is vendor 0x1002 1280x1024 windowed 1 flags 0x52",
            dev and (dev[2], dev[3], dev[4], dev[5], dev[7]) == ("1002", "1280", "1024", "1", "52"), dev and dev[0])
    r.check("window line names vulkan", re.search(r"^rt: window .*\bvulkan\b", log, re.M) is not None)
    r.check("dxvk log is non-empty and has the KosmicKrisp Found device line",
            bool(dxvk_log) and re.search(r"Found device:.*KosmicKrisp", dxvk_log) is not None,
            "no dxvk log" if not dxvk_log else dxvk_log[:200])
    r.check("dllmain stdshader_dx9 = 1", re.search(r"^rt: dllmain stdshader_dx9 = 1\s*$", log, re.M) is not None)
    sh = RE_SHADERS.findall(log)
    r.check("shaders failed 0 and ps_2_0 above 0",
            bool(sh) and int(sh[-1][5]) == 0 and int(sh[-1][4]) > 0, sh)
    r.check("rt: gfx frame backbuffer line", RE_BACK.search(log) is not None)
    check_images(r, log)
    return st


def check_menu_marker(r, log):
    m = next((x for x in RE_MARKER.finditer(log) if int(x[1]) >= 30), None)
    s11 = re.search(r"^rt: stage 11 ", log, re.M)
    r.check("first guest line main menu rendering particles with N >= 30 comes before the stage 11 line",
            m and s11 and m.start() < s11.start(), m and m[0])


def check_png(r, data, size, min_colours):
    try:
        img = read_png(data)
    except PngError as e:
        r.check("capture PNG reads", False, e)
        return None
    r.check(f"capture is {size[0]}x{size[1]}", (img[0], img[1]) == tuple(size), f"{img[0]}x{img[1]}")
    c = colours(img[2])
    r.check(f"capture has {c} distinct colours (>= {min_colours})", c >= min_colours, c)
    return img


def parse_otool(text):
    out = []
    for l in text.splitlines()[1:]:
        m = re.match(r"\s+(\S+) \(", l)
        if m:
            out.append(m[1])
    return out


def m4_rows(doc):
    m = re.search(r"^#{1,2} M4\b.*$", doc, re.M)
    if not m:
        return None
    rest = doc[m.end():]
    nxt = re.search(r"^# ", rest, re.M)
    rows, state = [], 0
    for l in (rest[:nxt.start()] if nxt else rest).splitlines():
        if not l.lstrip().startswith("|"):
            if state == 2:
                break
            state = 0
            continue
        c = [x.strip().strip("`") for x in l.strip().strip("|").split("|")]
        if state == 1 and all(re.fullmatch(r":?-+:?", x) for x in c):
            state = 2
        elif state == 2:
            rows.append(c)
        else:
            state = 1 if c[0].lower().startswith("library") else 0
    return rows


def lipo_archs(path):
    p = subprocess.run(["lipo", "-archs", path], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else "?"


def check_libs(r, exe, otool_text, log, doc, lipo=lipo_archs, probe=None):
    rows = m4_rows(doc)
    if rows is None:
        r.check("docs/recomp.md has an M4 section", False)
        return
    allibs = parse_otool(otool_text)
    linked = [p for p in allibs if not p.startswith(("/usr/lib/", "/System/"))]
    nsys = sum(1 for c in rows if c[0].lower().startswith("macos system libraries"))
    r.check("exactly one macOS system libraries row when otool -L lists /usr/lib or /System libraries",
            nsys == 1 if len(allibs) > len(linked) else nsys <= 1, f"{nsys} rows")
    images = RE_IMAGE.findall(log)
    r.check("the log has rt: gfx image lines", bool(images))
    need = {}
    for p, role in [(p, "linked") for p in linked] + [(p, "loaded") for p in images]:
        need.setdefault(re.sub(r"(\.\d+)*\.dylib$", "", os.path.basename(p)), role)
    names = [c[0].lower() for c in rows]
    missing = [s for s in need if not any(re.search(rf"(?<!\w){re.escape(s.lower())}(?!\w)", n) for n in names)]
    r.check(f"licence table has a row for each of {len(need)} libraries", not missing, f"missing {missing}")
    sysrow = lambda c: c[0].lower().startswith("macos system libraries")
    bad = [c[0] for c in rows if len(c) < 5 or not c[2] or c[4].lower() not in ROLES or
           (not sysrow(c) and (not c[1] or not c[3]))]
    r.check("every row has name, licence and role linked|loaded|build-only; non-system rows also version and "
            "licence file", not bad, bad)
    dx = [c for c in rows if "dxvk" in c[0].lower()]
    r.check(f"dxvk row reads jm2/dxvk {DXVK_COMMIT}, Zlib",
            any("jm2/dxvk" in " ".join(c).lower() and DXVK_COMMIT in " ".join(c) and len(c) > 2 and "zlib" in c[2].lower()
                for c in dx), dx)
    arch = {p: lipo(p) for p in [exe] + images}
    r.check("lipo -archs is exactly arm64 for X and every logged gfx image",
            bool(images) and all(a == "arm64" for a in arch.values()), arch)
    if probe is not None:
        pa = lipo(probe) if os.path.exists(probe) else "missing"
        r.check(f"probe {probe} is arm64", pa == "arm64", pa)


def newest(pattern):
    f = sorted(glob.glob(pattern), key=os.path.getmtime)
    return f[-1] if f else None


def dxvk_log_text(overlay):
    fs = [f for f in glob.glob(os.path.join(overlay, "logs", "*.log")) if not os.path.basename(f).startswith("run-")]
    return "".join(open(f, errors="replace").read() for f in fs)


def run(exe, overlay, extra, timeout):
    import m3
    try:
        return m3.run_exe(exe, overlay, extra, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return None, err, timeout, [], []


def timeout_note(r, rc, log, timeout):
    if rc is None:
        last = re.findall(r"^rt: stage (\d+) (\S+)", log, re.M)
        r.check(f"run finished within {timeout} s", False, f"timed out; last stage {last[-1] if last else 'none'}")


def run_live(exe, overlay, extra, timeout, delay=None):
    before = set(glob.glob(os.path.join(CRASHES, "bloodlines-native*")))
    p = subprocess.Popen([exe, "--run", "--overlay", overlay, *extra], stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE, text=True, errors="replace")
    lines, shot, wid, timers = [], {}, [], []

    def grab():
        out = os.path.join(overlay, "window-capture.png")
        q = subprocess.run(["screencapture", "-o", f"-l{wid[0]}", out], capture_output=True, text=True)
        shot.update(rc=q.returncode, err=q.stderr.strip(), path=out)

    def pump():
        for l in p.stderr:
            lines.append(l)
            m = re.match(r"rt: window id (\d+)", l)
            if m:
                wid[:] = [m[1]]
            if l.startswith("rt: stage 11 ") and wid and delay is not None:
                timers.append(threading.Timer(delay, grab))
                timers[-1].start()

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t0 = time.time()
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        rc = None
    t.join(5)
    for tm in timers:
        tm.join((delay or 0) + 30)
    crashes = sorted(set(glob.glob(os.path.join(CRASHES, "bloodlines-native*"))) - before)
    tsv = sorted(glob.glob(os.path.join(overlay, "logs", "first-calls-*.tsv")))
    rows = [l.rstrip("\n").split("\t") for l in open(tsv[-1])][1:] if tsv else []
    return rc, "".join(lines), time.time() - t0, crashes, rows, shot


def need_exe(a):
    if not os.path.exists(a.exe):
        print(f"FAIL exe {a.exe} not found")
        return False
    return True


def overlay_of(a):
    return a.overlay or tempfile.mkdtemp(prefix="m4-overlay-")


def maybe_compare(r, a, png):
    ref = a.ref or os.path.join(REFDIR, "m4-menu.png")
    mask = a.mask or os.path.join(os.path.dirname(ref), "m4-menu-mask.json")
    if not (os.path.exists(ref) and os.path.exists(mask)):
        print(f"  no reference at {ref} (first pass: the maintainer approves the capture)")
        return
    cmp_files(r, png, ref, mask, a)


def cmp_files(r, png, ref, mask, a):
    try:
        ok, d = compare(read_png(open(png, "rb").read()), read_png(open(ref, "rb").read()),
                        json.load(open(mask)), a.tol, a.pct, a.mad, a.luma, a.block, a.block_frac)
    except (PngError, ValueError, OSError) as e:
        r.check("compare inputs read", False, e)
        return
    r.check(f"compare {d}", ok, d)
    for f in (png, ref, mask):
        print(f"  sha256 {hashlib.sha256(open(f, 'rb').read()).hexdigest()} {f}")


def cmd_boot(a):
    if not need_exe(a):
        return 1
    r, o = Rep(), overlay_of(a)
    rc, log, dt, crashes, rows = run(a.exe, o, ["--until", "menu"] + (["--display", str(a.display)] if a.display is not None else []) + (["--"] + a.args if a.args else []),
                                     a.timeout)
    timeout_note(r, rc, log, a.timeout)
    check_boot(r, log, rows, dxvk_log_text(o))
    check_faults(r, log, crashes, rc)
    print(f"  overlay {o}, {dt:.1f} s")
    return 0 if r.ok else 1


def cmd_menu(a):
    if not need_exe(a):
        return 1
    r, o = Rep(), overlay_of(a)
    _, _, refused = menu_run(r, a, o, a.display)
    r.check("the display exists", refused is None, refused and refused[0])
    return 0 if r.ok else 1


def menu_run(r, a, o, display):
    delay = None if a.no_screencap else a.screencap_delay
    extra = ["--until", "menu", "--capture-after", str(a.capture_after)] + (["--display", str(display)]
                                                                           if display is not None else [])
    rc, log, dt, crashes, rows, shot = run_live(a.exe, o, extra, a.timeout, delay)
    ref = RE_REFUSED.search(log)
    if ref:
        return log, shot, ref
    timeout_note(r, rc, log, a.timeout)
    check_stages(r, log)
    check_menu_marker(r, log)
    check_faults(r, log, crashes, rc)
    png = newest(os.path.join(o, "shots", "menu-*.png"))
    r.check("shots/menu-*.png written", png is not None, o)
    if png:
        if check_png(r, open(png, "rb").read(), a.size, DEF["colours"]):
            maybe_compare(r, a, png)
    b = RE_BACK.search(log)
    r.check("rt: gfx frame backbuffer 1280x1024 line", b and (b[1], b[2]) == ("1280", "1024"), b and b[0])
    if delay is not None:
        wh = window_size(shot)
        if wh:
            print(f"  window capture {wh[0]}x{wh[1]} ({wh[0] / a.size[0]:.2f}x of the back buffer width)")
    st = RE_FRAMES.findall(log)
    print(f"  frames line: {st[-1] if st else 'none'}; overlay {o}; {dt:.1f} s")
    return log, shot, None


def window_size(shot):
    try:
        if shot.get("rc") != 0:
            raise PngError(shot)
        data = open(shot["path"], "rb").read()
        size = png_size(data)
        try:
            if colours(read_png(data)[2]) <= 1:
                raise PngError("all one colour")
        except PngError as e:
            if str(e) == "all one colour":
                raise
        return size
    except (PngError, OSError, KeyError) as e:
        print(f"  screen capture unavailable (Screen Recording permission?) [{str(e)[:120]}]")
        return None


def soak_one(r, a, o, tag, display=None):
    rc, log, dt, crashes, rows = run(a.exe, o, ["--guest-quiet", "--close-after", str(a.close_after)] +
                                     (["--display", str(display)] if display is not None else []), a.timeout)
    timeout_note(r, rc, log, a.timeout)
    sub = Rep(quiet=True)
    check_stages(sub, log, need_quiet=True)
    r.check(f"{tag} stage order and stage 1 line", sub.ok, sub.failed)
    check_faults(r, log, crashes, rc)
    check_stats(r, log, a.p99, a.pump, int(a.frames_per_60s * a.seconds / 60))
    return log


def cmd_soak(a):
    if not need_exe(a):
        return 1
    r = Rep()
    base = a.overlay or tempfile.mkdtemp(prefix="m4-soak-")
    for i in range(a.n):
        print(f"-- run {i + 1}")
        soak_one(r, a, base, f"run {i + 1}", a.display)
    return 0 if r.ok else 1


def backing_scales(text):
    out = []
    for g in json.loads(text).get("SPDisplaysDataType", []):
        for d in g.get("spdisplays_ndrvs", []):
            px = re.match(r"(\d+) x", d.get("_spdisplays_pixels", ""))
            pt = re.match(r"(\d+) x", d.get("_spdisplays_resolution", ""))
            if px and pt:
                out.append((d.get("spdisplays_main") == "spdisplays_yes", int(px[1]) / int(pt[1])))
    return [v for m, v in sorted(out, key=lambda x: not x[0])]


def host_backing_scale(n):
    p = subprocess.run(["system_profiler", "SPDisplaysDataType", "-json"], capture_output=True, text=True)
    try:
        v = backing_scales(p.stdout)
    except ValueError:
        return None
    return v[n] if n < len(v) else None


def cmd_displays(a):
    if not need_exe(a):
        return 1
    r = Rep()
    base = a.overlay or tempfile.mkdtemp(prefix="m4-displays-")
    ran = 0
    for n in a.displays:
        print(f"-- display {n}")
        log, shot, refused = menu_run(r, a, os.path.join(base, f"d{n}"), n)
        if refused:
            if n == 0:
                r.check("display 0 is not refused", False, refused[0])
            else:
                print(f"  display {n} is not connected ({refused[0].strip()}): built-in results only, AC10 stays open")
            continue
        ran += 1
        d = [x for x in RE_DISPLAY.findall(log) if x[0] == str(n)]
        r.check(f"display {n}: rt: display line {d[:1]}", bool(d), "missing")
        wh = window_size(shot) if not a.no_screencap else None
        if d and wh is None:
            print(f"  display {n}: AC10 scale check not run")
        if d and wh:
            w, h = wh
            sc = host_backing_scale(n) or float(d[0][2])
            r.check(f"display {n}: window capture {w}x{h} = {a.size[0]}*{sc} wide, at least {a.size[1]}*{sc} high",
                    w == round(a.size[0] * sc) and h >= round(a.size[1] * sc), (w, h))
        soak_one(r, a, os.path.join(base, f"d{n}-soak"), f"display {n}", n)
    r.check(f"at least one of the displays {a.displays} ran", ran > 0)
    return 0 if r.ok else 1


def cmd_cases(a):
    if not need_exe(a):
        return 1
    r, o = Rep(), overlay_of(a)
    try:
        p = subprocess.run([a.exe, "--gfx-selftest", "--overlay", o], capture_output=True, text=True, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        r.check(f"--gfx-selftest finished within {a.timeout} s", False, "timed out")
        return 1
    res = [l for l in (p.stdout + p.stderr).splitlines() if re.match(r"(PASS|FAIL) ", l)]
    bad = [l for l in res if l.startswith("FAIL")]
    for l in res:
        print(l)
    r.check(f"{len(res) - len(bad)} PASS lines (need >= {a.min_cases}), 0 FAIL", not bad and len(res) >= a.min_cases, bad)
    r.check(f"exit 0 (got {p.returncode})", p.returncode == 0)
    return 0 if r.ok else 1


def cmd_compare(a):
    r = Rep()
    cmp_files(r, a.png, a.ref, a.mask, a)
    return 0 if r.ok else 1


def cmd_stats(a):
    r = Rep()
    log = open(a.log, errors="replace").read()
    check_stats(r, log, a.p99, a.pump, a.min_frames)
    check_faults(r, log, [])
    return 0 if r.ok else 1


def cmd_stages(a):
    r = Rep()
    check_stages(r, open(a.log, errors="replace").read(), a.mode)
    return 0 if r.ok else 1


def cmd_licences(a):
    r = Rep()
    ot = subprocess.run(["otool", "-L", a.exe], capture_output=True, text=True).stdout
    check_libs(r, a.exe, ot, open(a.log, errors="replace").read(), open(a.doc).read(), probe=a.probe)
    return 0 if r.ok else 1


def cmd_png(a):
    r = Rep()
    check_png(r, open(a.png, "rb").read(), a.size, DEF["colours"])
    return 0 if r.ok else 1


def fixture_log(order=None, p99=20.0, pump=40, fault=False, lost=0, failed=0, images=None, marker=30,
                marker_after=False, quiet=False, whole=None, steady=True):
    order = order or [1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 10] + ([] if quiet else [11])
    names = {1: 'startup gfx dxvk args -window -w 1280 -h 1024 -dxlevel 90' + (' guest-quiet' if quiet else ''), 2: "modules 23", 3: "dllmain",
             5: 'launcher 1 "x" args 0x00000001 0x00000002 0x00000003', 6: "window 1280x1024",
             12: "map sp_tutorial_1", 7: "writer engine+0x1234", 8: "console", 10: "first-present", 11: "menu",
             9: 'device dxvk "AMD Radeon" vendor 0x1002 1280x1024 windowed 1 format 22 flags 0x52'}
    out, k = ["rt: window id 4242", "rt: window 1280x1024 vulkan"], 0
    for n in order:
        if n == 11 and not marker_after:
            out += [f"guest: main menu rendering particles on frame {f}" for f in range(1, marker)] + [
                f"guest: main menu rendering particles on frame {marker}"]
        if n == 4:
            k += 1
            out.append(f"rt: stage 4 dllmain mod{k} = 1")
        else:
            out.append(f"rt: stage {n} {names[n]}")
        if n == 11 and marker_after:
            out.append(f"guest: main menu rendering particles on frame {marker}")
    imgs = images if images is not None else ["/opt/lib/libSDL3.0.dylib", "/opt/lib/libvulkan.1.dylib",
                                              "/opt/lib/libvulkan_kosmickrisp.dylib", "/opt/lib/libdxvk_d3d9.0.dylib"]
    out += [f"rt: gfx image {p}" for p in imgs]
    out.append("rt: dllmain stdshader_dx9 = 1")
    out.append("rt: gfx frame backbuffer 1280x1024 22 depth 1280x1024 75")
    if fault:
        out.append("rt: FAULT host crash (exit 6): boom")
    frames = [16.6] * 97 + [p99] * 3
    out += [frames_line(frames).replace("n 100", "n 7200"), f"rt: max pump gap {pump if whole is None else whole} ms",
            *([f"rt: max pump gap steady {pump} ms"] if steady else []),
            "rt: shaders vs_1_1 3 vs_2_0 5 ps_1_1 3 ps_1_4 1 ps_2_0 7 failed %d" % failed,
            "rt: gfx locks 900 out-of-arena 0 late-writes 0", f"rt: gfx lost {lost}"]
    return "\n".join(out) + "\n"


def fixture_rows(d3d_kind="bridge", gdi=None, stub=False):
    rows = [["1", f"d3d9.dll!{n}", d3d_kind, "engine", "f", "1"] for n in ("Direct3DCreate9", "IDirect3D9::CreateDevice")]
    rows += [["3", f"gdi32.dll!{n}", "shim", "vguimatsurface", "f", "1"] for n in (gdi or sorted(GDI32_SHIMS))]
    rows += [["4", "kernel32.dll!Sleep", "shim", "launcher", "f", "1"]]
    if stub:
        rows.append(["5", "kernel32.dll!Foo", "stub", "launcher", "f", "1"])
    return rows


def picture(w=320, h=256):
    rows = [bytes(((x * 3 + y) & 255, (x + y * 2) & 255, (x * y) & 255)[c] for x in range(w) for c in range(3))
            for y in range(h)]
    return w, h, rows


def with_square(img, x0, y0, side=64, rgb=(0, 0, 0)):
    w, h, rows = img
    rows = [bytearray(r) for r in rows]
    for y in range(y0, y0 + side):
        rows[y][3 * x0:3 * (x0 + side)] = bytes(rgb) * side
    return w, h, [bytes(r) for r in rows]


def noisy(img, amount, every):
    w, h, rows = img
    return w, h, [bytes(min(255, v + amount) if (i // 3 + y) % every == 0 else v for i, v in enumerate(r))
                  for y, r in enumerate(rows)]


MASK = {"width": 320, "height": 256, "rects": [[0, 0, 320, 128], [0, 128, 160, 128]]}


def licence_doc(drop=None, extra="", system_row=True, second_first=False):
    libs = [("libSDL3", "3.2.0", "Zlib", "LICENSE.txt", "linked"), ("libvulkan", "1.4", "Apache-2.0", "LICENSE", "loaded"),
            ("libvulkan_kosmickrisp", "mesa 26.2", "MIT", "LICENSE", "loaded"),
            ("libdxvk_d3d9", "jm2/dxvk dd3a05b", "Zlib", "LICENSE", "loaded"), ("glslang", "15", "BSD-3-Clause", "LICENSE", "build-only")]
    rows = [f"| {n} | {v} | {l} | {f} | {rl} |" for n, v, l, f, rl in libs if n != drop]
    if system_row:
        rows.append("| macOS system libraries | 26 | Apple | - | linked |")
    ac = "| AC | Result |\n|---|---|\n| AC18 | pass |\n"
    return ("# M3\n\n| old | x |\n|---|---|\n# M4: First picture\n\n" + (ac + "\n" if second_first else "") +
            "| Library | Version | Licence | Licence file | Role |\n"
            "|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n" + ac + extra)


OTOOL = ("X:\n\t/opt/lib/libSDL3.0.dylib (compatibility version 1.0.0)\n"
         "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
         "\t/System/Library/Frameworks/Cocoa.framework/Cocoa (compatibility version 1.0.0)\n")


SIPS_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAAXNSR0IArs4c6QAAAHhlWElmTU0A"
    "KgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAA"
    "AAAAAABIAAAAAQAAAEgAAAABAAOgAQADAAAAAQABAACgAgAEAAAAAQAAAAygAwAEAAAAAQAAAAgA"
    "AAAA1QNoqgAAAAlwSFlzAAALEwAACxMBAJqcGAAAAKVJREFUGBk9jukKQWEURe+yryE385d5iiRJ"
    "kfzwDt7/ZdywO6J2uzOsM5BllcQ7YbeeiSxRS7wS5a9Y5lpWRC5qoi6a4U4bUSnC8y/kntURfdEW"
    "rUj/cWHoKRTtkeiKYWggxqInJmJgyIRHvcbETKzFXCwCsk9dMYSwzKUgDO1CG2E9xMrQS/hBQz7n"
    "ua04BXQXxwgO303VOOdvfGgfOotr0Bdx+wAVFBEx9EK3uAAAAABJRU5ErkJggg=="
)
SIPS_PIXELS = (
    "0000021401012802033c03055004006405007806068c0704a00800b40902c80a04dc0b00031e"
    "04171f082b200e3f211553221f6723267b242a8f2532a32638b72743cb2849df294d063c061a"
    "3d122e3e1c423f2b56403d6a414b7e4258924362a64474ba4582ce468fe2479a095a011d5b15"
    "315c2e455d45595e556d5f6b816081956194a962acbd63bdd164d6e565e90c7804207922347a"
    "3d487b555c7c70707d90847eac987fc9ac80e1c081fed48218e883380f960523972337984a4b"
    "99695f9a90739bb0879cd59b9dfaaf9e1cc39f3ed7a064eba18312b40326b52e3ab6574eb780"
    "62b8aa76b9d38aba029ebb27b2bc55c6bd80dabea5eebfce15d20429d3333dd46651d59665d6"
    "c679d7fa8dd829a1d959b5da8cc9dbb9dddceaf1dd1f"
)


def selftest():
    n = {"ok": 0}
    bad = []

    def expect(label, cond):
        n["ok"] += 1
        if not cond:
            bad.append(label)
            print(f"FAIL {label}")

    def result(fn, *args, **kw):
        r = Rep(quiet=True)
        fn(r, *args, **kw)
        return r.ok

    img = picture()
    for ft in range(5):
        for alpha in (False, True):
            got = read_png(write_png(img[0], img[1], img[2], alpha=alpha, filters=(ft,)))
            expect(f"png roundtrip filter {ft} alpha {alpha}", got == img)
    expect("png mixed filters", read_png(write_png(*img, filters=(0, 1, 2, 3, 4))) == img)
    good_png = write_png(*img)
    data_flip = bytearray(good_png)
    data_flip[45] ^= 1
    crc_flip = bytearray(good_png)
    crc_flip[29] ^= 1
    for label, data in (("flipped byte in chunk data with its CRC left alone", bytes(data_flip)),
                        ("flipped byte in a stored CRC (IHDR)", bytes(crc_flip)), ("not png", b"hello"), ("truncated", write_png(*img)[:-20])):
        try:
            read_png(data)
            expect(f"png {label} raises", False)
        except PngError:
            expect(f"png {label} raises", True)
    il = bytearray(write_png(*img))
    il[28] = 1
    il[29:33] = struct.pack(">I", zlib.crc32(bytes(il[12:29])))
    try:
        read_png(bytes(il))
        expect("png interlaced raises", False)
    except PngError:
        expect("png interlaced raises", True)
    expect("png_size", png_size(write_png(*img)) == (320, 256))
    sips = base64.b64decode("".join(SIPS_PNG))
    got = read_png(sips)
    expect("png: an image written by sips (Paeth and Sub rows, ancillary chunks) decodes to its source pixels",
           got[0] == 12 and got[1] == 8 and b"".join(got[2]).hex() == "".join(SIPS_PIXELS))
    raw = zlib.decompress(b"".join(b for t, b in png_chunks(sips) if t == b"IDAT"))
    expect("png: the sips fixture really contains filter type 4 rows", 4 in {raw[i * 37] for i in range(8)})

    def fix(chunks):
        return SIG + b"".join(struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b)) for t, b in chunks)

    ihdr = struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + bytes(6))
    for label, data in (("zero width", fix([(b"IHDR", struct.pack(">IIBBBBB", 0, 1, 8, 2, 0, 0, 0)), (b"IDAT", idat), (b"IEND", b"")])),
                        ("non-consecutive IDAT", fix([(b"IHDR", ihdr), (b"IDAT", idat[:5]), (b"tEXt", b"a\0b"), (b"IDAT", idat[5:]), (b"IEND", b"")])),
                        ("unknown critical chunk", fix([(b"IHDR", ihdr), (b"ABCD", b""), (b"IDAT", idat), (b"IEND", b"")])),
                        ("corrupt zlib stream", fix([(b"IHDR", ihdr), (b"IDAT", idat[:-6]), (b"IEND", b"")])),
                        ("huge chunk length", SIG + struct.pack(">I", 0x80000000) + b"IDAT")):
        try:
            read_png(data)
            expect(f"png {label} raises PngError", False)
        except PngError:
            expect(f"png {label} raises PngError", True)
    expect("png: a split IDAT and an unknown ancillary chunk are accepted",
           read_png(fix([(b"IHDR", ihdr), (b"tEXt", b"a\0b"), (b"IDAT", idat[:5]), (b"IDAT", idat[5:]), (b"IEND", b"")]))[:2] == (2, 1))
    expect("refusal text parses", RE_REFUSED.search("rt: run refused: display 1 not found (1 displays)\n") is not None and
           RE_REFUSED.search("rt: display 1 scale 2.0\n") is None)
    expect("colours counts distinct", colours([bytes([i, 0, 0]) for i in range(10)]) == 10)

    ok, _ = compare(img, img, MASK)
    expect("compare identical passes", ok)
    ok, _ = compare(with_square(img, 10, 10), img, MASK)
    expect("compare: black 64x64 square inside the mask fails", not ok)
    ok, _ = compare(with_square(img, 200, 150), img, MASK)
    expect("compare: same square outside the mask passes", ok)
    ok, _ = compare(noisy(img, 16, 5), img, MASK, mad=4.0)
    expect("compare: +16 on a fifth of the pixels is within tolerance", ok)
    ok, _ = compare(noisy(img, 17, 7), img, MASK)
    expect("compare: +17 on 1/7 of the pixels fails the 99 % rule", not ok)
    ok, _ = compare(noisy(img, 3, 1), img, MASK, mad=2.0)
    expect("compare: mean abs diff 3 fails the 2/255 limit", not ok)
    ok, _ = compare(noisy(img, 3, 1), img, MASK, mad=4.0)
    expect("compare: the mad threshold is configurable", ok)
    dark = (320, 256, [bytes(960) if y >= 128 else r for y, r in enumerate(img[2])])
    ok, d = compare(dark, (320, 256, [bytes(960) if y >= 128 else r for y, r in enumerate(img[2])]),
                    {"rects": [[0, 0, 320, 128]]})
    expect("compare: black outside the mask fails the luma rule", not ok and d["outside_luma"] < 5)
    ua = {"rects": [[10, 0, 90, 64]]}
    ok, d = compare(with_square(img, 96, 0, side=4), img, ua)
    expect("compare: a mask that starts off the 32 px grid does not raise and counts the partial cell",
           "worst_block" in d)
    strip = img
    for y0 in range(0, 32, 4):
        strip = with_square(strip, 96, y0, side=4)
    ok, d = compare(strip, img, ua)
    expect("compare: bad pixels in the last partial cell are counted by their true share", not ok and d["worst_block"] > 0.5)
    big = picture(1280, 1024)
    bigmask = {"rects": [[0, 0, 1280, 700]]}
    ok, _ = compare(big, big, bigmask)
    expect("compare 1280x1024: identical passes", ok)
    ok, d = compare(with_square(big, 100, 100), big, bigmask)
    expect("compare 1280x1024: black 64x64 square inside the mask fails (block rule)", not ok and d["worst_block"] > 0.5)
    ok, d = compare(with_square(big, 101, 103), big, bigmask)
    expect("compare 1280x1024: an unaligned square fails too", not ok)
    ok, _ = compare(with_square(big, 100, 100), big, bigmask, block=0)
    expect("compare 1280x1024: the global rules alone miss a 64x64 square", ok)
    ok, _ = compare(with_square(big, 100, 800), big, bigmask)
    expect("compare 1280x1024: the same square outside the mask passes", ok)
    ok, _ = compare(img, (160, 128, img[2][:128]), MASK)
    expect("compare: size mismatch fails", not ok)
    for spec in ({"rects": []}, {"rects": [[300, 0, 40, 10]]}, {"width": 99, "rects": [[0, 0, 1, 1]]}):
        try:
            compare(img, img, spec)
            expect(f"compare bad mask {spec} raises", False)
        except ValueError:
            expect(f"compare bad mask {spec} raises", True)

    good = fixture_log()
    expect("stages: good dxvk log passes", result(check_stages, good))
    swapped = fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 10, 11, 8])
    expect("stages: stage 11 before stage 8 fails", not result(check_stages, swapped))
    expect("stages: stage 9 after 7 fails", not result(check_stages, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 10, 7, 9, 8, 11])))
    expect("stages: missing stage 10 fails", not result(check_stages, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 11])))
    expect("stages: duplicate stage 6 fails", not result(check_stages, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 6, 9, 7, 8, 10, 11])))
    expect("stages: stage 4 after 5 fails", not result(check_stages, fixture_log(order=[1, 2, 3, 4, 5, 4, 6, 9, 7, 8, 10, 11])))
    expect("stages: zero stage 4 line fails", not result(check_stages, good.replace("mod2 = 1", "mod2 = 0")))
    nulllog = fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 7, 8]).replace("gfx dxvk", "gfx null")
    expect("stages: null log passes in null mode", result(check_stages, nulllog, "null"))
    expect("stages: null log with stage 11 last passes in null mode",
           result(check_stages, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 7, 8, 11]).replace("gfx dxvk", "gfx null"), "null"))
    expect("stages: stage 9 in null mode fails", not result(check_stages, good, "null"))
    expect("stages: old order with 10 before 7 fails", not result(check_stages, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 10, 7, 8, 11])))
    quietlog = fixture_log(quiet=True)
    expect("stages: quiet log without stage 11 passes", result(check_stages, quietlog, "dxvk", True))
    expect("stages: quiet log with stage 11 fails", not result(check_stages, fixture_log(quiet=True, order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 10, 11])))
    expect("stages: non-quiet log without stage 11 fails", not result(check_stages, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 10])))
    expect("stages: soak needs the guest-quiet token", not result(check_stages, good, "dxvk", True))
    expect("stats: quiet log passes", result(check_stats, quietlog))
    expect("stats: quiet log p99 over limit fails", not result(check_stats, fixture_log(quiet=True, p99=99.0)))
    expect("marker: non-quiet log without stage 11 fails menu", not result(check_menu_marker, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 10])))
    expect("stages: dxlevel missing from stage 1 fails", not result(check_stages, good.replace("-dxlevel 90", "")))
    dev = RE_DEVICE.search(good)
    expect("stage 9 line parses", dev and dev[1] == "AMD Radeon" and dev[2] == "1002" and dev[7] == "52")
    expect("stage 9 line parses without flags", RE_DEVICE.search(
        'rt: stage 9 device dxvk "A" vendor 0x1 1x2 windowed 0 format 22\n') is not None)

    frames = [16.6] * 96 + [40.0] * 4
    expect("percentile nearest rank", percentile(list(range(1, 101)), 99) == 99 and percentile(frames, 99) == 40.0
           and percentile([16.6] * 98 + [40.0] * 2, 99) == 40.0 and percentile([16.6] * 99 + [40.0], 99) == 16.6)
    expect("frames line from a frame list", f"p99 {percentile(frames, 99):.1f}" in frames_line(frames))
    st = lambda log, **k: result(check_stats, log, **k)
    expect("stats: good log passes", st(good))
    expect("stats: p99 33.3 passes", st(fixture_log(p99=33.3)))
    expect("stats: p99 above 33.3 fails", not st(fixture_log(p99=40.0)))
    expect("stats: frame list with 4 slow frames in 100 fails", not st(fixture_log(p99=percentile([16.6] * 96 + [40.0] * 4, 99))))
    expect("stats: p99 limit is configurable", st(fixture_log(p99=40.0), p99=41))
    expect("stats: pump 100 passes", st(fixture_log(pump=100)))
    expect("stats: pump 101 fails", not st(fixture_log(pump=101)))
    expect("stats: dxvk whole-run 1188 with steady 25 passes", st(fixture_log(pump=25, whole=1188)))
    expect("stats: dxvk steady 112 fails", not st(fixture_log(pump=112, whole=112)))
    expect("stats: dxvk without steady line fails", not st(fixture_log(pump=25, steady=False)))
    expect("stats: null whole-run 31 without steady passes",
           st(fixture_log(pump=31, steady=False).replace("gfx dxvk", "gfx null")))
    expect("stats: null whole-run 101 without steady fails",
           not st(fixture_log(pump=101, steady=False).replace("gfx dxvk", "gfx null")))
    expect("stats: null mode comes from the stage 1 line, not a later gfx dxvk mention",
           st(fixture_log(pump=31, steady=False).replace("gfx dxvk", "gfx null").replace(
               "rt: stage 10 first-present", "rt: stage 8 console\nrt: gfx dxvk")))
    expect("stats: dxvk stage 1 with a stray gfx null later still needs steady",
           not st(fixture_log(pump=25, steady=False) + "rt: gfx null\n"))
    expect("stats: dxvk without a stage 10 line fails", not st(re.sub(r"^rt: stage 10 .*\n", "", good, flags=re.M)))
    expect("stats: null without a stage 10 line passes", st(re.sub(
        r"^rt: stage 10 .*\n", "", fixture_log(pump=31, steady=False).replace("gfx dxvk", "gfx null"), flags=re.M)))
    expect("backing scales: main display first, pixels over points",
           backing_scales(json.dumps({"SPDisplaysDataType": [{"spdisplays_ndrvs": [
               {"spdisplays_main": "spdisplays_no", "_spdisplays_pixels": "3440 x 1440", "_spdisplays_resolution": "3440 x 1440 @ 120.00Hz"},
               {"spdisplays_main": "spdisplays_yes", "_spdisplays_pixels": "4112 x 2658", "_spdisplays_resolution": "2056 x 1329 @ 120.00Hz"}]}]})) == [2.0, 1.0])
    expect("stats: lost above 0 fails", not st(fixture_log(lost=7)))
    expect("stats: failed shaders fail", not st(fixture_log(failed=1)))
    expect("stats: too few frames fail", not st(good, min_frames=9000))
    expect("stats: no frames line fails", not st(re.sub(r"^rt: frames.*\n", "", good, flags=re.M)))
    expect("stats: out-of-arena above 0 fails", not st(good.replace("out-of-arena 0", "out-of-arena 2")))
    expect("faults: clean log passes", result(check_faults, good, [], 0))
    expect("faults: FAULT block fails", not result(check_faults, fixture_log(fault=True), [], 0))
    expect("faults: new .ips fails", not result(check_faults, good, ["/x/bloodlines-native.ips"], 0))
    hung = good + "rt: gfx hang: IDirect3DDevice9::Present blocked 5003 ms (load 12.30)\n"
    class Labels(Rep):
        def __init__(self):
            super().__init__(quiet=True)
            self.lines = []

        def check(self, label, good, detail=""):
            self.lines.append(label)
            super().check(label, good, detail)

    hr = Labels()
    check_faults(hr, hung, [], 0)
    hl = next((l for l in hr.lines if "gfx hang" in l), "")
    expect("faults: exit-0 log with a gfx hang stop line fails, label names method, ms and load",
           not hr.ok and "Present blocked 5003 ms" in hl and "load average 12.30" in hl and "rerun on a quiet machine" in hl)
    cr = Labels()
    check_faults(cr, good, [], 0)
    expect("faults: a clean log has no rerun hint", "rerun" not in " ".join(cr.lines))
    base = dxvk_config("d3d9.maxFrameLatency = 1; dxvk.numCompilerThreads = 4")
    expect("dxvk config: composed string is the bridge's value plus the extra",
           base == "d3d9.memoryTrackTest = True; d3d9.maxAvailableMemory = 1024; d3d9.deferSurfaceCreation = True; "
           "d3d9.maxFrameLatency = 1; dxvk.numCompilerThreads = 4")
    expect("faults: exit-0 log with the dxvk GPU-hung line fails",
           not result(check_faults, good + "Timeout occured, GPU likely hung.\n", [], 0))
    expect("faults: exit-0 log with the dxvk device-lost line fails",
           not result(check_faults, good + "err:   VK_ERROR_DEVICE_LOST\n", [], 0))
    expect("stages: one trailing stage 12 passes",
           result(check_stages, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 10, 11, 12])))
    expect("stages: stage 12 twice fails",
           not result(check_stages, fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 10, 11, 12, 12])))
    expect("faults: exit 15 fails", not result(check_faults, good, [], 15))

    dl = "Found device: Apple M1 Max (KosmicKrisp (probe)\n"
    boot = lambda log=good, rows=None, d=dl: result(check_boot, log, rows if rows is not None else fixture_rows(), d)
    expect("boot: good run passes", boot())
    expect("boot: stub row fails", not boot(rows=fixture_rows(stub=True)))
    expect("boot: d3d9 row of kind shim fails", not boot(rows=fixture_rows(d3d_kind="shim")))
    expect("boot: extra gdi32 shim fails", not boot(rows=fixture_rows(gdi=sorted(GDI32_SHIMS) + ["TextOutA"])))
    expect("boot: empty dxvk log fails", not boot(d=""))
    expect("boot: dxvk log without KosmicKrisp fails", not boot(d="Found device: llvmpipe\n"))
    expect("boot: lavapipe loaded fails", not boot(log=fixture_log(images=["/o/libSDL3.0.dylib", "/o/libvulkan.1.dylib",
           "/o/libvulkan_kosmickrisp.dylib", "/o/libvulkan_lvp.dylib", "/o/libdxvk_d3d9.0.dylib"])))
    expect("boot: two libSDL3 fail", not boot(log=fixture_log(images=["/a/libSDL3.0.dylib", "/b/libSDL3.0.dylib",
           "/o/libvulkan.1.dylib", "/o/libvulkan_kosmickrisp.dylib", "/o/libdxvk_d3d9.0.dylib"])))
    expect("boot: bitmap font failure fails", not boot(log=good + "guest: Couldn't create bitmap font\n"))
    expect("boot: no dllmain line fails", not boot(log=good.replace("rt: dllmain stdshader_dx9 = 1", "")))
    expect("boot: null-mode run with rows and no d3d9 checks passes", result(check_boot, nulllog, fixture_rows(d3d_kind="shim"), "", "null"))
    expect("marker: a log that starts at frame 1 passes at the first N >= 30", result(check_menu_marker, good) and
           "on frame 1\n" in good)
    expect("marker: frame 30 after the stage 11 line fails", not result(check_menu_marker, fixture_log(marker_after=True)))
    expect("marker: frame 29 fails", not result(check_menu_marker, fixture_log(marker=29)))
    expect("marker: missing fails", not result(check_menu_marker, good.replace("guest: main menu", "guest: other")))

    lip = lambda p: "arm64"
    libs = lambda doc=None, lipo=lip, **k: result(check_libs, "X", OTOOL, good, doc or licence_doc(),
                                                  lipo=lipo, **k)
    expect("libs: complete table passes", libs())
    for lib in ("libSDL3", "libvulkan", "libvulkan_kosmickrisp", "libdxvk_d3d9"):
        expect(f"libs: table missing {lib} fails", not libs(licence_doc(drop=lib)))
    expect("libs: a missing macOS system libraries row fails", not libs(licence_doc(system_row=False)))
    expect("libs: exactly one macOS system libraries row passes", libs(licence_doc()))
    expect("libs: two macOS system libraries rows fail", not libs(licence_doc()
           .replace("| macOS system libraries | 26 | Apple | - | linked |",
                    "| macOS system libraries | 26 | Apple | - | linked |\n| macOS system libraries | 27 | Apple | - | linked |")))
    otool_user = "X:\n\t/opt/lib/libSDL3.0.dylib (compatibility version 1.0.0)\n"
    expect("libs: no system libraries in otool -L means no system row is needed",
           result(check_libs, "X", otool_user, good, licence_doc(), lipo=lip))
    expect("libs: a Library header without a separator does not swallow a later table",
           m4_rows("# M4\n\n| Library | V |\ntext\n\n| AC | R |\n|---|---|\n| AC1 | ok |\n") == [])
    expect("libs: a second table before the licence table is ignored", libs(licence_doc(second_first=True)))
    expect("libs: rows of a second table are not read as licence rows",
           [c[0] for c in m4_rows(licence_doc(second_first=True))].count("AC18") == 0)
    expect("libs: empty version cell fails", not libs(licence_doc().replace("| 3.2.0 |", "|  |")))
    expect("libs: empty licence file cell fails", not libs(licence_doc().replace("| LICENSE.txt |", "|  |")))
    expect("libs: a log with no gfx image lines fails", not result(check_libs, "X", OTOOL, "", licence_doc(), lipo=lip))
    expect("libs: dxvk row without jm2/dxvk fails", not libs(licence_doc().replace("jm2/dxvk ", "")))
    expect("libs: no M4 section fails", not libs("# M3\n| libSDL3 | 1 | Zlib | f | linked |\n"))
    expect("libs: bad role fails", not libs(licence_doc().replace("| linked |", "| maybe |")))
    expect("libs: x86_64 image fails", not libs(lipo=lambda p: "x86_64 arm64"))
    expect("libs: wrong dxvk licence fails", not libs(licence_doc().replace("Zlib | LICENSE | loaded", "MIT | LICENSE | loaded")))
    pr = sys.executable
    plip = lambda p: "x86_64" if p == pr else "arm64"
    expect("libs: a probe that is not arm64 fails", not result(check_libs, "X", OTOOL, good, licence_doc(), lipo=plip, probe=pr))
    expect("libs: a missing probe fails", not libs(probe="/nonexistent/probe"))
    expect("libs: an arm64 probe passes", libs(probe=pr))
    expect("stage 9 vendor 0x1002 is required", not boot(log=good.replace("vendor 0x1002", "vendor 0x106B")))
    expect("stage 9 flags 0x52 are required", not boot(log=good.replace("flags 0x52", "flags 0x20")))
    expect("libs: otool parse skips the header", parse_otool(OTOOL)[0] == "/opt/lib/libSDL3.0.dylib")

    rep = Rep(quiet=True)
    check_png(rep, write_png(*img), (320, 256), 1000)
    expect("check_png: 320x256 with >= 1000 colours passes", rep.ok)
    rep = Rep(quiet=True)
    check_png(rep, write_png(*img), (1280, 1024), 1000)
    expect("check_png: wrong size fails", not rep.ok)
    rep = Rep(quiet=True)
    check_png(rep, write_png(320, 256, [bytes(960)] * 256), (320, 256), 1000)
    expect("check_png: one-colour picture fails", not rep.ok)

    import io
    from contextlib import redirect_stdout
    tmp = tempfile.mkdtemp(prefix="m4-self-")
    for name, data in (("good", write_png(*img)), ("black", write_png(320, 256, [bytes(960)] * 256))):
        open(os.path.join(tmp, name + ".png"), "wb").write(data)
    with redirect_stdout(io.StringIO()):
        good_wh = window_size({"rc": 0, "path": os.path.join(tmp, "good.png")})
        black_wh = window_size({"rc": 0, "path": os.path.join(tmp, "black.png")})
        fail_wh = window_size({"rc": 1, "err": "denied", "path": os.path.join(tmp, "good.png")})
        none_wh = window_size({})
    expect("window capture: readable picture gives its size", good_wh == (320, 256))
    expect("window capture: all-black picture is unavailable", black_wh is None)
    expect("window capture: screencapture failure is unavailable", fail_wh is None and none_wh is None)
    for f in glob.glob(os.path.join(tmp, "*.png")):
        os.remove(f)
    os.rmdir(tmp)

    print(f"selftest: {n['ok'] - len(bad)} of {n['ok']} passed")
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(prog="m4.py")
    sub = ap.add_subparsers(dest="mode", required=True)

    def add(name, fn, aliases=(), **opts):
        p = sub.add_parser(name, aliases=list(aliases))
        p.set_defaults(fn=fn)
        for k, v in opts.items():
            p.add_argument("--" + k.replace("_", "-"), **v)
        p.add_argument("--dxvk-config-extra")
        p.add_argument("--dxvk-cache")
        return p

    exe = {"default": X_DEFAULT}
    ov = {}
    tol = {"block": {"type": int, "default": DEF["block"]}, "block_frac": {"type": float, "default": DEF["block_frac"]},
           "tol": {"type": int, "default": DEF["tol"]}, "pct": {"type": float, "default": DEF["pct"]},
           "mad": {"type": float, "default": DEF["mad"]}, "luma": {"type": float, "default": DEF["luma"]}}
    size = {"type": lambda s: tuple(int(x) for x in s.split("x")), "default": DEF["size"]}
    stat = {"p99": {"type": float, "default": DEF["p99"]}, "pump": {"type": int, "default": DEF["pump"]}}
    add("selftest", lambda a: selftest())
    add("cases", cmd_cases, exe=exe, overlay=ov, timeout={"type": int, "default": 300},
        min_cases={"type": int, "default": 6})
    p = add("boot", cmd_boot, exe=exe, overlay=ov, timeout={"type": int, "default": 300}, display={"type": int})
    p.add_argument("args", nargs="*")
    menu = dict(exe=exe, overlay=ov, timeout={"type": int, "default": 300}, display={"type": int}, size=size,
                capture_after={"type": int, "default": 10}, screencap_delay={"type": float, "default": 3.0},
                ref={}, mask={}, **tol)
    p = add("menu", cmd_menu, **menu)
    p.add_argument("--no-screencap", action="store_true")
    p = add("compare", cmd_compare, mask={"required": True}, **tol)
    p.add_argument("png")
    p.add_argument("ref")
    p = add("png", cmd_png, size=size)
    p.add_argument("png")
    soak = dict(exe=exe, overlay=ov, timeout={"type": int, "default": 400}, display={"type": int},
                close_after={"type": int, "default": 130}, seconds={"type": int, "default": DEF["soak_seconds"]},
                frames_per_60s={"type": int, "default": DEF["frames_per_60s"]}, **stat)
    add("soak", cmd_soak, n={"type": int, "default": 3}, **soak)
    both = {k: v for k, v in {**menu, **soak}.items() if k != "display"}
    p = add("displays", cmd_displays, displays={"type": int, "nargs": "+", "default": [0]}, **both)
    p.add_argument("--no-screencap", action="store_true")
    add("stats", cmd_stats, log={"required": True}, min_frames={"type": int}, **stat)
    add("stages", cmd_stages, log={"required": True}, mode={"choices": ["dxvk", "null"], "default": "dxvk"})
    add("licences", cmd_licences, aliases=["libs"], exe=exe, log={"required": True}, doc={"default": DOC_DEFAULT},
        probe={"default": PROBE_DEFAULT})
    a = ap.parse_args()
    try:
        apply_dxvk_env(a)
        return a.fn(a)
    except (OSError, ValueError, PngError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
