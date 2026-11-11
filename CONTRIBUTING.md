# Contributing

Thanks for wanting to help. The "Help wanted" list in `README.md` names good places to start.

## Build

- The porter and its tests: see `docs/developing.md`.
- The release zip, from a fresh clone: `tools/release/build.sh --with-tests`. It never needs game files and never starts the game.
- A game build needs your own copy of Bloodlines. Never commit game files, patch files, saves or anything generated from them.

## Tests

Run these before you open a pull request:

    python3 -I -B -m unittest discover -s tools/kit -p 'test_*.py'
    python3 -I -B -m unittest discover -s tools/release -p 'test_*.py'
    python3 -I -B -m unittest discover -s tools/up -p 'test_*.py'
    python3 -I -B -m unittest discover -s tools/recomp -p 'test_*.py'
    python3 -I -B tools/recomp/test_uppatch.py
    sh tools/release/check_tree.sh

`test_uppatch.py` checks every patch row in `tools/up/patches.toml` and `tools/m9/aspect.toml` against `runtime/up_fixes.c` and `runtime/g2b_engine.c`, and patches your game's DLLs when it finds them. Without the game it fails the DLL proof; `BP_NO_GAME=1` skips that part, which is what CI does. `check_tree.sh` checks shell syntax and that no e-mail address or home-folder path slipped into the tree.

## CI and releases

- CI (`.github/workflows/ci.yml`) runs the tests above with `BP_NO_GAME=1`, plus the Swift build and tests of the Porter app, on every push to `main` and every pull request. It needs no game files and no private caches.
- Pushing a `v*` tag runs the same checks and drafts a GitHub release from `docs/release-notes-draft.md` (`.github/workflows/release.yml`).
- The release zip is not built in CI: it bundles prebuilt libraries (KosmicKrisp, dxvk, SDL3, FFmpeg and others) that are built on a Mac. Build it with `tools/release/build.sh --with-tests` and upload the zip and `SHA256SUMS` to the release by hand.

With a built game, the runtime has its own self-tests. Each prints PASS and FAIL lines:

    APP="$HOME/Games/Vampire Bloodlines.app/Contents/MacOS/bloodlines-native-up"
    "$APP" --m9-selftest engine-ui --overlay /tmp/m9      # also engine-rows, engine-load, engine-hud
    "$APP" --gfx-selftest
    "$APP" --input-selftest

A change to the wide-screen rows needs `engine-ui` (or the matching case) with bad=0 and `test_uppatch.py` with 0 FAIL.

## Proven fixes only

- Name the cause: the function and the line, a trace or a log line that shows it, not only the symptom.
- Show before and after on the same machine with the same steps, and say what was not checked.
- No workarounds that hide a symptom, and no timeouts or retries in place of a cause.
- Wide-screen fixes: a shot at 21:9 and the same screen at 4:3, which must stay as it was.

## Commits

One line, conventional: `type(scope): summary`, at most 72 characters, for example `fix(ui): centre the barter money label at wide aspects`. Keep one change per commit.

## Bugs

Use the Bug report form in Issues and attach the log zip from Collect Logs. Never attach game files, the patch installer or saves.
