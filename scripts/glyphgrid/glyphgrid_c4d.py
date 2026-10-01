"""
GlyphGrid for Cinema 4D: builder + bake.

    import sys; sys.path.insert(0, "<repo>/scripts/glyphgrid")
    import glyphgrid_c4d as G
    gen = G.build_generator(doc, source=my_object)    # live Python Generator
    G.bake_object(doc, poly_obj, grid=4, seed=7)       # one-shot on an editable mesh

The generator is self-contained: core + runtime source are pasted into its
OPYTHON_CODE, so the .c4d file works on any machine without this repo.
"""
import os

import c4d

HERE = os.path.dirname(os.path.abspath(__file__))


def font_data(name):
    """PostScript font name -> c4d.FontData for the font chooser."""
    from c4d.bitmaps import GeClipMap
    fd = c4d.FontData()
    desc = GeClipMap.GetFontDescription(name or "Menlo-Bold", c4d.GE_FONT_NAME_POSTSCRIPT)
    if desc is not None:
        fd.SetFont(desc)
    return fd


def collection_names(cdir):
    try:
        return sorted(d for d in os.listdir(cdir)
                      if os.path.isdir(os.path.join(cdir, d)) and not d.startswith((".", "_")))
    except Exception:
        return []

# ------------------------------------------------------------ user data spec ---
# (name, kind, default, extra)
#   kind: tab | help | int/float/pct/deg/bool/cycle/string/link/fields/dist
#   "tab" = its own Attribute Manager tab, "help" = collapsed "How this tab works" group of text lines
HELP = {
    "Grid": [
        "GRID: how many glyph cells and which polygon gets which.",
        "Grid (N x N): 1 = 1-up (one glyph everywhere), 2 = 4-up,",
        "  3 = 9-up, 4 = 16-up (max) - must match the plate you use.",
        "Seed: re-deals which polygon gets which cell.",
        "Glyph Offset: every polygon steps N places forward in the plate",
        "  (reading order, wraps around): 4-up A B / C D, offset 1 =",
        "  A->B, B->C, C->D, D->A. Layout + counts stay the same.",
        "  Keyframe 0,1,2,3... = all glyphs tick like a split-flap board.",
        "Source Object: optional. Drag any object here instead",
        "  of putting it under GlyphGrid (leave empty if it's a child).",
        "Cells are numbered top-left first, left to right,",
        "  row by row:  4-up = 1 2 / 3 4   9-up = 1 2 3 / 4 5 6 / 7 8 9",
        "Grid Mode Mixed: every polygon draws from the 1-, 4-, 9- or",
        "  16-up plate (Grid N is ignored). Level Weights = share per",
        "  level, e.g. 1,0,2,4 = no 4-up, 16-up 4x as common as 1-up.",
        "  The plate becomes one composite: TL 1-up, TR 4-up,",
        "  BL 9-up, BR 16-up (built automatically).",
    ],
    "Islands": [
        "ISLANDS & FIT: how each polygon sits inside its cell.",
        "Island Mode: Polygon = every polygon is one glyph.",
        "  Ngon = ngons stay one glyph (default).",
        "  Cluster = one big glyph across neighbouring polygons",
        "  (size = Cluster Size, in scene units).",
        "  Quadtree = mixed glyph SIZES: blocks of N x N polygons",
        "  (Quadtree Block) randomly split into halves, quarters ...",
        "  down to single polygons. Blocks snap to the mesh's own",
        "  polygon grid, so on planes / grids every edge lines up.",
        "  Levels = number of glyph sizes (1-4): Block, /2, /4, /8.",
        "  Levels 4 needs a Block of 8+ polys - raising Levels grows",
        "  the Block to fit. Subdivide Chance =",
        "  how often a block splits (0 = all big, 1 = all small).",
        "Fit Auto: square-ish quads fill the cell edge to edge;",
        "  triangles, ngons and long thin polygons are scaled",
        "  uniformly and centred (no stretching).",
        "Flush Aspect Limit: how stretched a quad may be and",
        "  still fill the cell (1.35 = 35 % longer than wide).",
        "Gutter: empty border in every cell (stops glyph bleed).",
        "Glyph Scale: shrink / grow every glyph in its cell.",
    ],
    "Orientation": [
        "ORIENTATION: which way the glyphs face.",
        "Orient Up Axis: glyphs stand upright toward Up Axis.",
        "  Random 90: each glyph turned 0 / 90 / 180 / 270.",
        "  Random Free: any angle (glyphs shrink to fit the cell).",
        "  Edge Flow: follows each polygon's first edge.",
        "Up Space: World = up stays world up while the object",
        "  rotates. Object = up turns with the object.",
        "Rotation Jitter: small random tilt per glyph.",
        "Random Mirror: flips about half of the glyphs.",
        "Flip Mirror: use if every glyph reads backwards.",
    ],
    "Distribution": [
        "DISTRIBUTION: how polygons are dealt to the cells.",
        "Even Random: random, same count in every cell.",
        "  (Value Source is ignored.)",
        "Weighted Random: random, cells share by Weights.",
        "Weights: relative amounts per cell in plate order.",
        "  4-up 8,1,1,1 = top-left 8 parts, top-right 1,",
        "  bottom-left 1, bottom-right 1  ->  73 % / 9 / 9 / 9 %.",
        "  Missing entries count as 1. Only used by Weighted.",
        "Value: the Value tab picks the cell. Low values ->",
        "  first cell (top-left), high -> last (bottom-right).",
        "  Use a plate sorted sparse -> dense for shading.",
        "Value Equalized: same order as Value but every cell gets",
        "  the same count = maximum contrast, less literal.",
    ],
    "Value": [
        "VALUE: what drives the Value / Value Equalized modes.",
        "Light / Lambert: brightness from Target Object (any null).",
        "  Directional Light OFF = bulb at the Target's position.",
        "  Directional Light ON = sun along the Target's -Z axis",
        "  (rotate the null; its position is ignored).",
        "  Light Wrap: light creeps past the shadow line (softer).",
        "Field: the Fields list. Height: along the Up Axis.",
        "Camera Facing: faces pointing at the camera = high.",
        "Curvature: convex = high, concave = low.",
        "Distance: from Target (Distance Period repeats rings).",
        "Invert / Gamma / Contrast / Offset reshape the value.",
        "  Wrap Values + animated Offset = glyphs ripple across.",
        "Knockout: hide glyphs where the value is below (or above)",
        "  the threshold - works the same on EVERY plate. In Value",
        "  mode the field only picks WHICH cell; plates whose first",
        "  cell is empty (ASCII, Bayer, dots) look knocked out, hex /",
        "  collections never do. Knockout makes it explicit.",
        "  With Distribute = Even Random + Knockout: random glyphs,",
        "  the field is just a reveal mask (animate it to wipe on).",
        "  Knockout Softness = dissolve-style ragged edge.",
        "Random Mix: 0 = pure value, 1 = pure random.",
        "Dither: jitter before choosing -> breaks hard bands of",
        "  one glyph into a smooth mix (try 0.3 - 0.6).",
        "Rebuild Every Frame: ON when lights, fields or Offset",
        "  are animated.",
    ],
    "Color": [
        "COLOR: tints the glyphs.",
        "Standard / Redshift / Octane: makes a glyph material for",
        "  that renderer and puts it on this generator: dark diffuse",
        "  body, the current plate knocks out opacity (black = gone),",
        "  emission = the colours set here. Press again any time.",
        "  Standard shows Glyph Color only (no random colours -",
        "  the Standard renderer can't read the colour tag).",
        "Glyph Color: the emission colour (the picker).",
        "Emission Strength: glow of every GlyphGrid material here.",
        "  Colours are written as a vertex colour tag 'GlyphGrid",
        "  Color' (RS Vertex Attribute / Octane Attribute Texture);",
        "  for an older material: G.enable_glyph_color(mat).",
        "Color Mode Uniform: every glyph = Glyph Color.",
        "  Random per Cell: one colour per plate cell, so every",
        "  'A' (or every icon of one kind) shares a colour.",
        "  Random per Glyph: every glyph gets its own colour.",
        "Random Amount: 0 = all Glyph Color, 1 = fully random.",
        "  Low values (0.1 - 0.3) = mostly uniform with a hint.",
        "Hue Spread: how far round the colour wheel randoms may",
        "  go from Glyph Color's hue (1 = any hue). With white /",
        "  grey Glyph Color the hue is free.",
        "Random Saturation: how colourful the random colours are.",
        "Brightness Jitter: some glyphs darker (0 = none).",
        "Color Seed: re-rolls the colours, glyphs stay put.",
        "Colour changes never re-shuffle glyphs.",
    ],
    "Output": [
        "OUTPUT: what gets written.",
        "UV Mode Atlas: the normal mode, works in every renderer.",
        "  Encoded: experimental, only for glyphgrid_atlas.osl.",
        "Write ID Tag: extra UV tag (value, random) per polygon",
        "  for custom shaders. Usually off.",
        "Cell Selection Tags: one polygon selection per cell",
        "  (GG_cell_00 ...) to give each glyph cell its own material.",
        "Keep Source UVs: keep the object's original UV tags too.",
        "Merge Objects: several objects / clones become one mesh",
        "  = one global even split.",
        "Lock Glyphs to Topology ON (default): when only the POINTS",
        "  move (a deformer, cloth, animated mesh) every glyph stays",
        "  on its polygon and rides along - no re-shuffle. UVs re-solve",
        "  only when the polygon count / order or a GlyphGrid setting",
        "  changes. OFF = re-solve on every point change.",
        "Deformers: put them UNDER GlyphGrid (bend the output) or",
        "  under the source object (bend the input) - both keep",
        "  glyphs locked. Don't mix the two on one setup.",
        "Cloth / Soft Body live under GlyphGrid: works, but the",
        "  output trails the simulation by one frame (sims run after",
        "  generators). For final renders press Write UVs onto",
        "  Source: the UV + colour tags go onto the cloth mesh itself,",
        "  it moves out next to GlyphGrid, GlyphGrid switches off.",
        "Bake to Polygon Object: editable COPY with the UVs +",
        "  material, generator off (sculpting, other tools).",
        "Custom glyphs / your own letters: see the Plate tab.",
    ],
    "Plate": [
        "PLATE: which image the material shows. Library files are",
        "  <style>_<cells>up.png, so Style picks the ROW and",
        "  Grid (N x N) picks the COLUMN (1 / 4 / 9 / 16-up).",
        "Auto Swap Plate ON: changing Grid or Style swaps the",
        "  texture in this object's material automatically.",
        "Styles: 1 ASCII ramp  2 Bayer dither  3 Halftone dots",
        "  4 Halftone squares  5 Noise  6 Hex  7 Binary  8 Custom",
        "  9 Collection = your own folder of images.",
        "Collection: dropdown of the folders in plates/collections.",
        "  Picking one switches Plate Style to 9 Collection.",
        "  Open Collections Folder = Finder. New Collection = makes an",
        "  empty folder + opens it. Refresh List = after adding folders.",
        "  Images sorted by filename = priority:",
        "  1-up uses #1, 4-up #1-4, 9-up #1-9, 16-up #1-16; fewer images",
        "  repeat. Hand-made name_4up.png files are used as they are.",
        "Custom: type your own characters in Custom Glyphs,",
        "  e.g. SDIMAGING or 0123456789 or .:-=+*#%@",
        "  Sort by Ink orders them sparse -> dense (for Value).",
        "  Font = Cinema 4D's font picker (any installed font).",
        "Apply Plate Now: force the swap (e.g. after editing text).",
        "Plates Folder (top): the GlyphGrid plates root - holds library/",
        "  (built-in styles) and collections/ (your folders). '...' browses,",
        "  Open Plates Folder shows it in Finder. Custom plates are cached",
        "  plates are written).",
        "Only textures that point at plate files (or the node",
        "  named gg_plate) are swapped - other maps are untouched.",
    ],
}

