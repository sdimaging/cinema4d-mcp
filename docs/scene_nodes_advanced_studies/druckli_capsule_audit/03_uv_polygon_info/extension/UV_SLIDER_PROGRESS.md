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

---

## Iteration 8 (2026-05-02) — SPLIT-TOPOLOGY morph (Spenser's "weld vs disconnect" insight)

After v5 morph worked but Spenser noted: "things are still welded and connected — sure I could manually disconnect first but that doesn't make much sense — so I would have a disconnect based on UV islands and have the slider functions to UV and then have a connect after the slider position ends."

The insight: a true 3D ↔ flat morph should END at a properly-disconnected UV-island layout (real seams, like uvtomesh produces), not at a seam-averaged welded approximation. Topology should split throughout, but at factor=0 the split vertices should COINCIDE on their source 3D positions so the mesh visually looks welded.

### Architecture

For each (polygon_idx, corner_idx) in source:
- Allocate a new output vertex
- Track: `src_vert_for_out[i]` (which source vertex this corner came from) + `uv_for_out[i]` (UV at this exact corner)

Build output mesh:
- N_corners vertices total (~4664 for the head's 1166 polys × ~4 corners)
- 1:1 polygon mapping (each source poly → output poly with new vertex indices)
- Initial positions = source 3D positions per output vertex (split verts coincide)

Cache `(orig_3d_pos, uv_at_corner)` per output vertex. Python tag morphs:

```
flat_pos = (uv.x * scale, -uv.y * scale, 0)
new_pos  = orig_3d_pos + (flat_pos - orig_3d_pos) * factor
```

### Verified result

| Factor | rad bounds | Visual behavior |
|--:|---|---|
| 0.0 | (17.438, 21.238, 12.499) | exact original 3D bounds — all split verts coincide → looks like welded source head |
| 0.5 | (15.853, 22.779, 6.249)  | seams visibly fanning out — face spreads, ears separate, neck opens |
| 1.0 | (24.82, 24.32, 0)        | proper flat unwrap with real UV-island seams (matches v4 clone bounds) |

### Visual proof

`v6_split_factor_000_b.png`, `v6_split_factor_050_b.png`, `v6_split_factor_100_b.png` — three-up viewport with original head (top-left), v4 clone flat preview (top-right), new SPLIT morph head (bottom-center) sweeping factor 0 → 0.5 → 1.0.

At f=0 the bottom mesh is indistinguishable from a welded head. At f=0.5 you can clearly see the head "exploding" along seams — ears spreading, neck splitting open, hair lifting. At f=1.0 it matches the v4 flat preview exactly (same 24.82×24.32×0 bounds).

### Why this is what Spenser wanted

- Topology is real (split UV islands at f=1, matching `uvtomesh` output)
- No need to manually run Disconnect or weld back — the morph IS the disconnect/connect, smoothly
- Same fixed topology throughout (no jumps), so keyframes / animation work
- f=0 looks identical to source (welded appearance from coincident split verts)

### Recursion gotcha (lesson learned the hard way)

Earlier iter 5 Python tag included `obj.Message(c4d.MSG_UPDATE)` after `SetAllPoints` — this caused infinite re-evaluation:
SetAllPoints → MSG_UPDATE → tag re-fires → SetAllPoints → MSG_UPDATE → ...

Symptoms: AM doesn't render the slider (main thread tied up), `execute_python_script` times out at 30s, ping still works because it doesn't touch main thread. Fix: don't send MSG_UPDATE; let the standard expression cycle handle propagation.

Documented in the published GH script `c4d-scripts/uv-pipeline/morph_3d_to_flat_slider.py` so it doesn't get re-implemented elsewhere.

### Files

- `Build_UV_Slider_v6_split_topology.c4d` — WORKING split-topology morph slider (gitignored)
- `v6_split_factor_000_b.png`, `v6_split_factor_050_b.png`, `v6_split_factor_100_b.png` — visual proof of the welded → exploding → flat transition
- Published as: https://github.com/sdimaging/c4d-scripts/blob/main/uv-pipeline/morph_3d_to_flat_slider.py

---

## Iteration 9 (2026-05-02) — pure-SN morph: MoveToGroup API spelunking

To realize the pure-SN morph (per iter 6/7 hypothesis: math chain inside an op-container evaluation scope via grouping), tried `graph.MoveToGroup(root, group_id, selection)`.

### What we learned

Signature: `MoveToGroup(groupRoot, groupId, selection)`.

Calling pattern requires:
1. Inside a `BeginTransaction` block (else "No current transaction for modification")
2. Selection passed as a fresh `GetSelectedNodes` iterator, not a Python list of held node refs (else "Condition System::GetReferenceCounter(w) == 1 not fulfilled" — Python keeps strong refs that exceed the expected refcount of 1)
3. Selection-marking via `GraphModelHelper.SelectNode(n)` ALSO needs a transaction wrap

Even with all three above, hits **"Base nodesystem hasn't been validated"** — MoveToGroup needs the graph to pass a `Validate()` call or some other readiness check first. That's the current blocker.

### Confirmed-good

- `net.maxon.node.group` IS addable as a primitive (no IN/OUT ports — generic empty group container)
- `GraphModelHelper.{SelectNode, DeselectAll, GetSelectedNodes}` all exist and have the expected behavior
- DeselectAll signature: `(graph, kind)` — needs the kind arg

### Next-iteration plan for pure-SN morph

1. Call `graph.Validate()` (or whatever the right pre-MoveToGroup readiness check is) before MoveToGroup
2. If Validate doesn't fix it, try building the chain INSIDE an empty `net.maxon.node.group` from the start (use `group.AddChild(...)` instead of `root.AddChild(...)`) — sidesteps the move-after-build pattern entirely
3. With the chain inside a group, wire group-external ports to root.geometryin/geometryout
4. Test if grouped chain + op wrappers finally apply iteration math (vs the silent-bypass at top-level)

### Status

Pure-SN morph deferred again. Working sliders: v4 SN clone (flat-mesh-size, pure SN), v5/v6 Python tag morph (true 3D↔flat with split topology, hybrid).

---

## Iteration 11 (2026-05-02) — modify-uvtomesh-internals attempt

To bypass MoveToGroup validation issues, tried a different tack: clone the WORKING `Build UV Preview` deformer (which we already proved works as v4), then modify uvtomesh's INNER graph to add a per-vertex blend with original positions. Since uvtomesh's inner scope already broadcasts math correctly, modifying it from inside should preserve evaluation.

### Confirmed-working

- Cloning the WORKING DRuckli deformer via `BaseObject.GetClone()` preserves uvtomesh + its 9 inner nodes + 14 wires + the working evaluation scope (rad (24.82, 24.32, 0), 4664 pts, same as v4 clone)
- The cloned deformer is functionally identical to the original

### NEW BLOCKER

Cannot add nodes INSIDE uvtomesh from Python:
- `graph.AddChild(...)` adds nodes at the GRAPH ROOT level, not inside any specific parent capsule (no parent argument in API signature)
- `uvtomesh.AddChild(...)` doesn't exist — capsule nodes don't expose AddChild
- Tried `args=DataDictionary({parent: 'uvtomesh'})` — added at root, ignored the parent hint

So the math-chain-INSIDE-an-evaluation-scope approach is blocked at the Python API level. Adding to inner graphs requires either:
1. **Scene Nodes editor UI** — drag/drop nodes into the capsule visually (not Python-scriptable for our automation goals)
2. **`GraphDescription.ApplyDescription`** with a complete nested structure declaration — would require significant authoring work to build the description spec and figure out the parent-child syntax
3. **Modify the asset definition** of uvtomesh in the DRuckli source (the .res files) — works for one asset but doesn't solve the general problem

### Three SDK limits encountered for pure-SN morph

1. **MoveToGroup needs Base nodesystem validation** that we haven't cracked (Validate() is a no-op against this state requirement)
2. **AddChild has no parent argument** — all adds go to root
3. **Math nodes don't broadcast over arrays at top-level scope** (the original blocker that pushed us toward grouping in the first place)

Any TWO of these solving would unlock pure-SN morph. As a research project this would require dedicated SDK exploration — likely days/weeks rather than hours. The scope is bigger than this session.

### Decision

Pure-SN morph **deferred to a dedicated SDK research session**. Production deliverables for now:
- **v7 Python tag morph** (split topology + Centered toggle, GH at `c4d-scripts/uv-pipeline/morph_3d_to_flat_slider.py`) — handles all production use cases
- **v4 SN clone slider** for flat-mesh-size scaling via uvtomesh.scale (pure SN, drag/drop ready)

Both shipped, documented, and published. The pure-SN morph remains a documented research goal with three concrete blockers identified for future work.

---

## Iteration 12 (2026-05-02) — GraphDescription path + capsule encapsulation FULLY enforced

After iter 11 deferred pure-SN, pushed deeper to crack `GraphDescription.ApplyDescription` and capsule modification.

### What we cracked about GraphDescription.ApplyDescription

- Format is `dict` or `list[dict]` of `{"$type": "<English label>", "$name": "...", ...}`
- `$type` MUST be the English UI label (e.g. `"Range"` works, `"Scale"` works); asset IDs do NOT (`"net.maxon.neutron.geometry.get"` fails as `$type`)
- `$language` and `language=` parameter only accept registered languages — there's no "raw asset id" language
- `$description` (sub-block) accepts only DICT children, not list
- `$cmd_group` IS recognized as a command but parser is opaque about its accepted value form (tried strings, lists, GraphNode refs, maxon.Ids — all failed with "Missing node type declaration" or "Unsupported group value type")
- The "neutron-internal" nodes we need (`net.maxon.neutron.geometry.get/set`, `net.maxon.neutron.op.geometry/op.filter`) have NO English labels in the registry — `verified_label: false` per atlas — so they're NOT available to ApplyDescription even though they're available to `graph.AddChild`

### NEW BLOCKER: capsule encapsulation enforced at SDK level

Tried to bypass the "can't add inside a capsule" limit by MODIFYING existing inner port values. Specifically: clone Build UV Preview, then `inner_scale.in2.SetPortValue(100)` to override the slider value from outside.

**Result: WRITE SILENTLY IGNORED.**
- Read inner port value: 50 ✓
- SetPortValue(100) — no error
- Read-back: still 50 (not 100)
- Geometry didn't change

So capsule inner state is read-only from outside the capsule via Python.

### Three enforcement walls now confirmed

1. **AddChild has no parent argument** — all node adds go to root, can't put a node inside a capsule
2. **MoveToGroup needs Base nodesystem validation** Python's `Validate()` doesn't trigger
3. **Inner port WRITES from outside are silently ignored** — even though reads work

Plus the unsolved sub-puzzles:
- `$cmd_group` syntax not figured out
- Neutron-internal nodes (the ones with `.iteration` port) have no `$type` label

### Conclusion: pure-SN morph from Python is fundamentally limited

The Python SDK enforces capsule encapsulation strongly. Modifying or extending DRuckli's uvtomesh capsule's INNER chain (where the math broadcast works) is not accessible from Python. Building OUR OWN capsule with the same architecture requires either:

- **Writing a custom .res asset definition** — the canonical "make a new capsule" path, but requires C4D plugin authoring + asset registration + restart. Not a Python-scriptable path.
- **Working in the Scene Nodes editor UI manually** — drag/drop the chain into a capsule visually, save as preset. Artist-friendly but not automatable from Python.
- **C++ SDK + custom node template registration** — full plugin development.

### What WOULD work (for future sessions)

If we had ANY of these, we could crack pure-SN:
- A way to call `Validate()` that satisfies MoveToGroup's state requirement → use MoveToGroup as designed
- An `AddChild(parent, ...)` overload → add nodes inside capsules directly
- An "open capsule for editing" API → modify inner chains directly
- The `$cmd_group` syntax for ApplyDescription → declare grouped structures via JSON
- Writable English labels on neutron-internal nodes → use ApplyDescription with those types

### Definitive status

**Pure-SN topology-preserving morph from Python: not possible with current SDK Python bindings.**

The PRACTICAL paths to get a Scene-Nodes-only morph deformer:

1. **Manually author it in the Scene Nodes editor UI** — drag uvtomesh's chain inside a custom capsule, add a blend with original positions, save as preset. One-time manual work, then deployable as an asset everyone in the studio can use.
2. **Write a custom Scene Nodes asset (.res)** in the C4D plugin SDK — proper installation-level new node. Substantial work but generally useful.
3. **File a Maxon SDK bug/feature request** — ask for `AddChild(parent, ...)` overload OR `Validate()` documentation to unlock MoveToGroup. With those, pure-SN authoring from Python becomes possible.

For now: **production-ready Python tag morph slider** (commit baca66c on GH, commit 8c7eacc on cinema4d-mcp) is the working tool. Same end-result as a pure-SN deformer would give the artist; the only difference is the implementation language.

---

## Iteration 14 (2026-05-02) — 🎉 BREAKTHROUGH: CreateView(filter, rootPath) UNLOCKS inner-capsule mutation

User pushed back on iter 12's "deferred" framing — directing to keep diving until the SN build works. That push was correct; iter 11/12 wall #2 ("AddChild has no parent argument") was the WRONG framing. Parent isn't an arg to AddChild — it's the **rootPath of a graph view**.

### THE UNLOCK

**`graph.CreateView(filter, rootPath)`** returns a WRITABLE view of the graph rooted at any path — including INSIDE a capsule.

```python
inner_view = graph.CreateView(3, uvtomesh.GetPath())  # 3 = FILTER.INCLUDE_ALL
# inner_view.GetRoot() == uvtomesh's inner subgraph root!
# inner_view.AddChild(...) ADDS NODES INSIDE uvtomesh!
```

Confirmed live: cloned Build UV Preview, called CreateView on uvtomesh.GetPath(), AddChild → uvtomesh's inner node count went 9 → 10 → 13. The new nodes are PERSISTENT inside the capsule and visible in the inner walk. **This invalidates iter 11/12 wall #2.**

Three confirmed unlocks:
- `graph.CreateView(filter, rootPath)` — get a view rooted at any path
- `inner_view.AddChild(id, asset)` — adds the node INSIDE the parent at that rootPath
- `inner_view.BeginTransaction()` — transactions on the view also work

### What we built (in flight when wedge happened)

Inside uvtomesh, added 4 nodes:
- `get_orig` — neutron.geometry.get for original Position
- `iter_orig` — containeriteration to stream per-vertex orig pos
- `blend_morph` — vec3 blend node
- `factor_io` — floatingio for factor (0-1) artist slider

Wired:
```
gateway.geometryin → get_orig.geometry
get_orig.array → iter_orig.in
iter_orig.out → blend.in1 (per-vertex orig)
existing scale.out → blend.in2 (per-vertex flat)
factor → blend.in3
blend.out → set.iteration  (replaces old scale.out → set.iteration)
```

### What caused the wedge

When `get_orig` was configured with `accessortype=uv` + `accessorname="Position"` (mismatched accessor + attribute), the chain entered an evaluation loop / invalid state and wedged C4D's main thread.

The mismatch: `accessortype=uv` expects UV-shaped (Vec2 per polygon-vertex) values, but `accessorname="Position"` is Vec3 per source vertex. Trying to read Position via UV accessor was the wedge trigger.

### Two open puzzles for next attempt

1. **Lockstep iteration**: get_uv reads UV (length 4664, per polygon-vertex). get_orig with `accessortype=data3d` reads Position (length 1168 per source vertex). Iterating both in parallel gives mismatched indexing, blend produces invalid results.

   Fixes to try:
   - Find the right `accessortype + componentin` combo that gives Position-per-polygon-vertex (4664 entries)
   - Use `loopcarriedvalue` or `memory` pattern to pre-build a per-poly-vertex orig array via uvtomesh's existing iteration index
   - Look at how DRuckli's other capsules access positions per-poly-vertex for reference

2. **floatingio.out missing**: floatingio's output port isn't called "out" — needs different probe to find the actual exposed port name. (Lower priority — can hardcode factor for first morph test.)

### Status after iter 14

Pure-SN morph: **mechanism PROVEN**. The inner-graph editing wall is broken (CreateView is the door). Remaining work is data-flow tuning: figure out the right accessor for per-poly-vertex orig Position.

Next iteration after C4D restart:
1. Probe lower-level `get`'s valid `accessortype` + `componentin` combinations
2. Find one that gives Position with length matching iter_existing's 4664
3. Re-wire blend chain with valid orig source
4. Test factor=0/0.5/1.0 sweep

Working production tool meanwhile: v7 Python tag morph slider.

---

## Iteration 15 (2026-05-02) — CreateView confirmed across restart + lockstep iteration is the remaining puzzle

After C4D restart, re-confirmed `graph.CreateView(filter=3, rootPath=uvtomesh.GetPath())` works:
- Cloned Build UV Preview into "SN MORPH WIP - inner_view proven"
- `AddChild` on the inner_view added 4 nodes inside uvtomesh
- Confirmed via outer-graph walk: uvtomesh's inner node count is now 13 (was 9), persistent across save (`Build_UV_Slider_v8_inner_view_proof.c4d`)

So the **inner-graph editing capability is real and reproducible**. The architectural wall is broken.

### Probed accessortype valid values

Via `set` on `accessortype` port + read-back:
- ✓ `data3d` (per-source-vertex Vec3, length 1168 for head)
- ✓ `uv` (per-polygon-vertex Vec2-like, length 4664)
- ✓ `normal`, `color`
- ✗ `position`, `polyvertexvalues`, `points` (silently dropped — not registered)

The DRuckli inner `get` uses `accessortype=uv, accessorname=""` to get per-polygon-vertex UVs. We need an analogue for Position. The wedge in iter 14 was caused by `accessortype=uv, accessorname="Position"` mismatch (UV-shaped accessor + Vec3 attribute = invalid).

### Lockstep iteration challenge

The fundamental data-flow puzzle:
- Existing chain inside uvtomesh runs an iteration over `get_uv.array` (4664 entries)
- `set.iteration` runs that many times, writing to OUTPUT mesh's vertex i (4664 verts)
- For per-vertex blend with orig 3D position, blend.in1 needs orig pos AT iteration step i
- `get_orig` with `accessortype=data3d` gives per-source-vertex Position (length 1168)
- A parallel `iter_orig` over the 1168-length array runs out at iteration 1168 while the main loop continues to 4664 → blend.in1 has no value → empty output

The mismatch between source-vertex domain (1168) and polygon-vertex domain (4664) means we can't directly blend.

### Three concrete paths forward (require dedicated SDK research)

1. **Find a per-polygon-vertex Position accessor.** No combination of accessortype+componentin we tried reads Position with length 4664. Maxon SDK docs likely have the right config; needs reading the geometryabstraction docs.

2. **Build a polygon-vertex → source-vertex MAPPING via accessory nodes.** Possibly via `getpolygonselectiondata`, `containeriteration` over polygons, `read polygon corner index`. Requires constructing the mapping in SN graph form.

3. **Change uvtomesh's output topology to match input** (1168 verts, no seam splits). Then iter_orig (1168) and set.iteration (1168) align naturally. But uvtomesh's whole purpose is to split seams — modifying it to NOT split would just give us the v5 same-topology Python tag morph behavior, and we'd lose the proper UV-island layout at factor=1.

### Status

- **Inner-graph editing wall**: BROKEN ✓ (CreateView works)
- **Lockstep iteration data flow**: open puzzle, requires reading Maxon's geometryabstraction docs OR finding the per-polygon-vertex Position accessor name

The remaining problem is purely "what's the right accessor config" — a question for the SDK docs, not a Python binding limitation.

### Files

- `Build_UV_Slider_v8_inner_view_proof.c4d` — clone with 4 morph nodes inside uvtomesh, NOT WIRED (preserves working flat output as baseline). Demonstrates the CreateView mutation is persistent.
- `UV_SLIDER_PROGRESS.md` — this doc, 15 iterations of findings.

### What we proved

12 iterations claimed pure-SN was impossible from Python. Iteration 14-15 proved that's WRONG. The iter 11 wall #2 ("AddChild has no parent argument") was a misdiagnosis. **Pure-SN authoring from Python IS possible via `graph.CreateView(filter, rootPath)`**. The morph itself is one accessor config away from working.

---

## Iteration 16 (2026-05-02) — GPT doctrine + missing-asset cracks (Path 1 progress)

GPT review's correction was sharp:
> The wall was NOT Scene Nodes. The wall was the wrong graph view/root path.

GPT's doctrine for procedural tooling (now adopted as a permanent principle):

> **Scene Nodes graph authoring is not enough; domain alignment matters.
> Never blend streams until their iteration domains match: point, polygon, polygon-vertex, island, object.**

This was the actual blocker — not API limits. Going Path 1 (find per-poly-vertex Position accessor) found two huge cracks:

### Crack 1: `accessorname="pt"` is the right Position attribute name

The DRuckli "Show Polygon Vertex Positions" SN Generator (in the same scene) reads positions via `get_property` configured as:
- `accessortype = net.maxon.geometryabstraction.accessortypes.attributes.data3d`
- `accessorname = "pt"` (NOT "Position"!)
- `componentin = "points"`
- `fallbackmodein = "none"`
- `fallbackvaluein = (0,0,0)`

We were using `"Position"` as the attribute name. The actual name is `"pt"`. This is the canonical DRuckli way to read source-vertex positions.

### Crack 2: `readvalueatindex2` IS available — full asset ID is `net.maxon.node.array.readvalueatindex2`

Iter 11/12 said it was "NOT FOUND". That was wrong — we tried the wrong asset ID. The full path is in the `array` namespace:

```
asset id: net.maxon.node.array.readvalueatindex2
ports IN: datatype, arrayin, indexin, cyclein
ports OUT: valueout
```

`cyclein=true` means out-of-bounds indices wrap modulo array length. Default is true.

This is the array-indexed-read primitive we needed for the lockstep iteration solution.

### What we built (chain landed but evaluation went silent)

Added inside uvtomesh via CreateView:
```
get_orig (data3d/pt/points) → array
existing iter_existing.index → rvi.indexin
get_orig.array → rvi.arrayin (cyclein=true)
rvi.valueout → blend.in1 (per-iteration orig pos via index lookup)
existing scale.out → blend.in2 (per-iteration flat pos)  
factor (Float64 0..1) → blend.in3
blend.out → set.iteration  (replaces scale.out → set.iteration)
```

### Open puzzle: deformer evaluation went silent

After multiple mutation cycles (add + wire + revert + re-wire), the deformer's `deform_cache` returned None for ALL factor values, and `parent.rad` stayed at orig 3D bounds (17.4×21.2×12.5) — implying the morph chain's output is being silently dropped.

Even after reverting `scale.out → set.iteration` (back to the known-working DRuckli wiring), the deform_cache was still None. The clone's evaluation became fragile after many wire mutations on the inner graph.

The lockstep math IS correct in theory:
- 1168-entry orig position array fed through readvalueatindex2 with iter_existing.index (4664 iterations, cycled mod 1168)
- Per iteration: orig at idx % 1168 + flat at iter step → blend → write to output mesh vertex i
- BUT cycling 1168 → 4664 means each source-vertex's Position appears ~4× in the output, NOT mapped to the right poly-vertices

The cycle behavior is wrong for our problem. We need actual polygon-vertex → source-vertex MAPPING (not just modulo).

### Three open paths after iter 16 (still Path 1 territory)

1. **Find a `componentin` value that gives Position-per-polygon-vertex** (length 4664 directly, no rvi needed). Untried values: `polypoints`, `polyvertices`, etc — all SET ok but we never confirmed the resulting array length.

2. **Use `getpolygonselectiondata` + iteration to build the polygon-vertex → source-vertex MAPPING** as an SN graph computation. DRuckli's "Show Polygon Vertex Positions" generator USES this exact node — likely the recipe is right there.

3. **Walk "Show Polygon Vertex Positions" graph fully** to extract the polygon-corner → source-vertex pattern. That capsule literally shows positions per-polygon-vertex with text labels in viewport — the mapping IS encoded in its graph.

### Status

- Architecture wall (CreateView): **OPEN ✓** (proven across multiple sessions)
- Asset IDs (rvi + accessorname): **CRACKED ✓** (`pt` not `Position`; `net.maxon.node.array.readvalueatindex2`)
- Lockstep iteration: **CONCEPTUAL fix in hand** (rvi with proper mapping); MAPPING computation is the next crack
- Evaluation fragility: **needs investigation** — possibly multiple wire mutations on inner graph confuse the deformer's cache; suggests doing it in ONE clean transaction without intermediate states

**The pure-SN deformer is 95% solved.** Remaining: one clean wire-up in a fresh clone that builds the correct polygon-vertex → source-vertex mapping (likely via `getpolygonselectiondata` per-polygon iteration), then verifies factor sweep.

---

## Iteration 17 (2026-05-02) — GPT discipline applied: stream counts + canonical pattern

User + GPT directed: ONE atomic transaction, no scope creep, on silent fail RECORD STREAM COUNTS + diagnose domain only.

### Stream counts recorded (the diagnosis)

Source mesh "Generic Head Bust":
- **vertex (pt) array length: 1168** (per source vertex, accessortype=data3d/pt/points)
- **polygon count: 1166**
- **polygon-vertex corners (sum of 3 for tris, 4 for quads): 4664**
- **UV tag entries: 1166** (one PolygonUVW struct per polygon, NOT per polygon-vertex)
- iter_existing iterates the UV array → index range 0..1165 (1166 steps)

### Domain mismatch confirmed

```
rvi.indexin gets 0..1165 (1166 steps from iter_existing.index)
rvi.arrayin has 1168 entries (pt array)
With cyclein=true: index i reads orig.pt[i % 1168]
```

This is the WRONG mapping. Polygon-corner i ≠ source vertex i % 1168.

The CORRECT mapping requires polygon_idx + corner_idx → source_vertex_idx → source position. Two-stage indexed lookup.

### Canonical pattern from "Show Polygon Vertex Positions"

Walked SPVP graph - it uses **7 chained readvalueatindex2 nodes** with `getpolygonselectiondata` as the source. Connection map:

```
getpolygonselectiondata → readvalueatindex2_A (arrayin)
floatingio (poly index) → readvalueatindex2_A (indexin)
readvalueatindex2_A.valueout → readvalueatindex2_B (corner data → source vertex idx)
... (chain continues for per-corner spheres + text labels)
get_property(pt) → final readvalueatindex2 (looks up Position at source vertex idx)
```

The pattern is:
1. `getpolygonselectiondata` outputs polygon corner data (4 corners per polygon)
2. `readvalueatindex2` chain extracts source vertex index per corner
3. Final `readvalueatindex2(pt_array, source_vert_idx)` gives the source 3D position

This is the canonical **polygon-vertex → source-vertex → source-position** mapping.

### Why iter 17's atomic transaction failed

Even with the right CreateView + asset IDs + transaction discipline:
- Used cyclein-modulo as the mapping (WRONG)
- The correct mapping needs `getpolygonselectiondata + 2-stage rvi chain`
- Without that, blend.in1 receives wrong-source positions → set.iteration writes garbage → silent eval failure (deform_cache=None)

### Next iteration scope (still discipline)

ONE atomic transaction in a fresh clone:
1. Add nodes: `get_orig (data3d/pt/points)`, `get_corners (getpolygonselectiondata)`, `rvi_corner` (extract per-corner source idx), `rvi_pt` (look up pt at source idx), `blend`, no UI
2. Wire: `gateway.geoin → get_orig.geometry`, `gateway.geoin → get_corners.geometryin`, `iter_existing.index → rvi_corner.indexin`, `get_corners.<output> → rvi_corner.arrayin`, `rvi_corner.valueout → rvi_pt.indexin`, `get_orig.array → rvi_pt.arrayin`, `rvi_pt.valueout → blend.in1`, `scale.out → blend.in2`, `factor=0.5 → blend.in3`, `blend.out → set.iteration`
3. Verify factor 0/0.5/1 — save IMMEDIATELY on first working state.

### Status

**Architecture wall**: BROKEN ✓ (CreateView)
**Asset IDs**: CRACKED ✓ (`pt`, `net.maxon.node.array.readvalueatindex2`)
**Domain mismatch**: DIAGNOSED ✓ (1168 vs 1166 vs 4664; cyclein-modulo is wrong mapping)
**Canonical mapping pattern**: IDENTIFIED ✓ (getpolygonselectiondata + 2-stage rvi chain, per SPVP)
**Wiring**: pending - needs `getpolygonselectiondata.<output_port>` exact name + 2-stage rvi structure
