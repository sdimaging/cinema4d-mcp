"""Scene Nodes pattern synthesizer.

Given a high-level intent (e.g. "loop_over_polygons", "reaction_diffusion",
"surface_clinging_growth"), produce a `GraphDescription.ApplyDescription`-shaped
dict that materializes the canonical node skeleton for that pattern.

This is the "I GOT YOU EASY" layer — instead of hand-wiring 144 nodes per
build, you call `build_pattern("surface_clinging_growth", host_mesh="MyMesh",
branches=20)` and get back the spec, plus the names of the new nodes so
downstream code can connect to them.

Patterns are derived from analysis of 9 real-world example scenes. Each
pattern has:
  - A skeleton (the minimum nodes to make it functional)
  - Optional extension hooks (`body=` to inject inner logic)
  - Documented carried state for loops
  - Type annotations on key ports

All `$type` values use English UI labels (the only form ApplyDescription
accepts). Patterns try to be additive — they emit nodes that connect to
existing ones in the host graph (e.g. via `$ref` to an `$input` port).
"""

from __future__ import annotations
from typing import Any, Optional


# ---------------------------------------------------------------------------
# VERIFIED $type labels — confirmed working with ApplyDescription via live
# probing 2026-04-29. Each label adds a new top-level node when used.
# Maps verified English label → dissection bare-name. Use this to validate
# pattern synthesizer output before sending to ApplyDescription.

VERIFIED_LABELS: dict[str, str] = {
    # Distribution / scatter (THE killer scatter primitives)
    "Surface Blue-Noise": "surfacebluenoise",
    "Surface Scaled Blue-Noise": "surfacescaledbluenoise",
    # Capsule UD exposure
    "Floating IO": "floatingio",  # exposes graph ports to parent capsule's Attribute Manager
    # Loop scaffold
    "Range": "range",
    "Loop Carried Value": "loopcarriedvalue",
    "Memory": "memory",
    "Get Count": "getcount",
    # Math/logic
    "Hash": "hash", "Compare": "compare", "If": "if", "Switch": "switch",
    "Arithmetic": "arithmetic", "Scale": "scale", "Round": "round",
    "Clamp": "clamp", "Blend": "blend", "Step": "step", "Negate": "negate",
    "Invert": "invert", "Distance": "distance", "Normalize": "normalize",
    "Boolean Operator": "booleanoperator",
    # Vector/matrix
    "Compose Matrix": "composematrix", "Decompose Matrix": "decomposematrix",
    "Dot Product": "dot", "Cross Product": "cross",
    "Vector Length": "length",
    # Stochastic / time
    "Noise": "mainnoise", "Time": "time",
    # Array
    "Build Array": "buildfromvalue",
    # Primitives
    "Sphere": "sphere", "Cube": "cube", "Tube": "tube",
    # Modeling
    "Inset": "inset", "Extrude": "extrude", "Subdivide": "subdivide",
    # Selection
    "Random Selection": "randomselection",
    "Grow Selection": "growselection",
    "Store Selection": "setselection",
    # Spline
    "Resample Spline": "resample",
}

# UNVERIFIED — these labels are referenced by patterns but haven't been
# confirmed to work via ApplyDescription. Likely need different label
# forms. The pattern builders that use them currently ship best-guess
# labels and may fail at apply time.
UNVERIFIED_LABELS_USED_BY_PATTERNS: set[str] = {
    "Container Iteration",  # used by loop_over_*
    "Read Value At Index", "Write Value At Index",
    "Append", "Concat",  # ambiguous — multiple templates collide
    "Get Polygon Selection Data", "Get Vertex Selection Data",
    "Selection String Parser", "Selection String To Selection",
    "Pt Pos From Poly Ids", "Poly Normals From Poly Ids",
    "Poly Center From Poly Ids", "Color Alpha From Pt Ids",
    "Weights From Pt Ids", "Edges From Poly Ids",
    "Closest Point On Surface", "Ray",
    "Compose Vector 3", "Matrix From Axis",
    "Push Apart", "Line Get", "Assembler",
    "Add Control Point Along Spline", "Split Spline", "Sort Container",
    "Length",  # disambig: Vector Length works, plain Length doesn't
    "Object Import", "Cloner",
    "Set Property", "Get Property",
}


