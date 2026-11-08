"""
Glyph It for Cinema 4D - every polygon becomes a glyph.

Registers:
  * the Glyph It generator object (plugin id 1071110, sdimaging)
  * "Convert GlyphGrid scenes to Glyph It" (Extensions menu)

Drop any object under Glyph It (or link one as Source Object). Every polygon / ngon / cluster /
quadtree block becomes a UV island fitted into one cell of an N x N glyph atlas ("plate").
See README.md next to this file.
"""
import os
import sys

import c4d
from c4d import plugins, bitmaps

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _bundled_libs():
    """numpy + Pillow shipped in libs/<platform>/ (tools/fetch_libs.py) when the host Python lacks them."""
    import platform
    tags = []
    mach = platform.machine().lower()
    if sys.platform == "darwin":
        tags = ["macos-arm64" if mach in ("arm64", "aarch64") else "macos-x86_64"]
    elif sys.platform.startswith("win"):
        tags = ["win-amd64"]
    else:
        tags = ["linux-x86_64"]
    for t in tags:
        d = os.path.join(HERE, "libs", t)
        if os.path.isdir(d) and d not in sys.path:
            sys.path.append(d)


for _mod in ("numpy", "PIL"):
    try:
        __import__(_mod)
    except Exception:
        _bundled_libs()
        break

from glyphit import params as P              # noqa: E402
from glyphit import runtime as R             # noqa: E402
from glyphit import generator as GEN         # noqa: E402

PLUGIN_ID = P.PLUGIN_ID
CONVERT_ID = P.CONVERT_ID
SYNC_ID = P.SYNC_ID
HELP_LINE_BASE = 1000     # help text line ids: tab help id + HELP_LINE_BASE + k


# ------------------------------------------------------------ description ---
def _desc_id(pid, dtype=c4d.DTYPE_LONG):
    return c4d.DescID(c4d.DescLevel(pid, dtype, PLUGIN_ID))


_DTYPE = {"int": c4d.DTYPE_LONG, "cycle": c4d.DTYPE_LONG, "float": c4d.DTYPE_REAL, "pct": c4d.DTYPE_REAL,
          "deg": c4d.DTYPE_REAL, "dist": c4d.DTYPE_REAL, "bool": c4d.DTYPE_BOOL, "string": c4d.DTYPE_STRING,
          "link": c4d.DTYPE_BASELISTLINK, "shader": c4d.DTYPE_BASELISTLINK, "fields": c4d.CUSTOMDATATYPE_FIELDLIST,
          "folder": c4d.DTYPE_FILENAME, "file": c4d.DTYPE_FILENAME, "font": c4d.FONTCHOOSER_DATA,
          "color": c4d.DTYPE_COLOR, "button": c4d.DTYPE_BUTTON, "tab": c4d.DTYPE_GROUP, "help": c4d.DTYPE_GROUP,
          "row": c4d.DTYPE_GROUP, "endrow": c4d.DTYPE_GROUP}


def dtype_of(name):
    return _DTYPE[P.KINDS[name]]


def param_descid(name):
    return _desc_id(P.NAME_TO_ID[name], dtype_of(name))


