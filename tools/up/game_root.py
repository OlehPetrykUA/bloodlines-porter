import argparse
import ctypes
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "kit"))
import kit_app

MOD = "Unofficial_Patch"
STAMP = "gu.sha256"
GRAPHS = ("Vampire", "maps", "graphs")
LOADER_INI = ("Bin", "loader", "loader.ini")
NO_PLUGIN_BLOCKS = {
    "resource/optionssubvideo.res": ("DisplayMode", "FOV Slider"),
    "resource/optionssubaudio.res": ("Note", "Sound Slider", "Music Slider"),
}
NO_PLUGIN_LINES = {
    "scripts/kb_act - hunter.lst": b'"+ironsights"',
    "scripts/kb_act - vampire.lst": b'"+ironsights"',
}


class GameRootError(Exception):
    pass


def without_blocks(data, names):
    out, lines, i = [], data.splitlines(keepends=True), 0
    while i < len(lines):
        m = re.match(rb'\s*"([^"]+)"\s*(//.*)?$', lines[i])
        if m and m.group(1).decode("latin-1") in names and i + 1 < len(lines) and lines[i + 1].strip() == b"{":
            depth, i = 0, i + 1
            while i < len(lines):
                depth += lines[i].count(b"{") - lines[i].count(b"}")
                i += 1
                if depth == 0:
                    break
            continue
        out.append(lines[i])
        i += 1
    return b"".join(out)


def mod_bytes(rel, data):
    key = rel.replace(os.sep, "/").lower()
    if key in NO_PLUGIN_BLOCKS:
        return without_blocks(data, NO_PLUGIN_BLOCKS[key])
    if key in NO_PLUGIN_LINES:
        return b"".join(l for l in data.splitlines(keepends=True) if not l.lstrip().startswith(NO_PLUGIN_LINES[key]))
    return data


def mod_rewrites(rel):
    key = rel.replace(os.sep, "/").lower()
    return key in NO_PLUGIN_BLOCKS or key in NO_PLUGIN_LINES


def drop_no_plugin_ui(mod):
    for rel in list(NO_PLUGIN_BLOCKS) + list(NO_PLUGIN_LINES):
        p = ci_path(mod, rel.split("/"))
        if not p:
            continue
        with open(p, "rb") as f:
            data = f.read()
        parent = os.path.dirname(p)
        os.chmod(parent, os.stat(parent).st_mode | stat.S_IWUSR)
        os.remove(p)
        with open(p, "wb") as f:
            f.write(mod_bytes(rel, data))


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inside_repo(path):
    try:
        return kit_app.inside_repo(path)
    except (kit_app.Fail, OSError) as e:
        raise GameRootError("cannot tell whether %s is inside the repo: %s" % (path, e))


def same_or_inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def ci_child(parent, name):
    try:
        names = os.listdir(parent)
    except OSError:
        return None
    for n in names:
        if n.lower() == name.lower():
            return n
    return None


def ci_path(root, parts):
    cur = root
    for p in parts:
        n = ci_child(cur, p)
        if n is None:
            return None
        cur = os.path.join(cur, n)
    return cur


def ci_make(root, parts):
    cur = root
    for p in parts:
        n = ci_child(cur, p) or p
        cur = os.path.join(cur, n)
        os.makedirs(cur, exist_ok=True)
    return cur


def app_root(u):
    inner = ci_path(u, ("app",))
    if inner and ci_path(inner, (MOD,)):
        return inner
    if ci_path(u, (MOD,)):
        return u
    raise GameRootError("%s holds no %s folder" % (u, MOD))


def is_junk(name):
    return name in (".DS_Store", ".localized") or name.startswith("._")


def files_under(root):
    for dirpath, _, names in os.walk(root):
        for name in names:
            if not is_junk(name):
                yield os.path.join(dirpath, name)


def graph_files(u):
    d = ci_path(u, GRAPHS)
    if not d:
        raise GameRootError("%s has no Vampire/maps/graphs" % u)
    names = sorted(n for n in os.listdir(d) if n.lower().endswith(".ain"))
    if not names:
        raise GameRootError("%s holds no .ain graphs" % d)
    return d, names


