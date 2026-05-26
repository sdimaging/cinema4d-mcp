# C4D 2026 API Gotchas — discoveries from MCP development

A reference sheet of things the C4D 2026 Python API does that don't match
older docs / tribal knowledge / what felt right. Each entry has the wrong
assumption, the actual behavior, and how it was discovered.

Maintained as bugs surface during MCP plugin development. Useful for
anyone building agent integrations against C4D 2026.

---

## 103. `c4d.Osweep` child order is load-bearing — profile FIRST, path SECOND

**Discovered 2026-05-26** building a procedural PCB-trace generator (CircuitBoardTool) and emitting Sweep generators from `execute_python_script`.

**Wrong assumption:** `c4d.Osweep` is forgiving about which child is the profile vs. which is the path — C4D will figure it out from the children's types (spline-primitive shapes vs. linear SplineObjects).

**Actual behavior:** Sweep walks its children **strictly in order**. The first child is the profile (the cross-section), the second is the path (the spline being swept along). Reverse the order and Sweep silently produces zero geometry — no error, no warning, no console message. The Sweep node appears in the Object Manager but renders nothing in the viewport.

Bit me when adding traces to a chip-routing pipeline: I created the path spline first (because that's the order I computed them in), then the rectangle profile. The Sweep showed up empty until I realized the insertion order was wrong.

**Fix:** insert profile FIRST, then path, into the Sweep:

```python
sweep = c4d.BaseObject(c4d.Osweep)
rect = c4d.BaseObject(c4d.Osplinerectangle)
rect[c4d.PRIM_RECTANGLE_WIDTH]  = 1.2
rect[c4d.PRIM_RECTANGLE_HEIGHT] = 0.5
path = c4d.SplineObject(N, c4d.SPLINETYPE_LINEAR)
for i, (x, y, z) in enumerate(points):
    path.SetPoint(i, c4d.Vector(x, y, z))
path.Message(c4d.MSG_UPDATE)

# ORDER MATTERS: profile first, path second
rect.InsertUnder(sweep)
path.InsertUnder(sweep)
path.InsertAfter(rect)   # belt-and-braces — guarantees path is below rect
```

The `InsertAfter(rect)` line is the defensive write — without it, `path.InsertUnder(sweep)` puts `path` at the top of sweep's child list (above `rect`), and you're back to silent-empty-Sweep land. With `InsertAfter`, the order is enforced regardless of Insert semantics.

**Detection:** if a Sweep generator shows up in the OM with the expected name but the viewport shows nothing where the swept geometry should be, check `sweep.GetDown().GetType()` — if it's `c4d.Ospline` (or your SplineObject type) instead of `c4d.Osplinerectangle` (or whatever your profile is), order is reversed.

---

## 102. Iterating on a disk Python file from MCP requires `del sys.modules[...]` before re-import

**Discovered 2026-05-26** during a long iteration loop editing a `CircuitBoardGenerator.py` file on disk and re-running it via `execute_python_script` after each edit.

**Wrong assumption:** Each `execute_python_script` call gets a fresh interpreter scope, so `import MyModule` at the top of every script picks up whatever's on disk now.

**Actual behavior:** `execute_python_script` calls all share **one Python interpreter** — C4D's bundled one. Once `import MyModule` runs, `MyModule` is cached in `sys.modules`. Subsequent `import MyModule` calls in later MCP scripts return the *cached* module object, NOT a fresh read of the edited disk file. Edits silently don't apply. You'll see the old behavior for hours and chase phantom bugs that don't exist in the on-disk code.

This is normal Python module-caching behavior, but the failure mode is invisible: there's no warning that you're running stale code. The script "succeeds" against an obsolete version.

**Fix:** the canonical preamble for iterating on a disk-resident `.py` file via MCP:

```python
import sys
src = r"C:\path\to\your\module\folder"
if src not in sys.path:
    sys.path.insert(0, src)
if "MyModule" in sys.modules:
    del sys.modules["MyModule"]
import MyModule
MyModule.main()
```

The `del sys.modules["MyModule"]` line is the only thing that forces a true re-read of the disk file. `importlib.reload()` also works but only after the module has been imported once — `del + re-import` is safer because it doesn't care about prior state.

For multi-module packages, walk the modules dict and drop everything matching a prefix:

```python
for name in [n for n in sys.modules if n.startswith("MyPackage")]:
    del sys.modules[name]
import MyPackage   # reloads the whole tree
```

**Related:**
- Gotcha #98 covers a different but adjacent case: re-stuffing `OPYTHON_CODE` for Python Generators specifically. That's needed because the generator's *body* doesn't re-execute on rebuild — distinct from the `sys.modules` caching issue which affects any `import` regardless of caller.

**When this bites hardest:** long debugging sessions iterating on a single algorithm file. You "fix" something, re-run, see the broken behavior, "fix" it differently, re-run, see broken behavior again — never realizing none of your edits ran. Adding the `del sys.modules` preamble to every iteration script turns this from a 30-minute confusion into a non-event.

---

## 101. `execute_python_script` heavy compute: bound work per call with explicit budgets, not naive loops

**Discovered 2026-05-26** scaling a Dijkstra-based PCB-trace router from 80 routes per board to 200+ and hitting `Execution on main thread timed out after 30s`.

**Wrong assumption:** if a Python loop is doing legitimate work (no deadlock, no infinite recursion, just expensive computation), the MCP 30s timeout is mostly informational — the script will finish, and worst case you wait.

**Actual behavior:** the timeout is **hard**. After 30s the MCP returns an error and the in-flight C4D main-thread work is abandoned mid-state. Anything the script had already mutated (objects inserted into the doc, parameters set, materials created) stays partially applied — leaving a corrupt half-built scene that requires `clear_previous()` or a fresh doc. The script doesn't "finish in the background" — it stops.

This is a different failure mode from gotcha #97 (main thread already busy, script never started). Here the script DOES start, runs for a while, then gets cut off mid-execution.

**Fix:** for any compute-heavy work submitted via `execute_python_script`, **bound the work per call** with explicit budgets so the loop bails before the timer expires. Three patterns that worked:

1. **Visit budget on graph searches** (the one that solved CircuitBoardTool):

   ```python
   def dijkstra(start, goals, ..., max_visits=4000):
       visits = 0
       while pq:
           ...
           visits += 1
           if visits > max_visits:
               return None    # bail — caller decides whether to retry
           ...
   ```

   Failed/hopeless searches cost the cap (4000 pq pops) instead of exhausting the grid. A multi-thousand-route fill pass that used to time out at ~80 routes completes cleanly at 200+ because no single search blows the budget.

2. **Iteration cap on retry loops:**

   ```python
   placed = 0; tries = 0
   while placed < target and tries < target * 12:   # cap retries
       tries += 1
       ...
   ```

   The `target * 12` cap means if scatter-placement is starving (too many rejections), the loop exits and the caller decides whether to relax constraints — instead of spinning forever.

3. **Per-call yield to C4D event loop:** for genuinely-long pipelines (heavy MoGraph rebuilds, voxel ops), split work into multiple `execute_python_script` calls and let MCP serialize them. State that needs to persist across calls goes in `sys.modules` (a module-level dict survives between calls; module-local globals do too).

**Diagnostics when you hit this:** the script's print output up to the timeout point is gone (no stdout flush before the abort). The fastest way to find where it died is to add cheap progress prints (`if k % 100 == 0: print(k)`) before scaling — if you see "100, 200, 300, ..." stop at 800 with no completion message, you know each iteration costs about (30s / 800) = 38ms and your budget per iteration needs to drop or your iteration count needs to cap.

**Related:** gotcha #97 covers the *queued-behind-busy-main-thread* case (script never starts). This covers the *script-runs-but-doesn't-finish-in-30s* case. Both surface the same error message but have opposite causes and fixes.

---

## 100. `CallCommand(11605)` "Reload Python Plugins" crashes C4D when registering a NEW .pyp

**Discovered 2026-05-26** deploying a new `.pyp` plugin and calling reload from MCP to avoid a C4D restart.

**Wrong assumption:** `CallCommand(11605)` (Reload Python Plugins) re-scans the plugins folder and loads any newly-added .pyp files cleanly, equivalent to a fresh C4D startup.

**Actual behavior:** reload works for EDITING an already-loaded .pyp, but for registering a NEW .pyp (one that wasn't loaded at startup), reload triggers an interpreter teardown-and-reinit cycle that races against worker threads holding the GIL. Crash signature:

```
ExceptionText = "ACCESS_VIOLATION"
Address       = python311.dll → PyThread_tss_create
Call stack    = PyGILState_Ensure during c4d_base teardown
```

The crash happens DURING the reload command, before the new plugin's `RegisterCommandPlugin` even runs. The .pyp itself is not the bug — it's the reload mechanism.

**Fix:** for NEW .pyp registration, always do a full C4D restart. Reload-only path is safe for code edits to plugins that were already loaded once at startup.

**Detection:** if `CallCommand(11605)` returns but C4D becomes unresponsive (MCP `ping` times out, console doesn't respond), inspect `%APPDATA%/Maxon/Maxon Cinema 4D <ver>_<hash>/_bugreports/_BugReport.zip` — newest one in `_bugreports/` is the crash trace.

Related: gotcha #98 (OPYTHON_CODE re-stuff for Python Generator module reload) addresses the per-generator case; this entry covers the per-plugin case.

---

## 99. WSL → MSVC build interop pattern (no manual Dev Prompt needed)

**Discovered 2026-05-25** building a C++ kernel DLL for the SWEAT plugin from WSL.

**Wrong assumption:** to build a Windows DLL with MSVC, the developer must manually open the "x64 Native Tools Command Prompt for VS" and run the build there.

**Actual behavior:** WSL has full Windows-binary interop. `cmd.exe`, `powershell.exe`, and any Windows executable can be invoked from a WSL bash session via absolute paths. With one batch-file wrapper that activates the MSVC environment via `vcvars64.bat`, you can drive the whole Windows build from WSL — no manual prompt, no per-build user action.

**The pattern:**

```bash
# 1. Find the VS installation (also works on machines with full VS,
#    not just BuildTools — vswhere is shipped with the VS installer)
"/mnt/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe" \
    -latest -property installationPath

# 2. Wrapper batch (build_msvc.bat in the project) calls vcvars64
#    then the actual build.bat. Critical: use the 8.3 short-name
#    PROGRA~2 for "Program Files (x86)" — the parens in the long
#    form break cmd.exe batch parsing inside if/then blocks.
cat > build_msvc.bat <<'EOF'
@echo off
setlocal
set "VCVARS=C:\PROGRA~2\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
call "%VCVARS%" >nul
cd /d "%~dp0"
call build.bat %*
endlocal
EOF

# 3. Run from WSL
cmd.exe /c build_msvc.bat
```

The `(x86)` parens are the only real footgun. Without `PROGRA~2`, you get cryptic `\Microsoft was unexpected at this time.` errors that look like batch syntax bugs but are actually cmd parsing the parens as expression grouping.

**Why it matters:** lets a dev agent drive Windows MSVC builds from WSL/Linux without making the human switch to a Dev Prompt for every iteration. Build → test → fix → rebuild loop stays in one terminal.

---

## 98. Python Generator `import` caches modules; reload pattern needs re-stuffing `OPYTHON_CODE`

**Discovered 2026-05-24** during SWEAT V1.5 dev iteration.

**Wrong assumption:** at the top of `GENERATOR_CODE`, `del sys.modules[k]` + `importlib.invalidate_caches()` is enough to force the Python Generator to re-import your edited algorithm modules on next rebuild.

**Actual behavior:** the Python Generator runs its top-level body ONCE at script-load. Subsequent rebuilds call `main()` but DON'T re-execute the body — so the `del sys.modules` block only runs the first time. Edits to imported modules don't pick up until the generator's body re-parses.

**Fix:** to force a true reimport, re-stuff the code:

```python
code = op[c4d.OPYTHON_CODE]
op[c4d.OPYTHON_CODE] = ""      # invalidate
op[c4d.OPYTHON_CODE] = code    # forces re-parse + body re-execution
```

Setting OPYTHON_CODE to itself doesn't trigger a re-parse; you have to set it to something else first. The blank-then-restore pattern works reliably.

For full algorithmic isolation, also drop sys.modules from outside the generator before triggering rebuild:

```python
for k in list(sys.modules.keys()):
    if k.startswith("MYPLUGIN"):
        del sys.modules[k]
import importlib; importlib.invalidate_caches()
op[c4d.OPYTHON_CODE] = ""; op[c4d.OPYTHON_CODE] = code
op.SetDirty(c4d.DIRTYFLAGS_DATA)
```

This is the only reliable dev-loop for Python Generator algorithm work.

---

## 97. C4D MCP `execute_python` timeout on busy main thread is NOT a script bug

**Discovered 2026-05-25** testing a C++ DLL load from C4D Python via MCP.

**Wrong assumption:** an `execute_python` command that hangs for 30 seconds and times out with "Main thread execution timed out" means the script itself deadlocked or hit an error in the loaded code.

**Actual behavior:** the MCP plugin queues all `execute_python` calls onto C4D's main thread. If the main thread is busy (Octane render starting up, a viewport rebuild in progress, a long-running script, a modal dialog), MCP commands wait in queue. After 30s the wait times out — but the script never ran. No error in the script, no DLL fault. C4D is just busy.

**How to diagnose:** call `get_console_log` after the timeout. If you see:

```
[plugin] [C4D] Waiting for main thread execution (29.0s elapsed)
[plugin] [C4D] Main thread execution timed out after 30.00s
```

— the script never executed. The DLL/script under test is fine.

**Workaround:** wait for C4D to finish whatever it's doing (close dialogs, let renders finish), or paste the script into C4D's Script Manager → Python Console directly (bypasses the MCP queue, runs synchronously in the foreground thread).

The MCP itself is healthy — restart Stop→Start the socket if the queue gets fully wedged. Sometimes a hung script earlier in the session leaves the queue in a bad state and a socket restart clears it.

---

## 96. Python `int(x)` and C++ `std::floor(x)` are NOT equivalent for negative values

**Discovered 2026-05-25** porting `TriSpatialHash` from Python to C++ and getting 100% query misses at negative coordinates.

**Wrong assumption:** when porting a Python hash function that computes integer cell keys via `int(value * inv)` to C++, `std::floor()` is the equivalent.

**Actual behavior:** `int()` truncates toward zero. `std::floor()` rounds toward negative infinity. They diverge for negative values:

| `x` | Python `int(x)` | C++ `std::floor(x)` |
|---|---|---|
| 0.5 | 0 | 0 |
| -0.5 | **0** | **-1** |
| -1.5 | **-1** | **-2** |

In a spatial hash, this means triangles with centroid x = -0.5 go to cell 0 in Python and cell -1 in C++. Query at the same negative position misses 100% of the time.

**Fix in C++:** `static_cast<int>(value)` truncates toward zero, matching Python:

```cpp
const int kx = static_cast<int>(p.x * inv);   // matches Python int()
// NOT: static_cast<int>(std::floor(p.x * inv));  // matches Python math.floor()
```

**Test pattern that catches this:** any C++ port of a coordinate-keyed Python function needs a fuzz test that queries across the signed range (e.g. uniform random `[-20, +20]`), not just positive coordinates. The bug is invisible to tests that only exercise positive space.

The Python `int()` truncation behavior is technically buggy (asymmetric cell-grid centered on origin), but for parity ports it's the reference. Document the asymmetry and plan a consistent fix later (would be its own kernel ABI bump).

---

## 95. ctypes + C ABI is the right Python ↔ C++ bridge for C4D-hosted plugins (NOT pybind11)

**Discovered 2026-05-25** scoping a hot-kernel C++ port for SWEAT.

**Wrong assumption:** pybind11 is the standard modern way to bridge Python and C++; use it for C4D plugins that want to call native code.

**Actual behavior:** pybind11 links against `Python.h` and depends on the Python ABI matching the host's Python interpreter. C4D 2026 bundles its own Python (3.11.x as of testing). If your pybind11 module was built against a different patch version, or with a different `_DEBUG` macro setting, or with a different MSVC runtime mode (`/MD` vs `/MT`), it either won't load or will load and crash on first call. C4D version bumps to a new Python micro-version can break previously-working pybind11 plugins.

**Better default for C4D-hosted Python ↔ C++ bridges:** plain `extern "C"` functions with C-ABI types (`float*`, `int*`, sizes, opaque `void*` handles) loaded via `ctypes.CDLL`. The DLL has zero Python dependency — same `.dll` loadable from Windows Python 3.11, 3.12, even non-C4D hosts (Houdini, Blender, standalone tools).

**Build flags that matter for C4D-loaded DLLs:**

| Flag | What it does | Use this |
|---|---|---|
| `/MD` | Link the dynamic MSVC CRT | **YES** — matches C4D's runtime |
| `/MT` | Statically embed the CRT | NO — runtime mismatch crashes |
| `/EHsc` | Standard C++ exception model | YES — C++ stdlib needs it |
| `/LD` | Build a DLL | YES |
| `/std:c++17` | Pin language standard | YES — reproducible builds |

**Cost:** Python wrapper has to marshal data manually via `ctypes.c_float`, `(c_float * N)()` buffers, etc. ~10-15 lines of wrapper per kernel. Worth it for zero-version-pin portability.

**Pattern for handle-based APIs** (e.g. spatial hash with internal state):

```cpp
// C++ side
SWEAT_API void* sweat_make_hash(...) { return new MyHash(...); }
SWEAT_API void sweat_destroy_hash(void* h) { delete (MyHash*)h; }
SWEAT_API int sweat_query_hash(void* h, ...) { return ((MyHash*)h)->query(...); }
```

```python
# Python side — wrap in a class with __del__ for cleanup
class MyHash:
    def __init__(self, ...):
        self._handle = _lib.sweat_make_hash(...)
    def __del__(self):
        if self._handle: _lib.sweat_destroy_hash(self._handle)
```

ctypes treats opaque pointers as `c_void_p`; bind that as the argument/return type and you get full handle-based lifecycle without ABI churn.

---

## 94. Volume Builder/Mesher don't evaluate inside another generator's cache output

**Discovered 2026-05-24** building a streak meshing pipeline inside SWEAT's `GetVirtualObjects`.

**Wrong assumption:** if you construct a `c4d.Ovolumebuilder` + `c4d.Ovolumemesher` chain and return it as part of your generator's cache hierarchy, C4D will evaluate it and the Volume Mesher will produce meshed polygons just like it does at scene level.

**Actual behavior:** Volume Builder and Volume Mesher only run their internal generation pass when they live in the document. Inside another generator's `GetVirtualObjects` return value, C4D treats them as inert cached display geometry — they exist in the hierarchy but `vm.GetCache().GetPointCount()` returns **0**.

**Empirical proof:**

| Setup | Volume Mesher.GetCache().GetPointCount() |
|---|---|
| 2 spheres at scene level → Volume Builder → Volume Mesher (in doc) | 13,134 ✓ |
| Raw PolygonObject at scene level → same chain (in doc) | 3,906 ✓ |
| Same chain inside a Python Generator's returned cache | **0** ✗ |

This is a C4D evaluation architecture decision (`ExecutePasses` only re-evaluates generators at one level), not a Python binding issue. **Same limitation applies to C++ ObjectData generators.**

**Workaround (Python prototype):** the parent generator creates+maintains a scene-level Volume Mesher chain in the document (not in its cache). Hide via a layer with `manager=False, view=True, render=True` so OM stays clean but eval+render stay on. Update the chain's source PolygonObject in place on each rebuild. Idempotent via stable naming (use a persistent UUID stored on the parent op, not its display name — names collide on rename/duplicate).

**Escape hatch (C++ port):** call `maxon::VolumeInterface` or OpenVDB directly via the Maxon SDK and produce raw polygons (marching cubes) inside the generator's `GetVirtualObjects`. No Volume Builder/Mesher plugin objects needed. Fastest, no scene clutter. Python doesn't have access to these low-level APIs.

**Key debug habit:** verifying the cache HIERARCHY (`cache.GetDown()` walks) is NOT the same as verifying the Volume Mesher produced output. Always check `vm.GetCache().GetPointCount()` to confirm meshed geometry. The hierarchy can be correct while the output is empty.

**LayerObject API note:** `LayerObject.GetLayerData(doc, rawdata=True)` returns a **dict with string keys** (`'manager'`, `'view'`, `'render'`, `'solo'`, `'locked'`, ...), **NOT** a `BaseContainer` with `c4d.ID_LAYER_*` int constants. Setting numeric keys via `bc[c4d.ID_LAYER_MANAGER] = False` silently writes a bogus dict entry that has no effect. Use string keys directly.

---

## 93. MoGraph Python Effector `md.SetArray(MODATA_COLOR/SIZE)` does NOT propagate in C4D 2026

**Discovered 2026-05-17** wiring per-clone state-driven coloring for a MoGraph prototype.

**Symptom:** A Python Effector calls
`mo.GeGetMoData(op).SetArray(c4d.MODATA_COLOR, carr, True)` per the
standard pattern that has worked since R20. The call returns without
error, the effector is wired into the cloner, `COLOR_MODE = 1 (Custom
Color)`, `EditorMode = MODE_ON`, the material has a MoGraph Color Shader
in the color channel — and clones render uniform with no per-clone
variation. Same effect with `MODATA_SIZE`.

**Verification it's not the surrounding pipeline:** Drop the Python
Effector, replace with a Plain Effector (same `COLOR_MODE = 1`, same
strength, color set to red on the effector). Clones go red. So the
shader + cloner + material chain is correct; only Python's SetArray
writes are silently dropped.

**Workarounds:**

1. **World-projected bitmap texture.** Most flexible. Create a small
   bitmap (1 pixel per clone in a grid), assign as the material's color
   texture with cubic/flat projection in world space scaled to cover
   the grid extents. Each clone naturally samples its own pixel.
   Update state by repainting pixels, no MoGraph color pipeline
   involved.

2. **Multiple Plain Effectors with selection groups.** One effector per
   state, each restricted to its own MoGraph Selection. Heavy if you
   have many states.

3. **MoGraph Multi Shader (`Xmgmulti`)** with sub-shader per state. Still
   needs a per-clone index source — another Plain/Random effector or a
   carefully-driven MoGraph Selection.

The Python Effector still works for **rotation** writes via the matrix
array — that channel propagates fine. Only color/size are affected, as
far as I've verified.

**Symbol info:** `c4d.Xmgcolor = 1018767`. Its `Channel` parameter (id
1000) has only two valid values in 2026: `0 (Color)` and `8 (Index
Ratio)`. The shader description's cycle dropdown confirms this — the
older "Weight / Falloff / Random / etc." options are gone.

---

## 92. MoGraph Effector `Color Mode` (id 1014) — `1 (Custom Color)` is the working value, not `3 (Effector Color)`; default is `5 (Fields Color)`

Cycle dropdown values for the standard `ID_MG_BASEEFFECTOR_COLOR_MODE`
on Plain / Random / Python effectors:

```
0  Off
1  Custom Color
3  Effector Color
5  Fields Color   ← DEFAULT for newly-created effectors
```

Two non-obvious things:

1. **The default is 5 (Fields Color)**, not 0 or 3. Newly-instantiated
   effectors have NO visible color contribution until you set this. If
   you create an effector and its color won't show, this is almost
   certainly why.

2. **"Custom Color" (1) is what you want for an effector that applies
   its own COLOR param.** "Effector Color" (3) sounds like the right
   one but renders gray in testing — its actual semantic seems to be a
   blend mode that requires additional setup. Just use `1`.

`c4d.ID_MG_BASEEFFECTOR_COLORMODE_EFFECTOR` symbol is missing in 2026;
use the literal `1` (Custom Color) for the standard use case.

---

## 91. Cloner Mode integer values — `3` is Grid Array; `0` produces zero clones silently

`cloner[c4d.ID_MG_MOTIONGENERATOR_MODE]` in C4D 2026:

```
0  (invalid / produces 0 clones, NO warning)
1  Linear
2  Radial
3  Grid Array
4  Honeycomb
5+ (invalid)
```

If you build a Cloner with mode `0` (the natural Python default for a
freshly-created BaseObject in some workflows), no clones are produced
and `cloner.GetCache().GetDown()` is `None`. The Cloner just shows
nothing, no warning. Default mode for a Cloner created via the UI is
`3` (Grid).

Verify by counting cache children: `cache = cloner.GetCache(); n = 0;
ch = cache.GetDown(); while ch: n += 1; ch = ch.GetNext()` should match
your expected resolution.

Related: `mo.GeGetMoData(cloner)` called from outside an effector's
`main()` (e.g., from `execute_python_script`) returns `None` even after
`ExecutePasses` — MoData is only readable inside an effector context.
Use cache traversal or screenshots for outside-the-effector
verification.

Also: newly-created effector BaseObjects default to `EditorMode = 2
(MODE_OFF)`. The cloner silently skips disabled effectors. Always call
`SetEditorMode(c4d.MODE_ON)` + `SetRenderMode(c4d.MODE_ON)` after
instantiating.

---

## 90. `SplineHelp.GetPosition(offset)` takes a NORMALIZED [0, 1] offset, NOT arc-length cm

**Discovered 2026-05-11** while building a Python Generator that scatters bubbles along a spline source.

The intuitive read of the API is that `GetPosition(offset)` takes arc-length cm because `GetSplineLength()` also returns cm. So the natural code is:

```python
sp = c4d.utils.SplineHelp()
sp.InitSplineWith(spline, c4d.SPLINEHELPFLAGS_GLOBALSPACE)
length = sp.GetSplineLength()         # 2211 cm for a 552×552 rectangle
ofs = rng.uniform(0.0, length)         # e.g. 1105 (midway)
p = sp.GetPosition(ofs)                # expects (-276, -276, 0) for that midpoint
```

**Actual behavior:** `GetPosition` takes a normalized parameter `t ∈ [0, 1]`. Any value > 1.0 gets clamped to the start of the spline. All samples land at one point.

```python
sp.GetPosition(0.00) → Vector(276.4, 276.4, 0)    # start corner
sp.GetPosition(0.25) → Vector(-276.4, 276.4, 0)   # next corner
sp.GetPosition(0.50) → Vector(-276.4, -276.4, 0)  # opposite corner
sp.GetPosition(1.00) → Vector(276.4, 276.4, 0)    # back to start
sp.GetPosition(1105) → Vector(276.4, 276.4, 0)    # ← clamped to start (silent)
```

**Fix:** Always sample with `rng.uniform(0.0, 1.0)`. If you specifically need arc-length-based sampling (uniform spacing along path), use `sp.GetOffsetFromReal(real_cm)` to convert cm → normalized param first.

**Why this is non-obvious:** `GetSplineLength()` and `GetSegmentLength()` both return cm, and `Real`/`Length` SDK types usually map to absolute scene units. Only `GetPosition`/`GetTangent` use the normalized convention.

---

## 89. `c4d.FieldList` (top-level c4d) is the container; `FieldInput/FieldOutput/FieldLayer/FieldObject` live under `c4d.modules.mograph`

**Discovered 2026-05-11** while adding field-driven density gating to a scatter generator. The class split is non-obvious — agents often look for everything under `c4d.modules.mograph` because that's the historical home.

```python
# Container — top-level c4d
fl = c4d.FieldList()                       # ← top-level
print(c4d.modules.mograph.FieldList)       # ← AttributeError: module has no attribute 'FieldList'

# Sampling infrastructure — under c4d.modules.mograph
import c4d.modules.mograph as mograph
fi = mograph.FieldInput(positions, count)   # WORLD positions, count
out = fl.SampleListSimple(gen_op, fi)       # returns FieldOutput
values = [out.GetValue(i) for i in range(count)]   # [0..1] each
```

**Working pattern for a UD field-link param:**

```python
# Add UD
bc = c4d.GetCustomDataTypeDefault(c4d.CUSTOMDATATYPE_FIELDLIST)
bc[c4d.DESC_CUSTOMGUI] = c4d.CUSTOMGUI_FIELDLIST
host.AddUserData(bc)

# Read + sample
field_list = host[c4d.ID_USERDATA, IDX]   # type c4d.FieldList
if field_list and field_list.HasContent():
    fi = c4d.modules.mograph.FieldInput(positions, len(positions))
    out = field_list.SampleListSimple(host, fi)
    if out:
        values = [out.GetValue(i) for i in range(len(positions))]
```

**Empty FieldList:** `SampleListSimple` still returns a valid `FieldOutput` with `GetValue()` = 0.0 for every position. Check `field_list.HasContent()` first to distinguish "no field set" from "field present and gives zero here."

**Sampling space:** `FieldInput(positions, count)` expects WORLD positions. The field's own falloff is evaluated against its own world matrix — no manual transform needed.

---

## 88. Primitive spline generators do NOT pass `IsInstanceOf(c4d.Ospline)` — use `OBJECT_ISSPLINE` classification flag

**Discovered 2026-05-11** while detecting whether a user-linked source object is spline-like.

The intuitive detection logic is:

```python
if source.IsInstanceOf(c4d.Ospline) or source.GetType() == c4d.Ospline:
    # treat as spline
```

**This is wrong for primitive spline generators**, because they have their own type IDs:

| Object | Type ID | `IsInstanceOf(c4d.Ospline)` |
|---|---|---|
| `c4d.Ospline` (generic SplineObject) | 5101 | True |
| Rectangle spline | 5186 | **False** |
| Circle spline | 5181 | **False** |
| Helix spline | 5147 | **False** |
| Star spline | 5172 | **False** |
| Text spline | 5178 | **False** |
| LineObject (cache of any of the above) | 5137 | **False** |

The cache walk doesn't help either — `source.GetCache()` for a primitive spline returns a `LineObject` (5137), which **also** doesn't pass `IsInstanceOf(c4d.Ospline)`.

**Correct detection — `OBJECT_ISSPLINE` classification flag on `BaseObject.GetInfo()`:**

```python
def is_spline_like(obj):
    if obj is None: return False
    return bool(obj.GetInfo() & c4d.OBJECT_ISSPLINE)
```

This flag is set on **every** spline generator, primitive spline, MoSpline, Text spline, Sweep, etc. — anything carrying spline-shaped data.

**Bonus:** `SplineHelp.InitSplineWith(source, flags)` accepts spline generators directly. No need to extract the cache — SplineHelp handles it internally.

**Failure mode of the wrong detection:** code returns `None`, falls through to mesh-mode scatter, which then no-ops because the cache is a `LineObject` (no polygons). Generator silently produces empty output. Symptom: clicking a Rectangle source link does nothing. Time-to-debug: ~30 min the first time.

---

## 87. `gen[c4d.OPYTHON_CODE] = new_code` is the canonical live-patch for Python Generators

**Discovered 2026-05-09 through 2026-05-11** while iterating on a Python Generator (`c4d.Opython`, type 1023866) without restarting C4D every time.

For a Python Generator object `gen`, the source code is stored at parameter `OPYTHON_CODE = 400` as a string. Setting that parameter swaps the code that runs on next evaluation:

```python
gen[c4d.OPYTHON_CODE] = updated_code_string
gen.Message(c4d.OPYTHON_MAKEDIRTY)
gen.SetDirty(c4d.DIRTYFLAGS_DATA)
c4d.EventAdd()
```

**For surgical patches** (small edits to a large existing code blob), read-string-replace-write is faster than full re-deploy and avoids hitting the MCP token budget:

```python
code = gen[c4d.OPYTHON_CODE]
code = code.replace(old_block, new_block)
gen[c4d.OPYTHON_CODE] = code
```

**MCP token budget gotcha:** `execute_python_script` echoes back all script-scope variables in the response. If your script reads a large generator source into a `code` variable (e.g. 30-50 KB), the response blows past the 60-80 KB MCP token limit and gets truncated. Workaround: assign the patched code straight into the generator (`gen[c4d.OPYTHON_CODE] = patched`) and `print` only short status — don't keep the full code string in a top-level variable after assignment, or reuse a small local function scope.

**Other live-patch behaviors worth knowing:**

- UserData group adds (`DTYPE_GROUP` via `AddUserData`) consume a UD slot index, shifting subsequent indices by one. Easy to lose 30 min on this; symptom is "my Cluster Overlap slider value is going to a different field."
- `gen[c4d.ID_USERDATA, N] = float_value` can fail with `__setitem__ expected int or bool, not float` even when the DescID reports `dtype=19` (REAL). Cause unconfirmed — possibly inherited state from a prior wrong-typed `AddUserData`. Workaround: assign an int and use `float()` in the generator's read.

---

## 86. `GetPixelRatio()` is NOT always 1.0 on Windows — returns the actual logical→physical scale

**Discovered 2026-05-09** while debugging a custom GL window inside a `GeDialog`. The SDK comment in `c4d_gui.h:858` says:

> "Always returns 1.0 except for user areas shown on OS X Retina displays, where it returns 2.0."

That's wrong as of C4D 2026 on Windows HighDPI. Real measurement on a Windows 11 machine with 150% display scaling: `GeUserArea::GetPixelRatio()` returned `1.5`. On 200% scaling it returns `2.0`. The function exposes the per-monitor effective DPI scale on Windows too — Maxon updated the implementation but didn't update the doc comment.

**Implications:**

- For sizing your own native HWND inside a GeUserArea, multiply logical sizes by `GetPixelRatio()` to convert to physical pixels.
- This makes `GetPixelRatio()` the right cross-platform DPI helper. Don't reach for raw `GetDpiForWindow` / `MonitorFromWindow` Win32 calls — the C4D API already abstracts it.
- If you wrote code that assumed `ratio == 1.0` on Windows, your child windows / framebuffers / mouse-coord conversions are silently wrong on HighDPI displays.

**How it surfaced:** SplatFlow custom GPU viewer was creating a GL child window sized via `GeUserArea::GetWidth/GetHeight` (which return logical pixels). The result was visibly cropped to ~67% of the GeUserArea — that's `1/1.5`. Logging confirmed `GetPixelRatio() == 1.5`. Multiplying size by ratio fixed the cropping.

```cpp
// Sizing a native HWND child to fill a GeUserArea, DPI-correct:
const Int32 logW = userArea.GetWidth();
const Int32 logH = userArea.GetHeight();
const Float r   = userArea.GetPixelRatio();   // 1.5 on Win HighDPI, 1.0 on standard, 2.0 on Retina
const Int32 physW = (Int32)(logW * r);
const Int32 physH = (Int32)(logH * r);
MoveWindow(childHWND, x, y, physW, physH, FALSE);
```

---

## 85. `GeUserArea::Local2Global` returns APP-WINDOW coords, not screen and not parent-client

**Discovered 2026-05-09**, again while embedding GL in a GeDialog.

**Wrong assumption:** the SDK doc says "global window coordinates" so I assumed it returned Win32 screen coords (origin at desktop top-left).

**Actual behavior:** "global" here means **the C4D application window**, not the OS desktop. Both `GeUserArea::Local2Global` and `GeDialog::Local2Global` answer in the same coordinate space — relative to the C4D app window's top-left. To position a Win32 child of a GeDialog (which expects parent-client coords), you have to subtract:

```cpp
// Convert userArea position to dialog-client coords
Int32 ux = 0, uy = 0; userArea.Local2Global(&ux, &uy);
Int32 dx = 0, dy = 0; dialog.Local2Global(&dx, &dy);
const Int32 childX = ux - dx;
const Int32 childY = uy - dy;
MoveWindow(childHWND, childX, childY, w, h, FALSE);  // ← parent-client coords
```

If you skip the dialog subtraction and pass the userArea-app coords directly to `MoveWindow`, the child window ends up offset by the dialog's position-within-app — making it land *outside* the dialog (sometimes outside the C4D window entirely, in the top-left of the C4D app).

If you reach for `ScreenToClient(parentHwnd, &p)`, you over-correct because `Local2Global` was never in screen space to begin with. (We tried this; child ended up in the upper-left of the actual desktop.)

---

## 84. C++ `GePrint` is invisible to MCP `get_console_log` — only Python's `c4d.GePrint` is hooked

**Discovered 2026-05-08** while debugging a C++ plugin via cinema4d-mcp.

**Wrong assumption:** `GePrint("foo")` from C++ shows up in the same log buffer that `mcp.get_console_log()` returns.

**Actual behavior:** the MCP's `c4d.GePrint hook installed` line refers to a **Python-side hook** monkey-patched onto the `c4d` module's `GePrint` binding. C4D's *native* (C++) `GePrint` writes directly to the desktop console window (Window → Console) without going through Python — the hook never sees it.

**Implications for plugin debugging via MCP:**

- Output from C++ plugins is not retrievable via `get_console_log` — you have to look at C4D's native console (Window → Console) directly, or copy-paste it.
- Workaround: write diagnostics to a file path the MCP server can read back, OR have the user paste console contents.
- For pure plugin development this matters less, but for agent-driven dev loops where you want full automated read-back, you need to extend MCP with a native-console hook OR a file-tail tool.

**MCP feature gap:** ideally MCP would also hook native `GePrint` (likely via `MaxonLogger` interception or the application-log file). Logged as an enhancement candidate.

---

## 83. `GeDialog` close destroys child HWNDs but singleton dialog instances keep dead handles

**Discovered 2026-05-08** in a custom-viewer plugin that creates a private GL child HWND inside a GeDialog.

**Wrong assumption:** if you keep a `GeDialog` singleton alive across close/reopen cycles, the underlying HWNDs persist too. `EnsureContext()` can short-circuit on `if (_hRC) return true;` because everything is still valid.

**Actual behavior:** when the user closes the dialog (X button), Windows destroys the dialog's HWND **and all of its child HWNDs**. Your singleton still holds the same `_hwnd` pointer but it's now a dangling handle. `MoveWindow`, `GetClientRect`, etc. silently fail or return zero. Your GL context (if you cached `_hRC`) was bound to a destroyed drawable — `wglMakeCurrent` calls on it produce undefined behavior.

**Pattern:** detect a stale HWND with `IsWindow()` at the top of your bring-up function. If the cached HWND is dead, null out the entire GL state and let the bring-up path run fresh.

```cpp
Bool MyArea::EnsureContext()
{
    if (_hwnd && !IsWindow((HWND)_hwnd)) {
        // Dialog was closed — Windows destroyed our child HWND.
        _hRC = nullptr;
        _hDC = nullptr;
        _hwnd = nullptr;
        _program = 0;
        _vao = _vboPos = _vboCol = 0;
        g_gl.loaded = false;  // force re-load on the new context
    }
    if (_hRC) return true;
    // ... fresh CreateWindowExW + wglCreateContext + LoadAllGLFns ...
}
```

**How it surfaced:** SplatFlow viewer rendered correctly on first dialog open. After closing + reopening the dialog, every `GetClientRect(child)` returned `0x0` — the visible symptom was a permanently-stuck black render area with no splats visible despite a fresh splat being bound.

---

## 82. Embedding a private OpenGL surface inside `GeDialog` — known-good pattern (Windows)

**Discovered 2026-05-08, refined 2026-05-09.** C4D 2026's `Draw()` runs in a deferred-command-buffer model where `wglGetCurrentContext()==NULL` (gotcha #75), so you cannot do real GL inside `ObjectData::Draw`. The way to get a true GL pipeline (for splat viewers, custom render engines, etc.) without partner-SDK access is to host it in a separate `GeDialog`.

The full pattern, after four debug rounds:

**1. Create a Win32 child window of the dialog HWND:**

```cpp
HWND parent = (HWND)dialog->GetWindowHandle();  // public, despite @markPrivate

// One-time class registration with custom WndProc that:
//   - returns 1 on WM_ERASEBKGND (do nothing — we own the pixels)
//   - BeginPaint+EndPaint no-op on WM_PAINT (trust SwapBuffers)
auto wndProc = +[](HWND h, UINT m, WPARAM w, LPARAM l) -> LRESULT {
    if (m == WM_ERASEBKGND) return 1;
    if (m == WM_PAINT) { PAINTSTRUCT ps; BeginPaint(h, &ps); EndPaint(h, &ps); return 0; }
    return DefWindowProcW(h, m, w, l);
};

WNDCLASSW wc = {};
wc.style = CS_OWNDC;                  // private DC required for wgl
wc.lpfnWndProc = wndProc;
wc.hInstance = GetModuleHandleW(nullptr);
wc.hbrBackground = nullptr;            // never paint background
wc.lpszClassName = L"MyGLChild";
RegisterClassW(&wc);

HWND child = CreateWindowExW(0, L"MyGLChild", L"",
    WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN,
    0, 0, 100, 100, parent, nullptr, GetModuleHandleW(nullptr), nullptr);
```

**2. Set up GL on the child's DC:**

```cpp
HDC dc = GetDC(child);
PIXELFORMATDESCRIPTOR pfd = { sizeof(pfd), 1,
    PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER,
    PFD_TYPE_RGBA, 32, 0,0,0,0,0,0, 0,0,0,0,0,0,0,
    24, 8, 0, PFD_MAIN_PLANE, 0, 0, 0, 0 };
int pf = ChoosePixelFormat(dc, &pfd);
SetPixelFormat(dc, pf, &pfd);
HGLRC rc = wglCreateContext(dc);
wglMakeCurrent(dc, rc);
// Load modern-GL function pointers via wglGetProcAddress here.
```

**3. Single render owner — Timer drives, DrawMsg passes:**

The dialog's `SetTimer(16)` (60Hz) is the **only** entry point that calls render. The `GeUserArea::DrawMsg` override handles `EnsureContext` on the first call and otherwise returns immediately. Having both DrawMsg and Timer call render produces flicker (you get redraw races between C4D's paint pipeline and our timer). Pick one. Timer is more reliable.

**4. Position via dialog-relative subtraction (gotcha #85):**

```cpp
Int32 ux = 0, uy = 0; userArea.Local2Global(&ux, &uy);
Int32 dx = 0, dy = 0; dialog->Local2Global(&dx, &dy);
const Float ratio = userArea.GetPixelRatio();  // see gotcha #86
MoveWindow(child, (Int32)((ux-dx)*ratio), (Int32)((uy-dy)*ratio),
                  (Int32)(GetWidth()*ratio), (Int32)(GetHeight()*ratio), FALSE);
```

**5. Use `GetClientRect(child)` for `glViewport`** — never trust your cached size variables. The HWND is authoritative.

**6. HWND lifecycle (gotcha #83)** — check `IsWindow()` on dialog reopen.

This pattern delivered the perf breakthrough: switching SplatFlow's render target from "viewport" to "custom viewer" took the C4D viewport from 16fps to 150–200fps with 161K splats by removing the splat-display cost from the in-place Draw path entirely. The custom viewer renders the same data via private GL.

**Why GeDialog and not a `WS_OVERLAPPEDWINDOW` standalone:** the dialog gives you the surrounding UI (LINK widgets, mode dropdowns, status bar) for free without needing your own message pump. Tradeoff: tighter coupling to C4D's window hierarchy, hence gotchas #83-#86. For ship-quality work, GeDialog wins.

---

## 81. `GePrint` in C++ does NOT support `@`-formatted format strings — single `maxon::String` only

**Discovered 2026-05-08.** `DiagnosticOutput("foo @ bar @", x, y)` works (variadic, `@` is a placeholder). `GePrint` with the same syntax fails to compile because `GePrint`'s only signature is:

```cpp
void GePrint(const maxon::String& str);
```

To log structured data, build the string yourself:

```cpp
GePrint("Foo "_s + maxon::String::IntToString(n)
         + " bar "_s + maxon::String::FloatToString(f));
```

When mixing in C-strings from external APIs (e.g. `glGetString` returns `const char*`):

```cpp
const char* vendor = (const char*)glGetString(GL_VENDOR);
GePrint("vendor: "_s + maxon::String(vendor ? vendor : "?"));
```

Avoid `String((const Char*)...)` form because the type alias `cinema::String` vs `maxon::String` operator+ overloads can be ambiguous — be explicit with `maxon::String(...)` everywhere.

**Use `DiagnosticOutput` for templated/formatted logs**, or build maxon strings manually for `GePrint`. They go to different destinations: `GePrint` → C4D's Window→Console; `DiagnosticOutput` → maxon application log file. Choose based on visibility you need — see also gotcha #84 about MCP capture differences.

---

## 80. `maxon::Block<T>` view lifetimes do NOT survive `ExecutePasses` / scene re-evaluation

**Discovered 2026-05-08** while researching a C++ particle ingestion path. Modern C4D APIs return `maxon::Block<const T>` (and `maxon::StridedBlock<>`) — a `(T* ptr, Int size)` view into internal C4D-owned storage. They're zero-copy (great for performance) but the underlying buffer can reallocate / move during the next scene evaluation, particle solver tick, or generator rebuild.

**Wrong assumption:** treat the Block return like a `std::span` you can hold across calls. Cache it on a member, read from it later.

**Actual behavior:** the Block is only valid until the next scene-mutating call. Evaluators (`ExecutePasses`, `GetVirtualObjects` of any generator, `EventAdd`-triggered reevaluations) can invalidate the pointer.

**Pattern:** acquire → finish copy → release. Never hold across an eval boundary.

```cpp
// CORRECT
const maxon::Block<const Vector> pos = pg->GetParticlePositionsR();
const Vector* src = pos.GetFirst();
const Int N = pos.GetCount();
// ... copy or process all N elements RIGHT NOW ...
// Block goes out of scope; no problem.

// WRONG
class MyState {
    maxon::Block<const Vector> _cached;  // ← BAD: can dangle after next eval
};
state._cached = pg->GetParticlePositionsR();
doc->ExecutePasses(...);                  // ← _cached pointer may now be stale
```

**How it surfaced:** the C++ ingestion path for particle position bulk reads. The block is fine within a single GVO call or SceneHook tick. Caching it on a member of the consumer plugin would have produced rare crashes-on-frame-change bugs that are hard to trace.

---

## 79. `maxon::Block<T>` is the modern C4D bulk-data idiom — many APIs return it

**Discovered 2026-05-08.** When looking for "is there a bulk version of `GetSomething(i)` that doesn't loop per element," check the corresponding `c4d_libs/lib_*.h` header — most modern (2024+) C4D APIs expose a `Block<>` accessor returning a zero-copy view.

**Where to look:**
- `frameworks/cinema.framework/source/c4d_libs/lib_*.h` — the lib-wrapped public APIs
- `frameworks/core.framework/source/maxon/block.h` — the Block primitive itself
- Convention: `*R()` returns `const Block<const T>` (read), `*W()` returns `Block<T>` (write)

**Block API:**
```cpp
maxon::Block<T> blk = ...;
const Int  count = blk.GetCount();   // alive / active count
T*         data  = blk.GetFirst();   // pointer to first element
// Range-iterable
for (auto& x : blk) { ... }
```

**StridedBlock variant:** `(T* ptr, Int size, Int stride)` — used when underlying storage isn't tightly packed. Use iterators rather than direct pointer arithmetic on these.

**Concrete examples found in 2026 SDK:**
- `ParticleGroupObject::GetParticlePositionsR() → Block<const Vector>`
- `ParticleGroupObject::GetParticleAlignmentsR() → Block<const Quaternion<Float32>>`
- `ParticleGroupObject::GetParticleVelocitiesR() → StridedBlock<const Vector32>`
- ...8 more on ParticleGroupObject alone
- Plus parallel APIs on PointObject, mesh attribute channels, etc.

**Why it matters for plugin dev:** if your plugin reads bulk per-frame data (positions, colors, point attributes, particle state), assuming "I need to loop with `GetX(i)` per element" silently wastes 50–500× the time of the bulk pattern. Always check the lib header for a Block-returning sibling first.

**Why it matters for MCP:** MCP tools that expose particle / point-cloud / vertex data to agents should call the C++ Block API on the C4D side and `memcpy` the contents into a Python `bytes` / `numpy` buffer in one shot. Looping `GetParticle(i).GetPosition()` from Python is the classical anti-pattern — even if every individual call is "fast," the overhead of N round-trips through the C-Python boundary kills the bulk-data use case.

---

## 78. Public `ParticleGroupObject` C++ API has zero-copy bulk accessors — most plugin docs don't cover it

**Discovered 2026-05-08** while looking for a C++ replacement for Python's `pg.GetParticlePositionsR()`. The C++ API is fully public, lives in the standard `cinema.framework`, and returns zero-copy `maxon::Block<>` views.

**Header:** `frameworks/cinema.framework/source/c4d_libs/lib_particlegroupobject.h`
**Type IDs** (in `ge_prepass.h`):
- `Ofpgroup        = 1060887` — ParticleGroupObject
- `Ofpmeshemitter  = 1062577` — Mesh Emitter (parent)

**Cast pattern:**
```cpp
#include "c4d_libs/lib_particlegroupobject.h"

if (op->GetType() == Ofpgroup) {
    auto* pg = static_cast<ParticleGroupObject*>(op);
    const maxon::Block<const Vector> pos = pg->GetParticlePositionsR();
    const Int alive = pos.GetCount();              // alive count
    const Vector* src = pos.GetFirst();            // Float64 xyz, contiguous
    // copy / convert / SIMD ...
}
```

**Full read API** (all `R()` suffix; matching `W()` writers exist):
| Method | Returns |
|---|---|
| `GetParticleUniqueIdsR()` | `Block<const UInt32>` |
| `GetParticlePositionsR()` | `Block<const Vector>` (Float64 xyz) |
| `GetParticleColorsR()` | `Block<const ColorA32>` |
| `GetParticleVelocitiesR()` | `StridedBlock<const Vector32>` |
| `GetParticleAgesR()` | `Block<const Float32>` |
| `GetParticleLifetimesR()` | `Block<const Float32>` |
| `GetParticleRadiiR()` | `Block<const Float32>` |
| `GetParticleDistancesTraversedR()` | `Block<const Float32>` |
| `GetParticleAlignmentsR()` | `Block<const Quaternion<Float32>>` |
| `GetParticleAngularVelocitiesR()` | `StridedBlock<const Vector32>` |

**Eval timing:** populated during `ExecutePasses(EXECUTIONFLAGS::ANIMATION | ::EXPRESSIONS | ::CACHES)`. Reading before any eval returns a 0-count block. SceneHooks at `EXECUTIONPRIORITY_GENERATOR` (or just after the particle solver's priority) get fresh data for the current frame.

**Wrong assumption:** "particle access in C++ requires the older `ParticleObject::GetParticleR(i)` per-particle API or going through Python."

**Actual behavior:** the new C4D Native Particles (2024+ Mesh Emitter system) exposes a fully bulk C++ surface with zero-copy reads. Per-particle calls are unnecessary.

**Why it matters for MCP:** an MCP tool that returns particle positions should use `ParticleGroupObject::GetParticlePositionsR()` server-side and ship the buffer to the agent as a single `bytes`/numpy payload. Avoid the per-particle loop pattern entirely.

**Doesn't apply to:** X-Particles (Insydium plugin, separate SDK gated by partner agreement). For XP, fall back to the Python proxy or ask Insydium for SDK access.

---

## 77. Custom GPU rendering needs `GeDialog`+`GeUserArea`+private Win32 child window — bypass `Draw()` entirely

**Discovered 2026-05-08** while researching custom point-cloud / particle / GPU-instanced viewport renderers (sibling problem to gotcha #76).

**Wrong assumption:** "I'll add `glDrawArrays`/VBOs to my `ObjectData::Draw()` callback for fast custom rendering."

**Actual behavior:** see #76 — `Draw()` runs without a current GL context in 2026, so raw GL silently fails. The fix is **don't draw in `Draw()`**.

**Pattern that works (Octane Live Viewer / V-Ray Frame Buffer):**
```
ObjectData (data + sim + scene state)
    │  Draw() returns SKIP — pixels are not its job
    ▼
GeDialog (dockable, registered as CommandData menu entry)
    └─ GeUserArea (reserves screen real estate)
         └─ Win32 child window (CreateWindowEx, parent = dialog HWND)
              └─ HDC + HGLRC (wglCreateContext succeeds here — normal Win32 surface)
                   └─ VAO + VBO + GLSL shaders + glDrawArrays / glDrawElements
                        → pixels at hardware speed (200–300fps for 500K points on RTX 3090)
```

**Why this works:** the Win32 child window has a normal surface with a normal HDC. `wglCreateContext` succeeds; `wglMakeCurrent` makes a context current that survives across calls; modern GL extension loading via `wglGetProcAddress` works as documented.

**Position the child window inside the GeUserArea bounds:** track `Sized()` messages on the user area and call `MoveWindow(childHwnd, x, y, w, h)`.

**Camera sync:** read C4D's active viewport via `BaseDraw::GetMg()` (camera matrix) + `GetSafeFrame()` for FOV → upload to a uniform buffer per frame.

**Why it matters for MCP:** MCP tools that screenshot custom viewports need to know about this pattern. Plugins using it will not appear in C4D's `viewport_screenshot` output (they're separate Win32 surfaces). MCP tooling for custom-viewer plugins would need a separate "screenshot the custom viewer dialog" tool that targets the dialog's HWND directly.

---

## 76. `drawport.framework` is a private partner SDK — forward-declared types are unusable in public plugins

**Discovered 2026-05-08** while researching custom GPU viewport rendering. C4D 2026's public SDK exposes `BaseDraw` methods that return `maxon::DrawportRef`, `maxon::DrawportContextRef`, `maxon::DrawportRedrawHelperRef`, `maxon::ViewportRenderRef` — but these types are **forward-declared only**. No class definition exists in the public headers, so you can call `bd->GetDrawport(...)` but you cannot `.method()` the returned reference.

**Smoke-test the partner-SDK boundary:** GSL/GSLGPU (the splat-rendering plugin by Alpha Pixel) ships strings in its binary like `DRAWPORT_STATE_DEPTH_TEST_MODE`, `SORT_32BIT/24/16`, `RENDER_MODE_GAUSSIAN`. These are flags passed to drawport-API entry points. Alpha Pixel has a Maxon developer partner relationship — they have the `drawport.framework` headers; public-SDK plugins do not.

**Wrong assumption:** "If `BaseDraw::GetDrawport()` is in the public SDK, the result is usable in a public-SDK plugin."

**Actual behavior:** the API surface is half-exposed. Calls compile, but consumers can't materialize the return type. This is intentional — Maxon controls who can render at the GPU level.

**Public-SDK alternative:** see #77 — host your own GL context in a `GeDialog`+`GeUserArea`+private Win32 child window. Doesn't require partner status.

**Why it matters for MCP:** any MCP tool that promises GPU-resident geometry buffers / custom render passes needs to use the public-SDK alternative. Don't write tools that silently depend on `drawport.framework` symbols — they won't link without the partner SDK.

---

## 75. `wglGetCurrentContext()` returns NULL inside `ObjectData::Draw()` in C4D 2026

**Discovered 2026-05-08.** C4D 2026 changed the viewport rendering pipeline to a **deferred command-buffer model**. The thread that calls your `ObjectData::Draw()` callback owns no current GL context. Raw `glXxx` / `wglGetProcAddress` calls silently fail and your custom rendering falls through to whatever `BaseDraw::DrawXxx()` calls you make as fallback.

**Diagnostic:** add this to `ObjectData::Draw()`:
```cpp
HGLRC ctx = wglGetCurrentContext();   // returns NULL in C4D 2026
```

**Wrong assumption (carryover from C4D 2024 and earlier):** `Draw()` is called with the viewport's GL context current; raw GL calls work; you can load extensions via `wglGetProcAddress` and use VBOs / shaders directly.

**Actual behavior:** the deferred pipeline submits draw commands; `Draw()` is invoked in a state where querying GL gives you no usable context. `wglGetProcAddress` returns NULL (it needs a current context to work). VBO uploads silently no-op. Your custom GL pipeline never runs.

**Path forward:** see #76 (drawport.framework is private) and #77 (the GeDialog+GeUserArea workaround).

**Why it matters for MCP:** if a user reports "my custom GL plugin worked in 2024 and renders nothing in 2026," this is the diagnosis. MCP tools that introspect render-engine state should be aware that ObjectData-based custom rendering doesn't work the way it used to.

---

## 74. Bulk SN graph mutation in one Python script triggers `pythonvm.module.xdl64` crash under memory pressure

**Discovered 2026-05-02** while attempting to bulk-swap 92 nodes in one mega-script (in-place parallel replacement methodology on Match Size practice file). C4D crashed with `ACCESS_VIOLATION` in `pythonvm.module.xdl64`. Memory peaked at 36.7 GB across 10-hour session + 5 open scenes.

Confirmed at smaller scale: even 6 batches of 15 in ONE Python script (90 swaps total) crashes the same way. Single batch of 15 in one ping works cleanly.

**Fix:** keep batches to ~15 mutations per ping, restart C4D periodically during long sessions, close stray scenes. The right long-term fix is a C++ MCP-side `scene_nodes_bulk_swap_nodes` tool that handles transaction lifecycle properly.

## 73. Redshift's `redshift4c4d.xdl64` NULL-derefs on scripted SN graph mutations

**Discovered 2026-05-02** during bulk swap work. ANY scripted `BeginTransaction → AddChild → Commit` in an SN graph (no events, no SetDirty, no ExecutePasses needed) crashes C4D with `ACCESS_VIOLATION` in `redshift4c4d.xdl64` callbacks. Manual UI mutations don't trip this. Confirmed across 5 crash reports — same `c4d_base.xdl64 → redshift4c4d.xdl64` call stack pattern, identical instruction address.

Setting `RDATA_RENDERENGINE = 0` does NOT unload Redshift — its plugin loads at startup and registers callbacks regardless of active engine.

**Fix:** disable Redshift plugin via folder-rename + .xdl64 file-rename. **Three load paths** to disable on a typical install:
- `C:\Program Files\Maxon Cinema 4D 2026\plugins\Redshift\`
- `C:\Program Files\Maxon Cinema 4D 2026\Redshift\` (outside plugins folder)
- `C:\Program Files\Maxon Redshift 2026\Plugins\C4D\R2026\Redshift\`

Renaming the folder alone is NOT enough — C4D scans by file extension. Must also rename `redshift4c4d.xdl64` → `redshift4c4d.xdl64.DISABLED`.

## 72. `<` and `>` in graph-node parent chains are AMBIGUOUS — both root gateways AND port-group names inside every node

**Discovered 2026-05-02** while debugging the in-place parallel-replacement bulk-swap. Climbing the parent chain of a port to find its node id, the first `>` or `<` encountered is usually a node's local OutputPortGroup or InputPortGroup, NOT the root output/input gateway. The root gateway also uses `<``>` as its id.

For a wire from `arithmetic.in1 ← reroute.out`, the chain is `[out, >, reroute@xxx, root]`. Naive climb returns `>` and treats it as root.

**Fix:** when climbing parent chain to resolve a port to its owning node, prefer ids containing `@` or starting with `context_` (real nodes). Only return `<``>` if no other match found AND we've climbed all the way to root level.

## 71. `arithmetic` node `operation``datatype` ports cannot be Connect()-mirrored — they're DEFAULT VALUE ports

**Discovered 2026-05-02** during bulk-swap. `port.GetConnections(PORT_DIR.INPUT)` on `operation` or `datatype` ports of an `arithmetic` node returns wires that look real but are internal "default value feeds." Calling `Connect()` to mirror them throws `no target to copy for '<net.maxon.graph.interface.graphmodel>'` and the error fires at `txn.Commit()` time (not at the Connect call), poisoning the whole transaction.

**Fix:** when bulk-swapping arithmetic-like nodes, skip `operation` and `datatype` in the input-mirror loop. Capture them via `GetDefaultValue()` and apply via `SetDefaultValue()` BEFORE wiring (per gotcha #69 — datatype port disappears after first connection).

## 70. Single-source-per-input-port: dual-feeding breaks the visual

**Discovered 2026-05-01** during in-place parallel-replacement swap #2 (`inversematrix@HvjBjO`). Connecting both `OG.out` and `MINE.out` to the same downstream port produced visually-broken output (file size dropped from 136,116 baseline to 93,565 bytes).

Most input ports accept ONE source at a time. The "parallel reading" phase of in-place replacement is fine for inputs (multiple consumers of one source = OK), but you can't dual-feed the same downstream input port.

**Fix in the swap protocol:** delete OG first to free its downstream input ports, THEN wire MINE.out to those now-free targets.

## 69. `arithmetic` node's `datatype` port DISAPPEARS after the first connection — set it BEFORE wiring

**Discovered 2026-05-01** during the bb→arithmetic auto-bbox-read crack. After connecting any input to an `arithmetic` node, the `datatype` port becomes unavailable for `SetDefaultValue()`. The internal type system has resolved the type from the connection, and the explicit datatype slot is gone.

**Fix:** set `datatype` BEFORE making any wire connections to the node. If you need to change datatype, you must re-add the node fresh.

## 68. `arithmetic` operation cycle Ids are SHORT — `sub``div``add``mul`, NOT `subtract``divide`

**Discovered 2026-05-01** via 4-iteration loop debugging the auto-bbox-read chain in Match Size. Wrong cycle Ids silently fall back to scalar mode (no error). The graph "works" but produces wrong values.

Also for `datatype`: the canonical Id is the FULL parametrictype path: `net.maxon.parametrictype.vec<3,float>` — NOT `vector` or `vec3`.

**Fix:** when configuring arithmetic-family nodes, use the short canonical Ids:
- operation: `add` / `sub` / `mul` / `div`
- datatype: `net.maxon.parametrictype.vec<3,float>` (or scalar `net.maxon.float64`)

## 67. `bb.bbox` is a composite AABB struct, NOT a vec3 — use `bb.max - bb.min` for source size

**Discovered 2026-05-01** while building auto-bbox-read in Match Size. The `bb` (bounding box) Neutron node has 4 outputs: `max`, `min`, `center`, `bbox`. The `bbox` output is a composite AABB struct (containing both min+max), not a vec3 size. Feeding it directly into a vec3 arithmetic input produces degenerate results.

**Fix:** for the source SIZE in vec3 form, compute it as `bb.max - bb.min` via an arithmetic(sub, vec<3,float>) node.

## 66. `GetPortValue()` returns DESIGN-TIME defaults, not RUNTIME-evaluated values

**Discovered 2026-05-01** during Match Size rebuild debugging. Calling `GetPortValue()` on an output port (e.g. an arithmetic's `out`) returns the cached default, not what the graph actually computes at runtime. This caused hours of false debugging where the math was correct but the API said the wrong number.

**Fix:** trust the visual outcome (screenshot diff), not the `GetPortValue()` query. For verification, render a viewport screenshot and byte-compare to a baseline.

## 65. SN deformer cache refresh requires the FULL 4-step ritual — `EventAdd` alone is NOT enough

**Discovered 2026-05-01** as the single biggest blocker during Match Size rebuild. After mutating an SN deformer's graph, calling only `c4d.EventAdd()` is not enough — the deformer's parent's polygon cache stays stale. The graph evaluates correctly but the viewport shows the un-deformed native geometry.

**Fix — the 4-step ritual:**

```python
sn_host.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_DESCRIPTION | c4d.DIRTYFLAGS_MATRIX)
parent_obj.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_MATRIX)
c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)
```

All four are required. The companion gotcha #66 (GetPortValue lies) made this maddening to diagnose — the math was right; it was the cache that lied.

## 64. Asset DB unmounted = empty 2-node graph with NO error

**Discovered 2026-05-01** while loading the reference Match Size practice file. When `scene_nodes_walk` returns only `context_externaltimeinput` + `context_notime` and no real nodes, suspect MISSING ASSET DATABASE (not mounted in Prefs → Library), not an empty graph. C4D doesn't surface an error — the graph just appears stub-empty.

**Fix:** verify the required asset DB is mounted in Prefs → Library before loading scenes that depend on it.

## 63. `scene_nodes_describe_node_template` may LEAK probe nodes into the live graph if cleanup fails

**Discovered 2026-05-01** while iterating on Match Size rebuild. The `describe_node_template` MCP tool adds + removes a temporary instance of the queried node template — but cleanup can silently fail. The leaked node sticks in the graph and pollutes subsequent operations.

**Fix:** always inspect `cleanup_succeeded` in the tool's response. If `false`, manually remove the leaked instance via `graph.BeginTransaction()` + `node.Remove()` before continuing.

## 62. Bare basenames are NOT asset IDs — `transform_element` ≠ asset ID

**Discovered 2026-05-01** during from-scratch Match Size replica work. Asset IDs follow patterns like `net.maxon.neutron.geometry.transform_element`, NOT just `transform_element`. The basename is the BASE of an instance ID (text before `@hash`), not an asset_id.

**Fix:** use `scene_nodes_list_assets(source="repository", filter_substring=basename)` to find canonical IDs.

Verified asset IDs from Match Size work:
- `transform_element` → `net.maxon.neutron.geometry.transform_element`
- `bb` → `net.maxon.neutron.geometry.bb`
- `connect_geometries` → `net.maxon.neutron.geometry.connect_geometries`
- `arithmetic` → `net.maxon.node.arithmetic`
- `composematrix` (artist form) → `net.maxon.node.composematrix`
- `floatingio` → `net.maxon.node.floatingio`
- `inversematrix` → `net.maxon.node.inversematrix`
- `reroute` → `net.maxon.node.reroute`
- `if` → `net.maxon.node.if`
- `switch` → `net.maxon.node.switch`
- `compare` → `net.maxon.node.compare`
- `transformmatrix` → `net.maxon.node.transformmatrix`
- `type` → `net.maxon.node.type`
- `scale` → `net.maxon.node.scale`
- `legacyobjectaccess` → `net.maxon.nbo.node.legacyobjectaccess`

`invertselection`, `getcount`, and `cube` are NOT under `net.maxon.neutron.geometry.*` — their canonical IDs need separate lookup.

## 61. composematrix has TWO forms with DIFFERENT port schemas — verify before assuming

**Discovered 2026-05-01** during Match Size rebuild. Two distinct asset IDs share the basename `composematrix`:

- `net.maxon.node.composematrix` — artist form: `scale` (vec3), `translation` (vec3), `rotation` (vec3), `rotationorder` (int). Output: `out`.
- `net.maxon.node.access.composematrix*` — basis-vector form: `off`, `v1`, `v2`, `v3`. Common in tooling/access-namespace builds.

the reference Match Size uses the basis-vector form (with `_0``_1` floatingio routing children); my MVP rebuild used the simpler artist form. Both work for normalization but they're different node templates.

**Fix:** when two graphs use the "same" node basename, verify they're the SAME asset_id by checking the namespace path. Don't assume.

## 60. `scene_nodes_connect_ports` MCP tool can't address root `<``>` gateways — use Python directly

**Discovered 2026-05-01** during Match Size MVP build. The MCP tool's name resolver doesn't recognize `<``>` as valid node names — it errors with `dest GetInputs failed: no target to copy for '<net.maxon.graph.interface.graphmodel>'`.

**Fix:** for connections that involve the root input/output gateways, drop into Python:

```python
nodes = {str(c.GetId()): c for c in root.GetChildren()}
root_in, root_out = nodes["<"], nodes[">"]
# Then iterate root_in.GetChildren() / root_out.GetChildren() to find specific gateway ports
```

The Connect direction convention is `source_port.Connect(target_port)` — the OUTPUT side calls `.Connect(input_side)`.

---

## 59. Particle scenes need PLAY mode, not SCRUB — `SetTime(N)` per-frame triggers full recomputes that timeout

**Discovered 2026-05-01** while studying the Coral Structures Tutorial
scene. Spenser's correction: *"with something like particles you may
want to PLAY rather than scrub 0,30,60, etc"*.

### The failure mode

For scenes containing C4D 2026's new particle system (Mesh Emitter +
Particle Group + Field Condition + Kill / Switch Group, etc.) or
classic dynamics (Cloth, Soft Body, Pyro), the scrub pattern that
works for Memory@-only scenes:

```python
# Times out on particle scenes:
for f in range(0, 100):
    doc.SetTime(c4d.BaseTime(f, fps))
    doc.ExecutePasses(...)
```

…wedges C4D's main thread because each `SetTime` + `ExecutePasses`
forces the particle solver to **re-evaluate from its previous-recorded
state**. If the target frame isn't cached, C4D recomputes from frame 0.
Result: 30s execute_python_script timeouts.

### Detection

A scene is particle-heavy if any of these object types are present:

| Plugin ID | Object | Notes |
|---|---|---|
| 1062577 | Mesh Emitter | C4D 2026 particle system |
| 1060887 | Particle Group | C4D 2026 particle system |
| 1062533 | Field Condition | particle modifier |
| 1061199 | Kill | particle modifier |
| 1062045 | Switch Group | particle modifier |
| 5126 | Connect Instance | with Tracer/Fracture |
| 1018655 | Tracer | trajectory tracer |
| 1018791 | Fracture | particle fracture |
| 1024529 | Cloth/Soft Body / Pyro | classic dynamics |

### Working alternatives

1. **Trust mid-state captures.** If f25 already shows the developed
   simulation state (full particle distribution), that's
   architecturally sufficient. Don't force f50/100 captures of
   nearly-identical-looking coverage.

2. **Use playback** if available. Once a scene has been played in C4D
   the cache is warm; capture from cached state.

3. **Single-frame stepping with longer timeouts** between calls. Step
   one frame at a time, ping in between. Slow but stable.

4. **Pre-cook the particle simulation** in the scene file (Bake to
   PSR / Memory Cache) before MCP study, then capture from cache.

### Why it matters for cinema4d-mcp

`viewport_screenshot(frame=N)` should DETECT particle-presence and
either:
- Warn the agent ("scene has particles; mid-state capture only")
- Use playback under the hood
- Increase the timeout dynamically

Currently the agent has to know to capture early frames + accept
mid-state-as-architecture-proof.

---

## 58. Use TOP-DOWN camera for flat 2D Scene-Nodes output (splines on plane, vertex maps, ornaments)

**Discovered 2026-05-01** while studying the the reference build `Spline_Grower_Ornament`
scene. Spenser's correction: *"your camera view was just level with the
ground plane so you couldnt see it from that view — i rotated to the top
(in perspective still) and it shows up nicely"*.

### The failure mode

For scenes that output **flat 2D content** (procedural splines lying
on the y=0 plane, vertex maps, painted-on-mesh patterns), a side-view
camera at y=0 looking horizontally sees the geometry **edge-on** — the
output appears as zero-thickness lines or a single pixel wide. Easy to
mis-conclude "scene is broken" or "geometry isn't generating."

### The fix

For procedural-spline / 2D-pattern scenes, use top-down framing:

```python
def frame_top_down(doc, host):
    cam = c4d.BaseObject(c4d.Ocamera)
    center = host.GetMg().off + host.GetMp()
    rad = host.GetRad()
    size = max(rad.x, rad.y, rad.z) * 2.5
    cam.SetAbsPos(center + c4d.Vector(0, size, 0))
    cam.SetRelRot(c4d.Vector(0, c4d.utils.DegToRad(-90), 0))
    doc.InsertObject(cam)
    doc.GetActiveBaseDraw().SetSceneCamera(cam)
```

### Decision rule

Default to **top-down** when the host produces:
- Spline output (Nodes Spline, ornament/curl/branch generators, paths)
- Vertex maps on a flat input mesh
- Voronoi tessellation / 2D pattern generation
- Anything intended to be viewed from above

Default to **side perspective** when the host produces:
- 3D volumetric output (Volume Mesher, Cloth, Hair, displacement on Sphere)
- Generators with explicit Z-axis structure (towers, recursive subdivision)
- Any scene with a SceneCamera authored by the artist

### Related: field positioning is a separate sanity check

Field-driven scenes (containing `getvertexselectiondata@`) can still
appear empty even with correct camera angle if the C4D Fields don't
overlap the input geometry. Repositioning fields into the input volume
is a useful behavioral test:

```python
host_center = host.GetMg().off + host.GetMp()
field.SetAbsPos(host_center)
```

But **camera framing is the FIRST thing to check** — don't move fields
before confirming the camera isn't edge-on to flat output.

### Why it matters for cinema4d-mcp

`viewport_screenshot` should auto-detect "this scene's host outputs flat
geometry" and prefer top-down framing. Heuristic: if `host.GetCache()`
is a SplineObject / LineObject, or if the cache bbox has near-zero
y-radius, default to top-down.

---

## 57. `memory@` Neutron primitive only updates on SEQUENTIAL frame stepping — direct `SetTime(N)` jumps produce stale state

**Discovered 2026-05-01** while studying the the reference build `Volume_Infection`
scene. The Neutron `memory@` primitive carries per-frame state via a
self-feedback wire (`out._0 → in._0`). State only propagates when frames
advance contiguously — jumping in time bypasses the per-frame update.

### Reproduction

In a scene with a `memory@` primitive driving e.g. a Volume Builder:

```python
# WRONG — produces 0 evolution:
doc.SetTime(c4d.BaseTime(120, fps))
doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS.NONE)
mesher_cache_points = mesher.GetCache().GetPointCount()  # 0

# RIGHT — sequentially steps, Memory accumulates each frame:
for f in range(0, 121):
    doc.SetTime(c4d.BaseTime(f, fps))
    doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS.NONE)
