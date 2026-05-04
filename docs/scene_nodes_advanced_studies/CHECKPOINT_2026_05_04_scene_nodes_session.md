# Scene Nodes Session Checkpoint — 2026-05-02 → 2026-05-04

**Status:** PAUSED at a clean checkpoint. Pivoting away from SN deep-dive for now; this doc is the revival anchor.

**Revival quick-start:** see [Section 8: When You Come Back](#8-when-you-come-back) at the bottom.

---

## TL;DR

A 3-day deep-dive into Cinema 4D 2026 Scene Nodes (Neutron) graph authoring via Python/MCP. Started trying to build a UV morph slider for DRuckli's "Build UV Preview" deformer, ended up:

1. **Cracking the canonical SN deformer authoring pattern** (8 Python idioms, including a previously-undocumented disconnect mechanism)
2. **Establishing 3 operating doctrines** (discipline protocol, recipe library, history-state snapshots)
3. **Building the first 3-recipe library** (BUV PathB, Centered toggle, wire-remove pattern) — all verified, with assembly test passed
4. **Capturing a 36-node algorithm snapshot** (relax-spline RD via Memory capsule) as the first history-state asset

Result: we now have **strong reference material** to teach SN authoring to others, and a **proven library workflow** for incrementally building reusable SN assets.

---

## 1. What was verified working (✓ green checkpoints)

### T1 PathB — true-from-scratch BUV replica
- 7-node + 11-wire MCP-authored deformer
- Bit-identical to DRuckli's "Build UV Preview" output (4664 verts, rad exact, mp Δ=0.01 vs reference)
- NO copy/paste, NO inner-capsule mutation, NO `uvtomesh` asset reuse
- Built using outer-level primitives (`get_property`, `containeriteration`, `splitvectorcomponents`, `invert`, `composevector3`, `scale`, `set_property`)
- Visual proof: `druckli_capsule_audit/03_uv_polygon_info/extension/_t1_pathb/multiview_*.png`

### T2 Centered toggle — math + parameter wiring
- Insert `arithmetic(sub, vec3)` between `composevector3.result` and `scale.in1`
- Toggle ON → mp = (-0.04, 0.17, 0) (origin-centered)
- Toggle OFF → mp = (24.96, 25.17, 0) (matches BUV exactly)
- Same rad in both states; topology preserved

### WIRE_MODE.REMOVE — disconnect API discovered
- `port.Connect(other, maxon.WIRE_MODE.REMOVE)` IS the disconnect mechanism
- WIRE_MODE values: NONE=0, NORMAL=16, REMOVE=62, ALL=63, IMPLICIT=64
- Major infrastructure unblock — earlier "no disconnect API exists" memory entry was wrong, retracted
- ⚠ NOTE: works for outer-graph wires but appears NOT to work for inner-capsule wires (failed during RD spline session). Needs `GraphModelHelper.RemoveConnection` or different approach for capsule interiors. SEPARATE TICKET.

### Recipe Library workflow validated end-to-end
- 3 verified recipes documented + locked in
- Assembly test PASSED: regenerated `SN_RECIPE_buv_pathb_uv_position` from `.md` script alone, on a different mesh (3×3 plane vs the 2×2 demo), got perfect 36-vert output
- Validates the doctrine: from-scratch discovery → verified recipe → reusable graph prefab → production assembly

### Reaction-Diffusion spline growth confirmed
- Loaded `Relax-Spline_01_Tutorial.c4d` (DRuckli, on Desktop)
- Visual proof of growth: frame 0 shows single seed spline, frame 60 shows full RD maze pattern filling the extruded "3" containment
- Located doc-level Scene Nodes graph via `doc.FindSceneHook(1054188)` (NOT via `doc.GetNimbusRef` which returns None)
- Outer chain mapped: `Spline → legacyobjectaccess → memory → tessellation → geometry op → scene_root.children`
- Memory body inventory: 36 nodes including `pushapart` (relax force) + 2× `closestpointonsurface` (substrate containment)
- **Memory capsule interior IS writable via Python** (unlike `uvtomesh` which is read-only) — full deep customization is possible
- Snapshotted: `recipes/history_states/HISTORY_STATE_relax_spline_memory_v1.snapshot.json` (36 nodes, 58 wires)

---

## 2. What got blocked / deferred (⚠ amber)

### T2 Factor slider (morph between 3D head and UV-flat)
- `readvalueatindex2` cross-stream lookup chain produces 0-vert output despite valid sub-chains
- Root cause unresolved: signal-routing problem deep in SN runtime, not graph-authoring
- For production: existing Python tag morph slider (sdimaging/c4d-scripts/uv-pipeline/morph_3d_to_flat_slider.py) remains the artist path
- Separate research ticket

### Path A — inner uvtomesh mutation
- Stock Maxon asset interiors are READ-ONLY via Python (`!self.IsReadOnly()` error on `AddChild` inside `CreateView`)
- Workaround: use UI "Edit Asset as Group" first to unlock, then Python can mutate
- BLOCKED for fully-MCP automation

### Inner-capsule WIRE_MODE.REMOVE behavior
- `WIRE_MODE.REMOVE` worked at outer-graph level (proven in T2 Centered toggle)
- Did NOT remove the wire inside the Memory capsule body (during RD spline collider-removal attempt)
- Both `compare(lt).in1` and `compare(gt).in1` ended up multi-source (broken state)
- Need to investigate: alternate API for inner-capsule disconnect, OR a different mode flag

### Memory body deep rebuild
- 36 inner nodes catalogued + snapshotted, but full from-scratch authoring NOT done
- Doctrine extension (history-state snapshots) provides path: replay snapshot into fresh memory
- Replay function sketched but not implemented

---

## 3. Critical Python authoring idioms (8 cracked)

Saved to user memory at `feedback`/`reference` files; reproduced here for portability:

### a. `MSG_CREATE_IF_REQUIRED` is required for root-port synthesis on new SN deformers
```python
new_def = c4d.BaseObject(180420400)
doc.InsertObject(new_def, parent=host)
new_def.Message(maxon.neutron.MSG_CREATE_IF_REQUIRED)  # ← critical
c4d.EventAdd()
```
Without this: `root.GetInputs()` and `root.GetOutputs()` return EMPTY; `FindChild` returns null-data stubs that silently fail in `Connect` calls.

### b. Root port topology is counter-intuitive
- `root.geometryin` lives in `root.GetInputs()` (but acts as a SOURCE for inner consumers)
- `root.geometryout` lives in `root.GetOutputs()` (but acts as a SINK for inner producers)

### c. `FindChild` requires `maxon.InternedId`, NOT `maxon.Id`
```python
# WRONG — silently returns garbage
p = node.GetInputs().FindChild(maxon.Id("portname"))
# CORRECT
p = node.GetInputs().FindChild(maxon.InternedId("portname"))
```
Misleading error message: "unable to convert builtins.NativePyData to @net.maxon.datatype.internedid" — points at SetPortValue, but the actual mismatch is on FindChild.

### d. Matrix64 ports require explicit positional construction
```python
# c4d.Matrix is rejected; MaxonConvert wrapper unwraps to c4d.Matrix; field assignment is silently ignored
m = maxon.Matrix64(
    maxon.Vector(0, 0, 0),    # off
    maxon.Vector(1, 0, 0),    # v1
    maxon.Vector(0, 0, -1),   # v2
    maxon.Vector(0, 1, 0),    # v3
)
trans_in.SetPortValue(m)
```

### e. Stock Maxon asset interiors are READ-ONLY via Python
```python
inner = graph.CreateView(maxon.NODE_KIND.NODE, uvtomesh_node.GetPath())
with inner.BeginTransaction() as tx:
    inner.AddChild(...)  # FAILS: "!self.IsReadOnly()"
```
Memory capsule (`net.maxon.node.memory`) is an exception — its interior IS writable.

### f. `WIRE_MODE.REMOVE` = the disconnect API (with caveat)
```python
src_port.Connect(dst_port, maxon.WIRE_MODE.REMOVE)
```
Works at outer-graph level. Inner-capsule wire removal is unreliable — needs further investigation.

### g. `containeriteration.datatype` cannot be set explicitly
- Fails with "Condition type->GetValueKind() & VALUEKIND::CONTAINER_REF not fulfilled"
- Skip — auto-infers from the wire connected to `iter.in`

### h. `set_property` with `newdataset=True` REBUILDS topology
- Configure: `accessortype=data3d, accessorname="" (empty), arraymode=False, newdataset=True`
- Wire: `get.topology → set.topology` + `<per-iter math> → set.iteration`
- Output mesh has one vertex per source poly-corner (4664 from 1166 polys × 4)
- **`accessorname` MUST be empty string `""`** — using `"Position"` or `"pt"` silently produces 0 verts

### Doc-level SN graph access
**Bonus, RD-spline-specific:** doc-level Scene Nodes graphs (the SN editor view, not per-object capsules) live on a SceneHook:
```python
sn_hook = doc.FindSceneHook(1054188)  # Scene Nodes hook ID
graph = sn_hook.GetNimbusRef(maxon.Id("net.maxon.neutron.nodespace")).GetGraph()
```
`doc.GetNimbusRef(...)` returns `None` for doc-level. The hook is the entry point.

---

## 4. Doctrines established (3 saved to memory)

### a. Scene Nodes Discipline Protocol
**File:** `~/.claude/projects/.../memory/feedback_scene_nodes_discipline_protocol.md`

Hard rules for SN work:
- No "shipped/solved/1:1" claims without verified visual + structural + numerical proof
- Copy-node = forensic-only (NEVER as proof of from-scratch authoring)
- Required reporting format (claim, method, copy-used?, source, target, nodes-by-MCP, connections, params, visual checks, structural checks, failures, confidence, next test)
- Banned language: "Solved", "Shipped", "1:1", "No asterisks", "Pure", "Verified end-to-end" — unless proven
- Allowed honest statuses: Hypothesis / Partial / Visually promising / Structurally incomplete / Recreated via copy NOT valid proof / Verified structurally / Verified visually / Production-ready

### b. Recipe Library Doctrine — Author-then-Assemble
**File:** `~/.claude/projects/.../memory/feedback_sn_recipe_library_doctrine.md`

Two-mode workflow:
- **Author Mode:** build from scratch, prove, document → produces a verified recipe
- **Assemble Mode:** pull verified library group, rewire, adjust safe params → production speed

Each recipe entry has 10 mandatory sections: provenance, asset IDs, port configs, wire list, IO contract, safe params, not-safe params, failure modes, MCP authoring script, verification test.

Folder layout: `recipes/SN_RECIPE_<name>.md` (authoritative) + `recipes/SN_Recipe_Library.c4d` (rapid-assembly cache).

### c. History-State Snapshot Doctrine
**File:** `~/.claude/projects/.../memory/feedback_history_state_snapshot_doctrine.md`

For complex inner-capsule bodies that are verified-working but expensive to fully decompose:
- **Snapshot** the working state (every node + asset ID + port values + wires) into structured JSON
- **Lock** as a versioned history-state asset with provenance + IO contract + substrate-redirection map
- **Make retrieval actionable** via a replay function that populates a fresh capsule from the snapshot
- **Decompose later** when value justifies it

This sits BETWEEN "documented recipe" and "verified prefab" — the snapshot IS the proof, the replay function IS the regeneration. Provenance preserved.

Folder layout: `recipes/history_states/HISTORY_STATE_<name>.md` + `.snapshot.json`.

---

## 5. Recipe Library — current entries

| File | Status | Provenance |
|---|---|---|
| `recipes/README.md` | Live | Library index + workflow + assembly test log |
| `recipes/SN_RECIPE_buv_pathb_uv_position.md` | ✓ Verified + assembly-tested | T1 PathB (2026-05-03/04) |
| `recipes/SN_RECIPE_centered_uv_toggle.md` | ✓ Verified | T2 Centered (2026-05-04) |
| `recipes/SN_RECIPE_wire_remove_swap_pattern.md` | ✓ Verified | technique entry (2026-05-04) |
| `recipes/SN_RECIPE_contained_rd_spline_growth.md` | ⚠ Documented, rebuild pending | Scene 17 study + 2026-05-04 forensic walk |
| `recipes/SN_Recipe_Library.c4d` | Live | Rapid-assembly cache with all 3 verified recipes grouped |
| `recipes/history_states/HISTORY_STATE_relax_spline_memory_v1.md` | Snapshot doc | First history-state entry |
| `recipes/history_states/HISTORY_STATE_relax_spline_memory_v1.snapshot.json` | Snapshot data | 36 nodes, 58 wires |

---

## 6. Major file index (where everything lives)

### In this repo (`cinema4d-mcp`)
- `docs/scene_nodes_advanced_studies/recipes/` — the recipe library (source of truth)
- `docs/scene_nodes_advanced_studies/scenes/` — 27 individual DRuckli scene studies (study.md per scene)
- `docs/scene_nodes_advanced_studies/druckli_capsule_audit/03_uv_polygon_info/extension/CANONICAL_SN_DEFORMER_PATTERN.md` — 664-line living doc with all 3 paths (a/a.5/b) + T2 status + WIRE_MODE.REMOVE breakthrough + Path (b) recipe + 8 idioms
- `docs/scene_nodes_advanced_studies/druckli_capsule_audit/03_uv_polygon_info/extension/UV_SLIDER_PROGRESS.md` — 1500+ line iteration log of the entire UV slider session
- `docs/scene_nodes_advanced_studies/druckli_capsule_audit/_relax_scrub/` — frame screenshots proving RD growth
- `docs/c4d_2026_api_gotchas.md` — public mirror of C4D 2026 API gotchas (referenced from user memory)

### In user memory (`~/.claude/projects/-mnt-c-Users-Spenser-Dickerson-Projects-LightPainter/memory/`)
- `feedback_scene_nodes_discipline_protocol.md` — the operating contract
- `feedback_sn_recipe_library_doctrine.md` — author-then-assemble doctrine
- `feedback_history_state_snapshot_doctrine.md` — snapshot doctrine
- `reference_sn_python_authoring_idioms.md` — 8 cracked idioms + working recipe template
- `reference_canonical_sn_deformer_pattern.md` — pre-existing canonical pattern + extensions
- `project_t1_pathb_buv_replica_complete.md` — milestone summary
- `MEMORY.md` — index (auto-loaded into every conversation)

### Source scenes (DRuckli reference; not in this repo)
- `~/Desktop/DRuckli/Relax-Spline_01_Tutorial/Relax-Spline_01_Tutorial.c4d` — the contained RD spline reference
- `~/Desktop/DRuckli/Relax-Spline_02_Optimized/Relax-Spline_02_Optimized.c4d` — variant
- `~/Desktop/DRuckli/Geometry-Solver_Scene-Files_01/03-RelaxSpline-Setup.c4d` — alternate setup

---

## 7. Open tickets (for revival)

### High-priority
1. **Inner-capsule WIRE_MODE.REMOVE alternative** — figure out why `WIRE_MODE.REMOVE` works at outer level but not inside Memory capsule. Try `maxon.GraphModelHelper.RemoveConnection`, alternate Wires flags, or graph-level vs view-level transactions.
2. **HISTORY_STATE replay function** — implement the full Python replay (build BASENAME_TO_ASSETID lookup, 3-pass populate-configure-wire) so we can clone the relax-spline body into a fresh memory capsule on demand.
3. **Substrate-swap test** — replace Landscape with Sphere/shoe; replace Text Spline with Nike swoosh; verify RD growth fills the swoosh interior on the shoe surface (the Nike-on-shoe POC).

### Medium
4. **T2 Factor slider blocker** — investigate why `readvalueatindex2.valueout → set.iteration` produces 0 verts. The signal isn't propagating per-iter. Likely a runtime context issue, not authoring.
5. **Pure-SN projection recipe** (`SN_RECIPE_spline_project_to_surface`) — abstract `closestpointonsurface` into a standalone primitive recipe (same pattern as the inner Memory algorithm uses). Useful as a building block.
6. **Connect-as-source pattern doc** — `legacyobjectaccess.Op` is polymorphic; sub-port `Op > Geometry` is the geo-out equivalent. Worth a focused recipe entry.

### Low
7. **AM exposure of Centered toggle** — wire `arith_center.in2` to a FloatingIO port for artist control via Object Manager (requires `cinema4d_mcp_helper` C++ shim; deferred).
8. **Recipe Library 2.0** — add the next 5-10 recipes (per the doctrine target list: container_iteration, geometry_passthrough, vertex_map_xyz_target, etc.).

---

## 8. When you come back

### Cold-start checklist
1. **Read this CHECKPOINT doc first** — it's the revival anchor
2. **Read `recipes/README.md`** — the recipe library entry point + assembly test record
3. **Re-read your auto-memory** — the doctrines + idioms are loaded automatically on conversation start; verify with a quick "what doctrines do you have for SN work?"
4. **Pick a ticket from Section 7** — most likely #1 (inner-capsule disconnect) or #3 (substrate swap) is highest leverage

### To resume the RD spline pivot specifically
- Open `Relax-Spline_01_Tutorial.c4d` from `~/Desktop/DRuckli/`
- Doc-level SN graph access: `doc.FindSceneHook(1054188).GetNimbusRef(maxon.Id("net.maxon.neutron.nodespace")).GetGraph()`
- Memory body snapshot already captured at `recipes/history_states/HISTORY_STATE_relax_spline_memory_v1.snapshot.json`
- The `compare(lt)` and `compare(gt)` inside Memory body use `distance.out` (which depends on the collider's `closestpointonsurface@Xf2B`). To remove the collider while keeping growth, both compare's `in1` need to be wired to the SAME source as their `in2` (the RELAX_SIZE param), so `lt(R,R)=false` and `gt(R,R)=false` — both erases skip. Surgery sketched but blocked on inner-capsule disconnect (ticket #1 above).

### To resume any other recipe work
- Use `SN_RECIPE_buv_pathb_uv_position.md` as the canonical example template
- The 8 Python idioms in Section 3 of this doc cover 90% of authoring gotchas
- The discipline protocol (Section 4a) governs claim language

### To teach others
This checkpoint + the recipes README + the canonical pattern doc + the 8 idioms ARE the teaching material. Hand someone:
1. This CHECKPOINT for the meta narrative
2. `recipes/README.md` for the workflow
3. `recipes/SN_RECIPE_buv_pathb_uv_position.md` for a full-worked example
4. `reference_sn_python_authoring_idioms.md` for the gotchas

That's a complete 0-to-authoring onboarding path.

---

## 9. Honest self-assessment

What went well:
- Recovered cleanly from 2 wrong calls (RD scene "is static" — twice; user corrected each time)
- The discipline protocol made retraction structured rather than messy
- Recipe library + history-state snapshot doctrines emerged organically from doing the work, then got formalized
- Per-session output: 4 recipes documented, 3 doctrines saved, 36-node algorithm snapshotted, 8 idioms cracked, 1 working library .c4d, 1 assembly test passed

What could be better next time:
- Should screenshot viewport at multiple frames FIRST when investigating "is this scene animated?" — internal cache inspection is not ground truth
- Should check inner-capsule mutation behavior earlier (the WIRE_MODE.REMOVE failure inside Memory was discovered late)
- Should not have spent cycles probing GraphModelHelper when C4D was clearly heap-locked from heavy iteration; pause + reset first

---

**Checkpoint saved 2026-05-04. Work paused at a clean state. Ready to revive whenever.**
