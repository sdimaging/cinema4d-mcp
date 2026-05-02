"""Phase-3 generic capsule-aware Scene Nodes rebuild script.

Captures the FULL graph descriptor of an artist-authored Scene Nodes graph
(all top-level nodes + every capsule's internal body, recursively) and
attempts to rebuild it from primitives in a fresh empty graph.

Usage (inside Cinema 4D's Python via cinema4d-mcp's execute_python_script):

    from sn_phase3_rebuild import capture_scene, rebuild_scene
    desc = capture_scene("Stack Stones")  # walks the named SN deformer/generator
    new_doc, new_sn = rebuild_scene(desc, target_name="Stack Stones REBUILT")
    # Compare new_sn's graph to the source

Or as a CLI from outside C4D — point at a JSON descriptor file:

    python sn_phase3_rebuild.py --capture --source-name "Stack Stones" \\
        --out /tmp/stack_stones_desc.json
    python sn_phase3_rebuild.py --rebuild --in /tmp/stack_stones_desc.json \\
        --target-name "Stack Stones REBUILT"

The script runs INSIDE C4D — it can be sourced via cinema4d-mcp's
execute_python_script tool. The CLI form is for documentation; the typical
use is to import the functions and call them inline.
"""

import json
import os


def _find_first_obj_of_type(start_obj, type_id):
    """Recursive depth-first walk finding the first BaseObject of given type."""
    o = start_obj
    while o:
        if o.GetType() == type_id:
            return o
        d = _find_first_obj_of_type(o.GetDown(), type_id)
        if d:
            return d
        o = o.GetNext()
    return None


def _find_obj_by_name(start_obj, name):
    o = start_obj
    while o:
        if o.GetName() == name:
            return o
        d = _find_obj_by_name(o.GetDown(), name)
        if d:
            return d
        o = o.GetNext()
    return None


def _walk_capsule_recursive(node, path, depth, out_nodes, out_defaults, out_wires, scope_paths):
    """Walk one node + all its capsule-interior descendants. Captures node
    info, port defaults, and outgoing wires (for stitching by path)."""
    import maxon

    nid = str(node.GetId())
    full_path = f"{path}/{nid}" if path else nid

    # Detect capsule interior
    inner = []
    try:
        node.GetChildren(lambda c: inner.append(c) or True, maxon.NODE_KIND.NODE)
    except Exception:
        pass
    is_capsule = len(inner) > 0

    out_nodes.append({
        "path": full_path,
        "id": nid,
        "depth": depth,
        "is_capsule": is_capsule,
        "inner_count": len(inner),
    })
    scope_paths.add(full_path)

    # Capture port default values (recursively into nested sub-ports)
    def _walk_ports_for_defaults(container, dir_label):
        if container is None:
            return

        def visit(p, ppath=""):
            try:
                pid = str(p.GetId())
                fp = f"{ppath}.{pid}" if ppath else pid
                try:
                    dv = p.GetDefaultValue()
                    if dv is not None:
                        s = str(dv)
                        # Filter out trivial defaults to keep descriptor lean
                        if s.strip() and s not in ("None", "0", "0.0", "false", ""):
                            out_defaults.append({
                                "node": full_path,
                                "dir": dir_label,
                                "port": fp,
                                "type": type(dv).__name__,
                                "val": s[:240],
                            })
                except Exception:
                    pass
                for child in p.GetChildren():
                    visit(child, fp)
            except Exception:
                pass

        for p in container.GetChildren():
            visit(p)

    _walk_ports_for_defaults(node.GetInputs(), "IN")
    _walk_ports_for_defaults(node.GetOutputs(), "OUT")

    # Recurse into inner capsule body
    for child in inner:
        _walk_capsule_recursive(child, full_path, depth + 1, out_nodes,
                                 out_defaults, out_wires, scope_paths)


