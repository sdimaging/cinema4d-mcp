"""
GlyphGrid core: every polygon (or ngon / cluster) becomes its own UV island,
fitted into one cell of an N x N atlas (1, 4, 9, 16 ... "up").

Pure Python, no c4d / numpy dependency, so it runs inside Cinema 4D's bundled
Python *and* can be unit-tested anywhere.  The C4D glue (glyphgrid_c4d.py)
feeds it points/polys and writes the result into a UVW tag.

Coordinates
-----------
* points  : list of (x, y, z) in OBJECT space
* polys   : list of (a, b, c, d) point indices, c == d for triangles (C4D style)
* up      : object-space vector that should read as "up" on the glyph
* UV      : C4D convention, (0,0) = top-left of the image, V grows downward.
            Cell 0 is the top-left cell, cells run left->right, top->bottom.

Result
------
compute() returns a dict with
  "uv"      : flat, 8 floats per polygon (a.u a.v b.u b.v c.u c.v d.u d.v) = C4D raw UVW layout
  "cell"    : cell index per polygon
  "value"   : final (remapped) value per polygon, 0..1
  "rand"    : per-island random 0..1 (same for every poly of an island)
  "island"  : island id per polygon
  "counts"  : polygons per cell
  "islands" : number of islands
"""

import math
import random

try:  # optional: 20-50x faster on big meshes (pip install numpy into c4dpy)
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

# ----------------------------------------------------------------- params ---
ORIENT_UP, ORIENT_RANDOM90, ORIENT_RANDOM_FREE, ORIENT_EDGE = 0, 1, 2, 3
FIT_AUTO, FIT_UNIFORM, FIT_FLUSH = 0, 1, 2
ISLAND_POLY, ISLAND_NGON, ISLAND_CLUSTER, ISLAND_QUADTREE = 0, 1, 2, 3
GRID_SINGLE, GRID_MIXED = 0, 1
MIX_LEVELS = (1, 2, 3, 4)          # mixed atlas quadrants: TL 1-up, TR 4-up, BL 9-up, BR 16-up
MIX_UNITS = 24                      # 2 quadrants x lcm(1,2,3,4)
ASSIGN_EVEN, ASSIGN_WEIGHTED, ASSIGN_VALUE, ASSIGN_VALUE_EQUALIZED = 0, 1, 2, 3
UVMODE_ATLAS, UVMODE_ENCODED = 0, 1

DEFAULTS = dict(
    grid=2,                 # N  -> N*N cells (1=1up, 2=4up, 3=9up, 4=16up ...)
    seed=12345,
    orient=ORIENT_UP,
    up=(0.0, 1.0, 0.0),     # object-space up
    fallback_up=(0.0, 0.0, 1.0),  # used when a face looks straight along `up`
    rot_jitter=0.0,         # extra random rotation, degrees (+/-)
    mirror_random=False,    # randomly mirror glyphs horizontally
    flip_mirror=False,      # global mirror fix (handedness)
    fit=FIT_AUTO,
    flush_aspect=1.35,      # quads up to this aspect snap flush to the cell corners
    gutter=0.02,            # fraction of a cell kept empty on every side
    glyph_scale=1.0,        # 1 = fill the cell (inside the gutter)
    island=ISLAND_NGON,
    cluster_size=0.0,       # world units, ISLAND_CLUSTER only
    qt_size=100.0,          # ISLAND_QUADTREE: largest block (world units)
    qt_levels=3,            #   block sizes qt_size, /2, /4 ... then single polygons
    qt_split=0.5,           #   chance a block splits into its 4 children
    grid_mode=GRID_SINGLE,  # GRID_MIXED: polygons draw from 1/4/9/16-up at once (composite atlas)
    level_weights=None,     #   relative share per level [1-up, 4-up, 9-up, 16-up], missing = 1
    assign=ASSIGN_EVEN,
    weights=None,           # list len N*N, ASSIGN_WEIGHTED
    invert=False,
    gamma=1.0,
    contrast=1.0,           # around 0.5
    offset=0.0,
    wrap=False,
    cell_shift=0,           # integer, rotates every island's cell (animatable)
    random_mix=0.0,         # blend value toward per-island random
    dither=0.0,             # 0..1, +/- half a cell of noise before quantising
    uv_mode=UVMODE_ATLAS,
    encode_levels=256,      # ENCODED: integer part carries value (U) and random (V)
)


