#!/usr/bin/env python3
import hashlib
import json
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gamecheck
import kit_app
import kitconst
import launcher
import test_launcher as tl
import up

HEX = "ab" * 32
CANCELLED = tl.CANCELLED


def installer_file(tmp, data=b"installer"):
    p = Path(tmp) / up.NAME
    p.write_bytes(data)
    return p


class Installer(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.f = installer_file(self.t.name)
        md5 = hashlib.md5(b"installer").hexdigest()
        p = mock.patch.object(up, "KNOWN", {md5: up.NAME})
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(up, "MD5", md5)
        q.start()
        self.addCleanup(q.stop)
        self.md5 = md5

    def test_known_installer_passes(self):
        self.assertEqual(up.check_installer(self.f), self.md5)

    def test_one_byte_changed_is_refused_with_both_md5s(self):
        bad = installer_file(self.t.name, b"installes")
        with self.assertRaises(up.UpError) as c:
            up.check_installer(bad)
        found = hashlib.md5(b"installes").hexdigest()
        self.assertIn("MD5 %s found" % found, str(c.exception))
        self.assertIn("MD5 %s expected" % self.md5, str(c.exception))
        self.assertIn(up.NAME, str(c.exception))

    def test_missing_file(self):
        with self.assertRaisesRegex(up.UpError, "installer not found"):
            up.check_installer(Path(self.t.name) / "nope.exe")


class FindInstaller(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.game = Path(self.t.name) / "Games" / "Vampire Bloodlines (Windows)"
        self.game.mkdir(parents=True)
        (self.game / "vampire.exe").write_bytes(b"exe")

    def test_none_asks(self):
        self.assertEqual(up.find_installers(self.game), [])
        with self.assertRaisesRegex(up.UpError, "no VTMBup\\*.exe in .*Vampire Bloodlines \\(Windows\\).*--up-installer"):
            up.pick_installer(self.game)

    def test_one_is_used_without_asking(self):
        f = installer_file(self.game)
        self.assertEqual(up.pick_installer(self.game), f)

    def test_two_ask_and_name_both(self):
        installer_file(self.game)
        (self.game / "vtmbup11.EXE").write_bytes(b"x")
        with self.assertRaisesRegex(up.UpError, "2 installers .*%s, vtmbup11.EXE" % re.escape(up.NAME)):
            up.pick_installer(self.game)

    def test_a_given_installer_anywhere_wins(self):
        installer_file(self.game)
        other = Path(self.t.name) / "Downloads (old)" / up.NAME
        self.assertEqual(up.pick_installer(self.game, str(other)), other)

    def test_folders_and_other_exes_are_not_installers(self):
        (self.game / "VTMBup-folder.exe").mkdir()
        (self.game / "setup.exe").write_bytes(b"x")
        self.assertEqual(up.find_installers(self.game), [])


class Prepare(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.tmp = Path(self.t.name)
        self.overlay = self.tmp / "ov"
        self.game = self.tmp / "game"
        self.game.mkdir()
        self.f = installer_file(self.tmp)
        md5 = hashlib.md5(b"installer").hexdigest()
        for name, val in (("KNOWN", {md5: up.NAME}), ("MD5", md5)):
            p = mock.patch.object(up, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.bt, self.gr = up.load()
        self.say = []
        self.check = mock.patch.object(gamecheck, "check_game", return_value={"ok": True, "files": 3, "failures": []})
        self.check.start()
        self.addCleanup(self.check.stop)

    def extract(self, installer, out, innoextract=None):
        Path(out).mkdir()
        (Path(out) / "manifest.tsv").write_text("x")

    def build_root(self, g, u, gu, log=print):
        Path(gu).mkdir(parents=True, exist_ok=True)
        (Path(gu) / "gu.sha256").write_text("s\n")
        return "built"

    def prepare(self):
        with mock.patch.object(up, "find_innoextract", return_value="/x/innoextract"), \
                mock.patch.object(self.bt, "build", side_effect=self.extract) as b, \
                mock.patch.object(self.gr, "build", side_effect=self.build_root) as g:
            root = up.prepare(self.f, str(self.game), self.overlay, say=self.say.append)
        return b, g, root

    def test_the_root_is_built_inside_the_given_work_folder(self):
        b, g, root = self.prepare()
        self.assertEqual(root, up.game_root(self.overlay))
        self.assertEqual(b.call_args[0][:2], (str(self.f), str(up.tree(self.overlay))))
        self.assertEqual(g.call_args[0][:3], (str(self.game), str(up.tree(self.overlay)), str(up.game_root(self.overlay))))
        self.assertIn(up.installer_line(up.MD5), self.say)

    def test_extracted_tree_is_reused(self):
        self.prepare()
        b, g, _ = self.prepare()
        b.assert_not_called()
        g.assert_called_once()

    def test_installer_is_never_copied_and_no_state_file_is_written(self):
        self.prepare()
        names = [p.name for p in self.overlay.rglob("*")]
        self.assertNotIn(up.NAME, names)
        self.assertNotIn("up.json", names)

    def test_unknown_md5_stops_before_anything_is_built(self):
        self.f.write_bytes(b"other")
        with mock.patch.object(self.bt, "build") as b, mock.patch.object(self.gr, "build") as g:
            with self.assertRaisesRegex(up.UpError, "unknown installer"):
                up.prepare(self.f, str(self.game), self.overlay)
        b.assert_not_called()
        g.assert_not_called()

    def test_extraction_failure_message(self):
        with mock.patch.object(up, "find_innoextract", return_value="x"), \
                mock.patch.object(self.bt, "build", side_effect=self.bt.BasicTreeError("innoextract failed: boom")):
            with self.assertRaisesRegex(up.UpError, "extraction failed: innoextract failed: boom"):
                up.prepare(self.f, str(self.game), self.overlay, say=self.say.append)

    def test_disk_full_message(self):
        with mock.patch.object(up, "find_innoextract", return_value="x"), \
                mock.patch.object(self.bt, "build", side_effect=self.extract), \
                mock.patch.object(self.gr, "build", side_effect=self.gr.GameRootError("cp failed: No space left on device")):
            with self.assertRaisesRegex(up.UpError, "not enough disk space"):
                up.prepare(self.f, str(self.game), self.overlay, say=self.say.append)

    def test_leftover_tmp_from_a_crashed_extraction_is_cleared(self):
        tmp = Path(str(up.tree(self.overlay)) + ".tmp")
        tmp.mkdir(parents=True)
        (tmp / "junk").write_text("x")
        self.prepare()
        self.assertFalse(tmp.exists())


class Innoextract(unittest.TestCase):
    def fake(self, text):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        p = Path(t.name) / "innoextract"
        p.write_text("#!/bin/sh\necho '%s'\n" % text)
        p.chmod(0o755)
        return p

    def find(self, text):
        p = self.fake(text)
        with mock.patch("shutil.which", return_value=str(p)):
            return up.find_innoextract()

    def test_1_9_and_newer_pass(self):
        self.assertTrue(self.find("innoextract 1.9 - extract installers").endswith("innoextract"))
        self.assertTrue(self.find("innoextract 1.10 - x"))

    def test_older_or_unreadable_refused(self):
        with self.assertRaisesRegex(up.UpError, "1.9 or newer"):
            self.find("innoextract 1.8 - x")
        with self.assertRaisesRegex(up.UpError, "no version"):
            self.find("")

    def test_missing(self):
        with mock.patch("shutil.which", return_value=None), mock.patch.object(up, "INNO_DIRS", ()):
            with self.assertRaisesRegex(up.UpError, "not installed"):
                up.find_innoextract()


class Launcher(tl.Base):
    def setUp(self):
        super().setUp()
        self.exe.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.up_exe = tl.put(self.bundle / "Contents/MacOS" / kitconst.UP_EXE_NAME, b"#!/bin/sh\necho 'build sha256 %s'\n" % HEX.encode())
        self.up_exe.chmod(0o755)
        self.good = tl.GameCheck.make_game(self)

    def argv(self, out):
        return shlex.split(next(l for l in out.splitlines() if l.startswith("command: "))[len("command: "):])

    def test_a_plain_app_runs_vanilla_from_its_own_game_folder(self):
        self.up_exe.unlink()
        out = self.L("status", "--bundle", str(self.bundle))[1]
        self.assertIn("unofficial patch: off", out)
        argv = self.argv(self.dry()[1])
        self.assertEqual(argv[0], str(self.exe))
        self.assertEqual(argv[argv.index("--game") + 1], str(self.game_data))
        self.assertNotIn("Unofficial_Patch", argv)

    def test_a_patched_app_runs_the_patched_exe_with_the_mod_from_its_own_game_folder(self):
        self.L("set", "--game", str(self.good))
        argv = self.argv(self.dry()[1])
        self.assertEqual(argv[0], str(self.up_exe))
        self.assertEqual(argv[argv.index("--game") + 1], str(self.game_data))
        self.assertEqual(argv[argv.index("--") + 1:], ["-game", "Unofficial_Patch", "+cl_captions", "1"])

    def test_the_patched_config_decides_the_captions_not_the_stock_one(self):
        self.L("set", "--game", str(self.good))
        cfg = self.overlay / "files/Vampire/cfg/config.cfg"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("x\n")
        self.assertIn("+cl_captions", self.argv(self.dry()[1]))
        up_cfg = self.overlay / "files/Unofficial_Patch/cfg/config.cfg"
        up_cfg.parent.mkdir(parents=True)
        up_cfg.write_text("x\n")
        self.assertNotIn("+cl_captions", self.argv(self.dry()[1]))

    def test_developer_extra_follows_the_mod_argument(self):
        self.L("set", "--mode", "developer", "--extra", "+map x")
        argv = self.argv(self.dry()[1])
        self.assertEqual(argv[argv.index("--") + 1:], ["-game", "Unofficial_Patch", "+cl_captions", "1", "+map", "x"])

    def test_the_patch_cannot_be_switched_at_run_time(self):
        with self.assertRaises(SystemExit):
            self.L("set", "--unofficial-patch", "off")
        self.assertNotIn("unofficial_patch", launcher.DEFAULTS)

    def test_old_patch_keys_in_launcher_json_are_ignored(self):
        tl.put(self.overlay / "launcher.json", json.dumps({"unofficial_patch": "on", "up_installer": "/x.exe", "mode": "developer"}))
        s = launcher.load_settings(self.overlay, launcher.Log(self.overlay))
        self.assertNotIn("unofficial_patch", s)
        self.assertEqual(s["mode"], "developer")

    def test_status_shows_the_build_sha_read_against_the_bundled_game(self):
        self.up_exe.write_bytes(b"#!/bin/sh\nprintf '%%s\\n' \"$@\" > \"$(dirname \"$0\")/args\"\necho 'build sha256 %s'\n" % HEX.encode())
        out = self.L("status", "--bundle", str(self.bundle))[1]
        self.assertIn("unofficial patch: on", out)
        self.assertIn("unofficial patch build: sha256 " + HEX, out)
        self.assertEqual((self.up_exe.parent / "args").read_text().splitlines(),["--up-list-fixes", "--game", str(self.game_data)])

    def test_status_says_when_the_build_line_is_missing(self):
        self.up_exe.write_bytes(b"#!/bin/sh\necho nothing\n")
        out = self.L("status", "--bundle", str(self.bundle))[1]
        self.assertIn("unofficial patch build: unknown", out)

    def test_nothing_writes_up_data_into_application_support(self):
        self.L("set", "--game", str(self.good))
        self.L("status", "--bundle", str(self.bundle))
        self.dry()
        self.assertFalse((self.overlay / "up").exists())
        self.assertFalse((self.overlay / "up.json").exists())

    def test_the_wrapper_dispatches_set_and_status(self):
        for word in ("set", "status", "run"):
            self.assertIn(word, kitconst.WRAPPER)


class WindowsPatchedGame(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.g = Path(self.t.name) / "game"
        self.files = {"vampire.exe": b"vanilla exe", "Bin/binkw32.dll": b"vanilla bink", "Bin/engine.dll": b"engine"}
        for n, d in self.files.items():
            tl.put(self.g / n, d)
        self.manifest = Path(self.t.name) / "m.txt"
        self.manifest.write_text("".join("%s  %s\n" % (hashlib.sha256(d).hexdigest(), n) for n, d in self.files.items()))
        p = mock.patch.object(gamecheck, "UP_REPLACED", {"vampire.exe": hashlib.sha256(b"UP loader").hexdigest(), "bin/binkw32.dll": hashlib.sha256(b"UP bink").hexdigest()})
        p.start()
        self.addCleanup(p.stop)

    def add_up_files(self):
        tl.put(self.g / "vampire.exe", b"UP loader")
        tl.put(self.g / "Bin/binkw32.dll", b"UP bink")
        tl.put(self.g / "Loader.exe", b"loader")
        tl.put(self.g / "Bin/loader/loader.ini", b"[Mods]")
        tl.put(self.g / "Bin/loader/x.vtm", b"vtm")

    def test_d5_up_files_added_is_accepted_with_a_note(self):
        self.add_up_files()
        res = gamecheck.check_game(self.g, self.manifest)
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(res["up_patched"]), ["Bin/binkw32.dll", "vampire.exe"])
        with mock.patch("builtins.print") as pr:
            self.assertEqual(gamecheck.report(res), 0)
        self.assertIn("Windows Unofficial Patch", pr.call_args_list[-1][0][0])

    def test_vanilla_game_has_no_note(self):
        res = gamecheck.check_game(self.g, self.manifest)
        self.assertEqual((res["ok"], res["up_patched"]), (True, []))

    def test_any_other_change_is_still_refused(self):
        self.add_up_files()
        tl.put(self.g / "Bin/engine.dll", b"engind")
        res = gamecheck.check_game(self.g, self.manifest)
        self.assertFalse(res["ok"])
        self.assertEqual(res["failures"], ["changed: Bin/engine.dll"])

    def test_a_third_exe_is_still_refused(self):
        tl.put(self.g / "vampire.exe", b"some other exe")
        self.assertFalse(gamecheck.check_game(self.g, self.manifest)["ok"])



class PinnedHashes(unittest.TestCase):
    def test_pinned_md5_matches_the_tool_and_the_spec(self):
        bt, _ = up.load()
        self.assertEqual(up.MD5, bt.KNOWN_MD5)
        self.assertEqual(up.KNOWN, {up.MD5: up.NAME})

    def test_pinned_hashes_are_the_up_installers_files(self):
        rows = {}
        for l in (kitconst.REPO / "tools/up/manifests/up120.8-basic.tsv").read_text().splitlines():
            n, size, digest = l.split("\t")
            rows[n] = digest
        self.assertEqual(gamecheck.UP_REPLACED["vampire.exe"], rows["app/Vampire.exe"])
        self.assertEqual(gamecheck.UP_REPLACED["bin/binkw32.dll"], rows["app/Bin/binkw32.dll"])


class NothingFromTheUpShips(unittest.TestCase):
    def test_repo_manifest_parses_and_is_big(self):
        h = kit_app.up_hashes()
        self.assertGreater(len(h), 100)
        self.assertTrue(all(len(k) == 64 for k in h))

    def test_up_file_in_the_app_is_caught_by_hash(self):
        with tempfile.TemporaryDirectory() as t:
            app = Path(t) / "A.app"
            tl.put(app / "Contents/Resources/clean.txt", b"ours")
            tl.put(app / "Contents/Resources/sneaky.bin", b"theirs")
            hashes = {hashlib.sha256(b"theirs").hexdigest(): ("app/Bin/x.vtm", 6)}
            errs = kit_app.up_file_errors(app, hashes)
            self.assertEqual(errs, ["Contents/Resources/sneaky.bin is Unofficial Patch file app/Bin/x.vtm"])
            self.assertEqual(kit_app.up_file_errors(app, {"0" * 64: ("n", 4)}), [])

    def test_up_files_are_allowed_only_in_the_apps_game_data(self):
        with tempfile.TemporaryDirectory() as t:
            app = Path(t) / "A.app"
            tl.put(app / kitconst.GAME_DIR / "Bin/x.vtm", b"theirs")
            hashes = {hashlib.sha256(b"theirs").hexdigest(): ("app/Bin/x.vtm", 6)}
            self.assertEqual(kit_app.up_file_errors(app, hashes), [])
            tl.put(app / "Contents/Resources/kit/x.vtm", b"theirs")
            self.assertEqual(kit_app.up_file_errors(app, hashes), ["Contents/Resources/kit/x.vtm is Unofficial Patch file app/Bin/x.vtm"])

    def test_renamed_up_file_is_still_caught(self):
        with tempfile.TemporaryDirectory() as t:
            app = Path(t) / "A.app"
            tl.put(app / "Contents/a/innocent.dat", b"theirs")
            hashes = {hashlib.sha256(b"theirs").hexdigest(): ("n", 6)}
            self.assertEqual(len(kit_app.up_file_errors(app, hashes)), 1)

    def test_the_kit_bundles_no_up_file(self):
        hashes = kit_app.up_hashes()
        for d in (Path(__file__).resolve().parent, kitconst.REPO / "tools" / "up"):
            for n in list(kitconst.KIT_FILES) + list(kitconst.UP_TOOLS):
                p = d / n
                if p.is_file():
                    self.assertNotIn(hashlib.sha256(p.read_bytes()).hexdigest(), hashes, n)

    def test_the_bundled_tool_files_exist_in_the_repo(self):
        for n in kitconst.UP_TOOLS:
            self.assertTrue((kit_app.UP_TOOLS_DIR / n).is_file(), n)
        self.assertTrue((kit_app.UP_TOOLS_DIR / "manifests" / kitconst.UP_MANIFEST).is_file())
        self.assertIn("up.py", kitconst.KIT_FILES)


class PatchedExecutable(unittest.TestCase):
    def app(self, up_text=None, vanilla_text=None, version=None):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        contents = Path(t.name) / "A.app/Contents"
        for name, text in ((kitconst.EXE_NAME, vanilla_text), (kitconst.UP_EXE_NAME, up_text)):
            if text is not None:
                p = tl.put(contents / "MacOS" / name, "#!/bin/sh\n%s\n" % text)
                p.chmod(0o755)
        if version is not None:
            tl.put(contents / "Resources/kit-version.txt", version)
        return contents

    def test_no_patched_executable_is_fine(self):
        self.assertEqual(kit_app.up_exe_errors(self.app(vanilla_text="echo hi")), [])

    def test_matching_build_sha_passes(self):
        c = self.app("echo 'build sha256 %s'" % HEX, "echo 'build sha256 none'", "kit-commit a\nup-build-sha256 %s\n" % HEX)
        self.assertEqual(kit_app.up_exe_errors(c), [])

    def test_wrong_sha_fails(self):
        c = self.app("echo 'build sha256 %s'" % ("cd" * 32), "echo 'build sha256 none'", "up-build-sha256 %s\n" % HEX)
        self.assertEqual(len(kit_app.up_exe_errors(c)), 1)

    def test_vanilla_executable_must_report_none(self):
        c = self.app("echo 'build sha256 %s'" % HEX, "echo 'build sha256 %s'" % HEX, "up-build-sha256 %s\n" % HEX)
        errs = kit_app.up_exe_errors(c)
        self.assertEqual(len(errs), 1)
        self.assertIn(kitconst.EXE_NAME, errs[0])

    def test_missing_version_line_fails(self):
        c = self.app("echo 'build sha256 %s'" % HEX, "echo 'build sha256 none'", "kit-commit a\n")
        self.assertIn("no valid up-build-sha256", kit_app.up_exe_errors(c)[0])

    def test_version_line_without_the_executable_fails(self):
        c = self.app(None, "echo hi", "up-build-sha256 %s\n" % HEX)
        self.assertIn("is missing", kit_app.up_exe_errors(c)[0])

    def test_failing_executable_fails(self):
        c = self.app("exit 3", "echo 'build sha256 none'", "up-build-sha256 %s\n" % HEX)
        self.assertEqual(len(kit_app.up_exe_errors(c)), 1)


if __name__ == "__main__":
    unittest.main()
