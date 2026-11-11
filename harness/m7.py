#!/usr/bin/env python3
import argparse
import array
import contextlib
import fcntl
import glob
import io
import json
import math
import operator
import os
import random
import re
import shutil
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
import wave
from itertools import accumulate
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "tools")))

import m4
import m5
import m6
from m4 import DEF, PngError, Rep, X_DEFAULT, need_exe
from m5 import RE_SCRIPT, ScriptError

SCRIPTS = os.path.join(HERE, "m7")
FAIL_C = os.path.join(HERE, "..", "shims", "fail.c")
LOCK_DEFAULT = m4.devpaths.build(".game.lock")
LOCK_WAIT = 1800.0
LOCK_POLL = 2.0
LOCK_STALE_S = 1200.0
HANG_RETRIES = 2
HANG_TRIES = 1 + HANG_RETRIES
RATE = 44100
FRAME_BYTES = 4
BYTES_S = RATE * FRAME_BYTES
WINDOW_MS = 100
SOUND_DB = -50.0
SILENT_DB = -70.0
FLOOR_DB = -120.0
DUAL_MONO = 0.999
LAG_S = 5.0
ENV_BLOCK_DIV = 100
DECIMATE = 10
CAND_TOP = 3
CAND_GAP = 5
NEAR_S = 0.02
EXACT_S = 0.0003
MATCH_SOUND = 0.8
MATCH_CHARGEN = 0.6
MATCH_CHARGEN_S = 10.0
MATCH_IDENT = 0.99
CLOCK_TOL = 0.02
CLOCK_QUEUED_MS = 200.0
CLOCK_CLOSE = 130
CLOCK_RUNS = 3
A5_START_S = 5.0
A5_SOUND = 0.5
A5_STEREO = 0.1
B3_SOUND = 0.8
B6_OPEN_MAX = 32
QUIT_SECONDS = 10
MENU_LISTEN_S = "15"
MENU_REF_S = 8.0
MUSIC_FILE = "sound/music/vampire_theme.mp3"
MUSIC_REF_S = 20.0
INTROS = ("activision", "whitewolf", "nvidiabloodlogo", "troika")
INTRO_FRAMES = (312, 661, 420, 221)
INTRO_SECONDS = (10.40, 22.06, 14.01, 14.73)
INTRO_BARS = {3: (640, 480), 4: (800, 400)}
INTRO_CLOSE = 120
NOVID_CLOSE = 60
BARS_MAX = 8
DRIFT_MS = 100.0
LENGTH_TOL = 0.03
SECONDS_TOL = 0.02
SKIP_WITHIN_S = 1.0
FRAMES_WAIT = 60
SKIP_WAIT = 30


def env_num(name, default, lo, hi, env=os.environ):
    try:
        v = float(env.get(name, default))
    except ValueError:
        return default
    return min(max(v, lo), hi) if math.isfinite(v) else default


GPU_FUNC = int(env_num("RT_GATE_GPU", 95, 60, 95))
GPU_PERF = 30
LOAD_PERF = 8.0
IDLE_MIN = env_num("RT_GATE_IDLE", 5.0, 5.0, 35.0)
IDLE_PERF = 35.0
IMPORT_GATE = (IDLE_MIN, GPU_FUNC)
PERF_P50_MS = 0.5
PERF_P99_MS = 2.0
PERF_PUMP_MS = 100
PERF_SOAK_CLOSE = 130
PLAYER_FLAGS = ("--player",)
PLAYER_FORBID = ("-dev", "+developer", "-nosound")
MERGED_ALL = ("A", "B", "C")
MIN_MP3_DECODES = 3
MIN_BIK_DECODES = 4
INTRO_MIN_COLOURS = 150
WAVE_ROWS = ("waveOutOpen", "waveOutClose", "waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutReset", "waveOutWrite")
BINK_KEEP = "_BinkOpenDirectSound@4"
MISSING_DEFAULT = "troika.bik"

BUILD_NAMES = ("ninja", "clang", "clang++", "ld")
BUSY_FUNC = ("bloodlines-native",)
BUSY_PERF = (*BUSY_FUNC, *BUILD_NAMES, "screencapture")
BUILD_RE = re.compile(r"(^|[/\s])build_all\.py(\s|$)")

RE_AUDIO_OPEN = re.compile(r'^rt: audio open device "([^"]*)" app (\d+)/(\d+)/(\w+) device (\d+)/(\d+)/(\w+)\s*$', re.M)
RE_AUDIO_CHANGED = re.compile(r'^rt: audio device changed "([^"]*)" (\d+)/(\d+)/(\w+)\s*$', re.M)
RE_AUDIO_SILENT = re.compile(r"^rt: audio silent clock \((.*)\)\s*$", re.M)
RE_AUDIO_OFF = re.compile(r"^rt: audio off \(--nosound\)\s*$", re.M)
RE_AUDIO_EXIT = re.compile(r"^rt: audio written (\d+) played (\d+) headers (\d+) underruns (\d+) steady (\d+) "
                           r"max queued ([\d.]+) ms resets (\d+) device changes (\d+)\s*$", re.M)
RE_MIXER = re.compile(r"^rt: audio mixer (\S+) refused", re.M)
RE_OVERRUN = re.compile(r"^guest: .*Sound overrun", re.M)
RE_MP3_OPEN = re.compile(r"^rt: mp3 open #(\d+) rate (\d+) ch (\d+)\s*$", re.M)
RE_MP3_EXIT = re.compile(r"^rt: mp3 decoders opened (\d+) closed (\d+) failed (\d+) frames (\d+) errors (\d+)\s*$", re.M)
RE_BINK_OPEN = re.compile(r"^rt: bink open (\S+) (\d+)x(\d+) ([\d.]+) fps frames (\d+) audio (\d+) Hz (\d+) ch\s*$", re.M)
RE_BINK_CLOSE = re.compile(r"^rt: bink close (\S+) shown (\d+)/(\d+) skipped ([01]) av drift (-?[\d.]+) ms\s*$", re.M)
RE_BINK_SKIPPED = re.compile(r"^rt: bink skipped (\S+) \(--novid\)\s*$", re.M)
RE_BINK_FAILED = re.compile(r"^rt: bink failed (\S+) (.*?)\s*$", re.M)
RE_PLAYER = re.compile(r"^rt: player mode\s*$", re.M)
RE_DEVCON = re.compile(r'^rt: dev console "developer ([01])"(?: \((\w+)\))?\s*$')
RE_WINDOW = re.compile(r"^rt: window (minimised|restored)\s*$", re.M)
RE_WINDOW_SIZE = re.compile(r"^rt: window (\d+)x(\d+) (\S+)\s*$", re.M)
RE_STAGE1 = re.compile(r"^rt: stage 1 (.*)$", re.M)
RE_EXE = re.compile(r'"[^"]*"\s*(.*)$')
RE_PRECACHE_FILE = re.compile(r"([\w./\\-]+\.(?:wav|mp3))", re.I)
RE_DECODE = re.compile(r"^decode (.+?\.(?:mp3|bik)) (.+\.wav)\s*$", re.M | re.I)
RE_CPU_IDLE = re.compile(r"CPU usage:.*?([\d.]+)% idle")
RE_GPU = re.compile(r'"Device Utilization %"=(\d+)')
RE_CASE = re.compile(r"(PASS|FAIL) ")

ORIG_PARSE = m6.parse_script
CFG = {"flags": (), "player": False, "nosound": False, "silent_ok": False}
STATE = {"entries": None, "known": None}


class WavError(ValueError):
    pass


class LockBusy(RuntimeError):
    pass


def parse_audio(log):
    opens = [SimpleNamespace(device=m[1], app=(int(m[2]), int(m[3]), m[4]), dev=(int(m[5]), int(m[6]), m[7]))
             for m in RE_AUDIO_OPEN.finditer(log)]
    changed = [SimpleNamespace(device=m[1], dev=(int(m[2]), int(m[3]), m[4])) for m in RE_AUDIO_CHANGED.finditer(log)]
    ex = RE_AUDIO_EXIT.findall(log)
    last = None
    if ex:
        e = ex[-1]
        last = SimpleNamespace(written=int(e[0]), played=int(e[1]), headers=int(e[2]), underruns=int(e[3]), steady=int(e[4]),
                               max_queued=float(e[5]), resets=int(e[6]), changes=int(e[7]))
    return SimpleNamespace(opens=opens, changed=changed, silent=RE_AUDIO_SILENT.findall(log), off=bool(RE_AUDIO_OFF.search(log)),
                           exit=last, mixers=RE_MIXER.findall(log), init_fail="Unable to init audio" in log,
                           overruns=len(RE_OVERRUN.findall(log)))


def parse_mp3(log):
    ex = RE_MP3_EXIT.findall(log)
    last = None
    if ex:
        e = [int(x) for x in ex[-1]]
        last = SimpleNamespace(opened=e[0], closed=e[1], failed=e[2], frames=e[3], errors=e[4])
    return SimpleNamespace(opens=[SimpleNamespace(n=int(m[1]), rate=int(m[2]), ch=int(m[3])) for m in RE_MP3_OPEN.finditer(log)], exit=last)


def parse_bink(log):
    opens = [SimpleNamespace(path=m[1], w=int(m[2]), h=int(m[3]), fps=float(m[4]), frames=int(m[5]), rate=int(m[6]), ch=int(m[7]))
             for m in RE_BINK_OPEN.finditer(log)]
    closes = [SimpleNamespace(path=m[1], shown=int(m[2]), frames=int(m[3]), skipped=int(m[4]), drift=float(m[5]))
              for m in RE_BINK_CLOSE.finditer(log)]
    return SimpleNamespace(opens=opens, closes=closes, skipped=[m[1] for m in RE_BINK_SKIPPED.finditer(log)],
                           failed=[(m[1], m[2]) for m in RE_BINK_FAILED.finditer(log)])


def parse_window(log):
    return SimpleNamespace(events=[m[1] for m in RE_WINDOW.finditer(log)],
                           sizes=[(int(m[1]), int(m[2]), m[3]) for m in RE_WINDOW_SIZE.finditer(log)])


def guest_args(log):
    m = RE_STAGE1.search(log)
    if not m:
        return []
    e = RE_EXE.search(m[1])
    return (e[1] if e else m[1]).split()


def dev_console(log):
    out = []
    for i, l in enumerate(log.splitlines()):
        m = RE_DEVCON.match(l)
        if m:
            out.append((i, m[1], m[2] or ""))
    return out


def stem(p):
    return os.path.splitext(os.path.basename(p.replace("\\", "/")))[0].lower()


def describe(log):
    a, p, b, w = parse_audio(log), parse_mp3(log), parse_bink(log), parse_window(log)
    x = a.exit
    return ("  audio " + ("off" if a.off else f"opens {len(a.opens)} silent-clock {len(a.silent)}") +
            (f" written {x.written} played {x.played} underruns {x.underruns} steady {x.steady} max queued {x.max_queued:g} ms resets {x.resets}"
             if x else " (no exit line)") + f"; sound overruns {a.overruns}; mp3 opens {len(p.opens)}" +
            (f" closed {p.exit.closed} failed {p.exit.failed} errors {p.exit.errors}" if p.exit else "") +
            f"; bink opens {len(b.opens)} closes {len(b.closes)} skipped {len(b.skipped)} failed {len(b.failed)}; window events {len(w.events)}")


class Wav:
    def __init__(self, rate, ch, samples):
        self.rate, self.ch, self.samples = rate, ch, samples

    @property
    def frames(self):
        return len(self.samples) // self.ch

    @property
    def seconds(self):
        return self.frames / self.rate


def read_wav(path):
    try:
        with contextlib.closing(wave.open(path, "rb")) as w:
            ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if sw != 2 or ch not in (1, 2):
                raise WavError(f"{path}: need 16-bit mono or stereo PCM, got {sw * 8}-bit {ch} channels")
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError) as e:
        raise WavError(f"{path}: {e}")
    s = array.array("h")
    s.frombytes(raw[:len(raw) - len(raw) % (2 * ch)])
    if sys.byteorder == "big":
        s.byteswap()
    return Wav(rate, ch, s)


def write_wav(path, w):
    s = array.array("h", w.samples)
    if sys.byteorder == "big":
        s.byteswap()
    with contextlib.closing(wave.open(path, "wb")) as f:
        f.setnchannels(w.ch)
        f.setsampwidth(2)
        f.setframerate(w.rate)
        f.writeframes(s.tobytes())


