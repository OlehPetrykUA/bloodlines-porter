#!/usr/bin/env python3
import argparse
import contextlib
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "tools", "up")))

import game_root
import m4
import m5
import m7
from m4 import Rep, X_DEFAULT, need_exe

MOD = "Unofficial_Patch"
BASE = "Vampire"
GU_DEFAULT = m4.devpaths.dev("up12-basic-gu")
U_DEFAULT = m4.devpaths.dev("up12-basic")
G_DEFAULT = m4.devpaths.GAME
TIMEOUT = 240
CAPTURE_AFTER = 5
QUIT_MARGIN = 30
INVALID = "ffffffff"
OPENS = ("resource\\gameui_english.txt", "resource\\trackerscheme.res", "cfg\\autoexec.cfg")
BAD_LOAD = ("Unabled to load game dll", "LoadLibrary: no module")
MENU_OPS = {"wait", "sleep", "move", "click"}
ALLOWED_PLUS = {"+echo", "+developer", "+mm_verbose"}
FORBIDDEN_ARGS = {"--input-script", "--until", "--game", "--overlay", "--close-after", "--capture-after"}
R1_COMMANDS = (
    "harness/m3.py cases",
    "harness/m4.py menu",
    "harness/m7.py menu",
)

RE_MOD = re.compile(r"^rt: mod (\S+)\s*$", re.M)
RE_MODDIR = re.compile(r"^rt: python moddir (\S+)\s*$", re.M)
RE_OPEN = re.compile(r'^rt: file CreateFileA "([^"]*)" access 0x[0-9A-Fa-f]+ disposition \d+ -> 0x([0-9A-Fa-f]+) error \d+\s*$', re.M)
RE_UPFIX = re.compile(r"^rt: up fix (\S.*) (on|off|not supported)\s*$", re.M)
RE_STAGE5 = re.compile(r'^rt: stage 5 .* "([^"]*)"\s*$', re.M)
RE_STAGE11 =re.compile(r"^rt: stage 11 ", re.M)


class Refused(Exception):
    pass


def norm(p):
    return re.sub(r"\\+", "\\\\", p.replace("/", "\\")).lower()


def guard_extra(extra):
    for t in extra:
        if t in FORBIDDEN_ARGS:
            raise Refused(f"{t} is set by this tool and cannot be passed")
        if t.startswith("+") and t.lower() not in ALLOWED_PLUS:
            raise Refused(f"{t} would go past the main menu; this tool stops at the menu")
        if t.lower() in ("-game", "-nosound"):
            raise Refused(f"{t} is set by this tool and cannot be passed")


def guard_script(text):
    cmds = m5.parse_script(text)
    for n, op, arg in cmds:
        if op not in MENU_OPS:
            raise Refused(f"quit script line {n}: {op} is not a menu-only command")
        if op == "move" and arg.split() and not re.fullmatch(r"\d+ \d+", arg):
            raise Refused(f"quit script line {n}: bad move")
        if op == "click" and arg != "left":
            raise Refused(f"quit script line {n}: only a left click is allowed")
    moves = [arg for _, op, arg in cmds if op == "move"]
    if len(moves) != 1:
        raise Refused(f"the quit script must move to exactly one point (the Quit button), it has {len(moves)}")
    return cmds


def quit_text(menu_path):
    menu = m5.load_menu(menu_path)
    text = m5.render_file("quit", menu)
    guard_script(text)
    return text


def stage5_args(log):
    m = RE_STAGE5.search(log)
    return m[1].split() if m else []


def check_a1(r, log, mod):
    args = stage5_args(log)
    want = mod is not None
    pair = any(args[i] == "-game" and args[i + 1] == MOD for i in range(len(args) - 1))
    r.check("A1 stage 5 args carry -game " + MOD if want else "A1 vanilla args carry no -game", pair if want else "-game" not in args, args)
    m = RE_MOD.findall(log)
    r.check(f"A1 one rt: mod {mod or BASE} line", m == [mod or BASE], m)


