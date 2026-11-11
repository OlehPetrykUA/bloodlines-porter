#!/usr/bin/env python3
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_app
from kitconst import Fail

CONF = "--prefix=/x --disable-gpl --disable-nonfree --disable-version3 --enable-shared"
BREW_FF = "/opt/homebrew/opt/ffmpeg/lib/"


def cc(out, src, *flags):
    subprocess.run(["cc", "-o", str(out), *flags, "-x", "c", "-"], input=src, text=True, check=True, capture_output=True)


def fake_lib(libdir, name, lic="LGPL version 2.1 or later", conf=CONF):
    stem = name.split(".")[0]
    (libdir / name).write_bytes(b"\0".join([b"MZ", ("%s license: %s" % (stem, lic)).encode(), conf.encode(), b""]))


def real_lib(libdir, name):
    stem = name.split(".")[0]
    cc(libdir / name, "int f_%s(void){return 1;}\n" % stem, "-dynamiclib", "-install_name", "@rpath/" + name)


def lgpl_dir(root, **kw):
    lib = Path(root) / "lib"
    lib.mkdir()
    for n in kit_app.LGPL_LIBS:
        fake_lib(lib, n, **kw.get(n.split(".")[0], {}))
    (Path(root) / kit_app.LGPL_COPYING).write_text("LGPL text\n")
    return lib


class Info(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)

    def test_missing_dir_is_a_clear_error(self):
        with self.assertRaisesRegex(Fail, "LGPL FFmpeg folder .* not found.*build_lgpl.sh.*--ffmpeg-lgpl"):
            kit_app.lgpl_info(self.root / "nope")

    def test_missing_library(self):
        lib = lgpl_dir(self.root)
        (lib / "libavcodec.62.dylib").unlink()
        with self.assertRaisesRegex(Fail, "lacks libavcodec.62.dylib"):
            kit_app.lgpl_info(lib)

    def test_good_dir_returns_configure_and_copying(self):
        conf, copying = kit_app.lgpl_info(lgpl_dir(self.root))
        self.assertEqual(conf, CONF)
        self.assertEqual(copying.name, kit_app.LGPL_COPYING)

    def test_gpl_licence_string_refused(self):
        lib = lgpl_dir(self.root, libavutil={"lic": "GPL version 3 or later"})
        with self.assertRaisesRegex(Fail, "reports licence .*GPL version 3 or later.*refusing"):
            kit_app.lgpl_info(lib)

    def test_gpl_configure_flag_refused(self):
        lib = lgpl_dir(self.root, libavcodec={"conf": "--prefix=/x --enable-gpl --enable-version3"})
        with self.assertRaisesRegex(Fail, "--enable-gpl --enable-version3"):
            kit_app.lgpl_info(lib)

    def test_missing_copying_text(self):
        lib = lgpl_dir(self.root)
        (self.root / kit_app.LGPL_COPYING).unlink()
        with self.assertRaisesRegex(Fail, "COPYING.LGPLv2.1"):
            kit_app.lgpl_info(lib)


