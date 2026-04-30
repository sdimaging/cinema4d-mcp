/* cinema4d_mcp_helper — C++ companion plugin for cinema4d-mcp.

PHASE A.1 (2026-04-30): wraps GraphModelInterface::AddPort for named-port
creation on a Floating IO node inside an SN Generator's embedded graph.
This is what the C4D editor calls on drag-wire when an artist exposes a
parameter — Python's maxon.frameworks.{nodes,graph} module doesn't wrap
the singular AddPort form, so we do it here.

Phase A.0 (earlier): minimal MessageData skeleton — verified plugin loads
+ is discoverable via FindPlugin.

Protocol (Python <-> C++ via shared GetWorldContainerInstance):
  Python writes args under our BC keys, fires
  c4d.SendCoreMessage(MSG_MCP_HELPER_REQ, c4d.BaseContainer()).
  C++ CoreMessage reads world container, dispatches by op-code, writes
  result back. Python reads result. Single in-flight only — MCP socket
  serializes calls at the orchestration layer.
*/

#include "main.h"

#include "maxon/graph.h"
#include "maxon/nodesgraph.h"
#include "maxon/nodesgraph_helpers.h"
#include "maxon/node_spaces.h"
#include "maxon/nodes_all.h"
#include "maxon/nimbusbase.h"
#include "maxon/neutron_ids.h"

namespace cinema
{

const Int32 MCP_HELPER_PLUGIN_ID = 1057845;

const Int32 MCP_HELPER_PROTOCOL_VERSION = 2; // 1=A.0, 2=A.1

const Int32 MSG_MCP_HELPER_REQ = 1057845001;

const Int32 BC_KEY_OP             = 1057845010;
const Int32 BC_KEY_TARGET         = 1057845011;
const Int32 BC_KEY_NODE_ID        = 1057845012;
const Int32 BC_KEY_PORT_NAME      = 1057845013;
const Int32 BC_KEY_IS_OUTPUT      = 1057845014;
const Int32 BC_KEY_STATUS         = 1057845020;
const Int32 BC_KEY_STATUS_MSG     = 1057845021;
const Int32 BC_KEY_NEW_PORT_ID    = 1057845022;
const Int32 BC_KEY_PROTOCOL_VER   = 1057845023;

const Int32 OP_PING                  = 0;
const Int32 OP_ADD_FLOATING_IO_PORT  = 1;

// ============================================================================
// Helpers
// ============================================================================

// Compare a graph node's id (last path segment) against a target string.
// Accepts either a full ID like "host@HASH/floatingio@HASH2" or a basename
// "floatingio". Returns true on match.
static Bool MatchesNodeIdSegment(const maxon::GraphNode& node, const maxon::String& target)
{
	const maxon::String idStr = node.GetId().ToString();
	// last path segment after final '/'
	const maxon::String separator = "/"_s;
	maxon::Int slashPos;
	maxon::String lastSeg = idStr;
	if (idStr.FindLast(separator, &slashPos))
	{
		lastSeg = idStr.GetPart(slashPos + 1, idStr.GetLength() - slashPos - 1);
	}
	if (lastSeg == target)
		return true;
	// also match basename (before '@') against the target. Use Find (not
	// FindFirst — that name doesn't exist on maxon::String, see gotcha #28).
	maxon::Int atPos;
	if (lastSeg.Find("@"_s, &atPos))
	{
		const maxon::String base = lastSeg.GetPart(0, atPos);
		if (base == target)
			return true;
	}
	return false;
}

// ============================================================================
// AddPort implementation — returns Result<void> so iferr_return works clean
// ============================================================================

static maxon::Result<void> DoAddFloatingIOPort_Impl(BaseContainer* wc,
                                                    maxon::String& outNewPortId)
{
	iferr_scope;

	const String targetName = wc->GetString(BC_KEY_TARGET);
	const String fioNodeId  = wc->GetString(BC_KEY_NODE_ID);
	const String portName   = wc->GetString(BC_KEY_PORT_NAME);
	const Bool   isOutput   = wc->GetBool(BC_KEY_IS_OUTPUT);

	if (!targetName.GetLength())
		return maxon::IllegalArgumentError(MAXON_SOURCE_LOCATION, "graph_target is required"_s);
	if (!fioNodeId.GetLength())
		return maxon::IllegalArgumentError(MAXON_SOURCE_LOCATION, "fio_node_id is required"_s);
	if (!portName.GetLength())
		return maxon::IllegalArgumentError(MAXON_SOURCE_LOCATION, "port_name is required"_s);

	BaseDocument* doc = GetActiveDocument();
	if (!doc)
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION, "no active document"_s);

