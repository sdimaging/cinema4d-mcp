# Scene Nodes — The Iterative Feedback-Loop Procedural Tool Pattern

Dissected from Maxon's reference scene `0360 Geo Feedback Loop [Scene Nodes Example]`
(2026-05-01). This is the canonical pattern for building **artist-facing
iterative procedural tools** — the "lock in a look, expose 3 sliders, apply
to anything" build that's the actual point of Scene Nodes.

The user's framing:
> "the simple shit isn't what gets me — these sort of builds get me… you see
> the exposed values in the AM (iterations / chance / seed) are to drive the
> random subdivisions… this is a cool functional build and… as you see this
> is a collected node modifier so you can apply this to a sphere a cube a
> plane or any geo in the object tree and it just works the same — that's
> the benefit of this scene node tooling"

## The setup (what the artist sees)

- **Plane** in object tree
- **Geo Feedback Loop (OPEN ME)** Node Modifier as child of Plane
- AM panel on the modifier exposes 3 draggable params: **Iterations** (int),
  **Chance** (float), **Seed** (float)
- Drag them → mesh updates live
- Detach the modifier from Plane, re-parent to Sphere/Cube/any geo → it
  just works the same way

## The graph (what the artist doesn't see, what we now do)

Plugin: **Scene Nodes Deformer (180420400)** — the canonical "Nodes
Modifier" that bridges to the Object Manager as a child-of-target deformer.

### Root I/O — the AM bridge

```
ROOT INPUTS  (host → graph)
  geometryin      ← parent's polygon mesh (the Plane / Sphere / whatever)
  integer         ← Iterations slider           (the "loop count" AM param)
  float           ← Chance slider               (per-iteration extrude probability)
  float1          ← Seed slider                 (deterministic randomness anchor)
  globalmatin / localmatin / objectmatrix / localobjectmatrix / modifiermatrix
                  ← matrix context (parent transform info, optional)
  time / frame / fps / nimbus / searchpaths / renderspace / ocioconfig
                  ← time/render context (optional)

ROOT OUTPUTS (graph → host)
  geometryout     → modified mesh fed back to host (replaces parent's display geom)
```

### Inner nodes (top-level)

```
reroute@                  ← organizational pass-through (integer slider → range.end)
range@                    ← iterator: 0..N-1 driven by Iterations
loopcarriedvalue@         ← THE FEEDBACK PRIMITIVE — geometry state across iterations
group@                    ← arithmetic sub-group (computes normalized progress 0..1)
  ├── arithmetic(sub)     ← (iterations - 1)
  └── arithmetic(div)     ← (current_index) / (iterations - 1)
setselection@             ← initial selection tagging on input geometry
extrude@                  ← per-iteration modeling op (the loop body)
scaffold@                 ← editor visual layout container (no compute)
3× annotation@            ← in-editor documentation
context_externaltimeinput / context_notime  ← framework boilerplate
```

### Connection map (the actual data flow)

```
                        AM SLIDERS (host → graph)
                               │
              ┌────────────────┼────────────────┬────────────────┐
              │                │                │                │
       root.integer     root.float       root.float1      root.geometryin
       (Iterations)     (Chance)         (Seed)          (parent mesh)
              │                │                │                │
              ▼                ▼                ▼                ▼
         reroute.in        LCV.in@LE…       LCV.in@Qe…      setselection.geometryin
              │           (passthrough)    (passthrough)            │
              ▼                                                     ▼
         reroute.out                                        setselection.geometryout
              │                                                     │
              ▼                                                     ▼
          range.end ─────────► (N iterations)              LCV.initial._0
              │                                                     │
   ┌──────────┴────────────────┐                                   │
   ▼                           ▼                                   │
 range.count → group.count  range.out → LCV.in@UXs6 (current idx)  │
                  │                                                │
                  ▼                                                │
              group (arithmetic):                                  │
                count - 1 = denom                                  │
                index / denom = normalized progress 0..1           │
                  │                                                │
                  ▼                                                │
              LCV.in@JdU (normalized progress passthrough)         │
                                                                   │
       LCV.innerdomain ←─────── range.innerdomain (loop scope)    │
                                                                   │
       ┌───────────────────────────────────────────────────────────┤
       ▼                                                           │
   LCV.current._0 ◄──────── extrude.current._0  (each iter result)│
                                                                   │
   LCV.next._0    ────────► extrude.next._0     (next iter input) │
                                                                   │
   LCV.final._0   ────────► extrude.geometryin   (post-loop final)│
                                                                   ▼
                                                          [LOOP COMPLETES]
                                                                   │
                                                            extrude.geometryout
                                                                   │
                                                                   ▼
                                                            root.geometryout
                                                                   │
                                                                   ▼
                                                         Modified parent mesh
```

