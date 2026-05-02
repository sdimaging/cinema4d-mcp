# Scene 19 — Oct-Tree Distribution (Tutorial Version)

**Studied:** 2026-05-01
**Source:** `Oct-Tree_Distribution_01/Oct-Tree_Distribution_Tutorial-Version.c4d`
**Brand-new C4D 2026 capability:** Distribution generator + Advanced Cloner mode

---

## TL;DR

C4D 2026 added a brand-new **Distribution generator** (plugin ID 190000011) that ships **5 stock built-in distribution algorithms** (Cannonball, Partition, Projected 2D Grid, Smart Hex, Spline Bead) AND can host **artist-custom Scene Nodes graphs** as alternative scatter algorithms. The new MoGraph Cloner has an "Advanced" Distribution mode wired to the Distribution generator via param [2115].

The the reference build **Oct Tree** scene in this folder is a CUSTOM artist-built distribution (NOT a stock variant) that demonstrates how to author your own. It implements octree spatial subdivision in 65 SN nodes — small enough to study end-to-end.

This session ran a controlled experiment: probed the 5 stocks + Oct Tree, swapped them through a Cloner, captured screenshots, enumerated all the new param IDs. Three new memories written for the cinema4d-mcp toolkit.

---

## Spenser's verbatim notes

> this one is brand new - i havent seen it but excited to learn it and for you to learn it

> this uses the NEW c4d distribution too so thats something you probably dont have documented in 2026 c4d added a new advanced cloner distribution

> it allows for scene node distribution

> ya the current MCP probably has no idea about the new distribution types there are cannon ball distribution and many others

> octree is just a custom scene node distribution built by this artist so let study all the distributions the new advanced cloner ops and obviously the oct tree set up itself and ins and outs

> if I go to edit asset as group it opens up all of the nodes for the other distributions like cannon ball - if I just double click on the oct tree distribution it opens up the graph bc I think its a scene node distribution renamed to the oct tree distribution so a custom build is what Im saying and the others are provided by MAXON so they're pretty much capsules - but if you look at all of the nodes for each you can get a pretty good analysis of the workings for each and that would provide a solid ref for distribution building I think

> toggle the distribution type yourself dive into the workings of it adjust things and understand it please

---

## What's new in C4D 2026 (this session's discoveries)

### 1. Distribution generator (plugin 190000011)

A new Scene Nodes generator type. One plugin, two authoring forms:

| Form | Examples | How to inspect |
|------|----------|----------------|
| **Capsule** (Maxon stock) | Cannonball, Partition, Projected 2D Grid, Smart Hex, Spline Bead | Right-click → **Edit Asset as Group** (double-click only opens the params editor) |
| **Custom-graph** (artist-built) | Oct Tree (the reference build in this scene) | **Double-click** opens the SN graph directly |

All variants share the SAME plugin host but differ in their internal SN graph. All produce the same kind of output: a `composedistributioncontainer` that the Cloner consumes.

### 2. New Cloner advanced section (plugin 1018544)

Verified DescIDs via enumerate_descids:

| Param ID | Label | Purpose |
|---------:|-------|---------|
| **[2107]** | Distribution Type | Cycle: 0=Basic, 1=Advanced |
| **[1028]** | Clones from Distribution | Toggle whether the Cloner uses a Distribution generator |
| **[2115]** | Distribution | BaseObject link to a Distribution generator (the new bridge) |
| **[5511]** | Display | Cycle: 0=None, 1=Weight, 2=UV, 3=Color, 4=Index — viewport debug overlay |
| **[1500]** | Color | Per-instance color override |
| **[1025]** | Instance Mode | (newly labeled in advanced mode) |
| **[1026]** | Viewport Mode | (newly labeled in advanced mode) |
| [1501] / [1204] | (hidden conditional) | Surface only under specific UI state |

### 3. The 6 distribution algorithms (node-vocab analysis = building reference)

| Variant | Form | Total nodes | LCVs | Algorithm signature |
|---------|------|------------:|-----:|---------------------|
| **Oct Tree** (custom) | bare-graph | **65** | 1 | `bb` (octree partition) + `closestpointonsurface` + `composedistributioncontainer` — adaptive octree scatter |
| **Smart Hex** (capsule) | Maxon stock | 210 | 1 | 103 arithmetic + 14 switch (hex orientation dispatch) — hex grid scatter |
| **Projected 2D Grid** (capsule) | Maxon stock | 221 | 1 | 67 arithmetic + 11 normalize + 4 cross products — 3D-to-2D projection scatter |
| **Cannonball** (capsule) | Maxon stock | 251 | 1 | 118 arithmetic + 27 switch + 8 readvalueatindex2 — stacking-pattern scatter |
| **Spline Bead** (capsule) | Maxon stock | 653 | 9 | 87 if + 50 compare + 28 booleanoperator — bead-along-spline with rich collision/spacing |
| **Partition** (capsule) | Maxon stock | **1268** | 6 | 427 arithmetic + 79 readvalueatindex + 36 distance — full mesh-element-query with bulk attribute extraction |

These node-vocab signatures ARE the building reference Spenser asked for — when designing a new scatter recipe, look up the closest stock by visual signature and use its node vocabulary as a starting palette.

---