	BaseObject* host = doc->SearchObject(targetName);
	if (!host)
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION,
			"graph_target not found in active document"_s);

	maxon::NimbusBaseRef nimbus = host->GetNimbusRef(maxon::neutron::NODESPACE);
	if (nimbus == nullptr)
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION,
			"host has no Scene Nodes graph (NimbusRef null)"_s);

	const maxon::nodes::NodesGraphModelRef& graph = nimbus.GetGraph();
	if (!graph)
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION, "graph model is null"_s);
	if (graph.IsReadOnly())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION, "graph is read-only"_s);

	// Walk graph from view root to find the FIO node. GetInnerNodes recurses
	// through node-kind children; we filter by id segment match. The found
	// node is captured by reference for use after the walk completes.
	const maxon::String fioTargetMaxon = MaxonConvert(fioNodeId);
	maxon::GraphNode foundFio;
	maxon::GraphNode root = graph.GetViewRoot();
	if (!root.IsValid())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION, "graph view root is invalid"_s);

	// GetInnerNodes is on the GraphNode, NOT on the graph reference (gotcha
	// #30). Recurses through node-kind children; the receiver is invoked for
	// each. Return true to continue, false to stop.
	root.GetInnerNodes(maxon::NODE_KIND::NODE, false,
		[&foundFio, &fioTargetMaxon](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool>
		{
			if (foundFio.IsValid())
				return maxon::Bool(true); // already found, keep iterating (cheap)
			if (MatchesNodeIdSegment(candidate, fioTargetMaxon))
				foundFio = candidate;
			return maxon::Bool(true);
		}) iferr_return;

	if (!foundFio.IsValid())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION,
			"fio_node_id not found in graph"_s);

	// AddPort on the inputs / outputs container of the FIO. The container
	// itself is a GraphNode (a port node) and supports AddPort directly.
	maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;

	maxon::GraphNode container = isOutput ? foundFio.GetOutputs() : foundFio.GetInputs();
	if (!container.IsValid())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION,
			"FIO has no inputs/outputs container"_s);

	const maxon::String portNameMaxon = MaxonConvert(portName);
	maxon::Id portId;
	portId.Init(portNameMaxon) iferr_return;

	// container.AddPort returns Result<GraphNode>. Use the explicit unwrap
	// pattern — `iferr_return` doesn't extract the value cleanly when the
	// caller-site type is the deduced template form (gotcha #29 caveat).
	maxon::Result<maxon::GraphNode> portResult = container.AddPort(portId);
	if (portResult.IsError())
		return portResult.GetError();
	maxon::GraphNode newPort = portResult.GetValue();
	if (!newPort.IsValid())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION,
			"AddPort returned invalid GraphNode"_s);

	outNewPortId = newPort.GetId().ToString();

	txn.Commit() iferr_return;
	return maxon::OK;
}

// Top-level wrapper called from CoreMessage. Translates Result<void> into
// status code + status message written to the world container. Returns 0 on
// success, non-zero on failure (the integer is informational only — Python
// reads the status message for diagnostics).
static Int32 DoAddFloatingIOPort(BaseContainer* wc)
{
	maxon::String newPortId;
	iferr (DoAddFloatingIOPort_Impl(wc, newPortId))
	{
		const String msg = MaxonConvert(err.GetMessage());
		wc->SetString(BC_KEY_STATUS_MSG, msg);
		return 1;
	}
	wc->SetString(BC_KEY_NEW_PORT_ID, MaxonConvert(newPortId));
	wc->SetString(BC_KEY_STATUS_MSG, "AddPort succeeded"_s);
	return 0;
}

// ============================================================================
// MessageData receiver
// ============================================================================
class CinemaMcpHelper : public MessageData
{
public:
	virtual Bool CoreMessage(Int32 id, const BaseContainer& bc) override
	{
		(void)bc;

		if (id != MSG_MCP_HELPER_REQ)
			return true;

		BaseContainer* wc = GetWorldContainerInstance();
		if (!wc)
			return true;

		const Int32 op = wc->GetInt32(BC_KEY_OP);

		// Reset response slots
		wc->SetInt32(BC_KEY_PROTOCOL_VER, MCP_HELPER_PROTOCOL_VERSION);
		wc->SetString(BC_KEY_NEW_PORT_ID, ""_s);
		wc->SetString(BC_KEY_STATUS_MSG, ""_s);

		Int32 status = 0;
		switch (op)
		{
			case OP_PING:
				wc->SetString(BC_KEY_STATUS_MSG, "pong"_s);
				status = 0;
				break;
			case OP_ADD_FLOATING_IO_PORT:
				status = DoAddFloatingIOPort(wc);
				break;
			default:
				wc->SetString(BC_KEY_STATUS_MSG, "unknown op-code"_s);
				status = 99;
				break;
		}

		wc->SetInt32(BC_KEY_STATUS, status);
		return true;
	}
};

Bool RegisterMcpHelperPlugin()
{
	return RegisterMessagePlugin(
		MCP_HELPER_PLUGIN_ID,
		"cinema4d-mcp helper"_s,
		0,
		NewObjClear(CinemaMcpHelper));
}

Bool PluginStart()
{
	if (!RegisterMcpHelperPlugin())
		return false;

	return true;
}

void PluginEnd()
{
}

Bool PluginMessage(Int32 id, void* data)
{
	switch (id)
	{
		case C4DPL_INIT_SYS:
			if (!g_resource.Init())
				return false;
			return true;

		case C4DMSG_PRIORITY:
			return true;
	}

	return false;
}

} // namespace cinema
