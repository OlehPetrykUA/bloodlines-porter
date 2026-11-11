#!/usr/bin/env python3
import argparse
import fcntl
import io
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import appicon
import gamecheck
import kit_app
import kitconst
import make_kit
import up
from kitconst import APP_NAME, run_json

EXIT_DOCTOR = 5
EXIT_PATCH = 7
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
CLT_TOOLS = ("clang", "ld", "lipo", "otool", "nm", "file", "install_name_tool", "codesign")
MIN_MACOS = 26
MIN_FREE = 3 * 1024**3
CAPABILITIES = ("--game", "--no-oracle", "--prebuilt")
CLT_MESSAGE = "The Xcode Command Line Tools are missing. Run: xcode-select --install"
DEFAULT_WORK = kitconst.WORK
DEFAULT_APP = kitconst.INSTALL / APP_NAME
PIN = re.compile(r"^(meson|ninja)==(\S+)", re.M)
SHIM_DIR = "kit-bin"
STEP_LOG = "kit.log"
LOCK_NAME = "build.lock"
STAGING = ".staging"
UP_OUT = "up"
UP_DATA = "up-data"
GAME_LOCK = "launcher.lock"
QUARANTINE ="com.apple.quarantine"
DEFAULT_OVERLAY = kitconst.SUPPORT
RECOMPILE_SECONDS = 720
PACKAGE_SECONDS = 120


def kit_root():
    return HERE.parents[2]


def kit_env(kit, work):
    path = "%s:%s:%s" % (Path(work) / SHIM_DIR, Path(kit) / "python/bin", SYSTEM_PATH)
    return {"PATH": path, "HOME": os.environ.get("HOME", ""), "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"}


def tool_run(argv, env):
    try:
        r = subprocess.run(argv, env=env, capture_output=True, text=True)
    except OSError as e:
        return 127, str(e)
    return r.returncode, (r.stdout + r.stderr).strip()


def nearest(path):
    p = Path(path).absolute()
    while not p.exists():
        p = p.parent
    return p


def pins():
    return dict(PIN.findall((HERE / "requirements-build.txt").read_text()))


