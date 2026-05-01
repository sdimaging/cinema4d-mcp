# Scene Nodes — Nodebase Deep Study

Companion to [`scene_nodes_guide.md`](scene_nodes_guide.md). Source-grounded
node + port inventory and recipe scaffolds extracted from 16 Nodebase
articles (2026-05-01 study session).

The architecture brief at
`Desktop/Scene_Nodes_Handoff/docs/scene_node_architecture_for_mcp.md`
captures the strategic mental model. This doc captures the **concrete
graph-construction primitives** — node names, port names, exact data
flows, performance facts, and verbatim load-bearing quotes — that the MCP
needs to synthesize correct production graphs without reverse-engineering
the same details over and over.

## Sources

All 16 articles fetched and processed:

| # | Article | Key contribution |
|---|---|---|
| 1 | [Scene Nodes vs. Xpresso](https://nodebase.info/general/scene-nodes-vs-xpresso/) | Subsystem boundary |
| 2 | [Modeling with Scene Nodes](https://nodebase.info/fundamentals/modeling-with-scene-nodes/) | Selection-aware modeling stack |
| 3 | [Modeling with Scene Nodes Part 2](https://nodebase.info/example/modeling-with-scene-nodes-part-2/) | Capsule interface + Resource Editor |
| 4 | [Iterations for Geometry Generation](https://nodebase.info/example/iterations-for-geometry-generation/) | Range → spline pattern |
| 5 | [Data Types in Scene Nodes 2 — Vectors](https://nodebase.info/fundamentals/data-types-in-scene-nodes-2-vectors/) | Vector semantics |
| 6 | [Data Types in Scene Nodes 1 — Auto Conversion](https://nodebase.info/example/data-types-in-scene-nodes-1-auto-conversion/) | Type interop matrix |
| 7 | [Conditional Creation of Arrays](https://nodebase.info/example/conditional-creation-of-arrays/) | Bool→count Append pattern |
| 8 | [Working with Arrays Part 1](https://nodebase.info/example/working-with-arrays-part-1/) | Whole-array vs per-element |
| 9 | [Working with Arrays Part 2](https://nodebase.info/example/working-with-arrays-part-2/) | Build/Concatenate/LCV-accumulator |
| 10 | [Working with Arrays Part 3](https://nodebase.info/example/working-with-arrays-part-3/) | Deletion performance |
| 11 | [Working with Arrays Part 4](https://nodebase.info/example/working-with-arrays-part-4/) | Array Collections |
| 12 | [Using Loops for Modeling](https://nodebase.info/example/using-loops-for-modeling/) | LCV exact ports + index validity |
| 13 | [Spline Plane from Noise](https://nodebase.info/example/spline-plane-from-noise/) | Nested iteration + Time/Switch |
| 14 | [Combining MoGraph and Scene Nodes — Voronoi](https://nodebase.info/general/combining-mograph-and-scene-nodes-voronoi-fracture/) | Connect-object bridge |
| 15 | [Controlling a Modifier with MoGraph Fields](https://nodebase.info/example/controlling-a-modifier-with-mograph-fields/) | Vertex Map field bridge |
| 16 | [Weight Controlled Poke Modifier](https://nodebase.info/example/weight-controlled-poke-modifier/) | Polygon-from-vertex weight reduction |

## Verbatim load-bearing quotes

These are the claims that should anchor MCP behavior. Quoted verbatim from
the source articles:

- **MoGraph field bridge is mandatory:** *"Currently is no way to directly
  sample MoGraph fields within Scene Nodes"* — must bridge through
  Vertex Map. (Article 15.)
- **Subsystem separation:** *"Scene Nodes and Xpresso do not work within
  the same context. They don't solve the same kinds of problems and
  therefore one can not replace the other."* (Article 1.)
- **Index stability under extrusion:** *"While extrusions change the
  topology of the geometry they do not change the polygon indexes of the
  polygons we start with, every polygon added by the extrusion process is
  added at the end of the list of polygons."* (Article 12.)
- **Index instability otherwise:** *"This is different if we were to use
  an operation like delete or subdivide, in that case the original
  polygon is either not there anymore, or is replaced by multiple
  polygons, there is no simple solution for those situations a new
  solution has to be found case by case."* (Article 12.)
- **Scope wires:** *"While the Scene Nodes can often find these iteration
  scopes correctly on their own so setups without this connection can
  work, it is good practice to clearly define the scope since later on in
  more complex setups with multiple nested iterations things might not be
  as clear. Do yourself a favor and always take care of this."*
  (Article 12.)
- **Graph optimization:** *"If the use of Time is switched off, the node
  graph is automatically optimized in a way that the Time node is never
  evaluated."* (Article 13.)
- **Assemble Collection beats Append Elements for iterations:**
  *"Assemble Collection is preferable over the Append Elements approach,
  since it can be better optimized by the internal system."* (Article 9.)
- **Swap Erase performance:** *"With sufficiently high array lengths the
  difference in speed is extreme, in case of 100.000 elements the speed
  factor between the two this example is 50."* (Article 10.)

## Node inventory — by use-case

### Iteration & control flow

| Node | Key ports | Notes |
|---|---|---|
| **Range** | `Count` (in), index (out) | iterates 0..Count-1 |
| **Range Mapper** | input/output high+low | re-maps domain (e.g. 0..1 → 0..2π) |
| **Iterate Collection** | array (in), `Index` (out — counter), `Elements` (out — semantic indexes), `{…` `…}` (scope) | **Index ≠ Elements** when iterating selections |
| **Loop Carried Value (LCV)** | `Variable N` (init), `Previous>Variable 1` (in this iter), `Next>Variable 1` (out next iter) | for stateful loops, topology mutation |
| **Memory** | similar to LCV but uses animation time as iterator | animation-driven accumulators |
| **Switch** | conditional input | gates branches; unevaluated branches optimized away |
| **Time** | continuous time value | auto-pruned if downstream Switch is off |
| **Selection String** | accepts `default`, `active`, `all`, `"named"`, indices | wires the *currently iterated* polygon/point in LCV |

### Array operations — whole-array (preferred when available)

| Node | Operation |
|---|---|
| **Average** | array → single result |
| **Scalar Arithmetic** | uniform op across all elements |
| **Concatenate** | variadic arrays → joined array |
| **Reverse** | reverses order |
| **Sort** | sorts by value |
| **Shuffle** | random reorder |

### Array operations — element-wise / mutation

| Node | Notes |
|---|---|
| **Build Array** | variadic literal-style construction |
| **Assemble Collection** | requires datatype string (e.g. `net.maxon.parametrictype.vec<3,float>[]C`); preferred for iteration outputs |
| **Append Elements** | `Count` input doubles as conditional gate — Bool auto-converts True=1 / False=0 |
| **Insert Element** | inserts at index |
| **Erase Element** | `Keep Order` flag (default true; off = parallel = faster) |
| **Erase Value** | removes all matching value |
| **Swap Erase Elements** | replaces erased slot with last element — **~50× faster than Erase Element at 100k elements**, loses order |
| **Truncate** | end-only deletion; fastest for tail removal |
| **Get Element** | array OR collection; multi-port output for collections |
| **Set Element** | array OR collection; has `{…` `…}` scope ports |

### Collections (parallel arrays)

| Node | Notes |
|---|---|
| **Compose Container** | variadic input of same-length arrays → collection |
| **Sort Collection** | sort by index OR by name — **prefer index** (name lookup fails silently on rename) |
| **Import Data** | delivers array collections from CSV |

### Math / procedural

| Node | Notes |
|---|---|
| **Hash** | deterministic pseudo-random vector from seed (typically Range index) |
| **Sample Noise** | expects sample positions in `(0,0,0)..(1,1,1)`; other scales OK |
| **Transform Vector** | matrix-applied to positional vector |
| **Transform Geometry** | uses `Pivot Matrix` for correct transform location |

### Geometry retrieve / write-back

| Node | Notes |
|---|---|
| **Geometry Property Get** | retrieves whole arrays (positions, normals…) |
| **Geometry Property Set** | writes modified arrays back |
| **Points Info** | outputs selected points + weights + positions + normals; `Named Weights` input — empty = first vertex map found |
| **Polygons Info** | array-per-polygon of vertex indexes — the structural bridge for polygon-level weight reductions |
| **Selection Modifiers** | Noise Selection / Grow Selection / etc. — procedural selection construction |

### Modeling capsules

Extrude · Bevel · Inset · Subdivide · Smooth · Delete · Poke. All
selection-aware (default to active selection unless a Selection String
overrides).

### MoGraph bridge

| Node / object | Role |
|---|---|
| **Connect object** (classic) | unify fragmented Voronoi/MoGraph output into one mesh before Scene Nodes input |
| **Object-mode input port** | child geometry replaced by Scene Nodes output (generator-style); set via Resource Editor port mode |
| **Vertex Map tag** | the **only** documented MoGraph Field → Scene Nodes data path |

## Type system

### Scalar / compound / collection / geometry

```
scalar:      float, int, bool
compound:    vector2d, vector (3D), vector4d, color, coloralpha, matrix
data:        string, url, time
geometry:    geometry, geometryobject, mesh, spline
collections: array<T>, collection (named-array bundle), tuple
special:     selection string, named weight maps
```

### Auto-conversion (Article 6)

All of `Float ↔ Integer ↔ Vector ↔ Vector2D ↔ Vector4D ↔ String ↔ Color
↔ ColorA ↔ Boolean ↔ Matrix` auto-convert.

The most-leveraged auto-conversion in graph patterns is **Bool → Int**
(True=1, False=0) — used by Append Elements `Count` to gate conditional
appends. Article 7 makes this idiomatic.

### Vector semantics (Article 5)

3D vectors do triple duty: positions / directions / RGB. 4D adds W
(geometric) or Alpha (color). Normals are the magnitude-1 special case.
Component access is via port unfolding in the editor.

## Canonical recipe scaffolds

These are five proven patterns the MCP should bake into recipe primitives.

### Recipe R1 — Circle from Range (Article 4)

```
Range(Count)
  └→ index ─→ Range Mapper(0..Count-1 → 0..2π) ─→ angle
                                              └→ Transform Vector(matrix from angle) ─→ position
                                                                                    └→ Assemble Collection(Vector[]) ─→ positions[]
                                                                                                                   └→ Assemble Spline ─→ spline
```

**Scope:** Assemble Collection's `…}` connects to Range's iteration end.

### Recipe R2 — Noise point displacement modifier (Article 12, simple)

```
Geometry Property Get(input geo)  ─→ positions[]
Points Info(input geo)            ─→ selected indexes (Elements port)

  Iterate Collection(selected_indexes)
    ├─ Index   (counter — 0..n-1)
    └─ Elements (real point indexes)
         ├→ Sample Noise(at position[Element]) ─→ noise(0..1)
         │                                    └→ Range Mapper(amplitude) ─→ offset
         │                                                              └→ position[Element] += offset*normal
         └→ Set Element(positions, Element, new_pos)

  scope ─{…}─ → Geometry Property Set(geo, "positions", positions[])
```

**Critical:** the **Elements** port is the real point index; the **Index**
port is just the iteration counter.

### Recipe R3 — Noise extrude with LCV (Article 12, LCV section)

```
Initial geometry → LCV Variable 1
  Previous>Variable 1 ─→ get current iteration's polygon
                     ├→ Selection String(current poly index) ─→ Extrude.selection
                     ├→ Sample Noise(poly center) → Range Mapper → offset
                     ├→ Extrude(geo, offset) ─→ extruded geo
                     └→ Transform Geometry(scale, Pivot Matrix) ─→ result
  result → Next>Variable 1
```

**Why LCV:** topology mutation — each extrusion needs the previous result.
**Why valid:** extrusion preserves original polygon indexes (new geometry
appended at end). For delete/subdivide this approach won't work — recompute
selections per stage.

### Recipe R4 — Weight-controlled Poke (Article 16)

```
Polygons Info(geo) ─→ poly_vertex_indexes[][]   // array per poly of vertex indexes
Points Info(geo)   ─→ vertex_weights[]          // empty Named Weights = first VMap

LCV(geo)
  for poly in poly_vertex_indexes:
    inner Iterate Collection over poly's vertex indexes:
      gather weights[v] for v in poly
    avg_weight = Average(gathered)               // reducer; max/min are alternatives
    if avg_weight * user_offset > 0:             // skip when zero — perf optimization
      Poke(geo, poly, avg_weight * user_offset)
```

**Reducer choice is a knob** — Average is the documented default; Max
gives "any vertex hot → poke" semantics; Min gives "all vertices hot →
poke" semantics.

### Recipe R5 — MoGraph Voronoi → Scene Nodes (Article 14)

```
Voronoi Fracture (MoGraph) ─→ child of Connect object → unified mesh
                                                     ↓
Scene Nodes Generator with Object-mode input port
  child geometry replaced by SN output (generator-style)
  → SN processing
  → output replaces child of Object Group
```

**Why Connect object:** without it, fragments are separate generator
children; with it, you get a single mesh suitable for SN input.
**Why Object-mode port:** standard Object Group routes through the
experimental Op system. Object-mode is the public, non-experimental path.
The port mode is set via Resource Editor.

## Performance heuristics

| Heuristic | Rule |
|---|---|
| Whole-array > iteration | If a built-in operates on the array, use it (Article 8). |
| End-deletion | Truncate — fastest. |
| Mid-deletion, order doesn't matter | Swap Erase — ~50× faster at 100k. |
| Mid-deletion, order matters, parallelizable | Erase Element with Keep Order=false. |
| Insert/delete during iteration | iterate **backwards** so changes only affect already-processed indexes. |
| Conditional append | Append Elements with `Count` driven by Bool auto-converted to Int. |
| Iteration accumulator | Assemble Collection > Append Elements (better internal optimization). |
| Time-driven branches | wire through Switch; unevaluated branch is pruned automatically. |

## Implications for cinema4d-mcp

### Atlas / handler additions

The atlas should add or verify entries (with port schemas) for:

```
Hash, Memory, Switch, Time, Selection String, Pivot Matrix,
Compose Container, Sort Collection, Import Data,
Geometry Property Get, Geometry Property Set,
Points Info, Polygons Info,
Average, Scalar Arithmetic, Build Array, Sample Noise,
Range, Range Mapper, Iterate Collection, Assemble Collection,
Append Elements, LCV, Set Element, Get Element,
Insert Element, Erase Element, Swap Erase Elements, Truncate,
Erase Value, Reverse, Sort, Shuffle,
Connect object, Vertex Map tag, Selection Modifiers (Noise / Grow)
```

Many already exist in `data/node_template_index.json` (802 templates).
Action: cross-reference the list against the atlas and flag missing port
schemas.

### Recipe primitives to ship

Build R1–R5 above as `scene_nodes_recipe_*` MCP primitives:

```
scene_nodes_recipe_circle_from_range(host, count, radius)
scene_nodes_recipe_noise_displacement(host, target_geo, amplitude, frequency)
scene_nodes_recipe_noise_extrude_lcv(host, target_geo, selection, amplitude)
scene_nodes_recipe_weight_poke(host, target_geo, vertex_map, offset, reducer="avg")
scene_nodes_recipe_mograph_voronoi_processor(host, voronoi_source, sn_op_chain)
```

Each ends with the verification contract from
`scene_node_architecture_for_mcp.md`:

```
- target object has SN graph
- all created nodes exist by stable id
- root exposed ports have handler.GetDescID(port.GetPath())
- no orphaned scope wires
- output geometry port wired
- save/reload persistence (battle test)
```

### New verification rules to add to `scene_nodes_classify_graph`

- **Scope wire orphans** — Iterate Collection / Set Element / Assemble
  Collection without a `{…` or `…}` connection.
- **Sort Collection by name** — flag with a hint to switch to index.
- **Topology-mutation without LCV** — delete/subdivide inside Iterate
  Collection without LCV wrapping.
- **Whole-array replaceable** — Iterate Collection that just sums /
  averages / arithmetic-operates without per-element specialization.

### Synthesize_port type coverage to verify

`scene_nodes_synthesize_port` v2.1 currently covers float/int/bool/string/
vector. Article 5 emphasizes 4D vectors and Color/ColorA. Add to battle
test:

- `vector4d` default → connect to a 4D-typed port → AM widget shape
- `color` default → connect to a Color-typed port → AM color picker
- `coloralpha` default → connect to a ColorA-typed port

### Resource Editor surface — research target

Article 14 references Resource Editor port mode switching for Object-mode
ports. Article 3 references Resource Editor parameter reordering. This is
the same UI surface our PORTLIST experiments touched. The `Edit Resource`
right-click is a public-API entry — worth a probe pass to see if its
backing dialog operations are exposed via `CallCommand` / command registry
(parallel to the gesture-differ work).

## What this study did NOT find

- **No direct Python API for Resource Editor port-mode flag.** The
  research lane should keep this open.
- **No documented field-sampling node inside Scene Nodes.** Verbatim:
  "Currently is no way." Vertex Map bridge is the only path.
- **No stable Asset Browser → Convert-to-Asset programmatic path** — the
  modal dialog is referenced but no NO_UI flag (already in gotchas #38).

## TL;DR

Scene Nodes is a typed, selection-aware, array-first geometry/dataflow
backend. Five canonical recipes (R1–R5) cover the bulk of practical
production work. The MCP's job is to (a) pick the right recipe, (b)
generate it with explicit scope wires and correct LCV use, (c) expose
only meaningful controls via the connection-based root-port recipe,
(d) verify the result, and (e) bridge to MoGraph/Fields via Vertex Map
or Connect-object — never by trying to sample fields directly.
