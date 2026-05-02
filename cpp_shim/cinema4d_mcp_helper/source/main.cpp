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

const Int32 MCP_HELPER_PROTOCOL_VERSION = 4; // 4 adds bulk-swap scaffold op.

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
const Int32 BC_KEY_BULK_SWAP_INPUT  = 1057845060; // String — line-delimited specs
const Int32 BC_KEY_BULK_SWAP_RESULT = 1057845061; // String — line-delimited audit

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

static Int32 DoBulkSwapNodesScaffold(BaseContainer* wc)
{
	if (!wc)
		return 90;

	const String input = wc->GetString(BC_KEY_BULK_SWAP_INPUT);
	wc->SetString(BC_KEY_BULK_SWAP_RESULT, ""_s);

	if (input.GetLength() == 0)
	{
		wc->SetString(BC_KEY_STATUS_MSG, "bulk_swap scaffold: empty input"_s);
		return 91;
	}

	BaseDocument* doc = GetActiveDocument();
	if (!doc)
	{
		wc->SetString(BC_KEY_STATUS_MSG, "bulk_swap scaffold: no active document"_s);
		return 92;
	}

	BaseObject* snHost = FindFirstObjectOfType(doc->GetFirstObject(), SN_DEFORMER_PLUGIN_ID);
	const Bool hostFound = snHost != nullptr;

	String audit;
	Int32 parsed = 0;
	Int32 malformed = 0;
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

		String ogId;
		String myName;
		String assetId;
		if (!SplitBulkSwapLine(line, ogId, myName, assetId))
		{
			malformed++;
			audit += line;
			audit += "|preflight_fail|0|0|malformed spec; expected og_id|my_name|asset_id\n";
			continue;
		}

		parsed++;
		audit += ogId;
		audit += "|scaffold_only|0|0|";
		audit += hostFound
			? "C++ bridge contract reached; mutation intentionally disabled until preflight+atomic swap body lands"
			: "C++ bridge contract reached, but no SN Deformer host (180420400) found in active document";
		audit += "\n";
	}

	wc->SetString(BC_KEY_BULK_SWAP_RESULT, audit);

	String summary;
	summary += "bulk_swap scaffold: parsed=";
	summary += String::IntToString((Int64)parsed);
	summary += " malformed=";
	summary += String::IntToString((Int64)malformed);
	summary += " sn_deformer_found=";
	summary += hostFound ? "true" : "false";
	summary += " mutation=disabled";
	wc->SetString(BC_KEY_STATUS_MSG, summary);

	// Non-zero by design: callers must not treat this scaffold as a completed
	// production mutation. It is a compile/bridge/audit foundation only.
	return 93;
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
				status = DoBulkSwapNodesScaffold(wc);
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