def _param_bc(name, kind, extra, op):
    """The description container for one parameter (same keys the user-data version used)."""
    if kind == "int":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_LONG)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_LONGSLIDER
    elif kind in ("float", "pct", "deg", "dist"):
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_REAL)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_REALSLIDER if kind != "dist" else c4d.CUSTOMGUI_REAL
        bc[c4d.DESC_UNIT] = {"pct": c4d.DESC_UNIT_PERCENT, "deg": c4d.DESC_UNIT_DEGREE,
                             "dist": c4d.DESC_UNIT_METER}.get(kind, c4d.DESC_UNIT_FLOAT)
        bc[c4d.DESC_STEP] = extra.get("step", {"pct": 0.01, "deg": 0.017453292519943295, "dist": 1.0}.get(kind, 0.01))
    elif kind == "bool":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_BOOL)
    elif kind == "cycle":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_LONG)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_CYCLE
        cyc = c4d.BaseContainer()
        items = extra.get("items") or (R.collection_list(op) if extra.get("items_fn") == "collections" else [])
        for i, s_ in enumerate(items or ["(none)"]):
            cyc.SetString(i, s_)
        bc[c4d.DESC_CYCLE] = cyc
    elif kind == "string":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STRING)
    elif kind == "color":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_COLOR)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_COLOR
    elif kind in ("folder", "file"):
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_FILENAME)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_FILENAME
        if kind == "folder":
            bc[c4d.FILENAME_DIRECTORY] = True
        bc[c4d.DESC_SCALEH] = True
    elif kind == "font":
        bc = c4d.GetCustomDataTypeDefault(c4d.FONTCHOOSER_DATA)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_FONTCHOOSER
        bc[c4d.DESC_SCALEH] = True
    elif kind == "shader":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_BASELISTLINK)
        bc[c4d.DESC_CUSTOMGUI] = getattr(c4d, "CUSTOMGUI_TEXBOX", 1000485)
        ac = c4d.BaseContainer()
        ac.SetInt32(c4d.Xbase, 1)
        bc[c4d.DESC_ACCEPT] = ac
        if hasattr(c4d, "DESC_SHADERLINKFLAG"):
            bc[c4d.DESC_SHADERLINKFLAG] = True
        bc[c4d.DESC_SCALEH] = True
    elif kind == "link":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_BASELISTLINK)
        ac = c4d.BaseContainer()
        ac.SetInt32(c4d.Obase, 1)
        bc[c4d.DESC_ACCEPT] = ac
    elif kind == "fields":
        bc = c4d.GetCustomDataTypeDefault(c4d.CUSTOMDATATYPE_FIELDLIST)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_FIELDLIST
    elif kind == "button":
        bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_BUTTON)
        bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_BUTTON
    else:
        raise ValueError(kind)
    bc[c4d.DESC_NAME] = name
    bc[c4d.DESC_SHORT_NAME] = name
    if "min" in extra:
        conv = (lambda v: v * 3.141592653589793 / 180.0) if kind == "deg" else (lambda v: v)
        bc[c4d.DESC_MIN] = conv(extra["min"])
        bc[c4d.DESC_MINSLIDER] = conv(extra["min"])
    if "max" in extra:
        conv = (lambda v: v * 3.141592653589793 / 180.0) if kind == "deg" else (lambda v: v)
        bc[c4d.DESC_MAX] = conv(extra["max"])
        bc[c4d.DESC_MAXSLIDER] = conv(extra["max"])
    if extra.get("hidden"):
        bc[c4d.DESC_HIDE] = True
    bc[c4d.DESC_ANIMATE] = c4d.DESC_ANIMATE_OFF if kind in ("button", "link", "shader", "fields", "folder",
                                                            "file", "font", "string") else c4d.DESC_ANIMATE_ON
    return bc


_STATES = {}        # object GUID -> solver state S (for the Standard colour-proxy sync, main thread)


def _state_key(op):
    try:
        return str(op.GetGUID())
    except Exception:
        return None


def sync_color_proxies(doc, force=False):
    """Main thread: copy fresh per-glyph colours onto every Glyph It colour proxy in doc
    (only objects that have one, i.e. use the Standard material). Cheap when nothing changed."""
    if doc is None:
        return 0
    n = 0
    stack = [doc.GetFirstObject()]
    while stack:
        o = stack.pop()
        while o is not None:
            if o.GetType() == PLUGIN_ID:
                S = _STATES.get(_state_key(o))
                if S is not None:
                    try:
                        if R.sync_color_proxy(o, S, force=force):
                            n += 1
                    except Exception as e:
                        print("GlyphIt colour proxy:", e)
            elif o.GetDown() is not None:
                stack.append(o.GetDown())
            o = o.GetNext()
    return n