def check_a2(r, o, mod):
    here = os.path.join(o, "files", mod or BASE, "logs", "console.log")
    other = os.path.join(o, "files", BASE if mod else MOD, "logs", "console.log")
    r.check(f"A2 console.log under files/{mod or BASE}/logs", os.path.isfile(here), here)
    r.check(f"A2 no console.log under files/{BASE if mod else MOD}/logs", not os.path.exists(other), other)


def check_a3(r, log, mod):
    opens = [(norm(n), h.lower()) for n, h in RE_OPEN.findall(log)]
    for rel in OPENS:
        good = [n for n, h in opens if h != INVALID and n.endswith("\\" + rel) and f"\\{mod.lower()}\\" in n]
        base = [n for n, h in opens if h != INVALID and n.endswith("\\" + rel) and f"\\{BASE.lower()}\\" in n]
        r.check(f"A3 {rel} opens under {mod.lower()} and succeeds", bool(good), base[:1] or "no successful open")


def check_a4(r, log, rc):
    r.check("A4 stage 11 reached", RE_STAGE11.search(log) is not None)
    r.check(f"A4 exit 0 (got {rc})", rc == 0, rc)
    for bad in BAD_LOAD:
        r.check(f"A4 no '{bad}' line", bad not in log)


def check_a7(r, log, mod):
    got = RE_MODDIR.findall(log)
    r.check(f"A7 rt: python moddir {mod or BASE}", bool(got) and got[0] == (mod or BASE), got)


def newer_than(roots, stamp):
    out = []
    for root in roots:
        for dirpath, dirs, names in os.walk(root):
            for n in dirs + names:
                p = os.path.join(dirpath, n)
                try:
                    if os.lstat(p).st_mtime_ns > stamp:
                        out.append(p)
                except OSError:
                    continue
    return out


def check_a5(r, o, g, gu, stamp):
    files = os.path.join(o, "files")
    r.check(f"A5 files/{MOD}/cfg/config.cfg exists", os.path.isfile(os.path.join(files, MOD, "cfg", "config.cfg")))
    r.check(f"A5 files/{BASE}/cfg/config.cfg does not exist", not os.path.exists(os.path.join(files, BASE, "cfg", "config.cfg")))
    r.check(f"A5 files/{BASE}/save does not exist", not os.path.exists(os.path.join(files, BASE, "save")))
    new = newer_than([g, gu], stamp)
    r.check("A5 nothing in G or GU is newer than the stamp", not new, new[:3])


def check_quit(r, log):
    r.check("quit: LauncherMain returned 0 or ExitProcess(0)", re.search(r"LauncherMain returned 0|ExitProcess\(0\)", log) is not None)
    r.check("quit: no forced quit line", "forced quit" not in log)


def manifest_hashes(u):
    p = os.path.join(game_root.app_root(u), "..", "manifest.tsv")
    if not os.path.isfile(p):
        p = os.path.join(game_root.app_root(u), "manifest.tsv")
    with open(p, encoding="utf-8") as f:
        return {l.rstrip("\n").split("\t")[2] for l in f if l.count("\t") == 2}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check_r1(r, o, hashes):
    hit = []
    for dirpath, _, names in os.walk(os.path.join(o, "files", BASE)):
        hit += [os.path.join(dirpath, n) for n in names if sha(os.path.join(dirpath, n)) in hashes]
    r.check(f"R1 files/{BASE} holds no file from U", not hit, hit[:3])


def check_fixlines(r, log):
    rows = RE_UPFIX.findall(log)
    r.check("C3 sys.path, Delete Paths, Cfg-Saves and Create Dirs log on", all(
        ("Mods/" + k, "on") in rows for k in ("Fix sys.path", "Delete Paths", "Fix Cfg-Saves", "Create Dirs")), rows[:3])


class FakeSys:
    def __init__(self, path):
        self.path = list(path)
        self.moddir = None


