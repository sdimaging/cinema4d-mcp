# RESUME HERE — Scene 23 (Match Size Stone Circle)

**Status: ✅ Match Size SN deformer (94 swappable nodes) replicated 1-1 via single C++ bulk_swap call.**
**Last update:** 2026-05-02
**Foundation:** C++ bulk_swap commits `45044d7` (atomic remove+rewire + owner-id fix) on whitelabel-public-release branch.

## What's done

1. **Scene composition study** — see `study.md`. Decoded:
   - OM architecture (Cube/Match Size + Null/Connect/{Cloner,Stack Stones,Match Size,Bend} sibling deformer stack)
   - Cloner uses C4D 2026 Advanced Distribution path (`MGCLONER_USE_DISTRIBUTION_CLONES=1`)
   - Stack Stones algorithm: 5-node loop_scaffold (explode_islands → erase → containeriteration → loopcarriedvalue accumulation)
   - Match Size: same 118-node asset as scene 21, identical histogram

2. **Asset_id discovery** — atlas hits for all 5 Stack Stones node types: `readvalueatindex`, `containeriteration`, `explode_islands`, `erase`, `loopcarriedvalue`. Full list in study.md.

3. **94-spec C++ bulk_swap** on Match Size on Cube:
   - 94/94 swapped, 87 wires mirrored, 75 wires rewired
   - 487ms wall (server: 483ms)
   - Net node count: 118 → 118 (94 MY23_ + 22 deferred OG + 2 context)
   - Other 2 SN deformers (Stack Stones + Match Size under Connect) untouched ✅
   - Snapshots: `_snapshots/before_swap.c4d`, `_snapshots/after_94_swap.c4d` (gitignored, local-only — derivative scenes)

## What's deferred

**Stack Stones replication** — needs C++ tool change. Currently the helper's `FindFirstObjectOfType` walks depth-first and always picks the first SN deformer (Match Size on Cube). For Stone Circle's THIRD SN deformer (Stack Stones, sibling of Cloner under Connect), need to add a `target_host_name` BC key + C++-side filter.

Once that lands, Stack Stones is a tiny 5-spec swap (asset_ids known, graph traced).

**Match Size #2 (sibling under Connect)** — same asset, would be a duplicate test. Skip unless we want a "swap two Match Size instances in one call" stress.

## Per-scene deferred set (22 OGs left after 94-spec swap)

Same classes as scene 21:
- 8 `scaffold@*` — UI organization, no functional role
- 2 `group@*` — UI grouping
- 2 `legacyobjectaccess@*` — wrapper capsules with NESTED sub-graphs
- 1 `delete@*` + 1 `cube@*` + 1 `transformmatrix@*` + 1 `type@*` — wrapper/no-asset-map
- 1 `invertselection@*` + 1 `active@*` + 1 `get_property@*` + 1 `getcount@*` — no-asset-map utility nodes
- 2 `if@*` — phantom-input deferred (gotcha #69 — chain-walks throw)

These need either (a) `transformmatrix`/`type` asset_ids added to ASSET_MAP (already in atlas — easy update) or (b) recursive sub-graph editing API for the wrappers.

## Replication evidence

| Criterion | Status |
|---|---|
| Match original node count | ✅ 118 → 118 |
| Match type histogram | ✅ 94 MY23_ basenames match swapped OG basenames |
| Match connection graph | ✅ 87 wires mirrored, 75 wires rewired (full topology) |
| Match exposed/user-facing parameters | ⚠️ Not verified (would need AM pass) |
| Match viewport output | ⚠️ Frames captured (`baseline_framed.png` + `after_94_swap.png`) but Cube parent disabled in editor — not a meaningful visual comparison without enabling parent first |
| Document deferred nodes with reasons | ✅ Above + study.md |

## Next moves (ordered)

1. **Add `target_host_name` to C++ bulk_swap tool.** Single BC key + filter switch in `FindFirstObjectOfType`. Build + sync + test. Unlocks Stack Stones + multi-deformer scenes.
2. **Stack Stones 5-spec replication.** Tiny target, fully decoded.
3. **Match Size #2 (under Connect) replication.** Same asset, prove repeat-call safety.
4. **Visual verification.** Re-enable Cube + capture proper viewport diff baseline → after.

## Operational notes

- **Source scene**: `C:\Users\Spenser Dickerson\Desktop\DRuckli\MatchSize_Tutorial-Files\Example_Stone-Circle.c4d` (proprietary tutorial material, not redistributed).
- **C++ tool stage**: `cxx_mutation_v3_full_swap`, protocol_version 8 (commit 45044d7).
- **MCP host**: 172.30.64.1:5555 from WSL (auto-detected gateway).
- **Plugin install**: `%APPDATA%/Maxon/Maxon Cinema 4D 2026_*/plugins/cinema4d-mcp/cinema4d_mcp_helper.xdl64` + `mcp_server_plugin.pyp`.
