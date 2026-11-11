# Third-party notices

Bloodlines Porter is MIT licensed (`LICENSE`). That covers the code written for this project. It does not cover Vampire: The Masquerade - Bloodlines, the Unofficial Patch or any file you supply from them, and the components below keep their own licences.

The release zip carries the full licence text of every shipped component in `licenses/<component>/`, listed in its `NOTICE.md` and checked at cut time by `tools/kit/make_kit.py check`. The "Licence file" column names the file in the component's own source or package that the text and SPDX id were taken from.

## In this repository

| Component | Version | SPDX | Where | Licence file |
| --- | --- | --- | --- | --- |
| xboxrecomp (sp00nz), recompiler subset | commit `f0f8a461496a4f23d9b0aeaa284b8d70ba7ab82b` | MIT | `third_party/xboxrecomp/recomp/*.py`, `runtime/recomp_types.h`, `recomp/data/win32_api_names.txt` | `third_party/xboxrecomp/LICENSE`, source in `third_party/xboxrecomp/SOURCE` |
| dr_mp3 (David Reid), built on minimp3 (lieff) | 0.7.3 | Unlicense or MIT-0 (your choice); minimp3 parts CC0-1.0 | `third_party/dr_mp3/dr_mp3.h` | the licence block at the end of `third_party/dr_mp3/dr_mp3.h` |
| Patches to dxvk | on top of the dxvk fork below | Zlib | `third_party/dxvk/*.patch` | dxvk `LICENSE` |
| Patches to Mesa (KosmicKrisp) | on top of Mesa 26.2.5 | MIT | `third_party/mesa/*.patch` | Mesa `docs/license.rst` |

xboxrecomp ships other licences in its own tree (LGPL-2.1-or-later files extracted from xemu). None of those files is vendored here: only the MIT-licensed recompiler files listed above are.

## In the release zip (built or bundled, not in this repository)

| Component | Version | SPDX | Licence file |
| --- | --- | --- | --- |
| SDL3 | 3.4.10 | Zlib | `LICENSE.txt` of the SDL 3.4.10 package |
| dxvk, fork `jm2/dxvk` branch `kisakcod-macos` (D3D9 on Vulkan) | `dd3a05bde9bae53eecd9b7314761c8c4571cb4f1` (v3.1.1-74) plus `third_party/dxvk/*.patch` | Zlib | `LICENSE` in the dxvk source tree (zlib/libpng licence; copyright Philip Rebohle, Joshua Ashton, Robin Kertels, Jeffrey Ellison) |
| dxbc-spirv, linked into the dxvk library | `c7f069701227dcb800a01bd93e42f8001b7543e7` | MIT | `subprojects/dxbc-spirv/LICENSE` in the dxvk tree |
| libdisplay-info, a dxvk subproject (compiled in when the dxvk build enables it) | as pinned by the dxvk tree | MIT | `subprojects/libdisplay-info/LICENSE` in the dxvk tree |
| Mesa, KosmicKrisp Vulkan driver | 26.2.5 plus `third_party/mesa/*.patch` | MIT for 810 of the 848 source files in the built library (270 with an SPDX line, 540 with MIT-style header text or none); the other 38 are listed in the Mesa section below; no GPL file | `docs/license.rst` and `licenses/` in the Mesa 26.2.5 source |
| Vulkan-Loader | 1.4.363.0 | Apache-2.0 (a few files more permissive) | `LICENSE.txt` of the vulkan-loader 1.4.363.0 package |
| SPIRV-Tools | 1.4.363.0 | Apache-2.0 | `LICENSE` of the spirv-tools 1.4.363.0 package |
| zstd | 1.5.7 | BSD-3-Clause (zstd is dual BSD or GPL-2.0; the BSD terms are used) | `LICENSE` of the zstd 1.5.7 package |
| FFmpeg libavformat, libavcodec, libavutil (LGPL build, no GPL or non-free parts) | 8.1.2 | LGPL-2.1-or-later | `COPYING.LGPLv2.1` in the FFmpeg 8.1.2 source |
| python-build-standalone CPython | 3.13.18, release 20261026 | PSF-2.0 | `lib/python3.13/LICENSE.txt` in the archive |
| pip, bundled in that CPython | 26.2.2 | MIT, with vendored libraries under MIT, BSD, Apache-2.0, ISC, PSF-2.0 and MPL-2.0 (certifi) | `pip-26.2.2.dist-info/licenses/` in the archive, copied to `licenses/python/pip/` |
| capstone (Python wheel) | 5.0.9 | BSD-3-Clause | `capstone-5.0.9.dist-info/LICENSE.TXT` in the wheel |
| meson (Python wheel) | 1.12.1 | Apache-2.0 | `meson-1.12.1.dist-info/licenses/COPYING` in the wheel |
| ninja (Python wheel) | 1.13.2 | Apache-2.0 | `ninja-1.13.2.dist-info/licenses/LICENSE_Apache_20` in the wheel |

### FFmpeg (LGPL-2.1-or-later)

FFmpeg is used as separate dynamic libraries, so you can replace them. The release zip carries the unmodified source `sources/ffmpeg-8.1.2.tar.xz` (from https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz, sha256 `464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c`), the configure line and build steps in `tools/ffmpeg/LGPL-BUILD.md`, and `Resources/LGPL-FFMPEG-NOTICE.txt`. Changes to the FFmpeg source: none. This software uses libraries from the FFmpeg project under the LGPLv2.1.

