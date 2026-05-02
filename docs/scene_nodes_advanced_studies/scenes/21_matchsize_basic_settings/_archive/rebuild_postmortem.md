# Match Size rebuild post-mortem — what I got wrong, what's actually true

> **CRITICAL semantic correction (Spenser, 2026-05-01):** Match Size is NOT a "scale A to be the size of B" tool. It's a **NORMALIZATION tool** for taking many varied input elements and making each one fit the same target bbox envelope, so they can be stacked/aligned/cloned in a row uniformly. Use cases: a row of modular kit pieces, the Stone-Circle scene's 5 different Megascans rocks all coming out unified, the Windows scene's Window+Handle pairs each fitting different Boole holes. My MVP rebuild that "scales a sphere 4× the size of a cube" performed a related but DIFFERENT operation (uniform multiply) — it proved the plumbing works but did NOT capture the actual algorithm intent. The TRUE Match Size: per-element, compute target_envelope_size / source_bbox_size per axis, apply that as the per-axis scale (potentially with anchor/pivot offset), so EVERY input ends up filling the SAME envelope regardless of its native size. **Multiple Match Size deformers all pointing at the same target = a normalization wand for the whole scene's modular pieces.**

> **🎉 PROOF UPDATE (2026-05-01 evening):** After this correction, I drove the rebuild to actually demonstrate normalization. Three different source shapes (Sphere r=50 → bbox 100×100×100; Cone bbox 160×200×160; Cylinder bbox 120×300×120) each got their own custom-built SN deformer with `compose.scale = target_envelope / source_bbox` computed per-axis, all targeting a 300×100×200 envelope. **All three deformed visually — each squashed/stretched per-axis to fit the SAME target envelope.** The flying-saucer-shaped Sphere (`compose.scale = (3, 1, 2)`) is the cleanest demonstration. See [rebuild_15_NORMALIZATION_PROOF_3_inputs.png](frames/rebuild_15_NORMALIZATION_PROOF_3_inputs.png). Algorithm intent CAPTURED.

## The 3 critical breakthroughs needed to reach this point (in order)

1. **`SetDirty` is required for SN-deformer cache invalidation.** `c4d.EventAdd()` alone is not enough. Without `host.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_DESCRIPTION | c4d.DIRTYFLAGS_MATRIX)` + `parent.SetDirty(...)` + `c4d.EventAdd(c4d.EVENT_FORCEREDRAW)` + `doc.ExecutePasses(...)`, the deformer evaluates its graph but the parent's polygon cache stays stale → viewport shows native un-deformed geometry even though the math is correct. **This was the single biggest blocker.** Hours wasted thinking my graph was wrong when it was actually computing fine — the visible result just wasn't being refreshed.

2. **`GetPortValue()` returns cached/default values, NOT runtime-evaluated values.** Throughout debugging, GetPortValue on `arith.out` returned `(4,4,4)` even when the math should produce different results. Trusting GetPortValue led me down many false paths. **Always trust the visual outcome, not the API's port-value query.** This is a graph-API gotcha to document loudly.

3. **`composematrix.scale` is the artist-friendly per-axis scale slot.** Set it via `port.SetDefaultValue(maxon.Vector(sx, sy, sz))` inside a transaction and the resulting matrix correctly carries a non-uniform scale (no need to set rotation/translation explicitly — they default to identity, no need for `rotationorder`). For a normalize-to-target deformer, `compose.scale = target_size / source_size` (computed per-axis) IS the algorithm.


**Goal of this doc:** turn my failed-and-recovered rebuild into a community-grade reference. Every assumption I made, the actual ground truth from the reference working build, and the lesson learned.

If you're trying to author your own SN deformer/distribution from scratch via Python + maxon API, **this is the cheat-sheet** that will save you the dead-ends I hit.

---

## How to read this

| # | Step | What I ASSUMED | What's ACTUALLY true | Lesson / gotcha |
|---|------|----------------|----------------------|-----------------|

### 1. Discovering canonical asset IDs

