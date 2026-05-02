# Phase-3 Rebuild Results — Match Size (Basic Settings, atomic-swap snapshot)

**Date:** 2026-05-02
**Script:** `scripts/sn_phase3_rebuild.py` v7 (RS-filter + auto-spawn + MY_-prefix + atlas-corrected ids)
**Source:** `_snapshots/after_swap_92_atomic.c4d` — final state of in-place parallel replacement (92/92 swapped + 27 deferred wrappers)

## Why test on the swap snapshot

This file is the canonical proof of "we can produce these nodes via Python" — every `MY_<type>_<hash>_swap` is one of our atomic-swap copies. If Phase-3 from-scratch can't reproduce nodes that the swap proved producible, that's a meaningful methodology gap.

## Result evolution

| Iter | Node fid | Wire fid | What changed |
|---|---|---|---|
| v3 baseline (Spiderweb script) | 21% | 7% | Naive basename split fails on MY_ ids |
| v5 (MY_-prefix rfind) | 64.5% | 30.9% | Last-underscore split — but hash can contain underscores |
| v6 (whitelist longest-prefix) | 71.9% | 38.1% | Disambiguates MY_floatingio_RK_Sp8 vs MY_arithmetic_X |
| v7 (added tessellation/edgetoline/nside to map) | **71.9%** | **38.1%** | No change — those errors hit GraphNode-no-AddChild ceiling |

## What 71.9% means

- **115 nodes successfully AddChild'd** from atlas asset_ids (top-level)
- **31 nodes auto-spawned** as capsule interior body (start/end markers + framework children)
- **132 port defaults applied** (operation, datatype, accessortype, type-determinants)
- **101 of 265 wires connected**

## Remaining 57 errors — three buckets

### Bucket A: scene-local custom capsules (3 nodes)
- `MY_xform_dty_swap` — custom capsule WE built during the in-place swap experiments
- `legacyobjectaccess/matrixop` — artist-extended body inside a maxon-shipped capsule
- `group/sweepline` — scene-local pattern

### Bucket B: parent_capsule_missing cascade (2 nodes)
- `MY_xform_dty_swap/transformpoint`, `/selectionstringparser` — body of the failed xform capsule

### Bucket C: GraphNode-no-AddChild architectural block (5 nodes)
- `group/tessellation` × 2
- `group/edgetoline`
- `group/cube`
- `group/nside`

These are inside an artist-built `group@` capsule. We KNOW these asset_ids — they're correctly resolved in DEFAULT_ASSET_MAP — but Python's `GraphNode` has no `AddChild` method:

> `'GraphNode' object has no attribute 'AddChild'`

The graph-level `graph.AddChild()` only works at root scope. Capsule interiors can ONLY be modified via:
1. The parent template's auto-spawn behavior (e.g. start/end markers, default body)
2. A C++ MCP path that bypasses Python (cinema4d-mcp's `scene_nodes_add_node` has the same limit — uses ApplyDescription at graph level, not interior)

## Comparison across the 3 scenes attempted with v7

| Scene | Host | Source nodes | Node fid | Wire fid | Notes |
|---|---|---|---|---|---|
| Recursive Subdivision | Nodes Mesh | 14 | **100%** | **100%** | Clean — no custom capsules, no artist body extensions |
| Recursive Subdivision | Nodes Spline | 31 | 45% | 15% | Has custom `splinechamfer` capsule + legacyobjectaccess body |
| Spiderweb_Tutorial_01 | Nodes Spline | 63 | 74.6% | 38.8% | Custom `spline` capsule + legacyobjectaccess body |
| Match Size (swap snapshot) | Match Size | 203 | 71.9% | 38.1% | Custom `xform` capsule + group body + legacyobjectaccess body |

**Pattern:** scenes without custom capsules or artist-extended capsule body hit 100%. Scenes with either limit hit 45-75% and the gap is ALWAYS exactly the artist-customized capsule interior content.

## The unblock paths (in order of effort)

### Path 1: clone-from-source capsule body (medium effort, no new C++)
After top-level Phase-3 completes, walk every capsule that auto-spawned interior different from the source's interior. For each missing interior node, walk the SOURCE capsule's matching GraphNode children and copy their graph subtree into the target capsule via... 

Actually this hits the same Python wall: there's no Python API to insert into a GraphNode. We'd need a deep-copy at the maxon level (NodesGraphModelRef.GetGraph() with cross-graph BeginTransaction binding).

### Path 2: extend cinema4d-mcp C++ helper (high effort, definitive solution)
Add a new helper command to `cpp_shim/cinema4d_mcp_helper` that takes (target_capsule_node_id, child_descriptor[]) and uses internal C++ maxon APIs to insert nodes inside a capsule's interior. This bypasses the Python GraphNode block since C++ has access to the full capsule model.

### Path 3: in-place parallel replacement INSIDE capsule interior (proven elsewhere)
Instead of from-scratch rebuild, extend our atomic-swap C++ command to descend into capsule interiors. We already have the swap mechanic working at top level (proven 94/94 on Stone Circle). Extending it to capsule scope reuses the same transaction model.

## Recommended next step

Path 3 is most actionable — it builds on the proven atomic-swap C++ work. The plumbing already exists for top-level swap; descending into capsule scope is a localized extension. This unlocks artist-customized capsule body for ALL future Phase-3 attempts.

Path 1 is the "no new C++" option but blocked by the same Python GraphNode wall.

Path 2 is correct architecturally but requires substantial C++ helper extension.
