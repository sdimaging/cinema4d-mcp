# cinema4d-mcp C++ Shim Plugin — Design

**Status:** design / pre-implementation
**Created:** 2026-04-30 (post-Scene-Nodes deep-dive session)
**Motivation:** Two Maxon SDK primitives are needed for full Scene Nodes capsule authoring but aren't exposed in Python: (1) `GraphModelInterface::AddPort(parent, Id name)` for adding named ports to Floating IOs, (2) the NodeTemplate-typed asset publishing path for surfacing FIO routing as Attribute Manager parameters. Both are accessible in C++. A small companion plugin closes the gap without rewriting the existing Python MCP.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Claude / agent  ←→  cinema4d-mcp Python server (MCP)   │
└─────────────┬───────────────────────────────────────────┘
              │  socket (already in place, port 5353)
              ▼
┌─────────────────────────────────────────────────────────┐
│  mcp_server_plugin.pyp  (Python plugin, 99+ handlers)   │
│  ├── orchestration: scene ops, recipes, descids,        │
│  │   pattern synthesis, asset save/load (File-type)     │
│  └── delegates 2 commands to C++ shim:                  │
│      scene_nodes_add_floating_io_port                   │
│      scene_nodes_publish_capsule_asset                  │
└─────────────┬───────────────────────────────────────────┘
              │  c4d.plugins.FindPlugin(SHIM_ID).Message()
              ▼
