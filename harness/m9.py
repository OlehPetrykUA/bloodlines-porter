#!/usr/bin/env python3
import argparse
import contextlib
import glob
import hashlib
import io
import json
import math
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "tools")))

import m4
import m6
import m7
from m4 import PngError, Rep, X_DEFAULT, need_exe, percentile, read_png, write_png

SCRIPTS = os.path.join(HERE, "m9")
ROOT = os.path.normpath(os.path.join(HERE, ".."))
CMD_MASK = 1 << 20
NAMES = ("overlays", "picture", "aspect")
MENU_KEYS = (("1", "overlays"), ("2", "picture"))
DEAD_KEYS = ("3", "4", "5", "6", "7", "8")
CHORD_NAMES = tuple(n for _, n in MENU_KEYS)
DEFAULTS_RUN = {"overlays": "all", "picture": "off", "aspect": "4:3"}
DEFAULTS_PLAYER = {**DEFAULTS_RUN, "overlays": "off"}
NONDEFAULT = {"overlays": "fps", "picture": "native", "aspect": "21:9"}
ITEMS = (("Overlays", "1", "G1"), ("Picture mode", "2", "G6"))
BANNED = ("Frame interpolation", "Edge smoothing", "HDR", "Texture filtering", "next launch", "Aspect", "Controller", "Developer", "Settings", "Close")
RE_MOJIBAKE = re.compile("Ã|â€|\ufffd|Â")
FX_ITEM = {"Picture mode": "spatial"}
LIVE_ROWS = tuple(n for _, n in MENU_KEYS)
RE_FPS_TITLE = re.compile(r"^(\d+)(?:\+(\d+))? FPS$")
RE_TOGGLE = re.compile(r"^rt: toggle (\w+) (\S+) -> (\S+) \((chord|menu|settings)\)\s*$", re.M)
RE_TOGGLE_NA = re.compile(r"^rt: toggle (\w+) unavailable \((.*)\)\s*$", re.M)
RE_FX_SUPPORT = re.compile(r"^rt: fx support spatial ([01]) temporal ([01]) interpolator ([01]) metal4fx ([01])\s*$", re.M)
RE_FX_UNSUPPORTED = re.compile(r"^rt: fx unsupported (spatial|temporal|interpolator) \((.*)\)\s*$", re.M)
RE_FX_FRAMES = re.compile(r"^rt: fx frames (\d+) scaled (\d+) smoothed (\d+) interpolated (\d+) passthrough (\d+) added p50 ([\d.]+) p99 ([\d.]+)\s*$", re.M)
RE_ROUTE_NA = re.compile(r"^rt: fx route r1 unavailable \((.*)\)\s*$", re.M)
RE_DXVK_ENV = re.compile(r"^rt: gfx env DXVK_CONFIG=(.*) \((kept|set)\)\s*$", re.M)
RE_ANISO = re.compile(r"samplerAnisotropy")
RE_FAIL_LINE = re.compile(r"^FAIL ", re.M)
RE_INIT_CALL = re.compile(r"\brt_hud_init\s*\(")
RE_MAP_LOG = re.compile(r"^rt: (?:stage 12 map|map ready) ", re.M)

WINDOW_S = 2
WINDOW_NS = WINDOW_S * 1_000_000_000
FPS_TOL_MS = 0.05
OVERLAY_BG = (17, 34, 51)
OVERLAY_CANVAS = (1280, 1024)
ASPECTS = {"16:9": (16, 9), "21:9": (43, 18)}
ASPECT_STEPS = ("16:9", "fit", "21:9", "engine")
ENGINE_SIZE = (1280, 1024)
MIN_SIZE = (640, 480)
FLOOR_SIZE = (800, 600)
DISPLAY = (3440, 1440)
WINDOWS = ((1280, 1024), (3440, 1440), (2056, 1329))
DEFAULT43_DESKTOPS = ((2056, 1329, 2056, 1196), (3440, 1440, 3440, 1383), (1470, 956, 1470, 891))
DEFAULT43_TOL = 0.05
FIT_SKIP = 0.01
HFOV_TOL = 0.01
VFOV_TOL = 0.01
SPATIAL_TARGETS = ((2560, 2048), (1800, 1440))
CARD_MEAN_TOL = 2.0
CARD_PATCH_TOL = 3.0
CARD_BLACK_EXTRA = 0.001
GRAD_MIN = 1.05
MOTION_TOL = 0.05
MOTION_FRAC = 0.99
TAA_FRAMES = 64
TAA_SIZE = 128
TAA_TAIL = 8
TAA_EDGE_GAIN = 0.5
TAA_TRAIL_TOL = 4.0
INTERP_MID_TOL = 1.0
INTERP_HUD_TOL = 1.0
INTERP_BG = 32
HDR_SDR_TOP = 0.8
HDR_TOL = 1.0 / 255
HDR_STEPS = 256
CAMERA_FRAC = 0.99
QUEUE_FRAME_NS = 10_000_000
FX_FRAMES = 3000
FX_P99 = {"spatial": 2.0, "taa": 3.0}
FX_COUNT = {"spatial": "scaled", "taa": "smoothed"}
LOOSE_BYTES = b"loose-mod-bytes\n"
VPK_BYTES = b"vpk-original-bytes\n"
CONTROLLER_CASES = (("Xbox Wireless Controller", "xbox"), ("DualSense Wireless Controller", "playstation"), ("Generic Pad", "xbox"))
PARSE_CASES = (("16:9", "ratio"), ("21:9", "ratio"), ("fit", "fit"), ("engine", "engine"), ("3440x1440", "size"), ("0x0", "bad"),
               ("text", "bad"), ("16:0", "bad"), ("", "bad"))
NEXT_CASES = (("16:9", 1.25, "fit"), ("16:9", 16 / 9, "21:9"), ("fit", 1.25, "21:9"), ("21:9", 1.25, "16:9"), ("engine", 1.25, "16:9"),
              ("engine", 1.7778, "16:9"), ("16:9", 43 / 18, "21:9"), ("16:9", 1.547, "fit"))
HFOV_CASES = ((60.0, 1280, 1024), (60.0, 1280, 720), (60.0, 3440, 1440), (75.0, 2056, 1329))
PROJ_RATIOS = (("4:3", 4 / 3), ("16:9", 16 / 9), ("21:9", 43 / 18))
PROJ_VFOV = 60.0
LIVE_VERBS = ("wait", "sleep", "capture", "chord")
LIVE_WAITS = ("menu", "bink")
CHORD_KEYS = tuple("12345678")
LOCK_OWNER = "m9"
PIECES = ("G1", "G2", "G2b", "G3", "G4", "G5", "G6", "G6b", "G7", "G8", "G9", "D", "M7", "W")
WIN_LAUNCH = ((2056, 1329), (3440, 1440), (1470, 956))
WIN_SIZES = ((2056, 1329), (3440, 1440), (1470, 956), (900, 500))
WIN_ASPECTS = ("4:3", "fit", "16:9", "21:9")
WIN_RATIO = {"4:3": (4, 3), "16:9": (16, 9), "21:9": (43, 18)}
WIN_STARTS = ((1280, 1024), (320, 240), (0, 0), (800, 300))
WIN_MENU = ("hide:", "terminate:", "performMiniaturize:", "performZoom:", "toggleFullScreen:")
WIN_BAD_SETTINGS = 9
WIN_RESTORE = (("inside", "100,100,1280,1024", 1), ("offscreen", "578,396,900,500", 0), ("second-display", "2500,100,1000,700", 1),
               ("unplugged-second", "528,296,1000,700", 0), ("too-big", "0,32,2056,1228", 1), ("too-small", "100,100,640,480", 1),
               ("overhang", "1156,200,900,500", 1), ("no-displays", "10,20,640,480", 0))
WIN_STATE = {"state-drag": {"have": "1", "due_early": "0", "due_late": "1", "rect": "120,80,900,500"},
             "state-enter": {"fullscreen": "1", "rect": "120,80,900,500"},
             "state-leave": {"fullscreen": "0", "rect": "40,60,1000,700"},
             "state-wrap": {"fullscreen": "1", "rect": "120,80,900,500"},
             "state-startup-fs": {"fullscreen": "1", "rect": "200,150,1100,800"},
             "state-prelive-fs": {"fullscreen": "1", "dirty": "1", "written": "1"},
             "state-prelive-leave": {"fullscreen": "0", "dirty": "1"},
             "state-first-launch": {"have": "0", "dirty": "0"}}
WIN_INPUT = {"focus-captured": ("1", "0"), "focus-lost-captured": ("0", "1"), "focus-regained": ("1", "0"), "host-unfocused": ("0", "1")}
ENGINE_CASES = ("engine-aspect", "engine-fov", "engine-hud", "engine-load", "engine-ui", "engine-rows")
HUD_READS = 426
RE_LOC = re.compile(r"^loc_([0-9A-F]{8}): ;", re.M)
TAA = {"edge_cx": 64.0, "edge_cy": 64.0, "edge_a0": 20.0, "edge_da": 0.5, "sq_size": 16.0, "sq_x0": 8.0, "sq_y": 96.0, "sq_vx": 1.5, "ss": 8}
OVERLAY_A = {"fps": 200, "p50": 5.0, "p99": 7.5, "created": 120, "failed": 2, "load": 1.5,
             "t": {"overlays": "all", "picture": "unavailable"}}
OVERLAY_B = {**OVERLAY_A, "fps": 144, "p50": 6.9, "p99": 9.1, "load": 2.25,
             "t": {**OVERLAY_A["t"], "picture": "native"}}


def parse_m9(text):
    out = []
    for l in text.splitlines():
        if not l.startswith("m9 "):
            continue
        try:
            w = shlex.split(l)
        except ValueError:
            continue
        if len(w) < 3:
            continue
        out.append((w[1], w[2], dict(t.partition("=")[::2] for t in w[3:])))
    return out


def rows_of(rows, case, label=None):
    return [kv for c, lab, kv in rows if c == case and (label is None or lab == label or (label.endswith("*") and lab.startswith(label[:-1])))]


def row_map(rows, case):
    return {lab: kv for c, lab, kv in rows if c == case}


def m9_line(case, label, **kv):
    return " ".join(["m9", case, label] + [f"{k}={shlex.quote(str(v))}" for k, v in kv.items()])


def tsv_menus(text):
    menus = []
    for l in text.splitlines()[1:]:
        f = l.split("\t")
        if f[0] == "menu" and len(f) >= 2:
            menus.append({"title": f[1], "items": []})
        elif f[0] == "item" and menus and len(f) >= 6:
            menus[-1]["items"].append({"title": f[1], "key": f[2], "mask": int(f[3]) if f[3].isdigit() else -1, "enabled": f[4] == "1", "tip": f[5]})
    return menus


def item_name(title):
    return title.split(":")[0].strip()


def check_menu(r, text, merged=("G1",), label="menu", player=False):
    menus = tsv_menus(text)
    titles = [m["title"] for m in menus]
    r.check(f"{label}: no FPS readout in the menu bar, in any mode ({titles})", not any(RE_FPS_TITLE.match(t) for t in titles), titles)
    r.check(f"{label}: exactly two menus, app and Game ({titles})", len(menus) == 2, titles)
    if len(menus) < 2:
        return
    game = menus[1]
    r.check(f"{label}: Game is the menu after the app menu", game["title"] == "Game", game["title"])
    items = game["items"]
    names = [item_name(i["title"]) for i in items]
    shown = ITEMS
    want = [n for n, _, _ in shown]
    r.check(f"{label}: Game items are exactly the live rows, in key order ({names})", names == want, names)
    r.check(f"{label}: every item carries its key and the Command mask only (1 << 20)",
            [(i["key"], i["mask"]) for i in items] == [(k, CMD_MASK) for _, k, _ in shown], [(i["key"], i["mask"]) for i in items])
    bad = [t for m in menus for i in m["items"] for t in (i["title"], i["tip"]) for b in BANNED if b in t]
    r.check(f"{label}: no dropped feature, Aspect, Controller, Developer info, Settings, Close or '(next launch)' anywhere in the menus ({bad})", not bad, bad)
    r.check(f"{label}: no mis-encoded text in the menus", not RE_MOJIBAKE.search(text), RE_MOJIBAKE.findall(text))
    by = {item_name(i["title"]): i for i in items}
    off = []
    for n, _, piece in ITEMS:
        i = by.get(n)
        if i and piece and piece not in merged and (i["enabled"] or not i["tip"]):
            off.append(n)
    r.check(f"{label}: items of unmerged pieces are disabled with a tooltip ({off})", not off, off)
    nt = [n for n, i in by.items() if not i["enabled"] and not i["tip"]]
    r.check(f"{label}: every disabled item has a tooltip ({nt})", not nt, nt)


def check_menu_same(r, a, b):
    r.check("menu --player writes the same menu.tsv as menu", a == b)


def check_menu_fx(r, text, support, merged=("G6", "G7", "G8")):
    by = {item_name(i["title"]): i for m in tsv_menus(text)[1:2] for i in m["items"]}
    piece = {n: p for n, _, p in ITEMS}
    for name, key in FX_ITEM.items():
        i = by.get(name)
        if support[key] and piece[name] not in merged:
            continue
        r.check(f"{name} enabled matches supportsDevice for the {key} scaler ({support[key]})", bool(i) and i["enabled"] == bool(support[key]), i)


def check_fx_cycle(r, rows, support):
    kv = (rows_of(rows, "fx", "cycle") or [{}])[0]
    if support.get("spatial") and kv.get("native") == "1":
        r.check("cmd-2 cycles metalfx, off with native kept out of the cycle (mask keeps the native step) and is back at its start", kv.get("seq") == "metalfx off" and kv.get("back") == "1"
                and kv.get("mask") == "7", kv)
    elif support.get("spatial"):
        r.check("cmd-2 cycles metalfx, off with no native step until the engine override exists, and is back at its start",
                kv.get("native") == "0" and kv.get("seq") == "metalfx off" and kv.get("back") == "1" and kv.get("mask") == "5", kv)
    else:
        r.check("cmd-2 is disabled with a reason and stays put", kv.get("mask") == "0" and bool(kv.get("reason")) and kv.get("back") == "1", kv)
    r.check("cmd-2 Picture mode starts off (MetalFX is opt-in until G6b)", kv.get("start") == "off", kv)


def fx_pipelines(log):
    return re.findall(r"^rt: fx pipelines spatial .* built in ([\d.]+) ms", log, re.M)


def check_menu_forced(r, text):
    by = {item_name(i["title"]): i for m in tsv_menus(text)[1:2] for i in m["items"]}
    for name in FX_ITEM:
        i = by.get(name)
        r.check(f"RT_FX_FORCE_UNSUPPORTED: {name} is disabled with a reason", bool(i) and not i["enabled"] and bool(i["tip"]), i)


def check_input(r, rows, log, merged=("G1",), player=False):
    chords = row_map(rows, "input")
    total = 0
    for key, name in MENU_KEYS:
        kv = chords.get(f"chord{key}", {})
        total += int(kv.get("toggles", -1)) if kv.get("toggles", "").lstrip("-").isdigit() else 0
        r.check(f"cmd-{key} gives exactly one toggle line for {name}, no guest key and no character",
                kv.get("name") == name and kv.get("toggles") == "1" and kv.get("guest_keys") == "0" and kv.get("guest_chars") == "0", kv)
    for key in DEAD_KEYS:
        kv = chords.get(f"chord{key}", {})
        r.check(f"cmd-{key} is not a feature key: no toggle, no developer request, no guest key", kv.get("toggles") == "0" and kv.get("dev") == "0"
                and kv.get("guest_keys") == "0", kv)
    for n in range(1, 9):
        b = chords.get(f"bare{n}", {})
        r.check(f"bare {n} gives a guest key and no toggle", b.get("toggles") == "0" and b.get("guest_keys", "0").isdigit() and int(b.get("guest_keys", "0")) >= 1, b)
    both = chords.get("both", {})
    total += int(both["toggles"]) if both.get("toggles", "").isdigit() else 0
    r.check("one chord through the menu action and the SDL path in one pump gives one toggle", both.get("toggles") == "1", both)
    n_log = len(RE_TOGGLE.findall(log)) + len(RE_TOGGLE_NA.findall(log))
    r.check(f"the log holds exactly the toggle lines the rows claim ({n_log} of {total})", n_log == total, (n_log, total))


def check_keys_work(r, menu_text, rows):
    chords = row_map(rows, "input")
    game = (tsv_menus(menu_text) + [{"items": []}, {"items": []}])[1]["items"]
    dead = []
    for i in game:
        if not i["key"]:
            continue
        kv = chords.get(f"chord{i['key']}", {})
        if kv.get("toggles") != "1":
            dead.append(item_name(i["title"]))
    r.check(f"every menu row with a key has a key that works ({dead})", not dead, dead)


def check_live(r, rows, player=False):
    live = row_map(rows, "live")
    for name in LIVE_ROWS:
        kv = live.get(name, {})
        r.check(f"live {name}: flipped in the running instance and the state it controls changed", kv.get("changed") == "1" or (name == "picture" and kv.get("unsupported") == "1" and kv.get("changed") == "1"), kv)
    gone = [k for k in ("controller", "aspect", "developer", "aniso") if k in live]
    r.check(f"no live row for a dropped control ({gone})", not gone, gone)
    kv = live.get("dead-keys", {})
    r.check("live dead keys cmd-4 to cmd-8 change nothing and request no developer toggle", kv.get("ok") == "1" and kv.get("toggles") == "0", kv)
    kv = live.get("result", {})
    r.check("live result bad=0", kv.get("bad") == "0", kv)


def check_overlay_player(r, text):
    r.check("overlay has no Developer info row and no cmd-4 to cmd-8 key, in any mode", "Developer" not in text and not any(f"⌘{n}" in text for n in range(4, 9)), text)


def check_capture(r, rows):
    c = row_map(rows, "input")
    want = {"capture-default": ("1", None), **WIN_INPUT}
    for lab, (rel, cur) in want.items():
        kv = c.get(lab, {})
        r.check(f"mouse {lab}: relative {rel}" + (f", cursor visible {cur}" if cur else ""),
                kv.get("relative") == rel and (cur is None or kv.get("cursor") == cur), kv)


def fps_series(name):
    if name == "steady":
        return [(i * 5_000_000, 0) for i in range(601)]
    if name == "spike":
        gaps = [5_000_000] * 600
        for back in (60, 150, 260, 387):
            gaps[599 - back] = 20_000_000
        t, out = 0, [(0, 0)]
        for g in gaps:
            t += g
            out.append((t, 0))
        return out
    out = []
    for i in range(301):
        if i:
            out.append((i * 10_000_000 - 5_000_000, 1))
        out.append((i * 10_000_000, 0))
    return out


def fps_model(presents, short=False):
    last = presents[-1][0]
    start = last - WINDOW_NS
    win = [p for p in presents if p[0] >= start]
    gaps = [(b[0] - a[0]) / 1e6 for a, b in zip(win, win[1:])]
    if short:
        gaps = gaps[1:]
    inside = [p for p in win if p[0] > start]
    real = sum(1 for p in inside if not p[1])
    made = len(inside) - real
    title = f"{round(real / WINDOW_S)}+{round(made / WINDOW_S)} FPS" if made else f"{round(real / WINDOW_S)} FPS"
    return title, percentile(gaps, 50), percentile(gaps, 99)


def check_fps(r, rows):
    got = row_map(rows, "fps")
    for name in ("steady", "spike", "interp"):
        title, p50, p99 = fps_model(fps_series(name))
        kv = got.get(name, {})
        try:
            g50, g99 = float(kv.get("p50", "nan")), float(kv.get("p99", "nan"))
        except ValueError:
            g50 = g99 = float("nan")
        r.check(f"fps {name}: overlay FPS text reads {title!r} (got {kv.get('title')!r})", kv.get("title") == title, kv)
        r.check(f"fps {name}: p50 {g50} and p99 {g99} are within {FPS_TOL_MS} ms of {p50:.2f} and {p99:.2f}",
                abs(g50 - p50) <= FPS_TOL_MS and abs(g99 - p99) <= FPS_TOL_MS, (g50, g99, p50, p99))
    pl = got.get("player", {})
    r.check(f"fps player: {pl.get('fps')} FPS and p50 {pl.get('p50')} ms are non-zero after 600 presents",
            pl.get("fps") == "200" and pl.get("p50") == "5.00", pl)
    r.check("fps player: the frame counter has a start (exit log reports frames)", pl.get("frames-start") == "1", pl)


def overlay_lines(o, state, player=False):
    t = o["t"]
    if state == "off":
        return []
    first = f"{o['fps']} FPS  p50 {o['p50']:.2f} ms  p99 {o['p99']:.2f} ms"
    if state == "fps":
        return [first]
    rows = [("⌘1 Overlays", "overlays"), ("⌘2 Picture mode", "picture")]
    return [first, f"Shaders {o['created']} created, {o['failed']} failed", f"Load {o['load']:.2f}"] + \
        [f"{name}: {t[k]}" for name, k in rows if t[k] != "unavailable"]


