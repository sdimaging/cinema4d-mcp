"""
sn_build_rd_growth.py — build a Gray-Scott reaction-diffusion Scene Nodes Deformer from scratch.

Cinema 4D 2026.x, Neutron nodespace (net.maxon.neutron.nodespace).
Run inside C4D (Script Manager, or any MCP python runner such as Maxon's built-in
2026.4 MCP `exec_python`), with the target object selected or passed by name:

    exec(open(".../sn_build_rd_growth.py").read())
    build_rd_growth(doc, doc.SearchObject("MySphere"))

Result: a "RD_Growth" Scene Nodes Deformer under the host. Drop it under ANY polygon/primitive
object. The pattern is carried per point index (topology mode), so deforming hosts keep the
pattern stuck to the surface. AM params: Feed, Kill, Diffusion A/B, Time Step, Speed,
Height, Thickness, Seed Radius, Seed Threshold, Seed Noise Scale, Seed, Subdivide.
Writes an `rd` Vertex Map (0..1) on the deformed cache for shading.

Architecture (verified 2026-10-01, C4D 2026.4):
  root.geometryin -> Subdivide -> gp(get_property Position)
  init:   iterate P -> noise/disk seed -> compose(1, seed, 0) -> build+write -> S0 array
  Memory(types._0 <- TypeOf(S0), initial._0 <- S0, +ports geo/feed/kill/da/db/dt/steps)
    body (INSIDE the memory capsule view):
      LCV(types._0 <- TypeOf(current), initial._0 <- current, innerdomain <- Range(end=steps).innerdomain)
        body (INSIDE the LCV view): Gray-Scott explicit step
          per point i:  nbr = Neighbor(i)  -> inner iterate -> read S[nbr] -> Sum(inner/outer domain)
                        avg = (sum + S[i]) / (n + 1)       # self-inclusive average = stable at dt=1
                        L = avg - S[i]; A' = A + dt(Da*LA - AB^2 + F(1-A)); B' = B + dt(Db*LB + AB^2 - (K+F)B)
          collect -> next._0
      LCV.final._0 -> memory next._0
  out:  rd = smoothstep(lo,hi,B) (Thickness); P + Normal * rd * Height -> set_property(Position)
        -> set_property(weight 'rd') -> root.geometryout   (rd becomes a Vertex Map tag)
"""
import c4d
import maxon

NS = maxon.Id("net.maxon.neutron.nodespace")
A = {
    "get": "net.maxon.neutron.geometry.get_property", "set": "net.maxon.neutron.geometry.set_property",
    "iter": "net.maxon.node.containeriteration", "ar": "net.maxon.node.arithmetic",
    "rv": "net.maxon.node.array.readvalueatindex", "bld": "net.maxon.node.array.buildfromsinglevalue",
    "wr": "net.maxon.node.array.writevalueatindex", "mem": "net.maxon.node.memory",
    "lcv": "net.maxon.node.loopcarriedvalue", "range": "net.maxon.neutron.node.range",
    "tof": "net.maxon.node.typeof", "split": "net.maxon.pattern.node.conversion.splitvectorcomponents",
    "comp": "net.maxon.pattern.node.conversion.composevector3", "clamp": "net.maxon.node.clamp",
    "cmp": "net.maxon.node.compare", "len": "net.maxon.node.length", "nbr": "net.maxon.neutron.geometry.neighbor",
    "sum": "net.maxon.node.sum", "noise": "net.maxon.node.noise", "subd": "net.maxon.neutron.modeling.subdivide",
    "gennrm": "net.maxon.neutron.asset.geo.generatepointnormals", "ss": "net.maxon.node.smoothstep",
}
VEC3 = maxon.Id("net.maxon.parametrictype.vec<3,float>")      # OK for arithmetic/sum datatype
DATA3D = maxon.Id("net.maxon.geometryabstraction.accessortypes.attributes.data3d")
NORMAL = maxon.Id("net.maxon.geometryabstraction.accessortypes.attributes.normal")
WEIGHT = maxon.Id("net.maxon.geometryabstraction.accessortypes.attributes.weight")   # -> Vertex Map tag on the cache
SUBD_SUB = "net.maxon.command.modeling.subdivide.subdivisions"   # 0 = passthrough; 'iterations' does nothing


