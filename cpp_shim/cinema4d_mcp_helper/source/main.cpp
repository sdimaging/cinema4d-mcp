/* cinema4d_mcp_helper — C++ companion plugin for cinema4d-mcp.

STATUS (2026-04-30): bridge architecture proven; Phase A.1 (FIO AddPort)
abandoned per gotcha #36 — runtime AddPort on FloatingIO is fundamentally
unsupported by the C4D 2026 runtime regardless of language/wrapping.
Phase B (NodeTemplate publishing via maxon::nodes::MutableRoot) is the
actual path for user-tunable capsules with AM-surfaced params.

Phase A.0 (kept): MessageData skeleton — plugin loads, is discoverable
via FindPlugin(1057845, PLUGINTYPE_COREMESSAGE), responds to OP_PING
through the SpecialEventAdd dispatch protocol.

Phase A.1 dispatch (kept; reusable for Phase B):
  Python:
    1. Worker thread writes args to GetWorldContainerInstance under
       reserved BC keys.
    2. Calls execute_on_main_thread(_write_and_fire) -> writes args +
       SpecialEventAdd(MCP_HELPER_PLUGIN_ID, op_code, 0).
    3. Worker sleeps + polls execute_on_main_thread(_read) until
       BC_KEY_STATUS != -1 sentinel (or timeout).
  C++:
    CoreMessage(EVMSG_CHANGE) fires -> filter by bc.GetInt32(BFM_CORE_ID)
    == MCP_HELPER_PLUGIN_ID -> read op-code from BFM_CORE_PAR1 -> dispatch
    -> write status / message / response payload back to world container.

OP_ADD_FLOATING_IO_PORT now returns a clear "unsupported" error pointing
at gotcha #36 instead of rejecting via cryptic Maxon runtime errors.

Phase B will add OP_PUBLISH_NODETEMPLATE: takes a typed-port spec, builds
a maxon::nodes::MutableRoot, registers as net.maxon.node.assettype.
nodetemplate (.c4dnodes) asset. Per GPT discipline: start with the
minimal possible template (1 input -> 1 internal node -> 1 output) before
scaling.
*/

#include "main.h"

#include "maxon/graph.h"
#include "maxon/nodesgraph.h"
#include "maxon/nodesgraph_helpers.h"
#include "maxon/node_spaces.h"
#include "maxon/nodes_all.h"
#include "maxon/nimbusbase.h"
#include "maxon/neutron_ids.h"
#include "maxon/commandobservable.h"

namespace cinema
{

const Int32 MCP_HELPER_PLUGIN_ID = 1057845;

const Int32 MCP_HELPER_PROTOCOL_VERSION = 8; // 8 adds bulk-swap mutation v3 (atomic remove+rewire).

// Phase A.1 dispatch mechanism: Python calls c4d.SpecialEventAdd(
//   MCP_HELPER_PLUGIN_ID, op_code, 0) which broadcasts a CoreMessage to
// every MessageData plugin. The BC carries the plugin_id at BFM_CORE_ID
// and the op-code at BFM_CORE_PAR1. We filter by BFM_CORE_ID match — only
// our own SpecialEventAdd calls are processed.
//
// Direct c4d.SendCoreMessage(custom_id, bc) does NOT broadcast custom IDs
// (verified empirically 2026-04-30 — see gotcha #34). Direct
// BasePlugin.Message() also doesn't route to MessageData::CoreMessage.
// SpecialEventAdd is the only Python -> C++ bridge that fires CoreMessage.

const Int32 BC_KEY_OP             = 1057845010;
const Int32 BC_KEY_TARGET         = 1057845011;
const Int32 BC_KEY_NODE_ID        = 1057845012;
const Int32 BC_KEY_PORT_NAME      = 1057845013;
const Int32 BC_KEY_IS_OUTPUT      = 1057845014;
const Int32 BC_KEY_STATUS         = 1057845020;
const Int32 BC_KEY_STATUS_MSG     = 1057845021;
const Int32 BC_KEY_NEW_PORT_ID    = 1057845022;
const Int32 BC_KEY_PROTOCOL_VER   = 1057845023;
const Int32 BC_KEY_DEBUG          = 1057845030; // optional debug trail (Phase A.1)

// Phase A.2 — promiscuous CoreMessage logger key (read-back only)
const Int32 BC_KEY_LOG_DUMP       = 1057845041; // String — read by client
// Scene Nodes bulk swap scaffold. These are intentionally string-only so the
// Python bridge can pass/resume audit state without new binary protocol work.
const Int32 BC_KEY_BULK_SWAP_INPUT          = 1057845060; // String — line-delimited specs
const Int32 BC_KEY_BULK_SWAP_RESULT         = 1057845061; // String — line-delimited audit
// 1057845062 reserved for BULK_SWAP_SNAPSHOT_PREFIX (future per-spec snapshot save)
const Int32 BC_KEY_BULK_SWAP_MUTATE         = 1057845063; // Bool — opt-in mutation after preflight

const Int32 OP_PING                   = 0;
const Int32 OP_ADD_FLOATING_IO_PORT   = 1;
// Phase A.2 promiscuous-logger ops (per-GPT empirical-probe approach)
const Int32 OP_LOGGER_START           = 10;
const Int32 OP_LOGGER_STOP            = 11;
const Int32 OP_LOGGER_READ            = 12;
const Int32 OP_LOGGER_CLEAR           = 13;
// Phase A.2.1 maxon command-framework observer ops (the definitive probe
// after A.2 returned only UI broadcasts — confirms editor right-click
// "Add Input" goes through the maxon command framework, not legacy
// CoreMessage. See docs/cpp_shim_phase_a2_1_design.md.)
const Int32 OP_OBSERVER_START         = 20;
const Int32 OP_OBSERVER_STOP          = 21;
const Int32 OP_OBSERVER_READ          = 22;
const Int32 OP_OBSERVER_CLEAR         = 23;
// Phase A.2.2 — direct registry enumeration. CommandClasses is the maxon
// command framework's registry of every registered command's class. We
// can iterate it and dump every registered Id, then search for ones
// matching "input"/"port"/"floating"/etc. This is more direct than
// observer-based capture: we get the FULL list of every callable command,
// regardless of whether the user invoked it.
const Int32 OP_LIST_COMMANDS          = 30;
// Bulk graph surgery op. Current implementation is a safe scaffold only:
// it proves the bridge/input/audit contract and refuses mutation until the
// preflight + atomic transaction body is implemented.
const Int32 OP_BULK_SWAP_NODES        = 50;

const Int32 SN_DEFORMER_PLUGIN_ID     = 180420400;

// Phase A.2 — promiscuous CoreMessage logger state. When active, every
// CoreMessage that fires (including ones NOT from our own SpecialEventAdd)
// is appended to a static String. User runs the probe scenario
// (right-click port → Add Input) and we read back what message IDs
// fired during that gesture.
//
// IMPORTANT (per GPT review): this is a CHEAP FIRST NET, not the
// definitive probe. CoreMessage covers legacy BFM_*/plugin messages but
// does NOT cover the maxon command framework's ObservableCommandInvokedInfo.
// If this logger finds nothing during Add Input/Add Output, that does
// NOT prove "no command exists" — it only proves the legacy message path
// is empty. Phase A.2.1 (CommandObserverInterface subscription via
// frameworks/command.framework/commandobservable.h) is the actual
// definitive probe and must be tried before declaring the editor action
// non-callable.
//
// Cap log at 1000 entries to prevent runaway if user forgets to stop.
static maxon::Bool g_loggerActive = false;
static String      g_msgLog;
static Int32       g_msgLogCount = 0;
static const Int32 G_MSG_LOG_CAP = 1000;

// Phase A.2.1 — maxon command framework observer state. Subscription is
// established lazily on first OP_OBSERVER_START and then KEPT for the
// plugin's lifetime (we leak the ticket — simpler than tracking the
// FunctionBaseRef type, and the plugin lifetime is the only concern).
// The g_cmdObserverActive flag gates whether the callback writes to the
// log, allowing repeated start/stop cycles cheaply.
static maxon::CommandObserverRef g_cmdObserver;
static maxon::Bool g_cmdObserverSubscribed = false;
static maxon::Bool g_cmdObserverActive = false;
static String      g_cmdObserverLog;
static Int32       g_cmdObserverCount = 0;
static const Int32 G_CMD_OBSERVER_CAP = 1000;
const Int32 BC_KEY_CMD_OBSERVER_DUMP = 1057845050;

#if 0  // Phase A.1 AddPort experiment — kept commented out as documentation
       // (see commit 670ae14 + docs/cpp_shim_phase_b_design.md). Wrapping
       // in #if 0 lets the SDK API references stay in source for Phase B
       // reuse without triggering "unused function" warnings (-WX).

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
// AddPort implementation — Phase A.1 historical (kept for future revisit)
// Currently the dispatch routes OP_ADD_FLOATING_IO_PORT to a clean
// "unsupported" reply; the implementation below is retained so the file
// keeps the SDK API references for documentation and Phase B reuse.
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

