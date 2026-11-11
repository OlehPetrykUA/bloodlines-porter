#!/usr/bin/env python3
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gamecheck
import kit
import kitconst
import launcher
import test_kit
import test_launcher as tl
import up

STEP_KEYS = {"step", "n", "of", "fraction", "elapsed"}
ITEM_KEYS = STEP_KEYS | {"done", "total"}
RESULT_KEYS = {"result", "exit", "message"}


def parse(out):
    return [json.loads(l) for l in out.splitlines()]


def check_stream(tc, objs, code):
    tc.assertTrue(objs)
    last = objs[-1]
    tc.assertEqual(set(last), RESULT_KEYS)
    tc.assertEqual(last["exit"], code)
    tc.assertEqual(last["result"], "pass" if code == 0 else "fail")
    fr = -1.0
    for o in objs[:-1]:
        if "log" in o:
            tc.assertEqual(set(o), {"log"})
            continue
        tc.assertIn(set(o), (STEP_KEYS, ITEM_KEYS))
        tc.assertGreaterEqual(o["fraction"], fr)
        tc.assertLessEqual(o["fraction"], 1)
        fr = o["fraction"]
    if code == 0:
        tc.assertEqual(fr, 1)


class Cli(tl.Base):
    def setUp(self):
        super().setUp()
        self.game = tl.GameCheck.make_game(self)
        self.manifest = str(self.tmp / "manifest.txt")
        self.saved_manifest = gamecheck.MANIFEST
        gamecheck.MANIFEST = self.tmp / "manifest.txt"
        self.addCleanup(setattr, gamecheck, "MANIFEST", self.saved_manifest)

    def settings(self):
        return {**json.loads((self.overlay / "launcher.json").read_text()), "aspect": launcher.read_aspect(self.overlay)}


class Ac10CheckProgress(Cli):
    def test_every_line_is_json_with_the_documented_keys_and_ends_at_one(self):
        code, out, err = self.L("check", "--game", str(self.game), "--manifest", self.manifest, "--progress")
        self.assertEqual(code, 0)
        objs = parse(out)
        check_stream(self, objs, 0)
        self.assertIn("step", objs[0])
        self.assertTrue(any("done" in o for o in objs))
        self.assertTrue(any(o.get("log", "").startswith("game ok: patch 1.2, 3 files") for o in objs))
        self.assertEqual(err, "")

    def test_a_failing_folder_ends_with_fail_and_exit_6(self):
        (self.game / "Bin/engine.dll").write_bytes(b"x")
        code, out, _ = self.L("check", "--game", str(self.game), "--manifest", self.manifest, "--progress")
        self.assertEqual(code, 6)
        objs = parse(out)
        check_stream(self, objs, 6)
        self.assertIn({"log": "  changed: Bin/engine.dll"}, objs)

    def test_no_game_folder_is_exit_2_with_stderr(self):
        code, out, err = self.L("check", "--progress")
        self.assertEqual(code, 2)
        check_stream(self, parse(out), 2)
        self.assertEqual(err.strip(), "no game folder")


def step_chain(tc, calls):
    tc.assertTrue(calls)
    tc.assertEqual(calls[0][3], 0.0)
    tc.assertEqual(calls[-1][4], 1.0)
    for prev, nxt in zip(calls, calls[1:]):
        tc.assertEqual(prev[4], nxt[3], (prev, nxt))
        tc.assertEqual(nxt[1], prev[1] + 1)


class ProgressUnit(unittest.TestCase):
    def prog(self, every=None):
        p = kitconst.Progress(every)
        p.out = io.StringIO()
        return p

    def test_a_regressing_item_count_never_lowers_the_fraction(self):
        p = self.prog()
        p.step("s", 1, 1, 0.2, 0.8)
        p.item(10, 10)
        self.assertEqual(p.line()["fraction"], 0.8)
        p.item(2, 10)
        self.assertEqual(p.line()["fraction"], 0.8)
        p.step("t", 2, 2, 0.1, 0.3)
        self.assertEqual(p.line()["fraction"], 0.8)

    def test_fraction_is_inside_the_step_bounds(self):
        p = self.prog()
        p.step("s", 1, 2, 0.25, 0.75)
        self.assertEqual(p.line()["fraction"], 0.25)
        p.item(1, 2)
        self.assertEqual(p.line()["fraction"], 0.5)
        p.item(2, 2)
        self.assertEqual(p.line()["fraction"], 0.75)

    def test_a_line_is_never_more_than_a_tick_and_a_half_apart(self):
        p = self.prog(0.2)
        stamps = []
        write = p.out.write

        def tap(text):
            if text.strip():
                stamps.append(time.monotonic())
            return write(text)

        p.out.write = tap
        p.start()
        p.step("s", 1, 1, 0.0, 1.0, expected=100)
        time.sleep(1.5)
        p.finish(0, "x")
        self.assertGreater(len(stamps), 5)
        self.assertLess(max(b - a for a, b in zip(stamps, stamps[1:])), 0.2 * 1.5)

    def test_a_tick_emits_when_the_last_line_is_over_half_a_tick_old(self):
        p = self.prog(0.5)
        p.start()
        p.step("s", 1, 1, 0.0, 1.0, expected=100)
        time.sleep(0.2)
        with p.lock:
            p.last = time.monotonic()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with p.lock:
                if len(p.out.getvalue().splitlines()) >= 2:
                    break
            time.sleep(0.02)
        p.stopped.set()
        self.assertEqual(len(p.out.getvalue().splitlines()), 2)

    def test_tick_is_half_a_second_so_two_ticks_stay_under_the_ceiling(self):
        self.assertLessEqual(kitconst.TICK, 0.5)

    def test_run_json_turns_any_exception_into_a_fail_line(self):
        out = io.StringIO()

        def work(p):
            raise ValueError("bad value")

        with redirect_stdout(out):
            code = kitconst.run_json(work)
        self.assertEqual(code, 1)
        objs = parse(out.getvalue())
        self.assertEqual(objs[-1], {"result": "fail", "exit": 1, "message": "bad value"})