def is_verified(label: str) -> bool:
    return label in VERIFIED_LABELS


# ---------------------------------------------------------------------------
# Pattern registry — name → builder function. Populated by @pattern decorator.

PATTERN_REGISTRY: dict[str, dict[str, Any]] = {}


def pattern(name: str, *, description: str, params: Optional[dict] = None,
            min_nodes: int = 0, observed_in: Optional[list] = None):
    """Decorator: register a pattern builder."""
    def deco(fn):
        PATTERN_REGISTRY[name] = {
            "name": name,
            "description": description,
            "params": params or {},
            "min_nodes": min_nodes,
            "observed_in": observed_in or [],
            "build": fn,
        }
        return fn
    return deco


def list_patterns() -> dict[str, dict[str, Any]]:
    """Return registry without the build fn (JSON-safe)."""
    return {
        n: {k: v for k, v in p.items() if k != "build"}
        for n, p in PATTERN_REGISTRY.items()
    }


def build_pattern(name: str, **kwargs) -> dict[str, Any]:
    """Look up a pattern and call its builder. Returns the description dict
    that should be passed to GraphDescription.ApplyDescription."""
    if name not in PATTERN_REGISTRY:
        raise ValueError(
            f"Unknown pattern {name!r}. Available: {sorted(PATTERN_REGISTRY)}"
        )
    return PATTERN_REGISTRY[name]["build"](**kwargs)


# ---------------------------------------------------------------------------
# Patterns
#
# Each builder returns a list-or-dict ApplyDescription spec. Use list form
# when emitting multiple top-level nodes (ApplyDescription accepts a list
# of dicts — applies each independently).
#
# Naming convention: every node gets a $name with prefix matching the
# pattern (e.g. "loop_idx", "loop_carry_color"). This makes downstream
# scene_nodes_connect_ports calls deterministic.

@pattern(
    "loop_over_indices",
    description="Generic indexed iteration. Loop body runs N times.",
    params={"count": "int — total iterations", "carried": "list of (name, type, init)"},
    min_nodes=4,
    observed_in=["explode_spline_segments", "ivy_generator", "geo_feedback_loop"],
)
def _build_loop_over_indices(count: int = 10, carried: Optional[list] = None,
                              prefix: str = "loop") -> list[dict[str, Any]]:
    """Emit the minimum loop scaffolding: Range driving an iteration with
    optional carried state. Only verified labels (Range, Loop Carried Value).
    """
    carried = carried or []
    out: list[dict[str, Any]] = []
    out.append({"$type": "Range", "$name": f"{prefix}_range"})
    for var_name, var_type, _init in carried:
        out.append({"$type": "Loop Carried Value",
                    "$name": f"{prefix}_carry_{var_name}"})
    out.append({"$type": "Get Count", "$name": f"{prefix}_count"})
    return out


@pattern(
    "loop_over_polygons",
    description="Iterate every polygon of an input mesh. Body runs once per poly.",
    params={
        "mesh_input_ref": "$ref string pointing to the mesh-supplying node port",
        "carried": "list of (name, type, init) tuples for state to thread through",
    },
    min_nodes=8,
    observed_in=["partition_modifier", "explode_spline_segments"],
)
def _build_loop_over_polygons(mesh_input_ref: str = "$input",
                               carried: Optional[list] = None,
                               prefix: str = "polyloop") -> list[dict[str, Any]]:
    carried = carried or []
    out = [
        {"$type": "Get Polygon Selection Data", "$name": f"{prefix}_polysel"},
        {"$type": "Get Count", "$name": f"{prefix}_count"},
        {"$type": "Range", "$name": f"{prefix}_range"},
        {"$type": "Container Iteration", "$name": f"{prefix}_iter"},
        {"$type": "Read Value At Index", "$name": f"{prefix}_read_idx"},
    ]
    for var_name, var_type, _init in carried:
        out.append({"$type": "Loop Carried Value",
                    "$name": f"{prefix}_carry_{var_name}"})
    return out


