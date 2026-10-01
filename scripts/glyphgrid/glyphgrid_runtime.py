"""
GlyphGrid runtime: the Cinema 4D side (mesh in -> UVW tag out).

Used two ways
  * embedded into the GlyphGrid Python Generator by glyphgrid_c4d.build_generator()
  * imported by glyphgrid_c4d.bake_object() for a one-shot bake on an editable mesh

Everything here is plain c4d + glyphgrid_core; numpy is used only if present.
"""
import math
import random
import time

import c4d

try:  # module mode
    from glyphgrid_core import compute, _np, DEFAULTS  # noqa: F401
except Exception:  # embedded mode: core source was pasted above this file
    pass

SRC_RANDOM, SRC_VMAP, SRC_FIELD, SRC_HEIGHT, SRC_LIGHT, SRC_CAMERA, SRC_CURV, SRC_INDEX, SRC_DIST = range(9)
UP_AXES = [(0, 1, 0), (0, 0, 1), (1, 0, 0), (0, -1, 0)]
TAG_NAME = "GlyphGrid UV"
ID_TAG_NAME = "GlyphGrid ID"
GRID_MAX = 4            # 1/4/9/16-up: the plate library's four levels
QT_LEVELS_MAX = 4       # glyph sizes in a quadtree: block, /2, /4, /8


def qt_level_cap(qt_cells):
    """Quadtree Levels that a block of qt_cells polygons can really split into (8 -> 8,4,2,1 = 4)."""
    blk = 1 << max(0, int(round(math.log(max(1, int(qt_cells)), 2))))
    return int(round(math.log(blk, 2))) + 1


# -------------------------------------------------------------- mesh read ---
def read_mesh(po):
    pts = po.GetAllPoints()
    polys = po.GetAllPolygons()
    P = [(p.x, p.y, p.z) for p in pts]
    F = [(q.a, q.b, q.c, q.d) for q in polys]
    tm = po.GetPolygonTranslationMap()
    ngon_map = tm[1] if tm and len(tm) > 1 and len(tm[1]) == len(F) else None
    return P, F, ngon_map


def _vec_t(v):
    return (v.x, v.y, v.z)


def _centroids(P, F):
    out = []
    for a, b, c, d in F:
        if c == d:
            out.append(((P[a][0] + P[b][0] + P[c][0]) / 3.0, (P[a][1] + P[b][1] + P[c][1]) / 3.0,
                        (P[a][2] + P[b][2] + P[c][2]) / 3.0))
        else:
            out.append(((P[a][0] + P[b][0] + P[c][0] + P[d][0]) * 0.25, (P[a][1] + P[b][1] + P[c][1] + P[d][1]) * 0.25,
                        (P[a][2] + P[b][2] + P[c][2] + P[d][2]) * 0.25))
    return out


def _normals(P, F):
    out = []
    for a, b, c, d in F:
        A, B, C, D = P[a], P[b], P[c], P[d]
        e1 = (C[0] - A[0], C[1] - A[1], C[2] - A[2])
        e2 = (D[0] - B[0], D[1] - B[1], D[2] - B[2])
        n = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
        l = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
        out.append((n[0] / l, n[1] / l, n[2] / l))
    return out


def _normalise(vals):
    if not vals:
        return vals
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [0.5] * len(vals)
    k = 1.0 / (hi - lo)
    return [(v - lo) * k for v in vals]


# ---------------------------------------------------------- value sources ---
def sample_values(po, mg, P, F, src, opts, caller=None, doc=None):
    """Per-polygon value 0..1 (or None for the pure-random source)."""
    if src == SRC_RANDOM:
        return None
    n = len(F)
    if src == SRC_INDEX:
        return [i / max(1, n - 1) for i in range(n)]
    if src == SRC_VMAP:
        name = (opts.get("vmap_name") or "").strip()
        tag = None
        t = po.GetFirstTag()
        while t:
            if t.CheckType(c4d.Tvertexmap) and (not name or t.GetName() == name):
                tag = t
                break
            t = t.GetNext()
        if tag is None:
            return [0.0] * n
        w = tag.GetAllHighlevelData()
        return [((w[a] + w[b] + w[c]) / 3.0) if c == d else (w[a] + w[b] + w[c] + w[d]) * 0.25 for a, b, c, d in F]

    cen = _centroids(P, F)
    wc = [mg * c4d.Vector(*c) for c in cen]
    if src == SRC_FIELD:
        fl = opts.get("fields")
        if fl is None or not fl.HasContent():
            return [0.0] * n
        fi = c4d.modules.mograph.FieldInput(wc, n)
        out = fl.SampleListSimple(caller, fi, c4d.FIELDSAMPLE_FLAG_VALUE)
        return [max(0.0, min(1.0, out.GetValue(i))) for i in range(n)]
    if src == SRC_HEIGHT:
        ax = c4d.Vector(*opts.get("height_axis", (0, 1, 0)))
        return _normalise([p.Dot(ax) for p in wc])
    if src == SRC_DIST:
        tgt = opts.get("target")
        org = tgt.GetMg().off if tgt else mg.off
        d = [(p - org).GetLength() for p in wc]
        per = opts.get("period", 0.0)
        if per > 0:
            return [x / per - math.floor(x / per) for x in d]
        return _normalise(d)

    nrm = _normals(P, F)
    rot = c4d.Matrix(c4d.Vector(0), mg.v1, mg.v2, mg.v3)
    wn = [(rot * c4d.Vector(*v)).GetNormalized() for v in nrm]
    if src == SRC_LIGHT:
        tgt = opts.get("target")
        wrap = opts.get("wrap_light", 0.0)
        if tgt is None:
            L = c4d.Vector(-0.5, 0.8, -0.6).GetNormalized()
            dots = [nn.Dot(L) for nn in wn]
        elif opts.get("light_directional", False):
            L = (-tgt.GetMg().v3).GetNormalized()  # object's -Z = light travel dir reversed
            dots = [nn.Dot(L) for nn in wn]
        else:
            lp = tgt.GetMg().off
            dots = [nn.Dot((lp - p).GetNormalized()) for nn, p in zip(wn, wc)]
        return [max(0.0, min(1.0, (dt + wrap) / (1.0 + wrap))) for dt in dots]
    if src == SRC_CAMERA:
        cam = opts.get("target")
        if cam is None and doc is not None:
            bd = doc.GetActiveBaseDraw()
            cam = bd.GetSceneCamera(doc) if bd else None
        cp = cam.GetMg().off if cam else c4d.Vector(0, 0, -1000)
        return [max(0.0, nn.Dot((cp - p).GetNormalized())) for nn, p in zip(wn, wc)]
    if src == SRC_CURV:
        # convexity: how far the averaged vertex normals lean away from the face normal
        vn = [[0.0, 0.0, 0.0] for _ in P]
        for (a, b, c, d), nn in zip(F, nrm):
            for j in ((a, b, c) if c == d else (a, b, c, d)):
                v = vn[j]
                v[0] += nn[0]; v[1] += nn[1]; v[2] += nn[2]
        vals = []
        for (a, b, c, d), nn, ce in zip(F, nrm, cen):
            acc, k = 0.0, 0
            for j in ((a, b, c) if c == d else (a, b, c, d)):
                v = vn[j]
                l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
                r = (P[j][0] - ce[0], P[j][1] - ce[1], P[j][2] - ce[2])
                rl = math.sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]) or 1.0
                acc += ((v[0] / l - nn[0]) * r[0] + (v[1] / l - nn[1]) * r[1] + (v[2] / l - nn[2]) * r[2]) / rl
                k += 1
            vals.append(acc / k)
        sc = opts.get("curv_scale", 4.0)
        return [max(0.0, min(1.0, 0.5 + v * sc)) for v in vals]
    return None


