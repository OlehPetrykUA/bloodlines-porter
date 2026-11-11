import json
import os
import shutil
import signal
import subprocess
import sys
import time

argv = sys.argv[1:]
d = os.environ["FAKE_DIR"]


def log(entry):
    with open(os.path.join(d, "argv.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def logged(key):
    p = os.path.join(d, "argv.log")
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8") as f:
        return any(key in json.loads(line) for line in f if line.strip())


def on_sigint(who):
    def handler(signum, frame):
        log({"signal": "SIGINT", "who": who, "group_leader": os.getpgid(0) == os.getpid()})
        sys.exit(130)
    return handler


if argv[:1] == ["grandchild"]:
    parent = os.getppid()
    signal.signal(signal.SIGINT, on_sigint("grandchild"))
    log({"grandchild_ready": True})
    while os.getppid() == parent:
        time.sleep(0.05)
    sys.exit(0)

signal.signal(signal.SIGINT, on_sigint("child"))
log({"argv": argv})
replies = []
p = os.path.join(d, "replies.json")
if os.path.exists(p):
    with open(p, encoding="utf-8") as f:
        replies = json.load(f)
reply = next((r for r in replies if all(m in argv for m in r["match"]) and not any(x in argv for x in r.get("unless", []))), {})
if argv[:1] == ["status"] and os.path.exists(os.path.join(d, "data-removed")):
    reply = {"stdout": ["state: no-game - pick the game folder", "subtitles: on", "aspect: default", "unofficial patch: off"]}
if reply.get("grandchild"):
    subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "grandchild"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 5
    while not logged("grandchild_ready") and time.time() < end:
        time.sleep(0.02)
for rel, text in reply.get("files", {}).items():
    full = os.path.join(d, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
for line in reply.get("stdout", []):
    print(line if isinstance(line, str) else json.dumps(line), flush=True)
    time.sleep(reply.get("delay", 0))
if reply.get("stderr"):
    print(reply["stderr"], file=sys.stderr, flush=True)
if reply.get("hang"):
    while not os.path.exists(os.path.join(d, "release")):
        time.sleep(0.05)
    sys.exit(0)
code = reply.get("exit", 0)
if code == 0 and argv[:1] == ["uninstall"] and "--yes" in argv:
    bundle = argv[argv.index("--bundle") + 1]
    if os.path.isdir(bundle):
        shutil.rmtree(bundle)
    if "--remove-data" in argv:
        open(os.path.join(d, "data-removed"), "w").close()
sys.exit(code)
