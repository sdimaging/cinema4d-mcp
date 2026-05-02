# Phase-3 Rebuild Results — Recursive Subdivision Tut 01

**Date:** 2026-05-02
**Script:** `scripts/sn_phase3_rebuild.py` v4 (RS-filter + auto-spawn detection + atlas-corrected ids)
**Source:** `Recursive_Subdivision_Tut_01.c4d` (2 SN hosts)

## Result summary

| Host | Type | Nodes | Wires | Capsules | Node fidelity | Wire fidelity | Errors |
|---|---|---|---|---|---|---|---|
| **Nodes Mesh** | SN Generator (180420700) | 14 | 11 | 3 | **100%** | **100%** | 0 |
| Nodes Spline | SN Generator (180420700) | 31 | 71 | 5 | 45% | 15% | 17 |

## What worked — Nodes Mesh full reproduction

A complete artist-authored Scene Nodes graph rebuilt from JSON descriptor:
- 9 nodes AddChild'd from canonical asset_ids
- 5 nodes auto-spawned as capsule body (start/end markers + assembler.coreNode + get_property.get + legacyobjectaccess.baselistparameter)
- 9 port defaults applied (operation, datatype, accessortype, etc.)
- All 11 wires connected — including 4 that route through capsule interiors

This is the **first end-to-end Phase-3 success** on a real DRuckli scene.

## Why Nodes Spline only got 45%

Two distinct architectural limits surfaced:

### Limit 1: scene-local custom capsules
- `splinechamfer@Fg$$wgMvAJ4m0kR_RzTO00` — a custom capsule defined inline in the .c4d, not from the maxon asset DB. Has interior `chamfernode` + `selectionstringtoselection` body — none of those are public asset_ids.
- These can't be AddChild'd; they need a "clone capsule body from source graph" pathway.

### Limit 2: artist-extended capsule body
- `legacyobjectaccess` is a maxon-shipped capsule, but the artist added body nodes inside it (`matrixop`, `objectimport`, `multransform_5`, `combine`, `mat`, `sqrpart`, `vectrans`, `sqrtrans`).
- Python's `GraphNode` has NO `AddChild` method — capsule interiors can ONLY be modified via the parent template auto-spawn. Confirmed by error message:

  > `'GraphNode' object has no attribute 'AddChild'`

- Maxon's empty capsule template auto-spawns SOME body (the framework markers + a few defaults), but the artist's customizations are not reproducible.

## Asset-id discoveries this session

Verified via runtime probing + atlas lookup:

| Basename | Asset ID | Source |
|---|---|---|
| `range` | `net.maxon.neutron.node.range` | atlas (was wrong namespace) |
| `get` | `net.maxon.neutron.geometry.get` | atlas |
| `objectimport` | `net.maxon.nbo.node.legacyobjectimport` | label probe ("Object Import") |

Plus the negative confirmation: `spline`, `matrixop`, `multransform_5`, `combine`, `mat`, `sqrpart`, `vectrans`, `sqrtrans`, `coreNode`, `rot`, `_0`, `_1`, `chamfernode` are NOT publicly addable assets — they exist only as capsule-interior body.

## Methodology gates

For Phase-3 to hit 100% on a graph, that graph must:
1. Use only standard atlas-resolvable nodes at top level
2. Use only maxon-shipped capsules whose default-template body matches what the artist used (no manual body modifications)
3. Have wires that don't route to artist-specific custom capsule slots

Nodes Mesh meets all three; Nodes Spline fails on #2 (legacyobjectaccess body) and on a custom splinechamfer (fails #1 because it's scene-local).

## Implications for the 30-scene grand vision

Most DRuckli scenes will land somewhere between 45% and 100% on Phase-3 v4:
- Pure-procedural scenes (math+iteration+geometry, like Nodes Mesh here) → 100%
- Scenes with custom capsules or artist-extended body → partial (45-80%)
- We cannot fully cover artist-extended bodies until we either:
  - (a) Build a "clone interior" path that reads source GraphNode children, serializes them, and re-creates them via the same auto-spawn API the source uses
  - (b) Find the C++/maxon API that bypasses Python's GraphNode.AddChild block
  - (c) Use the in-place parallel replacement methodology INSIDE capsule interiors as a separate pass

Path (c) is most actionable next — opens the source capsule interior, mirrors body nodes alongside originals, swaps wires, deletes originals (the same atomic transaction model proven for top-level swap in the Match Size work).
