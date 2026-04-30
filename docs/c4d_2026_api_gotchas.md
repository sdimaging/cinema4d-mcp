# C4D 2026 API Gotchas — discoveries from MCP development

A reference sheet of things the C4D 2026 Python API does that don't match
older docs / tribal knowledge / what felt right. Each entry has the wrong
assumption, the actual behavior, and how it was discovered.

Maintained as bugs surface during MCP plugin development. Useful for
anyone building agent integrations against C4D 2026.

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

**Wrong:** Sending `/mnt/c/Users/.../foo.png` as a `save_path` argument from WSL.

**Actual:** C4D's Python interpreter runs on Windows. `/mnt/c/...` is a WSL-mount path that Windows Python's `open()` can't resolve.

**Fix (server.py side):** Auto-translate `/mnt/<drive>/...` to `<DRIVE>:\\...` before sending the command. Already implemented in `_normalize_paths_in_command` — applied to known path-arg keys (`file_path`, `save_path`, `save_dir`, `bitmap_path`, `path`, etc.).

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

## Discovery process

This list grows organically. Whenever runtime contradicts an API
expectation, it gets logged here. The cinema4d-mcp project's contract
tests + recipe suite catch most of these on first run, which is
exactly why those exist.

If you're building against C4D 2026 and hit something that contradicts
the C4D Python docs, please open an issue or PR with the discovery —
keeping this list current saves everyone time.
