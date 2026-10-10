# Glyph It for Cinema 4D

**Every polygon becomes a glyph.** Glyph It turns any Cinema 4D object into a generative ASCII, dither, halftone, logo or custom-type surface, driven entirely from UV space, in every renderer.

Every polygon (or ngon, cluster, or quadtree block) gets its own UV island, fitted into one cell of a 1/4/9/16-up atlas. Texture the object with any N×N **plate** (ASCII characters, Bayer levels, halftone dots, your logos, your own letters) and you have a 3D glyph builder. Glyphs stay locked to their polygons through deformers and cloth, and a picture or movie can drive which glyph shows and in which colour.

Cinema 4D 2024–2026 (Python 3.11), macOS (Apple Silicon and Intel) and Windows. Redshift, Octane and Standard materials are built in.

## Install

1. Unzip `GlyphIt-<version>.zip`.
2. Put the `GlyphIt` folder into your Cinema 4D plugins folder:
   - macOS: `~/Library/Preferences/Maxon/Maxon Cinema 4D <version>/plugins/`
   - Windows: `%APPDATA%\Maxon\Maxon Cinema 4D <version>\plugins\`
   (or any folder listed under Preferences > Plugins).
3. Restart Cinema 4D. **Glyph It** appears in the Create > Generators menu.

numpy and Pillow are bundled (`libs/`), so nothing else is needed. If your Cinema 4D already has them installed, those are used.

## Quick start

1. Create **Glyph It** and drop any object under it (or link one in **Source Object**).
2. Go to the **Color** tab and press **Redshift**, **Octane** or **Standard**. That builds a glyph material for the renderer and assigns it: dark body, the plate knocks out everything around the glyphs, emission in the colour you pick.
3. Grid tab: **Grid (N x N)** sets 1/4/9/16-up. Plate tab: **Plate Style** picks the look. The material updates by itself.

Then try: **Value Source = Light / Lambert** with a null as **Target Object** (ASCII shading), **Island Mode = Quadtree** (mixed glyph sizes), **Color Mode = Random per Glyph**, or **Value Source = Image / Movie** on a plane (a picture or film turned into text).

```
GlyphIt/
  GlyphIt.pyp            plugin: the Glyph It object + "Convert Glyph It scenes to Glyph It"
  glyphit/
    params.py            the parameter table (fixed ids, help text) - the single source of truth
    core.py              pure-python solver (no c4d), optional numpy fast path
    runtime.py           c4d side: mesh read, value sources, images, tags, plates, materials
    generator.py         parameters -> output object (caching, lock, deformers)
    plates.py            plate / sequence / collection builder (Pillow)
    convert.py           Glyph It (Python Generator) -> Glyph It converter
    api.py               scripting API
  plates/                library plates (7 styles x 1/4/9/16-up) + collections/
  res/                   description stub, icon
  libs/                  bundled numpy + Pillow per platform (tools/fetch_libs.py)
  tools/                 fetch_libs.py, build_release.py, make_icon.py
```

## Scripting

```python
import sys; sys.path.insert(0, "<plugins>/GlyphIt")
from glyphit import api
obj = api.create(doc, source=op, **{"Grid (N x N)": 4, "Plate Style": 0})   # op becomes the child
api.material(doc, obj, "redshift")                                            # = the Redshift button
api.set_params(obj, Seed=7, **{"Color Mode": 2, "Island Mode": 3})
api.bake(doc, obj)                                                            # editable copies
api.convert_scene(doc)                                                        # legacy Glyph It -> Glyph It
```

Parameter names are the Attribute Manager labels. Keyframes work on every slider, dropdown, checkbox and colour.

## Coming from Glyph It (the Python Generator prototype)

Scenes built with the Glyph It Python Generator keep working as they are (the generator carries its own code). To move them onto the plugin, run **Extensions > Convert Glyph It scenes to Glyph It**: every Glyph It becomes a Glyph It object with the same name, children, tags, settings and keyframes, and Redshift materials are re-pointed to the new colour tag name. Materials, plates and demo scenes carry straight over.

## Controls: one tab each, with a built-in "? How this tab works" panel

Every tab starts with a collapsed **? How this tab works** group: plain-English help for that tab, right inside Cinema 4D. The same text is below.

Cells are numbered **top-left first, left to right, row by row**:

```
4-up:  1 2      9-up:  1 2 3      16-up:  1  2  3  4
       3 4             4 5 6              5  6  7  8
                       7 8 9              9 10 11 12
                                         13 14 15 16
