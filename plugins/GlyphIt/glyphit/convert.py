"""
Convert legacy GlyphGrid scenes (Python Generator + user data) to Glyph It plugin objects.

For every Python Generator whose user data carries "Grid (N x N)" and "Plates Folder" (or the
older "Plate Folder"): a Glyph It object with the same name, position, children, tags and
parameter values replaces it. Materials built for GlyphGrid keep working: their Redshift
vertex-attribute node is re-pointed from the old tag name "GlyphGrid Color" to "GlyphIt Color".
"""
import os

import c4d

from . import params as P
from . import runtime as R


def is_legacy(o):
    if o is None or o.GetType() != c4d.Opython:
        return False
    try:
        names = {bc[c4d.DESC_NAME] for _d, bc in o.GetUserDataContainer()}
    except Exception:
        return False
    return "Grid (N x N)" in names and ("Plates Folder" in names or "Plate Folder" in names)


def _legacy_values(o):
    vals = {}
    for did, bc in o.GetUserDataContainer():
        if did[-1].dtype in (c4d.DTYPE_GROUP, c4d.DTYPE_STATICTEXT, c4d.DTYPE_BUTTON):
            continue
        try:
            vals[bc[c4d.DESC_NAME]] = (o[did], bc)
        except Exception:
            pass
    return vals


def convert_object(doc, old):
    """Replace one legacy generator; returns the new object."""
    vals = _legacy_values(old)
    new = c4d.BaseObject(P.PLUGIN_ID)
    if new is None:
        raise RuntimeError("Glyph It plugin not loaded")
    new.SetName(old.GetName())
    new.SetMl(old.GetMl())
    new[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = old[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR]
    new[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = old[c4d.ID_BASEOBJECT_VISIBILITY_RENDER]
    new[c4d.ID_BASEOBJECT_GENERATOR_FLAG] = old[c4d.ID_BASEOBJECT_GENERATOR_FLAG]
    doc.InsertObject(new, pred=old)
    s_ = R.param_setter(new)
    for name, (v, bc) in vals.items():
        name = P.LEGACY_NAMES.get(name, name)
        if name not in P.NAME_TO_ID or v is None:
            continue
        kind = P.KINDS[name]
        try:
            if name == "Collection":
                cyc = bc[c4d.DESC_CYCLE]
                cname = cyc.GetString(int(v)) if cyc is not None else ""
                s_("Collection Name", cname)
                names_ = R.collection_list(new) if vals.get("Plates Folder") else []
                continue
            if kind == "font" and isinstance(v, str):
                v = R.font_data(v)
            if kind in ("link", "shader", "fields"):
                s_(name, v)
                continue
            s_(name, v)
        except Exception as e:
            print("GlyphIt convert %s: %s" % (name, e))
    # Plates Folder: the old scene may point at the repo checkout; use the plugin's own plates
    # folder when the old one is gone
    pf = new[P.NAME_TO_ID["Plates Folder"]] or ""
    if not pf or not os.path.isdir(pf):
        new[P.NAME_TO_ID["Plates Folder"]] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                           "plates")
    # collection index from the remembered name
    names_ = R.collection_list(new)
    cname = new[P.NAME_TO_ID["Collection Name"]] or ""
    new[P.NAME_TO_ID["Collection"]] = names_.index(cname) if cname in names_ else 0
    # children (the source + deformers) and tags (materials, Octane object tag ...)
    ch = old.GetDown()
    while ch is not None:
        nxt = ch.GetNext()
        ch.Remove()
        ch.InsertUnderLast(new)
        ch = nxt
    t = old.GetFirstTag()
    while t is not None:
        nxt = t.GetNext()
        if not t.CheckType(c4d.Tpython):
            t.Remove()
            new.InsertTag(t)
        t = nxt
    # animation tracks on user data -> the matching plugin parameters
    for tr in old.GetCTracks():
        did = tr.GetDescriptionID()
        try:
            if did[0].id != c4d.ID_USERDATA:
                continue
            for d_, bc in old.GetUserDataContainer():
                if d_ == did:
                    name = P.LEGACY_NAMES.get(bc[c4d.DESC_NAME], bc[c4d.DESC_NAME])
                    if name in P.NAME_TO_ID:
                        nt = tr.GetClone()
                        nt.SetDescriptionID(new, c4d.DescID(c4d.DescLevel(P.NAME_TO_ID[name], d_[-1].dtype, 0)))
                        new.InsertTrackSorted(nt)
                    break
        except Exception as e:
            print("GlyphIt convert track:", e)
    old.Remove()
    return new


def retarget_materials(doc):
    """Redshift materials built for GlyphGrid: the colour node reads the vertex colour tag by name."""
    n = 0
    try:
        import maxon
        RS = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")
        Pfx = "com.redshift3d.redshift4c4d.nodes.core."
    except Exception:
        return 0
    for mat in doc.GetMaterials():
        if mat.GetType() != c4d.Mmaterial:
            continue
        try:
            nm = mat.GetNodeMaterialReference()
            if nm is None or not nm.HasSpace(RS):
                continue
            g = nm.GetGraph(RS)
            changed = False
            with g.BeginTransaction() as tx:
                for nd in g.GetViewRoot().GetChildren():
                    if str(nd.GetId()).startswith("gg_color"):
                        port = nd.GetInputs().FindChild(maxon.InternedId(Pfx + "vertexattributelookup.attribute"))
                        if port is not None and str(port.GetPortValue()) == R.OLD_COLOR_TAG_NAME:
                            port.SetPortValue(maxon.String(R.COLOR_TAG_NAME))
                            changed = True
                tx.Commit()
            if changed:
                n += 1
        except Exception as e:
            print("GlyphIt retarget material %s: %s" % (mat.GetName(), e))
    return n


def convert_document(doc):
    olds = []
    stack = [doc.GetFirstObject()]
    while stack:
        o = stack.pop()
        while o is not None:
            if is_legacy(o):
                olds.append(o)
            elif o.GetDown() is not None:
                stack.append(o.GetDown())
            o = o.GetNext()
    doc.StartUndo()
    n = 0
    for o in olds:
        try:
            doc.AddUndo(c4d.UNDOTYPE_DELETEOBJ, o)
            new = convert_object(doc, o)
            doc.AddUndo(c4d.UNDOTYPE_NEWOBJ, new)
            n += 1
        except Exception as e:
            print("GlyphIt convert:", e)
    retarget_materials(doc)
    doc.EndUndo()
    c4d.EventAdd()
    return n
