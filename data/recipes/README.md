# Scene Nodes Recipe Library

Production Scene Nodes recipes — runnable via `recipe_run`. Each recipe is
a JSON spec composed of `setup` / `steps` / `teardown` blocks, with each
step calling existing MCP primitives (`scene_nodes_add_node`,
`scene_nodes_connect_ports`, `scene_nodes_synthesize_port`,
`scene_assert`, etc.).

## Source

The 5 canonical recipes (R1–R5) are derived from the Nodebase study at
[`docs/scene_nodes_nodebase_study.md`](../../docs/scene_nodes_nodebase_study.md).
Refer to that doc for the conceptual flow + verbatim quotes from the
source articles.

## Status

| Recipe | Status | Notes |
|---|---|---|
| R1 — Circle from Range | scaffolded | Uses well-mapped nodes (Range, Range Mapper, Transform Vector, Spline Assembler). Port names need live verification on first battle-test run. |
| R2 — Noise point displacement | TBD | Pending live probe of Iterate Collection / Set Element / Points Info / Geometry Property Get/Set port names. |
| R3 — Noise extrude with LCV | TBD | Same; plus LCV `Variable N` runtime template arg. |
| R4 — Weight-controlled Poke | TBD | Same; plus Polygons Info + nested iteration. |
| R5 — MoGraph Voronoi → SN | TBD | Mostly classic-C4D wiring (Connect object, Object-mode port). |

## Live-probe workflow

When C4D is connected and a recipe is ready to battle-test:

1. Start with R1 (smallest / fewest unknowns).
2. Run `recipe_run` with `stop_on_fail=True`.
3. On port-name failure, use `scene_nodes_walk` to introspect the actual
   port names of the freshly-created nodes; pin the recipe.
4. Re-run; when green, commit the pinned recipe.
5. Move to R2 with the lessons learned (Iterate Collection / Set Element
   port names will likely be reused).

## Port name conventions

From `data/node_port_schema.json` (51 known nodes):

- **Range** in: `start`, `end`, `domain`; out: `count`, `out`,
  `innerdomain`, `outerdomain`
- **Loop Carried Value** in: `innerdomain`, `outerdomain`, `types`,
  `initial._0`, `current._0`; out: `domain`, `next._0`, `final._0`
- **Memory** similar shape to LCV
- **Switch** in: `cycle`, `index`, `in`, `datatype`; out: `out`
- **Hash** in: `seed`, `salt`, `minimum`, `maximum`; out: `result*` (per type)
- **Get Count** in: `datatype`, `arrayin`; out: `countout`
- **Build Array** in: `datatypein`; out: `arraylengthout`
- **Modeling capsules (Extrude/Inset/Subdivide/Grow Selection)**:
  in: `selection` / `selectionstring` / `mode` / `geometryin` plus
  per-capsule params; out: `geometryout`
- **If** in: `datatype`, `in1`, `in2`, `in3`; out: `out`

Nodes with empty port_schema entries (need live probe):
`Iterate`, `Container Iteration`, `For Each`, `Append`,
`Read Value At Index`, `Write Value At Index`, `Set Selection`,
`Resample`, `Push Apart`.

Nodes missing entirely (need live probe + atlas backfill):
`Iterate Collection`, `Set Element`, `Get Element`, `Points Info`,
`Polygons Info`, `Geometry Property Get`, `Geometry Property Set`,
`Selection String`, `Sample Noise`, `Pivot Matrix`, `Compose Container`,
`Sort Collection`, `Concatenate`, `Average`, `Scalar Arithmetic`.
