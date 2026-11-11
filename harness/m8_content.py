#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "tools", "up")))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "tools")))

import formats
import m4
import m7
import vpk_list
from m4 import Rep, X_DEFAULT, need_exe

MOD = "Unofficial_Patch"
GU_DEFAULT = m4.devpaths.dev("up12-basic-gu")
U_DEFAULT = m4.devpaths.dev("up12-basic")
G_DEFAULT = m4.devpaths.GAME
TIMEOUT = 240
CAPTURE_AFTER = 5
PY_TIMEOUT = 300
INVALID = "ffffffff"
ALLOWED_PLUS = {"+echo", "+developer", "+mm_verbose"}
FORBIDDEN_ARGS = {"--input-script", "--until", "--game", "--overlay", "--close-after", "--capture-after"}

RE_OPEN = re.compile(r'^rt: file CreateFileA "([^"]*)" access 0x[0-9A-Fa-f]+ disposition \d+ -> 0x([0-9A-Fa-f]+) error \d+\s*$', re.M)
RE_PYERR = re.compile(r"Python Error!", re.I)
RE_STAGE11 = re.compile(r"^rt: stage 11 ", re.M)
RE_PY_OK = re.compile(r"^py21 ok (.+?)\s*$", re.M)
RE_PY_FAIL = re.compile(r"^py21 fail (.+):(\d+) (.*)$", re.M)
RE_PY_TOTAL = re.compile(r"^py21 total (\d+) ok (\d+) fail (\d+)\s*$", re.M)
RE_PY_MODDIR = re.compile(r"^py21 moddir (\S+)\s*$", re.M)


class Refused(Exception):
    pass


def guard_extra(extra):
    for t in extra:
        if t in FORBIDDEN_ARGS or t.lower() in ("-game", "-nosound"):
            raise Refused(f"{t} is set by this tool and cannot be passed")
        if t.startswith("+") and t.lower() not in ALLOWED_PLUS:
            raise Refused(f"{t} would go past the main menu; this tool stops at the menu")


def mod_dir(tree):
    for c in (os.path.join(tree, "app", MOD), os.path.join(tree, MOD), tree):
        if os.path.basename(c) == MOD and os.path.isdir(c):
            return c
    raise Refused(f"no {MOD} folder under {tree}")


def py_files(mod):
    out = []
    for d, _, fs in os.walk(os.path.join(mod, "python")):
        out += [os.path.join(d, f) for f in fs if f.lower().endswith(".py")]
    return sorted(out)


def norm(p):
    return re.sub(r"\\+", "/", p.replace("\\", "/")).lower()


class Known:
    def __init__(self, files):
        self.files = {f.lower() for f in files}
        self.dirs = {f.rsplit("/", 1)[0] for f in self.files if "/" in f}
        self.dirs |= {d.rsplit("/", 1)[0] for d in self.dirs if "/" in d}

    def has(self, rel):
        return rel in self.files or rel in self.dirs


def known_u(mod):
    return Known(rel for rel, _ in formats.scan_loose(mod))


def known_g(game):
    files = [rel for rel, _ in formats.scan_loose(os.path.join(game, "Vampire"))]
    files += [e.path.replace("\\", "/") for e in vpk_list.all_entries(game) if not e.dir_only]
    return Known(files)


def check_py(r, out, want):
    ok = RE_PY_OK.findall(out)
    bad = RE_PY_FAIL.findall(out)
    tot = RE_PY_TOTAL.findall(out)
    r.check("B1 one py21 total line", len(tot) == 1, tot)
    if tot:
        n, k, f = map(int, tot[0])
        r.check(f"B1 {want} files, all ok", (n, k, f) == (want, want, 0), tot[0])
        r.check("B1 ok and fail lines add up to the total", len(ok) == k and len(bad) == f, (len(ok), len(bad)))
    for p, ln, msg in bad:
        r.check(f"B1 {p}:{ln}", False, msg)


