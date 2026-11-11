# Static recompilation: vstdlib.dll to native arm64

`Bin/vstdlib.dll` (32-bit x86, MSVC 6.0, SHA-256 `75778ca7…9825f32`) is translated to C automatically, compiled with clang into `libvstdlib.dylib` (arm64), and every tested export is compared with the original x86 code running under Unicorn.

## Where things live

`GAME` holds the game (the files directly in it, read-only; `BN_GAME`, else `$HOME/Games/Vampire Bloodlines (Windows)` in `tools/devpaths.py`) and the installer `VTMBup120.8.exe` (patch 12.0.8). Everything for development lives under `DEV=~/Library/Caches/BloodlinesPorter/dev` (not under `~/Library/Caches/Vampire Bloodlines`, which the porter empties after every install) (override the base with `BN_DEV_HOME`; the paths are defined once in `tools/devpaths.py`):

| What | Where | Override |
|---|---|---|
| Dev builds | `$DEV/build/<name>` (default build `all`) | `--out`, `--exe` |
| Game folder | `$GAME` | `BN_GAME`, `--game` |
| dxvk prefix | `$DEV/dxvk/inst` | `RT_DXVK_LIB` |
| Harness Python (capstone) | `$DEV/venv` | |
| Vendored sources (dxvk, xboxrecomp) | `$DEV/vendor` | |
| FFmpeg source and LGPL output | `$DEV/ffmpeg/src`, `$DEV/ffmpeg/lgpl` | script arguments |
| UP roots for the harness | `$DEV/up12-basic`, `$DEV/up12-basic-gu` | `--up`, `--game-root` |
| Reference captures and menu files | none (current directory) | `BN_REFDIR`, `--refdir` |
| Ghidra names export | none; skipped | `BN_GHIDRA_PROJECT` |

Run the commands below from the repo root. The runtime has no built-in game folder: `--run` takes `--game DIR`, else `BN_GAME`, and refuses without either (the harness sets `BN_GAME` from `tools/devpaths.py`); the self-tests fall back to the current directory.

## Rebuild command

```sh
$DEV/venv/bin/python -I tools/recomp/build.py \
    "$GAME/Bin/vstdlib.dll" $DEV/build/vstdlib
```

Both arguments are the defaults. The command:

1. Exports Ghidra's function names once if `ghidra-names.tsv` is missing and `BN_GHIDRA_PROJECT` names a Ghidra project folder; without it this step is skipped and the labels stay empty (nothing else needs Ghidra). It opens `$BN_GHIDRA_PROJECT/bloodlines` with `-process vstdlib.dll -noanalysis -readOnly` and runs `tools/recomp/ExportNames.java`, using `/opt/homebrew/opt/ghidra/libexec/support/analyzeHeadless`.
2. Runs `tools/recomp/test_discover.py`, a self-check of the table and code-write scan and of the `ebp` strip. Then it translates with `layout.toml` into `gen/`.
3. Runs `meson setup` (first time only), then `ninja`.
4. Records the Unicorn oracle (`harness/run.py record`), compares the native library against it (`check`), runs the abort cases (`aborts`), and runs the shim unit checks (`shims`).
5. Repeats steps 2–4 with `harness/layout-moved.toml` (vstdlib at `0x14000000`) under `moved/`.

It prints `ALL PASS` and exits 0 only when every step passes. A clean run takes about 60 s (the Ghidra export is about 30 s of that). A rerun with no change takes about 30 s, almost all of it in the oracle.

Everything generated goes under `$DEV/build/vstdlib/`:
- `gen/` (generated C, dispatch table, `module.c` with the relocated image);
- `meson/` (objects and `libvstdlib.dylib`);
- `report.json` (translation report), `oracle.json` and its `.log`, `native-results.json`, `extra-entries.txt`;
- `moved/`, the same set for the moved layout.

## Tools (M0)

| Tool | Version | Source |
|---|---|---|
| meson | 1.12.1 | Homebrew (`brew info meson`) |
| ninja | 1.13.2 | Homebrew |
| ccache | 4.14.2 | Homebrew. Meson uses it automatically when it is installed (Meson docs, "Feature autodetection › Ccache"); the meson log shows `ccache cc` |
| Python | 3.13.14 (Homebrew), venv `$DEV/venv` | |
| unicorn | 2.1.4 (pip) | Runs natively on this arm64 Mac: 166,193 oracle runs per build |
| capstone | 5.0.9 (pip; pinned in `tools/kit/requirements-build.txt`) | |
| xboxrecomp | `f0f8a461496a4f23d9b0aeaa284b8d70ba7ab82b` (v0.16.0, 2026-10-20), cloned to `$DEV/vendor/xboxrecomp` | https://github.com/sp00nznet/xboxrecomp |

## Fork decision: fork the lifter, write our own front end

The project forks the lifter rather than writing its own.

**What was taken.** These files are copied unmodified into `third_party/xboxrecomp/`, with `LICENSE` (MIT) and `SOURCE` (URL and commit):
- `tools/recomp/{__init__,config,disasm,lifter,translator}.py`
- `tools/recomp/data/win32_api_names.txt`
- `templates/runtime/recomp_types.h`

**What was left out.**
- The LGPL-2.1-or-later sources (the APU and nv2a files under `src/`, listed in `NOTICE`) are not copied.
- `tools/disasm` and `tools/conformance` are not copied either. Their loader is XBE-only (`tools/disasm/loader.py`), and conformance runs MSVC under Wine in Docker (`docs/technical/conformance-testing.md`).

**Why a fork.**
- The translator is a capstone-based x86→C lifter that is already mature. Its README reports Half-Life 2 regenerated with 51,114 functions (`README.md` line 625), and it covers flags, x87, string instructions, jump tables, SEH helpers and setjmp/longjmp.
- Its only XBE coupling is the section table. `config._install()` takes any section list, and `BatchTranslator` reads a flat image plus a `functions.json`.
- On vstdlib it lifted every reachable instruction. The only unimplemented mnemonic left in live code is `int 6`.

**Our own code.**
- `tools/recomp/pe.py`: the PE32 parser (sections, exports, imports, base relocations, rebasing).
- `tools/recomp/discover.py`: relocation-seeded function discovery.
- `tools/recomp/translate.py`: feeds the lifter, binds imports, patches `ebp` and writes the report and `module.c`.
- `runtime/`, `shims/` and `harness/`.

**Two adaptations, without editing the third-party files.**
- **Every general register crosses calls.** xboxrecomp keeps `ebp` as a per-function C local (`docs/technical/register-model.md`, "ebp: The Exception"). `translate.py` wraps the translator's `write_if_changed` and removes only the statements `uint32_t ebp;` and `uint32_t ebp = 0;` (the two declaration forms the translator emits), `ebp = g_ebp;`, `g_ebp = ebp;` and `ebp = g_seh_ebp;` (the rest of each line stays) before the chunk is written. If any local `ebp` declaration survives, translation stops with `local ebp survived in <file>`. `runtime/gen_prefix.h` then maps `ebp` to the global `g_ebp`.
- **Intrinsics** use the translator's own `manual` mechanism: direct calls go through `recomp_lookup_manual`.

The fallback (a pypcode emitter) was not needed.

## Pipeline pieces

**PE front end and discovery (`pe.py`, `discover.py`).** Seeds are:
- the entry point;
- the code exports;
- every base-relocation target in an executable section (222 unique);
- recursive direct-call targets;
- direct jumps that leave a function's range (tail jumps).

Each function is the contiguous range from its start to the next start, walked recursively. Jump tables are followed through relocated slots. A relocation target counts as a function start unless one of these holds:
- its slot lies inside `.text` but in no decoded instruction (a jump-table entry);
- its slot is a 32-bit memory displacement (a data table in code).

A function whose first instruction is `jmp rel32` is marked `ilt-thunk`.

Two scans run over every decoded body (`discover.scan`):
- An indexed `jmp [reg*4+table]` whose table has no relocated slot near it is an unbounded jump table. It is listed in `report.json` under `unbounded_jump_tables`, and the owning function is marked skipped.
- A store to an absolute address inside an executable section is a write into code. It is listed under `writes_into_code`.

vstdlib has none of either. `tools/recomp/test_discover.py` proves both scans on a synthetic image. Relocations are applied at translate time: `PE.mapped(base)` adds the delta to all 1,498 HIGHLOW fixups.

**Layout (`layout.toml`).** vstdlib is at `0x10000000`. The `tier0` data import cell `g_DLLChainRoot` lives at the `data_cells` address. The IAT is bound at translate time: each function import gets a shim address `0xFE000000 + 16·i`, and a data import (mangled `@@3`) gets its cell.

**Runtime (`runtime/rt.c`).**
- **Guest arena:** 4 GiB `mmap(MAP_ANON|MAP_PRIVATE)` per world, addressed as base + u32 (`XBOX_PTR` in `recomp_types.h`). The low guard is one host page (`getpagesize()`, `man 3 getpagesize`), made `PROT_NONE`. The host page is 16 KiB, so guest `0x0000–0x3FFF` faults. A further 64 KiB past the 4 GiB is mapped `PROT_NONE`, so an access that runs past `0xFFFFFFFF` faults too.
- **Faults:** a `SIGSEGV`/`SIGBUS` handler (`SA_SIGINFO`, `si_addr`; `man 2 sigaction`) maps the fault back to a guest address, prints it and calls `_exit(4)`, so no crash report is written.
- **Indirect calls:** they go through the generated table (`recomp_lookup`), then the shim table (`recomp_lookup_kernel`). A miss counts, appends `0xADDR` to `extra-entries.txt` and exits 3. Translation reads that file back as extra seeds (`--extra-entries`).
- **x87:** C doubles, from `recomp_types.h`.
  - `g_fp_control_word` starts at `0x027F` for every call.
  - Integer stores honour RC (`recomp_fist`).
  - The header also rounds to 24 bits if a guest selects single precision (PC=00). That is never the case here.
- **`_ftol`:** an intrinsic (`rt_intrinsic_ftol`: pop ST0, truncate, edx:eax).
- **`_chkstk`:** an intrinsic too (`rt_intrinsic_chkstk`).
- **`_stricmp` in engine.dll:** an intrinsic (`rt_intrinsic_stricmp`), row `engine 0x172C30` in `tools/recomp/intrinsics.tsv`, 140 bytes pinned by sha256 (the same in the shipped and the patched engine.dll). The engine's model loader calls it in loops, also through function pointers (`recomp_lookup_manual`, which picks an intrinsic by the first letter of its name); recompiled byte by byte it took 16 % of the game thread in an opening-cutscene spawn stall. The host loop does what the CRT does: equal bytes go on, a mismatch folds A to Z to lower case when the CRT locale cell (`[0x2138EEA4 + 8]`, read from the pinned `lea`) is 0, otherwise it calls the module's own `tolower` (`0x201650B2`, read from the pinned `call`), and it returns -1, 0 or 1 with the return address popped. `--up-selftest` checks the decoded addresses, 12 fixed cases and 3000 random pairs against a separate reference.
- **setjmp/longjmp:** lowered by the lifter at the call site and served by `recomp_setjmp_slot` / `recomp_guest_longjmp`.
- **Binding intrinsics:** they are bound by the Ghidra (Function ID) name `__ftol` / `__chkstk` / `__alloca_probe` / `_stricmp` (`INTRINSICS` in `tools/recomp/translate.py`).
- **No SIMD:**
  - `xbox_Cpuid` leaf 1 reports a Pentium-class CPU with FPU, TSC and CMOV only: EDX bits 23/25/26 and ECX bit 0 are 0.
  - The `IsProcessorFeaturePresent` shim returns 0. PF_ values come from Microsoft Learn (https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-isprocessorfeaturepresent).
- **Untranslated instructions** reach `recomp_unimpl`, which exits 8 naming the instruction and address.