UD_SPEC = [
    ("Grid", "tab", None, {}),
    ("Grid", "help", None, {}),
    ("Grid (N x N)", "int", 4, dict(min=1, max=4)),
    ("Seed", "int", 12345, dict(min=0, max=999999)),
    ("Glyph Offset", "int", 0, dict(min=-1000, max=1000)),
    ("Grid Mode", "cycle", 0, dict(items=["Single (Grid N x N)", "Mixed 1/4/9/16-up (composite plate)"])),
    ("Level Weights", "string", "1,1,1,1", {}),
    ("Source Object", "link", None, {}),

    ("Islands", "tab", None, {}),
    ("Islands", "help", None, {}),
    ("Island Mode", "cycle", 1, dict(items=["Polygon", "Ngon (keep ngons whole)", "Cluster (glyph spans polys)",
                                            "Quadtree (mixed glyph sizes)"])),
    ("Cluster Size", "dist", 50.0, dict(min=0.0)),
    ("Quadtree Block (polys)", "cycle", 3, dict(items=["1", "2", "4", "8", "16", "32", "64"])),
    ("Quadtree Levels", "int", 4, dict(min=1, max=4)),
    ("Subdivide Chance", "pct", 0.5, dict(min=0.0, max=1.0)),
    ("Fit", "cycle", 0, dict(items=["Auto: flush squares, centre the rest", "Uniform + Centred (all)", "Flush (all quads)"])),
    ("Flush Aspect Limit", "float", 1.35, dict(min=1.0, max=4.0, step=0.05)),
    ("Gutter", "pct", 0.02, dict(min=0.0, max=0.45)),
    ("Glyph Scale", "pct", 1.0, dict(min=0.0, max=2.0)),

    ("Orientation", "tab", None, {}),
    ("Orientation", "help", None, {}),
    ("Orient", "cycle", 0, dict(items=["Up Axis (upright)", "Random 90 deg", "Random Free", "Edge Flow"])),
    ("Up Axis", "cycle", 0, dict(items=["+Y", "+Z", "+X", "-Y"])),
    ("Up Space", "cycle", 0, dict(items=["World", "Object"])),
    ("Rotation Jitter", "deg", 0.0, dict(min=0.0, max=180.0)),
    ("Random Mirror", "bool", False, {}),
    ("Flip Mirror", "bool", False, {}),

    ("Distribution", "tab", None, {}),
    ("Distribution", "help", None, {}),
    ("Distribute", "cycle", 0, dict(items=["Even Random (exact counts)", "Weighted Random", "Value", "Value Equalized (exact counts)"])),
    ("Weights", "string", "", {}),

    ("Value", "tab", None, {}),
    ("Value", "help", None, {}),
    ("Value Source", "cycle", 0, dict(items=["Random", "Vertex Map", "Field", "Height (Up Axis)", "Light / Lambert",
                                             "Camera Facing", "Curvature", "Polygon Index", "Distance"])),
    ("Target Object", "link", None, {}),
    ("Directional Light", "bool", False, {}),
    ("Light Wrap", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Fields", "fields", None, {}),
    ("Vertex Map Name", "string", "", {}),
    ("Distance Period", "dist", 0.0, dict(min=0.0)),
    ("Curvature Scale", "float", 4.0, dict(min=0.0, max=100.0)),
    ("Invert", "bool", False, {}),
    ("Gamma", "float", 1.0, dict(min=0.05, max=10.0, step=0.05)),
    ("Contrast", "float", 1.0, dict(min=0.0, max=10.0, step=0.05)),
    ("Offset", "float", 0.0, dict(min=-10.0, max=10.0, step=0.01)),
    ("Wrap Values", "bool", False, {}),
    ("Knockout", "cycle", 0, dict(items=["Off", "Hide below threshold", "Hide above threshold"])),
    ("Knockout Threshold", "pct", 0.5, dict(min=0.0, max=1.0)),
    ("Knockout Softness", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Random Mix", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Dither", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Rebuild Every Frame", "bool", False, {}),

    ("Plate", "tab", None, {}),
    ("Plate", "help", None, {}),
    ("row_folder", "row", None, dict(columns=2)),
    ("Plates Folder", "folder", "", {}),
    ("Open Plates Folder", "button", None, {}),
    ("row_folder", "endrow", None, {}),
    ("Plate Style", "cycle", 0, dict(items=["1 ASCII ramp", "2 Bayer dither", "3 Halftone dots", "4 Halftone squares",
                                            "5 Noise", "6 Hex digits", "7 Binary 0/1", "8 Custom (type below)",
                                            "9 Collection (pick below)"])),
    ("row_coll", "row", None, dict(columns=4)),
    ("Collection", "cycle", 0, dict(items_fn="collections")),
    ("Open Collection", "button", None, {}),
    ("Refresh List", "button", None, {}),
    ("New Collection", "button", None, {}),
    ("row_coll", "endrow", None, {}),
    ("Custom Glyphs", "string", "", {}),
    ("Font", "font", "Menlo-Bold", {}),
    ("Sort by Ink", "bool", True, {}),
    ("row_apply", "row", None, dict(columns=2)),
    ("Auto Swap Plate", "bool", True, {}),
    ("Apply Plate Now", "button", None, {}),
    ("row_apply", "endrow", None, {}),

    ("Color", "tab", None, {}),
    ("Color", "help", None, {}),
    ("row_mat", "row", None, dict(columns=3)),
    ("Standard", "button", None, {}),
    ("Redshift", "button", None, {}),
    ("Octane", "button", None, {}),
    ("row_mat", "endrow", None, {}),
    ("Glyph Color", "color", (1.0, 1.0, 1.0), {}),
    ("Emission Strength", "float", 1.0, dict(min=0.0, max=20.0, step=0.05)),
    ("Color Mode", "cycle", 0, dict(items=["Uniform (one colour)", "Random per Cell (every 'A' alike)",
                                           "Random per Glyph"])),
    ("Random Amount", "pct", 0.35, dict(min=0.0, max=1.0)),
    ("Hue Spread", "pct", 1.0, dict(min=0.0, max=1.0)),
    ("Random Saturation", "pct", 0.8, dict(min=0.0, max=1.0)),
    ("Brightness Jitter", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Color Seed", "int", 1, dict(min=0, max=9999)),

    ("Output", "tab", None, {}),
    ("Output", "help", None, {}),
    ("UV Mode", "cycle", 0, dict(items=["Atlas (grid baked in UVs)", "Encoded (experimental, OSL)"])),
    ("Write ID Tag", "bool", False, {}),
    ("Cell Selection Tags", "bool", False, {}),
    ("Keep Source UVs", "bool", False, {}),
    ("Merge Objects", "bool", True, {}),
    ("Lock Glyphs to Topology", "bool", True, {}),
    ("row_bake", "row", None, dict(columns=2)),
    ("Bake to Polygon Object", "button", None, {}),
    ("Write UVs onto Source", "button", None, {}),
    ("row_bake", "endrow", None, {}),
]


def _add_help(op, tab_name, parent):
    """Collapsed 'How this tab works' group with one static text line per entry."""
    bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_GROUP)
    bc[c4d.DESC_NAME] = bc[c4d.DESC_SHORT_NAME] = "? How this tab works"
    bc[c4d.DESC_COLUMNS] = 1
    bc[c4d.DESC_DEFAULT] = 0          # closed until clicked
    bc[c4d.DESC_PARENTGROUP] = parent
    g = op.AddUserData(bc)
    for k, line in enumerate(HELP.get(tab_name, [])):
        t = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STATICTEXT)
        t[c4d.DESC_NAME] = t[c4d.DESC_SHORT_NAME] = line
        t[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_STATICTEXT
        t[c4d.DESC_PARENTGROUP] = g
        op.AddUserData(t)
    return g


def _add_ud(op, name, kind, default, extra, parent):
    if kind in ("group", "tab"):
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_GROUP)
        bc[c4d.DESC_COLUMNS] = 1
        bc[c4d.DESC_DEFAULT] = 1  # open
        if kind == "tab":
            # an EMPTY DescID parent makes a top-level Attribute Manager tab (verified 2026.4)
            bc[c4d.DESC_NAME] = bc[c4d.DESC_SHORT_NAME] = name
            bc[c4d.DESC_PARENTGROUP] = c4d.DescID()
            return op.AddUserData(bc)
    elif kind in ("int",):
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_LONG)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_LONGSLIDER
    elif kind in ("float", "pct", "deg", "dist"):
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_REAL)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_REALSLIDER if kind != "dist" else c4d.CUSTOMGUI_REAL
        bc[c4d.DESC_UNIT] = {"pct": c4d.DESC_UNIT_PERCENT, "deg": c4d.DESC_UNIT_DEGREE,
                             "dist": c4d.DESC_UNIT_METER}.get(kind, c4d.DESC_UNIT_FLOAT)
        # DESC_STEP defaults to 1.0 = 100 % for percent sliders: the arrows jumped 5 % -> 100 %
        # and the slider snapped. Always give REAL params a sensible step.
        default_step = {"pct": 0.01, "deg": 0.017453292519943295, "dist": 1.0}.get(kind, 0.01)
        bc[c4d.DESC_STEP] = extra.get("step", default_step)
    elif kind == "bool":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_BOOL)
    elif kind == "cycle":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_LONG)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_CYCLE
        cyc = c4d.BaseContainer()
        items = extra.get("items") or (collection_names(os.path.join(HERE, "plates", "collections"))
                                       if extra.get("items_fn") == "collections" else [])
        for i, s_ in enumerate(items or ["(none)"]):
            cyc.SetString(i, s_)
        bc[c4d.DESC_CYCLE] = cyc
    elif kind == "string":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STRING)
    elif kind == "color":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_COLOR)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_COLOR
    elif kind == "folder":   # path field with the standard "..." browse button, directory mode
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_FILENAME)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_FILENAME
        bc[c4d.FILENAME_DIRECTORY] = True
        bc[c4d.DESC_SCALEH] = True
    elif kind == "font":     # Cinema 4D's own font chooser (same as the Text object)
        bc = c4d.GetCustomDataTypeDefault(c4d.FONTCHOOSER_DATA)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_FONTCHOOSER
        bc[c4d.DESC_SCALEH] = True
    elif kind == "button":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_BUTTON)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_BUTTON
    elif kind == "link":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_BASELISTLINK)
    elif kind == "fields":
        bc = c4d.GetCustomDataTypeDefault(c4d.CUSTOMDATATYPE_FIELDLIST)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_FIELDLIST
    else:
        raise ValueError(kind)
    bc[c4d.DESC_NAME] = name
    bc[c4d.DESC_SHORT_NAME] = name
    if parent is not None:
        bc[c4d.DESC_PARENTGROUP] = parent
    if "min" in extra:
        conv = (lambda v: v * 3.141592653589793 / 180.0) if kind == "deg" else (lambda v: v)
        bc[c4d.DESC_MIN] = conv(extra["min"])
        bc[c4d.DESC_MINSLIDER] = conv(extra["min"])
    if "max" in extra:
        conv = (lambda v: v * 3.141592653589793 / 180.0) if kind == "deg" else (lambda v: v)
        bc[c4d.DESC_MAX] = conv(extra["max"])
        bc[c4d.DESC_MAXSLIDER] = conv(extra["max"])
    # (no tooltip key is exposed to Python user data; "tip" entries are documentation only)
    did = op.AddUserData(bc)
    if kind == "font":
        op[did] = font_data(default)
    elif kind == "color":
        op[did] = c4d.Vector(*default)
    elif default is not None and kind not in ("group", "button"):
        op[did] = default
    return did