class _V:
    """Tiny builder bound to one graph view (top graph, or a capsule interior view)."""

    def __init__(self, view):
        self.v = view

    def root(self):
        return self.v.GetViewRoot()

    def n(self, label):
        for c in self.root().GetChildren():
            if str(c.GetId()) == label:
                return c
        raise KeyError(label)

    def add(self, label, kind, **vals):
        with self.v.BeginTransaction() as tx:
            self.v.AddChild(maxon.Id(label), maxon.Id(A[kind]))
            tx.Commit()
        if vals:
            self.set(label, **vals)

    def set(self, label, **vals):
        nd = self.n(label)
        with self.v.BeginTransaction() as tx:
            for k, val in vals.items():
                nd.GetInputs().FindChild(maxon.InternedId(k)).SetPortValue(val)
            tx.Commit()

    def _port(self, ref, out):
        label, port = ref.split(".", 1)
        if label == "ROOT":   # root inputs act as sources, root outputs as sinks
            lst = self.root().GetInputs() if out else self.root().GetOutputs()
        else:
            nd = self.n(label)
            lst = nd.GetOutputs() if out else nd.GetInputs()
        p = lst
        for part in port.split("/"):          # "types/_0" for variadic children
            p = p.FindChild(maxon.InternedId(part))
        return p

    def w(self, src, dst):
        a, b = self._port(src, True), self._port(dst, False)
        with self.v.BeginTransaction() as tx:
            a.Connect(b)
            tx.Commit()

    def ws(self, pairs):
        for a, b in pairs:
            self.w(a, b)

    def math(self, label, op, a=None, b=None, vec=False):
        # GOTCHA: never set datatype to parametrictype.float — every op silently becomes `add`.
        self.add(label, "ar")
        if vec:
            self.set(label, datatype=VEC3)
        self.set(label, operation=maxon.Id(op))   # valid: add / sub / mul / div  (sub = in1 - in2)
        for port, val in (("in1", a), ("in2", b)):
            if val is None:
                continue
            if isinstance(val, str):
                self.w(val, f"{label}.{port}")
            else:
                self.set(label, **{port: maxon.Float64(val)})
        return f"{label}.out"

    def view(self, label):
        return _V(self.v.CreateView(maxon.NODE_KIND.NODE, self.n(label).GetPath()))

    def addport(self, label, name):
        with self.v.BeginTransaction() as tx:
            self.n(label).GetInputs().AddPort(maxon.Id(name))
            tx.Commit()

    def expose(self, name, label, default, targets):
        """AM parameter on the capsule root: AddPort + typed default + label + Connect (connection types the widget)."""
        with self.v.BeginTransaction() as tx:
            prt = self.root().GetInputs().AddPort(maxon.Id(name))
            prt.SetPortValue(default)
            prt.SetValue(maxon.InternedId("net.maxon.node.base.name"), maxon.String(label))
            for t in targets:
                prt.Connect(self._port(t, False))
            tx.Commit()


def _set_props(v, label, name):
    v.set(label, accessortype=DATA3D, accessorname=maxon.String(name),
          arraymode=maxon.Bool(False), newdataset=maxon.Bool(False))


