# Migrating a C++ plugin from the C4D 2026.2 SDK to the 2026.3 SDK

Field-validated runbook + gotchas from migrating a real generator + scene-hook +
spline + particle-reading plugin. **Result: zero API breaks** — for typical Cinema
API usage (ObjectData, SceneHook, SplineObject, VertexColorTag, ParticleGroupObject,
`maxon::ParallelFor`), the 2026.3 cinema.framework changes are additive. The migration
is mechanical; the friction is entirely in the new **build system**, not the code.

> Context: a plugin compiled against the 2026.2 SDK still **loads** on a 2026.3.x app
> (ABI is stable within the 2026 major version — the official change notes confirm
> "existing compiled plugins remain unaffected"). So there's no fire; migrate
> deliberately and keep the old build as a fallback until the new one is verified.

## What changed in the 2026.3 SDK build system

- **Pure CMake now.** There's a top-level `CMakeLists.txt` + `CMakePresets.json` at the
  C++ SDK root and a `cmake/` tooling folder (`sdk_update_projects.cmake`,
  `sdk_targets.cmake`). No standalone "project tool" exe.
- **Plugins are auto-discovered** by globbing `plugins/*/` — a directory is treated as a
  module when its `projectdefinition.txt` lives in a **`<plugin>/project/` subfolder**
  (the root of the plugin folder must NOT contain `projectdefinition.txt`).
- **`custom_paths.txt`** at the SDK root can list module paths to reference plugins
  *in place* instead of copying them into `plugins/` — useful for a multi-plugin fleet.

## New `projectdefinition.txt` format

```
// Platforms - [windows;windows-arm64;windows-x64;macos;linux;linux-arm64;linux-x64;ios]
Platform=windows;macos;linux

Type=DLL

// Every framework whose headers you #include must be listed here.
APIS=\
cinema.framework;\
cinema_hybrid.framework;\
core.framework;\
math.framework;\
misc.framework;\
mesh_misc.framework

C4D=true

stylecheck.level=0   // MUST come after C4D=true

ModuleId=net.mycompany.myplugin
```

Differences from 2026.2: platform tokens are **lowercase** (`windows` not `Win64`) and
ARM64 targets are now available; `stylecheck.level` must appear **after** `C4D=true`.

## Runbook

1. Stage the plugin into the new SDK:
   `mkdir -p <SDK>/plugins/<NAME>/{source,res,project}`, copy your `source/*.cpp *.h`
   (skip any `*.bak`) and the `res/` tree, and write the `project/projectdefinition.txt`
   above.
2. **Configure** from the C++ SDK root (see the Windows-SDK gotcha first):
   `cmake --preset <your_preset>`
3. **Build:** `cmake --build <binaryDir> --config Release --target <NAME>`
   → output lands in `<binaryDir>/bin/Release/plugins/<NAME>/<NAME>.xdl64`.
4. Deploy (Cinema 4D closed): copy the `.xdl64` + `res/` to the install plugins dir;
   launch and smoke-test on the matching app version.

Re-run the configure step whenever you add a new plugin folder so the `plugins/*` glob
picks it up.

## Gotchas (the actual time-sinks)

### 1. Stock presets pin a Windows SDK version you may not have
`CMakePresets.json` ships presets like `windows_vs2022_v143_x64` with
`"architecture": "x64,version=10.0.20348.0"` (and `...22621...` for ARM64). If that
exact Windows SDK isn't installed, configure dies with *"no Windows SDK with that
version was found."*

**Fix** — add a machine-local `CMakeUserPresets.json` at the C++ SDK root that inherits
the stock preset and overrides the architecture to *your* installed Windows SDK:

```json
{
  "version": 6,
  "configurePresets": [
    {
      "name": "local_x64_v143",
      "inherits": "windows_vs2022_v143_x64",
      "architecture": "x64,version=10.0.26100.0"
    }
  ]
}
```

(Find installed versions under `C:\Program Files (x86)\Windows Kits\10\Include\`.)
`CMakeUserPresets.json` is the conventional user-local, git-ignored override — don't
edit the stock `CMakePresets.json`.

### 2. A failed configure leaves a poisoned cache
If the first configure fails (e.g. the gotcha above) it still writes a
`CMakeCache.txt` pinned to the bad platform, and the next attempt errors with *"does
not match the platform used previously."* **`rm -rf <binaryDir>`** before re-configuring.

### 3. List every framework you include
The `APIS=` list gates which framework headers you're allowed to include. A missing
entry surfaces as a header-not-found at compile, not at configure. Start from your old
2026.2 `APIS` list — it carries over unchanged for typical plugins.

## Verdict

For a plugin using mainstream Cinema API surfaces, expect a **clean first compile**
against 2026.3 (we hit only a pre-existing benign `C4244` narrowing warning). Budget
your time for the build-system setup (presets + Windows SDK), not for source fixes.
