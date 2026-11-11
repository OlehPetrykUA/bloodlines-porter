import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import vpk_list

BINARY = (".bsp", ".mdl", ".vtf", ".tth", ".ttz")
TEXT = (".vmt", ".res")
HEAD = 16


def version_of(ext, head):
    if ext == ".bsp":
        ok = head[:4] == b"VBSP" and len(head) >= 8
        return "%d" % struct.unpack_from("<I", head, 4) if ok else "bad:" + head[:4].hex()
    if ext == ".mdl":
        ok = head[:4] == b"IDST" and len(head) >= 8
        return "%d" % struct.unpack_from("<I", head, 4) if ok else "bad:" + head[:4].hex()
    if ext == ".vtf":
        ok = head[:4] == b"VTF\0" and len(head) >= 12
        return "%d.%d" % struct.unpack_from("<II", head, 4) if ok else "bad:" + head[:4].hex()
    if ext == ".tth":
        ok = head[:4] == b"TTH\0" and len(head) >= 6
        return "%d" % struct.unpack_from("<H", head, 4) if ok else "bad:" + head[:4].hex()
    if ext == ".ttz":
        ok = len(head) >= 2 and head[0] & 15 == 8 and ((head[0] << 8) | head[1]) % 31 == 0
        return "zlib" if ok else "bad:" + head[:4].hex()
    return "bad:" + head[:4].hex()


def ext_of(path):
    return os.path.splitext(path)[1].lower()


def text_ok(data):
    depth = 0
    quoted = False
    for c in data.decode("latin-1"):
        if quoted:
            quoted = c != '"' and c != "\n"
        elif c == '"':
            quoted = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quoted


def text_ok_stripped(data):
    lines = []
    for ln in data.split(b"\n"):
        i = ln.find(b"//")
        q = ln.count(b'"', 0, i) if i >= 0 else 0
        lines.append(ln[:i] if i >= 0 and q % 2 == 0 else ln)
    return text_ok(b"\n".join(lines))


def shader_of(data):
    return vpk_list.vmt_shader(data)


def scan_loose(root):
    out = []
    for d, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            full = os.path.join(d, f)
            out.append((os.path.relpath(full, root).replace(os.sep, "/"), full))
    return out


def gather_dir(root):
    rows = []
    for rel, full in scan_loose(root):
        e = ext_of(rel)
        if e in BINARY:
            with open(full, "rb") as f:
                rows.append((rel, e, version_of(e, f.read(HEAD)), None))
        elif e in TEXT:
            with open(full, "rb") as f:
                data = f.read()
            rows.append((rel, e, "ok" if text_ok_stripped(data) else "unbalanced", shader_of(data) if e == ".vmt" else None))
    return rows


def gather_game(game):
    rows = []
    vamp = os.path.join(game, "Vampire")
    if os.path.isdir(vamp):
        for rel, full in scan_loose(vamp):
            e = ext_of(rel)
            if e in BINARY or e in TEXT:
                with open(full, "rb") as f:
                    data = f.read() if e in TEXT else f.read(HEAD)
                if e in BINARY:
                    rows.append((rel, e, version_of(e, data), None))
                else:
                    rows.append((rel, e, "ok" if text_ok_stripped(data) else "unbalanced", shader_of(data) if e == ".vmt" else None))
    handles = {}
    try:
        for ent in vpk_list.all_entries(game):
            e = ext_of(ent.path)
            if ent.dir_only or (e not in BINARY and e not in TEXT):
                continue
            h = handles.get(ent.archive)
            if h is None:
                h = handles[ent.archive] = open(os.path.join(vamp, ent.archive), "rb")
            h.seek(ent.offset)
            data = h.read(ent.size if e in TEXT else min(ent.size, HEAD))
            if e in BINARY:
                rows.append((ent.path, e, version_of(e, data), None))
            else:
                rows.append((ent.path, e, "ok" if text_ok_stripped(data) else "unbalanced", shader_of(data) if e == ".vmt" else None))
    finally:
        for h in handles.values():
            h.close()
    return rows


def census(rows):
    return collections.Counter((e, v) for _, e, v, _ in rows)


def case_pairs(paths):
    seen = collections.defaultdict(list)
    for p in paths:
        seen[p.lower()].append(p)
    return sorted(tuple(v) for v in seen.values() if len(v) > 1)


def non_cp1252(paths):
    out = []
    for p in paths:
        try:
            p.encode("cp1252")
        except UnicodeEncodeError:
            out.append(p)
    return sorted(out)


def check(u_rows, g_rows, u_paths):
    uc = census(u_rows)
    gc = census(g_rows)
    problems = []
    for (e, v), n in sorted(uc.items()):
        if e in BINARY and (e, v) not in gc:
            files = [r[0] for r in u_rows if r[1] == e and r[2] == v]
            problems.append("version %s %s not in vanilla: %d file(s), first %s" % (e, v, n, files[0]))
    for rel, e, v, _ in u_rows:
        if e in TEXT and v != "ok":
            problems.append("text %s: %s" % (v, rel))
    gshaders = {s for _, e, _, s in g_rows if e == ".vmt" and s}
    for rel, e, _, s in u_rows:
        if e == ".vmt" and s and s not in gshaders:
            problems.append("vmt shader %s not in vanilla: %s" % (s, rel))
    for pair in case_pairs(u_paths):
        problems.append("case pair: %s" % " | ".join(pair))
    for p in non_cp1252(u_paths):
        problems.append("not CP1252: %s" % p)
    return problems


def report(u_rows, g_rows, problems):
    uc = census(u_rows)
    gc = census(g_rows)
    lines = []
    for e in BINARY:
        for v in sorted({v for (x, v) in list(uc) + list(gc) if x == e}):
            lines.append("formats %s %s U=%d G=%d" % (e, v, uc[(e, v)], gc[(e, v)]))
    for p in problems:
        lines.append("formats fail " + p)
    lines.append("formats total U=%d G=%d fail %d" % (len(u_rows), len(g_rows), len(problems)))
    return lines


def run(u, game):
    u_rows = gather_dir(u)
    g_rows = gather_game(game)
    paths = [rel for rel, _ in scan_loose(u)]
    problems = check(u_rows, g_rows, paths)
    return report(u_rows, g_rows, problems), problems


def main(argv):
    if len(argv) != 3:
        print("usage: formats.py U_DIR GAME_DIR", file=sys.stderr)
        return 2
    lines, problems = run(argv[1], argv[2])
    print("\n".join(lines))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