@pattern(
    "loop_over_points",
    description="Iterate every point of an input mesh.",
    params={"mesh_input_ref": "$ref string"},
    min_nodes=6,
    observed_in=["squiggle_spline (analogous spline-points pattern)"],
)
def _build_loop_over_points(mesh_input_ref: str = "$input",
                             prefix: str = "pointloop") -> list[dict[str, Any]]:
    return [
        {"$type": "Get Vertex Selection Data", "$name": f"{prefix}_vsel"},
        {"$type": "Get Count", "$name": f"{prefix}_count"},
        {"$type": "Range", "$name": f"{prefix}_range", "from": 0, "step": 1},
        {"$type": "Container Iteration", "$name": f"{prefix}_iter"},
        {"$type": "Read Value At Index", "$name": f"{prefix}_read_idx"},
    ]


@pattern(
    "loop_over_spline_segments",
    description="Iterate spline line segments. Common for spline modifiers.",
    params={"spline_input_ref": "$ref"},
    min_nodes=6,
    observed_in=["squiggle_spline", "balloon_inflate.angle", "balloon_inflate.length"],
)
def _build_loop_over_spline_segments(spline_input_ref: str = "$input",
                                      prefix: str = "splineloop") -> list[dict[str, Any]]:
    return [
        {"$type": "Get Count", "$name": f"{prefix}_count"},
        {"$type": "Range", "$name": f"{prefix}_range", "from": 0, "step": 1},
        {"$type": "Line Get", "$name": f"{prefix}_lineget"},
        {"$type": "Container Iteration", "$name": f"{prefix}_iter"},
        {"$type": "Assembler", "$name": f"{prefix}_assembler"},
    ]


@pattern(
    "reaction_diffusion_on_geometry",
    description="Two-buffer Gray-Scott reaction-diffusion. Memory nodes hold U/V across frames.",
    params={
        "feed_rate": "float (~0.055 typical)",
        "kill_rate": "float (~0.062 typical)",
        "diffusion_u": "float",
        "diffusion_v": "float",
        "iter_per_frame": "int",
    },
    min_nodes=12,
    observed_in=["squiggle_spline"],
)
def _build_reaction_diffusion(feed_rate: float = 0.055, kill_rate: float = 0.062,
                               diffusion_u: float = 1.0, diffusion_v: float = 0.5,
                               iter_per_frame: int = 1,
                               prefix: str = "rd") -> list[dict[str, Any]]:
    return [
        {"$type": "Memory", "$name": f"{prefix}_mem_u"},
        {"$type": "Memory", "$name": f"{prefix}_mem_v"},
        {"$type": "Get Property", "$name": f"{prefix}_get_u"},
        {"$type": "Get Property", "$name": f"{prefix}_get_v"},
        {"$type": "Arithmetic", "$name": f"{prefix}_diffusion_u"},
        {"$type": "Arithmetic", "$name": f"{prefix}_diffusion_v"},
        {"$type": "Arithmetic", "$name": f"{prefix}_reaction"},
        {"$type": "Blend", "$name": f"{prefix}_blend"},
        {"$type": "Set Property", "$name": f"{prefix}_set_u"},
        {"$type": "Set Property", "$name": f"{prefix}_set_v"},
        {"$type": "Push Apart", "$name": f"{prefix}_pushapart"},
        {"$type": "Resample", "$name": f"{prefix}_resample"},
    ]


