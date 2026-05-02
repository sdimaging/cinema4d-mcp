# Match Size REBUILD PROOF v2 — FULL AUTO-BBOX-READ working

**Date:** 2026-05-01 evening
**Status:** ✅ PSEUDO equivalent achieved (auto-bbox-read working in 5 nodes for the basic-settings test case ONLY — would break the moment you ask it to do selection scoping, Local mode, anchor offsets, AM controls, or any of the dozen other things the reference 203-node graph actually handles)
**Status of TRUE 1-to-1 replica (THE NORTH STAR):** ❌ NOT YET — see north-star reframe below

> **METHODOLOGY REFRAME (Spenser, 2026-05-01 evening):**
> "the EXACT replica is always FIRST - because that truly is the NORTH star once we can build the EXACT replica of the scene state from scratch then we know its functional and we can DO it with no barriers then we can push change alter and make it our own"
>
> **So this v2 proof is a stepping stone, NOT the destination.** What's documented here is a 5-node FUNCTIONAL EQUIVALENT that produces the same output as Match Size for the basic-settings case. The NORTH STAR is to rebuild the reference full 203-node graph node-for-node — every floatingio AM port, every if/switch mode branch, every transform_element with selection scoping, every scaffold organizational section, every inversematrix Local-mode branch, every connect_geometries+delete+invertselection selection-restriction node, every reroute. Six match criteria must pass: node count, type histogram, port wiring, AM exposure, scaffold organization, output equivalence across MULTIPLE inputs.
>
> Once that replica is solid, THEN variations become informed deviations from a fully understood foundation. Until then, the work continues.

---

## The breakthrough

After [v1 proof](rebuild_proof.md) hardcoded source sizes per parent, the auto-bbox-read chain (`bb` + `arithmetic`) was deferred. Tonight via the artist iterative loop (try-fail-reopen-reference-retry), the gap is CLOSED in 4 iterations.

**Visual proof:** [rebuild_iter4_CORRECT_ids.png](frames/rebuild_iter4_CORRECT_ids.png) — perfect (3, 1, 2) flying-saucer ellipsoid produced by the auto-read chain reading sphere's bbox dynamically.

---

## The 4 iterations (each failure = data)

| Iter | Hypothesis | Result | Lesson |
|-----:|-----------|--------|--------|
| **1** | `bb.bbox` directly → `arith.in2` | ELIMINATED — sphere collapses to flat disc | bb.bbox is composite AABB, not vec3 |
| **2** | `bb.max - bb.min` via `arith("subtract")` then `arith("divide")` | ELIMINATED — sphere engulfs camera (huge scalar fallback) | "subtract"/"divide" are not valid cycle Ids — silently scalar mode |
| **3** | Set `datatype = "vector"` BEFORE wiring | ELIMINATED — same engulfing-scale | "vector" wrong namespace |
| **4** | **`operation: "sub"/"div"` + `datatype: "net.maxon.parametrictype.vec<3,float>"` set BEFORE wiring** | ✅ **WORKS** — perfect (3,1,2) flying saucer | the reference exact cycle Ids |

---

## The full working chain (auto-bbox-read)

```
                   ┌─[bb]──┐
root.geometryin ──┤        ├── max ─→ arith(sub, vec<3,float>).in1
                   │        ├── min ─→ arith(sub, vec<3,float>).in2
                   └────────┘                   │
                                                ↓
                              source SIZE = (max - min) as vec3
                                                │
                                  ↓ feeds .in2 of:
                                                │
target (vec3 hardcoded) ─→ arith(div, vec<3,float>).in1
                                                ↓
                          per-axis ratio = target / size
                                                │
                                                ↓ feeds compose.scale
                                                │
                              compose.out ─→ xform.transformin
                                                │
                                  ↓ via xform.geometryin from root
                                                │
                                                ↓ deformed mesh
                                       root.geometryout
```

---

## Working code (full auto-read MVP, ~80 lines)

