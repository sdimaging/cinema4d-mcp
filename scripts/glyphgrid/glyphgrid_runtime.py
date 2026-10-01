"""
GlyphGrid runtime: the Cinema 4D side (mesh in -> UVW tag out).

Used two ways
  * embedded into the GlyphGrid Python Generator by glyphgrid_c4d.build_generator()
  * imported by glyphgrid_c4d.bake_object() for a one-shot bake on an editable mesh

Everything here is plain c4d + glyphgrid_core; numpy is used only if present.
"""
import math
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
    t4 = time.time()
    c = res["counts"]
    return dict(polys=len(F), islands=res["islands"], cells=len(c), min=min(c), max=max(c),
                t_read=round(t1 - t0, 3), t_values=round(t2 - t1, 3), t_solve=round(t3 - t2, 3),
                t_write=round(t4 - t3, 3), numpy=_np is not None)


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


def collect_polys(op, out=None, ignore_control=True):
    """SDK DoRecursion variant: deform cache > cache > the object itself.
    Children are visited only when the object has no cache (a generator consumes its children:
    Cloner, Symmetry, SDS ...). The control-object bit is ignored on purpose: inside a generator
    every source object and its caches carry it. Cache objects report correct GetMg() (2026.4)."""
    if out is None:
        out = []
    dc = op.GetDeformCache()
    c = op.GetCache()
    if dc is not None:
        collect_polys(dc, out)
    elif c is not None:
        collect_polys(c, out)
    elif op.IsInstanceOf(c4d.Opolygon) and op.GetPolygonCount():
        out.append((op, op.GetMg()))
    if c is None:
        ch = op.GetDown()
        while ch is not None:
            collect_polys(ch, out)
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


def open_in_finder(path):
    import os
    import subprocess
    import sys
    os.makedirs(path, exist_ok=True)
    try:
        c4d.storage.ShowInFinder(path, True)
        return
    except Exception:
        pass
    if sys.platform == "darwin":
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
