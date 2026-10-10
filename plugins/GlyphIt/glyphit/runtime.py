"""
GlyphIt runtime: the Cinema 4D side (mesh in -> UVW tag out).

Used two ways
  * used by the GlyphIt plugin object (GlyphIt.pyp) and the legacy Python Generator
  * imported by legacy_c4d.bake_object() for a one-shot bake on an editable mesh

Everything here is plain c4d + glyphit.core; numpy is used only if present.
"""
import math
import random
import time

import c4d

from .core import compute, _np, DEFAULTS  # noqa: F401

SRC_RANDOM, SRC_VMAP, SRC_FIELD, SRC_HEIGHT, SRC_LIGHT, SRC_CAMERA, SRC_CURV, SRC_INDEX, SRC_DIST, SRC_IMAGE = range(10)
UP_AXES = [(0, 1, 0), (0, 0, 1), (1, 0, 0), (0, -1, 0)]
TAG_NAME = "GlyphIt UV"
ID_TAG_NAME = "GlyphIt ID"
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


# ----------------------------------------------------------- image source ---
# Value Source "Image / Movie" + Color Mode "From Image": an image, numbered sequence, GIF / movie
# or any C4D shader, read through the SOURCE object's own UVs (a plane shows the picture 1:1).
# Brightness -> which glyph (dense glyphs where it's bright), RGB -> glyph colour.
_IMG_CACHE = {}           # (file, mtime, sub-frame, maxdim) -> (rgb float32 HxWx3, summed-area table)
_IMG_ORDER = []
_SEQ_CACHE = {}
_MOVIES = {}
IMAGE_MAXDIM = 768        # images are reduced to this many pixels on the long side (plenty per glyph)
_MOVIE_EXT = (".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv", ".mpg", ".mpeg")


def _seq_info(path):
    """Numbered sequence for path ('clock_0001.png', 'clock_####.png') -> (dir, {number: file}, sorted
    numbers) or None for a single still image."""
    import os
    import re
    d, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    m = re.search(r"#+", stem)
    if m:
        pre, post, nd = stem[:m.start()], stem[m.end():], len(m.group(0))
    else:
        m = re.search(r"(\d+)$", stem)
        if not m or len(m.group(1)) < 2:     # 'logo_v2.png' is a still, 'clock_0001.png' a sequence
            return None
        pre, post, nd = stem[:m.start()], "", len(m.group(1))
    try:
        dm = os.path.getmtime(d)
    except Exception:
        return None
    key = (d, pre, post, ext.lower(), nd)
    hit = _SEQ_CACHE.get(key)
    if hit and hit[0] == dm:
        return hit[1]
    files = {}
    try:
        for f in os.listdir(d):
            fs, fe = os.path.splitext(f)
            if fe.lower() != ext.lower() or not fs.startswith(pre) or (post and not fs.endswith(post)):
                continue
            mid = fs[len(pre):len(fs) - len(post)] if post else fs[len(pre):]
            if mid.isdigit() and (len(mid) == nd or (len(mid) > nd and not mid.startswith("0"))):
                files[int(mid)] = os.path.join(d, f)
    except Exception:
        files = {}
    info = (d, files, sorted(files)) if len(files) > 1 or "#" in stem else None
    _SEQ_CACHE[key] = (dm, info)
    return info


def _frame_index(img, count):
    """Document frame -> index into a sequence / movie of `count` frames (speed, offset, loop)."""
    i = int(math.floor(float(img.get("frame", 0)) * float(img.get("speed", 1.0)))) + int(img.get("offset", 0))
    if count <= 0:
        return 0
    if img.get("loop", True):
        return i % count
    return max(0, min(count - 1, i))


def image_resolve(img):
    """-> (kind, path, sub) for the current frame: ('still'|'seq'|'gif'|'movie', file, frame index)."""
    import os
    path = str(img.get("file") or "").strip()
    if not path:
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in _MOVIE_EXT:
        return ("movie", path, int(math.floor(float(img.get("frame", 0)) * float(img.get("speed", 1.0))))
                + int(img.get("offset", 0)))
    seq = _seq_info(path)
    if seq is not None and seq[2]:
        nums = seq[2]
        return ("seq", seq[1][nums[_frame_index(img, len(nums))]], 0)
    if ext == ".gif":
        return ("gif", path, int(math.floor(float(img.get("frame", 0)) * float(img.get("speed", 1.0))))
                + int(img.get("offset", 0)))
    return ("still", path, 0)


def image_signature(img):
    """Changes exactly when the sampled picture can change (new file, edited file, new frame of an
    animated source). A still image therefore never forces a rebuild per frame."""
    import os
    if not img:
        return None
    sh = img.get("shader")
    if sh is not None:
        try:
            return ("shader", str(sh.GetGUID()), sh.GetDirty(c4d.DIRTYFLAGS_DATA), img.get("frame"),
                    img.get("sampling"))
        except Exception:
            return ("shader", img.get("frame"))
    r = image_resolve(img)
    if r is None:
        return None
    try:
        mt = int(os.path.getmtime(r[1]) * 1000)
    except Exception:
        mt = 0
    return (r[0], r[1], mt, r[2] if r[0] != "still" else 0, img.get("sampling"),
            img.get("loop") if r[0] in ("gif", "movie") else None)


def _bitmap_array(bmp, maxdim=IMAGE_MAXDIM):
    """c4d.bitmaps.BaseBitmap -> float32 HxWx3 (reduced to maxdim)."""
    w, h = bmp.GetBw(), bmp.GetBh()
    step = max(1, int(math.ceil(max(w, h) / float(maxdim))))
    xs, ys = list(range(0, w, step)), list(range(0, h, step))
    rows = []
    for y in ys:
        row = []
        for x in xs:
            r, g_, b = bmp.GetPixel(x, y)
            row.append((r / 255.0, g_ / 255.0, b / 255.0))
        rows.append(row)
    if _np is not None:
        return _np.asarray(rows, dtype=_np.float32)
    return rows


def _movie_bitmap(path, index, loop=True):
    ml_cls = getattr(c4d.bitmaps, "MovieLoader", None)
    if ml_cls is None:
        raise RuntimeError("this Cinema 4D has no MovieLoader - export the movie as an image sequence")
    ml = _MOVIES.get(path)
    if ml is None:
        ml = ml_cls()
        if ml.Open(path) != c4d.IMAGERESULT_OK:
            raise RuntimeError("can't open movie %s" % path)
        _MOVIES[path] = ml
    info = ml.GetInfo()
    count = int(info[0]) if isinstance(info, (tuple, list)) else int(info)
    i = (index % count) if loop else max(0, min(count - 1, index))
    rr = ml.Read(i)
    bmp = rr[1] if isinstance(rr, (tuple, list)) else rr
    if bmp is None:
        raise RuntimeError("can't read frame %d of %s" % (i, path))
    return bmp


