# Bloodlines Porter

Bloodlines Porter turns your own installed copy of Vampire: The Masquerade - Bloodlines into a native Apple silicon Mac app. It does this on your Mac, by recompiling the game's code at build time. Nothing from the game is in this repository or in the download.

- You supply the game (Steam, English, patch 1.2) and, if you want it, the Unofficial Patch installer.
- The porter only reads your game folder. It never changes it and never uploads anything.
- No Wine, CrossOver or Rosetta: the result is an arm64 app that draws through Vulkan on Metal.

This is an unofficial fan tool, not affiliated with or endorsed by the owners of Vampire: The Masquerade or Bloodlines. It is early software. Read "Known issues" first.

## What you need

- A Mac with Apple silicon, macOS 26 or later.
- Xcode Command Line Tools (`xcode-select --install`).
- About 3 GB free for the app, more while it builds.
- Your own Bloodlines copy: the Steam build, English, patch 1.2. The porter checks every file against a list of hashes and names the ones that differ.
- Optional: the Unofficial Patch 12.0.8 Basic installer, `VTMBup120.8.exe`, which you download yourself. Put it in the game folder and the porter finds it, or point to it with `--up-installer`. The porter checks its MD5, extracts what it needs on your Mac and never copies the installer into the app. Other patch versions are not supported.

## Install and first run

1. Download the release zip and unzip it. Open `Bloodlines Porter.app`. The app is signed ad hoc and not notarized, so macOS may refuse the first open: open System Settings, Privacy & Security, scroll to Security, click Open Anyway and confirm ([Apple Support 102445](https://support.apple.com/en-us/102445)).
2. Choose game folder, then Verify. Fix anything it lists as missing or changed.
3. Turn the Unofficial Patch on in Options if you want it, and choose its installer. The patch is applied at build time: to switch it later, build again.
4. Build. The porter recompiles the game, packages `~/Games/Vampire Bloodlines.app` with the game data inside, and installs it. It takes a while.
5. Play: open `~/Games/Vampire Bloodlines.app`. Your original game folder is not needed afterwards.

Command line instead of the window: `Bloodlines Porter.app/Contents/Resources/porting-kit/bloodlines-porter` with `doctor`, `check` and `build` (see `tools/kit/CLI.md`).

## Where things live

- `~/Games/Vampire Bloodlines.app`: the game.
- `~/Library/Application Support/Vampire Bloodlines`: everything the game writes: settings, saves (and a backup of them), logs and the shader cache.
- `~/Library/Caches/Vampire Bloodlines`: build work, deleted after a successful build.

Uninstall from the Porter window. The command-line form `uninstall` lists what it would remove; `--yes` removes the app and caches, `--remove-data` also your saves and settings. It never touches your original game folder.

## Playing

- Window: 4:3 fitted to the screen by default, like the original. It is meant to be resizable with black bars at the chosen aspect, and full screen is the green button or ⌃⌘F; window handling is still being tested.
- Game menu: ⌘1 Overlays (all, FPS only, off), ⌘2 Picture mode (MetalFX, off). Resolution, texture quality, gamma and subtitles are in the game's own Options; video changes apply at the next launch.
- Aspect (16:9, 21:9, fit or any `W:H`) is set in the Porter Options or with `aspect=` in `settings.txt` and applies at the next launch.
- Settings live in `~/Library/Application Support/Vampire Bloodlines/settings.txt`, one `name=value` per line.

## Known issues

Played from New Game through character creation, the opening cutscene and the first conversation into the tutorial, with quick save and load, death and reload, and a clean quit. It is not signed off as finished. What is still open:

- Full screen on a MacBook's built-in display at its default resolution judders and can stall for a second. In a window there, or full screen on an external display, it runs smoothly.
- Wide-screen UI: the game's UI is laid out for 4:3, so at wider aspects the 2D screens are centred, not stretched. Inventory, character sheet (with the Info tab), barter, quest log and the loading screen are checked at 21:9. Other menus or labels may still sit off-centre at 21:9 and wider. The community widescreenUI_mod textures (16:9 only) are not included.
- Switching back to MetalFX with ⌘2 can hold the picture for about a second now and then.
- A short hitch can follow switching apps or opening and closing menus.
- After every level load the first 6 to 8 seconds run slower, and the theatre cutscene and the pier run below 60 FPS (game code).
- A short hitch after picking a dialogue line, and sound gaps when the game stalls for longer than about 0.3 seconds.
- Aspect and texture filtering cannot change while the game runs.

Not included, planned later: MetalFX speed-up, edge smoothing, frame generation, HDR output, and the plug-ins of the Unofficial Patch.

`docs/STATUS.md` lists what has been proven and how.

## Report a bug

Open an issue in this repository and pick "Bug report". Include your macOS version, Mac model, display, game build, whether the Unofficial Patch is on, and the log zip. Make the zip in the Porter window with Collect Logs, or in Terminal:

    "$HOME/Games/Vampire Bloodlines.app/Contents/MacOS/Vampire Bloodlines" collect-logs

It writes a zip of run logs, crash reports and the build record to your Desktop and prints its path. It never contains saves or game files, and nothing is uploaded. Look inside before you attach it.

Do not attach game files, the patch installer or saves.

## Help wanted

These are open, and each one is a good place to start. `CONTRIBUTING.md` says how to build, test and send a fix.

- **Wide-screen screens not yet checked at 21:9 and wider.** To report one, open an issue with the aspect (`aspect=` in `settings.txt`), the window size or display, the screen's name, and a screenshot, plus the same screen at 4:3 if you can. Where to look: `runtime/g2b_engine.c` (`k_rows` and the `UiImage`, `UiTextX` and `UiFrame` shims) and `tools/m9/aspect.toml` (one row per patched call, with its disassembly note). The usual cause is a Paint function that takes the panel's parent-relative position and draws at raw screen coordinates. Checks: `--m9-selftest engine-ui` and `tools/recomp/test_uppatch.py`.
- **⌘2 back into MetalFX holds about a second now and then.** Where to look: `layout_main`, `link_stop` and `link_next` in `runtime/fx.m`, and "Present pacing" in `docs/recomp.md`. Trace it with `RT_PRESENT_TRACE=FILE`. Check: `--gfx-selftest`.
- **Hitches after app switches.** The display link on the covered game layer can stop sending updates after the app is paused. The runtime gives up after 150 ms (`LINK_GIVE_UP_MS` in `runtime/fx.m`) and restarts it. Putting the link on the MetalFX layer (`RT_FX_LINK=1`) removes the hitches but loses smooth pacing. Wanted: a placement that keeps both. Check: `--gfx-selftest` with `RT_PRESENT_TRACE`.
- **Full screen on built-in Mac screens judders.** Where to look: `rt_gfx_vsync` and `fx_link_wanted` in `runtime/fx.m`, and `host_interval` in `shims/d3d9_bridge.c`. The numbers per display mode are in `docs/recomp.md`. Check: `--gfx-selftest`.

## For developers

- How to contribute: `CONTRIBUTING.md`.
- Build and release: `docs/developing.md`.
- How the recompiler and runtime work, with measurements: `docs/recomp.md`.
- Third-party components and their licences: `THIRD_PARTY_NOTICES.md`. The licence of this project is MIT (`LICENSE`).
