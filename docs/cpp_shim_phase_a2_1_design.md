# Phase A.2.1 — `CommandObserverInterface` subscription design

**Status:** designed, not yet implemented
**Trigger:** Phase A.2 promiscuous CoreMessage logger returns empty / non-informative results from the manual right-click "Add Input" gesture.
**Goal:** the definitive empirical probe — does the right-click context-menu action go through `maxon::CommandObserverInterface::ObservableCommandInvokedInfo`? If yes, capture the `Id` of the invoked command (and its `CommandDataRef`) so we know what to call programmatically. If no, the action is a Node Editor internal-only operation and Phase B (NodeTemplate publishing) is the only forward path.

---

## Why A.2 might fail

Phase A.2 logs every `MessageData::CoreMessage(Int32 id, const BaseContainer& bc)` that fires. This catches:
- ✅ Legacy `BFM_CORE_*` events (e.g. `EVMSG_CHANGE`, `SpecialEventAdd` calls)
- ✅ UI-driven scene/material change broadcasts
- ❌ **NOT** Maxon command framework invocations (which fire on `CommandObserverInterface`, a different observable system)

The right-click "Add Input" command is likely **maxon command framework**, identified by string `Id` rather than `Int32`. None of these reach `MessageData::CoreMessage`.

---

## SDK reference

[`frameworks/command.framework/source/maxon/commandobservable.h`](sdk_excerpts/commandobservable.h)

```cpp
class CommandObserverInterface : MAXON_INTERFACE_BASES(ObserverObjectInterface)
{
    MAXON_INTERFACE(CommandObserverInterface, MAXON_REFERENCE_NORMAL,
                    "net.maxon.command.interface.observer", ...);

public:
    // Per-command status update (status bar)
    MAXON_OBSERVABLE(Result<void>, ObservableCommandStatus,
        (const Id& commandId, const DataDictionary& statusMessage), ...);

    // Fires on every command invocation
    MAXON_OBSERVABLE(Result<void>, ObservableCommandInvoked,
        (const Id& commandId, const Result<COMMANDRESULT>& result), ...);

    // Fires between GetState and Execute
    MAXON_OBSERVABLE(Result<void>, ObservableCommandPrepareInfo,
        (const Id& commandId, const CommandDataRef& data), ...);

    // ★ THE ONE WE WANT — fires at each stage with full context
    MAXON_OBSERVABLE(Result<void>, ObservableCommandInvokedInfo,
        (const Id& commandId, const Result<COMMANDRESULT>& result,
         const CommandDataRef& data, const InvocationState& interactionState), ...);
};

MAXON_DECLARATION(Class<CommandObserverRef>, CommandObserverObjectClass,
    "net.maxon.command.class.observer", ...);
```

Subscription pattern (per `plugins/example.nodes/source/space/nodesystem_observer.cpp`):
```cpp
auto ticket = someObserverRef.ObservableXxx(true).AddObserver(
    [&](Args...) -> Result<...>
    {
        // callback body
        return OK;
    }) iferr_return;
```

Where `someObserverRef` is the **specific instance** that the system fires through. Open question: how to obtain that instance for the global command observer.

---

## Design

### New ops + BC keys

```cpp
// New ops
const Int32 OP_OBSERVER_START = 20;
const Int32 OP_OBSERVER_STOP  = 21;
const Int32 OP_OBSERVER_READ  = 22;
const Int32 OP_OBSERVER_CLEAR = 23;

// New BC key for the command-observer dump (separate from the A.2 logger
// dump so both can coexist if user wants to compare)
const Int32 BC_KEY_CMD_OBSERVER_DUMP = 1057845050;
```

### Subscription state

```cpp
static maxon::CommandObserverRef g_cmdObserver;     // the subscribed observer
static maxon::FunctionBaseRef    g_cmdObserverTicket; // unsubscribe handle
static String                     g_cmdObserverLog;
static Int32                      g_cmdObserverCount = 0;
static const Int32                G_CMD_OBSERVER_CAP = 1000;
```

### Subscribe / unsubscribe

