#!/usr/bin/env python3
"""
Download numpy + Pillow wheels for Cinema 4D's Python (3.11, 2024-2026) into libs/<platform>/ so
the plugin runs on machines without them. Run from any Python 3 with pip:

    python3 tools/fetch_libs.py            # all platforms
    python3 tools/fetch_libs.py macos-arm64

libs/ is git-ignored; tools/build_release.py runs this and zips everything.
"""
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = "3.11"
PKGS = ["numpy==2.2.6", "pillow==11.3.0"]
PLATFORMS = {
    "macos-arm64": ["macosx_14_0_arm64", "macosx_12_0_arm64", "macosx_11_0_arm64"],
    "macos-x86_64": ["macosx_10_13_x86_64", "macosx_10_9_x86_64"],
    "win-amd64": ["win_amd64"],
}


def fetch(tag):
    out = os.path.join(HERE, "libs", tag)
    os.makedirs(out, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
               "--python-version", PY, "--implementation", "cp", "--abi", "cp311", "-d", tmp]
        for p in PLATFORMS[tag]:
            cmd += ["--platform", p]
        cmd += PKGS
        print(" ".join(cmd))
        subprocess.check_call(cmd)
        for f in sorted(os.listdir(tmp)):
            if f.endswith(".whl"):
                print("unpack", f, "->", out)
                with zipfile.ZipFile(os.path.join(tmp, f)) as z:
                    z.extractall(out)
    return out


if __name__ == "__main__":
    tags = sys.argv[1:] or list(PLATFORMS)
    for t in tags:
        fetch(t)
    print("done")