```

### Grid
- **Grid (N x N)**: 1 = 1-up (one glyph everywhere), 2 = 4-up, 3 = 9-up, 4 = 16-up. It must match the plate (the Plate tab swaps the plate for you). Range 1–4. Collections or Custom Glyphs with fewer glyphs than cells repeat.
- **Seed**: re-deals which polygon gets which cell.
- **Glyph Offset**: every polygon steps N places forward in the plate's reading order and wraps around (4-up A B / C D at offset 1: A→B, B→C, C→D, D→A). Layout and counts stay the same. Keyframe 0, 1, 2, 3… and all glyphs tick in lockstep like a split-flap board.
- **Source Object**: optional. Drag any object here instead of putting it under Glyph It.

### Grid Mode: Mixed (1/4/9/16-up at once)
With **Grid Mode = Mixed**, every polygon draws from one of four levels, the 1-, 4-, 9- or 16-up plate, using exact proportions from **Level Weights** (`1,0,2,4` means no 4-up, and 16-up is 4× as common as 1-up). The plate becomes one composite texture: top-left 1-up, top-right 4-up, bottom-left 9-up, bottom-right 16-up. It's built automatically from the current style or collection into `plates/library/_mixed/`. Value modes still work inside each level.

### Islands
- **Island Mode**: Polygon = every polygon is one glyph. Ngon = ngons stay one glyph (default). Cluster = one big glyph across neighbouring polygons, sized by Cluster Size in scene units.
- **Quadtree (mixed glyph sizes)**: blocks of N×N polygons (**Quadtree Block**: 1/2/4/8/16/32/64) randomly split into halves, quarters and so on, down to single polygons. **Quadtree Levels** caps how many block sizes appear (1–4 sizes: Block, /2, /4, /8). Levels and Block are linked so every level is real: raising Levels grows the Block to fit (Levels 4 → 8 polys), lowering the Block pulls Levels down; **Subdivide Chance** runs from 0 (all big) to 1 (all single polygons).
  - Blocks come from the mesh **topology**: Glyph It walks the quad grid and gives every quad a (row, column), so blocks follow the mesh's own rows and columns whatever its position, rotation or curvature (planes, walls, cylinders, tori, sphere bands).
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
| **Sequential** | No randomness: polygon 1 gets cell 1, polygon 2 cell 2 ... wrapping round. With Custom Glyphs `DUDE` on a 4-up plate (Sort by Ink **off**) the mesh reads D U D E D U D E along its polygon order. Keyframe **Glyph Offset** to scroll the text. |

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
  - **Image / Movie** = a picture read through the source object's **own UVs**, so a plane shows it 1:1 (a ticking clock, footage, a logo). Bright areas get the last (densest) plate cell and dark areas the first, so an ink-sorted ASCII plate turns any image or film into text. Animated sources update every frame on their own; you don't need Rebuild Every Frame.
    - **Image File**: a still, a numbered sequence (pick any frame such as `clock_0001.png`, or write `clock_####.png`), a GIF, or a movie (mp4/mov through C4D's movie loader; image sequences are faster and safer). A name ending in one digit, like `logo_v2.png`, counts as a still.
    - **Image Shader**: any C4D shader instead of the file (a Bitmap shader with its own movie timing, Noise, Gradient, Layer…). When set, it wins over the file.
    - **Image Sampling**: Area averages every pixel under each glyph (calm, best for footage); Centre takes one pixel (crisper, can shimmer).
    - **Image Speed / Image Frame Offset / Image Loop**: sequence/GIF/movie playback against the timeline (0.5 = half speed, Loop off holds the last frame).
    - Distribute = Even switches to literal **Value** for pictures (equalising would push a white clock face into every glyph). Invert, Gamma, Contrast, Knockout and Dither all apply.
    - An object without UVs falls back to a planar projection over its largest side.
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

