"""Cinema 4D MCP Server."""

import socket
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP, Context

from .config import C4D_HOST, C4D_PORT
from .utils import logger, check_c4d_connection


@dataclass
class C4DConnection:
    sock: Optional[socket.socket] = None
    connected: bool = False


# Asynchronous context manager for Cinema 4D connection
@asynccontextmanager
async def c4d_connection_context():
    """Asynchronous context manager for Cinema 4D connection."""
    connection = C4DConnection()
    try:
        # Initialize connection to Cinema 4D
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((C4D_HOST, C4D_PORT))
        connection.sock = sock
        connection.connected = True
        logger.info(f"✅ Connected to Cinema 4D at {C4D_HOST}:{C4D_PORT}")
        yield connection  # Yield the connection
    except Exception as e:
        logger.error(f"❌ Failed to connect to Cinema 4D: {str(e)}")
        connection.connected = False  # Ensure connection is marked as not connected
        yield connection  # Still yield the connection object
    finally:
        # Clean up on server shutdown
        if connection.sock:
            connection.sock.close()
            logger.info("🔌 Disconnected from Cinema 4D")


def send_to_c4d(connection: C4DConnection, command: Dict[str, Any]) -> Dict[str, Any]:
    """Send a command to Cinema 4D and get the response with improved timeout handling."""
    if not connection.connected or not connection.sock:
        return {"error": "Not connected to Cinema 4D"}

    # Set appropriate timeout based on command type
    command_type = command.get("command", "")

    # Long-running operations need longer timeouts
    if command_type in [
        "render_frame",
        "render_preview",
        "snapshot_scene",
        "apply_mograph_fields",
        "execute_python",
    ]:
        timeout = 120  # 2 minutes for render and heavy script operations
        logger.info(f"Using extended timeout ({timeout}s) for {command_type}")
    else:
        timeout = 20  # Default timeout for regular operations

    try:
        # Convert command to JSON and send it
        command_json = json.dumps(command) + "\n"  # Add newline as message delimiter
        logger.debug(f"Sending command: {command_type}")
        connection.sock.sendall(command_json.encode("utf-8"))

        # Set socket timeout
        connection.sock.settimeout(timeout)

        # Receive response
        response_data = b""
        start_time = time.time()
        max_time = start_time + timeout

        # Log for long-running operations
        if command_type in [
            "render_frame",
            "render_preview",
            "snapshot_scene",
            "apply_mograph_fields",
            "execute_python",
        ]:
            logger.info(
                f"Waiting for response from {command_type} (timeout: {timeout}s)"
            )

        while time.time() < max_time:
            try:
                chunk = connection.sock.recv(4096)
                if not chunk:
                    # If we receive an empty chunk, the connection might be closed
                    if not response_data:
                        logger.error(
                            f"Connection closed by Cinema 4D during {command_type}"
                        )
                        return {
                            "error": f"Connection closed by Cinema 4D during {command_type}"
                        }
                    break

                response_data += chunk

                # For long operations, log progress on data receipt
                elapsed = time.time() - start_time
                if (
                    command_type
                    in [
                        "render_frame",
                        "render_preview",
                        "snapshot_scene",
                        "apply_mograph_fields",
                        "execute_python",
                    ]
                    and elapsed > 5
                ):
                    logger.debug(
                        f"Received partial data for {command_type} ({len(response_data)} bytes, {elapsed:.1f}s elapsed)"
                    )

                if b"\n" in chunk:  # Message complete when we see a newline
                    logger.debug(f"Received complete response for {command_type}")
                    break

            except socket.timeout:
                logger.error(f"Socket timeout while receiving data for {command_type}")
                return {
                    "error": f"Timeout waiting for response from Cinema 4D ({timeout}s) for {command_type}"
                }

        # Parse and return response
        if not response_data:
            logger.error(f"No response received from Cinema 4D for {command_type}")
            return {"error": f"No response received from Cinema 4D for {command_type}"}

        response_text = response_data.decode("utf-8").strip()

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            # If JSON parsing fails, log the exact response for debugging
            logger.error(f"Failed to parse JSON response: {str(e)}")
            logger.error(f"Raw response (first 200 chars): {response_text[:200]}...")
            return {"error": f"Invalid response from Cinema 4D: {str(e)}"}

    except socket.timeout:
        logger.error(f"Socket timeout during {command_type} ({timeout}s)")
        return {
            "error": f"Timeout communicating with Cinema 4D ({timeout}s) for {command_type}"
        }
    except Exception as e:
        logger.error(f"Communication error during {command_type}: {str(e)}")
        return {"error": f"Communication error: {str(e)}"}


def _fmt_vec(v):
    """Format a vector/list as a compact string."""
    if isinstance(v, (list, tuple)):
        return f"({', '.join(f'{x:.1f}' if isinstance(x, float) else str(x) for x in v)})"
    return str(v)


def _fmt_props(d, indent="  "):
    """Format a dict as bullet list lines."""
    lines = []
    for k, v in d.items():
        label = k.replace("_", " ").title()
        if isinstance(v, (list, tuple)) and len(v) <= 4 and all(isinstance(x, (int, float)) for x in v):
            lines.append(f"{indent}- **{label}**: {_fmt_vec(v)}")
        elif isinstance(v, dict):
            lines.append(f"{indent}- **{label}**:")
            lines.extend(_fmt_props(v, indent + "  "))
        else:
            lines.append(f"{indent}- **{label}**: {v}")
    return lines


