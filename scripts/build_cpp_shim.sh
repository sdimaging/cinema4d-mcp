#!/bin/bash
# build_cpp_shim.sh — sync the cpp_shim source into the C4D 2026 SDK plugins
# tree, regenerate the Visual Studio solution via Maxon's ProjectTool, and
# (after manual VS build) copy the resulting .cdl64 into the active C4D
# install's plugins directory.
#
# Usage:
#   ./scripts/build_cpp_shim.sh sync       # sync source -> SDK only (run before VS build)
#   ./scripts/build_cpp_shim.sh project    # sync + regenerate VS solution
#   ./scripts/build_cpp_shim.sh install    # copy built .cdl64 to C4D install (run after VS build)
#   ./scripts/build_cpp_shim.sh all        # sync + project (then user opens VS + builds + reruns "install")
#
# Phase A.0 (2026-04-30): the actual C++ compile is currently a manual VS step.
# Future revision can chain MSBuild from the command line if it pays off.

set -e

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SHIM_NAME="cinema4d_mcp_helper"
SHIM_SRC="$REPO/cpp_shim/$SHIM_NAME"

# C4D 2026 SDK location — adjust if SDK lives elsewhere
SDK="/mnt/c/Users/Spenser Dickerson/Documents/C4D_2026_SDK"

# Active C4D install's plugins directory (where .cdl64 needs to land)
APPDATA="/mnt/c/Users/Spenser Dickerson/AppData/Roaming/Maxon"
C4D_DIR=$(find "$APPDATA" -maxdepth 1 -type d -name "Maxon Cinema 4D 2026*" 2>/dev/null | head -1)
INSTALL_DIR="$C4D_DIR/plugins/cinema4d-mcp"

cmd="${1:-all}"

sync_to_sdk() {
    if [ ! -d "$SHIM_SRC" ]; then
        echo "❌ shim source not found at $SHIM_SRC"
        exit 1
    fi
    if [ ! -d "$SDK/plugins" ]; then
        echo "❌ SDK plugins dir not found at $SDK/plugins"
        echo "   adjust SDK= in this script if it lives elsewhere"
        exit 1
    fi
    DST="$SDK/plugins/$SHIM_NAME"
    echo "syncing: $SHIM_SRC"
    echo "    to:  $DST"
    mkdir -p "$DST"
    rsync -a --delete "$SHIM_SRC/" "$DST/"
    echo "✅ source synced ($(find "$DST" -type f | wc -l) files)"
}

regen_project() {
    PROJECTTOOL_DIR="$SDK/tools"
    if [ ! -d "$PROJECTTOOL_DIR" ]; then
        echo "⚠️  Maxon ProjectTool dir not found at $PROJECTTOOL_DIR"
        echo "   Manual step: open the C4D 2026 SDK Visual Studio solution and"
        echo "   regenerate via the SDK's normal tooling. Reference how Luminary,"
        echo "   MechFlow, Spikr2 are built in that SDK."
        return 0
    fi
    echo "(if this repo gets a ProjectTool wrapper, plug it in here)"
}

install_built_cdl64() {
    if [ -z "$C4D_DIR" ]; then
        echo "❌ Could not locate Maxon Cinema 4D 2026 install dir under $APPDATA"
        exit 1
    fi
    # Common Maxon SDK output paths — try each
    BUILD_OUTPUTS=(
        "$SDK/_build_v143/Win64/Release"
        "$SDK/_build_v143/Win64/Debug"
        "$SDK/plugins/$SHIM_NAME/_build_v143/Win64/Release"
    )
    OUTPUT_FILE=""
    for d in "${BUILD_OUTPUTS[@]}"; do
        candidate="$d/$SHIM_NAME.cdl64"
        if [ -f "$candidate" ]; then
            OUTPUT_FILE="$candidate"
            break
        fi
    done
    if [ -z "$OUTPUT_FILE" ]; then
        echo "❌ no .cdl64 output found. Checked:"
        for d in "${BUILD_OUTPUTS[@]}"; do echo "   $d/$SHIM_NAME.cdl64"; done
        echo "   Build via Visual Studio first, then re-run 'install'."
        exit 1
    fi
    mkdir -p "$INSTALL_DIR"
    cp -v "$OUTPUT_FILE" "$INSTALL_DIR/"
    echo "✅ installed. Restart C4D (or Stop+Start the MCP socket-server plugin"
    echo "   then C4D restart for full reload) and verify via:"
    echo "      c4d.plugins.FindPlugin(1057845, c4d.PLUGINTYPE_MESSAGEDATA)"
}

case "$cmd" in
    sync)    sync_to_sdk ;;
    project) sync_to_sdk; regen_project ;;
    install) install_built_cdl64 ;;
    all)     sync_to_sdk; regen_project
             echo ""
             echo "Next steps:"
             echo "  1. Open the C4D 2026 SDK Visual Studio solution"
             echo "  2. Build the '$SHIM_NAME' project (x64 Release)"
             echo "  3. Run: $0 install" ;;
    *)       echo "Usage: $0 {sync|project|install|all}"; exit 1 ;;
esac
