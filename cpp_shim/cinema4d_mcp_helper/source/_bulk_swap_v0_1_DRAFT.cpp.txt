/* ============================================================================
 * scene_nodes_bulk_swap_nodes — C++ v0.1 DRAFT
 * ============================================================================
 *
 * STATUS: draft — NOT YET integrated into main.cpp. Hand-write into main.cpp
 * around line 363 (after Phase A.2.1 helpers, before MessageData class) when
 * ready to build. Add the switch case (line 469+) per the integration notes
 * at the bottom of this file.
 *
 * v0.1 SCOPE (this file):
 *   - parse line-separated, pipe-delimited swap specs from BC string key
 *   - find SN deformer in active doc (plugin id 180420400)
 *   - open NodesGraphModel via NimbusBaseRef
 *   - per-swap: verify OG exists, MY name doesn't, create MY in transaction
 *   - return per-swap audit string (status only; no wires yet)
 *   - process in order; stop on first failure (caller resumes from last good)
 *
 * v0.2 will add:
 *   - capture OG input + output wires (read-only walk)
 *   - mirror inputs in dedicated transaction
 *
 * v0.3 will add:
 *   - ATOMIC remove OG + connect MINE.out -> dst port (single transaction)
 *     the proven Path A pattern from after_swap_46_atomic.c4d test
 *
 * v0.4 will add:
 *   - per-swap snapshot save (BC_KEY_SNAPSHOT_PATH_PREFIX -> save N.c4d)
 *   - arithmetic config copy (operation + datatype before wire mirror)
 *
 * INPUT (BC_KEY_BULK_SWAP_INPUT, cinema::String):
 *   "og_id_1|my_name_1|asset_id_1\nog_id_2|my_name_2|asset_id_2\n..."
 *
 * OUTPUT (BC_KEY_BULK_SWAP_RESULT, cinema::String):
 *   "og_id_1|status|wires_mirrored|wires_rewired|error_msg\n..."
 *   status one of: ok | missing_og | already_swapped | create_err | exception
 *
 * SAFETY: this v0.1 is NOT atomic-aware yet. It only creates MY nodes.
 * Production use REQUIRES v0.3+ for nodes whose outputs feed component
 * sub-ports (*access*). See c4d_2026_api_gotchas.md gotcha #76.
 * ============================================================================ */

// ----- Constants to add near existing OP_LIST_COMMANDS / BC_KEY_LOG_DUMP -----

const Int32 OP_BULK_SWAP_NODES         = 50;

const Int32 BC_KEY_BULK_SWAP_INPUT     = 1057845060; // String — input specs
const Int32 BC_KEY_BULK_SWAP_RESULT    = 1057845061; // String — output audit
// future: BC_KEY_SNAPSHOT_PATH_PREFIX = 1057845062 (v0.4)

// SN Deformer plugin id — matches Python side (mcp_server_plugin.pyp).
const Int32 SN_DEFORMER_PLUGIN_ID = 180420400;


// ----- Helpers -----

// Find first SN Deformer in active document (DFS one level deep).
// SN Deformer is usually a child of a regular geometry object (Torus, etc.)
static BaseObject* FindFirstSNDeformer(BaseDocument* doc)
{
    if (!doc) return nullptr;
    BaseObject* o = doc->GetFirstObject();
    while (o)
    {
        if (o->GetType() == SN_DEFORMER_PLUGIN_ID) return o;
        BaseObject* down = o->GetDown();
        while (down)
        {
            if (down->GetType() == SN_DEFORMER_PLUGIN_ID) return down;
            down = down->GetNext();
        }
        o = o->GetNext();
    }
    return nullptr;
}

