# SN_RECIPE_buv_pathb_uv_position

**Version:** 1.0
**Status:** Verified visually + numerically (4664 verts, rad exact, mp Δ=0.01 vs DRuckli BUV reference)
**Eligible for Assemble Mode:** YES

## Purpose

Take any input mesh that has a UV attribute and produce a flat 2D mesh in the XY plane representing the UV unwrap (one vertex per source poly-corner). Equivalent to DRuckli's "Build UV Preview" deformer — but built from outer-level primitives, no `uvtomesh` capsule used.

## Provenance

- **Discovered/proven:** 2026-05-03
- **Proof doc:** `cinema4d-mcp/docs/scene_nodes_advanced_studies/druckli_capsule_audit/03_uv_polygon_info/extension/CANONICAL_SN_DEFORMER_PATTERN.md` ("Path (b)" section)
- **Source scene:** `UV-Polygon-Info_Example_01.c4d` (Maxon-shipped, used as read-only forensic reference)
- **Reference deformer:** "Build UV Preview" inside Generic Head Bust UV Preview Instance
- **Verification screenshots:** `_t1_pathb/multiview_*.png`
- **Last verified:** 2026-05-04 (Cinema 4D 2026.2.0)

## Architecture

```
ROOT.geometryin
  ├─ get1 (get_property)        [accessortype=uv, accessorname="UVW"]
  │     ├→ array → iter1 (containeriteration)
  │     │             └→ out → split1 (splitvectorcomponents)
  │     │                       ├→ x → comp1 (composevector3) .x
  │     │                       └→ y → inv1 (invert) .in
  │     │                                └→ out → comp1 .y
  │     │                                          └→ result → scale1 (scale) .in1
  │     │                                                      [datatype=vec3, in2=50]
  │     │                                                      └→ out → set1.iteration
  │     └→ topology → set1.topology
  │
  └→ set1.geometryin (set_property)
        [accessortype=data3d, accessorname="", arraymode=False, newdataset=True]
        └→ geometryout → ROOT.geometryout
```

**7 functional nodes + 11 wires + 9 port configs.** No capsule reuse.

## Required asset IDs

```python
A_GET    = maxon.Id("net.maxon.neutron.geometry.get_property")
A_SET    = maxon.Id("net.maxon.neutron.geometry.set_property")
A_ITER   = maxon.Id("net.maxon.node.containeriteration")
A_SPLIT  = maxon.Id("net.maxon.pattern.node.conversion.splitvectorcomponents")
A_COMP   = maxon.Id("net.maxon.pattern.node.conversion.composevector3")
A_SCALE  = maxon.Id("net.maxon.node.scale")
A_INV    = maxon.Id("net.maxon.node.invert")

UV_TYPE    = maxon.Id("net.maxon.geometryabstraction.accessortypes.attributes.uv")
DATA3D     = maxon.Id("net.maxon.geometryabstraction.accessortypes.attributes.data3d")
VEC3_TYPE  = maxon.Id("net.maxon.parametrictype.vec<3,float>")
FLOAT_TYPE = maxon.Id("net.maxon.parametrictype.float")
```

## Port configurations

| Node | Port | Value | Notes |
|---|---|---|---|
| get1 | accessortype | UV_TYPE | required override (default = data3d) |
| get1 | accessorname | "UVW" (String) | required override (default = "pt") |
| iter1 | datatype | (skip) | auto-infers from `iter.in` wire — explicit set fails with CONTAINER_REF kind error |
| inv1 | datatype | FLOAT_TYPE | required (default = float anyway, set for clarity) |
| scale1 | datatype | VEC3_TYPE | **REQUIRED before wiring** (datatype port hides after first connection) |
| scale1 | in2 | Float64(50.0) | scale factor — safe to edit (UV→world scale) |
| set1 | accessortype | DATA3D | required override |
| set1 | accessorname | "" (empty String) | **CRITICAL** — empty = default Position attribute. "Position" or "pt" silently produces 0 verts. |
| set1 | arraymode | Bool(False) | required — exposes `.iteration` port |
| set1 | newdataset | Bool(True) | required — REBUILDS topology with one vert per source poly-corner |

## Wire list

```python
# 11 wires:
root.geoin → get1.geometry
root.geoin → set1.geometryin
get1.array → iter1.in
iter1.out → split1.vector
split1.x → comp1.x
split1.y → inv1.in
inv1.out → comp1.y
comp1.result → scale1.in1
scale1.out → set1.iteration
get1.topology → set1.topology
set1.geometryout → root.geoout
```

## IO contract

**Input:** geometry on the deformer's parent (read via `root.geometryin`). Must have a UV attribute named "UVW" (the C4D default UV tag name).

**Output:** flat 2D mesh on XY plane (Y-up convention), `geometryout` on root.
- vertex count: source_polys × 4 (one vert per poly-corner; source UV islands separate)
- bounding box: `rad ~ (24.82, 24.32, 0)` for [0,1] UV range × scale=50
- midpoint: `mp ~ (24.96, 25.18, 0)` for [0,1] UV, ~25 = (UV_mid * scale) due to V-flip via invert