	// Walk top-level children of view root. Switched from GetInnerNodes
	// (recursive) to GetChildren (one level) — the FIO we want is at the
	// top level for our use case, and the recursive walk previously matched
	// the root node itself somehow (live error: "You can't add a port
	// directly to node Root").
	//
	// Diagnostic: build a debug trail in cinema::String (which is what
	// BaseContainer::SetString expects) — captures each candidate's ID and
	// whether it matched. Python reads this from BC_KEY_STATUS_MSG.
	String debug;
	debug += "rootId=";
	debug += MaxonConvert(root.GetId().ToString());
	debug += " target=";
	debug += MaxonConvert(fioTargetMaxon);
	debug += " candidates=[";
	maxon::Bool firstCandidate = true;

	root.GetChildren([&foundFio, &fioTargetMaxon, &debug, &firstCandidate](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool>
		{
			if (!firstCandidate)
				debug += ", ";
			firstCandidate = false;
			debug += MaxonConvert(candidate.GetId().ToString());
			if (foundFio.IsValid())
				return maxon::Bool(true);
			if (MatchesNodeIdSegment(candidate, fioTargetMaxon))
			{
				foundFio = candidate;
				debug += "(MATCH)";
			}
			return maxon::Bool(true);
		}, maxon::NODE_KIND::NODE) iferr_return;

	debug += "]";
	wc->SetString(BC_KEY_DEBUG, debug);

	if (!foundFio.IsValid())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION,
			"fio_node_id not found among top-level children of view root"_s);

	// AddPort on the FIO node DIRECTLY, not on its GetInputs/GetOutputs
	// containers. Live error from a previous attempt was:
	//   "Illegal argument: Condition PrivateIsNodeAFloatingIo(trueNode)
	//    not fulfilled."
	// — the implementation checks that the parent IS a FloatingIO node.
	// The hidden-vs-visible port pair (hiddenin1.<path> + in1.<path>) is
	// created automatically by AddPort when the parent is a FIO; direction
	// is controlled by the FIO's net.maxon.node.floatingio.attribute.
	// direction Bool node-attribute, set separately.
	//
	// Note: isOutput parameter retained for API compatibility but does NOT
	// route to a different parent — see comment above. Future revision can
	// SetValue on attribute.direction here if needed.
	(void)isOutput;

	maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;

	const maxon::String portNameMaxon = MaxonConvert(portName);
	maxon::Id portId;
	portId.Init(portNameMaxon) iferr_return;

