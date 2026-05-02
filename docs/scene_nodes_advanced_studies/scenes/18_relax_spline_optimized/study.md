# Scene 18 — Relax-Spline_02_Optimized (delta-only)

**Studied:** 2026-05-01
**Source:** `Relax-Spline_02_Optimized/Relax-Spline_02_Optimized.c4d`
**Twin of:** [scene 17 — Relax-Spline_01_Tutorial](../17_relax_spline_tutorial/study.md)

> "optimized version just seems very similar to the other version" — Spenser

---

## TL;DR

Scene 18 is a structural near-duplicate of scene 17. **Same 23-node SN graph (identical node IDs), same 64-pt static Spline (identical coordinates), same Extrude params, same materials, same renderer.** The only structural delta is the **time range** (0..100 vs scene 17's 0..999). The "optimization" appears to be a parameter/timing tune for a clean short loop, not a graph rewrite. No new recipes extracted.

---

## Evidence of structural identity

- **SN graph node IDs match byte-for-byte:** `composematrix@a_4qfS9tKF$ta$ioW2TaY6`, `bb@JeZkk_GeCdGhY$$yBOP5g5`, `group@MQciJwvPPBUk5B$c1FlR$4`, `transform_element@dSzTWkgTJR8gqmrMycUAxL`, `inversematrix@fuvNWBECI1liizIt73vOc5` — all the same hashes as scene 17
- **Spline first/last 3 points identical** to the digit between scenes
- **Extrude MOVE = `Vector(0, 0, 100)`** in both
- **Text "3" height = 920.8253895627777** in both (full float precision match)
- **Landscape radius = `Vector(443.7, 96.45, 443.7)`, cache 10201 pts, 4 cache tags** in both
- **Materials, renderer ID, nimbus presence** all identical

The "Optimized" naming and the tighter time range (0..100) strongly suggest the artist tuned scalar parameter values inside the maprange/composematrix nodes for a clean looping cycle of 100 frames — not visible at the topology level, would require deep per-node value diff to capture.

---

## Insight added (small)

Identical SN architectures can be re-tuned for different production purposes (long-form playback vs short clean loop) **without touching node topology**. The optimization happens entirely in the parameter values + the document's max-time setting. Worth remembering: when shipping our own SN-based tools, expose the iteration-count or loop-length as an AM slider so the artist can pick "exploration mode" vs "loopable render mode" without rewiring.

---

## Recipe library impact

**No new recipes.** R28 ("contained RD spline on surface") and R29 ("axis-remap deformer") from scene 17 cover this scene completely.

The carried blockers from scene 17 remain **unresolved** (no new evidence here either):
- Identity of the visible animated colored ribbon
- Containment-clamp mechanism (the `selectionstringparser` hint is still suggestive but unverified)
- Loop-carried-value source — classifier still reports 0; visible animation is real but the driver is unconfirmed
- Tag type 5604 on Landscape cache
- Renderer ID 300001061

These will need to be resolved either by manually opening the SN editor in C4D (Spenser's task — the "gorgeous layout" capture) or by parameter-level deep comparison.

---

## Visual progression

| frame | observation |
|------:|-------------|
| 0 | Same as scene 17 frame 0 — short colored ribbon visible top-right of the "3" |
| 30 | Ribbon longer, similar evolution to scene 17 |
| 60 | Mid-growth filling pattern |
| 90 | Approaching the "fully filled 3 interior" state |

See `frames/f0000_persp.png` … `frames/f0090_persp.png`.

---

## Operational notes

- **C4D doc CLOSED** via `KillDocument` at end of session per Spenser's RAM-hygiene reminder ("remember to close scene files when youre done to save on ram/memory etc")
- Study cameras were inserted into the in-memory copy of the doc only; not saved back to disk
