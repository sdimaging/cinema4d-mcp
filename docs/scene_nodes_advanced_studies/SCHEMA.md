# Scene Nodes Deep-Study Record Schema

Every scene-study folder under `docs/scene_nodes_advanced_studies/scenes/NN_<slug>/`
must produce TWO artifacts:

1. **`record.json`** — machine-readable, schema-compliant. The training corpus.
2. **`study.md`** — human-readable prose with frame grid, slider grid, and
   architectural narrative.

`record.json` is the primary deliverable. `study.md` exists to support it
with images and prose; it should not contain facts that aren't in `record.json`.

## Why this exists

Spenser's directive (2026-05-01): *"30 scenes times 36 calls is worth it if
the output becomes structured knowledge and recipes. If it stays as raw notes,
it will be hard to reuse."* The schema turns the the reference build library into a
training corpus for architecture rather than a pile of screenshots.

## Pattern tags (controlled vocabulary)

Every scene gets one or more of these tags in `record.json.patterns[]`:

- `geometry_generation` — the scene's purpose is to author new geometry from primitives/parametrics
- `selection` — heavy use of selection capsules / setselection / facingselection2 / restrictions
- `array_processing` — buildfromvalue / readvalueatindex / array accumulation across iterations
- `loop_carried_value` — uses LCV / Memory primitive for state across iterations
- `field_weighting` — uses C4D Fields / vmaps / field samplers as weight inputs
- `mograph_bridge` — bridges to MoGraph cloners / effectors / Voronoi
- `output_routing` — instructive about how a Nodes Mesh/Modifier/Spline routes to its host
- `parameter_exposure` — instructive about AM-slider synthesis (root.GetInputs() typed ports)
- `modifier_stack` — Nodes Modifier (180420400) consuming an upstream object
- `simulation_bridge` — couples to dynamics / volumes / soft bodies / particles
- `feedback_loop` — the graph reads its own previous-frame output (PDE / RD / cellular)
- `volume_pipeline` — uses volumes builder / volume mesher / SDF
- `spline_pipeline` — generates / refines / sweeps splines (Nodes Spline 180420700)
- `twin_graph` — same scene authors both Nodes Mesh + Nodes Spline of equivalent shape
- `legacy_object_bridge` — uses `legacyobjectaccess` to consume an OM input
- `per_island_fanout` — `explode_islands` + `containeriteration` to process each island independently
- `point_stream_iteration` — `pointsmodifier` for per-vertex ops with named streams
- `parametric_primitive_chain` — chains `nside` / `circle` / `range` / `replicate`
- `noise_driven` — uses Perlin/Voronoi/Worley noise samplers as the modulation source
- `time_animation` — graph reads doc time and changes per frame independent of feedback

Add new tags when you find a pattern that doesn't fit. Document any
additions in this file.

## record.json schema

