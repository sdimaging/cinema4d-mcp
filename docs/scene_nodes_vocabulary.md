# Scene Nodes — Node Vocabulary Reference

The "engineer's mental model" for C4D 2026 Scene Nodes. Built incrementally
from dissecting Maxon-shipped reference scenes. **Holistic awareness of nodes
+ what they do + how they connect to each other** — the prerequisite for
architecting non-trivial procedural tools.

Compiled 2026-05-01 from dissection of `0360 Geo Feedback Loop` (extended
version with red caps + edge sweeps).

## The data type taxonomy (what flows on each wire)

Scene Nodes is **strongly typed at the wire level**. Every connection
carries a specific data type. The major streams:

| Type | What it is | Common port name |
|---|---|---|
| **Geometry** | polygon mesh + named selections + UV/vertex maps | `geometryin` / `geometryout` |
| **Spline** | curve geometry (specific subset of Geometry, kind=spline) | (same — uses geometryin/out, type-checked at runtime) |
| **Selection** | named subset of geom elements (poly/edge/point) | embedded in Geometry stream as named selection |
| **Float / Int / Bool / String / Vector / Matrix** | scalar/numeric primitives | typed inputs |
| **Container / DataDictionary** | the LCV's carried payload (geom + metadata) | `current._0` / `next._0` / `final._0` / `initial._0` |
| **Domain** | iteration scope binding | `innerdomain` / `outerdomain` |
| **SplineMapper** | parametric curve for sweep profiles, scale/rot envelopes | sweep-internal |

**Selections live INSIDE the Geometry stream, not as a separate type.**
Operators like `setselection` modify the geometry's embedded selection
metadata. Materials downstream read by name.

## Node taxonomy (organized by purpose)

### Primitives — sources of geometry

| Node | $type | Output | Notes |
|---|---|---|---|
| Cube | `Cube` | geometryout | parametric box w/ X/Y/Z subs |
| Sphere | `Sphere` | geometryout | |
| Tube | `Tube` | geometryout | |
| Torus | `#net.maxon.neutron.node.primitive.torus` | geometryout | **AMBIGUOUS** as `Torus` — also a Field type — must use `#`-prefixed canonical |
| Cone | (probe needed) | geometryout | |
| N-Sided | `#net.maxon.neutron.node.primitive.curve.nside` | geometryout (spline) | n-sided closed spline — used as SWEEP CROSS-SECTION |
| Plane | `Plane` | geometryout | |

### Modifiers — geometry → modified geometry

| Node | $type | What it does |
|---|---|---|
| Extrude | `Extrude` | extrudes selected polys; key params: `offset`, `inset`, `offsetvar`, `subdivisions`, `createcaps`, `selection`, `selectionstring`, `mode` |
| Inset | `Inset` | inset polys |
| Subdivide | `Subdivide` | subdivision surface |
| Bevel | `Bevel` | bevel edges |
| Tessellation | `Tessellation` | adaptive refinement (smooth swept tubes etc.). Inputs: `angle`, `length`, `tessellationtype` (adaptive/uniform), `number`, `geometryin` |
| Delete | `#net.maxon.neutron.modeling.delete` | removes selected components. Inputs: `selection`, `selectionstring="default"`, `mode`, `deleteunusedvertices` |
| Smooth Geometry | `Smooth Geometry` | smoothing |

### Selection ops — geometry → geometry with modified selection

| Node | $type | What it does |
|---|---|---|
| Store Selection | `Store Selection` (= setselection) | Names a subset of polys/edges/points. Inputs: `selectionname` (str = output name), `selectionstring` (str — see Selection String semantics below), `mode` (polygons/edges/points), `indices` (optional explicit array) |
| Random Selection | `Random Selection` | hash-based random subset. Variadic hash-named inputs control mode/seed/threshold/invert |
| Grow Selection | `Grow Selection` | expand selection to neighbors |
| Modulo Selection | `#net.maxon.neutron.asset.geo.modulo` | every Nth element |
| Facing Selection | `#net.maxon.neutron.asset.geo.facingselection2` | **selects polys by normal direction**. Inputs: facing vector, angle threshold, source selection name, output selection name. The KEY node for "give me the up-facing tops" |
| Invert Selection | `Invert Selection` | toggles inside↔outside of a selection |

