#!/usr/bin/env python3
import argparse
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
sys.path.insert(0, str(KIT))
import appicon
import kitconst
APP_NAME = kitconst.KIT_APP_NAME
EXECUTABLE = kitconst.KIT_TITLE
PRODUCT = "BloodlinesKit"
BUNDLE_ID_PLACEHOLDER = kitconst.KIT_BUNDLE_ID
COPYRIGHT_KEY = "NSHumanReadableCopyright"
COPYRIGHT = kitconst.KIT_TITLE + " contains no game data. You supply your own copy of Vampire: The Masquerade - Bloodlines."
PORTING_KIT = "porting-kit"
KIT_FILES = ("python.lock", "requirements-build.txt")


def build(build_path):
    base = ["swift", "build", "-c", "release", "--package-path", str(HERE), "--build-path", str(build_path),
            "-Xswiftc", "-file-prefix-map", "-Xswiftc", "%s=." % KIT.parents[1],
            "-Xlinker", "-S"]
    subprocess.run(base + ["--product", PRODUCT], check=True)
    bin_dir = Path(subprocess.run(base + ["--show-bin-path"], check=True, capture_output=True, text=True).stdout.strip())
    data = (bin_dir / PRODUCT).read_bytes()
    for p in (Path.home(), Path(build_path).resolve()):
        if str(p).encode() in data:
            sys.exit("%s holds a host path: %s" % (bin_dir / PRODUCT, p))
    return bin_dir


def info(bundle_id, version):
    return {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": EXECUTABLE,
        "CFBundleIdentifier": bundle_id,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": kitconst.KIT_TITLE,
        "CFBundleDisplayName": kitconst.KIT_TITLE,
        "CFBundleIconFile": kitconst.ICON_FILE,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "26.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        COPYRIGHT_KEY: COPYRIGHT,
    }


def assemble(out, bin_dir, bundle_id, version, kit_folder=None):
    app = out / APP_NAME
    if app.exists():
        sys.exit("%s already exists: move it away first" % app)
    macos = app / "Contents" / "MacOS"
    kit = app / "Contents" / "Resources" / "kit"
    macos.mkdir(parents=True)
    kit.mkdir(parents=True)
    shutil.copy2(bin_dir / PRODUCT, macos / EXECUTABLE)
    for f in sorted(KIT.iterdir()):
        if (f.suffix == ".py" and not f.name.startswith("test_")) or f.name in KIT_FILES:
            shutil.copy2(f, kit / f.name)
    for name in kitconst.KIT_REPO_TOOLS:
        shutil.copy2(KIT.parent / name, kit / name)
    for name in kitconst.UP_TOOLS:
        shutil.copy2(KIT.parent / "up" / name, kit / name)
    (kit / "manifests").mkdir()
    shutil.copy2(KIT.parent / "up" / "manifests" / kitconst.UP_MANIFEST, kit / "manifests" / kitconst.UP_MANIFEST)
    shutil.copy2(KIT.parent.parent / "docs" / kitconst.MANIFEST_NAME, kit / kitconst.MANIFEST_NAME)
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump(info(bundle_id, version), fh)
    appicon.porter_icon(app / "Contents" / "Resources" / kitconst.ICON_FILE)
    if kit_folder:
        shutil.copytree(kit_folder, app / "Contents" / "Resources" / PORTING_KIT, symlinks=True)
    subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(app)], check=True)
    return app


def main(argv=None):
    p = argparse.ArgumentParser(description="Build release and assemble an ad-hoc signed " + APP_NAME)
    p.add_argument("out", type=Path, help="folder that receives " + APP_NAME)
    p.add_argument("--kit-folder", type=Path, help="a cut kit folder (make_kit.py); copied into Contents/Resources/porting-kit before signing")
    p.add_argument("--bundle-id", default=BUNDLE_ID_PLACEHOLDER)
    p.add_argument("--version", default="0.1")
    p.add_argument("--build-path", type=Path, default=HERE / ".build")
    a = p.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", a.bundle_id):
        p.error("--bundle-id must be a reverse-DNS name")
    if a.kit_folder and not (a.kit_folder / "src/tools/kit/kit.py").is_file():
        p.error("--kit-folder must hold src/tools/kit/kit.py (a cut kit)")
    a.out.mkdir(parents=True, exist_ok=True)
    app = assemble(a.out.resolve(), build(a.build_path), a.bundle_id, a.version, a.kit_folder)
    print(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
