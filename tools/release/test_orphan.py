import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "orphan.sh"
NAME = "private.person@"
MAIL = NAME + "work.example"
MAIL2 = NAME + "home.example"
TOOL = "secrettool"
TOOL2 = "othertool"
BANNED = r"private[.]person@(work|home)[.]example|secrettool|othertool"
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
       "BP_BANNED": BANNED}


class Orphan(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orphan-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        (self.repo / "tools/release").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.repo / "tools/release/orphan.sh")
        self.git("init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("one\n")
        self.commit("first")
        (self.repo / "a.txt").write_text("two\n")
        self.commit("second")

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.repo)] + list(a), capture_output=True, text=True, env=dict(os.environ, **ENV))

    def commit(self, msg):
        self.git("add", "-A")
        self.assertEqual(self.git("commit", "-q", "-m", msg).returncode, 0)

    def run_script(self, *a):
        return subprocess.run(["sh", str(self.repo / "tools/release/orphan.sh")] + list(a), capture_output=True, text=True, env=dict(os.environ, **ENV))

    def test_clean_tree_gives_one_parentless_commit(self):
        r = self.run_script("HEAD", "pub")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.git("rev-list", "--count", "pub").stdout.strip(), "1")
        self.assertEqual(self.git("rev-parse", "pub^{tree}").stdout, self.git("rev-parse", "HEAD^{tree}").stdout)
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>|%cn <%ce>|%s", "pub").stdout.strip(), "Bloodlines Porter <noreply@invalid>|Bloodlines Porter <noreply@invalid>|chore: initial import")
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "main")
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_banned_text_refused_and_no_branch_made(self):
        for text in (MAIL, MAIL2.upper(), TOOL.upper(), TOOL2):
            (self.repo / "b.txt").write_text("x %s y\n" % text)
            self.commit("bad")
            r = self.run_script("HEAD", "pub")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("b.txt", r.stderr)
            self.assertNotEqual(self.git("rev-parse", "--verify", "--quiet", "pub").returncode, 0)

    def test_banned_file_name_refused(self):
        (self.repo / (TOOL + ".md")).write_text("x\n")
        self.commit("name")
        self.assertNotEqual(self.run_script("HEAD", "pub").returncode, 0)

    def test_no_banned_regex_refused(self):
        r = subprocess.run(["sh", str(self.repo / "tools/release/orphan.sh"), "HEAD", "pub"], capture_output=True, text=True,
                           env=dict(os.environ, **{k: v for k, v in ENV.items() if k != "BP_BANNED"}, BP_BANNED=""))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("BP_BANNED", r.stderr)
        self.assertNotEqual(self.git("rev-parse", "--verify", "--quiet", "pub").returncode, 0)

    def test_existing_branch_and_bad_ref_refused(self):
        self.git("branch", "pub")
        self.assertNotEqual(self.run_script("HEAD", "pub").returncode, 0)
        self.assertNotEqual(self.run_script("nope", "other").returncode, 0)

    def test_old_commit_with_banned_text_does_not_block_a_clean_ref(self):
        (self.repo / "b.txt").write_text(MAIL + "\n")
        self.commit("bad")
        (self.repo / "b.txt").unlink()
        self.commit("fix")
        self.assertEqual(self.run_script("HEAD", "pub").returncode, 0)
        self.assertEqual(self.git("grep", "-c", "-i", NAME, "pub").returncode, 1)


if __name__ == "__main__":
    unittest.main()
