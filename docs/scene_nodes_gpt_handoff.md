# Scene Nodes MCP — GPT 5.5 Handoff

This document hands off the Scene Nodes MCP work-in-progress to GPT 5.5 for assistance with specific unblocking tasks. The full architectural context is in [`scene_nodes_guide.md`](scene_nodes_guide.md). This doc focuses on **concrete gaps** where pattern recognition + Maxon-API knowledge would unblock progress.

---

## Project context (1-paragraph version)

We're building a **Cinema 4D 2026 Scene Nodes authoring layer** on top of the cinema4d-mcp project (single-user MCP bridge between Claude/Cursor and a running C4D 2026.2 instance). Goal: artist says "build me a procedural scatter capsule" → MCP synthesizes a working capsule with parameters exposed to the Attribute Manager. We've shipped:

- **22 codified patterns** distilled from dissecting 12+ real-world capsules (Modulo, Random Selection, Dual Mesh, Surface Blue-Noise, Memory_Nodes, Ivy Generator, Squiggle Spline, Dual Mesh, Edge to Spline, Fractal Trees, Balloon Inflate, Time Offset, plus 4 numbered example scenes)
- **40 verified `$type` labels** confirmed working with `GraphDescription.ApplyDescription`
- **Port schemas for 33 nodes** with full input/output port names
- **802 NodeTemplate canonical asset IDs** indexed
- **10 MCP handlers** (dissect/walk/classify/atlas_lookup/apply_pattern/add_node/connect_ports/describe_node_template/create_capsule_with_pattern/list_assets)
- **Comprehensive guide** in `docs/scene_nodes_guide.md` (+9-section MD with architecture, anti-patterns, design principles)

Tier 1 battle test passed 6/6: `create_capsule_with_pattern` produces working capsules with verified-only patterns.

The data lives in:
- `data/scene_nodes_atlas.json` (24KB) — patterns + port-type taxonomy + antipatterns + design principles
- `data/node_template_index.json` (124KB) — all 802 canonical asset IDs categorized
- `data/verified_labels.json` — the 40 working labels
- `data/node_port_schema.json` — port schemas for the 33 templates we've probed

---

## What works (high confidence)

✅ Atlas data + 22 patterns — empirical, derived from real dissections
✅ 40 verified `$type` labels (full list in `data/verified_labels.json`)
✅ Dissection / inspection / classification toolchain
✅ `create_capsule_with_pattern` for verified-only patterns (4 of them: `loop_over_indices`, `hash_threshold_selection`, `procedural_surface_scatter`, `memory_capsule_state_carrier`)
✅ The 6-layer architecture model (asset registry → node spaces → graph models → graph nodes → DSL → capsules)
✅ The 3 design principles (capsule-first, selection-bidirectionality, simulation-via-memory+classic-tools)
✅ **Just discovered (2026-04-30): connection between two nodes works via imperative API**: `g.BeginTransaction()` + `source_port.Connect(dest_port)` + `txn.Commit()`. Verified live.

---

## Unblocking questions for GPT 5.5

### Q1 — ApplyDescription connection-only spec format (HIGH priority)

Connections via `BeginTransaction` + `Connect()` work imperatively. But we'd prefer the declarative form via `GraphDescription.ApplyDescription` for batched graph synthesis. Our attempts:

```python
# All of these fail with "Missing node type declaration":
ApplyDescription(g, {"$name": dest_id, port_name: {"$ref": f"{src_id}/output_port"}})
ApplyDescription(g, {"$name": dest_id, "<port_name": {"$ref": f"{src_id}>output_port"}})
ApplyDescription(g, {src_id: {"$type": "Memory", port: {"$ref": f"{dest_id}/in_port"}}})
```

