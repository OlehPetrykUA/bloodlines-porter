import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kit_app
import kitconst
import make_kit

TARBALL_BYTES = b"fake ffmpeg source"
TARBALL_SHA = hashlib.sha256(TARBALL_BYTES).hexdigest()
SHA = "a" * 40
LOCK = {"tag": "20260101", "url": "https://example.test/cpython-3.13.1+20260101-aarch64-apple-darwin-install_only.tar.gz", "sha256": "b" * 64}
REQ = "capstone==5.0.9 \\\n  --hash=sha256:%s\nmeson==1.12.1 \\\n  --hash=sha256:%s\nninja==1.13.2 \\\n  --hash=sha256:%s\n" % ("1" * 64, "2" * 64, "3" * 64)


def write(p, text="x\n", mode=None):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if mode:
        p.chmod(mode)
    return p


def fake_id(path):
    return "@rpath/" + Path(path).name


def fake_deps(path):
    return ["/usr/lib/libSystem.B.dylib", "@rpath/libSDL3.0.dylib"]


def fake_kit(root):
    k = Path(root) / "kit"
    write(k / "SOURCE", SHA + "\n")
    write(k / "src/tools/kit/kit.py", "print(1)\n")
    write(k / "python/bin/python3", "#!/bin/sh\n", 0o755)
    write(k / "python/lib/python3.13/lib-dynload/_json.so", "so\n")
    for name in (kitconst.CLI_NAME,) + kitconst.CLI_ALIASES:
        write(k / name, make_kit.WRAPPER, 0o755)
    write(k / "sources" / make_kit.TARBALL, TARBALL_BYTES.decode())
    rows = []
    for n in make_kit.BUILD_COMPONENTS:
        rows.append("| `%s` | 1 | MIT | licenses/%s/ |" % (n, n))
        write(k / "licenses" / n / "LICENSE", "text\n")
    run = ["| `sdl3` | 3 | Zlib | licenses/sdl3/ |", "| `ffmpeg` | 8 | LGPL-2.1 | licenses/ffmpeg/ |", "| `mesa` | 1 | MIT | licenses/mesa/ |"]
    for n in ("sdl3", "mesa"):
        write(k / "licenses" / n / "LICENSE", "text\n")
    write(k / "licenses/ffmpeg" / kit_app.LGPL_COPYING, "lgpl\n")
    shutil.copytree(make_kit.STATIC_LICENCES, k / "licenses/python", dirs_exist_ok=True)
    write(k / "NOTICE.md", "\n".join(["# n", "## Build", ""] + rows + ["", "## Runtime", ""] + run) + "\n")
    write(k / "prebuilt/lib/libSDL3.0.dylib", "d\n")
    write(k / "prebuilt/lib/libavformat.62.dylib", "d\n")
    write(k / "prebuilt/lib/libvulkan_kosmickrisp.dylib", "d\n")
    write(k / "prebuilt/icd" / kitconst.ICD_NAME, json.dumps({"ICD": {"library_path": "../lib/libvulkan_kosmickrisp.dylib"}}))
    write(k / "EULA.txt", kit_app.EULA_TEXT)
    ent = lambda c, spdx: {"component": c, "version": "1", "spdx": spdx, "licence": "licenses/" + c}
    write(k / "prebuilt/manifest.json", json.dumps({
        "lib/libSDL3.0.dylib": ent("sdl3", "Zlib"),
        "lib/libavformat.62.dylib": ent("ffmpeg", "LGPL-2.1"),
        "lib/libvulkan_kosmickrisp.dylib": ent("mesa", "MIT"),
        "icd/" + kitconst.ICD_NAME: ent("mesa", "MIT"),
    }))
    return k


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="k3c-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(make_kit, "FFMPEG_SRC_SHA256", TARBALL_SHA)
        p.start()
        self.addCleanup(p.stop)
        self.kit = fake_kit(self.tmp)

    def errs(self):
        return make_kit.check(self.kit, fake_id, fake_deps)

    def has(self, text):
        self.assertTrue(any(text in e for e in self.errs()), self.errs())


class CheckBaseline(Base):
    def test_fake_kit_passes(self):
        self.assertEqual(self.errs(), [])