@pattern(
    "surface_clinging_growth",
    description="Branches grow while clinging to a host surface. Per step: snap to surface, decide direction stochastically, advance.",
    params={
        "host_mesh_ref": "$ref to the host BaseObject port",
        "branches": "int — initial branch seeds",
        "max_depth": "int — recursion depth via stacked iterations",
    },
    min_nodes=20,
    observed_in=["ivy_generator (668 nodes total)"],
)
def _build_surface_clinging_growth(host_mesh_ref: str = "$host",
                                    branches: int = 20, max_depth: int = 7,
                                    prefix: str = "cling") -> list[dict[str, Any]]:
    out = [
        # Carry pos, dir, age, depth — 4 state vars
        {"$type": "Loop Carried Value", "$name": f"{prefix}_carry_pos"},
        {"$type": "Loop Carried Value", "$name": f"{prefix}_carry_dir"},
        {"$type": "Loop Carried Value", "$name": f"{prefix}_carry_age"},
        {"$type": "Loop Carried Value", "$name": f"{prefix}_carry_depth"},
        # Iteration driver
        {"$type": "Range", "$name": f"{prefix}_range"},
        # Surface snap + collision
        {"$type": "Closest Point On Surface", "$name": f"{prefix}_snap"},
        {"$type": "Ray", "$name": f"{prefix}_ray"},
        # Stochastic direction
        {"$type": "Hash", "$name": f"{prefix}_hash"},
        {"$type": "Arithmetic", "$name": f"{prefix}_dir_math"},
        # Frame construction
        {"$type": "Compose Vector 3", "$name": f"{prefix}_dir_vec"},
        {"$type": "Matrix From Axis", "$name": f"{prefix}_frame"},
        {"$type": "Compose Matrix", "$name": f"{prefix}_xform"},
        # Decision: continue / fork / terminate
        {"$type": "Compare", "$name": f"{prefix}_decide"},
        {"$type": "If", "$name": f"{prefix}_branch"},
        # Output accumulation
        {"$type": "Append", "$name": f"{prefix}_emit"},
    ]
    return out


@pattern(
    "stochastic_branching_decision",
    description="Hash-driven random fork — produces deterministic randomness per loop step.",
    params={"branches": "int — output branch count", "seed_input": "$ref to a unique-per-iter value (e.g. index)"},
    min_nodes=4,
    observed_in=["ivy_generator (29× hash)", "fractal_trees", "geo_feedback_loop"],
)
def _build_stochastic_branching(branches: int = 2, prefix: str = "stoch") -> list[dict[str, Any]]:
    out = [
        {"$type": "Hash", "$name": f"{prefix}_hash"},
        {"$type": "Compare", "$name": f"{prefix}_threshold"},
        {"$type": "If", "$name": f"{prefix}_if"},
    ]
    if branches > 2:
        out.append({"$type": "Switch", "$name": f"{prefix}_switch"})
    return out


@pattern(
    "spline_break_by_threshold",
    description="Insert control points wherever a per-point metric exceeds threshold; then split.",
    params={"metric": "'angle' | 'length'", "threshold": "float"},
    min_nodes=10,
    observed_in=["balloon_inflate.angle", "balloon_inflate.length"],
)
def _build_spline_break(metric: str = "angle", threshold: float = 30.0,
                        prefix: str = "break") -> list[dict[str, Any]]:
    return [
        {"$type": "Get Count", "$name": f"{prefix}_count"},
        {"$type": "Range", "$name": f"{prefix}_range", "from": 0, "step": 1},
        {"$type": "Line Get", "$name": f"{prefix}_lineget"},
        {"$type": "Angle" if metric == "angle" else "Length",
         "$name": f"{prefix}_metric"},
        {"$type": "Compare", "$name": f"{prefix}_thresh"},
        {"$type": "If", "$name": f"{prefix}_if"},
        {"$type": "Add Control Point Along Spline", "$name": f"{prefix}_addpt"},
        {"$type": "Split Spline", "$name": f"{prefix}_split"},
        {"$type": "Sort Container", "$name": f"{prefix}_sort"},
        {"$type": "Assembler", "$name": f"{prefix}_asm"},
    ]


