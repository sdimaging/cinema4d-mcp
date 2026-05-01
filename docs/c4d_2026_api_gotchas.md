# C4D 2026 API Gotchas — discoveries from MCP development

A reference sheet of things the C4D 2026 Python API does that don't match
older docs / tribal knowledge / what felt right. Each entry has the wrong
assumption, the actual behavior, and how it was discovered.

Maintained as bugs surface during MCP plugin development. Useful for
anyone building agent integrations against C4D 2026.

---

## 56. Plugin ID 180420500 (Scene Nodes Generator) uses the **Neutron** nodespace, NOT `net.maxon.nodespace.scene`

**Discovered 2026-05-01** while studying the DRuckli `Reaction_Diffusion`
scene. Both hosts in that scene are type `180420500` ("Scene Nodes
Generator"). The expected access path:

```python
nbr = host.GetNimbusRef("net.maxon.nodespace.scene")
# returns None — wrong nodespace for this plugin
```

The actual access path:

```python
NEUTRON = "net.maxon.neutron.nodespace"
nbr = host.GetNimbusRef(NEUTRON)
ng = nbr.GetGraph(maxon.NODE_KIND.NODE)
root = ng.GetViewRoot()
```

Confirmed via `host.GetAllNimbusRefs()` which returned a single tuple
`(maxon.Id "net.maxon.neutron.nodespace", NimbusBaseRef ...)`.

### Updated nodespace-by-plugin-ID table

| Plugin ID | Plugin Name | Nodespace |
|---|---|---|
| 180420400 | Nodes Modifier (Deformer) | `net.maxon.nodespace.scene` |
| 180420500 | **Scene Nodes Generator** | **`net.maxon.neutron.nodespace`** |
| 180420600 | Nodes Mesh simple | `net.maxon.nodespace.scene` |
| 180420700 | Nodes Spline | `net.maxon.nodespace.scene` |

### Why this matters

The two nodespaces share UI conventions (graph editor, AM-exposed sliders,
floatingio ports) but have **different node libraries and different
canonical output sinks**. In `nodespace.scene`, the output emit is
`set_property → root.geometryout`. In `neutron.nodespace`, it's
`geometry@ → root's geometry input port`.

Other Neutron-specific quirks observed in the same scene:

- `memory@` primitive (per-frame state retention via self-feedback wire
  `out._0 → in._0`) — unique to Neutron.
- `nearestneighbor@` for K-NN spatial queries (also exists in scene
  nodespace but the surrounding port idioms differ).
- `getvertexselectiondata@` reads C4D Field-painted vertex selections
  from the input geometry — the Field-to-graph bridge.
- `containeriteration@` per-vertex iterator pattern.
- AM-slider names come back as `(unnamed)` from `host.GetDescription()`
  because Neutron's descid encoding doesn't surface DESC_NAME at leaf
  level; read from `floatingio` nodes' `effectivename` instead.
- Annotations are **not** OM-tag-based; they're encoded as
  `effectivename` on `scaffold@` nodes inside the graph (acting as
  graph-internal section headers).

### How to discover

```python
all_refs = host.GetAllNimbusRefs()
for nodespace_id, nbr in all_refs:
    print(nodespace_id, "->", nbr)
```

This returns the actual nodespace this host uses. **Always probe
GetAllNimbusRefs() before assuming a nodespace ID.**

---

## 55. THE NODES-FAMILY OUTPUT BRIDGE — Object Manager-bridged Scene Nodes containers come in 4 plugin variants, each with its own root-output recipe

**SUPERSEDES gotcha #54** (which incorrectly declared a "wall"). The "wall"
was wrong-plugin-ID — sampled 500/700, missed 600. **The output bridge IS
exposed in Python.** Cracked 2026-05-01 via Spenser's manual-baseline
diagnostic protocol.

**The Nodes family** (per C4D 2026 Command Manager — IDs 465002502-465002505):

| Container | Plugin ID | Root output port(s) | Bridges geometry kind |
|---|---|---|---|
| **Nodes Mesh** | `180420600` | `geometryout` | polygon mesh ↔ Object Manager |
| **Nodes Modifier** | `180420400` | `geometryout` (+ root.in `geometryin`) | deformer (modifies parent) |
| **Nodes Spline** | likely `180420500` or sibling | (probe needed) | spline object |
| **Nodes Selection** | (probe needed) | selection array | selection capsule |

### Recipe — Nodes Mesh (canonical, proven 2026-05-01)

```python
mesh = c4d.BaseObject(180420600)
doc.InsertObject(mesh)
mesh.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)
graph = mesh.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes).GetGraph()
root = graph.GetViewRoot()

# Add geometry-producing nodes (Cube, Sphere, Tube, modeling chains, etc.)
maxon.GraphDescription.ApplyDescription(graph, {"$type": "Cube", "$name": "my_cube"})

# Find ports
cube = <walk root.GetChildren() for cube@*>
cube_out = <find "geometryout" in cube.GetOutputs()>
root_geomout = <find "geometryout" in root.GetOutputs()>

# Wire DIRECTLY — no scene.root, no op.geometry wrapper, no variadic AddPort
with graph.BeginTransaction() as txn:
    cube_out.Connect(root_geomout)
    txn.Commit()

# Visible cube in viewport AND in Object Manager. host.GetCache() returns
# PolygonObject (type 5100). Done.
```

### Recipe — Nodes Modifier (acts on parent geometry)

The Nodes Modifier is a CHILD of the geometry it deforms. Its root has both
`geometryin` (host→graph: parent's geometry flows in) and `geometryout`
(graph→host: deformed result flows out).

```python
mod = c4d.BaseObject(180420400)
parent_cube.InsertUnder()  # mod must be child of target
# Inside graph:
# root.geometryin → first_modeling_op.geometryin
# (chain modeling ops via geometryout→geometryin)
# last_modeling_op.geometryout → root.geometryout
```

Verified working in `Untitled 4` reference scene: cube + Nodes Modifier
child with internal `extrude → root.geometryout` wiring.

### Recipe — doc-level Scene Nodes (no Object Manager bridge)

If you want to build EVERYTHING inside a doc-level SN graph (no OM bridge),
the recipe is different:

```python
graph = maxon.GraphDescription.GetGraph(doc)
# scene.root is auto-present in every doc-level graph (cannot delete)
# Add Cube + the "geometry" wrapper op + wire to scene.root.children._0
maxon.GraphDescription.ApplyDescription(graph, {"$type": "Cube", "$name": "c"})
maxon.GraphDescription.ApplyDescription(graph,
    {"$type": "#net.maxon.neutron.op.geometry", "$name": "geom"})

# Wire: cube.geometryout → geom.geometry → scene.root.op.objectbase.children._0
# (variadic _0 slot exists by default — no AddPort needed for first connection)
```

This renders in viewport but does NOT create an Object Manager entry.

### Why all my prior probes failed (sept 2026-05-01 discovery)

- I tested **180420500** (had complex `objectinput`/`op.input`/`op.objectbase`
  port set) — it does NOT have a simple geometryout output. Was the wrong
  plugin variant.
- I tested **180420700** with the Nodes Mesh recipe — got `cache=None`.
  Same simple `geometryout` port shape as 600 but doesn't render. Likely
  a different sibling variant (Nodes Spline?).
- I never tested **180420600** until Spenser's manual baseline protocol
  forced me to dissect his working setup.
- I filtered `net.maxon.neutron.scene.root` as scaffolding when it's the
  doc-level destination node.

**Lesson learned:** when probing the API surface, always include
"manual-baseline + dissect" as Step 1 before brute-forcing port hypotheses.

### Discovery process (2026-05-01)

1. Spenser's protocol: "Stop declaring the wall. Make the simplest manual
   working setup. I'll snapshot. Compare with what you'd build."
2. He dragged in the cube, it rendered. I snapshotted: doc-level graph
   had `scene.root` + `geometry@*` wrapper that I'd missed.
3. He pointed out the distinction: doc-level scene.root vs Nodes Mesh
   container. Nodes Mesh = OM-bridged.
4. He showed Command Manager: "Nodes Mesh" (ID 465002502), "Nodes
   Modifier" (465002504), "Nodes Spline" (465002503), "Nodes Selection".
5. He opened Maxon's "0100 Nodes Mesh" and "0130 Clone Onto Polygon
   Centers" reference scenes for ground truth.
6. Dissected `Mesh Primitive Group` (type 180420600) — annotation tag
   said *"This project demonstrates how to find different Mesh Primitive
   nodes (e.g. Cube, Sphere, Cone) and return their Geometry for use in
   the Objects Manager."*
7. The wiring inside: `cube.geometryout → root.geometryout`. Direct.
8. Rebuilt mine with plugin 180420600 — second cube appeared at offset
   immediately. Recipe proven.

---

## 54. ~~SN Generator output-routing wall~~ — INCORRECT, see gotcha #55

**This entry was wrong.** I claimed the SN Generator output side was
gated on Phase B C++ shim work (NodeTemplate publishing). It is NOT.
The "wall" was sampling the wrong plugin variants (180420500/700 instead
of 180420600 = Nodes Mesh).

The correct recipes — for both doc-level SN graphs AND each Nodes-family
container (Nodes Mesh / Modifier / Spline / Selection) — are in gotcha #55.

NodeTemplate publishing IS still a separate gap (relevant for surfacing
custom AM params on user-built capsules). But OM-bridged geometry output
from a generic Nodes Mesh container does NOT require it.

**Lesson:** declared "wall" without doing the manual-baseline-and-dissect
diagnostic Spenser explicitly requested. Pivoted to Path B (classic-stack
procedurality) when Path A was actually accessible. Will not repeat this
mistake.

---

## 53. SweepNurbs child order matters — profile FIRST, path SECOND. `InsertUnder` puts NEW child at TOP, so naive ordering swaps them

**Wrong:** to build a Sweep, insert the profile (cross-section) under the
sweep first, then the path (the spline being swept):

```python
profile.InsertUnder(sweep)
path.InsertUnder(sweep)   # WRONG — InsertUnder puts new child at index 0
```

This results in path-at-index-0 + profile-at-index-1, the opposite of
what Sweep expects. Visually you'll get a giant disc (the profile being
swept along the profile itself) instead of a thin tube.

**Actual:** `BaseObject.InsertUnder` always places the new object at the
**first child** position, shifting prior children down. So calling
`InsertUnder` on profile then on path leaves the path at index 0 (wrong)
and profile at index 1.

**Fix:** use `InsertUnderLast` for the second child, or insert in reverse
order:

```python
profile.InsertUnder(sweep)        # profile at index 0 ✓
path.InsertUnderLast(sweep)       # path appended after profile ✓
```

**Discovered:** 2026-05-01 building the M5 capstone (spline growth on
RD surface) — the sweep rendered as a flat pink disc until the order
was corrected.

## 52. Python-created BaseObjects sometimes ship with visibility set to `2` (UNDEF) — geometry won't render

**Wrong:** after `c4d.BaseObject(...)` + `doc.InsertObject(...)`, the
object should be visible by default.

**Actual:** in some scene contexts the new object lands with
`ID_BASEOBJECT_VISIBILITY_EDITOR = 2` and `ID_BASEOBJECT_VISIBILITY_RENDER
= 2` — the "undefined / inherit from parent" state. Whether that
inheritance resolves to visible depends on parent state and the cache
pipeline. Empirically: Volume Builder + Volume Mesher created via the
MCP `create_volume_builder` / `create_volume_mesher` handlers landed
with vis=2 and **did not render in viewport** until forced to
`vis_editor=0` + `vis_render=0`. Same for newly-created sweep nurbs
during the M5 capstone.

**Fix:** explicitly set both visibility flags after creation:

```python
obj = c4d.BaseObject(c4d.Osomething)
obj[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = 0   # default visible
obj[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = 0
doc.InsertObject(obj)
```

**Implication for cinema4d-mcp:** the
`create_volume_builder` / `create_volume_mesher` / future
`create_sweep_nurbs` handlers should auto-set visibility=0 on the new
generators (and probably also on every object the user creates via
helper handlers) so the result actually renders.

**Discovered:** 2026-05-01 during the M4 RD battle test — Volume
Mesher cache had 14080 polys but viewport stayed empty until visibility
was forced to 0. Recurred in M5 with sweep nurbs — same fix.

## 51. SN Generator's `root.objectinput` does NOT have a `geometry` subport — bare `geometryout → objectinput` produces empty Null cache

**Wrong:** wiring `last_modeling_node.geometryout → root.objectinput` on a
fresh Scene Nodes Generator should make the geometry render.

**Actual:** the connection commits successfully (verified via
`GetConnections(2)` showing 2 incoming wires on `objectinput`), but
`host.GetCache()` returns an empty `Null` (type 5140) and `host.GetRad()`
is `Vector(0,0,0)`. `objectinput` has subports `color / domain /
parentmatrix / translation / sqrmatrix / matrix` but **no `geometry`
subport** — it accepts the wire but doesn't route bare polygon data into
a renderable cache.

**Implication:** SN Generator output requires either an object-composer
node (e.g. wrapping geometry in `net.maxon.neutron.op.objectbase` or via
the dissect-known capsule output pattern) OR the existing
`scene_nodes_apply_pattern` handler that knows the proven wiring. The
direct approach (just connect the last `geometryout`) is incomplete.

**Discovered:** 2026-05-01 attempting to build a Cube → Random Selection
→ Extrude → Subdivide chain end-to-end. Gotcha #50, #49, #48 also
surfaced in the same session. Tracker: SN Generator output composition
needs follow-up research lane (sister of the synthesize_port "connection
IS the type" breakthrough — output side may have a similar discoverable
recipe).

## 50. `net.maxon.node.transformvector` is vector ARITHMETIC, not matrix×vector

**Wrong:** by name, `transformvector` should apply a matrix transform to
a vector (e.g. rotate a position by an angle-derived matrix).

**Actual:** it's a 3-input arithmetic node — `operation` (enum) +
`in1` + `in2` → `out`. Same shape as `Arithmetic` but for vectors. To
actually transform a vector by a matrix you need a **separate** matrix
construction (`Compose Matrix` from rotation/translation) followed by a
matrix-vector multiply node — not a single "Transform Vector".

**Implication:** the Nodebase R1 (Iterations for Geometry Generation)
recipe scaffold in `scene_nodes_nodebase_study.md` was wrong on this
node. Fix: use `Compose Matrix` to build the rotation, multiply, then
extract the position. Or use `Cos` + `Sin` + `Compose Vector 3` directly
to skip the matrix.

**Discovered:** 2026-05-01 walking the freshly-added node's ports:
`in1 / in2 / operation / out` — no matrix input visible.

## 49. ApplyDescription `$type` rejects bare canonical IDs — needs UI label OR `#`-prefixed canonical

**Wrong:** `scene_nodes_add_node(asset_id="net.maxon.neutron.node.range")`
should work since that's the canonical asset ID.

**Actual:** fails with
`The node type reference 'net.maxon.neutron.node.range' (lang: 'en-US',
space: 'net.maxon.neutron.nodespace') is not associated with any IDs.`

Three valid forms for `$type` (already documented in
`data/verified_labels.json` but easy to miss):

```
"Range"                              # English UI label, case-sensitive
"#net.maxon.neutron.node.range"      # canonical ID with leading #
"#~.range"                           # lazy-form shorthand
```

The bare canonical ID (no `#`) silently fails. Discovery credit:
GPT 5.5 (2026-04-30) — the `#` convention comes from Maxon's
GraphDescription docs.

**Implication:** add input validation to `scene_nodes_add_node` to detect
a bare `net.maxon.*` ID and either auto-prepend `#` or return a
descriptive error pointing to the three valid forms.

**Discovered:** 2026-05-01 first add_node attempt with `net.maxon.neutron.node.range` failed; retry with `Range` succeeded.

## 48. Scene Nodes node-template index (802 entries) misses some live-registry assets — repository scan is authoritative

**Wrong:** `scene_nodes_atlas_lookup` against the bundled
`node_template_index.json` (802 entries) is the canonical source for
asset IDs.

**Actual:** the bundled index was built from prior dissection sessions
and misses several common nodes. Examples missed:
- `net.maxon.node.containeriteration` (Iterate Collection)
- `net.maxon.neutron.geometry.polygoninfo` (Polygons Info — note
  singular, not "polygonsinfo")
- `net.maxon.neutron.geometry.get_property` / `set_property` (note
  underscore)

The live `scene_nodes_list_assets(source="repository")` call against
`maxon.AssetInterface.GetUserPrefsRepository().FindAssets()` is the
authoritative discovery path.

**Implication:** when `atlas_lookup` returns no matches for a substring,
fall back to `scene_nodes_list_assets(source="repository")` before
concluding the asset doesn't exist. Or schedule a periodic atlas
refresh that union-merges the live repository into the bundled index.

**Discovered:** 2026-05-01 — atlas had no matches for "iterate" but
repository scan found `net.maxon.node.containeriteration`.

## 47. THE BIG ONE — for Scene Nodes ports, the CONNECTION provides type binding (not the description attributes)

**Wrong:** to make an AM-exposed port draggable in a Scene Nodes Generator,
you must construct a 9-attribute schema (`fixedtype`, `portDescriptionData`,
`portDescriptionUi`, `portDescriptionStringLazy`, etc.) matching what the
Resource Editor stores. Set `classification`, `datatype`, `unit`, `guitypeid`,
build a `LazyLanguageDictionary` for the label, etc. — the more attributes
you replicate, the more it should look like an editor-created port.

**Actual:** none of those attributes drive the widget binding. Setting them
explicitly *blocks* C4D's runtime type inference and produces locked text
widgets in the AM. The widget binding comes from the **port connection**:
C4D infers the port's type from the connected downstream port at runtime.

The minimal recipe (4 lines) produces a fully draggable AM-exposed
parameter:
```python
with graph.BeginTransaction() as txn:
    port = inputs.AddPort(name)
    port.SetPortValue(maxon.Float64(0.0))               # initial value
    port.SetValue("net.maxon.node.base.name", label)    # display name
    port.Connect(target_typed_inner_port)               # ← THIS binds the widget
    txn.Commit()
```

Type-morphing also works — disconnect + reconnect to a different-typed port
and the widget adapts. No `fixedtype`, no description dicts, no template
cloning needed.

**Discovered:** spent ~6 hours over-specifying the schema and producing
locked widgets. User suggested "just create blank, connect to typed port,
adjust from there" — and that worked. The overspecified schema was
*overriding* the type inference. See `docs/gesture_differ_findings.md` for
full reverse-engineering history.

---

## 46. `idata`, `value_flags`, and `fixedtype:NativePyDataType` are derived attributes — Python can't write them

**Wrong:** `port.SetValue("idata", ...)` should work like any other
attribute write.

**Actual:** these three attributes are *derived* — only writable during
C++ "attribute derivation" triggered by editor-internal commands. Python
SetValue calls error with:
```
"The derived attribute idata may only be set during an attribute derivation"
"The derived attribute value_flags may only be set during an attribute derivation"
```

`fixedtype` is even trickier — the editor stores it as a `NativePyDataType`
(a special Python wrapper bound to C++ derivation state). Constructing one
from Python via `maxon.DataType.Get(...)` produces a regular `maxon.DataType`
that doesn't trigger widget binding. There's no public Python API to create
a `NativePyDataType`.

**Workarounds (in order of preference):**
1. **Don't write them.** Use the connection-based recipe in gotcha #47 —
   the connection provides everything these attributes would.
2. **`port.CopyValuesFrom(template_port, includeInner=True)`** — clones
   ALL attributes including derived ones from an existing typed port. The
   docstring says it "excludes derived attributes" but with `includeInner=True`
   it actually transfers them.
3. C++ shim that calls the editor's command-framework path (this is what
   Phase A.1 was originally targeting — see strategic docs).

