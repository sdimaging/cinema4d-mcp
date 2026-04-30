# cinema4d_mcp_helper Phase B — NodeTemplate publishing design

**Status:** design / pre-implementation, post-Phase A.1 conclusion (2026-04-30)
**Goal:** programmatically register a Scene-Nodes-space `NodeTemplate` whose Floating-IO-style ports surface as Attribute Manager parameters when an instance is dragged from the Asset Browser. The actual unlock for "user-tunable capsule with custom AM params" — the holy-grail capability identified at the start of this session.

---

## Why Phase A.1 didn't reach this

Phase A.1 tried runtime `GraphModelInterface::AddPort` on existing FloatingIO instances. **Confirmed unsupported across 4 variants** (gotcha #36). FloatingIO is `/// INTERNAL.` in the SDK; the editor's drag-wire UX uses a higher-level NodeTemplate-instantiation operation, not in-place port mutation.

What we kept from Phase A.1:
- Working Python ↔ C++ bridge (gotcha #34 — `SpecialEventAdd` + `BFM_CORE_ID` filter)
- Worker-poll dispatch (gotcha #35)
- Build automation (`build_cpp_shim.sh all` end-to-end)
- All infrastructure transfers directly to Phase B.

---

## Phase B mechanism — the Maxon SDK pattern

Reference: `plugins/example.nodes/source/space/dynamic_node_impl.cpp`

```cpp
// Class registered at plugin-init time
class DynamicNode : public maxon::Component<DynamicNode, maxon::nodes::NodeTemplateInterface>
{
    MAXON_COMPONENT(NORMAL, maxon::nodes::NodeTemplateBaseClass);

public:
    MAXON_METHOD maxon::Result<maxon::Bool> SupportsImpl(const maxon::nodes::NodeSystemClass& cls) const
    {
        // Limit to a specific node space (Scene Nodes for our use case)
        return cls.GetClass() == /* maxon::neutron::SceneNodesNodeSystemClass */;
    }

    MAXON_METHOD maxon::Result<maxon::nodes::NodeSystem> InstantiateImpl(
        const maxon::nodes::InstantiationTrace& parent,
        const maxon::nodes::TemplateArguments& args) const
    {
        iferr_scope;
        maxon::nodes::MutableRoot root = parent.CreateNodeSystem() iferr_return;

        // Read a "port spec" template argument (Python-controlled)
        const cinema::String& specJson = args.GetValueArgument<cinema::String>(specPort).GetOrDefault();

        // Parse spec, create ports accordingly
        // For each spec entry:
        maxon::nodes::MutablePort port = root.GetInputs().AddPort(portId) iferr_return;
        port.SetType<DesiredType>() iferr_return;
        // ... etc

        return root.Finalize() iferr_return;
    }
};

// Static registration at plugin-init time
MAXON_DECLARATION_REGISTER(maxon::nodes::BuiltinNodes, MyTemplateId)
{
    return DynamicNode::GetClass().Create().SetId(...).
}
```

**Key insight:** the NodeTemplate CLASS is static (registered once at plugin load), but the resulting NODE STRUCTURE is dynamic, driven by `TemplateArguments` passed at instantiation time. So a single static C++ class can produce arbitrary configurable capsules from runtime spec strings.

This is exactly what `dynamic_node_impl` demonstrates with its `code` parameter: a comma-separated string drives port creation per-instance.

---

## Phase B incremental targets (per GPT-5.5 review discipline)

### B.0 — Minimal "passthrough" template (target first)

**Goal:** prove the registration + Asset-Browser-visibility loop with the smallest possible template.

- 1 input port (`Float64`)
- 1 output port (`Float64`)
- Internal: `output = input` (passthrough)
- Registered as `net.sdimaging.test.passthrough.v1`
- Targets Scene Nodes space (`maxon::neutron::NODESPACE`)
- No `TemplateArguments` — completely static for B.0.

**Verification:**
1. Build + install the .xdl64
2. Restart C4D
3. Open Asset Browser, search "passthrough"
4. Confirm it appears under appropriate category
5. Drag into Scene Nodes editor — instance should have 1 input + 1 output
6. Confirm `repo.FindLatestAsset(NodeTemplate, asset_id, ...)` returns it via Python

**If B.0 succeeds:** the registration path works; scaling to dynamic ports is mechanical.
**If B.0 fails:** debug at this minimal level (much easier than chasing down a full dynamic system).

### B.1 — TemplateArguments-driven dynamic ports

Once B.0 confirms the registration loop:
- Add a `spec` String template argument
- `InstantiateImpl` parses the spec (JSON or simple delimiter format) to determine port count + types
- Each unique spec value produces a different node structure (each is its own asset, distinguished by the spec hash in the asset ID)

### B.2 — MCP wrapper

The Python plugin gets a new MCP tool:
```
scene_nodes_publish_capsule_template(
    asset_id: str,         # e.g. "com.userdomain.mycapsule.v1"
    asset_name: str,
    port_spec: dict,       # {inputs: [{name, type}], outputs: [...]}
)
```
Routes through the existing `_mcp_helper_dispatch` (op = `OP_PUBLISH_NODETEMPLATE`).

C++ side reads the spec from `BC_KEY_PORT_SPEC` (new BC key), invokes the dynamic instantiation, registers the result via `AssetCreationInterface` as `net.maxon.node.assettype.nodetemplate`-typed asset.

### B.3 — Verify AM-param surfacing

The original goal: an artist drags the published asset into a doc, sees AM parameters for each port. Verifies the entire pipeline.

---

## Open research items before B.0

1. **Which `NodeSystemClass` is Scene Nodes?** Need the C++ identifier for Scene Nodes space (the user-graph space, not the material space). `maxon::neutron::NODESPACE` is the LiteralId; the corresponding `NodeSystemClass` symbol may be different.
2. **`SupportsImpl` filter syntax** — confirm what to compare `cls.GetClass()` against to limit registration to Scene Nodes.
3. **`MutableRoot::Finalize()`** — verify this is the correct return-call (vs `Build()`, `Done()`, etc.).
4. **`MAXON_DECLARATION_REGISTER` second arg** — the `objectId` and `Id()` semantics for naming the registered template.
5. **Asset metadata required by the Asset Browser** — display name, category, icon, description. May need accompanying `.res` resource files.
6. **Whether registration to `maxon::nodes::BuiltinNodes` makes assets show in the Asset Browser**, or if we additionally need to register via `AssetCreationInterface::SaveDocumentAsset` or similar.

These get answered during B.0 implementation.

---

## What NOT to do in Phase B (per GPT discipline)

- ❌ Don't try to build a full dynamic-args system on the first commit. B.0 is one-input-one-output static.
- ❌ Don't bundle B.0 with B.1 / B.2. Each phase is its own commit + verify cycle.
- ❌ Don't claim Phase B "works" until an actual user-saved asset surfaces AM params on drag-instantiation.
- ❌ Don't skip the Asset Browser visual verification — that's the artist-facing test.

---

## Estimated effort

Rough scope per phase, based on the Phase A iteration count:
- **B.0** — 2-4 hours (1-2 build cycles to get registration shape right, 1 verification pass)
- **B.1** — 2-4 hours (TemplateArguments + parsing + per-spec uniqueness)
- **B.2** — 1 hour (Python MCP wrapper, mostly mechanical)
- **B.3** — 1 hour (verification + capture findings)

Total: ~8 hours when you're ready. Best as its own focused session — Phase A.1 was a long day already.

---

## When to start

User decides. Infrastructure is ready; nothing blocks Phase B.