class GlyphItObject(plugins.ObjectData):
    """The Glyph It generator."""

    def __init__(self):
        self.S = {}                     # per-object solver state (geometry, result, signatures)
        self.ui = {}                    # plate_pending / mat_pending / dragging

    def Free(self, node):
        _STATES.pop(_state_key(node), None)

    # ---- defaults
    def Init(self, node, isCloneInit=False):
        if isCloneInit:
            return True
        data = node.GetDataInstance()
        for name, kind, default, extra, pid in P.SPEC:
            if kind not in P.PARAM_KINDS or kind in ("button", "link", "shader"):
                continue
            try:
                if kind == "font":
                    data.SetData(pid, R.font_data(default))
                elif kind == "color":
                    data.SetVector(pid, c4d.Vector(*default))
                elif kind == "fields":
                    data.SetData(pid, c4d.FieldList())
                elif kind == "deg":
                    data.SetFloat(pid, float(default) * 3.141592653589793 / 180.0)
                elif kind in ("float", "pct", "dist"):
                    data.SetFloat(pid, float(default))
                elif kind in ("int", "cycle"):
                    data.SetInt32(pid, int(default))
                elif kind == "bool":
                    data.SetBool(pid, bool(default))
                elif kind == "string":
                    data.SetString(pid, str(default or ""))
                elif kind in ("folder", "file"):
                    data.SetFilename(pid, str(default or ""))  # DTYPE_FILENAME: a String shows blank in the AM
            except Exception as e:
                print("GlyphIt init %s: %s" % (name, e))
        data.SetFilename(P.NAME_TO_ID["Plates Folder"], os.path.join(HERE, "plates"))
        return True

    # ---- Attribute Manager
    def GetDDescription(self, node, description, flags):
        if not description.LoadDescription(node.GetType()):
            return False
        single = description.GetSingleDescID()
        parent = None
        stack = []
        objprops = c4d.DescID(c4d.DescLevel(c4d.ID_OBJECTPROPERTIES))
        root = c4d.DescID(c4d.DescLevel(0))
        for name, kind, default, extra, pid in P.SPEC:
            if kind == "tab":
                if name == "Grid":
                    parent = objprops          # the standard "Object" tab holds the Grid settings
                    stack = []
                    continue
                bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_GROUP)
                bc[c4d.DESC_NAME] = bc[c4d.DESC_SHORT_NAME] = name
                bc[c4d.DESC_COLUMNS] = 1
                bc[c4d.DESC_DEFAULT] = 1
                did = _desc_id(pid, c4d.DTYPE_GROUP)
                if single is None or did.IsPartOf(single)[0]:
                    # root-level group = its own Attribute Manager tab; if a C4D version refuses
                    # that, fall back to a group inside the Object tab
                    if not description.SetParameter(did, bc, root):
                        description.SetParameter(did, bc, objprops)
                parent = did
                stack = []
                continue
            if kind == "help":
                bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_GROUP)
                bc[c4d.DESC_NAME] = bc[c4d.DESC_SHORT_NAME] = "? How this tab works"
                bc[c4d.DESC_COLUMNS] = 1
                bc[c4d.DESC_DEFAULT] = 0
                gid = _desc_id(pid, c4d.DTYPE_GROUP)
                if single is None or gid.IsPartOf(single)[0]:
                    description.SetParameter(gid, bc, parent)
                for k, line in enumerate(P.HELP.get(name, [])):
                    t = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STATICTEXT)
                    t[c4d.DESC_NAME] = t[c4d.DESC_SHORT_NAME] = line
                    t[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_STATICTEXT
                    lid = _desc_id(pid + HELP_LINE_BASE + k, c4d.DTYPE_STATICTEXT)
                    if single is None or lid.IsPartOf(single)[0]:
                        description.SetParameter(lid, t, gid)
                continue
            if kind == "row":
                bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_GROUP)
                bc[c4d.DESC_NAME] = bc[c4d.DESC_SHORT_NAME] = ""
                bc[c4d.DESC_COLUMNS] = int(extra.get("columns", 2))
                bc[c4d.DESC_DEFAULT] = 1
                bc[c4d.DESC_TITLEBAR] = False
                gid = _desc_id(pid, c4d.DTYPE_GROUP)
                if single is None or gid.IsPartOf(single)[0]:
                    description.SetParameter(gid, bc, parent)
                stack.append(parent)
                parent = gid
                continue
            if kind == "endrow":
                parent = stack.pop() if stack else parent
                continue
            did = _desc_id(pid, _DTYPE[kind])
            if single is not None and not did.IsPartOf(single)[0]:
                continue
            try:
                description.SetParameter(did, _param_bc(name, kind, extra, node), parent)
            except Exception as e:
                print("GlyphIt description %s: %s" % (name, e))
        return (True, flags | c4d.DESCFLAGS_DESC_LOADED)

    def GetDEnabling(self, node, id, t_data, flags, itemdesc):
        try:
            name = P.ID_TO_NAME.get(id[0].id)
            if name is None:
                return True
            g = R.param_getter(node)
            src = int(g("Value Source", 0) or 0)
            mode = int(g("Color Mode", 0) or 0)
            if name in ("Image File", "Image Shader", "Image Sampling", "Image Speed", "Image Frame Offset", "Image Loop"):
                return src == R.SRC_IMAGE or mode == R.COLOR_IMAGE
            if name in ("Directional Light", "Light Wrap"):
                return src == R.SRC_LIGHT
            if name == "Fields":
                return src == R.SRC_FIELD
            if name == "Vertex Map Name":
                return src == R.SRC_VMAP
            if name == "Distance Period":
                return src == R.SRC_DIST
            if name == "Curvature Scale":
                return src == R.SRC_CURV
            if name == "Target Object":
                return src in (R.SRC_LIGHT, R.SRC_DIST, R.SRC_CAMERA)
            if name in ("Random Amount", "Hue Spread", "Random Saturation", "Brightness Jitter", "Color Seed"):
                return mode in (1, 2)
            if name == "Weights":
                return int(g("Distribute", 0) or 0) == 1
            if name == "Level Weights":
                return int(g("Grid Mode", 0) or 0) == 1
            if name in ("Cluster Size",):
                return int(g("Island Mode", 1) or 0) == 2
            if name in ("Quadtree Block (polys)", "Quadtree Levels", "Subdivide Chance"):
                return int(g("Island Mode", 1) or 0) == 3
            if name in ("Custom Glyphs", "Font", "Sort by Ink"):
                return int(g("Plate Style", 0) or 0) == 7
            if name in ("Collection", "Open Collection"):
                return int(g("Plate Style", 0) or 0) == 8
        except Exception:
            pass
        return True

    # ---- the generator
    def CheckDirty(self, op, doc):
        # animated image sources, fields and lights change without touching our parameters:
        # the solver's own signatures decide; make sure we get asked every pass when needed
        try:
            g = R.param_getter(op)
            src = int(g("Value Source", 0) or 0)
            if g("Rebuild Every Frame", False) or src == R.SRC_IMAGE or int(g("Color Mode", 0) or 0) == R.COLOR_IMAGE \
                    or src in (R.SRC_FIELD, R.SRC_LIGHT, R.SRC_CAMERA, R.SRC_DIST):
                op.SetDirty(c4d.DIRTYFLAGS_DATA)
        except Exception:
            pass

    def GetVirtualObjects(self, op, hh):
        doc = op.GetDocument() or c4d.documents.GetActiveDocument()
        k = _state_key(op)
        if k is not None and _STATES.get(k) is not self.S:
            _STATES[k] = self.S
        try:
            out = GEN.build(op, hh, doc, self.S)
        except Exception as e:
            import traceback
            traceback.print_exc()
            c4d.gui.StatusSetText("GlyphIt: %s" % e)
            out = None
        if out is None:
            return c4d.BaseObject(c4d.Onull)
        return out

    # ---- messages: buttons, live plate / material edits (main thread, active doc only)
    def Message(self, node, type, data):
        try:
            if type == c4d.MSG_MENUPREPARE:
                return True
            d_ = node.GetDocument()
            if type == c4d.MSG_MULTI_RENDERNOTIFICATION:
                # render about to start (Picture Viewer / Render View): the Standard material reads
                # the colour proxy, make sure it carries this build's colours
                try:
                    if isinstance(data, dict) and data.get("start") and c4d.threading.GeIsMainThread():
                        R.sync_color_proxy(node, self.S)
                except Exception as e:
                    print("GlyphIt colour proxy:", e)
                return True
            if not c4d.threading.GeIsMainThread() or d_ is None or d_ != c4d.documents.GetActiveDocument():
                return True
            ui = self.ui
            AM = (c4d.MSG_DESCRIPTION_POSTSETPARAMETER, c4d.MSG_DESCRIPTION_COMMAND,
                  getattr(c4d, "MSG_DESCRIPTION_USERINTERACTION_END", -2),
                  getattr(c4d, "MSG_DESCRIPTION_CHECKUPDATE", -3), getattr(c4d, "MSG_DESCRIPTION_VALIDATE", -4))
            if (ui.get("plate_pending") or ui.get("mat_pending")) and not ui.get("dragging") \
                    and type in AM and not _mouse_down():
                if ui.pop("plate_pending", False):
                    self._apply_plate(node)
                if ui.pop("mat_pending", False):
                    R.sync_material_color(node)
            if type == getattr(c4d, "MSG_DESCRIPTION_USERINTERACTION_BEGIN", -1):
                ui["dragging"] = True
            elif type == getattr(c4d, "MSG_DESCRIPTION_USERINTERACTION_END", -2):
                ui["dragging"] = False
                if ui.pop("plate_pending", False):
                    self._apply_plate(node)
                if ui.pop("mat_pending", False):
                    R.sync_material_color(node)
            elif type == c4d.MSG_DESCRIPTION_COMMAND:
                did = data.get("id") if isinstance(data, dict) else None
                hit = P.ID_TO_NAME.get(did[0].id) if did is not None else None
                self._button(node, hit)
            elif type == c4d.MSG_DESCRIPTION_POSTSETPARAMETER:
                did = data.get("descid") if isinstance(data, dict) else None
                name = P.ID_TO_NAME.get(did[0].id) if did is not None else None
                if name is None:
                    return True
                g = R.param_getter(node)
                if name in P.COLOR_KEYS:
                    if ui.get("dragging") or _mouse_down():
                        ui["mat_pending"] = True
                    else:
                        R.sync_material_color(node)
                if name in P.LIMIT_KEYS:
                    R.apply_slider_caps(node, name)
                if name == "Collection":
                    items = R.collection_list(node)
                    i = int(g("Collection", 0) or 0)
                    node[P.NAME_TO_ID["Collection Name"]] = items[i] if 0 <= i < len(items) else ""
                    if int(g("Plate Style", 0) or 0) != 8:
                        node[P.NAME_TO_ID["Plate Style"]] = 8
                if name == "Plates Folder":
                    R.set_cycle_items(node, "Collection", R.collection_list(node))
                if name in P.PLATE_KEYS:
                    if ui.get("dragging") or _mouse_down():
                        ui["plate_pending"] = True
                    else:
                        self._apply_plate(node)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("GlyphIt message:", e)
        return True

    def _apply_plate(self, node, force=False):
        g = R.param_getter(node)
        if not force and not g("Auto Swap Plate", True):
            return
        folder, _cd = R.plate_dirs(g("Plates Folder", "") or "")
        if not folder:
            return
        if int(g("Plate Style", 0) or 0) == 8 and R.cycle_text(node, "Collection").strip() in ("", "(none)"):
            return
        try:
            path = R.plate_path(folder, g("Plate Style", 0), int(g("Grid (N x N)", 4)), g("Custom Glyphs", ""),
                                R.font_name(g("Font", None)), bool(g("Sort by Ink", True)),
                                collection=R.cycle_text(node, "Collection"), mixed=int(g("Grid Mode", 0) or 0) == 1)
        except Exception as e:
            c4d.gui.StatusSetText("GlyphIt plate: %s" % e)
            print("GlyphIt plate:", e)
            return
        if path == self.ui.get("plate_applied") and not force:
            return
        n = R.swap_plate(node, path, folder)
        self.ui["plate_applied"] = path
        c4d.gui.StatusSetText("GlyphIt plate -> %s (%d texture%s)" % (os.path.basename(path), n, "" if n == 1 else "s"))

    def _button(self, node, hit):
        g = R.param_getter(node)
        root = g("Plates Folder", "") or ""
        folder, cdir = R.plate_dirs(root)
        doc = node.GetDocument()
        if hit == "Apply Plate Now":
            self._apply_plate(node, force=True)
        elif hit in ("Standard", "Redshift", "Octane"):
            R.build_glyph_material(doc, node, hit.lower())
        elif hit == "Bake to Polygon Object":
            _bake(node, self.S.get("result"))
        elif hit == "Write UVs onto Source":
            msg = R.write_result_to_source(node, self.S.get("result"))
            if msg:
                c4d.gui.MessageDialog(msg)
        elif hit == "Open Plates Folder" and root:
            R.open_in_finder(root)
        elif hit == "Clear Plate Cache" and root:
            n_ = R.clear_plate_cache(root)
            self.ui.pop("plate_applied", None)
            R.refresh_generators(doc)
            c4d.gui.StatusSetText("GlyphIt: cleared %d cached plate file(s)" % n_)
        elif hit == "Open Collection" and cdir:
            cname = R.cycle_text(node, "Collection").strip()
            R.open_in_finder(os.path.join(cdir, cname) if cname and cname != "(none)" else cdir)
        elif hit == "Refresh List" and cdir:
            R.set_cycle_items(node, "Collection", R.collection_list(node))
            c4d.gui.StatusSetText("GlyphIt: %d collections" % len(R.collection_list(node)))
        elif hit == "New Collection" and cdir:
            path = R.new_collection(cdir)
            names_ = R.collection_names(cdir)
            node[P.NAME_TO_ID["Collection Name"]] = os.path.basename(path.rstrip("/\\"))
            R.set_cycle_items(node, "Collection", names_, keep=True)
            node[P.NAME_TO_ID["Plate Style"]] = 8
            R.open_in_finder(path)
        c4d.EventAdd()


