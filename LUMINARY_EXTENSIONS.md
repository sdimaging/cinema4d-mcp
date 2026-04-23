# Luminary Extensions — cinema4d-mcp fork additions

This fork of [ttiimmaacc/cinema4d-mcp](https://github.com/ttiimmaacc/cinema4d-mcp) adds **21 new MCP tools** focused on Cinema 4D plugin development workflow — introspection, console capture, plugin lifecycle, viewport/render-engine inspection, and Octane integration helpers.

The original 25 upstream tools (object/material/MoGraph creation, rendering, etc.) are unchanged.

## Why this fork exists

Built to support C++ plugin development for Cinema 4D 2026 + Octane:
- **Luminary** (LightPainter port to C4D/Octane) — needs to crack undocumented Octane parameter IDs
- **SplatFlow** — gaussian splat plugin needing custom viewport renderer
- **MechFlow / Spikr / CastleGen** — existing generators getting revisited

These tools turn the typical plugin-dev loop (write code → build → install → restart C4D → manually test → check console → repeat) into something the AI can drive end-to-end.

---

## New tools by tier

### Tier 1 — Introspection (5 tools)

The cornerstone. **`enumerate_descids` is the single most important tool** in this set: it walks an object's full Description and returns every parameter as JSON, cracking undocumented plugin parameter IDs in seconds. Mirrors what C4D's "Customize Palettes → search by name" does, but scriptable.

| Tool | Purpose |
|------|---------|
| `enumerate_descids(object_name?, guid?, name_filter?, name_pattern?, top_level_only?, ...)` | Dump every parameter (DescID + name + type + current value) of an object. Filter by name substring or fnmatch pattern. **Use this to find Octane Area Light's texture/distribution input ID.** |
| `enumerate_userdata(object_name?, guid?)` | Same but for the User Data container (separate API). |
| `find_objects(name_pattern?, name_contains?, type_id?, type_id_min/max?)` | Search the scene with name + type filters. `type_id_min/max` enables "scan a plugin ID range" — useful for finding all Octane objects without knowing exact IDs. |
| `get_object_info(object_name?, guid?)` | Comprehensive single-object dump: transform, visibility flags, layer, parent, child count, all tags. |
| `dump_object_tree(root_name?, root_guid?, max_depth?)` | Flat scene tree as `{depth, name, type_name, type_id, guid}` — start from doc root or any subtree. |

### Tier 2 — Console capture (2 tools)

A module-level ring buffer (10K entries) captures **both** the plugin's `self.log()` output AND C4D's `c4d.GePrint()` output (via runtime monkey-patch installed when the socket server starts). Means you can read C4D's console state without manually inspecting the console window.

| Tool | Purpose |
|------|---------|
| `get_console_log(limit?, since_ts?, source?, contains?)` | Read recent buffer entries with filters. `source` ∈ {`plugin`, `c4d.GePrint`, `luminary`}. |
| `clear_console_log()` | Empty the buffer (does NOT clear C4D's own console). |

### Tier 3 — Plugin lifecycle (4 tools)

Speeds up the build → install → load loop. `build_and_install_plugin` is the workflow-killer — wraps the user's standard cmake + cp pattern into one call.

| Tool | Purpose |
|------|---------|
| `list_installed_plugins(plugin_type?, plugin_id?, name_contains?, id_min?, id_max?)` | Enumerate loaded plugins with filters. `plugin_type` ∈ {object, tag, shader, material, command, tool, node, videopost, sculptbrush, falloff, field, ...}. |
| `get_c4d_info()` | Diagnostic dump: C4D version, Python version, install paths, prefs path, active document, Luminary buffer state. |
| `install_plugin(source_dir, install_dir?, plugin_name?, include_res?, overwrite?)` | **Server-side** file copy. Doesn't need a live C4D connection — pure shutil. Defaults install_dir to `/mnt/c/Program Files/Maxon Cinema 4D 2026/plugins/<plugin_name>`. |
| `build_and_install_plugin(target, sdk_root?, config?, build_dir?, install_dir?, deploy?)` | **Server-side** subprocess `cmake.exe --build` + auto-install. Defaults to the user's standard SDK path and `_build_v143` directory. |

### Tier 4 — Viewport / render engine (4 tools)

For verifying viewport draws and validating that custom render plugins (e.g. SplatFlow's planned GLSL splat viewport renderer) registered correctly.

| Tool | Purpose |
|------|---------|
| `viewport_screenshot(width?, height?, renderer?, frame?)` | PNG (base64) capture via the render pipeline. `renderer` ∈ {`standard` (fast software, default), `hardware` (OpenGL preview), `current` (user's active renderer — Octane/Redshift/etc)}. |
| `get_viewport_state()` | Active viewport: dims, frame rect, active camera + matrix, projection mode, active renderer. |
| `list_render_engines()` | All registered VideoPost render engines + which one is active. Critical for "did my custom viewport renderer register?". |
| `get_active_renderer()` | Current document's renderer id, name, resolution. |

### Tier 5 — Octane workflow (6 tools)

The Luminary endgame: link a painted PNG → Octane Area Light's emission texture without hardcoding any Octane plugin IDs.

| Tool | Purpose |
|------|---------|
| `tap_octane_log(lines?, contains?, level?, log_path?)` | **Server-side** read of `%APPDATA%/Maxon/Maxon Cinema 4D <VER>_<HASH>/c4doctanelog.txt`. Auto-discovers via glob, prefers newest C4D version. Filter by level (`Info`/`Warning`/`Error`) or substring. |
| `find_command_by_name(name_contains)` | Find CallCommand-able command IDs by name. Use to discover IDs like `1033864` (Octane Area Light). |
| `enumerate_octane_plugins(name_contains?, plugin_type?)` | Two-pass scan for plugins matching `octane`/`oct` in name. Group by type. |
| `dump_material_graph(material_name?, object_name?, guid?, max_depth?)` | Walk a material's (or object's, or tag's) shader tree. Returns `{name, type_id, params, children}` per node. Works for Octane node graphs, Redshift, etc. |
| `create_via_command(command_id, object_name?)` | Wraps `c4d.CallCommand(id)` and returns the new active object. Use for plugin objects that can't be `BaseObject.Alloc()`-ed (Octane lights/cameras). |
| `link_shader_to_parameter(target, parameter_path or parameter_name, shader_plugin_id, shader_params?)` | **Killer tool.** Creates a shader, configures it, attaches it to an object/material/light, and links it as the value of a specific parameter — all in one call. Used for the Luminary paint-bitmap → Octane Area Light texture pipeline once the IDs are known. |

---

## Setup

Same as upstream README, with the following additions:

### Step 1 — Install `uv` (recommended) or pip-install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # install uv
cd /path/to/cinema4d-mcp
uv sync                                            # install deps from uv.lock
```

Alternative without uv:
```bash
pip install -e /path/to/cinema4d-mcp
```

### Step 2 — Install C4D-side socket plugin

Copy `c4d_plugin/mcp_server_plugin.pyp` to:
`%APPDATA%/Maxon/Maxon Cinema 4D <VER>_<HASH>/plugins/`

Restart C4D, then `Extensions → Socket Server Plugin → Start Server`.

### Step 3 — Wire into Claude Code (project-scoped) or Claude Desktop

**Claude Code project-scoped** (`.mcp.json` in your project root):
```json
{
  "mcpServers": {
    "cinema4d": {
      "command": "uv",
      "args": ["--directory", "/path/to/cinema4d-mcp", "run", "cinema4d-mcp"],
      "env": { "C4D_HOST": "127.0.0.1", "C4D_PORT": "5555" }
    }
  }
}
```

**Claude Code user-scoped** (so it works in any project):
```bash
claude mcp add cinema4d --scope user -- uv --directory /path/to/cinema4d-mcp run cinema4d-mcp
```

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "cinema4d": {
      "command": "uv",
      "args": ["--directory", "/path/to/cinema4d-mcp", "run", "cinema4d-mcp"]
    }
  }
}
```

---

## Canonical workflows

### Crack an undocumented Octane parameter ID

```
1. find_command_by_name("area light")     → discover Octane Area Light command id (e.g. 1033864)
2. create_via_command(1033864)            → instantiate one
3. enumerate_descids("Octane Area Light", name_filter="texture")
                                          → see every texture-related param with its DescID path
4. enumerate_octane_plugins(plugin_type="shader", name_contains="image")
                                          → find Octane Image Texture shader plugin id
5. link_shader_to_parameter(
       target_name="Octane Area Light",
       parameter_path=[<discovered>],
       shader_plugin_id=<discovered>,
       shader_params={"file": "/path/to/canvas.png"}
   )                                      → live wire-up, no hardcoded IDs ever
```

### Verify a custom plugin registered

```
1. list_render_engines()                    → confirm your VideoPost shows up + isn't active
2. list_installed_plugins(plugin_type="object", name_contains="luminary")
                                            → confirm ToolData/ObjectData registration
3. get_console_log(contains="luminary", limit=50)
                                            → see init logs, registration messages, errors
```

### Edit → build → install loop

```
1. (edit code in your editor)
2. build_and_install_plugin(target="Luminary")   → cmake build + auto-install
3. (manually restart C4D — no hot reload for compiled plugins)
4. get_console_log(contains="luminary")          → check load
5. (repeat)
```

### Diagnose Octane-related issues

```
1. tap_octane_log(level="Error", lines=50)       → recent Octane errors
2. tap_octane_log(contains="Material", lines=100) → material-related events
3. get_console_log(source="c4d.GePrint")         → C4D-side messages (often Octane error reflections)
```

---

## Implementation notes

- All new plugin-side handlers live as methods on `C4DSocketServer` between `handle_apply_shader` and `class SocketServerDialog`. Helpers prefixed `_` and module-level `luminary_*`.
- The `c4d.GePrint` runtime hook is installed in `SocketServerDialog.StartServer()`. It's idempotent and stays installed across server stop/start so the buffer keeps capturing.
- `install_plugin` and `build_and_install_plugin` are pure server-side (subprocess + shutil) — they do NOT require a live C4D socket connection. Useful for build-only workflows.
- `_native_to_wsl` / `_wsl_to_native` path helpers transparently handle WSL ↔ Windows path translation.
- Read-only tools (`enumerate_*`, `find_*`, `get_*`, `dump_*`, `list_*`) run on the worker thread for speed (mirroring upstream's `handle_get_scene_info` pattern). State-mutating tools (`create_via_command`, `link_shader_to_parameter`, `viewport_screenshot`) use `execute_on_main_thread()`.