## The 5 architectural lessons

### 1. AM params are root.GetInputs() typed ports — no UD, no FIO needed

For a Node Modifier, AM-exposed sliders surface as typed root inputs:
- `root.integer` (named generically — the Iterations int slider)
- `root.float` (Chance float slider)
- `root.float1` (Seed — second float slider, auto-suffixed `1`)

The `synthesize_port` v2.1 recipe (cracked Session 3) creates these. The
**connection IS the type system**: the type of the slider widget is
inferred from the typed inner port the root input is wired to (here:
`reroute.in` was Int, so the slider becomes Int).

Critical: **NO FloatingIO nodes, NO UserData — the typed root.GetInputs()
ports ARE the AM bridge** for Node Modifiers / Nodes Mesh / etc.

### 2. Loop Carried Value is the iterative-feedback primitive

LCV's port set:
- `initial._0`  ← initial state (loop entry point — the input geometry)
- `current._0`  ← this iteration's result (from loop body — feedback in)
- `next._0`     → next iteration's starting state (passed to loop body)
- `final._0`    → final state after N iterations
- `innerdomain` ← scope binding (drives N from a Range node)
- Auxiliary `in@hash...` ports — passthrough values available in every
  iteration without re-wiring (Chance, Seed, current index, normalized
  progress all flow through these)

The body of the loop lives in the SAME graph as the LCV (NOT in a sub-
graph). Body nodes have matching `current._0` / `next._0` ports that
pair with LCV's. The framework auto-routes per-iteration data through
this convention.

### 3. Range + Group(arithmetic) for "current iteration progress 0..1"

Range outputs `count`, `out` (current index), and `innerdomain` (scope).
Group with two arithmetic nodes:
- `arithmetic(sub)`: count - 1 = denominator
- `arithmetic(div)`: out / (count-1) = normalized 0..1 progress

This normalized progress is fed into LCV as a passthrough so the loop
body has access to "where we are in the iteration" as a 0..1 fraction.

### 4. Modeling-op nodes (Extrude, Inset, Subdivide, etc.) are LCV-aware

Modeling capsules have hidden `current._0` / `next._0` ports that pair
with LCV when wired. They're auto-discovered by the framework — you
don't see them in normal port enumeration, but they're addressable via
their matched names when LCV's `current._0` and `next._0` are wired.

This is why Extrude appears as a top-level node yet acts as the loop
body — the LCV-Extrude pair forms the iteration unit.

### 5. The Node Modifier (180420400) is the "apply to anything" container

Because the Modifier's input is `root.geometryin` (parent's mesh) and
output is `root.geometryout` (replacement deformed mesh), you can:
- Detach the Modifier from one parent, re-parent to another → works on
  the new parent
- Save the Modifier as an asset (CreateObjectAsset) → drag-drop reusable
  procedural tool

This is what the user means by *"a collected node modifier so you can
apply this to a sphere a cube a plane or any geo in the object tree
and it just works the same — that's the benefit of this scene node
tooling."*

## The pattern as a recipe spec

```yaml
recipe: iterative_geometry_modifier
host_kind: nodes_modifier  # plugin 180420400
parent: <any geometry-producing object>

am_params:
  - {name: iterations, type: int,    default: 5,    label: Iterations}
  - {name: chance,     type: float,  default: 0.3,  label: Chance,    range: [0, 1]}
  - {name: seed,       type: float,  default: 1.0,  label: Seed}

inner_graph:
  - {ref: reroute,      type: Reroute}
  - {ref: range,        type: Range}
  - {ref: lcv,          type: Loop Carried Value, types: [Geometry]}
  - {ref: group,        type: Group, contents: [
      {ref: arith_sub,  type: Arithmetic, op: sub},
      {ref: arith_div,  type: Arithmetic, op: div},
    ]}
  - {ref: setselection, type: Store Selection}
  - {ref: extrude,      type: Extrude}

connections:
  # AM → loop driver
  - {from: root.integer,         to: reroute.in}
  - {from: reroute.out,          to: range.end}
  # AM passthroughs into LCV
  - {from: root.float,           to: lcv.in_chance}     # auxiliary
  - {from: root.float1,          to: lcv.in_seed}       # auxiliary
  - {from: range.out,            to: lcv.in_current_idx}
  # Normalized progress
  - {from: range.count,          to: arith_sub.in1, op_const: 1}  # count - 1
  - {from: range.out,            to: arith_div.in1}
  - {from: arith_sub.out,        to: arith_div.in2}
  - {from: arith_div.out,        to: lcv.in_progress}
  # Loop scope
  - {from: range.innerdomain,    to: lcv.innerdomain}
  # Geometry pipeline
  - {from: root.geometryin,      to: setselection.geometryin}
  - {from: setselection.geometryout, to: lcv.initial._0}
  - {from: extrude.current._0,   to: lcv.current._0}    # body output
  - {from: lcv.next._0,          to: extrude.next._0}   # body input
  - {from: lcv.final._0,         to: extrude.geometryin} # post-loop
  - {from: extrude.geometryout,  to: root.geometryout}

verify:
  - host plugin id == 180420400
  - root.GetInputs has integer, float, float1 (typed AM ports)
  - root.GetOutputs.geometryout has incoming connection from extrude
  - LCV initial._0 ← setselection (initial state wired)
  - All 3 AM params show as draggable sliders in AM
  - Re-parenting the modifier to different geo still works
```

