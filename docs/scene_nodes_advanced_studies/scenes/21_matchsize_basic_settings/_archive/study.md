# Scene 21 — Match Size (Basic Settings) — the reference build BLUEPRINT

**Studied:** 2026-05-01
**Source:** `the basic-settings reference scene`
**Method:** Tear-apart deletion experiment ("decimate to understand")
**Asset DB required:** `the Match Size asset library` (must be mounted in C4D Prefs → Library; missing DB → graph appears as 2-node empty stub)

---

## Why this study exists

The Match Size SN Deformer (the reference build, Asset Version 1004) is a **203-node hand-built scene-nodes deformer** for **NORMALIZING VARIED ELEMENTS to a common target bbox envelope** — so artists can take a kitbash collection of differently-sized geometries and have each one resize to fit the same envelope, enabling clean stacking/alignment/cloning in rows. The Basic-Settings demo here uses just one source (Torus) + one target (Cylinder) but the intent shines through in sister scenes: **Stone-Circle** uses Match Size to unify 5 differently-sized Megascans rocks; **Windows** uses Match Size on Window+Handle pairs to fit them into different Boole hole sizes.

This study reverse-engineers the build by progressively deleting nodes and observing what breaks — turning the graph from a black box into a faithful blueprint.

This blueprint is for the cinema4d-mcp community: the goal is to lower the barrier to authoring complex SN-graph deformers/distributions/capsules so anyone can build & ship tools without needing to manually unpick nodal-integer wiring.

---

## Scene anchor

| Object | Type | Dimensions |
|--------|------|------------|
| **Cylinder** (target) | primitive 5170 | bbox radius 138.7×134.7×138.7 |
| **Torus** (source) | primitive 5163 | bbox radius **150×50×150** (native) |
| └ **Match Size** | SN Deformer 180420400 | 203-node graph, Asset Version 1004 |

Working state: torus is dramatically expanded into a fat sphere-like donut that fully engulfs the cylinder volume.

---

## True purpose (Spenser correction 2026-05-01)

Match Size is a **NORMALIZATION wand** — not a "scale A to be size B" function. The semantic distinction matters for understanding the algorithm:

- **Wrong mental model:** "make my sphere bigger by some scale factor" (this would be a regular Scale node)
- **Right mental model:** "given any input element of any native size, RESIZE it so its bbox exactly fits the target envelope" — the per-axis scale is COMPUTED from `target_envelope_size / source_bbox_size`, which means a 50-cm sphere becomes 277-cm wide × 270-cm tall × 277-cm deep when its target = a 277×270×277 cylinder. A 200-cm sphere with the same target becomes 277×270×277 too. **Both inputs end up the SAME size.**

This is why the algorithm needs BOTH `bb` (source bbox readers) AND `legacyobjectaccess` (target object reader) — without per-axis ratio compute, you'd just be uniformly scaling.

**Workflow use case:** drop Match Size on each child of a Cloner, link all of them to the same target → every clone child is now normalized → uniform output even from heterogeneous input.

## the reference algorithm (inferred from build + tear-apart)

```
INPUT: parent geometry (Torus mesh) + 15 AM controls (per-axis enables, mode, target obj link, …)

STEP 1  Read TARGET via legacyobjectaccess × 2 → get cylinder's transform + bbox
STEP 2  Read SOURCE via bb × 3 → get input geometry bbox(es)
STEP 3  Compute per-axis ratios via arithmetic × 20 (target.size / source.size, plus offsets)
STEP 4  Branch on mode flags via if × 16 + switch × 13 + compare × 1 (per-axis enables, Local/Global, anchor)
STEP 5  Apply 7 selection-scoped transform_element layers (each = transformpoint + selectionstringparser)
STEP 6  Compose output via connect_geometries × 2 + delete + invertselection
STEP 7  Final OUTPUT GATE: transform_element@dty… → >geometryout

DEAD-CODE in Global default mode: inversematrix × 2 (only used by Local-mode branch)
DEBUG: cube@KS3Scio0 = wireframe target-bbox visualizer
ORGANIZATION: scaffold × 8 (labeled section dividers) + reroute × 16 (clean wiring)
```

---

## Tear-apart experiment results

Each stage deletes a class of nodes from the SN graph and observes the visual outcome. Reload between stages for clean state.

