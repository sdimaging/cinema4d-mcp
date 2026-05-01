# Cinema 4D MCP

Production-grade Cinema 4D ↔ Claude bridge over [Model Context Protocol](https://modelcontextprotocol.io). Lets Claude read scene state, manipulate objects, render previews, run Python in the C4D evaluation context, introspect plugins/renderers, and a lot more — driven from a chat conversation.

This is a **substantially extended fork** of [`ttiimmaacc/cinema4d-mcp`](https://github.com/ttiimmaacc/cinema4d-mcp) hardened for daily-driver and public-deployment use:

- **+70 new tools** beyond upstream (95 total) — capability discovery, scene snapshot/diff, UV ops, viewport perception, undo grouping, doctor, ping, **Scene Nodes authoring + dissection + classification + gesture differ + typed-port synthesis**, Octane OSL, fields, deformers, volume builders
- **Scene Nodes knowledge layer** — a comprehensive [practical guide](docs/scene_nodes_guide.md) + 22 codified patterns + 802 categorized template asset IDs + 40 verified `$type` labels + port-type taxonomy. The MCP can now dissect any capsule, classify any graph, and synthesize artist-ready capsules with one call (`create_capsule_with_pattern`).
- **Scene Nodes gesture differ + typed-port synthesizer** ([findings](docs/gesture_differ_findings.md)) — `scene_nodes_record_gesture` snapshots graph state before/after a manual editor gesture and returns the precise structural diff. `scene_nodes_synthesize_port` then packages the recipe into a single call: `AddPort + SetPortValue + Connect to typed inner port` — produces a fully draggable AM-exposed parameter wired through to the inner node, with widget binding inferred at runtime from the connection. End-to-end programmatic Scene Nodes capsule parameter exposure, no manual Resource Editor pass needed.
- **Auth-token gate** + **safe-mode env gate** + **constant-time token compare** — exposes the bridge to less-trusted contexts safely
- **Standardized response envelope** — every response carries `ok`, `duration_ms`, `request_id`, `warnings`
- **Bounded payload check** + **request correlation** — production-grade transport
- **AST-based contract tests** — every tool guaranteed to have a real handler before commit
- **C4D 2026 compatibility** — verified against the current Python API, with explicit fixes for renderer integration, modeling commands, BaseDraw shading, and the C4D 2026 Scene Nodes maxon-frameworks API surface

---

## Components

1. **C4D Plugin** ([`c4d_plugin/mcp_server_plugin.pyp`](c4d_plugin/mcp_server_plugin.pyp)) — socket server that runs inside the C4D process. Handles JSON commands and returns JSON responses.
2. **MCP Server** ([`src/cinema4d_mcp/`](src/cinema4d_mcp/)) — FastMCP-based Python server. Receives tool calls from a Claude client, sends them to the plugin over TCP, formats responses.

---

## Prerequisites

- Cinema 4D **2024+** (primary target: 2026)
- Python **3.10+** for the MCP server side

## Installation

```bash
git clone https://github.com/sdimaging/cinema4d-mcp.git
cd cinema4d-mcp
pip install -e .
chmod +x bin/cinema4d-mcp-wrapper
```

## Setup

### 1. Install the C4D plugin

Copy `c4d_plugin/mcp_server_plugin.pyp` to your Cinema 4D plugins folder:
- **Windows**: `%APPDATA%\Maxon\Maxon Cinema 4D <version>_<hash>\plugins\`
- **macOS**: `~/Library/Preferences/Maxon/Maxon Cinema 4D <version>_<hash>/plugins/`

Then restart C4D — the plugin auto-starts its socket on launch (or use `Extensions → Socket Server Plugin → Start Server` to start manually).

### 2. Configure your MCP client

#### Claude Desktop / Code

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "cinema4d": {
      "command": "cinema4d-mcp-wrapper",
      "args": []
    }
  }
}
```

Restart the client. Look for the 🔨 icon to confirm tools are loaded.

### 3. (Optional) WSL2 environment

If you run the MCP server inside WSL2 while C4D runs on Windows host, the server auto-detects the Windows IP. To override:

```bash
export C4D_HOST=192.168.x.x   # your Windows host IP
export C4D_PORT=5555           # default
```

WSL-style paths (`/mnt/c/...`) passed in tool args are auto-translated to native Windows paths (`C:\...`) before sending to C4D — you can pass either form.

---

## Production hardening

### Environment variables

| Variable | Default | Effect |
| --- | --- | --- |
| `MCP_AUTH_TOKEN` | unset | When set, every command must carry a matching `auth_token` field. Constant-time comparison via `hmac.compare_digest`. Server.py auto-attaches the env value to outgoing commands. |
| `MCP_SAFE_MODE` | unset | When truthy (`1`/`true`/`yes`/`on`), the dispatcher rejects every command not in the SAFE allowlist. ~28 read-only commands (inspect/find/dump/screenshot/snapshot) remain available. |
| `C4D_HOST` | auto | Windows host IP for WSL→C4D socket. Auto-detected from default gateway when unset. |
| `C4D_PORT` | `5555` | Plugin socket port. |
| `C4D_MCP_NO_AUTOSTART` | unset | When set, plugin will NOT auto-start its socket on C4D launch (manual menu start required). |

### Standardized response envelope

Every response from the plugin carries:
```json
{
  "ok": true,
  "duration_ms": 12,
  "request_id": "<echoed if client supplied>",
  "warnings": ["..."],
  ...handler-specific fields...
}
```

On error: `ok=false`, plus `error` (str) and optionally `traceback` (str).

Existing per-handler fields (`status`, `image_data`, `path`, etc.) are preserved at the top level — backward compatible with v0.1.x clients.

### Bounded payload check

Commands larger than 5MB are rejected before parsing. Avoids OOM from malformed clients.

### Contract tests

```bash
python tests/test_contract.py
```

AST walks `server.py` + `mcp_server_plugin.pyp` and asserts:
1. Every `@mcp.tool()` command_type has a dispatcher branch
2. Every dispatcher branch calls a defined `handle_<name>` method
3. `_SUPPORTED_COMMANDS` advertises every routable branch
4. No "phantom" advertised commands without real branches
5. Every MCP tool is in the advertised set

Run before every commit. Currently: **93 tools / 93 branches / 93 advertised**, all 5 tests pass.

---

## Tool inventory (95 total)

### Scene Nodes (the C4D 2026 node-graph authoring surface)

**See [`docs/scene_nodes_guide.md`](docs/scene_nodes_guide.md) for the comprehensive guide** — 6-layer architecture, 22 codified patterns, 40 verified `$type` labels, port-type taxonomy, 5 anti-patterns, 3 architectural design principles, recommended workflows.

Authoring tools:
- `scene_nodes_create_capsule_with_pattern` — **the "I GOT YOU EASY" tool**. One call: creates a fresh SN Generator/Deformer object AND populates its embedded graph with a named pattern. Capsule appears in the object tree, ready for parameterization
- `scene_nodes_apply_pattern` — synthesize a known pattern into an existing graph (dry_run mode for inspection)
- `scene_nodes_add_node` — add a single node by `$type` label
- `scene_nodes_connect_ports` — wire two nodes by port name
- `scene_nodes_create_graph` — bootstrap a graph on the doc or an object
- `scene_nodes_open_editor` — open the Scene Nodes editor
- `scene_nodes_synthesize_port` — **programmatically add an AM-exposed user parameter to a Scene Nodes Generator, wired through to a specific inner-node port**. Single call: `(target_object, name, target_node_id, target_port_id, data_type, default_value, display_label)` → port appears in AM with the correct widget (slider for Float, integer field for Int, Vector XYZ field, etc.), drives the inner node's parameter when dragged. The widget binding comes from the connection (C4D infers type at runtime from the connected downstream port) — no description-dict construction needed. Replaces right-click "Add Input" + Resource Editor type pick + manual wire. See [findings doc](docs/gesture_differ_findings.md) for the full reverse-engineering history.

Reverse-engineering tool:
- `scene_nodes_record_gesture` — snapshot the graph state before/after a manual editor gesture, returns the precise structural diff (added/removed nodes, ports, connections, and parameter values). Enables "show me, then I'll script it" workflows. Used to crack the right-click "Add Input" gesture down to its public-API recipe — see [findings](docs/gesture_differ_findings.md)

Inspection tools:
- `scene_nodes_status` — survey doc + per-object embedded graphs
- `scene_nodes_walk` — typed graph tree (id/kind/port-counts/children) with depth control
- `scene_nodes_dissect_capsule` — auto-scans for capsule-class objects (5171, 180420400, 180420500/600/700, 440000274, 1057221), walks each one's embedded graph, returns asset_ids list. Cumulative session registry
- `scene_nodes_describe_node_template` — add a node, walk its ports, remove it. The "what are this node's port names?" tool
- `scene_nodes_classify_graph` — walks any graph, builds vocabulary histogram, matches against pattern signatures, returns probable_purpose
- `scene_nodes_atlas_lookup` — search 802 templates / 22 patterns / port_types / antipatterns / vocabulary classes by substring
- `scene_nodes_list_assets` — enumerate discovered + repository asset IDs

Bundled data ([`data/`](data/)):
- `scene_nodes_atlas.json` — patterns, port-type taxonomy, antipatterns, design principles
- `node_template_index.json` — all 802 NodeTemplate canonical asset IDs categorized into 16 buckets
- `verified_labels.json` — ApplyDescription `$type` labels confirmed working
- `node_port_schema.json` — full input/output port names per template

### Capability discovery + health
- `get_capabilities` — plugin/c4d versions, supported commands (safe vs unsafe), auth state, doc summary
- `doctor` — 5 independent health checks (main thread, doc, viewport, log hook, auth) with timing
- `ping` — cheapest possible liveness probe (doesn't acquire main thread)

### Scene snapshot + diff (GUID-first)
- `scene_snapshot` — typed, GUID-keyed model of the doc; `summary` or `full` detail; cached server-side (last 16) by `snapshot_id`
- `scene_diff` — added/removed/transform_changed/name_changed/topology_changed/tag_changes/material_diff between two snapshots

### Undo grouping (atomic multi-step ops)
- `begin_undo_group(name)` / `end_undo_group()` — wrap multi-step mutations into one undo entry
- `undo(steps=1)` / `redo(steps=1)` — programmatic Cmd-Z / Cmd-Shift-Z

### Scene & objects
- `get_scene_info`, `list_objects`, `find_objects`, `get_object_info`, `dump_object_tree`, `group_objects`, `modify_object`, `find_object_by_guid`

### Primitives & shapes
- `add_primitive`, `create_abstract_shape`

### Materials & shaders
- `create_material`, `apply_material`, `apply_shader`, `link_shader_to_parameter`, `inspect_redshift_materials`, `validate_redshift_materials`, `dump_material_graph`

### Cameras & lighting
- `create_camera`, `animate_camera`, `create_light`

### MoGraph & dynamics
- `create_mograph_cloner`, `add_effector`, `apply_mograph_fields`, `create_soft_body`, `apply_dynamics`

### Animation & file I/O
- `set_keyframe`, `save_scene`, `load_scene`, `snapshot_scene`

### Rendering
- `render_frame`, `render_preview`, `viewport_screenshot`, `viewport_screenshot_multiview`, `list_render_engines`, `get_active_renderer`

### Viewport perception
- `set_viewport_shading_mode` (8 modes + line overlays + auto-translate to baked `*_wire`)
- `get_viewport_state`

### Modeling
- `run_modeling_command` — wraps `SendModelingCommand` for axis_center (pure-math impl), optimize, make_editable, current_state_to_object, subdivide, smooth, connect, split, disconnect, bevel, inset, extrude, delete, polygonize, triangulate, untriangulate

### Deformers
- `list_deformer_types` — discover available deformer plugins on this install
- `apply_deformer` — apply any deformer to a target object with optional vertex-map restriction binding (Restriction tag schema RESTRICTIONTAG_NAME_01 + RESTRICTIONTAG_VAL_01)

### Fields
- `list_field_types` — discover available field plugins
- `add_field_to_scene` — add a Spherical/Linear/Box/Random/Noise/etc. field to the active doc
- `bake_field_to_vmap` — sample a field across an object's points and write the result to a Vertex Map tag. Uses `c4d.modules.mograph.FieldList.SampleListSimple(host, FieldInput, FIELDSAMPLE_FLAG_VALUE)` (top-level `c4d.FieldList`, not in `c4d.modules.mograph`)

### Volume builders + meshers
- `create_volume_builder` — VDB volume builder with object inputs
- `create_volume_mesher` — VDB volume mesher (post-processes builder output to polygons)
- `volume_to_polygons` — bake the volume mesher's output to a static polygon object

### Octane OSL (deep Octane integration)
- `list_osl_snippets` — bundled OSL kernels (10 snippets: constant_red, uv_gradient, checker, polar_warp, projection_xz_planar, projection_triplanar, camera_pinhole, camera_fisheye_180, camera_anamorphic_squeeze, camera_vortex)
- `octane_create_osl_texture` — create an Octane OSL Texture shader (plugin id 1039813) and inject a snippet, optionally drop into a host material's diffuse channel
- `octane_set_camera_to_osl` — attach an Octane Camera tag (id 1029524) to a CameraObject, switch mode to `OCT_CAMERA_OSL` (3), inject an OSL kernel via `OCTANECAM_OSL_CODE_EDITOR`
- `tap_octane_log` — read Octane's `c4doctanelog.txt` from `%APPDATA%/Maxon/.../c4doctanelog.txt`

### Scene Nodes asset discovery (legacy — superseded by Scene Nodes section above)
- `scene_nodes_dissect_capsule`, `scene_nodes_list_assets`, `scene_nodes_add_node` (still functional, but `scene_nodes_create_capsule_with_pattern` is the recommended high-level entrypoint)

### Vertex maps
- `vertex_map_stats`, `vertex_map_threshold_to_polygon_selection`
- `paint_vertex_map_from_formula` — write per-vertex values from a Python expression (access to vert pos/index/normal)
- `paint_vertex_map_radial` — paint a radial gradient from a center point with falloff

### Generic parameter access
- `get_parameter` / `set_parameter` — universal access to any object/tag/material parameter by ID or name. Auto-resolves DescID at runtime

### Image inspection + comparison
- `image_inspect` — md5, pixel variance, is_blank detection, dimensions, format
- `images_compare` — pairwise comparison of two images on disk

### Scene assertion + recipe runner (the feedback loop)
- `scene_assert` — declarative scene-state checks: object_exists, object_has_tag, object_polygon_count, object_point_count, vmap_stats, object_count, material_count, with approximate-equality tolerance for Float32 quantization
- `recipe_run` — execute a JSON recipe (setup + steps with assertions + teardown) atomically. Returns per-step pass/fail. The unit of regression testing

### UV ops
- `uv_layout_stats` — polygon-graph-correct island detection (keys on `(point_idx, uv)`)
- `uv_islands_to_objects` — split by island, preserve 3D positions
- `uv_from_projection` — box / sphere / cylinder / planar_xy / planar_xz / planar_yz
- `uv_transfer` — closest-point-on-source UV projection between meshes
- `sample_vmap_via_uv` — barycentric interpolation across UV-shared topologies
- `sample_bitmap_at_uv` — bake a B/W bitmap → vertex map via UVs

### Python execution
- `execute_python_script` — full Python in C4D evaluation context (gated by auth + safe-mode)

### Introspection
- `enumerate_descids`, `enumerate_userdata`, `find_command_by_name`, `enumerate_octane_plugins`

### Plugin development
- `list_installed_plugins`, `build_and_install_plugin`, `install_plugin`, `get_c4d_info`

### Console & diagnostics
- `get_console_log`, `clear_console_log`, `tap_octane_log`

### Generic helpers
- `create_via_command`

---

## Companion: standalone Cinema 4D scripts

For procedural scripts that run directly in C4D's Script Manager (no MCP needed): **[sdimaging/c4d-scripts](https://github.com/sdimaging/c4d-scripts)**.

Currently includes a UV pipeline for "cut holes via painted UV map" workflows — flatten a mesh to its UV layout as planar geo, modify the flat (boolean cuts, polygon delete, etc.), then deform the modified flat back to the curved 3D source.

---

## Development

### Adding a new tool

1. **C4D plugin side** (`c4d_plugin/mcp_server_plugin.pyp`): add `def handle_<name>(self, command):` returning a dict. Register the command in the dispatcher: `elif command_type == "<name>": response = self.handle_<name>(command)`.
2. **Add to** `_SUPPORTED_COMMANDS` (and `_SAFE_COMMANDS` if read-only).
3. **MCP server side** (`src/cinema4d_mcp/server.py`): add an `@mcp.tool()` async function that builds the command dict and calls `send_to_c4d()`.
4. **Run contract tests**: `python tests/test_contract.py` — must pass before commit.
5. **Sync**: `bash scripts/sync_to_installed.sh` writes the .pyp to your installed plugins folder.
6. **Activate**: full C4D restart for new dispatcher branches; VS Code / Claude Desktop restart for new MCP tool wrappers.

### Testing

```bash
python tests/test_contract.py                # AST-level contract tests (no C4D needed)
python main.py                                # Direct test of the bridge
python tests/mcp_test_harness_gui.py          # GUI for replaying test JSONL command sequences
```

---

## Troubleshooting

```bash
tail -f ~/Library/Logs/Claude/mcp*.log         # macOS Claude logs
cinema4d-mcp-wrapper                            # Test the wrapper standalone
pip install mcp                                 # If "module not found"

# MCP Inspector for live debugging:
npx @modelcontextprotocol/inspector uv --directory /path/to/cinema4d-mcp run cinema4d-mcp
```

**Plugin not auto-starting socket?** Check `mcp_server_plugin.pyp` is in the right plugins folder. Set `C4D_MCP_NO_AUTOSTART=1` if you want manual start instead.

**"Reload Python Plugins" doesn't pick up new tools?** Stop→Start the socket server, OR full C4D restart for class-definition changes. The .pyp's class is parsed once at startup.

**Tool calls return "unknown command"?** Run `tests/test_contract.py` to surface mismatches between the MCP wrapper and the dispatcher. Most often: forgot to add a dispatcher branch.

**Need to debug a wedged main thread?** Open the Socket Server Plugin dialog → Stop Server → Start Server. Recovers cleanly without a full C4D restart.

---

## Roadmap

Production phases shipped:
- ✅ **Phase 1**: auth-token gate, sandbox theatre dropped
- ✅ **Phase 2**: 11 verified bug fixes (load_scene, _timeout, render_preview, axis_center, multiview, WSL paths, etc.)
- ✅ **Phase 3**: capability discovery (`get_capabilities`, `doctor`)
- ✅ **Phase 4**: GUID-first scene snapshot + diff (16-snapshot LRU cache)
- ✅ **Phase 5**: `MCP_SAFE_MODE` env gate
- ✅ **Phase 8**: AST contract tests
- ✅ Transport hardening: `request_id` echo, bounded payload, constant-time auth, response envelope, `ping`
- ✅ Undo grouping: `begin/end_undo_group`, `undo`, `redo`

Pending:
- **Phase 6**: length-prefixed transport framing (current newline-split is correct for JSON but length-prefix is the modern protocol contract)
- **Phase 7**: transactions / batch operations (multi-command atomicity beyond undo grouping)
- Per-handler unit test harness (fake C4D socket — runs every handler against canned inputs)

---

## Project structure

```
cinema4d-mcp/
├── README.md
├── LICENSE                              MIT (dual copyright: upstream + this fork)
├── pyproject.toml
├── main.py                              Server entry point (direct test runner)
├── bin/
│   └── cinema4d-mcp-wrapper             Shell launcher resolved by Claude Desktop
├── c4d_plugin/
│   └── mcp_server_plugin.pyp            C4D-side socket server + handlers (~62 commands, ~10K lines)
├── src/cinema4d_mcp/
│   ├── __init__.py
│   ├── __main__.py                      Allows `python -m cinema4d_mcp`
│   ├── server.py                        FastMCP server + 62 tool definitions
│   ├── config.py                        Host/port resolution (incl. WSL2 detection)
│   └── utils.py                         Logging
├── scripts/
│   └── sync_to_installed.sh             Copies .pyp to %APPDATA%\Maxon\... after edits
└── tests/
    ├── test_contract.py                 AST contract tests (run on every commit)
    ├── test_server.py
    ├── mcp_test_harness.jsonl
    └── mcp_test_harness_gui.py
```

---

## License

MIT. See [LICENSE](LICENSE).

This fork carries dual copyright:
- Original work © 2025 Tim Mac ([github.com/ttiimmaacc/cinema4d-mcp](https://github.com/ttiimmaacc/cinema4d-mcp))
- Modifications and extensions © 2026 Spenser Dickerson / SD Imaging

---

## C4D version compatibility

| C4D version | Status |
| --- | --- |
| **2026** | ✅ Primary target (verified against 2026.2) |
| 2025 | ✅ Supported |
| 2024 | ✅ Supported |
| 2023.x | ⚠️ Likely works, untested on this fork |
| ≤ R25 | ❌ Not supported |