@pattern(
    "spline_resample_with_displacement",
    description="Resample a spline at constant arc-length, then displace each sample by time-varying noise.",
    params={"sample_count": "int", "displacement_amount": "float"},
    min_nodes=8,
    observed_in=["balloon_inflate.electric_resample (51 nodes)"],
)
def _build_spline_resample_displace(sample_count: int = 100,
                                     displacement_amount: float = 10.0,
                                     prefix: str = "resamp") -> list[dict[str, Any]]:
    return [
        {"$type": "Resample", "$name": f"{prefix}_resample"},
        {"$type": "Get Count", "$name": f"{prefix}_count"},
        {"$type": "Range", "$name": f"{prefix}_range", "from": 0, "step": 1},
        {"$type": "Read Value At Index", "$name": f"{prefix}_read"},
        {"$type": "Time", "$name": f"{prefix}_time"},
        {"$type": "Noise", "$name": f"{prefix}_noise"},
        {"$type": "Step", "$name": f"{prefix}_step"},
        {"$type": "Transform Matrix", "$name": f"{prefix}_xform"},
        {"$type": "Assembler", "$name": f"{prefix}_asm"},
    ]


@pattern(
    "mesh_element_query_by_selection",
    description="Look up a named selection, bulk-extract attributes for the selected polys/points.",
    params={"selection_name": "str — the named selection on the input mesh",
            "attributes": "list of 'pos'|'normal'|'center'|'color'|'weight'"},
    min_nodes=6,
    observed_in=["edge_to_spline (69 nodes)"],
)
def _build_mesh_element_query(selection_name: str = "default",
                               attributes: Optional[list] = None,
                               prefix: str = "meshq") -> list[dict[str, Any]]:
    attrs = attributes or ["pos", "normal"]
    out = [
        {"$type": "Selection String Parser", "$name": f"{prefix}_parser"},
        {"$type": "Get Polygon Selection Data", "$name": f"{prefix}_getpolysel"},
        {"$type": "Selection String To Selection", "$name": f"{prefix}_sel"},
    ]
    attr_to_node = {
        "pos": "Pt Pos From Poly Ids",
        "normal": "Poly Normals From Poly Ids",
        "center": "Poly Center From Poly Ids",
        "color": "Color Alpha From Pt Ids",
        "weight": "Weights From Pt Ids",
        "edges": "Edges From Poly Ids",
    }
    for attr in attrs:
        if attr in attr_to_node:
            out.append({"$type": attr_to_node[attr], "$name": f"{prefix}_{attr}"})
    out.append({"$type": "Assembler", "$name": f"{prefix}_asm"})
    return out


@pattern(
    "selection_evolution_chain",
    description="Random select → grow → invert → store named — common between modeling steps.",
    params={"random_percent": "float 0..1", "grow_steps": "int",
            "store_as": "str — name for the stored selection"},
    min_nodes=5,
    observed_in=["city_generator (multiple chains)", "geo_feedback_loop"],
)
def _build_selection_evolution(random_percent: float = 0.3, grow_steps: int = 1,
                                store_as: str = "evolved",
                                prefix: str = "selevo") -> list[dict[str, Any]]:
    out = [
        {"$type": "Random Selection", "$name": f"{prefix}_rand"},
    ]
    for i in range(int(grow_steps)):
        out.append({"$type": "Grow Selection", "$name": f"{prefix}_grow_{i}"})
    out.extend([
        {"$type": "Invert Selection", "$name": f"{prefix}_inv"},
        {"$type": "Set Selection", "$name": f"{prefix}_store"},
    ])
    return out