def add_user_data(op):
    parent = None
    stack = []
    for name, kind, default, extra in UD_SPEC:
        if kind == "help":
            _add_help(op, name, parent)
            continue
        if kind == "row":   # untitled sub-group laying its items out side by side
            bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_GROUP)
            bc[c4d.DESC_NAME] = bc[c4d.DESC_SHORT_NAME] = ""
            bc[c4d.DESC_COLUMNS] = int(extra.get("columns", 2))
            bc[c4d.DESC_DEFAULT] = 1
            bc[c4d.DESC_TITLEBAR] = False
            bc[c4d.DESC_PARENTGROUP] = parent
            stack.append(parent)
            parent = op.AddUserData(bc)
            continue
        if kind == "endrow":
            parent = stack.pop()
            continue
        did = _add_ud(op, name, kind, default, extra, None if kind in ("group", "tab") else parent)
        if kind in ("group", "tab"):
            parent = did
            stack = []


# ------------------------------------------------------------- generator code ---
GENERATOR_MAIN = r'''
# ======================================================= GlyphGrid generator ===
_GG_STATE = {"frame": None, "stats": None}


def _gg_params():
    U = {bc[c4d.DESC_NAME]: did for did, bc in op.GetUserDataContainer()
         if did[-1].dtype not in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT)}

    def g(k, d=None):
        try:
            return op[U[k]] if k in U else d
        except Exception:
            return d
    w = [float(x) for x in str(g("Weights", "") or "").replace(";", ",").replace(" ", ",").split(",") if x.strip()]
    params = dict(
        grid=int(g("Grid (N x N)", 4)), seed=int(g("Seed", 12345)), cell_shift=int(g("Glyph Offset", g("Cell Shift", 0))),
        island=int(g("Island Mode", 1)), cluster_size=float(g("Cluster Size", 50.0)),
        qt_cells=1 << int(g("Quadtree Block (polys)", 3)), qt_levels=int(g("Quadtree Levels", 4)),
        qt_split=float(g("Subdivide Chance", 0.5)), grid_mode=int(g("Grid Mode", 0)),
        level_weights=[float(x) for x in str(g("Level Weights", "") or "").replace(";", ",").replace(" ", ",").split(",")
                       if x.strip()] or None,
        fit=int(g("Fit", 0)), flush_aspect=float(g("Flush Aspect Limit", 1.35)),
        gutter=float(g("Gutter", 0.02)), glyph_scale=float(g("Glyph Scale", 1.0)),
        orient=int(g("Orient", 0)), up=UP_AXES[int(g("Up Axis", 0))],
        fallback_up=(0, 0, 1) if int(g("Up Axis", 0)) != 1 else (0, 1, 0),
        up_space_world=int(g("Up Space", 0)) == 0,
        rot_jitter=float(g("Rotation Jitter", 0.0)) * 57.29577951308232,
        mirror_random=bool(g("Random Mirror", False)), flip_mirror=bool(g("Flip Mirror", False)),
        assign=int(g("Distribute", 0)), weights=w or None,
        invert=bool(g("Invert", False)), gamma=float(g("Gamma", 1.0)), contrast=float(g("Contrast", 1.0)),
        offset=float(g("Offset", 0.0)), wrap=bool(g("Wrap Values", False)),
        random_mix=float(g("Random Mix", 0.0)), dither=float(g("Dither", 0.0)),
        knockout=int(g("Knockout", 0)), knock_thr=float(g("Knockout Threshold", 0.5)),
        knock_soft=float(g("Knockout Softness", 0.0)),
        uv_mode=int(g("UV Mode", 0)),
    )
    src = int(g("Value Source", 0))
    opts = dict(vmap_name=g("Vertex Map Name", ""), fields=g("Fields", None), target=g("Target Object", None),
                light_directional=bool(g("Directional Light", False)), wrap_light=float(g("Light Wrap", 0.0)),
                period=float(g("Distance Period", 0.0)), curv_scale=float(g("Curvature Scale", 4.0)),
                height_axis=UP_AXES[int(g("Up Axis", 0))])
    opts["color"] = _color_opts(op)
    flags = dict(animate=bool(g("Rebuild Every Frame", False)), write_id=bool(g("Write ID Tag", False)),
                 cell_sel=bool(g("Cell Selection Tags", False)), keep=bool(g("Keep Source UVs", False)),
                 merge=bool(g("Merge Objects", True)), source=g("Source Object", None),
                 lock=bool(g("Lock Glyphs to Topology", True)))
    # clamp to what the solver can really use (old scenes / typed values / keyframes)
    params["grid"] = max(1, min(GRID_MAX, params["grid"]))
    params["qt_levels"] = max(1, min(QT_LEVELS_MAX, qt_level_cap(params["qt_cells"]), params["qt_levels"]))
    return params, src, opts, flags


def _gg_topo(geo):
    """Cheap topology key: point/poly counts + a few sampled polygons per part. Same key = the
    UV solve still fits, only the points moved (deformer, cloth, animation)."""
    key = []
    for o, _ in geo:
        n = o.GetPolygonCount()
        smp = []
        for i in sorted({0, n // 5, n // 3, n // 2, (2 * n) // 3, n - 1}):
            if 0 <= i < n:
                p = o.GetPolygon(i)
                smp.append((p.a, p.b, p.c, p.d))
        key.append((o.GetPointCount(), n, tuple(smp)))
    return tuple(key)


def _gg_stamp(root):
    """GetClone() resets dirty counters to 1, so every rebuilt output looked 'unchanged' to
    renderers that diff by dirty count (Octane's Live Viewer only caught up when the generator was
    toggled). Stamp the output objects + their UV / colour tags with a counter that rises on every
    real change and stays put on idle redraws."""
    dv = 2 * _GG_STATE.get("dv", 1) + 1     # SetDirty steps by 2: +2 per change keeps every counter rising
    stack = [root]
    while stack:
        o = stack.pop()
        while o is not None:
            for fl in (c4d.DIRTYFLAGS_DATA, c4d.DIRTYFLAGS_CACHE):
                k = 0
                while o.GetDirty(fl) < dv and k < 100000:
                    o.SetDirty(fl)
                    k += 1
            if o.IsInstanceOf(c4d.Opolygon):
                t = o.GetFirstTag()
                while t is not None:
                    if t.CheckType(c4d.Tuvw) or t.CheckType(c4d.Tvertexcolor):
                        k = 0
                        while t.GetDirty(c4d.DIRTYFLAGS_DATA) < dv and k < 100000:
                            t.SetDirty(c4d.DIRTYFLAGS_DATA)
                            k += 1
                    t = t.GetNext()
            if o.GetDown() is not None:
                stack.append(o.GetDown())
            o = o.GetNext()
    return root


def _gg_recolor(res, color):
    """Colour-only change: rewrite the vertex colour tags on the kept result (no re-solve)."""
    keys = _GG_STATE.get("colkeys") or []
    o = res.GetDown()
    k = 0
    while o is not None:
        if o.IsInstanceOf(c4d.Opolygon):
            if k < len(keys) and keys[k] is not None:
                r_ = {"cell": keys[k][0], "island": keys[k][1]}
                write_color_tag(o, glyph_colors(r_, color))
                write_palette_uv(o, color_keys(r_, color))
            k += 1
        o = o.GetNext()


def _gg_move_points(res, geo, gmg, inv, merge):
    """Reuse the solved result (UVs, selections, ID tags) and only replace its points."""
    objs = []
    o = res.GetDown()
    while o is not None:
        if o.IsInstanceOf(c4d.Opolygon):
            objs.append(o)
        o = o.GetNext()
    if merge or len(geo) == 1:
        if len(geo) == 1:
            pts, ml = geo[0][0].GetAllPoints(), geo[0][1]
        else:
            m = merge_parts([(p.GetClone(c4d.COPYFLAGS_NO_HIERARCHY), gmg * ml_) for p, ml_ in geo], inv)
            pts, ml = m.GetAllPoints(), m.GetMl()
        if len(objs) != 1 or objs[0].GetPointCount() != len(pts):
            return False
        objs[0].SetAllPoints(pts)
        objs[0].SetMl(ml)
        objs[0].Message(c4d.MSG_UPDATE)
        return True
    if len(objs) != len(geo):
        return False
    for po, (p, ml) in zip(objs, geo):
        if po.GetPointCount() != p.GetPointCount():
            return False
        po.SetAllPoints(p.GetAllPoints())
        po.SetMl(ml)
        po.Message(c4d.MSG_UPDATE)
    return True


def _count_polys(o):
    n = 0
    while o is not None:
        if o.IsInstanceOf(c4d.Opolygon):
            n += o.GetPolygonCount()
        n += _count_polys(o.GetDown())
        o = o.GetNext()
    return n


def _gg_eval_private(src_obj, inv, CF):
    """Build src_obj's geometry in a throw-away document (render flags, visibility forced on)."""
    try:
        tmp = c4d.documents.BaseDocument()
        cp = src_obj.GetClone(c4d.COPYFLAGS_NONE)
        cp.SetMg(src_obj.GetMg())
        cp[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = 2
        cp[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = 2
        tmp.InsertObject(cp)
        tmp.ExecutePasses(None, False, False, True, c4d.BUILDFLAGS_EXTERNALRENDERER)
        found = [(o.GetClone(CF), inv * mg) for o, mg in collect_polys(cp)]
        c4d.documents.KillDocument(tmp)
        return found
    except Exception as e:
        print("GlyphGrid: private build failed:", e)
        return []


def main():
    params, src, opts, flags = _gg_params()
    link = flags.get("source")
    child_mode = link is None
    sib_def = False          # deformers directly under GlyphGrid (siblings of the source)
    if child_mode:
        src_obj = None
        ch = op.GetDown()
        while ch is not None:
            if ch.GetInfo() & c4d.OBJECT_MODIFIER:
                sib_def = sib_def or bool(ch.GetDeformMode())   # switched-off deformers don't count
            elif src_obj is None:
                src_obj = ch
            ch = ch.GetNext()
    else:
        src_obj = link
    if src_obj is None:
        return None
    frame = doc.GetTime().GetFrame(doc.GetFps())
    tgt = opts.get("target")
    fl = opts.get("fields")
    gmg = op.GetMg()
    inv = ~gmg
    CF = c4d.COPYFLAGS_NO_HIERARCHY | c4d.COPYFLAGS_NO_ANIMATION | c4d.COPYFLAGS_NO_BITS
    # what the SOLVE depends on: colour and plate settings are left out, so changing them never
    # re-shuffles glyphs (colour = just a new vertex colour tag, plate = a material edit)
    o_ = {k: v for k, v in opts.items() if k not in ("color", "fields", "target")}
    solve_key = repr((sorted(params.items()), src, sorted(o_.items()),
                      [flags[k] for k in ("write_id", "cell_sel", "keep", "merge", "lock", "animate")],
                      str(tgt.GetGUID()) if tgt else 0))
    col_sig = repr(sorted(opts["color"].items()))
    own_sig = (op.GetDirty(c4d.DIRTYFLAGS_MATRIX), solve_key,
               tgt.GetDirty(c4d.DIRTYFLAGS_MATRIX | c4d.DIRTYFLAGS_DATA) if tgt else 0,
               fields_signature(fl, doc) if (fl is not None and src == SRC_FIELD) else 0,
               frame if flags["animate"] else 0, child_mode)

    # ---- source geometry, stored as (polygon clone, matrix relative to the generator)
    geo_changed = False
    if child_mode:
        # Standard hierarchy-clone pattern. In Python (2026.4) the clone is only valid when the
        # child really changed (dirty); otherwise it is an empty shell, because Cinema 4D hands the
        # child cache over once. So we keep our own COW copy of the geometry for params-only edits.
        for _try in range(3):   # first call after a change may only trigger the child build
            res = op.GetAndCheckHierarchyClone(hh, src_obj, c4d.HIERARCHYCLONEFLAGS_ASPOLY, False)
            cl = res.get("clone") if isinstance(res, dict) else None
            if isinstance(res, dict) and res.get("dirty") and cl is not None and _count_polys(cl):
                found = None
                if sib_def:
                    # Cinema 4D applies deformers under GlyphGrid to the source sibling too, and
                    # again to our output (= bent twice, UVs re-solved on the bent mesh). Read the
                    # source BEFORE deformers; they act on the output only.
                    found = [(o.GetClone(CF), inv * mg) for o, mg in collect_polys(src_obj, deform=False)]
                _GG_STATE["geo"] = found or [(o.GetClone(CF), o.GetMg()) for o, _ in collect_polys(cl)]
                geo_changed = True
                break
            if _GG_STATE.get("geo") and not (isinstance(res, dict) and res.get("dirty")):
                break
        if not _GG_STATE.get("geo"):
            # nothing stored yet and the child is "clean" (code was just refreshed, module reloaded):
            # evaluate a copy of the child in a private document once.
            try:
                tmp = c4d.documents.BaseDocument()
                cp = src_obj.GetClone(c4d.COPYFLAGS_NONE)
                cp.SetMl(src_obj.GetMl())
                tmp.InsertObject(cp)
                tmp.ExecutePasses(None, False, False, True, c4d.BUILDFLAGS_INTERNALRENDERER)
                found = [(o.GetClone(CF), o.GetMg()) for o, _ in collect_polys(cp)]
                if found:
                    _GG_STATE["geo"] = found
                    geo_changed = True
                c4d.documents.KillDocument(tmp)
            except Exception as e:
                print("GlyphGrid fallback build failed:", e)
    else:
        ssig = root_signature(src_obj)
        if ssig != _GG_STATE.get("src_sig") or not _GG_STATE.get("geo"):
            found = [(o.GetClone(CF), inv * mg) for o, mg in collect_polys(src_obj)]
            if not found:
                # hidden / render-invisible sources (or a fresh render document) have no caches:
                # evaluate a copy of the linked object (with its children) in a private document
                found = _gg_eval_private(src_obj, inv, CF)
            if found:
                _GG_STATE["geo"] = found
                _GG_STATE["src_sig"] = ssig
                geo_changed = True

    keep = _GG_STATE.get("result")
    # NOTE: never return op.GetCache() from a Python Generator (C4D frees it right after: "object is
    # not alive"). We return a clone of our own copy; polygon data is copy-on-write, ~0 ms.
    if not geo_changed and keep is not None and own_sig == _GG_STATE.get("own_sig"):
        if col_sig != _GG_STATE.get("col_sig"):
            _gg_recolor(keep, opts["color"])
            _GG_STATE["col_sig"] = col_sig
            _GG_STATE["dv"] = _GG_STATE.get("dv", 1) + 1
        return _gg_stamp(keep.GetClone())
    geo = _GG_STATE.get("geo")
    if not geo:
        return None
    topo = _gg_topo(geo)
    if flags["lock"] and keep is not None and own_sig == _GG_STATE.get("own_sig") \
            and topo == _GG_STATE.get("topo"):
        # only the points moved (deformer in the source, cloth, animated points, or a deformer
        # under GlyphGrid): keep every glyph where it is, just carry the new shape
        res_ = keep.GetClone()
        if _gg_move_points(res_, geo, gmg, inv, flags["merge"]):
            if col_sig != _GG_STATE.get("col_sig"):
                _gg_recolor(res_, opts["color"])
                _GG_STATE["col_sig"] = col_sig
            _GG_STATE["result"] = res_.GetClone()
            _GG_STATE["dv"] = _GG_STATE.get("dv", 1) + 1
            return _gg_stamp(res_)
    _GG_STATE["topo"] = topo
    _GG_STATE["own_sig"] = own_sig
    _GG_STATE["frame"] = frame
    # With the lock on, non-spatial sources solve on the REST shape (before deformers): the glyph
    # layout no longer depends on which frame / bend amount you happened to tweak a setting at.
    # Spatial sources (height, light, field ...) keep solving on the current shape.
    shape, rest_used = geo, False
    if flags["lock"] and src in (SRC_RANDOM, SRC_VMAP, SRC_INDEX) and not sib_def:
        try:
            rest = [(o.GetClone(CF), inv * mg) for o, mg in collect_polys(src_obj, deform=False)]
        except Exception:
            rest = []
        if rest and _gg_topo(rest) == topo:
            shape, rest_used = rest, True
    parts = [(o.GetClone(CF), gmg * ml) for o, ml in shape]   # (clone, world matrix)
    for o, _ in parts:
        # cache objects inherit the SOURCE's visibility: a hidden source (e.g. a linked SDS with
        # render visibility off) would make our output invisible to the renderer -> reset to default
        o[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = c4d.OBJECT_UNDEF
        o[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = c4d.OBJECT_UNDEF
        strip_sim_tags(o)

    root = c4d.BaseObject(c4d.Onull)
    root.SetName("GlyphGrid")
    stats = []
    if flags["merge"] or len(parts) == 1:
        if len(parts) == 1:
            po = parts[0][0]
            po.SetMl(inv * parts[0][1])
            vals = "sample"
        else:
            vals = None
            if src != SRC_RANDOM:
                vals = []
                for p_, mg_ in parts:
                    P_, F_, _ = read_mesh(p_)
                    v_ = sample_values(p_, mg_, P_, F_, src, opts, op, doc)
                    vals.extend(v_ if v_ is not None else [0.0] * len(F_))
                if src == SRC_INDEX or src == SRC_HEIGHT:
                    vals = "sample"   # global, recompute on the merged mesh
            po = merge_parts(parts, inv)
        po.InsertUnder(root)
        stats.append(apply_to_object(po, gmg * po.GetMl(), params, src, opts, caller=op, doc=doc,
                                     write_id=flags["write_id"], cell_sel=flags["cell_sel"],
                                     strip_uvs=not flags["keep"], values=vals))
    else:
        for k, (p_, mg_) in enumerate(parts):
            po = p_
            po.SetMl(inv * mg_)
            po.InsertUnderLast(root)
            prm = dict(params, seed=params["seed"] + 7919 * k)
            stats.append(apply_to_object(po, mg_, prm, src, opts, caller=op, doc=doc,
                                         write_id=flags["write_id"], cell_sel=flags["cell_sel"],
                                         strip_uvs=not flags["keep"]))
    tot = dict(polys=sum(s.get("polys", 0) for s in stats), islands=sum(s.get("islands", 0) for s in stats),
               cells=stats[0].get("cells"), min=min(s.get("min", 0) for s in stats),
               max=max(s.get("max", 0) for s in stats), t_solve=round(sum(s.get("t_solve", 0) for s in stats), 3),
               numpy=stats[0].get("numpy"), objects=len(parts))
    _GG_STATE["stats"] = tot
    _GG_STATE["colkeys"] = [s_.get("keys") for s_ in stats]
    _GG_STATE["col_sig"] = col_sig
    if rest_used:
        _gg_move_points(root, geo, gmg, inv, flags["merge"])   # solved at rest, shown deformed
    _GG_STATE["result"] = root.GetClone()
    _GG_STATE["dv"] = _GG_STATE.get("dv", 1) + 1
    _gg_stamp(root)
    c4d.gui.StatusSetText("GlyphGrid: %(polys)s polys in %(objects)s object(s), %(islands)s islands, %(cells)s cells "
                          "(%(min)s-%(max)s per cell), solve %(t_solve)ss" % tot + (" numpy" if tot["numpy"] else ""))
    return root


# ---------------------------------------------------------------- plate swap ---
_GG_PLATE_KEYS = ("Grid (N x N)", "Grid Mode", "Plate Style", "Collection", "Custom Glyphs", "Font", "Sort by Ink",
                  "Plates Folder")


def _gg_apply_plate(force=False):
    U = {bc[c4d.DESC_NAME]: did for did, bc in op.GetUserDataContainer()
         if did[-1].dtype not in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT)}
    g = lambda k, d=None: op[U[k]] if k in U else d
    if not force and not g("Auto Swap Plate", True):
        return
    folder, _cd = plate_dirs(g("Plates Folder", "") or "")
    if not folder:
        return
    if int(g("Plate Style", 0)) == 8 and not cycle_text(op, "Collection").strip("() none"):
        return   # no collection picked yet
    try:
        path = plate_path(folder, g("Plate Style", 0), int(g("Grid (N x N)", 4)), g("Custom Glyphs", ""),
                          font_name(g("Font", None)), bool(g("Sort by Ink", True)),
                          collection=cycle_text(op, "Collection"), mixed=int(g("Grid Mode", 0)) == 1)
    except Exception as e:
        c4d.gui.StatusSetText("GlyphGrid plate: %s" % e)
        print("GlyphGrid plate:", e)
        return
    if path == _GG_STATE.get("plate_applied") and not force:
        return   # already showing this plate: no material edit
    n = swap_plate(op, path, folder)
    _GG_STATE["plate_applied"] = path
    c4d.gui.StatusSetText("GlyphGrid plate -> %s (%d texture%s)" % (path.split("/")[-1], n, "" if n == 1 else "s"))


_GG_LIMIT_KEYS = ("Quadtree Block (polys)", "Quadtree Levels")
_GG_COLOR_KEYS = ("Glyph Color", "Emission Strength", "Color Mode", "Random Amount", "Hue Spread",
                  "Random Saturation", "Brightness Jitter", "Color Seed")


def _gg_update_limits(changed=None):
    apply_slider_caps(op, changed)


def _gg_bake():
    """Editable copy of the current output (UVW + selection tags + material), generator off."""
    d = op.GetDocument()
    res = _GG_STATE.get("result")
    if d is None or res is None:
        c4d.gui.StatusSetText("GlyphGrid bake: nothing built yet")
        return
    polys = []
    o = res.GetDown()
    while o is not None:
        if o.IsInstanceOf(c4d.Opolygon):
            polys.append(o)
        o = o.GetNext()
    if not polys:
        return
    d.StartUndo()
    pred = op
    for k, po in enumerate(polys):
        cp = po.GetClone(c4d.COPYFLAGS_NO_HIERARCHY)
        cp.SetName(op.GetName() + " baked" + ("" if len(polys) == 1 else " %d" % (k + 1)))
        cp.SetMg(op.GetMg() * po.GetMl())
        cp[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = c4d.OBJECT_UNDEF
        cp[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = c4d.OBJECT_UNDEF
        t = op.GetFirstTag()
        while t is not None:   # material / texture tags of the generator follow the bake
            if t.CheckType(c4d.Ttexture):
                cp.InsertTag(t.GetClone())
            t = t.GetNext()
        d.InsertObject(cp, pred=pred)
        d.AddUndo(c4d.UNDOTYPE_NEW, cp)
        pred = cp
    d.AddUndo(c4d.UNDOTYPE_CHANGE_SMALL, op)
    op[c4d.ID_BASEOBJECT_GENERATOR_FLAG] = False
    d.EndUndo()
    d.SetActiveObject(pred)
    c4d.EventAdd()
    c4d.gui.StatusSetText("GlyphGrid: baked %d object(s) - add Cloth / Soft Body tags to the copy" % len(polys))


def _gg_write_to_source():
    msg = write_result_to_source(op, _GG_STATE.get("result"))
    if msg:
        c4d.gui.MessageDialog(msg)


def _gg_mouse_down():
    try:
        bc = c4d.BaseContainer()
        if c4d.gui.GetInputState(c4d.BFM_INPUT_MOUSE, c4d.BFM_INPUT_MOUSELEFT, bc):
            return bool(bc.GetInt32(c4d.BFM_INPUT_VALUE))
    except Exception:
        pass
    return False


def message(id, data):
    # runs on the main thread from the Attribute Manager: safe place to edit materials.
    # Scrubbing a slider sends a SETPARAMETER per step; rebuilding + swapping the plate on every
    # step (plus the generator rebuilding meanwhile) crashed C4D. So plate work is deferred while
    # the user is dragging and done ONCE when the interaction ends.
    try:
        # safety net if no INTERACTION_END arrives: the next main-thread message after the mouse
        # is released applies the pending plate
        if (_GG_STATE.get("plate_pending") or _GG_STATE.get("mat_pending")) and not _GG_STATE.get("dragging") \
                and c4d.threading.GeIsMainThread() and not _gg_mouse_down():
            if _GG_STATE.pop("plate_pending", False):
                _gg_apply_plate()
            if _GG_STATE.pop("mat_pending", False):
                sync_material_color(op)
        if id == getattr(c4d, "MSG_DESCRIPTION_USERINTERACTION_BEGIN", -1):
            _GG_STATE["dragging"] = True
        elif id == getattr(c4d, "MSG_DESCRIPTION_USERINTERACTION_END", -2):
            _GG_STATE["dragging"] = False
            if _GG_STATE.pop("plate_pending", False):
                _gg_apply_plate()
            if _GG_STATE.pop("mat_pending", False):
                sync_material_color(op)
        elif id == c4d.MSG_DESCRIPTION_COMMAND:
            did = data.get("id") if isinstance(data, dict) else None
            # NOTE: c4d.DescID is unhashable - never use it as a dict key (that silently killed
            # every button here). Match on the user-data index instead.
            hit = None
            U = {}
            for d_, bc in op.GetUserDataContainer():
                U[bc[c4d.DESC_NAME]] = d_
                if did is not None and d_[-1].id == did[-1].id and d_.GetDepth() == did.GetDepth():
                    hit = bc[c4d.DESC_NAME]
            root = op[U["Plates Folder"]] if "Plates Folder" in U else ""
            folder, cdir = plate_dirs(root)
            if hit == "Apply Plate Now":
                _gg_apply_plate(force=True)
            elif hit in ("Standard", "Redshift", "Octane"):
                build_glyph_material(op.GetDocument(), op, hit.lower())
            elif hit == "Bake to Polygon Object":
                _gg_bake()
            elif hit == "Write UVs onto Source":
                _gg_write_to_source()
            elif hit == "Open Plates Folder" and root:
                open_in_finder(root)
            elif hit == "Open Collection" and cdir:
                cname = cycle_text(op, "Collection").strip()
                sub = cdir + "/" + cname if cname and cname != "(none)" else cdir
                open_in_finder(sub)
            elif hit == "Refresh List" and cdir:
                set_cycle_items(op, "Collection", collection_names(cdir))
                c4d.gui.StatusSetText("GlyphGrid: %d collections" % len(collection_names(cdir)))
            elif hit == "New Collection" and cdir:
                path = new_collection(cdir)
                names_ = collection_names(cdir)
                set_cycle_items(op, "Collection", names_, keep=False)
                op[U["Collection"]] = names_.index(path.replace("\\", "/").split("/")[-1])
                open_in_finder(path)
        elif id == c4d.MSG_DESCRIPTION_POSTSETPARAMETER:
            did = data.get("descid") if isinstance(data, dict) else None
            if did is not None:
                for d_, bc in op.GetUserDataContainer():
                    if d_ == did and bc[c4d.DESC_NAME] in _GG_COLOR_KEYS:
                        if _GG_STATE.get("dragging") or _gg_mouse_down():
                            _GG_STATE["mat_pending"] = True      # material edit once, on release
                        else:
                            sync_material_color(op)
                        break
                for d_, bc in op.GetUserDataContainer():
                    if d_ == did and bc[c4d.DESC_NAME] in _GG_LIMIT_KEYS:
                        _gg_update_limits(bc[c4d.DESC_NAME])
                        break
                for d_, bc in op.GetUserDataContainer():
                    if d_ == did and bc[c4d.DESC_NAME] in _GG_PLATE_KEYS:
                        if bc[c4d.DESC_NAME] == "Plates Folder":   # new root -> re-read its collections
                            set_cycle_items(op, "Collection", collection_names(plate_dirs(op[d_])[1]))
                        if bc[c4d.DESC_NAME] == "Collection":   # picking a collection = use it
                            for d2, bc2 in op.GetUserDataContainer():
                                if bc2[c4d.DESC_NAME] == "Plate Style" and op[d2] != 8:
                                    op[d2] = 8
                        if _GG_STATE.get("dragging") or _gg_mouse_down():
                            _GG_STATE["plate_pending"] = True    # apply once on release
                        else:
                            _gg_apply_plate()
                        break
    except Exception as e:
        print("GlyphGrid message:", e)
    return True
'''


