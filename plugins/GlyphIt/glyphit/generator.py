"""
Glyph It generator core: parameters -> solved output object. Shared by the plugin object
(GlyphIt.pyp) and the legacy Python Generator. `S` is the per-object state dict (kept geometry,
last result, signatures). `hh` is the HierarchyHelp from GetVirtualObjects (None in legacy mode
means op.GetAndCheckHierarchyClone's hh is the generator's own).
"""
import c4d

from .runtime import *  # noqa: F401,F403  (the C4D-side helpers + the solver's compute)
from .runtime import (SRC_RANDOM, SRC_VMAP, SRC_FIELD, SRC_INDEX, SRC_HEIGHT, SRC_IMAGE, COLOR_IMAGE,
                      UP_AXES, GRID_MAX, QT_LEVELS_MAX, qt_level_cap, param_getter, read_mesh, sample_values,
                      collect_polys, root_signature, fields_signature, image_signature, image_sample,
                      image_boxes, concat_boxes, merge_parts, apply_to_object, write_color_tag,
                      write_palette_uv, glyph_colors, color_keys, strip_sim_tags, _color_opts,
                      is_color_proxy)



# ======================================================= GlyphIt generator ===


def params_from(op):
    """UI -> (params, src, opts, flags) for the solver."""
    g = param_getter(op)
    def _nums(txt):                       # "8,1 1;1" -> [8, 1, 1, 1]; junk entries are skipped
        out = []
        for x in str(txt or "").replace(";", ",").replace(" ", ",").split(","):
            try:
                if x.strip():
                    out.append(float(x))
            except ValueError:
                pass
        return out
    w = _nums(g("Weights", ""))
    params = dict(
        grid=int(g("Grid (N x N)", 4)), seed=int(g("Seed", 12345)), cell_shift=int(g("Glyph Offset", g("Cell Shift", 0))),
        island=int(g("Island Mode", 1)), cluster_size=float(g("Cluster Size", 50.0)),
        qt_cells=1 << int(g("Quadtree Block (polys)", 3)), qt_levels=int(g("Quadtree Levels", 4)),
        qt_split=float(g("Subdivide Chance", 0.5)), grid_mode=int(g("Grid Mode", 0)),
        level_weights=_nums(g("Level Weights", "")) or None,
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
    shd = g("Image Shader", None)
    opts["image"] = dict(file=str(g("Image File", "") or ""),
                         shader=shd if isinstance(shd, c4d.BaseShader) else None,
                         sampling=int(g("Image Sampling", 0)), speed=float(g("Image Speed", 1.0)),
                         offset=int(g("Image Frame Offset", 0)), loop=bool(g("Image Loop", True)))
    if not opts["image"]["file"] and opts["image"]["shader"] is None:
        opts["image"] = None
    flags = dict(animate=bool(g("Rebuild Every Frame", False)), write_id=bool(g("Write ID Tag", False)),
                 cell_sel=bool(g("Cell Selection Tags", False)), keep=bool(g("Keep Source UVs", False)),
                 merge=bool(g("Merge Objects", True)), source=g("Source Object", None),
                 lock=bool(g("Lock Glyphs to Topology", True)))
    # clamp to what the solver can really use (old scenes / typed values / keyframes)
    params["grid"] = max(1, min(GRID_MAX, params["grid"]))
    params["qt_levels"] = max(1, min(QT_LEVELS_MAX, qt_level_cap(params["qt_cells"]), params["qt_levels"]))
    return params, src, opts, flags


def topo_key(geo):
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


def recolor(S, res, color, img=None):
    """Colour-only change: rewrite the vertex colour tags on the kept result (no re-solve).
    From Image re-samples the picture (new movie frame) at the stored source-UV boxes."""
    keys = S.get("colkeys") or []
    o = res.GetDown()
    k = 0
    while o is not None:
        if o.IsInstanceOf(c4d.Opolygon):
            if k < len(keys) and keys[k] is not None:
                r_ = {"cell": keys[k][0], "island": keys[k][1]}
                if int(color.get("mode", 0)) == COLOR_IMAGE and len(keys[k]) > 2 and keys[k][2] is not None:
                    try:
                        r_["img_rgb"] = image_sample(img, keys[k][2])
                    except Exception as e:
                        print("GlyphIt image:", e)
                write_color_tag(o, glyph_colors(r_, color))
                write_palette_uv(o, color_keys(r_, color))
            k += 1
        o = o.GetNext()


def move_points(res, geo, gmg, inv, merge):
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


def eval_private(src_obj, inv, CF):
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
        return found      # (no KillDocument on a cache thread; the free document is garbage-collected)
    except Exception as e:
        print("GlyphIt: private build failed:", e)
        return []


def build(op, hh, doc, S):
    """The generator: returns the output object (a clone of the kept result) or None."""
    params, src, opts, flags = params_from(op)
    link = flags.get("source")
    child_mode = link is None
    sib_def = False          # deformers directly under GlyphIt (siblings of the source)
    if child_mode:
        src_obj = None
        ch = op.GetDown()
        while ch is not None:
            if ch.GetInfo() & c4d.OBJECT_MODIFIER:
                sib_def = sib_def or bool(ch.GetDeformMode())   # switched-off deformers don't count
            elif src_obj is None and not is_color_proxy(ch):
                src_obj = ch
            ch = ch.GetNext()
    else:
        src_obj = link
        p_ = op
        while p_ is not None:            # linking GlyphIt itself or one of its parents = feedback loop
            if p_ == link:               # (==, not "is": every c4d wrapper is a new Python object)
                return None
            p_ = p_.GetUp()
    if src_obj is None:
        return None
    # a different source object (or child <-> link switch): forget everything from the old one
    try:
        src_key = (child_mode, str(src_obj.GetGUID()))
    except Exception:
        src_key = (child_mode, id(src_obj))
    if src_key != S.get("src_key"):
        for k_ in ("geo", "src_sig", "result", "own_sig", "topo", "fallback_sig"):
            S.pop(k_, None)
        S["src_key"] = src_key
    frame = doc.GetTime().GetFrame(doc.GetFps())
    tgt = opts.get("target")
    fl = opts.get("fields")
    gmg = op.GetMg()
    inv = ~gmg
    CF = c4d.COPYFLAGS_NO_HIERARCHY | c4d.COPYFLAGS_NO_ANIMATION | c4d.COPYFLAGS_NO_BITS
    # what the SOLVE depends on: colour and plate settings are left out, so changing them never
    # re-shuffles glyphs (colour = just a new vertex colour tag, plate = a material edit)
    img = opts.get("image")
    img_used_val = img is not None and src == SRC_IMAGE
    img_used_col = img is not None and int(opts["color"].get("mode", 0)) == COLOR_IMAGE
    if img is not None:
        img.update(frame=frame, time=doc.GetTime().Get(), doc=doc)
    img_sig = image_signature(img) if (img_used_val or img_used_col) else None
    if img_used_col:
        opts["color"]["img"] = repr(img_sig)        # colour from a moving picture = recolour per frame
    o_ = {k: v for k, v in opts.items() if k not in ("color", "fields", "target", "image")}
    solve_key = repr((sorted(params.items()), src, sorted(o_.items()),
                      [flags[k] for k in ("write_id", "cell_sel", "keep", "merge", "lock", "animate")],
                      str(tgt.GetGUID()) if tgt else 0))
    col_sig = repr(sorted(opts["color"].items()))
    own_sig = (op.GetDirty(c4d.DIRTYFLAGS_MATRIX), solve_key,
               tgt.GetDirty(c4d.DIRTYFLAGS_MATRIX | c4d.DIRTYFLAGS_DATA) if tgt else 0,
               fields_signature(fl, doc) if (fl is not None and src == SRC_FIELD) else 0,
               frame if flags["animate"] else 0, child_mode, img_sig if img_used_val else None)

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
                    # Cinema 4D applies deformers under GlyphIt to the source sibling too, and
                    # again to our output (= bent twice, UVs re-solved on the bent mesh). Read the
                    # source BEFORE deformers; they act on the output only.
                    found = [(o.GetClone(CF), inv * mg) for o, mg in collect_polys(src_obj, deform=False)]
                S["geo"] = found or [(o.GetClone(CF), o.GetMg()) for o, _ in collect_polys(cl)]
                geo_changed = True
                break
            if S.get("geo") and not (isinstance(res, dict) and res.get("dirty")):
                break
        fsig = root_signature(src_obj)
        if not S.get("geo") and S.get("fallback_sig") != fsig:
            # nothing stored yet and the child is "clean" (code was just refreshed, module reloaded):
            # evaluate a copy of the child in a private document ONCE per child state (an empty
            # child - spline, null - must not trigger a rebuild on every evaluation)
            S["fallback_sig"] = fsig
            try:
                tmp = c4d.documents.BaseDocument()
                cp = src_obj.GetClone(c4d.COPYFLAGS_NONE)
                cp.SetMl(src_obj.GetMl())
                tmp.InsertObject(cp)
                tmp.ExecutePasses(None, False, False, True, c4d.BUILDFLAGS_INTERNALRENDERER)
                found = [(o.GetClone(CF), o.GetMg()) for o, _ in collect_polys(cp)]
                if found:
                    S["geo"] = found
                    geo_changed = True
                # (no KillDocument: this runs on a cache thread; the free document is garbage-collected)
            except Exception as e:
                print("GlyphIt fallback build failed:", e)
    else:
        ssig = root_signature(src_obj)
        if hh is not None:
            # plugin object: a real dependency on the linked object. AddDependence builds the link's
            # cache for us (it may live anywhere in the scene) and tells us when it changed.
            try:
                op.NewDependenceList()
                try:
                    op.AddDependence(hh, src_obj)
                except TypeError:
                    op.AddDependence(src_obj)  # 2026+: signature dropped the HierarchyHelp
                if not op.CompareDependenceList():
                    S["dep_tick"] = S.get("dep_tick", 0) + 1
                op.TouchDependenceList()
            except Exception as e:
                print("GlyphIt dependence:", e)
            ssig = (ssig, S.get("dep_tick", 0))
        if ssig != S.get("src_sig") or "geo" not in S:
            found = [(o.GetClone(CF), inv * mg) for o, mg in collect_polys(src_obj)]
            if not found:
                # hidden / render-invisible sources (or a fresh render document) have no caches:
                # evaluate a copy of the linked object (with its children) in a private document
                found = eval_private(src_obj, inv, CF)
            S["src_sig"] = ssig      # remembered even when empty: no rebuild loop
            S["geo"] = found
            if not found:
                S.pop("result", None)
            geo_changed = True

    if not S.get("geo"):
        return None                       # source has no polygons (spline, null, empty)
    keep = S.get("result")
    # NOTE: never return op.GetCache() from a Python Generator (C4D frees it right after: "object is
    # not alive"). We return a clone of our own copy; polygon data is copy-on-write, ~0 ms.
    if not geo_changed and keep is not None and own_sig == S.get("own_sig"):
        if col_sig != S.get("col_sig"):
            recolor(S, keep, opts["color"], img)
            S["col_sig"] = col_sig
            S["col_stamp"] = S.get("col_stamp", 0) + 1
        return keep.GetClone()
    geo = S.get("geo")
    if not geo:
        return None
    topo = topo_key(geo)
    if flags["lock"] and keep is not None and own_sig == S.get("own_sig") \
            and topo == S.get("topo"):
        # only the points moved (deformer in the source, cloth, animated points, or a deformer
        # under GlyphIt): keep every glyph where it is, just carry the new shape
        res_ = keep.GetClone()
        if move_points(res_, geo, gmg, inv, flags["merge"]):
            if col_sig != S.get("col_sig"):
                recolor(S, res_, opts["color"], img)
                S["col_sig"] = col_sig
                S["col_stamp"] = S.get("col_stamp", 0) + 1
            S["result"] = res_.GetClone()
            return res_
    S["topo"] = topo
    S["own_sig"] = own_sig
    S["frame"] = frame
    # With the lock on, non-spatial sources solve on the REST shape (before deformers): the glyph
    # layout no longer depends on which frame / bend amount you happened to tweak a setting at.
    # Spatial sources (height, light, field ...) keep solving on the current shape.
    shape, rest_used = geo, False
    if flags["lock"] and src in (SRC_RANDOM, SRC_VMAP, SRC_INDEX, SRC_IMAGE) and not sib_def:
        try:
            rest = [(o.GetClone(CF), inv * mg) for o, mg in collect_polys(src_obj, deform=False)]
        except Exception:
            rest = []
        if rest and topo_key(rest) == topo:
            shape, rest_used = rest, True
    parts = [(o.GetClone(CF), gmg * ml) for o, ml in shape]   # (clone, world matrix)
    for o, _ in parts:
        # cache objects inherit the SOURCE's visibility: a hidden source (e.g. a linked SDS with
        # render visibility off) would make our output invisible to the renderer -> reset to default
        o[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = c4d.OBJECT_UNDEF
        o[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = c4d.OBJECT_UNDEF
        strip_sim_tags(o)

    root = c4d.BaseObject(c4d.Onull)
    root.SetName("GlyphIt")
    stats = []
    ib = "sample"
    if flags["merge"] or len(parts) == 1:
        if len(parts) == 1:
            po = parts[0][0]
            po.SetMl(inv * parts[0][1])
            vals = "sample"
        else:
            vals = None
            if src == SRC_IMAGE:
                vals = "sample"              # from the merged image boxes below
            elif src != SRC_RANDOM:
                vals = []
                for p_, mg_ in parts:
                    P_, F_, _ = read_mesh(p_)
                    v_ = sample_values(p_, mg_, P_, F_, src, opts, op, doc)
                    vals.extend(v_ if v_ is not None else [0.0] * len(F_))
                if src == SRC_INDEX or src == SRC_HEIGHT:
                    vals = "sample"   # global, recompute on the merged mesh
            if img_used_val or img_used_col:    # merged mesh has no UVs: collect each part's own
                ib = concat_boxes([image_boxes(p_)[0] for p_, _m in parts])
            po = merge_parts(parts, inv)
        po.InsertUnder(root)
        stats.append(apply_to_object(po, gmg * po.GetMl(), params, src, opts, caller=op, doc=doc,
                                     write_id=flags["write_id"], cell_sel=flags["cell_sel"],
                                     strip_uvs=not flags["keep"], values=vals, img_boxes=ib))
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
    S["stats"] = tot
    S["colkeys"] = [s_.get("keys") for s_ in stats]
    S["col_sig"] = col_sig
    S["col_stamp"] = S.get("col_stamp", 0) + 1       # the Standard colour proxy re-syncs
    if rest_used:
        move_points(root, geo, gmg, inv, flags["merge"])   # solved at rest, shown deformed
    S["result"] = root.GetClone()
    c4d.gui.StatusSetText("GlyphIt: %(polys)s polys in %(objects)s object(s), %(islands)s islands, %(cells)s cells "
                          "(%(min)s-%(max)s per cell), solve %(t_solve)ss" % tot + (" numpy" if tot["numpy"] else ""))
    return root


# ---------------------------------------------------------------- plate swap ---