def eval_py(strings, path=()):
    s = FakeSys(path)
    for line in strings:
        exec(line.replace("import sys; ", ""), {"__builtins__": {}}, {"sys": s})
    return s


def py_lines(text):
    return [l[4:] for l in text.splitlines() if l.startswith("py> ")]


def run_menu(exe, o, gu, mod, timeout):
    extra = ["--game", gu, "--until", "menu", "--capture-after", str(CAPTURE_AFTER)]
    if mod:
        extra += ["--", "-game", mod]
    return m4.run_live(exe, o, extra, timeout)[:5]


def run_quit(exe, o, gu, mod, text):
    path, cmds = m5.prepare(o, "quit", text)
    ca = int(m5.script_seconds(cmds)) + QUIT_MARGIN
    extra = ["--game", gu, "--close-after", str(ca), "--input-script", path, "--", "-game", mod]
    return m5.run_timed(exe, o, extra, ca + 120)


def mkdirs(o, name):
    os.makedirs(os.path.join(o, "files", name, "logs"), exist_ok=True)


def live(a, menu=run_menu, quit_=run_quit, lock=None, gate=None):
    if not need_exe(a):
        return 1
    guard_extra(a.extra)
    text = None if getattr(a, "m_only", False) else quit_text(a.menu)
    try:
        game_root.verify(a.vanilla, a.up, a.game_root)
    except game_root.GameRootError as e:
        print(f"REFUSED: {e}")
        return 1
    hashes = manifest_hashes(a.up)
    lock = lock or (lambda: m7.game_lock(a.lock, owner="m8"))
    gate = gate or (lambda: m7.wait_quiet(False))
    r = Rep()
    with lock():
        why = gate()
        for w in why:
            print(f"REFUSED: {w}")
        if why:
            return 1
        om = a.overlay_m or tempfile.mkdtemp(prefix="m8-mod-")
        ov = a.overlay_v or tempfile.mkdtemp(prefix="m8-van-")
        stamp_file = os.path.join(tempfile.mkdtemp(prefix="m8-stamp-"), "stamp")
        open(stamp_file, "w").close()
        stamp = os.stat(stamp_file).st_mtime_ns
        mkdirs(om, MOD)
        mkdirs(ov, BASE)
        with m7.environ(RT_TRACE_FILES="1", SDL_AUDIO_DRIVER="dummy"):
            rc, log, dt, crashes, _ = menu(a.exe, om, a.game_root, MOD, TIMEOUT)
        m4.check_stages(r, log)
        m4.check_faults(r, log, crashes, rc)
        check_a1(r, log, MOD)
        check_a3(r, log, MOD)
        check_a4(r, log, rc)
        check_a7(r, log, MOD)
        check_fixlines(r, log)
        if getattr(a, "m_only", False):
            check_a2(r, om, MOD)
            print(f"  m-only run, overlay {om}; A5, quit and V not run")
            return 0 if r.ok else 1
        res = quit_(a.exe, om, a.game_root, MOD, text)
        check_quit(r, res.log)
        check_a2(r, om, MOD)
        check_a5(r, om, a.vanilla, a.game_root, stamp)
        rc, log, dt, crashes, _ = menu(a.exe, ov, a.vanilla, None, TIMEOUT)
        m4.check_stages(r, log)
        m4.check_faults(r, log, crashes, rc)
        check_a1(r, log, None)
        check_a7(r, log, None)
        r.check("C4 vanilla log has no rt: up fix line", not RE_UPFIX.search(log))
        check_a2(r, ov, None)
        check_r1(r, ov, hashes)
    print("AC-R1 earlier menu ACs, run separately on G without -game:")
    for c in R1_COMMANDS:
        print(f"  {c}")
    print(f"  overlays: {om} (mod), {ov} (vanilla)")
    return 0 if r.ok else 1


def cmd_live(a):
    try:
        return live(a)
    except (Refused, m5.ScriptError, ValueError, OSError) as e:
        print(f"REFUSED: {e}")
        return 1
    except m7.LockBusy as e:
        print(f"REFUSED: {e}")
        return 1