def _load_rgb(kind, path, sub, loop=True, maxdim=IMAGE_MAXDIM):
    """-> float32 HxWx3 in 0..1 (numpy) or nested lists without numpy."""
    if kind == "movie":
        return _bitmap_array(_movie_bitmap(path, sub, loop), maxdim)
    try:
        from PIL import Image
        im = Image.open(path)
        if kind == "gif":
            n = getattr(im, "n_frames", 1)
            im.seek((sub % n) if loop else max(0, min(n - 1, sub)))
        if im.mode in ("I;16", "I;16B", "I", "F"):
            im = im.convert("F")
            if _np is not None:
                a = _np.asarray(im, dtype=_np.float32)
                a = a / (65535.0 if a.max() > 255.0 else 255.0)
                im = Image.fromarray(_np.clip(a * 255.0, 0, 255).astype(_np.uint8))
        im = im.convert("RGB")
        if max(im.size) > maxdim:
            k = maxdim / float(max(im.size))
            im = im.resize((max(1, int(im.size[0] * k)), max(1, int(im.size[1] * k))), Image.BOX)
        if _np is not None:
            return _np.asarray(im, dtype=_np.float32) / 255.0
        w, h = im.size
        px = im.load()
        return [[tuple(c / 255.0 for c in px[x, y]) for x in range(w)] for y in range(h)]
    except ImportError:
        pass
    except Exception:
        if kind == "gif":
            raise
    bmp = c4d.bitmaps.BaseBitmap()                       # EXR / HDR / PSD / TIFF variants ...
    if bmp.InitWith(path)[0] != c4d.IMAGERESULT_OK:
        raise RuntimeError("can't read image %s" % path)
    return _bitmap_array(bmp, maxdim)


def _image_frame(img):
    """Current frame of the file source as (rgb, summed-area table); cached (a few frames)."""
    import os
    r = image_resolve(img)
    if r is None:
        return None
    kind, path, sub = r
    try:
        mt = os.path.getmtime(path)
    except Exception:
        raise RuntimeError("image not found: %s" % path)
    key = (kind, path, mt, sub if kind in ("gif", "movie") else 0, bool(img.get("loop", True)))
    hit = _IMG_CACHE.get(key)
    if hit is not None:
        return hit
    rgb = _load_rgb(kind, path, sub, img.get("loop", True))
    sat = None
    if _np is not None:
        h, w = rgb.shape[:2]
        sat = _np.zeros((h + 1, w + 1, 3), dtype=_np.float64)
        sat[1:, 1:] = rgb.cumsum(0).cumsum(1)
    _IMG_CACHE[key] = (rgb, sat)
    _IMG_ORDER.append(key)
    while len(_IMG_ORDER) > 4:
        _IMG_CACHE.pop(_IMG_ORDER.pop(0), None)
    return _IMG_CACHE[key]


def image_boxes(po, P=None, F=None):
    """Per polygon UV rectangle (u0, v0, u1, v1) from the SOURCE object's own UVW tag (the first one
    that isn't GlyphIt's). Without UVs: a planar projection over the object's largest side.
    Returns (boxes, from_uvs)."""
    n = po.GetPolygonCount()
    tag = None
    t = po.GetFirstTag()
    while t is not None:
        if t.CheckType(c4d.Tuvw) and t.GetName() not in (TAG_NAME, ID_TAG_NAME, PALETTE_UV_NAME) \
                and t.GetDataCount() == n:
            tag = t
            break
        t = t.GetNext()
    if n == 0:
        return ([] if _np is None else _np.zeros((0, 4), dtype=_np.float32)), False
    if tag is not None:
        corners = None
        raw = tag.GetLowlevelDataAddressR() if _np is not None else None
        if raw is not None:
            b = bytes(raw)
            bp = len(b) // n if n else 0
            try:
                if bp == 32:
                    corners = _np.frombuffer(b, dtype=_np.float32).reshape(n, 4, 2)
                elif bp == 48:
                    corners = _np.frombuffer(b, dtype=_np.float32).reshape(n, 4, 3)[:, :, :2]
                elif bp == 64:
                    corners = _np.frombuffer(b, dtype=_np.float64).reshape(n, 4, 2)
                elif bp == 96:
                    corners = _np.frombuffer(b, dtype=_np.float64).reshape(n, 4, 3)[:, :, :2]
            except Exception:
                corners = None
        if corners is None:                              # slow but layout-proof
            cl = []
            for i in range(n):
                q = tag.GetSlow(i)
                cl.append([(q["a"].x, q["a"].y), (q["b"].x, q["b"].y), (q["c"].x, q["c"].y), (q["d"].x, q["d"].y)])
            if _np is not None:
                corners = _np.asarray(cl, dtype=_np.float64)
            else:
                out = []
                for cs in cl:
                    us, vs = [c[0] for c in cs], [c[1] for c in cs]
                    cu, cv = math.floor((min(us) + max(us)) * 0.5), math.floor((min(vs) + max(vs)) * 0.5)
                    out.append((min(us) - cu, min(vs) - cv, max(us) - cu, max(vs) - cv))
                return out, True
        c = corners.astype(_np.float64)
        lo, hi = c.min(axis=1), c.max(axis=1)
        sh = _np.floor((lo + hi) * 0.5)                  # tiled UVs: shift each box into 0..1
        lo, hi = lo - sh, hi - sh
        return _np.concatenate([lo, hi], axis=1), True
    # no UVs: planar over the two largest bounding-box axes
    if P is None or F is None:
        P, F, _ = read_mesh(po)
    xs, ys, zs = [p[0] for p in P], [p[1] for p in P], [p[2] for p in P]
    ext = [(max(a) - min(a)) if a else 0.0 for a in (xs, ys, zs)]
    ax = sorted(range(3), key=lambda k: -ext[k])[:2]
    ax.sort()
    mn = [min(a) if a else 0.0 for a in (xs, ys, zs)]
    au, av = ax[0], ax[1]
    su, sv = ext[au] or 1.0, ext[av] or 1.0
    out = []
    for q in F:
        ids = (q[0], q[1], q[2]) if q[2] == q[3] else q
        us = [(P[j][au] - mn[au]) / su for j in ids]
        vs = [1.0 - (P[j][av] - mn[av]) / sv for j in ids]
        out.append((min(us), min(vs), max(us), max(vs)))
    if _np is not None:
        return _np.asarray(out, dtype=_np.float64), False
    return out, False


def _shader_rgb(img, boxes):
    sh = img.get("shader")
    doc = img.get("doc")
    R = c4d.modules.render
    try:
        irs = R.InitRenderStruct(doc) if doc is not None else R.InitRenderStruct()
    except TypeError:
        irs = R.InitRenderStruct()
    if sh.InitRender(irs) != c4d.INITRENDERRESULT_OK:
        raise RuntimeError("shader could not initialise")
    try:
        cd = R.ChannelData()
        cd.t = float(img.get("time", 0.0))
        cd.n = c4d.Vector(0, 0, 1)
        out = []
        area = int(img.get("sampling", 0)) == 0
        for b in boxes:
            u0, v0, u1, v1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
            cd.d = c4d.Vector(u1 - u0, v1 - v0, 0) if area else c4d.Vector(0)
            cd.p = c4d.Vector((u0 + u1) * 0.5, (v0 + v1) * 0.5, 0)
            col = sh.Sample(cd)
            out.append((col.x, col.y, col.z))
    finally:
        sh.FreeRender()
    return _np.asarray(out, dtype=_np.float32) if _np is not None else out


