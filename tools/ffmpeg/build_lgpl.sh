#!/bin/bash
set -euo pipefail

DEV="${BN_DEV_HOME:-$HOME/Library/Caches/BloodlinesPorter/dev}"
SRC="${1:-$DEV/ffmpeg/src/ffmpeg-8.1.2}"
OUT="${2:-$DEV/ffmpeg/lgpl}"
OWN_BUILD=
if [ -z "${3:-}" ]; then
  OWN_BUILD=$(mktemp -d "${TMPDIR:-/tmp}/ffmpeg-lgpl-build.XXXXXX")
fi
BUILD="${3:-$OWN_BUILD}"

mkdir -p "$BUILD" "$OUT"
cd "$BUILD"
ln -sfn "$SRC" src
STAGE="$BUILD/stage"
rm -rf "$STAGE"

./src/configure \
  --prefix=/ffmpeg-lgpl \
  --arch=arm64 \
  --install-name-dir=@rpath \
  --disable-everything \
  --disable-autodetect \
  --disable-gpl \
  --disable-nonfree \
  --disable-version3 \
  --disable-programs \
  --disable-doc \
  --disable-debug \
  --disable-network \
  --disable-avdevice \
  --disable-avfilter \
  --disable-swscale \
  --disable-swresample \
  --enable-shared \
  --disable-static \
  --enable-demuxer=bink \
  --enable-decoder=bink,binkaudio_rdft,binkaudio_dct \
  --enable-protocol=file

grep -q '^#define FFMPEG_LICENSE "LGPL version 2.1 or later"' config.h

make -j"${JOBS:-6}"
make install DESTDIR="$STAGE"
cp -R "$STAGE/ffmpeg-lgpl/." "$OUT/"
cp "$SRC/COPYING.LGPLv2.1" "$OUT/COPYING.LGPLv2.1"

if [ -n "$OWN_BUILD" ]; then
  cd /
  rm -r "$OWN_BUILD"
fi

echo "built: $OUT"