def _gray_scott_step(L, S, GEO, OUT):
    """One explicit Gray-Scott step on vec3 array S=(A,B,0). Params come from L's root ports."""
    L.add("it", "iter"); L.add("nb", "nbr"); L.add("itN", "iter"); L.add("rvn", "rv"); L.add("sum", "sum")
    L.add("cnt", "comp"); L.add("sA", "split"); L.add("sL", "split"); L.add("cN", "comp")
    L.add("bld", "bld"); L.add("wr", "wr"); L.add("clA", "clamp"); L.add("clB", "clamp")
    L.set("sum", datatype=VEC3)
    L.ws([(S, "it.in"), ("it.index", "nb.index"), (GEO, "nb.geometryin"), ("nb.neighborids", "itN.in"),
          (S, "rvn.arrayin"), ("itN.out", "rvn.indexin"),           # rv datatype LEFT UNSET (else 0 verts)
          ("rvn._0", "sum.values"), ("itN.innerdomain", "sum.innerdomain"), ("itN.outerdomain", "sum.outerdomain"),
          ("it.out", "sA.vector")])
    L.math("sumS", "add", "sum.out", "it.out", vec=True)            # include self -> stable Laplacian
    L.math("cp1", "add", "itN.count", 1.0)
    L.ws([("cp1.out", "cnt.x"), ("cp1.out", "cnt.y"), ("cp1.out", "cnt.z")])
    L.math("avg", "div", "sumS.out", "cnt.result", vec=True)
    L.math("lap", "sub", "avg.out", "it.out", vec=True)
    L.w("lap.out", "sL.vector")
    Av, Bv, LA, LB = "sA.x", "sA.y", "sL.x", "sL.y"
    L.math("ab", "mul", Av, Bv); L.math("abb", "mul", "ab.out", Bv)
    L.math("daL", "mul", LA, "ROOT.da"); L.math("t1", "sub", "daL.out", "abb.out")
    L.math("oma", "sub", 1.0, Av); L.math("foma", "mul", "oma.out", "ROOT.feed"); L.math("dA", "add", "t1.out", "foma.out")
    L.math("dbL", "mul", LB, "ROOT.db"); L.math("t2", "add", "dbL.out", "abb.out")
    L.math("kf", "add", "ROOT.kill", "ROOT.feed"); L.math("kfb", "mul", Bv, "kf.out"); L.math("dB", "sub", "t2.out", "kfb.out")
    L.math("dAt", "mul", "dA.out", "ROOT.dt"); L.math("nA", "add", Av, "dAt.out")
    L.math("dBt", "mul", "dB.out", "ROOT.dt"); L.math("nB", "add", Bv, "dBt.out")
    L.ws([("nA.out", "clA.in1"), ("nB.out", "clB.in1"), ("clA.out", "cN.x"), ("clB.out", "cN.y"),
          ("it.count", "bld.arraylengthin"), ("bld.arrayout", "wr.arrayin"), ("it.index", "wr.indexin")])
    L.w("cN.result", "wr._0")          # wr._0 only appears after arrayin is wired
    L.w("wr.arrayout", OUT)


