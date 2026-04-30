#!/bin/bash
# Sync the in-repo plugin file to C4D's installed plugins directory.
# Run after any edit to c4d_plugin/mcp_server_plugin.pyp so that
# Extensions → Reload Python Plugins picks up your changes.
#
# Usage:  ./scripts/sync_to_installed.sh

set -e

SRC_REL="c4d_plugin/mcp_server_plugin.pyp"
SRC="$(cd "$(dirname "$0")/.." && pwd)/$SRC_REL"

if [ ! -f "$SRC" ]; then
    echo "❌ Source not found: $SRC"
    exit 1
fi

# Find C4D 2026 plugins dir (Windows AppData via WSL)
APPDATA="/mnt/c/Users/Spenser Dickerson/AppData/Roaming/Maxon"
DST_DIR=$(find "$APPDATA" -maxdepth 1 -type d -name "Maxon Cinema 4D 2026*" 2>/dev/null | head -1)

if [ -z "$DST_DIR" ]; then
    echo "❌ Could not locate Maxon Cinema 4D 2026 directory under $APPDATA"
    exit 1
fi

DST="$DST_DIR/plugins/mcp_server_plugin.pyp"

echo "src: $SRC"
echo "dst: $DST"
echo ""

if [ -f "$DST" ]; then
    echo "  current installed size: $(stat -c %s "$DST") bytes / $(date -r "$DST" '+%Y-%m-%d %H:%M:%S')"
fi

cp "$SRC" "$DST"

echo "  new installed size:     $(stat -c %s "$DST") bytes / $(date -r "$DST" '+%Y-%m-%d %H:%M:%S')"

# --- Phase C atlas: also sync scene_nodes_patterns.py + data/*.json -------
PLUGINS_DIR="$DST_DIR/plugins"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PATTERNS_SRC="$REPO_ROOT/c4d_plugin/scene_nodes_patterns.py"
DATA_SRC_DIR="$REPO_ROOT/data"

if [ -f "$PATTERNS_SRC" ]; then
    cp "$PATTERNS_SRC" "$PLUGINS_DIR/scene_nodes_patterns.py"
    echo "  also synced: scene_nodes_patterns.py"
fi

if [ -d "$DATA_SRC_DIR" ]; then
    mkdir -p "$PLUGINS_DIR/data"
    cp "$DATA_SRC_DIR"/*.json "$PLUGINS_DIR/data/" 2>/dev/null || true
    echo "  also synced: data/*.json -> plugins/data/"
fi

echo ""
echo "✅ Synced. Now: Extensions → Reload Python Plugins in C4D."
