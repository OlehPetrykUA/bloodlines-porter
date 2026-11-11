#!/usr/bin/env python3
import argparse
import contextlib
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import m4
import m5
from m4 import DEF, PngError, REFDIR, Rep, X_DEFAULT, load_mask, need_exe, read_png
from m5 import ScriptError, RE_NAME, guest_lines, parse_log

SCRIPTS = os.path.join(HERE, "m6")
CHARGEN_DEFAULT = os.path.join(REFDIR, "m6-chargen.json")
GAME_DEFAULT = m4.devpaths.GAME
MANIFEST_DEFAULT = os.path.join(HERE, "..", "docs", "game-manifest.txt")
CLIENT = m5.CLIENT
LOOK_MAX = 4096
LOAD_LIMIT = 9.0
SAMPLE_S = 5.0
STAGE_LIMIT = 60
MOVE_MIN = 8.0
DIFF_TOL = 8
DIFF_PCT = 0.01
IDLE_MAX = 2.0
SOAK_CLOSE = 190
IDLE_CLOSE = 620
OPENING_LIMIT = 600
CHARGEN_LIMIT = 120
TIMEDEMO_MIN = 100
MIN_GFX = 32 + 93 + 4
QUIET_WAIT = 120.0
QUIET_POLL = 5.0
FUNC_IGNORED = ("build_all.py",)
HAVEN = "sm_pawnshop_1"
FIRST = "sp_genesisdevice_1"
SCAN = {"w": 0x11, "s": 0x1F, "a": 0x1E, "d": 0x20, "ctrl": 0x1D, "f9": 0x43, "f12": 0x58}
LEVEL_PRINT = {"sp_genesisdevice_1": "loading demo level script", "sp_theatre": "loading theatre level script",
               "sp_tutorial_1": "loading tutorial level script", "sm_pawnshop_1": "loading santa monica level script"}
BUSY = (("bloodlines-native", ["pgrep", "-f", r"(^|/)bloodlines-native( |$)"]),
        ("build_all.py", ["pgrep", "-f", r"(^|/)build_all\.py( |$)"]),
        ("screencapture", ["pgrep", "-x", "screencapture"]))

RE_S12 = re.compile(r"^rt: stage 12 map (\S+)\s*$", re.M)
RE_READY = re.compile(r"^rt: map ready (\S+) at \+([\d.]+) s\s*$", re.M)
RE_REL = re.compile(r"^rt: input relative (on|off)\s*$")
RE_WIN = re.compile(r"^rt: window (minimised|restored)\s*$")
RE_TIMEDEMO = re.compile(r"^guest:\s*(\d+) frames\s+([\d.]+) seconds\s+([\d.]+) fps\s*$", re.M)
RE_MAPLINE = re.compile(r"^guest: Map: (\S+)", re.M)
RE_HANG = re.compile(r"gfx hang: (\S.*)")
RE_DXVK_HANG = re.compile(r"GPU likely hung|VK_ERROR_DEVICE_LOST")
RE_TB = re.compile(r"^Traceback \(most recent call last\)")
RE_PATCH = re.compile(r"^NameError: (?:global )?name '(\w+)_patch' is not defined\s*$")
RE_TEXTARG = re.compile(r'"([^"\\]|\\["\\])*"')
RE_STAGE = re.compile(r"^rt: stage (\d+)(?: (.*))?$", re.M)
RE_SHASUM_BAD = re.compile(r"^(.+): FAILED$", re.M)

RUNNER = m5.run_timed


def expand_console(text, name="script"):
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        l = raw.strip()
        if l == "console" or l.startswith("console "):
            arg = l[7:].strip()
            if not RE_TEXTARG.fullmatch(arg):
                raise ScriptError(f"{name}: line {n}: console needs one double-quoted string: {l}")
            out += ["key grave", "sleep 0.5", f"text {arg}", "key return", "sleep 0.5", "key grave"]
        else:
            out.append(raw)
    return "\n".join(out) + "\n"


def render(text, chargen=None, env=None, name="script"):
    return m5.render(expand_console(text, name), chargen or {"points": {}}, env, name)


def render_file(name, chargen=None, env=None):
    with open(os.path.join(SCRIPTS, name + ".txt")) as f:
        return render(f.read(), chargen, env, name)


def own_command(n, l):
    w = l.split()
    a = w[1:]

    def bad(msg):
        raise ScriptError(f"line {n}: {msg}: {l}")
    if w[0] == "wait":
        if len(a) not in (1, 2) or (len(a) == 2 and not RE_NAME.fullmatch(a[1])):
            bad("wait map takes an optional NAME of letters, digits, _ or -")
    elif w[0] == "look":
        if len(a) != 2 or not all(re.fullmatch(r"-?\d+", x) and abs(int(x)) <= LOOK_MAX for x in a):
            bad(f"look needs DX DY integers from -{LOOK_MAX} to {LOOK_MAX}")
    elif len(a) != 1 or not re.fullmatch(r"\d+(\.\d+)?", a[0]):
        bad("minimise needs non-negative seconds")
    return (n, w[0], l.split(None, 1)[1].strip())


def parse_script(text):
    own, masked = [], []
    for n, raw in enumerate(expand_console(text).splitlines(), 1):
        l = raw.strip()
        w = l.split()
        if w and (w[0] in ("look", "minimise") or (w[0] == "wait" and w[1:2] == ["map"])):
            own.append(own_command(n, l))
            masked.append("")
        else:
            masked.append(raw)
    return sorted(m5.parse_script("\n".join(masked)) + own, key=lambda c: c[0])