| Stage | Action | Visual outcome | Verdict | Screenshot |
|------:|--------|----------------|---------|------------|
| **0** | Baseline (deformer ON) | Fat torus engulfs cylinder bbox | Working | `frames/stage0_baseline_ON.png` |
| **1** | Disable deformer entirely (`[906]=0`) | Tiny native torus (150×50×150) — small flat donut at cylinder midline | OFF reference | `frames/stage1_DISABLED.png` |
| **2** | Delete `cube@KS3Scio0` (1 internal cube primitive) | **NO change** vs baseline | **DEBUG VISUALIZER** — safe to delete; no functional contribution | `frames/stage2_NO_cube.png` |
| **3** | Delete `transform_element@dty…` (the final node feeding `>geometryout`) | Reverts to tiny native torus — identical to DISABLED | **OUTPUT GATE** — single chokepoint; deletion = killing the entire deformer | `frames/stage3_NO_finalTransform.png` |
| **4** | Delete `inversematrix` × 2 (`M32v6qW4` + `HvjBjOPu`) | **NO change** vs baseline | **DEAD-CODE in default mode** — only used by Local-mode branches that aren't active here | `frames/stage4_NO_inversematrix.png` |
| **5** | Delete `legacyobjectaccess` × 2 (`eOgP5PLP` + `I6pWQQNQ`) | **TORUS COLLAPSED TO FLAT YELLOW DISC** (pancake) | **TARGET OBJECT DATA SOURCE** — without it the cylinder's bbox is unknown, ratios go degenerate | `frames/stage5_NO_legacyobjectaccess.png` |
| **6** | Delete `bb` × 3 (`MgQzgESR` + `cQB1T8zz` + `XGFU3WtI`) | **TORUS COLLAPSED TO FLAT DISC** (same pancake as stage 5) | **SOURCE BBOX READER** — without it the source dimensions are unknown, ratios degenerate identically | `frames/stage6_NO_bb.png` |

---

## Key blueprint findings

### 1. The output is a single chokepoint
The graph terminates at exactly one node (`transform_element@dty…`) feeding `>geometryout`. Backtrace from this anchor reveals the full output chain. **For any custom deformer build: design a single terminal output node — makes the graph traceable and debuggable.**

### 2. Both bbox-reading subsystems are critical
The algorithm needs BOTH the SOURCE geometry's bbox (`bb` nodes) AND the TARGET object's data (`legacyobjectaccess` nodes). Removing either degenerates the per-axis ratios. The two subsystems run in parallel and feed the ratio-compute layer.

### 3. Mode-specific branches carry dead code
The 2 `inversematrix` nodes are present for **Local-mode** Match Size operation but unused in **Global mode** (the default). the reference build wired them anyway so toggling the mode just re-routes through pre-existing infrastructure. **For our builds: this is an excellent pattern — wire all mode paths even when default-inactive, so artists can switch modes without graph rewrites.**

### 4. Debug visualizers are safe to ignore
The internal `cube@KS3Scio0` is purely for graph-author debugging. Recognize this pattern: any "primitive geometry generator" inside a deformer/distribution graph is likely a viz aid, not algorithm-load-bearing.

### 5. The output chain is at least 3 nodes deep
Verified backtrace: `EZxTzq → XEOKhg → if(QIDSSw, 3 inputs) → dty (final)`. The `if` branch at the end suggests the output picks between 3 deformation-mode results (likely Position-only, Scale-only, and Both modes).

---

## Open questions (to upgrade the blueprint further)

To go from this 80%-blueprint to 100%-blueprint, additional tear-apart experiments needed:
- Delete each `floatingio` (×15) one at a time → confirm which AM control disappears (proves the floatingio→AM-param mapping)
- Delete each `transform_element` (×7) individually → identify which one handles position-match vs scale-match vs anchor-offset vs per-axis variants
- Delete each `if``switch` to map the mode-dispatch logic
- Test with [2107] Distribution Type toggled to Local mode → see if `inversematrix` deletion now breaks things

---

## Reusable tear-apart methodology (for other scenes/builds)

```python
# 1. Capture baseline ON screenshot
# 2. Toggle deformer/generator OFF via [906]=0 → screenshot OFF reference
# 3. Re-enable, then for each candidate node:
#    a) Open SN graph: nimbus = host.GetNimbusRef(nspace)
#                      graph = nimbus.GetGraph(nspace)
#                      root = graph.GetRoot()
#    b) Find target node by id-prefix match
#    c) graph.BeginTransaction() → node.Remove() → txn.Commit()
#    d) c4d.EventAdd() + d.ExecutePasses() to refresh
#    e) viewport_screenshot to capture result
#    f) Compare visually to baseline ON / baseline OFF
# 4. Reload .c4d file between independent tests (graph deletions are NOT in c4d undo stack)
```

---

## Visual signatures cheat-sheet (for other blueprint authors)

When tearing apart any SN deformer/distribution graph, these visual outcomes diagnose what you removed:

| Outcome | Likely cause |
|---------|--------------|
| **No visible change** | Removed a debug viz, dead-code branch, scaffold/reroute, or unused-mode infrastructure |
| **Reverts to native (un-deformed)** | Removed the OUTPUT GATE or a critical node in the only active path |
| **Collapses to degenerate (pancake / point / spike)** | Removed a critical INPUT data source (bbox reader, target accessor, ratio compute) — math went to zero/identity/NaN |
| **Visual error / NaN spikes** | Broke a connection mid-stream — partial data flow |
| **Missing material / black surface** | Removed a vertex-color or shader bridge |

---

## Operational notes

- Match Size graph deletions are NOT in C4D's standard undo stack — restore via `LoadFile()` between experiments
- The asset DB `the Match Size asset library` MUST be mounted in C4D Prefs → Library before loading these scenes (see `feedback_capsule_is_a_loose_term` + `reference_c4d_2026_missing_asset_appears_as_empty_graph` memories)
- This is a **single-pass static deformer** (0 LCV, no time_state) — graph runs end-to-end every viewport refresh
- Doc CLOSED via KillDocument at end of session per RAM-hygiene