def format_c4d_response(response: Dict[str, Any], command_type: str) -> str:
    """Format a Cinema 4D response dict as readable markdown."""
    if "error" in response:
        return f"❌ Error: {response['error']}"

    status = response.get("status", "ok")

    if command_type == "add_primitive":
        obj = response.get("object", {})
        name = obj.get("name", "Object")
        lines = [f"✅ Created **{name}**"]
        if "type" in obj:
            lines.append(f"  - **Type**: {obj['type']}")
        if "position" in obj:
            lines.append(f"  - **Position**: {_fmt_vec(obj['position'])}")
        if "size" in obj:
            lines.append(f"  - **Size**: {_fmt_vec(obj['size'])}")
        if "guid" in obj:
            lines.append(f"  - **GUID**: `{obj['guid']}`")
        return "\n".join(lines)

    elif command_type == "modify_object":
        obj_name = response.get("object", {}).get("name", "Object")
        modified = response.get("modified_properties", response.get("properties", {}))
        lines = [f"✅ Modified **{obj_name}**"]
        if isinstance(modified, dict):
            lines.extend(_fmt_props(modified))
        return "\n".join(lines)

    elif command_type == "list_objects":
        objects = response.get("objects", [])
        if not objects:
            return "Scene is empty — no objects found."
        lines = [f"📦 **Scene Objects** ({len(objects)} total)"]
        for obj in objects:
            indent = "  " * obj.get("depth", 0)
            lines.append(f"  {indent}- **{obj['name']}** ({obj.get('type', '?')})")
        return "\n".join(lines)

    elif command_type == "create_material":
        mat = response.get("material", {})
        name = mat.get("name", "Material")
        lines = [f"✅ Created material **{name}**"]
        if "color" in mat:
            lines.append(f"  - **Color**: {_fmt_vec(mat['color'])}")
        return "\n".join(lines)

    elif command_type == "apply_material":
        mat = response.get("material_name", response.get("material", "?"))
        obj = response.get("object_name", response.get("object", "?"))
        return f"✅ Applied material **{mat}** → **{obj}**"

    elif command_type == "render_frame":
        info = response.get("render_info", response)
        lines = ["✅ Render complete"]
        if "output_path" in info:
            lines.append(f"  - **Output**: `{info['output_path']}`")
        if "width" in info and "height" in info:
            lines.append(f"  - **Resolution**: {info['width']}×{info['height']}")
        if "render_time" in info:
            lines.append(f"  - **Time**: {info['render_time']}")
        return "\n".join(lines)

    elif command_type == "set_keyframe":
        lines = ["✅ Keyframe set"]
        for key in ("object_name", "property", "value", "frame"):
            if key in response:
                lines.append(f"  - **{key.replace('_', ' ').title()}**: {response[key]}")
        return "\n".join(lines)

    elif command_type in ("save_scene", "load_scene"):
        action = "Saved" if command_type == "save_scene" else "Loaded"
        path = response.get("file_path", response.get("path", ""))
        lines = [f"✅ {action} scene"]
        if path:
            lines.append(f"  - **Path**: `{path}`")
        return "\n".join(lines)

    elif command_type == "create_mograph_cloner":
        obj = response.get("object", {})
        name = obj.get("name", "Cloner")
        lines = [f"✅ Created cloner **{name}**"]
        if "mode" in obj:
            lines.append(f"  - **Mode**: {obj['mode']}")
        if "guid" in obj:
            lines.append(f"  - **GUID**: `{obj['guid']}`")
        return "\n".join(lines)

    elif command_type == "add_effector":
        obj = response.get("object", response.get("effector", {}))
        name = obj.get("name", "Effector")
        lines = [f"✅ Added effector **{name}**"]
        if "type" in obj:
            lines.append(f"  - **Type**: {obj['type']}")
        if "applied_to" in obj:
            lines.append(f"  - **Applied to**: {obj['applied_to']}")
        return "\n".join(lines)

    elif command_type == "apply_mograph_fields":
        field = response.get("field", {})
        name = field.get("name", "Field")
        lines = [f"✅ Applied field **{name}**"]
        if "type" in field:
            lines.append(f"  - **Type**: {field['type']}")
        if "applied_to" in field:
            lines.append(f"  - **Target**: {field['applied_to']}")
        if "strength" in field:
            lines.append(f"  - **Strength**: {field['strength']}")
        if "falloff" in field:
            lines.append(f"  - **Falloff**: {field['falloff']}")
        return "\n".join(lines)

    elif command_type in ("create_soft_body", "apply_dynamics"):
        obj_name = response.get("object_name", response.get("object", {}).get("name", "Object"))
        dtype = response.get("type", response.get("dynamics_type", "dynamics"))
        return f"✅ Applied **{dtype}** dynamics to **{obj_name}**"

    elif command_type == "create_abstract_shape":
        obj = response.get("object", {})
        name = obj.get("name", "Shape")
        lines = [f"✅ Created abstract shape **{name}**"]
        if "type" in obj:
            lines.append(f"  - **Type**: {obj['type']}")
        return "\n".join(lines)

    elif command_type == "create_camera":
        cam = response.get("camera", response.get("object", {}))
        name = cam.get("name", "Camera")
        lines = [f"✅ Created camera **{name}**"]
        if "position" in cam:
            lines.append(f"  - **Position**: {_fmt_vec(cam['position'])}")
        if "focal_length" in cam:
            lines.append(f"  - **Focal Length**: {cam['focal_length']}mm")
        if "guid" in cam:
            lines.append(f"  - **GUID**: `{cam['guid']}`")
        return "\n".join(lines)

    elif command_type == "create_light":
        obj = response.get("object", {})
        name = obj.get("name", "Light")
        lines = [f"✅ Created light **{name}**"]
        if "type" in obj:
            lines.append(f"  - **Type**: {obj['type']}")
        return "\n".join(lines)

    elif command_type == "apply_shader":
        shader = response.get("shader", {})
        lines = [f"✅ Applied **{shader.get('type', 'shader')}** shader"]
        if "material" in shader:
            lines.append(f"  - **Material**: {shader['material']}")
        if "applied_to" in shader and shader["applied_to"] != "None":
            lines.append(f"  - **Applied to**: {shader['applied_to']}")
        return "\n".join(lines)

    elif command_type == "animate_camera":
        cam = response.get("camera_animation", {})
        lines = [f"✅ Camera animation created"]
        if "type" in cam:
            lines.append(f"  - **Type**: {cam['type']}")
        if "camera_name" in cam:
            lines.append(f"  - **Camera**: {cam['camera_name']}")
        if "frame_range" in cam:
            lines.append(f"  - **Frame Range**: {cam['frame_range']}")
        if "keyframe_count" in cam:
            lines.append(f"  - **Keyframes**: {cam['keyframe_count']}")
        return "\n".join(lines)

    elif command_type == "execute_python":
        result = response.get("result", "No output")
        output = response.get("output", "")
        variables = response.get("variables", {})
        warning = response.get("warning", "")
        lines = ["✅ Script executed successfully"]
        if output:
            lines.append(f"**Output:**\n```\n{output}\n```")
        elif result and result != "No output":
            lines.append(f"**Output:**\n```\n{result}\n```")
        if variables:
            vars_str = "\n".join(f"  {k}: {v}" for k, v in variables.items())
            lines.append(f"**Variables:**\n{vars_str}")
        if warning:
            lines.append(f"⚠️ {warning}")
        return "\n".join(lines) if len(lines) > 1 else "Script executed (no output)"

    elif command_type == "group_objects":
        group = response.get("group", {})
        name = group.get("name", "Group")
        children = group.get("children", [])
        lines = [f"✅ Grouped into **{name}**"]
        if children:
            lines.append(f"  - **Children**: {', '.join(children)}")
        return "\n".join(lines)

    elif command_type == "render_preview":
        if "image_data" not in response:
            return "❌ No image data returned from Cinema 4D"
        w = response.get("width", "?")
        h = response.get("height", "?")
        fmt = response.get("format", "png")
        lines = [f"✅ Preview rendered ({w}×{h}, {fmt})"]
        # Embed as base64 markdown image so Claude Code can display it
        lines.append(f"![preview](data:image/{fmt};base64,{response['image_data']})")
        return "\n".join(lines)

    elif command_type == "snapshot_scene":
        snap = response.get("snapshot", {})
        lines = ["✅ Scene snapshot created"]
        if "path" in snap:
            lines.append(f"  - **Path**: `{snap['path']}`")
        if "timestamp" in snap:
            lines.append(f"  - **Timestamp**: {snap['timestamp']}")
        if "size" in snap:
            lines.append(f"  - **Size**: {snap['size']}")
        if "assets" in snap:
            lines.append(f"  - **Assets**: {len(snap['assets'])}")
        return "\n".join(lines)

    # ----- MCP extensions: introspection -----

    elif command_type == "enumerate_descids":
        obj = response.get("object", {})
        params = response.get("parameters", [])
        truncated = response.get("truncated", False)
        filt = response.get("filter_applied", {})
        lines = [
            f"✅ Enumerated **{response.get('parameter_count', len(params))}** parameters on **{obj.get('name', '?')}** "
            f"(type_id={obj.get('type_id')}, guid={obj.get('guid')})"
        ]
        if any(filt.values()):
            applied = [f"{k}={v}" for k, v in filt.items() if v]
            lines.append(f"  - **Filters**: {', '.join(applied)}")
        if truncated:
            lines.append("  - ⚠️ Result truncated — increase max_results to see more")
        if not params:
            lines.append("  - (no parameters matched)")
        else:
            # Show up to 50 inline; full data is in the JSON response anyway
            preview = params[:50]
            lines.append("")
            lines.append("| Path | Name | dtype | Current value |")
            lines.append("|------|------|-------|---------------|")
            for p in preview:
                path_str = ".".join(str(x) for x in p.get("path", []))
                name = p.get("name", "")[:40]
                dtype = p.get("dtype", "")
                val = p.get("current_value", p.get("current_value_error", ""))
                if isinstance(val, (dict, list)):
                    val = str(val)[:60] + ("…" if len(str(val)) > 60 else "")
                else:
                    val = str(val)[:60]
                lines.append(f"| `{path_str}` | {name} | {dtype} | {val} |")
            if len(params) > 50:
                lines.append(f"\n_…showing first 50 of {len(params)} parameters._")
        return "\n".join(lines)

    elif command_type == "enumerate_userdata":
        obj = response.get("object", {})
        items = response.get("userdata", [])
        lines = [f"✅ **{len(items)}** userdata entries on **{obj.get('name', '?')}**"]
        if not items:
            lines.append("  - (no userdata)")
        else:
            for it in items[:50]:
                path_str = ".".join(str(x) for x in it.get("path", []))
                lines.append(f"  - `{path_str}` **{it.get('name', '')}** (dtype={it.get('dtype')}) = `{it.get('current_value', '')}`")
            if len(items) > 50:
                lines.append(f"  - …and {len(items) - 50} more")
        return "\n".join(lines)

    elif command_type == "find_objects":
        matches = response.get("matches", [])
        truncated = response.get("truncated", False)
        lines = [f"✅ Found **{len(matches)}** matching object(s)" + (" _(truncated)_" if truncated else "")]
        for m in matches[:100]:
            indent = "  " * m.get("depth", 0)
            lines.append(f"  - {indent}**{m.get('name', '?')}** ({m.get('type_name', '?')}, type_id={m.get('type_id')}, guid={m.get('guid')})")
        if len(matches) > 100:
            lines.append(f"  - …and {len(matches) - 100} more")
        return "\n".join(lines)

    elif command_type == "get_object_info":
        lines = [f"✅ **{response.get('name', '?')}** ({response.get('type_name', '?')})"]
        lines.append(f"  - **Type ID**: {response.get('type_id')}")
        lines.append(f"  - **GUID**: `{response.get('guid')}`")
        if "position" in response:
            lines.append(f"  - **Position**: {_fmt_vec(response['position'])}")
        if "rotation" in response:
            lines.append(f"  - **Rotation**: {_fmt_vec(response['rotation'])}")
        if "scale" in response:
            lines.append(f"  - **Scale**: {_fmt_vec(response['scale'])}")
        if response.get("layer_name"):
            lines.append(f"  - **Layer**: {response['layer_name']}")
        if response.get("parent_name"):
            lines.append(f"  - **Parent**: {response['parent_name']} (`{response.get('parent_guid')}`)")
        lines.append(f"  - **Children**: {response.get('child_count', 0)}")
        lines.append(f"  - **Tags**: {response.get('tag_count', 0)}")
        for t in response.get("tags", [])[:20]:
            lines.append(f"    - {t.get('name')} (type_id={t.get('type_id')})")
        return "\n".join(lines)

    elif command_type == "dump_object_tree":
        nodes = response.get("nodes", [])
        lines = [f"✅ **{len(nodes)}** nodes in tree"]
        for n in nodes[:200]:
            indent = "  " * n.get("depth", 0)
            lines.append(f"  {indent}- **{n.get('name', '?')}** _{n.get('type_name', '?')}_ (id={n.get('type_id')}, guid={n.get('guid')})")
        if len(nodes) > 200:
            lines.append(f"  _…and {len(nodes) - 200} more nodes._")
        return "\n".join(lines)

    elif command_type == "get_console_log":
        entries = response.get("entries", [])
        hooked = response.get("geprint_hooked", False)
        lines = [
            f"✅ **{len(entries)}** log entries  "
            f"(buffer_max={response.get('buffer_max')}, GePrint_hook={'on' if hooked else 'off'})"
        ]
        if not hooked:
            lines.append("  - ⚠️ GePrint hook not installed — only plugin self.log() output is captured. Restart socket server to install hook.")
        if not entries:
            lines.append("  - (empty)")
        else:
            for e in entries[-300:]:
                lines.append(f"  `{e.get('iso', '')}` [{e.get('source', '')}] {e.get('message', '')}")
        return "\n".join(lines)

    elif command_type == "clear_console_log":
        return f"✅ Console log buffer cleared"

    elif command_type == "list_installed_plugins":
        plugins = response.get("plugins", [])
        flt = response.get("filter", {})
        lines = [f"✅ Found **{len(plugins)}** plugins"]
        applied = [f"{k}={v}" for k, v in flt.items() if v]
        if applied:
            lines.append(f"  - **Filter**: {', '.join(applied)}")
        # Group by type for readability
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for p in plugins:
            by_type.setdefault(p.get("type_name", "?"), []).append(p)
        for tname in sorted(by_type.keys()):
            entries = by_type[tname]
            lines.append(f"\n  **{tname}** ({len(entries)})")
            for p in entries[:50]:
                lines.append(f"  - id=`{p.get('id')}`  {p.get('name', '?')}")
            if len(entries) > 50:
                lines.append(f"  - …and {len(entries) - 50} more")
        return "\n".join(lines)

    elif command_type == "get_c4d_info":
        lines = ["✅ **Cinema 4D environment**"]
        for k, v in response.items():
            if k == "status" or k == "active_document":
                continue
            label = k.replace("_", " ").title()
            lines.append(f"  - **{label}**: `{v}`")
        if "active_document" in response:
            doc = response["active_document"]
            lines.append("\n  **Active document**:")
            for k, v in doc.items():
                lines.append(f"    - **{k.title()}**: {v}")
        return "\n".join(lines)

    elif command_type == "viewport_screenshot":
        if "image_data" not in response:
            return f"❌ No image data returned ({response.get('error', 'unknown')})"
        w = response.get("width", "?")
        h = response.get("height", "?")
        renderer = response.get("renderer", "?")
        cam = response.get("camera", "?")
        lines = [f"✅ Viewport screenshot ({w}×{h}) — renderer: **{renderer}**, camera: **{cam}**"]
        for warn in response.get("warnings", []) or []:
            lines.append(f"⚠️  {warn}")
        lines.append(f"![viewport](data:image/png;base64,{response['image_data']})")
        return "\n".join(lines)

    elif command_type == "get_viewport_state":
        lines = ["✅ **Viewport state**"]
        if "frame" in response and response["frame"]:
            f = response["frame"]
            lines.append(f"  - **Frame**: {f.get('width')}×{f.get('height')} (L={f.get('left')}, T={f.get('top')}, R={f.get('right')}, B={f.get('bottom')})")
        if response.get("camera"):
            c = response["camera"]
            lines.append(f"  - **Camera**: {c.get('name')} (type_id={c.get('type_id')}, guid={c.get('guid')})")
            lines.append(f"    - Position: {_fmt_vec(c.get('position', []))}")
            if c.get("focal_length_mm") is not None:
                lines.append(f"    - Focal length: {c['focal_length_mm']:.2f}mm")
        if response.get("projection_mode") is not None:
            lines.append(f"  - **Projection mode**: {response['projection_mode']}")
        if response.get("active_renderer"):
            lines.append(f"  - **Active renderer**: {response['active_renderer']}")
        return "\n".join(lines)

    elif command_type == "list_render_engines":
        engines = response.get("engines", [])
        active_id = response.get("active_renderer_id")
        active_name = response.get("active_renderer_name")
        lines = [
            f"✅ **{len(engines)}** render engine(s) registered",
            f"  - **Active**: {active_name} (id={active_id})",
            "",
        ]
        for e in engines:
            mark = "▶ " if e.get("is_active") else "  "
            lines.append(f"  {mark}id=`{e.get('id')}`  **{e.get('name', '?')}**")
        return "\n".join(lines)

    elif command_type == "get_active_renderer":
        lines = ["✅ **Active renderer**"]
        for k, v in response.items():
            if k == "status":
                continue
            lines.append(f"  - **{k.replace('_', ' ').title()}**: {v}")
        return "\n".join(lines)

    elif command_type == "dump_material_graph":
        target = response.get("target", {})
        graph = response.get("shader_graph", [])
        tag_graphs = response.get("tag_shader_graphs", [])
        lines = [f"✅ Material graph: **{target.get('name', '?')}** (type_id={target.get('type_id')})"]
        lines.append(f"  - Top-level shaders: {len(graph)}")
        if tag_graphs:
            lines.append(f"  - Tags with shaders: {len(tag_graphs)}")

        def _render_node(n, indent=2):
            out = []
            pad = "  " * indent
            out.append(f"{pad}- **{n.get('name', '?')}** (type_id={n.get('type_id')}, guid={n.get('guid')})")
            for p in n.get("params", [])[:10]:
                pname = p.get("name", "?")[:30]
                pval = str(p.get("value", ""))[:50]
                out.append(f"{pad}    `{p.get('path')}` {pname} = `{pval}`")
            for child in n.get("children", []):
                out.extend(_render_node(child, indent + 1))
            return out

        for n in graph:
            lines.extend(_render_node(n, indent=1))

        for tg in tag_graphs:
            lines.append(f"\n  **Tag**: {tg.get('tag_name')} (type_id={tg.get('tag_type_id')})")
            for n in tg.get("shaders", []):
                lines.extend(_render_node(n, indent=2))

        return "\n".join(lines)

    elif command_type == "create_via_command":
        new_obj = response.get("new_object", {})
        if not new_obj:
            return f"⚠️ {response.get('warning', 'No new object detected')}"
        lines = [f"✅ Created **{new_obj.get('name', '?')}** via CallCommand({response.get('command_id')})"]
        lines.append(f"  - **Type**: {new_obj.get('type_name')} (id={new_obj.get('type_id')})")
        lines.append(f"  - **GUID**: `{new_obj.get('guid')}`")
        return "\n".join(lines)

    elif command_type == "link_shader_to_parameter":
        tgt = response.get("target", {})
        sh = response.get("shader", {})
        param = response.get("parameter", {})
        applied = response.get("applied_shader_params", [])
        lines = [
            f"✅ Linked shader (plugin_id={sh.get('plugin_id')}) → **{tgt.get('name', '?')}**.{param.get('name') or param.get('path')}",
            f"  - **Target GUID**: `{tgt.get('guid')}`",
            f"  - **Shader GUID**: `{sh.get('guid')}`",
            f"  - **Parameter path**: `{param.get('path')}`",
        ]
        if applied:
            lines.append(f"  - **Shader params applied**:")
            for ap in applied:
                lines.append(f"    - {ap}")
        return "\n".join(lines)

    # Fallback: format the dict generically
    lines = [f"✅ {status}"]
    for k, v in response.items():
        if k == "status":
            continue
        if isinstance(v, dict):
            lines.append(f"  - **{k.replace('_', ' ').title()}**:")
            lines.extend(_fmt_props(v, "    "))
        elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
            lines.append(f"  - **{k.replace('_', ' ').title()}**: ({len(v)} items)")
        else:
            lines.append(f"  - **{k.replace('_', ' ').title()}**: {v}")
    return "\n".join(lines)


