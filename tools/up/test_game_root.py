import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import game_root as gr

REPO = os.path.dirname(os.path.dirname(HERE))

G_FILES = {
    "vampire.exe": b"MZ" + b"\0" * 62,
    "Bin/launcher.dll": b"launcher",
    "Bin/engine.dll": b"engine",
    "Vampire/pack000.vpk": b"vpk0",
    "Vampire/cfg/config.cfg": b"base config",
    "Vampire/python/vampire.py": b"base py",
    "version.inf": b"1.2",
}

U_FILES = {
    "app/Unofficial_Patch/resource/trackerscheme.res": b"mod scheme",
    "app/Unofficial_Patch/resource/gameui_english.txt": b"mod text",
    "app/Unofficial_Patch/python/lib/fileutil.py": b"mod fileutil",
    "app/Unofficial_Patch/cfg/autoexec.cfg": b"mod autoexec",
    "app/Vampire/maps/graphs/a.ain": b"graph a",
    "app/Vampire/maps/graphs/b.ain": b"graph b",
    "app/Vampire/maps/graphs/readme.txt": b"not a graph",
    "app/Bin/loader/loader.ini": b"[Mods]\nFix sys.path = 1\n",
    "app/Bin/loader/x.vtm": b"vtm",
    "app/Bin/loader/x.ini": b"plugin ini",
    "app/Bin/loader/x.txt": b"plugin notes",
    "app/Bin/loader/.DS_Store": b"finder",
    "app/Bin/loader/._x.vtm": b"finder",
    "app/Unofficial_Patch/cfg/loader.ini": b"[Mods]\nFix sys.path = 1\n",
}


def put(root, files):
    for rel, data in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)


def tree_state(root):
    out = {}
    for p in gr.files_under(root):
        with open(p, "rb") as f:
            out[os.path.relpath(p, root)] = (hashlib.sha256(f.read()).hexdigest(), os.stat(p).st_mode)
    return out


def quiet(*a):
    pass


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-game-root-")
        self.addCleanup(self.clean)
        self.g = os.path.join(self.tmp, "G")
        self.u = os.path.join(self.tmp, "U")
        self.gu = os.path.join(self.tmp, "GU")
        put(self.g, G_FILES)
        put(self.u, U_FILES)

    def clean(self):
        gr.unlock(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **kw):
        kw.setdefault("log", quiet)
        return gr.build(self.g, self.u, self.gu, **kw)

    def gu_path(self, rel):
        return os.path.join(self.gu, *rel.split("/"))