```jsonc
{
  / Identity
  "scene_name": "Reaction_Diffusion",                  / human label, no spaces preferred
  "source_file": "the reaction-diffusion practice scene", / exact filename in the reference build/
  "source_folder": "scene_03_reference",   / immediate parent folder name
  "studied_date": "2026-05-01",
  "study_index": 3,                                    / sequential per scenes/ folder

  / Pattern tags from the controlled vocabulary above
  "patterns": ["feedback_loop", "loop_carried_value", "array_processing"],

  / What it does
  "purpose": "Iterative random recursive subdivision of a flat plane.",
  "visual_result": "Fractal-like nested-blocks landscape...",
  "input_geometry": "Plane (5168) primitive — base mesh fed to the Nodes Mesh capsule",

  / Where the work lives — capsule hosts and graph topology
  "hosts": [
    {
      "om_name": "Nodes Mesh",
      "plugin_id": 180420600,
      "plugin_name": "Nodes Mesh simple",
      "graph_node_count": 20,
      "doc_level_node_count": 0,
      "role": "primary procedural generator"
    },
    {
      "om_name": "Nodes Spline",
      "plugin_id": 180420700,
      "plugin_name": "Nodes Spline",
      "graph_node_count": 8,
      "role": "twin spline branch — captures recursion as splines for sweep"
    }
  ],

  / Author-left annotations (verbatim where possible)
  "annotations": [
    {
      "host": "INFO null",
      "text": "There is an initial update issue. To solve this: Go inside..."
    }
  ],

  / AM-exposed sliders — synthesized via root.GetInputs()
  "am_sliders": [
    {
      "host": "Nodes Mesh",
      "name": "Iterations",
      "type": "Int32",
      "default": 6,
      "controls": "recursion depth — 1=single split, 10=fractal nesting",
      "descid_prefix": "(777, 5, 0)",
      "predicted_behavior": "depth of recursion",
      "observed_behavior": "confirmed — visually dramatic between 1 and 10"
    }
  ],

  / Internal graph topology — the load-bearing wires
  "key_connections": [
    {
      "from": "plane.geometryout",
      "to": "group(LCV).source",
      "purpose": "feed initial geometry into the iterator"
    },
    {
      "from": "group(LCV).final._0",
      "to": "explode_islands.geometriesin",
      "purpose": "fan out the iterated mesh by connected components"
    }
  ],

  / How the host's geometry output is produced
  "output_routing": {
    "final_node": "set_property",
    "via": "set_property → root.geometryout",
    "host_visibility_path": "Nodes Mesh capsule (180420600) renders root output as classic mesh in OM",
    "notes": "set_property is the canonical final-tag-and-emit pattern."
  },

  / Interesting data types observed in this scene
  "interesting_types": [
    "Geometry stream with multiple per-island components",
    "Array-of-iteration accumulator (4× readvalueatindex + buildfromvalue)"
  ],

  / Architecture decomposition
  "core_idea_nodes": [
    {
      "id": "group@<hash>",
      "type": "Loop Carried Value",
      "why_it_matters": "Iterates geometry through Iterations steps with auxiliary Hash/Seed/Speed passthroughs. THE node that does the work."
    }
  ],
  "scaffolding_nodes": [
    "multransform_5", "combine", "mat", "sqrpart", "sqrtrans", "vectrans"
  ],

  / What's clever — the design moves worth remembering
  "what_is_clever": [
    "Uses extrude with inset=100 inside LCV body — every iteration creates a new poly INSIDE each existing poly. No actual subdivide-the-mesh op; pure extrude-with-full-inset does it.",
    "explode_islands after LCV lets each recursion island be post-processed independently."
  ],

  / Gotchas — cost-saving warnings for re-implementers
  "gotchas": [
    {
      "issue": "On fresh load, LCV's current._0 doesn't bind until manually re-wired",
      "evidence": "Author's annotation tag",
      "workaround": "Open LCV, rewire the first wire between Variable 1 input and explode_islands"
    }
  ],

  / Plain-English rebuild recipe — the procedural cookbook entry
  "rebuild_recipe": [
    "1. Create a Nodes Mesh (180420600). Add a Plane primitive node inside; wire plane.geometryout → next stage.",
    "2. Create a Loop Carried Value node with types=[Geometry, Geometry]. Its source ← plane.geometryout.",
    "3. Inside the LCV body, place an extrude node with inset=100; wire current._0 → extrude.in, extrude.out → next._0.",
    "4. Drive the LCV's iteration count with a Range node whose end ← root.Iterations (synthesized AM port).",
    "5. After the LCV, fan out via explode_islands → containeriteration.",
    "6. Accumulate per-island data with buildfromvalue + 4× readvalueatindex.",
    "7. Final node set_property → root.geometryout.",
    "8. Synthesize 6 typed AM ports on root: Iterations(Int32), Global Seed(Int32), Noise Type(enum), Scale(Float), Seed(Float), Speed(Float)."
  ],

  / The crown jewel — minimum useful slice as an MCP recipe
  "minimal_reproducible_subgraph": {
    "name": "extrude_inset_lcv_recursion",
    "purpose": "Recursively subdivide a polygonal surface by repeated full-inset extrude inside an LCV.",
    "node_count": 7,
    "nodes": [
      "plane (or any input mesh)",
      "loop_carried_value (types: Geometry,Geometry)",
      "range (drives iteration count from AM Iterations)",
      "extrude (inset=100, depth from hash(seed, range.index))",
      "hash (seed=range.index, salt=root.Seed)",
      "set_property (final)",
      "root.geometryout"
    ],
    "exposed_params": ["Iterations:Int32", "Seed:Float"],
    "value_proposition": "Lets ANY mesh be fed into a recursive nested-poly generator. Tiny graph; massive visual return.",
    "candidate_for_mcp_recipe": true,
    "recipe_id_proposed": "R6_extrude_inset_lcv"
  },

  / Files in this study folder
  "artifacts": {
    "study_md": "study.md",
    "frames": ["frames/default_f000.png", "frames/default_f030.png", "..."],
    "sliders": ["sliders/iterations_1.png", "sliders/iterations_10.png", "..."],
    "raw_graph_dump": "graph_dump.json"
  }
}
```

## Field guidance

- **Be concrete in `key_connections`**: use `node.port` form. If you don't
  know the exact port name, mark it `?<name>` and verify before commit.
- **`am_sliders.descid_prefix`** is always `(777, 5, 0)` for synthesized
  ports — record it anyway because future tooling will key off this.
- **`minimal_reproducible_subgraph` is mandatory.** Even if the answer is
  "this whole scene IS the minimum," say so explicitly with that node count.
  The point is to extract a recipe lift-out, not to repeat the full scene.
- **`gotchas`** must include `evidence` (annotation, MCP probe, viewport
  observation) so future-readers can re-verify.
- **`patterns`** must come from the controlled vocabulary. If a new pattern
  is needed, add it to this SCHEMA.md first.

## Master index

Every committed scene appends one row to `docs/scene_nodes_advanced_studies/INDEX.json`,
which is a flat array of compact records:

```jsonc
[
  {
    "study_index": 2,
    "slug": "recursive_subdivision",
    "patterns": ["loop_carried_value", "geometry_generation", "twin_graph"],
    "minimal_recipe_id": "R6_extrude_inset_lcv",
    "node_count": 20,
    "complexity": "medium-hard",
    "folder": "scenes/02_recursive_subdivision/"
  }
]
```

This is the queryable surface for the MCP knowledge layer.