	// AddPort on the FIO itself — the iferr block unwraps Result<GraphNode>.
	maxon::GraphNode newPort;
	{
		iferr (maxon::GraphNode added = foundFio.AddPort(portId))
		{
			return err;
		}
		newPort = added;
	}
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

#endif  // Phase A.1 AddPort experiment

// ============================================================================
// Phase A.2.2 — direct registry enumeration of CommandClasses
// ============================================================================

// Dump every registered maxon command class Id to the cmd-observer log.
// Pattern: maxon::CommandClasses::GetEntriesWithId() iterates the registry.
// Each entry has GetKey() (the maxon::Id) and GetValue() (the class).
static maxon::Result<void> ListCommandClasses_Impl(maxon::Bool filter,
                                                    const maxon::String& filterStr)
{
	iferr_scope;
	String dump;
	Int32 count = 0;
	Int32 matched = 0;
	for (const auto& entry : maxon::CommandClasses::GetEntriesWithId())
	{
		count++;
		const maxon::Id idObj = entry.GetKey();
		const maxon::String idStr = idObj.ToString();
		if (filter)
		{
			// case-insensitive substring filter
			maxon::Int found;
			if (!idStr.FindUpper(filterStr, &found))
				continue;
		}
		matched++;
		dump += MaxonConvert(idStr);
		dump += "\n";
	}
	g_cmdObserverLog = dump;
	g_cmdObserverCount = matched;
	return maxon::OK;
}

// ============================================================================
// Phase A.2.1 — CommandObserverInterface subscription helper
// ============================================================================

// Lazy first-time subscription. Once subscribed, the observer stays
// subscribed for the plugin lifetime. The g_cmdObserverActive flag gates
// callback logging.
static maxon::Result<void> EnsureCommandObserverSubscribed_Impl()
{
	iferr_scope;

	if (g_cmdObserverSubscribed)
		return maxon::OK;

	// Create an observer instance via the registered class.
	// OPEN QUESTION: this creates a fresh instance — if the global command
	// system fires events through a SPECIFIC singleton, we won't receive
	// them. Sanity-check: subscribe + invoke a known command via
	// CallCommand → if the callback fires, we have the right instance.
	// If not, need to find the singleton accessor.
	g_cmdObserver = maxon::CommandObserverObjectClass().Create() iferr_return;

	// Subscribe to ObservableCommandInvokedInfo (the most informative
	// observable — fires at every stage of command invocation with the
	// full CommandDataRef + InvocationState context).
	g_cmdObserver.ObservableCommandInvokedInfo(true).AddObserver(
		[](const maxon::Id& cid,
		   const maxon::Result<maxon::COMMANDRESULT>& res,
		   const maxon::CommandDataRef& data,
		   const maxon::InvocationState& state) -> maxon::Result<void>
		{
			(void)res;
			(void)data;
			if (g_cmdObserverActive && g_cmdObserverCount < G_CMD_OBSERVER_CAP)
			{
				g_cmdObserverCount++;
				g_cmdObserverLog += "[";
				g_cmdObserverLog += String::IntToString((Int64)g_cmdObserverCount);
				g_cmdObserverLog += "] cmdId=";
				g_cmdObserverLog += MaxonConvert(cid.ToString());
				g_cmdObserverLog += " interactive=";
				g_cmdObserverLog += state._interactive ? "true" : "false";
				g_cmdObserverLog += " interaction=";
				g_cmdObserverLog += String::IntToString((Int64)(maxon::Int)state._interaction);
				g_cmdObserverLog += "\n";
			}
			return maxon::OK;
		}) iferr_return;

	g_cmdObserverSubscribed = true;
	return maxon::OK;
}

// ============================================================================
// Scene Nodes bulk-swap scaffold
// ============================================================================

static BaseObject* FindFirstObjectOfType(BaseObject* obj, Int32 typeId)
{
	while (obj)
	{
		if (obj->GetType() == typeId)
			return obj;
		if (BaseObject* childHit = FindFirstObjectOfType(obj->GetDown(), typeId))
			return childHit;
		obj = obj->GetNext();
	}
	return nullptr;
}

static Bool SplitBulkSwapLine(const String& line, String& ogId, String& myName, String& assetId)
{
	maxon::Int firstPipe = NOTOK;
	if (!line.Find("|"_s, &firstPipe))
		return false;

	const String rest = line.GetPart(firstPipe + 1, line.GetLength() - firstPipe - 1);
	maxon::Int secondPipe = NOTOK;
	if (!rest.Find("|"_s, &secondPipe))
		return false;

	ogId = line.GetPart(0, firstPipe);
	myName = rest.GetPart(0, secondPipe);
	assetId = rest.GetPart(secondPipe + 1, rest.GetLength() - secondPipe - 1);
	return ogId.GetLength() > 0 && myName.GetLength() > 0 && assetId.GetLength() > 0;
}

// Bulk swap status codes (returned via BC_KEY_STATUS).
// Distinct codes per failure class so callers can branch on the int alone;
// the audit rows carry per-spec detail when one call mixes outcomes.
//   0  = ok                : all specs mutated successfully (mutate=true path)
//   90 = setup_err         : null world container
//   91 = empty_input       : no specs supplied
//   92 = no_doc            : no active document
//   93 = no_host           : no SN Deformer host (180420400) found
//   94 = preflight_ok      : every spec passed preflight, mutate=false (dry run)
//   95 = missing_og        : at least one spec's OG node not found (first-failure)
//   96 = already_swapped   : at least one spec's MY name already exists (first-failure)
//   97 = malformed_spec    : at least one spec line failed to parse (first-failure)
//   98 = graph_error       : failed to open / walk the SN graph
//   99 = mutation_partial  : preflight passed but AddChild failed for one or more specs
//
// First-failure rule: when multiple specs fail with different reasons in one
// call, the overall status reflects the FIRST failure encountered. Per-spec
// detail lives in the audit row's status field.
//
// Per audit row status field (always one of these strings):
//   preflight_ok            : OG present, MY name available, ready to mutate (dry run)
//   mutated_addchild_only   : v1 — MY created via AddChild only
//   mutated_addchild_mirror : v2 — MY created + inputs mirrored (parallel reading)
//   swapped                 : v3 — full 1-1 swap (AddChild + mirror + atomic remove+rewire)
//   missing_og              : OG node id not found in graph top-level children
//   already_swapped         : MY node name already exists in graph (collision)
//   malformed_spec          : line failed to parse as og_id|my_name|asset_id
//   duplicate_my_in_batch   : two specs in one call requested the same MY name
//   addchild_err            : preflight passed but AddChild transaction failed
//   mirror_err              : AddChild succeeded but input mirror failed
//   capture_outputs_err     : output wire walk failed
//   atomic_err              : atomic remove+rewire transaction failed

// Open the SN deformer host's neutron graph. Returns the graph by reference;
// caller validates the return code before using it.
static maxon::Result<maxon::nodes::NodesGraphModelRef> OpenSNGraph(BaseObject* host)
{
	iferr_scope;

	maxon::NimbusBaseRef nimbus = host->GetNimbusRef(maxon::neutron::NODESPACE);
	if (nimbus == nullptr)
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION,
			"host has no Scene Nodes graph (NimbusRef null)"_s);

	const maxon::nodes::NodesGraphModelRef& graph = nimbus.GetGraph();
	if (!graph)
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION, "graph model is null"_s);

	return graph;
}

// Walk the SN deformer host's neutron graph and append top-level child node
// ids to `outIds` (one per child). Returns Result<void>; iferr_return
// surfaces any maxon::Error to the caller.
static maxon::Result<void> CollectTopLevelChildIds(BaseObject* host,
                                                    maxon::HashSet<maxon::String>& outIds)
{
	iferr_scope;

	maxon::nodes::NodesGraphModelRef graph = OpenSNGraph(host) iferr_return;

	maxon::GraphNode root = graph.GetViewRoot();
	if (!root.IsValid())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION, "graph view root is invalid"_s);

	root.GetChildren([&outIds](const maxon::GraphNode& child) -> maxon::Result<maxon::Bool>
		{
			iferr_scope;
			outIds.Insert(child.GetId().ToString()) iferr_return;
			return maxon::Bool(true);
		}, maxon::NODE_KIND::NODE) iferr_return;

	return maxon::OK;
}

