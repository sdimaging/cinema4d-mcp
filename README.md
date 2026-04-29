# Cinema 4D MCP — Extended Fork

Cinema 4D ↔ Claude bridge over [Model Context Protocol](https://modelcontextprotocol.io). Lets Claude read scene state, run Python in the C4D evaluation context, render previews, manipulate objects, manage materials, and a lot more — driven from a chat conversation.

This is a **substantially extended fork** of [`ttiimmaacc/cinema4d-mcp`](https://github.com/ttiimmaacc/cinema4d-mcp). The original 25 tools are still here; this fork adds **+21 more tools** (46 total) plus production hardening for C4D 2026, WSL2, and Octane workflows.

---

## What this fork adds over upstream

### Production hardening
- **C4D 2026 compatibility** — verified against current C4D Python API
- **WSL2 reachability** — plugin binds `0.0.0.0`; MCP server auto-detects Windows host IP from default gateway when running inside WSL
- **Socket auto-start** — plugin starts its socket on `C4DPL_PROGRAM_STARTED` (no manual menu click needed; opt out via `C4D_MCP_NO_AUTOSTART=1`)
- **Lifecycle recovery** — Stop→Start now recovers cleanly from dead-thread states without restarting C4D
- **Console log ring buffer** — 10K-entry in-process buffer with `c4d.GePrint` hook; surface via `get_console_log` tool

### viewport_screenshot fix
The default `Standard` renderer returns all-black bitmaps when Octane is installed (Octane hooks the standard render pipeline). This fork:
- Defaults `renderer` to `"hardware"` (OpenGL preview — always works)
- Auto-detects all-black renders and falls back to `"hardware"` with a warning surfaced in the response
- Fixes the underlying engine-override mechanism (sets engine on `clone` directly rather than the `GetData()` copy)

### +21 introspection / plugin-dev / render-engine tools
- **Introspection**: `enumerate_descids`, `enumerate_userdata`, `dump_object_tree`, `get_viewport_state`, `find_command_by_name`, `dump_material_graph`, `inspect_redshift_materials`, `find_objects`, `get_object_info`
- **Plugin development**: `build_and_install_plugin`, `install_plugin`, `list_installed_plugins`, `get_console_log`, `clear_console_log`, `get_c4d_info`
- **Render engine awareness**: `list_render_engines`, `get_active_renderer`, `enumerate_octane_plugins`, `tap_octane_log`
- **Generic helpers**: `link_shader_to_parameter`, `create_via_command`, `viewport_screenshot` (rebuilt with auto-fallback)

---

## Components

1. **C4D Plugin** (`c4d_plugin/mcp_server_plugin.pyp`) — Cinema 4D plugin that runs a TCP socket server inside the C4D process. Handles JSON commands and returns JSON responses.
2. **MCP Server** (`src/cinema4d_mcp/`) — Python MCP server. Receives tool calls from Claude, sends them to the plugin over TCP, formats responses.

---

## Prerequisites

- Cinema 4D 2024+ (primary target: 2026)
- Python 3.10+

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

Then either:
- Restart C4D (the plugin auto-starts its socket on launch), or
- `Extensions → Socket Server Plugin → Start Server`

### 2. Configure Claude Desktop

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

Restart Claude Desktop. Look for the 🔨 icon to confirm tools are loaded.

### 3. (Optional) WSL2 environment

If you run the MCP server inside WSL2 while C4D runs on Windows host, the server auto-detects the Windows IP. To override:

```bash
export C4D_HOST=192.168.x.x   # your Windows host IP
export C4D_PORT=5555           # default
```

---

## Companion: standalone Cinema 4D scripts

For procedural scripts that run directly in C4D's Script Manager (no MCP needed), see the companion repo: **[sdimaging/c4d-scripts](https://github.com/sdimaging/c4d-scripts)**.

Currently includes:
- **UV pipeline** — flatten a mesh into its UV layout as planar geo, modify the flat geo (boolean cuts, polygon delete, etc.), then deform the modified flat back to the curved 3D source. Useful for any "cut holes via painted UV map" workflow.

---

## Tool inventory (46 total)

### Scene & objects
- `get_scene_info`, `list_objects`, `find_objects`, `get_object_info`, `dump_object_tree`, `group_objects`, `modify_object`

### Primitives & shapes
- `add_primitive`, `create_abstract_shape`

### Materials & shaders
- `create_material`, `apply_material`, `apply_shader`, `inspect_redshift_materials`, `dump_material_graph`, `link_shader_to_parameter`

### Cameras & lighting
- `create_camera`, `animate_camera`, `create_light`

### MoGraph & dynamics
- `create_mograph_cloner`, `add_effector`, `apply_mograph_fields`, `create_soft_body`, `apply_dynamics`

### Animation
- `set_keyframe`

### File I/O
- `save_scene`, `load_scene`, `snapshot_scene`

### Rendering
- `render_frame`, `render_preview`, `viewport_screenshot`, `list_render_engines`, `get_active_renderer`

### Python execution
- `execute_python_script`

### Introspection (new in this fork)
- `enumerate_descids`, `enumerate_userdata`, `find_command_by_name`, `get_viewport_state`, `enumerate_octane_plugins`

### Plugin development (new in this fork)
- `list_installed_plugins`, `build_and_install_plugin`, `install_plugin`, `get_c4d_info`

### Console & diagnostics (new in this fork)
- `get_console_log`, `clear_console_log`, `tap_octane_log`

### Generic command runner
- `create_via_command`

---

## Development

### Testing

```bash
python main.py                              # Direct test of the bridge
python tests/mcp_test_harness_gui.py        # GUI for replaying test JSONL command sequences
```

### Adding a new tool

Tools are defined in two places:

1. **MCP server side** (`src/cinema4d_mcp/server.py`): add an `@mcp.tool()` async function that builds a command dict and calls `send_to_c4d()`.
2. **C4D plugin side** (`c4d_plugin/mcp_server_plugin.pyp`): add a `handle_<command>()` method to the dispatcher and register it in the command-dispatch table.

Response envelope convention: `{"status": "ok", ...}` on success, `{"error": "..."}` on failure. Use `format_c4d_response()` for consistent markdown output.

---

## Troubleshooting

```bash
tail -f ~/Library/Logs/Claude/mcp*.log         # macOS Claude logs
cinema4d-mcp-wrapper                            # Test the wrapper standalone
pip install mcp                                 # If "module not found"

# MCP Inspector for live debugging:
npx @modelcontextprotocol/inspector uv --directory /path/to/cinema4d-mcp run cinema4d-mcp
```

If the socket auto-start log doesn't appear in the C4D console, the plugin didn't load — check that `mcp_server_plugin.pyp` is in the right plugins folder and that no other plugin is blocking on import. Set `C4D_MCP_NO_AUTOSTART=1` in your environment if you want manual start instead.

---

## Project structure

```
cinema4d-mcp/
├── LICENSE                              MIT (dual copyright: upstream + this fork)
├── README.md
├── pyproject.toml
├── main.py                              Server entry point (direct test runner)
├── bin/
│   └── cinema4d-mcp-wrapper             Shell launcher resolved by Claude Desktop
├── c4d_plugin/
│   └── mcp_server_plugin.pyp            C4D-side socket server + handlers
├── src/cinema4d_mcp/
│   ├── __init__.py
│   ├── server.py                        FastMCP server + 46 tool definitions
│   ├── config.py                        Host/port resolution (incl. WSL2 detection)
│   └── utils.py                         Logging
└── tests/
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
| 2026 | ✅ Primary target |
| 2025 | ✅ Supported |
| 2024 | ✅ Supported |
| 2023.x | ⚠️ Likely works, untested on this fork |
| ≤ R25 | ❌ Not supported |