class SettingsRead(Cli):
    def test_bare_set_echoes_and_never_writes(self):
        self.L("set", "--game", str(self.game), "--aspect", "16:9")
        p = self.overlay / "launcher.json"
        os.utime(p, (1000, 1000))
        before = p.read_bytes()
        code, out, _ = self.L("set")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), self.settings())
        self.assertEqual(json.loads(out)["aspect"], "16:9")
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(p.stat().st_mtime, 1000)

    def test_bare_set_without_a_file_creates_nothing(self):
        code, out, _ = self.L("set")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["game"], "")
        self.assertFalse((self.overlay / "launcher.json").exists())


class UpStatus(Cli):
    def test_status_follows_the_patched_executable_in_the_app(self):
        self.assertIn("unofficial patch: off\n", self.L("status", "--bundle", str(self.bundle))[1])
        tl.put(self.bundle / "Contents/MacOS" / kitconst.UP_EXE_NAME, b"#!/bin/sh\necho 'build sha256 %s'\n" % (b"ab" * 32)).chmod(0o755)
        self.assertIn("unofficial patch: on\n", self.L("status", "--bundle", str(self.bundle))[1])


class FakeBuild(test_kit.Tmp):
    def setUp(self):
        super().setUp()
        self.kit = test_kit.fake_kit(self.root)
        self.rcs = {}
        self.delay = 0
        self.game = test_kit.OK_GAME
        self.calls = []

        def fake_stream(argv, env, log):
            label = ("recompile-patch" if "--up-ini" in argv else "recompile") if "build_all.py" in argv[3] else "package"
            self.calls.append((label, argv))
            time.sleep(self.delay)
            if label == "package" and not self.rcs.get("package"):
                out = Path(argv[argv.index("--out") + 1]) / kitconst.APP_NAME
                out.mkdir(parents=True)
                (out / "new").write_text("n")
            return self.rcs.get(label, 0)

        def fake_check(g, manifest=None, progress=None):
            if progress:
                progress(0, 3)
                progress(3, 3)
            return self.game

        def fake_add_game(app, src, game, say=print):
            self.added.append((Path(app), Path(src), Path(game)))
            if self.rcs.get("game-data"):
                raise kitconst.Fail("codesign failed")

        def fake_prepare(installer, game, base, say=print):
            gu = up.game_root(base)
            (gu / "Bin/loader").mkdir(parents=True, exist_ok=True)
            (gu / "Bin/loader/loader.ini").write_text("[Mods]\n")
            return gu

        self.added = []
        (self.root / "game").mkdir()
        self.installer = self.root / "game" / up.NAME
        self.installer.write_bytes(b"i")
        for p in (
            mock.patch.object(kit, "stream", fake_stream),
            mock.patch.object(kit, "doctor", lambda *a, **k: []),
            mock.patch.object(kit, "add_game", fake_add_game),
            mock.patch.object(gamecheck, "check_game", fake_check),
            mock.patch.object(up, "check_installer", return_value=up.MD5),
            mock.patch.object(up, "prepare", fake_prepare),
        ):
            p.start()
            self.addCleanup(p.stop)

    def argv(self, *extra):
        return ["build", "--game", str(self.root / "game"), "--work", str(self.work), "--app", str(self.app), "--overlay", str(self.root / "ov"), *extra]

    def run_build(self, *extra):
        return self.run_main(self.argv(*extra), {"PATH": "x"}, self.kit)

    def patched(self, *extra):
        return self.run_build("--unofficial-patch", "on", *extra)


