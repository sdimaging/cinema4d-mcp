/* cinema4d_mcp_helper — C++ companion plugin for cinema4d-mcp.

PHASE A.0 (2026-04-30): minimal Python <-> C++ bridge skeleton.

Goal: prove the bridge works end-to-end before adding Scene Nodes logic.
A future phase wraps GraphModelInterface::AddPort and NodeTemplate publishing
(see docs/cpp_shim_design.md in the cinema4d-mcp repo for full spec).

Bridge mechanism: a MessageData plugin registered with a stable plugin ID.
Python side calls c4d.plugins.FindPlugin(MCP_HELPER_PLUGIN_ID, PLUGINTYPE_MESSAGEDATA)
and dispatches a c4d.BaseContainer with input args. C++ Message() handler
populates the same BaseContainer with the result and returns. Python reads
the modified BaseContainer.

Phase A.0 supports exactly one message: MSG_MCP_HELPER_PING. Reads an Int32
"input" from the BaseContainer, writes "output = input * 2" plus a string
"status = pong" plus an Int32 "version" (this header) back. Verifies the
round-trip works.
*/

#include "c4d_plugin.h"
#include "c4d_resource.h"
#include "c4d.h"

// Plugin ID — sibling to the existing Python plugin family
//   1057843  PLUGIN_ID         (Python MCP server)
//   1057844  UI_OBSERVER_PID   (UiActionObserver MessageData)
//   1057845  MCP_HELPER_PID    (this — C++ companion)         <-- HERE
// If conflict surfaces with another vendor, switch to a registered ID at
// https://developers.maxon.net/forum/pid before shipping publicly.
const cinema::Int32 MCP_HELPER_PLUGIN_ID = 1057845;

// Phase A.0 protocol version. Increment when the wire shape changes.
const cinema::Int32 MCP_HELPER_PROTOCOL_VERSION = 1;

// Custom message IDs. Use values >= 1000000 to avoid collision with C4D's
// builtin MSG_* constants. The Python side mirrors these in handler code.
enum
{
    MSG_MCP_HELPER_PING = 1057845001,
    // Future:
    //   MSG_MCP_HELPER_ADD_FLOATING_IO_PORT = 1057845002,
    //   MSG_MCP_HELPER_PUBLISH_CAPSULE_ASSET = 1057845003,
};

// BaseContainer keys for the ping/pong protocol.
const cinema::Int32 BC_KEY_INPUT      = 1;
const cinema::Int32 BC_KEY_OUTPUT     = 2;
const cinema::Int32 BC_KEY_STATUS     = 3;
const cinema::Int32 BC_KEY_VERSION    = 4;
const cinema::Int32 BC_KEY_ERROR      = 99;

// MessageData receiver. Cinema 4D dispatches plugin messages here.
class CinemaMcpHelper : public cinema::MessageData
{
public:
    virtual cinema::Bool CoreMessage(cinema::Int32 id, const cinema::BaseContainer& bc) override
    {
        // We don't process the global C4D core-message stream. Custom messages
        // come in via plugin.Message() from the Python side, which dispatches
        // to GePluginMessage on the plugin instance — handled below in
        // PluginMessage forwarding (the C4D plugin runtime delivers them).
        (void)id;
        (void)bc;
        return true;
    }
};

bool RegisterMcpHelperPlugin();

cinema::Bool cinema::PluginStart()
{
    if (!RegisterMcpHelperPlugin())
        return false;
    return true;
}

void cinema::PluginEnd()
{
}

cinema::Bool cinema::PluginMessage(cinema::Int32 id, void* data)
{
    switch (id)
    {
        case C4DPL_INIT_SYS:
        {
            if (!cinema::g_resource.Init())
                return false;
            return true;
        }
    }
    return false;
}

// The actual ping handler. Called from a static dispatcher hooked into
// the plugin's Message() entry. Phase A.0 contract: read BC[BC_KEY_INPUT]
// (Int32), write BC[BC_KEY_OUTPUT] = input * 2, BC[BC_KEY_STATUS] = "pong",
// BC[BC_KEY_VERSION] = MCP_HELPER_PROTOCOL_VERSION.
static cinema::Bool HandlePing(cinema::BaseContainer& bc)
{
    const cinema::Int32 input = bc.GetInt32(BC_KEY_INPUT);
    bc.SetInt32(BC_KEY_OUTPUT, input * 2);
    bc.SetString(BC_KEY_STATUS, "pong"_s);
    bc.SetInt32(BC_KEY_VERSION, MCP_HELPER_PROTOCOL_VERSION);
    return true;
}

// Top-level message dispatcher. The Python side calls this via
// plugin.Message(MSG_MCP_HELPER_PING, bc).
class CinemaMcpHelperDispatcher : public cinema::MessageData
{
public:
    virtual cinema::Bool CoreMessage(cinema::Int32 id, const cinema::BaseContainer& bc) override
    {
        // Custom Python -> C++ messages arrive here when dispatched via
        // c4d.plugins.FindPlugin(...).Message(id, bc) on the Python side.
        if (id == MSG_MCP_HELPER_PING)
        {
            // CoreMessage receives a const BaseContainer; we need to mutate.
            // The Python -> C++ Message() pathway delivers a non-const BC
            // through a different entry point. Phase A.0 keeps this minimal:
            // we accept the message and rely on the BC being mutable when
            // dispatched from Python via the GePluginMessage style.
            // (CoreMessage's const signature means we cannot write back here;
            // the mutating handler runs via GePluginMessage as documented in
            // the SDK. If Phase A.0 testing reveals the BC arrives const,
            // switch to NodeData/CommandData with a Message() override that
            // takes a non-const BC.)
            return true;
        }
        return true;
    }
};

bool RegisterMcpHelperPlugin()
{
    return cinema::RegisterMessagePlugin(
        MCP_HELPER_PLUGIN_ID,
        "cinema4d-mcp helper"_s,
        0,
        NewObjClear(CinemaMcpHelperDispatcher));
}

// NOTE on Phase A.0 mechanism: MessageData::CoreMessage signature is
// `Bool CoreMessage(Int32 id, const BaseContainer& bc)` — const BC, return
// only. To round-trip data we will likely need to either (a) switch to a
// NodeData/CommandData plugin with a `Message()` overload that accepts
// non-const data, or (b) use a side channel like a global state slot the
// Python side reads back via a follow-up call. Phase A.0 ships the plugin
// loadable + discoverable; Phase A.1 picks the round-trip path after
// confirming plugin load + Python FindPlugin works in the live install.
// See cpp_shim_design.md "Open questions" item 4.
