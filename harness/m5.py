#!/usr/bin/env python3
import argparse
import contextlib
import glob
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import m4
from m4 import (DEF, PngError, REFDIR, Rep, X_DEFAULT, check_faults, check_stages, check_stats, load_mask,
                need_exe, picture, read_png, timeout_note)

SCRIPTS = os.path.join(HERE, "m5")
MENU_DEFAULT = os.path.join(REFDIR, "m5-menu.json")
CLIENT = (1280, 1024)
HOVER_MIN = 8.0
QUIET_MAX = 2.0
DIFFER_PCT = 0.01
QUIT_SECONDS = 15
ALIVE_SECONDS = 10
SOAK_MOVES = 5000
SOAK_TAIL = 50
SOAK_CLOSE = 100
STEP_MS = 10
CLICK_EVERY = 250
DINPUT_ROWS = ["DirectInput8Create", "IDirectInput8A::Release"]
CHORDS = 3
BUTTON_NAMES = ("left", "right", "middle", "x1", "x2")
KEYMSG = {"WM_KEYDOWN", "WM_KEYUP", "WM_SYSKEYDOWN", "WM_SYSKEYUP", "WM_CHAR"}
MARKER = re.compile(r"^guest: .*(\[CharGenWiz\]|CharGenWizard: DisplayPopUp)", re.M)

RE_INPUT = re.compile(r"^rt: input (WM_\w+) wp 0x([0-9A-Fa-f]+) lp 0x([0-9A-Fa-f]+)\s*$")
RE_SCRIPT = re.compile(r"^rt: script (\d+) (\S.*?)\s*$")
RE_DONE = re.compile(r"^rt: script done\s*$")
RE_CURSOR = re.compile(r"^rt: cursor (\S.*?)\s*$")
RE_WARP = re.compile(r"^(?:warp (-?\d+),(-?\d+)|set (-?\d+),(-?\d+) \(no warp\))$")
RE_COUNTERS = re.compile(r"^rt: input moves (\d+) buttons (\d+) keys (\d+) chars (\d+) dropped-chars (\d+) "
                         r"chords (\d+) warps (\d+)\s*$", re.M)
RE_QUEUE_FULL = re.compile(r"message queue full")
RE_NAME = re.compile(r"[A-Za-z0-9_-]+")

SEED = {
    "points": {"newgame": [617, 438], "loadgame": [617, 533], "options": [617, 634], "quit": [619, 729],
               "empty": [1180, 950]},
    "rects": {"newgame": [449, 417, 338, 44], "loadgame": [433, 512, 370, 44], "options": [483, 607, 270, 56],
              "quit": [540, 702, 159, 55]},
}


class ScriptError(ValueError):
    pass


def check_menu(m):
    if not isinstance(m, dict):
        raise ValueError("m5-menu.json is not an object")
    cw, ch = CLIENT
    pts, rects = m.get("points"), m.get("rects")
    if not isinstance(pts, dict) or not isinstance(rects, dict):
        raise ValueError("m5-menu.json needs points and rects objects")
    for k in ("newgame", "loadgame", "options", "quit", "empty"):
        if k not in pts:
            raise ValueError(f"m5-menu.json points has no {k}")
    for k in ("newgame", "loadgame", "options", "quit"):
        if k not in rects:
            raise ValueError(f"m5-menu.json rects has no {k}")
    for k, v in pts.items():
        if (not isinstance(v, list) or len(v) != 2 or not all(isinstance(i, int) for i in v) or
                not (0 <= v[0] < cw and 0 <= v[1] < ch)):
            raise ValueError(f"point {k} {v} is not [x, y] inside {cw}x{ch}")
    for k, v in rects.items():
        if (not isinstance(v, list) or len(v) != 4 or not all(isinstance(i, int) for i in v) or
                v[2] <= 0 or v[3] <= 0 or v[0] < 0 or v[1] < 0 or v[0] + v[2] > cw or v[1] + v[3] > ch):
            raise ValueError(f"rect {k} {v} is not [x, y, w, h] inside {cw}x{ch}")
    tabs = m.get("options", {}).get("tabs", [])
    for t in tabs:
        if not isinstance(t, str) or not RE_NAME.fullmatch(t):
            raise ValueError(f"options tab name {t!r} is not letters, digits, _ or -")
        if f"options.tab.{t}" not in pts:
            raise ValueError(f"point options.tab.{t} missing from m5-menu.json")
    return m


def load_menu(path):
    with open(path) as f:
        return check_menu(json.load(f))


def seed_menu(path, force=False):
    if os.path.exists(path) and not force:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(SEED, f, indent=1)
        f.write("\n")
    return True


def render(text, menu, env=None, name="script"):
    env = dict(env or {})
    lines = [l.strip() for l in text.splitlines()]
    out, i = [], 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("%each "):
            var = l.split()[1]
            if var != "tab":
                raise ValueError(f"{name}: %each {var}: only tab is known")
            tabs = menu.get("options", {}).get("tabs", [])
            if not tabs:
                raise ValueError(f"{name}: m5-menu.json has no options.tabs (Q3 not recorded yet)")
            j = next((k for k in range(i + 1, len(lines)) if lines[k] == "%end"), None)
            if j is None:
                raise ValueError(f"{name}: %each without %end")
            for t in tabs:
                out += [x.replace("$tab", t) for x in lines[i + 1:j]]
            i = j + 1
            continue
        out.append(l)
        i += 1

    def var(m):
        if m[1] not in env:
            raise ValueError(f"{name}: unknown variable ${m[1]}")
        return env[m[1]]

    res = []
    for l in "\n".join(out).replace("\r", "").split("\n"):
        l = re.sub(r"\$([A-Za-z_]\w*)", var, l).strip()
        for l in l.split("\n"):
            l = l.strip()
            if not l or l.startswith("#"):
                continue
            if l.startswith("%"):
                raise ValueError(f"{name}: unknown directive '{l}'")
            if l.startswith("move @"):
                key = l[6:].strip()
                p = menu["points"].get(key)
                if p is None:
                    raise ValueError(f"{name}: '{l}' names @{key}, which is not in m5-menu.json points")
                l = f"move {p[0]} {p[1]}"
            elif "@" in l and not l.startswith("text "):
                raise ValueError(f"{name}: '{l}': @name is only allowed after move")
            res.append(l)
    return "\n".join(res) + "\n"


def render_file(name, menu, env=None):
    with open(os.path.join(SCRIPTS, name + ".txt")) as f:
        return render(f.read(), menu, env, name)


def parse_script(text):
    cmds = []
    for n, raw in enumerate(text.splitlines(), 1):
        l = raw.strip()
        if not l or l.startswith("#"):
            continue
        w = l.split(None, 1)
        op, arg = w[0], (w[1].strip() if len(w) > 1 else "")
        a = arg.split()

        def bad(msg):
            raise ScriptError(f"line {n}: {msg}: {l}")
        if op == "wait":
            if a != ["menu"]:
                bad("only 'wait menu' exists")
        elif op == "sleep":
            if len(a) != 1 or not re.fullmatch(r"\d+(\.\d+)?", a[0]):
                bad("sleep needs non-negative seconds")
        elif op == "move":
            if len(a) != 2 or not all(re.fullmatch(r"\d+", x) for x in a) or int(a[0]) >= CLIENT[0] or int(a[1]) >= CLIENT[1]:
                bad(f"move needs X Y inside {CLIENT[0]}x{CLIENT[1]}")
        elif op in ("down", "up", "click"):
            if len(a) != 1 or a[0] not in BUTTON_NAMES:
                bad(f"{op} needs one of {'/'.join(BUTTON_NAMES)}")
        elif op == "wheel":
            if len(a) != 1 or not re.fullmatch(r"-?\d+", a[0]):
                bad("wheel needs an integer")
        elif op in ("key", "keydown", "keyup", "chord"):
            if len(a) != 1 or not re.fullmatch(r"[a-z0-9]+", a[0]):
                bad(f"{op} needs one lowercase SDL key name")
        elif op == "text":
            if not re.fullmatch(r'"([^"\\]|\\["\\])*"', arg):
                bad('text needs one double-quoted string')
        elif op == "focus":
            if a not in (["lost"], ["gained"]):
                bad("focus needs lost or gained")
        elif op == "capture":
            if len(a) != 1 or not RE_NAME.fullmatch(a[0]):
                bad("capture needs one NAME of letters, digits, _ or -")
        else:
            bad("unknown command")
        cmds.append((n, op, arg))
    return cmds