### Stream conversion ops — change data type

| Node | $type | Input → Output |
|---|---|---|
| Edge to Line | `#net.maxon.neutron.geometry.edgetoline` | poly geom (with edge selection) → spline geom of those edges |
| Spline Assembler | `#net.maxon.neutron.geometry.spline.assembler` | array of points → spline geom |
| Get Vertex Selection Data | `Get Vertex Selection Data` | extracts point selection as array |
| Get Polygon Selection Data | `Get Polygon Selection Data` | extracts poly selection as array |
| Polygons Info | `#net.maxon.neutron.geometry.polygoninfo` | per-polygon info (vertex indices) |
| Points Info | `Points Info` | per-vertex info (positions, normals, weights) |
| Geometry Property Get/Set | `#net.maxon.neutron.geometry.get_property` / `set_property` | read/write whole-geometry properties |

### Multi-stream ops — combine multiple geometry streams

| Node | $type | What it does |
|---|---|---|
| Connect Geometries | `#net.maxon.neutron.geometry.connect_geometries` | merges multiple geometry streams into one. **Variadic** `geometryin._0`, `_1`, etc. Param `mergeselectionin` controls whether named selections are unified |
| Compose Container | `Compose Container` | bundles parallel arrays into a collection |

### Spline operations

| Node | $type | What it does |
|---|---|---|
| Sweep Line | `#net.maxon.neutron.modeling.sweepline` | sweeps a profile (cross-section spline) along a path spline. **Variadic** `geometryin._0` (path) and `_1` (profile). Lots of params: profileshaping primary/secondary, caps, banking, growth, etc. |
| Resample Spline | `Resample Spline` | re-spaces points along a spline |
| Spline Length | `Spline Length` | total arc length |
| Add Control Point | `Add Control Point Along Spline` | insert points |

### Iteration / control flow

| Node | $type | What it does |
|---|---|---|
| Range | `Range` | iterator 0..N-1. Out: `count`, `out`, `innerdomain` |
| Loop Carried Value | `Loop Carried Value` | iterative feedback. **Variadic** types via `types` port. Cycle ports: `current._0` / `next._0`. Auxiliary inputs: `in@hash...` (passthroughs available each iter). State: `initial._0` / `final._0`. The PRIMITIVE for stateful iteration |
| Memory | `Memory` | time-based version of LCV (uses animation time as iterator) |
| Container Iteration | `Container Iteration` (= iterate) | per-element foreach |
| Switch | `Switch` | pick one of N inputs by index |
| If | `If` | ternary — datatype + cond + true_val + false_val |

### Math & utility

| Arithmetic | `Arithmetic` | binary op (operation enum: add/sub/mul/div/etc.) |
| Compare | `Compare` | comparison (>, <, ==, etc.) |
| Hash | `Hash` | deterministic pseudo-random vector from seed/salt |
| Sample Noise | `Sample Noise` | noise lookup at position |
| Range Mapper | `Fitrange` (= net.maxon.asset.math.fitrange) | remap domain |
| Reroute | `Reroute` | wire-organization passthrough — **NO computation, just visual** |

### Editor-only / framework

| Node | What it does |
|---|---|
| `annotation@*` | in-graph documentation text (editor metadata only) |
| `scaffold@*` | visual layout container (no I/O, no compute) |
| `group@*` | sub-graph wrapper (computational — contains other nodes) |
| `context_externaltimeinput` / `context_notime` | framework-internal time context binding (ignore) |

## Root-side "AM bridge" — the input contract

For Nodes Mesh / Nodes Modifier / Nodes Spline, **AM-exposed sliders surface
as typed root inputs** in `root.GetInputs()`:

| AM widget | root input type | Default-named ports |
|---|---|---|
| Int slider | int | `integer`, `integer1`, ... |
| Float slider | float | `float`, `float1`, ... |
| Vector | vec3 | `vector`, ... |
| Bool | bool | `bool`, ... |
| String | string | `string`, ... |

Wire these to internal node inputs via `synthesize_port` — the **connection
provides the type binding** that makes the AM widget render as a
draggable slider (Session 3 breakthrough).

## Root-side "host bridge" — the output contract

