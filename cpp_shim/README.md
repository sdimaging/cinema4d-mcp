# cinema4d-mcp C++ Helper Plugin

**Status (2026-04-30):** Phase A.0 ✅ **PROVEN LIVE** — built, installed, loaded, discoverable from Python. `c4d.plugins.FindPlugin(1057845, c4d.PLUGINTYPE_COREMESSAGE)` returns a non-None plugin instance with name `"cinema4d-mcp helper"`. Bridge is real. Subsequent phases wrap `GraphModelInterface::AddPort` (Phase A.1) and NodeTemplate publishing (Phase B). Full design at [`docs/cpp_shim_design.md`](../docs/cpp_shim_design.md).

## Why this exists

The Python `maxon.frameworks.{nodes,graph,asset}` modules don't expose two primitives needed for full Scene Nodes capsule authoring:
1. `GraphModelInterface::AddPort(parent, Id name)` — singular, named (the API the C4D editor calls on drag-wire). Python only wraps the plural count-based form, which requires a `VARIADIC_TEMPLATE` flag the FIO/PORTLIST ports don't satisfy.
2. NodeTemplate-typed asset publishing — `.c4dnodes` format, what Edge to Spline / Random Selection ship as. Python's `AssetCreationInterface` exposes 32 methods but none produce NodeTemplate.

Both are accessible in C++. This plugin is the bridge.

## Architecture

```
cinema4d-mcp Python plugin (.pyp)  ──FindPlugin + Message──>  this .cdl64
        ▲                                                          │
        │ socket (existing)                                        │
        │                                                          ▼
   Claude / agent                                          Maxon C++ APIs
                                                        (AddPort, NodeTemplate)
```

Sibling plugin pattern: ships alongside `mcp_server_plugin.pyp` in the same `plugins/cinema4d-mcp/` install dir, no fork of the Python plugin needed.

## Plugin ID

`1057845` — sibling to existing user IDs (1057843 for the Python MCP, 1057844 for the UiActionObserver). If conflict surfaces with another vendor, register a fresh ID at https://developers.maxon.net/forum/pid before public ship.

## Source layout

```
cpp_shim/
├── README.md                                    (this file)
└── cinema4d_mcp_helper/
    ├── project/projectdefinition.txt            Maxon ProjectTool build config
    ├── res/c4d_symbols.h                        empty (no UI/description in Phase A.0)
    └── source/main.cpp                          everything for Phase A.0
```

## Building (verified working 2026-04-30)

End-to-end verified path:

```bash
# 1. Sync source into the SDK plugins tree
./scripts/build_cpp_shim.sh sync

# 2. Build via cmake (Visual Studio backend)
cd "/mnt/c/Users/Spenser Dickerson/Documents/C4D_2026_SDK/_build_v143"
cmake --build . --config Release --target cinema4d_mcp_helper
# (or open the generated VS solution and build manually)

# 3. Install — finds the .xdl64 (yes, .xdl64 on Windows; gotcha #27)
#    and copies it to %APPDATA%/Maxon/Maxon Cinema 4D 2026_<HASH>/plugins/cinema4d-mcp/
./scripts/build_cpp_shim.sh install

# 4. FULLY RESTART Cinema 4D (Reload Python Plugins is not enough)

# 5. Verify
#    From Python: c4d.plugins.FindPlugin(1057845, c4d.PLUGINTYPE_COREMESSAGE)
#    Or via MCP:  scene_nodes_helper_ping
```

The actual build output path on Windows was:
`<SDK>/_build_v143/bin/Release/plugins/cinema4d_mcp_helper/cinema4d_mcp_helper.xdl64`

Note `.xdl64` not `.cdl64` — this is correct for the C4D 2026 Windows SDK
(see `docs/c4d_2026_api_gotchas.md` #27). The install script handles both
extensions automatically.

## Phase A.0 contract — what to verify after install

- C4D 2026 starts with the new plugin loaded (no crash, no error in Console)
- Python's `c4d.plugins.FindPlugin(1057845, c4d.PLUGINTYPE_MESSAGEDATA)` returns a non-None plugin instance
- The plugin's name is reported as `"cinema4d-mcp helper"`

If those three pass, the bridge is real and Phase A.1 (`AddPort` wrapper) can land.

## Phase A.0 known limitation

`MessageData::CoreMessage` signature is `(Int32 id, const BaseContainer& bc)` — the BaseContainer is const. Round-tripping a result back to Python via the BC isn't straightforward through this entry point. Phase A.1 will either (a) switch to `NodeData`/`CommandData` plugin type with a non-const `Message()` overload, or (b) use a global state slot + a follow-up read. Decide after confirming the plugin loads at all.