class UpBuild(FakeBuild):
    def test_patch_off_builds_two_stages_and_hands_the_original_files_to_the_app(self):
        rc, out = self.run_build("--progress")
        self.assertEqual(rc, 0)
        self.assertEqual([c[0] for c in self.calls], ["recompile", "package"])
        self.assertNotIn("--up-build", self.calls[1][1])
        self.assertEqual({o["of"] for o in parse(out) if "step" in o}, {6})
        self.assertEqual(self.added, [(self.app.parent / ".staging" / kitconst.APP_NAME, self.root / "game", self.root / "game")])

    def test_unknown_installer_stops_with_the_patch_exit_and_the_found_md5(self):
        up.check_installer.side_effect = up.UpError("unknown installer: MD5 abc found")
        rc, out = self.patched("--up-installer", str(self.installer))
        self.assertEqual(rc, kit.EXIT_PATCH)
        self.assertIn("unknown installer: MD5 abc found", out)
        self.assertEqual(self.calls, [])

    def test_patch_on_without_an_installer_stops_before_building(self):
        self.installer.unlink()
        rc, out = self.patched()
        self.assertEqual(rc, kit.EXIT_PATCH)
        self.assertIn("no VTMBup*.exe", out)
        self.assertEqual(self.calls, [])

    def test_patch_off_recompile_argv_is_exactly_the_old_one(self):
        self.run_build()
        self.assertEqual(self.calls[0][1], [str(self.kit / "python/bin/python3"), "-I", "-B", str(self.kit / "src/tools/recomp/build_all.py"),
                                            "--game", str(self.root / "game"), "--out", str(self.work), "--no-oracle",
                                            "--prebuilt", str(self.kit / "prebuilt"), "--jobs", self.calls[0][1][-1]])

    def test_the_patch_build_gets_the_fallbacks_file_when_the_kit_has_it(self):
        self.patched()
        self.assertNotIn("--fallbacks", self.calls[1][1])
        self.calls.clear()
        fb = self.kit / "src/tools/recomp/xflags_fallbacks_up.txt"
        fb.parent.mkdir(parents=True, exist_ok=True)
        fb.write_text("")
        self.patched()
        self.assertEqual(self.calls[1][1][self.calls[1][1].index("--fallbacks") + 1], str(fb))
        self.assertNotIn("--fallbacks", self.calls[0][1])

    def test_the_work_volume_needs_double_space_only_with_the_patch_build(self):
        seen = []
        with mock.patch.object(kit, "doctor", lambda *a, **k: seen.append(k.get("work_min", kit.MIN_FREE)) or []):
            self.run_build()
            self.patched()
        self.assertEqual(seen, [kit.MIN_FREE, 2 * kit.MIN_FREE])

    def test_the_patch_build_adds_a_stage_and_hands_it_to_the_package_step(self):
        ini = up.game_root(self.work / kit.UP_DATA) / "Bin/loader/loader.ini"
        rc, out = self.patched()
        self.assertEqual(rc, 0)
        self.assertEqual([c[0] for c in self.calls], ["recompile", "recompile-patch", "package"])
        base, patch, pkg = (c[1] for c in self.calls)
        self.assertEqual(base[base.index("--out") + 1], str(self.work))
        self.assertEqual(patch[patch.index("--out") + 1], str(self.work / "up"))
        self.assertEqual(patch[patch.index("--up-ini") + 1], str(ini))
        self.assertEqual(patch[patch.index("--patch-set") + 1], str(self.kit / "src/tools/up/patches.toml"))
        for flag in ("--game", "--prebuilt", "--jobs"):
            self.assertEqual(patch[patch.index(flag) + 1], base[base.index(flag) + 1])
        self.assertIn("--no-oracle", patch)
        self.assertEqual(pkg[pkg.index("--up-build") + 1], str(self.work / "up"))
        self.assertRegex(out, r"recompile-patch: ok \(\d+ s\)")
        self.assertEqual(self.added[0][1], up.game_root(self.work / kit.UP_DATA))

    def test_progress_has_eight_steps_with_the_patch_build(self):
        rc, out = self.patched("--progress")
        self.assertEqual(rc, 0)
        steps = [(o["step"], o["n"]) for o in parse(out) if "step" in o]
        names = []
        for s in steps:
            if s not in names:
                names.append(s)
        self.assertEqual(names, [("doctor", 1), ("check", 2), ("unofficial-patch", 3), ("recompile", 4), ("recompile-patch", 5), ("package", 6), ("game-data", 7), ("install", 8)])
        self.assertEqual({o["of"] for o in parse(out) if "step" in o}, {8})

    def test_a_failed_patch_build_keeps_its_exit_code_and_the_installed_app(self):
        self.app.mkdir(parents=True)
        (self.app / "old").write_text("o")
        for rc in (2, 4):
            with self.subTest(rc=rc):
                self.calls.clear()
                self.rcs = {"recompile-patch": rc}
                self.assertEqual(self.patched()[0], rc)
                self.assertEqual([c[0] for c in self.calls], ["recompile", "recompile-patch"])
                self.assertEqual((self.app / "old").read_text(), "o")