# ------------------------------------------------------------- tag write ---
_LSB_TABLE = bytes(i & 0xFE for i in range(256))


def _uv_bytes(uv):
    """8 floats/poly -> raw bytes, with the lowest mantissa bit cleared.

    GOTCHA (C4D 2026): UVWTag raw data is 4 x (float32 u, float32 v) per polygon, and
    C4D uses the LSB of each float as an internal flag. Raw writes with that bit set
    (0.1f, 0.2f ...) decode as garbage. SetSlow() clears it, so must we.
    """
    if _np is not None and hasattr(uv, "dtype"):
        a = _np.ascontiguousarray(uv, dtype=_np.float32).view(_np.uint32) & _np.uint32(0xFFFFFFFE)
        return a.tobytes()
    import array
    buf = bytearray(array.array("f", uv).tobytes())
    if __import__("sys").byteorder == "little":
        buf[0::4] = bytes(buf[0::4]).translate(_LSB_TABLE)
    else:
        buf[3::4] = bytes(buf[3::4]).translate(_LSB_TABLE)
    return bytes(buf)


def write_uvw(po, uv, name=TAG_NAME, first=True):
    n = po.GetPolygonCount()
    tag = None
    t = po.GetFirstTag()
    while t:
        if t.CheckType(c4d.Tuvw) and t.GetName() == name:
            tag = t
            break
        t = t.GetNext()
    if tag is None or tag.GetDataCount() != n:
        if tag:
            tag.Remove()
        tag = c4d.UVWTag(n)
        tag.SetName(name)
        if first:
            # material tags without an explicit UV tag use the FIRST UVW tag
            po.InsertTag(tag)
        else:
            last = None
            t = po.GetFirstTag()
            while t:
                if t.CheckType(c4d.Tuvw):
                    last = t
                t = t.GetNext()
            po.InsertTag(tag, last)
    raw = _uv_bytes(uv)
    mv = tag.GetLowlevelDataAddressW()
    if len(mv) != len(raw):
        raise RuntimeError("UVW layout mismatch: tag %d bytes, data %d bytes" % (len(mv), len(raw)))
    mv[:] = raw
    tag.Message(c4d.MSG_UPDATE)
    return tag


COLOR_TAG_NAME = "GlyphGrid Color"
COLOR_UNIFORM, COLOR_CELL, COLOR_GLYPH = range(3)


def _hsv_to_rgb(h, s, v):
    import colorsys
    return colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, v))


def glyph_colors(res, color):
    """Per-polygon RGB from the Color tab.
    color = dict(mode, base=(r,g,b), amount, hue_spread, saturation, brightness, seed)
      Uniform        every glyph = base
      Random / Cell  one colour per plate cell (every 'A' the same colour)
      Random / Glyph one colour per glyph (island)
    amount 0 = all base colour, 1 = full random; hue_spread = how far round the colour wheel
    from the base hue the randoms may go (1 = any hue)."""
    import colorsys
    mode = int(color.get("mode", COLOR_UNIFORM))
    base = tuple(float(x) for x in color.get("base", (1.0, 1.0, 1.0)))
    n = len(res["cell"])
    if mode == COLOR_UNIFORM:
        return [base] * n
    keys = res["cell"] if mode == COLOR_CELL else res["island"]
    amt = max(0.0, min(1.0, float(color.get("amount", 0.35))))
    spread = max(0.0, min(1.0, float(color.get("hue_spread", 1.0))))
    sat = float(color.get("saturation", 0.8))
    bj = max(0.0, min(1.0, float(color.get("brightness", 0.0))))
    seed = int(color.get("seed", 0))
    bh, bs, bv = colorsys.rgb_to_hsv(*[max(0.0, min(1.0, c)) for c in base])
    vmax = max(base) if max(base) > 0 else 1.0
    cache = {}
    out = []
    for k in keys:
        c = cache.get(k)
        if c is None:
            r = random.Random((int(k) + 1) * 2654435761 + seed * 97531)
            h = bh + (r.random() - 0.5) * spread if bs > 0.05 else r.random()
            v = vmax * (1.0 - bj * r.random())
            rc = _hsv_to_rgb(h, sat, v)
            c = tuple(b_ + (x - b_) * amt for b_, x in zip(base, rc))
            cache[k] = c
        out.append(c)
    return out


def write_color_tag(po, cols, name=COLOR_TAG_NAME):
    """Vertex Color tag in polygon mode (4 x float32 RGBA per polygon). Redshift reads it with a
    Vertex Attribute node, Octane with an Attribute Texture, both by the tag name."""
    import struct
    n = po.GetPolygonCount()
    tag = None
    t = po.GetFirstTag()
    while t:
        if t.CheckType(c4d.Tvertexcolor) and t.GetName() == name:
            tag = t
            break
        t = t.GetNext()
    if cols is None:
        if tag:
            tag.Remove()
        return None
    if tag is None or tag.GetDataCount() != n or tag.IsPerPointColor():
        if tag:
            tag.Remove()
        tag = c4d.VertexColorTag(n)
        tag.SetName(name)
        po.InsertTag(tag)
        tag.SetPerPointMode(False)
    if _np is not None:
        a = _np.ones((n, 4, 4), dtype=_np.float32)
        a[:, :, :3] = _np.asarray(cols, dtype=_np.float32)[:, None, :]
        raw = a.tobytes()
    else:
        raw = b"".join(struct.pack("16f", *((c[0], c[1], c[2], 1.0) * 4)) for c in cols)
    w = tag.GetLowlevelDataAddressW()
    if w is not None and len(w) == len(raw):
        w[:] = raw
    return tag


# simulation / expression tags that must never ride along onto GlyphGrid's OUTPUT: a Cloth tag
# copied onto the output made the simulation run on it too (two sims fighting = flicker)
_SIM_TAGS = {100004020, 100004021, 100004022, 1018068, 1018074, 180000102, 180000107, 1059981, 1058895}


def strip_sim_tags(o):
    t = o.GetFirstTag()
    dead = []
    while t is not None:
        if t.GetType() in _SIM_TAGS or (t.GetInfo() & c4d.TAG_EXPRESSION):
            dead.append(t)
        t = t.GetNext()
    for t in dead:
        t.Remove()
    return o