def _walk_wires_recursive(node, path, scope_paths, out_wires):
    """Walk a node's output ports and capture every outgoing wire as a
    (src_path, src_port_path, dst_path, dst_port_path) tuple. For each
    capsule interior, recurses to capture body-level wires too."""
    import maxon

    nid = str(node.GetId())
    full_path = f"{path}/{nid}" if path else nid

    def walk_owner_path(port):
        # Walk up parents until we hit a node whose path is in scope_paths
        cur = port
        for _ in range(12):
            if cur is None:
                return None
            cid = str(cur.GetId())
            # Try matching this node's path in scope by climbing the parent
            # Build candidate path bottom-up
            if cid in ("<", ">"):
                cur = cur.GetParent()
                continue
            # Try simple short match
            for sp in scope_paths:
                if sp.endswith(f"/{cid}") or sp == cid:
                    return sp
            cur = cur.GetParent()
        return None

    def emit_wires(container, direction):
        if container is None:
            return

        def visit(p, ppath=""):
            try:
                pid = str(p.GetId())
                fp = f"{ppath}.{pid}" if ppath else pid
                for other, _w in p.GetConnections(direction):
                    other_pid = str(other.GetId())
                    other_owner = walk_owner_path(other)
                    if other_owner:
                        if direction == maxon.PORT_DIR.OUTPUT:
                            out_wires.append({
                                "src_path": full_path, "src_port": fp,
                                "dst_path": other_owner, "dst_port": other_pid,
                            })
                for child in p.GetChildren():
                    visit(child, fp)
            except Exception:
                pass

        for p in container.GetChildren():
            visit(p)

    emit_wires(node.GetInputs(), maxon.PORT_DIR.INPUT)
    emit_wires(node.GetOutputs(), maxon.PORT_DIR.OUTPUT)

    inner = []
    try:
        node.GetChildren(lambda c: inner.append(c) or True, maxon.NODE_KIND.NODE)
    except Exception:
        pass
    for child in inner:
        _walk_wires_recursive(child, full_path, scope_paths, out_wires)


def capture_scene(host_name, source_doc=None, host_type_ids=None):
    """Capture the full descriptor of an SN host (deformer or generator) by
    name. Walks the SN graph + every capsule interior. Returns a dict that
    can be serialized to JSON."""
    import c4d
    import maxon

    if host_type_ids is None:
        host_type_ids = (180420400, 180420500, 180420600, 180420700)

    if source_doc is None:
        source_doc = c4d.documents.GetActiveDocument()

    host = _find_obj_by_name(source_doc.GetFirstObject(), host_name)
    if host is None:
        # Fallback: first SN host of any kind
        for tid in host_type_ids:
            host = _find_first_obj_of_type(source_doc.GetFirstObject(), tid)
            if host:
                break
    if host is None:
        return {"error": f"no SN host '{host_name}' found"}

    nimbus = host.GetNimbusRef(maxon.Id("net.maxon.neutron.nodespace"))
    graph = nimbus.GetGraph()
    root = graph.GetRoot()

    nodes = []
    defaults = []
    wires = []
    scope_paths = set()

    top_nodes = []
    root.GetChildren(lambda n: top_nodes.append(n) or True, maxon.NODE_KIND.NODE)

    for n in top_nodes:
        _walk_capsule_recursive(n, "", 0, nodes, defaults, wires, scope_paths)

    # Now do a separate pass to capture wires (we needed scope_paths populated first)
    for n in top_nodes:
        _walk_wires_recursive(n, "", scope_paths, wires)

    return {
        "source_host": host.GetName(),
        "source_host_type": host.GetType(),
        "node_count": len(nodes),
        "capsule_count": sum(1 for x in nodes if x["is_capsule"]),
        "wire_count": len(wires),
        "defaults_count": len(defaults),
        "nodes": nodes,
        "wires": wires,
        "port_defaults": defaults,
    }


