import hashlib
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import kitconst

MD5 = "dd8951ce97fb61aaddec2ad73607d201"
NAME = "VTMBup120.8.exe"
KNOWN = {MD5: NAME}
VARIANT = "12.0.8-basic"
MOD = "Unofficial_Patch"
INNO_MIN = (1, 9)
INNO_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
BUILD_LINE = re.compile(r"^build sha256 (\S+)$", re.M)
INSTALLER_GLOB = re.compile(r"^vtmbup.*\.exe$", re.I)


class UpError(Exception):
    pass


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_installer(path):
    p = Path(path).expanduser()
    if not p.is_file():
        raise UpError("installer not found: %s" % p)
    try:
        got = md5_of(p)
    except OSError as e:
        raise UpError("cannot read the installer: %s" % e)
    if got not in KNOWN:
        raise UpError("unknown installer: MD5 %s found, MD5 %s expected (%s is the build this kit knows)" % (got, MD5, NAME))
    return got


def find_installers(game):
    try:
        return sorted(p for p in Path(game).expanduser().iterdir() if INSTALLER_GLOB.match(p.name) and p.is_file())
    except OSError:
        return []


def pick_installer(game, given=None):
    if given:
        return Path(given).expanduser()
    found = find_installers(game)
    if len(found) == 1:
        return found[0]
    if not found:
        raise UpError("no VTMBup*.exe in %s: put the Unofficial Patch installer there or pass --up-installer" % game)
    raise UpError("%d installers in %s (%s): pass --up-installer to choose one" % (len(found), game, ", ".join(p.name for p in found)))


def tree(base):
    return Path(base) / VARIANT


def game_root(base):
    return Path(base) / (VARIANT + "-gu")


def friendly(msg):
    return "not enough disk space: " + msg if "No space left" in msg else msg


def find_innoextract():
    exe = shutil.which("innoextract") or next((str(Path(d) / "innoextract") for d in INNO_DIRS if (Path(d) / "innoextract").is_file()), None)
    if not exe:
        raise UpError("innoextract is not installed (brew install innoextract)")
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpError("innoextract cannot run: %s" % e)
    m = re.match(r"innoextract (\d+)\.(\d+)", out)
    if not m or (int(m.group(1)), int(m.group(2))) < INNO_MIN:
        raise UpError("innoextract 1.9 or newer is needed, found: %s" % (out.splitlines()[0] if out.strip() else "no version"))
    return exe


def load():
    try:
        import kit_app
    except ImportError:
        shim = types.ModuleType("kit_app")
        shim.Fail = Exception
        shim.inside_repo = lambda p: False
        sys.modules["kit_app"] = shim
    for d in (kitconst.REPO / "tools" / "up", Path(__file__).resolve().parent):
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))
    import basic_tree
    import game_root as gr

    return basic_tree, gr


def installer_line(md5):
    return "unofficial patch installer: %s MD5 %s ok" % (KNOWN.get(md5, NAME), md5)


def prepare(installer, game, base, say=print):
    installer = str(Path(installer).expanduser())
    md5 = check_installer(installer)
    say(installer_line(md5))
    bt, gr = load()
    u, gu = tree(base), game_root(base)
    if not (u / "manifest.tsv").is_file():
        exe = find_innoextract()
        shutil.rmtree(str(u) + ".tmp", ignore_errors=True)
        say("extracting %s (this takes a few minutes)" % NAME)
        Path(base).mkdir(parents=True, exist_ok=True)
        try:
            bt.build(installer, str(u), (exe,))
        except bt.BasicTreeError as e:
            raise UpError(friendly("extraction failed: %s" % e))
    say("building the Unofficial Patch game root")
    try:
        gr.build(str(game), str(u), str(gu), log=say)
    except (gr.GameRootError, OSError) as e:
        raise UpError(friendly("game root failed: %s" % e))
    return gu


def build_sha(exe, game):
    try:
        r = subprocess.run([str(exe), "--up-list-fixes", "--game", str(game)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "cannot run %s: %s" % (Path(exe).name, e)
    m = BUILD_LINE.search(r.stdout)
    if r.returncode != 0 or not m:
        return None, "%s --up-list-fixes exited %d without a build line" % (Path(exe).name, r.returncode)
    return m.group(1), ""
