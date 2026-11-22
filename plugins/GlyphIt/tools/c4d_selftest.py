"""In-Cinema-4D self test for Glyph It. Run from the Python console:
    exec(open("<path>/c4d_selftest.py").read())
Writes a report next to this file (glyphit_selftest.txt) and to the console."""
import os, sys, time, glob, traceback

import c4d

LOG = []
OUT_DIR = c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS)        # the prefs folder on any OS
OUT = os.path.join(OUT_DIR, "glyphit_selftest.txt")
PID = 1071110


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s)


def flush():
    with open(OUT, "w") as f:
        f.write("\n".join(LOG) + "\n")


def _plugin_dir():
    # wherever Glyph It is installed: prefs/plugins, the app's plugins folder, or next to this file
    cands = [os.path.join(c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS), "plugins", "GlyphIt"),
             os.path.join(c4d.storage.GeGetC4DPath(c4d.C4D_PATH_STARTUPWRITE if hasattr(c4d, "C4D_PATH_STARTUPWRITE") else c4d.C4D_PATH_PREFS), "plugins", "GlyphIt"),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "..") if "__file__" in globals() else None]
    try:
        import glyphit
        cands.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(glyphit.__file__))))
    except Exception:
        pass
    for p in cands:
        if p and os.path.isdir(os.path.join(p, "glyphit")):
            return os.path.abspath(p)
    return cands[0]


PLUG = _plugin_dir()
if PLUG not in sys.path:
    sys.path.insert(0, PLUG)
from glyphit import api, params as P, runtime as R  # noqa: E402


def passes(doc, n=1):
    for _ in range(n):
        doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)


def cache_polys(op):
    c = op.GetCache()
    out = []

    def walk(o):
        while o is not None:
            if o.IsInstanceOf(c4d.Opolygon):
                out.append(o)
            walk(o.GetDown())
            o = o.GetNext()
    if c is not None:
        if c.IsInstanceOf(c4d.Opolygon):
            out.append(c)
        walk(c.GetDown())
    return out


def tag_names(o):
    t, names = o.GetFirstTag(), []
    while t is not None:
        names.append("%s(%d)" % (t.GetName(), t.GetType()))
        t = t.GetNext()
    return names


def uv_signature(o):
    t = o.GetTag(c4d.Tuvw)
    if t is None:
        return None
    n = min(o.GetPolygonCount(), 400)
    s = 0.0
    for i in range(n):
        d = t.GetSlow(i)
        s += d["a"].x * 3 + d["a"].y * 5 + d["c"].x * 7 + d["c"].y * 11
    return round(s, 4)


def new_scene(name):
    doc = c4d.documents.BaseDocument()
    doc.SetDocumentName(name)
    c4d.documents.InsertBaseDocument(doc)
    c4d.documents.SetActiveDocument(doc)
    return doc


def plane(w=20, h=20):
    p = c4d.BaseObject(c4d.Oplane)
    p[c4d.PRIM_PLANE_SUBW] = w
    p[c4d.PRIM_PLANE_SUBH] = h
    return p


RESULTS = []


def test(name):
    def deco(fn):
        t0 = time.time()
        try:
            msg = fn()
            RESULTS.append((name, True))
            log("PASS  %-38s %5.2fs  %s" % (name, time.time() - t0, msg or ""))
        except Exception as e:  # noqa: BLE001
            RESULTS.append((name, False))
            log("FAIL  %-38s %s: %s" % (name, type(e).__name__, e))
            log(traceback.format_exc())
        flush()
        return fn
    return deco


log("Glyph It self test  %s  C4D %s  python %s" % (time.strftime("%Y-%m-%d %H:%M"), c4d.GetC4DVersion(),
                                                     sys.version.split()[0]))
log("plugin dir:", PLUG)
try:
    import numpy
    log("numpy", numpy.__version__, numpy.__file__)
except Exception as e:  # noqa: BLE001
    log("numpy MISSING", e)
try:
    import PIL
    log("Pillow", PIL.__version__)
except Exception as e:  # noqa: BLE001
    log("Pillow MISSING", e)

DOC = new_scene("GlyphIt selftest")
S = {}


@test("create + build (plane 20x20)")
def _():
    op = api.create(DOC, source=plane())
    S["op"] = op
    passes(DOC)
    ps = cache_polys(op)
    assert ps, "no cache"
    S["sig0"] = uv_signature(ps[0])
    return "%d polys, tags=%s" % (ps[0].GetPolygonCount(), tag_names(ps[0]))