**Plate tab layout:**
- **Plates Folder** `[path] [browse] [Open Plates Folder]`: the plates root that holds `library/` and `collections/`. If you browse to a subfolder by mistake, Glyph It walks up to the root.
- **Collection** `[dropdown] [Open Collection] [Refresh List] [New Collection]`: picking one switches Plate Style to *9 Collection* and swaps the texture.
  - **Open Collection** shows the selected collection in Finder.
  - **New Collection** creates `my_collection_N` with a how-to and opens it.
  - **Refresh List** re-reads the folders.
- **Custom Glyphs** + **Font**: Cinema 4D's own font picker (family + style, any installed font) + **Sort by Ink**.
- **Auto Swap Plate** `[Apply Plate Now]`.

**Collections:** a folder of glyph images under `plates/collections/<name>/`, named `01_x.png`, `02_y.png`… in priority order. 1-up uses #1, 4-up #1–4, 9-up #1–9, 16-up #1–16; fewer images repeat. Hand-made `name_4up.png` plates are used as-is. A folder holding only one big plate is sliced into its cells. `shapes/` is a 16-glyph example.

### Color
- **Standard | Redshift | Octane** (one row of buttons): builds a generic glyph material for that renderer and puts it on the generator, replacing the material on its last texture tag. Each one has a dark diffuse body, the current plate knocks out opacity (black = gone), and emission = the Color tab. Glyph Color and Emission Strength stay linked: changing them updates every Glyph It material on the generator, once you release the slider. All three render the per-glyph colours (Random per Cell / per Glyph / From Image). Standard reads them through a hidden "GlyphIt Color Proxy" child that Glyph It keeps in sync (the Standard renderer can't read a colour tag inside a generator directly); it isn't shown in the Object Manager, and a baked copy gets its own "GlyphIt Standard baked" material. In the viewport, Redshift shows the per-glyph colours; Standard shows the plate knockout with the flat Glyph Color.
- **Switching renderers:** the buttons reuse one material per renderer, so they work as a switch and never pile up copies. Look-dev with **Redshift** (knockout and colours in the viewport) or **Standard** (knockout in the viewport, colours in the render), then press **Octane** for the Live Viewer or the final render, and press back any time. The Octane material doesn't show in the C4D viewport: Octane materials only preview there through Octane's own texture preview, which can't show a vertex attribute or an opacity knockout. C4D also renders and displays only the last texture tag, so one material can't drive the viewport while another drives the render.
- **Octane, matched to Redshift:** Octane's Attribute Texture averages a vertex colour tag per *point*, so neighbouring glyphs bled into each other (colour noise instead of one colour per glyph). Octane now reads the colours from a palette texture (`plates/library/_custom/palette_<hash>.png`, 256×256, one pixel per cell or glyph) through a second UV set, **Glyph It Palette UV** (UV set 2). UVs are stored per polygon corner, so every glyph is one flat colour, exactly like Redshift's vertex colours; both come from the same colour table. The plate is read as non-colour data (read as sRGB, the glyph edges got thinned), and the emission is a flat colour rather than a light (surface brightness on, sampling rate 0, invisible to diffuse/specular rays, double sided), so there is no light-sampling noise. The Octane button adds an Octane Object tag with **Force Updates** on; without it the Live Viewer never re-sent the generator's geometry. Colour edits update the Live Viewer instantly (a new palette file); colour-mode changes re-send the mesh. All of this was verified in the Live Viewer.
- **Live updates:** every real change now raises the dirty counters on the output objects and their UV and colour tags. `GetClone()` resets them to 1, so renderers that compare dirty counts, like Octane's Live Viewer, saw nothing new until the generator was toggled. Idle redraws keep the same values.
- **Glyph Color**: colour picker for the glyph emission. **Emission Strength**: glow of the generator's Glyph It materials.
- **Color Mode**: Uniform (every glyph the picked colour), Random per Cell (one colour per plate cell, so every "A" or every icon of one kind matches), Random per Glyph (every glyph its own colour), **From Image** (every glyph takes the average colour of the Value tab's Image File / Shader under it, times Glyph Color; white keeps the image's own colours). From Image works with any Value Source, e.g. random glyphs coloured by a movie. Redshift reads exact colours; Octane uses a fixed RGB565 palette, which is close enough that you won't see the difference.
- **Random Amount**: 0 = all Glyph Color, 1 = fully random; 0.1–0.3 gives mostly uniform with a hint of variation. **Hue Spread** limits the randoms to hues near Glyph Color (1 = any hue; a white or grey Glyph Color leaves the hue free). **Random Saturation**, **Brightness Jitter** and **Color Seed** shape and re-roll them.
- Colour changes never re-shuffle glyphs and never re-solve UVs; only the vertex colour tag is rewritten. Changing plate settings no longer re-solves either.
- How it renders: Glyph It writes a polygon-mode Vertex Color tag named `Glyph It Color`. Materials made by `build_plate_material` (Redshift Vertex Attribute → texture colour multiplier, or emission/base for hologram) reads it; Octane reads the same colours from a palette texture on UV set 2. The picker then sets the colour; the plate gives the shapes. Older materials: `runtime.enable_glyph_color(mat)`. Scripts: `api.material(doc, obj, "standard" | "redshift" | "octane")`, `api.sync_colors(obj)`.

### Output
- **UV Mode**: Atlas is the normal mode and works in every renderer. Encoded is experimental and only for `glyphit_atlas.osl`.
- **Write ID Tag**: an extra UV tag (value, random) per polygon, for custom shaders.
- **Cell Selection Tags**: one polygon selection per cell (`GG_cell_00`…), so each glyph cell can get its own material.
- **Keep Source UVs**: keep the object's original UV tags as well.
- **Merge Objects**: several objects or clones become one mesh with one global even split.
- **Lock Glyphs to Topology** (default on): when only the points move, every glyph stays on its polygon and rides along. UVs re-solve only when the polygon count/order or a Glyph It setting changes. With Random / Vertex Map / Index values the solve always runs on the rest shape (before deformers), so the layout doesn't depend on the frame you tweaked a setting at.
- **Bake to Polygon Object**: editable copy with the UVs, selections, colours and material; the generator is switched off. For sculpting or other tools.
- **Write UVs onto Source**: puts the UV and colour tags straight onto the editable source, moves it out next to Glyph It and switches Glyph It off. Use it for final cloth / soft-body renders (script: `api.write_uvs_onto_source(obj)`).

### Deformers, cloth and dynamics
Glyph It only writes UVs, so anything that moves points carries the glyphs:

- **Deformer under Glyph It** (next to the source): bends the *output*. The source is read before deformers, so the bend is applied once and nothing re-solves while you scrub it.
- **Deformer under the source**: bends the *input*; with the lock on the glyphs stay put. Use one placement or the other on a setup, not both.
- **Cloth / Soft Body**: make the source editable, keep it under Glyph It and add the Simulation Cloth tag (plus a Collider on whatever it lands on). The glyphs flow with it live, but the output trails the simulation by one frame because simulations run after generators. Sim tags are never copied onto the output (a copied Cloth tag made two sims fight, which was the playback flicker). For the final render press **Write UVs onto Source**: the cloth mesh then carries the glyphs itself, with no lag. `~/Documents/Glyph It/Glyph It_cloth_demo.c4d` drapes a 40×40 sheet over a sphere.
- Animated meshes, MoGraph effectors on points, Alembic with fixed topology: same rule. Meshes whose topology changes every frame (remeshers, fluids) re-solve every frame.

**The ASCII-shading recipe:** Plate Style 1 (ASCII), Value Source = Light / Lambert, Target = a null, Distribute = Value, Dither ≈ 0.35. Move the null and the glyph density follows the light.

## Plates

**Plate cache:** every custom-text, collection, mixed and colour-palette plate is built once, the first time it's used, and cached in `plates/library/_custom`, `plates/library/_mixed` and `plates/collections/*/_built`. The Plate tab's **Clear Plate Cache** button (next to Open Plates Folder) empties those folders and immediately rebuilds only the plates and palettes the Glyph Its in the open scene use. Built-in library plates and your own collection images are never touched.

The cell order matches the solver: cell 0 is top-left, then left→right, top→bottom.

```bash
python glyphit/plates.py ascii    --grid 4 --out plates/ascii16.png         # ink-sorted ramp
python glyphit/plates.py chars    --grid 4 --text "SDIMAGING0123456" --out brand16.png
python glyphit/plates.py bayer    --grid 4 --dots 8 --out bayer16.png       # 4x4 Bayer = 16 exact levels
python glyphit/plates.py halftone --grid 3 --shape round --out dots9.png
python glyphit/plates.py logo     --grid 2 --images a.png b.png c.png d.png --out logos4.png
python glyphit/plates.py ascii    --grid 4 --frames 12 --shimmer --flipbook --out seq/ascii16
python glyphit/plates.py library  --out plates/library               # all styles x 1/4/9/16-up
python glyphit/plates.py sheet    --out contact.png                 # style x grid matrix
```

`--shimmer` re-picks every cell, every frame, from characters of **similar ink density**. A value-driven layout keeps its tone while the glyphs boil. `--flipbook` packs all frames into one atlas for the OSL shader.

## Looks

`build_plate_material(doc, obj, plate, mode=...)` builds a Redshift material in one of three modes:

- **`hologram`**: the plate drives **opacity**, so black is knocked out. Emission and base colour are a constant (green by default). You get see-through shapes built from characters, and the back faces show through.
- **`emissive`**: glyphs glow on a black body.
- **`diffuse`**: the plate drives base colour.

**Octane:** `build_plate_material_octane(doc, obj, plate, mode="hologram" | "emissive" | "diffuse", color=(r,g,b), emission=5.0)` builds the same three looks for Octane: an Octane Diffuse material, ImageTexture → opacity, RGB colour → Texture emission (hologram) or ImageTexture × colour (emissive / diffuse). It was verified with an Octane render. The Plate tab's auto-swap re-points the ImageTexture too.

To change the plate later, call `set_plate(mat, path)`. For an animated sequence, point the texture at `plates/seq/ascii_16up_0000.png` and enable the sequence in the texture node.

## Encoded mode + OSL (live grid, per-polygon time offsets)

With UV Mode = Encoded, each polygon's UV is `(valueLevel + s, randomLevel + t)`. The integer part carries data; the fractional part is the polygon's own 0–1 glyph frame. `glyphit_atlas.osl` decodes this and picks the cell and the flipbook frame in the shader. Grid size, value remap, cell shift and **per-polygon time offset** (each polygon starts the loop at a different frame) can then all be changed live, without rebuilding UVs.

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

- Linking Glyph It itself (or one of its parents) as the Source Object is ignored.

- Plate auto-swap runs from the generator's `message()` on real Attribute Manager edits. Scripts should call `runtime.plate_path()` + `swap_plate()` directly: messaging the generator from inside another Python call deadlocks C4D (gotcha #127).

- After editing the child, the generator can show the previous result for one redraw. It updates on the next pass. Render documents are correct on the first pass.
- Linked Target objects and fields are tracked by their dirty state. Keyframed changes need **Rebuild Every Frame**.
- Back faces read mirrored when seen through a hologram material. That is expected; flip it per side with two materials if needed.

See gotchas #117–#136 at the end of `docs/c4d_2026_api_gotchas.md` for the API lessons behind this tool.
