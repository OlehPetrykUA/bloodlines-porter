import calendar
import hashlib
import json
import os
import re
import subprocess
import time
import zipfile
from pathlib import Path

import kitconst

CAP = 20 * 1024 * 1024
KEEP = 10 * 1024 * 1024
ZIP_CAP = 100 * 1024 * 1024
BUILD_LINES = 2000
STAMP = re.compile(r"^run-(\d{8}T\d{6}Z)\.log$")
DESKTOP = Path.home() / "Desktop"
CRASH_DIR = Path.home() / "Library/Logs/DiagnosticReports"


def stamp_epoch(stamp):
    return calendar.timegm(time.strptime(stamp, "%Y%m%dT%H%M%SZ"))


def run_stamps(logs):
    found = []
    if Path(logs).is_dir():
        for n in os.listdir(logs):
            m = STAMP.match(n)
            if m:
                found.append(m.group(1))
    return sorted(found)


def select_runs(logs, since=None, last=None):
    stamps = run_stamps(logs)
    if last:
        return stamps[-last:]
    if since is not None:
        chosen = [s for s in stamps if stamp_epoch(s) >= since]
        if chosen:
            return chosen
    return stamps[-1:]


def read_capped(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size <= CAP:
            return f.read()
        head = f.read(KEEP)
        f.seek(size - KEEP)
        tail = f.read(KEEP)
    cut = size - 2 * KEEP
    return head + ("\n[... %d bytes cut by Collect logs ...]\n" % cut).encode() + tail


def read_tail_lines(path, n):
    with open(path, "rb") as f:
        return b"".join(f.readlines()[-n:])


def redact(data, home):
    for h in {str(home).rstrip("/"), os.path.realpath(home).rstrip("/")}:
        if len(h) > 1:
            data = data.replace(h.encode(), b"~")
    return data


def sh(run, cmd):
    try:
        r = run(cmd)
        return r.strip()
    except Exception as e:
        return "unavailable (%s)" % type(e).__name__


def default_run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True).stdout


def machine_info(run=default_run):
    displays = sh(run, ["system_profiler", "SPDisplaysDataType"])
    displays = "\n".join(l for l in displays.splitlines() if "serial" not in l.lower())
    return [
        "macOS: " + sh(run, ["sw_vers", "-productVersion"]),
        "Mac model: " + sh(run, ["sysctl", "-n", "hw.model"]),
        "chip: " + sh(run, ["sysctl", "-n", "machdep.cpu.brand_string"]),
        "memory bytes: " + sh(run, ["sysctl", "-n", "hw.memsize"]),
        "displays:",
        displays,
    ]


def gather(overlay, crash_dir, since, last, work):
    overlay, crash_dir = Path(overlay), Path(crash_dir)
    logs = overlay / "logs"
    runs = select_runs(logs, since, last)
    items = []
    for s in runs:
        for n in ("run-%s.log" % s, "first-calls-%s.tsv" % s):
            if (logs / n).is_file():
                whole = n == "run-%s.log" % s and s == runs[-1]
                items.append(("logs/" + n, ("whole", logs / n) if whole else logs / n))
    if (logs / "launcher.log").is_file():
        items.append(("logs/launcher.log", ("whole", logs / "launcher.log")))
    floor = stamp_epoch(runs[0]) if runs else since
    if runs and since is not None and last is None and floor >= since:
        floor = since
    if floor is not None:
        if logs.is_dir():
            for p in sorted(logs.glob("*_d3d9.log")):
                if p.stat().st_mtime >= floor:
                    items.append(("logs/" + p.name, p))
        con = overlay / "files/Vampire/logs/console.log"
        if con.is_file() and con.stat().st_mtime >= floor:
            items.append(("logs/console.log", con))
        if crash_dir.is_dir():
            for p in sorted(crash_dir.glob("bloodlines-native*.ips")):
                if p.stat().st_mtime >= floor:
                    items.append(("crash/" + p.name, p))
    if work:
        work = Path(work)
        if (work / "summary.json").is_file():
            items.append(("build/summary.json", work / "summary.json"))
        if (work / "build.log").is_file():
            items.append(("build/build.log", ("tail", work / "build.log")))
    return runs, items


def load(src):
    if isinstance(src, tuple) and src[0] == "whole":
        return Path(src[1]).read_bytes()
    if isinstance(src, tuple):
        return read_tail_lines(src[1], BUILD_LINES)
    return read_capped(src)


def collect(overlay, home, out_dir=DESKTOP, crash_dir=CRASH_DIR, since=None, last=None, work=None, running=False, header=(), machine=None, now=None, zip_cap=ZIP_CAP):
    now = time.time() if now is None else now
    runs, items = gather(overlay, crash_dir, since, last, work)
    files, whole = [], set()
    for arc, src in items:
        try:
            files.append((arc, redact(load(src), home)))
        except OSError:
            continue
        if isinstance(src, tuple) and src[0] == "whole":
            whole.add(arc)
    dropped = []
    total = sum(len(d) for _, d in files)
    for f in list(files):
        if total <= zip_cap:
            break
        if f[0] not in whole:
            files.remove(f)
            dropped.append(f[0])
            total -= len(f[1])
    lines = list(header)
    if dropped:
        lines.append("dropped to fit the zip cap: " + " ".join(dropped))
    lines.append("state: " + ("running (logs copied while the game was running)" if running else "not running"))
    lines.append("runs: " + (" ".join(runs) if runs else "no runs"))
    lines += machine if machine is not None else machine_info()
    lines.append("files:")
    for arc, data in files:
        lines.append("%s  %d  %s" % (hashlib.sha256(data).hexdigest(), len(data), arc))
    files.append(("info.txt", redact(("\n".join(lines) + "\n").encode(), home)))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = kitconst.GAME_TITLE.replace(" ", "") + "-logs-" +time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    dest = out_dir / (base + ".zip")
    n = 1
    while dest.exists():
        n += 1
        dest = out_dir / ("%s-%d.zip" % (base, n))
    tmp = dest.with_name(dest.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in files:
            z.writestr(arc, data)
    os.replace(tmp, dest)
    return dest
