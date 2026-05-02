# Scene 23 — Match Size (Stone Circle) (Stack Stones companion study)

**Studied:** 2026-05-01 (resumed)
**Source:** `the stone-circle reference scene`
**Method:** Tear-apart deletion + LCV identification

---

## TL;DR

Stone-Circle is a 3-deformer pipeline that turns 5 different Megascans rocks into a beautiful packed circular arrangement. The pipeline:

```
Cloner (radial, 5 rock children + Random Field for color/scale variation)
   └─ Polygon Reduction × 5 (LOD optimization on each rock)
   └─ Stack Stones (SN deformer 180420400 — packs the clones into a dense pile)
   └─ Match Size (SN deformer 180420400 — normalizes each stone to fit a target envelope)
   └─ Bend (classic deformer — curves the result)
```

**Stack Stones is Match Size + a fold-loop.** Same the reference build authoring style (214 nodes vs Match Size's 203, identical top-vocab) PLUS a `loopcarriedvalue` body that accumulates geometry per iteration. That's the only architectural difference.

---

## Stack Stones graph signature (Asset Version of Match Size family)

| Metric | Stack Stones | Match Size (for comparison) |
|--------|-------------:|----------------------------:|
| Total nodes | **214** | 203 |
| Unique node types | 52 | 44 |
| arithmetic | 20 | 20 |
| reroute | 16 | 16 |
| if | 16 | 16 |
| floatingio | 15 | 15 |
| switch | 13 | 13 |
| scaffold | 8 | 8 |
| transform_element | 7 | 7 |
| selectionstringparser | 7 | 7 |
| **loop_carried_state_count** | **1** | **0** |
| Function class dominant | visual 19.6% | visual 20.7% |

**The 11 extra unique node types in Stack Stones are the loop scaffold + accumulator nodes.** Specifically the LCV body contains:
- `loopcarriedvalue@B1m_2zL$…` (the LCV scaffold)
  - `start` (LCV initializer)
  - `0228e699ef6845f391e25f17f44c708d@C9Ro…` (a UUID-named CUSTOM ASSET — likely the per-stone placement logic, e.g. "find next low spot in accumulated pile and snap this stone there")
  - `connect_geometries@H4U7M4nv…` (cumulative geometry merge)
  - `end` (LCV terminator)

The presence of a UUID-named custom asset inside the LCV is interesting — the reference build built a sub-asset for the per-iteration placement and is reusing it. That's a sign of asset-library composition (build a primitive once, use it inside multiple distributions).

---

## Tear-apart results

| Stage | State | Visual outcome | Verdict | Screenshot |
|------:|-------|----------------|---------|------------|
| 0 | Baseline ON (all 3 deformers active) | Tight packed CIRCLE of normalized rocks, like a fire-pit ring | The intended look | `frames/stage0_close.png` |
| 1 | Stack Stones DISABLED, Match Size ON | Thin sparse ring (rocks at single Y level, evenly spaced, no overlap) | Stack Stones = "the pile-tightener" — turns evenly-spaced into densely-packed | `frames/stage1_NO_StackStones.png` |
| 2 | Stack Stones DISABLED + Match Size DISABLED | Rocks at NATIVE varied sizes break the radial spacing → vertical stairs of mismatched stones | Match Size = "the size unifier" needed BEFORE the packing logic to make stones spacing-compatible | `frames/stage2_NO_StackStones_NO_MatchSize.png` |

---

## Algorithm inferred (Stack Stones core logic)

```
Stack Stones algorithm (single-pass with per-element fold):

INPUT: parent geometry = many separate clone instances (from Cloner above)

1. Read input geometry, separate it into N clone meshes
2. INITIALIZE accumulator = empty geometry (LCV.start)
3. FOR each clone i in 0..N-1:
     a. Custom-asset (0228e699ef…) computes WHERE this clone should sit
        relative to the current accumulator (the pile so far)
        — likely: find the lowest valid contact point, snap this clone
        against neighbors, apply small random nudge for natural look
     b. Translate clone i to that placement
     c. accumulator = connect_geometries(accumulator, placed_clone_i)  ← LCV update
4. RETURN accumulator (LCV.end → root>.geometryout)

That's the FOLD pattern: fold(empty, placement_fn) over the clone list.
The LCV scaffold (start/end/<> ports) is the iteration framework.
```

**This is structurally identical to Match Size + LCV-fold around the `transform_element` chain.** If you understand Match Size's bbox-axis-remap deformer, Stack Stones = "Match Size's per-vertex transform replaced with per-clone-element placement, wrapped in fold."

---

## Pattern unlocked: "Match-Size-shape + fold-loop over elements"

This 2-scene pair (Match Size + Stack Stones) reveals a **the reference build authoring template** for any scenario where you need to process N input elements and combine them:

```
the reference build Match-family template:
- 200-ish nodes
- 15 floatingio AM controls
- 16 if + 13 switch for mode dispatch
- 7 transform_element chains (per-axis / per-mode variations)
- Optional: wrap in loopcarriedvalue + connect_geometries for fold-over-elements
- Output: single transform_element gate → root>.geometryout
```

When we author our own SN deformers/distributions for the recipe library, **start from this template**. The vocabulary is locked, the architecture is proven, and the Match Size + Stack Stones pair is a complete reference for "static deformer" vs "iterative fold deformer."

---

## Recipe candidates added

### R32 — Pile/stack any clone collection (LCV-fold over geometry)

**Purpose:** take N input clones (from a Cloner or Connect generator), pack them densely in a target volume by iteratively placing each one against the accumulated pile.

**Ingredients:**
- LCV scaffold: `start` + `end` + body + `<` + `>` ports
- Inside body: per-iteration placement logic (the UUID-asset OR a custom transform_element chain)
- `connect_geometries` to merge per-iteration result with accumulator
- Match-Size-style mode dispatch (16 if + 13 switch) for axis/anchor/density modes

**Reference implementation:** Stack Stones — 214 nodes total, 1 LCV. See screenshots in this folder for visual reference.

### R33 — Composable normalization-then-pack pipeline (Match Size → Stack Stones → Bend)

**Purpose:** complete pipeline from "varied native-size kitbash collection" to "naturally-arranged scene element" via stacking deformer chain.

**Steps:**
1. Cloner radial mode with N varied geometries as children
2. Match Size deformer — normalize each clone to a common envelope
3. Stack Stones deformer — pack normalized clones into dense pile  
4. Bend or other classic deformer for final shaping

**Note on dependency order:** Match Size MUST come before Stack Stones in the deformer chain. Stage 2 proved this — without Match Size, the rocks at native varied sizes break the radial spacing and the fold-place logic can't pack them properly. Match Size first ensures all clones have predictable bbox sizes that the fold algorithm assumes.

---

## Operational notes

- All 3 deformers (Match Size on Cube, Stack Stones, Match Size in Connect) are SAME plugin (180420400) but DIFFERENT asset templates (Match Size vs Stack Stones)
- The asset DB `the Match Size asset library` MUST be mounted (per the missing-asset gotcha)
- Custom UUID-named asset inside LCV body = an artist-built sub-asset reused across distributions in this library
- This scene also shows: classic OM generators (Cloner, Connect, Polygon Reduction, Bend) cooperating with SN deformers — hybrid is the production pattern