class Ac10BuildProgress(FakeBuild):
    def test_scripted_steps_stream_to_one(self):
        rc, out = self.run_build("--progress")
        self.assertEqual(rc, 0)
        objs = parse(out)
        check_stream(self, objs, 0)
        self.assertEqual([o["step"] for o in objs if "step" in o][0], "doctor")
        self.assertEqual({o["step"] for o in objs if "step" in o}, {"doctor", "check", "recompile", "package", "game-data", "install"})
        self.assertEqual({o["of"] for o in objs if "step" in o}, {6})
        self.assertIn({"log": "installed %s" % self.app}, objs)
        self.assertEqual((self.app / "new").read_text(), "n")

    def test_failures_end_fail_with_the_build_exit_code(self):
        for rc in (1, 2, 3, 4):
            with self.subTest(rc=rc):
                self.rcs = {"recompile": rc}
                code, out = self.run_build("--progress")
                self.assertEqual(code, rc)
                check_stream(self, parse(out), rc)
        self.rcs = {"package": 1}
        code, out = self.run_build("--progress")
        self.assertEqual(code, 1)
        check_stream(self, parse(out), 1)

    def test_doctor_failure_is_exit_5_and_game_check_failure_exit_6(self):
        with mock.patch.object(kit, "doctor", lambda *a, **k: ["no tools"]):
            code, out = self.run_build("--progress")
        self.assertEqual(code, 5)
        objs = parse(out)
        check_stream(self, objs, 5)
        self.assertEqual(objs[-1]["message"], "doctor: no tools")
        self.game = test_kit.BAD_GAME
        code, out = self.run_build("--progress")
        self.assertEqual(code, 6)
        check_stream(self, parse(out), 6)

    def test_a_line_arrives_every_tick_while_a_step_runs(self):
        self.delay = 0.6
        stamps = []

        class Tap(io.StringIO):
            def write(s, text):
                if text.strip():
                    stamps.append(time.monotonic())
                return super().write(text)

        tap = Tap()
        with mock.patch.object(kitconst, "TICK", 0.1), redirect_stdout(tap):
            rc = kit.main(self.argv("--progress"), {"PATH": "x"}, self.kit)
        self.assertEqual(rc, 0)
        self.assertGreater(len(stamps), 8)
        self.assertLess(max(b - a for a, b in zip(stamps, stamps[1:])), 0.3)
        self.assertLessEqual(kitconst.TICK * 2, 2)
        check_stream(self, parse(tap.getvalue()), 0)


class Ac1ByteIdentical(FakeBuild):
    def test_build_output_without_progress(self):
        rc, out = self.run_build()
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "game ok: patch 1.2, 3 files (0.1 s)")
        self.assertRegex(lines[1], r"^recompile: ok \(\d+ s\)$")
        self.assertRegex(lines[2], r"^package: ok \(\d+ s\)$")
        self.assertEqual(lines[3], "game data: %s/Contents/Resources/game" % kitconst.APP_NAME)
        self.assertEqual(lines[4], "installed %s" % self.app)
        self.assertEqual(lines[5], "removed the build work folder %s" % self.work)
        self.assertEqual(len(lines), 6)

    def test_build_failure_output_without_progress(self):
        self.rcs = {"recompile": 4}
        rc, out = self.run_build()
        self.assertEqual(rc, 4)
        self.assertRegex(out.splitlines()[-1], r"^recompile: failed, exit 4 \(\d+ s\)$")


class Ac1Launcher(Cli):
    def test_check_and_set_and_status_output_without_progress(self):
        code, out, _ = self.L("check", "--game", str(self.game), "--manifest", self.manifest)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^game ok: patch 1\.2, 3 files \(\d\.\d s\)\n$")
        (self.game / "vampire.exe").unlink()
        code, out, _ = self.L("check", "--game", str(self.game), "--manifest", self.manifest)
        self.assertEqual((code, out), (6, "game check failed: 1 of 3 files\n  missing: vampire.exe\n"))
        code, out, _ = self.L("set", "--mode", "developer", "--sound", "off")
        self.assertEqual(code, 0)
        self.assertEqual(out, '{"aspect": null, "display": null, "extra": "", "game": "", "intros": "play", "mode": "developer", "sound": "off", "subtitles": "on"}\n')
        self.assertEqual(self.L("status")[1], "state: no-game - pick the game folder\nsubtitles: on\naspect: default\nunofficial patch: off\n")

    def test_set_has_no_progress_flag_any_more(self):
        with self.assertRaises(SystemExit):
            self.L("set", "--sound", "off", "--progress")


