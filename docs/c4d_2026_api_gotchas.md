# C4D 2026 API Gotchas — discoveries from MCP development

A reference sheet of things the C4D 2026 Python API does that don't match
older docs / tribal knowledge / what felt right. Each entry has the wrong
assumption, the actual behavior, and how it was discovered.

Maintained as bugs surface during MCP plugin development. Useful for
anyone building agent integrations against C4D 2026.

Entries preserve historical discoveries, including superseded recipes. Match
their version and evidence to the task; check the project's pinned SDK. For
normal deployment prefer the saved-scene quit/replace/relaunch workflow in
#130 over #124's loaded-file rename mechanism. For current managed-spline UI
refresh, read #132–133 together with #125 and #131.

---

## 124. Hot-swapping a compiled plugin (`.xdl64`) while C4D is running — rename the loaded file to a NON-`.xdl64` stash, then copy the new build in; you can't delete the stash until C4D unloads

**Discovered 2026-07-15** iterating a C++ deformer with C4D open. Compiled plugins load once at startup, so a rebuild needs the binary replaced then C4D relaunched — but the OS has the running `.xdl64` memory-mapped and a plain `cp` over it fails/partially-writes.

**Wrong assumption:** you must close C4D before touching the plugin binary.
**Actual behavior (Windows):** you can *rename* a memory-mapped DLL/plugin even while it's loaded; you just can't overwrite or delete it in place. So: `mv Plugin.xdl64 Plugin.stash<timestamp>` (the running instance keeps executing from the renamed file), then `cp NewBuild.xdl64 Plugin.xdl64`. The relaunch loads the fresh one.

**Two traps:**
1. **The stash name must NOT end in `.xdl64`** — C4D scans the plugins tree for `*.xdl64` at startup and would try to load your stash as a second copy (duplicate registration / ID collision). Use `.stash`, `.old`, etc.
2. **Deleting the stash fails with an I/O error until C4D exits** — it's still memory-mapped by the running process. Don't chain `&& rm stash` into your deploy (the failing `rm` aborts the rest of the script). Sweep old stashes at the *start* of the next deploy, or after C4D closes.

**When it bites:** any compile-deploy-test loop against C4D 2026 with the app kept open.

---

## 123. `.res`: `ANIM OFF;` makes a parameter non-animatable — but `ANIM ON` is an INVALID token that silently kills the ENTIRE description panel

