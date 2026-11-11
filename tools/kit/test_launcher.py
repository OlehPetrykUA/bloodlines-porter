#!/usr/bin/env python3
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
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect_logs
import gamecheck
import kitconst
import launcher

HOLD = "import fcntl,sys,time\nf=open(sys.argv[1],'a+')\nfcntl.flock(f,fcntl.LOCK_EX)\nprint('ready',flush=True)\ntime.sleep(60)\n"
APPEND = "import fcntl,sys,time\nf=open(sys.argv[1],'a+')\nfcntl.flock(f,fcntl.LOCK_EX)\ng=open(sys.argv[2],'a')\nprint('ready',flush=True)\nwhile True:\n g.write('line\\n');g.flush();time.sleep(0.05)\n"


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = launcher.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def put(p, data, mtime=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="k2-"))
        self.home = self.tmp / "Users" / "someone"
        self.overlay = self.home / "Library/Application Support" / kitconst.SUPPORT_NAME
        self.crash = self.home / "Library/Logs/DiagnosticReports"
        self.bundle = self.tmp / "Games (mine)" / kitconst.APP_NAME
        self.exe = put(self.bundle / "Contents/MacOS/bloodlines-native", b"\0--player\0--novid\0--nosound\0")
        self.exe.chmod(0o755)
        self.game_data = put(self.bundle / kitconst.GAME_DIR / "vampire.exe", b"exe").parent
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
            p.stdout.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def holder(self, script, *args):
        p = subprocess.Popen([sys.executable, "-I", "-c", script, *args], stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        self.assertEqual(p.stdout.readline().strip(), "ready")
        return p

    def L(self, *argv):
        return call(*argv, "--home", str(self.home), "--overlay", str(self.overlay), "--crash-dir", str(self.crash))

    def dry(self):
        return self.L("run", "--dry-run", "--bundle", str(self.bundle))


class GameCheck(Base):
    def make_game(self):
        g = self.tmp / "game"
        files = {"Bin/engine.dll": b"engine", "Vampire/pack000.vpk": b"pack", "vampire.exe": b"exe"}
        for n, d in files.items():
            put(g / n, d)
        rows = ["%s  %s" % (hashlib.sha256(d).hexdigest(), n) for n, d in files.items()]
        rows.append("%s  Vampire/.DS_Store" % ("0" * 64))
        put(self.tmp / "manifest.txt", "\n".join(rows) + "\n")
        return g

    def check(self, g):
        return self.L("check", "--game", str(g), "--manifest", str(self.tmp / "manifest.txt"))

    def test_good_copy_and_unchanged(self):
        g = self.make_game()
        before = {p: p.read_bytes() for p in g.rglob("*") if p.is_file()}
        code, out, _ = self.check(g)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^game ok: patch 1\.2, 3 files \(")
        self.assertEqual(before, {p: p.read_bytes() for p in g.rglob("*") if p.is_file()})
        self.assertTrue(json.loads((self.overlay / "game-check.json").read_text())["ok"])

    def test_changed_byte_names_file(self):
        g = self.make_game()
        (g / "Bin/engine.dll").write_bytes(b"engind")
        code, out, _ = self.check(g)
        self.assertEqual(code, 6)
        self.assertIn("changed: Bin/engine.dll", out)
        self.assertNotIn("pack000", out)

    def test_missing_file_names_file(self):
        g = self.make_game()
        (g / "Vampire/pack000.vpk").unlink()
        code, out, _ = self.check(g)
        self.assertEqual(code, 6)
        self.assertIn("missing: Vampire/pack000.vpk", out)

    def test_case_and_extras_pass(self):
        g = self.make_game()
        (g / "Bin").rename(g / "BIN")
        (g / "BIN/engine.dll").rename(g / "BIN/ENGINE.DLL")
        (g / "vampire.exe").rename(g / "Vampire.EXE")
        put(g / "Vampire/SAVE/x.sav", b"save")
        put(g / "steamapps/appmanifest.acf", b"x")
        put(g / "Vampire/.DS_Store", b"junk")
        code, out, _ = self.check(g)
        self.assertEqual(code, 0, out)

    def test_ds_store_rows_skipped(self):
        self.make_game()
        rows = gamecheck.load_manifest(self.tmp / "manifest.txt")
        self.assertEqual(len(rows), 3)

    def test_repo_manifest_parses(self):
        rows = gamecheck.load_manifest(gamecheck.MANIFEST)
        self.assertGreater(len(rows), 5000)
        self.assertFalse([n for _, n in rows if n.endswith(".DS_Store")])


CANCELLED = (1, "", "0:30: execution error: User canceled. (-128)")
FAILED = (1, "", "0:30: execution error: osascript is not allowed assistive access. (-1743)")


def OK(out):
    return 0, out, ""


class Picker(Base):
    def setUp(self):
        super().setUp()
        self.exe.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.good = GameCheck.make_game(self)
        self.bad = self.tmp / "wrong folder"
        put(self.bad / "readme.txt", b"x")
        self.saved = (gamecheck.MANIFEST, launcher.osascript)
        gamecheck.MANIFEST = self.tmp / "manifest.txt"
        self.scripts = []
        self.picks = []
        self.retries = []
        launcher.osascript = self.fake_osascript

    def tearDown(self):
        gamecheck.MANIFEST, launcher.osascript = self.saved
        super().tearDown()

    def fake_osascript(self, script):
        self.scripts.append(script)
        if "choose folder" in script:
            return self.picks.pop(0)
        if "Choose Again" in script:
            return self.retries.pop(0)
        return OK("")

    def alerts(self):
        return [s for s in self.scripts if "choose folder" not in s and "Choose Again" not in s]

    def run_alert(self, *extra):
        return self.L("run", "--alert", "--bundle", str(self.bundle), *extra)

    def saved_game(self):
        p = self.overlay / "launcher.json"
        return json.loads(p.read_text()).get("game") if p.is_file() else None

    def test_game_plays_from_the_bundle_without_a_chosen_folder(self):
        code, _, _ = self.run_alert()
        self.assertEqual(code, 0)
        self.assertEqual(self.scripts, [])
        self.assertIsNone(self.saved_game())
        self.assertEqual(json.loads((self.overlay / "last-run.json").read_text())["exit"], 0)

    def test_app_without_game_data_alerts_once_and_starts_nothing(self):
        shutil.rmtree(self.game_data)
        code, _, err = self.run_alert()
        self.assertEqual(code, 2)
        self.assertIn("holds no game data", err)
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn(kitconst.KIT_APP_NAME, self.alerts()[0])
        self.assertFalse((self.overlay / "last-run.json").exists())

    def test_quote_as_escapes_backslash_then_quote(self):
        self.assertEqual(launcher.quote_as('a\\b"c'), 'a\\\\b\\"c')
        self.assertEqual(launcher.quote_as("\\"), "\\\\")
        self.assertEqual(launcher.quote_as('\\"'), '\\\\\\"')

    def test_no_flag_or_dry_run_never_asks(self):
        shutil.rmtree(self.game_data)
        self.L("run", "--bundle", str(self.bundle))
        self.run_alert("--dry-run")
        self.assertEqual(self.scripts, [])


class OsascriptOverride(Base):
    def test_env_points_osascript_at_a_stub(self):
        stub = self.tmp / "osa"
        stub.write_text('#!/bin/sh\necho "stub $2"\nexit 3\n')
        stub.chmod(0o755)
        saved = os.environ.get("BN_OSASCRIPT")
        os.environ["BN_OSASCRIPT"] = str(stub)
        try:
            self.assertEqual(launcher.osascript("display dialog"), (3, "stub display dialog", ""))
        finally:
            if saved is None:
                del os.environ["BN_OSASCRIPT"]
            else:
                os.environ["BN_OSASCRIPT"] = saved


class Wrapper(Base):
    def stub(self, name, body):
        p = self.tmp / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)
        return str(p)

    def run_wrapper(self, tools_ok, *args):
        log = self.tmp / "calls.log"
        log.write_text("")
        py = self.stub("python3", 'echo "python $*" >> "%s"\n' % log)
        xs = self.stub("xcode-select", "exit %d\n" % (0 if tools_ok else 1))
        osa = self.stub("osascript", 'echo "osascript $*" >> "%s"\n' % log)
        text = kitconst.WRAPPER.replace("/usr/bin/xcode-select", xs).replace("/usr/bin/osascript", osa).replace(kitconst.PYTHON, py)
        w = self.tmp / "Contents/MacOS/Bloodlines Native"
        put(w, text)
        w.chmod(0o755)
        r = subprocess.run([str(w), *args], capture_output=True, text=True)
        return r.returncode, log.read_text().splitlines()

    def launcher_args(self, line):
        return line.split("launcher.py ", 1)[1]

    def test_no_arguments_runs_with_alert(self):
        code, calls = self.run_wrapper(True)
        self.assertEqual(code, 0)
        self.assertEqual([self.launcher_args(c) for c in calls], ["run --alert"])

    def test_dry_run_flag_goes_to_run(self):
        _, calls = self.run_wrapper(True, "--dry-run", "--home", "/h h")
        self.assertEqual([self.launcher_args(c) for c in calls], ["run --alert --dry-run --home /h h"])

    def test_subcommands_pass_through(self):
        for sub in self.wrapper_subcommands():
            _, calls = self.run_wrapper(True, sub, "x y")
            self.assertEqual([self.launcher_args(c) for c in calls], [sub + " x y"])

    def test_no_command_line_tools_shows_dialog_and_exits_1(self):
        code, calls = self.run_wrapper(False)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("osascript"))
        self.assertIn("xcode-select --install", calls[0])

    def wrapper_subcommands(self):
        m = re.search(r'case "\$1" in\n\s+([^)]+)\)', kitconst.WRAPPER)
        return m.group(1).split("|")

    def test_wrapper_cases_match_launcher_subcommands(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            launcher.main(["--help"])
        m = re.search(r"\{([a-z\-,]+)\}", out.getvalue())
        self.assertEqual(sorted(self.wrapper_subcommands()), sorted(m.group(1).split(",")))


class DryRun(Base):
    def setUp(self):
        super().setUp()
        self.source = str(self.tmp / "Vampire Bloodlines (Windows)")
        self.set("--game", self.source)
        self.game = str(self.bundle / kitconst.GAME_DIR)
        self.env = {k: os.environ.pop(k) for k in launcher.BUNDLE_LIBS if k in os.environ}

    def tearDown(self):
        os.environ.update(self.env)
        super().tearDown()

    def set(self, *a):
        code, _, err = self.L("set", *a)
        self.assertEqual(code, 0, err)

    def argv(self):
        code, out, err = self.dry()
        self.assertEqual(code, 0, err)
        return [l.split(" ", 3)[3] for l in out.splitlines() if l.startswith("  argv[")], out

    def test_player_default(self):
        argv, _ = self.argv()
        self.assertEqual(argv, [str(self.exe), "--run", "--game", self.game, "--overlay", str(self.overlay), "--player", "--", "+cl_captions", "1"])

    def test_developer_has_no_player_flag(self):
        self.set("--mode", "developer")
        argv, _ = self.argv()
        self.assertEqual(argv, [str(self.exe), "--run", "--game", self.game, "--overlay", str(self.overlay), "--", "+cl_captions", "1"])

    def test_subtitles_default_on_adds_captions(self):
        argv, _ = self.argv()
        self.assertEqual(argv[argv.index("--") + 1:], ["+cl_captions", "1"])

    def test_subtitles_off_adds_nothing(self):
        self.set("--subtitles", "off")
        argv, _ = self.argv()
        self.assertNotIn("--", argv)
        self.assertNotIn("+cl_captions", argv)

    def test_subtitles_setting_applies_in_developer_mode(self):
        self.set("--mode", "developer", "--subtitles", "off")
        self.assertNotIn("+cl_captions", self.argv()[0])
        self.set("--subtitles", "on")
        self.assertEqual(self.argv()[0][-2:], ["+cl_captions", "1"])

    def test_subtitles_comes_before_developer_extra(self):
        self.set("--mode", "developer", "--extra", "+map x")
        argv, _ = self.argv()
        self.assertEqual(argv[argv.index("--") + 1:], ["+cl_captions", "1", "+map", "x"])

    def test_status_shows_subtitles(self):
        self.assertIn("subtitles: on", self.L("status")[1])
        self.set("--subtitles", "off")
        self.assertIn("subtitles: off", self.L("status")[1])

    def test_aspect_is_never_a_runtime_argument(self):
        self.set("--aspect", "21:9")
        self.assertNotIn("--aspect", self.argv()[0])

    def test_captions_only_until_the_game_has_its_own_config(self):
        self.assertEqual(self.argv()[0][-2:], ["+cl_captions", "1"])
        cfg = self.overlay / "files/Vampire/cfg/config.cfg"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("cl_captions 0\n")
        argv = self.argv()[0]
        self.assertNotIn("+cl_captions", argv)
        self.assertNotIn("--", argv)

    def test_status_shows_aspect(self):
        self.assertIn("aspect: default", self.L("status")[1])
        self.set("--aspect", "fit")
        self.assertIn("aspect: fit", self.L("status")[1])

    def test_status_shows_a_ratio_from_settings(self):
        settings = self.overlay / launcher.SETTINGS_TXT
        for v, want in (("16:10", "16:10"), ("2560x1600", "2560x1600"), ("16:0", "default"), ("0:9", "default"), ("4:3", "default")):
            with self.subTest(v=v):
                put(settings, f"aspect={v}\n".encode())
                self.assertIn(f"aspect: {want}", self.L("status")[1])

    def test_each_row_alone(self):
        for args, want in [
            (["--sound", "off"], ["--player", "--nosound", "--", "+cl_captions", "1"]),
            (["--intros", "skip"], ["--player", "--novid", "--", "+cl_captions", "1"]),
            (["--display", "2"], ["--player", "--display", "2", "--", "+cl_captions", "1"]),
        ]:
            with self.subTest(args=args):
                self.set("--mode", "player", "--sound", "on", "--intros", "play", "--display", "auto")
                self.set(*args)
                argv, _ = self.argv()
                self.assertEqual(argv[6:], want)
                self.assertEqual(argv[:4], [str(self.exe), "--run", "--game", self.game])

    def test_combined_developer(self):
        self.set("--mode", "developer", "--sound", "off", "--intros", "skip", "--display", "1", "--extra", "+map 'a b'")
        argv, out = self.argv()
        self.assertEqual(argv[6:], ["--nosound", "--novid", "--display", "1", "--", "+cl_captions", "1", "+map", "a b"])
        self.assertIn("command: ", out)

    def test_extra_ignored_in_player(self):
        self.set("--extra", "+map x")
        argv, _ = self.argv()
        self.assertNotIn("+map", argv)

    def test_never_hides_developer_menu(self):
        self.set("--mode", "developer", "--sound", "off", "--intros", "skip")
        for mode in launcher.MODES:
            self.set("--mode", mode)
            argv, _ = self.argv()
            self.assertFalse([a for a in argv if "menu" in a.lower() or "nodev" in a.lower() or a == "--no-dev"])

    def test_flag_missing_in_runtime_is_omitted_with_note(self):
        put(self.exe, b"nothing here")
        argv, out = self.argv()
        self.assertNotIn("--player", argv)
        self.assertIn("note: omitted --player", out)

    def test_bundle_env(self):
        _, out = self.argv()
        c = self.bundle / "Contents"
        self.assertIn("env RT_DXVK_LIB=%s (set)" % (c / "Frameworks/libdxvk_d3d9.0.dylib"), out)
        self.assertIn("env SDL_VULKAN_LIBRARY=%s (set)" % (c / "Frameworks/libvulkan.1.dylib"), out)
        self.assertIn("env VK_DRIVER_FILES=%s (set)" % (c / "Resources/kosmickrisp_mesa_icd.aarch64.json"), out)
        self.assertNotIn("/opt/homebrew", out)

    def test_user_env_kept(self):
        os.environ["RT_DXVK_LIB"] = "/mine/lib.dylib"
        try:
            _, out = self.argv()
        finally:
            del os.environ["RT_DXVK_LIB"]
        self.assertIn("env RT_DXVK_LIB=/mine/lib.dylib (kept)", out)

    def test_game_comes_from_the_bundle_never_the_chosen_folder(self):
        argv, out = self.argv()
        self.assertEqual(argv[argv.index("--game") + 1], str(self.bundle / "Contents/Resources/game"))
        self.assertNotIn(self.source, out)
        self.assertIn("Games (mine)", argv[0])

    def test_no_game_data_in_the_bundle(self):
        shutil.rmtree(self.game_data)
        code, _, err = self.dry()
        self.assertEqual(code, 2)
        self.assertIn("holds no game data", err)


    def test_dry_run_starts_nothing(self):
        self.dry()
        self.assertFalse((self.overlay / "last-run.json").exists())
        self.assertFalse((self.overlay / "backups").exists())


class Remembered(Base):
    def test_choices_come_back(self):
        self.L("set", "--game", "/g", "--mode", "developer", "--sound", "off", "--display", "1", "--extra", "+x 1")
        first = self.dry()
        again = self.dry()
        self.assertEqual(first, again)
        saved = json.loads((self.overlay / "launcher.json").read_text())
        self.assertEqual((saved["mode"], saved["sound"], saved["display"], saved["extra"]), ("developer", "off", 1, "+x 1"))
        self.assertIn("--nosound", first[1])

    def test_broken_json_falls_back(self):
        put(self.overlay / "launcher.json", "{not json")
        code, out, err = self.L("status")
        self.assertEqual(code, 0)
        self.assertIn("state: no-game", out)
        self.assertIn("launcher.json unreadable", (self.overlay / "logs/launcher.log").read_text())

    def test_bad_value_falls_back_field_only(self):
        put(self.overlay / "launcher.json", json.dumps({"game": "/g", "mode": "god", "sound": "off"}))
        s = launcher.load_settings(self.overlay, launcher.Log(self.overlay))
        self.assertEqual((s["game"], s["mode"], s["sound"]), ("/g", "player", "off"))
        self.assertIn("bad value for mode", (self.overlay / "logs/launcher.log").read_text())

    def test_bad_set_values_refused(self):
        self.assertEqual(self.L("set", "--display", "x")[0], 2)
        self.assertEqual(self.L("set", "--extra", "+echo 'open")[0], 2)
        self.assertFalse((self.overlay / "launcher.json").exists())

    def test_unbalanced_extra_in_file_falls_back(self):
        put(self.overlay / "launcher.json", json.dumps({"game": "/g", "mode": "developer", "extra": "a 'b"}))
        s = launcher.load_settings(self.overlay, launcher.Log(self.overlay))
        self.assertEqual(s["extra"], "")
        self.assertEqual(self.dry()[0], 0)

    def test_flag_probe_is_anchored(self):
        put(self.exe, b"x--player\0--nosound\0")
        self.assertFalse(launcher.supports(self.exe, "--player"))
        put(self.exe, b"\0--player\0")
        self.assertTrue(launcher.supports(self.exe, "--player"))

    def test_window_size_is_not_offered(self):
        with self.assertRaises(SystemExit):
            self.L("set", "--size", "800x600")
        out = self.dry()[1]
        self.assertIn("command: ", out)
        self.assertNotIn("-w", [l.split(" ", 3)[3] for l in out.splitlines() if l.startswith("  argv[")])


class Migration(Base):
    def setUp(self):
        super().setUp()
        self.old = self.home / "Library/Application Support" / kitconst.OLD_SUPPORT_NAME
        self.kept = {
            "settings.txt": b"s", "launcher.json": json.dumps({"mode": "developer"}).encode(), "registry.reg": b"r",
            "files/Vampire/SAVE/a.sav": b"save", "files/Vampire/cfg/autoexec.cfg": b"cfg", "cache/dxvk/x.dxvk-cache": b"dx",
        }
        self.left = {"up/12.0.8-basic-gu/vampire.exe": b"up", "up.json": b"{}", "logs/launcher.log": b"old"}
        for rel, data in {**self.kept, **self.left}.items():
            put(self.old / rel, data)

    def call_default(self, *argv):
        return call(*argv, "--home", str(self.home), "--crash-dir", str(self.crash))

    def test_first_run_copies_play_data_and_keeps_the_old_folder(self):
        before = launcher.file_list(self.old)
        code, out, _ = self.call_default("status")
        self.assertEqual(code, 0)
        for rel, data in self.kept.items():
            self.assertEqual((self.overlay / rel).read_bytes(), data, rel)
        self.assertFalse((self.overlay / "up").exists())
        self.assertFalse((self.overlay / "up.json").exists())
        self.assertEqual(launcher.file_list(self.old), before)
        self.assertIn("migrated 6 files", (self.overlay / "logs/launcher.log").read_text())
        self.assertEqual(launcher.load_settings(self.overlay, print)["mode"], "developer")

    def test_existing_new_folder_is_left_alone(self):
        put(self.overlay / "launcher.json", b"{}")
        self.call_default("status")
        self.assertFalse((self.overlay / "registry.reg").exists())

    def test_a_new_folder_holding_only_the_lock_does_not_block_the_migration(self):
        lock = put(self.overlay / launcher.LOCK_FILE, b"")
        ino = lock.stat().st_ino
        self.assertEqual(self.call_default("status")[0], 0)
        for rel, data in self.kept.items():
            self.assertEqual((self.overlay / rel).read_bytes(), data, rel)
        self.assertFalse((self.overlay / "up").exists())
        self.assertEqual((self.overlay / launcher.LOCK_FILE).stat().st_ino, ino)
        self.assertFalse(self.overlay.with_name(self.overlay.name + ".migrating").exists())

    def test_a_new_folder_with_anything_else_in_it_is_left_alone(self):
        put(self.overlay / launcher.LOCK_FILE, b"")
        put(self.overlay / "files/Vampire/cfg/config.cfg", b"x")
        self.assertFalse(launcher.migrate(self.old, self.overlay, lambda m: None))
        self.assertFalse((self.overlay / "registry.reg").exists())

    def test_logs_from_a_failed_try_do_not_block_the_next_one(self):
        put(self.overlay / launcher.LOCK_FILE, b"")
        put(self.overlay / "logs/launcher.log", b"failed once")
        self.assertTrue(launcher.migrate(self.old, self.overlay, lambda m: None))
        self.assertEqual((self.overlay / "logs/launcher.log").read_bytes(), b"failed once")
        for rel, data in self.kept.items():
            self.assertEqual((self.overlay / rel).read_bytes(), data, rel)

    def test_a_failed_migration_stops_and_the_next_start_retries(self):
        real = launcher.migrate

        def failing(old, new, log):
            raise launcher.MigrationError("the copy differs; nothing moved")

        launcher.migrate = failing
        try:
            self.assertEqual(self.call_default("status")[0], launcher.EXIT_MIGRATE)
        finally:
            launcher.migrate = real
        self.assertFalse((self.overlay / "files").exists())
        self.assertEqual(self.call_default("status")[0], 0)
        for rel, data in self.kept.items():
            self.assertEqual((self.overlay / rel).read_bytes(), data, rel)

    def test_a_real_copy_failure_leaves_the_old_folder_identical_and_can_retry(self):
        unreadable = self.old / "files/Vampire/SAVE/a.sav"
        before = launcher.file_list(self.old)
        unreadable.chmod(0)
        try:
            code, _, err = self.call_default("status")
            self.assertEqual(code, launcher.EXIT_MIGRATE)
            self.assertIn("your old folder is unchanged", err)
            self.assertEqual(oct(unreadable.stat().st_mode & 0o777), "0o0")
        finally:
            unreadable.chmod(0o644)
        self.assertEqual(launcher.file_list(self.old), before)
        self.assertFalse(self.overlay.with_name(self.overlay.name + ".migrating").exists())
        self.assertFalse(launcher.MIGRATE_ROOTS & set(os.listdir(self.overlay)))
        self.assertEqual(self.call_default("status")[0], 0)
        self.assertEqual(launcher.file_list(self.old), before)
        for rel, data in self.kept.items():
            self.assertEqual((self.overlay / rel).read_bytes(), data, rel)

    def test_two_starts_at_once_migrate_only_once(self):
        before = launcher.file_list(self.old)
        results = []
        threads = [threading.Thread(target=lambda: results.append(launcher.migrate(self.old, self.overlay, lambda m: None))) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(launcher.file_list(self.old), before)
        for rel, data in self.kept.items():
            self.assertEqual((self.overlay / rel).read_bytes(), data, rel)
        self.assertFalse(self.overlay.with_name(self.overlay.name + ".migrating").exists())

    def test_a_game_check_written_before_a_retry_does_not_block_it(self):
        put(self.overlay / launcher.LOCK_FILE, b"")
        put(self.overlay / "game-check.json", b"{}")
        self.assertEqual(self.call_default("status")[0], 0)
        self.assertEqual((self.overlay / "game-check.json").read_bytes(), b"{}")
        for rel, data in self.kept.items():
            self.assertEqual((self.overlay / rel).read_bytes(), data, rel)

    def test_a_changed_aspect_forgets_the_saved_window_only_when_it_changed(self):
        settings = self.overlay / launcher.SETTINGS_TXT
        put(settings, b"aspect=16:9\nwindow=0,71,2056,1195\nfullscreen=on\n")
        self.L("set", "--aspect", "16:9")
        self.assertIn("window=", settings.read_text())
        self.L("set", "--aspect", "21:9")
        text = settings.read_text()
        self.assertIn("aspect=21:9", text)
        self.assertNotIn("window=", text)
        self.assertIn("fullscreen=on", text)

    def test_a_copy_that_differs_moves_nothing(self):
        real = launcher.file_list
        calls = []

        def lying(root):
            calls.append(root)
            d = real(root)
            return dict(d, extra="x") if len(calls) == 2 else d

        launcher.file_list = lying
        try:
            with self.assertRaises(launcher.MigrationError):
                launcher.migrate(self.old, self.overlay, lambda m: None)
        finally:
            launcher.file_list = real
        self.assertFalse(self.overlay.exists())
        self.assertFalse(self.overlay.with_name(self.overlay.name + ".migrating").exists())

    def test_an_explicit_overlay_never_migrates(self):
        self.L("status")
        self.assertFalse((self.overlay / "registry.reg").exists())


class OneAtATime(Base):
    def test_lock_held_reports_running(self):
        self.L("set", "--game", "/g")
        self.holder(HOLD, str(self.overlay / "launcher.lock"))
        code, out, _ = self.dry()
        self.assertEqual(code, 0)
        self.assertIn("already running: would start nothing", out)
        self.assertNotIn("command:", out)
        self.assertIn("state: running", self.L("status")[1])

    def test_unstartable_exe_records_127(self):
        self.L("set", "--game", "/g")
        put(self.exe, "")
        self.exe.chmod(0o644)
        code, _, err = self.L("run", "--bundle", str(self.bundle))
        self.assertEqual(code, 127)
        self.assertIn("cannot start", err)
        self.assertEqual(json.loads((self.overlay / "last-run.json").read_text())["exit"], 127)
        self.assertIn("cannot start", (self.overlay / "logs/launcher.log").read_text())
        self.assertIn("state: last-run-failed", self.L("status")[1])
        self.assertFalse(launcher.lock_held(self.overlay))

    def test_real_run_with_failing_fake_exe(self):
        script = self.tmp / "fake.app/Contents/MacOS/bloodlines-native"
        put(self.tmp / "fake.app/Contents/Resources/game/vampire.exe", b"exe")
        put(script, "#!/bin/sh\necho fake-out\nexit 3\n")
        script.chmod(0o755)
        self.L("set", "--game", "/g")
        saves = put(self.overlay / "files/Vampire/SAVE/a.sav", b"s")
        patched = put(self.overlay / "files/Unofficial_Patch/save/b.sav", b"p")
        code, _, _ = self.L("run", "--bundle", str(self.tmp / "fake.app"))
        self.assertEqual(code, 3)
        self.assertEqual(json.loads((self.overlay / "last-run.json").read_text())["exit"], 3)
        self.assertIn("fake-out", (self.overlay / "logs/launcher.log").read_text())
        self.assertEqual(len(list((self.overlay / "backups").glob("SAVE-*/Vampire/SAVE/a.sav"))), 1)
        self.assertEqual(len(list((self.overlay / "backups").glob("SAVE-*/Unofficial_Patch/save/b.sav"))), 1)
        self.L("run", "--bundle", str(self.tmp / "fake.app"))
        self.assertEqual(len(list((self.overlay / "backups").glob("SAVE-*"))), 1)
        self.assertTrue(saves.exists() and patched.exists())
        self.assertIn("state: last-run-failed", self.L("status")[1])
        self.assertFalse(launcher.lock_held(self.overlay))


class Status(Base):
    def test_precedence(self):
        self.assertIn("state: no-game", self.L("status")[1])
        self.L("set", "--game", "/g")
        self.assertIn("state: ready", self.L("status")[1])
        put(self.overlay / "game-check.json", json.dumps({"game": "/g", "ok": False, "failures": ["missing: a"]}))
        self.assertIn("state: check-failed - missing: a", self.L("status")[1])
        put(self.overlay / "game-check.json", json.dumps({"game": "/other", "ok": False, "failures": ["x"]}))
        self.assertIn("state: ready", self.L("status")[1])

    def test_crash_offers_collect_logs(self):
        self.L("set", "--game", "/g")
        put(self.overlay / "last-run.json", json.dumps({"start": 1000, "exit": 0}))
        put(self.crash / "bloodlines-native-2026.ips", "{}", mtime=2000)
        out = self.L("status")[1]
        self.assertIn("state: last-run-failed", out)
        self.assertIn("Collect logs offered", out)

    def test_nonzero_exit_without_ips(self):
        self.L("set", "--game", "/g")
        put(self.overlay / "last-run.json", json.dumps({"start": 1000, "exit": 139}))
        self.assertIn("exit 139", self.L("status")[1])


T0 = 1_800_000_000


def stamp(offset):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(T0 + offset))


def iso(offset):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0 + offset))