def png_bbox(data, bg=OVERLAY_BG):
    w, h, rows = read_png(data)
    row_bg = bytes(bg) * w
    x0, y0, x1, y1 = w, h, -1, -1
    for y, row in enumerate(rows):
        if row == row_bg:
            continue
        xs = [x for x in range(w) if row[3 * x:3 * x + 3] != bytes(bg)]
        x0, x1 = min(x0, xs[0]), max(x1, xs[-1])
        y0, y1 = min(y0, y), max(y1, y)
    return (w, h), (None if x1 < 0 else (x0, y0, x1, y1))


def check_overlay(r, states, canvas=OVERLAY_CANVAS):
    boxes = {}
    for state, (o, text, png) in states.items():
        want = overlay_lines(o, state if state != "all2" else "all")
        got = text.splitlines()
        r.check(f"overlay {state}: the text lines equal the expected ones ({len(got)} of {len(want)})", got == want, (got, want))
        bad = [b for b in BANNED if b in text]
        r.check(f"overlay {state}: no dropped feature, Aspect, Texture filtering or '(next launch)' ({bad})", not bad, bad)
        r.check(f"overlay {state}: no mis-encoded text ('16×' style UTF-8 damage)", not RE_MOJIBAKE.search(text), RE_MOJIBAKE.findall(text))
        size, box = png_bbox(png)
        r.check(f"overlay {state}: PNG is {canvas[0]}x{canvas[1]}", size == canvas, size)
        boxes[state] = box
        if not want:
            r.check("overlay off: draws nothing", box is None, box)
        else:
            ok = box is not None and box[0] >= canvas[0] // 2 and box[3] < canvas[1] // 2
            r.check(f"overlay {state}: the box fits in the top-right quarter ({box})", ok, box)
    if boxes.get("all") and boxes.get("fps"):
        ha, hf = boxes["all"][3] - boxes["all"][1] + 1, boxes["fps"][3] - boxes["fps"][1] + 1
        r.check(f"overlay fps: one line, its box is under half the height of all ({hf} of {ha} px)", hf * 2 < ha, (hf, ha))


def check_identical(r, label, a, b):
    r.check(f"{label}: the two sets hold the same files ({sorted(a) == sorted(b)})", sorted(a) == sorted(b), (sorted(a), sorted(b)))
    bad = [k for k in a if k in b and a[k] != b[k]]
    r.check(f"{label}: every file is byte-identical ({len(a) - len(bad)} of {len(a)})", not bad and bool(a), bad)


def check_settings(r, rows, log=""):
    s = row_map(rows, "settings")
    for n in NAMES:
        kv = s.get(f"roundtrip-{n}", {})
        r.check(f"settings {n}: {NONDEFAULT[n]} written and read back equal after the restart",
                kv.get("wrote") == NONDEFAULT[n] and kv.get("read") == NONDEFAULT[n], kv)
    for lab, d in (("defaults-run", DEFAULTS_RUN), ("defaults-player", DEFAULTS_PLAYER), ("no-file", DEFAULTS_RUN)):
        kv = s.get(lab, {})
        r.check(f"settings {lab}: all toggles at their default", all(kv.get(n) == d[n] for n in NAMES), kv)
    kv = s.get("unknown-name", {})
    r.check("settings unknown name: one log line and every default", kv.get("unknown_lines") == "1" and all(kv.get(n) == DEFAULTS_RUN[n] for n in NAMES), kv)
    kv = s.get("bad-value", {})
    nm = kv.get("bad_name", "")
    r.check("settings bad value: one log line, that toggle at its default, not fatal",
            kv.get("bad_lines") == "1" and nm in NAMES and kv.get(nm) == DEFAULTS_RUN.get(nm), kv)
    kv = s.get("readonly", {})
    r.check("settings read-only folder: one log line and the run goes on", kv.get("readonly_lines") == "1" and kv.get("survived") == "1", kv)
    kv = s.get("dropped-keys", {})
    r.check("settings: interp, smoothing, hdr, controller, aniso and mouse lines from an old file are ignored without a warning", kv.get("warn_lines") == "0", kv)
    kv = s.get("options-video", {})
    r.check("settings: an Options Video write before stage 6 is ignored; after it, 1728x1080 is stored as aspect=1728x1080 and read back",
            kv.get("before_stage6") == "0" and kv.get("after_stage6") == "1" and kv.get("reload") == "1728x1080", kv)
    kv = s.get("player-native", {})
    r.check("settings picture=native in a player build reads as off without a warning, is written back as off, and stays native in a dev build",
            kv.get("player") == "off" and kv.get("warn_lines") == "0" and kv.get("saved_off") == "1" and kv.get("dev") == "native", kv)
    kv = s.get("held-step", {})
    r.check("settings picture=native with no native step: off this run, native kept in settings.txt across a save",
            kv.get("value") == "off" and kv.get("file_kept") == "1" and kv.get("save_kept") == "1", kv)


def check_dxvk(r, rows, log):
    s = row_map(rows, "settings")
    st, usr = s.get("dxvk-set", {}), s.get("dxvk-user", {})
    r.check("DXVK_CONFIG has no samplerAnisotropy entry, even with aniso=16 in an old settings file (the game's own Options own it)",
            "value" in st and not RE_ANISO.search(st["value"]), st)
    r.check("a DXVK_CONFIG that is already set is kept and logged 'kept'",
            usr.get("kept") == "1" and usr.get("value") == usr.get("user") and bool(usr.get("user")) and any(k == "kept" for _, k in RE_DXVK_ENV.findall(log)), usr)