**Shims (`shims/`).** These are stdcall `kernel32` and cdecl `tier0` functions. They read their arguments from the guest stack, set `eax`, and pop their arguments.
- **Heap:** a bump allocator. Its bound check cannot wrap, and running out returns NULL.
- **HeapAlloc:** accepts `HEAP_NO_SERIALIZE` and `HEAP_ZERO_MEMORY`.
- **HeapReAlloc:** also accepts `HEAP_REALLOC_IN_PLACE_ONLY` (0x10). It shrinks in place, and a grow fails with NULL, leaving the block unchanged. Any other flag aborts by name. On failure it does not set the last error, because the Microsoft Learn page says HeapReAlloc "does not call SetLastError" (https://learn.microsoft.com/en-us/windows/win32/api/heapapi/nf-heapapi-heaprealloc).
- **VirtualAlloc:** reserves 64 KiB-aligned regions. It checks the size against the free range before rounding, and fails with NULL and error 8 (87 for size 0). A commit inside a reservation rounds down to the 4 KiB page, per https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualalloc.
- **tier0:** all 7 functions are implemented: `Plat_Alloc`, `Plat_Free`, `Plat_Realloc`, `DevMsg`, `AssertValidStringPtr`, `_AssertValidReadPtr` and `_AssertValidWritePtr`. The `g_DLLChainRoot` cell makes 8 imports.
- **kernel32, implemented (34 of 50):** CRT start-up, heap, `VirtualAlloc`, environment, std handles, code pages, and cp1252 `GetStringTypeA`/`LCMapStringA`.
  - CT_CTYPE1 bits come from https://learn.microsoft.com/en-us/windows/win32/api/stringapiset/nf-stringapiset-getstringtypew.
  - LCMapString semantics come from https://learn.microsoft.com/en-us/windows/win32/api/winnls/nf-winnls-lcmapstringw.
- **IsProcessorFeaturePresent** is not a vstdlib import. The shim exists for later DLLs, and AC14 proves it by calling it directly through `rt_shim_call`.
- **kernel32, unicode variants:** `GetEnvironmentStringsW`, `GetStringTypeW` and `LCMapStringW` fail with error 120 (call not implemented), as on Windows 9x. The MSVC 6 CRT then takes its ANSI paths.
- **kernel32, not implemented (16):** these abort with `unimplemented shim KERNEL32.dll!<name> called from guest 0x…` and exit 7. None is reached by DllMain or the tested exports.
  - CloseHandle, CreateFileA, FlushFileBuffers, GetEnvironmentVariableA
  - GetProcAddress, LoadLibraryA, MultiByteToWideChar, RaiseException
  - ReadFile, RtlUnwind, SetEndOfFile, SetFilePointer
  - SetStdHandle, UnhandledExceptionFilter, WideCharToMultiByte, WriteFile

**Build (`runtime/meson.build`).**
- The generated chunks (40 functions each, 11 files) come from `gen/chunks.txt` (`fs.read`, Meson docs "Fs module › read").
- They are compiled into a static library with `-include gen_prefix.h`, then linked whole into the shared library with the runtime and shims.
- Flags: `-ffp-contract=off` (no FMA fusion; the clang default is `on`), `-fwrapv` and `-fno-strict-aliasing`. Source: clang Users Manual, https://clang.llvm.org/docs/UsersManual.html.
- The translator writes every file with `write_if_changed`.

**Oracle (`harness/run.py`, test-only).**
- **Process and memory:** Unicorn runs in the harness process. It maps the oracle world's own arena with `mem_map_ptr` (guest `0x4000–0x1FF00000` and the shim page), so the same native shims serve both worlds.
- **Shim calls:** a code hook on the shim page copies the registers to the runtime, calls the shim and sets EIP to the return address.
- **FS:** set through a GDT, following Unicorn's `samples/sample_x86_32_gdt_and_seg_regs.c` (CS 0x73, SS 0x88, DS/ES 0x7B, FS 0x83 based at the TIB). `UC_X86_REG_FS_BASE` is a no-op in 32-bit mode in 2.1.4: a test read it back as 0, and a write printed Unicorn's "deprecated" warning.
- **Before every run:** FPSW is set to 0 and FPTAG to 0xFFFF; FPCW is set to `0x027F` and read back.
- **Floats** are compared as the full 80-bit ST0, read via `UC_X86_REG_FP0 + top`, which is stricter than float bits. Unicorn sources used: `qemu/target/i386/unicorn.c` and `bindings/python/unicorn/unicorn_py3/arch/intel.py` at tag 2.1.4.

**Shim unit checks (`harness/run.py shims`).** Every implemented shim on the CRT start-up and tested paths is checked against expected values that do not come from the shims.
- **`GetStringTypeA` (CT_CTYPE1, all 256 bytes):**
  - Bytes map to Unicode through Microsoft's cp1252 table: Python's `cp1252` codec, plus the five undefined bytes, which `bestfit1252.txt` (https://www.unicode.org/Public/MAPPINGS/VENDORS/MICSFT/WindowsBestFit/bestfit1252.txt) maps to U+0081/8D/8F/90/9D.
  - Each Unicode category maps to the C1 meanings from https://learn.microsoft.com/en-us/windows/win32/api/stringapiset/nf-stringapiset-getstringtypew: Lu → UPPER|ALPHA, Ll → LOWER|ALPHA, other L → ALPHA, Nd → DIGIT, Zs → SPACE|BLANK, Cc → CNTRL (plus SPACE for 0x09–0x0D and BLANK for 0x09), everything else → PUNCT, and 0-9, A-F, a-f also get XDIGIT.
  - Writing these checks found two shim bugs, both fixed: `ˆ`, `ª` and `º` were misclassified, and the five undefined bytes were not CNTRL.
- **`LCMapStringA`:** upper and lower case over all 256 bytes, from Unicode case mapping where the result is one cp1252 byte. Also the `cchDest = 0` size query and the error-122 short buffer.
- **`GetCPInfo(1252)`:** MaxCharSize 1, DefaultChar `?`, no lead bytes, and exactly the 18 bytes of CPINFO's members written (MaxCharSize 4, DefaultChar 2, LeadByte 12; CPINFO, https://learn.microsoft.com/en-us/windows/win32/api/winnls/ns-winnls-cpinfo, and `CPINFO 1 0x3f` in bestfit1252). This check caught the shim clearing 20 bytes; that is fixed.
- **`HeapAlloc`/`HeapReAlloc`/`HeapSize`/`HeapFree`:** zeroing, in-place shrink and failed grow, moving grow, and NULL on oversize.
- **`VirtualAlloc`:** alignment, zero fill, commit rounding, oversize and size 0.

**Inputs (`harness/inputs.json`, made by `harness/make_inputs.py`).** These are hand-written and game-free:
- seed lists and bounds tables;
- string building blocks (both slash kinds, bytes 0x80–0xFF, exact and short buffers);
- a seeded generator (`random.Random(20261026)`).

Each AC6 helper's argument count was read from its x86 code with `tools/recomp/arity.py`, which tracks pushes and pops before every `[esp+N]`/`[ebp+N]` read. `Q_trimspace` reads three arguments (source, destination, destination size), and its last action is a call to `Q_strncpy(dst, src + skipped, n)`. The harness passes a padded source, a destination buffer and sizes from 0 to 64, so the whitespace loops and the copy run. The other 11 helpers' argument counts match the harness.

Output buffers are prefilled with 0xCC and compared from 16 bytes before the buffer to 16 bytes after its end.

## Results per acceptance criterion

All runs below are on master + this change, on an M-series Mac, macOS 26.6 (Darwin 25.6.0).

| AC | Result | Evidence |
|---|---|---|
| AC1 | Met | `report.json`: 411 seeded functions, 410 translated + 1 intrinsic (`__ftol`) = 411, 0 skipped. Seeds: call 219, reloc 119, export 64, tail-jump 46, ilt-thunk 3, entry 1 (a function can have several). Of the 222 relocation targets, 119 are function starts, 88 are jump-table arms and 15 are data tables in code (`reloc_targets_not_function_starts`). Of Ghidra's 329 functions, 292 are report rows. The other 37 are in `ghidra_not_function_starts`: 35 with "no relocation, direct call, jump or export reaches it" (dead CRT math code; a relocatable DLL has a relocation for every absolute pointer), and 2 "inside the body of sub_…". 16 FDIV-adjust stubs contain `int 6`; they are translated, and that instruction aborts if reached, as the original raises #UD. |
| AC2 | Met | `file`: `Mach-O 64-bit dynamically linked shared library arm64`; `lipo -info`: `Non-fat file … is architecture: arm64` |
| AC3 | Met | (a) `otool -L` lists only `@rpath/libvstdlib.dylib` and `/usr/lib/libSystem.B.dylib`; `nm -u` lists libc/libm symbols only, with no `uc_` symbol. (b) With `site-packages/unicorn` moved away (`import unicorn` → `ModuleNotFoundError`), `run.py check` exited 0 with 20 PASS lines and 0 FAIL. |
| AC4 | Met | 32 seeds (including 0, 1, -1, INT_MIN and INT_MAX) × (1,000 uniform RandomInt, 1,000 uniform RandomFloat, 1,000 Gaussian RandomFloat attached to a uniform stream, 1,000 global RandomInt and 1,000 global RandomFloat after RandomSeed) = 160,000 results. All are equal to the oracle; floats are compared as 80-bit ST0. |
| AC5 | Met | Check run: 115,137 indirect calls, 104,858 to guest code (vtable calls among them), 10,279 to shims, 0 misses, and `extra-entries.txt` is empty. Forced call to `entry+1` (`0x10005369`): `rt: indirect call to unknown guest address 0x10005369`, exit 3, address appended to `extra-entries.txt` (the harness then restores the file) |
| AC6 | Met | 12 helpers × 500 inputs = 6,000, all equal to the oracle (return value, plus the whole output buffer with 16 guard bytes each side) |
| AC7 | Met | Two consecutive runs write `native-results.json` with sha256 `7d3e33d5612508900a12bc636107554dfc133ff04c2b924b3e0e8db5c11c1315` both times. The moved layout gives the same hash. |
| AC8 | Met | Generated C, objects, the library and recordings are only under `$DEV/build/vstdlib/`. `git ls-files` lists no `.c` under `gen` and no `sub_` names. |
| AC9 | Met | Run into an empty directory: the command exits 0 and prints `ALL PASS`. The validator's `rm -rf $DEV/build/vstdlib` run is the gate. |
| AC10 | Met | `shasum -a 256 -c docs/game-manifest.txt` from `$GAME`: 5,399 OK, 0 failures |
| AC11 | Met | `rt_load` only copies the pre-relocated image (no relocation code in `runtime/`). The `layout-moved.toml` build (base `0x14000000`, 1,498 relocated words differ from the default image) passes all AC4 and AC6 cases. |
| AC12 | Met | `_Q_strlen(0)`: `rt: guest memory fault at guest address 0x00000000`, exit 4. The handler calls `_exit`, so there is no host crash report. |
| AC13 | Met | `DllMain(DLL_PROCESS_ATTACH)` runs natively through the MSVC 6 CRT start-up (heap, environment, code page and static initialisers, with `ebp` global so any `__finally` funclet sees its parent's frame) and returns 1, matching the oracle. The stack is balanced (`ret 0Ch`). |
| AC14 | Met | `cpuid` leaf 1 MMX/SSE/SSE2/SSE3 bits are (0, 0, 0, 0). `IsProcessorFeaturePresent`, called directly through `rt_shim_call` because vstdlib does not import it, returns 0 for MMX (3), SSE (6), SSE2 (10), 3DNow (7) and SSE3 (13). |
| AC15 | Met | `oracle.json.log`: `unicorn reset FPCW = 0x0000` (confirms the reset claim), then `oracle runs 166193, FPCW set to 0x027F and read back as 0x027F on 166193` |
| AC16 | Met | After a second run with no change, the generated sources (`gen/`, `image.bin`, `functions.json`, `module.json`, `report.json`), the objects and the libraries keep their modification times (37 files), and ninja reports `ninja: no work to do.` (0 compiles). The run outputs (`oracle.json`, `native-results.json`, `extra-entries.txt`) are rewritten on every run. |

Mutation checks:
- A one-ulp change in one RandomFloat result, and one changed guard byte in one `Q_snprintf` buffer: `check` exits 1 and names the input.
- `ebx++` → `ebx--` in the lifted whitespace loop of `Q_trimspace`: `FAIL Q_trimspace: 280/500 match the oracle`.
- Clearing the digit bit (`& ~0x4`) in the `GetStringTypeA` shim: `FAIL shim GetStringTypeA CT_CTYPE1 0x00-0xFF: 0x30: 0x080 != 0x084; …`.

## Known limits

- `_chkstk` and setjmp/longjmp do not occur in vstdlib.dll. Their intrinsics are wired up but not exercised by this test.
- Guest addresses below `0x4000` fault, not only page 0, because the host page is 16 KiB.
- The shims model Windows XP behaviour only as far as the tested paths need. The unicode variants fail as on Windows 9x, and 16 imports abort when called.
- The oracle runs the same relocated, IAT-bound image as the native build, with the same shims, so it checks the translation only. The shims are checked separately by `run.py shims` against documented values. The cp1252 ctype expectations are derived from Unicode categories, not from a run on Windows.
- Writes into code are detected only for absolute-address stores. A store through a register that happens to point into `.text` would surface at run time instead.

# M2: all 23 modules lifted and linked into one arm64 executable

M2 lifts every guest module through the same pipeline, binds every import at translate time, and links one native arm64 executable. Its `main` maps all 23 images into the guest arena and checks every IAT slot. It runs no guest code.

## The one command

```sh
$DEV/venv/bin/python -I tools/recomp/build_all.py [--out DIR] [--jobs 6]
```

`--out` defaults to `$DEV/build/all`. The command:

1. Runs `plan.py layout.toml --out DIR`. It checks the layout, then writes `plan.json`, the stub tables `stubs/*.c`, `stubs/index.c`, `stubs/plan.tsv`, `modules.txt`, `link.c` and the fallback lists.
2. Runs `test_discover.py` and `test_plan.py DIR/selftest` (game-free self-checks; the second also writes the synthetic `conform.dll` and `simd.dll`).
3. (Ghidra is no longer needed: since K3a, `build_all.py` does not run it. Function names are optional and the modules translate without them.)
4. Translates the 23 modules plus the 2 synthetic DLLs in a pool of 6 processes, largest code first (`translate.py --plan`).
5. Fails with `disallowed instruction` if any untranslated site is outside `simd-trap`, `fault-as-original` and `hint`.
6. Runs `meson setup` (first run only) and `ninja` on `link/`, and builds the two synthetic DLLs with `runtime/meson.build`. `ccache --zero-stats` runs before and `ccache --print-stats` after (ccache `--help`: "print statistics counter IDs and corresponding values in machine-parsable format").
7. Runs the link checks, the executable, its probes and the harness cases, and writes `summary.json` and `stubs.tsv`.

End states and exit codes: `pass` 0; `check failed` 1; `translate error` 2 (layout, plan, self-check or a module that could not be read); `disallowed instruction` 3 (names module, address and kind); `link error` 4 (prints the end of `build.log`).

Everything generated is under `--out`: `<module>/` (`gen/`, `image.bin`, `report.json`, `module.json`, `functions.json`, `inputs.sha256`, `translate.log`), `stubs/`, `selftest/`, `ghidra/`, `meson/` (objects and `bloodlines-native`), `plan.json`, `summary.json`, `stubs.tsv`, `build.log`, `ccache.txt`.

## Pipeline changes

**Guest layout (`layout.toml`, `plan.py`).** Each module has `path` (relative to `$GAME`) and `base`. `plan.py --check` refuses a layout when a base is not 64 KiB-aligned, when two images overlap (it names both, for example `layout: b [0x10020000, 0x10030000) overlaps a [0x10000000, 0x10030000)`), when an image overlaps a reserved range, or when a moved module has no `.reloc`. The reserved ranges are read from `runtime/rt.h`: `[0, RT_VIRT_HI)` (page-zero guard, TIB, state, data cells, stack, heap, virtual-alloc), the harness GDT `[RT_GDT, RT_GDT + RT_GDT_SIZE)`, and everything from `RT_GUEST_TOP` (0x80000000) up. `plan.py --suggest` prints the deterministic layout: vstdlib first, then the lift order; pass 1 keeps each preferred base that is free, and pass 2 gives each other module the lowest free 64 KiB-aligned base above its preferred one. The stored `layout.toml` is exactly that output: 7 modules keep their base, 16 move into 0x10020000–0x11750000, and the highest end is msvcrt's 0x78050000.

**Binding (`plan.py`).** Every IAT slot gets a guest address before translation:
- An import from a lifted module resolves through the exporter's export table by name, by ordinal (`#N`, N = EAT index + Ordinal Base) or through a forwarder (`DLL.Name` or `DLL.#N`; a chain longer than 8 is an error). A code export binds to the function VA. A data export (RVA outside every executable section) binds to the data VA in the exporter's laid-out image.
- An import from any other DLL binds to a stub VA `0xFE000000 + 16·k`, where k is its index in the sorted unique (dll, name) list. One stub serves every importer.
- A data import (`@@3`) from a DLL that is not lifted gets a 4-byte cell at `data_cells + 4·j` (0 today).

Stub pop counts come from the decorated name (`_Name@N`), or else from the shipped DLL's own code: every `ret`/`ret imm16` reachable from the export (`discover.descend`) must agree. Otherwise the count is unknown and logged as `pop ?`.

**Discovery (`discover.py`).**
- Speed: `_close` memoizes each body walk by (start, limit), so a fixed-point round re-walks only functions whose limit moved. `owner()` is a bisect over sorted starts, scanning back only while an earlier body can still reach the address. Output is unchanged: vstdlib, launcher and client.dll discovery dumps (functions, kinds, bodies, tables, interior targets, relocation targets) hash identically before and after (client `f12822b4…c6361f9e5`). client.dll discovery went from 277 s to 18 s. 38,000 `owner()` queries on engine.dll match the old linear scan.
- Two classification fixes found on the full set. A relocation target that is itself a relocation slot is a data table in code, never a function start (engine.dll's descriptor table at 0x201403F0 was being lifted as `sldt`, `daa`, `bound`, …). The 4-byte displacement of a `jmp [reg*4+table]` is never a jump-table entry, even when the next table starts right after it (msvcrt's `memcpy` tables put `0x78003150` among the arms). vstdlib's discovery hash is unchanged by both. `test_discover.py` covers both on a synthetic image.
- Split switch tables (`table-arm` seeds). When a `jmp [reg*4+table]` has any arm outside its own function's range, every arm of that table becomes a function start. The lifter inlines a table as gotos only while its leading arms lie inside the function, and otherwise emits `RECOMP_ITAIL`, which needs each arm in the dispatch table. msvcrt's `strtod` state machine (`jmp [eax*4+0x78001C18]` at 0x780016D3; arm 0 at 0x780016DA inside the function, arm 1 at 0x7802AF57 in the cold section) stopped the first map load with `dispatch miss … 0x780016DA` when parsing a float. Across the 23 modules this added 3 functions, all in msvcrt (0x780016DA, 0x78002B05, 0x78002B24): 85,630 → 85,633. `test_discover.py` covers it.

**Translation (`translate.py --plan`, `lifts.py`).**
- IAT slots come from `plan.json`. `module.c` is one `const rt_module rt_mod_<name>` record (base, size, entry, code range, image, image SHA-256, lookup, dispatch init, intrinsics, slots). The image comes in with `#embed "../image.bin"`; clang documents `#embed` before C23 as an extension (clang Diagnostics Reference, `-Wc23-extensions`: "#embed is a C23 extension"), so `module.c` is compiled with `c_std=gnu23` (Meson built-in options: `c_std` accepts `gnu23`). The ccache manual does not cover `#embed`, so `module.c` also carries the image's SHA-256: when the image changes, the source changes.
- Without `--plan`, the M1 binding is used, and `module.c` also defines the one-module `rt_modules` and `rt_imports` tables.
- Chunks are the files the translator writes in this run (`recomp_NNNN.c` and, when it exists, `recomp_stubs_unresolved.c`), not a directory listing, so a shrinking module leaves no stale chunk in the build.
- `inputs.sha256` hashes the DLL, the module's `plan.json` slice and chunk size, `tools/recomp/`, `third_party/xboxrecomp/`, the Ghidra names and the extra entries. It is written last; when it matches, the module is not translated again.
- `report.json` adds `untranslated` (one row per site: module, VA, mnemonic, function, category), `untranslated_counts`, `seeds`, `data_imports`, `tls`, `ordinal_imports`, `forwarders`, `forwarded_imports`, `data_exports_seeded` and `relocated_words_outside_image`. Intrinsics now count as translated, with the intrinsic named in the reason.

**Wrapper lifts (`tools/recomp/lifts.py`; `third_party/` is unchanged).** `lifts.install()` wraps `Lifter.lift_instruction` and `Disassembler._decode_instruction`:
- SIMD: an instruction whose capstone groups include MMX, 3DNOW, SSE1–SSE4A, SSSE3, AVX or AVX2 (capstone 5.0.9 `x86_const`), or with an `mm0–7`/`xmm0–7` operand, or `emms`, `femms`, `ldmxcsr`, `stmxcsr`, becomes `RT_SIMD_TRAP("<text>", va)`. At run time it prints `rt: SIMD instruction '<text>' at guest address 0x… is not translated` and exits 9. `prefetch*`, `sfence`, `lfence` and `mfence` keep the lifter's no-op and are counted as `hint`.
- MMX (M7 MMX): the 47 original MMX instructions (`movd`, `movq`, pack/unpack, `padd*`/`psub*` incl. saturating, `pmullw`, `pmulhw`, `pmaddwd`, `pcmp*`, logic, shifts, `emms`) with no `xmm` operand are translated, not trapped, through the upstream `_lift_mmx` and the `MMX_*` helpers in `recomp_types.h`; `mm0–7` are the TLS `g_mm0–7` in `runtime/rt.c`. Engine's sound mixer `sub_20136D50` runs these unconditionally (its vtable callers have no CPUID check), so CPUID still reports no MMX. As state, every MMX instruction sets x87 TOP to 0 and `emms` empties the stack (`g_fp_top = 0`); the 64-bit values are not aliased into `g_fp_stack`. `punpckl*` with a memory source reads 32 bits (SDM: `mm/m32`). 3DNow, the SSE extensions on `mm` (`pshufw`, `pavgb`, …) and everything with `xmm` still trap. `test_mmx.py` holds 160 cases in `simd.dll`, each checked against an SDM-derived golden and Unicorn; `build_all.py` runs it as harness mode `mmx`.
- `int n` and `int3` are `fault-as-original`: they abort where the original raises an exception (exit 8). `int3` used to lift to a comment.
- A lift that is only comments is `unimplemented` and becomes `RECOMP_UNIMPL`, unless it is a known no-op: `nop`, `wait`, `lahf` in the FPU compare idiom, `stc`/`clc`/`cmc` with no CF reader, or `bt` with no CF consumer.
- New lifts, each a Unicorn conformance case: `fnclex` (clears the exception and busy bits of the modelled status word), `fninit`, `ffree` (no state change; tags are derived from the stack depth), `fnstenv`/`fldenv` and `fnsave`/`frstor` (runtime helpers `rt_fnstenv`, `rt_fldenv`, `rt_fnsave`, `rt_frstor`: CW, SW and TW as 32-bit fields, 28-byte environment, registers as 80-bit values in stack order, then `fninit` after `fnsave`), and `lock`-prefixed read-modify-write instructions other than `xadd`/`cmpxchg`, which are decoded without the prefix so both the lift and the lifter's flag tracking see `inc`/`dec`.

Before these, the first full translation had 141 non-SIMD sites: `fnclex` 43, `fnsave` 13, `frstor` 13, `ffree` 13, `lock inc`/`lock dec` 12, `fnstenv` 5, `fldenv` 5, `fninit` 3, and 34 decodes of data (engine's descriptor table and msvcrt's jump table: `daa`, `sldt`, `bound`, `arpl`, `ins*`, `outs*`, `lcall`, …), fixed in discovery. None needed a redesign.

**Flags across functions (`tools/recomp/xflags.py`; `third_party/` is unchanged).** Discovery cuts a function where a cold block jumps back into it, and the lifter turns every jump that leaves `[func_start, func_end)` into a tail call (`lifter.py:2681-2683`, `:1130-1144`). A new C function starts with no flag state (`translator.py:105-115`), so a jcc at its entry lifted to the never-assigned `_flags` and an entry `sbb` read `_cf = 0`. The same happened after a call to a helper that returns its answer in the flags (the per-module x87 `__fload_withFB`, `cmp …; ret`). `xflags.Scan` runs over the discovered bodies and finds:
- consumers: function starts whose entry reads a flag (jcc, setcc, cmovcc, fcmovcc, loope/loopne, cmc (reads CF), `translator._FLAG_READERS`) before every flag it reads has been rewritten, or a call or ret, closed over entries that jump straight on to a consumer. The walk tracks which of CF/PF/ZF/SF/OF are still live, and a writer removes only the flags it writes: `translator._FLAG_WRITERS` plus shld/shrd/div/idiv and a plain (unprefixed) cmps/scas, adc and sbb write all five (a shift or rotate does so only when its count is non-zero, see below); inc/dec write all but CF; bt/bts/btr/btc/stc/clc write CF only; rol/ror/rcl/rcr write CF and OF; a rol/ror/rcl/rcr/shl/sal/shr/sar/shld/shrd whose count is `cl`, or an immediate whose count masked to 5 bits is 0, writes nothing, because the scan cannot know `cl` and a masked count of 0 leaves the flags unchanged (an immediate with a non-zero masked count keeps its kill set above); cmc rewrites nothing, since its CF still depends on the incoming CF; `rep`/`repe`/`repne` cmps/scas write nothing, because with a zero count the flags keep their old value (the lifter loads that ZF, `lifter.py:4292-4305`). Locked instructions are matched without the `lock` prefix, so `lock xadd` and `lock cmpxchg` are all-flag writers. A jump's exit carries only the flags still live there;
- flag-returning callees: X where the code after some `call X` reads a flag, or calls, jumps or falls into a consumer, before a writer; the one walk per call site merges the consumers it exits into, and closed over X's tail jumps.

`xflags.install()` wraps `translator._incoming_flag_state` (a consumer's entry block gets the state `("eflags", [])`, `lifter.py:590-615`, so its conditions read `g_eflags`; an entry with an in-function predecessor stops translation) and `translator.lift_basic_block` (looked up per call at `translator.py:3026` and `:3062`). The block wrapper writes `g_eflags` with the lifter's own `publish_flags` (`lifter.py:4064-4086`) before a jcc, jmp or fall-through into a consumer, before a `call` to one, and before the `ret` of a flag-returning function. It splits the block after a call to a flag-returning callee, so what follows reads `g_eflags`. Where the function uses `_cf`, it also loads `_cf = (int)(g_eflags & 1u)`, as popfd does (`lifter.py:1802-1803`). If a consumer reads a flag that the producer's state has no rule for, translation stops with `cross-fragment flag <F> has no rule after <setter> at 0x…`. No function is added.

`report.json` has `cross_flags`: consumers, flag-returning callees, publish sites, `_cf` loads, and every `_flags`/`_cf` fallback left with its category. `build_all.py` totals them in `summary.json` and fails unless the fallbacks equal, exactly, the checked-in list `tools/recomp/xflags_fallbacks.txt` (one `module VA category` line each; 48 now, every one `after-table`, bytes after a `jmp [reg*4+table]` that discovery never reached, or `after-simd`, behind an MMX/3DNow instruction that traps first). Any difference prints a unified diff. `cross_flags.exported` lists the consumers and flag-returning functions that a module exports (now only msvcrt `_chkesp`), and `exported_imported` the ones another module imports through its IAT; the build fails if that is not empty, because the other module's call site is not scanned. `test_xflags.py` (run by `build.py` and `build_all.py`) scans a synthetic image for the writer and reader rules above and translates a synthetic DLL covering the jmp, jcc, fall-through and call edges, a flag-returning helper read by a jcc and by a consumer call, an entry `sbb`, and a non-consumer that gets no publish. The Unicorn cases for the same edges live in `xflags.dll`, written by `test_plan.py`. They run with `harness/run.py xflags --lib … --module …`, and `--only a,b` keeps the cases whose names contain any of the comma-separated substrings.

Sources for the flag rules: Intel 64 and IA-32 Architectures Software Developer's Manual (order number 325462).
- Vol. 1 §3.4.3, "EFLAGS Register": the status-flag bit positions that `g_eflags` uses (CF bit 0, PF bit 2, ZF bit 6, SF bit 7, OF bit 11).
- Vol. 1 Appendix B, "EFLAGS Condition Codes": which flags each condition reads, which gives the per-consumer flag sets in `xflags.CC_FLAGS`.
- Vol. 2, the Jcc, SETcc, CMOVcc, ADC and SBB pages: these instructions read the flags (ADC and SBB read CF).
- Vol. 2 pages for the writer and reader rules, from the July 2017 edition (page of the "Flags Affected" line): INC (Vol. 2A 3-451, CF not affected) and DEC (3-285, "The CF flag is not affected. The OF, SF, ZF, AF, and PF flags are set according to the result"); BT (3-114), BTC (3-115), BTR (3-117), BTS (3-119): CF gets the selected bit, ZF unaffected, OF/SF/AF/PF undefined; CMC (3-148): CF complemented, the rest unaffected; CLC (3-137) and STC (Vol. 2B 4-641): only CF changes; CMPS (3-171) and SCAS (Vol. 2B 4-592): all status flags set from the comparison; REP/REPE/REPNE (Vol. 2B 4-549): the loop runs `WHILE CountReg != 0`, so a zero count executes nothing and the flags keep their value; XADD (Vol. 2C 5-580) and CMPXCHG (3-182): all status flags set; RCL/RCR/ROL/ROR (Vol. 2B 4-523): with a masked count of 0 the flags are not affected, otherwise CF (and OF for count 1) change, and SF/ZF/AF/PF are unaffected. SAL/SAR/SHL/SHR (Vol. 2B 4-583): "If the count is 0, the flags are not affected"; SHLD (4-610) and SHRD (4-613): CF, SF, ZF and PF are set only "if the count is 1 or greater".
- Vol. 2 RET, "Flags Affected: None": `ret` leaves the flags unchanged, so a `cmp …; ret` helper returns its result in ZF, which the caller's `je`/`jne` reads. For `__fload_withFB` that result is "exponent all ones".

**Runtime (`runtime/rt.c`, `rt.h`, `main.c`).**
- A module table (`rt_modules`, `rt_nmodules`) replaces the single-module globals. `rt_load` copies every image to its base (0 relocations at load), widens `g_xbox_code_lo/hi` over all modules, fills a 32,768-entry page table (64 KiB pages below 0x80000000), and calls each module's dispatch init.
- Each module's generated `recomp_dispatch.c` is compiled with `-Drecomp_lookup=rt_lookup_<module>` and the same rename for `recomp_dispatch_init`, `recomp_get_count`, `recomp_call_all` and `recomp_dispatch_flat_bytes`. `rt.c` defines the global `recomp_lookup`, which routes by page to the owning module. A miss still exits 3.
- The stub table is `rt_imports[]` (pointers, one per stub, in VA order). `rt_load` binds each entry to an M1 shim by name. An entry without an implemented shim is a stub: when called it prints `rt: stub DLL!name (stdcall, pop N | convention unknown, pop ?) called from guest 0x… args` with 8 stack words, and exits 10.
- `on_fault` prints the 64-bit arena offset: `rt: guest memory fault at guest address 0x%08X (arena offset 0x%09llX)` inside the arena, and `rt: guest memory fault past arena end (arena offset 0x%09llX)` in the tail guard. Both exit 4.
- Exit codes: 3 dispatch miss, 4 guest memory fault, 5 runtime, 6 host fault, 7 unknown name in `rt_shim_call` (harness only), 8 untranslated non-SIMD instruction or fault-as-original, 9 SIMD trap, 10 stub, 11 guest `ExitProcess`/`TerminateProcess` (was 9, moved so the SIMD trap code is distinct).
- `main` with no argument maps the images, checks every slot and prints one line. `--list-imports` prints `DLL!name VA shim|stub`. `--probe-stub DLL NAME w0 w1 w2 w3` pushes the four words and return address 0x0000BEEF and enters the stub's VA through `rt_call`. `--probe-read VA` reads 4 bytes with `MEM32`.

**Link (`link/meson.build`).** One static library per module for its chunks (`-include runtime/gen_prefix.h -Wno-everything` plus M1's `-ffp-contract=off -fwrapv -fno-strict-aliasing`), one per module for its dispatch and `module.c` (gnu23, the five renames), `stubs_binkw32`, `stubs_mss32`, `stubs_win32` (the other 14 DLLs and `index.c`) and `runtime` (`rt.c`, shims). All are `link_whole` into `executable('bloodlines-native', 'runtime/main.c', link.c)`, so a duplicate symbol fails the link. No `-undefined dynamic_lookup`, no `-flat_namespace`. Lists come from `fs.read`, which makes Meson reconfigure when a list changes (Meson Fs module, `read`).

**Fallbacks.** `layout.toml [build]` has three lists: `optimization1` (modules whose chunk library gets `override_options: ['optimization=1']`; Meson "Set C language version per-target with override_options"), `chunk1` (modules translated with `--chunk 1`) and `o0` (single shards, `<module>/recomp_NNNN.c`, built in their own library at `optimization=0`). Only `o0` is used, for one shard (see "Clean build").

## Results per acceptance criterion

Machine: M1 Max, 10 cores, 64 GB, macOS 26.6 (Darwin 25.6.0), Apple clang 21.0.0, meson 1.12.1, ninja 1.13.2, ccache 4.14.2, capstone 5.0.9, unicorn 2.1.4.

| Module | Base | Seeded | Translated | Skipped | SIMD traps | Fault-as-original | Hints | ILT thunks | Data imports | Stub slots |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| vstdlib | 0x10000000 | 411 | 411 | 0 | 0 | 16 | 0 | 3 | 1 | 50 |
| launcher | 0x10020000 | 281 | 281 | 0 | 0 | 0 | 0 | 4 | 5 | 86 |
| FileSystem_Stdio | 0x10040000 | 576 | 576 | 0 | 0 | 0 | 0 | 6 | 3 | 94 |
| MaterialSystem | 0x10070000 | 1,525 | 1,525 | 0 | 283 | 16 | 0 | 6 | 2 | 38 |
| stdshader_dbg | 0x10310000 | 588 | 588 | 0 | 258 | 16 | 0 | 46 | 1 | 35 |
| stdshader_dx6 | 0x10340000 | 1,020 | 1,020 | 0 | 258 | 16 | 0 | 122 | 1 | 35 |
| stdshader_dx7 | 0x10370000 | 415 | 415 | 0 | 258 | 16 | 0 | 28 | 1 | 35 |
| stdshader_dx8 | 0x10390000 | 1,126 | 1,126 | 0 | 258 | 16 | 0 | 227 | 1 | 35 |
| stdshader_dx9 | 0x103D0000 | 292 | 292 | 0 | 258 | 16 | 0 | 10 | 1 | 35 |
| tier0 | 0x103F0000 | 895 | 895 | 0 | 0 | 0 | 0 | 236 | 0 | 112 |
| vgui2 | 0x10430000 | 1,237 | 1,237 | 0 | 0 | 1 | 0 | 18 | 4 | 167 |
| vguimatsurface | 0x10480000 | 1,455 | 1,455 | 0 | 0 | 16 | 0 | 27 | 4 | 80 |
| vaudio_miles | 0x104E0000 | 341 | 341 | 0 | 0 | 1 | 0 | 5 | 3 | 77 |
| client | 0x10500000 | 16,478 | 16,478 | 0 | 258 | 16 | 0 | 1,179 | 7 | 60 |
| gameui | 0x10BF0000 | 2,715 | 2,715 | 0 | 0 | 0 | 0 | 75 | 4 | 108 |
| vampire | 0x10C70000 | 36,915 | 36,915 | 0 | 258 | 16 | 0 | 16,737 | 20 | 62 |
| zlib1 | 0x11730000 | 98 | 98 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| vampire_python21 | 0x1E100000 | 2,362 | 2,362 | 0 | 0 | 0 | 0 | 13 | 10 | 48 |
| engine | 0x20000000 | 7,503 | 7,503 | 0 | 325 | 18 | 30 | 73 | 14 | 183 |
| vphysics | 0x26000000 | 4,312 | 4,312 | 0 | 258 | 16 | 0 | 71 | 1 | 75 |
| shaderapidx9 | 0x2A000000 | 1,483 | 1,483 | 0 | 10,527 | 16 | 0 | 10 | 2 | 62 |
| StudioRender | 0x2C000000 | 811 | 811 | 0 | 500 | 16 | 384 | 1 | 4 | 43 |
| msvcrt | 0x78000000 | 2,791 | 2,791 | 0 | 0 | 18 | 0 | 2 | 0 | 140 |
| **Total** | | **85,630** | **85,630** | **0** | **13,699** | **246** | **414** | | **90** | **1,661** |

Every module has 0 relocated words pointing outside its own image, 0 unbounded jump tables and 0 writes into code.

| AC | Result | Evidence |
|---|---|---|
| AC1 | Met | 23 `report.json` files, one row per seeded function. `build_all.py` asserts translated + skipped = functions = rows for each, and a non-empty reason on every skip. Totals: 85,630 seeded, 85,630 translated (17 of them intrinsics), 0 skipped, so `summary.json` `skipped` is empty. |
| AC2 | Met | `file`: `Mach-O 64-bit executable arm64`; `lipo -info`: `Non-fat file: …/bloodlines-native is architecture: arm64`. AC13's `libvstdlib.dylib`: `Mach-O 64-bit dynamically linked shared library arm64`. |
| AC3 | Met | ninja links with no `-undefined dynamic_lookup` and no `-flat_namespace`. `otool -L` lists only `/usr/lib/libSystem.B.dylib`, and `nm -m -u` gives 44 undefined symbols, each `(from libSystem)` (`man nm`: `-m` shows the library expected to provide each undefined symbol). The executable prints `mapped 23 images (50626560 bytes); iat slots: 541 code, 90 data, 636 shim, 1025 stub, 0 cell, 0 unbound; guest code run: none` and exits 0. |
| AC4 | Met | `summary.json` `untranslated_kinds` has 83 kinds: 13,699 `simd-trap` sites, plus 246 `fault-as-original` (`int` 240, `int3` 6) and 414 `hint` (`prefetcht0`) in their own columns. No `unimplemented` row. `test_plan.py` gives the checker a report with one `unimplemented` row: it is returned as disallowed, and `build_all.py` then exits 3. |
| AC5 | Met | `harness/run.py trap` on the synthetic `simd.dll` (one export, `movss xmm0, dword ptr [esp + 4]; ret`, translated with `translate.py` and built with `runtime/meson.build`): `PASS trap: exit 9, rt: SIMD instruction 'movss xmm0, dword ptr [esp + 4]' at guest address 0x10001010 is not translated`. `cpuid` leaf 1 and `IsProcessorFeaturePresent` still report no MMX, SSE, SSE2, SSE3 or 3DNow (AC13's run, `PASS no SIMD`). |
| AC6 | Met | `stubs.tsv` has 401 lines, the plan's unique count: kernel32 190, user32 81, gdi32 32, winmm 17, wsock32 16, binkw32 15, dbghelp 10, advapi32 9, oleaut32 8, Mss32 6, ole32 6, ws2_32 5, shell32 3, d3d9, dinput8 and shlwapi 1 each. The binding column comes from `bloodlines-native --list-imports`: 35 shim, 366 stub, 0 missing. Pop counts: 19 from decorated names, 12 from the shipped DLLs' code, 370 unknown. Every stub name appears in `docs/inventory/imports-*.txt`. `git ls-files` shows `tools/recomp/plan.py` and no generated stub source. |
| AC7 | Met | `bloodlines-native --probe-stub USER32.dll MessageBoxA 0x11111111 0x22222222 0x33333333 0x44444444` prints `rt: stub USER32.dll!MessageBoxA (convention unknown, pop ?) called from guest 0x0000BEEF args 0x11111111 0x22222222 0x33333333 0x44444444 …` and exits 10. No new `bloodlines-native*` report in `~/Library/Logs/DiagnosticReports`. |
| AC8 | Met | AC13's M1 command runs `run.py shims` on a `libvstdlib.dylib` built from the M2 `runtime/rt.c` and `shims/*.c`: every shim check passes. `--list-imports` marks each implemented kernel32 shim `shim`. |
| AC9 | Met | `summary.json` `data_imports`: 90 slots, 37 names, 21 slots to the 16 vampire_python21 objects (`_Py_NoneStruct` and the others). For every slot, the word in the importer's `image.bin` equals the exporter's export VA in `plan.json` (`image_word_matches` true on all 90), and the executable checks the same in guest memory. |
| AC10 | Met | `plan.py --check layout.toml`: `layout: 23 modules, 0 overlaps, 0 reserved-range overlaps, highest end 0x78050000`. `test_plan.py` refuses an overlapping pair, naming both, and a module over the runtime regions. The executable maps 23 images. |
| AC11 | Met | A second run with no change: every module prints `unchanged` (inputs stamp), the summary says `0 generated files rewritten` (an mtime snapshot of the output directory without `meson/`, `ghidra/` and the run outputs), ninja prints `ninja: no work to do.` for the link and both synthetic builds, and `bloodlines-native` keeps its modification time. It takes 7 s. |
| AC12 | Met | Clean build into a new empty directory: exit 0 in 725 s; 237,187,289 bytes of C, 170,350,664 bytes of objects, a 137,043,056-byte executable, ccache hit rate 0.4 %. Details under "Clean build". |
| AC13 | Met | `tools/recomp/build.py` (M1 command, default and moved layouts) runs 80 checks, prints `ALL PASS`, exits 0, and both `native-results.json` files have sha256 `7d3e33d5612508900a12bc636107554dfc133ff04c2b924b3e0e8db5c11c1315`. vstdlib's M1 translation now has 16 `fault-as-original` sites and no `unimplemented` one: `fnclex` (2 sites, comment-only before M2) is lifted. |
| AC14 | Met | Generated output is only under `$DEV/build/`. `git ls-files` lists no `.c` under a `gen` directory and no `sub_` name. `shasum -a 256 -c docs/game-manifest.txt` from `$GAME`: 0 failures. |
| AC15 | Met | `on_fault` prints the 64-bit arena offset (both forms above). M1's `null` abort still matches `guest address 0x00000000`. |
| AC16 | Met | `bloodlines-native --probe-read 0xFFFFFFFE` prints `rt: guest memory fault past arena end (arena offset 0x100000000)` and exits 4, with no new crash report. `build_all.py` runs it. |

Conformance cases for the new lifts (`harness/run.py conform` on the synthetic `conform.dll`, Unicorn with FPCW 0x027F set and read back on 7 of 7 runs): `fnclex`, `fninit`, `ffree`, `fnstenv`, `fldenv`, `fnsave`/`frstor` and `lock` all PASS. They compare AX after `fnstsw`, ST0, and the written bytes: CW, SW and TW of the environment, the three used register slots of the `fnsave` image, and the values reloaded after `frstor`. The `lock` case checks `lock inc`, `lock dec` with `sete`, and `lock dec` with `jne`.

## Clean build

Final run, at the commit that adds the `-O0` fallback, into a new empty directory (`--out <build dir>`, for example `$DEV/build/all-clean2`, a scratch folder that no longer exists), with a cold ccache for these paths:

| Measure | Value |
|---|---|
| Exit, state | 0, `pass` |
| Wall time (`/usr/bin/time`) | 725 s (12.1 min): translation 155 s, ninja 552 s, the rest Ghidra name export (about 80 s) and the checks |
| Peak memory (largest process) | 955 MB |
| Generated C | 237,187,289 bytes, 2,173 module shards plus dispatch, `module.c` and stub files |
| Object bytes | 170,350,664 |
| `bloodlines-native` | 137,043,056 bytes |
| ccache | 10 hits of 2,242 lookups (0.4 %) |

A run before the fallback, also into an empty directory (`all-clean`), took 1,216 s (20.3 min), so it crossed the design's 20-minute trigger. `.ninja_log` named the slowest step: `vampire/recomp_0903.c`, 108 s, which holds one 13,939-instruction function (0x110826E0, 68,194 bytes). Compiled alone, that shard takes 72 s at `-O3` (279 MB), 114 s at `-O2`, 212 s at `-O1` and 1.1 s at `-O0`. `-O1` per module would have made it slower, so only the third fallback is used: `layout.toml [build] o0 = ["vampire/recomp_0903.c"]`.

A run with no change after it (`build_all.py --out <build dir>` again, the same example path): 7 s, every module `unchanged`, `0 generated files rewritten`, `ninja: no work to do.` three times, `bloodlines-native` modification time unchanged.

## Known limits (M2)

- `lock`-prefixed `inc`/`dec`/`add`/… are lifted as plain read-modify-write. That is exact while guest code runs on one thread; M3 needs atomics once guest threads run in parallel.
- The x87 tag word is derived from the stack depth (registers from TOP to 7 are in use), as the lifter's model has no tags. `ffree` therefore changes nothing, and a full 8-register stack reads as empty.
- `fnstenv`/`fnsave` write 0 for the instruction and operand pointers. Unicorn writes the last FPU instruction's address there; the conformance cases do not compare those fields.
- The lifter still linearly decodes data inside some function ranges (for example a jump table after an indirect `jmp`). That C is unreachable, and its sites are not counted, because only instructions that discovery decoded are reported. The same holds for `recomp_stubs_unresolved.c`, which the translator writes for call targets inside such data.
- 370 stubs have no known pop count. A stub never returns in M2; M3 writes real shims with Microsoft Learn signatures.
- The default `main` checks every IAT slot, but not every dispatch entry.

# M3: boot to the engine console

M3 runs the linked executable. `bloodlines-native --run` maps the 23 images, starts the guest primary thread on the host main thread, runs every DllMain in dependency order, calls `LauncherMain` with the game's command line, and the original engine boots to its developer console in an SDL3 window. The default argument list is `-window -w 1280 -h 720 -nosound -nojoy -noip -nolan -dev -console -condebug +echo M3_CONSOLE_READY`; the run prints one `rt: stage` line per step and ends with `rt: stage 8 console` when the engine's console prints the `+echo` text.

## Running it

```sh
X=$DEV/build/all/meson/bloodlines-native
$X --run [--until console] [--close-after S] [--overlay DIR] [--game DIR] [-- EXTRA ARGS]
```

- `--overlay` defaults to `~/Library/Application Support/BloodlinesNative/`. It holds `files/` (everything the game writes, under its game-relative path), `registry.reg` (REGEDIT4) and `logs/` (`run-<UTC>.log` and `first-calls-<UTC>.tsv`). Deleting it resets settings and saves.
- `--game` defaults to `$GAME`; it is only ever opened read-only (`rt_vfs_open` stops the run with exit 5 on any write, create or truncate under it).
- `--until console` exits 0 two seconds after stage 8; `--close-after S` posts a window close request S seconds after stage 8.
- Debug switches (environment): `RT_TRACE_CALLS=<substring>` logs every shim call whose name contains it with 8 argument words and the return value; `RT_TRACE_ICALLS=1` logs every indirect call target and caller; `RT_TRACE_FILES=1` logs every `CreateFileA` with its guest path, access, disposition, handle and error; `RT_STOP_AT=<import name>` stops with the SEH chain and a 32-entry return-address scan at that import; `RT_GUEST_QUANTUM_MS` (default 2) sets the guest-lock quantum.

Exit codes: M2's 3 to 11, plus 12 unhandled guest exception, 13 DllMain failed, 14 deadlock; in `--run` mode a guest `ExitProcess(n)` exits with n. A refused start (overlay, SDL, IAT check, unparsable `registry.reg`) exits 1.

## What was built

- `runtime/thread.c`: the guest lock (ticket queue, 2 ms quantum, waiters poll every 2 ms and raise `g_xbox_guest_cpu_preempt`; the scheme follows xboxrecomp's `src/kernel/kernel_guest_cpu.c`, MIT, rewritten on pthreads), the back-edge hook `xbox_GuestCpuPreempt`, guest threads (one pthread each, 64 MiB host stack, guest stack from the allocation region, own TEB and static-TLS copy, `DLL_THREAD_ATTACH`), `rt_guest_call` (one function for stdcall and cdecl callees, checked by `esp`), the kernel-object handle table, waits on one condition variable, `TerminateThread` (the victim exits at its next lock or wait point), and the watchdog (pump requests, `--until`, `--close-after`, forced quit 10 s after a close request, deadlock stop 14; each time check skips a stamp newer than the watchdog's own `now`, which another thread can set between the two reads, so an unsigned difference never wraps into a huge age and fires `--close-after` early). A window close is taken at the next `Present` (`rt_close_take`, `rt_engine_exit` queues the engine's `exit` command), so the game writes `cfg/config.cfg`; the forced quit stays as the fallback.
- **Guest registers.** One plain struct `g_cpu` in `third_party/xboxrecomp/runtime/recomp_types.h`, not thread-locals: only the lock holder runs guest code, and on macOS each thread-local read was a call to `_tlv_get_addr` (up to 35 % of main-thread samples in the courtroom cutscene). `acquire` swaps `g_cpu` with the saved copy after each hand-off to another thread (`cpu_take`). So no code may read a register while the lock is released: the four release regions (`EnterCriticalSection`, `GetMessageA`, `rt_wait`, the patch's loading delay) use locals only. `rt: guest cpu swaps <n>` counts the swaps.
- `runtime/heap.c`: one 64 KiB-granular page allocator over `0x30000000`–`0x78000000` (`plan.py` reserves it) for `VirtualAlloc`, TEBs and guest stacks, with size-class bins for `HeapAlloc`, `GlobalAlloc`, `LocalFree` and tier0's `Plat_*`. M1's dylib keeps the low region `0x01000000`–`0x0C000000` so the Unicorn oracle still sees every allocation.
- `runtime/trace.c`: run log, stage lines, guest output split into `guest:` lines, the first-call trace (pattern from MacNativeGames' `games/totk/runtime/hle_stack.c`, MIT: a per-import counter plus an atomic seen flag), the fault report and the one exit path (`rt_exit` writes the TSV, `max pump gap`, the allocator high-water mark).
- `runtime/vfs.c`: Win32 path translation (`C:\VTMB` is the game root, `\` and `/`, drive letter and case ignored, `..` cannot leave the root), case-insensitive component matching against cached listings of the overlay and the game folder, copy-on-write into `files/`.
- `runtime/window.c` + `shims/user32.c`: one SDL3 window per top-level `CreateWindowExA`, a message queue on the primary thread, the pump (`SDL_EVENT_WINDOW_CLOSE_REQUESTED`/`SDL_EVENT_QUIT` → `WM_CLOSE`, focus → `WM_ACTIVATEAPP`/`WM_ACTIVATE`, shown → `WM_SIZE`), window procedures called through `rt_guest_call`. The window number for `screencapture -l` comes from `SDL_PROP_WINDOW_COCOA_WINDOW_POINTER` and `-[NSWindow windowNumber]` through `dlsym` (no extra link dependency).
- `runtime/main.c`: `--run` and its eight stages; `--probe-load PATH [NAME|#N]…`; M2's modes unchanged.
- **Text conversions into a short buffer.** `MultiByteToWideChar` and `WideCharToMultiByte` (`shims/kernel32.c`) write the characters that fit, then return 0 with ERROR_INSUFFICIENT_BUFFER; with `CP_SYMBOL` they write nothing. Microsoft Learn documents this for `WideCharToMultiByte` ("If cbMultiByte is less than cchWideChar, this function writes the number of characters specified by cbMultiByte", except `CP_SYMBOL`); the `MultiByteToWideChar` page says only that the call fails, and [MS-UCODEREF] 3.1.5.1.1.3 converts one character at a time. The engine depends on it: vgui2's `ConvertANSIToUnicode` passes `-1` with a buffer one character short on every developer notify line and draws the buffer, so writing nothing drew stale stack text (bug row 45). Self-check: `rt_kernel32_text_selftest` in `--up-selftest`.
- Shims, one file per area: `kernel32.c` (system, memory, strings, environment, debug output), `kernel32_file.c`, `kernel32_sync.c` (threads, TLS, critical sections, events, mutexes, semaphores, waits, Interlocked, time), `kernel32_module.c` (module registry, `GetProcAddress` by name, ordinal and forwarder, exceptions, the SEH frame dump), `advapi32.c` (registry), `user32.c`, `winmm.c`, `misc.c` (the GDI calls the engine's "loading" text uses, ole32 initialisation, Miles startup), `fail.c` (deliberate failures), `d3d9_null.c` and `dinput8_null.c` (null devices, below), `index.c` (the table list every lookup walks). Every shim that copies a guest-sized buffer checks it with `rt_span_ok(va, n)` (the span must end at or below `0x80000000`) and fails with `ERROR_NOACCESS` (998), or `D3DERR_INVALIDCALL`/`E_OUTOFMEMORY` in the null device, instead of faulting. `--run` makes a relative overlay absolute against the current directory, refuses any overlay with a `..` component, and refuses (exit 1) an overlay that resolves to the game folder or inside it, all before creating anything. Shim rows are `{dll, name, pop, fn, kind}`; `build_all.py` checks every hand-written pop against the plan's pop where the plan knows it.
- Tooling: `translate.py` passes `back_edge_yield=True` to the vendored `BatchTranslator` and writes each module's path, dependencies, TLS and Ghidra function names into `module.c`, and `lift_counts` into `report.json`; `build_all.py` adds the C4 inventory cross-check (`--inventory DIR`), the shim pop cross-check, `lift_counts` in `summary.json` (and fails if `lock.plain` is not 0), the `selftest/m3` build and `harness/m3.py cases`, and allows exactly one `libSDL3.0.dylib` in `otool -L`; `runtime/sources.txt` is the one source list both meson files read; the executable links with `-Wl,-stack_size,0x10000000`.

## Q1: null devices

`Direct3DCreate9` returning NULL stops the boot with the engine's own `MessageBoxA` "Unable to init shader system". Following the Q1 ruling, `shims/d3d9_null.c` is a logging null `IDirect3D9`: its objects live in guest memory and their vtables hold dynamic shim VAs, so `call [vtbl+N]` lands in a native function. Vtable order and argument counts are those of `d3d9.h` as recorded in apitrace's `specs/d3d9.py` (MIT), and the structure sizes those of `specs/d3d9types.py` and `specs/d3d9caps.py`. Only the 72 d3d9 methods the measured boot, close and 60 s pump runs reach have an implementation; every other vtable entry is a stub that stops by name (exit 10), so an untested method can never report success without filling its out-parameters. Textures, surfaces and buffers get real lockable memory; `D3DPOOL_DEFAULT` resources count against a 256 MiB budget and fail with `D3DERR_OUTOFVIDEOMEMORY` past it, because shaderapidx9 probes video memory by creating 256×256 textures until creation fails. `CheckDeviceFormat` accepts every format (the material system asserts on formats the device refuses).

`DirectInput8Create` failing is fatal in this engine: `CreateGameWindow` (engine `0x200FE5F0`) returns the input object's "initialised" byte, and `E_FAIL` leaves it 0, so the engine shuts down before its console. `shims/dinput8_null.c` therefore returns a null `IDirectInput8A`. The measured runs call only `Release` on it; every other method (CreateDevice included) is a stub that stops by name. The vtable order is that of `DECLARE_INTERFACE_(IDirectInput8A, IUnknown)` in `dinput.h`, read from `IDirectInput8A_Vtbl` in the Microsoft-generated windows-rs projection of that header (`crates/libs/windows/src/Windows/Win32/dinput/mod.rs`, MIT/Apache-2.0): IUnknown's three methods, then CreateDevice, EnumDevices, GetDeviceStatus, RunControlPanel, Initialize, FindDevice, EnumDevicesBySemantics, ConfigureDevices. Method semantics come from Microsoft Learn (DirectInput8Create, IDirectInput8::CreateDevice); the Learn interface page lists members alphabetically, so it is not the source of the order. This replaces the spec's "DirectInput8Create → E_FAIL" deliberate failure.

## Deliberate failures (measured on the boot path)

| Import | Returns | Why |
|---|---|---|
| `wsock32!#115` (WSAStartup) | `WSASYSNOTREADY` (10091) | no network; reached by launcher |
| `wsock32!#116` (WSACleanup) | `SOCKET_ERROR` | reached at shutdown after the failed startup (Microsoft Learn: WSACleanup, `WSANOTINITIALISED`) |
| `kernel32!CreatePipe`, `CreateProcessA` | FALSE, error 50 | msvcrt `_popen`/`system` from the engine; no child processes |
| `binkw32!_BinkOpen@8` | NULL | no movies (not reached with these flags) |
| `ole32!CoCreateInstance` | `REGDB_E_CLASSNOTREG` | no COM servers (not reached) |
| `user32!ChangeDisplaySettingsA` | `DISP_CHANGE_FAILED` | windowed only (not reached) |

## Results per acceptance criterion

Measured at the ready commit on master 5074699 (P1 merged), build `$DEV/build/m3d` (clean, new empty directory), `M = $DEV/venv/bin/python -I harness/m3.py`.

| AC | Result |
|---|---|
| AC1 | `M boot`: exit 0 in 8.2 s; `rt: stage 1`…`8` once each in order, one `stage 4 dllmain` per module started before LauncherMain (tier0, vstdlib, launcher, each = 1). Modules the engine loads later log `rt: dllmain <module> = 1` (19 more, all 1). |
| AC2 | `rt: stage 5 launcher-main 0x100239A0 args 0x00400000 0x00000000 0x30000026 1 "…"`; the first first-call row from launcher is `WSOCK32.dll!#115`. |
| AC3 | `rt: stage 7 engine-init writer engine+0xFA0DF (FUN_200fa030)` followed by the guest line `Unable to remove c:\vtmb\vampire\SAVE!`. |
| AC4 | `guest: M3_CONSOLE_READY` after stage 7 triggers stage 8; the line comes from engine.dll through `OutputDebugStringA`. `-condebug` makes the engine open `logs/console.log` (engine `Con_DebugLog`, `0x2003F220`, string at `0x2019CAFC`) through the filesystem on every console print: with `RT_TRACE_FILES=1` the run logs `CreateFileA "c:\vtmb\vampire\logs\console.log" access 0x40000000 disposition 4 -> 0xFFFFFFFF error 3`, because `Vampire\logs` exists neither in the game folder nor in a fresh overlay, and `CreateFileA` does not create parent folders (Windows answers the same `ERROR_PATH_NOT_FOUND`). With `files/Vampire/logs/` created in the overlay, the same run writes `files/Vampire/logs/console.log` (1,568 bytes, containing `M3_CONSOLE_READY`) and still exits 0 (the console's `echo` prints a trailing space; the marker match ignores trailing blanks). Mutation `-- +echo M3_OTHER`: stage 8 fires on `guest: M3_OTHER`. |
| AC5 | **Partly met.** `rt: stage 6 window 1280x1024`, `rt: window id <n>`; `screencapture -o -l <n>` gives 2560×2112 pixels (1280×1024 client plus the title bar on a 2× display), black client area. 1280×720 cannot be reached: the engine keeps only the modes in its table at `engine+0x1AD44C` (640×480, 800×600, 1024×768, 1280×1024, 1600×1200), so `-w 1280 -h 720` falls back to 1280×1024. The stage 6 line is written when the first top-level window is shown, after the engine's `SetWindowPos`, so it reports the real client size. |
| AC6 | `M close`: exit 0 in 11.1 s, `rt: message WM_CLOSE to 0x00010004`, then `WM_DESTROY`, `Wrote cfg/config.cfg`, `guest LauncherMain returned 0`; no forced quit. |
| AC7 | `M pump` (`--close-after 60`): `rt: max pump gap 18 ms`, exit 0. Gaps are counted once the first window exists, and the clock restarts after the runtime's own SDL window calls (window creation takes about 100 ms inside Cocoa). |
| AC8 | `M repeat --n 3`: 3 of 3 exit 0, 0 fault reports, no new `bloodlines-native*.ips`. |
| AC9 | first-call rows: 189–191 `shim`, 2 `fail` (`WSOCK32.dll!#115`, `KERNEL32.dll!CreatePipe`), 0 `stub`. Full list below. |
| AC10 | `m3.py cases --only threads`: spin-wait finishes in 0.056 s, under a 5 s host-thread watchdog that prints FAIL and exits 1 if the guest never yields (mutation: `xbox_GuestCpuPreempt` returning at once fails the case after 5 s); a TLS slot starts NULL in a new thread and holds 0x1111/0x2222 per thread; worker and primary static-TLS copies equal the template. Boot: one `rt: thread … created by` line per `CreateThread` call (3). |
| AC11 | `cases --only files`: PACK000.VPK bytes match; `M3TEST.CFG` lands in `files/Vampire/cfg/`; a write-open of `cfg/dummy.txt` makes an overlay copy and the game file's SHA-256 is unchanged; deleting a game-only file gives 5; `C:\VTMB\..\x` gives 3. |
| AC12 | `cases --only registry`: DWORD and string round-trip, missing value 2, short buffer 234 with size 15. Boot: the first run writes `HKEY_CURRENT_USER\Software\Troika\Vampire\Settings` (`ScreenWindowed`, `ScreenWidth`, `ScreenHeight`, `ScreenBPP` as DWORDs, `User Token 2`/`3` as strings); a second boot on the same overlay logs `reg query … hit` for all six (6 of 6). The `%s` is `Vampire`; the Python key `Software\Python\PythonCore\%s\Modules` is not queried. |
| AC13 | `cases --only modules` all pass (base, any path and case, DllMain once across two loads, name and ordinal, 126, 127). `X --probe-load 'bin\ENGINE.DLL' CreateInterface '#1' Nope`: handle `0x20000000`, `CreateInterface 0x200A22E0`, `#1 0x200FB1B0` (both equal `plan.json`), `Nope` 0 with 127; `nope.dll` 0 with 126. |
| AC14 | `cases --only exceptions`: `RaiseException(0x406D1388)` returns and logs `thread 0x100 named "m3 worker"`; `0xE06D7363` in a subprocess exits 12 with the code, "MSVC C++ throw", the SEH chain and the stack scan. The boot raises no exception. |
| AC15 | `cases --only time`: over `Sleep(1000)` GetTickCount, timeGetTime, QPC and rdtsc give 1.002, 1.002, 1.0023, 1.0023 s; QPC never decreases over 10,000 reads. |
| AC16 | `build_all.py --out $DEV/build/m3d` twice after the clean build: `0 generated files rewritten`, `ninja: no work to do.`, executable mtime unchanged. |
| AC17 | B ends `pass` (0 unbound, probes, harness incl. `race 2000000`, SIMD trap). M1 `build.py` into a new directory: `ALL PASS`, `native-results.json` sha256 `7d3e33d5…c1c1315`. `shasum -a 256 -c docs/game-manifest.txt` from `$GAME`: 0 failures; `find "$GAME" -newer <stamp>`: nothing. `git ls-files` has no generated C, overlay, log or TSV. |
| AC18 | C1, C2, C5, C6 are P1's (merged); `summary.json` `lift_counts`: lock 38 atomic, 0 plain; lahf 3 unimplemented, 0 idiom, 0 lifted. C3, C4 and C7 below. |

### C3: why the default `main` checks IAT slots and not dispatch entries

Every dispatch entry is generated from the same function list that produced the code, so the table cannot name a function that was not translated; what can be missing is a function discovery never found, and no static check can list those. They show up only when guest code reaches them, as a dispatch miss (exit 3) naming the target address. What checks dispatch is running code: `run.py check` and `run.py conform` (indirect calls counted, 0 misses required), the M3 harness cases, and the M3 boot itself, which reaches the console with 0 dispatch misses (any miss is a fault report with exit 3). IAT slots, by contrast, are data written at translate time, so `main` can and does check every one of them.

### C4

`build_all.py --inventory DIR` (default `docs/inventory`) fails with `check failed` (exit 1) when a stub name is not in any `imports-*.txt`. Mutation: a copy of `docs/inventory` without `shlwapi.dll!PathRemoveFileSpecA` prints `inventory missing: SHLWAPI.dll!PathRemoveFileSpecA` and exits 1.

### Back-edge cost

Executable 137,043,056 bytes (M2) → 140,198,256 with back-edge yield and no runtime change → 140,865,392 with the M3 runtime. Clean build into a new directory: 568 s (translate 125.5 s, build 426.5 s, ccache warm); M2's clean build was 725 s cold. Allocator high-water mark at the console: `0x1F170000` bytes (about 497 MiB), 0 failed allocations.

### First-call list (one `--until console` run)

Rows per kind: 189 shim, 2 fail, 0 stub.
- `advapi32.dll`: RegCloseKey, RegCreateKeyExA, RegOpenKeyA, RegOpenKeyExA, RegQueryValueA, RegQueryValueExA, RegSetValueExA
- `d3d9.dll`: Direct3DCreate9, IDirect3D9::CheckDepthStencilMatch, IDirect3D9::CheckDeviceFormat, IDirect3D9::CreateDevice, IDirect3D9::EnumAdapterModes, IDirect3D9::GetAdapterCount, IDirect3D9::GetAdapterDisplayMode, IDirect3D9::GetAdapterIdentifier, IDirect3D9::GetAdapterModeCount, IDirect3D9::GetDeviceCaps, IDirect3DCubeTexture9::GetCubeMapSurface, IDirect3DCubeTexture9::GetLevelCount, IDirect3DCubeTexture9::GetLevelDesc, IDirect3DCubeTexture9::GetType, IDirect3DDevice9::BeginScene, IDirect3DDevice9::Clear, IDirect3DDevice9::CreateCubeTexture, IDirect3DDevice9::CreateDepthStencilSurface, IDirect3DDevice9::CreateIndexBuffer, IDirect3DDevice9::CreatePixelShader, IDirect3DDevice9::CreateQuery, IDirect3DDevice9::CreateTexture, IDirect3DDevice9::CreateVertexBuffer, IDirect3DDevice9::CreateVertexDeclaration, IDirect3DDevice9::CreateVertexShader, IDirect3DDevice9::DrawIndexedPrimitive, IDirect3DDevice9::EndScene, IDirect3DDevice9::EvictManagedResources, IDirect3DDevice9::GetDepthStencilSurface, IDirect3DDevice9::GetRenderTarget, IDirect3DDevice9::Present, IDirect3DDevice9::SetDepthStencilSurface, IDirect3DDevice9::SetGammaRamp, IDirect3DDevice9::SetIndices, IDirect3DDevice9::SetMaterial, IDirect3DDevice9::SetPixelShader, IDirect3DDevice9::SetPixelShaderConstantF, IDirect3DDevice9::SetRenderState, IDirect3DDevice9::SetRenderTarget, IDirect3DDevice9::SetSamplerState, IDirect3DDevice9::SetStreamSource, IDirect3DDevice9::SetTexture, IDirect3DDevice9::SetTextureStageState, IDirect3DDevice9::SetTransform, IDirect3DDevice9::SetVertexDeclaration, IDirect3DDevice9::SetVertexShader, IDirect3DDevice9::SetVertexShaderConstantF, IDirect3DDevice9::SetViewport, IDirect3DDevice9::ShowCursor, IDirect3DDevice9::TestCooperativeLevel, IDirect3DIndexBuffer9::GetDesc, IDirect3DIndexBuffer9::Lock, IDirect3DIndexBuffer9::Unlock, IDirect3DQuery9::Issue, IDirect3DQuery9::Release, IDirect3DSurface9::AddRef, IDirect3DSurface9::LockRect, IDirect3DSurface9::Release, IDirect3DSurface9::UnlockRect, IDirect3DTexture9::GetLevelCount, IDirect3DTexture9::GetLevelDesc, IDirect3DTexture9::GetSurfaceLevel, IDirect3DTexture9::GetType, IDirect3DTexture9::Release, IDirect3DVertexBuffer9::Lock, IDirect3DVertexBuffer9::Unlock
- `dinput8.dll`: DirectInput8Create
- `gdi32.dll`: CreateFontA, DeleteObject, GetStockObject, SelectObject, SetBkMode, SetTextColor
- `kernel32.dll`: CloseHandle, CompareStringA, CompareStringW, CreateDirectoryA, CreateEventA, CreateFileA, CreatePipe (fail), CreateThread, DeleteFileA, DisableThreadLibraryCalls, EnterCriticalSection, FileTimeToLocalFileTime, FileTimeToSystemTime, FindClose, FindFirstFileA, FindNextFileA, FreeEnvironmentStringsA, GetACP, GetCommandLineA, GetCommandLineW, GetCPInfo, GetCurrentDirectoryA, GetCurrentProcessId, GetCurrentThreadId, GetEnvironmentStrings, GetEnvironmentStringsW, GetFileType, GetFullPathNameA, GetLastError, GetModuleFileNameA, GetModuleHandleA, GetProcAddress, GetStartupInfoA, GetStdHandle, GetStringTypeA, GetStringTypeW, GetSystemInfo, GetSystemTimeAsFileTime, GetTimeZoneInformation, GetVersion, GetVersionExA, GlobalMemoryStatus, HeapAlloc, HeapCreate, HeapFree, HeapReAlloc, HeapSize, InitializeCriticalSection, InterlockedDecrement, InterlockedExchange, InterlockedIncrement, IsProcessorFeaturePresent, LCMapStringA, LCMapStringW, LeaveCriticalSection, LoadLibraryA, MultiByteToWideChar, OutputDebugStringA, QueryPerformanceCounter, QueryPerformanceFrequency, ReadFile, ResumeThread, SetConsoleCtrlHandler, SetCurrentDirectoryA, SetEnvironmentVariableA, SetFilePointer, SetHandleCount, SetLastError, SetUnhandledExceptionFilter, Sleep, TlsAlloc, TlsGetValue, TlsSetValue, VirtualAlloc, WaitForSingleObject, WideCharToMultiByte, WriteFile
- `mss32.dll`: _AIL_set_redist_directory@4, _AIL_startup@0
- `ole32.dll`: CoInitialize, OleInitialize
- `user32.dll`: AdjustWindowRectEx, ClientToScreen, CreateWindowExA, DefWindowProcA, DispatchMessageA, DrawTextA, EnumChildWindows, EnumWindows, FillRect, GetClientRect, GetCursorPos, GetDC, GetParent, GetSystemMetrics, GetWindowLongA, LoadCursorA, LoadIconA, LoadStringA, PeekMessageA, RegisterClassA, RegisterWindowMessageA, ReleaseDC, ScreenToClient, SetCursor, SetCursorPos, SetForegroundWindow, SetWindowLongA, SetWindowPos, TranslateMessage, UnregisterClassA, wsprintfA
- `wsock32.dll`: #115 (fail)

## Sources (M3)

Microsoft Learn, by API family: heap (HeapCreate, HeapAlloc, HeapReAlloc, HeapFree, HeapSize, HeapValidate, HeapCompact, HeapWalk, GlobalAlloc, GlobalFree, GlobalMemoryStatus); virtual memory (VirtualAlloc, VirtualFree, VirtualQuery, MEMORY_BASIC_INFORMATION); files (CreateFileA, ReadFile, WriteFile, SetFilePointer, GetFileSize, GetFileTime, GetFileAttributesA, GetFileInformationByHandle, FindFirstFileA, FindNextFileA, FindClose, WIN32_FIND_DATAA, GetFullPathNameA, GetCurrentDirectoryA, SetCurrentDirectoryA, CreateDirectoryA, RemoveDirectoryA, DeleteFileA, MoveFileA, GetDriveTypeA, GetDiskFreeSpaceA, GetStdHandle); synchronisation (InitializeCriticalSection, EnterCriticalSection, LeaveCriticalSection, DeleteCriticalSection, CreateEventA, SetEvent, ResetEvent, CreateMutexA, ReleaseMutex, CreateSemaphoreA, ReleaseSemaphore, WaitForSingleObject, WaitForMultipleObjects, MsgWaitForMultipleObjects ("WAIT_OBJECT_0 + nCount" means input is available; WAIT_TIMEOUT when the interval elapses), InterlockedIncrement, InterlockedDecrement, InterlockedExchange, CloseHandle, DuplicateHandle); threads and TLS (CreateThread, ExitThread, TerminateThread, ResumeThread, TlsAlloc, TlsFree, TlsGetValue, TlsSetValue, Sleep); modules (LoadLibraryA, LoadLibraryExA, GetProcAddress, FreeLibrary, GetModuleHandleA, GetModuleFileNameA, DisableThreadLibraryCalls); registry (RegCreateKeyExA, RegOpenKeyExA, RegOpenKeyA, RegQueryValueExA, RegQueryValueA, RegSetValueExA, RegEnumKeyExA, RegQueryInfoKeyA, RegCloseKey); strings and locale (MultiByteToWideChar, WideCharToMultiByte, CompareStringA, LCMapStringA, GetStringTypeA, GetCPInfo, lstrcpyA, lstrcatA, lstrlenA); time (QueryPerformanceCounter, QueryPerformanceFrequency, GetTickCount, timeGetTime, GetSystemTimeAsFileTime, GetLocalTime, FileTimeToLocalFileTime, FileTimeToDosDateTime, GetTimeZoneInformation); system (GetSystemInfo, GetVersionExA, OutputDebugStringA, GetEnvironmentVariableA, SetEnvironmentVariableA); window and GDI (RegisterClassA, CreateWindowExA, DestroyWindow, ShowWindow, SetWindowPos, GetClientRect, PeekMessageA, GetMessageA, DispatchMessageA, SendMessageA, PostMessageA, PostQuitMessage, DefWindowProcA, GetWindowLongA, SetWindowLongA, EnumWindows, EnumChildWindows, GetSystemMetrics, MessageBoxA, wsprintfA, wvsprintfA, ChangeDisplaySettingsA, GetDC, GetStockObject, CreateFontA, SelectObject, DrawTextA); COM and DirectX (CoInitialize, CoCreateInstance, Direct3DCreate9, IDirect3D9, IDirect3DDevice9, D3DADAPTER_IDENTIFIER9, DirectInput8Create, IDirectInput8 interface for semantics, `dinput.h` via windows-rs for the vtable order); sockets (WSAStartup, WSACleanup); processes (CreatePipe, CreateProcessA); exceptions (RaiseException, SetUnhandledExceptionFilter); plus CreateThread, TlsAlloc ("the slots for the index are initialized to zero"), WaitForSingleObject, WaitForMultipleObjects, Sleep, QueryPerformanceFrequency, CreateFileA, RegQueryValueExA ("If the lpData buffer is too small … ERROR_MORE_DATA"), CreateWindowExA, GetMessageA, PeekMessageA, WM_CLOSE, DefWindowProcA, RaiseException, "Set a thread name by throwing an exception", DllMain, GetProcAddress (ordinal in the low word), Direct3DCreate9, DirectInput8Create, WSAStartup, WSACleanup ("Otherwise, the value SOCKET_ERROR is returned"), CreatePipe, System Error Codes (0–499), Common HRESULT values. SDL wiki via Context7 (`/libsdl-org/sdlwiki`): SDL_PumpEvents, SDL_WaitEventTimeout, SDL_GetWindowID and SDL_GetDesktopDisplayMode ("should only be called on the main thread"; the window ID is stored atomically on the main thread and the desktop size is read once at SDL_Init, so the watchdog and other threads only call SDL_PushEvent), SDL_PushEvent ("safe to call this function from any thread"), FAQDevelopment; `SDL_video.h` 3.4.10 (`SDL_PROP_WINDOW_COCOA_WINDOW_POINTER`). Meson via Context7 (`/mesonbuild/meson`): Dependencies, "Dependency detection method" (pkg-config first), `fs.read`. `man ld` (`-stack_size`: "Without this option a program has a 8MB stack"), `man pthread_attr_setstacksize`, `man clock_gettime`, `man 2 rename`, `man screencapture`. apitrace `specs/d3d9.py` and `specs/d3d9caps.py` (MIT) for the d3d9 vtable order and `D3DCAPS9` layout. xboxrecomp `src/kernel/kernel_guest_cpu.c` (MIT) for the guest-lock scheme; MacNativeGames `games/totk/runtime/hle_stack.c` (MIT) for the first-call trace pattern. Microsoft Learn (previous-versions, DirectSound): `DirectSoundCreate`, `IDirectSound::CreateSoundBuffer`, `IDirectSound::GetCaps`, `IDirectSound::SetCooperativeLevel`, `IDirectSoundBuffer::GetCaps`, `GetCurrentPosition`, `GetStatus`, `Lock`, `Play`, `Restore`, `SetFormat`, `Stop`, `Unlock`, and "DirectSound Return Values"; the vtable order, `DSCAPS`, `DSBCAPS`, `DSBUFFERDESC` and the flag values from windows-rs 0.58 (`Win32/Media/Audio/DirectSound`, MIT). SDL wiki via Context7 (`/libsdl-org/sdlwiki`): `SDL_AudioStreamCallback`, `SDL_OpenAudioDeviceStream`, `SDL_SetAudioStreamGetCallback`. No Wine source was read.

## Known limits (M3)

- The x87 tag word is derived from the stack depth (registers from TOP to 7 are in use), as the lifter's model has no tags; `ffree` changes nothing and a full 8-register stack reads as empty.
- `fnstenv`/`fnsave` write 0 for FIP and FDP (the last instruction and operand pointers).
- The vendored fprem helper (`third_party/xboxrecomp/runtime/recomp_types.h:502`) overwrites `g_fp_cc` whole and so drops a sticky ZE (42 sites); a wrapper in `gen_prefix.h` can preserve the exception bits if a case ever needs it.
- No guest SEH handler runs: `0x406D1388` returns, any other `RaiseException` and any `RtlUnwind`/`UnhandledExceptionFilter` stop with exit 12. The boot raises none.
- The window is 1280×1024, not 1280×720 (the engine's mode table); nothing is drawn (null Direct3D device, M4).
- No input devices: the null `IDirectInput8A` implements only `Release`; SDL keyboard and mouse events are dropped (M5).
- The engine's own CPU clock estimate (`Clock speed: … Mhz` in the log) varies between runs (105 MHz to 4290 MHz measured); rdtsc runs at a fixed 2 GHz from the same clock as QPC, so the estimate comes from how the engine times its probe under the guest lock, and is left to M4.
- `-condebug` writes no console log into a fresh overlay: the engine appends to `Vampire\logs\console.log`, whose folder the vanilla game does not ship, so every open fails with error 3 as on Windows. Create `<overlay>/files/Vampire/logs/` to get the log.
- Window frames are not modelled: `AdjustWindowRect(Ex)` returns the rectangle unchanged and `GetWindowRect` equals the client area.
- `GlobalMemoryStatus` reports `0x7FF00000` bytes of physical memory, just under 2 GiB: with exactly 2 GiB the engine reads the value as a signed `int` and stops with "Available memory less than 15MB!!! -2147483648".

# M4: First picture

M4 replaces M3's logging null Direct3D device with a real one. The game's own `d3d9.dll` calls cross a hand-written bridge (`shims/d3d9_bridge.c`) into dxvk-native, which draws through Vulkan on Mesa's KosmicKrisp driver on Metal 4. The original engine draws its main menu (gold logo, New Game, Load Game, Options, Quit, blurred background, particles) in the SDL3 window. There is no Wine, CrossOver or Rosetta in the path, and every host binary is arm64. The aim is a native runner for Apple Silicon Macs. On first sight of the menu the maintainer found it ran smoothly.

Measured at master `c2fef02` plus the harness commits of this piece, build `$DEV/build/m4p3` (clean, new empty directory, 802 s), on an M1 Max (24-core GPU), macOS 26.6.1, built-in Liquid Retina XDR connected, ultrawide not connected. `M = $DEV/venv/bin/python -I harness/m4.py`, `X = $DEV/build/m4p3/meson/bloodlines-native`.

## Running it

```sh
X=$DEV/build/all/meson/bloodlines-native
$X --run [--gfx dxvk|null] [--until console|menu] [--display N] [--capture-after S] [--guest-quiet] [--close-after S] [--overlay DIR] [--game DIR] [-- EXTRA ARGS]
$X --gfx-selftest [--overlay DIR]
M selftest | cases | boot | menu | soak | displays | compare PNG REF --mask JSON [--tol N --luma L --block N --block-frac F --pct P --mad M] | licences --log RUNLOG
```

- `--gfx dxvk` (default) loads the bridge and dxvk. `--gfx null` keeps M3's null device, loads nothing from Vulkan and creates no Vulkan window. The stage 1 line prints the mode.
- `--until menu` exits 0 at max(5, S + 2) s after stage 11, where S is `--capture-after` (5 s without it). `--capture-after S` writes `<overlay>/shots/menu-<UTC>.png` from the back buffer in the first Present S seconds after stage 11 (`GetRenderTargetData` into a system-memory surface, then `SDL_SavePNG`). `--close-after` still counts from stage 8. `--display N` puts the window on SDL display N and refuses (exit 1) when it does not exist.
- The default argument list is `-window -w 1280 -h 1024 -dxlevel 90 -nosound -nojoy -noip -nolan -dev -condebug +mm_verbose 1 +echo M3_CONSOLE_READY`: M3's list with `-console` removed so the console does not cover the menu, `-h 1024` as M3 measured, and `-dxlevel 90` and `+mm_verbose 1` added. `--guest-quiet` drops `+mm_verbose 1` and `-condebug`; its stage 1 line carries `guest-quiet` and stage 11 does not fire.
- Environment the runtime sets before the window exists, each without overwriting a value that is already set (logged as `rt: gfx env NAME=value (set|kept)`): `DXVK_WSI_DRIVER=SDL3`; `VK_DRIVER_FILES` (the KosmicKrisp ICD, `/opt/homebrew/opt/mesa/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json`); `DXVK_CONFIG="d3d9.memoryTrackTest = True; d3d9.maxAvailableMemory = 1024; d3d9.deferSurfaceCreation = True"` (the fork's own Bloodlines profile matches `\vampire.exe$` and cannot match this binary); `MESA_KK_DEBUG=force_robustness`; `DXVK_LOG_PATH=<overlay>/logs`; `DXVK_SHADER_CACHE_PATH=<overlay>/cache/dxvk`; `MESA_SHADER_CACHE_DIR=<overlay>/cache/mesa`, so an uninstall leaves nothing in `~/.cache` and a scratch overlay really starts cold; and the SDL hint `SDL_HINT_VULKAN_LIBRARY` = `/opt/homebrew/opt/vulkan-loader/lib/libvulkan.1.dylib`. The dxvk library is opened with `dlopen` from `RT_DXVK_LIB`, else, for a dev build outside an `.app`, `$BN_DEV_HOME/dxvk/inst/lib/libdxvk_d3d9.0.dylib` (`BN_DEV_HOME` defaults to `~/Library/Caches/BloodlinesPorter/dev`). The harness (`tools/devpaths.py`) sets `RT_DXVK_LIB=$DEV/dxvk/inst/lib/libdxvk_d3d9.0.dylib` unless it is already set; set it yourself for a hand run. `RT_GFX_CHECK=1` turns on the bridge's extra checks.
- `<overlay>/files/Vampire/vidcfg.bin` is deleted, with a log line, when its device ID is 16 zero bytes (the null device's), so the engine re-detects the real device.
- The run is refused (exit 1) only when the dxvk library, `libvulkan.1.dylib` or the KosmicKrisp manifest is missing. That the loaded driver is KosmicKrisp is checked after stage 9 from the loaded images; a failure there is exit 15, as is a device-lost state or a blocked bridge call over 5 s in a dev run and over 120 s in a player build (a slow cold shader start is not a hang there). A d3d9 call from any thread but the guest primary thread stops with exit 5.

## How it works

- **Bridge.** One hand-written file, no generator: `Direct3DCreate9` plus the 72 methods that a measured boot reaches through the main menu. Every other vtable row keeps stopping by name (exit 10), as in M3. Slot order comes from `d3d9_null.c`'s tables (0 differences from windows-rs); declarations come from Microsoft Learn and windows-rs at commit `61f973beb0ce06590d6bbefceb236fc50de82729`. No dxvk header is included in our code. A guest vtable slot is a shim address; the bridge calls the host object's vtable slot with the plain arm64 C ABI, because the fork defines `STDMETHODCALLTYPE` as empty (`include/native/windows/windows_base.h:205-206` in the fork).
- **Proxies.** Each host object gets one 16-byte guest proxy found again through a host-pointer table, so the same object gives the same proxy. `AddRef` and `Release` go to dxvk, whose count is the only count.
- **Structures.** `D3DPRESENT_PARAMETERS` 56 to 64 bytes (`hDeviceWindow` moves from offset 28 to a pointer at 32), `D3DLOCKED_RECT` 16 to 8 bytes, `D3DADAPTER_IDENTIFIER9` 1,100 bytes copied from a 1,104-byte host local; the rest cross in place after `rt_span_ok`. `_Static_assert` pins the guest sizes 56, 8, 1,100 and 304.
- **Lock memory.** The guest never sees a dxvk pointer (they lie outside the arena). Each lock hands out a bounce block inside the arena; flags and RECT go to dxvk unchanged. Fill rules: a buffer's block is always filled from dxvk, since `Unlock` writes the whole range back and dxvk ignores DISCARD in some cases. A buffer lock takes up to 2 KiB (`LOCK_SLACK`) more than asked, capped at the buffer's end, and copies that room both ways: the engine locks room for one quad and writes four (a 128-byte vertex lock written 496 bytes, a 12-byte index lock written 48, from `shaderapidx9` `sub_2A015B80` and `sub_2A0159C0`). On Windows those bytes land in the real buffer; in the bridge they were lost, and stale indices drew the black, red and textured wedges over cutscene captions and bars. `RT_GFX_CHECK=1` logs `gfx lock overrun` for writes past the room and `gfx late write` for stores into a freed block; an image's block is not filled only for DISCARD without NOOVERWRITE, `D3DPOOL_DEFAULT` and a whole-level lock (no RECT, or RECT equal to (0, 0, Width, Height)); fork `src/d3d9/d3d9_device.cpp` lines 4958-4971 and 5006-5029. `Unlock` copies back unless the lock was READONLY. `LockRect` rejects a RECT outside the surface with `D3DERR_INVALIDCALL`. Surfaces are copied row by row with dxvk's pitch.
- **Windows.** In dxvk mode the game window is created with `SDL_WINDOW_VULKAN`; a guest `HWND` is that `SDL_Window *` (fork `include/native/wsi/native_sdl3.h`). `DestroyWindow` on the device window only hides it: destroying it freed the `CAMetalLayer` under dxvk's live swapchain and crashed in `_dispatch_semaphore_dispose`. Other windows are still destroyed.
- **Fixed swapchain.** The dxvk fork (`third_party/dxvk/0001-fix-d3d9-fixed-swapchain-at-back-buffer-size-subopti.patch`, applied to `~/Library/Caches/BloodlinesPorter/dev/vendor/dxvk`) creates the windowed swapchain at the back-buffer size and treats `VK_SUBOPTIMAL_KHR` as success, so a window resize no longer recreates the swapchain or starves the `CAMetalLayer` `nextDrawable` pool. Mesa 26.2.5 Metal WSI allows this: min image extent is {1,1}, and `queue_present` returns SUBOPTIMAL when the layer size differs from the chain extent. The bridge sets the game layer to `kCAGravityResizeAspect` over a black background (`rt_gfx_layer_fit`) and SDL does not auto-resize the drawable (`SDL_HINT_VIDEO_METAL_AUTO_RESIZE_DRAWABLE=0`), so Core Animation scales the picture and draws the bars; the bridge no longer passes a letterbox rectangle to the host Present. A windowed `0x0` back buffer takes the guest window size (`rt_gfx_pp_size`). `--gfx-selftest` runs `resize_cases`: five window sizes with 30 presents each and no swapchain recreation.
- **Present pacing.** The game asks for `D3DPRESENT_INTERVAL_IMMEDIATE` and caps itself at `fps_max 65` (the patch's `autoexec.cfg`). Unsynced on a 120 Hz screen, each frame stayed 8, 16 or 25 ms while the game stepped 16 ms, which reads as motion going back and forward. So the game layer is paced by a `CAMetalDisplayLink` at 60 Hz with `preferredFrameLatency` 1, on its own run-loop thread: `rt_gfx_layer_fit` replaces `-[CAMetalLayer nextDrawable]` so that KosmicKrisp's acquire waits for the link's next update and takes that update's drawable, and the frame goes out with plain `present` (KosmicKrisp calls `present`, Mesa is not ours to patch). While a link is attached to a layer, that layer's own `nextDrawable` throws `CAMetalLayerInvalidOperation`, with the link running or paused, and works again after `invalidate` (a minimal sample, `linkprobe`). So when the window is hidden or no update came for 150 ms, the acquire first stops the link (`invalidate` on the link's thread, logged `gfx display link stopped: window hidden` or `no update for 150 ms`; the first update of the next link is logged `gfx display link back N ms after giving up`, and a fallback plain acquire of 100 ms or more is logged) and only then takes a plain drawable; the next layer fit with the window visible attaches a new link to the same layer once no plain acquire is in flight (`gfx display link restarted`). The exit line counts the starts, stops and plain acquires. Apple: "Using alternative methods to present() that target the presentation for a specific time cause an assert when used with a CAMetalDisplayLink" (`metalDisplayLink(_:needsUpdate:)`); `presentAtTime:` threw, and `presentAfterMinimumDuration:` on a link drawable stopped the link after one update. Measured without any screen capture running, from the game's drawable request to the panel (`presentedTime`), `sm_hub_1` walk: ultrawide (fixed 120 Hz) 41.4 ms with 98.8 to 99.8 % of frames on the 60 Hz beat; built-in (ProMotion) frames shown exactly on the link's target. The earlier `presentAfterMinimumDuration:1/60` pacing took 48.9 ms with 2 drawables and 57 to 65 ms with 3, because the game ran ahead into that queue; pacing the game with a plain `CADisplayLink` tick in front of the same minimum kept a standing queue (65.5 ms). Known limit: in a window on the ultrawide, the window server sometimes takes a waiting frame one refresh early in bursts of about 1 s; a minimal `CAMetalDisplayLink` sample with nothing of the game in it bursts the same way (91 to 93 % of frames on the beat with `displaySyncEnabled` YES, 55 to 63 % with NO), the game 97.7 to 99.6 %; plain `present` is "Present this drawable as soon as possible" (MTLDrawable.h), and no documented call holds a link drawable. `RT_PACE=min` restores the 1/60 minimum pacing, `RT_PACE=0` turns pacing off. `RT_FX_LINK=1` puts the link on the MetalFX layer and `RT_FX_LINK=0` keeps it on the game layer, overriding the per-mode choice below, for side-by-side measurements in the same build. Per mode: a full-screen launch without FIFO (any built-in launch, or a fixed-refresh launch with MetalFX on) keeps the link on the game layer for the whole session and the MetalFX layer on `presentDrawable:afterMinimumDuration:` 1/60, the arrangement of the earlier builds; every other session moves the link to the MetalFX layer while MetalFX is live (`fx_link_wanted`). Why: on the ultrawide in full screen with MetalFX, the MetalFX-layer link had no 1 s stall in about 12 cells and 9 to 25 ms less latency, but the compositor showed a frame one 120 Hz refresh late about once a second although it was ready 11.6 ms before its target (on the beat 94.8 to 99.7 % and 1 % low 40 in 5 of 6 cells, against the installed app's 99.2 to 100 % and 60); hiding the covered game view did not change that, and a game-layer link with FIFO stalled 53 times in one cell after six clean ones. Where the link moves, the MetalFX layer is the one on screen: the window server skips the covered game layer's drawables (`presentedTime` 0 in 2,886 of 3,743, 2,434 of 3,720 and 3,742 of 5,436 link frames in three full-screen cells), and a link on that layer then sent no update for 1 s at a time, 28 times in one FIFO run. The game thread still waits for each update in `link_next`, hands it to the fx thread and takes a plain game drawable (no link is on the game layer then); the fx thread scales into the update's drawable and presents it plainly, and covered game drawables go out with plain `present`; while the MetalFX layer is live and holds the link, the covered game view is hidden (`gfx covered game view hidden`), and the MetalFX pass still reads its drawables' textures on the GPU. Why: with the covered game view shown, on full-screen entry and after every ⌘-Tab return, the covered game layer's `nextDrawable` waited its full second (Apple, `nextDrawable()`: "If all drawables are in use, the layer waits up to one second for one to become available") while none of its drawables was alive in our process: the system kept the layer's three most recently presented surfaces and never gave them back, and after the timeout the layer made one new surface, which the system returned at once for every later frame (game-layer acquire trace, 7 of 7 holds). The holds began 0.87 to 1.50 s after the window turned visible; no notification marks that moment (the active-Space change came 0.10 to 0.24 s after visible, the occlusion change 0.01 to 0.16 s before it, the first MetalFX frame and the first shown covered drawable within 0.05 s), and a minimal `CAMetalDisplayLink` app came back without a gap. Not presenting covered drawables at all cost frames on the beat in steady full screen (93.4 and 93.7 % against 94.7 and 97.2 %), and not presenting them for the first 180 link updates (3 s) after a link start only moved the wait (one return held 5.24 s after it turned visible, one full-screen entry held 711 ms). With the covered view hidden, 3 of 3 returns had no acquire over 100 ms, also while away, with every covered drawable presented, and steady full screen was unchanged (93.7 and 94.2 % against 94.3 and 93.8 % on the beat, interleaved in one run; window 60.0 FPS, 99.7 %). The Metal HUD reads `Direct` in both. After a hold the old arrangement ran smoother (99.2 to 99.7 % on the beat against 95.9 to 96.7 %, the covered layer then on one surface), a state the hidden view does not reach; that gap is still open. When the guest destroys its main window, the link is told at once (`gfx window gone, unpaced`), so quitting no longer waits out the 1 s give-up. A Picture change stops the link from the game thread in `rt_fx_frame`: the game's primary thread is the main thread, so waiting for the main-queue layout instead blocked it for the 1 s give-up (gfx self-test, 5 of 5 changes); the next layer fit attaches the link to the other layer. When MetalFX is switched on in a session whose link stays on the game layer (a full-screen MetalFX launch without FIFO), the MetalFX view now covers that layer. A link that runs across that moment gets no update for 1 s, while one started after it runs clean. In the maintainer's session, 7 of 9 one-second freezes followed a ⌘2 into MetalFX, and each restarted link ran without a stall until the next toggle. So `layout_main` stops the link when it shows the MetalFX view over a game-layer link (`gfx display link stopped: MetalFX view now covers the game layer`), and the next layer fit, within 250 ms, starts it under the cover. A plain acquire on a layer without the link, on the relay path or for another layer, counts as in flight like the fallback acquire, so `link_start` waits for it before attaching a link to that layer; without that, the original `nextDrawable` could run on a layer that had just got a link and throw `CAMetalLayerInvalidOperation` ("-nextDrawable should not be called when using CAMetalDisplayLink", gfx self-test). MetalFX in a window went from 83.0 to 49.7 ms ask-to-panel on the built-in and from 83.1 to 41.4 ms on the ultrawide, at 60 FPS. A built-in full-screen launch keeps the game-layer link because the MetalFX-layer link there meets the built-in halving described above (47.8 and 46.9 FPS against 58.7), unless the launch screen is drawn larger than its panel: then the swapchain is FIFO, the MetalFX layer is synced and the link moves to it like any other FIFO session. A window launched on any variable-refresh screen gets the same FIFO and synced MetalFX layer. On the built-in at 1496x967 (framebuffer smaller than the panel), a MetalFX window went from 69.9 to 74.3 % to 99.7 to 100 % of frames on the 60 Hz beat at 60 FPS, for one link refresh of latency (49.7 to 66.4 ms). A bare `CAMetalDisplayLink` window on that screen, synced, reached 99.4 to 100 %. Full screen in that mode stays as it is. There FIFO ran 39.9 and 57.8 FPS with MetalFX and 34.7 with Picture off (frames of 25 and 33.3 ms), against the unsynced 41.9 to 59.7 FPS with 45 to 78 % on the beat and up to eight 1 s stalls; that full-screen judder is still open. A window launch switched to full screen keeps the MetalFX-layer link: moving the link back to the covered game layer at the switch stalled it 40 times in one built-in cell. The game-layer link's rare stalls under MetalFX in full screen (the covered layer's drawables are skipped, 0 to 2 per cell in the installed app; the maintainer's own session on the ultrawide had 5 in about 7 minutes) come from the system: the link stops calling `needsUpdate` while nothing of ours is outstanding (0 live drawables, its surfaces free, no second update queued), after steady 16.7 ms frames as often as after long ones (22 of 27 traced stalls), so no game-side trigger exists. The give-up was 1 s, which made each one a 1 s freeze; it is now 150 ms. Across 402,242 traced link frames a successful wait was longer than 150 ms twice (198 ms at the first frame after launch, and one near-stall), and steady play peaked at 68.5 ms, so the shorter give-up does not fire in normal play. When no link is on it, the MetalFX layer keeps `presentDrawable:afterMinimumDuration:` 1/60. `rt_gfx_layer_fit` sets a `maximumDrawableCount` of 3 (Apple's default) on the layers once. No layer property changes after that: Mesa sets `displaySyncEnabled` to NO for dxvk's IMMEDIATE mode when it configures the swapchain and again on each present that carries a present-mode struct (`wsi_common.c`, `wsi_metal_layer_set_immediate`; the trace saw it set NO again after each of our 81 YES writes in 20 s), and when we set it back to YES, each flip while drawables waited in `presentAfterMinimumDuration:` made Core Animation drop the drawable pool, so the window showed black for a frame, about 3 times a second (all 67 black frames and all 133 new drawable textures in a 20 s trace came within 100 ms after a flip). A hidden/visible change of `maximumDrawableCount` rebuilt the pool the same way. A swapchain recreation makes Mesa set the count back to the image count, so the fit sets 3 once more: one rebuild per recreation, which the fixed swapchain makes rare. With `RT_PACE=min`, the paced pipeline needs three drawables at once: one on screen, one waiting out its 1/60 s minimum, and the one KosmicKrisp acquires right after it presents. With 2, nearly every acquire found both busy (3,136 of 3,173 in one `sm_hub_1` load) and waited for the pending one; during the load's present bursts Core Animation handed out a new drawable instead (12 new textures, up to 4 in use), and black frames followed (7 in one load). With 3 there were no new textures beyond the first three and no black frame after the window's first one; the cost is one more queued frame, a median of 37 ms instead of 21 ms from KosmicKrisp's copy to the screen. Pacing is unchanged: walking on the ultrawide, three runs each, gave a presented-time spread of 1.00, 1.33 and 1.32 ms with 2 drawables and 1.33, 1.20 and 1.25 ms with 3, 98.4 to 99.0 % of frames on the 60 Hz beat in all six. The swapchain is FIFO when the game launches in full screen on a screen with a fixed refresh rate with Picture mode off, or launches in any mode on a variable-refresh screen whose framebuffer is wider than its panel (the current display mode's `CGDisplayModeGetPixelWidth` against the widest mode carrying `kDisplayModeNativeFlag`, logged as `gfx launch screen framebuffer … px wide, panel … px`), and IMMEDIATE otherwise (`rt_gfx_vsync`, decided once at `CreateDevice` from the saved window state, the Picture mode and the launch screen's `NSScreen` `minimumRefreshInterval` and `maximumRefreshInterval`; `host_interval` in `shims/d3d9_bridge.c` hands dxvk `D3DPRESENT_INTERVAL_ONE` at `CreateDevice` and `Reset` and gives the game back its own value). Mesa sets `displaySyncEnabled = !immediate` on the layer from the present mode (`wsi_common_metal_layer.m` 140), and our MetalFX layer takes the same value. On the ultrawide (8.33 ms, fixed), measured with no capture running, installed app against FIFO, interleaved cells: on the beat 87.9 and 91.5 % against 94.2 to 100 % (7 cells), FPS 57.7 against 58.2 to 60.0, ask-to-panel p99 40.7 against 32.7 to 49.3 ms; MetalFX p50 51.6 → 32.5 ms, p90 58.1 → 33.1 ms; windows unchanged. On the built-in (8.33 to 41.67 ms, variable) FIFO ran at 30 Hz in 9 of 9 cells with no capture (31 to 33 FPS, 66 ms, against the installed app's 38 to 48 FPS): the game presents 16 ms before the link's deadline, yet each frame is shown one refresh after `targetPresentationTimestamp`, so at frame latency 1 the link sends an update every 33.3 ms. Instruments (Metal System Trace, table `displayed-surfaces-interval`) shows the game's surfaces composited ("Direct to Display: No", no reason given) at 4:3 and at native size, while the panel swaps at 12.5 to 41.7 ms. A window shot flips it to 60 Hz for a while, which is how an earlier 57.6 FPS was measured. So the built-in keeps IMMEDIATE, and full screen there is still open. Keeping IMMEDIATE and setting the layer's `displaySyncEnabled` to YES stayed at 30 FPS (about 66 ms). Those built-in numbers were taken with the display set to 1496x967 points, a 2992x1934 framebuffer the panel scales up to its 3456x2234 pixels. With the display at 2056x1329 points (a 4112x2658 framebuffer the panel scales down), the same refresh range of 8.33 to 41.67 ms behaves the other way, interleaved cells with no capture, installed app against FIFO: full screen with Picture off 58.2 and 58.4 FPS, 88.1 and 89.6 % on the beat, 1 % low 39.5 and 36.7, against 59.8 and 59.9 FPS, 98.7 and 96.9 %, 60.0 and 48.0, ask-to-panel 32.5 against 32.7 ms; full screen with MetalFX 58.2 to 59.2 FPS, 83.9 to 89.8 % (frames shown one 120 Hz refresh early, p10 −12.9 ms) against 59.0 to 59.7 FPS, 93.6 to 97.5 % (every frame on the link's target), 0 link stalls in both; a window with Picture off 57.0 FPS, 20.0 and 32.6 %, 1 % low 26.5, against 60.0 FPS, 99.2 %, 60.0, at 16.5 ms more ask-to-panel; a window with MetalFX 56.4 and 57.5 FPS, 14.1 and 16.8 %, against 60.0 FPS, 99.2 to 99.6 % with both the game swapchain and the MetalFX layer synced (syncing the MetalFX layer alone reached only 30.7 and 49.0 %). A minimal `CAMetalDisplayLink` sample in a window on that mode shows the same unsynced judder (44.9 and 54.0 % with `displaySyncEnabled` NO, 95.7 and 98.1 % with YES), while at 1496x967 it was 99.3 and 94.7 %, so the mode, not the game, decides which present is smooth. Only the two modes were measured, so the rule keys on the framebuffer being wider than the panel and every other mode keeps the earlier behaviour. Timing the plain present ourselves (waiting until 10 ms before `targetPresentationTimestamp`, on our own present thread) put every frame on its target but made the link's own targets step 29.17 ms about every 195 ms (133 to 333 times a cell, none without it) and cost up to 4 FPS, so it was dropped. The present interval is not switched while the game runs: a recreated swapchain starved the link of drawables until the KosmicKrisp leak below was fixed, so a window launch that goes full screen keeps IMMEDIATE and a full-screen launch that goes to a window keeps FIFO. A FIFO game fully hidden for 10 s showed no `Present` call of 1 s or more on either screen. Window shots (`screencapture -l`) and ScreenCaptureKit recordings make the window server composite a full-screen window for 10 to 20 s, which changes both latency and cadence, so full-screen numbers are taken before any capture. When `occlusionState` says hidden, `rt: gfx window hidden, unpaced` is logged and the link stops as above. Three debug tools stay: `RT_FRAME_TRACE=FILE` writes, for every frame, its end time, its wall time and the game thread's time in copies, device lock, draws, `Present`, sleep, lock wait, pump and on the CPU (`CLOCK_THREAD_CPUTIME_ID`); `RT_GFX_LUMA=1` reads the back buffer two frames late and logs `gfx black frame` when the game renders black, and `RT_PRESENT_TRACE=FILE` writes, for every present, the drawable, its texture, when KosmicKrisp's copy finished (a shared event signalled on its Metal 4 queue after `signalDrawable:`) and when it was shown, plus, with the link, when the game asked for the drawable, when the update arrived and was taken, its deadline and its target, and every layer property change and who made it, to `FILE.sets`; at exit the run log gets `gfx display link updates … presents past the deadline …`. Three more for the link and the back buffer: at each link stall the log lists the game drawables still alive and whether each was presented (`gfx live game drawables at a link stall`, first 12), and with `RT_PRESENT_TRACE` the IOSurfaces of the link's last 4 drawables with `IOSurfaceIsInUse` and the local use count (`gfx link surfaces at the stall`; in three stalls of a covered game-layer link both surfaces were free and no game drawable was alive), while the trace gains the time each presented handler ran and the time each drawable was freed, the exit line counts game-layer `maximumDrawableCount` writes and how many came with a link attached, `RT_GFX_DUMP=START:COUNT` (with `RT_GFX_LUMA=1`) writes the half-size back buffers of frames START to START+COUNT-1 to `<overlay>/dump/fNNNNNN.bin` (header `BBD1`, width, height, frame, uptime), and `RT_GFX_CHECK` also logs buffer locks that overlap a lock still open on the same buffer (`gfx nested lock overlap`). SDK headers: `Metal/MTLDrawable.h`, `Metal/MTLCommandBuffer.h`, `QuartzCore/CAMetalLayer.h`, `QuartzCore/CAMetalDisplayLink.h`.
- **Texture init.** dxvk zero-fills every new image before its first use (`DxvkContext::initImage`). For uncompressed images it used `vkCmdClearColorImage`, and KosmicKrisp 26.2.5 runs that clear as one `BeginRendering`/`EndRendering` per mip level (mesa `src/kosmickrisp/vulkan/kk_cmd_clear.c`, `clear_image`), each one a new Metal 4 command buffer and render encoder (`kk_cmd_buffer.c`, `cs_start_render`) that takes about 2.5 MB of `MTL4CommandAllocator` memory; the allocator keeps that memory after `reset`. The `la_hub_1` load creates about 800 mipmapped textures in one command list, 8,264 encoders, so one allocator reached 20,986,631,336 bytes, the process held 22 to 25 GB for the rest of the session, and the first submit spent 5 to 9 s in the kernel. `third_party/dxvk/0004-fix-dxvk-zero-sampled-color-images-by-copy-not-a-cle.patch` zero-fills color images that are never render targets with dxvk's own zero-buffer copy path (the one it already used for block-compressed formats); KosmicKrisp records those copies into its one open compute encoder (`kk_cmd_copy.c`, `cs_get_compute`). Render targets and depth images keep the clear. Measured on `la_hub_1`, same binary, two runs each: allocator peak 287,003,816 bytes, Metal graphics memory 1.82 GB against 22 GB, and no bridge call of 1 s or more against a 4.7 to 4.8 s first `Present`.
- **Index buffer length.** A triangle from the Prince's shoulder to the top of the screen in the theatre cutscene was a wrong index: in draws with an odd 16-bit start index that end exactly at the end of an index buffer whose size is 2 mod 4 (21,582, 18,726 and 1,878 bytes), the GPU ran vertex 0 in place of the last index. KosmicKrisp hands Metal the address `base + firstIndex * 2` with the rest of the bound range (`kk_cmd_draw.c`, 1885 to 1890); its robustness path knows about index ranges not aligned to 4 bytes ("Only need to handle case where index buffer is not aligned to 4 bytes", 1644) but unrolls a draw only when it reads past the range (1657 to 1660), and Metal 4 asks only 2-byte alignment and an even length for 16-bit indices (`MTL4RenderCommandEncoder.h`, `drawIndexedPrimitives:…indexBufferLength:`), so the last in-range index read as 0. dxvk bound the D3D9 size as is. `third_party/dxvk/0005-fix-d3d9-bind-index-buffers-with-a-4-byte-aligned-le.patch` creates D3D9 index buffers and binds them with the length rounded up to 4 bytes (`m_desc.Size` stays the game's for `GetDesc` and locks), and `DrawIndexedPrimitiveUP` starts and ends its index part on 4 bytes. A GPU dump of every skinned draw's vertex output against a CPU re-skin over the cutscene (evidence `inv-prince-fps/spike/`): before, one unmatched vertex 0 per affected draw per frame and the sliver in the shots; after, 0 unmatched in every frame, largest clip difference 5.6e-6, no sliver.
- **Slow GPU.** dxvk's 5 s wait in `dxvk_queue.cpp` called the 3.6 to 9 s first `la_hub_1` frame a lost device (exit 15, "GPU likely hung", 5 of 8 cold runs). That frame was the texture-init growth above, so patch 0004 removed the cause and the old 120 s wait patch is gone: dxvk keeps its own 5 s limit, and three cold `la_hub_1` loads with empty caches all exited 0 with no bridge call of 1 s or more. `rt_hang_due` stays at 120 s in the player as a deadlock net for any blocked bridge call.
- **KosmicKrisp build.** The kit ships a KosmicKrisp built from the official Mesa 26.2.5 source (`mesa-26.2.5.tar.xz` from archive.mesa3d.org, sha256 `794b3d17…a0a5`, the same as `brew cat mesa`) instead of the Homebrew mesa bottle, with three upstream fixes as `third_party/mesa` patches. The bottle leaked about 70 KB/s (252 MB an hour) in the hub (`leaks` on a copy signed with `com.apple.security.get-task-allow`): one `CAMetalDrawable` per presented frame, because `kk_encode_drawable_present` retains it and `kk_queue_submit` never released it, and about 127,000 `MTLRenderPassColorAttachmentDescriptorArrayInternal` objects in 300 s, because the Metal bridge ran `init` on the result of `new` (`[[MTL4RenderPassDescriptor new] init]`, a file built without ARC), which replaced the attachment array and lost the first. 0001 backports Mesa 750284be ("kk: release drawable after presentation signal"; in 26.2.5 the release goes after `mtl_drawable_present`, and `cmd_buffer->drawable` is set only in `kk_wsi.c` and cleared only in `kk_queue.c`, so one release per retain); 0002 is Mesa e4174f89 ("kk: Fix double init in the Metal bridge", five sites); 0003 is Mesa fa25935b ("kk: Release drawable on command buffer free"): Mesa's Metal WSI records each swapchain image's present command buffer again on every acquire and `kk_encode_drawable_present` retains the drawable into it, so an image acquired but never presented (a swapchain recreated with an image out) kept its drawable until the command buffer was freed, and `kk_destroy_cmd_buffer` never released it; after a present-mode change the new swapchain then found the layer's three drawables held and the display link sent no update for 1 s at a time. All three are on Mesa main but not in 26.2.5 or 26.2.6; 26.3 has them but reworks the present path that the pacing and the MetalFX feed hook, so the kit stays on 26.2.5. Only the Vulkan driver is built, so it no longer links libxcb or libX11 and those five components left the prebuilt spec: from the patched source, with a venv holding Mesa's Python build modules first on `PATH`, Homebrew's LLVM next and `SDKROOT=$(xcrun --show-sdk-path)`, `meson setup <build> --prefix=/opt/bloodlines-porter/mesa --buildtype=release -Db_ndebug=true -Dstrip=true -Dllvm=enabled -Dopengl=false -Dgles1=disabled -Dgles2=disabled -Degl=disabled -Dglx=disabled -Dgallium-drivers= -Dgallium-rusticl=false -Dvulkan-drivers=kosmickrisp -Dvulkan-layers= -Dplatforms=macos -Dtools= -Dvideo-codecs= -Dlibunwind=disabled -Dlmsensors=disabled -Dvalgrind=disabled -Dgbm=disabled -Dxlib-lease=disabled`, then `meson compile` and `meson install --destdir <root>`. The prefix is one that does not exist on a Mac, because Mesa bakes it into the driver as its `drirc` folder and the kit refuses a driver that holds a path under the home folder (`make_kit.py` `path_leak_errors`); the 26.2.5 `00-mesa-defaults.conf` has no entry for DXVK or this game, so a missing `drirc` changes nothing. The dev install is `~/Library/Caches/BloodlinesPorter/dev/mesa/root/opt/bloodlines-porter/mesa`, named by the kit spec `prebuilt-spec-kk.json`. Measured in `sm_hub_1` with `leaks` at 300 s: 0 drawable and 0 attachment-array root leaks, 306 leaks and 20 KB in all against 181,728 and 21.2 MB (what is left is Apple's `__NSMallocBlock__` from `-[IOGPUMetal4CommandQueue commitFillArgs:…]`, 18 in 240 s); `--gfx-selftest` 174 PASS; four interleaved walks against the bottle with on-beat, bursts and ask-to-panel inside the bottle's spread (ask-to-panel p50 41.4 ms in all four); New Game to the theatre exit 0 with no slow bridge call, gfx hang or `VK_ERROR`.
- **Pipeline cache.** KosmicKrisp has no graphics pipeline library, so dxvk builds each new pipeline on its CS thread at the first draw that needs it, and `Present` waits for it. KosmicKrisp 26.2 never creates a Mesa disk cache (`kk_physical_device_init_pipeline_cache` only sets the UUIDs, mesa `src/kosmickrisp/vulkan/kk_physical_device.c`), so `<overlay>/cache/mesa` stays empty, and upstream dxvk passes no `VkPipelineCache`. `third_party/dxvk/0003-perf-dxvk-keep-a-Vulkan-pipeline-cache-file-across-r.patch` loads `<overlay>/cache/dxvk/vk-pipeline-cache.bin` into one `VkPipelineCache` at device creation, passes it to `vkCreateGraphicsPipelines`, and writes it back (temp file, then rename) every 15 s when its size changed and when the device goes away; KosmicKrisp serializes its shaders into it (`kk_shader_ops`, mesa `src/kosmickrisp/vulkan/kk_shader.c`). Data the driver rejects gives an empty cache (Vulkan spec, `vkCreatePipelineCache`). `DXVK_PIPELINE_CACHE=0` turns it off.
- **MetalFX feed.** With Picture mode MetalFX the scaler reads the game's own drawable on the GPU; the game thread does nothing for it. The first feed copied each back buffer to system memory (`GetRenderTargetData`, READONLY `LockRect`), which made dxvk wait for the GPU every frame (`D3D9DeviceEx::WaitForResource`, about 11 % of the main thread in the opening cutscene); the second kept two such surfaces and read the previous one, which dropped the wait and showed MetalFX one frame late. Neither is in the code now (`fx_feed`, the two `SYSTEMMEM` surfaces and `rt_fx_slot_*` are gone). How it works: KosmicKrisp ends a present in `kk_queue_submit` (Mesa `kosmickrisp/vulkan/kk_queue.c`) by committing the command buffers, then `mtl_command_queue_signal_drawable` and `mtl_drawable_present`; the committed buffer is Mesa's copy of the swapchain image into the drawable's texture (`vulkan/wsi/wsi_common_metal.c`: `bind_drawable_to_vkimage`, `wsi_cmd_blit_image_to_image`, `encode_drawable_present`). `runtime/fx.m` swizzles `-[MTL4CommandQueue signalDrawable:]` (`sig_install`, hook `sig_hook`; the same hook feeds `RT_PRESENT_TRACE`) and, while the MetalFX view is live, signals an `MTLSharedEvent` on that queue with the next value and hands the drawable to the `fx` thread, which keeps only the newest. Apple, `Metal/MTL4CommandQueue.h`: `signalEvent:value:` "Schedules an operation to signal a GPU event with a specific value after all GPU work prior to this point is complete", and for `signalDrawable:` "You are responsible for calling this method after committing all command buffers that contain commands targeting this drawable, and before calling present", so the event fires when the copy into the drawable is done. The `fx` thread encodes, on its own classic queue, `encodeWaitForEvent:value:` (`Metal/MTLCommandBuffer.h`: "pauses execution of this command buffer until the specified event reaches a given value"), then the spatial scaler with `colorTexture` set to the game drawable's texture (`MetalFX/MTLFXSpatialScaler.h`: for every render pass "Set the spatial scaler's colorTexture property to the input texture"), then the clear and aspect-fit copy into the MetalFX layer's drawable and `presentDrawable:afterMinimumDuration:` 1/60 as before. It holds the game drawable until that command buffer completes. No CPU waits for the GPU, nothing is read back, and the picture shown is the frame just presented. The game layer needs `framebufferOnly = NO` to be sampled (`QuartzCore/CAMetalLayer.h`: "whether they may also be used for texture sampling"); Mesa already sets it when it configures the swapchain ("So we can blit to the drawable", `wsi_common_metal_layer.m`, `wsi_metal_layer_configure`), so no layer property changes here. The scaler is made again when the game drawable's size or the aspect-fit rectangle changes. A game drawable that is not `BGRA8Unorm` with `ShaderRead` usage is skipped and logged once (`fx game drawable not usable as scaler input`); dxvk's swapchain for an `X8R8G8B8` or `A8R8G8B8` back buffer is `B8G8R8A8_UNORM` (`d3d9_swapchain.cpp`, `GetSurfaceFormat`). The hook is installed the first time MetalFX is on (or when `RT_PRESENT_TRACE` is set); with Picture off and no trace nothing is swizzled. `RT_PRESENT_TRACE` gains the columns `fxshown` (the `presentedTime` of the MetalFX drawable that showed that game present, 0 when none), `fxgpustart` and `fxgpuend` (the GPU times of the scaler's command buffer), next to `call`, the time of the game's `signalDrawable:`; the "added" figure in `rt: fx frames` is now the `fx` thread's encode CPU only. Measured on the real game (windowed, muted, interleaved, 2 runs each): a 30 s `sample` of the theatre cutscene had `WaitForResource` in the game thread 383 and 10 times before and 0 times after (two runs); game-thread CPU per frame is unchanged (mean 19.7 and 20.0 ms before, 19.6 and 19.9 ms after); game present to MetalFX shown in the walk, mean 45.1 and 46.7 ms before, 36.1 and 42.9 ms after, so the gain is under one frame because `presentDrawable:afterMinimumDuration:` and the panel's own beat still decide when it shows.
- **Adapter identity.** dxvk's own D3D9 identity is kept ("AMD Radeon RX 6700 XT", vendor `0x1002`, device `0x73DF`); the bridge sets no custom vendor id. KosmicKrisp is proven by dxvk's `Found device: Apple M1 Max (KosmicKrisp 26.2.5)` line and the `rt: gfx image` lines.
- **Display modes.** `GetAdapterModeCount` and `EnumAdapterModes` add the engine's five table modes (640×480, 800×600, 1024×768, 1280×1024, 1600×1200) when dxvk's list lacks them. The built-in display offers no 1280×1024; without this the engine falls back to 640×480 (`rt: gfx display modes for format 22: 132 from dxvk, 5 windowed engine modes added`).
- **Text.** The menu labels are the engine's own bitmap fonts; the gdi32 rows stay M3's six shims.
- **Probe.** `probe/` (go/no-go 1, `probe/run.sh`) builds separately from the game and is not part of `build_all.py`.

## Boot stages

M3's eight stages stay. Stage 9 (device) comes between 6 and 7, stage 10 (first-present) after 8, stage 11 (menu) last. In dxvk mode the order is 1, 2, 3, 4 (one per module), 5, 6, 9, 7, 8, 10, 11; in `--guest-quiet` mode stage 11 does not fire; in null mode only stage 11 can follow stage 8. Stage 11 is the first guest line `main menu rendering particles on frame N` with N at least 30. The stage 9 line is `rt: stage 9 device dxvk "<D3D9 adapter>" vendor 0x<id> <w>x<h> windowed <0|1> format <n> flags 0x<BehaviorFlags>` and is printed only after `CreateDevice` returned D3D_OK. At stage 11 the log prints `rt: gfx frame backbuffer <w>x<h> <format> depth <w>x<h> <format>` (the surfaces set by `SetRenderTarget(0)` and `SetDepthStencilSurface`, kept reachable for later upscaling work).

Every exit prints `rt: frames n <n> p50 <ms> p95 <ms> p99 <ms> max <ms>` (present to present, after stage 11 or, in quiet mode, after the first Present), `rt: shown n <n> missed <n> mean <ms> sd <ms> short <n> on <n> long <n> max <ms>` (time each frame stayed on screen, from `presentedTime`; short is under 12.5 ms, long is over 20.8 ms; no counts while the window is hidden), `rt: max pump gap <n> ms` (whole run), `rt: max pump gap steady <n> ms in <where> at +<s> s` (from stage 10 + 5 s, with a `rt: gfx steady gap detail` line), `rt: shaders vs_1_1 … ps_2_0 … failed <n>`, `rt: gfx locks <n> out-of-arena <n> late-writes <n>` and `rt: gfx lost <n>`. The thread CPU time in the gap detail comes from `clock_gettime_nsec_np(CLOCK_THREAD_CPUTIME_ID)` (`man clock_gettime` on macOS).

## Rulings applied (validator)

- Stage order 1, 2, 3, 4…, 5, 6, 9, 7, 8, 10, 11. AC2 is evidenced by the stage 9 line.
- AC8 and AC9 run in `--guest-quiet` mode: per-frame guest logging (`+mm_verbose 1`, `-condebug`) costs about 4 to 10 ms per frame.
- AC9's pump limit applies to `rt: max pump gap steady`, measured from stage 10 + 5 s. The whole-run gap of about 1.0 to 1.1 s at the first menu frame is a known limit: KosmicKrisp has no graphics pipeline library, so the first draws compile their pipelines inside `Present`. Present waits under host load are environmental.
- `MESA_KK_DEBUG=force_robustness` is set because the engine reads past the ends of its buffers; without it the menu loses the device (`VK_ERROR_DEVICE_LOST`, "GPU likely hung") at 1280×1024 and draws black at 640×480 (mesa 26.2.5 `src/kosmickrisp/vulkan/kk_debug.c` lines 13 and 17).
- The engine's five display modes are added (above). The developer-overlay row of dots at the top right (`-dev`) is expected; the AC6 mask excludes it.
- The probe lives in `probe/`. The bridge is hand-written and the AMD adapter identity is kept.
- The reference is the first native capture, approved by the maintainer with its mask (AC6); it is compared only inside the mask.

## Results per acceptance criterion

| AC | Result | Evidence |
|---|---|---|
| AC1 | PASS | `PROBE_OUT=$DEV/build/m4p3-probe probe/run.sh` exits 0 (probe `lipo -archs` arm64): vulkan device `Apple M1 Max`, driverID 28 `KosmicKrisp` `Mesa 26.2.5`; vs_2_0+ps_2_0, vs_1_1+ps_1_1 and vs_1_1+ps_1_4 quads read back exactly on 4 sample pixels each; vs_1_1 2,866, vs_2_0 1,985, ps_1_1 144, ps_1_4 14, ps_2_0 4,186 created, drawn and 0 failed (9,195 of 9,195); both device flag sets (0x42, 0x22) pass. |
| AC2 | PASS | `M boot`: `rt: window 0x00010004 created 2056x1329 vulkan 1`; `rt: stage 9 device dxvk "AMD Radeon RX 6700 XT" vendor 0x1002 1280x1024 windowed 1 format 22 flags 0x52`; dxvk log `Found device: Apple M1 Max (KosmicKrisp 26.2.5)`; `rt: gfx image` lines: one libSDL3, one Vulkan loader (`libvulkan.1.4.363.dylib`), `libvulkan_kosmickrisp.dylib` loaded, `libvulkan_lvp.dylib` not. `M selftest` fails a log whose images lack KosmicKrisp or include lavapipe; the runtime's exit 15 mutation was run at P1 and not repeated here. |
| AC3 | PASS | Stage 1 `gfx dxvk`; first-call TSV: 67 d3d9 rows, all kind `bridge`, 0 `stub` (kinds shim 125, fail 2, bridge 67); dxvk log `bloodlines-native_d3d9.log` 315 lines; `rt: shaders vs_1_1 794 vs_2_0 184 ps_1_1 16 ps_1_4 5 ps_2_0 34 failed 0`. |
| AC4 | PASS | Stage 1 line has `-dxlevel 90`; `rt: dllmain stdshader_dx9 = 1`; vs_2_0 184 and ps_2_0 34 created. |
| AC5 | PASS | `M menu` (`--until menu --capture-after 10`): exit 0 in 20.9 s; stages 1, 2, 3, 4×3, 5, 6, 9, 7, 8, 10, 11 in order; stage 11 on frame 30; `shots/menu-*.png` 1280×1024 with 42,977 distinct colours; `screencapture -o -l <window id>` gave 2560×2112 (the 1280×1056-point window at 2×) and the picture shows the same menu. |
| AC6 | OPEN | The reference is captured but NOT approved by the maintainer: see "Reference picture". The 5 later captures pass `M compare` under the calibrated provisional thresholds and a 64×64 black square fails. Needs the maintainer's approval of the picture and the mask. |
| AC7 | PASS (labels pending with AC6) | No guest line `Couldn't create bitmap font`; gdi32 rows are exactly CreateFontA, DeleteObject, GetStockObject, SelectObject, SetBkMode, SetTextColor, 0 stub. The capture reads New Game, Load Game, Options, Quit and the Vampire: The Masquerade logo; the maintainer's AC6 review still confirms the labels. |
| AC8 | PASS | `M soak --n 3 --close-after 130` (quiet mode): 3 of 3 exit 0, 0 `rt: FAULT`, `rt: gfx lost 0`, no new `bloodlines-native*.ips` in DiagnosticReports. |
| AC9 | PASS | Same soak: n 26,578, 26,952 and 26,532 frames (about 204 per second, not capped), p99 10.70, 9.36 and 10.82 ms (limit 33.3), steady pump gap 24, 24 and 27 ms (limit 100). Table below. |
| AC10 | OPEN | Display 0 (built-in, 2056×1329 points at 2×): PASS, window capture 2560×2112 = 1280×2.0 wide, back-buffer PNG 1280×1024, numbers below. Display 1 (3440×1440 ultrawide) is not connected: `rt: run refused: display 1 not found`. Open until it is measured. |
| AC11 | PASS | `M cases`: 32 PASS lines, 0 FAIL, exit 0 (vertex and index buffers plain, DISCARD, NOOVERWRITE and MANAGED; 2D level full and RECT; out-of-bounds RECTs; cube face; DXT1; second-thread exit 5). Boot and soak runs print `rt: gfx locks … out-of-arena 0` (664,664 locks in the boot run; 18.4 to 18.7 million in a 130 s soak run). The skip-copy-back mutation was run at P1 (6 of 6 P1 mutations caught) and not repeated here. |
| AC12 | PASS | The build holds the `_Static_assert`s; `cases` prints `D3DPRESENT_PARAMETERS round-trips 56 -> 64 -> 56 bytes field by field, hDeviceWindow 28 <-> 32`, `D3DLOCKED_RECT 16 -> 8 bytes`, and `GetAdapterIdentifier writes 1100 bytes`. |
| AC13 | PASS | `rt: gfx frame backbuffer 1280x1024 22 depth 1280x1024 77`; the PNG comes from that back buffer. |
| AC14 | PASS | `X --run --gfx null --until console` (checked with `m3.stage_check`): exit 0 in 8.2 s, stages 1 to 8 as M3 AC1, stage 1 `gfx null`, first-call rows shim 192 and fail 2 with 0 stub, no `libvulkan`, `dxvk`, `gfx image` or `VK_DRIVER` text in the log, no crash report. `m3.py cases` on the `selftest/m3` module: 26 PASS, 0 FAIL. |
| AC15 | PASS | `build_all.py --out $DEV/build/m4p3` ends `pass` (13 min 22 s clean); `tools/recomp/build.py` prints `ALL PASS`, and both `native-results.json` files have sha256 `7d3e33d5612508900a12bc636107554dfc133ff04c2b924b3e0e8db5c11c1315`. A second `build_all.py`: `ninja: no work to do.`, `0 generated files rewritten`, 8.4 s. |
| AC16 | PASS | `shasum -a 256 -c docs/game-manifest.txt` from `$GAME`: 5,399 OK, 0 failures; `find "$GAME" -newer <stamp taken before the runs>` prints nothing. |
| AC17 | PASS | `lipo -archs` is `arm64` for X, the probe, `libdxvk_d3d9.0.dylib`, `libvulkan.1.4.363.dylib`, `libvulkan_kosmickrisp.dylib` and `libSDL3.0.dylib` (exactly the `rt: gfx image` paths plus X and the probe). |
| AC18 | PASS | The table below; `M licences --log <run log>` checks it against `otool -L X`, the `rt: gfx image` lines and `lipo`. |
| AC19 | PASS | `git ls-files` lists no generated or lifted C, `.vcs`, shader dump, PNG, log, TSV or dylib, and no dxvk or Mesa source. |

`M selftest` passes (142 cases, no game needed). Generated output, logs and captures live only under `$DEV/`.

## Numbers

Quiet soak, `M soak --n 3 --close-after 130 --exe X` (quiet mode, 130 s from stage 8, one overlay shared by the three runs so the shader cache is warm from run 2):

| Run | frames n | p50 ms | p95 ms | p99 ms | max ms | pump gap whole run | pump gap steady | gfx locks |
|---|---|---|---|---|---|---|---|---|
| 1 | 26,578 | 3.86 | 8.84 | 10.70 | 1035.32 | 1035 ms (stage 8, first menu frame) | 24 ms, present, +111.3 s | 18,464,962 |
| 2 | 26,952 | 3.87 | 8.73 | 9.36 | 1038.34 | 1038 ms | 24 ms, present, +99.2 s | 18,724,854 |
| 3 | 26,532 | 3.83 | 8.81 | 10.82 | 1072.81 | 1072 ms | 27 ms, present, +33.8 s | 18,433,060 |

Machine load for the soak: `uptime` before `load averages: 4.82 5.96 8.18`, after `6.33 7.10 8.01`. Background load came from system and desktop processes. No game, ninja, build or harness process was running (`pgrep`), and a `$DEV/build/.measure.lock` file was held. The load average comes from system and desktop agents and was not below 2, so these are the numbers of a normal working Mac, not of an idle one. The steady gap detail of run 1: `present 21.3 draws 0.0 locks 0.1 sleep 0.0 lock-wait 0.0 thread-cpu 3.0 wall 24.3 ms`.

Runs with logging on (`+mm_verbose 1`, `-condebug`) are not performance numbers. The `M boot` run (no screencapture): `rt: frames n 928 p50 4.63 p95 7.73 p99 14.00 max 128.43`, 197.2 KiB copied per frame, copies 0.03, device-lock 0.03, draws 0.02, present 0.85 and other 3.97 ms per frame, steady gap 19 ms in present, 664,664 gfx locks. The `M menu` run (`screencapture -l` ran on the window; `menu/logs/run-20261027T024421Z.log`): `rt: frames n 1296 p50 5.21 p95 11.33 p99 14.85 max 4558.88`, 199.2 KiB copied per frame, steady gap 79 ms in guest at +11.5 s, of which 73.5 ms was the guest thread's own CPU time; `screencapture` was running as a separate process during the run, and no link between the two is established; 921,031 gfx locks.

Per display (`M displays --displays 0 1`):

| Display | Window capture | Back-buffer PNG | frames n / p50 / p95 / p99 / max (quiet, 130 s) | steady pump gap | Result |
|---|---|---|---|---|---|
| 0: Built-in Retina Display, 2056×1329 points, 4112×2658 pixels (backing scale 2.0 by `system_profiler`; SDL reports content scale 1.00) | 2560×2112 (1280×1056 points, the 1024 client rows plus a 32-point title bar, at 2×) | 1280×1024, 43,156 colours, compare PASS | 27,126 / 3.84 / 8.68 / 9.72 / 1017.5 ms | 26 ms | PASS |
| 1: ultrawide 3440×1440, 120 Hz | not run | not run | not run | not run | not connected, OPEN |

That run started while another worker's game instance (`m5a`, 114 % CPU) was ending; its CPU use is in the `before` snapshot at `$DEV/build/m4p3-runs/displays2-load.txt`. An earlier display 0 run (same build, no other game) gave p99 11.05 ms, steady gap 24 ms.

## Reference picture (AC6, approval pending)

The reference folder (outside git; set `BN_REFDIR`, default the current directory) holds the first native capture and a static-UI mask, with `PENDING-APPROVAL.txt` beside them. The picture was taken with `--capture-after 10` from `M menu` at the commit above. **Neither file is approved yet.** Until they are, AC6 stays open and every compare is provisional.

| File | SHA-256 |
|---|---|
| `m4-menu.png` (1280×1024, 8-bit RGB) | `be0203a82e7dec69fccda8c37dc10ac5e196d110392b6ae80ae2b99e81da3019` |
| `m4-menu-mask.json` | `36cec80dfca96915b5d0b5de0f4a2570b8e26c9c23d961de3e5760ef3924532d` |

The mask (`[x, y, width, height]`) is the logo `[244, 46, 794, 337]` and the New Game button `[443, 411, 348, 55]`. It leaves out the animated background, the particles and the developer-overlay dots (top right, y 11 to 12, x 1046 to 1206). Load Game, Options and Quit are also left out: their brightness changed from capture to capture (mean red of the button text rectangles 37.1 to 45.9 against 41.6, 44.2 and 45.6 in the reference; per-pixel differences up to 78), and the cause (hover highlight or glow animation) was not isolated. Compared inside the four-button mask, the later captures reach only 94.7 %, 94.7 % and 95.8 % of pixels within tolerance.

Provisional thresholds, calibrated on five captures after the reference (`M compare PNG REF --mask JSON`): sizes equal; inside the mask at least 99 % of pixels within ±16 per channel, mean absolute difference at most 2/255, and no 32×32 block with more than half its pixels out of tolerance; outside the mask the mean luma above 1/255 (not black).

| Capture | within ±16 | mean abs diff | worst 32×32 block | outside mean luma | Result |
|---|---|---|---|---|---|
| calibration run 1 | 1.00000 | 0.2113 | 0.000 | 2.34 | PASS |
| calibration run 2 | 0.99985 | 0.1837 | 0.075 | 2.27 | PASS |
| calibration run 3 | 1.00000 | 0.1989 | 0.000 | 2.27 | PASS |
| first `M displays` capture, display 0 (`7a6197b0dda992c62d95e44c88b1022b6951aad7451220f816a016f42743f41d`) | 0.99975 | 0.1930 | 0.127 | 2.25 | PASS |
| second `M displays` capture, display 0 | 0.99999 | 0.2378 | 0.008 | 2.30 | PASS |
| calibration run 1 with a 64×64 black square at (500, 100) | 0.99404 | 0.8087 | 0.625 | 2.34 | FAIL (mutation) |

The spec's ±8 and 5/255 do not hold on this picture: at ±8 the first `M displays` capture has a New Game block at 0.533 (limit 0.5), and the picture's outside mean luma is 2.3, so 5/255 would reject every genuine capture. The largest per-channel difference inside the mask is 12, 22, 12, 18 and 23 in the five captures (calibration runs 1 to 3, second and first `M displays` capture), so ±16 does not bound every pixel: captures pass because 99 % of pixels are within ±16 and no block is more than half out. A black square passes the 99 % rule alone (0.994); the block rule is what fails it. A black square over an already dark part of the mask cannot fail, because it changes them by no more than the tolerance.

## Library and licence table (AC18)

One row per library in `otool -L X` and per `rt: gfx image` line, the libraries compiled into the dxvk dylib, and the build-time tools. Role is `linked` (in `otool -L X`), `loaded` (opened at run time, or compiled into a loaded library) or `build-only`. `otool -L X` lists `/opt/homebrew/opt/sdl3/lib/libSDL3.0.dylib` and `/usr/lib/libSystem.B.dylib`; `libdxvk_d3d9.0.dylib` lists only libSystem and libc++, so the submodule rows are compiled into it. Paths under `$DXVK` are `$DEV/vendor/dxvk`; Homebrew paths are under `/opt/homebrew/Cellar`.

| Library | Version or commit | Licence | Licence file | Role |
|---|---|---|---|---|
| libSDL3.0.dylib (Homebrew sdl3) | SDL 3.4.10 | Zlib | `/opt/homebrew/Cellar/sdl3/3.4.10/LICENSE.txt` | linked |
| libdxvk_d3d9.0.dylib (jm2/dxvk, branch kisakcod-macos) | jm2/dxvk dd3a05b (`dd3a05bde9bae53eecd9b7314761c8c4571cb4f1`, v3.1.1-74) | Zlib (zlib/libpng licence) | `$DXVK/LICENSE` | loaded |
| dxvk submodule dxbc-spirv | `c7f069701227dcb800a01bd93e42f8001b7543e7` | MIT | `$DXVK/subprojects/dxbc-spirv/LICENSE` | loaded |
| dxvk submodule libdisplay-info | `275e6459c7ab1ddd4b125f28d0440716e4888078` | MIT | `$DXVK/subprojects/libdisplay-info/LICENSE` | loaded |
| dxvk submodule include/native/directx (mingw-directx-headers) | `9df86f2341616ef1888ae59919feaa6d4fad693d` | MinGW-w64 notices | `$DXVK/include/native/directx/COPYING.MinGW-w64.txt` | build-only |
| dxvk submodule include/vulkan (Vulkan-Headers) | `6802bb4733b63ed5efd3adb308a6c885ef180ea1` (v1.4.363) | Apache-2.0 OR MIT, per file | `$DXVK/include/vulkan/LICENSE.md` | build-only |
| dxvk submodule include/spirv (SPIRV-Headers) | `04f10f650d514df88b76d25e83db360142c7b174` | MIT (Khronos), per file | `$DXVK/include/spirv/LICENSE` | build-only |
| dxvk submodule dxbc-spirv/submodules/spirv_headers (SPIRV-Headers) | `c8ad050fcb29e42a2f57d9f59e97488f465c436d` | MIT (Khronos), per file | `$DXVK/subprojects/dxbc-spirv/submodules/spirv_headers/LICENSE` | build-only |
| libvulkan.1.dylib (Homebrew vulkan-loader, file libvulkan.1.4.363.dylib) | Vulkan-Loader 1.4.363.0 | Apache-2.0 (majority; exceptions are more permissive) | `/opt/homebrew/Cellar/vulkan-loader/1.4.363.0/LICENSE.txt` | loaded |
| libvulkan_kosmickrisp.dylib (Homebrew mesa, KosmicKrisp) | Mesa 26.2.5 | MIT AND Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND HPND AND HPND-sell-variant AND ICU AND MIT-Khronos-old AND SGI-B-2.0 AND LicenseRef-Homebrew-public-domain AND (GPL-1.0-or-later WITH Linux-syscall-note) AND (GPL-2.0-only WITH Linux-syscall-note), per file (`brew info --json=v2 mesa`, a list for the whole package; the shipped library has no GPL file, see `THIRD_PARTY_NOTICES.md`) | `/opt/homebrew/Cellar/mesa/26.2.5/license.rst` | loaded |
| Vulkan-Headers (Homebrew vulkan-headers) | 1.4.363.0 | Apache-2.0 | `/opt/homebrew/Cellar/vulkan-headers/1.4.363.0/LICENSE.md` | build-only |
| glslang (Homebrew) | 16.6.0 | BSD-3-Clause AND GPL-3.0-or-later AND MIT AND Apache-2.0 | `/opt/homebrew/Cellar/glslang/16.6.0/LICENSE.txt` | build-only |
| meson (Homebrew) | 1.12.1 | Apache-2.0 | `/opt/homebrew/Cellar/meson/1.12.1/COPYING` | build-only |
| ninja (Homebrew) | 1.13.2 | Apache-2.0 | `/opt/homebrew/Cellar/ninja/1.13.2/COPYING` | build-only |
| macOS system libraries (libSystem.B.dylib and libc++.1.dylib) | macOS 26.6.1 | Apple, part of macOS (not redistributed) | none | linked |

Installed with mesa but never loaded (not in the `rt: gfx image` lines): lavapipe (`libvulkan_lvp.dylib`, kept out by `VK_DRIVER_FILES`), molten-vk 1.4.2 and llvm 23.1.4. The Vulkan SDK and its validation layers are not installed. Licence strings come from `brew info --json=v2`, the files named above, and the dxvk clone at the pinned commit.

## Spec deltas found while measuring

- AC6: the mask is the logo plus New Game (the other three buttons vary); the tolerance is ±16, not 8 (the per-channel maxima inside the mask were 12, 22, 12, 18 and 23, so ±16 passes through the 99 % rule and the block rule, not as a bound on every pixel); the outside-the-mask luma floor is 1/255 (measured 2.3; 5/255 is unreachable on this dark picture); a 32×32 block rule (at most half of a block out of tolerance) is added because a black square over mostly dark pixels passes the 99 % rule.
- AC9: the pump limit applies to the steady gap from stage 10 + 5 s. The whole-run gap of about 1.0 to 1.1 s at the first menu frame stays informational. The frame floor of 600 per 60 s is met 20 times over (the engine runs uncapped at about 204 frames per second), so p99 and the pump gap are the real limits.
- AC10: SDL reports content scale 1.00 for the built-in display, which runs a scaled mode of 2056×1329 points on 4112×2658 pixels; the backing scale is 2.0 (`system_profiler`), and the window capture is 2560×2112 because the 32-point title bar adds to the 1024 client rows. The harness takes the scale from `system_profiler`. The runtime's `rt: display` scale does not reflect it.
- AC14: `m3.py boot` builds its own argument list and cannot pass `--gfx null`; the null boot was checked with `m3.stage_check` on a run of `X --gfx null --until console`.
- AC18: libraries compiled into `libdxvk_d3d9.0.dylib` are rows with role `loaded`; the build tools meson and ninja are rows with role `build-only`.
- Measuring: a `.measure.lock` file did not stop another worker's game instance from starting during the display 0 run.

## Sources (M4)

- W3C, Portable Network Graphics (PNG) Specification (Third Edition), Recommendation 24 June 2025, https://www.w3.org/TR/png-3/: 5.2 PNG signature, 5.3 Chunk layout, 5.4 Chunk naming conventions (ancillary bit), 5.5 CRC algorithm, 9 Filtering (9.2 filter types, 9.3 Average, 9.4 Paeth), 11.2.1 IHDR, 11.2.3 IDAT, 11.2.4 IEND. The harness reads PNGs by these rules and checks every chunk's CRC.
- `man screencapture` (macOS): `-l <windowid>` captures the window, `-o` leaves out its shadow. Apple Support, "Control access to screen and system audio recording on Mac", https://support.apple.com/guide/mac-help/control-access-screen-system-audio-recording-mchld6aa7d23/mac: the Screen Recording permission that `screencapture` needs.
- SDL 3.4.10 `SDL_surface.h` lines 715-730 (`SDL_SavePNG`, available since SDL 3.4.0): the capture writer.
- `man clock_gettime` (macOS): `CLOCK_THREAD_CPUTIME_ID` and `clock_gettime_nsec_np`.
- `man system_profiler` (macOS): the `-json` option. The `_spdisplays_pixels`, `_spdisplays_resolution` and `spdisplays_main` keys that the harness reads are not documented there; they are what `system_profiler SPDisplaysDataType -json` prints on macOS 26.6.1.
- SDL 3.4.10 `src/video/cocoa/SDL_cocoamodes.m`, `Cocoa_InitModes`: the main display is added first, then the others, and displays that mirror another display are skipped. The harness orders `system_profiler` displays main first to match SDL's display index.
- jm2/dxvk at `dd3a05b`: `README.md`, `KISAK_MACOS.md`, `meson.build`, `.gitmodules`, `src/util/config/config.cpp:617` (Bloodlines profile), `src/d3d9/d3d9_device.cpp` lines 4958-4971 and 5006-5029 (DISCARD rules), `include/native/windows/windows_base.h:205-206`, `include/native/wsi/native_sdl3.h`, `LICENSE`.
- mesa 26.2.5 `src/kosmickrisp/vulkan/kk_debug.c` lines 13 and 17; Homebrew formulae via `brew info --json=v2`.
- Microsoft Learn and windows-rs `61f973beb0ce06590d6bbefceb236fc50de82729` for the d3d9 structures and slot order.

## Known limits (M4)

- The first menu frame stalls the guest for about 1.0 to 1.1 s (pipeline compilation inside `Present`; no graphics pipeline library on KosmicKrisp). Present waits under host load can also exceed 100 ms.
- The built-in display's 1280×1024 mode is not a real display mode; the engine's five table modes are added to the list and the window is simply 1280×1024 points.
- The developer-overlay dots at the top right of the picture are expected (`-dev`).
- `+developer 0` would remove them but also silences `+mm_verbose`, so stage 11 would never fire.
- `QueryInterface`, the UP draws and volume textures are not reached and stay stubs. `Reset` is bridged: it resizes the back buffer and hands the new size to the engine's aspect and FOV.
- Only the built-in display has been measured; the ultrawide is open.
- The harness pairs `--display N` with the Nth `system_profiler` display, main first. That is checked only for one display; with a mirrored display (skipped by SDL, listed by `system_profiler`) or three or more displays the pairing is untested and may be off.

# M5: Input

M5 makes the main menu answer the mouse and keyboard. The runtime turns SDL3 mouse, key and text events into the Win32 messages the engine already reads, and the user32 shims answer cursor and key-state questions from real state instead of constants. DirectInput stays a null object: the engine's DirectInput device code has no caller, so nothing needed to be built there. Merged on master as the input runtime `f19bf03..b6ac0ac` and the harness `02fcbd1..ce971b1`.

**Not measured here.** Live runs in this project are limited to the main menu (decision of 2026-10-27). The menu checks (hover, console, chords, focus, quit) were run while the pieces were built and are listed below; everything that needs a map, a second display or a reference approved by the maintainer is left to the play test.

## Running it

```sh
X=$DEV/build/all/meson/bloodlines-native
M="$DEV/venv/bin/python -I harness/m5.py"
$X --input-selftest                       # 218 game-free cases, no game needed
$M selftest                               # harness self-test
$M hover|console|buttons|options|newgame|quit|chords|focus --exe $X
$X --run --overlay O --close-after N --input-script O/NAME.txt [--display D] [-- +developer 4]
```

`--input-script` comes before `--`. The default close-after is the script's sleeps plus 60 s. Button and tab positions are not in git: they come from `$BN_REFDIR/m5-menu.json`, which `m5.py seed` writes from the measured button boxes (New 617,438; Load 617,533; Options 617,634; Quit 619,729) and which holds the Options tab points. The script templates in `harness/m5/` (`hover`, `console`, `buttons`, `options`, `newgame`, `quit`, `chords`, `focus`) name positions as `@newgame` and similar; `m5.py render` fills them in and writes `O/NAME.txt`.

## Pieces

| Piece | Commits | What |
|---|---|---|
| P1, input runtime | `f19bf03`, `1126a77`, `782754e`, `b6ac0ac` | `runtime/input.c` and `input.h`, `input_script.c`, `input_selftest.c`; hooks in `window.c`, `main.c`, `trace.c`, `shims/user32.c`, `shims/kernel32.c`, `shims/d3d9_bridge.c`. |
| P2, harness | `02fcbd1`, `4c56dc1`, `ce971b1` | `harness/m5.py`, its self-test and the 8 script templates in `harness/m5/`. |

### The input path

The engine reads input only through window messages and cursor polling (window class `Valve001`, window procedure `0x200FE720`, a 20-entry dispatch table at `0x201AD5B0`). Keys come from lParam bits 16-23 (scan code) and bit 24 (extended): `CGame::MapScanCodeToEngineKey` at `0x200FD830` ignores wParam, so an arrow without bit 24 becomes a keypad key. Buttons come from the MK_ flags in wParam, wheel from the sign of HIWORD(wParam), text from WM_CHAR. The cursor position is polled through `GetCursorPos` and `ScreenToClient` (951 calls each in a 919-frame menu run). Source for these addresses and counts: the M5 spec's measured-inputs table.

What `f19bf03` does:

- **Events to messages.** `rt_pump` (main thread only, where all SDL calls stay) turns SDL mouse motion, buttons, wheel, key and text events into WM_MOUSEMOVE, the button messages, WM_MOUSEWHEEL (one tick is +/-120), WM_KEYDOWN/UP and WM_SYSKEYDOWN/UP, and WM_CHAR on the existing guest queue. Scan codes come from a hand-written table from Microsoft Learn's HID-to-Scan-1 list. Text input starts when the window is first shown.
- **One cursor.** The runtime keeps one cursor in client pixels (1280×1024 game coordinates, mapped through the present rectangles the bridge records), the button mask, the set of held keys and the focus flag, under its own mutex (`rt_wait` also pumps, after releasing the guest lock).
- **user32 shims.** `GetCursorPos` and `SetCursorPos` use that cursor; `GetKeyState` answers from the held-key set, updated when the guest takes the message, not when it is posted; `ShowCursor`, `SetCursor` and `SetCapture` set the host cursor shape and visibility at the end of each pump. A host warp needs both the state's focus flag and the SDL window's input focus, so a scripted focus loss or a background window never moves the host pointer.
- **Command key.** A chord with the command key (⌘) posts nothing to the guest; a key held before it still gets its key-up. The host shortcuts are in "Extras" below.
- **Focus loss.** Losing focus first flushes a pending move, then posts key-ups and button-ups for everything held, then WM_ACTIVATEAPP 0.
- **Focus return.** With the game in full screen, ⌘-Tab out and back (or a Dock-style `open -b`) made the app active again with no key window: `NSApp.keyWindow` and `mainWindow` were nil, the SDL window was visible but not on the active Space, so macOS never brought the full-screen Space back, no WM_ACTIVATEAPP 1 came and no frame reached the screen (4 of 4 runs, the launcher's own launch path among them). The guest never hid or minimised the window (its `ShowWindow` and `SetWindowPos` show/hide calls are logged), SDL did not minimise it (its default only minimises exclusive full screen) and its collection behavior is `FullScreenPrimary` only. A 30-line AppKit app with one window put in full screen by `toggleFullScreen:`, no SDL and nothing of the game, does the same on macOS 26.6.1 after ⌘-Tab, `open -b` or a click on its Dock icon (active, `keyWindow` nil, window off the active Space), and the same call brings it back, so the cause is the system's activation, not our window code. On `NSApplicationDidBecomeActiveNotification` with no key window, the runtime now calls `makeKeyAndOrderFront:` on the visible game window (Apple: "Moves the window to the front of the screen list, within its level, and makes it the key window; that is, it shows the window"), logged as `window made key again: the app became active with no key window`; the Space comes back, WM_ACTIVATEAPP 1 follows and pacing resumes within a second.
- **Counters.** At exit: `rt: input moves N buttons N keys N chars N dropped-chars N chords N warps N`. `moves` counts motion events received (SDL events plus script `move` steps), before they are coalesced into WM_MOUSEMOVE.

Fixes after the first commit: `1126a77` keeps scripted text alive until the pump polls it (SDL 3.4.10 does not copy the `text` pointer of a pushed text event, so two `text` commands in one step overwrote each other; the script player now uses a rolling pool of 256 entries); `782754e` flushes the move on focus loss and caps a scripted `wheel` at +/-64; `b6ac0ac` adds the two missing bit-24 cases (below).

### The input script

`--input-script FILE` plays one command per line on absolute due times; the clock starts when the main window is first shown. The script only pushes SDL events (and takes captures), so it exercises the same path as a real device. Positions are game coordinates, `move` X 0..1279 and Y 0..1023. Each executed line logs `rt: script N COMMAND` and the run ends with `rt: script done`.

| Command | Meaning |
|---|---|
| `wait menu` | block until boot stage 11 |
| `sleep S` | wait S seconds (decimal) |
| `move X Y` | move the pointer |
| `down`, `up`, `click` + `left`, `right`, `middle`, `x1`, `x2` | button events |
| `wheel N` | N ticks, +/-64 at most |
| `key K`, `keydown K`, `keyup K` | key press, or only the down or the up |
| `chord K` | a ⌘ chord (stays with the host) |
| `text "..."` | typed characters (WM_CHAR); `\"` and `\\` are the only escapes |
| `focus lost`, `focus gained` | scripted focus change |
| `capture NAME` | PNG to `O/shots/NAME-<UTC>.png` |

`K` is `[a-z0-9]+` (for example `grave`, `return`, `left`, `escape`, `backspace`, `tab`, `a`, `lshift`). Regex waits and `wait console` are rejected. The later commands `look`, `minimise`, `wait map [NAME]`, the relative mouse mode they belong to and `RT_MOUSE_SYSTEM_SCALE` are M6's: see "B: relative mouse, `look`, `minimise`, `wait map`" in M6.

### The harness

`harness/m5.py` renders the templates and checks the log lines named in the contract: `rt: input (WM_*) wp lp` (WM_MOUSEMOVE is not logged), `rt: script`, `rt: cursor ...` and the counters line. Modes: `selftest`, `cases`, `hover`, `console`, `buttons` (`--button loadgame options`, `--back escape|cancel`), `options`, `newgame`, `quit`, `chords`, `focus`, `displays`, `soak`, `render`, `seed` and `check`. Every live mode fails on any `rt: FAULT`, stub stop or new crash report. `4c56dc1` makes the soak generate its own activity and the Options check reopen the dialog after Cancel; `ce971b1` sets the soak's `--close-after` to 100 s (boot to menu takes about 9 to 12 s, plus 62 s of script). The soak needs `moves` of at least 5000, `buttons` above 0 and at least (commands - 50) `rt: script` lines.

## Rulings applied

- Live runs are limited to the main menu; map, display and soak rows are left to the play test.
- The soak runs with `--close-after 100` (above).
- The Options check reopens Options after Cancel and compares `options-reopened` with the untoggled capture; `m5-menu.json` therefore needs `options.tabs` (including Gameplay), `options.tab.<Tab>`, `options.checkbox` and `options.cancel`.
- The `warps` group in the counters line is required.
- Screen references are first native captures approved by the maintainer, compared as in M4 AC6 (same size, inside the static-UI mask 99 % of pixels within +/-8 per channel and mean absolute difference at most 2/255). They live outside git.

## Results per acceptance criterion

"Met" means the commit or self-test named was added and reviewed; "deferred" has the command. `$X` and `$M` are as in "Running it". Numbers come from the input runtime measurements unless stated; the self-tests were not re-run for this section.

| AC | Status | Evidence | Command |
|---|---|---|---|
| AC1 (translation and mapping without the game) | Met by selftest | `f19bf03` and fixes: 211 cases at first ready, 216 after review, 218 PASS at `b6ac0ac`. Nine mutations (up without bit 24, fixed 1280×1024 mapping, WM_CHAR during a chord, no key-ups on focus loss, warp while unfocused, Enter's WM_CHAR after its key-up, GetKeyState set at post time, no extended bit on the translated key-up, none on the release-all key-up) each fail a case. | `$X --input-selftest` |
| AC2 (hover highlights) | Partly met on the main menu | New Game rectangle differs from idle by 10.82/255 (needs at least 8). Away vs idle was 2.01/255, over the limit of 2: menu particle noise with no m5 mask yet, so the away check is not met. | `$M hover --exe $X` |
| AC3 (Load Game, Options open, Escape goes back) | Deferred | Needs the approved references. Escape does not close Options; the harness uses Cancel (about 1058,753). | `$M buttons --back cancel --exe $X` |
| AC4 (Options pages, a setting toggles) | Deferred | Tab points at y about 300: Keyboard 214, Mouse 352, Audio 490, Video 628, Visual 766, Gameplay 904; no Advanced tab is visible. | `$M options --exe $X` |
| AC5 (typing, arrows, Backspace, Enter, console) | Met on the main menu | `console` prints `M5_ABC` then `M5_XZ`; the Left key-down has bit 24 set (the self-test cases for bit 24 on Up fail under mutation, AC1). | `$M console --exe $X` |
| AC6 (New Game reaches character creation) | Left to the play test | The click registers and `map sp_genesisdevice_1` starts. The input run stopped at a missing msvcrt address, fixed by `46e20c4`; character creation was then reached in the first play test described under M6. No chargen reference was compared. | `$M newgame --exe $X` |
| AC7 (Quit exits cleanly) | Met on the main menu | Exit 0 2.4 s after the click, with `LauncherMain returned 0`. | `$M quit --exe $X` |
| AC8 (command chords stay with the host) | Met on the main menu | No input line between the chord script lines; counters show `chords 3`. | `$M chords --exe $X` |
| AC9 (focus loss leaves nothing held) | Met on the main menu | Key-ups and button-up precede WM_ACTIVATEAPP 0; self-test cases cover the order, including the held Up key (lParam `0xC1480001`, `b6ac0ac`). | `$M focus --exe $X` |
| AC10 (both displays map the pointer) | Deferred | The ultrawide has not been measured; no `--display` run is recorded here. | `$M displays --displays 0 1 --exe $X` |
| AC11 (host cursor follows the game) | Partly met by selftest | Self-test covers `SetCursor(NULL)`, `ShowCursor(FALSE)` from 0 and each IDC_ shape. The 7 start-up `SetCursorPos` calls all target client (640,512) and log `(no warp)` in harness runs, because the window never has SDL input focus there; 3 more at shutdown target (252,360). | `$M check --what cursor --log LOG` |
| AC12 (DirectInput stays unused) | Not recorded | The first-call rows of the M5 runs were not checked for this section; the code facts (no caller of the device code) are in the spec. | any live mode, for example `$M hover --exe $X`; it checks the dinput8 first-call rows (`harness/m5.py:381`) |
| AC13 (a player clicks through the menu) | Left to the play test | The maintainer's play test under M6 went from the menu through New Game; Load Game, Options and Quit were not reported. | `$X --run` by hand |
| AC14 (frames and pump gaps hold under input) | Deferred | Device loss under host load was seen before any input was posted; see M6, "Device lost under load". | `$M soak --n 3 --exe $X` |
| AC15 (earlier milestones pass) | Partly met at `782754e` | The gate ran on its pre-rebase twin and passed: clean build, `--gfx-selftest` 32 PASS, `m4.py cases`, null boot `--until console` exit 0, M1 hash `7d3e33d5612508900a12bc636107554dfc133ff04c2b924b3e0e8db5c11c1315` unchanged, game manifest 0 failures. The M4 menu compare fails against the provisional reference (see known limits); the M4 soak, the M2 regression, `lipo -archs` and `git ls-files` were not run. Nothing was re-run for this section. | as AC-R1 under M6 |

## Sources (M5)

- Commits `f19bf03`, `1126a77`, `782754e`, `b6ac0ac`, `02fcbd1`, `4c56dc1`, `ce971b1` in this repository.
- Microsoft Learn: Keyboard Input Overview (HID-to-Scan-1 table, keystroke flags), WM_MOUSEWHEEL, WM_XBUTTONUP, GetKeyState, ShowCursor, SetCursor. SDL3 wiki: SDL_PushEvent, SDL_TextInputEvent, SDL_StartTextInput.

## Known limits (M5)

- Only the main menu was exercised live; no reference captures were approved, so the AC3, AC4 and AC6 compares are deferred.
- `reference/m4-menu.png` was taken with the cursor stuck at (0,0). With input working the cursor sits at (640,512), so the M4 compare fails in the blocks around it; the reference must be retaken or the mask must exclude that area.
- The hover away-vs-idle difference is 2.01/255, over the 2/255 limit (AC2), because of menu particles; an m5 mask is needed.
- An intermittent freeze with `VK_ERROR_DEVICE_LOST` about 56 to 86 frames after stage 11 occurs under host load, before any input is posted. M6's hang detector stops such a run with exit 15.
- Escape does not close Options; Cancel does.
- The ultrawide display is unmeasured.

# M6: In game

M6 takes the runtime from the main menu into the game. The map-load path (character creation, then `sp_genesisdevice_1`, `sp_theatre` and `sp_tutorial_1`) now runs on the arm64 runtime, with a relative mouse for looking around and a hang detector that turns a lost GPU into a clear stop. Merged on master as `910ff0f..d40d989` plus the hang detector `03d1a18` and `a064cfb`. The plan is to finish all milestones first and test properly once the game is in the porting kit.

**Not measured here.** The quiet-machine performance measurement (AC-A4) and every live run are left to the maintainer's play test (decision of 2026-10-27). The pieces below are checked by self-tests and review only; the one live evidence is the maintainer's own first play test.

## Running it

```sh
X=$DEV/build/all/meson/bloodlines-native
$X --gfx-selftest                                   # bridge and shim self-tests, no game needed
$DEV/venv/bin/python -I harness/m6.py selftest   # harness self-test
$X --run [--overlay DIR] [--close-after S] [-- +map sp_genesisdevice_1]
```

The deferred performance measurements, for the maintainer or a later perf run. AC-A4 is the soak on the default map `sp_genesisdevice_1` (`FIRST`, `harness/m6.py:48`, mode at `:1053`); AC-B7 is `perf` on `sm_pawnshop_1` (`:1063`, with a generated walk script):

```sh
M="$DEV/venv/bin/python -I harness/m6.py"
$M soak --n 3 --exe $X       # AC-A4
$M perf --n 3 --exe $X       # AC-B7
```

Two load limits apply. The harness refuses to start `perf` and `soak` at a 1-minute load average of 9 or more (`LOAD_LIMIT = 9.0`, `harness/m6.py:32`, used in `gate_reasons`, `harness/m6.py:478-481`). The project's performance rule is stricter: load below 8, GPU use at most 30 %, and no other app window over the game. The GPU and window conditions are part of the run protocol, not the harness; nothing in `harness/m6.py` checks GPU use. A run at load 8 to 9 starts but is not a performance run.

- Captions are off by default: the shipped config sets `cl_captions 0`, so the dialogue history window that carries the NPC lines stays hidden. Turn it on with Options > Audio > Captions, or press HOME in a conversation (`dlghist`). Backlog note P5.
- The mouse is relative while the guest hides its cursor (look mode). `RT_MOUSE_SYSTEM_SCALE=0` switches off the macOS pointer acceleration curve (raw deltas); the default follows the system scale. The log prints `input relative scale system|raw`, `input relative on|off` and `input relative switches N recentres N looks N`.
- Script commands added to the input script: `look DX DY` (a relative motion, each in -4096..4096), `minimise SECONDS` (minimise the window, restore it after the delay) and `wait map [NAME]` (wait for the next map-ready event, optionally for that map).

## Pieces

| Piece | Commits | What |
|---|---|---|
| H, hang detector | `03d1a18`, `a064cfb` | Exit 15 on a stuck bridge call or a dxvk device loss; harness checks. |
| A, bridge interface sets and map readiness | `910ff0f` | The d3d9 bridge covers whole interface sets; a map-ready event and stage 12. |
| B, relative mouse, scripting | `3202528`, `0f3cece`, `55f1dab` | Relative mouse mode, `look`, `minimise`, `wait map`, click and exit fixes. |
| C, shims and the ready rule | `b61d95a`, `7daae5b`, `adc53c1`, `d40d989` | wsock32 ordinals, font raster failures, the map-ready rule, bridge nits. |
| M, harness | `9947181`, `13481d2`, `a9ea205`, `582033d`, `dc1b897` | `harness/m6.py`, its self-test and the 14 script templates in `harness/m6/`. |

### M: the harness

`harness/m6.py` drives the in-game checks the same way `m4.py` and `m5.py` drive the earlier ones (it imports both). `9947181` adds it with its self-test and the 14 script templates (`harness/m6/`: chords, dialogue, focus-load, focus, genesis, keys, look, minimise, opening-theatre, opening-tutorial, quick, record, save, walk). `13481d2` is review fixes; `a9ea205` makes the allocator check read the real trace line; `582033d` fixes the graphics frame floor, the side-run gate, gate polling and the dialogue mask; `dc1b897` judges the load per run window for the hang rerun.

Modes (`harness/m6.py MODE`): `selftest` (no game), `cases` (first-call TSV union and counts), `map`, `soak`, `walk`, `look`, `keys`, `chords`, `focus` (`--during-load`), `replay` (`--demo`), `minimise`, `save` (`--quick`), `missing`, `perf`, `chargen`, `opening --from sp_theatre|sp_tutorial_1` and `dialogue`. The live modes share the options `--exe`, `--overlay`, `--close-after`, `--display` and the compare thresholds. `perf` and `soak` refuse a loaded machine (below). A run that hits a hang stop line or a dxvk hung line is rerun once when the load average was 9 or more.

### H: the hang detector

`03d1a18` adds `rt_bridge_t0` and `rt_bridge_name` (stamped in `runtime/rt.c:232-276`; only the watchdog that reads them is in `runtime/thread.c`): every call through a bridge shim stores its name and start time on entry and clears the time on return (a call of 1 s or more logs `gfx slow bridge call NAME N ms`). The watchdog thread stops the run with exit 15 and the line `rt: FAULT graphics (exit 15): gfx hang: <method> blocked <n> ms (load <load average>)` in two cases:

- a bridge call blocked for `HANG_MS` = 5000 ms;
- dxvk's own log (`<DXVK_LOG_PATH>/*_d3d9.log`, polled every 500 ms by `dxvk_log_scan`) contains `GPU likely hung` or `VK_ERROR_DEVICE_LOST`.

The 5000 ms matches dxvk's own: `src/dxvk/dxvk_queue.cpp:268-274` waits on a semaphore for 5,000,000,000 ns, logs "Timeout occured, GPU likely hung." and sets `VK_ERROR_DEVICE_LOST`. `a064cfb` makes the two shared values atomic (release/acquire), reads the whole new part of the dxvk log in a loop instead of one 64 KiB chunk, and lets the harness pass extra `DXVK_CONFIG` text (`--dxvk-config-extra`, appended to the bridge's own `DXVK_CONFIG_VALUE`) and a shader cache path (`--dxvk-cache`). `harness/m4.py` `check_faults` fails an exit-0 log that has a gfx hang line or a dxvk hung line, and its label carries the method, the milliseconds and the load average, with "rerun on a quiet machine" when it fails.

Why the detector exists: the lost device is invisible to the runtime without it. The bridge's lost timer stays 0 and a frozen run ends `rt: gfx lost 0`, `rt: exit 0`.

### Device lost under load

From the research note (read-only, no game run, dxvk `dd3a05b`, KosmicKrisp 26.2.5):

- The device loss is real Metal-side GPU work failing, not a dxvk false alarm. All 10 failing dxvk logs have the Timeout line before any DEVICE_LOST line; the unified log shows `IOGPUMetalError` in every hung run and in no clean run.
- It depends on host load: the main-thread stall at the first menu frame was 5032 to 5237 ms in the 10 hung runs, 1024 to 1526 ms in clean runs on a calm host and up to 2537 to 4064 ms in clean runs under load. Once it passes 5 s dxvk declares the device lost.
- Most likely cause: a Metal command-buffer timeout under contention (about 55 %, medium confidence; Metal's error text is `<private>`). A KosmicKrisp race or event-wait bug is about 30 %.
- Consequences used in M6: game checks gate on host load (project rulings below); a loss fails the run (piece H); `d3d9.maxFrameLatency = 1` is in the bridge's `DXVK_CONFIG_VALUE` by default (ruling 2026-10-29: the play-test reported look lag, "trackpad lag", and the investigation found dxvk's default queue of 3 frames; an idle-menu run showed no hang stop and an overlay p99 of 5.7–12.1 ms), and `dxvk.numCompilerThreads = 4` is offered through `--dxvk-config-extra` and not yet measured as a fix. The A/B under load is still owed, in game past the menu.

### A: bridge interface sets and map readiness

`910ff0f` (`shims/d3d9_bridge.c`, 520 lines changed) grows the bridge from the 72 methods the main menu reaches to whole interface sets: more device methods (caps, back buffer, render targets, off-screen surfaces, stretch, lights, clip planes, state blocks), the full surface, texture, cube texture, vertex and index buffer, query, vertex declaration, vertex shader, pixel shader and state-block sets. Locks are now tracked per level (`lockrec.sub`), so two different levels of one texture or cube can be open at once, up to 8 per object (more stops with exit 5, a second lock of the same level returns `D3DERR_INVALIDCALL`). Texture creates are logged once per format, pool and usage combination (`gfx texture 2d|cube format F pool P usage U first WxH levels L hr 0x…`, at most 64 combinations).

It also adds `runtime/map.h` with `rt_map_present`, `rt_map_ready` and `rt_gfx_frame_count`: a map-ready event and boot stage 12. The trace parser arms on the guest line `R_NewMap`, takes the name from the next `Map: NAME` line, and the bridge's `Present` feeds the counter. Stage 12 is `rt: stage 12 map <NAME>` (printed once, for the first ready event); after it, the frame statistics and the steady pump gap restart from stage 12. `harness/m4.py` accepts one trailing stage 12 (a second one fails).

### The map-ready rule

A map is ready when **30 Presents have been counted since `R_NewMap` and at least 1 s has passed since the first of them, with no gap between Presents over 250 ms** (a gap over 250 ms restarts the count at 1). The log line is `map ready NAME at +S s`.

`910ff0f` used 30 Presents with a 250 ms restart; `adc53c1` added the 1 s span (`s_map_start`, `runtime/trace.c`). The measurement behind it: after `R_NewMap`, a burst of loading-screen Presents of 2 to 7 ms fired the old rule at +12.41 s; stalls of 477 and 257 ms followed during the player load, and steady in-map frames began at +13.76 s. The new rule waits out the burst and the stalls. The map times of the maintainer's play test (below) were taken with the old rule, before `adc53c1`.

`d40d989` (bridge nits): `GetData` with a NULL size now passes the span check (`!a[2]`), the 64-combination texture log counts the creates it dropped (`gfx textures past 64 combos n N`, `rt_gfx_tex_overflow`), and the steady-gap label reads `d3d9 draw`.

### B: relative mouse, `look`, `minimise`, `wait map`

- `3202528`: when the window has focus and the guest's cursor is hidden (`RT_CUR_HIDDEN`), the input state switches to relative mode and calls `SDL_SetWindowRelativeMouseMode`. Motion events add `xrel`, `yrel` (scaled by the display density and the present rectangles) to a fractional accumulator, whole pixels go to the guest cursor, and the guest-requested re-centre warp is skipped (counted as `recentres`). `look DX DY` injects such a motion, `minimise SECONDS` minimises and restores the window.
- `0f3cece`: `wait map [NAME]` blocks the script until a new map-ready event (a count above what the script has already consumed, and the name matches when one is given; `rt_script_wait_done`). It is how a scripted run gets from the loading screen into the level without a fixed delay.
- `55f1dab`: while relative, a click keeps the guest cursor where it is (the click position is not re-mapped), and on leaving relative mode the host pointer is warped to the guest cursor's client point (`rt_input_client_point`). This also adds `RT_MOUSE_SYSTEM_SCALE`.

Reason: the first play test showed an absolute mouselook that warped the pointer 20,999 times in about 24,000 frames (backlog P4: "I see cursor and it's locked to screen center", "mouse lags to turn (trackpad)").

### C: wsock32 ordinals, font raster failures

`b61d95a` and `7daae5b` (`shims/misc.c`, `shims/fail.c`). Imports first reached in M6, from the map load (the engine's network address formatting runs on every map load, also with `-noip`). Ordinals are mapped from how the engine uses them:

| Import | Meaning | Kind | First caller | Behaviour |
|---|---|---|---|---|
| WSOCK32 #9 | `htons` | shim | engine host-string to address conversion (port) | host `ntohs`, a 16-bit byte swap |
| WSOCK32 #10 | `inet_addr` | shim | same, for a dotted host | host `inet_addr` (no WSAStartup needed); a NULL string returns `INADDR_NONE` |
| WSOCK32 #15 | `ntohs` | shim | the engine's address-to-string function, called by the map-load-finished path | host `ntohs` |
| WSOCK32 #52 | `gethostbyname` | fail | same conversion, for a name | returns NULL (no network) |
| WSOCK32 #111 | `WSAGetLastError` | fail | after a UDP socket open failure | returns 10093 (`WSANOTINITIALISED`) |
| GDI32 `CreateDIBSection` | Win32 font raster | fail | the vgui material-surface font create, when a scheme font has no bitmap `.fnt` | writes NULL to the bits pointer, returns NULL |
| GDI32 `GetCharABCWidthsA`, `GetCharABCWidthsW`, `ExtTextOutW`, `MoveToEx` | same fallback | fail | same | return 0 |
| GDI32 `GetGlyphOutlineA` | same fallback | fail | same | returns `GDI_ERROR` (`0xFFFFFFFF`) |

The scheme fonts that fall into this path (names only: Arial Unicode MS, Percolator Expert, Copperplate Gothic BT, Dominican) have no `.fnt` file, so their glyphs are blank. This is backlog P7 (blank text in the chargen and options combo boxes, cosmetic). The refuted theory is that this caused the invisible subtitles: dialogue uses a bitmap font (P5 is the `cl_captions` default).

`gfx-selftest` has cases for all of these (`shims/d3d9_selftest.c`, `wsock_cases` and the font cases), and `adc53c1` extends the map-ready cases in the same file.

## The maintainer's first play test

2026-10-27, 17:47 to 17:55 local, on the piece C worktree build `6a8ae57` (before the merge to master, same fix as `7daae5b`), by the maintainer. The session went from the menu to New Game, character creation, then:

| Map | Ready at (old rule: 30 Presents, 250 ms restart, no 1 s span) |
|---|---|
| `sp_genesisdevice_1` | +14.5 s |
| `sp_theatre` | +64.2 s |
| `sp_tutorial_1` | +374.3 s |

It ended `rt: exit 0` and `rt: gfx lost 0`. Frames: p50 11.94 ms, p95 62.62 ms, p99 82.68 ms, and 10 % of frames over 33 ms (`frames over 33.3 ms n 2488`; backlog P3). These are play numbers with guest logging on and the machine in use, not performance numbers.

Known issues from the session are in the backlog note `main/plans/bloodlines-port-play-test-1-backlog`, items P1 to P7: theatre level script error and scene stutter (P1), black spike triangles from the DISCARD bounce block (P2), cutscene lag (P3), mouse (P4, fixed by piece B), invisible subtitles (P5, `cl_captions 0`), an out-of-memory tracking line at device creation (P6), blank combo box text (P7). Decision: small bugs are fixed later, in a polish round after the porting kit test.

## Rulings applied

- **GPU functional gate.** A functional check (a map loads, a script finishes) is valid only when GPU use is at most 60 %; this gates the machine, not the game, and is not a performance limit. It lives in the run protocol, not the harness.
- **Performance rule.** A performance run needs a 1-minute load average below 8, GPU use at most 30 % and no other app window over the game. The harness itself refuses only at a load of 9 or more (`harness/m6.py:32`); the stricter numbers and the GPU gates are the protocol's. Numbers from any other run are not performance numbers.
- **Live runs** are left to the maintainer's play test from 2026-10-27; the pieces are checked by self-tests and review.
- Map-ready is 30 Presents with at least 1 s since the last stall over 250 ms (above). `910ff0f` used 30 Presents with a 250 ms restart, and the play-test map times were taken with that rule.

## Results per acceptance criterion

Spec numbering AC-A1 to AC-R1. "Met" means the commit or self-test named was added and reviewed; no live run was made for any row, and the self-tests were not re-run for this section. Only the maintainer's play test (above) is live evidence. `$M` is `$DEV/venv/bin/python -I harness/m6.py`, `$X` the runtime, each with `--exe $X` where a mode needs it. Commands for the deferred rows are in the last column.

| AC | Status | Evidence | Command |
|---|---|---|---|
| AC-A1 (no stub, SetPriority bridged, one case per added d3d9 method) | Met by selftest, in part | `910ff0f` adds the bridge rows and `shims/d3d9_selftest.c` cases (the spec counts 93). The method list is the rows of `k_m` in `shims/d3d9_bridge.c`; first callers are not recorded because no live run was made. The Win32 imports are in the table under C. | `$X --gfx-selftest`; `$M cases --union <first-call TSVs of the runs>` |
| AC-A2 (`sp_genesisdevice_1` loads, stage 12 within 60 s of stage 8) | Deferred (play test shows it) | Stage 12 and the ready rule: `910ff0f`, `adc53c1`. The maintainer's run reached the map at +14.5 s, exit 0 (old rule). | `$M map --map sp_genesisdevice_1 --close-after 90` |
| AC-A3 (map drawn, matches approved reference) | Deferred | The maintainer saw the character-creation 3D model draw; no reference compare was made. | same run; the reference compare is part of `$M map` |
| AC-A4 (frames keep coming; p99 at most 33.3 ms, gap at most 100 ms) | Deferred | The play test's frames (p99 82.68 ms, 10 % over 33 ms) are play numbers, not this measurement. | `$M soak --n 3 --exe $X` |
| AC-A5 (no shader fails) | Partly met | The maintainer's play run printed `rt: shaders vs_1_1 1172 vs_2_0 184 ps_1_1 30 ps_1_4 7 ps_2_0 34 failed 0`; the scripted map runs were not made. | any map run (`$M map`) prints the line |
| AC-A6 (idle map survives 10 minutes) | Deferred | The play session was 8 minutes (17:47 to 17:55), exit 0, `gfx lost 0`; not the scripted 620 s run. | `$M map --map sp_genesisdevice_1 --close-after 620 --guest-quiet` |
| AC-B1 to AC-B3 (walk, look, strafe, crouch, Escape) | Look met by selftest; rest deferred | `3202528`, `55f1dab`, `runtime/input_selftest.c` cover relative look and click behaviour. Capture compares are live. | `$M walk` (B1), `$M look` (B2), `$M keys` (B3) |
| AC-B4 (focus loss leaves nothing held) | Deferred | Focus code unchanged by M6; no M6 live check. | `$M focus` and `$M focus --during-load` |
| AC-B5 (chords stay with the host) | Deferred | As AC-B4. | `$M chords --map sm_pawnshop_1` |
| AC-B6 (replay kept as a regression test) | Harness mode present, live run deferred | `replay` is in `harness/m6.py` (`cmd_replay`, `:788-813`); it was not run. Default: `harness/m6/walk.txt` twice, comparing the w2 captures. `--demo`: `harness/m6/record.txt`, then `+timedemo m6walk`. | `$M replay` (`--demo` for the engine demo path) |
| AC-B7 (walking performance measured right) | Deferred | `perf` mode, `sm_pawnshop_1`. | `$M perf --n 3 --exe $X` |
| AC-C1 (each opening map loads) | Partly deferred | `sp_genesisdevice_1`, `sp_theatre` and `sp_tutorial_1` reached in the play test (+14.5, +64.2, +374.3 s, old rule); `sm_pawnshop_1` and the approved captures not made. | `$M map --map N --close-after 90` for `sp_theatre`, `sp_tutorial_1`, `sm_pawnshop_1` |
| AC-C2 (character creation leads on) | Deferred (play test shows it) | The maintainer went from New Game through character creation to `sp_genesisdevice_1` and on to `sp_theatre`. | `$M chargen` |
| AC-C3 (theatre leads to tutorial) | Deferred (play test shows it) | `sp_theatre` to `sp_tutorial_1` in the play test; known theatre script error P1. | `$M opening --from sp_theatre` |
| AC-C4 (dialogue with Python scripts) | Not covered | Subtitles hidden by `cl_captions 0` (P5); no dialogue compare made. | `$M dialogue` |
| AC-C5 (tutorial ends in the haven) | Not covered | The play test ended in `sp_tutorial_1`. | `$M opening --from sp_tutorial_1` |
| AC-C6, AC-C7 (save and quick save) | Not covered | No M6 commit or play-test line shows them. | `$M save` (C6), `$M save --quick` (C7) |
| AC-C8 (a player plays the opening) | Partly done by the maintainer | First play test 2026-10-27, up to the tutorial; save, load and the haven were not reported. | `$X --run` by hand |
| AC-R1 (earlier milestones pass) | Not covered | The regression set was not run for this section. | `m4.py` menu, soak and cases; `m5.py` console, chords, focus and quit; `$M replay`; the M3 null boot `$X --run --gfx null --until console`; the M1 and M2 regressions (`tools/recomp/build.py` prints `ALL PASS`; `tools/recomp/build_all.py` into a fresh `--out`, with both `native-results.json` files at sha256 `7d3e33d5612508900a12bc636107554dfc133ff04c2b924b3e0e8db5c11c1315`, the hash in M4 AC15 above); `cd "$GAME" && shasum -a 256 -c <repo>/docs/game-manifest.txt`; `git ls-files` (no capture, reference, save, demo, log or TSV); `lipo -archs $X`. Every live `m6.py` mode refuses to start when the game folder holds a file the manifest does not list (`game_files`, `harness/m6.py:484`; the `REFUSED ... file in the game folder` lines, `:523-533`). |
| Hang detector (not an AC) | Met by selftest | `03d1a18`, `a064cfb`; `harness/m4.py` and `harness/m6.py` self-tests: an exit-0 log with a gfx hang line or a dxvk GPU-hung or device-lost line fails. | `$M selftest` |

**DXVK_CONFIG A/B (H2).** Not run under load; `d3d9.maxFrameLatency = 1` is adopted as the default now (ruling 2026-10-29, reasons at the M6 consequences above), so the A/B owed is in game past the menu. To compare, run each arm on the same build under the same load: A is the bridge's own `DXVK_CONFIG_VALUE`; B adds `dxvk.numCompilerThreads = 4` through `m4.py menu --dxvk-config-extra "dxvk.numCompilerThreads = 4"` (any `m4.py` mode takes it; `harness/m4.py:1180`, composed in `dxvk_config`; `m6.py` has no such option). Run six 30 s runs per arm under natural load only, with exactly one real `build_all.py` compiling (never synthetic CPU loaders), and record the 1-minute load average with each; count hang stops and the worst `rt: max pump gap`. Run a control with no build. Then `m4.py soak --n 3` per arm in quiet. B is adopted only with fewer hang stops under load and a quiet p99 at most 1 ms worse than A. `--dxvk-cache DIR` keeps the shader cache across runs; leave it off for acceptance runs.

## Open questions Q1 to Q13

| Q | Question | Answer |
|---|---|---|
| Q1 | What ends a map load; where does the name come from? | Settled from static facts: the name is the guest line `Map: NAME`, ready is the map-ready rule above. |
| Q2 | Do `Map:` and `R_NewMap` print with developer 0? | Settled from static facts: they print in every run, quiet mode included, because `-dev` sets developer 1. A typed `+developer 0` would silence them and is not supported. |
| Q3 | Do single-player demos work? | Unmeasured. Default: the scripted-input replay (`$M replay`). |
| Q4 | Which inputs does character creation need, and does the theatre need input to reach the tutorial? | Unmeasured. Default: measure the chargen screens into `m6-chargen.json`; wait up to 600 s in the theatre. The maintainer's play test went through both. |
| Q5 | Does dialogue advance with `-nosound`? | Settled, no: pacing comes from the `.vcd` scenes and `SoundLength` is 0.0 under `-nosound`; no silent-duration shim is needed. |
| Q6 | What save file name does `save m6a` write; does `+load` work? | Unmeasured. Default: accept any new file whose name contains `m6a`; load from the console if `+load` fails. |
| Q7 | What difference counts as idle in a map? | Unmeasured. Default: 2/255 in the world mask. |
| Q8 | May the tutorial exit use `ent_fire`? | Settled, yes: `ent_fire` on `trig_leave_tutorial`, with `sv_cheats 1` if needed. The played path is AC-C8. |
| Q9 | What does Present return while minimised? | Unmeasured. Default: treat out-of-date or occluded results as not lost. Read from the code it returns OK and the 5 s stop does not fire; check with `$M minimise`. |
| Q10 | Does the original game also print the `<map>_patch` NameError? | Settled, yes: the game calls `%s_patch()` for every map and the vanilla scripts do not define it (static reading). |
| Q11 | What turns relative mode on? | Settled from static facts: the game hides the cursor (`SetCursor(NULL)`) while the window has focus. Check with `$M look`. |
| Q12 | How long may a map load take? | Default kept, 60 s from stage 8 to ready on a quiet machine. The play test measured `sp_genesisdevice_1` at +14.5 s (old rule). |
| Q13 | How is a dialogue answer chosen? | Unmeasured. Default: number key 1; if nothing happens, click the first answer's position. |

## Sources (M6)

- Commits `a064cfb`, `03d1a18`, `910ff0f`, `3202528`, `0f3cece`, `55f1dab`, `b61d95a`, `7daae5b`, `adc53c1`, `d40d989` in this repository.
- jm2/dxvk at `dd3a05b`: `src/dxvk/dxvk_queue.cpp:268-274`.

## Known limits (M6)

- The performance measurement and every live run are deferred to the maintainer's play test.
- The play test shows 10 % of frames over 33 ms and a p99 of 82.68 ms; causes are in backlog P3 and not fixed.
- Device loss under load is mitigated by detecting it (exit 15), not prevented; the research note puts the cause at a Metal timeout (about 55 %) and leaves it open.
- P1, P2, P5, P6 and P7 are open (backlog note).
- A relative mouse needs the guest to hide its cursor; menus use the absolute mouse.

# M7: Sound and video

M7 adds sound output, MP3 music and the Bink intro movies to the runtime. Based on master `2325361`.

## Running it

```sh
X=$DEV/build/all/meson/bloodlines-native
$X --run [--nosound] [--novid] [--player] [--audio-capture]
$X --audio-selftest [--game DIR] [--overlay DIR]
```

Fresh defaults (no `settings.txt`, or a line reset with `name=`): overlays off with `--player` (all without), no FPS item in the menu bar in any mode (the overlay shows the FPS), aspect `4:3` (the largest 4:3 window that fits the usable area, so the engine aspect is 4:3 and the Hor+ field of view is the original one; it is read at launch, so it is set in `settings.txt`, `launcher.py set --aspect` or the Kit.app Aspect popup, not in the game), MetalFX off, the mouse always captured while the guest hides its cursor, and the pad type found from the connected pad. The Game menu and the overlay list only options that change while the game runs and are not in the game's own Options: Overlays (⌘1: all, FPS only, off), Picture mode (⌘2, MetalFX and off; `native` is a dev-only launch value that a player build loads as `off`, with no warning, and writes back as `off`). A hidden control is not shown: the Game menu and the overlay leave out any row that cannot work on this Mac. ⌘3 to ⌘8 change nothing; only the generic `rt: input host chord` line is logged. There is no Developer info, Controller type, Aspect, Texture filtering or HDR row (texture filtering and HDR are in the game's Video options). The native menus also drop SDL's disabled Settings item and the Window menu's Close (⌘W), which would only quit the one window. In macOS full screen the overlay sits below the revealed menu bar. The old `interp`, `smoothing`, `hdr`, `controller`, `aniso` and `mouse` keys of an old `settings.txt` are read and ignored without a warning. A resolution picked in the game's Options, Video is stored as `aspect=WxH` in `settings.txt` once the game has started (stage 6; earlier writes are ignored). The asked size is kept exactly when it is at least 640 wide and 480 high and not 640x480 itself: the engine drops 640x480 from its mode list (`engine+0xfc8f8`, checked by `test_uppatch.py`), so that size and anything smaller become 800x600, the engine's smallest mode. Any ratio `W:H` (such as `16:10`) is accepted like the named ones. When the size is larger than the usable screen the window shrinks to the usable area at the same shape and Core Animation scales the picture, never stretches it. At display density 2 the native Picture mode renders at twice the window size, so a size that needs more than that is limited by the usable area. Window, full screen and last position are saved in `settings.txt` as soon as they change, also before the first frame. The live selftest `--m9-selftest live [--player]` flips each row in a running instance and checks the state it controls.

Sound is on by default. `--nosound` turns it off (`rt: audio off (--nosound)`; the guest gets `-nosound`); `--sound` is accepted and does nothing. `--novid` skips the intro movies (`rt: bink skipped NAME (--novid)`). `--player` is the player command line: the harness forbids `-dev`, `+developer` and `-nosound` with it. `--audio-capture` writes what the shim hands to the device to WAV files in the overlay (the DirectSound path writes a `dsound` file).

## Pieces

### Sound output

The engine looks up `DirectSoundCreate` in `dsound.dll` and takes its own DirectSound path, as on Windows. `shims/dsound.c` exports it and builds guest vtables for `IDirectSound` and `IDirectSoundBuffer` (order from the SDK header via windows-rs and Microsoft Learn). Only the methods the engine calls are implemented, with the documented semantics: `GetCaps`, `SetCooperativeLevel`, `CreateSoundBuffer` (primary and a 64 KB looping secondary), `SetFormat`, `Lock` (two regions when the range wraps), `Unlock`, `Play`, `Stop`, `GetCurrentPosition`, `GetStatus`, `Restore`. Every other slot logs `audio dsound IFACE slot N NAME is not implemented` and returns `DSERR_UNSUPPORTED`. `--nosound` makes `DirectSoundCreate` return `DSERR_NODRIVER`.

The play cursor comes from real device consumption: an SDL3 stream callback copies exactly the requested bytes from the ring buffer into the stream. The write cursor is the byte count handed to SDL, the play cursor is that count minus what SDL still queues (at most one device period, logged). There is no extra queue and no cushion: when the engine stalls, the device keeps looping the buffer, as Windows does. The engine creates its streaming buffer with flags 0x28 (`DSBCAPS_LOCSOFTWARE | DSBCAPS_CTRLFREQUENCY`), without `DSBCAPS_GLOBALFOCUS`, and stops writing while it has no focus, so on a Mac the loop replayed 0.4 to 0.7 s of stale audio after each ⌘-Tab out. Microsoft Learn ("Creating Secondary Buffers"): "Without this flag, the buffer will be muted when another application or even a dialog box has the input focus." So while the game window has no focus, a buffer with neither `DSBCAPS_GLOBALFOCUS` nor `DSBCAPS_STICKYFOCUS` plays silence and its cursor keeps moving (`audio dsound muted while the game had no focus: … ms` at exit; audio self-test case). It stays silent after the focus comes back until the engine paints ahead of the cursor again: the engine resumed writing up to 298 ms after a ⌘-Tab return, and in that gap the loop played 23 ms of old audio. A cursor pass (the device overtaking the last byte the engine changed between a `Lock` and its `Unlock`; the engine locks the whole 64 KB buffer every time, `engine+0x117287`, so the locked range says nothing about what it painted) is logged with the length of old audio replayed and the engine's quiet time, and counted in the report (`audio dsound cursor passes`, `mix-ahead at Unlock`, `SDL queued after fill`); a pass that replays only zero bytes, such as the buffer the engine clears before a load, is counted as a silent pass. When no output device opens, SDL is switched to the dummy driver and logs `audio silent clock (REASON)`; its stream is still paced in real time.

`shims/audio_sdl.c` keeps the winmm `waveOut*` calls (open, prepare, write, reset, unprepare, close) on SDL3 audio streams as the fallback when a guest does not find `DirectSoundCreate`. The played-bytes counter advances from `rt_now_ns` when silent, so headers still complete. Underruns are counted in the log, and the steady ones only after the frame statistics start.

### Music

`shims/mss32_asi.c` answers Miles' RIB provider lookup for the ASI `.MP3` codec and the `ASI_stream_*` calls, and decodes with dr_mp3 0.7.3 (`third_party/dr_mp3/dr_mp3.h`). A seek is a pending offset: the stream stores the requested offset and passes it to the guest's read callback on the next fill (V5a) in place of the continue marker. `--audio-selftest` includes a track that decodes after a seek. The game never closes a decoder. `vaudio_miles`' destroy method (vtable 0x1000d1a4, slot 1, 0x10001bc0) only calls `operator delete` on the decoder, and the decoder class has no virtual destructor, so the destructor that calls `ASI_stream_close` (0x10001910) never runs. A 20-minute session opened 343 streams. The stream table therefore has no small cap: it grows in blocks of 256 that never move (up to 1,048,576 handles), where a fixed 64 once silenced every voice line after the 64th (`mp3 decoders opened 64 closed 0 failed 279`). dr_mp3 reads in 16 KB chunks (`DRMP3_DATA_CHUNK_SIZE`, minimp3's recommended minimum per `dr_mp3.h`), so each never-closed stream keeps about 48 KB.

### MMX

The engine mixer calls MMX with no CPUID check, so the lifter translates the original MMX set. See the MMX bullet under [Pipeline changes](#pipeline-changes) (M2); nothing M7-specific is added here.

### Bink intros

`shims/bink.c` plays `.bik` files through FFmpeg (libavformat, libavcodec, libavutil), decoding video and audio and feeding the audio to the same SDL output. While audio is queued the video clock follows it: the start time is moved back by the audio played so far, and frame N is due when `(N-1)/fps` has passed. With an empty queue it falls back to wall time. Each movie logs `bink close NAME shown S/F skipped K av drift D ms`, where `av drift` is `(shown - 1)/fps - played`. The bink self-test checks that 31 frames shown at 15 fps against 2.0 s played give 0 ms.

### Developer toggle

There is no in-game developer toggle: the Command-8 chord and the Developer info menu item were removed because they only drew the engine's debug text and dots. The launcher's developer mode passes `-dev` and the `--player` command line does not (`runtime/trace.c`), so a player run starts with `developer` off. `rt_dev_chord_key`, `rt_dev_request`, `rt_dev_take` and `rt_dev_apply` remain as empty functions only for the selftests and the audit; nothing in the game calls them.

## Harness notes

`harness/m7.py` drives the checks, on top of `m6.py`.

- Live runs use SDL's dummy audio driver, so they are silent, and capture to WAV. `--real-device` (on `menu` and `intro`) plays aloud.
- `start7` waits a bounded time (`m6.QUIET_WAIT`, 120 s) for the CPU and GPU gate to clear, and refuses if it does not.
- Menu music (AC-A3): the capture of the main menu must match the reference theme.
- Offline music (AC-B2): `m7.py music` runs `--audio-selftest` and compares each decoded track, found at lag 0, with an FFmpeg decode of the same file; it must not be the menu theme.
- Intro drift: `m7.py intro` checks the `av drift` of each movie within +/-100 ms (`DRIFT_MS`).
- Live runs are allowed in the main menu only.

## Licence

The kit bundles an LGPL-only FFmpeg 8.1.2 (`tools/ffmpeg/build_lgpl.sh`, recorded in `tools/ffmpeg/LGPL-BUILD.md`; `kit_app.py` refuses a library that is not "LGPL version 2.1 or later" or whose configure line has `--enable-gpl`, `--enable-version3` or `--enable-nonfree`). Only a personal build (`--personal`) uses Homebrew's GPLv3 FFmpeg, and it is marked "Do not distribute".

## Known limits (M7)

- Live runs are silent unless `--real-device` is given.
- Live runs are limited to the main menu by project rule.

# M8: Unofficial Patch

M8 lets the runtime start from the Unofficial Patch (UP) mod folder and apply the UP's code fixes at build time. It targets the UP Basic only. Based on master `29d7ad1`. It is personal use: the patched executable is never distributed.

## Running it

```sh
X=$DEV/build/all/meson/bloodlines-native
$X --run -game Unofficial_Patch
$X --up-selftest [--game DIR] [--overlay DIR]
$X --up-list-fixes [--game DIR]
$X --py21-compile DIR [--game DIR] [--mod NAME]
```

## Pieces

### A: mod-folder launch

`-game Unofficial_Patch` makes the mod folder the guest's game directory. `rt_up_init` in `runtime/up.c` reads the folder name from the command line and refuses anything that is not a plain folder name. The UP's Python start-up wraps `sys.path` changes, so the runtime rewrites the guest's `sys.path.append` lines to put the mod folder first and sets `sys.moddir`. `--up-selftest` checks the name parsing, the Python wrap, the save and config paths, and `rt_up_init` in a scratch folder.

The game root is built by `tools/up/game_root.py`: an APFS clone of the vanilla game with the UP Basic tree laid over it, stamped with `gu.sha256`. The vanilla folder is never written.

### B: content checks

`tools/up/formats.py` reads the headers of the UP files and checks them against what the engine loads: BSP version 17, MDL version 2531, and the `.tth` and `.ttz` formats (plus VTF, VMT and RES). The UP has 108 BSP maps: 101 are vanilla names and 7 exist only in the UP. `--py21-compile` compiles the UP's Python sources (35 files) with the guest's Python 2.1 compiler and exits 1 if one fails.

### C1a, C1b: fixes

The fixes patch the lifted code at build time and switch on at run time.

- `runtime/hook.c` is the mechanism. `rt_hook_patch` writes new bytes only when the bytes there are the old ones (the old-byte guard); anything else is logged and left alone. `rt_hook_site` redirects a call site through a cell, and `rt_hook_revert` and `rt_hook_revert_all` restore the old bytes. `rt_hook_claimed` tells whether an address range belongs to a hook.
- `tools/recomp/uppatch.py` reads the rows in `tools/up/patches.toml` and produces the patched build input. `build_all.py --patch-set FILE --up-ini FILE --fallbacks tools/recomp/xflags_fallbacks_up.txt` builds with it; the fallbacks file is the UP's own xflags list. `build_all.py` takes one `--patch-set` file; it adds the aspect set (`tools/m9/aspect.toml`) itself unless `--no-engine-aspect` is given. `uppatch.py --patch-set` may repeat.
- The patched executable embeds the hash of its patch set as `up.sha256` (the kit-version line is `up-build-sha256`). A patched row run without `-game Unofficial_Patch` stops with `UP build needs -game Unofficial_Patch`.
- `--up-list-fixes` prints every row with its switch state, read from the game's `Bin/loader/loader.ini`.
- The 16 rows: `fix_paths_a`, `fix_paths_b`, `floats_female`, `model_lods`, `save_speed_a`, `save_speed_b`, `save_speed_c`, `ragdolls`, `histories`, `coronas_lights`, `claws_stats`, `shadows_rebuild`, `fov_reset_parse`, `fov_reset_store`, `no_tracers`, `protean_warform_b`.
- Five rows are keyed by two switches (`ragdolls`, `coronas_lights`, `fov_reset_parse`, `fov_reset_store`, `no_tracers`). Such a row is on when either switch is on and off only when both are off.
- A plain build after a patch-set build is byte-identical to a plain build made before it.

### D1: the kit option

The unofficial patch is a build-time choice: the app is built with it or without it, and there is no launcher option to switch it. The installer is checked by MD5 (`tools/kit/up.py`, the patch 12.0.8 installer `VTMBup120.8.exe`); a different file is refused. The files are taken out with innoextract 1.9 or newer. The GU (the cloned game root) is built once per game folder and rebuilt when the game folder changes. The app's `bloodlines-native-up` executable is added by `kit_app.py build --up-build`; with the option off the launcher uses the plain executable.

Two kit rules guard the app: `no-up-files` fails if any UP file is inside the app, and `up-executable` checks that the patched executable matches the `up-executable-sha256` and `up-build-sha256` lines in `kit-version`. `gamecheck` accepts a game folder where the Windows UP replaced `vampire.exe` or `bin/binkw32.dll`, by their UP hashes, and prints a note.

## Known limits (M8)

- The fixes that rebuild game logic in the plug-in code are in progress or deferred: door fps, then the history-stat reset, then the C1d list.
- Live checks run in the main menu only.
- Only the UP Basic is supported.

# Wide aspect: 2D UI

At a window wider than 4:3 (for example 3440x1440) the 3D view is Hor+ (`g2b_aspect`, `g2b_view_fov`, `g2b_camera_fov`) and the 2D UI keeps its 4:3 shape. The rows are in `tools/m9/aspect.toml` (owner `m9-g2b`, applied to the PE before the lift) and mirrored in `runtime/g2b_engine.c` (`k_rows`, checked by `aspect_parity` in `tools/recomp/test_uppatch.py`). They only act at run time when the window is wider than 4:3; at 4:3 and narrower (`W*3 <= H*4`) every value is the stock one.

- **Sizes.** The client lays out the 2D UI in a 1024x768 design space: a width is `ScreenWidth * (1/1024) * K`, a height is `ScreenHeight * (1/768) * K`. At a wide window that stretches widths. `g2b_hud` (`hud_write`) writes the double at `client+0x227ec8` as `1/(768*r)`, so `ScreenWidth * d = ScreenHeight/768`: widths scale like heights. At 4:3 `H/(768*W)` is exactly `1/1024`. It is a run-time DATA write that yields to any UP row on `client+0x4bc53` (`rt_hook_claimed`); the gamma ramp is the one other reader of the double that is not a screen-width scale and keeps the `1/1024` float (`g2b_gamma`). This covers the HUD, dialogue, character sheet, inventory and quest log sizes.
- **Positions.** Sites that use the window edge (`W - W*d*K`) or centre (`(W-w)/2`) keep the game's own anchor: left-anchored parts stay at the left edge of the window, right-anchored at the right edge, centred panels centred. The character sheet, inventory and quest log are the other kind: a full-width container (`x=0`, width `ScreenWidth`) whose children sit at a K-absolute x (`ScreenWidth*d*K`), so at a wide window the children were left-biased inside a container as wide as the window. See Character sheet below.
- **Loading screen.** `CLoadingDiscPanel` (ctor `client+0x1670d0`) is a full-screen panel (`SetSize(ScreenWidth, ScreenHeight)` at `client+0x16728d`, `SetPos(0,0)` at `client+0x167298`) that draws its background with `DrawTexturedRect(0, 0, wide, tall)`, so at a wide window the picture was stretched. Rows `g2b_load_size` and `g2b_load_pos` (cells 36 and 37, shims `LoadSize` and `LoadPos`) make the panel `(H*4+1)/3` wide and put it at `(W - width)/2`, only when the call is the full-screen one (`SetSize(W,H)`, `SetPos(0,0)`) and the HUD double was written. The tip text, bar and caps are placed in the panel's own coordinates with `ScreenWidth * d * K`, which is now the panel width over 1024, so they stay on the picture. Outside the panel nothing is drawn by the game.
- **Character sheet, inventory, quest log.** From RTTI (vtable-4, COL, type descriptor in client.dll): `CharEditPanel` (ctor `client+0x171430`, the object is at global `[client+0x5fb14c]`, its frame is at `this+0x10`) is the root; `CharEditCharPanel` (`0x176520`), `CharEditEquipPanel` (`0x1788b0`), `CharEditInfoPanel` (`0x17b550`), `CharEditStatsPanel` (`0x17d750`) and `QuestLogPanel` (`0x188430`) are its children, each `SetBounds(0, y, ScreenWidth - 2*0, h)`. `VCharWizardUI` (ctor `0x174800`) sets its frame `(0, 0, W*d*1024, H)` at two sites. Rows (cells 38 and 40 to 47): `g2b_ui_*_w` replace the `ScreenWidth` call (cell, shim `UiWidth`, pop 0) with `(H*4+1)/3` at a wide window and the stock call otherwise, which also covers the two centring sites inside `CharEditPanel`; `g2b_ui_charedit_frame`, `g2b_ui_wizard_ctor` and `g2b_ui_wizard_show` replace the frame `Panel::SetBounds` call (shim `UiFrame`, pop 4) and set `x = (W - w)/2` only when `x` is 0 and the width is the 4:3 width. The children keep their parent-relative K-absolute x, so they land in the centred 4:3 area. The bordered boxes (the Feats panel, the description, the Experience field, the Active, Completed and Failed quest lists) are one class (ctor `client+0x170e70`) whose Paint (`client+0x170f10`) takes the parent-relative `Panel::GetPos` and draws its border-and-fill image with `client+0x59620`, a material-system quad in raw screen coordinates. Moving the box panels by the offset pushed the right-hand ones outside the 4:3 frame, where vgui does not paint them; row `g2b_ui_box_paint` (cell 51, shim `UiImage`, pop 4) leaves the panels at their stock bounds and adds the offset to the x of that draw call instead. The player model on the Sheet and Info tabs (`CStudiomodelPanel`, Paint `client+0x18b5c0`) builds an off-centre frustum whose scale is `tan(20°)·near/(ScreenWidth/2)` (`+0x18b8cd`); at a wide window that drew the model `W/W43` too large and clipped it at the panel's right edge, so row `g2b_ui_model_k` (cell 54, shim `UiWidth`) gives that one call the 4:3 width, and the frustum centre (`+0x18b8eb`, `W/2`) is already the centred frame's centre. `QuestLogPanel` is a child of the `CharEditPanel` frame, so it needs the width row only. `Panel::SetSize` and `SetBounds` are never hooked as a whole; each row is one call site. The Unofficial Patch build gets the same rows: `g2b_hud` and these rows are `m9-g2b`, the UP set has no row on `client+0x4bc53`, the HUD double or any of these sites, and `test_uppatch.py` proves the sets apply together and do not overlap.
- **Inventory and barter.** The inventory is the `CharEditEquipPanel` tab of the sheet and is covered by `g2b_ui_equip_w` and the frame rows. Its item column (the key-ring frame and the tray under it) is two item-slot panel classes in the `VBarterUI` frame (Paint `client+0x16b0b0` and `client+0x16b1c0`) that take the parent-relative `Panel::GetPos` and draw their image with `client+0x59620` in raw screen coordinates, the same bug as the bordered boxes. Rows `g2b_ui_slot_paint` and `g2b_ui_slot_paint_b` (cells 52 and 53, shim `UiImage`) add the offset to x of those two draw calls. A traced run (`RT_TRACE_CALLS`) showed that the `CHudWeaponSelection` painter (`client+0x1a7fa0`) is not called while the inventory is open, so a row on its x origin moved nothing and was dropped. Barter's selection frame (`client+0x16cfc0`, called from the slot Paint with the slot's parent-relative bounds) draws through `client+0x5c3b0`, a profiling wrapper around a raw-screen material quad, so row `g2b_ui_barter_sel_paint` (cell 55, shim `UiImage5`, pop 5) adds the same offset to its x. `VBarterUI` (ctor `client+0x16b560`) sets its frame in vtable method `0x16edf0`: `SetBounds(ScreenWidth - ScreenWidth*d*1024, 0, ScreenWidth*d*1024, H)` at `client+0x16ee75`, so at a wide window the 4:3 barter panel sat against the right edge. Row `g2b_ui_barter_frame` (cell 50, the `UiFrame` shim) centres it when `x` is `W - w`. `VHotkeysUI`, `VItemInfoUI` and `VMapScreenUI` (methods `0x1838d0`, `0x186890`, `0x1879b0`) already place their frame with `(W - w) * 0.5`, so they are not changed. The barter's "Current Money:" label (`UI_Wallet_Strings` `Name0`, Paint `client+0x5f340`) gets its x from `client+0x5f590`: `(ScreenWidth*d*K - text width) / 2`, centred in a 4:3-wide strip that starts at 0. It is then drawn at that raw screen x, so at a wide window it landed on the bag at the frame's left edge. Row `g2b_ui_money_text` (cell 56, shim `UiTextX`, pop 3) calls the helper and adds the pillar offset to the x it wrote.
- **Dialogue.** `CHudDialog` (ctor `client+0x52980`): `w = ScreenWidth*d*K` (`+0x52aca`), `h = ScreenHeight*(1/768)*K`, `x = (ScreenWidth - w)/2` (`+0x52b36` to `+0x52b4c`), then `SetPos(x, ...)` and `SetSize(w, h)` (`+0x52b87`, `+0x52b9c`); the relayout at `+0x53159` to `+0x531d8` repeats the same centring. So the box is a centred 4:3-scaled panel and is not changed; its children draw at `ScreenWidth*d*K` from the panel origin. A visible misplacement in a conversation needs a run C capture.
- **Tests.** `test_uppatch.py` asserts the stock-double reads of the loading panel are untouched, every call row is a patched `E8` through `jmp [cell]`, each replaces the expected target (`0x1a9950`, `0xcd100` or `0x59620`), no UP row overlaps a ui row, and the pillarbox is the stock panel at 4:3 (640x480, 800x600, 1024x768, 1600x1200) and 4:3 wide, centred and on the HUD scale at 1920x1080, 2560x1080, 3440x1440, 5120x1440. `--m9-selftest engine-load` and `engine-ui` run the same checks on the shims with a recorded guest call (the call they make, with the width and the x they pass); `engine-hud` checks the double at every size.
- **Open.** The game's other full-screen 2D panels that set `(0,0,W,H)` themselves (for example the panel at `client+0x167de0`) and any other window placed with a K-absolute x are not changed; they are listed for the run C captures.

# Extras

The only extras the original game never had are two Game menu items: Overlays (⌘1) and Picture mode (⌘2, MetalFX). The Hor+ aspect is set at launch (`launcher.py set --aspect`, Kit.app or `settings.txt`). All are optional; the README lists them. Settings live in `<overlay>/settings.txt` (`runtime/toggles.c`).
