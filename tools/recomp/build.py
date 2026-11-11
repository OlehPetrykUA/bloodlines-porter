import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_all
import devpaths

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PY = [sys.executable, "-I", "-B"]
GHIDRA = "/opt/homebrew/opt/ghidra/libexec/support/analyzeHeadless"
JAVA_HOME = "/opt/homebrew/opt/openjdk@25/libexec/openjdk.jdk/Contents/Home"


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kw).returncode


def ghidra_names(dll, out):
    project = devpaths.ghidra_project()
    if os.path.exists(out) or not project:
        return 0
    env = dict(os.environ, JAVA_HOME=os.environ.get("JAVA_HOME", JAVA_HOME))
    return run([GHIDRA, project, "bloodlines", "-process", os.path.basename(dll), "-noanalysis", "-readOnly",
                "-postScript", "ExportNames.java", out, "-scriptPath", os.path.join(REPO, "tools", "recomp")],
               env=env, stdout=subprocess.DEVNULL)


def pipeline(dll, layout, out, names):
    os.makedirs(out, exist_ok=True)
    extra = os.path.join(out, "extra-entries.txt")
    if not os.path.exists(extra):
        open(extra, "w").close()
    rc = run(PY + [os.path.join(REPO, "tools", "recomp", "test_discover.py")]) or run(
        PY + [os.path.join(REPO, "tools", "recomp", "test_xflags.py"), os.path.join(out, "xflagtest")]) or run(
        PY + [os.path.join(REPO, "tools", "recomp", "translate.py"), dll, "--layout", layout,
                   "--module", "vstdlib", "--out", out, "--names", names, "--extra-entries", extra])
    if rc:
        return rc
    meson = os.path.join(out, "meson")
    rc = build_all.meson_setup(os.path.join(REPO, "runtime"), meson, [f"-Dgen_dir={os.path.join(out, 'gen')}"])
    if rc:
        return rc
    rc = run(["ninja", "-C", meson])
    if rc:
        return rc
    h = [os.path.join(REPO, "harness", "run.py")]
    common = ["--lib", os.path.join(meson, "libvstdlib.dylib"), "--module", os.path.join(out, "module.json"),
              "--inputs", os.path.join(REPO, "harness", "inputs.json"), "--recordings", os.path.join(out, "oracle.json"),
              "--results", os.path.join(out, "native-results.json"), "--extra", extra]
    for mode in ("record", "check", "aborts", "shims"):
        rc = run(PY + h + [mode] + common)
        if rc:
            return rc
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dll", nargs="?", default=os.path.join(devpaths.GAME, "Bin", "vstdlib.dll"))
    ap.add_argument("out", nargs="?", default=devpaths.build("vstdlib"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    names = os.path.join(a.out, "ghidra-names.tsv")
    rc = ghidra_names(a.dll, names)
    if rc:
        return rc
    rc = pipeline(a.dll, os.path.join(REPO, "layout.toml"), a.out, names)
    if rc:
        return rc
    rc = pipeline(a.dll, os.path.join(REPO, "harness", "layout-moved.toml"), os.path.join(a.out, "moved"), names)
    print("ALL PASS" if rc == 0 else f"FAILED ({rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
