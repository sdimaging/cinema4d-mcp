# Scene 27 — Spiderweb Setups (Simple→Complex pedagogical pair)

**Studied:** 2026-05-01 (resumed)
**Source:** `scene_05_reference/Spiderweb_Setups_2024_01.c4d`
**Plugin:** 180420700 × 2 (Scene Nodes Generator) — same plugin, different graph templates

---

## TL;DR

This scene is **the pedagogical artifact** showing how the reference build ships the same procedural recipe in two flavors:
- **Spiderweb_Simple_01** (53 nodes) = clean textbook web. Bare-minimum algorithm. Perfect for understanding the structure.
- **Spiderweb_Complex_01** (159 nodes, 3× larger) = realistic organic tangled web with anchor lines. Same algorithm + randomization, animation, more AM controls, more iteration loops.

See [stage0_both_webs.png](frames/stage0_both_webs.png) for the side-by-side: clean white symmetric web (Simple) next to messy green organic web (Complex).

This is the **"simple algorithm + randomization layer = realism"** authoring lesson — applicable to any procedural pattern recipe.

---

## Side-by-side comparison

| Metric | Simple_01 | Complex_01 | Delta |
|--------|----------:|----------:|-------|
| Total nodes | 53 | 159 | **3× larger** |
| Endpoints input pts | 9 | 26 | nearly 3× |
| containeriteration loops | 4 | 6 | +2 nested loops |
| arithmetic | 5 | **39** | **8× more math** |
| compare | 0 | 9 | NEW (conditional thread placement) |
| if | 0 | 6 | NEW (mode dispatch) |
| append | 3 | 5 | similar (output assembly) |
| group | 0 | 6 | NEW (organized sub-graphs) |
| floatingio (`_0`+`_1`) | 0 | **8+8 = 16** | **NEW: 16 AM-exposed params** |
| stochastic | 0 | 0.038 | **NEW: randomization** |
| time_state | 0 | 0.006 | **NEW: time-driven anim possible** |
| Loop-carried-state | 0 | 0 | both stateless nested-iteration |
| Dominant class | object_access (30.2%) | math_scalar (30.2%) | shifts from "reading inputs" to "computing positions" |

The Simple form just **reads inputs and emits the basic radial pattern**. The Complex form **reads, randomizes, varies, conditionally adds anchor strands, and exposes 16 artist controls.**

---

## Visual evidence

**Simple_01 (left, white):**
- ~8 perfectly even radial spokes radiating from center
- Concentric ring threads at uniform spacing
- Symmetric, geometric, slightly artificial-looking
- Endpoints input = 9 pts (a clean curve definition)

**Complex_01 (right, green):**
- Many radial spokes with VARIED lengths and angles (some longer than others)
- Multiple thread layers crossing chaotically (the messy "real web" look)
- Anchor lines extending OUTWARD beyond the main web body (the strands a real spider uses to anchor to nearby surfaces)
- Some threads are denser, some sparser — not uniform
- Endpoints input = 26 pts (more detailed thread definition with curvature)

This is the **"perfect → realistic"** transformation that randomization layers on top of a base algorithm.

---

## What the Complex version adds (architecturally)

Cross-referencing the node-vocab deltas with the visual outcome:

| Architectural addition | Visual effect |
|------------------------|---------------|
| 6 `if` + 9 `compare` (conditionals) | Some threads CONDITIONALLY appear (e.g. anchor strands only at certain angular positions, only when far enough from center) |
| 8× more `arithmetic` | Per-thread random offsets applied to position/length/angle |
| `stochastic` (3.8% of node fns) | Hash-based deterministic randomization for per-thread variation |
| `time_state` (0.6%) | Animation possible — web could grow over time |
| 16 `floatingio` AM ports | Artist tunes: spoke count, ring count, randomness amount, anchor-line probability, sag, etc. |
| 6 `containeriteration` (vs 4) | Extra loops for: anchor strands, fine detail crosses, irregular cross-threads |
| 6 `group` nodes | Better-organized sub-graphs (the artist organized this for maintainability) |

