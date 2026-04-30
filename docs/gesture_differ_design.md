---
title: Scene Nodes Gesture Differ — design
status: design — 2026-04-30
trigger: GPT 2-lane review concluded that `add_port` is reachable only via internal
  binary symbols. Pivot: stop trying to *call* the hidden function; observe its
  *outcome* by snapshotting the graph before/after a manual gesture and reproducing
  the delta via public API.
---

# Goal

Build a tool pair that turns any Node-Editor gesture (right-click "Add Input",
"Convert to Asset", drag-wire, etc.) into a reproducible MCP recipe expressed in
public Scene Nodes API calls (`GraphDescription.ApplyDescription`,
`BeginTransaction` + `Connect` + `Commit`).

This is reusable infrastructure: not specific to FIO Add-Input. Any editor gesture
that produces a deterministic graph delta becomes scriptable.

# Tools

## `scene_nodes_record_gesture`

```
action ∈ {"start", "stop", "diff"}
target_object: optional   # default = doc-level graph
label: str                # tag for this recording (for the dump bundle)
include_params: bool      # default True — capture port/parameter values too
```

- `start`: snapshot the graph into in-memory state slot `g_recorder.before`.
  Returns `{ok, snapshot_summary: {node_count, port_count, connection_count}}`.
- `stop`: snapshot again into `.after`, compute diff, return diff object.
- `diff`: re-emit last computed diff (idempotent — useful when stop's reply
  was lost to a wedge).

## `scene_nodes_synthesize_recipe`  (Phase 2 — built after first diff)

```
diff: {...}                         # output of record_gesture stop
target_object: str                  # the host that will receive the recipe
emit_format ∈ {"graphdescription", "transaction_script", "both"}
```

Returns a recipe block that, when executed against `target_object`, recreates
the diff. If parts of the diff have no public-API equivalent, those entries are
flagged in `unreproducible` and the recipe is partial.

# Snapshot data model

Node identity uses a **stable path** (chain from root) rather than `GetId()`,
since `GetId()` returns the asset/template id, which is non-unique among siblings
of the same type. Path format:

```
root/builder/builder.0/cube
```

Sibling collisions disambiguated with `.<index>` suffix in tree order.

```jsonc
{
  "graph_target": "<doc>" | "<object name>",
  "captured_at": <epoch ms>,
  "nodes": {
    "<path>": {
      "asset_id": "net.maxon.scene.builtinnode.cube",
      "kind": 1,                          // 1=node, 2=output port, 4=input port
      "parent_path": "<path>",
      "child_paths": ["<path>", ...],     // tree order preserved
      "input_ports": [
        {
          "port_id": "size",
          "kind": 4,
          "type": "<template id if discoverable>",
          "incoming": ["<src_node_path>/<src_port_id>", ...]
        }
      ],
      "output_ports": [
        {
          "port_id": "object",
          "kind": 2,
          "outgoing": ["<dst_node_path>/<dst_port_id>", ...]
        }
      ],
      "params": {                         // only when include_params=True
        "<port_id>": <jsonable value>     // skip if read fails — log under "param_errors"
      }
    }
  },
  "connections": [                        // denormalized from the per-port lists
    {"from": "<path>/<port_id>", "to": "<path>/<port_id>"}
  ],
  "selection": {                          // best-effort, may be empty
    "selected_node_paths": [...]
  }
}
```

# Diff format

```jsonc
{
  "graph_target": "<doc>" | "<object>",
  "label": "right-click-add-input-1",
  "added_nodes":     [{"path", "asset_id", "parent_path"}],
  "removed_nodes":   [{"path", "asset_id"}],
  "added_ports":     [{"node_path", "port_id", "direction": "in"|"out"}],
  "removed_ports":   [{"node_path", "port_id", "direction"}],
  "added_connections":   [{"from", "to"}],
  "removed_connections": [{"from", "to"}],
  "param_changes":   [{"node_path", "port_id", "before", "after"}],
  "selection_change": {"before": [...], "after": [...]},

  // Heuristic classification of the gesture (added by stop, advisory):
  "classification": {
    "kind": "fio_add_input"|"fio_add_output"|"node_add"|"connect"|"disconnect"|"unknown",
    "confidence": 0.0..1.0,
    "evidence": [...]
  }
}
```

The classifier uses simple pattern matching:
- `fio_add_input`: 1 added FIO node OR 0 added nodes + 1 added input port on existing FIO + 1 added connection routing through the paired `hiddenin`/`in` canonical names.
- `node_add`: ≥1 added node, no port additions on pre-existing nodes.
- `connect`: 0 added nodes/ports + ≥1 added connection.

# First experiment (the one we run now)

1. Open C4D. Create a fresh empty scene.
2. Add a Scene Nodes Generator (or any object with an embedded graph).
3. MCP: `scene_nodes_record_gesture(action="start", target_object="<obj>",
   label="right-click-add-input-1")`.
4. **(USER)** Right-click a port in Node Editor → "Add Input".
5. MCP: `scene_nodes_record_gesture(action="stop")`.
6. Inspect the diff.

Expected outcomes:

| Diff shape | Interpretation | Next step |
|---|---|---|
| 1 added FIO node + 2 paired ports + 2 connections | Pure public-API reproducible | Build synthesizer; close the gap |
| Only port additions on existing FIO node, no new node | Editor mutates the FIO template's portlist directly | Try `Connect` on `floatingio.portlist` after a `BeginTransaction` setting `port_id` parameters; if reject, document the precise unreproducible step |
| Connections route through opaque template (no canonical name match) | Editor uses a private port type | Capture exact template id; that's the wall to ask Maxon about |

Either way, the diff is the precise question we need to take to Maxon dev access.

# Implementation surface

All snapshot/diff logic in Python in `mcp_server_plugin.pyp`. No C++ changes.
Main-thread routed (existing `execute_on_main_thread` wrapper).

State slot:

```python
self._GESTURE_RECORDER = {
    "before": None,         # snapshot dict
    "after":  None,
    "label":  None,
    "diff":   None,
    "graph_target": None,
}
```

Reuses existing helpers:
- `GraphDescription.GetGraph(host)` — already in walk/dissect/connect handlers
- `port.GetConnections(0|1)` — already in `connect_ports` post-commit verifier
- `execute_on_main_thread` — main-thread routing wrapper

New helpers (~150 LoC):
- `_snapshot_graph(host, include_params) -> dict`
- `_compute_diff(before, after) -> dict`
- `_classify_diff(diff) -> dict`

# Out of scope (this design)

- Synthesizer (Phase 2 — only after first diff confirms the pattern)
- A.2.1 observer subscription (kept as backlog diagnostic, not blocking)
- Multi-session recording / persistent dump library (YAGNI for first experiment)

# Why this is Lane 2 (product) infra, not just Lane 1 (research)

A "learn editor operation from before/after state" tool is broadly useful:
- Speeds future cracking of any opaque gesture (not just FIO Add Input)
- Becomes a first-class MCP capability — "show me, then I'll script it"
- All snapshot reads use public Scene Nodes API only; no binary hooks
- Synthesizer outputs only public API recipes; nothing private ships