// Walk the graph's top-level NODE children to find one by exact Id.
// Returns an invalid GraphNode if not found (caller checks IsValid()).
static maxon::Result<maxon::GraphNode> FindTopLevelNodeById(
    maxon::nodes::NodesGraphModelRef& graph, const maxon::String& targetId)
{
	iferr_scope;

	maxon::GraphNode root = graph.GetViewRoot();
	if (!root.IsValid())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION, "graph view root is invalid"_s);

	maxon::GraphNode found;
	root.GetChildren([&found, &targetId](const maxon::GraphNode& candidate) -> maxon::Result<maxon::Bool>
		{
			if (candidate.GetId().ToString() == targetId)
			{
				found = candidate;
				return maxon::Bool(false);  // stop walk
			}
			return maxon::Bool(true);
		}, maxon::NODE_KIND::NODE) iferr_return;
	return found;
}

// Mutation v1: AddChild for one spec, in its own transaction. The asset_id
// must be a registered NodeTemplate Id; AddChild validates this internally
// and returns an error if unknown. Returns Result<GraphNode> (the new MY
// node); caller can use it to wire after the commit.
static maxon::Result<maxon::GraphNode> AddChildOnly(maxon::nodes::NodesGraphModelRef& graph,
                                                     const maxon::String& myName,
                                                     const maxon::String& assetId)
{
	iferr_scope;

	maxon::Id childId;
	childId.Init(myName) iferr_return;
	maxon::Id nodeId;
	nodeId.Init(assetId) iferr_return;

	maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;
	maxon::GraphNode added = graph.AddChild(childId, nodeId) iferr_return;
	if (!added.IsValid())
		return maxon::UnexpectedError(MAXON_SOURCE_LOCATION,
			"AddChild returned invalid GraphNode"_s);
	txn.Commit() iferr_return;

	return added;
}

// Find the id of the node that owns a given port, by asking the SDK for
// the nearest NODE_KIND::NODE ancestor. Works for any node id (no name
// heuristic — earlier versions failed on synthetic test names that
// didn't contain '@' or 'MY_' or 'context_'). Returns empty string only
// if the port has no node ancestor (e.g. wire to root gateway).
static String FindOwningNodeId(const maxon::GraphNode& port)
{
	iferr_scope_handler { return String(); };
	if (!port.IsValid())
		return String();
	maxon::GraphNode owner = port.GetAncestor(maxon::NODE_KIND::NODE) iferr_return;
	if (!owner.IsValid())
		return String();
	// GetAncestor for a port returns the nearest NODE; if that node is the
	// graph root itself, treat as "no owner" since rooting up to the graph
	// root means we walked off the actual graph nodes.
	if (owner.IsRoot())
		return String();
	return MaxonConvert(owner.GetId().ToString());
}

// Per-output-wire record: where MY's matching output port should re-connect
// after we delete OG. Captured pre-mutation; resolved to handles inside the
// atomic transaction (so dst handles are still valid through the Remove).
struct OutputWire
{
	String ogOutPortId;  // port id on OG's output side (== port id on MY's output side, same asset)
	String dstNodeId;    // id of the downstream node
	String dstPortId;    // id of the downstream node's input port
};

// Read-only walk of OG's output ports + their downstream connections.
// Captures (og_out_port_id, dst_node_id, dst_port_id) for every wire.
// Wires whose dst port has no resolvable owning-node id (e.g. root
// gateway destinations) are skipped — caller handles via audit message.
static maxon::Result<void> CaptureOutputWires(const maxon::GraphNode& ogNode,
                                                maxon::BaseArray<OutputWire>& outWires,
                                                Int32& outSkipped)
{
	iferr_scope;

	maxon::GraphNode ogOutputs = ogNode.GetOutputs() iferr_return;
	if (!ogOutputs.IsValid())
		return maxon::OK;  // no output port list — nothing to capture

	ogOutputs.GetChildren(
		[&outWires, &outSkipped](const maxon::GraphNode& ogPort) -> maxon::Result<maxon::Bool>
		{
			iferr_scope;
			const String ogPortIdStr = MaxonConvert(ogPort.GetId().ToString());

			ogPort.GetConnections(maxon::PORT_DIR::OUTPUT,
				[&outWires, &outSkipped, &ogPortIdStr](const maxon::GraphConnection& conn)
					-> maxon::Result<maxon::Bool>
				{
					iferr_scope;
					const maxon::GraphNode& dstPort = conn.first;
					if (!dstPort.IsValid())
					{
						outSkipped++;
						return maxon::Bool(true);
					}
					const String dstPortIdStr = MaxonConvert(dstPort.GetId().ToString());
					const String dstNodeIdStr = FindOwningNodeId(dstPort);
					if (dstNodeIdStr.GetLength() == 0)
					{
						outSkipped++;  // root-gateway destination, skip
						return maxon::Bool(true);
					}
					OutputWire ow;
					ow.ogOutPortId = ogPortIdStr;
					ow.dstNodeId = dstNodeIdStr;
					ow.dstPortId = dstPortIdStr;
					outWires.Append(ow) iferr_return;
					return maxon::Bool(true);
				}) iferr_return;
			return maxon::Bool(true);
		}, maxon::NODE_KIND::OUTPORT) iferr_return;

	return maxon::OK;
}

