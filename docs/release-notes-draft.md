# Bloodlines Porter 1.0.0

First public release. Bloodlines Porter builds a native Apple silicon Mac app from your own copy of Vampire: The Masquerade - Bloodlines. The recompile happens on your Mac, from your files. This download contains no game files and no Unofficial Patch files.

I made this to play Bloodlines on my own Mac without Wine or Rosetta, and I am sharing it as it is. It plays well, but it is not finished.

## What you need

- A Mac with Apple silicon, macOS 26 or later, and the Xcode Command Line Tools.
- Your own copy of the game: Steam, English, patch 1.2. The porter checks every file and tells you which ones differ.
- Optional: the Unofficial Patch 12.0.8 Basic installer (`VTMBup120.8.exe`). You download it yourself. Other versions are not supported.

## What works

- Build from your game folder in the Porter window or on the command line, with a progress display and a game check first.
- The game plays as a self-contained `Vampire Bloodlines.app` that keeps its data and saves in `~/Library/Application Support/Vampire Bloodlines`.
- Sound, the Bink intro movies, MetalFX picture mode, FPS overlays, quick save and load.
- The Unofficial Patch fixes as native code, chosen when you build.
- Wide screens: the 3D view is Hor+, and the 2D screens are centred (see below).
- Collect Logs makes a zip of logs for a bug report. It never includes saves or game files, and nothing is uploaded.

## Known issues: wide-screen UI

The game's UI is laid out for 4:3. At wider aspects (16:9, 21:9 and up) the panels are centred with the game view at the sides, not stretched.

Fixed and checked at 21:9:
- inventory: the item column and the selection frame;
- character sheet: all boxes, the player model, and the Info tab;
- barter: both item columns, the selection frame and the money label;
- quest log;
- loading screen, centred with bars at the sides on purpose.

Still possible: other menus or labels may sit off-centre at 21:9 and wider, because not every screen was checked. Please report any you find, with a screenshot.

The community widescreenUI_mod textures (16:9 only) are not included.

## Other known issues

- Switching back to MetalFX with ⌘2 can hold the picture for about a second now and then.
- A short hitch can follow switching apps or opening and closing menus.
- Full screen on a MacBook's built-in display can judder. Play in a window there, or use an external display.
- The first 6 to 8 seconds after a level load run slower.
- Aspect and texture filtering cannot change while the game runs.
- The app is signed ad hoc and not notarized. On the first open, use System Settings, Privacy & Security, Open Anyway.

## Help wanted

I hope others will improve this. Good places to start, with the file to look in and the check to run (details in `README.md` and `CONTRIBUTING.md`):

- Wide-screen screens not yet checked at 21:9 and wider: report the aspect, window size or display, screen and a screenshot. Code in `runtime/g2b_engine.c` and `tools/m9/aspect.toml`; checks `--m9-selftest engine-ui` and `tools/recomp/test_uppatch.py`.
- The ⌘2 MetalFX hold: `runtime/fx.m` (`layout_main`, `link_next`); check `--gfx-selftest`.
- Hitches after app switches: the display link give-up in `runtime/fx.m` (`LINK_GIVE_UP_MS`, `RT_FX_LINK`); check `--gfx-selftest` with `RT_PRESENT_TRACE`.
- Full screen on built-in Mac screens: `rt_gfx_vsync` in `runtime/fx.m` and `host_interval` in `shims/d3d9_bridge.c`; check `--gfx-selftest`.

Fixes are welcome when they name the cause and show before and after.

## Not in this release

MetalFX speed-up, edge smoothing, frame generation, HDR output, and the plug-ins of the Unofficial Patch.

## Download and check

`BloodlinesPorter-1.0.0-macos-arm64.zip` and `SHA256SUMS`. In the download folder: `shasum -a 256 -c SHA256SUMS`.

## Licence

The code is MIT. Third-party components keep their own licences: see `THIRD_PARTY_NOTICES.md` and the `licenses/` folder in the download. FFmpeg is used under the LGPL 2.1 and its source is included. Vampire: The Masquerade and Bloodlines belong to their owners, and this project is not affiliated with them.

## Bugs

Use the Bug report form in Issues and attach the log zip. Please do not attach game files, the patch installer or saves.
