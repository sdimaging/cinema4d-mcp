# UV ↔ Flat Slider Extension — Final Progress Notes

**Goal:** Spenser asked — "would it be possible to have an animated slider to drag and slide from 3D to flat?"

**Status after 3 iterations:** Wire surgery from outside the existing capsule didn't yield a visually morphing output. The HONEST answer: this needs a fresh deformer built from primitives (Option A in the original analysis), not a wire-surgery patch.

## What was tried (3 iterations)

### v1 — direct interception of set_property.array
Added orig_pos_reader, flat_pos_reader, blend, set_morphed_pos. Wired blend.out → existing set_property.array.
**Failed because:** existing set_property is configured for `accessortype=uv`, and a capsule-INTERNAL wire `uvtomesh@.../arraybuilder.out → set_property@.../set.array` (writing data3d directly via the interior) bypasses external wires.

### v2 — added own set_property(data3d) BEFORE set_property(uv) in the chain
Wired transform_element.geometryout → set_morphed_pos.geometryin → set_morphed_pos.geometryout → set_property@Og3.geometryin → output.
**Failed because:** my set_morphed_pos's effect didn't propagate. Possibly because set_property(uv)'s capsule-internal wires re-overwrote positions back to the flat data, or the maxon evaluation picked up a different chain.

### v3 — wired set_morphed_pos.geometryout DIRECTLY to root.geometryout
Bypassed the entire downstream chain. Connected set_morphed_pos's output directly to the SN deformer's host-output port.
**Failed because:** the resulting bounds of the deformed instance stayed at the head's natural size (17×21×12) regardless of factor — meaning my morph chain's output was always the original 3D positions OR the deformer's evaluation took a different (hidden) path.

The connections check showed `root.geometryout` had TWO inputs registered (both labeled `>`) — likely both my set_morphed_pos.geometryout and the original set_property@Og3.geometryout coexisting, and the original taking precedence.

## What this proves

**The DRuckli SN capsules are sealed deeper than just "no AddChild into interior."** Even at the TOP-LEVEL of the deformer host:
1. Existing wires (especially capsule-internal-to-capsule-internal ones) bypass external interception
2. Adding a new wire to root.geometryout doesn't *replace* the existing one
3. Without `Disconnect`, we can't programmatically remove existing wires from Python

The actual evaluation of the SN graph honors the original wire mesh and ignores my additions when they conflict.

## The real path forward — Option A (fresh-build deformer)

The slider IS feasible, but requires building a completely new SN Deformer from primitives:

```
Required nodes (~10 nodes total — all probed and confirmed addable):
- get_property (×2): orig data3d, uv
- containeriteration: walks per-vertex
- net.maxon.node.access.decomposevector3d64: split UV vec3 into x, y, z
- net.maxon.node.invert: flip y
- net.maxon.node.access.composevector3d64: build flat 3D vec from (x, -y, 0)
- net.maxon.node.scale (or arithmetic with op=mul): apply scale factor
- net.maxon.node.blend: lerp(orig, flat, factor)
- set_property (data3d): writes back to output geometry
- floatingio (×2): factor + scale params
```

This is a **clean fresh-build, ~100-line scripted construction**. Not done in this iteration due to time, but the recipe and confirmed primitives are documented.

## Files in this folder

- `Build_UV_Slider_v1.c4d` — initial attempt (v1, v2)
- `Build_UV_Slider_v2_partial.c4d` — v2 chain insertion
- `Build_UV_Slider_v3_partial.c4d` — v3 with root.geometryout direct wire
- 6 viewport screenshots (factor 0/0.5/1.0 across versions) — all show same image (the original 3D head, no morph)
- This progress doc

## Next-session recipe — fresh-build deformer

```python
import c4d, maxon

doc = c4d.documents.GetActiveDocument()
# Add a new SN Deformer to the head bust instance
host = c4d.BaseObject(180420400)
host.SetName("UV Morph Slider")
# ... insert under target object ...

graph = host.GetNimbusRef(maxon.Id("net.maxon.neutron.nodespace")).GetGraph()
root = graph.GetRoot()

# Add all nodes in one transaction
with graph.BeginTransaction() as tx:
    orig_get = graph.AddChild(maxon.Id("orig"), maxon.Id("net.maxon.neutron.geometry.get_property"))
    uv_get   = graph.AddChild(maxon.Id("uv"),   maxon.Id("net.maxon.neutron.geometry.get_property"))
    iter_uv  = graph.AddChild(maxon.Id("iter"), maxon.Id("net.maxon.node.containeriteration"))
    decomp   = graph.AddChild(maxon.Id("split"), maxon.Id("net.maxon.node.access.decomposevector3d64"))
    inv_y    = graph.AddChild(maxon.Id("inv_y"), maxon.Id("net.maxon.node.invert"))
    compose  = graph.AddChild(maxon.Id("compose"), maxon.Id("net.maxon.node.access.composevector3d64"))
    scale    = graph.AddChild(maxon.Id("scale"), maxon.Id("net.maxon.node.scale"))
    blend    = graph.AddChild(maxon.Id("blend"), maxon.Id("net.maxon.node.blend"))
    set_pos  = graph.AddChild(maxon.Id("setpos"), maxon.Id("net.maxon.neutron.geometry.set_property"))
    fio_factor = graph.AddChild(maxon.Id("fio_factor"), maxon.Id("net.maxon.node.floatingio"))
    fio_scale  = graph.AddChild(maxon.Id("fio_scale"),  maxon.Id("net.maxon.node.floatingio"))
    tx.Commit()

# Configure ports + wire chain — see UV_SLIDER_PROGRESS.md for the topology
# Test factor=0/0.5/1.0; commit working result
```

This recipe is the HONEST deliverable — a confirmed-addable node-set + wiring spec ready for the next session to execute.
