# GlyphGrid: every polygon is a UV island in an N×N atlas

GlyphGrid turns any Cinema 4D object into a generative ASCII, dither or glyph surface, driven entirely from UV space.

Every polygon (or ngon, or cluster of polygons) gets its own UV island. Each island is fitted into one cell of a 1/4/9/16-up atlas. You can then texture the object with any N×N **plate**: ASCII characters, Bayer dither levels, halftone dots, logos, numbers, or a looping image sequence of plates. The result is a 3D glyph builder.

```
scripts/glyphgrid/
  glyphgrid_core.py      pure-python solver (no c4d), optional numpy fast path
  glyphgrid_runtime.py   c4d side: read mesh, value sources, raw UVW write, tags
  glyphgrid_c4d.py       builder: live Python Generator, bake, Redshift plate material
  glyphgrid_plates.py    plate / sequence / flipbook generator (PIL)
  glyphgrid_atlas.osl    shader-side decoder for "Encoded" UV mode (live grid, time offsets)
  plates/                example plates (1024 px) + a 12-frame shimmer sequence and flipbook
```

## Quick start (Script Manager or MCP exec_python)

```python
import sys; sys.path.insert(0, "<repo>/scripts/glyphgrid")
import glyphgrid_c4d as G
gen = G.build_generator(doc, source=op)                      # op = any object; it becomes the child
G.build_plate_material(doc, gen, G.HERE + "/plates/ascii_16up.png", mode="hologram",
                       color=(0.15, 1.0, 0.35), emission=2.0)
```

You can also drop any object under the **GlyphGrid** generator, or link it in **Source Object**. The generator embeds its own code, so a saved `.c4d` works without this repo. The plate image path is still absolute.

For a one-shot bake onto an editable mesh, without a generator:

```python
G.bake_object(doc, poly_obj, grid=4, seed=7, orient=0)
```

## Controls (generator user data)

| Group | Parameter | What it does |
|---|---|---|
| Grid | **Grid (N x N)** | 1 = 1up (all stacked), 2 = 4up, 3 = 9up, 4 = 16up … up to 8 |
| | Seed | Re-deals every polygon |
| | Cell Shift | Integer. Animate it so every polygon steps to the next cell (global glyph cycle) |
| | Source Object | Optional link that is used instead of the first child |
| Islands & Fit | Island Mode | Polygon / **Ngon** (keeps ngons whole) / Cluster (one glyph spans many polygons) |
| | Cluster Size | World size of a cluster. Gives big letters across many quads |
| | Fit | **Auto**: near-square quads snap flush to the cell corners; triangles, ngons and long thin polygons are kept **uniform and centred**. Also Uniform-all or Flush-all |
| | Flush Aspect Limit | Quads up to this aspect ratio count as square |
| | Gutter / Glyph Scale | Empty border per cell (stops mip bleed) and glyph size |
| Orientation | Orient | **Up Axis** (glyphs read upright on the model), Random 90°, Random Free, Edge Flow |
| | Up Axis / Up Space | +Y +Z +X −Y, world or object |
| | Rotation Jitter, Random Mirror, Flip Mirror | Variation and a handedness fix |
| Distribution | Distribute | **Even Random**: shuffled, so counts differ by at most 1 (2M polygons at 16up gives 125,000 per cell). Weighted Random takes `Weights` (e.g. `8,1,1,1`). **Value** picks the cell from a value. **Value Equalized** picks by value order but keeps exact even counts |
| | Value Source | Random, Vertex Map, **Field**, Height, **Light / Lambert**, Camera Facing, Curvature, Polygon Index, Distance |
| | Fields / Target Object | Field list. Light, camera or distance origin |
| | Invert, Gamma, Contrast, Offset, Wrap Values | Value remap. Animate **Offset** with **Wrap** on to make glyphs cycle across the surface |
| | Random Mix, Dither | Blend toward random. Noise before quantising, which gives smooth tonal ramps |
| | Rebuild Every Frame | Turn on for animated offsets, keyframed lights and time-based fields |
| Output | UV Mode | **Atlas** (grid baked into UVs, works in every renderer) or **Encoded** (for `glyphgrid_atlas.osl`) |
| | Write ID Tag | Adds a 2nd UVW tag `GlyphGrid ID` holding (value, random) per polygon, for custom shaders |
| | Cell Selection Tags | One polygon selection per cell (`GG_cell_00` …), for multi-material looks |
| | Merge Objects | One mesh means one global distribution (Cloners, hierarchies) |