# Initialize our FastMCP server
mcp = FastMCP(name="Cinema4D")


@mcp.tool()
async def get_scene_info(ctx: Context) -> str:
    """Get information about the current Cinema 4D scene."""
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        response = send_to_c4d(connection, {"command": "get_scene_info"})

        if "error" in response:
            return f"❌ Error: {response['error']}"

        # Format scene info nicely
        scene_info = response.get("scene_info", {})
        return f"""
# Cinema 4D Scene Information
- **Filename**: {scene_info.get('filename', 'Untitled')}
- **Objects**: {scene_info.get('object_count', 0)}
- **Polygons**: {scene_info.get('polygon_count', 0):,}
- **Materials**: {scene_info.get('material_count', 0)}
- **Current Frame**: {scene_info.get('current_frame', 0)}
- **FPS**: {scene_info.get('fps', 30)}
- **Frame Range**: {scene_info.get('frame_start', 0)} - {scene_info.get('frame_end', 90)}
"""


@mcp.tool()
async def add_primitive(
    primitive_type: str,
    name: Optional[str] = None,
    position: Optional[List[float]] = None,
    size: Optional[List[float]] = None,
    ctx: Context = None,
) -> str:
    """
    Add a primitive object to the Cinema 4D scene.

    Args:
        primitive_type: Type of primitive (cube, sphere, cone, cylinder, plane, etc.)
        name: Optional name for the new object
        position: Optional [x, y, z] position
        size: Optional [x, y, z] size or dimensions
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Prepare command
        command = {
            "command": "add_primitive",
            "type": primitive_type,
        }

        if name:
            command["object_name"] = name
        if position:
            command["position"] = position
        if size:
            command["size"] = size

        # Send command to Cinema 4D
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "add_primitive")


@mcp.tool()
async def modify_object(
    object_name: str, properties: Dict[str, Any], ctx: Context
) -> str:
    """
    Modify properties of an existing object.

    Args:
        object_name: Name of the object to modify
        properties: Dictionary of properties to modify (position, rotation, scale, etc.)
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Send command to Cinema 4D
        response = send_to_c4d(
            connection,
            {
                "command": "modify_object",
                "object_name": object_name,
                "properties": properties,
            },
        )

        return format_c4d_response(response, "modify_object")


@mcp.tool()
async def list_objects(ctx: Context) -> str:
    """List all objects in the current Cinema 4D scene.

    If this tool returns a validation error, use execute_python_script as a fallback
    to traverse the object hierarchy manually via the c4d API.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        response = send_to_c4d(connection, {"command": "list_objects"})
        return format_c4d_response(response, "list_objects")


@mcp.tool()
async def create_material(
    name: str,
    color: Optional[List[float]] = None,
    properties: Optional[Dict[str, Any]] = None,
    ctx: Context = None,
) -> str:
    """
    Create a new material in Cinema 4D.

    Args:
        name: Name for the new material
        color: Optional [R, G, B] color (values 0-1)
        properties: Optional additional material properties
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Prepare command
        command = {"command": "create_material", "material_name": name}

        if color:
            command["color"] = color
        if properties:
            command["properties"] = properties

        # Send command to Cinema 4D
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "create_material")


@mcp.tool()
async def apply_material(material_name: str, object_name: str, ctx: Context) -> str:
    """
    Apply a material to an object.

    Args:
        material_name: Name of the material to apply
        object_name: Name of the object to apply the material to
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Send command to Cinema 4D
        response = send_to_c4d(
            connection,
            {
                "command": "apply_material",
                "material_name": material_name,
                "object_name": object_name,
            },
        )
        return format_c4d_response(response, "apply_material")


@mcp.tool()
async def inspect_redshift_materials(
    material_name: Optional[str] = None,
    include_assignments: bool = True,
    include_preview: bool = True,
    include_description: bool = True,
    include_container: bool = True,
    include_graph: bool = True,
    ctx: Context = None,
) -> str:
    """
    Inspect Redshift materials with best-effort fallbacks.

    This tool is read-only and is designed to be useful even when the Redshift
    Python runtime is unavailable. It can still report names, assignments,
    preview-derived colors, readable description/container fields, and will
    attempt graph inspection only when Cinema 4D exposes that data.

    Args:
        material_name: Optional material name filter
        include_assignments: Include texture-tag assignments in the scene
        include_preview: Include sampled preview bitmap color data
        include_description: Include readable description entries
        include_container: Include safe BaseContainer values
        include_graph: Attempt node-graph inspection when available
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        command = {
            "command": "inspect_redshift_materials",
            "include_assignments": include_assignments,
            "include_preview": include_preview,
            "include_description": include_description,
            "include_container": include_container,
            "include_graph": include_graph,
        }

        if material_name:
            command["material_name"] = material_name

        response = send_to_c4d(connection, command)

        if "error" in response:
            return f"❌ Error: {response['error']}"

        return json.dumps(response, indent=2)


@mcp.tool()
async def render_frame(
    output_path: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    ctx: Context = None,
) -> str:
    """
    Render the current frame.

    Args:
        output_path: Optional path to save the rendered image
        width: Optional render width in pixels
        height: Optional render height in pixels
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Prepare command
        command = {"command": "render_frame"}

        if output_path:
            command["output_path"] = output_path
        if width:
            command["width"] = width
        if height:
            command["height"] = height

        # Send command to Cinema 4D
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "render_frame")


@mcp.tool()
async def set_keyframe(
    object_name: str, property_name: str, value: Any, frame: int, ctx: Context
) -> str:
    """
    Set a keyframe for an object property.

    Args:
        object_name: Name of the object
        property_name: Name of the property to keyframe (e.g., 'position.x')
        value: Value to set at the keyframe
        frame: Frame number to set the keyframe at
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Send command to Cinema 4D
        response = send_to_c4d(
            connection,
            {
                "command": "set_keyframe",
                "object_name": object_name,
                "property_name": property_name,
                "value": value,
                "frame": frame,
            },
        )
        return format_c4d_response(response, "set_keyframe")


