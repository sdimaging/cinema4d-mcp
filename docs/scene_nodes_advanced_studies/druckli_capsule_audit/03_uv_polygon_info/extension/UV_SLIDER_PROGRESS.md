# UV ↔ Flat Slider Extension — Final Progress Notes

**Goal:** Spenser asked — "would it be possible to have an animated slider to drag and slide from 3D to flat?"

**Status after 3 iterations:** Wire surgery from outside the existing capsule didn't yield a visually morphing output. The HONEST answer: this needs a fresh deformer built from primitives (Option A in the original analysis), not a wire-surgery patch.

## What was tried (3 iterations)

### v1 — direct interception of set_property.array
Added orig_pos_reader, flat_pos_reader, blend, set_morphed_pos. Wired blend.out → existing set_property.array.
**Failed because:** existing set_property is configured for `accessortype=uv`, and a capsule-INTERNAL wire `uvtomesh@.../arraybuilder.out → set_property@.../set.array` (writing data3d directly via the interior) bypasses external wires.

### v2 — added own set_property(data3d) BEFORE set_property(uv) in the chain
Wired transform_element.geometryout → set_morphed_pos.geometryin → set_morphed_pos.geometryout → set_property@Og3.geometryin → output.
**Failed because:** my set_morphed_pos's effect didn't propagate. Possibly because set_property(uv)'s capsule-internal wires re-overwrote positions back to the flat data, or the maxon evaluation picked up a different chain.

### v3 — wired set_morphed_pos.geometryout DIRECTLY to root.geometryout
Bypassed the entire downstream chain. Connected set_morphed_pos's output directly to the SN deformer's host-output port.
**Failed because:** the resulting bounds of the deformed instance stayed at the head's natural size (17×21×12) regardless of factor — meaning my morph chain's output was always the original 3D positions OR the deformer's evaluation took a different (hidden) path.

The connections check showed `root.geometryout` had TWO inputs registered (both labeled `>`) — likely both my set_morphed_pos.geometryout and the original set_property@Og3.geometryout coexisting, and the original taking precedence.

## What this proves

**The DRuckli SN capsules are sealed deeper than just "no AddChild into interior."** Even at the TOP-LEVEL of the deformer host:
1. Existing wires (especially capsule-internal-to-capsule-internal ones) bypass external interception
2. Adding a new wire to root.geometryout doesn't *replace* the existing one
3. Without `Disconnect`, we can't programmatically remove existing wires from Python

The actual evaluation of the SN graph honors the original wire mesh and ignores my additions when they conflict.

## The real path forward — Option A (fresh-build deformer)

The slider IS feasible, but requires building a completely new SN Deformer from primitives:

```
Required nodes (~10 nodes total — all probed and confirmed addable):
- get_property (×2): orig data3d, uv
- containeriteration: walks per-vertex
- net.maxon.node.access.decomposevector3d64: split UV vec3 into x, y, z
- net.maxon.node.invert: flip y
- net.maxon.node.access.composevector3d64: build flat 3D vec from (x, -y, 0)
- net.maxon.node.scale (or arithmetic with op=mul): apply scale factor
- net.maxon.node.blend: lerp(orig, flat, factor)
- set_property (data3d): writes back to output geometry
- floatingio (×2): factor + scale params
```

This is a **clean fresh-build, ~100-line scripted construction**. Not done in this iteration due to time, but the recipe and confirmed primitives are documented.

## Files in this folder

- `Build_UV_Slider_v1.c4d` — initial attempt (v1, v2)
- `Build_UV_Slider_v2_partial.c4d` — v2 chain insertion
- `Build_UV_Slider_v3_partial.c4d` — v3 with root.geometryout direct wire
- 6 viewport screenshots (factor 0/0.5/1.0 across versions) — all show same image (the original 3D head, no morph)
- This progress doc

## Next-session recipe — fresh-build deformer