// Mutation v3 — the load-bearing atomic transition. In ONE transaction:
//   1. Pre-resolve every dst port handle + MY's matching output port handle
//      (still valid because OG hasn't been removed yet)
//   2. ogNode.Remove() — severs OG's wires INSIDE the transaction
//   3. For each (myOut, dstPort) pair: myOut.Connect(dstPort)
//   4. Commit — single atomic state transition; graph evaluator never
//      observes the intermediate broken state
//
// This is the load-bearing fix per gotcha #69 — *access* component sub-port
// destinations crash hard if OG's wire is severed without an immediate
// reconnect. Doing both in one tx makes them indistinguishable from a
// "no change" event from the evaluator's perspective.
//
// Returns the number of wires successfully rewired.
static maxon::Result<Int32> AtomicRemoveAndRewire(maxon::nodes::NodesGraphModelRef& graph,
                                                    maxon::GraphNode ogNode,
                                                    maxon::GraphNode myNode,
                                                    const maxon::BaseArray<OutputWire>& outWires)
{
	iferr_scope;

	if (!ogNode.IsValid() || !myNode.IsValid())
		return maxon::IllegalArgumentError(MAXON_SOURCE_LOCATION,
			"AtomicRemoveAndRewire: invalid GraphNode arg"_s);

	maxon::GraphNode myOutputs = myNode.GetOutputs() iferr_return;

	maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;

	// Phase A — resolve every dst handle BEFORE the destructive Remove.
	// Captured tuples reference ports by Id; we look them up live so the
	// returned GraphNode handles are stable across the remove.
	struct ResolvedPair { maxon::GraphNode myOut; maxon::GraphNode dstPort; };
	maxon::BaseArray<ResolvedPair> pairs;
	Int32 skippedAtResolve = 0;
	for (const OutputWire& ow : outWires)
	{
		const maxon::String dstNodeIdMaxon = MaxonConvert(ow.dstNodeId);
		maxon::GraphNode dstNode;
		iferr (dstNode = FindTopLevelNodeById(graph, dstNodeIdMaxon))
		{
			skippedAtResolve++;
			continue;
		}
		if (!dstNode.IsValid())
		{
			skippedAtResolve++;
			continue;
		}
		maxon::GraphNode dstInputs = dstNode.GetInputs() iferr_return;
		if (!dstInputs.IsValid())
		{
			skippedAtResolve++;
			continue;
		}
		maxon::Id dstPortId;
		dstPortId.Init(MaxonConvert(ow.dstPortId)) iferr_return;
		maxon::GraphNode dstPort = dstInputs.FindChild(dstPortId) iferr_return;
		if (!dstPort.IsValid())
		{
			skippedAtResolve++;
			continue;
		}
		maxon::Id myOutPortId;
		myOutPortId.Init(MaxonConvert(ow.ogOutPortId)) iferr_return;
		maxon::GraphNode myOut = myOutputs.FindChild(myOutPortId) iferr_return;
		if (!myOut.IsValid())
		{
			skippedAtResolve++;
			continue;
		}
		ResolvedPair rp;
		rp.myOut = myOut;
		rp.dstPort = dstPort;
		pairs.Append(rp) iferr_return;
	}

	// Phase B — remove OG (severs its wires inside the tx).
	ogNode.Remove() iferr_return;

	// Phase C — re-fill the dst slots with MY's output. Same tx so the
	// graph evaluator sees a single atomic transition.
	Int32 rewired = 0;
	for (const ResolvedPair& rp : pairs)
	{
		iferr (rp.myOut.Connect(rp.dstPort))
		{
			// Connect failed — keep going for the rest, surface error count
			// in audit. Don't roll back the tx; per Python pattern, partial
			// success is informative.
			continue;
		}
		rewired++;
	}

	txn.Commit() iferr_return;
	(void)skippedAtResolve;  // could surface to caller in future
	return rewired;
}

// Mutation v2: mirror OG's input connections onto MY in a single transaction.
// Walks each of OG's input ports, captures the source side of every wire,
// and creates the same wire pointing at MY's matching input port (matched
// by port id — works because MY is the same asset_id as OG, so port
// topology is identical). "Parallel reading": original wires stay intact.
//
// SDK note: graph.CopyConnectionsFrom() exists on GraphModelInterface but
// is NOT brought into NodesGraphModelRef via MAXON_USING, so we walk
// manually. Port-level Connect() (graph.h:1510) handles the actual wire.
//
// Returns the number of wires successfully mirrored.
static maxon::Result<Int32> MirrorInputs(maxon::nodes::NodesGraphModelRef& graph,
                                          const maxon::GraphNode& myNode,
                                          const maxon::GraphNode& ogNode)
{
	iferr_scope;

	if (!myNode.IsValid() || !ogNode.IsValid())
		return maxon::IllegalArgumentError(MAXON_SOURCE_LOCATION,
			"MirrorInputs: invalid GraphNode arg"_s);

	maxon::GraphNode ogInputs = ogNode.GetInputs() iferr_return;
	maxon::GraphNode myInputs = myNode.GetInputs() iferr_return;
	if (!ogInputs.IsValid() || !myInputs.IsValid())
		return Int32(0);  // node has no input port list — nothing to mirror

	maxon::GraphTransaction txn = graph.BeginTransaction() iferr_return;

	Int32 wireCount = 0;
	ogInputs.GetChildren(
		[&graph, &myInputs, &wireCount](const maxon::GraphNode& ogPort) -> maxon::Result<maxon::Bool>
		{
			iferr_scope;
			const maxon::String ogPortIdStr = ogPort.GetId().ToString();

			// Find MY's matching input port by id. If not present (asset_id
			// mismatch), skip silently — caller's responsibility to ensure
			// port topology compatibility.
			maxon::Id portId;
			portId.Init(ogPortIdStr) iferr_return;
			maxon::GraphNode myPort = myInputs.FindChild(portId) iferr_return;
			if (!myPort.IsValid())
				return maxon::Bool(true);

			// For every wire feeding OG's input port, create the same wire
			// to MY's matching port. conn.first is the source port (GraphNode),
			// conn.second is Wires metadata (kept as default).
			ogPort.GetConnections(maxon::PORT_DIR::INPUT,
				[&graph, &myPort, &wireCount](const maxon::GraphConnection& conn) -> maxon::Result<maxon::Bool>
				{
					iferr_scope;
					const maxon::GraphNode& sourcePort = conn.first;
					if (!sourcePort.IsValid())
						return maxon::Bool(true);  // skip phantom-input wires (defensive)
					sourcePort.Connect(myPort) iferr_return;
					wireCount++;
					return maxon::Bool(true);
				}) iferr_return;
			return maxon::Bool(true);
		}, maxon::NODE_KIND::INPORT) iferr_return;

	txn.Commit() iferr_return;
	return wireCount;
}

// Preflight a single spec. Sets `outStatus` to one of: "preflight_ok",
// "missing_og", "already_swapped". Returns true if the spec passes preflight.
static Bool PreflightSpec(const maxon::HashSet<maxon::String>& topLevelIds,
                          const String& ogId,
                          const String& myName,
                          String& outStatus,
                          String& outMessage)
{
	const maxon::String ogIdMaxon = MaxonConvert(ogId);
	const maxon::String myNameMaxon = MaxonConvert(myName);

	if (!topLevelIds.Contains(ogIdMaxon))
	{
		outStatus = "missing_og"_s;
		outMessage = "OG node id not found among top-level graph children"_s;
		return false;
	}
	if (topLevelIds.Contains(myNameMaxon))
	{
		outStatus = "already_swapped"_s;
		outMessage = "MY node name already exists in graph (collision)"_s;
		return false;
	}
	outStatus = "preflight_ok"_s;
	outMessage = "OG present + MY name available; ready to mutate"_s;
	return true;
}