---

## 45. `dir(graph_node_instance)` triggers "expected generic datatype capsule" error

**Wrong:** `dir(my_port)` lists available methods like any Python object.

**Actual:** for freshly-allocated maxon GraphNode instances,
`dir(instance)` triggers a binding-internal type-resolution error:
```
TypeError: expected generic datatype capsule
```

Probably a maxon Python binding bug — `dir` walks the instance and
something in the proxy resolution fails on certain freshly-created
objects.

**Workaround:** walk the class instead of the instance:
```python
methods = set()
for cls in type(my_port).__mro__:
    for name in dir(cls):
        if not name.startswith("_"):
            methods.add(name)
```

This bypasses the instance-level binding and gets the full method list.

---

## 44. Variadic ports — Connect() to the parent creates metadata slots but NO data flow; must Connect to a child slot

**Wrong:** `connect_node.GetInputs().FindChild("geometryin").Connect(...)`
multiple times will create N variadic input slots that all carry the data
properly.

**Actual:** the parent variadic port (e.g. `connect_geometries.geometryin`)
has type `GenericInstantiation<Array<Tuple<Id, DataDictionary>>>` — it
accepts a STRUCTURED ARRAY of geometry+insertindex tuples, not direct
geometry. When you Connect to the parent, C4D dutifully records metadata
(`{_0/insertindex:1, _1/insertindex:2}`) but no actual data flows because
the path needs an orange CHILD slot.