# ------------------------------------------------------------- vec helpers ---
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    l = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    if l < 1e-20:
        return (0.0, 0.0, 0.0), 0.0
    return (a[0] / l, a[1] / l, a[2] / l), l


def _poly_ids(p):
    a, b, c, d = p
    return (a, b, c) if c == d else (a, b, c, d)


def _frame(n, up, fallback):
    """Tangent frame (right, upv) on the plane with normal n; upv = up projected."""
    u = _sub(up, tuple(n[i] * _dot(up, n) for i in range(3)))
    u, l = _norm(u)
    if l < 1e-4:
        u = _sub(fallback, tuple(n[i] * _dot(fallback, n) for i in range(3)))
        u, l = _norm(u)
        if l < 1e-6:
            u, _ = _norm(_cross(n, (1.0, 0.0, 0.0)))
    r, _ = _norm(_cross(n, u))
    return r, u


# ----------------------------------------------------------------- islands ---
def _centroid_dir(points, p):
    ids = _poly_ids(p)
    k = len(ids)
    cx = sum(points[j][0] for j in ids) / k
    cy = sum(points[j][1] for j in ids) / k
    cz = sum(points[j][2] for j in ids) / k
    pa, pb, pc = points[ids[0]], points[ids[1]], points[ids[2]]
    if k == 4:
        nn = _cross(_sub(points[ids[2]], pa), _sub(points[ids[3]], pb))
    else:
        nn = _cross(_sub(pb, pa), _sub(pc, pa))
    ax = max(range(3), key=lambda q: abs(nn[q]))
    return cx, cy, cz, ax * 2 + (1 if nn[ax] < 0 else 0)   # 6 facing buckets


def _h01(*key):
    """Deterministic 0..1 hash of a tuple of ints (int/tuple hashing is not salted in Python)."""
    h = hash(key) & 0xFFFFFFFF
    h = (h ^ (h >> 16)) * 0x45D9F3B & 0xFFFFFFFF
    h = (h ^ (h >> 16)) * 0x45D9F3B & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / float(0x1000000)


def build_islands(points, polys, mode=ISLAND_POLY, ngon_map=None, cluster_size=0.0,
                  qt_size=100.0, qt_levels=3, qt_split=0.5, seed=0):
    """Returns island id per polygon (list) and the island count."""
    n = len(polys)
    if mode == ISLAND_QUADTREE and qt_size > 0.0:
        # world-space quadtree: a block of size S either stays one island or splits into its
        # 2x2x2 children (S/2) with chance qt_split; after qt_levels the leaves are single polys.
        remap, out = {}, [0] * n
        L = max(1, int(qt_levels))
        for i, p in enumerate(polys):
            cx, cy, cz, dom = _centroid_dir(points, p)
            key = None
            size = float(qt_size)
            for lvl in range(L):
                k = (lvl, int(math.floor(cx / size)), int(math.floor(cy / size)), int(math.floor(cz / size)), dom)
                if _h01(seed, *k) >= qt_split:
                    key = k
                    break
                size *= 0.5
            if key is None:
                key = ("p", i)
            out[i] = remap.setdefault(key, len(remap))
        return out, len(remap)
    if mode == ISLAND_NGON and ngon_map is not None and len(ngon_map) == n:
        # C4D GetPolygonTranslationMap(): poly -> ngon index (ngons + plain polys)
        remap, out = {}, [0] * n
        for i, g in enumerate(ngon_map):
            out[i] = remap.setdefault(g, len(remap))
        return out, len(remap)
    if mode == ISLAND_CLUSTER and cluster_size > 0.0:
        inv = 1.0 / cluster_size
        remap, out = {}, [0] * n
        for i, p in enumerate(polys):
            ids = _poly_ids(p)
            k = len(ids)
            cx = sum(points[j][0] for j in ids) / k
            cy = sum(points[j][1] for j in ids) / k
            cz = sum(points[j][2] for j in ids) / k
            pa, pb, pc = points[ids[0]], points[ids[1]], points[ids[2]]
            if k == 4:
                nn = _cross(_sub(points[ids[2]], pa), _sub(points[ids[3]], pb))
            else:
                nn = _cross(_sub(pb, pa), _sub(pc, pa))
            ax = max(range(3), key=lambda q: abs(nn[q]))
            dom = ax * 2 + (1 if nn[ax] < 0 else 0)  # 6 direction buckets
            key = (int(math.floor(cx * inv)), int(math.floor(cy * inv)), int(math.floor(cz * inv)), dom)
            out[i] = remap.setdefault(key, len(remap))
        return out, len(remap)
    return list(range(n)), n


