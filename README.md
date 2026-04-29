# Cinema 4D MCP

Production-grade Cinema 4D ↔ Claude bridge over [Model Context Protocol](https://modelcontextprotocol.io). Lets Claude read scene state, manipulate objects, render previews, run Python in the C4D evaluation context, introspect plugins/renderers, and a lot more — driven from a chat conversation.

This is a **substantially extended fork** of [`ttiimmaacc/cinema4d-mcp`](https://github.com/ttiimmaacc/cinema4d-mcp) hardened for daily-driver and public-deployment use:

- **+37 new tools** beyond upstream (62 total) — capability discovery, scene snapshot/diff, UV ops, viewport perception, undo grouping, doctor, ping
- **Auth-token gate** + **safe-mode env gate** + **constant-time token compare** — exposes the bridge to less-trusted contexts safely
- **Standardized response envelope** — every response carries `ok`, `duration_ms`, `request_id`, `warnings`
- **Bounded payload check** + **request correlation** — production-grade transport
- **AST-based contract tests** — every tool guaranteed to have a real handler before commit
- **C4D 2026 compatibility** — verified against the current Python API, with explicit fixes for renderer integration, modeling commands, and BaseDraw shading

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

Run before every commit. Currently: **62 tools / 62 branches / 62 advertised**, all 5 tests pass.

---

## Tool inventory (62 total)

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

### Vertex maps
- `vertex_map_stats`, `vertex_map_threshold_to_polygon_selection`

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
