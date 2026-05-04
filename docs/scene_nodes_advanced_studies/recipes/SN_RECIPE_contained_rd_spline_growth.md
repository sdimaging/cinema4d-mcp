# SN_RECIPE_contained_rd_spline_growth

**Version:** 1.0 (in progress — outer chain documented + writable; Memory body deep-dive pending)
**Status:** Forensically verified visually (frame 0 single spline → frame 60 full RD maze). Rebuild PENDING.
**Eligible for Assemble Mode:** YES (with current Memory body intact); inner-body recipe SEPARATE follow-up
**Source scene:** `Relax-Spline_01_Tutorial.c4d` (DRuckli, on Desktop)
**Study:** `cinema4d-mcp/docs/scene_nodes_advanced_studies/scenes/17_relax_spline_tutorial/`

## Purpose

Generate a reaction-diffusion-style spline that grows over time, **contained within** the geometry of a substrate (e.g. extruded text, logo shape, or any closed surface). End-use: **Nike-logo-on-shoe** style growing decoration on any surface.

Frame 0: single seed spline. Frame 60: full maze pattern filling the containment shape interior.

## Provenance

- **Discovered:** 2026-05-01 in scene 17 study (R28 target recipe)
- **Earlier misanalysis (2026-05-04 first pass):** I mistakenly walked the `Geometry Axis` deformer inside the `Collision/Extrude` (a static axis-remap utility) and concluded "scene is static." User corrected me: the actual growth lives in the **doc-level Scene Nodes graph** (Scene Nodes hook id 1054188), NOT in any deformer.
- **Visual proof:** `_relax_scrub/frame_000.png` (single spline) vs `frame_060.png` (full RD maze in "3")
- **Last verified:** 2026-05-04 (Cinema 4D 2026.2.0)

## Critical lesson (added to discipline doctrine)