def script_seconds(cmds):
    return sum(float(a) for _, op, a in cmds if op == "sleep")


def lcg(s):
    return (s * 1103515245 + 12345) & 0x7FFFFFFF


def gen_soak(menu, seconds=60):
    cw, ch = CLIENT
    ex, ey = menu["points"]["empty"]
    rects = list(menu["rects"].values())
    gap = f"{STEP_MS / 1000:g}"
    lines, s = ["wait menu", "sleep 2"], 5
    for i in range(seconds * 1000 // STEP_MS):
        if i % CLICK_EVERY == CLICK_EVERY - 1:
            while True:
                s = lcg(s)
                x = min(cw - 1, max(0, ex + s % 41 - 20))
                s = lcg(s)
                y = min(ch - 1, max(0, ey + s % 41 - 20))
                if not any(rx - 10 <= x < rx + rw + 10 and ry - 10 <= y < ry + rh + 10 for rx, ry, rw, rh in rects):
                    break
            lines += [f"move {x} {y}", f"sleep {gap}", "click left"]
        else:
            s = lcg(s)
            x = s % cw
            s = lcg(s)
            lines.append(f"move {x} {s % ch}")
        lines.append(f"sleep {gap}")
    return "\n".join(lines) + "\n"


def parse_log(log):
    out = []
    for i, l in enumerate(log.splitlines()):
        m = RE_INPUT.match(l)
        if m:
            out.append(("input", i, m[1], int(m[2], 16), int(m[3], 16)))
            continue
        m = RE_SCRIPT.match(l)
        if m:
            out.append(("script", i, int(m[1]), m[2]))
            continue
        if RE_DONE.match(l):
            out.append(("done", i))
            continue
        m = RE_CURSOR.match(l)
        if m:
            t = m[1]
            w = RE_WARP.match(t)
            kind = "hidden" if t == "hidden" else "warp" if w and w[1] else "set" if w else "shape"
            out.append(("cursor", i, kind, t))
    return out


def counters(log):
    m = list(RE_COUNTERS.finditer(log))
    return tuple(int(x) for x in m[-1].groups()) if m else None


def guest_lines(log):
    return [l[7:].rstrip() for l in log.splitlines() if l.startswith("guest: ")]


def scan_of(lp):
    return (lp >> 16) & 0xFF


def ext_of(lp):
    return (lp >> 24) & 1


def mean_abs(a, b, rows):
    (w, h, ra), (w2, h2, rb) = a, b
    if (w, h) != (w2, h2):
        raise ValueError(f"sizes differ: {w}x{h} vs {w2}x{h2}")
    n = tot = 0
    for y in range(h):
        for m in re.finditer(rb"\x01+", bytes(rows[y])):
            s, e = m.span()
            n += e - s
            pa, pb = ra[y][3 * s:3 * e], rb[y][3 * s:3 * e]
            if pa != pb:
                tot += sum(abs(p - q) for p, q in zip(pa, pb))
    if not n:
        raise ValueError("empty region")
    return tot / (3 * n)


def rect_rows(w, h, rect):
    x, y, rw, rh = rect
    rows = [bytearray(w) for _ in range(h)]
    for yy in range(y, y + rh):
        rows[yy][x:x + rw] = b"\x01" * rw
    return rows


def minus_rect(rows, rect):
    x, y, rw, rh = rect
    out = [bytearray(r) for r in rows]
    for yy in range(y, min(y + rh, len(out))):
        out[yy][x:x + rw] = bytes(rw)
    return out


def check_hover(r, idle, hover, away, rect, mask_rows=None):
    w, h, _ = idle
    inr = rect_rows(w, h, rect)
    dh, da = mean_abs(hover, idle, inr), mean_abs(away, idle, inr)
    r.check(f"hover differs from idle by {dh:.2f}/255 inside New Game (need >= {HOVER_MIN:g})", dh >= HOVER_MIN, dh)
    r.check(f"away differs from idle by {da:.2f}/255 inside New Game (need <= {QUIET_MAX:g})", da <= QUIET_MAX, da)
    if mask_rows is not None:
        out = minus_rect(mask_rows, rect)
        for label, img in (("hover", hover), ("away", away)):
            d = mean_abs(img, idle, out)
            r.check(f"{label} differs from idle by {d:.2f}/255 outside New Game inside the mask (need <= {QUIET_MAX:g})",
                    d <= QUIET_MAX, d)


def check_chords(r, log):
    ev = parse_log(log)
    sc = [e for e in ev if e[0] == "script"]
    ch = [k for k, e in enumerate(sc) if e[3].split()[0] == "chord"]
    r.check(f"{CHORDS} chord script lines", len(ch) == CHORDS, len(ch))
    if ch:
        lo = sc[ch[0]][1]
        hi = next((e[1] for e in sc[ch[-1] + 1:] if e[3].split()[0] != "sleep"), 1 << 60)
        leaks = [e[2:] for e in ev if e[0] == "input" and lo < e[1] < hi and e[2] in KEYMSG]
        r.check("no rt: input key or character line between the chord script lines", not leaks, leaks[:3])
    c = counters(log)
    r.check(f"exit counters line has chords {CHORDS}", c is not None and c[5] == CHORDS, c)
    g = guest_lines(log)
    r.check("guest prints exactly one M5_AFTER line", g.count("M5_AFTER") == 1, g.count("M5_AFTER"))


def check_focus(r, log):
    ev = parse_log(log)
    sc = {e[3]: e[1] for e in ev if e[0] == "script" and e[3].startswith("focus ")}
    lost, gained = sc.get("focus lost"), sc.get("focus gained")
    r.check("log has the focus lost and focus gained script lines", lost is not None and gained is not None, sc)
    if lost is None or gained is None:
        return
    inp = [e for e in ev if e[0] == "input"]
    a0 = next((e for e in inp if e[1] > lost and e[2] == "WM_ACTIVATEAPP" and e[3] == 0), None)
    r.check("WM_ACTIVATEAPP wp 0x0 follows the focus lost line", a0 is not None)
    if a0 is None:
        return
    a1 = next((e for e in inp if e[1] > a0[1] and e[2] == "WM_ACTIVATEAPP" and e[3] == 1), None)
    r.check("WM_ACTIVATEAPP wp 0x1 follows it", a1 is not None)
    ku = [e for e in inp if lost < e[1] < a0[1] and e[2] == "WM_KEYUP" and scan_of(e[4]) == 0x2A]
    bu = [e for e in inp if lost < e[1] < a0[1] and e[2] == "WM_LBUTTONUP"]
    r.check("WM_KEYUP for scan 0x2A comes before WM_ACTIVATEAPP wp 0x0", bool(ku))
    r.check("WM_LBUTTONUP comes before WM_ACTIVATEAPP wp 0x0", bool(bu))
    if a1 is not None:
        mid = [e[2:] for e in inp if a0[1] < e[1] < a1[1] and e[2] != "WM_ACTIVATE"]
        r.check("only WM_ACTIVATE between WM_ACTIVATEAPP wp 0x0 and wp 0x1", not mid, mid[:3])


def check_console(r, log):
    g = guest_lines(log)
    abc = g.index("M5_ABC") if "M5_ABC" in g else None
    xz = g.index("M5_XZ") if "M5_XZ" in g else None
    r.check("guest prints M5_ABC and then M5_XZ", abc is not None and xz is not None and abc < xz, (abc, xz))
    bad = [x for x in g if re.search(r"\bM5_(AC|XY)\b", x)]
    r.check("guest prints neither M5_AC nor M5_XY", not bad, bad[:2])
    left = [e for e in parse_log(log) if e[0] == "input" and e[2] in ("WM_KEYDOWN", "WM_SYSKEYDOWN") and scan_of(e[4]) == 0x4B]
    r.check("Left key-down line has bit 24 set", bool(left) and all(ext_of(e[4]) for e in left), [hex(e[4]) for e in left])


def check_dinput(r, rows):
    d = sorted(x[1].split("!", 1)[1] for x in rows if x[1].lower().startswith("dinput8.dll!"))
    r.check(f"dinput8 first-call rows are exactly {DINPUT_ROWS}", d == sorted(DINPUT_ROWS), d)
    r.check("no dinput8 row of kind stub", not any(x[1].lower().startswith("dinput8.dll!") and x[2] == "stub" for x in rows))


def check_cursor(r, log):
    cur = [e for e in parse_log(log) if e[0] == "cursor"]
    shapes = [e for e in cur if e[2] in ("shape", "hidden")]
    s11 = re.search(r"^rt: stage 11 ", log, re.M)
    last = shapes[-1][3] if shapes else None
    r.check(f"rt: cursor shape or hidden line present (last: {last})", bool(shapes), len(cur))
    if shapes and s11:
        after = [e for e in shapes if e[1] > log[:s11.start()].count("\n")]
        print(f"  cursor lines after stage 11: {len(after)}; last {after[-1][3] if after else 'none'}")
    for e in cur:
        if e[2] in ("warp", "set"):
            print(f"  {e[3]}")


def check_script(r, log, cmds, need_done=True, min_lines=1):
    ev = parse_log(log)
    got = [e[2] for e in ev if e[0] == "script"]
    want = [n for n, _, _ in cmds]
    ok = got == want if need_done else got == want[:len(got)] and len(got) >= min_lines
    r.check(f"one rt: script line per command ({len(got)} of {len(want)})", ok, got[:5])
    if need_done:
        r.check("rt: script done", any(e[0] == "done" for e in ev))


def check_soak(r, log, cmds):
    c = counters(log)
    r.check(f"exit counters line has moves >= {SOAK_MOVES} and buttons > 0", c is not None and c[0] >= SOAK_MOVES and c[1] > 0, c)


def check_newgame(r, log, t=None, rc=None):
    ev = parse_log(log)
    sc = [e for e in ev if e[0] == "script"]
    click = next((e for e in reversed(sc) if e[3].startswith("click")), None)
    cap = next((e for e in sc if e[3] == "capture chargen"), None)
    m = MARKER.search(log)
    mi = log[:m.start()].count("\n") if m else None
    r.check("chargen marker line present", m is not None)
    r.check("marker comes after the click and before the capture chargen line",
            bool(m and click and cap and click[1] < mi < cap[1]), (click and click[1], mi, cap and cap[1]))
    if t is not None and cap is not None:
        r.check(f"process still running 10 s after the capture ({t['exit'] - t['cap']:.1f} s)",
                t["exit"] - t["cap"] >= ALIVE_SECONDS and rc == 0)


def check_quit(r, log, dt):
    r.check(f"exit within {QUIT_SECONDS} s of the click ({'?' if dt is None else format(dt, '.1f')} s)",
            dt is not None and 0 <= dt <= QUIT_SECONDS, dt)
    r.check("LauncherMain returned 0 or ExitProcess(0)", re.search(r"LauncherMain returned 0|ExitProcess\(0\)", log) is not None)
    r.check("no forced quit line", "forced quit" not in log)


def common(r, res, timeout, cmds=None, need_done=True, min_lines=1):
    timeout_note(r, res.rc, res.log, timeout)
    check_stages(r, res.log)
    check_faults(r, res.log, res.crashes, res.rc)
    check_dinput(r, res.rows)
    r.check("no message queue full line", not RE_QUEUE_FULL.search(res.log))
    if cmds is not None:
        check_script(r, res.log, cmds, need_done, min_lines)


def run_timed(exe, overlay, extra, timeout):
    before = set(glob.glob(os.path.join(m4.CRASHES, "bloodlines-native*")))
    p = subprocess.Popen([exe, "--run", "--overlay", overlay, *extra], stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE, text=True, errors="replace")
    lines = []
    t0 = time.time()

    def pump():
        for l in p.stderr:
            lines.append((time.time() - t0, l))
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        rc = None
    texit = time.time() - t0
    th.join(5)
    crashes = sorted(set(glob.glob(os.path.join(m4.CRASHES, "bloodlines-native*"))) - before)
    tsv = sorted(glob.glob(os.path.join(overlay, "logs", "first-calls-*.tsv")))
    rows = [l.rstrip("\n").split("\t") for l in open(tsv[-1])][1:] if tsv else []
    return SimpleNamespace(rc=rc, log="".join(l for _, l in lines), lines=lines, exit=texit, crashes=crashes, rows=rows)


def line_time(res, pred):
    for t, l in res.lines:
        if pred(l):
            return t
    return None


def close_after_for(a, cmds):
    return a.close_after or int(math.ceil(script_seconds(cmds))) + a.margin


def prepare(o, name, text):
    cmds = parse_script(text)
    os.makedirs(o, exist_ok=True)
    path = os.path.join(o, name + ".txt")
    with open(path, "w") as f:
        f.write(text)
    return path, cmds


def live(a, o, name, text, display=None, tail=(), close_after=None):
    path, cmds = prepare(o, name, text)
    ca = close_after or close_after_for(a, cmds)
    extra = ["--close-after", str(ca), "--input-script", path]
    if display is not None:
        extra += ["--display", str(display)]
    if tail:
        extra += ["--", *tail]
    to = a.timeout or ca + 120
    res = run_timed(a.exe, o, extra, to)
    return res, cmds, to


def shot_of(o, name):
    fs = [f for f in glob.glob(os.path.join(o, "shots", f"{name}-*.png")) if re.match(rf"{re.escape(name)}-\d", os.path.basename(f))]
    return max(fs, key=os.path.getmtime) if fs else None


def refpair(a, name):
    return os.path.join(a.refdir, name + ".png"), os.path.join(a.refdir, name + "-mask.json")


def screen(r, a, o, shot, ref=None):
    p = shot_of(o, shot)
    r.check(f"shots/{shot}-*.png written", p is not None, o)
    if not p:
        return None
    img = m4.check_png(r, open(p, "rb").read(), a.size, a.min_colours)
    if img and ref:
        png, mask = refpair(a, ref)
        if os.path.exists(png) and os.path.exists(mask):
            m4.cmp_files(r, p, png, mask, a)
        else:
            print(f"  no reference {png} (first pass: the maintainer approves the capture)")
    return img


def differs(r, a, img, label, ref, versus):
    png, mask = refpair(a, ref)
    vpng, vmask = refpair(a, versus)
    if not (os.path.exists(vpng) and os.path.exists(vmask)):
        print(f"  no reference {vpng}: {label} difference check not run")
        return
    mp = mask if os.path.exists(mask) else vmask
    ok, d = m4.compare(img, read_png(open(vpng, "rb").read()), json.load(open(mp)), DEF["tol"])
    r.check(f"{label} compares with {versus}", "error" not in d, d)
    if "error" in d:
        return
    f = 1 - d["within_tol"]
    r.check(f"{label} differs from {versus} in {f * 100:.2f}% of masked pixels by more than {DEF['tol']} (need >= {DIFFER_PCT * 100:g}%)",
            f >= DIFFER_PCT, f)


def hover_mask(a, w, h):
    cands = [a.mask] if a.mask else [os.path.join(a.refdir, "m5-hover-mask.json"), os.path.join(a.refdir, "m4-menu-mask.json")]
    for c in cands:
        if c and os.path.exists(c):
            return load_mask(json.load(open(c)), w, h)
    return None


def refused_of(res, display):
    return m4.RE_REFUSED.search(res.log) if display is not None else None


def hover_run(r, a, o, menu, display=None):
    res, cmds, to = live(a, o, "hover", render_file("hover", menu), display)
    ref = refused_of(res, display)
    if ref:
        return ref
    common(r, res, to, cmds)
    idle, hover, away = (screen(r, a, o, n) for n in ("idle", "hover", "away"))
    if idle and hover and away:
        mrows = hover_mask(a, idle[0], idle[1])
        r.check("static-UI mask found (--mask, m5-hover-mask.json or m4-menu-mask.json in the reference folder)", mrows is not None)
        check_hover(r, idle, hover, away, menu["rects"]["newgame"], mrows)
    check_cursor(r, res.log)
    return None


def console_run(r, a, o, menu, display=None):
    res, cmds, to = live(a, o, "console", render_file("console", menu), display)
    ref = refused_of(res, display)
    if ref:
        return ref
    common(r, res, to, cmds)
    check_console(r, res.log)
    return None


def base_of(a, tag):
    return a.overlay or tempfile.mkdtemp(prefix=f"m5-{tag}-")


def cmd_hover(a):
    if not need_exe(a):
        return 1
    r = Rep()
    ref = hover_run(r, a, base_of(a, "hover"), load_menu(a.menu), a.display)
    r.check("the display exists", ref is None, ref and ref[0])
    return 0 if r.ok else 1


def cmd_console(a):
    if not need_exe(a):
        return 1
    r = Rep()
    ref = console_run(r, a, base_of(a, "console"), load_menu(a.menu), a.display)
    r.check("the display exists", ref is None, ref and ref[0])
    return 0 if r.ok else 1


def cmd_buttons(a):
    if not need_exe(a):
        return 1
    r, menu, base = Rep(), load_menu(a.menu), base_of(a, "buttons")
    for b in a.button:
        print(f"-- {b}")
        back = "key escape" if a.back == "escape" else f"move @{b}.cancel\nsleep 0.5\nclick left"
        res, cmds, to = live(a, os.path.join(base, b), b, render_file("buttons", menu, {"button": b, "back": back}))
        common(r, res, to, cmds)
        img = screen(r, a, os.path.join(base, b), f"{b}-screen", f"m5-{b}")
        if img:
            differs(r, a, img, f"{b} screen", f"m5-{b}", "m4-menu")
        screen(r, a, os.path.join(base, b), f"{b}-back", "m4-menu")
    return 0 if r.ok else 1


def options_shots(menu):
    return ([("options-open", "m5-options")] + [(f"options-{t}", f"m5-options-{t.lower()}") for t in menu["options"]["tabs"]] +
            [("options-toggled", "m5-options-toggled"), ("options-after", "m4-menu"), ("options-reopened", "m5-options-gameplay")])


OPTIONS_NEEDS = ["options.tabs (must include Gameplay)", "options.tab.<Tab> for every tab", "options.checkbox", "options.cancel"]


def need_options(menu):
    pts = menu["points"]
    tabs = menu.get("options", {}).get("tabs", [])
    miss = [k for k in ("options.tab.Gameplay", "options.checkbox", "options.cancel") if k not in pts]
    if "Gameplay" not in tabs:
        miss.insert(0, "options.tabs entry Gameplay")
    if not tabs:
        miss.insert(0, "options.tabs")
    if miss:
        raise ValueError("m5-menu.json is missing for the options mode: " + ", ".join(miss) + " (P3 records them from the first Options captures)")


def cmd_options(a):
    if not need_exe(a):
        return 1
    r, menu, o = Rep(), load_menu(a.menu), base_of(a, "options")
    need_options(menu)
    res, cmds, to = live(a, o, "options", render_file("options", menu))
    common(r, res, to, cmds)
    for shot, ref in options_shots(menu):
        screen(r, a, o, shot, ref)
    return 0 if r.ok else 1


def cmd_newgame(a):
    if not need_exe(a):
        return 1
    r, menu, o = Rep(), load_menu(a.menu), base_of(a, "newgame")
    res, cmds, to = live(a, o, "newgame", render_file("newgame", menu), tail=("+developer", "4"))
    common(r, res, to, cmds)
    tc = line_time(res, lambda l: RE_SCRIPT.match(l) and RE_SCRIPT.match(l)[2] == "capture chargen")
    check_newgame(r, res.log, {"exit": res.exit, "cap": tc} if tc is not None else None, res.rc)
    screen(r, a, o, "chargen", "m5-chargen")
    return 0 if r.ok else 1


def cmd_quit(a):
    if not need_exe(a):
        return 1
    r, menu, o = Rep(), load_menu(a.menu), base_of(a, "quit")
    res, cmds, to = live(a, o, "quit", render_file("quit", menu))
    common(r, res, to, cmds, need_done=False)
    tc = None
    for t, l in res.lines:
        m = RE_SCRIPT.match(l)
        if m and m[2].startswith("click"):
            tc = t
    check_quit(r, res.log, None if tc is None else res.exit - tc)
    return 0 if r.ok else 1


def cmd_chords(a):
    if not need_exe(a):
        return 1
    r, menu, o = Rep(), load_menu(a.menu), base_of(a, "chords")
    res, cmds, to = live(a, o, "chords", render_file("chords", menu))
    common(r, res, to, cmds)
    check_chords(r, res.log)
    return 0 if r.ok else 1


def cmd_focus(a):
    if not need_exe(a):
        return 1
    r, menu, o = Rep(), load_menu(a.menu), base_of(a, "focus")
    res, cmds, to = live(a, o, "focus", render_file("focus", menu))
    common(r, res, to, cmds)
    check_focus(r, res.log)
    return 0 if r.ok else 1


def cmd_displays(a):
    if not need_exe(a):
        return 1
    r, menu, base = Rep(), load_menu(a.menu), base_of(a, "displays")
    ran = 0
    for n in a.displays:
        print(f"-- display {n}")
        o = os.path.join(base, f"d{n}")
        ref = hover_run(r, a, o, menu, n)
        if ref:
            if n == 0:
                r.check("display 0 is not refused", False, ref[0])
            else:
                print(f"  display {n} is not connected ({ref[0].strip()}): built-in results only, AC10 stays open")
            continue
        ran += 1
        nl = newest_log(o)
        log = open(nl, errors="replace").read() if nl else ""
        info = [l for l in log.splitlines() if re.match(r"rt: (window|display) ", l)]
        d = [x for x in m4.RE_DISPLAY.findall(log) if x[0] == str(n)]
        r.check(f"display {n}: rt: display line {d[:1]}", bool(d), "missing")
        for l in info:
            print(f"  {l}")
        console_run(r, a, os.path.join(base, f"d{n}-console"), menu, n)
    r.check(f"at least one of the displays {a.displays} ran", ran > 0)
    return 0 if r.ok else 1


def newest_log(o):
    return m4.newest(os.path.join(o, "logs", "run-*.log"))


def cmd_soak(a):
    if not need_exe(a):
        return 1
    r, menu, base = Rep(), load_menu(a.menu), base_of(a, "soak")
    text = gen_soak(menu, a.seconds)
    for i in range(a.n):
        print(f"-- run {i + 1}")
        res, cmds, to = live(a, os.path.join(base, f"run{i + 1}"), "soak", text, close_after=a.close_after or SOAK_CLOSE)
        common(r, res, to, cmds, need_done=False, min_lines=len(cmds) - SOAK_TAIL)
        check_soak(r, res.log, cmds)
        check_stats(r, res.log, a.p99, a.pump)
    return 0 if r.ok else 1


def cmd_cases(a):
    if not need_exe(a):
        return 1
    r = Rep()
    try:
        p = subprocess.run([a.exe, "--input-selftest"], capture_output=True, text=True, timeout=a.timeout or 120)
    except subprocess.TimeoutExpired:
        r.check("--input-selftest finished", False, "timed out")
        return 1
    res = [l for l in (p.stdout + p.stderr).splitlines() if re.match(r"(PASS|FAIL) ", l)]
    bad = [l for l in res if l.startswith("FAIL")]
    for l in res:
        print(l)
    r.check(f"{len(res) - len(bad)} PASS lines (need >= {a.min_cases}), 0 FAIL", not bad and len(res) >= a.min_cases, bad)
    r.check(f"exit 0 (got {p.returncode})", p.returncode == 0)
    return 0 if r.ok else 1


def cmd_render(a):
    menu = load_menu(a.menu)
    env = {"button": a.button, "back": "key escape"}
    sys.stdout.write(gen_soak(menu, a.seconds) if a.template == "soak" else render_file(a.template, menu, env))
    return 0


def cmd_seed(a):
    made = seed_menu(a.menu, a.force)
    print(f"{'wrote' if made else 'kept'} {a.menu}")
    print("P3 must still add for the options mode: " + "; ".join(OPTIONS_NEEDS))
    return 0


def cmd_check(a):
    r = Rep()
    log = open(a.log, errors="replace").read()
    {"chords": check_chords, "focus": check_focus, "console": check_console, "cursor": check_cursor}[a.what](r, log)
    return 0 if r.ok else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="m5.py")
    sub = ap.add_subparsers(dest="mode", required=True)

    def add(name, fn, **opts):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        for k, v in opts.items():
            p.add_argument("--" + k.replace("_", "-"), **v)
        return p

    size = {"type": lambda s: tuple(int(x) for x in s.split("x")), "default": DEF["size"]}
    tol = {"block": {"type": int, "default": DEF["block"]}, "block_frac": {"type": float, "default": DEF["block_frac"]},
           "tol": {"type": int, "default": DEF["tol"]}, "pct": {"type": float, "default": DEF["pct"]},
           "mad": {"type": float, "default": DEF["mad"]}, "luma": {"type": float, "default": DEF["luma"]}}
    live_opts = dict(exe={"default": X_DEFAULT}, overlay={}, timeout={"type": int}, menu={"default": MENU_DEFAULT},
                     refdir={"default": REFDIR}, close_after={"type": int}, margin={"type": int, "default": 60},
                     mask={}, size=size, min_colours={"type": int, "default": 200}, **tol)
    disp = {"type": int}
    add("selftest", lambda a: selftest())
    add("cases", cmd_cases, exe={"default": X_DEFAULT}, timeout={"type": int}, min_cases={"type": int, "default": 20})
    add("hover", cmd_hover, display=disp, **live_opts)
    add("console", cmd_console, display=disp, **live_opts)
    add("buttons", cmd_buttons, button={"nargs": "+", "default": ["loadgame", "options"]},
        back={"choices": ["escape", "cancel"], "default": "escape"}, **live_opts)
    add("options", cmd_options, **live_opts)
    add("newgame", cmd_newgame, **live_opts)
    add("quit", cmd_quit, **live_opts)
    add("chords", cmd_chords, **live_opts)
    add("focus", cmd_focus, **live_opts)
    add("displays", cmd_displays, displays={"type": int, "nargs": "+", "default": [0]}, **live_opts)
    add("soak", cmd_soak, n={"type": int, "default": 3}, seconds={"type": int, "default": 60},
        p99={"type": float, "default": DEF["p99"]}, pump={"type": int, "default": DEF["pump"]}, **live_opts)
    add("render", cmd_render, template={"required": True}, menu={"default": MENU_DEFAULT}, button={"default": "loadgame"},
        seconds={"type": int, "default": 60})
    add("seed", cmd_seed, menu={"default": MENU_DEFAULT}).add_argument("--force", action="store_true")
    add("check", cmd_check, what={"choices": ["chords", "focus", "console", "cursor"], "required": True},
        log={"required": True})
    return ap