class ControlTable(Cli):
    def test_session_start_writes_the_start_and_prints_nothing(self):
        code, out, err = self.L("session-start")
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertGreater(json.loads((self.overlay / "session.json").read_text())["start"], 0)

    def test_status_states(self):
        self.assertIn("state: no-game", self.L("status")[1])
        self.L("set", "--game", str(self.game))
        self.assertIn("state: ready\n", self.L("status")[1])
        tl.put(self.overlay / "game-check.json", json.dumps({"game": str(self.game), "ok": False, "failures": ["missing: a", "changed: b"]}))
        self.assertIn("state: check-failed - missing: a; changed: b\n", self.L("status")[1])
        (self.overlay / "game-check.json").unlink()
        tl.put(self.overlay / "last-run.json", json.dumps({"start": 1, "exit": 3}))
        self.assertIn("state: last-run-failed - exit 3; Collect logs offered\n", self.L("status")[1])
        (self.overlay / "last-run.json").unlink()
        self.holder(tl.HOLD, str(self.overlay / "launcher.lock"))
        self.assertIn("state: running - ", self.L("status")[1])

    def test_personal_build_line(self):
        tl.put(self.bundle / "Contents/Info.plist", plistlib.dumps({"BNPersonalBuild": True}))
        self.assertIn("personal build (GPLv3 FFmpeg)\n", self.L("status", "--bundle", str(self.bundle))[1])
        tl.put(self.bundle / "Contents/Info.plist", plistlib.dumps({}))
        self.assertNotIn("personal build", self.L("status", "--bundle", str(self.bundle))[1])

    def test_set_echoes_json_for_each_option(self):
        for argv, key, val in (
            (("--mode", "developer"), "mode", "developer"),
            (("--sound", "off"), "sound", "off"),
            (("--intros", "skip"), "intros", "skip"),
            (("--display", "1"), "display", 1),
            (("--display", "auto"), "display", None),
            (("--extra", "-foo bar"), "extra", "-foo bar"),
            (("--aspect", "21:9"), "aspect", "21:9"),
            (("--subtitles", "off"), "subtitles", "off"),
        ):
            code, out, _ = self.L("set", *argv)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)[key], val)
            self.assertEqual(json.loads(out), self.settings())

    def test_invalid_values_exit_2_and_keep_the_file(self):
        self.L("set", "--sound", "off")
        before = (self.overlay / "launcher.json").read_text()
        for argv in (("--extra", "'unclosed"), ("--display", "x")):
            code, _, err = self.L("set", *argv)
            self.assertEqual(code, 2)
            self.assertIn("invalid value for", err)
        self.assertEqual((self.overlay / "launcher.json").read_text(), before)

    def test_aspect_default_clears_the_choice(self):
        self.L("set", "--game", str(self.game), "--aspect", "16:9")
        self.assertIn("aspect: 16:9", self.L("status")[1])
        self.assertNotIn("--aspect", self.dry()[1])
        self.assertIn("aspect=16:9\n", (self.overlay / "settings.txt").read_text())
        code, out, _ = self.L("set", "--aspect", "default")
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["aspect"])
        self.assertIsNone(self.settings()["aspect"])
        self.assertNotIn("aspect", (self.overlay / "settings.txt").read_text())
        self.assertIn("aspect: default", self.L("status")[1])
        self.assertNotIn("aspect", (self.overlay / "launcher.json").read_text())

    def test_aspect_lives_only_in_settings_txt_and_keeps_the_other_lines(self):
        self.overlay.mkdir(parents=True, exist_ok=True)
        (self.overlay / "settings.txt").write_text("overlays=off\naspect=4:3\nwindow=10,10,800,600\n")
        self.assertEqual(self.L("set", "--aspect", "fit")[0], 0)
        self.assertEqual((self.overlay / "settings.txt").read_text(), "overlays=off\naspect=fit\n")
        self.assertNotIn("aspect", (self.overlay / "launcher.json").read_text())

    def test_the_runtimes_4_3_reads_as_default_and_a_stored_size_is_valid(self):
        self.overlay.mkdir(parents=True, exist_ok=True)
        (self.overlay / "settings.txt").write_text("aspect=4:3\n")
        self.assertIn("aspect: default", self.L("status")[1])
        (self.overlay / "settings.txt").write_text("aspect=1600x900\n")
        self.assertIn("aspect: 1600x900", self.L("status")[1])
        code, out, err = self.L("set", "--sound", "off")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["aspect"], "1600x900")

    def test_a_legacy_aspect_in_launcher_json_moves_once(self):
        self.overlay.mkdir(parents=True, exist_ok=True)
        (self.overlay / "launcher.json").write_text('{"aspect": "21:9", "sound": "off"}\n')
        self.assertIn("aspect: 21:9", self.L("status")[1])
        self.assertEqual((self.overlay / "settings.txt").read_text(), "aspect=21:9\n")
        self.assertNotIn("aspect", (self.overlay / "launcher.json").read_text())
        self.assertEqual(self.settings()["sound"], "off")

    def test_run_exit_codes(self):
        shutil.rmtree(self.game_data)
        code, _, err = self.L("run", "--bundle", str(self.bundle))
        self.assertEqual(code, 2)
        self.assertIn("holds no game data", err)
        tl.put(self.game_data / "vampire.exe", b"exe")
        self.exe.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.assertEqual(self.L("run", "--bundle", str(self.bundle))[0], 0)
        self.exe.write_bytes(b"#!/bin/sh\nexit 3\n")
        self.assertEqual(self.L("run", "--bundle", str(self.bundle))[0], 3)
        self.exe.chmod(0o644)
        code, _, err = self.L("run", "--bundle", str(self.bundle))
        self.assertEqual(code, 127)
        self.assertIn("cannot start the game", err)

    def test_collect_logs_with_and_without_work(self):
        tl.put(self.overlay / "logs/run-20260101T000000Z.log", "log")
        work = self.tmp / "work"
        tl.put(work / "summary.json", "{}")
        tl.put(work / "build.log", "b\n")
        names = {}
        for extra in ((), ("--work", str(work))):
            code, out, _ = self.L("collect-logs", "--out-dir", str(self.tmp / "out"), *extra)
            self.assertEqual(code, 0)
            z = out.strip().splitlines()[-1]
            self.assertTrue(z.endswith(".zip"))
            names[extra] = zipfile.ZipFile(z).namelist()
            os.unlink(z)
        self.assertFalse([n for n in names[()] if "build.log" in n])
        w = names[("--work", str(work))]
        self.assertTrue([n for n in w if n.endswith("build.log")])
        self.assertTrue([n for n in w if n.endswith("summary.json")])