def build_rd_growth(doc, host, name="RD_Growth"):
    d = c4d.BaseObject(180420400)                  # Scene Nodes Deformer
    d.Message(c4d.MSG_MENUPREPARE, doc)
    d.SetName(name)
    d.InsertUnderLast(host)
    d.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)  # without this root ports are empty stubs
    g = _V(d.GetNimbusRef(NS).GetGraph())

    # --- domain + positions
    g.add("subd", "subd", **{SUBD_SUB: maxon.Int64(0)})
    g.add("gp", "get")
    g.ws([("ROOT.geometryin", "subd.geometryin"), ("subd.geometryout", "gp.geometry")])

    # --- initial state S0 = (1, seed, 0)
    for lbl, k in (("it0", "iter"), ("len0", "len"), ("cmp0", "cmp"), ("snz", "noise"), ("scmp", "cmp"),
                   ("sclamp", "clamp"), ("comp0", "comp"), ("bld0", "bld"), ("wr0", "wr"), ("tof0", "tof")):
        g.add(lbl, k)
    g.set("len0", datatype=VEC3)
    g.set("cmp0", operation=maxon.Id("lt"), in2=maxon.Float64(0.0))
    g.set("scmp", operation=maxon.Id("gt"), in2=maxon.Float64(0.75))
    g.set("snz", scale=maxon.Float64(60.0))
    g.set("comp0", x=maxon.Float64(1.0))
    g.ws([("gp.array", "it0.in"), ("it0.out", "len0.in"), ("len0.out", "cmp0.in1"),
          ("it0.out", "snz.value"), ("snz.result", "scmp.in1")])
    g.math("sadd", "add", "cmp0.out", "scmp.out")
    g.ws([("sadd.out", "sclamp.in1"), ("sclamp.out", "comp0.y"),
          ("it0.count", "bld0.arraylengthin"), ("bld0.arrayout", "wr0.arrayin"), ("it0.index", "wr0.indexin")])
    g.w("comp0.result", "wr0._0")
    g.w("wr0.arrayout", "tof0.in")

    # --- memory (per-frame state). NEVER wire into the parent `types` port: it empties the list.
    g.add("mem", "mem")
    g.ws([("tof0.out", "mem.types/_0"), ("wr0.arrayout", "mem.initial._0")])
    params = ["geo", "feed", "kill", "da", "db", "dt", "steps"]
    for p in params:
        g.addport("mem", p)
    g.w("subd.geometryout", "mem.geo")

    m = g.view("mem")
    m.add("lp", "lcv"); m.add("rg", "range"); m.add("tofL", "tof")
    m.ws([("ROOT.current._0", "tofL.in"), ("tofL.out", "lp.types/_0"), ("ROOT.current._0", "lp.initial._0"),
          ("rg.innerdomain", "lp.innerdomain"), ("lp.final._0", "ROOT.next._0"), ("ROOT.steps", "rg.end")])
    for p in params[:-1]:
        m.addport("lp", p)
        m.w(f"ROOT.{p}", f"lp.{p}")
    _gray_scott_step(m.view("lp"), "ROOT.current._0", "ROOT.geo", "ROOT.next._0")

    # --- output: P + N * B * height
    g.add("gnrm", "gennrm"); g.add("gpn", "get", accessortype=NORMAL, accessorname=maxon.String("Normal"))
    for lbl, k in (("it1", "iter"), ("rvS", "rv"), ("spS", "split"), ("rvN", "rv"), ("hv", "comp"), ("sp", "set")):
        g.add(lbl, k)
    _set_props(g, "sp", "Position")   # newdataset=False wants "Position" (newdataset=True wants "")
    g.ws([("subd.geometryout", "gnrm.geometryin"), ("gnrm.geometryout", "gpn.geometry"),
          ("gp.array", "it1.in"), ("mem.nextout._0", "rvS.arrayin"), ("it1.index", "rvS.indexin"),
          ("rvS._0", "spS.vector")])
    # crisp tube profile: rd = smoothstep(c-0.06, c+0.06, B), c = 0.36 - 0.22*Thickness  (same as the Blender build)
    g.add("rdss", "ss")
    g.expose("thickness", "Thickness", maxon.Float64(0.5), [])
    g.math("thk", "mul", "ROOT.thickness", -0.22); g.math("ctr", "add", "thk.out", 0.36)
    g.math("lo", "sub", "ctr.out", 0.08); g.math("hi", "add", "ctr.out", 0.08)
    g.ws([("spS.y", "rdss.in1"), ("lo.out", "rdss.in2"), ("hi.out", "rdss.in3")])
    # softness: one self-inclusive neighbour blur of rd -> anti-aliased edges at vertex resolution.
    # rd is collected into an array (stream it1), then re-read per point in a second stream (it2).
    for lbl, k in (("bldR", "bld"), ("wrR", "wr"), ("it2", "iter"), ("nbR", "nbr"), ("itR", "iter"),
                   ("rvR", "rv"), ("sumR", "sum"), ("rvR0", "rv")):
        g.add(lbl, k)
    g.ws([("it1.count", "bldR.arraylengthin"), ("bldR.arrayout", "wrR.arrayin"), ("it1.index", "wrR.indexin")])
    g.w("rdss.out", "wrR._0")
    g.ws([("gp.array", "it2.in"), ("it2.index", "nbR.index"), ("subd.geometryout", "nbR.geometryin"),
          ("nbR.neighborids", "itR.in"), ("wrR.arrayout", "rvR.arrayin"), ("itR.out", "rvR.indexin"),
          ("rvR._0", "sumR.values"), ("itR.innerdomain", "sumR.innerdomain"), ("itR.outerdomain", "sumR.outerdomain"),
          ("wrR.arrayout", "rvR0.arrayin"), ("it2.index", "rvR0.indexin")])
    g.math("sumRS", "add", "sumR.out", "rvR0._0"); g.math("cntR", "add", "itR.count", 1.0)
    g.math("rdsoft", "div", "sumRS.out", "cntR.out")
    # displacement + attribute now live in the it2 stream
    g.math("hgt", "mul", "rdsoft.out", 20.0)
    g.ws([("hgt.out", "hv.x"), ("hgt.out", "hv.y"), ("hgt.out", "hv.z"), ("gpn.array", "rvN.arrayin"), ("it2.index", "rvN.indexin")])
    g.math("offN", "mul", "rvN._0", "hv.result", vec=True)
    g.math("newp", "add", "it2.out", "offN.out", vec=True)
    g.ws([("subd.geometryout", "sp.geometryin"), ("gp.topology", "sp.topology"), ("newp.out", "sp.iteration")])
    # rd (0..1) as a weight attribute -> shows up as a Vertex Map tag named "rd" on the deformed cache,
    # readable by Redshift's Vertex Attribute node (attribute = "rd") for the white-on-black look.
    g.add("sw", "set", accessortype=WEIGHT, accessorname=maxon.String("rd"),
          arraymode=maxon.Bool(False), newdataset=maxon.Bool(False))
    g.ws([("sp.geometryout", "sw.geometryin"), ("gp.topology", "sw.topology"),
          ("rdsoft.out", "sw.iteration"), ("sw.geometryout", "ROOT.geometryout")])

    # --- AM parameters
    F, I = maxon.Float64, maxon.Int64
    g.expose("feed", "Feed", F(0.0545), ["mem.feed"])
    g.expose("kill", "Kill", F(0.062), ["mem.kill"])
    g.expose("da", "Diffusion A", F(1.0), ["mem.da"])
    g.expose("db", "Diffusion B", F(0.5), ["mem.db"])
    g.expose("dt", "Time Step", F(1.0), ["mem.dt"])
    g.expose("steps", "Speed (steps per frame)", I(30), ["mem.steps"])
    g.expose("height", "Height", F(5.0), ["hgt.in2"])
    g.expose("seedr", "Seed Radius", F(0.0), ["cmp0.in2"])
    g.expose("seedthr", "Seed Threshold (higher = fewer)", F(0.75), ["scmp.in2"])
    g.expose("seedscale", "Seed Noise Scale", F(60.0), ["snz.scale"])
    g.expose("seed", "Seed", I(123), ["snz.seed"])
    g.expose("subdiv", "Subdivide (density)", I(0), [f"subd.{SUBD_SUB}"])

    d.SetDirty(c4d.DIRTYFLAGS_ALL)
    c4d.EventAdd()
    return d