## Experiments run + visual signatures

### Experiment A: Distribution OFF vs ON

| State | Result | Screenshot |
|-------|--------|------------|
| `[1028]=0` Distribution OFF | Cloner shows 7 child shapes scattered randomly (legacy Object mode, no target) | `frames/exp_A1_distribution_OFF.png` |
| `[1028]=1` ON, `[2115]=Oct Tree` | **Beautiful HIERARCHICAL OCTREE PATTERN** — clustered spheres with finer subdivisions in denser regions | `frames/exp_A2_distribution_ON_OctTree.png` |

### Experiment B: 6-variant swap

| Variant | Result | Notes |
|---------|--------|-------|
| Oct Tree | Hierarchical octree clusters (see A2) | Works out-of-box (input geometry baked into custom graph) |
| Cannonball | **EMPTY** | "Selected Objects" link unset — deeply-nested floatingio path, NOT Python-settable per FIO gotcha |
| **Partition** | **KITBASH/MECHA effect** — partitioned regions, each gets a different child shape | Killer use case: greebles, instrument panels, mechanical assemblages |
| Projected 2D Grid | EMPTY | Same input-link issue |
| **Smart Hex** | DENSE HEX GRID of varied shapes | Camera framing was inadequate — generates clones at scale that engulfs the scene |
| Spline Bead | EMPTY | Needs spline input wired |

The 3 empty variants need their `Selected Objects` link set via UI — the deep `777.<long-path>` floatingio paths are not Python-settable (matches the existing FIO/PORTLIST gotcha memory).

---

## Recipe extracted

### R30 — Author a custom Distribution generator

**Priority:** HIGH — unblocks shipping any procedural scatter as an artist-friendly Cloner-compatible tool.

**Conceptual ingredients:**
- Scene Nodes graph hosted in plugin 190000011 (Distribution)
- Standard Cloner-facing input ports: `clonerchildrenin`, `globalclonermatrixin`, `localclonermatrixin`
- Standard time inputs: `time`, `frame`, `fps`, `nimbus`, `globalmatin`
- Custom algorithm core (octree / blue-noise / curvature-driven / RD / whatever fits)
- Output: `composedistributioncontainer` node terminating in `distdataout` port

**Reference implementation:** Oct Tree Distribution by the reference build — 65 nodes, demonstrates octree spatial subdivision + closestpointonsurface for surface-aware scatter. Bare-graph style (no AM exposure).

**Implementation paths (per the "the reference build scenes are awareness data" feedback):**
- *Bare-graph* — fastest to validate, what Oct Tree does
- *Capsule wrap* — what Maxon's stocks do; surface AM controls via FloatingIO at root 777
- *Hybrid* — bare-graph + a thin capsule wrapper with just the few user-facing knobs you actually need

**Next steps for artist-shipping:**
1. Author algorithm in bare-graph form first (validate visually)
2. Identify the 3-5 controls artists need (count, density, scale, seed, source-object)
3. Wrap as capsule with FloatingIO ports for those controls
4. Match the Maxon stock pattern: Asset Version 1002 at `777.1852142638...`

---

## Tooling action items for cinema4d-mcp

- Add plugin 190000011 to `dissect_capsule` scan + asset registry
- Document new Cloner descids (this study captures them)
- Crawl `scene_nodes_list_assets(source='repository', filter_substring='distribution')` to identify the 5 stock asset IDs
- Add a deep-floatingio setter helper that knows how to traverse `777.<long-path>` paths via UI commands rather than Python (likely needs CallCommand or scripted UI action)
- Consider a "Distribution sandbox" helper: create N distributions, plug in a torus, screenshot each — useful visual reference
- Update `c4d_2026_api_gotchas.md` with the Distribution + Advanced Cloner section

## Memories saved this session

- [reference_c4d_2026_distribution_generator_190000011.md](../../../../../home/spenser/.claude/projects/-mnt-c-Users-Spenser-Dickerson-Projects-LightPainter/memory/reference_c4d_2026_distribution_generator_190000011.md) — plugin overview + Cloner wiring
- [reference_c4d_2026_distribution_family_inventory.md](../../../../../home/spenser/.claude/projects/-mnt-c-Users-Spenser-Dickerson-Projects-LightPainter/memory/reference_c4d_2026_distribution_family_inventory.md) — 5 stock + Oct Tree custom inventory
- [reference_c4d_2026_cloner_advanced_distribution_descids.md](../../../../../home/spenser/.claude/projects/-mnt-c-Users-Spenser-Dickerson-Projects-LightPainter/memory/reference_c4d_2026_cloner_advanced_distribution_descids.md) — new Cloner param IDs
- [reference_c4d_2026_distribution_capsule_vs_custom_graph.md](../../../../../home/spenser/.claude/projects/-mnt-c-Users-Spenser-Dickerson-Projects-LightPainter/memory/reference_c4d_2026_distribution_capsule_vs_custom_graph.md) — capsule vs bare-graph distinction

---

## Operational notes

- Scene was modified during the experiment (added DBG_Cloner_* + StudyCam + linked Cannonball etc.) — these will not persist unless saved
- RS Dome Light removed (clutter strip per the rule)
- Doc CLOSED via KillDocument at end of session per the RAM-hygiene rule