For object-tree-bridged containers, the graph's output goes to typed
`root.GetOutputs()`:

| Container | Plugin ID | Root output |
|---|---|---|
| Nodes Mesh | 180420600 | `geometryout` (poly mesh to host) |
| Nodes Modifier | 180420400 | `geometryout` (modified mesh replaces parent's) — and `root.GetInputs().geometryin` provides parent's geometry |
| Nodes Spline | (probe needed) | `splineout` (spline to host) |
| Doc-level scene.root graph | n/a (doc graph) | `scene.root.op.objectbase.children._0` (variadic) — viewport-only, no Object Manager |

## Selection String semantics — the keywords

Anywhere a `selectionstring` input appears (setselection, extrude,
subdivide, delete, etc.), it accepts these forms:

| Value | Meaning | When to use |
|---|---|---|
| `default` | the **currently-active selection** at this point in the stream | Use this 90% of the time. Many ops auto-set "default" to their natural output (sweepline → swept polys; extrude → caps; etc.). It's **context-aware** and stays correct if you re-route the node elsewhere. |
| `active` | synonym for `default` | |
| `all` | every element of the geometry | Use only when you really want everything regardless of upstream tagging. Less reusable than `default`. |
| `"name"` | a specific named selection (case-sensitive, **must be wrapped in literal quotes inside the string**) | Use when you need a SPECIFIC selection by name — e.g., `"top"` to operate on the facingselection2 output |
| index ranges | e.g. `0-15, 20, 25-30` | Use for explicit poly index lists |
| **`*` (or other wildcards)** | **NOT VALID — silently empties the selection** | Common mistake: wildcards aren't supported. Use `default` or `all` instead. |

### Why `default` beats `all` in most cases

`default` is **semantically correct** — it picks up "whatever the
upstream chain considered the active result." If you build a sub-pipeline
like `extrude → facingselection2 → setselection(name="top", string="default")`,
the setselection is naming "the current selection (= the up-facing polys
from facingselection2)" as "top". Reusable, robust to chain changes.

`all` would always select every poly at that point — fine if the
upstream chain hasn't narrowed anything, but breaks the moment your
upstream gets more specific.

**Real bug from this session (2026-05-01)**: setselection_2 in the
extended Geo Feedback Loop had `selectionstring="*"`. Wildcards aren't
supported → empty selection → texture-tag "all" restriction had 0 polys
→ green material didn't render. Fix: `"*"` → `"default"`. Green now paints
the swept tubes (which sweepline naturally tags as default).

## The Selection→Material bridge

**Named selections in the graph surface as classic C4D Selection Tags on
the host object.** Then standard Texture Tags with `Restriction = "name"`
paint materials onto those polys.

```
Inside graph: setselection(selectionname="caps", selectionstring="default") on the post-extrude geom
    ↓
Host (Plane / Cube / etc.): "caps" Selection Tag appears (Polygon Selection Tag, type 5673)
    ↓
On host: Texture Tag with material="MyRedMat", restriction="caps"
    → red paints only the polys in the "caps" selection
```

**Material order matters** — multiple Texture Tags compose left-to-right:
later tags override earlier within their restricted polys. So `[yellow on
"initial", red on "top", green on "all"]` correctly paints green over
everything (overriding yellow + red), IF the "all" selection is non-empty.

