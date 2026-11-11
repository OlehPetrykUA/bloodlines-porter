# Release checklist

Release waits for the maintainer's sign-off. Nothing here is signed off.

## What the release ships

- `Bloodlines Porter.app`, with the cut kit in `Contents/Resources/porting-kit/` (layout in `docs/developing.md`).
- `src/`: a `git archive` of the commit named in `SOURCE`, without `tools/ghidra`, the binary inventories and `.github`.
- `prebuilt/`: the non-game libraries (SDL3, vulkan-loader, KosmicKrisp, dxvk, LGPL FFmpeg, SPIRV-Tools, zstd) with `manifest.json` (component, version, SPDX licence per file).
- `python/` with capstone, meson and ninja, pinned by SHA-256 (`tools/kit/requirements-build.txt`, `python.lock`).
- The hash manifests of the files the build reads (`docs/game-manifest.txt`, `tools/up/manifests/up120.8-basic.tsv`): paths, sizes and hashes only, no content.
- `NOTICE.md`, `licenses/`, `EULA.txt`, `sources/ffmpeg-8.1.2.tar.xz`, the `bloodlines-porter` wrapper and its alias `bloodlines-kit`.

## What never ships

- Game files of any kind, including `.vpk` and `.fnt`; Unofficial Patch files or its installer.
- Recompiled game code, and the clang profile of it (`pgo/default.profdata`).
- Saves, settings, logs, the overlay folder, the QA test saves.
- Homebrew's GPLv3 FFmpeg, Ghidra, ccache.

## Where things go on a player's Mac

- `~/Games/Vampire Bloodlines.app` (bundle id `local.vampire-bloodlines`, game data inside in `Contents/Resources/game`) and `~/Games/Bloodlines Porter.app` (bundle id `local.bloodlines-porter`).
- What the game makes: `~/Library/Application Support/Vampire Bloodlines`. Build work: `~/Library/Caches/Vampire Bloodlines`, deleted after a successful build.

## Open release questions

- [ ] Licence: `LICENSE` is MIT, copyright "Bloodlines Porter contributors". Confirm the licence and the copyright holder.
- [ ] Notarization: not done. The app is ad hoc signed; the README gives the Open Anyway steps.
- [ ] The bundle identifiers (today `local.*`).
- [ ] The mpdecimal text in `tools/kit/licences/python/mpdecimal/` comes from the 4.0.1 package; compare it with the 4.0.0 tarball. OpenSSL, expat, bzip2, libffi, liblzma and SQLite texts are in, each from the upstream tag of its version.
- [ ] The judgement calls of the legal scan (byte patterns in `tools/up/patches.toml` and `tools/m9/aspect.toml`, hash manifests, `docs/recomp.md`, the PGO profile).
- [ ] The history: every commit carries the maintainer's work e-mail address. Publish with fresh history (`tools/release/orphan.sh`) or rewritten author data.
- [ ] The play-test defects listed in `README.md` under Known issues are fixed and re-tested (3 of 3 clean runs for a crash).
- [ ] Sign-off.

## Checks before a release

- [ ] `git ls-files '*.vpk' '*.fnt'` prints nothing; no QA test save is in git or in the kit.
- [ ] `tools/release/build.sh --with-tests` passes from a fresh clone: kit check (no game-data scan hit, no host path), host-path scan of the app, `codesign --verify --strict --deep`.
- [ ] `shasum -a 256 -c SHA256SUMS` passes on the zip.
- [ ] CI is green on the release commit.
- [ ] No builder home-folder path in the docs or in the FFmpeg dylibs.

## LGPL FFmpeg duties

- [ ] The source tarball `sources/ffmpeg-8.1.2.tar.xz` is in the kit (sha256 checked by `make_kit.py`) and hosted beside the download.
- [ ] The notice file `Resources/LGPL-FFMPEG-NOTICE.txt` gives the source URL, sha256, configure line and "Changes to the FFmpeg source: none".
- [ ] The FFmpeg libraries stay separate dylibs in `Frameworks/`, so they can be replaced.
- [ ] `licenses/ffmpeg/COPYING.LGPLv2.1` is present; `EULA.txt` states the use of FFmpeg under the LGPLv2.1.
- [ ] The build is recorded in `tools/ffmpeg/LGPL-BUILD.md` (`build_lgpl.sh`).
