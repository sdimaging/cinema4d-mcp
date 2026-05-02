# Match Size REBUILD PROOF — community-grade reference

**Date:** 2026-05-01
**Status:** ALGORITHM CAPTURED (semantic intent achieved). Auto-bbox-read via `bb`+`arithmetic` chain has a separate debug path documented below.

---

## What this proves

A custom Scene Nodes Match-Size-style deformer can be built from scratch in **~50 lines of Python** using the maxon graph API, and produces the correct **per-axis normalization** behavior — given a target envelope size, deform any parent geometry so its bbox exactly fits the envelope.

Demonstrated with 3 sources of different native sizes, all targeting a single 300×100×200 envelope:
- Sphere (r=50, bbox 100×100×100) → ratio (3, 1, 2) → flying saucer ellipsoid
- Cone (bbox 160×200×160) → ratio (1.875, 0.500, 1.250) → flat-wide cone
- Cylinder (bbox 120×300×120) → ratio (2.500, 0.333, 1.667) → flat-tall cylinder

Visual: [`frames/rebuild_15_NORMALIZATION_PROOF_3_inputs.png`](frames/rebuild_15_NORMALIZATION_PROOF_3_inputs.png)

---

## The minimum viable rebuild (working code)

```python
import c4d, maxon

def build_match_size(parent_obj, target_size, source_size):
    """
    parent_obj: any C4D BaseObject (mesh-producing) to deform
    target_size: c4d.Vector — the envelope all parents normalize to
    source_size: c4d.Vector — parent's native bbox size (TODO: auto-read via bb)
    """
    # 1) Add the SN Deformer host as a child of the parent
    sn = c4d.BaseObject(180420400)
    sn.SetName(f"MS_{parent_obj.GetName()}")
    sn.InsertUnder(parent_obj)

    # 2) Open the graph
    nimbus = sn.GetNimbusRef("net.maxon.neutron.nodespace")
    nspace = maxon.Id("net.maxon.neutron.nodespace")
    graph = nimbus.GetGraph(nspace)
    root = graph.GetRoot()

    # 3) Add nodes (transactions REQUIRED for all graph mutations)
    with graph.BeginTransaction() as txn:
        compose = graph.AddChild(maxon.Id("matrix"), maxon.Id("net.maxon.node.composematrix"))
        xform   = graph.AddChild(maxon.Id("xform"),  maxon.Id("net.maxon.neutron.geometry.transform_element"))
        txn.Commit()

    # Helper to find ports by name
    def fp(node, kind, name):
        h = node.GetInputs() if kind == "in" else node.GetOutputs()
        return next(p for p in h.GetChildren() if str(p.GetId()) == name)

    nodes = {str(c.GetId()): c for c in root.GetChildren()}
    root_in, root_out = nodes["<"], nodes[">"]

    # 4) Wire output FIRST (this lazily synthesizes root<.geometryin)
    with graph.BeginTransaction() as txn:
        fp(xform, "out", "geometryout").Connect(
            next(p for p in root_out.GetChildren() if str(p.GetId()) == "geometryout"))
        txn.Commit()

    # 5) Wire root.geometryin → xform.geometryin
    with graph.BeginTransaction() as txn:
        next(p for p in root_in.GetChildren() if str(p.GetId()) == "geometryin").Connect(
            fp(xform, "in", "geometryin"))
        txn.Commit()

    # 6) Wire compose.out → xform.transformin
    with graph.BeginTransaction() as txn:
        fp(compose, "out", "out").Connect(fp(xform, "in", "transformin"))
        txn.Commit()

    # 7) Set compose.scale = per-axis ratio (this IS the normalization)
    rx = target_size.x / source_size.x
    ry = target_size.y / source_size.y
    rz = target_size.z / source_size.z
    with graph.BeginTransaction() as txn:
        fp(compose, "in", "scale").SetDefaultValue(maxon.Vector(rx, ry, rz))
        fp(compose, "in", "translation").SetDefaultValue(maxon.Vector(0.0, 0.0, 0.0))
        fp(compose, "in", "rotation").SetDefaultValue(maxon.Vector(0.0, 0.0, 0.0))
        txn.Commit()

    # 8) CRITICAL: SetDirty + ForceRedraw + ExecutePasses to invalidate cache
    sn.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE
                | c4d.DIRTYFLAGS_DESCRIPTION | c4d.DIRTYFLAGS_MATRIX)
    parent_obj.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_MATRIX)
    c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
    c4d.documents.GetActiveDocument().ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)
    return sn
```

Usage:

```python
TARGET = c4d.Vector(300, 100, 200)

# Each call gives the parent its own MS deformer that normalizes to TARGET
build_match_size(my_sphere,   TARGET, c4d.Vector(100, 100, 100))  # native sphere bbox
build_match_size(my_cone,     TARGET, c4d.Vector(160, 200, 160))  # native cone bbox
build_match_size(my_cylinder, TARGET, c4d.Vector(120, 300, 120))  # native cylinder bbox
# All three now normalize to 300x100x200
```

