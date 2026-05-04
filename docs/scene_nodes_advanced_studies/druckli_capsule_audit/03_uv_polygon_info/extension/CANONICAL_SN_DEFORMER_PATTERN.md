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

## ITER 25 — PURE-SN UV-COORD MORPH SHIPPED via 3-vertex-map approach

GPT picked Option C variant: **3 Vertex Maps storing target XYZ**, read in SN via `accessortype=weight`, composed into Vec3, blended with orig pos.

### The pattern (extends canonical spine)

```
SPINE (locked):
  get_pos(data3d, "")
    → containeriteration
      → blend(orig_per_iter, target_per_iter, factor)
        → set(data3d, "", arraymode=False).iteration

PER-VERTEX TARGET COMPOSITION (the new organ):
  get_x(weight, "uv_target_x") ─┐
  get_y(weight, "uv_target_y") ─┼─ readvalueatindex2(arr, iter.index) → composevector3 → blend.in2
  get_z(weight, "uv_target_z") ─┘
```

The **single-iter + readvalueatindex2** pattern bridges domains: iter_pos drives the iteration, rvi looks up the per-vertex weight at that index. Two parallel iters DO NOT auto-align — single iter + rvi is the right pattern for cross-stream lookup.

### Preprocessing step (one-time, hybrid)

```python
# Compute averaged UV per source vertex
uvw_sum = [c4d.Vector(0,0,0) for _ in range(n)]
uvw_cnt = [0] * n
for poly_idx in range(src.GetPolygonCount()):
    poly = src.GetPolygon(poly_idx)
    uv = uvtag.GetSlow(poly_idx)
    for ci, vidx in enumerate([poly.a, poly.b, poly.c, poly.d]):
        uvw_sum[vidx] += [uv["a"], uv["b"], uv["c"], uv["d"]][ci]
        uvw_cnt[vidx] += 1

SCALE = 50.0
target_x = []; target_y = []; target_z = []
for i in range(n):
    a = uvw_sum[i] / uvw_cnt[i] if uvw_cnt[i] > 0 else c4d.Vector(0,0,0)
    target_x.append((a.x - 0.5) * SCALE)
    target_y.append(0.0)
    target_z.append(-(a.y - 0.5) * SCALE)

# Bake as 3 Vertex Maps via SetAllHighlevelData
for name, data in [("uv_target_x", target_x), ("uv_target_y", target_y), ("uv_target_z", target_z)]:
    vm = c4d.VariableTag(c4d.Tvertexmap, n)
    vm.SetName(name)
    vm.SetAllHighlevelData(data)
    mesh.InsertTag(vm)
```

**Vertex maps store RAW Float values** (not clamped 0-1). Verified roundtrip: target_x[0]=17.675 → vmap.GetAllHighlevelData()[0]=17.675.

### Verified result on Generic Head Bust

```
f=0.00 → rad (17.4, 21.2, 12.5)  mp (0,    21.22, -1.9)  ← orig 3D head
f=0.25 → rad (14.6, 15.9, 10.9)  mp (-.01, 15.91,  .01)
f=0.50 → rad (14.4, 10.6, 14.2)  mp (-.02, 10.61, -.58)  ← halfway morph
f=0.75 → rad (17.2,  5.3, 18.6)  mp (-.03,  5.31, -.32)
f=1.00 → rad (21.9,  0.0, 23.8)  mp (-.04,  0.00,  .70)  ← FULL UV LAYOUT (Y=0 plane)
```

Topology preserved (1168 verts throughout). Linear factor sweep. Pure Scene Nodes deformer (after one-time Python preprocessing for vmap baking).

### Files

- `PURE_SN_UV_MORPH_3VMAP.c4d` — working scene with 3-vmap UV morph
- `PURE_SN_3vmap_factor_1.png` — viewport at f=1 (head fully flattened to UV layout)
- This doc — canonical pattern + 3vmap extension

### Why this is the right shipped form

- **Pure SN at runtime**: deformer is 100% Scene Nodes, no Python tag eval per frame
- **Reusable preprocessing pattern**: any per-vertex Vec3 source can be packed into 3 vmaps + read same way
- **Vertex maps are first-class C4D data**: render-safe, scene-saved, editable in C4D's vmap tools
- **Same canonical spine**: the `get → iter → math → set` contract is reused identically

---

## ⚠️ HONEST ACCOUNTING — what was overclaimed

After visual side-by-side comparison with Build UV Preview, the iter 25 "PURE SN UV MORPH SHIPPED" claim was WRONG. Real comparison:

| | Build UV Preview (DRuckli) | My PURE SN UV MORPH (3vmap) |
|---|---|---|
| Vertex count | **4664** (split UV islands) | **1168** (welded source topology) |
| Bounds (rad) | (24.82, 24.32, 0) — Y-up plane | (21.91, 0.00, 23.79) — Y=0 plane |
| Visual at f=1 | Clean recognizable UV unwrap with 2 islands | Folded distorted strip on ground plane |

**They are not the same result.** Side-by-side proof: `HONEST_COMPARISON_druckli_vs_mine.png`.

### Why my deformer can't match Build UV Preview

`uvtomesh` (DRuckli's capsule) is a **topology GENERATOR** — it OUTPUTS a new mesh with 4664 split vertices, one per polygon-corner. Per-corner positions = (uv.x*scale, -uv.y*scale, 0). This produces clean UV island separation.

My pattern is a **position MODIFIER deformer** — preserves topology (1168 verts in, 1168 out). Each vertex morphs to a SINGLE position, so seam-vertices that have multiple UV coords get averaged into ONE point. Result: distorted "fold" shape, not a clean UV unwrap.

**Deformers cannot reproduce DRuckli's flat output by definition** — split topology requires a GENERATOR pattern, not a deformer.

### What was actually shipped

✓ A reusable canonical SN deformer pattern (geometry-in → modified positions → geometry-out)
✓ Single-iter + readvalueatindex2 cross-stream lookup pattern
✓ 3-vmap per-vertex Vec3 source supply pattern
✓ A working morph slider that flattens the head onto an averaged-UV plane (NOT a UV unwrap)

### What was NOT shipped

✗ A pure-SN equivalent of Build UV Preview's output (the actual UV unwrap with split islands)
✗ Any topology-rebuilding pattern (generator-style write contract)

### What it would take to match DRuckli

The right primitive is a SN **GENERATOR** (180420500-700), not a deformer (180420400). The generator needs to:
1. Read input geometry (gateway.geometryin)
2. Iterate polygons (or polygon-corners — 4664 entries)
3. Output a NEW mesh with split topology
4. Per-output-vertex position = UV-derived flat position

`getpolygonselectiondata.ptsposout` provides per-poly-vertex source positions. We'd want analogous per-poly-vertex UV access (which we haven't fully cracked).

Or simpler: clone DRuckli's uvtomesh, use it as-is for the flat output, and apply morph at a DIFFERENT level (e.g., between the orig 3D mesh and uvtomesh's flat output via a Pose Morph or geometry-blend pattern).

The user is right: **what we have is a working pure-SN deformer pattern, not a 1-1 reproduction of Build UV Preview.** Two different things.
