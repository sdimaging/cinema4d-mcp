# Strategy — Two Lanes (locked 2026-04-30)

After Phase A's deep dive into Scene Nodes / NodeTemplate publishing exposed Maxon's "internal-only" wall, we lock in a two-lane strategy per GPT review. **The lanes have different risk profiles and different ship criteria.**

## Lane 1 — Research

**Goal:** answer "can we truly unlock native Scene Nodes authoring?"

**Risk profile:** exploratory. May fail. Findings are internal evidence + gotchas docs, never production code.

**What we ship from this lane:**
- ✅ Knowledge — the gotchas doc (#27-37 already), guides, design docs
- ✅ Reusable infrastructure — the C++ helper bridge architecture, `SpecialEventAdd` dispatch, build automation
- ✅ Negative results documented clearly — saves anyone else time

**What we never ship:**
- ❌ Calls into private binary symbols (`add_port` from `c4d_nodeeditor.xdl64`)
- ❌ Ghidra/IDA-extracted offsets
- ❌ Private command IDs that aren't in any documented framework
- ❌ Anything that depends on Maxon's binary layout staying stable across versions

**Continued work in this lane (low priority, no production dependency):**
- Phase A.2.1 fix: proper `CommandObserverInterface` subscription via singleton accessor (vs the abstract `Create()` that failed). Definitive empirical answer on whether right-click "Add Input" goes through the maxon command framework.
- Phase B (true NodeTemplate publishing via `MAXON_DECLARATION_REGISTER(BuiltinNodes, ...)`) — only if Maxon developer access opens up to clarify the right approach.
- Reverse engineering for **research/curiosity only** — keep notes, never ship.

## Lane 2 — Product

**Goal:** ship valuable creative tools without waiting on hidden Maxon internals.

**Risk profile:** product-grade. Stable APIs only. Distributable as polished plugins.

**Architecture (the unlock):** custom C4D Generator plugin with **standard `.res` description and UserData controls**, internally driving an embedded Scene Nodes graph as the computational backend. Mirrors what Luminary, Spikr2, MechFlow already do — proven pattern.

```
┌─────────────────────────────────────────────────────┐
│  User-facing: standard C4D Generator object         │
│   - Plugin ID we own                                 │
│   - Standard .res description (proven path)          │
│   - AM exposes our UserData (full control)           │
│   - Distributed as normal .cdl64                     │
│                                                      │
│  Backend: Scene Nodes graph                          │
│   - Built via GraphDescription.ApplyDescription      │
│     using all 802 Maxon NodeTemplates as building   │
│     blocks (already indexed)                         │
│   - Wired via BeginTransaction + Connect + Commit    │
│     (battle-test 11/11 green)                        │
│   - UserData values bridge into inner-node values    │
│     in GetVirtualObjects() at eval time              │
│   - Resulting geometry/spline returned to host       │
└─────────────────────────────────────────────────────┘
```

**What this gets us:**
- ✅ All 802 Maxon NodeTemplates as primitives (free; already indexed)
- ✅ Full AM control via standard C4D Description (proven across 4 of user's plugins)
- ✅ Imperative graph API for connections (proven working)
- ✅ Standard plugin distribution (`.cdl64` install, no Asset Browser dependency)
- ✅ No FloatingIO wrestling — sidesteps the `Add Input` wall entirely
- ✅ No reliance on undocumented APIs

**What we lose vs Maxon's NodeTemplate publishing:**
- ❌ Asset doesn't appear in Asset Browser as a `.c4dnodes` (appears in Object Manager → Add → Generators)
- ❌ Doesn't ship via Maxon's Asset Browser ZIP database (we ship via normal plugin install)

For shipping creative tools, this tradeoff is acceptable. **Distribution via the plugin install pipeline works.**

## Lane separation rules

| Question | Answer |
|---|---|
| Did the discovery come from the maxon command framework / SDK headers? | Either lane can use it |
| Did the discovery come from a binary string / Ghidra / IDA / private offsets? | Research lane only — never product |
| Does the code path require runtime symbol resolution into `c4d_*.xdl64`? | Research lane only |
| Does the code path use ApplyDescription / BeginTransaction / Connect / GraphDescription? | Both lanes (proven public API) |
| Does the code use standard `BaseObject`/`Description`/`UserData`? | Product lane |

## Phase B' (revised) — the product lane

```
B.0'  Minimal Generator skeleton
      - New plugin ID (next sibling: 1057846)
      - Plugin type: PLUGINTYPE_OBJECT (mirroring Spikr2)
      - .res with one Float UserData
      - GetVirtualObjects returns a built-in primitive scaled by UserData
      - Verifies the plugin shape works end-to-end (no SN backend yet)

B.1'  SN backend wired
      - GetVirtualObjects creates an embedded SN graph
      - Wires UserData → a known-working node parameter
      - Returns the SN graph's output geometry
      - Confirms the bridge: AM param → inner SN node → final geometry

B.2'  Multi-param + complex inner graph
      - 3+ UserData params (scalar, vector, dropdown)
      - More elaborate SN graph (procedural scatter, hash threshold, etc.)
      - Proves the framework scales

B.3'  Reusable framework
      - Header file + macro to declare new SN-backed generators
      - ~50 lines per new generator type
      - Foundation for shipping a family of products
```

Each step is independently valuable + testable. ~6-10 hours total but each step ships value.

## Maxon developer API request

This 2-lane work strengthens the case substantially:
- ✅ Cinema4d-mcp public WIP (mature now)
- ✅ Luminary, Spikr2, MechFlow plugins
- ✅ Phase A architectural research (37 cross-plugin gotchas — actual engineering output)
- ✅ Proposed Phase B' framework (genuinely novel — SN as backend, not as artist surface)

Worth re-applying once one or two B.x' steps land + are public.
