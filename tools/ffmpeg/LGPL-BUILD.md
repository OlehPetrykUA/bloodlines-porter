# LGPL-only FFmpeg for the Bink movies

The runtime (`shims/bink.c`) needs only libavformat (bink demuxer, file protocol), libavcodec (bink, binkaudio_rdft, binkaudio_dct) and libavutil. It uses no swscale and no swresample, so this build has neither.

Homebrew's FFmpeg is configured with `--enable-gpl --enable-version3` and is therefore GPLv3. This build is LGPL 2.1 or later and can be bundled.

## Source

- URL: https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz (signature: https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz.asc)
- Version: 8.1.2 (same as Homebrew, so the sonames match: libavformat.62, libavcodec.62, libavutil.60)
- Size: 11710924 bytes
- sha256: `464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c` (equals the sha256 in Homebrew's ffmpeg formula; the PGP signature was not checked because gpg is not installed)

## Build

```
tools/ffmpeg/build_lgpl.sh [SRC_DIR] [OUT_DIR] [BUILD_DIR]
```

Defaults (base `$BN_DEV_HOME`, else `~/Library/Caches/BloodlinesPorter/dev`): `<base>/ffmpeg/src/ffmpeg-8.1.2`, `<base>/ffmpeg/lgpl`, a fresh temp dir (removed on success). The script also copies `COPYING.LGPLv2.1` to `<OUT>/`, where `kit_app.py build --ffmpeg-lgpl <OUT>/lib` reads it. The script fails if `config.h` does not say LGPL.

The script runs `./src/configure` through a `src` symlink to the source tree and installs with `DESTDIR` into a staging dir, then copies the result into `<OUT>/`. The prefix is neutral and `__FILE__` paths are relative, so no local username lands in the dylibs or `config.h`.

Configure line recorded in `config.h`:

```
--prefix=/ffmpeg-lgpl --arch=arm64 --install-name-dir='@rpath' --disable-everything --disable-autodetect --disable-gpl --disable-nonfree --disable-version3 --disable-programs --disable-doc --disable-debug --disable-network --disable-avdevice --disable-avfilter --disable-swscale --disable-swresample --enable-shared --disable-static --enable-demuxer=bink --enable-decoder='bink,binkaudio_rdft,binkaudio_dct' --enable-protocol=file
```

## Licence

`FFMPEG_LICENSE` is "LGPL version 2.1 or later". Dynamic linking is kept (shared libraries only). When bundling, ship the licence text (`COPYING.LGPLv2.1` from the source tarball) and a pointer to the source URL above. See https://ffmpeg.org/legal.html.

## Result

- `otool -L` of the three dylibs: only `@rpath/libav*`, libSystem, CoreFoundation, CoreVideo, CoreMedia.
- `bloodlines-native --audio-selftest` with `DYLD_LIBRARY_PATH` at `<OUT>/lib`: 93 PASS, 0 FAIL; `DYLD_PRINT_LIBRARIES=1` shows the three libav dylibs loaded from `<OUT>/lib`.
