#!/usr/bin/env python3
import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect_logs
import fonts
import gamecheck
import kitconst
import up

DEFAULTS = {"game": "", "mode": "player", "sound": "on", "intros": "play", "subtitles": "on", "aspect": None, "display": None, "extra": ""}
MIGRATE = ("settings.txt", "launcher.json", "registry.reg", "files", "cache/dxvk")
MIGRATE_ROOTS = {r.split("/")[0] for r in MIGRATE}
EXIT_MIGRATE = 8
LOCK_FILE = "launcher.lock"
SAVE_DIRS = ("files/Vampire/SAVE", "files/Unofficial_Patch/save")
MODES = ("player", "developer")
BUNDLE_LIBS = {
    "RT_DXVK_LIB": "Frameworks/libdxvk_d3d9.0.dylib",
    "SDL_VULKAN_LIBRARY": "Frameworks/libvulkan.1.dylib",
    "VK_DRIVER_FILES": "Resources/" + kitconst.ICD_NAME,
}
PLAYER_FLAG = "--player"
NOVID_FLAG = "--novid"
ASPECTS = ("16:9", "21:9", "fit", "engine")
SIZE_RE = re.compile(r"[1-9][0-9]{2,4}x[1-9][0-9]{2,4}")
RATIO_RE = re.compile(r"[1-9][0-9]{0,4}:[1-9][0-9]{0,4}")
SETTINGS_TXT = "settings.txt"


class Log:
    def __init__(self, overlay):
        self.path = Path(overlay) / "logs" / "launcher.log"

    def __call__(self, msg):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg))


def valid(key, v):
    if key == "game":
        return isinstance(v, str)
    if key == "extra":
        try:
            return isinstance(v, str) and shlex.split(v) is not None
        except ValueError:
            return False
    if key == "mode":
        return v in MODES
    if key in ("sound", "subtitles"):
        return v in ("on", "off")
    if key == "intros":
        return v in ("play", "skip")
    if key == "aspect":
        return v is None or v in ASPECTS or (isinstance(v, str) and (SIZE_RE.fullmatch(v) or RATIO_RE.fullmatch(v)) is not None)
    if key == "display":
        return v is None or (type(v) is int and 0 <= v <= 64)
    return False


def read_aspect(overlay):
    try:
        lines = (Path(overlay) / SETTINGS_TXT).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    v = None
    for l in lines:
        if l.startswith("aspect="):
            v = l[7:].strip()
    return v if v != "4:3" and valid("aspect", v) else None


def write_aspect(overlay, v, forget_window=False):
    p = Path(overlay) / SETTINGS_TXT
    drop = ("aspect=", "window=") if forget_window else ("aspect=",)
    try:
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if not l.startswith(drop)]
    except OSError:
        lines = []
    if v is not None:
        lines.append("aspect=" + v)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    os.replace(tmp, p)


