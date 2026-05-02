# Scene 26 — Spiderweb (Tutorial)

**Studied:** 2026-05-01 (resumed)
**Source:** `scene_05_reference/the spiderweb practice scene`
**Plugin:** **180420700 (Scene Nodes Generator)** — note: GENERATOR, not deformer (180420400 was Match Size)

---

## Why this scene exists (theory + creative direction)

Spider webs are CONSTRUCTED, not deformed. Real spiders build them by:
1. Anchoring at a CENTER point
2. Spinning RADIAL threads outward (structural spokes)
3. Spiraling/connecting threads BETWEEN spokes (the catching surface)
4. Each web's shape varies with environment, spider species, gravity, anchor support

**This scene's procedural mirror of that reality:**

| Creative element | Technical input |
|------------------|-----------------|
| The QUALITY of one thread (sag/curve/length/taper) | A user-editable Spline (6 points in 3D space) — the artist DRAWS what one thread looks like |
| The SPIDER'S relative position on the thread | A Center Point Null inside the host (positions the spider's central reference) |
| The GENERATOR LOGIC (radial replication + ring weaving) | The Scene Nodes Generator host (180420700) with internal nodes |

**Authoring archetype:** "creative template + geometric anchor → procedural pattern." The artist owns the creative part (template spline), the generator owns the geometric rule. Decoupling them = artist freedom + reusable algorithm. Same archetype applies to mandalas (petal+center), snowflakes (arm+center), sunbursts (ray+center), wheels (spoke+hub), gears, compass roses.

**Why a GENERATOR (180420700) not a DEFORMER (180420400):** the web doesn't pre-exist — it's CONSTRUCTED from scratch using the inputs as a recipe. Generators produce new geometry; deformers transform existing geometry.

---

## Object hierarchy

```
Nodes Spline (180420700, Scene Nodes Generator) ── output: a single spline (the entire web as one geometry)
├─ Spline (5101)            — INPUT #1: the artist-drawn template thread (6 pts, open, in 3D)
└─ Center Point (Null 5140) — INPUT #2: relative anchor reference (lives in generator's local space)
```

