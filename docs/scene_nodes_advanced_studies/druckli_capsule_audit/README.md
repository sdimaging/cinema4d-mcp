# DRuckli Capsule Audit — 19 Scenes / 38 SN Hosts / 5,773 Nodes

**Date:** 2026-05-03
**Source:** Currently-open scenes from the DRuckli asset library in C4D
**Methodology:** Per-scene tree dump → SN host descriptor capture → viewport capture → study writeup.
**Snapshot for crash recovery:** [`_snapshots_t0/`](_snapshots_t0/) — 19 .c4d files (~12MB total)

## Headline numbers

- **19 scenes audited** — full set of currently-open DRuckli capsule examples
- **38 unique SN/Distribution hosts captured**
- **~5,773 total nodes** across all hosts
- **~9,200 total wires**
- **3 priority deep-dives** with full study.md (Spenser-called-out scenes)
- **2 consolidated batch studies** for the remaining 15

## ⚡ The 3 priority deep-dives

### 1. UV-Polygon-Info — the 3D ↔ flat slider unlock ([study](03_uv_polygon_info/study.md))

The scene Spenser asked: "would it be possible to have an animated slider to drag and slide from 3D to flat and would it be possible to add that same object surface from flat to 3D?"

**Answer: YES, and the recipe is documented.** The `uvtomesh` SN node already converts UV→flat-3D. Adding a `mix(orig_pos, flat_pos, factor)` node + a floatingio for the factor exposes a 0-1 morph slider. Reverse direction (flat→arbitrary 3D surface like the Python script) needs additional `nearestneighbor` or raycast onto a target — also feasible with existing SN vocabulary.

### 2. Surface Stippling solver (Basics-Noise) — the canonical Lloyd's relaxation ([study](13_surface_stippling_basics_noise/study.md))

The scene that "pushes apart on play" — confirmed Lloyd's relaxation / poisson-disk in SN. **294 nodes / 426 wires / 19 capsules** with `loopcarriedvalue` carrying point positions across frames + `closestpointonsurface` constraining to surface + `polyvoxel` spatial hashing for neighbor queries. 10 floatingio AM params expose the artist controls. Frame captures show the visual progression from clumpy noise (t=0) through mid-relaxation (t=40) to organized stipple (t=80).

### 3. Paint Strokes Distribution Bust — the painterly still-life pipeline ([study](04_paint_strokes_bust/study.md))

The "beautifully creative" vertex-map spline-flow Spenser called out. **Paint Strokes Distribution alone is 514 nodes / 812 wires / 75 capsules.** Combines:
- Image-texture sampling per stroke (`image` node)
- Direction-following (`align` node — orients strokes to flow-spline tangent)
- Color variation along stroke (`gradient` × 2)
- Density modulation by vertex-map (`get_property` reading vertex colors)
- New C4D 2026 `composedistributioncontainer` → Cloner-compatible output

The scene is a **production painterly rendering pipeline** with bust + apple + orange + curved BG, each with its own Flow Spline + Paint Strokes Distribution + Strokes Cloner + camera-aware Backdrop SN.

## Batch studies

| Doc | Coverage |
|---|---|
| [_BATCH_1_STIPPLING_AND_VISUALIZERS.md](_BATCH_1_STIPPLING_AND_VISUALIZERS.md) | 4 scenes: Stippling Distribution HighAmount + Portrait + Spiky-Head + Visualizer Examples (selection visualizers, color/normal vector overlays) |
| [_BATCH_2_REMAINING_SCENES.md](_BATCH_2_REMAINING_SCENES.md) | 11 scenes: Spline Smooth, Spiderweb Example, Image Subdivider, Match Size Books, RD Sphere, RD Simple2, Ray Connector, Shortest Path, Splint Spline Mask, Closest Point on Spline, Field VertexMap |

## Per-scene index

