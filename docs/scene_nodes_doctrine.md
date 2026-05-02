# Scene Nodes Doctrine

**A serious Scene Nodes system is grown, not dumped.**
Every production graph starts as a tiny ugly proof with verified data flow.

---

## The principle

A great painting starts with a single stroke. A great 3D model starts with a low-poly blockout. A great Scene Nodes graph starts with **4-5 nodes that produce one visible result**.

The failure mode is universal: generate 188 nodes, viewport shows nothing, debug unknown node + unknown port + unknown bridge + unknown cache + unknown datatype simultaneously. There's no anchor point. Everything is suspect. Hours disappear.

The winning mode is the inverse: a tiny working seed, then add one concept at a time, with verification after each addition.

---

## The 6-step workflow

1. **Build the smallest meaningful graph.** 4-5 nodes max. One input, one output, one visible result. No options, no controls, no AM exposure yet.
2. **Verify every connection.** Not "the graph exists" — *"data flows and the viewport proves it."*
3. **Expose one control.** Make one AM parameter drive one inner-node value. Confirm it works in the editor by sliding the value.
4. **Save that as a named recipe.** This becomes the seed, not disposable scratch.
5. **Add one concept at a time.** Sampling → filtering → orientation → masking → animation. After each addition, return to step 2.
6. **Screenshot/compare after each expansion.** If it breaks, the failure is in the *last small step*, not hidden somewhere in 188 nodes.

---

## How this applied to the C++ bulk-swap shim

The same principle drove the C++ work that ships in this repo:

| Iteration | What it did | Why it shipped before the next |
|---|---|---|
| **Scaffold** | Bridge contract only — no mutation surface | Proved Python ↔ C++ message routing + audit format |
| **Preflight** | Validates each spec; refuses mutation if any fail | Proved graph open + walk + child-id check |
| **v1 (AddChild)** | Creates MY node next to OG, no wiring | Proved transaction-per-spec + GraphNode return value |
| **v2 (mirror inputs)** | MY parallel-reads OG's input wires | Proved port walk + port-level Connect |
| **v3 (atomic remove+rewire)** | OG removed + downstream rewired in one tx | Proved atomic-transaction semantics — the production-grade pattern |

If we'd shipped v3 directly, debugging interactions between mirror + atomic transaction would have been blind. Instead each layer was verified end-to-end before the next was added. Same exact principle as the Scene Nodes workflow above — different surface, same discipline.

---

## How it applies to your graphs

- **New recipe:** start with the bare-graph version (4-5 nodes that produce a visible result), save as the seed. Production-tuned version (full controls, animation, masking) comes after.
- **Replicating someone else's graph:** swap one node 1-1, verify deformation still works, repeat. Don't try to rebuild the whole thing in parallel.
- **Adding new functionality:** isolate the new concept in its own tiny graph first; only integrate after it works standalone.
- **Debugging a broken graph:** decimation by deletion. Remove half, see if the failure persists, narrow until you find the breaking node. Same principle — work in small verified subsets.

---

## What this is NOT

- **Not "premature optimization."** Tiny ugly is functional + complete for its scope, just minimal in extent.
- **Not "ship MVP and walk away."** The seed is the foundation for the eventual production graph.
- **Not "skip the hard parts."** The hard parts get sequenced AFTER the easy parts work — they're never skipped.

---

## When you find yourself debugging blind

Stop. The graph is too big for the level of verification you've done. Cut back to the last known-working state. Add ONE thing. Verify. Repeat.

This is the methodology. Every long-form Scene Nodes session that goes well follows it. Every one that goes badly skipped it.
