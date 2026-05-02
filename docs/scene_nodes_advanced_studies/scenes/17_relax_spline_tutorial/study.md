# Scene 17 — Relax Spline (Tutorial)

**Studied:** 2026-05-01
**Source:** `Relax-Spline_01_Tutorial/Relax-Spline_01_Tutorial.c4d`
**Target-recipe status:** TOP PRIORITY — flagged by Spenser as a target template for "spline-based reaction diffusion ON the surface of an object, contained within an extruded logo."

---

## TL;DR

A reaction-diffusion-style spline grows inside a containment volume (the extruded letter "3"), filling its interior over time without ever escaping the boundary. The visible "growth" is real and time-driven (the SN graph reads `context_externaltimeinput`), but the rooted 64-pt `Spline` is static — it is either a baked final state or a guide path. The math machinery is a 23-node Scene Nodes Deformer that performs a bounding-box → target-frame axis-remap with selection-scoped point transport.

This scene is the proof-of-concept for a capability we want to build: **logo-fillable RD growth** — drag any extruded shape in as the boundary, and watch a procedural curve grow to fill it.

---

## Spenser's annotations (verbatim)

> the node layout here is gorgeously done

> this is that reaction diffusion style spline growth that i LOVE

> this does reaction diffusion within a contained space, the extruded number 3 is called Collision in the viewport bc its the contained colision of the reaction diffusion growth

> this is a key build bc if we want to do a spline based reaction diffusion build ON the surface of an object and contain it within something like an exruded logo etc this would be the kind of scene wed want to engineer

These are architectural claims: (a) the layout is reference-quality, (b) the renamed "Collision" object is the **boundary**, not a deformation modifier, (c) the system is generalizable to any extruded logo, (d) we want to ship this as a capability.

(Note: the rename of Extrude → "Collision" is an operator-style quirk by the original artist, not a methodology rule we should adopt or generalize.)

---

## Object hierarchy

```
Landscape         (Landscape primitive 5169)        — substrate, 887x192x887, 100x100 subdivisions, cached to 101x101 grid
Collision         (Extrude 5116, RENAMED)           — containment volume; extrudes Text Spline by Vec(0,0,100)
├─ Text Spline    (Text Spline 5178)                — letter "3", height 920.8 — the boundary shape source
└─ Geometry Axis  (Scene Nodes Deformer 180420400)  — the math + time machinery; deforms the extrude output
Spline            (SplineObject 5101)               — STATIC 64-pt open polyline; baked final state OR guide path
```

Tags: only Phong on Landscape + Collision; Spline carries `Tnotes(5617)` + `Tpointselection(5600)` (the selection probably encodes growth tips).

**No CTracks anywhere. No XPresso tags. No Fields at doc level. No hidden objects.** All animation is driven internally by the SN graph reading `context_externaltimeinput`.

The two materials in the doc (`Standart`, `RS`) are **unused** — neither Landscape nor Collision has a texture tag. The visible color comes from somewhere else (likely a vertex color attribute or shader applied at draw time).

---

## Visual progression (top-down + perspective)

| frame | observation |
|------:|-------------|
| 0 | Landscape gray slab; "3" extrude visible as halftone-dotted alpha cutout punching the surface; a SHORT colored spline ribbon visible top-right of the 3 |
| 30 | Spline ribbon longer, beginning to wind |
| 60 | Spline ribbon coiling within the upper bulb of the 3 |
| 83 / 90 | Spline ribbon CONVOLUTED, near-fills the entire 3 interior — never escapes the boundary |

