# Match Size 1-1 Replica — Iteration 1

**Date:** 2026-05-01 evening
**Goal:** rebuild the reference 203-node Match Size from scratch, matching every node type and count, no copy-paste cheating.

---

## Iteration 1 result

| Metric | My v1 | the reference build | Verdict |
|--------|------:|--------:|---------|
| **Total nodes** | **153** | **203** | -50 (75% of target) |
| Top-level nodes I added | 117 | ~118 (+1 interpolate I missed) | -1 type missed |
| transform_element subnodes | 14 (auto) | 14 (auto) | ✓ |
| composematrix subnodes | 4 (auto _0/_1) | 4 (auto _0/_1) | ✓ |
| connect_geometries subnodes | 4 (auto filter/connect) | 4 (auto) | ✓ |

## Pitfalls discovered

### Pitfall 1: Missed the `interpolate` node entirely
the reference build has `interpolate@aEgLSKMYBBqtdLtPdNx2D7` at top level with `get` and `prep` sub-children. I dropped it from my counts during planning. Fix: re-tally from the practice file before starting iteration 2.

### Pitfall 2: Sub-nodes inside "wrapper" nodes don't auto-populate
The following node types in the reference graph have characteristic sub-children that DID NOT auto-appear when I added the parent in my replica:
- **legacyobjectaccess** → 9 sub-children in the reference build (matrixop, combine, mat, sqrpart, vectrans, sqrtrans, objectimport, multransform_5, baselistparameter). Mine: only 2 baselistparameter visible.
- **delete** → 1 sub-child (deletemeshcomponent). Mine: 0.
- **active** → 2 sub-children (variadictolist + selectionoperator). Mine: 0.
- **cube** → 3 sub-children (parambuilder + generategeometry + defaultselections). Mine: 0.
- **get_property** → 1 sub-child (get). Mine: 0.

**Hypothesis:** these sub-nodes appear automatically when the parent's ports get WIRED to specific upstream sources. They're created on-demand by the node's runtime expansion. This means my unfilled wrapper nodes are "minimal" versions — they'll grow when I wire them.

### Pitfall 3: Function class distribution shows 0% on most categories
| Function class | My v1 | the reference build |
|----------------|------:|--------:|
| math_scalar | 0% | 10.3% |
| math_vector | 0% | 0% |
| math_compound | 4.6% | 7.4% |
| logic | 1.3% | 16.3% |
| object_access | 1.3% | 12.8% |
| visual | **0%** | **20.7%** |

The 0%s aren't because I'm missing those node types — I have all the right node types. They register at 0% because **none of them are WIRED** so their function-class contribution is dormant. the reference high % across categories comes from active connections feeding visual / logic / object-access operations.

**Implication:** the COUNT match isn't enough; the WIRING is what makes the function class distribution match. For a true 1-1 replica I need ~200+ port-to-port connections matching the reference structure, not just node counts.

## What's solid

- **Asset ID resolution:** 17/24 first-guess + 6 reference-lookups = all asset IDs known
- **Node placement:** 117 top-level nodes added cleanly via transactions, no errors
- **Auto sub-children:** transform_element, composematrix, connect_geometries auto-populate their internal helpers — those are "free"
- **GetActiveDocument workflow:** scene-build + node-add + classify all work end-to-end

## Iteration 2 plan

1. **Add the missed `interpolate` node** with its 2 children (`get` + `prep`)
2. **Wire the major data flow connections** to trigger sub-node expansion in legacyobjectaccess, delete, active, cube, get_property — should bring node count up significantly
3. **Re-classify** and check if total ≈ 203
4. **Then start mapping individual port wires** — at least the obvious ones from the practice file backtrace (xform.geometryout → root>.geometryout, bb chain, etc.)

## Honest framing

This is iteration 1 of the 1-1 replica work. We're at **75% of node count** (153/203) and **functionally inert** (no wiring). True 1-1 replica = match all 6 criteria from [project_north_star_exact_replica_first](../../../home/spenser/.claude/projects/-mnt-c-Users-Spenser-Dickerson-Projects-LightPainter/memory/project_north_star_exact_replica_first.md):
- ✓ Approaching node count match (153/203, will reach with sub-node expansion)
- ✓ Type histogram approaching match (have all major types except interpolate)
- ❌ Port wiring (0/many — nothing wired yet)
- ❌ AM exposure (floatingio nodes present but not configured for AM exposure)
- ❌ Scaffold organization (nodes added but no labels matching the reference section names)
- ❌ Output equivalence across multiple inputs (graph is inert)

Iteration 2 needs to start wiring. That's the harder half — the practice file has 200+ wires to mirror.

## Reusable learnings (to other practice files)

These apply to any future 1-1 rebuild attempts:
- Node placement is the easy part — wiring is 10× harder
- "Wrapper" nodes (legacyobjectaccess, delete, active, cube, get_property) have on-demand sub-children that appear when wired
- Auto sub-children are free (transform_element, composematrix, connect_geometries)
- Re-tally counts from practice file directly via classify (don't trust manual counting)
- Plan to spend at least as much time on wiring as on placement
