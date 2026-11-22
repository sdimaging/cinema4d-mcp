#!/usr/bin/env python3
"""
Exercise the plugin's Python-level paths with a mock `c4d`: registration, Init, GetDDescription,
GetDEnabling, Message (buttons / parameter edits), params_from(), convert name mapping.
It can't prove Cinema 4D behaviour, but it catches NameErrors, wrong signatures, bad ids and
broken imports before the real test on a Mac.

    python3 tools/mock_test.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------ mock c4d ---
class Vector(object):
    def __init__(self, x=0.0, y=None, z=None):
        if y is None and z is None:
            y = z = x
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __mul__(self, o):
        return Vector(self.x * o, self.y * o, self.z * o) if isinstance(o, (int, float)) else o

    __rmul__ = __mul__


class BaseContainer(dict):
    def SetInt32(self, k, v): self[k] = int(v)
    def SetFloat(self, k, v): self[k] = float(v)
    def SetString(self, k, v): self[k] = str(v)
    def SetFilename(self, k, v): self[k] = str(v)
    def SetBool(self, k, v): self[k] = bool(v)
    def SetVector(self, k, v): self[k] = v
    def SetData(self, k, v): self[k] = v
    def GetInt32(self, k, d=0): return self.get(k, d)
    def GetString(self, k, d=""): return self.get(k, d)

    def __iter__(self):
        return iter(list(dict.items(self)))


class DescLevel(object):
    def __init__(self, id, dtype=0, creator=0):
        self.id, self.dtype, self.creator = id, dtype, creator


class DescID(object):
    def __init__(self, *levels):
        self.levels = [l if isinstance(l, DescLevel) else DescLevel(l) for l in levels] or [DescLevel(0)]

    def __getitem__(self, i): return self.levels[i]
    def __len__(self): return len(self.levels)
    def GetDepth(self): return len(self.levels)
    def IsPartOf(self, other): return (self.levels[0].id == other.levels[0].id, 0)
    def __eq__(self, o): return isinstance(o, DescID) and [l.id for l in self.levels] == [l.id for l in o.levels]


class Description(object):
    def __init__(self):
        self.params = {}
        self.loaded = False

    def LoadDescription(self, t):
        self.loaded = True
        return True

    def GetSingleDescID(self):
        return None

    def SetParameter(self, did, bc, parent):
        self.params[did[0].id] = (bc, parent[0].id if parent is not None else None)
        return True


class BaseList2D(object):
    def __init__(self, t=0):
        self._t = t
        self._data = BaseContainer()
        self._tags = []
        self._name = "obj"
        self._down = None
        self._next = None
        self._up = None
        self._doc = None

    def GetType(self): return self._t
    def GetName(self): return self._name
    def SetName(self, n): self._name = n
    def GetDataInstance(self): return self._data
    def __getitem__(self, k): return self._data.get(k)
    def __setitem__(self, k, v): self._data[k] = v
    def GetDocument(self): return self._doc
    def GetFirstTag(self): return self._tags[0] if self._tags else None
    def GetTags(self): return list(self._tags)
    def GetDown(self): return self._down
    def GetNext(self): return self._next
    def GetUp(self): return self._up
    def SetDirty(self, f): pass
    def GetDirty(self, f): return 0
    def GetUserDataContainer(self): return []
    def GetGUID(self): return id(self)
    def GetTag(self, t): return None
    def GetInfo(self): return 0
    def GetMg(self): return "MG"


class BaseObject(BaseList2D):
    pass


class BaseDocument(object):
    def __init__(self):
        self._objs = []
        self._mats = []

    def GetFirstObject(self): return self._objs[0] if self._objs else None
    def GetMaterials(self): return list(self._mats)
    def InsertObject(self, o, parent=None, pred=None): self._objs.append(o); o._doc = self
    def StartUndo(self): pass
    def EndUndo(self): pass
    def AddUndo(self, *a): pass


class FieldList(object):
    pass


class FontData(object):
    def SetFont(self, d): pass
    def GetFont(self): return BaseContainer()


def _const_module():
    c4d = types.ModuleType("c4d")
    consts = dict(
        DTYPE_LONG=15, DTYPE_REAL=19, DTYPE_BOOL=400006001, DTYPE_STRING=130, DTYPE_BASELISTLINK=133,
        DTYPE_FILENAME=1000492, DTYPE_COLOR=3, DTYPE_BUTTON=1000525, DTYPE_GROUP=1, DTYPE_STATICTEXT=1000998,
        DTYPE_VECTOR=23, CUSTOMDATATYPE_FIELDLIST=440000200, FONTCHOOSER_DATA=1009371,
        CUSTOMGUI_LONGSLIDER=1000490, CUSTOMGUI_REALSLIDER=1000489, CUSTOMGUI_REAL=1000488, CUSTOMGUI_CYCLE=200000180,
        CUSTOMGUI_COLOR=1000492, CUSTOMGUI_FILENAME=1000491, CUSTOMGUI_FONTCHOOSER=1009371, CUSTOMGUI_TEXBOX=1000485,
        CUSTOMGUI_FIELDLIST=440000201, CUSTOMGUI_BUTTON=1000525, CUSTOMGUI_STATICTEXT=1000998,
        DESC_NAME=1, DESC_SHORT_NAME=2, DESC_CUSTOMGUI=10, DESC_UNIT=11, DESC_STEP=12, DESC_MIN=13, DESC_MAX=14,
        DESC_MINSLIDER=15, DESC_MAXSLIDER=16, DESC_CYCLE=17, DESC_COLUMNS=18, DESC_DEFAULT=19, DESC_TITLEBAR=20,
        DESC_SCALEH=21, DESC_HIDE=22, DESC_ANIMATE=23, DESC_ANIMATE_OFF=0, DESC_ANIMATE_ON=1, DESC_ACCEPT=24,
        DESC_SHADERLINKFLAG=25, DESC_UNIT_PERCENT=1, DESC_UNIT_DEGREE=2, DESC_UNIT_METER=3, DESC_UNIT_FLOAT=0,
        FILENAME_DIRECTORY=1, DESCFLAGS_DESC_LOADED=2, ID_OBJECTPROPERTIES=1100, Xbase=5700, Obase=5155,
        Opython=1023866, Onull=5140, Opolygon=5100, Ttexture=5616, Tuvw=5671, Tvertexcolor=431000045,
        Tpolygonselection=5673, Tpython=5620, Tvertexmap=5682, Tphong=5612, ID_USERDATA=700,
        MSG_MENUPREPARE=1, MSG_DESCRIPTION_COMMAND=2, MSG_DESCRIPTION_POSTSETPARAMETER=3,
        MSG_DESCRIPTION_USERINTERACTION_END=31, MSG_DESCRIPTION_CHECKUPDATE=4, MSG_DESCRIPTION_VALIDATE=5,
        DIRTYFLAGS_DATA=1, DIRTYFLAGS_MATRIX=2, DIRTYFLAGS_CACHE=4, DIRTYFLAGS_DESCRIPTION=8,
        OBJECT_GENERATOR=1, OBJECT_INPUT=2, OBJECT_MODIFIER=4, OBJECT_UNDEF=2, ID_BASEOBJECT_GENERATOR_FLAG=901,
        ID_BASEOBJECT_VISIBILITY_EDITOR=902, EVMSG_CHANGE=604, EVMSG_TIMECHANGED=605, MSG_MULTI_RENDERNOTIFICATION=606, Xvertexmap=1011137, OBJECT_OFF=1, ID_BASEOBJECT_XRAY=904, ID_BASEOBJECT_VISIBILITY_RENDER=903, IMAGERESULT_OK=1,
        BFM_INPUT_MOUSE=1, BFM_INPUT_MOUSELEFT=1, BFM_INPUT_VALUE=1, Mmaterial=5703, UNDOTYPE_NEW=1,
        UNDOTYPE_CHANGE_SMALL=2, UNDOTYPE_CHANGE=3, UNDOTYPE_DELETEOBJ=4, UNDOTYPE_NEWOBJ=5,
        COPYFLAGS_NO_HIERARCHY=1, GE_FONT_NAME_POSTSCRIPT=3, GE_FONT_NAME_FAMILY=1, GE_FONT_NAME_STYLE=2,
        GE_FONT_NAME_DISPLAY=4, HDIRTYFLAGS_OBJECT_HIERARCHY=1,
    )
    for k, v in consts.items():
        setattr(c4d, k, v)
    c4d.Vector = Vector
    c4d.BaseContainer = BaseContainer
    c4d.DescID = DescID
    c4d.DescLevel = DescLevel
    c4d.BaseObject = BaseObject
    c4d.BaseList2D = BaseList2D
    c4d.FieldList = FieldList
    c4d.FontData = FontData
    c4d.BaseShader = BaseList2D
    c4d.GetCustomDataTypeDefault = lambda t: BaseContainer()
    c4d.EventAdd = lambda *a: None

    class _Threading:
        @staticmethod
        def GeIsMainThread(): return True
    c4d.threading = _Threading()

    class _Gui:
        @staticmethod
        def StatusSetText(s): print("  [status]", s)
        @staticmethod
        def MessageDialog(s): print("  [dialog]", s)
        @staticmethod
        def GetInputState(*a): return False
    c4d.gui = _Gui()

    class _Docs:
        _active = BaseDocument()
        @staticmethod
        def GetActiveDocument(): return _Docs._active
    c4d.documents = _Docs()

    class _Plugins:
        class ObjectData(object):
            pass

        class CommandData(object):
            pass

        class MessageData(object):
            pass
        calls = []

        @staticmethod
        def RegisterMessagePlugin(**kw): _Plugins.calls.append(("message", kw)); return True

        @staticmethod
        def RegisterObjectPlugin(**kw): _Plugins.calls.append(("object", kw)); return True

        @staticmethod
        def RegisterCommandPlugin(**kw): _Plugins.calls.append(("command", kw)); return True

        @staticmethod
        def FindPlugin(i, t): return None
    c4d.plugins = _Plugins()

    class _Bitmaps:
        class BaseBitmap(object):
            def InitWith(self, p): return (1, 0)

        class GeClipMap(object):
            @staticmethod
            def GetFontDescription(n, t): return None
    c4d.bitmaps = _Bitmaps()
    c4d.modules = types.SimpleNamespace(render=types.SimpleNamespace(), mograph=types.SimpleNamespace())
    c4d.utils = types.SimpleNamespace()
    return c4d


def main():
    c4d = _const_module()
    sys.modules["c4d"] = c4d
    for sub in ("plugins", "bitmaps", "gui", "documents", "threading", "utils", "modules"):
        sys.modules["c4d." + sub] = getattr(c4d, sub)
    sys.modules["maxon"] = types.ModuleType("maxon")
    sys.path.insert(0, HERE)
    import runpy
    g = runpy.run_path(os.path.join(HERE, "GlyphIt.pyp"), run_name="__main__")
    calls = c4d.plugins.calls
    assert [c[0] for c in calls] == ["object", "command", "message"], calls
    assert calls[0][1]["id"] == 1071110 and calls[0][1]["description"] == "Oglyphit"
    print("register ok:", calls[0][1]["str"], "/", calls[1][1]["str"])

    Obj = g["GlyphItObject"]
    P = g["P"]
    inst = Obj()
    node = BaseObject(P.PLUGIN_ID)
    doc = c4d.documents.GetActiveDocument()
    doc.InsertObject(node)
    assert inst.Init(node)
    assert node[P.NAME_TO_ID["Grid (N x N)"]] == 4 and node[P.NAME_TO_ID["Plates Folder"]].endswith("plates")
    print("init ok:", len(node.GetDataInstance()), "values")

    desc = Description()
    r = inst.GetDDescription(node, desc, 0)
    assert r == (True, c4d.DESCFLAGS_DESC_LOADED), r
    n_params = len([i for i in desc.params if i in P.ID_TO_NAME])
    n_help = len([i for i in desc.params if i > 11000])
    assert n_params == len(P.NAME_TO_ID), (n_params, len(P.NAME_TO_ID))
    assert desc.params[P.NAME_TO_ID["Grid (N x N)"]][1] == c4d.ID_OBJECTPROPERTIES
    assert desc.params[P.NAME_TO_ID["Random Amount"]][1] == 10500   # Color tab group
    print("description ok:", n_params, "params,", n_help, "help lines,", len(desc.params), "entries")

    for name in ("Light Wrap", "Fields", "Image File", "Weights", "Random Amount", "Grid (N x N)"):
        did = DescID(DescLevel(P.NAME_TO_ID[name]))
        inst.GetDEnabling(node, did, None, 0, None)
    node[P.NAME_TO_ID["Value Source"]] = 9
    assert inst.GetDEnabling(node, DescID(DescLevel(P.NAME_TO_ID["Image File"])), None, 0, None)
    print("enabling ok")

    from glyphit import generator as GEN, runtime as R, convert
    params, src, opts, flags = GEN.params_from(node)
    assert params["grid"] == 4 and src == 9 and opts["color"]["mode"] == 0 and flags["lock"] is True
    assert opts["image"] is None
    node[P.NAME_TO_ID["Image File"]] = "/tmp/x.png"
    assert GEN.params_from(node)[2]["image"]["file"] == "/tmp/x.png"
    print("params_from ok")

    # messages: a colour edit, a limit edit, the buttons
    pid = P.NAME_TO_ID["Quadtree Levels"]
    node[pid] = 4
    node[P.NAME_TO_ID["Quadtree Block (polys)"]] = 1
    inst.Message(node, c4d.MSG_DESCRIPTION_POSTSETPARAMETER, {"descid": DescID(DescLevel(pid))})
    assert node[P.NAME_TO_ID["Quadtree Block (polys)"]] == 3, "levels 4 should grow the block to 8 polys"
    for b in ("Open Plates Folder", "Refresh List", "Apply Plate Now", "Bake to Polygon Object"):
        inst.Message(node, c4d.MSG_DESCRIPTION_COMMAND, {"id": DescID(DescLevel(P.NAME_TO_ID[b]))})
    print("messages ok")

    # legacy name mapping used by the converter
    assert P.LEGACY_NAMES["Cell Shift"] == "Glyph Offset"
    assert R.param_setter(node) and R.cycle_text(node, "Plate Style") == "1 ASCII ramp"
    print("ALL OK")


if __name__ == "__main__":
    main()
