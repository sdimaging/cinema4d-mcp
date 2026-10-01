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

# ------------------------------------------------------------ user data spec ---
# (name, kind, default, extra)
#   kind: tab | help | int/float/pct/deg/bool/cycle/string/link/fields/dist
#   "tab" = its own Attribute Manager tab, "help" = collapsed "How this tab works" group of text lines
HELP = {
    "Grid": [
        "GRID: how many glyph cells and which polygon gets which.",
        "Grid (N x N): 1 = 1-up (one glyph everywhere), 2 = 4-up,",
        "  3 = 9-up, 4 = 16-up ... must match the plate you use.",
        "Seed: re-deals which polygon gets which cell.",
        "Cell Shift: every polygon moves N cells forward.",
        "  Animate it for a global glyph cycle.",
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
        "  Levels = max number of block sizes, Subdivide Chance =",
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
        "Random Mix: 0 = pure value, 1 = pure random.",
        "Dither: jitter before choosing -> breaks hard bands of",
        "  one glyph into a smooth mix (try 0.3 - 0.6).",
        "Rebuild Every Frame: ON when lights, fields or Offset",
        "  are animated.",
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
        "Collection: folder name inside plates/collections (e.g.",
        "  shapes) or a full path. Images sorted by filename = priority:",
        "  1-up uses #1, 4-up #1-4, 9-up #1-9, 16-up #1-16; fewer images",
        "  repeat. Hand-made name_4up.png files are used as they are.",
        "Custom: type your own characters in Custom Glyphs,",
        "  e.g. SDIMAGING or 0123456789 or .:-=+*#%@",
        "  Sort by Ink orders them sparse -> dense (for Value).",
        "  Font = PostScript name, e.g. Menlo-Bold, Courier,",
        "  HelveticaNeue-Bold, SFMono-Heavy.",
        "Apply Plate Now: force the swap (e.g. after editing text).",
        "Plate Folder: where the library lives (and where custom",
        "  plates are written).",
        "Only textures that point at plate files (or the node",
        "  named gg_plate) are swapped - other maps are untouched.",
    ],
}

UD_SPEC = [
    ("Grid", "tab", None, {}),
    ("Grid", "help", None, {}),
    ("Grid (N x N)", "int", 4, dict(min=1, max=8)),
    ("Seed", "int", 12345, dict(min=0, max=999999)),
    ("Cell Shift", "int", 0, dict(min=-1000, max=1000)),
    ("Grid Mode", "cycle", 0, dict(items=["Single (Grid N x N)", "Mixed 1/4/9/16-up (composite plate)"])),
    ("Level Weights", "string", "1,1,1,1", {}),
    ("Source Object", "link", None, {}),

    ("Islands", "tab", None, {}),
    ("Islands", "help", None, {}),
    ("Island Mode", "cycle", 1, dict(items=["Polygon", "Ngon (keep ngons whole)", "Cluster (glyph spans polys)",
                                            "Quadtree (mixed glyph sizes)"])),
    ("Cluster Size", "dist", 50.0, dict(min=0.0)),
    ("Quadtree Block (polys)", "cycle", 3, dict(items=["1", "2", "4", "8", "16", "32", "64"])),
    ("Quadtree Levels", "int", 4, dict(min=1, max=7)),
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
    ("Random Mix", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Dither", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Rebuild Every Frame", "bool", False, {}),

    ("Plate", "tab", None, {}),
    ("Plate", "help", None, {}),
    ("Plate Style", "cycle", 0, dict(items=["1 ASCII ramp", "2 Bayer dither", "3 Halftone dots", "4 Halftone squares",
                                            "5 Noise", "6 Hex digits", "7 Binary 0/1", "8 Custom (type below)",
                                            "9 Collection (folder below)"])),
    ("Collection", "string", "shapes", {}),
    ("Custom Glyphs", "string", "", {}),
    ("Font", "string", "Menlo-Bold", {}),
    ("Sort by Ink", "bool", True, {}),
    ("Auto Swap Plate", "bool", True, {}),
    ("Apply Plate Now", "button", None, {}),
    ("Plate Folder", "string", "", {}),

    ("Output", "tab", None, {}),
    ("Output", "help", None, {}),
    ("UV Mode", "cycle", 0, dict(items=["Atlas (grid baked in UVs)", "Encoded (experimental, OSL)"])),
    ("Write ID Tag", "bool", False, {}),
    ("Cell Selection Tags", "bool", False, {}),
    ("Keep Source UVs", "bool", False, {}),
    ("Merge Objects", "bool", True, {}),
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
        for i, s in enumerate(extra["items"]):
            cyc.SetString(i, s)
        bc[c4d.DESC_CYCLE] = cyc
    elif kind == "string":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STRING)
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
    if default is not None and kind not in ("group", "button"):
        op[did] = default
    return did