// Find a top-level graph node by full id string. Returns invalid GraphNode
// if not found. Stops iteration on first match.
static maxon::GraphNode FindGraphNodeById(const maxon::nodes::NodesGraphModelRef& graph,
                                          const maxon::String& target)
{
    maxon::GraphNode result;
    maxon::GraphNode root = graph.GetViewRoot();
    if (!root.IsValid()) return result;
    // Note: we discard any iteration error since the lambda returns Bool(true)
    // to continue or Bool(false) to stop. iferr_ignore keeps this from
    // breaking caller flow on a bad walk (very unlikely).
    iferr (root.GetChildren([&result, &target](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool>
        {
            if (result.IsValid())
                return maxon::Bool(false);
            if (candidate.GetId().ToString() == target)
                result = candidate;
            return maxon::Bool(true);
        }, maxon::NODE_KIND::NODE))
    {
        // swallow — caller will check result.IsValid()
    }
    return result;
}


// ----- Spec parsing -----

struct BulkSwapSpec
{
    maxon::String og_id;
    maxon::String my_name;
    maxon::String asset_id;
};

// Parse line-separated, pipe-delimited specs.
// Format: "og_id|my_name|asset_id\n..."
// Empty lines and lines with fewer than 2 pipes are skipped.
static maxon::Result<maxon::BaseArray<BulkSwapSpec>> ParseBulkSwapSpecs(
    const maxon::String& input)
{
    iferr_scope;
    maxon::BaseArray<BulkSwapSpec> out;
    if (input.GetLength() == 0) return out;

    // Manual line-by-line scan. Maxon's String API is conservative — we use
    // GetPart + Find. Start position is tracked in pos.
    maxon::Int pos = 0;
    const maxon::Int total = input.GetLength();
    while (pos < total)
    {
        // Find next newline
        maxon::String remainder = input.GetPart(pos, total - pos);
        maxon::Int relNewline;
        maxon::String line;
        if (remainder.Find("\n"_s, &relNewline))
        {
            line = remainder.GetPart(0, relNewline);
            pos += relNewline + 1;
        }
        else
        {
            line = remainder;
            pos = total;
        }

        if (line.GetLength() == 0) continue;

        // First pipe
        maxon::Int firstPipe;
        if (!line.Find("|"_s, &firstPipe)) continue;
        const maxon::String og = line.GetPart(0, firstPipe);
        const maxon::String rest1 = line.GetPart(firstPipe + 1, line.GetLength() - firstPipe - 1);

        // Second pipe
        maxon::Int secondPipe;
        if (!rest1.Find("|"_s, &secondPipe)) continue;
        const maxon::String my = rest1.GetPart(0, secondPipe);
        const maxon::String asset = rest1.GetPart(secondPipe + 1, rest1.GetLength() - secondPipe - 1);

        BulkSwapSpec spec;
        spec.og_id = og;
        spec.my_name = my;
        spec.asset_id = asset;
        out.Append(spec) iferr_return;
    }
    return out;
}


// ----- v0.1 single-swap implementation: minimal (create only) -----

// Just creates MY node. No wire capture, no mirror, no delete OG, no rewire.
// v0.1 validates the C++ -> graph plumbing end-to-end.
static maxon::Result<void> DoBulkSwapOne_v0_1_Impl(
    const maxon::nodes::NodesGraphModelRef& graph,
    const BulkSwapSpec& spec)
{
    iferr_scope;

    // Verify OG exists
    maxon::GraphNode og = FindGraphNodeById(graph, spec.og_id);
    if (!og.IsValid())
        return maxon::IllegalArgumentError(MAXON_SOURCE_LOCATION,
            "missing_og: "_s + spec.og_id);

    // Verify MY doesn't already exist
    maxon::GraphNode existing = FindGraphNodeById(graph, spec.my_name);
    if (existing.IsValid())
        return maxon::IllegalArgumentError(MAXON_SOURCE_LOCATION,
            "already_swapped: "_s + spec.my_name);

    // Build maxon::Ids
    maxon::Id my_id;
    my_id.Init(spec.my_name) iferr_return;
    maxon::Id asset_id;
    asset_id.Init(spec.asset_id) iferr_return;

    // Create MY in transaction
    maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;
    iferr (graph.AddChild(my_id, asset_id))
    {
        // Roll back implicitly by not committing
        return err;
    }
    txn.Commit() iferr_return;

    return maxon::OK;
}


// ----- Top-level dispatcher (called from CoreMessage switch) -----

static Int32 DoBulkSwapNodes(BaseContainer* wc)
{
    const String inputStr = wc->GetString(BC_KEY_BULK_SWAP_INPUT);
    if (inputStr.GetLength() == 0)
    {
        wc->SetString(BC_KEY_STATUS_MSG, "BC_KEY_BULK_SWAP_INPUT empty"_s);
        return 50;
    }

    BaseDocument* doc = GetActiveDocument();
    if (!doc)
    {
        wc->SetString(BC_KEY_STATUS_MSG, "no active document"_s);
        return 51;
    }

    BaseObject* sn = FindFirstSNDeformer(doc);
    if (!sn)
    {
        wc->SetString(BC_KEY_STATUS_MSG, "no SN deformer (180420400) found in active document"_s);
        return 52;
    }

    maxon::NimbusBaseRef nimbus = sn->GetNimbusRef(maxon::neutron::NODESPACE);
    if (nimbus == nullptr)
    {
        wc->SetString(BC_KEY_STATUS_MSG, "SN deformer has no nimbus ref"_s);
        return 53;
    }

    const maxon::nodes::NodesGraphModelRef& graph = nimbus.GetGraph();
    if (!graph)
    {
        wc->SetString(BC_KEY_STATUS_MSG, "graph model is null"_s);
        return 54;
    }
    if (graph.IsReadOnly())
    {
        wc->SetString(BC_KEY_STATUS_MSG, "graph is read-only"_s);
        return 55;
    }

    // Parse specs
    const maxon::String inputMaxon = MaxonConvert(inputStr);
    maxon::BaseArray<BulkSwapSpec> specs;
    {
        iferr (specs = ParseBulkSwapSpecs(inputMaxon))
        {
            wc->SetString(BC_KEY_STATUS_MSG, String("parse error: ") + MaxonConvert(err.GetMessage()));
            return 56;
        }
    }

    // Process swaps in order; build result string
    maxon::String resultStr;
    Int32 ok_count = 0;
    Int32 fail_count = 0;
    for (const BulkSwapSpec& spec : specs)
    {
        maxon::String status;
        maxon::String errMsg;
        iferr (DoBulkSwapOne_v0_1_Impl(graph, spec))
        {
            errMsg = err.GetMessage();
            // Map common error prefixes to typed status codes
            if (errMsg.Find("missing_og"_s, nullptr))         status = "missing_og"_s;
            else if (errMsg.Find("already_swapped"_s, nullptr)) status = "already_swapped"_s;
            else                                                status = "create_err"_s;
            fail_count++;
        }
        else
        {
            status = "ok"_s;
            ok_count++;
        }

        // Append result line
        resultStr += spec.og_id;
        resultStr += "|"_s;
        resultStr += status;
        resultStr += "|0|0|"_s;  // wires_mirrored | wires_rewired (v0.1: not yet)
        resultStr += errMsg;
        resultStr += "\n"_s;

        if (fail_count > 0) break; // stop on first failure
    }

    wc->SetString(BC_KEY_BULK_SWAP_RESULT, MaxonConvert(resultStr));

    String summary;
    summary += "bulk_swap_v0.1: ok=";
    summary += String::IntToString((Int64)ok_count);
    summary += "/";
    summary += String::IntToString((Int64)specs.GetCount());
    if (fail_count > 0)
    {
        summary += " STOPPED_AT_FIRST_FAILURE";
    }
    wc->SetString(BC_KEY_STATUS_MSG, summary);

    return (fail_count == 0) ? 0 : 57;
}

/* ============================================================================
 * INTEGRATION NOTES — to insert into main.cpp
 * ============================================================================
 *
 * 1. Add the constants at the top of cinema namespace (around existing
 *    OP_LIST_COMMANDS = 30):
 *
 *      const Int32 OP_BULK_SWAP_NODES         = 50;
 *      const Int32 BC_KEY_BULK_SWAP_INPUT     = 1057845060;
 *      const Int32 BC_KEY_BULK_SWAP_RESULT    = 1057845061;
 *      const Int32 SN_DEFORMER_PLUGIN_ID      = 180420400;
 *
 * 2. Add the helper functions, struct, parser, and DoBulkSwapOne_v0_1_Impl
 *    + DoBulkSwapNodes at file scope BEFORE the MessageData class (around
 *    line 363, after the Phase A.2.1 helpers).
 *
 * 3. Add this case to the switch in CinemaMcpHelper::CoreMessage (line 469+):
 *
 *      case OP_BULK_SWAP_NODES:
 *      {
 *          status = DoBulkSwapNodes(wc);
 *          break;
 *      }
 *
 * 4. On the Python side (mcp_server_plugin.pyp around line 15600), mirror
 *    the new constants:
 *
 *      _OP_BULK_SWAP_NODES         = 50
 *      _BC_KEY_BULK_SWAP_INPUT     = 1057845060
 *      _BC_KEY_BULK_SWAP_RESULT    = 1057845061
 *
 *    and extend _mcp_helper_dispatch (or add a sibling method) to:
 *      - SetString(_BC_KEY_BULK_SWAP_INPUT, swap_specs_str)
 *      - SetString(_BC_KEY_BULK_SWAP_RESULT, "")  // reset
 *      - fire SpecialEventAdd as usual
 *      - poll BC_KEY_STATUS, and read BC_KEY_BULK_SWAP_RESULT into return
 *
 * 5. Add the MCP tool in src/cinema4d_mcp/server.py:
 *
 *      @mcp.tool()
 *      async def scene_nodes_bulk_swap_nodes(
 *          swap_specs: list[dict],  # [{"og_id": ..., "my_name": ..., "asset_id": ...}]
 *          ctx: Context = None,
 *      ) -> str:
 *          # Encode as line-separated string
 *          lines = [f"{s['og_id']}|{s['my_name']}|{s['asset_id']}" for s in swap_specs]
 *          input_str = "\n".join(lines)
 *          response = send_to_c4d(connection, {
 *              "command": "scene_nodes_bulk_swap_nodes",
 *              "input": input_str,
 *          })
 *          # Parse pipe-delimited result rows
 *          rows = []
 *          for line in response.get("result", "").split("\n"):
 *              if not line: continue
 *              parts = line.split("|", 4)
 *              if len(parts) >= 5:
 *                  rows.append({
 *                      "og_id": parts[0],
 *                      "status": parts[1],
 *                      "wires_mirrored": int(parts[2]),
 *                      "wires_rewired": int(parts[3]),
 *                      "error_msg": parts[4],
 *                  })
 *          return json.dumps({"results": rows, "summary": response.get("status_msg")}, indent=2)
 *
 * 6. Build:  ./scripts/build_cpp_shim.sh all
 *
 * 7. Test (BEFORE running on Match Size — per GPT test pyramid):
 *    a. Create a fresh SN Deformer with 2 reroute nodes wired together.
 *       Run scene_nodes_bulk_swap_nodes([{"og_id": "reroute@HASH",
 *           "my_name": "MY_test_1", "asset_id": "net.maxon.node.reroute"}]).
 *       Verify MY appears in graph, status=ok, no crash.
 *    b. Add a type@ node with a reroute feeding *access*zin.
 *       Run bulk_swap on the reroute. Validates v0.1 doesn't crash on
 *       component-sub-port destinations (though wires aren't reconnected
 *       until v0.3).
 *    c. ONE real Match Size swap from after_swap_46_atomic.c4d.
 *    d. Then 3, 10, remainder per GPT test pyramid.
 *
 * 8. Iterate to v0.2 (wire mirror) → v0.3 (atomic remove+reconnect) →
 *    v0.4 (snapshot save). Each version: build + install + run pyramid.
 *
 * ============================================================================
 * MAXON SDK API NOTES (gleaned from main.cpp Phase A.1 dead code)
 * ============================================================================
 *
 * - Document access:   BaseDocument* doc = GetActiveDocument();
 * - Object lookup:     BaseObject* host = doc->SearchObject(name);
 * - Nimbus ref:        host->GetNimbusRef(maxon::neutron::NODESPACE);
 * - Graph model:       nimbus.GetGraph();  // const NodesGraphModelRef&
 * - View root:         graph.GetViewRoot();  // GraphNode
 * - Walk children:     root.GetChildren(lambda, NODE_KIND::NODE);
 * - Transaction:       graph.BeginTransaction() iferr_return;
 *                      txn.Commit() iferr_return;
 * - Add child:         graph.AddChild(my_id, asset_id) iferr_return;
 *   (UNVERIFIED for graph-level — Phase A.1 used node.AddPort. Real signature
 *    may be on graph or via different method. CHECK SDK header
 *    maxon/nodesgraph.h for graph.AddChild or similar.)
 * - Remove node:       node.Remove() — UNVERIFIED, check SDK
 * - Connect ports:     port.Connect(target) — UNVERIFIED, check SDK
 *
 * If graph.AddChild doesn't exist, alternatives:
 *   - GraphNode constructor + manual insertion
 *   - graph.GetViewRoot().AddChild(my_id, asset_id)
 *   - maxon::nodes::NodesLib::CreateNode(graph, my_id, asset_id)
 *
 * The Maxon SDK reference at frameworks/nodes.framework/source/ has examples
 * — read maxon/nodesgraph.h and maxon/graph.h once SDK is accessible from
 * the build environment.
 * ============================================================================
 */