def loader_ini(u):
    p = ci_path(u, LOADER_INI)
    if not p:
        raise GameRootError("%s has no Bin/loader/loader.ini" % u)
    return p


def loader_files(u):
    d = os.path.dirname(loader_ini(u))
    return d, sorted(n for n in os.listdir(d) if n.lower().endswith((".vtm", ".ini")) and not is_junk(n) and os.path.isfile(os.path.join(d, n)))


def unlock(path):
    if not os.path.lexists(path):
        return
    for dirpath, dirs, names in os.walk(path):
        for n in dirs + names:
            p = os.path.join(dirpath, n)
            if not os.path.islink(p):
                os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)
    os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)


def lock(path):
    for dirpath, dirs, names in os.walk(path, topdown=False):
        for n in dirs + names:
            p = os.path.join(dirpath, n)
            if not os.path.islink(p):
                os.chmod(p, os.stat(p).st_mode & ~0o222)
    os.chmod(path, os.stat(path).st_mode & ~0o222)


def writable_anywhere(path):
    for dirpath, dirs, names in os.walk(path):
        for n in dirs + names + ["."]:
            p = os.path.join(dirpath, n)
            if not os.path.islink(p) and os.stat(p).st_mode & 0o222:
                return p
    return None


def can_clone(parent):
    libc = ctypes.CDLL(None, use_errno=True)
    fn = getattr(libc, "clonefile", None)
    if fn is None:
        return False
    with tempfile.TemporaryDirectory(dir=parent) as d:
        src, dst = os.path.join(d, "a"), os.path.join(d, "b")
        with open(src, "wb") as f:
            f.write(b"x")
        return fn(src.encode(), dst.encode(), 0) == 0