@test("description")
def _():
    op = S["op"]
    desc = op.GetDescription(c4d.DESCFLAGS_DESC_NONE)
    names = []
    for bc, did, _g in desc:
        n = bc[c4d.DESC_NAME]
        if n:
            names.append(n)
    missing = [n for n in P.NAME_TO_ID if P.KINDS[n] not in ("tab", "help", "row", "endrow")
               and not n.startswith("row_") and n not in names and not P.EXTRA.get(n, {}).get("hidden")]
    assert not missing, "missing in AM: %s" % missing
    return "%d entries" % len(names)


@test("params round trip")
def _():
    op = S["op"]
    api.set_params(op, **{"Grid (N x N)": 3, "Seed": 7, "Glyph Color": (1, 0.2, 0.1), "Gutter": 0.1})
    g = api.get_params(op)
    assert g["Grid (N x N)"] == 3 and g["Seed"] == 7, g
    passes(DOC)
    sig = uv_signature(cache_polys(op)[0])
    assert sig != S["sig0"], "UVs did not change after params"
    return "grid=3 seed=7 sig %s -> %s" % (S["sig0"], sig)


@test("grid clamp 1..4")
def _():
    op = S["op"]
    desc = op.GetDescription(c4d.DESCFLAGS_DESC_NONE)
    for bc, did, _g in desc:
        if bc[c4d.DESC_NAME] == "Grid (N x N)":
            return "min %s max %s" % (bc[c4d.DESC_MIN], bc[c4d.DESC_MAX])
    raise AssertionError("not found")


@test("island modes")
def _():
    op = S["op"]
    out = []
    for m in range(4):
        api.set_params(op, **{"Island Mode": m})
        passes(DOC)
        ps = cache_polys(op)
        assert ps and ps[0].GetPolygonCount() > 0
        out.append(str(ps[0].GetPolygonCount()))
    api.set_params(op, **{"Island Mode": 1})
    return "polys per mode " + "/".join(out)


@test("plate styles 1-9")
def _():
    op = S["op"]
    out = []
    for st in range(9):
        api.set_params(op, **{"Plate Style": st})
        if st == 8:
            api.set_params(op, Collection="shapes")
        R.refresh_generators(DOC) if hasattr(R, "refresh_generators") else None
        passes(DOC)
        out.append(os.path.basename(R.current_plate(op) or "-") if hasattr(R, "current_plate") else "?")
    api.set_params(op, **{"Plate Style": 0})
    return ", ".join(out)


@test("value sources (no image)")
def _():
    op = S["op"]
    for v in range(9):
        api.set_params(op, **{"Value Source": v, "Distribute": 2})
        passes(DOC)
        assert cache_polys(op), "no cache for source %d" % v
    api.set_params(op, **{"Value Source": 0, "Distribute": 0})
    return "9 sources built"


@test("color modes + color tag")
def _():
    op = S["op"]
    out = []
    for m in range(3):
        api.set_params(op, **{"Color Mode": m})
        passes(DOC)
        po = cache_polys(op)[0]
        vt = po.GetTag(c4d.Tvertexcolor)
        out.append("m%d:%s" % (m, "tag" if vt else "none"))
        if vt is not None:
            assert not vt.IsPerPointColor(), "colour tag must be per polygon"
    return " ".join(out)


@test("materials standard/redshift/octane")
def _():
    op = S["op"]
    out = []
    for r in ("standard", "redshift", "octane"):
        try:
            m = api.material(DOC, op, r)
            out.append("%s:%s" % (r, m.GetName() if m else "None"))
        except Exception as e:  # noqa: BLE001
            out.append("%s:ERR %s" % (r, e))
    passes(DOC)
    return " ".join(out)


@test("image source (generated gradient)")
def _():
    op = S["op"]
    from PIL import Image
    img = Image.new("RGB", (256, 256))
    px = img.load()
    for y in range(256):
        for x in range(256):
            px[x, y] = (x, y, 255 - x)
    path = os.path.join(OUT_DIR, "glyphit_selftest_gradient.png")
    img.save(path)
    api.set_params(op, **{"Value Source": 9, "Image File": path, "Color Mode": 3})
    passes(DOC, 2)
    po = cache_polys(op)[0]
    vt = po.GetTag(c4d.Tvertexcolor)
    cols = set()
    if vt is not None:
        data = vt.GetDataAddressR()
        for i in range(0, min(po.GetPolygonCount(), 400)):
            c = vt.GetPolygon(data, i)
            cols.add((round(c["a"].x, 2), round(c["a"].y, 2)))
    assert len(cols) > 10, "image colours not varying (%d)" % len(cols)
    api.set_params(op, **{"Value Source": 0, "Color Mode": 0})
    return "%d distinct colours" % len(cols)


