# Scene Nodes Recipe Library

**Doctrine:** see `feedback_sn_recipe_library_doctrine.md` in user memory. TL;DR: recipes are valid acceleration only after from-scratch proof. Author Mode for learning, Assemble Mode for production speed using these verified entries.

## Authority hierarchy

- **`.md` files in this folder** = source of truth (provenance, asset IDs, port configs, wire list, IO contract, safe params, failure modes, MCP regeneration script, verification test).
- **`SN_Recipe_Library.c4d`** = rapid-assembly cache (live grouped graphs, drag-into-scene, copy-paste source).
- The `.md` script can REGENERATE the `.c4d` if it's lost or corrupted. The `.c4d` is the cached prefab; the `.md` is authoritative.

## Recipe entries

| File | Status | Provenance | Assemble Mode |
|---|---|---|---|
| [SN_RECIPE_buv_pathb_uv_position.md](SN_RECIPE_buv_pathb_uv_position.md) | Verified + Assembly-tested 2026-05-04 | T1 PathB (2026-05-03) | ✓ eligible |
| [SN_RECIPE_centered_uv_toggle.md](SN_RECIPE_centered_uv_toggle.md) | Verified | T2 Centered (2026-05-04) | ✓ eligible |
| [SN_RECIPE_wire_remove_swap_pattern.md](SN_RECIPE_wire_remove_swap_pattern.md) | Verified | technique entry (2026-05-04) | ✓ technique |
| [SN_RECIPE_contained_rd_spline_growth.md](SN_RECIPE_contained_rd_spline_growth.md) | **In progress** — outer chain mapped, Memory body algorithm documented, full rebuild pending | Scene 17 study + 2026-05-04 forensic walk | ⚠ outer-chain only for now |

## Assembly test results

| Test date | Recipe | Result | Notes |
|---|---|---|---|
| 2026-05-04 | SN_RECIPE_buv_pathb_uv_position v1.0 | PASSED | Regenerated from .md script on a fresh 3×3 plane (different from the library's 2×2 demo). Got 36 verts (= 9 polys × 4 corners), rad=(25, 25, 0), mp=(25, 25, 0) — bit-perfect math. |

## Each recipe entry contains

1. **Provenance** — discovery date, proof scene, verification commit/screenshot, last-tested-on
2. **Required asset IDs** — canonical node template strings
3. **Port configurations** — every SetPortValue with type
4. **Wire list** — every Connect
5. **IO contract** — what comes in, what goes out
6. **Safe-to-edit params** — what an artist/Claude can change
7. **Not-safe-to-edit** — what depends on internal wiring
8. **Known failure modes** — common breakage patterns
9. **MCP authoring script** — Python that regenerates from scratch
10. **Minimal verification test** — quick check after assembly

## Workflow

### Author Mode (creating a new recipe)
1. Build from scratch via MCP, no copy
2. Verify structurally + visually + numerically
3. Write the `.md` entry with all 10 sections
4. Add a grouped instance to `SN_Recipe_Library.c4d`
5. Run assembly test on a fresh scene to validate

### Assemble Mode (using a verified recipe)
1. Open `SN_Recipe_Library.c4d`
2. Copy the recipe's grouped Null
3. Paste into your working scene
4. Decimate demo elements (the .md notes which)
5. Rewire root-gateway connections to your scene's input
6. Adjust safe-editable params
7. Run the recipe's verification test
