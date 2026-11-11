#!/bin/sh
set -eu

usage() {
    cat <<'EOF'
usage: tools/release/build.sh [options]

Builds the release zip of Bloodlines Porter from a fresh clone, in a temp folder.
It never needs game files and never starts the game.

  --ref REF              commit or branch of this repository to build (default: HEAD)
  --work DIR             work folder (default: a new folder under $TMPDIR)
  --out DIR              where the zip and SHA256SUMS go (default: <work>/dist)
  --version V            app version, dotted numbers (default: 0.1)
  --spec FILE            prebuilt-spec.json: the non-game libraries to bundle
  --ffmpeg-tarball FILE  ffmpeg-8.1.2.tar.xz
  --python-archive FILE  the python-build-standalone archive named in tools/kit/python.lock
  --wheels DIR           folder with the wheels pinned in tools/kit/requirements-build.txt
  --pgo-profile FILE     ship a clang profile in the kit (off by default: it holds game function addresses)
  --with-tests           run the python unit tests of tools/kit and tools/up in the clone first
  --keep                 keep the work folder

The script and everything it starts run at nice 10 (or nicer if started so).
Inputs default to $BN_DEV_HOME/kit-cache (BN_DEV_HOME defaults to ~/Library/Caches/BloodlinesPorter/dev).
Signing is ad hoc. Developer ID signing and notarization are not done here (docs/developing.md).
EOF
}

[ "$(ps -o nice= -p $$)" -ge 10 ] || renice -n 10 -p $$ >/dev/null

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DEV=${BN_DEV_HOME:-$HOME/Library/Caches/BloodlinesPorter/dev}
KC=$DEV/kit-cache
REF=$(git -C "$REPO" rev-parse HEAD)
WORK=
OUT=
VERSION=0.1
SPEC=$KC/prebuilt-spec.json
FFMPEG=$DEV/ffmpeg/src/ffmpeg-8.1.2.tar.xz
PYARCHIVE=
WHEELS=$KC
PGO=
TESTS=0
KEEP=0

while [ $# -gt 0 ]; do
    case "$1" in
        --ref) REF=$2; shift 2 ;;
        --work) WORK=$2; shift 2 ;;
        --out) OUT=$2; shift 2 ;;
        --version) VERSION=$2; shift 2 ;;
        --spec) SPEC=$2; shift 2 ;;
        --ffmpeg-tarball) FFMPEG=$2; shift 2 ;;
        --python-archive) PYARCHIVE=$2; shift 2 ;;
        --wheels) WHEELS=$2; shift 2 ;;
        --pgo-profile) PGO=$2; shift 2 ;;
        --with-tests) TESTS=1; shift ;;
        --keep) KEEP=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[ -n "$PYARCHIVE" ] || PYARCHIVE=$(ls "$KC"/cpython-*-aarch64-apple-darwin-install_only.tar.gz 2>/dev/null | head -n 1 || true)

for f in "$SPEC" "$FFMPEG" "$PYARCHIVE"; do
    [ -f "$f" ] || { echo "release-build: missing input: ${f:-python archive}" >&2; exit 2; }
done
[ -d "$WHEELS" ] || { echo "release-build: missing wheels folder $WHEELS" >&2; exit 2; }
[ "$(uname -m)" = arm64 ] || { echo "release-build: needs an Apple silicon Mac" >&2; exit 2; }

if [ -z "$WORK" ]; then
    WORK=$(mktemp -d "${TMPDIR:-/tmp}/bloodlines-release.XXXXXX")
else
    mkdir -p "$WORK"
fi
WORK=$(cd "$WORK" && pwd)
[ -n "$OUT" ] || OUT=$WORK/dist
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

cleanup() {
    if [ "$KEEP" = 0 ]; then
        rm -rf "$WORK/clone" "$WORK/swift-build" "$WORK/kit"
    fi
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }

step "fresh clone of $REF"
git clone --quiet --no-hardlinks "$REPO" "$WORK/clone"
git -C "$WORK/clone" checkout --quiet "$REF"
SHA=$(git -C "$WORK/clone" rev-parse HEAD)
echo "commit $SHA"
CLONE=$WORK/clone
PY=${PYTHON:-python3}

if [ "$TESTS" = 1 ]; then
    step "python unit tests"
    for d in tools/kit tools/up; do
        (cd "$CLONE" && "$PY" -I -B -m unittest discover -s "$d" -p 'test_*.py')
    done
fi

step "cut the kit"
set -- "$WORK/kit" --repo "$CLONE" --python-archive "$PYARCHIVE" --wheels "$WHEELS" --prebuilt-spec "$SPEC" --ffmpeg-tarball "$FFMPEG"
[ -z "$PGO" ] || set -- "$@" --pgo-profile "$PGO"
"$PY" -I -B "$CLONE/tools/kit/make_kit.py" "$@"

step "build and sign Bloodlines Porter.app (ad hoc)"
"$PY" -I -B "$CLONE/tools/kit/app/package.py" "$WORK/app" --kit-folder "$WORK/kit" --build-path "$WORK/swift-build" --version "$VERSION"
APP=$WORK/app/Bloodlines\ Porter.app

step "checks"
codesign --verify --strict --deep "$APP"
codesign -dv "$APP" 2>&1 | sed -n 's/^\(Signature\|Authority\|TeamIdentifier\|Identifier\)=/\1: /p'
"$PY" -I -B "$CLONE/tools/kit/make_kit.py" check "$APP/Contents/Resources/porting-kit"
if grep -r -a -l -e "$HOME/" -e "$WORK" "$APP/Contents/MacOS" "$APP/Contents/Resources/kit" "$APP/Contents/Info.plist" >/dev/null 2>&1; then
    echo "release-build: a host path is inside the app" >&2
    exit 1
fi
if [ -n "$(find "$APP" \( -iname '*.vpk' -o -iname '*.bsp' -o -iname '*.dll' -o -iname '*.exe' -o -iname 'VTMBup*' -o -iname 'loader.ini' \) -print -quit)" ]; then
    echo "release-build: a game or patch file is inside the app" >&2
    exit 1
fi
echo "ok"

step "zip and checksum"
ZIP=BloodlinesPorter-$VERSION-macos-arm64.zip
rm -f "$OUT/$ZIP" "$OUT/SHA256SUMS"
(cd "$WORK/app" && ditto -c -k --keepParent "Bloodlines Porter.app" "$OUT/$ZIP")
(cd "$OUT" && shasum -a 256 "$ZIP" > SHA256SUMS && shasum -a 256 -c SHA256SUMS)

step "result"
echo "source commit: $SHA"
echo "zip:           $OUT/$ZIP"
echo "checksums:     $OUT/SHA256SUMS"
echo "layout:        Bloodlines Porter.app/Contents/{MacOS,Resources/kit,Resources/porting-kit}"
echo "porting-kit:   $(ls "$APP/Contents/Resources/porting-kit" | tr '\n' ' ')"
echo "signing:       ad hoc, not notarized"
