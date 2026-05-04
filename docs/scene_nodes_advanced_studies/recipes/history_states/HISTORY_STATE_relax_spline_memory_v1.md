# HISTORY_STATE_relax_spline_memory_v1

**Captured:** 2026-05-04 (Cinema 4D 2026.2.0)
**Source:** `Relax-Spline_01_Tutorial.c4d` / doc-level Scene Nodes graph / `memory@WUtnkL0zF92mHEKQPG0OQK`
**Status:** verified working on source (frame 0 → 60 viewport screenshots prove growth)
**Eligible for replay:** YES, with substrate-redirection step
**Snapshot:** [HISTORY_STATE_relax_spline_memory_v1.snapshot.json](HISTORY_STATE_relax_spline_memory_v1.snapshot.json) (36 nodes, 58 wires)

## Purpose

Capture the verified-working 36-node body of the Memory capsule that implements the relax-spline reaction-diffusion algorithm. This snapshot can be replayed into ANY fresh `net.maxon.node.memory` capsule in any scene to reproduce the same growth behavior — with the substrate references redirected to the user's chosen objects.

## What this snapshot contains

The Memory body's full inner graph that, given:
- `memory.initial._0` ← a seed spline (any small spline)
- `memory.in@PZCNe_CdIZik8QPd45CpgN` ← Float (POINT_SIZE — minimum spacing between points)
- `memory.in@N2BxabrJHlenAfqs10CPKM` ← Int (MAX_ITERATIONS — relax steps per frame)
- `memory.in@f7Z3Gg9CAx0qNKmkVrlByP` ← Float (RELAX_SIZE — repulsion radius)
- (substrate objects referenced inside via UUIDs that need redirection per scene)

…produces per frame:
- `memory.next._0` → an evolved spline that:
  - Repels its own points (relax/RD aesthetic)
  - Snaps onto a substrate surface (containment)
  - Grows by appending new points where spline gets too short
  - Erases points where spline doubles back too tightly

## Algorithm (decoded from snapshot)

```
Per frame:
  1. Read current spline state from memory.current._0
  2. EVALUATE the curve at uniform parameter samples (range/arithmetic chain)
  3. DECOMPOSE matrices to extract per-point positions
  4. For each point:
     - Find closest point on substrate surface #1 (closestpointonsurface@Xf2B...)
     - Compute distance to substrate
     - Snap to surface within max_distance=11
  5. Apply PUSHAPART with min_separation = POINT_SIZE × <some factor>, max_iter = MAX_ITERATIONS
  6. For each iter point:
     - Find closest point on substrate surface #2 (closestpointonsurface@NpAX...)
     - Re-snap to substrate (containment enforcement)
  7. ERASE points where compare<gt(dist, RELAX_SIZE)> = true (curve too far)
  8. APPEND new points where compare<lt(dist, 5)> = true (curve too short, grow)
  9. ASSEMBLE new spline from updated control points (bezier)
  10. Output → memory.next._0
```

The two `closestpointonsurface` calls are the dual-substrate containment — one for the Landscape (the surface points sit ON), one for the Collision/Extrude (the containment volume the points stay INSIDE).

## Substrate references (need redirection on apply)

The snapshot contains TWO `legacyobjectaccess` nodes with hardcoded baselistlink UUIDs:

| Inner node | UUID | Purpose |
|---|---|---|
| `legacyobjectaccess@ZcL8lEnGBdnvG_GjT233sN` | `2CF05D04-2262-1A05-E3E4-BA3300000000` | Substrate #1 (referenced by `closestpointonsurface@NpAX...`'s `object` port) |
| `legacyobjectaccess@deXihN6HORTkBqbNpk8I7H` | `58FA7DAD-9D35-45E1-A317-A1573D24D0A8` | Substrate #2 (verify usage during apply) |

**On apply:** redirect both UUIDs to the user's substrate objects (typically: the surface to grow ON, and the containment volume to grow WITHIN).

## IO contract (host capsule outer ports)

| Port | Type | Direction | Required | Notes |
|---|---|---|---|---|
| `initial._0` | geometry | input | YES | seed spline (any small spline) |
| `current._0` | geometry | input (self-feedback) | YES | wired by stock memory scaffold |
| `in@PZCNe_CdIZik8QPd45CpgN` | Float | input | YES | POINT_SIZE (default 8.4) |
| `in@N2BxabrJHlenAfqs10CPKM` | Int | input | YES | MAX_ITERATIONS (default 15) |
| `in@f7Z3Gg9CAx0qNKmkVrlByP` | Float | input | YES | RELAX_SIZE (default 8) |
| `next._0` | geometry | output | — | evolved spline (use this for visualization) |
| `nextout._0` | geometry | output | — | mirror of next._0 |

## Safe-to-edit on apply

