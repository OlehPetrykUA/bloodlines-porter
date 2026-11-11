#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kitconst import (
    APP_NAME,
    BUNDLE_ID,
    EXE_NAME,
    GAME_DIR,
    GAME_TITLE,
    ICD_NAME,
    KIT_DIR,
    KIT_FILES,
    KIT_REPO_TOOLS,
    MANIFEST_NAME,
    REPO,
    UP_EXE_NAME,
    UP_MANIFEST,
    UP_TOOLS,
    WRAPPER,
    WRAPPER_NAME,
    Fail,
    git_head,
    git_out,
)

sys.path.insert(0, str(REPO / "tools"))
import devpaths

SYSTEM_PREFIXES = ("/usr/lib/", "/System/")
OK_PREFIXES = ("@rpath/", "@executable_path/", "@loader_path/") + SYSTEM_PREFIXES
FORBIDDEN = re.compile(r"/opt/homebrew|/usr/local|/Users/|/private/|/Volumes/")
MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
LICENCE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|notice)")
ICD_REL = "../Frameworks/libvulkan_kosmickrisp.dylib"
EXE_RPATH = "@executable_path/../Frameworks"
LIB_RPATH = "@loader_path"
ROW = re.compile(r"^\| `([^`]+)` \|.*\| (licenses/[^|\s/]+)/ \|$")
HOME = Path.home()
DEFAULT_BUILD = Path(devpaths.build("all"))
DEFAULT_DXVK_SRC = Path(devpaths.dev("vendor", "dxvk"))
BREW = "/opt/homebrew"
PERSONAL_KEY = "BNPersonalBuild"
PERSONAL_NOTICE = "PERSONAL-BUILD-NOTICE.md"
FFMPEG_LEGAL = "https://ffmpeg.org/legal.html"
DEFAULT_FFMPEG_LGPL = Path(devpaths.dev("ffmpeg", "lgpl", "lib"))
FFMPEG_VERSION = "8.1.2"
FFMPEG_SRC_URL = "https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz"
FFMPEG_SRC_SHA256 = "464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c"
LGPL_NOTICE = "LGPL-FFMPEG-NOTICE.txt"
LGPL_COPYING = "COPYING.LGPLv2.1"
LGPL_LICENCE = "LGPL version 2.1 or later"
LGPL_LIBS = ("libavformat.62.dylib", "libavcodec.62.dylib", "libavutil.60.dylib")
LGPL_SOURCE_COPY = "sources/ffmpeg-%s.tar.xz" % FFMPEG_VERSION
COPYRIGHT_KEY = "NSHumanReadableCopyright"
COPYRIGHT = "This software uses libraries from the FFmpeg project under the LGPLv2.1."
EULA_NAME = "EULA.txt"
EULA_TEXT = (
    GAME_TITLE + " - licence notice\n"
    "\n"
    "This program uses libraries from the FFmpeg project under the LGPLv2.1.\n"
    "The licence text is in licenses/ffmpeg/" + LGPL_COPYING + " and the FFmpeg source is at " + FFMPEG_SRC_URL + ".\n"
    "\n"
    "This program contains no game data. You supply your own copy of Vampire: The Masquerade - Bloodlines.\n"
    "The port's own code is under the MIT licence (LICENSE in src/). Other components keep their own licences (NOTICE.md).\n"
)
PREBUILT_MANIFEST = "manifest.json"
GPL_FLAGS = ("--enable-gpl", "--enable-version3", "--enable-nonfree")
LICENSE_STRING = re.compile(rb"(libav\w+) license: ([^\0]+)\0")
CONFIGURE_STRING = re.compile(rb"--prefix=[^\0]+")
UP_TOOLS_DIR = REPO / "tools" / "up"
UP_SHA_KEY = "up-build-sha256"
UP_BUILD_LINE = re.compile(r"^build sha256 (\S+)$", re.M)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def up_hashes():
    rows = {}
    for l in (UP_TOOLS_DIR / "manifests" / UP_MANIFEST).read_text().splitlines():
        parts = l.split("\t")
        if len(parts) == 3 and int(parts[1]) > 0:
            rows[parts[2]] = (parts[0], int(parts[1]))
    return rows


def up_file_errors(app, hashes):
    sizes = {size for _, size in hashes.values()}
    errs = []
    game = app / GAME_DIR
    for p in sorted(app.rglob("*")):
        if p.is_symlink() or not p.is_file() or p.stat().st_size not in sizes or game in p.parents:
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in hashes:
            errs.append("%s is Unofficial Patch file %s" % (p.relative_to(app), hashes[digest][0]))
    return errs