def write_id_tag(po, values, rands):
    """Second UVW tag: every corner of a polygon = (value, random). For custom shaders."""
    uv = []
    for v, r in zip(values, rands):
        uv.extend((v, r, v, r, v, r, v, r))
    return write_uvw(po, uv, ID_TAG_NAME, first=False)


def write_cell_selections(po, cells, ncell, prefix="GG_cell_"):
    t = po.GetFirstTag()
    dead = []
    while t:
        if t.CheckType(c4d.Tpolygonselection) and t.GetName().startswith(prefix):
            dead.append(t)
        t = t.GetNext()
    for t in dead:
        t.Remove()
    n = len(cells)
    for c in range(ncell):
        st = c4d.SelectionTag(c4d.Tpolygonselection)
        st.SetName("%s%02d" % (prefix, c))
        st.GetBaseSelect().SetAll([cells[i] == c for i in range(n)])
        po.InsertTag(st)


def remove_other_uvw(po, keep=(TAG_NAME, ID_TAG_NAME)):
    t = po.GetFirstTag()
    dead = []
    while t:
        if t.CheckType(c4d.Tuvw) and t.GetName() not in keep:
            dead.append(t)
        t = t.GetNext()
    for t in dead:
        t.Remove()


# ------------------------------------------------------------------ apply ---
def apply_to_object(po, mg, params, src=SRC_RANDOM, opts=None, caller=None, doc=None,
                    write_id=False, cell_sel=False, strip_uvs=True, progress=None, values="sample"):
    """Compute + write. Returns stats dict. `values`: "sample" (use src) or a precomputed list/None."""
    t0 = time.time()
    opts = opts or {}
    P, F, ngon_map = read_mesh(po)
    if not F:
        return dict(polys=0)
    prm = dict(params)
    # up vector: world axis -> object space unless 'up_space' == 'object'
    up = c4d.Vector(*prm.get("up", (0, 1, 0)))
    fb = c4d.Vector(*prm.get("fallback_up", (0, 0, 1)))
    if prm.pop("up_space_world", True):
        inv = ~c4d.Matrix(c4d.Vector(0), mg.v1, mg.v2, mg.v3)
        up, fb = (inv * up).GetNormalized(), (inv * fb).GetNormalized()
    prm["up"], prm["fallback_up"] = _vec_t(up), _vec_t(fb)
    t1 = time.time()
    vals = sample_values(po, mg, P, F, src, opts, caller, doc) if isinstance(values, str) else values
    if vals is not None and prm.get("assign", 0) == 0 and not prm.get("knockout"):
        prm["assign"] = 3  # a value source with "Even" distribution -> value-driven, still exact counts
        # (with Knockout on, Even stays random: the value then only masks where glyphs appear)
    if vals is None and prm.get("assign", 0) >= 2:
        prm["assign"] = 0
    t2 = time.time()
    res = compute(P, F, prm, ngon_map=ngon_map, poly_values=vals, progress=progress)
    t3 = time.time()
    if strip_uvs:
        remove_other_uvw(po)
    write_uvw(po, res["uv"])
    if write_id:
        write_id_tag(po, res["value"], res["rand"])
    if cell_sel:
        write_cell_selections(po, res["cell"], len(res["counts"]))
    col = opts.get("color")
    write_color_tag(po, glyph_colors(res, col) if col else None)
    t4 = time.time()
    c = res["counts"]
    return dict(polys=len(F), islands=res["islands"], cells=len(c), min=min(c), max=max(c),
                t_read=round(t1 - t0, 3), t_values=round(t2 - t1, 3), t_solve=round(t3 - t2, 3),
                t_write=round(t4 - t3, 3), numpy=_np is not None, keys=(res["cell"], res["island"]))


def iter_polys(root, parent_mg):
    """Yield (polygon object, global matrix) for a hierarchy clone."""
    stack = [(root, parent_mg)]
    while stack:
        o, pm = stack.pop()
        while o:
            mg = pm * o.GetMl()
            if o.CheckType(c4d.Opolygon):
                yield o, mg
            d = o.GetDown()
            if d:
                stack.append((d, mg))
            o = o.GetNext() if o is not root else None


# ------------------------------------------------- source collection / merge ---
DIRTY_FLAGS = c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_MATRIX | c4d.DIRTYFLAGS_CACHE


def collect_polys(op, out=None, ignore_control=True, deform=True):
    """SDK DoRecursion variant: deform cache > cache > the object itself.
    Children are visited only when the object has no cache (a generator consumes its children:
    Cloner, Symmetry, SDS ...). The control-object bit is ignored on purpose: inside a generator
    every source object and its caches carry it. Cache objects report correct GetMg() (2026.4).
    deform=False skips deform caches (= the shape before any deformer)."""
    if out is None:
        out = []
    dc = op.GetDeformCache() if deform else None
    c = op.GetCache()
    if dc is not None:
        collect_polys(dc, out, deform=deform)
    elif c is not None:
        collect_polys(c, out, deform=deform)
    elif op.IsInstanceOf(c4d.Opolygon) and op.GetPolygonCount():
        out.append((op, op.GetMg()))
    if c is None:
        ch = op.GetDown()
        while ch is not None:
            if deform or not (ch.GetInfo() & c4d.OBJECT_MODIFIER):
                collect_polys(ch, out, deform=deform)
            ch = ch.GetNext()
    return out


def root_signature(o):
    """Dirty checksum of o and its descendants (not its siblings)."""
    s = o.GetDirty(DIRTY_FLAGS) + o.GetHDirty(c4d.HDIRTYFLAGS_OBJECT_HIERARCHY)
    d = o.GetDown()
    return s + (hierarchy_signature(d) if d is not None else 0)


def hierarchy_signature(o):
    s = 0
    while o is not None:
        s += o.GetDirty(DIRTY_FLAGS) + o.GetHDirty(c4d.HDIRTYFLAGS_OBJECT_HIERARCHY)
        d = o.GetDown()
        if d is not None:
            s += hierarchy_signature(d)
        o = o.GetNext()
    return s


def touch_hierarchy(o):
    while o is not None:
        o.Touch()
        d = o.GetDown()
        if d is not None:
            touch_hierarchy(d)
        o = o.GetNext()


def merge_parts(parts, inv_out):
    """Concatenate polygon objects into one mesh in the output's local space."""
    pts, cnt_p, cnt_f = [], 0, 0
    for po, mg in parts:
        cnt_p += po.GetPointCount()
        cnt_f += po.GetPolygonCount()
    out = c4d.PolygonObject(cnt_p, cnt_f)
    allp = []
    fi, base = 0, 0
    CP = c4d.CPolygon
    for po, mg in parts:
        m = inv_out * mg
        allp.extend(m * p for p in po.GetAllPoints())
        for q in po.GetAllPolygons():
            out.SetPolygon(fi, CP(q.a + base, q.b + base, q.c + base, q.d + base))
            fi += 1
        base += po.GetPointCount()
    out.SetAllPoints(allp)
    ph = out.MakeTag(c4d.Tphong)
    ph[c4d.PHONGTAG_PHONG_ANGLELIMIT] = True
    ph[c4d.PHONGTAG_PHONG_ANGLE] = 1.0472  # 60 deg
    out.Message(c4d.MSG_UPDATE)
    return out