def check_moddir(r, out, mod):
    got = RE_PY_MODDIR.findall(out)
    r.check(f"B1 py21 moddir {mod}", got == [mod], got)


def failed_opens(log, mod):
    base = f"c:/vtmb/{mod.lower()}/"
    out = []
    for name, h in RE_OPEN.findall(log):
        n = norm(name)
        if h.lower() == INVALID and n.startswith(base):
            out.append(n[len(base):])
    return sorted(set(out))


def check_b4(r, log, rc, mod, ku, kg):
    m4.check_stages(r, log)
    r.check("B4 stage 11 reached", RE_STAGE11.search(log) is not None)
    r.check(f"B4 exit 0 (got {rc})", rc == 0, rc)
    r.check("B4 no Python Error! line", not RE_PYERR.search(log), [l for l in log.splitlines() if RE_PYERR.search(l)][:3])
    for rel in failed_opens(log, mod):
        r.check(f"B4 failed open {rel} names a file in neither U, G nor the VPKs", not ku.has(rel) and not kg.has(rel))


def run_py21(exe, mod, game, timeout=PY_TIMEOUT):
    cmd = [exe, "--py21-compile", os.path.join(mod, "python"), "--game", game, "--mod", MOD]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def run_menu(exe, o, gu, timeout):
    extra = ["--game", gu, "--until", "menu", "--capture-after", str(CAPTURE_AFTER), "--", "-game", MOD]
    return m4.run_live(exe, o, extra, timeout)[:5]


def cmd_static(a):
    mod = mod_dir(a.up)
    lines, problems = formats.run(mod, a.vanilla)
    r = Rep()
    print("\n".join(l for l in lines if not l.startswith("formats fail")))
    r.check("B2 every header version is one vanilla already reads", not any("not in vanilla" in p or p.startswith("text") for p in problems), problems[:5])
    r.check("B3 no case-only path pairs", not any(p.startswith("case pair") for p in problems), [p for p in problems if p.startswith("case pair")][:5])
    r.check("B3 no names outside CP1252", not any(p.startswith("not CP1252") for p in problems), [p for p in problems if p.startswith("not CP1252")][:5])
    return 0 if r.ok else 1


def cmd_py21(a):
    if not need_exe(a):
        return 1
    mod = mod_dir(a.up)
    r = Rep()
    rc, out = run_py21(a.exe, mod, a.game_root)
    print(out, end="")
    r.check(f"B1 exit {rc} matches the fail count", rc == 0, rc)
    check_py(r, out, len(py_files(mod)))
    check_moddir(r, out, MOD)
    return 0 if r.ok else 1


def live(a, menu=run_menu, lock=None, gate=None, known=None):
    if not need_exe(a):
        return 1
    guard_extra(a.extra)
    mod = mod_dir(a.up)
    lock = lock or (lambda: m7.game_lock(a.lock, owner="m8-content"))
    gate = gate or m7.gate7
    ku, kg = known or (known_u(mod), known_g(a.vanilla))
    r = Rep()
    with lock():
        why = gate()
        for w in why:
            print(f"REFUSED: {w}")
        if why:
            return 1
        o = a.overlay or tempfile.mkdtemp(prefix="m8-content-")
        os.makedirs(os.path.join(o, "files", MOD, "logs"), exist_ok=True)
        with m7.environ(RT_TRACE_FILES="1"):
            rc, log, dt, crashes, _ = menu(a.exe, o, a.game_root, TIMEOUT)
        m4.check_faults(r, log, crashes, rc)
        check_b4(r, log, rc, MOD, ku, kg)
        print(f"  overlay {o}; {dt:.1f} s")
    return 0 if r.ok else 1


def cmd_live(a):
    try:
        return live(a)
    except (Refused, ValueError) as e:
        print(f"REFUSED: {e}")
        return 1
    except m7.LockBusy as e:
        print(f"REFUSED: {e}")
        return 1