**Correct pattern:**
```python
parent_variadic = find_port(connect_node, "geometryin", "in")
slot0 = parent_variadic.AddPort(maxon.Id("_0"))   # creates slot
slot1 = parent_variadic.AddPort(maxon.Id("_1"))   # next slot
clone_output.Connect(slot0)                         # data actually flows
setsel_output.Connect(slot1)
```

Slot identifiers go `_0, _1, _2, ...` — created on demand. Calling
`AddPort("_0")` twice errors with "already has a child port named _0".
Always check existing children first if you might re-run.

**Discovered:** built a Scene Nodes capsule with 2 wires into Connect's
variadic, all "succeeded" but the graph was red because data never flowed.
User pointed out the white-vs-orange port distinction in the editor view.

---

## 43. `graph.AddChild(child_id, node_id, args)` accepts long Maxon canonical IDs; `ApplyDescription` `$type` does NOT

**Wrong:** `GraphDescription.ApplyDescription(graph, {"$type": "net.maxon.neutron.node.primitive.cube"})`
adds a Cube node — same id you'd find via `AssetInterface.FindAssets`.

**Actual:** ApplyDescription's `$type` requires SHORT-FORM type labels
that aren't the same as the canonical asset registry IDs. Long-form IDs
like `net.maxon.neutron.node.primitive.cube` produce:
```
"The node type reference 'net.maxon.neutron.node.primitive.cube' is not associated with any IDs"
```

The lower-level `graph.AddChild(child_id, node_id, args)` DOES accept the
long-form canonical IDs:
```python
new_node = graph.AddChild("my_cube",
                           "net.maxon.neutron.node.primitive.cube",
                           maxon.DataDictionary())
```

Use `AddChild` for programmatic construction; reserve `ApplyDescription`
for cases where you have a verified `$type` label (see
`docs/scene_nodes_guide.md` and `data/verified_labels.json`).

ALSO: `ApplyDescription` is fundamentally a node-creation DSL — the top
level requires `$type`. It cannot mutate root's port list (you'll get
"Missing node type declaration" if you try to put port keys at the top
level).

---

## 42. `obj.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)` is required before `obj.GetNimbusRef()` for fresh SN Generators

**Wrong:** `obj.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes)` on a
freshly-created Scene Nodes Generator returns a usable handler.

**Actual:** on a freshly-inserted SN Generator, `GetNimbusRef` returns
`None` until you "wake up" the Nimbus subsystem with:
```python
obj.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)
handler = obj.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes)
```

Required ritual for any code path that creates an SN Generator + immediately
operates on its embedded graph. Maxon's own example
`associate_nodes_2026_2.py` shows this pattern.

---

## 41. `handler.GetDescID(port.GetPath())` is the canonical AM-exposure verifier

**Wrong:** to verify that a Scene Nodes port is exposed in the Attribute
Manager, walk the AM's parameter list looking for a matching name.

**Actual:** the Nimbus handler exposes a direct check:
```python
handler = obj.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes)
try:
    did = handler.GetDescID(port.GetPath())
    print(f"port is AM-exposed with DescID: {did}")
except Exception:
    print("port is internal/hidden — not in AM")
```

Built-in graph-context ports (`time`, `frame`, `nimbus`, `searchpaths`)
correctly throw — they're not user-facing. User-added or programmatically-
synthesized root ports return a valid 6-level DescID.

This pattern comes from Maxon's `associate_nodes_2026_2.py` example —
also shows `handler.FindOrCreateCorrespondingBaseList(node.GetPath())`
which returns the cinema-side `BaseList2D` surrogate.

---

## 40. Enumerate ALL attributes on a maxon GraphNode via `GetValues(0xFFFFFFFF)`

**Wrong:** `node.GetValue("some.attribute.id")` on string keys you guess
returns the value if present.

**Actual:** `GetValue(string_key)` returns `None` for almost every key on
a node, even keys that ARE set internally. Reason: most internal attributes
are keyed by `maxon.InternedId`, not string, and the binding lookup is
type-strict.

To enumerate ALL attributes set on a node:
```python
for (key, value) in port.GetValues(0xFFFFFFFF):  # uint32 mask, not Id!
    print(f"{key}: {value}")
```

The mask argument is a **uint32 bitflag** (use `0xFFFFFFFF` for all),
NOT a `maxon.Id` despite docstring suggesting so.

To read a specific attribute by its known key, use:
```python
v = port.GetStoredValue(maxon.InternedId("net.maxon.attribute.foo"))
```

Note `GetStoredValue` requires `InternedId` (not `Id` — different type).

If you want a DataDictionary's contents fully:
```python
ddata = port.GetStoredValue(maxon.InternedId("portDescriptionData"))
for (subkey, subval) in ddata:
    print(f"  {subkey}: {subval}")
```

---

## 39. `maxon.DataType.Get(<type>)` accepts `str`, NOT `Id` or `InternedId`

**Wrong:** `maxon.DataType.Get(maxon.Id("float64"))` to get the Float64
DataType.

**Actual:** errors with `"id must be str not <class 'maxon.data.Id'>"`.
This is unusual — most maxon getters accept `Id` or `InternedId`.

**Correct:**
```python
float_dt = maxon.DataType.Get("float64")        # plain str
int_dt   = maxon.DataType.Get("int64")
vec_dt   = maxon.DataType.Get("vector64")
bool_dt  = maxon.DataType.Get("bool")
str_dt   = maxon.DataType.Get("net.maxon.interface.string-C")
```