def input_log(n_chords=3, leak=None, chords_counter=None, after=1):
    out = ["rt: stage 11 menu"]
    k = 10
    for i, c in enumerate(["chord a", "chord tab", "chord grave"][:n_chords]):
        out.append(f"rt: script {k} {c}")
        if leak == i:
            out.append("rt: input WM_KEYDOWN wp 0x41 lp 0x1E0001")
        out.append(f"rt: script {k + 1} sleep 0.5")
        if leak == "tail" and i == n_chords - 1:
            out.append("rt: input WM_CHAR wp 0x41 lp 0x1E0001")
        k += 2
    out += [f'rt: script {k} text "echo M5_AFTER"', "rt: input WM_CHAR wp 0x61 lp 0x1E0001"]
    out += ["guest: M5_AFTER"] * after
    out.append("rt: input moves 4 buttons 0 keys 9 chars 12 dropped-chars 0 chords %d warps 1" %
               (CHORDS if chords_counter is None else chords_counter))
    return "\n".join(out) + "\n"


def focus_log(late_up=False, leak=False, stray=False):
    ku = "rt: input WM_KEYUP wp 0xA0 lp 0xC02A0001"
    bu = "rt: input WM_LBUTTONUP wp 0x0 lp 0x1B80258"
    a0 = "rt: input WM_ACTIVATEAPP wp 0x0 lp 0x0"
    out = ["rt: script 5 keydown lshift", "rt: input WM_KEYDOWN wp 0xA0 lp 0x2A0001", "rt: script 6 down left",
           "rt: input WM_LBUTTONDOWN wp 0x1 lp 0x1B80258", "rt: script 7 focus lost"]
    out += [bu, a0] if late_up else [ku, bu, a0]
    out += ["rt: input WM_ACTIVATE wp 0x0 lp 0x0", "rt: script 8 move 100 100", "rt: script 9 key a"]
    if leak:
        out.append("rt: input WM_KEYDOWN wp 0x41 lp 0x1E0001")
    if stray:
        out.append("rt: input WM_SIZE wp 0x0 lp 0x0")
    out += ["rt: script 10 focus gained", "rt: input WM_ACTIVATEAPP wp 0x1 lp 0x0", "rt: input WM_ACTIVATE wp 0x1 lp 0x0"]
    if late_up:
        out.append(ku)
    return "\n".join(out) + "\n"


