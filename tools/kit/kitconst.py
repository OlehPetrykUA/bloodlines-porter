import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GAME_TITLE = "Vampire Bloodlines"
APP_NAME = GAME_TITLE + ".app"
BUNDLE_ID = "local.vampire-bloodlines"
KIT_TITLE = "Bloodlines Porter"
KIT_APP_NAME = KIT_TITLE + ".app"
KIT_BUNDLE_ID = "local.bloodlines-porter"
CLI_NAME = "bloodlines-porter"
CLI_ALIASES = ("bloodlines-kit",)
SUPPORT_NAME = "Vampire Bloodlines"
OLD_SUPPORT_NAME = "BloodlinesNative"
CACHE_NAME = "Vampire Bloodlines"
INSTALL_DIR = "Games"
HOME = Path.home()
SUPPORT = HOME / "Library/Application Support" / SUPPORT_NAME
OLD_SUPPORT = HOME / "Library/Application Support" / OLD_SUPPORT_NAME
WORK = HOME / "Library/Caches" / CACHE_NAME
INSTALL = HOME / INSTALL_DIR
GAME_DIR = "Contents/Resources/game"
ICON_FILE = "AppIcon.icns"
EXE_NAME = "bloodlines-native"
WRAPPER_NAME = GAME_TITLE
ICD_NAME = "kosmickrisp_mesa_icd.aarch64.json"
PGO_PROFILE = "pgo/default.profdata"
KIT_DIR = "Contents/Resources/kit"
KIT_FILES = ("kitconst.py", "launcher.py", "gamecheck.py", "collect_logs.py", "up.py", "fonts.py")
KIT_REPO_TOOLS = ("vpk_list.py",)
UP_TOOLS = ("basic_tree.py", "game_root.py")
UP_MANIFEST = "up120.8-basic.tsv"
UP_EXE_NAME = "bloodlines-native-up"
MANIFEST_NAME = "game-manifest.txt"
PYTHON = "/usr/bin/python3"
NO_TOOLS_MESSAGE = (
    GAME_TITLE + " needs the Xcode Command Line Tools. "
    "Run xcode-select --install in Terminal, then open the app again."
)
CANCEL_MARK = "(-128)"
DIALOG_SCRIPT = 'display dialog "%s" buttons {"OK"} default button "OK" with icon stop'
WRAPPER = (
    "#!/bin/sh\n"
    'DIR="$(cd "$(dirname "$0")" && pwd)"\n'
    "if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then\n"
    "    /usr/bin/osascript -e '" + DIALOG_SCRIPT % NO_TOOLS_MESSAGE + "' >/dev/null 2>&1\n"
    "    exit 1\n"
    "fi\n"
    'case "$1" in\n'
    "    set|check|status|collect-logs|session-start|uninstall|run)\n"
    '        exec %(py)s -I -B "$DIR/../Resources/kit/launcher.py" "$@" ;;\n'
    "esac\n"
    'exec %(py)s -I -B "$DIR/../Resources/kit/launcher.py" run --alert "$@"\n' % {"py": PYTHON}
)


class Fail(Exception):
    pass


TICK = 0.5


class Progress:
    def __init__(self, every=None):
        self.every = every or TICK
        self.out = sys.stdout
        self.t0 = time.monotonic()
        self.lock = threading.RLock()
        self.cur = None
        self.frac = 0.0
        self.last = self.t0
        self.stopped = threading.Event()
        self.thread = None

    def emit(self, obj):
        with self.lock:
            print(json.dumps(obj, ensure_ascii=False), file=self.out, flush=True)
            self.last = time.monotonic()

    def start(self):
        self.thread = threading.Thread(target=self.tick, daemon=True)
        self.thread.start()

    def tick(self):
        while not self.stopped.wait(self.every):
            with self.lock:
                if self.cur and time.monotonic() - self.last >= self.every * 0.5:
                    self.emit(self.line())

    def step(self, name, n, of, lo, hi, expected=None):
        with self.lock:
            self.cur = {"step": name, "n": n, "of": of, "lo": lo, "hi": hi, "expected": expected, "t": time.monotonic(), "done": None, "total": None}
            self.emit(self.line())

    def item(self, done, total):
        with self.lock:
            self.cur["done"], self.cur["total"] = done, total
            if done >= total or time.monotonic() - self.last >= 0.25:
                self.emit(self.line())

    def line(self):
        c, now = self.cur, time.monotonic()
        if c["total"]:
            f = c["lo"] + (c["hi"] - c["lo"]) * c["done"] / c["total"]
        elif c["expected"]:
            f = c["lo"] + (c["hi"] - c["lo"]) * min(0.95, (now - c["t"]) / c["expected"])
        else:
            f = c["lo"]
        self.frac = max(self.frac, round(f, 4))
        d = {"step": c["step"], "n": c["n"], "of": c["of"]}
        if c["total"]:
            d["done"], d["total"] = c["done"], c["total"]
        d["fraction"] = self.frac
        d["elapsed"] = round(now - self.t0, 1)
        return d

    def log(self, text):
        self.emit({"log": text})

    def finish(self, code, message):
        self.stopped.set()
        with self.lock:
            if code == 0 and self.cur:
                self.frac = 1.0
                self.cur["lo"] = 1.0
                self.cur["hi"] = 1.0
                self.emit(self.line())
            self.emit({"result": "pass" if code == 0 else "fail", "exit": code, "message": message})


def run_json(work, err2=True):
    p = Progress()
    p.start()
    try:
        code, message = work(p)
    except KeyboardInterrupt:
        code, message = 1, "stopped"
    except Exception as e:
        code, message = 1, str(e) or type(e).__name__
    if code == 2 and err2:
        print(message, file=sys.stderr)
    p.finish(code, message)
    return code


def git_out(path, *args):
    r = subprocess.run(["git", "-C", path] + list(args), capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise Fail("git %s failed in %s: %s" % (" ".join(args), path, r.stderr.strip()))
    return r.stdout.strip()


def git_head(path):
    return git_out(path, "rev-parse", "HEAD")
