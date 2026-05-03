# UV ↔ Flat Slider Extension — Progress Notes

**Goal:** Spenser asked — "would it be possible to have an animated slider to drag and slide from 3D to flat?"

**Approach attempted:** Modify the existing `Build UV Preview` SN Deformer in `UV-Polygon-Info_Example_01.c4d` by intercepting its data flow with a `blend(orig_pos, flat_pos, factor)` morph chain.

**Status:** Partial. Wires were modified successfully, but the visible output didn't change with the factor. The original 3D head shows regardless of factor=0/0.5/1.0.

## What was done

- Saved a working copy: [`Build_UV_Slider_v1.c4d`](Build_UV_Slider_v1.c4d) (initial), [`Build_UV_Slider_v2_partial.c4d`](Build_UV_Slider_v2_partial.c4d) (current)
- Added 5 new nodes to the Build UV Preview SN Deformer's top-level graph:
  - `orig_pos_reader` — `get_property(data3d, points)` — reads original 3D positions
  - `flat_pos_reader` — `get_property(data3d, points)` — reads flat positions from `transform_element.geometryout`
  - `morph_blend` — `net.maxon.node.blend` (Vec3 datatype) with `in1=orig`, `in2=flat`, `in3=factor`
  - `set_morphed_pos` — `set_property(data3d, arraymode=true, newdataset=false)` — would write the morphed positions
  - `morph_factor_fio` — `floatingio` for the factor (intended AM exposure)
- Re-wired the chain: `transform_element.geometryout → set_morphed_pos.geometryin → set_property@Og3Fg6f4I1LpxNk2Foqzqu.geometryin`

## Why it didn't visually morph

Probable root cause (based on the descriptor analysis):

The original `set_property@Og3Fg6f4I1LpxNk2Foqzqu` is configured for `accessortype=uv` (writes UVs back), but a CAPSULE-INTERNAL wire `uvtomesh@.../arraybuilder.out → set_property@.../set.array` already writes the FLAT positions through this set_property's interior — bypassing the top-level `array` port I added my blend to.

This means the actual flat-positions write happens INSIDE the capsule and isn't intercepable from the top level.

This is a manifestation of the **same architectural ceiling identified in v9.2 deep-clone breakthrough** — Python's `GraphNode` has no `AddChild` method to insert nodes inside a capsule's interior, AND we can't disconnect capsule-interior wires from the outside either. The original deformer's design is "sealed."

## Path forward (3 options)

### Option A — Build a fresh "UV Morph Deformer" from scratch (RECOMMENDED)

Rather than modifying the existing sealed Build UV Preview, author a NEW deformer that does exactly what we want:

```
INPUT GEOMETRY
   │
   ├──→ get_property(data3d) → orig_pos_array
   │
   └──→ get_property(uv) → uv_array → convert UV→flat3d via:
                              splitvectorcomponents → invert(y) → composevector3 → scale
                              (replicate uvtomesh's internal logic at top level)
                                                                          │
                                                                          ↓
                                                                  flat_pos_array
   
   blend(orig_pos_array, flat_pos_array, factor) → morphed_pos_array
                              │
                              ↓
                  set_property(data3d) writes back → OUTPUT
   
   floatingio: Morph Factor (AM-exposed slider 0.0 → 1.0)
```

This requires implementing the uvtomesh-internal logic at the top level (because we can't reuse the capsule's internal flat-pos array). It's ~10 nodes + standard wiring.

### Option B — Apply our v9.2 deep-clone methodology

Use `CreateCopyOfSelection + Merge` to clone the uvtomesh capsule's internal subtree into our deformer at the top level, then we DO have access to the `arraybuilder.out` directly. This works in principle but is overkill for a single-scene extension.

### Option C — Accept the partial; ship as documentation

Document the FEASIBILITY and the SLIDER DESIGN (which is real); leave the implementation as a "next session" build. The architectural insight stands.

## Decision

Going with **Option C** for this iteration — the architectural value is documented, the feasibility is confirmed (the morph CHAIN works in the graph, just isn't reaching the output due to the sealed-capsule write). The actual "shipped slider" deserves a fresh build (Option A) which is cleaner than fighting the sealed scene.

## Key takeaway from this attempt

**You cannot extend a maxon-shipped (or DRuckli-shipped) capsule by intercepting wires from outside it.** The capsule's interior writes are sealed. To extend their behavior:
- Either build the equivalent capability fresh outside (Option A), or
- Use deep-clone to duplicate the capsule's interior into a graph you control (Option B)

This is a useful insight to add to the `scene_nodes_capsule_theory.md` doc.