def build_parser():
    p = argparse.ArgumentParser(prog="m8_content.py")
    s = p.add_subparsers(dest="cmd", required=True)

    def common(x):
        x.add_argument("--up", default=U_DEFAULT)
        x.add_argument("--vanilla", default=G_DEFAULT)

    st = s.add_parser("static")
    common(st)
    st.set_defaults(fn=cmd_static)
    py = s.add_parser("py21")
    common(py)
    py.add_argument("--exe", default=X_DEFAULT)
    py.add_argument("--game-root", default=GU_DEFAULT)
    py.set_defaults(fn=cmd_py21)
    lv = s.add_parser("live")
    common(lv)
    lv.add_argument("--exe", default=X_DEFAULT)
    lv.add_argument("--game-root", default=GU_DEFAULT)
    lv.add_argument("--lock", default=m7.LOCK_DEFAULT)
    lv.add_argument("--overlay")
    lv.add_argument("extra", nargs="*")
    lv.set_defaults(fn=cmd_live)
    t = s.add_parser("selftest")
    t.set_defaults(fn=lambda a: selftest())
    return p


def py_out(files, fails=(), moddir=MOD):
    lines = [f"py21 ok {f}" for f in files] + [f"py21 fail {p}:{n} {m}" for p, n, m in fails]
    lines.append(f"py21 total {len(files) + len(fails)} ok {len(files)} fail {len(fails)}")
    return "\n".join(lines + [f"py21 moddir {moddir}"]) + "\n"


def open_line(path, h):
    return f'rt: file CreateFileA "{path}" access 0x80000000 disposition 3 -> 0x{h} error 0'


def b4_log(extra=()):
    return m4.fixture_log() + "\n".join(extra) + "\n"


