# SN_RECIPE_wire_remove_swap_pattern

**Version:** 1.0
**Status:** Verified — disconnect mechanism confirmed working in T2 Centered toggle build
**Eligible for Assemble Mode:** YES (technique entry, not a graph)

## Purpose

The disconnect-and-replace pattern for SN graph wires. Required whenever you need to swap an input port's source from one node to another. Without this, `Connect()` ADDS a second source instead of replacing the existing one — producing an invalid multi-source input that silently outputs 0 verts.

**This is a TECHNIQUE entry, not a graph recipe.** No nodes ship with this recipe — it's a Python idiom you call against any existing graph.

## Provenance

- **Discovered/proven:** 2026-05-04 (during T2 Centered toggle build, `cinema4d-mcp/docs/scene_nodes_advanced_studies/druckli_capsule_audit/03_uv_polygon_info/extension/CANONICAL_SN_DEFORMER_PATTERN.md`)
- **Earlier (incorrect) memory entry:** "no Disconnect API exists" (2026-05-03) — RETRACTED
- **Last verified:** 2026-05-04 (Cinema 4D 2026.2.0)

## The mechanism

`port.Connect(other, modes)` accepts a second positional argument controlling wire creation behavior.

```python
# Default — ADDS a wire (does NOT replace existing)
src_port.Connect(dst_port)
# equivalent to:
src_port.Connect(dst_port, maxon.WIRE_MODE.NORMAL)   # NORMAL = 16

# REMOVE — disconnects the wire
src_port.Connect(dst_port, maxon.WIRE_MODE.REMOVE)   # REMOVE = 62
```

**Connect signature** (from inspect.signature):
```
Connect(target, modes=WIRE_MODE.NORMAL, reverse=False)
```

`modes` accepts `maxon.WIRE_MODE` (an IntFlag) or `maxon.Wires` (the bitmap object).

## Full WIRE_MODE values

| Mode | Value | Meaning |
|---|---|---|
| `WIRE_MODE.NONE` | 0 | (empty) |
| `WIRE_MODE.MIN` | 8 | |
| `WIRE_MODE.NORMAL` | 16 | Default for Connect — creates a value-type wire |
| `WIRE_MODE.CONNECT_DEFAULT` | 16 | Same as NORMAL |
| `WIRE_MODE.PROPAGATION` | 32 | |
| `WIRE_MODE.AUTO_PROPAGATION` | 40 | |
| `WIRE_MODE.MAX` | 48 | |
| `WIRE_MODE.PRIORITY_MASK` | 60 | |
| `WIRE_MODE.AMBIGUOUS` | 61 | |
| `WIRE_MODE.REMOVE` | **62** | **DISCONNECT** |
| `WIRE_MODE.ALL` | 63 | |
| `WIRE_MODE.GETCONNECTIONS_DEFAULT` | 63 | |
| `WIRE_MODE.IMPLICIT` | 64 | |
| `WIRE_MODE.ALL_INCLUDING_IMPLICIT` | 127 | |
| `WIRE_MODE.INHERIT` | 128 | |
| `WIRE_MODE.FULL_MASK` | 255 | |
| `WIRE_MODE.FLAGS_MASK` | 3 | |
| `WIRE_MODE.FLAG0` | 1 | |
| `WIRE_MODE.FLAG1` | 2 | |

## The swap idiom

To swap a wire (replace the source on an input port):

```python
def swap_wire(graph, old_src_port, new_src_port, dst_port):
    """Replace dst_port's incoming wire from old_src_port with one from new_src_port."""
    with graph.BeginTransaction() as tx:
        old_src_port.Connect(dst_port, maxon.WIRE_MODE.REMOVE)   # disconnect old
        new_src_port.Connect(dst_port)                            # connect new
        tx.Commit()
```

Order matters slightly: REMOVE first ensures the input port is empty before NEW connect, avoiding any transient multi-source state. (In practice both calls inside a single transaction commit atomically, but ordering REMOVE first matches the mental model.)

## Insert-node-into-wire idiom

Common operation: insert a transformation node between an existing wire's source and destination.

```python
def insert_into_wire(graph, src_port, dst_port, new_in_port, new_out_port):
    """Convert (src → dst) into (src → new_in, new_out → dst)."""
    with graph.BeginTransaction() as tx:
        src_port.Connect(dst_port, maxon.WIRE_MODE.REMOVE)   # disconnect old
        src_port.Connect(new_in_port)                         # src → new
        new_out_port.Connect(dst_port)                        # new → dst
        tx.Commit()
```

Used in `SN_RECIPE_centered_uv_toggle` to insert `arith_center` between `comp1.result` and `scale1.in1`.

## IO contract

**Input:** any existing graph with a wire you need to remove or swap.

**Output:** modified graph with the disconnect/swap applied.

**Idempotency:** calling REMOVE on a wire that doesn't exist is a no-op (no error). Safe to call defensively.

## Failure modes BEFORE this discovery

Before WIRE_MODE.REMOVE was found, the only way to "disconnect" was:
1. Delete the destination node + recreate it (loses all other wires on it) — brittle
2. Override at outer level by adding a transform/blend that compensates — increases node count
3. Use UI to disconnect manually — not MCP-reproducible

These are all SUPERSEDED by the WIRE_MODE.REMOVE pattern.

## Verification test

```python
def verify_wire_remove(graph, src_port, dst_port):
    """Verify that calling Connect(REMOVE) actually removes the wire."""
    # Add a wire
    with graph.BeginTransaction() as tx:
        src_port.Connect(dst_port)
        tx.Commit()

    # Count sources on dst
    sources_before = []
    dst_port.GetConnections(maxon.PORT_DIR.INPUT, lambda o, w: sources_before.append(o) or True)
    if not any(s.GetAncestor(maxon.NODE_KIND.NODE).GetId() == src_port.GetAncestor(maxon.NODE_KIND.NODE).GetId() for s in sources_before):
        return False, "Connect didn't add the wire"

    # Remove it
    with graph.BeginTransaction() as tx:
        src_port.Connect(dst_port, maxon.WIRE_MODE.REMOVE)
        tx.Commit()

    # Count again
    sources_after = []
    dst_port.GetConnections(maxon.PORT_DIR.INPUT, lambda o, w: sources_after.append(o) or True)
    if any(s.GetAncestor(maxon.NODE_KIND.NODE).GetId() == src_port.GetAncestor(maxon.NODE_KIND.NODE).GetId() for s in sources_after):
        return False, "Connect(REMOVE) didn't remove the wire"

    return True, "wire add+remove cycle confirmed"
```

## Anti-patterns

- ❌ Don't call `dst_port.Connect(new_src)` expecting it to replace `old_src` — it ADDS, doesn't replace
- ❌ Don't try `port.Disconnect()` — method doesn't exist on ports
- ❌ Don't try `wire.Remove()` — the `Wires` object is a flag bitmap, not a removable handle
- ❌ Don't try `graph.Disconnect()` — method doesn't exist on graph
- ✅ Use `src.Connect(dst, maxon.WIRE_MODE.REMOVE)` — the canonical disconnect

## Related recipes

- **`SN_RECIPE_centered_uv_toggle`** — uses `insert_into_wire` to add `arith_center` between `comp1` and `scale1`
- Future recipes that mutate existing graphs will rely on this pattern
