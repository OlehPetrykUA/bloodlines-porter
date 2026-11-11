import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import basic_tree as bt
import devpaths

INSTALLER = devpaths.UP_INSTALLER

FAKE = textwrap.dedent(
    """
    import os, sys
    out = sys.argv[sys.argv.index("-d") + 1]
    assert "--collisions" in sys.argv and sys.argv[sys.argv.index("--collisions") + 1] == "rename-all"
    files = {
        "app/cfg/skill1.cfg#basic": b"B" * 10,
        "app/cfg/skill1.cfg#plus": b"P" * 20,
        "app/cfg/user.cfg#basic": b"u",
        "app/cfg/user.cfg#plus": b"UU",
        "app/only_plus.txt": b"plus only, untagged",
        "app/snd/a.wav$0": b"old",
        "app/snd/a.wav$1": b"new",
        "app/readme.txt": b"hello",
    }
    for rel, data in files.items():
        p = os.path.join(out, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
    """
)

EXPECTED = {
    "app/cfg/skill1.cfg": b"B" * 10,
    "app/cfg/user.cfg": b"u",
    "app/only_plus.txt": b"plus only, untagged",
    "app/snd/a.wav": b"new",
    "app/readme.txt": b"hello",
}


def manifest_for(files):
    rows = sorted(files.items(), key=lambda kv: kv[0].encode())
    return "".join("%s\t%d\t%s\n" % (k, len(v), hashlib.sha256(v).hexdigest()) for k, v in rows)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="basic_tree_test_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.installer = os.path.join(self.root, "setup.exe")
        with open(self.installer, "wb") as f:
            f.write(b"synthetic installer")
        self.md5 = hashlib.md5(b"synthetic installer").hexdigest()
        self.fake = os.path.join(self.root, "fake_innoextract.py")
        with open(self.fake, "w") as f:
            f.write(FAKE)
        self.cmd = (sys.executable, "-I", self.fake)
        self.manifest = os.path.join(self.root, "m.tsv")
        self.write_manifest(manifest_for(EXPECTED))

    def write_manifest(self, text):
        with open(self.manifest, "w", newline="") as f:
            f.write(text)

    def build(self, name="out", **kw):
        out = os.path.join(self.root, name)
        c = bt.build(self.installer, out, self.cmd, manifest=self.manifest, known_md5=self.md5, **kw)
        return out, c


class BuildTests(Fixture):
    def test_keeps_basic_drops_plus(self):
        out, c = self.build()
        for rel, data in EXPECTED.items():
            with open(os.path.join(out, rel), "rb") as f:
                self.assertEqual(f.read(), data, rel)
        self.assertEqual((c["basic"], c["plus"], c["dup"]), (2, 2, 1))
        self.assertEqual(c["files"], len(EXPECTED))

    def test_no_tagged_leftovers(self):
        out, _ = self.build()
        for p in bt.all_files(out):
            self.assertNotRegex(p, r"#(basic|plus)$|\$\d+$")

    def test_untagged_kept_and_counted(self):
        out, c = self.build()
        self.assertTrue(os.path.isfile(os.path.join(out, "app/only_plus.txt")))
        self.assertEqual(c["untagged"], 3)

    def test_repeatable_manifest_hash(self):
        a, ca = self.build("one")
        b, cb = self.build("two")
        self.assertEqual(ca["manifest_sha256"], cb["manifest_sha256"])
        with open(os.path.join(a, "manifest.tsv")) as fa, open(os.path.join(b, "manifest.tsv")) as fb:
            self.assertEqual(fa.read(), fb.read())

    def test_write_manifest_roundtrip(self):
        os.remove(self.manifest)
        self.build("gen", write_manifest=True)
        with open(self.manifest) as f:
            self.assertEqual(f.read(), manifest_for(EXPECTED))
        self.build("check")

    def test_manifest_has_no_times_and_three_columns(self):
        out, _ = self.build()
        with open(os.path.join(out, "manifest.tsv")) as f:
            for line in f:
                self.assertEqual(len(line.rstrip("\n").split("\t")), 3)

    def test_tmp_removed_on_failure(self):
        self.write_manifest("")
        out = os.path.join(self.root, "bad")
        with self.assertRaises(bt.BasicTreeError):
            bt.build(self.installer, out, self.cmd, manifest=self.manifest, known_md5=self.md5)
        self.assertFalse(os.path.exists(out))
        self.assertFalse(os.path.exists(out + ".tmp"))


class RefusalTests(Fixture):
    def test_unknown_md5_names_known_build(self):
        with self.assertRaises(bt.BasicTreeError) as cm:
            bt.build(self.installer, os.path.join(self.root, "o"), self.cmd, manifest=self.manifest)
        self.assertIn(bt.KNOWN_MD5, str(cm.exception))
        self.assertIn("VTMBup120.8.exe", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "o.tmp")))

    def test_variant_plus_refused(self):
        with self.assertRaises(bt.BasicTreeError):
            self.build(variant="plus")

    def test_default_variant_is_basic(self):
        import inspect
        self.assertEqual(inspect.signature(bt.build).parameters["variant"].default, "basic")

    def test_output_inside_repo_refused(self):
        with self.assertRaises(bt.BasicTreeError):
            bt.build(self.installer, os.path.join(bt.REPO, "tools", "up", "leak"), self.cmd,
                     manifest=self.manifest, known_md5=self.md5)

    def test_existing_output_refused(self):
        os.mkdir(os.path.join(self.root, "taken"))
        with self.assertRaises(bt.BasicTreeError):
            self.build("taken")

    def test_failing_innoextract_reported(self):
        with self.assertRaises(bt.BasicTreeError):
            bt.build(self.installer, os.path.join(self.root, "f"), (sys.executable, "-I", "-c", "raise SystemExit(3)"),
                     manifest=self.manifest, known_md5=self.md5)