// Per-spec record carried from preflight pass into mutation pass.
struct SpecRecord
{
	String ogId;
	String myName;
	String assetId;
	String status;            // audit-row status field
	String message;           // audit-row message field
	Bool   readyToMutate = false;  // true when preflight passed
	Int32  wiresMirroredCount = 0; // populated by MirrorInputs
	Int32  wiresRewiredCount  = 0; // populated by atomic remove+rewire (v3)
};

static Int32 DoBulkSwapNodesPreflight(BaseContainer* wc)
{
	if (!wc)
		return 90;

	const String input = wc->GetString(BC_KEY_BULK_SWAP_INPUT);
	const Bool   mutate = wc->GetBool(BC_KEY_BULK_SWAP_MUTATE);
	wc->SetString(BC_KEY_BULK_SWAP_RESULT, ""_s);

	if (input.GetLength() == 0)
	{
		wc->SetString(BC_KEY_STATUS_MSG, "bulk_swap: empty input"_s);
		return 91;
	}

	BaseDocument* doc = GetActiveDocument();
	if (!doc)
	{
		wc->SetString(BC_KEY_STATUS_MSG, "bulk_swap: no active document"_s);
		return 92;
	}

	BaseObject* snHost = FindFirstObjectOfType(doc->GetFirstObject(), SN_DEFORMER_PLUGIN_ID);
	if (snHost == nullptr)
	{
		wc->SetString(BC_KEY_STATUS_MSG,
			"bulk_swap: no SN Deformer host (180420400) found in active document"_s);
		return 93;
	}

	// Build the top-level child id set once. All preflight checks read from it.
	maxon::HashSet<maxon::String> topLevelIds;
	iferr (CollectTopLevelChildIds(snHost, topLevelIds))
	{
		String msg;
		msg += "bulk_swap: failed to walk SN graph: ";
		msg += MaxonConvert(err.GetMessage());
		wc->SetString(BC_KEY_STATUS_MSG, msg);
		return 98;
	}

	// Pass 1 — parse + preflight. Collect every line into a SpecRecord so
	// pass 2 (mutation) can iterate the same set without re-parsing.
	maxon::BaseArray<SpecRecord> specs;
	maxon::HashSet<maxon::String> myNamesSeenInBatch;
	Int32 parsed = 0;
	Int32 malformed = 0;
	Int32 preflightOk = 0;
	Int32 preflightFail = 0;
	Int32 firstFailureCode = 0;  // 0 = no failure, otherwise one of 95/96/97
	maxon::Int pos = 0;
	const maxon::Int total = input.GetLength();

	while (pos < total)
	{
		const String remainder = input.GetPart(pos, total - pos);
		maxon::Int newline = NOTOK;
		String line;
		if (remainder.Find("\n"_s, &newline))
		{
			line = remainder.GetPart(0, newline);
			pos += newline + 1;
		}
		else
		{
			line = remainder;
			pos = total;
		}

		if (line.GetLength() == 0)
			continue;

		SpecRecord rec;
		if (!SplitBulkSwapLine(line, rec.ogId, rec.myName, rec.assetId))
		{
			malformed++;
			preflightFail++;
			if (firstFailureCode == 0)
				firstFailureCode = 97;  // malformed_spec
			rec.ogId = line;
			rec.status = "malformed_spec"_s;
			rec.message = "line failed to parse as og_id|my_name|asset_id"_s;
			rec.readyToMutate = false;
			iferr (specs.Append(rec))
			{
				wc->SetString(BC_KEY_STATUS_MSG, "bulk_swap: spec array Append failed"_s);
				return 98;
			}
			continue;
		}

		parsed++;

		// Per-spec preflight against the graph snapshot.
		String specStatus;
		String specMessage;
		Bool passed = PreflightSpec(topLevelIds, rec.ogId, rec.myName, specStatus, specMessage);

		// Additional check: same MY name appears twice in the batch — would
		// collide on the second AddChild. Catches it pre-mutation.
		if (passed)
		{
			const maxon::String myNameMaxon = MaxonConvert(rec.myName);
			if (myNamesSeenInBatch.Contains(myNameMaxon))
			{
				passed = false;
				specStatus = "duplicate_my_in_batch"_s;
				specMessage = "another spec in this batch already requested this MY name"_s;
			}
			else
			{
				iferr (myNamesSeenInBatch.Insert(myNameMaxon))
				{
					wc->SetString(BC_KEY_STATUS_MSG,
						"bulk_swap: HashSet Insert failed during MY name dedup"_s);
					return 98;
				}
			}
		}

		rec.status = specStatus;
		rec.message = specMessage;
		rec.readyToMutate = passed;

		if (passed)
		{
			preflightOk++;
		}
		else
		{
			preflightFail++;
			if (firstFailureCode == 0)
			{
				if (specStatus == "missing_og"_s)
					firstFailureCode = 95;
				else if (specStatus == "already_swapped"_s)
					firstFailureCode = 96;
				else if (specStatus == "duplicate_my_in_batch"_s)
					firstFailureCode = 96;  // bucket under already_swapped
				else
					firstFailureCode = 95;
			}
		}

		iferr (specs.Append(rec))
		{
			wc->SetString(BC_KEY_STATUS_MSG, "bulk_swap: spec array Append failed"_s);
			return 98;
		}
	}

	// Pass 2 — mutation, only if all preflight passed AND mutate flag is true.
	const Bool runMutation = mutate && preflightFail == 0 && preflightOk > 0;
	Int32 mutated = 0;
	Int32 mutationErr = 0;
	if (runMutation)
	{
		// Open the graph fresh for the mutation phase. Cheap; reuses Nimbus.
		maxon::nodes::NodesGraphModelRef graph;
		iferr (graph = OpenSNGraph(snHost))
		{
			String msg;
			msg += "bulk_swap mutation: failed to open SN graph: ";
			msg += MaxonConvert(err.GetMessage());
			wc->SetString(BC_KEY_STATUS_MSG, msg);
			return 98;
		}

		for (SpecRecord& rec : specs)
		{
			if (!rec.readyToMutate)
				continue;
			const maxon::String myNameMaxon  = MaxonConvert(rec.myName);
			const maxon::String assetIdMaxon = MaxonConvert(rec.assetId);
			const maxon::String ogIdMaxon    = MaxonConvert(rec.ogId);

			// Phase 1 — AddChild (separate transaction so MY exists before
			// mirror tx; matches the Python pattern's tx-1 semantics).
			maxon::GraphNode myNode;
			iferr (myNode = AddChildOnly(graph, myNameMaxon, assetIdMaxon))
			{
				rec.status = "addchild_err"_s;
				String errMsg = "AddChild failed: "_s;
				errMsg += MaxonConvert(err.GetMessage());
				rec.message = errMsg;
				rec.readyToMutate = false;
				mutationErr++;
				continue;
			}

			// Phase 2 — re-find OG (must be re-resolved post-commit; the
			// pre-mutation walk's GraphNode handles may be stale across tx)
			// then CopyConnectionsFrom(my, og, INPUT). MY's port topology
			// matches OG's because MY was created from the same asset_id.
			maxon::GraphNode ogNode;
			iferr (ogNode = FindTopLevelNodeById(graph, ogIdMaxon))
			{
				rec.status = "mirror_err"_s;
				String errMsg = "post-AddChild OG re-resolve failed: "_s;
				errMsg += MaxonConvert(err.GetMessage());
				rec.message = errMsg;
				mutationErr++;
				continue;
			}
			if (!ogNode.IsValid())
			{
				rec.status = "mirror_err"_s;
				rec.message = "post-AddChild OG no longer in graph (impossible — investigate)"_s;
				mutationErr++;
				continue;
			}

			Int32 wiresMirrored = 0;
			iferr (wiresMirrored = MirrorInputs(graph, myNode, ogNode))
			{
				rec.status = "mirror_err"_s;
				String errMsg = "MirrorInputs failed: "_s;
				errMsg += MaxonConvert(err.GetMessage());
				rec.message = errMsg;
				mutationErr++;
				continue;
			}
			rec.wiresMirroredCount = wiresMirrored;

			// Phase 3 — capture OG's output wires (read-only walk, BEFORE
			// the atomic transaction) so we know what to rewire.
			maxon::BaseArray<OutputWire> outWires;
			Int32 outSkipped = 0;
			iferr (CaptureOutputWires(ogNode, outWires, outSkipped))
			{
				rec.status = "capture_outputs_err"_s;
				String errMsg = "CaptureOutputWires failed: "_s;
				errMsg += MaxonConvert(err.GetMessage());
				rec.message = errMsg;
				mutationErr++;
				continue;
			}

			// Phase 4 — atomic remove+rewire (THE load-bearing pattern).
			Int32 wiresRewired = 0;
			iferr (wiresRewired = AtomicRemoveAndRewire(graph, ogNode, myNode, outWires))
			{
				rec.status = "atomic_err"_s;
				String errMsg = "AtomicRemoveAndRewire failed: "_s;
				errMsg += MaxonConvert(err.GetMessage());
				rec.message = errMsg;
				mutationErr++;
				continue;
			}
			rec.wiresRewiredCount = wiresRewired;

			// Full success.
			rec.status = "swapped"_s;
			String msg = "1-1 swap complete: mirrored="_s;
			msg += String::IntToString((Int64)wiresMirrored);
			msg += " rewired=";
			msg += String::IntToString((Int64)wiresRewired);
			if (outSkipped > 0)
			{
				msg += " (skipped ";
				msg += String::IntToString((Int64)outSkipped);
				msg += " unresolvable dst port(s) in capture)";
			}
			rec.message = msg;
			mutated++;
		}

		// Refresh ritual — same 4 calls Python's swap_one() runs after mutation.
		// Even though we only added a node (no rewire), the host needs to be
		// dirtied so the editor + cache reflect the new graph state.
		snHost->SetDirty(DIRTYFLAGS::DATA | DIRTYFLAGS::CACHE
		               | DIRTYFLAGS::DESCRIPTION | DIRTYFLAGS::MATRIX);
		BaseObject* parent = snHost->GetUp();
		if (parent)
			parent->SetDirty(DIRTYFLAGS::DATA | DIRTYFLAGS::CACHE | DIRTYFLAGS::MATRIX);
		EventAdd(EVENT::FORCEREDRAW);
		doc->ExecutePasses(nullptr, true, true, true, BUILDFLAGS::NONE);
	}

	// Build the audit string from the collected records.
	String audit;
	for (const SpecRecord& rec : specs)
	{
		audit += rec.ogId;
		audit += "|";
		audit += rec.status;
		audit += "|";
		audit += String::IntToString((Int64)rec.wiresMirroredCount);
		audit += "|";
		audit += String::IntToString((Int64)rec.wiresRewiredCount);
		audit += "|";
		audit += rec.message;
		audit += "\n";
	}
	wc->SetString(BC_KEY_BULK_SWAP_RESULT, audit);

	// Summary line.
	String summary;
	summary += "bulk_swap: parsed=";
	summary += String::IntToString((Int64)parsed);
	summary += " preflight_ok=";
	summary += String::IntToString((Int64)preflightOk);
	summary += " preflight_fail=";
	summary += String::IntToString((Int64)preflightFail);
	summary += " malformed=";
	summary += String::IntToString((Int64)malformed);
	summary += " host_top_level_nodes=";
	summary += String::IntToString((Int64)topLevelIds.GetCount());
	if (runMutation)
	{
		summary += " mutated=";
		summary += String::IntToString((Int64)mutated);
		summary += " mutation_err=";
		summary += String::IntToString((Int64)mutationErr);
		summary += " stage=swapped";
	}
	else
	{
		summary += " mutation=";
		summary += mutate ? "skipped(preflight_failed)" : "not_requested";
	}
	wc->SetString(BC_KEY_STATUS_MSG, summary);

	// Status code resolution:
	//   preflight failures → first-failure code (95/96/97), unchanged
	//   no mutation requested → 94 (preflight_ok)
	//   mutation requested + all OK → 0
	//   mutation requested + any AddChild failed → 99 (mutation_partial)
	if (preflightFail > 0)
		return firstFailureCode;
	if (!runMutation)
		return 94;
	if (mutationErr > 0)
		return 99;
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
		// Phase A.2 promiscuous logger — record every CoreMessage that
		// fires while active. Empirical probe to identify what messages
		// fire when user does right-click port → Add Input/Add Output
		// in the Node Editor. See gotcha #34/#35 for dispatch model.
		if (g_loggerActive && g_msgLogCount < G_MSG_LOG_CAP)
		{
			g_msgLogCount++;
			g_msgLog += "[";
			g_msgLog += String::IntToString((Int64)g_msgLogCount);
			g_msgLog += "] type_id=";
			g_msgLog += String::IntToString((Int64)id);
			g_msgLog += " core_id=";
			g_msgLog += String::IntToString((Int64)bc.GetInt32(BFM_CORE_ID));
			g_msgLog += " par1=";
			g_msgLog += String::IntToString((Int64)bc.GetInt32(BFM_CORE_PAR1));
			g_msgLog += " par2=";
			g_msgLog += String::IntToString((Int64)bc.GetInt32(BFM_CORE_PAR2));
			g_msgLog += "\n";
		}

		// Only handle requests addressed to us via SpecialEventAdd.
		// SpecialEventAdd(MCP_HELPER_PLUGIN_ID, op, 0) encodes the
		// plugin_id at BFM_CORE_ID and the op at BFM_CORE_PAR1.
		if (bc.GetInt32(BFM_CORE_ID) != MCP_HELPER_PLUGIN_ID)
			return true;
		(void)id;

		BaseContainer* wc = GetWorldContainerInstance();
		if (!wc)
			return true;

		// Read op-code preferably from BFM_CORE_PAR1 (the SpecialEventAdd
		// payload), falling back to the world container's BC_KEY_OP.
		// SpecialEventAdd's p1 is reliable; world container is just for
		// args/results that don't fit in two UInts.
		Int32 op = bc.GetInt32(BFM_CORE_PAR1);
		if (op == 0 && wc->GetInt32(BC_KEY_OP) != 0)
			op = wc->GetInt32(BC_KEY_OP);

		// Reset response slots before dispatch
		wc->SetInt32(BC_KEY_PROTOCOL_VER, MCP_HELPER_PROTOCOL_VERSION);
		wc->SetString(BC_KEY_NEW_PORT_ID, ""_s);
		wc->SetString(BC_KEY_STATUS_MSG, ""_s);
		wc->SetString(BC_KEY_BULK_SWAP_RESULT, ""_s);

		Int32 status = 0;
		switch (op)
		{
			case OP_PING:
				wc->SetString(BC_KEY_STATUS_MSG, "pong"_s);
				status = 0;
				break;
			case OP_ADD_FLOATING_IO_PORT:
				// Runtime AddPort on FloatingIO is fundamentally unsupported
				// by the C4D 2026 runtime (gotcha #36). Return a clean
				// unsupported message rather than running the failing impl.
				wc->SetString(BC_KEY_STATUS_MSG, "Runtime AddPort on FloatingIO is unsupported in C4D 2026 — see gotcha #36. Use NodeTemplate publishing path (Phase B)."_s);
				status = 100;
				break;

			case OP_LOGGER_START:
				g_loggerActive = true;
				wc->SetString(BC_KEY_STATUS_MSG, "logger started — perform the probe action now"_s);
				status = 0;
				break;

			case OP_LOGGER_STOP:
				g_loggerActive = false;
				wc->SetString(BC_KEY_STATUS_MSG, "logger stopped"_s);
				status = 0;
				break;

			case OP_LOGGER_READ:
			{
				// Dump the captured log into BC_KEY_LOG_DUMP. Caller can
				// also poll BC_KEY_DEBUG which carries a quick summary.
				wc->SetString(BC_KEY_LOG_DUMP, g_msgLog);
				String summary;
				summary += "logger active=";
				summary += g_loggerActive ? "true" : "false";
				summary += " entries=";
				summary += String::IntToString((Int64)g_msgLogCount);
				wc->SetString(BC_KEY_STATUS_MSG, summary);
				status = 0;
				break;
			}

			case OP_LOGGER_CLEAR:
				g_msgLog = String();
				g_msgLogCount = 0;
				wc->SetString(BC_KEY_STATUS_MSG, "logger cleared"_s);
				status = 0;
				break;

			case OP_OBSERVER_START:
			{
				// Lazy subscribe on first start (idempotent).
				iferr (EnsureCommandObserverSubscribed_Impl())
				{
					wc->SetString(BC_KEY_STATUS_MSG,
						String("observer subscribe failed: ") + MaxonConvert(err.GetMessage()));
					status = 1;
					break;
				}
				g_cmdObserverActive = true;
				wc->SetString(BC_KEY_STATUS_MSG, "command observer started"_s);
				status = 0;
				break;
			}

			case OP_OBSERVER_STOP:
				g_cmdObserverActive = false;
				wc->SetString(BC_KEY_STATUS_MSG, "command observer stopped"_s);
				status = 0;
				break;

			case OP_OBSERVER_READ:
			{
				wc->SetString(BC_KEY_CMD_OBSERVER_DUMP, g_cmdObserverLog);
				String summary;
				summary += "command observer subscribed=";
				summary += g_cmdObserverSubscribed ? "true" : "false";
				summary += " active=";
				summary += g_cmdObserverActive ? "true" : "false";
				summary += " entries=";
				summary += String::IntToString((Int64)g_cmdObserverCount);
				wc->SetString(BC_KEY_STATUS_MSG, summary);
				status = 0;
				break;
			}

			case OP_OBSERVER_CLEAR:
				g_cmdObserverLog = String();
				g_cmdObserverCount = 0;
				wc->SetString(BC_KEY_STATUS_MSG, "command observer cleared"_s);
				status = 0;
				break;

			case OP_LIST_COMMANDS:
			{
				// Read filter from BC_KEY_TARGET (reuse existing key) — empty = no filter.
				const String filterStr = wc->GetString(BC_KEY_TARGET);
				const maxon::String filterMaxon = MaxonConvert(filterStr);
				const maxon::Bool useFilter = (filterStr.GetLength() > 0);
				iferr (ListCommandClasses_Impl(useFilter, filterMaxon))
				{
					wc->SetString(BC_KEY_STATUS_MSG,
						String("list cmds failed: ") + MaxonConvert(err.GetMessage()));
					status = 1;
					break;
				}
				wc->SetString(BC_KEY_CMD_OBSERVER_DUMP, g_cmdObserverLog);
				String summary;
				summary += "matched=";
				summary += String::IntToString((Int64)g_cmdObserverCount);
				summary += " (filter=";
				summary += filterStr.GetLength() > 0 ? filterStr : String("(none)");
				summary += ")";
				wc->SetString(BC_KEY_STATUS_MSG, summary);
				status = 0;
				break;
			}

			case OP_BULK_SWAP_NODES:
				status = DoBulkSwapNodesPreflight(wc);
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