mesher_cache_points = mesher.GetCache().GetPointCount()  # > 0, fully evolved
```

### Concrete numbers from Volume_Infection

| Method | Frame | Mesher cache points |
|---|---|---|
| Direct `SetTime(120)` | 120 | **0** |
| Sequential 0..5 | 5 | 18 |
| Sequential 0..15 | 15 | 1432 |
| Sequential 0..30 | 30 | 3858 |

### Implication for cinema4d-mcp

Any simulation scene (RD, infection, fire, growth, cellular-automaton)
that uses `memory@` requires **contiguous frame stepping** in MCP
operations. Affected handlers:

- `viewport_screenshot(frame=N)` — currently jumps; needs sequential
  step-up option for simulation scenes.
- `render_frame` — same.
- `scene_assert` over time — same.
- Any test loop that wants to validate per-frame state.

### How to detect "this scene uses memory@"

After loading, walk the host's Neutron graph and check for any
`memory@<hash>` node:

```python
NEUTRON = "net.maxon.neutron.nodespace"
nbr = host.GetNimbusRef(NEUTRON)
if nbr:
    ng = nbr.GetGraph(maxon.NODE_KIND.NODE)
    has_memory = any(
        "memory@" in str(n.GetId())
        for n in ng.GetViewRoot().GetChildren()
    )