def _strip_doc(src):
    """Drop the module docstring so pasted sources stay readable."""
    s = src.lstrip()
    if s.startswith('"""'):
        end = s.find('"""', 3)
        s = s[end + 3:]
    return s


def generator_code():
    core = open(os.path.join(HERE, "glyphgrid_core.py")).read()
    rt = open(os.path.join(HERE, "glyphgrid_runtime.py")).read()
    head = ('"""GlyphGrid UV generator (sdimaging/cinema4d-mcp scripts/glyphgrid).\n'
            'Drop any object under this generator. Every polygon becomes a UV island in an N x N atlas.\n'
            'Core + runtime are embedded below; edit the source in the repo and rebuild."""\n')
    return head + "\n# ---- glyphgrid_core ----\n" + _strip_doc(core) + "\n# ---- glyphgrid_runtime ----\n" + \
        _strip_doc(rt) + GENERATOR_MAIN


def build_generator(doc, source=None, name="GlyphGrid", **overrides):
    gen = c4d.BaseObject(c4d.Opython)
    gen.SetName(name)
    gen[c4d.OPYTHON_CODE] = generator_code()
    gen[c4d.OPYTHON_OPTIMIZE] = False  # we cache ourselves via GetAndCheckHierarchyClone
    add_user_data(gen)
    set_params(gen, **{"Plates Folder": os.path.join(HERE, "plates")})
    set_params(gen, **overrides)
    doc.InsertObject(gen)
    if source is not None:
        if source.GetDocument() is not None:
            source.Remove()
        source.InsertUnder(gen)
    c4d.EventAdd()
    return gen


