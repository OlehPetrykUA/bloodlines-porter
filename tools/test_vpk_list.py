import os
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devpaths
import vpk_list

game = sys.argv[1] if len(sys.argv) > 1 else devpaths.GAME

es = vpk_list.all_entries(game)
assert len(es) > 0
assert all(e.offset + e.size <= e.limit for e in es)

with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "strings.txt")
    subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "vpk_list.py"), "extract", game, "vdata/system/stats.txt", out], check=True)
    text = open(out, "rb").read().decode("latin-1")
    assert len(text) > 100 and all(c.isprintable() or c in "\r\n\t" for c in text)

vcs = [e for e in es if e.path.startswith("shaders/") and e.path.endswith(".vcs")]
assert vcs
tokens = [t for e in vcs for t in vpk_list.vcs_tokens(vpk_list.read_entry(game, e))]
assert tokens.count(-1) == 0
assert {vpk_list.model(t) for t in tokens if t > 0} >= {"vs_1_1", "ps_1_1", "ps_2_0"}

def raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def pack(tmp, name, payload, directory, count, ver):
    p = os.path.join(tmp, name)
    with open(p, "wb") as f:
        f.write(payload + directory + struct.pack("<IIB", count, len(payload), ver))
    return p


with tempfile.TemporaryDirectory() as tmp:
    short = os.path.join(tmp, "short.vpk")
    with open(short, "wb") as f:
        f.write(b"12345")
    assert raises(lambda: vpk_list.read_pack(short))
    assert raises(lambda: vpk_list.read_pack(pack(tmp, "v7.vpk", b"", b"", 0, 7)))
    entry = lambda size: struct.pack("<I", 1) + b"x" + struct.pack("<II", 0, size)
    assert vpk_list.read_pack(pack(tmp, "ok.vpk", b"ab", entry(2), 1, 0))[0].size == 2
    assert raises(lambda: vpk_list.read_pack(pack(tmp, "past.vpk", b"ab", entry(99), 1, 0)))
    assert raises(lambda: vpk_list.read_pack(pack(tmp, "cut.vpk", b"ab", entry(2)[:-3], 1, 0)))

assert vpk_list.vcs_tokens(b"ab") == [-1]
assert vpk_list.vcs_tokens(struct.pack("<5I", 0, 5, 0, 0, 0)) == [-1]
dup = next(p for p in {e.path for e in es if not e.dir_only} if sum(1 for e in es if e.path == p) > 1)
assert subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "vpk_list.py"), "extract", game, dup, os.devnull], stderr=subprocess.DEVNULL).returncode != 0

print("ok", len(es), "entries,", len(tokens), "combos")