def image_sample(img, boxes):
    """RGB 0..1 per polygon box, or None if there's no image. Area mode averages every pixel under
    the glyph (summed-area table: stable, no flicker on moving footage); centre takes one pixel."""
    if not img or boxes is None or len(boxes) == 0:
        return None
    if img.get("shader") is not None:
        return _shader_rgb(img, boxes)
    fr = _image_frame(img)
    if fr is None:
        return None
    rgb, sat = fr
    area = int(img.get("sampling", 0)) == 0
    if _np is not None:
        h, w = rgb.shape[:2]
        b = _np.asarray(boxes, dtype=_np.float64)
        if area and sat is not None:
            x0 = _np.clip(_np.floor(b[:, 0] * w), 0, w - 1).astype(_np.int64)
            y0 = _np.clip(_np.floor(b[:, 1] * h), 0, h - 1).astype(_np.int64)
            x1 = _np.maximum(_np.clip(_np.ceil(b[:, 2] * w), 0, w).astype(_np.int64), x0 + 1)
            y1 = _np.maximum(_np.clip(_np.ceil(b[:, 3] * h), 0, h).astype(_np.int64), y0 + 1)
            s = sat[y1, x1] - sat[y0, x1] - sat[y1, x0] + sat[y0, x0]
            return (s / ((x1 - x0) * (y1 - y0))[:, None]).astype(_np.float32)
        cx = _np.clip(((b[:, 0] + b[:, 2]) * 0.5 * w).astype(_np.int64), 0, w - 1)
        cy = _np.clip(((b[:, 1] + b[:, 3]) * 0.5 * h).astype(_np.int64), 0, h - 1)
        return rgb[cy, cx].astype(_np.float32)
    h, w = len(rgb), len(rgb[0])
    out = []
    for b in boxes:
        x = min(w - 1, max(0, int((b[0] + b[2]) * 0.5 * w)))
        y = min(h - 1, max(0, int((b[1] + b[3]) * 0.5 * h)))
        out.append(rgb[y][x])
    return out


def concat_boxes(parts):
    """Join per-part box lists (numpy or lists) in part order."""
    parts = [b for b in parts if b is not None and len(b)]
    if not parts:
        return None
    if _np is not None:
        return _np.concatenate([_np.asarray(b, dtype=_np.float64).reshape(-1, 4) for b in parts], 0)
    out = []
    for b in parts:
        out.extend(b)
    return out


def luminance(rgb):
    """Rec.709 brightness 0..1 per polygon (the value that picks the glyph)."""
    if rgb is None:
        return None
    if _np is not None:
        a = _np.asarray(rgb, dtype=_np.float64)
        return _np.clip(a[:, 0] * 0.2126 + a[:, 1] * 0.7152 + a[:, 2] * 0.0722, 0.0, 1.0).tolist()
    return [max(0.0, min(1.0, r * 0.2126 + g_ * 0.7152 + b * 0.0722)) for r, g_, b in rgb]


def image_glyph_colors(res, color):
    """From Image: each glyph = the average image colour of its polygons (one flat colour per glyph,
    also for clusters / quadtree blocks) x Glyph Color (white = the image's own colours)."""
    base = tuple(float(x) for x in color.get("base", (1.0, 1.0, 1.0)))
    rgb = res.get("img_rgb")
    n = len(res["cell"])
    if rgb is None or len(rgb) != n:
        return [base] * n if _np is None else _np.tile(_np.asarray(base, dtype=_np.float32), (n, 1))
    if _np is not None:
        a = _np.asarray(rgb, dtype=_np.float64)
        isl = _np.asarray(res["island"], dtype=_np.int64)
        cnt = _np.bincount(isl).astype(_np.float64)
        cnt[cnt == 0] = 1.0
        avg = _np.stack([_np.bincount(isl, weights=a[:, c], minlength=len(cnt)) / cnt for c in range(3)], 1)
        return (_np.clip(avg[isl], 0.0, 1.0) * _np.asarray(base)[None, :]).astype(_np.float32)
    acc = {}
    for k, c in zip(res["island"], rgb):
        s = acc.setdefault(k, [0.0, 0.0, 0.0, 0])
        s[0] += c[0]; s[1] += c[1]; s[2] += c[2]; s[3] += 1
    return [tuple(max(0.0, min(1.0, acc[k][j] / acc[k][3])) * base[j] for j in range(3)) for k in res["island"]]


def rgb565(cols):
    """RGB 0..1 -> RGB565 code (0..65535) = pixel in the fixed palette (see rgb565_table)."""
    if _np is not None:
        a = _np.clip(_np.asarray(cols, dtype=_np.float64), 0.0, 1.0)
        r = _np.rint(a[:, 0] * 31).astype(_np.int64)
        g_ = _np.rint(a[:, 1] * 63).astype(_np.int64)
        b = _np.rint(a[:, 2] * 31).astype(_np.int64)
        return (r << 11) | (g_ << 5) | b
    out = []
    for c in cols:
        r, g_, b = [max(0.0, min(1.0, float(x))) for x in c]
        out.append((int(round(r * 31)) << 11) | (int(round(g_ * 63)) << 5) | int(round(b * 31)))
    return out


def rgb565_table():
    """Every RGB565 colour once, in code order (palette for From Image)."""
    if _np is not None:
        k = _np.arange(PALETTE_N, dtype=_np.int64)
        return _np.stack([((k >> 11) & 31) / 31.0, ((k >> 5) & 63) / 63.0, (k & 31) / 31.0], 1).astype(_np.float32)
    return [(((k >> 11) & 31) / 31.0, ((k >> 5) & 63) / 63.0, (k & 31) / 31.0) for k in range(PALETTE_N)]


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


COLOR_TAG_NAME = "GlyphIt Color"
OLD_COLOR_TAG_NAME = "GlyphGrid Color"
COLOR_UNIFORM, COLOR_CELL, COLOR_GLYPH, COLOR_IMAGE = range(4)


def _hsv_to_rgb(h, s, v):
    import colorsys
    return colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, v))


PALETTE_W = 256                     # palette texture = 256 x 256 colours, key k -> pixel (k % W, k // W)
PALETTE_N = PALETTE_W * PALETTE_W
PALETTE_UV_NAME = "GlyphIt Palette UV"
_M64 = 0xFFFFFFFFFFFFFFFF


def _mix64_py(x):
    x &= _M64
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & _M64
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & _M64
    x ^= x >> 31
    return x