class StaticLicences(Base):
    def test_missing_text_fails(self):
        (self.kit / "licenses/python/expat/LICENSE.txt").unlink()
        self.has("licenses/python/expat/LICENSE.txt is missing")

    def test_changed_text_fails(self):
        (self.kit / "licenses/python/openssl/LICENSE.txt").write_text("changed\n")
        self.has("licenses/python/openssl/LICENSE.txt does not match")

    def test_each_library_text_required(self):
        for n in make_kit.PYTHON_STATIC_LIBS:
            k = fake_kit(self.tmp / n)
            (k / "licenses/python" / n / "LICENSE.txt").unlink()
            self.assertTrue(any("licenses/python/%s/LICENSE.txt is missing" % n in e for e in make_kit.check(k, fake_id, fake_deps)), n)

    def test_table_names_all_seven_libraries_with_versions(self):
        libs = json.loads((make_kit.STATIC_LICENCES / make_kit.STATIC_LIBS).read_text())["libs"]
        self.assertEqual(sorted(libs), sorted(make_kit.PYTHON_STATIC_LIBS))
        self.assertEqual({n: libs[n]["version"] for n in libs}, {"openssl": "3.5.9", "expat": "2.9.0", "mpdecimal": "4.0.0", "bzip2": "1.0.8", "libffi": "3.8.0", "liblzma": "5.8.4", "sqlite": "3.53.1"})

    def test_missing_table_fails(self):
        (self.kit / "licenses/python" / make_kit.STATIC_LIBS).unlink()
        self.has(make_kit.STATIC_LIBS)

    def test_cut_copies_texts_next_to_the_python_licence(self):
        out = self.tmp / "out"
        write(out / "licenses/python/LICENSE.txt")
        make_kit.collect_static_licences(out, json.loads(make_kit.LOCK.read_text())["tag"])
        for n in make_kit.PYTHON_STATIC_LIBS:
            self.assertTrue((out / "licenses/python" / n / "LICENSE.txt").is_file())
        self.assertTrue((out / "licenses/python/LICENSE.txt").is_file())

    def test_python_build_mismatch_refused(self):
        with self.assertRaises(make_kit.Fail):
            make_kit.collect_static_licences(self.tmp / "out", "19000101")

    def test_repo_texts_match_python_lock(self):
        self.assertEqual(make_kit.static_licence_errors(make_kit.STATIC_LICENCES, json.loads(make_kit.LOCK.read_text())["tag"]), [])


class AcK37(Base):
    def test_missing_licence_folder_fails(self):
        shutil.rmtree(self.kit / "licenses/capstone")
        self.has("capstone")

    def test_empty_licence_file_fails(self):
        write(self.kit / "licenses/capstone/LICENSE", "")
        self.has("capstone")

    def test_missing_build_row_fails(self):
        n = self.kit / "NOTICE.md"
        n.write_text(n.read_text().replace("| `ninja` | 1 | MIT | licenses/ninja/ |\n", ""))
        self.has("no Build row for ninja")

    def test_each_build_component_required(self):
        for c in make_kit.BUILD_COMPONENTS:
            k = fake_kit(self.tmp / c)
            n = k / "NOTICE.md"
            n.write_text("\n".join(l for l in n.read_text().splitlines() if "`%s`" % c not in l) + "\n")
            self.assertTrue(any("Build row for " + c in e for e in make_kit.check(k, fake_id, fake_deps)), c)

    def test_readline_module_fails(self):
        write(self.kit / "python/lib/python3.13/lib-dynload/readline.cpython-313-darwin.so")
        self.has("GPL module")

    def test_gdbm_module_fails(self):
        write(self.kit / "python/lib/python3.13/lib-dynload/_gdbm.cpython-313-darwin.so")
        self.has("GPL module")

    def test_runtime_licence_required(self):
        shutil.rmtree(self.kit / "licenses/sdl3")
        self.has("sdl3")


class NoGameData(Base):
    def test_vpk_in_src_fails(self):
        write(self.kit / "src/data/pack001.vpk")
        self.has(".vpk")

    def test_pe_header_under_innocent_name_fails(self):
        p = self.kit / "src/tools/readme.txt"
        p.write_bytes(b"MZ\x90\x00 payload")
        self.has("magic")

    def test_vpk_magic_fails(self):
        (self.kit / "src/blob.bin").write_bytes(b"\x34\x12\xaa\x55rest")
        self.has("magic")

    def test_game_file_in_prebuilt_fails(self):
        write(self.kit / "prebuilt/lib/x.dll")
        self.has(".dll")

    def test_ghidra_files_fail(self):
        write(self.kit / "src/tools/recomp/a.gpr")
        write(self.kit / "src/ghidra/x.txt")
        self.has("Ghidra file")
        self.has("directory ghidra")

    def test_vendor_dir_fails(self):
        write(self.kit / "src/vendor/dxvk/a.c")
        self.has("vendor")

    def test_oversize_file_fails(self):
        with mock.patch.object(make_kit, "MAX_SOURCE_FILE", 4):
            self.has("over the")

    def test_clean_tree_has_no_findings(self):
        self.assertEqual(make_kit.game_data_errors(self.kit / "src"), [])


class Eula(Base):
    def test_missing_eula_fails(self):
        (self.kit / "EULA.txt").unlink()
        self.has("EULA.txt")

    def test_eula_without_the_lgpl_line_fails(self):
        write(self.kit / "EULA.txt", "terms\n")
        self.has("EULA.txt")