def fields_signature(fl, doc):
    """FieldList.GetDirty() alone misses a field OBJECT being moved/edited: add every layer's own
    dirty and its linked object's data+matrix dirty, so live field edits rebuild the generator."""
    s = 0
    try:
        s += fl.GetDirty(doc)
    except Exception:
        pass

    def walk(layer):
        nonlocal s
        while layer is not None:
            s += layer.GetDirty(c4d.DIRTYFLAGS_DATA)
            try:
                o = layer.GetLinkedObject(doc)
            except Exception:
                o = None
            if o is not None:
                s += o.GetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_MATRIX)
                try:
                    s += int(o.GetMg().off.GetLength() * 1000) + int(o.GetMg().v1.x * 1000)
                except Exception:
                    pass
            walk(layer.GetDown())
            layer = layer.GetNext()
    try:
        walk(fl.GetLayersRoot().GetDown())
    except Exception:
        pass
    return s


# ------------------------------------------------------------------ plates ---
PLATE_STYLES = ["ascii", "bayer", "dots", "squares", "noise", "hex", "binary", "custom", "collection"]


def _plates_mod(folder=None):
    """glyphgrid_plates lives next to this file; inside the embedded generator there is no
    __file__, so it is found from the Plate Folder (<repo>/scripts/glyphgrid/plates/library)."""
    import os
    import sys
    cands = []
    if "__file__" in globals():
        cands.append(os.path.dirname(os.path.abspath(__file__)))
    if folder:
        cands.append(os.path.dirname(os.path.dirname(os.path.abspath(folder))))
    for here in cands:
        if os.path.exists(os.path.join(here, "glyphgrid_plates.py")) and here not in sys.path:
            sys.path.insert(0, here)
    import glyphgrid_plates
    return glyphgrid_plates


def plate_path(folder, style_index, grid, custom_text="", font="Menlo-Bold", sort_ink=True, size=1024,
               collection="", mixed=False):
    """Image for (style, grid). Built-in styles come from the library folder (<style>_<cells>up.png);
    'custom' is rendered from typed characters; 'collection' reads <plates>/collections/<name>/.
    mixed=True returns the composite 1/4/9/16-up atlas used by Grid Mode = Mixed."""
    import os
    import hashlib
    style = PLATE_STYLES[max(0, min(len(PLATE_STYLES) - 1, int(style_index)))]
    if mixed:
        parts = [plate_path(folder, style_index, g, custom_text, font, sort_ink, size, collection) for g in (1, 2, 3, 4)]
        sig = hashlib.md5("|".join("%s%d" % (p_, int(os.path.getmtime(p_)) if os.path.exists(p_) else 0)
                                    for p_ in parts).encode()).hexdigest()[:8]
        out = os.path.join(folder, "_mixed", "mixed_%s_%s.png" % (style, sig))
        if not os.path.exists(out):
            _plates_mod(folder).mixed_plate(parts, out, size * 2)
        return out
    cells = grid * grid
    if style == "collection":
        cdir = collection if os.path.isabs(collection or "") else \
            os.path.join(os.path.dirname(os.path.abspath(folder)), "collections", collection or "")
        return _plates_mod(folder).collection_plate(cdir, cells, size)
    if style != "custom":
        lib = os.path.join(folder, "%s_%dup.png" % (style, cells))
        if os.path.exists(lib):
            return lib
        # grids beyond the shipped library (5x5 ... 8x8): build that plate once, on demand
        out = os.path.join(folder, "_custom", "%s_%dup.png" % (style, cells))
        if not os.path.exists(out):
            _plates_mod(folder).library_plate(style, grid, out, size)
        return out
    key = hashlib.md5(("%s|%s|%d|%d" % (custom_text, font, sort_ink, cells)).encode("utf8")).hexdigest()[:8]
    path = os.path.join(folder, "_custom", "custom_%dup_%s.png" % (cells, key))   # unique name: renderers cache by path
    if not os.path.exists(path):
        try:   # crisp FreeType path when Pillow is importable in C4D's Python (see README)
            _plates_mod(folder).make_custom_plate(custom_text or "ABC", grid, path, font, sort_ink, size)
        except Exception as e:
            print("GlyphGrid: Pillow plate failed (%s), using GeClipMap fallback" % e)
            render_custom_plate(custom_text or "ABC", grid, path, font, sort_ink, size)
    return path