## Safe-to-edit parameters

- **`scale1.in2`** (Float64) — scale factor. Default 50. Adjust to fit your scene scale.
- **`get1.accessorname`** (String) — name of UV tag to read. Default "UVW". Change if your mesh uses a non-default tag name.
- **`set1.accessorname`** (String) — leave empty unless you want to write to a non-default attribute.

## NOT safe to edit (depends on internal wiring)

- `set1.arraymode` — must be False for `.iteration` port to exist
- `set1.newdataset` — must be True for topology rebuild
- `iter1.datatype` — must NOT be explicitly set (errors on CONTAINER_REF kind)
- `scale1.datatype` — must be VEC3_TYPE and set BEFORE the first wire
- `comp1.z` — leave None (= 0 implicitly) for XY-plane output
- Any change to wire topology breaks the per-iteration signal

## Known failure modes

1. **0 verts in output** — almost always `accessorname` issue on get1 or set1. Check both are correct.
2. **Wrong plane (XZ instead of XY)** — `inv1` not wired between split.y and compose.y; OR compose.y wired to split.y directly.
3. **Mirrored output** — invert direction wrong, OR you accidentally got `scale.in1` connected via two sources.
4. **`unable to convert builtins.NativePyData to @net.maxon.datatype.internedid`** — using `maxon.Id` instead of `maxon.InternedId` on `FindChild` calls.
5. **Wires fail silently with `no target to copy for '<net.maxon.graph.interface.graphmodel>'`** — forgot `MSG_CREATE_IF_REQUIRED` after deformer creation; root ports never synthesized.

## MCP authoring script (regenerates from scratch)

```python
import c4d, maxon

NODESPACE = maxon.Id("net.maxon.neutron.nodespace")

def author_buv_pathb(host_obj, deformer_name="BUV PathB"):
    """Author a BUV PathB deformer as a child of host_obj. Returns the deformer."""
    doc = c4d.documents.GetActiveDocument()

    # 1. Create SN deformer + synthesize root ports
    deformer = c4d.BaseObject(180420400)  # Scene Nodes Deformer
    deformer.SetName(deformer_name)
    doc.InsertObject(deformer, parent=host_obj, pred=None)
    deformer.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)
    c4d.EventAdd()

    # 2. Asset + type IDs
    A_GET    = maxon.Id("net.maxon.neutron.geometry.get_property")
    A_SET    = maxon.Id("net.maxon.neutron.geometry.set_property")
    A_ITER   = maxon.Id("net.maxon.node.containeriteration")
    A_SPLIT  = maxon.Id("net.maxon.pattern.node.conversion.splitvectorcomponents")
    A_COMP   = maxon.Id("net.maxon.pattern.node.conversion.composevector3")
    A_SCALE  = maxon.Id("net.maxon.node.scale")
    A_INV    = maxon.Id("net.maxon.node.invert")
    UV_TYPE    = maxon.Id("net.maxon.geometryabstraction.accessortypes.attributes.uv")
    DATA3D     = maxon.Id("net.maxon.geometryabstraction.accessortypes.attributes.data3d")
    VEC3_TYPE  = maxon.Id("net.maxon.parametrictype.vec<3,float>")
    FLOAT_TYPE = maxon.Id("net.maxon.parametrictype.float")

    graph = deformer.GetNimbusRef(NODESPACE).GetGraph()

    def _in(node, name):
        return node.GetInputs().FindChild(maxon.InternedId(name))
    def _out(node, name):
        return node.GetOutputs().FindChild(maxon.InternedId(name))

    # 3. Add 7 nodes (separate transactions = safer than monolithic)
    nodes_spec = [
        ("get1",   A_GET),
        ("iter1",  A_ITER),
        ("split1", A_SPLIT),
        ("inv1",   A_INV),
        ("comp1",  A_COMP),
        ("scale1", A_SCALE),
        ("set1",   A_SET),
    ]
    for label, aid in nodes_spec:
        with graph.BeginTransaction() as tx:
            graph.AddChild(maxon.Id(label), aid)
            tx.Commit()

    # 4. Re-resolve nodes (fresh refs)
    graph = deformer.GetNimbusRef(NODESPACE).GetGraph()
    root = graph.GetRoot()
    def _node(label):
        for c in root.GetChildren():
            try:
                if str(c.GetId()) == label: return c
            except: pass
        return None
    n_get   = _node("get1")
    n_iter  = _node("iter1")
    n_split = _node("split1")
    n_inv   = _node("inv1")
    n_comp  = _node("comp1")
    n_scale = _node("scale1")
    n_set   = _node("set1")

    # 5. Configure ports — datatype BEFORE wiring (scale.datatype port hides after first wire)
    with graph.BeginTransaction() as tx:
        _in(n_get, "accessortype").SetPortValue(UV_TYPE)
        _in(n_get, "accessorname").SetPortValue(maxon.String("UVW"))
        _in(n_inv, "datatype").SetPortValue(FLOAT_TYPE)
        _in(n_scale, "datatype").SetPortValue(VEC3_TYPE)
        _in(n_scale, "in2").SetPortValue(maxon.Float64(50.0))
        _in(n_set, "accessortype").SetPortValue(DATA3D)
        _in(n_set, "accessorname").SetPortValue(maxon.String(""))
        _in(n_set, "arraymode").SetPortValue(maxon.Bool(False))
        _in(n_set, "newdataset").SetPortValue(maxon.Bool(True))
        # Note: do NOT set iter1.datatype — auto-infers
        tx.Commit()

    # 6. Wire (one wire per transaction makes failures findable)
    root_geoin   = root.GetInputs().FindChild(maxon.InternedId("geometryin"))
    root_geoout  = root.GetOutputs().FindChild(maxon.InternedId("geometryout"))
    wires = [
        (root_geoin,                       _in(n_get,   "geometry")),
        (root_geoin,                       _in(n_uvm := n_get, "geometry")) if False else (root_geoin, _in(n_get, "geometry")),  # placeholder removed below
    ]
    # Actual 11 wires:
    wires = [
        (root_geoin,                       _in(n_get,   "geometry")),
        (root_geoin,                       _in(n_set,   "geometryin")),
        (_out(n_get,   "array"),           _in(n_iter,  "in")),
        (_out(n_iter,  "out"),             _in(n_split, "vector")),
        (_out(n_split, "x"),               _in(n_comp,  "x")),
        (_out(n_split, "y"),               _in(n_inv,   "in")),
        (_out(n_inv,   "out"),             _in(n_comp,  "y")),
        (_out(n_comp,  "result"),          _in(n_scale, "in1")),
        (_out(n_scale, "out"),             _in(n_set,   "iteration")),
        (_out(n_get,   "topology"),        _in(n_set,   "topology")),
        (_out(n_set,   "geometryout"),     root_geoout),
    ]
    for src, dst in wires:
        with graph.BeginTransaction() as tx:
            src.Connect(dst)
            tx.Commit()

    # 7. Refresh
    deformer.SetDirty(c4d.DIRTYFLAGS_DATA)
    if host_obj:
        host_obj.SetDirty(c4d.DIRTYFLAGS_DATA)
    c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
    doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)

    return deformer
```