**Discovered 2026-07-21** making recipe-affecting plugin parameters non-keyframable (they're hashed into a sim cache; animating them would clear+re-sim every frame).

**Wrong assumption:** to control animatability you write `ANIM ON;` / `ANIM OFF;` symmetrically.
**Actual behavior:** description parameters are **keyframable by default** — there is no `ANIM ON`, and writing it is an unknown token. Like any unknown `.res` keyword (the classic `FLOATLINE` trap), a single bad token makes C4D's resource parser **silently fail the whole CONTAINER** — the Attribute Manager then shows only Basic/Coordinates and none of your params, with no error in the console. `ANIM OFF;` *is* valid (used in SDK examples) and correctly makes a param static/non-animatable.

**Fix:** to make a param non-animatable, add `ANIM OFF;` inside its block. To keep it animatable, write nothing (that's the default). Never write `ANIM ON`. Preflight new `.res` tokens against the SDK `.res` corpus before shipping — an ALL-CAPS keyword that appears in zero SDK files is suspect.

**When it bites:** adding any parameter attribute you half-remember; the failure mode (whole panel blanks) looks like a build/registration problem, not a one-token typo.

---

## 122. Adding a NEW source file to a CMake-generated SDK plugin needs a reconfigure (`cmake -S . -B build`) — `cmake --build` alone silently omits it

**Discovered 2026-07-21** splitting a plugin into more `.cpp` files. Added `foo.cpp`, rebuilt, and the new class's `RegisterFooPlugin()` link-errored / the plugin element never appeared.

**Wrong assumption:** the Maxon CMake setup globs `source/` so a new `.cpp` is picked up by the next build.
**Actual behavior:** the generated `.vcxproj` lists source files **explicitly** (from a `MaxonFileList.txt` captured at configure time). `cmake --build <dir>` compiles that fixed list; a newly-added file isn't in it. You must **re-run the configure step** — `cmake.exe -S . -B _build_x64_v143` — which re-scans `source/` and regenerates the project, *then* `--build`. (Verify the file landed with `grep foo.cpp _build.../<proj>.vcxproj`.)

**When it bites:** every time you add a file rather than editing existing ones; symptom is an unresolved-external link error or a plugin element that just doesn't register, with no compile error on the new file (it was never compiled).

---

## 121. A generator that queries its linked deformer via node `Message()` runs on a PARALLEL cache-build thread — it can race the deformer's `ModifyObject`; publish an immutable snapshot under a mutex

**Discovered 2026-07-22** with a generator (`GetVirtualObjects`) that sends a private `Message` to a linked deformer to read the deformer's rolling per-frame state, then draws geometry from it.

**Wrong assumption:** the two objects evaluate serially, so the message handler can hand back a reference/copy of the deformer's live member directly.
**Actual behavior:** modern C4D builds independent objects' caches **concurrently**. The deformer's `ModifyObject` (itself a `const` method invoked on a worker thread) may be *rewriting* that shared member (`clear()` + `push_back`, i.e. reallocating a `std::vector`) at the exact moment the generator's message handler copies it out — a data race → UB (crash or garbage geometry). This is not exotic: any scene with both objects present hits it, and enabling the feature often auto-adds the generator.

**Fix (publish/snapshot):** keep the live working buffer thread-local to `ModifyObject`, and after each rebuild copy it into a separate *published* member under a `std::mutex`; the message handler reads only the published copy under the same lock. The hot per-element loop that also reads the working buffer stays lock-free (same thread as the rebuild). One copy-under-lock per eval; contention is negligible.

**When it bites:** any cross-object "generator asks deformer/other-node for live state via `Message`" design in 2026's parallel evaluation.

---

## 120. A deformer must NEVER derive its parameters from the deform cache's stored bbox (`op->GetRad()/GetMp()`) — that reflects the DEFORMED result of the *previous* eval → positive-feedback loop

**Discovered 2026-07-03** adding an "auto" mode to a deformer that sized itself to "the top of the host mesh" via `op->GetRad()`/`GetMp()` (bounding-box centre/half-size of the object being deformed).

**Wrong assumption:** `op->GetRad()/GetMp()` inside `ModifyObject` give the *input* mesh's bounds.
**Actual behavior:** the deform cache's stored bbox is updated from the deformer's OWN output (via the `MSG_UPDATE` the deformer sends after writing points). So on frame N it reflects frame N−1's **deformed** shape. Deriving a parameter from it (e.g. a water level = "top of mesh") makes the parameter chase the previous frame's peak displacement → the surface climbs into itself → runaway "mountains". Symptom: "I just touch the object and everything explodes."

**Fix:** measure from the **entry point positions** the deformer is handed — loop the incoming `PointObject::GetPointW()`/`GetPointR()` array and compute your own min/max — never from `GetRad()/GetMp()`-derived bboxes. In a `Message` handler that needs the undeformed shape, read `host->GetCache()` (the pre-deform cache), not `GetRad()`. General rule: **a deformer parameter must never be a function of the deformer's own output.**

**When it bites:** any "auto-fit / auto-level / adaptive" deformer parameter computed from object bounds; the feedback is invisible on a flat host (whose bbox top is already 0) and only explodes on a solid/thick one.

---

## 119. Driving an external app (e.g. a headless renderer) from a C4D C++ plugin — write a `.cmd` launcher + `std::system`, don't fight `GeExecuteProgram` or Windows quote-hell

**Discovered 2026-07-18** wiring a one-click "bake to external format" action into a generator plugin: after exporting a per-frame file bucket, the plugin shells out to a headless renderer to bundle it. Two traps:

1. **`GeExecuteProgram(const Filename& program, const Filename& file)` only passes ONE file argument** — you cannot append flags like `--no-gui --script foo.lua --stop-after-script`. It's for "open this file in that program", not "run this command line". `GeExecuteFile(path)` is even more limited (shell-open by association).

2. **`std::system()` on Windows is `cmd /c <string>`, and multi-token quoted command lines hit the infamous double-quote rule** — `system("\"C:/a b/app.exe\" --flag \"C:/c d/in.lua\"")` gets mis-parsed because `cmd /c` strips quotes unpredictably when there's more than one quoted token. Paths with spaces (`C:\Users\First Last\...`) make this bite every time.

**Fix — write a tiny `.cmd` next to your output that owns all the quoting, then `std::system` just the (single, quoted) `.cmd` path.** `cmd /c "C:\x y\bake.cmd"` has exactly two quote chars → cmd runs the batch cleanly. Inside the `.cmd` you control quoting fully:

```cpp
// bake.cmd  ->  @echo off\r\n "app.exe" --no-gui --script "bake.lua" --stop-after-script\r\n
std::string cmd = "@echo off\r\n\"" + appExe + "\" --no-gui --script \"" + luaPath + "\" --stop-after-script\r\n";
WriteTextFile(cmdPath, cmd);            // fopen/fwrite; Filename::GetString().GetCString(buf, sz) for the path
std::system(("\"" + cmdPath + "\"").c_str());   // blocks until the app exits; then fopen-probe the output to verify
```

Bonus robustness for *scripted* targets (a Lua/Python the external app runs): **bake the config as literals into the emitted script** rather than passing them as CLI `-a` args — then spaced paths never touch the shell's arg parser at all. Verify success by `fopen`-probing the expected output file (no dependency on `GeFExist`). This is exactly how SplatFlow's "ORBX (animated, Octane)" export drives headless Octane (`octane.exe --no-gui --script <lua> --stop-after-script`).

---

## 118. `GeRayCollider.GetNearestIntersection()` — the key is misspelled `barrycoords`, and on QUADS it's unreliable; compute your own barycentric from `face_id` + `hitpos`

**Discovered 2026-06-29** building a surface-bound stroke tool (raycast paint onto 3D, must stick through deformation). The hit dict from `c4d.utils.GeRayCollider.GetNearestIntersection()` (and `GetIntersection`) has these keys:

```
face_id, tri_face_id, hitpos, distance, s_normal, f_normal, barrycoords, backface
```

Three traps:
1. **The barycentric key is spelled `barrycoords` (double-r)** — `hit["barycoords"]` raises `KeyError`. Easy to miss.
2. **On quad polygons `tri_face_id` comes back `-1`** (not 0/1 for the triangle half), and the 3 `barrycoords` weights do **not** reconstruct the hit position from any 3 of the quad's 4 corners. Brute-forcing all `(triple, permutation)` combos against known hits gave a *minimum* avg error of ~63 scene units — i.e. there is no triangle-corner assignment that works. The reported barycentric is not usable triangle-barycentric over the quad's vertices.
3. **`f_normal` and `s_normal` are equal and NOT normalized** (`s_normal` is a reserved/unimplemented phong slot — same caveat as the C++ `GeRayColResult`). Normalize before use; interpolate your own smooth normal if you need one.

**Fix — ignore C4D's `barrycoords`/`tri_face_id` entirely.** Use only `face_id` + `hitpos` from the collider, then compute your own barycentric against a triangle you pick from the quad:

```python
def bary(A, B, C, P):
    v0=B-A; v1=C-A; v2=P-A
    d00=v0.Dot(v0); d01=v0.Dot(v1); d11=v1.Dot(v1); d20=v2.Dot(v0); d21=v2.Dot(v1)
    den=d00*d11-d01*d01
    v=(d11*d20-d01*d21)/den; w=(d00*d21-d01*d20)/den; return (1-v-w, v, w)

# bind: pick tri (a,b,c) else (a,c,d) by which gives in-range weights; store (face_id, tri, u,v,w)
# reconstruct on the DEFORMED mesh (plane.GetDeformCache()) with the same weights
```

Verified: this reconstructs bound points with **0.0 error** on the rest mesh and sticks exactly through a 689-unit Bend deformation (read back via `GetDeformCache()` + `ExecutePasses`). The collider is correct for *hit position/face* — just not for barycentric on quads.

---

## 117. Vertex colors do NOT render in the Hardware/Standard material pipeline — there is no RGB "Vertex Color" channel shader; use per-bucket flat materials

**Discovered 2026-06-29** rendering vertex-colored ribbon geometry with the Hardware preview renderer (`RDATA_RENDERENGINE = 300001061`). A `VertexColorTag` (per-point RGB) shows in the editor viewport but renders **flat gray** in the Hardware/Standard pipeline.

**Cause:** there is no standard-material channel shader that reads `VertexColorTag` RGB. Scanning `FilterPluginList(PLUGINTYPE_SHADER)` for "vertex"/"color" yields only **`Vertex Map` (1011137)** — *scalar*, reads Vertex *Map* tags, not RGB — and **`Display Color` (1033961)**, which **also renders flat gray** (it does not pull VertexColorTag RGB). There is no `Xvertexcolor`. Per-point/gradient vertex color along a stroke is a **nodal-material / Redshift / Octane vertex-attribute** feature, not available in Standard/Hardware preview.

**Fix:** for color in Hardware render, bake color into the **material**, one flat material per color bucket, and assign via texture tag (`MATERIAL_COLOR_COLOR`). Confirmed clean red/green/blue strokes render this way. Implication for any stroke/GP-style tool: batch geometry by color bucket; flat-per-stroke color is the render-correct path, smooth per-point color is deferred to a nodal/3rd-party renderer.

---

## 116. `VertexColorTag` per-point write: `SetPerPointMode(True)` then `SetColor(data, None, None, i, c4d.Vector(rgb))` — a dict (per-polygon form) is rejected

**Discovered 2026-06-29.** Writing per-point vertex colors in Python:

```python
vct = c4d.VertexColorTag(pointCount)
vct.SetPerPointMode(True)           # MUST set before writing per-point
obj.InsertTag(vct)
data = vct.GetDataAddressW()
for i, col in enumerate(colors):
    c4d.VertexColorTag.SetColor(data, None, None, i, c4d.Vector(r, g, b))
```

The per-polygon form `SetColor(data, None, None, polyIdx, {"a":Vec4d, "b":..., "c":..., "d":...})` (seen in older snippets) raises **`argument 5 must be c4d.Vector, not dict`** in 2026. In per-point mode arg 5 is a plain `c4d.Vector` (RGB) and the index is the *point* index. `GetDataAddressW()` returns a write handle that must be re-fetched if you toggle per-point mode.

---

## 115. `c4d.utils` has no `Sin` / `Cos` — use Python's `math`

**Discovered 2026-06-29.** `c4d.utils.Sin(x)` raises `AttributeError: module 'c4d.utils' has no attribute 'Sin'`. The trig helpers tribal knowledge expects there don't exist; `c4d.utils` does have `DegToRad`/`RadToDeg`/`VectorToHPB`/`HPBToMatrix`. Just `import math` and use `math.sin`/`math.cos` (allowed through the MCP bridge — only `os`/`subprocess`/`exec`/`eval` are blocked).

---

## 114. MCP `execute_python_script`: globals do NOT persist across calls; offscreen `RenderDocument` works on a temp doc but can drop the bridge

**Discovered 2026-06-29.** Two bridge behaviors worth knowing for multi-step agent work:

1. **No global persistence between calls.** Each `execute_python_script` runs in a fresh namespace — functions/vars defined in one call are GONE in the next. Multi-step harnesses must be **self-contained per call** (inline every helper) rather than "define once, call later."
2. **Offscreen render is safe but not free.** `documents.RenderDocument(tempDoc, rd.GetData(), bmp, RENDERFLAGS_EXTERNAL)` on a throwaway `BaseDocument` renders without touching the user's active doc — `GetRenderBaseDraw()`/`SetSceneCamera()` are valid on an *uninserted* doc. This is the way to render previews without disturbing the live scene. **Caveat:** an offscreen `RenderDocument` was observed to drop the socket once mid-run (`Connection reset by peer` → `Not connected`). Prefer modest resolutions, and have a reconnect/retry path. The Hardware preview render **background is dark viewport gray, not white/transparent** — image-diff/coverage metrics must sample the actual corner pixel, not assume a white background.

---

## 113. A C4D Python plugin running a background thread MUST stop+join it on `C4DPL_ENDACTIVITY`, or it crashes C4D at shutdown (`PyGILState_Ensure`)

**Discovered 2026-06-26.** The MCP bridge plugin (`.pyp`) runs a TCP socket server in a `threading.Thread` and spawns a per-client handler thread per connection. C4D closes produced intermittent crash minidumps with this stack:

```
python311.dll: PyThread_tss_create
python311.dll: PyGILState_Ensure
python.xdl64 ...
```

**Cause:** at shutdown C4D finalizes the embedded Python interpreter. Any background thread still blocked in a C call (`socket.accept()`, `socket.recv()`) gets torn down *mid-GIL-acquire* — `PyGILState_Ensure` runs against thread-state that's already gone → crash. **`thread.daemon = True` does NOT save you**: daemon threads are killed abruptly at finalization, which is exactly when they're mid-call.

**The two-part fix:**
1. **Actually stop the threads on shutdown.** C4D sends `C4DPL_ENDACTIVITY` (module-level `PluginMessage(msg_id, data)`) *before* finalizing Python — that's the hook. Most plugins only handle `C4DPL_PROGRAM_STARTED` and never register a shutdown path, so the threads are never stopped.
2. **Make stop() unblock and join, not just set a flag.** Setting `running = False` is useless against a thread blocked in `accept()`/`recv()` — those don't re-check the flag until they return. You must **close the sockets to force the blocking calls to raise**, then join:
   - close the listening socket → `accept()` raises `OSError` → server loop exits
   - track every client socket + handler thread; close each client socket → its `recv()` raises → handler exits
   - `join(timeout=...)` every thread (bounded, so a wedged handler can't hang C4D's shutdown)

```python
def PluginMessage(msg_id, data):
    if msg_id == getattr(c4d, "C4DPL_ENDACTIVITY", None):
        srv = _get_running_server()
        if srv: srv.stop()      # closes listen+client sockets, joins all threads
    return True
```

**Detection:** intermittent `_bugreports/minidump.dmp` on close with `PyGILState_Ensure` near the top of a non-main thread. Applies to ANY `.pyp` with a socket server, file watcher, or polling thread.

---

## 112. Migrating a plugin to the 2026.3 SDK breaks on the BUILD system, not the code — stock CMake presets pin a Windows SDK you may not have

**Discovered 2026-06-26** migrating a generator+scenehook+spline+particle plugin from the 2026.2 SDK to 2026.3.

**The code side is a non-event.** Source written against 2026.2 (ObjectData, SceneHook, SplineObject, VertexColorTag, ParticleGroupObject, `maxon::ParallelFor`) compiled **clean against 2026.3 frameworks — zero API breaks**, one pre-existing benign `C4244` warning. The 190 cinema.framework changes are additive for mainstream usage, and 2026.2-compiled plugins keep loading on 2026.3.x (ABI stable within the major version).

**The friction is entirely the new build system.** 2026.3 is pure-CMake (top-level `CMakeLists.txt` + `CMakePresets.json`, no project-tool exe); plugins are globbed from `plugins/*/` with `projectdefinition.txt` in a `<plugin>/project/` subfolder; the projectdefinition format changed (lowercase `Platform=windows;...`, `stylecheck.level` must follow `C4D=true`).

**The trap:** stock presets pin an exact Windows SDK, e.g. `windows_vs2022_v143_x64` → `"architecture": "x64,version=10.0.20348.0"`. If that version isn't installed, configure dies: *"no Windows SDK with that version was found."* And a failed configure poisons the cache (*"does not match the platform used previously"*) so you must `rm -rf <binaryDir>` before retrying.

**Fix:** machine-local `CMakeUserPresets.json` at the C++ SDK root inheriting the stock preset but overriding `architecture` to your installed Win SDK (`...\Windows Kits\10\Include\`). Full validated runbook: [c4d_2026_3_sdk_migration_guide.md](c4d_2026_3_sdk_migration_guide.md).

---

## 111. `CSegment` doesn't exist in C4D 2026 — `SplineObject::GetSegmentW()` returns `Segment*`

**Discovered 2026-06-10** building a managed SplineObject output (SplatFlow Filaments). Older tribal knowledge / forum code says spline segments are `CSegment`. In the 2026 cinema API the struct is plain `Segment` (`c4d_baseobject.h`, `struct Segment { Int32 cnt; Bool closed; }`), accessed via `spline->GetSegmentW()` after `SplineObject::ResizeObject(pointCnt, segCnt)`. `CSegment` is a hard compile error (C2065).

---

## 110. New C4D particle system: freshly-shot particles are INVISIBLE to expression tags for one evaluation (1-frame lag)

**Discovered 2026-06-05** debugging why a particle->splat binding cache never built (exports silently wrote the un-deformed rest splat).

**Wrong assumption:** a Mesh Emitter (1062577) in Shot mode emits at frame 1, so a Python expression tag evaluating frame 1 can read the new particles from the ParticleGroupObject.

**Actual behavior:** at the frame-1 expression evaluation `pg.GetParticlePositionsR()` returns empty. The particles only become readable at the NEXT evaluation (frame 2) — where they still sit at rest for a frame or two before forces move them.

**When this bites:** any "capture state at the spawn frame" logic hard-gated to the shot frame (`frame <= 1`) can NEVER fire — the capture window never coincides with readable data. The failure is silent: no error, the cache just never exists, and downstream consumers fall back to stale/rest data.

**Fix:** widen the capture window across the early frames (`frame <= 5`) AND validate the captured data really is rest state before persisting (SplatFlow uses a nearest-neighbor miss-rate check: at true rest every particle sits on a source point, ~0 misses; a moved frame produces mass misses and is rejected, retried next frame).

---

## 109. Scripted timeline control of the new particle sim: `SetTime(0)` resets, `SetTime(movedFrame)` does NOT rewind

**Discovered 2026-06-05** (same session as #110), verified live via MCP.

- `doc.SetTime(BaseTime(0, fps))` + `ExecutePasses` **resets/re-emits** the sim (particle count drops to 0, re-shoots at frame 1).
- `SetTime` to a *moved* frame does **not** rewind live sim state — the sim stays wherever playback left it (jumping 36 -> 1 leaves particles deformed).
- Forward stepping (`SetTime(f)` + `ExecutePasses` per frame) advances the sim deterministically, and the sim **survives scripted ExecutePasses** (it does not reset like some legacy systems).

**Consequence:** batch exporters can safely rewind-to-0 and re-simulate forward to rebuild spawn-frame state, but "jump to frame N" does not mean "state as if played to N".

---

## 108. Hidden child (`NBIT::OHIDE`) leaves a lingering '+' fold arrow on the parent in the Object Manager

**Discovered 2026-06-09.** A SceneHook-managed helper object parented under a generator and hidden with `ChangeNBit(NBIT::OHIDE, NBITCONTROL::SET)` disappears from the OM — but the parent keeps a phantom expand arrow.

**Fix pattern:** keep managed helpers at **document root** and make them follow the owner manually from the SceneHook each pass:
- transform: `if (!(helper->GetMg() == owner->GetMg())) helper->SetMg(owner->GetMg());` — the compare-first matters: unconditional `SetMg` dirties the object every pass and re-triggers scene evaluation forever.
- visibility: root objects no longer inherit the owner's OM dots — mirror the owner's EFFECTIVE mode (walk up the parent chain for the first non-`MODE_UNDEF` `GetEditorMode()`, default `MODE_ON`).

---

## 107. ObjectData GVO container-write rules: `SetString` from a static/viewer GVO path = infinite GVO loop — SceneHook must own UI readouts

**Hard rule (from GSL analysis, re-confirmed 2026-06-09):** `bc->SetString()` (or any container write) inside `GetVirtualObjects` marks the object DATA-dirty, which schedules another GVO, which writes again — an infinite evaluation loop on static scenes. Consequence: STATICTEXT info readouts (point counts, format info) can never be populated from a viewer-mode GVO, and stay permanently blank on objects that never enter a sim path.

**Correction (2026-09-04):** SceneHook Execute is documented as threaded context in the pinned 2026.3 SDK. The earlier claim that it always runs on the main thread and may write freely was too broad. Publish immutable GVO results, use an explicitly scheduled consumer, and obey the thread/document-access rules for the actual callback. Change guards prevent dirty loops; they do not make an unsafe document write thread-safe.

---

## 106. Python expression tag's BaseContainer is a usable C++<->tag contract (status, blobs, versioned auto-upgrading code)

**2026-09-15 live GS-offset addendum (C4D2026.3.4):** `TPYTHON_FRAME=True` suppressed re-evaluation after changing a Python tag User Data offset while paused, even with `ExecutePasses`; stepping the timeline advanced correctly. `TPYTHON_FRAME=False` allowed the offset change on the same frame. Add a filename equality early-return before Octane SetParameter/MSG_UPDATE to avoid reloading unchanged splats on extra evaluations. Validated five independent GS objects, paused offset30->31->30 at frame45 (files0075->0076->0075), then timeline46->45. This validates expression/filename state, not rendered ORBX animation. Evidence: a private production scene's live-repair validation JSON and raw MCP results. Do not put a Script Manager installer in a Python tag; inspect source before automatic patching and retain backups.


**Discovered/established 2026-06-09** (SplatFlow bridge tag v7).

- A Python tag can `op.GetDataInstance().SetString/SetInt32` private IDs (10000+) on **itself** during expression execution; C++ reads them via `tag->GetDataInstance()` — a clean status channel (e.g. machine-readable `"BOUND|count|frame"` + human display string).
- Binary payloads survive as **base64 strings** in the tag container (Python `BaseContainer` can't store raw bytes) — a 125k-int32 index map is ~667KB of base64 and travels INSIDE the .c4d scene file, surviving reboots/machine moves that kill `%TEMP%` caches.
- `tag[c4d.TPYTHON_CODE]` / `SetString(400, code)` replaces the tag code, recompiles, and **resets the tag's module-level globals** — useful as a deliberate state-clear (e.g. a "clear cache" button that must drop the tag's in-memory copy too).
- Stamp a **code-version int** in the tag container; a SceneHook compares it against the plugin's current version and rewrites the embedded code when stale — saved scenes auto-inherit every tag fix without user action. Keep the tag source in ONE C++ raw-string function (`R"PY(...)PY"_s`) shared by setup, upgrade, and reset paths.

---

## 105. `c4d.documents.RenderDocument` produces all-black PNGs on Octane-installed C4D — override `RDATA_RENDERENGINE` to `PREVIEWHARDWARE`

**Discovered 2026-05-26** building a batch-screenshot system for procedural board variants. `RenderDocument` was producing fully black PNGs even though the scene rendered fine in the viewport.

**Wrong assumption:** `c4d.documents.RenderDocument(doc, doc.GetActiveRenderData().GetData(), bmp, RENDERFLAGS_EXTERNAL | RENDERFLAGS_PREVIEWRENDER)` uses the C4D Standard renderer when no special render engine is configured, and Standard always produces valid output for a scene with materials + camera.

**Actual behavior:** on a C4D install where Octane (or any third-party render plugin) is registered, the Standard renderer path is intercepted by the plugin's hooks. RenderDocument runs to completion (returns `RENDERRESULT_OK`), the bitmap is initialized, the PNG is saved — but the bitmap contents are all `(0,0,0,255)`. Same gotcha as the MCP `viewport_screenshot` Standard-mode issue (gotcha hidden in `reference_c4d_mcp_viewport_limitations`): Octane's intercepts catch the Standard pipeline.

**Fix:** clone the render data, override `RDATA_RENDERENGINE` to the hardware previewer (`300796274` aka `RDATA_RENDERENGINE_PREVIEWHARDWARE`), then render through that clone — the original document's render settings are unchanged:

```python
rd = doc.GetActiveRenderData()
rd_data = rd.GetDataInstance().GetClone(c4d.COPYFLAGS_NONE)
rd_data[c4d.RDATA_RENDERENGINE] = 300796274  # PREVIEWHARDWARE
rd_data[c4d.RDATA_XRES] = 1200
rd_data[c4d.RDATA_YRES] = 750
bmp = c4d.bitmaps.BaseBitmap()
bmp.Init(1200, 750)
rc = c4d.documents.RenderDocument(doc, rd_data, bmp,
    c4d.RENDERFLAGS_EXTERNAL | c4d.RENDERFLAGS_PREVIEWRENDER)
if rc == c4d.RENDERRESULT_OK:
    bmp.Save(out_path, c4d.FILTER_PNG)
```

`PREVIEWHARDWARE` is the same engine the MCP plugin's `viewport_screenshot` uses internally (per `reference_c4d_mcp_viewport_limitations`). It bypasses Octane's hooks because Octane doesn't subscribe to the hardware-preview pipeline.

**When this bites:** any batch / unattended render system (variant exports, baking, animation frame dumps) running on an artist's machine that has Octane (or Redshift) installed will produce all-black output. The dev never notices because the viewport looks correct.

**Detection:** if `RenderDocument` returns OK but `bmp.Save()` writes a file that's suspiciously small (~20KB for 1200×750 is typical for all-black PNG) and looks black when opened, the renderer hook is the culprit. Hardware override fixes it.

Related: `cinema4d-mcp` ships a viewport_screenshot auto-detect that watches for all-black output and falls back to hardware. The equivalent auto-detect can be folded into your own bake pipeline:

```python
# Detect all-black output and fallback if needed
mid_pixel = bmp.GetPixel(bmp.GetBw()//2, bmp.GetBh()//2)
if sum(mid_pixel) < 3:  # essentially black
    print("warning: black render — confirm renderer override applied")
```

---

## 104. `c4d.Osweep` child order is load-bearing — profile FIRST, path SECOND

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

## 103. Iterating on a disk Python file from MCP requires `del sys.modules[...]` before re-import

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

## 102. `execute_python_script` heavy compute: bound work per call with explicit budgets, not naive loops

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

## 101. `SetDirty()` from a `MessageData.CoreMessage` during an active draw tool crashes C4D via recursive cache eval

**Discovered 2026-05-26** building a scene-level watcher that auto-rebuilds a Python generator when a linked spline's data dirty count changes. The watcher worked correctly for programmatic edits (`SetPoint`/`ResizeObject` from a script). But the first interactive test — drawing a new point onto the spline with the Bézier/Pen tool — crashed C4D instantly.

**Crash:** `ACCESS_VIOLATION in VCRUNTIME140.dll!memmove`, with ~50 frames of recursive `c4d_base.xdl64` cache-evaluation in the stack underneath (the same recursive pattern that fires when an Opython generator is configured with Optimize Cache OFF).

**Mechanism:** during an active draw stroke, the tool dispatches `EVMSG_CHANGE` per click. The watcher's `CoreMessage` fires on each, calls `op.SetDirty(DIRTYFLAGS_DATA)` on the heavy generator, and C4D enters a generator-rebuild pass IMMEDIATELY — still inside the same event chain. The rebuild itself runs more scene-graph evaluation, which dirties more objects, which triggers more recursive eval. If the generator takes >100ms to rebuild AND the tool keeps firing events, the recursive stack grows until `memmove` (or any other allocation path) hits a buffer past the stack limit.

**Fix (defense in depth):**

1. Skip the watcher entirely while a non-default tool is active. The default Move/Select/Scale/Rotate tools are safe; everything else (Bezier, Pen, Sketch, paint tools, etc.) means "interactive in-progress, do not interrupt." When the user exits the tool back to Move/Select, one more `EVMSG_CHANGE` fires and the watcher catches up.
2. Heavy debounce (e.g. 1.5s) so even safe paths can't fire dirty faster than the generator can rebuild.
3. Session-fatal exception kill-switch: any uncaught exception in the watcher disables it for the rest of the session so a transient failure doesn't keep crashing on every subsequent `EVMSG_CHANGE`.

Pattern:

```python
SAFE_TOOL_IDS = frozenset({
    c4d.ID_MODELING_LIVE_SELECTION_TOOL,
    c4d.Tmove, c4d.Tscale, c4d.Trotate,
})

class MyWatcher(plugins.MessageData):
    def CoreMessage(self, mid, bc):
        if mid != c4d.EVMSG_CHANGE: return True
        if c4d.modules.tools.GetActiveTool().GetType() not in SAFE_TOOL_IDS:
            return True   # interactive tool — do NOT dirty heavy generators
        # ... safe to SetDirty here ...
```

The general principle: **MessageData on EVMSG_CHANGE is delivered SYNCHRONOUSLY on the main thread inside whatever event handler caused the change.** `SetDirty` there isn't queued; it triggers immediate recursive evaluation. Treat MessageData like a render-thread context — only do cheap, idempotent work.

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

**Correction (2026-09-04):** a timeout alone proves neither that the script never started nor that it stopped. The old queue could execute it AFTER the timeout. See #128 for the new queued-cancellation/running-status protocol; do not repeat mutations based on a timeout message alone.

**How to diagnose:** call `get_console_log` after the timeout. If you see:

```
[plugin] [C4D] Waiting for main thread execution (29.0s elapsed)
[plugin] [C4D] Main thread execution timed out after 30.00s
```

— the waiter expired. Execution outcome is still unknown without queue-state evidence.

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

**2026-10-15 OSL follow-up:** [Octane OSL practical notes](octane_osl_practical_notes.md)
separate OTOY-documented animation/mapping rules, numerical caustic-shader
findings and the native Octane checks still pending. A Blender OSL compile
does not establish Octane GPU acceptance; do not generalize a constant
noise probe to all of Octane's documented noise functions.

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

## 125. A scripted parameter check is not an Attribute Manager acceptance test

Weavr (2026-09-04): SetParameter + ExecutePasses passed while artists still saw
keyboard commits, cycle changes and InExclude drops wait for a slider drag.
Exercise all three actual UI paths and observe the managed child WITHOUT a
manual second evaluation. Report API and UI results separately.

ObjectData receives MSG_DESCRIPTION_CHECKUPDATE for custom-GUI/container edits;
POSTSETPARAMETER alone is insufficient coverage. Call the superclass before
adjusting its drawflags and explicitly mark DATA dirty on the common commit
path. SYNC means interactive refresh; it is not a substitute for dirtying a
final commit. This is a candidate fix until the real UI path is checked.

2026-09-05 correction: the artist disproved that candidate on Weavr 8D928030.
The value and real spline HAD updated; the downstream Oline cache had not.
See #131. Adding more DATA dirtiness to the input was not the missing step.

A SceneHook AddToExecution priority must explicitly follow generator execution
when consuming a published snapshot. The pinned 2026.3 SDK also documents
SceneHook Execute as threaded context: do not treat this as general permission
to mutate the document hierarchy from arbitrary execution callbacks.

## 126. Distinguish ineffective controls from stale evaluation

Weavr Sheet hid a six-samples-PER-SOURCE floor, making small requested counts
indistinguishable. Its free junctions also depended on surplus anchor samples;
removing the floor revealed a nearly empty low-count sheet. Test both value
commit and a meaningful geometric response. Interior structure and attachment
sampling must be independent. Width-map multipliers do not change the line
thickness of an unrendered spline viewport; label them accordingly.

## 127. Null axis display is not a local +Z arrow

The 2026.3 Null description has no arrow display enum. AXIS with default SCREEN
orientation is a screen-facing bracket, not a directional target. A generator/
scene-hook viewport glyph can draw a shaft and arrowhead using the target's Mg
without adding rendered child geometry. Rotate the target and visually verify
the arrow; checking its enum alone is not acceptance.

## 128. Transport timeout does not mean cancellation

The old queue wrapper returned a timeout while leaving its closure executable.
A late mutation could run after a retry, causing duplicated objects or edits.
The bridge now atomically cancels QUEUED work before start; RUNNING work cannot
be interrupted safely. Its timeout returns execution_id + execution_state.
Poll get_execution_status; never blindly retry a running/unknown mutation.
Only 64 recent timed-out operations are retained; expired is NOT never-ran.
Repro: python -B tests/test_transport_safety.py (queued and running cases).

## 129. Bound wire frames and script output; do not log raw requests

TCP recv boundaries can split UTF-8 characters. Accumulate bytes and decode
complete newline frames. Enforce the 5 MiB request limit even before a newline;
bound responses to 8 MiB. The former raw-request log exposed auth_token and
script bodies before authentication. Log command metadata only.

execute_python now captures at most 65,536 stdout characters. Variable previews
are opt-in (include_variables), at most 64 names, and never stringify large
containers/user-defined repr. Truncation is explicit. Use files for large data.
This is trusted arbitrary Python, NOT a sandbox or a keyword blacklist.

## 130. Screenshot and reload hygiene

Screenshots are file-first (explicit save_path or a unique host temp PNG).
inline=True is opt-in and capped at 384 KiB of PNG; dimensions are 1..2048.
A failed file save reports an error, not an unexpected base64 dump. Frame and
active RenderData are restored in finally, including renderer failure. Stateful
simulation caches may still need resetting after frame-override captures.

Install into an EXPLICIT plugins directory. Backups must live OUTSIDE scanned
plugin directories, or use non-plugin extensions, to avoid duplicate plugin
registration. Use scripts/sync_to_installed.ps1; it refuses dirty documents,
quits normally, verifies copied hashes and checks ping's process_id + loaded
source SHA256 after restart. Never substitute Reload Python Plugins for a clean
socket-thread/plugin restart. C4D_MCP_AUTOSTART=1 is opt-in, not default-on.

BaseDocument.SetChanged(False) is invalid in Python 2026.3: SetChanged() only
marks changed. Do not invent a dirty-flag clearing API. C4D may auto-close an
empty untitled document during a document switch; retained Python wrappers can
then be dead. Preserve real artist documents and isolate test ownership.

## 131. A real managed spline can be current while its viewport line cache is stale

Weavr, C4D 2026.3.1: the artist typed 17 Radials and committed without touching
a slider. A read-only probe found Radials=17 and 2,894 real spline points, but
`spline.GetCache()` (Oline, type 5137) still had the previous 956 points.
One ordinary EventAdd, without editing a value, repaired the line cache.

The SceneHook published new points after C4D had already built that child's
line representation. Checking GetAllPoints/GetPointCount alone falsely passed
the earlier gates. A new multi-request test (commit, return to the event loop,
read only) reproduced the old cache on five consecutive numeric/mode edits.
For a linear spline without extra interpolation, compare real/cache point
counts AND hashes. B-Spline caches have different tessellation and need an
evaluated-curve check, not a point-count equality assertion.

Fix: queue `EventAdd(EVENT::ENQUEUE_REDRAW)` after changed managed output,
including transitions to empty. Make output publication idempotent first, or
the follow-up evaluation can rewrite identical points and loop indefinitely.
The pinned 2026.3 c4d_general.h documents EventAdd as thread-safe; ge_prepass.h
documents ENQUEUE_REDRAW as deferring until the current redraw completes.
Do not overwrite C4D-owned caches or recursively ExecutePasses from a scene hook.

Verified 2026-09-05 on C4D 2026.3.1, Weavr source 94705db / DLL 7412E5A0...966AC5:
all five previously failing numeric/mode edits now produce identical real/Oline
point hashes after returning to the event loop, without a second edit or forced
evaluation. Source-list clear/restore also passes (4,004 -> 0/no cache -> 4,004,
exact original hash restored). Idle read-only snapshots 1.2 seconds apart retain
the same geometry and dirty counter; no endless redraw observed. Existing 72
native product/lifecycle/single-pass/control checks remain green.

Repro: Weavr's `scripts/weavr_native_viewport_gate.py`, actions start/read,
radials/read, turns/read, sheet/read, volume/read, orb/read, then cleanup in
finally; separate requests with an event-loop return between commit and read.
Also test sheet/read, clear_sources/read, restore_sources/read. Never insert
ExecutePasses/DrawViews to make this check pass. These are event-loop/cache
checks, NOT OS keyboard/drop acceptance. This is not a solution claim for
external-render frame lag or hierarchy-thread safety.

Artist acceptance, 2026-09-05: after the above DLL was installed, Spenser
reported "nice type values work like a charm". This closes the typed-value
refresh defect with hands-on confirmation, not just scripted cache checks.
Do not extend that report to unconfirmed Web Type menu or object-drop paths.

## 132. A no-op main-thread publisher can cancel the very viewport draw it is meant to refresh

**Weavr, C4D 2026.3.1, 2026-09-06.** Artist QC: typed values and Seed steppers
appeared frozen, sometimes briefly blank; a camera nudge revealed the web.
Real spline AND Oline buffers could already agree. This was not established
loss of Volume topology: seed 204 had 439 segments / 4,829 finite points in
view, and 41 nearby seeds evaluated nonempty in a private clone.

The guarded native publication architecture introduced another failure after #131:
every worker SceneHook Execute queued a main-thread publisher, including
already-synchronized output. That callback **unconditionally called
StopAllThreads()**, interrupting a viewport draw. Sync then found no work and
requested no replacement redraw. Safe-thread dispatch alone was insufficient.

**Correction:** use a read-only revision/ownership/geometry preflight before
queuing publication, then rebuild the pending worklist before stopping any
threads. An already-current output must not cancel drawing. Keep synced
revision/dirty counters safe for worker reads. Still guard needed loaded-scene
hierarchy writes; this is NOT advice to mutate the document from SceneHook
worker callbacks. Avoid recursive ExecutePasses/DrawViews from those callbacks.

An initial event-wake + GetDimension-bounds candidate (F78043A5) was rejected
by the artist. The no-op guard candidate, SHA256
`70716EFE839EB8044E893D53EC7B4CE21D64A8DC28D1B22B5AC21C404FA1C788`, was installed
and Spenser reported **"nice seems to work"** after actual Seed/slider testing.
A passive native trace recorded seeds 205–211 publishing without subsequent
no-op cancellation. Native product 26/26, single-pass 7/7, controls 10/10,
private-worker 9/9, lifecycle 32/32 pass. Mode/empty/restore caches settle.
Evidence/repro: Weavr `docs/gate/ui_refresh_20260906/` and
`scripts/weavr_native_ui_probe.py`. This is bounded artist acceptance, not a
certificate for every control, renderer, simulation or hardware configuration.

## 133. Even a read-only MCP probe can supply the missing event that hides a UI refresh bug

The bridge posts SpecialEventAdd to get execute_python work onto C4D's main
thread. Consequently, a script with **no** EventAdd/ExecutePasses/DrawViews is
read-only for scene data, but is not timing-neutral. Its arrival can wake a
deferred publisher before the script inspects it. Earlier scripted checks
also explicitly called EventAdd after SetParameter.

Distinguish four layers: committed parameter, generated/managed geometry,
evaluated line cache, and the actual presented frame. Equality of the middle
two after a bridge event does not prove that the artist saw a fresh frame.

Record keyboard/stepper/drop behavior before the next bridge request, using
artist feedback or an authorized native capture. A bounded opt-in native trace
written during callbacks can be read from disk without waking C4D; disable it
for ordinary launches. viewport_screenshot is a new RenderDocument operation,
not an untouched framebuffer capture: it evaluates the scene and posts events.
Do not use it to certify the previously frozen frame.

Asynchronous cache checks also need a genuine event-loop interval: a back-to-
back socket read can land before the queued publication/line rebuild. Preserve
early observations, then check bounded convergence and stable idle. Matching
old real/Oline buffers immediately after a new parameter is not a pass either;
require the expected new geometry. Do not keep injecting events until a failing
actual UI test disappears and then call it fixed.

## 134. An inner PASS does not override an outer execution or cleanup error

A Weavr visual gate printed successful render results, then its wrapper raised
ReferenceError during cleanup: double isolation had let C4D retire the empty
outer document. The host parser initially extracted the inner PASS and missed
the outer failed execution. The rejected response was retained, isolation was
fixed, then visual and lifecycle checks were rerun successfully.

Require transport/execution success **and** the scenario's assertions **and**
clean cleanup. Preserve raw responses, DLL/source provenance and rejected
attempts. Reacquire objects after Undo/Redo: even a stock Cube can have a new
Python wrapper/live identity. Only destroy test-owned documents and objects;
keep artist data out of public fixture commits. See #128 and #130 for timeout
outcomes and empty-document retirement.

## 135. Evaluated mesh support must cover the entire hierarchy, in every consumer

Weavr's source reader supported evaluated Text/Extrude, but its collider reader
still used a first-polygon shortcut: Text was ignored, split caps were omitted,
and Bend-deformed editables used undeformed coordinates. Testing only source
inputs had not exercised Avoid/Cut/Adhesion readers. The audit found 24/27
collider parity failures against independently flattened evaluated references.

Use the project's pinned SDK hierarchy/deformation contract, preserve transforms,
and account for all mesh parts. Declare/track linked-child and cache dependencies
so edits invalidate consumers. Reuse ingestion across roles where appropriate,
but retain role-specific behavior. Evaluate vertex-map/selection correspondence
against the actual resulting topology; never silently map original indices onto
incompatible generated geometry.

All 27 collider comparisons and 27 live child-edit cases passed on Weavr's
0ECE7165 build; `docs/gate/audit_fixes_20260905/` records the fixtures and remaining
limits. Separately, point-only cutting missed outside-to-outside chords through
thin/disconnected bodies; finite-segment intersection tests closed the three
zero-gap failures. Neither result proves arbitrary mesh/B-Spline/renderer safety.

## 136. Knowledge section extraction can mistake a body hash/reference for a heading

The bridge build `2026-09-04-transport-controls-1` uses a simple leading-`#`
section parser. A line starting `#131:` in entry 132 was treated as a level-one
heading, so knowledge_get returned only 545 bytes with `truncated: false`,
omitting the actual correction and acceptance. C/C++ `#include` lines and
Markdown-looking headings inside fenced examples can cause the same issue.

For authoritative reading use the canonical local archive (the skill's
knowledge.py helper returns complete numbered entries), or fetch the full
file with a sufficient limit and verify completion. The new entry was
rewrapped so its live MCP retrieval works; that is not a parser-code fix.
Parser hardening should recognize real ATX headings and fenced code, with
regression fixtures. Bridge code is unchanged in this documentation update.

## 137. Do not assert a boolean return from Python c4d.CallCommand

In C4D 2026.3.4, Circuitry's command test used `assert c4d.CallCommand(1070114)`
and failed even though Execute ran and created the correct Python Generator.
An instrumented callback confirmed dispatch; the return was None. The local
2026.3 Python SDK reference declares no return value for this function.

Call it on the main thread, then assert the command's observable result: in this
case exactly one Opython object, its native controls, a successful evaluated cache,
and module paths pointing at the installed build. The corrected test passed via
the registered command. No restart was needed. This is separate from direct
Execute calls, normal startup loading, and actual menu/typing acceptance.

Evidence: CircuitBoardTool/tools/c4d_activate_live_install.py and the raw
validation_python/installed_010_outer.json checkpoint, September 12, 2026.

## 138. Compare generated world geometry to the model's rotation convention

Circuitry's routing plane uses x'=cos(a)*x+sin(a)*z and
z'=-sin(a)*x+cos(a)*z. C4D heading +a produced the opposite rotation in the
2026.3.4 live fixture. Symmetric boxes hid this until Draft-batched asymmetric
surface marks were compared with individually parented Medium geometry.

The adapter now uses heading -a for that model convention. World-space axes and
each detailed component's vertices agree within 1e-6. The previous package test
multiplied by the inverse parent transform and asserted the stored heading, which
could not catch a consistently wrong parent rotation. Test world-space points
against independent model coordinates before declaring model/render agreement.

Avoid comparing rounded coordinate multisets for exact equality: tiny floating
point differences straddled decimal rounding boundaries after the rotation fix.
Compare corresponding authored vertices using a tolerance. CircuitBoardTool's
c4d_live_smoke.py and c4d_art_direction_smoke.py cover this; rejected and corrected
outer responses are retained under validation_python/*_011_outer.json.

## 139. Rectangular Sweep joins can spike at tiny path reversals

Circuitry's artist-edited logo spline exposed this in C4D 2026.3.4: a 0.8125-wide,
0.5-high rectangular Sweep had evaluated vertices 19.4996 units from its path
(40.88 times the rectangular profile's half-diagonal). The route contained tiny
grid-to-pad backtracks and near reversals. A finite, connected centerline alone
does not establish safe swept geometry.

For planar copper, Circuitry 0.12 unions finite segment rectangles and inscribed
round joins before extruding the resulting closed outlines. Unioning the strip
footprints absorbs reversals and overlaps rather than joining offset lines with
unbounded miters. All 96 traces in the artist fixture now stay within half-width
in XZ (5,038 evaluated vertices), including the actual previously failing routes.
The layout, net identities and artist values are preserved. Separate synthetic
tests cover short reversals, loops, duplicates and seeded self-overlaps. This
does not validate arbitrary 3D Sweep frames or the legacy Sweep-animation path.

Evidence: CircuitBoardTool/c4d/circuitry_copper.py, tests/c4d_live_012_smoke.py,
validation_python/swoosh_sweep_geometry_012.json and artist_copper_bounds_012.json.

## 140. A linked source below a Python Generator can still have its previous cache

Circuitry's native-fracture fixture initially passed first-pass updates while the
source preceded the generator. Reordering the generator above the fracture then
changing the fracture seed left the first pass using old geometry. The second
pass corrected it. Testing only the original object order would have missed this.

Do not treat GetHierarchyClone/GetAndCheckHierarchyClone as a general dependency
graph for linked external siblings. Trials in this fixture returned old clones,
None clones or an explicit dirty result without resolving the external ordering.
The current Python Generator contract explicitly requires Source Mesh to precede
Circuitry. New generators are inserted last; an unsupported ordering retains the
last good geometry and reports the required correction. A source-first seed edit
then gives matching first- and second-pass snapshots. No active-document
ExecutePasses, reparenting or document writes occur inside generator main().

The official explanation of this classic-API limitation is also recorded at
https://developers.maxon.net/forum/topic/15383/unexpected-refresh-behavior-when-manipulating-linked-object/10
This is a bounded workaround, not universal dependency handling for animated or
simulation-driven inputs. Raw rejected/accepted trials live under CircuitBoardTool/
validation_python/live012Dependency_012_outer.json and live012Hierarchy*_012_outer.json.

## 141. User-data parents can fail to resolve when the group is inserted later

Discovered in C4D 2026.3.4 while repairing Circuitry 0.12. Reparenting old controls
to newly appended Advanced groups preserved DescIDs and valid-looking stored
DESC_PARENTGROUP values, but the native Attribute Manager lost usable controls.
Comparing GetUserDataContainer with GetDescription exposed 53 fields whose actual
resolved parent fell back to ID_USERDATA instead of the assigned later group.

Explicit DESC_TITLEBAR / DESC_COLUMNS and LinkBox custom GUI flags alone did not
fix those parent mismatches. Reinsert descriptions parent-first using the SAME
DescIDs and restore their values. The disposable fixture retained BaseLinks and
an existing CTrack/key through repair, Undo/Redo and save/reopen. Do this only on
the main thread inside the caller's appropriate Undo operation. Never renumber
artist parameters just to make their displayed order convenient.

Circuitry now sets group title bars, puts link fields near the beginning, and
uses short wrapped Help labels. Long static labels plausibly widened the label
column enough to crowd link editors, but that explanation is an inference: this
session had no native desktop UI surface. Resolved-description, native parameter
message and saved-scene tests are evidence of correctness, not visual AM approval.

Evidence: CircuitBoardTool/c4d/circuitry_live_ui.py (repair_order/audit),
tests/c4d_live_013_smoke.py, validation_python/ui013Repro_outer.json (53 errors
before and after flags-only trial, zero after reorder), ui013Repair_outer.json,
smoke013final_outer.json. Python Generator message(id,data) handles main-thread
MSG_DESCRIPTION_POSTSETPARAMETER dict['descid']; native USERINTERACTION SetParameter
tests verified spline/mesh input switching without writes from generator main().

## 142. C4D 2026 vertex maps use TRANSFER_CONTROL_FIELDS to evaluate their Fields list

Assigning ID_TAGFIELDS on a fresh vertex-map tag alone leaves its evaluated
weights unchanged when Transfer is Off. The 2026.3 resource includes Ttransfer:
set tag[c4d.TRANSFER_CONTROL] = c4d.TRANSFER_CONTROL_FIELDS for a Fields-driven tag.
Do not assume the older ID_TAGFIELD_ENABLE checkbox remains its active UI control.
The local 2026.3 Python SDK cinema_resource/tag/ttransfer.html and the runtime
description agree: Transfer is Off / Fields / Property.

Circuitry's test first rejected an ID_TAGFIELDS-only fixture (all weights stayed
zero), then verified a moving Linear Field with native tag evaluation. Reading
GetAllHighlevelData after the source's native evaluation supplied the current
weights to a later Python Generator on the first pass: full / empty / gradient /
full, with 12 / 0 / 4 / 12 placed small parts. A second pass reused the same geometry.
No recursive ExecutePasses or document writes occur inside the generator callback.
This tests a native Linear Field/vertex-map setup in source-before-generator
ordering, not arbitrary stateful simulations or every tag-priority arrangement.

Evidence: CircuitBoardTool/tests/c4d_fields_013_smoke.py and
validation_python/fields013v1_outer.json, fields013v2_outer.json (density-count
refill flaw exposed), fields013v3_outer.json and installedFields013_outer.json.

## 143. Per-face normal offsets can close a concave joint despite local outline insets

Geometric pitfall demonstrated in Circuitry on C4D 2026.3.4: two planar source
faces met at a dip. Their independent normal-directed board lifts moved the
generated substrates into each other, even with a percentage inset on both
outlines. Validate finite thickness after final local frames and lifts, not only
source coplanarity or 2D outline gaps. Different-height coplanar neighbors also
need a finite 3D distance test; overlapping projections alone are insufficient.

The bounded correction measures board prisms, then clips each nearby pair into
opposite half-spaces separated by the requested distance. Generate mounting sites,
sockets, packages and routed copper only after those corrected outlines exist.
Recenter local clipped coordinates together with their frame origin so placement
grids and vertex-map sampling preserve world alignment. Work on private plans;
never fix this by editing the source or reentering scene evaluation from main().

In the real six-board fixture, evaluated substrate vertices confirm a 6.0000016
unit gap, both dipped boards retain about 65% area and their rebuilt ribbon joins
the cut edges. Native checks cover 1,480 ribbon edges against all six substrates,
Undo, parameter changes, save/reopen and unchanged source points/matrix. These
checks do not certify arbitrary tall package geometry or universal cable routing.

Area-only scoring can choose the wrong artistic cut (across the seam's length).
Constrain separators to face the neighbor. A folded inset corner in an older
logo also rejected one candidate: continue trying other supported separating
planes instead of aborting on the first candidate, while preserving an explicit
failure if no valid cut meets the trim limit. Exact fixtures and rejected/accepted
outer results: CircuitBoardTool/tests/test_python_clearance.py,
tests/c4d_clearance_014_smoke.py and validation_python/CHECKPOINT_014.md.

## 144. User-data static text can leave blank gadgets and clip multiline Help

Confirmed by artist screenshots in C4D 2026.3.4, Circuitry 0.15. Using instructions
as DTYPE_STATICTEXT names produced instruction labels plus empty controls. Moving
a wrapped paragraph into the static-text value round-tripped correctly via the
API, but still clipped text under the parent title and drew an empty-looking box.
The earlier hypothesis in #141 that short labels alone solve the Help layout is
therefore incomplete; stored/native descriptions do not certify its visual layout.

The accepted replacement is DTYPE_STRING / CUSTOMGUI_STRINGMULTI, with a blank
DESC_SHORT_NAME, DESC_SCALEH true (ALIGNLEFT/FITH false), animation off, and
DR_MULTILINE_READONLY, WORDWRAP, NO_BORDER, NO_DARK_BACKGROUND, NO_SCROLLBARS true.
Set DR_MULTILINE_LINECOUNT to an appropriate initial paragraph line count and
initialize the actual string parameter. Old static rows are hidden. The artist
explicitly confirmed the corrected layout looked right. Parent-first user-data
ordering from #141 remains required; preserve parameter values when reordering.
This verifies this native Help presentation, not every multiline custom GUI flag
or all Attribute Manager widths. Evidence: CircuitBoardTool/c4d/circuitry_live_ui.py,
validation_python/help015trial_outer.json, help015wrapping_outer.json and
CHECKPOINT_015.md; the artist's screenshots of the package and shape help show the rejection.

## 145. BaseBitmap.ScaleIt can replace destination alpha-channel wrappers

Observed in C4D 2026.3.4 while reading a transparent PNG for a Python Generator.
Allocating a private destination bitmap, AddChannel(True, True), then ScaleIt
left the earlier channel wrapper invalid. GetAlphaPixel with that wrapper raised
ReferenceError: the object c4d.bitmaps.BaseBitmap is not alive. A small in-memory
probe did not fail, so it was insufficient evidence for real-file use.

Reacquire destination.GetInternalChannel() after ScaleIt and use that wrapper for
pixel reads. The corrected native PNG path passes live generator evaluation,
alpha/luminance fixtures, cache reuse, modified-file reload and save/reopen.
Source images and decoded buffers remain private; no document writes occur in
the generator callback. Evidence: CircuitBoardTool/c4d/circuitry_images.py,
tests/c4d_images_015_smoke.py, validation_python/images015v1_outer.json,
images015bitmapFailure_outer.json, images015v2_outer.json and images015installed_outer.json.

## 146. SetMl loses shear; source-following generators may need two transform factors

Verified in C4D 2026.3.4. inverse(generator Mg) * source Mg * source-local frame
is the correct affine map, but assigning it directly with BaseObject.SetMl can
lose nonorthogonal components when C4D decomposes it into native rotation/scale.
Rotated objects under differently non-uniformly scaled parents expose this even
when every artist object has ordinary transforms. Circuitry's first native
placement fixture drifted by roughly 2.5 units; a minimal arbitrary-matrix probe
confirmed SetMl changed the supplied basis.

Factor the linear map into two column-orthogonal matrices (e.g. U*Sigma and V^T
from a small SVD), then assign them to two private parent nulls. Apply translation
only on the outer null. Their product retains source scale, reflection and
inherited shear while each native object has a representable transform. Preserve
canonical geometry before placement so transform-only edits do not reroute.
Do not modify an already-owned generator cache. Reject singular transforms.

Evidence: CircuitBoardTool/c4d/circuitry_transforms.py,
circuitry_spline_placement.py, tests/test_python_transforms.py,
tests/c4d_spline_placement_016_smoke.py, validation_python/placement016v1_outer.json,
placement016v2_outer.json, placement016shearProbe_outer.json,
placement016v3_outer.json and placement016installed_outer.json. Tests include
mirrored/scaled parents, source offsets, fit, solos, Undo and save/reopen. This
does not guarantee behavior of downstream effects that deliberately inspect or
replace those internal transform nulls.

## 147. SplineHelp retained line points can differ from evaluated deformed vertices

Observed in a C4D 2026.3.4 linear spline with Bend. The source's GetDeformCache
was None; source.GetCache returned a LineObject whose own GetDeformCache held the
deformed line. source.GetRealSpline still exposed original spline points. Reading
only that spline therefore ignored deformation while returning valid-looking data.

Initialize SplineHelp with SPLINEHELPFLAGS_USERDEFORMERS (omit GLOBALSPACE for
source-local results). In the tested fixture, GetPosition and
GetVertexMatrix(index).off matched the deformed vertices. Even with USERDEFORMERS
and RETAINLINEOBJECT, GetLineObject().GetPoint still returned original vertices.
For exact linear corners, read GetVertexMatrix(index).off; obtain segment sizes
with SplineHelp.GetSegmentCount/GetVertexCount. Python LineObject does not expose
GetSegment. Retained line point count remains useful as a consistency check.

Circuitry now samples these evaluated vertices, then checks planarity and retains
the source-local frame before applying the source hierarchy. A native Star and
planar Bend passed independent deformed-line world-point comparisons with max
error 1.49e-13 units; strength changes matched on the first source-before-generator
pass. This does not make nonplanar deformations valid single board outlines.
Evidence: CircuitBoardTool/tests/c4d_spline_inputs_016_smoke.py,
validation_python/placement016inputs*_outer.json (rejected assumptions included),
placement016sourceFinal_outer.json and placement016installed_outer.json.

## 148. Verify a command's native result as well as its return value

In this C4D 2026.3.4 bridge session, calling the registered Circuitry command
1070114 returned None while successfully creating its native Opython object.
A lifecycle test using `assert c4d.CallCommand(1070114)` therefore failed before
checking the valid result. A corrected probe inspected the new object's type,
parameter revision/defaults, resolved description and evaluated cache; all passed.
This observation is scoped to that host/command invocation, not a claim that every
CallCommand returns None or a change to the documented API contract. The reason
for the return value was not isolated. Do not retry a command solely because its
return is falsey: inspect whether the intended object/action already occurred.
Evidence: CircuitBoardTool/validation_python/density017lifecyclefinal.json and
density017lifecyclefinal2.json (rejected and accepted outer results).

## 149. FieldList userdata needs retained owners and semantic migration comparisons

Verified in C4D 2026.3.4. Reading FieldList userdata returns native wrappers which
compare unequal with == even for an unchanged empty list. A blanket userdata
equality assertion can therefore report a migration failure when no data changed.
For auditing, snapshot GetFlags and each recursive layer's type/name/GetData copy,
children and GetMaskHead hierarchy while retaining the owning list. A linked
Linear Field retained identical layer data, link and strength through UI upgrade;
sample output, Undo/Redo and document save/load also passed. This does not imply
arbitrary custom userdata can be compared using the same schema.

Chaining read_values(node)['Density Fields'].GetLayersRoot().GetDown() without
retaining the FieldList caused a dead GeListHead ReferenceError in a native test.
Hold the FieldList and root explicitly while traversing borrowed native layers.
GetLayersRoot returns a GeListHead whose GetDown is the first layer, not a layer
itself. GetLinkedObject takes the document. FLfield rejected an Opython link in
the fixture; native Field objects are c4d.modules.mograph.FieldObject instances.

Evidence: CircuitBoardTool/tools/c4d_userdata_audit.py,
tests/c4d_density_fields_018_smoke.py, validation_python/fields018eqprobe_outer.json,
fields018source2_outer.json, fields018layerprobe_outer.json and
fields018finaltest0_outer.json. These include rejected assumptions and accepted
native source/installed tests. Direct sampling uses world positions and identity
FieldInput transform; translated generators and followed rotated splines pass.


## 150. Redshift material Undo must not duplicate graph Undo entries

Verified in C4D 2026.3.4 with Redshift 2026.7.1. Circuitry wrapped material
changes in document StartUndo/AddUndo(UNDOTYPE_CHANGE, material)/EndUndo and
also used graph transaction UndoMode.ADD. Undo restored the prior look but Redo
left the Redshift base-color port at its old value while generator controls
advanced. With the complete material Undo retained and transaction UndoMode.NONE,
both Undo and Redo restored matching control and shader values for all five looks.
This is specific to a caller already recording a complete material Undo, not a
blanket instruction to disable graph undo in node editing tools.

A normal Mmaterial plus NodeMaterialReference.CreateEmptyGraph in the Redshift
node space, explicit Standard Material/output nodes and a surface connection
worked. BaseMaterial(1067447) alone did not initialize that graph. GetPortValue
may return a maxon.Float64 wrapper; convert with float() for numeric assertions.
Evidence: CircuitBoardTool/validation_python/materials020next1_outer.json,
materials020manager5_outer.json (rejected Redo), materials020manager6/7_outer.json
(accepted lifecycle). Actual renderer appearance was not validated by these tests.

## 151. Programmatic userdata changes can synchronously call description handlers

Circuitry material preset application updated its own renderer/look userdata.
Guarding only its MSG_DESCRIPTION_POSTSETPARAMETER handler was insufficient:
a direct API call to apply set parameters, entered the handler synchronously
and constructed a second material bank before the outer call committed. Guard
the public mutation entry point as well as the description callback. A per-object
reentrancy guard eliminated the duplicate bank in the native 15-look test.
Userdata ordering code must also exclude DTYPE_BUTTON and DTYPE_SEPARATOR when
copying values; buttons have descriptions but no readable parameter value.
Evidence: CircuitBoardTool/validation_python/materials020manager_trace_outer.json,
materials020manager3_outer.json and materials020manager6/7_outer.json.


## 152. New documents can already contain the requested renderer video post

In C4D 2026.3.4 / Redshift 2026.7.1, a fresh BaseDocument's active RenderData
already contained video posts 1036219 (Redshift) and 1054755 (Magic Bullet Looks).
A preview helper unconditionally inserted another Redshift post. RenderDocument
then hung; the Redshift log reported Context: Locked:Render followed by
Context: Busy:Render Waiting until idle. Native readback confirmed two Redshift
posts in the test and one in a separately allocated fresh document. A private
three-cube C4DThread fixture reproduced it without any Python Generator present.
The thread remained in RenderDocument after a custom TestDBreak deadline and an
explicit End(False). Main-thread bridge commands still ran in the asynchronous
fixture, allowing artist documents to be saved before a requested restart.

Walk RenderData.GetFirstVideoPost/GetNext and reuse the post with the requested
type. Insert a BaseVideoPost only if missing. Do not mutate or destroy the private
render document while its render thread is still active. Duplicate removal before
rendering is the correction, but post-restart corrected render acceptance remains
pending at this entry's creation. Do not claim a material graph or API fixture
proves actual rendering. Source helper code now has phase markers and saves the
prepared scene before rendering. Synchronous generator preview was the first hang;
its exact stopping call was not instrumented and must not be asserted retroactively.

Evidence: CircuitBoardTool/validation_python/materials020doubleVP_outer.json,
materials020minimalRSstatus_outer.json, materials020minimalRScancel_outer.json,
materials020rendererRecoveryBackup_outer.json; tools/c4d_async_material_probe.py.

## 153. Userdata material links alone do not protect generator materials from Delete Unused

Verified in C4D 2026.3.4: a material referenced only by a userdata BaseLink was
deleted by native Delete Unused Materials (CallCommand 12168). A real TextureTag
on an object retained its material even when its restriction named a nonexistent
selection. The final Circuitry fixture kept all 31 linked material roles using
real per-role TextureTags with NBIT_OHIDE and an unmatched restriction on the
generator, while a truly unused material was removed. Shading tags remain on
actual generated geometry. Generated cache tags had not protected those roles.

Synchronize these reference tags on the main thread when role links change,
including Undo/load and native replacements. Unlinked old material banks remain
eligible for cleanup. This is a demonstrated native reference mechanism, not a
reason to defer material usage correctness to a C++ port. Renderer appearance
and direct Alt-drop gestures remain separate from tested equivalent link edits.
Also explicitly use DESC_CUSTOMGUI=CUSTOMGUI_BUTTON for userdata DTYPE_BUTTON;
Circuitry's stored/resolved descriptions previously existed without visible
buttons. After that correction the artist confirmed both design buttons visible.
Evidence: CircuitBoardTool/tests/c4d_runtime_021_smoke.py,
validation_python/021installedsuites_outer.json, upgrade_021_live.json.

## 154. Managed spline identity, zero points and current private deformation

In the C4D 2026.3.4 fixture, SplineObject.GetGUID changed after ResizeObject;
GetGUID is a checksum, not sufficient evidence of BaseList2D replacement. Test
identity with retained object equality and a persistent native BaseLink instead.
ResizeObject(0,0) succeeded but SetAllPoints([]) raised Invalid object length.
Skip SetAllPoints for empty managed outputs and retain artist tags/children.

Copying the trace spline and its Bend into a Python Generator's private output
did not by itself evaluate the deformation. Direct SplineHelp on a detached copy,
and a dummy-document copy without a modeling pass, were rejected. The final
implementation uses SendModelingCommand(MCOMMAND_CURRENTSTATETOOBJECT) on a
copy with current points inside an unlisted dummy document. This is the explicit
worker-safe modeling pattern in the local 2026.3 c4d.utils SDK documentation.
It does not ExecutePasses or alter loaded-document hierarchy in GVO. Local Bend
was verified against the real native evaluated curve. External targets, Fields
and simulation context were not certified by this bounded test.

After a seed change and main-thread point publication, immediate real-child
SplineHelp/Oline deformation was still stale (292.55 units in this fixture).
The next host pass converged. The FIRST private generated deformation was already
current and matched the later real result with 0.0 error. Preserve these separate
observations; manual ExecutePasses convergence does not certify an untouched
viewport or external consumer timing. Registration/automatic event acceptance
for Circuitry's new MessageData 1070276 remains pending a startup in this entry.
RegisterMessagePlugin from MCP exec failed with cannot find pyp file, even when
using an existing PYP command class's globals; use normal plugin startup.
Evidence: CircuitBoardTool/tests/c4d_runtime_021_smoke.py,
validation_python/021guidprobe_outer.json, 021runtime1..18_outer.json (rejected
and accepted trials), 021installedsuites_outer.json (final installed acceptance).

Startup follow-up: after the artist accepted the old render-cancellation dialog,
normal Quit remained unresponsive. With all documents saved and verified, the
stuck process was ended; PID46868 loaded both Circuitry registrations normally.
A small owned loaded document then received its 183-point managed spline via
SpecialEventAdd automatically in 16 ms. Later calls were no-op with stable hash
through 5.7 seconds. Disk evidence was captured before any second bridge request.
The initial snapshot used explicit private L.evaluate; no manual flush or
ExecutePasses. This verifies queue dispatch while leaving actual UI/viewport and
external-consumer acceptance distinct. Evidence: CircuitBoardTool/tools/
c4d_update_handler_021_probe.py and validation_python/
update_handler_021_before_followup.json, restart_021_verified.json.

## 155. Renderer-followable material ownership cycles and persisted identity

Circuitry native stored a generator BaseLink inside material metadata, while
the generator referenced that material through role links and TextureTags.
An Octane Studio+1.9.3 crash report on C4D2026.3.4 showed C00000FD stack overflow
and repetitive Octane frames; its log stopped at material creation. This cycle
is a strong suspect, not a symbolized diagnosis of third-party internals.
Replace bookkeeping backlinks with a persistent text identifier, and migrate
legacy material metadata on main-thread document import before rendering.
Do not remove real forward material usage references needed by Delete Unused.

Using owner.GetGUID as the text identity was rejected: a value computed during
native document-import migration differed from the finalized object's GUID.
Persist an explicit owner token in the generator container. Clone bank ownership
must fork on preset editing without modifying the other owner's materials;
render-document clones need not fork. Save/load, duplicate, Undo and custom
replacement checks are distinct from material shader-value tests.

After removing backlinks, actual small and recovered-scene Octane shaded frames
completed successfully at384x384/16samples. This does not certify sustained Live
Viewer or explain separate earlier hardware-render access violations. Evidence:
CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_013.md and its raw artifacts.

For bounded Octane QA, BaseVideoPost1029525 provides initialized defaults;
an empty doc[1029499] container can silently select Info Channels (kernel0).
Runtime description confirms kernel1 Direct Lighting. Set both VP overrides and
document sample controls, and inspect image/log for actual beauty kernel/samples.
The installed old example mentions SET_DEF_ENV_COLOR, absent from this runtime;
the current local header has SET_ENV_COLOR3190. BaseBitmap.GetPixel returns lists,
so convert to tuples before unique-color set assertions.

## 156. Native GVO must reuse unchanged output and track external inputs

Circuitry/C4D2026.3.4 rebuilt every GetVirtualObjects call. The artist's live
flower board had98398 polygons, but unchanged private-document ExecutePasses
took1.81..2.00seconds repeatedly. Native cache dirty counter increased while
the point hash stayed identical. Python object equality reported the cache as
equal across rebuilds: wrapper equality alone is insufficient cache evidence.

Per-ObjectData transient state now records generator DATA dirtiness plus a light
dependency signature of relevant source hierarchy/caches/tags/world matrices,
linked masks and FieldList.GetDirty(document). CheckDirty marks DATA only after
a real dependency change; GVO returns op->GetCache when state matches and
CheckCache(hh) allows reuse. Never mutate the published cache to report hits.
Build serial/time stamped on newly allocated output provide separate evidence.

Same exact scene after fix: initial1.623seconds then0.982..2.262milliseconds per
unchanged document pass, identical96782-point hash, stable cache dirty counter.
Camera/disabled-Field moves do not rebuild. Seed/source/parent/active Field/tag
edits rebuild then return to cache. Field time is tracked conservatively for
procedural layers; static boards without active Fields ignore time. Nested
source spline edits inside real Extrude/Voronoi update without an owner nudge.
This is evaluation evidence, not viewport FPS or render timing. Artist handles
actual presentation/render acceptance. See CircuitBoardTool validation_native/
CHECKPOINT_NATIVE_015.md, before/after timings and cache lifecycle tests.

Save lifecycle follow-up: after assigning a NEW verified checkpoint path/name,
native Save command12098 cleared the save state and normal Quit12104 completed
without the previous extra save dialog. Verify GetCommandName before invoking
IDs:12112 is Select All. Reverify the new saved file before quitting; never use
this to overwrite an artist original or bypass a render-cancellation dialog.

September14 follow-up: CallCommand(12098) returned false in one already-saved
checkpoint workflow while GetChanged() was false and the new checkpoint had
loaded with the expected object/material counts. Do not assert on that command
return alone; verify the saved file and document change state before normal Quit.

## 157. A serialized cycle value also needs its resource entry

Native Circuitry016 compiled a new enum value and its handler successfully, but
its generated .res CYCLE omitted that entry because a whitespace-sensitive text
replacement did not match. GetDescription lacked value3, and node[parameter]=3
raised "parameter set failed" in C4D2026.3.4. Make the resource generator own the
complete cycle, then check runtime DESC_CYCLE and parameter assignment. A C++
enum and a localized string alone do not expose a usable choice. Resources are
part of installed build identity even when the DLL hash remains unchanged.

The same host suite caught UNIT PERCENT with resource MAX1: setting0.25 through
the API clamped to0.01. A resource percentage range uses human percentage units
(MIN0/MAX100 for stored0..1), unlike the stored BaseContainer float. Direct
container writes bypassed this clamp and restored geometric parity, establishing
the resource as the cause. Test public parameter writes/readback, not only the
planner or direct container access, before declaring a restored controller usable.

## 158. Fragment parity needs contour traversal and complete containment

Circuitry017's actual Extrude/Voronoi fixture exposed two gaps that simple
rectangle tests missed. Normalizing a planning contour's winding changed which
mounting corners Python's ordered subsampling chose. Preserve input traversal
for planning; normalize independent polygon publication as needed. Separately,
checking a package's corners/edges against holes misses a hole fully covered by
the package. Test hole containment as well as perimeter intersections. Frozen
failing artist fragments reproduced the omission without Cinema; host tests then
matched all ten fragments at100/200% density. See CHECKPOINT_NATIVE_017.md.

For nested diagnostic BaseContainers in C4D2026.3.4, iterating children obtained
from a temporary parent gave zero-valued child reads in this fixture. Keeping
the parent container alive and obtaining each child with GetContainer(index)
returned the correct native data. Establish container ownership/lifetime before
changing production code in response to apparently empty cache metadata.

## 159. Verify a terminated Windows process remnant before DLL deployment

Normal Cinema Quit on September14 left a child process entry with0threads,
0handles, no window/path, an exited parent and~0.4MB working set. OpenProcess
returned AccessDenied; no new crash report appeared. Get-Process by name alone
therefore blocked the installer even though the installed plugin DLL could be
opened exclusively for read/write (the probe changed NO bytes).

The CircuitBoardTool installer now permits this narrowly verified remnant case:
every matching entry must report zero threads/handles and an absent parent, and
the installed image must pass that exclusive open. Missing metadata, active
threads/handles, live parent or locked image still stops installation. This is
not permission to ignore inaccessible live processes, rename a loaded DLL,
terminate through dialogs or discard scene preservation. The artist documents
were saved/reloaded before the normal Quit. Successful new PID45372 loaded the
expected018 DLL and passed host tests afterward; see CHECKPOINT_NATIVE_018.md.

## 160. Cable body refinement can preserve connector geometry

Circuitry018 resamples its existing analytic cable curve and increases native
wire radial segments instead of placing the whole generator under SDS. Host
levels0..3 retained byte-identical housing/board/trace/package geometry hashes,
cable attachment coordinates and ribbon end-section corners. Native wire radius
and centerlines also matched Python. This preserves square ribbon cross-sections
and hard socket housings while increasing curved-body resolution. It is not
Catmull-Clark subdivision and should not be labeled as an SDS object.

The same actual-logo fixture showed that an unnecessarily broad circular socket
keepout plus a fixed-width socket eliminated the nearby mounting edge. Exact
oriented-rectangle/circle clearance and smaller sockets only where needed fixed
the long jump. Relaxing short-edge sampling alone did not fix it. Keep rejected
hypotheses distinct from demonstrated causes; validate against actual geometry
and the user's source, not just a connected-network count.

## 161. Publish real generator children through the actual main-thread queue

Circuitry019–021 publishes a real editable trace-spline child from a native
generator. GVO only stages immutable point/segment data. A coalesced Maxon Job
on GetMainThreadQueue scans loaded documents and changes children there. The job
captures no object/document/plugin-instance pointer: Undo, removal and close can
retire them before it runs. ExecuteOnMainThread can execute inline and is not a
guarantee of deferred scene mutation. Shutdown cancels/waits the owned job.

Use forward and backward owner links, validate hierarchy, and preserve artist
tags, name, transform and local deformers while resizing the owned spline.
Detached output stays independent. A private cache clone is refilled from the
fresh snapshot; local deformers evaluate through Current State to Object in an
owned unlisted document. Do not recursively ExecutePasses from GVO. External
deformer targets and arbitrary feedback hierarchies are not thereby certified.

Lifecycle tests must allow the real event loop to run between phases. Eleven
immediate bridge calls inside one sub-second orchestration were too tight: the
initial publication caused a single settling rebuild (serial 2 to 3). A proper
spaced run settled and measured about 0.15ms unchanged passes. This is different
from continuous idle rebuilding. Inspect bridge inner text: an outer isError
false can contain "Python execution error"; stop dependent test phases on that
failure. Evidence: CircuitBoardTool native_output_019/021 reports and raw output.

## 162. A custom image filename needs asset and rename messages

Circuitry021's native object implements MSG_GETALLASSETS with AssetData.Add
(owner and parameter ID), plus MSG_RENAMETEXTURES and
MSG_MULTI_CLEARSUGGESTEDFOLDER through HandleModifyFilenameMessage. A filename
control alone did not establish scene asset collection. Staged-plugin testing
verified GetAllAssets and Save Project with Assets, copied the image, rewrote
the relative path, reopened and regenerated the scene without the prototype/MCP.

Resolve relative paths against the scene directory and tex folder, use UTF-8
filesystem paths, and fingerprint resolved path/mtime/size for idle invalidation.
Cache both successful decode/contours and invalid-input geometry/status until
inputs change. Width/material changes should not retrace unchanged pixels.
For embedded Python test scripts, explicitly read UTF-8; default Windows decoding
can turn a correct Unicode fixture name into mojibake before native code sees it.
Evidence: native_images_021.json and release_clean_c4dpy_021.json.

## 163. c4dpy functional success and shutdown success are separate gates

On this Windows/Cinema2026.3.4 installation, isolated native021 package checks
completed successfully without the Circuitry Python/MCP plugins. After an
explicit sys.exit(0), c4dpy returned -1073740791 (0xC0000409). A minimal script
with Circuitry absent (FindPlugin returned None) failed at shutdown with the
same code. The underlying host/other-plugin cause is undiagnosed; do not assign
it to Circuitry or a renderer without further evidence. The functional report
proves the tested operations; it does not prove clean host exit or qualify
headless/Team Render support. Preserve both process exit code and script report.
Evidence: CircuitBoardTool validation_native/headless_shutdown_021.json.

## 164. A cancelled SDK modeling result must not become a cached input error

Circuitry021's live 300% Flower board was blank with an idle cached copper
triangulation error. The preserved input rebuilt at 100/200/300/500%, and a dirty
retry restored the artist board without parameter changes. The original host
break flag was not recorded: cancellation as the trigger is inferred. What was
demonstrated is that a transient failure had become indefinitely cached.

The GVO implementation had cached all std::exceptions without checking the
HierarchyHelp calling thread. Native022 checks TestBreak before heavy trace
work/publication and again before classifying SDK null returns as invalid input.
On cancellation it unwinds private allocations, retains the last trace snapshot
and sets retry state for CheckDirty. Only completed builds and actual input
errors become idle cache states. This prevents cancellation from being treated
as a permanent invalid contour while preserving invalid-image error caching.

Do not infer cancellation-test success from a private worker finishing cleanly.
Overridden Python C4DThread.TestDBreak received zero callbacks during these
ExecutePasses attempts, and StopAllThreads did not interrupt that unlisted
worker's generation. Those attempts did not exercise the guard. Record an
actual aborted-build counter or equivalent before claiming that gate. Fresh
native Flower/Field fixtures passed nine density/coverage combinations and idle
hash checks; direct artist slider interaction was requested separately.
Evidence: CircuitBoardTool CHECKPOINT_NATIVE_022.md and its linked JSON reports.

## 165. Coverage filters can conceal shared-edge topology failures

Circuitry023 investigated an artist remesh with only 27 points / 52 triangular
faces. Upward coverage worked; All Faces produced no output. Two source edges
had four incident faces each. The normal filter removed enough participating
faces to hide the extractor's global non-manifold rejection. Polygon budget or
non-planar quads did not explain this particular repro. Preserve exact topology
and committed controls before simplifying the source or offering density advice.

For this planar-patch generator, treating junction faces as independent boards
retained every valid candidate. Merely blocking the ambiguous edge in adjacency
was insufficient: alternate adjacency paths can merge the same faces again.
The extractor isolates all faces incident to the junction from region merging,
while keeping ordinary manifold region/contour behavior. This is a product-level
policy, not a general C4D modeling repair; it leaves the input mesh untouched.

The next stage exposed a second all-or-nothing error: one pair could not meet
the configured clearance/trim limits. Native fitting mode now omits the later
candidate and reports it explicitly, removes it before connector planning, and
uses stable source identities for population seeds. It never lowers requested
clearance. The strict core mode remains available and covered by parity tests.
In both Faces/Planar Regions, exact host tests produce 51 boards at clearance 6
(one reported omission), all 52 at clearance 0.5, and stable idle caches. Clone,
cold load and ordinary-density Voronoi/Python differential checks pass. This does
not certify warped quads, every invalid contour, or useful population on tiny
curved faces; release and artist interaction acceptance remain separate gates.
Later on September 17, the artist directly confirmed the Upward / All Faces switch
stays visible in the viewport; that interaction gate passed for this fixture.
Evidence: CircuitBoardTool CHECKPOINT_NATIVE_023.md, native_surface_023.json and
surface_core_023.json. Tests used owned unlisted documents and no renderer calls.

## 166. Surface detail normalization must include the entire design space

Circuitry024's torus repro had 20 valid panels around 26–61 units across, ample
population budgets, but only two packages and no small parts or routes. Scaling
package bodies alone left absolute package margins, pin extents, row spacing and
routing constants sized for the original large board. More requested parts did
not create valid sites. At least one fitted package is required by this planner
before it creates small-component banks and their circuit graph.

Normalize the actual planning outline and every spatial mask/keepout into a
consistent design space, run both planner and router there, and map all emitted
detail back through a uniform scaled frame. This includes package subgeometry,
pins, passives, copper thickness/layers, vias and managed spline coordinates.
Convert the physical board-top offset into design units before mapping; otherwise
the populated surface floats or sinks as scale changes. Diagnostic route metadata
must use physical coordinates too. Do not normalize only centers or only widths.
Board mechanical thickness, mounts and cables remain separate explicit controls.

Native024 provides default-on Shape/Normalize Surface Detail (2018), with OFF
retaining the old assembly look. The exact torus at 100% now populates 20/20 panels
(60 packages, 600 parts, 1,202 routes). A fresh object also populates all 20. Physical
panel sizes 3/30/300/3,000 retain the same part/package/route counts; copper bounds
and trace-spline mid-height align. Match copper and spline routes by stable IDs:
layer/role mesh traversal order differs from planner segment order. Field tests
must cut the actual selected population; the first 20 torus faces were not
symmetric about world origin. A Field at population median produced partial
coverage and returning to full restored the exact geometry hash. These are numeric
geometry checks, not render or appearance acceptance.

Evidence: CircuitBoardTool CHECKPOINT_NATIVE_024.md and linked test reports.
Native024 also corrected misplaced native023 foundation test code: the old log
had build errors despite a recorded pass. Always check the actual compiler exit
and run the newly built executable; a PowerShell wrapper's final successful
command or stale LASTEXITCODE can misrepresent the preceding operation.

## 167. Populated panels are not necessarily visually filled panels

Circuitry024's scale fix placed 600 parts on all 20 torus panels, but the artist
still rejected the look as sparse. Each face had only 10–46 allocated parts on a
fixed 300-unit design canvas. Nearest-chip-first placement consumed those budgets
in central rows, and satellites also clustered near the hero. A count-based
acceptance gate missed the actual composition failure.

Native025 sizes normalized design space to the requested population and its
packing envelopes, using sampled usable area to handle overlapping/external
keepouts without double subtraction. It distributes satellite anchors and short
regular-grid component banks over regions of the face. A distribution-only
candidate left tiny remote islands and was rejected before deployment. The final
version retains count caps, contour/clearance checks, field thinning and legacy
behavior with normalization disabled; board/socket/ribbon geometry hashes match.

At the same 100% / 600-part budget, exact torus output has 599 parts / 60 packages
on 20 panels; mean route bounding-rectangle fraction grew ~0.098 to ~0.474. This
is layout extent, not physical copper area or a rendered coverage percentage.
Builds measured ~1.1s at 100%; unchanged output remains cached. Dense 500% rebuilding
still took ~23s in this session, so no universal performance improvement is claimed.
Numeric scale/mask/persistence/spline checks passed. Crucially, the artist then
confirmed **"This fills the panels well"**. Preserve that actual artist acceptance
alongside the metrics. Evidence: CircuitBoardTool CHECKPOINT_NATIVE_025.md and
surface_fill_025_before/after.json. No Cinema render calls were used.

## 168. Uniform output scaling does not repair upstream package proportions

Circuitry025 scaled package/copper/passive geometry through a uniform surface
frame, but the planner's fixed 14/8-unit carrier heights survived manual footprint
reductions to 10/5 units. Actual package geometry was ~2.3 times as tall as wide.
Scaling all three axes faithfully preserves that bad ratio. Native026 derives
carrier/housing height from fitted footprint relative to the original 160/60-unit
package references, counting density/fit once. Initial live geometry checks show
~0.27–0.28 height/width across 3/30/300-unit panels. Existing accepted panel coverage
and legacy normalization-OFF differential checks remain unchanged.

Hardware was a separate omission. Native026 uses panel short side / 300 for all
mount dimensions and seating/keepout offsets, independently of density or Fields.
This prevents density edits moving holes or changing supports. Top/bottom contact
planes, height edits, scale invariance and density stability passed live checks.
Board thickness/lift/clearance/cables remain explicit scene units, not normalized.

A follow-on audit found that fixed .45 pin-lead thickness can protrude below the
new small carrier. The compiled final correction caps lead thickness to a quarter
of carrier height, only in normalized mode. Its install/live acceptance is pending
as of this entry: Cinema commands were cancelled-before-start during artist work.
Do not promote the initial host pass to validation of this last compiled change.
See CircuitBoardTool CHECKPOINT_NATIVE_026.md for candidate provenance and status.

Amendment: the combined native026 build is installed on GUI PID69064. Pin lower
bounds, production primitive pins, height edits, mount seating and density-stable
hardware pass live checks. Final surrounding height/width is ~0.258–0.262 after
removing below-board lead thickness; primary ~0.276. The artist checkpoint is
restored. These are geometry checks; visual/renderer acceptance is still pending.

## 169. Connector meshes need graph terminals; spline output must omit solid vias

Circuitry sockets/cables were mechanically connected to panels, but no terminal
in the circuit graph represented them. Reserving the housing as a routing keepout
cannot create a feed automatically. Native026 adds inward-facing feed terminals
just outside each socket reservation and attaches them to nearby already-routed
internal nets, reusing net identity. Incremental routing reserves existing copper
and leaves every original path/layer unchanged. A short explicit contact tail
enters its own socket housing; routing beyond that tail uses normal obstacle and
copper clearance checks. Failed feeds stay explicit in Build Status (metadata147).

Both geometry and spline output include these new feed paths. The old via loop
ran even when trace geometry was replaced by splines, leaving 10-sided solid posts.
It now runs only in geometry mode and produces hollow chamfered barrels at quality-
dependent radial resolution with Phong smoothing. Barrels crossing part bodies or
leaving the contour are omitted. These are visual plated details, not physical
through-board drilling or certification of electrical correctness.

Live native026 checks: two-panel fixture has six routed feeds, both sockets fed;
32 via meshes are closed/manifold with annular topology (Euler characteristic 0).
Spline mode keeps all 181 route segments and no solid vias. Restored artist torus
has 19 links, 114 feeds and zero unfed sockets, retaining accepted internal layout
and population. Preserve these deliberate additions when comparing legacy Python:
exclude only CONNECTOR_ branches from old-route assertions, not all copper checks.
Evidence: CircuitBoardTool CHECKPOINT_NATIVE_026.md final section, successful raw
envelopes, connector_vias_026.json and native_final_state_026.json. Artist rendering
and visual acceptance remain separate gates.

## 170. Multiple spline segments are not automatically holes; normalize after source extraction

Circuitry native027 corrected a merged two-island spline that produced only one
populated board, while the same source under Extrude correctly produced two.
Evaluated curve sampling was accurate: the mistake was declaring every contour
after the largest one a hole. Classify closed coplanar loops by containment depth,
independent of winding: even depth is a board, immediate children are holes, and
islands within holes are boards. Validate segment crossings/touching first; a
single containment point does not distinguish a hole from an overlapping loop.
Retain each region's own center/frame while applying whole-source Fit uniformly.

Run the resulting BoardInputs through shared planning, normalization and assembly
logic. Source-type checks around detail scaling caused tall spline packages even
after the equivalent surface path was fixed. Native027 uses the same normalized
population space, footprint-relative package heights and panel-relative hardware
for built-in/spline/mesh/image boards; legacy sizing remains an explicit OFF mode.
Multiple spline islands share existing socket/clearance/cable/feed generation.
Source-specific eligibility and masks still belong in the source adapter.

Evidence on C4D2026.3.4 GUI PID67184: exact artist comparison gives2 populated
boards/600 parts/6 packages/1 cable/0 unfed sockets in both modes, unchanged
Extrude geometry hash. Nine built-in/spline/surface cases across40/400/4000 units
match scale, population and package ratios. Nested-hole/island, source transforms,
Fit, source-link redirect and roundtrip checks pass. Six curved-spline boundary
checks retain Python agreement (<1e-12 error), and14 cache checks pass. See
CircuitBoardTool validation_native/CHECKPOINT_NATIVE_027.md and raw envelopes.
An initial3-unit built-in test silently clamped to its existing40-unit UI minimum;
cross-source fixtures must use the common exposed dimension range. No renderer
or actual UI-drop acceptance is implied by the API link-assignment checks.

## 171. Rotate the planning space, not the completed circuit geometry

Circuitry native028 adds one coherent layout angle per panel. Rotating an already
fitted component hierarchy would break boundary, mounting and socket clearances.
Instead, rotate the physical planning outline and its keepout holes by the inverse
angle, fit and route in that coordinate system, then rotate the detail emission
frame back into the original source frame. Normalize before rotation so an angled
bounding box does not itself resize the design. Physical board/mount/socket/cable
geometry stays unchanged. Map socket terminal positions, directions and contact
tails into the same planning frame; map copper path metadata back to the physical
board frame as well as transforming the visible geometry.

Density needs the same coordinate contract. An axis-aligned Field grid cannot
simply inherit the rotated bounds: sample through a layout-to-original-grid
transform. Barycentric vertex-map triangles can instead be inverse-rotated into
the planning frame. This retains masks in source space when layout angles change.

Live evidence on C4D2026.3.4 GUI PID56572: fixed23/45/90-degree layouts and seeded
free/45/90-step variants retain exact mechanical geometry hashes and six socket
feeds with zero unfed sockets. All copper stays within the panels; 192 spline
routes align with their corresponding rotated copper meshes. A hard X Field stays
fixed while the layout rotates23 degrees. Core tests cover barycentric mask values.
Returning controls to zero restores the prior geometry exactly. Actual artist027
spline/Extrude fixture hashes are also unchanged with new features disabled.

The companion Max Fit heuristic uses existing packages/components as obstacles
and two smaller-component passes within the requested budget. Core fixture80->106,
live full-generator fixture79->102; main packages unchanged in each. These are
different settings, not a promised universal count. Live descriptions, Randomize
retention, Reset/Undo, cache reuse and save/reopen pass. See CircuitBoardTool
CHECKPOINT_NATIVE_028.md and layout_028_outer_live.json. No renderer or viewport
appearance claim follows from these numeric checks; artist preview is separate.

## 172. A Cloner cache may contain generators and instance references, not polygon children

Circuitry's old mesh reader evaluated the linked source once, then walked only
GetDown/GetNext. A real artist Cloner produced Null -> Plane primitive -> polygon
cache, so it found no polygons although all nine planes were visible. At every
node prefer GetDeformCache, then GetCache, replacing that node's input hierarchy
with its evaluated output. Recurse those caches as well as ordinary children.
Do not traverse the linked source's unrelated document siblings.

Render instances require mesh occurrences, not only polygon pointers: retain the
referenced polygon plus its mapped world matrix. InstanceObject::GetReferenceObject
resolves the source, and GetInstanceMatrices supplies GLOBAL matrices for multi-
instances (documented in pinned2026.3 lib_instanceobject.h). Transform reference
world geometry by instanceWorld * inverse(referenceWorld). Apply any outer instance
mapping as well. Detect cycles with an active recursion path; a global visited set
would incorrectly remove repeated valid occurrences of the same reference mesh.
Dependency fingerprints must include external reference geometry and instance
matrices, even when they are outside the linked hierarchy.

Observed multi-instance Cloner cache: first clone remained a Plane generator in a
Null, with BIT_CONTROLOBJECT set and an evaluated polygon cache. An Instance node
held eight global matrices for the remaining clones. Normal traversal plus those
eight occurrences yields nine boards. Blindly skipping control-bit primitives
would omit the first clone; blindly adding another prototype would duplicate it.

Native029 live evidence on C4D2026.3.4 PID11108: exact artist scene yields nine
boards,600 parts,23 packages,eight cables,zero unfed sockets in all three modes.
World-space board point sets agree within1e-5, including transformed/nonuniformly
scaled Cloner tests. Random effector, clone count, Plane size, external reference,
idle and save/load checks pass; accepted Extrude/Voronoi geometry is preserved.
See CircuitBoardTool CHECKPOINT_NATIVE_029.md and cloner_sources_029_outer_first.json.
This supports evaluated polygon-region sources, not all arbitrary generator or
renderer-specific deformation semantics. No live cache mutation or recursive
GetVirtualObjects/ExecutePasses calls are required inside the generator snapshot.

## 173. SDK defaults disable C++ unwinding: caught errors can strand RAII locks

Circuitry native029 image errors exposed a deployment-only crash. The SDK target
generated `/EHs-c-` and `_HAS_EXCEPTIONS=0`, while the independent pure-core CMake
tests used `/EHsc`. The plugin throws recoverable validation errors and catches
them at GetVirtualObjects, but without unwinding an intervening lock_guard can
remain locked. A later image evaluation attempts to lock its own nonrecursive
mutex; the runtime aborts rather than recovering.

Windows dump Cinema4D PID11108, September19: fail-fast0xc0000409 parameter7,
thread53440. Matching native029 PDB resolves stack candidates to
std::_Mutex_base::lock and CircuitryReadImage; imageMutex owner53440/count1.
Raw scans are not a full unwound backtrace. Exact first error in the artist
session was not recovered. A reference .jpg image was actually WebP: Cinema decode
rejected it inside this lock, demonstrating a plausible triggering path.

Pinned SDK2026.3 supports `ExceptionHandling=true` in projectdefinition.txt;
sdk_update_projects.cmake and sdk_compiler_helper.cmake generate /EHsc and
_HAS_EXCEPTIONS=1. Enable this for a plugin that uses exception-based recovery,
and add compile-time _CPPUNWIND/_HAS_EXCEPTIONS guards. Keep exceptions contained
within plugin callback boundaries; this does not authorize throwing through SDK.
Do not substitute recursive mutexes or manually unlock selected error branches.

Native030 installed on GUI PID64372 passes repeated empty-mask and missing-alpha
errors followed by valid work on both same/other generators, decode failure then
valid PNG, image geometry/cache and accepted spline/Extrude exact hashes. Prior
tests of only missing paths were insufficient: path resolution throws BEFORE
the mutex. Test errors INSIDE resource ownership scopes and subsequent recovery.
Evidence: CircuitBoardTool validation_native/CHECKPOINT_NATIVE_030.md, raw outer
envelopes, crash symbol scan and matching forensic DLL/PDB. No render performed.

## 174. Custom geometry fitting needs evaluated bounds and inherited material ancestry

Circuitry native035, Cinema2026.3.4/SDK2026.3, September19: artist package sources
can have remote axes, nested transforms, generators and deformers. Measure
evaluated polygon points in the linked source's local frame; fit uniformly in
X/Z and translate minimumY to the receiving surface. GetRad/GetMp on a Null or
the unevaluated source alone cannot describe that entire hierarchy.

Snapshot evaluated/deformed caches in preference to the source input hierarchy;
handle instance occurrences separately and guard cycles. Never recursively
ExecutePasses inside GVO. Self-contained evaluated polygon clones avoid repeated
expression execution and output links back into live input graphs. Retain UVW,
selection and shading tags but remove expression tags from evaluated mesh copies.
This is copied geometry, not a claim of renderer-native multi-instance memory use.

When a linked library's direct children are alternatives, each child can inherit
texture tags from the library Null. Preserve material-bearing ancestors with
their projection transforms, without copying their unrelated children. Include
ancestor texture-tag edits in dependency state, and exclude the receiving
generator's own material-usage tags. Skip procedural material-role binding for
custom subtrees, even when an artist mesh is named like a built-in part.

33 private host checks pass: fitted bounds, primitive/deformer updates, child
sources, material inheritance/editing, Fields, save/load, clones and idle reuse.
Use tolerance appropriate to float point buffers (here1e-4 model units) when
comparing transformed bounds; built-in board top was thickness/2, not thickness.
Evidence CircuitBoardTool/tests/c4d_custom_packages_035.py and
validation_native/CHECKPOINT_NATIVE_035.md. GUI PID58904. No renderer test or
universal support claim for procedural render-only objects.


## 175. Native mechanical animation: local anchors and safe pose-only cache reuse

Circuitry native037/038 on GUI C4D2026.3.4 demonstrates a pose-only GVO branch:
compare complete source dependencies and all parameters except offset/rotation.
If only those change, clone the completed cache, update dedicated subgroup
matrices and publish the new owned result. Never mutate the published cache.
Other edits rebuild normally; idle still reuses.48screws on the artist assembly
measured56-63ms per keyed pose vs5.61s initial evaluation. Hierarchy copying still
costs memory/CPU; this is not viewport FPS or universal renderer certification.

Use a bearing-plane matrix at each mount. Local+Y points outward, including bottom
mounts with a proper rotated basis (not reflection). Offset alongY and rotate
aboutY. Custom fitting uses radialXZ extent and authored axis/seat correction;
never totalY bounds. Verified10x shaft length leaves head scale/seat unchanged.

Mixed custom/procedural hierarchies need explicit material ownership boundaries:
artist marker180 inherits through descendants; generated contacts marker210
re-enables role binding only inside that subtree. Preserve custom/inherited
shaders while contacts use the linked pad material.33 custom-source host checks
pass. Names alone do not establish ownership.

Evidence: CircuitBoardTool/tests/c4d_screws_037.py,
c4d_screw_animation_037.py,c4d_custom_packages_038.py and associated checkpoint/raw
reports. No agent render calls.


## 176. Generated Phong control and testing actual custom meshes

Circuitry native040 / C4D2026.3.4 uses the local SDK tphong.h definitions:
PHONGTAG_STYLE0 Uniform,1 Angle Weighted,2 Area Weighted,3 Angle and Area
Weighted,4 Square Area Weighted. SetPhong(on,angleLimit,angleRadians), then
set PHONGTAG_PHONG_USEEDGES and PHONGTAG_STYLE on each generated mesh tag.
Angles use radians internally. Phong must exist on child meshes; an exposed
parameter on the parent alone does not smooth the generated geometry.

Validate CreatePhongNormals on adjacent faces sharing the same vertex, not
only tag presence. Cylinder resolution changes silhouette; Phong only changes
normals. Ensure desired circular faces share indices. Preserve authored custom
Phong and normal tags by inherited ownership markers; selectively opt generated
contacts back in. Snapshot hierarchies may contain identically named outer
Nulls and inner evaluated PolygonObjects. Tests must identify the actual mesh,
not stop at the first matching name. Initial040 custom-preservation assertion
was a test selecting a wrapper Null; the mesh retained authored17deg/style2.

Evidence: CircuitBoardTool/tests/c4d_shading_040.py, shading_040.json and raw
outer attempt1/2. Six groups cover computed smooth normals, five algorithms,
limits/edge flags/off, resolution, custom ownership and save/load. No renders
and no universal renderer visual certification.


## 177. Thin generated contacts must account for every attached routing layer

Circuitry041 replaced three mismatched pin cubes with closed stamped-strip
meshes. A test found8 lower-layer return traces did not meet feet positioned
only at the highest attached layer.042 uses the actual connection layer range
and adds a thin downward toe at shared terminals. Validate actual route-end
heights against emitted geometry, not only2D terminal positions. The042500%
fixture has339 contacts and zero geometric misses. Avoid retaining redundant
via barrels where a physical package lead already carries the connection.

For contours, raster-pitch simplification can remove visible curvature from
thin traces even when it removes raster noise.042 offsets the sampled source
rings and simplifies relative to trace width. Recheck swept-width containment,
holes, clearances and concave collapse after every simplification/offset step.
An exact-circle test catches coarse chords that generic containment misses.

Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_042.md,
tests/c4d_lead_contacts_042.py, tests/c4d_package_finish_042.py and native_contour_tests.cpp.
Actual viewport before/after was inspected with a desktop screen-capture tool;
this is desktop capture, not C4D rendering.
Use returned app/window identities; do not substitute the render-based MCP
viewport screenshot for the actual presented viewport. No Octane render invoked.


## 178. New native parameter defaults can alter loaded legacy objects

Circuitry045 added enum2204 with Init default1. A saved pre-control artist
scene loaded with2204=1 on all three generators, despite no stored control.
A GetInt32 fallback0 alone does not preserve absent parameters: initialization
may already fill the container.046 uses Init0 and getter0, retaining the old
look; new appearances are opt-in. Verify legacy load and explicit nondefault
save/load in the live host. Evidence: c4d_component_mix_045.py (rejected),
c4d_component_mix_046.py and047 (pass), corresponding outer/results JSON.

## 179. Copper intersection endpoint keys need a bounded precision repair

Circuitry046's rounded base-board fixture produced Open copper boundary for
Network and Hybrid. Exporting spline paths isolated two failing native unions.
Coincident calculated intersections could land in adjacent quantization bins.
047 uses exact lookup first, then a nearest unvisited boundary start within
32*epsilon only; ring closure uses the same tiny bound. Do not bridge visible
gaps or discard failed paths. Spline success does not establish mesh success.
Two extracted paths at0.1/1/1000 scale pass finite/area checks. The original
Hybrid live fixture now triangulates successfully. See native_copper_precision_tests.cpp
and CHECKPOINT_NATIVE_047.md for evidence and remaining limits.


## 180. Layer pitch and component cap overlap need geometry checks, not Phong fixes

Circuitry047's black capacitor sleeve had a solid cap exactly coincident with
its separate silver lid.048 lowers the hidden cap below the lid underside and
extends the rolled rim over the sleeve/lid seam. Test actual emitted point bounds
on rotated sources, not just shading tags.74 live can assemblies passed.

Copper layer spacing was an independent base-to-base pitch:0.1thickness with
0.35pitch leaves0.25 empty space.048 resolves Auto0 to thickness*1.02 and clamps
manual pitch to at least that spacing. Do this once before density scaling and
use the same resolved pitch for copper, vias, lead toes and spline centers.
Upper routes still float above the board; this does not implement local bridges.

A lead-contact test's width-radius capsule incorrectly associated a nearby via
past a lead tip with that lead. Inspect owner/endpoint evidence before changing
geometry.048 narrowed this sampled test to actual centerline terminal positions:
350 matched, zero misses. Keep the initial failure and diagnostic evidence.
Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_048.md and tests
c4d_component_sources_048.py, c4d_layer_pitch_048.py, c4d_lead_contacts_048.py.

## 181. Section seed isolation requires geometry ownership, not name-only filters

Circuitry049 adds zero-default section offsets while retaining the common
master seed for density thinning and source layout. Capture pre-install world
geometry/polygon fingerprints, then compare zero-offset results on both a single
board and connected panels. Init defaults matter (entry178); deterministic new
seeds alone do not prove legacy parity. Also test reset-to-zero, save/load and
Randomize/Reset semantics separately. Geometry dependencies still propagate:
changed footprints or holes can legitimately alter copper routing.

The first live cable-isolation test falsely failed because its name filter
included CONNECTOR_* routes under TRACES alongside physical external cables.
Use ownership/group ancestry as well as names. Preserve the original baseline
comparison; add a separate physical-cable fingerprint excluding copper routes.
No production change was needed. Both exact legacy fixtures and all five seed
tests pass after correcting classification; retain rejected raw results.
Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_049.md,
section_seeds_049_previous.json, section_seeds_049_rejected.json,
section_seeds_049.json and raw outer results; tests/c4d_section_seeds_049.py.
API control descriptions and visible Attribute Manager acceptance remain
separate checks; cached evaluation timings are not viewport frame rates.

## 182. A routing feature must be tested after host normalization and placement

Circuitry050's initial coordinated-bus candidates passed core rectangular-board
tests but produced zero groups on the live default board. Normalize Detail uses
prepareSurfacePlanning plus fillSurface placement; these spread package anchors
and need lateral turns, not just straight trunks. Turning normalization off
isolated the cause but was not a fix. Add normalized core fixtures as assertions,
then validate the same default, curved spline and connected panels in the host.

Bundling only after part placement leaves useful corridors occupied.050 reserves
only fully validated bus segments before placing small components; all lanes
must pass width/hole/footprint/copper checks before any reservation is committed.
Failed candidates retain the original router. Nested doglegs reverse turn order
along the pins to avoid braided crossings. Keep corridor seed selection separate
from the exposed routing-priority seed if that control promises not to move parts.
Test geometry and acceptance counts; a successful nonempty cache alone did not
catch the initial feature doing nothing. Report actual counts in Build Status.

Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_050.md,
bundles_050_rejected.json, bundle_probe_050_outer.json, bundle_core_050.json,
bundles_050.json and corresponding raw outer results. Source coverage does not
mean all nonparallel package sides or arbitrary paths have a coordinated solver.


## 183. Coordinate package-to-bank feeds without reallocating connectivity

Circuitry051 found that greedy nearest-pin assignment could invert neighboring
bank feeds before geometric routing began. For local links from one package
side, sort the already allocated source pin pool and pair it with targets in
spatial order. Preserve the exact pin set, owner/side, target ports, net/group/ID
and component-chain order; bus/return and component-chain sources remain intact.
Tests must compare synthesis on the same placed parts, not separate layouts.
Validate grouped copper against later fallback routes as well as its own lanes.
When extending bundle routing to different target owners/depths, use the nearest
target projection for the common trunk envelope, then validate every full-width
lane. Bounded splitting of contiguous pin groups permits smaller valid fans.

Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_051.md,
tests/native_feed_tests.cpp, feed_baseline_051.json, feeds_051.json and raw outer
results. Three live source fixtures preserve050-on component positions and
external cables; disabled geometry matches exactly. These checks establish
bounded aesthetic coordination, not universal routing or more completed nets.


## 184. Board angle filtering and finite obstruction tests are independent

Circuitry's cube had six boards and no cables: the board-normal dot<=0 guard
rejected all90-degree corners. Exposing an inclusive angle limit alone was not
enough: the next test projected candidate chord samples through every board
without checking distance to its plane. Test finite slab intersection first,
then the clipped2D segment against outline and hole boundaries. Full segments
catch crossings that four fixed samples can miss. This remains a chord test,
not a swept cubic-cable or arbitrary source-mesh collision solver.

Visible boards are centered on source faces: materialization subtracts half
thickness from the frame origin before emitting0..thickness. Zero lift on a
200-unit cube with thickness5 therefore spans97.5..102.5, not100..105. UI moves
must retain IDs and saved values; check description parent groups and save/load.
Artist values may change between diagnosis and preservation:052's lift changed
18 to12. Fixtures must not silently replace the latest saved values.
Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_052.md,
connections_052.json, rejected fixtures and raw outer results. Default/flower/
flat-panel geometries remain exact; cube connects all six boards at90 with five
links and supports zero lift and all four cable styles.


## 185. Neighbor degree targets need topology and ownership-aware acceptance

Circuitry053's first balanced greedy network still stranded two opposite cube
faces at target1. Prefer scarce available unique neighbors after current degree,
before cost, so early links do not consume their last choices. Keep bounded
socket alternatives per board pair and enforce pair uniqueness and socket spacing.
Preserve old tree/chain candidate selection exactly. A bounded greedy target is
not an exact degree solver: live cube target3 gave3,3,3,3,2,2, while target4 gave
four per face/12cables. Report actual degree range and unmet targets to artists.

Package pin tests must use PINS ancestry, not Lead_ name alone: passive parts
also contain Lead_ meshes. A first base-thickness test falsely expected those
unrelated leads to shrink. Corrected ownership proves192package pins lower with
unchanged copper foot heights at25% substrate thickness;100% resets exactly.
Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_053.md,
neighbors_053.json/outer and rejected results; native_neighbor_network_tests.cpp.

## 186. Stacked sockets need alternate sites and stable planning precision

Matching board normals are0 degrees apart. A maximum normal-angle control does
not solve a stacked cable: sockets face outward beside each plate, so routing
must leave the edge, turn along the stack axis and return. Use local frames for
both the path and ribbon cross-section; fixed world-up thickness collapses on
upright sections. Validate finite board slabs and distinguish this from arbitrary
scene or cable/cable collision. Intermediate boards need separate socket sites
above and below; retaining only the cheapest pair stranded a four-board chain.

Evaluated Extrude hierarchy transforms can retain coordinate differences near
1e-12 after rotation/reset. In054 those tiny differences changed planning choices
and output topology. Quantizing stack route cost alone was insufficient; also
stabilizing the new stack planning outlines/keepouts and fitting dimensions to
1e-8 restored topology, with maximum emitted-point error3.21e-12. Physical source
geometry and legacy modes stayed unchanged. Do not weaken exact topology tests
to accept this silently. Check numerical point tolerance separately from hashes.

Per-board Field control should apply the outline-average weight once. Applying
the average to counts and then reusing the same grid to thin placements applies
the mask twice. Keep spatial vertex-map masking independent. Refit reduced counts
only when density auto-scaling is enabled. Validate zero masks, movement/reset,
idle and save/load, not just static counts.

Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_054.md,
stacks_054.json, stack_curves_054.json and retained rejected/raw outer results.
These are bounded GUI-host tests, not clean-install or renderer certification.


## 187. Join a flat private mesh list with material proxies; preserve corner normals (Circuitry055)

Cinema2026.3.4: a14,998-mesh generator cache joined through MCOMMAND_JOIN in a
private document, with MDATA_JOIN_MERGE_SELTAGS enabled, becomes one mesh with18
material tags and18 selections. Final direct-button action took1.017s after
initial cache evaluation;1,242,058 polygons and material coverage were unchanged.
This is not proof that the editor Connect Objects + Delete command is equivalent
in cost. Artist reported that command hanging after Make Editable; cause unknown.

Resolve source texture materials while the source remains in its own document.
Use cheap private Standard placeholders keyed by original material identity,
insert the joined mesh into the private document before resolving its links,
then remap to original materials after insertion into the artist document. This
avoids copying renderer networks. Restrict hierarchy statistics to root+children;
a generic sibling walker can accidentally include temporary siblings in counts.

A joined object has one Phong tag. Freeze per-source corner normals with
PolygonObject::CreatePhongNormals and NormalTag::Set before joining to preserve
custom assets with different Phong settings. Existing explicit normals remain.
CreatePhongNormals memory is caller-owned (DeleteMem). NormalTag::Alloc count is
polygon count; four normals per polygon. Python NormalTag.Get(handle,index)
returns an a/b/c/d dictionary. Custom UVs, inherited UV material,11,568 corner
normals, transformed bounds, Undo/Redo and save/reload checked on055. Numerical
point signatures can cross rounding bins; use a bounded positional comparison
and an independent normal tolerance, not rounded-key equality alone.

Evidence: CircuitBoardTool/validation_native/CHECKPOINT_NATIVE_055.md and raw
consolidate_055_outer.json. No renderer/UI appearance certification. Non-UV
projection spaces, layered shaders and arbitrary per-child renderer attributes
require wider review before promising universal bake parity.


## 188. A cold linked cache needs a bounded normal-event-loop follow-up (Circuitry056)

A Cube below its Circuitry consumer had no polygon cache during the first pass.
An unlisted document recovered on a second ExecutePasses, but a cold GUI scene
remained on the cached input error with no user event. Thus two-pass private tests
alone would incorrectly qualify the workflow. Preserve first-pass and actual
normal-event-loop observations separately.

056 queues one main-thread EventAdd(ENQUEUE_REDRAW) per failed mesh dependency
state, using the existing pointer-free publication queue. CheckDirty then sees
the newly available source cache; no GVO recursion, broad dirtying or forced
ExecutePasses is needed. An unsupported empty source must not request a redraw
forever. Four mixed cold sources recovered without tool-forced passes, and the
empty Null error remained at serial1 over later phases. Queue callbacks capture
no scene pointers so deletion remains safe. The clone/deformer/spline/Undo/load
lifecycle was rerun because the shared queue changed.

Evidence: CircuitBoardTool cold_source_order_055.json (failed),
cold_source_order_056.json (passed), phased raw results and CHECKPOINT_NATIVE_056.
Fresh-preference c4dpy loaded the staged056 product without Python prototype/MCP,
passed six examples and consolidated roundtrip, and exited0. This driver returned
naturally; older021/022 drivers explicitly called sys.exit(0). Do not claim that
this isolates the cause of old shutdown failures or qualifies headless rendering.


## 189. MCP scripts that mutate or evaluate a visible document must call `c4d.StopAllThreads()` first

**Discovered 2026-09-23** (Circuitry native, heavy 250-board scenes): three C4D crashes, all from the test harness, none from plugin code. ACCESS_VIOLATION at `c4d_base+a385ec` inside a hierarchy walk, once on the Python main thread, once on a viewport-scheduler worker; an untouched release binary crashed identically under the same harness.

**Wrong assumption:** the MCP `execute_python_script` handler runs on the main thread, so it is safe to edit the active document and call `ExecutePasses`/`DoUndo`.
**Actual behavior:** the handler does not stop the viewport/scheduler threads. They keep evaluating the same document while the script edits it. The race widens as evaluation gets longer.
**Fix:** start every mutating script with `c4d.StopAllThreads()` and call it again before each `doc.ExecutePasses(...)` / `doc.DoUndo()`. Prefer `LoadDocument` (never displayed) documents for regression checks. Plugin `MSG_DESCRIPTION_COMMAND` handlers that edit the scene call `StopAllThreads()` after the main-thread check, before `AddUndo`. With this, the exact crashing sequence plus 10 stress rounds ran clean.
**When it bites:** any agent test loop against a heavy scene the user can see.


## 190. Display color is not in the object's data container: write it through `SetParameter`

**Discovered 2026-09-23** adding random viewport colors to generated cache objects.

**Wrong assumption:** `op.GetDataInstance().SetInt32(ID_BASEOBJECT_USECOLOR, 2)` plus `SetVector(ID_BASEOBJECT_COLOR, c)` sets the display color, like any other object parameter.
**Actual behavior:** the container write is silently ignored. `op[c4d.ID_BASEOBJECT_USECOLOR]` still reads 0, and the viewport is unchanged. The reverse also holds: a parameter write does not appear in the container (both verified on a fresh `Opolygon`).
**Fix (C++):** `node->SetParameter(ConstDescID(DescLevel(ID_BASEOBJECT_USECOLOR)), GeData(ID_BASEOBJECT_USECOLOR_ALWAYS), DESCFLAGS_SET::NONE)`, and the same for `ID_BASEOBJECT_COLOR`. Read it back with `GetParameter`. USECOLOR 1 (Automatic) shows the material color when a material exists; 2 (Always) shows the display color. Renders are unaffected.
**When it bites:** generators coloring cache objects, "random color" tools, density or debug previews.


## 191. Cinema adds `ID_BASELIST_ICON_COLOR` (1041671) to an object's container on its first parameter edit

**Discovered 2026-09-23.** A generator compares a filtered copy of its BaseContainer to decide between a cheap cache re-dress and a full rebuild. The first edit of a text field on a generator from an older scene always forced a full rebuild; later edits did not.

**Actual behavior:** the first parameter edit on an older object inserts `ID_BASELIST_ICON_COLOR` (1041671, value like `Vector(1,0.9,0.4)`) into the object's own container. A "did anything geometry-relevant change?" key built from the whole container sees a new key. (Python wrappers of SplineData/FieldList values also differ in identity on each read, but not in content.)
**Fix:** exclude the icon-settings range `ID_BASELIST_ICON_SETTINGS_GROUP..ID_BASELIST_ICON_PRESET_BUTTONS` (1041666-1041672), along with your own cosmetic IDs, from rebuild keys.


## 192. Texture tag precedence: a parent's restricted tag loses to a child's own; Join reorders overlapping assignments

**Discovered 2026-09-23** building per-board material overrides on a generator whose cache meshes each carry their own texture tag.

1. A texture tag on a parent (even restricted to a selection name) loses to the child mesh's own texture tag. To override, insert the new tag on each mesh after its existing tag: `node->InsertTag(tag, node->GetLastTag())`. The rightmost tag wins, and `InsertTag(tag)` with no predecessor inserts first.
2. `MCOMMAND_JOIN` with `MDATA_JOIN_MERGE_SELTAGS` merges texture tags per material in first-seen order across the joined objects. Where assignments overlap (an override over a base material), the base can end up right of the override and win again after Join. **Fix:** before joining, drop every texture tag left of a later tag that fully covers the mesh (empty restriction, `TEXTURETAG_MIX` off, `TEXTURETAG_TILE` on). Selections then never overlap and order no longer matters. Verified: override selections summed exactly to the polygon total after Join.


## 193. A MoGraph Cloner cannot use a generator whose cache is a hierarchy, and through Connect it ignores selections

**Discovered 2026-09-23** testing whether particle-style consumers can use a polygon selection ("Cable Ports") published inside a generator's cache, without Make Editable.

- Cloner, Object mode, `MG_OBJECT_LINK` = a generator whose cache is a Null with polygon children, Distribution Polygon: **0 clones**, with or without `MG_POLY_SELECTION`.
- The same generator under a Connect object with the Cloner linked to the Connect: clones on every polygon, but `MG_POLY_SELECTION` is ignored. Selection tags do not survive Connect's merge.
**Implication:** selection-driven distribution or emission from a live generator needs a real mesh: Make Editable, a consolidate/join step (merged selection tags are kept), or a generator-published real child object. Test the consumer before promising "no Make Editable".


## 194. A dynamic `DESC_MAX` from `GetDDescription` clamps values when the description was cached before the data existed

**Discovered 2026-09-23.** A "Highlight Board" slider set `DESC_MAX = boardCount` read from the cache. On a freshly loaded scene the description was built before the first build, so the max was 1, and `op[id] = 5` stored 1.
**Fix:** keep `MAX` static and generous in the `.res`. Only set `DESC_MAXSLIDER` dynamically (it shapes the drag range but never clamps), and report out-of-range values in a status string.


## 195. A generator's dependency on a child it writes must exclude that child's own dirty count

**Discovered 2026-09-23** (Circuitry managed spline children). The generator hashed its managed child (`GetDirty(DATA)`, points, tags) into its dependency state, then its main-thread publication job wrote new points into that child. Every publish bumped the child's DATA dirty, so `CheckDirty` forced a second full rebuild after every parameter change.
**Fix:** for a child whose points you own, hash only what the user controls: the child's pointer, its matrix, its tags (pointer and dirty count), and its own children (deformers). Result: publication no longer rebuilds, and adding a tag or deformer to the child still does. A 10-phase clone/undo/delete-before-callback acceptance test still passed.


## 196. Viewport overlays over geometry: use the OBJECT pass with `DRAW_PARAMETER_SETZ = DRAW_Z_ALWAYS`, not HIGHLIGHTS

**Discovered 2026-09-23** drawing a translucent board highlight from `ObjectData::Draw`.
- The SDK warns against drawing in `DRAWPASS::HIGHLIGHTS` (it is the selection-highlight pass; see `GetHighlightPassColor`).
- In `DRAWPASS::OBJECT`: `SetMatrix_Matrix(op, op->GetMg())`, `SetDrawParam(DRAW_PARAMETER_SETZ, GeData(DRAW_Z_ALWAYS))`, `SetTransparency(-170)`, `DrawPolygon(...)`. Then `DrawLine(..., NOCLIP_Z)` with `DRAW_PARAMETER_LINEWIDTH` for outlines. Restore `DRAW_Z_LOWEREQUAL`, transparency 0 and line width 1.0 afterwards.
- Verified in the live viewport, with a PrintWindow capture (#198).


## 197. `.res` LONG 0/1 cycle to BOOL keeps scenes and scripts working; `SelectAll` takes a range

- Changing `LONG X { CYCLE { A; B; } }` (values 0/1) to `BOOL X { }` under the same ID kept saved scenes, C++ `GetInt32(X)==1` checks and test scripts writing `op[X] = 0/1` working unchanged (a 10-phase output test passed). Remove the cycle-member strings from the `.str` when you do it.
- 2026: `BaseSelect::SelectAll(Int32 min, Int32 max, Bool deselectAll = true)` has no single-argument form; select every polygon with `SelectAll(0, count - 1)`.


## 198. Seeing the real viewport from an agent: `PrintWindow(hwnd, dc, 2)`, not `CopyFromScreen`

**Discovered 2026-09-23.** `System.Drawing.Graphics.CopyFromScreen` of the C4D window rectangle captured whatever window was on top (the IDE). `PrintWindow` with `PW_RENDERFULLCONTENT` (2) on `Get-Process 'Cinema 4D'` `.MainWindowHandle` captured C4D's own window, DirectX viewport and plugin overlays included, while C4D was fully occluded. Downscale before viewing (e.g. to 1200 px wide). Use it when you need what the artist actually sees, including `Draw()` overlays and display colors.


## 199. Save a copy of an unsaved user document before quitting C4D: `SaveDocument` does not rename it

**Discovered 2026-09-23** needing to quit C4D to install a plugin while the user's scene had unsaved changes.
`c4d.documents.SaveDocument(doc, other_path, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT)` wrote a full copy (304 MB in 0.3 s). The open document kept its name, path and changed flag. Verify the copy with `LoadDocument` (object count, materials, key parameters) before `KillDocument` on the live one. Never overwrite the user's file, and never quit with a save dialog pending.


## 200. Octane Universal shading models: find IDs and values from the description's cycle, then set plain ints

**Discovered 2026-09-24** switching preset materials to energy-preserving models (Octane for C4D 2026.4).
Walk `mat.GetDescription(c4d.DESCFLAGS_DESC_NONE)`, match `DESC_NAME`, and read `DESC_CYCLE` for the option values:
- `2645` "Diffuse BRDF model": 0 Octane, 1 Lambertian, 2 Oren-Nayar, **3 Oren-Nayar (energy preserving)**
- `2585` "BSDF model": 0 Octane, 1 Beckmann, 2 GGX, **6 GGX(energy preserving)**, 7 STD (the values are not contiguous)
- `2644` "Compatibility mode": a version code such as 15030000 ("2026.4"). The field shows blank when the stored code is not a listed option.
Set with `mat[2645] = 3; mat[2585] = 6`. Material libraries loaded with `LoadDocument` can be patched this way and saved in place; compare the other parameters before and after.


## 201. Redshift node ports: `GraphDescription.ApplyDescription` stores an enum *label* as a string in an int64 port

**Discovered 2026-09-24.** The Redshift Standard Material `diffuse_model` port is `int64` (default 0). `GraphDescription.ApplyDescription(graph, {"$query": {...,"$type": "Standard Material"}, "Diffuse Model": "EON"})` succeeded, but the port then read back a `maxon.String` "EON", not the enum value. No conversion happens, so the port is corrupted. Port descriptions (`PortDescriptionData`/`Ui`) returned None on a loaded material, and `NodeMaterial.GetDescIDForNodePort` raised ValueError.
**Pattern:** set the integer directly in a transaction (`port.SetPortValue(maxon.Int64(n))`). Take the enum order from Redshift's string table `Redshift/res/core/Data/Strings/en-US/rscore.json` (keys `...standardmaterial.inparam.diffuse_model.enum.*`, listed in declaration order: Oren-Nayar, d'Eon Lambertian Spheres, EON). Confirm the label once in the Attribute Manager. EON only differs from Lambert when Diffuse Roughness > 0.


## 202. A document returned by `LoadDocument` dies with its Python reference: keep the document, not just its materials

**Discovered 2026-09-24.** `mats = c4d.documents.LoadDocument(path, ...).GetMaterials()` followed by reading `mats[0]` raised `the object 'c4d.BaseMaterial' is not alive`. The unlisted document was freed as soon as nothing referenced it. Keep `doc = LoadDocument(...)` alive (e.g. in a list) while using its objects or materials.


## 203. Agent tests must never `SetActiveDocument` while the artist is working

**Discovered 2026-09-24.** A test made its own document active to read a label from the Attribute Manager, and that switched the artist's view away from the scene they were editing. Restore it at once (`SetActiveDocument(previous)`, `KillDocument(test)`, `EventAdd()`). Prefer unlisted `LoadDocument`/`BaseDocument()` documents, which need no UI. When a UI check is unavoidable, ask first or wait until the artist is done.


## 204. `execute_python_script` returns "cancelled_before_start" while the artist's main thread is busy; `ping` still answers

**Discovered 2026-09-24.** With an artist working in Cinema (a dialog open or a long main-thread operation), every script call timed out with `state=cancelled_before_start`, even with a 5 s timeout, for several minutes. `ping` answered instantly because it does not acquire the main thread. From outside, `Get-Process 'Cinema 4D'` showed `Responding: True` and low CPU, so this was waiting on the artist, not a hang.
**Pattern:** `cancelled_before_start` guarantees nothing ran, so do not retry in a loop. Use `ping` for liveness and check the process from outside (`Responding`, CPU seconds over a few seconds, `MainWindowTitle`). Continue work that needs no Cinema, and resume when a short script call succeeds.

## 205. A primitive's BIT_CONTROLOBJECT is not by itself evidence that a linked generator consumed it

**Discovered 2026-09-29, C4D 2026.3.4, Bubblor regression tests.** In a fresh unlisted document containing only a native cube and sphere, both objects changed from `GetBit(BIT_CONTROLOBJECT) == False` to `True` after ordinary `ExecutePasses`. Their renderable polygon caches retained `False`. A source-preservation test that asserted the bit remained false on a linked primitive therefore failed even though the primitive's geometry was intact.

Compare equivalent native controls and inspect the renderable cache and editable polygon sources, source dimensions, visibility modes, and repeat evaluations. Do not clear this internal bit on artist objects to make a test pass. Bubblor's corrected lifecycle gate checks an unconsumed polygon cache plus an editable polygon source and passes clone/save/reload tests too. These are API checks, not viewport visibility acceptance.

Related source-evaluation result: Bubblor's cold first evaluation could not rely on an already-built linked source cache. Its final implementation clones the source into an owned temporary document and uses the local SDK's documented threaded `SendModelingCommand(MCOMMAND_CURRENTSTATETOOBJECT, ...)` pattern, then flattens the result. This passed cold/repeat evaluation, nested pieces, a deformed polygon fixture, keyed source motion and rewind. It does **not** establish compatibility with external linked dependencies or simulation state outside the copied subtree. Evidence and raw tool results: `Projects/Bubblor/FIXES_2026-09-29.md` and `tests/results/`.

## 206. Stable Field growth must be tested with downstream layout stages and between animation keys

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** A new grow/shrink mode kept scatter positions stable but initially applied Field radii before cohesion. Changing radii then changed cohesion partners, causing up to 65.886 units of vertex motion in one frame in the Pearl Orchard fixture. Applying growth after scatter/merge/cohesion preserved anchors. The native moving-Field regression now checks 61 sequential frames, rewind, save/reload, and cohesion-enabled anchors, rather than treating a scatter-only fixture as sufficient. This is a generator pipeline finding, not a fault in C4D Field sampling.

Separately, checking only frames 0/30/60/90/120 missed small folded quads between morph keys. Scanning every frame 0–120 caught them; constraining both contact and bridge deformation produced 484 clean frames across four fixtures. Count finite coordinates, triangle areas and quad triangle-normal consistency; these checks do not prove general self-intersection freedom. Native `RenderDocument` previews validate rendered output, not actual viewport interaction or FPS. Repros, rejected candidates, final hash and raw results are retained in `Projects/Bubblor/RELEASE_READINESS_2026-09-29.md` and `tests/results/battle/`.

## 207. FieldList HasContent does not establish that its Field-object links still resolve

**Demonstrated 2026-09-29, C4D 2026.3.4, artist-edited Bubblor scene.** A Placement Field layer remained after its object was deleted. `HasContent()` was true; the live container could still expose an undo-retained object wrapper, but the link was unresolved after native save/reload. Sampling returned zero, silently shrinking large-tier shells under an 80% grow/shrink mask. Check `FieldLayer::GetLinkedObject(doc)._object` and document membership when applying a product-specific empty-list fallback. Bubblor now treats lists made only of unresolved direct FLfield layers as absent; it preserves native semantics for mixed/grouped lists and intentionally zero-valued live/Solid layers. Never interpret a sampled zero by itself as a missing Field. Tests cover Placement, Scale and Inflation slots in both layout modes. Evidence: `Projects/Bubblor/ARTIST_FEEDBACK_2026-09-29.md` and `tests/results/user_feedback/`.

## 208. PolygonObject storage tags must not be cloned when splitting a generated mesh; cache colors require registration support

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** C++ iteration over a polygon object's tags includes internal geometry storage. GetPointCount/GetPointR and GetPolygonCount/GetPolygonR use Tpoint/Tpolygon tags (see c4d_baseobject.h). A split-output implementation that cloned every unknown tag replaced each shell's allocated arrays with the whole build's arrays. Its per-shell vertex maps retained shorter allocations. A subsequent lock/flatten operation read beyond those map arrays and crashed in BubblorFlattenCache. Copy only the semantic tags appropriate to the new topology, remap their data explicitly, and validate vertex-map GetDataCount against the source point count. The custom frozen-mesh serialization must also omit internal storage tags because points/polygons are serialized separately. Corrected bounds/map-length assertions, lock/save/reload and deformation tests pass; original crash DLL/PDB/minidump are retained.

Separately, SetParameter(ID_BASEOBJECT_USECOLOR/COLOR) on generated objects was not sufficient: without OBJECT_USECACHECOLOR in RegisterObjectPlugin, Cinema overwrote their colors with the owner's settings after publication. The SDK's ge_prepass.h documents this flag. Once opted in, explicitly copy the owner's ObjectColorProperties for normal-shading output, and set each shell's color for per-bubble output. Native display RGB values round to Float32; e.g. requested .8 reads as .800000011920929. Use an appropriate tolerance. Hardware-render previews verify the resulting colors, not interactive viewport FPS.

Evidence: Projects/Bubblor/APPEARANCE_AND_BUILD_WORKFLOW_2026-09-29.md and tests/results/appearance/. The final installed candidate passes 64 bounded native checks, including four 31-frame Cloth smoke tests on frozen generators and editable copies. This does not certify long simulation/cache workflows or arbitrary tag remapping.

## 209. Procedural appearance identity must survive solver reindexing and merge traversal

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** Just moving a merge control from 100% to 99.9999% reordered its output traversal without moving the 12 test shells. Colors keyed to the compact output index changed on 11 shells. Assign persistent appearance identity before traversal; keep a separate compact index for solver use. Carry appearance identity through growth/culling, merged-parent inheritance, generated metadata, frozen mesh serialization and cloning. Compare colors and positions by identity, not output array index. The corrected regression preserves all 12 colors, and continuous Burst tests maintain identities during reverse/random scrubbing and lock/save/reopen.

A transform test also must control its dependencies: the artist's Bubblor had native Fields parented underneath it. Moving the generator moved those Fields and legitimately changed their samples. Use independent fixed inputs to test world/local output conversion, or account for the Fields' world transforms. Do not patch generator matrix conversion to satisfy a test that unknowingly moved its inputs. Evidence: Projects/Bubblor/BUBBLE_BURST_2026-09-29.md and tests/results/burst/.

## 210. Live generators need explicit cache reuse and dependency validation; benchmark metadata outside the timer

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** An unchanged linked-source generator rebuilt on every ExecutePasses call: 1,016.16 ms warm median on an artist's 2,495-bubble scene, versus 0.2266 ms locked. Reusing the native GetCache when CheckCache is false and parameter/dependency state matches reduced live evaluation to 1.1409 ms. Track evaluated source hierarchies/caches/tags and world transforms, generator transform, FieldList GetDirty(doc), and document time when procedural Fields may animate. CheckDirty must invalidate changed inputs; successful publication alone records valid build state, and failed/cancelled work must remain retryable. No geometry evaluation belongs in Draw. This implementation passed source edits without manual dirtiness, deformer/spline/parent changes, moving Fields, keyed animation and rewind, static timeline reuse, clone/save/Undo, and frozen Cloth tests.

Read published scalar statistics in Draw instead of scanning geometry, and use GetContainerInstance where a full metadata copy is unnecessary. A first timing harness copied thousands of per-bubble metadata entries inside the timer, reporting roughly 8 ms instead of about 1 ms for ExecutePasses itself. Keep metadata extraction outside the timed region and retain a build serial to prove no rebuild occurred. Neither number measures viewport FPS. Evidence: Projects/Bubblor/DENSITY_AND_CACHE_2026-09-29.md and tests/results/density/.

## 211. Init defaults can change old-scene behavior unless native disk-level migration distinguishes them

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** Adding a new capped-count mode with Init default 1 and GetInt32 fallback 0 did not preserve old scenes: loading an older scene exposed value 1, seeded by Init. New native disk level 2 now identifies new saves; Read explicitly sets legacy count behavior for levels below 2. No binary payload change was needed because the parameter is in the normal container. Validate both directions: an old artist scene unlocks to exactly the same full point hash as its saved frozen mesh (2,495 shells), while a newly saved capped-count scene preserves its mode, density, fill setting and HUD after reopening. A compile and new-object test alone would miss this migration defect. Evidence: the density report and 12 new gates in Projects/Bubblor/tests/density_regression.py; final build C8BA686D… passes 88 bounded checks.

## 212. Effort controls should extend a deterministic search; use a multiline widget for multiline AM data

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** Field-aware scattering generated all candidate positions, then drew random radii and mask gates from the same RNG. Increasing the position-pool size advanced the RNG before the first radius, reshuffling the existing packing and sometimes reducing counts (85 to 83; 27 to 21). Processing successive fixed-base-size batches preserves the Effort-1 result and extends its accepted sequence. Test full shell hashes/prefixes and monotonic acceptance in isolated scatter, then test coupled artist scenes separately because added neighbors legitimately affect downstream solvers and tiers. The original count-only smoke test had plateaued at 11 and missed this defect; 50 of 75 focused effort cases failed before the fix and all 75 passed afterward. Full-shell Effort-1 hashes remained identical in 72 configurations.

The artist also supplied an Attribute Manager screenshot where a four-line dynamic STATICTEXT value overpainted the preceding HUD checkbox. Use a native STRING with CUSTOMGUI MULTISTRING for that content, not a single-line static label. GetDEnabling can make it read-only and disable controls with no effect under current conditions while preserving their stored values. Python BaseList2D.GetEnabling takes only the DescID; it does not use the four-argument C++ callback signature. Native description/enabling checks confirm the widget and states; actual on-screen visual acceptance is separate and must not be inferred from API success. Evidence: Projects/Bubblor/COUNT_CONTROLS_AUDIT_2026-09-29.md and tests/results/controls/. Build B933EEF9… passes 235 bounded native cases.

## 213. A normal Instance's evaluated cache retains external references that an isolated subtree clone loses

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** Cloning a normal Instance alone into a temporary document for Current State to Object lost its external source link and yielded no bubbles. The native Instance had a valid cache: a referenced primitive with its own polygon cache, in Instance-local coordinates. After declaring source evaluation dependencies through GetAccessedObjects, flattening that evaluated cache with the Instance's world matrix produced correct geometry without mutating the input. Eleven bounded checks cover cold first pass, parent/reference/Instance transforms, source/child edits, retargeting, a multi-piece reference, save/reload, cache reuse and clearing/restoring the link. This is a targeted correction, not evidence for render/multi-instance support or arbitrary linked dependency graphs. Other source types retain the private evaluation path described in entry 205.

For an independent reference test, native Python SendModelingCommand CURRENTSTATETOOBJECT output retained the input's parent-relative matrix. Inserting it under an identity root discarded the original parent transform and falsely reported a 233-unit mismatch. Giving the reference root the original parent world matrix reduced the maximum point difference below 6.6e-14. Do not patch the implementation to compensate for a reference fixture with lost transforms. Evidence: Projects/Bubblor/HERO_LAB_READINESS_2026-09-29.md and tests/results/hero_lab/. Build 5A26F635… passes 260 native contract checks, plus five scene mesh audits, 61 animation frames and four exact-hash performance comparisons.

## 214. Validate contact formation separately from mesh integrity when changing a bubble solver

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** The legacy inflation path capped contact radii at 100% and applied excess inflation as exposed bulging. Raising its stored value to 300% produced larger lobes, not the artist's desired cellular contact network. Most topology diagnostics still passed. Read the actual contact radius computation; rendered growth alone does not prove stronger contact solving, and a valid mesh does not establish the intended art result.

An opt-in native cell mode now constrains expanding shells to complementary base-radius contact planes, preserves a small seed interior, relaxes inside the constraints, and re-constrains bridge deformation. The first quad prototype had 157 warped faces and was rejected. Triangular sampling with constrained relaxation passed the revised tests. Validate every vertex against all applicable contact half-spaces, not just counts/finite coordinates; the maximum tested post-bridge violation was 5.33e-14. Contact maps must represent the constrained cells rather than their now-overlapping spherical envelopes. Disable old controls that have no role in the new model and show its actual topology while preserving the stored legacy choice. Updating the UI inflation range also requires auditing internal variation clamps.

The 300% wall study develops broad cell walls, but sparse 3D exterior cells can still grow long lobes. That is a visual boundary behavior, not automatically a torn mesh. Keep art review, collision constraints, manifold checks, and performance measurements separate. An 82-cell animation evaluated around 16 ms, while a 1,585-cell rebuild took 0.49 seconds; neither is a measured viewport FPS claim. Evidence: Projects/Bubblor/FOAM_SOLVER_2026-09-29.md and tests/results/foam/. Build BC4A9B97… passed 295 native checks, plus 61 animation frames and a dense mesh stress audit. This is an artistic cell model, not proof of physical foam equilibrium, volume conservation or universally welded geometry.

## 215. Cache rest layouts separately and validate convex-solver optimizations against the previous native mesh

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** A generator's final-output cache avoids idle work but still repeats scatter, source baking, cohesion and Field sampling on every surface slider edit. A separate rest-layout snapshot keyed by retained BaseContainer data plus evaluated source/Field/time dependencies allows downstream edits to reuse that work. Keep complete custom Field data in the key: freshly replaced FieldLists cannot safely be identified only by a dirty counter. Tests replaced Scale, Inflation and Placement lists repeatedly and compared cached results against fresh document-clone rebuilds. Clone caches must invalidate, not inherit links or snapshots accidentally.

A primitive can acquire its first native polygon cache after the first generator evaluation. In the tested private cube fixture, the next surface edit conservatively rebuilt the layout once; subsequent edits reused it. Distinguish this dependency-settling miss from a failed warm cache. Do not remove source-cache dependency tracking to make the first assertion pass.

The foam solver's positive-weight Laplacian relaxation and fold-guard interpolation are convex combinations. For an intersection of half-spaces and a sphere, these operations remain inside the same convex cell, so projecting against every plane after every pass was redundant. Retaining initial/final projection and fold guards reduced measured high-detail inflation edits from 1.78/1.90 seconds to 0.54/0.63 seconds, with maximum coordinate differences of 8.53e-14 from the previous binary. This reasoning does not apply to negative-weight Taubin steps, arbitrary bridges, or nonconvex domains. Bridges retain explicit final constraints.

Optional exterior support planes constrain free growth without changing interior contact planes; do not label those envelope faces as bubble contacts. A convex outline is not an exact source/concavity boundary or volume-conserving liquid model. Native keyed animation checks, independent axial bounds, sparse seed sweeps, topology audits and visual comparisons remain separate evidence. Dynamic descriptions should expose the effective topology's real detail range rather than allowing apparently meaningful values that silently saturate. Evidence: Projects/Bubblor/COMPACT_FOAM_2026-09-29.md and tests/results/compact_foam/. Build DCFDB8DC… passed 344 bounded native checks, two 61-frame animation sweeps and three high-detail scene mesh audits; this is not universal renderer or viewport-interaction certification.

## 216. Parallel solver work needs ordered bridge reduction and complete-pass shape validation

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** The 2026.3 SDK's synchronous maxon::ParallelFor::Dynamic supports independent per-cell point slices without touching live C4D objects. Keep scene API work outside workers, immutable geometry/planes shared, and allocations or mutable results local to each task. A lambda returning Result<void> permits allocation errors to propagate. Bridge pairs share output vertices, so computing deltas in parallel and reducing in original pair/vertex order preserves deterministic floating-point sums and guide order. Bounded 64-pair batches avoid storing the entire pair graph's contributions. The intermediate parallel-only candidate matched saved artist meshes exactly; the strong-bridge ribbon edit dropped from 5.50 s to about 1.30 s on a 24-core Threadripper.

Exact parity is not sufficient safety evidence. A broad saved-scene audit found the existing full-strength bridge sequence could progressively collapse tiny faces even though each individual pass had a fold guard. The final foam safeguard compares the fully projected result against the pre-bridge surface, checks relative face area and orientation about the surface centroid, and limits deformation per affected cell. Both endpoints lie inside the same convex contact cell, so interpolation preserves containment. The ribbon's Exterior diagnostics improved from 186 near-degenerate / 17 inward-radial faces to zero; Whole Surface improved from 18 / 58 to zero. A safe Pearl mesh stayed identical. Historical rejected prototypes must not be mixed into a clean-showcase glob: Legacy_200 already had two inward diagnostics in the earlier audit, explicitly retained as a known diagnostic example.

A contact-masked scalar radial smoother can preserve deep contact vertices while softening exposed regions. Fixed valid sphere rays and strictly positive distances preserve radial triangle orientation; per-ray upper bounds maintain convex cell containment. An isolated sphere remains unchanged, avoiding the whole-surface Laplacian shrinkage. In the tested fixture 5,210 protected vertices moved exactly zero. This is not volume preservation, physical soap-film equilibrium, or a guarantee of smooth-looking flat contact junctions. Smoothing width also depends on template resolution. High outline restraint can deliberately flatten the exterior.

Evidence: Projects/Bubblor/EXTERIOR_SMOOTHING_2026-09-29.md and tests/results/exterior/{outer_results,final_outer_results,final_acceptance}.json. Final build FC0B8EEA passed 371 bounded native contracts, three 61-frame pressure/outline/exterior sweeps and nine saved showcase audits. Full-detail Pearl (5.74M points) smoothing edits measured 10.88 s before, 1.86 s parallel Whole Surface, 1.51 s Exterior; detail 4 was a separate lower-resolution 0.48 s option. ExecutePasses timing excludes viewport drawing. Compiler/API/mesh checks do not establish actual Attribute Manager interaction, artist approval, Octane behavior or universal release readiness. Preserve BaseTime numerator/denominator across restarts; restoring decimal seconds with the default denominator can slightly shift an exact frame.

## 217. Foam contact validation must test mutual reach, not only topology and containment

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** A base-radius power partition combined with separately inflated spherical bounds can clip a large cell before its tiny neighbor reaches the opposite side, leaving an empty halo despite manifold topology and exact half-space containment. In the artist's controlled Pearl case, six large cells had 107 / 133 / 115 / 92 / 120 / 99 premature plane contacts, with axial reach deficits up to 17.09 scene units. The preceding 371 contracts did not test opposing-surface reach and therefore did not rule this out.

The deployed correction divides center separation by current inflated radius ratio: hA = distance * radiusA / (radiusA + radiusB). It preserves complementary planes and positive seed interiors while making hA/radiusA equal hB/radiusB. Disjoint spheres are not clipped. Current radius must include each bubble's Inflation Field factor. Three native two-cell fixtures verify exact spheres before mutual reach and coincident facing planes after it; a nonuniform native Field test verifies moving boundaries and exact rewind. All six controlled artist cells now have zero premature center-axis contact deficits. This guarantees axial reach, not that every point on an infinite plane lies in both spherical bounds; finite tessellation, smoothing and outline constraints can still leave seams. Do not market it as volume conservation or gapless physical foam.

For diagnostic radius extraction, Random Variation can modify global inflation: a solver-bypass mesh at displayed 100% is not necessarily the unscaled base radius. Disable variation in explicitly labeled controlled tests, or account for its effective multiplier. Keep actual-scene render comparisons separate so the artist's layout and parameters are still represented. Evidence: Projects/Bubblor/HALO_FIX_2026-09-29.md, tests/halo_regression.py and tests/results/halo/. Built/installed 1CB8C226 passed 385 bounded native contracts, including 14 new reach checks, nine saved-scene mesh audits, three 61-frame pressure/outline/exterior sweeps, and a 1,585-cell stress test. Both artist documents and unsaved flags were restored; native geometry checks do not replace artist visual acceptance.

## 218. Replace contradictory legacy controls with an effective-state adapter, and test applicability separately from visible effect

**Demonstrated 2026-09-29, C4D 2026.3.4, Bubblor.** Legacy Remove/Protect flags implemented `remove && !protect`. A single new description ID derives its BOOL through GetDParameter and writes both old bits through SetDParameter, setting DATA dirty and PARAM_SET. Removing the two old widgets, while retaining stored IDs and solver reads, preserves all four old-scene combinations without migration. Native clone, save/reload, undo/redo and a real orphan fixture verified the adapter; 60 tiny bubbles became 10 and returned to 60, with the large shell unchanged. The combined UI control is ANIM OFF; existing legacy tracks remain stored.

An apparently inert slider can have three distinct causes. Force Tightness used only polygon source snapshots and was genuinely inapplicable to a Text Spline, despite invalidating a 2.5-second build. It is now labelled Limit Distance to Mesh Surface and disabled for spline/missing sources; the margin is a maximum center distance, not an exact offset or inter-bubble tightness. Use the same cheap OBJECT_ISSPLINE classifier as the actual source path. Conversely, a packing rejection ceiling can be active but produce the same accepted sequence at 50% and 100%; 0% changed the artist layout, and a crowded fixture accepted 17 versus 25 large bubbles. Cleanup can likewise be active with no eligible orphans. Numeric tests must independently prove actual effects on eligible fixtures, plus no-op/applicability and legacy behavior, instead of expecting every slider change to visibly alter every scene.

Evidence: Projects/Bubblor/CONTROL_CLARITY_2026-09-29.md and tests/results/control_clarity/. Build 3B52549A passed 115 focused native checks. The initial distance oracle was corrected to leave already-near centers unchanged, with the failed output retained. Artist edits continued after the verified restart restore; do not overwrite new live settings to satisfy a stale hash assertion. Compare the private checkpoint for old-build parity and preserve current artist edits separately. Description/enabling API checks remain distinct from actual mouse/keyboard UI acceptance.

## 219. Rope simulation can deform a spline's evaluated line cache rather than the editable spline cache

**Demonstrated 2026-10-04, C4D 2026.4.0, Weavr (SDK 2026.3).** A 61-frame
Rope probe read `Ospline.GetDeformCache()` and fell back to editable points.
It reported no motion for a stock spline, a frozen Weavr copy and live Weavr.
The solver was running: the actual output was under
`Ospline.GetCache().GetDeformCache()` (evaluated Oline). Traverse the evaluated
cache chain, preferring deformation over undeformed cache data, before
concluding that simulation is inactive. Both default-device and explicit CPU
stock probes moved identically. `MSG_MENUPREPARE` and making the test document
active were not the missing fix. The Simulation SceneHook's own parameter
container is also not its default simulation object's settings: its branch
contains the initialized Simulation Scene with gravity/time-scale values.

The corrected private-document probe stepped frames 0..60 sequentially with
SetTime/ExecutePasses. It required actual free-point movement, finite aligned
output and held black Pins. The live and frozen Orb evaluated positions hashed
identically at all 61 frames; 22 checks passed. This does not certify B-Spline
point-map indexing, every mode, long simulations, concurrent document teardown
or third-party rendering. Independent segments without Connectors can still
fall away; map pinning is not welded topology. Retain the rejected test reports
instead of erasing the false-negative evidence. Repro and full reports:
`Projects/Weavr/scripts/weavr_native_rope_playback_gate.py` and
`Projects/Weavr/docs/gate/release_20261004/rope_playback_final.json`.

## 220. NeXus vertex emission needs real per-entry options; clone finalized geometry before remapping the emitter

**Demonstrated 2026-10-05, C4D 2026.4.0, INSYDIUM XP build 1920, SplatFlow QA.** nxEmitter 1067563 exposes its Objects list (20021) as custom datatype 1051396, not InExcludeData. Assigning InExcludeData returned success but replaced the datatype without creating real entries; reject that approach. After a manual source drop and per-entry Points (20043=2) / One per Element (20072=true), an eight-vertex mesh emitted exactly eight unique particles. A same-position points-only clone emitted none. Three-point/one-triangle and five-point/two-triangle clones emitted exactly three/five particles. These are bounded fixtures, not proof that every degenerate topology is acceptable.

Use one AliasTrans initialized against the configured template document: clone the source, finish all geometry, then clone its nxEmitter with the same translator and Translate(True). This remapped the custom-list source successfully without further manual dragging. Never resize source topology while it is linked: a prior investigator reported a crash, and the user separately crashed C4D changing Random to Regular during this QA. Windows reported ucrtbase fatal-app-exit 0xc0000409, not a symbolized NeXus root cause. Keep Random for this proven Points setup.

The attached dynamic DescID getter under 50000/row/20043 or 20072 returned 0 even after effective user edits; nested containers and detached-clone reads reported 2/true. Getter success/defaults are not enough to prove the custom UI's committed state. Validate actual unique emitted positions. Direct scripted custom-list insertion remains unproven. Evidence: local SplatFlow QA scenes (mesh points one-per-element, cloned-geometry matrix) and their raw check JSON.

## 221. A NeXus bridge can report BOUND while reading positions before CPU-transfer settlement

**Demonstrated 2026-10-05 in the same C4D/INSYDIUM build as #220.** GPU-only mode 21038=0 produced a Null cache; modes 1/2 exposed an XP emitter via xparticles.ToEmitter(nx.GetCache()). In mode 2, a scene-local SplatFlow bridge at Generators +100 reported BOUND on the current frame but output positions lagged the final cache by one velocity timestep. A same-time expression-only refresh corrected the proxy without moving the simulation. Generators +499 instead passed first evaluation, with no refresh: three fresh-tag/reset runs over frames 0..30 for Skip=1 (8 particles) and Skip=3 (3), then a private saved-scene reload with stored mappings cleared. All 248 numeric count/position checks passed, with exact zero proxy/cache position error on nonzero frames. This does not certify a downstream spline/generator evaluated earlier in the pass, UI playback, dense scenes, arbitrary forces, or exports.

Birth matching still used a strict geometric bijection; this fixture needed a first-birth-step candidate pos - velocity * (age + 1/fps), not classic XP's pos - velocity * age. That observation is not a general integration law and must not bypass rest validation. Frame 0 was empty. An initial run at document end frame 30 lost one particle under Full Lifetime and correctly became UNBOUND; extending the QA duration to 300 isolated timing without relaxing the count gate. Preserve both failed and passing results. Never call recursive evaluation from a tag or generator to hide ordering defects. Evidence: a local SplatFlow QA prototype scene, its priority-499 test script and the raw post-checkbox / cold-reload JSON. Production SplatFlow v1.0.2/source/release files were unchanged.

## 222. A generator Free callback that edits its document can corrupt full-object Undo

**Demonstrated 2026-10-06, C4D 2026.4.0, SplatFlow SDK 2026.3.** In a private document, a loaded SplatFlow with a managed display disappeared together with its display after full UNDOSTYPE_CHANGE Undo, even when the edit was only a rename. A bare/unloaded SplatFlow and a Null control did not fail. CHANGE_SMALL parameter tests had passed and therefore concealed this different lifecycle defect. The existing NodeData Free callback removed a linked display from the live document while Cinema freed Undo/transient snapshots.

Do not perform live document/hierarchy edits from Free. Release node-owned memory normally, and retire product-owned derived geometry later from an appropriate main-thread scene hook with explicit ownership and current-document reference checks. SplatFlow marks hidden point displays separately from managed native splines; its retirement pass deletes only marked hidden display objects without a live owner/reference. It preserves ownerless native splines as static snapshots and clears markers on explicit editable copies. It does not delete emitters, emission sources or artist geometry by name.

The built/installed candidate passes full-object parameter Undo/Redo for classic XP/native C4D compatibility/Direct PG; NeXus Setup and repair Undo/Redo at frame zero; generator deletion/Undo/Redo while preserving native/explicit editable splines and unrelated points. These bounded private API tests do not establish every possible teardown or UI interaction. Evidence: SplatFlow/tests/managed_lifecycle_gate.py, managed_cleanup_gate.py, nexus_structure_gate.py and their raw result JSON; current details in SplatFlow/NEXUS_1_1_0_WORKLOG.md.

## 223. Current-frame binding metadata does not prove XP/native particle positions are current; prepare saved-tag upgrades before first execution

**Demonstrated 2026-10-06, C4D 2026.4.0, XP 4.0.1920, SplatFlow.** Extending #221 to classic XP and native C4D compatibility tags found current-frame BOUND status but pre-simulation coordinates at default Expression priority. An eight-point gravity fixture differed from the final particles by 3.05767 (XP) and 1.09000 (C4D) at frame two. Generators+499 managed readers and Generator+500 managed spline publication fix the checked first-pass positions. This is the tested product ordering, not a universal priority prescription for all plugins.

Upgrading an older saved tag only during the later Generator phase is too late: the upgrade invalidates the Python mapping after its frame-zero execution, leaving frame one UNBOUND. A change-guarded Initial-phase preparation fixes this before the first tag execution. Export may refresh expression/generator consumers at the same timeline time after a complete simulation evaluation; no recursive ExecutePasses belongs in tags, generators or draw callbacks.

The final candidate passes full and Skip-3 classic XP/native compatibility/Direct PG fixtures, all three PLY encodings, SPZ3/4 reimport, cold replay and 161,688 active-particle XP/Direct PG smoke exports. Numerical assertions compare actual current particles, not just status strings or old point buffers. Managed spline points match, but a native Sweep referencing them differs on first pass versus same-time settled evaluation in all three paths. Do not infer arbitrary downstream zero-latency, interactive viewport acceptance, production NeXus or cache playback from these tests. Evidence: SplatFlow/tests/classic_runtime_gate.py and managed_lifecycle_gate.py and their raw runtime / lifecycle JSON.

## 224. A native plugin creation command can return false despite creating its object

**Demonstrated 2026-10-06, C4D2026.4.0, new NeXus beta.** GetCommandName(1067563) returned nxEmitter. CallCommand(1067563) returned false, causing the initial QA assertion to reject preparation. A separate inspection found exactly one newly created nxEmitter in the owned active document at frame0. Do not replay uncertain creation because of the return value alone. Verify the owned document, expected object count/type and active object first, then complete that same object if the side effect is confirmed. The command-name search tool returned zero matches, yet this native command did create the object; name-filter results are not a complete behavioral guarantee. Native-menu versus BaseObject scalar defaults differed in colour vectors, not a proven simulation setting. Evidence: QA/driver_trial_2026-10-06/MENU8000_PREPARE_RAW.json, MENU8000_PREPARE_RECOVERED_RAW.json and QA/nexus_61692_menu_transport8000/creation_comparison.json.

## 225. Separate beta-engine loadedness isolation from particle-binding qualification

**Demonstrated 2026-10-06, C4D 2026.4.0, RTX 5090, a NeXus 2026.1 beta build.** After a verified reboot and a GPU driver update, an eight-shot native-Play control passed frames 0..58, while an 8,000-shot emission still crashed Cinema at its first emission boundary (Windows fail-fast 0xc0000409 in ucrtbase, with a matching GPU driver event). Native-menu creation did not change it. To separate the plugin from the engine, a final cold host started with the SplatFlow DLL moved outside the scanned plugin folders: no SplatFlow registration and no SplatFlow module loaded. The saved scene held one nxEmitter and zero tags, with no source mesh, template or observer. Native Play still crashed the same way, so SplatFlow and its callbacks are not necessary for this failure. Other plugins and the bridge stayed loaded; the exact beta/host/driver/hardware cause is unproven. Do not loosen binding/export gates or loop identical shots to mask it. Restore the exact DLL and preferences afterwards, and recover only the identified, already-crashed host.

The user clarified that their working alternative is classic XP plus NeXus modifiers, not the new-beta nxEmitter. A tiny nxGravity-child-on-classic-XP control under this installation produced no motion and legacy GPU status 0; it is not the same proven working recipe and was correctly rejected before export. Classic XP CPU gravity separately passed frames 0..12 with exact proxy/filament positions, three PLY encodings and SPZ3/4 reimport on the updated driver. Neither result certifies arbitrary hybrid GPU setups; compare against the actual working scene. Evidence: the project's driver-trial QA JSON and a minimal no-tag repro scene.

**Emission-count follow-up.** Another cold host with SplatFlow unloaded passed native 8- and 1,000-shot emissions with exact counts and finite moving positions (zero tags, forces or source meshes; display off; GPU-to-CPU transfer 2). A 4,000-shot emission crashed the same way. Stop at the first new failure: 8,000 was not repeated, and neither count is an exact threshold. An early 8-shot read of frame 0 / count 0 was inconclusive; native transport then advanced and passed, so it was not an emitter failure. Paused snapshots can cross a loop boundary, so compare differing frames plus finite position hashes rather than assuming monotonic frames. Afterwards the exact DLL was restored and its module path and hash verified in a healthy reopen; the bridge was not autostarted, so no post-reopen registration readback is claimed.

## 226. Finish independent cell smoothing locally; separate Python mesh readback from native memory soaks

**Demonstrated 2026-10-08, C4D 2026.4.0, Bubblor built with SDK 2026.3.** Foam smoothing has no adjacency between bubbles. Completing all passes inside each bubble worker removes per-pass global barriers and replaces full-mesh scratch arrays with small worker-local arrays. Preserve arithmetic order and the Whole Surface per-pass fold guard; independent work is not permission to remove geometric safeguards. Acquiring writable Float32 vertex-map buffers on the private output before workers run also avoids five full-mesh Float64 temporary maps. Each worker writes disjoint point slices and does not call live scene APIs. Disabled map outputs need not be calculated.

The candidate matches the preceding build's native geometry exactly in four dense performance fixtures and 18 seed/mode/inflation cases; map/selection hashes match in six mode/bridge cases, and all 32 map-toggle combinations pass. On the same 2026.4 host, warm 5.74M-point Pearl edits fall from 330.78 to 172.78 ms Exterior and 559.84 to 320.70 ms Whole Surface. These exclude viewport draw. Full regression contracts pass 401/401, with three additional 61-frame procedural loops, stable counts and exact endpoint hashes. This does not certify arbitrary renderer behavior or artist acceptance. New examples use Standard rendering, with Whole Surface chosen for softer compressed junctions where Exterior intentionally preserves angular deep contacts.

A first 600-edit private-memory soak failed after repeatedly hashing the complete mesh inside the measured loop. Native sphere, empty generator, fixed/alternating smoothing and disabled-tag controls stayed essentially flat without those readbacks. Thirty identical full hashes of a detached 269,730-point / 532,800-face static polygon, after destroying its Bubblor document, reproduced about 175 MiB growth. Bubblor evaluation is therefore not necessary for that observed readback growth; the exact host/Python allocation cause remains unproven. Moving full readbacks outside the measured loop yielded zero net private-memory growth after warm-up over 600 edits and an exact final mesh rewind. Keep deterministic geometry assertions and memory measurements as separate gates, retain the failing harness evidence, and do not label a bounded soak universally leak-free.

The normal installation remained unchanged because an old process still mapped its DLL and scene preservation could not be inspected. An isolated g_prefspath profile allowed candidate loading and testing without replacing that mapped file. OS module path, candidate hash and native API preferences confirmed the QA installation. Both the custom socket bridge and configured native MCP were usable; a native asynchronous Standard render completed. This is not a comparative connector reliability benchmark. Evidence: Projects/Bubblor/PRODUCTION_PASS_2026-10-08.md, tests/results/production_20261008/ and dist/Bubblor_candidate_2026-10-08_DA22D8D0.zip.

## 227. Qualify an isolated native candidate in c4dpy before restarting the artist GUI

**Demonstrated 2026-10-09, Cinema 4D 2026.4.0, Bubblor 87F6E3D4.** The installed c4dpy.exe loaded a candidate DLL/resources under a separate g_prefspath while the artist continued using the earlier DLL in the GUI. A second private preference directory held the old DA22D8D0 reference. Both hosts ran the same 18 mesh/map hash cases and exited naturally with code 0; the candidate additionally passed eight focused groups, 144 affected existing contracts, a 61-frame Field loop and Standard-rendered comparisons. This is positive evidence for this local headless workflow, not a universal resolution of entry 163's historical exit failure. Do not force sys.exit to manufacture a clean shutdown. Headless registration/API/render checks still do not prove GUI interaction or native viewport FPS.

For the subsequent GUI update, both current artist documents were cloned to new checkpoints, loaded privately, and compared for hierarchy, transforms, source links, scalar generator parameters, materials, frame and FPS before removing any in-memory original. The GUI then quit normally. After confirming exit, the explicitly selected QA installation was backed up and updated. The new process mapped the expected DLL path/hash; both artist documents were restored at frame 30 with the prior active scene, selected objects, names and original paths. Original scene files were not overwritten. Changing document names/paths during restoration marked both tabs changed, including the previously clean one; this is not evidence of lost scene content. Keep the verified checkpoint and manifest regardless.

Evidence: Projects/Bubblor/CONTACT_GUIDES_2026-10-09.md, tests/guides_contacts_regression.py, tests/contact_candidate_acceptance.py, tools/contact_session_checkpoint.py, and tests/results/contacts_20261009/. Candidate/package qualification remains separate from the prior 401-contract sweep and 600-edit soak; those older results must not be relabeled as tests of the new binary.

## 228. Prune dense native spline output during collection without changing the solver

**Demonstrated 2026-10-09, C4D 2026.4.0, Bubblor F960C923.** An artificial 100,000 guide ceiling prevented the artist's intended dense correspondence web. The corrected native generator emitted 14,109,959 linear segments (28,219,918 points) in a private copy of the current Star scene. Stable 10% selection emitted 1,412,294; adding a 0.1–1 cm length range emitted 46,695. Bubble-mesh hashes were identical across those output-only changes. Guide Limit 0 means no artistic count cap; native signed point indexing and allocation success remain actual storage constraints. These counts are a tested case, not a guarantee of smooth drawing at that density.

Pruning hashes fixed bubble/vertex/iteration identities, not positions, time or filtered traversal order. Thresholding that hash gives deterministic nested subsets as Keep changes; the requested percentage is statistical, not an exact quota. Filter before appending segments and allocating the SplineObject so rejected records need not occupy the full dense output buffer. Bridge deformation must continue through every match even when a guide is discarded or an optional count limit is reached. Exact old/new parity was verified on all 887,558 nonzero-length guides of a separate reference fixture, including order, mesh and vertex-map hashes. On the largest scene use native counts and bounded GetPoint samples; avoid materializing tens of millions of Python vectors just to count output (see entry 226).

Native Current State to Object preserved 4,141 pruned editable guides in the lifecycle fixture. Bubblor's custom Make Geometry Copy is a polygon-only flattening operation and does not preserve splines; do not infer identical output types from similarly named bake commands. Evidence: Projects/Bubblor/DENSE_GUIDES_2026-10-09.md, tests/dense_pruning_regression.py and tests/results/dense_pruning_20261009/. Five focused groups and 33 affected existing contracts passed; the previous wider suites are not new-binary qualification.