def build_rd_lookdev(doc, host, frame_target=None):
    """Redshift white-on-black look: OpenPBR + coat, base colour from the `rd` vertex map, 2 RS area lights,
    RS camera aimed at the host. Assumes a Redshift document (the 2026 default). Returns the material."""
    RS = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")
    P = "com.redshift3d.redshift4c4d.nodes.core."
    mat = c4d.BaseMaterial(c4d.Mmaterial)
    mat.SetName("RD_Worms_BW")
    doc.InsertMaterial(mat)
    nm = mat.GetNodeMaterialReference()
    g = nm.CreateDefaultGraph(RS) if not nm.HasSpace(RS) else nm.GetGraph(RS)
    root = g.GetViewRoot()
    with g.BeginTransaction() as tx:
        surf = None
        for c in root.GetChildren():
            if "material" in str(c.GetId()) and "output" not in str(c.GetId()):
                surf = c
        va = g.AddChild(maxon.Id("rd_attr"), maxon.Id(P + "vertexattributelookup"))
        va.GetInputs().FindChild(maxon.InternedId(P + "vertexattributelookup.attribute")).SetPortValue(maxon.String("rd"))
        va.GetInputs().FindChild(maxon.InternedId(P + "vertexattributelookup.defaultcolor")).SetPortValue(maxon.ColorA64(0, 0, 0, 1))
        kind = str(surf.GetId()).split("@")[0]            # standardmaterial (CreateDefaultGraph) or openpbrmaterial
        va.GetOutputs().FindChild(maxon.InternedId(P + "vertexattributelookup.outcolor")).Connect(
            surf.GetInputs().FindChild(maxon.InternedId(P + kind + ".base_color")))
        for port, val in (("coat_weight", 0.8), ("coat_roughness", 0.04), ("refl_roughness", 0.28), ("specular_roughness", 0.28)):
            try:   # port names differ between RS Standard and OpenPBR; missing ones are skipped
                surf.GetInputs().FindChild(maxon.InternedId(P + kind + "." + port)).SetPortValue(maxon.Float64(val))
            except Exception:
                pass
        tx.Commit()
    tag = host.GetTag(c4d.Ttexture) or host.MakeTag(c4d.Ttexture)
    tag[c4d.TEXTURETAG_MATERIAL] = mat
    tag[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_UVW
    center = host.GetMg().off
    EV = c4d.DescID(c4d.DescLevel(11022, 19, 1036751))
    SX = c4d.DescID(c4d.DescLevel(11016, 19, 1036751)); SY = c4d.DescID(c4d.DescLevel(11017, 19, 1036751))
    def aimed(tid, name, off):
        o = c4d.BaseObject(tid); o.SetName(name); doc.InsertObject(o); o.SetAbsPos(center + off)
        t = c4d.BaseTag(c4d.Ttargetexpression); t[c4d.TARGETEXPRESSIONTAG_LINK] = host; o.InsertTag(t)
        return o
    for name, off, ev, sz in (("RD_Key", c4d.Vector(300, 550, -500), 9.0, 300.0), ("RD_Rim", c4d.Vector(-400, 250, 600), 8.0, 400.0)):
        L = aimed(1036751, name, off); L[EV] = ev; L[SX] = sz; L[SY] = sz
    cam = aimed(1057516, "RD_Cam", c4d.Vector(330, 180, -520))
    doc.GetActiveBaseDraw().SetSceneCamera(cam)
    c4d.EventAdd()
    return mat


def step_sim(doc, host, budget=45.0, until=None):
    """Advance a Memory sim sequentially from the current frame, inside a wall-clock budget (MCP calls cap at 60 s).
    Hides the host in the editor while stepping: the viewport drawing the cache mid-rebuild crashed C4D (gotcha #116)."""
    import time
    fps = doc.GetFps()
    vis = host[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR]
    host[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = 1
    t, f = time.time(), doc.GetTime().GetFrame(fps)
    try:
        while time.time() - t < budget and (until is None or f < until):
            f += 1
            doc.SetTime(c4d.BaseTime(f, fps))
            doc.ExecutePasses(None, False, True, True, c4d.BUILDFLAGS_NONE)
    finally:
        host[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = vis
    return f


if __name__ == "__main__":
    op = doc.GetActiveObject()  # noqa: F821  (doc is injected by C4D)
    if op:
        build_rd_growth(doc, op)  # noqa: F821