class Layout(Base):
    def test_bad_source_sha_fails(self):
        (self.kit / "SOURCE").write_text("abc\n")
        self.has("SOURCE")

    def test_wrapper_must_use_bundled_python(self):
        (self.kit / kitconst.CLI_NAME).write_text("#!/bin/sh\nexec /usr/bin/python3\n")
        self.has("wrapper")

    def test_alias_wrapper_must_be_executable(self):
        (self.kit / kitconst.CLI_ALIASES[0]).chmod(0o644)
        self.has(kitconst.CLI_ALIASES[0] + " wrapper")

    def test_wrapper_must_be_executable(self):
        (self.kit / kitconst.CLI_NAME).chmod(0o644)
        self.has("wrapper")

    def test_tarball_sha_mismatch_fails(self):
        (self.kit / "sources" / make_kit.TARBALL).write_text("tampered")
        self.has(make_kit.TARBALL)

    def test_dylib_id_must_be_rpath(self):
        out = make_kit.check(self.kit, lambda p: "/opt/homebrew/lib/libSDL3.0.dylib", fake_deps)
        self.assertTrue(any("need @rpath/libSDL3.0.dylib" in e for e in out), out)

    def test_prebuilt_file_must_be_in_manifest(self):
        write(self.kit / "prebuilt/lib/libextra.dylib")
        self.has("not in manifest.json")

    def test_cli_check_exit_codes(self):
        with mock.patch.object(make_kit, "otool_id", fake_id), mock.patch.object(make_kit, "otool_deps", fake_deps):
            self.assertEqual(make_kit.main(["check", str(self.kit)]), 0)
            shutil.rmtree(self.kit / "licenses/capstone")
            self.assertEqual(make_kit.main(["check", str(self.kit)]), 1)


class Leaks(Base):
    HOME = "/Users/cutter"

    def leaks(self):
        return make_kit.check(self.kit, fake_id, fake_deps, home=self.HOME)

    def test_fixture_with_placeholder_paths_passes(self):
        write(self.kit / "src/harness/t.py", "p = '/Users/x/build/bloodlines-native'\nt = b'/var/folders/'\n")
        write(self.kit / "python/lib/python3.13/site-packages/pip/urls.py", "# /C:/Users/foo\n")
        write(self.kit / "python/lib/python3.13/site-packages/mesonbuild/apple.py", "p = '/opt/homebrew/lib'\n")
        self.assertEqual(self.leaks(), [])

    def test_make_kit_source_passes_its_own_scan_so_a_kit_app_can_bundle_it(self):
        write(self.kit / "Contents/Resources/kit/make_kit.py", Path(make_kit.__file__).read_text())
        self.assertEqual(self.leaks(), [])

    def test_kit_app_build_maps_the_source_path_out_of_the_binary(self):
        text = (Path(make_kit.__file__).parent / "app/package.py").read_text()
        self.assertIn('"-file-prefix-map"', text)

    def test_cutter_home_in_compiled_python_fails(self):
        write(self.kit / "python/lib/python3.13/__pycache__/os.cpython-313.pyc", "\0\0%s/kit/python/lib/os.py\0" % self.HOME)
        self.assertTrue(any("__pycache__/os.cpython-313.pyc holds the host path /Users/cutter/" in e for e in self.leaks()), self.leaks())

    def test_cutter_home_in_shipped_source_fails(self):
        write(self.kit / "src/tools/x.py", "p = '%s/Games/VTMB/vanilla'\n" % self.HOME)
        self.assertTrue(any("src/tools/x.py holds the host path" in e for e in self.leaks()), self.leaks())

    def test_kit_folder_path_fails(self):
        write(self.kit / "python/lib/python3.13/site-packages/x.dist-info/RECORD", "%s/python/bin/meson,,\n" % self.kit.resolve())
        self.assertTrue(any("RECORD holds the host path" in e for e in self.leaks()), self.leaks())

    def test_temp_folder_fails(self):
        write(self.kit / "prebuilt/include/x.h", "/private/var/folders/ab/T/x\n")
        manifest = self.kit / "prebuilt/manifest.json"
        m = json.loads(manifest.read_text())
        m["include/x.h"] = m["lib/libSDL3.0.dylib"]
        manifest.write_text(json.dumps(m))
        self.assertTrue(any("x.h holds the host path /var/folders/" in e for e in self.leaks()), self.leaks())

    def test_host_shebang_fails(self):
        for i, sb in enumerate(("/Users/other/kit/python/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3")):
            write(self.kit / ("python/bin/tool%d" % i), "#!%s\n" % sb, 0o755)
        self.assertEqual(sum("holds the host path" in e for e in self.leaks()), 3, self.leaks())

    def test_symlink_into_home_fails(self):
        (self.kit / "python/bin/py").symlink_to(self.HOME + "/x/python3")
        self.assertTrue(any("python/bin/py holds the host path" in e for e in self.leaks()), self.leaks())

    def test_absolute_icd_library_path_fails(self):
        write(self.kit / "prebuilt/icd" / kitconst.ICD_NAME, json.dumps({"ICD": {"library_path": "/opt/homebrew/lib/libvulkan_kosmickrisp.dylib"}}))
        self.has("is not a relative path")

    def test_icd_library_path_must_name_a_file_in_prebuilt_lib(self):
        (self.kit / "prebuilt/lib/libvulkan_kosmickrisp.dylib").unlink()
        mf = self.kit / "prebuilt/manifest.json"
        m = json.loads(mf.read_text())
        del m["lib/libvulkan_kosmickrisp.dylib"]
        mf.write_text(json.dumps(m))
        self.has("is not a file in prebuilt/lib")

    def test_icd_library_path_outside_prebuilt_lib_fails(self):
        write(self.kit / "prebuilt/icd" / kitconst.ICD_NAME, json.dumps({"ICD": {"library_path": "../../x/libvulkan_kosmickrisp.dylib"}}))
        self.has("is not a file in prebuilt/lib")

    def test_kit_path_with_dotdot_is_not_falsely_rejected(self):
        odd = self.kit.parent / "side" / ".." / self.kit.name
        (self.kit.parent / "side").mkdir()
        out = make_kit.check(odd, fake_id, fake_deps, home=self.HOME)
        self.assertFalse(any("is not a file in prebuilt/lib" in e for e in out), out)

    def test_used_kit_bytecode_is_flagged_by_the_cutter_but_not_by_the_kit_check(self):
        import kit as kit_cli
        write(self.kit / "python/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc", "\0\0%s/python/lib/python3.13/encodings/aliases.py\0" % self.kit.resolve())
        with mock.patch.object(make_kit, "otool_id", fake_id), mock.patch.object(make_kit, "otool_deps", fake_deps), \
                mock.patch.object(make_kit, "otool_rpaths", lambda p: []):
            with mock.patch("sys.stdout"):
                self.assertEqual(make_kit.main(["check", str(self.kit)]), 1)
            self.assertTrue(any("aliases.cpython-313.pyc holds the host path" in e for e in make_kit.check(self.kit)))
            with mock.patch.object(kit_cli, "tool_run", lambda argv, env: (0, "")), mock.patch("sys.stdout"):
                self.assertEqual(kit_cli.main(["check"], {"PATH": ""}, self.kit), 0)


