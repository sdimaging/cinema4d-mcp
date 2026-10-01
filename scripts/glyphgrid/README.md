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

## Controls: one tab each, with a built-in "? How this tab works" panel

Every tab in the generator starts with a collapsed **? How this tab works** group: plain-English help for that tab, right inside Cinema 4D. The same text is below.

Cells are numbered **top-left first, left to right, row by row**:

```
4-up:  1 2      9-up:  1 2 3      16-up:  1  2  3  4
       3 4             4 5 6              5  6  7  8
                       7 8 9              9 10 11 12
                                         13 14 15 16
```

### Grid
- **Grid (N x N)**: 1 = 1-up (one glyph everywhere), 2 = 4-up, 3 = 9-up, 4 = 16-up. It must match the plate (the Plate tab swaps the plate for you).
- **Seed**: re-deals which polygon gets which cell.
- **Cell Shift**: moves every polygon N cells forward. Animate it for a global glyph cycle.
- **Source Object**: optional. Drag any object here instead of putting it under GlyphGrid.

### Grid Mode: Mixed (1/4/9/16-up at once)
With **Grid Mode = Mixed**, every polygon draws from one of four levels, the 1-, 4-, 9- or 16-up plate, using exact proportions from **Level Weights** (`1,0,2,4` means no 4-up, and 16-up is 4× as common as 1-up). The plate becomes one composite texture: top-left 1-up, top-right 4-up, bottom-left 9-up, bottom-right 16-up. It's built automatically from the current style or collection into `plates/library/_mixed/`. Value modes still work inside each level.

### Islands
- **Island Mode**: Polygon = every polygon is one glyph. Ngon = ngons stay one glyph (default). Cluster = one big glyph across neighbouring polygons, sized by Cluster Size in scene units.
- **Quadtree (mixed glyph sizes)**: blocks of N×N polygons (**Quadtree Block**: 1/2/4/8/16/32/64) randomly split into halves, quarters and so on, down to single polygons. **Quadtree Levels** caps how many block sizes appear; **Subdivide Chance** runs from 0 (all big) to 1 (all single polygons).
  - Blocks come from the mesh **topology**: GlyphGrid walks the quad grid and gives every quad a (row, column), so blocks follow the mesh's own rows and columns whatever its position, rotation or curvature (planes, walls, cylinders, tori, sphere bands).
  - Big glyphs are laid out in grid coordinates, like a decal that bends with the surface, and aspect-corrected so they stay square and centred on non-square quads.
  - Triangles and ngons stay single glyphs. Combine with Mixed for big logos next to tiny ones.
- **Fit Auto**: square-ish quads fill the cell edge to edge. Triangles, ngons and long thin polygons are scaled uniformly and centred, with no stretching.
- **Flush Aspect Limit**: how stretched a quad can be and still fill the cell (1.35 = 35 % longer than wide).
- **Gutter**: empty border in every cell, which stops neighbouring glyphs bleeding in. **Glyph Scale** shrinks or grows every glyph.

### Orientation
- **Orient**: Up Axis = glyphs stand upright. Random 90 = each glyph turned 0/90/180/270. Random Free = any angle (glyphs shrink to fit). Edge Flow = follows each polygon's first edge.
- **Up Space**: World = "up" stays world-up while the object rotates. Object = "up" turns with the object.
- **Rotation Jitter**, **Random Mirror** (flips about half the glyphs), **Flip Mirror** (use if every glyph reads backwards).

### Distribution
| Mode | What happens |
|---|---|
| **Even Random** | Random, with exactly the same count in every cell. Value Source is ignored. |
| **Weighted Random** | Random, but cells share polygons according to **Weights** |
| **Value** | The Value tab picks the cell: low values go to the first cell (top-left), high values to the last (bottom-right). Use a sparse→dense plate. |
| **Value Equalized** | Same order as Value, but every cell gets the same count. Maximum contrast, less literal. |

**Weights** are *relative amounts* per cell, in plate order. On a 4-up, `8,1,1,1` means top-left 8 parts, top-right 1, bottom-left 1, bottom-right 1, which gives **73 % / 9 % / 9 % / 9 %** (verified: 1629 / 204 / 204 / 203 polygons). Cells without an entry count as 1, so `8` on a 16-up makes cell 1 eight times as common as each of the others. Weights are only used by Weighted Random.

### Value
- **Value Source**:
  - **Light / Lambert** = brightness from **Target Object** (any null).
    - **Directional Light** off = a bulb at the null's position.
    - **Directional Light** on = a sun along the null's −Z axis (rotate it; its position is ignored).
    - **Light Wrap** softens the shadow line.
  - **Field** = the Fields list.
  - **Height** = position along the Up Axis.
  - **Camera Facing** = surfaces facing the camera score high.
  - **Curvature** = convex scores high, concave low.
  - **Distance** = from the Target (**Distance Period** repeats it as rings).
