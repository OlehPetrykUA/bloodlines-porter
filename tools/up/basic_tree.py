import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys

KNOWN_MD5 = "dd8951ce97fb61aaddec2ad73607d201"
KNOWN_NAME = "VTMBup120.8.exe (Unofficial Patch 12.0, MD5 %s)" % KNOWN_MD5
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "kit"))
import kit_app
MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifests", "up120.8-basic.tsv")
MANIFEST_NAME = "manifest.tsv"
DUP = re.compile(r"^(.*)\$(\d+)$")


class BasicTreeError(Exception):
    pass


def file_hash(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inside_repo(path):
    try:
        return kit_app.inside_repo(path)
    except (kit_app.Fail, OSError) as e:
        raise BasicTreeError("cannot tell whether %s is inside the repo: %s" % (path, e))


def all_files(root):
    for dirpath, _, names in os.walk(root):
        for name in names:
            if name not in (".DS_Store", ".localized") and not name.startswith("._"):
                yield os.path.join(dirpath, name)


def resolve(tree):
    counts = {"basic": 0, "plus": 0, "dup": 0, "untagged": 0}
    dups = {}
    for path in sorted(all_files(tree)):
        if path.endswith("#basic"):
            plain = path[: -len("#basic")]
            if os.path.lexists(plain):
                raise BasicTreeError("plain copy exists beside %s" % path)
            os.rename(path, plain)
            counts["basic"] += 1
        elif path.endswith("#plus"):
            os.remove(path)
            counts["plus"] += 1
        else:
            m = DUP.match(path)
            if m:
                dups.setdefault(m.group(1), []).append((int(m.group(2)), path))
            else:
                counts["untagged"] += 1
    for plain, copies in dups.items():
        if os.path.lexists(plain):
            raise BasicTreeError("plain copy exists beside %s$N" % plain)
        copies.sort()
        for _, path in copies[:-1]:
            os.remove(path)
        os.rename(copies[-1][1], plain)
        counts["dup"] += len(copies) - 1
        counts["untagged"] += 1
    return counts


def manifest_text(tree):
    rows = []
    for path in all_files(tree):
        rel = os.path.relpath(path, tree).replace(os.sep, "/")
        if rel == MANIFEST_NAME:
            continue
        if "\t" in rel or "\n" in rel:
            raise BasicTreeError("path with tab or newline: %r" % rel)
        rows.append((rel, os.path.getsize(path), file_hash(path, "sha256")))
    rows.sort(key=lambda r: r[0].encode("utf-8"))
    return "".join("%s\t%d\t%s\n" % r for r in rows)


def check_manifest(text, manifest_path):
    if not os.path.isfile(manifest_path):
        raise BasicTreeError("manifest missing: %s" % manifest_path)
    with open(manifest_path, encoding="utf-8", newline="") as f:
        want = f.read()
    if want == text:
        return
    a = {l.split("\t")[0]: l for l in want.splitlines()}
    b = {l.split("\t")[0]: l for l in text.splitlines()}
    missing = sorted(set(a) - set(b))
    extra = sorted(set(b) - set(a))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    raise BasicTreeError(
        "tree differs from manifest: %d missing %s, %d extra %s, %d changed %s"
        % (len(missing), missing[:3], len(extra), extra[:3], len(changed), changed[:3])
    )


def build(installer, out, innoextract=("innoextract",), manifest=MANIFEST, variant="basic",
          known_md5=KNOWN_MD5, write_manifest=False):
    if variant != "basic":
        raise BasicTreeError("variant %r refused: only basic is built until the Plus ruling" % variant)
    if not os.path.isfile(installer):
        raise BasicTreeError("installer not found: %s" % installer)
    if inside_repo(out):
        raise BasicTreeError("output must be outside the repo: %s" % out)
    try:
        got = file_hash(installer, "md5")
    except OSError as e:
        raise BasicTreeError("cannot read installer: %s" % e)
    if got != known_md5:
        raise BasicTreeError("unknown installer MD5 %s; the known build is %s" % (got, KNOWN_NAME))
    tmp = out + ".tmp"
    if os.path.lexists(out) or os.path.lexists(tmp):
        raise BasicTreeError("output or %s already exists; use a new folder" % tmp)
    try:
        subprocess.run(
            list(innoextract) + ["-e", "--collisions", "rename-all", "-q", "-d", tmp, installer],
            check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, OSError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        err = getattr(e, "stderr", None)
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace").strip()
        raise BasicTreeError("innoextract failed: %s" % (err or e))
    try:
        counts = resolve(tmp)
        if counts["plus"] != counts["basic"]:
            raise BasicTreeError("%d #plus copies but %d #basic copies" % (counts["plus"], counts["basic"]))
        text = manifest_text(tmp)
        if write_manifest:
            os.makedirs(os.path.dirname(manifest), exist_ok=True)
            with open(manifest, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        else:
            check_manifest(text, manifest)
        with open(os.path.join(tmp, MANIFEST_NAME), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.rename(tmp, out)
    except BasicTreeError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise BasicTreeError("filesystem error: %s" % e)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    counts["files"] = text.count("\n")
    counts["manifest_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return counts


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("installer")
    p.add_argument("out")
    p.add_argument("--variant", default="basic")
    p.add_argument("--innoextract", default="innoextract")
    p.add_argument("--write-manifest", action="store_true")
    a = p.parse_args(argv)
    try:
        c = build(os.path.abspath(a.installer), os.path.abspath(a.out), (a.innoextract,),
                  variant=a.variant, write_manifest=a.write_manifest)
    except BasicTreeError as e:
        print("basic_tree: %s" % e, file=sys.stderr)
        return 1
    print("basic kept %(basic)d, plus dropped %(plus)d, duplicates dropped %(dup)d, "
          "untagged kept %(untagged)d, files %(files)d, manifest sha256 %(manifest_sha256)s" % c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
