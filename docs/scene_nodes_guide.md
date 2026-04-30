# Cinema 4D 2026 Scene Nodes — A Practical Guide for the MCP

This document captures everything the cinema4d-mcp project has learned about
authoring, dissecting, and synthesizing Cinema 4D 2026 Scene Nodes graphs
programmatically. It's the artifact of an extensive live-probing session
against a running C4D 2026.2 instance combined with dissection of 12+
real-world capsules across 9+ example scenes.

The data backing this guide ships with the plugin as JSON:
- [`data/scene_nodes_atlas.json`](../data/scene_nodes_atlas.json) — patterns, port types, anti-patterns, vocabulary classes, design principles
- [`data/node_template_index.json`](../data/node_template_index.json) — all 802 NodeTemplate canonical asset IDs categorized
- [`data/verified_labels.json`](../data/verified_labels.json) — ApplyDescription `$type` labels confirmed working
- [`data/node_port_schema.json`](../data/node_port_schema.json) — full input/output port names per template

---

## 1. The 6-layer architecture

Scene Nodes is a stack. Understanding the layers is the difference between "I'm typing things in and hoping" and "I know exactly which surface to touch."

```
LAYER 6: CAPSULES (object-tree action layer)              ← what artists touch
   │  classic-object wrapper around a Scene Nodes graph
   │  plugin IDs: 5171 Capsule, 180420400 SN Deformer,
   │              180420500/600/700 SN Generator,
   │              440000274 Capsule Field, 1057221 Sim Scene
   │  artist drags from Asset Browser → drops on host → adjusts via UD

LAYER 5: DESCRIPTION DSL (the declarative authoring format)
   │  GraphDescription.ApplyDescription(graph, dict)
   │  Special keys: $type, $name, $space, $id, $language, $description,
   │                $query, $commands, $ref
   │  ★ $type accepts ONLY case-sensitive English UI labels
   │  ★ Canonical asset IDs (net.maxon...) do NOT work as $type

LAYER 4: GRAPH NODES (live instances)
   │  GraphNode class — what GetRoot/GetChildren return
   │  GetId() → instance name (e.g. "memory@HASH" or local sub-name)
   │  GetType() → kind = "net.maxon.graph.graphnode" (uniform across all)
   │  GetInputs/GetOutputs → port lists (children of kind 4 / 2)
   │  GetKind() → 1=node, 2=output port, 4=input port

LAYER 3: GRAPH MODELS (the graph instance)
   │  NodesGraphModelInterface, NodesGraphModelRef
   │  GraphDescription.GetGraph(host) — fetch-or-create
   │  Doc-level: GraphDescription.GetGraph(doc)
   │  Per-object embedded: GraphDescription.GetGraph(capsule_obj)

LAYER 2: NODE SPACES (dialects)
   │  NodeSpaceIdentifiers.SceneNodes = "net.maxon.neutron.nodespace"
   │  NodeSpaceIdentifiers.StandardMaterial / RedshiftMaterial
   │  Each space has its own template registry; templates from one space
   │  can't be used in another

LAYER 1: ASSET REGISTRY (the template library)
   │  maxon.AssetInterface.GetUserPrefsRepository()
   │  FindAssets(AssetTypes.NodeTemplate.GetId(), ...) → 802 templates
   │  Three families:
   │    • net.maxon.neutron.* — core scene-nodes primitives + ops (~250)
   │    • com.redshift3d.* — Redshift material nodes (~158)
   │    • net.maxon.node.* — math/array/utility (~160)
   │    • + user/community assets w/ UUIDs (~30)
```

### Critical distinction: NodeTemplate vs Keyword

The Asset Browser shows BOTH node templates AND search-tag keywords. They look identical, but:

- `net.maxon.node.assettype.nodetemplate` ← addable via `$type`
- `net.maxon.assettype.keyword` ← search categories, NOT addable

**Example trap:** "Set Selection" is a keyword (`keyword@0656a456b8bf...`), not a template. The actual template that does set-selection is labeled "Store Selection". Labels that look reasonable but fail with "not associated with any IDs" likely target keywords.

---

## 2. The 22 codified patterns