class Uninstall(Cli):
    def setUp(self):
        super().setUp()
        self.work = self.tmp / "work"
        self.kitapp = self.tmp / kitconst.KIT_APP_NAME
        tl.put(self.kitapp / "Contents/x", "kit")
        tl.put(self.work / "build.log", "b")
        tl.put(self.overlay / "cache/dxvk/x.dxvk-cache", "cache")
        tl.put(self.overlay / "files/Vampire/SAVE/a.sav", "SAVE")
        tl.put(self.overlay / "backups/SAVE-20260101T000000Z/a.sav", "SAVE")
        tl.put(self.overlay / "up/12.0.8-basic/m.tsv", "up")
        tl.put(self.overlay / "logs/run-20260101T000000Z.log", "log")
        self.L("set", "--game", str(self.game))
        self.L("check", "--game", str(self.game), "--manifest", self.manifest)
        self.desktop_zip = tl.put(self.home / "Desktop/BloodlinesNative-logs-x.zip", "z")
        self.game_before = self.tree(self.game)

    def tree(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}

    def U(self, *argv):
        return self.L("uninstall", "--bundle", str(self.bundle), "--work", str(self.work), *argv)

    def default_paths(self):
        return [str(self.bundle), str(self.work), str(self.overlay / "cache/dxvk")]

    def test_dry_run_prints_exactly_the_default_paths_and_removes_nothing(self):
        before = self.tree(self.tmp)
        code, out, _ = self.U("--dry-run")
        self.assertEqual((code, out.splitlines()), (0, self.default_paths()))
        self.assertEqual(before, self.tree(self.tmp))

    def test_no_flag_is_a_dry_run(self):
        before = self.tree(self.tmp)
        self.assertEqual(self.U()[1].splitlines(), self.default_paths())
        self.assertEqual(before, self.tree(self.tmp))

    def test_yes_removes_exactly_the_dry_run_list_and_keeps_the_data(self):
        listed = self.U("--dry-run")[1].splitlines()
        code, out, _ = self.U("--yes")
        self.assertEqual((code, out.splitlines()), (0, listed))
        for p in listed:
            self.assertFalse(Path(p).exists(), p)
        for kept in ("files/Vampire/SAVE/a.sav", "backups/SAVE-20260101T000000Z/a.sav", "up/12.0.8-basic/m.tsv", "launcher.json", "game-check.json", "logs/run-20260101T000000Z.log"):
            self.assertTrue((self.overlay / kept).is_file(), kept)
        self.assertEqual((self.overlay / "files/Vampire/SAVE/a.sav").read_bytes(), b"SAVE")
        self.assertTrue((self.kitapp / "Contents/x").is_file())
        self.assertTrue(self.desktop_zip.is_file())
        self.assertEqual(self.game_before, self.tree(self.game))
        self.assertTrue(gamecheck.check_game(self.game, self.manifest)["ok"])

    def test_remove_data_lists_the_overlay_too_and_removes_exactly_that(self):
        listed = self.U("--dry-run", "--remove-data")[1].splitlines()
        self.assertEqual(listed, self.default_paths() + [str(self.overlay)])
        self.assertTrue(self.overlay.exists())
        code, out, _ = self.U("--yes", "--remove-data")
        self.assertEqual((code, out.splitlines()), (0, listed))
        self.assertFalse(self.overlay.exists())
        self.assertFalse(self.work.exists())
        self.assertTrue((self.kitapp / "Contents/x").is_file())
        self.assertTrue(self.desktop_zip.is_file())
        self.assertEqual(self.game_before, self.tree(self.game))
        self.assertTrue(gamecheck.check_game(self.game, self.manifest)["ok"])

    def test_refused_while_the_game_runs(self):
        self.holder(tl.HOLD, str(self.overlay / "launcher.lock"))
        before = self.tree(self.tmp)
        for extra in ((), ("--remove-data",)):
            code, out, err = self.U("--yes", *extra)
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("the game is running", err)
        self.assertEqual(before, self.tree(self.tmp))
        self.assertTrue(self.bundle.exists() and self.work.exists())
        self.assertEqual(self.U("--dry-run")[0], 0)

    def test_never_removes_the_game_folder_or_the_kit_app(self):
        before = self.tree(self.tmp)
        for work in (self.game, self.kitapp, self.tmp):
            code, out, err = self.L("uninstall", "--bundle", str(self.bundle), "--work", str(work), "--yes")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("refusing to remove", err)
        code, _, err = self.L("uninstall", "--bundle", str(self.kitapp), "--work", str(self.work), "--yes")
        self.assertEqual(code, 2)
        self.assertEqual(before, self.tree(self.tmp))

    def test_refuses_a_folder_holding_the_kit_app_the_home_or_the_overlay(self):
        holder = self.tmp / "holder"
        shutil.copytree(self.kitapp, holder / kitconst.KIT_APP_NAME)
        before = self.tree(self.tmp)
        for work, extra in ((holder, ()), (self.home, ()), (self.home.parent, ()), (self.overlay, ()), (self.overlay.parent, ()), (self.home, ("--remove-data",))):
            with self.subTest(work=str(work), extra=extra):
                code, out, err = self.L("uninstall", "--bundle", str(self.bundle), "--work", str(work), "--yes", *extra)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("refusing to remove", err)
        self.assertEqual(before, self.tree(self.tmp))

    def test_refuses_what_holds_the_overlay_even_with_remove_data(self):
        before = self.tree(self.tmp)
        for work in (self.overlay.parent, self.home / "Library"):
            for extra in (("--yes",), ("--dry-run",), ("--yes", "--remove-data"), ("--dry-run", "--remove-data")):
                with self.subTest(work=str(work), extra=extra):
                    code, out, err = self.L("uninstall", "--bundle", str(self.bundle), "--work", str(work), *extra)
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn("refusing to remove", err)
        self.assertEqual(before, self.tree(self.tmp))
        self.assertTrue(self.overlay.exists())

    def test_refuses_a_kit_app_at_any_depth(self):
        deep = self.tmp / "dl"
        tl.put(deep / "a/b" / kitconst.KIT_APP_NAME / "Contents/x", "kit")
        before = self.tree(self.tmp)
        code, out, err = self.L("uninstall", "--bundle", str(self.bundle), "--work", str(deep), "--yes")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("refusing to remove", err)
        self.assertEqual(before, self.tree(self.tmp))

    def test_refuses_what_holds_the_running_launcher(self):
        here = Path(launcher.__file__).resolve()
        for work in (here.parent, here.parent.parent):
            code, out, err = self.L("uninstall", "--bundle", str(self.bundle), "--work", str(work), "--dry-run")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("this launcher runs from inside it", err)
        self.assertTrue(here.is_file())

    def test_uninstall_through_the_game_apps_own_launcher_names_the_reason(self):
        inside = self.bundle / "Contents/Resources/kit/launcher.py"
        tl.put(inside, "x")
        before = self.tree(self.tmp)
        with mock.patch.object(launcher, "__file__", str(inside)):
            code, out, err = self.U("--yes")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("this launcher runs from inside it; uninstall from %s instead" % kitconst.KIT_TITLE, err)
        self.assertNotIn("game folder", err)
        self.assertEqual(before, self.tree(self.tmp))

    def test_dry_run_refuses_too(self):
        code, out, _ = self.L("uninstall", "--bundle", str(self.bundle), "--work", str(self.home), "--dry-run")
        self.assertEqual((code, out), (2, ""))

    def test_an_os_error_while_removing_is_exit_2_with_a_message(self):
        with mock.patch.object(launcher.shutil, "rmtree", side_effect=PermissionError(13, "Permission denied", str(self.work))):
            code, out, err = self.U("--yes")
        self.assertEqual(code, 2)
        self.assertIn("cannot remove", err)
        self.assertIn("Permission denied", err)
        self.assertNotIn("Traceback", err)

    def test_nothing_to_remove_prints_nothing(self):
        shutil.rmtree(self.bundle)
        shutil.rmtree(self.work)
        shutil.rmtree(self.overlay / "cache")
        self.assertEqual(self.U("--yes")[0:2], (0, ""))


