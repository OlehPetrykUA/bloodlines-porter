#!/usr/bin/env python3
import fcntl
import io
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import appicon
import gamecheck
import kit
import kit_app
import kitconst
import launcher
import make_kit

APP = kitconst.APP_NAME
OK_GAME = {"ok": True, "files": 3, "failures": [], "seconds": 0.1, "up_patched": []}
BAD_GAME = {"ok": False, "files": 3, "failures": ["missing: vampire.exe"], "seconds": 0.1, "up_patched": []}
FREE = lambda p: 100 * 1024**3
MAC = ("arm64", "26.0")


def script(path, body, mode=0o755):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(mode)
    return path


def clt_env(root, select_rc=0, xcrun_rc=0):
    d = Path(root) / "clt-bin"
    script(d / "xcode-select", 'echo /Library/Developer/CommandLineTools\nexit %d\n' % select_rc)
    script(d / "xcrun", "echo /x/$2\nexit %d\n" % xcrun_rc)
    return {"PATH": str(d)}


def fake_kit(root, meson=None, ninja=None, imports_rc=0, flags="--game --no-oracle --prebuilt"):
    want = kit.pins()
    k = Path(root) / "kit"
    script(k / "python/bin/python3", 'echo "$*" >> "$0.argv"\ncase "$*" in\n*"import tomllib"*) exit %d;;\n*mesonbuild*) echo %s;;\n*build_all.py*) echo "%s";;\nesac\n' % (imports_rc, meson or want["meson"], flags))
    script(k / "python/bin/ninja", "echo %s\n" % (ninja or want["ninja"]))
    (k / "src/tools/recomp").mkdir(parents=True, exist_ok=True)
    (k / "src/tools/recomp/build_all.py").write_text("")
    return k


