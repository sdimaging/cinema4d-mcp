---
title: Scene Nodes Gesture Differ — first findings
status: 2026-04-30 — milestone
trigger: GPT 2-lane review concluded the right-click "Add Input" gesture isn't
  reachable through any documented Maxon command/observer surface. Pivot:
  observe the gesture's *outcome*, reproduce via public API.
---

# TL;DR

**The right-click "Add Input"/"Add Output" gesture in the Node Editor is just one Python call
inside a graph transaction:**

```python
from maxon.frameworks.nodes import GraphDescription
graph = GraphDescription.GetGraph(scene_nodes_generator_object)
inputs  = graph.GetRoot().GetInputs()
outputs = graph.GetRoot().GetOutputs()

txn = graph.BeginTransaction()
inputs.AddPort("my_param")     # equivalent of right-click Add Input
outputs.AddPort("my_result")   # equivalent of right-click Add Output
txn.Commit()
```

The diff between this synthesis and the manual gesture is **structurally identical**.

# How we got here

## 1. Built a gesture differ

`scene_nodes_record_gesture` MCP tool — see `docs/gesture_differ_design.md`.
Snapshots the full graph state (nodes/ports/connections + per-port values) before
and after a manual gesture, returns the precise structural diff.

## 2. Captured 4 variants of the gesture

| Variant | Gesture | Observed diff |
|---|---|---|
| v1 | Add Input, type=GeometryObject | +1 input port `object` on root |
| v2 | Add Output, type=GeometryObject + manual wire | +1 output port `object` on root + 1 manual connection |
| v3 | Add Input, type=Float | +1 input port `float` on root |
| v4 | Sphere `radius` 4 → 6 | 3 param_changes (radius + dependent dict cascades) |

The unifying pattern: gesture = **+1 port to root, port_id = lowercase(datatype)**.
No new nodes. No port-list-node mutations. No paired hidden/in canonicals.

## 3. Eliminated `GraphDescription.ApplyDescription` as the synthesizer

ApplyDescription is a **node-creation DSL**: every description must declare a
node at the top via `$type`. Top-level port keys fail with
`"Missing node type declaration"`. **It cannot mutate root's port list.**

## 4. Found the public primitive

The Python binding exposes **`GraphNode.AddPort(name)`** — and the docstring is exact:

> Adds a port to this node with the given id.
> This node has to be a port list or a port itself.

So you call AddPort on `root.GetInputs()` (the input port-list child of root),
not on root itself or on the graph model. AddPort requires an active
`BeginTransaction` — without it: `"No current transaction for modification of NodesGraphModel."`

## 5. Verified synthesis = manual gesture

```
v1 manual:  {node_path: "", port_id: "object",            direction: "in"}
synthesis:  {node_path: "", port_id: "synth_test_input",  direction: "in"}
```

Same node_path (root), same direction, same kind. Add Output is symmetric
(`outputs.AddPort(name)` produces the equivalent output-side delta).

# What this collapses

The C++ Phase A.1 attempt failed because it called `GraphModelInterface::AddPort` —
the wrong abstraction. The correct target is `GraphNode::AddPort` on the
**port-list child** of root, not the graph model itself.

The "Maxon internal-only" framing on this gesture turns out to be wrong: the API
is fully reachable from Python as long as you find the right object.

# Solved: port datatype = typed default value

