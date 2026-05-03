# Build UV Preview — Granular Reference Dump

**Source:** `Build UV Preview` SN Deformer in `_snapshots_t0/UV-Polygon-Info_Example_01.c4d`
**Captured:** 2026-05-02
**Why this exists:** Per-port reference to ground all rebuild attempts in literal DRuckli truth, not guesswork.

---

## Layer 1 — Top-level deformer graph

**4 functional nodes + 2 context nodes. 4 wires.**

### Nodes

| Id | Asset | Purpose |
|---|---|---|
| `get_property@btZ$7yDaM9XmsGyjZr51uo` | `net.maxon.neutron.geometry.get_property` | Read UVW array |
| `transform_element@Iw9KeqNmPZ4mKMXpAKrBIq` | `net.maxon.neutron.geometry.transform_element` | Re-orient flat mesh in space |
| `uvtomesh@cPG6cQOOHF3pO3DY4E0SBp` | **scene-local custom capsule** (9 inner nodes) | Per-vertex UV→flat conversion |
| `set_property@Og3Fg6f4I1LpxNk2Foqzqu` | `net.maxon.neutron.geometry.set_property` | Write back UVW so flat mesh keeps its UVs |

### Wires (top-level)

```
__ROOT__.geometryin   → uvtomesh.geometryin                       (gateway)
uvtomesh.geometryout  → transform_element.geometryin
transform_element.geometryout → set_property.geometryin
set_property.geometryout      → __ROOT__.geometryout              (gateway)

__ROOT__.geometryin → get_property.geometry                       (UV side-branch)
get_property.array  → set_property.array                          (writes UVW back onto flat mesh)
get_property.accessortypeout → get_property.accessortype          (self-loop type propagation)
```

### Port values (top-level)

**get_property (read UVW):**
- `accessorname = "UVW"`
- `accessortype = net.maxon.geometryabstraction.accessortypes.attributes.<truncated>` (likely polyvertexvalues)
- `componentin = "points"`
- `fallbackmodein = "none"`

**set_property (write UVW back):**
- `accessorname = "UVW"`
- `accessortype = same as get_property`
- `arraymode = true`
- `newdataset = true`
- `fallbackmodein = "none"`

**transform_element:**
- `selectionstringin = "default"`
- `selectiontypein = "points"`
- `pivotin = 0`
- (transformin, boundingbox*, useselectionbb, pivotmatrixin all = None → defaults)

**uvtomesh (top-level inputs):**
- `inport@PxTGkq2oDdAgGRlbBgxn7m = 50` (scale factor — exposed on the capsule)
- `geometryin = None` (filled by wire from root)

---

## Layer 2 — uvtomesh capsule internals

**9 functional nodes. 14 wires.** This is where the per-vertex math happens.

### Inner nodes

| Id | Asset | Role |
|---|---|---|
| `get@GZJeUTHcPJhi$2j0_fSBZs` | `net.maxon.neutron.geometry.get` | Read input attribute (UV array) |
| `containeriteration@EGGDHxSqLBlobCYtom4gNq` | `net.maxon.node.containeriteration` | **THE bridge: array → per-element vec3** |
| `splitvectorcomponents@a3nE3CW7BFAo33zEoZ7oXp` | `net.maxon.pattern.node.conversion.splitvectorcomponents` | vec3 → x, y, z |
| `invert@INPWz6BsIlehvXHfvGRb$v` | `net.maxon.node.invert` | Flip Y (datatype = `float`) |
| `composevector3@I5ChQe6JK5Zl1R2S2ivkRs` | `net.maxon.pattern.node.conversion.composevector3` | (x, -y, 0) → vec3 |
| `scale@YICGjSYWJFPiS$_K0eyXLk` | `net.maxon.node.scale` | × 50, datatype = `vec<3,float>` |
| `set@KWVj7mlENhWo8HOfRdDK6r` | `net.maxon.neutron.geometry.set` | Write per-iteration position back |
| `geometry@Pyo9PEUUEB_lK82Wcg3cbo` | `net.maxon.neutron.op.geometry` | Geometry op container (wraps set) |
| `filter@Ime1$$wdB5qhe69kB5A3xv` | `net.maxon.neutron.op.filter` | Filter op (likely the topology forwarder) |

### Inner wires (the per-vertex pipeline)

```
get.array                  → containeriteration.in              ← array enters iteration
containeriteration.out     → splitvectorcomponents.vector       ← per-vertex UV vec3
splitvectorcomponents.x    → composevector3.x                   ← preserve X
splitvectorcomponents.y    → invert.in                          ← flip Y
invert.out                 → composevector3.y                   ← (-Y)
composevector3.result      → scale.in1                          ← compose (x, -y, 0)
scale.out                  → set.iteration                      ← scaled vec3 → SET'S ITERATION INPUT
get.topology               → set.topology                       ← preserve topology

get.accessortypeout        → get.accessortype                   ← self-loop type propagation
get.accessornameout        → get.accessorname                   ← self-loop name propagation

geometry.output            → geometry.input                     ← geometry op self-loop
geometry.output            → filter.input                       ← geometry → filter
filter.output              → filter.input                       ← filter self-loop
set.geometryout            → geometry.geometry                  ← set feeds geometry op
```