| 1.1 | Find a node by basename | `add_node(asset_id="transform_element")` would resolve | **Bare basenames are not asset IDs.** `$type` resolver in `GraphDescription.ApplyDescription` only accepts ENGLISH UI LABELS (e.g. "Bounding Box") OR fully-qualified canonical IDs that are registered for label resolution. `transform_element` is the BASENAME of the instance ID (text before `@hash`), NOT the asset ID. | Use `scene_nodes_list_assets(source="repository", filter_substring="...")` to find canonical IDs. The repository search is the source of truth. |
| 1.2 | UI label "Transform Element" works | The UI label "Bounding Box" worked, so "Transform Element" should too | **It doesn't.** Label resolution is asset-by-asset and not all assets register an English UI label. `transform_element`'s actual UI label is something else (likely "Modify Element" or similar) — I never found it. | When `describe_node_template(label=X)` returns "label not resolvable", fall back to direct `graph.AddChild(maxon.Id(name), maxon.Id(canonical_asset_id))` with the full canonical ID from the repository. |
| 1.3 | Asset IDs follow a pattern | Maxon's IDs all start with `net.maxon.neutron.corenode.X` | **No, they're scattered across multiple namespaces:** `net.maxon.neutron.geometry.bb`, `net.maxon.neutron.geometry.transform_element`, `net.maxon.node.composematrix`, `net.maxon.node.arithmetic`, `net.maxon.node.floatingio`, `net.maxon.nbo.node.legacyobjectaccess`. | **Don't guess asset IDs.** Always look them up via `scene_nodes_list_assets(source="repository", filter_substring=basename)`. |

### 2. Adding nodes to a graph

| 2.1 | `graph.AddChild()` works directly | I tried `graph.AddChild(maxon.Id("my_xform"), asset_xform)` outside a transaction | **Failed:** `No current transaction for modification of NodesGraphModel.` | All graph mutations MUST be wrapped: `with graph.BeginTransaction() as txn: ... txn.Commit()` |
| 2.2 | `scene_nodes_add_node` MCP tool resolves canonical IDs | Tool's `asset_id` parameter would accept `net.maxon.neutron.geometry.transform_element` | **Failed** with the same "label not resolvable" error — because the tool routes through `GraphDescription.ApplyDescription` which is label-based. Only direct `graph.AddChild` works for canonical IDs. | When the MCP tool fails, drop into Python with the maxon API. Future MCP improvement: add a parallel "add_node_by_canonical_id" path that routes through `graph.AddChild`. |
| 2.3 | `scene_nodes_describe_node_template` is non-destructive | The tool docstring says "adds + removes the node — non-destructive" | **The bb probe LEAKED into the graph** (confirmed: `cleanup_via: null, cleanup_succeeded: false, leaked: true`). I had to manually clean it up before adding my own bb. | Always inspect `cleanup_succeeded` in the response. If `false`, manually remove the leaked instance before continuing. |

### 3. Port names — I was wrong about a lot

