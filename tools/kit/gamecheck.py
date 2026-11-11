import hashlib
import json
import os
import time
from pathlib import Path

from kitconst import REPO

BUNDLED = Path(__file__).resolve().parent / "game-manifest.txt"
MANIFEST = BUNDLED if BUNDLED.is_file() else REPO / "docs" / "game-manifest.txt"
SKIP = {".ds_store"}
EXIT_FAIL = 6
UP_REPLACED = {
    "vampire.exe": "0625c6b5986d76a8b8f4f2b0447a08c1d1faba7655d417b07d8fa1fdcc076a76",
    "bin/binkw32.dll": "6e40138bc8706fa6b9bb5afebe1c3cadba9dcd8784274acc5eb4d02baca319b4",
}


def load_manifest(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        if name.rsplit("/", 1)[-1].lower() in SKIP:
            continue
        rows.append((digest.lower(), name))
    return rows


class Folder:
    def __init__(self, root):
        self.root = Path(root)
        self.cache = {}

    def names(self, d):
        if d not in self.cache:
            try:
                self.cache[d] = {n.lower(): n for n in sorted(os.listdir(d), reverse=True)}
            except OSError:
                self.cache[d] = {}
        return self.cache[d]

    def find(self, rel):
        cur = self.root
        for part in rel.split("/"):
            real = self.names(cur).get(part.lower())
            if real is None:
                return None
            cur = cur / real
        return cur if cur.is_file() else None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_game(game, manifest=MANIFEST, progress=None):
    t0 = time.monotonic()
    folder = Folder(game)
    rows = load_manifest(manifest)
    bad = []
    patched = []
    for i, (digest, name) in enumerate(rows):
        if progress:
            progress(i, len(rows))
        p = folder.find(name)
        if p is None:
            bad.append(("missing", name))
        else:
            try:
                got = sha256(p)
                if got == digest:
                    continue
                if UP_REPLACED.get(name.lower()) == got:
                    patched.append(name)
                else:
                    bad.append(("changed", name))
            except OSError:
                bad.append(("unreadable", name))
    if progress:
        progress(len(rows), len(rows))
    return {"ok": not bad, "files": len(rows), "failures": ["%s: %s" % b for b in bad], "seconds": round(time.monotonic() - t0, 1), "up_patched": patched}


def report(result):
    if result["ok"]:
        print("game ok: patch 1.2, %d files (%.1f s)" % (result["files"], result["seconds"]))
        if result.get("up_patched"):
            print("note: the Windows Unofficial Patch is installed here (%s); accepted" % ", ".join(result["up_patched"]))
    else:
        print("game check failed: %d of %d files" % (len(result["failures"]), result["files"]))
        for f in result["failures"]:
            print("  " + f)
    return 0 if result["ok"] else EXIT_FAIL


def record(overlay, game, result):
    p = Path(overlay)
    p.mkdir(parents=True, exist_ok=True)
    (p / "game-check.json").write_text(json.dumps(dict(result, game=str(game), at=time.time()), indent=2) + "\n")
