# Scene 23 — Match Size (Stone Circle) — Composition Study

A creative marriage of **classic MoGraph** (Cloner + Distribution + Connect) and **Scene Nodes** (Stack Stones + Match Size deformers). Five rocks get cloned across a layout, stacked vertically without overlap by a custom SN deformer, normalized to a uniform size envelope by another SN deformer, and shaped with a final classic Bend.

This is the second external scene we use to validate the C++ bulk-swap tool — and the first one that exercises a **non-trivial composition** (multiple SN deformers, classic generators in the chain, sibling-deformer ordering).

> See `study_v1.md` for an earlier tear-apart-style notes pass on this same scene.

---

## OM architecture

```
Cube                                       (top-level — disabled in editor; isolated test setup)
└─ Match Size                              SN deformer (180420400) — 118 nodes, asset_v1004 (same as scene 21)

Doodle Object::Front                       (annotation)
Time + Random Field                        (animation drivers, hidden)
Random Initial Scale                       (XPresso-style randomizer)
Random Animation Scale                     (XPresso-style randomizer)

Null
└─ Connect                                 classic generator (1011010) — concatenates child output
   ├─ Cloner                               1018544 — 5 source rocks, NEW Distribution path enabled
   │   └─ Polygon Reduction × 5
   │       └─ Aset_nature_rock_S_*_LOD0    (5 reference rocks)
   ├─ Stack Stones                         SN deformer (180420400) — 7 nodes (5 functional + 2 framework)
   ├─ Match Size                           SN deformer — 118 nodes (same asset as Cube/Match Size + scene 21)
   └─ Bend                                 classic deformer (5128) — final shaping
```

**Critical observation:** the three deformers (Stack Stones, Match Size, Bend) are **siblings of Cloner under Connect**, NOT children. C4D processes Connect's output through them in OM order. Connect concatenates Cloner's clone instances → Stack Stones reorganizes → Match Size normalizes → Bend curves.

---

## Cloner config (the MoGraph half)

| ID | Param | Value | Meaning |
|---|---|---|---|
| `MGCLONER_MODE` (1020) | mode | 2 | (multi-mode: works in conjunction with Distribution) |
| `MGCLONER_USE_DISTRIBUTION_CLONES` (1028) | use distribution | **1** | **uses C4D 2026 Advanced Distribution path** |
| `[2107]` | Distribution Type | 0 | "Basic" |
| `[2114]` | (Distribution flag) | 1 | enabled |
| `MGCLONER_SEED` (1022) | seed | 123456 | deterministic |
| `MG_LINEAR_COUNT` (1270) | linear count | 25 | (used when not in distribution mode) |
| `MG_GRID_RESOLUTION` (1200) | grid res | (3,1,3) | 9 grid clones (when in grid mode) |

The Cloner uses C4D 2026's **Advanced Distribution Generator** — a new pipeline (per memory ref `c4d_2026_distribution_generator_190000011`) where the cloner reads from a Distribution node rather than computing positions itself. The exact distribution source needs further inspection (param `[2115]` Distribution link).

---

## Stack Stones SN deformer — algorithm decoded

**7 top-level nodes, 5 functional + 2 framework.** Pattern: `loop_scaffold` (per scene-nodes atlas) — depth-bounded iteration with carried state.

```
                    <geometryin>  (graph input — Connect's output flowing in)
                         │
                         ↓
                  [explode_islands]
                         │
                         ↓ geometriesout (array of N island geometries, one per rock clone)
                  ┌──────┴──────────┐
                  ↓                  ↓
        [readvalueatindex]      [erase]
                  │                  │
                  ↓ ._0              ↓ arrayout (array minus one element)
        (extracts FIRST            │
         island as initial         ↓
         carry value)        [containeriteration]
                  │                  │
                  │                  ├─ innerdomain (loop var)
                  │                  └─ out (current array element)
                  │                  │
                  ↓                  ↓
              initial._0       in@MW3PA (LCV body input)
              ↘                ↙
            [loopcarriedvalue]
                  │
                  ↓ next._0  (recursive: feeds back into current._0 next iteration)
                  │
                  ↓ final._0 (after iteration completes)
                  ↓
              <geometryout>  (graph output — to Match Size)
```

**Algorithm interpretation (best read from the wires):**
1. **explode_islands** splits the Cloner-output mesh into N separate-island geometries (one per rock clone).
2. **readvalueatindex** picks the FIRST island as the iteration's initial accumulator.
3. **erase** drops that first element from the array (so the iteration sees the remaining N-1).
4. **containeriteration** loops over the remaining islands one at a time, exposing `innerdomain` (loop index) + `out` (current element).
5. **loopcarriedvalue** holds the running combined geometry. Its `current._0` is what we have so far; `next._0` is what we hand to the next iteration. The "stack on top" math (Y-offset by current.bbox.height) lives inside the LCV body — likely encoded via maxon value-type combination semantics on the geometry-typed carry slot.

**Assumed intent — "stones don't collide":** by iterating sequentially and accumulating, each new island is combined with the running stack at a position offset by the existing stack's bbox max-Y. The collision-free guarantee is structural, not computed (no per-pair distance checks).

This is `R-loop-scaffold`-class procedural code: scene 23 is a clean reference for this pattern.

---

## Match Size SN deformer — same asset as scene 21

The Match Size under Connect is a **direct instance of the same Match Size asset** scene 21 already analyzed. 118 nodes total, identical type histogram. Per scene 21 work: 92 are functional swappable; 27 are deferred (8 scaffolds, 2 groups, 2 contexts, 2 phantom-input `if`s, 5 wrapper capsules with nested sub-graphs, plus `transformmatrix`/`type`/etc. without known asset_ids).

In Stone Circle, Match Size's role is **per-stone normalization** — every stacked rock gets resized to fit the same envelope so the stack reads as visually uniform. This is the same "normalization wand" semantic articulated in the scene 21 study.

There's also a SECOND Match Size on the top-level Cube (depth 1, parent disabled). Likely a leftover test setup or alternative configuration.

---

## Bend deformer — final shaping

Classic deformer (5128). Likely curves the stacked column into a slight arc — the "creative" finishing touch that takes a vertical stack into a more sculptural form. Settings not yet captured.

---

## C++ bulk-swap tool validation plan

**Primary target for tonight: Match Size on Cube (depth 1)** — first SN deformer the C++ tool's `FindFirstObjectOfType` walk hits, no targeting needed. 94 swappable, 22 deferred (per scene 21 deferred-set rules + 2 extras: `transformmatrix` and `type` since their asset_ids are now in the atlas but un-attempted).

**Spec list built:** 94 specs ready in `_snapshots/specs.txt`. Bulk_swap call should complete in ~150ms (~30 minutes of Python with restart cycles otherwise).

**Stack Stones replication: deferred.** Requires `target_host_name` parameter on the C++ tool so we can address the third SN deformer in the OM (currently the tool always picks the first). That's a follow-up C++ iteration, not blocking the validation.

---

## What this scene proves once replicated

1. **C++ tool works on a real artist-authored production scene** (not just synthetic).
2. **94 swaps in one call** is feasible, no per-session ceiling.
3. **Multi-deformer scenes are accessible** (with the `target_host_name` follow-up).
4. **The composition pattern is documented** — future scenes using "Cloner + Connect + sibling SN deformers" have a clear precedent.
5. **The loop_scaffold pattern is captured** as a reference recipe via the Stack Stones algorithm decode.

---

## Source attribution

This study covers the scene composition + algorithm. The original scene file is not redistributed (proprietary tutorial material). The replication artifacts (specs, snapshots, audit logs) are derivative analysis only.