| 3.1 | `composematrix` ports | I assumed `v1`, `v2`, `v3`, `off` (basis-vector form, like the matrix ports in the reference graph) | **Maxon's `net.maxon.node.composematrix` ports are: `scale` (vec3), `translation` (vec3), `rotation` (vec3), `rotationorder` (int). Output: `out` (not `matrix`).** Much simpler / more artist-friendly than basis vectors. | The `composematrix@a_4qfS9...` instances I saw in the reference graph (with `_0``_1` floatingio routing children) appear to be a DIFFERENT NODE TYPE that just happens to share the basename. Could be `net.maxon.node.access.composematrix64` or similar — the access namespace seems to expose the basis-vector form. **Lesson: same basename ≠ same node type. Verify ports before assuming.** |
| 3.2 | `transform_element` inputs | I assumed: `geometryin`, `transformin`, maybe `selectionin` | **Actual: 11 inputs:** `geometryin`, `transformin`, `selectionin`, `selectionstringin`, `boundingboxxin`, `boundingboxyin`, `boundingboxzin`, `useselectionbbin`, `pivotmatrixin`, `pivotin`, `selectiontypein`. Plus 1 output `geometryout`. | The transform_element is much richer than expected — has explicit per-axis bbox-clamp inputs, pivot/selection-scope, etc. **For our recipes: leverage these — don't reinvent selection scoping when the node already does it.** |
| 3.3 | `bb` outputs | I assumed: just `bbox` (a single bounding box value) | **Actual outputs: `max`, `min`, `center`, `bbox` — 4 separate outputs.** Saves an arithmetic step (don't need to compute center yourself). | Inspect outputs FIRST before adding arithmetic compute downstream. |
| 3.4 | Root gateway port names | I expected `<` and `>` for input/output gateways, but didn't know what sub-children would exist | **Root `<` (input gateway) sub-children include the standard external inputs:** `time`, `frame`, `nimbus`, `globalmatin`, `searchpaths`, `fps`, `ocioconfig`, `renderspace`. And `geometryin` is **lazily synthesized** when you wire an output to root `>.geometryout`. **Root `>` (output gateway) sub-children: just `geometryout`** for a deformer (or `distdataout` for a distribution). | Don't pre-create geometryin — make the output connection first and the system creates the corresponding input. |

### 4. Wiring API mechanics

| 4.1 | `Connect()` direction | I was unsure if Connect goes source→target or target→source | **`source_port.Connect(target_port)`** — the OUTPUT side calls `.Connect(input_side)`. Tested: `xform.geometryout.Connect(root>.geometryout)` works; `compose.out.Connect(xform.transformin)` works. | The receiving (input) port is the ARGUMENT to Connect. The output is the SELF. |
| 4.2 | `scene_nodes_connect_ports` MCP tool handles root< / root> | I assumed I could pass `to_node="<"` to connect to root gateway | **Failed:** `dest GetInputs failed: no target to copy for '<net.maxon.graph.interface.graphmodel>'`. The MCP tool's name resolver doesn't recognize `<``>` as valid node names. | For root-gateway connections, drop into Python. Find the gateway via `for c in root.GetChildren(): if str(c.GetId()) == "<": ...` |
| 4.3 | Port discovery in active graph | I assumed `port.GetConnections(maxon.PORT_DIR.INPUT)` gives me incoming wires, OUTPUT gives outgoing | **Confirmed correct.** For backtracing from an output: query INPUT direction. For tracing what a port feeds: query OUTPUT direction. | Use `walk_back` recursion via INPUT direction to backtrace from the OUTPUT GATE node — that's the most efficient way to map a graph's data flow. |
| 4.4 | Setting port values | I assumed `port.SetDefaultValue(value)` works in any context | **Works inside transaction; silently fails outside.** Also values must be `maxon.Vector``maxon.Float` etc. — passing `c4d.Vector` doesn't always work. | Always wrap value-set in `with graph.BeginTransaction() as txn: ... txn.Commit()`. Use maxon types: `maxon.Vector(x, y, z)`. |

### 5. Architectural inferences from tear-apart — what was right vs wrong

| 5.1 | OUTPUT GATE (single chokepoint) | I inferred from delete-test that `transform_element@dty…` is the single output gate | **CORRECT** — verified again during rebuild: my own `my_xform.geometryout → root>.geometryout` connection produces the single-source pattern. | The "single OUTPUT GATE" is a deliberate authoring pattern — adopt it for our own builds. |
| 5.2 | `bb` reads SOURCE bbox, `legacyobjectaccess` reads TARGET | Inferred from "delete each → torus collapses to pancake" | **Half right.** Both ARE critical, but the SPLIT-OF-LABOR isn't `bb=source vs legacy=target`. Likely both bb AND legacyobjectaccess are used for BOTH source and target reading at different stages. The 3 bb's might be for: input-mesh bbox, target-object bbox (via legacyobjectaccess feeding mesh into a 2nd bb), and a 3rd derived bbox. | Re-examine: which `bb` reads what? My rebuild only used 1 bb (for source) and would need a `legacyobjectaccess → bb` chain for the target. The 3-bb pattern in the reference build implies this two-stage read. |
| 5.3 | `inversematrix` is dead code in default Global mode | Inferred from "delete x2 → no visible change" | **CORRECT** — the inversematrix nodes are wired through unused Local-mode branches in default Global mode. To VERIFY this fully, would need to switch to Local mode and re-test deletion → predict that deletion would now break things. | Deleting a node that produces no visible change does NOT prove it's useless — it might be needed in a different mode. The right test is: try the deletion in EACH MODE and see when it breaks. |
| 5.4 | `composematrix` in the reference graph uses scale/trans/rot inputs | I assumed Maxon's standard composematrix port form | **WRONG (probably).** the reference `composematrix@a_4qfS9...` has children `_0` and `_1` — these are floatingio routing nodes. The ports likely are `off`, `v1`, `v2`, `v3` (basis-vector form via the `net.maxon.node.access.composematrix32/64` namespace). My MVP rebuild used the simpler scale-vector form. **Both work, but they're different node templates.** | When two graphs use the "same" basename, verify they're the SAME asset ID by checking the namespace path. The `access.composematrix*` form vs the bare `composematrix` form serve different layout/UX needs. |
| 5.5 | Internal `cube` is debug viz | Inferred from "delete → no change" | **CORRECT** — confirmed safe to delete in basic-settings. Likely the reference build used it during authoring to visualize the target bbox as a wireframe overlay. | Pattern recognition: any "primitive geometry generator" inside a deformer that's NOT in the output chain is almost certainly a debug aid. |
| 5.6 | 7 transform_elements are organized in a CHAIN | Backtrace showed `EZxTzq → XEOKhg → if(QIDSSw, 3 inputs) → dty (final)` — a 3-deep chain | **PARTIALLY right.** The chain is at least 3 deep, but the if-branch with 3 inputs means the OTHER 4 transform_elements feed into one of those 3 inputs — so the topology is actually a TREE merging at the if, not a strict linear chain. Per-axis variants likely each get their own transform_element + selection scope. | Backtrace alone doesn't reveal the full tree if there's an if-branch in the way. Need to also walk DOWN from each unrelated transform_element to see where IT feeds. |

### 6. Things I never figured out (open blueprint gaps)

These remain UNVERIFIED in my study — for the community to fill in by deeper tear-apart:

- **The 16 `if` branch conditions** — what does each one branch on? Likely some combination of: per-axis enable flags, mode toggles (Local/Global), anchor-mode, "match position vs match scale", "match-when-active" boolean, etc.
- **The 13 `switch` selectors** — what mode does each switch dispatch on? Likely tied to enum-type AM controls (Anchor mode, Coordinate space, etc.)
- **Selection scoping per `transform_element`** — each has a `selectionstringparser` child. What selection STRING does each pull (e.g. "all", "named selection X", "inverted selection")?
- **The 15 floatingio → AM parameter mapping** — which floatingio in the graph corresponds to which AM control labeled "X Scale", "Y Scale", etc.?
- **Why exactly 3 `bb` nodes** — my hypothesis (source-bbox, target-bbox, derived-anchor-bbox) is unverified. Could also be 3 different per-axis decompositions.
- **The role of `connect_geometries` × 2 + `delete` + `invertselection`** — these only run in selection-restricted mode. In a basic global match, they're likely passthrough. Need a scene with a selection tag to verify.

The next-level tear-apart would: switch to each non-default MODE one at a time, then delete the "dead-code" nodes — predicted result: that mode now breaks. That's how to confirm what each mode-specific branch does.

---

## What this rebuild proved (community-actionable)

A minimum viable **custom Match Size deformer** = **3 nodes + 3 connections**:

```python
# Setup
host = create_sn_deformer_on_parent_geometry()
graph = host.GetNimbusRef("net.maxon.neutron.nodespace").GetGraph(nspace)

# Add nodes (transaction required)
with graph.BeginTransaction() as txn:
    bb      = graph.AddChild(maxon.Id("my_bb"),      maxon.Id("net.maxon.neutron.geometry.bb"))
    compose = graph.AddChild(maxon.Id("my_compose"), maxon.Id("net.maxon.node.composematrix"))
    xform   = graph.AddChild(maxon.Id("my_xform"),   maxon.Id("net.maxon.neutron.geometry.transform_element"))
    txn.Commit()

# Get ports
xform_geom_in  = next(p for p in xform.GetInputs().GetChildren()  if str(p.GetId()) == "geometryin")
xform_geom_out = next(p for p in xform.GetOutputs().GetChildren() if str(p.GetId()) == "geometryout")
xform_xfin     = next(p for p in xform.GetInputs().GetChildren()  if str(p.GetId()) == "transformin")
compose_out    = next(p for p in compose.GetOutputs().GetChildren() if str(p.GetId()) == "out")
compose_scale  = next(p for p in compose.GetInputs().GetChildren()  if str(p.GetId()) == "scale")
root_in        = next(c for c in root.GetChildren() if str(c.GetId()) == "<")
root_out       = next(c for c in root.GetChildren() if str(c.GetId()) == ">")

# Wire (transaction required) — make the OUTPUT connection FIRST so geometryin auto-synthesizes
with graph.BeginTransaction() as txn:
    xform_geom_out.Connect(next(p for p in root_out.GetChildren() if str(p.GetId()) == "geometryout"))
    compose_out.Connect(xform_xfin)
    compose_scale.SetDefaultValue(maxon.Vector(4.0, 4.0, 4.0))  # hardcoded for now
    txn.Commit()

# Now geometryin appears in root< — wire it
with graph.BeginTransaction() as txn:
    root_geom_in = next(p for p in root_in.GetChildren() if str(p.GetId()) == "geometryin")
    root_geom_in.Connect(xform_geom_in)
    txn.Commit()

c4d.EventAdd()
```

**Result:** parent geometry is uniformly scaled by 4×. (To upgrade to true target-driven match: wire `bb.bbox``bb.max - bb.min` through arithmetic ratio compute → `compose.scale`.)

---

## For the community MCP

The biggest win for the cinema4d-mcp would be:

1. **Asset ID lookup helper:** input basename, return canonical IDs across all namespaces (auto-search `net.maxon.neutron.*` + `net.maxon.node.*` + `net.maxon.nbo.*`)
2. **Direct `add_node_by_canonical_id` path:** route through `graph.AddChild` instead of `GraphDescription.ApplyDescription` — no English-label dependency
3. **Root-gateway-aware connect:** allow `to_node="<"` / `to_node=">"` in `scene_nodes_connect_ports`, with auto-resolution to the gateway's named sub-port
4. **`scene_nodes_describe_node_template` cleanup fix:** investigate why bb-style probes leak; hardening this would prevent test pollution
5. **Port-value setter helper:** wrap `SetDefaultValue` with type coercion (accept `c4d.Vector` or `maxon.Vector`)

Each of these would close one of the gaps I hit. Together they'd let any community contributor build a custom SN deformer in <50 lines without battling the asset-ID/port-name maze.
