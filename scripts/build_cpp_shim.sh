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

install_built_plugin() {
    if [ -z "$C4D_DIR" ]; then
        echo "❌ Could not locate Maxon Cinema 4D 2026 install dir under $APPDATA"
        exit 1
    fi
    # The C4D 2026 SDK on Windows produces .xdl64, not .cdl64 (verified
    # 2026-04-30 from the user's first successful build). Find both.
    # Recursive search anywhere under _build_v143 or under plugin's own
    # _build_v143 subdir — the SDK output path varies by build config.
    OUTPUT_FILE=""
    for ext in xdl64 cdl64; do
        # Use find with -type f to traverse the unknown subdir structure
        candidate=$(find "$SDK/_build_v143" -type f -name "$SHIM_NAME.$ext" 2>/dev/null \
                    | grep -i "release" | head -1)
        if [ -z "$candidate" ]; then
            # fallback: any subdir named release/Release
            candidate=$(find "$SDK/_build_v143" -type f -name "$SHIM_NAME.$ext" 2>/dev/null | head -1)
        fi
        if [ -n "$candidate" ]; then
            OUTPUT_FILE="$candidate"
            break
        fi
    done
    if [ -z "$OUTPUT_FILE" ]; then
        echo "❌ no $SHIM_NAME.{xdl64,cdl64} output found under $SDK/_build_v143"
        echo "   Build via Visual Studio (or cmake --build) first, then re-run 'install'."
        echo "   (Note: Windows builds produce .xdl64; .cdl64 is macOS/Linux convention.)"
        exit 1
    fi
    mkdir -p "$INSTALL_DIR"
    cp -v "$OUTPUT_FILE" "$INSTALL_DIR/"
    EXT="${OUTPUT_FILE##*.}"
    echo ""
    echo "✅ Installed: $INSTALL_DIR/$SHIM_NAME.$EXT"
    echo "   Now FULLY RESTART Cinema 4D (Reload Python Plugins is NOT enough)."
    echo "   Verify via:"
    echo "      c4d.plugins.FindPlugin(1057845, c4d.PLUGINTYPE_COREMESSAGE)"
    echo "   or via the MCP tool: scene_nodes_helper_ping"
}

case "$cmd" in
    sync)    sync_to_sdk ;;
    project) sync_to_sdk; regen_project ;;
    install) install_built_plugin ;;
    all)     sync_to_sdk; regen_project
             echo ""
             echo "Next steps (verified working 2026-04-30):"
             echo "  1. Open the C4D 2026 SDK Visual Studio solution OR run cmake:"
             echo "       cd \"\$SDK/_build_v143\""
             echo "       cmake --build . --config Release --target $SHIM_NAME"
             echo "  2. Run: $0 install"
             echo "  3. FULLY RESTART Cinema 4D (Reload Python Plugins is not enough)"
             echo "  4. Run scene_nodes_helper_ping (MCP tool) — expect helper_loaded=True" ;;
    *)       echo "Usage: $0 {sync|project|install|all}"; exit 1 ;;
esac