def _rand01(keys, seed, salt):
    """Deterministic 0..1 per key (splitmix64), identical in numpy and pure Python."""
    off = ((int(seed) * 1000003 + salt) * 0xBF58476D1CE4E5B9) & _M64
    if _np is not None:
        with _np.errstate(over="ignore"):
            x = (_np.asarray(keys, dtype=_np.uint64) + _np.uint64(1)) * _np.uint64(0x9E3779B97F4A7C15) + _np.uint64(off)
            x ^= x >> _np.uint64(30)
            x *= _np.uint64(0xBF58476D1CE4E5B9)
            x ^= x >> _np.uint64(27)
            x *= _np.uint64(0x94D049BB133111EB)
            x ^= x >> _np.uint64(31)
        return (x >> _np.uint64(11)).astype(_np.float64) / float(1 << 53)
    return [(_mix64_py((int(k) + 1) * 0x9E3779B97F4A7C15 + off) >> 11) / float(1 << 53) for k in keys]


def color_table(color, n):
    """Colours for keys 0..n-1 (n x 3 floats). The ONE colour function: Redshift reads it as
    vertex colours, Octane as a palette texture on a second UV set - same colours in both."""
    import colorsys
    mode = int(color.get("mode", COLOR_UNIFORM))
    base = tuple(float(x) for x in color.get("base", (1.0, 1.0, 1.0)))
    if mode == COLOR_UNIFORM:
        return [base] * n if _np is None else _np.tile(_np.asarray(base, dtype=_np.float32), (n, 1))
    amt = max(0.0, min(1.0, float(color.get("amount", 0.35))))
    spread = max(0.0, min(1.0, float(color.get("hue_spread", 1.0))))
    sat = max(0.0, min(1.0, float(color.get("saturation", 0.8))))
    bj = max(0.0, min(1.0, float(color.get("brightness", 0.0))))
    seed = int(color.get("seed", 0))
    bh, bs, bv = colorsys.rgb_to_hsv(*[max(0.0, min(1.0, c)) for c in base])
    vmax = max(base) if max(base) > 0 else 1.0
    keys = range(n)
    r1, r2 = _rand01(keys, seed, 11), _rand01(keys, seed, 23)
    if _np is not None:
        h = (bh + (r1 - 0.5) * spread) if bs > 0.05 else r1
        h = _np.mod(h, 1.0)
        v = vmax * (1.0 - bj * r2)
        i6 = _np.floor(h * 6.0)
        f = h * 6.0 - i6
        p_, q_, t_ = v * (1 - sat), v * (1 - sat * f), v * (1 - sat * (1 - f))
        i6 = i6.astype(_np.int64) % 6
        rgb = _np.choose(i6[:, None] * _np.ones((1, 3), dtype=_np.int64), [
            _np.stack([v, t_, p_], 1), _np.stack([q_, v, p_], 1), _np.stack([p_, v, t_], 1),
            _np.stack([p_, q_, v], 1), _np.stack([t_, p_, v], 1), _np.stack([v, p_, q_], 1)])
        b = _np.asarray(base, dtype=_np.float64)[None, :]
        return (b + (rgb - b) * amt).astype(_np.float32)
    out = []
    for a, c in zip(r1, r2):
        h = bh + (a - 0.5) * spread if bs > 0.05 else a
        rc = _hsv_to_rgb(h, sat, vmax * (1.0 - bj * c))
        out.append(tuple(b_ + (x - b_) * amt for b_, x in zip(base, rc)))
    return out


def color_keys(res, color):
    """Per-polygon palette key: 0 for Uniform, the plate cell, or the glyph (island) id.
    From Image: the RGB565 code of the glyph's colour (palette = the fixed 565 colour table)."""
    mode = int(color.get("mode", COLOR_UNIFORM))
    if mode == COLOR_IMAGE:
        return rgb565(glyph_colors(res, color))
    if mode == COLOR_UNIFORM:
        return [0] * len(res["cell"])
    keys = res["cell"] if mode == COLOR_CELL else res["island"]
    if _np is not None:
        return _np.asarray(keys, dtype=_np.int64) % PALETTE_N
    return [int(k) % PALETTE_N for k in keys]


def glyph_colors(res, color):
    """Per-polygon RGB from the Color tab (see color_table)."""
    if int(color.get("mode", COLOR_UNIFORM)) == COLOR_IMAGE:
        return image_glyph_colors(res, color)
    keys = color_keys(res, color)
    if _np is not None:
        k = _np.asarray(keys, dtype=_np.int64)
        tab = color_table(color, int(k.max()) + 1 if len(k) else 1)
        return _np.asarray(tab, dtype=_np.float32)[k]
    n = (max(keys) + 1) if keys else 1
    tab = color_table(color, n)
    return [tab[k] for k in keys]


