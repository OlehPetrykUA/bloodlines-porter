# Developing and releasing

## Layout

- `runtime/`, `shims/`, `link/`: the arm64 runtime that hosts the recompiled game and replaces the Windows DLLs it imports.
- `tools/recomp/`, `third_party/xboxrecomp/`: the static recompiler. It runs on your Mac at build time, on your own game files, and its output is never committed.
- `tools/kit/`: the porter (`kit.py`, `launcher.py`, `make_kit.py`) and the window (`tools/kit/app`, Swift).
- `tools/up/`, `runtime/up*.c`: the Unofficial Patch support, read from the installer you supply.
- `harness/`: scripted checks that need a built game. They are not run in CI.
- `docs/recomp.md`: the engineering log with measurements. `docs/STATUS.md`: what is proven.

## Tests

No test in CI needs game files or starts the game. `.github/workflows/ci.yml` runs on `macos-26`:

    python3 -I -B -m unittest discover -s tools/kit -p 'test_*.py'
    python3 -I -B -m unittest discover -s tools/release -p 'test_*.py'
    python3 -I -B -m unittest discover -s tools/up -p 'test_*.py'
    python3 -I -B -m unittest discover -s tools/recomp -p 'test_*.py'
    BP_NO_GAME=1 python3 -I -B tools/recomp/test_uppatch.py
    sh tools/release/check_tree.sh
    swift build -c release --package-path tools/kit/app
    swift test --package-path tools/kit/app

The tests need `capstone` (`pip install capstone==5.0.9`). `tools/test_vpk_list.py` and `tools/test_pe_inventory.py` read a real game folder and are run by hand.

## Release build

    tools/release/build.sh [--ref REF] [--out DIR] [--version V] [--with-tests] [--keep]

It clones this repository into a temp folder, cuts the kit from that clone, builds and ad-hoc signs `Bloodlines Porter.app` around it, runs the kit check, the host-path scan and the game-file scan, then writes `BloodlinesPorter-<version>-macos-arm64.zip` and `SHA256SUMS`. It never starts the game.

Inputs it cannot build from a clone (the non-game libraries are built on the maintainer's Mac):

- `prebuilt-spec.json` (`--spec`): the libraries to bundle (SDL3, vulkan-loader, KosmicKrisp, dxvk, LGPL FFmpeg, SPIRV-Tools, zstd) with their licence files.
- `ffmpeg-8.1.2.tar.xz` (`--ffmpeg-tarball`): checked against the pinned sha256.
- the python-build-standalone archive named in `tools/kit/python.lock` (`--python-archive`) and the wheels pinned in `tools/kit/requirements-build.txt` (`--wheels`).

### Zip layout

    BloodlinesPorter-<version>-macos-arm64.zip
      Bloodlines Porter.app/Contents/
        MacOS/Bloodlines Porter           the window (Swift)
        Resources/kit/                    the porter's Python modules, the manifests
        Resources/porting-kit/            the cut kit
          src/                            git archive of the release commit (SOURCE holds its sha)
          prebuilt/ + manifest.json       the non-game libraries
          python/                         bundled CPython with capstone, meson, ninja
          sources/ffmpeg-8.1.2.tar.xz     LGPL source offer
          licenses/, NOTICE.md, EULA.txt
          bloodlines-porter               the command-line wrapper
    SHA256SUMS

The kit leaves out `.github`. The binary inventories (`docs/inventory*`, `docs/vpk-inventory.md`, `docs/m4-probe.md`) and `tools/ghidra` are not in this repository, because they list the game's own imports and strings. `python3 tools/pe_inventory.py GAME docs` writes `docs/inventory/` from your copy of the game; `build_all.py` checks the stubs against it.

### What the zip must not contain

Game files, Unofficial Patch files, recompiled game code, a clang profile of the recompiled game, saves, logs. The build script checks the first two and the kit check rejects game extensions and magic numbers. The PGO profile (`pgo/default.profdata`) holds function addresses and execution counts of the recompiled game: `make_kit.py` ships one only when you pass `--pgo-profile`, and the release build does not. Without it the game is about 4.5 to 5 percent slower on the game thread (measured on the maintainer's Mac).

### Signing

Today the app is ad hoc signed and not notarized, and the README tells players to use Open Anyway. Developer ID signing and notarization need an Apple Developer account and are not done. The steps when it is available:

1. Sign every Mach-O inside the app, inside out (bundled python, `prebuilt/**/*.dylib`, the window binary), then the app, with `codesign --force --options runtime --timestamp --sign "Developer ID Application: <name>"`.
2. Zip it with `ditto -c -k --keepParent`, submit with `xcrun notarytool submit <zip> --keychain-profile <profile> --wait` (credentials saved once with `xcrun notarytool store-credentials`).
3. Staple with `xcrun stapler staple "Bloodlines Porter.app"`, check with `xcrun stapler validate`, zip again and write the checksum.

The game app the porter builds on the player's Mac is always ad hoc signed locally; that needs no account.

### Checksum

`SHA256SUMS` lists the zip. Players check it with `shasum -a 256 -c SHA256SUMS` in the download folder.

### Publishing with a fresh history

`tools/release/orphan.sh REF NEW-BRANCH` makes `NEW-BRANCH` a single commit holding the tree of `REF`, authored as "Bloodlines Porter <noreply@invalid>" (override with `BP_AUTHOR_NAME` and `BP_AUTHOR_EMAIL`). It touches no working tree and pushes nothing. It needs `BP_BANNED`, an extended regex of strings that must never be published (private e-mail addresses, private names), set in your shell and never committed. It refuses when the tree or the author matches it, when the branch exists, or when REF is not a commit. Push that branch, not the old history.

## Before a release

See `docs/release-checklist.md`.
