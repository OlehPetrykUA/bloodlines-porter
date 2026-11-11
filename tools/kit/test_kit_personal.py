#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kit_app
from kitconst import Fail

FF = "/opt/homebrew/opt/ffmpeg/lib/libavformat.62.dylib"
GOOD_NOTICE = "# Personal build - GPLv3 FFmpeg\nHomebrew configuration: --enable-gpl --enable-version3\n"


def fake_deps(p):
    return [FF] if str(p) == "root" else []


class Closure(unittest.TestCase):
    def test_default_refuses_ffmpeg(self):
        with mock.patch.object(kit_app, "deps", fake_deps):
            with self.assertRaisesRegex(Fail, "only the LGPL build"):
                kit_app.closure(["root"])

    def test_personal_allows_ffmpeg(self):
        with mock.patch.object(kit_app, "deps", fake_deps):
            self.assertIn("libavformat.62.dylib", kit_app.closure(["root"], True))


class Licences(unittest.TestCase):
    def bundle(self, notice, flag, libs=("libavformat.62.dylib",)):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        contents = Path(t.name) / "A.app/Contents"
        (contents / "Frameworks").mkdir(parents=True)
        res = contents / "Resources"
        (res / "licenses/ffmpeg").mkdir(parents=True)
        (res / "licenses/ffmpeg/COPYING").write_text("x")
        rows = "\n".join("| `%s` | ffmpeg | 8 | GPL-2.0-or-later | licenses/ffmpeg/ |" % n for n in libs)
        (res / "NOTICE.md").write_text(rows + "\n")
        files = []
        for n in libs:
            (contents / "Frameworks" / n).write_text("x")
            files.append(contents / "Frameworks" / n)
        if notice is not None:
            (res / kit_app.PERSONAL_NOTICE).write_text(notice)
        return contents, ({kit_app.PERSONAL_KEY: True} if flag else {}), files

    def test_personal_with_key_and_notice_passes(self):
        self.assertEqual(kit_app.licence_errors(*self.bundle(GOOD_NOTICE, True)), [])

    def test_unflagged_ffmpeg_refused(self):
        errs = kit_app.licence_errors(*self.bundle(GOOD_NOTICE, False))
        self.assertTrue(any(kit_app.LGPL_NOTICE in e for e in errs))
        self.assertTrue(any("configure line without --enable-gpl" in e for e in errs))

    def test_flag_without_notice_fails(self):
        errs = kit_app.licence_errors(*self.bundle(None, True))
        self.assertTrue(any(kit_app.PERSONAL_NOTICE in e and "missing" in e for e in errs))

    def test_notice_without_gpl_or_configure_fails(self):
        errs = kit_app.licence_errors(*self.bundle("hello\n", True))
        self.assertEqual(len(errs), 2)


if __name__ == "__main__":
    unittest.main()
