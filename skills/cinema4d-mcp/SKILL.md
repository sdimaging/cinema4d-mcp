---
name: cinema4d-mcp
description: Develop, debug, test, and deploy Cinema 4D plugins or procedural scenes through an existing Cinema 4D MCP connection. Use for C4D SDK, Fields, spline/cache, Scene Nodes, viewport, transport, and plugin lifecycle issues.
---

# Cinema 4D MCP

Use the maintained cinema4d-mcp knowledge base to avoid rediscovering host quirks.
Stay within the user's task: review does not authorize scene changes; debugging
does not authorize discarding artist work or publishing a release. Honor existing
restart authorization without asking again for each routine step.

## Establish the environment

Read the project's working rules and current handoff/log. Identify the installed
plugin, registered IDs, source revision, SDK target and open documents. Ping the
existing bridge for PID/version/build; a listening socket does not prove that
main-thread requests run. Prefer exposed tools; inspect their schemas or the
actual bridge source before using raw command names. Do not install/reconfigure
a bridge or relax bind/auth/safe-mode settings incidentally.

The archive's current tested target is C4D 2026.3, not a universal requirement
for other projects. Use the project's exact local SDK/examples before importing
older API idioms. Never reuse another product's registered IDs. Compile success,
installed hash, registration, API tests, actual UI and art approval are distinct.

## Retrieve knowledge progressively

`docs/c4d_2026_api_gotchas.md` in the cinema4d-mcp checkout is canonical and
retains every numbered discovery, including corrections. Read
[knowledge routing](references/knowledge.md) to select relevant entries. Do not
load the full archive by default or substitute a stale copied summary.

- With knowledge tools exposed, inspect `cinema4d_knowledge_index`, then use
  `cinema4d_knowledge_get` / `cinema4d_knowledge_search` according to their schemas.
- Otherwise use `rg` in the checkout or the read-only helper:
  `python <skill>/scripts/knowledge.py --list`, `--id 132 133`,
  `--search "vertex map"`, or `--catalog`. Use `--repo <checkout>` if needed.
  This helper reads files only; it never pings C4D or executes a recipe.
- Read selected entries completely, including amendments and evidence limits.
  Historical workarounds are not blanket requirements.

## Choose the workflow

- Socket, timeout, restart or DLL deployment: read
  [session safety](references/session.md).
- Stale controls, disappearing geometry, Fields, source/collider dependencies,
  native publication or performance claims: read
  [debugging and acceptance](references/qa.md).
- Scene Nodes: read the checkout's `docs/scene_nodes_doctrine.md` and the
  relevant guide/recipe from the catalog. Start with a tiny correct graph;
  inspect runtime schemas and transactions before bulk artist-graph edits.

## Leave a usable checkpoint

Record the symptom, smallest repro, installed build, rejected candidates,
correction, checks and limits in the project log. Preserve raw outer execution
results alongside assertions. Add reusable demonstrated findings to the
canonical gotchas; label hypotheses explicitly. Stage narrowly in shared
worktrees. This skill grants no additional publishing or mutation authority.
