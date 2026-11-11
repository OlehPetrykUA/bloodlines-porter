#!/usr/bin/env python3
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_app
from kitconst import APP_NAME, EXE_NAME, ICD_NAME, Fail

SHA = "a" * 40
CONF = "--prefix=/ffmpeg-lgpl --disable-gpl --disable-nonfree --enable-shared"
LIBS = {
    "libSDL3.0.dylib": ("sdl3", "3.4.0", "Zlib"),
    "libvulkan.1.dylib": ("vulkan-loader", "1.4", "Apache-2.0"),
    "libvulkan_kosmickrisp.dylib": ("kosmickrisp", "26.0", "MIT"),
    "libdxvk_d3d9.0.dylib": ("dxvk", "dd3a05b", "Zlib"),
    "libavformat.62.dylib": ("ffmpeg", "8.1.2", "LGPL-2.1-or-later"),
    "libavcodec.62.dylib": ("ffmpeg", "8.1.2", "LGPL-2.1-or-later"),
    "libavutil.60.dylib": ("ffmpeg", "8.1.2", "LGPL-2.1-or-later"),
}


def cc(out, src, *flags):
    subprocess.run(["cc", "-o", str(out), *flags, "-x", "c", "-"], input=src, text=True, check=True, capture_output=True)


def dylib(path, i):
    name = Path(path).name
    text = ""
    if name.startswith("libav"):
        text = 'const char lic[] = "%s license: LGPL version 2.1 or later"; const char conf[] = "%s";\n' % (name.split(".")[0], CONF)
    cc(path, "int f_%d(void){return %d;}\n%s" % (i, i, text), "-dynamiclib", "-install_name", "@rpath/" + name)


def make_kit(root, sha=SHA):
    kit = Path(root) / "kit"
    pre = kit / "prebuilt"
    (pre / "lib").mkdir(parents=True)
    (pre / "icd").mkdir()
    (pre / "icd" / ICD_NAME).write_text(json.dumps({"ICD": {"library_path": "x"}}))
    (kit / "SOURCE").write_text(sha + "\n")
    manifest = {}
    for i, (name, (comp, ver, spdx)) in enumerate(sorted(LIBS.items())):
        dylib(pre / "lib" / name, i)
        manifest["lib/" + name] = {"component": comp, "version": ver, "spdx": spdx, "licence": "licenses/" + comp}
        d = kit / "licenses" / comp
        d.mkdir(parents=True, exist_ok=True)
        (d / "LICENSE").write_text("text of %s\n" % comp)
    (kit / "licenses/ffmpeg" / kit_app.LGPL_COPYING).write_text("LGPL text\n")
    (pre / "manifest.json").write_text(json.dumps(manifest))
    return kit


def make_build(root, source=None):
    build = Path(root) / "work"
    (build / "meson").mkdir(parents=True)
    summary = {"state": "pass"}
    if source:
        summary["source"] = {"sha": source}
    (build / "summary.json").write_text(json.dumps(summary))
    pre = Path(root) / "kit/prebuilt/lib"
    libs = [str(pre / "libSDL3.0.dylib"), str(pre / "libavformat.62.dylib")]
    cc(build / "meson" / EXE_NAME, "int f_0(void);int f_2(void);int main(void){return f_0()+f_2();}\n", *libs, "-Wl,-headerpad_max_install_names")
    return build


def args(build, out, kit, **kw):
    return Namespace(build=str(build), out=str(out), dxvk=None, dxvk_src="/nonexistent", vulkan_loader="/nonexistent", kosmickrisp="/nonexistent",
                     icd="/nonexistent", up_build=None, ffmpeg_lgpl="/nonexistent", no_sign=True, personal=False, prebuilt=str(kit / "prebuilt"), **kw)


class Prebuilt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.kit = make_kit(cls.root)
        cls.build = make_build(cls.root)
        cls.out = cls.root / "out"
        cls.app = kit_app.build(args(cls.build, cls.out, cls.kit))
        cls.contents = cls.app / "Contents"

    def test_libraries_come_from_the_kit_prebuilt_folder(self):
        self.assertEqual(sorted(p.name for p in (self.contents / "Frameworks").iterdir()), sorted(LIBS))

    def test_exe_and_libs_load_through_rpath_only(self):
        for p in [self.contents / "MacOS" / EXE_NAME] + list((self.contents / "Frameworks").iterdir()):
            self.assertFalse([d for d in kit_app.deps(p) if not d.startswith(kit_app.OK_PREFIXES)], p.name)

    def test_licences_are_copied_from_the_kit_licence_folders(self):
        self.assertEqual((self.contents / "Resources/licenses/sdl3/LICENSE").read_text(), "text of sdl3\n")
        self.assertTrue((self.contents / "Resources/licenses/ffmpeg" / kit_app.LGPL_COPYING).is_file())

    def test_licence_check_passes_on_the_built_app(self):
        plist = plistlib.loads((self.contents / "Info.plist").read_bytes())
        files = [p for p in (self.contents / "Frameworks").iterdir()]
        self.assertEqual(kit_app.licence_errors(self.contents, plist, files), [])

    def test_eula_and_about_box_line_are_in_the_app(self):
        self.assertEqual((self.contents / "Resources" / kit_app.EULA_NAME).read_text(), kit_app.EULA_TEXT)
        plist = plistlib.loads((self.contents / "Info.plist").read_bytes())
        self.assertEqual(plist[kit_app.COPYRIGHT_KEY], kit_app.COPYRIGHT)

    def test_version_file_names_the_kit_commit_not_a_git_head(self):
        self.assertIn("kit-commit " + SHA, (self.contents / "Resources/kit-version.txt").read_text())

    def test_icd_manifest_points_into_frameworks(self):
        icd = json.loads((self.contents / "Resources" / ICD_NAME).read_text())
        self.assertEqual(icd["ICD"]["library_path"], kit_app.ICD_REL)


class SourceSha(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.kit = make_kit(self.root)

    def test_a_build_from_another_commit_is_refused(self):
        build = make_build(self.root, source="b" * 40)
        with self.assertRaisesRegex(Fail, "build is stale: built from b+, sources are a+; run the build again"):
            kit_app.build(args(build, self.root / "out", self.kit))

    def test_a_build_from_this_commit_is_packaged(self):
        build = make_build(self.root, source=SHA)
        self.assertTrue(kit_app.build(args(build, self.root / "out", self.kit)).name == APP_NAME)


if __name__ == "__main__":
    unittest.main()