```

If `has_memory`, schedule a sequential warm-up loop before any
frame-jump operation.

### Why it matters architecturally

This isn't a bug — it's the framework's signature for "this is a
real time-dependent simulation, not a pure procedural evaluation."
Pure procedural graphs (recursive subdivision, scene 02) evaluate
the same regardless of frame-arrival order. Simulations gate on
sequential time. The presence of `memory@` is the marker.

---

## 56. Plugin ID 180420500 (Scene Nodes Generator) uses the **Neutron** nodespace, NOT `net.maxon.nodespace.scene`

**Discovered 2026-05-01** while studying the the reference build `Reaction_Diffusion`
scene. Both hosts in that scene are type `180420500` ("Scene Nodes
Generator"). The expected access path:

```python
nbr = host.GetNimbusRef("net.maxon.nodespace.scene")
# returns None — wrong nodespace for this plugin
```

The actual access path:

```python
NEUTRON = "net.maxon.neutron.nodespace"
nbr = host.GetNimbusRef(NEUTRON)
ng = nbr.GetGraph(maxon.NODE_KIND.NODE)
root = ng.GetViewRoot()
```

Confirmed via `host.GetAllNimbusRefs()` which returned a single tuple
`(maxon.Id "net.maxon.neutron.nodespace", NimbusBaseRef ...)`.

### Updated nodespace-by-plugin-ID table

⚠️ **UPDATED 2026-05-01 (scene 05 Spiderweb):** plugin ID alone is **not
authoritative**. The same plugin ID can host EITHER nodespace depending on
when/how the scene was authored. Scene 05's Nodes Spline (180420700) uses
Neutron, but scene 02's same-ID host uses the older `nodespace.scene`.

The table below is the **common case** observed across the reference build scenes —
always probe `host.GetAllNimbusRefs()` for the actual nodespace before
accessing the graph.

| Plugin ID | Plugin Name | Common Case | Possible |
|---|---|---|---|
| 180420400 | Nodes Modifier (Deformer) | `net.maxon.nodespace.scene` | Neutron (verify per-scene) |
| 180420500 | **Scene Nodes Generator** | **`net.maxon.neutron.nodespace`** | scene (verify per-scene) |
| 180420600 | Nodes Mesh simple | `net.maxon.nodespace.scene` | Neutron (verify per-scene) |
| 180420700 | Nodes Spline | `net.maxon.nodespace.scene` OR Neutron | both observed in the reference build |

### Why this matters

The two nodespaces share UI conventions (graph editor, AM-exposed sliders,
floatingio ports) but have **different node libraries and different
canonical output sinks**. In `nodespace.scene`, the output emit is
`set_property → root.geometryout`. In `neutron.nodespace`, it's
`geometry@ → root's geometry input port`.

