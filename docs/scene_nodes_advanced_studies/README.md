# Scene Nodes Advanced Studies — Procedural C4D Scene Nodes Analysis

Dissection of 16 advanced Scene Nodes scenes from the reference set. Each
scene is a self-contained procedural tool — captured here as both raw JSON
dumps (`raw_dumps/`) and as a master vocabulary
expansion. Goal: deep enough understanding to rebuild any of these from
scratch.

Studied 2026-05-01.

## Scenes covered

| # | Scene | What it does | Architecture |
|---|---|---|---|
| 01 | Reaction_Diffusion | Gray-Scott reaction-diffusion on a plane (U/V chemicals diffusing across frames) | **Doc-level** (88 nodes, no host objects). 2× LCV (U+V chemicals), 2× noise, 11× reroute, 9× scaffold |
| 02 | Recursive_Subdivision | Iterative random subdivision modifier — like the 0360 we already cracked | Plane + **Nodes Mesh (180420600)** (20 nodes) + **Nodes Spline (180420700)** (11 nodes) — twin graphs side-by-side |
| 03 | Relax-Spline | Relaxes/smooths a spline iteratively via Memory feedback | "Geometry Axis" Nodes Modifier (180420400, 9 nodes) + doc-graph (15 nodes) chain: Memory → Subdivide → Sweep → Tessellation |
| 04 | EdgeToLine | Convert mesh edges to splines/tubes | Object Group Nodes Mesh (180420500, 11 nodes) — `edgetoline` + multransform/combine/mat scaffolding |
| 05 | Plexus | Connect-nearest-neighbor lines (the "Plexus" effect) | **TWO implementations side-by-side**: `Plexus_with_Loop` (8 nodes, LCV-based) vs `Plexus_without_Loop` (28 nodes, manual). **Direct loop-vs-unrolled comparison** |
| 06 | Spiderweb | Generative spiderweb via radial spokes + connecting strands | **Nodes Spline 180420700, 38 nodes**. Heavy on `arithmetic(9)`, `containeriteration(4)`, `append(3)`, `range(2)` |
| 07 | Coral_Structures | Coral-like growth (vertex displacement procedurally with axis masking) | "Geometry Axis" Nodes Modifier (16 nodes) + "Mask Y" Modifier (1 node). Uses `floatingio(4)`, `pointsmodifier(1)`, `composematrix(2)`, `transformvector(2)` |
| 08 | Mycelium | Branching network like fungal mycelium | Nodes Spline (13 nodes). Uses `surfacebluenoise` for distribution, `containeriteration(3)`, `append(2)` |
| 09 | Geometry-Solver_Basic | Foundation for procedural sim — minimal "Store for next Frame" capsule | "Store for next Frame" Nodes Mesh (180420600, **1 node**: just Memory). The atomic simulation primitive |
| 10 | Grow_Points | Iteratively grown points across frames | Object Group Nodes Mesh (180420500, 17 nodes) — Memory + buildfromvalue + maprange + readvalueatindex pattern |
| 11 | Voxelizer | Voxelize an input mesh into cubes (with Lego variant) | **Twin graphs**: `Voxelizer_Simple` (18 nodes) + `Voxelizer_Lego` (19 nodes). Uses `nearestneighbor`, `fillgeometry`, `decomposematrix`, `getvertexselectiondata` |
| 12 | Crystal_Cutter | Crystal-growth-like geometry from a base mesh | 38 objects! Two "Store for next Frame" Memory capsules drive simulation state. Most object-tree-heavy scene |
| 13 | Shortest_Path_Basic | Shortest path through points/graph | 7 objects, no SN bridges (likely uses MoGraph or classic ops + CallCommand) |
| 14 | Volume_Infection | Infectious propagation across mesh volume | **Object Group Nodes Mesh, 43 nodes** — most complex single graph in the set. `reroute(8)`, `container(4)`, `containeriteration(4)`, `legacyobjectaccess(3)`, `memory`, `nearestneighbor` |
| 15 | Particle_Emitter | Procedural particle system | **Doc-level (12 nodes)**. Uses `loopingfunction`, `containeriteration`, `torus`, `colorize`, `time`, `convertdegrees` |
| 16 | Spline_Grower (Ornament) | Tree-like spline growth (ornamental) | "Spline_Grower" Nodes Mesh (180420500, **32 nodes**). `loopcarriedvalue(1)` + `range(2)` + `decomposecontainer(2)` + `containeriteration(2)` + `append(1)` |