class Locks(unittest.TestCase):
    def test_good_lock_passes(self):
        self.assertEqual(make_kit.lock_errors(json.dumps(LOCK), REQ), [])

    def test_unfilled_lock_fails(self):
        self.assertTrue(make_kit.lock_errors(json.dumps({"tag": "", "url": "", "sha256": ""}), REQ))

    def test_wrong_archive_kind_fails(self):
        bad = dict(LOCK, url=LOCK["url"].replace("install_only", "full"))
        self.assertTrue(any("install_only" in e for e in make_kit.lock_errors(json.dumps(bad), REQ)))

    def test_unhashed_requirement_fails(self):
        req = REQ.replace("--hash=sha256:" + "1" * 64, "")
        self.assertTrue(any("no --hash" in e for e in make_kit.lock_errors(json.dumps(LOCK), req)))

    def test_unpinned_requirement_fails(self):
        req = REQ.replace("ninja==1.13.2", "ninja>=1.13")
        self.assertTrue(any("not pinned" in e for e in make_kit.lock_errors(json.dumps(LOCK), req)))

    def test_missing_wheel_fails(self):
        req = "\n".join(l for l in REQ.replace("\\\n", "").splitlines() if not l.startswith("meson"))
        self.assertTrue(any("lacks meson" in e for e in make_kit.lock_errors(json.dumps(LOCK), req)))

    def test_shipped_requirements_pin_with_hashes(self):
        errs = make_kit.lock_errors(json.dumps(LOCK), make_kit.REQUIREMENTS.read_text())
        self.assertFalse([e for e in errs if "capstone" in e or "ninja" in e or "no --hash" in e or "not pinned" in e], errs)


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo)] + list(a), check=True, capture_output=True)


class CutSource(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="k3c-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        write(self.repo / "tools/a.py", "print(1)\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "c")
        self.out = self.tmp / "out"
        self.out.mkdir()

    def test_clean_tree_writes_source_and_src(self):
        sha = make_kit.cut_source(self.repo, self.out)
        self.assertRegex(sha, make_kit.SHA40)
        self.assertEqual((self.out / "SOURCE").read_text().strip(), sha)
        self.assertTrue((self.out / "src/tools/a.py").is_file())
        self.assertFalse((self.out / "src/.git").exists())

    def test_relative_out_folder_is_resolved_before_git_runs(self):
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            make_kit.cut_source(self.repo, Path("out"))
        finally:
            os.chdir(cwd)
        self.assertTrue((self.out / "src/tools/a.py").is_file())

    def test_dirty_tree_refused(self):
        write(self.repo / "tools/a.py", "print(2)\n")
        with self.assertRaises(make_kit.Fail):
            make_kit.cut_source(self.repo, self.out)

    def test_untracked_file_refused(self):
        write(self.repo / "new.py")
        with self.assertRaises(make_kit.Fail):
            make_kit.cut_source(self.repo, self.out)

    def test_ghidra_scripts_are_left_out_of_the_cut(self):
        write(self.repo / "tools/ghidra/run.sh")
        for n in ("docs/ghidra-scout.md", "docs/inventory/rtti-x.dll.txt", "docs/inventory.md", "docs/vpk-inventory.md", "docs/m4-probe.md", ".github/workflows/ci.yml"):
            write(self.repo / n)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "g")
        make_kit.cut_source(self.repo, self.out)
        self.assertFalse((self.out / "src/tools/ghidra").exists())
        for n in ("docs/ghidra-scout.md", "docs/inventory", "docs/inventory.md", "docs/vpk-inventory.md", "docs/m4-probe.md", ".github"):
            self.assertFalse((self.out / "src" / n).exists(), n)
        self.assertTrue((self.out / "src/tools/a.py").is_file())

    def test_untracked_game_file_never_enters_the_cut(self):
        git(self.repo, "config", "status.showUntrackedFiles", "no")
        write(self.repo / "pack001.vpk")
        make_kit.cut_source(self.repo, self.out)
        self.assertFalse((self.out / "src/pack001.vpk").exists())

    def test_committed_game_file_is_caught_by_scan(self):
        write(self.repo / "pack001.vpk")
        git(self.repo, "add", "-f", "-A")
        git(self.repo, "commit", "-qm", "oops")
        make_kit.cut_source(self.repo, self.out)
        self.assertTrue(make_kit.game_data_errors(self.out / "src"))


