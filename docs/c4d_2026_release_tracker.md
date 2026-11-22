# C4D 2026.x Release Tracker — what changed, and what it means for MCP/agent work

Maintained alongside the gotchas doc. Where the gotchas doc records *how the API
actually behaves*, this tracks *what Maxon shipped per release* — so an agent
driving C4D knows which features exist on the user's build and which subsystems
recently changed (recently-rewritten subsystems are where gotchas breed).

Primary sources: Maxon Knowledge Base release notes (support.maxon.net →
Release Notes), CG Channel and Digital Production coverage. Last updated for
**2026.3.0 (June 10, 2026)**.

---

## 2026.3.0 — June 10, 2026

### New UV Editor (BodyPaint UV editor REPLACED)
The headline change. The UV Editor was rebuilt on the modern viewport tech and
no longer runs on BodyPaint 3D dependencies. Full unwrapping/packing/organizing
in-app: seam editing, smart auto-unwrap, zero-overlap packing, texel density
tools, UDIM support, and 3D-viewport tools working in the UV context.
**Internal UV coordinate handling was restructured.**

**MCP relevance (HIGH):** any tooling that touches UVs (`uv_from_projection`,
`uv_layout_stats`, `uv_transfer`, `uv_islands_to_objects`, UVWTag manipulation,
`CallUVCommand`-era APIs) is now sitting on a rewritten subsystem. Validate on
2026.3 before trusting; BodyPaint-era UV command IDs and behaviors may have
shifted. Expect new gotchas here — this is the "recently rewritten" zone.

### Cloner: three new Distribution types
- **UV Projection** — clones distributed by the surface's UV coordinates;
  positions survive later subdivision of the surface (UV-stable, not
  point-index-stable).
- **Rivet** — clones around the outlines of faces.
- **Edge** — clones along edges.

**MCP relevance:** new cycle values on `MG_POLY_MODE_`-family Cloner params —
re-enumerate Cloner DescIDs on 2026.3 before scripting distributions.

### Particles: Particle Property Manager
Dedicated manager to create, view, and edit particle properties in one place
(previously scattered across emitter/group UIs).

**MCP relevance (HIGH for particle pipelines):** the new particle system
(ParticleGroupObject 1060887, Mesh Emitter 1062577, etc.) keeps gaining
first-class UI. Property *creation* via a manager implies properties are
addressable data channels — worth probing whether custom per-particle
properties are scriptable (Python `c4d.modules.particles` / maxon Block APIs)
for binding/advection workflows that currently ride on position+color only.

---

## 2026.2.0 — April 15, 2026

- **Fabric Brush** — physics-based sculpting that drives the cloth sim while
  brushing (folds/draping in viewport). Simulation-backed *modeling tool* —
  note the pattern: sim framework leaking into interactive tools.
- **Bend Deformer** — C-shaped and S-shaped modes, symmetry origin options
  (mirrored bends from one deformer). New DescIDs on Obend.
- **Edit Isolines** works inside Symmetry objects and SDS.
- **Soft selection for splines.**
- **Material Manager "Active Object" tab** — materials of selected objects only.
- **Align Tangents** command for F-Curves (neighboring keys).
- **Target Effector: Loop** option for closed clone arrangements.
- **Look at Camera** can use any scene camera, not just the active one.
- **XPresso: Command Line Argument node** — string args readable in XPresso
  during command-line rendering. **MCP relevance (HIGH for automation):**
  headless parameterization without scene edits — pass values into a scene at
  render time via CLI, read them in XPresso, drive anything. Pairs naturally
  with agent-driven batch rendering.
- **Redshift Live** replaces Redshift RT as the real-time preview engine.
  **MCP relevance:** any renderer-enumeration logic that special-cases RT
  should be rechecked (render engine IDs / availability).
- **Redshift Sun & Sky** night-sky option.
- **Windows ARM native** (Snapdragon) + **Cinema 4D for iPad beta** (M2+).

---

## 2026.1.0 — December 3, 2025 (+ 2026.1.1 Jan 14, 2026)

- **Cloner: Advanced Distribution** — node-based distribution patterns; six
  ready-made distribution Capsules (stacking, pyramids, bead stringing, terrain
  projection); custom distributions via a **Nodes Distribution object**.
  **MCP relevance (HIGH):** this is Scene Nodes tech surfacing inside MoGraph —
  distribution Capsules are assets the MCP's scene-nodes tooling
  (`scene_nodes_*`) can author. A custom distribution = a capsule graph; our
  GraphDescription knowledge applies directly.
- **Liquid Flow emitter** — continuous liquid streams / volume filling (was a
  Basic-emitter + Liquify workaround). Part of the unified particle/sim stack.
- **Preserve UV** — mesh edits without texture stretching (modeling).
- **Camera View Snapping** between orthographic views.
- **Take system** workflow improvements; **Pyro** quality/stability pass;
  general particle updates.
- **Substance Connector** — direct material transfer with Adobe Substance 3D
  Sampler.
- **Cineware for Unreal** — modifiers respected, auto-created material
  instances.

---

## 2026.0.0 — September 10, 2025

Foundation release for the 2026 cycle (feature-light at launch; the cycle's
features landed in .1/.2/.3):

- **Unified Simulation Framework: fluids** — GPU liquid dynamics with
  viscosity, surface tension, rigid-body collisions; **Liquid Fill emitter**,
  **Liquify modifier**, **Liquid Mesher** (particles → surface).
  **MCP relevance:** liquids are particles in the SAME new particle system —
  ParticleGroupObject reads (positions/velocities via maxon Block API) apply to
  liquid sims too.
- **OpenColorIO default: ACEScg working space** — input tagging → linear ACEScg
  math → output transforms (ACES/AgX). **MCP relevance:** affects what
  `render_frame`/viewport grabs return; color-pipeline assumptions from pre-OCIO
  scenes don't carry over.
- **UDIM + texel density groundwork** (completed by the 2026.3 UV Editor).
- **AI Asset Search** in the Asset Browser (on-device index).
- Redshift 2025.0 **Procedural Clouds** integrated with Sun & Sky.

---

## Cross-release patterns worth knowing

1. **The new particle system is the strategic center** — fluids (.0), Liquid
   Flow (.1), general updates (.1/.2), Property Manager (.3). Every release
   deepens it. Plugins/agents integrating against it (emitter configs, PG
   reads, per-particle data) are on the actively-developed path — expect
   additive DescID changes each release; re-enumerate after upgrades.
2. **Scene Nodes keeps surfacing through Capsules, not the Node Editor** —
   Advanced Distribution (.1) follows the established pattern: node graphs
   wrapped as artist-facing assets. Authoring capsules programmatically is the
   highest-leverage Scene Nodes skill.
3. **Legacy subsystems are being replaced wholesale** (BodyPaint UV editor →
   new UV Editor in .3; Redshift RT → Redshift Live in .2). After each
   replacement, treat old command IDs/APIs in that zone as suspect until
   re-verified — and log what changed in the gotchas doc.