class Names(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.lib = Path(cls.tmp.name) / "lib"
        cls.lib.mkdir()
        real_lib(cls.lib, "libavformat.62.dylib")
        cls.exe0 = Path(cls.tmp.name) / "exe0"
        cc(cls.exe0, "int f_libavformat(void);\nint main(void){return f_libavformat();}\n", str(cls.lib / "libavformat.62.dylib"), "-Wl,-headerpad_max_install_names")
        subprocess.run(["install_name_tool", "-change", "@rpath/libavformat.62.dylib", BREW_FF + "libavformat.62.dylib", str(cls.exe0)], check=True)

    def setUp(self):
        self.exe = Path(self.tmp.name) / "exe"
        shutil.copyfile(self.exe0, self.exe)
        self.exe.chmod(0o755)

    def test_rewrite_turns_homebrew_names_into_rpath(self):
        self.assertIn(BREW_FF + "libavformat.62.dylib", kit_app.deps(self.exe))
        kit_app.rewrite(self.exe, False)
        d = kit_app.deps(self.exe)
        self.assertIn("@rpath/libavformat.62.dylib", d)
        self.assertFalse([x for x in d if "/opt/homebrew" in x])

    def test_closure_takes_libav_from_lgpl_dir(self):
        libs = kit_app.closure([self.exe], False, self.lib)
        self.assertEqual(libs["libavformat.62.dylib"], (self.lib / "libavformat.62.dylib").resolve())

    def test_closure_without_lgpl_dir_still_refuses(self):
        with self.assertRaisesRegex(Fail, "only the LGPL build"):
            kit_app.closure([self.exe], False)

    def test_closure_refuses_swscale(self):
        with mock.patch.object(kit_app, "deps", lambda p: [BREW_FF + "libswscale.9.dylib"]):
            with self.assertRaisesRegex(Fail, "only the LGPL build"):
                kit_app.closure(["root"], False, self.lib)


class Notices(unittest.TestCase):
    def bundle(self, notice=True, copying=True, lib_lic="LGPL version 2.1 or later", eula=True, copyright=True):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        contents = Path(t.name) / "A.app/Contents"
        fw, res = contents / "Frameworks", contents / "Resources"
        fw.mkdir(parents=True)
        (res / "licenses/ffmpeg").mkdir(parents=True)
        n = "libavformat.62.dylib"
        fake_lib(fw, n, lic=lib_lic)
        if copying:
            (res / "licenses/ffmpeg" / kit_app.LGPL_COPYING).write_text("LGPL text\n")
        else:
            (res / "licenses/ffmpeg/other").write_text("x")
        row = "| `%s` | ffmpeg | 8.1.2 | LGPL-2.1-or-later (configure line in %s) | licenses/ffmpeg/ |\n" % (n, kit_app.LGPL_NOTICE)
        (res / "NOTICE.md").write_text(row)
        if notice:
            (res / kit_app.LGPL_NOTICE).write_text(kit_app.lgpl_notice(CONF))
        if eula:
            (res / kit_app.EULA_NAME).write_text(kit_app.EULA_TEXT)
        return contents, {kit_app.COPYRIGHT_KEY: kit_app.COPYRIGHT} if copyright else {}, [fw / n]

    def test_complete_lgpl_bundle_passes(self):
        self.assertEqual(kit_app.licence_errors(*self.bundle()), [])

    def test_missing_eula_fails(self):
        errs = kit_app.licence_errors(*self.bundle(eula=False))
        self.assertTrue(any(kit_app.EULA_NAME in e for e in errs))

    def test_eula_that_drops_the_lgpl_line_fails(self):
        contents, plist, files = self.bundle()
        (contents / "Resources" / kit_app.EULA_NAME).write_text("nothing here\n")
        self.assertTrue(any(kit_app.EULA_NAME in e for e in kit_app.licence_errors(contents, plist, files)))

    def test_missing_about_box_copyright_fails(self):
        errs = kit_app.licence_errors(*self.bundle(copyright=False))
        self.assertTrue(any(kit_app.COPYRIGHT_KEY in e for e in errs))

    def test_copyright_line_is_the_ffmpeg_sentence(self):
        self.assertEqual(kit_app.COPYRIGHT, "This software uses libraries from the FFmpeg project under the LGPLv2.1.")

    def test_notice_names_the_source_copy_in_the_kit(self):
        self.assertIn("Source copy: sources/ffmpeg-8.1.2.tar.xz in the kit", kit_app.lgpl_notice(CONF))

    def test_notice_without_the_source_copy_line_fails(self):
        contents, plist, files = self.bundle()
        p = contents / "Resources" / kit_app.LGPL_NOTICE
        p.write_text(p.read_text().replace("Source copy:", "Source:"))
        self.assertTrue(any("source copy" in e for e in kit_app.licence_errors(contents, plist, files)))

    def test_personal_build_needs_neither_eula_nor_copyright(self):
        contents, plist, files = self.bundle(eula=False, copyright=False)
        self.assertNotIn(kit_app.EULA_NAME, " ".join(kit_app.licence_errors(contents, {kit_app.PERSONAL_KEY: True}, files)))

    def test_notice_text_has_source_sha_configure_changes(self):
        t = kit_app.lgpl_notice(CONF)
        for s in (kit_app.FFMPEG_SRC_URL, kit_app.FFMPEG_SRC_SHA256, "Configure line: " + CONF, "Changes to the FFmpeg source: none", kit_app.FFMPEG_LEGAL):
            self.assertIn(s, t)

    def test_missing_notice_file_fails(self):
        errs = kit_app.licence_errors(*self.bundle(notice=False))
        self.assertTrue(any(kit_app.LGPL_NOTICE in e for e in errs))

    def test_missing_copying_fails(self):
        errs = kit_app.licence_errors(*self.bundle(copying=False))
        self.assertTrue(any(kit_app.LGPL_COPYING in e for e in errs))

    def test_gpl_dylib_in_bundle_fails(self):
        errs = kit_app.licence_errors(*self.bundle(lib_lic="GPL version 3 or later"))
        self.assertTrue(any("GPL version 3 or later" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