@mcp.tool()
async def save_scene(file_path: Optional[str] = None, ctx: Context = None) -> str:
    """
    Save the current Cinema 4D scene.

    Args:
        file_path: Optional path to save the scene to
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Prepare command
        command = {"command": "save_scene"}

        if file_path:
            command["file_path"] = file_path

        # Send command to Cinema 4D
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "save_scene")


@mcp.tool()
async def load_scene(file_path: str, ctx: Context) -> str:
    """
    Load a Cinema 4D scene file.

    Args:
        file_path: Path to the scene file to load
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Send command to Cinema 4D
        response = send_to_c4d(
            connection, {"command": "load_scene", "file_path": file_path}
        )
        return format_c4d_response(response, "load_scene")


@mcp.tool()
async def create_mograph_cloner(
    cloner_type: str, name: Optional[str] = None, ctx: Context = None
) -> str:
    """
    Create a MoGraph Cloner object of specified type.

    Args:
        cloner_type: Type of cloner (grid, radial, linear)
        name: Optional name for the cloner
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        command = {"command": "create_mograph_cloner", "mode": cloner_type}

        if name:
            command["cloner_name"] = name

        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "create_mograph_cloner")


@mcp.tool()
async def add_effector(
    effector_type: str,
    name: Optional[str] = None,
    target: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """
    Add a MoGraph Effector to the scene.

    Args:
        effector_type: Type of effector (random, shader, field)
        name: Optional name for the effector
        target: Optional target object (e.g., cloner) to apply the effector to
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        command = {"command": "add_effector", "effector_type": effector_type}

        if name:
            command["effector_name"] = name

        if target:
            command["cloner_name"] = target

        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "add_effector")


@mcp.tool()
async def apply_mograph_fields(
    field_type: str,
    target: Optional[str] = None,
    field_name: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
    ctx: Context = None,
) -> str:
    """
    Create and apply a MoGraph Field.

    Args:
        field_type: Type of field (spherical, box, cylindrical, linear, radial, noise)
        target: Optional target object to apply the field to
        field_name: Optional name for the field
        parameters: Optional parameters for the field (strength, falloff)
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Build the command with required parameters
        command = {"command": "apply_mograph_fields", "field_type": field_type}

        # Add optional parameters
        if target:
            command["target_name"] = target

        if field_name:
            command["field_name"] = field_name

        if parameters:
            command["parameters"] = parameters

        # Log the command for debugging
        logger.info(f"Sending apply_mograph_fields command: {command}")

        # Send the command to Cinema 4D
        response = send_to_c4d(connection, command)

        if "error" in response:
            logger.error(f"Error applying field: {response['error']}")
        return format_c4d_response(response, "apply_mograph_fields")


@mcp.tool()
async def create_soft_body(object_name: str, ctx: Context = None) -> str:
    """
    Add soft body dynamics to the specified object.

    Args:
        object_name: Name of the object to convert to a soft body
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        response = send_to_c4d(
            connection, {"command": "create_soft_body", "object_name": object_name}
        )
        return format_c4d_response(response, "create_soft_body")


@mcp.tool()
async def apply_dynamics(
    object_name: str, dynamics_type: str, ctx: Context = None
) -> str:
    """
    Add dynamics (rigid or soft) to the specified object.

    Args:
        object_name: Name of the object to apply dynamics to
        dynamics_type: Type of dynamics to apply (rigid, soft)
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        response = send_to_c4d(
            connection,
            {
                "command": "apply_dynamics",
                "object_name": object_name,
                "type": dynamics_type,
            },
        )
        return format_c4d_response(response, "apply_dynamics")


@mcp.tool()
async def create_abstract_shape(
    shape_type: str, name: Optional[str] = None, ctx: Context = None
) -> str:
    """
    Create an organic, abstract shape.

    Args:
        shape_type: Type of shape (blob, metaball)
        name: Optional name for the shape
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        command = {"command": "create_abstract_shape", "shape_type": shape_type}

        if name:
            command["object_name"] = name

        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "create_abstract_shape")


@mcp.tool()
async def create_camera(
    name: Optional[str] = None,
    position: Optional[List[float]] = None,
    properties: Optional[Dict[str, Any]] = None,
    ctx: Context = None,
) -> str:
    """
    Create a new camera in the scene.

    Args:
        name: Optional name for the new camera.
        position: Optional [x, y, z] position.
        properties: Optional dictionary of camera properties (e.g., {"focal_length": 50}).
    """
    requested_name = name

    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        command = {"command": "create_camera"}
        if requested_name:
            command["name"] = (
                requested_name  # Use the 'name' key expected by the handler
            )
        if position:
            command["position"] = position
        if properties:
            command["properties"] = properties

        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "create_camera")


@mcp.tool()
async def create_light(
    light_type: str, name: Optional[str] = None, ctx: Context = None
) -> str:
    """
    Add a light to the scene.

    Args:
        light_type: Type of light (area, dome, spot)
        name: Optional name for the light
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        command = {"command": "create_light", "type": light_type}

        if name:
            command["object_name"] = name

        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "create_light")


@mcp.tool()
async def apply_shader(
    shader_type: str,
    material_name: Optional[str] = None,
    object_name: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """
    Create and apply a specialized shader material.

    Args:
        shader_type: Type of shader (noise, gradient, fresnel, etc)
        material_name: Optional name of material to apply shader to
        object_name: Optional name of object to apply the material to
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        command = {"command": "apply_shader", "shader_type": shader_type}

        if material_name:
            command["material_name"] = material_name

        if object_name:
            command["object_name"] = object_name

        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "apply_shader")


@mcp.tool()
async def animate_camera(
    animation_type: str,
    camera_name: Optional[str] = None,
    positions: Optional[List[List[float]]] = None,
    frames: Optional[List[int]] = None,
    ctx: Context = None,
) -> str:
    """
    Create a camera animation.

    Args:
        animation_type: Type of animation (wiggle, orbit, spline, linear)
        camera_name: Optional name of camera to animate
        positions: Optional list of [x,y,z] camera positions for keyframes
        frames: Optional list of frame numbers for keyframes
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Create command with the animation type
        command = {"command": "animate_camera", "path_type": animation_type}

        # Add camera name if provided
        if camera_name:
            command["camera_name"] = camera_name

        # Handle positions and frames if provided
        if positions:
            command["positions"] = positions

            # Generate frames if not provided (starting at 0 with 15 frame intervals)
            if not frames:
                frames = [i * 15 for i in range(len(positions))]

            command["frames"] = frames

        if animation_type == "orbit":
            # For orbit animations, we need to generate positions in a circle
            # if none are provided
            if not positions:
                # Create a set of default positions for an orbit animation
                radius = 200  # Default orbit radius
                height = 100  # Default height
                points = 12  # Number of points around the circle

                orbit_positions = []
                orbit_frames = []

                # Create positions in a circle
                for i in range(points):
                    angle = (i / points) * 2 * 3.14159  # Convert to radians
                    x = radius * math.cos(angle)
                    z = radius * math.sin(angle)
                    y = height
                    orbit_positions.append([x, y, z])
                    orbit_frames.append(i * 10)  # 10 frames between positions

                command["positions"] = orbit_positions
                command["frames"] = orbit_frames

        # Send the command to Cinema 4D
        response = send_to_c4d(connection, command)

        return format_c4d_response(response, "animate_camera")


@mcp.tool()
async def execute_python_script(script: str, ctx: Context) -> str:
    """
    Execute a Python script in Cinema 4D's Python environment.

    This is the most reliable tool for non-trivial operations — it gives full access
    to the c4d API and avoids wrapper/schema mismatches that can affect other tools.

    Args:
        script: Python code to execute in Cinema 4D. Has access to `c4d` and
            `c4d.modules.mograph` modules.

    Important usage notes:
        - For animated/MoGraph data, always call doc.ExecutePasses() after SetTime():
            doc.SetTime(c4d.BaseTime(frame, fps))
            doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)
        - For MoGraph/effector data, iterate frames sequentially (0..N) rather than
          jumping directly to a later frame — sequential stepping produces more
          faithful results.
        - Security restrictions block certain keywords: import os, subprocess, exec(, eval(.
          Keep scripts within the c4d API surface.
        - For heavy operations (dense frame loops, complex MoGraph scenes), split work
          into multiple smaller scripts rather than one large monolith.
        - Use print() to return results — output is captured and returned.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Send command to Cinema 4D
        response = send_to_c4d(
            connection, {"command": "execute_python", "script": script}
        )
        return format_c4d_response(response, "execute_python")


@mcp.tool()
async def group_objects(
    object_names: List[str], group_name: Optional[str] = None, ctx: Context = None
) -> str:
    """
    Group multiple objects under a null object.

    Args:
        object_names: List of object names to group
        group_name: Optional name for the group
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Prepare command
        command = {"command": "group_objects", "object_names": object_names}

        if group_name:
            command["group_name"] = group_name

        # Send command to Cinema 4D
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "group_objects")


@mcp.tool()
async def render_preview(
    width: Optional[int] = None,
    height: Optional[int] = None,
    frame: Optional[int] = None,
    ctx: Context = None,
) -> str:
    """
    Render the current view and return a base64-encoded preview image.

    Args:
        width: Optional preview width in pixels
        height: Optional preview height in pixels
        frame: Optional frame number to render
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Prepare command
        command = {"command": "render_preview"}

        if width:
            command["width"] = width
        if height:
            command["height"] = height
        if frame is not None:
            command["frame"] = frame

        # Set longer timeout for rendering
        logger.info(f"Sending render_preview command with parameters: {command}")

        # Send command to Cinema 4D
        response = send_to_c4d(connection, command)

        if "error" in response:
            return f"❌ Error: {response['error']}"

        return format_c4d_response(response, "render_preview")


@mcp.tool()
async def snapshot_scene(
    file_path: Optional[str] = None, include_assets: bool = False, ctx: Context = None
) -> str:
    """
    Create a snapshot of the current scene state.

    Args:
        file_path: Optional path to save the snapshot
        include_assets: Whether to include external assets in the snapshot
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"

        # Prepare command
        command = {"command": "snapshot_scene"}

        if file_path:
            command["file_path"] = file_path

        command["include_assets"] = include_assets

        # Send command to Cinema 4D
        response = send_to_c4d(connection, command)

        return format_c4d_response(response, "snapshot_scene")


# ============================================================
# MCP extensions — Tier 1: Introspection tools
# Added 2026-04-23. These are read-only and unblock plugin development
# by exposing C4D's full description/userdata/scene-tree state to the
# MCP client. enumerate_descids in particular cracks undocumented
# plugin parameter IDs (Octane, Redshift, third-party tags).
# ============================================================


@mcp.tool()
async def enumerate_descids(
    object_name: Optional[str] = None,
    guid: Optional[str] = None,
    name_filter: Optional[str] = None,
    name_pattern: Optional[str] = None,
    include_values: bool = True,
    max_results: int = 5000,
    top_level_only: bool = False,
    ctx: Context = None,
) -> str:
    """Enumerate every parameter (DescID) of a Cinema 4D object.

    THIS IS THE MOST IMPORTANT TOOL FOR DISCOVERING UNDOCUMENTED PLUGIN
    PARAMETER IDS. Use it to find Octane Area Light's texture/distribution
    input, Redshift node parameters, third-party tag IDs, etc. Mirrors
    the workflow of C4D's Customize Palettes attribute inspector.

    Provide either `object_name` or `guid` to identify the target object.
    Filter the result with `name_filter` (case-insensitive substring) or
    `name_pattern` (fnmatch wildcard) to narrow large parameter sets —
    e.g. name_filter="texture" surfaces texture-related params on an
    Octane light in seconds.

    Set `top_level_only=True` to skip nested group parameters.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "enumerate_descids",
            "include_values": include_values,
            "max_results": max_results,
            "top_level_only": top_level_only,
        }
        if guid:
            command["guid"] = guid
        if object_name:
            command["object_name"] = object_name
        if name_filter:
            command["name_filter"] = name_filter
        if name_pattern:
            command["name_pattern"] = name_pattern
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "enumerate_descids")


