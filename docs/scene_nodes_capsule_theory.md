# Scene Nodes Capsule Theory

**Many "atomic-looking" Scene Nodes nodes are actually CAPSULES with internal sub-graphs.** This is the load-bearing insight for genuinely understanding (and rebuilding from scratch) any artist-authored Scene Nodes graph.

---

## The discovery (2026-05-02)

While decoding the `loop_scaffold` pattern in DRuckli's "Stack Stones" Scene Nodes deformer, port-walking the `loopcarriedvalue` node revealed:

```
loopcarriedvalue (NODE_KIND::NODE) — appears as a leaf when seen externally
└── INTERNAL CHILDREN (visible only when walking with NODE_KIND::NODE filter):
    ├── start             ← exposes LCV's IN ports to the body
    ├── end               ← sinks the body's outputs back to LCV's interface
    ├── connect_geometries ← the body's actual computation
    └── 0228e699...      ← anonymous sub-capsule (per-iteration transform master)
```

**`loopcarriedvalue` is a capsule.** It has an interior. The "self-references" you see in the wire dump (e.g. `lcv.current._0 → lcv.current._0`) are wires INSIDE the capsule connecting the LCV's interface to its body's `start`/`end` markers.

---

## Universal: the capsule class is huge

After confirming on `loopcarriedvalue`, the same pattern surfaced in every "wrapper-class" node previously deferred during in-place node-swap experiments:

| Node id base | Has body? | Body contents (typical) |
|---|---|---|
| `loopcarriedvalue` | yes | `start`, `end`, body computation |
| `legacyobjectaccess` | yes | `matrixop` (nested capsule!), `objectimport`, `baselistparameter`, `multransform`, `combine`, `mat`, `sqrpart`, `vectrans`, `sqrtrans` |
| `delete` | yes | selection-handling sub-graph |
| `cube` | yes | primitive geometry construction |
| `getcount` | yes | array length read sub-graph |
| `active` | yes | selection mode dispatch (poly\|edge\|point) |
| `get_property` | yes | property accessor body |
| `transformmatrix` | yes | matrix composition body |
| `type` | yes | vec3 destructure (`*access*xin/yin/zin`) |
| `assembler` | yes | spline/curve assembler `coreNode` |
| `children` | yes | array of typed slots, each its own sub-capsule |
| `spline` | yes | spline definition + `_0`, `_1` sub-capsules per segment |
| `get` | yes | accessor body |
| `matrixop` | yes | matrix op decomposition |
| `scaffold`, `group` | typically no | UI/organizational, no body |

**Capsules can also nest** — Spiderweb_Tutorial_01 has `matrixop` capsule INSIDE `legacyobjectaccess` capsule INSIDE the top-level `Nodes Spline` (180420700) capsule. Nested 3 levels deep.

---

## Why this is the rebuild unlock

Earlier in-place swap experiments capped at ~91% of nodes because the deferred wrappers couldn't be rebuilt — `AddChild` creates an EMPTY capsule with `start`+`end` markers but no body. Without the body, the capsule does nothing.

**To rebuild a capsule from scratch:**
1. `graph.AddChild(name, capsule_asset_id)` — creates the wrapper with default empty body
2. **Walk INTO the capsule's interior** via `capsule_node.GetChildren(callback, NODE_KIND::NODE)` — same API, scoped to the capsule's sub-graph
3. `capsule_node.AddChild(...)` (if the API supports it) OR construct the body via the same graph transaction operating on the capsule's interior
4. Wire body nodes to each other AND to the capsule's `start`/`end` markers (which expose the wrapper's outer IN/OUT ports to the body)
5. Set port defaults on the capsule's interface (typed slots, parameters)

---

## Walking a capsule interior (Python)

```python
import maxon

# Get the capsule node (e.g. an LCV instance from a graph walk)
lcv = ...  # GraphNode

# Walk the capsule's interior — children that are themselves NODE_KIND::NODE
inner_nodes = []
lcv.GetChildren(lambda n: inner_nodes.append(n) or True, maxon.NODE_KIND.NODE)

# inner_nodes typically contains "start", "end", and any body computation nodes
for n in inner_nodes:
    print(f"  capsule body: {n.GetId()}")

# Recurse for nested capsules
def walk_capsule_recursive(node, depth=0):
    inner = []
    node.GetChildren(lambda c: inner.append(c) or True, maxon.NODE_KIND.NODE)
    for c in inner:
        print(f"  {'  '*depth}{c.GetId()}")
        walk_capsule_recursive(c, depth+1)
```

The `start` and `end` nodes inside a capsule are **auto-present** when you `AddChild` a fresh capsule. They expose the capsule's outer port topology to the body:

- **`start`** — has OUT ports for every IN port on the capsule's outer interface (so body can READ inputs)
- **`end`** — has IN ports for every OUT port on the capsule's outer interface (so body can WRITE outputs)

A trivial "identity capsule" (passthrough) is wired by connecting each `start` OUT port to the matching `end` IN port. Anything between is the actual computation.

---

## Type priming with `types._0`

For typed-slot capsules (LCV, generic carry containers, etc.), the `_0` slot's actual type is determined by the `types._0` port. Setting it changes the slot's sub-port topology:

| `types._0` value | `_0` slot sub-ports |
|---|---|
| Vec3 (default) | `*access*xin/yin/zin` (in), `*access*xout/yout/zout/lenout/normalizedout` (out) |
| Geometry (`net.maxon.geometryabstraction.interface.object`) | atomic — no destructure |
| Float | atomic |
| Array<T> | varies by T |

Set BEFORE wiring `_0` connections. Once a connection exists on `_0`, the type-determinant port may become locked or harder to retype.

```python
geom_id = maxon.Id("net.maxon.geometryabstraction.interface.object")
types_0_port.SetDefaultValue(geom_id)
# Now lcv.initial._0 / current._0 / next._0 / final._0 are all atomic Geometry ports
```

---

## Implications for the Phase-3 rebuild work

This theory makes it possible to build a **generic capsule-aware rebuild script** that takes any DRuckli scene's SN graph and reproduces it from primitives:

1. Recursive descriptor capture (top-level + every capsule's body, all the way down)
2. Recursive AddChild (build capsules + populate their bodies)
3. Per-capsule wire-restoration (body wiring + interface wires + cross-scope wires)
4. Port default replay (especially type-determinants)
5. Refresh + visual diff against source

Once that script is robust, all 30 DRuckli scenes become rebuildable. The end product is a teaching pack: every scene shipped as both the original analysis (Phase 1+2) AND the from-scratch rebuild (Phase 3) — proof of true understanding, plus reusable patterns artists can lift directly.

---

## Open questions for future deep-dives

1. **Can you `AddChild` directly into a capsule's interior?** Or must you use a different API to construct body nodes? The graph transaction context probably matters.
2. **How does `start`/`end` map ports automatically?** What happens when you add a NEW IN port to the capsule's outer interface — does `start` auto-grow a matching OUT?
3. **Cross-scope wiring rules:** when can a body node wire OUT to a node OUTSIDE the capsule? Stack Stones shows this happening, but the rules aren't documented.
4. **Asset-published capsules** (the ones that come from `.c4dnodes` files in the asset DB) vs scene-local capsules — same internal structure?
5. **The `0228e699...` anonymous master node** in Stack Stones — what asset id is it? Is it a scene-local custom capsule the artist created? Or a hidden Maxon utility?