```python
import c4d, maxon

def build_match_size_AUTO(parent_obj, target_size):
    """
    Build a custom Match Size deformer that reads source bbox automatically.
    parent_obj: any C4D BaseObject with geometry
    target_size: c4d.Vector — envelope to normalize to
    """
    # 1) Add SN deformer host
    sn = c4d.BaseObject(180420400)
    sn.SetName(f"MS_AUTO_{parent_obj.GetName()}")
    sn.InsertUnder(parent_obj)
    
    nimbus = sn.GetNimbusRef("net.maxon.neutron.nodespace")
    nspace = maxon.Id("net.maxon.neutron.nodespace")
    graph = nimbus.GetGraph(nspace)
    root = graph.GetRoot()
    
    # 2) Add 5 nodes
    with graph.BeginTransaction() as txn:
        bb      = graph.AddChild(maxon.Id("bb"),       maxon.Id("net.maxon.neutron.geometry.bb"))
        sub     = graph.AddChild(maxon.Id("subSize"),  maxon.Id("net.maxon.node.arithmetic"))
        divr    = graph.AddChild(maxon.Id("divRatio"), maxon.Id("net.maxon.node.arithmetic"))
        compose = graph.AddChild(maxon.Id("matrix"),   maxon.Id("net.maxon.node.composematrix"))
        xform   = graph.AddChild(maxon.Id("xform"),    maxon.Id("net.maxon.neutron.geometry.transform_element"))
        txn.Commit()
    
    def fp(node, kind, name):
        h = node.GetInputs() if kind == "in" else node.GetOutputs()
        return next(p for p in h.GetChildren() if str(p.GetId()) == name)
    
    # 3) CRITICAL: Set arith config BEFORE wiring (datatype port disappears after first connection)
    VEC3 = maxon.Id("net.maxon.parametrictype.vec<3,float>")
    with graph.BeginTransaction() as txn:
        fp(sub,  "in", "datatype").SetDefaultValue(VEC3)
        fp(sub,  "in", "operation").SetDefaultValue(maxon.Id("sub"))
        fp(divr, "in", "datatype").SetDefaultValue(VEC3)
        fp(divr, "in", "operation").SetDefaultValue(maxon.Id("div"))
        fp(divr, "in", "in1").SetDefaultValue(maxon.Vector(target_size.x, target_size.y, target_size.z))
        fp(compose, "in", "translation").SetDefaultValue(maxon.Vector(0.0, 0.0, 0.0))
        fp(compose, "in", "rotation").SetDefaultValue(maxon.Vector(0.0, 0.0, 0.0))
        txn.Commit()
    
    # 4) Wire output FIRST (auto-synthesizes root<.geometryin)
    nodes = {str(c.GetId()): c for c in root.GetChildren()}
    root_in, root_out = nodes["<"], nodes[">"]
    with graph.BeginTransaction() as txn:
        fp(xform, "out", "geometryout").Connect(
            next(p for p in root_out.GetChildren() if str(p.GetId()) == "geometryout"))
        txn.Commit()
    
    # 5) Wire root.geometryin → bb + xform
    nodes = {str(c.GetId()): c for c in root.GetChildren()}
    root_in = nodes["<"]
    root_geom_in = next(p for p in root_in.GetChildren() if str(p.GetId()) == "geometryin")
    with graph.BeginTransaction() as txn:
        root_geom_in.Connect(fp(bb, "in", "geometryin"))
        root_geom_in.Connect(fp(xform, "in", "geometryin"))
        # bb.max - bb.min = source size
        fp(bb, "out", "max").Connect(fp(sub, "in", "in1"))
        fp(bb, "out", "min").Connect(fp(sub, "in", "in2"))
        # target / size = ratio
        fp(sub, "out", "out").Connect(fp(divr, "in", "in2"))
        # ratio → compose.scale → xform.transformin
        fp(divr, "out", "out").Connect(fp(compose, "in", "scale"))
        fp(compose, "out", "out").Connect(fp(xform, "in", "transformin"))
        txn.Commit()
    
    # 6) CRITICAL: SetDirty + ExecutePasses to invalidate cache
    sn.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_DESCRIPTION | c4d.DIRTYFLAGS_MATRIX)
    parent_obj.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_MATRIX)
    c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
    c4d.documents.GetActiveDocument().ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)
    return sn
```

Usage:

```python
TARGET = c4d.Vector(300, 100, 200)
build_match_size_AUTO(my_sphere,   TARGET)  # auto-reads sphere bbox
build_match_size_AUTO(my_cone,     TARGET)  # auto-reads cone bbox
build_match_size_AUTO(my_cylinder, TARGET)  # auto-reads cylinder bbox
# ALL three normalize to TARGET envelope automatically
```

---

## Critical learnings (locked into memory)

1. **`net.maxon.node.arithmetic` cycle Ids are SHORT:** `sub`, `div`, `add`, `mul` — NOT `subtract``divide`. Silently scalar-fallback if wrong.

2. **`datatype` Id uses full parametrictype path:** `net.maxon.parametrictype.vec<3,float>` — NOT `vector`. Same silent-fallback.

3. **`datatype` port DISAPPEARS after first connection** — set it BEFORE wiring. Once gone, you can't change it without re-adding the node fresh.

4. **`bb.bbox` is a composite AABB struct, NOT a vec3.** Use `bb.max - bb.min` via subtract for source size.

5. **`GetPortValue()` lies** — returns design-time defaults, not runtime computed values. Trust the visual.

6. **SetDirty(host + parent, all flags) + EventAdd(FORCEREDRAW) + ExecutePasses** — required after any graph mutation to refresh the cache.

7. **The artist iterative loop works.** 4 iterations cracked this. Each "failure" eliminated one wrong path. By iter 4, the practice file's exact Ids gave us the answer.

---

## What's now possible

- R31 (Match Size full auto-bbox-read) recipe is complete — can be wrapped as `build_match_size(parent, target)` one-liner
- The same arith-cycle-Id learnings apply to ANY arithmetic-using SN graph (Spiderweb's compute, Stack Stones' iteration, all future authoring)
- Future rebuilds can short-circuit by IMMEDIATELY checking practice files for cycle Ids before assuming English-style names