def _mouse_down():
    try:
        bc = c4d.BaseContainer()
        if c4d.gui.GetInputState(c4d.BFM_INPUT_MOUSE, c4d.BFM_INPUT_MOUSELEFT, bc):
            return bool(bc.GetInt32(c4d.BFM_INPUT_VALUE))
    except Exception:
        pass
    return False


def _bake(op, res):
    """Editable copy of the current output (UVW + selection + colour tags + material), generator off."""
    d = op.GetDocument()
    if d is None or res is None:
        c4d.gui.StatusSetText("GlyphIt bake: nothing built yet")
        return
    from glyphit import api
    out = api.bake(d, op, res=res, undo=True)
    c4d.gui.StatusSetText("GlyphIt: baked %d object(s), generator switched off and hidden" % len(out)
                          if out else "GlyphIt bake: nothing built yet")


# ------------------------------------------------------------- converter ---
class ConvertGlyphGrid(plugins.CommandData):
    """Replaces every legacy GlyphGrid Python Generator in the active document with a Glyph It object
    (same name, children, tags, parameters) and re-targets its materials' colour hook."""

    def Execute(self, doc):
        from glyphit import convert
        n = convert.convert_document(doc)
        c4d.gui.MessageDialog("Glyph It: converted %d GlyphGrid object(s)." % n if n else
                              "Glyph It: no GlyphGrid objects found in this scene.")
        return True


