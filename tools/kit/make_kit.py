import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kitconst
from kit_app import EULA_NAME, EULA_TEXT, FFMPEG_SRC_SHA256, FFMPEG_VERSION, LGPL_COPYING

HERE = Path(__file__).resolve().parent
LOCK = HERE / "python.lock"
REQUIREMENTS = HERE / "requirements-build.txt"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
ROW = re.compile(r"^\| `([^`]+)` \|.*\| (licenses/[^|\s/]+)/ \|$")
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==(\S+)$")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
LICENCE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|notice)")
BUILD_COMPONENTS = ("python", "capstone", "meson", "ninja")
GPL_MODULE = re.compile(r"^(_gdbm|readline)")
WIN_LAUNCHERS = ("lib/python3.*/site-packages/pip/_vendor/distlib", re.compile(r"^[tw](32|64)(-arm)?\.exe$"))
UNLICENSED = [
    ("lib/python3.*/lib-dynload", re.compile(r"^(_tkinter|_dbm)")),
    ("lib/python3.*", re.compile(r"^(tkinter|idlelib|turtle|turtledemo)(\.py)?$")),
    ("lib", re.compile(r"^(lib)?(tcl|tk|itcl|tdbc|thread)")),
    ("bin", re.compile(r"^idle")),
]
TARBALL = "ffmpeg-%s.tar.xz" % FFMPEG_VERSION
WRAPPER = (
    "#!/bin/sh\n"
    'DIR="$(cd "$(dirname "$0")" && pwd)"\n'
    'exec "$DIR/python/bin/python3" -I -B "$DIR/src/tools/kit/kit.py" "$@"\n'
)
GAME_EXT = frozenset(
    ".vpk .bsp .dll .exe .bik .bk2 .tth .ttz .mdl .vtx .vvd .phy .wav .mp3 .ogg .vtf .vmt .fnt .gcf .nuc .cache .mp4 .avi".split()
)
GAME_MAGIC = (b"MZ", b"\x34\x12\xaa\x55", b"VBSP", b"IBSP", b"RIFF", b"BIK", b"OggS", b"ID3")
BAD_DIRS = frozenset(("vendor", "venv", "build", "ghidra", "reference", "vanilla"))
EXCLUDES = [
    ".",
    ":(exclude)tools/ghidra",
    ":(exclude)docs/ghidra-scout.md",
    ":(exclude)docs/inventory",
    ":(exclude)docs/inventory.md",
    ":(exclude)docs/vpk-inventory.md",
    ":(exclude)docs/m4-probe.md",
    ":(exclude).github",
]
MAX_SOURCE_FILE = 16 * 1024 * 1024
SYSTEM_DEP_PREFIXES = ("/usr/lib/", "/System/Library/")
LICENCES = "licenses"
STATIC_LICENCES = HERE / "licences" / "python"
STATIC_LIBS = "static-libs.json"
PYTHON_STATIC_LIBS = ("openssl", "expat", "mpdecimal", "bzip2", "libffi", "liblzma", "sqlite")
MACHO_MAGIC = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