@pattern(
    "per_vertex_property_storage",
    description="Tag vertices with named scalar/vector data for downstream reads.",
    params={"property_name": "str", "value_type": "'Float64'|'Vector'|'Color'"},
    min_nodes=2,
    observed_in=["squiggle_spline", "ivy_generator"],
)
def _build_per_vertex_property(property_name: str = "value",
                                value_type: str = "Float64",
                                prefix: str = "prop") -> list[dict[str, Any]]:
    return [
        {"$type": "Set Property", "$name": f"{prefix}_set"},
        {"$type": "Get Property", "$name": f"{prefix}_get"},
    ]


@pattern(
    "object_instancing_with_variation",
    description="Generate N parametric variations of a child object, driven by hash + index.",
    params={"count": "int", "child_object_ref": "$ref"},
    min_nodes=12,
    observed_in=["time_offset (106 nodes, doc-level graph)"],
)
def _build_object_instancing(count: int = 50, prefix: str = "inst") -> list[dict[str, Any]]:
    return [
        {"$type": "Object Import", "$name": f"{prefix}_obj"},
        {"$type": "Range", "$name": f"{prefix}_range"},
        {"$type": "Read Value At Index", "$name": f"{prefix}_idx"},
        {"$type": "Hash", "$name": f"{prefix}_hash"},
        {"$type": "Noise", "$name": f"{prefix}_noise"},
        {"$type": "Compose Vector 3", "$name": f"{prefix}_translate"},
        {"$type": "Compose Vector 3", "$name": f"{prefix}_rotate"},
        {"$type": "Compose Vector 3", "$name": f"{prefix}_scale"},
        {"$type": "Compose Matrix", "$name": f"{prefix}_xform"},
        {"$type": "Cloner", "$name": f"{prefix}_cloner"},
    ]


# ---------------------------------------------------------------------------
# Classification — given a graph node-vocabulary histogram, name the patterns

VOCABULARY_BY_FUNCTION = {
    "math_scalar": {"arithmetic", "add", "sub", "scale", "pow", "abs", "modulo",
                    "round", "clamp", "evaluate", "interpolate", "blend",
                    "step", "negate", "invert", "sign", "sum"},
    "math_vector": {"distance", "dot", "cross", "normalize", "length",
                    "distancetoline", "trigonometry", "angle", "convertdegrees"},
    "math_compound": {"composevector3", "composematrix", "decomposematrix",
                      "composecontainer", "decomposecontainer", "vectortofloat",
                      "matrixtovectors", "vectorstomatrix", "matrixfromaxis",
                      "transformvector", "transformmatrix", "transformpoint",
                      "transform_element", "maprange"},
    "stochastic": {"noise", "hash", "randomselection"},
    "time_state": {"time", "memory"},
    "logic": {"if", "compare", "switch", "booleannot", "booleanoperator", "or",
              "typeof", "filter", "active"},
    "loop_scaffold": {"loopcarriedvalue", "start", "end", "range", "getcount",
                      "containeriteration", "selectionrange"},
    "array_io": {"readvalueatindex", "readvalueatindex2", "writevalueatindex",
                 "get", "get_property", "set", "set_property", "append",
                 "append2", "buildfromvalue", "buildfromsinglevalue", "concat",
                 "erase", "insert", "sort", "sortcontainer", "aggregate",
                 "toarray", "arraynode", "arraybuilder", "arraycont",
                 "swaperase", "indexarrayfromstring",
                 "getsinglearrayfromcontainer", "polygonarrayget",
                 "polygonarrayset", "edgemap"},
    "selection_ops": {"buildselection", "setselection",
                      "selectionstringtoselection", "selectionoperator",
                      "selectionrange", "selectionstringparser",
                      "removeselection", "growselection", "shrinkselection",
                      "invertselection"},
    "geometry_modeling": {"extrude", "extrudeline", "inset", "subdivide",
                          "tessellation", "delete", "deletemeshcomponent",
                          "removengons", "connect", "connect_geometries",
                          "pushapart"},
    "geometry_spline": {"spline", "lineget", "lineset", "assembler",
                        "addcontrolpointalongspline", "splitspline",
                        "splinedistnode", "cachesplinenode",
                        "cacherailsplinenode", "resample", "sweepline"},
    "geometry_query_byid": {"ptidsfrompolyids", "ptposfrompolyids",
                            "selfrompolyids", "edgesfrompolyids",
                            "polynormalsfrompolyids", "polycenterfrompolyids",
                            "selfromptids", "polyidsfromptids",
                            "edgesfromptids", "vertexnormalsfromptids",
                            "coloralhpafromptids", "weightsfromptids",
                            "getpolygonselectiondata",
                            "getvertexselectiondata", "extractgeobytype"},
    "object_access": {"objectimport", "legacyobjectaccess", "baselistparameter",
                      "children", "cloner", "ray", "closestpointonsurface",
                      "sqrtrans", "vectrans", "sqrpart", "mat", "combine",
                      "matrix", "rot"},
    "visual": {"scaffold", "annotation", "group", "reroute", "floatingio",
               "text", "type"},
    "framework": {"context_externaltimeinput", "context_notime", "parambuilder",
                  "modelingoperator", "generategeometry", "defaultselections",
                  "coreNode"},
}


