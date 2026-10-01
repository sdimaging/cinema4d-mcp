# SN_RECIPE_rd_growth_deformer

**Version:** 1.1 (2026-10-01: Thickness smoothstep, `rd` Vertex Map output, Redshift look-dev rig, bubbling-terrain demo)
**Status:** Verified structurally, numerically and visually (C4D 2026.4.0, 2026-10-01)
**Eligible for Assemble Mode:** YES. `scripts/sn_build_rd_growth.py` regenerates it from scratch.
**Assembly test:** PASSED. The builder recreated the full deformer on a fresh torus (19.2k pts) in an empty doc, and colonies grew frame over frame.

## Purpose

A Gray-Scott reaction-diffusion **Scene Nodes Deformer** (180420400). Drop it under any polygon/primitive object and animated worm/maze/spot growth appears on the surface, displaced along normals. The pattern is carried by point index, so a deforming host keeps the pattern stuck to it (topology mode). It's the C4D equivalent of the Blender `RD_Growth` geo-nodes group.

This closes the `R7_memory_feedback_pde` open item from scene study 03. It uses an edge-neighbor stencil instead of K-NN.

## Provenance

- Built live through Maxon's built-in 2026.4 MCP (`exec_python` only, see gotcha #112), using this repo's knowledge layer.
- Proofs, all on 2026-10-01:
  - plane 100×100 grows radial worms
  - sphere 80k pts gives a full labyrinth at 0.38 s/frame
  - torus assembly test from the script
- Gotchas cracked on the way: #101–#112.

## Architecture

```
root.geometryin ─► subd (modeling.subdivide, subdivisions = AM "Subdivide")
                    ├─► gp  get_property(pt)                         P array, topology
                    ├─► mem.geo (AddPort)                            geometry into the sim
                    ├─► gnrm generatepointnormals ─► gpn get_property(normal,"Normal")
                    └─► sp.geometryin
INIT   it0 ⟳ P: seed = clamp(|p|<SeedRadius  +  noise(p,scale,seed)>SeedThreshold)
       comp0 (1, seed, 0) ─► bld0/wr0 ─► S0 array ─► tof0 ─► mem.types/_0 ; S0 ─► mem.initial._0
MEMORY (body inside capsule view)                       ports: geo feed kill da db dt steps
   tofL(current) ─► lp.types/_0 ; current ─► lp.initial._0
   rg Range(end = steps).innerdomain ─► lp.innerdomain ; lp.final._0 ─► next._0
   LCV body (inside lp view)                            ports: geo feed kill da db dt
      it ⟳ S ; nb neighbor(it.index, geo) ; itN ⟳ nb.neighborids ; rvn = S[itN.out]
      sum = Sum(rvn, itN.inner/outerdomain)            ← domain-aware reduction (#111)
      avg = (sum + S_i)/(n+1) ; L = avg − S_i           ← self-inclusive = stable (#110)
      A' = clamp(A + dt(Da·LA − AB² + F(1−A)))
      B' = clamp(B + dt(Db·LB + AB² − (K+F)B))
      compose(A',B',0) ─► bld/wr ─► next._0
OUTPUT it1 ⟳ P: rd = smoothstep(c−.06, c+.06, B), c = .36 − .22·Thickness
       newp = P + N·(rd·Height) ─► sp(Position) ─► sw(weight "rd") ─► root.geometryout   (rd = Vertex Map tag, #115)
```

## AM parameters (root input ports, typed via connection)

| Port | Label | Default | Wired to |
|---|---|---|---|
| feed | Feed | 0.0545 | mem.feed → lp.feed |
| kill | Kill | 0.062 | mem.kill → lp.kill |
| da / db | Diffusion A / B | 1.0 / 0.5 | mem → lp |
| dt | Time Step | 1.0 | mem → lp |
| steps | Speed (steps per frame) | 30 | mem.steps → rg.end |
| height | Height | 5.0 | hgt.in2 |
| thickness | Thickness | 0.5 | smoothstep window (fatter ↔ thinner worms) |
| seedr | Seed Radius | 0 | cmp0.in2 (disk at object origin) |
| seedthr | Seed Threshold (higher = fewer) | 0.75 | scmp.in2 |
| seedscale | Seed Noise Scale | 60 | snz.scale |
| seed | Seed | 123 | snz.seed |
| subdiv | Subdivide (density) | 0 | subd.subdivisions |

Pattern presets (Feed, Kill):
- Worms 0.0545, 0.062
- Maze 0.029, 0.057
- Spots 0.0367, 0.0649
- Fingerprint 0.037, 0.06
- Holes 0.039, 0.058

## Look-dev (white on black, like the Blender build)

`build_rd_lookdev(doc, host)` builds an RS material with base colour from the `rd` vertex attribute, coat 0.8 at roughness 0.04, plus two RS area lights (EV 9/8, size 300/400) and an RS camera aimed at the host. With no environment the background is black.

Demo scenes built with the script:
- `~/Documents/RD_SceneNodes/RD_BubblingTerrain.c4d`: a plane with an animated noise Displacer, with RD_Growth on top (#114).
- A cube-sphere host: Cube 60 segs + Spherify + RD_Growth, Subdivide 1 (86k pts).

## Performance (M-series Mac, C4D 2026.4)

- 10k pts × 30 steps: about 0.05 s/frame
- 80k pts × 30 steps: about 0.38 s/frame (about 6M point-steps/s)

## Safe to edit

- All the AM params.
- `snz` noise settings.
- `subd` type (catmull default).

## Not safe to edit

- `rvn`/`rvS`/`rvN` datatype: leave unset (#106).
- Float arithmetic datatype: leave at the default (#105).
- `mem`/`lp` `types/_0` must stay driven by typeof. Never wire the parent `types` port (#109).
- The `+ self` in the average (#110).

## Failure modes

1. **0 verts:** a readvalueatindex datatype was set, a `types` parent port got wired, or the normals weren't generated.
2. **Flat plateau instead of worms:** the self term is missing from the average (#110), or dt·Da is too large.
3. **Sim frozen:** call `SetDirty(DIRTYFLAGS_ALL)` and step frames sequentially from 0 (#57).
4. **MCP call "times out" but C4D keeps going:** chunk frame loops under 50 s (#112).

## Verification test

```python
for f in range(0, 81):
    doc.SetTime(c4d.BaseTime(f, doc.GetFps())); doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)
c = host.GetCache(); dc = c.GetDeformCache()
disp = [(dc.GetPoint(i) - c.GetPoint(i)).GetLength() for i in range(dc.GetPointCount())]
assert max(disp) < height * 0.6          # B peaks around 0.4, so it shouldn't saturate
assert sum(d > 1 for d in disp) grows frame over frame
```

## Known quality gaps vs. the Blender build

- Edge aliasing at vertex resolution. The fix is in-graph (a softness/blur pass on rd and a wider window), not an SDS wrap on the live sim (#116).
- Remesh mode (even remeshing per frame plus nearest-point state transfer) isn't ported yet. Hosts need constant topology.

## Open / next

- Coverage modes (noise patches, height, radius-from-seed front, vertex-map attribute): feed `(1-mask)*0.12` into the kill term, as in the Blender build.
- Seed Object link (`legacyobjectaccess`) instead of an origin disk.
- Remesh mode for topology-changing hosts: nearest-point state transfer between frames.
- Expose the pattern preset as a dropdown.
- Bubbling-terrain demo scene.