class Fail(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lock_errors(lock_text, req_text):
    errs = []
    try:
        lock = json.loads(lock_text)
    except ValueError as e:
        return ["python.lock is not JSON: %s" % e]
    for k in ("tag", "url", "sha256"):
        if not lock.get(k):
            errs.append("python.lock has no %s" % k)
    url = lock.get("url", "")
    if url and not (url.startswith("https://") and "cpython-3.13" in url and "aarch64-apple-darwin" in url and "install_only" in url):
        errs.append("python.lock url is not a CPython 3.13 aarch64-apple-darwin install_only archive")
    if lock.get("sha256") and not SHA64.match(lock["sha256"]):
        errs.append("python.lock sha256 is not 64 hex")
    seen = {}
    req = req_text.replace("\\\n", " ")
    for line in req.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        m = PIN.match(parts[0])
        if not m:
            errs.append("requirements-build.txt: %r is not pinned with ==" % parts[0])
            continue
        hashes = HASH.findall(line)
        if not hashes:
            errs.append("requirements-build.txt: %s has no --hash=sha256:" % m.group(1))
        seen[m.group(1).lower()] = hashes
    for n in ("capstone", "meson", "ninja"):
        if n not in seen:
            errs.append("requirements-build.txt lacks %s" % n)
    return errs


def game_data_errors(root):
    errs = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        for d in dirnames:
            if d.lower() in BAD_DIRS:
                errs.append("%s: directory %s must not ship" % (rel / d, d))
        for n in filenames:
            p = Path(dirpath) / n
            r = rel / n
            low = n.lower()
            if p.suffix.lower() in GAME_EXT:
                errs.append("%s: game data extension %s" % (r, p.suffix.lower()))
            elif low.endswith((".gpr", ".rep")) or "analyzeheadless" in low:
                errs.append("%s: Ghidra file" % r)
            elif p.is_symlink() or not p.is_file():
                continue
            else:
                if p.stat().st_size > MAX_SOURCE_FILE:
                    errs.append("%s: %d bytes is over the %d limit" % (r, p.stat().st_size, MAX_SOURCE_FILE))
                with open(p, "rb") as f:
                    head = f.read(4)
                for m in GAME_MAGIC:
                    if head.startswith(m):
                        errs.append("%s: starts with game or media magic %r" % (r, m))
                        break
    return errs


def python_paths(kit, patterns):
    py = Path(kit) / "python"
    found = []
    for where, rx in patterns:
        for d in (x for x in py.glob(where) if x.is_dir()):
            found +=[p for p in d.iterdir() if rx.match(p.name)]
    return found


def python_module_errors(kit):
    errs = []
    for p in python_paths(kit, [("lib/python3.*/lib-dynload", GPL_MODULE)]):
        errs.append("python has GPL module %s" % p.relative_to(kit))
    for p in python_paths(kit, UNLICENSED):
        errs.append("python ships %s, whose licence text the kit does not carry" % p.relative_to(kit))
    return errs


def notice_rows(text):
    rows = {"Build": {}, "Runtime": {}}
    section = None
    for l in text.splitlines():
        if l.startswith("## "):
            section = l[3:].split()[0]
        m = ROW.match(l)
        if m and section in rows:
            rows[section][m.group(1)] = m.group(2)
    return rows


def static_licence_errors(d, python_tag=None):
    d = Path(d)
    try:
        meta = json.loads((d / STATIC_LIBS).read_text())
        libs = meta["libs"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["%s/%s is missing or not a libs table" % (d.name, STATIC_LIBS)]
    errs = []
    if python_tag is not None and meta.get("python_build") != python_tag:
        errs.append("%s is for python build %s, python.lock has %s" % (STATIC_LIBS, meta.get("python_build"), python_tag))
    for n in PYTHON_STATIC_LIBS:
        text = d / n / "LICENSE.txt"
        e = libs.get(n) if isinstance(libs, dict) else None
        if not isinstance(e, dict) or not e.get("version") or not e.get("text_from"):
            errs.append("%s has no version and text_from for %s" % (STATIC_LIBS, n))
        elif not text.is_file() or text.stat().st_size == 0:
            errs.append("licenses/python/%s/LICENSE.txt is missing" % n)
        elif sha256(text) != e.get("sha256"):
            errs.append("licenses/python/%s/LICENSE.txt does not match the sha256 in %s" % (n, STATIC_LIBS))
    return errs


def collect_static_licences(out, python_tag, src=STATIC_LICENCES):
    errs = static_licence_errors(src, python_tag)
    if errs:
        raise Fail("\n".join(errs))
    shutil.copytree(src, Path(out) / LICENCES / "python", dirs_exist_ok=True)


def licence_errors(kit):
    kit = Path(kit)
    notice = kit / "NOTICE.md"
    if not notice.is_file():
        return ["NOTICE.md missing"]
    rows = notice_rows(notice.read_text())
    errs = static_licence_errors(kit / LICENCES / "python")
    for n in BUILD_COMPONENTS:
        if n not in rows["Build"]:
            errs.append("NOTICE.md has no Build row for " + n)
    for kind in ("Build", "Runtime"):
        for n, folder in sorted(rows[kind].items()):
            d = kit / folder
            if not any(f.is_file() and f.stat().st_size > 0 for f in d.glob("*")):
                errs.append("%s row %s: no licence text in %s/" % (kind, n, folder))
    return errs


def prebuilt_errors(kit, dylib_id, dylib_deps=None, rpaths=None):
    kit = Path(kit)
    dylib_deps, rpaths = dylib_deps or otool_deps, rpaths or otool_rpaths
    pre = kit / "prebuilt"
    mf = pre / "manifest.json"
    if not mf.is_file():
        return ["prebuilt/manifest.json missing"]
    try:
        manifest = json.loads(mf.read_text())
    except ValueError as e:
        return ["prebuilt/manifest.json is not JSON: %s" % e]
    errs = []
    rows = notice_rows((kit / "NOTICE.md").read_text()) if (kit / "NOTICE.md").is_file() else {"Runtime": {}}
    files = {str(p.relative_to(pre)) for p in pre.rglob("*") if p.is_file() and p != mf}
    for f in sorted(files - set(manifest)):
        errs.append("prebuilt/%s is not in manifest.json" % f)
    for f in sorted(set(manifest) - files):
        errs.append("manifest.json lists %s but the file is missing" % f)
    for f, e in sorted(manifest.items()):
        for k in ("component", "version", "spdx", "licence"):
            if not e.get(k):
                errs.append("manifest.json %s has no %s" % (f, k))
        if e.get("component") and e["component"] not in rows["Runtime"]:
            errs.append("NOTICE.md has no Runtime row for " + e["component"])
        if f.endswith(".dylib") and f in files:
            want = "@rpath/" + Path(f).name
            got = dylib_id(pre / f)
            if got != want:
                errs.append("prebuilt/%s id is %s, need %s" % (f, got, want))
            for dep in dylib_deps(pre / f):
                name = dep[len("@rpath/"):] if dep.startswith("@rpath/") else None
                if name is None:
                    if not dep.startswith(SYSTEM_DEP_PREFIXES):
                        errs.append("prebuilt/%s depends on %s, outside the system and prebuilt/lib" % (f, dep))
                elif not (pre / "lib" / name).is_file():
                    errs.append("prebuilt/%s depends on %s, which is not in prebuilt/lib" % (f, dep))
            for rp in rpaths(pre / f):
                if not rp.startswith("@"):
                    errs.append("prebuilt/%s has the absolute rpath %s" % (f, rp))
        if f.startswith("icd/") and f.endswith(".json") and f in files:
            try:
                lp = json.loads((pre / f).read_text())["ICD"]["library_path"]
            except (ValueError, KeyError, TypeError):
                lp = None
            if not lp or lp.startswith("/"):
                errs.append("prebuilt/%s library_path %r is not a relative path" % (f, lp))
            elif os.path.normpath((pre / f).parent / lp) != os.path.normpath(pre / "lib" / Path(lp).name) or not (pre / "lib" / Path(lp).name).is_file():
                errs.append("prebuilt/%s library_path %r is not a file in prebuilt/lib" % (f, lp))
        if Path(f).name.startswith("libav") and e.get("licence") != "%s/ffmpeg" % LICENCES:
            errs.append("prebuilt/%s licence must be %s/ffmpeg" % (f, LICENCES))
    copying = kit / LICENCES / "ffmpeg" / LGPL_COPYING
    if not copying.is_file() or copying.stat().st_size == 0:
        errs.append("%s/ffmpeg/%s missing" % (LICENCES, LGPL_COPYING))
    if not (pre / "icd" / kitconst.ICD_NAME).is_file():
        errs.append("prebuilt/icd/%s missing" % kitconst.ICD_NAME)
    return errs


TEMP_PATH = rb"/var/" + rb"folders/"
HOST_SHEBANG = re.compile(rb"^#!\s*(/Users/|/opt/homebrew|/usr/local/|/private/|/var/" rb"folders/|/Volumes/)")


def path_leak_errors(kit, home=None):
    kit = Path(kit)
    roots = {str(home if home is not None else Path.home()).rstrip("/"), str(kit.resolve()).rstrip("/"), str(kit.absolute()).rstrip("/")}
    mine = [re.escape(r.encode() + b"/") for r in sorted(roots) if r]
    rx, src_rx = re.compile(rb"|".join(mine + [TEMP_PATH])), re.compile(rb"|".join(mine))
    errs = []
    for dirpath, _, filenames in os.walk(kit):
        for n in filenames:
            p = Path(dirpath) / n
            rel = p.relative_to(kit)
            if p.is_symlink():
                data = os.readlink(p).encode()
            elif p.is_file():
                data = p.read_bytes()
            else:
                continue
            m = (src_rx if rel.parts[0] == "src" else rx).search(data) or HOST_SHEBANG.search(data[:256])
            if m:
                errs.append("%s holds the host path %s" % (rel, data[m.start():m.start() + 80].split(b"\0")[0].split(b"\n")[0].decode(errors="replace")))
    return errs


def check(kit, dylib_id=None, dylib_deps=None, home=None, rpaths=None, leaks=True):
    kit = Path(kit)
    dylib_id = dylib_id or otool_id
    errs = []
    src = kit / "SOURCE"
    if not src.is_file() or not SHA40.match(src.read_text().strip()):
        errs.append("SOURCE is missing or not a 40-hex commit")
    if not (kit / "src").is_dir() or not any((kit / "src").iterdir()):
        errs.append("src/ is missing or empty")
    for top in ("src", "prebuilt", "sources"):
        if (kit / top).is_dir():
            errs += ["%s/%s" % (top, e) for e in game_data_errors(kit / top)]
    for name in (kitconst.CLI_NAME,) + kitconst.CLI_ALIASES:
        w = kit / name
        if not w.is_file() or not os.access(w, os.X_OK) or 'python/bin/python3" -I' not in w.read_text():
            errs.append("%s wrapper is missing, not executable or not on the bundled python -I" % name)
    if not (kit / "python/bin/python3").is_file():
        errs.append("python/bin/python3 missing")
    eula = kit / EULA_NAME
    if not eula.is_file() or "LGPLv2.1" not in eula.read_text(errors="replace"):
        errs.append("%s is missing or does not state the FFmpeg LGPLv2.1 use" % EULA_NAME)
    errs += python_module_errors(kit)
    errs += licence_errors(kit)
    errs += prebuilt_errors(kit, dylib_id, dylib_deps, rpaths)
    if leaks:
        errs += path_leak_errors(kit, home)
    tb = kit / "sources" / TARBALL
    if not tb.is_file() or sha256(tb) != FFMPEG_SRC_SHA256:
        errs.append("sources/%s is missing or its sha256 differs" % TARBALL)
    return errs


def otool_id(path):
    r = subprocess.run(["otool", "-arch", "arm64", "-D", str(path)], capture_output=True, text=True)
    lines = r.stdout.strip().splitlines()
    return lines[-1].strip() if r.returncode == 0 and len(lines) > 1 else ""


def otool_deps(path):
    r = subprocess.run(["otool", "-arch", "arm64", "-L", str(path)], capture_output=True, text=True)
    deps = [l.split(" (")[0].strip() for l in r.stdout.splitlines()[1:]]
    return [d for d in deps if d != otool_id(path)]


def run(argv):
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail("%s failed: %s" % (" ".join(map(str, argv)), (r.stderr or r.stdout).strip()))
    return r.stdout


def cut_source(repo, out, runner=run):
    repo, out = Path(repo), Path(out).absolute()
    if runner(["git", "-C", str(repo), "status", "--porcelain"]).strip():
        raise Fail("%s has uncommitted changes: commit first, the kit is cut from a commit" % repo)
    sha = runner(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip()
    (out / "src").mkdir()
    archive = out / "src.tar"
    runner(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(archive), sha, "--"] + EXCLUDES)
    with tarfile.open(archive) as t:
        t.extractall(out / "src", filter="data") if hasattr(tarfile, "data_filter") else t.extractall(out / "src")
    archive.unlink()
    (out / "SOURCE").write_text(sha + "\n")
    return sha


def unpack_python(archive, lock, out):
    archive = Path(archive)
    if not archive.is_file():
        raise Fail("python archive %s not found: fetch %s (sha256 %s) and pass --python-archive" % (archive, lock["url"], lock["sha256"]))
    if sha256(archive) != lock["sha256"]:
        raise Fail("python archive %s does not match the sha256 in python.lock" % archive)
    with tarfile.open(archive) as t:
        t.extractall(out, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(out)
    for p in python_paths(out, [("lib/python3.*/lib-dynload", GPL_MODULE), WIN_LAUNCHERS] + UNLICENSED):
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def install_wheels(out, wheels, requirements, runner=run):
    runner([
        str(out / "python/bin/python3"), "-I", "-B", "-m", "pip", "install", "--no-compile",
        "--require-hashes", "--only-binary", ":all:", "--no-deps", "--no-index",
        "--find-links", str(wheels), "-r", str(requirements),
    ])
    cut_path = str(Path(out).absolute()).encode()
    for p in (Path(out) / "python/bin").iterdir():
        if p.is_file() and not p.is_symlink():
            data = p.read_bytes()
            if data[:4] in MACHO_MAGIC:
                continue
            if (data.startswith(b"#!/") and not data.startswith((b"#!/bin/", b"#!/usr/bin/"))) or cut_path in data:
                p.unlink()


def collect_licences(out, name, files):
    d = out / LICENCES / name
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in files:
        f = Path(f)
        if f.is_file() and f.stat().st_size > 0:
            shutil.copy2(f, d / f.name)
            n += 1
    if not n:
        raise Fail("no licence text found for %s" % name)


def python_licences(out):
    py = list((out / "python/lib").glob("python3.*/LICENSE*"))
    return py


def wheel_licences(out, dist):
    sp = next((out / "python/lib").glob("python3.*/site-packages"), None)
    files = []
    for d in (sp.glob("%s-*.dist-info" % dist) if sp else ()):
        files += [p for p in d.rglob("*") if p.is_file() and LICENCE_FILE.match(p.name)]
    return files


def versions(out):
    sp = next((out / "python/lib").glob("python3.*/site-packages"))
    v = {}
    for n in ("capstone", "meson", "ninja"):
        d = next(sp.glob("%s-*.dist-info" % n), None)
        v[n] = d.name[len(n) + 1:-len(".dist-info")] if d else "unknown"
    pyd = next((out / "python/lib").glob("python3.*"))
    v["python"] = pyd.name[len("python"):]
    return v


def otool_rpaths(path):
    r = subprocess.run(["otool", "-arch", "arm64", "-l", str(path)], capture_output=True, text=True)
    out, want = [], False
    for l in r.stdout.splitlines():
        l = l.strip()
        if l.startswith("cmd "):
            want = l == "cmd LC_RPATH"
        elif want and l.startswith("path "):
            out.append(l[5:].rsplit(" (offset", 1)[0])
    return out


def relink(dst, runner, dylib_deps, rpaths):
    argv = ["install_name_tool", "-id", "@rpath/" + dst.name]
    for dep in dylib_deps(dst):
        if not dep.startswith(("@",) + SYSTEM_DEP_PREFIXES):
            argv += ["-change", dep, "@rpath/" + Path(dep).name]
    for rp in rpaths(dst):
        if not rp.startswith("@"):
            argv += ["-delete_rpath", rp]
    runner(argv + [str(dst)])
    runner(["codesign", "--force", "--sign", "-", str(dst)])


def stage_prebuilt(out, spec, runner=run, dylib_id=None, dylib_deps=None, rpaths=None):
    dylib_id, dylib_deps, rpaths = dylib_id or otool_id, dylib_deps or otool_deps, rpaths or otool_rpaths
    pre = out / "prebuilt"
    manifest = {}
    for comp in spec["components"]:
        for item in comp["files"]:
            src = Path(item["from"]).resolve()
            to = item["to"]
            if to.endswith(".dylib"):
                to = str(Path(to).with_name(Path(dylib_id(src) or to).name))
            dst = pre / to
            if to in manifest:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            dst.chmod(dst.stat().st_mode | stat.S_IWUSR)
            if dst.suffix == ".dylib":
                relink(dst, runner, dylib_deps, rpaths)
            elif dst.parent.name == "icd" and dst.suffix == ".json":
                icd = json.loads(dst.read_text())
                icd["ICD"]["library_path"] = "../lib/" + Path(icd["ICD"]["library_path"]).name
                dst.write_text(json.dumps(icd, indent=4) + "\n")
            manifest[to] = {"component": comp["name"], "version": comp["version"], "spdx": comp["spdx"], "licence": "%s/%s" % (LICENCES, comp["name"])}
        collect_licences(out, comp["name"], comp["licences"])
    (pre / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def write_notice(out, v, spec):
    lines = ["# %s - NOTICE" % kitconst.KIT_TITLE, "", "## Build", "", "| Component | Version | Licence | Texts |", "| --- | --- | --- | --- |"]
    spdx = {"python": "PSF-2.0", "capstone": "BSD-3-Clause", "meson": "Apache-2.0", "ninja": "Apache-2.0"}
    for n in BUILD_COMPONENTS:
        lines.append("| `%s` | %s | %s | %s/%s/ |" % (n, v[n], spdx[n], LICENCES, n))
    lines += ["", "## Runtime", "", "| Component | Version | Licence | Texts |", "| --- | --- | --- | --- |"]
    for c in spec["components"]:
        lines.append("| `%s` | %s | %s | %s/%s/ |" % (c["name"], c["version"], c["spdx"], LICENCES, c["name"]))
    (out / "NOTICE.md").write_text("\n".join(lines) + "\n")


def cut(out, repo, python_archive, wheels, spec, ffmpeg_tarball, runner=run, pgo_profile=None):
    out = Path(out).absolute()
    lock_text, req_text = LOCK.read_text(), REQUIREMENTS.read_text()
    errs = lock_errors(lock_text, req_text)
    if errs:
        raise Fail("\n".join(errs))
    if out.exists() and any(out.iterdir()):
        raise Fail("%s exists and is not empty" % out)
    out.mkdir(parents=True, exist_ok=True)
    cut_source(repo, out, runner)
    unpack_python(python_archive, json.loads(lock_text), out)
    install_wheels(out, wheels, REQUIREMENTS, runner)
    v = versions(out)
    collect_licences(out, "python", python_licences(out))
    for pip_licences in (out / "python/lib").glob("python3.*/site-packages/pip-*.dist-info/licenses"):
        shutil.copytree(pip_licences, out / LICENCES / "python" / "pip", dirs_exist_ok=True)
    collect_static_licences(out, json.loads(lock_text)["tag"])
    for n in ("capstone", "meson", "ninja"):
        collect_licences(out, n, wheel_licences(out, n))
    stage_prebuilt(out, spec, runner)
    ffmpeg_tarball = Path(ffmpeg_tarball)
    if not ffmpeg_tarball.is_file() or sha256(ffmpeg_tarball) != FFMPEG_SRC_SHA256:
        raise Fail("%s is missing or does not match the pinned sha256 of %s" % (ffmpeg_tarball, TARBALL))
    (out / "sources").mkdir()
    shutil.copy2(ffmpeg_tarball, out / "sources" / TARBALL)
    if pgo_profile:
        if not Path(pgo_profile).is_file():
            raise Fail("%s is missing" % pgo_profile)
        (out / kitconst.PGO_PROFILE).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pgo_profile, out / kitconst.PGO_PROFILE)
    write_notice(out, v, spec)
    (out / EULA_NAME).write_text(EULA_TEXT)
    for name in (kitconst.CLI_NAME,) + kitconst.CLI_ALIASES:
        w = out / name
        w.write_text(WRAPPER)
        w.chmod(w.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    errs = check(out)
    if errs:
        raise Fail("the cut kit fails its own check:\n" + "\n".join(errs))
    return out


def main(argv):
    if argv and argv[0] == "check":
        ap = argparse.ArgumentParser(prog="make_kit.py check")
        ap.add_argument("kit")
        errs = check(ap.parse_args(argv[1:]).kit)
        for e in errs:
            print("check failed: " + e)
        if not errs:
            print("kit check passed")
        return 1 if errs else 0
    ap = argparse.ArgumentParser(prog="make_kit.py")
    ap.add_argument("out")
    ap.add_argument("--repo", default=str(kitconst.REPO))
    ap.add_argument("--python-archive", required=True)
    ap.add_argument("--wheels", required=True)
    ap.add_argument("--prebuilt-spec", required=True)
    ap.add_argument("--ffmpeg-tarball", required=True)
    ap.add_argument("--pgo-profile", help="clang .profdata shipped in the kit; the game build uses it for profile-guided optimisation")
    a = ap.parse_args(argv)
    try:
        cut(a.out, a.repo, a.python_archive, a.wheels, json.loads(Path(a.prebuilt_spec).read_text()), a.ffmpeg_tarball, pgo_profile=a.pgo_profile)
    except Fail as e:
        print("make_kit: %s" % e)
        return 1
    print("kit cut at %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