def console_log(left_ext=True, xz=True):
    lp = 0x014B0001 if left_ext else 0x004B0001
    out = ["rt: input WM_KEYDOWN wp 0x25 lp 0x%X" % lp, "guest: ] echo M5_ABC", "guest: M5_ABC",
           "guest: M5_XZ" if xz else "guest: M5_XY"]
    return "\n".join(out) + "\n"


def dinput_rows(extra=()):
    rows = [["1", f"dinput8.dll!{n}", "shim", "engine", "f", "1"] for n in DINPUT_ROWS]
    rows += [["1", f"dinput8.dll!{n}", "stub", "engine", "f", "1"] for n in extra]
    return rows


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
        return r.ok

    def raises(exc, fn, *args, **kw):
        try:
            fn(*args, **kw)
        except exc as e:
            return str(e)
        except Exception:
            return None
        return None

    menu = json.loads(json.dumps(SEED))
    menu["options"] = {"tabs": ["Keyboard", "Gameplay"]}
    for t in menu["options"]["tabs"]:
        menu["points"][f"options.tab.{t}"] = [100, 200]
    menu["points"]["options.checkbox"] = [300, 400]
    menu["points"]["options.cancel"] = [500, 600]
    menu["points"]["loadgame.cancel"] = [510, 610]

    for k in ("newgame", "loadgame", "options", "quit"):
        expect(f"menu without the {k} rect is refused", raises(ValueError, check_menu, {**SEED, "rects": {x: v for x, v in SEED["rects"].items() if x != k}}))
        expect(f"menu without the {k} point is refused", raises(ValueError, check_menu, {**SEED, "points": {x: v for x, v in SEED["points"].items() if x != k}}))
    expect("need_options names every missing key", all(k in (raises(ValueError, need_options, SEED) or "") for k in ("options.tabs", "options.checkbox", "options.cancel")))
    expect("need_options passes on a full menu", need_options(menu) is None)
    expect("seed menu validates", check_menu(json.loads(json.dumps(SEED))) is not None)
    expect("menu with options validates", check_menu(menu) is not None)
    expect("menu without quit point is refused", raises(ValueError, check_menu, {**SEED, "points": {k: v for k, v in SEED["points"].items() if k != "quit"}}))
    expect("menu with a point outside the client is refused", raises(ValueError, check_menu, {**SEED, "points": {**SEED["points"], "empty": [1280, 5]}}))
    expect("menu with a rect outside the client is refused", raises(ValueError, check_menu, {**SEED, "rects": {**SEED["rects"], "quit": [1200, 1000, 100, 100]}}))
    expect("menu with a float point is refused", raises(ValueError, check_menu, {**SEED, "points": {**SEED["points"], "empty": [1.5, 5]}}))
    expect("menu tab without its point is refused", raises(ValueError, check_menu, {**SEED, "options": {"tabs": ["Audio"]}}))
    expect("seed centres are the Q5 values", [SEED["points"][k] for k in ("newgame", "loadgame", "options", "quit", "empty")] ==
           [[617, 438], [617, 533], [617, 634], [619, 729], [1180, 950]])
    expect("seed rects contain their centres", all(
        r[0] <= SEED["points"][k][0] < r[0] + r[2] and r[1] <= SEED["points"][k][1] < r[1] + r[3] for k, r in SEED["rects"].items()))
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "sub", "m5-menu.json")
        expect("seed writes the file", seed_menu(p) and load_menu(p)["points"]["quit"] == [619, 729])
        open(p, "w").write(json.dumps({**SEED, "points": {**SEED["points"], "quit": [1, 2]}}))
        expect("seed keeps an existing file", not seed_menu(p) and load_menu(p)["points"]["quit"] == [1, 2])
        expect("seed --force replaces it", seed_menu(p, True) and load_menu(p)["points"]["quit"] == [619, 729])

    names = sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(SCRIPTS, "*.txt")))
    expect(f"script files are {names}", names == ["buttons", "chords", "console", "focus", "hover", "newgame", "options", "quit"])
    for nm in names:
        env = {"button": "loadgame", "back": "key escape"}
        txt = render_file(nm, menu, env)
        cmds = parse_script(txt)
        expect(f"template {nm} renders and parses", len(cmds) > 3 and cmds[0][1:] == ("wait", "menu")
               and [c[0] for c in cmds] == list(range(1, len(cmds) + 1)) and "@" not in txt.replace('"', "") and "$" not in txt)
    expect("hover renders New Game centre", "move 617 438\n" in render_file("hover", menu) and "move 1180 950\n" in render_file("hover", menu))
    expect("quit renders Quit centre", "move 619 729\n" in render_file("quit", menu))
    expect("buttons with cancel renders the cancel point",
           "move 510 610\n" in render_file("buttons", menu, {"button": "loadgame", "back": "move @loadgame.cancel\nclick left"}))
    ot = render_file("options", menu)
    expect("options expands each tab", "capture options-Keyboard" in ot and "capture options-Gameplay" in ot and "move 100 200" in ot and "%" not in ot)
    ot_lines = ot.splitlines()
    k = ot_lines.index("capture options-after")
    expect("after the options-after capture the script moves to Options, then the Gameplay tab, then captures",
           ot_lines[k + 1] == "move 617 634" and ot_lines[k + 4] == "sleep 3" and ot_lines[k + 5] == "move 100 200" and "capture options-reopened" in ot_lines[k + 6:])
    expect("options_shots compares the reopened capture with the untoggled Gameplay reference",
           ("options-reopened", "m5-options-gameplay") in options_shots(menu) and ("options-toggled", "m5-options-toggled") in options_shots(menu)
           and ("options-after", "m4-menu") in options_shots(menu))
    expect("options without recorded tabs is refused", raises(ValueError, render_file, "options", SEED))
    expect("a template naming a point missing from m5-menu.json is refused",
           "quit" in (raises(ValueError, render, "move @quit\n", {"points": {"newgame": [1, 2]}}) or ""))
    expect("a missing point makes render raise ValueError", raises(ValueError, render, "move @quit\n", {"points": {"newgame": [1, 2]}}) is not None)
    expect("the renderer names the missing point and the template",
           "@bogus" in (raises(ValueError, render, "wait menu\nmove @bogus\n", menu, None, "t") or "") and
           "t:" in (raises(ValueError, render, "move @bogus\n", menu, None, "t") or ""))
    expect("an unknown $variable is refused", raises(ValueError, render, "capture $nope\n", menu))
    expect("an @name outside move is refused", raises(ValueError, render, "click @quit\n", menu))
    expect("an @ inside a text string is kept", render('text "a@b"\n', menu) == 'text "a@b"\n')
    expect("an unknown % directive is refused", raises(ValueError, render, "%bogus\n", menu))
    expect("a stray %end is refused", raises(ValueError, render, "%end\n", menu))
    expect("render drops comments and blank lines", render("# x\n\nwait menu\n", menu) == "wait menu\n")

    good = 'wait menu\nsleep 0.5\nmove 0 0\nmove 1279 1023\ndown left\nup x2\nclick middle\nwheel -3\nkey grave\nkeydown lshift\nkeyup lshift\n' \
           'text "a \\"b\\" \\\\"\nchord a\nfocus lost\nfocus gained\ncapture a_b-1\n# c\n\n'
    expect("every command parses", [c[1] for c in parse_script(good)] ==
           "wait sleep move move down up click wheel key keydown keyup text chord focus focus capture".split())
    expect("comments and blanks keep source line numbers", [c[0] for c in parse_script("# a\n\nwait menu\nsleep 1\n")] == [3, 4])
    for bad_line in ("wait console", 'wait "x"', "sleep -1", "sleep x", "move 1280 0", "move 0 1024", "move 1", "click up",
                     "down", "wheel 1.5", "key", "key Grave", "key a b", "text hi", 'text "a" "b"', 'text "a\\n"', "focus", "focus x",
                     "capture", "capture a b", "capture a/b", "jump 1"):
        e = raises(ScriptError, parse_script, "wait menu\n" + bad_line + "\n")
        expect(f"bad line '{bad_line}' is refused with its line number", e and e.startswith("line 2:"))

    soak = gen_soak(menu)
    sc = parse_script(soak)
    moves = [c for c in sc if c[1] == "move"]
    expect(f"soak script parses, has 6000 steps of 10 ms ({len(moves)} moves)", len(moves) >= 6000 and abs(script_seconds(sc) - 62.24) < 0.001)
    clicks = [i for i, c in enumerate(sc) if c[1] == "click"]
    expect("soak has 24 clicks, each after a move and a sleep", len(clicks) == 24 and all(sc[i - 2][1] == "move" and sc[i - 1][1] == "sleep" for i in clicks))
    outside = True
    for i in clicks:
        x, y = map(int, sc[i - 2][2].split())
        outside &= not any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in SEED["rects"].values())
    expect("soak clicks land outside every button rectangle", outside)
    expect("soak is deterministic per seed", gen_soak(menu) == soak and gen_soak(menu, 1) != soak)
    expect("soak is one 'wait menu' first", sc[0][1:] == ("wait", "menu"))
    expect("soak positions stay inside the client", all(int(c[2].split()[0]) < 1280 and int(c[2].split()[1]) < 1024 for c in moves))

    sample = ("rt: input WM_KEYDOWN wp 0x25 lp 0x14B0001\nrt: input host chord\nrt: input unmapped scancode 9\n"
              "rt: script 3 key left\nrt: script done\nrt: cursor hidden\nrt: cursor default\nrt: cursor warp 10,20\n"
              "rt: cursor set 5,6 (no warp)\nrt: input moves 1 buttons 2 keys 3 chars 4 dropped-chars 5 chords 6 warps 7\n"
              "rt: input moves 1 buttons 2 keys 3 chars 4 dropped-chars 5 chords 6 warps 7\nnoise\n")
    pl = parse_log(sample)
    expect("parse_log reads input, script, done and cursor lines",
           [e[0] for e in pl] == ["input", "script", "done", "cursor", "cursor", "cursor", "cursor"])
    expect("parse_log keeps the input fields", pl[0][2:] == ("WM_KEYDOWN", 0x25, 0x14B0001) and scan_of(pl[0][4]) == 0x4B and ext_of(pl[0][4]) == 1)
    expect("parse_log classifies cursor lines", [e[2] for e in pl if e[0] == "cursor"] == ["hidden", "shape", "warp", "set"])
    expect("counters takes the last line and warps", counters(sample) == (1, 2, 3, 4, 5, 6, 7))
    expect("counters without warps is None", counters("rt: input moves 1 buttons 2 keys 3 chars 4 dropped-chars 5 chords 6\n") is None)
    expect("counters without a line is None", counters("noise\n") is None)
    expect("guest_lines strips the prefix", guest_lines("guest: a b\nrt: x\nguest: c\n") == ["a b", "c"])

    img = picture(320, 256)
    sq = (40, 40, 80, 40)
    rows = rect_rows(320, 256, sq)
    expect("rect_rows marks the rectangle", sum(sum(r) for r in rows) == 80 * 40)
    expect("minus_rect clears it", sum(sum(r) for r in minus_rect(rect_rows(320, 256, (0, 0, 320, 256)), sq)) == 320 * 256 - 80 * 40)

    def shift(im, rect, d):
        w, h, rs = im
        rs = [bytearray(r) for r in rs]
        x, y, rw, rh = rect
        for yy in range(y, y + rh):
            for xx in range(x, x + rw):
                for c in range(3):
                    rs[yy][3 * xx + c] = rs[yy][3 * xx + c] + d if rs[yy][3 * xx + c] + d < 256 else rs[yy][3 * xx + c] - d
        return w, h, [bytes(r) for r in rs]
    expect("mean_abs is 0 for equal pictures", mean_abs(img, img, rows) == 0)
    expect("mean_abs of a uniform 7 shift is 7", abs(mean_abs(shift(img, sq, 7), img, rows) - 7) < 1e-9)
    expect("mean_abs outside a shifted rectangle is 0", mean_abs(shift(img, sq, 50), img, minus_rect(rect_rows(320, 256, (0, 0, 320, 256)), sq)) == 0)
    expect("mean_abs refuses sizes that differ", raises(ValueError, mean_abs, img, picture(64, 64), rows))
    expect("mean_abs refuses an empty region", raises(ValueError, mean_abs, img, img, [bytearray(320) for _ in range(256)]))
    away = img
    allmask = rect_rows(320, 256, (0, 0, 320, 256))
    expect("hover pair differing by 9/255 inside the rectangle passes", result(check_hover, img, shift(img, sq, 9), away, sq, allmask))
    expect("hover pair differing by 8/255 inside the rectangle passes", result(check_hover, img, shift(img, sq, 8), away, sq, allmask))
    expect("hover pair differing by 7/255 inside the rectangle fails", not result(check_hover, img, shift(img, sq, 7), away, sq, allmask))
    expect("an away capture differing by 3/255 fails", not result(check_hover, img, shift(img, sq, 9), shift(img, sq, 3), sq, allmask))
    expect("an away capture differing by 2/255 passes", result(check_hover, img, shift(img, sq, 9), shift(img, sq, 2), sq, allmask))
    other = (0, 100, 320, 150)
    expect("a hover change outside the rectangle and inside the mask fails",
           not result(check_hover, img, shift(shift(img, sq, 9), other, 30), away, sq, allmask))
    expect("a hover change outside the mask is ignored",
           result(check_hover, img, shift(shift(img, sq, 9), other, 30), away, sq, minus_rect(allmask, other)))
    expect("an away change outside the rectangle inside the mask fails",
           not result(check_hover, img, shift(img, sq, 9), shift(img, other, 30), sq, allmask))
    expect("hover without a mask still checks the rectangle", result(check_hover, img, shift(img, sq, 9), away, sq, None))

    expect("chord log with 3 chords and counters 3 passes", result(check_chords, input_log()))
    expect("a key line between chord script lines fails", not result(check_chords, input_log(leak=1)))
    expect("a key line after the last chord sleep fails", not result(check_chords, input_log(leak="tail")))
    expect("a key line after the first chord fails", not result(check_chords, input_log(leak=0)))
    expect("counters chords 2 fails", not result(check_chords, input_log(chords_counter=2)))
    expect("counters chords 4 fails", not result(check_chords, input_log(chords_counter=4)))
    expect("a log with 2 chord script lines fails", not result(check_chords, input_log(n_chords=2)))
    expect("no M5_AFTER line fails", not result(check_chords, input_log(after=0)))
    expect("two M5_AFTER lines fail", not result(check_chords, input_log(after=2)))
    expect("an input-less chord log without counters fails", not result(check_chords, "rt: script 1 chord a\n"))

    expect("focus log in the right order passes", result(check_focus, focus_log()))
    expect("a WM_KEYUP after WM_ACTIVATEAPP wp 0x0 fails", not result(check_focus, focus_log(late_up=True)))
    expect("a key message while unfocused fails", not result(check_focus, focus_log(leak=True)))
    expect("any other message while unfocused fails", not result(check_focus, focus_log(stray=True)))
    expect("a log without focus script lines fails", not result(check_focus, "rt: script 1 key a\n"))
    expect("a log without WM_ACTIVATEAPP fails", not result(check_focus, "rt: script 1 focus lost\nrt: script 2 focus gained\n"))
    expect("a missing WM_LBUTTONUP fails", not result(check_focus, focus_log().replace("WM_LBUTTONUP", "WM_LBUTTONDOWN")))
    expect("a missing WM_KEYUP for scan 0x2A fails", not result(check_focus, focus_log().replace("0xC02A0001", "0xC01E0001")))

    expect("console log passes", result(check_console, console_log()))
    expect("a Left key-down without bit 24 fails", not result(check_console, console_log(left_ext=False)))
    expect("M5_XY instead of M5_XZ fails", not result(check_console, console_log(xz=False)))
    expect("M5_XZ before M5_ABC fails", not result(check_console, "guest: M5_XZ\nguest: M5_ABC\nrt: input WM_KEYDOWN wp 0x25 lp 0x14B0001\n"))
    expect("a stray M5_AC line fails", not result(check_console, console_log() + "guest: M5_AC\n"))
    expect("a console log without a Left key-down fails", not result(check_console, "guest: M5_ABC\nguest: M5_XZ\n"))

    expect("dinput8 rows Create and Release pass", result(check_dinput, dinput_rows()))
    expect("a CreateDevice row fails", not result(check_dinput, dinput_rows(["IDirectInput8A::CreateDevice"])))
    expect("a CreateDevice row of kind shim also fails",
           not result(check_dinput, dinput_rows() + [["1", "dinput8.dll!IDirectInput8A::CreateDevice", "shim", "e", "f", "1"]]))
    expect("a missing Release row fails", not result(check_dinput, dinput_rows()[:1]))
    expect("a log with no dinput8 rows fails", not result(check_dinput, []))
    expect("other dlls are ignored", result(check_dinput, dinput_rows() + [["1", "user32.dll!GetCursorPos", "shim", "e", "f", "1"]]))

    stage = m4.fixture_log()
    expect("cursor lines: a shape passes", result(check_cursor, stage + "rt: cursor default\n"))
    expect("cursor lines: hidden passes", result(check_cursor, stage + "rt: cursor hidden\n"))
    expect("cursor lines: only warps fail", not result(check_cursor, stage + "rt: cursor warp 1,2\n"))
    expect("cursor lines: none fail", not result(check_cursor, stage))

    scmds = parse_script("wait menu\n" + "move 1 1\nsleep 0.01\n" * 100)
    sgood = "".join(f"rt: script {n} x\n" for n, _, _ in scmds) + "rt: input moves 5000 buttons 24 keys 0 chars 0 dropped-chars 0 chords 0 warps 0\n"
    expect("soak log with every script line and the counters passes",
           result(common, SimpleNamespace(rc=0, log=m4.fixture_log() + sgood, crashes=[], rows=dinput_rows() + m4.fixture_rows()), 60, scmds, False, len(scmds) - SOAK_TAIL)
           and result(check_soak, sgood, scmds))
    sempty = SimpleNamespace(rc=0, log=m4.fixture_log(), crashes=[], rows=dinput_rows() + m4.fixture_rows())
    expect("soak log with no script or input lines fails", not result(common, sempty, 60, scmds, False, len(scmds) - SOAK_TAIL) and not result(check_soak, sempty.log, scmds))
    expect("soak with 4999 moves fails", not result(check_soak, sgood.replace("moves 5000", "moves 4999"), scmds))
    expect("soak with 0 buttons fails", not result(check_soak, sgood.replace("buttons 24", "buttons 0"), scmds))
    expect("soak missing more than the tail of script lines fails",
           not result(common, SimpleNamespace(**{**vars(sempty), "log": m4.fixture_log() + "".join(f"rt: script {n} x\n" for n, _, _ in scmds[:-SOAK_TAIL - 5])}), 60, scmds, False, len(scmds) - SOAK_TAIL))
    cmds = parse_script("wait menu\nsleep 1\ncapture a\n")
    full = "rt: script 1 wait menu\nrt: script 2 sleep 1\nrt: script 3 capture a\nrt: script done\n"
    expect("script lines per command and done pass", result(check_script, full, cmds))
    expect("a missing script line fails", not result(check_script, full.replace("rt: script 2 sleep 1\n", ""), cmds))
    expect("a missing done line fails", not result(check_script, full.replace("rt: script done\n", ""), cmds))
    expect("a prefix passes when done is not needed", result(check_script, "rt: script 1 wait menu\n", cmds, False))
    expect("a wrong line number fails even without done", not result(check_script, "rt: script 2 sleep 1\n", cmds, False))

    nl = ("rt: script 4 click left\nrt: script 5 sleep 60\nguest: [CharGenWiz] start\nrt: script 6 capture chargen\n")
    expect("chargen marker between click and capture passes", result(check_newgame, nl))
    expect("a CharGenWizard: DisplayPopUp marker passes", result(check_newgame, nl.replace("[CharGenWiz] start", "CharGenWizard: DisplayPopUp x")))
    expect("a marker after the capture fails", not result(check_newgame, nl.replace(nl.splitlines()[2] + "\n", "") + "guest: [CharGenWiz] late\n"))
    expect("no marker fails", not result(check_newgame, nl.replace("guest: [CharGenWiz] start\n", "")))
    expect("alive 10 s after the capture passes", result(check_newgame, nl, {"exit": 80.0, "cap": 70.0}, 0))
    expect("dying 3 s after the capture fails", not result(check_newgame, nl, {"exit": 73.0, "cap": 70.0}, 0))
    expect("a non-zero exit fails", not result(check_newgame, nl, {"exit": 80.0, "cap": 70.0}, 1))

    qlog = "guest LauncherMain returned 0\n"
    expect("quit in 4 s with LauncherMain returned 0 passes", result(check_quit, qlog, 4.0))
    expect("quit with ExitProcess(0) passes", result(check_quit, "ExitProcess(0)\n", 4.0))
    expect("quit after 16 s fails", not result(check_quit, qlog, 16.0))
    expect("quit with no exit line fails", not result(check_quit, "nothing\n", 4.0))
    expect("quit with a forced quit line fails", not result(check_quit, qlog + "rt: forced quit\n", 4.0))
    expect("quit without a click time fails", not result(check_quit, qlog, None))

    good_log = m4.fixture_log() + full + "rt: input moves 1 buttons 0 keys 0 chars 0 dropped-chars 0 chords 0 warps 0\n"
    res = SimpleNamespace(rc=0, log=good_log, crashes=[], rows=dinput_rows() + m4.fixture_rows())
    expect("common passes on a clean synthetic run", result(common, res, 60, cmds))
    expect("common fails on a CreateDevice row",
           not result(common, SimpleNamespace(**{**vars(res), "rows": res.rows + dinput_rows(["IDirectInput8A::CreateDevice"])[2:]}), 60, cmds))
    expect("common fails on a queue full line", not result(common, SimpleNamespace(**{**vars(res), "log": good_log + "rt: message queue full\n"}), 60, cmds))
    expect("common fails on a FAULT", not result(common, SimpleNamespace(**{**vars(res), "log": good_log + "rt: FAULT host crash (exit 6): x\n"}), 60, cmds))
    expect("common fails on exit 10", not result(common, SimpleNamespace(**{**vars(res), "rc": 10}), 60, cmds))
    expect("common fails on a timeout", not result(common, SimpleNamespace(**{**vars(res), "rc": None}), 60, cmds))

    expect("script_seconds adds the sleeps", script_seconds(parse_script("sleep 1\nsleep 0.5\nwait menu\n")) == 1.5)
    expect("close_after adds the margin", close_after_for(SimpleNamespace(close_after=None, margin=60), parse_script("sleep 1.2\n")) == 62
           and close_after_for(SimpleNamespace(close_after=9, margin=60), parse_script("sleep 1.2\n")) == 9)

    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        cases = {"chords": (input_log(), input_log(chords_counter=2)), "focus": (focus_log(), focus_log(late_up=True)),
                 "console": (console_log(), console_log(left_ext=False)),
                 "cursor": (m4.fixture_log() + "rt: cursor default\n", m4.fixture_log())}
        for what, (ok_log, bad_log) in cases.items():
            for tag, text in (("good", ok_log), ("bad", bad_log)):
                lp = os.path.join(tmp, f"{what}-{tag}.log")
                open(lp, "w").write(text)
                rc = cmd_check(SimpleNamespace(what=what, log=lp))
                expect(f"check --what {what} on a {tag} log exits {0 if tag == 'good' else 1}", rc == (0 if tag == "good" else 1))
    ap = build_parser()
    for mode in ("hover", "console", "buttons", "options", "newgame", "quit", "chords", "focus", "displays", "soak", "cases", "seed", "selftest"):
        expect(f"parser has {mode}", ap.parse_args([mode]).mode == mode)
    expect("parser takes --display and --back", ap.parse_args(["hover", "--display", "1"]).display == 1 and
           ap.parse_args(["buttons", "--back", "cancel"]).back == "cancel" and ap.parse_args(["soak"]).n == 3)
    expect("parser takes check", ap.parse_args(["check", "--what", "chords", "--log", "x"]).what == "chords")

    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        os.makedirs(os.path.join(tmp, "shots"))
        old = os.path.join(tmp, "shots", "options-20260101T000000Z.png")
        new = os.path.join(tmp, "shots", "options-20260102T000000Z.png")
        other = os.path.join(tmp, "shots", "options-esc-20260103T000000Z.png")
        for i, f in enumerate((old, new, other)):
            open(f, "wb").write(b"x")
            os.utime(f, (100 + i * 10, 100 + i * 10))
        expect("shot_of returns the newest file of that exact name", shot_of(tmp, "options") == new and shot_of(tmp, "options-esc") == other)
        expect("shot_of returns None for a missing name", shot_of(tmp, "nope") is None)
        p, c = prepare(os.path.join(tmp, "o"), "t", "wait menu\nsleep 1\n")
        expect("prepare writes the script into the overlay", open(p).read() == "wait menu\nsleep 1\n" and len(c) == 2)
        expect("prepare refuses a bad script", raises(ScriptError, prepare, os.path.join(tmp, "o"), "t2", "bogus\n"))
        w, h, rs = img
        png = m4.write_png(w, h, rs)
        pa = SimpleNamespace(size=(320, 256), min_colours=10, refdir=tmp, tol=8, pct=0.99, mad=2.0, luma=5.0, block=32, block_frac=0.5)
        os.makedirs(os.path.join(tmp, "s2", "shots"))
        open(os.path.join(tmp, "s2", "shots", "cap-20260101T000000Z.png"), "wb").write(png)
        rr = Rep(quiet=True)
        expect("screen reads a capture and notes a missing reference", screen(rr, pa, os.path.join(tmp, "s2"), "cap", "m5-none") is not None and rr.ok)
        rr = Rep(quiet=True)
        expect("screen fails when the capture is missing", screen(rr, pa, os.path.join(tmp, "s2"), "nocap") is None and not rr.ok)
        rr = Rep(quiet=True)
        json.dump({"width": 320, "height": 256, "rects": [[0, 0, 320, 256]]}, open(os.path.join(tmp, "m5-ref-mask.json"), "w"))
        open(os.path.join(tmp, "m5-ref.png"), "wb").write(png)
        screen(rr, pa, os.path.join(tmp, "s2"), "cap", "m5-ref")
        expect("screen compares equal pictures against a reference", rr.ok)
        rr = Rep(quiet=True)
        open(os.path.join(tmp, "m5-ref.png"), "wb").write(m4.write_png(w, h, shift(img, (0, 0, 320, 256), 30)[2]))
        screen(rr, pa, os.path.join(tmp, "s2"), "cap", "m5-ref")
        expect("screen fails against a different reference", not rr.ok)
        rr = Rep(quiet=True)
        open(os.path.join(tmp, "m4-menu.png"), "wb").write(m4.write_png(w, h, shift(img, (0, 0, 320, 256), 30)[2]))
        json.dump({"width": 320, "height": 256, "rects": [[0, 0, 320, 256]]}, open(os.path.join(tmp, "m4-menu-mask.json"), "w"))
        differs(rr, pa, img, "x screen", "m5-ref", "m4-menu")
        expect("differs passes when the screen differs from the menu everywhere", rr.ok)
        rr = Rep(quiet=True)
        open(os.path.join(tmp, "m4-menu.png"), "wb").write(png)
        differs(rr, pa, img, "x screen", "m5-ref", "m4-menu")
        expect("differs fails when the screen equals the menu", not rr.ok)

    print(f"selftest: {n['ok'] - len(bad)} of {n['ok']} passed")
    return 0 if not bad else 1


def main():
    a = build_parser().parse_args()
    try:
        return a.fn(a)
    except (OSError, ValueError, PngError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