def build_parser():
    p = argparse.ArgumentParser(prog="m8_launch.py")
    s = p.add_subparsers(dest="cmd", required=True)
    l = s.add_parser("live")
    l.add_argument("--exe", default=X_DEFAULT)
    l.add_argument("--game-root", default=GU_DEFAULT)
    l.add_argument("--up", default=U_DEFAULT)
    l.add_argument("--vanilla", default=G_DEFAULT)
    l.add_argument("--menu", default=m5.MENU_DEFAULT)
    l.add_argument("--lock", default=m7.LOCK_DEFAULT)
    l.add_argument("--overlay-m")
    l.add_argument("--overlay-v")
    l.add_argument("--m-only", action="store_true")
    l.add_argument("extra", nargs="*")
    l.set_defaults(fn=cmd_live)
    t = s.add_parser("selftest")
    t.set_defaults(fn=lambda a: selftest())
    return p


def good_log(mod, rc_line=""):
    five = "rt: stage 5 0x1 args 0x400000 0x0 0x3 1 \"-window -condebug" + (f" -game {mod}" if mod else "") + "\""
    lines = [five if l.startswith("rt: stage 5 ") else l for l in m4.fixture_log().splitlines()]
    lines += [f"rt: mod {mod or BASE}", f"rt: python moddir {mod or BASE}"]
    if mod:
        base = f"c:\\vtmb\\{mod.lower()}\\"
        lines += [f'rt: file CreateFileA "{base}{rel}" access 0x80000000 disposition 3 -> 0x1C error 0' for rel in OPENS]
        lines += [f'rt: up fix Mods/{k} on' for k in ("Fix sys.path", "Delete Paths", "Fix Cfg-Saves", "Create Dirs")]
    return "\n".join(lines) + "\n"