class Update(tl.Base):
    def setUp(self):
        super().setUp()
        self.game = self.tmp / "game"
        self.game.mkdir()
        self.app = self.tmp / "Apps" / kitconst.APP_NAME
        self.build_app(self.app, "A")
        tl.put(self.overlay / "files/Vampire/SAVE/a.sav", "SAVE")
        tl.put(self.overlay / "cache/dxvk/x.dxvk-cache", "cache")
        tl.put(self.overlay / "logs/run-20260101T000000Z.log", "old log")
        self.L("set", "--game", str(self.game), "--mode", "developer", "--sound", "off")

    def build_app(self, app, tag):
        exe = app / "Contents/MacOS/bloodlines-native"
        tl.put(exe, "#!/bin/sh\n# %s\nexit 0\n" % tag)
        exe.chmod(0o755)
        tl.put(app / kitconst.GAME_DIR / "vampire.exe", b"exe")

    def overlay_files(self):
        return {str(p.relative_to(self.overlay)): p.read_bytes() for p in self.overlay.rglob("*") if p.is_file() and "logs" not in p.relative_to(self.overlay).parts}

    def app_files(self):
        return {str(p.relative_to(self.app)): p.read_bytes() for p in self.app.rglob("*") if p.is_file()}

    def backups(self):
        return sorted((self.overlay / "backups").glob("SAVE-*"))

    def test_ac2_install_over_a_keeps_the_overlay_and_backs_up_once(self):
        before = self.overlay_files()
        staged = self.tmp / "stage" / kitconst.APP_NAME
        self.build_app(staged, "B")
        kit.install(staged, self.app, self.overlay)
        self.assertIn(b"# B", (self.app / "Contents/MacOS/bloodlines-native").read_bytes())
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.L("run", "--bundle", str(self.app))[0], 0)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual((self.backups()[0] / "Vampire/SAVE/a.sav").read_bytes(), b"SAVE")
        self.assertEqual(self.L("run", "--bundle", str(self.app))[0], 0)
        self.assertEqual(len(self.backups()), 1)
        after = self.overlay_files()
        for k, v in before.items():
            self.assertEqual(after[k], v, k)

    def test_ac3_a_failed_update_leaves_the_old_app(self):
        old = self.app_files()
        for rcs in ({"recompile": 4}, {"package": 1}):
            with self.subTest(rcs=rcs):
                self.assertNotEqual(self.update_with(rcs), 0)
                self.assertEqual(old, self.app_files())
                self.assertFalse(self.app.with_name(self.app.name + ".replaced").exists())

    def test_ac3_a_package_step_killed_midway_leaves_the_old_app(self):
        old = self.app_files()

        def killed(argv, env, log):
            if "build_all.py" not in argv[3]:
                (Path(argv[argv.index("--out") + 1]) / kitconst.APP_NAME).mkdir(parents=True)
                raise KeyboardInterrupt
            return 0

        with self.assertRaises(KeyboardInterrupt):
            self.update_with({}, killed)
        self.assertEqual(old, self.app_files())

    def update_with(self, rcs, stream=None):
        def fake_stream(argv, env, log):
            label = "recompile" if "build_all.py" in argv[3] else "package"
            if label == "package" and not rcs.get("package"):
                self.build_app(Path(argv[argv.index("--out") + 1]) / kitconst.APP_NAME, "B")
            return rcs.get(label, 0)

        a = Namespace(game=str(self.game), work=str(self.tmp / "work"), app=str(self.app), overlay=str(self.overlay))
        with mock.patch.object(kit, "doctor", lambda *a, **k: []), mock.patch.object(gamecheck, "check_game", lambda g: test_kit.OK_GAME), \
                mock.patch.object(kit, "add_game", lambda *a, **k: None), \
                mock.patch.object(kit, "stream", stream or fake_stream), redirect_stdout(io.StringIO()):
            return kit.build(a, {"PATH": "x"}, test_kit.fake_kit(self.tmp))


class Wrapper(unittest.TestCase):
    def test_wrapper_dispatches_uninstall(self):
        self.assertIn("uninstall", kitconst.WRAPPER.split("case")[1].split(")")[0])


class IsolatedScripts(unittest.TestCase):
    def test_scripts_import_siblings_under_python_isolated(self):
        here = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as d:
            kd = Path(d) / "tools" / "kit"
            kd.mkdir(parents=True)
            for f in here.glob("*.py"):
                if not f.name.startswith("test_"):
                    shutil.copy2(f, kd / f.name)
            for name in kitconst.KIT_REPO_TOOLS + ("devpaths.py",):
                shutil.copy2(here.parent / name, kd.parent / name)
            shutil.copy2(here / "python.lock", kd / "python.lock")
            shutil.copy2(here / "requirements-build.txt", kd / "requirements-build.txt")
            for name in ("kit_app.py", "kit.py", "make_kit.py", "launcher.py"):
                p = subprocess.run([sys.executable, "-I", "-B", str(kd / name), "--help"], cwd=d, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, name + ": " + p.stderr[-400:])
                self.assertIn("usage", p.stdout.lower(), name)


if __name__ == "__main__":
    unittest.main()