@test("bend deformer under generator")
def _():
    op = S["op"]
    passes(DOC)
    before = uv_signature(cache_polys(op)[0])
    b = c4d.BaseObject(c4d.Obend)
    b[c4d.DEFORMOBJECT_STRENGTH] = c4d.utils.DegToRad(90)
    b.InsertUnderLast(op)
    passes(DOC, 2)
    po = cache_polys(op)[0]
    after = uv_signature(po)
    bb = po.GetRad()
    b.Remove()
    passes(DOC)
    assert before == after, "UVs changed under bend (%s vs %s)" % (before, after)
    return "UVs stable, rad %s" % bb


@test("animation frames (Random, time)")
def _():
    op = S["op"]
    for f in (0, 5, 10, 0):
        DOC.SetTime(c4d.BaseTime(f, DOC.GetFps()))
        passes(DOC)
        assert cache_polys(op)
    return "ok"


@test("bake")
def _():
    op = S["op"]
    out = api.bake(DOC, op)
    assert out and out[0].GetPolygonCount() > 0
    n = out[0].GetPolygonCount()
    for o in out:
        o.Remove()
    op[c4d.ID_BASEOBJECT_GENERATOR_FLAG] = True
    passes(DOC)
    return "%d polys baked" % n


@test("save / load round trip")
def _():
    op = S["op"]
    api.set_params(op, **{"Seed": 42, "Grid (N x N)": 2})
    passes(DOC)
    sig = uv_signature(cache_polys(op)[0])
    path = os.path.join(OUT_DIR, "glyphit_selftest.c4d")
    assert c4d.documents.SaveDocument(DOC, path, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT)
    d2 = c4d.documents.LoadDocument(path, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
    o2 = d2.SearchObject(op.GetName())
    assert o2 is not None and o2.GetType() == PID
    g = api.get_params(o2)
    assert g["Seed"] == 42 and g["Grid (N x N)"] == 2, (g["Seed"], g["Grid (N x N)"])
    passes(d2)
    sig2 = uv_signature(cache_polys(o2)[0])
    assert sig == sig2, "reloaded UVs differ %s %s" % (sig, sig2)
    return "params + UVs identical after reload"


@test("clone + undo")
def _():
    op = S["op"]
    c = op.GetClone()
    DOC.InsertObject(c)
    passes(DOC)
    assert cache_polys(c)
    DOC.StartUndo()
    DOC.AddUndo(c4d.UNDOTYPE_DELETEOBJ, c)
    c.Remove()
    DOC.EndUndo()
    DOC.DoUndo()
    passes(DOC)
    return "ok"


@test("cloth sphere (sim tags stripped)")
def _():
    sph = c4d.BaseObject(c4d.Osphere)
    sph[c4d.PRIM_SPHERE_TYPE] = 0
    op = api.create(DOC, source=sph, name="cloth test")
    try:
        sph.MakeTag(100004020)
    except Exception:  # noqa: BLE001
        return "skipped (no cloth tag)"
    passes(DOC, 3)
    po = cache_polys(op)[0]
    bad = [t for t in tag_names(po) if "100004020" in t]
    assert not bad, "cloth tag copied onto output"
    return "ok"


def convert_demos():
    root = os.path.expanduser("~/Documents/GlyphGrid")
    files = sorted(glob.glob(os.path.join(root, "*.c4d")))
    if not files:
        log("demo scenes: none in ~/Documents/GlyphGrid (skipped)")
        return
    log("demo scenes:", [os.path.basename(f) for f in files])
    for f in files:
        @test("convert " + os.path.basename(f)[:28])
        def _():
            d = c4d.documents.LoadDocument(f, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
            assert d is not None, "load failed"
            passes(d)
            res = api.convert_scene(d)
            passes(d, 2)
            plugs, built = 0, 0
            o = d.GetFirstObject()
            stack = [o] if o else []
            while stack:
                o = stack.pop()
                if o.GetType() == PID:
                    plugs += 1
                    if cache_polys(o):
                        built += 1
                if o.GetNext():
                    stack.append(o.GetNext())
                if o.GetDown():
                    stack.append(o.GetDown())
            assert plugs and plugs == built, "converted %d, built %d" % (plugs, built)
            out = os.path.join(OUT_DIR, "glyphit_selftest_" + os.path.basename(f))
            c4d.documents.SaveDocument(d, out, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT)
            return "%s -> %d Glyph It objects (saved copy)" % (res, plugs)


try:
    convert_demos()
except Exception as e:  # noqa: BLE001
    log("demo conversion skipped:", e)

c4d.EventAdd()
ok = sum(1 for _n, r in RESULTS if r)
log("\n%d/%d passed" % (ok, len(RESULTS)))
flush()