# ---------------------------------------------------------- cell assignment ---
def _remap_value(v, prm):
    if prm["invert"]:
        v = 1.0 - v
    c = prm["contrast"]
    if c != 1.0:
        v = (v - 0.5) * c + 0.5
    v += prm["offset"]
    if prm.get("wrap"):          # animate Offset with Wrap on -> glyphs cycle across the surface
        v -= math.floor(v)
    v = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
    g = prm["gamma"]
    if g != 1.0 and v > 0.0:
        v = v ** (1.0 / g)
    return v


def assign_cells(n_isl, ncell, prm, rng, isl_value=None, isl_rand=None):
    mode = prm["assign"]
    cells = [0] * n_isl
    if ncell == 1 or n_isl == 0:
        return cells, [0.0] * n_isl
    order = list(range(n_isl))
    rng.shuffle(order)

    if mode in (ASSIGN_VALUE, ASSIGN_VALUE_EQUALIZED) and isl_value is not None:
        vals = [0.0] * n_isl
        rm, dz = prm["random_mix"], prm["dither"]
        for i in range(n_isl):
            v = _remap_value(isl_value[i], prm)
            if rm:
                v = v * (1.0 - rm) + isl_rand[i] * rm
            vals[i] = v
        if mode == ASSIGN_VALUE_EQUALIZED:
            # sort by value (random tiebreak via the shuffled order) -> exactly even counts
            pos = {isl: k for k, isl in enumerate(order)}
            srt = sorted(range(n_isl), key=lambda i: (vals[i] + (isl_rand[i] - 0.5) * dz / ncell, pos[i]))
            for rank, i in enumerate(srt):
                cells[i] = min(ncell - 1, rank * ncell // n_isl)
        else:
            for i in range(n_isl):
                v = vals[i] + (isl_rand[i] - 0.5) * dz / ncell
                c = int(v * ncell)
                cells[i] = 0 if c < 0 else (ncell - 1 if c >= ncell else c)
        return cells, vals

    if mode == ASSIGN_WEIGHTED and prm.get("weights"):
        # relative amounts in plate order; cells without an entry count as 1
        w = list(prm["weights"])[:ncell] + [1.0] * max(0, ncell - len(prm["weights"]))
        tot = sum(max(0.0, x) for x in w) or 1.0
        raw = [max(0.0, x) / tot * n_isl for x in w]
        cnt = [int(x) for x in raw]
        rem = n_isl - sum(cnt)
        for k in sorted(range(ncell), key=lambda k: raw[k] - cnt[k], reverse=True)[:rem]:
            cnt[k] += 1
        k, filled = 0, 0
        for isl in order:
            while filled >= cnt[k]:
                k, filled = k + 1, 0
            cells[isl] = k
            filled += 1
    else:  # ASSIGN_EVEN: shuffled rank mod ncell -> counts differ by at most 1
        for rank, isl in enumerate(order):
            cells[isl] = rank % ncell
    return cells, [c / max(1, ncell - 1) for c in cells]


# --------------------------------------------------------------- the solver ---
_CORNERS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))  # TL TR BR BL in (s,t), t down


def _flush_corners(xs, ys):
    """Corner index (into _CORNERS) for each of 4 projected verts (x right, y up).
    The top-left-most vertex gets TL, winding decides the direction around the cell."""
    hx = max(abs(x) for x in xs) or 1.0
    hy = max(abs(y) for y in ys) or 1.0
    k0 = max(range(4), key=lambda k: ys[k] / hy - xs[k] / hx)
    area = sum(xs[k] * ys[(k + 1) % 4] - xs[(k + 1) % 4] * ys[k] for k in range(4))
    out = [0] * 4
    for j in range(4):
        out[(k0 + j) % 4] = (-j) % 4 if area > 0 else j % 4
    return out


