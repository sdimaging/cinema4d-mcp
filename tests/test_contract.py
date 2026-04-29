"""Contract tests for cinema4d-mcp.

Walks the MCP server (server.py) and the C4D plugin (.pyp) at the AST level
and asserts that every advertised tool maps to a real handler — and vice
versa. Catches the class of bugs where a tool is committed but the handler
isn't wired into the dispatcher (or the reverse).

Runs entirely without Cinema 4D. Just `pytest tests/test_contract.py` or
`python tests/test_contract.py` to check.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Set


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = REPO_ROOT / "src" / "cinema4d_mcp" / "server.py"
PLUGIN_PYP = REPO_ROOT / "c4d_plugin" / "mcp_server_plugin.pyp"


# ---------- server.py ----------

def _extract_mcp_tool_command_types(server_py: Path) -> Set[str]:
    """Find every @mcp.tool() function and the `command` field it sends.

    Pattern: each @mcp.tool() async function builds a `command` dict that
    includes a `"command": "..."` key, then calls send_to_c4d. We grep for
    every literal string assigned to that key inside the function body.
    """
    src = server_py.read_text()
    tree = ast.parse(src)

    command_types: Set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        # Has @mcp.tool() decorator?
        is_tool = False
        for dec in node.decorator_list:
            # Either `@mcp.tool` or `@mcp.tool(...)`
            target = dec.func if isinstance(dec, ast.Call) else dec
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "tool"
                and isinstance(target.value, ast.Name)
                and target.value.id == "mcp"
            ):
                is_tool = True
                break
        if not is_tool:
            continue

        # Walk the function body for `"command": "<literal>"`
        for sub in ast.walk(node):
            if isinstance(sub, ast.Dict):
                for k, v in zip(sub.keys, sub.values):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "command"
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)
                    ):
                        command_types.add(v.value)
            elif isinstance(sub, ast.Assign):
                # Catch `command["command"] = "..."`
                for tgt in sub.targets:
                    if (
                        isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.slice, ast.Constant)
                        and tgt.slice.value == "command"
                        and isinstance(sub.value, ast.Constant)
                        and isinstance(sub.value.value, str)
                    ):
                        command_types.add(sub.value.value)

    return command_types


# ---------- mcp_server_plugin.pyp ----------

# Match: elif command_type == "name":
DISPATCH_BRANCH_RE = re.compile(
    r'^\s*(?:if|elif)\s+command_type\s*==\s*"([^"]+)"\s*:',
    re.MULTILINE,
)

# Match: def handle_<name>(self, ...):
HANDLER_DEF_RE = re.compile(r'^\s*def\s+handle_(\w+)\s*\(', re.MULTILINE)

# Match: self.handle_<name>(
HANDLER_CALL_RE = re.compile(r'self\.handle_(\w+)\s*\(')


def _extract_dispatcher_branches(pyp: Path) -> Set[str]:
    """Every command_type literal that has an `if/elif command_type == "..."` branch."""
    src = pyp.read_text()
    return set(DISPATCH_BRANCH_RE.findall(src))


def _extract_handler_methods(pyp: Path) -> Set[str]:
    """Every `handle_<name>` defined as a method on C4DSocketServer."""
    src = pyp.read_text()
    return set(HANDLER_DEF_RE.findall(src))


def _extract_handler_calls(pyp: Path) -> Set[str]:
    """Every `handle_<name>` invoked as `self.handle_<name>(...)`."""
    src = pyp.read_text()
    return set(HANDLER_CALL_RE.findall(src))


def _extract_supported_commands_tuple(pyp: Path) -> Set[str]:
    """Read the `_SUPPORTED_COMMANDS` tuple advertised by get_capabilities.

    Returns the literal command names listed there; helps catch "shipped a
    handler but forgot to advertise it" mistakes.
    """
    src = pyp.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_SUPPORTED_COMMANDS":
                    if isinstance(node.value, ast.Tuple):
                        return {
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
    return set()


# ---------- assertions ----------

# A handful of internal/legacy command types that ARE branched in the dispatcher
# but not exposed as MCP tool wrappers (yet). Listing them here is intentional
# — adding a wrapper for one means deleting it from this set.
DISPATCHER_ONLY_OK = {
    "execute_python",        # exposed as `execute_python_script` tool, server builds the command
}

# Handlers that exist as helpers (called from other handlers) but aren't routed
# by the dispatcher — that's fine, they're internal.
HANDLER_NOT_DISPATCHED_OK = {
    "render_preview_base64",  # called via render_preview command_type
    "viewport_screenshot",    # called BOTH directly via dispatcher AND by multiview internally
}


def test_every_mcp_tool_has_a_dispatcher_branch():
    """Every MCP tool's command_type must be routed by the .pyp dispatcher."""
    tools = _extract_mcp_tool_command_types(SERVER_PY)
    branches = _extract_dispatcher_branches(PLUGIN_PYP)
    missing = tools - branches
    assert not missing, (
        f"MCP tools without a dispatcher branch in .pyp: {sorted(missing)}\n"
        f"Add `elif command_type == \"<name>\": response = self.handle_<name>(command)` "
        f"in the run() loop."
    )


