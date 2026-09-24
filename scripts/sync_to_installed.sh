#!/usr/bin/env bash
# WSL wrapper. Explicit target only; never choose the first Maxon profile.
# Usage: ./scripts/sync_to_installed.sh '/mnt/c/.../plugins' [-Restart|-Start]
set -euo pipefail
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <exact C4D plugins directory> [-Restart|-Start]" >&2
    exit 2
fi
target=$1
shift
script_dir="$(cd "$(dirname "$0")" && pwd)"
exec powershell.exe -NoProfile -File "$(wslpath -w "$script_dir/sync_to_installed.ps1")" -PluginsDirectory "$(wslpath -w "$target")" "$@"