class Build(Case):
    def test_lays_mod_graphs_and_ini(self):
        self.assertEqual(self.build(), "built")
        gr.verify(self.g, self.u, self.gu, deep=True)
        for rel, data in U_FILES.items():
            if rel.startswith("app/Unofficial_Patch/") or rel.endswith(".ain") or rel.endswith("loader.ini"):
                with open(self.gu_path(rel[4:]), "rb") as f:
                    self.assertEqual(f.read(), data, rel)
        self.assertFalse(os.path.exists(self.gu_path("Vampire/maps/graphs/readme.txt")))

    def test_plugin_vtm_and_ini_files_are_laid_and_finder_files_are_not(self):
        self.build()
        for rel in ("Bin/loader/x.vtm", "Bin/loader/x.ini", "Bin/loader/loader.ini"):
            self.assertTrue(os.path.isfile(self.gu_path(rel)), rel)
        with open(self.gu_path("Bin/loader/x.vtm"), "rb") as f:
            self.assertEqual(f.read(), b"vtm")
        for rel in ("Bin/loader/x.txt", "Bin/loader/.DS_Store", "Bin/loader/._x.vtm"):
            self.assertFalse(os.path.lexists(self.gu_path(rel)), rel)

    def test_a_changed_plugin_file_rebuilds(self):
        self.build()
        with open(os.path.join(self.u, "app", "Bin", "loader", "x.vtm"), "wb") as f:
            f.write(b"vtm 2")
        self.assertEqual(self.build(), "built")
        with open(self.gu_path("Bin/loader/x.vtm"), "rb") as f:
            self.assertEqual(f.read(), b"vtm 2")

    def test_base_files_stay_and_g_is_unchanged(self):
        before = tree_state(self.g)
        self.build()
        self.assertEqual(tree_state(self.g), before)
        for rel, data in G_FILES.items():
            with open(self.gu_path(rel), "rb") as f:
                self.assertEqual(f.read(), data, rel)

    def test_gu_is_read_only(self):
        self.build()
        with self.assertRaises(PermissionError):
            open(self.gu_path("Unofficial_Patch/cfg/autoexec.cfg"), "ab")
        with self.assertRaises(PermissionError):
            open(self.gu_path("Unofficial_Patch/new.txt"), "wb")
        self.assertIsNone(gr.writable_anywhere(self.gu))

    def test_second_build_is_current_and_a_changed_tree_rebuilds(self):
        self.build()
        self.assertEqual(self.build(), "current")
        put(self.u, {"app/Vampire/maps/graphs/a.ain": b"graph a v2"})
        self.assertEqual(self.build(), "built")
        with open(self.gu_path("Vampire/maps/graphs/a.ain"), "rb") as f:
            self.assertEqual(f.read(), b"graph a v2")
        self.assertEqual(self.build(force=True), "built")

    def test_manifest_hash_decides_when_present(self):
        put(self.u, {"app/manifest.tsv": b"x\t1\tabc\n"})
        self.build()
        self.assertEqual(self.build(), "current")
        put(self.u, {"app/manifest.tsv": b"x\t1\tabd\n"})
        self.assertEqual(self.build(), "built")

    def test_windows_patched_g_gets_the_u_mod(self):
        put(self.g, {"Unofficial_Patch/resource/trackerscheme.res": b"old scheme", "Unofficial_Patch/old_only.txt": b"old"})
        self.build()
        with open(self.gu_path("Unofficial_Patch/resource/trackerscheme.res"), "rb") as f:
            self.assertEqual(f.read(), b"mod scheme")
        self.assertFalse(os.path.exists(self.gu_path("Unofficial_Patch/old_only.txt")))

    def test_lowercase_base_folder_gets_no_second_copy(self):
        shutil.rmtree(self.g)
        put(self.g, {"vampire.exe": b"MZ", "vampire/maps/pack.bsp": b"b"})
        self.build()
        names = [n for n in os.listdir(self.gu) if n.lower() == "vampire"]
        self.assertEqual(len(names), 1)
        self.assertTrue(os.path.exists(os.path.join(self.gu, names[0], "maps", "graphs", "a.ain")))
        self.assertTrue(os.path.exists(os.path.join(self.gu, names[0], "maps", "pack.bsp")))

    def test_up_root_may_be_the_app_folder_itself(self):
        self.u = os.path.join(self.u, "app")
        self.build()
        gr.verify(self.g, self.u, self.gu)

    def test_no_clone_warns_and_still_builds(self):
        logs = []
        self.build(cloner=lambda p: False, log=logs.append)
        self.assertTrue(any("full copy" in l for l in logs), logs)
        gr.verify(self.g, self.u, self.gu, deep=True)

    def test_failed_build_leaves_no_tmp_and_no_gu(self):
        def boom(g, u, tmp):
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.build(layer=boom)
        self.assertFalse(os.path.lexists(self.gu))
        self.assertFalse(os.path.lexists(self.gu + ".tmp"))