def set_params(gen, **kv):
    """set_params(gen, **{"Grid (N x N)": 4, "Seed": 3}) -- names as in UD_SPEC."""
    U = {bc[c4d.DESC_NAME]: did for did, bc in gen.GetUserDataContainer()
         if did[-1].dtype not in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT)}
    for k, v in kv.items():
        k = k.replace("_", " ")
        if k == "Collection" and isinstance(v, str):
            names_ = collection_names(os.path.join(HERE, "plates", "collections"))
            v = names_.index(v) if v in names_ else 0
        if k in U:
            gen[U[k]] = v
        else:
            raise KeyError(k)
    keys = [k.replace("_", " ") for k in kv]
    update_limits(gen, "Quadtree Levels" if "Quadtree Levels" in keys and "Quadtree Block (polys)" not in keys else None)


def _rt():
    import importlib
    import sys
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import glyphgrid_runtime
    return glyphgrid_runtime


def write_uvs_onto_source(gen):
    """Script twin of the 'Write UVs onto Source' button (uses the generator's current cache)."""
    return _rt().write_result_to_source(gen, gen.GetCache())


def update_limits(gen, changed=None):
    """Clamp Grid N / Quadtree Levels sliders to what the current plate + block size can use."""
    try:
        return _rt().apply_slider_caps(gen, changed)
    except Exception as e:
        print("GlyphGrid limits:", e)