### Libraries inside the bundled CPython

The python-build-standalone `libpython3.13.dylib` (release 20261026) links libraries statically. Their texts are not in the `install_only` archive, so the repository carries them in `tools/kit/licences/python/` and `make_kit.py` copies them to `licenses/python/<name>/`; `check` fails the kit when one is missing or changed.

| Library | Version | SPDX | Where the version was read | Where the text comes from |
| --- | --- | --- | --- | --- |
| OpenSSL | 3.5.9 | Apache-2.0 | `libpython3.13.dylib` strings, `ssl.OPENSSL_VERSION` | `LICENSE.txt` at upstream tag `openssl-3.5.9` |
| expat | 2.9.0 | MIT | `libpython3.13.dylib` strings, `pyexpat.EXPAT_VERSION` | `expat/COPYING` at upstream tag `R_2_9_0` |
| mpdecimal | 4.0.0 | BSD-2-Clause | `decimal.__libmpdec_version__` | `COPYRIGHT.txt` of the mpdecimal 4.0.1 package; not compared with the 4.0.0 tarball |

| bzip2 | 1.0.8 | bzip2-1.0.6 | `libpython3.13.dylib` string `1.0.8, 13-Jul-2019`; python-build-standalone `downloads.json` at tag 20261026 | `LICENSE` at upstream tag `bzip2-1.0.8` (sourceware.org git) |
| libffi | 3.8.0 | MIT | `libpython3.13.dylib` string `3.8.0`; `downloads.json` at tag 20261026 | `LICENSE` at upstream tag `v3.8.0` |
| liblzma (xz) | 5.8.4 | 0BSD | `libpython3.13.dylib` string `5.8.4`; `downloads.json` at tag 20261026 | `COPYING.0BSD` at upstream tag `v5.8.4` |
| SQLite | 3.53.1 | public domain (blessing) | `sqlite3.sqlite_version` | `LICENSE.md` at upstream tag `version-3.53.1` |

`tools/kit/licences/python/static-libs.json` records the version, the source and the sha256 of each text. The bare `5.8.4` and `3.8.0` strings in the dylib are not labelled; python-build-standalone's own `pythonbuild/downloads.json` at the same release tag names xz 5.8.4 and libffi 3.8.0, and the three agree. The upstream files differ from python-build-standalone's copies only in the copyright years and one added copyright line; the upstream tag files are used.

### Mesa: files under other licences

The sources and headers of the shipped `libvulkan_kosmickrisp.dylib` were listed from the Mesa build that produced it (build graph and depfiles: 848 files). No file is under a GPL licence. The Linux syscall note does not come into play: the GPL-tagged Mesa headers (the `drm-uapi` kernel headers, `msm_kgsl.h`, the SVGA headers) are not part of the build.

| Files | Licence |
| --- | --- |
| 24 Vulkan and OpenGL headers under `include/` (SPDX line), `include/vulkan/vk_android_native_buffer.h`, `src/vulkan/registry/vk.xml` (Apache-2.0 OR MIT) | Apache-2.0 |
| `src/c11/impl/threads_posix.c` | BSL-1.0 |
| `include/GLES3/gl3ext.h` | SGI-B-2.0 |
| `src/util/softfloat.c`, `src/util/softfloat.h` | BSD-3-Clause, Berkeley SoftFloat 3e, text below |
| `src/util/xxhash.h` | BSD-2-Clause, xxHash, text below |
| `src/util/blake3/` (6 files, BLAKE3 1.8.2) | CC0-1.0 OR Apache-2.0 (upstream README, tag 1.8.2) |
| `src/util/u_atomic.h` | No copyright claimed |

Berkeley SoftFloat Release 3e, as in `src/util/softfloat.c`:

```
Copyright 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018 The Regents of the
University of California.  All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

 1. Redistributions of source code must retain the above copyright notice,
    this list of conditions, and the following disclaimer.

 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions, and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

 3. Neither the name of the University nor the names of its contributors
    may be used to endorse or promote products derived from this software
    without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS "AS IS", AND ANY
EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE, ARE
DISCLAIMED.  IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

xxHash, as in `src/util/xxhash.h`:

```
Copyright (C) 2012-2023 Yann Collet

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

   * Redistributions of source code must retain the above copyright
     notice, this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above
     copyright notice, this list of conditions and the following disclaimer
     in the documentation and/or other materials provided with the
     distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### Build tools

SDL3, Vulkan-Loader, Mesa, SPIRV-Tools and zstd are built or packaged on the maintainer's Mac and copied in unchanged apart from the install-name and rpath rewrite done by `tools/kit/make_kit.py`. Xcode, the Swift toolchain and Apple system frameworks are used from your Mac and are not redistributed.

## Not shipped

- Homebrew's FFmpeg build (GPL-3.0-or-later). Only a personal build on the maintainer's own Mac uses it, and it is never distributed.
- Ghidra (Apache-2.0): used for analysis during development; its scripts are not in this repository or the release zip and are not needed to build.
- ccache.
- Any file of Vampire: The Masquerade - Bloodlines or of the Unofficial Patch.

## Trademarks

Vampire: The Masquerade and Bloodlines are trademarks of their owners. This project is not affiliated with or endorsed by them.
