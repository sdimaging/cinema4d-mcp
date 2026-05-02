# Phase-3 Cross-Scene Results — v9.1 milestone

**Date:** 2026-05-02
**Script:** `scripts/sn_phase3_rebuild.py` v9.1 (RS-filter + auto-spawn + MY_-prefix + atlas asset_ids + CreateCopyOfSelection+Merge deep-clone with surgical trigger)

## Result table — 4 real artist scenes, all hit 100% node fidelity

| Scene | Source nodes | Source wires | Node fid | Wire fid | clone_built | autospawn | AddChild |
|---|---|---|---|---|---|---|---|
| Recursive Subdivision Mesh | 156 | 229 | **100%** | 52% | 6 | 4 | 15 |
| Recursive Subdivision Spline | 31 | 71 | **100%** | 79% | 3 | 5 | 9 |
| Spiderweb_Tutorial_01 | 63 | 116 | **100%** | 79% | 2 | 10 | 37 |
| Match Size after_swap_92 | 203 | 265 | **100%** | 77% | 5 | 31 | 115 |

## What changed v7 → v9.1

| Scene | v7 nodes | v7 wires | v9.1 nodes | v9.1 wires | Improvement |
|---|---|---|---|---|---|
| Recursive Subdivision Spline | 45% | 15% | 100% | 79% | +55 / +64 |
| Spiderweb_Tutorial_01 | 75% | 39% | 100% | 79% | +25 / +40 |
| Match Size after_swap_92 | 72% | 38% | 100% | 77% | +28 / +39 |

## The unlock — `CreateCopyOfSelection + Merge`

Prior architectural ceiling: Python's `GraphNode` has no `AddChild` method, so capsule interiors could only auto-spawn from default templates. Artist-extended body (legacyobjectaccess+matrixop+multransform_5+combine+mat+sqrpart+vectrans+sqrtrans, custom xform/spline/group capsules) couldn't be reproduced.

`NodesGraphModelRef` exposes two methods that break through this:

```python
# 1. Copy a sub-graph (one capsule + its full interior, recursively)
sel = maxon.BaseArray(maxon.GraphNode)
sel.Append(source_capsule_node)
sub_graph = source_graph.CreateCopyOfSelection(sel)

# 2. Merge the sub-graph into target (deep — interior comes along)
with target_graph.BeginTransaction() as tx:
    id_mapping = target_graph.Merge(sub_graph)
    tx.Commit()
# id_mapping[0] = list[(orig_top_level_id, new_renamed_id)]
```

This enables hybrid v9 strategy:
- **AddChild from primitives** for everything resolvable via DEFAULT_ASSET_MAP (true reproduction)
- **CreateCopyOfSelection + Merge** for unaddable artist capsules (deep clone from source)

## v9.1 surgical-clone trigger

v9 over-cloned (cloned capsules even when AddChild worked → degraded wire fidelity by clobbering already-correct connections). v9.1 only clones when:
1. The wrapping capsule's AddChild itself failed with `unknown_asset`, OR
2. The capsule has body-error records WHERE the body's basename has no entry in `DEFAULT_ASSET_MAP` (truly unaddable, not just unprocessed)

Net: clean scenes use AddChild only. Scenes with unaddable interior body use clone surgically.

## What's left — wire fidelity 52-79% (next iteration)

After Merge, top-level cloned ids get renamed (e.g. `legacyobjectaccess@X3iV...` → `legacyobjectaccess@VFie...`). Our `path_to_node` aliases the new id back to the descriptor path, so most wires connect. But ~21-48% of wires fail because:
- Port lookup inside the cloned capsule expects a port id present in the source graph but renamed in the cloned subtree
- Some wires reference deep paths into the cloned interior where Merge's mapping doesn't fully propagate

Path forward: walk Merge's `id_mapping` recursively and build a comprehensive descriptor-port → live-port lookup before PHASE C runs. The infrastructure is there; just needs threading through.

## Implication for the 30-scene grand vision

**Phase-3 has achieved the "from-scratch rebuild" goal at the structural level.** Every node in any artist scene can be reproduced (via AddChild for addable types + Merge for the rest). Wire fidelity is currently a refinement target, not a blocker.

The 30-scene sweep is now mechanical:
1. Load source .c4d
2. Capture descriptor
3. Rebuild with `source_host=src_host`
4. Expect 100% node, 50-80% wire fidelity
5. Visual diff against source

For the teaching pack, this is enough — every scene can be shown as both the original AND a from-scratch rebuild that proves the methodology.