- **Invert / Gamma / Contrast / Offset** reshape the value. **Wrap Values** with an animated **Offset** makes the glyphs ripple across the surface.
- **Random Mix**: 0 = pure value, 1 = pure random.
- **Dither**: jitters each value before it picks a cell, which breaks hard bands of one glyph into a smooth mix. Try 0.3–0.6.
- **Rebuild Every Frame**: turn on when lights, fields or Offset are animated.

### Plate (style × grid library, auto-swapped)
Library files are named `<style>_<cells>up.png` in `plates/library/`. **Plate Style picks the row and Grid picks the column**: 7 styles × 4 grids = 28 ready plates (see `plates/contact_sheet.png`). With **Auto Swap Plate** on, changing Grid or Style re-points the texture in this object's material. That works for Redshift texture nodes, Octane ImageTexture and C4D Bitmap shaders, but only for textures that already point at a plate file (or the RS node named `gg_plate`).

| # | Style | Notes |
|---|---|---|
| 1 | ASCII ramp | characters sorted sparse → dense |
| 2 | Bayer dither | 4×4 Bayer: 16-up = 16 exact levels |
| 3 | Halftone dots | round, area-correct |
| 4 | Halftone squares | |
| 5 | Noise | stochastic dither levels |
| 6 | Hex digits | 0–F |
| 7 | Binary 0/1 | |
| 8 | **Custom** | type your own characters |
| 9 | **Collection** | your own folder of images, see `plates/collections/README.md` |

**Custom glyphs:** pick style 8 and type characters into **Custom Glyphs**, e.g. `SDIMAGING`, `0123456789` or `.:-=+*#%@`.
- **Sort by Ink** orders them sparse → dense, so they shade correctly in the Value modes.
- **Font** is a PostScript name (`Menlo-Bold`, `Courier`, `HelveticaNeue-Bold`, `SFMono-Heavy`).
- The plate is drawn inside Cinema 4D (GeClipMap, no extra installs) and saved as `custom_<cells>up_<hash>.png` in the Plate Folder.
- **Apply Plate Now** forces the swap.
- Custom plates use Pillow (FreeType) when it's importable in C4D's Python, and give crisp glyphs. Install it the same way as numpy, with `pillow==11.3.0`. Without Pillow, GeClipMap is used, which is softer because GeClipMap clips text taller than ~126 px, so glyphs get upscaled.

**Collections:** a folder of glyph images under `plates/collections/<name>/`, named `01_x.png`, `02_y.png`… in priority order. 1-up uses #1, 4-up #1–4, 9-up #1–9, 16-up #1–16; fewer images repeat. Hand-made `name_4up.png` plates are used as-is. A folder holding only one big plate is sliced into its cells. `shapes/` is a 16-glyph example.

### Output
- **UV Mode**: Atlas is the normal mode and works in every renderer. Encoded is experimental and only for `glyphgrid_atlas.osl`.
- **Write ID Tag**: an extra UV tag (value, random) per polygon, for custom shaders.
- **Cell Selection Tags**: one polygon selection per cell (`GG_cell_00`…), so each glyph cell can get its own material.
- **Keep Source UVs**: keep the object's original UV tags as well.
- **Merge Objects**: several objects or clones become one mesh with one global even split.

**The ASCII-shading recipe:** Plate Style 1 (ASCII), Value Source = Light / Lambert, Target = a null, Distribute = Value, Dither ≈ 0.35. Move the null and the glyph density follows the light.

## Plates

The cell order matches the solver: cell 0 is top-left, then left→right, top→bottom.

```bash
python glyphgrid_plates.py ascii    --grid 4 --out plates/ascii16.png         # ink-sorted ramp
python glyphgrid_plates.py chars    --grid 4 --text "SDIMAGING0123456" --out brand16.png
python glyphgrid_plates.py bayer    --grid 4 --dots 8 --out bayer16.png       # 4x4 Bayer = 16 exact levels
python glyphgrid_plates.py halftone --grid 3 --shape round --out dots9.png
python glyphgrid_plates.py logo     --grid 2 --images a.png b.png c.png d.png --out logos4.png
python glyphgrid_plates.py ascii    --grid 4 --frames 12 --shimmer --flipbook --out seq/ascii16
python glyphgrid_plates.py library  --out plates/library               # all styles x 1/4/9/16-up
python glyphgrid_plates.py sheet    --out contact.png                 # style x grid matrix
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

- Plate auto-swap runs from the generator's `message()` on real Attribute Manager edits. Scripts should call `glyphgrid_runtime.plate_path()` + `swap_plate()` directly: messaging the generator from inside another Python call deadlocks C4D (gotcha #127).

- After editing the child, the generator can show the previous result for one redraw. It updates on the next pass. Render documents are correct on the first pass.
- Linked Target objects and fields are tracked by their dirty state. Keyframed changes need **Rebuild Every Frame**.
- Back faces read mirrored when seen through a hologram material. That is expected; flip it per side with two materials if needed.

See gotchas #117–#126 at the end of `docs/c4d_2026_api_gotchas.md` for the API lessons behind this tool.