**The ASCII-shading trick:** use a plate sorted sparse→dense (`glyphgrid_plates.py ascii`), set Value Source = Light and Distribute = Value, and add a little Dither. Glyph density then follows the lighting, so you get ASCII shading in 3D straight out of the render.

## Plates

The cell order matches the solver: cell 0 is top-left, then left→right, top→bottom.

```bash
python glyphgrid_plates.py ascii    --grid 4 --out plates/ascii16.png         # ink-sorted ramp
python glyphgrid_plates.py chars    --grid 4 --text "SDIMAGING0123456" --out brand16.png
python glyphgrid_plates.py bayer    --grid 4 --dots 8 --out bayer16.png       # 4x4 Bayer = 16 exact levels
python glyphgrid_plates.py halftone --grid 3 --shape round --out dots9.png
python glyphgrid_plates.py logo     --grid 2 --images a.png b.png c.png d.png --out logos4.png
python glyphgrid_plates.py ascii    --grid 4 --frames 12 --shimmer --flipbook --out seq/ascii16
python glyphgrid_plates.py sheet    --out contact.png
```

`--shimmer` re-picks every cell, every frame, from characters of **similar ink density**. A value-driven layout keeps its tone while the glyphs boil. `--flipbook` packs all frames into one atlas for the OSL shader.

## Looks

`build_plate_material(doc, obj, plate, mode=...)` builds a Redshift material in one of three modes:

- **`hologram`**: the plate drives **opacity**, so black is knocked out. Emission and base colour are a constant (green by default). You get see-through shapes built from characters, and the back faces show through.
- **`emissive`**: glyphs glow on a black body.
- **`diffuse`**: the plate drives base colour.

To change the plate later, call `set_plate(mat, path)`. For an animated sequence, point the texture at `plates/seq/ascii_16up_0000.png` and enable the sequence in the texture node.

## Encoded mode + OSL (live grid, per-polygon time offsets)

With UV Mode = Encoded, each polygon's UV is `(valueLevel + s, randomLevel + t)`. The integer part carries data; the fractional part is the polygon's own 0–1 glyph frame. `glyphgrid_atlas.osl` decodes this and picks the cell and the flipbook frame in the shader. Grid size, value remap, cell shift and **per-polygon time offset** (each polygon starts the loop at a different frame) can then all be changed live, without rebuilding UVs.

- **Octane:** use an OSL Texture node with Projection = Mesh UV. Paste the shader and set Atlas to a flipbook such as `plates/seq/ascii_16up_flipbook_4x3.png`, with Frames 12 and Flipbook Columns 4. Animate **Frame**.
- **Redshift and other renderers:** set `UseUVGlobals = 1`. Toggle `FlipV` / `FlipT` if a renderer's UV or image origin differs.

The decode logic is verified by a numpy port: frame 0 with Encoded+OSL matches the baked Atlas pixel for pixel. Octane compilation has not been tested from Python, because the OSL compile button needs the Octane UI.

## Performance (M-series Mac, C4D 2026.4)

| Mesh | Pure Python | With numpy |
|---|---|---|
| 500k polygons, full rebuild | ~3.4 s | ~1.9 s |
| No-change redraw | 0 ms (COW clone of the kept result) | 0 ms |

Rough scaling is ~7 µs per polygon in pure Python, so 2M polygons take ~14 s per rebuild. A rebuild only happens when the source or a parameter changes.

To install numpy for C4D's Python 3.11 (the solver picks it up automatically):

```bash
python3 -m pip install --target ~/Library/Preferences/Maxon/python/python311/libs \
  --python-version 3.11 --platform macosx_14_0_arm64 --implementation cp --only-binary=:all: numpy==2.2.6
```

## Known limits

- After editing the child, the generator can show the previous result for one redraw. It updates on the next pass. Render documents are correct on the first pass.
- Linked Target objects and fields are tracked by their dirty state. Keyframed changes need **Rebuild Every Frame**.
- Back faces read mirrored when seen through a hologram material. That is expected; flip it per side with two materials if needed.

See gotchas #117–#126 at the end of `docs/c4d_2026_api_gotchas.md` for the API lessons behind this tool.
