import os
import sys
import unittest

CACHES = os.path.expanduser("~/Library/Caches/BloodlinesPorter")
GAME = os.path.expanduser("~/Games/Vampire Bloodlines (Windows)")
UP_INSTALLER = os.path.join(GAME, "VTMBup120.8.exe")


def dev_home():
    return os.environ.get("BN_DEV_HOME") or os.path.join(CACHES, "dev")


def dev(*parts):
    return os.path.join(dev_home(), *parts)


def build(*parts):
    return dev("build", *parts)


def dxvk_inst():
    return dev("dxvk", "inst")


def dxvk_lib():
    return os.path.join(dxvk_inst(), "lib", "libdxvk_d3d9.0.dylib")


def export_dxvk_lib():
    os.environ.setdefault("RT_DXVK_LIB", dxvk_lib())


def refdir():
    return os.environ.get("BN_REFDIR") or "."


def ghidra_project():
    return os.environ.get("BN_GHIDRA_PROJECT") or None


class Test(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in ("BN_DEV_HOME", "RT_DXVK_LIB")}

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_defaults(self):
        base = os.path.expanduser("~/Library/Caches/BloodlinesPorter/dev")
        self.assertEqual(dev_home(), base)
        self.assertEqual(build("all"), base + "/build/all")
        self.assertEqual(dxvk_inst(), base + "/dxvk/inst")
        self.assertEqual(dev("venv"), base + "/venv")
        self.assertEqual(dev("up12-basic"), base + "/up12-basic")
        for p in (dev_home(), build(), dxvk_inst(), dxvk_lib()):
            self.assertNotIn("Games/", p)
            self.assertNotIn("Vampire Bloodlines", p)

    def test_override(self):
        os.environ["BN_DEV_HOME"] = "/x/dev"
        self.assertEqual(build("a"), "/x/dev/build/a")
        self.assertEqual(dxvk_lib(), "/x/dev/dxvk/inst/lib/libdxvk_d3d9.0.dylib")

    def test_export_keeps_existing(self):
        os.environ["BN_DEV_HOME"] = "/x/dev"
        export_dxvk_lib()
        self.assertEqual(os.environ["RT_DXVK_LIB"], dxvk_lib())
        os.environ["RT_DXVK_LIB"] = "/mine.dylib"
        export_dxvk_lib()
        self.assertEqual(os.environ["RT_DXVK_LIB"], "/mine.dylib")

    def test_refdir_and_ghidra_env(self):
        saved = {k: os.environ.pop(k, None) for k in ("BN_REFDIR", "BN_GHIDRA_PROJECT")}
        try:
            self.assertEqual(refdir(), ".")
            self.assertIsNone(ghidra_project())
            os.environ["BN_REFDIR"] = "/r"
            os.environ["BN_GHIDRA_PROJECT"] = "/g"
            self.assertEqual(refdir(), "/r")
            self.assertEqual(ghidra_project(), "/g")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_build_skips_ghidra_without_project(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "recomp"))
        import build

        calls = []
        saved = os.environ.pop("BN_GHIDRA_PROJECT", None), build.run
        build.run = lambda cmd, **kw: calls.append(cmd) or 0
        try:
            self.assertEqual(build.ghidra_names("a.dll", "/nonexistent/names.tsv"), 0)
            self.assertEqual(calls, [])
            os.environ["BN_GHIDRA_PROJECT"] = "/g"
            self.assertEqual(build.ghidra_names("a.dll", "/nonexistent/names.tsv"), 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], "/g")
        finally:
            build.run = saved[1]
            os.environ.pop("BN_GHIDRA_PROJECT", None)
            if saved[0] is not None:
                os.environ["BN_GHIDRA_PROJECT"] = saved[0]

    def test_game_kept(self):
        home = os.path.expanduser("~")
        self.assertEqual(GAME, home + "/Games/Vampire Bloodlines (Windows)")
        self.assertEqual(UP_INSTALLER, GAME + "/VTMBup120.8.exe")


if __name__ == "__main__":
    sys.exit(unittest.main())