## Minimal verification test

```python
def verify_buv_pathb(deformer, source_poly_count, expected_uv_range_max=1.0):
    """Verify a BUV PathB deformer produces correct output."""
    doc = c4d.documents.GetActiveDocument()
    parent = deformer.GetUp()

    def deepest_polygon(op):
        if op is None: return None
        if op.GetType() == c4d.Opolygon: return op
        for getter in (op.GetDeformCache, op.GetCache):
            child = getter()
            if child:
                r = deepest_polygon(child)
                if r: return r
        return None

    poly = deepest_polygon(parent)
    if poly is None:
        return False, "no polygon in cache"
    pts = poly.GetAllPoints()
    rad = poly.GetRad()

    # Expected: 4 verts per source poly (corner-vertex split)
    expected_verts = source_poly_count * 4
    # Expected rad on flat plane: ~ scale × UV range × 0.5
    expected_rad_xy = 50.0 * expected_uv_range_max * 0.5  # ~25 for [0,1]

    checks = {
        "vert_count_correct": len(pts) == expected_verts,
        "z_is_zero": abs(rad.z) < 0.01,  # XY plane
        "x_in_range": abs(rad.x - expected_rad_xy) < expected_rad_xy * 0.5,  # within 50%
        "y_in_range": abs(rad.y - expected_rad_xy) < expected_rad_xy * 0.5,
    }
    passed = all(checks.values())
    return passed, checks
```

## Assemble Mode usage (decimate-and-rewire workflow)

1. Open `SN_Recipe_Library.c4d`
2. Find the `RECIPE_buv_pathb_uv_position_v1` Null
3. Copy the entire Null and its children into your working scene
4. **Decimate:** delete the demo source mesh inside the recipe Null (keep only the Scene Nodes Deformer)
5. **Reparent:** drag the Scene Nodes Deformer to be a child of YOUR mesh (the one with UV)
6. **Verify:** if your mesh's UV tag isn't named "UVW", set the deformer's `get1.accessorname` to your tag's name via Python:
   ```python
   ref = your_deformer.GetNimbusRef(maxon.Id("net.maxon.neutron.nodespace"))
   graph = ref.GetGraph()
   # find get1 and update accessorname
   ```
7. **Adjust scale:** set `scale1.in2` to fit your scene (default 50 = world cm per UV unit)
8. **Run verification test** above with your source mesh's poly count

## Anti-patterns (will fail)

- ❌ Don't set `iter1.datatype` explicitly — fails with CONTAINER_REF error
- ❌ Don't use `maxon.Id("portname")` for FindChild — must be `maxon.InternedId("portname")`
- ❌ Don't skip `MSG_CREATE_IF_REQUIRED` after creating the deformer — root ports won't synthesize
- ❌ Don't change `set1.accessorname` from "" without testing — "Position" or "pt" silently produces 0 verts
- ❌ Don't set `scale1.datatype` AFTER wiring — datatype port hides after first connection