Note: even when you write `fixedtype` correctly via this path, it produces
a `maxon.DataType` (not the `NativePyDataType` the editor uses) — see
gotcha #46 for the implications.

---

## 38. Port description `unit` belongs in `portDescriptionData`, NOT `portDescriptionUi`

**Wrong:** "unit" is a UI concept (what unit to display), so it belongs
in `portDescriptionUi`.

**Actual:** the `net.maxon.description.ui.base.unit` Id key is stored
inside the `portDescriptionData` DataDictionary, not `portDescriptionUi`.
Despite "ui.base" being in the key name itself.

```python
ddata = maxon.DataDictionary()
ddata.Set(maxon.InternedId("net.maxon.description.ui.base.unit"),
          maxon.InternedId("meter"))
port.SetValue(maxon.InternedId("portDescriptionData"), ddata)  # NOT portDescriptionUi
```

Caveat: per gotcha #47, you usually don't need to set this at all — the
connection-based recipe handles type binding without explicit unit specs.
But if you DO write description metadata explicitly (e.g. for a port
without a downstream connection), the unit goes in the data dict.

---

## 1. `doc.GetNimbusRef()` is single-arg only

**Wrong:** `doc.GetNimbusRef(maxon.Id(sid), True)` with `create=True` to fetch-or-create.

**Actual (2026):** Method takes only 1 argument. Returns `None` if no graph at that space. **No auto-create option.**

**Fix:** Use the modern `maxon.frameworks.nodes.GraphDescription.GetGraph(host)` API instead — it auto-creates if missing.

```python
from maxon.frameworks.nodes import GraphDescription
graph = GraphDescription.GetGraph(doc)  # auto-creates doc-level scene nodes graph
```

## 2. `GraphDescription.CreateGraph()` is DEPRECATED (2025+)

**Wrong:** Calling `CreateGraph(target=doc, space=Id)`.

**Actual:** Deprecated since 2025; method's docstring explicitly says "Use `GetGraph` instead."

## 3. Restriction tag (`Trestriction`) param schema

