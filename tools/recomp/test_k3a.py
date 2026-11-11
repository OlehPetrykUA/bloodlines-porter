import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_all
import translate

IMG = bytes(range(256)) * 4


def write_tsv(d, rows):
    path = os.path.join(d, "intrinsics.tsv")
    with open(path, "w") as f:
        f.write("module\trva\tname\tsize\tsha256\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    return path


def row(module, rva, size, img=IMG, digest=None):
    return (module, f"0x{rva:X}", "__ftol", str(size), digest or hashlib.sha256(img[rva:rva + size]).hexdigest())


PLAN = {"modules": {"a": {"path": "Bin/a.dll", "code_size": 2}, "b": {"path": "Bin/b.dll", "code_size": 1}},
        "build": {"chunk1": []}}


class Intrinsics(unittest.TestCase):
    def test_match(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tsv(d, [row("a", 0x10, 39), row("b", 0x20, 39)])
            self.assertEqual(translate.load_intrinsics(p, "a", IMG, 0), {0x10: "__ftol"})

    def test_mismatch_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tsv(d, [row("a", 0x10, 39, digest="0" * 64)])
            with self.assertRaises(SystemExit) as cm:
                translate.load_intrinsics(p, "a", IMG, 0)
            self.assertEqual(cm.exception.code, 2)

    def test_other_module_not_verified(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tsv(d, [row("b", 0x10, 39, digest="0" * 64)])
            self.assertEqual(translate.load_intrinsics(p, "a", IMG, 0), {})

    def test_shipped_table_is_well_formed(self):
        lines = open(os.path.join(HERE, "intrinsics.tsv")).read().splitlines()
        self.assertEqual(lines[0], "module\trva\tname\tsize\tsha256")
        self.assertEqual(len(lines) - 1, 18)
        names = set(translate.INTRINSICS.values())
        self.assertEqual(len({n[0] for n in names}), len(names))
        for line in lines[1:]:
            m, rva, name, size, digest = line.split("\t")
            self.assertIn(name, translate.INTRINSICS)
            int(rva, 16), int(size)
            self.assertEqual(len(digest), 64)


class BuildAll(unittest.TestCase):
    def test_ccache_absent(self):
        with mock.patch.object(build_all.shutil, "which", return_value=None), \
                mock.patch.object(build_all, "out_of", side_effect=AssertionError("ccache ran")):
            self.assertEqual(build_all.ccache("--print-stats"), "")

    def test_ccache_present(self):
        with mock.patch.object(build_all.shutil, "which", return_value="/x/ccache"), \
                mock.patch.object(build_all, "out_of", return_value=(0, "s")) as o:
            self.assertEqual(build_all.ccache("--zero-stats"), "s")
            o.assert_called_once_with(["ccache", "--zero-stats"])

    def test_every_python_child_runs_without_bytecode_writes(self):
        import re
        self.assertEqual(build_all.PY[1:], ["-I", "-B"])
        repo = os.path.dirname(os.path.dirname(HERE))
        for rel in ("tools/recomp/build.py", "harness/run.py", "harness/m3.py"):
            with open(os.path.join(repo, rel)) as f:
                src = f.read()
            launches = re.findall(r"\[sys\.executable,[^\]]*\]", src)
            self.assertTrue(launches, rel)
            for l in launches:
                self.assertIn('"-B"', l, rel)

    def test_game_flag(self):
        saved = build_all.GAME
        try:
            with tempfile.TemporaryDirectory() as d, mock.patch.object(build_all, "run", return_value=1):
                self.assertEqual(build_all.main(["--game", d, "--out", os.path.join(d, "o")]), 2)
                self.assertEqual(build_all.GAME, os.path.abspath(d))
                jobs = build_all.jobs_for(os.path.join(d, "o"), PLAN)
                srcs = {name: cmd[len(build_all.PY) + 1] for name, cmd, _ in jobs if name in PLAN["modules"]}
                self.assertEqual(srcs["a"], os.path.join(os.path.abspath(d), "Bin/a.dll"))
        finally:
            build_all.GAME = saved

    def test_game_default(self):
        saved = build_all.GAME
        try:
            with tempfile.TemporaryDirectory() as d, mock.patch.object(build_all, "run", return_value=1):
                build_all.main(["--out", d])
                self.assertEqual(build_all.GAME, os.path.abspath(saved))
        finally:
            build_all.GAME = saved

    def test_no_oracle_keeps_selftest_jobs(self):
        names = {n for n, _, _ in build_all.jobs_for("/o", PLAN)}
        self.assertEqual(names, set(PLAN["modules"]) | {"conform", "simd", "m3", "xflags"})

    def test_no_oracle_skips_only_unicorn_modes(self):
        ran = []

        def fake(cmd):
            ran.append(cmd[len(build_all.PY) + 1])
            return 0, "ok"

        with mock.patch.object(build_all, "out_of", side_effect=fake), mock.patch("builtins.print"):
            res = build_all.harness("/o", build_all.UNICORN_MODES)
            self.assertEqual([r["mode"] for r in res], ["trap", "cases"])
            self.assertEqual(len(ran), 2)
            self.assertEqual(len(build_all.harness("/o")), 5)
        self.assertEqual(set(build_all.UNICORN_MODES), {"conform", "xflags", "mmx"})

    def test_m3_cases_gets_game(self):
        saved = build_all.GAME
        ran = []
        try:
            build_all.GAME = "/elsewhere/game"
            with mock.patch.object(build_all, "out_of", side_effect=lambda cmd: ran.append(cmd) or (0, "ok")), \
                    mock.patch("builtins.print"):
                build_all.harness("/o")
        finally:
            build_all.GAME = saved
        m3 = [c for c in ran if c[len(build_all.PY)].endswith("m3.py")]
        self.assertEqual(len(m3), 1)
        self.assertEqual(m3[0][m3[0].index("--game") + 1], "/elsewhere/game")
        self.assertTrue(all("--game" not in c for c in ran if c not in m3))

    def test_plan_gets_game(self):
        saved = build_all.GAME
        calls = []
        try:
            with tempfile.TemporaryDirectory() as d, \
                    mock.patch.object(build_all, "run", side_effect=lambda cmd, **kw: calls.append(cmd) or 1):
                build_all.main(["--game", d, "--out", os.path.join(d, "o")])
            plan = next(c for c in calls if c[len(build_all.PY)].endswith("plan.py"))
            self.assertEqual(plan[plan.index("--game") + 1], os.path.abspath(d))
        finally:
            build_all.GAME = saved

    def test_no_analyze_headless(self):
        self.assertNotIn("analyzeHeadless", open(build_all.__file__).read())

    def test_options_reach_meson_build(self):
        src = open(build_all.__file__).read()
        self.assertEqual(src.count("] + pre_opts"), 2)
        self.assertIn("meson_build(os.path.join(REPO, \"link\"), os.path.join(out, \"meson\"), link_opts, log)", src)

    def test_no_oracle_flag_parses(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(build_all, "run", return_value=1):
            self.assertEqual(build_all.main(["--no-oracle", "--out", d]), 2)

    def test_no_ghidra(self):
        self.assertFalse(hasattr(build_all, "ghidra_names"))
        self.assertFalse(hasattr(build_all, "GHIDRA"))
        for name, cmd, _ in build_all.jobs_for("/o", PLAN):
            self.assertNotIn("--names", cmd)


class Prebuilt(unittest.TestCase):
    def test_options_only_when_given(self):
        self.assertEqual(build_all.prebuilt_options(None), [])
        self.assertEqual(build_all.prebuilt_options("/k/prebuilt"), ["-Dprebuilt=true", "-Dprebuilt_prefix=/k/prebuilt"])

    def test_flag_parses_to_absolute_options(self):
        seen = []
        with tempfile.TemporaryDirectory() as d, mock.patch.object(build_all, "run", return_value=1), \
                mock.patch.object(build_all, "prebuilt_options", side_effect=lambda p: seen.append(p) or []):
            build_all.main(["--prebuilt", "rel/pre", "--out", d])
            build_all.main(["--out", d])
        self.assertEqual(seen, [os.path.abspath("rel/pre"), None])

    def test_source_sha_from_source_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(build_all.source_sha(d))
            with open(os.path.join(d, "SOURCE"), "w") as f:
                f.write("a" * 40 + "\n")
            self.assertEqual(build_all.source_sha(d), "a" * 40)

    def test_source_sha_kit_layout(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.mkdir(src)
            self.assertIsNone(build_all.source_sha(src))
            with open(os.path.join(d, "SOURCE"), "w") as f:
                f.write("d" * 40 + "\n")
            self.assertEqual(build_all.source_sha(src), "d" * 40)
            self.assertEqual(build_all.source_info(src), {"sha": "d" * 40, "from": "SOURCE"})

    def test_source_info_git_dirty(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, ".git"))
            with mock.patch.object(build_all, "out_of", side_effect=[(0, "e" * 40), (0, " M x")]):
                self.assertEqual(build_all.source_info(d), {"sha": "e" * 40, "from": "git", "dirty": True})

    def test_meson_prebuilt_interface(self):
        root = os.path.dirname(os.path.dirname(HERE))
        link = open(os.path.join(root, "link", "meson.build")).read()
        rt = open(os.path.join(root, "runtime", "meson.build")).read()
        for text in (link, rt):
            for n in ("SDL3.0", "avformat.62", "avcodec.62", "avutil.60"):
                self.assertIn(n, text)
            self.assertIn("-ffile-prefix-map=' + meson.project_source_root() + '=src'", text)
            self.assertIn("-ffile-prefix-map=' + gen + '=gen'", text)
        self.assertIn("run_args = ['-Wl,-rpath,' + pre_lib]", link)
        self.assertIn("args: run_args", link)
        self.assertIn("rpaths = []", link)

    def test_main_reaches_translate_with_selftest_jobs(self):
        import json
        for extra in ([], ["--no-oracle"]):
            with tempfile.TemporaryDirectory() as d:
                with open(os.path.join(d, "plan.json"), "w") as f:
                    json.dump(PLAN, f)
                seen = []
                with mock.patch.object(build_all, "run", return_value=0), \
                        mock.patch.object(build_all, "translate_all",
                                          side_effect=lambda jobs, n: seen.append(jobs) or ["stop"]):
                    rc = build_all.main(["--out", d, "--no-engine-aspect", "--game", d] + extra)
                self.assertEqual(rc, 2)
                names = {n for n, _, _ in seen[0]}
                self.assertEqual(names, {"a", "b", "conform", "simd", "m3", "xflags"})

    def test_source_sha_from_git(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, ".git"))
            with open(os.path.join(d, "SOURCE"), "w") as f:
                f.write("b" * 40)
            with mock.patch.object(build_all, "out_of", return_value=(0, "c" * 40)):
                self.assertEqual(build_all.source_sha(d), "c" * 40)
            with mock.patch.object(build_all, "out_of", return_value=(128, "fatal")):
                self.assertEqual(build_all.source_sha(d), "b" * 40)


if __name__ == "__main__":
    unittest.main()
