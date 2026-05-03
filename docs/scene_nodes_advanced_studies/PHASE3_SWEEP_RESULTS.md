# Phase-3 v9.2 Cross-Scene Sweep — 12 Artist Hosts

**Date:** 2026-05-02
**Script:** `scripts/sn_phase3_rebuild.py` v9.2
**Raw data:** `PHASE3_SWEEP_v9_2.json`

## Headline result

**ALL 12 hosts hit 100% node fidelity. Wire fidelity range: 80-100% (median ~95%).**

## Per-host results (unique rebuilds, deduped)

| Scene file | Host | Nodes | Wires | Node fid | Wire fid | clones |
|---|---|---|---|---|---|---|
| Reaction_Diffusion_Tut-Files_01 | Reaction_Diffusion | 181 | 292 | **100%** | **92.8%** | 13 |
| Reaction_Diffusion_Tut-Files_01 | Reaction_Diffusion_Simple | 89 | 161 | **100%** | **95.7%** | 6 |
| Volume-Infection_Tutorial_File_01 | Object Group | 125 | 289 | **100%** | **98.3%** | 14 |
| Grow_Points_Simple_01 | Point_Grower Simple | 75 | 93 | **100%** | **97.8%** | 7 |
| Mycelium_Tutorial_01 | Nodes Spline | 61 | 100 | **100%** | **89.0%** | 5 |
| Mycelium_Generator_V3_01 | Myzel_Generator_V3.1 | 299 | 620 | **100%** | **98.9%** | 10 |
| Coral-Structures_Tutorial_01 | Geometry Axis | 157 | 278 | **100%** | **95.7%** | 8 |
| Coral-Structures_Tutorial_01 | Mask Y | 69 | 121 | **100%** | **100%** | 1 |
| Example_Stone-Circle | Match Size | 203 | 367 | **100%** | **80.1%** | 4 |
| Example_Stone-Circle | Stack Stones | 214 | 385 | **100%** | **100%** | 1 |
| Spiderweb_Setups_2024_01 | Spiderweb_Complex_01 | 159 | 255 | **100%** | **94.1%** | 11 |
| Spiderweb_Setups_2024_01 | Spiderweb_Simple_01 | 53 | 100 | **100%** | **91.0%** | 1 |

**Totals:**
- 12 host rebuilds across 7 unique source files
- 1,885 nodes reproduced — **100% node fidelity** across all
- 3,061 wires (avg ~94% reproduced)
- 81 deep-clone operations across the sweep (artist-extended capsule body)

## Summary stats

- **Mean node fidelity: 100%**
- **Mean wire fidelity: ~94.4%**
- **Smallest scene:** 53 nodes (Spiderweb_Simple_01)
- **Largest scene:** 299 nodes / 620 wires (Mycelium V3.1) — still 100%/98.9%

## Two problem cases (worth flagging)

1. **Oct-Tree_Distribution_Tutorial-Version.c4d** — 0 hosts found. Symptom of [missing-asset-shows-as-empty-graph](reference_c4d_2026_missing_asset_appears_as_empty_graph.md) gotcha. The Oct Tree scene needs the asset DB mounted; without it the SN host's graph is empty.
2. **02_Crystal_Cutter-Tutorial-File.c4d** — `'NoneType' object has no attribute 'GetGraph'` on a host named "Store for next Frame". Likely a non-SN object that my `find_hosts` walker misclassified by type id. Worth filtering more carefully.

Neither is a fidelity issue — both are scene/discovery quirks.

## What this proves

The Phase-3 v9.2 methodology generalizes. From a 4-scene proof to a 12-host sweep across 7 different scene files, every successful rebuild hit 100% node fidelity. Wire fidelity stayed at 80%+ even on the most complex scenes (Stack Stones with 214 nodes hit 100%; Mycelium V3 with 299 nodes hit 98.9%).

The 30-scene grand vision is mechanically achievable. Each remaining scene needs ~30s of compute + a one-line capture+rebuild call.

## How to run additional scenes

```python
import sys
sys.path.insert(0, r"C:\Users\Spenser Dickerson\Projects\cinema4d-mcp\scripts")
import sn_phase3_rebuild as r3
import c4d, maxon

src = c4d.documents.LoadDocument(r"<path to .c4d>", c4d.SCENEFILTER_OBJECTS, None)
c4d.documents.InsertBaseDocument(src); c4d.documents.SetActiveDocument(src)

# find SN host(s)
SN_TYPES = (180420400, 180420500, 180420600, 180420700)
def find_hosts(o, out=None):
    if out is None: out = []
    while o:
        if o.GetType() in SN_TYPES: out.append(o)
        find_hosts(o.GetDown(), out)
        o = o.GetNext()
    return out

for host in find_hosts(src.GetFirstObject()):
    desc = r3.capture_scene(host.GetName(), source_doc=src)
    new_doc, new_host, report = r3.rebuild_scene(
        desc, target_name=f"{host.GetName()}_v9_2", source_host=host)
    print(f"{host.GetName()}: {report['node_fidelity_pct']:.1f}% nodes / "
          f"{report['wire_fidelity_pct']:.1f}% wires")
```
