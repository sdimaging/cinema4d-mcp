# Scene Study — Mycelium Tutorial

**Source:** `Mycelium_Growth_Files_01/Mycelium_Tutorial_01.c4d`
**Studied:** 2026-05-01
**Co-scene:** scene 14 (Mycelium Generator V3 — production capsule).

## What this scene does

Pedagogical minimal version of the mycelium growth idea. Just 17 graph
nodes and 3 OM objects. No memory@ — graph reads `time` as a root
input and procedurally generates output as a function of `t`.

## Object tree (after stripping clutter — none present)

```
Sweep                         (5118 — classic Sweep generator)
├── n-Side                    (5179 — cross-section profile, radius 2, 6 sides)
└── Nodes Spline              (180420700 — Scene Nodes Generator/Neutron, 17 nodes)
```

Just 3 objects total. The Sweep extrudes the n-Side along the
Nodes Spline's procedural spline → visible mycelium-like mesh.

## Architecture — 17 nodes, NO memory@

`recipe_style: bare_graph` (vs scene 14 V3's capsule style).

### Key nodes
- **`children@`** "Children Op" — reads OM children of host (for variable-input topology)
- **`surfacebluenoise@`** "Surface Blue-Noise" — distributes spore points on surface (uniform-but-non-aliased)
- **`disc@`** "Disc" — built-in fallback surface (procedural)
- **3× `containeriteration@`** — multi-stage iteration
- **2× `append@`** — building Points + Segments arrays
- **`assembler@`** "Assemble Spline" — same R12 multi-segment spline output as scene 05
- **`group@`** "Memory" — labeled metaphorically (NOT memory@; just a Group named Memory)
- **`cube@`**, **`disc@`** — primitive shapes used internally
- **`geometry@`** "Geometry Op" — output emit

### 7 named AM params + 9 generic
- Direction Scale (default 10)
- Random Amount (0.3)
- Random Seed (123)
- Maximum Neighbors (12)
- Min Length (6)
- Count (100)
- (and 9 generic Inputs)

NOTE: NO Source Object Object-typed AM port. The graph internally
samples a built-in `disc@` primitive. So this is NOT a capsule —
artists can't drag custom geometry. **It's a bare-graph
demonstration of the surfacebluenoise + iteration + assembler
pattern.**

## Why nothing renders on fresh load

When studied, the Nodes Spline produced no output. Sweep cache was
None, n-Side was correct, Sweep child order was correct, AM params
had non-zero defaults (Count=100, Direction Scale=10, etc.), yet no
spline emerged.

Most likely the same LCV-rewire-on-load bug as scene 02 (Recursive
Subdivision). The author of those scenes documented this
specifically: graphs sometimes need a manual interaction (parameter
touch, wire re-engagement) to start producing output on fresh load.

## Pattern tags

`geometry_generation`, `spline_pipeline`, `legacy_object_bridge`,
`parameter_exposure`, `parametric_primitive_chain`, `noise_driven`

NOTE: NO `feedback_loop`, NO `simulation_bridge`, NO `time_animation`
in the graph-state sense. Pure procedural graph; same params = same
output (modulo the LCV-rewire bug).

## Architectural insights

### `surfacebluenoise@` is the seed primitive

Distributes N points on a surface with blue-noise (Poisson-disk-like)
distribution — produces uniform spacing without grid aliasing. The
input "Geometry" wire goes to a `disc@` primitive (built-in) — so the
mycelium grows on the disc.

For a capsule version (scene 14 V3), this would wire to a
`legacyobjectaccess@`-bridged Source Object instead.

### Same `assembler@` output as scene 05 Spiderweb

Confirms R12_multi_segment_spline_assembler is universal across
spline-output scenes. Different graph paths produce the same
Points + Segments arrays format; assembler emits.

### Compact 17-node graph

Smallest hero-asset graph yet observed (vs scene 03's 91, scene 11's
chain, scene 14's 48). Pedagogical-minimum size.

## Recipe candidates

- `R41_blue_noise_seeded_spline_growth` — surfacebluenoise + iteration + assembler (the core pattern)
- `R42_time_driven_procedural_growth` — root.time as input, no memory@ (potential — graph has time root input but I couldn't observe its effect since output didn't render)

## Lessons for cinema4d-mcp

1. **Bare-graph recipes are pedagogical** — show the core pattern
   without artist-shipping wrap. Scene 13 is bare-graph; scene 14 is
   capsule.
2. **`surfacebluenoise@` is the canonical surface-seeding primitive.**
3. **Recipes for the same `assembler@` output (scenes 05, 06, 13)**
   confirm Points + Segments arrays as the universal Nodes-Spline
   output protocol.
4. **Built-in surface primitives (`disc@`, `cube@`) inside graphs**
   provide fallback samples when no Source Object is bound.

## Comparison vs scene 14 V3

| Aspect | Scene 13 Tutorial | Scene 14 V3 |
|---|---|---|
| Graph nodes | 17 | 48 |
| Has memory@? | NO (time-as-input) | YES |
| AM params | 7 named + 9 generic | 21 named |
| Field children | 0 | 2 |
| Defensive gates | 0 | 3 |
| Lifecycle pruning | NO | YES |
| Internal sweep | NO (uses OM Sweep) | YES |
| Production-readiness | pedagogical | shippable capsule |
| recipe_style | bare_graph | capsule |
| Source Object input | NO (uses internal disc@) | YES (Object-typed AM port) |

The Tutorial teaches the core idea (blue-noise seeding + iteration +
assembler); V3 productionizes it as a capsule. **This is the
canonical "tutorial → production" arc — recipes should show both.**

## Recreation difficulty

**Medium.** No memory@, no defensive gates, no lifecycle pruning, no
internal sweep. Just the bare core: blue-noise sampling → iteration
→ assembler → output. Once recipe primitives are exposed in
cinema4d-mcp, this is ~12 graph operations.