Every pattern was extracted from a real-world capsule we dissected. Each entry tells you what nodes are involved, what algorithm runs, and where it's been observed.

### 2.1 Loop scaffolding

| Pattern | Min nodes | Required | Observed in |
|---|---|---|---|
| `loop_over_indices` | 4 | Range, Loop Carried Value × N, Get Count | Many capsules |
| `loop_over_polygons` | 8 | + Get Polygon Selection Data, Read Value At Index | Partition Modifier (102), Explode Spline (444) |
| `loop_over_points` | 6 | + Get Vertex Selection Data | Squiggle Spline (37) |
| `loop_over_spline_segments` | 6 | + Line Get, Assembler | Squiggle, Balloon Inflate |

### 2.2 Time + state

| Pattern | Min nodes | Required | Observed in |
|---|---|---|---|
| `reaction_diffusion_on_geometry` | 12 | 2× Memory, Arithmetic, Blend, Set/Get Property | Squiggle Spline |
| `memory_capsule_state_carrier` | 1 | Memory + 2 floatingio | Memory_Nodes (Store For Next Frame) |
| `iterative_simulation_via_memory_and_classic_tools` | 1+ | Memory + classic-tool stack | Memory_Nodes (4 simulation styles) |
| `per_vertex_property_storage` | 2 | Set Property + Get Property | Squiggle, Ivy |

### 2.3 Stochastic + iteration

| Pattern | Min nodes | Required | Observed in |
|---|---|---|---|
| `surface_clinging_growth` | 100 | closestpointonsurface, Ray, 4× Loop Carried Value, matrixfromaxis | Ivy Generator (668) |
| `stochastic_branching_decision` | 4 | Hash, Compare, If/Switch | Ivy, Fractal Trees, Geo Feedback |
| `fractal_recursion_via_stacking` | N/A | One template stacked N× | Fractal Trees (7-11×) |

### 2.4 Selection production

| Pattern | Min nodes | Required | Observed in |
|---|---|---|---|
| `modular_polygon_selection` | 50 | polygonarrayget, containeriteration, modulo, compare, switch, buildselection | Modulo capsule (84) |
| `hash_threshold_selection` | 70 | polygonarrayget, range, hash, compare, switch, active, type, setselection, invertselection | Random Selection (80) |
| `selection_evolution_chain` | 5 | Random Selection, Grow Selection, Invert Selection, Set Selection | City Generator |
| `mesh_element_query_by_selection` | 30 | selectionstringparser, getpolygonselectiondata, *frompolyids family | Edge to Spline (69) |

### 2.5 Geometry transformation

| Pattern | Min nodes | Required | Observed in |
|---|---|---|---|
| `dual_mesh_topology_transform` | 100 | polygoninfo, pointinfo, indexarrayfromstring, melt, polygonbevel, triangulate | Dual Mesh (138) |
| `partition_split` | 100 | distancetoline, clamp, polygonarrayset, subdivide, hash | City Generator partition |
| `spline_break_by_threshold` | 10 | addcontrolpointalongspline, splitspline, sort, compare | Balloon Inflate angle/length |
| `spline_resample_with_displacement` | 8 | Resample, splinedistnode, step, noise, time | Balloon Inflate electric |

### 2.6 Distribution + scatter

| Pattern | Min nodes | Required | Observed in |
|---|---|---|---|
| `procedural_surface_scatter` | 1 | Surface Blue-Noise OR Surface Scaled Blue-Noise | (THE killer scatter primitive) |
| `object_instancing_with_variation` | 12 | Object Import, Cloner, multransform, Hash, sqrtrans, vectrans | Time Offset (106) |

### 2.7 Doc-level scene building

| Pattern | Min nodes | Required | Observed in |
|---|---|---|---|
| `doc_level_procedural_scene_builder` | 13 | Primitive(s) + 6-node transform stack + algorithm operator | 6 numbered scenes (045437-045802) |

### 2.8 Sub-patterns (recurring building blocks)

| Sub-pattern | Found in |
|---|---|
| `named_selection_storage_triad` (selectionstringtoselection + selectionoperator + variadictolist) | Modulo (×6), Random Selection (×6), most modeling-op capsules |
| `dynamic_selection_naming` (string.concat for runtime-named selections) | Dual Mesh |
| `element_mode_dispatch_triplet` (3× active + 3× setselection — poly/edge/point) | Random Selection, Modulo |