def wav_sizes_ok(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False, "not a RIFF WAVE file"
    riff = struct.unpack_from("<I", data, 4)[0]
    if riff != len(data) - 8:
        return False, f"RIFF size {riff} but file has {len(data) - 8} bytes after it"
    p = 12
    while p + 8 <= len(data):
        cid, sz = struct.unpack_from("<4sI", data, p)
        if cid == b"data":
            have = len(data) - (p + 8)
            return sz == have, f"data size {sz} but file holds {have} bytes"
        p += 8 + sz + (sz & 1)
    return False, "no data chunk"


def db_of(sumsq, n):
    if n <= 0 or sumsq <= 0:
        return FLOOR_DB
    return max(FLOOR_DB, 10 * math.log10(sumsq / n / (32768.0 * 32768.0)))


def corr_lr(seg):
    l, r = seg[0::2], seg[1::2]
    ll, rr = sum(map(operator.mul, l, l)), sum(map(operator.mul, r, r))
    if ll <= 0 or rr <= 0:
        return 0.0
    return sum(map(operator.mul, l, r)) / math.sqrt(ll * rr)


def windows(w, start=0.0, end=None, ms=WINDOW_MS):
    n = w.rate * ms // 1000
    first = int(math.ceil(start * 1000 / ms - 1e-9))
    last = w.frames // n if end is None else min(w.frames // n, int(math.floor(end * 1000 / ms + 1e-9)))
    out = []
    for i in range(max(0, first), last):
        seg = w.samples[i * n * w.ch:(i + 1) * n * w.ch]
        db = db_of(sum(map(operator.mul, seg, seg)), len(seg))
        dual = True if w.ch == 1 else (db > SOUND_DB and corr_lr(seg) >= DUAL_MONO)
        out.append(SimpleNamespace(t=i * ms / 1000, db=db, dual=dual))
    return out


def shares(rows):
    n = len(rows)
    sound = [x for x in rows if x.db > SOUND_DB]
    return SimpleNamespace(total=n, sound=len(sound), silent=sum(1 for x in rows if x.db < SILENT_DB),
                           stereo=sum(1 for x in sound if not x.dual),
                           sound_frac=len(sound) / n if n else 0.0,
                           stereo_frac=sum(1 for x in sound if not x.dual) / len(sound) if sound else 0.0)


def mono(w, start=0.0, end=None):
    a = int(round(start * w.rate))
    b = w.frames if end is None else min(w.frames, int(round(end * w.rate)))
    s = w.samples[a * w.ch:max(a, b) * w.ch]
    if w.ch == 1:
        return array.array("d", s)
    return array.array("d", ((x + y) * 0.5 for x, y in zip(s[0::2], s[1::2])))


def prefix_energy(v):
    return list(accumulate(map(operator.mul, v, v), initial=0.0))


def ncc(x, r, pre, ex, lag):
    n = len(x)
    i0 = max(0, -lag)
    i1 = min(n, len(r) - lag)
    if i1 <= i0 or ex <= 0.0:
        return 0.0
    er = pre[i1 + lag] - pre[i0 + lag]
    if er <= 1e-9:
        return 0.0
    return sum(map(operator.mul, x[i0:i1], r[i0 + lag:i1 + lag])) / math.sqrt(ex * er)


def envelope(v, block):
    out = []
    for i in range(0, len(v) - block + 1, block):
        seg = v[i:i + block]
        out.append(math.sqrt(sum(map(operator.mul, seg, seg)) / block))
    if out:
        m = sum(out) / len(out)
        out = [e - m for e in out]
    return out


def decimate(v, k):
    n = len(v) // k
    cols = [v[i:i + n * k:k] for i in range(k)]
    return array.array("d", (sum(t) / k for t in zip(*cols)))


def pick_lags(scores, base, top, gap):
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    out = []
    for i in order:
        if all(abs(i - j) >= gap for j in out):
            out.append(i)
        if len(out) == top:
            break
    return [base + i for i in out]


def match(x, r, rate=RATE):
    if len(x) < rate // 10 or len(r) < rate // 10:
        return 0.0, 0
    ex = sum(map(operator.mul, x, x))
    if ex <= 0.0:
        return 0.0, 0
    block = rate // ENV_BLOCK_DIV
    ex_e, er_e = envelope(x, block), envelope(r, block)
    span = len(r) - len(x)
    lo = int(math.floor(min(0, span) / block))
    hi = int(math.ceil(max(0, span) / block))
    pre_e = prefix_energy(er_e)
    ee = sum(map(operator.mul, ex_e, ex_e))
    scores = [ncc(ex_e, er_e, pre_e, ee, l) for l in range(lo, hi + 1)]
    cands = pick_lags(scores, lo, CAND_TOP, CAND_GAP) if any(s > 0 for s in scores) else []
    cands = [c * block for c in cands] or [0]
    xd, rd = decimate(x, DECIMATE), decimate(r, DECIMATE)
    exd = sum(map(operator.mul, xd, xd))
    pre_d = prefix_energy(rd)
    xm, rm, pre_r = memoryview(x), memoryview(r), prefix_energy(r)
    xdm, rdm = memoryview(xd), memoryview(rd)
    near = int(NEAR_S * rate / DECIMATE)
    exact = max(int(EXACT_S * rate), DECIMATE)
    best, best_lag = 0.0, 0
    for s in cands:
        ld = int(round(s / DECIMATE))
        top = max(range(ld - near, ld + near + 1), key=lambda l: ncc(xdm, rdm, pre_d, exd, l))
        for lag in range(top * DECIMATE - exact, top * DECIMATE + exact + 1):
            v = ncc(xm, rm, pre_r, ex, lag)
            if v > best:
                best, best_lag = v, lag
    return best, best_lag


def check_match(r, label, ref, cap, need, ref_span=(0.0, None), cap_span=(0.0, None)):
    if cap.rate != ref.rate:
        r.check(f"{label}: capture rate {cap.rate} Hz and reference rate {ref.rate} Hz are the same", False, (cap.rate, ref.rate))
        return 0.0
    peak, lag = match(mono(ref, *ref_span), mono(cap, *cap_span), cap.rate)
    r.check(f"{label}: the reference is found in the capture at {cap_span[0] + lag / cap.rate:.2f} s with match {peak:.3f} (need >= {need:g})",
            peak >= need, peak)
    return peak


def check_sound_share(r, label, rows, need):
    s = shares(rows)
    r.check(f"{label}: {s.sound} of {s.total} windows are sound above {SOUND_DB:g} dBFS ({s.sound_frac * 100:.0f} %, need >= {need * 100:g} %)",
            s.total > 0 and s.sound_frac >= need, s.sound_frac)
    return s


def check_stereo_share(r, label, rows, need=A5_STEREO):
    s = shares(rows)
    r.check(f"{label}: {s.stereo} of {s.sound} sound windows are not dual mono ({s.stereo_frac * 100:.0f} %, need >= {need * 100:g} %)",
            s.sound > 0 and s.stereo_frac >= need, s.stereo_frac)


def check_duration(r, label, w, want_s, tol=SECONDS_TOL):
    dev = abs(w.seconds - want_s) / want_s if want_s else 1.0
    r.check(f"{label}: lasts {w.seconds:.2f} s, want {want_s:.2f} s within {tol * 100:g} % ({dev * 100:.1f} %)", dev <= tol, w.seconds)


def check_wav_file(r, label, path):
    if not path or not os.path.isfile(path):
        r.check(f"{label}: capture file written", False, path or "no file")
        return None
    ok, why = wav_sizes_ok(open(path, "rb").read())
    r.check(f"{label}: WAV header sizes match the data", ok, why)
    try:
        w = read_wav(path)
    except WavError as e:
        r.check(f"{label}: WAV reads", False, e)
        return None
    r.check(f"{label}: WAV reads ({w.rate} Hz, {w.ch} ch, {w.seconds:.1f} s)", True)
    return w


def newest(pattern):
    fs = glob.glob(pattern)
    return max(fs, key=os.path.getmtime) if fs else None


def check_audio_open(r, log, app=(RATE, 2, "s16")):
    a = parse_audio(log)
    r.check("guest command line has no -nosound", "-nosound" not in guest_args(log), guest_args(log)[:8])
    r.check("no Unable to init audio line", not a.init_fail)
    r.check(f"rt: audio open line with app {app[0]}/{app[1]}/{app[2]} (got {[o.app for o in a.opens][:1]})",
            bool(a.opens) and a.opens[0].app == app, [o.app for o in a.opens][:1])
    r.check(f"rt: audio exit line has written above 0 (got {a.exit and a.exit.written})", bool(a.exit and a.exit.written > 0), a.exit)
    return a


def check_nosound_log(r, log):
    a = parse_audio(log)
    r.check("rt: audio off (--nosound) line and no rt: audio open line", a.off and not a.opens, (a.off, len(a.opens)))
    r.check("guest command line has -nosound", "-nosound" in guest_args(log), guest_args(log)[:8])


def check_clock(r, log, t_open, t_exit):
    a = parse_audio(log)
    x = a.exit
    if x is None or t_open is None or t_exit is None or t_exit <= t_open:
        r.check("clock check has the exit line and both times", False, (x, t_open, t_exit))
        return
    secs = t_exit - t_open
    for name, v in (("written", x.written), ("played", x.played)):
        got = v / BYTES_S
        dev = abs(got - secs) / secs
        r.check(f"{name} {got:.2f} s of audio within {CLOCK_TOL * 100:g} % of {secs:.2f} s from open to exit ({dev * 100:.1f} %)",
                dev <= CLOCK_TOL, got)
    r.check(f"steady underruns 0 (got {x.steady})", x.steady == 0, x.steady)
    r.check(f"max queued {x.max_queued:g} ms <= {CLOCK_QUEUED_MS:g}", x.max_queued <= CLOCK_QUEUED_MS, x.max_queued)


def check_silent_clock(r, log, seconds):
    a = parse_audio(log)
    r.check("rt: audio silent clock line", bool(a.silent), a.silent)
    r.check("no Unable to init audio line", not a.init_fail)
    r.check("stage 11 reached", re.search(r"^rt: stage 11\b", log, re.M) is not None)
    if a.exit and seconds:
        got = a.exit.written / BYTES_S
        dev = abs(got - seconds) / seconds
        r.check(f"written {got:.1f} s within {CLOCK_TOL * 100:g} % of the run's {seconds:.1f} s ({dev * 100:.1f} %)", dev <= CLOCK_TOL, got)
    else:
        r.check("exit line and run length present", False, (a.exit, seconds))


def check_quit_audio(r, log, dt):
    a = parse_audio(log)
    r.check(f"exit line has resets 1 or more (got {a.exit and a.exit.resets})", bool(a.exit and a.exit.resets >= 1), a.exit)
    r.check(f"exit within {QUIT_SECONDS} s of Quit ({'?' if dt is None else format(dt, '.1f')} s)", dt is not None and 0 <= dt <= QUIT_SECONDS, dt)


def check_wave_rows(r, rows):
    got = {x[1].split("!", 1)[-1]: x[2] for x in rows if len(x) > 2 and x[1].lower().startswith("winmm.dll!")}
    bad = {n: got.get(n, "absent") for n in WAVE_ROWS if got.get(n) != "shim"}
    r.check("first-calls TSV lists the six WINMM wave* rows as kind shim", not bad, bad)


def stub_rows(text):
    return [w[0] for w in (l.split() for l in text.splitlines()) if len(w) == 3 and "!" in w[0] and w[2] == "stub"]


def check_stub_list(r, text, merged):
    bad = []
    for n in stub_rows(text):
        dll, _, name = n.lower().partition("!")
        if "A" in merged and dll == "winmm.dll" and name.startswith("waveout"):
            bad.append(n)
        if "B" in merged and dll.startswith("mss32"):
            bad.append(n)
        if "C" in merged and dll == "binkw32.dll" and n.split("!", 1)[-1] != BINK_KEEP:
            bad.append(n)
    r.check(f"X --list-imports has no stub row for a merged piece's imports, and binkw32 keeps only {BINK_KEEP} (merged {','.join(sorted(merged)) or 'none'})",
            not bad, bad[:3])


def check_no_movie(r, log, rows):
    opens = parse_bink(log).opens
    calls = [x for x in rows if len(x) > 1 and x[1].lower().endswith("!_binkopen@8")]
    r.check("no rt: bink open line and no _BinkOpen first-call row in a run with -dev", not opens and not calls, (len(opens), calls[:1]))


def check_player_args(r, log):
    args = guest_args(log)
    r.check(f"guest command line has none of {' '.join(PLAYER_FORBID)}", not [a for a in args if a in PLAYER_FORBID], args[:10])
    r.check("log has rt: player mode", RE_PLAYER.search(log) is not None)


def check_mp3_music(r, log):
    p = parse_mp3(log)
    r.check(f"rt: mp3 open with rate {RATE} ch 2", any(o.rate == RATE and o.ch == 2 for o in p.opens), [(o.rate, o.ch) for o in p.opens][:2])
    r.check(f"rt: mp3 exit line has failed 0 and errors 0 (got {p.exit and (p.exit.failed, p.exit.errors)})",
            bool(p.exit and p.exit.failed == 0 and p.exit.errors == 0), p.exit)
    return p


def check_mp3_leak(r, log):
    p = parse_mp3(log)
    left = p.exit.opened - p.exit.closed if p.exit else None
    r.check(f"decoders opened minus closed {left} <= {B6_OPEN_MAX}", p.exit is not None and left <= B6_OPEN_MAX, p.exit)
    m6.check_allocator(r, log)


def norm_sound(n):
    n = n.lower().replace("\\", "/").strip("\"' ")
    return n[6:] if n.startswith("sound/") else n


def precache_names(log):
    out, bare = [], 0
    for l in m5.guest_lines(log):
        if "could not precache" in l and "SoundLength" in l:
            m = RE_PRECACHE_FILE.search(l)
            if m:
                out.append(norm_sound(m[1]))
            else:
                bare += 1
    return out, bare


def check_a6(r, log, known):
    names, bare = precache_names(log)
    have = sorted({n for n in names if n in known})
    r.check(f"every could-not-precache SoundLength line names a missing file ({len(names)} lines, {len(have)} name an existing file)",
            not have and not bare, (have[:3], bare))


def check_a6_control(r, log, known):
    names, _ = precache_names(log)
    have = sorted({n for n in names if n in known})
    r.check(f"the --nosound run shows could-not-precache SoundLength lines for existing files ({len(have)}), so the check is live", bool(have), names[:3])


def check_intro(r, log, rows=()):
    b = parse_bink(log)
    got = [stem(o.path) for o in b.opens]
    r.check(f"exactly four rt: bink open lines in the order {', '.join(INTROS)} (got {got})", got == list(INTROS), got)
    shown = [(c.shown, c.frames, c.skipped) for c in b.closes]
    want = [(n, n, 0) for n in INTRO_FRAMES]
    r.check(f"rt: bink close lines show {want} (got {shown})", shown == want, shown)
    r.check("no rt: bink failed line", not b.failed, b.failed[:2])
    drifts = [c.drift for c in b.closes]
    r.check(f"every rt: bink close av drift {drifts} within +/-{DRIFT_MS:g} ms", all(abs(d) <= DRIFT_MS for d in drifts), drifts)
    calls = [x[-1] for x in rows if len(x) > 1 and x[1].lower().endswith("!_binkopen@8")]
    r.check(f"_BinkOpen called 4 times in the first-calls TSV (got {calls})", calls == ["4"], calls)
    for o, sec in zip(b.opens, INTRO_SECONDS):
        dev = abs(o.frames / o.fps - sec) / sec
        r.check(f"{stem(o.path)} length {o.frames / o.fps:.2f} s from the open line is {sec:.2f} s within {SECONDS_TOL * 100:g} %", dev <= SECONDS_TOL, dev)


def check_novid(r, log):
    b = parse_bink(log)
    got = [stem(p) for p in b.skipped]
    r.check(f"four rt: bink skipped lines in the order {', '.join(INTROS)} (got {got})", got == list(INTROS), got)
    r.check("no rt: bink open line", not b.opens, len(b.opens))
    r.check("no rt: bink failed line", not b.failed, b.failed[:2])


def check_skip(r, log, t_skips, t_opens):
    b = parse_bink(log)
    c = b.closes
    r.check(f"four rt: bink close lines (got {len(c)})", len(c) == 4, len(c))
    if len(c) < 4:
        return
    for i in (0, 1):
        r.check(f"movie {i + 1} closed skipped 1 with shown {c[i].shown} below {c[i].frames} frames", c[i].skipped == 1 and c[i].shown < c[i].frames, c[i])
    for i in (2, 3):
        r.check(f"movie {i + 1} played whole: skipped 0, shown {c[i].shown}/{c[i].frames}", c[i].skipped == 0 and c[i].shown == c[i].frames, c[i])
    for i, ts in enumerate(t_skips[:2]):
        nxt = t_opens[i + 1] if len(t_opens) > i + 1 else None
        r.check(f"movie {i + 2} opens within {SKIP_WITHIN_S:g} s of skip {i + 1} ({'?' if nxt is None else format(nxt - ts, '.2f')} s)",
                nxt is not None and 0 <= nxt - ts <= SKIP_WITHIN_S, (ts, nxt))
    r.check("two skip inputs seen in the script log", len(t_skips) >= 2, t_skips)


def check_sync(r, log, t_opens, t_closes):
    b = parse_bink(log)
    for i, c in enumerate(b.closes):
        r.check(f"movie {i + 1} av drift {c.drift:g} ms within +/-{DRIFT_MS:g}", abs(c.drift) <= DRIFT_MS, c.drift)
    for i, (o, c) in enumerate(zip(b.opens, b.closes)):
        if i < len(t_opens) and i < len(t_closes):
            want = o.frames / o.fps
            got = t_closes[i] - t_opens[i]
            dev = abs(got - want) / want
            r.check(f"movie {i + 1} open to close {got:.2f} s within {LENGTH_TOL * 100:g} % of {want:.2f} s ({dev * 100:.1f} %)", dev <= LENGTH_TOL, got)
        else:
            r.check(f"movie {i + 1} has open and close times", False, (len(t_opens), len(t_closes)))


def check_bink_audio(r, log, caps, refs):
    b = parse_bink(log)
    for o in b.opens:
        k = stem(o.path)
        cap, ref = caps.get(k), refs.get(k)
        if cap is None or ref is None:
            r.check(f"{k}: capture and reference decode present", False, (cap is None, ref is None))
            continue
        check_duration(r, f"{k} capture", cap, o.frames / o.fps)
        check_match(r, f"{k} audio", ref, cap, MATCH_SOUND)


def check_bars(r, img, inner, label, limit=BARS_MAX):
    w, h, rows = img
    iw, ih = inner
    x0, y0 = (w - iw) // 2, (h - ih) // 2
    worst = 0
    for y in range(h):
        row = bytes(rows[y])
        parts = (row[:3 * x0], row[3 * (x0 + iw):]) if y0 <= y < y0 + ih else (row,)
        for p in parts:
            if p:
                worst = max(worst, max(p))
    r.check(f"{label}: pixels outside the centred {iw}x{ih} area are within {limit} of black (worst {worst})", worst <= limit, worst)


def check_toggle_log(r, log):
    lines = log.splitlines()
    chords = [i for i, l in enumerate(lines) if (m := RE_SCRIPT.match(l)) and m[2] == "chord 8"]
    dev = dev_console(log)
    vals = [d[1] for d in dev]
    r.check(f"two chord 8 script lines (got {len(chords)})", len(chords) == 2, chords)
    r.check(f"console commands developer 1 then developer 0, one each (got {vals})", vals == ["1", "0"], vals)
    ok = len(chords) == 2 and len(dev) == 2 and chords[0] < dev[0][0] < chords[1] < dev[1][0]
    r.check("developer 1 follows the first chord and developer 0 follows the second", ok, (chords, [d[0] for d in dev]))
    tail = lines[dev[1][0] + 1:] if len(dev) == 2 else []
    stop = next((i for i, l in enumerate(tail) if "close-after" in l or "WM_CLOSE" in l), len(tail))
    late = [l for l in tail[:stop] if l.startswith("guest: ")] if len(dev) == 2 else ["no developer 0 line"]
    r.check(f"no new developer console text after developer 0 ({len(late)} lines)", not late, late[:2])


def check_toggle_images(r, a, imgs, refdir):
    base, on, off = imgs
    names = {"dots": "m7-dots-mask.json", "full": "m7-menu-player-mask.json"}
    masks = {}
    for k, n in names.items():
        p = os.path.join(refdir, n)
        r.check(f"mask {p} exists", os.path.exists(p), p)
        if os.path.exists(p):
            masks[k] = json.load(open(p))
    if "dots" in masks:
        m6.differs_core(r, on, base, masks["dots"], "dev-on", "menu-player-a")
    if "full" in masks:
        ok, d = m4.compare(off, base, masks["full"], DEF["tol"], DEF["pct"], DEF["mad"], DEF["luma"], DEF["block"], DEF["block_frac"])
        r.check(f"dev-off matches menu-player-a {d}", ok, d)


def check_fail_c(r, text):
    r.check("shims/fail.c has no _BinkOpen@8 row", '"_BinkOpen@8"' not in text)


def run_stats(log):
    f = m4.RE_FRAMES.findall(log)
    pump = m4.RE_PUMP_STEADY.findall(log)
    a = parse_audio(log)
    return SimpleNamespace(p50=float(f[-1][1]) if f else None, p99=float(f[-1][3]) if f else None,
                           steady=a.exit.steady if a.exit else None, pump=int(pump[-1]) if pump else None, sound=bool(a.opens))


def check_perf(r, label, on, off):
    if not on or not off or any(x.p50 is None for x in on + off):
        r.check(f"{label}: every run has an rt: frames line", False, (len(on), len(off)))
        return
    d50 = statistics.median(x.p50 for x in on) - statistics.median(x.p50 for x in off)
    d99 = statistics.median(x.p99 for x in on) - statistics.median(x.p99 for x in off)
    print(f"  {label}: median p50 {statistics.median(x.p50 for x in on):.2f} ms with sound, {statistics.median(x.p50 for x in off):.2f} ms --nosound; "
          f"median p99 {statistics.median(x.p99 for x in on):.2f} / {statistics.median(x.p99 for x in off):.2f} ms")
    r.check(f"{label}: sound-on median p50 is {d50:+.2f} ms from --nosound (need <= {PERF_P50_MS:g})", d50 <= PERF_P50_MS, d50)
    r.check(f"{label}: sound-on median p99 is {d99:+.2f} ms from --nosound (need <= {PERF_P99_MS:g})", d99 <= PERF_P99_MS, d99)
    r.check(f"{label}: steady underruns 0 in every sound-on run ({[x.steady for x in on]})", all(x.steady == 0 for x in on), [x.steady for x in on])
    r.check(f"{label}: steady max pump gap <= {PERF_PUMP_MS} ms in every run ({[x.pump for x in on + off]})",
            all(x.pump is not None and x.pump <= PERF_PUMP_MS for x in on + off), [x.pump for x in on + off])


def parse_script(text):
    own, masked = [], []
    for n, raw in enumerate(m6.expand_console(text).splitlines(), 1):
        l = raw.strip()
        w = l.split()
        if w[:2] == ["wait", "bink"]:
            a = w[2:]
            if len(a) not in (1, 2) or not re.fullmatch(r"[1-4]", a[0]) or (len(a) == 2 and not re.fullmatch(r"[1-9]\d{0,5}", a[1])):
                raise ScriptError(f"line {n}: wait bink takes a movie number 1 to 4 and an optional frame count: {l}")
            own.append((n, "wait", " ".join(w[1:])))
            masked.append("")
        else:
            masked.append(raw)
    return sorted(ORIG_PARSE("\n".join(masked)) + own, key=lambda c: c[0])


def render7(name, env=None, chargen=None):
    with open(os.path.join(SCRIPTS, name + ".txt")) as f:
        return m6.render(f.read(), chargen, env, name)


run_text = m6.run_text


def cpu_idle(run=run_text):
    m = RE_CPU_IDLE.findall(run(["top", "-l", "2", "-n", "0", "-s", "1"]))
    return float(m[-1]) if m else None


def gpu_busy(run=run_text):
    v = [int(x) for x in RE_GPU.findall(run(["ioreg", "-r", "-c", "IOAccelerator", "-w0"]))]
    return max(v) if v else None


def procs(run=run_text):
    out = []
    for l in run(["ps", "-axo", "pid=,comm="]).splitlines():
        w = l.strip().split(None, 1)
        if len(w) == 2 and w[0].isdigit():
            out.append((int(w[0]), w[1]))
    return out


def builds7(run=run_text, me=None):
    me = m6.ancestors() if me is None else me
    out = [f"{os.path.basename(c)} (pid {p})" for p, c in procs(run) if p not in me and os.path.basename(c) in BUILD_NAMES]
    for l in run(["ps", "-axo", "pid=,command="]).splitlines():
        w = l.strip().split(None, 1)
        if len(w) == 2 and w[0].isdigit() and int(w[0]) not in me and BUILD_RE.search(w[1]) and "python" in w[1].lower():
            out.append(f"build_all.py (pid {w[0]})")
    return out


def busy7(run=run_text, me=None, perf=False):
    me = m6.ancestors() if me is None else me
    names = BUSY_PERF if perf else BUSY_FUNC
    out = [f"{os.path.basename(c)} (pid {p})" for p, c in procs(run) if p not in me and os.path.basename(c) in names and os.path.basename(c) not in BUILD_NAMES]
    return out + (builds7(run, me) if perf else [])


def readings(run=run_text, load=None):
    return SimpleNamespace(load=m6.load1() if load is None else load, idle=cpu_idle(run), gpu=gpu_busy(run))


def fmt_readings(x):
    return f"load average {x.load:.2f}, CPU idle {x.idle} %, GPU {x.gpu} %"


def gate_text(perf=False):
    return f"gate: CPU idle >= {IDLE_PERF if perf else IDLE_MIN:g} %, GPU <= {GPU_PERF if perf else GPU_FUNC} %"


def build_note(perf=False, run=run_text, me=None):
    if perf:
        return ""
    b = builds7(run, me)
    return f"; builds running (logged, not blocking): {', '.join(b)}" if b else ""


def gate7(load=None, run=run_text, me=None, perf=False):
    x = readings(run, load)
    out =[f"1-minute load average {x.load:.2f} is {LOAD_PERF:g} or more"] if perf and x.load >= LOAD_PERF else []
    low = IDLE_PERF if perf else IDLE_MIN
    out += ["CPU idle could not be read"] if x.idle is None else [f"CPU idle {x.idle:.1f} % is below {low:g} %"] if x.idle < low else []
    top = GPU_PERF if perf else GPU_FUNC
    out += ["GPU utilisation could not be read"] if x.gpu is None else [f"GPU utilisation {x.gpu} % is above {top} %"] if x.gpu > top else []
    return out + [f"another process is running: {b}" for b in busy7(run, me, perf)]


def lock_info(path):
    f = os.path.join(path, "owner")
    try:
        w = open(f).read().split()
    except OSError:
        w = []
    pid = int(w[1]) if len(w) > 1 and w[1].isdigit() else None
    try:
        epoch = int(w[2])
    except (IndexError, ValueError):
        try:
            epoch = int(os.path.getmtime(f if w else path))
        except OSError:
            epoch = None
    return pid, epoch


def pid_alive(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_stale(path, now):
    pid, epoch = lock_info(path)
    return epoch is not None and now - epoch > LOCK_STALE_S and not pid_alive(pid)


@contextlib.contextmanager
def guard(path):
    fd = os.open(path + ".guard", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def write_owner(path, owner, now=time.time):
    tmp = os.path.join(path, "owner.tmp")
    with open(tmp, "w") as f:
        f.write(f"{owner} {os.getpid()} {int(now())}\n")
    os.replace(tmp, os.path.join(path, "owner"))


def remove_if_ours(path, ino):
    try:
        st = os.stat(path)
    except OSError:
        return False
    if os.path.exists(os.path.join(path, "owner")):
        if lock_info(path)[0] != os.getpid():
            return False
    elif st.st_ino != ino:
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def release_lock(path, ino):
    with guard(path):
        return remove_if_ours(path, ino)


def refresh_owner(now=time.time):
    cur = STATE.get("lock")
    if cur:
        if lock_info(cur[0])[0] != os.getpid():
            raise LockBusy(f"{cur[0]} is no longer held by this run (owner pid {lock_info(cur[0])[0]})")
        write_owner(cur[0], cur[1], now)


def with_owner(orig):
    def run(*a, **k):
        refresh_owner()
        return orig(*a, **k)
    return run


@contextlib.contextmanager
def game_lock(path, wait=LOCK_WAIT, poll=LOCK_POLL, sleep=time.sleep, clock=time.monotonic, owner="m7", now=time.time):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    end = clock() + wait
    ino = None
    while ino is None:
        with guard(path):
            while True:
                try:
                    os.mkdir(path)
                except FileExistsError:
                    if lock_stale(path, now()):
                        print(f"-- taking the stale game lock {path}")
                        shutil.rmtree(path, ignore_errors=True)
                        if not os.path.lexists(path):
                            continue
                    break
                ino = os.stat(path).st_ino
                try:
                    write_owner(path, owner, now)
                except BaseException:
                    remove_if_ours(path, ino)
                    raise
                break
        if ino is None:
            if clock() >= end:
                raise LockBusy(f"{path} is held by another game run; waited {wait:g} s")
            sleep(poll)
    old = {}

    def bail(sig, frame):
        subprocess.run(["pkill", "-TERM", "-P", str(os.getpid())], capture_output=True)
        raise SystemExit(128 + sig)
    try:
        for s in (signal.SIGTERM, signal.SIGHUP):
            try:
                old[s] = signal.signal(s, bail)
            except ValueError:
                pass
        STATE["lock"] = (path, owner)
        yield
    finally:
        STATE["lock"] = None
        release_lock(path, ino)
        for s, h in old.items():
            signal.signal(s, h)


def hang_stop(log):
    return bool(m6.RE_HANG.search(log))


def wait_quiet(perf, sleep=time.sleep):
    t0 = time.monotonic()
    why = gate7(perf=perf)
    while why and time.monotonic() - t0 < m6.QUIET_WAIT:
        print(f"-- waiting for a quiet machine: {'; '.join(why)}", flush=True)
        sleep(m6.QUIET_POLL)
        why = gate7(perf=perf)
    return why


def rerun7(body, mk=m6.Sampler, limit=None, tries=HANG_TRIES, perf=False):
    passes = []
    for attempt in range(1, tries + 1):
        r, logs = Rep(), []
        print(f"-- attempt {attempt} of {tries}: {fmt_readings(readings())}; {gate_text(perf)}{build_note(perf)}", flush=True)
        s = mk()
        s.start()
        try:
            body(r, logs)
        finally:
            samples = s.stop()
        passes.append((r, samples, logs))
        if attempt == tries or not any(hang_stop(l) for l, _, _ in logs):
            break
        print(f"-- gfx hang stop (exit 15): retry {attempt} of {tries - 1}; checking the machine before running again")
        why = wait_quiet(perf)
        if why:
            print(f"-- not running again: {'; '.join(why)}")
            break
    return passes


@contextlib.contextmanager
def patched(mod, **kw):
    saved = {k: getattr(mod, k) for k in kw}
    for k, v in kw.items():
        setattr(mod, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(mod, k, v)


@contextlib.contextmanager
def environ(**kw):
    saved = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def ffmpeg_argv(src, dst, native=False):
    return ["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-map", "0:a:0", "-acodec", "pcm_s16le",
            *([] if native else ["-ar", str(RATE), "-ac", "2"]), "-f", "wav", dst]


def make_ref(src, dst, native=False, run=subprocess.run):
    if os.path.realpath(src) == os.path.realpath(dst) or (os.path.exists(dst) and os.path.exists(src) and os.path.samefile(src, dst)):
        raise ValueError(f"the reference output {dst} is the input file")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    p = run(ffmpeg_argv(src, dst, native), capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(dst):
        raise ValueError(f"ffmpeg could not decode {src}: {(p.stderr or '').strip()[:200]}")
    return dst


def resolve_ci(root, rel):
    p = root
    for seg in rel.replace("\\", "/").split("/"):
        try:
            names = os.listdir(p)
        except OSError:
            return None
        hit = next((n for n in names if n.lower() == seg.lower()), None)
        if hit is None:
            return None
        p = os.path.join(p, hit)
    return p


def vpk_entries(game):
    if STATE["entries"] is None or STATE["entries"][0] != game:
        import vpk_list
        STATE["entries"] = (game, [e for e in vpk_list.all_entries(game) if not e.dir_only])
    return STATE["entries"][1]


def find_game_file(game, rel, out_dir):
    loose = resolve_ci(os.path.join(game, "Vampire"), rel)
    if loose and os.path.isfile(loose):
        return loose
    want = rel.lower().replace("\\", "/")
    e = next((e for e in vpk_entries(game) if e.path.lower().replace("\\", "/") == want), None)
    if e is None:
        return None
    import vpk_list
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, os.path.basename(want))
    with open(p, "wb") as f:
        f.write(vpk_list.read_entry(game, e))
    return p


def find_movie(game, name):
    for d in ("media", "movies", "Media", "Movies"):
        p = resolve_ci(os.path.join(game, "Vampire"), f"{d}/{name}.bik")
        if p:
            return p
    for d, _, fs in os.walk(os.path.join(game, "Vampire")):
        for f in fs:
            if f.lower() == f"{name}.bik":
                return os.path.join(d, f)
    return None


def known_sounds(game):
    if STATE["known"] is None or STATE["known"][0] != game:
        s = {norm_sound(e.path) for e in vpk_entries(game) if e.path.lower().replace("\\", "/").startswith("sound/")}
        base = os.path.join(game, "Vampire", "sound")
        for d, _, fs in os.walk(base) if os.path.isdir(base) else []:
            for f in fs:
                s.add(norm_sound(os.path.relpath(os.path.join(d, f), os.path.join(game, "Vampire")).replace(os.sep, "/")))
        STATE["known"] = (game, s)
    return STATE["known"][1]


def reference(r, A, o, rel, native=False, movie=None):
    d = os.path.join(o, "audio", "ref")
    try:
        src = find_movie(A.game, movie) if movie else find_game_file(A.game, rel, os.path.join(d, "src"))
        if not src:
            raise ValueError(f"{movie or rel} is in neither the VPKs nor the loose files")
        dst = os.path.join(d, os.path.splitext(os.path.basename(src))[0] + ("-native" if native else "") + ".wav")
        return read_wav(make_ref(src, dst, native))
    except (ValueError, OSError) as e:
        r.check(f"reference decode of {movie or rel}", False, e)
        return None


def line_times(res, pat):
    rx = re.compile(pat)
    return [t for t, l in res.lines if rx.match(l.rstrip("\n"))]


def script_times(res, arg_prefix):
    out = []
    for t, l in res.lines:
        m = RE_SCRIPT.match(l.rstrip("\n"))
        if m and m[2].startswith(arg_prefix):
            out.append(t)
    return out


def t_open(res):
    t = line_times(res, r"rt: audio open ")
    return t[0] if t else None


def audio_pos(res, t_event, off=0.0):
    t0 = t_open(res)
    base = 0.0 if t0 is None or t_event is None else t_event - t0
    return max(0.0, base + off)


def cap_window(res, t_event, lo, hi):
    return audio_pos(res, t_event, lo - LAG_S), audio_pos(res, t_event, hi + LAG_S)


def rules7(orig):
    def rules(r, res, timeout, cmds=None, need_done=True, min_lines=1):
        orig(r, res, timeout, cmds, need_done, min_lines)
        log = res.log
        if CFG["nosound"]:
            check_nosound_log(r, log)
        elif not CFG.get("silent_ok"):
            check_audio_open(r, log)
        if CFG["player"]:
            check_player_args(r, log)
        else:
            check_no_movie(r, log, res.rows)
        print(describe(log))
    return rules


def spy(orig, after):
    def go(r, logs, a, o, *args, **kw):
        res = orig(r, logs, a, o, *args, **kw)
        if after:
            after(r, res, o, args[0] if args else "plain")
        return res
    return go


def with_flags(orig):
    def extra_args(a, flags, close_after, script=None, tail=()):
        return orig(a, (*flags, *CFG["flags"]), close_after, script, tail)
    return extra_args


def m6_defaults():
    return vars(m6.build_parser().parse_args(["map"]))


def ns(a):
    out = m6_defaults()
    for k, v in vars(a).items():
        if v is not None or k not in out:
            out[k] = v
    return SimpleNamespace(**out)


def start7(A, perf=False):
    if m6.start(A):
        return 1
    why = wait_quiet(perf)
    for w in why:
        print(f"REFUSED: {w}")
    note = build_note(perf)
    if note and not why:
        print(f"--{note[1:]}")
    return 1 if why else 0


def drive(A, fn, flags=(), after=None, perf=False, driver=None, real=False, player=False, nosound=False, silent_ok=False):
    if start7(A, perf):
        return 1
    CFG.update(flags=tuple(flags), player=player, nosound=nosound, silent_ok=silent_ok)
    sdl = None if real and not driver else driver or "dummy"
    with patched(m6, rules=rules7(m6.rules), go=spy(m6.go, after), go_plain=spy(m6.go_plain, after), extra_args=with_flags(m6.extra_args),
                 parse_script=parse_script, gate_reasons=lambda *a_, **k_: gate7(perf=perf), RUNNER=with_owner(m6.RUNNER),
                 run_with_rerun=lambda body, mk=m6.Sampler, limit=None: rerun7(body, mk, limit, perf=perf)), environ(SDL_AUDIO_DRIVER=sdl):
        return fn()


def finish_run(body):
    return m6.finish(m6.run_with_rerun(body))


def newest_wav(o, kind="waveout"):
    return newest(os.path.join(o, "audio", f"{kind}-*.wav"))


def check_menu_run(r, A, res, o, name="menu"):
    check_wave_rows(r, res.rows)
    w = check_wav_file(r, "waveout capture", newest_wav(o))
    if not w:
        return
    ref = reference(r, A, o, MUSIC_FILE)
    if ref:
        peak = check_match(r, "menu music", ref, w, MATCH_SOUND, (0.0, min(ref.seconds, MENU_REF_S)))
        print(f"  AC-A3 menu music match {peak:.3f} (need >= {MATCH_SOUND:g}); capture {w.seconds:.1f} s", flush=True)
        check_mp3_music(r, res.log)


def cmd_menu(A):
    def body(r, logs):
        if A.clock:
            for i in range(A.n):
                if not m6.quiet_start(r, f"run {i + 1}"):
                    continue
                res = m6.go_plain(r, logs, A, m6.fresh(A, "clock"), close_after=A.close_after or CLOCK_CLOSE)
                check_clock(r, res.log, t_open(res), res.exit)
            return
        if A.driver:
            res = m6.go_plain(r, logs, A, m6.fresh(A, "nodevice"), close_after=A.close_after or 60)
            t = line_times(res, r"rt: audio silent clock")
            check_silent_clock(r, res.log, res.exit - (t[0] if t else 0.0))
            return
        o = m6.fresh(A, "menu")
        res = m6.go(r, logs, A, o, "menu", render7("menu", {"len": MENU_LISTEN_S}), close_after=A.close_after or 90)
        check_menu_run(r, A, res, o)

    flags = () if A.clock else ("--audio-capture",)
    return drive(A, lambda: finish_run(body), flags=flags, perf=A.clock, driver=A.driver, real=A.clock or A.real_device,
                 silent_ok=bool(A.driver))


def cmd_quit(A):
    def body(r, logs):
        menu = m5.load_menu(A.menu)
        o = m6.fresh(A, "quit")
        res = m6.go(r, logs, A, o, "quit", m6.render(open(os.path.join(m5.SCRIPTS, "quit.txt")).read(), menu), need_done=False)
        t = script_times(res, "click")
        check_quit_audio(r, res.log, None if not t else res.exit - t[-1])

    return drive(A, lambda: finish_run(body))


def cmd_nosound(A):
    def body(r, logs):
        o = m6.fresh(A, "nosound")
        res = m6.go(r, logs, A, o, "nosound", "wait menu\nsleep 3\ncapture menu\n", close_after=A.close_after or 60)
        m4.check_stages(r, res.log)
        m4.check_menu_marker(r, res.log)
        m5.screen(r, A, o, "menu", "m4-menu")

    return drive(A, lambda: finish_run(body), flags=("--nosound",), nosound=True)


def cmd_map(A):
    known = {}

    def sound_known():
        if not known:
            known["s"] = known_sounds(A.game)
        return known["s"]

    def on_after(r, res, o, name):
        check_a6(r, res.log, sound_known())
        if A.guest_quiet:
            check_mp3_leak(r, res.log)

    rc = drive(A, lambda: m6.cmd_map(A), after=on_after)
    if rc or A.guest_quiet or A.no_control:
        return rc

    def off_after(r, res, o, name):
        check_a6_control(r, res.log, sound_known())

    return drive(A, lambda: m6.cmd_map(A), flags=("--nosound",), after=off_after, nosound=True)


def cmd_look(A):
    def after(r, res, o, name):
        w = check_wav_file(r, "waveout capture", newest_wav(o))
        t = line_times(res, r"rt: map ready ")
        if w and t:
            rows = windows(w, audio_pos(res, t[0], A5_START_S))
            check_sound_share(r, "look", rows, A5_SOUND)
            check_stereo_share(r, "look", rows)

    return drive(A, lambda: m6.cmd_look(A), flags=("--audio-capture",), after=after)


def is_menu_theme(rel):
    return os.path.basename(rel.replace("\\", "/")).lower() == os.path.basename(MUSIC_FILE).lower()


def music_decodes(decodes):
    return [d for d in decodes if "/music/" in d[0].lower().replace("\\", "/") and d[0].lower().endswith(".mp3")]


def check_music_pair(r, rel, w, ref):
    r.check(f"offline music {rel}: is not the menu theme", not is_menu_theme(rel), rel)
    if w and ref:
        r.check(f"offline music {rel}: length {w.frames} frames within 1152 of the ffmpeg decode's {ref.frames}",
                abs(w.frames - ref.frames) <= 1152, (w.frames, ref.frames))
        check_match(r, f"offline music {rel}", ref, w, MATCH_SOUND, (0.0, min(ref.seconds, MUSIC_REF_S)))


def run_selftest(r, A, o):
    try:
        p = subprocess.run([A.exe, "--audio-selftest", "--game", A.game, "--overlay", o], capture_output=True, text=True,
                           timeout=A.timeout or 300, env={**os.environ, "SDL_AUDIO_DRIVER": "dummy"})
    except subprocess.TimeoutExpired:
        r.check("--audio-selftest finished", False, "timed out")
        return None
    return p


def cmd_music(A):
    if not need_exe(A):
        return 1
    r, o = Rep(), A.overlay or tempfile.mkdtemp(prefix="m7-music-")
    refresh_owner()
    p = run_selftest(r, A, o)
    if p is None:
        return 1
    text = p.stdout + p.stderr
    r.check(f"--audio-selftest exit {p.returncode}", p.returncode == 0, p.returncode)
    r.check("selftest line: a music track decodes through the ASI stream after a seek",
            any(l.startswith("PASS music ") and "after a seek" in l for l in text.splitlines()))
    found = music_decodes(RE_DECODE.findall(text))
    r.check("at least one music decode line", bool(found), found)
    for rel, wavp in found:
        w = check_wav_file(r, f"decode {rel}", wavp if os.path.isabs(wavp) else os.path.join(o, wavp))
        check_music_pair(r, rel, w, reference(r, A, o, rel))
    return 0 if r.ok else 1


def cmd_chargen(A):
    def after(r, res, o, name):
        w = check_wav_file(r, "waveout capture", newest_wav(o))
        clicks = script_times(res, "click")
        if not (w and len(clicks) >= 2):
            r.check("chargen capture and click lines present", False, (bool(w), len(clicks)))
            return
        dur = clicks[-1] - clicks[0]
        check_sound_share(r, "chargen", windows(w, audio_pos(res, clicks[0]), audio_pos(res, clicks[0], dur)), B3_SOUND)
        ref = reference(r, A, o, MUSIC_FILE)
        if ref:
            check_match(r, "chargen music", ref, w, MATCH_CHARGEN, (2.0, 2.0 + MATCH_CHARGEN_S), cap_window(res, clicks[0], 0.0, dur))

    return drive(A, lambda: m6.cmd_chargen(A), flags=("--audio-capture",), after=after)


def cmd_dialogue(A):
    def after(r, res, o, name):
        w = check_wav_file(r, "waveout capture", newest_wav(o))
        ref = reference(r, A, o, A.voice)
        if w and ref:
            check_match(r, "voice line", ref, w, MATCH_CHARGEN)

    return drive(A, lambda: m6.cmd_dialogue(A), flags=("--audio-capture",), after=after)


def cmd_opening(A):
    return drive(A, lambda: m6.cmd_opening(A))


def player_flags(A, extra=()):
    return (*PLAYER_FLAGS, "--audio-capture", *extra, *(("--novid",) if getattr(A, "novid", False) else ()))


def cmd_intro(A):
    def body(r, logs):
        o = m6.fresh(A, "intro")
        res = m6.go(r, logs, A, o, "intro", render7("intro", {"frames": str(FRAMES_WAIT)}), close_after=A.close_after or INTRO_CLOSE)
        check_intro(r, res.log, res.rows)
        menu_colours = A.min_colours
        A.min_colours = min(menu_colours, INTRO_MIN_COLOURS)
        for i in range(1, 5):
            img = m5.screen(r, A, o, f"intro{i}", f"m7-intro{i}")
            if img and i in INTRO_BARS:
                check_bars(r, img, INTRO_BARS[i], f"intro{i}")
        A.min_colours = menu_colours
        m5.screen(r, A, o, "menu-after-intro")
        b = parse_bink(res.log)
        caps, refs = {}, {}
        for op in b.opens:
            k = stem(op.path)
            f = newest(os.path.join(o, "audio", f"bink-{k}-*.wav")) or newest(os.path.join(o, "audio", f"bink-*{k}*.wav"))
            w = check_wav_file(r, f"{k} capture", f)
            if w:
                caps[k] = w
                ref = reference(r, A, o, "", native=True, movie=k)
                if ref:
                    refs[k] = ref
        check_bink_audio(r, res.log, caps, refs)
        if A.real_device:
            check_sync(r, res.log, line_times(res, r"rt: bink open "), line_times(res, r"rt: bink close "))

    return drive(A, lambda: finish_run(body), flags=player_flags(A), perf=A.real_device, real=A.real_device, player=True)


def cmd_skip(A):
    def body(r, logs):
        o = m6.fresh(A, "skip")
        res = m6.go(r, logs, A, o, "skip", render7("skip", {"frames": str(SKIP_WAIT)}), close_after=A.close_after or INTRO_CLOSE)
        ts = [script_times(res, "key space"), script_times(res, "click left")]
        check_skip(r, res.log, [t[0] for t in ts if t], line_times(res, r"rt: bink open "))

    return drive(A, lambda: finish_run(body), flags=player_flags(A), player=True)


def cmd_novid(A):
    A.novid = True

    def body(r, logs):
        o = m6.fresh(A, "novid")
        res = m6.go(r, logs, A, o, "novid", render7("novid"), close_after=A.close_after or NOVID_CLOSE)
        check_novid(r, res.log)
        m5.screen(r, A, o, "menu-player", "m7-menu-player")

    return drive(A, lambda: finish_run(body), flags=player_flags(A), player=True)


def cmd_toggle(A):
    def body(r, logs):
        o = m6.fresh(A, "toggle")
        intro = "sleep 20" if A.novid else f"wait bink 4 {INTRO_FRAMES[-1] - 21}\nsleep 8"
        res = m6.go(r, logs, A, o, "toggle", render7("toggle", {"intro": intro}), close_after=A.close_after or INTRO_CLOSE)
        check_toggle_log(r, res.log)
        imgs = [m5.screen(r, A, o, n) for n in ("menu-player-a", "dev-on", "dev-off")]
        if all(imgs):
            check_toggle_images(r, A, imgs, A.refdir)

    return drive(A, lambda: finish_run(body), flags=player_flags(A), player=True)


def cmd_missing(A):
    bik = not A.empty or A.empty.lower().endswith(".bik")

    def body(r, logs):
        tree = tempfile.mkdtemp(prefix="m7-missing-")
        try:
            want = A.empty or MISSING_DEFAULT
            hit = next((os.path.relpath(os.path.join(d, f), A.game).replace(os.sep, "/") for d, _, fs in os.walk(A.game)
                        for f in fs if f.lower() == os.path.basename(want).lower()), None)
            r.check(f"{want} exists in the game folder", hit is not None, want)
            if hit is None:
                return
            m6.symlink_tree(A.game, tree, hit)
            open(os.path.join(tree, hit), "wb").close()
            o = m6.fresh(A, "missing")
            ca = A.close_after or INTRO_CLOSE
            t0 = time.monotonic()
            res = m6.RUNNER(A.exe, o, ["--game", tree, *m6.extra_args(A, [], ca, None, ())], A.timeout or ca + 120)
            logs.append((res.log, t0, time.monotonic()))
            m6.rules(r, res, A.timeout or ca + 120)
            b = parse_bink(res.log)
            if bik:
                r.check(f"three movies play and one rt: bink failed line ({len(b.opens)} opens, {len(b.failed)} failed)",
                        len(b.opens) == 3 and len(b.failed) == 1, (len(b.opens), b.failed))
            else:
                p = parse_mp3(res.log)
                r.check("rt: mp3 exit line counts a failed decoder or errors", bool(p.exit and (p.exit.failed or p.exit.errors)), p.exit)
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    return drive(A, lambda: finish_run(body), flags=PLAYER_FLAGS if bik else (), player=bik)


def setup_text():
    return f"{fmt_readings(readings())}; {gate_text(True)}; tool window hidden and status pane closed: confirm by hand"


def cmd_perf(A):
    def body(r, logs):
        print(f"-- setup: {setup_text()}; exe {A.exe}; {A.n} interleaved runs per cell" + (f"; {A.note}" if A.note else ""))
        walk = m6.gen_walk(A.close_after or m6.SOAK_CLOSE)
        cells = {(w, s): [] for w in ("soak", "perf") for s in (True, False)}
        for i in range(A.n):
            for w in ("soak", "perf"):
                for sound in (True, False):
                    if not m6.quiet_start(r, f"{w} run {i + 1} {'sound' if sound else '--nosound'}"):
                        continue
                    print(f"  readings at start: {fmt_readings(readings())}")
                    CFG.update(flags=() if sound else ("--nosound",), nosound=not sound)
                    if w == "soak":
                        res = m6.go_plain(r, logs, A, m6.fresh(A, "perf"), close_after=PERF_SOAK_CLOSE, flags=("--guest-quiet",))
                    else:
                        res = m6.go(r, logs, A, m6.fresh(A, "perf"), "perf", walk, tail=("+map", A.map),
                                    close_after=A.close_after or m6.SOAK_CLOSE, flags=("--guest-quiet",), need_done=False)
                    st = run_stats(res.log)
                    cells[(w, sound)].append(st)
                    print(f"  {w} {'sound' if sound else 'nosound'} run {i + 1}: p50 {st.p50} p99 {st.p99} steady underruns {st.steady} steady pump {st.pump}")
        for w in ("soak", "perf"):
            check_perf(r, w, cells[(w, True)], cells[(w, False)])

    return drive(A, lambda: finish_run(body), perf=True, real=True)


def merged_of(A):
    return {x for x in (A.merged or ",".join(MERGED_ALL)).split(",") if x}


def cmd_cases(A):
    if not need_exe(A):
        return 1
    r, o = Rep(), A.overlay or tempfile.mkdtemp(prefix="m7-cases-")
    refresh_owner()
    p = run_selftest(r, A, o)
    if p is None:
        return 1
    text = p.stdout + p.stderr
    res = [l for l in text.splitlines() if RE_CASE.match(l)]
    bad = [l for l in res if l.startswith("FAIL")]
    for l in res:
        print(l)
    r.check(f"{len(res) - len(bad)} PASS lines (need >= {A.min_cases}), 0 FAIL, exit {p.returncode}",
            not bad and len(res) >= A.min_cases and p.returncode == 0, bad[:3])
    merged = merged_of(A)
    decodes = RE_DECODE.findall(text)
    mp3 = [d for d in decodes if d[0].lower().endswith(".mp3")]
    bik = [d for d in decodes if d[0].lower().endswith(".bik")]
    music_found = music_decodes(decodes)
    if "B" in merged:
        r.check(f"at least {MIN_MP3_DECODES} .mp3 decode lines (got {len(mp3)})", len(mp3) - len(music_found) >= MIN_MP3_DECODES, [d[0] for d in mp3])
    if "C" in merged:
        r.check(f"{MIN_BIK_DECODES} .bik decode lines (got {len(bik)})", len(bik) == MIN_BIK_DECODES, [d[0] for d in bik])
    for rel, wavp in decodes:
        w = check_wav_file(r, f"decode {rel}", wavp if os.path.isabs(wavp) else os.path.join(o, wavp))
        native = rel.lower().endswith(".bik")
        ref = reference(r, A, o, rel, native=native, movie=stem(rel) if native else None)
        if (rel, wavp) in music_found:
            check_music_pair(r, rel, w, ref)
        elif w and ref:
            r.check(f"decode {rel}: length {w.frames} frames within 1152 of the ffmpeg decode's {ref.frames}", abs(w.frames - ref.frames) <= 1152, (w.frames, ref.frames))
            check_match(r, f"decode {rel}", ref, w, MATCH_IDENT)
    try:
        q = subprocess.run([A.exe, "--list-imports"], capture_output=True, text=True, timeout=A.timeout or 120)
        r.check(f"--list-imports exit {q.returncode}", q.returncode == 0, q.returncode)
        check_stub_list(r, q.stdout, merged)
    except subprocess.TimeoutExpired:
        r.check("--list-imports finished", False, "timed out")
    if "C" in merged:
        r.check(f"{A.fail_c} exists", os.path.exists(A.fail_c), A.fail_c)
        if os.path.exists(A.fail_c):
            check_fail_c(r, open(A.fail_c).read())
    return 0 if r.ok else 1


LIVE = ("cases", "menu", "quit", "nosound", "map", "look", "music", "chargen", "dialogue", "opening", "intro", "skip", "novid", "missing",
        "toggle", "perf")


def build_parser():
    ap = argparse.ArgumentParser(prog="m7.py")
    sub = ap.add_subparsers(dest="mode", required=True)

    def add(name, fn, **opts):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn, live=name in LIVE)
        common = dict(exe={"default": X_DEFAULT}, overlay={}, timeout={"type": int}, close_after={"type": int}, game={}, manifest={},
                      refdir={}, menu={}, chargen={}, display={"type": int}, merged={}, lock={"default": LOCK_DEFAULT},
                      lock_wait={"type": float, "default": LOCK_WAIT})
        for k, v in {**(common if name != "selftest" else {}), **opts}.items():
            p.add_argument("--" + k.replace("_", "-"), **v)
        return p

    flag = {"action": "store_true"}
    add("selftest", lambda a: selftest())
    add("cases", cmd_cases, min_cases={"type": int, "default": 12}, fail_c={"default": FAIL_C})
    add("menu", cmd_menu, clock=flag, n={"type": int, "default": CLOCK_RUNS}, driver={}, real_device=flag)
    add("quit", cmd_quit)
    add("nosound", cmd_nosound)
    add("map", cmd_map, map={"default": m6.HAVEN}, guest_quiet=flag, no_control=flag)
    add("look", cmd_look, map={"default": m6.HAVEN})
    add("music", cmd_music)
    add("chargen", cmd_chargen)
    add("dialogue", cmd_dialogue, voice={"required": True})
    add("opening", cmd_opening, **{"from": {"dest": "start", "choices": ["sp_theatre", "sp_tutorial_1"], "required": True}})
    add("intro", cmd_intro, real_device=flag)
    add("skip", cmd_skip)
    add("novid", cmd_novid)
    add("missing", cmd_missing, empty={})
    add("toggle", cmd_toggle, novid=flag)
    add("perf", cmd_perf, map={"default": m6.HAVEN}, n={"type": int, "default": 3}, note={})
    return ap


def synth(sec, seed=1, rate=RATE, amp=9000):
    rng = random.Random(seed)
    n = int(sec * rate)
    out = array.array("h", bytes(2 * n))
    note = max(1, int(0.2 * rate))
    pos = 0
    while pos < n:
        f = rng.choice((220, 262, 330, 392, 440, 523, 659)) * rng.choice((1, 1.5, 2))
        ph = rng.random() * 6.2832
        for i in range(min(note, n - pos)):
            env = 1 - 0.8 * i / note
            v = amp * env * (math.sin(ph + 6.2832 * f * i / rate) + 0.5 * math.sin(2 * ph + 12.5664 * f * i / rate)) + rng.uniform(-300, 300)
            out[pos + i] = max(-32768, min(32767, int(v)))
        pos += note
    return out


def tone(sec, amp, hz=1000, rate=RATE):
    return array.array("h", (int(amp * math.sin(6.2832 * hz * i / rate)) for i in range(int(sec * rate))))


def silence(sec, rate=RATE):
    return array.array("h", bytes(2 * int(sec * rate)))


def stereo(l, r, rate=RATE):
    s = array.array("h", bytes(4 * len(l)))
    s[0::2] = l
    s[1::2] = r
    return Wav(rate, 2, s)


def lines_res(lines, rc=0, exit_t=30.0, rows=None):
    return SimpleNamespace(rc=rc, log="".join(l + "\n" for _, l in lines), lines=[(t, l + "\n") for t, l in lines], exit=exit_t,
                           crashes=[], rows=m5.dinput_rows() + [["1", f"winmm.dll!{n}", "shim", "engine", "f", "1"] for n in WAVE_ROWS]
                           if rows is None else rows)


def alog(app="44100/2/s16", written=17640000, played=17640000, steady=0, maxq=93.0, resets=1, silent=None, off=False,
         args="-window -w 1280 -h 1024 -dxlevel 90 -dev", init_fail=False, extra=()):
    out = [f'rt: stage 1 host gfx dxvk "C:\\VTMB\\vampire.exe" {args}']
    if off:
        out.append("rt: audio off (--nosound)")
    elif silent:
        out.append(f"rt: audio silent clock ({silent})")
    else:
        out.append(f'rt: audio open device "Speakers" app {app} device 48000/2/f32')
    if init_fail:
        out.append("guest: Unable to init audio")
    out += list(extra)
    if not off:
        out.append(f"rt: audio written {written} played {played} headers 99 underruns 0 steady {steady} max queued {maxq} ms "
                   f"resets {resets} device changes 0")
    return "\n".join(out) + "\n"


FPS = (30.0, 29.97, 29.97, 15.0)
SHOWN = {"activision": "activision", "whitewolf": "whitewolf", "nvidiabloodlogo": "NvidiaBloodLogo", "troika": "troika"}


def blog(shown=INTRO_FRAMES, skipped=(0, 0, 0, 0), drift=(0, 0, 0, 0), frames=INTRO_FRAMES, names=INTROS, fail=()):
    out = []
    for i in range(4):
        p = f"Vampire/media/{SHOWN.get(names[i], names[i])}.bik"
        out.append(f"rt: bink open {p} 640x480 {FPS[i]} fps frames {frames[i]} audio 44100 Hz 2 ch")
        out.append(f"rt: bink close {p} shown {shown[i]}/{frames[i]} skipped {skipped[i]} av drift {drift[i]} ms")
    return "\n".join(out + [f"rt: bink failed {p} no decoder" for p in fail]) + "\n"


def vpk_bytes(files):
    payload, recs = b"", b""
    for name, data in files.items():
        recs += struct.pack("<I", len(name)) + name.encode() + struct.pack("<II", len(payload), len(data))
        payload += data
    return payload + recs + struct.pack("<IIB", len(files), len(payload), 0)


def selftest():
    global IDLE_MIN, GPU_FUNC
    IDLE_MIN, GPU_FUNC = 35.0, 60
    n = {"ok": 0}
    bad = []

    def expect(label, cond):
        n["ok"] += 1
        if not cond:
            bad.append(label)
            print(f"FAIL {label}", file=sys.__stdout__)

    def result(fn, *args, **kw):
        r = Rep(quiet=True)
        with contextlib.redirect_stdout(io.StringIO()):
            fn(r, *args, **kw)
        return r

    def raises(exc, fn, *args, **kw):
        try:
            fn(*args, **kw)
        except exc as e:
            return str(e)
        except Exception:
            return None
        return None

    def quiet(fn, *args, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            v = fn(*args, **kw)
        return v, buf.getvalue()

    me = sys.modules[__name__]
    tmp = tempfile.mkdtemp(prefix="m7-selftest-")
    try:
        names = sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(SCRIPTS, "*.txt")))
        want = ["intro", "menu", "novid", "skip", "toggle"]
        expect(f"template files are exactly the 5 named in the design ({names})", names == want)
        envs = {"menu": {"len": MENU_LISTEN_S}, "intro": {"frames": "60"},
                "skip": {"frames": "30"}, "novid": {}, "toggle": {"intro": "sleep 20"}}
        for nm in names:
            raw = open(os.path.join(SCRIPTS, nm + ".txt")).read()
            txt = render7(nm, envs[nm])
            cmds = parse_script(txt)
            digits = re.findall(r"\b\d{3,}\b", re.sub(r"^(sleep|look) .*$", "", txt, flags=re.M))
            expect(f"template {nm} renders, parses and holds no screen position", len(cmds) >= 3 and "@" not in raw and
                   "$" not in txt and not digits and not re.search(r"^move\b", raw, re.M) and [c[0] for c in cmds] == list(range(1, len(cmds) + 1)))
            expect(f"template {nm} holds no game-derived name",
                   not re.search(r"\.(wav|mp3|bik|vpk)|sm_|sp_|vampire|scrape|activision|troika", raw, re.I))
        expect("toggle renders with the intro-wait variant too", [c[1] for c in parse_script(render7("toggle", {"intro": "wait bink 4 200\nsleep 8"}))][:2] == ["wait", "sleep"])
        tg = [(c[1], c[2]) for c in parse_script(render7("toggle", {"intro": "sleep 20"}))][1:]
        expect("toggle captures menu-player-a before the first chord, after a 1 s wait, and waits 1 s after the chord before dev-on",
               [w for w, _ in tg[:4]] == ["capture", "sleep", "chord", "sleep"] and tg[0][1] == "menu-player-a" and int(tg[1][1]) >= 1 and tg[3][1] and
               tg[4] == ("capture", "dev-on"))
        expect("toggle waits at least 1 s between the dev-on capture and the second chord",
               [w for w, _ in tg[4:7]] == ["capture", "sleep", "chord"] and int(tg[5][1]) >= 1)
        expect("a template with an unknown variable is refused", raises(ValueError, render7, "menu", {}) is not None)

        c = parse_script("wait bink 1\nwait bink 4 200\nwait menu\nwait map sp_theatre\nsleep 1\ncapture a\nchord 8\nkey space\nclick left\n")
        expect("parser takes wait bink N and wait bink N F and hands the rest to m6 and m5",
               [(x[1], x[2]) for x in c][:2] == [("wait", "bink 1"), ("wait", "bink 4 200")] and [x[1] for x in c][2:] ==
               ["wait", "wait", "sleep", "capture", "chord", "key", "click"])
        expect("parser keeps source line numbers", [x[0] for x in c] == list(range(1, 10)))
        for bad_line in ("wait bink", "wait bink 0", "wait bink 5", "wait bink 1 0", "wait bink 1 2 3", "wait bink x", "wait bink 1 x",
                         "wait bink 1 1000000", "wait bink 1 -3", "bogus"):
            expect(f"parser refuses '{bad_line}'", raises(ScriptError, parse_script, bad_line + "\n") is not None)
        expect("m5 alone refuses wait bink", raises(ScriptError, m5.parse_script, "wait bink 1\n") is not None)
        expect("m6 alone refuses wait bink", raises(ScriptError, ORIG_PARSE, "wait bink 1\n") is not None)
        expect("console macro lines and wait bink keep their numbers",
               [x[0] for x in parse_script('wait bink 1\nconsole "play a.wav"\nsleep 1\n')] == [1, 2, 3, 4, 5, 6, 7, 8])
        expect("script_seconds counts sleeps only for a wait bink script", m5.script_seconds(parse_script("wait bink 1 30\nsleep 2\n")) == 2)

        a = parse_audio(alog() + 'rt: audio device changed "AirPods" 48000/2/f32\nrt: audio mixer mixerOpen refused\nguest: Sound overrun\n')
        expect("audio parser reads open, change, mixer, overrun and exit lines",
               a.opens[0].app == (44100, 2, "s16") and a.opens[0].dev == (48000, 2, "f32") and a.opens[0].device == "Speakers" and
               a.changed[0].device == "AirPods" and a.mixers == ["mixerOpen"] and a.overruns == 1 and a.exit.written == 17640000 and
               a.exit.max_queued == 93.0 and a.exit.resets == 1 and not a.init_fail and not a.off)
        expect("audio parser reads silent clock and off", parse_audio(alog(silent="no device")).silent == ["no device"] and parse_audio(alog(off=True)).off)
        expect("audio parser sees Unable to init audio", parse_audio(alog(init_fail=True)).init_fail)
        expect("audio parser on an empty log", parse_audio("").exit is None and parse_audio("").opens == [])
        expect("audio parser reads fractional max queued", parse_audio(alog(maxq=12.5)).exit.max_queued == 12.5)
        p = parse_mp3("rt: mp3 open #1 rate 44100 ch 2\nrt: mp3 open #2 rate 22050 ch 1\nrt: mp3 decoders opened 2 closed 1 failed 0 frames 900 errors 3\n")
        expect("mp3 parser reads opens and exit counters", [(o.n, o.rate, o.ch) for o in p.opens] == [(1, 44100, 2), (2, 22050, 1)] and
               (p.exit.opened, p.exit.closed, p.exit.failed, p.exit.frames, p.exit.errors) == (2, 1, 0, 900, 3) and parse_mp3("").exit is None)
        b = parse_bink(blog(drift=(0, -12.5, 3, 120)) + "rt: bink skipped Vampire/media/x.bik (--novid)\n")
        expect("bink parser reads opens, closes with negative and fractional drift, skipped and failed",
               len(b.opens) == 4 and b.opens[1].fps == 29.97 and b.opens[0].frames == 312 and b.opens[0].rate == 44100 and b.closes[1].drift == -12.5 and
               b.closes[3].drift == 120.0 and b.skipped == ["Vampire/media/x.bik"] and parse_bink(blog(fail=("a.bik",))).failed == [("a.bik", "no decoder")])
        expect("bink parser reads the movie path case-insensitively through stem", [stem(o.path) for o in b.opens] == list(INTROS))
        w = parse_window("rt: window id 4242\nrt: window 1280x1024 vulkan\nrt: window minimised\nrt: window restored\n")
        expect("window parser reads sizes and minimise events", w.events == ["minimised", "restored"] and w.sizes == [(1280, 1024, "vulkan")])
        expect("guest_args reads the arguments after the exe path of the real stage 1 line", guest_args(alog(args="-dev -nosound +map x")) == ["-dev", "-nosound", "+map", "x"] and guest_args("") == [])
        real1 = 'rt: stage 1 host gfx dxvk guest-quiet "C:\\VTMB\\vampire.exe" -window -w 1280 -h 1024 -dxlevel 90 -dev -nosound\n'
        expect("guest_args reads runtime/main.c's own stage 1 shape, with guest-quiet before the exe path",
               guest_args(real1) == ["-window", "-w", "1280", "-h", "1024", "-dxlevel", "90", "-dev", "-nosound"])
        expect("a --nosound run's real stage 1 line passes the nosound check and a normal run's real line has no -nosound",
               result(check_nosound_log, real1 + "rt: audio off (--nosound)\n").ok and
               not result(check_nosound_log, real1.replace(" -nosound", "") + "rt: audio off (--nosound)\n").ok and
               not result(check_audio_open, real1 + alog().split("\n", 1)[1]).ok and
               result(check_audio_open, real1.replace(" -nosound", "") + alog().split("\n", 1)[1]).ok)
        expect("only the --nosound audio-off line is read; the retired default spelling and other text are not",
               parse_audio(real1 + "rt: audio off (--nosound)\n").off and
               not parse_audio(real1 + "rt: audio off (default until mp3)\n").off and
               not parse_audio(real1 + "rt: audio off (other)\n").off and
               not result(check_nosound_log, real1 + "rt: audio off (default until mp3)\n").ok and
               not result(check_nosound_log, real1.replace(" -nosound", "") + "rt: audio off (--nosound)\n").ok)
        expect("a default run (sound on) has no audio-off line, no -nosound and passes check_audio_open",
               not parse_audio(real1.replace(" -nosound", "") + alog().split("\n", 1)[1]).off and
               result(check_audio_open, real1.replace(" -nosound", "") + alog().split("\n", 1)[1]).ok)
        expect("a player run's real line with -dev fails C10 and without it passes", not result(check_player_args, real1 + "rt: player mode\n").ok and
               result(check_player_args, real1.replace(" -dev -nosound", "") + "rt: player mode\n").ok)
        expect("dev_console reads the command and its source", dev_console('x\nrt: dev console "developer 1" (chord)\nrt: dev console "developer 0"\n') ==
               [(1, "1", "chord"), (2, "0", "")])
        expect("describe summarises a whole log", "written 17640000" in describe(alog() + blog()) and "bink opens 4" in describe(alog() + blog()))
        expect("stem drops directory, extension and case", stem("A/B\\NvidiaBloodLogo.BIK") == "nvidiabloodlogo")

        wp = os.path.join(tmp, "t.wav")
        sig = synth(1.0)
        write_wav(wp, Wav(RATE, 1, sig))
        back = read_wav(wp)
        expect("write_wav then read_wav returns the same samples", back.rate == RATE and back.ch == 1 and list(back.samples) == list(sig) and abs(back.seconds - 1.0) < 1e-9)
        expect("a WAV written by wave has consistent header sizes", wav_sizes_ok(open(wp, "rb").read())[0])
        data = bytearray(open(wp, "rb").read())
        struct.pack_into("<I", data, 4, 0)
        expect("mutation 6: unpatched RIFF size fails the header check", not wav_sizes_ok(bytes(data))[0])
        data = bytearray(open(wp, "rb").read())
        struct.pack_into("<I", data, 40, 0)
        expect("mutation 6: unpatched data size fails the header check", not wav_sizes_ok(bytes(data))[0])
        expect("header check refuses a non-WAV file and a missing data chunk", not wav_sizes_ok(b"hello")[0] and not wav_sizes_ok(bytes(data[:36]))[0])
        wbad = os.path.join(tmp, "bad.wav")
        open(wbad, "wb").write(b"RIFF\x00\x00\x00\x00WAVEjunk")
        expect("read_wav refuses garbage with WavError", raises(WavError, read_wav, wbad) is not None)
        w8 = os.path.join(tmp, "w8.wav")
        with contextlib.closing(wave.open(w8, "wb")) as f:
            f.setnchannels(1)
            f.setsampwidth(1)
            f.setframerate(RATE)
            f.writeframes(bytes(100))
        expect("read_wav refuses 8-bit PCM", raises(WavError, read_wav, w8) is not None)
        expect("read_wav refuses a missing file", raises(WavError, read_wav, os.path.join(tmp, "nope.wav")) is not None)
        rep = result(lambda r_: check_wav_file(r_, "x", None))
        expect("check_wav_file fails when no file was written", not rep.ok)
        rep = result(lambda r_: check_wav_file(r_, "x", os.path.join(tmp, "missing.wav")))
        expect("check_wav_file names a failed check, and does not raise, for a path that does not exist",
               not rep.ok and rep.failed == ["x: capture file written"])
        rep = result(lambda r_: check_wav_file(r_, "x", wp))
        expect("check_wav_file passes a good file", rep.ok)

        full = windows(Wav(RATE, 1, tone(1.0, 32767)))
        expect("a full-scale sine measures -3.01 dBFS", len(full) == 10 and all(abs(x.db + 3.01) < 0.1 for x in full))
        expect("a 0.01 sine is sound, a 0.001 sine is neither sound nor silent, a 0.0001 sine is silent",
               windows(Wav(RATE, 1, tone(0.5, 328)))[0].db > SOUND_DB and SILENT_DB < windows(Wav(RATE, 1, tone(0.5, 33)))[0].db <= SOUND_DB and
               windows(Wav(RATE, 1, tone(0.5, 3.3)))[0].db < SILENT_DB)
        expect("digital silence reads the floor", windows(Wav(RATE, 1, silence(0.5)))[0].db == FLOOR_DB)
        expect("windows are 100 ms and only whole ones count", len(windows(Wav(RATE, 1, silence(1.05)))) == 10)
        expect("windows honour start and end seconds", [round(x.t, 1) for x in windows(Wav(RATE, 1, silence(2.0)), 0.5, 1.0)] == [0.5, 0.6, 0.7, 0.8, 0.9])
        expect("rms over both channels: one loud channel and one silent channel read -6 dB from the loud one",
               abs(windows(stereo(tone(0.5, 32767), silence(0.5)))[0].db + 6.02) < 0.1)
        l, rgt = synth(1.0, 1), synth(1.0, 2)
        expect("left equal to right is dual mono, different channels are not",
               all(x.dual for x in windows(stereo(l, l))) and not any(x.dual for x in windows(stereo(l, rgt))))
        expect("sound only in the left channel is not dual mono", not any(x.dual for x in windows(stereo(l, silence(1.0)))))
        expect("a mono file is dual mono by definition", all(x.dual for x in windows(Wav(RATE, 1, l))))
        expect("a stereo file with right = 0.9998 left is still dual mono (>= 0.999)",
               all(x.dual for x in windows(stereo(l, array.array("h", (int(v * 0.9998) for v in l))))))
        sh = shares(windows(stereo(l, rgt)) + windows(stereo(silence(1.0), silence(1.0))))
        expect("shares counts sound, silent and stereo windows", (sh.total, sh.sound, sh.silent, sh.stereo) == (20, 10, 10, 10) and sh.sound_frac == 0.5 and sh.stereo_frac == 1.0)
        expect("shares of nothing is zero", shares([]).sound_frac == 0.0 and shares([]).stereo_frac == 0.0)

        half_quiet = stereo(synth(1.8, 3) + silence(2.2), synth(1.8, 4) + silence(2.2))
        rows = windows(half_quiet)
        expect("mutation 1: a capture whose second half is silent fails A5's 50 % sound",
               not result(check_sound_share, "A5", rows, A5_SOUND).ok and shares(rows).sound_frac <= 0.5)
        music_cap = stereo(synth(2.0, 3), synth(2.0, 4))
        expect("a capture of music passes the 50 % sound check and the 10 % stereo check",
               result(check_sound_share, "A5", windows(music_cap), A5_SOUND).ok and result(check_stereo_share, "A5", windows(music_cap)).ok)
        expect("mutation 2: a capture with right equal to left fails A5's 10 % stereo",
               not result(check_stereo_share, "A5", windows(stereo(synth(2.0, 3), synth(2.0, 3)))).ok)
        expect("mutation 2: a stereo share exactly under 10 % fails and over 10 % passes",
               not result(check_stereo_share, "A5", windows(stereo(synth(2.0, 3) + synth(0.2, 3), synth(2.0, 3) + synth(0.2, 8))), 0.2).ok and
               result(check_stereo_share, "A5", windows(stereo(synth(2.0, 3), synth(2.0, 3)[:44100 * 2 - 4410 * 3] + synth(0.3, 9)))).ok)
        expect("an empty window list fails both checks", not result(check_sound_share, "x", [], 0.5).ok and not result(check_stereo_share, "x", []).ok)
        expect("silence is found below -70 dBFS: a window of digital silence counts, a 3000 sine does not",
               shares(windows(Wav(RATE, 1, silence(1.0)))).silent == 10 and shares(windows(Wav(RATE, 1, tone(1.0, 3000)))).silent == 0)
        expect("the -50 dBFS edge: a sine at -49 dBFS is sound and at -51 dBFS is not",
               windows(Wav(RATE, 1, tone(0.5, 165)))[0].db > SOUND_DB and windows(Wav(RATE, 1, tone(0.5, 130)))[0].db <= SOUND_DB)
        expect("the -70 dBFS edge: a sine at -69 dBFS is not silent and at -71 dBFS is silent",
               windows(Wav(RATE, 1, tone(0.5, 17)))[0].db >= SILENT_DB and windows(Wav(RATE, 1, tone(0.5, 13)))[0].db < SILENT_DB)
        rng2 = random.Random(9)
        close = stereo(l, array.array("h", (int(v * (1 + 0.04 * rng2.uniform(-1, 1))) for v in l)))
        apart = stereo(l, array.array("h", (int(v * (1 + 0.10 * rng2.uniform(-1, 1))) for v in l)))
        expect("the dual-mono edge at 0.999: a right channel with 4 % gain jitter is dual mono and one with 10 % jitter is not",
               all(x.dual for x in windows(close)) and not any(x.dual for x in windows(apart)))

        base = synth(2.0, 5)
        mono_base = Wav(RATE, 1, base)

        def m(cap, ref, rate=RATE):
            return match(mono(ref), mono(cap), rate)

        expect("the audio knobs keep their spec values", (MATCH_SOUND, MATCH_IDENT, MATCH_CHARGEN, DUAL_MONO, SOUND_DB, SILENT_DB, WINDOW_MS, LAG_S, A5_SOUND, A5_STEREO, B3_SOUND) ==
               (0.8, 0.99, 0.6, 0.999, -50.0, -70.0, 100, 5.0, 0.5, 0.1, 0.8))
        expect("the clock and sync knobs keep their spec values: A4 +/-2 % and 200 ms, C5 +/-3 % and +/-100 ms, C4 +/-2 %, load 8",
               (CLOCK_TOL, CLOCK_QUEUED_MS, LENGTH_TOL, DRIFT_MS, SECONDS_TOL, LOAD_PERF) == (0.02, 200.0, 0.03, 100.0, 0.02, 8.0))
        pk, lg = m(mono_base, mono_base)
        expect(f"match of a capture with itself is 1.0 at lag 0 ({pk:.4f}, {lg})", pk > 0.9999 and lg == 0)
        pad = Wav(RATE, 1, silence(2.5) + base)
        pk, lg = m(pad, mono_base)
        expect(f"mutation 3: a capture with the reference 2.5 s in still matches at 0.99 or more ({pk:.4f}, lag {lg / RATE:.4f} s)", pk >= MATCH_IDENT and abs(lg - int(2.5 * RATE)) <= 1)
        pk, lg = m(Wav(RATE, 1, silence(7.0) + base), mono_base)
        expect(f"the reference is found anywhere in the capture, not only within 5 s ({pk:.4f}, lag {lg / RATE:.3f} s)", pk >= MATCH_IDENT and abs(lg - 7 * RATE) <= 1)
        stall = Wav(RATE, 1, silence(3.0) + base + silence(1.0))
        expect("a 3 s starvation stall before the sound still matches when the capture window is widened by the lag limit",
               result(check_match, "stall", mono_base, stall, MATCH_SOUND, (0.0, 2.0), (0.0, 2.0 + 2 * LAG_S)).ok)
        expect("the same stall misses the sound when the capture window is not widened (the old alignment gave 0.0)",
               not result(check_match, "stall", mono_base, stall, MATCH_SOUND, (0.0, 2.0), (0.0, 2.0)).ok and m(Wav(RATE, 1, silence(3.0)), mono_base)[0] == 0.0)
        voice = synth(3.0, 41)
        long_cap = Wav(RATE, 1, silence(30.0) + voice + silence(2.0))
        pk, lg = m(long_cap, Wav(RATE, 1, voice))
        expect(f"B4: a voice line at 30 s in a 35 s capture is found with the whole capture as the search range ({pk:.4f}, {lg / RATE:.2f} s)",
               pk >= MATCH_IDENT and abs(lg - 30 * RATE) <= 1)
        expect("B4: the same capture passes check_match with no spans, so the silence around the voice does not dilute it",
               result(check_match, "voice", Wav(RATE, 1, voice), long_cap, MATCH_CHARGEN).ok)
        sw = array.array("h", base)
        sw.byteswap()
        expect("mutation 4: a byte-swapped copy scores below the literal 0.8", m(Wav(RATE, 1, sw), mono_base)[0] < 0.8)
        expect("mutation 4: a byte-swapped copy fails the match", m(Wav(RATE, 1, sw), mono_base)[0] < MATCH_SOUND)
        expect("mutation 4: a double-speed copy fails the match", m(Wav(RATE, 1, base[::2]), mono_base)[0] < MATCH_SOUND)
        expect("a half-speed copy fails the match", m(Wav(RATE, 1, array.array("h", (v for v in base[:len(base) // 2] for _ in (0, 1)))), mono_base)[0] < MATCH_SOUND)
        expect("mutation: a capture of zeros has match 0", m(Wav(RATE, 1, silence(2.0)), mono_base)[0] == 0.0)
        expect("a reference of zeros has match 0", m(mono_base, Wav(RATE, 1, silence(2.0)))[0] == 0.0)
        expect("unrelated music has a low match", m(Wav(RATE, 1, synth(2.0, 77)), mono_base)[0] < 0.5)
        expect("the match ignores gain", m(Wav(RATE, 1, array.array("h", (int(v * 0.3) for v in base))), mono_base)[0] >= MATCH_IDENT)
        noisy = random.Random(3)
        expect("the match survives mild noise", m(Wav(RATE, 1, array.array("h", (max(-32768, min(32767, v + noisy.randint(-900, 900))) for v in base))), mono_base)[0] >= 0.95)
        expect("a stereo capture is mixed to mono before matching", m(stereo(base, base), mono_base)[0] > 0.9999)
        sec = Wav(RATE, 1, base[44100 // 2:44100 // 2 + 44100])
        pk, lg = m(mono_base, sec)
        expect("a one-second reference inside a two-second capture matches at the right offset", pk >= MATCH_IDENT and abs(lg - 44100 // 2) <= 1)
        expect("a two-second reference with only one second captured does not match", m(sec, mono_base)[0] < 0.95)
        expect("a partial overlap is not a full match", m(Wav(RATE, 1, synth(1.0, 5) + synth(1.0, 55)), mono_base)[0] < 0.95)
        expect("a capture under 100 ms has match 0", match(array.array("d", [1.0] * 100), array.array("d", [1.0] * 100), RATE)[0] == 0.0)
        b48 = synth(2.0, 6, 48000)
        expect("the match works at 48000 Hz", match(mono(Wav(48000, 1, b48)), mono(Wav(48000, 1, silence(0.5, 48000) + b48)), 48000)[0] >= MATCH_IDENT)
        b8 = synth(1.5, 7, 8000)
        expect("the match works at 8000 Hz", match(mono(Wav(8000, 1, b8)), mono(Wav(8000, 1, silence(0.5, 8000) + b8)), 8000)[0] >= MATCH_IDENT)
        expect("check_match refuses two different rates", not result(check_match, "x", mono_base, Wav(48000, 1, b48), 0.8).ok)
        expect("check_match passes a good match and fails a bad one",
               result(check_match, "x", mono_base, Wav(RATE, 1, silence(1.0) + base), 0.8).ok and
               not result(check_match, "x", mono_base, Wav(RATE, 1, synth(2.0, 99)), 0.8).ok)
        expect("check_match searches only the stated capture span and takes only the stated reference span",
               result(check_match, "x", mono_base, Wav(RATE, 1, silence(1.0) + base), 0.8, (0.0, None), (1.0, 3.0)).ok and
               not result(check_match, "x", mono_base, Wav(RATE, 1, silence(1.0) + base), 0.8, (0.0, None), (0.0, 1.0)).ok and
               result(check_match, "x", mono_base, Wav(RATE, 1, silence(1.0) + base), 0.8, (0.5, 1.5), (0.0, 3.0)).ok)

        expect("mutation 7: a bink capture 2.5 % too long fails C4's length", not result(check_duration, "x", Wav(RATE, 1, silence(10.4 * 1.025)), 10.4).ok)
        expect("a capture 1.9 % off passes, 2.1 % fails", result(check_duration, "x", Wav(RATE, 1, silence(10.4 * 1.019)), 10.4).ok and
               not result(check_duration, "x", Wav(RATE, 1, silence(10.4 * 0.979)), 10.4).ok)
        short = blog()
        caps = {k: Wav(8000, 1, synth(1.0, i, 8000)) for i, k in enumerate(INTROS)}
        durs = dict(zip(INTROS, (o_f / fp for o_f, fp in zip(INTRO_FRAMES, FPS))))
        okcaps = {k: Wav(8000, 1, silence(durs[k], 8000)) for k in INTROS}
        rep = result(check_bink_audio, blog(), okcaps, {k: okcaps[k] for k in INTROS})
        expect("C4: silent captures of the right length fail the match, not the length", [x for x in rep.failed if "lasts" in x] == [] and any("match" in x for x in rep.failed))
        exact = {}
        for i, k in enumerate(INTROS):
            exact[k] = Wav(8000, 1, (synth(1.0, 20 + i, 8000) * (int(durs[k]) + 2))[:int(durs[k] * 8000)])
        expect("C4: captures of the right length that equal the reference pass", result(check_bink_audio, blog(), exact, exact).ok)
        expect("mutation: the 48000 Hz track played as 44100 Hz fails C4's length",
               not result(check_bink_audio, blog(), {**exact, "troika": Wav(8000, 1, exact["troika"].samples[:int(len(exact["troika"].samples) * 44100 / 48000)])}, exact).ok)
        expect("C4: a missing capture or reference fails", not result(check_bink_audio, blog(), {}, exact).ok and not result(check_bink_audio, blog(), exact, {}).ok)

        a_ok = alog()
        expect("A2 passes a normal sound-on log", result(check_audio_open, a_ok).ok)
        expect("A2 fails on a 22050 Hz app format, on no open line, on written 0 and on Unable to init audio",
               not result(check_audio_open, alog(app="22050/2/s16")).ok and not result(check_audio_open, "rt: stage 11 menu\n").ok and
               not result(check_audio_open, alog(written=0)).ok and not result(check_audio_open, alog(init_fail=True)).ok)
        expect("A7 nosound log passes and a log with an open line fails", result(check_nosound_log, alog(off=True, args="-dev -nosound")).ok and
               not result(check_nosound_log, alog(args="-dev -nosound")).ok and not result(check_nosound_log, alog(off=True)).ok)
        s100 = alog(written=100 * BYTES_S, played=100 * BYTES_S)
        expect("A4 passes written and played within 2 % of the seconds", result(check_clock, s100, 2.0, 102.0).ok)
        expect("mutation: WHDR_DONE on Write makes written exceed the A4 bound", not result(check_clock, alog(written=110 * BYTES_S, played=100 * BYTES_S), 2.0, 102.0).ok)
        def clk(w=100.0, p=100.0, q=93.0):
            return alog(written=int(w * BYTES_S), played=int(p * BYTES_S), maxq=q)

        expect("A4 bound on written: 101.9 s of 100 s passes and 102.1 s fails", result(check_clock, clk(w=101.9), 2.0, 102.0).ok and
               not result(check_clock, clk(w=102.1), 2.0, 102.0).ok)
        expect("A4 bound on played: 98.1 s of 100 s passes and 97.9 s fails", result(check_clock, clk(p=98.1), 2.0, 102.0).ok and
               not result(check_clock, clk(p=97.9), 2.0, 102.0).ok)
        expect("A4 bound on the queue: 200 ms passes and 200.5 ms fails", result(check_clock, clk(q=200.0), 2.0, 102.0).ok and
               not result(check_clock, clk(q=200.5), 2.0, 102.0).ok)
        expect("A8 bound on the silent clock: 2 % inside passes and outside fails",
               result(check_silent_clock, alog(silent="x", written=int(61.1 * BYTES_S)) + "rt: stage 11 menu\n", 60.0).ok and
               not result(check_silent_clock, alog(silent="x", written=int(61.3 * BYTES_S)) + "rt: stage 11 menu\n", 60.0).ok)
        expect("A4 fails played short of the clock", not result(check_clock, alog(written=100 * BYTES_S, played=90 * BYTES_S), 2.0, 102.0).ok)
        expect("A4 fails steady underruns and a deep queue", not result(check_clock, alog(written=100 * BYTES_S, played=100 * BYTES_S, steady=1), 2.0, 102.0).ok and
               not result(check_clock, alog(written=100 * BYTES_S, played=100 * BYTES_S, maxq=250.0), 2.0, 102.0).ok)
        expect("A4 fails without an exit line or times", not result(check_clock, "rt: audio open\n", 2.0, 102.0).ok and not result(check_clock, s100, None, 102.0).ok)
        sil = alog(silent="no device") + "rt: stage 11 menu\n"
        expect("A8 passes a silent clock log at the clock", result(check_silent_clock, sil.replace("written 17640000", f"written {60 * BYTES_S}"), 60.0).ok)
        expect("A8 fails without the silent clock line, without stage 11, off the clock and with Unable to init audio",
               not result(check_silent_clock, alog() + "rt: stage 11 menu\n", 100.0).ok and not result(check_silent_clock, alog(silent="x"), 100.0).ok and
               not result(check_silent_clock, sil, 60.0).ok and not result(check_silent_clock, sil + "guest: Unable to init audio\n", 100.0).ok)
        expect("A7 quit passes with resets 1 and an exit within 10 s", result(check_quit_audio, alog(resets=1), 3.0).ok)
        expect("A7 quit fails with resets 0, a late exit and no exit", not result(check_quit_audio, alog(resets=0), 3.0).ok and not result(check_quit_audio, alog(), 11.0).ok and
               not result(check_quit_audio, alog(), None).ok)
        wr = [["1", f"WINMM.dll!{n}", "shim", "engine", "f", "1"] for n in WAVE_ROWS]
        expect("A1 first-calls rows: six shim rows pass", result(check_wave_rows, wr).ok)
        expect("A1 first-calls rows: a stub row, a missing row and a bridge row fail", not result(check_wave_rows, wr[:5] + [["1", "WINMM.dll!waveOutWrite", "stub", "engine", "f", "1"]]).ok and
               not result(check_wave_rows, wr[:5]).ok and not result(check_wave_rows, wr[:5] + [["1", "WINMM.dll!waveOutWrite", "bridge", "engine", "f", "1"]]).ok)
        stubrow = [["1", "WINMM.dll!waveOutWrite", "stub", "engine", "f", "1"]]
        expect("mutation 6: a WINMM waveOutWrite stub row in the first-calls TSV fails the run rule (m6.check_stubs)", not result(m6.check_stubs, stubrow).ok and
               result(m6.check_stubs, wr).ok)
        imp = "WINMM.dll!waveOutWrite 0x20173248 stub\n"
        expect("mutation 6: a waveOutWrite stub in X --list-imports after A fails and before A does not", not result(check_stub_list, imp, {"A"}).ok and
               result(check_stub_list, imp, set()).ok)
        expect("X --list-imports: mss32 stubs fail after B only, and binkw32 stubs fail after C except _BinkOpenDirectSound@4",
               not result(check_stub_list, "mss32.dll!AIL_x 0x1 stub\n", {"B"}).ok and result(check_stub_list, "mss32.dll!AIL_x 0x1 stub\n", {"A"}).ok and
               not result(check_stub_list, "binkw32.dll!_BinkClose@4 0x1 stub\n", {"C"}).ok and
               result(check_stub_list, f"binkw32.dll!{BINK_KEEP} 0x1 stub\n", {"C"}).ok and
               result(check_stub_list, "binkw32.dll!_BinkClose@4 0x1 shim\n", {"A", "B", "C"}).ok)
        expect("stub_rows reads only three-field stub lines", stub_rows("a.dll!x 0x1 stub\nb.dll!y 0x2 shim\njunk\n") == ["a.dll!x"])
        expect("C8: a -dev run with no movie passes, an open line or a _BinkOpen row fails",
               result(check_no_movie, alog(), []).ok and not result(check_no_movie, blog(), []).ok and
               not result(check_no_movie, alog(), [["1", "binkw32.dll!_BinkOpen@8", "shim", "e", "f", "1"]]).ok)
        pl = alog(args="-window -w 1280 -h 1024") + "rt: player mode\n"
        expect("C10: a player log has no -dev, no +developer and the player line", result(check_player_args, pl).ok)
        expect("mutation: adding -dev back fails C10", not result(check_player_args, pl.replace("1024", "1024 -dev")).ok)
        expect("C10 fails +developer, -nosound and a missing player line", not result(check_player_args, pl.replace("1024", "1024 +developer 1")).ok and
               not result(check_player_args, pl.replace("1024", "1024 -nosound")).ok and not result(check_player_args, alog(args="-window")).ok)

        mp = "rt: mp3 open #1 rate 44100 ch 2\nrt: mp3 decoders opened 1 closed 1 failed 0 frames 100 errors 0\n"
        expect("B2 passes rate 44100 ch 2 with failed 0 and errors 0", result(check_mp3_music, mp).ok)
        expect("B2 fails a mono open, a failed decoder, an error and no exit line", not result(check_mp3_music, mp.replace("ch 2", "ch 1")).ok and
               not result(check_mp3_music, mp.replace("failed 0", "failed 1")).ok and not result(check_mp3_music, mp.replace("errors 0", "errors 2")).ok and
               not result(check_mp3_music, mp.split("\n")[0]).ok)
        expect("B6 passes 32 open decoders and fails 33", result(check_mp3_leak, mp.replace("opened 1 closed 1", "opened 40 closed 8") + "rt: allocator high-water 0x1000 bytes, 0 failed allocations\n").ok and
               not result(check_mp3_leak, mp.replace("opened 1 closed 1", "opened 41 closed 8") + "rt: allocator high-water 0x1000 bytes, 0 failed allocations\n").ok)
        expect("mutation: close without uninit and count fails B6", not result(check_mp3_leak, mp.replace("opened 1 closed 1", "opened 90 closed 0") + "rt: allocator high-water 0x1000 bytes, 0 failed allocations\n").ok)
        expect("B6 fails failed allocations and no exit line", not result(check_mp3_leak, mp + "rt: allocator high-water 0x1000 bytes, 3 failed allocations\n").ok and
               not result(check_mp3_leak, "rt: allocator high-water 0x1000 bytes, 0 failed allocations\n").ok)
        music_ref = stereo(synth(30.0, 5), synth(30.0, 6))
        track = "sound/music/disturbed_and_twisted.mp3"
        expect("offline music: the decoded track passes, found at lag 0 over the ffmpeg reference's first 20 s",
               result(check_music_pair, track, music_ref, music_ref).ok)
        expect("mutation: silence in place of the track fails the offline music check",
               not result(check_music_pair, track, stereo(silence(30.0), silence(30.0)), music_ref).ok)
        expect("mutation: another signal of the right length (the menu theme's audio) fails the offline music check",
               not result(check_music_pair, track, stereo(synth(30.0, 7), synth(30.0, 8)), music_ref).ok)
        expect("mutation: the menu theme as the track under test fails even when its decode matches",
               not result(check_music_pair, "sound/music/Vampire_Theme.mp3", music_ref, music_ref).ok)
        expect("mutation: a truncated decode fails the length check",
               not result(check_music_pair, track, stereo(synth(20.0, 5), synth(20.0, 6)), music_ref).ok)
        expect("music_decodes keeps only .mp3 decodes under music/",
               music_decodes([("sound/music/a.mp3", "x.wav"), ("sound/area/b.mp3", "y.wav"), ("media/c.bik", "z.wav")]) == [("sound/music/a.mp3", "x.wav")])

        known = {"surfaces/scrape1.wav", "music/vampire_theme.mp3"}
        miss = "guest: Sound 'sound/zz/none.wav' could not precache, SoundLength unknown\n"
        have = "guest: Sound 'sound\\surfaces\\Scrape1.WAV' could not precache, SoundLength unknown\n"
        expect("A6 passes lines that name missing files", result(check_a6, "guest: x\n" + miss, known).ok and result(check_a6, "", known).ok)
        expect("A6 fails a line that names an existing file, case and slash blind, and a line with no file name",
               not result(check_a6, miss + have, known).ok and not result(check_a6, "guest: could not precache SoundLength\n", known).ok)
        expect("A6 ignores could-not-precache lines without SoundLength", result(check_a6, "guest: " + "'sound/surfaces/scrape1.wav' could not precache\n", known).ok)
        expect("A6 control needs a line naming an existing file", result(check_a6_control, have, known).ok and not result(check_a6_control, miss, known).ok and
               not result(check_a6_control, "", known).ok)

        ib = alog() + blog()
        irows = [["1", "binkw32.dll!_BinkOpen@8", "shim", "engine", "f", "4"]]
        expect("C2 passes four movies in order with full frames", result(check_intro, ib, irows).ok)
        expect("mutation 5: rt: bink close shown 311/312 fails C2", not result(check_intro, alog() + blog(shown=(311, 661, 420, 221)), irows).ok)
        expect("mutation: FrameNum starting at 0 makes shown 313/312 fail C2", not result(check_intro, alog() + blog(shown=(313, 661, 420, 221)), irows).ok)
        expect("C2 fails a wrong order, a skipped movie, a failed line, three opens and _BinkOpen called 3 times",
               not result(check_intro, blog(names=("whitewolf", "activision", "nvidiabloodlogo", "troika")), irows).ok and
               not result(check_intro, blog(skipped=(1, 0, 0, 0)), irows).ok and not result(check_intro, blog(fail=("x.bik",)), irows).ok and
               not result(check_intro, "\n".join(blog().splitlines()[2:]) + "\n", irows).ok and
               not result(check_intro, blog(), [["1", "binkw32.dll!_BinkOpen@8", "shim", "engine", "f", "3"]]).ok and not result(check_intro, blog(), []).ok)
        expect("C2 av drift: 0.5 s fails, 0.05 s passes",
               not result(check_intro, blog(drift=(500, 0, 0, 0)), irows).ok and result(check_intro, blog(drift=(50, -50, 0, 0)), irows).ok)
        expect("C2 fails a movie whose frames and fps make the wrong length", not result(check_intro, blog(frames=(300, 661, 420, 221), shown=(300, 661, 420, 221)), irows).ok)
        expect("C7 passes four skipped lines in order and nothing else", result(check_novid, "".join(f"rt: bink skipped Vampire/media/{SHOWN[k]}.bik (--novid)\n" for k in INTROS)).ok)
        expect("C7 fails an open line, a wrong order and three lines", not result(check_novid, blog()).ok and
               not result(check_novid, "".join(f"rt: bink skipped Vampire/media/{k}.bik (--novid)\n" for k in INTROS[::-1])).ok and
               not result(check_novid, "".join(f"rt: bink skipped Vampire/media/{k}.bik (--novid)\n" for k in INTROS[:3])).ok)
        sk = blog(shown=(40, 90, 420, 221), skipped=(1, 1, 0, 0))
        expect("C6 passes two skips, each next movie within 1 s, movies 3 and 4 whole", result(check_skip, sk, [10.0, 20.0], [5.0, 10.5, 20.8, 25.0]).ok)
        expect("C6 fails a skipped movie 3, a slow next open, a missing skip and a movie not skipped",
               not result(check_skip, blog(shown=(40, 90, 420, 100), skipped=(1, 1, 0, 1)), [10.0, 20.0], [5.0, 10.5, 20.8, 25.0]).ok and
               not result(check_skip, sk, [10.0, 20.0], [5.0, 12.5, 20.8, 25.0]).ok and not result(check_skip, sk, [10.0], [5.0, 10.5, 20.8, 25.0]).ok and
               not result(check_skip, blog(shown=(312, 90, 420, 221), skipped=(0, 1, 0, 0)), [10.0, 20.0], [5.0, 10.5, 20.8, 25.0]).ok)
        to = [1.0, 11.4, 33.5, 47.5]
        tc = [11.4, 33.5, 47.5, 62.2]
        expect("C5 passes drift within 100 ms and times within 3 %", result(check_sync, blog(drift=(10, -50, 99, 0)), to, tc).ok)
        durs5 = [f / p for f, p in zip(INTRO_FRAMES, FPS)]

        def closes(first):
            return [to[0] + first, to[1] + durs5[1], to[2] + durs5[2], to[3] + durs5[3]]

        expect("C5 length bound: 2.9 % over the movie's length passes and 3.1 % fails",
               result(check_sync, blog(), to, closes(durs5[0] * 1.029)).ok and not result(check_sync, blog(), to, closes(durs5[0] * 1.031)).ok)
        expect("C5 length bound: 2.9 % under passes and 3.1 % under fails",
               result(check_sync, blog(), to, closes(durs5[0] * 0.971)).ok and not result(check_sync, blog(), to, closes(durs5[0] * 0.969)).ok)
        expect("C5 drift bound: 100 ms passes and 100.5 ms fails", result(check_sync, blog(drift=(100, 0, 0, 0)), to, closes(durs5[0])).ok and
               not result(check_sync, blog(drift=(100.5, 0, 0, 0)), to, closes(durs5[0])).ok)
        expect("mutation 8: av drift 120 ms fails C5", not result(check_sync, blog(drift=(120, 0, 0, 0)), to, tc).ok)
        expect("mutation: Wait always 0 plays the movie fast and fails C5's open-to-close time", not result(check_sync, blog(), to, [3.0, 5.0, 7.0, 9.0]).ok)
        expect("C5 fails missing times", not result(check_sync, blog(), to[:2], tc[:2]).ok)

        def pic(w, h, inner, level=128):
            iw, ih = inner
            x0, y0 = (w - iw) // 2, (h - ih) // 2
            rows = []
            for y in range(h):
                row = bytearray(3 * w)
                if y0 <= y < y0 + ih:
                    row[3 * x0:3 * (x0 + iw)] = bytes([level]) * (3 * iw)
                rows.append(bytes(row))
            return (w, h, rows)

        expect("C3 bars: black outside the centred area passes", result(check_bars, pic(1280, 1024, (640, 480)), (640, 480), "i3").ok)
        pb = pic(1280, 1024, (800, 400))
        wb = bytearray(pb[2][5])
        wb[10] = 9
        expect("C3 bars: a pixel 9 above black outside the area fails and 8 passes", not result(check_bars, (1280, 1024, [bytes(wb)] + pb[2][1:]), (800, 400), "i4").ok and
               (wb.__setitem__(10, 8) or result(check_bars, (1280, 1024, [bytes(wb)] + pb[2][1:]), (800, 400), "i4").ok))
        expect("C3 bars: a full-window picture where a centred one is expected fails", not result(check_bars, pic(1280, 1024, (1280, 1024)), (640, 480), "i3").ok)
        base_img, moved = m4.picture(), m4.with_square(m4.picture(), 20, 20, 128, (0, 0, 0))
        mask = {"width": 320, "height": 256, "rects": [[0, 0, 320, 256]]}
        expect("mutation: skipping CopyToBuffer (a blank window) fails C3's compare", not m4.compare(moved, base_img, mask, DEF["tol"])[0] and m4.compare(base_img, base_img, mask, DEF["tol"])[0])

        tlog = "\n".join(["rt: script 3 chord 8", 'rt: dev console "developer 1" (chord)', "guest: dots", "rt: script 5 chord 8",
                          'rt: dev console "developer 0" (chord)', "rt: frames n 1 p50 1 p95 1 p99 1 max 1"]) + "\n"
        expect("C11 log: chord, developer 1, chord, developer 0 in order passes", result(check_toggle_log, tlog).ok)
        expect("mutation: a toggle that does not send the command fails C11's log check", not result(check_toggle_log, "rt: script 3 chord 8\nrt: script 5 chord 8\n").ok)
        expect("mutation: a counter instead of a flag (two command lines per chord) fails C11",
               not result(check_toggle_log, tlog.replace('rt: dev console "developer 1" (chord)\n', 'rt: dev console "developer 1" (chord)\nrt: dev console "developer 1" (chord)\n')).ok)
        expect("mutation: always developer 1 fails C11", not result(check_toggle_log, tlog.replace("developer 0", "developer 1")).ok)
        expect("C11 fails developer console text after developer 0 and a command before its chord",
               not result(check_toggle_log, tlog + "guest: late\n").ok and
               result(check_toggle_log, tlog + "rt: close-after: requesting window close\nguest: Reference Count for Material x\n").ok and
               result(check_toggle_log, tlog + "rt: message WM_CLOSE to 0x00010004\nguest: Reference Count for Material x\n").ok and
               not result(check_toggle_log, tlog + "guest: late\nrt: message WM_CLOSE to 0x00010004\n").ok and
               not result(check_toggle_log, tlog.replace("rt: script 3 chord 8\n", "").replace("rt: script 5 chord 8\n", "rt: script 5 chord 8\nrt: script 6 chord 8\n")).ok)
        for k, v in {"m7-dots-mask.json": mask, "m7-menu-player-mask.json": mask}.items():
            json.dump(v, open(os.path.join(tmp, k), "w"))
        expect("C11 images: dev-on differs from menu-player-a and dev-off matches it", result(check_toggle_images, None, [base_img, moved, base_img], tmp).ok)
        expect("C11 images: dev-on equal to menu-player-a fails", not result(check_toggle_images, None, [base_img, base_img, base_img], tmp).ok)
        expect("mutation: dots still showing at dev-off fails C11", not result(check_toggle_images, None, [base_img, moved, moved], tmp).ok)
        expect("C11 images: a missing mask fails", not result(check_toggle_images, None, [base_img, moved, base_img], os.path.join(tmp, "none")).ok)
        expect("fail.c check: no _BinkOpen row passes, the row fails", result(check_fail_c, '{"binkw32.dll", "_BinkOpenDirectSound@4"}').ok and
               not result(check_fail_c, '{"binkw32.dll", "_BinkOpen@8", 2}').ok)

        def st(p50, p99, steady=0, pump=40):
            return SimpleNamespace(p50=p50, p99=p99, steady=steady, pump=pump, sound=True)

        on = [st(16.6, 20.0), st(16.7, 20.5), st(16.6, 20.2)]
        off = [st(16.4, 19.5), st(16.6, 19.8), st(16.5, 20.0)]
        expect("D1 passes deltas within 0.5 and 2 ms", result(check_perf, "p", on, off).ok)
        expect("D1 fails a p50 delta above 0.5 ms", not result(check_perf, "p", [st(17.2, 20.0)] * 3, off).ok)
        expect("D1 fails a p99 delta above 2 ms", not result(check_perf, "p", [st(16.5, 23.0)] * 3, off).ok)
        expect("D1 fails a steady underrun and a pump gap above 100 ms", not result(check_perf, "p", [st(16.6, 20.0, steady=1)] * 3, off).ok and
               not result(check_perf, "p", on, [st(16.5, 20.0, pump=150)] * 3).ok)
        expect("D1 uses medians, so one bad run does not fail it", result(check_perf, "p", [st(16.6, 20.0), st(16.7, 20.1), st(30.0, 90.0)], off).ok)
        expect("D1 fails with no runs or a run without a frames line", not result(check_perf, "p", [], off).ok and not result(check_perf, "p", [st(None, None)], off).ok)
        rs = run_stats(alog() + "rt: frames n 100 p50 16.6 p95 18.0 p99 21.3 max 40.0\nrt: max pump gap steady 33 ms\n")
        expect("run_stats reads p50, p99, steady underruns and the steady pump gap", (rs.p50, rs.p99, rs.steady, rs.pump) == (16.6, 21.3, 0, 33) and run_stats("").p50 is None)

        def canned(top="CPU usage: 5.0% user, 5.0% sys, 90.0% idle", gpu='"Device Utilization %"=12', ps=()):
            def run(cmd):
                if cmd[0] == "top":
                    return "CPU usage: 50% user, 40% sys, 10% idle \n" + top + "\n"
                if cmd[0] == "ioreg":
                    return gpu + "\n"
                if cmd[:3] == ["ps", "-axo", "pid=,comm="]:
                    return "\n".join(f" {p} {c}" for p, c in [(1, "/sbin/launchd"), *ps])
                if cmd[:3] == ["ps", "-axo", "pid=,command="]:
                    return "\n".join(f" {p} {c}" for p, c in [(1, "/sbin/launchd"), *[(p, f"{c} x") for p, c in ps]])
                return ""
            return run

        expect("cpu_idle reads the last sample, gpu_busy the busiest accelerator",
               cpu_idle(canned()) == 90.0 and gpu_busy(canned(gpu='"Device Utilization %"=12\n"Device Utilization %"=44')) == 44 and
               cpu_idle(lambda c: "") is None and gpu_busy(lambda c: "") is None)
        expect("a quiet machine has no gate reasons", gate7(1.0, canned(), {1}) == [])
        expect("CPU idle below 35 % is a reason, 35.0 is not", len(gate7(1.0, canned(top="CPU usage: 1% user, 1% sys, 34.9% idle"), {1})) == 1 and
               gate7(1.0, canned(top="CPU usage: 1% user, 1% sys, 35.0% idle"), {1}) == [])
        expect("functional runs: GPU above 60 % is a reason, 60 is not", len(gate7(1.0, canned(gpu='"Device Utilization %"=61'), {1})) == 1 and
               gate7(1.0, canned(gpu='"Device Utilization %"=60'), {1}) == [])
        expect("functional runs: a GPU at 52 % passes", gate7(1.0, canned(gpu='"Device Utilization %"=52'), {1}) == [])
        expect("functional runs have no load-average gate", gate7(14.6, canned(), {1}) == [] and gate7(99.0, canned(), {1}) == [])
        expect("performance runs: GPU above 30 % is a reason, 30 is not", len(gate7(1.0, canned(gpu='"Device Utilization %"=31'), {1}, perf=True)) == 1 and
               gate7(1.0, canned(gpu='"Device Utilization %"=30'), {1}, perf=True) == [])
        expect("performance runs: a load average of 8 is a reason, 7.99 is not", len(gate7(8.0, canned(), {1}, perf=True)) == 1 and gate7(7.99, canned(), {1}, perf=True) == [])
        expect("performance runs keep the CPU idle gate", len(gate7(1.0, canned(top="CPU usage: 1% user, 1% sys, 34.9% idle"), {1}, perf=True)) == 1)
        expect("unreadable CPU or GPU is a reason, not a pass", len(gate7(1.0, lambda c: "", {1})) == 2 and len(gate7(1.0, lambda c: "", {1}, perf=True)) == 2)
        expect("a running screencapture blocks performance runs only", busy7(canned(ps=[(500, "/usr/sbin/screencapture")]), {1}) == [] and
               len(busy7(canned(ps=[(500, "/usr/sbin/screencapture")]), {1}, True)) == 1)
        expect("readings print load, CPU idle and GPU", fmt_readings(SimpleNamespace(load=1.5, idle=80.0, gpu=12)) == "load average 1.50, CPU idle 80.0 %, GPU 12 %")
        for exe, label in (("/Users/x/build/meson/bloodlines-native", "game"), ("/opt/homebrew/bin/ninja", "ninja"), ("/usr/bin/clang", "clang"),
                           ("/usr/bin/ld", "ld"), ("/usr/bin/clang++", "clang++")):
            expect(f"a running {label} is a reason by exe name for performance runs", len(busy7(canned(ps=[(500, exe)]), {1}, True)) == 1)
            expect(f"a running {label} blocks functional runs only if it is the game",
                   len(busy7(canned(ps=[(500, exe)]), {1})) == (1 if label == "game" else 0))
        expect("a game counted by exe name: a different name and a path containing the name do not count",
               busy7(canned(ps=[(500, "/Users/x/bloodlines-native/tools/other"), (501, "/usr/bin/vim"), (502, "/usr/bin/ldd")]), {1}) == [])
        expect("two games are two reasons", len(busy7(canned(ps=[(500, "/a/bloodlines-native"), (501, "/b/bloodlines-native")]), {1})) == 2)
        expect("a running build_all.py under python is a reason for performance runs", len(busy7(canned(ps=[(500, "/usr/bin/python3 /x/build_all.py")]), {1}, True)) == 1 and
               busy7(canned(ps=[(500, "/usr/bin/vim build_all.py")]), {1}, True) == [])
        build_ps = canned(ps=[(500, "/usr/bin/python3 /x/build_all.py"), (501, "/opt/homebrew/bin/ninja"), (502, "/usr/bin/clang")])
        expect("a running build_all.py, ninja and clang do not block a functional run", busy7(build_ps, {1}) == [] and len(busy7(build_ps, {1}, True)) == 3)
        expect("functional runs log the builds that are running",
               build_note(False, build_ps, {1}).count("pid") == 3 and "not blocking" in build_note(False, build_ps, {1}) and build_note(True, build_ps, {1}) == "" and
               build_note(False, canned(), {1}) == "")
        expect("functional defaults are 5 % idle and 95 % GPU", env_num("RT_GATE_IDLE", 5.0, 5.0, 35.0, {}) == 5.0 and env_num("RT_GATE_GPU", 95, 60, 95, {}) == 95 and
               IMPORT_GATE == (env_num("RT_GATE_IDLE", 5.0, 5.0, 35.0, os.environ), int(env_num("RT_GATE_GPU", 95, 60, 95, os.environ))))
        with patched(me, IDLE_MIN=5.0, GPU_FUNC=95):
            heavy = canned(top="CPU usage: 1% user, 1% sys, 8.0% idle", gpu='"Device Utilization %"=90',
                           ps=[(500, "/opt/homebrew/bin/ninja"), (501, "/usr/bin/python3 /x/build_all.py")])
            expect("functional mode starts at GPU 90 %, CPU idle 8 % and a build running", gate7(1.0, heavy, {1}) == [])
            expect("performance mode still refuses GPU 90 %, CPU idle 8 % and a build running separately",
                   any("GPU utilisation 90 %" in w for w in gate7(1.0, heavy, {1}, perf=True)) and
                   any("CPU idle 8.0 %" in w for w in gate7(1.0, heavy, {1}, perf=True)) and
                   len(gate7(1.0, canned(ps=[(500, "/opt/homebrew/bin/ninja")]), {1}, perf=True)) == 1 and
                   len(gate7(1.0, canned(gpu='"Device Utilization %"=96'), {1})) == 1 and len(gate7(1.0, canned(top="CPU usage: 1% user, 1% sys, 4.9% idle"), {1})) == 1)
            expect("a second game still blocks a functional run", len(gate7(1.0, canned(ps=[(500, "/a/bloodlines-native")]), {1})) == 1)
        expect("the harness's own ancestors are not busy", busy7(canned(ps=[(500, "/a/bloodlines-native")]), {1, 500}) == [])

        lp = os.path.join(tmp, "locks", ".game.lock")
        def got_lock(**kw):
            try:
                with contextlib.redirect_stdout(io.StringIO()), game_lock(lp, **{"wait": 0, **kw}):
                    return True
            except LockBusy:
                return False

        def plant(owner, pid, age, raw=None):
            os.mkdir(lp)
            open(os.path.join(lp, "owner"), "w").write(raw if raw is not None else f"{owner} {pid} {int(time.time() - age)}\n")

        def clear():
            shutil.rmtree(lp, ignore_errors=True)

        dead = subprocess.Popen(["true"])
        dead.wait()
        live = None
        try:
            with game_lock(lp, 0, owner="m7-menu"):
                own = open(os.path.join(lp, "owner")).read().split()
                expect("the game lock is a directory with an owner file of owner, pid and epoch",
                       os.path.isdir(lp) and len(own) == 3 and own[0] == "m7-menu" and own[1] == str(os.getpid()) and abs(int(own[2]) - time.time()) < 60)
                expect("a held lock blocks a second game run", not got_lock(wait=0.05, poll=0.01))
            expect("release removes the owner file and the directory", not os.path.exists(lp) and STATE["lock"] is None)
            expect("the game lock can be taken again after release", got_lock())
            slept = []
            t = iter([0.0, 1.0, 2.0, 5.0])
            with game_lock(lp, 0):
                msg = raises(LockBusy, game_lock(lp, 3.0, 1.0, sleep=slept.append, clock=lambda: next(t)).__enter__)
            expect("the lock sleeps one poll at a time until the deadline, then names the lock file", slept == [1.0, 1.0] and msg is not None and lp in msg)
            expect("the lock waits 2 s between tries and gives up after 30 minutes", LOCK_POLL == 2.0 and LOCK_WAIT == 1800.0 and LOCK_STALE_S == 1200.0)
            try:
                with game_lock(lp, 0):
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            expect("the lock is released after an exception", not os.path.exists(lp))

            before = signal.getsignal(signal.SIGTERM)
            sig_exit = None
            kid = subprocess.Popen(["sleep", "60"])
            try:
                with game_lock(lp, 0):
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(2.0)
            except SystemExit as e:
                sig_exit = e.code
            try:
                kid.wait(5)
            except subprocess.TimeoutExpired:
                kid.kill()
            expect("SIGTERM kills the child game before the lock is released, then releases and restores the handler",
                   sig_exit == 128 + signal.SIGTERM and kid.poll() is not None and not os.path.exists(lp) and signal.getsignal(signal.SIGTERM) == before)

            live = subprocess.Popen(["sleep", "60"])
            plant("m7-old", dead.pid, 1250)
            expect("a lock 1250 s old whose owner pid is dead is taken, and now names this process",
                   got_lock() and not os.path.exists(lp) and not glob.glob(lp + ".stale.*"))
            plant("m7-old", dead.pid, 1190)
            expect("a lock 1190 s old whose owner pid is dead is kept", not got_lock(wait=0.05, poll=0.01) and os.path.isdir(lp))
            clear()
            plant("m7-old", live.pid, 3000)
            expect("a lock 3000 s old whose owner pid is alive is kept", not got_lock(wait=0.05, poll=0.01) and os.path.isdir(lp))
            clear()
            plant("m7-old", 0, 0, raw=f"shell-writer {dead.pid} Tue Oct 27 17:05:00 CEST 2026\n")
            os.utime(os.path.join(lp, "owner"), (time.time() - 1300, time.time() - 1300))
            expect("an owner file with a date string in field 3 falls back to its mtime: old and dead is taken", got_lock())
            plant("m7-old", 0, 0, raw=f"shell-writer {dead.pid} Tue Oct 27 17:05:00 CEST 2026\n")
            expect("an owner file with a date string in field 3 and a fresh mtime is kept", not got_lock(wait=0.05, poll=0.01))
            clear()
            plant("m7-old", 0, 0, raw="garbage\n")
            expect("an unreadable owner file is kept while fresh", not got_lock(wait=0.05, poll=0.01))
            clear()
            expect("pid_alive: self and a sleeping child are alive, an exited child and None are not",
                   pid_alive(os.getpid()) and pid_alive(live.pid) and not pid_alive(dead.pid) and not pid_alive(None))

            real_rm = shutil.rmtree

            def slow_rm(started=None, pauses=(0.2,)):
                left = list(pauses)

                def rm(p, **k):
                    if started:
                        started.set()
                    time.sleep(left.pop(0) if left else 0.0)
                    real_rm(p, **k)
                return rm

            plant("m7-old", dead.pid, 1300)
            wins, lost = [], []
            barrier = threading.Barrier(2)

            def taker(name):
                barrier.wait()
                try:
                    with game_lock(lp, 0.05, 0.01, owner=name):
                        wins.append(name)
                        time.sleep(0.6)
                except LockBusy:
                    lost.append(name)

            with contextlib.redirect_stdout(io.StringIO()), patched(shutil, rmtree=slow_rm(None, (0.05, 0.4))):
                ts =[threading.Thread(target=taker, args=(n_,)) for n_ in ("t1", "t2")]
                for t_ in ts:
                    t_.start()
                for t_ in ts:
                    t_.join()
            expect("two threads that both judge a stale lock stale, with a slow steal, end with exactly one holder, one refusal and no stranded folder",
                   len(wins) == 1 and len(lost) == 1 and not os.path.exists(lp) and not glob.glob(lp + ".stale.*"))

            plant("m7-old", dead.pid, 1300)
            started, third_won, third_busy = threading.Event(), [], []

            def third():
                started.wait()
                try:
                    with game_lock(lp, 0.05, 0.01, owner="third"):
                        third_won.append(1)
                except LockBusy:
                    third_busy.append(1)

            th = threading.Thread(target=third)
            with contextlib.redirect_stdout(io.StringIO()), patched(shutil, rmtree=slow_rm(started, (0.3,))):
                th.start()
                with game_lock(lp, 0, owner="m7-first"):
                    th.join()
                    holder = (lambda p: open(p).read().split() if os.path.exists(p) else [])(os.path.join(lp, "owner"))
            expect("a third run that arrives mid-steal waits for the guard, then finds the new lock alive and is refused; the holder keeps its owner file",
                   third_busy == [1] and not third_won and holder[:2] == ["m7-first", str(os.getpid())] and not os.path.exists(lp) and
                   not glob.glob(lp + ".stale.*"))

            os.mkdir(lp)
            ino = os.stat(lp).st_ino
            expect("an owner-less folder with another inode is not ours and stays", not remove_if_ours(lp, ino + 1) and os.path.isdir(lp))
            expect("an owner-less folder we made (same inode) is removed", remove_if_ours(lp, ino) and not os.path.exists(lp))
            plant("m7-other", live.pid, 5)
            expect("a folder whose owner file names another pid stays whatever the inode", not remove_if_ours(lp, os.stat(lp).st_ino) and os.path.isdir(lp))
            clear()
            expect("the guard file sits next to the lock", os.path.exists(lp + ".guard"))

            os.mkdir(lp)
            open(os.path.join(lp, "owner"), "w").write(f"m7-x {os.getpid()} {int(time.time())}\n")
            open(os.path.join(lp, "owner.tmp"), "w").write("leftover")
            expect("a lock we own is removed with a leftover owner.tmp inside", remove_if_ours(lp, os.stat(lp).st_ino) and not os.path.exists(lp))

            open(lp, "w").write("not a directory")
            os.utime(lp, (time.time() - 1300, time.time() - 1300))
            rm_calls = []

            def counting_rm(p, **k):
                rm_calls.append(1)
                if len(rm_calls) > 50:
                    raise RuntimeError("spinning on an entry that cannot be removed")
                real_rm(p, **k)

            ticks = [0]

            def tick():
                ticks[0] += 1
                return ticks[0]

            t_start = time.monotonic()
            try:
                with contextlib.redirect_stdout(io.StringIO()), patched(shutil, rmtree=counting_rm), \
                        game_lock(lp, 5, 1, sleep=lambda s: None, clock=tick):
                    outcome = "acquired"
            except LockBusy:
                outcome = "busy"
            except RuntimeError as e:
                outcome = str(e)
            expect("a stale regular file at the lock path gives LockBusy within a bounded time and does not spin on the guard",
                   outcome == "busy" and len(rm_calls) == 5 and time.monotonic() - t_start < 5.0 and os.path.isfile(lp))
            os.unlink(lp)
            expect("the guard is free afterwards", got_lock())

            with game_lock(lp, 0, owner="m7-mine"):
                open(os.path.join(lp, "owner"), "w").write(f"m7-other {live.pid} {int(time.time())}\n")
                expect("a run that lost its lock cannot refresh the owner file", raises(LockBusy, refresh_owner) is not None)
            expect("release does not remove a lock someone else took over", os.path.isdir(lp) and open(os.path.join(lp, "owner")).read().startswith("m7-other"))
            clear()

            calls = []
            with game_lock(lp, 0, owner="m7-run"):
                open(os.path.join(lp, "owner"), "w").write(f"m7-run {os.getpid()} {int(time.time() - 900)}\n")
                out = with_owner(lambda *a_, **k_: calls.append(1) or 7)()
                ep = int(open(os.path.join(lp, "owner")).read().split()[2])
            expect("the owner file is rewritten before each game run", out == 7 and calls == [1] and abs(ep - time.time()) < 60)
            refresh_owner()
        finally:
            if live:
                live.kill()
                live.wait()
            clear()

        expect("ffmpeg argv: -ar 44100 -ac 2 for stereo references, none for native", "-ar" in ffmpeg_argv("a", "b") and "-ar" not in ffmpeg_argv("a", "b", True) and
               ffmpeg_argv("a", "b")[0] == "ffmpeg" and "-vn" in ffmpeg_argv("a", "b") and ffmpeg_argv("a", "b")[-1] == "b")

        def fake_ffmpeg(cmd, **kw):
            if "fail" in cmd[cmd.index("-i") + 1]:
                return SimpleNamespace(returncode=1, stderr="bad")
            open(cmd[-1], "wb").write(b"x")
            return SimpleNamespace(returncode=0, stderr="")

        expect("make_ref runs ffmpeg and returns the output path", make_ref("a.mp3", os.path.join(tmp, "ref", "a.wav"), run=fake_ffmpeg) == os.path.join(tmp, "ref", "a.wav"))
        expect("make_ref refuses an output path equal to the input", raises(ValueError, make_ref, os.path.join(tmp, "s.wav"), os.path.join(tmp, "s.wav"), False, fake_ffmpeg) is not None)
        same_dir = os.path.join(tmp, "same")
        os.makedirs(same_dir, exist_ok=True)
        s_wav = os.path.join(same_dir, "Foo.wav")
        open(s_wav, "wb").write(b"x")
        os.symlink(s_wav, os.path.join(same_dir, "link.wav"))
        os.link(s_wav, os.path.join(same_dir, "hard.wav"))
        variant = os.path.join(same_dir, "foo.WAV")
        case_insensitive = os.path.exists(variant)
        expect("make_ref refuses a symlink to the source as the output",
               raises(ValueError, make_ref, s_wav, os.path.join(same_dir, "link.wav"), False, fake_ffmpeg) is not None)
        expect("make_ref refuses a hardlink to the source as the output",
               raises(ValueError, make_ref, s_wav, os.path.join(same_dir, "hard.wav"), False, fake_ffmpeg) is not None)
        expect("make_ref refuses a case variant of the source as the output on a case-insensitive volume",
               not case_insensitive or raises(ValueError, make_ref, s_wav, variant, False, fake_ffmpeg) is not None)
        seen_ref = []

        def vpk_like(game, rel, out_dir):
            os.makedirs(out_dir, exist_ok=True)
            p_ = os.path.join(out_dir, os.path.basename(rel))
            open(p_, "wb").write(b"x")
            return p_

        def spy_ref(src_, dst_, native_=False, run_=None):
            seen_ref.append((src_, dst_))
            return dst_

        with patched(me, find_game_file=vpk_like, make_ref=spy_ref, read_wav=lambda p_: p_):
            got_ref = reference(Rep(), SimpleNamespace(game=tmp), tmp, "sound/surfaces/scrape1.wav")
        expect("reference decodes a VPK wav such as scrape1.wav to a path different from the extracted input",
               len(seen_ref) == 1 and seen_ref[0][0] != seen_ref[0][1] and os.path.basename(seen_ref[0][0]) == "scrape1.wav" and got_ref == seen_ref[0][1])
        expect("make_ref raises on an ffmpeg failure", raises(ValueError, make_ref, "fail.mp3", os.path.join(tmp, "ref", "f.wav"), False, fake_ffmpeg) is not None)

        game = os.path.join(tmp, "game")
        os.makedirs(os.path.join(game, "Vampire", "sound", "Music"))
        open(os.path.join(game, "Vampire", "sound", "Music", "Short.MP3"), "wb").write(b"x" * 10)
        open(os.path.join(game, "Vampire", "sound", "Music", "Long.mp3"), "wb").write(b"x" * 90)
        open(os.path.join(game, "Vampire", "sound", "Music", "note.txt"), "wb").write(b"x" * 999)
        os.makedirs(os.path.join(game, "Vampire", "media"))
        open(os.path.join(game, "Vampire", "media", "troika.bik"), "wb").write(b"b")
        open(os.path.join(game, "Vampire", "pack000.vpk"), "wb").write(vpk_bytes({"sound/surfaces/scrape1.wav": b"RIFFdata", "sound/misc/x.wav": b"y"}))
        STATE["entries"] = STATE["known"] = None
        expect("resolve_ci finds a path whatever its case and returns None for a missing one",
               resolve_ci(os.path.join(game, "Vampire"), "SOUND/music/short.mp3") == os.path.join(game, "Vampire", "sound", "Music", "Short.MP3") and
               resolve_ci(os.path.join(game, "Vampire"), "sound/nope") is None)
        got = find_game_file(game, "sound/surfaces/scrape1.wav", os.path.join(tmp, "out"))
        expect("a sound only in a VPK is extracted for ffmpeg", got and open(got, "rb").read() == b"RIFFdata")
        expect("a loose sound is found before the VPK", find_game_file(game, "sound/music/short.mp3", os.path.join(tmp, "out")) ==
               os.path.join(game, "Vampire", "sound", "Music", "Short.MP3"))
        expect("a missing sound gives None", find_game_file(game, "sound/none.wav", os.path.join(tmp, "out")) is None)
        expect("find_movie finds a .bik by name in media", find_movie(game, "troika") == os.path.join(game, "Vampire", "media", "troika.bik") and find_movie(game, "none") is None)
        ks = known_sounds(game)
        expect("known_sounds holds VPK and loose sounds without the sound/ prefix", {"surfaces/scrape1.wav", "misc/x.wav", "music/short.mp3", "music/long.mp3"} <= ks)
        expect("norm_sound folds case, slashes and the sound prefix", norm_sound("'Sound\\Surfaces\\Scrape1.WAV'") == "surfaces/scrape1.wav")
        STATE["entries"] = STATE["known"] = None

        res = lines_res([(1.0, "rt: audio open device x"), (5.0, 'rt: script 5 text "play a"'), (6.0, "rt: script 6 click left")])
        expect("t_open, script_times and line_times read the timed lines", t_open(res) == 1.0 and script_times(res, 'text "play ') == [5.0] and
               script_times(res, "click") == [6.0] and line_times(res, r"rt: audio open") == [1.0] and t_open(lines_res([])) is None)
        expect("audio_pos maps wall time to audio time through the audio open line and never goes below 0",
               audio_pos(res, 5.0, 0.5) == 4.5 and audio_pos(res, 5.0, -9.0) == 0.0 and audio_pos(lines_res([]), 5.0, 1.0) == 1.0)
        expect("cap_window widens the span by the 5 s lag limit on each side", cap_window(res, 5.0, 0.0, 2.0) == (0.0, 11.0) and cap_window(res, 15.0, 0.0, 2.0) == (9.0, 21.0))

        expect("the module has no comments", not [t for t in tokenize.generate_tokens(open(__file__).readline)
                                                  if t.type == tokenize.COMMENT and t.start[0] > 1])
        pr = build_parser()
        for argv in (["menu"], ["menu", "--clock", "--n", "1"], ["quit"], ["nosound"], ["map"], ["look"], ["music"], ["chargen"], ["dialogue", "--voice", "x"],
                     ["opening", "--from", "sp_theatre"], ["intro"], ["skip"], ["novid"], ["missing"], ["toggle", "--novid"], ["perf", "--n", "1"], ["cases"], ["selftest"]):
            pa = pr.parse_args(argv)
            expect(f"parser accepts '{' '.join(argv)}' and marks live modes", pa.live == (argv[0] != "selftest") and callable(pa.fn))
        expect("every mode of the design's table has a sub-command",
               set(LIVE) | {"selftest"} == {"selftest", "cases", "menu", "quit", "nosound", "map", "look", "music", "chargen", "dialogue", "opening", "intro", "skip",
                                            "novid", "missing", "toggle", "perf"})
        expect("ns merges m6's defaults under the m7 options", ns(pr.parse_args(["map", "--map", "x"])).map == "x" and ns(pr.parse_args(["map"])).p99 == DEF["p99"])

        def drive_env(argv):
            a = ns(pr.parse_args([*argv, "--exe", sys.executable, "--overlay", tmp]))
            return a

        def script_of(extra):
            return parse_script(open(extra[extra.index("--input-script") + 1]).read())

        def timeline(lines, scripts, t_script=5.0, t_exit=30.0):
            out = [(0.01 * i, l) for i, l in enumerate(lines)]
            out += [(t_script + i, f"rt: script {c[0]} {c[1]} {c[2]}".rstrip()) for i, c in enumerate(scripts)]
            out.append((t_script + len(scripts), "rt: script done"))
            return sorted(out, key=lambda x: x[0]), t_exit

        base_lines = m4.fixture_log(order=[1, 2, 3, 4, 4, 5, 6, 9, 7, 8, 10, 11]).splitlines()
        saved_start, saved_gate, saved_screen, saved_read = m6.start, me.gate7, m5.screen, me.readings
        seen = []
        ref_sig = synth(2.0, 31)
        ref_wav = Wav(RATE, 1, ref_sig)
        try:
            m6.start = lambda a, gated=False: 0
            me.gate7 = lambda *a_, **k_: []
            me.readings = lambda *a_, **k_: SimpleNamespace(load=1.0, idle=80.0, gpu=12)
            quiet_ref = lambda r_, A_, o_, rel, native=False, movie=None: ref_wav

            mp3_lines = ["rt: mp3 open #1 rate 44100 ch 2", "rt: mp3 decoders opened 1 closed 1 failed 0 frames 900 errors 0"]

            def menu_runner(cap_fn, mp3=True):
                def run(exe, overlay, extra, timeout):
                    seen.append((list(extra), os.environ.get("SDL_AUDIO_DRIVER")))
                    os.makedirs(os.path.join(overlay, "audio"), exist_ok=True)
                    lines, ex = timeline(base_lines + alog().splitlines()[1:2], script_of(extra))
                    lines = sorted(lines + [(ex - 1, alog().splitlines()[-1])], key=lambda x: x[0])
                    if mp3:
                        lines = sorted(lines + [(ex - 3, mp3_lines[0]), (ex - 1.5, mp3_lines[1])], key=lambda x: x[0])
                    write_wav(os.path.join(overlay, "audio", "waveout-1.wav"), cap_fn(2.0))
                    return lines_res(lines, exit_t=ex)
                return run

            def good_cap(off):
                return stereo(silence(off) + ref_sig + silence(1.0), silence(off) + ref_sig + silence(1.0))

            with patched(me, reference=quiet_ref), patched(m6, RUNNER=menu_runner(good_cap)):
                rc, out = quiet(cmd_menu, drive_env(["menu"]))
            expect("menu drive: a capture holding the menu music passes A2, the music match and the mp3 lines and the run gets the dummy driver and --audio-capture",
                   rc == 0 and seen and "--audio-capture" in seen[-1][0] and seen[-1][1] == "dummy" and "--nosound" not in seen[-1][0] and "--close-after" in seen[-1][0])
            with patched(me, reference=quiet_ref), patched(m6, RUNNER=menu_runner(lambda off: stereo(silence(off + 3.0), silence(off + 3.0)))):
                rc, out = quiet(cmd_menu, drive_env(["menu"]))
            expect("mutation: a shim that queues zeros fails the menu music match", rc == 1 and "FAIL menu music" in out)
            half = array.array("h", (v // 2 for v in ref_sig))
            with patched(me, reference=quiet_ref), patched(m6, RUNNER=menu_runner(lambda off: stereo(silence(off) + ref_sig, silence(off) + half))):
                rc, out = quiet(cmd_menu, drive_env(["menu"]))
            expect("menu drive: a sound panned to the left (right at half level) still passes the music match", rc == 0)
            with patched(me, reference=quiet_ref), patched(m6, RUNNER=menu_runner(good_cap, False)):
                rc, out = quiet(cmd_menu, drive_env(["menu"]))
            expect("menu drive: a run without the mp3 open line fails even when the capture matches", rc == 1 and "FAIL rt: mp3 open" in out)

            def clock_runner(written):
                def run(exe, overlay, extra, timeout):
                    seen.append((list(extra), os.environ.get("SDL_AUDIO_DRIVER")))
                    lines = [(0.01 * i, l) for i, l in enumerate(base_lines)] + [(1.0, alog().splitlines()[1]), (101.0, alog(written=written, played=100 * BYTES_S).splitlines()[-1])]
                    return lines_res(sorted(lines, key=lambda x: x[0]), exit_t=101.0)
                return run

            with patched(m6, RUNNER=clock_runner(100 * BYTES_S)):
                rc, out = quiet(cmd_menu, drive_env(["menu", "--clock", "--n", "1"]))
            expect("menu --clock drive: a run on the clock passes, gated, on the real device (no SDL_AUDIO_DRIVER) and without a capture",
                   rc == 0 and seen[-1][1] is None and "--audio-capture" not in seen[-1][0])
            with patched(m6, RUNNER=clock_runner(110 * BYTES_S)):
                rc, out = quiet(cmd_menu, drive_env(["menu", "--clock", "--n", "1"]))
            expect("menu --clock drive: written above the bound fails", rc == 1)
            with patched(m6, RUNNER=clock_runner(100 * BYTES_S)):
                rc, out = quiet(cmd_menu, drive_env(["menu", "--driver", "nosuchdriver"]))
            expect("menu --driver drive: the bogus driver reaches the child and the missing silent clock line fails A8", rc == 1 and seen[-1][1] == "nosuchdriver")

            PLAYER_ROWS = m5.dinput_rows() + [["1", "binkw32.dll!_BinkOpen@8", "shim", "engine", "f", "4"]] + \
                [["1", f"winmm.dll!{n_}", "shim", "engine", "f", "1"] for n_ in WAVE_ROWS]

            def player_lines(extra_lines, args="-window -w 1280 -h 1024 -dxlevel 90"):
                return [l for l in base_lines if not l.startswith("rt: stage 1 ")] + alog(args=args).splitlines()[:2] + ["rt: player mode"] + list(extra_lines)

            def intro_runner(log_extra, with_caps=True):
                def run(exe, overlay, extra, timeout):
                    seen.append((list(extra), os.environ.get("SDL_AUDIO_DRIVER")))
                    os.makedirs(os.path.join(overlay, "audio"), exist_ok=True)
                    if with_caps:
                        for k in INTROS:
                            write_wav(os.path.join(overlay, "audio", f"bink-{k}-1.wav"), Wav(8000, 1, synth(0.3, 1, 8000)))
                    ex_line = alog().splitlines()[-1]
                    lines = [(0.01 * i, l) for i, l in enumerate(player_lines(blog().splitlines() + [ex_line] + log_extra))]
                    sc = script_of(extra)
                    tl, ex = timeline([], sc)
                    return lines_res(sorted(lines + tl, key=lambda x: x[0]), exit_t=ex, rows=PLAYER_ROWS)
                return run

            shots = []
            def fake_screen(r_, a_, o_, shot, ref=None):
                shots.append((shot, ref))
                return pic(1280, 1024, INTRO_BARS.get(int(shot[5]), (1280, 1024))) if shot.startswith("intro") else m4.picture()

            m5.screen = fake_screen
            calls = []
            with patched(me, reference=quiet_ref, check_bink_audio=lambda r_, log, caps, refs: calls.append(sorted(caps))), patched(m6, RUNNER=intro_runner([])):
                rc, out = quiet(cmd_intro, drive_env(["intro"]))
            expect("intro drive: four good movies pass, run with --player and --audio-capture, compare intro1 to intro4 with their references",
                   rc == 0 and "--player" in seen[-1][0] and "--audio-capture" in seen[-1][0] and shots[:4] == [(f"intro{i}", f"m7-intro{i}") for i in range(1, 5)] and
                   calls == [sorted(INTROS)])
            with patched(me, reference=quiet_ref, check_bink_audio=lambda *a_: None), patched(m6, RUNNER=intro_runner([])):
                rc, out = quiet(cmd_intro, drive_env(["intro"]))
            expect("intro drive: the player log passes the run rules with no -dev (C10 args, C8 not applied)", rc == 0 and "no rt: bink open line" not in out)

            def devarg_runner(exe, overlay, extra, timeout):
                res_ = intro_runner([])(exe, overlay, extra, timeout)
                return SimpleNamespace(**{**vars(res_), "log": res_.log.replace("-dxlevel 90", "-dxlevel 90 -dev")})

            with patched(me, reference=quiet_ref, check_bink_audio=lambda *a_: None), patched(m6, RUNNER=devarg_runner):
                rc, out = quiet(cmd_intro, drive_env(["intro"]))
            expect("mutation: -dev left in the player list fails C10's argument check", rc == 1 and "FAIL guest command line has none of" in out)

            def skip_runner(exe, overlay, extra, timeout):
                seen.append((list(extra), None))
                tl, ex = timeline([], script_of(extra))
                ts = [t for t, l in tl if l.rstrip().endswith(("key space", "click left"))]
                lines = [(0.01 * i, l) for i, l in enumerate(player_lines(sk.splitlines() + [alog().splitlines()[-1]]))]
                opens = [(0.5, "rt: bink open"), (ts[0] + 0.4, "rt: bink open"), (ts[1] + 0.5, "rt: bink open"), (ts[1] + 15.0, "rt: bink open")]
                lines = [x for x in lines if not x[1].startswith("rt: bink open")] + [(t, l + f" x{i}") for i, (t, l) in enumerate(opens)]
                return lines_res(sorted(lines + tl, key=lambda x: x[0]), exit_t=ex, rows=PLAYER_ROWS)

            with patched(m6, RUNNER=skip_runner):
                rc, out = quiet(cmd_skip, drive_env(["skip"]))
            expect("skip drive: the skip script runs under --player and the skips and next opens are timed", rc == 0 and "--player" in seen[-1][0])

            def novid_runner(args="-window -w 1280 -h 1024 -dxlevel 90"):
                def run(exe, overlay, extra, timeout):
                    seen.append((list(extra), None))
                    skipped = [f"rt: bink skipped Vampire/media/{SHOWN[k]}.bik (--novid)" for k in INTROS]
                    lines = [(0.01 * i, l) for i, l in enumerate(player_lines(skipped + [alog().splitlines()[-1]], args))]
                    tl, ex = timeline([], script_of(extra))
                    return lines_res(sorted(lines + tl, key=lambda x: x[0]), exit_t=ex)
                return run

            shots.clear()
            with patched(m6, RUNNER=novid_runner()):
                rc, out = quiet(cmd_novid, drive_env(["novid"]))
            expect("novid drive: --novid is passed with --player and the menu-player capture is compared with m7-menu-player",
                   rc == 0 and "--novid" in seen[-1][0] and "--player" in seen[-1][0] and ("menu-player", "m7-menu-player") in shots)
            with patched(m6, RUNNER=novid_runner("-window -dev")):
                rc, out = quiet(cmd_novid, drive_env(["novid"]))
            expect("novid drive: -dev in the guest list fails", rc == 1)

            def hang_runner(hangs):
                inner, count = novid_runner(), []

                def run(exe, overlay, extra, timeout):
                    count.append(1)
                    res_ = inner(exe, overlay, extra, timeout)
                    if len(count) <= hangs:
                        return SimpleNamespace(**{**vars(res_), "log": res_.log + "rt: stop 15 gfx hang: present\n", "rc": 15})
                    return res_
                return run, count

            gate_calls = []

            def counting_gate(*a_, **k_):
                gate_calls.append(k_.get("perf"))
                return []

            run_h, cnt = hang_runner(2)
            with patched(m6, RUNNER=run_h), patched(me, gate7=counting_gate):
                rc, out = quiet(cmd_novid, drive_env(["novid"]))
            expect("a gfx hang stop (exit 15) is rerun, the third attempt passes, and every attempt records load, CPU idle and GPU",
                   rc == 0 and len(cnt) == 3 and all(f"-- attempt {i} of 3: load average 1.00, CPU idle 80.0 %, GPU 12 %" in out for i in (1, 2, 3)) and
                   out.count("checking the machine before running again") == 2)
            expect("gate7 is called before each rerun (once at the start and once before each of the 2 reruns)", len(gate_calls) == 3)
            gate_calls.clear()
            run_h, cnt = hang_runner(9)
            with patched(m6, RUNNER=run_h), patched(me, gate7=lambda *a_, **k_: [] if not gate_calls.append(1) and len(gate_calls) == 1 else ["GPU utilisation 90 % is above 60 %"]), \
                    patched(m6, QUIET_WAIT=0.0, QUIET_POLL=0.0):
                rc, out = quiet(cmd_novid, drive_env(["novid"]))
            expect("a busy machine before a rerun stops the reruns and says why", rc == 1 and len(cnt) == 1 and "not running again: GPU utilisation 90 %" in out)
            gate_calls.clear()
            with patched(me, gate7=lambda *a_, **k_: ["GPU utilisation 90 % is above 60 %"] if len(gate_calls) < 2 and not gate_calls.append(1) else []), \
                    patched(m6, QUIET_WAIT=5.0, QUIET_POLL=0.0):
                rc7, out7 = quiet(start7, drive_env(["novid"]))
            expect("start7 waits for a flickering gate to open instead of refusing at once", rc7 == 0 and "REFUSED" not in out7 and "waiting for a quiet machine" in out7)
            with patched(me, gate7=lambda *a_, **k_: ["GPU utilisation 90 % is above 60 %"]), patched(m6, QUIET_WAIT=0.0, QUIET_POLL=0.0):
                rc7, out7 = quiet(start7, drive_env(["novid"]))
            expect("start7 still refuses when the gate never opens within the wait", rc7 == 1 and "REFUSED: GPU utilisation 90 %" in out7)
            dx_runner = lambda *a_:SimpleNamespace(**{**vars(novid_runner()(*a_)), "log": novid_runner()(*a_).log + "GPU likely hung\n"})
            cnt2 = []
            with patched(m6, RUNNER=lambda *a_: cnt2.append(1) or dx_runner(*a_)):
                rc, out = quiet(cmd_novid, drive_env(["novid"]))
            expect("a dxvk device-lost line alone does not trigger a rerun, only the gfx hang: stop does", rc == 1 and len(cnt2) == 1 and "attempt 2" not in out)
            expect("HANG_TRIES counts attempts in total: 3", HANG_TRIES == 3)
            run_h, cnt = hang_runner(9)
            with patched(m6, RUNNER=run_h):
                rc, out = quiet(cmd_novid, drive_env(["novid"]))
            expect("a hang on every attempt stops after 3 tries and fails", rc == 1 and len(cnt) == 3 and "attempt 4" not in out)
            expect("each functional hang retry is logged and the retry count is bounded at 2",
                   HANG_RETRIES == 2 and "retry 1 of 2" in out and "retry 2 of 2" in out and "retry 3" not in out and out.count("gfx hang stop (exit 15): retry") == 2)
            run_h, cnt = hang_runner(0)
            with patched(m6, RUNNER=run_h):
                rc, out = quiet(cmd_novid, drive_env(["novid"]))
            expect("a clean run is not rerun", rc == 0 and len(cnt) == 1)

            def toggle_runner(second="0", extra_text=()):
                def run(exe, overlay, extra, timeout):
                    seen.append((list(extra), None))
                    sc = script_of(extra)
                    tl, ex = timeline([], sc)
                    chords = [t for t, l in tl if l.rstrip().endswith("chord 8")]
                    lines = [(0.01 * i, l) for i, l in enumerate(player_lines([alog().splitlines()[-1]]))]
                    lines += [(chords[0] + 0.1, 'rt: dev console "developer 1" (chord)'), (chords[1] + 0.1, f'rt: dev console "developer {second}" (chord)')]
                    return lines_res(sorted(lines + tl, key=lambda x: x[0]), exit_t=ex)
                return run

            def toggle_screen(dots_after_off=False):
                def scr(r_, a_, o_, shot, ref=None):
                    img = {"menu-player-a": base_img, "dev-on": moved, "dev-off": moved if dots_after_off else base_img}[shot]
                    return img
                return scr

            m5.screen = toggle_screen()
            td = drive_env(["toggle", "--novid"])
            td.refdir = tmp
            with patched(m6, RUNNER=toggle_runner()):
                rc, out = quiet(cmd_toggle, td)
            expect("toggle drive: chords, console lines and pictures agree, run with --player", rc == 0 and "--player" in seen[-1][0])
            with patched(m6, RUNNER=toggle_runner("1")):
                rc, out = quiet(cmd_toggle, td)
            expect("toggle drive: developer 1 twice fails", rc == 1)
            m5.screen = toggle_screen(True)
            with patched(m6, RUNNER=toggle_runner()):
                rc, out = quiet(cmd_toggle, td)
            expect("toggle drive: dots left after the second chord fail", rc == 1)

            def perf_runner(delta):
                def run(exe, overlay, extra, timeout):
                    sound = "--nosound" not in extra
                    seen.append((list(extra), None))
                    p99 = 20.0 + (delta if sound else 0.0)
                    fl = m4.frames_line([16.6] * 97 + [p99] * 3).replace("n 100", "n 7200")
                    lines = [(0.01 * i, l) for i, l in enumerate(base_lines)] + [(1.0, alog(off=not sound).splitlines()[1])]
                    lines = [x for x in lines if not x[1].startswith("rt: stage 1 ")] + [(0.0, alog(args="-window" + ("" if sound else " -nosound")).splitlines()[0])]
                    lines += [(2.0, fl), (3.0, "rt: max pump gap steady 30 ms")]
                    if sound:
                        lines.append((4.0, alog().splitlines()[-1]))
                    else:
                        lines.append((4.0, "rt: audio off (--nosound)"))
                    if "--input-script" in extra:
                        sc = script_of(extra)
                        tl, ex = timeline([], sc)
                        lines += tl
                    return lines_res(sorted(lines, key=lambda x: x[0]), exit_t=50.0, rows=m5.dinput_rows() + [["1", f"winmm.dll!{n_}", "shim", "e", "f", "1"] for n_ in WAVE_ROWS])
                return run

            pa = drive_env(["perf", "--n", "1"])
            pa.close_after = 10
            with patched(m6, RUNNER=perf_runner(0.2)), patched(m6, check_stage12=lambda *a_, **k_: None, map_stages=lambda *a_, **k_: None):
                rc, out = quiet(cmd_perf, pa)
            expect("perf drive: four interleaved runs, sound on and --nosound, within the deltas pass and the setup is printed",
                   rc == 0 and "-- setup:" in out and [("--nosound" in e) for e, _ in seen[-4:]] == [False, True, False, True])
            with patched(m6, RUNNER=perf_runner(3.0)), patched(m6, check_stage12=lambda *a_, **k_: None, map_stages=lambda *a_, **k_: None):
                rc, out = quiet(cmd_perf, pa)
            expect("perf drive: a p99 3 ms above --nosound fails D1", rc == 1)

            cdir = os.path.join(tmp, "cases dir")
            os.makedirs(cdir, exist_ok=True)
            mp3s = [("sound/voice/a.mp3", "dec a.wav"), ("sound/music/b.mp3", "dec b.wav"), ("sound/voice/tagged.mp3", "dec c.wav"), ("sound/voice/d.mp3", "dec d.wav")]
            biks = [(f"media/{k}.bik", f"{k}.wav") for k in INTROS]
            for _, wn in mp3s + biks:
                write_wav(os.path.join(cdir, wn), Wav(RATE, 1, ref_sig))
            dec = lambda items: "".join(f"decode {p} {w}\n" for p, w in items)
            passes = "".join(f"PASS case {i}\n" for i in range(12))
            cases_out = passes + dec(mp3s + biks)
            failc = os.path.join(tmp, "fail.c")
            open(failc, "w").write('{"kernel32.dll", "CreatePipe"},\n')
            imports_ok = f"binkw32.dll!{BINK_KEEP} 0x1 stub\nWINMM.dll!waveOutWrite 0x2 shim\n"

            def cases_args(*more):
                return ns(pr.parse_args(["cases", "--exe", sys.executable, "--overlay", cdir, "--game", game, "--fail-c", failc, *more]))

            def cases_run(text, rc_=0, imports=imports_ok):
                return lambda cmd, **kw: SimpleNamespace(stdout=imports if "--list-imports" in cmd else text, stderr="", returncode=0 if "--list-imports" in cmd else rc_)

            def run_cases(text, *more, **kw):
                with patched(subprocess, run=cases_run(text, **kw)), patched(me, reference=quiet_ref):
                    return quiet(cmd_cases, cases_args(*more))

            rc, out = run_cases(cases_out)
            expect("cases drive: 12 PASS lines, 4 .mp3 and 4 .bik decodes (one WAV path with a space) equal to the reference, a clean stub list and fail.c pass", rc == 0)
            rc, out = run_cases(cases_out + "FAIL case 13\n")
            expect("cases drive: one FAIL line fails", rc == 1)
            rc, out = run_cases(cases_out, rc_=1)
            expect("cases drive: exit 1 fails", rc == 1)
            rc, out = run_cases(cases_out.replace("PASS case 11\n", ""))
            expect("cases drive: too few PASS lines fail", rc == 1)
            rc, out = run_cases(passes + dec(mp3s[:2] + biks))
            expect("cases drive: 2 .mp3 decode lines with B merged fail, the false PASS of an empty decode list", rc == 1 and "at least 3 .mp3 decode lines" in out)
            rc, out = run_cases(passes + dec(mp3s[:3] + biks))
            expect("cases drive: 3 .mp3 decodes of which one is music fail, music is not counted", rc == 1 and "at least 3 .mp3 decode lines" in out)
            rc, out = run_cases(passes + dec(mp3s[:2] + biks), "--merged", "A,C")
            expect("cases drive: the .mp3 count is not required while B is not merged", rc == 0)
            rc, out = run_cases(passes + dec(mp3s + biks[:3]))
            expect("cases drive: 3 .bik decode lines with C merged fail", rc == 1 and "4 .bik decode lines" in out)
            rc, out = run_cases(passes + dec(mp3s), "--merged", "A,B")
            expect("cases drive: no .bik lines are required while C is not merged", rc == 0)
            rc, out = run_cases(passes)
            expect("cases drive: no decode lines at all fails", rc == 1)
            rc, out = run_cases(cases_out, imports=imports_ok + "binkw32.dll!_BinkClose@4 0x3 stub\n")
            expect("cases drive: a binkw32 stub other than _BinkOpenDirectSound@4 fails with C merged", rc == 1 and "binkw32 keeps only" in out)
            rc, out = run_cases(cases_out, "--merged", "A,B", imports=imports_ok + "binkw32.dll!_BinkClose@4 0x3 stub\n")
            expect("cases drive: the same stub is ignored while C is not merged", rc == 0)
            write_wav(os.path.join(cdir, "dec a.wav"), Wav(RATE, 1, ref_sig[:len(ref_sig) - 2000]))
            rc, out = run_cases(cases_out)
            expect("cases drive: a decode 2000 frames shorter than ffmpeg's fails B1", rc == 1)
            write_wav(os.path.join(cdir, "dec a.wav"), Wav(RATE, 1, ref_sig[:len(ref_sig) - 1000]))
            rc, out = run_cases(cases_out)
            expect("cases drive: a decode 1000 frames shorter is inside the 1152 bound and still matches", rc == 0)
            write_wav(os.path.join(cdir, "dec a.wav"), Wav(RATE, 1, ref_sig))
            open(failc, "w").write('{"binkw32.dll", "_BinkOpen@8", 2, BinkOpen, RT_SHIM_FAIL},\n')
            rc, out = run_cases(cases_out)
            expect("cases drive: a _BinkOpen@8 row left in shims/fail.c fails with C merged", rc == 1 and "FAIL shims/fail.c" in out)
            rc, out = run_cases(cases_out, "--merged", "A,B")
            expect("cases drive: fail.c is not checked while C is not merged", rc == 0)
            expect("RE_DECODE takes a WAV path with spaces after a game path relative to Vampire/",
                   RE_DECODE.findall("decode sound/music/a.mp3 /tmp/x y/z w.wav\n") == [("sound/music/a.mp3", "/tmp/x y/z w.wav")])
            expect("RE_DECODE takes a game path with spaces, such as an answering machine file",
                   RE_DECODE.findall("decode sound/character/conversations/answering machine/muddy.mp3 /tmp/o/audio/mp3-3.wav\n") ==
                   [("sound/character/conversations/answering machine/muddy.mp3", "/tmp/o/audio/mp3-3.wav")])
            expect("RT_GATE_IDLE clamps to 5..35 and a non-number, nan or inf gives the default 5",
                   [env_num("RT_GATE_IDLE", 5.0, 5.0, 35.0, {"RT_GATE_IDLE": v}) for v in ("1", "5", "20", "99", "-3", "abc", "", "nan", "inf")] ==
                   [5.0, 5.0, 20.0, 35.0, 5.0, 5.0, 5.0, 5.0, 5.0])
            expect("RT_GATE_GPU clamps to 60..95 and a non-number gives the default 95",
                   [env_num("RT_GATE_GPU", 95, 60, 95, {"RT_GATE_GPU": v}) for v in ("10", "65", "95", "200", "x1", "nan")] == [60, 65, 95, 95, 95, 95])
            with patched(me, IDLE_MIN=15.0, GPU_FUNC=80, readings=saved_read):
                expect("a stricter env gates functional runs at 15 % idle and 80 % GPU",
                       saved_gate(1.0, canned(top="CPU usage: 1% user, 1% sys, 16.0% idle", gpu='"Device Utilization %"=80'), {1}) == [] and
                       len(saved_gate(1.0, canned(top="CPU usage: 1% user, 1% sys, 14.0% idle"), {1})) == 1)
                expect("perf runs ignore the env: 35 % idle and 30 % GPU stay",
                       len(saved_gate(1.0, canned(top="CPU usage: 1% user, 1% sys, 34.9% idle"), {1}, perf=True)) == 1 and
                       len(saved_gate(1.0, canned(gpu='"Device Utilization %"=31'), {1}, perf=True)) == 1 and
                       saved_gate(1.0, canned(top="CPU usage: 1% user, 1% sys, 35.0% idle", gpu='"Device Utilization %"=30'), {1}, perf=True) == [])
                expect("the gate text prints the values in use", gate_text() == "gate: CPU idle >= 15 %, GPU <= 80 %" and
                       gate_text(True) == "gate: CPU idle >= 35 %, GPU <= 30 %")
        finally:
            m6.start, me.gate7, m5.screen, me.readings = saved_start, saved_gate, saved_screen, saved_read
            CFG.update(flags=(), player=False, nosound=False, silent_ok=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"selftest: {n['ok'] - len(bad)} of {n['ok']} passed")
    return 0 if not bad else 1


def main():
    a = build_parser().parse_args()
    try:
        if not a.live:
            return a.fn(a)
        with game_lock(a.lock, a.lock_wait, owner=f"m7-{a.mode}"):
            return a.fn(ns(a))
    except LockBusy as e:
        print(f"REFUSED: {e}")
        return 1
    except (OSError, ValueError, PngError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