### Critical port-value details

**get (input read):**
- `accessorname = ""` (empty — accessor name read from outer port?)
- `accessortype = net.maxon.geometryabstraction.accessortypes.attributes.<truncated>`

**containeriteration:**
- `in`, `domain`, `datatype`, `independent` all = None (defaults)

**scale:**
- `datatype = net.maxon.parametrictype.vec<3,float>` ✓
- `in2 = 50` (the scale factor — exposed via `uvtomesh.inport@PxTGkq...` to outer capsule)
- `in1 = None` (filled by composevector3.result)

**invert:**
- `datatype = float` ← scalar, not vec3 (because it operates on Y component only after split)

**set (write):**
- `accessortype = net.maxon.geometryabstraction.accessortypes.attributes.<truncated>` (Position-style)
- `newdataset = true`
- All other inputs (topology, geometryin, accessorname, arraymode, iteration, rebuildtopology) = None / wired from elsewhere

**composevector3:**
- `x = None`, `y = None`, `z = None` (all wired or defaulted to 0)

---

## The "set.iteration" insight — KEY DISCOVERY

**This is the bridge that makes per-vertex math work in SN:**

`net.maxon.neutron.geometry.set` has an `iteration` input port (NOT just `array`).

When you wire:
```
containeriteration.out → [per-vertex math nodes] → set.iteration
```

…then `set` automatically loops over the iteration source, applying the math to each element. The result is a fully transformed array written into the geometry — without needing to manually iterate or use loopcarriedvalue.

This is why my earlier wire-surgery attempts on `set_property.array` (with whole-array math) all failed: the math nodes (scale, blend) **don't broadcast across arrays**. They operate on single values. The bridge is `containeriteration → math → set.iteration`.

---

## Why my fresh build (`UV Morph Slider`) failed

I added 12 nodes and wired:
```
orig_get.array → scale_op.in1     (whole array fed to scale)
scale_op.in2  = 2.0
scale_op.out  → set_pos.array     (whole-array write)
```

`scale_op` doesn't broadcast over arrays → produced empty/null output → `set_pos` wrote nothing → `deform_cache.GetPointCount() = 0`, `rad = (0,0,0)`.

**The fix:** the chain must be:
```
get.array → containeriteration.in
containeriteration.out → scale.in1 (per-vertex)
scale.out → set.iteration (NOT set.array!)
```

---

## Implications for the 3D ↔ flat morph slider

**For the slider, we need TWO source positions per vertex** (orig_xyz, flat_xyz) blended by a factor.

The DRuckli pattern doesn't directly do a blend — it just REPLACES with the flat. So we need to extend it:

```
get_orig_pos.array → containeriteration_orig.in          → cont.out = orig_vec3 (per-vert)
get_uv_pos.array   → containeriteration_uv.in            → cont.out = uv_vec3 (per-vert)

# Inside the iteration body, do the per-vertex blend:
blend(in1=orig_vec3, in2=composed_flat_vec3, in3=factor) → set.iteration
```

But: **two parallel containeriterations need to step IN LOCKSTEP.** That's not automatic. The `set.iteration` connection only iterates ONE source. To consume two arrays in parallel, we need either:

- **(a)** Read the second array via `get` indexed by `containeriteration.index` (read-by-index pattern)
- **(b)** Use `loopcarriedvalue` for explicit dual-array iteration
- **(c)** Pre-combine the two arrays into a paired vec6 array, iterate once, split back

Option (a) is the simplest. The pattern would be:
```
get_orig_pos.array → containeriteration.in         (drives iteration)
containeriteration.out  → blend.in1                (per-vertex orig)
containeriteration.index → readvalueatindex.index 
get_uv_pos.array       → readvalueatindex.array   → readvalueatindex.value → composevector3 (x,-y,0) chain → scale → blend.in2
fio_factor             → blend.in3
blend.out              → set.iteration
```

That's the full architecture. ~10 nodes, all addable as primitives (containeriteration, readvalueatindex, splitvectorcomponents, invert, composevector3, scale, blend, set, get×2).

---

## Replication plan (next iteration)

1. **Save reference dump** ✓ (this doc)
2. **Build fresh "UV Morph Slider v2" using the verified internal pattern:**
   - 11 nodes per the architecture above
   - Single containeriteration drives the per-vertex pipeline
   - readvalueatindex bridges the second (UV) array
   - blend per-vertex with factor from floatingio
   - set.iteration (NOT set.array!) writes back to Position
3. **Test factor=0/0.5/1.0** — should see head ↔ flat morph
4. **Capture frames + commit**

## Files in this folder
- `UV_SLIDER_PROGRESS.md` — earlier 3-iteration debug log (wire-surgery on existing capsule, failed)
- `UVTOMESH_GRANULAR_REFERENCE.md` — this doc
- `Build_UV_Slider_v1.c4d`, `_v2_partial.c4d`, `_v3_partial.c4d` — earlier failed wire-surgery attempts
- `UV_Morph_Slider_FRESH.c4d` (in working dir) — current fresh-build with broken whole-array math, ready to be re-wired with the correct iteration pattern
