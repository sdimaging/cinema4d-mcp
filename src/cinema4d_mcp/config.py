"""Configuration handling for Cinema 4D MCP Server."""

import os
import sys


def _is_wsl() -> bool:
    """Detect if we're running inside WSL2 (Windows Subsystem for Linux)."""
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", "r") as f:
            return "microsoft" in f.read().lower()
    except Exception:
        return False


def _detect_wsl_windows_host() -> str:
    """Resolve the Windows host IP from inside WSL by reading the default gateway.
    Returns the IP as a string, or '127.0.0.1' if detection fails."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["ip", "route", "show", "default"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        )
        # "default via <IP> dev <iface> ..."
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
                return parts[2]
    except Exception:
        pass
    return "127.0.0.1"


def _resolve_c4d_host() -> str:
    """Resolve C4D_HOST honoring env var override + WSL auto-detection.

    Priority:
      1. If C4D_HOST env var is set explicitly to anything OTHER than the default
         placeholders ('127.0.0.1', 'localhost'), use it as-is.
      2. Otherwise, if running inside WSL2, auto-detect the Windows host IP via
         the default gateway (since 127.0.0.1 inside WSL2 is a separate loopback
         from Windows' 127.0.0.1). The C4D plugin must bind to 0.0.0.0.
      3. Otherwise, fall back to 127.0.0.1.
    """
    env = os.environ.get("C4D_HOST")
    if env and env not in ("127.0.0.1", "localhost"):
        return env
    if _is_wsl():
        return _detect_wsl_windows_host()
    return "127.0.0.1"


C4D_HOST = _resolve_c4d_host()
C4D_PORT = int(os.environ.get("C4D_PORT", 5555))