**Common failure:** giving setselection an invalid `selectionstring`
(like `"*"` — wildcards aren't supported) → empty named selection →
texture restriction has 0 polys → material doesn't render.

## Variadic ports — the hash-suffix trick

Some nodes (Random Selection, LCV, sweepline, connect_geometries, group)
have variadic ports with hash-named children like `inport@W7ihSMVTPdz...`.
These are framework-generated ports keyed by content hash. To wire them:

1. List the node's inputs/outputs (via `GraphNode.GetInputs/Outputs`)
2. Match by full ID (which includes the hash) when re-wiring
3. The hash is **stable per content** but instance-specific per graph

For variadic merge ports (connect_geometries, sweepline), child slots
follow `_0`, `_1`, `_2`, ... convention — these are added by `AddPort` on
the parent variadic, not standard `Connect`.

## Annotated topology of 0360 (the full pipeline)

```
                    AM SLIDERS (host → graph via root.GetInputs)
                          │
   Iterations(int) → reroute → range.end (drives N iterations)
   Chance(float) → LCV.in@LE (passthrough through iterations)
   Seed(float)   → LCV.in@Qe (passthrough)
   parent geom   → setselection(name="initial", str="all") → LCV.initial._0
                                                              │
                                  [N iterations of recursive extrude
                                   inside LCV's scope, framework-managed]
                                                              │
                                                         LCV.final._0
                                                              │
                                                              ▼
                                          extrude(inset=100,offset=20,offsetvar=0.26)
                                                              │
                                                              ▼
                                          facingselection2(facing=Y+, angle≈0.262, name="top")
                                                  │                      │
                              ┌───────────────────┘                      └────→ connect_geometries.geometryin._0
                              ▼                                                         (BODY branch w/ "top" selection)
                       invertselection
                              │
                              ▼
                          delete(selectionstring="default")  [removes non-top polys]
                              │
                              ▼
                         edgetoline   [edges of remaining tops → spline]
                              │
                              ▼
                         tessellation   [refine spline]
                              │
                              ▼
                         sweepline.geometryin._0 (PATH)
                              ▲
                              │
                         nside.geometryout → tessellation → sweepline.geometryin._1 (PROFILE)
                              │
                         sweepline.geometryout (the SWEPT TUBES)
                              │
                              ▼
                         setselection_2(name="all", selectionstring="*"  ← BUG! should be "all")
                              │
                              ▼
                         connect_geometries.geometryin._1
                              │
                              ▼ (merged: BODY + SWEPT TUBES)
                         root.geometryout
```

## What I learned about engineering with this vocabulary

1. **Selections are first-class.** The whole pipeline pivots on creating
   named selections and routing materials/edges/operations to them.
2. **Branch + merge is fundamental.** facingselection2 outputs to TWO
   destinations (continuing chain + merging back) — this is normal, not
   unusual. The graph isn't always linear.
3. **Multi-stream merging via connect_geometries** is the pattern for
   "show the body + show the tubes" simultaneously. Without this, a Nodes
   Mesh has only ONE output port and you can only emit one stream.
4. **Sweep needs TWO geometry streams** — path + profile — via variadic
   `geometryin._0` and `_1`.
5. **Tessellation between conversion and consumption** keeps swept geom
   smooth (the path's polys are sparse from edgetoline; tessellation
   refines).
6. **Order of texture tags on the host** determines material precedence
   (later tags override earlier within their selection).

## Mistakes I made before this dissection

1. Building "single-pass random selection + extrude" as if that was the
   recursive iterative pattern. The recursive look comes from
   `inset=100` (full inset creates new poly inside) + LCV iterating, NOT
   from per-iteration random selection.
2. Looking only at "geometry chain" wires — missed that **a single
   facingselection2 output goes to TWO downstream nodes** (a fork).
3. Treating connect_geometries as exotic when it's the standard
   "multi-stream output" merger.
4. Not noticing variadic ports (`_0` / `_1`) on sweep + connect — these
   are first-class Scene Nodes idioms, not edge cases.

## Engineering recipe template — "build me X" architectural decomposition

When asked to build a procedural tool, decompose by:

1. **Source** — what produces the initial geometry? (primitive node, or
   `root.geometryin` from parent for a Modifier)
2. **Stream operations** — what modifications happen on the geom stream?
   (extrude, inset, etc.)
3. **Selection-creation ops** — what subsets of the geom need to be
   named? (random/facing/modulo/grow + setselection)
4. **Branching** — does any selection or stream need to fork to multiple
   downstream consumers? (one node's output wired to multiple inputs)
5. **Conversion ops** — do we need to extract spline / point / array
   data from the geometry? (edgetoline, points info, etc.)
6. **Multi-stream merge** — do we need multiple parallel pipelines that
   merge before output? (connect_geometries with variadic _N inputs)
7. **AM exposure** — which params should artists control? (synthesize_port
   each one, named, typed, default value, label)
8. **Material/tag side** — for each named selection, decide whether it
   gets surfaced as a host Selection Tag for material restriction

Apply this checklist BEFORE wiring anything. Architecture first, then
implementation.