def _solve_singles_py(points, polys, singles, island, isl_cell, isl_val, isl_rand,
                      isl_rot, isl_k90, isl_mir, ctx, uv, progress=None):
    """Pure-python fast path: one polygon == one island. Everything inlined."""
    N, span, fit, lim = ctx["N"], ctx["span"], ctx["fit"], ctx["flush_lim"]
    encoded, L, gmir = ctx["encoded"], ctx["L"], ctx["gmir"]
    upx, upy, upz = ctx["up"]
    fbx, fby, fbz = ctx["fb"]
    invN = 1.0 / N
    sqrt, cos, sin = math.sqrt, math.cos, math.sin
    CORN = _CORNERS
    lim2 = lim * lim
    it = range(len(polys)) if singles is None else singles
    total = len(polys) if singles is None else len(singles)
    step = max(1, total // 50)
    for cnt, i in enumerate(it):
        a, b, c, d = polys[i]
        isl = island[i]
        A, B, C, D = points[a], points[b], points[c], points[d]
        # normal = (C-A) x (D-B)  (valid for quads and C4D triangles c==d)
        e1x, e1y, e1z = C[0] - A[0], C[1] - A[1], C[2] - A[2]
        e2x, e2y, e2z = D[0] - B[0], D[1] - B[1], D[2] - B[2]
        nx, ny, nz = e1y * e2z - e1z * e2y, e1z * e2x - e1x * e2z, e1x * e2y - e1y * e2x
        l = sqrt(nx * nx + ny * ny + nz * nz)
        if l < 1e-30:
            nx, ny, nz = 0.0, 0.0, 1.0
        else:
            nx, ny, nz = nx / l, ny / l, nz / l
        dp = upx * nx + upy * ny + upz * nz
        ux, uy, uz = upx - nx * dp, upy - ny * dp, upz - nz * dp
        l = sqrt(ux * ux + uy * uy + uz * uz)
        if l < 1e-4:
            dp = fbx * nx + fby * ny + fbz * nz
            ux, uy, uz = fbx - nx * dp, fby - ny * dp, fbz - nz * dp
            l = sqrt(ux * ux + uy * uy + uz * uz) or 1.0
        ux, uy, uz = ux / l, uy / l, uz / l
        rx, ry, rz = ny * uz - nz * uy, nz * ux - nx * uz, nx * uy - ny * ux
        tri = c == d
        P = (A, B, C) if tri else (A, B, C, D)
        xs = [p[0] * rx + p[1] * ry + p[2] * rz for p in P]
        ys = [p[0] * ux + p[1] * uy + p[2] * uz for p in P]
        mir = isl_mir[isl] != gmir
        ang = isl_rot[isl]
        k90 = isl_k90[isl]
        # cell placement constants
        if encoded:
            e = 1e-4
            sc = (1.0 - 2 * e) * span
            ox = min(L - 1, int(isl_val[isl] * L)) + 0.5
            oy = min(L - 1, int(isl_rand[isl] * L)) + 0.5
            mul = 1.0
        else:
            ox, oy = ctx["cx"][isl], ctx["cy"][isl]
            sc = span * (ctx["cs"][isl] if ctx["cs"] is not None else 1.0)
            mul = ctx["mul"]
        o = i * 8
        done = False
        if not tri and fit != FIT_UNIFORM and ang == 0.0:
            ok = fit == FIT_FLUSH
            if not ok:
                # aspect + diagonal checks (squared lengths)
                def d2(p, q):
                    return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
                w = sqrt(d2(A, B)) + sqrt(d2(C, D))
                h = sqrt(d2(B, C)) + sqrt(d2(D, A))
                q1, q2 = d2(A, C), d2(B, D)
                ok = (max(w, h) <= lim * min(w, h)) and (max(q1, q2) <= 1.5625 * min(q1, q2))
            if ok:
                cx = (max(xs) + min(xs)) * 0.5
                cy = (max(ys) + min(ys)) * 0.5
                xs = [x - cx for x in xs]
                ys = [y - cy for y in ys]
                if mir:
                    xs = [-x for x in xs]
                order = _flush_corners(xs, ys)
                for k in range(4):
                    s, t = CORN[(order[k] + k90) % 4]
                    uv[o + k * 2] = (ox + (s - 0.5) * sc) * mul
                    uv[o + k * 2 + 1] = (oy + (t - 0.5) * sc) * mul
                done = True
        if not done:
            if mir:
                xs = [-x for x in xs]
            ang += k90 * 1.5707963267948966
            if ang:
                ca, sa = cos(ang), sin(ang)
                xs, ys = [x * ca - y * sa for x, y in zip(xs, ys)], [x * sa + y * ca for x, y in zip(xs, ys)]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            ext = max(x1 - x0, y1 - y0) or 1.0
            mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
            k = sc / ext
            for j in range(len(P)):
                uv[o + j * 2] = (ox + (xs[j] - mx) * k) * mul
                uv[o + j * 2 + 1] = (oy - (ys[j] - my) * k) * mul
            if tri:
                uv[o + 6], uv[o + 7] = uv[o + 4], uv[o + 5]
        if progress and cnt % step == 0:
            progress(cnt / total)


def _solve_singles_np(points, polys, singles, island, isl_cell, isl_val, isl_rand,
                      isl_rot, isl_k90, isl_mir, ctx, uv_list):
    """numpy path: same maths as _solve_singles_py, vectorised. Returns float32 (npoly*8,)."""
    np = _np
    N, span, fit, lim = ctx["N"], ctx["span"], ctx["fit"], ctx["flush_lim"]
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    F = np.asarray(polys, dtype=np.int64).reshape(-1, 4)
    out = np.zeros((len(F), 4, 2), np.float32) if uv_list is None else np.asarray(uv_list, np.float32).reshape(-1, 4, 2)
    sel = np.arange(len(F)) if singles is None else np.asarray(singles, dtype=np.int64)
    if sel.size == 0:
        return out.reshape(-1)
    F = F[sel]
    isl = np.asarray(island, dtype=np.int64)[sel]
    V = P[F]                                   # (n,4,3)
    tri = F[:, 2] == F[:, 3]
    n = np.cross(V[:, 2] - V[:, 0], V[:, 3] - V[:, 1])
    nl = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(nl > 1e-30, n / np.maximum(nl, 1e-30), np.array([0.0, 0.0, 1.0]))
    up = np.asarray(ctx["up"], float)
    fb = np.asarray(ctx["fb"], float)
    u = up - n * (n * up).sum(1)[:, None]   # (no @: numpy 2 + Accelerate warns spuriously)
    ul = np.linalg.norm(u, axis=1, keepdims=True)
    uf = fb - n * (n * fb).sum(1)[:, None]
    u = np.where(ul > 1e-4, u, uf)
    u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-30)
    r = np.cross(n, u)
    xs = np.einsum("nkj,nj->nk", V, r)
    ys = np.einsum("nkj,nj->nk", V, u)
    xs = xs - (xs.max(1, keepdims=True) + xs.min(1, keepdims=True)) * 0.5
    ys = ys - (ys.max(1, keepdims=True) + ys.min(1, keepdims=True)) * 0.5
    mir = np.asarray(isl_mir, bool)[isl] != ctx["gmir"]
    xs = np.where(mir[:, None], -xs, xs)
    rot = np.asarray(isl_rot, float)[isl]
    k90 = np.asarray(isl_k90, np.int64)[isl]

    if ctx["encoded"]:
        e = 1e-4
        sc = (1.0 - 2 * e) * span
        L = ctx["L"]
        ox = np.minimum(L - 1, (np.asarray(isl_val, float)[isl] * L).astype(np.int64)) + 0.5
        oy = np.minimum(L - 1, (np.asarray(isl_rand, float)[isl] * L).astype(np.int64)) + 0.5
        mul = 1.0
    else:
        ox = np.asarray(ctx["cx"], float)[isl]
        oy = np.asarray(ctx["cy"], float)[isl]
        sc = span * (np.asarray(ctx["cs"], float)[isl][:, None] if ctx["cs"] is not None else 1.0)
        mul = ctx["mul"]

    # ---- flush candidates
    if fit == FIT_UNIFORM:
        flush = np.zeros(len(F), bool)
    else:
        flush = (~tri) & (rot == 0.0)
        if fit != FIT_FLUSH:
            d = lambda i, j: np.linalg.norm(V[:, i] - V[:, j], axis=1)
            w = d(0, 1) + d(2, 3)
            h = d(1, 2) + d(3, 0)
            q1, q2 = d(0, 2), d(1, 3)
            flush &= (np.maximum(w, h) <= lim * np.minimum(w, h)) & (np.maximum(q1, q2) <= 1.25 * np.minimum(q1, q2))
    s = np.zeros((len(F), 4))
    t = np.zeros((len(F), 4))
    if flush.any():
        fx, fy = xs[flush], ys[flush]
        hx = np.maximum(np.abs(fx).max(1, keepdims=True), 1e-30)
        hy = np.maximum(np.abs(fy).max(1, keepdims=True), 1e-30)
        k0 = np.argmax(fy / hy - fx / hx, axis=1)
        area = (fx * np.roll(fy, -1, 1) - np.roll(fx, -1, 1) * fy).sum(1)
        j = (np.arange(4)[None, :] - k0[:, None]) % 4       # steps from TL vertex
        corner = np.where(area[:, None] > 0, (-j) % 4, j % 4)
        corner = (corner + k90[flush][:, None]) % 4
        cs = np.array([c[0] for c in _CORNERS])
        ct = np.array([c[1] for c in _CORNERS])
        s[flush], t[flush] = cs[corner], ct[corner]
    nf = ~flush
    if nf.any():
        x, y = xs[nf], ys[nf]
        ang = rot[nf] + k90[nf] * (np.pi * 0.5)
        ca, sa = np.cos(ang)[:, None], np.sin(ang)[:, None]
        x, y = x * ca - y * sa, x * sa + y * ca
        x0, x1, y0, y1 = x.min(1), x.max(1), y.min(1), y.max(1)
        ext = np.maximum(np.maximum(x1 - x0, y1 - y0), 1e-30)[:, None]
        s[nf] = 0.5 + (x - ((x0 + x1) * 0.5)[:, None]) / ext
        t[nf] = 0.5 - (y - ((y0 + y1) * 0.5)[:, None]) / ext
    U = (ox[:, None] + (s - 0.5) * sc) * mul
    W = (oy[:, None] + (t - 0.5) * sc) * mul
    out[sel, :, 0] = U
    out[sel, :, 1] = W
    return out.reshape(-1)