See `frames/f0000_persp.png` → `frames/f0090_persp.png` for the perspective sweep, and `frames/live_now.png` for the top-down at frame 83 — the top-down is the diagnostic camera (per gotcha #58: flat-output scenes need top-down).

---

## Scene Nodes graph — the 23-node Geometry Axis deformer

**Purpose** (inferred): bounding-box → target-frame axis-remap with selection-scoped point transport, time-driven via `context_externaltimeinput`.

**Top-level children (11 plus 2 ports):**

```
context_externaltimeinput   — TIME source (the one the classifier missed)
context_notime              — graph machinery
composematrix #1            — SOURCE basis, with _0/_1 floatingio routing helpers
bb (bounding box)           — extracts min/max/size/center of input geometry
group                       — normalizes per-axis position (maprange ×3 + splitvectorcomponents ×2 + composevector3)
transform_element           — the per-vertex transform; contains transformpoint + selectionstringparser
composematrix #2            — TARGET basis, with _0/_1 routing helpers
transformvector ×3          — apply matrix to the three basis vectors
inversematrix               — SOURCE^-1 for round-trip space conversion
```

**Function-class distribution:** math_compound 47.8%, framework 8.7%, selection_ops 4.3%, visual 4.3%. Loop-carried-state count = 0. Stochastic = 0. Time_state = 0 (misleading — see note below).

**Classifier blind spot:** the classifier reports `time_state: 0`, yet visible animation is happening. The animation is driven by `context_externaltimeinput`, which the classifier does not count as a "time node". We should add this to our gotchas list — `context_externaltimeinput` IS a time source even though it appears as a graph context rather than a math primitive.

**AM exposure:** ZERO. The deformer's BC dump shows only display-color slots (10000, 1041671, 1041667, 1036147). All graph parameters are internal. The `_0` / `_1` floatingio routing nodes inside each `composematrix` route values graph-internally but do not surface to AM. This is a deliberately "closed" deformer asset — Spenser called the layout "gorgeously done" partly because the artist hid all complexity.

---

## Open questions (carry into scene 18)

1. **What is the visible animated colored ribbon?** Not the rooted Spline (static, 64pts confirmed). Not the Extrude cache mesh (that's the dotted "3" outline). Possibilities: a sub-cache produced by the SN deformer; a separate SplineObject output via the deformer's downstream; a PLA cache on a generator I missed.
2. **How does volume-containment actually clamp the growth?** The bbox math gives a frame, but the growth never leaves the 3. There must be a containment test — point-in-polygon, winding-number, or selection lookup. The `selectionstringparser` node strongly hints at the latter.
3. **What does the rooted Spline (64 open pts) do?** Static throughout the timeline. Roles: (a) baked final-state reference, (b) name-referenced guide curve, (c) input to a Sweep/Profile not present here.
4. **What does the `Tpointselection` on the Spline encode?** Worth opening manually — likely growth-tip markers.
5. **What is tag type 5604 on the Landscape cache?** Unknown.
6. **Active renderer ID is 300001061** — third-party, not Standard / Physical / Octane / Redshift-classic. Needs identification.

These all carry forward into the scene 18 (Optimized) study, which probably exposes the loop-carried-value as a Memory primitive and resolves several of the above.

---

## Recipe candidates extracted

### R28 — Contained reaction-diffusion spline growth ON a surface, bounded by an extruded shape (TARGET)

**Priority:** TOP. Spenser explicitly named this as a capability we want to engineer.

**Ingredients:**
- Substrate object (Landscape / Plane / any UV'd polygon mesh)
- Containment volume: Extrude generator wrapping a Text Spline / Spline / Logo Spline
- Scene Nodes Deformer as a child of the Extrude — provides the bbox→target-frame math + selection-scoped transport
- Time-driven RD step via Memory primitive feedback INSIDE the SN graph (TODO: confirm vs scene 18)
- Optional: separate guide Spline read by name for initial seed path

**Key techniques:**
- bb + composematrix ×2 + inversematrix → SOURCE⁻¹ × TARGET axis remap
- selectionstringparser + transform_element → scope deformation to a named selection (probably "inside the volume")
- Drive per-frame change via `context_externaltimeinput` — even small deformations accumulate into the RD look
- Bake the final state to a static SplineObject for reuse / export

**Conceptual operations (A→Z) — implementation-agnostic:**
1. Establish a CONTAINMENT volume from any extruded shape
2. Establish a SUBSTRATE surface where growth lives
3. Provide a per-vertex/per-point COORDINATE TRANSFORM from substrate-space into containment-space (so we can ask "is this point inside the volume?")
4. Maintain a TIME-EVOLVING STATE (the growing curve) — requires loop-carried-value
5. Apply a CONTAINMENT TEST each step to clamp growth inside the volume
6. Visualize the state as a renderable spline / mesh / vertex attribute

**Implementation paths — pick by use case (the reference build scene is ONE option):**
- *Pure Scene Nodes:* what scene 17 does — bbox+matrix axis-remap + transform_element + (somewhere) Memory feedback
- *Hybrid:* SN deformer for the math + classic Field/MoGraph for the iteration + Sweep generator for visualization
- *OM + XPresso:* Extrude + Field-driven point modification via XPresso Iterator nodes + Tracer/Connect for the curve
- *Capsule shipping form:* wrap any of the above with AM-exposed sliders for substrate / containment / seed / step-size / iteration-count

The the reference build scene shows WHAT operations have to happen and proves it works; HOW we ship it depends on artist UX, performance, and integration with existing tools.

### R29 — Axis-remap SN deformer (bbox → target frame)

A reusable extract of the inner machinery, regardless of RD application.

**Ingredients:** `bb`, `composematrix ×2`, `inversematrix`, `transformvector ×3`, `transform_element` (with `transformpoint` + `selectionstringparser`).

**Use case:** transport per-vertex positions from one local frame to another — align extruded geometry to a curve-driven frame, conform to a target axis system, re-orient a sub-mesh to match a parent direction.

---

## Cross-links

- **Carries forward to:** scene 18 `Relax-Spline_02_Optimized` (next in the priority queue)
- **Reuses recipes from:** R7 (`memory_feedback_pde` from scene 03), R29 (axis-remap extracted here)
- **Feeds into the rebuild milestone:** the Mycelium V3 + R47 target-directed-growth rebuild deal — R28 belongs in the same toolkit since both are growth-with-target capabilities

---

## Operational notes

- **Animation IS time-driven** despite the classifier reporting `time_state: 0`. When re-probing this scene per-frame, step sequentially (0, 1, 2, ...) per gotcha #57.
- **Top-down is the diagnostic camera** here (gotcha #58). Perspective shows the bumped landscape, but the containment of the RD pattern within the 3 outline is only fully readable from above.
- **Image-size discipline:** all 6 captures at 800×450 with `save_path`, per the chat-locking rule.
- **Two study cameras left in scene** (`StudyCam_Persp`, `StudyCam_Top`) — clean up before saving if Spenser wants the file untouched.
- **C4D doc deliberately left open** at end of session — the SN editor needs to be opened manually for the "gorgeous layout" visual capture.