**The "Complex" version is essentially the Simple algorithm + 4 production-quality features:**
1. Per-thread randomization (stochastic + arithmetic)
2. Conditional thread placement (if + compare)
3. Time-driven animation hook (time_state)
4. Artist tuning surface (16 floatingio)

---

## Pedagogical pattern: the "Simple → Complex" recipe-shipping convention

the reference authoring style (extrapolated from this pair):

1. **Build the Simple version first.** Bare algorithm. Minimal nodes. Just prove the math/structure works.
2. **Ship the Simple version.** It's small (53 nodes), readable, debuggable. Other artists/learners can study the algorithm.
3. **Layer realism on top → Complex version.** Add randomization, conditional variations, AM controls, animation. Same fundamental algorithm at the core.
4. **Ship BOTH together** in the same file. Side-by-side viewing teaches the "raw → polished" progression.

**This is the gold standard for recipe-library publishing:** every recipe ships in a Simple + Complex pair so consumers can understand the core AND get a production-ready version.

For our cinema4d-mcp recipe library: adopt this convention. Every R-recipe should have:
- `R_simple` form: bare algorithm, ~50 nodes, all in one file
- `R_complex` form: same algorithm + randomization/variation/UI surface, ~150 nodes
- Both delivered in one .c4d so artists see the progression

---

## Recipe candidates

### R35 — Simple→Complex shipping convention for recipe library

**Purpose:** every procedural recipe ships in BOTH bare-algorithm and production-quality flavors, side-by-side in one .c4d, so consumers can study the core algorithm AND get a usable production tool.

**Pattern:**
- Build bare version (~50-100 nodes) — just the algorithm, minimal UI
- Build production version (~150-300 nodes) — same algorithm + randomization + AM surface + conditional variations + animation hooks
- Ship both side-by-side in one scene file
- Document the deltas explicitly (this study's table is the template)

### R36 — Procedural realism via stochastic + conditional layering

**Purpose:** turn any "perfect geometric pattern" into a realistic organic-looking version by adding 4 specific layers:

1. **Stochastic per-element randomization** — `arithmetic` + `hash` produces deterministic per-iteration variation (offset position, length, angle by small random amounts)
2. **Conditional element placement** — `if` + `compare` lets some elements appear only sometimes (e.g., anchor strands only at outer ring positions)
3. **Animation hook via context_externaltimeinput** — graph reads time, optionally grows/shifts over animation
4. **AM-exposed tuning surface via floatingio** — expose ~10-16 controls so artists can dial the realism

**Use case:** any time you have a "too perfect" procedural output (mandala, gear pattern, brick wall, fence, fabric weave) and want to add organic imperfection.

---

## Cross-link to scene 26 (Tutorial)

| Aspect | Tutorial (scene 26) | Setups Simple (scene 27) | Setups Complex (scene 27) |
|--------|--------------------:|-------------------------:|--------------------------:|
| Node count | ~40 | 53 | 159 |
| Endpoints pts | 6 | 9 | 26 |
| AM params (BC) | ~6 | ~0 | 16 |
| Visual quality | mid (curvy 3D) | clean geometric | organic realistic |
| Purpose | teach the architecture | show bare algorithm in production form | show production-tuned form |

The **scene 26 Tutorial** is essentially a stepping-stone between the Simple and Complex Setups — slightly more elaborate than Simple, less polished than Complex. Three-tier progression: tutorial → simple-prod → complex-prod.

---

## Operational notes

- Both generators are PLUGIN 180420700 (same as scene 26's "Nodes Spline") — different graph templates inside the same host plugin
- Both have 0 LCV — both use stateless nested iteration (the Spiderweb pattern doesn't need fold)
- The Complex version's `time_state` (0.006) is barely active — animation hook present but not the dominant feature
- The 16 floatingio in Complex = the AM tuning surface; would need UI exploration to map each port to its visual effect
- The Center_Point Null in both webs is the same per-input-spline relative reference (per the scene 26 study — NOT a world anchor)