---

## 3. The 39 verified `$type` labels

These are the labels confirmed to work as `$type` in `ApplyDescription` calls. Source: live probes against C4D 2026.2 user prefs repo.

```
LOOP / TIME / STATE
  Range                    → range
  Loop Carried Value       → loopcarriedvalue
  Memory                   → memory
  Get Count                → getcount
  Time                     → time
  Noise                    → mainnoise

LOGIC / MATH (POLYMORPHIC — datatype port)
  Hash                     → hash
  Compare                  → compare
  If                       → if
  Switch                   → switch
  Boolean Operator         → booleanoperator
  Arithmetic               → arithmetic       (Add/Sub/Mult/Div absorbed via 'operation' port)
  Scale                    → scale
  Round                    → round
  Clamp                    → clamp
  Blend                    → blend
  Step                     → step
  Negate                   → negate
  Invert                   → invert
  Distance                 → distance
  Normalize                → normalize

VECTOR / MATRIX
  Compose Matrix           → composematrix
  Decompose Matrix         → decomposematrix
  Dot Product              → dot
  Cross Product            → cross
  Vector Length            → length

ARRAY
  Build Array              → buildfromvalue

PRIMITIVES
  Sphere                   → sphere
  Cube                     → cube
  Tube                     → tube

MODELING OPERATORS
  Inset                    → inset
  Extrude                  → extrude
  Subdivide                → subdivide

SELECTION
  Random Selection         → randomselection
  Grow Selection           → growselection
  Store Selection          → setselection
  (NOT "Set Selection" — that's a keyword, not a template)

SPLINE
  Resample Spline          → resample

DISTRIBUTION (THE killer scatter primitives)
  Surface Blue-Noise       → surfacebluenoise              (10 inputs, distdataout)
  Surface Scaled Blue-Noise → surfacescaledbluenoise       (14 inputs, distdataout)
```

### Polymorphic-node insight

The "missing" math nodes — Add, Subtract, Multiply, Divide, Power, Abs, Modulo (math), Length (scalar), Compose Vector 3 — are **not separate templates**. They're absorbed into polymorphic dispatchers:

- `Arithmetic` has `operation` + `datatype` + `in1`/`in2`/`in3` → covers Add/Sub/Mult/Div/Pow/Abs/Mod
- Same shape for Compare/If/Switch/Scale/Step/Round/Clamp/Negate/Invert/Distance/Normalize
- `Hash` exposes 7 typed outputs (`resultfloat`, `resultvector3`, `resultcolor`, `resultint`, `resultbool`, `resultvector2`, `resultcolora`) — pluggable per consumer's expected type

So when a dissection shows `add@HASH`, that's an Arithmetic instance with `operation=add`. **Add is a port value, not a node.**

---

## 4. Port-type taxonomy + conversion paths

When wiring two nodes, port types must match (or have an automatic conversion path).

```
Float64    ← Int (auto), Vector (vectortofloat or component port), Bool (auto)
Vector     ← Float64 (composevector3 OR set ONE component leave others 0)
Vector4d   ← Vector (extend with 1.0 alpha)
Matrix     ← Vector (composematrix or matrixfromaxis), Vec[3] (vectorstomatrix)
Geometry   ← Spline (tessellation)
Spline     ← Geometry (edge_to_spline pattern)
Selection  ← String (selectionstringtoselection), Bool array (buildselection)
Container  ← Array (composecontainer with field names)
Array      ← Single value (buildfromsinglevalue), Container (decomposecontainer)
Bool       ← Float64 (compare e.g. > 0), Int (auto)
Int        ← Float64 (round/modulo/cast)
String     ← any: NO automatic conversion
```

### Range port truth (the canonical loop driver)

```
Range INPUTS:  start, end, domain
Range OUTPUTS: count, out, innerdomain, outerdomain
```

NOT `from`/`to`/`step` — those don't exist. Use `start`/`end` and connect a separate node for step if needed.

---

## 5. Anti-patterns + traps

### 5.1 Don't pass canonical asset IDs to `$type`