def write_palette_uv(po, keys, name=PALETTE_UV_NAME):
    """Second UVW tag (right after GlyphIt UV = Octane UV set 2): every corner of a polygon
    points at the centre of its colour's pixel in the palette texture. UVs are per corner, so
    unlike vertex colours (which Octane averages per POINT = smeared colour noise across glyphs)
    every glyph gets exactly one flat colour."""
    W = float(PALETTE_W)
    if _np is not None:
        k = _np.asarray(keys, dtype=_np.int64)
        u = ((k % PALETTE_W) + 0.5) / W
        v = ((k // PALETTE_W) + 0.5) / W
        uv = _np.repeat(_np.stack([u, v], 1), 4, axis=0).reshape(-1).astype(_np.float32)
    else:
        uv = []
        for k in keys:
            u, v = ((k % PALETTE_W) + 0.5) / W, ((k // PALETTE_W) + 0.5) / W
            uv.extend((u, v, u, v, u, v, u, v))
    tag = write_uvw(po, uv, name, first=False)
    # keep it directly behind the GlyphIt UV tag: Octane addresses UV sets by order
    main = None
    t = po.GetFirstTag()
    while t:
        if t.CheckType(c4d.Tuvw) and t.GetName() == TAG_NAME:
            main = t
            break
        t = t.GetNext()
    if main is not None and tag is not None and main.GetNext() is not tag:
        tag.Remove()
        po.InsertTag(tag, main)
    return tag


def palette_file(folder, color):
    """The palette PNG for these Color-tab settings (built once, cached by a hash of the settings).
    Raw values (Octane reads it as non-colour data), same numbers as the vertex colours."""
    import os
    import hashlib
    if not folder:
        return None
    d = os.path.join(folder, "_custom")
    if int(color.get("mode", COLOR_UNIFORM)) == COLOR_IMAGE:
        path = os.path.join(d, "palette_rgb565.png")       # fixed: every RGB565 colour once
        key = None
    else:
        key = hashlib.md5(repr(sorted((k, v) for k, v in color.items() if k != "img")).encode()).hexdigest()[:10]
        path = os.path.join(d, "palette_%s.png" % key)
    if os.path.exists(path):
        return path
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    tab = rgb565_table() if key is None else color_table(color, PALETTE_N)
    try:
        from PIL import Image
        import numpy as np_
        a = (np_.clip(np_.asarray(tab, dtype=np_.float32), 0.0, 1.0) * 255.0 + 0.5).astype(np_.uint8)
        Image.fromarray(a.reshape(PALETTE_W, PALETTE_W, 3)).save(path)
    except Exception:
        bmp = c4d.bitmaps.BaseBitmap()
        bmp.Init(PALETTE_W, PALETTE_W, 24)
        for k in range(PALETTE_N):
            c = tab[k]
            bmp.SetPixel(k % PALETTE_W, k // PALETTE_W, *[int(max(0.0, min(1.0, float(x))) * 255 + 0.5) for x in c])
        bmp.Save(path, c4d.FILTER_PNG)
    return path


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
        # created in per-point mode with the POINT count (the default mode), then switched: making it
        # with the polygon count and converting could read past the end on meshes with more points
        tag = c4d.VertexColorTag(po.GetPointCount())
        tag.SetName(name)
        po.InsertTag(tag)
        tag.SetPerPointMode(False)
        if tag.GetDataCount() != n or tag.IsPerPointColor():
            tag.Remove()
            return None
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


# simulation / expression tags that must never ride along onto GlyphIt's OUTPUT: a Cloth tag
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


def remove_other_uvw(po, keep=(TAG_NAME, ID_TAG_NAME, PALETTE_UV_NAME)):
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
                    write_id=False, cell_sel=False, strip_uvs=True, progress=None, values="sample",
                    img_boxes="sample"):
    """Compute + write. Returns stats dict. `values`: "sample" (use src) or a precomputed list/None.
    `img_boxes`: per-polygon source-UV rectangles for the image source ("sample" = read them from po,
    which still carries its own UVW tag; a merged mesh gets them passed in)."""
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
    col = opts.get("color")
    img = opts.get("image")
    boxes, img_rgb, img_err = None, None, None
    if img and (src == SRC_IMAGE or (col and int(col.get("mode", 0)) == COLOR_IMAGE)):
        try:
            boxes = image_boxes(po, P, F)[0] if isinstance(img_boxes, str) else img_boxes
            img_rgb = image_sample(img, boxes)
        except Exception as e:
            img_err = str(e)
            print("GlyphIt image:", e)
    if src == SRC_IMAGE and isinstance(values, str):
        vals = luminance(img_rgb)
    else:
        vals = sample_values(po, mg, P, F, src, opts, caller, doc) if isinstance(values, str) else values
    if vals is not None and prm.get("assign", 0) == 0 and not prm.get("knockout"):
        # a value source with "Even" distribution -> value-driven. Pictures map brightness literally
        # (Value): equalising would push a mostly-white clock face into every glyph.
        prm["assign"] = 2 if src == SRC_IMAGE else 3
        # (with Knockout on, Even stays random: the value then only masks where glyphs appear)
    if vals is None and prm.get("assign", 0) in (2, 3):
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
    if img_rgb is not None:
        res["img_rgb"] = img_rgb
    write_color_tag(po, glyph_colors(res, col) if col else None)
    if col:
        write_palette_uv(po, color_keys(res, col))
    t4 = time.time()
    c = res["counts"]
    return dict(polys=len(F), islands=res["islands"], cells=len(c), min=min(c), max=max(c),
                t_read=round(t1 - t0, 3), t_values=round(t2 - t1, 3), t_solve=round(t3 - t2, 3),
                t_write=round(t4 - t3, 3), numpy=_np is not None, keys=(res["cell"], res["island"], boxes),
                img_error=img_err)


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
    """The plate builder module (Pillow-based). `folder` is ignored (kept for old callers)."""
    from . import plates
    return plates
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
            print("GlyphIt: Pillow plate failed (%s), using GeClipMap fallback" % e)
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
            print("GlyphIt: node material swap skipped:", e)
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


def clear_plate_cache(root):
    """Delete GlyphIt's generated files: library/_custom (custom plates, palettes),
    library/_mixed and collections/*/_built. Built-in library plates and the user's own collection
    images are never touched. Returns the number of files removed."""
    import os
    lib, cdir = plate_dirs(root)
    dirs = [os.path.join(lib, "_custom"), os.path.join(lib, "_mixed")]
    try:
        for c in os.listdir(cdir):
            dirs.append(os.path.join(cdir, c, "_built"))
    except Exception:
        pass
    n = 0
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if os.path.isfile(fp) and f.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr")):
                try:
                    os.remove(fp)
                    n += 1
                except Exception as e:
                    print("GlyphIt cache:", e)
    return n


def refresh_generators(doc):
    """Re-point plates + palettes of every GlyphIt in doc (after Clear Plate Cache)."""
    n = 0
    stack = [doc.GetFirstObject()] if doc else []
    while stack:
        o = stack.pop()
        while o is not None:
            if is_plugin_object(o) or o.GetType() == c4d.Opython:
                names = set()
                if not is_plugin_object(o):
                    try:
                        names = {bc[c4d.DESC_NAME] for _d, bc in o.GetUserDataContainer()}
                    except Exception:
                        names = set()
                if is_plugin_object(o) or ("Plates Folder" in names and "Clear Plate Cache" in names):
                    p_ = current_plate(o)
                    if p_:
                        swap_plate(o, p_, plate_dirs(_ud(o)("Plates Folder", "") or "")[0])
                    sync_material_color(o)
                    n += 1
            if o.GetDown() is not None:
                stack.append(o.GetDown())
            o = o.GetNext()
    return n


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


def font_data(name):
    """PostScript font name -> c4d.FontData for the font chooser."""
    from c4d.bitmaps import GeClipMap
    fd = c4d.FontData()
    try:
        desc = GeClipMap.GetFontDescription(name or "Menlo-Bold", c4d.GE_FONT_NAME_POSTSCRIPT)
        if desc is not None:
            fd.SetFont(desc)
    except Exception:
        pass
    return fd


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
        # 'Family||Style||names...' -> plates.resolve_font (CoreText on macOS)
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
                "Then press 'Refresh List' on the GlyphIt Plate tab and pick this folder.\n")
    return path


_COLL_CACHE = {}


def collection_list(op):
    """Collections for op's Plates Folder (the dropdown entries). Cached by the folder's mtime:
    GetDDescription asks for it on every Attribute Manager redraw."""
    import os
    g = param_getter(op)
    cdir = plate_dirs(g("Plates Folder", "") or "")[1]
    try:
        mt = os.path.getmtime(cdir)
    except Exception:
        mt = None
    hit = _COLL_CACHE.get(cdir)
    if hit is not None and hit[0] == mt:
        return hit[1]
    names = collection_names(cdir) or []
    _COLL_CACHE[cdir] = (mt, names)
    return names


def set_cycle_items(op, name, items, keep=True):
    """Rebuild a dropdown's entries; keeps the selected entry by name when possible.
    Plugin object: the Collection dropdown is rebuilt from disk in GetDDescription, so only the
    selection is re-synced here (by the remembered collection name)."""
    if is_plugin_object(op):
        if name == "Collection":
            from .params import NAME_TO_ID
            want = op[NAME_TO_ID["Collection Name"]] if keep else ""
            idx = items.index(want) if (items and want in items) else 0
            op[NAME_TO_ID["Collection"]] = idx
            op[NAME_TO_ID["Collection Name"]] = items[idx] if items else ""
            op.SetDirty(c4d.DIRTYFLAGS_DESCRIPTION)
        return True
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
    if is_plugin_object(op):
        from .params import NAME_TO_ID, EXTRA
        if name == "Collection":
            items = collection_list(op)
        else:
            items = EXTRA.get(name, {}).get("items") or []
        try:
            i = int(op[NAME_TO_ID[name]])
        except Exception:
            return ""
        return items[i] if 0 <= i < len(items) else ""
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
    if is_plugin_object(op):
        from .params import NAME_TO_ID
        dl, db = NAME_TO_ID["Quadtree Levels"], NAME_TO_ID["Quadtree Block (polys)"]
        lv, bi = int(op[dl] or 1), int(op[db] or 0)
        if bi < lv - 1:
            if changed == "Quadtree Levels":
                op[db] = lv - 1
            else:
                op[dl] = bi + 1
        return {"Grid (N x N)": GRID_MAX, "Quadtree Levels": QT_LEVELS_MAX}
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
    """Cloth / soft bodies: put GlyphIt's UV + colour tags straight onto the editable source,
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
                "GlyphIt.\nMake it editable first, or use Bake to Polygon Object.")
    d.StartUndo()
    d.AddUndo(c4d.UNDOTYPE_CHANGE, src)
    for t in list(src.GetTags()):      # old GlyphIt tags + the source's own UVs (first UVW wins)
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
    src.Remove()                       # (the CHANGE undo above already covers the move)
    d.InsertObject(src, pred=op)
    src.SetMg(mg)
    d.AddUndo(c4d.UNDOTYPE_CHANGE_SMALL, op)
    op[c4d.ID_BASEOBJECT_GENERATOR_FLAG] = False
    d.EndUndo()
    d.SetActiveObject(src)
    c4d.EventAdd()
    c4d.gui.StatusSetText("GlyphIt: UVs written onto %s - its simulation now drives the glyphs directly" % src.GetName())
    return None


# ---------------------------------------------------------------- look-dev ---
def _rs_glyph_color(g, tex, port, mode):
    """Vertex Attribute 'GlyphIt Color' (the generator's Color tab) -> glyph colour.
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


def build_plate_material(doc, target, plate_path, name="GlyphIt Plate", mode="emissive",
                         color=(1.0, 1.0, 1.0), emission=1.0, base_tint=0.0, glyph_color=True, assign=True):
    """Redshift node material driven by a plate. Assigned to `target` with UVW projection
    (uses the FIRST UVW tag = GlyphIt UV).

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
    """Add the Color-tab hook to an existing GlyphIt plate material (Redshift or Octane).
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


def build_plate_material_octane(doc, target, plate_path, name="GlyphIt Plate (Octane)", mode="hologram",
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
    _oct_clean(mat, emission if emission <= 3.0 else 1.0)
    if assign:
        assign_material(target, mat, replace=False, new_tag=True)
    c4d.EventAdd()
    return mat


def _oct_glyph_color(mat):
    """Octane colour hook = the palette texture read through UV set 2 (see write_palette_uv).
    (Octane's Attribute Texture reads the vertex colour tag per POINT, averaging neighbouring
    polygons - colours smeared across glyphs. The palette keeps every glyph one flat colour.)"""
    return _oct_palette(mat, None)


def _ensure_octane_tag(op):
    """Octane Object tag with Force Updates on: without it the Live Viewer treats the generator as
    'movable' and never re-sends its geometry (new UVs / palette keys) until it is toggled."""
    try:
        t = op.GetTag(1029603)
        if t is None:
            t = op.MakeTag(1029603)
            t.SetName("GlyphIt Octane updates")
        if t[1307] != 1:
            t[1307] = 1                    # OBJECTTAG_FORCE_UPDATES: Enabled
    except Exception as e:
        print("GlyphIt: Octane object tag:", e)


def _oct_palette(mat, path):
    IMG, MUL, TEXEM, PROJ, ATTR = 1029508, 1029516, 1029642, 1031460, 1056908
    pal = None
    sh = mat.GetFirstShader()
    while sh is not None:
        if sh.GetName() == "gg_palette" and sh.GetType() == IMG:
            pal = sh
        sh = sh.GetNext()
    if pal is None:
        pal = c4d.BaseShader(IMG)
        pal.SetName("gg_palette")
        mat.InsertShader(pal)
        proj = c4d.BaseShader(PROJ)
        proj.SetName("gg_palette_uv")
        mat.InsertShader(proj)
        proj[1360] = 2                     # Texture projection: Mesh UV
        proj[1388] = 2                     # UV set 2 = 'GlyphIt Palette UV'
        pal[c4d.IMAGETEXTURE_PROJECTION_LINK] = proj
        pal[1118] = 0                      # Color space: non-colour data (raw = same as vertex colours)
        pal[c4d.IMAGETEX_BORDER_MODE] = 3  # clamp
        pal[c4d.IMAGETEX_BORDER_MODE_V] = 3
        # wire it where the colour goes, replacing an old Attribute Texture hook
        em = mat[c4d.OCT_MATERIAL_EMISSION]
        if em is not None and em.GetType() == TEXEM:
            cur = em[c4d.TEXEMISSION_EFFIC_OR_TEX]
            if cur is not None and cur.GetType() == MUL:               # emissive: plate x colour
                cur[c4d.MULTIPLY_TEXTURE2] = pal
            else:                                                       # hologram
                em[c4d.TEXEMISSION_EFFIC_OR_TEX] = pal
        else:
            dl = mat[c4d.OCT_MATERIAL_DIFFUSE_LINK]
            if dl is not None and dl.GetType() == MUL:
                dl[c4d.MULTIPLY_TEXTURE2] = pal
    if path:
        if pal[c4d.IMAGETEXTURE_FILE] != path:
            pal[c4d.IMAGETEXTURE_FILE] = path
    mat.Message(c4d.MSG_UPDATE)
    return pal

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


def is_plugin_object(op):
    try:
        from .params import PLUGIN_ID
        return op is not None and op.GetType() == PLUGIN_ID
    except Exception:
        return False


def param_getter(op):
    """Read Glyph It parameters BY NAME from either the plugin object (fixed ids, see params.SPEC)
    or a legacy GlyphGrid Python Generator (user data with the same names)."""
    if is_plugin_object(op):
        from .params import NAME_TO_ID, LEGACY_NAMES

        def g(k, d=None):
            k = LEGACY_NAMES.get(k, k)
            i = NAME_TO_ID.get(k)
            if i is None:
                return d
            try:
                v = op[i]
            except Exception:
                return d
            return d if v is None and d is not None and not isinstance(d, (bool, int, float, str)) else (
                v if v is not None else d)
        return g
    U = {bc[c4d.DESC_NAME]: did for did, bc in op.GetUserDataContainer()
         if did[-1].dtype not in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT)}

    def g(k, d=None):
        try:
            return op[U[k]] if k in U else d
        except Exception:
            return d
    return g


def param_setter(op):
    """Write a parameter by name on either kind of object."""
    if is_plugin_object(op):
        from .params import NAME_TO_ID, LEGACY_NAMES

        def s_(k, v):
            k = LEGACY_NAMES.get(k, k)
            if k not in NAME_TO_ID:
                raise KeyError(k)
            op[NAME_TO_ID[k]] = v
        return s_
    U = {bc[c4d.DESC_NAME]: did for did, bc in op.GetUserDataContainer()
         if did[-1].dtype not in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT)}

    def s_(k, v):
        if k not in U:
            raise KeyError(k)
        op[U[k]] = v
    return s_


_ud = param_getter


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
        print("GlyphIt plate:", e)
        return None


def _color_opts(op):
    """The Color tab as the dict color_table() takes (same as the generator's opts['color'])."""
    g = _ud(op)
    gc = g("Glyph Color", None)
    return dict(mode=int(g("Color Mode", 0)), base=(gc.x, gc.y, gc.z) if gc is not None else (1.0, 1.0, 1.0),
                amount=float(g("Random Amount", 0.35)), hue_spread=float(g("Hue Spread", 1.0)),
                saturation=float(g("Random Saturation", 0.8)), brightness=float(g("Brightness Jitter", 0.0)),
                seed=int(g("Color Seed", 1)))


def _glyph_rgb(op):
    g = _ud(op)
    c = g("Glyph Color", None)
    return (c.x, c.y, c.z) if c is not None else (1.0, 1.0, 1.0)


PROXY_NAME = "GlyphIt Color Proxy"
PROXY_MARK = 1071110          # BaseContainer bool on the proxy object


def is_color_proxy(o):
    try:
        return o is not None and o.IsInstanceOf(c4d.Opolygon) and bool(o.GetDataInstance().GetBool(PROXY_MARK))
    except Exception:
        return False


def color_proxy(op, create=False):
    """The Standard renderer can't read a vertex colour tag that lives inside a generator's cache
    (a Vertex Map shader linked to it renders white: the link doesn't survive the render clone).
    It CAN read one on a different, hidden polygon object with the same polygon count - the
    shader samples the linked tag by polygon index. So the Standard material links its Vertex
    Map shader to the 'GlyphIt Color' tag on this proxy: a hidden last child of the generator,
    one dummy point, as many (degenerate) polygons as the output, kept in sync by
    sync_color_proxy() on the main thread. Redshift / Octane don't need it."""
    ch = op.GetDown()
    while ch is not None:
        if is_color_proxy(ch):
            return ch
        ch = ch.GetNext()
    if not create:
        return None
    pr = c4d.PolygonObject(1, 0)
    pr.SetName(PROXY_NAME)
    pr.GetDataInstance().SetBool(PROXY_MARK, True)
    pr[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = c4d.OBJECT_OFF
    pr[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = c4d.OBJECT_OFF
    pr[c4d.ID_BASEOBJECT_XRAY] = False
    pr.InsertUnderLast(op)
    try:
        pr.ChangeNBit(c4d.NBIT_OHIDE, c4d.NBITCONTROL_SET)   # keep it out of the Object Manager
    except Exception:
        pass
    return pr


def proxy_color_tag(pr):
    t = pr.GetFirstTag() if pr is not None else None
    while t is not None:
        if t.CheckType(c4d.Tvertexcolor) and t.GetName() == COLOR_TAG_NAME:
            return t
        t = t.GetNext()
    return None


def sync_color_proxy(op, S, force=False):
    """Copy the kept result's colour tag onto the proxy (main thread). Cheap when nothing changed:
    S['col_stamp'] is bumped by the generator whenever it (re)writes colours."""
    pr = color_proxy(op)
    if pr is None:
        return False
    if not force and S.get("proxy_stamp") == S.get("col_stamp") and proxy_color_tag(pr) is not None:
        return False
    res = S.get("result")
    src = None
    o = res.GetDown() if res is not None else None
    while o is not None:                       # merged output: one polygon object carries the tag
        if o.IsInstanceOf(c4d.Opolygon):
            t = o.GetFirstTag()
            while t is not None:
                if t.CheckType(c4d.Tvertexcolor) and t.GetName() == COLOR_TAG_NAME and not t.IsPerPointColor():
                    src = (o, t)
                    break
                t = t.GetNext()
            if src:
                break
        o = o.GetNext()
    if src is None:
        return False
    po, st = src
    n = po.GetPolygonCount()
    if pr.GetPolygonCount() != n or pr.GetPointCount() != 1:
        pr.ResizeObject(1, n)              # polygons stay (0,0,0,0): a tag host, never drawn
    tag = proxy_color_tag(pr)              # the tag resizes with its object; the material's
    if tag is not None and tag.IsPerPointColor():     # link to it must survive, so never recreate
        tag.SetPerPointMode(False)         # it unless we have to (then re-link the materials)
    if tag is None or tag.GetDataCount() != n:
        old_tag = tag
        tag = c4d.VertexColorTag(1)
        tag.SetName(COLOR_TAG_NAME)
        pr.InsertTag(tag)
        tag.SetPerPointMode(False)
        if tag.GetDataCount() != n:
            tag.Remove()
            return False
        d_ = pr.GetDocument()
        for m in (d_.GetMaterials() if d_ is not None else []):
            sh = m.GetFirstShader() if m.GetType() == c4d.Mmaterial else None
            while sh is not None:
                if sh.GetType() == c4d.Xvertexmap and (sh[1001] is None or sh[1001] == old_tag
                                                       or sh[1001].GetObject() is None):
                    sh[1001] = tag
                    m.Message(c4d.MSG_UPDATE)
                sh = sh.GetNext()
        if old_tag is not None:
            old_tag.Remove()
    r = st.GetLowlevelDataAddressR()
    w = tag.GetLowlevelDataAddressW()
    if r is None or w is None or len(r) != len(w):
        return False
    w[:] = r                      # raw copy only: no dirty flag, so the generator isn't rebuilt
    S["proxy_stamp"] = S.get("col_stamp")
    return True


def link_standard_to_proxy(mat, op):
    """Point the Standard material's luminance Vertex Map shader at op's colour proxy (creating
    the proxy). Returns the shader."""
    pr = color_proxy(op, create=True)
    sh = mat[c4d.MATERIAL_LUMINANCE_SHADER]
    if sh is None or sh.GetType() != c4d.Xvertexmap:
        sh = c4d.BaseShader(c4d.Xvertexmap)
        sh.SetName("gg_color")
        sh[1003] = 0                       # colour space 0 = matches Redshift / Octane 1:1 (tested)
        mat[c4d.MATERIAL_LUMINANCE_SHADER] = sh
        mat.InsertShader(sh)
    tag = proxy_color_tag(pr)
    if tag is None:
        tag = c4d.VertexColorTag(1)
        tag.SetName(COLOR_TAG_NAME)
        pr.InsertTag(tag)
        tag.SetPerPointMode(False)
    sh[1001] = tag
    mat.Message(c4d.MSG_UPDATE)
    return sh


def build_plate_material_standard(doc, target, plate_path, name="GlyphIt Standard", color=(1.0, 1.0, 1.0),
                                  emission=1.0, assign=True):
    """Cinema 4D Standard/Physical material: dark diffuse, the plate knocks out opacity (Alpha
    channel, black = gone), Luminance = the per-glyph colours (Vertex Map shader -> the colour
    proxy, see color_proxy) with the Color tab's Glyph Color as the viewport / fallback colour."""
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
    if target is not None and is_plugin_object(target) and target.GetDocument() is not None:
        try:
            link_standard_to_proxy(mat, target)
        except Exception as e:
            print("GlyphIt colour proxy:", e)
    mat.Message(c4d.MSG_UPDATE)
    if assign:
        assign_material(target, mat)
    return mat


def build_glyph_material(doc, op, renderer="redshift"):
    """Color tab buttons: a generic glyph material for the chosen renderer, assigned to op.
    Diffuse body, the current plate knocks out opacity, emission = the Color tab (Glyph Color /
    Color Mode via the 'GlyphIt Color' tag; Standard gets the picked colour)."""
    plate = current_plate(op)
    if not plate:
        c4d.gui.MessageDialog("GlyphIt: set the Plates Folder (Plate tab) first.")
        return None
    col = _glyph_rgb(op)
    em = float(_ud(op)("Emission Strength", 1.0))
    name = {"standard": "GlyphIt Standard", "octane": "GlyphIt Octane"}.get(renderer, "GlyphIt Redshift")
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
            c4d.gui.StatusSetText("GlyphIt: switched to %s" % name)
            return m_
    if renderer == "octane" and c4d.plugins.FindPlugin(1029501, c4d.PLUGINTYPE_MATERIAL) is None:
        c4d.gui.MessageDialog("GlyphIt: Octane isn't installed - use Standard or Redshift.")
        return None
    if renderer == "redshift" and c4d.plugins.FindPlugin(1036219, c4d.PLUGINTYPE_VIDEOPOST) is None:
        c4d.gui.MessageDialog("GlyphIt: Redshift isn't available - use Standard or Octane.")
        return None
    mat = None
    doc.StartUndo()
    try:
        if renderer == "standard":
            mat = build_plate_material_standard(doc, op, plate, color=col, emission=em, assign=False)
        elif renderer == "octane":
            mat = build_plate_material_octane(doc, op, plate, name="GlyphIt Octane", mode="hologram", color=col,
                                              emission=em, base=0.05, glyph_color=True, assign=False)
        else:
            mat = build_plate_material(doc, op, plate, name="GlyphIt Redshift", mode="hologram", color=col,
                                       emission=em, glyph_color=True, assign=False)
        if mat is not None:
            doc.AddUndo(c4d.UNDOTYPE_NEW, mat)
            doc.AddUndo(c4d.UNDOTYPE_CHANGE, op)
            assign_material(op, mat)
    except Exception as e:
        print("GlyphIt material:", e)
        c4d.gui.MessageDialog("GlyphIt: couldn't build the %s material:\n%s" % (renderer, e))
    finally:
        doc.EndUndo()
    if mat is None:
        return None
    sync_material_color(op, force=True)
    c4d.EventAdd()
    c4d.gui.StatusSetText("GlyphIt: %s assigned - colour follows the Color tab" % mat.GetName())
    return mat


def sync_material_color(op, force=False):
    """Keep op's GlyphIt materials on the Color tab: Standard luminance colour + brightness,
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
            _ensure_octane_tag(op)
            _oct_palette(mat, palette_file(plate_dirs(g("Plates Folder", "") or "")[0], _color_opts(op)))
            _oct_clean(mat, em)
            n += 1
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
            print("GlyphIt colour sync (RS):", e)
        if not rs_done and mat[c4d.MATERIAL_USE_LUMINANCE] and _has_plate_shader(mat):
            mat[c4d.MATERIAL_LUMINANCE_COLOR] = c4d.Vector(*col)
            mat[c4d.MATERIAL_LUMINANCE_BRIGHTNESS] = em
            if is_plugin_object(op) and op.GetDocument() is not None:
                sh = mat[c4d.MATERIAL_LUMINANCE_SHADER]
                pr = color_proxy(op)
                if sh is None or sh.GetType() != c4d.Xvertexmap or pr is None or sh[1001] is None \
                        or sh[1001].GetObject() != pr:
                    try:
                        link_standard_to_proxy(mat, op)      # proxy deleted / older scene: re-link
                    except Exception as e:
                        print("GlyphIt colour proxy:", e)
            mat.Message(c4d.MSG_UPDATE)
            n += 1
    c4d.EventAdd()
    return n


def _oct_clean(mat, em=1.0):
    """Make an Octane GlyphIt material behave like the Redshift / Standard ones.
    - The plate is a MASK: read it as non-colour data. Read as sRGB, Octane linearised the
      anti-aliased edges, so glyphs came out thin and wormy.
    - Emission = a flat colour, not a light. Octane's texture emission is a real light source:
      power spread over the whole mesh, importance-sampled, lighting its neighbours = dim
      speckled colour noise. Surface brightness on, sampling rate 0, invisible to diffuse /
      specular rays, no shadows, double sided -> clean per-glyph colour like Redshift."""
    IMG, TEXEM = 1029508, 1029642
    sh = mat.GetFirstShader()
    while sh is not None:
        if sh.GetType() == IMG and (sh.GetName() == "gg_plate" or "plates" in str(sh[c4d.IMAGETEXTURE_FILE])):
            sh[1118] = 0                                  # Color space: Non-color data
        sh = sh.GetNext()
    em_sh = mat[c4d.OCT_MATERIAL_EMISSION]
    if em_sh is not None and em_sh.GetType() == TEXEM:
        em_sh[c4d.TEXEMISSION_SURFACE_BRIGHTNESS] = True
        em_sh[c4d.TEXEMISSION_SAMPL_RATE] = 0.0
        em_sh[c4d.TEXEMISSION_VISIBLE_ON_DIFFUSE] = False
        em_sh[c4d.TEXEMISSION_VISIBLE_ON_SPECULAR] = False
        em_sh[c4d.TEXEMISSION_CAST_SHADOWS] = False
        em_sh[c4d.TEXEMISSION_DOUBLE_SIDED] = True
        em_sh[c4d.TEXEMISSION_POWER] = float(em)
    mat.Message(c4d.MSG_UPDATE)


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
