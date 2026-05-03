# Manual Scene Nodes Morph Recipe — "Tiny Ugly Correct Graph"

**Status:** GPT-corrected framing (2026-05-02). Pure-SN morph IS feasible — the working uvtomesh chain in DRuckli's `Build UV Preview` deformer proves it. The only blocker we hit was Python authoring of capsule internals; manual authoring in the SN editor is fully available.

This doc is the step-by-step recipe to add a 3D ↔ flat morph slider to a CLONE of the existing `Build UV Preview` deformer by adding **3 nodes + 4 wires** in the SN editor.

## The "tiny ugly correct" doctrine (per GPT review)

> Don't generate a new giant graph. Start from the proven Druckli UV chain and add one blend node.

We modify the SHORTEST possible delta:
- 1 node added: a **blend** (vec3, mixes orig position with flat position by factor)
- 1 node added: a **get_property** (reads orig "Position" attribute alongside existing UV read)
- 1 node added: a **floatingio** (the artist-facing factor slider, 0-1)
- 4 wires changed: re-route the per-vertex math chain through the blend

## Pre-conditions

- The DRuckli scene `UV-Polygon-Info_Example_01.c4d` is loaded
- "Build UV Preview" is enabled and working (head shows flat unwrap when active)
- Granular reference dump available at `UVTOMESH_GRANULAR_REFERENCE.md` for context

## The CURRENT uvtomesh inner chain (what already works)

```
get(UV).array → containeriteration.in
containeriteration.out → splitvectorcomponents.vector
splitvectorcomponents.x → composevector3.x
splitvectorcomponents.y → invert.in
invert.out → composevector3.y
composevector3.result → scale.in1
scale.in2 = inport@PxTGkq...  (the existing capsule-exposed Float64 = current "scale" slider)
scale.out → set.iteration         ← THIS WIRE IS WHAT WE NEED TO INTERCEPT
get(UV).topology → set.topology
```

Where `scale.out` is the flat position per vertex (per iteration step). It currently goes directly to `set.iteration`.

## The target chain after our additions

```
... existing chain through scale.out ...
scale.out → blend.in2                 ← NEW: flat goes into blend's in2

get(orig Position).array → containeriteration_2 → blend.in1   ← NEW: orig pos
                                                                  per-vertex into blend.in1

floatingio(factor) → blend.in3        ← NEW: artist slider drives blend factor

blend.out → set.iteration             ← REWIRED: blend output instead of raw scale
```

When factor=0: blend.out = blend.in1 = orig 3D position → output looks like welded source mesh
When factor=1: blend.out = blend.in2 = scale.out = flat position → output looks like full UV unwrap
Factor 0→1: smooth interpolation per vertex, same topology throughout

## Step-by-step manual edits (in C4D's SN editor)

### Setup

1. **Select the `Build UV Preview` deformer** in the Object Manager.
2. **Open the SN editor** — Window → Scene Nodes (or via the deformer's "Open in Node Editor" right-click).
3. **Drill into uvtomesh** — double-click the `uvtomesh` capsule node. You should see its 9 inner nodes: `get`, `containeriteration`, `splitvectorcomponents`, `invert`, `composevector3`, `scale`, `set`, `geometry`, `filter`.
4. **Locate the wire `scale.out → set.iteration`** — this is the wire we'll redirect through our blend.

### Add the 3 new nodes

5. **Add `Get Property` node** (Asset Browser → search "Get Property"). Drop it near the existing `get(UV)`.
   - Configure: `accessortype = Position`, `accessorname = Position`, `componentin = points`
   - This reads the source mesh's original 3D positions per vertex.

6. **Add `Blend` node** (Asset Browser → search "Blend").
   - Configure: `datatype = vec<3,float>` (right-click datatype port → set vector type)
   - Position it between `scale` and `set` in the existing chain.

7. **Add `Floating IO` node** (Asset Browser → search "Floating IO" or "FIO").
   - This will become an artist-exposed slider on the capsule's outer ports.
   - Configure: name it `Factor`, default 0.0, range 0.0-1.0.

### Wire the 4 connections

8. **Disconnect** `scale.out → set.iteration` (left-click the wire, press Delete).
9. **Connect** `scale.out → blend.in2`
10. **Connect** the new `Get Property (orig Position)` chain into the existing `containeriteration`:
    - You can wire it through the SAME `containeriteration` if it accepts a second array input
    - OR add a second `containeriteration` node with the same `in` driven by `get_orig.array`, then wire its `.out → blend.in1`
    - The simpler path: feed `get_orig.array → blend.in1` directly if blend handles the array+iteration broadcast (test this first)
11. **Connect** `floatingio(Factor).out → blend.in3`
12. **Connect** `blend.out → set.iteration`

### Verify

13. **Drag the new Factor slider** in the AM (it should appear after the floatingio is exposed via uvtomesh's outer port — may need to right-click the floatingio → "Expose to AM").
14. Sweep factor 0 → 1:
    - 0.0: head should look like the welded 3D bust (rad ≈ 17.4×21.2×12.5)
    - 0.5: half-morphed, seams visibly opening
    - 1.0: fully flat (rad ≈ 24.82×24.32×0)

### Save as preset for studio reuse

15. Right-click the uvtomesh node → **Save Asset as Preset** (or similar). Name it `uv_morph_slider`.
16. Now anyone in the studio can drag `uv_morph_slider` from the asset browser onto a polygon mesh and get the working morph.

## Troubleshooting

- **Blend not broadcasting over arrays:** if `blend` doesn't handle array inputs (single-vec3 only, like `scale` does internally), wrap each arm in its own `containeriteration` so the per-vertex values are streamed properly.
- **floatingio not appearing in AM:** the capsule's outer port needs to expose it. Right-click the floatingio inside uvtomesh → "Add as Port" → it should appear on uvtomesh's external port list.
- **Factor 0 doesn't look exactly like 3D head:** check that `get_property(Position)` is reading the SAME source data the deformer's input geometry uses (not a transformed version).
- **Topology jumps at factor edges:** uvtomesh's output topology stays the same throughout (split UV islands), so f=0 will be the welded-looking version of that split mesh, identical visually to the source if all duplicate verts coincide.

## Why this works (where iter 1-12 didn't)

We're modifying an EXISTING working capsule's inner chain, not building one from scratch in Python:
- The op.geometry/op.filter wrappers are ALREADY in place (they're load-bearing for evaluation scope)
- The set.iteration mechanism is ALREADY proven to work because uvtomesh broadcasts correctly
- We just intercept ONE wire and add ONE blend → minimal delta to a known-good system

This is the "tiny ugly correct graph" doctrine in action.

## After this works

The same recipe applied to OTHER per-vertex morph use cases:
- 3D ↔ noise displacement (replace `scale.out` flat position with noise-displaced position)
- 3D ↔ projected texture position (replace with shader-driven position)
- Any "morph between two same-topology positions" with one slider

Each is a clone-and-modify of this same uvtomesh-with-blend pattern.
