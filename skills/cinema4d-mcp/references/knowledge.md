# Knowledge routing

`scripts/knowledge.py --list` indexes every numbered gotcha dynamically;
`--id N` prints the complete entry. `--catalog` reads the maintained manifest
of guides, Scene Nodes studies, recipes and data. No duplicated archive.

| Task | Start with gotchas | Other sources in the checkout |
|---|---|---|
| Transport, scripts, logs, restart | 97, 100, 102, 103, 113, 114, 128–130, 134 | `docs/weavr_mcp_hardening_2026-09-04.md`, `docs/USAGE_GUIDE.md`, sync scripts |
| Native build, ownership, threading | 9, 27–29, 33, 79–81, 95, 99, 107, 112, 120–124, 132 | `docs/c4d_2026_3_sdk_migration_guide.md`, pinned SDK |
| UI commits, caches, blank view | 5–6, 84, 101, 125–127, 131–134 | Project repro, passive traces and actual UI acceptance |
| Splines, maps, geometry inputs | 10, 13, 88, 90, 104, 111, 116, 118, 135 | SDK spline/Field APIs, independent evaluated-mesh fixtures |
| Fields, MoGraph, particles | 16, 18, 78–80, 89, 91–93, 109–110 | Catalog and project timing/state tests |
| Rendering / custom GPU | 5–6, 12, 15, 75–77, 82–86, 105, 117, 130, 133 | Camera/shading introspection; renders are not untouched screens |
| Scene Nodes / assets / capsules | Search: graph, ports, capsule, Neutron, asset | `docs/scene_nodes_doctrine.md`, `docs/scene_nodes_guide.md`, `docs/scene_nodes_capsule_theory.md`, `docs/scene_nodes_advanced_studies/` |

Follow supersession notices. #54 is explicitly incorrect and points to #55.
#124 describes a loaded-DLL rename mechanism, not the recommended normal
deployment workflow. #52 is historical: inherited UNDEF alone does not prove
invisibility. Validate the hierarchy before changing artist visibility.

The helper locates the checkout from its real file location, supplied `--repo`,
`C4D_MCP_REPO`, or the user's `Projects/cinema4d-mcp`. If unavailable, use the
knowledge connector or request the location. Do not invent entries or claim
the full archive was reviewed from headings alone.

Current bridge section extraction can clip on body lines beginning with `#`
and still report `truncated: false` (#136). Prefer complete local entry reads
for authoritative diagnosis; otherwise retrieve the whole document and verify
the intended section through its next real heading. Do not trust the flag alone.