## Why this is the "real deal" procedural tool

Compare with the M5 capstone (RD blob + spline growth on surface, 2026-05-01):
- M5 baked geometry via Python at scene-construction time
- M5 had no exposed AM params — to change anything the artist would have
  to re-run a Python script
- M5 was procedural at the GENERATIVE step but not as a TOOL

The Geo Feedback Loop pattern is procedural as a TOOL:
- Drag a slider → re-evaluates entire iteration chain → live geometry update
- Detach from one parent, attach to another → works on any geo
- Save as asset → reusable across scenes
- Compose with other Node Modifiers in a stack → infinite creative space

This is the cinema4d-mcp authoring target: **patterns that produce
artist-facing tools, not Python-driven baked outputs.**

## Implications for cinema4d-mcp

### Add to atlas
- Pattern: `iterative_geometry_modifier` — codify the topology above
- Pattern primitive: `lcv_with_passthroughs` — the LCV + Range + Group(arithmetic)
  scaffolding (reusable across MANY iterative tools)

### Add to MCP handlers
- `scene_nodes_create_iterative_modifier(host_object, am_params, body_node_chain)`
  — high-level "build me an iterative modifier with N AM sliders driving body X"

### Confirmed via deeper probe (2026-05-01)

- **Extrude has only standard ports** (`geometryin`, `geometryout`, `selection`,
  `selectionstring`, `mode`, plus 11 modeling-param inputs). No hidden
  `current._0` / `next._0`. So Extrude is NOT inside the loop body in the
  way I first guessed.
- **LCV's `current._0` and `next._0` are SELF-CYCLE ports** — they bind
  back to LCV itself via the framework's variadic-types contract. When
  LCV's `types` port is set to `[Geometry]`, the framework auto-creates
  the cycle that re-evaluates LCV's `initial._0`-derived data N times.
- **The actual loop mechanics**: LCV holds `initial._0` as its starting
  state. Range drives N iterations via `innerdomain` (scope binding).
  Each iteration, the LCV re-evaluates its scope (everything that
  depends on `LCV.next._0`). After N iterations, `LCV.final._0` emits
  the converged result, which feeds Extrude's geometryin → root.geometryout.
- **The randomization**: comes from the body re-evaluation reading the
  iteration index (`LCV.in@UXs6...`) plus Seed (`LCV.in@Qe...`) plus
  Chance (`LCV.in@LE...`) — these are LCV's auxiliary passthrough inputs
  that surface inside the loop's scope. The selection-randomization
  logic uses Hash-equivalent computation; this scene encodes it via the
  setselection node + arithmetic on these passthroughs (the exact hash
  node may live in a deeper portion of the graph not yet probed).
- **`scaffold@` is layout-only** — confirmed 0 I/O ports. Editor visual
  organization that survives in the runtime graph but doesn't compute.

### What's still mysterious

- The exact mechanism by which LCV's auxiliary `in@hash...` ports become
  available to internal nodes (setselection, etc.) without explicit
  outgoing connections from LCV. May involve a "hidden output side" of
  LCV that isn't visible to standard port enumeration.
- The Hash → "is this poly selected this iteration?" computation — must
  exist somewhere; possibly inside `setselection` itself as a
  setselection-specific feature, possibly via implicit framework
  plumbing in the LCV scope.

These are research questions for future sessions. The pattern works
empirically; mastering it for tool authoring doesn't require resolving
these mysteries — just follow the topology.