Other Neutron-specific quirks observed in the same scene:

- `memory@` primitive (per-frame state retention via self-feedback wire
  `out._0 → in._0`) — unique to Neutron.
- `nearestneighbor@` for K-NN spatial queries (also exists in scene
  nodespace but the surrounding port idioms differ).
- `getvertexselectiondata@` reads C4D Field-painted vertex selections
  from the input geometry — the Field-to-graph bridge.
- `containeriteration@` per-vertex iterator pattern.
- AM-slider names come back as `(unnamed)` from `host.GetDescription()`
  because Neutron's descid encoding doesn't surface DESC_NAME at leaf
  level; read from `floatingio` nodes' `effectivename` instead.
- Annotations are **not** OM-tag-based; they're encoded as
  `effectivename` on `scaffold@` nodes inside the graph (acting as
  graph-internal section headers).

### How to discover

```python
all_refs = host.GetAllNimbusRefs()
for nodespace_id, nbr in all_refs:
    print(nodespace_id, "->", nbr)
```

This returns the actual nodespace this host uses. **Always probe
GetAllNimbusRefs() before assuming a nodespace ID.**

---

## 55. THE NODES-FAMILY OUTPUT BRIDGE — Object Manager-bridged Scene Nodes containers come in 4 plugin variants, each with its own root-output recipe