class GlyphItSync(plugins.MessageData):
    """Core-message listener: after every scene change / frame change (main thread) push the
    generators' per-glyph colours onto their Standard colour proxies."""

    def CoreMessage(self, id, bc):
        if id in (c4d.EVMSG_CHANGE, c4d.EVMSG_TIMECHANGED):
            try:
                sync_color_proxies(c4d.documents.GetActiveDocument())
            except Exception as e:
                print("GlyphIt sync:", e)
        return True


def _icon(name):
    bmp = bitmaps.BaseBitmap()
    p = os.path.join(HERE, "res", "icons", name)
    if os.path.exists(p) and bmp.InitWith(p)[0] == c4d.IMAGERESULT_OK:
        return bmp
    return None


if __name__ == "__main__":
    ok = plugins.RegisterObjectPlugin(id=PLUGIN_ID, str="Glyph It", g=GlyphItObject, description="Oglyphit",
                                      icon=_icon("glyphit.png"),
                                      info=c4d.OBJECT_GENERATOR | c4d.OBJECT_INPUT)
    plugins.RegisterCommandPlugin(id=CONVERT_ID, str="Convert GlyphGrid scenes to Glyph It", info=0,
                                  icon=_icon("glyphit.png"), help="Replace legacy GlyphGrid Python Generators",
                                  dat=ConvertGlyphGrid())
    plugins.RegisterMessagePlugin(id=SYNC_ID, str="Glyph It colour sync", info=0, dat=GlyphItSync())
    print("Glyph It for Cinema 4D loaded" if ok else "Glyph It: object registration failed")