def put(root, rel, data=b"x"):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def selftest():
    r = Rep(quiet=True)
    tmp = tempfile.mkdtemp(prefix="m8-selftest-")
    out = []

    def expect(name, ok, detail=""):
        out.append((name, bool(ok), detail))

    def fails(fn, *args):
        rep = Rep(quiet=True)
        fn(rep, *args)
        return not rep.ok

    def passes(fn, *args):
        rep = Rep(quiet=True)
        fn(rep, *args)
        return rep.ok

    try:
        expect("norm collapses and lowercases", norm("C:\\\\VTMB/Unofficial_Patch//x") == "c:\\vtmb\\unofficial_patch\\x")
        for bad in (["+map", "a"], ["+newgame"], ["+load", "x"], ["--until", "x"], ["-game", "x"], ["--input-script", "f"]):
            try:
                guard_extra(bad)
                expect(f"guard refuses {bad[0]}", False)
            except Refused:
                expect(f"guard refuses {bad[0]}", True)
        guard_extra(["+echo", "X", "-condebug", "+developer", "1"])
        expect("guard allows menu-safe args", True)

        menu = m5.SEED
        text = m5.render_file("quit", menu)
        expect("the M5 quit script is menu-only", len(guard_script(text)) >= 4)
        for name, script in (("a key press", text + "key a\n"), ("a text command", text + 'text "x"\n'), ("a right click", text + "click right\n"),
                             ("two moves", text + "move 1 1\n"), ("no move", "wait menu\nsleep 1\n")):
            try:
                guard_script(script)
                expect(f"quit script with {name} is refused", False)
            except (Refused, m5.ScriptError):
                expect(f"quit script with {name} is refused", True)

        good_m, good_v = good_log(MOD), good_log(None)
        expect("A1 passes on both runs", passes(check_a1, good_m, MOD) and passes(check_a1, good_v, None))
        expect("A1 mutation: -game missing from args", fails(check_a1, good_m.replace(" -game " + MOD, ""), MOD))
        expect("A1 mutation: V run has -game", fails(check_a1, good_m, None))
        expect("A1 mutation: rt: mod Vampire in M", fails(check_a1, good_m.replace(f"rt: mod {MOD}", "rt: mod Vampire"), MOD))
        expect("A7 passes", passes(check_a7, good_m, MOD) and passes(check_a7, good_v, None))
        expect("A7 mutation: no moddir line", fails(check_a7, good_m.replace(f"rt: python moddir {MOD}\n", ""), MOD))
        expect("A7 mutation: moddir Vampire in M", fails(check_a7, good_m.replace(f"moddir {MOD}", "moddir Vampire"), MOD))
        expect("A3 passes", passes(check_a3, good_m, MOD))
        base_open = good_m.replace(f"c:\\vtmb\\{MOD.lower()}\\resource\\trackerscheme.res", "c:\\vtmb\\vampire\\resource\\trackerscheme.res")
        expect("A3 mutation: base trackerscheme.res opened", fails(check_a3, base_open, MOD))
        failed = good_m.replace("-> 0x1C error 0", "-> 0xFFFFFFFF error 2")
        expect("A3 mutation: mod opens fail", fails(check_a3, failed, MOD))
        expect("A4 passes", passes(check_a4, good_m, 0))
        expect("A4 mutation: no stage 11", fails(check_a4, good_m.replace("rt: stage 11 menu", ""), 0))
        expect("A4 mutation: exit 5", fails(check_a4, good_m, 5))
        expect("A4 mutation: game dll error", fails(check_a4, good_m + "guest: Unabled to load game dll\n", 0))
        expect("A4 mutation: LoadLibrary no module", fails(check_a4, good_m + "rt: LoadLibrary: no module x\n", 0))
        expect("C4 up fix regex", RE_UPFIX.search("rt: up fix Mods/Fix sys.path on\n") and RE_UPFIX.search("rt: up fix Bugs/Fix Paths not supported\n"))
        expect("C3 check passes on M, fails without a row", passes(check_fixlines, good_m) and fails(check_fixlines, good_m.replace("Create Dirs on", "Create Dirs off")))

        o = os.path.join(tmp, "o")
        put(o, f"files/{MOD}/logs/console.log")
        put(o, f"files/{MOD}/cfg/config.cfg")
        expect("A2 passes", passes(check_a2, o, MOD))
        put(o, f"files/{BASE}/logs/console.log")
        expect("A2 mutation: console.log also under Vampire", fails(check_a2, o, MOD))
        ov = os.path.join(tmp, "ov")
        put(ov, f"files/{BASE}/logs/console.log")
        expect("A2 vanilla passes", passes(check_a2, ov, None))
        mkdirs(os.path.join(tmp, "o2"), MOD)
        expect("A2 mutation: console.log missing", fails(check_a2, os.path.join(tmp, "o2"), MOD))

        g, gu = os.path.join(tmp, "G"), os.path.join(tmp, "GU")
        put(g, "vampire.exe")
        put(gu, "vampire.exe")
        os.makedirs(os.path.join(o, "files", BASE), exist_ok=True)
        shutil.rmtree(os.path.join(o, "files", BASE))
        stamp = os.stat(os.path.join(g, "vampire.exe")).st_mtime_ns
        expect("A5 passes", passes(check_a5, o, g, gu, stamp + 10**9))
        put(o, f"files/{BASE}/cfg/config.cfg")
        expect("A5 mutation: base config.cfg written", fails(check_a5, o, g, gu, stamp + 10**9))
        shutil.rmtree(os.path.join(o, "files", BASE))
        put(o, f"files/{BASE}/save/quick.sav")
        expect("A5 mutation: base save written", fails(check_a5, o, g, gu, stamp + 10**9))
        shutil.rmtree(os.path.join(o, "files", BASE))
        os.remove(os.path.join(o, "files", MOD, "cfg", "config.cfg"))
        expect("A5 mutation: mod config.cfg missing", fails(check_a5, o, g, gu, stamp + 10**9))
        put(o, f"files/{MOD}/cfg/config.cfg")
        expect("A5 mutation: GU file newer than the stamp", fails(check_a5, o, g, gu, stamp - 10**9))

        ovr = os.path.join(tmp, "ovr")
        put(ovr, f"files/{BASE}/cfg/config.cfg", b"mine")
        expect("R1 passes", passes(check_r1, ovr, {sha(os.path.join(ovr, "files", BASE, "cfg", "config.cfg")) + "0"}))
        expect("R1 mutation: a U file in files/Vampire", fails(check_r1, ovr, {sha(os.path.join(ovr, "files", BASE, "cfg", "config.cfg"))}))

        path = ["base"]
        s = eval_py(["import sys; sys.path.append(\"C:/VTMB/Unofficial_Patch/python\")", "import sys; sys.path.append(\"C:/VTMB/Vampire/python\")"], path)
        expect("eval: mod path before base path", s.path == ["base", "C:/VTMB/Unofficial_Patch/python", "C:/VTMB/Vampire/python"], s.path)
        scenario = [
            "import sys; sys.path.append(\"C:/VTMB/Unofficial_Patch/python\")",
            "import sys; sys.path.append(\"C:/VTMB/Vampire/python\")",
            "import sys; sys.moddir = \"Unofficial_Patch\"",
            "sys.path.append(\"C:/VTMB/Unofficial_Patch/python/sm_pawnshop_1\")",
            "sys.path.append(\"C:/VTMB/Vampire/python/sm_pawnshop_1\")",
            "sys.path = sys.path[:-2]",
            "sys.path.append(\"C:/VTMB/Unofficial_Patch/python/oceanhouse\")",
            "sys.path.append(\"C:/VTMB/Vampire/python/oceanhouse\")",
        ]
        s = eval_py(scenario)
        expect("eval: moddir set", s.moddir == "Unofficial_Patch", s.moddir)
        expect("eval: previous map pair dropped, new pair after the core pair",
               s.path == ["C:/VTMB/Unofficial_Patch/python", "C:/VTMB/Vampire/python", "C:/VTMB/Unofficial_Patch/python/oceanhouse",
                          "C:/VTMB/Vampire/python/oceanhouse"], s.path)
        wrong = [x for x in scenario if "[:-2]" not in x]
        expect("eval mutation: without the deletion the old map stays", "C:/VTMB/Vampire/python/sm_pawnshop_1" in eval_py(wrong).path)
        expect("py_lines parses the transcript", py_lines("PASS x\npy> a = 1\ncall 0x1\npy> b\n") == ["a = 1", "b"])

        gp = os.path.join(tmp, "game")
        put(gp, "vampire.exe", b"MZ")
        up = os.path.join(tmp, "up")
        for rel, d in {"app/Unofficial_Patch/resource/trackerscheme.res": b"t", "app/Vampire/maps/graphs/a.ain": b"g",
                       "app/Bin/loader/loader.ini": b"[Mods]\n"}.items():
            put(up, rel, d)
        put(up, "manifest.tsv", b"a\t1\t" + b"0" * 64 + b"\n")
        groot = os.path.join(tmp, "gameroot")
        game_root.build(gp, up, groot, log=lambda *x: None)
        exe = put(tmp, "x/bloodlines-native")
        menu_json = os.path.join(tmp, "m5-menu.json")
        m5.seed_menu(menu_json)
        calls = []

        def fake_menu(exe_, o_, gu_, mod_, timeout):
            calls.append(("menu", mod_))
            log = good_log(mod_)
            put(o_, f"files/{mod_ or BASE}/logs/console.log")
            return 0, log, 1.0, [], []

        def fake_quit(exe_, o_, gu_, mod_, text_):
            calls.append(("quit", mod_))
            put(o_, f"files/{mod_}/cfg/config.cfg")
            return SimpleNamespace(rc=0, log="LauncherMain returned 0\n", crashes=[], rows=[], lines=[], exit=1.0)

        def args(**kw):
            base = dict(exe=exe, game_root=groot, up=up, vanilla=gp, menu=menu_json, lock="", overlay_m=os.path.join(tmp, "om"),
                        overlay_v=os.path.join(tmp, "ov2"), extra=[])
            base.update(kw)
            return SimpleNamespace(**base)

        ok_gate = dict(lock=lambda: contextlib.nullcontext(), gate=lambda: [])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = live(args(), fake_menu, fake_quit, **ok_gate)
        expect("live: fabricated good runs pass", rc == 0, buf.getvalue()[-400:])
        expect("live: M, quit, V in that order", calls == [("menu", MOD), ("quit", MOD), ("menu", None)], calls)
        for bad in (["+map", "c1"], ["+newgame"]):
            calls.clear()
            try:
                live(args(extra=bad), fake_menu, fake_quit, **ok_gate)
                expect(f"live: extra {bad[0]} refuses before any run", False)
            except Refused:
                expect(f"live: extra {bad[0]} refuses before any run", not calls, calls)
        with contextlib.redirect_stdout(io.StringIO()):
            calls.clear()
            rc = live(args(), fake_menu, fake_quit, lock=lambda: contextlib.nullcontext(), gate=lambda: ["CPU idle 10 % is below 35 %"])
        expect("live: a busy machine refuses without a run", rc == 1 and not calls, (rc, calls))
        heavy = SimpleNamespace(load=14.0, idle=8.0, gpu=90)
        with contextlib.redirect_stdout(io.StringIO()), m7.patched(m7, readings=lambda *x, **k: heavy, procs=lambda *x, **k: [(500, "/x/ninja"), (501, "/x/clang")],
                                                              IDLE_MIN=5.0, GPU_FUNC=95):
            calls.clear()
            rc = live(args(overlay_m=os.path.join(tmp, "om5"), overlay_v=os.path.join(tmp, "ov5")), fake_menu, fake_quit,
                      lock=lambda: contextlib.nullcontext())
        expect("live: the functional gate starts at GPU 90 %, CPU idle 8 % and a build running", rc == 0 and len(calls) == 3, (rc, calls))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = live(args(exe=os.path.join(tmp, "nope")), fake_menu, fake_quit, **ok_gate)
        expect("live: a missing executable fails", rc == 1)
        with contextlib.redirect_stdout(io.StringIO()):
            gr_w = os.path.join(tmp, "gameroot2")
            game_root.build(gp, up, gr_w, log=lambda *x: None, layer=lambda g_, u_, t_: None)
            rc = live(args(game_root=gr_w), fake_menu, fake_quit, **ok_gate)
        expect("live: a game root without the mod is refused", rc == 1)

        def bad_menu(exe_, o_, gu_, mod_, timeout):
            rc_, log, dt, cr, rows = fake_menu(exe_, o_, gu_, mod_, timeout)
            return rc_, log.replace(f"rt: python moddir {MOD}\n", "") if mod_ else log, dt, cr, rows

        with contextlib.redirect_stdout(io.StringIO()):
            rc = live(args(overlay_m=os.path.join(tmp, "om3"), overlay_v=os.path.join(tmp, "ov3")), bad_menu, fake_quit, **ok_gate)
        expect("live mutation: no moddir line in M fails", rc == 1)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = live(args(overlay_m=os.path.join(tmp, "om4"), overlay_v=os.path.join(tmp, "ov4")), fake_menu, lambda *x: SimpleNamespace(
                rc=0, log="LauncherMain returned 0\n", crashes=[], rows=[], lines=[], exit=1.0), **ok_gate)
        expect("live mutation: the quit run writes no mod config.cfg", rc == 1)
    finally:
        with contextlib.suppress(Exception):
            game_root.unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
    for name, ok, detail in out:
        r.check(name, ok, detail)
        print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + str(detail) if detail and not ok else ''}")
    print(f"selftest: {sum(1 for _, ok, _ in out if ok)} PASS, {sum(1 for _, ok, _ in out if not ok)} FAIL")
    return 0 if r.ok else 1


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