class NoPluginUi(Case):
    VIDEO = (b'"Resource\\OptionsSubVideo.res"\r\n{\r\n\t"Note"\r\n\t{\r\n\t\t"xpos" "1"\r\n\t}\r\n'
             b'\t"DisplayMode"\t// added\r\n\t{\r\n\t\t"xpos" "40"\r\n\t}\r\n'
             b'\t"FOV Slider"\r\n\t{\r\n\t\t"inner"\r\n\t\t{\r\n\t\t}\r\n\t}\r\n}\r\n')
    AUDIO = (b'"a"\r\n{\r\n\t"Note"\t// added\r\n\t{\r\n\t}\r\n\t"Captions"\r\n\t{\r\n\t}\r\n'
             b'\t"SFX Slider"\r\n\t{\r\n\t}\r\n\t"Sound Slider"\r\n\t{\r\n\t}\r\n\t"Music Slider"\r\n\t{\r\n\t}\r\n}\r\n')
    KB = b'"+attack"\t"Attack"\r\n"+ironsights"\t\t"Iron Sights"\t// x\r\n"+jump"\t"Jump"\r\n'

    def setUp(self):
        super().setUp()
        put(self.u, {"app/Unofficial_Patch/resource/optionssubvideo.res": self.VIDEO,
                     "app/Unofficial_Patch/resource/optionssubaudio.res": self.AUDIO,
                     "app/Unofficial_Patch/scripts/kb_act - vampire.lst": self.KB})

    def read(self, rel):
        with open(self.gu_path(rel), "rb") as f:
            return f.read()

    def test_controls_of_absent_plugins_are_dropped_and_the_rest_stays(self):
        self.build()
        video = self.read("Unofficial_Patch/resource/optionssubvideo.res")
        self.assertEqual(video, b'"Resource\\OptionsSubVideo.res"\r\n{\r\n\t"Note"\r\n\t{\r\n\t\t"xpos" "1"\r\n\t}\r\n}\r\n')
        audio = self.read("Unofficial_Patch/resource/optionssubaudio.res")
        self.assertEqual(audio, b'"a"\r\n{\r\n\t"Captions"\r\n\t{\r\n\t}\r\n\t"SFX Slider"\r\n\t{\r\n\t}\r\n}\r\n')
        self.assertEqual(self.read("Unofficial_Patch/scripts/kb_act - vampire.lst"), b'"+attack"\t"Attack"\r\n"+jump"\t"Jump"\r\n')
        gr.verify(self.g, self.u, self.gu, deep=True)

    def test_source_tree_is_not_changed(self):
        before = tree_state(self.u)
        self.build()
        self.assertEqual(tree_state(self.u), before)

    def test_verify_fails_on_an_unfiltered_copy(self):
        self.build()
        p = self.gu_path("Unofficial_Patch/resource/optionssubvideo.res")
        gr.unlock(self.gu)
        with open(p, "wb") as f:
            f.write(self.VIDEO)
        gr.lock(self.gu)
        with self.assertRaises(gr.GameRootError) as c:
            gr.verify(self.g, self.u, self.gu)
        self.assertIn("mod file differs: resource/optionssubvideo.res", str(c.exception))


class Refusals(Case):
    def test_output_inside_the_repo_is_refused(self):
        with self.assertRaises(gr.GameRootError) as c:
            gr.build(self.g, self.u, os.path.join(REPO, "gu-test-out"), log=quiet)
        self.assertIn("outside the repo", str(c.exception))
        self.assertFalse(os.path.exists(os.path.join(REPO, "gu-test-out")))

    def test_output_inside_or_equal_to_g_or_u_is_refused(self):
        for out in (self.g, os.path.join(self.g, "sub"), os.path.join(self.u, "app", "sub"), self.u):
            with self.assertRaises(gr.GameRootError, msg=out):
                gr.build(self.g, self.u, out, log=quiet)

    def test_existing_foreign_folder_is_refused_and_kept(self):
        put(self.gu, {"mine.txt": b"mine"})
        with self.assertRaises(gr.GameRootError):
            self.build()
        self.assertTrue(os.path.exists(os.path.join(self.gu, "mine.txt")))

    def test_leftover_tmp_is_refused(self):
        os.makedirs(self.gu + ".tmp")
        with self.assertRaises(gr.GameRootError):
            self.build()

    def test_bad_inputs_are_refused(self):
        with self.assertRaises(gr.GameRootError):
            gr.build(os.path.join(self.tmp, "nope"), self.u, self.gu, log=quiet)
        os.remove(os.path.join(self.g, "vampire.exe"))
        with self.assertRaises(gr.GameRootError):
            self.build()
        put(self.g, {"vampire.exe": b"MZ"})
        shutil.rmtree(os.path.join(self.u, "app", "Vampire"))
        with self.assertRaises(gr.GameRootError):
            self.build()
        shutil.rmtree(os.path.join(self.u, "app", "Unofficial_Patch"))
        with self.assertRaises(gr.GameRootError):
            self.build()

    def test_u_without_loader_ini_is_refused(self):
        os.remove(os.path.join(self.u, "app", "Bin", "loader", "loader.ini"))
        with self.assertRaises(gr.GameRootError):
            self.build()
        self.assertFalse(os.path.lexists(self.gu))