# Pattern signatures: required base names that, when present, suggest the pattern
PATTERN_SIGNATURES = [
    {
        "name": "reaction_diffusion_on_geometry",
        "required_min": [("memory", 2), ("arithmetic", 1), ("blend", 1)],
        "boosts": ["set_property", "get_property", "pushapart", "resample"],
    },
    {
        "name": "surface_clinging_growth",
        "required_min": [("closestpointonsurface", 1), ("ray", 1),
                         ("loopcarriedvalue", 3), ("matrixfromaxis", 1)],
        "boosts": ["transformmatrix", "hash", "composematrix"],
    },
    {
        "name": "spline_break_by_threshold",
        "required_min": [("addcontrolpointalongspline", 1), ("splitspline", 1)],
        "boosts": ["sortcontainer", "sort", "compare"],
    },
    {
        "name": "spline_resample_with_displacement",
        "required_min": [("resample", 1), ("splinedistnode", 1)],
        "boosts": ["step", "noise", "time", "transformmatrix"],
    },
    {
        "name": "mesh_element_query_by_selection",
        "required_min": [("selectionstringparser", 1),
                         ("getpolygonselectiondata", 1)],
        "boosts": ["ptposfrompolyids", "polynormalsfrompolyids", "assembler"],
    },
    {
        "name": "object_instancing_with_variation",
        "required_min": [("objectimport", 1), ("cloner", 1)],
        "boosts": ["sqrtrans", "vectrans", "multransform", "hash"],
    },
    {
        "name": "stochastic_branching",
        "required_min": [("hash", 3), ("compare", 2), ("if", 1)],
        "boosts": ["switch", "arithmetic"],
    },
    {
        "name": "loop_with_carried_state",
        "required_min": [("loopcarriedvalue", 1), ("range", 1), ("start", 1),
                         ("end", 1)],
        "boosts": ["readvalueatindex", "writevalueatindex", "getcount"],
    },
    {
        "name": "selection_evolution_chain",
        "required_min": [("randomselection", 1), ("setselection", 1)],
        "boosts": ["growselection", "invertselection"],
    },
    {
        "name": "fractal_recursion_via_stacking",
        "required_min": [],
        "note": "Only detectable at scene-tree level (multiple instances of "
                "same capsule template stacked under one parent), not from "
                "the graph itself.",
        "boosts": [],
    },
]