def render_custom_plate(text, grid, path, font="Menlo-Bold", sort_ink=True, size=1024, fill=0.86):
    """Draw your own characters into an N x N plate inside Cinema 4D (no PIL).

    Fallback when Pillow is not available (softer: upscaled).
    GOTCHA: GeClipMap.TextAt clips every pixel row beyond ~126 px below the text origin (font
    size above ~130 renders cut in half). So each glyph is drawn at size 120 into a scratch map,
    its ink bbox measured, and the crop is ScaleIt-upscaled and blitted into its atlas cell
    (BaseBitmap.ScaleBicubic only downscales). One scale for all glyphs (cap height of
    "M"), so '.' stays small and 'B' stays big. sort_ink orders cells sparse -> dense."""
    from c4d.bitmaps import GeClipMap, BaseBitmap
    chars = [ch for ch in text if not ch.isspace()] or ["?"]
    fd = GeClipMap.GetFontDescription(font, c4d.GE_FONT_NAME_POSTSCRIPT) or \
        GeClipMap.GetDefaultFont(c4d.GE_FONT_DEFAULT_MONOSPACED)
    P, FS, OX, OY = 200, 120.0, 40, 10    # glyph rows past ~origin+126 px are clipped: stay below

    def draw(ch):
        cm = GeClipMap()
        cm.Init(P, P, 32)
        cm.BeginDraw()
        cm.SetColor(0, 0, 0, 255)
        cm.FillRect(0, 0, P - 1, P - 1)
        cm.SetFont(fd, FS)
        cm.SetColor(255, 255, 255, 255)
        cm.TextAt(OX, OY, ch)
        x0 = y0 = 10 ** 9
        x1 = y1 = -1
        ink = 0
        for y in range(P):
            for x in range(P):
                v = cm.GetPixelRGBA(x, y)[0]
                if v > 20:
                    ink += v
                    if x < x0: x0 = x
                    if x > x1: x1 = x
                    if y < y0: y0 = y
                    if y > y1: y1 = y
        cm.EndDraw()
        bmp = cm.GetBitmap().GetClone()
        return bmp, ((x0, y0, x1, y1) if x1 >= 0 else None), ink

    info = {ch: draw(ch) for ch in set(chars) | {"M"}}
    seq = [chars[i % len(chars)] for i in range(grid * grid)]
    if sort_ink:
        seq.sort(key=lambda c: info[c][2])
    mb = info["M"][1]
    refh = (mb[3] - mb[1] + 1) if mb else FS * 0.7
    cell = size // grid
    W = cell * grid
    atlas = GeClipMap()
    atlas.Init(W, W, 32)
    atlas.BeginDraw()
    atlas.SetColor(0, 0, 0, 255)
    atlas.FillRect(0, 0, W - 1, W - 1)
    for i, ch in enumerate(seq):
        bmp, bb, _ = info[ch]
        if bb is None:
            continue
        w, h = bb[2] - bb[0] + 1, bb[3] - bb[1] + 1
        k = fill * cell / max(refh, w, h)
        dw, dh = max(1, int(round(w * k))), max(1, int(round(h * k)))
        # ScaleBicubic only DOWNscales ("destination image has to be smaller"): crop, then ScaleIt
        crop = BaseBitmap()
        crop.Init(w, h, 24)
        bmp.CopyPartTo(crop, bb[0], bb[1], w, h)
        big = BaseBitmap()
        big.Init(dw, dh, 24)
        crop.ScaleIt(big, 256, True, False)
        src = GeClipMap()
        src.InitWithBitmap(big, None)
        cx = (i % grid) * cell + (cell - dw) // 2
        cy = (i // grid) * cell + (cell - dh) // 2
        src.BeginDraw()
        atlas.Blit(cx, cy, src, 0, 0, dw - 1, dh - 1, c4d.GE_CM_BLIT_COPY)
        src.EndDraw()
    atlas.EndDraw()
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atlas.GetBitmap().Save(path, c4d.FILTER_PNG)
    return path


def _plate_like(p, folder):
    import os
    p = str(p or "").replace("file://", "")
    if not p:
        return False
    ap, af = os.path.abspath(p), os.path.abspath(os.path.dirname(os.path.abspath(folder)))
    return ap.startswith(af + os.sep) or os.path.basename(p).split("_")[0] in PLATE_STYLES + ["gg", "mixed"]


def swap_plate(op, new_path, folder):
    """Point every plate texture on op's materials at new_path: Redshift node texture samplers
    (node id gg_plate, or any sampler whose file sits in the plate folder), Octane ImageTexture
    and C4D Bitmap shaders using a plate. Returns the number of textures changed. Main thread only."""
    n = 0
    mats = []
    t = op.GetFirstTag()
    while t:
        if t.CheckType(c4d.Ttexture) and t[c4d.TEXTURETAG_MATERIAL]:
            mats.append(t[c4d.TEXTURETAG_MATERIAL])
        t = t.GetNext()
    for mat in mats:
        # Redshift / node materials
        try:
            import maxon
            nm = mat.GetNodeMaterialReference()
            RS = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")
            P = "com.redshift3d.redshift4c4d.nodes.core."
            if nm and nm.HasSpace(RS):
                g = nm.GetGraph(RS)
                with g.BeginTransaction() as tx:
                    for nd in g.GetViewRoot().GetChildren():
                        if "texturesampler" not in str(nd.GetValue(maxon.InternedId("net.maxon.node.attribute.assetid"))):
                            continue
                        pp = nd.GetInputs().FindChild(maxon.InternedId(P + "texturesampler.tex0")).FindChild(
                            maxon.InternedId("path"))
                        try:
                            cur = pp.GetPortValue()
                        except Exception:
                            cur = None
                        if str(nd.GetId()).startswith("gg_plate") or _plate_like(cur, folder):
                            pp.SetPortValue(maxon.Url(new_path))
                            n += 1
                    tx.Commit()
        except Exception as e:
            print("GlyphGrid: node material swap skipped:", e)
        # classic shaders (Octane ImageTexture 1029508, C4D Bitmap)
        sh = mat.GetFirstShader()
        stack = [sh] if sh else []
        while stack:
            s = stack.pop()
            while s:
                for pid in (getattr(c4d, "IMAGETEXTURE_FILE", None), c4d.BITMAPSHADER_FILENAME):
                    if pid is None:
                        continue
                    try:
                        cur = s[pid]
                    except Exception:
                        continue
                    if isinstance(cur, str) and _plate_like(cur, folder):
                        s[pid] = new_path
                        n += 1
                if s.GetDown():
                    stack.append(s.GetDown())
                s = s.GetNext()
        mat.Message(c4d.MSG_UPDATE)   # (no mat.Update(True, True): forcing the preview render from here hung C4D)
    c4d.EventAdd()
    return n


# ------------------------------------------------------------- collections UI ---
def collection_names(cdir):
    """Sub-folders of the collections folder (sorted), i.e. the Collection dropdown entries."""
    import os
    try:
        return sorted(d for d in os.listdir(cdir)
                      if os.path.isdir(os.path.join(cdir, d)) and not d.startswith((".", "_")))
    except Exception:
        return []


def collections_dir(plate_folder):
    import os
    return os.path.join(os.path.dirname(os.path.abspath(plate_folder)), "collections")


def plate_dirs(root):
    """Plates Folder -> (library dir, collections dir). Forgiving: if you browse to library/,
    collections/ or a single collection folder, it walks up to the folder that holds them."""
    import os
    if not root:
        return "", ""
    p = os.path.abspath(str(root).rstrip("/\\"))
    cand = p
    for _ in range(4):
        if os.path.isdir(os.path.join(cand, "library")) or os.path.isdir(os.path.join(cand, "collections")):
            p = cand
            break
        parent = os.path.dirname(cand)
        if parent == cand:
            break
        cand = parent
    lib = os.path.join(p, "library")
    return (lib if os.path.isdir(lib) else p), os.path.join(p, "collections")


def font_name(v):
    """Font user-data value (c4d.FontData or str) -> PostScript name for the plate renderer."""
    if v is None:
        return "Menlo-Bold"
    if isinstance(v, str):
        return v or "Menlo-Bold"
    try:
        bc = v.GetFont()
        fam = bc.GetString(c4d.GE_FONT_NAME_FAMILY) or ""
        sty = bc.GetString(c4d.GE_FONT_NAME_STYLE) or ""
        names = [bc.GetString(c4d.GE_FONT_NAME_POSTSCRIPT), bc.GetString(508), bc.GetString(509),
                 bc.GetString(c4d.GE_FONT_NAME_DISPLAY)]
        if not fam and not any(names):
            return "Menlo-Bold"
        # 'Family||Style||names...' -> glyphgrid_plates.resolve_font (CoreText on macOS)
        return "||".join([fam, sty] + [n for n in names if n])
    except Exception:
        return "Menlo-Bold"


def open_in_finder(path):
    import os
    import subprocess
    import sys
    os.makedirs(path, exist_ok=True)
    if sys.platform == "darwin":   # ShowInFinder only reveals the item in its parent: open the folder itself
        subprocess.Popen(["open", path])
    elif sys.platform.startswith("win"):
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(["xdg-open", path])


def new_collection(cdir, base="my_collection"):
    """Creates <cdir>/my_collection_N with a short how-to, returns its path."""
    import os
    k = 1
    while os.path.exists(os.path.join(cdir, "%s_%d" % (base, k))):
        k += 1
    path = os.path.join(cdir, "%s_%d" % (base, k))
    os.makedirs(path)
    with open(os.path.join(path, "_HOW_TO.txt"), "w") as f:
        f.write("Drop glyph images here, numbered in priority order:\n"
                "  01_first.png, 02_second.png, ...\n"
                "1-up uses #1, 4-up #1-4, 9-up #1-9, 16-up #1-16 (fewer images repeat).\n"
                "Transparent PNGs or white-on-black work best.\n"
                "Optional hand-made plates: name_4up.png etc. are used as-is.\n"
                "Then press 'Refresh List' on the GlyphGrid Plate tab and pick this folder.\n")
    return path


def set_cycle_items(op, name, items, keep=True):
    """Rebuild a user-data dropdown's entries; keeps the selected entry by name when possible."""
    for did, bc in op.GetUserDataContainer():
        if bc[c4d.DESC_NAME] != name:
            continue
        old = bc[c4d.DESC_CYCLE]
        cur = None
        if keep and old is not None:
            try:
                cur = old.GetString(op[did])
            except Exception:
                cur = None
        cyc = c4d.BaseContainer()
        for i, s_ in enumerate(items or ["(none)"]):
            cyc.SetString(i, s_)
        bc[c4d.DESC_CYCLE] = cyc
        op.SetUserDataContainer(did, bc)
        if cur in (items or []):
            op[did] = items.index(cur)
        elif op[did] >= len(items or [1]):
            op[did] = 0
        return True
    return False


def cycle_text(op, name):
    for did, bc in op.GetUserDataContainer():
        if bc[c4d.DESC_NAME] == name:
            cyc = bc[c4d.DESC_CYCLE]
            try:
                return cyc.GetString(op[did]) if cyc is not None else ""
            except Exception:
                return ""
    return ""


# ------------------------------------------------------------ slider limits ---
def apply_slider_caps(op, changed=None):
    """Fixed ranges: Grid N 1-4 (1/4/9/16-up), Quadtree Levels 1-4.
    Levels and Block are coupled so every level is real: a block of B polygons has
    log2(B)+1 sizes (2 polys -> 2,1). Raising Levels grows the Block to fit (Levels 4 -> 8 polys);
    lowering the Block pulls Levels down. Main thread only."""
    U = {bc[c4d.DESC_NAME]: (did, bc) for did, bc in op.GetUserDataContainer()}
    for name, cap in (("Grid (N x N)", GRID_MAX), ("Quadtree Levels", QT_LEVELS_MAX)):
        if name not in U:
            continue
        did, bc = U[name]
        if bc[c4d.DESC_MAX] != cap or bc[c4d.DESC_MAXSLIDER] != cap:
            bc[c4d.DESC_MAX] = cap
            bc[c4d.DESC_MAXSLIDER] = cap
            op.SetUserDataContainer(did, bc)
        if op[did] > cap:
            op[did] = cap
    if "Quadtree Levels" in U and "Quadtree Block (polys)" in U:
        dl, db = U["Quadtree Levels"][0], U["Quadtree Block (polys)"][0]
        lv, bi = int(op[dl]), int(op[db])
        if bi < lv - 1:
            if changed == "Quadtree Levels":
                op[db] = lv - 1          # block 2^(L-1) polygons
            else:
                op[dl] = bi + 1
    return {"Grid (N x N)": GRID_MAX, "Quadtree Levels": QT_LEVELS_MAX}


def write_result_to_source(op, res):
    """Cloth / soft bodies: put GlyphGrid's UV + colour tags straight onto the editable source,
    move it out next to the generator and switch the generator off. The simulation then runs on
    the real mesh (no one-frame lag behind the sim, no copy)."""
    d = op.GetDocument()
    src = op.GetDown()
    while src is not None and (src.GetInfo() & c4d.OBJECT_MODIFIER):
        src = src.GetNext()
    if d is None or res is None or src is None:
        return "Nothing built yet."
    out = res.GetDown()
    while out is not None and not out.IsInstanceOf(c4d.Opolygon):
        out = out.GetNext()
    if not src.IsInstanceOf(c4d.Opolygon) or out is None or src.GetPolygonCount() != out.GetPolygonCount() \
            or src.GetDown() is not None:
        return ("Write UVs onto Source needs ONE editable polygon object (no children) under "
                "GlyphGrid.\nMake it editable first, or use Bake to Polygon Object.")
    d.StartUndo()
    d.AddUndo(c4d.UNDOTYPE_CHANGE, src)
    for t in list(src.GetTags()):      # old GlyphGrid tags + the source's own UVs (first UVW wins)
        if t.CheckType(c4d.Tuvw) or (t.CheckType(c4d.Tvertexcolor) and t.GetName() == COLOR_TAG_NAME) \
                or (t.CheckType(c4d.Tpolygonselection) and t.GetName().startswith("GG_cell_")):
            t.Remove()
    pred = None
    for t in out.GetTags():
        if t.CheckType(c4d.Tuvw) or t.CheckType(c4d.Tvertexcolor) or t.CheckType(c4d.Tpolygonselection):
            cp = t.GetClone()
            src.InsertTag(cp, pred)
            pred = cp
    t = op.GetFirstTag()
    while t is not None:
        if t.CheckType(c4d.Ttexture):
            src.InsertTag(t.GetClone())
        t = t.GetNext()
    mg = src.GetMg()
    d.AddUndo(c4d.UNDOTYPE_CHANGE, src)
    src.Remove()
    d.InsertObject(src, pred=op)
    src.SetMg(mg)
    d.AddUndo(c4d.UNDOTYPE_NEW, src)
    d.AddUndo(c4d.UNDOTYPE_CHANGE_SMALL, op)
    op[c4d.ID_BASEOBJECT_GENERATOR_FLAG] = False
    d.EndUndo()
    d.SetActiveObject(src)
    c4d.EventAdd()
    c4d.gui.StatusSetText("GlyphGrid: UVs written onto %s - its simulation now drives the glyphs directly" % src.GetName())
    return None


# ---------------------------------------------------------------- look-dev ---
def _rs_glyph_color(g, tex, port, mode):
    """Vertex Attribute 'GlyphGrid Color' (the generator's Color tab) -> glyph colour.
    emissive / diffuse: multiplies the plate (texture sampler colour multiplier).
    hologram: drives emission + base colour (the plate stays the opacity mask)."""
    import maxon
    P = "com.redshift3d.redshift4c4d.nodes.core."
    va = g.AddChild(maxon.Id("gg_color"), maxon.Id(P + "vertexattributelookup"))
    vi = va.GetInputs()
    vi.FindChild(maxon.InternedId(P + "vertexattributelookup.attribute")).SetPortValue(maxon.String(COLOR_TAG))
    vi.FindChild(maxon.InternedId(P + "vertexattributelookup.defaultcolor")).SetPortValue(maxon.Color64(1, 1, 1))
    vo = va.GetOutputs().FindChild(maxon.InternedId(P + "vertexattributelookup.outcolor"))
    if mode == "hologram":
        vo.Connect(port("emission_color"))
        vo.Connect(port("base_color"))
    else:
        vo.Connect(tex.GetInputs().FindChild(maxon.InternedId(P + "texturesampler.color_multiplier")))
    return va


COLOR_TAG = COLOR_TAG_NAME


def build_plate_material(doc, target, plate_path, name="GlyphGrid Plate", mode="emissive",
                         color=(1.0, 1.0, 1.0), emission=1.0, base_tint=0.0, glyph_color=True, assign=True):
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
        if glyph_color:
            _rs_glyph_color(g, tex, port, mode)
        tx.Commit()
    if assign:
        assign_material(target, mat, replace=False)
    c4d.EventAdd()
    return mat


def enable_glyph_color(mat):
    """Add the Color-tab hook to an existing GlyphGrid plate material (Redshift or Octane).
    Returns True if something was added."""
    if mat.GetType() == 1029501:
        return _oct_glyph_color(mat)
    import maxon
    RS = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")
    P = "com.redshift3d.redshift4c4d.nodes.core."
    nm = mat.GetNodeMaterialReference()
    if not nm.HasSpace(RS):
        return False
    g = nm.GetGraph(RS)
    root = g.GetViewRoot()
    tex = surf = None
    for nd in root.GetChildren():
        sid = str(nd.GetId())
        if sid.startswith("gg_color"):
            return False      # already there
        if sid.startswith("gg_plate"):
            tex = nd
        elif "material" in sid and "output" not in sid:
            surf = nd
    if tex is None or surf is None:
        return False
    kind = str(surf.GetId()).split("@")[0]
    si = surf.GetInputs()
    port = lambda n: si.FindChild(maxon.InternedId(P + kind + "." + n))
    mode = "emissive"
    out = tex.GetOutputs().FindChild(maxon.InternedId(P + "texturesampler.outcolor"))
    for dst in [c[0] for c in out.GetConnections(maxon.PORT_DIR.OUTPUT)] if hasattr(out, "GetConnections") else []:
        d = str(dst.GetId())
        if d.endswith("opacity_color"):
            mode = "hologram"
        elif d.endswith("base_color"):
            mode = "diffuse"
    with g.BeginTransaction() as tx:
        _rs_glyph_color(g, tex, port, mode)
        tx.Commit()
    return True


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


def build_plate_material_octane(doc, target, plate_path, name="GlyphGrid Plate (Octane)", mode="hologram",
                                color=(0.15, 1.0, 0.35), emission=5.0, base=0.05, glyph_color=True, assign=True):
    """Octane (C4D) version of build_plate_material: Octane Diffuse material + ImageTexture.
      hologram: plate -> opacity (black knocked out), constant colour TextureEmission
      emissive: plate x colour -> TextureEmission, black body
      diffuse:  plate x colour -> diffuse
    swap_plate() re-points the ImageTexture (IMAGETEXTURE_FILE) like the Redshift sampler."""
    OCT_MAT, IMG, RGB, MUL, TEXEM = 1029501, 1029508, 1029504, 1029516, 1029642  # noqa
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
    if glyph_color:
        _oct_glyph_color(mat)
    mat.Message(c4d.MSG_UPDATE)
    if assign:
        assign_material(target, mat, replace=False, new_tag=True)
    c4d.EventAdd()
    return mat


def _oct_glyph_color(mat):
    """Octane: Attribute Texture 'GlyphGrid Color' takes over the colour (the generator's Color tab
    picker is the glyph colour; the plate still gives the glyph shapes / opacity)."""
    ATTR, MUL, TEXEM = 1056908, 1029516, 1029642
    sh = mat.GetFirstShader()
    while sh is not None:
        if sh.GetName() == "gg_color":
            return False
        sh = sh.GetNext()
    attr = c4d.BaseShader(ATTR)
    attr.SetName("gg_color")
    attr[2001] = 1                    # ATTRIBTEX_TYPE: colour attribute
    attr[2002] = COLOR_TAG            # ATTRIBTEX_IN_NAME
    mat.InsertShader(attr)
    em = mat[c4d.OCT_MATERIAL_EMISSION]
    target = None
    if em is not None and em.GetType() == TEXEM:
        cur = em[c4d.TEXEMISSION_EFFIC_OR_TEX]
        if cur is not None and cur.GetType() == MUL:      # emissive: plate x glyph colour
            cur[c4d.MULTIPLY_TEXTURE2] = attr
            target = cur
        else:                                              # hologram: glyph colour emission
            em[c4d.TEXEMISSION_EFFIC_OR_TEX] = attr
            target = em
    else:
        dl = mat[c4d.OCT_MATERIAL_DIFFUSE_LINK]
        if dl is not None and dl.GetType() == MUL:         # diffuse: plate x glyph colour
            dl[c4d.MULTIPLY_TEXTURE2] = attr
            target = dl
    mat.Message(c4d.MSG_UPDATE)
    return target is not None


# ------------------------------------------------- generator material buttons ---
def assign_material(op, mat, replace=True, new_tag=False):
    """Put mat on op with UVW projection. replace=True swaps the material of op's LAST texture tag
    (the one that wins), so pressing a material button never stacks tags."""
    tag = None
    if not new_tag:
        t = op.GetFirstTag()
        while t is not None:
            if t.CheckType(c4d.Ttexture):
                tag = t
                if not replace:
                    break
            t = t.GetNext()
    if tag is None:
        tag = op.MakeTag(c4d.Ttexture)
    tag[c4d.TEXTURETAG_MATERIAL] = mat
    tag[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_UVW
    return tag


def _ud(op):
    U = {bc[c4d.DESC_NAME]: did for did, bc in op.GetUserDataContainer()
         if did[-1].dtype not in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT)}
    return lambda k, d=None: op[U[k]] if k in U else d


def current_plate(op):
    """The plate file the generator's Plate tab points at right now (builds it if needed)."""
    g = _ud(op)
    folder, _cd = plate_dirs(g("Plates Folder", "") or "")
    if not folder:
        return None
    try:
        return plate_path(folder, g("Plate Style", 0), int(g("Grid (N x N)", 4)), g("Custom Glyphs", ""),
                          font_name(g("Font", None)), bool(g("Sort by Ink", True)),
                          collection=cycle_text(op, "Collection"), mixed=int(g("Grid Mode", 0)) == 1)
    except Exception as e:
        print("GlyphGrid plate:", e)
        return None


def _glyph_rgb(op):
    g = _ud(op)
    c = g("Glyph Color", None)
    return (c.x, c.y, c.z) if c is not None else (1.0, 1.0, 1.0)


def build_plate_material_standard(doc, target, plate_path, name="GlyphGrid Standard", color=(1.0, 1.0, 1.0),
                                  emission=1.0, assign=True):
    """Cinema 4D Standard/Physical material: dark diffuse, the plate knocks out opacity (Alpha
    channel, black = gone), Luminance = glyph colour. Uniform colour only: the Standard renderer
    can't read GlyphGrid's per-glyph colour tag from a generator (use Redshift / Octane for that)."""
    mat = c4d.BaseMaterial(c4d.Mmaterial)
    mat.SetName(name)
    mat[c4d.MATERIAL_USE_COLOR] = True
    mat[c4d.MATERIAL_COLOR_COLOR] = c4d.Vector(0.02)
    mat[c4d.MATERIAL_USE_REFLECTION] = False
    mat[c4d.MATERIAL_USE_LUMINANCE] = True
    mat[c4d.MATERIAL_LUMINANCE_COLOR] = c4d.Vector(*color)
    mat[c4d.MATERIAL_LUMINANCE_BRIGHTNESS] = float(emission)
    mat[c4d.MATERIAL_USE_ALPHA] = True
    bmp = c4d.BaseShader(c4d.Xbitmap)
    bmp[c4d.BITMAPSHADER_FILENAME] = plate_path
    bmp.SetName("gg_plate")
    mat[c4d.MATERIAL_ALPHA_SHADER] = bmp
    mat.InsertShader(bmp)
    mat[c4d.MATERIAL_ALPHA_IMAGEALPHA] = False      # luminance of the plate = opacity
    doc.InsertMaterial(mat)
    mat.Message(c4d.MSG_UPDATE)
    if assign:
        assign_material(target, mat)
    return mat


def build_glyph_material(doc, op, renderer="redshift"):
    """Color tab buttons: a generic glyph material for the chosen renderer, assigned to op.
    Diffuse body, the current plate knocks out opacity, emission = the Color tab (Glyph Color /
    Color Mode via the 'GlyphGrid Color' tag; Standard gets the picked colour)."""
    plate = current_plate(op)
    if not plate:
        c4d.gui.MessageDialog("GlyphGrid: set the Plates Folder (Plate tab) first.")
        return None
    col = _glyph_rgb(op)
    em = float(_ud(op)("Emission Strength", 1.0))
    name = {"standard": "GlyphGrid Standard", "octane": "GlyphGrid Octane"}.get(renderer, "GlyphGrid Redshift")
    # one material per renderer: pressing a button again (or switching back) reuses it, so the
    # buttons work as a switch - e.g. Redshift / Standard while working (they show in the viewport),
    # Octane for the Live Viewer / final render - and never pile up copies
    for m_ in doc.GetMaterials():
        if m_.GetName() == name and (m_.GetType() == 1029501) == (renderer == "octane") and \
                (renderer != "redshift" or _is_rs(m_)):
            doc.StartUndo()
            doc.AddUndo(c4d.UNDOTYPE_CHANGE, op)
            assign_material(op, m_)
            doc.EndUndo()
            swap_plate(op, plate, plate_dirs(_ud(op)("Plates Folder", "") or "")[0])
            sync_material_color(op, force=True)
            c4d.EventAdd()
            c4d.gui.StatusSetText("GlyphGrid: switched to %s" % name)
            return m_
    doc.StartUndo()
    if renderer == "standard":
        mat = build_plate_material_standard(doc, op, plate, color=col, emission=em, assign=False)
    elif renderer == "octane":
        mat = build_plate_material_octane(doc, op, plate, name="GlyphGrid Octane", mode="hologram", color=col,
                                          emission=5.0 * em, base=0.05, glyph_color=True, assign=False)
    else:
        mat = build_plate_material(doc, op, plate, name="GlyphGrid Redshift", mode="hologram", color=col,
                                   emission=em, glyph_color=True, assign=False)
    doc.AddUndo(c4d.UNDOTYPE_NEW, mat)
    doc.AddUndo(c4d.UNDOTYPE_CHANGE, op)
    assign_material(op, mat)
    doc.EndUndo()
    sync_material_color(op, force=True)
    c4d.EventAdd()
    c4d.gui.StatusSetText("GlyphGrid: %s assigned - colour follows the Color tab" % mat.GetName())
    return mat


def sync_material_color(op, force=False):
    """Keep op's GlyphGrid materials on the Color tab: Standard luminance colour + brightness,
    Redshift vertex-attribute fallback colour + emission weight, Octane emission power.
    (Per-glyph colours themselves come from the vertex colour tag.) Main thread only."""
    g = _ud(op)
    col = _glyph_rgb(op)
    em = float(g("Emission Strength", 1.0))
    n = 0
    t = op.GetFirstTag()
    while t is not None:
        mat = t[c4d.TEXTURETAG_MATERIAL] if t.CheckType(c4d.Ttexture) else None
        t = t.GetNext()
        if mat is None:
            continue
        if mat.GetType() == 1029501:                       # Octane
            sh = mat[c4d.OCT_MATERIAL_EMISSION]
            if sh is not None and sh.GetType() == 1029642:
                sh[c4d.TEXEMISSION_POWER] = 5.0 * em
                n += 1
            mat.Message(c4d.MSG_UPDATE)
            continue
        if mat.GetType() != c4d.Mmaterial:
            continue
        rs_done = False
        try:
            import maxon
            RS = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")
            P = "com.redshift3d.redshift4c4d.nodes.core."
            nm = mat.GetNodeMaterialReference()
            if nm is not None and nm.HasSpace(RS):
                gr = nm.GetGraph(RS)
                with gr.BeginTransaction() as tx:
                    for nd in gr.GetViewRoot().GetChildren():
                        sid = str(nd.GetId())
                        if sid.startswith("gg_color"):
                            nd.GetInputs().FindChild(maxon.InternedId(P + "vertexattributelookup.defaultcolor")) \
                                .SetPortValue(maxon.Color64(*col))
                        elif "material" in sid and "output" not in sid:
                            kind = sid.split("@")[0]
                            p_ = nd.GetInputs().FindChild(maxon.InternedId(P + kind + ".emission_weight"))
                            if p_ is not None:
                                p_.SetPortValue(maxon.Float64(em))
                    tx.Commit()
                rs_done = True
                n += 1
        except Exception as e:
            print("GlyphGrid colour sync (RS):", e)
        if not rs_done and mat[c4d.MATERIAL_USE_LUMINANCE] and _has_plate_shader(mat):
            mat[c4d.MATERIAL_LUMINANCE_COLOR] = c4d.Vector(*col)
            mat[c4d.MATERIAL_LUMINANCE_BRIGHTNESS] = em
            mat.Message(c4d.MSG_UPDATE)
            n += 1
    c4d.EventAdd()
    return n


def _is_rs(mat):
    try:
        import maxon
        nm = mat.GetNodeMaterialReference()
        return bool(nm) and nm.HasSpace(maxon.Id("com.redshift3d.redshift4c4d.class.nodespace"))
    except Exception:
        return False


def _has_plate_shader(mat):
    sh = mat.GetFirstShader()
    while sh is not None:
        if sh.GetName() == "gg_plate":
            return True
        sh = sh.GetNext()
    return False