class Mutations(Case):
    def setUp(self):
        super().setUp()
        self.build()

    def fails(self, text, deep=True):
        with self.assertRaises(gr.GameRootError) as c:
            gr.verify(self.g, self.u, self.gu, deep=deep)
        self.assertIn(text, str(c.exception))

    def edit(self, rel, data=None):
        p = self.gu_path(rel)
        gr.unlock(self.gu)
        if data is None:
            os.remove(p)
        else:
            with open(p, "wb") as f:
                f.write(data)
        gr.lock(self.gu)

    def test_clean_root_verifies(self):
        gr.verify(self.g, self.u, self.gu, deep=True)

    def test_finder_metadata_is_ignored(self):
        for base in (self.u, self.g):
            for sub in ("", "app/Unofficial_Patch/cfg"):
                d = os.path.join(base, *sub.split("/"))
                if os.path.isdir(d):
                    for n in (".DS_Store", "._foo", ".localized"):
                        with open(os.path.join(d, n), "wb") as f:
                            f.write(b"x")
        gr.verify(self.g, self.u, self.gu, deep=True)

    def test_m1_mod_file_missing(self):
        self.edit("Unofficial_Patch/resource/trackerscheme.res")
        self.fails("mod file missing: resource/trackerscheme.res")

    def test_m1_mod_folder_missing(self):
        gr.unlock(self.gu)
        shutil.rmtree(self.gu_path("Unofficial_Patch"))
        gr.lock(self.gu)
        self.fails("mod folder")

    def test_m2_base_trackerscheme_instead_of_the_mod_one(self):
        self.edit("Unofficial_Patch/resource/trackerscheme.res", b"base scheme!")
        self.fails("mod file differs: resource/trackerscheme.res")

    def test_m2_same_size_other_content_needs_deep(self):
        self.edit("Unofficial_Patch/resource/trackerscheme.res", b"mod SCHEME")
        gr.verify(self.g, self.u, self.gu, deep=False)
        self.fails("mod file differs", deep=True)

    def test_m3_graph_missing(self):
        self.edit("Vampire/maps/graphs/b.ain")
        self.fails("graph missing or differs: b.ain")

    def test_m3_graph_content_differs(self):
        self.edit("Vampire/maps/graphs/a.ain", b"other")
        self.fails("graph missing or differs: a.ain")

    def test_m4_loader_ini_missing(self):
        self.edit("Bin/loader/loader.ini")
        self.fails("loader.ini")

    def test_m4_loader_ini_differs(self):
        self.edit("Bin/loader/loader.ini", b"[Mods]\nFix sys.path = 0\n")
        self.fails("loader.ini")

    def test_m4_plugin_vtm_missing(self):
        self.edit("Bin/loader/x.vtm")
        self.fails("loader file missing or differs: x.vtm")

    def test_m4_plugin_ini_differs(self):
        self.edit("Bin/loader/x.ini", b"other")
        self.fails("loader file missing or differs: x.ini")

    def test_m5_not_read_only(self):
        gr.unlock(self.gu)
        self.fails("not read-only")

    def test_m5_one_writable_file_is_enough(self):
        p = self.gu_path("Unofficial_Patch/cfg/autoexec.cfg")
        os.chmod(p, 0o644)
        self.fails("not read-only")

    def test_m6_base_file_missing(self):
        self.edit("version.inf")
        self.fails("game file missing: version.inf")

    def test_m7_stamp_missing(self):
        self.edit(gr.STAMP)
        self.fails("stamp")

    def test_m8_build_without_chmod_is_caught(self):
        gr.unlock(self.gu)
        shutil.rmtree(self.gu)
        with mock.patch.object(gr, "lock", lambda p: None):
            self.build()
        self.fails("not read-only")

    def test_m9_build_without_mod_is_caught(self):
        gr.unlock(self.gu)
        shutil.rmtree(self.gu)
        self.build(layer=lambda g, u, t: None)
        self.fails("mod folder")


if __name__ == "__main__":
    unittest.main()