```python
import c4d, maxon

doc = c4d.documents.GetActiveDocument()
# Add a new SN Deformer to the head bust instance
host = c4d.BaseObject(180420400)
host.SetName("UV Morph Slider")
# ... insert under target object ...

graph = host.GetNimbusRef(maxon.Id("net.maxon.neutron.nodespace")).GetGraph()
root = graph.GetRoot()

# Add all nodes in one transaction
with graph.BeginTransaction() as tx:
    orig_get = graph.AddChild(maxon.Id("orig"), maxon.Id("net.maxon.neutron.geometry.get_property"))
    uv_get   = graph.AddChild(maxon.Id("uv"),   maxon.Id("net.maxon.neutron.geometry.get_property"))
    iter_uv  = graph.AddChild(maxon.Id("iter"), maxon.Id("net.maxon.node.containeriteration"))
    decomp   = graph.AddChild(maxon.Id("split"), maxon.Id("net.maxon.node.access.decomposevector3d64"))
    inv_y    = graph.AddChild(maxon.Id("inv_y"), maxon.Id("net.maxon.node.invert"))
    compose  = graph.AddChild(maxon.Id("compose"), maxon.Id("net.maxon.node.access.composevector3d64"))
    scale    = graph.AddChild(maxon.Id("scale"), maxon.Id("net.maxon.node.scale"))
    blend    = graph.AddChild(maxon.Id("blend"), maxon.Id("net.maxon.node.blend"))
    set_pos  = graph.AddChild(maxon.Id("setpos"), maxon.Id("net.maxon.neutron.geometry.set_property"))
    fio_factor = graph.AddChild(maxon.Id("fio_factor"), maxon.Id("net.maxon.node.floatingio"))
    fio_scale  = graph.AddChild(maxon.Id("fio_scale"),  maxon.Id("net.maxon.node.floatingio"))
    tx.Commit()

# Configure ports + wire chain — see UV_SLIDER_PROGRESS.md for the topology
# Test factor=0/0.5/1.0; commit working result
```

This recipe is the HONEST deliverable — a confirmed-addable node-set + wiring spec ready for the next session to execute.

---

## Iteration 4 (2026-05-02) — set.iteration WEDGES C4D main thread

After cracking the granular DRuckli reference (see `UVTOMESH_GRANULAR_REFERENCE.md`), the next attempt wired the lower-level `net.maxon.neutron.geometry.set` (which DOES have the `.iteration` port) with this minimal scale-only test:

```
get_lower.array → containeriteration.in
containeriteration.out → scale.in1 (per-vertex Vec3)
scale.in2 = 2.0
scale.out → set_lower.iteration                    ← THE KEY WIRE
get_lower.topology → set_lower.topology
root.geometryin → get_lower.geometry
root.geometryin → set_lower.geometryin
set_lower.geometryout → root.geometryout
```

**Result:** C4D main thread wedged. `execute_python_script` timed out at 120s, follow-up `ping` calls timed out at 20s each. Required full C4D restart to recover.

### Hypothesis on the wedge

The `set.iteration` connection likely creates a re-evaluation loop because we wired `set.geometryout → root.geometryout` AND `root.geometryin → set.geometryin` in the same chain. If `set` references `geometryin` while computing the iterated output, and the host's geometry pipeline re-triggers on `geometryout` changes, the evaluation can recurse.

The DRuckli pattern AVOIDS this by wrapping `set` inside `geometry.op` + `filter.op` containers (the 2 extra nodes inside uvtomesh that I initially thought were just utility wrappers). Those op containers establish proper evaluation boundaries that prevent the recursion.

### Next iteration plan

1. **Add `net.maxon.neutron.op.geometry` + `net.maxon.neutron.op.filter` to the chain.** Wire `set.geometryout → geometry.geometry` (NOT directly to root). Then `geometry.output → filter.input → root.geometryout`.
2. **Test with single-pass static scale FIRST** before adding the slider — to isolate any remaining wedge sources.
3. **If still wedges:** the safer fallback is to fully replicate uvtomesh as a custom capsule via `CreateCopyOfSelection + Merge` from the original scene (the copy preserves all internal wiring including the op containers). Then add a slider on top.

### Confirmed-good asset IDs (probed this iteration)

| Asset ID | Has .iteration | Notes |
|---|---|---|
| `net.maxon.neutron.geometry.set` | ✓ YES | The lower-level set — DRuckli uses this internally |
| `net.maxon.neutron.geometry.get` | n/a | Lower-level get |
| `net.maxon.neutron.geometry.set_property` | ✗ no | The wrapper — only has .array |
| `net.maxon.neutron.geometry.get_property` | n/a | The wrapper |
| `net.maxon.node.containeriteration` | n/a | datatype port REJECTS maxon.Id("net.maxon.parametrictype.vec<3,float>") with "VALUEKIND::CONTAINER_REF" error — must auto-derive from connection |
| `net.maxon.node.readvalueatindex` / `readvalueatindex2` | ✗ NOT FOUND | Cannot add as primitive — DRuckli must have a different parallel-array consumer pattern |
| `net.maxon.pattern.node.conversion.composevector3` / `splitvectorcomponents` | ✓ | DRuckli's actual splitter/composer (matches uvtomesh internals) |
| `net.maxon.neutron.op.geometry` | not yet tested | Required for safe set.geometryout wrapping |
| `net.maxon.neutron.op.filter` | not yet tested | Required for safe set.geometryout wrapping |

