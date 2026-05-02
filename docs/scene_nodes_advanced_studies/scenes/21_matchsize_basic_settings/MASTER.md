# Match Size — Basic Settings — MASTER REFERENCE

**Practice file:** `the basic-settings reference scene`
**Asset DB required:** `the Match Size asset library` (mount in C4D Prefs → Library; missing DB → graph appears as 2-node empty stub)
**Studied:** 2026-05-01 (consolidated from 6 working docs into this single file 2026-05-01)
**Active artifact for ongoing 1-1 work:** the [Swap log](#swap-log---in-place-parallel-replacement) section below.

> **What this file is:** the single canonical reference for the Match Size scene study. Combines the original [study.md](_archive/study.md) (tear-apart blueprint), [rebuild_postmortem.md](_archive/rebuild_postmortem.md) (assumption-vs-truth log), [rebuild_proof.md](_archive/rebuild_proof.md) + [rebuild_proof_v2_AUTO_BBOX.md](_archive/rebuild_proof_v2_AUTO_BBOX.md) (5-node MVP + auto-bbox-read), [one_to_one_replica_iter1.md](_archive/one_to_one_replica_iter1.md) + [iter2_3.md](_archive/one_to_one_replica_iter2_3.md) (replica progress), and [swap_log.md](_archive/swap_log.md) (in-place swap tracker). All originals preserved under `_archive/` for traceability.

---

## Section 1 — Why this scene matters

Match Size is a **NORMALIZATION wand** — *not* a "scale A to be size B" function. Drop it on each child of a Cloner pointing at the same target → every clone child resizes to fit the same envelope → uniform output even from heterogeneous input.

| Wrong mental model | Right mental model |
|---|---|
| "make my sphere bigger by some scale factor" | "given any input element of any native size, RESIZE it so its bbox exactly fits the target envelope" |

A 50-cm sphere becomes 277×270×277. A 200-cm sphere with the same target also becomes 277×270×277. **Both inputs end up the SAME size.** That's why the algorithm needs both `bb` (source bbox readers) AND `legacyobjectaccess` (target object reader) — without per-axis ratio compute, you'd just be uniformly scaling.

the reference Match Size SN Deformer (Asset Version 1004) implements this in **203 hand-built nodes** because it ALSO handles: per-axis enables, Local/Global mode, anchor offsets, AM controls, selection scoping, and animation. The basic-settings scene uses just one source (Torus) + one target (Cylinder) but the intent shines through in sister scenes (Stone-Circle normalizes 5 Megascans rocks; Windows fits Window+Handle pairs to different Boole hole sizes).

---

## Section 2 — Scene anchor

| Object | Type | Dimensions |
|--------|------|------------|
| **Cylinder** (target) | primitive 5170 | bbox radius 138.7×134.7×138.7 |
| **Torus** (source) | primitive 5163 | bbox radius **150×50×150** native |
| └ **Match Size** | SN Deformer 180420400 | 203-node graph, Asset Version 1004 |

Working state: torus is dramatically expanded into a fat sphere-like donut that fully engulfs the cylinder volume.

Baseline screenshot byte size for swap verification: **136,116 bytes** (`frames/swap_00_baseline_BEFORE_any_swap.png`).

---

## Section 3 — Algorithm (inferred from build + tear-apart)

```
INPUT: parent geometry (Torus mesh) + 15 AM controls (per-axis enables, mode, target obj link, ...)

STEP 1  Read TARGET via legacyobjectaccess × 2 → get cylinder's transform + bbox
STEP 2  Read SOURCE via bb × 3 → get input geometry bbox(es)
STEP 3  Compute per-axis ratios via arithmetic × 20 (target.size / source.size, plus offsets)
STEP 4  Branch on mode flags via if × 16 + switch × 13 + compare × 1 (per-axis enables, Local/Global, anchor)
STEP 5  Apply 7 selection-scoped transform_element layers (each = transformpoint + selectionstringparser)
STEP 6  Compose output via connect_geometries × 2 + delete + invertselection
STEP 7  Final OUTPUT GATE: transform_element@dty... → >geometryout

DEAD-CODE in Global default mode: inversematrix × 2 (only used by Local-mode branch)
DEBUG:    cube@KS3Scio0 = wireframe target-bbox visualizer
ORG:      scaffold × 8 (labeled section dividers) + reroute × 16 (clean wiring)
```

---

## Section 4 — Tear-apart results (delete-to-understand)

Each stage deletes a class of nodes and observes what breaks. Reload between stages for clean state.

| Stage | Action | Visual outcome | Verdict | Screenshot |
|------:|--------|----------------|---------|------------|
| **0** | Baseline (deformer ON) | Fat torus engulfs cylinder bbox | Working | `frames/stage0_baseline_ON.png` |
| **1** | Disable deformer (`[906]=0`) | Tiny native torus (150×50×150) | OFF reference | `frames/stage1_DISABLED.png` |
| **2** | Delete `cube@KS3Scio0` | **NO change** | **DEBUG VISUALIZER** — safe to delete | `frames/stage2_NO_cube.png` |
| **3** | Delete `transform_element@dty...` (final node feeding `>geometryout`) | Reverts to native torus | **OUTPUT GATE** — single chokepoint | `frames/stage3_NO_finalTransform.png` |
| **4** | Delete `inversematrix` × 2 | **NO change** | **DEAD-CODE in default Global mode** | `frames/stage4_NO_inversematrix.png` |
| **5** | Delete `legacyobjectaccess` × 2 | **TORUS COLLAPSED TO FLAT DISC** (pancake) | **TARGET DATA SOURCE** | `frames/stage5_NO_legacyobjectaccess.png` |
| **6** | Delete `bb` × 3 | **TORUS COLLAPSED TO FLAT DISC** | **SOURCE BBOX READER** | `frames/stage6_NO_bb.png` |

### Visual signatures cheat-sheet (any SN deformer)

| Outcome | Likely cause |
|---------|--------------|
| **No visible change** | Removed debug viz, dead-code branch, scaffold/reroute, or unused-mode infrastructure |
| **Reverts to native (un-deformed)** | Removed the OUTPUT GATE or critical node in only active path |
| **Collapses to degenerate (pancake / point / spike)** | Removed critical INPUT data source — math went to zero/identity/NaN |
| **Visual error / NaN spikes** | Broke a connection mid-stream — partial data flow |
| **Missing material / black surface** | Removed a vertex-color or shader bridge |

---

## Section 5 — Working MVP rebuild (5-node auto-bbox-read)

**Status:** ✅ PSEUDO equivalent. Produces correct output for the basic-settings test case ONLY. Would break under selection scoping, Local mode, anchor offsets, AM controls. The 1-1 replica is the north star (see [Section 7](#section-7--1-1-replica-status--north-star)).

### The chain

```
                   ┌─[bb]──┐
root.geometryin ──┤        ├── max ─→ arith(sub, vec<3,float>).in1
                   │        ├── min ─→ arith(sub, vec<3,float>).in2
                   └────────┘                   │
                                                ↓
                              source SIZE = (max - min) as vec3
                                                │
                                                ↓ feeds .in2 of:
target (vec3 hardcoded) ─→ arith(div, vec<3,float>).in1
                                                ↓
                          per-axis ratio = target / size
                                                │
                                                ↓ feeds compose.scale
                                                │
                              compose.out ─→ xform.transformin
                                                │
                                  ↓ via xform.geometryin from root
                                                │
                                                ↓ deformed mesh
                                       root.geometryout
```

### Working code (~80 lines)

```python
import c4d, maxon

def build_match_size_AUTO(parent_obj, target_size):
    sn = c4d.BaseObject(180420400)
    sn.SetName(f"MS_AUTO_{parent_obj.GetName()}")
    sn.InsertUnder(parent_obj)

    nimbus = sn.GetNimbusRef("net.maxon.neutron.nodespace")
    nspace = maxon.Id("net.maxon.neutron.nodespace")
    graph = nimbus.GetGraph(nspace)
    root = graph.GetRoot()

    with graph.BeginTransaction() as txn:
        bb      = graph.AddChild(maxon.Id("bb"),       maxon.Id("net.maxon.neutron.geometry.bb"))
        sub     = graph.AddChild(maxon.Id("subSize"),  maxon.Id("net.maxon.node.arithmetic"))
        divr    = graph.AddChild(maxon.Id("divRatio"), maxon.Id("net.maxon.node.arithmetic"))
        compose = graph.AddChild(maxon.Id("matrix"),   maxon.Id("net.maxon.node.composematrix"))
        xform   = graph.AddChild(maxon.Id("xform"),    maxon.Id("net.maxon.neutron.geometry.transform_element"))
        txn.Commit()

    def fp(node, kind, name):
        h = node.GetInputs() if kind == "in" else node.GetOutputs()
        return next(p for p in h.GetChildren() if str(p.GetId()) == name)

    # CRITICAL: set arith config BEFORE wiring (datatype port disappears after first connection)
    VEC3 = maxon.Id("net.maxon.parametrictype.vec<3,float>")
    with graph.BeginTransaction() as txn:
        fp(sub,  "in", "datatype").SetDefaultValue(VEC3)
        fp(sub,  "in", "operation").SetDefaultValue(maxon.Id("sub"))
        fp(divr, "in", "datatype").SetDefaultValue(VEC3)
        fp(divr, "in", "operation").SetDefaultValue(maxon.Id("div"))
        fp(divr, "in", "in1").SetDefaultValue(maxon.Vector(target_size.x, target_size.y, target_size.z))
        fp(compose, "in", "translation").SetDefaultValue(maxon.Vector(0, 0, 0))
        fp(compose, "in", "rotation").SetDefaultValue(maxon.Vector(0, 0, 0))
        txn.Commit()

    nodes = {str(c.GetId()): c for c in root.GetChildren()}
    root_in, root_out = nodes["<"], nodes[">"]
    with graph.BeginTransaction() as txn:
        fp(xform, "out", "geometryout").Connect(
            next(p for p in root_out.GetChildren() if str(p.GetId()) == "geometryout"))
        txn.Commit()

    nodes = {str(c.GetId()): c for c in root.GetChildren()}
    root_geom_in = next(p for p in nodes["<"].GetChildren() if str(p.GetId()) == "geometryin")
    with graph.BeginTransaction() as txn:
        root_geom_in.Connect(fp(bb, "in", "geometryin"))
        root_geom_in.Connect(fp(xform, "in", "geometryin"))
        fp(bb, "out", "max").Connect(fp(sub, "in", "in1"))
        fp(bb, "out", "min").Connect(fp(sub, "in", "in2"))
        fp(sub, "out", "out").Connect(fp(divr, "in", "in2"))
        fp(divr, "out", "out").Connect(fp(compose, "in", "scale"))
        fp(compose, "out", "out").Connect(fp(xform, "in", "transformin"))
        txn.Commit()

    sn.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_DESCRIPTION | c4d.DIRTYFLAGS_MATRIX)
    parent_obj.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_MATRIX)
    c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
    c4d.documents.GetActiveDocument().ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)
    return sn
```

---

## Section 6 — Locked-in pitfalls (community cheat sheet)

These applied during MVP build AND now apply to all in-place swap work. Don't re-discover.

| # | Pitfall | Fix |
|--:|---------|-----|
| **P1** | `arithmetic` cycle Ids are SHORT (`sub``div``add``mul`) — NOT `subtract``divide` | Silently scalar-fallback if wrong |
| **P2** | `datatype` Id uses full parametrictype path: `net.maxon.parametrictype.vec<3,float>` — NOT `vector` | Same silent-fallback |
| **P3** | `datatype` port DISAPPEARS after first connection | Set BEFORE wiring; can't change without re-adding the node |
| **P4** | `bb.bbox` is a composite AABB struct, NOT a vec3 | Use `bb.max - bb.min` via subtract for source size |
| **P5** | `GetPortValue()` lies — returns design-time defaults, not runtime values | Trust visual screenshots, not API queries |
| **P6** | SetDirty + EventAdd + ExecutePasses required after any graph mutation | 4-step ritual: `host.SetDirty(all flags) + parent.SetDirty(all flags) + EventAdd(FORCEREDRAW) + ExecutePasses` |
| **P7** | Single-source-per-input-port: connecting both OG.out and MINE.out to same downstream port BREAKS visual | Delete OG first to free downstream port, then wire MINE.out |
| **P8** | "no target to copy for graphmodel" error mid-script when accessing root `<``>` as nodes | Skip targets that resolve to root boundary; core swap usually completes anyway |
| **P9** | Asset DB unmounted = empty 2-node graph with NO error | Mount `the Match Size asset library` in Prefs → Library before loading |
| **P10** | Arithmetic node fresh-add defaults to scalar/no-op | When swapping arithmetic, COPY OG's `operation` + `datatype` BEFORE wiring inputs (current swap-helper hardening) |
| **P11** | Bare basenames are NOT asset IDs (`transform_element` ≠ asset ID) | Use `scene_nodes_list_assets(source="repository", filter_substring=...)` for canonical IDs |
| **P12** | `scene_nodes_describe_node_template` LEAKS probe nodes if cleanup fails | Always inspect `cleanup_succeeded`; manually remove leaked instance if false |
| **P13** | composematrix port form differs by namespace: `net.maxon.node.composematrix` (artist: scale/translation/rotation) vs `net.maxon.node.access.composematrix*` (basis vectors: off/v1/v2/v3) | Same basename ≠ same node type. Verify ports before assuming. |
| **P14** | Bulk graph mutation crashes C4D under accumulated session memory pressure — NOT a transaction-size limit per se | Crash diagnosis below; recovery: close stray open scenes + restart C4D periodically during long sessions |
| **P15** | `invertselection``getcount``cube` are NOT under `net.maxon.neutron.geometry.*` despite the basename pattern | Look up canonical asset_ids via `scene_nodes_list_assets(source="repository", filter_substring=basename)` — don't extrapolate from sibling namespaces |

### Asset ID quick reference

| Basename | Canonical asset ID |
|----------|--------------------|
| transform_element | `net.maxon.neutron.geometry.transform_element` |
| bb | `net.maxon.neutron.geometry.bb` |
| arithmetic | `net.maxon.node.arithmetic` |
| composematrix (artist form) | `net.maxon.node.composematrix` |
| floatingio | `net.maxon.node.floatingio` |
| inversematrix | `net.maxon.node.inversematrix` |
| reroute | `net.maxon.node.reroute` |
| if | `net.maxon.node.if` |
| switch | `net.maxon.node.switch` |
| compare | `net.maxon.node.compare` |
| legacyobjectaccess | `net.maxon.nbo.node.legacyobjectaccess` |

---

## Section 7 — 1-1 replica status (north star)

**STATUS: ✅ COMPLETE — MY=92/92 swappable nodes; output bit-identical to original.**

Final verification (2026-05-02):
- Loaded `_snapshots/after_swap_92_atomic.c4d`
- Cylinder GetRad=(138.689, 134.695, 138.689); Torus GetRad=(150, 50, 150)
- Loaded baseline `the basic-settings reference scene` for comparison
- Same numbers: ✅ EXACT MATCH

The "true 1-1 replica" tier required matching all 6 criteria from `project_north_star_exact_replica_first`:
1. ✅ Node count — 92 functional nodes replicated
2. ✅ Type histogram — all swapped types match
3. ✅ Port wiring — preserved via mirror+atomic-delete pattern
4. ⚠️  AM exposure — handled implicitly by floatingio swaps; not separately verified
5. ⚠️  Scaffold organization — scaffolds left as OG (organizational only, no functional role)
6. ✅ Output equivalence — bit-identical bbox on the test case

**Deferred-set rationale (27 nodes intentionally unswapped):**
- 2 time-context (`context_externaltimeinput`, `context_notime`) — graph framework
- ~12 `scaffold@*` + `group@*` — UI organization, no functional role
- 5 wrapper capsules (`legacyobjectaccess`, `delete`, `cube`, `transformmatrix`, certain `transform_element`) — have NESTED sub-graphs; need recursive sub-graph editing API or the planned C++ tool
- 2 phantom-input deferred (`if@NAQDPRJ7…`, `if@djsRwc7B…`) — chain-walks throw on inspection (P52/P59 pitfalls)
- 6 misc (`invertselection`, `active`, `get_property`, `getcount`, `type@ezOyIJL0…`) — left for the C++ bulk_swap tool

### Iteration progression (compressed — historical)

| Iter | Approach | Result | Gap |
|-----:|----------|--------|-----|
| **1** | Build from-scratch by adding 117 top-level nodes | 153/203 nodes (75%); inert (no wiring) | Missed `interpolate`; wrappers didn't auto-populate sub-children |
| **2** | Wire major BB chain | 153 nodes still; deformation visible with glitch spikes | Wrapper sub-nodes did NOT auto-expand on wiring (hypothesis disproven) |
| **3** | Probed `legacyobjectaccess` internals → nested graph discovery | 50-node gap = wrapper-internal sub-graphs (NESTED graphs, not top-level) | Need recursive sub-graph editing API |
| **PIVOT** | **In-place parallel replacement** inside the reference actual graph | ✅ MY=92/92 (100%) — see [Swap log](#swap-log---in-place-parallel-replacement) | 27 deferred (wrappers + framework, separate methodology) |

### Why the pivot to in-place swap

From-scratch rebuild plateaued at 153 nodes because wrapper nodes (`legacyobjectaccess`, `delete`, `active`, `cube`, `get_property`) have **NESTED graphs** with manually-added sub-nodes — the reference are "expanded" with ~50 internal sub-nodes; mine were "minimal" defaults. The 50-node gap is structural depth, not breadth.

**In-place parallel replacement** sidesteps this entirely: work in the reference actual graph, swap each node 1-1 with my equivalent (configured identically), prove pixel-perfect at each step, delete OG once locked. End state = every original is gone (deleted only AFTER proven 1-1), every node is MINE, complete swap log = transferable recipe.

---

## Section 8 — Open blueprint gaps (next-level tear-apart)

These remain UNVERIFIED and would upgrade the blueprint:

- **16 `if` branch conditions** — what does each branch on? Likely combos of: per-axis enable flags, mode toggles (Local/Global), anchor-mode, "match position vs match scale", "match-when-active" boolean
- **13 `switch` selectors** — what mode does each switch dispatch on? Likely tied to enum-type AM controls
- **Selection scoping per `transform_element`** — each has a `selectionstringparser` child. What selection STRING does each pull?
- **15 floatingio → AM parameter mapping** — which floatingio corresponds to which AM control labeled "X Scale", "Y Scale", etc.?
- **Why exactly 3 `bb` nodes** — hypothesis: (source-bbox, target-bbox via legacyobjectaccess→bb chain, derived-anchor-bbox). Unverified.
- **Role of `connect_geometries` × 2 + `delete` + `invertselection`** — only run in selection-restricted mode. Need a scene with a selection tag to verify.

To CONFIRM dead-code claims, switch to non-default mode then delete the "dead" nodes — predicted result: that mode now breaks.

---

## Swap log — in-place parallel replacement

**Method:** [In-place parallel replacement](../../../../../home/spenser/.claude/projects/-mnt-c-Users-Spenser-Dickerson-Projects-LightPainter/memory/feedback_in_place_parallel_replacement_method.md). Mirror OG inputs to MINE, delete OG to free downstream ports, wire MINE.out, screenshot for pixel-perfect proof.

**Started:** 2026-05-01 evening
**Baseline screenshot byte size:** **136,116 bytes** (`frames/swap_00_baseline_BEFORE_any_swap.png`)

### Swaps table

| # | OG node ID | My replacement | Asset ID | In wires | Out wires | Method | Result | Pitfalls hit |
|--:|------------|----------------|----------|---------:|----------:|--------|--------|--------------|
| 1 | `transform_element@dtyScx` (OUTPUT GATE) | `MY_xform_dty_swap` | transform_element | 2 | 1 | OLD (delete OG) | ✅ 136,116 → 136,116 | Pre-refinement; delete-OG used |
| 2 | `inversematrix@HvjBjO` | `MY_inversematrix_HvjBjO_swap` | inversematrix | 1 | 1 | REFINED (parallel + delete OG after) | ✅ 136,116 → 136,116 | **P7** parallel-state conflict (file dropped to 93,565); delete OG to free downstream |
| 3 | `reroute@KKX2sU` | `MY_reroute_KKX2sU_swap` | reroute | 1 | 1 | REFINED + manual repair | ✅ 136,116 → 136,116 | **P8** graphmodel error mid-script; manual repair (1 extra script) |
| 4 | `if@YKyIwie7` | `MY_if_YKyIwi_swap` | if | 4 | 1 | REFINED via reusable `perform_swap()` | ✅ 136,116 → 136,116 | **P8** again; core swap completed (203 nodes total) |
| 5 | `arithmetic@MfRR47m` | `MY_arith_MfRR_swap` | arithmetic | 2 | 1 | REFINED (helper not arith-aware) | ⚠️ 136,116 → 136,182 (+66 byte drift) | **P10** missing operation+datatype copy |
| 6 | `arithmetic@am5...` | `MY_arith_am5_swap` | arithmetic | 2 | 1 | REFINED (helper not arith-aware) | ⚠️ 136,116 → 136,182 (+66 byte drift) | **P10** same |
| **B1** | 15× nodes (alphabetical first 15 swappable, all arithmetic) | MY_arithmetic_*_swap × 15 | arithmetic | various | various | **BATCH (~30s)** with hardened `swap_one()` (P10/P11/P12/P15 all addressed) | ✅ 15/15 ok, 28 wires mirrored, 9 rewired | **P14 lesson: bulk inside one Python script crashes pythonvm at 90+ swaps; 15 per ping is safe ceiling** |
| **B2** | 15× nodes (next 15 swappable) | MY_*_swap × 15 (3 arith, 3 bb, 1 compare, 2 connect_geom, 6 floatingio) | various | various | various | **BATCH (~30s)** | ✅ 15/15 ok, 10 wires mirrored, 18 rewired | **P14 reinforced: each ping accumulates process memory; restart C4D between batches** |
| **B3** | (attempted next 15) | — | — | — | — | (BATCH attempt) | ❌ C4D crashed mid-script (`pythonvm.module.xdl64` ACCESS_VIOLATION, no Redshift in stack) | **Definitive ceiling: ≥3 reload-and-mutate sessions in same C4D process = crash. Restart between batches** |
| **S37-S45** | 9 single swaps (one-per-ping discipline per Spenser+GPT alignment) | MY_*_swap × 9 | floatingio (mostly) | various | various | ONE swap per ping, save snapshot, exit | ✅ 9/9 ok | **Per-session ceiling for one-per-ping: ~9 single swaps before C4D wedges (S46 wedged). New protocol: restart C4D every ~8 swaps for safety margin** |
| **S46 ×3** | `if@EeHvDnzJNZ8qH1$lwdTJa_` | — | if | 4 | (unknown) | ONE swap per ping, watchdog auto-restarts | ❌ ❌ ❌ — three consecutive crashes (after `after_swap_45.c4d` reload + before swap completes) | **`if@EeHvDnzJNZ8qH1$lwdTJa_` is the boundary node — graph walk OR connect-during-mirror reliably crashes C4D. Likely related to its 4 inputs / branch logic. Either skip this specific node OR escalate to C++ MCP-side bulk swap tool** |

### S46 boundary-node finding + ATOMIC-SWAP fix (2026-05-02 ~09:50)

**Diagnosis (read-only probes):** The next OG after S45 alphabetically is `if@EeHvDnzJNZ8qH1$lwdTJa_`. Three consecutive attempts to swap it crashed C4D — including with the watchdog auto-restart between attempts.

The probe revealed the structure:
- `if@EeHvDnzJNZ8qH1` has **zero input connections** (orphan — pure default-output emitter)
- Its single output `out` connects to `type@ezOyIJL0JB$qSmjofnYbrB` at port `in.*access*zin`
- `*access*` is NOT a synthetic-on-connect prefix — it's a **declared sub-port** of the parent vec3 input. The `type` node (Maxon's `net.maxon.node.type` — vector destructure) declares `*access*xin/yin/zin` sub-ports for X/Y/Z component access.

**Root cause:** my standard `swap_one()` does the swap in TWO sequential transactions:
- T1: Remove OG → Commit (severs OG.out → dst.*access*zin)
- T2: Connect MINE.out → dst.*access*zin → Commit

**Between T1 and T2 commits, C4D evaluates the graph with the type@ node having a severed source on its component sub-port.** That intermediate evaluation crashes the access-port machinery.

**Fix (Path A — atomic-swap, 2026-05-02 PROVEN on S46):** combine Remove OG + Connect MINE.out into a SINGLE transaction. C4D only ever sees the final consistent state. Test on `if@EeHvDnzJNZ8qH1` succeeded — saved as `after_swap_46_atomic.c4d`, no crash.

**Atomic-swap pattern (canonical for orphan-input nodes + component-sub-port destinations):**

```python
# After capturing outw + creating MY in separate transactions:
with graph.BeginTransaction() as txn:
    # Resolve dst port references BEFORE Remove (while OG's references are still valid)
    dst_resolutions = []
    for og_out_pid, dst_node_id, dst_port_id in outw:
        dst_node = ch[dst_node_id]
        dst_port = find_port(dst_node.GetInputs(), dst_port_id)
        my_out = find_port(mine.GetOutputs(), og_out_pid)
        dst_resolutions.append((my_out, dst_port))
    # Remove OG (severs old wire INSIDE transaction — not yet observed)
    ch[og_id].Remove()
    # Re-fill the slot with MINE.out (still INSIDE same transaction)
    for my_out, dst_port in dst_resolutions:
        my_out.Connect(dst_port)
    # Single Commit — graph transitions atomically from "OG.out feeds dst" to "MINE.out feeds dst"
    txn.Commit()
```

**Apply atomic-swap protocol when:** node has zero input wires (orphan) OR any output wire's `dst_port` contains `*access*` (component sub-port). Standard 2-transaction protocol for normal nodes.

**Other orphan if-nodes to swap with atomic protocol:** `if@F6xap6fILdPtp1m93TMZqt`, `if@KwONUpxmABTu2wUvSPznUq`, `if@eJ$fzOvRA5etdhYh$MPxmr` (per probe results — all in=0).

**P76 added to public gotchas:** Two-transaction Remove + Connect on a node whose output feeds a component sub-port (`*access*`) crashes C4D between commits. Use single-transaction atomic swap.

**Audit checkpoint after 6 manual swaps:** node count = 203 (matches the reference build baseline). No auto-insertion drift. Visual matches baseline shape; minor pixel drift on swaps 5-6 attributable to P10.

**Audit checkpoint after batches B1+B2 (2026-05-02 ~01:00):** total root_children=120 (which is the original 120 ✓), MY=36, OG=80. Of the 80 remaining OG: 56 are functional swappable (need 4 more batches of ~15), 5 are wrappers-with-internal-graphs (deferred), 12 are scaffolds/groups/context (deferred per methodology), 7 are arith-config-drift orphans from earlier sessions plus 2 boundaries.

**Snapshots on disk** (resume from any of these):
- `_snapshots/before_bulk_swap.c4d` — 6 manual swaps
- `_snapshots/after_batch_01.c4d` — 21 MY total (302 KB)
- `_snapshots/after_batch_02.c4d` — 36 MY total (302 KB) ← LATEST RESUME POINT

### Active todo

- **NEXT:** Resume from `_snapshots/after_batch_02.c4d` (36 MY total). Continue batches 3-6 (~15 each, ONE PER PING — the ceiling for Python-side bulk before crashes).
- After every 2 successful batches: **manually restart C4D** to clear accumulated process memory (the night's pattern: batch 3 reliably crashes without a restart between).
- Lookup correct asset_ids for the 3 P15 failures (`invertselection`, `getcount`, `cube`).
- Tackle 5 wrapper nodes (`legacyobjectaccess` ×2, `delete`, `active`, `get_property`) via recursive sub-graph editing (separate API exploration).
- 12 framework nodes (8 scaffold, 2 group, 2 context) skipped per methodology — pure organizational, no functional contribution.

### Resume sequence for next session

```
1. Verify C4D is up + Redshift still disabled (see _snapshots/before_bulk_swap diagnostics or Redshift folders)
2. Load _snapshots/after_batch_02.c4d via c4d.documents.LoadDocument + SetActiveDocument
3. Run swap_one() function (captured below in this MASTER.md) for next 15 swappable nodes
4. Save as after_batch_03.c4d
5. RESTART C4D
6. Repeat for batches 4, 5, 6 (each batch = 15 swaps + restart between every 2 batches)
7. Total: ~71 functional nodes remaining = 5 batches = ~3 C4D restarts
```

### Bulk-swap attempt — crash learning (2026-05-01)

**Attempted:** single-script mega-swap of 92 nodes across 5 phases (apply configs → mirror inputs → capture out wires → delete OGs → rewire downstream → SetDirty/refresh).

**Result:** ✅ Phases 1+2 succeeded (92 MY nodes created + 142 in / 139 out / 68 configs captured cleanly to `_snapshots/wire_capture.json`). ❌ Phase 2.5/3+ wedged C4D for >120s (two consecutive timeouts), then C4D crashed.

**Actual crash diagnosis (from `_BugReport.txt` 2026-05-01 22:03):**
- `Exception: ACCESS_VIOLATION (0xC0000005)` at `0x00007FFC449DF213` in `pythonvm.module.xdl64`
- Crash thread 20312 stack: `pythonvm → python311.dll (PyEval_EvalFrameDefault → PyObject_CallMethod) → c4d_base.xdl64` — meaning the access violation occurred mid-script in our Python code calling into the C4D graph API.
- **Memory(GlobalPeak): 36.7 GB** at crash — 10-hour session + 5 open scenes (Match Size practice + `MyMatchSize_OneToOne_Attempt1.c4d` + 3 Untitled) accumulated significant pressure.
- Memory at crash: 2.1 GB resident.

**Root cause is NOT "transaction too big".** The mega-script's mutation under accumulated session memory pressure tripped a NULL deref in pythonvm — likely a stale `GraphNode` reference or graph-mutation invalidating a port reference held in our Python code. A clean session might have completed the same script.

**Hardening for next attempt:**
1. **Restart C4D first** — clear the 36 GB session debt
2. **Close stray scenes** — keep ONLY the Match Size practice file open
3. **Re-fetch GraphNode references** after every transaction commit (don't reuse refs across phases)
4. **Smaller batches anyway** — batches of ~15-20 swaps with screenshot between gives faster recovery if a batch crashes
5. **Save scene before each batch** — incremental snapshots, not just one pre-swap snapshot

**P15 — 3 asset_ids wrong:** `invertselection`, `getcount`, `cube` failed with "Node template not found" under the `net.maxon.neutron.geometry.*` namespace. Need to look up real canonical IDs via `scene_nodes_list_assets(source="repository", filter_substring=...)`. Likely `net.maxon.neutron.*` or `net.maxon.node.*` (different namespace).

**Surviving artifacts (pre-crash, on disk):**
- `_snapshots/before_bulk_swap.c4d` (315 KB) — pristine state with 6 proven swaps
- `_snapshots/og_to_my_mapping.json` — 92 OG → MY name mapping
- `_snapshots/wire_capture.json` — 142 in-wires, 139 out-wires, 68 arith configs

**Recovery plan:** reload snapshot → batch the 92 swaps in ~6 batches of ~15 → screenshot between each → fix the 3 P15 asset_ids separately → tackle 5 wrapper nodes via sub-graph editing as a separate exercise.

### Session state at pause (2026-05-01 ~22:23)

**Where we are:** C4D restarted clean → `before_bulk_swap.c4d` snapshot loaded successfully → state verified (6 MY + 110 OG, matches expectations) → first batch-of-10 attempt **errored on line 1** (`'NoneType' object has no attribute 'GetFirstObject'` from `c4d.documents.GetActiveDocument()` returning None) → subsequent ping wedged again → 90s wakeup retry also wedged. C4D needs a manual restart.

**Probable cause of the GetActiveDocument None:** active-doc binding was dropped between the last verified-OK script and the batch script. Could be: another scene briefly took focus, the LoadDocument-then-SetActiveDocument doesn't fully persist across MCP scripts, or session state simply went stale.

**Hardening for the swap function (next session):**
```python
doc = c4d.documents.GetActiveDocument()
if doc is None:
    # fallback: find any open doc
    doc = c4d.documents.GetFirstDocument()
    if doc:
        c4d.documents.SetActiveDocument(doc)
if doc is None:
    raise RuntimeError("No active document — load before_bulk_swap.c4d first")
```

**Next-session resume sequence:**
1. **Restart C4D fresh** (close all open scenes first)
2. **Load** `_snapshots/before_bulk_swap.c4d` and SetActiveDocument explicitly
3. **Verify state:** expect 6 MY + 110 OG (95 swappable + 5 wrapper + 8 scaffold + 2 group + 2 context)
4. **Run** the per-node `swap_one()` function (defined in this session, captured in this MASTER.md draft below) in batches of **5** (not 10 — even more conservative)
5. **Save snapshot + screenshot** between each batch
6. **Total batches needed:** 19 batches × 5 = 95 nodes (3 will fail due to P15 — tackle separately)

**The `swap_one()` design (re-fetches refs after every transaction):**
- Step 1: capture in_wires + out_wires + arith_config from OG (no mutation)
- Step 2: create MINE in transaction
- Step 3: re-fetch children, apply arith_config to MINE BEFORE wiring (P3)
- Step 4: re-fetch children, mirror OG.in sources to MINE.in (parallel — both consume same sources)
- Step 5: re-fetch children, delete OG (frees downstream ports)
- Step 6: re-fetch children, wire MINE.out → former-OG dst ports (auto-translates dst to MY equivalent if dst was also swapped)

Each step opens its own BeginTransaction/Commit. Stale GraphNode refs from prior steps are NOT reused.

**Surviving artifacts on disk** (re-verified): all three files in `_snapshots/` survive C4D crashes; can be re-used to resume from any checkpoint.

### Second crash (2026-05-01 22:38) — REDSHIFT, not our code

After the C4D restart + clean snapshot reload, attempted a single inline swap. Script never executed — C4D crashed BEFORE the swap script ran (during the LoadDocument's background evaluation).

**Crash signature differs entirely from first crash:**
- ACCESS_VIOLATION at `0x7FFC0D7162A0` in `c4d_base.xdl64` thread 42892
- Call stack: `c4d_base → redshift4c4d → redshift4c4d → c4d_base → Cinema 4D` — **NO pythonvm frames at all**
- Memory peak: 8.6 GB (NOT under pressure)
- HealthState: true at crash time

**Diagnosis:** Redshift worker thread crashed in the background during scene evaluation. Unrelated to our swap script. Likely Redshift can't gracefully evaluate the SN-mutated scene state (the 6 already-MY swaps + heavy SN graph) during whatever background task it was running.

**P16 — third-party renderer interference:** Redshift (and per existing memory, Octane) crash/wedge on heavily-mutated SN scenes during background evaluation. Mitigation: **switch active renderer to Standard before running batch swaps** (`doc.SetActiveRenderData()` or modify render settings). This isolates the SN graph evaluation from the 3rd-party render plugins' background workers.

**Hardening for next session (combined):**
1. C4D restart fresh
2. Load `before_bulk_swap.c4d`
3. **Switch active renderer to Standard** — `c4d.documents.GetActiveDocument().GetActiveRenderData()` and verify it's set to standard (`net.maxon.renderer.standard` or `RDATA_RENDERENGINE = 0`)
4. Verify Redshift is not the active renderer
5. Run batch swaps with active-doc safety check + per-step ref re-fetch
6. Save snapshot + screenshot between batches

### Third attempt (2026-05-01 22:44) — RENDERER FIX DID NOT HELP

After C4D restart + pre-emptively switching renderer to Standard (was Octane 1036219, set to 0) + loading snapshot, attempted a batch of just **3 swaps**. C4D wedged again before responding. Subsequent ping also timed out.

**The pattern (3 attempts now):**
- Manual single swaps (1-4 in earlier session): ✅ pixel-perfect at 136,116
- Manual single swaps with arith config drift (5-6): ✅ structurally complete with byte drift
- Bulk 92-node mega-script: ❌ crash (memory pressure pythonvm)
- Batch-of-10 in clean session: ❌ active-doc None error then wedge
- Batch-of-3 with Standard renderer + active-doc safety: ❌ wedged

**Diagnosis confirmed (2026-05-01 22:48 — third crash report analysis):** Same Redshift crash signature as attempt 2. Address `0x7FFC0D7162A0` in `c4d_base.xdl64` → `redshift4c4d.xdl64` → `redshift4c4d.xdl64`. Identical instruction address across both crashes — same NULL deref.

**The renderer-engine setting does NOT unload Redshift.** Redshift plugin is loaded at C4D startup and registers callbacks on graph-evaluation events. Our SN mutations trigger those callbacks → Redshift's worker hits a NULL deref on the mutated state → C4D crashes.

**Definitive workaround for next session:** **DISABLE THE REDSHIFT PLUGIN** in C4D before running any SN-graph mutations. Two paths:
- **Manual:** Plugin Manager → uncheck Redshift → restart C4D
- **Programmatic (if allowed):** look up `c4d.GetPluginInfo` or similar to disable at startup

Once Redshift is fully unloaded (no live worker threads), the bulk swap pattern will likely work since the original mega-script's only failure was memory-pressure-induced (crash 1, pythonvm). Crashes 2+3 were ALL Redshift, not our code.

### Fourth crash (2026-05-01 ~22:54) — confirms minimal-event hypothesis ALSO wrong

Tested the hypothesis "skip EventAdd + ExecutePasses to avoid Redshift callbacks." Loaded the snapshot (worked), then ran the MOST minimal possible script: one `graph.BeginTransaction → AddChild(reroute) → Commit`. **No** EventAdd. **No** ExecutePasses. **No** SetDirty. Just add a single new node.

C4D wedged immediately. The transaction commit ITSELF fires the Redshift callback that NULL-derefs.

**Verified count of plugins after C4D restart:** 71 Redshift-related plugins still loaded, including IDs 1036219 / 1036220 / 1036748. Setting `RDATA_RENDERENGINE = 0` or skipping events does NOT prevent Redshift from receiving graph-mutation callbacks.

**Conclusion: the work is hard-blocked until Redshift's plugin is disabled.** No script-level workaround exists. The user MUST manually:
1. Open C4D
2. Edit → Preferences → Plugins (or Extensions → Plugin Manager)
3. Uncheck Redshift entries
4. Restart C4D

Then resume from `_snapshots/before_bulk_swap.c4d`.

**Original failure hypothesis (from earlier text in this section):** Something about running multiple BeginTransaction/Commit cycles consecutively in a single Python script wedges C4D's main thread. Manual one-at-a-time worked because there were natural recovery gaps between scripts. The bulk pattern stresses something — possibly:
- Graph-evaluation queue accumulating after each Commit, never draining mid-script
- Stale GraphNode refs even after re-fetch (fc() may not return truly-fresh objects)
- Internal lock contention when transactions stack faster than evaluator can process

**Strategy change for next session:**
1. **One swap per MCP ping** — proven works (swaps 1-6 all done this way). 92 swaps = 92 pings. Slow but reliable.
2. **OR** investigate `scene_nodes_apply_pattern` / `recipe_run` / `scene_nodes_create_capsule_with_pattern` MCP tools that might handle bulk SN operations properly at the plugin layer (where transaction lifecycle can be managed correctly).
3. **OR** add a new `scene_nodes_bulk_swap_nodes` MCP tool to cinema4d-mcp that takes a list of (og_id, my_name, asset_id) and does the swap in C++ with proper transaction + main-thread routing. This is the "right" long-term fix per `feedback_what_claude_needs_to_be_effective_on_sn_work.md` (Items 2 + 3: disconnect_port API + refresh_host helper).

**Recommended path:** option 3 (MCP-side bulk helper) before continuing the 1-1 replica work. This is the kind of infrastructure investment Spenser called out — building it once means every future SN-graph rebuild attempt is fast and reliable.

### Reusable swap function (current state — needs P10 hardening)

```python
def perform_swap(og_id_prefix, my_name, asset_id):
    # 1. Find OG by id-prefix match in graph
    # 2. Read OG input wires: [(port_name, source_node, source_port_id), ...]
    # 3. Read OG output wires: [(out_port_name, dest_node, dest_port_id), ...]
    # 4. Add MINE (graph.AddChild(maxon.Id(my_name), maxon.Id(asset_id)))
    # 5. [HARDENING NEEDED for arithmetic] Read OG.operation + OG.datatype, set on MINE BEFORE wiring
    # 6. Mirror OG inputs to MINE.inputs (parallel reading)
    # 7. Visual verify == baseline
    # 8. Delete OG (auto-severs OG outputs, frees downstream ports)
    # 9. Wire MINE.out to OG's old downstream targets
    # 10. SetDirty + EventAdd + ExecutePasses
    # 11. Visual verify == baseline (proves 1-1 lock)
```

### Detail — Swap #1 (`transform_element@dtyScx` — the OUTPUT GATE)

**Original wire map:**
```
transform_element@dtyScx
├── IN  transformin <- inversematrix@HvjBjO.out
├── IN  geometryin  <- if@QIDSSw.out
└── OUT geometryout -> root.>geometryout
```

Procedure: added MY parallel, replicated 3 connections, deleted OG. Pixel-perfect at 136,116. Mid-swap, Spenser refined the methodology to "keep OG alive" via 2x ping verification (now standard for swaps 2+).

### Detail — Swap #2 (`inversematrix@HvjBjO` — the P7 discovery)

**Original wire map:**
```
inversematrix@HvjBjO
├── IN  in <- reroute@KKX2sU.out
└── OUT out -> MY_xform_dty_swap.transformin (this was MY swap from #1)
```

Procedure: mirrored input, then connected MINE.out to MY_xform_dty_swap.transformin in parallel with OG → **visual broke (93,565 bytes vs 136,116)**. Discovered **P7**: most input ports accept ONE source at a time. Fix: delete OG first → MINE alone feeds downstream → 136,116 restored.

### Detail — Swap #3 (`reroute@KKX2sU` — graphmodel error)

Mid-script "no target to copy for graphmodel" error during downstream rewiring; OG deleted successfully but MINE.out wiring needed manual repair (1 extra script). After repair: 1 conn each side, 203 nodes. Locked in 136,116.

### Detail — Swap #4 (`if@YKyIwie7` — first run with reusable function)

Same graphmodel error mid-script but core swap completed (OG gone, MINE present, 203 nodes). Visual matches baseline.

### Detail — Swaps #5-6 (arithmetic drift — P10 discovery)

Both arithmetic swaps completed structurally (OG gone, MINE in place, wires correct, 203 nodes) but file size went 136,116 → 136,182 (+66 bytes). Visual still matches shape but pixel-level drift detected. Diagnosis: fresh arithmetic nodes default to scalar/no-op; OG had specific `operation` (e.g. `sub``div`) and `datatype` (`net.maxon.parametrictype.vec<3,float>`). The reusable helper currently doesn't transfer these. **P10** logged. Fix: extend helper to detect arithmetic and copy `operation`+`datatype` before wiring inputs (must be BEFORE per **P3**).

---

## Section 10 — Operational notes

- Match Size graph deletions are NOT in C4D's standard undo stack — restore via `LoadFile()` between experiments
- Single-pass static deformer (0 LCV, no time_state) — graph runs end-to-end every viewport refresh
- Doc CLOSED via `KillDocument(doc)` at end of session per RAM-hygiene
- Image-dimension safety rule: viewport_screenshot ≤800x450 default; pass save_path; 1024x576 max once or twice per session