def _normalize_basename(b: str) -> str:
    """Strip canonical-ID prefixes so 'net.maxon.node.switch' counts as 'switch'.

    Some dissections emit fully-qualified basenames when a local name
    collides with another node in the graph. For classification, we want
    the simple basename. Pattern observed in Dual Mesh Modifier dissection
    (2026-04-29): ~half the switches were emitted as
    `net.maxon.node.switch@HASH` instead of `switch@HASH`.
    """
    # Strip canonical prefixes — keep only last segment
    if b.startswith("net.maxon.") or b.startswith("com."):
        return b.rsplit(".", 1)[-1]
    return b


def classify_graph_histogram(histogram: dict[str, int]) -> dict[str, Any]:
    """Given a {basename: count} histogram from a dissection, return:
      - function_class_distribution (fractions per class)
      - patterns_detected (list of pattern names with confidence)
      - loop_carried_state_count
      - probable_purpose (heuristic English description)
    """
    # Normalize: collapse 'net.maxon.node.switch' → 'switch' before counting
    normalized = {}
    for b, c in histogram.items():
        nb = _normalize_basename(b)
        normalized[nb] = normalized.get(nb, 0) + c
    histogram = normalized
    total = sum(histogram.values()) or 1
    # Function class distribution
    class_counts = {}
    for cls, names in VOCABULARY_BY_FUNCTION.items():
        class_counts[cls] = sum(histogram.get(n, 0) for n in names)
    distribution = {cls: round(c / total, 3) for cls, c in class_counts.items()}

    # Pattern detection
    detected = []
    for sig in PATTERN_SIGNATURES:
        ok = True
        for name, min_count in sig["required_min"]:
            if histogram.get(name, 0) < min_count:
                ok = False
                break
        if ok and sig["required_min"]:  # skip empty signatures
            boost_present = sum(1 for b in sig["boosts"] if histogram.get(b, 0) > 0)
            confidence = 1.0 if not sig["boosts"] else (
                0.6 + 0.4 * (boost_present / max(len(sig["boosts"]), 1))
            )
            detected.append({
                "name": sig["name"],
                "confidence": round(confidence, 2),
            })

    # Loop carried-state count
    carry_count = histogram.get("loopcarriedvalue", 0)

    # Probable purpose — heuristic
    if any(d["name"] == "surface_clinging_growth" for d in detected):
        purpose = "Surface-clinging branch/growth generator (ivy-style)"
    elif any(d["name"] == "reaction_diffusion_on_geometry" for d in detected):
        purpose = "Reaction-diffusion field on geometry (chemical/biological pattern)"
    elif any(d["name"] == "spline_break_by_threshold" for d in detected):
        purpose = "Threshold-based spline subdivision (break by metric)"
    elif any(d["name"] == "spline_resample_with_displacement" for d in detected):
        purpose = "Time-varying spline resampling/displacement (electric/wobble)"
    elif any(d["name"] == "object_instancing_with_variation" for d in detected):
        purpose = "Parametric object instancing (cloner with hash-driven variation)"
    elif any(d["name"] == "mesh_element_query_by_selection" for d in detected):
        purpose = "Mesh element query — bulk attribute extraction by selection"
    elif carry_count >= 5:
        purpose = f"Heavy iterative algorithm ({carry_count} carried state vars)"
    elif carry_count >= 1:
        purpose = "Iterative algorithm with carried state"
    elif class_counts.get("geometry_modeling", 0) > total * 0.1:
        purpose = "Geometry modification pipeline"
    elif class_counts.get("geometry_spline", 0) > total * 0.1:
        purpose = "Spline modification pipeline"
    else:
        purpose = "Mixed-purpose graph"

    return {
        "node_count_total": total,
        "function_class_distribution": distribution,
        "loop_carried_state_count": carry_count,
        "patterns_detected": detected,
        "probable_purpose": purpose,
        "dominant_class": max(class_counts.items(), key=lambda kv: kv[1])[0]
        if class_counts else None,
    }
