# Canonical Scene Nodes Deformer Pattern — Geometry-In → Modified Positions → Geometry-Out

**Status:** PROVEN WORKING 2026-05-02 after 22 iterations of failed attempts using uvtomesh's pattern.

**The unlock came from GPT review:** stop trying wire permutations on uvtomesh (a topology generator), find a real position-modifier deformer (Spline Smooth) and inspect ITS canonical write contract. Then build a minimal cube test before scaling up.

## The pattern

```
get(accessortype=data3d, accessorname="", componentin=points)
  → containeriteration
    → [per-vertex math: split, compose, blend, arithmetic, scale, etc]
      → set(accessortype=data3d, accessorname="", arraymode=False).iteration

PLUS:
  root.geometryin → get.geometry          (read input geometry)
  root.geometryin → set.geometryin        (write target context)
  get.topology → set.topology             (preserve topology)
  set.geometryout → root.geometryout      (output)
```

## Critical configuration details

**get node** (`net.maxon.neutron.geometry.get`):
- `accessortype = net.maxon.geometryabstraction.accessortypes.attributes.data3d`
- `accessorname = ""` (EMPTY STRING — defaults to Position attribute)
- `componentin = "points"` (per-source-vertex)

**set node** (`net.maxon.neutron.geometry.set`):
- `accessortype = net.maxon.geometryabstraction.accessortypes.attributes.data3d`
- `accessorname = ""` (EMPTY STRING — same as get)
- `arraymode = False` (this exposes the `.iteration` port)
- `newdataset = False` (modify existing attribute, don't create new)

**The key insight that took 22 iterations to find:**
**`accessorname = ""` (empty string) NOT `"Position"` NOT `"pt"`.** Empty name = "default attribute of this accessortype." Discovered by inspecting Reaction Diffusion's set nodes which use empty accessorname.

## Why uvtomesh's pattern was wrong for our use case

uvtomesh is a **topology GENERATOR** — it BUILDS a new mesh with different vertex count (4664) from the source (1168). Its set.iteration writes to that newly-generated mesh's vertices, not to the input.

For a **position MODIFIER deformer** (same vertex count in & out), the canonical pattern is what's documented above. Different writeback contract entirely.

## Proven: minimal cube +10Y deformer

```python
# All inside ONE atomic transaction:
g = graph.AddChild(maxon.Id("g"), maxon.Id("net.maxon.neutron.geometry.get"))
s = graph.AddChild(maxon.Id("s"), maxon.Id("net.maxon.neutron.geometry.set"))
it = graph.AddChild(maxon.Id("it"), maxon.Id("net.maxon.node.containeriteration"))
arith = graph.AddChild(maxon.Id("arith"), maxon.Id("net.maxon.node.arithmetic"))

# Configure
ACC = maxon.Id("net.maxon.geometryabstraction.accessortypes.attributes.data3d")
get_port(g, "accessortype").SetPortValue(ACC)
get_port(g, "accessorname").SetPortValue(maxon.String(""))
get_port(s, "accessortype").SetPortValue(ACC)
get_port(s, "accessorname").SetPortValue(maxon.String(""))
get_port(s, "arraymode").SetPortValue(maxon.Bool(False))
get_port(s, "newdataset").SetPortValue(maxon.Bool(False))
get_port(arith, "operation").SetPortValue(maxon.Id("add"))
get_port(arith, "datatype").SetPortValue(maxon.Id("net.maxon.parametrictype.vec<3,float>"))
get_port(arith, "in2").SetPortValue(maxon.Vector(0, 10, 0))

# Wire
root.geometryin → g.geometry
root.geometryin → s.geometryin
g.array → it.in
g.topology → s.topology
it.out → arith.in1
arith.out → s.iteration
s.geometryout → root.geometryout
```

**Result on 100×100×100 cube (98 verts, mp=0,0,0):**
- Without deformer: `mp=(0, 0, 0)`
- With deformer: `mp=(0, 10, 0)` — **moved up exactly 10 units, vertex count preserved (98)**

## Proven: head Y-flatten morph slider

Same pattern, swap arithmetic for blend chain:

```python
# Per-iteration: split orig into x/y/z, compose flat as (x, 0, z), blend by factor
splitv = AddChild(...splitvectorcomponents)
composev = AddChild(...composevector3)
blend = AddChild(net.maxon.node.blend)
get_port(blend, "datatype").SetPortValue(VEC3)

# Wire (replacing arithmetic with blend chain):
it.out → splitv.vector
splitv.x → composev.x
splitv.z → composev.z
# composev.y stays at 0 (default)
it.out → blend.in1   (orig per-iter)
composev.result → blend.in2  (flat per-iter, y=0)
blend.in3 = factor (Float64 0..1)
blend.out → s.iteration
```

**Verified factor sweep on Generic Head Bust (1168 verts, orig mp.y=21.22):**

| factor | rad | mp |
|---:|---|---|
| 0.00 | (17.4, 21.2, 12.5) | (0, 21.22, -1.9) |
| 0.25 | (17.4, 15.9, 12.5) | (0, 15.91, -1.9) |
| 0.50 | (17.4, 10.6, 12.5) | (0, 10.61, -1.9) |
| 0.75 | (17.4,  5.3, 12.5) | (0,  5.31, -1.9) |
| 1.00 | (17.4,  0.0, 12.5) | (0,  0.00, -1.9) |

Perfect linear interpolation. Vertex count preserved (1168 throughout).

## Lessons from the 22-iteration journey

1. **The wall was never "Scene Nodes can't do it"** — it was "we used the wrong write contract."
2. **uvtomesh is a generator, not a modifier** — wrong reference for a deformer pattern.
3. **`accessorname=""` not `"Position"` or `"pt"`** — single character of discovery unlocked everything.
4. **Pattern discovery beats wire permutation** — find a working capsule with the right output type, copy its contract.
5. **Minimal test before scaling up** — cube + add(0,10,0) caught the issue immediately; would have saved 20 iterations.
6. **`graph.CreateView(filter, rootPath)` was a real unlock** — and works for capsule-internal mutation. Not used in the final canonical pattern (we operate at deformer's root graph), but proven reusable.
7. **Domain alignment doctrine** (GPT) — never blend streams across different cardinality without explicit mapping. Position vs UV vs polygon-vertex are different domains.

## Files

- `MIN_PURE_SN_DEFORMER.c4d` — minimal cube +10Y deformer proof
- `PURE_SN_MORPH_WORKING.c4d` — head Y-flatten morph slider proof
- `PURE_SN_min_deformer_works.png` — viewport screenshot of the cube test
- `PURE_SN_morph_factor_1.png` — viewport screenshot at factor=1 (head fully flattened)

## Next: actual UV-coord morph

Now that the canonical write contract is proven, the UV morph is straightforward:

1. Use this exact pattern
2. Replace `composev.y = 0` (constant Y) with `composev.x/y/z = uv_at_this_vertex.x*scale / -uv_at_this_vertex.y*scale / 0`
3. The remaining puzzle is reading per-vertex UV — likely via a SECOND get node with `accessortype=uv` aggregated to per-vertex via averaging (or use `get_property` with the right `componentin`)

This is a standard extension of the proven pattern, not a new architecture.

## Spine reusability — PROVEN via target swap (v2)

GPT-disciplined extension: lock the spine, swap ONLY the target organ. Replaced the per-vertex math from `(orig.x, 0, orig.z)` to `(orig.x*2, 0, orig.z*2)`. Same spine identical, additional `scale_x` + `scale_z` nodes inserted before composevector3.

**Verified factor sweep:**

| factor | rad | math check |
|---:|---|---|
| 0.0 | (17.4, 21.2, 12.5) | orig (no morph) |
| 0.5 | (26.16, 10.62, 18.75) | (17.4×1.5, 21.2×0.5, 12.5×1.5) ✓ exact |
| 1.0 | (34.88, 0.00, 25.00)  | (17.4×2, 0, 12.5×2) ✓ exact |

Math precision: 26.156 vs expected 26.1, 10.619 vs 10.6, 18.748 vs 18.75 — sub-millimeter accuracy.

**The pattern is REUSABLE.** Lock spine, swap one organ at a time. This is the canonical authoring discipline for SN deformers.

## Per-vertex UV access (the remaining bridge for actual UV-coord morph)

Pure SN can read UV via:
- `accessortype=uv, accessorname=""` → 4664-length per-polygon-vertex array

Per-source-vertex UV is NOT directly available as an attribute (UV is fundamentally per-poly-vertex in C4D). To bridge:

**Option A (pure SN):** use `getpolygonselectiondata` (which exposes `ptsidsout` = source vertex IDs per poly-vertex) + `readvalueatindex2` chain to look up. This is the SPVP capsule's pattern.

**Option B (hybrid pre-bake):** Python helper script bakes averaged UV onto a Vertex Color tag (Vec3 per vertex). SN reads it via `accessortype=color, accessorname="<tag-name>"`. Works because Vertex Color is per-vertex when accessed correctly.

**Option C (deformer geo-input swap):** the deformer host's INPUT geometry is replaced with one whose Position attribute IS the averaged UV. Then read "Position" gives the UV-derived flat positions. Most pragmatic for one-off use.

For our PROVEN canonical pattern, all three are valid extensions. The target-swap discipline (v2) demonstrates that swapping in any per-vertex Vec3 source works — once we have that source, the morph chain is identical.

## Option C exploration notes (iter 24)

Tried Tvertexcolor in PerPointMode via VariableTag(c4d.Tvertexcolor, n). API surfaced unexpected complexity:
- `SetPoint(data_ptr, neighbor, polygon_ptr, vertex_idx, vec)` — requires polygon ptr even in per-point mode
- The Vertex Color tag is fundamentally per-poly-vertex; PerPointMode is a storage hint not an access mode shift

Cleaner Option C variants that need exploration:
- **3 Vertex Maps** (orig_x, orig_y, orig_z scaled to 0-1, then composed in SN). Vertex Maps ARE per-vertex Float and SN reads cleanly via `accessortype=weight`.
- **Custom Vec3 attribute** via maxon's MeshAttribute API directly (skips the wrapper tags).
- **Replace Position via SetAllPoints** to UV-flat, then store orig as Pose Morph base pose — uses C4D's native morph machinery.

The canonical write contract (proven) is the unlocking primitive. Per-vertex source supply is a separate puzzle with multiple valid solutions; pick the one that fits the workflow.

For this session: **canonical pattern + spine reuse SHIPPED**. Per-vertex UV-coord SOURCE is the next concrete step, but the deformer architecture is no longer the blocker.
