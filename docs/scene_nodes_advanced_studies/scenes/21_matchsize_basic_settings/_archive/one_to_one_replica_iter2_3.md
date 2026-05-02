# Match Size 1-1 Replica — Iterations 2 & 3

**Date:** 2026-05-01 evening continuation

---

## Iteration 2 — wiring the BB chain

**Action:** wired core data flow:
- `root.geometryin → my_bb_0.geometryin` + `my_bb_1.geometryin` + `my_bb_2.geometryin` + `my_xform.geometryin`
- `my_bb_0.max → my_arithmetic_0(sub).in1`, `my_bb_0.min → my_arithmetic_0(sub).in2` (= source size)
- `my_arithmetic_0.out → my_arithmetic_1(div).in2` (target / size)
- `my_arithmetic_1.in1 = (277.4, 269.4, 277.4)` hardcoded cylinder size
- `my_arithmetic_1.out → my_composematrix_0.scale`
- `my_composematrix_0.out → my_xform.transformin`
- `my_bb_0.center → my_xform.pivotin` (centering)
- `my_xform.geometryout → root.geometryout`

**Set arith config first** (per the canonical-cycle-Id memory):
- `my_arithmetic_0`: datatype=`net.maxon.parametrictype.vec<3,float>`, operation=`sub`
- `my_arithmetic_1`: datatype=`net.maxon.parametrictype.vec<3,float>`, operation=`div`

**Result:** Torus VISIBLY DEFORMS — scaled to roughly cylinder bbox. But there are GLITCH SPIKES at the bottom edge, suggesting either:
- Pivot isn't right (`bb0.center → xform.pivotin` may be wrong port name; the reference build might use a different pivot port)
- Or chain-order issue (the reference build has 7 transform_elements; mine wires only 1)

Screenshot: `frames/replica_iter2_BB_chain_wired.png`

## Iteration 2 hypothesis DISPROVEN

**Hypothesis:** wiring would trigger wrapper sub-children to auto-populate (legacyobjectaccess, delete, active, cube, get_property would grow internal sub-nodes).

**Result:** Node count UNCHANGED at 153. Wiring did not trigger sub-node expansion.

## Iteration 3 — the deeper revelation

**Probed the reference `legacyobjectaccess@eOgP` internals:**

Direct children of the legacyobjectaccess node:
- `baselistparameter` (kind=1, real node)
- `matrixop` (kind=1, real node) — has its own sub-children: combine, mat, sqrpart, vectrans, sqrtrans
- `objectimport` (kind=1, real node)
- `net.maxon.neutron.corenode.multransform_5` (kind=1, real node)
- `combine`, `mat`, `sqrpart`, `vectrans`, `sqrtrans` (top-level inside legacyobjectaccess)
- `>` (output port, kind=4)
- `<` (input port, kind=2)

**The realization:** the reference wrapper nodes are NESTED GRAPHS. He added sub-nodes INSIDE each wrapper's own embedded graph. My wrapper instances are "minimal" — just the default `baselistparameter` for legacyobjectaccess. the reference are "expanded" with 9 manually-added internal sub-nodes.

**This means:** to truly 1-1 replicate Match Size, I'd need to:
1. Get the nested graph reference from each wrapper node
2. Add the matching sub-nodes inside that nested graph
3. Wire the sub-nodes' ports inside the wrapper

That's a fundamentally different operation than top-level node addition. And it's why:
- My 117 top-level nodes + 14 auto-subs = 131 + framework = 153
- the reference 117 top-level + 14 auto + ~50 manually-added wrapper internals = 203

**The 50-node gap is wrapper-internal sub-graphs.** Not wiring, not missed top-level types.

## Iteration 3 also found: floatingio → legacyobjectaccess wiring pattern

the reference `legacyobjectaccess.baselistlink ← floatingio.in1.in@ZSqEEYyhPRXoHnuvR_w3oq`

This is HOW the artist's "target object" AM link works structurally:
- Artist drops a target object into the deformer's AM slot
- That slot is a `floatingio` node inside the graph
- floatingio.out → legacyobjectaccess.baselistlink → reads target's transform/bbox

For my replica, I need a similar floatingio→legacyobjectaccess connection so the legacyobjectaccess actually has data to read.

## Updated 1-1 status

| Metric | My v1 (iter 2) | the reference build | Notes |
|--------|---------------:|--------:|-------|
| Total nodes | 153 | 203 | -50 (wrapper internals) |
| Top-level nodes | 117 | ~118 | -1 (interpolate, asset id not found) |
| Auto sub-nodes (xform/compose/connect) | 14+4+4 | 14+4+4 | ✓ |
| Wrapper internal sub-nodes | ~10 | ~60 | **BIG GAP** |
| BB chain wired | ✅ | ✅ | match |
| Visible deformation | ✅ (with spikes) | ✅ (clean fat torus) | partial match |
| Floatingio → legacyobjectaccess wiring | ❌ | ✅ | next |
| AM exposure | 0 | 15 | not started |
| Scaffold organization | 0 named | 8 named sections | not started |

## What this iteration unlocked (lessons)

1. **"Wrapper" nodes (legacyobjectaccess, delete, active, cube, get_property, transform_element's transformpoint, composematrix's _0/_1) all have NESTED graphs.** Some auto-populate (transform_element/composematrix/connect_geometries), most don't (legacyobjectaccess/delete/active/cube). Manual sub-graph editing needed for those.

2. **The floatingio → legacyobjectaccess pattern is how AM-linked target objects flow into the deformer.** Generalizable: any time a Match-family deformer needs to read an external object's properties, the chain is `[AM slot] → floatingio → legacyobjectaccess.baselistlink → outputs (transform, bbox via separate bb)`.

3. **Visible deformation can occur with the BB chain alone**, but the result is "rough" — the 7 transform_element chain in the reference full graph is what cleans up the result (per-axis variants, anchor handling, mode dispatch).

4. **The 50-node gap is structural depth, not breadth.** I have all the right top-level node types, but the reference wrappers are deeper hierarchies.

## Iteration 4 plan

1. **Add the floatingio → legacyobjectaccess wiring** for the target-object link (would need a 2nd bb to read the target's bbox so the divide can use real target size, not hardcoded)
2. **Probe the reference xform output chain** — backtrace from transform_element@dty (the OUTPUT GATE) through the if-branches to see how the 7 transform_elements connect in his graph
3. **Wire MY transform_elements into a chain** matching the reference structure (5 in series + if-branch picking between Position-only/Scale-only/Both)
4. **Compare visual output** — see if the spikes go away when the full chain is wired

## Iteration N plan (longer-term)

To reach true 1-1 (203 nodes match): tackle the wrapper sub-graph editing. This requires figuring out:
- How to get a reference to a wrapper node's INTERNAL graph (probably via `node.GetGraph()` or similar maxon API)
- How to BeginTransaction on that internal graph
- Add sub-nodes via the same `AddChild(maxon.Id, asset_id)` pattern but targeting the internal graph

That's a separate API exploration. For iteration 4, focus on top-level wiring + visible-output convergence first, leave wrapper-depth for iteration 5+.