---

## The 3 breakthroughs that made this work

### #1 SetDirty is required (not just EventAdd)

The single biggest blocker. Without these flags, the deformer's graph evaluates correctly but the parent's polygon cache doesn't refresh — viewport shows native un-deformed geometry. **Always include all 4 dirty flags + ForceRedraw + ExecutePasses.**

```python
sn.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE
            | c4d.DIRTYFLAGS_DESCRIPTION | c4d.DIRTYFLAGS_MATRIX)
parent.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_MATRIX)
c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)
```

### #2 `GetPortValue()` lies — trust the visual

`port.GetPortValue()` returns the LAST-SET DEFAULT, not the runtime-evaluated value. Throughout debugging, `arith.out` reported `(4,4,4)` even when the math should have produced different results. **Don't validate graph correctness via GetPortValue. Validate via screenshot.**

### #3 `compose.scale` IS the normalization input

Maxon's `net.maxon.node.composematrix` accepts `scale` as a vec3 directly. Set per-axis ratios via `SetDefaultValue(maxon.Vector(rx, ry, rz))` inside a transaction. No need to mess with basis vectors, rotation matrices, or rotationorder enums. Identity defaults are fine for translation/rotation when they're not connected to anything.

---

## What's still open (auto-bbox-read via bb + arithmetic)

The proof above HARDCODES the parent's source size (e.g. `Vector(100,100,100)` for the sphere). the reference actual Match Size reads the source bbox automatically via the `bb` node and computes the ratio via `arithmetic`. My attempt at this chain hit issues:

- `arith.out` produced `(4,4,4)` regardless of input vectors (cache stale or scalar-cast)
- Connecting `bb.geometryin` to `root<.geometryin` (in addition to `xform.geometryin`) made the deformer collapse the parent to a flat disc

**Hypothesized causes (next-session debug targets):**
1. The `bb.bbox` output may be a composite AABB struct, not a vec3 — feeding it into `arith.in2` may downcast to scalar. Try `arith(subtract, bb.max, bb.min)` to get the size as a vec3 first, then divide.
2. Dual-consumer of `root<.geometryin` (both bb and xform) may break the deformer's evaluation ordering. Try chaining: `bb` reads from `xform`'s upstream geometry via a separate path.
3. The `arithmetic` node may need its `datatype` port explicitly set to `vector` (the port appears in fresh nodes but disappears after first connection — possible asset-versioning quirk).

---

## Side-by-side: my MVP vs the reference actual build

| Feature | MVP rebuild (working) | the reference Match Size (203 nodes) |
|---------|----------------------|----------------------------------|
| Per-axis normalization | ✅ via `compose.scale = target/source` | ✅ same fundamental math |
| Auto bbox read | ❌ source size hardcoded in Python | ✅ via `bb` node inside graph |
| Target object link | ❌ target hardcoded | ✅ via `legacyobjectaccess` linked to AM-exposed object |
| Mode dispatch | ❌ none | ✅ Local/Global, anchor modes via 16 if + 13 switch |
| Per-axis enables | ❌ none | ✅ X/Y/Z toggles via floatingio |
| Selection scoping | ❌ none | ✅ via 7 `selectionstringparser` |
| Padding/offset | ❌ none | ✅ via additional arithmetic |
| AM exposure | ❌ none | ✅ via 15 `floatingio` ports → ~190 descids |
| Debug visualizer | ❌ none | ✅ via internal `cube` (wireframe target bbox) |
| Total nodes | 2 (compose + xform) | 203 |

**Verdict:** the MVP is the algorithmic CORE done right (10× simpler with same fundamental result). the reference build adds production-grade UX (artist controls, mode flexibility, selection scoping, debug viz). For our recipe library: ship the MVP first, layer the UX on top per use case.

---

## Community MCP improvements this session unlocked

These should land in the public cinema4d-mcp before next session pushes more recipes:

1. **`scene_nodes_add_node` should accept canonical asset IDs**, not just English UI labels (current behavior fails for `transform_element` and many others)
2. **`scene_nodes_connect_ports` should support `<` / `>` root gateway addressing** (currently fails with "no target to copy for graphmodel")
3. **Helper for `SetDirty` + `EventAdd(FORCEREDRAW)` + `ExecutePasses`** as a single `refresh_deformer(host, parent)` call
4. **Documentation warning that `GetPortValue()` returns design-time defaults, not runtime values** — add to gotchas
5. **Automatic cleanup for `scene_nodes_describe_node_template` leaks** (currently flags `cleanup_succeeded: false, leaked: true`)
6. **Asset-ID lookup helper** that searches all known namespaces (`net.maxon.neutron.*`, `net.maxon.node.*`, `net.maxon.nbo.*`) given a basename