def save_descriptor(desc, path):
    """Save a captured descriptor to JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(desc, f, indent=2)
    return os.path.getsize(path)


def load_descriptor(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# Asset-id resolution helpers — extend as new wrapper basenames are confirmed
# UPDATED 2026-05-02 with atlas-corrected ids from Spiderweb attempt
DEFAULT_ASSET_MAP = {
    # Math + control
    "arithmetic": "net.maxon.node.arithmetic",
    "compare": "net.maxon.node.compare",
    "if": "net.maxon.node.if",
    "switch": "net.maxon.node.switch",
    "clamp": "net.maxon.node.clamp",
    "negate": "net.maxon.node.negate",
    "scale": "net.maxon.node.scale",
    "range": "net.maxon.node.range",  # unverified — atlas only had Redshift hits
    "hash": "net.maxon.pattern.node.generator.hash",  # ATLAS-VERIFIED
    "inversematrix": "net.maxon.node.inversematrix",
    "transformmatrix": "net.maxon.node.transformmatrix",
    "type": "net.maxon.node.type",
    # Routing + grouping
    "reroute": "net.maxon.node.reroute",
    "floatingio": "net.maxon.node.floatingio",
    "scaffold": "net.maxon.node.scaffold",
    "group": "net.maxon.node.group",
    "container": "net.maxon.node.container",
    "concat": "net.maxon.node.array.concat",  # ATLAS-VERIFIED (array variant)
    # Array ops
    "buildfromsinglevalue": "net.maxon.node.array.buildfromsinglevalue",
    "readvalueatindex": "net.maxon.node.array.readvalueatindex",
    "erase": "net.maxon.node.array.erase",
    "append": "net.maxon.node.array.append",
    "getcount": "net.maxon.node.array.getcount",
    # Iteration
    "containeriteration": "net.maxon.node.containeriteration",
    "loopcarriedvalue": "net.maxon.node.loopcarriedvalue",
    # Geometry
    "bb": "net.maxon.neutron.geometry.bb",
    "transform_element": "net.maxon.neutron.geometry.transform_element",
    "connect_geometries": "net.maxon.neutron.geometry.connect_geometries",
    "explode_islands": "net.maxon.neutron.geometry.explode_islands",
    "get_property": "net.maxon.neutron.geometry.get_property",
    "delete": "net.maxon.neutron.modeling.delete",
    "invertselection": "net.maxon.neutron.modeling.selection.invertselection",
    "active": "net.maxon.neutron.modeling.selection.active",
    "cube": "net.maxon.neutron.node.primitive.cube",
    "spline": "net.maxon.neutron.geometry.spline",  # unverified
    "assembler": "net.maxon.neutron.geometry.spline.assembler",  # ATLAS-VERIFIED
    "children": "net.maxon.neutron.op.children",  # unverified
    "get": "net.maxon.neutron.op.get",  # unverified
    # Object access
    "legacyobjectaccess": "net.maxon.nbo.node.legacyobjectaccess",
}


def discover_asset_id(node, fallback_base):
    """Best-effort runtime asset_id discovery for a GraphNode by reading
    its underlying NodeTemplate id. Returns None if not resolvable."""
    try:
        # Try common attribute paths for the template id
        # Many maxon GraphNodes expose this via GetValue or as a metadata attribute
        # If we can't resolve, callers fall back to the asset map / basename.
        # This is a placeholder — extend as we learn the right API.
        import maxon
        # Attempt: walk up to find a node with template metadata
        # (left as a learning exercise — many capsule-interior nodes don't
        # have publicly-queryable template ids)
        return None
    except Exception:
        return None


def basename_of(node_id):
    if "@" in node_id:
        return node_id.split("@", 1)[0]
    return node_id


def rebuild_scene(desc, target_name="REBUILT", parent_obj=None,
                  asset_map=None, host_type_id=None):
    """Rebuild an SN host + graph from a descriptor. Creates a fresh SN
    host (deformer or generator depending on host_type_id), walks the
    captured nodes recursively, AddChild's each, walks INTO capsule
    interiors to populate bodies, sets port defaults, then connects all
    wires by path matching.

    Returns (new_doc, new_host, report_dict)."""
    import c4d
    import maxon

    if asset_map is None:
        asset_map = DEFAULT_ASSET_MAP
    if host_type_id is None:
        host_type_id = desc.get("source_host_type", 180420400)

    # Fresh doc
    doc = c4d.documents.BaseDocument()
    doc.SetDocumentName(f"{target_name}.c4d")
    c4d.documents.InsertBaseDocument(doc)
    c4d.documents.SetActiveDocument(doc)

    # Host parent — for deformers, need a deformable; for generators, can stand alone
    if parent_obj is None:
        if host_type_id == 180420400:  # Scene Nodes Deformer
            parent_obj = c4d.BaseObject(c4d.Ocube)
            parent_obj.SetName("RebuildHost")
            parent_obj[c4d.PRIM_CUBE_LEN] = c4d.Vector(100, 100, 100)
            parent_obj[c4d.PRIM_CUBE_SUBX] = 5
            parent_obj[c4d.PRIM_CUBE_SUBY] = 5
            parent_obj[c4d.PRIM_CUBE_SUBZ] = 5
            doc.InsertObject(parent_obj)

    new_host = c4d.BaseObject(host_type_id)
    new_host.SetName(target_name)
    if parent_obj:
        new_host.InsertUnder(parent_obj)
    else:
        doc.InsertObject(new_host)

    nimbus = new_host.GetNimbusRef(maxon.Id("net.maxon.neutron.nodespace"))
    graph = nimbus.GetGraph()

    # Index for fast lookup: path -> GraphNode (populated as we build)
    path_to_node = {}

    # PHASE A — recursive AddChild for top-level nodes
    # We walk the descriptor in DEPTH ORDER (depth 0 first, then 1, ...) so
    # that capsule interiors are added AFTER their parent capsule exists.
    nodes_by_depth = {}
    for n in desc["nodes"]:
        nodes_by_depth.setdefault(n["depth"], []).append(n)

    addchild_ok = 0
    addchild_skip = 0
    addchild_err = []

    # Pre-existing context_* nodes
    existing_top = set()
    graph.GetRoot().GetChildren(
        lambda n: existing_top.add(str(n.GetId())) or True,
        maxon.NODE_KIND.NODE
    )

    for depth in sorted(nodes_by_depth.keys()):
        with graph.BeginTransaction() as txn:
            for n in nodes_by_depth[depth]:
                full_path = n["path"]
                nid = n["id"]

                # Skip framework nodes that are auto-present
                if nid.startswith("context_") or nid in ("start", "end"):
                    addchild_skip += 1
                    # Still need to track them for wiring — find the live handle
                    if depth == 0 and nid in existing_top:
                        # Find the live node
                        live = []
                        graph.GetRoot().GetChildren(
                            lambda n: live.append(n) or True if str(n.GetId()) == nid else True,
                            maxon.NODE_KIND.NODE
                        )
                        # Simpler: just walk + match
                        for c in []:  # placeholder — real lookup below
                            pass
                    continue

                base = basename_of(nid)
                asset = asset_map.get(base)
                if asset is None:
                    addchild_err.append({"path": full_path, "reason": "unknown_asset", "base": base})
                    continue

                # AddChild — but where? For depth 0, on root graph.
                # For depth >0, INSIDE the parent capsule (path's parent).
                if depth == 0:
                    target_parent_node = None  # graph root
                else:
                    parent_path = full_path.rsplit("/", 1)[0]
                    target_parent_node = path_to_node.get(parent_path)
                    if target_parent_node is None:
                        addchild_err.append({"path": full_path, "reason": "parent_capsule_missing"})
                        continue

                try:
                    if target_parent_node is None:
                        # Top-level
                        added = graph.AddChild(maxon.Id(nid), maxon.Id(asset))
                    else:
                        # Inside a capsule — same API, scoped to the capsule
                        # NOTE: this may or may not work depending on maxon API;
                        # if it fails we report it as a gap to investigate
                        added = target_parent_node.AddChild(maxon.Id(nid), maxon.Id(asset))
                    addchild_ok += 1
                    path_to_node[full_path] = added
                except Exception as e:
                    addchild_err.append({"path": full_path, "reason": str(e)[:120]})
            txn.Commit()

    # Populate path_to_node for top-level nodes that were added
    # Re-walk to capture handles for context_/start/end (auto-present)
    def repop_handles():
        # Walk top-level
        graph.GetRoot().GetChildren(
            lambda n: path_to_node.update({str(n.GetId()): n}) or True,
            maxon.NODE_KIND.NODE
        )
        # Recurse: for each capsule we've populated, walk its interior
        for path, nd in list(path_to_node.items()):
            try:
                inner = []
                nd.GetChildren(lambda c: inner.append(c) or True, maxon.NODE_KIND.NODE)
                for c in inner:
                    cpath = f"{path}/{str(c.GetId())}"
                    path_to_node[cpath] = c
                    # Recurse one more level
                    inner2 = []
                    c.GetChildren(lambda cc: inner2.append(cc) or True, maxon.NODE_KIND.NODE)
                    for cc in inner2:
                        ccpath = f"{cpath}/{str(cc.GetId())}"
                        path_to_node[ccpath] = cc
            except Exception:
                pass
    repop_handles()

    # PHASE B — set port defaults (especially type-determinants like types._0)
    defaults_set = 0
    defaults_skip = 0
    with graph.BeginTransaction() as txn:
        for d in desc.get("port_defaults", []):
            node_path = d["node"]
            target_node = path_to_node.get(node_path)
            if target_node is None:
                defaults_skip += 1
                continue
            container = target_node.GetInputs() if d["dir"] == "IN" else target_node.GetOutputs()
            # Navigate the port path (split on ".") — but ids can contain dots,
            # so try direct id match first, then split-and-descend
            port_path = d["port"]

            def find_port_by_id(c, target_id):
                for p in c.GetChildren():
                    if str(p.GetId()) == target_id:
                        return p
                return None

            # First try the literal id (ports like "current._0" are literal ids)
            p = find_port_by_id(container, port_path)
            if p is None:
                # Try splitting by "." and descending
                parts = port_path.split(".")
                cursor = container
                for part in parts:
                    if cursor is None:
                        break
                    p = find_port_by_id(cursor, part)
                    cursor = p
            if p is None:
                defaults_skip += 1
                continue
            # Apply the default — best-effort, depends on type
            try:
                # The val is stringified; for some types we can re-parse
                val_str = d["val"]
                vtype = d["type"]
                if vtype == "Id":
                    p.SetDefaultValue(maxon.Id(val_str))
                    defaults_set += 1
                elif vtype == "Bool":
                    p.SetDefaultValue(val_str.lower() in ("true", "1", "yes"))
                    defaults_set += 1
                elif vtype in ("Int64",):
                    try:
                        p.SetDefaultValue(int(val_str))
                        defaults_set += 1
                    except Exception:
                        defaults_skip += 1
                elif vtype in ("Float64",):
                    try:
                        p.SetDefaultValue(float(val_str))
                        defaults_set += 1
                    except Exception:
                        defaults_skip += 1
                else:
                    defaults_skip += 1
            except Exception:
                defaults_skip += 1
        txn.Commit()

    # PHASE C — connect wires
    def find_port_recursive(c, target_id, depth=0):
        if c is None or depth > 6:
            return None
        try:
            for p in c.GetChildren():
                if str(p.GetId()) == target_id:
                    return p
                sub = find_port_recursive(p, target_id, depth + 1)
                if sub:
                    return sub
        except Exception:
            return None
        return None

    connect_ok = 0
    connect_skip = 0
    with graph.BeginTransaction() as txn:
        for w in desc.get("wires", []):
            src = path_to_node.get(w["src_path"])
            dst = path_to_node.get(w["dst_path"])
            if src is None or dst is None:
                connect_skip += 1
                continue
            sp = (find_port_recursive(src.GetOutputs(), w["src_port"]) or
                  find_port_recursive(src.GetInputs(), w["src_port"]))
            dp = (find_port_recursive(dst.GetInputs(), w["dst_port"]) or
                  find_port_recursive(dst.GetOutputs(), w["dst_port"]))
            if sp is None or dp is None:
                connect_skip += 1
                continue
            try:
                sp.Connect(dp)
                connect_ok += 1
            except Exception:
                connect_skip += 1
        txn.Commit()

    # Refresh
    new_host.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE
                      | c4d.DIRTYFLAGS_DESCRIPTION | c4d.DIRTYFLAGS_MATRIX)
    if parent_obj:
        parent_obj.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_MATRIX)
    c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
    doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)

    report = {
        "source_node_count": desc["node_count"],
        "addchild_ok": addchild_ok,
        "addchild_skip_framework": addchild_skip,
        "addchild_err_count": len(addchild_err),
        "addchild_err_sample": addchild_err[:10],
        "defaults_set": defaults_set,
        "defaults_skip": defaults_skip,
        "wires_total": desc.get("wire_count", 0),
        "connect_ok": connect_ok,
        "connect_skip": connect_skip,
        "node_fidelity_pct": (
            100.0 * len(path_to_node) / desc["node_count"]
            if desc["node_count"] else 0
        ),
        "wire_fidelity_pct": (
            100.0 * connect_ok / desc.get("wire_count", 1)
            if desc.get("wire_count") else 0
        ),
    }
    return doc, new_host, report
