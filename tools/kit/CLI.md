# Porter CLI for the Mac app

The control table, states and build exit codes are in the spec (K4: the Mac app). This file only lists what the CLI adds or fixes.

`launcher.py <command> [--home H] [--overlay O] [--bundle A]`; the build is `kit.py build --game G [--unofficial-patch on|off] [--up-installer F] [--work W] [--app A] [--overlay O] [--progress]`. The `bloodlines-porter` wrapper runs `kit.py`; `bloodlines-kit` is an alias for it.

| Command | stdout | stderr | Exit |
| --- | --- | --- | --- |
| `session-start` | nothing | nothing | 0 |
| `status` | `state: <no-game, check-failed - <failures>, running - ..., last-run-failed - <why>, ready>`, `subtitles:`, `aspect:`, `unofficial patch: on` and `unofficial patch build: sha256 <hash>` (or `unknown (<why>)`) when the app carries the patched executable, else `unofficial patch: off`; `personal build (GPLv3 FFmpeg)` in a personal build | nothing | 0 |
| `check [--game G] [--progress]` | `game ok: ...` or `game check failed: n of m files` plus `  missing:/changed:/unreadable:` lines | `no game folder` | 0, 6 failed, 2 no folder |
| `run`, `status`, `set`, `check`, `session-start`, on a first start with an old data folder | nothing | `could not move your saves from <old>: ...; your old folder is unchanged` | 8; nothing starts and the next start retries. `run --alert` also shows it in a dialog |
| `set --mode/--sound/--intros/--subtitles/--aspect/--display/--extra/--game` | the settings JSON | `invalid value for <key>: ...` | 0, 2 |
| `set` (no options) | the settings JSON; the settings read: it never writes `launcher.json` | | 0 |
| `set --aspect default` | settings JSON with `"aspect": null` (engine default) | | 0 |
| `run --bundle A` | nothing (the game's output goes to logs/launcher.log); `already running: nothing started` | `cannot start the game: ...`, `<app> holds no game data (Contents/Resources/game). Build it again with Bloodlines Porter.app.`, `no app bundle: pass --bundle ...` | the game's exit code, 2 refused, 127 cannot start |
| `collect-logs [--work W] [--out-dir D]` | last line is the zip path | | 0 |
| `uninstall [--dry-run] [--yes] [--remove-data] [--work W]` | one absolute path per line: the paths it would remove / removed | `the game is running ...`, `refusing to remove ...` | 0, 2 |

`aspect` is kept only in `<overlay>/settings.txt` (`aspect=`; `4:3`, which the runtime writes, reads as default; a stored `WxH` is valid). `set --aspect X` edits that one line, `default` removes it, an old `aspect` in `launcher.json` moves there once, and `run` never passes `--aspect`. `run` passes `+cl_captions 1` (subtitles on) only while `<overlay>/files/<Vampire or Unofficial_Patch>/cfg/config.cfg` does not exist yet, so the game's own Options choice sticks after the first run.

`set --game` stores the game folder the porter builds from. The game itself plays from `Contents/Resources/game` inside the app, so `run` never reads the stored folder. The Unofficial Patch is not a launcher setting: it is chosen when the app is built (`kit.py build --unofficial-patch on`), and `status` reads it from the app.

The default app is `~/Games/Vampire Bloodlines.app` (`kit.py build --app`). Bloodlines Porter.app passes `--bundle` itself. Bloodlines Porter.app carries the kit in `Contents/Resources/porting-kit`, so its CLI is `Bloodlines Porter.app/Contents/Resources/porting-kit/src/tools/kit/launcher.py`.

Overlay (the game's data, `--overlay`): `~/Library/Application Support/Vampire Bloodlines`. `run`, `status`, `set` and `session-start` without `--overlay` first copy `settings.txt`, `launcher.json`, `registry.reg`, `files/` and `cache/dxvk` from `~/Library/Application Support/BloodlinesNative` when that folder exists and the new one holds nothing but `launcher.lock`; they never carry `up/` or `logs/` and never change the old folder. Before the first run of a new executable the launcher copies the saves (`files/Vampire/SAVE` and `files/Unofficial_Patch/save`) to `backups/SAVE-<time>/Vampire/SAVE` and `backups/SAVE-<time>/Unofficial_Patch/save`.

`uninstall` without `--yes` is a dry run. The default list is the app (`--bundle`), `<app folder>/.staging` and `<app>.replaced` when present, the work folder (default `~/Library/Caches/Vampire Bloodlines`) and `<overlay>/cache/dxvk`. `--remove-data` adds the overlay folder. `--yes` is refused (exit 2, nothing removed) while the game runs; any path that is, holds or lies inside the game folder or a `Bloodlines Porter.app`, or is or holds your home folder, or holds the overlay (or, without `--remove-data`, is it), or holds a `Bloodlines Porter.app` at any depth, makes it refuse (exit 2, nothing removed, also for a dry run). A path that is or holds the running launcher is refused the same way with `this launcher runs from inside it`: the game app's own `uninstall` always hits this, so run uninstall from Bloodlines Porter (or the kit's `src/tools/kit/launcher.py`). An error while removing is exit 2 with a message on stderr.

## kit.py build

`--unofficial-patch on` needs the `VTMBup*.exe` installer: `--up-installer F`, else the one installer inside the game folder (none or several is exit 7 with the reason). The installer must be one the porter knows (`up.py` `KNOWN`, MD5 checked, exit 7 `unknown installer: MD5 <found> found, ...`). With the patch on the build extracts the installer into `<work>/up-data`, builds the patched game root there, runs a second recompile with `--up-ini` and `--patch-set tools/up/patches.toml`, packages with `--up-build <work>/up`, and copies the patched root into the app as its game data. A failed patch build exits 1 to 4 and leaves the installed app alone. Without the patch the app's game data is a clone of the game folder.

## --progress

`check --progress` and `kit.py build --progress` print only JSON, one object per line:

- Step: `{"step", "n", "of", "fraction", "elapsed"}`, plus `"done"` and `"total"` while the step counts items. `fraction` never decreases and is 1 on the last step line of a passing run. A line is repeated at least every second while a step runs (ticks every 0.5 s).
- Log: `{"log": "<text>"}` carries the human lines (`game ok: ...`, `  changed: <file>`, `unofficial patch installer: <name> MD5 <md5> ok`, `extracting ...`, `game data: ...`, `installed <app>`).
- Last: `{"result": "pass"|"fail", "exit": <code>, "message": "<text>"}`; `exit` equals the process exit code. For exit 2 of `check` the message is also on stderr. An unexpected error ends with `"result": "fail", "exit": 1`.

Steps: `check` has `verify` (1 of 1). The build has `doctor`, `check`, `recompile`, `package`, `game-data`, `install` (n of 6), and `doctor`, `check`, `unofficial-patch`, `recompile`, `recompile-patch`, `package`, `game-data`, `install` (n of 8) with `--unofficial-patch on`. `recompile` has no `done`/`total` and its fraction moves with elapsed time against a fixed estimate. Build exits: 0, 1 to 4 from the recompile steps, 1 package or game data, 7 Unofficial Patch refused (unknown, missing or several installers, or the patch game root failed), 5 doctor, 6 game check. A successful build removes the work folder.