**Wrong:** Setting `tag[c4d.RESTRICTION_VMAPS] = "vmap_name"` (constant doesn't exist).

**Actual (2026):** Restriction tag uses 12 paired slots:
- `RESTRICTIONTAG_NAME_01..12` (id 1100..1111) — vmap name (string)
- `RESTRICTIONTAG_VAL_01..12` (id 1200..1211) — enable flag (bool)

```python
rtag = c4d.BaseTag(c4d.Trestriction)
rtag[c4d.RESTRICTIONTAG_NAME_01] = "bend_mask"
rtag[c4d.RESTRICTIONTAG_VAL_01] = True
```

**Failure mode if wrong:** writes to non-existent param produce a malformed tag that downstream plugins (Greyscalegorilla Signal etc.) can crash on. Real ACCESS_VIOLATION crash dump caught this 2026-04-29.

## 4. `MCOMMAND_AXIS` does not exist in 2026

**Wrong:** `c4d.utils.SendModelingCommand(c4d.MCOMMAND_AXIS, ...)` for "Axis Center" recenter.

**Actual:** No `MCOMMAND_AXIS` constant in C4D 2026. The "Axis Center" tool is a `CommandData` plugin, NOT a `SendModelingCommand` op.

**Fix:** Implement axis recenter as pure math:
```python
pts = obj.GetAllPoints()
xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
c_local = c4d.Vector((min(xs)+max(xs))*0.5, (min(ys)+max(ys))*0.5, (min(zs)+max(zs))*0.5)
for i in range(obj.GetPointCount()):
    obj.SetPoint(i, obj.GetPoint(i) - c_local)
m = obj.GetMl()
m.off += m.v1*c_local.x + m.v2*c_local.y + m.v3*c_local.z
obj.SetMl(m)
```

## 5. BaseDraw shading constants

**Wrong:** `BASEDRAW_DATA_SDISPLAYMODE`, `BASEDRAW_DATA_LDISPLAYMODE`.

**Actual:** `BASEDRAW_DATA_SDISPLAYACTIVE` (also `_INACTIVE`), `BASEDRAW_DATA_WDISPLAYACTIVE`, `BASEDRAW_DATA_LINES_ON_SHADING_ACTIVE`.

**Mode values (`SDISPLAYACTIVE`):**
- 0 = gouraud
- 1 = gouraud_wire (built-in wire baked into shading)
- 2 = quick
- 3 = quick_wire
- 4 = flat_wire
- 5 = hidden_line
- 6 = noshading
- 7 = flat (faceted, no smooth normals)

## 6. Line overlay is EDITOR ONLY for screenshots

**Wrong:** `LINES_ON_SHADING_ACTIVE = True` + `WDISPLAYACTIVE = 0` (wireframe overlay) will render with wireframe in `viewport_screenshot`.

**Actual:** Editor-only setting. C4D's render pipeline (`RenderDocument`) ignores the line-overlay layer. Captured PNG has no wireframe.

**Fix:** Use the built-in `*_wire` SDISPLAY modes instead (1, 3, 4) — they bake the wireframe into the render path.

## 7. Plane primitive `PRIM_AXIS` values

`PRIM_AXIS=5` is **NOT** "+Y up" — it's "-Z facing." If you want Y-up, omit `PRIM_AXIS` or set to a different value (test the resulting GetMl orientation).

## 8. `inspect.getsource()` LIES; `dis.dis()` tells truth

When developing the C4D plugin (.pyp) and verifying whether a fix is loaded:

- `inspect.getsource(cls.method)` reads the SOURCE FILE on disk → may show your latest edits even when C4D is still running OLD bytecode.
- `dis.dis(cls.method)` reads the actual loaded bytecode → tells you what's REALLY running.

**Canonical check:**
```python
import dis, io, contextlib
f = io.StringIO()
with contextlib.redirect_stdout(f):
    dis.dis(cls.handle_yourthing)
loaded = f.getvalue()
print(f"new constant 'YOUR_FIX_TOKEN' in bytecode: {'YOUR_FIX_TOKEN' in loaded}")
```

If False → C4D needs a full restart. "Reload Python Plugins" doesn't re-import a running socket-server plugin's class. Stop→Start the socket server doesn't either.

## 9. Worker thread vs main thread for doc operations

C4D's MCP socket runs each client connection on a worker thread. Most doc operations work fine from worker threads, BUT:

**MUST be main-thread (verified by failures in recipe suite):**
- `doc.StartUndo()`, `doc.EndUndo()`, `doc.DoUndo()`, `doc.DoRedo()` — undo manager state. **Worker-thread call silently fails** (DoUndo returns True without doing anything; StartUndo never opens a group).
- `maxon.frameworks.nodes.GraphDescription.GetGraph(host)` — returns explicit error: `"GetGraph() must be run from the main thread"`. Both fetch + auto-create paths are main-thread-only.

**Fix:** Wrap in `execute_on_main_thread`:
```python
def _do():
    doc.StartUndo()
    return True
self.execute_on_main_thread(_do, _timeout=10)
```

**Apparently safe from worker thread (verified via recipe suite):**
- `obj.SetAbsPos`, `obj.SetMl`, `obj.SetPoint`
- `tag.SetAllHighlevelData`
- `obj.InsertObject`, `obj.InsertUnder`, `obj.InsertTag`
- `c4d.utils.SendModelingCommand` (most ops)

**Heuristic:** anything in the `maxon.frameworks.*` family (modern node-graph APIs) seems to require main thread. The classic `c4d.*` API is more permissive but still has hot spots like the undo manager.

If a tool starts misbehaving inexplicably, check whether wrapping in `execute_on_main_thread` fixes it.

## 10. Vertex map storage is Float32

**Wrong:** Asserting `vmap.weight == 0.42` after writing `0.42`.

**Actual:** C4D vertex maps store Float32 internally. Round-trip:
- Write `0.42` (Python double)
- Read back `0.41999998688697815` (Float32 quantization)

**Fix:** Use approximate equality (default tolerance 1e-5) for vmap weight comparisons. Strict equality is wrong.

## 11. `doc.GetAllNimbusRefs()` doesn't show modern Scene Nodes graphs

**Wrong:** Assuming any Scene Nodes graph in the doc shows up in `GetAllNimbusRefs()`.

**Actual:** That registry is the CLASSIC per-object/per-space NimbusRef list. The modern doc-level Scene Nodes graph (created via `GraphDescription.GetGraph()`) lives in the maxon model layer and does NOT appear there.

**Fix:** Probe BOTH registries when checking what graphs exist:
```python
all_refs = doc.GetAllNimbusRefs()
modern_doc_graph = None
try:
    modern_doc_graph = GraphDescription.GetGraph(doc)
except Exception:
    pass
```

## 12. `bmp.Save()` returns `1` not `0` on success

**Wrong:** `if bmp.Save(path, c4d.FILTER_PNG) != c4d.IMAGERESULT_OK: # error`

**Actual:** On C4D 2026 (at least with the PNG filter) `bmp.Save()` returns `1`, not `IMAGERESULT_OK` (`0`). The save SUCCEEDED — the return code is misleading.

**Fix:** Don't bail on `!= IMAGERESULT_OK`. Verify by checking the file exists on disk.

## 13. `SendModelingCommand(MAKEEDITABLE)` removes source from doc

**Wrong:** `result = SendModelingCommand(MAKEEDITABLE, [generator]); doc.InsertObject(result[0])` AFTER manually removing the generator.

**Actual:**
1. The generator IS removed from the doc by SendModelingCommand itself.
2. The returned polygon object is NOT yet in the doc — you MUST `doc.InsertObject(poly)`.
3. Manual `generator.Remove()` after the command is a no-op (already removed).

**Canonical pattern:**
```python
generator = c4d.BaseObject(c4d.Ocube)
doc.InsertObject(generator)
result = c4d.utils.SendModelingCommand(c4d.MCOMMAND_MAKEEDITABLE, [generator], doc=doc)
poly = result[0]
poly.SetName("MyPoly")
doc.InsertObject(poly)  # REQUIRED — result is orphan otherwise
# generator is already gone; no Remove() needed
```

## 14. WSL paths must be Windows-converted before sending to C4D

**Wrong:** Sending `/mnt/c/Users/.../foo.png` as a `save_path` argument from WSL.

**Actual:** C4D's Python interpreter runs on Windows. `/mnt/c/...` is a WSL-mount path that Windows Python's `open()` can't resolve.

**Fix (server.py side):** Auto-translate `/mnt/<drive>/...` to `<DRIVE>:\\...` before sending the command. Already implemented in `_normalize_paths_in_command` — applied to known path-arg keys (`file_path`, `save_path`, `save_dir`, `bitmap_path`, `path`, etc.).

## 18. `FieldList.SampleListSimple` returns FieldOutput (don't pre-create)

**Wrong:** `flist.SampleListSimple(host, FieldInput, pre_built_FieldOutput)` — errors with `'FieldOutput' object cannot be interpreted as an integer`.

**Actual (2026):** `field_output = flist.SampleListSimple(host, FieldInput, flags_int)` — returns a NEW FieldOutput. The 3rd arg is a `FIELDSAMPLE_FLAG_*` int, NOT a pre-built FieldOutput. Don't construct one yourself.

```python
flist = c4d.FieldList()
layer = c4d.modules.mograph.FieldLayer(c4d.FLfield)
layer.SetLinkedObject(field_obj)
flist.InsertLayer(layer)
inputs = c4d.modules.mograph.FieldInput(positions, n)
output = flist.SampleListSimple(host_obj, inputs, c4d.FIELDSAMPLE_FLAG_VALUE)
weights = [output.GetValue(i) for i in range(n)]
```

The signature is `(BaseList2D, FieldInput, int) -> FieldOutput`. Took several probe iterations to land on this — the docstring just says "Sample a FieldList with simpler parameters" with no signature hint.

## 17. DescriptionResource IDs vs Shader Plugin IDs (don't confuse)

**Wrong:** `c4d.BaseShader(c4d.DESCRIPTIONRESOURCE_OSLTEXTURE)` to instantiate Octane's OSL texture (804752314 — description resource id, NOT a plugin id).

**Actual:** Calling `BaseShader()` with a description-resource ID hangs C4D (no plugin matches → infinite wait somewhere). The actual plugin id has to come from `c4d.plugins.FilterPluginList(c4d.PLUGINTYPE_SHADER, True)` — for Octane's "OSL texture" it's `1039813`.

**Heuristic:** any constant named `DESCRIPTIONRESOURCE_*` is for editing/UI registration of a description resource. Plugin instantiation needs the PLUGIN ID from `FilterPluginList`. They're different namespaces with similar-looking integer IDs.

```python
from c4d.plugins import FilterPluginList
shaders = FilterPluginList(c4d.PLUGINTYPE_SHADER, True)
osl_plugin_id = next((p.GetID() for p in shaders if p.GetName() == "OSL texture"), None)
shader = c4d.BaseShader(osl_plugin_id)  # works
```

## 16. `FieldList` is at top-level `c4d`, NOT in `c4d.modules.mograph`

**Wrong:** `from c4d.modules import mograph; flist = mograph.FieldList()`

**Actual (2026):** `c4d.FieldList()` — top-level. The OTHER field helpers
ARE in `c4d.modules.mograph`:
- `c4d.modules.mograph.FieldLayer`
- `c4d.modules.mograph.FieldInput`
- `c4d.modules.mograph.FieldOutput`
- `c4d.modules.mograph.FieldInfo`

Confusing split — be explicit about each import.

```python
flist = c4d.FieldList()  # top-level
from c4d.modules.mograph import FieldLayer, FieldInput, FieldOutput, FieldInfo
```

Field-layer subtype constants (also top-level): `c4d.FLfield`,
`c4d.FLnoise`, `c4d.FLformula`, `c4d.FLcurve`, `c4d.FLremap`, etc.

## 15. `bmp.GetPixel()` length varies

**Wrong:** Always assuming `(r, g, b) = px`.

**Actual:** Can be 3-tuple (RGB) or 4-tuple (RGBA) depending on bitmap color mode.

**Fix:** Check `if px and len(px) >= 3` before unpacking.

---

## 19. Capsule plugin IDs are the entry point for asset-ID discovery

**Context:** C4D 2026 buries Scene Nodes asset IDs (the strings you need
to programmatically create graph nodes via `GraphDescription.ApplyDescription`)
behind every Capsule object. The Asset Browser is full of them (Primitive
▶ Cube, Modifier ▶ Bevel, etc.) — each one is a classic-object-shaped
wrapper around a Scene Nodes graph.

**Wrong:** Trying to enumerate available Scene Nodes asset IDs via the
maxon SDK alone — `maxon.AssetInterface.GetUserPrefsRepository().FindAssets`
has shifting signatures and doesn't reliably return scene-nodes assets in
a usable form.

**Fix:** Walk existing capsules via `GraphDescription.GetGraph(obj)` +
recursive `GetChildren()` and collect every node's `GetId()`. The
canonical capsule plugin IDs to scan for in a doc:

```
5171      = Capsule
180420400 = Scene Nodes Deformer
180420500/600/700 = Scene Nodes Generator (3 variants)
440000274 = Capsule Field
1057221   = Simulation Scene
```

The cinema4d-mcp `scene_nodes_dissect_capsule` handler implements this
pattern and caches discovered IDs into a session-level registry.

**Caveat:** `GraphDescription.GetGraph` MUST run on the main thread.
From a worker thread it errors with "GetGraph() must be run from the
main thread" (same constraint as undo / maxon.frameworks.* APIs).

---

## 20. `node.GetValue()` requires `maxon.InternedId`, not `maxon.Id`

`GraphNode.GetValue(attribute_id)` reads a node attribute (e.g. a Floating
IO's `attribute.direction`). Passing `maxon.Id("net.maxon.node.floatingio.attribute.direction")`
fails with `unable to convert builtins.NativePyData to @net.maxon.datatype.internedid`.
The fix is `maxon.InternedId(...)`.

```python
# WRONG:
v = node.GetValue(maxon.Id("net.maxon.node.floatingio.attribute.direction"))

# RIGHT:
v = node.GetValue(maxon.InternedId("net.maxon.node.floatingio.attribute.direction"))
```

`InternedId` is the canonical attribute-key type. `Id` is for asset/object
identifiers. Different namespaces internally — they don't auto-coerce.

---

## 21. `port.Connect()` can SILENTLY NO-OP on void-template ports

The Scene Nodes imperative API (`graph.BeginTransaction() → src.Connect(dst) → txn.Commit()`)
works for typed-port-to-typed-port wires. But for **void-template ports** —
notably `net.maxon.node.floatingio.portlist` — `Connect()` returns no error
*and* the transaction commits cleanly, but **no wire actually lands**. The
C4D editor uses a higher-level auto-port-specialization on drag-wire that
isn't exposed in the Python imperative API.

**Always verify after commit:**

```python
src_port_obj.Connect(dst_port_obj)
txn.Commit()

dst_id = str(dst_port_obj.GetId())
landed = any(str(p.GetId()) == dst_id
             for (p, _wires) in src_port_obj.GetConnections(1))
if not landed:
    # Connect silently no-oped — likely a void-template port.
    raise RuntimeError("wire did not land after commit")
```

The `cinema4d-mcp` `scene_nodes_connect_ports` handler does this verification
post-commit and returns `ok=false` with a diagnostic if the wire didn't land.

---

## 22. `graph.AddPorts(parent, idx, count)` needs VARIADIC_TEMPLATE on the parent

Python wraps the plural form `AddPorts(parent, index, count)` (count-based,
adds N numbered slots). It fails with `Illegal argument: Condition variadic &
PORT_FLAGS::VARIADIC_TEMPLATE not fulfilled` when the parent port doesn't
have the variadic-template flag. Floating IO nodes and PORTLIST ports do
NOT satisfy this.

The C++ singular form at `frameworks/graph.framework/source/maxon/graph.h:891`:
```cpp
MAXON_METHOD Result<GraphNode> AddPort(const GraphNode& parent, const Id& name);
```
is the API the C4D editor actually uses for named-port creation — but it's
**not exposed in Python**. Wrap it in a C++ shim plugin if needed.

---

## 23. `AssetCreationInterface.CreateObjectAsset` works programmatically

`maxon.AssetCreationInterface.CreateObjectAsset` is fully exposed in Python
in C4D 2026 (verified 2026-04-30). Saves a `BaseObject` + its embedded graph
as a `net.maxon.assettype.file` asset (`.c4d` format). Bit-identical
round-trip via `AssetManagerInterface.LoadAssets`.

Signature (from docstring):
```python
desc = maxon.AssetCreationInterface.CreateObjectAsset(
    op,                              # BaseObject
    activeDoc,                       # BaseDocument
    storeAssetStruct,                # maxon.StoreAssetStruct
    assetId,                         # maxon.Id (empty -> auto)
    assetName,                       # str
    assetVersion,                    # str
    copyMetaData,                    # maxon.AssetMetaData
    addAssetsIfNotInThisRepository,  # bool
)
# returns maxon.AssetDescription
```

`StoreAssetStruct` constructor takes 3 args: `parentCategory` (must be
`maxon.Id` or string-convertible to Id, NOT `InternedId`), `lookupRepo`,
`saveRepo`. Get the user prefs repo via
`maxon.AssetInterface.GetUserPrefsRepository()`.

```python
repo = maxon.AssetInterface.GetUserPrefsRepository()
sas  = maxon.StoreAssetStruct(
    maxon.Id("net.maxon.assetcategory.uncategorized"),
    repo, repo)
desc = maxon.AssetCreationInterface.CreateObjectAsset(
    obj, doc, sas, maxon.Id(), "MyAsset", "1.0",
    maxon.AssetMetaData(), True)
```

To reload: `maxon.AssetManagerInterface.LoadAssets(repo, [(asset_id, "")], None, None)`
returns True on success and inserts the asset's content into the active doc.

**Caveat:** `CreateObjectAsset` produces `net.maxon.assettype.file`, NOT
`net.maxon.node.assettype.nodetemplate`. Maxon's shipped capsules
(Edge to Spline, Random Selection, etc.) are NodeTemplate-typed (`.c4dnodes`
format) — and that asset type is NOT exposed in Python. NodeTemplate
publishing requires C++.

---

## 24. Asset type registry — `maxon.AssetTypes` enumeration

`maxon.AssetTypes` is a registry exposing 50+ asset type declarations. The
ones relevant for graph/capsule work:

| `AssetTypes.X()` returns | Type ID |
|---|---|
| `File` | `net.maxon.assettype.file` (generic .c4d wrapper) |
| `NodeTemplate` | `net.maxon.node.assettype.nodetemplate` (Scene Nodes capsule .c4dnodes) |
| `NodeContext` | `net.maxon.assettype.nodecontext` |
| `NodeSpace` | `net.maxon.class.datalessassettype` |
| `NodeDescription` | `net.maxon.node.assettype.nodedescription` |
| `NodeDefaultsPreset` | `net.maxon.assettype.preset.defaults.node` |
| `DocumentPreset` | `net.maxon.assettype.preset.document` |
| `UserDataPreset` | `net.maxon.assettype.preset.userdata` |

Use these as the type filter for `repo.FindAssets(type_id, asset_id, version, mode)`.
637 NodeTemplate assets ship in a vanilla install of C4D 2026.

---

## 28. `cinema::String` and `maxon::String` are different types — convert via `MaxonConvert`

C++ plugins routinely mix two string types: `cinema::String` (the older C4D
string used by `BaseContainer`, `BaseObject`, etc.) and `maxon::String`
(modern, used by the maxon framework — graphs, assets, ids). They CANNOT
be concatenated with `+` directly. The compiler error looks like:
```
error C2666: 'cinema::operator +': overloaded functions have similar conversions
while trying to match the argument list '(cinema::String, const maxon::String)'
```

This commonly bites when building error messages — a `BaseContainer::GetString`
returns `cinema::String`, but the literal `"text"_s` resolves to
`maxon::String` (because `maxon::operator""_s` is in scope from any maxon
header include).

**Conversion functions** (in `c4d_string.h`):
```cpp
inline const String& MaxonConvert(const maxon::String& val);  // maxon -> cinema
inline String MaxonConvert(maxon::String&& val);
inline const maxon::String& MaxonConvert(const String& val);  // cinema -> maxon
inline maxon::String MaxonConvert(String&& val);
```

**Idiom:** build error/diagnostic messages in `maxon::String` (since `_s`
literals produce that), then convert ONCE at the BaseContainer boundary:
```cpp
maxon::String msg = "graph_target '"_s + MaxonConvert(targetName) + "' not found"_s;
wc->SetString(BC_KEY_STATUS_MSG, MaxonConvert(msg));
```

---

## 29. `iferr_scope` (no `_handler`) + impl-returns-Result is the clean Maxon idiom

The Maxon error system uses `iferr_return` to bail out of a function with a
`Result<>` return type. To use it in a function that returns a non-Result
type (like `Int32` or `void`), you have two options:

**Option A (preferred): wrap the impl as `Result<void>`, return early via
`iferr_return`, catch at the caller via `iferr (call) { ... }`.** This is
what every Maxon SDK example does:

```cpp
static maxon::Result<void> DoWork_Impl(BaseContainer* wc) {
    iferr_scope;  // Scope marker — required for iferr_return to work.
    SomeMaxonCall() iferr_return;
    return maxon::OK;
}

static Int32 DoWork(BaseContainer* wc) {
    iferr (DoWork_Impl(wc)) {
        wc->SetString(KEY_ERR, MaxonConvert(err.GetMessage()));
        return 1;
    }
    return 0;
}
```

**Option B (don't): `iferr_scope_handler` does NOT exist.** Despite what
auto-complete/AI may suggest, the macro is `iferr_scope` (no suffix). The
"_handler" form will compile-fail with cryptic messages.

---

## 30. `NodesGraphModelRef` lacks `GetRoot` — use `GetViewRoot()`

For walking an existing Scene Nodes graph in C++:
```cpp
maxon::NimbusBaseRef nimbus = host->GetNimbusRef(maxon::neutron::NODESPACE);
const maxon::nodes::NodesGraphModelRef& graph = nimbus.GetGraph();
maxon::GraphNode root = graph.GetViewRoot();  // NOT GetRoot()
```

`graph.GetViewRoot()` returns the root GraphNode at `GetViewRootPath()`. If
you need recursive traversal, use `GetInnerNodes`:
```cpp
graph.GetInnerNodes(root, maxon::NODE_KIND::NODE, false,
    [&](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool>
    {
        // process candidate; return true to continue iteration
        return maxon::Bool(true);
    }) iferr_return;
```

`maxon::neutron::NODESPACE` lives in `maxon/neutron_ids.h` — must include
that header AND list `neutron.framework` in the project's APIS.

---

## 32. `maxon::String` has `Find` not `FindFirst`; `GetInnerNodes` is on `GraphNode` not on the graph

Two API-shape traps in one. Both bit me on the second compile attempt of
the cinema4d-mcp helper plugin (2026-04-30):

**(a) `maxon::String::FindFirst` doesn't exist.** The available methods
(c4d 2026 SDK):
```
Bool Find(const REFTYPE& str, Int* pos, StringPosition start = 0)
Bool Find(CHARTYPE ch, Int* pos, StringPosition start = 0)
Bool FindLast(const REFTYPE& str, Int* pos, StringPosition start = StringEnd())
Bool FindLast(CHARTYPE ch, Int* pos, StringPosition start = StringEnd())
Int  FindIndex(...)        // returns -1 if not found, vs Bool result
Int  FindLastIndex(...)
```
Use `Find` (no "First"). All methods take an output position pointer or
return an Int index.

**(b) `GetInnerNodes` is a method on `GraphNode`, not on `NodesGraphModelRef`.**
The pattern is:
```cpp
maxon::GraphNode root = graph.GetViewRoot();
root.GetInnerNodes(maxon::NODE_KIND::NODE, /*includeThis=*/false,
    [&](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool> {
        // process candidate
        return maxon::Bool(true); // continue
    }) iferr_return;
```
Same applies to `GetChildren` — both are on the GraphNode, with the underlying
implementation forwarded to the graph internally.

---

## 37. NodeTemplate-typed asset creation is not exposed in Python — C++ only

After exhaustive probing of the C4D 2026 Python surface (verified live
2026-04-30 evening, cinema4d-mcp), creating a `net.maxon.node.assettype.
nodetemplate`-typed asset (`.c4dnodes` format — what Edge to Spline /
Random Selection ship as) is unreachable from Python:

**Asset commands open modal dialogs:**
- `c4d.CallCommand(465002339)` "Convert To Asset..." → opens modal,
  blocks main thread until user input. Script timeout at 30s.
- `c4d.CallCommand(200001023)` "Save as New Asset..." → same pattern.
  The "..." in command names indicates dialog-style commands.

**`OPENSAVEASSETDIALOGFLAGS` has no `NO_UI` / `BATCH` flag:**
```
ALLOW_EDIT_ID, ALLOW_EDIT_NAME, ALLOW_EMPTY_CATEGORY, HIDE_AI_BUTTON,
NONE, SHOW_MAKE_DEFAULT, SHOW_VERSION
```
Only UI-control toggles. Dialog can't be suppressed.

**`AssetCreationInterface` exposes 32 methods, NONE produce NodeTemplate:**
```
AddPreviewRenderAsset, BrowseDescriptionForDefaults, CheckObjectsOnDrop,
CreateMaterialAsset, CreateMaterialsOnDrag, CreateObjectAsset,
CreateObjectsOnDrag, CreateSceneAsset, GenerateImagePreview,
GenerateScenePreviewImage, GetAddDependencyDelegate, GetClass,
GetDefaultObject, GetDefaultSettings, GetHashCodeImpl,
GetNewAssetIdFromIdAndVersion, OpenSaveAssetDialog, RenderDocumentAsset,
SaveActiveDocumentAsNewVersion, SaveBaseDocumentAsAsset,
SaveBrowserPreset, SaveDefaultPresetFromObject, SaveDocumentAsset,
SaveMemFileAsAsset, SaveMemFileAsAssetAlone,
SaveMemFileAsAssetWithCopyAsset, SaveMetaDataForAsset,
SaveTextureAsset, SetDefaultObject, SupportDefaultPresets,
UpdateMetaData, UpdateSubtypeAndMetaData
```
All `Save*` / `Create*` hardcode the produced asset to `File`-type
(`net.maxon.assettype.file`, `.c4d` format). The `subType` parameter
on some of them refers to ASSET SUBTYPE (Object/Material/Scene), not
ASSET TYPE.

**No `RegisterNodeTemplate` / `CreateNodeTemplate` / `PublishNode`** in
the `maxon.*` namespace.

**`maxon.nodes.BuiltinNodes`** (the registry the C++ side uses for
NodeTemplate registration) evaluates to `None` from Python — not
accessible at runtime.

**`NodeTemplate*` Python symbols:** only `NodeTemplateBaseClass` and
`NodeTemplateDecoratorBaseClass` exist — these are C++ inheritance
markers (`MAXON_COMPONENT(NORMAL, NodeTemplateBaseClass)`), not runtime
factories.

**Right-click context-menu commands (Add Input, Add Output, Toggle
Node Type, Add User Data, Add Children) do NOT have global command
IDs** — `find_command_by_name` returns 0 for all of them. They're
context-menu-only operations not exposed via `CallCommand`.

**Conclusion:** the only way to publish a NodeTemplate-typed asset
programmatically in C4D 2026 is C++:
1. Inherit a class from `maxon::Component<MyClass, NodeTemplateInterface>`
2. Implement `InstantiateImpl` building the structure via
   `maxon::nodes::MutableRoot.GetInputs().AddPort(id).SetType<T>()`
3. Register at static-init time via
   `MAXON_DECLARATION_REGISTER(maxon::nodes::BuiltinNodes, ...)`

Reference: `plugins/example.nodes/source/space/dynamic_node_impl.cpp`.

The cinema4d-mcp helper plugin (proven Phase A.0/A.1 bridge) can host
this — see `docs/cpp_shim_design.md` Phase B.

---

## 36. Runtime `AddPort` on a FloatingIO is NOT supported — must define at NodeTemplate-build-time

After 4 attempts (2026-04-30 cinema4d-mcp Phase A.1), all runtime
`AddPort` variants targeting a FloatingIO instance fail:

| Attempt | Error |
|---|---|
| `fio.AddPort(portId)` (FIO node directly) | `"You can't add a port directly to node Root."` |
| `fio.GetInputs().AddPort(portId)` | `"PrivateIsNodeAFloatingIo(trueNode) not fulfilled."` |
| `graph.AddPorts(fio, idx, count)` | `"VARIADIC_TEMPLATE not fulfilled."` |
| `graph.AddPorts(portlist_port, idx, count)` | `"VARIADIC_TEMPLATE not fulfilled."` |

The walker DID find the correct FIO node (debug-trail confirmed via
`BC_KEY_DEBUG`: `candidates=[floatingio@HASH(MATCH), context_externaltimeinput, context_notime]`).
The runtime C++ AddPort implementation rejects FloatingIO targets.

**FloatingIO is marked `/// INTERNAL.`** in
`frameworks/nodes.framework/source/maxon/definitions/nodes_utility.h`.
Adding ports to a FIO is a NodeTemplate-build-time operation, NOT a
runtime graph-edit operation.

**The SDK pattern that DOES work for adding named ports** lives in
`plugins/example.nodes/source/space/dynamic_node_impl.cpp`:
```cpp
// During NodeTemplate definition (template-build-time):
maxon::nodes::MutableRoot root = parent.CreateNodeSystem() iferr_return;
maxon::nodes::MutablePort outPort = root.GetOutputs().AddPort(NODE::DYNAMIC::RESULT) iferr_return;
outPort.SetType<maxon::Color>() iferr_return;
```

Note `MutableRoot` / `MutablePort` are different types from runtime
`GraphNode`. They're for asset-creation, not graph-mutation.

**Implication for "user-tunable capsule with AM params" workflows:** the
actual path is `Phase B` — build a complete NodeTemplate definition with
FIOs+ports baked in via `MutableRoot`, save as a NodeTemplate-typed
`.c4dnodes` asset (not `.c4d` File asset — see gotcha #23). The asset's
FIOs surface as AM params when an instance is dragged in.

The C4D editor's drag-wire UX likely reaches this via an even
higher-level operation (instantiate a new NodeTemplate from the existing
FIO's definition + the new desired port; replace the in-place FIO).

---

## 35. `SpecialEventAdd` is ASYNC — handler fires after caller returns

Continuation of gotcha #34. `SpecialEventAdd` queues a CoreMessage to be
broadcast on the main thread, but **doesn't dispatch synchronously**. The
caller's stack frame must finish before the message thread (which IS the
main thread) can pick up the queue and fire the C++ `CoreMessage`
handlers.

Practical implication for Python ↔ C++ request/response patterns:

```python
# WRONG: write+fire+read on main thread — handler never sees the request
def _do_all_on_main():
    wc.SetInt32(KEY_OP, 1)        # write
    wc.SetInt32(KEY_STATUS, -1)
    c4d.SpecialEventAdd(PID, 1, 0)  # fire (queued)
    return wc.GetInt32(KEY_STATUS)  # reads -1, queue hasn't drained
self.execute_on_main_thread(_do_all_on_main)  # main thread blocked all the while
```

```python
# RIGHT: split — fire on main thread, worker yields, then read on main thread
def _write_and_fire():
    wc.SetInt32(KEY_OP, 1)
    wc.SetInt32(KEY_STATUS, -1)
    c4d.SpecialEventAdd(PID, 1, 0)
self.execute_on_main_thread(_write_and_fire)  # quick — main thread freed

# Worker thread sleeps, main thread processes queue
import time
deadline = time.time() + 5.0
while time.time() < deadline:
    s = self.execute_on_main_thread(lambda: wc.GetInt32(KEY_STATUS))
    if s != -1:
        return s
    time.sleep(0.05)
```

The mcp-socket worker thread is naturally separate from the C4D main
thread, so this poll-pattern works cleanly. **Calling from the main
thread directly will time out forever.** Even `time.sleep` on the main
thread doesn't yield to the message queue (sleep blocks the thread; the
message thread can't pick up the queue without the same thread releasing).

---

## 34. Python -> C++ messaging: `SpecialEventAdd` works, `SendCoreMessage` (custom IDs) and `BasePlugin.Message` do NOT

Real-world bridge for Python ↔ C++ messaging in C4D 2026 (verified live
2026-04-30):

| Python call | Reaches MessageData::CoreMessage? |
|---|---|
| `c4d.SendCoreMessage(BUILTIN_ID, bc)` (e.g. `EVMSG_CHANGE`) | ✅ yes |
| `c4d.SendCoreMessage(custom_id, bc)` | ❌ silently dropped |
| `BasePlugin.Message(msg_id, data)` | ❌ returns True but doesn't route |
| **`c4d.SpecialEventAdd(plugin_id, p1, p2)`** | ✅ **YES — use this** |

**`SpecialEventAdd(plugin_id, p1, p2)` is the only Python -> C++ bridge
that actually fires `MessageData::CoreMessage` for custom message routing.**
It packs:
- `plugin_id` at `BFM_CORE_ID` ('MciI') in the BC
- `p1` at `BFM_CORE_PAR1` ('Mci1')
- `p2` at `BFM_CORE_PAR2` ('Mci2')

C++ filters by checking the BC, NOT by the `id` parameter:
```cpp
virtual Bool CoreMessage(Int32 id, const BaseContainer& bc) override
{
    if (bc.GetInt32(BFM_CORE_ID) != MY_PLUGIN_ID)
        return true;  // not addressed to us
    Int32 op = bc.GetInt32(BFM_CORE_PAR1);
    // ... process op ...
}
```

For complex args/results that don't fit in two UInts, pair `SpecialEventAdd`
with `c4d.GetWorldContainerInstance()` shared state (Python writes args
into BC keys, calls `SpecialEventAdd`, reads results from same BC keys
after — the call is synchronous on the main thread).

**Why `SendCoreMessage` doesn't broadcast custom IDs:** the docstring
calls them "core messages" but only the predefined `EVMSG_*` and
`COREMSG_*` constants are actually broadcast. Custom IDs are filtered by
C4D's internal dispatcher.

**Why `BasePlugin.Message` returns True without routing:** the Python
`Message()` wrapper exists for `BaseList2D.Message()` (the base class
method), which is for sending messages to scene objects (NodeData
overrides). `MessageData` doesn't override `Message()` — only
`CoreMessage()` — so the call no-ops at the plugin instance level.

---

## 33. Use `iferr (decl = expr) { return err; }` block pattern, not `IsError()`

`Result<T>` has NO `IsError()` method. Despite intuition, the API uses
comparison operators (`== maxon::OK`, `== maxon::FAILED`) or — more
canonically — the `iferr` block macro that auto-binds an `err` variable.

The chained `Type x = expr iferr_return;` pattern works for many Maxon
APIs but breaks for `GraphNode`-returning template methods (`GetInputs`,
`GetOutputs`, `AddPort`, etc.) because the SFINAEHelper template return
type doesn't always unwrap cleanly to the declared LHS type. Compile
error:
```
error C2440: 'initializing': cannot convert from 'maxon::Result<maxon::GraphNode>' to 'maxon::GraphNode'
```

**Canonical pattern — `iferr` block (verified from
plugins/example.nodes/source/space/nodesystem_presethandler.cpp:81):**
```cpp
iferr (maxon::nodes::Port port = maxon::nodes::ToPort(node))
{
    return err;  // 'err' auto-bound by the iferr macro to the maxon::Error
}
// 'port' is in scope here as the unwrapped Port value
```

**Applied to GraphNode-returning template methods:**
```cpp
// Get a port container — won't unwrap with iferr_return chain
maxon::GraphNode container;
{
    iferr (maxon::GraphNode tmp = fio.GetInputs())  // or GetOutputs()
    {
        return err;
    }
    container = tmp;  // 'tmp' is the unwrapped value here
}

// AddPort same shape:
maxon::GraphNode newPort;
{
    iferr (maxon::GraphNode added = container.AddPort(portId))
    {
        return err;
    }
    newPort = added;
}
```

The trailing `iferr_return` form still works fine for non-template
returns: `BeginTransaction()`, `Init()`, `Commit()`, etc. Reach for the
`iferr` block specifically when `iferr_return` fights the compiler.

`Result<T>` checking via comparison if you need it without the macro:
```cpp
if (result == maxon::FAILED) return result.GetError();
T value = result.GetValue();
```
But the `iferr` block is cleaner and idiomatic.

---

## 31. `AddPort` on a Floating IO must be called on the FIO node DIRECTLY (not on GetInputs/GetOutputs)

**Corrected 2026-04-30** after live error feedback from the C++ runtime:
```
Illegal argument: Condition PrivateIsNodeAFloatingIo(trueNode) not fulfilled.
```

The `AddPort` implementation explicitly checks that the parent is a
FloatingIO node. Passing the FIO's `GetInputs()` or `GetOutputs()`
container fails this check.

**Correct usage:**
```cpp
maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;

maxon::Id portId;
portId.Init(MaxonConvert(portName)) iferr_return;

// Call AddPort ON THE FIO NODE (not on GetInputs()):
maxon::GraphNode newPort;
{
    iferr (maxon::GraphNode added = fio.AddPort(portId))
    {
        return err;
    }
    newPort = added;
}

txn.Commit() iferr_return;
```

The hidden+visible port pair (`hiddenin1.<path>` + `in1.<path>`) is
created automatically by `AddPort` when the parent is a FIO. Input vs
output direction is controlled separately by the FIO's
`net.maxon.node.floatingio.attribute.direction` Bool node-attribute,
set via `node.SetValue(maxon::InternedId(...), value)`.

**For non-FIO graph nodes:** `AddPort` would have to be called on the
node itself too — the same `PrivateIsNodeA<NodeType>` check applies per
template. The FIO error message is the most informative because it names
the expected node type explicitly.

---

## 27. C4D 2026 Windows SDK produces `.xdl64`, not `.cdl64`

The Maxon SDK convention has historically used `.cdl64` for Windows
plugin extensions and `.xdl64` for macOS. **In C4D 2026's Windows SDK
this is reversed**: Visual Studio builds produce `.xdl64` files (verified
2026-04-30 with the cinema4d-mcp helper plugin built via
`cmake --build . --config Release`).

C4D 2026 loads `.xdl64` files from the Windows plugins directory just
fine — install pattern is:
```
%APPDATA%/Maxon/Maxon Cinema 4D 2026_<HASH>/plugins/<plugin_name>/<plugin_name>.xdl64
```

When writing build/install scripts, search for both extensions and pick
whichever the build produced — don't hardcode `.cdl64`. Example
(`scripts/build_cpp_shim.sh`):
```bash
for ext in xdl64 cdl64; do
    candidate=$(find "$SDK/_build_v143" -type f -name "$plugin.$ext" 2>/dev/null | head -1)
    if [ -n "$candidate" ]; then break; fi
done
```

The exact build output path under the user's setup was:
```
C4D_2026_SDK/_build_v143/bin/Release/plugins/<plugin_name>/<plugin_name>.xdl64
```
(`bin/Release/plugins/<name>/` is deeper than expected — must use
recursive `find`, not a hardcoded path).

---

## 26. `PLUGINTYPE_MESSAGEDATA` doesn't exist — use `PLUGINTYPE_COREMESSAGE`

C++ plugins registered via `RegisterMessagePlugin(...)` (with a
`MessageData`-derived dispatcher class) are looked up from Python via
`c4d.plugins.FindPlugin(plugin_id, c4d.PLUGINTYPE_COREMESSAGE)` — value
`17`. There is **no** `c4d.PLUGINTYPE_MESSAGEDATA` constant despite the
C++-side class being called `MessageData`. The naming mismatch is a
classic Maxon trap.

Available `PLUGINTYPE_*` constants (verified C4D 2026.2):
```
ANY=0  SHADER=1  MATERIAL=2  COMMAND=4  OBJECT=5  TAG=6  BITMAPFILTER=7
VIDEOPOST=8  TOOL=9  SCENEHOOK=10  NODE=11  LIBRARY=12  BITMAPLOADER=13
BITMAPSAVER=14  SCENELOADER=15  SCENESAVER=16  COREMESSAGE=17
CUSTOMGUI=18  CUSTOMDATATYPE=19  RESOURCEDATATYPE=20
MANAGERINFORMATION=21  CTRACK=32  FALLOFF=33  VMAPTRANSFER=34  PREFS=35
SNAP=36  FIELDLAYER=37  DESCRIPTION=38
```

If you're discovering a C++ plugin you registered yourself, use
`PLUGINTYPE_COREMESSAGE` for MessageData-style registrations and
`PLUGINTYPE_COMMAND` for `RegisterCommandPlugin` registrations.

---

## 25. Scene Nodes 777 DescID root is editor metadata, NOT user AM params

The cinema4d-mcp project initially treated DescIDs under root 777 as the
"Scene Nodes Attribute Manager namespace." This is wrong. After 3 rounds
of probing across different inner graph configurations (bare empty, Memory
+ FloatingIO, Edge to Spline with 5 inner FIOs), the 777 tree was always
the **same 12 entries** — Scene Nodes editor metadata (group folders +
filter tags + node category + a fixed Maxon placeholder hash). The hash
`BrM5f_dgHBXvK6gQuZ3cQA` LOOKS per-instance but is identical across all
SN Generators.

User-facing AM params live under capsule-asset-specific roots (e.g. spline
generators surface params at roots 1000-1005, 4000 from the SplineObject
base class). FIO-routed params surface as AM params **only when the inner
graph is registered as a NodeTemplate-typed asset** — see gotcha #23.

---

## Discovery process

This list grows organically. Whenever runtime contradicts an API
expectation, it gets logged here. The cinema4d-mcp project's contract
tests + recipe suite catch most of these on first run, which is
exactly why those exist.

If you're building against C4D 2026 and hit something that contradicts
the C4D Python docs, please open an issue or PR with the discovery —
keeping this list current saves everyone time.