### Anti-patterns confirmed

1. **Don't wire `set.geometryout` directly to `root.geometryout` when set has `.iteration`** — wedges main thread (this iteration).
2. **Don't wire whole-array math** (scale.in1 = orig_get.array, blend.in1 = orig_get.array) — silently produces empty geometry (iter 1-3 of v3.x).
3. **Don't wire dual-consumer of root.geometryin** to both a math chain and a pass-through — likely causes the original wire-surgery failures (iter 1-3 of v3.x).

---

## Iteration 5 (2026-05-02) — WORKING SLIDER ✓ via Path 2 (clone)

After iter 4's wedge, switched to wholesale-clone strategy. The WIN:

```python
# In a doc with original Build UV Preview deformer present:
clone = host_orig.GetClone(c4d.COPYFLAGS_NO_ANIMATION)
clone.SetName("UV Slider v2")
doc.InsertObject(clone, parent=parent_inst, pred=host_orig)
```

`BaseObject.GetClone()` preserves the entire SN graph including all 9 inner nodes of `uvtomesh` and its 14 internal wires — confirmed by walking the clone's graph (6 top-level / 9 inner). No Python graph manipulation required to reproduce the working pipeline.

### The slider mechanism

The exposed AM parameter on uvtomesh is `inport@PxTGkq2oDdAgGRlbBgxn7m` (a Float64). Internally it drives `scale.in2` of the per-vertex math chain — meaning it scales the entire flat layout linearly.

Sweep results (clone deformer, original disabled):

| scale | rad | description |
|---:|---|---|
| 0   | (0, 0, 0)         | flat mesh collapsed to point |
| 25  | (12.41, 12.16, 0) | half-size flat unwrap |
| 50  | (24.82, 24.32, 0) | DRuckli default (matches viewport) |
| 100 | (49.64, 48.64, 0) | 2× full-size flat |

All sub-50 values are smooth and continuous. Slider works perfectly with NO code changes — just driving the existing `inport@PxTGkq…` value.

**Visual proof:** `v4_no_deformer_3d.png` (deformer disabled = orig 3D head shown), `v4_slider_scale_000/025/050/100.png` (the UV-preview instance shrinks/expands continuously while the source 3D head stays put on the left).

### IMPORTANT — what kind of slider this is

This is a **flat-mesh-size slider, NOT a 3D↔flat morph**. The clone produces a TOPOLOGICALLY DIFFERENT mesh (4664 pts vs orig 1168 pts) because uvtomesh splits seam vertices to lay them flat. So:

- Slider can smoothly scale the flat unwrap from 0 to N (collapse → full size)
- Slider CANNOT smoothly morph between the orig 3D head shape and the flat unwrap

A true 3D↔flat morph requires a **different deformer architecture** that preserves the source topology and only changes per-vertex positions (not vertex count).

### Path forward — true topology-preserving morph

This needs a fresh SN Deformer that:

1. Reads each source vertex's UV coordinate (averaged across seams if vertex has multiple UVs)
2. Computes `flat_pos = (uv.x * scale, -uv.y * scale, 0)` per vertex
3. Reads source vertex position
4. Outputs `lerp(orig_pos, flat_pos, factor)` per vertex
5. **Same topology as input** — no seam splits

The architecture must use:
- `containeriteration` to iterate per-vertex (we proved this is needed for math nodes)
- `set.iteration` for writeback (we proved this is the bridge)
- **`net.maxon.neutron.op.geometry` + `net.maxon.neutron.op.filter` wrappers** to establish evaluation boundaries (the missing piece from iter 4 wedge)

Per-vertex UV averaging is the new puzzle: PolygonVertexValues "UVW" has 4664 entries (one per polygon-vertex) but we want one per VERTEX (1168 entries). Need to either:
- Pre-bake an averaged UV-per-vertex attribute on the source mesh (one-time setup)
- Use `componentin = "polygons"` or similar to access per-poly-vertex via the iteration index, mapping back to vertex via topology lookup

