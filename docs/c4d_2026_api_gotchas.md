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
inline const String& MaxonConvert(const maxon::String& val);  // maxon -> cinema
inline String MaxonConvert(maxon::String&& val);
inline const maxon::String& MaxonConvert(const String& val);  // cinema -> maxon
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
    iferr_scope;  // Scope marker — required for iferr_return to work.
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
maxon::GraphNode root = graph.GetViewRoot();  // NOT GetRoot()
```

`graph.GetViewRoot()` returns the root GraphNode at `GetViewRootPath()`. If
you need recursive traversal, use `GetInnerNodes`:
```cpp
graph.GetInnerNodes(root, maxon::NODE_KIND::NODE, false,
    [&](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool>
    {
        // process candidate; return true to continue iteration
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
Int  FindIndex(...)        // returns -1 if not found, vs Bool result
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
        // process candidate
        return maxon::Bool(true); // continue
    }) iferr_return;
```
Same applies to `GetChildren` — both are on the GraphNode, with the underlying
implementation forwarded to the graph internally.

---

## 33. `Result<GraphNode>` from `AddPort` may need explicit unwrap (not just `iferr_return`)

`container.AddPort(portId)` returns a templated `Result<GraphNode>`. The
chained `iferr_return` pattern that works for most other Maxon APIs:
```cpp
maxon::GraphNode newPort = container.AddPort(portId) iferr_return;  // FAILS
```
sometimes fails to compile with:
```
error C2440: 'initializing': cannot convert from 'maxon::Result<maxon::GraphNode>' to 'maxon::GraphNode'
```
This appears to depend on the inferred type of the `container` (the
SFINAEHelper template that drives `AddPort`'s return type may resolve to
something `iferr_return` doesn't unwrap cleanly).

**Bulletproof workaround — explicit Result unwrap:**
```cpp
maxon::Result<maxon::GraphNode> portResult = container.AddPort(portId);
if (portResult.IsError())
    return portResult.GetError();
maxon::GraphNode newPort = portResult.GetValue();
```
Verbose but always compiles. Use this pattern when `iferr_return` fights you.

---

## 31. `GraphNode.AddPort(name)` lives on the PORT (not the graph)

The C++ surface for adding a named port to an existing port-container has
two callable forms:

```cpp
// On the graph:
maxon::Result<GraphNode> AddPort(const GraphNode& parent, const Id& name);

// On a GraphNode (template wrapper that delegates to the graph):
Result<GraphNode> AddPort(const Id& name) const;
```

Both are equivalent. To add a named port to a Floating IO node's input
container, use the second form on the container (NOT on the FIO node):
```cpp
maxon::GraphNode container = fio.GetInputs();   // or .GetOutputs()
maxon::Id portId;
portId.Init(MaxonConvert(portName)) iferr_return;
maxon::GraphNode newPort = container.AddPort(portId) iferr_return;
```

Calling `fio.AddPort(...)` directly fails — the FIO is a NODE, not a port
container. Always go through `GetInputs()` / `GetOutputs()`.

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