def add_user_data(op):
    parent = None
    for name, kind, default, extra in UD_SPEC:
        if kind == "help":
            _add_help(op, name, parent)
            continue
        did = _add_ud(op, name, kind, default, extra, None if kind in ("group", "tab") else parent)
        if kind in ("group", "tab"):
            parent = did


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
        grid=int(g("Grid (N x N)", 4)), seed=int(g("Seed", 12345)), cell_shift=int(g("Cell Shift", 0)),
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
        uv_mode=int(g("UV Mode", 0)),
    )
    src = int(g("Value Source", 0))
    opts = dict(vmap_name=g("Vertex Map Name", ""), fields=g("Fields", None), target=g("Target Object", None),
                light_directional=bool(g("Directional Light", False)), wrap_light=float(g("Light Wrap", 0.0)),
                period=float(g("Distance Period", 0.0)), curv_scale=float(g("Curvature Scale", 4.0)),
                height_axis=UP_AXES[int(g("Up Axis", 0))])
    flags = dict(animate=bool(g("Rebuild Every Frame", False)), write_id=bool(g("Write ID Tag", False)),
                 cell_sel=bool(g("Cell Selection Tags", False)), keep=bool(g("Keep Source UVs", False)),
                 merge=bool(g("Merge Objects", True)), source=g("Source Object", None))
    return params, src, opts, flags


def _count_polys(o):
    n = 0
    while o is not None:
        if o.IsInstanceOf(c4d.Opolygon):
            n += o.GetPolygonCount()
        n += _count_polys(o.GetDown())
        o = o.GetNext()
    return n


def main():
    params, src, opts, flags = _gg_params()
    link = flags.get("source")
    child_mode = link is None
    src_obj = op.GetDown() if child_mode else link
    if src_obj is None:
        return None
    frame = doc.GetTime().GetFrame(doc.GetFps())
    tgt = opts.get("target")
    fl = opts.get("fields")
    gmg = op.GetMg()
    inv = ~gmg
    CF = c4d.COPYFLAGS_NO_HIERARCHY | c4d.COPYFLAGS_NO_ANIMATION | c4d.COPYFLAGS_NO_BITS
    own_sig = (op.GetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_MATRIX),
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
                _GG_STATE["geo"] = [(o.GetClone(CF), o.GetMg()) for o, _ in collect_polys(cl)]
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
            if found:
                _GG_STATE["geo"] = found
                _GG_STATE["src_sig"] = ssig
                geo_changed = True

    keep = _GG_STATE.get("result")
    # NOTE: never return op.GetCache() from a Python Generator (C4D frees it right after: "object is
    # not alive"). We return a clone of our own copy; polygon data is copy-on-write, ~0 ms.
    if not geo_changed and keep is not None and own_sig == _GG_STATE.get("own_sig"):
        return keep.GetClone()
    geo = _GG_STATE.get("geo")
    if not geo:
        return None
    _GG_STATE["own_sig"] = own_sig
    _GG_STATE["frame"] = frame
    parts = [(o.GetClone(CF), gmg * ml) for o, ml in geo]   # (clone, world matrix)

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
    _GG_STATE["result"] = root.GetClone()
    c4d.gui.StatusSetText("GlyphGrid: %(polys)s polys in %(objects)s object(s), %(islands)s islands, %(cells)s cells "
                          "(%(min)s-%(max)s per cell), solve %(t_solve)ss" % tot + (" numpy" if tot["numpy"] else ""))
    return root


# ---------------------------------------------------------------- plate swap ---
_GG_PLATE_KEYS = ("Grid (N x N)", "Grid Mode", "Plate Style", "Collection", "Custom Glyphs", "Font", "Sort by Ink",
                  "Plate Folder")