**Rendered viewport state is ground truth, not internal cached data.** When checking if a scene is animated, screenshot the viewport at multiple frames FIRST. Internal object caches may be static (the rooted `Spline` here is — it's only the seed reference) while the SN graph generates the visible result via a different pipeline.

## Architecture

The full chain lives at the **doc-level Scene Nodes graph**, accessed via:

```python
sn_hook = doc.FindSceneHook(1054188)  # Scene Nodes hook
graph = sn_hook.GetNimbusRef(maxon.Id("net.maxon.neutron.nodespace")).GetGraph()
```

**NOT** `doc.GetNimbusRef(...)` (returns None for doc-level SN — the hook is the entry point).

### Top-level data flow

```
[OM] Spline (UUID-referenced static seed)
    ↓
loa_spline (legacyobjectaccess) [baselistlink=Spline UUID]
    ↓ .geometry
memory.initial._0   ←  seed for first frame
memory.current._0   ←  memory.current._0   (SELF-FEEDBACK = per-frame loop driver!)
memory.in@xxx       ←  type1.out (point_size = 8.4)
memory.in@xxx       ←  type2.out (max_iterations = 15)
memory.in@xxx       ←  type3.out (relax_size = 8)
    ↓ .nextout._0
tess1_spline (tessellation) [angle=0.087, length=5, type=none]
    ↓ .geometryout
geom3_minimal_wrap (geometry op)
    ↓ .net.maxon.neutron.op.output
scene_root.net.maxon.neutron.op.objectbase.children._0   ← VISIBLE OUTPUT
    ↓
.net.maxon.neutron.op.output (graph-top output)
```

**Optional sweep visualization branch (parallel):**
```
tess1_spline.geometryout → sweepline.geometry (as Lines)
circle (r=100) → tess2_circle [length=5, type=uniform] → sweepline.geometry (as Profile)
sweepline.geometryout → subdivide → smoothgeometry → geom2_smooth_wrap → geom1_final → material.input
material.result → if.in3   (conditional toggle for which output goes to scene_root)
```

The minimal visible chain bypasses the sweep entirely. The user's screenshot shows both — the sweep gives a tube-rendered version with material; the minimal gives raw spline.

## Required asset IDs (VERIFIED 2026-05-04 via scene_nodes_list_assets)

```python
# Outer-chain primitives
A_LOA           = maxon.Id("net.maxon.nbo.node.legacyobjectaccess")          # read OM object
                    # (variant: "net.maxon.nbo.node.op.legacyobjectaccess" for op-wrapped form)
A_MEMORY        = maxon.Id("net.maxon.node.memory")                          # loop-carried-value capsule
A_TESSELLATION  = maxon.Id("net.maxon.neutron.geometry.spline.tessellation") # spline tessellate
A_GEOMETRY_OP   = maxon.Id("net.maxon.neutron.op.geometry")                  # op-wrap (verified by inspection)
A_TYPE          = maxon.Id("net.maxon.node.type")                            # value-type wrapper

# Memory body primitives (KEY for the relax-spline algorithm)
A_PUSHAPART      = maxon.Id("net.maxon.neutron.node.distribution.parametric.pushapart")
A_CLOSESTPOINT   = maxon.Id("net.maxon.nbo.node.collision.closestpointonsurface")
# Plus standard math/array primitives: containeriteration, compare, erase, concat,
# arithmetic, length, append, readvalueatindex, writevalueatindex, etc.
```

**Tessellation settings sub-asset:** `net.maxon.neutron.geometry.spline.tessellation.settings` — for parameterizing tessellation behavior.

## Memory capsule body (the relax-spline RD algorithm)

The Memory capsule has an INNER BODY of 36 nodes that implements the relax-spline reaction-diffusion. The artist customized this body — Maxon ships an empty Memory scaffold (just `initial`/`current`/`next` ports, no body).

### Inner node inventory (36 nodes)

| Category | Count | Nodes |
|---|---|---|
| Iteration | 4 | `containeriteration ×4` (multiple loops, possibly nested) |
| Surface containment | 2 | `closestpointonsurface ×2` (KEY — keeps growth on the substrate surface) |
| Point repulsion | 1 | `pushapart` (KEY — relax-spline repulsion between points) |
| Conditional logic | 2 | `compare ×2` |
| Array ops | 8 | `erase ×4`, `concat ×2`, `append`, `length` |
| Index access | 4 | `readvalueatindex ×2`, `writevalueatindex ×2` |
| Math | 3 | `arithmetic ×3` |
| Building | 3 | `buildfromvalue ×2`, `buildfromsinglevalue` |
| Misc | 9 | `assembler`, `distance`, `range`, `decomposematrix`, `container ×2`, `evaluate`, `legacyobjectaccess ×2`, `typeof` |

### Algorithm (high-level inferred from node mix)

Per iteration:
1. Read current spline points + parameters
2. For each point: find closest point on substrate surface (`closestpointonsurface ×2`)
3. Apply pushapart force to keep points distributed (Point Size param controls min spacing)
4. Snap points back onto substrate surface (containment)
5. Append new points where spline is too short OR remove where too long (concat/erase/append)
6. Run multiple relax iterations per frame (Max Iterations param)
7. Output evolved spline → `next._0`

The user's screenshot shows the closest-point-on-surface mechanism is what does CONTAINMENT to the extruded "3" volume. The pushapart provides the relax/RD aesthetic.

## Memory ports (stock asset structure)

A fresh `net.maxon.node.memory` has:

| Direction | Port | Purpose |
|---|---|---|
| Input | `domain` | iteration domain spec |
| Input | `types` | type metadata for state |
| Input | `initial._0` | seed value (frame 1) |
| Input | `current._0` | self-feedback (current state from previous frame) |
| Input | `in@xxx` (variadic) | additional parameters/inputs accessible inside body |
| Output | `next._0` | computed next state (will become current next frame) |
| Output | `nextout._0` | external-facing output (often = next._0) |
| Output | `currentout._0` | external-facing current state |
| Output | `net.maxon.node.memory.dependency_0` | dependency tracking |

## **Critical idiom: doc-level SN graph access**

```python
# WRONG — returns None for doc-level SN
graph = doc.GetNimbusRef(NODESPACE).GetGraph()

# CORRECT — Scene Nodes hook (id 1054188) IS the entry point
sn_hook = doc.FindSceneHook(1054188)
graph = sn_hook.GetNimbusRef(NODESPACE).GetGraph()
```

`doc.GetAllNimbusRefs()` returns empty for doc-level SN graphs. The hook is the only access path.

## **Critical idiom: Memory capsule interior IS writable** (unlike uvtomesh)

```python
mem_view = graph.CreateView(maxon.NODE_KIND.NODE, memory_node.GetPath())
with mem_view.BeginTransaction() as tx:
    mem_view.AddChild(maxon.Id("my_inner_node"), maxon.Id("net.maxon.node.invert"))
    tx.Commit()  # SUCCEEDS for memory; FAILS for uvtomesh
```

This means the 36-node body CAN be authored from scratch via Python. (Uvtomesh's body could not.)

## Safe-to-edit parameters

- **`type1.in`** (Float, default 8.4) — POINT SIZE = minimum point spacing on the spline
- **`type2.in`** (Int, default 15) — MAX ITERATIONS = relax steps per frame (higher = smoother but slower)
- **`type3.in`** (Float, default 8) — RELAX SIZE = radius of repulsion force
- **`tess1.length`** (Float, default 5) — tessellation segment length on output
- **`tess1.angle`** (Float, default 0.087 rad ≈ 5°) — tessellation angle threshold
- **The OM Spline UUID** referenced by `loa_spline.baselistlink` — change to point at a different seed spline

## NOT safe to edit

- The Memory capsule's body wiring (36 nodes — deep relax-spline algorithm)
- `memory.current._0 ← memory.current._0` self-feedback wire (THE iteration loop)
- The substrate-surface containment is hardcoded inside the Memory body via `closestpointonsurface` (the substrate Landscape is referenced inside)

## Substrate replacement (Nike-on-shoe path)

To use this for a NEW containment shape (e.g., a Nike swoosh logo extruded over a shoe surface):
1. Replace the substrate Landscape with your target surface (e.g., shoe mesh)
2. Replace the Text Spline (the "3") with your logo spline
3. The `Collision/Extrude` wraps the logo spline → containment volume
4. Inside the Memory body, the `closestpointonsurface ×2` nodes reference the substrate via `legacyobjectaccess` — those references point to a specific UUID; need to redirect them to your new surface
5. Initial seed Spline can be any small starting spline inside the containment

The Memory body's `legacyobjectaccess ×2` are the "knobs" that connect the algorithm to the substrate + containment surfaces.

## MCP authoring script (OUTER chain — Memory body separate)

```python
import c4d, maxon

NODESPACE = maxon.Id("net.maxon.neutron.nodespace")

def author_relax_spline_outer(doc, seed_spline, point_size=8.4, max_iter=15, relax_size=8.0):
    """Author the OUTER chain. Memory body must be populated separately
    (clone from reference scene OR from-scratch sub-recipe).
    Returns the doc-level SN hook + memory node for further customization."""
    sn_hook = doc.FindSceneHook(1054188)
    graph = sn_hook.GetNimbusRef(NODESPACE).GetGraph()

    A_LOA          = maxon.Id("net.maxon.neutron.geometry.legacyobjectaccess")
    A_MEMORY       = maxon.Id("net.maxon.node.memory")
    A_TESSELLATION = maxon.Id("net.maxon.neutron.geometry.tessellation")
    A_GEOMETRY_OP  = maxon.Id("net.maxon.neutron.op.geometry")
    A_TYPE         = maxon.Id("net.maxon.node.type")

    def _in(node, name): return node.GetInputs().FindChild(maxon.InternedId(name))
    def _out(node, name): return node.GetOutputs().FindChild(maxon.InternedId(name))

    # Add 7 nodes
    nodes_spec = [
        ("loa_spline", A_LOA),
        ("memory", A_MEMORY),
        ("tess1", A_TESSELLATION),
        ("geom_op", A_GEOMETRY_OP),
        ("type_pointsize", A_TYPE),
        ("type_maxiter", A_TYPE),
        ("type_relaxsize", A_TYPE),
    ]
    for label, aid in nodes_spec:
        with graph.BeginTransaction() as tx:
            graph.AddChild(maxon.Id(label), aid)
            tx.Commit()

    # ... configure + wire (full implementation pending verification)
    # Memory body MUST be populated separately — empty Memory passes-through, no growth
    return sn_hook, "memory"
```

(Full from-scratch authoring pending — see "Open work" below.)

## Verification test

```python
def verify_growth(doc, frame_start=0, frame_end=60):
    """Capture viewport at multiple frames, confirm visible growth via file size diff
    (file size grows as more pixels are filled with the maze pattern)."""
    fps = doc.GetFps()
    sizes = []
    for f in [frame_start, frame_end]:
        doc.SetTime(c4d.BaseTime(f, fps))
        doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)
        # ... viewport_screenshot at frame f, save to disk, get file size
        # If size_60 / size_0 > 1.5, growth confirmed
```

Empirical reference for Relax-Spline_01_Tutorial.c4d:
- frame 0: ~78KB (single thin spline)
- frame 15: ~86KB
- frame 30: ~101KB
- frame 60: ~145KB (full maze pattern) — almost 2× growth confirmed

## Anti-patterns

- ❌ Don't try `doc.GetNimbusRef()` for doc-level SN — returns None. Use the hook.
- ❌ Don't trust internal object caches as proof of animation state — viewport screenshots are ground truth.
- ❌ Don't skip the `current._0 ← current._0` self-feedback wire — that IS the loop. Without it, no per-frame iteration.
- ❌ Don't expect a fresh empty Memory to grow anything — the BODY is what does the work.

## Open work (deferred to follow-up sessions)

1. **Walk the Memory body wires** (36 nodes; need full inner connection map)
2. **Verify all asset IDs** for sweepline/subdivide/smoothgeometry/material via `scene_nodes_list_assets`
3. **From-scratch rebuild** of the Memory inner body — large undertaking; sub-recipe candidate
4. **Generalization** — refactor Memory body's `legacyobjectaccess ×2` to use FloatingIO ports so substrate/containment surfaces can be swapped without inner-graph mutation
5. **Substrate-swap test** — replace Landscape with a shoe surface, replace Text Spline with a Nike swoosh, verify growth fills the swoosh interior

## Cross-recipe links

- `SN_RECIPE_buv_pathb_uv_position` — established the canonical SN deformer authoring pattern; same `MSG_CREATE_IF_REQUIRED` + `FindChild(InternedId)` idioms apply
- `SN_RECIPE_wire_remove_swap_pattern` — `WIRE_MODE.REMOVE` will be needed for any Memory body mutation involving wire-swap

## Status summary

| Component | State |
|---|---|
| Visual growth confirmed | ✓ frame 0..60 screenshots prove it |
| Doc-level SN hook access | ✓ `doc.FindSceneHook(1054188)` |
| Outer chain (7 nodes) mapped | ✓ all wires + ports documented above |
| Memory body (36 nodes) inventory | ✓ node types listed; wires NOT yet mapped |
| Memory capsule writable via Python | ✓ confirmed (unlike uvtomesh) |
| MCP authoring script | ⚠ outer-chain skeleton only; needs port-config completion + verification |
| From-scratch rebuild verified | ✗ NOT YET (this entry is documentation; rebuild is next) |
| Recipe library .c4d entry | ✗ pending after rebuild verification |
