#!/bin/sh
set -eu
SRC=$(cd "$(dirname "$0")" && pwd)
DEV="${BN_DEV_HOME:-$HOME/Library/Caches/BloodlinesPorter/dev}"
OUT="${PROBE_OUT:-$DEV/build/probe}"
GAME="${GAME_DIR:-$HOME/Games/Vampire Bloodlines (Windows)}"
DXVK="${DXVK_PREFIX:-$DEV/dxvk/inst}"
BREW=$(brew --prefix)
ICD=${PROBE_ICD:-$(brew --prefix mesa)/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json}
LIB=$DXVK/lib/libdxvk_d3d9.dylib

for f in "$ICD" "$LIB" "$BREW/lib/libvulkan.1.dylib" "$BREW/lib/libSDL3.0.dylib"; do
    [ -e "$f" ] || { echo "probe: missing $f"; exit 1; }
done

if [ -d "$OUT/b" ]; then
    meson setup --wipe "$OUT/b" "$SRC" >/dev/null
else
    meson setup "$OUT/b" "$SRC" >/dev/null
fi
ninja -C "$OUT/b" >/dev/null
echo "probe: binary $OUT/b/probe $(lipo -archs "$OUT/b/probe")"

python3 -I "$SRC/extract_shaders.py" "$GAME" "$OUT/shaders"

mkdir -p "$OUT/logs"
export DXVK_WSI_DRIVER=SDL3
export VK_DRIVER_FILES="$ICD"
export SDL_VULKAN_LIBRARY="$BREW/lib/libvulkan.1.dylib"
export DYLD_FALLBACK_LIBRARY_PATH="$BREW/lib"
export DXVK_LOG_PATH="$OUT/logs"
export DXVK_LOG_LEVEL=info
export DXVK_SHADER_CACHE=0
export DXVK_CONFIG="d3d9.memoryTrackTest = True; d3d9.maxAvailableMemory = 1024; d3d9.deferSurfaceCreation = True"
echo "probe: VK_DRIVER_FILES=$VK_DRIVER_FILES"

rc=0
for mode in hwvp swvp; do
    rm -f "$OUT/logs/probe_d3d9.log"
    set -- --lib "$LIB" --shaders "$OUT/shaders"
    if [ "$mode" = swvp ]; then set -- "$@" --swvp; else set -- "$@" --png "$OUT/probe.png"; fi
    echo "probe: ---- run $mode"
    "$OUT/b/probe" "$@" > "$OUT/logs/run-$mode.txt" 2>&1 || rc=1
    grep '^probe:' "$OUT/logs/run-$mode.txt"
    grep -E '^info: +(DXVK: v|Found device|Driver|D3D9: Hiding|vendor ID|device ID|device description|d3d9\.)' "$OUT/logs/run-$mode.txt" | sed 's/^/dxvk: /'
    grep -E '^warn:' "$OUT/logs/run-$mode.txt" | sort | uniq -c | sed 's/^/dxvk: /'
    if grep -q -E '^info: +Found device: .* \(KosmicKrisp' "$OUT/logs/run-$mode.txt"; then
        echo "probe: $mode dxvk device is KosmicKrisp: PASS"
    else
        echo "probe: $mode dxvk device is KosmicKrisp: FAIL"
        rc=1
    fi
    errs=$(grep -c '^err:' "$OUT/logs/run-$mode.txt" || true)
    echo "probe: $mode dxvk err lines $errs"
    [ "$errs" = 0 ] || { grep '^err:' "$OUT/logs/run-$mode.txt" | sort | uniq -c | head; rc=1; }
    [ -f "$OUT/logs/probe_d3d9.log" ] && cp "$OUT/logs/probe_d3d9.log" "$OUT/logs/dxvk-$mode.log"
done
[ $rc = 0 ] && echo "probe: AC1 verdict PASS" || echo "probe: AC1 verdict FAIL"
exit $rc