Frame range: 0..90 (likely some animation possible — the Spider's web could grow/animate, but baseline shows static)

---

## Generator graph signature (40 root children)

| Node type | Count | Purpose |
|-----------|------:|---------|
| arithmetic | ~10 | math (positions, ratios, angles) |
| containeriteration | **3** | nested loops — outer = N spokes, inner = M ring crossings (this is how the web is built piece by piece) |
| append | **3** | building up arrays of computed segments |
| range / clamp | 3 | parameter mapping for positions |
| children@ | **1** | reads HOST'S CHILDREN (Spline + Center Point) — the input interface |
| spline@ | **1** | OUTPUT: builds the final spline geometry |
| legacyobjectaccess | 1 | reads input objects' transforms / matrices |
| concat / scale | 3 | combine arrays + scaling |
| assembler | **1** | assembles per-iteration outputs into final spline |
| hash | 1 | deterministic randomization (per-spoke seed?) |
| getcount | 1 | count input spline points or iteration count |
| scaffold | 4 | section organization labels |
| context_externaltimeinput / notime | 2 | standard graph framework |

**Architectural read:** the algorithm is `for spoke in 0..N: for ring_distance in 0..M: compute_thread_segment_at(spoke, ring_distance)` — a doubly-nested iteration that walks the polar grid (spoke-angle × ring-radius) and emits a thread segment at each cell. Output piped through `assembler` → `spline@` → root `>geometryout`.

NOT an LCV-fold (no loopcarriedvalue node). This is **stateless nested iteration** — each iteration's output is independent, all results appended into one spline at the end. Simpler than Stack Stones' fold pattern.

---

## AM-exposed parameters (host BC dump)

| Param ID | Default value | Purpose (inferred) |
|---------:|--------------:|---------------------|
| [1000] | 0 | mode toggle |
| [1001] | 3 | could be a count (axis variants?) |
| [1002] | 0 | mode toggle |
| [1003] | 8 | possibly the spoke count (8 visible) but [1003]=16 didn't change visual — needs deeper test |
| [1004] | 0.0873 rad (≈5°) | angular increment between rings |
| [1005] | 5.0 | could be ring spacing or thickness |
| [10000] | 2 | display flag |
| [2000] | 1 | enable toggle |

**Open:** which AM param IS the spoke count? `[1003] = 16` produced no visible change with SetDirty + ExecutePasses. Either the param has a different role or the asset's eval doesn't react to that change without a proper UI commit. Future debug.

---

## Tear-apart results

| Stage | State | Visual outcome | Verdict | Screenshot |
|------:|-------|----------------|---------|------------|
| 0 | Baseline | Beautiful 3D spiderweb: ~8 radial spokes from center, multiple concentric rings | Working as intended | `frames/stage0_baseline.png` |
| 1 | [1003] set 8→16 + SetDirty | NO visible change (file size identical) | [1003] is NOT the spoke count, OR change isn't reactive | `frames/stage1_spokes_16_dirty.png` |
| 2 | Center Point Null moved to (300, 100, 0) + SetDirty | Null moves visibly (top-right corner now) but WEB STAYS PUT | **Center Point is NOT a world-anchor** — it's a relative reference inside the generator's local space | `frames/stage2_center_moved.png` |

**Key finding from Stage 2:** my initial theory ("Center Point = world anchor for the web") was wrong. The web is anchored at the GENERATOR HOST's position, and the Center Point Null serves as a RELATIVE reference INSIDE the generator's local coordinate space (probably "where on the input spline does the spider sit" — defining which end is the inner/outer side of the radial thread).

---

## Revised theory (after Stage 2)

The TWO inputs serve different purposes than I first assumed:

- **Spline (template thread):** defines the SHAPE/CURVE of one radial thread. The spider's web is a radial pattern of N copies of this shape.
- **Center Point Null:** defines the SPIDER'S RELATIVE POSITION on the thread template. The Null tells the generator "this point on the input spline is where the spider sits" → the algorithm uses that as the inner end for the radial replication, with the rest of the spline extending outward.

This refinement makes the authoring even more elegant: the Null acts as a SEMANTIC MARKER inside the user's input space, not a world-coordinate anchor. The user can position it ANYWHERE along the spline path to shift where the web's center is on the thread template.

---

## Recipe candidate

### R34 — Procedural radial pattern from template + relative-anchor

**Purpose:** generate a radially-symmetric pattern (web, mandala, gear, sunburst) from a single user-drawn template piece + an anchor reference.

**Ingredients:**
- Scene Nodes GENERATOR host (plugin 180420700, NOT deformer 180420400)
- `children@` node — reads host's child objects to find inputs
- `legacyobjectaccess` — extracts transform/matrix from each input
- 2× `containeriteration` (or 3× for spoke + ring + per-segment) — nested polar grid walk
- `arithmetic` + `range` + `clamp` — compute positions per (spoke, ring) cell
- `append` × N — accumulate computed segments
- `assembler` + `spline@` — assemble per-iteration results into one output spline

**Use cases:** spider webs, mandalas, sunbursts, snowflakes, gears, compass roses, mecha gear arrays, decorative trim patterns. Anything radial/concentric where the artist wants to define the unit shape and let the algorithm replicate it.

---

## Cross-link to Stack Stones (architectural comparison)

| Aspect | Stack Stones (LCV-fold) | Spiderweb (nested iteration) |
|--------|------------------------|------------------------------|
| Plugin | 180420400 (Deformer) | **180420700 (Generator)** |
| Total nodes | 214 | ~40 |
| Loop pattern | LCV fold (cumulative pile) | Nested stateless iteration (polar grid) |
| Output type | Modified mesh (deform parent) | NEW spline (constructed) |
| Input | parent geometry | child Spline + child Null |
| LCV | 1 (accumulator) | 0 |

**Two distinct authoring patterns** — Stack Stones is a deformer that USES the parent's geometry as both input and target; Spiderweb is a generator that CONSTRUCTS new geometry from object-link inputs. Different problems, different tools.

---

## Operational notes

- AM param exploration was inconclusive — needs UI-side toggling to map IDs to behavior
- The web is anchored at the GENERATOR HOST'S position, not the Center Point Null
- `frames: 0..90` available — possible the web grows/animates over time but unconfirmed (no time_state in graph classifier)
- This is a tutorial-grade scene — the artist is teaching the procedural-radial pattern via a recognizable subject (the web). Highly didactic.