```python
# WRONG — even though this is the real asset ID
ApplyDescription(g, {"$type": "net.maxon.neutron.node.primitive.sphere"})
# → "is not associated with any IDs"

# RIGHT — case-sensitive English label
ApplyDescription(g, {"$type": "Sphere"})
```

### 5.2 Don't assume dissection bare-names work as `$type`

Dissection returns local graph instance names (e.g. `extrude@HASH` → bare `extrude`). These are NOT addable as `$type` labels. The label form is what works (`Extrude`, capitalized).

```python
# WRONG — bare-name from dissection
ApplyDescription(g, {"$type": "extrude"})
# → "is not associated with any IDs"

# RIGHT — UI label
ApplyDescription(g, {"$type": "Extrude"})
```

### 5.3 Don't try to add framework sub-nodes directly

`parambuilder`, `modelingoperator`, `generategeometry`, `defaultselections`, `containeriteration`, `selectionoperator`, `selectionstringtoselection` are **framework sub-nodes** auto-emitted inside compound operators. They're NOT addable at top-level.

```python
# WRONG
ApplyDescription(g, {"$type": "Container Iteration"})
# → "is not associated with any IDs"

# RIGHT — add the OUTER operator (e.g. Inset, Extrude, a selection capsule),
# the framework auto-emits these inside it.
```

### 5.4 Don't run GraphDescription from a worker thread

```python
# WRONG — silent error
def my_handler():
    g = GraphDescription.GetGraph(doc)  # errors: "must be run from main thread"

# RIGHT — wrap in main-thread routing
def _do():
    return GraphDescription.GetGraph(doc)
g = self.execute_on_main_thread(_do, _timeout=15)
```

### 5.5 Don't expect recursion within a single graph

Scene Nodes is NOT a recursive language. To achieve "fractal" behavior, **stack N copies** of a deformer (like Fractal Trees stacks "Branch Spline Modifier" 7-11×) or use a depth-bounded loop with `loopcarriedvalue` + `range`.

---

## 6. Architectural design principles

These are the rules that should drive ALL programmatic synthesis going forward.

### 6.1 Capsule-first

**Rule:** Scene Nodes graphs exist to be CONSUMED by the object tree as Generators / Deformers (capsules). The artist deliverable is a registered Capsule asset (NodeTemplate type) where Floating IOs surface as Attribute Manager parameters.

**Why:** Artists work in the object tree. The graph editor is a low-level engineering surface, not a creative one. Once a graph is built, the artist's relationship to it is "drag-and-tune via parameters."

**Implication:** Every functional pattern should default to materializing INSIDE an SN Generator/Deformer wrapper. Floating IO nodes are the routing primitive that, once the wrapper is published as a registered NodeTemplate asset, surface as AM parameters.

**Status (2026-04-30):** The Python imperative API can build the graph + connect typed wires + save as a File-type asset (round-trippable). It CANNOT yet (a) add named ports to a Floating IO programmatically, or (b) publish the graph as a NodeTemplate-typed asset that surfaces FIO routing as AM params. Both gaps are mapped to a planned C++ shim plugin — see §8.

### 6.2 Selection-capsule bidirectionality

**Rule:** Selection-producer capsules (Random Selection, Modulo, Set Selection, etc.) are BIDIRECTIONAL bridges between Scene Nodes graphs and the C4D object tree. Their named output selections live in BOTH worlds simultaneously.

**Two outputs from one input:**
1. **Inside the graph**: the named selection flows as a value to downstream nodes (Inset, Extrude, etc.)
2. **Out to the object tree**: the same selection materializes as a classic Polygon/Edge/Vertex Selection Tag on the host object — consumable by Materials' Restriction, MoGraph Cloner's Selection field, the Reduce deformer, Restriction Tags, viewport selection display, ANY classic C4D system.

**Why this matters:** the Selection Tag is the protocol every classic C4D surface speaks. So when a Scene Nodes capsule emits a named selection, the artist gets it in BOTH paths simultaneously. The graph is invisible after dropdown; the tag is the artist-facing artifact.

**Implication:** every selection-producer pattern should expose its selection-name as a `floatingio` UD parameter so the artist can rename + re-target without re-opening the graph.