@mcp.tool()
async def enumerate_userdata(
    object_name: Optional[str] = None,
    guid: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Enumerate the User Data container on an object (separate from regular Description params)."""
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "enumerate_userdata"}
        if guid:
            command["guid"] = guid
        if object_name:
            command["object_name"] = object_name
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "enumerate_userdata")


@mcp.tool()
async def find_objects(
    name_pattern: Optional[str] = None,
    name_contains: Optional[str] = None,
    type_id: Optional[int] = None,
    type_id_min: Optional[int] = None,
    type_id_max: Optional[int] = None,
    max_results: int = 200,
    ctx: Context = None,
) -> str:
    """Find scene objects matching name pattern, substring, and/or type id.

    Combine filters AND-style. Examples:
      - name_pattern="Cube*" matches "Cube", "Cube.1", "Cube_test"
      - name_contains="light" — case-insensitive substring match
      - type_id=5159 — exact type match (c4d.Ocube)
      - type_id_min=1029525, type_id_max=1029999 — Octane-plugin ID range scan

    Results include name, type, type_id, guid, and depth in the hierarchy.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "find_objects", "max_results": max_results}
        if name_pattern:
            command["name_pattern"] = name_pattern
        if name_contains:
            command["name_contains"] = name_contains
        if type_id is not None:
            command["type_id"] = type_id
        if type_id_min is not None:
            command["type_id_min"] = type_id_min
        if type_id_max is not None:
            command["type_id_max"] = type_id_max
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "find_objects")


@mcp.tool()
async def get_object_info(
    object_name: Optional[str] = None,
    guid: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Return comprehensive info on a single object: name, type, GUID, transform,
    visibility flags, layer, parent, child count, and all tags."""
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "get_object_info"}
        if guid:
            command["guid"] = guid
        if object_name:
            command["object_name"] = object_name
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "get_object_info")


@mcp.tool()
async def dump_object_tree(
    root_name: Optional[str] = None,
    root_guid: Optional[str] = None,
    max_depth: int = 100,
    ctx: Context = None,
) -> str:
    """Dump the scene hierarchy as a flat list of {depth, name, type, guid}.
    Pass `root_name` or `root_guid` to start from a specific subtree; omit both
    to dump the entire scene from the document root."""
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "dump_object_tree", "max_depth": max_depth}
        if root_guid:
            command["guid"] = root_guid
        if root_name:
            command["object_name"] = root_name
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "dump_object_tree")


# ---- Tier 2: Console log capture ----


@mcp.tool()
async def get_console_log(
    limit: Optional[int] = 200,
    since_ts: Optional[float] = None,
    source: Optional[str] = None,
    contains: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Read recent entries from the MCP console log buffer.

    Captures both Cinema 4D's c4d.GePrint() output (via runtime hook installed
    when the socket server starts) AND the plugin's internal self.log() messages.
    This lets the MCP client see what's happening in C4D without manually
    inspecting the console window.

    Filters:
      - limit: max number of entries to return (default 200, newest last)
      - since_ts: only entries with timestamp > since_ts (epoch seconds)
      - source: filter by source — 'plugin', 'c4d.GePrint', or 'mcp'
      - contains: case-insensitive substring filter on message content
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "get_console_log"}
        if limit is not None:
            command["limit"] = limit
        if since_ts is not None:
            command["since_ts"] = since_ts
        if source:
            command["source"] = source
        if contains:
            command["contains"] = contains
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "get_console_log")


@mcp.tool()
async def clear_console_log(ctx: Context = None) -> str:
    """Empty the MCP console log ring buffer (does NOT clear C4D's own console window)."""
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "clear_console_log"}
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "clear_console_log")


# ---- Tier 3: Plugin lifecycle ----


@mcp.tool()
async def list_installed_plugins(
    plugin_type: str = "all",
    plugin_id: Optional[int] = None,
    name_contains: Optional[str] = None,
    id_min: Optional[int] = None,
    id_max: Optional[int] = None,
    ctx: Context = None,
) -> str:
    """List loaded Cinema 4D plugins, filterable by type, id, or name.

    plugin_type options: 'object', 'tag', 'shader', 'material', 'command',
    'tool', 'node', 'bitmapsaver', 'bitmaploader', 'videopost', 'sculptbrush',
    'falloff', 'field', 'all' (default).

    Use id_min/id_max to scan a plugin ID range — invaluable for discovering
    Octane/Redshift/third-party plugins. Example: id_min=1029525, id_max=1030000
    to enumerate plugins in Octane's typical range.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "list_installed_plugins", "plugin_type": plugin_type}
        if plugin_id is not None:
            command["plugin_id"] = plugin_id
        if name_contains:
            command["name_contains"] = name_contains
        if id_min is not None:
            command["id_min"] = id_min
        if id_max is not None:
            command["id_max"] = id_max
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "list_installed_plugins")


@mcp.tool()
async def get_c4d_info(ctx: Context = None) -> str:
    """Return C4D environment info: version, Python version, install paths, prefs path,
    active document, and Cinema 4D MCP buffer state. Useful for diagnostics."""
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "get_c4d_info"}
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "get_c4d_info")


# ---- Tier 4: Viewport / render engine ----


@mcp.tool()
async def viewport_screenshot(
    width: int = 800,
    height: int = 450,
    renderer: str = "hardware",
    frame: Optional[int] = None,
    save_path: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Capture a viewport-style screenshot of the active C4D scene.

    `renderer` options:
      - 'hardware' (default): C4D's OpenGL preview renderer. Always works,
        doesn't need a scene light, fast. Use this unless you need otherwise.
      - 'standard': C4D's software renderer. WARNING: in installs with Octane
        (or some other 3rd-party render plugin), Octane's hooks intercept the
        Standard pipeline and produce all-black output. The plugin auto-detects
        an all-black render and falls back to 'hardware', surfacing a warning
        in the response. Standard also requires at least one scene light.
      - 'current': render through the user's active engine (Octane/Redshift).
        Note: Octane viewport_screenshot output may not match the live Octane
        viewer — verify Octane-specific behavior in C4D directly.

    `save_path` options:
      - None (default): the image is returned inline as a base64 PNG.
        Practical limit ~800x450 due to MCP response token budget (~60K).
      - file path: the PNG is written to disk and the response returns
        {path, width, height, renderer} instead of base64. Use this for
        captures larger than ~1024x768, or when the inline path
        exceeds the token budget.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "viewport_screenshot",
            "width": width,
            "height": height,
            "renderer": renderer,
        }
        if frame is not None:
            command["frame"] = frame
        if save_path is not None:
            command["save_path"] = save_path
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "viewport_screenshot")


@mcp.tool()
async def viewport_screenshot_multiview(
    width: int = 400,
    height: int = 300,
    renderer: str = "hardware",
    views: Optional[List[str]] = None,
    save_dir: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Capture multiple viewport angles in a single call by toggling the active
    BaseDraw projection between captures.

    Args:
      width / height: per-view dimensions (default 400x300 — kept small so 4
          views fit under the MCP token budget when returned inline).
      renderer: 'hardware' (default), 'standard', or 'current'. Same semantics
          as `viewport_screenshot`.
      views: subset of ['perspective', 'top', 'front', 'right', 'left',
          'bottom', 'back']. Default: ['perspective', 'top', 'front', 'right'].
      save_dir: if provided, each PNG is written to this directory as
          `multiview_<viewname>.png` and the response returns file paths
          instead of inline base64. Use this when capturing at higher
          resolutions that would blow the token budget.

    The original viewport projection is restored after capture.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "viewport_screenshot_multiview",
            "width": width,
            "height": height,
            "renderer": renderer,
        }
        if views is not None:
            command["views"] = views
        if save_dir is not None:
            command["save_dir"] = save_dir
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "viewport_screenshot_multiview")


@mcp.tool()
async def set_viewport_shading_mode(
    mode: Optional[str] = None,
    line_overlay: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Set the active viewport's shading mode and/or line overlay.

    `mode` (BASEDRAW_DATA_SDISPLAYMODE) — one of:
      - 'gouraud'      : full shading with lights (default C4D mode)
      - 'quick'        : quick gouraud, faster but less accurate
      - 'nolights'     : ambient shading, ignores scene lights
      - 'noshading'    : flat constant-color (silhouette+matte)
      - 'hidden_line'  : hidden-line wireframe
      - 'lines'        : wireframe with all edges visible
      - 'wire'         : edges only, no faces
      - 'box'          : bounding boxes only
      - 'skeleton'     : object axis triads only

    `line_overlay` (BASEDRAW_DATA_LDISPLAYMODE) — overlay drawn on top of shading:
      - 'none'         : no overlay
      - 'wire'         : show wireframe over shaded surface
      - 'isoparms'     : show isoparm lines (NURBS / SDS cage)
      - 'box'          : show bounding box overlay

    At least one of `mode` or `line_overlay` should be provided. Returns the
    previous values so callers can restore state.

    Tip: combine `wire` line_overlay with `gouraud` shading to get the classic
    "shaded with wireframe" view that's invaluable for topology debugging.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "set_viewport_shading_mode"}
        if mode is not None:
            command["mode"] = mode
        if line_overlay is not None:
            command["line_overlay"] = line_overlay
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "set_viewport_shading_mode")


@mcp.tool()
async def run_modeling_command(
    op: str,
    targets: Optional[List[str]] = None,
    params: Optional[Dict[str, Any]] = None,
    mode: str = "all",
    ctx: Context = None,
) -> str:
    """Run a Cinema 4D modeling command (`SendModelingCommand`) on one or more objects.

    Wraps the most-needed C4D modeling operations. For ops that operate on
    selections (bevel, inset, smooth on selected polys), use `mode` to
    specify the selection level.

    Args:
      op: canonical op name. Supported:
        - 'axis_center'             — recenter the object's axis to its bbox
                                       center (the canonical fix for
                                       post-Disconnect/Split frozen-coord polys).
                                       Params: center_mode='bbox' | 'world_center' | 'keep'
        - 'optimize'                — weld coincident verts, drop unused points,
                                       merge redundant polys.
                                       Params: tolerance (float, scene units, default 0.01),
                                               merge_points (bool, default True),
                                               merge_polys (bool, default True),
                                               remove_unused (bool, default True)
        - 'make_editable'           — convert generators (primitives, sweeps,
                                       lofts, etc.) to editable polygon meshes.
                                       Equivalent to pressing 'C' in C4D.
        - 'current_state_to_object' — bake generator output into a static mesh
                                       at its current evaluation state.
                                       Same effect for primitives;
                                       different for SDS/cloners (bakes
                                       smooth/clone state, not just makes editable).
        - 'subdivide'               — subdivide each polygon.
                                       Params: levels (int, default 1),
                                               hyper (bool, default False;
                                               True = HyperNURBS-style smoothing).
        - 'smooth'                  — laplacian smooth on points / point selection.
                                       Params: strength, iterations, smooth_type
        - 'bevel'                   — bevel selected edges.
                                       Params: offset, subdivision
        - 'inset'                   — inner-extrude selected polys.
        - 'extrude'                 — extrude selected polys.
        - 'connect' / 'split' / 'disconnect' / 'delete'
        - 'polygonize' / 'triangulate' / 'untriangulate'

      targets: list of object names or GUIDs. If empty/None, uses the
        current Object Manager selection.
      params: op-specific keyword args (see per-op notes above). Optional.
      mode: 'all' | 'points' | 'edges' | 'polygons' — selection level the
        command operates on. Default 'all'.

    Returns: per-target status including any newly created objects (e.g.
    make_editable returns a new editable mesh; the original generator is
    replaced).

    Examples:
      - Recenter axes on every selected piece (the post-Split fix):
          run_modeling_command(op='axis_center')
      - Weld coincident verts on the active mesh:
          run_modeling_command(op='optimize', params={'tolerance': 0.01})
      - Bake a Cloner's output into static geometry:
          run_modeling_command(op='current_state_to_object', targets=['MyCloner'])
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "run_modeling_command",
            "op": op,
            "mode": mode,
        }
        if targets is not None:
            command["targets"] = targets
        if params is not None:
            command["params"] = params
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "run_modeling_command")