## Key architectural patterns identified

### Pattern A: Doc-level full simulation (Reaction Diffusion, Particle Emitter)
Entire scene lives in the doc-level graph — NO host objects. Renders via
`net.maxon.neutron.scene.root` → object-base children. The scene IS the
graph. Best for: simulations whose state lives in the graph (LCV/Memory
chains), where there's no parent geometry to deform.

### Pattern B: Memory-only "Store for next Frame" capsule (Geometry Solver, Crystal Cutter)
The atomic simulation primitive. A Nodes Mesh (180420600) wrapping ONE
Memory node, exposed as `currentout._0` (read previous frame) +
`next._0` (write this frame). Plus 2 AM inputs `object` + `object1`.
Drag this onto any classic-tools chain → instant frame-to-frame state
preservation. **Foundational unit.**

### Pattern C: Twin graphs side-by-side (Recursive Subdivision Mesh+Spline; Plexus with/without Loop; Voxelizer Simple+Lego)
Same scene contains TWO Nodes containers implementing the same effect
differently — for teaching, comparison, or stylistic variation. The
Plexus example is gold: **Plexus_with_Loop has 8 nodes, Plexus_without_Loop
has 28** for the same visual result. Refactoring with LCV cuts node
count ~3.5×.

### Pattern D: Semantically-named Nodes Modifier (Geometry Axis, Mask Y, Spline_Grower, Voxelizer)
Instead of generic "Nodes Modifier", the reference build renames each container by
its semantic role. **The container's name IS its role description.**
Geometry Axis = "transforms vertex positions along an axis";
Mask Y = "masks effect by Y coordinate"; Voxelizer_Lego = "voxelize but
Lego-style". Naming is part of the architecture.

### Pattern E: legacyobjectaccess as the OM-to-graph bridge
Used in 8+ scenes. Brings a CLASSIC C4D object (referenced by user link)
INTO the SN graph as a typed input — its geometry, matrices, etc. flow
on graph wires. **The inverse of Nodes Mesh** (which exposes graph output
to the OM): legacyobjectaccess exposes OM objects to the graph.
**This is how procedural tools "consume" external geometry without
having to be a child of it.**

### Pattern F: pointsmodifier for vertex-level deformation (Coral, Mask Y)
The `pointsmodifier` node iterates per-vertex and emits `pointout_position`,
`pointout.normals._0`, `pointout.colors._0`, `pointout.weights._0` —
all per-vertex streams. Use to displace points, modulate weights, set
colors per-vertex. The cleanest "do something to every vertex" primitive.

## NEW node vocabulary unlocked (additions to scene_nodes_vocabulary.md)

### Object/scene bridge
- **`legacyobjectaccess`** — bring an OM-tree classic object into the SN graph as typed input (matrix, geometry, children, color). Top output: `matrixout`, `net.maxon.nbo.legacyobject.*`. Used 8× across the 16 scenes — the canonical "input from object tree" primitive.
- **`children`** — access child objects of the SN-bridged host. Output: `array` of child object data. Used to iterate parent's children.