### 6.3 Simulation = Memory + classic-tool mutator

**Rule:** Time-varying simulations in C4D 2026 are NOT a separate subsystem. They're `previous_state → [classic-tool stack] → next_state` wrapped in a Memory node loop.

**Pattern:**
```
[Store For Next Frame capsule] (1× Memory node)
   .current  →  [classic-tool mutator: Volume Builder / Cloner / Deformer / Field]
                      ↓
                 next_state
                      ↓
   .next     ←  ────────
```

**Memory_Nodes example scene proves it** with 4 distinct simulations sharing the same single-Memory primitive:
- VDB Growth (Volume Builder + Sphere Instance + Displacer)
- Soft-body Collision (Cloner + Collision deformer + Jiggle)
- Spline Push Apart (Push Apart Field + Tracer + Resample Spline)
- Directional VDB Growth (+ Plain Effector for direction)

**Implication:** to give artists "simulation" capability programmatically, we don't need to invent a new system. We just need `apply_pattern("iterative_simulation_via_memory_and_classic_tools")` that wraps their chosen mutator stack in a Memory loop.

---

## 7. How to use the MCP for Scene Nodes work

The cinema4d-mcp plugin ships **9 scene-nodes-specific tools**. Use them in this rough order:

### 7.1 Discovery / inspection

| Step | Tool | What it does |
|---|---|---|
| 1 | `scene_nodes_status` | Tells you if the doc has a graph at all (returns nimbus_refs + per_object_graphs) |
| 2 | `scene_nodes_walk(target_object?, max_depth)` | Returns the graph tree as nested JSON — node IDs, kinds, port counts, hierarchy |
| 3 | `scene_nodes_dissect_capsule(target_object?, max_depth)` | Auto-scans for capsule-class objects, walks each one's embedded graph, returns the full asset_ids list |
| 4 | `scene_nodes_classify_graph(target_object?, max_depth)` | Walks + builds histogram + matches against 13 pattern signatures + returns probable_purpose |
| 5 | `scene_nodes_atlas_lookup(query, kind)` | Search 802 templates / 22 patterns / port_types / antipatterns / vocabulary classes by substring |
| 6 | `scene_nodes_describe_node_template(label)` | Add a node, walk its ports, remove it. Returns full input/output port schema |

### 7.2 Authoring

| Step | Tool | What it does |
|---|---|---|
| 7 | `scene_nodes_apply_pattern(pattern_name, params, dry_run?)` | Synthesize a known pattern's nodes into a graph. `dry_run=True` returns the spec without applying |
| 8 | `scene_nodes_add_node(asset_id, ...)` | Add a single node by label (the basic primitive — apply_pattern is preferred for multi-node) |
| 9 | `scene_nodes_connect_ports(from_node, from_port, to_node, to_port)` | Wire two nodes by name |

### 7.3 Recommended workflow for "build me X with Scene Nodes"

```
1. Decide which pattern fits the user's intent.
   → scene_nodes_atlas_lookup query="<intent>" kind="pattern"

2. Inspect what's already in the doc.
   → scene_nodes_status     (any existing graph?)
   → scene_nodes_walk       (if yes, what's there?)

3. If wrapping in a capsule (recommended per capsule-first principle):
   → Insert SN Generator (180420700) or SN Deformer (180420400) via execute_python
   → scene_nodes_apply_pattern with graph_target=<new capsule name>

4. Validate the result.
   → scene_nodes_classify_graph target=<new capsule name>
   → Confirm probable_purpose matches your intent

5. Connect ports as needed.
   → scene_nodes_connect_ports from=<x> to=<y>

6. Materialize as artist-ready capsule (CURRENTLY UI-GATED).
   → Adding a floatingio node alone does NOT surface its connected param in
     the Attribute Manager. AM-param surfacing requires the inner graph to
     be saved as a NodeTemplate-typed asset (.c4dnodes format), which is
     not exposed in the Python imperative API. CreateObjectAsset saves a
     File-type asset (.c4d) — the graph round-trips bit-identically but
     no new AM params appear. See §8 for full details + the C++ shim path.
```

---

## 8. The Floating IO / capsule Attribute Manager bridge

