#!/usr/bin/env python3
"""Build GlyphIt-<version>.zip (plugin folder with bundled libs, without caches / git files)."""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from glyphit import __version__  # noqa: E402

SKIP_DIRS = {"__pycache__", "_custom", "_mixed", "_built", ".git", "tests", "testing", "include", "f2py"}
SKIP_FILES = {".DS_Store"}


def main():
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import fetch_libs
    for t in fetch_libs.PLATFORMS:
        if not os.path.isdir(os.path.join(HERE, "libs", t)):
            fetch_libs.fetch(t)
    only = [a for a in sys.argv[1:] if a in fetch_libs.PLATFORMS]
    targets = [None] + list(fetch_libs.PLATFORMS) if not only else only
    for plat in targets:        # None = universal zip (all platforms), else one zip per platform
        tag = "" if plat is None else "-" + plat
        out = os.path.join(os.path.dirname(HERE), "GlyphIt-%s%s.zip" % (__version__, tag))
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(HERE):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                rel_root = os.path.relpath(root, HERE)
                if plat is not None and rel_root.startswith("libs" + os.sep):
                    if not rel_root.startswith(os.path.join("libs", plat)):
                        dirs[:] = []
                        continue
                for f in files:
                    if f in SKIP_FILES or f.endswith((".pyc", ".zip", ".whl")):  # .whl: PyPI numpy win wheel carries an empty self-named file
                        continue
                    full = os.path.join(root, f)
                    z.write(full, os.path.join("GlyphIt", os.path.relpath(full, HERE)))
        print("wrote", out, round(os.path.getsize(out) / 1e6, 1), "MB")


if __name__ == "__main__":
    main()