def compute(points, polys, params=None, ngon_map=None, poly_values=None, progress=None):
    prm = dict(DEFAULTS)
    if params:
        prm.update({k: v for k, v in params.items() if v is not None})
    N = max(1, int(prm["grid"]))
    ncell = N * N
    npoly = len(polys)
    rng = random.Random(int(prm["seed"]))

    island, n_isl = build_islands(points, polys, prm["island"], ngon_map, prm["cluster_size"],
                                  prm["qt_size"], prm["qt_levels"], prm["qt_split"], int(prm["seed"]))
    mixed = int(prm.get("grid_mode") or 0) == GRID_MIXED and prm["uv_mode"] != UVMODE_ENCODED

    # per-island random (stable for a given seed), value = mean of poly values
    isl_rand = [rng.random() for _ in range(n_isl)]
    isl_value = None
    if poly_values is not None:
        acc, cnt = [0.0] * n_isl, [0] * n_isl
        for i in range(npoly):
            acc[island[i]] += poly_values[i]
            cnt[island[i]] += 1
        isl_value = [acc[i] / cnt[i] if cnt[i] else 0.0 for i in range(n_isl)]

    shift = int(prm.get("cell_shift") or 0)
    if not mixed:
        isl_cell, isl_val = assign_cells(n_isl, ncell, prm, rng, isl_value, isl_rand)
        if shift % ncell:  # animate Cell Shift for a global glyph cycle that keeps the distribution
            isl_cell = [(c + shift) % ncell for c in isl_cell]
        isl_cx = [(c % N) + 0.5 for c in isl_cell]
        isl_cy = [(c // N) + 0.5 for c in isl_cell]
        isl_cs = None                     # every cell is 1 unit
        unit_mul = 1.0 / N
        counts_len = ncell
    else:
        # 1) exact proportional split of the islands over the levels, 2) cells inside each level
        w = list(prm.get("level_weights") or [])[:4]
        w = [max(0.0, float(x)) for x in w] + [1.0] * (4 - len(w))
        tot = sum(w) or 1.0
        raw = [x / tot * n_isl for x in w]
        cnt = [int(x) for x in raw]
        for k in sorted(range(4), key=lambda k: raw[k] - cnt[k], reverse=True)[:n_isl - sum(cnt)]:
            cnt[k] += 1
        order = list(range(n_isl))
        rng.shuffle(order)
        isl_level = [0] * n_isl
        pos = 0
        for lv in range(4):
            for isl in order[pos:pos + cnt[lv]]:
                isl_level[isl] = lv
            pos += cnt[lv]
        isl_cell = [0] * n_isl
        isl_val = [0.0] * n_isl
        isl_cx, isl_cy, isl_cs = [0.0] * n_isl, [0.0] * n_isl, [0.0] * n_isl
        base = 0
        offsets = []
        for lv, Nl in enumerate(MIX_LEVELS):
            offsets.append(base)
            members_l = [i for i in range(n_isl) if isl_level[i] == lv]
            if members_l:
                cl, vl = assign_cells(len(members_l), Nl * Nl, prm, rng,
                                      [isl_value[i] for i in members_l] if isl_value else None,
                                      [isl_rand[i] for i in members_l])
                qx, qy = lv % 2, lv // 2
                csz = (MIX_UNITS // 2) / Nl
                for i, c, v in zip(members_l, cl, vl):
                    c = (c + shift) % (Nl * Nl)
                    isl_cell[i] = base + c
                    isl_val[i] = v
                    isl_cx[i] = qx * (MIX_UNITS // 2) + ((c % Nl) + 0.5) * csz
                    isl_cy[i] = qy * (MIX_UNITS // 2) + ((c // Nl) + 0.5) * csz
                    isl_cs[i] = csz
            base += Nl * Nl
        unit_mul = 1.0 / MIX_UNITS
        counts_len = base

    # per-island orientation choices
    orient = prm["orient"]
    jit = math.radians(prm["rot_jitter"])
    isl_rot = [0.0] * n_isl
    isl_k90 = [0] * n_isl
    isl_mir = [False] * n_isl
    rr = random.Random(int(prm["seed"]) * 7919 + 17)
    for i in range(n_isl):
        if orient == ORIENT_RANDOM90:
            isl_k90[i] = rr.randrange(4)
        elif orient == ORIENT_RANDOM_FREE:
            isl_rot[i] = rr.uniform(-math.pi, math.pi)
        if jit:
            isl_rot[i] += rr.uniform(-jit, jit)
        if prm["mirror_random"]:
            isl_mir[i] = rr.random() < 0.5
    gmir = bool(prm["flip_mirror"])

    up, fb = _norm(tuple(prm["up"]))[0], _norm(tuple(prm["fallback_up"]))[0]
    g = min(0.45, max(0.0, prm["gutter"]))
    span = (1.0 - 2.0 * g) * max(0.0, prm["glyph_scale"])
    fit, flush_lim = prm["fit"], prm["flush_aspect"]
    encoded = prm["uv_mode"] == UVMODE_ENCODED
    L = int(prm["encode_levels"])
    invN = 1.0 / N

    # group polys per island only when islands span several polygons
    multi = n_isl != npoly
    members = None
    if multi:
        members = [[] for _ in range(n_isl)]
        for i in range(npoly):
            members[island[i]].append(i)

    want_np = _np is not None and prm.get("use_numpy", True)
    uv = None if want_np else [0.0] * (8 * npoly)
    cell_out = [0] * npoly
    val_out = [0.0] * npoly
    rand_out = [0.0] * npoly
    counts = [0] * counts_len

    def place(isl, s, t):
        """local (s,t) in 0..1 (t down) -> final UV for this island."""
        if encoded:
            e = 1e-4
            s = e + (1.0 - 2 * e) * (0.5 + (s - 0.5) * span)
            t = e + (1.0 - 2 * e) * (0.5 + (t - 0.5) * span)
            vq = min(L - 1, int(isl_val[isl] * L))
            rq = min(L - 1, int(isl_rand[isl] * L))
            return vq + s, rq + t
        sc = span * (isl_cs[isl] if isl_cs is not None else 1.0)
        return ((isl_cx[isl] + (s - 0.5) * sc) * unit_mul, (isl_cy[isl] + (t - 0.5) * sc) * unit_mul)

    def solve_group(isl, plist):
        # gather unique verts
        vids = []
        seen = {}
        for pi in plist:
            for j in _poly_ids(polys[pi]):
                if j not in seen:
                    seen[j] = len(vids)
                    vids.append(j)
        # area-weighted normal
        nx = ny = nz = 0.0
        for pi in plist:
            ids = _poly_ids(polys[pi])
            pa = points[ids[0]]
            if len(ids) == 4:
                nn = _cross(_sub(points[ids[2]], pa), _sub(points[ids[3]], points[ids[1]]))
            else:
                nn = _cross(_sub(points[ids[1]], pa), _sub(points[ids[2]], pa))
            nx += nn[0]; ny += nn[1]; nz += nn[2]
        n, nl = _norm((nx, ny, nz))
        if nl == 0.0:
            n = (0.0, 0.0, 1.0)
        if orient == ORIENT_EDGE:
            ids = _poly_ids(polys[plist[0]])
            e = _sub(points[ids[1]], points[ids[0]])
            r, _ = _norm(_sub(e, tuple(n[q] * _dot(e, n) for q in range(3))))
            u = _cross(r, n)
            if _dot(_cross(n, u), r) < 0:
                u = (-u[0], -u[1], -u[2])
        else:
            r, u = _frame(n, up, fb)
        # project
        xs, ys = [], []
        for j in vids:
            p = points[j]
            xs.append(_dot(p, r)); ys.append(_dot(p, u))
        cx = (max(xs) + min(xs)) * 0.5
        cy = (max(ys) + min(ys)) * 0.5
        ang = isl_rot[isl] + isl_k90[isl] * (math.pi * 0.5)
        mir = isl_mir[isl] != gmir
        ca, sa = math.cos(ang), math.sin(ang)
        for k in range(len(vids)):
            x, y = xs[k] - cx, ys[k] - cy
            if mir:
                x = -x
            xs[k], ys[k] = x * ca - y * sa, x * sa + y * ca

        # flush fit for single near-square quads
        if fit != FIT_UNIFORM and len(plist) == 1 and len(vids) == 4:
            ids = _poly_ids(polys[plist[0]])
            P = [points[j] for j in ids]
            e = [_norm(_sub(P[(q + 1) % 4], P[q]))[1] for q in range(4)]
            w, h = (e[0] + e[2]) * 0.5, (e[1] + e[3]) * 0.5
            d1 = _norm(_sub(P[2], P[0]))[1]
            d2 = _norm(_sub(P[3], P[1]))[1]
            asp = max(w, h) / max(1e-12, min(w, h))
            dr = max(d1, d2) / max(1e-12, min(d1, d2))
            if fit == FIT_FLUSH or (asp <= flush_lim and dr <= 1.25 and not isl_rot[isl]):
                o = plist[0] * 8
                for k, ci in enumerate(_flush_corners(xs, ys)):
                    s, t = _CORNERS[ci]
                    uv[o + k * 2], uv[o + k * 2 + 1] = place(isl, s, t)
                return
        # uniform, centred fit
        ext = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
        mx = (max(xs) + min(xs)) * 0.5
        my = (max(ys) + min(ys)) * 0.5
        inv = 1.0 / ext
        loc = {}
        for k, j in enumerate(vids):
            loc[j] = place(isl, 0.5 + (xs[k] - mx) * inv, 0.5 - (ys[k] - my) * inv)
        for pi in plist:
            a, b, c, d = polys[pi]
            o = pi * 8
            for k, j in enumerate((a, b, c, d)):
                uu, vv = loc[j]
                uv[o + k * 2], uv[o + k * 2 + 1] = uu, vv

    # single-polygon islands take the fast path (numpy when available)
    if multi:
        singles = [m[0] for m in members if len(m) == 1]
        groups = [isl for isl in range(n_isl) if len(members[isl]) > 1]
    else:
        singles, groups = None, []
    ctx = dict(N=N, span=span, fit=fit, flush_lim=flush_lim, encoded=encoded, L=L,
               up=up, fb=fb, gmir=gmir, orient=orient, cx=isl_cx, cy=isl_cy, cs=isl_cs, mul=unit_mul)
    used_np = False
    if want_np:
        uv = _solve_singles_np(points, polys, singles, island, isl_cell, isl_val, isl_rand,
                               isl_rot, isl_k90, isl_mir, ctx, uv)
        used_np = True
    else:
        _solve_singles_py(points, polys, singles, island, isl_cell, isl_val, isl_rand,
                          isl_rot, isl_k90, isl_mir, ctx, uv, progress)
    if groups:
        if used_np:  # groups write into a python list, merged back below
            uvl = uv.reshape(-1).tolist()
            uv = uvl
        step = max(1, len(groups) // 50)
        for k, isl in enumerate(groups):
            solve_group(isl, members[isl])
            if progress and k % step == 0:
                progress(k / len(groups))

    for i in range(npoly):
        isl = island[i]
        cell_out[i] = isl_cell[isl]
        val_out[i] = isl_val[isl]
        rand_out[i] = isl_rand[isl]
    for isl in range(n_isl):
        counts[isl_cell[isl]] += 1
    return dict(uv=uv, cell=cell_out, value=val_out, rand=rand_out, island=island,
                counts=counts, islands=n_isl, grid=N, mixed=mixed)