**Cracked 2026-04-30** through three rounds of live probing + GPT 5.5 review + dissection of Edge to Spline (the gold-standard reference because Maxon's engineers shipped it with 5 working FIOs inside).

### Layer separation: 777 ≠ AM-params

Earlier we conflated "DescIDs under root 777" with "what artists see in the Attribute Manager." Three rounds of probing proved they are different things:

- **Root 777 is Scene Nodes editor metadata.** Always 12 entries, layout fixed across every SN Generator regardless of inner graph contents. The hash `BrM5f_dgHBXvK6gQuZ3cQA` is a Maxon-shipped placeholder, NOT per-instance.
- **AM-params live under capsule-asset-specific roots** (e.g. spline params at 1000–1005, 4000 for SplineObject-derived capsules; transform at 800–933 for BaseObject-derived; etc.).
- **AM-param visibility is governed by the host capsule's REGISTERED CLASS, not by the inner graph.** A generic SN Generator wrapper (180420700) does NOT auto-surface its inner FIOs as AM params — that requires the capsule to be saved as a registered asset.

### Floating IO node — what it actually is

A FIO is a **routing node** with three semantic slots:
- 1× input port `net.maxon.node.floatingio.portlist` — a `void` template port (always present, even on bare FIOs, never has children at the FIO root)
- N× input port `hiddenin1.<canonical.attribute.path>` — picks up a value from outside-scope (the host node's input)
- N× output port `in1.<canonical.attribute.path>` — distributes that value to inside-scope consumers

The N pairs of `hiddenin1` / `in1` ports are **named after the canonical attribute path** they route. For Edge to Spline's `reverse` parameter:
- `hiddenin1.net.maxon.nodes.scene.geo.spline.generator.edgetospline.reverse`
- `in1.net.maxon.nodes.scene.geo.spline.generator.edgetospline.reverse`

Plus three node-attribute fields readable via `node.GetValue(maxon.InternedId(...))`:
- `net.maxon.node.floatingio.attribute.direction` (Bool: `false` = input direction, `true` = output direction)
- `net.maxon.node.floatingio.defaultname.inputs` (String, optional UI override)
- `net.maxon.node.floatingio.defaultname.outputs` (String, optional UI override)

### What the imperative API CAN'T do (verified 2026-04-30)

- **`graph.AddPorts(parent, index, count)`** fails with `Illegal argument: Condition variadic & PORT_FLAGS::VARIADIC_TEMPLATE not fulfilled` on both the FIO node AND its PORTLIST port. PORTLIST is `void`-typed, not flagged variadic-template at this layer.
- **`port.Connect(other_port)` to/from PORTLIST** silently no-ops (returns no error but no wire is created and PORTLIST stays empty). The auto-specialization that the C4D UI does on drag-wire is NOT exposed in the imperative API.
- **The C++ singular `AddPort(parent, Id name)`** at `graph.framework/source/maxon/graph.h:891` would in theory let us add named ports directly; only the plural `AddPorts` is wrapped in the Python frameworks module.
- **No Python entry point** to "save inner graph as capsule asset" was found in `c4d.modules` or `maxon.frameworks.{nodes,graph,nodespace,asset}`.

### Bottom line

Building a user-tunable Capsule generator with custom AM parameters is **a UI-driven workflow**, not a Python-imperative one. The Python path can:
- Build inner graphs via `GraphDescription.ApplyDescription`
- Connect existing typed ports via `BeginTransaction` + `port.Connect()` + `Commit`
- Read static metadata via `GetDescription` / `enumerate_descids`

But cannot (with the API surface we've mapped):
- Auto-emit `hiddenin1.<canonical>` + `in1.<canonical>` named ports on a FIO
- Register the inner graph as a typed asset class
- Surface inner FIOs as AM parameters on a generic SN Generator wrapper

### Path forward — what we've added 2026-04-30 (post-CreateObjectAsset session)

**Confirmed Python entry points that DO work:**
- `maxon.AssetCreationInterface.CreateObjectAsset(op, doc, sas, id, name, version, meta, bool)` — saves an SN Generator + its embedded graph as `net.maxon.assettype.file` asset. Returns an `AssetDescription` with the new asset's ID + URL.
- `maxon.AssetManagerInterface.LoadAssets(repo, [(id, '')], None, None)` — programmatic equivalent of asset-browser-drag. Inserts the asset's content into the active doc.
- `maxon.StoreAssetStruct(parentCategory_id, lookup_repo, save_repo)` — settings for where to save.
- Asset-type registry exposed: `AssetTypes.{File, NodeTemplate, NodeContext, NodeSpace, NodeDescription, NodeDefaultsPreset, DocumentPreset, UserDataPreset, SubType}`.
- 32 methods total on `AssetCreationInterface`: `CreateObjectAsset`, `CreateSceneAsset`, `CreateMaterialAsset`, `SaveDocumentAsset`, `SaveBaseDocumentAsAsset`, `OpenSaveAssetDialog`, `SaveDefaultPresetFromObject`, `SaveBrowserPreset`, `UpdateMetaData`, etc.

**The unresolved gap:** None of the exposed Python entry points produce a `net.maxon.node.assettype.nodetemplate`-typed asset (the `.c4dnodes` format used by Maxon's shipped capsules like Edge to Spline). NodeTemplate registration is plugin-init-only in the Python surface. `CreateObjectAsset` saves File-type (round-trippable but no AM-param surfacing); no Python-callable `CreateNodeTemplateAsset` exists.

**Concrete path forward (planned C++ shim):**
1. Build a small C4D `.cdl64` plugin (alongside `mcp_server_plugin.pyp`) that wraps:
   - `GraphModelInterface::AddPort(parent, Id name)` (singular, named — graph.h:891) — for adding `hiddenin1.<canonical>` / `in1.<canonical>` ports on a Floating IO.
   - The native NodeTemplate publishing path — likely `AssetTypes::NodeTemplate()` + `CreateAsset` in C++ — for registering the saved graph as a `.c4dnodes` asset.
2. Expose those as new MCP commands routed through the existing plugin's socket: `scene_nodes_add_floating_io_port`, `scene_nodes_publish_capsule_asset`.
3. Python-side build flow: synthesize graph → connect typed wires → add named FIO ports via shim → publish as NodeTemplate via shim → asset auto-surfaces in Asset Browser with FIO routing → AM params appear when artist drags the new asset.

**Already shipped 2026-04-30 (File-type roundtrip — useful even without NodeTemplate):**
- `scene_nodes_save_as_asset` MCP handler — wraps `CreateObjectAsset`. User-built graphs become reusable file assets. Round-tripping preserves bit-identical FIO state.
- `scene_nodes_load_asset` MCP handler — wraps `LoadAssets`. Reload by ID.
- Use cases this unlocks even pre-shim: pattern libraries, curated graph templates, programmatic workflow capture.

### How `enumerate_descids` surfaces 777 entries

Every entry under root 777 returns:
- `path`: the raw level-id list (e.g. `[777, 1852142638, ...]`)
- `decoded_path`: the human-readable form (e.g. `"<777>/net./maxo/n.no/..."`)
- `instance_hash`: extracted hash segment when present (4-char tokens not in canonical vocab)
- `semantic_guess`: heuristic classification (`group_inputs`, `group_outputs`, `instance_hash_leaf`, etc.)
- `current_value` OR `current_value_error` per-row (whole row preserved on read failure)

### The 12 standard groups under root 777
| Decoded path | Dtype | Semantic |
|---|---|---|
| `net.maxon.datadescription.editor.1` | 1 | editor metadata |
| `net.maxon.node.base.<HASH>/<1>` | 11 | static placeholder (NOT per-instance) |
| `net.maxon.node.base.filtertags/<1>` | 130 | filter tags string |
| `net.maxon.node.base.category/<1>` | 15 | node category int |
| `<777>/<1>` | 12 | (anonymous) |
| `net.maxon.node.base.group.basic/<1>` | 1 | **Basic** group folder |
| `net.maxon.node.base.group.coord/<1>` | 1 | **Coord** group folder |
| `net.maxon.node.base.group.inputs/<1>` | 1 | **Inputs** group folder |
| `net.maxon.node.base.group.outputs/<1>` | 1 | **Outputs** group folder |
| `geom/etry/out` | 12 | geometry output |
| `net.maxon.node.base.group.object/<1>` | 1 | **Object** group folder |
| `net.maxon.render.node.base.group.context/<1>` | 1 | **Context** group folder |

---

## 9. Known unresolved labels

These templates exist (confirmed via dissection) but no working `$type` label has been found. They're either keyword-only, framework-sub-node-only (auto-emitted, not addable), or need a label form we haven't tried.

```
append, append2, concat, readvalueatindex, writevalueatindex,
containeriteration, pushapart, lineget, assembler,
addcontrolpointalongspline, splitspline, set_property, get_property,
closestpointonsurface, ray, composevector3, matrixfromaxis,
*frompolyids family (ptposfrompolyids etc.),
*fromptids family (selfromptids etc.)
```

Patterns that reference any of these will fail at apply time. Track in `c4d_plugin/scene_nodes_patterns.py` `UNVERIFIED_LABELS_USED_BY_PATTERNS` set.

If you hit one and find the label, add to `data/verified_labels.json` and `VERIFIED_LABELS` dict in patterns module.

---

## 9. Source data + maintenance

The atlas data is the single source of truth. To add a new pattern:

1. Dissect a real capsule via `scene_nodes_dissect_capsule`
2. Identify the unique vocabulary signature
3. Add a pattern entry to `data/scene_nodes_atlas.json` under the `patterns` key
4. Add a synthesizer function to `c4d_plugin/scene_nodes_patterns.py` decorated with `@pattern(...)`
5. Add a smoke recipe in `tests/recipes/`
6. Run contract tests + sync to installed plugin

The atlas is meant to grow organically with every new capsule we crack.

---

## Appendix A: Capsule plugin IDs (for dissection auto-scan)

```
5171      = Capsule (canonical wrapper)
180420400 = Scene Nodes Deformer
180420500 = Scene Nodes Generator (variant A)
180420600 = Scene Nodes Generator (variant B)
180420700 = Scene Nodes Generator (variant C)
440000274 = Capsule Field
1057221   = Simulation Scene
```

## Appendix B: Framework nodes (auto-generated, never user-added)

Every functional capsule wraps any user logic with these 6 framework nodes:

```
context_externaltimeinput  — animated time context (frame, scene_time, fps)
context_notime             — time-less / frozen evaluation context
parambuilder               — wraps user-facing params from the BaseObject
modelingoperator           — wraps a graph as a classic-object operator
generategeometry           — output sink for generators
defaultselections          — fallback selection sets when none is named
```

Plus visual organization (no functional output):

```
scaffold     — clickable visual frame around regions
annotation   — sticky note / comment
group        — collapsible bubble
reroute      — visual cable redirect (single in, single out, identity)
floatingio   — graph boundary I/O (exposes a port to parent capsule's UD)
text         — text label on canvas
type         — type declaration scaffold
```

## Appendix C: Example dissection results (size + pattern fingerprint)

| Capsule | Plugin ID | Inner nodes | Unique bases | Levels | Dominant pattern |
|---|---|---|---|---|---|
| Squiggle Spline | 180420700 | 37 | 31 | 5 | reaction_diffusion_on_geometry (2× memory) |
| Time Offset (doc-level) | n/a | 106 | 69 | n/a | object_instancing_with_variation |
| Explode Spline Segments | 180420400 | 444 | 346 | 7 | massive loop_over_polygons (11× loopcarriedvalue) |
| Scaffolds (educational) | 180420600 | 26 | 25 | 3 | scaffold-demo |
| Ivy Generator | 180420500 | 668 | 106 | 8 | surface_clinging_growth |
| Edge to Spline | 180420700 | 69 | 40 | 4 | mesh_element_query_by_selection |
| Modulo | 180420400 | 84 | 56 | 6 | modular_polygon_selection |
| Random Selection | 180420400 | 80 | 55 | 5 | hash_threshold_selection |
| Dual Mesh Modifier | 180420400 | 138 | 46 | 7 | dual_mesh_topology_transform |
| Store For Next Frame | 180420600 | 4 (1 real) | 1 | 2 | memory_capsule_state_carrier |

---

*This guide is a living document. Each new capsule we dissect adds vocabulary, refines patterns, or exposes new principles. PR contributions welcome.*