### Math primitives
- **`composematrix`** — build a 4×4 from translation/rotation/scale/shear. Inputs: `translationin`, `rotationin`, `scalein`, `shearin`, `rotationorderin`. Output: `out` (matrix).
- **`decomposematrix`** — inverse: matrix → translation/rotation/scale.
- **`inversematrix`** — invert a matrix.
- **`transformvector`** — applies an OPERATION (add/sub/cross/dot/etc.) to two vectors. Confirmed: this is **vector arithmetic**, not matrix×vector (gotcha #50). For matrix-times-vector use a different node.
- **`composevector3`** — make Vector from 3 floats. Critical primitive for procedural distribution-from-math (used in Coral, Particle Emitter).
- **`negate`** — sign flip.
- **`clamp`** — clamp value to range.
- **`distance`** — distance between two points.

### Geometry primitives extended
- **`fillgeometry`** — fill a geometry container with computed data. Outputs `distdataout` + `geometryout`.
- **`pointsmodifier`** — per-vertex iteration with named output streams (position, normals, colors, weights). The vertex-level deformation primitive.
- **`set_property` / `get_property`** — read/write geometry-level named properties (canonical IDs: `net.maxon.neutron.geometry.set_property` / `get_property`).
- **`getvertexselectiondata`** — extract vertex selection as array.
- **`splinechamfer`** — chamfer/round spline corners.
- **`explode_islands`** — split a mesh into separate connected components.
- **`bb`** — bounding box: outputs `min`, `max`, `center` (Vector). Critical for "scale to fit" / "match size" tools.
- **`transform_element`** — applies transform to specific geometry elements.

### Distribution
- **`surfacebluenoise`** — Maxon's blue-noise (Poisson-disk) surface point distribution. Already in atlas.
- **`nearestneighbor`** — find K nearest neighbors of each query point. Outputs `nearestindices` (array of indices). The PRIMITIVE for plexus, voxelization, network connectivity.

### Iteration
- **`loopingfunction`** — function-style loop (alternative to LCV's value-carrier model). Single input `distdataout`, single output `in`. Used in Particle Emitter for emission iteration.

### Color
- **`color`** — color value source.
- **`colorize`** — apply color to per-element streams (used in Particle Emitter).

### Utility
- **`floatingio`** — the AM-port surfacing primitive (Floating IO node). Usually 2-3 per Nodes Modifier — one per AM-exposed param.
- **`reroute`** — wire-organization passthrough (no compute).
- **`scaffold`** — visual layout container (no I/O, editor-only).
- **`group`** — sub-graph wrapper that DOES compute (containerable nodes).
- **`cmdlinearg`** — internal command-line argument node (framework-internal — appears in built-in capsules).
- **`net.maxon.neutron.corenode.multransform_5`** — internal matrix transformation core (framework scaffolding inside Nodes Mesh containers — appears 6× because each Nodes Mesh has the same root template).

### Subgraph internals (framework scaffolding inside Nodes Mesh root)
The Nodes Mesh container's root template includes 6 internal nodes that
appear in every Nodes Mesh graph:
`builder`, `combine`, `mat`, `sqrpart`, `sqrtrans`, `vectrans` — these
are the matrix/transform plumbing that wires the graph to the host's
transform. Don't touch them; they're auto-managed.

### `cmdlinearg`
Appears 6× in the user's "Plexus_without_Loop" — this is a framework-
internal arg node. Likely auto-generated for the unrolled loop variant's
hard-coded indices.

## Universal node-frequency (top 50 across all 16 scenes)

```
   27  arithmetic
   24  containeriteration
   23  readvalueatindex
   20  reroute
   13  scaffold
   11  container
    9  maprange
    9  append
    9  builder
    8  spline
    8  range
    8  group
    8  buildfromvalue
    8  legacyobjectaccess         ← ESSENTIAL: OM-to-graph bridge
    7  geometry
    7  memory                      ← preferred over LCV for time-based sims
    7  assembler
    6  type
    6  color
    6  children
    6  multransform_5              ← Nodes Mesh framework scaffolding
    6  combine, mat, sqrpart, vectrans, sqrtrans  ← all framework scaffolding
    6  cmdlinearg
    5  cube
    5  composematrix               ← matrix construction
    5  transformvector             ← vector arithmetic (NOT matrix*vec)
    5  compare
    5  floatingio                  ← AM-bridge primitive
    4  loopcarriedvalue            ← used less than memory
    4  decomposecontainer
    4  noise
    4  nearestneighbor             ← plexus / voxelize / network primitive
    4  set, getvertexselectiondata, getcount, buildfromsinglevalue
    3  time, scene.root, tessellation, hash, composevector3, matrix, fillgeometry
    2  scale, smoothgeometry, typeof, set_property, splinechamfer, explode_islands,
       get_property, inversematrix, booleanoperator, distance, concat,
       pointsmodifier, decomposematrix
```

## Insights for cinema4d-mcp tool authoring

1. **Plugin ID 180420700 IS Nodes Spline** (confirmed via Recursive Subdivision + Mycelium + Plexus + Spiderweb + Spline_Grower). My earlier hypothesis confirmed across 5 separate scenes.
2. **Memory > LCV for time-based simulations** — 7× usage vs 4×. Memory's "previous frame" semantics are simpler for animations.
3. **legacyobjectaccess** is critical infrastructure I was missing. It's how procedural tools take a classic-OM object as input without being a child-deformer.
4. **The "Store for next Frame" capsule** (1-node Memory wrapper) is so common it's basically a primitive. Worth shipping as a recipe template.
5. **`floatingio` count per scene roughly = AM-exposed param count** — 4 FIOs in Coral = 4 AM sliders. Useful heuristic.
6. **Reroute count is a complexity signal** — Reaction Diffusion has 11 reroutes (heavy organization needed for 88-node graph); minimal scenes have 0-2. >5 reroutes = "this is a complex multi-pipeline graph that benefits from visual organization."
7. **Twin graphs are intentional teaching artifacts** (Plexus with/without Loop, Voxelizer Simple/Lego) — when you see them in a scene, the lesson IS the comparison.

## Recreation difficulty rankings (for future "build me X" exercises)

| Tier | Scenes | Why this difficulty |
|---|---|---|
| **Easy** (1-15 nodes, well-known patterns) | Geometry Solver Basic, EdgeToLine, Mycelium, Crystal Cutter | Mostly Memory + 1-2 modifiers + standard wiring |
| **Medium** (15-30 nodes) | Recursive Subdivision, Coral, Voxelizer, Grow_Points, Spline_Grower | Multi-stream, Memory loops, FloatingIO surfacing |
| **Hard** (30-50 nodes, novel patterns) | Spiderweb, Volume_Infection | 30+ nodes, complex iteration patterns, multiple feedback loops |
| **Expert** (50+ nodes, doc-level orchestration) | Reaction_Diffusion (88) | Doc-level Gray-Scott PDE; double LCV; 11 reroutes for graph organization |

## Per-scene next-step learnings

The richest 5 scenes to study deeper (in priority order for advanced tool fluency):

1. **Volume_Infection (43 nodes)** — most complex single Modifier graph. Has 8 reroutes + nearestneighbor + memory + 3 legacyobjectaccess. Likely the "infection spreads to nearest neighbors" iteration.
2. **Spline_Grower (32 nodes)** — LCV + Range + 2× decomposecontainer + 2× containeriteration. The "tree growth" pattern.
3. **Spiderweb (38 nodes)** — pure Nodes Spline. Heavy arithmetic + radial structure logic.
4. **Reaction_Diffusion (88 doc nodes)** — Gray-Scott PDE simulation. Educational gold for doc-level setups.
5. **Plexus with/without Loop comparison** — the canonical example of "loop refactoring saves 70% of nodes."

## Raw dumps

All 16 scene dissections live in [`raw_dumps/*.json`](raw_dumps/) — node
type counts, top-level connection samples, AM input lists, materials,
and per-object metadata. Use these as ground truth when authoring
recreation attempts.