def lock_free(work):
    Path(work).mkdir(parents=True, exist_ok=True)
    f = open(Path(work) / LOCK_NAME, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def version_matches(out, want):
    m = re.match(r"\d+(?:\.\d+)*", out.strip())
    return bool(want) and m is not None and m.group(0) == want


def doctor(kit, work, app, env, mac=None, free=None, work_min=MIN_FREE):
    rc, _ = tool_run(["xcode-select", "-p"], env)
    if rc != 0:
        return [CLT_MESSAGE]
    errs = []
    if tool_run(["xattr", "-p", QUARANTINE, str(kit)], env)[0] == 0:
        errs.append("the kit folder carries %s because it was downloaded: run xattr -dr %s \"%s\" and try again" % (QUARANTINE, QUARANTINE, kit))
    for t in CLT_TOOLS:
        if tool_run(["xcrun", "--find", t], env)[0] != 0:
            errs.append("%s is not in the Command Line Tools" % t)
    machine, version = mac or (platform.machine(), platform.mac_ver()[0])
    if machine != "arm64":
        errs.append("this Mac is %s, an Apple silicon (arm64) Mac is needed" % machine)
    major = int(version.split(".")[0]) if version[:1].isdigit() else 0
    if major < MIN_MACOS:
        errs.append("macOS %s is too old, macOS %d.0 or later is needed" % (version or "unknown", MIN_MACOS))
    free = free or (lambda p: shutil.disk_usage(p).free)
    for label, p, need in (("work folder", work, work_min), ("app folder", Path(app).parent, MIN_FREE)):
        got = free(nearest(p))
        if got < need:
            errs.append("%s needs %d GB free, %.1f GB is free" % (label, need // 1024**3, got / 1024**3))
    py = Path(kit) / "python/bin/python3"
    rc, out = tool_run([str(py), "-I", "-B", "-c", "import tomllib, capstone"], env)
    if rc != 0:
        errs.append("the kit's Python cannot import tomllib and capstone: %s" % out)
    want = pins()
    for name, argv in (("meson", [str(py), "-I", "-B", "-m", "mesonbuild.mesonmain", "--version"]), ("ninja", [str(Path(kit) / "python/bin/ninja"), "--version"])):
        rc, out = tool_run(argv, env)
        if rc != 0 or not version_matches(out, want.get(name)):
            errs.append("%s reports %r, the kit pins %s" % (name, out.strip(), want.get(name)))
    rc, out = tool_run([str(py), "-I", "-B", str(Path(kit) / "src/tools/recomp/build_all.py"), "--help"], env)
    for flag in CAPABILITIES + (("--pgo-profile",) if (Path(kit) / kitconst.PGO_PROFILE).is_file() else ()):
        if flag not in out:
            errs.append("this kit's build_all.py has no %s: cut the kit from a commit that has it" % flag)
    if (Path(work) / LOCK_NAME).is_file():
        held = lock_free(work)
        if held is None:
            errs.append("another build holds %s" % (Path(work) / LOCK_NAME))
        else:
            held.close()
    return errs


def stream(argv, env, log):
    with open(log, "ab") as f:
        return subprocess.run(argv, env=env, stdout=f, stderr=subprocess.STDOUT).returncode


def step(label, argv, env, log, say=None):
    t0 = time.monotonic()
    rc = stream(argv, env, log)
    line = "%s: %s (%.0f s)" % (label, "ok" if rc == 0 else "failed, exit %d" % rc, time.monotonic() - t0)
    if say:
        say(line)
    else:
        print(line, flush=True)
    return rc


def write_shims(kit, work):
    d = Path(work) / SHIM_DIR
    d.mkdir(parents=True, exist_ok=True)
    meson = d / "meson"
    meson.write_text('#!/bin/sh\nexec "%s" -I -B -m mesonbuild.mesonmain "$@"\n' % (Path(kit) / "python/bin/python3"))
    meson.chmod(meson.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def wait_game_lock(overlay, say=None):
    Path(overlay).mkdir(parents=True, exist_ok=True)
    f = open(Path(overlay) / GAME_LOCK, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        text = "The game is running: waiting for it to close before replacing the app"
        say(text) if say else print(text, flush=True)
        fcntl.flock(f, fcntl.LOCK_EX)
    return f


def install(staged, app, overlay=None, say=None):
    with wait_game_lock(overlay or DEFAULT_OVERLAY, say):
        replace_app(staged, app)


def replace_app(staged, app):
    app = Path(app)
    app.parent.mkdir(parents=True, exist_ok=True)
    old = app.with_name(app.name + ".replaced")
    if old.exists():
        shutil.rmtree(old)
    if app.exists():
        app.rename(old)
    try:
        Path(staged).rename(app)
    except OSError:
        if old.exists():
            old.rename(app)
        raise
    if old.exists():
        shutil.rmtree(old)


def add_game(app, src, game, say=print):
    app = Path(app)
    dst = app / kitconst.GAME_DIR
    gr = up.load()[1]
    if os.path.lexists(dst):
        gr.unlock(str(dst))
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not gr.can_clone(str(dst.parent)):
        say("warning: %s cannot clone files; making a full copy" % dst.parent)
    gr.run_cp(["-c", "-R", str(src), str(dst)])
    gr.unlock(str(dst))
    for p in up.find_installers(dst):
        p.unlink()
    stamp = dst / "gu.sha256"
    if stamp.is_file():
        stamp.unlink()
    appicon.add(app, gr.ci_path(str(game), ("vampire.exe",)) or Path(game) / "vampire.exe", kitconst.ICON_FILE, say)
    for argv in (["codesign", "--force", "--sign", "-", str(app)], ["codesign", "--verify", "--deep", "--strict", str(app)]):
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode:
            raise kit_app.Fail("%s failed: %s" % (" ".join(argv[:2]), r.stderr.strip()))
    return dst


def remove_work(work):
    gr = up.load()[1]
    gr.unlock(str(work))
    shutil.rmtree(work)


def build(a, env=None, kit=None):
    if getattr(a, "progress", False):
        return run_json(lambda p: run_build(a, env, kit, p), err2=False)
    return run_build(a, env, kit)[0]


def run_build(a, env, kit, p=None):
    say = p.log if p else print
    kit = Path(kit or kit_root())
    work, app = Path(a.work).expanduser(), Path(a.app).expanduser()
    env = env or kit_env(kit, work)
    overlay = Path(getattr(a, "overlay", None) or DEFAULT_OVERLAY).expanduser()
    game = Path(a.game).expanduser()
    patched = getattr(a, "unofficial_patch", "off") == "on"
    total = 8 if patched else 6
    if p:
        p.step("doctor", 1, total, 0.0, 0.02)
    installer = None
    if patched:
        try:
            installer = up.pick_installer(game, getattr(a, "up_installer", None))
            say(up.installer_line(up.check_installer(installer)))
        except up.UpError as e:
            say("unofficial patch: %s" % e)
            return EXIT_PATCH, "unofficial patch: %s" % e
    errs = doctor(kit, work, app, env, work_min=MIN_FREE * (2 if patched else 1))
    if errs:
        for e in errs:
            say("doctor: " + e)
        return EXIT_DOCTOR, "; ".join("doctor: " + e for e in errs)
    if p:
        p.step("check", 2, total, 0.02, 0.05)
        res = gamecheck.check_game(game, progress=p.item)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = gamecheck.report(res)
        lines = buf.getvalue().splitlines()
        for l in lines:
            say(l)
    else:
        rc = gamecheck.report(gamecheck.check_game(game))
    if rc:
        return rc, lines[0] if p else ""
    lock = lock_free(work)
    if lock is None:
        say("doctor: another build holds %s" % (work / LOCK_NAME))
        return EXIT_DOCTOR, "doctor: another build holds %s" % (work / LOCK_NAME)
    with lock:
        write_shims(kit, work)
        log = work / STEP_LOG
        py = str(kit / "python/bin/python3")
        src = kit / "src"
        jobs = str(min(6, os.cpu_count() or 1))
        fallbacks = src / "tools/recomp/xflags_fallbacks_up.txt"
        root = game
        if patched:
            if p:
                p.step("unofficial-patch", 3, total, 0.05, 0.15, expected=360)
            try:
                root = up.prepare(installer, game, work / UP_DATA, say=say)
            except up.UpError as e:
                say("unofficial patch: %s" % e)
                return EXIT_PATCH, "unofficial patch: %s" % e
        stages = [("recompile", work, [])]
        if patched:
            stages.append(("recompile-patch", work / UP_OUT, ["--up-ini", str(root / "Bin/loader/loader.ini"), "--patch-set", str(src / "tools/up/patches.toml")]
                           + (["--fallbacks", str(fallbacks)] if fallbacks.is_file() else [])))
        first = 4 if patched else 3
        for i, (label, out, extra) in enumerate(stages):
            if p:
                lo = 0.15 + 0.65 * i / len(stages)
                p.step(label, first + i, total, lo, 0.15 + 0.65 * (i + 1) / len(stages), expected=RECOMPILE_SECONDS)
            argv = [py, "-I", "-B", str(src / "tools/recomp/build_all.py"), "--game", str(game), "--out", str(out),
                    "--no-oracle", "--prebuilt", str(kit / "prebuilt"), "--jobs", jobs] + extra
            if (kit / kitconst.PGO_PROFILE).is_file():
                argv += ["--pgo-profile", str(kit / kitconst.PGO_PROFILE)]
            rc = step(label, argv, env, log, p and p.log)
            if rc:
                rc = rc if 1 <= rc <= 4 else 1
                return rc, "%s failed, exit %d" % (label, rc)
        staging = app.parent / STAGING
        if staging.exists():
            remove_work(staging)
        if p:
            p.step("package", total - 2, total, 0.8, 0.9, expected=PACKAGE_SECONDS)
        rc = step("package", [py, "-I", "-B", str(src / "tools/kit/kit_app.py"), "build", "--build", str(work), "--out", str(staging),
                              "--prebuilt", str(kit / "prebuilt")] + (["--up-build", str(work / UP_OUT)] if patched else []), env, log, p and p.log)
        if rc:
            return 1, "package failed, exit %d" % rc
        if p:
            p.step("game-data", total - 1, total, 0.9, 0.97, expected=60)
        try:
            add_game(staging / APP_NAME, root, game, say)
        except Exception as e:
            say("game data failed: %s" % e)
            return 1, "game data failed: %s" % e
        say("game data: %s" % (staging / APP_NAME / kitconst.GAME_DIR).relative_to(staging))
        if p:
            p.step("install", total, total, 0.97, 1.0)
        install(staging / APP_NAME, app, overlay, p and p.log)
        shutil.rmtree(staging, ignore_errors=True)
    say("installed %s" % app)
    if (work / LOCK_NAME).is_file() and work.resolve() != Path.home().resolve():
        remove_work(work)
        say("removed the build work folder %s" % work)
    return 0, "installed %s" % app


def main(argv, env=None, kit=None):
    ap = argparse.ArgumentParser(prog=kitconst.CLI_NAME)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    d, b = sub.add_parser("doctor"), sub.add_parser("build")
    b.add_argument("--game", required=True, help="your game folder, wherever it is; it is only read")
    b.add_argument("--unofficial-patch", choices=("on", "off"), default="off", help="on builds the app with the Unofficial Patch; switching means building again")
    b.add_argument("--up-installer", help="the VTMBup*.exe to use; default: the one installer inside the game folder")
    b.add_argument("--overlay", default=str(DEFAULT_OVERLAY))
    b.add_argument("--progress", action="store_true", help="print one JSON object per line")
    for p in (d, b):
        p.add_argument("--work", default=str(DEFAULT_WORK))
        p.add_argument("--app", default=str(DEFAULT_APP))
    a = ap.parse_args(argv)
    kit = Path(kit or kit_root())
    if a.cmd == "check":
        env = env if env is not None else kit_env(kit, DEFAULT_WORK)
        if tool_run(["xcode-select", "-p"], env)[0] != 0:
            print("check failed: " + CLT_MESSAGE)
            return EXIT_DOCTOR
        errs = make_kit.check(kit, leaks=False)
        for e in errs:
            print("check failed: " + e)
        if not errs:
            print("kit check passed")
        return 1 if errs else 0
    if a.cmd == "doctor":
        work = Path(a.work).expanduser()
        errs = doctor(kit, work, Path(a.app).expanduser(), env or kit_env(kit, work))
        for e in errs:
            print("doctor: " + e)
        if not errs:
            print("doctor ok")
        return EXIT_DOCTOR if errs else 0
    return build(a, env, kit)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