@mcp.tool()
async def vertex_map_stats(
    target: Optional[str] = None,
    vmap_name: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Compute statistics for a Vertex Map tag on a polygon mesh.

    Returns: vertex count, min/max/sum/mean weight, painted_count (weight > 0),
    zero/full counts, and a 10-bin histogram of the weight distribution.

    Args:
      target: object name; defaults to active selection.
      vmap_name: specific vertex map name; defaults to first vertex map tag found.

    Useful for verifying a painted hole map has the expected coverage before
    using it as a Field source or driving a polygon selection.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "vertex_map_stats"}
        if target is not None:
            command["target"] = target
        if vmap_name is not None:
            command["vmap_name"] = vmap_name
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "vertex_map_stats")


@mcp.tool()
async def vertex_map_threshold_to_polygon_selection(
    target: Optional[str] = None,
    vmap_name: Optional[str] = None,
    threshold: float = 0.5,
    require_all: bool = False,
    selection_name: str = "vmap_threshold",
    replace_existing: bool = True,
    ctx: Context = None,
) -> str:
    """Convert a vertex map weight threshold into a polygon selection tag.

    For each polygon, evaluates whether the polygon's vertices meet the
    threshold and selects the polygon if so:

      - require_all=False (default): polygon is selected if ANY vertex
        has weight >= threshold (broad — picks edge polygons where painting
        bleeds onto a single corner)
      - require_all=True: polygon is selected only if ALL vertices have
        weight >= threshold (conservative — only fully-inside polygons)

    Result is a polygon selection tag named `selection_name` on the target.
    From there you can drive any operation that takes a polygon selection
    (Boolean, Inner Extrude + Delete for hole-cutting, Field Layer, etc.).

    Args:
      target: object name; defaults to active selection.
      vmap_name: specific vertex map name; defaults to first found.
      threshold: weight cutoff in [0, 1]. Default 0.5.
      require_all: see above. Default False (any-vertex match).
      selection_name: name for the resulting polygon selection tag.
      replace_existing: if a selection tag with this name exists, overwrite it.

    Pipeline example: paint hole regions with a vertex map → run this with
    threshold=0.5 → use `run_modeling_command(op='delete', mode='polygons')`
    to cut the hole pattern in real geometry.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "vertex_map_threshold_to_polygon_selection",
            "threshold": threshold,
            "require_all": require_all,
            "selection_name": selection_name,
            "replace_existing": replace_existing,
        }
        if target is not None:
            command["target"] = target
        if vmap_name is not None:
            command["vmap_name"] = vmap_name
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "vertex_map_threshold_to_polygon_selection")


@mcp.tool()
async def uv_layout_stats(
    target: Optional[str] = None,
    quantize: int = 1000000,
    ctx: Context = None,
) -> str:
    """Compute layout stats for a polygon mesh's UV tag.

    Walks the UV layout, detects islands via union-find on shared UV
    vertex positions, measures per-island bbox + area in both UV space
    and 3D space (giving a distortion ratio = sqrt(world_area/uv_area)
    which is the local texel-density factor), and detects shell overlap
    via 32x32 UV grid binning + 3D-spread heuristic.

    Returns:
      - global UV bbox
      - island_count
      - per-island stats: polygon_count, uv_bbox, uv_area, world_area,
        distortion (local texel density factor)
      - overlap_grid: cells with high 3D-spread (sign of mirrored or
        stacked UV shells, which break naive UV→3D pipelines)

    Use cases:
      - sanity check before running UV-flatten / hole-cut workflows
      - identify oversized or undersized UV islands (texel density
        comparison)
      - detect mirrored/overlapping shells before they cause issues
      - estimate proper texture resolution per island
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "uv_layout_stats",
            "quantize": quantize,
        }
        if target is not None:
            command["target"] = target
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "uv_layout_stats")


@mcp.tool()
async def sample_bitmap_at_uv(
    bitmap_path: str,
    target: Optional[str] = None,
    vmap_name: str = "baked",
    channel: str = "luminance",
    invert: bool = False,
    gamma: float = 1.0,
    v_flip: bool = True,
    ctx: Context = None,
) -> str:
    """Sample a bitmap at every vertex's UV coord, write to a vertex map.

    The "Photoshop mask → procedural mesh attribute" bridge. Lets you paint
    a black/white mask (or any image) externally, save as PNG, and bake
    the per-pixel values into a vertex map on the mesh — at which point
    every other procedural tool that takes a vertex map (Field source,
    threshold-to-polygon-selection, MoGraph effector, etc.) can drive off it.

    For each vertex, the per-vertex UV is computed by averaging the UVs
    from all polygons referencing it (handles UV seams gracefully — verts
    on a seam end up with one of their UV positions, doesn't matter which
    since both correspond to the same 3D point).

    Args:
      bitmap_path: file path of source bitmap (any format C4D supports).
      target: object name; defaults to active selection.
      vmap_name: name of the output vertex map (created if not present,
                 overwritten if it is). Default 'baked'.
      channel: which bitmap channel to read.
        - 'luminance' (default): perceptual gray (0.299R + 0.587G + 0.114B)
        - 'red' / 'green' / 'blue' / 'alpha'
        - 'average': simple (R+G+B)/3
      invert: if True, output 1.0 - value.
      gamma: apply pow(value, gamma) to each sample. Default 1.0 (off).
      v_flip: if True (default), flip V axis to account for image-Y vs UV-Y
              direction mismatch. Set False if your bitmap was authored
              UV-up.

    Returns: counts (sampled, skipped), min/max/mean of resulting weights,
    plus echo of the bitmap params used.

    Pipeline example: paint hole regions in Photoshop / Painter as a B/W
    mask → save as hole_mask.png → sample_bitmap_at_uv(hole_mask.png) →
    vertex_map_threshold_to_polygon_selection(threshold=0.5) →
    run_modeling_command(op='delete', mode='polygons').
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "sample_bitmap_at_uv",
            "bitmap_path": bitmap_path,
            "vmap_name": vmap_name,
            "channel": channel,
            "invert": invert,
            "gamma": gamma,
            "v_flip": v_flip,
        }
        if target is not None:
            command["target"] = target
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "sample_bitmap_at_uv")


@mcp.tool()
async def uv_islands_to_objects(
    target: Optional[str] = None,
    quantize: int = 1000000,
    name_prefix: Optional[str] = None,
    parent_name: Optional[str] = None,
    min_polygons: int = 1,
    ctx: Context = None,
) -> str:
    """Split a polygon mesh into separate objects, one per UV island.

    Output objects retain their original 3D positions (NOT flattened).
    UV tag and any vertex map tags are carried over with weights remapped.
    Each island's polygons become an independent polygon mesh.

    Args:
      target: object name; defaults to active selection.
      quantize: UV-position dedup precision (default 6 decimal places).
      name_prefix: prefix for new objects. Default = source name.
                   Output named "{prefix}_island_00", "_island_01", ...
                   (sorted largest-first).
      parent_name: optional Null name to parent results under.
                   Created if not present at top level.
      min_polygons: skip islands smaller than this. Default 1.

    Returns: per-island stats + naming + parent.

    Use cases:
      - process each panel of a chair / shoe / car independently
        (different hole density per panel, different material)
      - export each piece separately for fab / 3D print
      - apply per-island procedural workflows (Spikr scatter at
        different rates, MoGraph effectors per region, etc.)
      - debugging UV layout — see each island as a separate object
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "uv_islands_to_objects",
            "quantize": quantize,
            "min_polygons": min_polygons,
        }
        if target is not None:
            command["target"] = target
        if name_prefix is not None:
            command["name_prefix"] = name_prefix
        if parent_name is not None:
            command["parent_name"] = parent_name
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "uv_islands_to_objects")


@mcp.tool()
async def get_viewport_state(ctx: Context = None) -> str:
    """Return the active viewport's state: dimensions, frame rect, active camera matrix,
    projection mode, and active renderer. Useful for debugging plugin viewport draws."""
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "get_viewport_state"}
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "get_viewport_state")