┌─────────────────────────────────────────────────────────┐
│  cinema4d_mcp_helper.cdl64  (C++ plugin, MessageData)   │
│  ├── primitive 1: GraphModelInterface::AddPort          │
│  └── primitive 2: NodeTemplate publishing path          │
└─────────────────────────────────────────────────────────┘
```

**Why a sibling plugin and not a fork:** the existing Python plugin is healthy and full-featured. Pairing it with a tiny C++ helper preserves the orchestration layer (and all 99 existing tools) while adding the missing primitives. Same install path (`plugins/cinema4d-mcp/`), same lifecycle, same MCP tool surface to the agent.

---

## Primitive 1 — `add_floating_io_port`

**Wraps:** `frameworks/graph.framework/source/maxon/graph.h:891`

```cpp
MAXON_METHOD Result<GraphNode> AddPort(const GraphNode& parent, const Id& name);
```

**Why C++:** Python's `maxon.frameworks.graph.NodesGraphModelRef` only wraps the plural form `AddPorts(parent, index, count)` which requires the parent to satisfy `PORT_FLAGS::VARIADIC_TEMPLATE` — neither the FIO node nor the PORTLIST port satisfy this. The singular `AddPort(parent, Id name)` is the API the C4D editor uses internally on drag-wire, but it's C++-only.

**Shape (Python-side, exposed via MCP):**

```python
@mcp.tool()
async def scene_nodes_add_floating_io_port(
    floating_io_node: str,         # the FIO instance ID, e.g. 'floatingio@HASH'
    canonical_attr_path: str,       # e.g. 'net.maxon.nodes.scene.geo.spline.generator.edgetospline.reverse'
    direction: str = "input",       # 'input' (false) or 'output' (true)
    graph_target: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Add a named port pair (hiddenin1.<canonical>, in1.<canonical>) to a
    Floating IO node, replicating what the C4D editor does on drag-wire.
    Routed through the C++ shim because Python only exposes the plural,
    count-based AddPorts which doesn't accept names.
    """
```

**C++ side (skeleton):**

```cpp
// In cinema4d_mcp_helper plugin's MessageData::Message handler
case MSG_ADD_FLOATING_IO_PORT: {
    auto* args = static_cast<AddPortArgs*>(data);
    NodesGraphModelRef graph = ...; // resolved from args
    GraphNode fio = graph.GetNode(args->fio_path) iferr_return;
    
    // Add hiddenin1.<canonical>
    Id hidden_name("hiddenin1." + args->canonical);
    GraphTransaction txn = graph.BeginTransaction() iferr_return;
    GraphNode hiddenPort = graph.AddPort(fio, hidden_name) iferr_return;
    
    // Add in1.<canonical>
    Id in_name("in1." + args->canonical);
    GraphNode inPort = graph.AddPort(fio, in_name) iferr_return;
    
    // Set ATTRIBUTE_DIRECTION = (direction == "output")
    fio.SetValue(maxon::Id("net.maxon.node.floatingio.attribute.direction"),
                 args->direction_is_output) iferr_return;
    
    txn.Commit() iferr_return;
    return MAXON_OK;
}
```

**Verification (Python-side, post-call):** `floatingio.GetInputs()` should now contain the new `hiddenin1.<canonical>` port; `floatingio.GetOutputs()` the new `in1.<canonical>` port. Mirror the existing `connect_ports` post-commit `GetConnections()` verification pattern.

---

## Primitive 2 — `publish_capsule_asset`

**Wraps:** the C++ NodeTemplate publishing path. Specific entry point TBD in implementation — candidates include `AssetCreationInterface::CreateAsset` overload with `AssetTypes::NodeTemplate()`, or a direct `WriteDescription` + `.c4dnodes` serialization.

**Why C++:** Python's `AssetCreationInterface` exposes 32 methods but none of them produce a `net.maxon.node.assettype.nodetemplate`-typed asset. `CreateObjectAsset` produces `net.maxon.assettype.file` (round-trippable as `.c4d` but doesn't surface FIO routing as AM params). NodeTemplate registration is the formal asset-type for Scene Nodes capsules — what Edge to Spline / Random Selection / etc. ship as.

**Shape (Python-side, exposed via MCP):**

```python
@mcp.tool()
async def scene_nodes_publish_capsule_asset(
    object_name: str,                # SN Generator with embedded graph + configured FIOs
    asset_id: str,                   # e.g. 'com.userdomain.mycapsule'
    asset_name: str,                 # human-readable
    asset_version: str = "1.0",
    parent_category: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Publish an SN Generator's embedded graph as a NodeTemplate-typed
    asset (.c4dnodes format). After publish, the asset appears in the C4D
    Asset Browser and dragging it into a doc creates an instance whose
    Floating IOs surface as Attribute Manager parameters — the same way
    Edge to Spline and other Maxon-shipped capsules behave.
    """
```

**Reference — Maxon's NodeTemplate URL pattern (observed 2026-04-30):**
- ID: `net.maxon.nodes.scene.geo.spline.generator.edgetospline`
- Type: `net.maxon.node.assettype.nodetemplate`
- URL: `ramdisk://A9C0F37BAE5146F2/.../1/asset.c4dnodes`

We need to mirror this: write a `.c4dnodes` file in the user prefs repo, register it via `AssetCreationInterface::CreateAsset` (or whichever C++ overload accepts `AssetTypes::NodeTemplate()`).

---

## Build / Install Plan

**Source location:** `cinema4d-mcp/cpp_shim/` (new directory).

**Build system:**
- Use the C4D 2026 SDK's CMake project template at `frameworks/example.framework/`.
- Output: `cinema4d_mcp_helper.cdl64` (Win64) / `.xdl64` (macOS).
- Sign on macOS per Maxon's signing requirements.

**Install paths:**
- Win: `%APPDATA%/Maxon/Maxon Cinema 4D 2026_<HASH>/plugins/cinema4d-mcp/cinema4d_mcp_helper.cdl64`
- Loads alongside the existing `mcp_server_plugin.pyp`.

**Build script:** add `scripts/build_cpp_shim.sh` (mirror existing `build_spikr2.sh` pattern).

---

## Phasing

**Phase A — Primitive 1 only (estimate: 2-3 hours)**
- Wire `add_floating_io_port` end-to-end.
- Test: programmatically replicate Edge to Spline's 5-FIO config inside a fresh SN Generator. Use the existing `scene_nodes_dissect_capsule` to compare port shapes.
- Ship as MCP tool.

**Phase B — Primitive 2 (estimate: 4-8 hours, depends on which C++ entry point works)**
- Spike the NodeTemplate publish path. Multiple candidates (`CreateAsset` overload, `WriteDescription`, direct `.c4dnodes` write).
- Verify: after publish, dragging the asset from the asset browser surfaces the configured FIO params in the AM.
- Ship as MCP tool.

**Phase C — Integration (estimate: 1-2 hours)**
- Update `scene_nodes_create_capsule_with_pattern` to optionally chain: synthesize → add named FIO ports (Phase A) → publish (Phase B) → reload as native NodeTemplate.
- Add a battle-test recipe for the full pipeline.
- Update guide §6.1 + §8 to remove the "C++ shim is the path forward" caveats.

**Phase D — Operational (when the user builds with this for real)**
- Wire failure-mode tests for missing/duplicate canonical paths
- Add an "asset library" concept on top: structured save/list/categorize for user-saved capsules
- Optional: command for asset un-publish / overwrite

---

## Open questions to resolve during implementation

1. **What's the canonical attribute path for a USER-DEFINED parameter?** Maxon's shipped capsules use canonicals like `net.maxon.nodes.scene.geo.spline.generator.edgetospline.reverse` (the asset-id + param suffix). For user capsules, do we mint our own (`com.userdomain.mycapsule.<param>`) or does Maxon expect a particular convention?
2. **Does `AddPort` on a Floating IO require the FIO's PORTLIST to be pre-typed?** If yes, we may need to write a typed value to PORTLIST first.
3. **What does the `.c4dnodes` file actually contain?** A graph serialization + a description schema mapping FIO canonicals → DescIDs. Investigate by examining a Maxon-shipped `.c4dnodes` byte-for-byte if accessible, OR by writing one via `WriteDescription` and inspecting.
4. **Does the C++ shim need its own plugin registration, or can it just be a `MessageData` listener on a fixed plugin ID?** MessageData is simpler and is the same pattern the existing `UiActionObserver` uses — preferred.

---

## What this unlocks

When both primitives ship:

- The MCP can fully author capsules end-to-end: synthesize graph → add named FIO ports → publish as NodeTemplate → asset auto-appears in the Asset Browser.
- Drag-from-asset-browser produces a fresh instance whose AM parameters are exactly the user-defined ones.
- `scene_nodes_create_capsule_with_pattern` becomes a true "I GOT YOU EASY" pipeline — agent can ship a tunable artist deliverable in one call.
- The MCP's "graph authoring" reputation upgrades from "build the structure" to "ship the polished tool."

This is the holy-grail Scene Nodes capability the user identified. The Python deep-dive established the boundary of what's possible; the C++ shim crosses it.