def aspect_fit(rw, rh, w, h):
    return (h * rw // rh, h) if w * rh >= h * rw else (w, w * rh // rw)


def aspect_size(state, win_w, win_h, display=DISPLAY):
    if state == "engine":
        return ENGINE_SIZE
    w, h = min(win_w, display[0]), min(win_h, display[1])
    if state in ASPECTS:
        rw, rh = ASPECTS[state]
        w, h = aspect_fit(rw, rh, w, h)
    if w < MIN_SIZE[0] or h < MIN_SIZE[1] or (w, h) == MIN_SIZE:
        return FLOOR_SIZE
    return w, h


def fit_skipped(ratio):
    return any(abs(ratio / (a / b) - 1) <= FIT_SKIP for a, b in ASPECTS.values())


def aspect_next(step, ratio):
    n = ASPECT_STEPS[ASPECT_STEPS.index(step) + 1] if step in ASPECT_STEPS[:2] else ASPECT_STEPS[0]
    return "21:9" if n == "fit" and fit_skipped(ratio) else n


def hfov_deg(vfov, w, h):
    return math.degrees(2 * math.atan(math.tan(math.radians(vfov) / 2) * w / h))


def vfov_back(hfov, w, h):
    return math.degrees(2 * math.atan(math.tan(math.radians(hfov) / 2) * h / w))


def parse_size(s):
    m = re.fullmatch(r"(\d+)x(\d+)", s)
    return (int(m[1]), int(m[2])) if m else None


def check_aspect(r, rows):
    a = rows_of(rows, "aspect", "parse")
    got = {kv.get("input"): kv.get("kind") for kv in a}
    for s, kind in PARSE_CASES:
        r.check(f"aspect parse {s!r} is {kind}", got.get(s) == kind, got.get(s))
    sizes = {(kv.get("state"), kv.get("win")): kv.get("out") for kv in rows_of(rows, "aspect", "size")}
    for st in ASPECT_STEPS:
        for w, h in WINDOWS:
            want = "%dx%d" % aspect_size(st, w, h)
            r.check(f"aspect {st} in {w}x{h} gives {want}", sizes.get((st, f"{w}x{h}")) == want, sizes.get((st, f"{w}x{h}")))
    for n in ("size-keep", "size-window"):
        got = rows_of(rows, "aspect", n)
        r.check(f"aspect {n}: the asked size is kept, the window keeps its shape and fits the usable area ({len(got)} rows)",
                len(got) >= 4 and all(kv.get("ok") == "1" for kv in got), got)
    kv = (rows_of(rows, "aspect", "pp-size") or [{}])[0]
    r.check("aspect pp-size: a windowed 0x0 back buffer takes the guest window, a set size stays, full screen stays",
            kv.get("zero") == "1594x1196" and kv.get("kept") == "800x600" and kv.get("full") == "0x0" and kv.get("ok") == "1", kv)
    nxt = {(kv.get("from"), kv.get("ratio")): kv.get("to") for kv in rows_of(rows, "aspect", "next")}
    for frm, ratio, to in NEXT_CASES:
        got_to = next((v for (f, rt), v in nxt.items() if f == frm and rt and abs(float(rt) - ratio) < 1e-3), None)
        r.check(f"aspect next after {frm} at window ratio {ratio:.4f} is {to} (fit skipped within 1 %)", got_to == to, got_to)
    hf = rows_of(rows, "aspect", "hfov")
    for v, w, h in HFOV_CASES:
        kv = next((x for x in hf if x.get("w") == str(w) and x.get("h") == str(h) and float(x.get("vfov", "0")) == v), {})
        out = float(kv["out"]) if kv.get("out") else float("nan")
        want = hfov_deg(v, w, h)
        r.check(f"Hor+ {v:g} deg at {w}x{h}: horizontal {want:.3f} (got {out}), vertical kept within {HFOV_TOL}",
                abs(out - want) <= HFOV_TOL and abs(vfov_back(out, w, h) - v) <= HFOV_TOL, kv)
    check_default43(r, rows)


def check_default43(r, rows):
    got = {(kv.get("desktop"), kv.get("client")): kv for kv in rows_of(rows, "aspect", "default43")}
    for dw, dh, cw, ch in DEFAULT43_DESKTOPS:
        kv = got.get((f"{dw}x{dh}", f"{cw}x{ch}"), {})
        want = "%dx%d" % aspect_fit(4, 3, cw, ch)
        win, eng = parse_size(kv.get("window", "")), parse_size(kv.get("engine", ""))
        try:
            hf = float(kv.get("hfov75", "nan"))
        except ValueError:
            hf = float("nan")
        r.check(f"fresh default on {dw}x{dh} (client {cw}x{ch}): starts at 4:3, window {want} (largest 4:3 that fits), engine aspect 4:3 within 1 px, "
                f"Hor+ neutral within {DEFAULT43_TOL} deg",
                kv.get("start") == "4:3" and kv.get("window") == want and bool(win) and bool(eng) and win[0] <= cw and win[1] <= ch
                and abs(win[0] * 3 - win[1] * 4) <= 4 and abs(eng[0] * 3 - eng[1] * 4) <= 4 and abs(hf - 75.0) <= DEFAULT43_TOL
                and kv.get("ok") == "1", kv)


def check_proj(r, rows):
    p = {kv.get("ratio"): kv for kv in rows_of(rows, "aspect", "proj")}
    if not p:
        print("OPEN (Q4): AC-G2-4 not checked, the T aspect case printed no 'proj' rows")
        return
    v = {k: float(x.get("vfov", "nan")) for k, x in p.items()}
    h = {k: float(x.get("hfov", "nan")) for k, x in p.items()}
    keys = [k for k, _ in PROJ_RATIOS]
    r.check(f"the engine's vertical FOV is the same at 4:3, 16:9 and 21:9 within {VFOV_TOL} deg ({v})",
            all(k in v for k in keys) and max(v.values()) - min(v.values()) <= VFOV_TOL, v)
    r.check(f"the horizontal FOV widens with the ratio ({h})", all(k in h for k in keys) and h["4:3"] < h["16:9"] < h["21:9"], h)


def check_rects(r, rows):
    rc = rows_of(rows, "aspect", "rect")
    r.check("present rectangle rows exist", bool(rc))
    for kv in rc:
        try:
            bw, bh, dw, dh = (int(kv[k]) for k in ("bw", "bh", "dw", "dh"))
            dx, dy = int(kv["dx"]), int(kv["dy"])
            ww, wh = parse_size(kv["within"])
        except (KeyError, ValueError, TypeError):
            r.check("rect row is complete", False, kv)
            continue
        r.check(f"rect {bw}x{bh} -> {dw}x{dh}: letterbox or pillarbox only (shape kept within 1 px) and inside {ww}x{wh}",
                abs(dw * bh - dh * bw) <= max(bw, bh) and dx >= 0 and dy >= 0 and dx + dw <= ww and dy + dh <= wh and dw > 0 and dh > 0, kv)


def win_bb(aspect, launch):
    w, h = launch
    if aspect in WIN_RATIO:
        w, h = aspect_fit(*WIN_RATIO[aspect], w, h)
    return (w, h) if w >= MIN_SIZE[0] and h >= MIN_SIZE[1] and (w, h) != MIN_SIZE else FLOOR_SIZE


def win_min(w, h):
    return (w if 0 < w < MIN_SIZE[0] else MIN_SIZE[0], h if 0 < h < MIN_SIZE[1] else MIN_SIZE[1])


def check_window_rects(r, rows):
    got = {(kv.get("launch"), kv.get("aspect"), kv.get("win")): kv for kv in rows_of(rows, "window", "rect")}
    for lw, lh in WIN_LAUNCH:
        for a in WIN_ASPECTS:
            for ww, wh in WIN_SIZES:
                kv = got.get((f"{lw}x{lh}", a, f"{ww}x{wh}"), {})
                want = win_bb(a, (lw, lh))
                try:
                    bw, bh = parse_size(kv["bb"])
                    dx, dy, dw, dh = (int(kv[k]) for k in ("dx", "dy", "dw", "dh"))
                    ok = ((bw, bh) == want and 0 < dw <= ww and 0 < dh <= wh and dx >= 0 and dy >= 0 and dx + dw <= ww and dy + dh <= wh
                          and (dw == ww or dh == wh) and (dx <= 1 or dy <= 1) and abs(dw * bh - dh * bw) <= max(bw, bh)
                          and abs(dx - (ww - dw - dx)) <= 1 and abs(dy - (wh - dh - dy)) <= 1
                          and (not (a == "fit" and (lw, lh) == (ww, wh)) or (dw, dh) == (ww, wh)))
                except (KeyError, TypeError, ValueError):
                    ok = False
                r.check(f"window {a} picture {want[0]}x{want[1]} (launched on {lw}x{lh}) in a {ww}x{wh} window: centred, bars on one axis only, never stretched",
                        ok, kv)


def check_window_flags(r, rows):
    fl = {kv.get("start"): kv for kv in rows_of(rows, "window", "flags")}
    for w, h in WIN_STARTS[:2]:
        kv = fl.get(f"{w}x{h}", {})
        mn = win_min(w, h)
        r.check(f"window created {w}x{h}: resizable, minimum {mn[0]}x{mn[1]} points, full-screen capable (green button, Spaces on)",
                kv.get("available") == "1" and kv.get("resizable") == "1" and kv.get("min") == f"{mn[0]}x{mn[1]}"
                and kv.get("fullscreen_primary") == "1" and kv.get("fullscreen_none") == "0" and kv.get("spaces") == "1", kv)
    mins = {kv.get("start"): kv.get("min") for kv in rows_of(rows, "window", "minimum")}
    for w, h in WIN_STARTS:
        mn = win_min(w, h)
        r.check(f"minimum size for a {w}x{h} start is {mn[0]}x{mn[1]}", mins.get(f"{w}x{h}") == f"{mn[0]}x{mn[1]}", mins.get(f"{w}x{h}"))
    kv = next(iter(rows_of(rows, "window", "collection")), {})
    r.check("collection behavior: full-screen primary set, auxiliary and none cleared, other bits kept",
            kv.get("from") == "0x301" and kv.get("to") == "0x81", kv)
    menu = {kv.get("action"): kv.get("present") for kv in rows_of(rows, "window", "menu")}
    for a in WIN_MENU:
        r.check(f"menu action {a} is present (Hide, Quit, Minimize, Zoom, Enter Full Screen)", menu.get(a) == "1", menu.get(a))
    kv = next(iter(rows_of(rows, "window", "menu-again")), {})
    r.check("adding the menu items twice adds nothing the second time", kv.get("missing") == "0", kv)


def check_window_settings(r, rows):
    s = row_map(rows, "window")
    kv = s.get("settings-first-launch", {})
    r.check("first launch: no window or full-screen line is written, the toggles still are",
            kv.get("window_lines") == "0" and kv.get("fullscreen_lines") == "0" and kv.get("toggle_lines") == "1", kv)
    kv = s.get("settings-roundtrip", {})
    r.check("window 10,20,900,500 and full screen on survive a load and save twice, one line each, no warning, toggles kept",
            all(kv.get(k) == v for k, v in (("window", "1"), ("fullscreen", "1"), ("window_lines", "1"), ("fullscreen_lines", "1"),
                                            ("toggle_lines", "1"), ("warnings", "0"))), kv)
    kv = s.get("settings-negative-off", {})
    r.check("a window left of the origin (-5,-6) and full screen off survive", kv.get("window") == "1" and kv.get("fullscreen") == "1", kv)
    kv = s.get("settings-fullscreen-only", {})
    r.check("full screen on with no window rectangle survives and writes no window line", kv.get("window_lines") == "0" and kv.get("fullscreen") == "1", kv)
    for k in range(WIN_BAD_SETTINGS):
        kv = s.get(f"settings-bad-{k}", {})
        r.check(f"bad window setting #{k}: one log line, nothing written back, not fatal",
                kv.get("warnings") == "1" and kv.get("window_lines") == "0" and kv.get("fullscreen_lines") == "0", kv)


def check_window_state(r, rows):
    s = row_map(rows, "window")
    for lab, want in WIN_STATE.items():
        kv = s.get(lab, {})
        r.check(f"window state {lab}: {', '.join(f'{k} {v}' for k, v in want.items())}", all(kv.get(k) == v for k, v in want.items()), kv)
    rs = {kv.get("case"): kv for kv in rows_of(rows, "window", "restore")}
    for name, out, placed in WIN_RESTORE:
        kv = rs.get(name, {})
        r.check(f"restore {name}: window {out}, placed on its own display {placed}", kv.get("out") == out and kv.get("placed") == str(placed), kv)


def check_window(r, rows):
    r.check("window rows exist", bool(rows_of(rows, "window")))
    check_window_rects(r, rows)
    check_window_flags(r, rows)
    check_window_settings(r, rows)
    check_window_state(r, rows)
    kv = next(iter(rows_of(rows, "window", "result")), {})
    r.check("window case result bad=0", kv.get("bad") == "0", kv)


def check_controller(r, rows):
    got = {kv.get("name"): kv.get("type") for kv in rows_of(rows, "controller", "type")}
    for name, typ in CONTROLLER_CASES:
        r.check(f"controller {name!r} is {typ}", got.get(name) == typ, got.get(name))
    rm = row_map(rows, "controller")
    c, d, m = rm.get("controls", {}), rm.get("defaults", {}), rm.get("mappings", {})
    r.check("controls.txt south=e makes south press e, a bad key and an unknown button keep the default, look_speed=600 halves the look",
            c.get("south") == "e" and c.get("north") == "f" and c.get("back") == "c" and c.get("bad") == "3" and c.get("px") == "60", c)
    r.check("no controls.txt gives the built-in table and 120 px", d.get("south") == "space" and d.get("bad") == "0" and d.get("px") == "120", d)
    r.check("a gamecontrollerdb.txt with one line logs pad mappings 1", m.get("n") == "1", m)
    r.check("the selftest found nothing wrong", rm.get("result", {}).get("bad") == "0", rm.get("result"))


def lum(rows):
    return [[0.299 * row[i] + 0.587 * row[i + 1] + 0.114 * row[i + 2] for i in range(0, len(row), 3)] for row in rows]


def planes(rows):
    return [[[float(row[i + c]) for i in range(0, len(row), 3)] for row in rows] for c in range(3)]


def bilinear(P, w, h, ow, oh, box=None):
    x0, y0, x1, y1 = box or (0, 0, ow, oh)
    out = []
    for oy in range(y0, y1):
        sy = min(max((oy + 0.5) * h / oh - 0.5, 0.0), h - 1.0)
        iy = min(int(sy), h - 2) if h > 1 else 0
        fy = sy - iy
        ra, rb = P[iy], P[min(iy + 1, h - 1)]
        row = []
        for ox in range(x0, x1):
            sx = min(max((ox + 0.5) * w / ow - 0.5, 0.0), w - 1.0)
            ix = min(int(sx), w - 2) if w > 1 else 0
            fx = sx - ix
            jx = min(ix + 1, w - 1)
            row.append((ra[ix] * (1 - fx) + ra[jx] * fx) * (1 - fy) + (rb[ix] * (1 - fx) + rb[jx] * fx) * fy)
        out.append(row)
    return out


def mean_grad(L):
    h, w = len(L), len(L[0])
    s = n = 0
    for y in range(h - 1):
        a, b = L[y], L[y + 1]
        for x in range(w - 1):
            s += abs(a[x + 1] - a[x]) + abs(b[x] - a[x])
            n += 1
    return s / n


def mean_lap(L):
    h, w = len(L), len(L[0])
    s = n = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            s += abs(4 * L[y][x] - L[y - 1][x] - L[y + 1][x] - L[y][x - 1] - L[y][x + 1])
            n += 1
    return s / n


def chan_means(rows):
    n = len(rows) * (len(rows[0]) // 3)
    return [sum(sum(row[c::3]) for row in rows) / n for c in range(3)]


def region_mean(rows, box):
    x0, y0, x1, y1 = box
    n = (x1 - x0) * (y1 - y0)
    return [sum(sum(row[3 * x0 + c:3 * x1:3]) for row in rows[y0:y1]) / n for c in range(3)]


def black_frac(rows):
    z = bytes(3)
    return sum(1 for row in rows for i in range(0, len(row), 3) if row[i:i + 3] == z) / (len(rows) * (len(rows[0]) // 3))


def ints(s):
    return [int(x) for x in s.split(",")]


def scale_box(box, sx, sy, inset=0):
    x, y, w, h = box
    return (int(x * sx) + inset, int(y * sy) + inset, int((x + w) * sx) - inset, int((y + h) * sy) - inset)


def check_spatial(r, card, outs, patch, rgb, lines, targets=SPATIAL_TARGETS, grad_min=GRAD_MIN):
    cw, ch, crows = card
    src_mean = chan_means(crows)
    src_black = black_frac(crows)
    pbox = scale_box(patch, 1, 1, 1)
    r.check(f"the source card holds the random patch {rgb} (the frame came from dxvk)",
            all(abs(a - b) <= CARD_PATCH_TOL for a, b in zip(region_mean(crows, pbox), rgb)), (region_mean(crows, pbox), rgb))
    src_l = lum(crows)
    for (tw, th), (ow, oh, orows) in zip(targets, outs):
        tag = f"{tw}x{th}"
        r.check(f"spatial {tag}: PNG size is {tw}x{th}", (ow, oh) == (tw, th), (ow, oh))
        if (ow, oh) != (tw, th):
            continue
        sx, sy = tw / cw, th / ch
        mean = chan_means(orows)
        r.check(f"spatial {tag}: mean colour within {CARD_MEAN_TOL}/255 of the source", all(abs(a - b) <= CARD_MEAN_TOL for a, b in zip(mean, src_mean)), (mean, src_mean))
        bf = black_frac(orows)
        r.check(f"spatial {tag}: no black frame ({bf:.4f} black, source {src_black:.4f})", bf <= src_black + CARD_BLACK_EXTRA, bf)
        got = region_mean(orows, scale_box(patch, sx, sy, max(2, int(sx))))
        r.check(f"spatial {tag}: the random patch {rgb} is in the output", all(abs(a - b) <= CARD_PATCH_TOL for a, b in zip(got, rgb)), got)
        lb = scale_box(lines, sx, sy)
        out_l = lum(orows[lb[1]:lb[3]])
        out_l = [row[lb[0]:lb[2]] for row in out_l]
        ref_l = bilinear(src_l, cw, ch, tw, th, lb)
        g, gb = mean_grad(out_l), mean_grad(ref_l)
        r.check(f"spatial {tag}: edge gradient on the lines {g:.2f} is at least {grad_min:g} x bilinear {gb:.2f}", g >= grad_min * gb, (g, gb))


def mat_mul(a, b):
    return [sum(a[4 * i + k] * b[4 * k + j] for k in range(4)) for i in range(4) for j in range(4)]


def mat_inv(m):
    a = [[m[4 * i + j] for j in range(4)] + [1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for c in range(4):
        p = max(range(c, 4), key=lambda i: abs(a[i][c]))
        if abs(a[p][c]) < 1e-12:
            raise ValueError("singular matrix")
        a[c], a[p] = a[p], a[c]
        d = a[c][c]
        a[c] = [v / d for v in a[c]]
        for i in range(4):
            if i != c:
                f = a[i][c]
                a[i] = [v - f * u for v, u in zip(a[i], a[c])]
    return [a[i][4 + j] for i in range(4) for j in range(4)]


def mat_vec(m, v):
    return [sum(m[4 * i + k] * v[k] for k in range(4)) for i in range(4)]


def analytic_motion(depth, w, h, cur, prev):
    inv = mat_inv(cur)
    out = []
    for y in range(h):
        for x in range(w):
            ndc = [(x + 0.5) / w * 2 - 1, 1 - (y + 0.5) / h * 2, depth[y * w + x], 1.0]
            wp = mat_vec(inv, ndc)
            wp = [v / wp[3] for v in wp]
            c = mat_vec(prev, wp)
            px = (c[0] / c[3] + 1) / 2 * w - 0.5
            py = (1 - c[1] / c[3]) / 2 * h - 0.5
            out.append((px - x, py - y))
    return out


def check_motion(r, depth, w, h, cur, prev, motion):
    r.check("motion texture holds w*h pairs", len(motion) == w * h, len(motion))
    if len(motion) != w * h:
        return
    want = analytic_motion(depth, w, h, cur, prev)
    good = sum(1 for (a, b), (c, d) in zip(want, motion) if abs(a - c) <= MOTION_TOL and abs(b - d) <= MOTION_TOL)
    r.check(f"motion within {MOTION_TOL} px of the analytic motion for {good / (w * h):.4f} of pixels (need {MOTION_FRAC})", good / (w * h) >= MOTION_FRAC, good / (w * h))


def check_camera(r, rows):
    kv = rows_of(rows, "gfx", "camera")
    kv = kv[0] if kv else {}
    try:
        n, f, p = int(kv["frames"]), int(kv["found"]), int(kv["passthrough"])
    except (KeyError, ValueError):
        r.check("camera row is complete", False, kv)
        return
    r.check(f"the camera is found in {f} of {n} frames (need {CAMERA_FRAC:.0%}) and the rest are counted ({p})", n >= 600 and f >= CAMERA_FRAC * n and f + p == n, kv)


def taa_inside(k, px, py):
    a = math.radians(TAA["edge_a0"] + TAA["edge_da"] * k)
    sx0 = TAA["sq_x0"] + TAA["sq_vx"] * k
    return (px - TAA["edge_cx"]) * math.sin(a) - (py - TAA["edge_cy"]) * math.cos(a) > 0 or (
        sx0 <= px < sx0 + TAA["sq_size"] and TAA["sq_y"] <= py < TAA["sq_y"] + TAA["sq_size"])


def taa_cov(k):
    s = TAA["ss"]
    a = math.radians(TAA["edge_a0"] + TAA["edge_da"] * k)
    sa, ca = math.sin(a), math.cos(a)
    sx0 = TAA["sq_x0"] + TAA["sq_vx"] * k
    sq = TAA["sq_size"]
    out = []
    for y in range(TAA_SIZE):
        row = []
        oy = max(0.0, min(y + 1, TAA["sq_y"] + sq) - max(y, TAA["sq_y"]))
        for x in range(TAA_SIZE):
            d = (x + 0.5 - TAA["edge_cx"]) * sa - (y + 0.5 - TAA["edge_cy"]) * ca
            ov = oy * max(0.0, min(x + 1, sx0 + sq) - max(x, sx0))
            if d > 0.75 or ov >= 1.0:
                row.append(255.0)
            elif d < -0.75 and ov <= 0.0:
                row.append(0.0)
            else:
                hit = sum(taa_inside(k, x + (i + 0.5) / s, y + (j + 0.5) / s) for j in range(s) for i in range(s))
                row.append(255.0 * hit / (s * s))
        out.append(row)
    return out


def taa_aliased(k):
    return [[255.0 if taa_inside(k, x + 0.5, y + 0.5) else 0.0 for x in range(TAA_SIZE)] for y in range(TAA_SIZE)]


def check_taa(r, off, on, ref, tail=TAA_TAIL, gain=TAA_EDGE_GAIN, trail_tol=TAA_TRAIL_TOL):
    n = len(ref)
    ks = range(n - tail, n)
    e_off = e_on = c = 0.0
    for k in ks:
        for y in range(len(ref[k])):
            for x in range(len(ref[k][y])):
                v = ref[k][y][x]
                if 0 < v < 255:
                    e_off += abs(off[k][y][x] - v)
                    e_on += abs(on[k][y][x] - v)
                    c += 1
    r.check(f"taa has edge pixels to judge ({int(c)})", c > 0, c)
    if c:
        r.check(f"taa edge coverage error {e_on / c:.2f} is at most {gain:g} x the unsmoothed {e_off / c:.2f}", e_on / c <= gain * e_off / c, (e_on / c, e_off / c))
    s = t = 0.0
    for k in range(n // 2, n):
        left = TAA["sq_x0"] + TAA["sq_vx"] * k + 1
        for y in range(int(TAA["sq_y"]), int(TAA["sq_y"] + TAA["sq_size"])):
            for x in range(min(len(ref[k][y]), int(left))):
                if ref[k][y][x] == 0 and ref[k - 1][y][x] == 255:
                    s += abs(on[k][y][x] - 0)
                    t += 1
    r.check(f"taa trail behind the square has {int(t)} pixels", t > 0, t)
    if t:
        r.check(f"taa trail mean error {s / t:.2f} is at most {trail_tol:g}/255 (no ghosting)", s / t <= trail_tol, s / t)


def centroid(rows, skip=None):
    sx = sy = sw = 0.0
    for y, row in enumerate(rows):
        for x in range(len(row) // 3):
            if skip and skip[0] <= x < skip[2] and skip[1] <= y < skip[3]:
                continue
            v = max(0.0, 0.299 * row[3 * x] + 0.587 * row[3 * x + 1] + 0.114 * row[3 * x + 2] - INTERP_BG)
            sx, sy, sw = sx + v * (x + 0.5), sy + v * (y + 0.5), sw + v
    return (sx / sw, sy / sw) if sw else (float("nan"), float("nan"))


def check_interp(r, prev, nxt, mid, hud, hud_box):
    x, y, w, h = hud_box
    skip = (x, y, x + w, y + h)
    (p, n, m) = (centroid(i[2], skip) for i in (prev, nxt, mid))
    want = ((p[0] + n[0]) / 2, (p[1] + n[1]) / 2)
    d = math.hypot(m[0] - want[0], m[1] - want[1])
    r.check(f"made frame square centre {m} is within {INTERP_MID_TOL} px of the midpoint {want} (off by {d:.2f})", d <= INTERP_MID_TOL, d)
    worst = 0
    for yy in range(y, y + h):
        a, b = mid[2][yy][3 * x:3 * (x + w)], hud[2][yy][3 * x:3 * (x + w)]
        worst = max(worst, max(abs(p - q) for p, q in zip(a, b)))
    r.check(f"made frame HUD region equals the HUD layer within {INTERP_HUD_TOL:g}/255 (worst {worst})", worst <= INTERP_HUD_TOL, worst)


def check_cut(r, rows):
    c = rows_of(rows, "gfx", "cut")
    n = rows_of(rows, "gfx", "cut-none")
    r.check("the HUD cut index equals the first 2D draw", bool(c) and c[0].get("cut") == c[0].get("first2d") and c[0].get("cut", "-1") != "-1", c)
    r.check("a stream with no 2D draw has no cut and is counted as passthrough", bool(n) and n[0].get("cut") == "-1" and n[0].get("passthrough") == "1", n)


def queue_rows(text):
    out = []
    for l in text.splitlines():
        f = l.split("\t")
        if len(f) == 3 and f[0].isdigit() and f[2].isdigit():
            out.append((int(f[0]), f[1], int(f[2])))
    return out


def check_queue(r, rows, title):
    r.check(f"the queue trace has at least 60 ticks ({len(rows)})", len(rows) >= 60, len(rows))
    kinds = [k for _, k, _ in rows]
    r.check("shown frames alternate made and real", all(a != b for a, b in zip(kinds, kinds[1:])) and set(kinds) <= {"real", "made"}, kinds[:12])
    r.check("no frame is shown twice", len({(k, i) for _, k, i in rows}) == len(rows))
    t = [x[0] for x in rows]
    r.check("ticks only move forward", all(a < b for a, b in zip(t, t[1:])))
    content = [i if k == "real" else i - 0.5 for _, k, i in rows]
    r.check("content never goes back in time", all(a < b for a, b in zip(content, content[1:])), content[:12])
    stale = [(tk, i) for tk, k, i in rows if k == "made" and i != tk // QUEUE_FRAME_NS]
    r.check(f"the newest frame wins: each made frame targets the newest arrived frame ({len(stale)} stale)", not stale, stale[:3])
    real = [i for _, k, i in rows if k == "real"]
    r.check("every real frame follows the made frame for the same id", all(
        rows[n - 1][1] == "made" and rows[n - 1][2] == rows[n][2] for n in range(1, len(rows)) if rows[n][1] == "real"))
    m = RE_FPS_TITLE.match(title or "")
    r.check(f"the FPS menu reads N+N FPS ({title!r})", bool(m) and m[2] is not None and m[1] == m[2], title)


def check_hdr(r, rows, ramps):
    en = {kv.get("headroom"): kv.get("enabled") for kv in rows_of(rows, "hdr", "enable")}
    r.check("HDR is disabled at headroom 1.0", en.get("1.0") == "0", en)
    r.check("HDR is enabled at headroom 16.0", en.get("16.0") == "1", en)
    for head, ys in ramps.items():
        r.check(f"hdr ramp {head}: {HDR_STEPS} samples", len(ys) == HDR_STEPS, len(ys))
        if len(ys) != HDR_STEPS:
            continue
        xs = [i / 255 for i in range(HDR_STEPS)]
        low = [abs(y - x) for x, y in zip(xs, ys) if x <= HDR_SDR_TOP]
        r.check(f"hdr ramp {head}: values up to {HDR_SDR_TOP} unchanged within 1/255 (worst {max(low) * 255:.2f}/255)", max(low) <= HDR_TOL, max(low))
        r.check(f"hdr ramp {head}: monotonic", all(b >= a for a, b in zip(ys, ys[1:])) and all(math.isfinite(y) for y in ys))
        r.check(f"hdr ramp {head}: the top reaches the headroom ({ys[-1]:.3f})", abs(ys[-1] - head) <= HDR_TOL * max(1.0, head), ys[-1])


def check_gfx_hdr(r, rows):
    kv = rows_of(rows, "gfx", "hdr")
    kv = kv[0] if kv else {}
    try:
        mx, sp = float(kv["max"]), int(kv["spaces"])
    except (KeyError, ValueError):
        r.check("gfx hdr row is complete", False, kv)
        return
    r.check(f"the HDR route reads back a value above 1.0 ({mx}) and logs its colour spaces ({sp})", mx > 1.0 and sp > 0, kv)


def check_vfs(r, rows):
    s = row_map(rows, "vfs")
    a, b = s.get("loose-over-vpk", {}), s.get("vpk-only", {})
    r.check("a loose file takes the place of the same path in a VPK, whatever the letter case", a.get("source") == "loose" and a.get("sha1") == hashlib.sha1(LOOSE_BYTES).hexdigest(), a)
    r.check("a path only in the VPK still reads from the VPK", b.get("source") == "vpk" and b.get("sha1") == hashlib.sha1(VPK_BYTES).hexdigest(), b)


def fx_frames(log):
    m = RE_FX_FRAMES.findall(log)
    return SimpleNamespace(n=int(m[-1][0]), scaled=int(m[-1][1]), smoothed=int(m[-1][2]), interpolated=int(m[-1][3]), passthrough=int(m[-1][4]),
                           p50=float(m[-1][5]), p99=float(m[-1][6])) if m else None


def check_fx_support(r, rows, log):
    kv = rows_of(rows, "fx", "support")
    kv = kv[0] if kv else {}
    m = RE_FX_SUPPORT.findall(log)
    flags = {"spatial": kv.get("spatial"), "temporal": kv.get("temporal"), "interpolator": kv.get("interpolator"), "metal4fx": kv.get("metal4fx")}
    r.check("T fx prints supportsDevice for the three effects and supportsMetal4FX", all(v in ("0", "1") for v in flags.values()), kv)
    r.check("the rt: fx support log line agrees with the row", len(m) == 1 and list(m[0]) == [flags[k] for k in flags], (m, flags))
    return {k: flags[k] == "1" for k in flags}


def check_fx_forced(r, log, rc):
    got = RE_FX_UNSUPPORTED.findall(log)
    r.check("forced unsupported: exit 0", rc == 0, rc)
    r.check("forced unsupported: one rt: fx unsupported spatial line with a reason", len([g for g in got if g[0] == "spatial" and g[1]]) == 1, got)


def check_perf_numbers(r, label, kind, log, frames=FX_FRAMES, budget=True):
    st = fx_frames(log)
    r.check(f"{label}: an rt: fx frames line is in the log", st is not None)
    if not st:
        return
    limit = FX_P99[kind]
    count = getattr(st, FX_COUNT[kind])
    r.check(f"{label}: {st.n} frames (need >= {frames}) and {count} {FX_COUNT[kind]}", st.n >= frames and count >= 0.99 * st.n, st)
    r.check(f"{label}: added p50 {st.p50} ms and p99 {st.p99} ms reported apart", st.p50 <= st.p99, st)
    if budget:
        r.check(f"{label}: added p99 {st.p99} ms is at most {limit} ms", st.p99 <= limit, st.p99)
    else:
        print(f"INFO {label}: added p50 {st.p50} ms, p99 {st.p99} ms; the {limit} ms p99 budget is G6b's gate (R3 readback)")


def check_route(r, rows, log, rc):
    st = rows_of(rows, "fx-route", "step")
    na = RE_ROUTE_NA.findall(log)
    ok = all(kv.get("ok") == "1" for kv in st) and len(st) == 6
    r.check("fx-route: exit 0", rc == 0, rc)
    r.check("fx-route: either all six steps pass (bytes equal to R3) or one 'unavailable' line says why and R3 stays",
            ok and not na or (not ok and len(na) == 1 and bool(na[0])), (st, na))


def check_static_main(r, text):
    calls = [i for i, l in enumerate(text.splitlines()) if RE_INIT_CALL.search(l) and not re.match(r"\s*(void|int)\s", l)]
    lines = text.splitlines()
    guarded = [i for i in calls if any(re.search(r"\bif\s*\(.*player", lines[j]) or re.search(r"player\s*\)\s*\{?$", lines[j]) for j in range(max(0, i - 6), i))]
    dev = [i for i, l in enumerate(lines) if re.search(r"\brt_devmenu_init\s*\(", l)]
    r.check(f"main.c calls rt_hud_init once, on the path --run and --player share ({len(calls)} calls, {len(guarded)} behind a player test)", len(calls) == 1 and not guarded, (calls, guarded))
    r.check("rt_hud_init comes after rt_devmenu_init", bool(calls) and bool(dev) and calls[0] > dev[0], (calls, dev))


def check_static_bridge(r, text):
    m = re.search(r"\bv_present\s*\([^)]*\)\s*\{(.*?)\n\}", text, re.S)
    r.check("v_present takes its destination rectangle from rt_aspect_letterbox", bool(m) and "rt_aspect_letterbox" in m[1], bool(m))
    mv = fn_body(text, "rt_gfx_movie_blit")
    p = re.search(r"V_PRESENT\b.*?\)\)\(\s*dev\s*,\s*([^,]+?)\s*,\s*([^,]+?)\s*,", mv)
    r.check("rt_gfx_movie_blit records the letterbox rectangle for input and presents with a NULL one (the layer fits the picture)",
            "rt_aspect_letterbox" in mv and bool(p) and p[2] == "NULL", p[2] if p else None)
    d = re.search(r'^#define\s+DXVK_CONFIG_VALUE\s+"([^"\n]*)"', text, re.M)
    r.check("the DXVK_CONFIG the bridge sets holds d3d9.maxFrameLatency = 1", bool(d) and "d3d9.maxFrameLatency = 1" in d[1].split("; "), d[1] if d else None)


def check_engine(r, case, res):
    rows = rows_of(res.rows, case)
    oks = [kv for kv in rows if "ok" in kv or "same" in kv]
    bad = [kv for kv in oks if kv.get("ok", "1") != "1" or kv.get("same", "1") != "1"]
    res_row = row_map(res.rows, case).get("result", {})
    r.check(f"{case}: {len(oks)} checked rows, none failed", (bool(oks) or case == "engine-rows") and not bad, bad[:2])
    r.check(f"{case}: result bad=0 on a G2b build", res_row.get("bad") == "0" and res_row.get("built") == "1", res_row)
    n = len(re.findall(r"rt: aspect engine \d+x\d+", res.log))
    r.check(f"{case}: rt aspect engine lines, one per init ({n})", n == (3 if case == "engine-rows" else 1), n)


def reach_starts(new_hex):
    import capstone
    out = [0]
    for i in capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32).disasm(bytes.fromhex(new_hex), 0):
        out.append(i.address + i.size)
        if i.mnemonic == "jmp":
            break
    return out[:-1] or [0]


def site_spans(rows, bases):
    out = []
    for r in rows:
        lo = bases[r["module"]] + r["rva"]
        starts = reach_starts(r["new"]) if r.get("new") else [0]
        out.append((r["id"], lo, lo + len(bytes.fromhex(r["old"])), tuple(lo + s for s in starts)))
    return out


def check_lifted_sites(r, spans, labels):
    inside = [(i, f"loc_{a:08X}") for i, lo, hi, ok in spans for a in labels if lo <= a < hi and a not in ok]
    r.check(f"no lifted loc_ label sits inside a patched G2b site off the new code's reachable instructions ({len(spans)} sites, {len(labels)} labels)",
            bool(labels) and not inside, inside[:3])


def check_hud_census(r, client_c, hud_va, gamma_va, gamma_fn):
    reads = client_c.count(f"MEMD(0x{hud_va:08X})")
    r.check(f"the lifted client reads the HUD double {reads} times, the census count (want {HUD_READS})", reads == HUD_READS, reads)
    m = re.search(rf"^void sub_{gamma_fn:08X}\(void\)\n\{{\n.*?^\}}", client_c, re.S | re.M)
    body = m[0] if m else ""
    ok = f"MEMF(0x{gamma_va:08X})); /* fmul dword ptr [0x{gamma_va:08x}]" in body and f"MEMD(0x{hud_va:08X})" not in body
    r.check(f"the lifted gamma ramp sub_{gamma_fn:08X} multiplies by the 1/1024 float, not the HUD double", ok, body[:80] or "function missing")


def check_static_g2b(r, A):
    import tomllib
    with open(os.path.join(A.root, "tools", "m9", "aspect.toml"), "rb") as f:
        rows = tomllib.load(f)["row"]
    with open(os.path.join(A.root, "layout.toml"), "rb") as f:
        bases = {k: v["base"] for k, v in tomllib.load(f)["modules"].items()}
    r.check(f"aspect.toml: {len(rows)} rows, each owned by m9-g2b with an asm cite", rows and all(x["owner"] == "m9-g2b" and x.get("asm") for x in rows), None)
    gen = os.path.dirname(os.path.dirname(os.path.abspath(A.exe)))
    labels, texts = [], {}
    for mod in sorted({x["module"] for x in rows}):
        for p in glob.glob(os.path.join(gen, mod, "gen", "recomp_*.c")):
            with open(p, encoding="utf-8", errors="replace") as f:
                t = f.read()
            labels += [int(a, 16) for a in RE_LOC.findall(t)]
            texts[mod] = texts.get(mod, "") + t
    if not texts:
        r.check(f"generated C of the G2b build under {gen}", False, gen)
        return
    check_lifted_sites(r, site_spans(rows, bases), labels)
    check_hud_census(r, texts.get("client", ""), bases["client"] + 0x227EC8, bases["client"] + 0x22B97C, bases["client"] + 0x109950)


def check_static_docs(r, text):
    sec = re.search(r"^#+\s*Mods\b.*?(?=^#+\s|\Z)", text, re.S | re.M)
    body = sec[0] if sec else ""
    r.check("docs/recomp.md has a Mods section", bool(sec))
    for word, rule in (("loose", "loose files take the place of the same path in a VPK"), ("M8", "DLL-replacing mods go through M8"), ("ReShade", "mods that hook Windows do not work")):
        r.check(f"Mods section states the rule: {rule}", word.lower() in body.lower(), word)
    m9 = re.search(r"^#+\s*.*\bM9\b.*?(?=^#\s|\Z)", text, re.S | re.M)
    b9 = m9[0] if m9 else ""
    r.check("docs/recomp.md has an M9 section", bool(m9))
    for word in ("⌘1", "⌘7", "AC-G6-3", "p99", "licen"):
        r.check(f"M9 section mentions {word}", word.lower() in b9.lower(), word)


def check_tracked(r, files):
    bad = [f for f in files if re.search(r"\.(png|tsv|log|f32)$", f) and not f.startswith(("docs/", "tests/fixtures/"))]
    r.check(f"git ls-files holds no PNG, TSV, log or reference ({bad[:3]})", not bad, bad)


def refuse_past_menu(text):
    bad = []
    for n, raw in enumerate(text.splitlines(), 1):
        w = raw.split()
        if not w or w[0].startswith("#"):
            continue
        if w[0] not in LIVE_VERBS:
            bad.append(f"line {n}: '{w[0]}' goes past the main menu")
        elif w[0] == "wait" and (len(w) < 2 or w[1] not in LIVE_WAITS):
            bad.append(f"line {n}: wait {' '.join(w[1:])} goes past the main menu")
        elif w[0] == "chord" and (len(w) != 2 or w[1] not in CHORD_KEYS):
            bad.append(f"line {n}: chord {' '.join(w[1:])} is not cmd-1 to cmd-8")
    return bad


def check_at_menu(r, log):
    r.check("the run never left the main menu (no map load, no New Game)", not RE_MAP_LOG.search(log), RE_MAP_LOG.findall(log)[:2])


def check_live_toggles(r, log, merged):
    got = [(m[1], m[4]) for m in RE_TOGGLE.finditer(log)]
    na = [m[1] for m in RE_TOGGLE_NA.finditer(log)]
    seq = [n for n, _ in got] + na
    for n in CHORD_NAMES:
        r.check(f"cmd-chord for {n} logged a toggle line (changed or unavailable)", n in seq, seq)
    r.check("no toggle line came from anything but a chord", all(s == "chord" for _, s in got), got)
    r.check(f"dead chords {', '.join(DEAD_KEYS)} toggled nothing outside {', '.join(CHORD_NAMES)}", all(n in CHORD_NAMES for n in seq), seq)


def uptime():
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def run_t(exe, case, extra=(), overlay=None, env=None, timeout=300, player=False):
    o = overlay or tempfile.mkdtemp(prefix="m9-")
    argv = [exe, "--m9-selftest", case, *(["--player"] if player else []), "--overlay", o, *extra]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout, env={**os.environ, **(env or {})})
        rc, log = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        rc, log = None, ""
    return SimpleNamespace(rc=rc, log=log, rows=parse_m9(log), dir=os.path.join(o, "m9"), overlay=o, argv=argv)


def check_ran(r, label, res):
    r.check(f"{label}: finished with exit 0 (got {res.rc})", res.rc == 0, res.argv)
    r.check(f"{label}: no Traceback and no FAIL line", "Traceback" not in res.log and not RE_FAIL_LINE.search(res.log), res.log[-300:])


def read_file(res, name, mode="r"):
    with open(os.path.join(res.dir, name), mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def png_at(path):
    with open(path, "rb") as f:
        return read_png(f.read())


def finish_mode(body, rerun=False):
    if rerun:
        return m6.finish(m7.rerun7(lambda r, logs: body(r)))
    r = Rep()
    body(r)
    return 0 if r.ok else 1


class MergedError(ValueError):
    pass


def merged_of(A):
    out = tuple(x.strip() for x in (A.merged or "G1").split(","))
    bad = [x for x in out if x not in PIECES]
    if bad:
        raise MergedError(f"--merged names {bad!r}; the pieces are {', '.join(PIECES)} (exact case, comma separated)")
    return out


def has(A, piece):
    return piece in merged_of(A)


def staged(A, piece, what, fn):
    if has(A, piece):
        fn()
    else:
        print(f"SKIP {what}: {piece} is not merged")


def t_run(A, case, **kw):
    return run_t(A.exe, case, timeout=A.timeout or 300, **kw)


def cmd_menu(A):
    def body(r):
        a, b = t_run(A, "menu"), t_run(A, "menu", player=True)
        for lab, x, player in (("menu", a, False), ("menu --player", b, True)):
            check_ran(r, lab, x)
            check_menu(r, read_file(x, "menu.tsv"), merged_of(A), lab, player)
        check_menu_same(r, read_file(a, "menu.tsv"), read_file(b, "menu.tsv"))
    return finish_mode(body)


def cmd_input(A):
    def body(r):
        x = t_run(A, "input")
        check_ran(r, "input", x)
        check_input(r, x.rows, x.log, merged_of(A))
        staged(A, "G3", "mouse capture (AC-G3-1)", lambda: check_capture(r, x.rows))
        mp, xp = t_run(A, "menu", player=True), t_run(A, "input", player=True)
        check_ran(r, "input --player", xp)
        check_input(r, xp.rows, xp.log, merged_of(A), True)
        check_keys_work(r, read_file(t_run(A, "menu"), "menu.tsv"), x.rows)
        check_keys_work(r, read_file(mp, "menu.tsv"), xp.rows)
    return finish_mode(body)


def cmd_live(A):
    def body(r):
        for player in (False, True):
            x = t_run(A, "live", player=player)
            check_ran(r, "live --player" if player else "live", x)
            check_live(r, x.rows, player)
    return finish_mode(body)


def cmd_fps(A):
    def body(r):
        x = t_run(A, "fps")
        check_ran(r, "fps", x)
        check_fps(r, x.rows)
    return finish_mode(body)


def cmd_overlay(A):
    def body(r):
        x = t_run(A, "overlay")
        check_ran(r, "overlay", x)
        st = {s: ({"all": OVERLAY_A, "all2": OVERLAY_B, "fps": OVERLAY_A, "off": OVERLAY_A}[s], read_file(x, f"overlay-{s}.tsv"),
                  read_file(x, f"overlay-{s}.png", "rb")) for s in ("all", "all2", "fps", "off")}
        check_overlay(r, st, getattr(A, "canvas", OVERLAY_CANVAS))
        check_overlay_player(r, read_file(x, "overlay-all.tsv") + read_file(x, "overlay-all2.tsv"))
        xp = t_run(A, "overlay", player=True)
        check_ran(r, "overlay --player", xp)
        check_overlay_player(r, read_file(xp, "overlay-all.tsv") + read_file(xp, "overlay-all2.tsv"))
    return finish_mode(body)


def cmd_settings(A):
    def body(r):
        x = t_run(A, "settings")
        check_ran(r, "settings", x)
        check_settings(r, x.rows, x.log)
        staged(A, "G5", "DXVK_CONFIG (AC-G5-1)", lambda: check_dxvk(r, x.rows, x.log))
    return finish_mode(body)


def cmd_aspect(A):
    def body(r):
        def run():
            x = t_run(A, "aspect")
            check_ran(r, "aspect", x)
            check_aspect(r, x.rows)
            check_proj(r, x.rows)
            check_rects(r, x.rows)
        staged(A, "G2", "aspect (AC-G2-1, AC-G2-4, AC-G2-5)", run)

        def engine():
            for case in ENGINE_CASES:
                x = t_run(A, case)
                check_ran(r, case, x)
                check_engine(r, case, x)
        staged(A, "G2b", "engine aspect (AC-G2b-2 to AC-G2b-5)", engine)
    return finish_mode(body)


def cmd_window(A):
    def body(r):
        def run():
            x = t_run(A, "window")
            check_ran(r, "window", x)
            check_window(r, x.rows)
            x = t_run(A, "input")
            check_ran(r, "input", x)
            check_capture(r, x.rows)
        staged(A, "W", "game window (resize, full screen, remembered state, focus)", run)
    return finish_mode(body)


def cmd_controller(A):
    def body(r):
        def run():
            y = t_run(A, "controller")
            check_ran(r, "controller", y)
            check_controller(r, y.rows)
        staged(A, "G4", "controller (AC-G4-2)", run)
    return finish_mode(body)


def cmd_hdr(A):
    def body(r):
        def run():
            x = t_run(A, "hdr")
            check_ran(r, "hdr", x)
            ramps = {}
            for kv in rows_of(x.rows, "hdr", "ramp"):
                ys = [float(l.split("\t")[1]) for l in read_file(x, kv["file"]).splitlines() if re.match(r"[\d.]+\t", l)]
                ramps[float(kv["headroom"])] = ys
            r.check("hdr ramps exist", bool(ramps))
            check_hdr(r, x.rows, ramps)
        staged(A, "G9", "hdr (AC-G9-1, AC-G9-2)", run)
    return finish_mode(body)


def cmd_vfs(A):
    def body(r):
        def run():
            x = t_run(A, "vfs")
            check_ran(r, "vfs", x)
            check_vfs(r, x.rows)
        staged(A, "D", "vfs (AC-M1)", run)
    return finish_mode(body)


def read_floats(path):
    with open(path, "rb") as f:
        d = f.read()
    return list(struct.unpack("<%df" % (len(d) // 4), d[:len(d) // 4 * 4]))


def fx_g6(A, r):
    x = t_run(A, "fx")
    check_ran(r, "fx", x)
    support = check_fx_support(r, x.rows, x.log)
    check_fx_cycle(r, x.rows, support)
    m = t_run(A, "menu")
    check_menu_fx(r, read_file(m, "menu.tsv"), support, merged_of(A))
    env = {"RT_FX_FORCE_UNSUPPORTED": "1"}
    fm, fs = t_run(A, "menu", env=env), t_run(A, "fx-spatial", env=env)
    check_menu_forced(r, read_file(fm, "menu.tsv"))
    check_fx_forced(r, fs.log, fs.rc)
    sp = t_run(A, "fx-spatial")
    check_ran(r, "fx-spatial", sp)
    print(f"-- fx-spatial scaler builds (ms, compare launch 1 and 2): {fx_pipelines(sp.log)}")
    kv = (rows_of(sp.rows, "fx-spatial", "card") or [{}])[0]
    if kv:
        card = png_at(os.path.join(sp.dir, "card.png"))
        outs = [png_at(os.path.join(sp.dir, f"spatial-{w}x{h}.png")) for w, h in SPATIAL_TARGETS]
        check_spatial(r, card, outs, ints(kv["patch"]), ints(kv["rgb"]), ints(kv["lines"]))
    else:
        r.check("fx-spatial printed its card row", False)


def fx_g6b(A, r):
    rt = t_run(A, "fx-route")
    check_ran(r, "fx-route", rt)
    check_route(r, rt.rows, rt.log, rt.rc)


def fx_g7(A, r):
    rp = t_run(A, "reproject")
    check_ran(r, "reproject", rp)
    meta = json.loads(read_file(rp, "reproject.json"))
    check_motion(r, read_floats(os.path.join(rp.dir, "reproject-depth.f32")), meta["w"], meta["h"], meta["cur"], meta["prev"],
                 list(zip(*[iter(read_floats(os.path.join(rp.dir, "reproject-motion.f32")))] * 2)))
    ta = t_run(A, "fx-taa")
    check_ran(r, "fx-taa", ta)
    load = lambda tag: [[[float(v) for v in row[0::3]] for row in png_at(os.path.join(ta.dir, f"taa-{tag}-{k:02d}.png"))[2]] for k in range(TAA_FRAMES)]
    check_taa(r, load("off"), load("on"), [taa_cov(k) for k in range(TAA_FRAMES)])


def fx_g8(A, r):
    it = t_run(A, "fx-interp")
    check_ran(r, "fx-interp", it)
    sc = rows_of(it.rows, "fx-interp", "scene")
    r.check("fx-interp printed its scene row", bool(sc), sc)
    if sc:
        check_interp(r, *(png_at(os.path.join(it.dir, f"interp-{n}.png")) for n in ("prev", "next", "mid", "hud")), ints(sc[0]["hud"]))
    q = t_run(A, "present-queue")
    check_ran(r, "present-queue", q)
    fps = rows_of(q.rows, "present-queue", "fps")
    check_queue(r, queue_rows(read_file(q, "queue.tsv")), fps[0].get("title") if fps else None)


def fx_body(A, r):
    staged(A, "G6", "MetalFX spatial (AC-G6-1, AC-G6-2, AC-G6-5)", lambda: fx_g6(A, r))
    staged(A, "G6b", "R1 route probe", lambda: fx_g6b(A, r))
    staged(A, "G7", "edge smoothing (AC-G7-1, AC-G7-3)", lambda: fx_g7(A, r))
    staged(A, "G8", "frame interpolation (AC-G8-1, AC-G8-3)", lambda: fx_g8(A, r))


def cmd_fx(A):
    return locked(A, lambda: finish_mode(lambda r: fx_body(A, r), True))


def gfx_run(A, settings, tag):
    o = tempfile.mkdtemp(prefix=f"m9-gfx-{tag}-")
    with open(os.path.join(o, "settings.txt"), "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in settings.items()))
    try:
        p = subprocess.run([A.exe, "--gfx-selftest", "--overlay", o], capture_output=True, text=True, errors="replace", timeout=A.timeout or 600)
        rc, log = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        rc, log = None, ""
    return SimpleNamespace(rc=rc, log=log, rows=parse_m9(log), dir=o)


def slurp(path):
    with open(path, "rb") as f:
        return f.read()


def gfx_pngs(o):
    return {os.path.relpath(p, o): slurp(p) for p in glob.glob(os.path.join(o, "**", "*.png"), recursive=True)}


def check_reset(r, rows):
    rs = {(kv.get("from"), kv.get("to")): kv for kv in rows_of(rows, "gfx", "reset")}
    for pair in (("1280x1024", "3440x1440"), ("3440x1440", "1280x1024")):
        kv = rs.get(pair, {})
        r.check(f"Reset {pair[0]} to {pair[1]} works with default-pool resources released first", kv.get("ok") == "1" and kv.get("released") == "1", kv)


def gfx_body(A, r):
    on, off = gfx_run(A, {"overlays": "all"}, "ov-on"), gfx_run(A, {"overlays": "off"}, "ov-off")
    for lab, x in (("overlay on", on), ("overlay off", off)):
        r.check(f"gfx-selftest {lab}: exit 0 (got {x.rc})", x.rc == 0, x.log[-200:])
    check_identical(r, "AC-G1-5 overlay on and off", gfx_pngs(on.dir), gfx_pngs(off.dir))
    wide = gfx_run(A, {"aspect": "3440x1440"}, "wide") if any(has(A, p) for p in ("G2", "G7", "G8", "G9")) else None

    def g2():
        kv = (rows_of(wide.rows, "gfx", "mode-3440x1440") or [{}])[0]
        r.check("3440x1440 is listed for format 22 and a windowed device was created", kv.get("enum") == "1" and kv.get("created") == "1", kv)
        png = gfx_pngs(wide.dir).get(os.path.join("m9", "mode-3440x1440.png"))
        r.check("the 3440x1440 capture is 3440x1440", bool(png) and m4.png_size(png) == (3440, 1440), png and m4.png_size(png))
        check_reset(r, wide.rows)

    def g6():
        if A.baseline:
            base = {os.path.relpath(p, A.baseline): slurp(p) for p in glob.glob(os.path.join(A.baseline, "**", "*.png"), recursive=True)}
            check_identical(r, "AC-G6-4 picture off against the pre-M9 captures", {k: v for k, v in gfx_pngs(off.dir).items() if k in base}, base)
        else:
            r.check("AC-G6-4 needs --baseline DIR (the pre-M9 captures) once G6 is merged", False)

    staged(A, "G2", "3440x1440 mode and Reset (AC-G2-2, AC-G2-3)", g2)
    staged(A, "G6", "picture off byte-identical (AC-G6-4)", g6)
    staged(A, "G7", "camera found (AC-G7-2)", lambda: check_camera(r, wide.rows))
    staged(A, "G8", "HUD cut (AC-G8-2)", lambda: check_cut(r, wide.rows))
    staged(A, "G9", "HDR route (AC-G9-3)", lambda: check_gfx_hdr(r, wide.rows))


def cmd_gfx(A):
    return locked(A, lambda: finish_mode(lambda r: gfx_body(A, r), True))


def perf_run(A, runner=run_t, gate=m7.gate7, up=uptime, read=m7.readings, fmt=m7.fmt_readings):
    case, kind = ("fx-spatial", "spatial") if A.kind == "spatial" else ("fx-taa", "taa")
    piece = "G6" if kind == "spatial" else "G7"
    if not has(A, piece):
        print(f"REFUSED: perf {kind} needs {piece} merged (--merged)")
        return 1
    why = gate(perf=True)
    for w in why:
        print(f"REFUSED: {w}")
    if why:
        return 1
    r = Rep()
    print(f"-- setup: {fmt(read())}; uptime: {up()}; exe {A.exe}; T {case} --frames {A.frames}; tool window hidden and status pane closed: confirm by hand"
          + (f"; {A.note}" if A.note else ""))
    x = runner(A.exe, case, extra=("--frames", str(A.frames)), timeout=A.timeout or 900)
    check_ran(r, case, x)
    check_perf_numbers(r, f"{case} {A.frames} frames", kind, x.log, A.frames, kind != "spatial" or has(A, "G6b"))
    return 0 if r.ok else 1


def cmd_perf(A):
    return locked(A, lambda: perf_run(A))


def cmd_static(A):
    def body(r):
        def text(rel):
            with open(os.path.join(A.root, rel), encoding="utf-8") as f:
                return f.read()
        staged(A, "G1", "main.c start path (AC-G1-7)", lambda: check_static_main(r, text("runtime/main.c")))
        staged(A, "G2", "v_present letterbox (AC-G2-5)", lambda: check_static_bridge(r, text("shims/d3d9_bridge.c")))
        staged(A, "G2b", "patched sites and the HUD census (AC-G2b-4, AC-G2b-5)", lambda: check_static_g2b(r, A))
        staged(A, "W", "window.c resizable and full screen, the bridge layer fit",
               lambda: (check_static_window(r, text("runtime/window.c")), check_static_resize(r, text("shims/d3d9_bridge.c"))))

        def docs():
            check_static_docs(r, text("docs/recomp.md"))
            check_tracked(r, subprocess.run(["git", "-C", A.root, "ls-files"], capture_output=True, text=True).stdout.split())
        staged(A, "D", "docs and tracked files (AC-M2, AC-D1)", docs)
    return finish_mode(body)


def toggles_text(intro):
    with open(os.path.join(SCRIPTS, "toggles.txt"), encoding="utf-8") as f:
        return f.read().replace("$intro", intro)


def toggles_body(A, r, logs):
    text = toggles_text("sleep 20" if A.novid else f"wait bink 4 {m7.INTRO_FRAMES[-1] - 21}\nsleep 8")
    bad = refuse_past_menu(text)
    r.check("the live script stays at the main menu", not bad, bad)
    if bad:
        return
    o = m6.fresh(A, "m9-toggles")
    res = m6.go(r, logs, A, o, "toggles", text, close_after=A.close_after or m7.INTRO_CLOSE)
    check_at_menu(r, res.log)
    check_live_toggles(r, res.log, merged_of(A))


def toggles_drive(A):
    B = m7.ns(A)

    def body(r, logs):
        toggles_body(B, r, logs)
    with m7.patched(m6, RUNNER=guarded_runner(m6.RUNNER)):
        return m7.drive(B, lambda: m7.finish_run(body), flags=m7.player_flags(B), player=True)


def cmd_toggles(A):
    return locked(A, lambda: toggles_drive(A))


HUD_BOX = (0.6, 0.0, 1.0, 0.5)
HUD_MIN_IN = 0.01
HUD_MAX_OUT = 0.02


def region_diff(png_a, png_b, box=HUD_BOX, tol=24):
    wa, ha, ra = read_png(png_a)
    wb, hb, rb = read_png(png_b)
    if (wa, ha) != (wb, hb):
        raise ValueError(f"captures differ in size {(wa, ha)} vs {(wb, hb)}")
    x0, y0, x1, y1 = (int(box[0] * wa), int(box[1] * ha), int(box[2] * wa), int(box[3] * ha))
    inside = outside = n_in = n_out = 0
    for y in range(ha):
        a, b = ra[y], rb[y]
        if a == b:
            n_in += (x1 - x0) if y0 <= y < y1 else 0
            n_out += wa - ((x1 - x0) if y0 <= y < y1 else 0)
            continue
        for x in range(wa):
            d = max(abs(a[3 * x + c] - b[3 * x + c]) for c in range(3)) > tol
            if y0 <= y < y1 and x0 <= x < x1:
                n_in += 1
                inside += d
            else:
                n_out += 1
                outside += d
    return inside / max(n_in, 1), outside / max(n_out, 1)


def check_hud_live(r, on_png, off_png, box=HUD_BOX):
    try:
        fin, fout = region_diff(on_png, off_png, box)
    except (PngError, ValueError) as e:
        r.check("hud-live: both window captures read", False, e)
        return
    r.check(f"hud-live: the top-right region differs between overlays all and off ({fin:.4f} of its pixels, need {HUD_MIN_IN})", fin >= HUD_MIN_IN, fin)
    r.check(f"hud-live: the rest of the frame does not ({fout:.4f} of its pixels, at most {HUD_MAX_OUT})", fout <= HUD_MAX_OUT, fout)


def hud_live_run(A, mode, tag):
    o = tempfile.mkdtemp(prefix=f"m9-hudlive-{tag}-")
    with open(os.path.join(o, "settings.txt"), "w") as f:
        f.write(f"overlays={mode}\n")
    extra = ["--until", "menu", "--capture-after", str(A.capture_after)] + (["--display", str(A.display)] if A.display is not None else [])
    rc, log, dt, crashes, rows, shot = m4.run_live(A.exe, o, extra, A.timeout or 300, A.screencap_delay)
    return o, rc, log, crashes, shot


def hud_live_body(A, r):
    caps = {}
    for mode in ("all", "off"):
        o, rc, log, crashes, shot = hud_live_run(A, mode, mode)
        r.check(f"hud-live overlays={mode}: exit 0 (got {rc})", rc == 0, log[-200:])
        r.check(f"hud-live overlays={mode}: no crash report", not crashes, crashes)
        r.check(f"hud-live overlays={mode}: window captured", shot.get("rc") == 0 and os.path.exists(shot.get("path", "")), shot)
        caps[mode] = open(shot["path"], "rb").read() if shot.get("rc") == 0 and os.path.exists(shot.get("path", "")) else None
        print(f"  overlays={mode}: {shot.get('path')}")
    if caps["all"] and caps["off"]:
        check_hud_live(r, caps["all"], caps["off"])


def cmd_hud_live(A):
    def run():
        r = Rep()
        hud_live_body(A, r)
        return 0 if r.ok else 1
    return locked(A, run)


def guarded_runner(orig):
    def run(exe, o, argv, *a, **k):
        if "+map" in argv or "--" in argv:
            raise ValueError(f"M9 live runs stay at the main menu; refusing argv {argv}")
        return orig(exe, o, argv, *a, **k)
    return run


def locked(A, fn):
    if not need_exe(A):
        return 1
    why = m7.gate7()
    for w in why:
        print(f"REFUSED: {w}")
    if why:
        return 1
    with m7.game_lock(A.lock, A.lock_wait, owner=f"{LOCK_OWNER}-{A.mode}"):
        return fn()


def cmd_selftest(A):
    return selftest()


def build_parser():
    ap = argparse.ArgumentParser(prog="m9.py")
    sub = ap.add_subparsers(dest="mode", required=True)

    def add(name, fn, **opts):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        common = dict(exe={"default": X_DEFAULT}, timeout={"type": int}, merged={}, lock={"default": m7.LOCK_DEFAULT},
                      lock_wait={"type": float, "default": m7.LOCK_WAIT}) if name != "selftest" else {}
        for k, v in {**common, **opts}.items():
            p.add_argument("--" + k.replace("_", "-"), **v)

    flag = {"action": "store_true"}
    add("selftest", cmd_selftest)
    for n, f in (("menu", cmd_menu), ("input", cmd_input), ("fps", cmd_fps), ("overlay", cmd_overlay), ("settings", cmd_settings), ("live", cmd_live),
                 ("aspect", cmd_aspect), ("window", cmd_window), ("controller", cmd_controller), ("hdr", cmd_hdr), ("vfs", cmd_vfs), ("fx", cmd_fx)):
        add(n, f)
    add("gfx", cmd_gfx, baseline={})
    add("perf", cmd_perf, kind={"choices": ["spatial", "taa"], "required": True}, frames={"type": int, "default": FX_FRAMES}, note={})
    add("static", cmd_static, root={"default": ROOT})
    add("hud-live", cmd_hud_live, display={"type": int}, capture_after={"type": int, "default": 10}, screencap_delay={"type": float, "default": 3.0})
    add("toggles", cmd_toggles, overlay={}, game={"default": m6.GAME_DEFAULT}, manifest={"default": m6.MANIFEST_DEFAULT}, refdir={}, menu={},
        chargen={}, display={"type": int}, close_after={"type": int}, novid=flag)
    return ap


def synth_menu(merged=("G1",), swap=None, fps=None):
    items = list(ITEMS)
    if swap:
        i, j = swap
        items[i], items[j] = items[j], items[i]
    out = ["kind\ttitle\tkey\tmask\tenabled\ttooltip", "menu\tBloodlinesNative", "item\tAbout\t\t0\t1\t", "menu\tGame"]
    for n, k, piece in items:
        on = piece in merged
        tip = "" if on else "Not built yet"
        out.append(f"item\t{n}: x\t{k}\t{CMD_MASK if k else 0}\t{int(on)}\t{tip}")
    if fps:
        out += ["menu\t" + fps, "item\tLive frame rate\t\t0\t0\tFrames presented per second over the last two seconds"]
    return "\n".join(out) + "\n"


def synth_input(guest_key_on_chord=None, focus=None, player=False, drop=None):
    rows, log = [], []
    names = dict(MENU_KEYS)
    for n in range(1, 9):
        key = str(n)
        gk = 1 if guest_key_on_chord == n else 0
        if key in names:
            tg = 0 if drop == n else 1
            rows.append(m9_line("input", f"chord{n}", name=names[key], toggles=tg, guest_keys=gk, guest_chars=gk, dev=0))
            if tg:
                log.append(f"rt: toggle {names[key]} off -> on (chord)")
        else:
            rows.append(m9_line("input", f"chord{n}", name="-", toggles=0, guest_keys=gk, guest_chars=gk, dev=1 if drop == "dev" and n == 8 else 0))
        rows.append(m9_line("input", f"bare{n}", toggles=0, guest_keys=1, guest_chars=1))
    rows.append(m9_line("input", "both", name="overlays", toggles=1))
    log.append("rt: toggle overlays on -> off (menu)")
    rows.append(m9_line("input", "capture-default", relative=1, cursor=0))
    for lab, (rel, cur) in WIN_INPUT.items():
        stuck = focus == "stuck" and lab == "focus-lost-captured"
        hidden = focus == "hidden" and lab == "host-unfocused"
        rows.append(m9_line("input", lab, relative="1" if stuck else rel, cursor="0" if hidden else cur))
    return parse_m9("\n".join(rows)), "\n".join(log) + "\n"


def synth_window(bad=None):
    L = []

    def line(lab, **kv):
        L.append(" ".join(["m9", "window", lab] + [f"{k}={shlex.quote(str(v))}" for k, v in kv.items()]))

    for lw, lh in WIN_LAUNCH:
        for a in WIN_ASPECTS:
            bw, bh = win_bb(a, (lw, lh))
            for ww, wh in WIN_SIZES:
                if ww * bh <= wh * bw:
                    dw, dh = ww, (bh * ww + bw // 2) // bw
                else:
                    dw, dh = (bw * wh + bh // 2) // bh, wh
                dx, dy = (ww - dw) // 2, (wh - dh) // 2
                if (lw, lh, a, ww, wh) == (2056, 1329, "16:9", 900, 500):
                    if bad == "stretch":
                        dx, dy, dw, dh = 0, 0, ww, wh
                    elif bad == "off-centre":
                        dx, dy = 0, 0
                    elif bad == "both-bars":
                        dw, dh = dw * 9 // 10, dh * 9 // 10
                        dx, dy = (ww - dw) // 2, (wh - dh) // 2
                line("rect", launch=f"{lw}x{lh}", aspect=a, bb=f"{bw}x{bh}", win=f"{ww}x{wh}", dx=dx, dy=dy, dw=dw, dh=dh)
    for w, h in WIN_STARTS[:2]:
        mn = win_min(w, h)
        line("flags", available=1, start=f"{w}x{h}", resizable=0 if bad == "fixed" else 1, min="0x0" if bad == "no-min" else f"{mn[0]}x{mn[1]}",
             fullscreen_primary=0 if bad == "no-fs" else 1, fullscreen_none=0, spaces=1)
    for w, h in WIN_STARTS:
        mn = win_min(w, h)
        line("minimum", start=f"{w}x{h}", min=f"{mn[0]}x{mn[1]}")
    line("collection", **{"from": "0x301", "to": "0x301" if bad == "collection" else "0x81"})
    for a in WIN_MENU:
        line("menu", action=a, present=0 if bad == "menu" and a == "toggleFullScreen:" else 1)
    line("menu-again", added_first=5, missing=0)
    line("settings-first-launch", window_lines=1 if bad == "first-launch" else 0, fullscreen_lines=0, toggle_lines=1)
    line("settings-roundtrip", window=0 if bad == "lost" else 1, fullscreen=1, window_lines=1, fullscreen_lines=1, toggle_lines=1, warnings=0)
    line("settings-negative-off", window=1, fullscreen=1)
    line("settings-fullscreen-only", window_lines=0, fullscreen=1)
    for k in range(WIN_BAD_SETTINGS):
        accepted = bad == "accepted" and k == 0
        line(f"settings-bad-{k}", warnings=0 if accepted else 1, window_lines=1 if accepted else 0, fullscreen_lines=0)
    for lab, want in WIN_STATE.items():
        kv = dict(want)
        if bad == "junk-rect" and lab == "state-enter":
            kv["rect"] = "0,0,2056,1329"
        if bad == "fs-stuck" and lab == "state-leave":
            kv["fullscreen"] = "1"
        if bad == "wrap" and lab == "state-wrap":
            kv["rect"] = "100,100,1280,1024"
        line(lab, **kv)
    for name, out, placed in WIN_RESTORE:
        if bad == "no-clamp" and name == "too-big":
            out = "100,100,3000,2000"
        if bad == "offscreen" and name == "offscreen":
            out, placed = "5000,100,900,500", 1
        line("restore", case=name, out=out, placed=placed)
    line("result", bad=0)
    return parse_m9("\n".join(L))


def synth_fps(short=None, player_start=1, player_fps=200):
    rows = [m9_line("fps", "player", fps=player_fps, p50="5.00", **{"frames-start": player_start})]
    for name in ("steady", "spike", "interp"):
        title, p50, p99 = fps_model(fps_series(name), short=short == name)
        rows.append(m9_line("fps", name, title=title, p50=f"{p50:.2f}", p99=f"{p99:.2f}"))
    return parse_m9("\n".join(rows))


def overlay_png(n_lines, w=320, h=256):
    rows = [bytes(OVERLAY_BG) * w for _ in range(h)]
    for y in range(4, 4 + 8 * n_lines + 4 if n_lines else 4):
        if n_lines:
            rows[y] = rows[y][:3 * (w - 110)] + b"\xf0\xf0\xf0" * 100 + rows[y][3 * (w - 10):]
    return write_png(w, h, rows)


def synth_overlay(stale=False, player=False):
    st = {}
    for s, o in (("all", OVERLAY_A), ("all2", OVERLAY_A if stale else OVERLAY_B), ("fps", OVERLAY_A), ("off", OVERLAY_A)):
        lines = overlay_lines(o, "all" if s == "all2" else s, player)
        st[s] = ({"all": OVERLAY_A, "all2": OVERLAY_B, "fps": OVERLAY_A, "off": OVERLAY_A}[s], "\n".join(lines) + ("\n" if lines else ""), overlay_png(len(lines)))
    return st


def synth_live(bad=None, player=False):
    rows = [m9_line("live", "overlays", lines="9,1,0", back=1, changed=0 if bad == "overlays" else 1),
            m9_line("live", "picture", mode_on="0,1,0", changed=0 if bad == "picture" else 1),
            m9_line("live", "dead-keys", toggles=1 if bad == "dead" else 0, ok=0 if bad == "dead" else 1),
            m9_line("live", "result", bad=1 if bad else 0)]
    return parse_m9("\n".join(rows))


def synth_settings(bad=None):
    rows = [m9_line("settings", f"roundtrip-{n}", wrote=NONDEFAULT[n], read=NONDEFAULT[n] if bad != "read" else DEFAULTS_RUN[n]) for n in NAMES]
    rows.append(m9_line("settings", "defaults-run", **DEFAULTS_RUN))
    rows.append(m9_line("settings", "defaults-player", **DEFAULTS_PLAYER))
    rows.append(m9_line("settings", "no-file", **DEFAULTS_RUN))
    rows.append(m9_line("settings", "unknown-name", unknown_lines=1, **DEFAULTS_RUN))
    rows.append(m9_line("settings", "bad-value", bad_name="aspect", bad_lines=1 if bad != "fatal" else 0, **DEFAULTS_RUN))
    rows.append(m9_line("settings", "readonly", readonly_lines=1, survived=1 if bad != "fatal" else 0))
    rows.append(m9_line("settings", "options-video", before_stage6=0, after_stage6=0 if bad == "opt" else 1, reload="1728x1080"))
    rows.append(m9_line("settings", "held-step", saved="native", mask=5, value="native" if bad == "held" else "off", file_kept=1,
                        save_kept=0 if bad == "overwritten" else 1))
    user = "d3d9.maxFrameLatency = 1"
    rows.append(m9_line("settings", "dxvk-set", value="d3d9.a = 1; d3d9.samplerAnisotropy = 8" if bad == "eight" else "d3d9.samplerAnisotropy = 16" if bad == "always" else "d3d9.a = 1"))
    rows.append(m9_line("settings", "player-native", player="native" if bad == "pn" else "off", warn_lines=0, saved_off=1, dev="native"))
    rows.append(m9_line("settings", "dropped-keys", warn_lines=1 if bad == "warn" else 0))
    rows.append(m9_line("settings", "dxvk-user", value="d3d9.samplerAnisotropy = 16" if bad == "overwrite" else user, user=user, kept=1))
    return parse_m9("\n".join(rows)), "rt: gfx env DXVK_CONFIG=d3d9.maxFrameLatency = 1 (kept)\n"


def synth_aspect(bad=None):
    rows = [m9_line("aspect", "parse", input=s, kind=k) for s, k in PARSE_CASES]
    for st in ASPECT_STEPS:
        for w, h in WINDOWS:
            ow, oh = aspect_size(st, w, h)
            if bad == "off-by-one" and st == "16:9" and (w, h) == (1280, 1024):
                oh += 1
            rows.append(m9_line("aspect", "size", state=st, win=f"{w}x{h}", out=f"{ow}x{oh}"))
    for frm, ratio, to in NEXT_CASES:
        rows.append(m9_line("aspect", "next", **{"from": frm, "ratio": f"{ratio:.4f}", "to": "fit" if bad == "no-skip" and to != "fit" and frm == "16:9" else to}))
    for v, w, h in HFOV_CASES:
        o = hfov_deg(v, h, w) if bad == "swapped" else hfov_deg(v, w, h)
        rows.append(m9_line("aspect", "hfov", vfov=v, w=w, h=h, out=f"{o:.4f}"))
    for name, ratio in PROJ_RATIOS:
        v = PROJ_VFOV if bad != "vert-" else 2 * math.degrees(math.atan(math.tan(math.radians(PROJ_VFOV) / 2) * (4 / 3) / ratio))
        h = hfov_deg(v, ratio, 1)
        rows.append(m9_line("aspect", "proj", ratio=name, vfov=f"{v:.4f}", hfov=f"{h:.4f}"))
    for n, (dw, dh, cw, ch) in enumerate(DEFAULT43_DESKTOPS):
        ww, wh = (cw, ch) if bad == "default-fit" else aspect_fit(4, 3, cw, ch)
        hf = math.degrees(2 * math.atan(math.tan(math.radians(37.5)) * 0.75 * ww / wh))
        rows.append(m9_line("aspect", "default43", desktop=f"{dw}x{dh}", client=f"{cw}x{ch}", player=n & 1, start="fit" if bad == "default-fit" else "4:3",
                            window=f"{ww}x{wh}", engine=f"{ww}x{wh}", hfov75=f"{hf:.3f}", ok=1))
    for n in range(4):
        rows.append(m9_line("aspect", "size-keep", state="1920x1440", win="3440x1316", out="1920x1440", ok=0 if bad == "keep" and n == 0 else 1))
        rows.append(m9_line("aspect", "size-window", usable="3440x1316", ok=0 if bad == "win" and n == 0 else 1))
    rows.append(m9_line("aspect", "pp-size", zero="1594x1196", kept="800x600", full="0x0", ok=1))
    rows.append(m9_line("aspect", "rect", bw=1280, bh=720, dx=0, dy=152, dw=2560 if bad == "stretch" else 1280, dh=1136 if bad == "stretch" else 720, within="1280x1024"))
    return parse_m9("\n".join(rows))


def to_rows(P):
    return [bytes(max(0, min(255, int(round(P[c][y][x])))) for x in range(len(P[0][0])) for c in range(3)) for y in range(len(P[0]))]


def unsharp(p, amt):
    h, w = len(p), len(p[0])
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            ys, xs = range(max(0, y - 1), min(h, y + 2)), range(max(0, x - 1), min(w, x + 2))
            blur = sum(p[j][i] for j in ys for i in xs) / (len(ys) * len(xs))
            row.append(p[y][x] + amt * (p[y][x] - blur))
        out.append(row)
    return out


def make_card(w=160, h=128, patch=(100, 70, 24, 16), rgb=(200, 40, 90), with_patch=True):
    rows = [bytearray(b"".join(bytes((g, g, g)) for g in (int(40 + 150 * x / w) for x in range(w)))) for _ in range(h)]
    for x in range(8, 56, 6):
        for y in range(8, 48):
            rows[y][3 * x:3 * x + 3] = b"\xff\xff\xff"
    if with_patch:
        for y in range(patch[1], patch[1] + patch[3]):
            rows[y][3 * patch[0]:3 * (patch[0] + patch[2])] = bytes(rgb) * patch[2]
    return w, h, [bytes(r) for r in rows]


def upscale(card, tw, th, sharp=0.8):
    w, h, rows = card
    P = [bilinear(p, w, h, tw, th) for p in planes(rows)]
    if sharp:
        P = [unsharp(p, sharp) for p in P]
    return tw, th, to_rows(P)


def blank(w, h, v):
    return [bytearray([v] * (3 * w)) for _ in range(h)]


def paint(rows, x0, y0, w, h, v, rgb=None):
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            rows[y][3 * x:3 * x + 3] = bytes(rgb or (v, v, v))
    return rows


def fn_body(text, name):
    m = re.search(r"\b" + re.escape(name) + r"\s*\([^)]*\)\s*\{(.*?)\n\}", text, re.S)
    return m[1] if m else ""


def check_static_window(r, text):
    r.check("rt_window_init turns the Spaces full screen hint on before SDL_Init",
            0 <= fn_body(text, "rt_window_init").find("SDL_HINT_VIDEO_MAC_FULLSCREEN_SPACES") < fn_body(text, "rt_window_init").find("SDL_Init"))
    r.check("rt_win_sdl configures the main window (resizable, minimum size, full-screen collection behavior)", "rt_window_configure(" in fn_body(text, "rt_win_sdl"))
    cfg = fn_body(text, "rt_window_configure") + fn_body(text, "set_minimum")
    r.check("the configuration sets resizable, a minimum size and the full-screen primary collection behavior",
            all(x in cfg for x in ("SDL_SetWindowResizable(sw, true)", "SDL_SetWindowMinimumSize", "setCollectionBehavior:")), cfg[:120])
    pump = fn_body(text, "rt_pump")
    r.check("the pump follows move, resize, enter and leave full screen and the display change",
            all(x in pump for x in ("SDL_EVENT_WINDOW_MOVED", "SDL_EVENT_WINDOW_RESIZED", "SDL_EVENT_WINDOW_ENTER_FULLSCREEN",
                                    "SDL_EVENT_WINDOW_LEAVE_FULLSCREEN", "SDL_EVENT_WINDOW_DISPLAY_CHANGED")), pump[:80])
    closes = re.findall(r"case SDL_EVENT_(?:WINDOW_CLOSE_REQUESTED|QUIT):(.*?)break;", pump, re.S)
    r.check("both quit paths save the window state, then post WM_CLOSE to the game", len(closes) == 2 and all("window_flush()" in c and "0x0010" in c for c in closes), closes)
    rs = fn_body(text, "rt_win_resize").splitlines()
    guard = next((i for i, l in enumerate(rs) if re.search(r"\bfull\s*=.*SDL_WINDOW_FULLSCREEN", l)), None)
    acts = [i for i, l in enumerate(rs) if re.search(r"\b(SDL_SetWindowSize|rt_window_place)\s*\(", l)]
    unguarded = [rs[i].strip() for i in acts if not re.search(r"!\s*full\b", rs[i])]
    r.check("a full-screen window ignores the engine's resize request (the guard is read first and gates every resize and placement)",
            guard is not None and bool(acts) and guard < min(acts) and not unguarded, (guard, acts, unguarded))
    r.check("the window state goes live only after the first present", "rt_gfx_present_rects" in fn_body(text, "window_tick"))


def check_static_resize(r, text):
    for name in ("v_present", "rt_gfx_movie_blit"):
        b = fn_body(text, name)
        r.check(f"{name} fits the game layer to its drawable and never drains or passes a letterbox rect to the host Present",
                "rt_gfx_layer_fit(" in b and "resize_drain" not in b, b[:80])
    for name in ("d_createdevice", "v_reset"):
        r.check(f"{name} fills a 0x0 back buffer from the guest window", "rt_gfx_pp_size(" in fn_body(text, name), name)
    r.check("no resize drain or its gate is left in the bridge", "resize_drain" not in text and "s_drain_" not in text)
    rs = fn_body(text, "v_reset")
    g, s = rs.find("if (hr) return"), rs.find("rt_aspect_engine_set(")
    r.check("v_reset hands the new back-buffer size to rt_aspect_engine_set after a successful Reset", 0 <= g < s, (g, s))


def hud_pattern(rows, box, shift=0):
    x, y, w, h = box
    for j in range(h):
        for i in range(w):
            v = 60 + int(180 * ((i + shift) % w) / w)
            rows[y + j][3 * (x + i):3 * (x + i) + 3] = bytes((v, 255 - v, 90))
    return rows


def synth_interp(hud_in_colour=False):
    w, h, box = 96, 48, (60, 4, 32, 12)
    mk = lambda x0, shift=0: (w, h, [bytes(r) for r in hud_pattern(paint(blank(w, h, INTERP_BG), x0, 24, 8, 8, 255), box, shift)])
    hud = (w, h, [bytes(r) for r in hud_pattern(blank(w, h, INTERP_BG), box)])
    return mk(8), mk(24), mk(16, 5 if hud_in_colour else 0), hud, box


def depth_scene(w, h):
    return [0.55 + 0.4 * (((x * 7 + y * 3) % 17) / 17) for y in range(h) for x in range(w)]


def matrices():
    zn, zf, f = 0.1, 100.0, 1.7
    proj = [f, 0, 0, 0, 0, f, 0, 0, 0, 0, zf / (zf - zn), -zn * zf / (zf - zn), 0, 0, 1, 0]
    ca, sa = math.cos(0.02), math.sin(0.02)
    cur = mat_mul(proj, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 5, 0, 0, 0, 1])
    prev = mat_mul(proj, [ca, 0, sa, 0.3, 0, 1, 0, 0.1, -sa, 0, ca, 5, 0, 0, 0, 1])
    return cur, prev


def synth_queue(bad=None):
    tick, rows, want, last = 0, [], "made", -1
    period = 1_000_000_000 // 165
    for _ in range(400):
        tick += period
        i = tick // QUEUE_FRAME_NS
        if want == "made" and i > last:
            rows.append((tick, "made", i - 1 if bad == "oldest" else i))
            last, want = i, "real"
        elif want == "real":
            rows.append((tick, "real", last))
            want = "made"
    if bad == "repeat":
        rows.insert(5, rows[4])
    return rows


def synth_ramp(head, dip=False):
    ys = [x / 255 if x / 255 <= HDR_SDR_TOP else HDR_SDR_TOP + (head - HDR_SDR_TOP) * ((lambda t: t * t * (3 - 2 * t))((x / 255 - HDR_SDR_TOP) / (1 - HDR_SDR_TOP))) for x in range(HDR_STEPS)]
    if dip:
        ys[230] -= 0.3
    return ys


def synth_fx_log(n=3000, p50=0.4, p99=1.5, scaled=None, smoothed=0):
    return f"rt: fx frames {n} scaled {n if scaled is None else scaled} smoothed {smoothed} interpolated 0 passthrough 0 added p50 {p50} p99 {p99}\n"


def selftest():
    n = {"ok": 0}
    bad = []
    caught = []

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

    def good(label, fn, *args, **kw):
        r = result(fn, *args, **kw)
        expect(f"{label} passes ({r.failed[:3]})", r.ok)

    def mutation(num, desc, fn, *args, **kw):
        r = result(fn, *args, **kw)
        n["ok"] += 1
        if r.ok:
            bad.append(f"mutation {num} {desc} not caught")
            print(f"FAIL mutation {num} {desc}: NOT caught", file=sys.__stdout__)
        else:
            caught.append(num)
            print(f"MUTATION {num} {desc}: caught by FAIL {r.failed[0]}" + (f" (+{len(r.failed) - 1} more)" if len(r.failed) > 1 else ""), file=sys.__stdout__)

    def raises(exc, fn, *args):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                fn(*args)
        except exc:
            return True
        except Exception:
            return False
        return False

    tmp = tempfile.mkdtemp(prefix="m9-selftest-")
    try:
        merged = ("G1", "G2", "G3", "G5", "G6")
        good("G1-1 menu (G1 merged)", check_menu, synth_menu(), ("G1",))
        good("G1-1 menu (G1 to G6 merged)", check_menu, synth_menu(merged), merged)
        good("G1-1 menu (all merged)", check_menu, synth_menu(tuple(p for _, _, p in ITEMS if p)), tuple(p for _, _, p in ITEMS if p))
        mutation(1, "menu.tsv with cmd-1 and cmd-2 swapped", check_menu, synth_menu(swap=(0, 1)), ("G1",))
        expect("G1-1 an item of an unmerged piece left enabled fails", not result(check_menu, synth_menu(("G1", "G6")), ("G1",)).ok)
        expect("G1-1 a disabled item with no tooltip fails", not result(check_menu, synth_menu().replace("Not built yet", ""), ("G1",)).ok)
        expect("G1-1 a wrong modifier mask fails", not result(check_menu, synth_menu().replace(f"\t{CMD_MASK}\t", "\t1\t", 1), ("G1",)).ok)
        expect("G1-1 Game not after the app menu fails", not result(check_menu, synth_menu().replace("menu\tGame", "menu\tGames"), ("G1",)).ok)
        expect("G1-1 menu --player must equal menu", not result(check_menu_same, synth_menu(), synth_menu(("G1", "G6"))).ok)
        good("G1-7 menu --player equals menu", check_menu_same, synth_menu(), synth_menu())
        good("G1-7 menu --player", check_menu, synth_menu(), ("G1",), "menu --player", True)
        mutation(35, "an FPS readout in the menu bar", check_menu, synth_menu(fps="200 FPS"), ("G1",))
        mutation(88, "Developer info in the menu", check_menu, synth_menu().replace("menu\tGame\n", "menu\tGame\nitem\tDeveloper info\t4\t" + str(CMD_MASK) + "\t1\t\n"), ("G1",))
        mutation(110, "Controller type in the menu", check_menu, synth_menu().replace("menu\tGame\n", "menu\tGame\nitem\tController type: auto\t6\t" + str(CMD_MASK) + "\t1\t\n"), ("G1",))
        mutation(111, "Aspect in the menu", check_menu, synth_menu().replace("menu\tGame\n", "menu\tGame\nitem\tAspect: 16:9\t4\t" + str(CMD_MASK) + "\t1\t\n"), ("G1",))
        mutation(112, "SDL's dead Settings item in a menu", check_menu, synth_menu().replace("item\tAbout\t\t0\t1\t\n", "item\tAbout\t\t0\t1\t\nitem\tSettings…\t,\t" + str(CMD_MASK) + "\t0\tx\n"), ("G1",))
        mutation(113, "Close in a menu", check_menu, synth_menu().replace("menu\tGame\n", "menu\tGame\nitem\tClose\tw\t" + str(CMD_MASK) + "\t1\t\n"), ("G1",))
        mutation(114, "Picture mode on cmd-3, the old chord", check_menu, synth_menu().replace("item\tPicture mode: x\t2\t", "item\tPicture mode: x\t3\t"), ("G1",))
        mutation(116, "a third menu after Game", check_menu, synth_menu() + "menu\tExtra\n", ("G1",))
        mutation(89, "a dropped feature (Edge smoothing) in the menu", check_menu, synth_menu().replace("menu\tGame\n", "menu\tGame\nitem\tEdge smoothing: off\t5\t" + str(CMD_MASK) + "\t1\t\n"), ("G1",))
        mutation(90, "Frame interpolation in the menu", check_menu, synth_menu().replace("menu\tGame\n", "menu\tGame\nitem\tFrame interpolation: off\t2\t" + str(CMD_MASK) + "\t1\t\n"), ("G1",))
        mutation(91, "HDR in the menu", check_menu, synth_menu().replace("menu\tGame\n", "menu\tGame\nitem\tHDR: off\t\t0\t1\t\n"), ("G1",))
        mutation(92, "Texture filtering in the menu", check_menu, synth_menu().replace("menu\tGame\n", "menu\tGame\nitem\tTexture filtering: off\t\t0\t1\t\n"), ("G1",))
        mutation(93, "'(next launch)' in a menu title", check_menu, synth_menu().replace("Picture mode: x", "Picture mode: native (next launch)"), ("G1",))
        mutation(94, "'16×' mis-encoded in a menu title", check_menu, synth_menu().replace("Picture mode: x", "Picture mode: 16Ã—"), ("G1",))
        km = synth_menu().replace("item\tPicture mode: x\t2\t", "item\tPicture mode: x\t9\t")
        mutation(95, "a listed row with a key that does nothing", check_keys_work, km, synth_input()[0])
        good("G1-2 every listed key works", check_keys_work, synth_menu(), synth_input()[0])

        rows, log = synth_input()
        good("G1-2 input", check_input, rows, log)
        good("G1-2 input in player mode", check_input, *synth_input(player=True), ("G1",), True)
        mutation(96, "cmd-8 requesting Developer info", check_input, *synth_input(drop="dev"))
        mutation(97, "cmd-1 giving no toggle", check_input, *synth_input(drop=1))
        lr = synth_live()
        good("live: every kept row changes its state", check_live, lr)
        good("live: player mode", check_live, synth_live(player=True), True)
        for i, (bn, bd) in enumerate((("overlays", "overlay lines that do not change"), ("picture", "a picture mode that does not change"),
                                      ("dead", "a dead key that toggles"))):
            mutation(99 + i, bd, check_live, synth_live(bn))
        mutation(104, "a live row for a dropped control (controller)", check_live, parse_m9("\n".join(f"m9 live {n} changed=1" for n in ("overlays", "picture", "controller")) + "\nm9 live dead-keys toggles=0 ok=1\nm9 live result bad=0"))
        txt = "\n".join(overlay_lines(OVERLAY_B, "all")) + "\n"
        good("overlay: clean text", lambda r: (r.check("no mojibake", not RE_MOJIBAKE.search(txt)), r.check("no banned", not any(b in txt for b in BANNED))))
        for i, (bt, bd) in enumerate(((" ⌘2 Frame interpolation: on", "a dropped feature in the overlay"), ("Texture filtering: 16× (next launch)", "'(next launch)' in the overlay"),
                                      ("Texture filtering: 16Ã—", "'16×' mis-encoded in the overlay"), ("HDR: on", "HDR in the overlay"))):
            st = synth_overlay()
            st["all"] = (OVERLAY_A, st["all"][1] + bt + "\n", st["all"][2])
            mutation(105 + i, bd, check_overlay, st, (320, 256))
        good("overlay: no Developer info", check_overlay_player, txt)
        mutation(109, "Developer info in the overlay", check_overlay_player, txt + "⌘4 Developer info: on\n")
        mutation(117, "a Controller type row on the old cmd-6 in the overlay", check_overlay_player, txt + "⌘6 Controller type: Xbox\n")
        for i, (bt, bd) in enumerate((("⌘4 Aspect: 16:9", "an Aspect row"), ("⌘3 Picture mode: off", "Picture mode on its old cmd-3"), ("⌘3 Mouse capture: on", "Mouse capture"))):
            st = synth_overlay()
            st["all"] = (OVERLAY_A, st["all"][1].replace("⌘1 Overlays: all", bt), st["all"][2])
            mutation(118 + i, bd + " in the overlay", check_overlay, st, (320, 256))
        good("G3-1 capture", check_capture, rows)
        mutation(74, "a capture that stays on after the window loses focus", check_capture, synth_input(focus="stuck")[0])
        mutation(75, "a hidden cursor over an unfocused window", check_capture, synth_input(focus="hidden")[0])
        good("W window", check_window, synth_window())
        for num, flaw, desc in ((59, "stretch", "the picture stretched to the window"), (60, "off-centre", "a picture pinned to the corner"),
                               (61, "both-bars", "bars on both axes"), (62, "fixed", "a window that cannot be resized"),
                               (63, "no-min", "no minimum window size"), (64, "no-fs", "a window without the full-screen behavior"),
                               (65, "collection", "the full-screen primary bit left off"), (66, "menu", "no Enter Full Screen menu item"),
                               (67, "first-launch", "a first launch that writes a window line"), (68, "lost", "a saved window lost on the next load"),
                               (69, "accepted", "a bad window line accepted"), (70, "junk-rect", "the rectangle of the full-screen animation saved"),
                               (71, "fs-stuck", "full screen still on after leaving it"), (72, "no-clamp", "a saved window bigger than its display"),
                               (73, "offscreen", "a saved window on a display that is gone"),
                               (83, "wrap", "a settle check that breaks across the uint32 millisecond wrap")):
            mutation(num, desc, check_window, synth_window(flaw))
        mutation(2, "a guest key on a chord", check_input, *synth_input(guest_key_on_chord=3))
        expect("G1-2 a missing toggle line in the log fails", not result(check_input, rows, log.replace("rt: toggle picture off -> on (chord)\n", "")).ok)
        expect("G3-1 a default capture that is not relative fails", not result(check_capture, parse_m9(m9_line("input", "capture-default", relative=0, cursor=1))).ok)

        good("G1-3 fps", check_fps, synth_fps())
        mutation(87, "player mode with no frame-counter start", check_fps, synth_fps(player_start=0))
        expect("G1-3 player mode reading 0 FPS fails", not result(check_fps, synth_fps(player_fps=0)).ok)
        mutation(3, "p99 from a window one frame short", check_fps, synth_fps(short="spike"))
        expect("G1-3 the spike series is sensitive to a one-frame window (p99 differs)", fps_model(fps_series("spike"))[2] != fps_model(fps_series("spike"), True)[2])
        expect("G1-3 steady reads 200 FPS and interp 100+100 FPS", fps_model(fps_series("steady"))[0] == "200 FPS" and fps_model(fps_series("interp"))[0] == "100+100 FPS")
        expect("G1-3 an interpolated run reading 200 FPS fails", not result(check_fps, parse_m9(m9_line("fps", "interp", title="200 FPS", p50="5.00", p99="5.00"))).ok)

        good("G1-4 overlay", check_overlay, synth_overlay(), (320, 256))
        mutation(4, "a reminder that shows a stale state", check_overlay, synth_overlay(stale=True), (320, 256))
        wrong = synth_overlay()
        wrong["off"] = (OVERLAY_A, "", overlay_png(3))
        expect("G1-4 off drawing something fails", not result(check_overlay, wrong, (320, 256)).ok)
        wrong = synth_overlay()
        wrong["fps"] = (OVERLAY_A, wrong["all"][1], wrong["all"][2])
        expect("G1-4 fps showing every line fails", not result(check_overlay, wrong, (320, 256)).ok)
        wrong = synth_overlay()
        wrong["all"] = (OVERLAY_A, wrong["all"][1], write_png(320, 256, [bytes(OVERLAY_BG) * 320 for _ in range(256)]))
        expect("G1-4 a box missing from the PNG fails", not result(check_overlay, wrong, (320, 256)).ok)
        a = {"x/a.png": b"1", "x/b.png": b"2"}
        good("G1-5 identical sets", check_identical, "t", a, dict(a))
        expect("G1-5 a differing PNG fails", not result(check_identical, "t", a, {**a, "x/b.png": b"3"}).ok)
        expect("G1-5 a missing file fails", not result(check_identical, "t", a, {"x/a.png": b"1"}).ok)

        rows, log = synth_settings()
        good("G1-6 settings", check_settings, rows, log)
        good("G5-1 dxvk config", check_dxvk, rows, log)
        expect("G1-6 a value lost across the restart fails", not result(check_settings, synth_settings("read")[0]).ok)
        expect("G6 a saved native step kept while unavailable fails", not result(check_settings, synth_settings("held")[0]).ok)
        expect("G6 a saved native step overwritten by the fallback fails", not result(check_settings, synth_settings("overwritten")[0]).ok)
        expect("E a Video write that is not stored after stage 6 fails", not result(check_settings, synth_settings("opt")[0]).ok)
        expect("E a player build that shows picture native fails", not result(check_settings, synth_settings("pn")[0]).ok)
        mutation(13, "a bad settings value that is fatal", check_settings, *synth_settings("fatal"))
        mutation(14, "a samplerAnisotropy entry added to DXVK_CONFIG", check_dxvk, *synth_settings("eight"))
        mutation(15, "samplerAnisotropy = 16 added from an old aniso=16 line", check_dxvk, *synth_settings("always"))
        mutation(16, "an already set DXVK_CONFIG overwritten", check_dxvk, *synth_settings("overwrite"))

        good("G2-1 aspect", check_aspect, synth_aspect())
        good("G2-4 projection", check_proj, synth_aspect())
        good("G2-5 rects", check_rects, synth_aspect())
        mutation(5, "an aspect size off by one", check_aspect, synth_aspect("off-by-one"))
        mutation(17, "fit not skipped within 1 percent", check_aspect, synth_aspect("no-skip"))
        mutation(18, "Hor+ with width and height swapped", check_aspect, synth_aspect("swapped"))
        mutation(19, "a Vert- engine projection", check_proj, synth_aspect("vert-"))
        mutation(20, "the destination filled, not letterboxed", check_rects, synth_aspect("stretch"))
        mutation(203, "a custom size that is clamped instead of kept", check_aspect, synth_aspect("keep"))
        mutation(204, "a custom size window that stretches or overflows", check_aspect, synth_aspect("win"))
        mutation(36, "the fresh aspect default back to fit", check_aspect, synth_aspect("default-fit"))
        expect("G2-1 fresh default windows: 1594x1196, 1844x1383 and 1188x891", [aspect_fit(4, 3, cw, ch) for _, _, cw, ch in DEFAULT43_DESKTOPS] == [(1594, 1196), (1844, 1383), (1188, 891)])
        expect("G2-1 sizes: 16:9 in 1280x1024 is 1280x720, 21:9 in 3440x1440 is 3440x1440 and fit in 2056x1329 is 2056x1329", aspect_size("16:9", 1280, 1024) == (1280, 720) and aspect_size("21:9", 3440, 1440) == (3440, 1440) and aspect_size("fit", 2056, 1329) == (2056, 1329))
        expect("G2-1 engine stays 1280x1024 in any window (V3); a tiny window or exactly 640x480 becomes 800x600, the engine drops 640x480 (engine+0xfc8f8)",
               aspect_size("engine", 3440, 1440) == ENGINE_SIZE and aspect_size("16:9", 400, 300) == FLOOR_SIZE and aspect_size("4:3", 640, 480) == FLOOR_SIZE
               and aspect_size("4:3", 720, 540) == (720, 540))
        expect("G2-1 the cycle goes 16:9, fit, 21:9 and back, engine is outside it, and fit is skipped near 16:9 or 21:9", [aspect_next(s, 1.25) for s in ASPECT_STEPS] == ["fit", "21:9", "16:9", "16:9"] and aspect_next("16:9", 16 / 9) == "21:9")

        clines = [m9_line("controller", "type", name=nm, type=t) for nm, t in CONTROLLER_CASES]
        clines += [m9_line("controller", "controls", south="e", north="f", back="c", bad=3, look_speed=600, px=60),
                   m9_line("controller", "defaults", south="space", bad=0, look_speed=1200, px=120),
                   m9_line("controller", "mappings", n=1), m9_line("controller", "result", bad=0)]
        crows = parse_m9("\n".join(clines))
        good("G4-2 controller", check_controller, crows)
        expect("G4-2 a wrong type fails", not result(check_controller, parse_m9("\n".join(clines).replace("type=playstation", "type=xbox", 1))).ok)
        expect("G4-5 a controls.txt that ignores the bad lines fails", not result(check_controller, parse_m9("\n".join(clines).replace("bad=3", "bad=0"))).ok)

        patch, rgb, lines = (100, 70, 24, 16), (200, 40, 90), (8, 8, 48, 40)
        card = make_card()
        tg = ((320, 256), (225, 180))
        outs = [upscale(card, *t) for t in tg]
        good("G6-2 spatial", check_spatial, card, outs, patch, rgb, lines, tg)
        mutation(6, "a card without the random patch", check_spatial, make_card(with_patch=False), outs, patch, rgb, lines, tg)
        mutation(7, "a bilinear PNG (the gradient check)", check_spatial, card, [upscale(card, *t, sharp=0) for t in tg], patch, rgb, lines, tg)
        mutation(21, "an output without the patch", check_spatial, card, [upscale(make_card(with_patch=False), *t) for t in tg], patch, rgb, lines, tg)
        mutation(22, "an output of the wrong size", check_spatial, card, [upscale(card, 300, 240), outs[1]], patch, rgb, lines, tg)
        mutation(23, "a black output", check_spatial, card, [(w, h, [bytes(3 * w)] * h) for w, h, _ in outs], patch, rgb, lines, tg)

        cur, prev = matrices()
        w8, h8 = 24, 16
        depth = depth_scene(w8, h8)
        good("G7-1 motion", check_motion, depth, w8, h8, cur, prev, analytic_motion(depth, w8, h8, cur, prev))
        mutation(8, "swapped current and previous matrices", check_motion, depth, w8, h8, cur, prev, analytic_motion(depth, w8, h8, prev, cur))
        expect("G7-1 the swap moves most pixels by more than the tolerance", sum(1 for a, b in zip(analytic_motion(depth, w8, h8, cur, prev), analytic_motion(depth, w8, h8, prev, cur)) if abs(a[0] - b[0]) > MOTION_TOL) > 0.9 * w8 * h8)
        expect("G7-1 zero motion fails", not result(check_motion, depth, w8, h8, cur, prev, [(0.0, 0.0)] * (w8 * h8)).ok)
        expect("G7-1 a short motion texture fails", not result(check_motion, depth, w8, h8, cur, prev, [(0.0, 0.0)] * 5).ok)

        cam = parse_m9(m9_line("gfx", "camera", frames=600, found=598, passthrough=2))
        good("G7-2 camera", check_camera, cam)
        expect("G7-2 below 99 percent fails", not result(check_camera, parse_m9(m9_line("gfx", "camera", frames=600, found=590, passthrough=10))).ok)
        expect("G7-2 uncounted frames fail", not result(check_camera, parse_m9(m9_line("gfx", "camera", frames=600, found=598, passthrough=0))).ok)

        nf = 12
        ref = [taa_cov(k) for k in range(nf)]
        off = [taa_aliased(k) for k in range(nf)]
        good("G7-3 taa", check_taa, off, ref, ref)
        mutation(24, "smoothing that does nothing (edge error)", check_taa, off, off, ref)
        ghost = [ref[0]]
        for k in range(1, nf):
            ghost.append([[0.5 * a + 0.5 * b for a, b in zip(ra, rb)] for ra, rb in zip(ref[k], ghost[-1])])
        mutation(25, "zero motion accumulation (ghost trail)", check_taa, off, ghost, ref)
        expect("G7-3 the aliased frames really have edge error", sum(abs(a - b) for ra, rb in zip(off[-1], ref[-1]) for a, b in zip(ra, rb)) > 100)

        pv, nx, mid, hud, box = synth_interp()
        good("G8-1 interp", check_interp, pv, nx, mid, hud, box)
        mutation(9, "the HUD inside the colour input", check_interp, pv, nx, synth_interp(True)[2], hud, box)
        mid_off = (mid[0], mid[1], [bytes(r) for r in paint([bytearray(r) for r in mid[2]], 16, 24, 8, 8, INTERP_BG)])
        mid_off = (mid[0], mid[1], [bytes(r) for r in paint([bytearray(r) for r in mid_off[2]], 20, 24, 8, 8, 255)])
        mutation(26, "the made square away from the midpoint", check_interp, pv, nx, mid_off, hud, box)

        good("G8-2 cut", check_cut, parse_m9(m9_line("gfx", "cut", first2d=7, cut=7) + "\n" + m9_line("gfx", "cut-none", cut=-1, passthrough=1)))
        expect("G8-2 a cut at the first draw fails", not result(check_cut, parse_m9(m9_line("gfx", "cut", first2d=7, cut=0) + "\n" + m9_line("gfx", "cut-none", cut=-1, passthrough=1))).ok)
        expect("G8-2 an uncounted cutless frame fails", not result(check_cut, parse_m9(m9_line("gfx", "cut", first2d=7, cut=7) + "\n" + m9_line("gfx", "cut-none", cut=-1, passthrough=0))).ok)

        good("G8-3 queue", check_queue, synth_queue(), "82+82 FPS")
        mutation(10, "a repeated frame", check_queue, synth_queue("repeat"), "82+82 FPS")
        mutation(27, "the oldest frame wins", check_queue, synth_queue("oldest"), "82+82 FPS")
        mutation(28, "the FPS menu without N+N", check_queue, synth_queue(), "164 FPS")

        hr = parse_m9(m9_line("hdr", "enable", headroom="1.0", enabled=0) + "\n" + m9_line("hdr", "enable", headroom="16.0", enabled=1))
        ramps = {4.0: synth_ramp(4.0), 16.0: synth_ramp(16.0)}
        good("G9-1 and G9-2 hdr", check_hdr, hr, ramps)
        mutation(11, "a non-monotonic curve", check_hdr, hr, {4.0: synth_ramp(4.0, dip=True), 16.0: ramps[16.0]})
        mutation(29, "values below 0.8 changed", check_hdr, hr, {4.0: [y * 1.1 for y in ramps[4.0]], 16.0: ramps[16.0]})
        mutation(30, "enabled at headroom 1.0", check_hdr, parse_m9(m9_line("hdr", "enable", headroom="1.0", enabled=1) + "\n" + m9_line("hdr", "enable", headroom="16.0", enabled=1)), ramps)
        mutation(31, "the top short of the headroom", check_hdr, hr, {4.0: [min(y, 3.0) for y in ramps[4.0]], 16.0: ramps[16.0]})
        good("G9-3 gfx hdr", check_gfx_hdr, parse_m9(m9_line("gfx", "hdr", max="4.0", spaces=3)))
        mutation(32, "an 8-bit target (nothing above 1.0)", check_gfx_hdr, parse_m9(m9_line("gfx", "hdr", max="1.0", spaces=3)))

        vok = parse_m9(m9_line("vfs", "loose-over-vpk", source="loose", sha1=hashlib.sha1(LOOSE_BYTES).hexdigest()) + "\n" + m9_line("vfs", "vpk-only", source="vpk", sha1=hashlib.sha1(VPK_BYTES).hexdigest()))
        good("M1 vfs", check_vfs, vok)
        mutation(33, "a case-sensitive lookup (the VPK wins)", check_vfs, parse_m9(m9_line("vfs", "loose-over-vpk", source="vpk", sha1=hashlib.sha1(VPK_BYTES).hexdigest()) + "\n" + m9_line("vfs", "vpk-only", source="vpk", sha1=hashlib.sha1(VPK_BYTES).hexdigest())))

        sup = parse_m9(m9_line("fx", "support", spatial=1, temporal=1, interpolator=0, metal4fx=1))
        slog = "rt: fx support spatial 1 temporal 1 interpolator 0 metal4fx 1\n"
        support = {}
        good("G6-1 fx support", lambda r: support.update(check_fx_support(r, sup, slog)))
        expect("G6-1 a log line that disagrees with the row fails", not result(lambda r: check_fx_support(r, sup, slog.replace("interpolator 0", "interpolator 1"))).ok)
        menu_fx = synth_menu(("G1", "G6"))
        good("G6-1 menu matches support", check_menu_fx, menu_fx, support)
        expect("G6-1 an enabled item for an unsupported effect fails", not result(check_menu_fx, menu_fx, {**support, "spatial": False}).ok)
        good("G6-1 a supported effect of an unmerged piece stays Not built yet", check_menu_fx, synth_menu(("G1", "G6")), support, ("G1", "G6"))
        expect("G6-1 Picture mode disabled while spatial is supported fails", not result(check_menu_fx, synth_menu(("G1",)), support, ("G1", "G6")).ok)
        cyc = lambda **kv: parse_m9(m9_line("fx", "cycle", **kv))
        good("G6 cmd-3 cycle returns to its start", check_fx_cycle, cyc(mask=5, start="off", seq="metalfx off", back=1, native=0, reason=""), {"spatial": True})
        good("G6 cmd-3 cycle with the engine override has the native step", check_fx_cycle,
             cyc(mask=7, start="off", seq="metalfx off", back=1, native=1, reason=""), {"spatial": True})
        expect("G6 a native step without the engine override fails",
               not result(check_fx_cycle, cyc(mask=7, start="off", seq="metalfx off", back=1, native=0, reason=""), {"spatial": True}).ok)
        expect("G6 a cycle stuck on metalfx fails", not result(check_fx_cycle, cyc(mask=5, start="off", seq="metalfx metalfx", back=0, native=0, reason=""), {"spatial": True}).ok)
        good("G6 unsupported cmd-3 stays put with a reason", check_fx_cycle, cyc(mask=0, start="off", seq="off off off", back=1, reason="No Metal device"), {"spatial": False})
        expect("G6 unsupported cmd-3 without a reason fails", not result(check_fx_cycle, cyc(mask=0, start="off", seq="off off off", back=1, reason=""), {"spatial": False}).ok)
        expect("G6 scaler build lines are read", fx_pipelines("rt: fx pipelines spatial 1280x1024 -> 3440x1440 built in 12.50 ms (1 this run, 12.50 ms in all)\n") == ["12.50"])
        forced = synth_menu(("G1",))
        good("G6-5 forced unsupported menu", check_menu_forced, forced)
        expect("G6-5 an enabled item fails", not result(check_menu_forced, synth_menu(("G1", "G6", "G7", "G8"))).ok)
        good("G6-5 forced unsupported log", check_fx_forced, "rt: fx unsupported spatial (MetalFX unavailable)\n", 0)
        expect("G6-5 a nonzero exit fails", not result(check_fx_forced, "rt: fx unsupported spatial (x)\n", 1).ok)
        expect("G6-5 two lines fail", not result(check_fx_forced, "rt: fx unsupported spatial (x)\nrt: fx unsupported spatial (x)\n", 0).ok)

        good("G6-3 added cost", check_perf_numbers, "t", "spatial", synth_fx_log())
        good("G7-4 added cost", check_perf_numbers, "t", "taa", synth_fx_log(p99=2.9, scaled=0, smoothed=3000))
        expect("G6-3 p99 above 2.0 ms fails", not result(check_perf_numbers, "t", "spatial", synth_fx_log(p99=2.5)).ok)
        info = io.StringIO()
        ri = Rep(quiet=True)
        with contextlib.redirect_stdout(info):
            check_perf_numbers(ri, "t", "spatial", synth_fx_log(p99=5.5), 3000, False)
        expect("G6-3 p99 above 2.0 ms is INFO without G6b, numbers kept", ri.ok and "INFO" in info.getvalue() and "5.5" in info.getvalue())
        expect("G6 a cycle starting on metalfx fails", not result(check_fx_cycle, cyc(mask=5, start="metalfx", seq="off metalfx", back=1, native=0, reason=""), {"spatial": True}).ok)
        expect("G7-4 p99 above 3.0 ms fails", not result(check_perf_numbers, "t", "taa", synth_fx_log(p99=3.5, scaled=0, smoothed=3000)).ok)
        expect("G6-3 a short run fails", not result(check_perf_numbers, "t", "spatial", synth_fx_log(n=2000)).ok)
        expect("G6-3 no stats line fails", not result(check_perf_numbers, "t", "spatial", "nothing\n").ok)
        expect("G6-3 frames that were not scaled fail", not result(check_perf_numbers, "t", "spatial", synth_fx_log(scaled=10)).ok)

        steps = parse_m9("\n".join(m9_line("fx-route", "step", name=s, ok=1) for s in ("interop", "proc", "export", "alloc", "bind", "bytes")))
        good("G6b route probe passes", check_route, steps, "", 0)
        good("G6b a failed step logs why and keeps R3", check_route, parse_m9(m9_line("fx-route", "step", name="proc", ok=0)), "rt: fx route r1 unavailable (dxvk without VK_EXT_external_memory_metal)\n", 0)
        expect("G6b a failed step with no log line fails", not result(check_route, parse_m9(m9_line("fx-route", "step", name="proc", ok=0)), "", 0).ok)

        pol = lambda args: SimpleNamespace(**{"kind": "spatial", "frames": 3000, "note": "", "merged": "G6,G7", "exe": "x", "timeout": 5, **args})
        calls = []

        def fake_run(exe, case, extra=(), timeout=0):
            calls.append(case)
            return SimpleNamespace(rc=0, log=synth_fx_log(), rows=[], argv=[exe, case])

        def perf_refused(gate):
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                rc = perf_run(pol({}), fake_run, gate, lambda: "up 1 day", lambda: None, lambda x: "readings")
            return rc == 1 and not calls

        expect("perf refuses to run under load and never starts T", perf_refused(lambda perf=False: ["1-minute load average 9.10 is 8 or more"]))
        expect("perf passes the quiet gate with perf=True (the strict thresholds)", (lambda seen: (perf_refused(lambda perf=False: seen.append(perf) or ["x"]), seen == [True])[1])([]))
        with contextlib.redirect_stdout(io.StringIO()):
            calls.clear()
            rc = perf_run(pol({}), fake_run, lambda perf=False: [], lambda: "up 1 day", lambda: None, lambda x: "readings")
        expect("perf runs T on a quiet machine, with --frames and exit 0", rc == 0 and calls == ["fx-spatial"])
        mutation(12, "a perf run without the quiet gate", lambda r: r.check("a loaded machine refuses the perf run", perf_refused(lambda perf=False: [])))
        calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = perf_run(pol({"merged": "G1"}), fake_run, lambda perf=False: [], lambda: "up", lambda: None, lambda x: "r")
        expect("perf spatial refuses while G6 is not merged and never starts T", rc == 1 and not calls)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = perf_run(pol({"kind": "taa", "merged": "G6"}), fake_run, lambda perf=False: [], lambda: "up", lambda: None, lambda x: "r")
        expect("perf taa refuses while G7 is not merged", rc == 1 and not calls)

        main_ok = "int main(void) {\n  rt_devmenu_init();\n  rt_hud_init();\n  if (player) { x(); }\n}\n"
        good("G1-7 main.c", check_static_main, main_ok)
        mutation(34, "--player skipping rt_hud_init", check_static_main, "int main(void) {\n  rt_devmenu_init();\n  if (!player)\n    rt_hud_init();\n}\n")
        expect("G1-7 rt_hud_init before rt_devmenu_init fails", not result(check_static_main, "rt_hud_init();\nrt_devmenu_init();\n").ok)
        expect("G1-7 no call at all fails", not result(check_static_main, "rt_devmenu_init();\n").ok)
        bridge_ok = ('#define DXVK_CONFIG_VALUE "d3d9.a = 1; d3d9.maxFrameLatency = 1"\nstatic int v_present(void *d) {\n  rt_aspect_letterbox(1,2,3,4,r);\n}\n'
                     "int rt_gfx_movie_blit(const void *xrgb, uint32_t w)\n{\n    rt_aspect_letterbox((int)bd.Width, (int)bd.Height, pw, ph, r);\n"
                     "    HR ph = VT(dev, V_PRESENT, HR (*)(void *, const void *, const void *, void *, const void *))(dev, NULL, NULL, NULL, NULL);\n}\n")
        good("G2-5 bridge", check_static_bridge, bridge_ok)
        with open(os.path.join(ROOT, "shims", "d3d9_bridge.c"), encoding="utf-8") as f:
            good("G2-5 the real d3d9_bridge.c", check_static_bridge, f.read())
        mutation(86, "a movie blit that presents with a letterbox destination rectangle", check_static_bridge, bridge_ok.replace("(dev, NULL, NULL, NULL, NULL)", "(dev, NULL, pd, NULL, NULL)"))
        window_ok = ("int rt_window_init(void)\n{\n    SDL_SetHint(SDL_HINT_VIDEO_MAC_FULLSCREEN_SPACES, \"1\");\n    if (!SDL_Init(SDL_INIT_VIDEO)) {\n    }\n}\n"
                     "static void set_minimum(void *sw)\n{\n    SDL_SetWindowMinimumSize(sw, 1, 1);\n}\n"
                     "void rt_window_configure(void *sw)\n{\n    SDL_SetWindowResizable(sw, true);\n    sel(\"setCollectionBehavior:\");\n}\n"
                     "void rt_win_sdl(rt_win *w)\n{\n    rt_window_configure(sw);\n}\n"
                     "void rt_win_resize(rt_win *w)\n{\n    int full = w->sdl && (SDL_GetWindowFlags(w->sdl) & SDL_WINDOW_FULLSCREEN);\n"
                     "    if (w->sdl && !full) SDL_SetWindowSize(w->sdl, cw, ch);\n    if (w->sdl && !full && w->hwnd == s_main_hwnd) rt_window_place(w->sdl, cw, ch);\n}\n"
                     "static void window_tick(void)\n{\n    rt_gfx_present_rects(&pr);\n}\n"
                     "void rt_pump(int force)\n{\n    case SDL_EVENT_WINDOW_CLOSE_REQUESTED:\n        window_flush();\n        rt_input_post(w->hwnd, 0x0010, 0, 0);\n"
                     "        break;\n    case SDL_EVENT_QUIT:\n        window_flush();\n        rt_input_post(s_main_hwnd, 0x0010, 0, 0);\n        break;\n"
                     "    SDL_EVENT_WINDOW_MOVED SDL_EVENT_WINDOW_RESIZED SDL_EVENT_WINDOW_ENTER_FULLSCREEN SDL_EVENT_WINDOW_LEAVE_FULLSCREEN "
                     "SDL_EVENT_WINDOW_DISPLAY_CHANGED\n}\n")
        good("W window.c", check_static_window, window_ok)
        with open(os.path.join(ROOT, "runtime", "window.c"), encoding="utf-8") as f:
            good("W the real window.c", check_static_window, f.read())
        for num, desc, old, new in ((76, "a window.c that never makes the window resizable", "SDL_SetWindowResizable(sw, true)", "x"),
                                    (77, "a window.c without the full-screen collection behavior", "setCollectionBehavior:", "x"),
                                    (78, "a window.c without a minimum size", "SDL_SetWindowMinimumSize", "x"),
                                    (79, "a quit path that does not save the window", "window_flush();\n        rt_input_post(s_main_hwnd", "rt_input_post(s_main_hwnd"),
                                    (80, "a window.c that never configures the main window", "rt_window_configure(sw);", "x"),
                                    (81, "a window.c that ignores the leave-full-screen event", "SDL_EVENT_WINDOW_LEAVE_FULLSCREEN", "x"),
                                    (82, "a window.c that starts the state before the first present", "rt_gfx_present_rects", "x"),
                                    (84, "an engine resize that skips the full-screen guard", "if (w->sdl && !full) SDL_SetWindowSize", "if (w->sdl) SDL_SetWindowSize"),
                                    (85, "a full-screen guard read after the resize", "    int full = w->sdl && (SDL_GetWindowFlags(w->sdl) & SDL_WINDOW_FULLSCREEN);\n    if (w->sdl && !full) SDL_SetWindowSize(w->sdl, cw, ch);\n",
                                     "    if (w->sdl && !full) SDL_SetWindowSize(w->sdl, cw, ch);\n    int full = w->sdl && (SDL_GetWindowFlags(w->sdl) & SDL_WINDOW_FULLSCREEN);\n")):
            mutation(num, desc, check_static_window, window_ok.replace(old, new))
        with open(os.path.join(ROOT, "shims", "d3d9_bridge.c"), encoding="utf-8") as f:
            real_bridge = f.read()
        good("W the real bridge layer fit", check_static_resize, real_bridge)
        mutation(200, "a v_present that skips the layer fit", check_static_resize, real_bridge.replace("if (fx_win) rt_gfx_layer_fit(fx_win);", "", 1))
        mutation(202, "a Reset that leaves a 0x0 back buffer", check_static_resize, real_bridge.replace("if (dw || fw) rt_gfx_pp_size(", "if (dw || fw) x_pp_size(", 1))
        mutation(201, "a Reset that leaves the engine aspect and FOV stale", check_static_resize,
                 real_bridge.replace("rt_aspect_engine_set((int)pp.BackBufferWidth, (int)pp.BackBufferHeight, 1);", ""))
        expect("G2-5 a bridge that never letterboxes fails", not result(check_static_bridge, bridge_ok.replace("rt_aspect_letterbox", "x")).ok)
        spans = [("g2b_view_fov", 0x10692069, 0x10692072, (0x10692069,)),
                 ("g2b_aspect", 0x2007A424, 0x2007A452, tuple(0x2007A424 + s for s in reach_starts("ff158008400089442410eb22909090")))]
        good("G2b-5 labels outside every site or on the new code", check_lifted_sites, spans, [0x10692069, 0x10692072, 0x10691F10, 0x2007A42A, 0x2007A42E])
        expect("G2b-5 a loc_ label inside a patched site fails", not result(check_lifted_sites, spans, [0x1069206C]).ok)
        expect("G2b-5 a loc_ label on a nop after the new jmp fails", not result(check_lifted_sites, spans, [0x2007A431]).ok)
        expect("G2b-5 no labels at all fails", not result(check_lifted_sites, spans, []).ok)
        other = "void sub_10500000(void)\n{\n    fp_top() = RECOMP_FP_PC(fp_top() * MEMF(0x1072B97C)); /* fmul dword ptr [0x1072b97c] */\n}\n"
        ramp = other.replace("sub_10500000", "sub_10609950")
        census = "MEMD(0x10727EC8)\n" * HUD_READS + other * 2 + ramp
        good("G2b-4 HUD census", check_hud_census, census, 0x10727EC8, 0x1072B97C, 0x10609950)
        stock = "MEMD(0x10727EC8)\n" * (HUD_READS - 1) + other * 2 + ramp.replace("MEMF(0x1072B97C)); /* fmul dword ptr [0x1072b97c]", "MEMD(0x10727EC8)); /* fmul qword ptr [0x10727ec8]")
        expect("G2b-4 the gamma ramp left on the HUD double fails though stock reads the float twice elsewhere",
               not result(check_hud_census, stock, 0x10727EC8, 0x1072B97C, 0x10609950).ok)
        expect("G2b-4 a missing gamma ramp function fails", not result(check_hud_census, census.replace("sub_10609950", "sub_10609951"), 0x10727EC8, 0x1072B97C, 0x10609950).ok)
        eng = SimpleNamespace(rows=parse_m9(m9_line("engine-fov", "size=1024x768", ok=1) + "\n" + m9_line("engine-fov", "result", bad=0, built=1)),
                              log="rt: aspect engine 1024x768 r 1.3333 fov hor+ hud uniform\n")
        good("G2b-3 engine case rows", check_engine, "engine-fov", eng)
        expect("G2b-3 a failed engine row fails", not result(check_engine, "engine-fov", SimpleNamespace(rows=parse_m9(m9_line("engine-fov", "x", ok=0) + "\n" + m9_line("engine-fov", "result", bad=1, built=1)), log=eng.log)).ok)
        expect("G2b-5 a plain build (built=0) fails the engine case", not result(check_engine, "engine-fov", SimpleNamespace(rows=parse_m9(m9_line("engine-fov", "x", ok=1) + "\n" + m9_line("engine-fov", "result", bad=0, built=0)), log=eng.log)).ok)
        expect("G5 a bridge DXVK_CONFIG without maxFrameLatency = 1 fails", not result(check_static_bridge, bridge_ok.replace("; d3d9.maxFrameLatency = 1", "")).ok)
        docs = "# M7\n## M9: Graphics\nkey map ⌘1 to ⌘7, AC-G6-3 p99 2.0 ms, licence rows\n## Mods\nloose files win over the VPK; DLL mods go through M8; ReShade hooks do not work\n"
        good("AC-M2 and AC-D1 docs", check_static_docs, docs)
        expect("AC-M2 a missing Mods section fails", not result(check_static_docs, docs.replace("## Mods", "## Other")).ok)
        expect("AC-M2 a missing rule fails", not result(check_static_docs, docs.replace("ReShade", "x")).ok)
        expect("AC-D1 a missing M9 section fails", not result(check_static_docs, docs.replace("M9: Graphics", "Graphics")).ok)
        good("AC-D1 git ls-files", check_tracked, ["a.py", "docs/x.png"])
        expect("AC-D1 a tracked PNG fails", not result(check_tracked, ["a.py", "out/frame.png"]).ok)

        live = toggles_text("sleep 20")
        expect("live: the toggles script stays at the main menu", refuse_past_menu(live) == [])
        expect("live: the intro wait variant stays at the main menu", refuse_past_menu(toggles_text("wait bink 4 200\nsleep 8")) == [])
        for bad_line in ("click newgame", "move 10 10", "key return", "wait map sp_theatre", "console \"map x\"", "chord 9", "text \"x\"", "wait", "keydown a"):
            expect(f"live: '{bad_line}' is refused", len(refuse_past_menu(live + bad_line + "\n")) == 1)
        expect("live: the script parses under the m7 parser", len(m7.parse_script(live)) >= 10)
        r = result(check_at_menu, "rt: stage 12 map sp_theatre\n")
        expect("live: a map load in the log fails", not r.ok)
        good("live: the main-menu log", check_at_menu, "rt: stage 11 x\n")
        tlog = "".join(f"rt: toggle {nm} off -> on (chord)\n" for nm in CHORD_NAMES[:2])
        good("live: chord toggle lines", check_live_toggles, tlog, ("G1",))
        expect("live: a toggle from the menu source fails", not result(check_live_toggles, tlog.replace("(chord)", "(menu)", 1), ("G1",)).ok)
        expect("live: a missing toggle fails", not result(check_live_toggles, tlog.replace("rt: toggle picture off -> on (chord)\n", ""), ("G1",)).ok)

        expect("parse_m9 reads quoted values", parse_m9("junk\nm9 fps steady title='200 FPS' p50=5.00\nm9 bad") == [("fps", "steady", {"title": "200 FPS", "p50": "5.00"})])
        expect("parse_m9 skips an unbalanced quote", parse_m9("m9 fps x title='oops") == [])
        data = overlay_png(2)
        expect("png_bbox finds the box", png_bbox(data)[1] == (210, 4, 309, 23) and png_bbox(overlay_png(0))[1] is None)
        def frame(box_at=None, noise=False):
            w, h = 200, 100
            rows = [bytes((40, 40, 40)) * w for _ in range(h)]
            if box_at:
                for y in range(5, 15):
                    rows[y] = rows[y][:3 * 120] + b"\xf0\xf0\xf0" * 60 + rows[y][3 * 180:]
            if noise:
                for y in range(80, 90):
                    rows[y] = bytes((200, 0, 0)) * w
            return write_png(w, h, rows)
        hin, hout = region_diff(frame(True), frame())
        expect("region_diff sees a box in the top-right and nothing outside", hin > 0.05 and hout == 0)
        hin, hout = region_diff(frame(), frame())
        expect("region_diff of identical frames is zero", hin == 0 and hout == 0)
        expect("region_diff sees a change outside the region", region_diff(frame(), frame(noise=True))[1] > 0.02)
        expect("check_hud_live passes a box", result(check_hud_live, frame(True), frame()).ok)
        expect("check_hud_live fails when the box is missing", not result(check_hud_live, frame(), frame()).ok)
        expect("check_hud_live fails when the rest also changes", not result(check_hud_live, frame(True, True), frame()).ok)
        expect("percentile model is nearest rank like m4", fps_model([(i * 5_000_000, 0) for i in range(601)])[1] == 5.0)
        expect("mat_inv inverts", max(abs(a - b) for a, b in zip(mat_mul(cur, mat_inv(cur)), [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])) < 1e-6)
        expect("mat_inv refuses a singular matrix", raises(ValueError, mat_inv, [0] * 16))
        expect("hfov at 16:9 for a 60 degree vertical view is 91.5 degrees", abs(hfov_deg(60, 16, 9) - 91.49) < 0.01)

        fake = os.path.join(tmp, "fakex")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho \"m9 fps steady title='200 FPS' p50=5.00 p99=5.00\"\necho junk >&2\nexit 0\n")
        os.chmod(fake, 0o755)
        res = run_t(fake, "fps", overlay=os.path.join(tmp, "o"))
        expect("run_t runs X --m9-selftest and parses its lines", res.rc == 0 and rows_of(res.rows, "fps", "steady") == [{"title": "200 FPS", "p50": "5.00", "p99": "5.00"}] and res.argv[1:3] == ["--m9-selftest", "fps"])
        expect("run_t puts --player before --overlay", run_t(fake, "menu", player=True).argv[3] == "--player")
        r = result(check_ran, "t", SimpleNamespace(rc=0, log="Traceback (most recent call last)", argv=[]))
        expect("check_ran fails on a Traceback", not r.ok)
        expect("check_ran fails on a FAIL line", not result(check_ran, "t", SimpleNamespace(rc=0, log="FAIL x\n", argv=[])).ok)
        expect("check_ran fails on a timeout", not result(check_ran, "t", SimpleNamespace(rc=None, log="", argv=[])).ok)

        with open(os.path.join(SCRIPTS, "contract.txt"), encoding="utf-8") as f:
            contract = f.read()
        used = ("menu", "input", "fps", "overlay", "settings", "live", "aspect", "controller", "hdr", "vfs", "fx", "fx-spatial", "fx-route", "reproject", "fx-taa", "fx-interp", "present-queue")
        miss = [c for c in used if not re.search(rf"^{re.escape(c)}\s", contract, re.M)]
        expect(f"contract.txt describes every T case ({miss})", not miss)
        parsed = build_parser()
        for mode in ("selftest", "menu", "input", "fps", "overlay", "settings", "live", "aspect", "controller", "hdr", "vfs", "fx", "gfx", "static", "toggles"):
            expect(f"the parser takes mode {mode}", parsed.parse_args([mode] + ([] if mode == "selftest" else ["--exe", "x"])).mode == mode)
        expect("perf needs --kind", raises(SystemExit, parsed.parse_args, ["perf"]))
        expect("perf has no --extra", raises(SystemExit, parsed.parse_args, ["perf", "--kind", "taa", "--extra", "+map"]))
        guarded = guarded_runner(lambda exe, o, argv, t: "ran")
        expect("the runner guard refuses +map and --, and lets a menu argv through",
               raises(ValueError, guarded, "x", "o", ["--close-after", "5", "--", "+map", "x"], 1) and raises(ValueError, guarded, "x", "o", ["+map", "x"], 1)
               and guarded("x", "o", ["--close-after", "5"], 1) == "ran")
        seen = []
        saved_locked = globals()["locked"]
        globals()["locked"] = lambda A, fn: seen.append(fn) or 7
        try:
            expect("toggles takes the game lock", cmd_toggles(SimpleNamespace()) == 7 and len(seen) == 1)
        finally:
            globals()["locked"] = saved_locked

        fixd = os.path.join(tmp, "fix")
        os.makedirs(fixd)
        text_of = lambda rows: "\n".join(m9_line(c, lab, **kv) for c, lab, kv in rows) + "\n"

        def fixture(case, rows=(), log="", files=None):
            with open(os.path.join(fixd, case + ".txt"), "w") as f:
                f.write(text_of(rows) + log)
            if files:
                os.makedirs(os.path.join(fixd, case + ".files"), exist_ok=True)
                for k, v in files.items():
                    with open(os.path.join(fixd, case + ".files", k), "wb" if isinstance(v, bytes) else "w") as f:
                        f.write(v)

        in_rows, in_log = synth_input()
        fixture("menu", files={"menu.tsv": synth_menu()})
        fixture("menu-player", files={"menu.tsv": synth_menu()})
        fixture("input", [x for x in in_rows if not x[1].startswith(("capture", "focus"))], in_log)
        pin_rows, pin_log = synth_input(player=True)
        fixture("input-player", [x for x in pin_rows if not x[1].startswith(("capture", "focus"))], pin_log)
        fixture("live", [(c, l, kv) for c, l, kv in synth_live()])
        fixture("live-player", [(c, l, kv) for c, l, kv in synth_live(player=True)])
        ovp = synth_overlay(player=True)
        fixture("overlay-player", files={f"overlay-{s}.{e}": (ovp[s][2] if e == "png" else ovp[s][1]) for s in ovp for e in ("png", "tsv")})
        fixture("fps", synth_fps())
        ov = synth_overlay()
        fixture("overlay", files={f"overlay-{s}.{e}": (ov[s][2] if e == "png" else ov[s][1]) for s in ov for e in ("png", "tsv")})
        st_rows, st_log = synth_settings()
        fixture("settings", [x for x in st_rows if not x[1].startswith("dxvk")], st_log)
        with open(os.path.join(fixd, "gfx.png"), "wb") as f:
            f.write(overlay_png(2))
        gx = os.path.join(tmp, "gfxfake")
        with open(gx, "w") as f:
            f.write("#!/bin/sh\nD=$(dirname \"$0\")/fix\nO=\"$3\"\nif [ \"$1\" = --gfx-selftest ]; then cp \"$D/gfx.png\" \"$O/a.png\"; exit 0; fi\n"
                    "for a; do if [ \"$p\" = --overlay ]; then O=\"$a\"; fi; p=\"$a\"; done\nmkdir -p \"$O/m9\"\n"
                    "S=$2\nif [ \"$3\" = --player ] && [ -d \"$D/$2-player.files\" ]; then S=$2-player; fi\n"
                    "cp \"$D/$S.files\"/* \"$O/m9/\" 2>/dev/null\nT=$2\nif [ \"$3\" = --player ] && [ -f \"$D/$2-player.txt\" ]; then T=$2-player; fi\ncat \"$D/$T.txt\" 2>/dev/null\nexit 0\n")
        os.chmod(gx, 0o755)
        g1 = SimpleNamespace(exe=gx, timeout=30, merged="G1", baseline=None, canvas=(320, 256))

        def drive_mode(fn, A):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = fn(A)
            return rc, buf.getvalue()

        for nm, fn in (("menu", cmd_menu), ("input", cmd_input), ("fps", cmd_fps), ("overlay", cmd_overlay), ("settings", cmd_settings), ("live", cmd_live)):
            rc, out = drive_mode(fn, g1)
            fails = [l for l in out.splitlines() if l.startswith("FAIL")]
            expect(f"G1 only merged: mode {nm} passes its gate ({fails[:2]})", rc == 0 and not fails)
        rc, out = drive_mode(cmd_input, SimpleNamespace(**{**vars(g1), "merged": "G1,G3"}))
        expect("G3 merged: mode input fails without the capture rows", rc == 1)
        rc, out = drive_mode(cmd_settings, SimpleNamespace(**{**vars(g1), "merged": "G1,G5"}))
        expect("G5 merged: mode settings fails without the dxvk rows", rc == 1)
        for nm, fn in (("aspect", cmd_aspect), ("window", cmd_window), ("controller", cmd_controller), ("hdr", cmd_hdr), ("vfs", cmd_vfs)):
            rc, out = drive_mode(fn, g1)
            expect(f"G1 only merged: mode {nm} runs nothing and says SKIP", rc == 0 and "SKIP" in out)
        rc, out = drive_mode(lambda A: finish_mode(lambda r: fx_body(A, r), True), g1)
        expect("G1 only merged: mode fx skips G6, G6b, G7 and G8", "SKIP MetalFX" in out and "SKIP frame interpolation" in out and "SKIP R1" in out and "SKIP edge" in out and "FAIL" not in out)
        rc, out = drive_mode(lambda A: finish_mode(lambda r: gfx_body(A, r)), g1)
        expect(f"G1 only merged: mode gfx passes AC-G1-5 and skips the rest ({out[-300:]})", rc == 0 and "PASS AC-G1-5" in out and out.count("SKIP") == 5)
        rc, out = drive_mode(lambda A: finish_mode(lambda r: gfx_body(A, r)), SimpleNamespace(**{**vars(g1), "merged": "G1,G6"}))
        expect("G6 merged without --baseline fails AC-G6-4 with rc 1", rc == 1 and "FAIL AC-G6-4 needs --baseline" in out and "SKIP AC-G6-4" not in out)
        base_dir = os.path.join(tmp, "base")
        os.makedirs(base_dir)
        with open(os.path.join(base_dir, "a.png"), "wb") as f:
            f.write(overlay_png(2))
        rc, out = drive_mode(lambda A: finish_mode(lambda r: gfx_body(A, r)), SimpleNamespace(**{**vars(g1), "merged": "G1,G6", "baseline": base_dir}))
        expect("G6 merged with a matching --baseline passes AC-G6-4", rc == 0 and "PASS AC-G6-4" in out)
        with open(os.path.join(base_dir, "a.png"), "wb") as f:
            f.write(overlay_png(3))
        rc, out = drive_mode(lambda A: finish_mode(lambda r: gfx_body(A, r)), SimpleNamespace(**{**vars(g1), "merged": "G1,G6", "baseline": base_dir}))
        expect("G6 merged with a differing --baseline fails AC-G6-4", rc == 1)
        for ok_text, want in (("G1", ("G1",)), ("G1,G2", ("G1", "G2")), ("G1, G6b ,D", ("G1", "G6b", "D")), (None, ("G1",))):
            expect(f"merged_of({ok_text!r}) is {want}", merged_of(SimpleNamespace(merged=ok_text)) == want)
        for bad_text in ("g2", "G1,,G2", "G10", "G1;G2", "G2 G3", "", "G1,x", "G6B"):
            if bad_text == "":
                expect("merged_of('') falls back to G1", merged_of(SimpleNamespace(merged="")) == ("G1",))
            else:
                expect(f"merged_of({bad_text!r}) is refused", raises(MergedError, merged_of, SimpleNamespace(merged=bad_text)))
        lock_path = os.path.join(tmp, "lk", ".game.lock")
        LA = SimpleNamespace(exe=gx, lock=lock_path, lock_wait=1.0, mode="fx")
        ran = []
        with m7.patched(m7, gate7=lambda perf=False: ["CPU idle 3.0 % is below 35 %"]), contextlib.redirect_stdout(io.StringIO()) as so:
            rc = locked(LA, lambda: ran.append(1) or 0)
        expect("locked: a refusing gate gives REFUSED, rc 1, and fn never runs", rc == 1 and not ran and "REFUSED: CPU idle" in so.getvalue() and not os.path.exists(lock_path))
        held = []
        with m7.patched(m7, gate7=lambda perf=False: []):
            rc = locked(LA, lambda: held.append(os.path.isdir(lock_path)) or 5)
        expect("locked: the game lock is held while fn runs and gone after", rc == 5 and held == [True] and not os.path.exists(lock_path))
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w") as f:
            f.write(f"other {os.getpid()} {int(time.time())}\n")
        with m7.patched(m7, gate7=lambda perf=False: []), contextlib.redirect_stdout(io.StringIO()):
            busy = raises(m7.LockBusy, locked, SimpleNamespace(**{**vars(LA), "lock_wait": 0.0}), lambda: 0)
        expect("locked: a lock held by another run gives LockBusy", busy)
        shutil.rmtree(lock_path)
        sys_argv = sys.argv
        sys.argv = ["m9.py", "static", "--merged", "G1,g2"]
        so = io.StringIO()
        try:
            with contextlib.redirect_stdout(so):
                rc = main()
        finally:
            sys.argv = sys_argv
        expect("main refuses a bad --merged with REFUSED and rc 1", rc == 1 and "REFUSED" in so.getvalue())
        rc, out = drive_mode(lambda A: finish_mode(lambda r: gfx_body(A, r)), SimpleNamespace(**{**vars(g1), "merged": "G1,G2"}))
        expect("G2 merged with no reset rows fails", rc == 1)
        root = os.path.join(tmp, "root")
        os.makedirs(os.path.join(root, "runtime"))
        with open(os.path.join(root, "runtime", "main.c"), "w") as f:
            f.write(main_ok)
        rc, out = drive_mode(cmd_static, SimpleNamespace(root=root, merged="G1"))
        expect("G1 only merged: mode static checks main.c and skips v_present, the G2b sites and the docs", rc == 0 and out.count("SKIP") == 4)
        good("AC-G2-3 reset there and back", check_reset, parse_m9(m9_line("gfx", "reset", **{"from": "1280x1024", "to": "3440x1440", "ok": 1, "released": 1}) + "\n" + m9_line("gfx", "reset", **{"from": "3440x1440", "to": "1280x1024", "ok": 1, "released": 1})))
        expect("AC-G2-3 no reset rows fail", not result(check_reset, []).ok)
        expect("AC-G2-3 only one direction fails", not result(check_reset, parse_m9(m9_line("gfx", "reset", **{"from": "1280x1024", "to": "3440x1440", "ok": 1, "released": 1}))).ok)
        so = io.StringIO()
        with contextlib.redirect_stdout(so):
            check_proj(Rep(quiet=True), [])
        expect("AC-G2-4 with no proj rows reports OPEN (Q4)", "OPEN (Q4)" in so.getvalue())
        expect("the parser refuses an unknown mode", raises(SystemExit, parsed.parse_args, ["live-newgame"]))
        names = sorted(os.path.basename(f) for f in glob.glob(os.path.join(SCRIPTS, "*")))
        expect(f"harness/m9/ holds exactly contract.txt, look.txt and toggles.txt ({names})", names == ["contract.txt", "look.txt", "toggles.txt"])
        expect("every H mutation of the spec's list of 12 was caught", all(i in caught for i in range(1, 13)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"selftest: {n['ok'] - len(bad)} of {n['ok']} passed; mutations caught {len(caught)}")
    return 0 if not bad else 1


def main():
    a = build_parser().parse_args()
    try:
        if getattr(a, "merged", None) is not None:
            merged_of(a)
        return a.fn(a)
    except (m7.LockBusy, MergedError) as e:
        print(f"REFUSED: {e}")
        return 1
    except (OSError, ValueError, PngError, KeyError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
