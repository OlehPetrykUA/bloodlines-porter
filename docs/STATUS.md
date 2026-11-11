# Status

## Scope

2026-10-29: scope cut by the maintainer: finish the game, skip what the game never had. The game is not finished: the play test of 2026-10-29 to 10-30 contradicts the earlier "shipped" wording.

- Built (code exists, tests pass): the native app from your own 1.2 copy; M7 sound and video; the M8 Unofficial Patch fixes as native hooks; the porter (`bloodlines-porter doctor`, `build --game`, `check`, the `launcher.py` CLI, Bloodlines Porter.app, LGPL FFmpeg, no Ghidra); the self-contained `Vampire Bloodlines.app` design (game data inside, patch chosen at build time, saves and settings in Application Support).
- Proven by the play test: a patched build starts, reaches the opening cutscene and writes saves (`files/Unofficial_Patch/save`, `sp_genesisdevice_1`).
- Proven on the packaged `Vampire Bloodlines.app` (2026-11-06, scripted runs in scratch overlays): New Game to the theatre 6 of 6 (MetalFX on and off, empty caches), Space through the opening cutscene 3 of 3, first load of `la_hub_1` 4 of 4 (dxvk waits up to 120 s for the GPU), cutscene wedges 0 of 72 shots in 6 runs, Jack's first conversation, quick save and load, death and reload, Options tabs, a window close that writes `cfg/config.cfg`. Self-tests: gfx, input, Unofficial Patch, audio, guest registers under preemption.
- Measured 2026-11-09 on the placed app (PGO build). Ultrawide 3440x1440 full screen with MetalFX: 60.0 FPS, 98.7 to 99.4 % of frames on the 60 Hz beat, 1 % low 60. The ⌘-Tab return has no wait, and a scripted gameplay pass (dialogue, inventory, sheet, quest log, hits, save, load, area change) finishes with 0 stalls. A window on the built-in at its default 1496x967 runs 60.0 FPS, 99.8 % on the beat.
- Measured 2026-11-09 evening on the placed app (master e6b0d53, built-in only, 21:9 full screen with MetalFX): 99.0 % of frames on the 60 Hz beat, 0 stalls; voice lines play (MP3 decoders no longer stop at 64, `--audio-selftest` holds 400 open); the loading screen is a centred 4:3 panel at wide aspects; the open-file limit is raised to 10240 on a Finder launch. The character sheet, quest log and barter keep the stock left-anchored layout at wide aspects (a centring attempt moved the frame but not the boxes inside, so it was taken out).
- Open: full screen on the built-in at its default 1496x967 mode judders and can stall 1 s (42 to 60 FPS, 45 to 82 % on the beat). FIFO and a MetalFX-layer link were both measured worse. Also open: game-thread CPU in the 6 to 8 s after a level load, the theatre cutscene and the pier below 60 FPS (game code), a CPU hitch of the game's dialogue code after a pick, and sound gaps on game stalls longer than about 0.3 s.
- In-game controls: ⌘1 Overlays (all, FPS only, off) and ⌘2 Picture mode (MetalFX, off). Resolution, texture quality, HDR lighting, gamma and subtitles are in the game's own Options; Options > Video applies at the next launch. Aspect is set at launch (`launcher.py set --aspect`, Porter Options or `settings.txt`).
- Defaults like the original game: 4:3 window fitted to the screen, overlays off, extras off, subtitles on.
- Known issues: at wide aspects the 2D screens are centred, and screens other than the inventory, character sheet, barter, quest log and loading screen are not yet checked; the ⌘2 MetalFX hold; hitches after app switches; aspect and texture filtering cannot change live; full screen on the built-in at its default resolution judders (play in a window there, or on an external screen).
- Later: MetalFX speed-up, edge smoothing, frame generation, HDR output, C1f Unofficial Patch plug-ins.
- Removed as not in the original game: Frame interpolation, Edge smoothing and HDR toggles, Texture filtering and Controller type items, the runtime Aspect item (⌘4), Developer info (⌘8) and the FPS menu-bar item. Old `settings.txt` keys for them are ignored.
- QA test saves, never in git, loaded in scratch overlays: `~/Library/Caches/BloodlinesPorter/dev/build/test-saves/after-first-cutscene/` (autosave in the tutorial) and `…/past-jack/` (quick save after Jack's first conversation).
- Docs: `README.md`, `docs/release-checklist.md`, `docs/recomp.md`. Release waits for the maintainer's sign-off.

## Game copy

- Game: Vampire: The Masquerade - Bloodlines (2004, Windows, Source engine)
- Source: own Steam copy, read-only, at `~/Games/Vampire Bloodlines (Windows)`, with the Unofficial Patch installer beside it.

## Where things live

- Game and installer: see Game copy. The built game is `~/Games/Vampire Bloodlines.app` (game data inside); what it makes is in `~/Library/Application Support/Vampire Bloodlines`. The older `~/Games/Bloodlines Native.app` and `~/Library/Application Support/BloodlinesNative` come from the previous design and are not touched until the new app is proven.
- Dev builds, dxvk prefix, venv, vendored sources, FFmpeg, UP roots: `~/Library/Caches/BloodlinesPorter/dev/` (`build`, `dxvk/inst`, `venv`, `vendor`, `ffmpeg`, `up12-basic`). Base override: `BN_DEV_HOME`; dxvk library override: `RT_DXVK_LIB`. Table in `docs/recomp.md`.
- Ghidra projects and the reference folder are not needed to build; `BN_GHIDRA_PROJECT` and `BN_REFDIR` point at them when wanted.
- Size: 2.8 GB
- Patch: official 1.2
- Steam buildid: 242386
- Integrity: `docs/game-manifest.txt` (SHA-256 of every file)

## Coverage

| DLL | game functions | ported | % |
|-----|----------------|--------|---|

Numbers come from the scouting run.