**SUPERSEDES gotcha #54** (which incorrectly declared a "wall"). The "wall"
was wrong-plugin-ID — sampled 500/700, missed 600. **The output bridge IS
exposed in Python.** Cracked 2026-05-01 via Spenser's manual-baseline
diagnostic protocol.

**The Nodes family** (per C4D 2026 Command Manager — IDs 465002502-465002505):

| Container | Plugin ID | Root output port(s) | Bridges geometry kind |
|---|---|---|---|
| **Nodes Mesh** | `180420600` | `geometryout` | polygon mesh ↔ Object Manager |
| **Nodes Modifier** | `180420400` | `geometryout` (+ root.in `geometryin`) | deformer (modifies parent) |
| **Nodes Spline** | likely `180420500` or sibling | (probe needed) | spline object |
| **Nodes Selection** | (probe needed) | selection array | selection capsule |

### Recipe — Nodes Mesh (canonical, proven 2026-05-01)

```python
mesh = c4d.BaseObject(180420600)
doc.InsertObject(mesh)
mesh.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)
graph = mesh.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes).GetGraph()
root = graph.GetViewRoot()

# Add geometry-producing nodes (Cube, Sphere, Tube, modeling chains, etc.)
maxon.GraphDescription.ApplyDescription(graph, {"$type": "Cube", "$name": "my_cube"})

# Find ports
cube = <walk root.GetChildren() for cube@*>
cube_out = <find "geometryout" in cube.GetOutputs()>
root_geomout = <find "geometryout" in root.GetOutputs()>

# Wire DIRECTLY — no scene.root, no op.geometry wrapper, no variadic AddPort
with graph.BeginTransaction() as txn:
    cube_out.Connect(root_geomout)
    txn.Commit()

# Visible cube in viewport AND in Object Manager. host.GetCache() returns
# PolygonObject (type 5100). Done.
```

### Recipe — Nodes Modifier (acts on parent geometry)

The Nodes Modifier is a CHILD of the geometry it deforms. Its root has both
`geometryin` (host→graph: parent's geometry flows in) and `geometryout`
(graph→host: deformed result flows out).

```python
mod = c4d.BaseObject(180420400)
parent_cube.InsertUnder()  # mod must be child of target
# Inside graph:
# root.geometryin → first_modeling_op.geometryin
# (chain modeling ops via geometryout→geometryin)
# last_modeling_op.geometryout → root.geometryout
```

Verified working in `Untitled 4` reference scene: cube + Nodes Modifier
child with internal `extrude → root.geometryout` wiring.

### Recipe — doc-level Scene Nodes (no Object Manager bridge)

If you want to build EVERYTHING inside a doc-level SN graph (no OM bridge),
the recipe is different:

```python
graph = maxon.GraphDescription.GetGraph(doc)
# scene.root is auto-present in every doc-level graph (cannot delete)
# Add Cube + the "geometry" wrapper op + wire to scene.root.children._0
maxon.GraphDescription.ApplyDescription(graph, {"$type": "Cube", "$name": "c"})
maxon.GraphDescription.ApplyDescription(graph,
    {"$type": "#net.maxon.neutron.op.geometry", "$name": "geom"})

# Wire: cube.geometryout → geom.geometry → scene.root.op.objectbase.children._0
# (variadic _0 slot exists by default — no AddPort needed for first connection)
```

This renders in viewport but does NOT create an Object Manager entry.

### Why all my prior probes failed (sept 2026-05-01 discovery)

- I tested **180420500** (had complex `objectinput``op.input``op.objectbase`
  port set) — it does NOT have a simple geometryout output. Was the wrong
  plugin variant.
- I tested **180420700** with the Nodes Mesh recipe — got `cache=None`.
  Same simple `geometryout` port shape as 600 but doesn't render. Likely
  a different sibling variant (Nodes Spline?).
- I never tested **180420600** until Spenser's manual baseline protocol
  forced me to dissect his working setup.
- I filtered `net.maxon.neutron.scene.root` as scaffolding when it's the
  doc-level destination node.

**Lesson learned:** when probing the API surface, always include
"manual-baseline + dissect" as Step 1 before brute-forcing port hypotheses.

### Discovery process (2026-05-01)

1. Spenser's protocol: "Stop declaring the wall. Make the simplest manual
   working setup. I'll snapshot. Compare with what you'd build."
2. He dragged in the cube, it rendered. I snapshotted: doc-level graph
   had `scene.root` + `geometry@*` wrapper that I'd missed.
3. He pointed out the distinction: doc-level scene.root vs Nodes Mesh
   container. Nodes Mesh = OM-bridged.
4. He showed Command Manager: "Nodes Mesh" (ID 465002502), "Nodes
   Modifier" (465002504), "Nodes Spline" (465002503), "Nodes Selection".
5. He opened Maxon's "0100 Nodes Mesh" and "0130 Clone Onto Polygon
   Centers" reference scenes for ground truth.
6. Dissected `Mesh Primitive Group` (type 180420600) — annotation tag
   said *"This project demonstrates how to find different Mesh Primitive
   nodes (e.g. Cube, Sphere, Cone) and return their Geometry for use in
   the Objects Manager."*
7. The wiring inside: `cube.geometryout → root.geometryout`. Direct.
8. Rebuilt mine with plugin 180420600 — second cube appeared at offset
   immediately. Recipe proven.

---

## 54. ~~SN Generator output-routing wall~~ — INCORRECT, see gotcha #55

**This entry was wrong.** I claimed the SN Generator output side was
gated on Phase B C++ shim work (NodeTemplate publishing). It is NOT.
The "wall" was sampling the wrong plugin variants (180420500/700 instead
of 180420600 = Nodes Mesh).

The correct recipes — for both doc-level SN graphs AND each Nodes-family
container (Nodes Mesh / Modifier / Spline / Selection) — are in gotcha #55.

NodeTemplate publishing IS still a separate gap (relevant for surfacing
custom AM params on user-built capsules). But OM-bridged geometry output
from a generic Nodes Mesh container does NOT require it.

**Lesson:** declared "wall" without doing the manual-baseline-and-dissect
diagnostic Spenser explicitly requested. Pivoted to Path B (classic-stack
procedurality) when Path A was actually accessible. Will not repeat this
mistake.

---

## 53. SweepNurbs child order matters — profile FIRST, path SECOND. `InsertUnder` puts NEW child at TOP, so naive ordering swaps them

**Wrong:** to build a Sweep, insert the profile (cross-section) under the
sweep first, then the path (the spline being swept):

```python
profile.InsertUnder(sweep)
path.InsertUnder(sweep)   # WRONG — InsertUnder puts new child at index 0
```

This results in path-at-index-0 + profile-at-index-1, the opposite of
what Sweep expects. Visually you'll get a giant disc (the profile being
swept along the profile itself) instead of a thin tube.

**Actual:** `BaseObject.InsertUnder` always places the new object at the
**first child** position, shifting prior children down. So calling
`InsertUnder` on profile then on path leaves the path at index 0 (wrong)
and profile at index 1.

**Fix:** use `InsertUnderLast` for the second child, or insert in reverse
order:

```python
profile.InsertUnder(sweep)        # profile at index 0 ✓
path.InsertUnderLast(sweep)       # path appended after profile ✓
```

**Discovered:** 2026-05-01 building the M5 capstone (spline growth on
RD surface) — the sweep rendered as a flat pink disc until the order
was corrected.

## 52. Python-created BaseObjects sometimes ship with visibility set to `2` (UNDEF) — geometry won't render

**Wrong:** after `c4d.BaseObject(...)` + `doc.InsertObject(...)`, the
object should be visible by default.

**Actual:** in some scene contexts the new object lands with
`ID_BASEOBJECT_VISIBILITY_EDITOR = 2` and `ID_BASEOBJECT_VISIBILITY_RENDER
= 2` — the "undefined / inherit from parent" state. Whether that
inheritance resolves to visible depends on parent state and the cache
pipeline. Empirically: Volume Builder + Volume Mesher created via the
MCP `create_volume_builder` / `create_volume_mesher` handlers landed
with vis=2 and **did not render in viewport** until forced to
`vis_editor=0` + `vis_render=0`. Same for newly-created sweep nurbs
during the M5 capstone.

**Fix:** explicitly set both visibility flags after creation:

```python
obj = c4d.BaseObject(c4d.Osomething)
obj[c4d.ID_BASEOBJECT_VISIBILITY_EDITOR] = 0   # default visible
obj[c4d.ID_BASEOBJECT_VISIBILITY_RENDER] = 0
doc.InsertObject(obj)
```

**Implication for cinema4d-mcp:** the
`create_volume_builder` / `create_volume_mesher` / future
`create_sweep_nurbs` handlers should auto-set visibility=0 on the new
generators (and probably also on every object the user creates via
helper handlers) so the result actually renders.

**Discovered:** 2026-05-01 during the M4 RD battle test — Volume
Mesher cache had 14080 polys but viewport stayed empty until visibility
was forced to 0. Recurred in M5 with sweep nurbs — same fix.

## 51. SN Generator's `root.objectinput` does NOT have a `geometry` subport — bare `geometryout → objectinput` produces empty Null cache

**Wrong:** wiring `last_modeling_node.geometryout → root.objectinput` on a
fresh Scene Nodes Generator should make the geometry render.

**Actual:** the connection commits successfully (verified via
`GetConnections(2)` showing 2 incoming wires on `objectinput`), but
`host.GetCache()` returns an empty `Null` (type 5140) and `host.GetRad()`
is `Vector(0,0,0)`. `objectinput` has subports `color / domain /
parentmatrix / translation / sqrmatrix / matrix` but **no `geometry`
subport** — it accepts the wire but doesn't route bare polygon data into
a renderable cache.

**Implication:** SN Generator output requires either an object-composer
node (e.g. wrapping geometry in `net.maxon.neutron.op.objectbase` or via
the dissect-known capsule output pattern) OR the existing
`scene_nodes_apply_pattern` handler that knows the proven wiring. The
direct approach (just connect the last `geometryout`) is incomplete.

**Discovered:** 2026-05-01 attempting to build a Cube → Random Selection
→ Extrude → Subdivide chain end-to-end. Gotcha #50, #49, #48 also
surfaced in the same session. Tracker: SN Generator output composition
needs follow-up research lane (sister of the synthesize_port "connection
IS the type" breakthrough — output side may have a similar discoverable
recipe).

## 50. `net.maxon.node.transformvector` is vector ARITHMETIC, not matrix×vector

**Wrong:** by name, `transformvector` should apply a matrix transform to
a vector (e.g. rotate a position by an angle-derived matrix).

**Actual:** it's a 3-input arithmetic node — `operation` (enum) +
`in1` + `in2` → `out`. Same shape as `Arithmetic` but for vectors. To
actually transform a vector by a matrix you need a **separate** matrix
construction (`Compose Matrix` from rotation/translation) followed by a
matrix-vector multiply node — not a single "Transform Vector".

**Implication:** the Nodebase R1 (Iterations for Geometry Generation)
recipe scaffold in `scene_nodes_nodebase_study.md` was wrong on this
node. Fix: use `Compose Matrix` to build the rotation, multiply, then
extract the position. Or use `Cos` + `Sin` + `Compose Vector 3` directly
to skip the matrix.

**Discovered:** 2026-05-01 walking the freshly-added node's ports:
`in1 / in2 / operation / out` — no matrix input visible.

## 49. ApplyDescription `$type` rejects bare canonical IDs — needs UI label OR `#`-prefixed canonical

**Wrong:** `scene_nodes_add_node(asset_id="net.maxon.neutron.node.range")`
should work since that's the canonical asset ID.

**Actual:** fails with
`The node type reference 'net.maxon.neutron.node.range' (lang: 'en-US',
space: 'net.maxon.neutron.nodespace') is not associated with any IDs.`

Three valid forms for `$type` (already documented in
`data/verified_labels.json` but easy to miss):

```
"Range"                              # English UI label, case-sensitive
"#net.maxon.neutron.node.range"      # canonical ID with leading #
"#~.range"                           # lazy-form shorthand
```

The bare canonical ID (no `#`) silently fails. Discovery credit:
GPT 5.5 (2026-04-30) — the `#` convention comes from Maxon's
GraphDescription docs.

**Implication:** add input validation to `scene_nodes_add_node` to detect
a bare `net.maxon.*` ID and either auto-prepend `#` or return a
descriptive error pointing to the three valid forms.

**Discovered:** 2026-05-01 first add_node attempt with `net.maxon.neutron.node.range` failed; retry with `Range` succeeded.

## 48. Scene Nodes node-template index (802 entries) misses some live-registry assets — repository scan is authoritative

