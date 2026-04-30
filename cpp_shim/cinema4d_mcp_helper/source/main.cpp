/* cinema4d_mcp_helper — C++ companion plugin for cinema4d-mcp.

PHASE A.1 (2026-04-30): wraps GraphModelInterface::AddPort for named-port
creation on a Floating IO node inside an SN Generator's embedded graph.
This is what the C4D editor calls on drag-wire when an artist exposes a
parameter — Python's maxon.frameworks.{nodes,graph} module doesn't wrap
the singular AddPort form, so we do it here.

Phase A.0 (2026-04-30 earlier): minimal MessageData skeleton — verified
plugin loads + is discoverable via FindPlugin. Bridge proven live.

Protocol (Python <-> C++ via shared GetWorldContainerInstance):
  Python writes args under key MCP_HELPER_BC namespace, then fires
  c4d.SendCoreMessage(MSG_MCP_HELPER_REQ, c4d.BaseContainer()). C++
  CoreMessage reads world container, dispatches by op-code, writes
  result back. Python reads result. Single in-flight only — MCP socket
  serializes calls at the orchestration layer.

Style follows Spikr2: namespace cinema { ... } block, separate main.h
with `using namespace cinema;`, tabs for indentation, stylecheck level 0.
*/

#include "main.h"

#include "maxon/graph.h"
#include "maxon/nodesgraph.h"
#include "maxon/nodesgraph_helpers.h"
#include "maxon/node_spaces.h"
#include "maxon/nodes_all.h"
#include "maxon/nimbusbase.h"