def put(root, rel, data=b"x"):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def selftest():
    out = []

    def expect(name, ok, detail=""):
        out.append((name, bool(ok), detail))

    def result(fn, *args):
        r = Rep(quiet=True)
        fn(r, *args)
        return r

    expect("guard: --until, -game, -nosound and +map are refused", all(_refused(x) for x in (["--until"], ["-game"], ["-nosound"], ["+map", "x"])))
    expect("guard: +echo and a plain argument pass", not _refused(["+echo", "hi"]) and not _refused(["-window"]))

    tmp = tempfile.mkdtemp(prefix="m8-content-selftest-")
    mod = os.path.join(tmp, "app", MOD)
    for rel in ("python/a.py", "python/lib/b.PY", "python/c.txt", "maps/x.bsp", "materials/dir/m.vmt"):
        put(mod, rel)
    expect("mod_dir finds app/Unofficial_Patch", mod_dir(tmp) == mod)
    expect("py_files lists .py case-insensitively and sorted", [os.path.basename(f) for f in py_files(mod)] == ["a.py", "b.PY"])
    expect("mod_dir refuses a tree without the mod", _raises(lambda: mod_dir(os.path.join(tmp, "nope"))))

    names = ["python/a.py", "python/b.py"]
    expect("B1 passes 2 ok", result(check_py, py_out(names), 2).ok)
    expect("B1 mutation: one fail line fails and names file and line", not result(check_py, py_out(names[:1], [("python/b.py", 7, "invalid syntax")]), 2).ok)
    expect("B1 mutation: a missing file (fewer than wanted) fails", not result(check_py, py_out(names[:1]), 2).ok)
    expect("B1 mutation: no total line fails", not result(check_py, "py21 ok x\n", 1).ok)
    expect("B1 mutation: total that disagrees with the lines fails", not result(check_py, "py21 ok a\npy21 total 1 ok 1 fail 0\npy21 fail b:1 x\n", 1).ok)
    expect("B1 moddir passes and a wrong one fails", result(check_moddir, py_out([]), MOD).ok and not result(check_moddir, py_out([], moddir="Other"), MOD).ok)

    ku = Known(["maps/x.bsp", "scripts/kb_act.lst"])
    kg = Known(["cfg/config.cfg", "materials/dir/m.vmt"])
    base = "C:\\VTMB\\Unofficial_Patch\\"
    miss = open_line(base + "nothing\\here.txt", INVALID)
    good = b4_log([miss, open_line(base + "maps\\x.bsp", "1C")])
    expect("failed_opens keeps invalid handles under the mod only", failed_opens(good + open_line("C:\\VTMB\\Vampire\\q.txt", INVALID) + "\n", MOD) == ["nothing/here.txt"])
    expect("B4 passes: exit 0, stage 11, a failed open of a file nobody has", result(check_b4, good, 0, MOD, ku, kg).ok)
    expect("B4 mutation: a failed open of a file U has fails", not result(check_b4, b4_log([open_line(base + "Maps\\X.BSP", INVALID)]), 0, MOD, ku, kg).ok)
    expect("B4 mutation: a failed open of a G loose file fails", not result(check_b4, b4_log([open_line(base + "cfg\\config.cfg", INVALID)]), 0, MOD, ku, kg).ok)
    expect("B4 mutation: a failed open of a folder U has fails", not result(check_b4, b4_log([open_line(base + "materials\\dir", INVALID)]), 0, MOD, ku, kg).ok)
    expect("B4 mutation: Python Error! fails", not result(check_b4, b4_log(["Python Error! line 3"]), 0, MOD, ku, kg).ok)
    expect("B4 mutation: exit 1 fails", not result(check_b4, good, 1, MOD, ku, kg).ok)
    expect("B4 mutation: no stage 11 fails", not result(check_b4, "rt: stage 1 host\n", 0, MOD, ku, kg).ok)

    a = build_parser().parse_args(["live", "--up", tmp, "--exe", sys.executable])
    calls = []

    def fake_menu(exe, o, gu, timeout):
        calls.append((o, gu))
        return 0, good, 1.0, [], []

    class L:
        def __enter__(self):
            calls.append("lock")

        def __exit__(self, *e):
            calls.append("unlock")

    expect("live runs the menu inside the lock and passes", live(a, fake_menu, L, lambda: [], (ku, kg)) == 0 and calls[0] == "lock" and calls[-1] == "unlock" and len(calls) == 3)
    calls.clear()
    expect("live refuses on a busy gate without running the menu", live(a, fake_menu, L, lambda: ["busy"], (ku, kg)) == 1 and not any(isinstance(c, tuple) for c in calls))
    calls.clear()
    heavy = SimpleNamespace(load=14.0, idle=8.0, gpu=90)
    with m7.patched(m7, readings=lambda *x, **k: heavy, procs=lambda *x, **k: [(500, "/x/ninja")], IDLE_MIN=5.0, GPU_FUNC=95):
        expect("live uses the functional gate: it starts at GPU 90 %, CPU idle 8 % and a build running",
               live(a, fake_menu, L, None, (ku, kg)) == 0 and len([c for c in calls if isinstance(c, tuple)]) == 1)
    a.extra = ["+map", "x"]
    expect("live refuses a +map argument", _raises(lambda: live(a, fake_menu, L, lambda: [], (ku, kg)), Refused))
    expect("live source only ever passes --until menu", 'extra = ["--game", gu, "--until", "menu"' in open(__file__).read())

    for n, ok, d in out:
        print(f"{'PASS' if ok else 'FAIL'} {n}{': ' + str(d) if d and not ok else ''}")
    print(f"selftest: {sum(1 for _, ok, _ in out if ok)} PASS, {sum(1 for _, ok, _ in out if not ok)} FAIL")
    return 0 if all(ok for _, ok, _ in out) else 1


def _refused(extra):
    return _raises(lambda: guard_extra(extra), Refused)


def _raises(fn, exc=Refused):
    try:
        fn()
    except exc:
        return True
    return False


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