- All 3 outer params (POINT_SIZE / MAX_ITERATIONS / RELAX_SIZE) are exposed via memory's outer in@ ports
- Substrate UUID redirection (the 2 inner legacyobjectaccess nodes)
- `closestpointonsurface@NpAX...maxdistance` defaults to 11 — change for tighter/looser surface snap
- `pushapart.strengthin = 1` — change for softer/harder repulsion
- `assembler.splinetypein = bezier` — change to linear/etc. for different output curve type
- `assembler.curveclosedin = false` — change to true for closed-loop spline

## NOT safe to edit

- The 58 inner wires (any rewire breaks the algorithm)
- Inner port hashes (instance_hash@xxx must match snapshot when replaying)
- The `compare` thresholds (5 = "too short", `RELAX_SIZE` = "too long") — these are tuned to the relax aesthetic

## Replay function (TODO — to be implemented)

```python
def apply_history_state_relax_spline_memory(
    target_memory_node,    # the FRESH stock memory capsule to populate
    substrate_obj_1,       # OM object — the surface to grow ON
    substrate_obj_2,       # OM object — the containment volume to grow WITHIN
    point_size=8.4,
    max_iter=15,
    relax_size=8.0,
):
    """
    Replay HISTORY_STATE_relax_spline_memory_v1 inside target_memory_node.
    Loads the snapshot JSON, recreates each inner node, configures every port,
    wires every connection, and redirects the 2 legacyobjectaccess UUIDs to
    substrate_obj_1 and substrate_obj_2.

    Returns: True on success, raises on failure.
    """
    import json
    SNAPSHOT_PATH = "<path>/HISTORY_STATE_relax_spline_memory_v1.snapshot.json"
    with open(SNAPSHOT_PATH) as f:
        snap = json.load(f)

    NODESPACE = maxon.Id("net.maxon.neutron.nodespace")
    # Get a writable view INSIDE the target memory capsule
    parent_graph = target_memory_node.GetGraph()  # ... or sn_hook.GetNimbusRef.GetGraph
    mem_view = parent_graph.CreateView(maxon.NODE_KIND.NODE, target_memory_node.GetPath())

    # Pass 1: add all 36 nodes (asset = basename, label = instance_hash for collision-free)
    with mem_view.BeginTransaction() as tx:
        for n_spec in snap["nodes"]:
            asset_id = maxon.Id(BASENAME_TO_ASSETID[n_spec["basename"]])
            mem_view.AddChild(maxon.Id(n_spec["instance_hash"]), asset_id)
        tx.Commit()

    # Pass 2: configure all port values + redirect substrate UUIDs
    with mem_view.BeginTransaction() as tx:
        for n_spec in snap["nodes"]:
            node = _find_inner(mem_view, n_spec["instance_hash"])
            for inp_spec in n_spec["inputs"]:
                if inp_spec["sources"]:  # has wire — skip value config
                    continue
                # Substrate UUID redirection
                if n_spec["basename"] == "legacyobjectaccess" and inp_spec["port"] == "baselistlink":
                    if n_spec["instance_hash"] == "ZcL8lEnGBdnvG_GjT233sN":
                        new_uuid = _get_uuid(substrate_obj_1)
                    else:
                        new_uuid = _get_uuid(substrate_obj_2)
                    _in(node, "baselistlink").SetPortValue(maxon.String(new_uuid))
                # Outer-param overrides
                # ... etc
                else:
                    _set_port_from_snapshot_value(node, inp_spec["port"], inp_spec["value"])
        tx.Commit()

    # Pass 3: wire all 58 connections
    with mem_view.BeginTransaction() as tx:
        for n_spec in snap["nodes"]:
            target_node = _find_inner(mem_view, n_spec["instance_hash"])
            for inp_spec in n_spec["inputs"]:
                for src_spec in inp_spec["sources"]:
                    if src_spec["src_basename"] == "memory":
                        # source is the OUTER memory node's port (current._0, in@xxx)
                        src_port = _outer_port(target_memory_node, src_spec["src_port"])
                    else:
                        src_node = _find_inner(mem_view, src_spec["src_hash"])
                        src_port = _out(src_node, src_spec["src_port"])
                    src_port.Connect(_in(target_node, inp_spec["port"]))
        tx.Commit()

    return True
```

(Replay function is sketched — full implementation pending. Requires `BASENAME_TO_ASSETID` lookup table built from another verification pass.)

## Verification (after applying)

1. Set time to frame 0 — viewport should show seed spline only
2. Sequentially step 1..60
3. At frame 60, viewport should show the relax-spline maze pattern filled into the substrate's containment shape
4. Capture screenshot — file size should be ~2× larger than frame 0 (indicates more pixels with growth)

## Cross-references

- Parent recipe: [SN_RECIPE_contained_rd_spline_growth.md](../SN_RECIPE_contained_rd_spline_growth.md)
- Doctrine: [feedback_history_state_snapshot_doctrine.md](memory)
- Next-version candidate: `HISTORY_STATE_relax_spline_memory_v2` — when we generalize substrate-references to FloatingIO ports instead of hardcoded UUIDs, eliminating the redirection step on apply