**Wrong:** `scene_nodes_atlas_lookup` against the bundled
`node_template_index.json` (802 entries) is the canonical source for
asset IDs.

**Actual:** the bundled index was built from prior dissection sessions
and misses several common nodes. Examples missed:
- `net.maxon.node.containeriteration` (Iterate Collection)
- `net.maxon.neutron.geometry.polygoninfo` (Polygons Info — note
  singular, not "polygonsinfo")
- `net.maxon.neutron.geometry.get_property` / `set_property` (note
  underscore)

The live `scene_nodes_list_assets(source="repository")` call against
`maxon.AssetInterface.GetUserPrefsRepository().FindAssets()` is the
authoritative discovery path.

**Implication:** when `atlas_lookup` returns no matches for a substring,
fall back to `scene_nodes_list_assets(source="repository")` before
concluding the asset doesn't exist. Or schedule a periodic atlas
refresh that union-merges the live repository into the bundled index.

**Discovered:** 2026-05-01 — atlas had no matches for "iterate" but
repository scan found `net.maxon.node.containeriteration`.

## 47. THE BIG ONE — for Scene Nodes ports, the CONNECTION provides type binding (not the description attributes)

**Wrong:** to make an AM-exposed port draggable in a Scene Nodes Generator,
you must construct a 9-attribute schema (`fixedtype`, `portDescriptionData`,
`portDescriptionUi`, `portDescriptionStringLazy`, etc.) matching what the
Resource Editor stores. Set `classification`, `datatype`, `unit`, `guitypeid`,
build a `LazyLanguageDictionary` for the label, etc. — the more attributes
you replicate, the more it should look like an editor-created port.

**Actual:** none of those attributes drive the widget binding. Setting them
explicitly *blocks* C4D's runtime type inference and produces locked text
widgets in the AM. The widget binding comes from the **port connection**:
C4D infers the port's type from the connected downstream port at runtime.

The minimal recipe (4 lines) produces a fully draggable AM-exposed
parameter:
```python
with graph.BeginTransaction() as txn:
    port = inputs.AddPort(name)
    port.SetPortValue(maxon.Float64(0.0))               # initial value
    port.SetValue("net.maxon.node.base.name", label)    # display name
    port.Connect(target_typed_inner_port)               # ← THIS binds the widget
    txn.Commit()
```

Type-morphing also works — disconnect + reconnect to a different-typed port
and the widget adapts. No `fixedtype`, no description dicts, no template
cloning needed.

**Discovered:** spent ~6 hours over-specifying the schema and producing
locked widgets. User suggested "just create blank, connect to typed port,
adjust from there" — and that worked. The overspecified schema was
*overriding* the type inference. See `docs/gesture_differ_findings.md` for
full reverse-engineering history.

---

## 46. `idata`, `value_flags`, and `fixedtype:NativePyDataType` are derived attributes — Python can't write them

**Wrong:** `port.SetValue("idata", ...)` should work like any other
attribute write.

**Actual:** these three attributes are *derived* — only writable during
C++ "attribute derivation" triggered by editor-internal commands. Python
SetValue calls error with:
```
"The derived attribute idata may only be set during an attribute derivation"
"The derived attribute value_flags may only be set during an attribute derivation"
```

`fixedtype` is even trickier — the editor stores it as a `NativePyDataType`
(a special Python wrapper bound to C++ derivation state). Constructing one
from Python via `maxon.DataType.Get(...)` produces a regular `maxon.DataType`
that doesn't trigger widget binding. There's no public Python API to create
a `NativePyDataType`.

**Workarounds (in order of preference):**
1. **Don't write them.** Use the connection-based recipe in gotcha #47 —
   the connection provides everything these attributes would.
2. **`port.CopyValuesFrom(template_port, includeInner=True)`** — clones
   ALL attributes including derived ones from an existing typed port. The
   docstring says it "excludes derived attributes" but with `includeInner=True`
   it actually transfers them.
3. C++ shim that calls the editor's command-framework path (this is what
   Phase A.1 was originally targeting — see strategic docs).

---

## 45. `dir(graph_node_instance)` triggers "expected generic datatype capsule" error

**Wrong:** `dir(my_port)` lists available methods like any Python object.

**Actual:** for freshly-allocated maxon GraphNode instances,
`dir(instance)` triggers a binding-internal type-resolution error:
```
TypeError: expected generic datatype capsule
```

Probably a maxon Python binding bug — `dir` walks the instance and
something in the proxy resolution fails on certain freshly-created
objects.

**Workaround:** walk the class instead of the instance:
```python
methods = set()
for cls in type(my_port).__mro__:
    for name in dir(cls):
        if not name.startswith("_"):
            methods.add(name)
```

This bypasses the instance-level binding and gets the full method list.

---

## 44. Variadic ports — Connect() to the parent creates metadata slots but NO data flow; must Connect to a child slot

**Wrong:** `connect_node.GetInputs().FindChild("geometryin").Connect(...)`
multiple times will create N variadic input slots that all carry the data
properly.

**Actual:** the parent variadic port (e.g. `connect_geometries.geometryin`)
has type `GenericInstantiation<Array<Tuple<Id, DataDictionary>>>` — it
accepts a STRUCTURED ARRAY of geometry+insertindex tuples, not direct
geometry. When you Connect to the parent, C4D dutifully records metadata
(`{_0/insertindex:1, _1/insertindex:2}`) but no actual data flows because
the path needs an orange CHILD slot.

**Correct pattern:**
```python
parent_variadic = find_port(connect_node, "geometryin", "in")
slot0 = parent_variadic.AddPort(maxon.Id("_0"))   # creates slot
slot1 = parent_variadic.AddPort(maxon.Id("_1"))   # next slot
clone_output.Connect(slot0)                         # data actually flows
setsel_output.Connect(slot1)
```

Slot identifiers go `_0, _1, _2, ...` — created on demand. Calling
`AddPort("_0")` twice errors with "already has a child port named _0".
Always check existing children first if you might re-run.

**Discovered:** built a Scene Nodes capsule with 2 wires into Connect's
variadic, all "succeeded" but the graph was red because data never flowed.
User pointed out the white-vs-orange port distinction in the editor view.

---

## 43. `graph.AddChild(child_id, node_id, args)` accepts long Maxon canonical IDs; `ApplyDescription` `$type` does NOT

**Wrong:** `GraphDescription.ApplyDescription(graph, {"$type": "net.maxon.neutron.node.primitive.cube"})`
adds a Cube node — same id you'd find via `AssetInterface.FindAssets`.

**Actual:** ApplyDescription's `$type` requires SHORT-FORM type labels
that aren't the same as the canonical asset registry IDs. Long-form IDs
like `net.maxon.neutron.node.primitive.cube` produce:
```
"The node type reference 'net.maxon.neutron.node.primitive.cube' is not associated with any IDs"
```

The lower-level `graph.AddChild(child_id, node_id, args)` DOES accept the
long-form canonical IDs:
```python
new_node = graph.AddChild("my_cube",
                           "net.maxon.neutron.node.primitive.cube",
                           maxon.DataDictionary())
```

Use `AddChild` for programmatic construction; reserve `ApplyDescription`
for cases where you have a verified `$type` label (see
`docs/scene_nodes_guide.md` and `data/verified_labels.json`).

ALSO: `ApplyDescription` is fundamentally a node-creation DSL — the top
level requires `$type`. It cannot mutate root's port list (you'll get
"Missing node type declaration" if you try to put port keys at the top
level).

---

## 42. `obj.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)` is required before `obj.GetNimbusRef()` for fresh SN Generators

**Wrong:** `obj.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes)` on a
freshly-created Scene Nodes Generator returns a usable handler.

**Actual:** on a freshly-inserted SN Generator, `GetNimbusRef` returns
`None` until you "wake up" the Nimbus subsystem with:
```python
obj.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)
handler = obj.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes)
```

Required ritual for any code path that creates an SN Generator + immediately
operates on its embedded graph. Maxon's own example
`associate_nodes_2026_2.py` shows this pattern.

---

## 41. `handler.GetDescID(port.GetPath())` is the canonical AM-exposure verifier

**Wrong:** to verify that a Scene Nodes port is exposed in the Attribute
Manager, walk the AM's parameter list looking for a matching name.

**Actual:** the Nimbus handler exposes a direct check:
```python
handler = obj.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes)
try:
    did = handler.GetDescID(port.GetPath())
    print(f"port is AM-exposed with DescID: {did}")
except Exception:
    print("port is internal/hidden — not in AM")
```

Built-in graph-context ports (`time`, `frame`, `nimbus`, `searchpaths`)
correctly throw — they're not user-facing. User-added or programmatically-
synthesized root ports return a valid 6-level DescID.

This pattern comes from Maxon's `associate_nodes_2026_2.py` example —
also shows `handler.FindOrCreateCorrespondingBaseList(node.GetPath())`
which returns the cinema-side `BaseList2D` surrogate.

---

## 40. Enumerate ALL attributes on a maxon GraphNode via `GetValues(0xFFFFFFFF)`

**Wrong:** `node.GetValue("some.attribute.id")` on string keys you guess
returns the value if present.

**Actual:** `GetValue(string_key)` returns `None` for almost every key on
a node, even keys that ARE set internally. Reason: most internal attributes
are keyed by `maxon.InternedId`, not string, and the binding lookup is
type-strict.

To enumerate ALL attributes set on a node:
```python
for (key, value) in port.GetValues(0xFFFFFFFF):  # uint32 mask, not Id!
    print(f"{key}: {value}")
```

The mask argument is a **uint32 bitflag** (use `0xFFFFFFFF` for all),
NOT a `maxon.Id` despite docstring suggesting so.

To read a specific attribute by its known key, use:
```python
v = port.GetStoredValue(maxon.InternedId("net.maxon.attribute.foo"))
```

Note `GetStoredValue` requires `InternedId` (not `Id` — different type).

If you want a DataDictionary's contents fully:
```python
ddata = port.GetStoredValue(maxon.InternedId("portDescriptionData"))
for (subkey, subval) in ddata:
    print(f"  {subkey}: {subval}")
```

---

## 39. `maxon.DataType.Get(<type>)` accepts `str`, NOT `Id` or `InternedId`

**Wrong:** `maxon.DataType.Get(maxon.Id("float64"))` to get the Float64
DataType.

**Actual:** errors with `"id must be str not <class 'maxon.data.Id'>"`.
This is unusual — most maxon getters accept `Id` or `InternedId`.

**Correct:**
```python
float_dt = maxon.DataType.Get("float64")        # plain str
int_dt   = maxon.DataType.Get("int64")
vec_dt   = maxon.DataType.Get("vector64")
bool_dt  = maxon.DataType.Get("bool")
str_dt   = maxon.DataType.Get("net.maxon.interface.string-C")
```

Note: even when you write `fixedtype` correctly via this path, it produces
a `maxon.DataType` (not the `NativePyDataType` the editor uses) — see
gotcha #46 for the implications.

---

## 38. Port description `unit` belongs in `portDescriptionData`, NOT `portDescriptionUi`

**Wrong:** "unit" is a UI concept (what unit to display), so it belongs
in `portDescriptionUi`.

**Actual:** the `net.maxon.description.ui.base.unit` Id key is stored
inside the `portDescriptionData` DataDictionary, not `portDescriptionUi`.
Despite "ui.base" being in the key name itself.

```python
ddata = maxon.DataDictionary()
ddata.Set(maxon.InternedId("net.maxon.description.ui.base.unit"),
          maxon.InternedId("meter"))
port.SetValue(maxon.InternedId("portDescriptionData"), ddata)  # NOT portDescriptionUi
```

Caveat: per gotcha #47, you usually don't need to set this at all — the
connection-based recipe handles type binding without explicit unit specs.
But if you DO write description metadata explicitly (e.g. for a port
without a downstream connection), the unit goes in the data dict.

---

## 1. `doc.GetNimbusRef()` is single-arg only

**Wrong:** `doc.GetNimbusRef(maxon.Id(sid), True)` with `create=True` to fetch-or-create.

**Actual (2026):** Method takes only 1 argument. Returns `None` if no graph at that space. **No auto-create option.**

**Fix:** Use the modern `maxon.frameworks.nodes.GraphDescription.GetGraph(host)` API instead — it auto-creates if missing.

```python
from maxon.frameworks.nodes import GraphDescription
graph = GraphDescription.GetGraph(doc)  # auto-creates doc-level scene nodes graph
```

## 2. `GraphDescription.CreateGraph()` is DEPRECATED (2025+)

**Wrong:** Calling `CreateGraph(target=doc, space=Id)`.

**Actual:** Deprecated since 2025; method's docstring explicitly says "Use `GetGraph` instead."

## 3. Restriction tag (`Trestriction`) param schema

