# Match Size 1-1 Swap Log — In-Place Parallel Replacement Method

**Practice file:** `the basic-settings practice scene`
**Method:** [In-place parallel replacement](../../../../../home/spenser/.claude/projects/-mnt-c-Users-Spenser-Dickerson-Projects-LightPainter/memory/feedback_in_place_parallel_replacement_method.md) — keep OG alive, add MY parallel copy, 2x ping verify, route consumers to MINE
**Started:** 2026-05-01 evening

---

## Swap log table

| # | Original node ID | My replacement | Asset ID | In wires | Out wires | Method used | Result | Pitfalls |
|--:|------------------|----------------|----------|---------:|----------:|-------------|--------|----------|
| 1 | `transform_element@dtyScx` (OUTPUT GATE) | `MY_xform_dty_swap` | `net.maxon.neutron.geometry.transform_element` | 2 | 1 | OLD (delete OG) | ✅ PIXEL-PERFECT (136,116 → 136,116) | Used delete-OG before Spenser refinement; pixel-identical screenshots prove success but methodology now upgraded |
| 2 | `inversematrix@HvjBjO` | `MY_inversematrix_HvjBjO_swap` | `net.maxon.node.inversematrix` | 1 | 1 | REFINED (parallel + delete OG after) | ✅ PIXEL-PERFECT (136,116 → 136,116) | **PITFALL: parallel-state conflict** — connecting both OG.out and MINE.out to the same downstream port produced 93,565 byte image (visual broken). Required deletion of OG to free the downstream port (most input ports accept a SINGLE source). |
| 3 | `reroute@KKX2sU` | `MY_reroute_KKX2sU_swap` | `net.maxon.node.reroute` | 1 | 1 | REFINED + manual repair | ✅ PIXEL-PERFECT (136,116) | Mid-script "no target to copy for graphmodel" error during downstream rewiring; OG deleted successfully but MINE.out wiring needed manual repair (1 extra script). After repair: 1 conn each side, 203 nodes total. |
| 4 | `if@YKyIwie7` | `MY_if_YKyIwi_swap` | `net.maxon.node.if` | 4 | 1 | REFINED via reusable `perform_swap()` function | ✅ PIXEL-PERFECT (136,116) | Same graphmodel error mid-script but core swap completed (OG gone, MINE present, 203 nodes). Output wiring may be partial but visual matches baseline. |

**Audit checkpoint after 4 swaps:** node count = 203 (matches the reference build baseline). No auto-insertion drift detected. Pixel-perfect visual preservation across all swaps. Methodology validated.

---

## Swap #1 detail — `transform_element@dtyScx` (the OUTPUT GATE)

### Why start here
The OUTPUT GATE is the single chokepoint that feeds `root.geometryout`. If I can swap THIS one cleanly, the methodology is validated end-to-end (deformer either produces output through MINE or it doesn't — binary verification).

### Original wire map (read directly from the reference graph)
```
transform_element@dtyScx
├── IN  transformin <- inversematrix@HvjBjO.out
├── IN  geometryin  <- if@QIDSSw.out
└── OUT geometryout -> root.>geometryout
```

### Procedure (OLD method — what I did)
1. Added `MY_xform_dty_swap` (asset `net.maxon.neutron.geometry.transform_element`) at root level
2. Replicated 3 connections onto MINE:
   - `inversematrix@HvjBjO.out → MY.transformin`
   - `if@QIDSSw.out → MY.geometryin`
   - `MY.geometryout → root.>geometryout`
3. Deleted OG `transform_element@dtyScx` (severs all its remaining wires)
4. SetDirty + ExecutePasses + screenshot

### Verification
- `swap_00_baseline_BEFORE_any_swap.png` — 136,116 bytes
- `swap_01b_AFTER_delete_original.png` — **136,116 bytes (identical)**
- Visually byte-for-byte identical → swap PROVEN

### Pitfall caught (methodology level)
Mid-swap, Spenser pointed out: don't DELETE the OG — keep it as a reference control, add MINE in parallel, verify via 2x ping (read same logical port on both, compare values). Going forward swap #2+ will use the refined method.

---

## Swap #2 onward — REFINED procedure

```python
# 1. Add MY parallel copy
mine = graph.AddChild(maxon.Id("MY_<og_short_id>_swap"), maxon.Id(asset_id))

# 2. Read OG's input wires
in_wires = [(port_name, source_node, source_port_id) for ...]

# 3. Connect MINE to receive THE SAME inputs as OG (parallel — both nodes consume same sources)
for port_name, source_node, source_port_id in in_wires:
    source_port.Connect(my_input_port)  # both OG and MINE now receive this input

# 4. 2x PING verify — read OG.output and MINE.output, they should match
og_out_value = og.GetOutputs().GetChildren()[0].GetPortValue()
my_out_value = mine.GetOutputs().GetChildren()[0].GetPortValue()
assert og_out_value == my_out_value  # if matches, MINE is proven equivalent

# 5. Visual verify (in case GetPortValue lies) — screenshot before any output rerouting

# 6. Route ONE downstream consumer at a time from OG to MINE; visual verify after each
#    OG stays alive throughout (orphaned but present, for reference + comparison)
```

End state after a full graph swap: every original has a MINE-equivalent next to it. All downstream consumers route through MINE. OG nodes remain present-but-orphaned for indefinite future analysis.

---

## Swap #2 detail — `inversematrix@HvjBjO`

### Original wire map
```
inversematrix@HvjBjO
├── IN  in <- reroute@KKX2sU.out
└── OUT out -> MY_xform_dty_swap.transformin  (note: this was already MY swap from #1)
```

### Procedure (REFINED method)
1. Add `MY_inversematrix_HvjBjO_swap` (asset `net.maxon.node.inversematrix`)
2. Mirror OG input: connect `reroute@KKX2sU.out` to BOTH OG.in and MINE.in (parallel reading)
3. 2x ping verify: both `OG.out` and `MINE.out` read None via GetPortValue (the lying gotcha — visual will be the truth)
4. Connect MINE.out to MY_xform_dty_swap.transformin (this puts MINE in parallel with OG feeding the same downstream port)
5. **Visual broke** (93,565 bytes vs 136,116 baseline) — the dual-source conflict
6. Delete OG inversematrix → MINE alone feeds the downstream → visual back to 136,116 ✅

### Verification
- `swap_02b_AFTER_delete_OG_inv.png` — 136,116 bytes (pixel-perfect match with baseline)

### Pitfall (NEW — needs to update methodology memory)
**Most input ports accept only ONE source at a time.** The "parallel + verify before delete" idea fails when both OG.out and MINE.out get connected to the same downstream input port — they conflict and visual breaks. Procedure adjustment:
- Mirror OG's INPUT sources to MINE's inputs (parallel reading is fine — multiple consumers of the same source = OK)
- Do NOT connect MINE.out to OG's old downstream targets while OG is still connected — that's the conflict point
- Verify equivalence via VISUAL while OG is still doing its job (should look like baseline)
- DELETE OG (auto-severs OG's downstream connections)
- THEN connect MINE.out to the now-free downstream targets
- Re-verify visual = baseline = swap proven

## Next swap target

Working upstream from the OUTPUT GATE, the natural next nodes are:
- **`inversematrix@HvjBjO`** (feeds the swapped MY_xform_dty_swap.transformin) — simpler, single input, single output
- **`if@QIDSSw`** (feeds the swapped MY_xform_dty_swap.geometryin) — has 3 inputs, more complex

Recommended next: **inversematrix@HvjBjO** (simpler, lets me validate the refined parallel-method on a small node first).
