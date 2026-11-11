#!/usr/bin/env python3
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import package


def main():
    build_path = Path(os.environ.get("KIT_BUILD_PATH", HERE / ".build"))
    bad = 0

    def check(ok, what):
        nonlocal bad
        bad += not ok
        print("%s %s" % ("ok" if ok else "FAIL", what))

    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "dist"
        r = subprocess.run([sys.executable, "-I", "-B", str(HERE / "package.py"), str(out), "--build-path", str(build_path)], capture_output=True, text=True)
        check(r.returncode == 0, "package.py exits 0 " + r.stderr[-400:])
        app = out / package.APP_NAME
        check(r.stdout.strip().splitlines()[-1:] == [str(app.resolve())], "prints the bundle path")
        exe = app / "Contents" / "MacOS" / package.EXECUTABLE
        check(exe.is_file() and os.access(exe, os.X_OK), "executable in Contents/MacOS")
        data = exe.read_bytes() if exe.is_file() else b""
        check(str(Path.home()).encode() not in data and str(build_path.resolve()).encode() not in data, "no home or build path in the executable")
        check((app / "Contents" / "Resources" / "kit" / "launcher.py").is_file(), "launcher.py bundled where the app looks for it")
        check(not any(p.name.startswith("test_") for p in (app / "Contents" / "Resources" / "kit").iterdir()), "no kit tests bundled")
        bundled = app / "Contents" / "Resources" / "kit"
        check(all((bundled / n).is_file() for n in package.kitconst.KIT_REPO_TOOLS), "kitconst.KIT_REPO_TOOLS bundled beside the kit scripts")
        home = Path(t) / "scratch-home"
        home.mkdir()
        s = subprocess.run(["/usr/bin/python3", "-I", "-B", str(bundled / "launcher.py"), "status", "--home", str(home), "--overlay", str(home), "--bundle", str(home / "none.app")], capture_output=True, text=True)
        check(s.returncode == 0, "bundled launcher.py status runs from the bundle alone " + s.stderr[-300:])
        extra = package.kitconst.UP_TOOLS + (package.kitconst.MANIFEST_NAME, "manifests/" + package.kitconst.UP_MANIFEST)
        check(all((bundled / n).is_file() for n in extra), "UP tools, UP manifest and game manifest bundled where the launcher looks")
        h = subprocess.run(["/usr/bin/python3", "-I", "-B", str(bundled / "launcher.py"), "check", "--help"], capture_output=True, text=True)
        check(h.returncode == 0 and "--game" in h.stdout, "bundled launcher.py check --help " + h.stderr[-300:])
        game = Path(t) / "tiny-game"
        (game / "Bin").mkdir(parents=True)
        (game / "vampire.exe").write_bytes(b"exe")
        g = subprocess.run(["/usr/bin/python3", "-I", "-B", str(bundled / "launcher.py"), "check", "--game", str(game), "--home", str(home), "--overlay", str(home)], capture_output=True, text=True)
        check(g.returncode == 6 and "Traceback" not in g.stderr and "missing" in (g.stdout + g.stderr), "bundled launcher.py check on a tiny game fails cleanly against the bundled manifest " + g.stderr[-300:])
        plist = app / "Contents" / "Info.plist"
        check(subprocess.run(["plutil", "-lint", str(plist)], capture_output=True).returncode == 0, "plutil -lint")
        with open(plist, "rb") as fh:
            info = plistlib.load(fh)
        check(info.get("CFBundleName") == package.kitconst.KIT_TITLE, "CFBundleName")
        check(info.get("CFBundleExecutable") == package.EXECUTABLE, "CFBundleExecutable")
        check(info.get("CFBundleIdentifier") == package.BUNDLE_ID_PLACEHOLDER, "CFBundleIdentifier placeholder")
        check(info.get("LSMinimumSystemVersion") == "26.0", "LSMinimumSystemVersion")
        check(bool(info.get(package.COPYRIGHT_KEY)), "NSHumanReadableCopyright")
        v = subprocess.run(["codesign", "--verify", "--strict", "--verbose=2", str(app)], capture_output=True, text=True)
        check(v.returncode == 0, "codesign --verify --strict " + v.stderr.strip())
        d = subprocess.run(["codesign", "-d", "--verbose=2", str(app)], capture_output=True, text=True)
        check("Signature=adhoc" in d.stderr, "ad-hoc signature")
        again = subprocess.run([sys.executable, "-I", "-B", str(HERE / "package.py"), str(out), "--build-path", str(build_path)], capture_output=True, text=True)
        check(again.returncode != 0 and "already exists" in again.stderr, "refuses to overwrite an existing bundle")
        exe.write_bytes(exe.read_bytes() + b"\0")
        check(subprocess.run(["codesign", "--verify", "--strict", str(app)], capture_output=True).returncode != 0, "tampered bundle fails verify")
        bad_id = subprocess.run([sys.executable, "-I", "-B", str(HERE / "package.py"), str(Path(t) / "x"), "--bundle-id", "no spaces", "--build-path", str(build_path)], capture_output=True, text=True)
        check(bad_id.returncode == 2, "rejects a malformed bundle id")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