def _gg_apply_plate(force=False):
    U = {bc[c4d.DESC_NAME]: did for did, bc in op.GetUserDataContainer()
         if did[-1].dtype not in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT)}
    g = lambda k, d=None: op[U[k]] if k in U else d
    if not force and not g("Auto Swap Plate", True):
        return
    folder = g("Plate Folder", "") or ""
    if not folder:
        print("GlyphGrid: set Plate Folder first")
        return
    try:
        path = plate_path(folder, g("Plate Style", 0), int(g("Grid (N x N)", 4)), g("Custom Glyphs", ""),
                          g("Font", "Menlo-Bold") or "Menlo-Bold", bool(g("Sort by Ink", True)),
                          collection=g("Collection", "") or "", mixed=int(g("Grid Mode", 0)) == 1)
    except Exception as e:
        c4d.gui.StatusSetText("GlyphGrid plate: %s" % e)
        print("GlyphGrid plate:", e)
        return
    n = swap_plate(op, path, folder)
    c4d.gui.StatusSetText("GlyphGrid plate -> %s (%d texture%s)" % (path.split("/")[-1], n, "" if n == 1 else "s"))


def message(id, data):
    # runs on the main thread from the Attribute Manager: safe place to edit materials
    try:
        if id == c4d.MSG_DESCRIPTION_COMMAND:
            did = data.get("id") if isinstance(data, dict) else None
            for d_, bc in op.GetUserDataContainer():
                if bc[c4d.DESC_NAME] == "Apply Plate Now" and did is not None and did == d_:
                    _gg_apply_plate(force=True)
        elif id == c4d.MSG_DESCRIPTION_POSTSETPARAMETER:
            did = data.get("descid") if isinstance(data, dict) else None
            if did is not None:
                for d_, bc in op.GetUserDataContainer():
                    if d_ == did and bc[c4d.DESC_NAME] in _GG_PLATE_KEYS:
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
    set_params(gen, **{"Plate Folder": os.path.join(HERE, "plates", "library")})
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
        if k in U:
            gen[U[k]] = v
        else:
            raise KeyError(k)


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