class CollectLogs(Base):
    def fixture(self):
        h = str(self.home)
        logs = self.overlay / "logs"
        for i, off in enumerate((0, 1000, 2000), 1):
            put(logs / ("run-%s.log" % stamp(off)), "run %d opened %s/Library/x\n" % (i, h))
            put(logs / ("first-calls-%s.tsv" % stamp(off)), "tsv %d\n" % i)
        put(logs / "bloodlines-native_d3d9.log", "d3d9 %s\n" % h, mtime=T0 + 1500)
        put(logs / "launcher.log", "%s old-line\n%s new-line\nchild output\n" % (iso(100), iso(1600)))
        put(self.overlay / "files/Vampire/logs/console.log", "console %s\n" % h, mtime=T0 + 1700)
        put(self.overlay / "files/Vampire/SAVE/slot1.sav", "SECRET-SAVE")
        put(self.overlay / "registry.reg", "REGEDIT4\nSECRET-REG\n")
        put(self.overlay / "cache/dxvk/x.dxvk-cache", "cache")
        put(self.overlay / "launcher.json", json.dumps({"game": h + "/Games"}))
        put(self.crash / "bloodlines-native-in.ips", '{"path":"%s/x"}' % h, mtime=T0 + 1800)
        put(self.crash / "bloodlines-native-old.ips", "old", mtime=T0 + 10)
        put(self.crash / "other-app.ips", "other", mtime=T0 + 1800)
        self.out = self.tmp / "Desktop"

    def collect(self, **kw):
        kw.setdefault("since", T0 + 500)
        return collect_logs.collect(self.overlay, self.home, out_dir=self.out, crash_dir=self.crash, machine=["chip: test"], header=["kit: x"], now=T0 + 3000, **kw)

    def test_session_zip_contents(self):
        self.fixture()
        z = zipfile.ZipFile(self.collect())
        self.assertEqual(
            sorted(z.namelist()),
            sorted([
                "crash/bloodlines-native-in.ips",
                "info.txt",
                "logs/bloodlines-native_d3d9.log",
                "logs/console.log",
                "logs/first-calls-%s.tsv" % stamp(1000),
                "logs/first-calls-%s.tsv" % stamp(2000),
                "logs/launcher.log",
                "logs/run-%s.log" % stamp(1000),
                "logs/run-%s.log" % stamp(2000),
            ]),
        )
        blob = b"".join(z.read(n) for n in z.namelist())
        for secret in (b"SECRET-SAVE", b"SECRET-REG", b"slot1", b"registry", b"dxvk-cache"):
            self.assertNotIn(secret, blob)

    def test_home_path_never_in_zip(self):
        self.fixture()
        z = zipfile.ZipFile(self.collect())
        for n in z.namelist():
            d = z.read(n)
            self.assertNotIn(str(self.home).encode(), d, n)
            self.assertNotIn(b"/Users/someone", d, n)
        self.assertIn(b"~/Library/x", z.read("logs/run-%s.log" % stamp(1000)))
        self.assertIn(b"~", z.read("crash/bloodlines-native-in.ips"))

    def test_launcher_log_whole_and_console_log_limited_to_session(self):
        self.fixture()
        z = zipfile.ZipFile(self.collect())
        launcher_log = z.read("logs/launcher.log").decode()
        self.assertIn("new-line", launcher_log)
        self.assertIn("child output", launcher_log)
        self.assertIn("old-line", launcher_log)
        old = self.overlay / "files/Vampire/logs/console.log"
        os.utime(old, (T0 + 10, T0 + 10))
        self.assertNotIn("logs/console.log", zipfile.ZipFile(self.collect()).namelist())

    def big_latest_run(self):
        self.fixture()
        logs = self.overlay / "logs"
        body = ("x" * 99 + "\n") * (30 * 10486) + "tail %s/Library/end\n" % self.home
        latest = logs / ("run-%s.log" % stamp(2000))
        latest.write_text(body)
        return latest

    def assert_latest_whole(self, z, latest):
        want = collect_logs.redact(latest.read_bytes(), self.home)
        self.assertGreater(len(want), 30 * 1024 * 1024)
        name = "logs/run-%s.log" % stamp(2000)
        self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(), hashlib.sha256(want).hexdigest())
        launcher = collect_logs.redact((self.overlay / "logs/launcher.log").read_bytes(), self.home)
        self.assertEqual(z.read("logs/launcher.log"), launcher)

    def test_latest_run_log_whole_outside_session_window(self):
        latest = self.big_latest_run()
        z = zipfile.ZipFile(self.collect(since=T0 + 5000))
        self.assert_latest_whole(z, latest)
        self.assertIn("runs: %s\n" % stamp(2000), z.read("info.txt").decode())

    def test_latest_run_log_whole_with_last_two_runs(self):
        latest = self.big_latest_run()
        z = zipfile.ZipFile(self.collect(last=2))
        self.assert_latest_whole(z, latest)
        self.assertIn("runs: %s %s\n" % (stamp(1000), stamp(2000)), z.read("info.txt").decode())
        self.assertIn("logs/run-%s.log" % stamp(1000), z.namelist())

    def test_latest_run_log_survives_zip_cap_older_logs_give_way(self):
        latest = self.big_latest_run()
        z = zipfile.ZipFile(self.collect(last=2, zip_cap=1024 * 1024))
        self.assert_latest_whole(z, latest)
        self.assertNotIn("logs/run-%s.log" % stamp(1000), z.namelist())
        self.assertIn("dropped to fit the zip cap", z.read("info.txt").decode())

    def test_no_runs_without_since_skips_everything_else(self):
        self.fixture()
        for p in (self.overlay / "logs").glob("run-*.log"):
            p.unlink()
        for p in (self.overlay / "logs").glob("first-calls-*"):
            p.unlink()
        z = zipfile.ZipFile(self.collect(since=None))
        self.assertEqual(sorted(z.namelist()), ["info.txt", "logs/launcher.log"])
        self.assertIn(b"old-line", z.read("logs/launcher.log"))
        self.assertIn("runs: no runs", z.read("info.txt").decode())

    def test_no_runs_with_since_uses_since(self):
        self.fixture()
        for p in (self.overlay / "logs").glob("run-*.log"):
            p.unlink()
        z = zipfile.ZipFile(self.collect(since=T0 + 1700))
        self.assertEqual(sorted(z.namelist()), ["crash/bloodlines-native-in.ips", "info.txt", "logs/console.log", "logs/launcher.log"])
        self.assertIn("runs: no runs", z.read("info.txt").decode())

    def test_non_ascii_home_redacted_everywhere(self):
        self.home = self.tmp / "Users" / "Олег"
        self.overlay = self.home / "Library/Application Support/BloodlinesNative"
        self.crash = self.home / "Library/Logs/DiagnosticReports"
        self.fixture()
        self.L("set", "--game", str(self.home / "Steam/Bloodlines"))
        code, out, _ = self.L("collect-logs", "--bundle", str(self.bundle), "--out-dir", str(self.out), "--since", str(T0 + 500))
        self.assertEqual(code, 0)
        z = zipfile.ZipFile(out.strip())
        info = z.read("info.txt").decode()
        self.assertIn("~/Steam/Bloodlines", info)
        for n in z.namelist():
            d = z.read(n)
            self.assertNotIn("Олег".encode(), d, n)
            self.assertNotIn(b"\\u041e", d, n)

    def test_info_txt_hashes_match(self):
        self.fixture()
        z = zipfile.ZipFile(self.collect())
        info = z.read("info.txt").decode()
        self.assertIn("kit: x", info)
        self.assertIn("chip: test", info)
        rows = [l.split("  ") for l in info.split("files:\n", 1)[1].splitlines()]
        self.assertEqual(sorted(r[2] for r in rows), sorted(n for n in z.namelist() if n != "info.txt"))
        for sha, size, name in rows:
            self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(), sha)
            self.assertEqual(len(z.read(name)), int(size))

    def test_no_session_takes_latest_run(self):
        self.fixture()
        z = zipfile.ZipFile(self.collect(since=None))
        runs = [n for n in z.namelist() if n.startswith("logs/run-")]
        self.assertEqual(runs, ["logs/run-%s.log" % stamp(2000)])

    def test_last_n(self):
        self.fixture()
        z = zipfile.ZipFile(self.collect(last=3))
        self.assertEqual(len([n for n in z.namelist() if n.startswith("logs/run-")]), 3)

    def test_big_log_cut(self):
        self.fixture()
        big = self.overlay / "logs" / ("run-%s.log" % stamp(1000))
        with open(big, "wb") as f:
            f.write(b"HEAD-MARK\n")
            f.truncate(200 * 1024 * 1024 - 10)
            f.seek(0, 2)
            f.write(b"\nTAIL-MARK\n")
        zp = self.collect()
        self.assertLess(zp.stat().st_size, 100 * 1024 * 1024)
        d = zipfile.ZipFile(zp).read("logs/run-%s.log" % stamp(1000))
        self.assertTrue(d.startswith(b"HEAD-MARK"))
        self.assertTrue(d.endswith(b"TAIL-MARK\n"))
        self.assertIn(b"bytes cut by Collect logs", d)
        self.assertLess(len(d), 20 * 1024 * 1024 + 200)

    def test_works_while_running(self):
        self.fixture()
        self.holder(APPEND, str(self.overlay / "launcher.lock"), str(self.overlay / "logs" / ("run-%s.log" % stamp(2000))))
        code, out, _ = self.L("collect-logs", "--bundle", str(self.bundle), "--out-dir", str(self.out), "--since", str(T0 + 500))
        self.assertEqual(code, 0, out)
        z = zipfile.ZipFile(out.strip())
        self.assertIn("state: running", z.read("info.txt").decode())

    def test_not_running_and_cli_header(self):
        self.fixture()
        put(self.bundle / "Contents/Resources/kit-version.txt", "kit-commit abc\nexecutable-sha256 def\n")
        code, out, _ = self.L("collect-logs", "--bundle", str(self.bundle), "--out-dir", str(self.out), "--since", str(T0 + 500))
        self.assertEqual(code, 0)
        info = zipfile.ZipFile(out.strip()).read("info.txt").decode()
        self.assertIn("state: not running", info)
        self.assertIn("kit: kit-commit abc", info)
        self.assertIn("launcher choices:", info)
        self.assertIn("game check: not run", info)
        self.assertIn("macOS:", info)

    def test_build_record(self):
        self.fixture()
        work = self.tmp / "work"
        put(work / "summary.json", "{}")
        put(work / "build.log", "".join("l%d\n" % i for i in range(5000)))
        z = zipfile.ZipFile(self.collect(work=work))
        log = z.read("build/build.log").decode().splitlines()
        self.assertEqual((len(log), log[0], log[-1]), (2000, "l3000", "l4999"))
        self.assertIn("build/summary.json", z.namelist())

    def test_machine_info_survives_failures(self):
        def boom(cmd):
            raise OSError("no")

        lines = collect_logs.machine_info(boom)
        self.assertTrue(all("unavailable" in l for l in lines if l.startswith(("macOS", "chip"))))

    def test_serial_numbers_dropped(self):
        lines = collect_logs.machine_info(lambda c: "Display\n  Serial Number: ABC\n  Resolution: 1\n" if c[0] == "system_profiler" else "x")
        shown = "\n".join(lines[lines.index("displays:"):])
        self.assertNotIn("ABC", shown)
        self.assertIn("Resolution", shown)


if __name__ == "__main__":
    unittest.main()