@mcp.tool()
async def list_render_engines(ctx: Context = None) -> str:
    """List all registered render engines (VideoPost plugins) and flag the active one.

    Critical for verifying that custom viewport-renderer plugins (custom GLSL
    shaders, third-party engines, etc.) registered correctly. Also surfaces
    Octane, Redshift, Arnold, Standard, etc. with their plugin IDs.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "list_render_engines"}
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "list_render_engines")


@mcp.tool()
async def get_active_renderer(ctx: Context = None) -> str:
    """Return the active document's renderer id, name, and resolution."""
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "get_active_renderer"}
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "get_active_renderer")


# ---- Tier 5: Octane workflow ----


@mcp.tool()
async def tap_octane_log(
    lines: int = 200,
    contains: Optional[str] = None,
    level: Optional[str] = None,
    log_path: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Read recent lines from the C4D Octane plugin log file (`c4doctanelog.txt`).

    SERVER-SIDE TOOL — does not require a live C4D connection. Reads the file
    directly from `%APPDATA%/Maxon/Maxon Cinema 4D <VER>_<HASH>/c4doctanelog.txt`.
    Auto-discovers the path by globbing if log_path is not provided; if multiple
    C4D versions are installed, prefers the highest-numbered (newest).

    Args:
        lines: how many trailing lines to return (default 200, max 5000)
        contains: case-insensitive substring filter on message content
        level: filter by log level — 'Info' | 'Warning' | 'Error'
        log_path: explicit path override (skip auto-discovery)
    """
    import glob
    import os

    candidates: List[str] = []
    if log_path:
        candidates = [_native_to_wsl(log_path)]
    else:
        # Try common Windows locations
        appdata_globs = [
            "/mnt/c/Users/*/AppData/Roaming/Maxon/Maxon Cinema 4D */c4doctanelog.txt",
            "/mnt/c/Users/*/AppData/Roaming/Maxon/Maxon Cinema 4D *_*/c4doctanelog.txt",
        ]
        for g in appdata_globs:
            for path in glob.glob(g):
                # Skip _BACKUP variants
                if "_BACKUP" in path or "_backup" in path:
                    continue
                candidates.append(path)
        # Sort by version preference (2026 > 2025 > R22 ...) — heuristic: take last numeric token
        def _version_key(p: str) -> int:
            try:
                folder = os.path.basename(os.path.dirname(p))
                # e.g. "Maxon Cinema 4D 2026_1ABCDC12"
                parts = folder.split()
                for tok in reversed(parts):
                    head = tok.split("_")[0]
                    if head.isdigit():
                        return int(head)
                    if head.startswith("R") and head[1:].isdigit():
                        return int(head[1:])
            except Exception:
                pass
            return 0
        candidates.sort(key=_version_key, reverse=True)

    if not candidates:
        return (
            "❌ No `c4doctanelog.txt` found via glob. "
            "Either C4D Octane plugin isn't installed/run yet, or pass `log_path` explicitly."
        )

    chosen = candidates[0]
    if not os.path.isfile(chosen):
        return f"❌ Log file not found: {chosen}"

    try:
        # Read efficiently — large logs would be slow with full read; cap at 1 MB tail
        size = os.path.getsize(chosen)
        with open(chosen, "rb") as f:
            if size > 1024 * 1024:
                f.seek(-1024 * 1024, 2)
                _ = f.readline()  # skip partial
            raw = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"❌ Failed to read log file `{chosen}`: {e}"

    all_lines = raw.splitlines()
    filtered = []
    contains_lc = contains.lower() if contains else None
    level_filter = level.lower() if level else None
    for ln in all_lines:
        if contains_lc and contains_lc not in ln.lower():
            continue
        if level_filter:
            # Lines look like "[2026-04-20 16:07:30][Info][Core]..."
            try:
                bracketed = ln.split("]")[1].lstrip("[")
                if bracketed.lower() != level_filter:
                    continue
            except Exception:
                continue
        filtered.append(ln)

    cap = min(int(lines), 5000)
    tail = filtered[-cap:]

    out = [
        f"✅ **{len(tail)}** lines from `{chosen}`",
        f"  - Total lines in file: {len(all_lines)}, after filter: {len(filtered)}",
    ]
    if contains:
        out.append(f"  - **Filter contains**: `{contains}`")
    if level:
        out.append(f"  - **Filter level**: `{level}`")
    if len(candidates) > 1:
        out.append(f"  - _Found {len(candidates)} candidate logs; chose newest._")
    out.append("")
    out.append("```")
    out.extend(tail)
    out.append("```")
    return "\n".join(out)


@mcp.tool()
async def find_command_by_name(
    name_contains: str,
    max_results: int = 50,
    ctx: Context = None,
) -> str:
    """Find C4D commands (CallCommand-able plugin commands) matching a name substring.

    Use this to discover the integer command ID for things like 'Octane Area Light',
    'Redshift Sun', 'Alembic Export', etc. Once you have the ID, pass it to
    `create_via_command` to actually invoke it.

    Example: name_contains="area light" → returns ids for 'Light' (Octane Area), 'Area Light' (C4D), etc.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "list_installed_plugins",
            "plugin_type": "command",
            "name_contains": name_contains,
        }
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "list_installed_plugins")


@mcp.tool()
async def enumerate_octane_plugins(
    name_contains: Optional[str] = None,
    plugin_type: str = "all",
    ctx: Context = None,
) -> str:
    """List all loaded Octane-related plugins (objects, commands, shaders, materials, tags, videoposts).

    Octane registers plugins across many types. This wraps `list_installed_plugins`
    with a name filter for 'octane' (and 'oct' for short-name variants). Pass
    `name_contains` to further narrow (e.g. 'image texture', 'area light',
    'directional').
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        # We do two passes since the plugin's name_contains is a single substring filter
        # First pass: name_contains="octane"
        results_combined: Dict[int, Dict[str, Any]] = {}
        for needle in ["octane", "oct"]:
            command: Dict[str, Any] = {
                "command": "list_installed_plugins",
                "plugin_type": plugin_type,
                "name_contains": needle,
            }
            response = send_to_c4d(connection, command)
            for p in response.get("plugins", []):
                results_combined[p["id"]] = p

        # Optional further narrowing
        plugins = list(results_combined.values())
        if name_contains:
            nc = name_contains.lower()
            plugins = [p for p in plugins if nc in p.get("name", "").lower()]
        plugins.sort(key=lambda p: (p.get("type_name", ""), p.get("name", "")))

        if not plugins:
            return "✅ No Octane plugins matched."

        # Build readable output
        lines = [f"✅ **{len(plugins)}** Octane plugin(s) matched"]
        if name_contains:
            lines.append(f"  - **Filter**: `{name_contains}`")
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for p in plugins:
            by_type.setdefault(p.get("type_name", "?"), []).append(p)
        for tname in sorted(by_type.keys()):
            entries = by_type[tname]
            lines.append(f"\n  **{tname}** ({len(entries)})")
            for p in entries:
                lines.append(f"  - id=`{p.get('id')}`  {p.get('name', '?')}")
        return "\n".join(lines)


@mcp.tool()
async def dump_material_graph(
    material_name: Optional[str] = None,
    object_name: Optional[str] = None,
    guid: Optional[str] = None,
    max_depth: int = 20,
    ctx: Context = None,
) -> str:
    """Walk and dump the shader/node graph of a material (or any shader-bearing object/tag).

    Provide `material_name` to dump a specific material from the doc.GetMaterials() list,
    OR `object_name`/`guid` to dump shaders attached to that object (covers Octane Area
    Lights' emission-shader inputs, OctaneTag shader graphs on objects, etc.).

    Returns a tree of {name, type_id, guid, params, children}. Each node's params
    include the first ~30 description parameters with their current values — usually
    enough to identify what an Octane node does and where its inputs live.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "dump_material_graph", "max_depth": max_depth}
        if material_name:
            command["material_name"] = material_name
        if object_name:
            command["object_name"] = object_name
        if guid:
            command["guid"] = guid
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "dump_material_graph")


@mcp.tool()
async def create_via_command(
    command_id: int,
    object_name: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Execute c4d.CallCommand(command_id) and return the new active object.

    Use this for plugin objects that can't be instantiated via BaseObject.Alloc() —
    notably Octane lights/cameras/postprocess, which require the CallCommand pattern.
    Example: command_id=1033864 creates an Octane Area Light.

    Pass `object_name` to rename the new object after creation.
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {"command": "create_via_command", "command_id": command_id}
        if object_name:
            command["object_name"] = object_name
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "create_via_command")


@mcp.tool()
async def link_shader_to_parameter(
    target_name: Optional[str] = None,
    target_guid: Optional[str] = None,
    parameter_path: Optional[List[int]] = None,
    parameter_name: Optional[str] = None,
    shader_plugin_id: int = 0,
    shader_params: Optional[Dict[str, Any]] = None,
    ctx: Context = None,
) -> str:
    """Create a shader, optionally configure it, and link it as the value of a parameter
    on a target object/material/light.

    Killer helper for any "create shader, configure, link to parameter" workflow.
    Once you've discovered the Octane Image Texture shader plugin ID and the Area
    Light's texture parameter ID (via enumerate_octane_plugins + enumerate_descids),
    call this with shader_params={"file": "/path/to/canvas.png", ...} to wire it up
    in one shot.

    Args:
        target_name / target_guid: the receiver (object, material, light)
        parameter_path (list[int]): explicit DescID path on target; OR
        parameter_name (str): substring match on parameter name (auto-resolves)
        shader_plugin_id: plugin ID of the shader to create (e.g. Octane ImageTexture)
        shader_params: dict of {param_id_or_name_substring: value} to set on the new shader
                       Examples:
                         {"file": "/tmp/x.png"}        — fuzzy match by name
                         {1003: "/tmp/x.png"}          — explicit param id
    """
    async with c4d_connection_context() as connection:
        if not connection.connected:
            return "❌ Not connected to Cinema 4D"
        command: Dict[str, Any] = {
            "command": "link_shader_to_parameter",
            "shader_plugin_id": shader_plugin_id,
        }
        if target_name:
            command["target_name"] = target_name
        if target_guid:
            command["target_guid"] = target_guid
        if parameter_path:
            command["parameter_path"] = parameter_path
        if parameter_name:
            command["parameter_name"] = parameter_name
        if shader_params:
            command["shader_params"] = shader_params
        response = send_to_c4d(connection, command)
        return format_c4d_response(response, "link_shader_to_parameter")


def _wsl_to_native(path: str) -> str:
    """Convert /mnt/c/... to C:\\... if running on WSL; otherwise return as-is."""
    import os
    if os.name == "posix" and path.startswith("/mnt/") and len(path) >= 7 and path[6] == "/":
        drive = path[5].upper()
        rest = path[7:].replace("/", "\\")
        return f"{drive}:\\{rest}"
    return path