class Python(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="k3c-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        stage = self.tmp / "stage"
        write(stage / "python/bin/python3", "x")
        write(stage / "python/lib/python3.13/lib-dynload/readline.so")
        write(stage / "python/lib/python3.13/lib-dynload/_gdbm.so")
        write(stage / "python/lib/python3.13/lib-dynload/_json.so")
        self.archive = self.tmp / "py.tar.gz"
        with tarfile.open(self.archive, "w:gz") as t:
            t.add(stage / "python", arcname="python")
        self.lock = {"url": "https://example.test/x", "sha256": make_kit.sha256(self.archive)}
        self.out = self.tmp / "out"
        self.out.mkdir()

    def test_gpl_modules_removed_others_kept(self):
        make_kit.unpack_python(self.archive, self.lock, self.out)
        self.assertEqual(make_kit.python_module_errors(self.out), [])
        self.assertTrue((self.out / "python/lib/python3.13/lib-dynload/_json.so").is_file())

    def test_sha_mismatch_refused(self):
        with self.assertRaises(make_kit.Fail):
            make_kit.unpack_python(self.archive, dict(self.lock, sha256="0" * 64), self.out)

    def test_missing_archive_names_url_and_sha(self):
        with self.assertRaises(make_kit.Fail) as c:
            make_kit.unpack_python(self.tmp / "none.tar.gz", self.lock, self.out)
        self.assertIn(self.lock["url"], str(c.exception))
        self.assertIn(self.lock["sha256"], str(c.exception))

    def test_install_uses_hashes_offline(self):
        calls = []
        bin_ = self.out / "python/bin"
        write(bin_ / "meson", "#!%s/python/bin/python3\nimport mesonbuild\n" % self.out, 0o755)
        write(bin_ / "pip", "#!/bin/sh\n'''exec' \"$(dirname -- \"$0\")/python3\" \"$0\" \"$@\"\n", 0o755)
        write(bin_ / "ninja", "\xcf\xfa\xed\xfe binary", 0o755)
        (bin_ / "python3").symlink_to("python3.13")
        make_kit.install_wheels(self.out, self.tmp / "w", Path("req.txt"), runner=calls.append)
        argv = calls[0]
        for flag in ("--require-hashes", "--only-binary", "--no-deps", "--no-index", "--no-compile", "-I", "-B"):
            self.assertIn(flag, argv)
        self.assertEqual(sorted(p.name for p in bin_.iterdir()), ["ninja", "pip", "python3"])
        self.assertEqual(make_kit.path_leak_errors(self.out), [])

    def test_install_drops_sh_wrapped_scripts_pip_writes_for_a_spaced_cut_path(self):
        out = self.tmp / "my kit"
        bin_ = out / "python/bin"
        write(bin_ / "meson", "#!/bin/sh\n'''exec' \"%s/python/bin/python3\" \"$0\" \"$@\"\n' '''\nimport mesonbuild\n" % out, 0o755)
        write(bin_ / "pip", "#!/bin/sh\n'''exec' \"$(dirname -- \"$0\")/python3\" \"$0\" \"$@\"\n", 0o755)
        (bin_ / "ninja").write_bytes(b"\xcf\xfa\xed\xfe" + str(out).encode())
        make_kit.install_wheels(out, self.tmp / "w", Path("req.txt"), runner=lambda a: "")
        self.assertEqual(sorted(p.name for p in bin_.iterdir()), ["ninja", "pip"])


class Unlicensed(Base):
    PATHS = (
        "python/lib/python3.13/lib-dynload/_tkinter.cpython-313-darwin.so",
        "python/lib/python3.13/lib-dynload/_dbm.cpython-313-darwin.so",
        "python/lib/libtcl9.0.dylib",
        "python/lib/libtcl9tk9.0.dylib",
        "python/lib/tcl9.0/init.tcl",
        "python/lib/tk9.0/button.tcl",
        "python/lib/itcl4.3.8/itcl.tcl",
        "python/lib/thread3.0.6/pkgIndex.tcl",
        "python/lib/python3.13/tkinter/__init__.py",
        "python/lib/python3.13/idlelib/a.py",
        "python/lib/python3.13/turtledemo/a.py",
        "python/lib/python3.13/turtle.py",
        "python/bin/idle3",
    )

    def test_each_unlicensed_part_is_refused(self):
        for i, rel in enumerate(self.PATHS):
            with self.subTest(rel=rel):
                k = fake_kit(self.tmp / str(i))
                write(k / rel)
                out = make_kit.check(k, fake_id, fake_deps)
                self.assertTrue(any("whose licence text" in e for e in out), out)

    def test_pure_python_readline_helper_is_not_a_gpl_module(self):
        write(self.kit / "python/lib/python3.13/_pyrepl/readline.py")
        self.assertEqual(self.errs(), [])

    def test_unpack_drops_every_unlicensed_part(self):
        tmp = Path(tempfile.mkdtemp(prefix="k3c-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        stage = tmp / "stage"
        write(stage / "python/lib/python3.13/lib-dynload/_json.so")
        write(stage / "python/lib/python3.13/_pyrepl/readline.py")
        write(stage / "python/lib/python3.13/LICENSE.txt")
        for rel in self.PATHS:
            write(stage / rel)
        archive = tmp / "py.tar.gz"
        with tarfile.open(archive, "w:gz") as t:
            t.add(stage / "python", arcname="python")
        out = tmp / "out"
        out.mkdir()
        make_kit.unpack_python(archive, {"url": "u", "sha256": make_kit.sha256(archive)}, out)
        self.assertEqual(make_kit.python_module_errors(out), [])
        self.assertEqual(sorted(p.name for p in (out / "python/lib").iterdir()), ["python3.13"])
        self.assertFalse(list((out / "python/bin").glob("idle*")))
        self.assertEqual(sorted(p.name for p in (out / "python/lib/python3.13").iterdir()), ["LICENSE.txt", "_pyrepl", "lib-dynload"])
        self.assertTrue((out / "python/lib/python3.13/lib-dynload/_json.so").is_file())
        self.assertTrue((out / "python/lib/python3.13/_pyrepl/readline.py").is_file())
        self.assertTrue((out / "python/lib/python3.13/LICENSE.txt").is_file())

    def test_pip_windows_launchers_are_dropped(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        d = tmp / "stage/python/lib/python3.13/site-packages/pip/_vendor/distlib"
        for n in ("t32.exe", "t64.exe", "t64-arm.exe", "w32.exe", "w64.exe", "w64-arm.exe", "scripts.py"):
            write(d / n)
        archive = tmp / "py.tar.gz"
        with tarfile.open(archive, "w:gz") as t:
            t.add(tmp / "stage/python", arcname="python")
        out = tmp / "out"
        out.mkdir()
        make_kit.unpack_python(archive, {"url": "u", "sha256": make_kit.sha256(archive)}, out)
        left = out / "python/lib/python3.13/site-packages/pip/_vendor/distlib"
        self.assertEqual(sorted(p.name for p in left.iterdir()), ["scripts.py"])


class PrebuiltSpecChecks(Base):
    def test_missing_lgpl_text_fails(self):
        (self.kit / "licenses/ffmpeg" / kit_app.LGPL_COPYING).unlink()
        self.has(kit_app.LGPL_COPYING)

    def test_empty_lgpl_text_fails(self):
        write(self.kit / "licenses/ffmpeg" / kit_app.LGPL_COPYING, "")
        self.has(kit_app.LGPL_COPYING)

    def test_missing_icd_fails(self):
        (self.kit / "prebuilt/icd" / kitconst.ICD_NAME).unlink()
        mf = self.kit / "prebuilt/manifest.json"
        m = json.loads(mf.read_text())
        del m["icd/" + kitconst.ICD_NAME]
        mf.write_text(json.dumps(m))
        self.has("prebuilt/icd/%s missing" % kitconst.ICD_NAME)

    def test_ffmpeg_libs_must_point_at_the_ffmpeg_licence_folder(self):
        mf = self.kit / "prebuilt/manifest.json"
        mf.write_text(mf.read_text().replace('"licenses/ffmpeg"', '"licenses/sdl3"'))
        self.has("licence must be licenses/ffmpeg")

    def test_dependency_outside_system_and_prebuilt_fails(self):
        out = make_kit.check(self.kit, fake_id, lambda p: ["/opt/homebrew/lib/libz.dylib"])
        self.assertTrue(any("depends on /opt/homebrew/lib/libz.dylib" in e for e in out), out)

    def test_rpath_dependency_missing_from_prebuilt_lib_fails(self):
        out = make_kit.check(self.kit, fake_id, lambda p: ["@rpath/libgone.dylib"])
        self.assertTrue(any("libgone.dylib, which is not in prebuilt/lib" in e for e in out), out)

    def test_otool_deps_drops_the_own_id_line(self):
        out = "x.dylib:\n\t@rpath/x.dylib (compatibility version 1)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1)\n"
        with mock.patch.object(subprocess, "run", return_value=mock.Mock(stdout=out, returncode=0)), \
                mock.patch.object(make_kit, "otool_id", lambda p: "@rpath/x.dylib"):
            self.assertEqual(make_kit.otool_deps("x.dylib"), ["/usr/lib/libSystem.B.dylib"])

    def test_otool_reads_only_the_arm64_slice_of_a_universal_file(self):
        fat = {
            "-L": "x.dylib (architecture x86_64):\n\t@rpath/x.dylib (c)\n\t/opt/homebrew/lib/libintel.dylib (c)\nx.dylib (architecture arm64):\n\t@rpath/x.dylib (c)\n\t/usr/lib/libSystem.B.dylib (c)\n",
            "-D": "x.dylib (architecture arm64):\n@rpath/x.dylib\nx.dylib (architecture x86_64):\n/opt/homebrew/lib/libintel.dylib\n",
            "-l": "x.dylib (architecture x86_64):\n  cmd LC_RPATH\n path /opt/homebrew/lib (offset 12)\nx.dylib (architecture arm64):\n  cmd LC_RPATH\n path @loader_path (offset 12)\n",
        }
        thin = {
            "-L": "x.dylib:\n\t@rpath/x.dylib (c)\n\t/usr/lib/libSystem.B.dylib (c)\n",
            "-D": "x.dylib:\n@rpath/x.dylib\n",
            "-l": "x.dylib:\n  cmd LC_RPATH\n path @loader_path (offset 12)\n",
        }

        def otool(argv, **kw):
            arm = argv[1:3] == ["-arch", "arm64"]
            return mock.Mock(stdout=(thin if arm else fat)[argv[-2]], returncode=0)

        with mock.patch.object(subprocess, "run", otool):
            self.assertEqual(make_kit.otool_id("x.dylib"), "@rpath/x.dylib")
            self.assertEqual(make_kit.otool_deps("x.dylib"), ["/usr/lib/libSystem.B.dylib"])
            self.assertEqual(make_kit.otool_rpaths("x.dylib"), ["@loader_path"])


def cc(out, src, *flags):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cc", "-o", str(out), "-Wl,-headerpad_max_install_names", *flags, "-x", "c", "-"], input=src, text=True, check=True, capture_output=True)


class StagePrebuilt(unittest.TestCase):
    BREW_ID = "/opt/homebrew/opt/zfake/lib/libzfake.1.dylib"

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="k3c-real-"))
        brew = cls.root / "Cellar/zfake/1.0/lib/libzfake.1.dylib"
        cc(brew, "int zf(void){return 1;}\n", "-dynamiclib", "-install_name", cls.BREW_ID)
        brew.chmod(0o444)
        lib = cls.root / "ffmpeg/lib"
        real = {"libavutil": ("60", "60.26.102", []), "libavcodec": ("62", "62.28.102", ["libavutil"]), "libavformat": ("62", "62.12.102", ["libavcodec", "libavutil"])}
        for stem, (major, full, deps) in real.items():
            links = [str(lib / ("%s.%s.dylib" % (d, real[d][1]))) for d in deps] + ([str(brew), "-Wl,-rpath,/opt/homebrew/lib"] if stem == "libavcodec" else [])
            src = "".join("int f_%s(void);" % d for d in deps) + "int f_%s(void){return %s;}\n" % (stem, "+".join(["0"] + ["f_%s()" % d for d in deps]))
            cc(lib / ("%s.%s.dylib" % (stem, full)), src, "-dynamiclib", "-install_name", "@rpath/%s.%s.dylib" % (stem, major), *links)
            (lib / ("%s.%s.dylib" % (stem, major))).symlink_to("%s.%s.dylib" % (stem, full))
            (lib / ("%s.dylib" % stem)).symlink_to("%s.%s.dylib" % (stem, major))
        write(cls.root / "ffmpeg/COPYING.LGPLv2.1", "lgpl\n")
        write(cls.root / "Cellar/zfake/1.0/LICENSE", "zfake\n")
        write(cls.root / "mesa/icd.d" / kitconst.ICD_NAME, json.dumps({"ICD": {"library_path": "/opt/homebrew/Cellar/mesa/1/lib/libvulkan_kosmickrisp.dylib"}}))
        write(cls.root / "mesa/license.rst", "mit\n")
        cc(cls.root / "mesa/lib/libvulkan_kosmickrisp.dylib", "int vk(void){return 1;}\n", "-dynamiclib", "-install_name", "/opt/homebrew/Cellar/mesa/1/lib/libvulkan_kosmickrisp.dylib")
        cls.lib = lib
        cls.brew = brew

    @classmethod
    def tearDownClass(cls):
        cls.brew.chmod(0o644)
        shutil.rmtree(cls.root, True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="k3c-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "kit"
        self.out.mkdir()

    def spec(self, names):
        return {"components": [
            {"name": "ffmpeg", "version": "8", "spdx": "LGPL-2.1", "licences": [str(self.root / "ffmpeg/COPYING.LGPLv2.1")],
             "files": [{"from": str(self.lib / n), "to": "lib/" + n} for n in names]},
            {"name": "zfake", "version": "1", "spdx": "MIT", "licences": [str(self.root / "Cellar/zfake/1.0/LICENSE")],
             "files": [{"from": str(self.brew),"to": "lib/libzfake.1.dylib"}]},
            {"name": "mesa", "version": "1", "spdx": "MIT", "licences": [str(self.root / "mesa/license.rst")],
             "files": [{"from": str(self.root / "mesa/icd.d" / kitconst.ICD_NAME), "to": "icd/" + kitconst.ICD_NAME},
                       {"from": str(self.root / "mesa/lib/libvulkan_kosmickrisp.dylib"), "to": "lib/libvulkan_kosmickrisp.dylib"}]},
        ]}

    def stage(self, names=("libavformat.dylib", "libavcodec.dylib", "libavutil.dylib"), runner=make_kit.run):
        spec = self.spec(list(names))
        make_kit.stage_prebuilt(self.out, spec, runner=runner)
        return spec

    def errors(self, spec):
        make_kit.write_notice(self.out, {"python": "3", "capstone": "5", "meson": "1", "ninja": "1"}, spec)
        return make_kit.prebuilt_errors(self.out, make_kit.otool_id) + make_kit.path_leak_errors(self.out, home=self.root)

    def test_aliases_stage_under_the_versioned_soname(self):
        self.stage(["libavformat.dylib", "libavformat.62.dylib", "libavcodec.dylib", "libavutil.60.dylib"])
        lib = self.out / "prebuilt/lib"
        self.assertEqual(sorted(p.name for p in lib.iterdir()), ["libavcodec.62.dylib", "libavformat.62.dylib", "libavutil.60.dylib", "libvulkan_kosmickrisp.dylib", "libzfake.1.dylib"])
        self.assertFalse(any(p.is_symlink() for p in lib.iterdir()))
        manifest = json.loads((self.out / "prebuilt/manifest.json").read_text())
        self.assertIn("lib/libavformat.62.dylib", manifest)
        self.assertTrue((self.out / "licenses/ffmpeg/COPYING.LGPLv2.1").is_file())

    def test_staged_names_match_kit_app_lgpl_libs(self):
        self.stage()
        staged = {p.name for p in (self.out / "prebuilt/lib").iterdir() if p.name.startswith("libav")}
        self.assertEqual(staged, set(kit_app.LGPL_LIBS))

    def test_ids_deps_and_rpaths_are_relocatable_and_signed(self):
        self.stage()
        lib = self.out / "prebuilt/lib"
        for p in lib.iterdir():
            self.assertEqual(make_kit.otool_id(p), "@rpath/" + p.name)
            for d in make_kit.otool_deps(p):
                self.assertTrue(d.startswith(("@rpath/",) + make_kit.SYSTEM_DEP_PREFIXES), (p.name, d))
            self.assertEqual([r for r in make_kit.otool_rpaths(p) if not r.startswith("@")], [], p.name)
            self.assertEqual(subprocess.run(["codesign", "--verify", "--strict", str(p)], capture_output=True).returncode, 0, p.name)
        self.assertIn("@rpath/libzfake.1.dylib", make_kit.otool_deps(lib / "libavcodec.62.dylib"))

    def test_staged_real_layout_passes_the_prebuilt_and_leak_checks(self):
        spec = self.stage()
        self.assertEqual(self.errors(spec), [])
        icd = json.loads((self.out / "prebuilt/icd" / kitconst.ICD_NAME).read_text())
        self.assertEqual(icd["ICD"]["library_path"], "../lib/libvulkan_kosmickrisp.dylib")

    def test_without_relink_the_homebrew_dependency_is_caught(self):
        def id_only(argv):
            if argv[0] == "install_name_tool":
                argv = ["install_name_tool", "-id", argv[2], argv[-1]]
            return make_kit.run(argv)

        spec = self.stage(runner=id_only)
        errs = self.errors(spec)
        self.assertTrue(any("depends on %s" % self.BREW_ID in e for e in errs), errs)
        self.assertTrue(any("absolute rpath /opt/homebrew/lib" in e for e in errs), errs)

    def test_component_without_a_licence_text_fails(self):
        spec = self.spec(["libavformat.dylib"])
        spec["components"][0]["licences"] = [str(self.tmp / "nope")]
        with self.assertRaises(make_kit.Fail):
            make_kit.stage_prebuilt(self.out, spec, runner=lambda a: "")


class RealRepo(unittest.TestCase):
    def test_head_archive_has_no_game_data(self):
        tmp = Path(tempfile.mkdtemp(prefix="k3c-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        r = subprocess.run(["git", "-C", str(kitconst.REPO), "archive", "--format=tar", "HEAD", "--"] + make_kit.EXCLUDES, capture_output=True)
        if r.returncode != 0:
            self.skipTest("no git")
        tar = tmp / "a.tar"
        tar.write_bytes(r.stdout)
        with tarfile.open(tar) as t:
            t.extractall(tmp / "src")
        self.assertEqual(make_kit.game_data_errors(tmp / "src"), [])


if __name__ == "__main__":
    unittest.main()