def up_exe_errors(contents):
    errs = []
    up = contents / "MacOS" / UP_EXE_NAME
    ver = {}
    vp = contents / "Resources" / "kit-version.txt"
    if vp.is_file():
        ver = dict(l.split(" ", 1) for l in vp.read_text().splitlines() if " " in l)
    want = ver.get(UP_SHA_KEY)
    if not up.is_file():
        return ["kit-version lists %s but %s is missing" % (UP_SHA_KEY, UP_EXE_NAME)] if want else []
    if not want or not HEX64.match(want):
        return ["%s is bundled but kit-version has no valid %s" % (UP_EXE_NAME, UP_SHA_KEY)]
    try:
        r = subprocess.run([str(up), "--up-list-fixes"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return ["%s --up-list-fixes failed: %s" % (up.name, e)]
    m = UP_BUILD_LINE.search(r.stdout)
    if r.returncode != 0 or not m or m.group(1) != want:
        errs.append("%s --up-list-fixes reports build sha256 %s, expected %s" % (up.name, m.group(1) if m else None, want))
    van = contents / "MacOS" / EXE_NAME
    try:
        m = UP_BUILD_LINE.search(subprocess.run([str(van), "--up-list-fixes"], capture_output=True, text=True, timeout=120).stdout)
    except (OSError, subprocess.TimeoutExpired):
        m = None
    if m and m.group(1) != "none":
        errs.append("%s reports build sha256 %s: the vanilla executable must carry no patch" % (van.name, m.group(1)))
    return errs


def personal_notice(libs):
    real = next(r for n, r in sorted(libs.items()) if n.startswith("libav"))
    cellar = component_of(real)[2]
    out = run([cellar / "bin/ffmpeg", "-version"]).splitlines()
    conf = next((l for l in out if l.startswith("configuration:")), "configuration: unknown")
    return "\n".join([
        "# Personal build - GPLv3 FFmpeg",
        "",
        "This " + APP_NAME + " bundles Homebrew FFmpeg. That build is licensed GPLv3, not LGPL.",
        "It is for the builder's own personal use only. Do not distribute this app.",
        "A public or distributed build must use the LGPL-only FFmpeg (milestone M7) and is made without --personal.",
        "",
        "FFmpeg: " + out[0],
        "Homebrew " + conf,
        "",
        "FFmpeg licensing: " + FFMPEG_LEGAL,
        "",
    ])


def lgpl_lib(p):
    data = Path(p).read_bytes()
    found = {m.group(2).decode(errors="replace") for m in LICENSE_STRING.finditer(data)}
    if found != {LGPL_LICENCE}:
        raise Fail("%s reports licence %s, need only %r: refusing to bundle" % (p, sorted(found) or "none", LGPL_LICENCE))
    m = CONFIGURE_STRING.search(data)
    conf = m.group().decode(errors="replace") if m else None
    bad = [f for f in GPL_FLAGS if conf and f in conf.split()]
    if bad:
        raise Fail("configure line of %s has %s: refusing to bundle" % (p, " ".join(bad)))
    return conf


def lgpl_info(libdir, copying=None):
    libdir = Path(libdir)
    if not libdir.is_dir():
        raise Fail("LGPL FFmpeg folder %s not found: build it with tools/ffmpeg/build_lgpl.sh or pass --ffmpeg-lgpl" % libdir)
    confs = []
    for n in LGPL_LIBS:
        if not (libdir / n).is_file():
            raise Fail("LGPL FFmpeg folder %s lacks %s" % (libdir, n))
        confs.append(lgpl_lib(libdir / n))
    conf = next((c for c in confs if c), None)
    if conf is None:
        raise Fail("no configure line found in the libraries of %s" % libdir)
    copying = Path(copying) if copying else libdir.parent / LGPL_COPYING
    if not copying.is_file():
        raise Fail("%s missing: copy COPYING.LGPLv2.1 from the FFmpeg source there" % copying)
    return conf, copying


def lgpl_notice(conf):
    return "\n".join([
        "FFmpeg " + FFMPEG_VERSION + " - LGPL notice",
        "",
        "This app bundles libavformat, libavcodec and libavutil from FFmpeg, built as shared libraries under the " + LGPL_LICENCE + ".",
        "The licence text is in licenses/ffmpeg/" + LGPL_COPYING + ". The libraries are replaceable: they sit in Contents/Frameworks and are loaded dynamically.",
        "",
        "Source URL: " + FFMPEG_SRC_URL,
        "Source sha256: " + FFMPEG_SRC_SHA256,
        "Source copy: " + LGPL_SOURCE_COPY + " in the kit",
        "Configure line: " + conf,
        "Changes to the FFmpeg source: none",
        "",
        "FFmpeg licensing: " + FFMPEG_LEGAL,
        "",
    ])


def lgpl_errors(contents, fw):
    errs = []
    text = ""
    pn = contents / "Resources" / LGPL_NOTICE
    if pn.is_file():
        text = pn.read_text(errors="replace")
    else:
        errs.append("FFmpeg is bundled without Resources/%s" % LGPL_NOTICE)
    for key, what in (("Source URL: http", "source URL"), ("Source sha256: ", "source sha256"), ("Source copy: " + LGPL_SOURCE_COPY, "source copy line"), ("Configure line: --", "configure line"), ("Changes to the FFmpeg source: ", "changes note")):
        if text and key not in text:
            errs.append("%s lacks the %s" % (LGPL_NOTICE, what))
    if text and any(f in text for f in GPL_FLAGS):
        errs.append("%s configure line enables GPL" % LGPL_NOTICE)
    cp = contents / "Resources/licenses/ffmpeg" / LGPL_COPYING
    if not (cp.is_file() and cp.stat().st_size > 0):
        errs.append("licenses/ffmpeg/%s missing" % LGPL_COPYING)
    for n in sorted(fw):
        if n.startswith("libsw"):
            errs.append("%s is bundled but the LGPL build has no swscale or swresample" % n)
        elif n.startswith("libav"):
            try:
                lgpl_lib(contents / "Frameworks" / n)
            except Fail as e:
                errs.append(str(e))
    return errs


def licence_errors(contents, plist, files):
    app = contents.parent
    errs = []
    notice = contents / "Resources/NOTICE.md"
    rows = {}
    if notice.is_file():
        for l in notice.read_text().splitlines():
            m = ROW.match(l)
            if m:
                rows[m.group(1)] = (m.group(2), l)
    else:
        errs.append("NOTICE.md missing")
    fw = {p.name for p in files if p.parent == contents / "Frameworks"}
    personal = bool(plist.get(PERSONAL_KEY))
    ffmpeg = any(n.startswith(("libav", "libsw")) for n in fw)
    if ffmpeg and not personal:
        errs += lgpl_errors(contents, fw)
        if "LGPLv2.1" not in str(plist.get(COPYRIGHT_KEY, "")):
            errs.append("Info.plist %s does not state the FFmpeg LGPLv2.1 use" % COPYRIGHT_KEY)
        eula = contents / "Resources" / EULA_NAME
        if not eula.is_file() or "LGPLv2.1" not in eula.read_text(errors="replace"):
            errs.append("Resources/%s is missing or does not state the FFmpeg LGPLv2.1 use" % EULA_NAME)
    if personal:
        pn = contents / "Resources" / PERSONAL_NOTICE
        text = pn.read_text(errors="replace") if pn.is_file() else ""
        if not text:
            errs.append("%s is set but Resources/%s is missing" % (PERSONAL_KEY, PERSONAL_NOTICE))
        if text and "GPLv3" not in text:
            errs.append("%s does not state GPLv3" % PERSONAL_NOTICE)
        if text and "configuration:" not in text:
            errs.append("%s lacks the FFmpeg configure line" % PERSONAL_NOTICE)
    for n in sorted(fw - set(rows)):
        errs.append("NOTICE.md has no row for " + n)
    for n in sorted(set(rows) - fw):
        errs.append("NOTICE.md row %s has no library" % n)
    for n, (folder, line) in sorted(rows.items()):
        lic = contents / "Resources" / folder
        if not any(f.is_file() and f.stat().st_size > 0 for f in lic.glob("*")):
            errs.append("%s: no licence text in %s" % (n, folder))
        if n.startswith(("libav", "libsw")) and not personal and ("configure" not in line or "--enable-gpl" in line):
            errs.append("%s: FFmpeg row needs a configure line without --enable-gpl" % n)
    return errs


def run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail("%s failed: %s" % (" ".join(str(c) for c in cmd[:2]), (r.stderr or r.stdout).strip()))
    return r.stdout


def is_macho(p):
    if p.is_symlink() or not p.is_file():
        return False
    with open(p, "rb") as f:
        return f.read(4) in MACHO_MAGICS


def own_id(p):
    lines = run(["otool", "-D", p]).splitlines()
    return lines[1].strip() if len(lines) > 1 else None


def deps(p):
    lines = run(["otool", "-L", p]).splitlines()[1:]
    names = [re.match(r"\s*(.+?) \(compatibility", l).group(1) for l in lines if "(compatibility" in l]
    me = own_id(p)
    if me in names:
        names.remove(me)
    return names


def rpaths(p):
    lines = run(["otool", "-l", p]).splitlines()
    out = []
    for i, l in enumerate(lines):
        if l.strip() == "cmd LC_RPATH":
            out.append(re.match(r"\s*path (.*) \(offset \d+\)", lines[i + 2]).group(1))
    return out


def brew_licences(formulas):
    brew = shutil.which("brew") or BREW + "/bin/brew"
    data = json.loads(run([brew, "info", "--json=v2"] + sorted(formulas)))
    return {f["name"]: f["license"] for f in data["formulae"]}


def component_of(real):
    parts = real.parts
    if "Cellar" in parts:
        i = parts.index("Cellar")
        return parts[i + 1], parts[i + 2], Path(*parts[: i + 3])
    if real.name.startswith("libdxvk"):
        return "dxvk", None, None
    if real.name.startswith("libav"):
        return "ffmpeg", FFMPEG_VERSION, None
    raise Fail("no component rule for %s" % real)


def closure(roots, personal=False, lgpl_dir=None, prebuilt_lib=None):
    libs = {}
    queue = list(roots)
    while queue:
        f = queue.pop()
        for d in deps(f):
            if prebuilt_lib and d.startswith("@rpath/") and (Path(prebuilt_lib) / Path(d).name).is_file():
                d = str(Path(prebuilt_lib) / Path(d).name)
            elif d.startswith(OK_PREFIXES):
                continue
            if not d.startswith("/"):
                raise Fail("cannot resolve %s of %s" % (d, f))
            name, real = Path(d).name, Path(d).resolve()
            if name.startswith(("libav", "libsw")) and not personal:
                if lgpl_dir is None or name not in LGPL_LIBS:
                    raise Fail("FFmpeg dependency %s: only the LGPL build (%s) may be bundled" % (name, ", ".join(LGPL_LIBS)))
                real = (Path(lgpl_dir) / name).resolve()
            if name in libs:
                if libs[name] != real:
                    raise Fail("two sources for %s" % name)
                continue
            libs[name] = real
            queue.append(real)
    return libs


def rewrite(target, is_lib):
    args = ["install_name_tool"]
    for r in rpaths(target):
        args += ["-delete_rpath", r]
    args += ["-add_rpath", LIB_RPATH if is_lib else EXE_RPATH]
    if is_lib:
        args += ["-id", "@rpath/" + target.name]
    for d in deps(target):
        if not d.startswith(OK_PREFIXES):
            args += ["-change", d, "@rpath/" + Path(d).name]
    run(args + [target])


def copy_writable(src, dst):
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o755)


def dxvk_sources(src):
    return [
        ("LICENSE", src / "LICENSE"),
        ("dxbc-spirv-LICENSE", src / "subprojects/dxbc-spirv/LICENSE"),
        ("libdisplay-info-LICENSE", src / "subprojects/libdisplay-info/LICENSE"),
    ]


def inside_repo(out):
    p = Path(out).absolute()
    while not p.exists():
        p = p.parent
    roots = [REPO]
    try:
        roots.append(Path(os.path.join(REPO, git_out(REPO, "rev-parse", "--git-common-dir"))).parent)
    except Fail:
        pass
    while True:
        if any(os.path.samefile(p, r) for r in roots):
            return True
        if p == p.parent:
            return False
        p = p.parent


def build(a):
    build_dir, dxvk_src = Path(a.build).resolve(), Path(a.dxvk_src).resolve()
    out = Path(a.out).resolve()
    if inside_repo(out):
        raise Fail("output path is inside the repo")
    try:
        summary = json.loads((build_dir / "summary.json").read_text())
    except (OSError, ValueError) as e:
        raise Fail("cannot read summary.json: %s" % e)
    if summary.get("state") != "pass":
        raise Fail("summary.json state is %r, not pass" % summary.get("state"))
    pre = Path(a.prebuilt).resolve() if a.prebuilt else None
    pm = {}
    if pre:
        try:
            pm = {Path(k).name: v for k, v in json.loads((pre / PREBUILT_MANIFEST).read_text()).items() if k.endswith(".dylib")}
            head = (pre.parent / "SOURCE").read_text().strip()
        except (OSError, ValueError) as e:
            raise Fail("cannot read the prebuilt set %s: %s" % (pre, e))
    else:
        head = git_head(REPO)
    built = (summary.get("source") or {}).get("sha")
    if built is not None and built != head:
        raise Fail("build is stale: built from %s, sources are %s; run the build again" % (built, head))
    exe_src = build_dir / "meson" / EXE_NAME
    if pre:
        extra = {n: pre / "lib" / n for n in pm}
        a.icd = str(pre / "icd" / ICD_NAME)
    else:
        extra = {
            "vulkan": Path(a.vulkan_loader),
            "kosmickrisp": Path(a.kosmickrisp),
            "dxvk": Path(a.dxvk) if a.dxvk else build_dir.parent / "dxvk/inst/lib/libdxvk_d3d9.0.dylib",
        }
    up_src = up_sha = None
    if a.up_build:
        ub = Path(a.up_build).resolve()
        try:
            if json.loads((ub / "summary.json").read_text()).get("state") != "pass":
                raise Fail("%s summary.json state is not pass" % ub)
            up_sha = (ub / "up.sha256").read_text().strip()
        except (OSError, ValueError) as e:
            raise Fail("cannot read the patched build %s: %s" % (ub, e))
        if not HEX64.match(up_sha):
            raise Fail("%s/up.sha256 is not a 64-digit hex string" % ub)
        up_src = ub / "meson" / EXE_NAME
    for p in [exe_src, Path(a.icd)] + ([] if pre else [dxvk_src / "LICENSE"]) + list(extra.values()) + ([up_src] if up_src else []):
        if not p.exists():
            raise Fail("missing input %s" % p)

    lgpl = None
    lgpl_dir = pre / "lib" if pre else a.ffmpeg_lgpl
    if not a.personal and any(Path(d).name.startswith("libav") for d in deps(exe_src)):
        copying = None
        if pre:
            av = next((n for n in sorted(pm) if n.startswith("libav")), None)
            copying = pre.parent / pm[av]["licence"] / LGPL_COPYING if av else None
        lgpl = lgpl_info(lgpl_dir, copying)

    app = out / APP_NAME
    if app.exists():
        shutil.rmtree(app)
    mac, fw, res = (app / "Contents" / d for d in ("MacOS", "Frameworks", "Resources"))
    for d in (mac, fw, res / "licenses"):
        d.mkdir(parents=True)
    exe = mac / EXE_NAME
    copy_writable(exe_src, exe)
    up_exe = mac / UP_EXE_NAME
    if up_src:
        copy_writable(up_src, up_exe)

    libs = {}
    for r in extra.values():
        libs[r.name] = r.resolve()
    libs.update(closure([exe_src] + ([up_src] if up_src else []) + list(libs.values()), a.personal, lgpl_dir if lgpl else None, pre / "lib" if pre else None))
    for name, real in libs.items():
        copy_writable(real, fw / name)

    for name in libs:
        rewrite(fw / name, True)
    rewrite(exe, False)
    if up_src:
        rewrite(up_exe, False)

    manifest = json.loads(Path(a.icd).read_text())
    manifest["ICD"]["library_path"] = ICD_REL
    (res / ICD_NAME).write_text(json.dumps(manifest, indent=4) + "\n")

    if pre:
        comps = {name: (pm[name]["component"], pm[name]["version"], None) for name in libs}
    else:
        comps = {name: component_of(real) for name, real in libs.items()}
    brewed = {c[0] for c in comps.values() if c[2]}
    spdx = brew_licences(brewed) if brewed else {}
    rows = []
    done = set()
    for name in sorted(libs):
        comp, ver, cellar = comps[name]
        if pre:
            lic = pm[name]["spdx"]
            files = [(f.name, f) for f in sorted((pre.parent / pm[name]["licence"]).iterdir()) if f.is_file()]
            if name.startswith("libav") and lgpl:
                lic = "LGPL-2.1-or-later (configure line in %s)" % LGPL_NOTICE
        elif comp == "dxvk":
            ver = git_head(dxvk_src)[:12]
            lic = "Zlib; submodules dxbc-spirv and libdisplay-info MIT"
            files = dxvk_sources(dxvk_src)
        elif cellar is None:
            lic = "LGPL-2.1-or-later (configure line in %s)" % LGPL_NOTICE
            files = [(LGPL_COPYING, lgpl[1])]
        else:
            lic = spdx[comp]
            files = [(f.name, f) for f in sorted(cellar.iterdir()) if f.is_file() and LICENCE_FILE.match(f.name)]
        if not files:
            raise Fail("no licence text found for %s" % comp)
        if comp not in done:
            done.add(comp)
            (res / "licenses" / comp).mkdir()
            for dst, src in files:
                shutil.copyfile(src, res / "licenses" / comp / dst)
        rows.append("| `%s` | %s | %s | %s | licenses/%s/ |" % (name, comp, ver, lic, comp))
    notice = [
        "# " + GAME_TITLE + " - NOTICE",
        "",
        GAME_TITLE + " as shipped contains no game data; the kit adds your own game files at Contents/Resources/game when it builds the app on your Mac. Each library below is bundled in Contents/Frameworks.",
        "Licence texts are copied unchanged from the installed package or the dxvk source into Contents/Resources/licenses/<component>/.",
        "",
        "| Library | Component | Version | Licence | Licence folder |",
        "|---|---|---|---|---|",
    ] + rows + [
        "",
        "Mesa carries per-file licences: the KosmicKrisp library is built from MIT, Apache-2.0, BSD, BSL-1.0, SGI-B-2.0 and CC0 files and contains no GPL file; THIRD_PARTY_NOTICES.md lists the exceptions and licenses/mesa holds the licence index Mesa ships.",
        "The port's own code is under the MIT licence (LICENSE in the source).",
        "FFmpeg is bundled as a GPLv3 personal build, see %s." % PERSONAL_NOTICE if a.personal else
        ("FFmpeg is bundled as an LGPL-only build, see %s." % LGPL_NOTICE if lgpl else "FFmpeg is not bundled in this build."),
        "",
    ]
    (res / "NOTICE.md").write_text("\n".join(notice))
    if lgpl:
        (res / LGPL_NOTICE).write_text(lgpl_notice(lgpl[0]))
    if a.personal:
        (res / PERSONAL_NOTICE).write_text(personal_notice(libs))

    if not a.personal:
        (res / EULA_NAME).write_text(EULA_TEXT)

    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    version = "kit-commit %s\nexecutable-sha256 %s\nbuild-state pass\n" % (head, sha)
    if up_src:
        version += "up-executable-sha256 %s\n%s %s\n" % (hashlib.sha256(up_exe.read_bytes()).hexdigest(), UP_SHA_KEY, up_sha)
    (res / "kit-version.txt").write_text(version)

    kit = app / KIT_DIR
    kit.mkdir()
    here = Path(__file__).resolve().parent
    for n in KIT_FILES:
        shutil.copyfile(here / n, kit / n)
    for n in KIT_REPO_TOOLS:
        shutil.copyfile(here.parent / n, kit / n)
    for n in UP_TOOLS:
        shutil.copyfile(UP_TOOLS_DIR / n, kit / n)
    (kit / "manifests").mkdir()
    shutil.copyfile(UP_TOOLS_DIR / "manifests" / UP_MANIFEST, kit / "manifests" / UP_MANIFEST)
    shutil.copyfile(REPO / "docs" / MANIFEST_NAME, kit / MANIFEST_NAME)
    wrapper = mac / WRAPPER_NAME
    wrapper.write_text(WRAPPER)
    os.chmod(wrapper, 0o755)

    with open(app / "Contents/Info.plist", "wb") as f:
        plistlib.dump(({PERSONAL_KEY: True} if a.personal else {COPYRIGHT_KEY: COPYRIGHT}) | {
            "CFBundleExecutable": WRAPPER_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": GAME_TITLE,
            "CFBundleDisplayName": GAME_TITLE,
            "CFBundlePackageType": "APPL",
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleVersion": head[:7] or "0",
            "CFBundleShortVersionString": "0.1",
            "LSMinimumSystemVersion": "26.0",
            "NSHighResolutionCapable": True,
            "LSSupportsGameMode": True,
            "GCSupportsControllerUserInteraction": True,
        }, f)

    if not a.no_sign:
        sign(app)
    print("built %s (%d libraries)%s" % (app, len(libs), ", PERSONAL BUILD (GPLv3 FFmpeg, not for distribution)" if a.personal else ""))
    return app


def macho_files(app):
    return [p for p in sorted(app.rglob("*")) if is_macho(p)]


def sign(app):
    for p in macho_files(app / "Contents/Frameworks"):
        run(["codesign", "--force", "--sign", "-", p])
    for n in (EXE_NAME, UP_EXE_NAME):
        if (app / "Contents/MacOS" / n).is_file():
            run(["codesign", "--force", "--sign", "-", app / "Contents/MacOS" / n])
    run(["codesign", "--force", "--sign", "-", app])


def expand(token, base_file, app):
    if token.startswith("@executable_path"):
        return app / "Contents/MacOS" / token[len("@executable_path/"):]
    if token.startswith("@loader_path"):
        return base_file.parent / token[len("@loader_path/"):]
    return None


def inside(p, app):
    r = Path(os.path.realpath(p))
    return app in r.parents


def list_imports(exe):
    r = subprocess.run([str(exe), "--list-imports"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise Fail("%s --list-imports exited %d" % (exe.name, r.returncode))
    return r.stdout


GFX_ENV = ("RT_DXVK_LIB", "SDL_VULKAN_LIBRARY", "VK_DRIVER_FILES")


def check(app, strings=False, imports_from=None, gfx=False):
    app = app.resolve()
    results = []

    def rule(name, errs):
        results.append((name, errs))

    contents = app / "Contents"
    errs = []
    plist = {}
    try:
        run(["plutil", "-lint", contents / "Info.plist"])
        plist = plistlib.loads((contents / "Info.plist").read_bytes())
    except (Fail, OSError) as e:
        errs.append(str(e))
    for k in ("CFBundleExecutable", "CFBundleIdentifier", "LSMinimumSystemVersion", "NSHighResolutionCapable", "LSSupportsGameMode", "GCSupportsControllerUserInteraction"):
        if k not in plist:
            errs.append("Info.plist lacks " + k)
    if plist and not (contents / "MacOS" / plist.get("CFBundleExecutable", "")).is_file():
        errs.append("CFBundleExecutable not in MacOS")
    for d in ("MacOS", "Frameworks", "Resources"):
        if not (contents / d).is_dir():
            errs.append("missing Contents/" + d)
    rule("bundle-layout", errs)

    errs = []
    w = contents / "MacOS" / plist.get("CFBundleExecutable", "")
    if not (w.is_file() and os.access(w, os.X_OK)):
        errs.append("CFBundleExecutable is missing or not executable")
    else:
        if w.read_text(errors="replace") != WRAPPER:
            errs.append("wrapper text differs from the kit's wrapper")
    for n in KIT_FILES + KIT_REPO_TOOLS + UP_TOOLS + (MANIFEST_NAME, "manifests/" + UP_MANIFEST):
        if not (contents.parent / KIT_DIR / n).is_file():
            errs.append("%s/%s is not bundled" % (KIT_DIR, n))
    kit_dir = contents.parent / KIT_DIR
    if (kit_dir / "fonts.py").is_file():
        imp = subprocess.run([sys.executable, "-I", "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import fonts", str(kit_dir)], capture_output=True, text=True)
        if imp.returncode != 0:
            errs.append("bundled fonts.py cannot import: %s" % imp.stderr.strip().splitlines()[-1:])
    if not is_macho(contents / "MacOS" / EXE_NAME):
        errs.append("MacOS/%s (the runtime) is missing" % EXE_NAME)
    rule("wrapper", errs)

    rule("no-bytecode", ["%s is bytecode inside the app" % p.relative_to(app) for p in sorted(app.rglob("*")) if p.name == "__pycache__" or p.suffix == ".pyc"])

    files = macho_files(app)
    inst, rp, paths = [], [], []
    for f in files:
        rel = f.relative_to(app)
        try:
            d = deps(f)
            r = rpaths(f)
            ident = own_id(f)
            if ident and not ident.startswith("@rpath/"):
                inst.append("%s has install name %s, not @rpath/" % (rel, ident))
            text = "".join(run(["otool", o, f]).split("\n", 1)[1] for o in ("-L", "-l"))
        except Fail as e:
            inst.append(str(e))
            continue
        for m in sorted(set(FORBIDDEN.findall(text))):
            paths.append("%s contains %s" % (rel, m))
        for x in d:
            if not x.startswith(OK_PREFIXES):
                inst.append("%s depends on %s" % (rel, x))
            elif x.startswith("@rpath/"):
                if not any((t := expand(q, f, app)) and (t / x[len("@rpath/"):]).is_file() for q in r):
                    inst.append("%s: %s not found in its rpaths" % (rel, x))
            elif x.startswith(("@executable_path", "@loader_path")):
                t = expand(x, f, app)
                if not t.is_file():
                    inst.append("%s: %s missing" % (rel, x))
        for q in r:
            t = expand(q, f, app)
            if t is None:
                rp.append("%s has absolute rpath %s" % (rel, q))
            elif not inside(t, app):
                rp.append("%s rpath %s leaves the bundle" % (rel, q))
    if not files:
        inst.append("no Mach-O files in the bundle")
    rule("install-names", inst)
    rule("rpaths", rp)
    rule("no-host-paths", paths)

    errs = []
    v = subprocess.run(["codesign", "--verify", "--deep", "--strict", app], capture_output=True, text=True)
    if v.returncode != 0:
        errs.append(v.stderr.strip())
    d = subprocess.run(["codesign", "-dv", app], capture_output=True, text=True)
    if "Signature=adhoc" not in d.stderr:
        errs.append("not ad-hoc signed")
    rule("codesign", errs)

    errs = []
    manifests = []
    for p in (contents / "Resources").rglob("*.json"):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            errs.append("%s is not valid JSON: %s" % (p.name, e))
            continue
        if isinstance(d, dict) and isinstance(d.get("ICD"), dict):
            manifests.append((p, d["ICD"]))
    if not manifests:
        errs.append("no ICD manifest in Resources")
    for p, icd in manifests:
        lp = icd.get("library_path", "")
        if not isinstance(lp, str) or lp.startswith("/") or "/" not in lp:
            errs.append("%s library_path %r is not a relative pathname" % (p.name, lp))
            continue
        t = p.parent / lp
        if not (t.is_file() and inside(t, app) and is_macho(t.resolve())):
            errs.append("%s library_path %r does not resolve to a library inside the bundle" % (p.name, lp))
    rule("icd-manifest", errs)

    rule("notice-licences", licence_errors(contents, plist, files))

    try:
        rule("no-up-files", up_file_errors(app, up_hashes()))
    except (OSError, ValueError) as e:
        rule("no-up-files", ["cannot read the Unofficial Patch manifest: %s" % e])
    rule("up-executable", up_exe_errors(contents))

    if strings:
        errs = []
        for f in files:
            data = f.read_bytes()
            for m in sorted(set(re.findall(rb"/opt/homebrew[\x21-\x7e]*|/Users/[\x21-\x7e]*", data))):
                errs.append("%s contains %s" % (f.relative_to(app), m.decode()))
        rule("strings", errs)

    if imports_from:
        errs = []
        try:
            a, b = list_imports(contents / "MacOS" / EXE_NAME), list_imports(Path(imports_from))
            if not a.strip():
                errs.append("bundled --list-imports printed nothing")
            if a != b:
                errs.append("--list-imports differs from the original executable")
        except (Fail, OSError, subprocess.TimeoutExpired) as e:
            errs.append(str(e))
        rule("list-imports", errs)

    if gfx:
        errs = []
        env = {k: v for k, v in os.environ.items() if k not in GFX_ENV}
        with tempfile.TemporaryDirectory() as o:
            try:
                r = subprocess.run([str(contents / "MacOS" / EXE_NAME), "--gfx-selftest", "--overlay", o], env=env, capture_output=True, text=True, errors="replace", timeout=600)
                if r.returncode != 0:
                    errs.append("--gfx-selftest without %s exited %d: %s" % ("/".join(GFX_ENV), r.returncode, r.stdout[-300:]))
            except (OSError, subprocess.TimeoutExpired) as e:
                errs.append(str(e))
        rule("gfx-selftest-no-env", errs)

    bad = 0
    for name, errs in results:
        print("%s %s" % ("FAIL" if errs else "PASS", name))
        for e in errs:
            print("     " + e)
        bad += bool(errs)
    return bad


def main():
    ap = argparse.ArgumentParser(description="Package or check " + APP_NAME)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="package a build_all.py output into an app")
    b.add_argument("--build", default=str(DEFAULT_BUILD), help="build_all.py output folder (summary.json, meson/bloodlines-native)")
    b.add_argument("--out", required=True, help="folder that receives " + APP_NAME + "; must be outside the repo")
    b.add_argument("--dxvk", help="libdxvk_d3d9.0.dylib (default: <build>/../dxvk/inst/lib)")
    b.add_argument("--dxvk-src", default=str(DEFAULT_DXVK_SRC), help="dxvk source clone for licence texts")
    b.add_argument("--vulkan-loader", default=BREW + "/opt/vulkan-loader/lib/libvulkan.1.dylib")
    b.add_argument("--kosmickrisp", default=BREW + "/opt/mesa/lib/libvulkan_kosmickrisp.dylib")
    b.add_argument("--icd", default=BREW + "/opt/mesa/share/vulkan/icd.d/" + ICD_NAME)
    b.add_argument("--up-build", help="build_all.py --patch-set output folder; adds its patched executable as the Unofficial Patch option (personal use, never distribute)")
    b.add_argument("--ffmpeg-lgpl", default=str(DEFAULT_FFMPEG_LGPL), help="lib folder of the LGPL FFmpeg from tools/ffmpeg/build_lgpl.sh (COPYING.LGPLv2.1 beside it); used without --personal")
    b.add_argument("--prebuilt", help="kit prebuilt/ folder (lib/, icd/, manifest.json): libraries and licences come from it instead of Homebrew and the dxvk clone")
    b.add_argument("--no-sign", action="store_true")
    b.add_argument("--personal", action="store_true", help="bundle the GPLv3 Homebrew FFmpeg for your own use; never distribute the result")
    c = sub.add_parser("check", help="static gate for a built app")
    c.add_argument("app")
    c.add_argument("--strings", action="store_true", help="also reject /opt/homebrew and /Users/ strings inside Mach-O files")
    c.add_argument("--imports-from", help="original executable; runs both with --list-imports (prints a table, starts no game) and diffs")
    c.add_argument("--gfx-selftest", action="store_true", help="also run the bundled --gfx-selftest with RT_DXVK_LIB, SDL_VULKAN_LIBRARY and VK_DRIVER_FILES unset")
    a = ap.parse_args()
    try:
        if a.cmd == "build":
            app = build(a)
            return check(app, imports_from=Path(a.build) / "meson" / EXE_NAME)
        return check(Path(a.app), a.strings, a.imports_from, a.gfx_selftest)
    except Fail as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