namespace cinema
{

// Plugin ID — sibling to the existing Python plugin family
//   1057843  PLUGIN_ID         (Python MCP server SpecialEventAdd)
//   1057844  UI_OBSERVER_PID   (UiActionObserver MessageData)
//   1057845  MCP_HELPER_PID    (this — C++ companion)         <-- HERE
const Int32 MCP_HELPER_PLUGIN_ID = 1057845;

// Phase protocol version. Increment when wire shape changes.
const Int32 MCP_HELPER_PROTOCOL_VERSION = 2; // 1=A.0 (ping only), 2=A.1 (AddPort)

// Custom CoreMessage IDs we listen for. Use values >= our plugin ID *1000
// to avoid collision with the C4D builtin range.
const Int32 MSG_MCP_HELPER_REQ = 1057845001;

// World-container BC keys for the shared request/response protocol. All
// under our plugin's reserved range to avoid collisions with C4D core
// settings under the same singleton.
const Int32 BC_KEY_OP             = 1057845010;  // Int32 — operation code
const Int32 BC_KEY_TARGET         = 1057845011;  // String — graph_target object name
const Int32 BC_KEY_NODE_ID        = 1057845012;  // String — node instance ID (e.g. "floatingio@HASH")
const Int32 BC_KEY_PORT_NAME      = 1057845013;  // String — port name to add
const Int32 BC_KEY_IS_OUTPUT      = 1057845014;  // Bool — true = add to outputs, false = inputs
const Int32 BC_KEY_STATUS         = 1057845020;  // Int32 — 0=success, !=0=error
const Int32 BC_KEY_STATUS_MSG     = 1057845021;  // String — human-readable status / error
const Int32 BC_KEY_NEW_PORT_ID    = 1057845022;  // String — resulting port ID on success
const Int32 BC_KEY_PROTOCOL_VER   = 1057845023;  // Int32 — protocol version for handshake

// Op codes
const Int32 OP_PING               = 0; // returns protocol version, status=0
const Int32 OP_ADD_FLOATING_IO_PORT = 1;

// ============================================================================
// AddPort implementation — Phase A.1 core
// ============================================================================

// Walk a graph recursively to find a node whose ID's last path segment
// matches target_id. Returns invalid GraphNode if not found.
static maxon::GraphNode FindNodeByIdSegment(const maxon::GraphNode& root,
											const String& targetIdSegment,
											Int32 maxDepth = 14)
{
	if (maxDepth < 0)
		return maxon::GraphNode();

	// Check direct children first
	maxon::BaseArray<maxon::GraphNode> children;
	iferr (root.GetChildren(children, maxon::NODE_KIND::NODE))
	{
		// fall through; recursion may still find it via different path
	}

	for (Int32 i = 0; i < children.GetCount(); ++i)
	{
		const maxon::GraphNode& child = children[i];
		String childId = child.GetId().ToString();
		// match against last segment after final '/'
		Int slashPos = childId.FindLast('/');
		String lastSeg = (slashPos >= 0) ? childId.Right(childId.GetLength() - slashPos - 1)
		                                  : childId;
		if (lastSeg == targetIdSegment)
			return child;
		// recurse
		maxon::GraphNode found = FindNodeByIdSegment(child, targetIdSegment, maxDepth - 1);
		if (found.IsValid())
			return found;
	}
	return maxon::GraphNode();
}

// Returns 0 on success; sets status_msg on failure. Writes new_port_id to
// world container on success.
static Int32 DoAddFloatingIOPort(BaseContainer* wc)
{
	const String targetName = wc->GetString(BC_KEY_TARGET);
	const String fioNodeId  = wc->GetString(BC_KEY_NODE_ID);
	const String portName   = wc->GetString(BC_KEY_PORT_NAME);
	const Bool isOutput     = wc->GetBool(BC_KEY_IS_OUTPUT);

	if (!targetName.GetLength())
	{
		wc->SetString(BC_KEY_STATUS_MSG, "graph_target (target object name) is required for Phase A.1"_s);
		return 1;
	}
	if (!fioNodeId.GetLength())
	{
		wc->SetString(BC_KEY_STATUS_MSG, "fio_node_id is required"_s);
		return 2;
	}
	if (!portName.GetLength())
	{
		wc->SetString(BC_KEY_STATUS_MSG, "port_name is required"_s);
		return 3;
	}

	BaseDocument* doc = GetActiveDocument();
	if (!doc)
	{
		wc->SetString(BC_KEY_STATUS_MSG, "no active document"_s);
		return 10;
	}

	BaseObject* host = doc->SearchObject(targetName);
	if (!host)
	{
		wc->SetString(BC_KEY_STATUS_MSG, "graph_target '" + targetName + "' not found in active document"_s);
		return 11;
	}

	// Get the Scene Nodes graph from the host BaseObject
	maxon::NimbusBaseRef nimbus = host->GetNimbusRef(maxon::neutron::NODESPACE);
	if (nimbus == nullptr)
	{
		wc->SetString(BC_KEY_STATUS_MSG, "host '" + targetName + "' has no Scene Nodes graph (NimbusRef null)"_s);
		return 12;
	}

	const maxon::nodes::NodesGraphModelRef& graph = nimbus.GetGraph();
	if (!graph)
	{
		wc->SetString(BC_KEY_STATUS_MSG, "graph model is null on host '" + targetName + "'"_s);
		return 13;
	}
	if (graph.IsReadOnly())
	{
		wc->SetString(BC_KEY_STATUS_MSG, "graph is read-only on host '" + targetName + "'"_s);
		return 14;
	}

	// Find the FIO node by walking
	maxon::GraphNode root = graph.GetRoot();
	maxon::GraphNode fio = FindNodeByIdSegment(root, fioNodeId);
	if (!fio.IsValid())
	{
		wc->SetString(BC_KEY_STATUS_MSG, "fio_node_id '" + fioNodeId + "' not found in graph"_s);
		return 20;
	}

	// AddPort under transaction
	String newPortIdStr;
	String resultMsg;
	Int32 status = 0;
	{
		iferr_scope_handler
		{
			resultMsg = err.GetMessage();
			status = 30;
			return;
		};

		maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;

		// Convert port_name (cinema::String) to maxon::Id
		// Cinema String has GetCString() but for maxon::Id we use Init(const cinema::String&)
		maxon::Id portId;
		portId.Init(portName) iferr_return;

		// Decide which side to add to: inputs or outputs
		maxon::GraphNode parent;
		if (isOutput)
			parent = fio.GetOutputs();
		else
			parent = fio.GetInputs();

		if (!parent.IsValid())
		{
			resultMsg = "FIO has no inputs/outputs container port"_s;
			status = 31;
			return;
		}

		// AddPort returns Result<GraphNode> for the newly-created port
		maxon::GraphNode newPort = parent.AddPort(portId) iferr_return;
		if (!newPort.IsValid())
		{
			resultMsg = "AddPort returned invalid GraphNode"_s;
			status = 32;
			return;
		}

		newPortIdStr = newPort.GetId().ToString();

		txn.Commit() iferr_return;
	}

	if (status != 0)
	{
		wc->SetString(BC_KEY_STATUS_MSG, resultMsg);
		return status;
	}

	wc->SetString(BC_KEY_NEW_PORT_ID, newPortIdStr);
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
		(void)bc; // request payload comes via shared world container, not bc

		if (id != MSG_MCP_HELPER_REQ)
			return true;

		BaseContainer* wc = GetWorldContainerInstance();
		if (!wc)
			return true; // shared state unavailable; nothing we can do

		const Int32 op = wc->GetInt32(BC_KEY_OP);

		// Initialize response slots
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