# Branch → aliased handler name mapping for cases where the handler isn't
# named handle_<branch>. Add new entries here when introducing a deliberate
# rename rather than a 1:1 branch→handler mapping.
BRANCH_HANDLER_ALIAS = {
    "render_preview": "render_preview_base64",
}


def test_every_dispatcher_branch_has_a_handler():
    """Every `elif command_type == X` must call a defined `handle_X` method."""
    branches = _extract_dispatcher_branches(PLUGIN_PYP)
    calls = _extract_handler_calls(PLUGIN_PYP)
    defs = _extract_handler_methods(PLUGIN_PYP)
    missing = []
    for b in branches:
        # Accept handle_<branch>, OR an explicit alias entry, OR any handler
        # that's actually invoked by this dispatch (covered by `calls`).
        target = BRANCH_HANDLER_ALIAS.get(b, b)
        if target not in defs and target not in calls and b not in calls:
            missing.append(b)
    assert not missing, (
        f"Dispatcher branches with no matching handle_<name> defined OR called: {sorted(missing)}"
    )


def test_supported_commands_advertises_every_dispatcher_branch():
    """`_SUPPORTED_COMMANDS` must list every branch the dispatcher actually routes."""
    branches = _extract_dispatcher_branches(PLUGIN_PYP)
    advertised = _extract_supported_commands_tuple(PLUGIN_PYP)
    if not advertised:
        # Tuple not found / empty — fail loud
        raise AssertionError("Could not extract _SUPPORTED_COMMANDS tuple from .pyp")
    unadvertised = branches - advertised
    assert not unadvertised, (
        f"Dispatcher routes commands not in _SUPPORTED_COMMANDS: {sorted(unadvertised)}\n"
        f"Add them to _SUPPORTED_COMMANDS so get_capabilities reflects reality."
    )


def test_advertised_commands_actually_have_a_branch():
    """Every command in `_SUPPORTED_COMMANDS` must have a real dispatcher branch."""
    branches = _extract_dispatcher_branches(PLUGIN_PYP)
    advertised = _extract_supported_commands_tuple(PLUGIN_PYP)
    phantom = advertised - branches
    assert not phantom, (
        f"_SUPPORTED_COMMANDS lists commands with no dispatcher branch: {sorted(phantom)}"
    )


def test_every_mcp_tool_is_advertised_as_supported():
    """Every command_type the MCP server builds should be in _SUPPORTED_COMMANDS."""
    tools = _extract_mcp_tool_command_types(SERVER_PY)
    advertised = _extract_supported_commands_tuple(PLUGIN_PYP)
    missing = tools - advertised - DISPATCHER_ONLY_OK
    assert not missing, (
        f"MCP tools not advertised in _SUPPORTED_COMMANDS: {sorted(missing)}"
    )


# ---------- run as a script for visibility ----------

if __name__ == "__main__":
    tools = _extract_mcp_tool_command_types(SERVER_PY)
    branches = _extract_dispatcher_branches(PLUGIN_PYP)
    handler_defs = _extract_handler_methods(PLUGIN_PYP)
    handler_calls = _extract_handler_calls(PLUGIN_PYP)
    advertised = _extract_supported_commands_tuple(PLUGIN_PYP)

    print(f"  MCP tool command_types:      {len(tools):4}  (server.py)")
    print(f"  Dispatcher branches:         {len(branches):4}  (.pyp run() loop)")
    print(f"  handle_* methods defined:    {len(handler_defs):4}  (.pyp class)")
    print(f"  handle_* methods called:     {len(handler_calls):4}  (.pyp self.handle_*)")
    print(f"  _SUPPORTED_COMMANDS listed:  {len(advertised):4}  (.pyp class attr)")

    # Run every test
    failures = []
    for fn in [
        test_every_mcp_tool_has_a_dispatcher_branch,
        test_every_dispatcher_branch_has_a_handler,
        test_supported_commands_advertises_every_dispatcher_branch,
        test_advertised_commands_actually_have_a_branch,
        test_every_mcp_tool_is_advertised_as_supported,
    ]:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            failures.append((fn.__name__, str(e)))
            print(f"  ✗ {fn.__name__}")
            print(f"      {e}")

    if failures:
        print(f"\n{len(failures)} contract test(s) failed.")
        raise SystemExit(1)
    print("\nAll contract tests passed.")