def refresh_code(gen):
    """After editing core/runtime in the repo: re-stuff the code (gotcha #87/#98)."""
    gen[c4d.OPYTHON_CODE] = generator_code()
    gen.SetDirty(c4d.DIRTYFLAGS_DATA)


def bake_object(doc, obj, src=0, opts=None, write_id=False, cell_sel=False, **params):
    """One-shot: writes the GlyphGrid UVW tag straight onto an editable polygon object."""
    import importlib
    import sys
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import glyphgrid_core
    import glyphgrid_runtime
    importlib.reload(glyphgrid_core)
    importlib.reload(glyphgrid_runtime)
    if not obj.CheckType(c4d.Opolygon):
        raise TypeError("bake_object needs an editable polygon object (make it editable or use build_generator)")
    doc.StartUndo()
    doc.AddUndo(c4d.UNDOTYPE_CHANGE, obj)
    st = glyphgrid_runtime.apply_to_object(obj, obj.GetMg(), params, src, opts or {}, caller=obj, doc=doc,
                                           write_id=write_id, cell_sel=cell_sel)
    doc.EndUndo()
    c4d.EventAdd()
    return st


# ----------------------------------------------------------------- upgrade ---
def upgrade_generator(gen):
    """Rebuild an existing GlyphGrid's user data (new params / slider fixes) + code, keeping values."""
    old = {}
    for did, bc in gen.GetUserDataContainer():
        if did[-1].dtype in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT):
            continue
        try:
            old[bc[c4d.DESC_NAME]] = gen[did]
        except Exception:
            pass
    for did, bc in reversed(list(gen.GetUserDataContainer())):
        gen.RemoveUserData(did)
    # RemoveUserData leaves the old VALUES in the ID_USERDATA sub-container. Re-added params reuse
    # those ids, so a REAL landing on an old LONG slot fails: "__setitem__ expected int, not float".
    gen.GetDataInstance().RemoveData(c4d.ID_USERDATA)
    add_user_data(gen)
    U = {bc[c4d.DESC_NAME]: did for did, bc in gen.GetUserDataContainer()
         if did[-1].dtype not in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT)}
    for k, v in old.items():
        if k in U and v is not None:
            try:
                gen[U[k]] = v
            except Exception:
                pass
    if "Cell Shift" in old and "Glyph Offset" in U:   # renamed
        gen[U["Glyph Offset"]] = old["Cell Shift"]
    if isinstance(old.get("Font"), str) and "Font" in U:
        gen[U["Font"]] = font_data(old["Font"])
    if old.get("Plate Folder") and "Plates Folder" in U:   # old: .../plates/library -> new: .../plates
        pf = old["Plate Folder"].rstrip("/")
        gen[U["Plates Folder"]] = os.path.dirname(pf) if os.path.basename(pf) == "library" else pf
    if isinstance(old.get("Collection"), str) and "Collection" in U:   # old text field -> dropdown
        names_ = collection_names(os.path.join(HERE, "plates", "collections"))
        if old["Collection"] in names_:
            gen[U["Collection"]] = names_.index(old["Collection"])
    update_limits(gen)
    refresh_code(gen)
    c4d.EventAdd()
    return gen


# ---------------------------------------------------------------- look-dev ---
# The material builders live in glyphgrid_runtime (embedded in the generator, so its Standard /
# Redshift / Octane buttons work in any .c4d without this repo). Thin wrappers for scripts:
def build_plate_material(*a, **k):
    return _rt().build_plate_material(*a, **k)


def build_plate_material_octane(*a, **k):
    return _rt().build_plate_material_octane(*a, **k)


def build_plate_material_standard(*a, **k):
    return _rt().build_plate_material_standard(*a, **k)


def build_glyph_material(doc, gen, renderer="redshift"):
    """What the Color tab's Standard / Redshift / Octane buttons do."""
    return _rt().build_glyph_material(doc, gen, renderer)


def enable_glyph_color(mat):
    return _rt().enable_glyph_color(mat)


def sync_material_color(gen):
    return _rt().sync_material_color(gen)


def set_plate(mat, plate_path):
    return _rt().set_plate(mat, plate_path)
