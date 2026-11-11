#!/usr/bin/env python3
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_app


def failing(app):
    buf = io.StringIO()
    with redirect_stdout(buf):
        kit_app.check(app)
    return {l.split()[1] for l in buf.getvalue().splitlines() if l.startswith("FAIL")}


def sh(*cmd):
    subprocess.run([str(c) for c in cmd], check=True, capture_output=True)


def m_homebrew_install_name(app):
    sh("install_name_tool", "-change", "@rpath/libSDL3.0.dylib", "/opt/homebrew/opt/sdl3/lib/libSDL3.0.dylib", app / "Contents/MacOS/bloodlines-native")


def m_host_dylib_id(app):
    sh("install_name_tool", "-id", "/opt/homebrew/opt/sdl3/lib/libSDL3.0.dylib", app / "Contents/Frameworks/libSDL3.0.dylib")


def m_homebrew_rpath(app):
    sh("install_name_tool", "-add_rpath", "/opt/homebrew/lib", app / "Contents/MacOS/bloodlines-native")


def m_unsigned(app):
    sh("codesign", "--remove-signature", app)


def m_tampered_dylib(app):
    p = app / "Contents/Frameworks/libSDL3.0.dylib"
    data = bytearray(p.read_bytes())
    data[len(data) // 2] ^= 1
    p.write_bytes(bytes(data))


def m_absolute_icd(app):
    p = app / "Contents/Resources" / kit_app.ICD_NAME
    d = json.loads(p.read_text())
    d["ICD"]["library_path"] = "/opt/homebrew/Cellar/mesa/26.2.5/lib/libvulkan_kosmickrisp.dylib"
    p.write_text(json.dumps(d))


def m_icd_dangling(app):
    p = app / "Contents/Resources" / kit_app.ICD_NAME
    d = json.loads(p.read_text())
    d["ICD"]["library_path"] = "../Frameworks/missing.dylib"
    p.write_text(json.dumps(d))


def m_wrapper_missing(app):
    (app / "Contents/MacOS" / kit_app.WRAPPER_NAME).unlink()


def m_wrapper_not_executable(app):
    os.chmod(app / "Contents/MacOS" / kit_app.WRAPPER_NAME, 0o644)


def m_wrapper_elsewhere(app):
    (app / "Contents/MacOS" / kit_app.WRAPPER_NAME).write_text("#!/bin/sh\nexec /usr/bin/true\n")


def rewrite_wrapper(app, old, new):
    assert old in kit_app.WRAPPER
    (app / "Contents/MacOS" / kit_app.WRAPPER_NAME).write_text(kit_app.WRAPPER.replace(old, new))


def m_wrapper_no_exec(app):
    rewrite_wrapper(app, 'exec /usr/bin/python3 -I -B "$DIR/../Resources/kit/launcher.py" run', '/usr/bin/python3 -I -B "$DIR/../Resources/kit/launcher.py" run')


def m_wrapper_no_args(app):
    rewrite_wrapper(app, 'run --alert "$@"', "run --alert")


def m_wrapper_other_python(app):
    rewrite_wrapper(app, "/usr/bin/python3", "/opt/homebrew/bin/python3")


def m_wrapper_no_tools_check(app):
    rewrite_wrapper(app, "xcode-select -p", "true")


def m_launcher_not_bundled(app):
    (app / kit_app.KIT_DIR / "launcher.py").unlink()


def m_manifest_not_bundled(app):
    (app / kit_app.KIT_DIR / kit_app.MANIFEST_NAME).unlink()


def m_bad_json(app):
    (app / "Contents/Resources/broken.json").write_text("{")


def m_drop_licence(app):
    shutil.rmtree(app / "Contents/Resources/licenses/sdl3")


def m_drop_notice_row(app):
    p = app / "Contents/Resources/NOTICE.md"
    p.write_text("\n".join(l for l in p.read_text().splitlines() if "libzstd" not in l) + "\n")


def m_bytecode(app):
    d = app / kit_app.KIT_DIR / "__pycache__"
    d.mkdir()
    (d / "fonts.cpython-313.pyc").write_bytes(b"x")


def m_drop_dylib(app):
    (app / "Contents/Frameworks/libSDL3.0.dylib").unlink()


MUTATIONS = [
    (m_homebrew_install_name, {"install-names", "no-host-paths"}),
    (m_host_dylib_id, {"install-names", "no-host-paths"}),
    (m_homebrew_rpath, {"rpaths", "no-host-paths"}),
    (m_unsigned, {"codesign"}),
    (m_tampered_dylib, {"codesign"}),
    (m_absolute_icd, {"icd-manifest"}),
    (m_icd_dangling, {"icd-manifest"}),
    (m_wrapper_missing, {"wrapper", "bundle-layout"}),
    (m_wrapper_not_executable, {"wrapper"}),
    (m_wrapper_elsewhere, {"wrapper"}),
    (m_wrapper_no_exec, {"wrapper"}),
    (m_wrapper_no_args, {"wrapper"}),
    (m_wrapper_other_python, {"wrapper"}),
    (m_wrapper_no_tools_check, {"wrapper"}),
    (m_launcher_not_bundled, {"wrapper"}),
    (m_manifest_not_bundled, {"wrapper"}),
    (m_bad_json, {"icd-manifest"}),
    (m_drop_licence, {"notice-licences"}),
    (m_drop_notice_row, {"notice-licences"}),
    (m_drop_dylib, {"install-names", "notice-licences"}),
    (m_bytecode, {"no-bytecode"}),
]


def refusal(name, out, build, want):
    r = subprocess.run([sys.executable, kit_app.__file__, "build", "--build", str(build), "--out", str(out)], capture_output=True, text=True)
    ok = r.returncode != 0 and want in r.stderr
    print("%s %s -> exit %d: %s" % ("CAUGHT" if ok else "MISSED", name, r.returncode, r.stderr.strip()[:80]))
    return not ok


def refusals(scratch):
    bad = 0
    with tempfile.TemporaryDirectory(dir=scratch) as t:
        fake = Path(t) / "build"
        fake.mkdir()
        (fake / "summary.json").write_text('{"state": "fail"}')
        miscased = str(kit_app.REPO).swapcase() + "/zz-out"
        inside = str(kit_app.REPO / "tools/kit/zz-out")
        bad += refusal("out inside repo", inside, fake, "inside the repo")
        bad += refusal("out inside repo, mis-cased", miscased, fake, "inside the repo")
        bad += refusal("summary state not pass", Path(t) / "out", fake, "not pass")
        bad += refusal("summary.json missing", Path(t) / "out", Path(t) / "nope", "cannot read summary.json")
        main_checkout = Path(os.path.join(kit_app.REPO, kit_app.git_out(kit_app.REPO, "rev-parse", "--git-common-dir"))).parent
        bad += refusal("out inside main checkout", main_checkout / "zz-out", fake, "inside the repo")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app", help="a built Bloodlines Native.app that passes the check")
    ap.add_argument("--scratch", default=tempfile.gettempdir())
    a = ap.parse_args()
    os.makedirs(a.scratch, exist_ok=True)
    app = Path(a.app)
    assert not failing(app), "baseline app must pass"
    bad = refusals(a.scratch)
    for fn, want in MUTATIONS:
        with tempfile.TemporaryDirectory(dir=a.scratch) as t:
            copy = Path(t) / kit_app.APP_NAME
            shutil.copytree(app, copy, symlinks=True)
            fn(copy)
            if fn not in (m_unsigned, m_tampered_dylib):
                kit_app.sign(copy)
            got = failing(copy)
        ok = want <= got
        bad += not ok
        print("%s %s -> failing: %s" % ("CAUGHT" if ok else "MISSED", fn.__name__, ", ".join(sorted(got)) or "none"))
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