**Wrong:** Setting `tag[c4d.RESTRICTION_VMAPS] = "vmap_name"` (constant doesn't exist).

**Actual (2026):** Restriction tag uses 12 paired slots:
- `RESTRICTIONTAG_NAME_01..12` (id 1100..1111) — vmap name (string)
- `RESTRICTIONTAG_VAL_01..12` (id 1200..1211) — enable flag (bool)

```python
rtag = c4d.BaseTag(c4d.Trestriction)
rtag[c4d.RESTRICTIONTAG_NAME_01] = "bend_mask"
rtag[c4d.RESTRICTIONTAG_VAL_01] = True
```

**Failure mode if wrong:** writes to non-existent param produce a malformed tag that downstream plugins (Greyscalegorilla Signal etc.) can crash on. Real ACCESS_VIOLATION crash dump caught this 2026-04-29.

## 4. `MCOMMAND_AXIS` does not exist in 2026

**Wrong:** `c4d.utils.SendModelingCommand(c4d.MCOMMAND_AXIS, ...)` for "Axis Center" recenter.

**Actual:** No `MCOMMAND_AXIS` constant in C4D 2026. The "Axis Center" tool is a `CommandData` plugin, NOT a `SendModelingCommand` op.

**Fix:** Implement axis recenter as pure math:
```python
pts = obj.GetAllPoints()
xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
c_local = c4d.Vector((min(xs)+max(xs))*0.5, (min(ys)+max(ys))*0.5, (min(zs)+max(zs))*0.5)
for i in range(obj.GetPointCount()):
    obj.SetPoint(i, obj.GetPoint(i) - c_local)
m = obj.GetMl()
m.off += m.v1*c_local.x + m.v2*c_local.y + m.v3*c_local.z
obj.SetMl(m)
```

## 5. BaseDraw shading constants

**Wrong:** `BASEDRAW_DATA_SDISPLAYMODE`, `BASEDRAW_DATA_LDISPLAYMODE`.

**Actual:** `BASEDRAW_DATA_SDISPLAYACTIVE` (also `_INACTIVE`), `BASEDRAW_DATA_WDISPLAYACTIVE`, `BASEDRAW_DATA_LINES_ON_SHADING_ACTIVE`.

**Mode values (`SDISPLAYACTIVE`):**
- 0 = gouraud
- 1 = gouraud_wire (built-in wire baked into shading)
- 2 = quick
- 3 = quick_wire
- 4 = flat_wire
- 5 = hidden_line
- 6 = noshading
- 7 = flat (faceted, no smooth normals)

## 6. Line overlay is EDITOR ONLY for screenshots

**Wrong:** `LINES_ON_SHADING_ACTIVE = True` + `WDISPLAYACTIVE = 0` (wireframe overlay) will render with wireframe in `viewport_screenshot`.

**Actual:** Editor-only setting. C4D's render pipeline (`RenderDocument`) ignores the line-overlay layer. Captured PNG has no wireframe.

**Fix:** Use the built-in `*_wire` SDISPLAY modes instead (1, 3, 4) — they bake the wireframe into the render path.

## 7. Plane primitive `PRIM_AXIS` values

`PRIM_AXIS=5` is **NOT** "+Y up" — it's "-Z facing." If you want Y-up, omit `PRIM_AXIS` or set to a different value (test the resulting GetMl orientation).

## 8. `inspect.getsource()` LIES; `dis.dis()` tells truth

When developing the C4D plugin (.pyp) and verifying whether a fix is loaded:

- `inspect.getsource(cls.method)` reads the SOURCE FILE on disk → may show your latest edits even when C4D is still running OLD bytecode.
- `dis.dis(cls.method)` reads the actual loaded bytecode → tells you what's REALLY running.

**Canonical check:**
```python
import dis, io, contextlib
f = io.StringIO()
with contextlib.redirect_stdout(f):
    dis.dis(cls.handle_yourthing)
loaded = f.getvalue()
print(f"new constant 'YOUR_FIX_TOKEN' in bytecode: {'YOUR_FIX_TOKEN' in loaded}")
```

If False → C4D needs a full restart. "Reload Python Plugins" doesn't re-import a running socket-server plugin's class. Stop→Start the socket server doesn't either.

## 9. Worker thread vs main thread for doc operations

C4D's MCP socket runs each client connection on a worker thread. Most doc operations work fine from worker threads, BUT:

**MUST be main-thread (verified by failures in recipe suite):**
- `doc.StartUndo()`, `doc.EndUndo()`, `doc.DoUndo()`, `doc.DoRedo()` — undo manager state. **Worker-thread call silently fails** (DoUndo returns True without doing anything; StartUndo never opens a group).
- `maxon.frameworks.nodes.GraphDescription.GetGraph(host)` — returns explicit error: `"GetGraph() must be run from the main thread"`. Both fetch + auto-create paths are main-thread-only.

**Fix:** Wrap in `execute_on_main_thread`:
```python
def _do():
    doc.StartUndo()
    return True
self.execute_on_main_thread(_do, _timeout=10)
```

**Apparently safe from worker thread (verified via recipe suite):**
- `obj.SetAbsPos`, `obj.SetMl`, `obj.SetPoint`
- `tag.SetAllHighlevelData`
- `obj.InsertObject`, `obj.InsertUnder`, `obj.InsertTag`
- `c4d.utils.SendModelingCommand` (most ops)

**Heuristic:** anything in the `maxon.frameworks.*` family (modern node-graph APIs) seems to require main thread. The classic `c4d.*` API is more permissive but still has hot spots like the undo manager.

If a tool starts misbehaving inexplicably, check whether wrapping in `execute_on_main_thread` fixes it.

## 10. Vertex map storage is Float32

**Wrong:** Asserting `vmap.weight == 0.42` after writing `0.42`.

**Actual:** C4D vertex maps store Float32 internally. Round-trip:
- Write `0.42` (Python double)
- Read back `0.41999998688697815` (Float32 quantization)

**Fix:** Use approximate equality (default tolerance 1e-5) for vmap weight comparisons. Strict equality is wrong.

## 11. `doc.GetAllNimbusRefs()` doesn't show modern Scene Nodes graphs

**Wrong:** Assuming any Scene Nodes graph in the doc shows up in `GetAllNimbusRefs()`.

**Actual:** That registry is the CLASSIC per-object/per-space NimbusRef list. The modern doc-level Scene Nodes graph (created via `GraphDescription.GetGraph()`) lives in the maxon model layer and does NOT appear there.

**Fix:** Probe BOTH registries when checking what graphs exist:
```python
all_refs = doc.GetAllNimbusRefs()
modern_doc_graph = None
try:
    modern_doc_graph = GraphDescription.GetGraph(doc)
except Exception:
    pass
```

## 12. `bmp.Save()` returns `1` not `0` on success

**Wrong:** `if bmp.Save(path, c4d.FILTER_PNG) != c4d.IMAGERESULT_OK: # error`

**Actual:** On C4D 2026 (at least with the PNG filter) `bmp.Save()` returns `1`, not `IMAGERESULT_OK` (`0`). The save SUCCEEDED — the return code is misleading.

**Fix:** Don't bail on `!= IMAGERESULT_OK`. Verify by checking the file exists on disk.

## 13. `SendModelingCommand(MAKEEDITABLE)` removes source from doc

**Wrong:** `result = SendModelingCommand(MAKEEDITABLE, [generator]); doc.InsertObject(result[0])` AFTER manually removing the generator.

**Actual:**
1. The generator IS removed from the doc by SendModelingCommand itself.
2. The returned polygon object is NOT yet in the doc — you MUST `doc.InsertObject(poly)`.
3. Manual `generator.Remove()` after the command is a no-op (already removed).

**Canonical pattern:**
```python
generator = c4d.BaseObject(c4d.Ocube)
doc.InsertObject(generator)
result = c4d.utils.SendModelingCommand(c4d.MCOMMAND_MAKEEDITABLE, [generator], doc=doc)
poly = result[0]
poly.SetName("MyPoly")
doc.InsertObject(poly)  # REQUIRED — result is orphan otherwise
# generator is already gone; no Remove() needed
```

## 14. WSL paths must be Windows-converted before sending to C4D

**Wrong:** Sending `mnt/c/Users/.../foo.png` as a `save_path` argument from WSL.

**Actual:** C4D's Python interpreter runs on Windows. `mnt/c/...` is a WSL-mount path that Windows Python's `open()` can't resolve.

**Fix (server.py side):** Auto-translate `mnt/<drive>/...` to `<DRIVE>:\\...` before sending the command. Already implemented in `_normalize_paths_in_command` — applied to known path-arg keys (`file_path`, `save_path`, `save_dir`, `bitmap_path`, `path`, etc.).

## 18. `FieldList.SampleListSimple` returns FieldOutput (don't pre-create)

**Wrong:** `flist.SampleListSimple(host, FieldInput, pre_built_FieldOutput)` — errors with `'FieldOutput' object cannot be interpreted as an integer`.

**Actual (2026):** `field_output = flist.SampleListSimple(host, FieldInput, flags_int)` — returns a NEW FieldOutput. The 3rd arg is a `FIELDSAMPLE_FLAG_*` int, NOT a pre-built FieldOutput. Don't construct one yourself.

```python
flist = c4d.FieldList()
layer = c4d.modules.mograph.FieldLayer(c4d.FLfield)
layer.SetLinkedObject(field_obj)
flist.InsertLayer(layer)
inputs = c4d.modules.mograph.FieldInput(positions, n)
output = flist.SampleListSimple(host_obj, inputs, c4d.FIELDSAMPLE_FLAG_VALUE)
weights = [output.GetValue(i) for i in range(n)]
```

The signature is `(BaseList2D, FieldInput, int) -> FieldOutput`. Took several probe iterations to land on this — the docstring just says "Sample a FieldList with simpler parameters" with no signature hint.

## 17. DescriptionResource IDs vs Shader Plugin IDs (don't confuse)

**Wrong:** `c4d.BaseShader(c4d.DESCRIPTIONRESOURCE_OSLTEXTURE)` to instantiate Octane's OSL texture (804752314 — description resource id, NOT a plugin id).

**Actual:** Calling `BaseShader()` with a description-resource ID hangs C4D (no plugin matches → infinite wait somewhere). The actual plugin id has to come from `c4d.plugins.FilterPluginList(c4d.PLUGINTYPE_SHADER, True)` — for Octane's "OSL texture" it's `1039813`.

**Heuristic:** any constant named `DESCRIPTIONRESOURCE_*` is for editing/UI registration of a description resource. Plugin instantiation needs the PLUGIN ID from `FilterPluginList`. They're different namespaces with similar-looking integer IDs.

```python
from c4d.plugins import FilterPluginList
shaders = FilterPluginList(c4d.PLUGINTYPE_SHADER, True)
osl_plugin_id = next((p.GetID() for p in shaders if p.GetName() == "OSL texture"), None)
shader = c4d.BaseShader(osl_plugin_id)  # works
```

## 16. `FieldList` is at top-level `c4d`, NOT in `c4d.modules.mograph`

**Wrong:** `from c4d.modules import mograph; flist = mograph.FieldList()`

**Actual (2026):** `c4d.FieldList()` — top-level. The OTHER field helpers
ARE in `c4d.modules.mograph`:
- `c4d.modules.mograph.FieldLayer`
- `c4d.modules.mograph.FieldInput`
- `c4d.modules.mograph.FieldOutput`
- `c4d.modules.mograph.FieldInfo`

Confusing split — be explicit about each import.

```python
flist = c4d.FieldList()  # top-level
from c4d.modules.mograph import FieldLayer, FieldInput, FieldOutput, FieldInfo
```

Field-layer subtype constants (also top-level): `c4d.FLfield`,
`c4d.FLnoise`, `c4d.FLformula`, `c4d.FLcurve`, `c4d.FLremap`, etc.

## 15. `bmp.GetPixel()` length varies

**Wrong:** Always assuming `(r, g, b) = px`.

**Actual:** Can be 3-tuple (RGB) or 4-tuple (RGBA) depending on bitmap color mode.

**Fix:** Check `if px and len(px) >= 3` before unpacking.

---

## 19. Capsule plugin IDs are the entry point for asset-ID discovery

**Context:** C4D 2026 buries Scene Nodes asset IDs (the strings you need
to programmatically create graph nodes via `GraphDescription.ApplyDescription`)
behind every Capsule object. The Asset Browser is full of them (Primitive
▶ Cube, Modifier ▶ Bevel, etc.) — each one is a classic-object-shaped
wrapper around a Scene Nodes graph.

**Wrong:** Trying to enumerate available Scene Nodes asset IDs via the
maxon SDK alone — `maxon.AssetInterface.GetUserPrefsRepository().FindAssets`
has shifting signatures and doesn't reliably return scene-nodes assets in
a usable form.

**Fix:** Walk existing capsules via `GraphDescription.GetGraph(obj)` +
recursive `GetChildren()` and collect every node's `GetId()`. The
canonical capsule plugin IDs to scan for in a doc:

```
5171      = Capsule
180420400 = Scene Nodes Deformer
180420500/600/700 = Scene Nodes Generator (3 variants)
440000274 = Capsule Field
1057221   = Simulation Scene
```

The cinema4d-mcp `scene_nodes_dissect_capsule` handler implements this
pattern and caches discovered IDs into a session-level registry.

**Caveat:** `GraphDescription.GetGraph` MUST run on the main thread.
From a worker thread it errors with "GetGraph() must be run from the
main thread" (same constraint as undo / maxon.frameworks.* APIs).

---

## 20. `node.GetValue()` requires `maxon.InternedId`, not `maxon.Id`

`GraphNode.GetValue(attribute_id)` reads a node attribute (e.g. a Floating
IO's `attribute.direction`). Passing `maxon.Id("net.maxon.node.floatingio.attribute.direction")`
fails with `unable to convert builtins.NativePyData to @net.maxon.datatype.internedid`.
The fix is `maxon.InternedId(...)`.

```python
# WRONG:
v = node.GetValue(maxon.Id("net.maxon.node.floatingio.attribute.direction"))

# RIGHT:
v = node.GetValue(maxon.InternedId("net.maxon.node.floatingio.attribute.direction"))
```

`InternedId` is the canonical attribute-key type. `Id` is for asset/object
identifiers. Different namespaces internally — they don't auto-coerce.

---

## 21. `port.Connect()` can SILENTLY NO-OP on void-template ports

The Scene Nodes imperative API (`graph.BeginTransaction() → src.Connect(dst) → txn.Commit()`)
works for typed-port-to-typed-port wires. But for **void-template ports** —
notably `net.maxon.node.floatingio.portlist` — `Connect()` returns no error
*and* the transaction commits cleanly, but **no wire actually lands**. The
C4D editor uses a higher-level auto-port-specialization on drag-wire that
isn't exposed in the Python imperative API.

**Always verify after commit:**

```python
src_port_obj.Connect(dst_port_obj)
txn.Commit()

dst_id = str(dst_port_obj.GetId())
landed = any(str(p.GetId()) == dst_id
             for (p, _wires) in src_port_obj.GetConnections(1))
if not landed:
    # Connect silently no-oped — likely a void-template port.
    raise RuntimeError("wire did not land after commit")
```

The `cinema4d-mcp` `scene_nodes_connect_ports` handler does this verification
post-commit and returns `ok=false` with a diagnostic if the wire didn't land.

---

## 22. `graph.AddPorts(parent, idx, count)` needs VARIADIC_TEMPLATE on the parent

Python wraps the plural form `AddPorts(parent, index, count)` (count-based,
adds N numbered slots). It fails with `Illegal argument: Condition variadic &
PORT_FLAGS::VARIADIC_TEMPLATE not fulfilled` when the parent port doesn't
have the variadic-template flag. Floating IO nodes and PORTLIST ports do
NOT satisfy this.

The C++ singular form at `frameworks/graph.framework/source/maxon/graph.h:891`:
```cpp
MAXON_METHOD Result<GraphNode> AddPort(const GraphNode& parent, const Id& name);
```
is the API the C4D editor actually uses for named-port creation — but it's
**not exposed in Python**. Wrap it in a C++ shim plugin if needed.

---

## 23. `AssetCreationInterface.CreateObjectAsset` works programmatically

`maxon.AssetCreationInterface.CreateObjectAsset` is fully exposed in Python
in C4D 2026 (verified 2026-04-30). Saves a `BaseObject` + its embedded graph
as a `net.maxon.assettype.file` asset (`.c4d` format). Bit-identical
round-trip via `AssetManagerInterface.LoadAssets`.

Signature (from docstring):
```python
desc = maxon.AssetCreationInterface.CreateObjectAsset(
    op,                              # BaseObject
    activeDoc,                       # BaseDocument
    storeAssetStruct,                # maxon.StoreAssetStruct
    assetId,                         # maxon.Id (empty -> auto)
    assetName,                       # str
    assetVersion,                    # str
    copyMetaData,                    # maxon.AssetMetaData
    addAssetsIfNotInThisRepository,  # bool
)
# returns maxon.AssetDescription
```

`StoreAssetStruct` constructor takes 3 args: `parentCategory` (must be
`maxon.Id` or string-convertible to Id, NOT `InternedId`), `lookupRepo`,
`saveRepo`. Get the user prefs repo via
`maxon.AssetInterface.GetUserPrefsRepository()`.

```python
repo = maxon.AssetInterface.GetUserPrefsRepository()
sas  = maxon.StoreAssetStruct(
    maxon.Id("net.maxon.assetcategory.uncategorized"),
    repo, repo)
desc = maxon.AssetCreationInterface.CreateObjectAsset(
    obj, doc, sas, maxon.Id(), "MyAsset", "1.0",
    maxon.AssetMetaData(), True)
```

To reload: `maxon.AssetManagerInterface.LoadAssets(repo, [(asset_id, "")], None, None)`
returns True on success and inserts the asset's content into the active doc.

**Caveat:** `CreateObjectAsset` produces `net.maxon.assettype.file`, NOT
`net.maxon.node.assettype.nodetemplate`. Maxon's shipped capsules
(Edge to Spline, Random Selection, etc.) are NodeTemplate-typed (`.c4dnodes`
format) — and that asset type is NOT exposed in Python. NodeTemplate
publishing requires C++.

---

## 24. Asset type registry — `maxon.AssetTypes` enumeration

`maxon.AssetTypes` is a registry exposing 50+ asset type declarations. The
ones relevant for graph/capsule work:

| `AssetTypes.X()` returns | Type ID |
|---|---|
| `File` | `net.maxon.assettype.file` (generic .c4d wrapper) |
| `NodeTemplate` | `net.maxon.node.assettype.nodetemplate` (Scene Nodes capsule .c4dnodes) |
| `NodeContext` | `net.maxon.assettype.nodecontext` |
| `NodeSpace` | `net.maxon.class.datalessassettype` |
| `NodeDescription` | `net.maxon.node.assettype.nodedescription` |
| `NodeDefaultsPreset` | `net.maxon.assettype.preset.defaults.node` |
| `DocumentPreset` | `net.maxon.assettype.preset.document` |
| `UserDataPreset` | `net.maxon.assettype.preset.userdata` |

Use these as the type filter for `repo.FindAssets(type_id, asset_id, version, mode)`.
637 NodeTemplate assets ship in a vanilla install of C4D 2026.

---

## 28. `cinema::String` and `maxon::String` are different types — convert via `MaxonConvert`

C++ plugins routinely mix two string types: `cinema::String` (the older C4D
string used by `BaseContainer`, `BaseObject`, etc.) and `maxon::String`
(modern, used by the maxon framework — graphs, assets, ids). They CANNOT
be concatenated with `+` directly. The compiler error looks like:
```
error C2666: 'cinema::operator +': overloaded functions have similar conversions
while trying to match the argument list '(cinema::String, const maxon::String)'
```

This commonly bites when building error messages — a `BaseContainer::GetString`
returns `cinema::String`, but the literal `"text"_s` resolves to
`maxon::String` (because `maxon::operator""_s` is in scope from any maxon
header include).

**Conversion functions** (in `c4d_string.h`):
```cpp
inline const String& MaxonConvert(const maxon::String& val);  / maxon -> cinema
inline String MaxonConvert(maxon::String&& val);
inline const maxon::String& MaxonConvert(const String& val);  / cinema -> maxon
inline maxon::String MaxonConvert(String&& val);
```

**Idiom:** build error/diagnostic messages in `maxon::String` (since `_s`
literals produce that), then convert ONCE at the BaseContainer boundary:
```cpp
maxon::String msg = "graph_target '"_s + MaxonConvert(targetName) + "' not found"_s;
wc->SetString(BC_KEY_STATUS_MSG, MaxonConvert(msg));
```

---

## 29. `iferr_scope` (no `_handler`) + impl-returns-Result is the clean Maxon idiom

The Maxon error system uses `iferr_return` to bail out of a function with a
`Result<>` return type. To use it in a function that returns a non-Result
type (like `Int32` or `void`), you have two options:

**Option A (preferred): wrap the impl as `Result<void>`, return early via
`iferr_return`, catch at the caller via `iferr (call) { ... }`.** This is
what every Maxon SDK example does:

```cpp
static maxon::Result<void> DoWork_Impl(BaseContainer* wc) {
    iferr_scope;  / Scope marker — required for iferr_return to work.
    SomeMaxonCall() iferr_return;
    return maxon::OK;
}

static Int32 DoWork(BaseContainer* wc) {
    iferr (DoWork_Impl(wc)) {
        wc->SetString(KEY_ERR, MaxonConvert(err.GetMessage()));
        return 1;
    }
    return 0;
}
```

**Option B (don't): `iferr_scope_handler` does NOT exist.** Despite what
auto-complete/AI may suggest, the macro is `iferr_scope` (no suffix). The
"_handler" form will compile-fail with cryptic messages.

---

## 30. `NodesGraphModelRef` lacks `GetRoot` — use `GetViewRoot()`

For walking an existing Scene Nodes graph in C++:
```cpp
maxon::NimbusBaseRef nimbus = host->GetNimbusRef(maxon::neutron::NODESPACE);
const maxon::nodes::NodesGraphModelRef& graph = nimbus.GetGraph();
maxon::GraphNode root = graph.GetViewRoot();  / NOT GetRoot()
```

`graph.GetViewRoot()` returns the root GraphNode at `GetViewRootPath()`. If
you need recursive traversal, use `GetInnerNodes`:
```cpp
graph.GetInnerNodes(root, maxon::NODE_KIND::NODE, false,
    [&](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool>
    {
        / process candidate; return true to continue iteration
        return maxon::Bool(true);
    }) iferr_return;
```

`maxon::neutron::NODESPACE` lives in `maxon/neutron_ids.h` — must include
that header AND list `neutron.framework` in the project's APIS.

---

## 32. `maxon::String` has `Find` not `FindFirst`; `GetInnerNodes` is on `GraphNode` not on the graph

Two API-shape traps in one. Both bit me on the second compile attempt of
the cinema4d-mcp helper plugin (2026-04-30):

**(a) `maxon::String::FindFirst` doesn't exist.** The available methods
(c4d 2026 SDK):
```
Bool Find(const REFTYPE& str, Int* pos, StringPosition start = 0)
Bool Find(CHARTYPE ch, Int* pos, StringPosition start = 0)
Bool FindLast(const REFTYPE& str, Int* pos, StringPosition start = StringEnd())
Bool FindLast(CHARTYPE ch, Int* pos, StringPosition start = StringEnd())
Int  FindIndex(...)        / returns -1 if not found, vs Bool result
Int  FindLastIndex(...)
```
Use `Find` (no "First"). All methods take an output position pointer or
return an Int index.

**(b) `GetInnerNodes` is a method on `GraphNode`, not on `NodesGraphModelRef`.**
The pattern is:
```cpp
maxon::GraphNode root = graph.GetViewRoot();
root.GetInnerNodes(maxon::NODE_KIND::NODE, /*includeThis=*/false,
    [&](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool> {
        / process candidate
        return maxon::Bool(true); / continue
    }) iferr_return;
```
Same applies to `GetChildren` — both are on the GraphNode, with the underlying
implementation forwarded to the graph internally.

---

## 37. NodeTemplate-typed asset creation is not exposed in Python — C++ only

After exhaustive probing of the C4D 2026 Python surface (verified live
2026-04-30 evening, cinema4d-mcp), creating a `net.maxon.node.assettype.
nodetemplate`-typed asset (`.c4dnodes` format — what Edge to Spline /
Random Selection ship as) is unreachable from Python:

**Asset commands open modal dialogs:**
- `c4d.CallCommand(465002339)` "Convert To Asset..." → opens modal,
  blocks main thread until user input. Script timeout at 30s.
- `c4d.CallCommand(200001023)` "Save as New Asset..." → same pattern.
  The "..." in command names indicates dialog-style commands.

**`OPENSAVEASSETDIALOGFLAGS` has no `NO_UI` / `BATCH` flag:**
```
ALLOW_EDIT_ID, ALLOW_EDIT_NAME, ALLOW_EMPTY_CATEGORY, HIDE_AI_BUTTON,
NONE, SHOW_MAKE_DEFAULT, SHOW_VERSION
```
Only UI-control toggles. Dialog can't be suppressed.

**`AssetCreationInterface` exposes 32 methods, NONE produce NodeTemplate:**
```
AddPreviewRenderAsset, BrowseDescriptionForDefaults, CheckObjectsOnDrop,
CreateMaterialAsset, CreateMaterialsOnDrag, CreateObjectAsset,
CreateObjectsOnDrag, CreateSceneAsset, GenerateImagePreview,
GenerateScenePreviewImage, GetAddDependencyDelegate, GetClass,
GetDefaultObject, GetDefaultSettings, GetHashCodeImpl,
GetNewAssetIdFromIdAndVersion, OpenSaveAssetDialog, RenderDocumentAsset,
SaveActiveDocumentAsNewVersion, SaveBaseDocumentAsAsset,
SaveBrowserPreset, SaveDefaultPresetFromObject, SaveDocumentAsset,
SaveMemFileAsAsset, SaveMemFileAsAssetAlone,
SaveMemFileAsAssetWithCopyAsset, SaveMetaDataForAsset,
SaveTextureAsset, SetDefaultObject, SupportDefaultPresets,
UpdateMetaData, UpdateSubtypeAndMetaData
```
All `Save*` / `Create*` hardcode the produced asset to `File`-type
(`net.maxon.assettype.file`, `.c4d` format). The `subType` parameter
on some of them refers to ASSET SUBTYPE (Object/Material/Scene), not
ASSET TYPE.

**No `RegisterNodeTemplate` / `CreateNodeTemplate` / `PublishNode`** in
the `maxon.*` namespace.

**`maxon.nodes.BuiltinNodes`** (the registry the C++ side uses for
NodeTemplate registration) evaluates to `None` from Python — not
accessible at runtime.

**`NodeTemplate*` Python symbols:** only `NodeTemplateBaseClass` and
`NodeTemplateDecoratorBaseClass` exist — these are C++ inheritance
markers (`MAXON_COMPONENT(NORMAL, NodeTemplateBaseClass)`), not runtime
factories.

**Right-click context-menu commands (Add Input, Add Output, Toggle
Node Type, Add User Data, Add Children) do NOT have global command
IDs** — `find_command_by_name` returns 0 for all of them. They're
context-menu-only operations not exposed via `CallCommand`.

**Conclusion:** the only way to publish a NodeTemplate-typed asset
programmatically in C4D 2026 is C++:
1. Inherit a class from `maxon::Component<MyClass, NodeTemplateInterface>`
2. Implement `InstantiateImpl` building the structure via
   `maxon::nodes::MutableRoot.GetInputs().AddPort(id).SetType<T>()`
3. Register at static-init time via
   `MAXON_DECLARATION_REGISTER(maxon::nodes::BuiltinNodes, ...)`

Reference: `plugins/example.nodes/source/space/dynamic_node_impl.cpp`.

The cinema4d-mcp helper plugin (proven Phase A.0/A.1 bridge) can host
this — see `docs/cpp_shim_design.md` Phase B.

---

## 36. Runtime `AddPort` on a FloatingIO is NOT supported — must define at NodeTemplate-build-time

After 4 attempts (2026-04-30 cinema4d-mcp Phase A.1), all runtime
`AddPort` variants targeting a FloatingIO instance fail:

| Attempt | Error |
|---|---|
| `fio.AddPort(portId)` (FIO node directly) | `"You can't add a port directly to node Root."` |
| `fio.GetInputs().AddPort(portId)` | `"PrivateIsNodeAFloatingIo(trueNode) not fulfilled."` |
| `graph.AddPorts(fio, idx, count)` | `"VARIADIC_TEMPLATE not fulfilled."` |
| `graph.AddPorts(portlist_port, idx, count)` | `"VARIADIC_TEMPLATE not fulfilled."` |

The walker DID find the correct FIO node (debug-trail confirmed via
`BC_KEY_DEBUG`: `candidates=[floatingio@HASH(MATCH), context_externaltimeinput, context_notime]`).
The runtime C++ AddPort implementation rejects FloatingIO targets.

**FloatingIO is marked ` INTERNAL.`** in
`frameworks/nodes.framework/source/maxon/definitions/nodes_utility.h`.
Adding ports to a FIO is a NodeTemplate-build-time operation, NOT a
runtime graph-edit operation.

**The SDK pattern that DOES work for adding named ports** lives in
`plugins/example.nodes/source/space/dynamic_node_impl.cpp`:
```cpp
/ During NodeTemplate definition (template-build-time):
maxon::nodes::MutableRoot root = parent.CreateNodeSystem() iferr_return;
maxon::nodes::MutablePort outPort = root.GetOutputs().AddPort(NODE::DYNAMIC::RESULT) iferr_return;
outPort.SetType<maxon::Color>() iferr_return;
```

Note `MutableRoot` / `MutablePort` are different types from runtime
`GraphNode`. They're for asset-creation, not graph-mutation.

**Implication for "user-tunable capsule with AM params" workflows:** the
actual path is `Phase B` — build a complete NodeTemplate definition with
FIOs+ports baked in via `MutableRoot`, save as a NodeTemplate-typed
`.c4dnodes` asset (not `.c4d` File asset — see gotcha #23). The asset's
FIOs surface as AM params when an instance is dragged in.

The C4D editor's drag-wire UX likely reaches this via an even
higher-level operation (instantiate a new NodeTemplate from the existing
FIO's definition + the new desired port; replace the in-place FIO).

---

## 35. `SpecialEventAdd` is ASYNC — handler fires after caller returns

Continuation of gotcha #34. `SpecialEventAdd` queues a CoreMessage to be
broadcast on the main thread, but **doesn't dispatch synchronously**. The
caller's stack frame must finish before the message thread (which IS the
main thread) can pick up the queue and fire the C++ `CoreMessage`
handlers.

Practical implication for Python ↔ C++ request/response patterns:

```python
# WRONG: write+fire+read on main thread — handler never sees the request
def _do_all_on_main():
    wc.SetInt32(KEY_OP, 1)        # write
    wc.SetInt32(KEY_STATUS, -1)
    c4d.SpecialEventAdd(PID, 1, 0)  # fire (queued)
    return wc.GetInt32(KEY_STATUS)  # reads -1, queue hasn't drained
self.execute_on_main_thread(_do_all_on_main)  # main thread blocked all the while
```

```python
# RIGHT: split — fire on main thread, worker yields, then read on main thread
def _write_and_fire():
    wc.SetInt32(KEY_OP, 1)
    wc.SetInt32(KEY_STATUS, -1)
    c4d.SpecialEventAdd(PID, 1, 0)
self.execute_on_main_thread(_write_and_fire)  # quick — main thread freed

# Worker thread sleeps, main thread processes queue
import time
deadline = time.time() + 5.0
while time.time() < deadline:
    s = self.execute_on_main_thread(lambda: wc.GetInt32(KEY_STATUS))
    if s != -1:
        return s
    time.sleep(0.05)
```

The mcp-socket worker thread is naturally separate from the C4D main
thread, so this poll-pattern works cleanly. **Calling from the main
thread directly will time out forever.** Even `time.sleep` on the main
thread doesn't yield to the message queue (sleep blocks the thread; the
message thread can't pick up the queue without the same thread releasing).

---

## 34. Python -> C++ messaging: `SpecialEventAdd` works, `SendCoreMessage` (custom IDs) and `BasePlugin.Message` do NOT

Real-world bridge for Python ↔ C++ messaging in C4D 2026 (verified live
2026-04-30):

| Python call | Reaches MessageData::CoreMessage? |
|---|---|
| `c4d.SendCoreMessage(BUILTIN_ID, bc)` (e.g. `EVMSG_CHANGE`) | ✅ yes |
| `c4d.SendCoreMessage(custom_id, bc)` | ❌ silently dropped |
| `BasePlugin.Message(msg_id, data)` | ❌ returns True but doesn't route |
| **`c4d.SpecialEventAdd(plugin_id, p1, p2)`** | ✅ **YES — use this** |

**`SpecialEventAdd(plugin_id, p1, p2)` is the only Python -> C++ bridge
that actually fires `MessageData::CoreMessage` for custom message routing.**
It packs:
- `plugin_id` at `BFM_CORE_ID` ('MciI') in the BC
- `p1` at `BFM_CORE_PAR1` ('Mci1')
- `p2` at `BFM_CORE_PAR2` ('Mci2')

C++ filters by checking the BC, NOT by the `id` parameter:
```cpp
virtual Bool CoreMessage(Int32 id, const BaseContainer& bc) override
{
    if (bc.GetInt32(BFM_CORE_ID) != MY_PLUGIN_ID)
        return true;  / not addressed to us
    Int32 op = bc.GetInt32(BFM_CORE_PAR1);
    / ... process op ...
}
```

For complex args/results that don't fit in two UInts, pair `SpecialEventAdd`
with `c4d.GetWorldContainerInstance()` shared state (Python writes args
into BC keys, calls `SpecialEventAdd`, reads results from same BC keys
after — the call is synchronous on the main thread).

**Why `SendCoreMessage` doesn't broadcast custom IDs:** the docstring
calls them "core messages" but only the predefined `EVMSG_*` and
`COREMSG_*` constants are actually broadcast. Custom IDs are filtered by
C4D's internal dispatcher.

**Why `BasePlugin.Message` returns True without routing:** the Python
`Message()` wrapper exists for `BaseList2D.Message()` (the base class
method), which is for sending messages to scene objects (NodeData
overrides). `MessageData` doesn't override `Message()` — only
`CoreMessage()` — so the call no-ops at the plugin instance level.

---

## 33. Use `iferr (decl = expr) { return err; }` block pattern, not `IsError()`

`Result<T>` has NO `IsError()` method. Despite intuition, the API uses
comparison operators (`== maxon::OK`, `== maxon::FAILED`) or — more
canonically — the `iferr` block macro that auto-binds an `err` variable.

The chained `Type x = expr iferr_return;` pattern works for many Maxon
APIs but breaks for `GraphNode`-returning template methods (`GetInputs`,
`GetOutputs`, `AddPort`, etc.) because the SFINAEHelper template return
type doesn't always unwrap cleanly to the declared LHS type. Compile
error:
```
error C2440: 'initializing': cannot convert from 'maxon::Result<maxon::GraphNode>' to 'maxon::GraphNode'
```

**Canonical pattern — `iferr` block (verified from
plugins/example.nodes/source/space/nodesystem_presethandler.cpp:81):**
```cpp
iferr (maxon::nodes::Port port = maxon::nodes::ToPort(node))
{
    return err;  / 'err' auto-bound by the iferr macro to the maxon::Error
}
/ 'port' is in scope here as the unwrapped Port value
```

**Applied to GraphNode-returning template methods:**
```cpp
/ Get a port container — won't unwrap with iferr_return chain
maxon::GraphNode container;
{
    iferr (maxon::GraphNode tmp = fio.GetInputs())  / or GetOutputs()
    {
        return err;
    }
    container = tmp;  / 'tmp' is the unwrapped value here
}

/ AddPort same shape:
maxon::GraphNode newPort;
{
    iferr (maxon::GraphNode added = container.AddPort(portId))
    {
        return err;
    }
    newPort = added;
}
```

The trailing `iferr_return` form still works fine for non-template
returns: `BeginTransaction()`, `Init()`, `Commit()`, etc. Reach for the
`iferr` block specifically when `iferr_return` fights the compiler.

`Result<T>` checking via comparison if you need it without the macro:
```cpp
if (result == maxon::FAILED) return result.GetError();
T value = result.GetValue();
```
But the `iferr` block is cleaner and idiomatic.

---

## 31. `AddPort` on a Floating IO must be called on the FIO node DIRECTLY (not on GetInputs/GetOutputs)

**Corrected 2026-04-30** after live error feedback from the C++ runtime:
```
Illegal argument: Condition PrivateIsNodeAFloatingIo(trueNode) not fulfilled.
```

The `AddPort` implementation explicitly checks that the parent is a
FloatingIO node. Passing the FIO's `GetInputs()` or `GetOutputs()`
container fails this check.

**Correct usage:**
```cpp
maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;

maxon::Id portId;
portId.Init(MaxonConvert(portName)) iferr_return;

/ Call AddPort ON THE FIO NODE (not on GetInputs()):
maxon::GraphNode newPort;
{
    iferr (maxon::GraphNode added = fio.AddPort(portId))
    {
        return err;
    }
    newPort = added;
}

txn.Commit() iferr_return;
```

The hidden+visible port pair (`hiddenin1.<path>` + `in1.<path>`) is
created automatically by `AddPort` when the parent is a FIO. Input vs
output direction is controlled separately by the FIO's
`net.maxon.node.floatingio.attribute.direction` Bool node-attribute,
set via `node.SetValue(maxon::InternedId(...), value)`.

**For non-FIO graph nodes:** `AddPort` would have to be called on the
node itself too — the same `PrivateIsNodeA<NodeType>` check applies per
template. The FIO error message is the most informative because it names
the expected node type explicitly.

---

## 27. C4D 2026 Windows SDK produces `.xdl64`, not `.cdl64`

The Maxon SDK convention has historically used `.cdl64` for Windows
plugin extensions and `.xdl64` for macOS. **In C4D 2026's Windows SDK
this is reversed**: Visual Studio builds produce `.xdl64` files (verified
2026-04-30 with the cinema4d-mcp helper plugin built via
`cmake --build . --config Release`).

C4D 2026 loads `.xdl64` files from the Windows plugins directory just
fine — install pattern is:
```
%APPDATA%/Maxon/Maxon Cinema 4D 2026_<HASH>/plugins/<plugin_name>/<plugin_name>.xdl64
```

When writing build/install scripts, search for both extensions and pick
whichever the build produced — don't hardcode `.cdl64`. Example
(`scripts/build_cpp_shim.sh`):
```bash
for ext in xdl64 cdl64; do
    candidate=$(find "$SDK/_build_v143" -type f -name "$plugin.$ext" 2>/dev/null | head -1)
    if [ -n "$candidate" ]; then break; fi
done
```

The exact build output path under the user's setup was:
```
C4D_2026_SDK/_build_v143/bin/Release/plugins/<plugin_name>/<plugin_name>.xdl64
```
(`bin/Release/plugins/<name>/` is deeper than expected — must use
recursive `find`, not a hardcoded path).

---

## 26. `PLUGINTYPE_MESSAGEDATA` doesn't exist — use `PLUGINTYPE_COREMESSAGE`

C++ plugins registered via `RegisterMessagePlugin(...)` (with a
`MessageData`-derived dispatcher class) are looked up from Python via
`c4d.plugins.FindPlugin(plugin_id, c4d.PLUGINTYPE_COREMESSAGE)` — value
`17`. There is **no** `c4d.PLUGINTYPE_MESSAGEDATA` constant despite the
C++-side class being called `MessageData`. The naming mismatch is a
classic Maxon trap.

Available `PLUGINTYPE_*` constants (verified C4D 2026.2):
```
ANY=0  SHADER=1  MATERIAL=2  COMMAND=4  OBJECT=5  TAG=6  BITMAPFILTER=7
VIDEOPOST=8  TOOL=9  SCENEHOOK=10  NODE=11  LIBRARY=12  BITMAPLOADER=13
BITMAPSAVER=14  SCENELOADER=15  SCENESAVER=16  COREMESSAGE=17
CUSTOMGUI=18  CUSTOMDATATYPE=19  RESOURCEDATATYPE=20
MANAGERINFORMATION=21  CTRACK=32  FALLOFF=33  VMAPTRANSFER=34  PREFS=35
SNAP=36  FIELDLAYER=37  DESCRIPTION=38
```

If you're discovering a C++ plugin you registered yourself, use
`PLUGINTYPE_COREMESSAGE` for MessageData-style registrations and
`PLUGINTYPE_COMMAND` for `RegisterCommandPlugin` registrations.

---

## 25. Scene Nodes 777 DescID root is editor metadata, NOT user AM params

The cinema4d-mcp project initially treated DescIDs under root 777 as the
"Scene Nodes Attribute Manager namespace." This is wrong. After 3 rounds
of probing across different inner graph configurations (bare empty, Memory
+ FloatingIO, Edge to Spline with 5 inner FIOs), the 777 tree was always
the **same 12 entries** — Scene Nodes editor metadata (group folders +
filter tags + node category + a fixed Maxon placeholder hash). The hash
`BrM5f_dgHBXvK6gQuZ3cQA` LOOKS per-instance but is identical across all
SN Generators.

User-facing AM params live under capsule-asset-specific roots (e.g. spline
generators surface params at roots 1000-1005, 4000 from the SplineObject
base class). FIO-routed params surface as AM params **only when the inner
graph is registered as a NodeTemplate-typed asset** — see gotcha #23.

---

## Discovery process

This list grows organically. Whenever runtime contradicts an API
expectation, it gets logged here. The cinema4d-mcp project's contract
tests + recipe suite catch most of these on first run, which is
exactly why those exist.

If you're building against C4D 2026 and hit something that contradicts
the C4D Python docs, please open an issue or PR with the discovery —
keeping this list current saves everyone time.