class Tmp(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.work = self.root / "work"
        self.app = self.root / "Applications" / "Bloodlines Native.app"
        self.overlay = self.root / "overlay"
        p = mock.patch.object(kit, "DEFAULT_OVERLAY", self.overlay)
        p.start()
        self.addCleanup(p.stop)

    def doctor(self, k, env, **kw):
        kw.setdefault("mac", MAC)
        kw.setdefault("free", FREE)
        return kit.doctor(k, self.work, self.app, env, **kw)

    def run_main(self, argv, env, k=None):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = kit.main(argv, env, k)
        return rc, out.getvalue()


class Ac1CommandLineTools(Tmp):
    def build_argv(self):
        return ["build", "--game", str(self.root / "game"), "--work", str(self.work), "--app", str(self.app)]

    def test_missing_tools_print_the_install_command_and_exit_5_before_the_game_check(self):
        k = fake_kit(self.root)
        with mock.patch.object(gamecheck, "check_game") as g:
            rc, out = self.run_main(self.build_argv(), clt_env(self.root, select_rc=2), k)
        self.assertEqual(rc, 5)
        self.assertIn("xcode-select --install", out)
        g.assert_not_called()

    def test_doctor_command_exits_5_without_tools(self):
        rc, out = self.run_main(["doctor", "--work", str(self.work), "--app", str(self.app)], clt_env(self.root, select_rc=2), fake_kit(self.root))
        self.assertEqual(rc, 5)
        self.assertIn("xcode-select --install", out)

    def test_a_missing_clt_tool_is_named(self):
        errs = self.doctor(fake_kit(self.root), clt_env(self.root, xcrun_rc=1))
        for t in kit.CLT_TOOLS:
            self.assertIn("%s is not in the Command Line Tools" % t, errs)

    def test_doctor_passes_with_tools_and_a_good_kit(self):
        self.assertEqual(self.doctor(fake_kit(self.root), clt_env(self.root)), [])

    @unittest.skipUnless(sys.platform == "darwin", "needs macOS")
    def test_doctor_tool_checks_pass_on_this_mac(self):
        errs = kit.doctor(fake_kit(self.root), self.work, self.app, {"PATH": kit.SYSTEM_PATH})
        self.assertEqual(errs, [])

    def test_doctor_runs_before_the_game_check(self):
        order = []
        with mock.patch.object(kit, "doctor", lambda *a, **k: order.append("doctor") or []), \
                mock.patch.object(gamecheck, "check_game", lambda g: order.append("game") or BAD_GAME):
            self.run_main(self.build_argv(), clt_env(self.root), fake_kit(self.root))
        self.assertEqual(order, ["doctor", "game"])


class DoctorReasons(Tmp):
    def test_intel_mac(self):
        errs = self.doctor(fake_kit(self.root), clt_env(self.root), mac=("x86_64", "26.0"))
        self.assertTrue(any("arm64" in e for e in errs))

    def test_old_macos(self):
        errs = self.doctor(fake_kit(self.root), clt_env(self.root), mac=("arm64", "15.4"))
        self.assertTrue(any("macOS 15.4 is too old" in e for e in errs))

    def test_too_little_disk_on_either_volume(self):
        errs = self.doctor(fake_kit(self.root), clt_env(self.root), free=lambda p: 1024**3)
        self.assertEqual(len([e for e in errs if "3 GB free" in e]), 2)

    def test_python_without_capstone(self):
        errs = self.doctor(fake_kit(self.root, imports_rc=1), clt_env(self.root))
        self.assertTrue(any("tomllib and capstone" in e for e in errs))

    def test_wrong_meson_version(self):
        errs = self.doctor(fake_kit(self.root, meson="0.1"), clt_env(self.root))
        self.assertTrue(any(e.startswith("meson reports") for e in errs))

    def test_the_pinned_ninja_wheels_long_version_string_is_accepted(self):
        want = kit.pins()["ninja"]
        errs = self.doctor(fake_kit(self.root, ninja="%s.git.kitware.jobserver-pipe-1" % want), clt_env(self.root))
        self.assertEqual(errs, [])

    def test_a_suffix_on_a_different_version_is_still_refused(self):
        for bad in ("1.13.20.git.x", "1.13.git.x", "1.13.3.git.x", "x1.13.2", ""):
            with self.subTest(bad=bad):
                errs = self.doctor(fake_kit(self.root / "n", ninja=bad or "''"), clt_env(self.root))
                self.assertTrue(any(e.startswith("ninja reports") for e in errs), errs)

    def test_meson_with_a_suffix_on_the_pin_is_accepted(self):
        want = kit.pins()["meson"]
        self.assertEqual(self.doctor(fake_kit(self.root, meson="%s.dev1" % want), clt_env(self.root)), [])

    def test_wrong_ninja_version(self):
        errs = self.doctor(fake_kit(self.root, ninja="0.1"), clt_env(self.root))
        self.assertTrue(any(e.startswith("ninja reports") for e in errs))

    def test_another_build_holds_the_lock(self):
        self.work.mkdir()
        with open(self.work / kit.LOCK_NAME, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            errs = self.doctor(fake_kit(self.root), clt_env(self.root))
        self.assertTrue(any("another build holds" in e for e in errs))

    def test_doctor_runs_the_kit_python_without_writing_bytecode(self):
        k = fake_kit(self.root)
        self.assertEqual(self.doctor(k, clt_env(self.root)), [])
        runs = (k / "python/bin/python3.argv").read_text().splitlines()
        self.assertEqual(len(runs), 3)
        for r in runs:
            self.assertTrue(r.startswith("-I -B "), r)

    def test_doctor_does_not_create_the_work_folder(self):
        self.assertEqual(self.doctor(fake_kit(self.root), clt_env(self.root)), [])
        self.assertFalse(self.work.exists())

    def test_released_lock_is_free_again(self):
        self.doctor(fake_kit(self.root), clt_env(self.root))
        self.assertEqual(self.doctor(fake_kit(self.root), clt_env(self.root)), [])

    def test_a_kit_whose_build_all_lacks_a_flag_is_refused(self):
        for flag in kit.CAPABILITIES:
            with self.subTest(flag=flag):
                flags = " ".join(f for f in kit.CAPABILITIES if f != flag)
                errs = self.doctor(fake_kit(self.root / flag.strip("-"), flags=flags), clt_env(self.root))
                self.assertTrue(any("no " + flag in e for e in errs), errs)


class Pipeline(Tmp):
    def setUp(self):
        super().setUp()
        self.kit = fake_kit(self.root)
        self.calls = []
        self.rcs = {}

        def fake_stream(argv, env, log):
            label = "recompile" if "build_all.py" in argv[3] else "package"
            self.calls.append((label, argv, env))
            if label == "package" and not self.rcs.get("package"):
                (Path(argv[argv.index("--out") + 1]) / APP).mkdir(parents=True)
                (Path(argv[argv.index("--out") + 1]) / APP / "new").write_text("n")
            return self.rcs.get(label, 0)

        def fake_add_game(app, src, game, say=print):
            self.added.append((Path(app), Path(src), Path(game)))
            if self.rcs.get("game-data"):
                raise kit_app.Fail("codesign failed")

        self.added = []
        self.patches = [
            mock.patch.object(kit, "stream", fake_stream),
            mock.patch.object(kit, "doctor", lambda *a, **k: []),
            mock.patch.object(kit, "add_game", fake_add_game),
            mock.patch.object(gamecheck, "check_game", lambda g: self.game),
        ]
        self.game = OK_GAME
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def build(self, **kw):
        a = Namespace(game=str(self.root / "game"), work=str(self.work), app=str(self.app), overlay=str(self.root / "ov"), **kw)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = kit.build(a, {"PATH": "x"}, self.kit)
        return rc, out.getvalue()

    def test_a_bad_game_folder_exits_6_and_builds_nothing(self):
        self.game = BAD_GAME
        rc, out = self.build()
        self.assertEqual(rc, 6)
        self.assertIn("vampire.exe", out)
        self.assertEqual(self.calls, [])

    def test_recompile_runs_the_bundled_python_with_the_k3a_flags(self):
        self.build()
        label, argv, _ = self.calls[0]
        self.assertEqual(label, "recompile")
        self.assertEqual(argv[:3], [str(self.kit / "python/bin/python3"), "-I", "-B"])
        self.assertEqual(argv[3], str(self.kit / "src/tools/recomp/build_all.py"))
        self.assertEqual(argv[argv.index("--game") + 1], str(self.root / "game"))
        self.assertEqual(argv[argv.index("--out") + 1], str(self.work))
        self.assertIn("--no-oracle", argv)
        self.assertEqual(argv[argv.index("--prebuilt") + 1], str(self.kit / "prebuilt"))
        self.assertLessEqual(int(argv[argv.index("--jobs") + 1]), 6)

    def test_a_kit_with_a_profile_builds_the_game_with_it(self):
        prof = self.kit / kit.kitconst.PGO_PROFILE
        prof.parent.mkdir(parents=True, exist_ok=True)
        prof.write_bytes(b"profile")
        self.build()
        builds = [argv for label, argv, _ in self.calls if label.startswith("recompile")]
        self.assertTrue(builds)
        for argv in builds:
            self.assertEqual(argv[argv.index("--pgo-profile") + 1], str(prof))

    def test_a_kit_without_a_profile_passes_no_profile(self):
        self.build()
        for label, argv, _ in self.calls:
            self.assertNotIn("--pgo-profile", argv)

    def test_package_runs_the_bundled_python_without_writing_bytecode(self):
        self.build()
        label, argv, _ = self.calls[-1]
        self.assertEqual(label, "package")
        self.assertEqual(argv[:4], [str(self.kit / "python/bin/python3"), "-I", "-B", str(self.kit / "src/tools/kit/kit_app.py")])

    def test_nothing_in_the_pipeline_asks_for_ghidra(self):
        self.build()
        self.assertNotIn("analyzeHeadless", " ".join(sum((c[1] for c in self.calls), [])))
        self.assertNotIn("analyzeHeadless", (HERE / "kit.py").read_text())

    def test_build_environment_has_no_homebrew(self):
        env = kit.kit_env(self.kit, self.work)
        self.assertEqual(env["PATH"], "%s:%s:/usr/bin:/bin:/usr/sbin:/sbin" % (self.work / "kit-bin", self.kit / "python/bin"))
        self.assertNotIn("homebrew", env["PATH"])

    def test_build_children_write_no_bytecode_into_the_app(self):
        env = kit.kit_env(self.kit, self.work)
        mod = self.work / "probe_mod"
        mod.mkdir(parents=True, exist_ok=True)
        (mod / "probe_pyc.py").write_text("x = 1\n")
        env = dict(env, PATH=os.environ.get("PATH", ""))
        subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import probe_pyc", str(mod)], env=env, check=True)
        self.assertFalse((mod / "__pycache__").exists())

    def test_meson_runs_through_the_bundled_python_not_a_baked_in_shebang(self):
        kit.write_shims(self.kit, self.work)
        shim = (self.work / "kit-bin" / "meson").read_text()
        self.assertIn('"%s" -I -B -m mesonbuild.mesonmain' % (self.kit / "python/bin/python3"), shim)
        self.assertTrue(os.access(self.work / "kit-bin" / "meson", os.X_OK))

    def test_translate_and_link_exits_pass_through(self):
        for rc in (1, 2, 3, 4):
            with self.subTest(rc=rc):
                self.calls.clear()
                self.rcs = {"recompile": rc}
                self.assertEqual(self.build()[0], rc)
                self.assertEqual([c[0] for c in self.calls], ["recompile"])

    def test_package_runs_staged_beside_the_app_with_the_prebuilt_set(self):
        self.build()
        label, argv, _ = self.calls[1]
        self.assertEqual(label, "package")
        self.assertEqual(argv[argv.index("--out") + 1], str(self.app.parent / ".staging"))
        self.assertEqual(argv[argv.index("--build") + 1], str(self.work))
        self.assertEqual(argv[argv.index("--prebuilt") + 1], str(self.kit / "prebuilt"))

    def test_success_installs_the_staged_app_and_clears_staging(self):
        rc, out = self.build()
        self.assertEqual(rc, 0)
        self.assertEqual((self.app / "new").read_text(), "n")
        self.assertFalse((self.app.parent / ".staging").exists())
        self.assertIn("installed", out)

    def test_plain_build_puts_the_original_files_into_the_staged_app(self):
        rc, out = self.build()
        self.assertEqual(rc, 0)
        self.assertEqual(self.added, [(self.app.parent / ".staging" / APP, self.root / "game", self.root / "game")])
        self.assertNotIn("--up-build", self.calls[-1][1])
        self.assertIn("game data: %s/Contents/Resources/game" % APP, out)

    def test_patched_build_puts_the_up_root_from_the_work_folder_into_the_staged_app(self):
        game = self.root / "game"
        game.mkdir()
        (game / kit.up.NAME).write_bytes(b"i")
        seen = []

        def fake_prepare(installer, g, base, say=print):
            seen.append((Path(installer), Path(g), Path(base)))
            self.assertTrue(Path(base).is_relative_to(self.work))
            return Path(base) / "gu"

        with mock.patch.object(kit.up, "check_installer", return_value=kit.up.MD5), mock.patch.object(kit.up, "prepare", fake_prepare):
            rc, out = self.build(unofficial_patch="on")
        self.assertEqual(rc, 0, out)
        self.assertEqual(seen, [(game / kit.up.NAME, game, self.work / kit.UP_DATA)])
        self.assertEqual(self.added[0][1], self.work / kit.UP_DATA / "gu")
        self.assertEqual([c[0] for c in self.calls], ["recompile", "recompile", "package"])
        self.assertIn("--up-ini", self.calls[1][1])
        self.assertEqual(self.calls[1][1][self.calls[1][1].index("--up-ini") + 1], str(self.work / kit.UP_DATA / "gu/Bin/loader/loader.ini"))
        self.assertIn("--up-build", self.calls[-1][1])

    def test_patch_on_without_an_installer_in_the_folder_stops_before_building(self):
        (self.root / "game").mkdir()
        rc, out = self.build(unofficial_patch="on")
        self.assertEqual(rc, kit.EXIT_PATCH)
        self.assertIn("no VTMBup*.exe", out)
        self.assertEqual(self.calls, [])

    def test_success_removes_the_work_folder(self):
        rc, out = self.build()
        self.assertEqual(rc, 0)
        self.assertFalse(self.work.exists())
        self.assertIn("removed the build work folder %s" % self.work, out)

    def test_failure_keeps_the_work_folder_and_its_log(self):
        for rcs in ({"recompile": 2}, {"package": 1}, {"game-data": 1}):
            with self.subTest(rcs=rcs):
                self.rcs = rcs
                (self.work / "kit.log").parent.mkdir(parents=True, exist_ok=True)
                (self.work / "kit.log").write_text("log")
                rc, out = self.build()
                self.assertNotEqual(rc, 0)
                self.assertTrue((self.work / "kit.log").is_file())
                self.assertNotIn("removed the build work folder", out)

    def test_read_only_game_root_in_the_work_folder_is_still_removed(self):
        ro = self.work / kit.UP_DATA / "gu" / "Bin"
        ro.mkdir(parents=True)
        (ro / "x.dll").write_bytes(b"x")
        for p in (ro / "x.dll", ro, ro.parent):
            p.chmod(0o555 if p.is_dir() else 0o444)
        self.assertEqual(self.build()[0], 0)
        self.assertFalse(self.work.exists())

    def test_each_step_prints_one_line_with_its_time(self):
        out = self.build()[1]
        self.assertRegex(out, r"recompile: ok \(\d+ s\)")
        self.assertRegex(out, r"package: ok \(\d+ s\)")

    def test_a_failed_package_leaves_the_installed_app_alone(self):
        self.app.mkdir(parents=True)
        (self.app / "old").write_text("o")
        self.rcs = {"package": 1}
        self.assertEqual(self.build()[0], 1)
        self.assertEqual((self.app / "old").read_text(), "o")

    def test_build_holds_the_lock_while_running_and_frees_it_after(self):
        held = []
        orig = kit.stream

        def probe(argv, env, log):
            held.append(kit.lock_free(self.work) is None)
            return orig(argv, env, log)

        with mock.patch.object(kit, "stream", probe):
            self.build()
        self.assertEqual(held, [True, True])
        again = kit.lock_free(self.work)
        self.assertIsNotNone(again)
        again.close()

    def test_a_second_build_while_locked_exits_5(self):
        self.work.mkdir()
        with open(self.work / kit.LOCK_NAME, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            rc, out = self.build()
        self.assertEqual(rc, 5)
        self.assertEqual(self.calls, [])


def resource_section(tree, rva):
    types = sorted(tree)
    leaves = [(t, i, tree[t][i]) for t in types for i in sorted(tree[t])]
    off = 16 + 8 * len(types)
    tdir, ldir, dent, blob = {}, {}, {}, {}
    for t in types:
        tdir[t] = off
        off += 16 + 8 * len(tree[t])
    for t, i, _ in leaves:
        ldir[(t, i)] = off
        off += 24
    for t, i, _ in leaves:
        dent[(t, i)] = off
        off += 16
    for t, i, b in leaves:
        blob[(t, i)] = off
        off += len(b)
    buf = bytearray(off)
    struct.pack_into("<IIHHHH", buf, 0, 0, 0, 0, 0, 0, len(types))
    for k, t in enumerate(types):
        struct.pack_into("<II", buf, 16 + 8 * k, t, 0x80000000 | tdir[t])
        ids = sorted(tree[t])
        struct.pack_into("<IIHHHH", buf, tdir[t], 0, 0, 0, 0, 0, len(ids))
        for j, i in enumerate(ids):
            struct.pack_into("<II", buf, tdir[t] + 16 + 8 * j, i, 0x80000000 | ldir[(t, i)])
    for t, i, b in leaves:
        struct.pack_into("<IIHHHHII", buf, ldir[(t, i)], 0, 0, 0, 0, 0, 1, 0x409, dent[(t, i)])
        struct.pack_into("<IIII", buf, dent[(t, i)], rva + blob[(t, i)], len(b), 0, 0)
        buf[blob[(t, i)]:blob[(t, i)] + len(b)] = b
    return bytes(buf)


def dib4(rows):
    h, w = len(rows), len(rows[0])
    head = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 4, 0, 0, 0, 0, 0, 0)
    pal = bytes((0, 0, 0, 0, 255, 255, 255, 0, 0, 0, 255, 0)) + bytes(13 * 4)
    code = {".": 0, "W": 1, "R": 2, " ": 0}
    stride, mask_stride = ((w * 4 + 31) // 32) * 4, ((w + 31) // 32) * 4
    xor, mask = bytearray(), bytearray()
    for row in reversed(rows):
        r = bytearray(stride)
        m = bytearray(mask_stride)
        for x, ch in enumerate(row):
            r[x // 2] |= code[ch] << (0 if x % 2 else 4)
            if ch == " ":
                m[x // 8] |= 0x80 >> (x % 8)
        xor += r
        mask += m
    return head + pal + bytes(xor) + bytes(mask)


def fake_pe(path, sizes=(16, 32)):
    icons = [dib4(["." * s] * (s // 2 - 1) + ["W" * s] * 2 + ["." * s] * (s // 2 - 1)) for s in sizes]
    group = struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(struct.pack("<BBBBHHIH", s, s, 0, 0, 1, 4, len(icons[k]), k + 1) for k, s in enumerate(sizes))
    sec = resource_section({appicon.RT_ICON: {k + 1: b for k, b in enumerate(icons)}, appicon.RT_GROUP_ICON: {1: group}}, 0x1000)
    d = bytearray(0x200 + len(sec))
    d[0:2] = b"MZ"
    struct.pack_into("<I", d, 0x3C, 0x40)
    d[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", d, 0x44, 0x14C, 1, 0, 0, 0, 224, 0x102)
    opt = 0x58
    struct.pack_into("<H", d, opt, 0x10B)
    struct.pack_into("<I", d, opt + 92, 16)
    struct.pack_into("<II", d, opt + 96 + 16, 0x1000, len(sec))
    d[opt + 224:opt + 232] = b".rsrc\0\0\0"
    struct.pack_into("<IIII", d, opt + 232, len(sec), 0x1000, len(sec), 0x200)
    d[0x200:] = sec
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(d))
    return Path(path)


def fake_bundle(root):
    app = Path(root) / APP
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources").mkdir()
    script(app / "Contents/MacOS/run", "exit 0\n")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "run", "CFBundleIdentifier": kitconst.BUNDLE_ID, "CFBundlePackageType": "APPL"}))
    return app


def tree_state(root):
    return {str(p.relative_to(root)): (p.stat().st_mode, p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob("*"))}


class GameData(Tmp):
    def setUp(self):
        super().setUp()
        self.game = self.root / "Games" / "Vampire Bloodlines (Windows)"
        fake_pe(self.game / "vampire.exe")
        for rel, data in {"Bin/engine.dll": b"engine", "Vampire/pack000.vpk": b"pack", kit.up.NAME: b"installer"}.items():
            (self.game / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.game / rel).write_bytes(data)
        self.app = fake_bundle(self.root / "staging (x)")
        self.lines = []

    def lock_game(self):
        for p in sorted(self.game.rglob("*"), reverse=True) + [self.game]:
            p.chmod(0o555 if p.is_dir() else 0o444)
        self.addCleanup(lambda: [p.chmod(0o755 if p.is_dir() else 0o644) for p in [self.game] + list(self.game.rglob("*"))])

    def test_plain_game_data_icon_and_signature(self):
        self.lock_game()
        before = tree_state(self.game)
        dst = kit.add_game(self.app, self.game, self.game, self.lines.append)
        self.assertEqual(dst, self.app / "Contents/Resources/game")
        self.assertEqual((dst / "Vampire/pack000.vpk").read_bytes(), b"pack")
        self.assertFalse((dst / kit.up.NAME).exists())
        self.assertEqual(tree_state(self.game), before)
        self.assertTrue((self.app / "Contents/Resources/AppIcon.icns").is_file())
        self.assertEqual(plistlib.loads((self.app / "Contents/Info.plist").read_bytes())["CFBundleIconFile"], "AppIcon.icns")
        r = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(self.app)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.access(dst / "Bin/engine.dll", os.W_OK))

    def test_patched_root_goes_in_and_its_stamp_stays_out(self):
        root = self.root / "work" / kit.UP_DATA / (kit.up.VARIANT + "-gu")
        (root / "Unofficial_Patch").mkdir(parents=True)
        (root / "Unofficial_Patch/patch.vpk").write_bytes(b"up")
        (root / "gu.sha256").write_text("s\n")
        for p in sorted(root.rglob("*"), reverse=True) + [root]:
            p.chmod(0o555 if p.is_dir() else 0o444)
        dst = kit.add_game(self.app, root, self.game, self.lines.append)
        self.assertEqual((dst / "Unofficial_Patch/patch.vpk").read_bytes(), b"up")
        self.assertFalse((dst / "gu.sha256").exists())
        kit.remove_work(self.root / "work")
        self.assertFalse((self.root / "work").exists())
        self.assertEqual(subprocess.run(["codesign", "--verify", "--deep", "--strict", str(self.app)]).returncode, 0)

    def test_no_icon_still_succeeds_with_one_line(self):
        (self.game / "vampire.exe").write_bytes(b"MZ" + b"\0" * 64)
        kit.add_game(self.app, self.game, self.game, self.lines.append)
        self.assertEqual(len([l for l in self.lines if l.startswith("no app icon")]), 1)
        self.assertNotIn("CFBundleIconFile", plistlib.loads((self.app / "Contents/Info.plist").read_bytes()))
        self.assertEqual(subprocess.run(["codesign", "--verify", "--deep", "--strict", str(self.app)]).returncode, 0)

    def test_the_largest_icon_frame_is_used(self):
        self.assertEqual(max(appicon.frames(self.game / "vampire.exe"))[0], 32)

    def test_icon_pixels_keep_their_real_colours(self):
        self.assertEqual(appicon.dib_lit(dib4(["W.", "R "])), [[1.0, 0.0], [0.6, 1.0]])

    def test_only_the_largest_shape_survives_so_a_frame_is_dropped(self):
        lit = [[1.0 if ch != "." else 0.0 for ch in r] for r in ("R....", "..WW.", "..WW.", ".....", "....R")]
        self.assertEqual(sorted(appicon.largest_shape(lit)), [(2, 1), (2, 2), (3, 1), (3, 2)])

    def test_the_game_icon_is_an_opaque_dark_tile_with_a_bright_mark(self):
        lit = [[1.0 if 14 <= x <= 17 else 0.0 for x in range(32)] for _ in range(32)]
        field, cols, rows = appicon.field_of(appicon.largest_shape(lit), lit)
        pixel = appicon.game_pixel(field, cols, rows)
        self.assertEqual(pixel(0, 0)[3], 0)
        self.assertEqual(pixel(200, 512)[3], 255)
        self.assertLess(max(pixel(200, 512)[:3]), 80)
        self.assertGreater(min(pixel(512, 512)[:3]), 150)

    def test_the_porter_icon_is_a_dark_tile_with_a_drop_and_an_arrow(self):
        self.assertEqual(appicon.porter_pixel(0, 0)[3], 0)
        tile, drop, arrow = appicon.porter_pixel(512, 130), appicon.porter_pixel(320, 650), appicon.porter_pixel(512, 700)
        self.assertLess(max(tile[:3]), 60)
        self.assertGreater(drop[0], drop[1] + 80)
        self.assertGreater(min(arrow[:3]), 230)

    def test_rerun_replaces_old_game_data(self):
        kit.add_game(self.app, self.game, self.game, self.lines.append)
        (self.app / "Contents/Resources/game/stale").write_text("x")
        kit.add_game(self.app, self.game, self.game, self.lines.append)
        self.assertFalse((self.app / "Contents/Resources/game/stale").exists())


class Install(Tmp):
    def staged(self):
        s = self.root / "stage" / "Bloodlines Native.app"
        s.mkdir(parents=True)
        (s / "f").write_text("new")
        return s

    def test_fresh_install(self):
        kit.install(self.staged(), self.app)
        self.assertEqual((self.app / "f").read_text(), "new")

    def test_replaces_the_old_app_and_drops_the_backup_folder(self):
        self.app.mkdir(parents=True)
        (self.app / "f").write_text("old")
        kit.install(self.staged(), self.app)
        self.assertEqual((self.app / "f").read_text(), "new")
        self.assertFalse(self.app.with_name(self.app.name + ".replaced").exists())

    def test_a_failed_rename_puts_the_old_app_back(self):
        self.app.mkdir(parents=True)
        (self.app / "f").write_text("old")
        with self.assertRaises(OSError):
            kit.install(self.root / "does-not-exist", self.app)
        self.assertEqual((self.app / "f").read_text(), "old")


class Commands(Tmp):
    def test_game_check_tolerates_the_installer_and_ds_store_in_the_game_folder(self):
        import hashlib
        game = self.root / "VTMB"
        (game / "Bin").mkdir(parents=True)
        (game / "vampire.exe").write_bytes(b"exe")
        (game / kit.up.NAME).write_bytes(b"installer")
        (game / ".DS_Store").write_bytes(b"x")
        manifest = self.root / "manifest.txt"
        manifest.write_text("%s  vampire.exe\n" % hashlib.sha256(b"exe").hexdigest())
        res = gamecheck.check_game(game, manifest)
        self.assertTrue(res["ok"], res)

    def test_defaults_match_the_spec(self):
        self.assertEqual(kit.DEFAULT_WORK, Path.home() / "Library/Caches/Vampire Bloodlines")
        self.assertEqual(kit.DEFAULT_APP, Path.home() / "Games/Vampire Bloodlines.app")
        self.assertEqual(kitconst.SUPPORT, Path.home() / "Library/Application Support/Vampire Bloodlines")

    def test_names_live_in_kitconst_only(self):
        self.assertEqual((kitconst.APP_NAME, kitconst.KIT_APP_NAME, kitconst.BUNDLE_ID, kitconst.SUPPORT_NAME, kitconst.INSTALL_DIR, kitconst.CLI_NAME),
                         ("Vampire Bloodlines.app", "Bloodlines Porter.app", "local.vampire-bloodlines", "Vampire Bloodlines", "Games", "bloodlines-porter"))
        literals = ("Vampire Bloodlines", "Bloodlines Porter", "local.vampire-bloodlines", "bloodlines-porter", "BloodlinesNative", "Bloodlines Native", "Bloodlines Kit")
        for f in ["kit.py", "kit_app.py", "launcher.py", "make_kit.py", "up.py", "collect_logs.py", "gamecheck.py", "appicon.py", "app/package.py"]:
            text = (HERE / f).read_text()
            with self.subTest(f=f):
                self.assertEqual([n for n in literals if n in text], [])
        self.assertEqual(kit_app.BUNDLE_ID, kitconst.BUNDLE_ID)
        self.assertEqual(kitconst.WRAPPER_NAME, "Vampire Bloodlines")

    def test_check_command_runs_the_kit_check(self):
        env = clt_env(self.root)
        with mock.patch.object(make_kit, "check", lambda k, leaks: ["EULA.txt is missing"]):
            rc, out = self.run_main(["check"], env, self.root)
        self.assertEqual(rc, 1)
        self.assertIn("EULA.txt is missing", out)
        with mock.patch.object(make_kit, "check", return_value=[]) as c:
            self.assertEqual(self.run_main(["check"], env, self.root)[0], 0)
        c.assert_called_once_with(self.root, leaks=False)

    def test_check_command_tests_the_command_line_tools_first(self):
        with mock.patch.object(make_kit, "check") as c:
            rc, out = self.run_main(["check"], clt_env(self.root, select_rc=2), self.root)
        self.assertEqual(rc, 5)
        self.assertIn("xcode-select --install", out)
        c.assert_not_called()

    def test_a_quarantined_kit_is_named(self):
        env = clt_env(self.root)
        script(Path(env["PATH"]) / "xattr", "echo 0081;com.apple.quarantine\nexit 0\n")
        errs = self.doctor(fake_kit(self.root), env)
        self.assertTrue(any("com.apple.quarantine" in e and "xattr -dr" in e for e in errs), errs)

    def test_install_waits_for_the_game_lock_and_says_so(self):
        import threading
        import time
        s = self.root / "stage" / "Bloodlines Native.app"
        s.mkdir(parents=True)
        self.overlay.mkdir()
        held = open(self.overlay / launcher.lock_path(self.overlay).name, "a+")
        fcntl.flock(held, fcntl.LOCK_EX)
        out = io.StringIO()
        done = threading.Event()

        def go():
            with redirect_stdout(out):
                kit.install(s, self.app)
            done.set()

        t = threading.Thread(target=go)
        t.start()
        time.sleep(0.5)
        self.assertFalse(done.is_set())
        self.assertFalse(self.app.exists())
        held.close()
        t.join(10)
        self.assertTrue(done.is_set())
        self.assertTrue(self.app.is_dir())
        self.assertIn("game is running", out.getvalue())

    def test_kit_root_is_found_from_the_kit_layout_under_python_dash_i(self):
        k = self.root / "kit"
        shutil.copytree(HERE, k / "src/tools/kit", ignore=shutil.ignore_patterns("__pycache__", ".build"))
        shutil.copy2(HERE.parent / "devpaths.py", k / "src/tools/devpaths.py")
        (k / "SOURCE").write_text("a" * 40 + "\n")
        r = subprocess.run([sys.executable, "-I", str(k / "src/tools/kit/kit.py"), "check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("SOURCE is missing", r.stdout)
        self.assertIn("bloodlines-porter wrapper is missing", r.stdout)
        self.assertIn("bloodlines-kit wrapper is missing", r.stdout)

    def test_wrapper_runs_this_file(self):
        self.assertIn('python3" -I -B "$DIR/src/tools/kit/kit.py"', make_kit.WRAPPER)

    def test_the_app_wrapper_runs_the_launcher_without_writing_bytecode(self):
        self.assertEqual(kit_app.WRAPPER.count(" -I -B "), 2)
        self.assertNotIn(" -I \"", kit_app.WRAPPER)

    def test_no_dialog_is_ever_opened(self):
        self.assertNotIn("osascript", (HERE / "kit.py").read_text())


if __name__ == "__main__":
    unittest.main()