def gen_walk(seconds=SOAK_CLOSE, step=0.1, turn=30, block=20):
    lines, each = ["wait map"], int(block / 2 / step)
    keys = ["w", "s"]
    for b in range(max(1, -(-int(seconds) // block))):
        for k in keys:
            lines.append(f"keydown {k}")
            for sign in (1, -1):
                for _ in range(each // 2):
                    lines += [f"look {sign * turn} 0", f"sleep {step:g}"]
            lines.append(f"keyup {k}")
    return "\n".join(lines) + "\n"


def check_chargen(c):
    if not isinstance(c, dict) or not isinstance(c.get("points"), dict):
        raise ValueError("m6-chargen.json needs a points object")
    cw, ch = CLIENT
    for k, v in c["points"].items():
        if (not isinstance(v, list) or len(v) != 2 or not all(isinstance(i, int) for i in v) or
                not (0 <= v[0] < cw and 0 <= v[1] < ch)):
            raise ValueError(f"chargen point {k} {v} is not [x, y] inside {cw}x{ch}")
    steps = c.get("steps", [])
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise ValueError("m6-chargen.json steps must be a list of script lines")
    return c


def load_chargen(path):
    if not os.path.exists(path):
        return {"points": {}, "steps": []}
    with open(path) as f:
        return check_chargen(json.load(f))


def gen_chargen(menu, chargen):
    pts = {**menu.get("points", {}), **chargen["points"]}
    body = "\n".join(chargen.get("steps", []))
    text = ("wait menu\nsleep 3\nmove @newgame\nsleep 0.5\nclick left\nwait map " + FIRST + "\nsleep 3\n" + body +
            "\nwait map sp_theatre\nsleep 5\ncapture sp_theatre-arrival\n")
    return render(text, {"points": pts}, name="chargen")


def find(lines, rx, start=0):
    for i in range(start, len(lines)):
        if rx.search(lines[i]):
            return i
    return None


def in_order(log, pats):
    pos = 0
    for p in pats:
        m = re.compile(p, re.M).search(log, pos)
        if not m:
            return p
        pos = m.end()
    return None


def lost_values(log):
    return [int(x) for x in m4.RE_LOST.findall(log)]


def gfx_trouble(log):
    return bool(RE_HANG.search(log) or RE_DXVK_HANG.search(log) or any(lost_values(log)))


def tracebacks(log):
    g, out = guest_lines(log), []
    for i, l in enumerate(g):
        if RE_TB.match(l):
            j = i + 1
            while j < len(g) and g[j][:1] in (" ", "\t"):
                j += 1
            out.append(g[j] if j < len(g) else "")
    return out


def check_traceback(r, log):
    maps = set(RE_MAPLINE.findall(log))
    bad = []
    for t in tracebacks(log):
        m = RE_PATCH.match(t)
        if not (m and m[1] in maps):
            bad.append(t)
    r.check("no Python Traceback other than the <map>_patch NameError", not bad, bad[:2])


def check_hang(r, log):
    h = RE_HANG.findall(log)
    d = RE_DXVK_HANG.findall(log)
    r.check("no gfx hang stop line and no dxvk GPU-hung line (rerun on a quiet machine if the load average was 9 or more)",
            not h and not d, (h[:1], d[:1]))


def check_lost(r, log):
    v = lost_values(log)
    r.check(f"rt: gfx lost 0 (got {v[-1] if v else '?'})", bool(v) and v[-1] == 0, v)


def check_shaders(r, log):
    sh = m4.RE_SHADERS.findall(log)
    r.check(f"rt: shaders failed 0 (got {sh[-1][5] if sh else '?'})", bool(sh) and int(sh[-1][5]) == 0, sh)


def check_stubs(r, rows):
    stubs = sorted(x[1] for x in rows if len(x) > 2 and x[2] == "stub")
    r.check("no first-call row of kind stub", not stubs, stubs[:3])


def rules(r, res, timeout, cmds=None, need_done=True, min_lines=1):
    log = res.log
    m4.timeout_note(r, res.rc, log, timeout)
    m4.check_faults(r, log, res.crashes, res.rc)
    check_hang(r, log)
    check_lost(r, log)
    check_traceback(r, log)
    check_shaders(r, log)
    check_stubs(r, res.rows)
    m5.check_dinput(r, res.rows)
    r.check("no message queue full line", not m5.RE_QUEUE_FULL.search(log))
    if cmds is not None:
        m5.check_script(r, log, cmds, need_done, min_lines)


def stages_of(log):
    return [(int(m[1]), m[2] or "") for m in RE_STAGE.finditer(log)]


def map_stages(r, log):
    order = [n for n, _ in stages_of(log) if n != 4]
    base = [n for n in order if n != 12]
    want = m4.DXVK_ORDER[:-1]
    r.check("stages 1,2,3,5,6,9,7,8,10 once each and in order, 11 only after them", base in (want, want + [11]), order)
    n12 = order.count(12)
    r.check("stage 12 once and last", n12 == 1 and order[-1] == 12, order)
    s1 = next((l for n, l in stages_of(log) if n == 1), "")
    r.check("stage 1 line names gfx dxvk and -dxlevel 90", re.search(r"\bgfx dxvk\b", s1) and "-dxlevel 90" in s1, s1)


def check_stage12(r, res, name=None, limit=STAGE_LIMIT):
    t8 = m5.line_time(res, lambda l: l.startswith("rt: stage 8"))
    t12 = m5.line_time(res, lambda l: RE_S12.match(l.rstrip("\n")))
    got = RE_S12.findall(res.log)
    r.check("rt: stage 12 map line present", bool(got), got)
    if name is not None:
        r.check(f"stage 12 names {name} (got {got[:1]})", got[:1] == [name], got)
    ok = t8 is not None and t12 is not None and 0 <= t12 - t8 <= limit
    r.check(f"stage 12 within {limit} s of stage 8 ({'?' if not ok and (t8 is None or t12 is None) else format(t12 - t8, '.1f')} s)",
            ok, (t8, t12))


def check_ready_names(r, log, names=None):
    got = [m[1] for m in RE_READY.finditer(log)]
    r.check(f"every rt: map ready line names a map, not ? ({got})", bool(got) and "?" not in got, got)
    if names is not None:
        r.check(f"rt: map ready lines are {names}", got[:len(names)] == names, got)


def ready_times(log):
    return {m[1]: float(m[2]) for m in RE_READY.finditer(log)}


def check_a2(r, log, name):
    miss = in_order(log, [r"^guest: " + re.escape(LEVEL_PRINT.get(name, "")), r"^guest: Map: " + re.escape(name) + r"\b",
                          r"^guest: R_NewMap", r"^rt: stage 12 map " + re.escape(name) + r"\s*$"])
    r.check("log has level-script print, Map:, R_NewMap, stage 12 in that order" + (f" (missing {miss})" if miss else ""), miss is None)


def in_map_seconds(res):
    t12 = m5.line_time(res, lambda l: RE_S12.match(l.rstrip("\n")))
    return None if t12 is None else res.exit - t12


RE_ALLOC = re.compile(r"^rt: allocator high-water 0x[0-9A-Fa-f]+ bytes, (\d+) failed allocations\s*$", re.M)


def check_allocator(r, log):
    got = RE_ALLOC.findall(log)
    if got:
        print(f"  rt: allocator failed allocations {got[-1]}")
    r.check(f"rt: allocator high-water line reports 0 failed allocations (got {got[-1] if got else 'no line'})",
            bool(got) and int(got[-1]) == 0, got)


def check_map_stats(r, a, res):
    dur = in_map_seconds(res)
    r.check(f"in-map span {'?' if dur is None else format(dur, '.1f')} s (need >= 60)", dur is not None and dur >= 60, dur)
    m4.check_stats(r, res.log, a.p99, a.pump, int(DEF["frames_per_60s"] * (dur or 0) / 60))
    f = m4.RE_FRAMES.findall(res.log)
    if f:
        print(f"  in-map frames n {f[-1][0]} p50 {f[-1][1]} p95 {f[-1][2]} p99 {f[-1][3]} max {f[-1][4]}")


def events(log):
    out = []
    for i, l in enumerate(log.splitlines()):
        m = RE_REL.match(l)
        if m:
            out.append(("rel", i, m[1]))
        m = RE_WIN.match(l)
        if m:
            out.append(("win", i, m[1]))
    return out


def scripts_of(log):
    return [e for e in parse_log(log) if e[0] == "script"]


def keys_of(log, msg, scan):
    return [e for e in parse_log(log) if e[0] == "input" and e[2] == msg and m5.scan_of(e[4]) == scan]


def check_scans(r, log, pairs):
    for name in pairs:
        scan = SCAN[name]
        r.check(f"WM_KEYDOWN and WM_KEYUP with scan {scan:#04x} ({name})",
                bool(keys_of(log, "WM_KEYDOWN", scan)) and bool(keys_of(log, "WM_KEYUP", scan)))


def check_relative(r, log):
    ev = events(log)
    look = next((e[1] for e in scripts_of(log) if e[3].startswith("look ")), None)
    on = next((e[1] for e in ev if e[0] == "rel" and e[2] == "on"), None)
    r.check("log has a look script line and rt: input relative on", look is not None and on is not None, (look, on))
    if look is None or on is None:
        return
    r.check("rt: input relative on comes before the first look line", on < look, (on, look))
    warps = [e for e in parse_log(log) if e[0] == "cursor" and e[2] == "warp" and e[1] > on]
    r.check("no rt: cursor warp line follows rt: input relative on", not warps, [w[3] for w in warps[:2]])


def check_focus6(r, log):
    ev = events(log)
    sc = scripts_of(log)
    lost = next((e[1] for e in sc if e[3] == "focus lost"), None)
    r.check("log has the focus lost script line", lost is not None)
    if lost is None:
        return
    inp = [e for e in parse_log(log) if e[0] == "input"]
    a0 = next((e[1] for e in inp if e[1] > lost and e[2] == "WM_ACTIVATEAPP" and e[3] == 0), None)
    r.check("WM_ACTIVATEAPP wp 0x0 follows the focus lost line", a0 is not None)
    ku = [e[1] for e in keys_of(log, "WM_KEYUP", SCAN["w"])]
    r.check("WM_KEYUP for scan 0x11 comes before WM_ACTIVATEAPP wp 0x0", a0 is not None and any(lost < i < a0 for i in ku), (ku, a0))
    off = [e[1] for e in ev if e[0] == "rel" and e[2] == "off" and e[1] > lost]
    r.check("rt: input relative off follows focus loss", bool(off), off)


def check_focus_load(r, log):
    ku = [e[1] for e in keys_of(log, "WM_KEYUP", SCAN["w"])]
    kd = [e[1] for e in keys_of(log, "WM_KEYDOWN", SCAN["w"])]
    r.check("WM_KEYUP for scan 0x11 present", bool(ku))
    r.check("no WM_KEYDOWN for scan 0x11 after the WM_KEYUP", bool(ku) and not [i for i in kd if i > ku[0]], kd)


def check_chords6(r, log, want=2):
    sc = scripts_of(log)
    ch = [k for k, e in enumerate(sc) if e[3].split()[0] == "chord"]
    r.check(f"{want} chord script lines", len(ch) == want, len(ch))
    if ch:
        lo = sc[ch[0]][1]
        hi = next((e[1] for e in sc[ch[-1] + 1:] if e[3].split()[0] != "sleep"), 1 << 60)
        leaks = [e[2:] for e in parse_log(log) if e[0] == "input" and lo < e[1] < hi and e[2] in m5.KEYMSG]
        r.check("no rt: input key or character line between the chord script lines", not leaks, leaks[:3])
    c = m5.counters(log)
    r.check(f"exit counters line has chords {want}", c is not None and c[5] == want, c)


def check_minimise(r, log, a=None):
    ev = events(log)
    mn = next((e[1] for e in ev if e[0] == "win" and e[2] == "minimised"), None)
    rs = next((e[1] for e in ev if e[0] == "win" and e[2] == "restored"), None)
    cap = next((e[1] for e in scripts_of(log) if e[3] == "capture after"), None)
    r.check("rt: window minimised, then restored, then the capture after line",
            mn is not None and rs is not None and cap is not None and mn < rs < cap, (mn, rs, cap))


def check_timedemo(r, log):
    t = RE_TIMEDEMO.findall(log)
    r.check(f"guest prints <n> frames <s> seconds <f> fps with n >= {TIMEDEMO_MIN} ({t[-1] if t else 'none'})",
            bool(t) and int(t[-1][0]) >= TIMEDEMO_MIN, t)


def world_spec(r, a):
    p = a.mask or os.path.join(a.refdir, "m6-world-mask.json")
    r.check("world mask found (--mask or m6-world-mask.json in the reference folder)", os.path.exists(p), p)
    return json.load(open(p)) if os.path.exists(p) else None


def check_idle(r, label, a, b, rows, limit):
    d = m5.mean_abs(a, b, rows)
    r.check(f"{label}: MAD {d:.2f}/255 <= idle limit {limit:g}", d <= limit, d)
    return d


def check_triple(r, label, base, moved, back, rows, lo=MOVE_MIN):
    d1, d2 = m5.mean_abs(base, moved, rows), m5.mean_abs(base, back, rows)
    r.check(f"{label}: moved MAD {d1:.2f}/255 >= {lo:g}", d1 >= lo, d1)
    r.check(f"{label}: back MAD {d2:.2f}/255 < half of moved ({d1 * 0.5:.2f})", d2 < d1 * 0.5, (d1, d2))
    return d1, d2


def same_world(r, a, label, x, y, spec):
    ok, d = m4.compare(x, y, spec, a.tol, a.pct, a.mad, a.luma, a.block, a.block_frac)
    r.check(f"{label} compare {d}", ok, d)


def differs8(r, a, img, label, versus):
    png, mask = m5.refpair(a, versus)
    if not (os.path.exists(png) and os.path.exists(mask)):
        print(f"  no reference {png}: {label} difference check not run")
        return
    differs_core(r, img, read_png(open(png, "rb").read()), json.load(open(mask)), label, versus)


def differs_core(r, x, y, mask, label, versus):
    ok, d = m4.compare(x, y, mask, DIFF_TOL)
    r.check(f"{label} compares with {versus}", "error" not in d, d)
    if "error" not in d:
        f = 1 - d["within_tol"]
        r.check(f"{label} differs from {versus} in {f * 100:.2f}% of masked pixels by more than {DIFF_TOL} (need >= {DIFF_PCT * 100:g}%)",
                f >= DIFF_PCT, f)


def load1():
    return os.getloadavg()[0]


def run_text(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def pids(text):
    return [int(w) for w in text.split() if w.isdigit()]


def ancestors(ps=run_text):
    out, p = {os.getpid()}, os.getpid()
    for _ in range(32):
        p = (pids(ps(["ps", "-o", "ppid=", "-p", str(p)])) or [0])[0]
        if p <= 1:
            break
        out.add(p)
    return out


def busy(run=run_text, me=None):
    me = ancestors() if me is None else me
    return [f"{name} (pid {p})" for name, cmd in BUSY for p in pids(run(cmd)) if p not in me]


def gate_reasons(load=None, run=run_text, me=None, functional=False):
    load = load1() if load is None else load
    out = [f"1-minute load average {load:.2f} is {LOAD_LIMIT:g} or more"] if load >= LOAD_LIMIT and not functional else []
    return out + [f"another process is running: {b}" for b in busy(run, me) if not (functional and b.startswith(FUNC_IGNORED))]


def build_notes(run=run_text, me=None):
    return [b for b in busy(run, me) if b.startswith(FUNC_IGNORED)]


def game_files(game):
    out = set()
    for d, _, fs in os.walk(game):
        for f in fs:
            if f != ".DS_Store":
                out.add(os.path.relpath(os.path.join(d, f), game).replace(os.sep, "/"))
    return out


def manifest_names(manifest):
    out = set()
    for l in open(manifest, errors="replace"):
        l = l.rstrip("\n")
        if l.strip():
            out.add(l.split(None, 1)[1].lstrip("*").strip())
    return out


def shasum_check(game, manifest):
    return subprocess.run(["shasum", "-a", "256", "-c", os.path.abspath(manifest)], cwd=game, capture_output=True,
                          text=True, errors="replace").stdout


def check_game(game, manifest, shasum=shasum_check):
    have, want = game_files(game), manifest_names(manifest)
    extra, gone = sorted(have - want), sorted(want - have)
    changed = sorted(set(RE_SHASUM_BAD.findall(shasum(game, manifest))) - set(gone))
    return extra, changed, gone


SESSION = {}


def session_ok(game, manifest, shasum=shasum_check):
    key = (os.path.abspath(game), os.path.abspath(manifest))
    if key not in SESSION:
        SESSION[key] = check_game(game, manifest, shasum)
        extra, changed, gone = SESSION[key]
        for label, names in (("extra", extra), ("changed", changed), ("missing", gone)):
            for n in names[:20]:
                print(f"REFUSED {label} file in the game folder: {n}")
            if len(names) > 20:
                print(f"REFUSED ... and {len(names) - 20} more {label} files")
    return not any(SESSION[key])


def start(a, gated=False, functional=False):
    if not need_exe(a):
        return 1
    if not session_ok(a.game, a.manifest):
        print("REFUSED: the game folder is not vanilla; acceptance runs need the manifest's exact set of files")
        return 1
    if gated:
        why = gate_reasons(functional=functional) if functional else gate_reasons()
        if why:
            for w in why:
                print(f"REFUSED: {w}")
            return 1
    return 0


class Sampler:
    def __init__(self, read=load1, period=SAMPLE_S):
        self.read, self.period, self.samples = read, period, []
        self.ev = threading.Event()
        self.th = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.ev.wait(self.period):
            self.samples.append((time.monotonic(), self.read()))

    def start(self):
        self.samples = [(time.monotonic(), self.read())]
        self.th.start()

    def stop(self):
        self.ev.set()
        self.th.join(5)
        self.samples.append((time.monotonic(), self.read()))
        return self.samples


def run_with_rerun(body, mk=Sampler, limit=LOAD_LIMIT):
    passes = []
    for attempt in (1, 2):
        r, logs = Rep(), []
        s = mk()
        s.start()
        try:
            body(r, logs)
        finally:
            samples = s.stop()
        passes.append((r, samples, logs))
        again = attempt == 1 and any(gfx_trouble(l) and any(v >= limit for t, v in samples if t0 <= t <= t1)
                                     for l, t0, t1 in logs)
        if again:
            print(f"-- hang stop or rt: gfx lost above 0 with a load sample of {limit:g} or more during the run "
                  f"{[round(v, 2) for t, v in samples]}: running once more")
        if not again:
            break
    return passes


def finish(passes):
    for i, (r, samples, _) in enumerate(passes, 1):
        print(f"== run {i} of {len(passes)}: {'PASS' if r.ok else 'FAIL'}; load samples {[round(v, 2) for t, v in samples]}"
              + ("" if r.ok else f"; failed {r.failed[:5]}"))
    return 0 if passes[-1][0].ok else 1


def prepare(o, name, text):
    text = expand_console(text)
    cmds = parse_script(text)
    os.makedirs(o, exist_ok=True)
    path = os.path.join(o, name + ".txt")
    with open(path, "w") as f:
        f.write(text)
    return path, cmds


def extra_args(a, flags, close_after, script=None, tail=()):
    extra = [*flags, "--close-after", str(close_after)]
    if script:
        extra += ["--input-script", script]
    if getattr(a, "display", None) is not None:
        extra += ["--display", str(a.display)]
    if tail:
        extra += ["--", *tail]
    return extra


def go(r, logs, a, o, name, text, tail=(), close_after=None, flags=(), need_done=True):
    path, cmds = prepare(o, name, text)
    ca = close_after or a.close_after or m5.close_after_for(a, cmds)
    t0 = time.monotonic()
    res = RUNNER(a.exe, o, extra_args(a, flags, ca, path, tail), a.timeout or ca + 120)
    logs.append((res.log, t0, time.monotonic()))
    rules(r, res, a.timeout or ca + 120, cmds, need_done)
    return res


def go_plain(r, logs, a, o, tail=(), close_after=None, flags=()):
    ca = close_after or a.close_after or SOAK_CLOSE
    to = a.timeout or ca + 120
    t0 = time.monotonic()
    res = RUNNER(a.exe, o, extra_args(a, flags, ca, None, tail), to)
    logs.append((res.log, t0, time.monotonic()))
    rules(r, res, to)
    return res


def fresh(a, tag):
    if a.overlay:
        os.makedirs(a.overlay, exist_ok=True)
        return tempfile.mkdtemp(prefix=f"{tag}-", dir=a.overlay)
    return tempfile.mkdtemp(prefix=f"m6-{tag}-")


def maprun(r, logs, a, tag, template, mapname, env=None, o=None, tail=(), **kw):
    o = o or fresh(a, tag)
    text = render_file(template, load_chargen(a.chargen), {"map": mapname, **(env or {})})
    res = go(r, logs, a, o, tag, text, tail=("+developer", "4", "+map", mapname, *tail), **kw)
    map_stages(r, res.log)
    check_stage12(r, res, mapname)
    return res, o


def world_pair(r, a, o, names):
    imgs = [m5.screen(r, a, o, n) for n in names]
    if not all(imgs):
        return None, None
    spec = world_spec(r, a)
    return (imgs, load_mask(spec, imgs[0][0], imgs[0][1])) if spec else (None, None)


def cmd_map(a):
    if start(a):
        return 1

    def body(r, logs):
        res, o = maprun(r, logs, a, "map", "genesis", a.map, close_after=a.close_after or 90,
                        flags=("--guest-quiet",) if a.guest_quiet else ())
        if a.map in LEVEL_PRINT:
            check_a2(r, res.log, a.map)
        check_ready_names(r, res.log, [a.map])
        ref = "m6-genesis" if a.map == FIRST else f"m6-{a.map}"
        img = m5.screen(r, a, o, a.map, ref)
        if img and a.map == FIRST:
            differs8(r, a, img, "genesis capture", "m4-menu")
        if a.guest_quiet:
            check_map_stats(r, a, res)
            check_allocator(r, res.log)

    return finish(run_with_rerun(body))


def quiet_start(r, label, functional=False):
    gate = (lambda: gate_reasons(functional=True)) if functional else gate_reasons
    why = gate()
    t0 = time.monotonic()
    while why and time.monotonic() - t0 < QUIET_WAIT:
        print(f"-- {label}: waiting for a quiet machine: {'; '.join(why)}", flush=True)
        time.sleep(QUIET_POLL)
        why = gate()
    r.check(f"quiet machine at {label} start", not why, why)
    note = f"; builds running (logged, not blocking): {', '.join(build_notes())}" if functional and build_notes() else ""
    print(f"-- {label}; load average at start {load1():.2f}" +
          ("; no other bloodlines-native, build or screencapture process" if not why and not functional else
           "; no other bloodlines-native or screencapture process" if not why else "") + note)
    return not why


def cmd_soak(a):
    if start(a, gated=True, functional=True):
        return 1

    def body(r, logs):
        for i in range(a.n):
            if not quiet_start(r, f"run {i + 1}", functional=True):
                continue
            res = go_plain(r, logs, a, fresh(a, "soak"), tail=("+map", a.map), flags=("--guest-quiet",))
            map_stages(r, res.log)
            check_stage12(r, res, a.map)
            check_map_stats(r, a, res)

    return finish(run_with_rerun(body))


def cmd_walk(a):
    if start(a):
        return 1

    def body(r, logs):
        res, o = maprun(r, logs, a, "walk", "walk", a.map)
        check_scans(r, res.log, ["w", "s"])
        imgs, rows = world_pair(r, a, o, ["w0", "w0b", "w1", "w2"])
        if imgs:
            check_idle(r, "w0 vs w0b", imgs[0], imgs[1], rows, a.idle)
            check_triple(r, "walk", imgs[0], imgs[2], imgs[3], rows)

    return finish(run_with_rerun(body))


def cmd_look(a):
    if start(a):
        return 1

    def body(r, logs):
        res, o = maprun(r, logs, a, "look", "look", a.map)
        check_relative(r, res.log)
        imgs, rows = world_pair(r, a, o, ["l0", "l1", "l2"])
        if imgs:
            check_triple(r, "look", *imgs, rows)

    return finish(run_with_rerun(body))


def cmd_keys(a):
    if start(a):
        return 1

    def body(r, logs):
        res, o = maprun(r, logs, a, "keys", "keys", a.map)
        check_scans(r, res.log, ["a", "d", "ctrl"])
        imgs, rows = world_pair(r, a, o, ["k0", "ka1", "ka2", "kd1", "kd2", "kc0", "kc1", "kc2", "e0", "e1", "e2"])
        if imgs:
            k0, ka1, ka2, kd1, kd2, kc0, kc1, kc2, e0, e1, e2 = imgs
            check_triple(r, "A strafe", k0, ka1, ka2, rows)
            check_triple(r, "D strafe", ka2, kd1, kd2, rows)
            check_triple(r, "Ctrl crouch", kc0, kc1, kc2, rows)
            m5.screen(r, a, o, "e1", "m6-pause")
            check_idle(r, "second Escape returns to the pre-Escape view", e0, e2, rows, a.idle)

    return finish(run_with_rerun(body))


def cmd_focus(a):
    if start(a):
        return 1

    def body(r, logs):
        tmpl, shots = ("focus-load", ["fl1", "fl2"]) if a.during_load else ("focus", ["f1", "f2"])
        res, o = maprun(r, logs, a, "focus", tmpl, a.map)
        if a.during_load:
            check_focus_load(r, res.log)
        else:
            check_focus6(r, res.log)
        imgs, rows = world_pair(r, a, o, shots)
        if imgs:
            check_idle(r, f"{shots[0]} vs {shots[1]}", imgs[0], imgs[1], rows, a.idle)

    return finish(run_with_rerun(body))


def cmd_chords(a):
    if start(a):
        return 1

    def body(r, logs):
        res, _ = maprun(r, logs, a, "chords", "chords", a.map)
        check_chords6(r, res.log)

    return finish(run_with_rerun(body))


REPLAY_PLAY = "wait map\nsleep 10\ncapture rec-end\n"


def cmd_replay(a):
    if start(a):
        return 1

    def body(r, logs):
        if not a.demo:
            caps = []
            for i in (1, 2):
                res, o = maprun(r, logs, a, f"replay{i}", "walk", a.map)
                caps.append(m5.screen(r, a, o, "w2"))
            spec = world_spec(r, a)
            if all(caps) and spec:
                same_world(r, a, "w2 of run 1 and run 2", caps[0], caps[1], spec)
            return
        res, o = maprun(r, logs, a, "record", "record", a.map)
        o2 = fresh(a, "play")
        if os.path.isdir(os.path.join(o, "files")):
            shutil.copytree(os.path.join(o, "files"), os.path.join(o2, "files"))
        res2 = go(r, logs, a, o2, "play", render(REPLAY_PLAY, load_chargen(a.chargen)), tail=("+developer", "4", "+timedemo", "m6walk"))
        check_timedemo(r, res2.log)
        i1, i2 = m5.screen(r, a, o, "rec-end"), m5.screen(r, a, o2, "rec-end")
        spec = world_spec(r, a)
        if i1 and i2 and spec:
            same_world(r, a, "last captures of record and play", i1, i2, spec)

    return finish(run_with_rerun(body))


def cmd_perf(a):
    if start(a, gated=True):
        return 1
    text = gen_walk(a.close_after or SOAK_CLOSE)

    def body(r, logs):
        for i in range(a.n):
            if not quiet_start(r, f"run {i + 1}"):
                continue
            res = go(r, logs, a, fresh(a, "perf"), "perf", text, tail=("+map", a.map), close_after=a.close_after or SOAK_CLOSE,
                     flags=("--guest-quiet",), need_done=False)
            map_stages(r, res.log)
            check_stage12(r, res, a.map)
            check_map_stats(r, a, res)
        side = Rep(quiet=True)
        if not quiet_start(side, "logging-cost run (+developer 4 +mm_verbose 1 -condebug, reported, never gated)"):
            print("-- logging-cost run not run: machine stayed busy")
            return
        res = go(side, [], a, fresh(a, "perfv"), "perf", text, tail=("+developer", "4", "+mm_verbose", "1", "-condebug", "+map", a.map),
                 close_after=a.close_after or SOAK_CLOSE, need_done=False)
        f = m4.RE_FRAMES.findall(res.log)
        print(f"  logging-cost run frames {f[-1] if f else 'none'}")

    return finish(run_with_rerun(body))


def cmd_chargen(a):
    if start(a):
        return 1
    menu = m5.load_menu(a.menu)
    ch = load_chargen(a.chargen)
    if not ch["points"] and not ch["steps"]:
        raise ValueError(f"{a.chargen} not found: measure the character-creation screens first (Q4)")

    def body(r, logs):
        o = fresh(a, "chargen")
        res = go(r, logs, a, o, "chargen", gen_chargen(menu, ch), tail=("+developer", "4"),
                 close_after=a.close_after or CHARGEN_LIMIT + 240)
        check_ready_names(r, res.log, [FIRST, "sp_theatre"])
        miss = in_order(res.log, [r"^rt: map ready " + FIRST, r"^guest: loading theatre level script", r"^rt: map ready sp_theatre\b"])
        r.check("map ready genesis, theatre script print, map ready sp_theatre in that order" + (f" (missing {miss})" if miss else ""),
                miss is None)
        m5.screen(r, a, o, "sp_theatre-arrival", "m6-sp_theatre-arrival")
        t0 = m5.line_time(res, lambda l: (m5.RE_SCRIPT.match(l.rstrip("\n")) or [None, None, ""])[2] == "wait map sp_theatre")
        t1 = m5.line_time(res, lambda l: l.startswith("rt: map ready sp_theatre "))
        r.check(f"rt: map ready sp_theatre within {CHARGEN_LIMIT} s of the last chargen step",
                t0 is not None and t1 is not None and 0 <= t1 - t0 <= CHARGEN_LIMIT, (t0, t1))

    return finish(run_with_rerun(body))


def cmd_opening(a):
    if start(a):
        return 1
    theatre = a.start == "sp_theatre"

    def body(r, logs):
        tmpl = "opening-theatre" if theatre else "opening-tutorial"
        res, o = maprun(r, logs, a, "opening", tmpl, a.start, close_after=a.close_after or OPENING_LIMIT + 120, need_done=True)
        t = ready_times(res.log)
        if theatre:
            miss = in_order(res.log, [r"^guest: loading tutorial level script", r"^rt: map ready sp_tutorial_1\b"])
            r.check("tutorial script print, then map ready sp_tutorial_1" + (f" (missing {miss})" if miss else ""), miss is None)
            gap = t.get("sp_tutorial_1", 1e9) - t.get("sp_theatre", 0)
            r.check(f"sp_tutorial_1 ready within {OPENING_LIMIT} s of sp_theatre ready ({gap:.0f} s)", 0 <= gap <= OPENING_LIMIT, gap)
            m5.screen(r, a, o, "tutorial-arrival", "m6-sp_tutorial_1-arrival")
        else:
            miss = in_order(res.log, [r"^guest: loading santa monica level script", r"^rt: map ready " + HAVEN + r"\b"])
            r.check("santa monica script print, then map ready sm_pawnshop_1" + (f" (missing {miss})" if miss else ""), miss is None)
            m5.screen(r, a, o, f"{HAVEN}-arrival", f"m6-{HAVEN}-arrival")
        check_ready_names(r, res.log)

    return finish(run_with_rerun(body))


def cmd_dialogue(a):
    if start(a):
        return 1

    def body(r, logs):
        res, o = maprun(r, logs, a, "dialogue", "dialogue", "sp_tutorial_1")
        r.check("log has loading tutorial level script", "guest: loading tutorial level script" in res.log)
        i1 = m5.screen(r, a, o, "dialogue-1", "m6-dialogue-1")
        i2 = m5.screen(r, a, o, "dialogue-2", "m6-dialogue-2")
        if i1 and i2:
            mask = m5.refpair(a, "m6-dialogue-1")[1]
            r.check(f"mask {mask} exists", os.path.exists(mask))
            if os.path.exists(mask):
                differs_core(r, i2, i1, json.load(open(mask)), "dialogue-2", "dialogue-1")

    return finish(run_with_rerun(body))


LOAD_SCRIPT = "wait map\nsleep 3\ncapture s1\n"


def save_files(o):
    return glob.glob(os.path.join(o, "files", "Vampire", "SAVE", "**", "*m6a*"), recursive=True)


def cmd_save(a):
    if start(a):
        return 1

    def body(r, logs):
        o = fresh(a, "save")
        if a.quick:
            res, _ = maprun(r, logs, a, "quick", "quick", a.map, o=o)
            check_scans(r, res.log, ["f9", "f12"])
            q0, q1 = m5.screen(r, a, o, "q0"), m5.screen(r, a, o, "q1")
            spec = world_spec(r, a)
            if q0 and q1 and spec:
                same_world(r, a, "q1 and q0", q1, q0, spec)
            return
        res, _ = maprun(r, logs, a, "save", "save", a.map, o=o)
        r.check("a new file with m6a in its name exists under files/Vampire/SAVE/", bool(save_files(o)), save_files(o))
        bad = [x for x in ("Unable to open save game file", "ID_SaveFailed") if x in res.log]
        r.check("log has no save failure line", not bad, bad)
        res2 = go(r, logs, a, o, "load", render(LOAD_SCRIPT, load_chargen(a.chargen)), tail=("+developer", "4", "+load", "m6a"))
        check_ready_names(r, res2.log, [a.map])
        s0, s1 = m5.screen(r, a, o, "s0"), m5.screen(r, a, o, "s1")
        spec = world_spec(r, a)
        if s0 and s1 and spec:
            same_world(r, a, "s1 and s0", s1, s0, spec)
        r.check("log has no Save file can't be found line", "can't be found" not in res2.log)

    return finish(run_with_rerun(body))


def cmd_minimise(a):
    if start(a):
        return 1

    def body(r, logs):
        res, o = maprun(r, logs, a, "minimise", "minimise", a.map)
        check_minimise(r, res.log)
        m5.screen(r, a, o, "after")

    return finish(run_with_rerun(body))


def symlink_tree(src, dst, skip):
    for d, _, fs in os.walk(src):
        rel = os.path.relpath(d, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in fs:
            p = os.path.normpath(os.path.join(rel, f)).replace(os.sep, "/")
            if p != skip:
                os.symlink(os.path.join(d, f), os.path.join(dst, rel, f))


def cmd_missing(a):
    if start(a):
        return 1

    def body(r, logs):
        tree = tempfile.mkdtemp(prefix="m6-missing-")
        try:
            symlink_tree(a.game, tree, "Vampire/maps/sp_tutorial_1.bsp")
            o = fresh(a, "missing")
            ca = a.close_after or 60
            t0 = time.monotonic()
            res = RUNNER(a.exe, o, ["--game", tree, *extra_args(a, [], ca, None, ("+developer", "4", "+map", "sp_tutorial_1"))],
                         a.timeout or ca + 120)
            logs.append((res.log, t0, time.monotonic()))
            rules(r, res, a.timeout or ca + 120)
            r.check("log has map load failed: sp_tutorial_1 not found or invalid",
                    re.search(r"map load failed: sp_tutorial_1 not found or invalid", res.log) is not None)
        finally:
            for d, _, fs in os.walk(tree):
                for f in fs:
                    os.unlink(os.path.join(d, f))
            for d, ds, _ in os.walk(tree, topdown=False):
                os.rmdir(d)

    return finish(run_with_rerun(body))


def check_union(r, rows):
    stubs = sorted({x[1] for x in rows if len(x) > 2 and x[2] == "stub"})
    r.check(f"union of first-calls TSVs has 0 stub rows ({len(rows)} rows)", not stubs, stubs[:5])
    sp = [x for x in rows if len(x) > 2 and x[1] == "d3d9.dll!IDirect3DTexture9::SetPriority"]
    r.check("d3d9.dll!IDirect3DTexture9::SetPriority is a bridge row", bool(sp) and all(x[2] == "bridge" for x in sp), sp[:2])


def cmd_cases(a):
    if not need_exe(a):
        return 1
    r, o = Rep(), a.overlay or tempfile.mkdtemp(prefix="m6-cases-")
    for flag, extra, floor in (("--gfx-selftest", ["--overlay", o], a.min_gfx), ("--input-selftest", [], a.min_input)):
        try:
            p = subprocess.run([a.exe, flag, *extra], capture_output=True, text=True, timeout=a.timeout or 300)
        except subprocess.TimeoutExpired:
            r.check(f"{flag} finished", False, "timed out")
            continue
        res = [l for l in (p.stdout + p.stderr).splitlines() if re.match(r"(PASS|FAIL) ", l)]
        bad = [l for l in res if l.startswith("FAIL")]
        r.check(f"{flag}: {len(res) - len(bad)} PASS (need >= {floor}), 0 FAIL, exit {p.returncode}",
                not bad and len(res) >= floor and p.returncode == 0, bad[:3])
    rows = []
    for d in [o, *a.union]:
        for t in glob.glob(os.path.join(d, "**", "first-calls-*.tsv"), recursive=True):
            rows += [l.rstrip("\n").split("\t") for l in open(t)][1:]
    check_union(r, rows)
    return 0 if r.ok else 1


LIVE = ("map", "soak", "walk", "look", "keys", "focus", "chords", "replay", "perf", "chargen", "opening", "dialogue", "save",
        "minimise", "missing")


def build_parser():
    ap = argparse.ArgumentParser(prog="m6.py")
    sub = ap.add_subparsers(dest="mode", required=True)

    def add(name, fn, **opts):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        for k, v in opts.items():
            p.add_argument("--" + k.replace("_", "-"), **v)
        return p

    flag = {"action": "store_true"}
    live_opts = dict(exe={"default": X_DEFAULT}, overlay={}, timeout={"type": int}, close_after={"type": int},
                     margin={"type": int, "default": 60}, refdir={"default": REFDIR}, mask={}, display={"type": int},
                     size={"type": lambda s: tuple(int(x) for x in s.split("x")), "default": DEF["size"]},
                     min_colours={"type": int, "default": DEF["colours"]}, menu={"default": m5.MENU_DEFAULT},
                     chargen={"default": CHARGEN_DEFAULT}, game={"default": GAME_DEFAULT}, manifest={"default": MANIFEST_DEFAULT},
                     idle={"type": float, "default": IDLE_MAX}, p99={"type": float, "default": DEF["p99"]},
                     pump={"type": int, "default": DEF["pump"]}, block={"type": int, "default": DEF["block"]},
                     block_frac={"type": float, "default": DEF["block_frac"]}, tol={"type": int, "default": DEF["tol"]},
                     pct={"type": float, "default": DEF["pct"]}, mad={"type": float, "default": DEF["mad"]},
                     luma={"type": float, "default": DEF["luma"]})
    add("selftest", lambda a: selftest())
    add("cases", cmd_cases, exe={"default": X_DEFAULT}, overlay={}, timeout={"type": int},
        union={"nargs": "*", "default": []}, min_gfx={"type": int, "default": MIN_GFX}, min_input={"type": int, "default": 216})
    add("map", cmd_map, map={"default": FIRST}, guest_quiet=flag, **live_opts)
    add("soak", cmd_soak, map={"default": FIRST}, n={"type": int, "default": 3}, **live_opts)
    for nm, fn in (("walk", cmd_walk), ("look", cmd_look), ("keys", cmd_keys), ("chords", cmd_chords), ("replay", cmd_replay),
                   ("minimise", cmd_minimise), ("save", cmd_save), ("focus", cmd_focus), ("missing", cmd_missing)):
        p = add(nm, fn, map={"default": HAVEN}, **live_opts)
        if nm == "focus":
            p.add_argument("--during-load", action="store_true")
        if nm == "replay":
            p.add_argument("--demo", action="store_true")
        if nm == "save":
            p.add_argument("--quick", action="store_true")
    add("perf", cmd_perf, map={"default": HAVEN}, n={"type": int, "default": 3}, **live_opts)
    add("chargen", cmd_chargen, **live_opts)
    add("opening", cmd_opening, **{"from": {"dest": "start", "choices": ["sp_theatre", "sp_tutorial_1"], "required": True}}, **live_opts)
    add("dialogue", cmd_dialogue, **live_opts)
    return ap


def stage_lines(map_name=FIRST, t8=10.0, t12=30.0, hang=None, lost=0, extra=()):
    body = m4.fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 10], lost=lost).splitlines()
    out = list(body) + list(extra)
    if hang:
        out.append(f"rt: stop 15 gfx hang: {hang}")
    out.append(f"rt: stage 12 map {map_name}")
    return out, t8, t12


def timed(lines, t8, t12, exit_t=120.0):
    out, t = [], 0.0
    for l in lines:
        if l.startswith("rt: stage 8"):
            t = t8
        elif l.startswith("rt: stage 12"):
            t = t12
        else:
            t += 0.01
        out.append((t, l + "\n"))
    return out, exit_t


def fake_res(lines, t8=10.0, t12=30.0, rc=0, rows=None, exit_t=120.0):
    tl, ex = timed(lines, t8, t12, exit_t)
    return SimpleNamespace(rc=rc, log="".join(l for _, l in tl), lines=tl, exit=ex, crashes=[],
                           rows=m5.dinput_rows() if rows is None else rows)


def good_map_lines(name=FIRST, hang=None, lost=0, extra=(), tb=()):
    pre = [f"guest: {LEVEL_PRINT.get(name, 'loading x level script')}", f"guest: Map: {name}", "guest: R_NewMap",
           *[f"guest: {x}" for x in tb]]
    lines, _, _ = stage_lines(name, hang=hang, lost=lost, extra=[*pre, *extra])
    return lines


def look_log(on_before=True, warp=False, look=True):
    out = ["rt: stage 11 menu"]
    if on_before:
        out.append("rt: input relative on")
    out += ["rt: script 3 look 200 0" if look else "rt: script 3 sleep 1"]
    if not on_before:
        out.append("rt: input relative on")
    if warp:
        out.append("rt: cursor warp 640,512")
    return "\n".join(out) + "\n"


def walk_images(w=320, h=256, half_back=False, moved=True):
    base = m4.picture(w, h)
    mv = m4.with_square(base, 20, 20, 128, (0, 0, 0)) if moved else base
    back = m4.with_square(base, 20, 20, 108, (0, 0, 0)) if half_back else base
    return base, mv, back


def tree_with_manifest(tmp, files):
    game = os.path.join(tmp, "game")
    lines = []
    for rel, data in files.items():
        p = os.path.join(game, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
    mf = os.path.join(tmp, "manifest.txt")
    open(mf, "w").write("\n".join(lines) + "\n")
    return game, mf


def selftest():
    n = {"ok": 0}
    bad = []

    def expect(label, cond):
        n["ok"] += 1
        if not cond:
            bad.append(label)
            print(f"FAIL {label}", file=sys.__stdout__)

    def result(fn, *args, **kw):
        r = Rep(quiet=True)
        with contextlib.redirect_stdout(io.StringIO()):
            fn(r, *args, **kw)
        return r

    def raises(exc, fn, *args, **kw):
        try:
            fn(*args, **kw)
        except exc as e:
            return str(e)
        except Exception:
            return None
        return None

    def quiet(fn, *args, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            v = fn(*args, **kw)
        return v, buf.getvalue()

    names = sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(SCRIPTS, "*.txt")))
    want = sorted(["genesis", "walk", "look", "keys", "focus", "focus-load", "chords", "record", "minimise", "save", "quick",
                   "dialogue", "opening-theatre", "opening-tutorial"])
    expect(f"template files are exactly the 14 named in the design ({names})", names == want)
    ch = {"points": {"chargen.next": [640, 900], "dialogue.answer1": [300, 700]}, "steps": ["move @chargen.next", "click left", "sleep 1"]}
    for nm in names:
        txt = render_file(nm, None, {"map": HAVEN})
        cmds = parse_script(txt)
        digits = re.findall(r"\b\d{3,}\b", re.sub(r"^(sleep|look|minimise) .*$", "", txt, flags=re.M))
        expect(f"template {nm} renders, parses and holds no screen position", len(cmds) >= 3 and "@" not in txt.replace('"', "")
               and "$" not in txt and not digits and [c[0] for c in cmds] == list(range(1, len(cmds) + 1)))
    expect("every template has a wait map or starts with a focus/keydown line",
           all(parse_script(render_file(nm, None, {"map": HAVEN}))[0][1] in ("wait", "keydown") for nm in names))

    c = parse_script('wait map\nwait map sp_theatre\nlook 200 -4096\nminimise 10\nminimise 0.5\nwait menu\nsleep 1\ncapture a\n')
    expect("parser accepts wait map, wait map NAME, look, minimise and hands the rest to m5", [x[1] for x in c] ==
           ["wait", "wait", "look", "minimise", "minimise", "wait", "sleep", "capture"] and c[1][2] == "map sp_theatre")
    expect("parser keeps the source line numbers", [x[0] for x in c] == list(range(1, 9)))
    for bad_line in ("wait map a b", "wait map bad!name", "look 4097 0", "look 1", "look a b", "look 1 -4097", "minimise", "minimise -1",
                     "minimise 1 2", "wait nothing", "bogus"):
        expect(f"parser refuses '{bad_line}'", raises(ScriptError, parse_script, bad_line + "\n") is not None)
    expect("parser accepts look at the 4096 limits", parse_script("look -4096 4096\n")[0][1] == "look")
    expect("m5 alone refuses wait map (m6 owns it)", raises(ScriptError, m5.parse_script, "wait map\n") is not None)
    expect("script_seconds counts sleeps only", m5.script_seconds(parse_script("sleep 1.5\nminimise 10\nsleep 2\nwait map\n")) == 3.5)

    six = ["key grave", "sleep 0.5", 'text "save m6a"', "key return", "sleep 0.5", "key grave"]
    expect('console "save m6a" renders to the six lines', expand_console('console "save m6a"\n').splitlines() == six)
    expect("console macro keeps surrounding lines in order",
           expand_console('wait map\nconsole "stop"\nsleep 1\n').splitlines() == ["wait map", *six[:2], 'text "stop"', *six[3:], "sleep 1"])
    expect("console macro refuses an unquoted argument", raises(ScriptError, expand_console, "console save\n") is not None)
    expect("console macro refuses a bare console", raises(ScriptError, expand_console, "console\n") is not None)
    expect("console macro output parses", [x[1] for x in parse_script('console "x"\n')] == ["key", "sleep", "text", "key", "sleep", "key"])
    expect("render expands the macro", render('console "stop"\n') == "\n".join(six[:2] + ['text "stop"'] + six[3:]) + "\n")
    expect("render keeps an @ inside a console string", 'text "a@b"' in render('console "a@b"\n'))
    expect("@names come from the chargen points", "move 640 900\n" in render("move @chargen.next\n", ch))
    expect("an @name missing from the chargen points is refused", raises(ValueError, render, "move @nope\n", ch) is not None)
    expect("a $variable is substituted and an unknown one refused", "capture a1\n" in render("capture $m\n", None, {"m": "a1"}) and
           raises(ValueError, render, "capture $zz\n") is not None)
    expect("chargen with a point outside the client is refused", raises(ValueError, check_chargen, {"points": {"x": [1280, 1]}}) is not None)
    expect("chargen with a non-list steps is refused", raises(ValueError, check_chargen, {"points": {}, "steps": "x"}) is not None)
    expect("a missing chargen file loads empty", load_chargen("/nonexistent/m6-chargen.json") == {"points": {}, "steps": []})
    g = gen_chargen(m5.SEED, ch)
    expect("gen_chargen renders newgame from the menu points and the steps from the chargen points",
           "move 617 438\n" in g and "move 640 900\n" in g and g.index("wait map sp_genesisdevice_1") < g.index("move 640 900") <
           g.index("wait map sp_theatre") and len(parse_script(g)) > 8)
    w = gen_walk(120)
    wc = parse_script(w)
    expect("generated walk script parses and runs 120 s from the map ready", wc[0][2] == "map" and abs(m5.script_seconds(wc) - 120.0) < 1e-6)
    expect("generated walk script lasts at least as long as the close-after it is made for",
           all(m5.script_seconds(parse_script(gen_walk(x))) >= x for x in (130, 190, 205)))
    expect("generated walk script never stands still before the first key", wc[1][1] == "keydown")
    expect("generated walk script turns both ways and releases every key",
           any(c[1] == "look" and c[2].startswith("30") for c in wc) and any(c[1] == "look" and c[2].startswith("-30") for c in wc)
           and sum(c[1] == "keydown" for c in wc) == sum(c[1] == "keyup" for c in wc))
    expect("generated walk script is deterministic", gen_walk(60) == gen_walk(60) and gen_walk(60) != gen_walk(120))

    expect("RE_S12 reads the stage 12 line", RE_S12.findall("rt: stage 12 map sp_theatre\n") == ["sp_theatre"])
    expect("RE_READY reads name and seconds", RE_READY.findall("rt: map ready sm_pawnshop_1 at +41.25 s\n") == [("sm_pawnshop_1", "41.25")])
    expect("RE_READY reads the ? name", RE_READY.findall("rt: map ready ? at +3 s\n") == [("?", "3")])
    expect("relative lines parse", [e[2] for e in events("rt: input relative on\nrt: input relative off\n")] == ["on", "off"])
    expect("window lines parse", [e[2] for e in events("rt: window minimised\nrt: window restored\n")] == ["minimised", "restored"])
    expect("timedemo line parses printf padding", RE_TIMEDEMO.findall("guest: 523 frames  8.142 seconds 64.231 fps\n") == [("523", "8.142", "64.231")])
    expect("timedemo n below 100 fails", not result(check_timedemo, "guest: 99 frames 1.000 seconds 99.000 fps\n").ok and
           result(check_timedemo, "guest: 100 frames 1.000 seconds 99.000 fps\n").ok)

    nm = "NameError: name 'sp_genesisdevice_1_patch' is not defined"
    tbhead = ["Traceback (most recent call last):", '  File "<string>", line 1, in ?']
    for label, tb, ok in (("the <map>_patch NameError", tbhead + [nm], True),
                          ("the Python 2 global-name form", tbhead + ["NameError: global name 'sp_genesisdevice_1_patch' is not defined"], True),
                          ("a ValueError", tbhead + ["ValueError: bad"], False),
                          ("a NameError for another name", tbhead + ["NameError: name 'foo' is not defined"], False),
                          ("a _patch NameError for a map that never loaded", tbhead + ["NameError: name 'sm_hub_1_patch' is not defined"], False),
                          ("a traceback with no exception line", tbhead, False)):
        log = "\n".join(good_map_lines(tb=tb)) + "\n"
        expect(f"traceback rule {'allows' if ok else 'fails'} {label}", result(check_traceback, log).ok == ok)
    log = "\n".join(good_map_lines(tb=tbhead + [nm] + tbhead + ["KeyError: 1"])) + "\n"
    expect("one allowed and one other traceback fail together", not result(check_traceback, log).ok)
    expect("a log with no traceback passes", result(check_traceback, "\n".join(good_map_lines()) + "\n").ok)

    hang = "IDirect3DDevice9::Present blocked 5003 ms"
    hl = "\n".join(good_map_lines(hang=hang)) + "\n"
    expect("the hang stop line fails check_hang", not result(check_hang, hl).ok)
    expect("a dxvk GPU-hung line fails check_hang", not result(check_hang, "gfx hang: dxvk: Timeout occured, GPU likely hung.\n").ok)
    expect("VK_ERROR_DEVICE_LOST fails check_hang", not result(check_hang, "err: VK_ERROR_DEVICE_LOST\n").ok)
    expect("a clean log passes check_hang", result(check_hang, "\n".join(good_map_lines()) + "\n").ok)
    clean = fake_res(good_map_lines())
    expect("a clean map run passes the run rules", result(rules, clean, 60).ok)
    expect("the hang stop line fails the run rules even with exit 0 and a clean script", not result(rules, fake_res(good_map_lines(hang=hang)), 60).ok)
    expect("rt: gfx lost 1 fails the run rules", not result(rules, fake_res(good_map_lines(lost=1)), 60).ok)
    expect("a stub first-call row fails the run rules", not result(rules, fake_res(good_map_lines(), rows=m5.dinput_rows() + [["1", "d3d9.dll!X", "stub"]]), 60).ok)
    expect("a non-zero exit fails the run rules", not result(rules, fake_res(good_map_lines(), rc=10), 60).ok)
    expect("a Python traceback fails the run rules", not result(rules, fake_res(good_map_lines(tb=tbhead + ["KeyError: 1"])), 60).ok)
    expect("rt: shaders failed above 0 fails the run rules", not result(rules, SimpleNamespace(
        **{**vars(clean), "log": clean.log.replace("failed 0", "failed 2")}), 60).ok)
    try:
        fl = m4.fixture_log().splitlines() + [f"rt: stop 15 gfx hang: {hang}"]
        pend = not result(m4.check_faults, "\n".join(fl) + "\n", [], 0).ok
        print(f"note: pending H: m4.check_faults {'fails' if pend else 'does not yet fail'} on the hang stop line (not counted)", file=sys.__stdout__)
    except Exception as e:
        print(f"note: pending H: m4.check_faults probe failed ({e}) (not counted)", file=sys.__stdout__)
    ap = build_parser()
    for nm in LIVE + ("selftest", "cases"):
        argv = [nm] + (["--from", "sp_theatre"] if nm == "opening" else [])
        expect(f"mode {nm} parses", ap.parse_args(argv).fn is not None)
    expect("mode table is complete", set(LIVE) | {"selftest", "cases"} == set(ap._subparsers._group_actions[0].choices))

    class Runner:
        def __init__(self, res):
            self.res, self.calls = res, []

        def __call__(self, exe, o, extra, timeout):
            self.calls.append(extra)
            return self.res

    global RUNNER
    saved = RUNNER
    with tempfile.TemporaryDirectory() as tmp:
        a = SimpleNamespace(exe=sys.executable, overlay=tmp, timeout=None, close_after=None, margin=60, display=None, chargen="/nonexistent/c.json")
        try:
            for label, res, ok in (("a clean map run", None, True),
                                   ("a run with the hang stop line", fake_res(good_map_lines(hang=hang)), False)):
                if res is None:
                    path_cmds = parse_script(render_file("genesis", None, {"map": FIRST}))
                    sl = [f"rt: script {c[0]} {c[1]} {c[2]}".rstrip() for c in path_cmds] + ["rt: script done"]
                    res = fake_res(good_map_lines(extra=sl))
                RUNNER = Runner(res)
                r, logs = Rep(quiet=True), []
                with contextlib.redirect_stdout(io.StringIO()):
                    maprun(r, logs, a, "t", "genesis", FIRST)
                expect(f"go runs {label} through the rules ({'passes' if ok else 'fails'})", r.ok == ok and len(logs) == 1)
            call = RUNNER.calls[0]
            expect("go passes --close-after, --input-script and the +developer 4 +map tail",
                   call[:1] == ["--close-after"] and "--input-script" in call and call[call.index("--") + 1:] == ["+developer", "4", "+map", FIRST])
            RUNNER = Runner(fake_res(good_map_lines()))
            r, logs = Rep(quiet=True), []
            with contextlib.redirect_stdout(io.StringIO()):
                go_plain(r, logs, a, tmp, tail=("+map", FIRST), flags=("--guest-quiet",))
            c0 = RUNNER.calls[0]
            expect("go_plain passes --guest-quiet, 190 s and no input script", c0[0] == "--guest-quiet" and c0[c0.index("--close-after") + 1] == "190"
                   and "--input-script" not in c0)
        finally:
            RUNNER = saved

    r1 = fake_res(good_map_lines(), t8=10, t12=70)
    r2 = fake_res(good_map_lines(), t8=10, t12=71)
    expect("stage 12 exactly 60 s after stage 8 passes AC-A2", result(check_stage12, r1, FIRST).ok)
    expect("stage 12 61 s after stage 8 fails AC-A2", not result(check_stage12, r2, FIRST).ok)
    expect("stage 12 for the wrong map fails", not result(check_stage12, r1, "sp_theatre").ok)
    expect("a log with no stage 12 fails", not result(check_stage12, SimpleNamespace(log="rt: stage 8 console\n", lines=[(1.0, "rt: stage 8 console\n")]), FIRST).ok)
    expect("level print, Map:, R_NewMap, stage 12 in order passes", result(check_a2, "\n".join(good_map_lines()) + "\n", FIRST).ok)
    o = good_map_lines()
    o.insert(0, o.pop(o.index("guest: R_NewMap")))
    expect("R_NewMap before the level print fails the order", not result(check_a2, "\n".join(o) + "\n", FIRST).ok)
    expect("map ready with ? fails", not result(check_ready_names, "rt: map ready ? at +3 s\n").ok and
           result(check_ready_names, "rt: map ready sp_theatre at +3 s\n", ["sp_theatre"]).ok)
    expect("ready_times maps names to seconds", ready_times("rt: map ready a at +1.5 s\nrt: map ready b at +9 s\n") == {"a": 1.5, "b": 9.0})
    ok_order = "\n".join(good_map_lines()) + "\n"
    expect("map_stages accepts 1..10 then 12", result(map_stages, ok_order).ok)
    expect("map_stages accepts a menu run (11 then 12)", result(map_stages, ok_order.replace("rt: stage 10 first-present\n", "rt: stage 10 first-present\nrt: stage 11 menu\n")).ok)
    expect("map_stages refuses a missing stage 12", not result(map_stages, "\n".join(good_map_lines()[:-1]) + "\n").ok)
    expect("map_stages refuses stage 12 twice", not result(map_stages, ok_order + "rt: stage 12 map x\n").ok)
    expect("map_stages refuses a missing stage 8", not result(map_stages, ok_order.replace("rt: stage 8 console\n", "")).ok)

    expect("AC-B2: relative on before the first look passes", result(check_relative, look_log(True)).ok)
    expect("AC-B2: rt: input relative on after the first look line fails", not result(check_relative, look_log(False)).ok)
    expect("AC-B2: a cursor warp after relative on fails", not result(check_relative, look_log(True, warp=True)).ok)
    expect("AC-B2: a log with no look line fails", not result(check_relative, look_log(True, look=False)).ok)
    expect("AC-B2: a log with no relative on fails", not result(check_relative, "rt: script 3 look 200 0\n").ok)

    full = [bytes([1] * 320) for _ in range(256)]
    b, mv, back = walk_images()
    expect("walk fixture moves by at least 8/255", m5.mean_abs(b, mv, full) >= MOVE_MIN)
    expect("a walk triple that returns passes AC-B1", result(check_triple, "walk", b, mv, back, full).ok)
    b2, mv2, half = walk_images(half_back=True)
    d1, d2 = m5.mean_abs(b2, mv2, full), m5.mean_abs(b2, half, full)
    expect("fixture: MAD(w0,w2) is above half of MAD(w0,w1)", d2 > d1 * 0.5)
    expect("a walk triple with MAD(w0,w2) above half of MAD(w0,w1) fails AC-B1", not result(check_triple, "walk", b2, mv2, half, full).ok)
    expect("a walk triple that never moves fails AC-B1", not result(check_triple, "walk", b, b, b, full).ok)
    expect("idle pair within the limit passes and above fails", result(check_idle, "i", b, b, full, 2.0).ok and
           not result(check_idle, "i", b, mv, full, 2.0).ok)
    wl = ["rt: input WM_KEYDOWN wp 0x57 lp 0x110001", "rt: input WM_KEYUP wp 0x57 lp 0xC0110001",
          "rt: input WM_KEYDOWN wp 0x53 lp 0x1F0001", "rt: input WM_KEYUP wp 0x53 lp 0xC01F0001"]
    expect("AC-B1 scan check passes with W and S down and up", result(check_scans, "\n".join(wl) + "\n", ["w", "s"]).ok)
    expect("AC-B1 scan check fails without the S key-up", not result(check_scans, "\n".join(wl[:3]) + "\n", ["w", "s"]).ok)

    fl = ["rt: script 5 keydown w", "rt: input WM_KEYDOWN wp 0x57 lp 0x110001", "rt: script 7 focus lost",
          "rt: input WM_KEYUP wp 0x57 lp 0xC0110001", "rt: input WM_ACTIVATEAPP wp 0x0 lp 0x0", "rt: input relative off",
          "rt: script 9 focus gained"]
    expect("AC-B4: key-up before ACTIVATEAPP 0 and relative off after focus loss passes", result(check_focus6, "\n".join(fl) + "\n").ok)
    expect("AC-B4: key-up after ACTIVATEAPP 0 fails", not result(check_focus6, "\n".join(fl[:3] + [fl[4], fl[3], fl[5]]) + "\n").ok)
    expect("AC-B4: no relative off fails", not result(check_focus6, "\n".join(fl[:5]) + "\n").ok)
    ld = ["rt: input WM_KEYDOWN wp 0x57 lp 0x110001", "rt: input WM_KEYUP wp 0x57 lp 0xC0110001"]
    expect("focus during load: no key-down after the key-up passes", result(check_focus_load, "\n".join(ld) + "\n").ok)
    expect("focus during load: a key-down after the key-up fails", not result(check_focus_load, "\n".join(ld + [ld[0]]) + "\n").ok)
    cl = ["rt: script 4 chord tab", "rt: script 5 sleep 0.5", "rt: script 6 chord a", "rt: script 7 sleep 0.5", "rt: script 8 keyup w",
          "rt: input moves 4 buttons 0 keys 9 chars 12 dropped-chars 0 chords 2 warps 1"]
    expect("AC-B5: two chords with no key line between pass", result(check_chords6, "\n".join(cl) + "\n").ok)
    expect("AC-B5: a key line between the chords fails", not result(check_chords6, "\n".join(cl[:2] + ["rt: input WM_KEYDOWN wp 0x41 lp 0x1E0001"] + cl[2:]) + "\n").ok)
    expect("AC-B5: chords counter other than 2 fails", not result(check_chords6, "\n".join(cl).replace("chords 2", "chords 3") + "\n").ok)
    mn = ["rt: script 4 minimise 10", "rt: window minimised", "rt: window restored", "rt: script 6 capture after"]
    expect("minimise: restored before the capture passes", result(check_minimise, "\n".join(mn) + "\n").ok)
    expect("minimise: capture while still minimised fails", not result(check_minimise, "\n".join([mn[0], mn[1], mn[3], mn[2]]) + "\n").ok)
    expect("minimise: no restored line fails", not result(check_minimise, "\n".join(mn[:2] + mn[3:]) + "\n").ok)

    with tempfile.TemporaryDirectory() as tmp:
        files = {"Bin/a.dll": b"aaa", "Vampire/maps/m.bsp": b"bsp", "Vampire/cfg/x.cfg": b"cfg"}
        game, mf = tree_with_manifest(tmp, files)
        expect("manifest names parse", manifest_names(mf) == set(files))
        expect("game_files lists relative paths", game_files(game) == set(files))
        open(os.path.join(game, "Vampire", ".DS_Store"), "wb").write(b"x")
        open(os.path.join(game, ".DS_Store"), "wb").write(b"x")
        expect("game_files skips .DS_Store at any depth", game_files(game) == set(files))
        os.unlink(os.path.join(game, "Vampire", ".DS_Store"))
        os.unlink(os.path.join(game, ".DS_Store"))
        expect("a clean tree passes the file-set and hash check", check_game(game, mf) == ([], [], []))
        gargs = SimpleNamespace(exe=sys.executable, game=game, manifest=mf)
        SESSION.clear()
        rc, out = quiet(start, gargs)
        expect("start passes on a clean tree", rc == 0 and out == "")
        open(os.path.join(game, "Vampire", "extra.dll"), "wb").write(b"patch")
        expect("check_game names an extra file", check_game(game, mf)[0] == ["Vampire/extra.dll"])
        rc, out = quiet(start, SimpleNamespace(exe=sys.executable, game=game, manifest=mf))
        expect("session cache keeps the first answer for the same tree", rc == 0)
        SESSION.clear()
        rc, out = quiet(start, SimpleNamespace(exe=sys.executable, game=game, manifest=mf))
        expect("an extra file in the game tree refuses with exit 1 and names it", rc == 1 and "Vampire/extra.dll" in out and "REFUSED" in out)
        os.unlink(os.path.join(game, "Vampire", "extra.dll"))
        open(os.path.join(game, "Bin", "a.dll"), "wb").write(b"AAA")
        expect("check_game names a changed file", check_game(game, mf)[1] == ["Bin/a.dll"])
        SESSION.clear()
        rc, out = quiet(start, SimpleNamespace(exe=sys.executable, game=game, manifest=mf))
        expect("a changed file refuses with exit 1 and names it", rc == 1 and "Bin/a.dll" in out)
        open(os.path.join(game, "Bin", "a.dll"), "wb").write(b"aaa")
        os.unlink(os.path.join(game, "Bin", "a.dll"))
        expect("check_game names a missing file", check_game(game, mf)[2] == ["Bin/a.dll"])
        SESSION.clear()
        rc, out = quiet(start, SimpleNamespace(exe=sys.executable, game=game, manifest=mf))
        expect("a missing file refuses with exit 1 and names it", rc == 1 and "Bin/a.dll" in out)
        SESSION.clear()
        rc, out = quiet(start, SimpleNamespace(exe="/nonexistent/exe", game=game, manifest=mf))
        expect("a missing executable refuses before the file check", rc == 1 and "REFUSED" not in out)
        SESSION.clear()
    expect("shasum failure lines parse", RE_SHASUM_BAD.findall("a/b c: OK\nd e: FAILED\nf: FAILED open or read\ng: FAILED\n") == ["d e", "g"])
    expect("a manifest line with a binary-mode star parses", manifest_names_from("abc  *x/y\n") == {"x/y"})

    expect("a padded pgrep count parses (macOS pads wc -l)", pids("       3\n") == [3] and pids("\n  12\n 7 \n") == [12, 7])
    expect("pids ignores non-numeric text", pids("pgrep: no match\n") == [])
    expect("a load of 9.0 refuses and 8.99 does not", gate_reasons(9.0, lambda c: "", set()) != [] and gate_reasons(8.99, lambda c: "", set()) == [])
    expect("a load of 12 refuses", "load average" in " ".join(gate_reasons(12.0, lambda c: "", set())))
    fakes = {"bloodlines-native": "  4242\n", "build_all.py": "", "screencapture": ""}
    run = lambda c: fakes[next(n for n, cmd in BUSY if cmd == c)]
    expect("another bloodlines-native refuses and names it", any("bloodlines-native (pid 4242)" in x for x in gate_reasons(1.0, run, set())))
    fakes["bloodlines-native"] = ""
    fakes["build_all.py"] = "77\n"
    expect("a running build_all.py refuses", any("build_all.py (pid 77)" in x for x in gate_reasons(1.0, run, set())))
    fakes["build_all.py"] = ""
    fakes["screencapture"] = " 5\n"
    expect("a running screencapture refuses", any("screencapture (pid 5)" in x for x in gate_reasons(1.0, run, set())))
    expect("our own ancestors do not count", gate_reasons(1.0, run, {5}) == [])
    fakes["build_all.py"] = "77\n"
    expect("functional mode starts at load 12 with a build_all.py running and logs it",
           gate_reasons(12.0, run, {5}, functional=True) == [] and build_notes(run, {5}) == ["build_all.py (pid 77)"])
    expect("strict mode still refuses load 12 and a build_all.py", len(gate_reasons(12.0, run, {5})) == 2 and
           any("load average" in x for x in gate_reasons(12.0, run, {5})) and any("build_all.py" in x for x in gate_reasons(1.0, run, {5})))
    fakes["bloodlines-native"] = "  4242\n"
    expect("functional mode still refuses a second game", len(gate_reasons(1.0, run, {5}, functional=True)) == 1)
    fakes["bloodlines-native"] = ""
    fakes["build_all.py"] = ""
    fakes["screencapture"] = " 5\n"
    expect("functional mode keeps refusing a screencapture", len(gate_reasons(1.0, run, {1}, functional=True)) == 1)
    fakes["screencapture"] = ""
    expect("nothing running and a quiet load passes", gate_reasons(1.0, lambda c: "", set()) == [])
    expect("ancestors walks ps and stops at pid 1", ancestors(lambda c: {str(os.getpid()): " 500\n", "500": "  1\n"}.get(c[-1], "1")) == {os.getpid(), 500})
    expect("the real gate parses the real pgrep and ps", isinstance(gate_reasons(0.0), list))

    class Fake:
        def __init__(self, vals):
            self.vals, self.i = vals, 0

        def read(self):
            v = self.vals[min(self.i, len(self.vals) - 1)]
            self.i += 1
            return v

    s = Sampler(Fake([1.0, 2.0, 9.5, 4.0]).read, 0.01)
    s.start()
    time.sleep(0.15)
    got = s.stop()
    vals = [v for t, v in got]
    expect("the sampler reads at start, every period and at stop, with times", len(got) >= 4 and vals[0] == 1.0 and 9.5 in vals and
           all(got[i][0] <= got[i + 1][0] for i in range(len(got) - 1)))
    expect("the sampler thread ends on stop", not s.th.is_alive())

    class FakeSampler:
        def __init__(self, samples):
            self.samples = samples

        def start(self):
            pass

        def stop(self):
            return self.samples

    def windowed(samples, win, log):
        calls = []

        def body(r, out):
            calls.append(1)
            out.append((log, *win))
            r.check("x", "ok" in log)
        with contextlib.redirect_stdout(io.StringIO()):
            ps = run_with_rerun(body, lambda: FakeSampler(samples))
        return len(calls), quiet(finish, ps)[0]

    expect("a busy wait before a quiet run does not buy a rerun: gfx lost 1 at load 2 gives one attempt and rc 1",
           windowed([(1.0, 10.0), (5.0, 2.0)], (3.0, 6.0), "rt: gfx lost 1\n") == (1, 1))
    expect("a load sample of 9.5 inside the run window still reruns exactly once",
           windowed([(1.0, 2.0), (4.0, 9.5)], (3.0, 6.0), "rt: gfx lost 1\n")[0] == 2)
    expect("a load sample of 9 at the window edge counts", windowed([(6.0, 9.0)], (3.0, 6.0), "rt: gfx lost 1\n")[0] == 2)
    expect("a load sample just after the window does not count", windowed([(6.1, 12.0)], (3.0, 6.0), "rt: gfx lost 1\n")[0] == 1)

    def rerun(samples, logs):
        calls = []

        def body(r, out):
            calls.append(1)
            out.append((logs[min(len(calls), len(logs)) - 1], 0.0, 1.0))
            r.check("x", "ok" in out[-1][0])
        mk = lambda: FakeSampler([(0.1 * (i + 1), v) for i, v in enumerate(samples)])
        with contextlib.redirect_stdout(io.StringIO()):
            ps = run_with_rerun(body, mk)
        return len(calls), ps

    lost1 = "rt: gfx lost 1\n"
    expect("a load sample of 9.5 with rt: gfx lost 1 gives exactly one rerun", rerun([9.5], [lost1, "ok rt: gfx lost 0\n"])[0] == 2)
    expect("the rerun is not repeated when the second run is also bad", rerun([9.5], [lost1, lost1, lost1])[0] == 2)
    expect("the rerun reports both runs", len(rerun([9.5], [lost1, "ok rt: gfx lost 0\n"])[1]) == 2)
    expect("the rerun's second run decides the verdict", quiet(finish, rerun([9.5], [lost1, "ok rt: gfx lost 0\n"])[1])[0] == 0 and
           quiet(finish, rerun([9.5], [lost1, lost1])[1])[0] == 1)
    expect("a load of 8.9 with rt: gfx lost 1 does not rerun", rerun([8.9], [lost1])[0] == 1)
    expect("a load of 9.0 counts as 9 or more", rerun([9.0], [lost1])[0] == 2)
    expect("a hang stop line with load 10 reruns", rerun([10.0], ["gfx hang: x blocked 5001 ms\n", "ok\n"])[0] == 2)
    expect("a dxvk hung line with load 10 reruns", rerun([10.0], ["Timeout occured, GPU likely hung.\n", "ok\n"])[0] == 2)
    expect("load 12 with a clean run does not rerun", rerun([12.0], ["rt: gfx lost 0\nok\n"])[0] == 1)
    expect("a failing run without a gfx fault does not rerun even at load 12", rerun([12.0], ["rt: gfx lost 0\n"])[0] == 1)
    expect("any sample of 9 or more counts, not only the first", rerun([2.0, 9.5], [lost1, "ok\n"])[0] == 2 and rerun([2.0, 2.0], [lost1])[0] == 1)
    expect("gfx_trouble sees lost above 0, a hang stop and a dxvk line", gfx_trouble(lost1) and gfx_trouble("gfx hang: x\n") and
           gfx_trouble("GPU likely hung") and not gfx_trouble("rt: gfx lost 0\n"))

    with tempfile.TemporaryDirectory() as tmp:
        p, cmds = prepare(os.path.join(tmp, "o"), "t", 'wait map\nconsole "stop"\nlook 5 5\n')
        txt = open(p).read()
        expect("prepare writes the expanded script so rt: script numbers match", txt.splitlines()[1] == "key grave" and len(cmds) == 8 and
               [c[0] for c in cmds] == list(range(1, 9)))
        expect("prepare refuses a bad script", raises(ScriptError, prepare, os.path.join(tmp, "o"), "t2", "look 1\n") is not None)
        os.makedirs(os.path.join(tmp, "s", "files", "Vampire", "SAVE"))
        expect("save_files finds nothing before a save", save_files(os.path.join(tmp, "s")) == [])
        open(os.path.join(tmp, "s", "files", "Vampire", "SAVE", "m6a-0001.sav"), "w").write("x")
        expect("save_files finds a file whose name contains m6a", len(save_files(os.path.join(tmp, "s"))) == 1)
        src = os.path.join(tmp, "src")
        os.makedirs(os.path.join(src, "Vampire", "maps"))
        for f in ("Vampire/maps/sp_tutorial_1.bsp", "Vampire/maps/sp_theatre.bsp", "Vampire/x.txt"):
            open(os.path.join(src, f), "w").write("x")
        dst = os.path.join(tmp, "dst")
        symlink_tree(src, dst, "Vampire/maps/sp_tutorial_1.bsp")
        expect("the missing-map tree has symlinks to every file but the skipped one",
               sorted(game_files(dst)) == ["Vampire/maps/sp_theatre.bsp", "Vampire/x.txt"] and
               all(os.path.islink(os.path.join(dst, f)) for f in game_files(dst)))
        wa = SimpleNamespace(mask=None, refdir=tmp)
        rr = Rep(quiet=True)
        expect("world_spec is None and fails without a mask", world_spec(rr, wa) is None and not rr.ok)
        json.dump({"width": 320, "height": 256, "rects": [[0, 0, 320, 128]]}, open(os.path.join(tmp, "m6-world-mask.json"), "w"))
        rr = Rep(quiet=True)
        expect("world_spec loads m6-world-mask.json", world_spec(rr, wa)["width"] == 320 and rr.ok)

    ok_rows = [["1", "d3d9.dll!IDirect3DTexture9::SetPriority", "bridge"], ["1", "d3d9.dll!IDirect3D9::CreateDevice", "bridge"]]
    expect("AC-A1: a bridge SetPriority row and no stub passes", result(check_union, ok_rows).ok)
    expect("AC-A1: no SetPriority row fails (not vacuous)", not result(check_union, ok_rows[1:]).ok and not result(check_union, []).ok)
    expect("AC-A1: a stub row fails", not result(check_union, ok_rows + [["1", "x.dll!Y", "stub"]]).ok)
    expect("AC-A1: a shim SetPriority row fails", not result(check_union, [["1", ok_rows[0][1], "shim"]]).ok)
    expect("AC-A1: the gfx selftest floor is today's 32 plus 93 methods plus 4 map-tracker cases", build_parser().parse_args(["cases"]).min_gfx == 129)
    ar = "rt: allocator high-water 0x1F400000 bytes, %d failed allocations\n"
    expect("AC-A6: allocator failed 0 passes", result(check_allocator, ar % 0).ok)
    expect("AC-A6: allocator with 3 failed allocations fails", not result(check_allocator, ar % 3).ok)
    expect("AC-A6: a missing allocator line fails", not result(check_allocator, "rt: stage 8 console\n").ok)
    expect("AC-C5: the tutorial exit uses the ChangeNow input",
           'console "ent_fire trig_leave_tutorial ChangeNow"' in open(os.path.join(SCRIPTS, "opening-tutorial.txt")).read() and
           "ChangeLevel" not in render_file("opening-tutorial"))
    expect("replay is the scripted replay by default and --demo selects the demo",
           not build_parser().parse_args(["replay"]).demo and build_parser().parse_args(["replay", "--demo"]).demo)

    gl = globals()
    saved_g = {k: gl[k] for k in ("RUNNER", "gate_reasons", "session_ok", "QUIET_WAIT", "QUIET_POLL")}
    saved_screen = m5.screen
    try:
        with tempfile.TemporaryDirectory() as tmp:
            gdir = os.path.join(tmp, "g")
            os.makedirs(gdir)
            gl["session_ok"] = lambda *x, **k: True
            gl["QUIET_WAIT"], gl["QUIET_POLL"] = 0.2, 0.01

            def args(*argv, **extra):
                opts = {"exe": sys.executable, "overlay": os.path.join(tmp, "o"), "game": gdir, "chargen": os.path.join(tmp, "none.json"),
                        "refdir": tmp, **extra}
                return build_parser().parse_args([*argv] + [x for k, v in opts.items() for x in (f"--{k}", v)])

            def drive(nm, argv, gate, res=None, **extra):
                seq = iter(gate)
                gl["gate_reasons"] = lambda *x, **k: next(seq, gate[-1])
                rn = Runner(res or fake_res(good_map_lines()))
                gl["RUNNER"] = rn
                try:
                    rc, out = quiet(globals()["cmd_" + nm], args(nm, *argv, **extra))
                except Exception:
                    rc, out = None, ""
                return rc, out, rn

            rc, out, rn = drive("perf", ["--n", "2"], [[]])
            expect("perf starts every run, and the logging-cost run, on a quiet machine", len(rn.calls) == 3 and
                   out.count("no other bloodlines-native, build or screencapture process") == 3)
            ex = rn.calls[0]
            secs = m5.script_seconds(parse_script(open(ex[ex.index("--input-script") + 1]).read()))
            expect("AC-B7: the perf walk script lasts as long as the run stays in the map", secs >= float(ex[ex.index("--close-after") + 1]))
            rc, out, rn = drive("perf", ["--n", "2"], [[], [], ["screencapture (pid 5)"]])
            expect("AC-B7: a busy machine at the second run start skips that run and the logging-cost run and fails",
                   len(rn.calls) == 1 and rc == 1 and "FAIL quiet machine at run 2 start" in out)
            rc, out, rn = drive("soak", ["--n", "2"], [[], [], ["build_all.py (pid 9)"]])
            expect("AC-A4: a busy machine at the second soak run start skips it and fails",
                   len(rn.calls) == 1 and rc == 1 and "FAIL quiet machine at run 2 start" in out)
            rc, out, rn = drive("soak", ["--n", "2"], [[]])
            expect("AC-A4: a quiet machine runs every soak run", len(rn.calls) == 2 and "no other bloodlines-native" in out)
            rc, out, rn = drive("perf", ["--n", "1"], [[], [], ["screencapture (pid 5)"]])
            expect("the logging-cost run's busy machine is recorded in the side report, not the gated verdict",
                   len(rn.calls) == 1 and "quiet machine at logging-cost" not in out)
            rc, out, rn = drive("perf", ["--n", "1"], [[], [], ["screencapture (pid 5)"]])
            expect("a busy machine at the logging-cost run says it was not run", "-- logging-cost run not run: machine stayed busy" in out)
            rc, out, rn = drive("perf", ["--n", "1"], [[], ["load average 10 is 9 or more"], ["load average 9.5 is 9 or more"], []])
            expect("the quiet gate polls and runs once the load clears within the window",
                   len(rn.calls) == 2 and out.count("waiting for a quiet machine") == 2 and "FAIL quiet machine" not in out)
            for nm in LIVE:
                argv = ["--from", "sp_theatre"] if nm == "opening" else []
                rc, out, rn = drive(nm, argv, [["load average 12 is 9 or more"]])
                expect(f"mode {nm} {'refuses' if nm in ('perf', 'soak') else 'does not refuse'} a loaded machine",
                       ("REFUSED: load average" in out) == (nm in ("perf", "soak")) and (nm not in ("perf", "soak") or (rc == 1 and not rn.calls)))
            rc, out, rn = drive("replay", [], [[]])
            expect("replay runs the walk script twice and no timedemo by default",
                   len(rn.calls) == 2 and not any("+timedemo" in c for c in rn.calls))
            rc, out, rn = drive("replay", ["--demo"], [[]])
            expect("replay --demo records, then plays a timedemo", len(rn.calls) == 2 and "+timedemo" in rn.calls[1])

            shots = []
            m5.screen = lambda r_, a_, o_, shot, ref=None: shots.append((shot, ref))
            cj = os.path.join(tmp, "cg.json")
            json.dump({"points": {"x": [1, 2]}, "steps": ["move @x", "click left"]}, open(cj, "w"))
            mj = os.path.join(tmp, "menu.json")
            m5.seed_menu(mj)
            drive("chargen", [], [[]], chargen=cj, menu=mj)
            expect("AC-C2: the chargen run compares the theatre arrival capture with its reference",
                   ("sp_theatre-arrival", "m6-sp_theatre-arrival") in shots)

            base = m4.picture()
            second = m4.with_square(base, 20, 20, 128, (0, 0, 0))
            m5.screen = lambda r_, a_, o_, shot, ref=None: {"dialogue-1": base, "dialogue-2": second}.get(shot)
            dm = os.path.join(tmp, "m6-dialogue-1-mask.json")
            rc, out, rn = drive("dialogue", [], [[]])
            expect("AC-C4: a missing dialogue-1 mask fails the run instead of skipping the check",
                   "FAIL mask" in out and "PASS dialogue-2 differs" not in out)
            json.dump({"width": 320, "height": 256, "rects": [[0, 0, 320, 256]]}, open(dm, "w"))
            rc, out, rn = drive("dialogue", [], [[]])
            expect("AC-C4: dialogue-2 is compared with this run's dialogue-1 inside the reference's mask, with no world mask",
                   "PASS dialogue-2 differs from dialogue-1" in out)
            m5.screen = lambda r_, a_, o_, shot, ref=None: base if shot.startswith("dialogue") else None
            rc, out, rn = drive("dialogue", [], [[]])
            expect("AC-C4: identical dialogue captures fail", "FAIL dialogue-2 differs from dialogue-1" in out)
    finally:
        gl.update(saved_g)
        m5.screen = saved_screen

    print(f"selftest: {n['ok'] - len(bad)} of {n['ok']} passed")
    return 0 if not bad else 1


def manifest_names_from(text):
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as f:
        f.write(text)
        f.flush()
        return manifest_names(f.name)


def main():
    a = build_parser().parse_args()
    try:
        return a.fn(a)
    except (OSError, ValueError, PngError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