| # | Scene | SN Hosts | Total Nodes | Folder |
|---|---|:---:|---:|---|
| 00 | Spline_Smooth_Examples | 3 | 265 | [`00_spline_smooth/`](00_spline_smooth/) |
| 01 | Spiderweb_Example_01 (3 config variants of same 202-node graph) | 3 | 606 | [`01_spiderweb_example/`](01_spiderweb_example/) |
| 02 | Image_Subdivider_Example_01 | 6 | **684** | [`02_image_subdivider/`](02_image_subdivider/) |
| 03 | **UV-Polygon-Info_Example_01** ⭐ | 2 | 145 | [`03_uv_polygon_info/`](03_uv_polygon_info/) |
| 04 | **Paint_Strokes_Distribution_Example-Bust_01** ⭐ | 6 | **905** | [`04_paint_strokes_bust/`](04_paint_strokes_bust/) |
| 05 | Match_Size_Books | 2 | 362 | [`05_match_size_books/`](05_match_size_books/) |
| 06 | Reaction_Diffusion_Example_Sphere | 1 | 176 | [`06_rd_sphere/`](06_rd_sphere/) |
| 07 | Reaction_Diffusion_Example_Simple2 | 2 | 604 | [`07_rd_simple2/`](07_rd_simple2/) |
| 08 | Ray_Connector_Example-Mograph_01 | 3 | 145 | [`08_ray_connector/`](08_ray_connector/) |
| 09 | Shortest_Path_Example_02 | 3 | 137 | [`09_shortest_path/`](09_shortest_path/) |
| 10 | Surface_Stippling_Distribution_HighAmount_01 | 1 | 187 | [`10_stippling_dist_highamount/`](10_stippling_dist_highamount/) |
| 11 | Splint_Spline_Mask_Example_01 | 1 | 243 | [`11_splint_spline_mask/`](11_splint_spline_mask/) |
| 12 | Closest_Point_on_Spline_Example_Advanced_02 | 4 | **759** | [`12_closest_point_spline/`](12_closest_point_spline/) |
| 13 | **Surface_Stippling_Example_Basics-Noise** ⭐ (the SOLVER) | 1 | 294 | [`13_surface_stippling_basics_noise/`](13_surface_stippling_basics_noise/) |
| 14 | Field_VertexMap_Example_01 (BIGGEST single host: 1278n / 155c) | 1 | **1278** | [`14_field_vmap/`](14_field_vmap/) |
| 15 | Surface_Stippling_Example_Spiky-Head | 3 | 330 | [`15_stippling_spiky_head/`](15_stippling_spiky_head/) |
| 16 | Surface_Stippling_Distribution_Portrait_01 | 1 | 187 | [`16_stippling_dist_portrait/`](16_stippling_dist_portrait/) |
| 17 | Visualizer_Examples_01 | 8 | **1003** | [`17_visualizers/`](17_visualizers/) |

## Spenser's "use as-is vs rebuild" decisions

Per Spenser's instruction:

**LOCK AS SHORTCUTS — use as-is** (don't rebuild every time):
- All 4 Selection Visualizers (Points, Edges, Polys, Color-as-Vector)
- Match Size (covered in scenes 5, 21-25)
- Surface Stippling Distribution variants (any new "stipple my surface" task)
- Spiderweb Standard / Tunnel / Umbrella (production radial generators)
- Linear Arrow (any procedural arrow work)
- Image Subdivider (architectural/urban styling)
- Split Spline Mask (the only spline-boolean tool in the library)
- Trim Spline Modifier (animated spline reveals)
- Field Vertex Map Debug Capsule — REFERENCE, don't rebuild

**WORTH REBUILDING (or extending)**:
- UV-Polygon-Info → extend with the 3D↔flat slider unlock (recipe in study)
- Surface Stippling solver — to validate Phase-3 v9.2 fidelity at 19-capsule complexity (similar size to Mycelium V3 = expected 100% nodes / 90-95% wires)

## New SN vocabulary discovered in this audit

Atlas-worthy nodes encountered for the first time:

| Node | Where | What |
|---|---|---|
| `uvtomesh` | UV-Polygon-Info | UV → flat-3D conversion (the slider engine) |
| `closestpointonsurface` | Surface Stippling solver | snap point to nearest position on surface (the "stay on surface" constraint) |
| `polyvoxel` | Surface Stippling solver | voxel-grid spatial hash (for fast neighbor queries) |
| `composedistributioncontainer` | Paint Strokes | the new C4D 2026 distribution-container output |
| `image` | Paint Strokes | image-texture sampling node |
| `align` | Paint Strokes | aligns input vector to target direction (THE flow-following primitive) |
| `gradient` | Paint Strokes, Spline_Grower | gradient sampler |
| `layer` | Paint Strokes | layered output composition |
| `dot` | Paint Strokes | vector dot product |
| `getpropertynames` | Visualizers | dynamic attribute name lookup |
| `readvalueatindex2` | Visualizers, Paint Strokes (6×) | newer/specialized array read variant — worth investigating vs `readvalueatindex` |
| `extrude` (modeling-command capsule) | Visualizers | full SN-wrapped Extrude modeling command |
| `split` (modeling capsule) | Visualizers | SN-wrapped Split |
| `connect_geometries` (deeper than expected) | Visualizers | now confirmed as the canonical "merge multiple geometries" primitive |
| `Field Vertex Map Debug Capsule` (1278 nodes) | Field_VertexMap | the field-to-vmap reference rig |

## Suggested next steps

1. **Run Phase-3 v9.2 sweep** on these 19 scenes / 38 hosts — predict 100% node fidelity across the board (this script has handled 32+ hosts already with that result). Particularly stress-test on Field Vertex Map Debug Capsule (1278 nodes) — would set a new "largest reproduced" record.
2. **Build the 3D↔flat slider extension** of UV-Polygon-Info as a teaching pack contribution.
3. **Add new vocabulary** to `scene_nodes_atlas`: `uvtomesh`, `closestpointonsurface`, `polyvoxel`, `composedistributioncontainer`, `image`, `align`, `gradient`, `layer`, `dot`, `getpropertynames`, `readvalueatindex2`.
4. **Catalog the "use as-is" capsules** in a separate doc as the recommended DRuckli-shortcut set for future C4D work — Match Size, Selection Visualizers, Surface Stippling family, Spiderweb family, Image Subdivider, Split Spline Mask, Trim Spline Modifier.

## Crash recovery

If a scene gets lost or corrupted, reload from [`_snapshots_t0/`](_snapshots_t0/) — taken before the audit started, all 19 scenes saved as fresh .c4d files.
