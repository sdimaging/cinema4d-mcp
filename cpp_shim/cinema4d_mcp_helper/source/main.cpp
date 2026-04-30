/* cinema4d_mcp_helper — C++ companion plugin for cinema4d-mcp.

PHASE A.0 (2026-04-30): minimal Python <-> C++ bridge skeleton.

Goal: prove the bridge works end-to-end before adding Scene Nodes logic.
A future phase wraps GraphModelInterface::AddPort and NodeTemplate publishing
(see docs/cpp_shim_design.md in the cinema4d-mcp repo for full spec).

Bridge mechanism: a MessageData plugin registered with a stable plugin ID.
Python side calls c4d.plugins.FindPlugin(MCP_HELPER_PLUGIN_ID, PLUGINTYPE_COREMESSAGE)
to verify the plugin loaded. Round-trip message dispatch (Python -> C++ ->
Python) lands in Phase A.1 once load is confirmed.

Style follows Spikr2 (the user's most-mature C4D 2026 plugin): namespace
cinema { ... } block, separate main.h with `using namespace cinema;`,
tabs for indentation, stylecheck level 0.
*/

#include "main.h"

namespace cinema
{

// Plugin ID — sibling to the existing Python plugin family
//   1057843  PLUGIN_ID         (Python MCP server SpecialEventAdd)
//   1057844  UI_OBSERVER_PID   (UiActionObserver MessageData)
//   1057845  MCP_HELPER_PID    (this — C++ companion)         <-- HERE
// If conflict surfaces with another vendor, register a fresh ID at
// https://developers.maxon.net/forum/pid before public ship.
const Int32 MCP_HELPER_PLUGIN_ID = 1057845;

// Phase A.0 protocol version. Increment when the wire shape changes.
const Int32 MCP_HELPER_PROTOCOL_VERSION = 1;

// MessageData receiver. C4D dispatches plugin messages here. Phase A.0
// is just registers-the-listener; CoreMessage is a no-op until Phase A.1
// adds Scene Nodes primitives. The mere existence of a registered plugin
// at MCP_HELPER_PLUGIN_ID is what FindPlugin verifies on the Python side.
class CinemaMcpHelper : public MessageData
{
public:
	virtual Bool CoreMessage(Int32 id, const BaseContainer& bc) override
	{
		// Phase A.0: no-op. Custom Python -> C++ messages with a return
		// payload arrive in Phase A.1 — likely via a NodeData/CommandData
		// plugin type with a non-const Message() override (CoreMessage's
		// BC is const, so we can't mutate it for a reply).
		(void)id;
		(void)bc;
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
