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
# (name, kind, default, extra)   kind: group/int/float/pct/deg/bool/cycle/string/link/fields/dist
UD_SPEC = [
    ("Grid", "group", None, {}),
    ("Source Object", "link", None, dict(tip="optional: use this object instead of the first child")),
    ("Grid (N x N)", "int", 4, dict(min=1, max=8, tip="1=1up 2=4up 3=9up 4=16up")),
    ("Seed", "int", 12345, dict(min=0, max=999999)),
    ("Cell Shift", "int", 0, dict(min=-1000, max=1000, tip="animate: every polygon steps to the next cell")),

    ("Islands & Fit", "group", None, {}),
    ("Island Mode", "cycle", 1, dict(items=["Polygon", "Ngon (keep ngons whole)", "Cluster (glyph spans polys)"])),
    ("Cluster Size", "dist", 50.0, dict(min=0.0)),
    ("Fit", "cycle", 0, dict(items=["Auto: flush squares, centre the rest", "Uniform + Centred (all)", "Flush (all quads)"])),
    ("Flush Aspect Limit", "float", 1.35, dict(min=1.0, max=4.0, step=0.05)),
    ("Gutter", "pct", 0.02, dict(min=0.0, max=0.45)),
    ("Glyph Scale", "pct", 1.0, dict(min=0.0, max=2.0)),

    ("Orientation", "group", None, {}),
    ("Orient", "cycle", 0, dict(items=["Up Axis (upright)", "Random 90 deg", "Random Free", "Edge Flow"])),
    ("Up Axis", "cycle", 0, dict(items=["+Y", "+Z", "+X", "-Y"])),
    ("Up Space", "cycle", 0, dict(items=["World", "Object"])),
    ("Rotation Jitter", "deg", 0.0, dict(min=0.0, max=180.0)),
    ("Random Mirror", "bool", False, {}),
    ("Flip Mirror", "bool", False, {}),

    ("Distribution", "group", None, {}),
    ("Distribute", "cycle", 0, dict(items=["Even Random (exact counts)", "Weighted Random", "Value", "Value Equalized (exact counts)"])),
    ("Weights", "string", "", dict(tip="comma list, one per cell, e.g. 8,1,1,1")),
    ("Value Source", "cycle", 0, dict(items=["Random", "Vertex Map", "Field", "Height (Up Axis)", "Light / Lambert",
                                             "Camera Facing", "Curvature", "Polygon Index", "Distance"])),
    ("Vertex Map Name", "string", "", {}),
    ("Fields", "fields", None, {}),
    ("Target Object", "link", None, dict(tip="light / camera / distance origin")),
    ("Directional Light", "bool", False, {}),
    ("Light Wrap", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Distance Period", "dist", 0.0, dict(min=0.0)),
    ("Curvature Scale", "float", 4.0, dict(min=0.0, max=100.0)),
    ("Invert", "bool", False, {}),
    ("Gamma", "float", 1.0, dict(min=0.05, max=10.0, step=0.05)),
    ("Contrast", "float", 1.0, dict(min=0.0, max=10.0, step=0.05)),
    ("Offset", "float", 0.0, dict(min=-10.0, max=10.0, step=0.01)),
    ("Wrap Values", "bool", False, dict(tip="with Offset animated, glyphs cycle across the surface")),
    ("Random Mix", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Dither", "pct", 0.0, dict(min=0.0, max=1.0)),
    ("Rebuild Every Frame", "bool", False, dict(tip="on for animated fields / lights / offsets")),

    ("Output", "group", None, {}),
    ("UV Mode", "cycle", 0, dict(items=["Atlas (grid baked in UVs)", "Encoded (for glyphgrid_atlas.osl)"])),
    ("Write ID Tag", "bool", False, dict(tip="2nd UVW tag: (value, random) per polygon")),
    ("Cell Selection Tags", "bool", False, dict(tip="one polygon selection per cell")),
    ("Keep Source UVs", "bool", False, {}),
    ("Merge Objects", "bool", True, dict(tip="one mesh = one global even distribution (clones, hierarchies)")),
]


def _add_ud(op, name, kind, default, extra, parent):
    if kind == "group":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_GROUP)
        bc[c4d.DESC_COLUMNS] = 1
        bc[c4d.DESC_DEFAULT] = 1  # open
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
    if default is not None and kind != "group":
        op[did] = default
    return did


def add_user_data(op):
    parent = None
    for name, kind, default, extra in UD_SPEC:
        did = _add_ud(op, name, kind, default, extra, None if kind == "group" else parent)
        if kind == "group":
            parent = did


# ------------------------------------------------------------- generator code ---
GENERATOR_MAIN = r'''
# ======================================================= GlyphGrid generator ===
_GG_STATE = {"frame": None, "stats": None}


def _gg_params():
    U = {bc[c4d.DESC_NAME]: did for did, bc in op.GetUserDataContainer()}

    def g(k, d=None):
        try:
            return op[U[k]] if k in U else d
        except Exception:
            return d
    w = [float(x) for x in str(g("Weights", "") or "").replace(";", ",").replace(" ", ",").split(",") if x.strip()]
    params = dict(
        grid=int(g("Grid (N x N)", 4)), seed=int(g("Seed", 12345)), cell_shift=int(g("Cell Shift", 0)),
        island=int(g("Island Mode", 1)), cluster_size=float(g("Cluster Size", 50.0)),
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
    U = {bc[c4d.DESC_NAME]: did for did, bc in gen.GetUserDataContainer()}
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
        tex = g.GetViewRoot().FindChild(maxon.Id("gg_plate"))
        tex.GetInputs().FindChild(maxon.InternedId(P + "texturesampler.tex0")).FindChild(
            maxon.InternedId("path")).SetPortValue(maxon.Url(plate_path))
        tx.Commit()


def upgrade_generator(gen):
    """Rebuild an existing GlyphGrid's user data (new params / slider fixes) + code, keeping values."""
    old = {}
    for did, bc in gen.GetUserDataContainer():
        if did[-1].dtype == c4d.DTYPE_GROUP:
            continue
        try:
            old[bc[c4d.DESC_NAME]] = gen[did]
        except Exception:
            pass
    for did, bc in reversed(list(gen.GetUserDataContainer())):
        gen.RemoveUserData(did)
    add_user_data(gen)
    U = {bc[c4d.DESC_NAME]: did for did, bc in gen.GetUserDataContainer()}
    for k, v in old.items():
        if k in U and v is not None:
            try:
                gen[U[k]] = v
            except Exception:
                pass
    refresh_code(gen)
    c4d.EventAdd()
    return gen