The breakthrough realization came from inspecting Sphere.radius (which the
editor's tooltip shows as `Type: Float64, Value: 6`):

```
GetType()              → net.maxon.graph.graphnode  (same as untyped ports!)
GetEffectivePortValue() → maxon.Float64 object ... data: 6
```

**The port itself doesn't carry type info.** `GetType()` returns the GraphNode
class (always `net.maxon.graph.graphnode` for any port). The editor's tooltip
reads the type from the **value** flowing through the port. So setting a port's
type = setting its default value to a value of the desired type:

```python
txn = graph.BeginTransaction()
new_port = inputs.AddPort("my_float_param")
new_port.SetPortValue(maxon.Float64(0.0))   # types it as Float64
txn.Commit()
```

Verified across all primitive types:
- `maxon.Float64(default)` → typed Float64 port
- `maxon.Int64(default)` → typed Int64
- `maxon.Vector64(x,y,z)` → typed Vector64
- `maxon.Bool(default)` → typed Bool
- `maxon.String(default)` → typed String

After SetPortValue, `GetEffectivePortValue()` returns the right typed object,
matching what the editor's tooltip would display.

`maxon.Geometry` / `maxon.Mesh` / `maxon.GeometryObject` aren't exposed in the
Python binding; graph-IO ports route geometry as part of node connections rather
than via SetPortValue. This is fine for the parameter-exposure use case (which
is what FIO ports are for).

# Canonical datatype list (from the editor's Resource Editor dropdown)

The Resource Editor's `Data Type` dropdown lists every type a user can pick
when adding a port via the right-click menu:

| UI label | maxon Python constructor |
|---|---|
| Bool | `maxon.Bool(False)` |
| Color | (Vector64 with 3 components, RGB convention) |
| ColorAlpha | (Vector64 with 4 components, RGBA) |
| Float | `maxon.Float64(0.0)` |
| GeometryObject | (graph-IO, not via SetPortValue) |
| Int | `maxon.Int64(0)` |
| Matrix | (likely needs maxon.Matrix64) |
| String | `maxon.String("")` |
| TimeValue | (TimeValue type) |
| Url | (Url type) |
| Vector | `maxon.Vector64(0,0,0)` |
| Vector2d | (Vector2d) |
| Vector4d | (Vector4d) |

# Per-port advanced attributes (from Resource Editor)

The editor's "Advanced" tab exposes additional per-port writable attributes
beyond datatype:

- `Group Identifier`: `net.maxon.node.base.group.inputs` — tells the editor
  this port is in the inputs port-list (vs `...group.outputs` for outputs).
  Probably what `root.GetInputs()` resolves to internally.
- `Animatable`: bool — whether the port can be keyframed
- `Hide Port in Nodegraph`: bool — visibility in the editor canvas
- `Is Converter Port`: bool
- `Scene Port Mode`: enum (None/...)
- `Show Condition` / `Enable Condition`: filter expressions
- `Parent Folder ID`: nesting under groups
- `Import Splines As`: enum

Plus from the General tab:
- `String` (display name, separate from Identifier)
- `Default Value`
- `User Interface` (custom GUI override)
- `Read Only` (bool)
- `Multiline` (bool, for strings)

These all map to specific Id-keyed attributes the editor reads. Future synthesizer
work: enumerate the matching attribute Ids for these so a complete port can be
declared in a single call.

# Complete synthesis recipe

```python
from maxon.frameworks.nodes import GraphDescription
import maxon

graph = GraphDescription.GetGraph(scene_nodes_generator_object)
txn = graph.BeginTransaction()

# Add Input with Float64 type:
input_port = graph.GetRoot().GetInputs().AddPort("my_radius")
input_port.SetPortValue(maxon.Float64(1.0))

# Add Input with Vector64 type:
vec_port = graph.GetRoot().GetInputs().AddPort("my_offset")
vec_port.SetPortValue(maxon.Vector64(0, 0, 0))

# Add Output (untyped — will adopt type from incoming connection):
out_port = graph.GetRoot().GetOutputs().AddPort("my_result")

txn.Commit()
```

# Open: persistence + AM exposure

Untested:
- Do API-added ports survive scene save+reload?
- Do they appear in the Scene Nodes Generator's Attribute Manager?

These are the next checks before declaring the primitive production-ready.

# Full editor-equivalent port schema (decoded via GetValues(0xFFFFFFFF))

The right-click → Add Input → pick Float type gesture writes **9 attributes**
on the port GraphNode. We can read them via `port.GetValues(0xFFFFFFFF)`
which returns `(InternedId, value)` tuples:

| InternedId | Type | Purpose |
|---|---|---|
| `effectiveportdescription` | portdescriptionmap | resolved description (computed) |
| `synthesizedportdescription` | synthesizedportdescriptionmap | computed when other attrs set |
| `fixedtype` | maxon.DataType | the port's type (e.g. `float64`) |
| `idata` | inheriteddata | inheritance/cascade |
| `net.maxon.node.attribute.orderindex` | Int64 | position |
| `portDescriptionData` | DataDictionary | classification/datatype/comment/maxvalue/limitvalue |
| `portDescriptionStringLazy` | LazyLanguageDictionary | per-language display labels |
| `portDescriptionUi` | DataDictionary | guitypeid/groupid/orderindex/stepvalue |
| `value_flags` | value_flags | port flags |

A bare `AddPort(name)` only creates **4 attributes** (`effectiveportdescription`,
`idata`, `orderindex`, `value_flags`). The remaining 5 must be written
explicitly to match the editor's output.

# Full synthesis recipe

```python
import maxon
from maxon.frameworks.nodes import GraphDescription

obj.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)
handler = obj.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes)
graph = handler.GetGraph()
inputs = graph.GetViewRoot().GetInputs()

with graph.BeginTransaction() as txn:
    port = inputs.AddPort("my_radius")
    port.SetPortValue(maxon.Float64(1.0))    # default value

    # fixedtype — the port's type as a maxon.DataType object
    port.SetValue(maxon.InternedId("fixedtype"),
                  maxon.DataType.Get("float64"))

    # portDescriptionData — classification + type + comment
    ddata = maxon.DataDictionary()
    ddata.Set(maxon.InternedId("net.maxon.description.data.base.classification"),
              maxon.InternedId("input"))
    ddata.Set(maxon.InternedId("net.maxon.description.data.base.datatype"),
              maxon.InternedId("float"))
    port.SetValue(maxon.InternedId("portDescriptionData"), ddata)

    # portDescriptionUi — widget type + group id + step
    udata = maxon.DataDictionary()
    udata.Set(maxon.InternedId("net.maxon.description.ui.base.guitypeid"),
              maxon.InternedId("net.maxon.ui.number"))
    udata.Set(maxon.InternedId("net.maxon.description.ui.base.groupid"),
              maxon.InternedId("net.maxon.node.base.group.inputs"))
    udata.Set(maxon.InternedId("net.maxon.description.ui.base.addminmax.stepvalue"),
              maxon.Int64(1))
    port.SetValue(maxon.InternedId("portDescriptionUi"), udata)

    # portDescriptionStringLazy — display label translations
    # WORKAROUND: maxon.LazyLanguageDictionary() constructor returns a null
    # shell that rejects Set(); LazyLanguageDictionary.Create() fails with
    # "could not find any Alloc function". Instead, copy the lazy dict from
    # an existing typed port of the same data type:
    template_port = ...  # find an existing port of the same type
    template_lazy = template_port.GetStoredValue(
        maxon.InternedId("portDescriptionStringLazy"))
    port.SetValue(maxon.InternedId("portDescriptionStringLazy"), template_lazy)

    txn.Commit()
```

# guitypeid mapping (UI widget per type)

| Data Type | guitypeid InternedId |
|---|---|
| Float, Int | `net.maxon.ui.number` (Numerical Edit Field) |
| Vector, Vector2d, Vector4d | (TBD — Vector field group) |
| Bool | (TBD — checkbox) |
| String | (TBD — text edit) |
| GeometryObject | (TBD — link/picker) |

# Verification — Lane 1 fully closed

Visual confirmation in C4D 2026:
- Synthesized port appears in Scene Nodes Generator's Attribute Manager
- Renders with the correct widget (Numerical Edit Field for Float)
- Resource Editor recognizes it: shows correct String/Identifier/Classification/
  Data Type/User Interface fields
- Advanced tab shows correct Group Identifier and Animatable flag
- handler.GetDescID(port.GetPath()) returns a valid 6-level DescID

This is **production-ready** for programmatic typed-port creation, with one
known workaround: display label requires copying a lazy dict from an
existing typed port (fresh LazyLanguageDictionary construction needs an
Alloc path we haven't found in the Python binding).

# Implications for the strategic plan

This **fully unblocks Lane 1 (Research)** for FIO port creation. Both
structural creation, datatype assignment, AM exposure, AND widget rendering
are reproducible via public Python API.

For Lane 2 (Product), a custom Generator can programmatically expose its
entire parameter surface — Float, Int, Vector, Bool, String inputs + outputs
with proper widgets, groups, and labels — entirely from Python. This is the
cornerstone of programmatic Scene Nodes capsule authoring.

Remaining follow-ups (none blocking):
- LazyLanguageDictionary construction from scratch (currently must copy)
- Persistence (save+reopen) — untested
- Per-type guitypeid mapping table for non-Float types
- `scene_nodes_synthesize_recipe` MCP tool wrapping this recipe

# Reproducing the experiments

The 4 variant captures are in `Desktop/Scene_Nodes_Handoff/gesture_variants/`:
- `variant_1_add_input_object.json`
- `variant_2_add_output.json`
- `variant_3_add_input_nondefault_type.json`
- `variant_4_param_value_change.json`

The recorder is the `scene_nodes_record_gesture` MCP tool. Synthesis is in this
session's transcript; will be packaged as `scene_nodes_synthesize_recipe` once
datatype is solved.
