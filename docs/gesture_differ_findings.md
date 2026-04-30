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

# Open: port datatype mechanism

`AddPort(name)` takes only one argument and creates an untyped port (`GetType()`
returns the generic `net.maxon.graph.graphnode` for all root-level ports,
including the standard `time`, `frame`, `nimbus`, etc.).

Probed datatype attribute keys via `port.GetValue(<key>)` on the **existing**
typed `geometryout` port:

```
net.maxon.node.attribute.datatype          → None
net.maxon.node.port.datatype               → None
net.maxon.description.data.base.datatype   → None
net.maxon.description.data.base.defaultvalue → None
net.maxon.description.ui.base.gui          → None
net.maxon.description.ui.base.guitypeid    → None
net.maxon.node.attribute.valuetype         → None
net.maxon.node.port.template               → None
net.maxon.node.port.wrappednode            → None
... (all None)
```

**Port datatype is not stored as a writable GraphNode attribute the Python
binding exposes.** The editor's tooltip displays
`net.maxon.geometryabstraction.objects.mesh.mesh` for GeometryObject ports —
that's the canonical type Id — but writing it under any of the candidate keys
above doesn't make C4D treat the port as that type.

Likely it's held at the template/registry layer (port template registration
similar to `MAXON_DECLARATION_REGISTER`), or via a C++-only API surface like
`GraphNode::SetWrappedNode` / `Port::SetTemplate`.

# Open: persistence + AM exposure

Untested:
- Do API-added ports survive scene save+reload?
- Do they appear in the Scene Nodes Generator's Attribute Manager?

These are the next checks before declaring the primitive production-ready.

# Implications for the strategic plan

This **partially unblocks Lane 1 (Research)** — the structural primitive for FIO
port creation is now public-API. Datatype-discrimination is the remaining gap.

For Lane 2 (Product), this means a custom Generator can programmatically
expose its parameter surface through this primitive — once datatype is solved.
Until then, untyped ports may still be usable if C4D infers the type from
downstream connections.

# Reproducing the experiments

The 4 variant captures are in `Desktop/Scene_Nodes_Handoff/gesture_variants/`:
- `variant_1_add_input_object.json`
- `variant_2_add_output.json`
- `variant_3_add_input_nondefault_type.json`
- `variant_4_param_value_change.json`

The recorder is the `scene_nodes_record_gesture` MCP tool. Synthesis is in this
session's transcript; will be packaged as `scene_nodes_synthesize_recipe` once
datatype is solved.
