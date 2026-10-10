"""
Scripting API for Glyph It.

    import sys; sys.path.insert(0, "<plugins>/GlyphIt")
    from glyphit import api
    obj = api.create(doc, source=my_object, **{"Grid (N x N)": 4, "Plate Style": 0})
    api.set_params(obj, Seed=7, Collection="shapes")
    api.material(doc, obj, "redshift")          # the Color tab's Redshift button
    api.bake(doc, obj)                          # editable copy

Parameter names are exactly the Attribute Manager labels (see params.SPEC).
"""
import os

import c4d

from . import params as P
from . import runtime as R

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create(doc, source=None, name="Glyph It", **kv):
    obj = c4d.BaseObject(P.PLUGIN_ID)
    if obj is None:
        raise RuntimeError("Glyph It plugin is not loaded (plugin id %d)" % P.PLUGIN_ID)
    obj.SetName(name)
    doc.InsertObject(obj)
    set_params(obj, **kv)
    if source is not None:
        if source.GetDocument() is not None:
            source.Remove()
        source.InsertUnder(obj)
    c4d.EventAdd()
    return obj


def set_params(obj, **kv):
    """set_params(obj, **{"Grid (N x N)": 4}) -- names as in the Attribute Manager. Underscores in
    keyword names become spaces. Collection accepts a folder name."""
    s_ = R.param_setter(obj)
    g = R.param_getter(obj)
    for k, v in kv.items():
        k = k.replace("_", " ") if k not in P.NAME_TO_ID else k
        if k == "Collection" and isinstance(v, str):
            names_ = R.collection_list(obj)
            s_("Collection Name", v)
            v = names_.index(v) if v in names_ else 0
        if k == "Glyph Color" and isinstance(v, (tuple, list)):
            v = c4d.Vector(*v)
        if k == "Font" and isinstance(v, str):
            v = R.font_data(v)
        s_(k, v)
    if any(k.replace("_", " ") in P.LIMIT_KEYS for k in kv):
        R.apply_slider_caps(obj, "Quadtree Levels" if "Quadtree Levels" in [k.replace("_", " ") for k in kv] else None)
    return obj


def get_params(obj):
    g = R.param_getter(obj)
    return {n: g(n) for n in P.NAME_TO_ID if P.KINDS[n] != "button"}


def material(doc, obj, renderer="redshift"):
    return R.build_glyph_material(doc, obj, renderer)


def sync_colors(obj):
    return R.sync_material_color(obj)


def bake(doc, obj, res=None, undo=False):
    """Editable copies of the current output (UVW + selection + colour tags + material); the
    generator is switched off and hidden so its source doesn't render underneath the copy."""
    from . import generator  # noqa: F401
    if res is None:
        res = obj.GetCache()
    if res is None:
        doc.ExecutePasses(None, False, False, True, c4d.BUILDFLAGS_NONE)
        res = obj.GetCache()
    if res is None:
        return []
    polys = []
    o = res.GetDown() if res else None
    while o is not None:
        if o.IsInstanceOf(c4d.Opolygon):
            polys.append(o)
        o = o.GetNext()
    if not polys:
        return []
    out = []
    pred = obj
    if undo:
        doc.StartUndo()
    for k, po in enumerate(polys):
        cp = po.GetClone(c4d.COPYFLAGS_NO_HIERARCHY)
        cp.SetName(obj.GetName() + " baked" + ("" if len(polys) == 1 else " %d" % (k + 1)))
        cp.SetMg(obj.GetMg() * po.GetMl())
        cp[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = c4d.OBJECT_UNDEF
        cp[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = c4d.OBJECT_UNDEF
        t = obj.GetFirstTag()
        while t is not None:
            if t.CheckType(c4d.Ttexture):
                tt = t.GetClone()
                cp.InsertTag(tt)
                m = tt.GetMaterial()
                # a Standard material reads colours through the generator's hidden proxy; the baked
                # copy carries its own colour tag, so give it a material that reads that directly
                sh = m[c4d.MATERIAL_LUMINANCE_SHADER] if m is not None and m.GetType() == c4d.Mmaterial else None
                own = R.proxy_color_tag(cp)
                if sh is not None and sh.GetType() == c4d.Xvertexmap and own is not None:
                    m2 = m.GetClone()
                    m2.SetName(m.GetName() + " baked")
                    doc.InsertMaterial(m2)
                    m2[c4d.MATERIAL_LUMINANCE_SHADER][1001] = own
                    m2.Message(c4d.MSG_UPDATE)
                    tt.SetMaterial(m2)
            t = t.GetNext()
        doc.InsertObject(cp, pred=pred)
        if undo:
            doc.AddUndo(c4d.UNDOTYPE_NEW, cp)
        pred = cp
        out.append(cp)
    if undo:
        doc.AddUndo(c4d.UNDOTYPE_CHANGE_SMALL, obj)
    obj[c4d.ID_BASEOBJECT_GENERATOR_FLAG] = False
    obj[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = c4d.OBJECT_OFF
    obj[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = c4d.OBJECT_OFF
    if undo:
        doc.EndUndo()
    doc.SetActiveObject(pred)
    c4d.EventAdd()
    return out


def write_uvs_onto_source(obj):
    return R.write_result_to_source(obj, obj.GetCache())


def plates_folder():
    return os.path.join(HERE, "plates")


def convert_scene(doc):
    from . import convert
    return convert.convert_document(doc)