class MutationTests(Fixture):
    def test_m1_plus_copy_kept_fails(self):
        mutated = dict(EXPECTED)
        mutated["app/cfg/skill1.cfg"] = b"P" * 20
        self.write_manifest(manifest_for(mutated))
        with self.assertRaises(bt.BasicTreeError):
            self.build()

    def test_m1_resolver_keeping_plus_is_caught(self):
        tree = os.path.join(self.root, "t")
        os.makedirs(tree)
        for name, data in (("x#basic", b"B"), ("x#plus", b"PP")):
            with open(os.path.join(tree, name), "wb") as f:
                f.write(data)
        bt.resolve(tree)
        with open(os.path.join(tree, "x"), "rb") as f:
            self.assertEqual(f.read(), b"B")
        self.assertFalse(os.path.exists(os.path.join(tree, "x#plus")))

    def test_m2_manifest_ignores_mtimes(self):
        out, _ = self.build()
        before = bt.manifest_text(out)
        for p in bt.all_files(out):
            os.utime(p, (1, 1))
        self.assertEqual(bt.manifest_text(out), before)

    def test_plus_basic_count_mismatch_fails(self):
        with open(self.fake, "a") as f:
            f.write('open(os.path.join(out, "app/cfg/extra.cfg#plus"), "wb").write(b"x")\n')
        with self.assertRaises(bt.BasicTreeError) as cm:
            self.build()
        self.assertIn("#plus", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "out.tmp")))

    def test_dup_numeric_order_9_vs_10(self):
        tree = os.path.join(self.root, "n")
        os.makedirs(tree)
        for i in (9, 10, 2):
            with open(os.path.join(tree, "a.wav$%d" % i), "wb") as f:
                f.write(b"v%d" % i)
        bt.resolve(tree)
        with open(os.path.join(tree, "a.wav"), "rb") as f:
            self.assertEqual(f.read(), b"v10")

    def test_case_variant_repo_path_refused(self):
        variant = os.path.join(bt.REPO.swapcase(), "tools", "up", "leak")
        if not os.path.exists(os.path.dirname(variant)):
            self.skipTest("case-sensitive filesystem")
        with self.assertRaises(bt.BasicTreeError):
            bt.build(self.installer, variant, self.cmd, manifest=self.manifest, known_md5=self.md5)

    def test_main_checkout_root_refused(self):
        common = subprocess.run(["git", "-C", bt.REPO, "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        main = os.path.dirname(os.path.join(bt.REPO, common))
        with self.assertRaises(bt.BasicTreeError):
            bt.build(self.installer, os.path.join(main, "leak"), self.cmd,
                     manifest=self.manifest, known_md5=self.md5)

    def test_unreadable_installer_wrapped(self):
        with self.assertRaises(bt.BasicTreeError):
            bt.build(self.root, os.path.join(self.root, "o"), self.cmd, manifest=self.manifest)

    def test_innoextract_stderr_decoded(self):
        with self.assertRaises(bt.BasicTreeError) as cm:
            bt.build(self.installer, os.path.join(self.root, "e"),
                     (sys.executable, "-I", "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"),
                     manifest=self.manifest, known_md5=self.md5)
        self.assertIn("boom", str(cm.exception))
        self.assertNotIn("b'", str(cm.exception))

    def test_m3_untagged_dropped_fails(self):
        dropped = {k: v for k, v in EXPECTED.items() if k != "app/only_plus.txt"}
        self.write_manifest(manifest_for(dropped))
        with self.assertRaises(bt.BasicTreeError) as cm:
            self.build()
        self.assertIn("only_plus.txt", str(cm.exception))

    def test_m4_default_plus_refused_by_cli(self):
        self.assertEqual(bt.main([self.installer, os.path.join(self.root, "cli"), "--variant", "plus"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "cli")))

    def test_dup_keeps_highest_index(self):
        tree = os.path.join(self.root, "d")
        os.makedirs(tree)
        for i in (0, 1, 2):
            with open(os.path.join(tree, "a.wav$%d" % i), "wb") as f:
                f.write(b"v%d" % i)
        bt.resolve(tree)
        with open(os.path.join(tree, "a.wav"), "rb") as f:
            self.assertEqual(f.read(), b"v2")
        self.assertEqual(os.listdir(tree), ["a.wav"])


@unittest.skipUnless(os.path.isfile(INSTALLER), "installer not present")
class RealInstaller(unittest.TestCase):
    def test_real_build_matches_repo_manifest(self):
        root = tempfile.mkdtemp(prefix="basic_tree_real_")
        self.addCleanup(shutil.rmtree, root, True)
        out = os.path.join(root, "up")
        c = bt.build(INSTALLER, out)
        with open(bt.MANIFEST, encoding="utf-8") as f:
            rows = sum(1 for line in f if line.strip())
        self.assertEqual((c["basic"], c["plus"], c["dup"], c["files"]), (350, 350, 63, rows))
        self.assertEqual(rows, 16695)
        self.assertEqual(os.path.getsize(os.path.join(out, "app/Unofficial_Patch/cfg/skill1.cfg")), 2608)
        self.assertEqual(os.path.getsize(os.path.join(out, "app/Unofficial_Patch/cfg/user.cfg")), 265)
        with open(bt.MANIFEST, "rb") as f:
            self.assertEqual(c["manifest_sha256"], hashlib.sha256(f.read()).hexdigest())

    def test_repo_holds_manifest_only(self):
        self.assertFalse(os.path.exists(os.path.join(bt.REPO, "tools", "up", "app")))
        self.assertLess(os.path.getsize(bt.MANIFEST), 4 << 20)


if __name__ == "__main__":
    unittest.main()
