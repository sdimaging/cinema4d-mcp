# Phase B — Revised after reading Maxon's Scene Nodes docs (2026-04-30)

**Status:** research-complete, ready for live probing in next session.
**Update:** the original 8-hour C++ shim path may be obviated by a 1-hour Python-only path. Verify via three quick probes when C4D is next open.

---

## What changed

The `cpp_shim_phase_b_design.md` plan assumed we'd need a C++ helper to register a new `NodeTemplate` class via `MAXON_DECLARATION_REGISTER` + `maxon::nodes::MutableRoot`. That's still a valid path, but the Maxon docs reveal a **simpler editor-command path** we hadn't mapped:

### The doc-revealed workflow

From [Introduction to Scene Nodes](https://help.maxon.net/c4d/en-us/Content/html/Introduction_Scene_Nodes.html):

> "When user data for subordinate objects gets created on these nodes, the color of the icon also changes from blue to green."

A graph becomes a generator (drag-into-Object-Manager-with-AM-params) by **adding user data**. The user-data creation gesture is:

> "Right-click the Node and select Add Input or Add Output from the context menu."

This is the **operation we tried to reach with `GraphModelInterface::AddPort`** in Phase A.1 — but `AddPort` is the runtime graph-edit primitive, not the editor-context-menu operation. The context-menu operation is almost certainly a registered C4D command (i.e., has a `Cmd*****` integer ID) that we can invoke via `c4d.CallCommand(...)`.

### Revised flow

```
1. Build graph                              (works — ApplyDescription)
2. Select target node                        (CallCommand for select-by-id?)
3. CallCommand(<Add Input ID>)               (NEED TO FIND — see probe queue)
4. Connect new port to internal consumer    (works — BeginTransaction/Connect)
5. CallCommand(465002339) Convert To Asset   (we have the ID; needs verify)
```

If steps 3 + 5 each map to a single `CallCommand`, **Phase B drops to ~1 hour of Python work**.

---

## Probe queue (next session, ~10 min total)

All require C4D running but no rebuild. Run via `find_command_by_name` MCP tool + `CallCommand`:

### Probe 1 — Find the "Add Input" / "Add Output" command IDs

```python
mcp__cinema4d__find_command_by_name(name_contains="Add Input", max_results=10)
mcp__cinema4d__find_command_by_name(name_contains="Add Output", max_results=10)
mcp__cinema4d__find_command_by_name(name_contains="Add New Input", max_results=10)
```

Expected: `Cmd_AddInputXxxx` style entries. If found, jot the IDs. If not, the operation may be context-menu-only (no global command).

### Probe 2 — Find related Node Editor commands

```python
mcp__cinema4d__find_command_by_name(name_contains="Toggle Node Type")
mcp__cinema4d__find_command_by_name(name_contains="Add Children")    # Scene Node's children expansion
mcp__cinema4d__find_command_by_name(name_contains="Group in Scaffold")
mcp__cinema4d__find_command_by_name(name_contains="Add User Data")    # the user-data-creation menu
```

### Probe 3 — `IDM_CAPSULE_DUMP` reverse-engineering

```python
# Assumes Edge to Spline is selected as the active object
import c4d
c4d.CallCommand(190000005)  # IDM_CAPSULE_DUMP
# Then via MCP:
get_console_log(limit=200)
```

This dumps the **complete internal NodeTemplate structure** of a real Maxon-shipped capsule to the C4D console. Heat map shown earlier in this session reveals data like:
```
base@HASH/randomselection@HASH/active@HASH/selectionoperator
base@HASH/randomselection@HASH/range@HASH
base@HASH/polygonbevel@HASH/selectionstringtoselection
```

This is the **exact internal NodeTemplate composition** that would inform Phase B's template-build scripts (whether via Python `CallCommand` or C++ `MutableRoot`).

### Probe 4 — `Convert To Asset` vs `Save as New Asset` comparison

```python
# Synthesize a graph
mcp__cinema4d__scene_nodes_create_capsule_with_pattern(
    pattern_name="hash_threshold_selection",
    capsule_name="ConvertProbe",
)

# Test Save as New Asset (we tested this — produces File-type)
c4d.CallCommand(200001023)
# Read produced asset's TypeId

# Reset, then test Convert To Asset
c4d.CallCommand(465002339)
# Read produced asset's TypeId
```

If `465002339` produces `net.maxon.node.assettype.nodetemplate` (the `.c4dnodes` format we need), the C++ path is unnecessary and Phase B is just MCP wrappers around these CallCommands.

---

## Maxon doc details captured

### From [60627.html — Scene Node Assets](https://help.maxon.net/c4d/en-us/Content/html/60627.html)

Asset Browser node-asset categories under "Nodes > Asset construction":
- **Nodes Mesh** — geometry from points/edges/polygons
- **Nodes Spline** — spline geometry
- **Node Modifier** — modeling/deformation foundation
- **Node Selection** — procedural selection methods

Distribution mechanisms:
- Create/Export Latest Version → ZIP file
- Create/Import Assets
- Connect Zip Database... / Connect database... / URL Connect Database... (cloud)

Missing-asset handling: placeholder nodes maintain connections + display warnings.

### From [Introduction_Scene_Nodes.html](https://help.maxon.net/c4d/en-us/Content/html/Introduction_Scene_Nodes.html)

**Node color taxonomy** (drives where a node can be used):
- **Blue** — mesh/spline geometry (Object Manager primitives)
- **Green** — generators (with subordinate objects + user data) — what we want for "user-tunable capsule"
- **Purple** — deformers / selection operators (require subordination)
- **Red** — distribution framework (NOT for Object Manager)
- **Orange + purple** — non-destructive modifier operators

**Workflow gestures** (each likely backed by a C4D command):
- Right-click → "Add Input" / "Add Output" — promotes parameter to user-data input
- Right-click → "Toggle Node Type" — converts Geometry mode ↔ Object mode (Op output adds matrix/coords/etc.)
- Right-click → "Add Children" / "Remove Children" — Scene Node's expandable children-input slots
- Shift+Ctrl-click — adds a Wire Rerouter (purely visual)
- Connect to Scene Node Children → auto-creates a **Geometry Op Node** in the graph

**Selection String** field — direct numeric ("0,2") or pattern syntax in the Attribute Manager for selection-style nodes.

### From [NET_MAXON_NODE_SCAFFOLD.html](https://help.maxon.net/c4d/en-us/Content/html/NET_MAXON_NODE_SCAFFOLD.html)

Scaffolds are purely organizational — colored frames around nodes. Don't change connections. CAN be converted to a normal Node group (suggests a `Scaffold → Group` command exists). Created via `Cmd_465002403` (New Empty Scaffold) per earlier doc map.

### From [NET_MAXON_NODE_ANNOTATION.html](https://help.maxon.net/c4d/en-us/Content/html/NET_MAXON_NODE_ANNOTATION.html)

Note nodes — purely visual text labels. Created via `Cmd_465002313` (New Note). No ports, no functionality.

### From [NET_MAXON_NODES_SIMULATION_PARTICLENODEMODIFIER_MODIFIER.html](https://help.maxon.net/c4d/en-us/Content/html/NET_MAXON_NODES_SIMULATION_PARTICLENODEMODIFIER_MODIFIER.html)

Particle Node Modifier — separate domain (CPU-based simulation in capsule mode). Uses Get Particle Property Node etc. Doesn't affect Phase B but confirms Scene Nodes drive particle workflows too.

---

## Decision tree for Phase B start

```
Probe 1+2+4 results when C4D next open
│
├── Add Input / Add Output have command IDs
│   AND Convert To Asset (465002339) produces NodeTemplate-typed asset
│   ├── Phase B is Python-only via CallCommand wrappers (~1 hr)
│   └── Drop the C++ shim's NodeTemplate-publish ambitions; keep
│       the bridge for genuinely-not-exposed primitives only.
│
├── Add Input has no command ID (context-menu-only)
│   ├── Try simulating context-menu via maxon framework or
│   │   a different programmatic path (research)
│   └── Fall back to C++ shim with MutableRoot pattern (~8 hr)
│
└── Convert To Asset (465002339) ALSO produces File-type
    └── Confirms NodeTemplate truly requires C++ registration;
        proceed with original Phase B C++ shim plan.
```

---

## Carry-forward from this session

- All Phase A.1 infrastructure (bridge, build automation, gotchas #28-36) carries to whichever Phase B path wins.
- The IDM_CAPSULE_DUMP reverse-engineering data (when run) is an unconditional win — informs the inner-graph spec we'd build in either path.
- 22 codified patterns, 802 NodeTemplate ID index, 62 verified `$type` strings continue to underpin graph synthesis regardless of publish-path choice.
