#!/bin/bash
# build_cpp_shim.sh — full C++ shim build pipeline, runs end-to-end from WSL.
#
# Usage:
#   ./scripts/build_cpp_shim.sh           # full pipeline: sync -> build -> install
#   ./scripts/build_cpp_shim.sh sync      # sync source to SDK only
#   ./scripts/build_cpp_shim.sh build     # sync + build (no install)
#   ./scripts/build_cpp_shim.sh install   # find latest built .xdl64 + install
#   ./scripts/build_cpp_shim.sh all       # alias for full pipeline
#
# Pipeline:
#   1. rsync cpp_shim/<name>/ -> <SDK>/plugins/<name>/
#   2. invoke Windows cmake.exe to compile (calls MSBuild internally)
#   3. find the built .xdl64 (Windows) or .cdl64 (macOS/Linux convention) under
#      <SDK>/_build_v143/bin/Release/plugins/<name>/
#   4. copy to <C4D install>/plugins/cinema4d-mcp/
#
# After install, fully restart C4D (Reload Python Plugins is NOT enough for
# C++ plugins). Then run scene_nodes_helper_ping to verify.

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SHIM_NAME="cinema4d_mcp_helper"
SHIM_SRC="$REPO/cpp_shim/$SHIM_NAME"

# C4D 2026 SDK location
SDK="/mnt/c/Users/Spenser Dickerson/Documents/C4D_2026_SDK"
SDK_BUILD_DIR="$SDK/_build_v143"

# Windows cmake.exe — called from WSL to drive the actual MSBuild compile
CMAKE_EXE="/mnt/c/Program Files/CMake/bin/cmake.exe"

# Active C4D install's plugins directory
APPDATA="/mnt/c/Users/Spenser Dickerson/AppData/Roaming/Maxon"
C4D_DIR=$(find "$APPDATA" -maxdepth 1 -type d -name "Maxon Cinema 4D 2026*" 2>/dev/null | head -1)
INSTALL_DIR="$C4D_DIR/plugins/cinema4d-mcp"

cmd="${1:-all}"

sync_to_sdk() {
    if [ ! -d "$SHIM_SRC" ]; then
        echo "❌ shim source not found at $SHIM_SRC"; exit 1
    fi
    if [ ! -d "$SDK/plugins" ]; then
        echo "❌ SDK plugins dir not found at $SDK/plugins (adjust SDK= in this script)"; exit 1
    fi
    DST="$SDK/plugins/$SHIM_NAME"
    echo "[1/4] sync: $SHIM_SRC"
    echo "          -> $DST"
    mkdir -p "$DST"
    rsync -a --delete "$SHIM_SRC/" "$DST/"
    echo "      ✅ synced ($(find "$DST" -type f | wc -l) files)"
}

build_via_cmake() {
    if [ ! -f "$CMAKE_EXE" ]; then
        echo "❌ cmake.exe not found at $CMAKE_EXE — install CMake or update path"; exit 1
    fi
    if [ ! -d "$SDK_BUILD_DIR" ]; then
        echo "❌ SDK build dir not found at $SDK_BUILD_DIR"
        echo "   First run the SDK's project generator to create _build_v143/"; exit 1
    fi
    echo "[2/4] build: cmake.exe --build . --config Release --target $SHIM_NAME"
    echo "      cwd: $SDK_BUILD_DIR"
    # Convert WSL path to Windows path for cmake. cmake.exe is fine with
    # either path style on Windows (it normalizes), but using -B with a
    # Windows-style path is most reliable.
    SDK_BUILD_WIN=$(wslpath -w "$SDK_BUILD_DIR" 2>/dev/null || echo "$SDK_BUILD_DIR")
    "$CMAKE_EXE" --build "$SDK_BUILD_WIN" --config Release --target "$SHIM_NAME" 2>&1 \
        | tail -20
    BUILD_RC=${PIPESTATUS[0]}
    if [ "$BUILD_RC" -ne 0 ]; then
        echo "      ❌ build failed (cmake exit $BUILD_RC)"; exit "$BUILD_RC"
    fi
    echo "      ✅ build succeeded"
}

install_built_plugin() {
    if [ -z "$C4D_DIR" ]; then
        echo "❌ Could not locate Maxon Cinema 4D 2026 install dir under $APPDATA"; exit 1
    fi
    echo "[3/4] locate output (.xdl64 on Win, .cdl64 on macOS/Linux per gotcha #27)"
    OUTPUT_FILE=""
    for ext in xdl64 cdl64; do
        candidate=$(find "$SDK_BUILD_DIR" -type f -name "$SHIM_NAME.$ext" 2>/dev/null \
                    | grep -i "release" | head -1)
        if [ -z "$candidate" ]; then
            candidate=$(find "$SDK_BUILD_DIR" -type f -name "$SHIM_NAME.$ext" 2>/dev/null | head -1)
        fi
        if [ -n "$candidate" ]; then OUTPUT_FILE="$candidate"; break; fi
    done
    if [ -z "$OUTPUT_FILE" ]; then
        echo "      ❌ no $SHIM_NAME.{xdl64,cdl64} found under $SDK_BUILD_DIR"
        echo "         Run 'build' first."; exit 1
    fi
    EXT="${OUTPUT_FILE##*.}"
    echo "      found: $OUTPUT_FILE"
    echo "[4/4] install -> $INSTALL_DIR/$SHIM_NAME.$EXT"
    mkdir -p "$INSTALL_DIR"
    if ! cp "$OUTPUT_FILE" "$INSTALL_DIR/" 2>/tmp/cp_err; then
        cp_err=$(cat /tmp/cp_err)
        if echo "$cp_err" | grep -qi "permission denied\|busy\|locked"; then
            echo "      ❌ install blocked — destination file is locked"
            echo "         CLOSE Cinema 4D fully, then re-run: $0 install"
            echo "         (Windows holds an exclusive lock on loaded .xdl64 plugins.)"
            exit 1
        fi
        echo "      ❌ cp failed: $cp_err"
        exit 1
    fi
    echo "      ✅ installed"
    echo ""
    echo "🔄 NEXT: fully restart Cinema 4D (Reload Python Plugins is NOT enough"
    echo "         for C++ plugins). Then run scene_nodes_helper_ping."
}

case "$cmd" in
    sync)        sync_to_sdk ;;
    build)       sync_to_sdk; build_via_cmake ;;
    install)     install_built_plugin ;;
    all|"")      sync_to_sdk; build_via_cmake; install_built_plugin ;;
    *)           echo "Usage: $0 {sync|build|install|all}"; exit 1 ;;
esac