```cpp
case OP_OBSERVER_START:
{
    iferr_scope_handler { /* fall back to error message */ };

    // Step 1: get the singleton CommandObserverRef. Two candidates to try:
    //   A. CommandObserverObjectClass.Create() — creates a NEW observer
    //      instance. If the global command system fires through a *specific*
    //      instance, this won't receive events. Try first; if no events
    //      arrive during a known command (e.g. invoke the "Frame All" cmd
    //      via CallCommand), fall back to (B).
    //   B. There's likely a global registry-style accessor. Need to find it
    //      via SDK reflection — search for any function that returns a
    //      CommandObserverRef.
    g_cmdObserver = CommandObserverObjectClass.Create() iferr_return;

    // Step 2: subscribe to ObservableCommandInvokedInfo.
    g_cmdObserverTicket = g_cmdObserver.ObservableCommandInvokedInfo(true).AddObserver(
        [](const maxon::Id& cid, const maxon::Result<maxon::COMMANDRESULT>& res,
           const maxon::CommandDataRef& data, const maxon::InvocationState& state) -> maxon::Result<void>
        {
            iferr_scope;
            if (g_cmdObserverCount < G_CMD_OBSERVER_CAP)
            {
                g_cmdObserverCount++;
                g_cmdObserverLog += "[";
                g_cmdObserverLog += String::IntToString((Int64)g_cmdObserverCount);
                g_cmdObserverLog += "] cmdId=";
                g_cmdObserverLog += MaxonConvert(cid.ToString());
                g_cmdObserverLog += " interactive=";
                g_cmdObserverLog += state._interactive ? "true" : "false";
                g_cmdObserverLog += " interactionType=";
                g_cmdObserverLog += String::IntToString((Int64)(Int)state._interaction);
                // Optional: dump CommandDataRef contents if accessible
                // const DataDictionary& dict = data.GetDictionary() iferr_ignore("");
                // ...
                g_cmdObserverLog += "\n";
            }
            return OK;
        }) iferr_return;

    wc->SetString(BC_KEY_STATUS_MSG, "command observer started"_s);
    status = 0;
    break;
}

case OP_OBSERVER_STOP:
    if (g_cmdObserverTicket)
    {
        // Disconnect the subscription. The ticket disposes itself.
        g_cmdObserverTicket = nullptr;
    }
    g_cmdObserver = nullptr;
    wc->SetString(BC_KEY_STATUS_MSG, "command observer stopped"_s);
    status = 0;
    break;

case OP_OBSERVER_READ:
    wc->SetString(BC_KEY_CMD_OBSERVER_DUMP, g_cmdObserverLog);
    {
        String summary;
        summary += "command observer entries=";
        summary += String::IntToString((Int64)g_cmdObserverCount);
        wc->SetString(BC_KEY_STATUS_MSG, summary);
    }
    status = 0;
    break;

case OP_OBSERVER_CLEAR:
    g_cmdObserverLog = String();
    g_cmdObserverCount = 0;
    wc->SetString(BC_KEY_STATUS_MSG, "command observer cleared"_s);
    status = 0;
    break;
```

### Required new framework dependency

Update `cpp_shim/cinema4d_mcp_helper/project/projectdefinition.txt`:
```
APIS=\
cinema.framework;\
cinema_hybrid.framework;\
core.framework;\
math.framework;\
misc.framework;\
mesh_misc.framework;\
graph.framework;\
nodes.framework;\
nodespace.framework;\
neutron.framework;\
command.framework
```

Add include in `main.cpp`:
```cpp
#include "maxon/commandobservable.h"
```

---

## Verification plan

### Step 1 — sanity check the subscription itself

Before testing the right-click gesture, verify the observer is wired correctly by triggering a KNOWN maxon command:
1. `OP_OBSERVER_START`
2. From Python: invoke a known command via `c4d.CallCommand(...)` — try one that's likely registered with the maxon framework (e.g. asset operations: `200001022` Save New Version, `465002339` Convert To Asset)
3. `OP_OBSERVER_READ` — confirm at least ONE entry was logged

If no entries appear even for known commands, the subscription approach (A) didn't work — the system fires through a different instance. Need to find the singleton accessor (option B).

### Step 2 — actual probe

Once subscription is verified working:
1. `OP_OBSERVER_CLEAR`
2. `OP_OBSERVER_START`
3. **(USER)** manually right-click a port in Node Editor → "Add Input"
4. `OP_OBSERVER_STOP`
5. `OP_OBSERVER_READ`
6. Inspect log for entries with `cmdId` containing strings like `addinput`, `add_input`, `floatingio`, `port`, etc.

### Outcomes

| Result | Interpretation | Next action |
|---|---|---|
| Sanity step 1 captures known commands | Observer is wired correctly | Run Step 2 |
| Step 1 captures nothing | Need singleton accessor (option B) | Search SDK for `CommandObserverRef` factories; try `CommandObserverObjectClass()` (singleton accessor pattern), `MAXON_DECLARATION` instance access, etc. |
| Step 2 captures `cmdId` for "Add Input" | Programmatic path exists | Try `CommandManager::Invoke(cmdId, ...)` from C++ or find Python equivalent |
| Step 2 captures nothing for the gesture | Action is Node Editor internal-only, not in command framework | Phase B (NodeTemplate publishing) is the ONLY remaining path |

---

## Estimated effort

- **C++ source**: ~50-70 lines added to the existing helper (~1 hour)
- **Build cycle**: 1 minute (we have automation)
- **Sanity step 1 + step 2 verification**: ~10 minutes including user-driven gesture
- **Risk**: medium — the singleton-instance question may need iteration. If option A doesn't fire on known commands, we'll need 1-2 more iterations to find the right access pattern.

Total ~1.5-2 hours including iteration buffer.

---

## What this unlocks

If the gesture fires `ObservableCommandInvokedInfo`:
- We get the exact `cmdId` (a `maxon::Id`, not an integer)
- We get the `CommandDataRef` (the data dictionary the editor passes — likely contains target node/port references)
- We can attempt programmatic invocation via the maxon command framework's invoke API

If the gesture does NOT fire `ObservableCommandInvokedInfo`:
- Definitively confirms the action is Node Editor internal-only
- Closes the last "is there a non-NodeTemplate path" question
- Phase B (NodeTemplate publishing in C++) becomes the only forward path with full confidence

Either way, the result is decisive.