def _native_to_wsl(path: str) -> str:
    """Convert C:\\... to /mnt/c/... if running on WSL; otherwise return as-is."""
    import os
    if os.name == "posix" and len(path) >= 3 and path[1] == ":" and path[2] in ("\\", "/"):
        drive = path[0].lower()
        rest = path[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return path


@mcp.tool()
async def install_plugin(
    source_dir: str,
    install_dir: Optional[str] = None,
    plugin_name: Optional[str] = None,
    include_res: bool = True,
    overwrite: bool = True,
    ctx: Context = None,
) -> str:
    """Copy a built C4D plugin (.xdl64 + res/) into Cinema 4D's plugin directory.

    SERVER-SIDE TOOL — works without a live C4D connection (file operation only).

    Args:
        source_dir: directory containing the built plugin. e.g.
                    `<sdk_root>/_build_v143/bin/Release/plugins/<PluginName>`.
                    Accepts WSL or Windows-native paths.
        install_dir: target directory. Defaults to env var C4D_PLUGINS_DIR if set,
                     otherwise `/mnt/c/Program Files/Maxon Cinema 4D 2026/plugins/<plugin_name>`.
        plugin_name: explicit plugin name. If omitted, derived from source_dir's basename.
        include_res: also copy the `res/` subdirectory (defaults True).
        overwrite: overwrite existing files in install_dir (defaults True).

    Returns a summary of files copied. **Note:** C4D must be restarted to load
    a freshly-installed compiled plugin — there's no hot-reload for .xdl64.
    """
    import os
    import shutil

    src = _native_to_wsl(source_dir)
    if not os.path.isdir(src):
        return f"❌ Source directory does not exist: {src}"

    name = plugin_name or os.path.basename(os.path.normpath(src))
    if not name:
        return "❌ Could not derive plugin_name from source_dir; pass plugin_name explicitly."

    if install_dir is None:
        env_plugins_dir = os.environ.get("C4D_PLUGINS_DIR")
        if env_plugins_dir:
            install_dir = os.path.join(env_plugins_dir, name)
        else:
            install_dir = f"/mnt/c/Program Files/Maxon Cinema 4D 2026/plugins/{name}"
    install_dir = _native_to_wsl(install_dir)

    try:
        os.makedirs(install_dir, exist_ok=True)
    except Exception as e:
        return f"❌ Failed to create install_dir `{install_dir}`: {e}"

    copied = []
    skipped = []

    # Copy .xdl64 binary (and pdb if present)
    for fname in os.listdir(src):
        full = os.path.join(src, fname)
        if not os.path.isfile(full):
            continue
        if not (fname.endswith(".xdl64") or fname.endswith(".pdb") or fname.endswith(".dylib")):
            continue
        dest = os.path.join(install_dir, fname)
        if os.path.exists(dest) and not overwrite:
            skipped.append(fname)
            continue
        try:
            shutil.copy2(full, dest)
            copied.append(fname)
        except Exception as e:
            return f"❌ Failed to copy {fname}: {e}"

    # Copy res/ subdirectory (recursive). Some builds have res/ as a sibling of source_dir
    # (i.e. the SDK's plugins/<name>/res/, not _build/<name>/res/) — try both.
    if include_res:
        res_candidates = [
            os.path.join(src, "res"),
            os.path.join(os.path.dirname(src), "res"),  # one level up
        ]
        # Also try plugins/<name>/res/ in the SDK root if we can guess it
        for res_src in res_candidates:
            if os.path.isdir(res_src):
                res_dest = os.path.join(install_dir, "res")
                try:
                    if os.path.isdir(res_dest):
                        if overwrite:
                            shutil.rmtree(res_dest)
                        else:
                            skipped.append("res/ (exists)")
                            break
                    shutil.copytree(res_src, res_dest)
                    copied.append(f"res/ (from {res_src})")
                except Exception as e:
                    return f"❌ Failed to copy res/: {e}"
                break
        else:
            skipped.append("res/ (not found in source or sibling dir)")

    if not copied:
        return f"⚠️ Nothing copied from `{src}` to `{install_dir}` (skipped: {skipped})"

    lines = [f"✅ Installed **{name}** to `{install_dir}`"]
    for f in copied:
        lines.append(f"  - copied: {f}")
    for f in skipped:
        lines.append(f"  - skipped: {f}")
    lines.append("")
    lines.append("⚠️  **Restart Cinema 4D** to load the new plugin (no hot-reload for compiled plugins).")
    return "\n".join(lines)


@mcp.tool()
async def build_and_install_plugin(
    target: str,
    sdk_root: Optional[str] = None,
    config: str = "Release",
    build_dir: str = "_build_v143",
    install_dir: Optional[str] = None,
    cmake_cmd: Optional[str] = None,
    deploy: bool = True,
    ctx: Context = None,
) -> str:
    """Build a C4D plugin via CMake and (optionally) deploy it to the C4D plugins folder.

    SERVER-SIDE TOOL — runs subprocess on the host. Wraps the standard C4D SDK
    workflow:
        cmake --build <build_dir> --config Release --target <target>
        cp <build_dir>/bin/Release/plugins/<target>/<target>.xdl64 <install_dir>/
        cp -r plugins/<target>/res <install_dir>/

    Args:
        target: CMake target name (e.g. plugin module name)
        sdk_root: path to the C4D SDK root containing CMakeLists.txt.
                  Defaults to env var C4D_SDK_ROOT if set, else cwd.
        config: "Release" (default) or "Debug"
        build_dir: name of the build directory under sdk_root (default "_build_v143")
        install_dir: where to deploy. Defaults to env var C4D_PLUGINS_DIR + target,
                     else `/mnt/c/Program Files/Maxon Cinema 4D 2026/plugins/<target>`.
        cmake_cmd: explicit cmake binary path. Defaults to trying "cmake.exe" then "cmake".
        deploy: if True (default), also copy the built artifacts to install_dir after build.

    Returns build log + install summary. Does NOT restart C4D.
    """
    import os
    import shutil
    import subprocess

    if sdk_root is None:
        sdk_root = os.environ.get("C4D_SDK_ROOT") or os.getcwd()
    sdk_root = _native_to_wsl(sdk_root)
    if not os.path.isdir(sdk_root):
        return f"❌ sdk_root does not exist: {sdk_root}"

    # Resolve cmake binary
    candidates = [cmake_cmd] if cmake_cmd else ["cmake.exe", "cmake"]
    cmake_bin = None
    for c in candidates:
        if not c:
            continue
        if shutil.which(c):
            cmake_bin = c
            break
    if not cmake_bin:
        return f"❌ Could not find cmake. Tried: {candidates}. Install cmake or pass `cmake_cmd` explicitly."

    build_path = os.path.join(sdk_root, build_dir)
    if not os.path.isdir(build_path):
        return (
            f"❌ Build directory does not exist: {build_path}\n"
            f"Run `cmake --preset windows_vs2022_v143` from {sdk_root} first."
        )

    cmd = [cmake_bin, "--build", build_path, "--config", config, "--target", target]
    try:
        proc = subprocess.run(
            cmd,
            cwd=sdk_root,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes max for a build
        )
    except subprocess.TimeoutExpired:
        return f"❌ Build timed out after 10 minutes: {' '.join(cmd)}"
    except Exception as e:
        return f"❌ Build invocation failed: {e}\nCommand: {' '.join(cmd)}"

    # Tail of build output
    stdout_tail = "\n".join((proc.stdout or "").splitlines()[-40:])
    stderr_tail = "\n".join((proc.stderr or "").splitlines()[-20:])

    if proc.returncode != 0:
        return (
            f"❌ Build failed (exit {proc.returncode})\n"
            f"Command: {' '.join(cmd)}\n\n"
            f"--- stdout (tail) ---\n{stdout_tail}\n\n"
            f"--- stderr (tail) ---\n{stderr_tail}"
        )

    lines = [
        f"✅ Build succeeded: **{target}** ({config})",
        f"  - cmake: `{cmake_bin}`",
        f"  - build_dir: `{build_path}`",
        "",
        "--- build output (tail) ---",
        f"```\n{stdout_tail or '(no stdout)'}\n```",
    ]

    if not deploy:
        lines.append("\n_(deploy=False — not installing)_")
        return "\n".join(lines)

    # Deploy
    src_dir = os.path.join(build_path, "bin", config, "plugins", target)
    if not os.path.isdir(src_dir):
        lines.append(f"\n❌ Build artifacts not found at expected path: `{src_dir}`")
        return "\n".join(lines)

    target_install = install_dir or f"/mnt/c/Program Files/Maxon Cinema 4D 2026/plugins/{target}"
    target_install = _native_to_wsl(target_install)

    install_result = await install_plugin(
        source_dir=src_dir,
        install_dir=target_install,
        plugin_name=target,
        include_res=True,
        overwrite=True,
    )
    lines.append("")
    lines.append("--- install ---")
    lines.append(install_result)
    return "\n".join(lines)


@mcp.resource("c4d://primitives")
def get_primitives_info() -> str:
    """Get information about available Cinema 4D primitives."""
    return """
# Cinema 4D Primitive Objects

## Cube
- **Parameters**: size, segments

## Sphere
- **Parameters**: radius, segments

## Cylinder
- **Parameters**: radius, height, segments

## Cone
- **Parameters**: radius, height, segments

## Plane
- **Parameters**: width, height, segments

## Torus
- **Parameters**: outer radius, inner radius, segments

## Pyramid
- **Parameters**: width, height, depth

## Platonic
- **Parameters**: radius, type (tetrahedron, hexahedron, octahedron, dodecahedron, icosahedron)
"""


@mcp.resource("c4d://material_types")
def get_material_types() -> str:
    """Get information about available Cinema 4D material types and their properties."""
    return """
# Cinema 4D Material Types

## Standard Material
- **Color**: Base diffuse color
- **Specular**: Highlight color and intensity
- **Reflection**: Surface reflectivity
- **Transparency**: Surface transparency
- **Bump**: Surface bumpiness or displacement

## Physical Material
- **Base Color**: Main surface color
- **Specular**: Surface glossiness and reflectivity
- **Roughness**: Surface irregularity
- **Metallic**: Metal-like properties
- **Transparency**: Light transmission properties
- **Emission**: Self-illumination properties
- **Normal**: Surface detail without geometry
- **Displacement**: Surface geometry modification
"""


@mcp.resource("c4d://status")
def get_connection_status() -> str:
    """Get the current connection status to Cinema 4D."""
    is_connected = check_c4d_connection(C4D_HOST, C4D_PORT)
    status = (
        "✅ Connected to Cinema 4D" if is_connected else "❌ Not connected to Cinema 4D"
    )

    return f"""
# Cinema 4D Connection Status
{status}

## Connection Details
- **Host**: {C4D_HOST}
- **Port**: {C4D_PORT}
"""


mcp_app = mcp