## Files in this folder (after iter 5)
- `Build_UV_Slider_v4_clone.c4d` — WORKING clone with slider via uvtomesh.inport scale (LOCAL ONLY — `.c4d` files are gitignored repo-wide; reproduce by loading the original UV-Polygon-Info_Example_01 snapshot and running the GetClone() snippet at the top of iter 5)
- `v4_no_deformer_3d.png`, `v4_slider_scale_000.png`, `v4_slider_scale_025.png`, `v4_slider_scale_050.png`, `v4_slider_scale_100.png` — visual proof
- `UVTOMESH_GRANULAR_REFERENCE.md` — full anatomy + key insights
- `UV_SLIDER_PROGRESS.md` — this doc
- (older v1/v2/v3 .c4d files + screenshots — failed wire-surgery attempts, kept as cautionary tale)

---

## Iteration 6 (2026-05-02) — sandbox probe of true position-blend deformer

Built a fresh sandbox doc with a sphere + SN deformer. Goal: get `set.iteration` working WITHOUT the wedge, to enable per-vertex morph for any deformer.

### Findings

1. **`set.iteration` requires `arraymode=False` to expose the port.** Setting `arraymode=true` (which I'd been doing because the wrapper `set_property` always uses array mode) hides `.iteration` and `.domain`. Toggling `arraymode=false` reveals them. **This was the missing unlock that made all earlier set.iteration attempts impossible to even wire.**

2. **`op.geometry` + `op.filter` wrappers prevent the wedge.** Wiring `set.geometryout → op_g.input → op_f.input → root.geometryout` with arraymode=false and set.iteration wired produces NO main-thread wedge (vs iter 4 which wedged C4D solid).

3. **But the iteration math STILL doesn't apply.** Deformer outputs the input geometry verbatim (rad=100,100,100, not 200,200,200 with scale ×2). The op chain is acting as a passthrough; the set.iteration changes are being silently dropped.

4. **`outerdomain` is NOT the aggregated post-iteration array.** Tested with `containeriteration.outerdomain → set_property.array` — produced empty geometry (rad=0). The naming was misleading; outerdomain appears to be an iteration-context value, not the result array.

5. **The "self-loops" in DRuckli's IsConnected wire trace are FALSE POSITIVES.** Tried wiring `op_g.output → op_g.input` literally — SN rejected with `"The ports form a cycle"`. So my granular reference dump's `geometry.output → geometry.input` and `filter.output → filter.input` entries are an `IsConnected` artifact, not real wires.

6. **`op.filter` has hidden internal structure (`op_f/or` child node).** These op wrappers aren't simple pass-throughs — they have inner corenodes (likely the conditional/branching logic for the filter operation). Cracking this requires walking op_f's internal graph, not just its top-level ports.

7. **DRuckli's inner `set` has unset `accessorname` and `arraymode`** (only `accessortype` set). Yet it works. This means the op container chain must inject context (which attribute to write, array vs single mode) via a mechanism that's not explicit at the set node's input ports — probably via the geometry-context flow established by op_g + op_f.

### Current architecture state

```
root.geometryin → gl.geometry           ✓
root.geometryin → sl.geometryin         ✓
gl.array → it.in                        ✓
gl.topology → sl.topology               ✓
it.out → sc.in1                         ✓ (per-vertex Vec3, sc.in2=2.0, sc.datatype=vec3)
sc.out → sl.iteration                   ✓ (the bridge, NOW VISIBLE because arraymode=false)
it.outerdomain → sl.domain              ✓ (didn't help)
sl.geometryout → op_g.input             ✓ (NOT op_g.geometry!)
op_g.output → op_f.input                ✓
op_f.output → root.geometryout          ✓ (no wedge)
```

Result: rad=100,100,100 (passthrough, scale not applied). Geometry flows through, math chain is silently bypassed.

### Hypothesis for next iteration

The op chain needs the math chain to be INSIDE its evaluation context, not parallel to it. In DRuckli, the `set` and the math nodes are inside `uvtomesh` capsule WITH the op wrappers, all sharing one evaluation context. When we put them at the top-level of our deformer alongside op_g/op_f, the ops don't see the iteration math's output — they only see the input geometry.

Possible fixes to try next:
1. **Group everything into a sub-capsule** via `graph.MoveToGroup()` so the set + math + op wrappers share an evaluation scope
2. **Walk op_f's internal `or` corenode** to understand what context flow ops actually establish
3. **Wire op_g.geometry from set.geometryout AND op_g.input from root.geometryin** simultaneously — maybe op needs both the value-to-inject AND the chain-context

For now: v4 clone (Path 2) remains the working slider deliverable. True topology-preserving morph deferred pending a session focused on cracking the op container evaluation model.

---

## Iteration 7 (2026-05-02) — TRUE 3D ↔ FLAT MORPH WORKING (Python tag)

After hitting walls in SN op-container evaluation, switched to a guaranteed-working pure-Python approach. Result: **true topology-preserving 3D ↔ flat morph slider with smooth interpolation.** Visual proof at `v5_morph_factor_000_v2.png`, `v5_morph_factor_050_v2.png`, `v5_morph_factor_100_v2.png`.

### Architecture

1. **Clone the source 3D head** (preserves topology + UV tag)
2. **Pre-compute averaged UV-per-vertex** at scene-build time:
   - Loop polygons, accumulate per-corner UV into per-vertex buckets
   - Divide by count → averaged UV per source vertex
   - This collapses the "split-vertex" problem (multiple UVs per vertex from seams → one)
3. **Cache** original positions + averaged UVs in the polygon's `BaseContainer[99999]` (sub-BC with vec3 entries indexed 0..N (orig) and 10000..10000+N (uv))
4. **Add UD sliders**: `Factor` (0-1, 3D ↔ flat) + `Scale` (0-200, flat layout size)
5. **Add a Python tag** that on every evaluation:
   - Reads cached orig pos + uv per vertex
   - Computes flat pos = `(uv.x * scale, -uv.y * scale, 0)`
   - Outputs `lerp(orig_pos, flat_pos, factor)` per vertex
   - Writes via `SetAllPoints`

### Sweep verified

| Factor | rad bounds | Description |
|--:|---|---|
| 0.0  | (17.438, 21.238, 12.499) | Original 3D head shape |
| 0.25 | (14.572, 21.843, 9.374)  | Slight flatten — Z compresses |
| 0.5  | (14.389, 22.494, 6.249)  | Halfway — Z half, sides spreading |
| 0.75 | (17.174, 23.144, 3.125)  | Nearly flat — Z thin, X/Y close to flat |
| 1.0  | (21.912, 23.795, 0.0)    | Fully flat (z=0), UV layout dimensions |

Smooth, continuous, no jumps. Same topology throughout (1168 verts).

### Why Python tag succeeded where Pose Morph + SN failed

- **Pose Morph** stored both Base 3D + Flat morphs but apparently shared underlying data references. Both `m0.Apply()` and `m1.Apply()` produced identical output (flat). Either a Store() bug or a CAMORPH_MODE configuration we didn't crack. The Apply method works directly, but the slider param isn't auto-evaluating in our setup.
- **SN op-wrapper chain** doesn't wedge but doesn't apply iteration math at the top-level deformer scope. Math runs but is silently bypassed. The op containers establish an evaluation scope that the math chain isn't entering.
- **Python tag** runs in C4D's standard expression-evaluation phase, has direct access to BaseContainer cache + UD params, and does the per-vertex math in plain Python with `SetAllPoints`. No SN evaluation context to crack, no Pose Morph delta semantics, just a function evaluating per frame.

### Files

- `Build_UV_Slider_v5_python_morph.c4d` — WORKING true morph slider scene (gitignored; reproduce via the script in this doc)
- `v5_morph_factor_000_v2.png`, `v5_morph_factor_050_v2.png`, `v5_morph_factor_100_v2.png` — visual proof of 3D ↔ flat continuous morph
- `v5_morph_factor_025.png`, `v5_morph_factor_075.png` — additional sweep stops (older layout)

### How to reproduce

```python
import c4d
doc = c4d.documents.GetActiveDocument()

def find_obj(o, name):
    while o:
        if o.GetName() == name: return o
        r = find_obj(o.GetDown(), name)
        if r: return r
        o = o.GetNext()
    return None

head_orig = find_obj(doc.GetFirstObject(), "Generic Head Bust")
mh = head_orig.GetClone(c4d.COPYFLAGS_NONE)
mh.SetName("UV Morph Head LIVE")
mh.SetAbsPos(head_orig.GetAbsPos() + c4d.Vector(70, 0, 0))
doc.InsertObject(mh, pred=head_orig)

# Bake averaged UV per vertex
n = mh.GetPointCount()
uvtag = mh.GetTag(c4d.Tuvw)
uvw_sum = [c4d.Vector(0,0,0) for _ in range(n)]
uvw_cnt = [0] * n
for poly_idx in range(mh.GetPolygonCount()):
    poly = mh.GetPolygon(poly_idx)
    uv = uvtag.GetSlow(poly_idx)
    corners = [poly.a, poly.b, poly.c, poly.d]
    uv_corners = [uv["a"], uv["b"], uv["c"], uv["d"]]
    is_tri = (poly.c == poly.d)
    for ci in range(3 if is_tri else 4):
        uvw_sum[corners[ci]] = uvw_sum[corners[ci]] + uv_corners[ci]
        uvw_cnt[corners[ci]] += 1

orig_pts = [mh.GetPoint(i) for i in range(n)]
uv_avg = [uvw_sum[i]/uvw_cnt[i] if uvw_cnt[i]>0 else c4d.Vector(0,0,0) for i in range(n)]

# Cache in BaseContainer
data_bc = c4d.BaseContainer()
for i in range(n):
    data_bc.SetVector(i, orig_pts[i])
    data_bc.SetVector(10000 + i, uv_avg[i])
mh.GetDataInstance().SetContainer(99999, data_bc)

# Add Factor + Scale UD sliders
for ud_name, ud_short, ud_min, ud_max, ud_def, ud_step in [
    ("Factor (0=3D, 1=Flat)", "Factor", 0.0, 1.0, 0.0, 0.01),
    ("Scale (flat layout)",   "Scale",  0.0, 200.0, 50.0, 1.0),
]:
    bc_ud = c4d.GetCustomDataTypeDefault(c4d.DTYPE_REAL)
    bc_ud[c4d.DESC_NAME] = ud_name
    bc_ud[c4d.DESC_SHORT_NAME] = ud_short
    bc_ud[c4d.DESC_MIN] = ud_min
    bc_ud[c4d.DESC_MAX] = ud_max
    bc_ud[c4d.DESC_STEP] = ud_step
    bc_ud[c4d.DESC_DEFAULT] = ud_def
    bc_ud[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_REALSLIDER
    bc_ud[c4d.DESC_ANIMATE] = c4d.DESC_ANIMATE_ON
    bc_ud[c4d.DESC_UNIT] = c4d.DESC_UNIT_FLOAT
    new_id = mh.AddUserData(bc_ud)
    mh[new_id] = ud_def

# Add Python tag — descIDs assumed Factor=(700,5,0)/(1,19,0), Scale=(700,5,0)/(2,19,0)
py_tag = c4d.BaseTag(c4d.Tpython)
py_tag.SetName("UV Morph (Python)")
mh.InsertTag(py_tag)
py_tag[c4d.TPYTHON_CODE] = '''import c4d
def main():
    obj = op.GetObject()
    bc = obj.GetDataInstance()
    cache = bc.GetContainer(99999)
    if not cache: return
    fid = c4d.DescID(c4d.DescLevel(700, 5, 0), c4d.DescLevel(1, 19, 0))
    sid = c4d.DescID(c4d.DescLevel(700, 5, 0), c4d.DescLevel(2, 19, 0))
    factor = obj[fid] if obj[fid] is not None else 0.0
    scale  = obj[sid] if obj[sid] is not None else 50.0
    n = obj.GetPointCount()
    new_pts = []
    for i in range(n):
        orig = cache.GetVector(i)
        uv = cache.GetVector(10000 + i)
        flat = c4d.Vector(uv.x * scale, -uv.y * scale, 0)
        new_pts.append(orig + (flat - orig) * factor)
    obj.SetAllPoints(new_pts)
    obj.Message(c4d.MSG_UPDATE)
'''

c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
```

### What we now have

1. **v4 clone slider** (commit 6979617) — flat-mesh-size slider via uvtomesh.inport scale (0 = collapsed to point, N = full flat layout). Topology-altering (4664 vs 1168 pts). Uses pure SN.
2. **v5 Python morph slider** (this commit) — TRUE 3D ↔ flat morph via per-vertex lerp. Topology-preserving (1168 verts throughout). Uses Python tag, not SN.

Two complementary approaches, both demoed and committed.

### Lessons for future SN morph attempts

- The DRuckli `uvtomesh` capsule + sister `set` + iteration pattern works flawlessly INSIDE the capsule scope — but reproducing that pattern at the top-level of a custom deformer requires understanding op container evaluation semantics that we haven't fully cracked.
- The path forward for a pure-SN morph: build the entire chain INSIDE a sub-capsule (group via `MoveToGroup`), so the math + set + op wrappers share the same evaluation scope as DRuckli's working uvtomesh.
- For now, hybrid (SN for procedural geometry generation + Python tag for per-vertex math) is the pragmatic working approach.
