# SN_RECIPE_centered_uv_toggle

**Version:** 1.0
**Status:** Verified — toggle ON/OFF produces expected mp shifts (off → BUV's mp; on → origin)
**Eligible for Assemble Mode:** YES — extends `SN_RECIPE_buv_pathb_uv_position`
**Depends on:** `SN_RECIPE_buv_pathb_uv_position` v1.0

## Purpose

Add a Centered toggle on top of a UV-position deformer (e.g., BUV PathB). When ON, the UV preview centers on world origin instead of being offset to +Y/+X by the UV [0,1] range × scale.

## Provenance

- **Discovered/proven:** 2026-05-04
- **Proof doc:** `cinema4d-mcp/docs/scene_nodes_advanced_studies/druckli_capsule_audit/03_uv_polygon_info/extension/CANONICAL_SN_DEFORMER_PATTERN.md` ("T2 Centered toggle" section)
- **Verification screenshots:** `_t2_centered_on/multiview_*.png`
- **Last verified:** 2026-05-04 (Cinema 4D 2026.2.0)

## Architecture (insertion into BUV PathB)

```
... existing BUV PathB chain:
comp1.result → scale1.in1
                ↓
                scale1.out → set1.iteration

NEW: insert arith_center BETWEEN compose and scale:

comp1.result → arith_center.in1 [op=sub, datatype=vec3]
                                in2 = (toggle_offset)
               arith_center.out → scale1.in1
                                  ↓
                                  (rest of chain unchanged)
```

**Adds 1 node + 2 wires (net +1 wire after disconnecting old comp1→scale1 wire).**

## Required asset IDs

```python
A_ARITH = maxon.Id("net.maxon.node.arithmetic")
VEC3_TYPE = maxon.Id("net.maxon.parametrictype.vec<3,float>")
```

## Port configurations

| Node | Port | Value | Notes |
|---|---|---|---|
| arith_center | datatype | VEC3_TYPE | **REQUIRED before wiring** (datatype port hides after first connection) |
| arith_center | operation | maxon.Id("sub") | subtract — short ID, not "subtract" |
| arith_center | in2 | Vector(0.5, 0.5, 0.0) when ON, Vector(0, 0, 0) when OFF | the toggle value |

## Wire list (mutation on existing BUV PathB)

```python
# Before:
comp1.result → scale1.in1   (existing)

# After:
comp1.result → arith_center.in1
arith_center.out → scale1.in1

# Operations:
1. Disconnect comp1.result → scale1.in1   (use WIRE_MODE.REMOVE)
2. Connect comp1.result → arith_center.in1
3. Connect arith_center.out → scale1.in1
```

## IO contract

**Input:** any UV-position deformer chain that produces a per-iter Vec3 in pre-scale UV space (where UV is in [0,1] range and unscaled).

**Output:** same per-iter Vec3 stream, optionally offset by `-arith_center.in2`. When `in2 = (0.5, 0.5, 0)`: shifts UV [0,1] → [-0.5, +0.5], which after scale=50 → [-25, +25] (centered).

## Safe-to-edit parameters

- **`arith_center.in2`** (Vector) — the centering offset. Set to `(0.5, 0.5, 0)` for full centering, `(0, 0, 0)` for off, intermediate values for partial offset.

## NOT safe to edit

- `arith_center.datatype` — must be VEC3_TYPE and set BEFORE wiring
- `arith_center.operation` — must be "sub" (Id)

## Toggle implementation (Python)

```python
def set_centered(deformer, on=True):
    """Toggle the Centered offset on a BUV PathB+Centered deformer."""
    NODESPACE = maxon.Id("net.maxon.neutron.nodespace")
    graph = deformer.GetNimbusRef(NODESPACE).GetGraph()
    root = graph.GetRoot()

    arith = None
    for c in root.GetChildren():
        try:
            if str(c.GetId()) == "arith_center":
                arith = c; break
        except: pass
    if arith is None:
        raise RuntimeError("arith_center node not found — recipe not applied")

    offset = maxon.Vector(0.5, 0.5, 0.0) if on else maxon.Vector(0.0, 0.0, 0.0)
    in2 = arith.GetInputs().FindChild(maxon.InternedId("in2"))
    with graph.BeginTransaction() as tx:
        in2.SetPortValue(offset)
        tx.Commit()

    deformer.SetDirty(c4d.DIRTYFLAGS_DATA)
    parent = deformer.GetUp()
    if parent: parent.SetDirty(c4d.DIRTYFLAGS_DATA)
    c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
```

## Known failure modes

1. **Output goes to 0 verts after applying** — disconnection didn't happen; `scale1.in1` now has 2 sources (comp1.result AND arith_center.out). Use `WIRE_MODE.REMOVE` first. See `SN_RECIPE_wire_remove_swap_pattern.md`.
2. **Toggle has no visual effect** — `arith_center.datatype` not set to VEC3, so it's running in scalar mode. Result still passes through but Vec3 components aren't subtracted.

## MCP authoring script (applies recipe to existing BUV PathB)

```python
import c4d, maxon

def apply_centered_toggle(buv_pathb_deformer, initial_on=True):
    """Add Centered toggle to an existing BUV PathB deformer.
    Returns (deformer, arith_center_node_id) for later toggle calls."""
    NODESPACE = maxon.Id("net.maxon.neutron.nodespace")
    A_ARITH = maxon.Id("net.maxon.node.arithmetic")
    VEC3_TYPE = maxon.Id("net.maxon.parametrictype.vec<3,float>")

    graph = buv_pathb_deformer.GetNimbusRef(NODESPACE).GetGraph()
    root = graph.GetRoot()
    def _node(label):
        for c in root.GetChildren():
            try:
                if str(c.GetId()) == label: return c
            except: pass
        return None
    def _in(node, name):
        return node.GetInputs().FindChild(maxon.InternedId(name))
    def _out(node, name):
        return node.GetOutputs().FindChild(maxon.InternedId(name))

    n_comp  = _node("comp1")
    n_scale = _node("scale1")
    if n_comp is None or n_scale is None:
        raise RuntimeError("Source deformer is not a BUV PathB recipe (missing comp1/scale1)")

    # 1. Add arith_center
    with graph.BeginTransaction() as tx:
        graph.AddChild(maxon.Id("arith_center"), A_ARITH)
        tx.Commit()

    # 2. Configure (BEFORE wiring)
    graph = buv_pathb_deformer.GetNimbusRef(NODESPACE).GetGraph()
    root = graph.GetRoot()
    n_arith = _node("arith_center")
    n_comp  = _node("comp1")
    n_scale = _node("scale1")
    initial_offset = maxon.Vector(0.5, 0.5, 0.0) if initial_on else maxon.Vector(0.0, 0.0, 0.0)
    with graph.BeginTransaction() as tx:
        _in(n_arith, "datatype").SetPortValue(VEC3_TYPE)
        _in(n_arith, "operation").SetPortValue(maxon.Id("sub"))
        _in(n_arith, "in2").SetPortValue(initial_offset)
        tx.Commit()

    # 3. Rewire: REMOVE old, ADD new (THE crucial step — Connect default ADDS, doesn't replace)
    with graph.BeginTransaction() as tx:
        _out(n_comp, "result").Connect(_in(n_scale, "in1"), maxon.WIRE_MODE.REMOVE)
        _out(n_comp, "result").Connect(_in(n_arith, "in1"))
        _out(n_arith, "out").Connect(_in(n_scale, "in1"))
        tx.Commit()

    # 4. Refresh
    buv_pathb_deformer.SetDirty(c4d.DIRTYFLAGS_DATA)
    parent = buv_pathb_deformer.GetUp()
    if parent: parent.SetDirty(c4d.DIRTYFLAGS_DATA)
    c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
    doc = c4d.documents.GetActiveDocument()
    doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)

    return buv_pathb_deformer, "arith_center"
```

## Minimal verification test

```python
def verify_centered_toggle(deformer):
    """Toggle Centered ON and OFF, verify mp shifts as expected."""
    set_centered(deformer, on=False)
    parent = deformer.GetUp()
    poly_off = deepest_polygon(parent)
    mp_off = poly_off.GetMp() if poly_off else None

    set_centered(deformer, on=True)
    poly_on = deepest_polygon(parent)
    mp_on = poly_on.GetMp() if poly_on else None

    if mp_off is None or mp_on is None:
        return False, "no polygon in cache"

    # OFF: mp should be ~ (scale*0.5, scale*0.5, 0) ≈ (25, 25, 0) for default scale=50
    # ON:  mp should be ~ (0, 0, 0)
    off_ok = abs(mp_off.x) > 10 and abs(mp_off.y) > 10
    on_ok  = abs(mp_on.x) < 1 and abs(mp_on.y) < 1
    return off_ok and on_ok, {"mp_off": mp_off, "mp_on": mp_on, "off_ok": off_ok, "on_ok": on_ok}
```

## Anti-patterns

- ❌ Don't connect `arith_center.out → scale1.in1` without first removing the old `comp1.result → scale1.in1` wire — produces 2-source input port = 0 vert output
- ❌ Don't set `arith_center.datatype` AFTER any wire is connected to its in1/in2 ports — datatype hides after first wire and silently falls back to scalar mode
