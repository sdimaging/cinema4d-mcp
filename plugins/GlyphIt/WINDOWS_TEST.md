# Glyph It — Windows test handoff

Build: GlyphIt 1.0.0, win-amd64 (bundled numpy 2.2.6 + Pillow 11.3.0 for Python 3.11). Tested on macOS (C4D 2026.4, Apple Silicon); never run on Windows yet. This zip is the first Windows run.

## Install (2 minutes)

1. Unzip. You get a folder `GlyphIt` with `GlyphIt.pyp` inside.
2. Move the whole `GlyphIt` folder into Cinema 4D's plugins folder:
   `C:\Users\<you>\AppData\Roaming\Maxon\Maxon Cinema 4D 2026_XXXXXXXX\plugins\`
   (Edit > Preferences > "Open Preferences Folder" shows it; make `plugins` if it doesn't exist.)
3. Start Cinema 4D. The Python console (Extensions > Console, Python tab) must say `Glyph It for Cinema 4D loaded`.

## Smoke test (1 minute)

- Extensions > GlyphIt > Glyph It. A Glyph It object appears; the Attribute Manager shows the tabs Object (Grid settings), Islands, Orientation, Distribution, Value, Color, Plate, Output, each with a "? HOW THIS TAB WORKS" foldout. The object icon is the green dot-grid G.
- Drop a Sphere under it. Glyphs appear on it.
- Plate tab: Plates Folder shows a path (if it's blank, that's a bug). Switch Plate Style a few times.
- Color tab: press **Standard** (and Redshift if installed). Set Color Mode to Random per Glyph and render (Render View). Expect per-glyph colours.
- Bake to Polygon Object on the Output tab.

## Full self test (2 minutes)

In the Python console paste (adjust the path to where you unzipped):

    exec(open(r"C:\path\to\GlyphIt\tools\c4d_selftest.py").read())

It builds a scene, runs 16 checks (build, description, params, all plate styles, 9 value sources,
colour modes, materials, image source, bend, animation, bake, save/reload, undo, cloth) and writes
`glyphit_selftest.txt` to the prefs folder. Send me that file plus anything the console printed.

Expected line 3 of the report: `numpy 2.2.6 ...\GlyphIt\libs\win-amd64\numpy\__init__.py` — that
proves the bundled Windows wheels load (if numpy/Pillow are already installed in your C4D Python,
it'll show those instead, which is fine too).

## What's most likely to break on Windows

- Bundled wheels not importing (DLL load error in the console on startup). The fallback only adds
  `libs\win-amd64` to `sys.path` when `import numpy` fails; if you see a "GlyphIt init" or import
  error, grab the console text.
- Font list / custom plates: Plate Style "8 Custom" uses the system font chooser and builds plates with
  Pillow. Try a custom glyph string with the default font and with one other font.
- Paths with backslashes in the Plates Folder field and in Image File (Value tab). Try an image.
- Open Plates Folder button (uses `os.startfile` on Windows).
- Octane / Redshift: only if installed there.

## Not in this zip

Your brands collection (plates/collections/brands) — copy it over separately if you want it on the PC.