def run_cp(args):
    r = subprocess.run(["cp"] + args, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if r.returncode:
        raise GameRootError("cp failed: %s" % r.stderr.strip())


def fingerprint(g, u):
    h = hashlib.sha256()
    d, names = graph_files(u)
    for n in names:
        h.update(("graph %s %s\n" % (n, file_hash(os.path.join(d, n)))).encode())
    ld, lnames = loader_files(u)
    for n in lnames:
        h.update(("loader %s %s\n" % (n, file_hash(os.path.join(ld, n)))).encode())
    manifest = os.path.join(u, "manifest.tsv")
    if os.path.isfile(manifest):
        h.update(("manifest %s\n" % file_hash(manifest)).encode())
    else:
        mod = ci_path(u, (MOD,))
        for p in sorted(files_under(mod)):
            h.update(("mod %s %d\n" % (os.path.relpath(p, mod), os.path.getsize(p))).encode())
    for n in sorted(os.listdir(g)):
        p = os.path.join(g, n)
        if os.path.isfile(p) and not is_junk(n):
            h.update(("g %s %d\n" % (n, os.path.getsize(p))).encode())
    return h.hexdigest()


def stamp_of(gu):
    try:
        with open(os.path.join(gu, STAMP)) as f:
            return f.read().strip()
    except OSError:
        return None


def lay(g, u, tmp):
    mod_src = ci_path(u, (MOD,))
    old = ci_child(tmp, MOD)
    if old:
        shutil.rmtree(os.path.join(tmp, old))
    run_cp(["-c", "-R", mod_src, os.path.join(tmp, MOD)])
    drop_no_plugin_ui(os.path.join(tmp, MOD))
    gdir, names = graph_files(u)
    out = ci_make(tmp, GRAPHS)
    for n in names:
        dst = os.path.join(out, n)
        if os.path.lexists(dst):
            os.remove(dst)
        shutil.copyfile(os.path.join(gdir, n), dst)
    ld, lnames = loader_files(u)
    lout = ci_make(tmp, LOADER_INI[:-1])
    for n in lnames:
        dst = os.path.join(lout, n)
        if os.path.lexists(dst):
            os.remove(dst)
        shutil.copyfile(os.path.join(ld, n), dst)


def build(g, u, gu, force=False, log=print, cloner=can_clone, layer=lay):
    g, gu = os.path.abspath(g), os.path.abspath(gu)
    if not os.path.isdir(g):
        raise GameRootError("game folder not found: %s" % g)
    if not os.path.isfile(os.path.join(g, "vampire.exe")) and not ci_child(g, "vampire.exe"):
        raise GameRootError("%s is not a game folder: no vampire.exe" % g)
    u = app_root(os.path.abspath(u))
    if same_or_inside(gu, g) or same_or_inside(g, gu):
        raise GameRootError("the game root and the game folder must be separate folders")
    if same_or_inside(gu, u) or same_or_inside(u, gu):
        raise GameRootError("the game root and the UP tree must be separate folders")
    if inside_repo(gu):
        raise GameRootError("output must be outside the repo: %s" % gu)
    want = fingerprint(g, u)
    if os.path.lexists(gu):
        have = stamp_of(gu)
        if have is None:
            raise GameRootError("%s exists and is not a game root built by this tool" % gu)
        if have == want and not force:
            log("game root %s is current" % gu)
            return "current"
        unlock(gu)
        shutil.rmtree(gu)
    tmp = gu + ".tmp"
    if os.path.lexists(tmp):
        raise GameRootError("%s already exists; remove it" % tmp)
    parent = os.path.dirname(gu)
    os.makedirs(parent, exist_ok=True)
    if not cloner(parent):
        log("warning: %s cannot clone files (not APFS?); making a full copy" % parent)
    try:
        run_cp(["-c", "-R", g, tmp])
        unlock(tmp)
        layer(g, u, tmp)
        with open(os.path.join(tmp, STAMP), "w") as f:
            f.write(want + "\n")
        lock(tmp)
        os.rename(tmp, gu)
    except BaseException:
        unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    log("game root %s built" % gu)
    return "built"


def verify(g, u, gu, deep=False):
    u = app_root(os.path.abspath(u))
    bad = []
    if not os.path.isdir(gu):
        raise GameRootError("game root not found: %s" % gu)
    mod_src = ci_path(u, (MOD,))
    mod_dst = ci_path(gu, (MOD,))
    if not mod_dst:
        bad.append("mod folder %s missing" % MOD)
    else:
        for p in files_under(mod_src):
            rel = os.path.relpath(p, mod_src)
            q = ci_path(mod_dst, rel.split(os.sep))
            if not q:
                bad.append("mod file missing: %s" % rel)
            elif mod_rewrites(rel):
                with open(p, "rb") as a, open(q, "rb") as b:
                    if mod_bytes(rel, a.read()) != b.read():
                        bad.append("mod file differs: %s" % rel)
            elif os.path.getsize(q) != os.path.getsize(p) or (deep and file_hash(q) != file_hash(p)):
                bad.append("mod file differs: %s" % rel)
    gdir, names = graph_files(u)
    for n in names:
        q = ci_path(gu, GRAPHS + (n,))
        if not q or file_hash(q) != file_hash(os.path.join(gdir, n)):
            bad.append("graph missing or differs: %s" % n)
    ld, lnames = loader_files(u)
    for n in lnames:
        q = ci_path(gu, LOADER_INI[:-1] + (n,))
        if not q or file_hash(q) != file_hash(os.path.join(ld, n)):
            bad.append("loader.ini missing or differs" if n.lower() == LOADER_INI[-1] else "loader file missing or differs: %s" % n)
    for n in sorted(os.listdir(g)):
        if n.lower() in (MOD.lower(), "bin", "vampire") or is_junk(n):
            continue
        if ci_child(gu, n) is None:
            bad.append("game file missing: %s" % n)
    ex = writable_anywhere(gu)
    if ex:
        bad.append("not read-only: %s" % ex)
    if stamp_of(gu) is None:
        bad.append("stamp %s missing" % STAMP)
    if bad:
        raise GameRootError("; ".join(bad[:8]) + (" (+%d more)" % (len(bad) - 8) if len(bad) > 8 else ""))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("game")
    p.add_argument("up")
    p.add_argument("out")
    p.add_argument("--force", action="store_true")
    p.add_argument("--verify", action="store_true")
    a = p.parse_args(argv)
    try:
        r = build(a.game, a.up, a.out, force=a.force)
        if a.verify:
            verify(a.game, a.up, a.out, deep=True)
            print("game root verified")
    except GameRootError as e:
        print("game_root: %s" % e, file=sys.stderr)
        return 1
    except OSError as e:
        print("game_root: filesystem error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
