# MoveToGroup + Ungroup — the Phase-3 capsule-interior unblock

**Date:** 2026-05-02
**Discovered after:** v7 architectural-ceiling RESULTS.md flagged the problem

## The (now-solved) problem

Phase-3 v7 capped at 45-75% on scenes with artist-customized capsule body. The error was always:

> `'GraphNode' object has no attribute 'AddChild'`

Conclusion at the time: Python's GraphNode has no AddChild method, so capsule interiors can ONLY auto-spawn from the parent template. Recommended Path 3 was "extend C++ helper" — heavy effort.

## The breakthrough

`NodesGraphModelRef` exposes two graph-restructuring methods that work in Python:

### `MoveToGroup(groupRoot, groupId, selection)`

```python
from maxon import GraphNode, Id

with graph.BeginTransaction() as tx:
    new_group = graph.MoveToGroup(
        GraphNode(),                  # empty root → creates new group asset
        Id("my_group_name"),          # id for the new group node
        [body_node1, body_node2, ...] # nodes to move INSIDE the new group
    )
    tx.Commit()
```

**Verified behavior:**
- Creates a fresh group capsule with the given id
- Moves the selection nodes INTO it
- Children paths become `<group_id>/<child_id>` (proves nodes are inside, not siblings)
- All inner connections of the selection are preserved
- External connections leaving the group are auto-promoted via new group ports

### `Ungroup(group)`

```python
with graph.BeginTransaction() as tx:
    id_remap = graph.Ungroup(group_node)
    tx.Commit()

# id_remap is list[(InternedId, InternedId)] — original_id → new_id_at_parent
```

**Verified behavior:**
- Dissolves the group, moving children to the parent
- Returns the id-rename mapping (children get @hash suffixes)
- External connections preserved + reattached to inner nodes

## Phase-3 v8 strategy: artist-extended capsule body via add-then-MoveToGroup

### Detection (in capture)
A capsule has artist-extended body iff its descriptor contains depth+1 children that are NOT auto-spawned by the maxon template's default. We already classify these — they're the ones currently failing AddChild with "GraphNode has no AddChild" or "unknown_asset" inside a capsule scope.

### Build sequence (in rebuild)
For each artist-extended capsule:

1. **DON'T AddChild the wrapping capsule** (it would only get the auto-spawn body)
2. **AddChild every body node at root** using their bare basenames
3. **Wire body inter-connections** at root scope (still resolves correctly since they're all siblings)
4. **MoveToGroup the body nodes** with `groupId = original_capsule_id`
5. **Map descriptor wires** that reference `<original_capsule>/...` paths to land on the new group's promoted ports

### Caveat
The result is a generic group capsule, NOT necessarily the same maxon-shipped type (e.g. legacyobjectaccess). But it CONTAINS the same body nodes with the same wiring. Functionally equivalent for procedural output.

If the type matters for some downstream use (specific port topology), we can:
- AddChild the maxon capsule first (gets the typed ports)
- AddChild a sibling group with the body
- ReplaceChild swap once we crack ReplaceChild's signature

For Phase-3's "1-1 graph rebuild" goal, the generic-group path is sufficient.

## What's still NOT possible from Python

- **Inserting into an EXISTING capsule's interior** — MoveToGroup creates a NEW group; can't append to existing. Workaround: MoveToGroup with the existing capsule's children + new body together → builds replacement group → ReplaceChild swap (TBD).
- **Deeply-nested artist body** — if matrixop INSIDE legacyobjectaccess INSIDE Nodes Spline has its own artist body, we'd need recursive MoveToGroup at each depth. Doable but iterative.
- **Scene-local custom capsules with no public asset_id** — `splinechamfer`, `xform` etc. The v8 group path produces a functional equivalent but not the original asset.

## Expected v8 fidelity (projection)

| Scene | v7 fid | v8 projected | Reason |
|---|---|---|---|
| Recursive Subdivision Nodes Mesh | 100% | 100% | already complete |
| Recursive Subdivision Nodes Spline | 45% | ~85% | legacyobjectaccess body via MoveToGroup; splinechamfer still scene-local |
| Spiderweb_Tutorial_01 | 75% | ~95% | legacyobjectaccess body covered; spline custom capsule still gap |
| Match Size after_swap_92 | 72% | ~95% | group@... interior covered; xform still scene-local |

This pushes Phase-3 from "explains the gap" to "covers most of the gap."