# ---------------------------------------------------------------- look-dev ---
def build_plate_material(doc, target, plate_path, name="GlyphGrid Plate", mode="emissive",
                         color=(1.0, 1.0, 1.0), emission=1.0, base_tint=0.0):
    """Redshift node material driven by a plate. Assigned to `target` with UVW projection
    (uses the FIRST UVW tag = GlyphGrid UV).

    mode
      "emissive"  plate -> emission (glyphs glow on a black body)
      "diffuse"   plate -> base colour (lit glyphs)
      "hologram"  plate -> OPACITY (black knocked out), constant `color` emission + base colour:
                  floating characters, see-through shapes built from glyphs
    """
    import maxon
    RS = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")
    P = "com.redshift3d.redshift4c4d.nodes.core."
    mat = c4d.BaseMaterial(c4d.Mmaterial)
    mat.SetName(name)
    doc.InsertMaterial(mat)
    nm = mat.GetNodeMaterialReference()
    g = nm.CreateDefaultGraph(RS) if not nm.HasSpace(RS) else nm.GetGraph(RS)
    root = g.GetViewRoot()
    col = maxon.Color64(*color)
    with g.BeginTransaction() as tx:
        surf = None
        for c in root.GetChildren():
            if "material" in str(c.GetId()) and "output" not in str(c.GetId()):
                surf = c
        kind = str(surf.GetId()).split("@")[0]
        tex = g.AddChild(maxon.Id("gg_plate"), maxon.Id(P + "texturesampler"))
        t0 = tex.GetInputs().FindChild(maxon.InternedId(P + "texturesampler.tex0"))
        t0.FindChild(maxon.InternedId("path")).SetPortValue(maxon.Url(plate_path))
        out = tex.GetOutputs().FindChild(maxon.InternedId(P + "texturesampler.outcolor"))
        si = surf.GetInputs()

        def port(n):
            return si.FindChild(maxon.InternedId(P + kind + "." + n))

        def setp(n, v):
            try:
                port(n).SetPortValue(v)
            except Exception:
                pass
        if mode == "diffuse":
            out.Connect(port("base_color"))
        elif mode == "hologram":
            out.Connect(port("opacity_color"))
            setp("emission_color", col)
            setp("emission_weight", maxon.Float64(emission))
            setp("base_color", col)
            setp("base_color_weight", maxon.Float64(0.2))
            setp("refl_weight", maxon.Float64(0.0))
        else:  # emissive
            out.Connect(port("emission_color"))
            setp("emission_weight", maxon.Float64(emission))
            setp("base_color", maxon.Color64(base_tint, base_tint, base_tint))
            setp("refl_weight", maxon.Float64(0.0))
        tx.Commit()
    tag = target.GetTag(c4d.Ttexture) or target.MakeTag(c4d.Ttexture)
    tag[c4d.TEXTURETAG_MATERIAL] = mat
    tag[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_UVW
    c4d.EventAdd()
    return mat


def set_plate(mat, plate_path):
    """Swap the plate image on a material made by build_plate_material."""
    import maxon
    RS = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")
    P = "com.redshift3d.redshift4c4d.nodes.core."
    g = mat.GetNodeMaterialReference().GetGraph(RS)
    with g.BeginTransaction() as tx:
        for nd in g.GetViewRoot().GetChildren():      # FindChild(maxon.Id) does not take node ids
            if str(nd.GetId()).startswith("gg_plate"):
                nd.GetInputs().FindChild(maxon.InternedId(P + "texturesampler.tex0")).FindChild(
                    maxon.InternedId("path")).SetPortValue(maxon.Url(plate_path))
        tx.Commit()

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
    refresh_code(gen)
    c4d.EventAdd()
    return gen


def build_plate_material_octane(doc, target, plate_path, name="GlyphGrid Plate (Octane)", mode="hologram",
                                color=(0.15, 1.0, 0.35), emission=5.0, base=0.05):
    """Octane (C4D) version of build_plate_material: Octane Diffuse material + ImageTexture.
      hologram: plate -> opacity (black knocked out), constant colour TextureEmission
      emissive: plate x colour -> TextureEmission, black body
      diffuse:  plate x colour -> diffuse
    swap_plate() re-points the ImageTexture (IMAGETEXTURE_FILE) like the Redshift sampler."""
    OCT_MAT, IMG, RGB, MUL, TEXEM = 1029501, 1029508, 1029504, 1029516, 1029642
    mat = c4d.BaseMaterial(OCT_MAT)
    mat.SetName(name)
    mat[c4d.OCT_MATERIAL_TYPE] = getattr(c4d, "OCT_MAT_TYPE_DIFFUSE", 2510)
    doc.InsertMaterial(mat)

    def shader(tid, **params):
        sh = c4d.BaseShader(tid)
        for k, v in params.items():
            sh[getattr(c4d, k)] = v
        mat.InsertShader(sh)
        return sh
    tex = shader(IMG, IMAGETEXTURE_FILE=plate_path)
    tex.SetName("gg_plate")
    col = shader(RGB, RGBSPECTRUMSHADER_COLOR=c4d.Vector(*color))
    if mode == "hologram":
        mat[c4d.OCT_MATERIAL_OPACITY_LINK] = tex
        mat[c4d.OCT_MATERIAL_DIFFUSE_COLOR] = c4d.Vector(*color) * base
        em = shader(TEXEM, TEXEMISSION_POWER=emission)
        em[c4d.TEXEMISSION_EFFIC_OR_TEX] = col
        mat[c4d.OCT_MATERIAL_EMISSION] = em
    elif mode == "emissive":
        mul = shader(MUL)
        mul[c4d.MULTIPLY_TEXTURE1], mul[c4d.MULTIPLY_TEXTURE2] = tex, col
        em = shader(TEXEM, TEXEMISSION_POWER=emission)
        em[c4d.TEXEMISSION_EFFIC_OR_TEX] = mul
        mat[c4d.OCT_MATERIAL_EMISSION] = em
        mat[c4d.OCT_MATERIAL_DIFFUSE_COLOR] = c4d.Vector(0)
    else:
        mul = shader(MUL)
        mul[c4d.MULTIPLY_TEXTURE1], mul[c4d.MULTIPLY_TEXTURE2] = tex, col
        mat[c4d.OCT_MATERIAL_DIFFUSE_LINK] = mul
    mat.Message(c4d.MSG_UPDATE)
    tag = target.MakeTag(c4d.Ttexture)
    tag[c4d.TEXTURETAG_MATERIAL] = mat
    tag[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_UVW
    c4d.EventAdd()
    return mat