**What's the canonical connection-spec format?** Per Maxon's Scene Nodes docs (which we couldn't fetch reliably — the help.maxon.net pages use JS routing that defeats fetching), there's likely a documented form like `$connections: [...]` or a per-node `>port` / `<port` notation we haven't tried.

If you have access to Maxon's docs or example .c4d files showing GraphDescription connection specs, please share.

### Q2 — Floating IO → Attribute Manager UD bridge (HIGH priority)

`Floating IO` is a verified node label. Single input port `net.maxon.node.floatingio.portlist`. Outputs: 0.

**Question:** What's the mechanism by which adding a `Floating IO` node to a capsule's embedded graph causes a parameter to appear in the host capsule's Attribute Manager? Is it:

- (a) Connecting a target port (e.g. Memory's `current` output) to Floating IO's `portlist` input, AND something else
- (b) The floating io needs its `portlist` parameter set to a specific kind of value (a "port description" struct?)
- (c) The capsule's `parambuilder` (one of the framework sub-nodes) needs to be informed about the floating io
- (d) Some other call required (registering the param with the host BaseObject's user-data container)

We can synthesize Floating IO nodes but **haven't verified that the resulting capsule actually shows the param in the Attribute Manager.** Without this bridge, the "capsule-first / artist-tunable parameters" promise is unfulfilled.

### Q3 — Resolve unverified `$type` labels (MEDIUM priority — incremental progress)

The following bare-names appear in dissections but no working English label has been found via probing. Each one would unlock 1-3 partially-stubbed patterns:

| Bare-name (from dissection) | What we tried | What patterns need it |
|---|---|---|
| `append`, `append2` | Append, Append Array, Add To Array, append2 (all fail) | array accumulation in loops |
| `concat` | Concat (fails), Concatenate (returns "ambiguous: array.concat OR string.concat") | Dual Mesh's name-mangling |
| `readvalueatindex` | Read Value At Index, Read Value At Index Array (both fail) | every loop body |
| `writevalueatindex` | Write Value At Index (fails) | output accumulation |
| `containeriteration` | Container Iteration, Iterate, For Each, Iterate Container (all fail) | every loop |
| `pushapart` | Push Apart, Push Apart Geometry, Push Apart Spline (all fail) | dynamics, reaction-diffusion |
| `lineget`, `lineset`, `assembler` | (untried) | spline-based capsules |
| `set_property`, `get_property` | Set Property, Get Property (fail) | reaction-diffusion, per-vertex tags |
| `closestpointonsurface`, `ray` | (untried — likely "Closest Point On Surface" / "Ray") | Ivy growth, cling patterns |
| `composevector3`, `matrixfromaxis`, `transformvector` | Compose Vector 3, Matrix From Axis (fail) | every transform stack |
| `selectionstringtoselection`, `selectionoperator`, `variadictolist` | (these are framework sub-nodes — possibly NOT user-addable at top level) | if needed at all? |
| `*frompolyids` family (`ptposfrompolyids`, `polynormalsfrompolyids`, etc.) | (untried) | Edge-to-spline-style mesh queries |
| `*fromptids` family (`vertexnormalsfromptids` etc.) | (untried) | per-vertex queries |
| `geoselectionrange` | (untried) | Dual Mesh-style topology transforms |
| `polygoninfo`, `pointinfo` | (untried — possibly "Polygon Info" / "Point Info") | Dual Mesh, Ivy attribute reads |

**Pattern observed:** verified labels are mostly the human-friendly English form ("Cross Product", "Boolean Operator", "Resample Spline", "Surface Blue-Noise"). Dissection bare-names are programmer-friendly compressed forms (`crossproduct` → `cross`, `booleanoperator` → `booleanoperator`, `surfacebluenoise` → `surfacebluenoise`).

**The C4D 2026 Asset Browser** is the canonical source — every node has an English display name visible there. If you can suggest pattern-based label guesses for any of the above that we should try, please share. The user has been hand-providing some by hovering in the Asset Browser; that's what unblocked Memory, Build Array, Resample (Spline), and a few others.

### Q4 — Connection-spec via ApplyDescription would let us batch graph builds (MEDIUM)

Currently `scene_nodes_create_capsule_with_pattern` emits all nodes via ONE `ApplyDescription` call (atomic), but connections require a SECOND step using `BeginTransaction + Connect`. If there's a way to specify connections inside the ApplyDescription dict, the whole pattern (nodes + wires) could be one atomic call — important for transactional integrity.

### Q5 — Operator-subtype capsule synthesis (MEDIUM)

The Modulo node has subtype `net.maxon.asset.subtype.node.operator` — it's an operator-class capsule with its own embedded graph (we dissected: 84 inner nodes). When you add an operator-class node to a graph, the framework auto-emits its inner sub-nodes (parambuilder, modelingoperator, etc.).

**Question:** Are operator-subtype capsules (like Modulo, Inset, Extrude, Surface Blue-Noise) SUPPOSED to be addable at the top level of a graph? They obviously are (we've added them). But their inner sub-nodes (`containeriteration`, `parambuilder`, etc.) are auto-emitted only when wrapped — so when WE see them in a dissection, they're not directly addable, only nested. Our atlas treats this as a hard rule but we'd like confirmation.

### Q6 — User-data exposure on a Scene Nodes Generator host BaseObject (LOW)

When a Floating IO inside a capsule's graph is wired to the input of a port, does the parent SN Generator object (180420700) automatically gain a corresponding USERDATA container entry? Or does the parent's userdata need to be programmatically populated separately via `BaseList2D.AddUserData(bc)`?

We have an `enumerate_userdata` MCP tool that can read the parent's UD. Plan was to test this after Q2 was answered, but if you know off-the-bat, please share.

---

## What GPT 5.5 could specifically help us produce

In rough priority order:

1. **A list of verified `$type` label guesses** for the unverified-bare-names list above, based on Maxon docs / pattern matching. Even 5-10 correct guesses would unlock 5+ stub patterns into fully-working ones.
2. **The canonical connection-spec format** for `GraphDescription.ApplyDescription` (Q1).
3. **The Floating IO ↔ UD bridge mechanism** (Q2). What's the missing step?
4. **A code review of `c4d_plugin/scene_nodes_patterns.py`** — flag any patterns whose synthesizer would produce a non-functional graph due to missing wires or wrong port names.
5. **Scene Nodes example .c4d files or maxon-frameworks code references** that show the connection-spec format in action (Maxon ships many in their documentation; we couldn't get the help.maxon.net pages via JS-routed URLs).

---

## Repo state

- Public branch: [`sdimaging/cinema4d-mcp/tree/main`](https://github.com/sdimaging/cinema4d-mcp/tree/main)
- Latest commit: `1e9af29` (README updated to 93 tools)
- Scene Nodes data in [`data/`](../data/) folder
- Pattern synthesizer: [`c4d_plugin/scene_nodes_patterns.py`](../c4d_plugin/scene_nodes_patterns.py)
- The full guide: [`docs/scene_nodes_guide.md`](scene_nodes_guide.md)

The codebase is single-user-local-dev style (auth-token gating optional, transport assumed trusted). This is intentional — the threat model is "the C4D process running on the artist's laptop," not multi-tenant SaaS.

---

## Reciprocal favor

If you can solve Q1-Q3, we'll bake your answers into the atlas + give credit. The atlas is meant to grow organically with every new capsule and label discovery — the more verified data we have, the closer the "I got you easy → 144 nodes" promise gets to delivered.