def has_aspect_line(overlay):
    try:
        return any(l.startswith("aspect=") for l in (Path(overlay) / SETTINGS_TXT).read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def load_settings(overlay, log):
    s = dict(DEFAULTS)
    p = Path(overlay) / "launcher.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as e:
            log("launcher.json unreadable (%s); using defaults" % e)
            data = {}
        legacy = data.pop("aspect", None) if "aspect" in data else None
        for k, v in data.items():
            if k in DEFAULTS:
                if valid(k, v):
                    s[k] = v
                else:
                    log("launcher.json: bad value for %s; using the default" % k)
        if legacy is not None and valid("aspect", legacy) and not has_aspect_line(overlay):
            write_aspect(overlay, legacy)
            save_settings(overlay, s)
            log("moved the aspect choice from launcher.json to settings.txt")
    s["aspect"] = read_aspect(overlay)
    return s


def save_settings(overlay, s):
    p = Path(overlay) / "launcher.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({k: v for k, v in s.items() if k != "aspect"}, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def find_bundle(arg):
    if arg:
        return Path(arg)
    for p in Path(__file__).resolve().parents:
        if p.suffix == ".app":
            return p
    return None


def read_plist(bundle):
    try:
        return plistlib.loads((Path(bundle) / "Contents/Info.plist").read_bytes())
    except (OSError, ValueError):
        return None


def supports(exe, flag):
    try:
        return b"\0" + flag.encode() + b"\0" in Path(exe).read_bytes()
    except OSError:
        return False


def file_list(root):
    root = Path(root)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


class MigrationError(Exception):
    pass


def migrated_files(root):
    rows = {}
    for rel in MIGRATE:
        p = root / rel
        if p.is_file():
            rows[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir():
            rows.update({rel + "/" + k: v for k, v in file_list(p).items()})
    return rows


def migrate(old, new, log):
    old, new = Path(old), Path(new)
    if not old.is_dir():
        return False
    fd = os.open(old, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return copy_play_data(old, new, log)
    finally:
        os.close(fd)


def copy_play_data(old, new, log):
    if new.exists() and MIGRATE_ROOTS & set(os.listdir(new)):
        return False
    tmp = new.with_name(new.name + ".migrating")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        for rel in MIGRATE:
            src = old / rel
            if src.is_dir():
                shutil.copytree(src, tmp / rel, symlinks=True)
            elif src.is_file():
                (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, tmp / rel)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    want = migrated_files(old)
    if migrated_files(tmp) != want:
        shutil.rmtree(tmp)
        raise MigrationError("the copy differs; nothing moved")
    if new.exists():
        for name in os.listdir(new):
            os.replace(new / name, tmp / name)
    os.replace(tmp, new)
    log("migrated %d files from %s; the old folder is kept" % (len(want), old))
    return True


def game_dir(bundle):
    return Path(bundle) / kitconst.GAME_DIR


def patched_app(bundle):
    return bool(bundle) and (Path(bundle) / "Contents/MacOS" / kitconst.UP_EXE_NAME).is_file()


def build_command(s, bundle, overlay, environ):
    patched = patched_app(bundle)
    exe = bundle / "Contents/MacOS" / (kitconst.UP_EXE_NAME if patched else kitconst.EXE_NAME)
    argv = [str(exe), "--run", "--game", str(game_dir(bundle)), "--overlay", str(overlay)]
    notes = []
    flags = []
    if s["mode"] == "player":
        flags.append(PLAYER_FLAG)
    if s["sound"] == "off":
        flags.append("--nosound")
    if s["intros"] == "skip":
        flags.append(NOVID_FLAG)
    for f in flags:
        if f in (PLAYER_FLAG, NOVID_FLAG) and not supports(exe, f):
            notes.append("omitted %s: this runtime does not have it yet" % f)
        else:
            argv.append(f)
    if s["display"] is not None:
        argv += ["--display", str(s["display"])]
    guest = ["-game", up.MOD] if patched else []
    mod = up.MOD if patched else "Vampire"
    if s["subtitles"] == "on" and not (Path(overlay) / "files" / mod / "cfg" / "config.cfg").exists():
        guest += ["+cl_captions", "1"]
    if s["mode"] == "developer" and s["extra"].strip():
        guest += shlex.split(s["extra"])
    if guest:
        argv += ["--"] + guest
    env = {}
    for k, rel in BUNDLE_LIBS.items():
        env[k] = (environ[k], "kept") if environ.get(k) else (str(bundle / "Contents" / rel), "set")
    return argv, env, notes


def lock_path(overlay):
    return Path(overlay) / LOCK_FILE


def try_lock(overlay):
    lock_path(overlay).parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path(overlay), "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    return f


def lock_held(overlay):
    f = try_lock(overlay)
    if f is None:
        return True
    f.close()
    return False


def read_json(p):
    try:
        d = json.loads(Path(p).read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def newest_crash(crash_dir, after):
    d = Path(crash_dir)
    return [p for p in sorted(d.glob("bloodlines-native*.ips")) if p.stat().st_mtime > after] if d.is_dir() else []


def status(overlay, crash_dir, log, bundle=None):
    s = load_settings(overlay, log)
    if not s["game"] and not (bundle and game_dir(bundle).is_dir()):
        return "no-game", "pick the game folder"
    chk = read_json(Path(overlay) / "game-check.json")
    if chk and chk.get("game") == s["game"] and not chk.get("ok"):
        return "check-failed", "; ".join(chk.get("failures", []))
    if lock_held(overlay):
        return "running", "Play brings the game to the front; Collect logs is on"
    last = read_json(Path(overlay) / "last-run.json")
    if last:
        crashes = newest_crash(crash_dir, last.get("start", 0))
        if last.get("exit") not in (0, None) or crashes:
            why = "exit %s" % last.get("exit") if last.get("exit") else "new crash report"
            if crashes:
                why += " (%s)" % crashes[-1].name
            return "last-run-failed", why + "; Collect logs offered"
    return "ready", ""


def backup_saves(overlay, exe, dry, say):
    ov = Path(overlay)
    if not Path(exe).is_file():
        return
    tag = hashlib.sha256(Path(exe).read_bytes()).hexdigest()[:16]
    marker = ov / "backups" / (".built-" + tag)
    if marker.exists():
        return
    saves = [d for d in SAVE_DIRS if (ov / d).is_dir()]
    dest = ov / "backups" / ("SAVE-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    if dry:
        say("backup: would copy %s to %s" % (", ".join(saves), dest) if saves else "backup: no saves to copy")
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    if saves:
        tmp = dest.with_name(dest.name + ".tmp")
        for d in saves:
            shutil.copytree(ov / d, tmp / Path(d).relative_to("files"))
        os.replace(tmp, dest)
    marker.write_text("")


class DialogError(Exception):
    pass


def osascript(script):
    r = subprocess.run([os.environ.get("BN_OSASCRIPT", "/usr/bin/osascript"), "-e", script], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr


def ask(script):
    code, out, err = osascript(script)
    if code == 0:
        return out
    if kitconst.CANCEL_MARK in err:
        return None
    raise DialogError(err.strip() or "osascript exited %d" % code)


def quote_as(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def alert(message):
    ask(kitconst.DIALOG_SCRIPT % quote_as(message))


def up_lines(bundle):
    if not patched_app(bundle):
        return ["unofficial patch: off"]
    sha, why = up.build_sha(Path(bundle) / "Contents/MacOS" / kitconst.UP_EXE_NAME, game_dir(bundle))
    return ["unofficial patch: on", "unofficial patch build: " + (("sha256 " + sha) if sha else "unknown (%s)" % why)]


def no_game_text(bundle):
    return "%s holds no game data (%s). Build it again with %s." % (Path(bundle).name, kitconst.GAME_DIR, kitconst.KIT_APP_NAME)


def cmd_run(a, bundle, overlay, log):
    s = load_settings(overlay, log)
    why = None if game_dir(bundle).is_dir() else no_game_text(bundle)
    if why:
        print(why, file=sys.stderr)
        log(why)
        if a.alert and not a.dry_run:
            try:
                alert(why)
            except DialogError as e:
                log("dialog failed: %s" % e)
        return 2
    if lock_held(overlay):
        print("already running: would start nothing" if a.dry_run else "already running: nothing started")
        return 0
    argv, env, notes = build_command(s, bundle, overlay, os.environ)
    if a.dry_run:
        print("command: " + shlex.join(argv))
        for i, x in enumerate(argv):
            print("  argv[%d] %s" % (i, x))
        for k, (v, how) in sorted(env.items()):
            print("env %s=%s (%s)" % (k, v, how))
        for n in notes:
            print("note: " + n)
        backup_saves(overlay, argv[0], True, print)
        return 0
    lock = try_lock(overlay)
    if lock is None:
        print("already running: nothing started")
        return 0
    for n in notes:
        log(n)
    fonts.copy_aliases(game_dir(bundle), overlay, log)
    backup_saves(overlay, argv[0], False, log)
    log("start " + shlex.join(argv))
    start = time.time()
    full = dict(os.environ, **{k: v for k, (v, _) in env.items()})
    try:
        with open(Path(overlay) / "logs" / "launcher.log", "a") as out:
            code = subprocess.run(argv, env=full, stdout=out, stderr=subprocess.STDOUT).returncode
    except OSError as e:
        log("cannot start %s: %s" % (argv[0], e))
        print("cannot start the game: %s" % e, file=sys.stderr)
        code = 127
    else:
        log("exit %d" % code)
    (Path(overlay) / "last-run.json").write_text(json.dumps({"start": start, "exit": code}) + "\n")
    lock.close()
    return code


def cmd_set(a, overlay, log, echo=print, prog=None):
    s = load_settings(overlay, log)
    opts = ("game", "mode", "sound", "intros", "subtitles", "aspect", "display", "extra")
    if all(getattr(a, k) is None for k in opts):
        echo(json.dumps(s, sort_keys=True, ensure_ascii=False))
        return 0
    if a.game is not None:
        s["game"] = str(Path(a.game).expanduser())
    for k in ("mode", "sound", "intros", "subtitles", "aspect"):
        if getattr(a, k):
            s[k] = None if getattr(a, k) == "default" else getattr(a, k)
    if a.display is not None:
        s["display"] = None if a.display == "auto" else int(a.display) if a.display.isdigit() else -1
    if a.extra is not None:
        s["extra"] = a.extra
    for k, v in s.items():
        if not valid(k, v):
            print("invalid value for %s: %r" % (k, v), file=sys.stderr)
            return 2
    save_settings(overlay, s)
    if a.aspect:
        write_aspect(overlay, s["aspect"], forget_window=read_aspect(overlay) != s["aspect"])
    echo(json.dumps(s, sort_keys=True, ensure_ascii=False))
    return 0


def check_progress(prog, game, manifest, overlay):
    if not game:
        return 2, "no game folder"
    prog.step("verify", 1, 1, 0.0, 1.0)
    res = gamecheck.check_game(game, manifest, progress=prog.item)
    gamecheck.record(overlay, game, res)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = gamecheck.report(res)
    lines = buf.getvalue().splitlines()
    for l in lines:
        prog.log(l)
    return code, lines[0]


def uninstall_paths(bundle, overlay, work, remove_data):
    cand = []
    if bundle:
        cand += [bundle, bundle.parent / ".staging", bundle.with_name(bundle.name + ".replaced")]
    cand += [work, overlay / "cache/dxvk"]
    if remove_data:
        cand.append(overlay)
    return [p for p in cand if p.exists() or p.is_symlink()]


def protected(p, game, home, overlay, remove_data):
    r = p.resolve()
    ov = Path(overlay).resolve()
    keep = [Path(home).resolve(), Path(__file__).resolve()] + ([game] if game else []) + ([] if remove_data else [ov])
    if any(r == k or r in k.parents for k in keep) or r in ov.parents:
        return True
    if game and game in r.parents or kitconst.KIT_APP_NAME in r.parts:
        return True
    return r.is_dir() and any(kitconst.KIT_APP_NAME in dirs for _, dirs, _ in os.walk(r))


def cmd_uninstall(a, bundle, overlay, home, log):
    s = load_settings(overlay, log)
    work = Path(a.work).expanduser() if a.work else home / "Library/Caches" / kitconst.CACHE_NAME
    game = Path(s["game"]).expanduser().resolve() if s["game"] else None
    paths = uninstall_paths(bundle, overlay, work, a.remove_data)
    me = Path(__file__).resolve()
    for p in paths:
        if me == p.resolve() or p.resolve() in me.parents:
            print("refusing to remove %s: this launcher runs from inside it; uninstall from %s instead; nothing removed" % (p, kitconst.KIT_TITLE), file=sys.stderr)
            return 2
        if protected(p, game, home, overlay, a.remove_data):
            print("refusing to remove %s: it is, holds or lies in the game folder, a kit app, your home folder or your saved data; nothing removed" % p, file=sys.stderr)
            return 2
    if a.yes and not a.dry_run and lock_held(overlay):
        print("the game is running: close it first; nothing removed", file=sys.stderr)
        return 2
    for p in paths:
        print(p)
    if a.yes and not a.dry_run:
        try:
            for p in paths:
                if p.is_symlink() or p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(p)
        except OSError as e:
            print("cannot remove %s: %s" % (e.filename or p, e.strerror or e), file=sys.stderr)
            return 2
        if not a.remove_data:
            log("uninstall: removed %d paths" % len(paths))
    return 0


def kit_header(bundle, overlay, log):
    lines = []
    ver = bundle / "Contents/Resources/kit-version.txt" if bundle else None
    if ver and ver.is_file():
        lines += ["kit: " + l for l in ver.read_text().splitlines()]
    else:
        try:
            lines.append("kit: kit-commit " + kitconst.git_head(kitconst.REPO))
        except kitconst.Fail:
            lines.append("kit: unknown")
    if bundle and (bundle / "Contents/Info.plist").is_file():
        lines.append("app build: %s" % plistlib.loads((bundle / "Contents/Info.plist").read_bytes()).get("CFBundleVersion"))
    lines.append("launcher choices: " + json.dumps(load_settings(overlay, log), sort_keys=True, ensure_ascii=False))
    chk = read_json(Path(overlay) / "game-check.json")
    if chk:
        verdict = "ok" if chk.get("ok") else "failed " + "; ".join(chk.get("failures", []))
        lines.append("game check: %s, %s files, %s s" % (verdict, chk.get("files"), chk.get("seconds")))
    else:
        lines.append("game check: not run")
    return lines


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", default=str(Path.home()))
    common.add_argument("--overlay")
    common.add_argument("--bundle")
    common.add_argument("--crash-dir")
    ap = argparse.ArgumentParser(description=kitconst.GAME_TITLE + " launcher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", parents=[common])
    c.add_argument("--game")
    c.add_argument("--manifest", default=str(gamecheck.MANIFEST))
    c.add_argument("--progress", action="store_true", help="print one JSON object per line")
    t = sub.add_parser("set", parents=[common])
    t.add_argument("--game", help="the game folder the porter builds from; the game itself plays from the app")
    t.add_argument("--mode", choices=MODES)
    t.add_argument("--sound", choices=("on", "off"))
    t.add_argument("--intros", choices=("play", "skip"))
    t.add_argument("--subtitles", choices=("on", "off"), help="on starts the game with captions shown")
    t.add_argument("--aspect", choices=ASPECTS + ("default",), help="screen shape the game is drawn in; default clears the choice")
    t.add_argument("--display", help="display number, or auto")
    t.add_argument("--extra", help="extra guest arguments, Developer mode only")
    r = sub.add_parser("run", parents=[common])
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--alert", action="store_true", help="show a dialog when the run cannot start (double-click)")
    sub.add_parser("status", parents=[common])
    sub.add_parser("session-start", parents=[common])
    u = sub.add_parser("uninstall", parents=[common])
    u.add_argument("--dry-run", action="store_true", help="print the paths that would be removed; remove nothing")
    u.add_argument("--yes", action="store_true", help="remove the paths the dry run lists")
    u.add_argument("--remove-data", action="store_true", help="also remove saves, settings, logs and backups")
    u.add_argument("--work", help="kit work folder (default ~/Library/Caches/%s)" % kitconst.CACHE_NAME)
    g = sub.add_parser("collect-logs", parents=[common])
    g.add_argument("--out-dir")
    g.add_argument("--since", type=float, help="epoch seconds; default is the session start")
    g.add_argument("--last", type=int)
    g.add_argument("--work", help="kit work folder with summary.json and build.log")
    a = ap.parse_args(argv)

    home = Path(a.home)
    support = home / "Library/Application Support"
    overlay = Path(a.overlay) if a.overlay else support / kitconst.SUPPORT_NAME
    crash = Path(a.crash_dir) if a.crash_dir else home / "Library/Logs/DiagnosticReports"
    log = Log(overlay)
    if not a.overlay and a.cmd in ("run", "status", "set", "session-start", "check"):
        try:
            migrate(support / kitconst.OLD_SUPPORT_NAME, overlay, log)
        except (MigrationError, OSError) as e:
            why = "could not move your saves from %s: %s; your old folder is unchanged" % (support / kitconst.OLD_SUPPORT_NAME, e)
            log(why)
            print(why, file=sys.stderr)
            if getattr(a, "alert", False) and not getattr(a, "dry_run", False):
                try:
                    alert(why)
                except DialogError as d:
                    log("dialog failed: %s" % d)
            return EXIT_MIGRATE
    bundle = find_bundle(a.bundle)

    if a.cmd == "check":
        s = load_settings(overlay, log)
        game = a.game or s["game"]
        if a.progress:
            return kitconst.run_json(lambda p: check_progress(p, game, a.manifest, overlay))
        if not game:
            print("no game folder", file=sys.stderr)
            return 2
        res = gamecheck.check_game(game, a.manifest)
        gamecheck.record(overlay, game, res)
        return gamecheck.report(res)
    if a.cmd == "uninstall":
        return cmd_uninstall(a, bundle, overlay, home, log)
    if a.cmd == "set":
        return cmd_set(a, overlay, log)
    if a.cmd == "status":
        state, why = status(overlay, crash, log, bundle)
        print("state: " + state + (" - " + why if why else ""))
        print("subtitles: " + load_settings(overlay, log)["subtitles"])
        print("aspect: " + (load_settings(overlay, log)["aspect"] or "default"))
        for line in up_lines(bundle):
            print(line)
        if bundle and (read_plist(bundle) or {}).get("BNPersonalBuild"):
            print("personal build (GPLv3 FFmpeg)")
        return 0
    if a.cmd == "session-start":
        (overlay / "logs").mkdir(parents=True, exist_ok=True)
        (overlay / "session.json").write_text(json.dumps({"start": time.time()}) + "\n")
        return 0
    if a.cmd == "run":
        if bundle is None:
            print("no app bundle: pass --bundle <%s>" % kitconst.APP_NAME, file=sys.stderr)
            return 2
        return cmd_run(a, bundle, overlay, log)
    since = a.since if a.since is not None else (read_json(overlay / "session.json") or {}).get("start")
    running = lock_held(overlay)
    z = collect_logs.collect(
        overlay, home, out_dir=a.out_dir or home / "Desktop", crash_dir=crash, since=since, last=a.last,
        work=a.work, running=running, header=kit_header(bundle, overlay, log),
    )
    print(z)
    return 0


if __name__ == "__main__":
    sys.exit(main())
