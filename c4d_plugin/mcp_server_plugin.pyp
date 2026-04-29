"""
Cinema 4D MCP Server Plugin
Updated for Cinema 4D R2025/2026 compatibility
Version 0.2.0 - auth token gate + correctness fixes (load_scene args,
                _timeout honoring, render_preview args, __main__ shim)
"""

import c4d
from c4d import gui
import socket
import threading
import json
import time
import math
import queue
import os
import sys
import base64
import traceback
from collections import deque

PLUGIN_ID = 1057843  # Unique plugin ID for SpecialEventAdd

# Transport guardrail — reject command payloads above this size to avoid
# OOMing the plugin if a client misbehaves (e.g. dumping a multi-GB scene
# into a single execute_python script). 5MB is generous for any reasonable
# command including base64 bitmaps.
MCP_MAX_COMMAND_BYTES = 5 * 1024 * 1024

# ============================================================
# MCP extensions — module-level console log buffer
# Captures both plugin self.log() output AND C4D's c4d.GePrint() output
# (via runtime monkey-patch) into a single timestamped ring buffer.
# Read it back via the get_console_log tool.
# ============================================================

MCP_LOG_BUFFER_MAX = 10000
_mcp_log_buffer = deque(maxlen=MCP_LOG_BUFFER_MAX)
_mcp_log_lock = threading.Lock()
_mcp_geprint_original = None
_mcp_geprint_patched = False


def mcp_log_append(source, message):
    """Append a single log entry to the ring buffer. Thread-safe."""
    try:
        ts = time.time()
        entry = (ts, str(source), str(message))
        with _mcp_log_lock:
            _mcp_log_buffer.append(entry)
    except Exception:
        pass  # never let logging crash the plugin


def mcp_log_get(limit=None, since_ts=None, source_filter=None, contains=None):
    """Return a list of {ts, iso, source, message} dicts from the buffer, newest last."""
    with _mcp_log_lock:
        snapshot = list(_mcp_log_buffer)
    out = []
    for ts, src, msg in snapshot:
        if since_ts is not None and ts <= since_ts:
            continue
        if source_filter and src != source_filter:
            continue
        if contains and contains.lower() not in msg.lower():
            continue
        out.append({
            "ts": ts,
            "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) + f".{int((ts % 1) * 1000):03d}",
            "source": src,
            "message": msg,
        })
    if limit is not None and limit > 0:
        out = out[-int(limit):]
    return out


def mcp_log_clear():
    """Empty the ring buffer."""
    with _mcp_log_lock:
        _mcp_log_buffer.clear()


def mcp_install_geprint_hook():
    """Install a runtime hook on c4d.GePrint that mirrors output to the buffer.
    Idempotent — calling twice is a no-op."""
    global _mcp_geprint_original, _mcp_geprint_patched
    if _mcp_geprint_patched:
        return
    try:
        _mcp_geprint_original = c4d.GePrint

        def _patched_geprint(*args, **kwargs):
            try:
                msg = " ".join(str(a) for a in args)
                mcp_log_append("c4d.GePrint", msg)
            except Exception:
                pass
            return _mcp_geprint_original(*args, **kwargs)

        c4d.GePrint = _patched_geprint
        _mcp_geprint_patched = True
        mcp_log_append("mcp", "c4d.GePrint hook installed")
    except Exception as e:
        mcp_log_append("mcp", f"Failed to install GePrint hook: {e}")


def mcp_uninstall_geprint_hook():
    """Restore the original c4d.GePrint. Idempotent."""
    global _mcp_geprint_original, _mcp_geprint_patched
    if not _mcp_geprint_patched:
        return
    try:
        if _mcp_geprint_original is not None:
            c4d.GePrint = _mcp_geprint_original
        _mcp_geprint_patched = False
        mcp_log_append("mcp", "c4d.GePrint hook removed")
    except Exception:
        pass

# Check Cinema 4D version and log compatibility info
C4D_VERSION = c4d.GetC4DVersion()
C4D_VERSION_MAJOR = C4D_VERSION // 1000
C4D_VERSION_MINOR = (C4D_VERSION // 100) % 10
print(f"[C4D MCP] Running on Cinema 4D R{C4D_VERSION_MAJOR}{C4D_VERSION_MINOR}")
print(f"[C4D MCP] Python version: {sys.version}")

# Warn if using unsupported version
if C4D_VERSION_MAJOR < 20:
    print(
        "[C4D MCP] ## Warning ##: This plugin is in development for Cinema 4D 2025 or later with plans to futher support earlier versions. Some features may not work correctly."
    )


class C4DSocketServer(threading.Thread):
    """Socket Server running in a background thread, sending logs & status via queue."""

    def __init__(self, msg_queue, host="0.0.0.0", port=5555):
        # bind 0.0.0.0 (all interfaces) by default so WSL2 clients
        # can reach the socket from a separate network namespace. Original
        # upstream default was "127.0.0.1". Pass host="127.0.0.1" explicitly
        # to restore localhost-only behavior.
        super(C4DSocketServer, self).__init__()
        self.host = host
        self.port = port
        self.socket = None
        # Auth token: if MCP_AUTH_TOKEN is set in the environment, every command
        # must include a matching "auth_token" field. If unset, all commands are
        # accepted (preserves existing single-user-localhost workflow) but a
        # warning is logged at startup.
        self.auth_token = os.environ.get("MCP_AUTH_TOKEN") or None
        # Safe mode: if MCP_SAFE_MODE is set (any truthy value), commands not
        # in _SAFE_COMMANDS are rejected. Lets a user expose the bridge to
        # less-trusted contexts (review tools, screenshot bots, CI inspection)
        # without enabling Python execution / scene mutation. Default is OFF
        # to preserve existing single-user workflow.
        _safe = os.environ.get("MCP_SAFE_MODE", "").strip().lower()
        self.safe_mode = _safe in ("1", "true", "yes", "on")
        self.running = False
        self.msg_queue = msg_queue  # Queue to communicate with UI
        self.daemon = True  # Ensures cleanup on shutdown

        # --- ADDED FOR CONTEXT AWARENESS ---
        self._object_name_registry = (
            {}
        )  # OLDs? --Maps GUID -> requested_name AND requested_name -> GUID (less robust)

        self._name_to_guid_registry = (
            {}
        )  # Maps requested_name.lower() or actual_name.lower() -> guid
        self._guid_to_name_registry = (
            {}
        )  # Maps guid -> {'requested_name': str, 'actual_name': str}

    def log(self, message):
        """Send log messages to UI via queue and trigger an event.
        Also mirrored to the MCP console buffer for MCP get_console_log tool."""
        self.msg_queue.put(("LOG", message))
        c4d.SpecialEventAdd(PLUGIN_ID)  # Notify UI thread
        mcp_log_append("plugin", message)

    def update_status(self, status):
        """Update status via queue and trigger an event."""
        self.msg_queue.put(("STATUS", status))
        c4d.SpecialEventAdd(PLUGIN_ID)

    def execute_on_main_thread(self, func, args=None, kwargs=None, _timeout=None):
        """Execute a function on the main thread using a thread-safe queue and special event.

        Since CallMainThread is not available in the Python SDK (R2025), we use
        a thread-safe approach by queuing the function and triggering it via SpecialEventAdd.

        Args:
            func: The function to execute on the main thread
            *args: Arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function
                      Special keyword '_timeout': Override default timeout (in seconds)

        Returns:
            The result of executing the function on the main thread
        """
        args = args or ()
        kwargs = kwargs or {}

        # Use the explicit `_timeout` parameter (if caller passed one); fall
        # back to a stray "_timeout" key inside `kwargs` for callers that
        # accidentally pack it there. Prior code only consulted kwargs and
        # silently dropped the explicit param.
        timeout = _timeout if _timeout is not None else kwargs.pop("_timeout", None)

        # Set appropriate timeout based on operation type
        if timeout is None:
            # Use different default timeouts based on the function name
            func_name = func.__name__ if hasattr(func, "__name__") else str(func)

            if "render" in func_name.lower():
                timeout = 120  # 2 minutes for rendering
                self.log(f"[C4D] Using extended timeout (120s) for rendering operation")
            elif "save" in func_name.lower():
                timeout = 60  # 1 minute for saving
                self.log(f"[C4D] Using extended timeout (60s) for save operation")
            elif "field" in func_name.lower():
                timeout = 30  # 30 seconds for field operations
                self.log(f"[C4D] Using extended timeout (30s) for field operation")
            else:
                timeout = 60  # Default timeout increased to 60 seconds

        self.log(f"[C4D] Main thread execution will timeout after {timeout}s")

        # Create a thread-safe container for the result
        result_container = {"result": None, "done": False}

        # Define a wrapper that will be executed on the main thread
        def main_thread_exec():
            try:
                self.log(
                    f"[C4D] Starting main thread execution of {func.__name__ if hasattr(func, '__name__') else 'function'}"
                )
                start_time = time.time()
                result_container["result"] = func(*args, **kwargs)
                execution_time = time.time() - start_time
                self.log(
                    f"[C4D] Main thread execution completed in {execution_time:.2f}s"
                )
            except Exception as e:
                self.log(
                    f"[**ERROR**] Error executing function on main thread: {str(e)}"
                )
                result_container["result"] = {"error": str(e)}
            finally:
                result_container["done"] = True
            return True

        # Queue the request and signal the main thread
        self.log("[C4D] Queueing function for main thread execution")
        self.msg_queue.put(("EXEC", main_thread_exec))
        c4d.SpecialEventAdd(PLUGIN_ID)  # Notify UI thread

        # Wait for the function to complete (with timeout)
        start_time = time.time()
        poll_interval = 0.01  # Small sleep to prevent CPU overuse
        progress_interval = 1.0  # Log progress every second
        last_progress = 0

        while not result_container["done"]:
            time.sleep(poll_interval)

            # Calculate elapsed time
            elapsed = time.time() - start_time

            # Log progress periodically for long-running operations
            if int(elapsed) > last_progress:
                if elapsed > 5:  # Only start logging after 5 seconds
                    self.log(
                        f"[C4D] Waiting for main thread execution ({elapsed:.1f}s elapsed)"
                    )
                last_progress = int(elapsed)

            # Check for timeout
            if elapsed > timeout:
                self.log(f"[C4D] Main thread execution timed out after {elapsed:.2f}s")
                return {"error": f"Execution on main thread timed out after {timeout}s"}

        # Improved result handling
        if result_container["result"] is None:
            self.log(
                "[C4D] ## Warning ##: Function execution completed but returned None"
            )
            # Return a structured response instead of None
            return {
                "status": "completed",
                "result": None,
                "warning": "Function returned None",
            }

        return result_container["result"]

    def run(self):
        """Main server loop"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)
            self.running = True
            self.update_status("Online")
            self.log(f"[C4D] Server started on {self.host}:{self.port}")
            if self.auth_token:
                self.log(f"[C4D] [auth] MCP_AUTH_TOKEN is set — commands must include auth_token field")
            elif self.host == "0.0.0.0":
                self.log(
                    "[C4D] [auth] WARNING: server bound to 0.0.0.0 with NO auth token. "
                    "Anyone with network access to this machine can issue commands "
                    "(including execute_python). Set MCP_AUTH_TOKEN env var, "
                    "or bind 127.0.0.1, before running on a multi-user / networked host."
                )
            if self.safe_mode:
                self.log(
                    f"[C4D] [safe-mode] MCP_SAFE_MODE active — only read-only commands "
                    f"({len(self._SAFE_COMMANDS)} of {len(self._SUPPORTED_COMMANDS)}) accepted. "
                    f"unset MCP_SAFE_MODE to re-enable scene mutation / Python execution."
                )

            while self.running:
                client, addr = self.socket.accept()
                self.log(f"[C4D] Client connected from {addr}")
                threading.Thread(target=self.handle_client, args=(client,)).start()

        except Exception as e:
            self.log(f"[C4D] Server Error: {str(e)}")
            self.update_status("Offline")
            self.running = False

    def handle_client(self, client):
        """Handle incoming client connections."""
        buffer = ""
        try:
            while self.running:
                data = client.recv(4096)
                if not data:
                    break

                # Add received data to buffer
                buffer += data.decode("utf-8")

                # Process complete messages (separated by newlines)
                while "\n" in buffer:
                    message, buffer = buffer.split("\n", 1)

                    # Bounded payload check — refuse oversize commands before
                    # we even try to parse them.
                    if len(message) > MCP_MAX_COMMAND_BYTES:
                        err = {
                            "error": f"payload too large: {len(message)} bytes "
                                     f"(limit {MCP_MAX_COMMAND_BYTES}). Split into "
                                     f"smaller commands or write to a file and "
                                     f"reference the path.",
                        }
                        client.sendall((json.dumps(err) + "\n").encode("utf-8"))
                        self.log(f"[C4D] [transport] rejected oversize msg ({len(message)} bytes)")
                        continue

                    self.log(f"[C4D] Received: {message}")

                    try:
                        # Parse the command
                        command = json.loads(message)
                        command_type = command.get("command", "")
                        # request_id is an optional client-supplied correlator
                        # (any string). Echoed back on the response so async
                        # clients can pair responses to requests. Has no
                        # semantic effect on the dispatcher.
                        request_id = command.get("request_id")

                        # Auth gate: if MCP_AUTH_TOKEN is set, every command must
                        # carry a matching auth_token field. The 'ping' / capability
                        # commands are intentionally NOT exempt — there's no reason
                        # an unauthenticated peer needs to learn anything about the host.
                        # Constant-time comparison via hmac.compare_digest to defeat
                        # timing-side-channel attacks on the token.
                        if self.auth_token:
                            import hmac
                            client_token = command.get("auth_token") or ""
                            if not hmac.compare_digest(str(client_token), self.auth_token):
                                response = {
                                    "ok": False,
                                    "error": "auth: missing or invalid auth_token. "
                                             "Set MCP_AUTH_TOKEN client-side to match the server."
                                }
                                client.sendall((json.dumps(response) + "\n").encode("utf-8"))
                                self.log(f"[C4D] [auth] rejected command={command_type!r} (bad/missing token)")
                                continue

                        # Safe-mode gate: if MCP_SAFE_MODE is set, refuse any
                        # command not in the SAFE allowlist (mutating ops,
                        # execute_python, file writes, plugin installs, etc.)
                        if self.safe_mode and command_type not in self._SAFE_COMMANDS:
                            response = {
                                "error": f"safe-mode: '{command_type}' is not in the SAFE allowlist. "
                                         f"unset MCP_SAFE_MODE on the server to enable mutation. "
                                         f"safe commands: {sorted(self._SAFE_COMMANDS)}",
                                "rejected_command": command_type,
                                "safe_mode": True,
                            }
                            client.sendall((json.dumps(response) + "\n").encode("utf-8"))
                            self.log(f"[C4D] [safe-mode] rejected unsafe command={command_type!r}")
                            continue

                        # Scene info & execution
                        if command_type == "get_scene_info":
                            response = self.handle_get_scene_info()
                        elif command_type == "list_objects":
                            response = self.handle_list_objects()
                        elif command_type == "group_objects":
                            response = self.handle_group_objects(command)
                        elif command_type == "execute_python":
                            response = self.handle_execute_python(command)
                        elif command_type == "save_scene":
                            response = self.handle_save_scene(command)
                        elif command_type == "load_scene":
                            response = self.handle_load_scene(command)
                        elif command_type == "set_keyframe":
                            response = self.handle_set_keyframe(command)
                        # Object creation & modification
                        elif command_type == "add_primitive":
                            response = self.handle_add_primitive(command)
                        elif command_type == "modify_object":
                            response = self.handle_modify_object(command)
                        elif command_type == "create_abstract_shape":
                            response = self.handle_create_abstract_shape(command)
                        # Materials & shaders
                        elif command_type == "create_material":
                            response = self.handle_create_material(command)
                        elif command_type == "apply_material":
                            response = self.handle_apply_material(command)
                        elif command_type == "apply_shader":
                            response = self.handle_apply_shader(command)
                        elif command_type == "inspect_redshift_materials":
                            response = self.handle_inspect_redshift_materials(command)
                        elif command_type == "validate_redshift_materials":
                            response = self.handle_validate_redshift_materials(command)
                        # Rendering & preview
                        elif command_type == "render_frame":
                            response = self.handle_render_frame(command)
                        elif command_type == "render_preview":
                            response = self.handle_render_preview_base64(
                                frame=int(command.get("frame", 0)),
                                width=int(command.get("width", 640)),
                                height=int(command.get("height", 360)),
                            )
                        elif command_type == "snapshot_scene":
                            response = self.handle_snapshot_scene(command)
                        # Camera & light handling
                        elif command_type == "create_camera":
                            response = self.handle_create_camera(command)
                        elif command_type == "animate_camera":
                            response = self.handle_animate_camera(command)
                        elif command_type == "create_light":
                            response = self.handle_create_light(command)
                        # MoGraph/dynamics
                        elif command_type == "create_mograph_cloner":
                            response = self.handle_create_mograph_cloner(command)
                        elif command_type == "add_effector":
                            response = self.handle_add_effector(command)
                        elif command_type == "apply_mograph_fields":
                            response = self.handle_apply_mograph_fields(command)
                        elif command_type == "create_soft_body":
                            response = self.handle_create_soft_body(command)
                        elif command_type == "apply_dynamics":
                            response = self.handle_apply_dynamics(command)
                        # ----- MCP extensions -----
                        # Introspection (read-only, fast)
                        elif command_type == "enumerate_descids":
                            response = self.handle_enumerate_descids(command)
                        elif command_type == "enumerate_userdata":
                            response = self.handle_enumerate_userdata(command)
                        elif command_type == "find_objects":
                            response = self.handle_find_objects(command)
                        elif command_type == "get_object_info":
                            response = self.handle_get_object_info(command)
                        elif command_type == "dump_object_tree":
                            response = self.handle_dump_object_tree(command)
                        # Console log capture
                        elif command_type == "get_console_log":
                            response = self.handle_get_console_log(command)
                        elif command_type == "clear_console_log":
                            response = self.handle_clear_console_log(command)
                        # Plugin lifecycle
                        elif command_type == "list_installed_plugins":
                            response = self.handle_list_installed_plugins(command)
                        elif command_type == "get_c4d_info":
                            response = self.handle_get_c4d_info(command)
                        elif command_type == "get_capabilities":
                            response = self.handle_get_capabilities(command)
                        elif command_type == "doctor":
                            response = self.handle_doctor(command)
                        elif command_type == "ping":
                            response = self.handle_ping(command)
                        elif command_type == "scene_snapshot":
                            response = self.handle_scene_snapshot(command)
                        elif command_type == "scene_diff":
                            response = self.handle_scene_diff(command)
                        # Viewport / render engine
                        elif command_type == "viewport_screenshot":
                            response = self.handle_viewport_screenshot(command)
                        elif command_type == "viewport_screenshot_multiview":
                            response = self.handle_viewport_screenshot_multiview(command)
                        elif command_type == "set_viewport_shading_mode":
                            response = self.handle_set_viewport_shading_mode(command)
                        elif command_type == "run_modeling_command":
                            response = self.handle_run_modeling_command(command)
                        elif command_type == "vertex_map_stats":
                            response = self.handle_vertex_map_stats(command)
                        elif command_type == "vertex_map_threshold_to_polygon_selection":
                            response = self.handle_vertex_map_threshold_to_polygon_selection(command)
                        elif command_type == "uv_layout_stats":
                            response = self.handle_uv_layout_stats(command)
                        elif command_type == "sample_bitmap_at_uv":
                            response = self.handle_sample_bitmap_at_uv(command)
                        elif command_type == "uv_islands_to_objects":
                            response = self.handle_uv_islands_to_objects(command)
                        elif command_type == "sample_vmap_via_uv":
                            response = self.handle_sample_vmap_via_uv(command)
                        elif command_type == "uv_transfer":
                            response = self.handle_uv_transfer(command)
                        elif command_type == "uv_from_projection":
                            response = self.handle_uv_from_projection(command)
                        elif command_type == "get_viewport_state":
                            response = self.handle_get_viewport_state(command)
                        elif command_type == "list_render_engines":
                            response = self.handle_list_render_engines(command)
                        elif command_type == "get_active_renderer":
                            response = self.handle_get_active_renderer(command)
                        # Material graph / shader / CallCommand (Octane tier)
                        elif command_type == "dump_material_graph":
                            response = self.handle_dump_material_graph(command)
                        elif command_type == "create_via_command":
                            response = self.handle_create_via_command(command)
                        elif command_type == "link_shader_to_parameter":
                            response = self.handle_link_shader_to_parameter(command)
                        else:
                            response = {"error": f"Unknown command: {command_type}"}

                        # Echo client-supplied request_id back if present, so
                        # async clients can correlate responses to requests.
                        if request_id is not None and isinstance(response, dict):
                            response.setdefault("request_id", request_id)

                        # Send the response as JSON
                        response_json = json.dumps(response) + "\n"
                        client.sendall(response_json.encode("utf-8"))
                        self.log(f"[C4D] Sent response for {command_type}")

                    except json.JSONDecodeError:
                        error_response = {"error": "Invalid JSON format"}
                        client.sendall(
                            (json.dumps(error_response) + "\n").encode("utf-8")
                        )
                    except Exception as e:
                        error_response = {
                            "error": f"Error processing command: {str(e)}"
                        }
                        client.sendall(
                            (json.dumps(error_response) + "\n").encode("utf-8")
                        )
                        self.log(f"[**ERROR**] Error processing command: {str(e)}")

        except Exception as e:
            self.log(f"[C4D] Client error: {str(e)}")
        finally:
            client.close()
            self.log("[C4D] Client disconnected")

    def stop(self):
        """Stop the server."""
        self.running = False
        if self.socket:
            self.socket.close()
        self.update_status("Offline")
        self.log("[C4D] Server stopped")

    # Basic commands
    def handle_get_scene_info(self):
        """Handle get_scene_info command."""
        doc = c4d.documents.GetActiveDocument()

        # Get scene information
        scene_info = {
            "filename": doc.GetDocumentName() or "Untitled",
            "object_count": self.count_objects(doc),
            "polygon_count": self.count_polygons(doc),
            "material_count": len(doc.GetMaterials()),
            "current_frame": doc.GetTime().GetFrame(doc.GetFps()),
            "fps": doc.GetFps(),
            "frame_start": doc.GetMinTime().GetFrame(doc.GetFps()),
            "frame_end": doc.GetMaxTime().GetFrame(doc.GetFps()),
        }

        return {"scene_info": scene_info}

    def count_objects(self, doc):
        """Count all objects in the document."""
        count = 0
        obj = doc.GetFirstObject()
        while obj:
            count += 1
            obj = obj.GetNext()
        return count

    def count_polygons(self, doc):
        """Count all polygons in the document."""
        count = 0
        obj = doc.GetFirstObject()
        while obj:
            if obj.GetType() == c4d.Opolygon:
                count += obj.GetPolygonCount()
            obj = obj.GetNext()
        return count

    def get_object_type_name(self, obj):
        """Get a human-readable object type name."""
        type_id = obj.GetType()

        # Expanded type map including MoGraph objects
        type_map = {
            c4d.Ocube: "Cube",
            c4d.Osphere: "Sphere",
            c4d.Ocone: "Cone",
            c4d.Ocylinder: "Cylinder",
            c4d.Odisc: "Disc",
            c4d.Ocapsule: "Capsule",
            c4d.Otorus: "Torus",
            c4d.Otube: "Tube",
            c4d.Oplane: "Plane",
            c4d.Olight: "Light",
            c4d.Ocamera: "Camera",
            c4d.Onull: "Null",
            c4d.Opolygon: "Polygon Object",
            c4d.Ospline: "Spline",
            c4d.Omgcloner: "MoGraph Cloner",  # MoGraph Cloner
        }

        # Check for MoGraph objects using ranges
        if 1018544 <= type_id <= 1019544:  # MoGraph objects general range
            if type_id == c4d.Omgcloner:
                return "MoGraph Cloner"
            elif type_id == c4d.Omgtext:
                return "MoGraph Text"
            elif type_id == c4d.Omgtracer:
                return "MoGraph Tracer"
            elif type_id == c4d.Omgmatrix:
                return "MoGraph Matrix"
            else:
                return "MoGraph Object"

        # MoGraph Effectors
        if 1019544 <= type_id <= 1019644:
            if type_id == c4d.Omgrandom:
                return "Random Effector"
            elif type_id == c4d.Omgstep:
                return "Step Effector"
            elif type_id == c4d.Omgformula:
                return "Formula Effector"
            else:
                return "MoGraph Effector"

        # Fields (newer Cinema 4D versions)
        if 1039384 <= type_id <= 1039484:
            field_types = {
                1039384: "Spherical Field",
                1039385: "Box Field",
                1039386: "Cylindrical Field",
                1039387: "Torus Field",
                1039388: "Cone Field",
                1039389: "Linear Field",
                1039390: "Radial Field",
                1039394: "Noise Field",
            }
            return field_types.get(type_id, "Field")

        return type_map.get(type_id, f"Object (Type: {type_id})")

    def find_object_by_name(self, doc, name_or_guid, use_guid=False):
        """Find object by GUID (preferred) or name, using local registry first. FIX for recursion and GUID format check."""
        if not name_or_guid:
            self.log("[C4D FIND] Cannot find object: No name or GUID provided.")
            return None
        if not doc:
            self.log("[C4D FIND] ## Error ##: No document provided for search.")
            return None
        if not hasattr(self, "_name_to_guid_registry"):
            self._name_to_guid_registry = {}
        if not hasattr(self, "_guid_to_name_registry"):
            self._guid_to_name_registry = {}

        search_term = str(name_or_guid).strip()
        self.log(
            f"[C4D FIND] Attempting to find: '{search_term}' (Treat as GUID: {use_guid})"
        )

        # --- GUID Search Logic ---
        if use_guid:
            guid_to_find = search_term
            # --- FIXED: GUID format check ---
            # C4D GUIDs converted with str() are typically long numbers (sometimes negative).
            # Check if it's likely numeric and long enough. Hyphen is NOT required.
            is_valid_guid_format = False
            if guid_to_find:  # Check if not empty
                try:
                    int(guid_to_find)  # Check if it can be interpreted as an integer
                    if len(guid_to_find) > 10:  # Check if it's reasonably long
                        is_valid_guid_format = True
                except ValueError:
                    is_valid_guid_format = False  # Not purely numeric
            # --- END FIXED ---

            if not is_valid_guid_format:
                self.log(
                    f"[C4D FIND] ## Warning ##: Invalid format/length for GUID search: '{guid_to_find}'. Treating as name."
                )
                use_guid = False  # Fallback to name search
            else:
                # 1. Try direct C4D SearchObject
                obj_from_search = doc.SearchObject(guid_to_find)
                if obj_from_search:
                    self.log(
                        f"[C4D FIND] Success (GUID Scene Search): Found '{obj_from_search.GetName()}' (GUID: {guid_to_find})"
                    )
                    current_actual_name = obj_from_search.GetName()
                    reg_entry = self._guid_to_name_registry.get(guid_to_find)
                    if (
                        not reg_entry
                        or reg_entry.get("actual_name") != current_actual_name
                    ):
                        req_name = (
                            reg_entry.get("requested_name", current_actual_name)
                            if reg_entry
                            else current_actual_name
                        )
                        self.register_object_name(obj_from_search, req_name)
                    return obj_from_search

                # 2. Manual iteration fallback
                self.log(
                    f"[C4D FIND] Info: doc.SearchObject failed for GUID {guid_to_find}. Iterating manually..."
                )
                all_objects = self._get_all_objects(doc)
                found_obj_manual = None
                for obj_iter in all_objects:
                    try:
                        iter_guid = str(obj_iter.GetGUID())
                        if iter_guid == guid_to_find:
                            self.log(
                                f"[C4D FIND] Success (GUID Manual Iteration): Found '{obj_iter.GetName()}' (GUID: {guid_to_find})"
                            )
                            found_obj_manual = obj_iter
                            break
                    except Exception as e_iter:
                        self.log(
                            f"[C4D FIND] Error checking GUID during iteration for '{obj_iter.GetName()}': {e_iter}"
                        )

                if found_obj_manual:
                    current_actual_name = found_obj_manual.GetName()
                    reg_entry = self._guid_to_name_registry.get(guid_to_find)
                    if (
                        reg_entry
                        and reg_entry.get("actual_name") != current_actual_name
                    ):
                        req_name = reg_entry.get("requested_name", current_actual_name)
                        self.register_object_name(found_obj_manual, req_name)
                    elif not reg_entry:
                        self.register_object_name(
                            found_obj_manual, found_obj_manual.GetName()
                        )
                    return found_obj_manual

                # 3. If both failed, cleanup registry
                self.log(
                    f"[C4D FIND] Failed (GUID): Object with GUID '{guid_to_find}' not found by SearchObject or Manual Iteration."
                )
                if guid_to_find in self._guid_to_name_registry:
                    self.log(
                        f"[C4D FIND] Cleaning registry for supposedly existing but unfound GUID {guid_to_find}."
                    )
                    reg_entry = self._guid_to_name_registry.pop(guid_to_find, None)
                    if reg_entry:
                        req_name_lower = reg_entry.get("requested_name", "").lower()
                        act_name_lower = reg_entry.get("actual_name", "").lower()
                        if req_name_lower:
                            self._name_to_guid_registry.pop(req_name_lower, None)
                        if act_name_lower and act_name_lower != req_name_lower:
                            self._name_to_guid_registry.pop(act_name_lower, None)
                return None

        # --- Name Search Logic (Keep as is from previous correction) ---
        name_to_find_lower = search_term.lower()

        # 1. Check registry by name -> GUID -> Object
        guid_from_registry = self._name_to_guid_registry.get(name_to_find_lower)
        if guid_from_registry:
            obj_from_guid_lookup = self.find_object_by_name(
                doc, guid_from_registry, use_guid=True
            )
            if obj_from_guid_lookup:
                stored_names = self._guid_to_name_registry.get(guid_from_registry, {})
                actual_name_reg = stored_names.get("actual_name", "").lower()
                requested_name_reg = stored_names.get("requested_name", "").lower()
                found_name_actual = obj_from_guid_lookup.GetName().lower()
                if name_to_find_lower in [
                    actual_name_reg,
                    requested_name_reg,
                    found_name_actual,
                ]:
                    self.log(
                        f"[C4D FIND] Success (Registry Name '{search_term}' -> GUID {guid_from_registry}): Found '{obj_from_guid_lookup.GetName()}'"
                    )
                    if found_name_actual != actual_name_reg:
                        self.register_object_name(
                            obj_from_guid_lookup,
                            stored_names.get("requested_name", search_term),
                        )
                    return obj_from_guid_lookup
                else:
                    self.log(
                        f"[C4D FIND] ## Warning ## Registry inconsistency for name '{search_term}'. Continuing search."
                    )
            else:
                self.log(
                    f"[C4D FIND] ## Warning ## Name '{search_term}' maps to non-existent GUID. Cleaning registry."
                )
                self._name_to_guid_registry.pop(name_to_find_lower, None)
                reg_entry = self._guid_to_name_registry.pop(guid_from_registry, None)
                if reg_entry:
                    other_name_key = (
                        "actual_name"
                        if name_to_find_lower
                        == reg_entry.get("requested_name", "").lower()
                        else "requested_name"
                    )
                    other_name_val = reg_entry.get(other_name_key, "").lower()
                    if other_name_val:
                        self._name_to_guid_registry.pop(other_name_val, None)

        # 2. Direct name search
        all_objects_name = self._get_all_objects(doc)
        for obj in all_objects_name:
            if obj.GetName().strip().lower() == name_to_find_lower:
                self.log(
                    f"[C4D FIND] Success (Direct Name Search): Found '{obj.GetName()}'"
                )
                self.register_object_name(obj, search_term)
                return obj

        # 3. Comment Tag Search
        self.log(f"[C4D FIND] Trying comment tag search for '{search_term}'")
        if hasattr(c4d, "Tcomment"):
            for obj in all_objects_name:
                for tag in obj.GetTags():
                    if tag.GetType() == c4d.Tcomment:
                        try:
                            tag_text = tag[c4d.COMMENTTAG_TEXT]
                            if tag_text and tag_text.startswith("MCP_NAME:"):
                                tagged_name = tag_text[9:].strip()
                                if tagged_name.lower() == name_to_find_lower:
                                    self.log(
                                        f"[C4D FIND] Success (Comment Tag): Found '{obj.GetName()}'"
                                    )
                                    self.register_object_name(obj, search_term)
                                    return obj
                        except Exception as e:
                            self.log(f"Error reading comment tag: {e}")

        # 4. User Data Search
        self.log(f"[C4D FIND] Trying user data search for '{search_term}'")
        for obj in all_objects_name:
            try:
                userdata = obj.GetUserDataContainer()
                if userdata:
                    for i in range(len(userdata)):
                        desc_id_tuple = obj.GetUserDataContainer()[i]
                        if (
                            isinstance(desc_id_tuple, tuple)
                            and len(desc_id_tuple) > c4d.DESC_NAME
                        ):
                            if desc_id_tuple[c4d.DESC_NAME] == "mcp_original_name":
                                desc_id = desc_id_tuple[c4d.DESC_ID]
                                if obj[desc_id].strip().lower() == name_to_find_lower:
                                    self.log(
                                        f"[C4D FIND] Success (User Data): Found '{obj.GetName()}'"
                                    )
                                    self.register_object_name(obj, search_term)
                                    return obj
            except Exception as e:
                self.log(f"Error checking user data for '{obj.GetName()}': {e}")

        # 5. Fuzzy Name Search
        self.log(f"[C4D FIND] Trying fuzzy name matching for '{search_term}'")
        similar_objects = []
        for obj in all_objects_name:
            obj_name_lower = obj.GetName().strip().lower()
            if (
                name_to_find_lower in obj_name_lower
                or obj_name_lower in name_to_find_lower
                or obj_name_lower.startswith(name_to_find_lower)
                or name_to_find_lower.startswith(obj_name_lower)
            ):
                similarity = abs(len(obj_name_lower) - len(name_to_find_lower))
                similar_objects.append((obj, similarity))
        if similar_objects:
            similar_objects.sort(key=lambda pair: pair[1])
            closest_match = similar_objects[0][0]
            self.log(
                f"[C4D FIND] Success (Fuzzy Fallback): Using '{closest_match.GetName()}' for '{search_term}'"
            )
            self.register_object_name(closest_match, search_term)
            return closest_match

        # Final failure
        self.log(
            f"[C4D FIND] Failed: Object '{search_term}' not found after all checks."
        )
        return None

    def _get_all_objects(self, doc):
        """Recursively collects all objects in the scene into a flat list."""
        result = []

        def collect_recursive(obj):
            while obj:
                result.append(obj)
                if obj.GetDown():
                    collect_recursive(obj.GetDown())
                obj = obj.GetNext()

        first_obj = doc.GetFirstObject()
        if first_obj:
            collect_recursive(first_obj)

        return result

    def get_all_objects_comprehensive(self, doc):
        """Get all objects in the document using multiple methods to ensure complete coverage.

        This method is specifically designed to catch objects that might be missed by
        standard GetFirstObject()/GetNext() iteration, particularly MoGraph objects.

        Args:
            doc: The Cinema 4D document to search

        Returns:
            List of all objects found
        """
        all_objects = []
        found_ids = set()

        # Method 1: Standard traversal using GetFirstObject/GetNext/GetDown
        self.log("[C4D] Comprehensive search - using standard traversal")

        def traverse_hierarchy(obj):
            while obj:
                try:
                    obj_id = str(obj.GetGUID())
                    if obj_id not in found_ids:
                        all_objects.append(obj)
                        found_ids.add(obj_id)

                        # Check children
                        child = obj.GetDown()
                        if child:
                            traverse_hierarchy(child)
                except Exception as e:
                    self.log(f"[**ERROR**] Error in hierarchy traversal: {str(e)}")

                # Move to next sibling
                obj = obj.GetNext()

        # Start traversal from the first object
        first_obj = doc.GetFirstObject()
        if first_obj:
            traverse_hierarchy(first_obj)

        # Method 2: Use GetObjects() for flat list (catches some objects)
        try:
            self.log("[C4D] Comprehensive search - using GetObjects()")
            flat_objects = doc.GetObjects()
            for obj in flat_objects:
                obj_id = str(obj.GetGUID())
                if obj_id not in found_ids:
                    all_objects.append(obj)
                    found_ids.add(obj_id)
        except Exception as e:
            self.log(f"[**ERROR**] Error in GetObjects search: {str(e)}")

        # Method 3: Special handling for MoGraph objects
        try:
            self.log("[C4D] Comprehensive search - direct access for MoGraph")

            # Direct check for Cloners
            if hasattr(c4d, "Omgcloner"):
                # Try using FindObjects if available (R20+)
                if hasattr(c4d.BaseObject, "FindObjects"):
                    cloners = c4d.BaseObject.FindObjects(doc, c4d.Omgcloner)
                    for cloner in cloners:
                        obj_id = str(cloner.GetGUID())
                        if obj_id not in found_ids:
                            all_objects.append(cloner)
                            found_ids.add(obj_id)
                            self.log(
                                f"[C4D] Found cloner using FindObjects: {cloner.GetName()}"
                            )

            # Check for other MoGraph objects if needed
            # (Add specific searches here if certain objects are still missed)

        except Exception as e:
            self.log(f"[**ERROR**] Error in MoGraph direct search: {str(e)}")

        self.log(
            f"[C4D] Comprehensive object search complete, found {len(all_objects)} objects"
        )
        return all_objects

    def handle_group_objects(self, command):
        """Handle group_objects command with GUID support."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        requested_group_name = command.get("group_name", "Group")
        object_identifiers = command.get("object_names", [])
        position = command.get("position", None)
        center = command.get("center", False)
        keep_world_pos = command.get("keep_world_position", True)

        objects_to_group = []
        identifiers_found_guids = set()
        identifiers_not_found = []

        if object_identifiers:
            self.log(f"[GROUP] Received identifiers: {object_identifiers}")
            for identifier in object_identifiers:
                if not identifier:
                    continue

                identifier_str = str(identifier).strip()
                # --- REVISED: Detect GUID format correctly ---
                use_current_id_as_guid = False
                if "-" in identifier_str and len(identifier_str) > 30:
                    use_current_id_as_guid = True
                elif identifier_str.isdigit() or (
                    identifier_str.startswith("-") and identifier_str[1:].isdigit()
                ):
                    if len(identifier_str) > 10:
                        use_current_id_as_guid = True
                # --- END REVISED ---

                self.log(
                    f"[GROUP] Finding object by identifier: '{identifier_str}' (Treat as GUID: {use_current_id_as_guid})"
                )
                # --- Pass the correct flag to find_object_by_name ---
                obj = self.find_object_by_name(
                    doc, identifier_str, use_guid=use_current_id_as_guid
                )

                if obj:
                    obj_guid = str(obj.GetGUID())
                    if obj_guid not in identifiers_found_guids:
                        objects_to_group.append(obj)
                        identifiers_found_guids.add(obj_guid)
                        self.log(
                            f"[GROUP] Found object: '{obj.GetName()}' (GUID: {obj_guid})"
                        )
                    else:
                        self.log(
                            f"[GROUP] Info: Object '{obj.GetName()}' (GUID: {obj_guid}) already added."
                        )
                else:
                    self.log(
                        f"[GROUP] ## Warning ##: Object identifier not found: '{identifier_str}' (Searched as GUID: {use_current_id_as_guid})"
                    )
                    identifiers_not_found.append(identifier_str)
        else:
            objects_to_group = doc.GetActiveObjects(
                c4d.GETACTIVEOBJECTFLAGS_SELECTIONORDER
                | c4d.GETACTIVEOBJECTFLAGS_TOPLEVEL
            )
            if not objects_to_group:
                return {
                    "error": "No objects selected (top-level) or specified via 'object_names'."
                }
            self.log(
                f"[GROUP] Fallback: Grouping {len(objects_to_group)} selected top-level objects."
            )

        if not objects_to_group:
            error_msg = "No valid objects found to group."
            if identifiers_not_found:
                error_msg += f" Identifiers not found: {identifiers_not_found}"
            return {"error": error_msg}

        # --- Grouping Logic ---
        group_null = None
        try:
            doc.StartUndo()
            group_null = c4d.BaseObject(c4d.Onull)
            group_null.SetName(requested_group_name)
            doc.InsertObject(group_null, None, None)
            doc.AddUndo(c4d.UNDOTYPE_NEW, group_null)

            grouped_actual_names = []
            grouped_guids = []
            original_matrices = {}

            # Calculate center
            group_center_pos = c4d.Vector(0)
            # ... (keep centering logic as before) ...
            if center:
                min_vec, max_vec = c4d.Vector(float("inf")), c4d.Vector(float("-inf"))
                count = 0
                for obj in objects_to_group:
                    try:
                        rad, mp = obj.GetRad(), obj.GetMp()
                        min_vec.x, min_vec.y, min_vec.z = (
                            min(min_vec.x, mp.x - rad.x),
                            min(min_vec.y, mp.y - rad.y),
                            min(min_vec.z, mp.z - rad.z),
                        )
                        max_vec.x, max_vec.y, max_vec.z = (
                            max(max_vec.x, mp.x + rad.x),
                            max(max_vec.y, mp.y + rad.y),
                            max(max_vec.z, mp.z + rad.z),
                        )
                        count += 1
                    except Exception as e_bounds:
                        self.log(
                            f"[GROUP] Warning: Error getting bounds for '{obj.GetName()}': {e_bounds}"
                        )
                if count > 0:
                    group_center_pos = (min_vec + max_vec) * 0.5
                    self.log(f"[GROUP] Calculated center for null: {group_center_pos}")
                else:
                    center = False
                    self.log(
                        "[GROUP] Warning: Could not calculate center, disabling centering."
                    )

            # Reparent
            for obj in reversed(objects_to_group):
                try:
                    obj_name = obj.GetName()
                    obj_guid = str(obj.GetGUID())
                    grouped_actual_names.append(obj_name)
                    grouped_guids.append(obj_guid)
                    if keep_world_pos:
                        original_matrices[obj_guid] = obj.GetMg()
                    obj.Remove()
                    obj.InsertUnder(group_null)
                    doc.AddUndo(c4d.UNDOTYPE_CHANGE, obj)
                except Exception as e_reparent:
                    self.log(
                        f"[**ERROR**] Failed to reparent object '{obj_name}': {e_reparent}"
                    )

            # Set Position
            if isinstance(position, list) and len(position) == 3:
                try:
                    target_pos = c4d.Vector(
                        float(position[0]), float(position[1]), float(position[2])
                    )
                    group_null.SetAbsPos(target_pos)
                    doc.AddUndo(c4d.UNDOTYPE_CHANGE, group_null)
                except (ValueError, TypeError) as e_pos:
                    self.log(
                        f"[GROUP] Warning: Invalid position value '{position}': {e_pos}"
                    )
            elif center:
                group_null.SetAbsPos(group_center_pos)
                doc.AddUndo(c4d.UNDOTYPE_CHANGE, group_null)

            # Adjust children
            if keep_world_pos:
                null_mg_inv = ~group_null.GetMg()
                for child in group_null.GetChildren():
                    child_guid = str(child.GetGUID())
                    if child_guid in original_matrices:
                        new_ml = null_mg_inv * original_matrices[child_guid]
                        child.SetMl(new_ml)
                        doc.AddUndo(c4d.UNDOTYPE_CHANGE, child)
                    else:
                        self.log(
                            f"[GROUP] ## Warning ## Original matrix not found for child '{child.GetName()}'."
                        )

            doc.EndUndo()
            c4d.EventAdd()

            # --- Contextual Return ---
            actual_group_name = group_null.GetName()
            group_guid = str(group_null.GetGUID())
            pos_vector = group_null.GetAbsPos()
            self.register_object_name(group_null, requested_group_name)
            response = {
                "group": {
                    "requested_name": requested_group_name,
                    "actual_name": actual_group_name,
                    "guid": group_guid,
                    "children_actual_names": grouped_actual_names,
                    "children_guids": grouped_guids,
                    "position": [pos_vector.x, pos_vector.y, pos_vector.z],
                    "centered": center,
                    "kept_world_position": keep_world_pos,
                }
            }
            if identifiers_not_found:
                response["warnings"] = [
                    f"Object identifier not found: '{idf}'"
                    for idf in identifiers_not_found
                ]
            return response

        except Exception as e:
            doc.EndUndo()
            error_msg = f"Error during grouping: {str(e)}"
            self.log(f"[**ERROR**] {error_msg}\n{traceback.format_exc()}")
            if group_null and group_null.GetDown() is None:
                try:
                    doc.AddUndo(c4d.UNDOTYPE_DELETE, group_null)
                    group_null.Remove()
                except:
                    pass
            return {"error": error_msg, "traceback": traceback.format_exc()}

    def handle_add_primitive(self, command):
        """Handle add_primitive command."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}  # Added check

        primitive_type = command.get("primitive_type") or command.get("type") or "cube"
        primitive_type = primitive_type.lower()

        # Use provided name or generate one
        requested_name = (
            command.get("name")
            or command.get("object_name")
            or f"MCP_{primitive_type.capitalize()}_{int(time.time()) % 1000}"  # Generate unique name
        )

        position_list = command.get("position", [0, 0, 0])
        size_list = command.get("size", [50, 50, 50])  # Default size

        # --- Safely parse position and size ---
        position = [0.0, 0.0, 0.0]
        if isinstance(position_list, list) and len(position_list) >= 3:
            try:
                position = [float(p) for p in position_list[:3]]
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid position data {position_list}")
        else:
            self.log(f"Warning: Position data not a list of 3: {position_list}")

        size = [50.0, 50.0, 50.0]
        if isinstance(size_list, list) and len(size_list) > 0:
            try:
                size_raw = [float(s) for s in size_list if s is not None]
                if not size_raw:
                    raise ValueError("Empty size list after filtering None")
                sx = size_raw[0]
                sy = size_raw[1] if len(size_raw) > 1 else sx
                sz = size_raw[2] if len(size_raw) > 2 else sx
                size = [sx, sy, sz]
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid size data {size_list}")
        elif isinstance(size_list, (int, float)):  # Allow single size value
            size = [float(size_list)] * 3
        else:
            self.log(f"Warning: Size data not a list or number: {size_list}")
        # --- End safe parse ---

        obj = None
        try:  # Wrap object creation/setting in try-except
            # Create the appropriate primitive object
            if primitive_type == "cube":
                obj = c4d.BaseObject(c4d.Ocube)
                obj[c4d.PRIM_CUBE_LEN] = c4d.Vector(*size)
            elif primitive_type == "sphere":
                obj = c4d.BaseObject(c4d.Osphere)
                obj[c4d.PRIM_SPHERE_RAD] = size[0] / 2.0  # Use float division
            elif primitive_type == "cone":
                obj = c4d.BaseObject(c4d.Ocone)
                obj[c4d.PRIM_CONE_TRAD] = 0
                obj[c4d.PRIM_CONE_BRAD] = size[0] / 2.0
                obj[c4d.PRIM_CONE_HEIGHT] = size[1]
            elif primitive_type == "cylinder":
                obj = c4d.BaseObject(c4d.Ocylinder)
                obj[c4d.PRIM_CYLINDER_RADIUS] = size[0] / 2.0
                obj[c4d.PRIM_CYLINDER_HEIGHT] = size[1]
            elif primitive_type == "plane":
                obj = c4d.BaseObject(c4d.Oplane)
                obj[c4d.PRIM_PLANE_WIDTH] = size[0]
                obj[c4d.PRIM_PLANE_HEIGHT] = size[1]
            elif primitive_type == "pyramid":
                obj = c4d.BaseObject(c4d.Opyramid)
                if hasattr(c4d, "PRIM_PYRAMID_LEN"):
                    obj[c4d.PRIM_PYRAMID_LEN] = c4d.Vector(*size)
                else:
                    if hasattr(c4d, "PRIM_PYRAMID_WIDTH"):
                        obj[c4d.PRIM_PYRAMID_WIDTH] = size[0]
                    if hasattr(c4d, "PRIM_PYRAMID_HEIGHT"):
                        obj[c4d.PRIM_PYRAMID_HEIGHT] = size[1]
                    if hasattr(c4d, "PRIM_PYRAMID_DEPTH"):
                        obj[c4d.PRIM_PYRAMID_DEPTH] = size[2]
            elif primitive_type == "disc":
                obj = c4d.BaseObject(c4d.Odisc)
                # Use ORAD/IRAD for disc
                obj[c4d.PRIM_DISC_ORAD] = size[0] / 2.0
                obj[c4d.PRIM_DISC_IRAD] = 0  # Default inner radius
            elif primitive_type == "tube":
                obj = c4d.BaseObject(c4d.Otube)
                obj[c4d.PRIM_TUBE_RADIUS] = size[0] / 2.0
                obj[c4d.PRIM_TUBE_IRADIUS] = size[1] / 2.0
                obj[c4d.PRIM_TUBE_HEIGHT] = size[2]
            elif primitive_type == "torus":
                obj = c4d.BaseObject(c4d.Otorus)
                # Use RINGRAD/PIPERAD for Torus
                obj[c4d.PRIM_TORUS_RINGRAD] = size[0] / 2.0
                obj[c4d.PRIM_TORUS_PIPERAD] = size[1] / 2.0
            elif primitive_type == "platonic":
                obj = c4d.BaseObject(c4d.Oplatonic)
                obj[c4d.PRIM_PLATONIC_TYPE] = c4d.PRIM_PLATONIC_TYPE_TETRA
                obj[c4d.PRIM_PLATONIC_RAD] = size[0] / 2.0
            else:
                self.log(
                    f"Unknown primitive_type: {primitive_type}, defaulting to cube."
                )
                obj = c4d.BaseObject(c4d.Ocube)
                obj[c4d.PRIM_CUBE_LEN] = c4d.Vector(*size)

            if obj is None:  # Check if object creation failed
                return {
                    "error": f"Failed to create base object for type '{primitive_type}'"
                }

            # Set common properties
            obj.SetName(requested_name)
            obj.SetAbsPos(c4d.Vector(*position))

            # Add to doc and finalize
            doc.InsertObject(obj)
            doc.AddUndo(c4d.UNDOTYPE_NEW, obj)  # Add Undo step
            doc.SetActiveObject(obj)  # Make it active
            c4d.EventAdd()

            # --- MODIFIED FOR CONTEXT ---
            actual_name = obj.GetName()
            guid = str(obj.GetGUID())
            pos_vec = obj.GetAbsPos()
            obj_type_name = self.get_object_type_name(obj)

            # Register the object
            self.register_object_name(obj, requested_name)

            # Return contextual information
            return {
                "object": {
                    "requested_name": requested_name,
                    "actual_name": actual_name,
                    "guid": guid,
                    "type": obj_type_name,
                    "type_id": obj.GetType(),
                    "position": [pos_vec.x, pos_vec.y, pos_vec.z],
                }
            }
            # --- END MODIFIED ---

        except Exception as e:
            # Catch errors during object creation or property setting
            self.log(
                f"[**ERROR**] Error adding primitive '{requested_name}': {str(e)}\n{traceback.format_exc()}"
            )
            # Clean up object if created but not inserted
            if obj and not obj.GetDocument():
                try:
                    obj.Remove()
                except:
                    pass
            return {
                "error": f"Failed to add primitive: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def register_object_name(self, obj, requested_name):
        """Register object GUID, actual name, and requested name for context tracking."""
        if not obj or not isinstance(obj, c4d.BaseObject):
            self.log("[C4D REG] Invalid object provided for registration.")
            return
        # Ensure registries exist (redundant if __init__ is correct, but safe)
        if not hasattr(self, "_name_to_guid_registry"):
            self._name_to_guid_registry = {}
        if not hasattr(self, "_guid_to_name_registry"):
            self._guid_to_name_registry = {}
        # Keep original for compatibility if needed
        if not hasattr(self, "_object_name_registry"):
            self._object_name_registry = {}

        try:
            # Ensure the object is part of a document before getting GUID
            if not obj.GetDocument():
                self.log(
                    f"[C4D REG] ## Warning ##: Object '{obj.GetName()}' not in document, cannot get reliable GUID."
                )
                return  # Skip registration if not in doc

            obj_id = str(obj.GetGUID())
            actual_name = obj.GetName()

            if not obj_id or len(obj_id) < 10:  # Basic check for non-empty GUID
                self.log(
                    f"[C4D REG] ## Warning ##: Got potentially invalid GUID '{obj_id}' for object '{actual_name}'. Cannot register."
                )
                return

            if not requested_name:
                self.log(
                    f"[C4D REG] ## Warning ## Empty requested name provided for '{actual_name}', using actual name."
                )
                requested_name = actual_name

            # Prepare names for registry check (lower case)
            req_name_lower = requested_name.lower()
            act_name_lower = actual_name.lower()

            # Clean up potentially stale entries for these names in _name_to_guid_registry
            for name_lower in {req_name_lower, act_name_lower}:
                old_guid = self._name_to_guid_registry.pop(name_lower, None)
                if old_guid and old_guid != obj_id:
                    self.log(
                        f"[C4D REG] Cleaning old name->guid mapping: '{name_lower}' pointed to {old_guid}, now points to {obj_id}"
                    )
                    # Also remove the reverse mapping for the old GUID if it exists
                    old_reg_entry = self._guid_to_name_registry.get(old_guid)
                    if old_reg_entry:
                        old_req_lower_check = old_reg_entry.get(
                            "requested_name", ""
                        ).lower()
                        old_act_lower_check = old_reg_entry.get(
                            "actual_name", ""
                        ).lower()
                        if (
                            old_req_lower_check == name_lower
                            or old_act_lower_check == name_lower
                        ):
                            self._guid_to_name_registry.pop(old_guid, None)
                            self.log(
                                f"[C4D REG] Removed stale guid->name entry for {old_guid}"
                            )

            # Clean up potentially stale entry for this GUID in _guid_to_name_registry
            old_name_entry = self._guid_to_name_registry.pop(obj_id, None)
            if old_name_entry:
                # Remove old name mappings associated with this GUID from _name_to_guid_registry
                self._name_to_guid_registry.pop(
                    old_name_entry.get("requested_name", "").lower(), None
                )
                self._name_to_guid_registry.pop(
                    old_name_entry.get("actual_name", "").lower(), None
                )
                self.log(
                    f"[C4D REG] Cleaning old guid->name mapping for {obj_id} (was pointing to '{old_name_entry.get('actual_name')}')."
                )

            # Add the new mappings to the *new* registries
            self._name_to_guid_registry[req_name_lower] = obj_id
            if (
                act_name_lower != req_name_lower
            ):  # Avoid duplicate key if names are same
                self._name_to_guid_registry[act_name_lower] = obj_id

            self._guid_to_name_registry[obj_id] = {
                "requested_name": requested_name,
                "actual_name": actual_name,
            }

            # --- Keep Original Registry Logic (Optional - for strict backward compatibility) ---
            # If you need the old registry structure for some reason, keep these lines.
            # Otherwise, they can be removed once find_object_by_name is fully updated.
            self._object_name_registry[obj_id] = requested_name
            self._object_name_registry[requested_name] = obj_id
            # --- End Optional Original Registry ---

            self.log(
                f"[C4D REG] Registered: Req='{requested_name}', Act='{actual_name}', GUID={obj_id}"
            )

            # User Data part from original (keep as is)
            try:
                has_tag = False
                userdata = obj.GetUserDataContainer()
                if userdata:
                    for data_index in range(len(userdata)):
                        desc_entry = userdata[data_index]
                        # Check if it's a valid description element before accessing DESC_NAME
                        if (
                            isinstance(desc_entry, tuple)
                            and len(desc_entry) > c4d.DESC_NAME
                        ):  # Handle tuple structure
                            if desc_entry[c4d.DESC_NAME] == "mcp_original_name":
                                has_tag = True
                                break
                        elif hasattr(desc_entry, "__getitem__") and c4d.DESC_NAME < len(
                            desc_entry
                        ):  # Handle potential sequence access
                            if desc_entry[c4d.DESC_NAME] == "mcp_original_name":
                                has_tag = True
                                break

                if not has_tag:
                    bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STRING)
                    if bc:
                        bc[c4d.DESC_NAME] = "mcp_original_name"
                        bc[c4d.DESC_SHORT_NAME] = "MCP Name"
                        element = obj.AddUserData(bc)
                        if element:
                            # Make sure element is a DescID before using it as an index
                            if isinstance(element, c4d.DescID):
                                obj[element] = requested_name
                                self.log(
                                    f"[C4D] Stored original name '{requested_name}' in object user data"
                                )
                            else:
                                # Handle case where AddUserData returns index directly (older C4D?)
                                try:
                                    descid_from_index = obj.GetUserDataContainer()[
                                        element
                                    ][c4d.DESC_ID]
                                    obj[descid_from_index] = requested_name
                                    self.log(
                                        f"[C4D] Stored original name '{requested_name}' in object user data (via index)"
                                    )
                                except Exception as e_ud_index:
                                    self.log(
                                        f"[C4D] ## Warning ##: AddUserData returned unexpected value '{element}', cannot set user data: {e_ud_index}"
                                    )

            except Exception as e:
                # Catch potential errors during GetUserDataContainer or AddUserData
                self.log(
                    f"[C4D] ## Warning ##: Could not add/check user data for original name: {str(e)}\n{traceback.format_exc()}"
                )

        except Exception as e:
            # Catch potential errors during GetGUID, GetName etc.
            failed_name = requested_name or (obj.GetName() if obj else "UnknownObject")
            self.log(
                f"[**ERROR**] Failed to register object '{failed_name}': {e}\n{traceback.format_exc()}"
            )

    def handle_render_preview_base64(self, frame=0, width=640, height=360):
        """SDK 2025-compliant base64 renderer with error resolution"""
        import c4d
        import base64
        import traceback

        def _execute_render():
            try:
                doc = c4d.documents.GetActiveDocument()
                if not doc:
                    return {"error": "No active document"}

                # 1. Camera Validation (Critical Fix)
                if not doc.GetActiveBaseDraw().GetSceneCamera(doc):
                    return {"error": "No active camera (create camera first)"}

                # 2. RenderData Protocol Fix (SDK §9.1.3)
                original_rd = doc.GetActiveRenderData()
                if not original_rd:
                    return {"error": "No render settings configured"}

                rd_clone = original_rd.GetClone(c4d.COPYFLAGS_NONE)
                if not rd_clone:
                    return {"error": "RenderData clone failed"}

                try:
                    doc.InsertRenderData(rd_clone)
                    doc.SetActiveRenderData(rd_clone)  # Required activation

                    # 3. 2025-Specific Configuration
                    settings = rd_clone.GetData()
                    settings[c4d.RDATA_XRES] = width
                    settings[c4d.RDATA_YRES] = height
                    settings[c4d.RDATA_FRAMESEQUENCE] = (
                        c4d.RDATA_FRAMESEQUENCE_CURRENTFRAME
                    )

                    # 4. Mandatory Flags (SDK §9.4.5)
                    render_flags = (
                        c4d.RENDERFLAGS_EXTERNAL
                        | c4d.RENDERFLAGS_SHOWERRORS
                        | 0x00040000  # EMBREE_STREAMING
                        | c4d.RENDERFLAGS_NODOCUMENTCLONE
                    )

                    # 5. Bitmap Initialization (SDK §11.2.3)
                    bmp = c4d.bitmaps.MultipassBitmap(width, height, c4d.COLORMODE_RGB)
                    bmp.AddChannel(True, True)  # Required alpha

                    # 6. Frame Synchronization
                    doc.SetTime(c4d.BaseTime(frame, doc.GetFps()))
                    doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)

                    # 7. Core Render Execution
                    result = c4d.documents.RenderDocument(
                        doc, settings, bmp, render_flags
                    )
                    if result != c4d.RENDERRESULT_OK:
                        return {
                            "error": f"Render failed: {self._render_code_to_str(result)}"
                        }

                    # 8. MemoryFile Handling Fix
                    mem_file = c4d.storage.MemoryFileStruct()
                    mem_file.SetMemoryWriteMode()
                    if bmp.Save(mem_file, c4d.FILTER_PNG) != c4d.IMAGERESULT_OK:
                        return {"error": "PNG encoding failed"}

                    data, _ = mem_file.GetData()
                    return {
                        "success": True,
                        "image_base64": f"data:image/png;base64,{base64.b64encode(data).decode()}",
                    }

                finally:
                    # 9. Correct Resource Cleanup (SDK §9.1.4)
                    if rd_clone:
                        rd_clone.Remove()  # Fixed removal method
                    if "bmp" in locals():
                        bmp.FlushAll()
                    c4d.EventAdd()

            except Exception as e:
                return {"error": f"Render failure: {str(e)}"}

        return self.execute_on_main_thread(_execute_render, _timeout=120)

    def _render_code_to_str(self, code):
        """Convert Cinema4D render result codes to human-readable strings"""
        codes = {
            0: "Success",
            1: "Out of memory",
            2: "Command canceled",
            3: "Missing assets",
            4: "Rendering in progress",
            5: "Invalid document",
            6: "Version mismatch",
            7: "Network error",
            8: "Invalid parameters",
            9: "IO error",
        }
        return codes.get(code, f"Unknown error ({code})")

    def handle_modify_object(self, command):
        """Handle modify_object command with full property support, GUID option, and Camera params."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        properties = command.get("properties", {})
        if not properties:
            return {"error": "No properties provided to modify."}

        # --- Identifier Detection ---
        identifier = None
        use_guid = False
        if command.get("guid"):
            identifier = command.get("guid")
            use_guid = True
            self.log(f"[MODIFY] Using GUID identifier: '{identifier}'")
        elif command.get("object_name"):
            identifier = command.get("object_name")
            identifier_str = str(identifier)
            if "-" in identifier_str and len(identifier_str) > 30:
                use_guid = True
                self.log(f"[MODIFY] Identifier '{identifier}' looks like GUID.")
            else:
                use_guid = False
                self.log(f"[MODIFY] Using Name identifier: '{identifier}'")
        elif command.get("name"):
            identifier = command.get("name")
            use_guid = False
            self.log(f"[MODIFY] Using 'name' key as Name identifier: '{identifier}'")
        else:
            return {
                "error": "No object identifier ('guid', 'object_name', or 'name') provided."
            }

        # Find the object using the determined identifier and flag
        obj = self.find_object_by_name(doc, identifier, use_guid=use_guid)
        if obj is None:
            search_type = "GUID" if use_guid else "Name"
            return {
                "error": f"Object '{identifier}' (searched by {search_type}) not found."
            }

        # Apply modifications
        modified = {}
        name_before = obj.GetName()
        something_changed = False
        obj_type = obj.GetType()  # Get type for specific param handling

        try:
            doc.StartUndo()  # Start undo block

            # Position
            pos_val = properties.get("position")
            if isinstance(pos_val, list) and len(pos_val) >= 3:
                try:
                    new_pos = c4d.Vector(
                        float(pos_val[0]), float(pos_val[1]), float(pos_val[2])
                    )
                    if obj.GetAbsPos() != new_pos:
                        obj.SetAbsPos(new_pos)
                        modified["position"] = [new_pos.x, new_pos.y, new_pos.z]
                        something_changed = True
                except (ValueError, TypeError) as e:
                    self.log(f"Warning: Invalid position value '{pos_val}': {e}")

            # Rotation
            rot_val = properties.get("rotation")
            if isinstance(rot_val, list) and len(rot_val) >= 3:
                try:
                    new_rot_deg = [float(r) for r in rot_val[:3]]
                    new_rot_rad = c4d.Vector(
                        *[c4d.utils.DegToRad(r) for r in new_rot_deg]
                    )
                    obj.SetAbsRot(new_rot_rad)
                    modified["rotation"] = new_rot_deg
                    something_changed = True
                except (ValueError, TypeError) as e:
                    self.log(f"Warning: Invalid rotation value '{rot_val}': {e}")

            # Scale
            scale_val = properties.get("scale")
            if isinstance(scale_val, list) and len(scale_val) >= 3:
                try:
                    new_scale = c4d.Vector(
                        float(scale_val[0]), float(scale_val[1]), float(scale_val[2])
                    )
                    if obj.GetAbsScale() != new_scale:
                        obj.SetAbsScale(new_scale)
                        modified["scale"] = [new_scale.x, new_scale.y, new_scale.z]
                        something_changed = True
                except (ValueError, TypeError) as e:
                    self.log(f"Warning: Invalid scale value '{scale_val}': {e}")

            # Color
            color_val = properties.get("color")
            if isinstance(color_val, list) and len(color_val) >= 3:
                try:
                    new_color = c4d.Vector(
                        max(0.0, min(1.0, float(color_val[0]))),
                        max(0.0, min(1.0, float(color_val[1]))),
                        max(0.0, min(1.0, float(color_val[2]))),
                    )
                    if (
                        obj.IsCorrectType(c4d.Opoint)
                        or obj.IsCorrectType(c4d.Opolygon)
                        or obj.IsCorrectType(c4d.Ospline)
                        or obj.IsCorrectType(c4d.Onull)
                    ):
                        if (
                            obj.GetParameter(c4d.DescID(c4d.ID_BASEOBJECT_COLOR))[1]
                            != new_color
                        ):  # Safer comparison
                            obj[c4d.ID_BASEOBJECT_USECOLOR] = (
                                c4d.ID_BASEOBJECT_USECOLOR_ON
                            )
                            obj[c4d.ID_BASEOBJECT_COLOR] = new_color
                            modified["color"] = [new_color.x, new_color.y, new_color.z]
                            something_changed = True
                    else:
                        self.log(
                            f"Warning: Cannot set display color for object type {obj.GetType()} ('{name_before}')"
                        )
                except (ValueError, TypeError, AttributeError) as e:
                    self.log(f"Warning: Error setting color for '{name_before}': {e}")

            # Primitive Size
            size = properties.get("size")
            if isinstance(size, list) and len(size) > 0:
                obj_type = obj.GetType()
                size_applied = False
                new_size_applied = []
                try:
                    safe_size = [float(s) for s in size if s is not None]
                    if not safe_size:
                        raise ValueError("No valid numeric sizes")
                    sx, sy, sz = (
                        safe_size[0],
                        safe_size[1] if len(safe_size) > 1 else safe_size[0],
                        safe_size[2] if len(safe_size) > 2 else safe_size[0],
                    )

                    if obj_type == c4d.Ocube:
                        new_val = c4d.Vector(sx, sy, sz)
                        current = obj[c4d.PRIM_CUBE_LEN]
                        setter = lambda v: obj.SetParameter(
                            c4d.DescID(c4d.PRIM_CUBE_LEN), v, c4d.DESCFLAGS_SET_NONE
                        )
                        params = [sx, sy, sz]

                    elif obj_type == c4d.Osphere:
                        new_val = sx / 2.0
                        current = obj[c4d.PRIM_SPHERE_RAD]
                        setter = lambda v: obj.SetParameter(
                            c4d.DescID(c4d.PRIM_SPHERE_RAD), v, c4d.DESCFLAGS_SET_NONE
                        )
                        params = [sx]

                    elif obj_type == c4d.Ocone:
                        new_val = (sx / 2.0, sy)
                        current = (obj[c4d.PRIM_CONE_BRAD], obj[c4d.PRIM_CONE_HEIGHT])
                        setter = lambda v: obj.SetParameters(
                            {
                                c4d.DescID(c4d.PRIM_CONE_BRAD): v[0],
                                c4d.DescID(c4d.PRIM_CONE_HEIGHT): v[1],
                            }
                        )
                        params = [sx, sy]

                    elif obj_type == c4d.Ocylinder:
                        new_val = (sx / 2.0, sy)
                        current = (
                            obj[c4d.PRIM_CYLINDER_RADIUS],
                            obj[c4d.PRIM_CYLINDER_HEIGHT],
                        )
                        setter = lambda v: obj.SetParameters(
                            {
                                c4d.DescID(c4d.PRIM_CYLINDER_RADIUS): v[0],
                                c4d.DescID(c4d.PRIM_CYLINDER_HEIGHT): v[1],
                            }
                        )
                        params = [sx, sy]

                    elif obj_type == c4d.Oplane:
                        new_val = (sx, sy)
                        current = (
                            obj[c4d.PRIM_PLANE_WIDTH],
                            obj[c4d.PRIM_PLANE_HEIGHT],
                        )
                        setter = lambda v: obj.SetParameters(
                            {
                                c4d.DescID(c4d.PRIM_PLANE_WIDTH): v[0],
                                c4d.DescID(c4d.PRIM_PLANE_HEIGHT): v[1],
                            }
                        )
                        params = [sx, sy]
                    # Add other primitives here if needed...
                    else:
                        new_val = None
                        current = None
                        setter = None
                        params = None  # Indicate not applicable

                    if setter and new_val is not None and current != new_val:
                        setter(new_val)
                        size_applied = True
                        new_size_applied = params

                    if size_applied:
                        modified["size"] = new_size_applied
                        something_changed = True
                    elif size:
                        self.log(
                            f"Info: 'size' prop not applicable to type {obj_type} ('{name_before}')"
                        )
                except Exception as e_size:
                    self.log(
                        f"Warning: Error modifying size for {name_before}: {e_size}"
                    )

            # --- NEW: Camera Specific Properties ---
            elif obj_type == c4d.Ocamera:
                bc = obj.GetDataInstance()
                if bc:
                    focal_length = properties.get("focal_length")
                    if focal_length is not None:
                        try:
                            val = float(focal_length)
                            focus_id = getattr(
                                c4d, "CAMERAOBJECT_FOCUS", c4d.CAMERA_FOCUS
                            )
                            if bc[focus_id] != val:
                                bc[focus_id] = val
                                modified["focal_length"] = val
                                something_changed = True
                        except (ValueError, TypeError, AttributeError) as e:
                            self.log(
                                f"Warning: Failed to set focal_length '{focal_length}': {e}"
                            )

                    focus_distance = properties.get("focus_distance")
                    if focus_distance is not None:
                        try:
                            val = float(focus_distance)
                            dist_id = getattr(
                                c4d, "CAMERAOBJECT_TARGETDISTANCE", None
                            )  # ID for focus distance
                            if dist_id and bc[dist_id] != val:
                                bc[dist_id] = val
                                modified["focus_distance"] = val
                                something_changed = True
                            elif not dist_id:
                                self.log(
                                    "Warning: CAMERAOBJECT_TARGETDISTANCE parameter not found."
                                )
                        except (ValueError, TypeError, AttributeError) as e:
                            self.log(
                                f"Warning: Failed to set focus_distance '{focus_distance}': {e}"
                            )
                else:
                    self.log(
                        f"Warning: Could not get BaseContainer for camera '{name_before}'"
                    )

            # Rename - process *after* other properties in case identifier was 'name'
            requested_new_name = properties.get("name")
            if isinstance(requested_new_name, str):
                new_name_stripped = requested_new_name.strip()
                if new_name_stripped and new_name_stripped != name_before:
                    self.log(
                        f"[MODIFY] Renaming '{name_before}' to '{new_name_stripped}'"
                    )
                    obj.SetName(new_name_stripped)
                    name_after_rename = obj.GetName()
                    modified["name"] = {
                        "from": name_before,
                        "requested": new_name_stripped,
                        "to": name_after_rename,
                    }
                    something_changed = True
                    self.register_object_name(
                        obj, new_name_stripped
                    )  # Register with requested new name

            # Finalize
            if something_changed:
                doc.AddUndo(c4d.UNDOTYPE_CHANGE, obj)
                c4d.EventAdd()
            else:
                self.log(f"No modifications applied to '{name_before}'")

            doc.EndUndo()  # End undo block

            # Contextual Return
            final_name = obj.GetName()
            guid = str(obj.GetGUID())
            pos_vec = obj.GetAbsPos()
            rot_vec_rad = obj.GetAbsRot()
            scale_vec = obj.GetAbsScale()

            if "name" not in modified and final_name != name_before:
                self.log(
                    f"Warning: Object name changed unexpectedly from '{name_before}' to '{final_name}'. Updating registry."
                )
                self.register_object_name(obj, name_before)

            return {
                "object": {
                    "requested_identifier": identifier,
                    "was_guid": use_guid,
                    "actual_name": final_name,
                    "guid": guid,
                    "name_before": name_before,
                    "modified_properties": modified,
                    "current_position": [pos_vec.x, pos_vec.y, pos_vec.z],
                    "current_rotation": [
                        c4d.utils.RadToDeg(r)
                        for r in [rot_vec_rad.x, rot_vec_rad.y, rot_vec_rad.z]
                    ],
                    "current_scale": [scale_vec.x, scale_vec.y, scale_vec.z],
                }
            }

        except Exception as e:
            if doc and doc.IsUndoEnabled():
                doc.EndUndo()  # Ensure undo ended
            error_msg = f"Unexpected error modifying object '{name_before}': {str(e)}"
            self.log(f"[**ERROR**] {error_msg}\n{traceback.format_exc()}")
            return {"error": error_msg, "traceback": traceback.format_exc()}

    def handle_apply_material(self, command):
        """Handle apply_material command with GUID support."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        material_name = command.get("material_name", "")
        identifier = None
        use_guid = False

        # --- GUID Detection Improved ---
        if command.get("guid"):
            identifier = command.get("guid")
            use_guid = True
            self.log(f"[APPLY MAT] Using GUID identifier: '{identifier}'")
        elif command.get("object_name"):
            identifier = command.get("object_name")
            if "-" in str(identifier) and len(str(identifier)) > 30:
                self.log(
                    f"[APPLY MAT] Identifier '{identifier}' looks like GUID, treating as GUID."
                )
                use_guid = True
            else:
                use_guid = False
                self.log(f"[APPLY MAT] Using Name identifier: '{identifier}'")
        else:
            return {"error": "No object identifier ('guid' or 'object_name') provided."}

        # Find object
        obj = self.find_object_by_name(doc, identifier, use_guid=use_guid)
        if obj is None:
            search_type = "GUID" if use_guid else "Name"
            return {
                "error": f"Object '{identifier}' (searched by {search_type}) not found."
            }

        # Find material
        mat = self._find_material_by_name(doc, material_name)
        if mat is None:
            return {"error": f"Material not found: {material_name}"}

        material_type = command.get("material_type", "standard").lower()
        projection_type = command.get("projection_type", "cubic").lower()
        auto_uv = command.get("auto_uv", False)
        procedural = command.get("procedural", False)

        try:
            doc.StartUndo()

            # Create and configure texture tag
            tag = c4d.TextureTag()
            if not tag:
                raise RuntimeError("Failed to create TextureTag")
            tag.SetMaterial(mat)

            proj_map = {
                "cubic": c4d.TEXTURETAG_PROJECTION_CUBIC,
                "spherical": c4d.TEXTURETAG_PROJECTION_SPHERICAL,
                "flat": c4d.TEXTURETAG_PROJECTION_FLAT,
                "cylindrical": c4d.TEXTURETAG_PROJECTION_CYLINDRICAL,
                "frontal": c4d.TEXTURETAG_PROJECTION_FRONTAL,
                "uvw": c4d.TEXTURETAG_PROJECTION_UVW,
            }
            tag[c4d.TEXTURETAG_PROJECTION] = proj_map.get(
                projection_type, c4d.TEXTURETAG_PROJECTION_UVW
            )

            obj.InsertTag(tag)
            doc.AddUndo(c4d.UNDOTYPE_NEW, tag)

            # Auto UV generation
            if auto_uv:
                self.log(
                    f"[APPLY MAT] Attempting auto UV generation for '{obj.GetName()}'"
                )
                try:
                    if obj.IsInstanceOf(c4d.Opolygon):
                        uvw_tag = obj.GetTag(c4d.Tuvw)
                        if not uvw_tag:
                            uvw_tag = obj.MakeTag(c4d.Tuvw)
                            if uvw_tag:
                                doc.AddUndo(c4d.UNDOTYPE_NEW, uvw_tag)
                            else:
                                self.log("Warning: Failed to create UVW tag.")

                        if uvw_tag:
                            c4d.plugins.CallCommand(12205)  # Optimal Cubic Mapping
                            self.log("Executed Optimal (Cubic) UV mapping command.")
                        else:
                            self.log(
                                "Warning: Could not get or create UVW tag for auto UV."
                            )
                    else:
                        self.log(
                            f"Warning: Auto UV skipped, object '{obj.GetName()}' not a polygon."
                        )
                except Exception as e_uv:
                    self.log(
                        f"[**ERROR**] Error during auto UV generation: {str(e_uv)}"
                    )

            # Handle Redshift
            if (
                material_type == "redshift"
                and hasattr(c4d, "modules")
                and hasattr(c4d.modules, "redshift")
            ):
                self.log(
                    f"[APPLY MAT] Checking Redshift setup for material '{mat.GetName()}'"
                )
                try:
                    redshift = c4d.modules.redshift
                    rs_id = getattr(c4d, "ID_REDSHIFT_MATERIAL", 1036224)

                    if mat.GetType() != rs_id:
                        self.log(
                            f"Converting material '{mat.GetName()}' to Redshift (ID: {rs_id})"
                        )
                        rs_mat = c4d.BaseMaterial(rs_id)
                        if not rs_mat:
                            raise RuntimeError("Failed to create Redshift material")

                        rs_mat.SetName(f"RS_{mat.GetName()}")
                        doc.InsertMaterial(rs_mat)
                        doc.AddUndo(c4d.UNDOTYPE_NEW, rs_mat)

                        try:
                            if hasattr(c4d, "REDSHIFT_MATERIAL_DIFFUSE_COLOR"):
                                rs_mat[c4d.REDSHIFT_MATERIAL_DIFFUSE_COLOR] = mat[
                                    c4d.MATERIAL_COLOR_COLOR
                                ]
                        except Exception as e_color_copy:
                            self.log(
                                f"Warning: Could not copy color during RS conversion: {e_color_copy}"
                            )

                        try:
                            import maxon

                            ns_id = maxon.Id(
                                "com.redshift3d.redshift4c4d.class.nodespace"
                            )
                            node_rs_mat = c4d.NodeMaterial(rs_mat)
                            if node_rs_mat and not node_rs_mat.HasSpace(ns_id):
                                node_rs_mat.CreateDefaultGraph(ns_id)
                                self.log("Created default Redshift node graph.")
                        except Exception as e_graph:
                            self.log(
                                f"Warning: Failed to create Redshift graph: {e_graph}"
                            )

                        if procedural:
                            try:
                                node_space = redshift.GetRSMaterialNodeSpace(rs_mat)
                                root = redshift.GetRSMaterialRootShader(rs_mat)
                                if node_space and root:
                                    tex_node = (
                                        redshift.RSMaterialNodeCreator.CreateNode(
                                            node_space,
                                            redshift.RSMaterialNodeType.TEXTURE,
                                            "RS::TextureNode",
                                        )
                                    )
                                    if tex_node:
                                        tex_node[redshift.TEXTURE_TYPE] = (
                                            redshift.TEXTURE_NOISE
                                        )
                                        redshift.CreateConnectionBetweenNodes(
                                            node_space,
                                            tex_node,
                                            "outcolor",
                                            root,
                                            "diffuse_color",
                                        )
                                        self.log(
                                            "Connected procedural Noise node to diffuse color."
                                        )
                                    else:
                                        self.log(
                                            "Warning: Failed to create procedural texture node."
                                        )
                            except Exception as e_proc:
                                self.log(
                                    f"Warning: Error setting up procedural RS nodes: {e_proc}"
                                )

                        tag.SetMaterial(rs_mat)
                        mat = rs_mat
                        doc.AddUndo(c4d.UNDOTYPE_CHANGE, tag)
                        self.log(
                            f"Swapped tag to use new Redshift material '{rs_mat.GetName()}'"
                        )

                except Exception as e_rs_setup:
                    self.log(
                        f"[**ERROR**] Error during Redshift setup: {str(e_rs_setup)}"
                    )

            doc.EndUndo()
            c4d.EventAdd()

            return {
                "success": True,
                "message": f"Applied material '{mat.GetName()}' to object '{obj.GetName()}'.",
                "object_name": obj.GetName(),
                "object_guid": str(obj.GetGUID()),
                "material_name": mat.GetName(),
                "material_type_id": mat.GetType(),
                "projection": projection_type,
                "auto_uv_attempted": auto_uv,
            }

        except Exception as e:
            doc.EndUndo()
            err = f"Error applying material '{material_name}' to '{obj.GetName()}': {str(e)}"
            self.log(f"[**ERROR**] {err}\n{traceback.format_exc()}")
            return {"error": err, "traceback": traceback.format_exc()}

    # def handle_render_to_file(self, doc, frame, width, height, output_path=None):
    #     """Render a frame to file, with optional base64 and fallback output path."""
    #     import os
    #     import tempfile
    #     import time
    #     import base64
    #     import c4d.storage
    #     import traceback

    #     try:
    #         start_time = time.time()

    #         # Clone active render settings
    #         render_data = doc.GetActiveRenderData()
    #         if not render_data:
    #             return {"error": "No active RenderData found"}

    #         rd_clone = render_data.GetClone()
    #         if not rd_clone:
    #             return {"error": "Failed to clone render settings"}

    #         # Update render settings
    #         settings = rd_clone.GetData()
    #         settings[c4d.RDATA_XRES] = float(width)
    #         settings[c4d.RDATA_YRES] = float(height)
    #         settings[c4d.RDATA_PATH] = output_path or os.path.join(
    #             tempfile.gettempdir(), "temp_render_output.png"
    #         )

    #         settings[c4d.RDATA_RENDERENGINE] = c4d.RDATA_RENDERENGINE_STANDARD
    #         settings[c4d.RDATA_FRAMESEQUENCE] = c4d.RDATA_FRAMESEQUENCE_CURRENTFRAME
    #         settings[c4d.RDATA_SAVEIMAGE] = False

    #         # render_data.SetData(settings)
    #         # Create temp RenderData container
    #         # Insert actual RenderData object into the scene with settings
    #         temp_rd = c4d.documents.RenderData()
    #         temp_rd.SetData(settings)
    #         doc.InsertRenderData(temp_rd)

    #         # Update document time/frame
    #         if isinstance(frame, dict):
    #             frame = frame.get("frame", 0)
    #         doc.SetTime(c4d.BaseTime(frame, doc.GetFps()))

    #         doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)

    #         # Create target bitmap
    #         bmp = c4d.bitmaps.BaseBitmap()
    #         if not bmp.Init(int(width), int(height)):
    #             return {"error": "Failed to initialize bitmap"}

    #         self.log(f"[RENDER] Rendering frame {frame} at {width}x{height}...")
    #         self.log(f"[RENDER DEBUG] Using RenderData name: {temp_rd.GetName()}")

    #         self.log(
    #             f"[RENDER DEBUG] Width: {settings[c4d.RDATA_XRES]}, Height: {settings[c4d.RDATA_YRES]}"
    #         )

    #         # Render to bitmap
    #         result = c4d.documents.RenderDocument(
    #             doc,
    #             temp_rd.GetData(),
    #             bmp,
    #             c4d.RENDERFLAGS_EXTERNAL | c4d.RENDERFLAGS_NODOCUMENTCLONE,
    #             None,
    #         )

    #         if not result:
    #             self.log("[RENDER] RenderDocument returned False")
    #             return {"error": "RenderDocument failed"}

    #         # Fallback path if needed
    #         if not output_path:
    #             doc_name = doc.GetDocumentName() or "untitled"
    #             if doc_name.lower().endswith(".c4d"):
    #                 doc_name = doc_name[:-4]
    #             base_dir = doc.GetDocumentPath() or tempfile.gettempdir()
    #             output_path = os.path.join(base_dir, f"{doc_name}_snapshot_{frame}.png")

    #         # Choose format based on extension
    #         ext = os.path.splitext(output_path)[1].lower()
    #         format_map = {
    #             ".png": c4d.FILTER_PNG,
    #             ".jpg": c4d.FILTER_JPG,
    #             ".jpeg": c4d.FILTER_JPG,
    #             ".tif": c4d.FILTER_TIF,
    #             ".tiff": c4d.FILTER_TIF,
    #         }
    #         format_id = format_map.get(ext, c4d.FILTER_PNG)

    #         # Save image to file
    #         if not bmp.Save(output_path, format_id):
    #             self.log(f"[RENDER] Failed to save bitmap to file: {output_path}")
    #             return {"error": f"Failed to save image to: {output_path}"}

    #         # Optionally encode to base64 if PNG
    #         image_base64 = None
    #         if format_id == c4d.FILTER_PNG:
    #             mem_file = c4d.storage.MemoryFileWrite()
    #             if mem_file.Open(1024 * 1024):
    #                 if bmp.Save(mem_file, c4d.FILTER_PNG):
    #                     raw_bytes = mem_file.GetValue()
    #                     image_base64 = base64.b64encode(raw_bytes).decode("utf-8")
    #                     self.log("[RENDER] Base64 preview generated")
    #                 mem_file.Close()

    #         elapsed = round(time.time() - start_time, 3)

    #         return {
    #             "success": True,
    #             "frame": frame,
    #             "resolution": f"{width}x{height}",
    #             "output_path": output_path,
    #             "file_exists": os.path.exists(output_path),
    #             "image_base64": image_base64,
    #             "render_time": elapsed,
    #         }

    #     except Exception as e:
    #         self.log("[RENDER ] Exception during render_to_file")
    #         self.log(traceback.format_exc())

    #         return {"error": f"Exception during render: {str(e)}"}

    def handle_snapshot_scene(self, command=None):
        """
        Generates a snapshot: object list + base64 preview render.
        Uses the corrected core render logic via handle_render_preview_base64.
        """
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document for snapshot."}

        frame = doc.GetTime().GetFrame(doc.GetFps())
        width, height = 640, 360

        self.log(f"[C4D SNAPSHOT] Generating snapshot for frame {frame}...")

        # 1. List objects
        object_data = self.handle_list_objects()  # Runs via execute_on_main_thread
        objects = object_data.get("objects", [])

        # 2. Render preview - uses handle_render_preview_base64 which now uses corrected core logic
        render_command = {"width": width, "height": height, "frame": frame}
        render_result = self.handle_render_preview_base64(
            **render_command
        )  # Runs via execute_on_main_thread

        render_info = {}
        if render_result and render_result.get("success"):
            render_info = {
                "frame": render_result.get("frame", frame),
                "resolution": f"{render_result.get('width', width)}x{render_result.get('height', height)}",
                "image_base64": render_result.get("image_base64"),
                "render_time": render_result.get("render_time", 0.0),
                "format": render_result.get("format", "png"),
                "success": True,
            }
            self.log(f"[C4D SNAPSHOT] Render successful.")
        else:
            error_msg = render_result.get("error", "Unknown rendering error")
            render_info = {"error": error_msg, "success": False}
            self.log(f"[C4D SNAPSHOT] Render failed: {error_msg}")
            # Include traceback from render result if available
            if isinstance(render_result, dict) and "traceback" in render_result:
                render_info["traceback"] = render_result["traceback"]

        # 3. Return combined result
        return {
            "objects": objects,
            "render": render_info,
        }

    def handle_set_keyframe(self, command):
        """Set a keyframe on an object, supporting both GUID and name lookup."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        # --- Identifier Detection ---
        identifier = None
        use_guid = False
        if command.get("guid"):
            identifier = command.get("guid")
            use_guid = True
            self.log(f"[KEYFRAME] Using GUID identifier: '{identifier}'")
        elif command.get("object_name"):
            identifier = command.get("object_name")
            if "-" in str(identifier) and len(str(identifier)) > 30:
                use_guid = True
                self.log(
                    f"[KEYFRAME] Identifier '{identifier}' looks like GUID, treating as GUID."
                )
            else:
                use_guid = False
                self.log(f"[KEYFRAME] Using Name identifier: '{identifier}'")
        else:
            identifier = command.get("name")
            if identifier:
                use_guid = False
                self.log(
                    f"[KEYFRAME] Using 'name' key as Name identifier: '{identifier}'"
                )
            else:
                return {
                    "error": "No object identifier ('guid', 'object_name', or 'name') provided."
                }

        # Find object
        obj = self.find_object_by_name(doc, identifier, use_guid=use_guid)
        if obj is None:
            search_type = "GUID" if use_guid else "Name"
            return {
                "error": f"Object '{identifier}' (searched by {search_type}) not found for keyframing."
            }

        # --- Property, Frame, and Value ---
        property_type = (
            command.get("property_type") or command.get("property") or "position"
        ).lower()
        frame = command.get("frame", doc.GetTime().GetFrame(doc.GetFps()))
        value = command.get("value")
        if value is None:
            return {"error": "No 'value' provided for keyframe."}

        try:
            frame = int(frame)
        except (ValueError, TypeError):
            return {"error": f"Invalid frame: {frame}"}

        try:
            # --- Handle Different Property Types ---
            if "." in property_type:
                # Vector component property (e.g., position.x)
                parts = property_type.split(".")
                if len(parts) != 2:
                    return {
                        "error": f"Invalid property format: '{property_type}'. Use 'position.x' etc."
                    }

                base_property, component = parts
                property_map = {
                    "position": c4d.ID_BASEOBJECT_POSITION,
                    "rotation": c4d.ID_BASEOBJECT_ROTATION,
                    "scale": c4d.ID_BASEOBJECT_SCALE,
                    "color": c4d.LIGHT_COLOR if obj.GetType() == c4d.Olight else None,
                }
                component_map = {
                    "x": c4d.VECTOR_X,
                    "y": c4d.VECTOR_Y,
                    "z": c4d.VECTOR_Z,
                    "r": c4d.VECTOR_X,
                    "g": c4d.VECTOR_Y,
                    "b": c4d.VECTOR_Z,
                }

                if (
                    base_property not in property_map
                    or property_map[base_property] is None
                ):
                    return {
                        "error": f"Unsupported/invalid base property '{base_property}' for object type."
                    }
                if component not in component_map:
                    return {
                        "error": f"Unsupported component '{component}'. Use x, y, z, r, g, or b."
                    }

                if isinstance(value, list):
                    value = value[0] if value else 0.0

                result = self._set_vector_component_keyframe(
                    obj,
                    frame,
                    property_map[base_property],
                    component_map[component],
                    float(value),
                    base_property,
                    component,
                )
                if not result:
                    return {"error": f"Failed to set {property_type} keyframe"}

            elif property_type in ["position", "rotation", "scale"]:
                # Full vector properties
                property_ids = {
                    "position": c4d.ID_BASEOBJECT_POSITION,
                    "rotation": c4d.ID_BASEOBJECT_ROTATION,
                    "scale": c4d.ID_BASEOBJECT_SCALE,
                }

                if isinstance(value, (int, float)):
                    value = [float(value)] * 3
                elif isinstance(value, list):
                    if len(value) == 1:
                        value = [float(value[0])] * 3
                    elif len(value) == 2:
                        value = [float(value[0]), float(value[1]), 0.0]
                    elif len(value) > 3:
                        value = [float(v) for v in value[:3]]
                    else:
                        value = [float(v) for v in value]
                else:
                    return {
                        "error": f"{property_type.capitalize()} value must be a number or a list [x,y,z]."
                    }

                if len(value) != 3:
                    return {
                        "error": f"{property_type.capitalize()} value must have 3 components."
                    }

                result = self._set_vector_keyframe(
                    obj, frame, property_ids[property_type], value, property_type
                )
                if not result:
                    return {"error": f"Failed to set {property_type} keyframe"}

            elif obj.GetType() == c4d.Olight and property_type in [
                "intensity",
                "color",
            ]:
                if property_type == "intensity":
                    if isinstance(value, list):
                        value = value[0] if value else 0.0
                    result = self._set_scalar_keyframe(
                        obj,
                        frame,
                        c4d.LIGHT_BRIGHTNESS,
                        c4d.DTYPE_REAL,
                        float(value) / 100.0,
                        "intensity",
                    )
                    if not result:
                        return {"error": "Failed to set intensity keyframe"}

                elif property_type == "color":
                    if not isinstance(value, list) or len(value) < 3:
                        return {"error": "Color must be a list [r,g,b]."}
                    result = self._set_vector_keyframe(
                        obj, frame, c4d.LIGHT_COLOR, value[:3], "color"
                    )
                    if not result:
                        return {"error": "Failed to set color keyframe"}

            else:
                return {
                    "error": f"Unsupported property type '{property_type}' for object '{obj.GetName()}'."
                }

            # --- Success ---
            return {
                "keyframe_set": {
                    "object_name": obj.GetName(),
                    "object_guid": str(obj.GetGUID()),
                    "property": property_type,
                    "value_set": value,
                    "frame": frame,
                    "success": True,
                }
            }

        except Exception as e:
            self.log(
                f"[**ERROR**] Error setting keyframe: {str(e)}\n{traceback.format_exc()}"
            )
            return {
                "error": f"Error setting keyframe: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def _set_position_keyframe(self, obj, frame, position):
        """Set a position keyframe for an object at a specific frame.

        Args:
            obj: The Cinema 4D object to keyframe
            frame: The frame number
            position: A list of [x, y, z] coordinates

        Returns:
            True if successful, False otherwise
        """
        if not obj or not isinstance(position, list) or len(position) < 3:
            self.log(f"[C4D KEYFRAME] Invalid object or position for keyframe")
            return False

        try:
            # Get the active document and time
            doc = c4d.documents.GetActiveDocument()

            # Log what we're doing
            self.log(
                f"[C4D KEYFRAME] Setting position keyframe for {obj.GetName()} at frame {frame} to {position}"
            )

            # Create the position vector from the list
            pos = c4d.Vector(position[0], position[1], position[2])

            # Set the object's position
            obj.SetAbsPos(pos)

            # Create track or get existing track for position
            track_x = obj.FindCTrack(
                c4d.DescID(
                    c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION, c4d.DTYPE_VECTOR, 0),
                    c4d.DescLevel(c4d.VECTOR_X, c4d.DTYPE_REAL, 0),
                )
            )
            if track_x is None:
                track_x = c4d.CTrack(
                    obj,
                    c4d.DescID(
                        c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION, c4d.DTYPE_VECTOR, 0),
                        c4d.DescLevel(c4d.VECTOR_X, c4d.DTYPE_REAL, 0),
                    ),
                )
                obj.InsertTrackSorted(track_x)

            track_y = obj.FindCTrack(
                c4d.DescID(
                    c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION, c4d.DTYPE_VECTOR, 0),
                    c4d.DescLevel(c4d.VECTOR_Y, c4d.DTYPE_REAL, 0),
                )
            )
            if track_y is None:
                track_y = c4d.CTrack(
                    obj,
                    c4d.DescID(
                        c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION, c4d.DTYPE_VECTOR, 0),
                        c4d.DescLevel(c4d.VECTOR_Y, c4d.DTYPE_REAL, 0),
                    ),
                )
                obj.InsertTrackSorted(track_y)

            track_z = obj.FindCTrack(
                c4d.DescID(
                    c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION, c4d.DTYPE_VECTOR, 0),
                    c4d.DescLevel(c4d.VECTOR_Z, c4d.DTYPE_REAL, 0),
                )
            )
            if track_z is None:
                track_z = c4d.CTrack(
                    obj,
                    c4d.DescID(
                        c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION, c4d.DTYPE_VECTOR, 0),
                        c4d.DescLevel(c4d.VECTOR_Z, c4d.DTYPE_REAL, 0),
                    ),
                )
                obj.InsertTrackSorted(track_z)

            # Create time object for the keyframe
            time = c4d.BaseTime(frame, doc.GetFps())

            # Set the keyframes for each axis
            curve_x = track_x.GetCurve()
            key_x = curve_x.AddKey(time)
            if key_x is not None and key_x["key"] is not None:
                key_x["key"].SetValue(curve_x, position[0])

            curve_y = track_y.GetCurve()
            key_y = curve_y.AddKey(time)
            if key_y is not None and key_y["key"] is not None:
                key_y["key"].SetValue(curve_y, position[1])

            curve_z = track_z.GetCurve()
            key_z = curve_z.AddKey(time)
            if key_z is not None and key_z["key"] is not None:
                key_z["key"].SetValue(curve_z, position[2])

            # Update the document
            c4d.EventAdd()

            self.log(
                f"[C4D KEYFRAME] Successfully set keyframe for {obj.GetName()} at frame {frame}"
            )
            return True

        except Exception as e:
            self.log(f"[C4D KEYFRAME] Error setting position keyframe: {str(e)}")
            return False

    def _set_vector_keyframe(self, obj, frame, property_id, value, property_name):
        """Set a keyframe for a vector property of an object.

        Args:
            obj: The Cinema 4D object to keyframe
            frame: The frame number
            property_id: The ID of the property (e.g., c4d.ID_BASEOBJECT_POSITION)
            value: A list of [x, y, z] values
            property_name: Name of the property for logging

        Returns:
            True if successful, False otherwise
        """
        if not obj or not isinstance(value, list) or len(value) < 3:
            self.log(
                f"[C4D KEYFRAME] Invalid object or {property_name} value for keyframe"
            )
            return False

        try:
            # Get the active document and time
            doc = c4d.documents.GetActiveDocument()

            # Log what we're doing
            self.log(
                f"[C4D KEYFRAME] Setting {property_name} keyframe for {obj.GetName()} at frame {frame} to {value}"
            )

            # Create the vector from the list
            vec = c4d.Vector(value[0], value[1], value[2])

            # Set the object's property value based on property type
            if property_id == c4d.ID_BASEOBJECT_POSITION:
                obj.SetAbsPos(vec)
            elif property_id == c4d.ID_BASEOBJECT_ROTATION:
                # Convert degrees to radians for rotation
                rot_rad = c4d.Vector(
                    c4d.utils.DegToRad(value[0]),
                    c4d.utils.DegToRad(value[1]),
                    c4d.utils.DegToRad(value[2]),
                )
                obj.SetRotation(rot_rad)
            elif property_id == c4d.ID_BASEOBJECT_SCALE:
                obj.SetScale(vec)
            elif property_id == c4d.LIGHT_COLOR:
                obj[c4d.LIGHT_COLOR] = vec

            # Component IDs for vector properties
            component_ids = [c4d.VECTOR_X, c4d.VECTOR_Y, c4d.VECTOR_Z]
            component_names = ["X", "Y", "Z"]

            # Create tracks and set keyframes for each component
            for i, component_id in enumerate(component_ids):
                # Create or get track for this component
                track = obj.FindCTrack(
                    c4d.DescID(
                        c4d.DescLevel(property_id, c4d.DTYPE_VECTOR, 0),
                        c4d.DescLevel(component_id, c4d.DTYPE_REAL, 0),
                    )
                )

                if track is None:
                    track = c4d.CTrack(
                        obj,
                        c4d.DescID(
                            c4d.DescLevel(property_id, c4d.DTYPE_VECTOR, 0),
                            c4d.DescLevel(component_id, c4d.DTYPE_REAL, 0),
                        ),
                    )
                    obj.InsertTrackSorted(track)

                # Create time object for the keyframe
                time = c4d.BaseTime(frame, doc.GetFps())

                # Set the keyframe
                curve = track.GetCurve()
                key = curve.AddKey(time)

                # Convert rotation values from degrees to radians if necessary
                component_value = value[i]
                if property_id == c4d.ID_BASEOBJECT_ROTATION:
                    component_value = c4d.utils.DegToRad(component_value)

                if key is not None and key["key"] is not None:
                    key["key"].SetValue(curve, component_value)
                    self.log(
                        f"[C4D KEYFRAME] Set {property_name}.{component_names[i]} keyframe to {value[i]}"
                    )

            # Update the document
            c4d.EventAdd()

            self.log(
                f"[C4D KEYFRAME] Successfully set {property_name} keyframe for {obj.GetName()} at frame {frame}"
            )

            return True
        except Exception as e:
            self.log(f"[C4D KEYFRAME] Error setting {property_name} keyframe: {str(e)}")
            return False

    def _set_scalar_keyframe(
        self, obj, frame, property_id, data_type, value, property_name
    ):
        """Set a keyframe for a scalar property of an object.

        Args:
            obj: The Cinema 4D object to keyframe
            frame: The frame number
            property_id: The ID of the property (e.g., c4d.LIGHT_BRIGHTNESS)
            data_type: The data type of the property (e.g., c4d.DTYPE_REAL)
            value: The scalar value
            property_name: Name of the property for logging

        Returns:
            True if successful, False otherwise
        """
        if not obj:
            self.log(f"[C4D KEYFRAME] Invalid object for {property_name} keyframe")
            return False

        try:
            # Get the active document and time
            doc = c4d.documents.GetActiveDocument()

            # Log what we're doing
            self.log(
                f"[C4D KEYFRAME] Setting {property_name} keyframe for {obj.GetName()} at frame {frame} to {value}"
            )

            # Set the object's property value
            obj[property_id] = value

            # Create or get track for this property
            track = obj.FindCTrack(c4d.DescID(c4d.DescLevel(property_id, data_type, 0)))

            if track is None:
                track = c4d.CTrack(
                    obj, c4d.DescID(c4d.DescLevel(property_id, data_type, 0))
                )
                obj.InsertTrackSorted(track)

            # Create time object for the keyframe
            time = c4d.BaseTime(frame, doc.GetFps())

            # Set the keyframe
            curve = track.GetCurve()
            key = curve.AddKey(time)

            if key is not None and key["key"] is not None:
                key["key"].SetValue(curve, value)

            # Update the document
            c4d.EventAdd()

            self.log(
                f"[C4D KEYFRAME] Successfully set {property_name} keyframe for {obj.GetName()} at frame {frame}"
            )

            return True
        except Exception as e:
            self.log(f"[C4D KEYFRAME] Error setting {property_name} keyframe: {str(e)}")
            return False

    def _set_vector_component_keyframe(
        self,
        obj,
        frame,
        property_id,
        component_id,
        value,
        property_name,
        component_name,
    ):
        """Set a keyframe for a single component of a vector property.

        Args:
            obj: The Cinema 4D object to keyframe
            frame: The frame number
            property_id: The ID of the property (e.g., c4d.ID_BASEOBJECT_POSITION)
            component_id: The ID of the component (e.g., c4d.VECTOR_X)
            value: The scalar value for the component
            property_name: Name of the property for logging
            component_name: Name of the component for logging

        Returns:
            True if successful, False otherwise
        """
        if not obj:
            self.log(
                f"[C4D KEYFRAME] Invalid object for {property_name}.{component_name} keyframe"
            )
            return False

        try:
            # Get the active document and time
            doc = c4d.documents.GetActiveDocument()

            # Log what we're doing
            self.log(
                f"[C4D KEYFRAME] Setting {property_name}.{component_name} keyframe for {obj.GetName()} at frame {frame} to {value}"
            )

            # Get the current vector value
            current_vec = None
            if property_id == c4d.ID_BASEOBJECT_POSITION:
                current_vec = obj.GetAbsPos()
            elif property_id == c4d.ID_BASEOBJECT_ROTATION:
                current_vec = obj.GetRotation()
                # For rotation, convert the input value from degrees to radians
                value = c4d.utils.DegToRad(value)
            elif property_id == c4d.ID_BASEOBJECT_SCALE:
                current_vec = obj.GetScale()
            elif property_id == c4d.LIGHT_COLOR:
                current_vec = obj[c4d.LIGHT_COLOR]

            if current_vec is None:
                self.log(f"[C4D KEYFRAME] Could not get current {property_name} value")
                return False

            # Update the specific component
            if component_id == c4d.VECTOR_X:
                current_vec.x = value
            elif component_id == c4d.VECTOR_Y:
                current_vec.y = value
            elif component_id == c4d.VECTOR_Z:
                current_vec.z = value

            # Set the updated vector back to the object
            if property_id == c4d.ID_BASEOBJECT_POSITION:
                obj.SetAbsPos(current_vec)
            elif property_id == c4d.ID_BASEOBJECT_ROTATION:
                obj.SetRotation(current_vec)
            elif property_id == c4d.ID_BASEOBJECT_SCALE:
                obj.SetScale(current_vec)
            elif property_id == c4d.LIGHT_COLOR:
                obj[c4d.LIGHT_COLOR] = current_vec

            # Create or get track for this component
            track = obj.FindCTrack(
                c4d.DescID(
                    c4d.DescLevel(property_id, c4d.DTYPE_VECTOR, 0),
                    c4d.DescLevel(component_id, c4d.DTYPE_REAL, 0),
                )
            )

            if track is None:
                track = c4d.CTrack(
                    obj,
                    c4d.DescID(
                        c4d.DescLevel(property_id, c4d.DTYPE_VECTOR, 0),
                        c4d.DescLevel(component_id, c4d.DTYPE_REAL, 0),
                    ),
                )
                obj.InsertTrackSorted(track)

            # Create time object for the keyframe
            time = c4d.BaseTime(frame, doc.GetFps())

            # Set the keyframe
            curve = track.GetCurve()
            key = curve.AddKey(time)

            if key is not None and key["key"] is not None:
                key["key"].SetValue(curve, value)

            # Update the document
            c4d.EventAdd()

            self.log(
                f"[C4D KEYFRAME] Successfully set {property_name}.{component_name} keyframe for {obj.GetName()} at frame {frame}"
            )

            return True
        except Exception as e:
            self.log(
                f"[C4D KEYFRAME] Error setting {property_name}.{component_name} keyframe: {str(e)}"
            )
            return False

    def handle_save_scene(self, command):
        """Handle save_scene command."""
        file_path = command.get("file_path", "")
        if not file_path:
            return {"error": "No file path provided"}

        # Log the save request
        self.log(f"[C4D SAVE] Saving scene to: {file_path}")

        # Define function to execute on main thread
        def save_scene_on_main_thread(doc, file_path):
            try:
                # Ensure the directory exists
                directory = os.path.dirname(file_path)
                if directory and not os.path.exists(directory):
                    os.makedirs(directory)

                # Check file extension
                _, extension = os.path.splitext(file_path)
                if not extension:
                    file_path += ".c4d"  # Add default extension
                elif extension.lower() != ".c4d":
                    file_path = file_path[: -len(extension)] + ".c4d"

                # Save document
                self.log(f"[C4D SAVE] Saving to: {file_path}")
                if not c4d.documents.SaveDocument(
                    doc,
                    file_path,
                    c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST,
                    c4d.FORMAT_C4DEXPORT,
                ):
                    return {"error": f"Failed to save document to {file_path}"}

                # R2025.1 fix: Update document name and path to fix "Untitled-1" issue
                try:
                    # Update the document name
                    doc.SetDocumentName(os.path.basename(file_path))

                    # Update document path
                    doc.SetDocumentPath(os.path.dirname(file_path))

                    # Ensure UI is updated
                    c4d.EventAdd()
                    self.log(
                        f"[C4D SAVE] Updated document name and path for {file_path}"
                    )
                except Exception as e:
                    self.log(
                        f"[C4D SAVE] ## Warning ##: Could not update document name/path: {str(e)}"
                    )

                return {
                    "success": True,
                    "file_path": file_path,
                    "message": f"Scene saved to {file_path}",
                }
            except Exception as e:
                return {"error": f"Error saving scene: {str(e)}"}

        # Execute the save function on the main thread with extended timeout
        doc = c4d.documents.GetActiveDocument()
        result = self.execute_on_main_thread(
            save_scene_on_main_thread, args=(doc, file_path), _timeout=60
        )
        return result

    def handle_load_scene(self, command):
        """Handle load_scene command with improved path handling."""
        file_path = command.get("file_path", "")
        if not file_path:
            return {"error": "No file path provided"}

        # Normalize path to handle different path formats
        file_path = os.path.normpath(os.path.expanduser(file_path))

        # Log the normalized path
        self.log(f"[C4D LOAD] Normalized file path: {file_path}")

        # If path is not absolute, try to resolve it relative to current directory
        if not os.path.isabs(file_path):
            current_doc_path = c4d.documents.GetActiveDocument().GetDocumentPath()
            if current_doc_path:
                possible_path = os.path.join(current_doc_path, file_path)
                self.log(
                    f"[C4D LOAD] Trying path relative to current document: {possible_path}"
                )
                if os.path.exists(possible_path):
                    file_path = possible_path

        # Check if file exists
        if not os.path.exists(file_path):
            # Try to find the file in common locations
            common_dirs = [
                os.path.expanduser("~/Documents"),
                os.path.expanduser("~/Desktop"),
                "/Users/Shared/",
                ".",
                # Add the current working directory
                os.getcwd(),
                # Add the directory containing the plugin
                os.path.dirname(os.path.abspath(__file__)),
                # Add parent directory of plugin
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ]

            # Try with different extensions
            filename = os.path.basename(file_path)
            basename, ext = os.path.splitext(filename)
            if not ext:
                filenames_to_try = [filename, filename + ".c4d"]
            else:
                filenames_to_try = [filename]

            # Report search paths
            self.log(
                f"[C4D LOAD] Searching for file '{filename}' in multiple locations"
            )

            # Try each directory and filename combination
            for directory in common_dirs:
                for fname in filenames_to_try:
                    possible_path = os.path.join(directory, fname)
                    self.log(f"[C4D LOAD] Trying path: {possible_path}")
                    if os.path.exists(possible_path):
                        file_path = possible_path
                        self.log(f"[C4D LOAD] Found file at: {file_path}")
                        break
                else:
                    continue  # Continue to next directory if file not found
                break  # Break main loop if file found
            else:
                # Try a case-insensitive search as a last resort
                for directory in common_dirs:
                    if os.path.exists(directory):
                        for file in os.listdir(directory):
                            if file.lower() == filename.lower():
                                file_path = os.path.join(directory, file)
                                self.log(
                                    f"[C4D LOAD] Found file with case-insensitive match: {file_path}"
                                )
                                break
                        else:
                            continue  # Continue to next directory if file not found
                        break  # Break main loop if file found
                else:
                    return {"error": f"File not found: {file_path}"}

        # Log the load request
        self.log(f"[C4D LOAD] Loading scene from: {file_path}")

        # Define function to execute on main thread
        def load_scene_on_main_thread(file_path):
            try:
                # Load the document
                new_doc = c4d.documents.LoadDocument(file_path, c4d.SCENEFILTER_NONE)
                if not new_doc:
                    return {"error": f"Failed to load document from {file_path}"}

                # Set the new document as active
                c4d.documents.SetActiveDocument(new_doc)

                # Add the document to the documents list
                # (only needed if the document wasn't loaded by the document manager)
                c4d.documents.InsertBaseDocument(new_doc)

                # Update Cinema 4D
                c4d.EventAdd()

                return {
                    "success": True,
                    "file_path": file_path,
                    "message": f"Scene loaded from {file_path}",
                }
            except Exception as e:
                return {"error": f"Error loading scene: {str(e)}"}

        # Execute the load function on the main thread with extended timeout.
        # NOTE: args MUST be a tuple. Passing the bare string `file_path` here
        # would cause `func(*args)` to unpack character-by-character (each
        # letter as a separate positional arg) — long-standing upstream bug.
        result = self.execute_on_main_thread(
            load_scene_on_main_thread, args=(file_path,), _timeout=60
        )
        return result

    def handle_execute_python(self, command):
        """Handle execute_python command with improved output capturing and error handling."""
        code = command.get("code", "")
        if not code:
            # Try alternative parameter names
            code = command.get("script", "")
            if not code:
                self.log(
                    "[C4D PYTHON] Error: No Python code provided in 'code' or 'script' parameters"
                )
                return {"error": "No Python code provided"}

        # NOTE: execute_python runs arbitrary Python with full builtins access
        # (Python's exec() injects __builtins__ into any namespace it can't find
        # them in, which means `__import__("os").system(...)` reaches the host
        # shell from inside this handler). The earlier keyword blacklist
        # ('os.system', 'subprocess', 'exec(', 'eval(', 'import os') was trivially
        # bypassable and gave a false sense of safety, so it's been removed in
        # favour of the auth-token gate at the dispatcher level.
        # Treat this as: trusted-local-only, gated by MCP_AUTH_TOKEN.
        self.log(f"[C4D PYTHON] Executing Python code")

        # Prepare improved capture function with thread-safe collection
        captured_output = []
        import sys
        import traceback
        from io import StringIO

        # Execute the code on the main thread
        def execute_code():
            # Save original stdout
            original_stdout = sys.stdout
            # Create a StringIO object to capture output
            string_io = StringIO()

            try:
                # Redirect stdout to our capture object
                sys.stdout = string_io

                # Create a new namespace with limited globals
                sandbox = {
                    "c4d": c4d,
                    "math": __import__("math"),
                    "random": __import__("random"),
                    "time": __import__("time"),
                    "json": __import__("json"),
                    "doc": c4d.documents.GetActiveDocument(),
                }

                # Print startup message
                print("[C4D PYTHON] Starting script execution")

                # Execute the code
                exec(code, sandbox)

                # Print completion message
                print("[C4D PYTHON] Script execution completed")

                # Get any variables that were set in the code
                result_vars = {
                    k: v
                    for k, v in sandbox.items()
                    if not k.startswith("__")
                    and k not in ["c4d", "math", "random", "time", "json", "doc"]
                }

                # Get captured output
                full_output = string_io.getvalue()

                # Process variables to make them serializable
                processed_vars = {}
                for k, v in result_vars.items():
                    try:
                        # Try to make the value JSON-serializable
                        if hasattr(v, "__dict__"):
                            processed_vars[k] = f"<{type(v).__name__} object>"
                        else:
                            processed_vars[k] = str(v)
                    except:
                        processed_vars[k] = f"<{type(v).__name__} object>"

                # Return results
                return {
                    "success": True,
                    "output": full_output,
                    "variables": processed_vars,
                }

            except Exception as e:
                error_msg = f"Python execution error: {str(e)}"
                self.log(f"[C4D PYTHON] {error_msg}")

                # Get traceback info
                tb = traceback.format_exc()

                # Get any output captured before the error
                captured = string_io.getvalue()

                # Return error with details
                return {
                    "error": error_msg,
                    "traceback": tb,
                    "output": captured,
                }
            finally:
                # Restore original stdout
                sys.stdout = original_stdout

                # Close the StringIO object
                string_io.close()

        # Execute on main thread with extended timeout
        result = self.execute_on_main_thread(execute_code, _timeout=30)

        # Check for empty output and add warning
        if result.get("success") and not result.get("output").strip():
            self.log(
                "[C4D PYTHON] ## Warning ##: Script executed successfully but produced no output"
            )
            result["warning"] = "Script executed but produced no output"

        return result

    def handle_create_mograph_cloner(self, command):
        """Handle create_mograph_cloner command with context and fixed parameter names."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        requested_name = command.get("cloner_name", "MoGraph Cloner")
        mode = command.get("mode", "grid").lower()
        object_identifier = command.get("object_name", None)

        use_child_guid = False
        clone_child_provided = object_identifier is not None
        if clone_child_provided:
            identifier_str = str(object_identifier)
            if "-" in identifier_str and len(identifier_str) > 30:
                use_child_guid = True
            elif identifier_str.isdigit() or (
                identifier_str.startswith("-") and identifier_str[1:].isdigit()
            ):
                if len(identifier_str) > 10:
                    use_child_guid = True

        # Count Parsing (robust version from previous step)
        default_count = [3, 1, 3] if mode == "grid" else 10
        raw_count = command.get("count", default_count)
        count_vec = None
        count_scalar = None
        reported_count = raw_count
        try:
            if mode == "grid":
                if isinstance(raw_count, list) and len(raw_count) >= 3:
                    count_vec = c4d.Vector(
                        int(raw_count[0]), int(raw_count[1]), int(raw_count[2])
                    )
                    reported_count = [int(c) for c in count_vec]
                elif isinstance(raw_count, (int, float)):
                    count_vec = c4d.Vector(int(raw_count), 1, 1)
                    reported_count = [int(raw_count), 1, 1]
                else:
                    self.log(
                        f"[CLONER] ## Warning ## Invalid count '{raw_count}' for grid. Using defaults."
                    )
                    count_vec = c4d.Vector(3, 1, 3)
                    reported_count = [3, 1, 3]
            elif mode in ["linear", "radial", "object", "spline", "honeycomb"]:
                if isinstance(raw_count, list) and len(raw_count) >= 1:
                    count_scalar = int(raw_count[0])
                    reported_count = count_scalar
                elif isinstance(raw_count, (int, float)):
                    count_scalar = int(raw_count)
                    reported_count = count_scalar
                else:
                    self.log(
                        f"[CLONER] ## Warning ## Invalid count '{raw_count}' for {mode}. Using default 10."
                    )
                    count_scalar = 10
                    reported_count = 10
            else:
                self.log(
                    f"[CLONER] ## Warning ## Unsupported mode '{mode}'. Using default grid."
                )
                mode = "grid"
                count_vec = c4d.Vector(3, 1, 3)
                reported_count = [3, 1, 3]
        except (ValueError, TypeError) as e:
            self.log(
                f"[CLONER] ## Warning ## Error parsing count '{raw_count}': {e}. Using defaults."
            )
            if mode == "grid":
                count_vec = c4d.Vector(3, 1, 3)
                reported_count = [3, 1, 3]
            else:
                count_scalar = 10
                reported_count = 10

        self.log(
            f"[C4D CLONER] Creating: Name='{requested_name}', Mode='{mode}', Count='{reported_count}', Source='{object_identifier}' (GUID: {use_child_guid})"
        )

        clone_obj_target = None
        child_obj_details = {"source": "Default Cube"}
        child_guid = None
        child_actual_name = "Default Cube"
        if clone_child_provided:
            clone_obj_target = self.find_object_by_name(
                doc, object_identifier, use_guid=use_child_guid
            )
            if not clone_obj_target:
                search_type = "GUID" if use_child_guid else "Name"
                return {
                    "error": f"Object '{object_identifier}' (searched by {search_type}) not found to clone."
                }
            else:
                target_name = clone_obj_target.GetName()
                target_guid = str(clone_obj_target.GetGUID())
                child_obj_details["source"] = (
                    f"Existing Object: '{target_name}' (GUID: {target_guid})"
                )
                self.log(
                    f"[CLONER] Found clone object: '{target_name}' (GUID: {target_guid})"
                )

        def create_mograph_cloner_safe(
            doc, name, mode, count, count_vec, found_clone_object
        ):
            nonlocal child_guid, child_actual_name
            try:
                cloner = c4d.BaseObject(c4d.Omgcloner)
                if not cloner:
                    raise RuntimeError("Failed to create Cloner object")
                cloner.SetName(name)

                mode_ids = {
                    "linear": 0,
                    "radial": 2,
                    "grid": 1,
                    "object": 3,
                    "spline": 4,
                    "honeycomb": 5,
                }
                mode_id = mode_ids.get(mode, 1)

                doc.StartUndo()
                doc.InsertObject(cloner)
                doc.AddUndo(c4d.UNDOTYPE_NEW, cloner)
                cloner[c4d.ID_MG_MOTIONGENERATOR_MODE] = mode_id

                if found_clone_object:
                    child_obj = found_clone_object.GetClone()
                else:
                    child_obj = c4d.BaseObject(c4d.Ocube)
                    child_obj.SetName("Default Cube")
                    child_obj.SetAbsScale(c4d.Vector(0.5, 0.5, 0.5))
                if not child_obj:
                    raise RuntimeError("Failed to create/clone child object")

                doc.InsertObject(child_obj)
                doc.AddUndo(c4d.UNDOTYPE_NEW, child_obj)
                child_actual_name = child_obj.GetName()
                child_guid = str(child_obj.GetGUID())
                child_obj.InsertUnderLast(cloner)
                self.register_object_name(
                    child_obj,
                    (
                        found_clone_object.GetName()
                        if found_clone_object
                        else "Default Cube"
                    ),
                )

                mg_bc = cloner.GetDataInstance()
                if not mg_bc:
                    raise RuntimeError("Failed to get MoGraph BaseContainer")

                # --- FIXED: Use getattr for potentially missing constants ---
                if mode == "linear":
                    mg_bc[c4d.MG_LINEAR_COUNT] = count
                    # Use getattr for MG_LINEAR_PERSTEP, provide default vector if missing
                    perstep_id = getattr(c4d, "MG_LINEAR_PERSTEP", None)
                    mode_id_param = getattr(c4d, "MG_LINEAR_MODE", None)
                    perstep_mode_val = getattr(
                        c4d, "MG_LINEAR_MODE_PERSTEP", 0
                    )  # Default to 0 if missing

                    if perstep_id:
                        mg_bc[perstep_id] = c4d.Vector(0, 50, 0)
                    else:
                        self.log("[CLONER] ## Warning ## MG_LINEAR_PERSTEP not found.")
                    if mode_id_param:
                        mg_bc[mode_id_param] = perstep_mode_val
                    else:
                        self.log("[CLONER] ## Warning ## MG_LINEAR_MODE not found.")
                    self.log(f"[C4D CLONER] Set linear count: {count}")
                # --- END FIXED ---
                elif mode == "grid":
                    version = c4d.GetC4DVersion()
                    try:
                        if version >= 2025000 and hasattr(c4d, "MGGRIDARRAY_MODE"):
                            mg_bc[c4d.MGGRIDARRAY_MODE] = c4d.MGGRIDARRAY_MODE_ENDPOINT
                            mg_bc[c4d.MGGRIDARRAY_RESOLUTION] = count_vec
                            mg_bc[c4d.MGGRIDARRAY_SIZE] = c4d.Vector(200, 200, 200)
                            self.log(
                                f"[C4D CLONER] Using 2025+ MGGRIDARRAY_*; resolution: {count_vec}"
                            )
                        else:
                            if (
                                hasattr(c4d, "MG_GRID_COUNT")
                                and hasattr(c4d, "MG_GRID_MODE")
                                and hasattr(c4d, "MG_GRID_SIZE")
                            ):
                                mg_bc[c4d.MG_GRID_COUNT] = count_vec
                                mg_bc[c4d.MG_GRID_MODE] = c4d.MG_GRID_MODE_PERSTEP
                                mg_bc[c4d.MG_GRID_SIZE] = c4d.Vector(100, 100, 100)
                                self.log(
                                    f"[C4D CLONER] Using legacy MG_GRID_COUNT: {count_vec}, Mode: Per Step"
                                )
                            else:
                                if all(
                                    hasattr(c4d, attr)
                                    for attr in [
                                        "MG_GRID_COUNT_X",
                                        "MG_GRID_COUNT_Y",
                                        "MG_GRID_COUNT_Z",
                                        "MG_CLONER_SIZE",
                                    ]
                                ):
                                    mg_bc[c4d.MG_GRID_COUNT_X] = int(count_vec.x)
                                    mg_bc[c4d.MG_GRID_COUNT_Y] = int(count_vec.y)
                                    mg_bc[c4d.MG_GRID_COUNT_Z] = int(count_vec.z)
                                    mg_bc[c4d.MG_CLONER_SIZE] = c4d.Vector(
                                        200, 200, 200
                                    )
                                    self.log(
                                        f"[C4D CLONER] Using legacy MG_GRID_COUNT_X/Y/Z: {count_vec}, Size: 200"
                                    )
                                else:
                                    self.log(
                                        "[C4D CLONER] ## Warning ##: Could not find suitable grid parameters."
                                    )
                    except Exception as e_grid:
                        self.log(
                            f"[C4D CLONER] ## Warning ## Grid mode config failed: {e_grid}"
                        )
                elif mode == "radial":
                    if hasattr(c4d, "MG_POLY_COUNT") and hasattr(c4d, "MG_POLY_RADIUS"):
                        mg_bc[c4d.MG_POLY_COUNT] = count
                        mg_bc[c4d.MG_POLY_RADIUS] = 200
                        self.log(f"[C4D CLONER] Set radial count: {count}, Radius: 200")
                    else:
                        self.log(
                            "[C4D CLONER] ## Warning ##: Radial parameters not found."
                        )
                elif mode == "object":
                    self.log("[C4D CLONER] Object mode selected, requires linking.")
                    if not hasattr(c4d, "MG_OBJECT_LINK"):
                        self.log(
                            "[C4D CLONER] ## Warning ##: Object link parameter not found."
                        )

                if hasattr(c4d, "MGCLONER_MODE"):
                    cloner[c4d.MGCLONER_MODE] = c4d.MGCLONER_MODE_ITERATE

                doc.EndUndo()
                c4d.EventAdd()

                actual_cloner_name = cloner.GetName()
                cloner_guid = str(cloner.GetGUID())
                pos_vec = cloner.GetAbsPos()
                self.register_object_name(cloner, name)  # Use 'name' (requested name)

                return {
                    "cloner": {
                        "requested_name": name,
                        "actual_name": actual_cloner_name,
                        "guid": cloner_guid,
                        "type": mode,
                        "count_set": reported_count,
                        "position": [pos_vec.x, pos_vec.y, pos_vec.z],
                        "child_object": {
                            "source": child_obj_details["source"],
                            "actual_name": child_actual_name,
                            "guid": child_guid,
                        },
                    }
                }
            except Exception as e:
                doc.EndUndo()
                self.log(
                    f"[**ERROR**] Exception during cloner creation safe wrapper: {str(e)}\n{traceback.format_exc()}"
                )
                return {
                    "error": f"Exception during cloner creation: {str(e)}",
                    "traceback": traceback.format_exc(),
                }

        try:
            self.log("[C4D CLONER] Dispatching cloner creation to main thread")
            result = self.execute_on_main_thread(
                create_mograph_cloner_safe,
                args=(
                    doc,
                    requested_name,
                    mode,
                    count_scalar,
                    count_vec,
                    clone_obj_target,
                ),
                _timeout=30,
            )
            if isinstance(result, dict) and "error" in result:
                self.log(f"[C4D CLONER] Error: {result['error']}")
                return result
            return result
        except Exception as e:
            self.log(
                f"[**ERROR**] Exception in cloner handler dispatch: {str(e)}\n{traceback.format_exc()}"
            )
            return {
                "error": f"Exception dispatching cloner handler: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def handle_list_objects(self):
        """Handle list_objects command with comprehensive object detection including MoGraph objects."""
        doc = c4d.documents.GetActiveDocument()
        objects = []
        found_ids = set()  # Track object IDs to avoid duplicates

        # Function to recursively get all objects including children with improved traversal
        def get_objects_recursive(start_obj, depth=0):
            current_obj = start_obj
            while current_obj:
                try:
                    # Get object ID to avoid duplicates
                    obj_id = str(current_obj.GetGUID())

                    # Skip if we've already processed this object
                    if obj_id in found_ids:
                        current_obj = current_obj.GetNext()
                        continue

                    found_ids.add(obj_id)

                    # Get object name and type
                    obj_name = current_obj.GetName()
                    obj_type_id = current_obj.GetType()

                    # Get basic object info with enhanced MoGraph detection
                    obj_type = self.get_object_type_name(current_obj)

                    # Additional properties dictionary for specific object types
                    additional_props = {}

                    # MoGraph Cloner enhanced detection - explicitly check for cloner type
                    if obj_type_id == c4d.Omgcloner:
                        obj_type = "MoGraph Cloner"
                        try:
                            # Get the cloner mode
                            mode_id = current_obj[c4d.ID_MG_MOTIONGENERATOR_MODE]
                            modes = {
                                0: "Linear",
                                1: "Grid",
                                2: "Radial",
                                3: "Object",
                            }
                            mode_name = modes.get(mode_id, f"Mode {mode_id}")
                            additional_props["cloner_mode"] = mode_name

                            # Add counts based on mode - using R2025.1 constant paths
                            try:
                                # Try R2025.1 module path first
                                if mode_id == 0:  # Linear
                                    if hasattr(c4d, "MG_LINEAR_COUNT"):
                                        additional_props["count"] = current_obj[
                                            c4d.MG_LINEAR_COUNT
                                        ]
                                elif mode_id == 1:  # Grid
                                    if hasattr(c4d, "MGGRIDARRAY_RESOLUTION"):
                                        resolution = current_obj[
                                            c4d.MGGRIDARRAY_RESOLUTION
                                        ]
                                        additional_props["count_x"] = int(resolution.x)
                                        additional_props["count_y"] = int(resolution.y)
                                        additional_props["count_z"] = int(resolution.z)
                                        # Fallback to legacy MG_GRID_COUNT_* if available
                                    elif all(
                                        hasattr(c4d, attr)
                                        for attr in [
                                            "MG_GRID_COUNT_X",
                                            "MG_GRID_COUNT_Y",
                                            "MG_GRID_COUNT_Z",
                                        ]
                                    ):
                                        additional_props["count_x"] = int(
                                            current_obj[c4d.MG_GRID_COUNT_X]
                                        )
                                        additional_props["count_y"] = int(
                                            current_obj[c4d.MG_GRID_COUNT_Y]
                                        )
                                        additional_props["count_z"] = int(
                                            current_obj[c4d.MG_GRID_COUNT_Z]
                                        )
                                    else:
                                        self.log(
                                            "[C4D CLONER WARNING] No valid grid count parameters found"
                                        )
                                elif mode_id == 2:  # Radial
                                    if hasattr(c4d, "MG_POLY_COUNT"):
                                        additional_props["count"] = current_obj[
                                            c4d.MG_POLY_COUNT
                                        ]
                            except Exception as e:
                                self.log(
                                    f"[C4D CLONER] Error getting cloner counts: {str(e)}"
                                )

                            self.log(
                                f"[C4D CLONER] Detected MoGraph Cloner: {obj_name}, Mode: {mode_name}"
                            )
                        except Exception as e:
                            self.log(
                                f"[C4D CLONER] Error getting cloner details: {str(e)}"
                            )

                    # MoGraph Effector enhanced detection
                    elif 1019544 <= obj_type_id <= 1019644:
                        if obj_type_id == c4d.Omgrandom:
                            obj_type = "Random Effector"
                        elif obj_type_id == c4d.Omgformula:
                            obj_type = "Formula Effector"
                        elif hasattr(c4d, "Omgstep") and obj_type_id == c4d.Omgstep:
                            obj_type = "Step Effector"
                        else:
                            obj_type = "MoGraph Effector"

                        # Try to get effector strength
                        try:
                            if hasattr(c4d, "ID_MG_BASEEFFECTOR_STRENGTH"):
                                additional_props["strength"] = current_obj[
                                    c4d.ID_MG_BASEEFFECTOR_STRENGTH
                                ]
                        except:
                            pass

                    # Field objects enhanced detection
                    elif 1039384 <= obj_type_id <= 1039484:
                        field_types = {
                            1039384: "Spherical Field",
                            1039385: "Box Field",
                            1039386: "Cylindrical Field",
                            1039387: "Torus Field",
                            1039388: "Cone Field",
                            1039389: "Linear Field",
                            1039390: "Radial Field",
                            1039394: "Noise Field",
                        }
                        obj_type = field_types.get(obj_type_id, "Field")

                        # Try to get field strength
                        try:
                            if hasattr(c4d, "FIELD_STRENGTH"):
                                additional_props["strength"] = current_obj[
                                    c4d.FIELD_STRENGTH
                                ]
                        except:
                            pass

                    # Base object info
                    obj_info = {
                        "id": obj_id,
                        "name": obj_name,
                        "type": obj_type,
                        "type_id": obj_type_id,
                        "level": depth,
                        **additional_props,  # Include any additional properties
                    }

                    # Position
                    if hasattr(current_obj, "GetAbsPos"):
                        pos = current_obj.GetAbsPos()
                        obj_info["position"] = [pos.x, pos.y, pos.z]

                    # Rotation (converted to degrees)
                    if hasattr(current_obj, "GetRelRot"):
                        rot = current_obj.GetRelRot()
                        obj_info["rotation"] = [
                            c4d.utils.RadToDeg(rot.x),
                            c4d.utils.RadToDeg(rot.y),
                            c4d.utils.RadToDeg(rot.z),
                        ]

                    # Scale
                    if hasattr(current_obj, "GetAbsScale"):
                        scale = current_obj.GetAbsScale()
                        obj_info["scale"] = [scale.x, scale.y, scale.z]

                    # Add to the list
                    objects.append(obj_info)

                    # Recurse children
                    if current_obj.GetDown():
                        get_objects_recursive(current_obj.GetDown(), depth + 1)

                    # Move to next object
                    current_obj = current_obj.GetNext()
                except Exception as e:
                    self.log(f"[C4D CLONER] Error processing object: {str(e)}")
                    if current_obj:
                        current_obj = current_obj.GetNext()

        def get_all_root_objects():
            # Start with standard objects
            get_objects_recursive(doc.GetFirstObject())

            # Also check for MoGraph objects that might not be in main hierarchy
            # (This is more for thoroughness as get_objects_recursive should find everything)
            try:
                if hasattr(c4d, "GetMoData"):
                    mograph_data = c4d.GetMoData(doc)
                    if mograph_data:
                        for i in range(mograph_data.GetCount()):
                            obj = mograph_data.GetObject(i)
                            if obj and obj.GetType() == c4d.Omgcloner:
                                if str(obj.GetGUID()) not in found_ids:
                                    get_objects_recursive(obj)
            except Exception as e:
                self.log(f"[**ERROR**] Error checking MoGraph objects: {str(e)}")

        # Get all objects starting from the root level
        get_all_root_objects()

        self.log(
            f"[C4D] Comprehensive object search complete, found {len(objects)} objects"
        )
        return {"objects": objects}

    def handle_add_effector(self, command):
        """Adds a MoGraph effector and optionally links it to a cloner, returns context."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        type_name = command.get("effector_type", "random").lower()
        # --- Use 'target' preferentially, fallback to 'cloner_name' ---
        cloner_identifier = command.get("target") or command.get("cloner_name") or ""
        properties = command.get("properties", {})
        requested_name = (
            command.get("name")
            or command.get("effector_name")
            or f"{type_name.capitalize()} Effector"
        )

        # --- Detect if the cloner_identifier looks like a GUID ---
        use_cloner_guid = False
        if cloner_identifier:  # Check only if identifier exists
            identifier_str = str(cloner_identifier)
            if "-" in identifier_str and len(identifier_str) > 30:
                use_cloner_guid = True
            elif identifier_str.isdigit() or (
                identifier_str.startswith("-") and identifier_str[1:].isdigit()
            ):
                if len(identifier_str) > 10:
                    use_cloner_guid = True
        # --- End GUID detection ---

        effector = None
        try:
            self.log(
                f"[C4D EFFECTOR] Creating {type_name} effector named '{requested_name}'"
            )
            if cloner_identifier:
                self.log(
                    f"[C4D EFFECTOR] Will attempt to apply to cloner '{cloner_identifier}' (Treat as GUID: {use_cloner_guid})"
                )

            # (Effector type mapping and creation remains the same)
            effector_types = {
                "random": c4d.Omgrandom,
                "formula": c4d.Omgformula,
                "step": c4d.Omgstep,
                "target": getattr(
                    c4d, "Omgtarget", getattr(c4d, "Omgeffectortarget", None)
                ),
                "time": c4d.Omgtime,
                "sound": c4d.Omgsound,
                "plain": c4d.Omgplain,
                "delay": c4d.Omgdelay,
                "spline": c4d.Omgspline,
                "python": c4d.Omgpython,
                "shader": c4d.Omgshader,
                "volume": c4d.Omgvolume,
            }
            if hasattr(c4d, "Omgfalloff"):
                effector_types["falloff"] = c4d.Omgfalloff
            effector_id = effector_types.get(type_name)
            if effector_id is None:
                return {"error": f"Unsupported effector type: {type_name}"}

            doc.StartUndo()
            effector = c4d.BaseObject(effector_id)
            if effector is None:
                raise RuntimeError(f"Failed to create {type_name} effector BaseObject")
            effector.SetName(requested_name)

            # (Property setting remains the same)
            bc = effector.GetDataInstance()
            if bc:
                if "strength" in properties and isinstance(
                    properties["strength"], (int, float)
                ):
                    try:
                        bc[c4d.ID_MG_BASEEFFECTOR_STRENGTH] = (
                            float(properties["strength"]) / 100.0
                        )
                    except Exception as e_prop:
                        self.log(f"Warning: Could not set strength: {e_prop}")
                if "position_mode" in properties and isinstance(
                    properties["position_mode"], bool
                ):
                    try:
                        bc[c4d.ID_MG_BASEEFFECTOR_POSITION_ACTIVE] = properties[
                            "position_mode"
                        ]
                    except Exception as e_prop:
                        self.log(f"Warning: Could not set position_mode: {e_prop}")
                if "rotation_mode" in properties and isinstance(
                    properties["rotation_mode"], bool
                ):
                    try:
                        bc[c4d.ID_MG_BASEEFFECTOR_ROTATION_ACTIVE] = properties[
                            "rotation_mode"
                        ]
                    except Exception as e_prop:
                        self.log(f"Warning: Could not set rotation_mode: {e_prop}")
                if "scale_mode" in properties and isinstance(
                    properties["scale_mode"], bool
                ):
                    try:
                        bc[c4d.ID_MG_BASEEFFECTOR_SCALE_ACTIVE] = properties[
                            "scale_mode"
                        ]
                    except Exception as e_prop:
                        self.log(f"Warning: Could not set scale_mode: {e_prop}")
            else:
                self.log(
                    f"Warning: Could not get BaseContainer for effector '{requested_name}'"
                )

            doc.InsertObject(effector)
            doc.AddUndo(c4d.UNDOTYPE_NEW, effector)

            # --- Linking logic (remains the same, but find_object_by_name call uses correct flag now) ---
            cloner_applied_to_name = "None"
            cloner_applied_to_guid = None
            cloner_found = None

            if cloner_identifier:
                # Pass the use_cloner_guid flag correctly
                cloner_found = self.find_object_by_name(
                    doc, cloner_identifier, use_guid=use_cloner_guid
                )

                if cloner_found is None:
                    search_type = "GUID" if use_cloner_guid else "Name"
                    self.log(
                        f"[C4D EFFECTOR] ## Warning ##: Cloner '{cloner_identifier}' (searched by {search_type}) not found, effector created but not linked."
                    )
                else:
                    if cloner_found.GetType() != c4d.Omgcloner:
                        self.log(
                            f"[C4D EFFECTOR] ## Warning ##: Target '{cloner_found.GetName()}' is not a MoGraph Cloner (Type: {cloner_found.GetType()})"
                        )
                    else:
                        try:
                            effector_list = None
                            try:
                                effector_list = cloner_found[
                                    c4d.ID_MG_MOTIONGENERATOR_EFFECTORLIST
                                ]
                            except:
                                self.log(
                                    f"[C4D EFFECTOR] Creating new effector list for cloner '{cloner_found.GetName()}'"
                                )
                            if not isinstance(effector_list, c4d.InExcludeData):
                                effector_list = c4d.InExcludeData()

                            effector_list.InsertObject(effector, 1)
                            cloner_found[c4d.ID_MG_MOTIONGENERATOR_EFFECTORLIST] = (
                                effector_list
                            )
                            doc.AddUndo(c4d.UNDOTYPE_CHANGE, cloner_found)
                            cloner_applied_to_name = cloner_found.GetName()
                            cloner_applied_to_guid = str(cloner_found.GetGUID())
                            self.log(
                                f"[C4D EFFECTOR] Successfully applied effector to cloner '{cloner_applied_to_name}'"
                            )
                        except Exception as e_apply:
                            self.log(
                                f"[**ERROR**] Error applying effector to cloner '{cloner_found.GetName()}': {str(e_apply)}"
                            )

            doc.EndUndo()
            c4d.EventAdd()

            # --- Contextual Return (remains the same) ---
            actual_effector_name = effector.GetName()
            effector_guid = str(effector.GetGUID())
            pos_vec = effector.GetAbsPos()

            self.register_object_name(effector, requested_name)

            return {
                "effector": {
                    "requested_name": requested_name,
                    "actual_name": actual_effector_name,
                    "guid": effector_guid,
                    "type": type_name,
                    "position": [pos_vec.x, pos_vec.y, pos_vec.z],
                    "applied_to_cloner_name": cloner_applied_to_name,
                    "applied_to_cloner_guid": cloner_applied_to_guid,
                }
            }

        except Exception as e:
            doc.EndUndo()
            self.log(
                f"[**ERROR**] Error creating effector: {str(e)}\n{traceback.format_exc()}"
            )
            if effector and not effector.GetDocument():
                try:
                    effector.Remove()
                except:
                    pass
            return {
                "error": f"Failed to create effector: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def handle_apply_mograph_fields(self, command):
        """Applies a MoGraph field (as a child) to a MoGraph effector, returns context."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        field_type = command.get("field_type", "spherical").lower()
        requested_name = command.get("field_name", f"{field_type.capitalize()} Field")
        target_identifier = command.get("target_name", "")
        parameters = command.get("parameters", {})

        # --- REVISED: Detect if target_identifier is likely a GUID ---
        use_target_guid = False
        if target_identifier:
            identifier_str = str(target_identifier)
            if "-" in identifier_str and len(identifier_str) > 30:
                use_target_guid = True
            elif identifier_str.isdigit() or (
                identifier_str.startswith("-") and identifier_str[1:].isdigit()
            ):
                if len(identifier_str) > 10:
                    use_target_guid = True
        # --- END REVISED ---

        field = None
        try:
            self.log(
                f"[C4D FIELDS] Request: Field='{requested_name}' Type='{field_type}' Target='{target_identifier}' (Treat as GUID: {use_target_guid})"
            )

            target = self.find_object_by_name(
                doc, target_identifier, use_guid=use_target_guid
            )
            if not target:
                search_type = "GUID" if use_target_guid else "Name"
                return {
                    "error": f"Target effector '{target_identifier}' (searched by {search_type}) not found"
                }

            valid_effector_types = {
                c4d.Omgplain,
                c4d.Omgrandom,
                c4d.Omgstep,
                c4d.Omgdelay,
                c4d.Omgformula,
                c4d.Omgtime,
                c4d.Omgsound,
                c4d.Omgpython,
                c4d.Omgshader,
                c4d.Omgvolume,
                getattr(c4d, "Omgtarget", getattr(c4d, "Omgeffectortarget", None)),
            }
            if target.GetType() not in valid_effector_types:
                return {
                    "error": f"Target '{target.GetName()}' is not a supported effector type (Type: {target.GetType()})"
                }

            target_name = target.GetName()
            target_guid = str(target.GetGUID())

            field_type_map = {
                "spherical": getattr(c4d, "Fspherical", 440000243),
                "box": getattr(c4d, "Fbox", 440000244),
                "radial": getattr(c4d, "Fradial", 440000245),
                "linear": getattr(c4d, "Flinear", 440000246),
                "noise": 440000248,
                "cylinder": getattr(c4d, "Fcylinder", 1039386),
                "cone": getattr(c4d, "Fcone", 1039388),
                "torus": getattr(c4d, "Ftorus", 1039387),
                "formula": getattr(c4d, "Fformula", 1040830),
                "random": getattr(c4d, "Frandom", 1040831),
                "step": getattr(c4d, "Fstep", 1040832),
            }
            field_type_id = field_type_map.get(field_type)
            if not field_type_id:
                return {"error": f"Unsupported field type: '{field_type}'"}

            doc.StartUndo()
            field = c4d.BaseObject(field_type_id)
            if not field:
                raise RuntimeError("Failed to create field BaseObject")
            field.SetName(requested_name)

            bc = field.GetDataInstance()
            if bc:
                if (
                    "position" in parameters
                    and isinstance(parameters["position"], list)
                    and len(parameters["position"]) >= 3
                ):
                    try:
                        field.SetAbsPos(
                            c4d.Vector(*[float(p) for p in parameters["position"][:3]])
                        )
                    except (ValueError, TypeError):
                        self.log(
                            f"Warning: Invalid field position {parameters['position']}"
                        )
                if (
                    "scale" in parameters
                    and isinstance(parameters["scale"], list)
                    and len(parameters["scale"]) >= 3
                ):
                    try:
                        field.SetAbsScale(
                            c4d.Vector(*[float(p) for p in parameters["scale"][:3]])
                        )
                    except (ValueError, TypeError):
                        self.log(f"Warning: Invalid field scale {parameters['scale']}")
                if (
                    "rotation" in parameters
                    and isinstance(parameters["rotation"], list)
                    and len(parameters["rotation"]) >= 3
                ):
                    try:
                        hpb_rad = [
                            c4d.utils.DegToRad(float(angle))
                            for angle in parameters["rotation"][:3]
                        ]
                        field.SetAbsRot(c4d.Vector(*hpb_rad))
                    except (ValueError, TypeError):
                        self.log(
                            f"Warning: Invalid field rotation {parameters['rotation']}"
                        )
                if field_type == "spherical" and "radius" in parameters:
                    radius_id = getattr(
                        c4d, "FIELD_SIZE", getattr(c4d, "FIELDSPHERICAL_RADIUS", None)
                    )
                    if radius_id:
                        try:
                            bc[radius_id] = float(parameters["radius"])
                        except (ValueError, TypeError):
                            self.log(
                                f"Warning: Invalid radius value {parameters['radius']}"
                            )
                    else:
                        self.log(
                            "Warning: Could not find radius parameter ID for spherical field."
                        )
            else:
                self.log(
                    f"Warning: Could not get BaseContainer for field '{requested_name}'"
                )

            doc.InsertObject(field)
            doc.AddUndo(c4d.UNDOTYPE_NEW, field)
            field.InsertUnder(target)
            doc.AddUndo(c4d.UNDOTYPE_CHANGE, target)
            doc.EndUndo()
            c4d.EventAdd()
            self.log(
                f"[C4D FIELDS] Linked field '{field.GetName()}' to effector '{target_name}'"
            )

            actual_field_name = field.GetName()
            field_guid = str(field.GetGUID())
            pos_vec = field.GetAbsPos()
            self.register_object_name(field, requested_name)

            return {
                "field": {
                    "requested_name": requested_name,
                    "actual_name": actual_field_name,
                    "guid": field_guid,
                    "type": field_type,
                    "target_name": target_name,
                    "target_guid": target_guid,
                    "position": [pos_vec.x, pos_vec.y, pos_vec.z],
                }
            }

        except Exception as e:
            doc.EndUndo()
            self.log(f"[**ERROR**] Error applying field: {e}\n{traceback.format_exc()}")
            if field and not field.GetDocument():
                try:
                    field.Remove()
                except:
                    pass
            return {
                "error": f"Exception occurred applying field: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def handle_create_soft_body(self, command):
        """Handle create_soft_body command with GUID support."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        # --- MODIFIED: Identify target object ---
        identifier = None
        use_guid = False
        if command.get("guid"):
            identifier = command.get("guid")
            use_guid = True
            self.log(f"[SOFT BODY] Using GUID identifier: '{identifier}'")
        elif command.get("object_name"):
            identifier = command.get("object_name")
            use_guid = False
            self.log(f"[SOFT BODY] Using Name identifier: '{identifier}'")
        else:
            return {"error": "No object identifier ('guid' or 'object_name') provided."}

        # Find target object using the determined method
        obj = self.find_object_by_name(doc, identifier, use_guid=use_guid)
        if obj is None:
            search_type = "GUID" if use_guid else "Name"
            return {
                "error": f"Object '{identifier}' (searched by {search_type}) not found for soft body."
            }
        # --- END MODIFIED ---

        # Get parameters (using original logic)
        name = command.get(
            "name", f"{obj.GetName()} Soft Body"
        )  # Default name based on object
        stiffness = command.get("stiffness", 50)
        mass = command.get("mass", 1.0)

        # Define safe wrapper (using original logic, but noting potential RIGID_BODY_SOFTBODY issue)
        def create_soft_body_safe(
            target_obj, tag_name, stiff_val, mass_val, obj_actual_name
        ):
            self.log(
                f"[C4D SBODY] Creating soft body dynamics tag '{tag_name}' for object '{obj_actual_name}'"
            )
            dynamics_tag_id = 180000102  # Standard Dynamics Body Tag ID

            tag = c4d.BaseTag(dynamics_tag_id)
            if tag is None:
                self.log(
                    f"[C4D SBODY] Error: Failed to create Dynamics Body tag with ID {dynamics_tag_id}"
                )
                raise RuntimeError("Failed to create Dynamics Body tag")

            tag.SetName(tag_name)
            self.log(f"[C4D SBODY] Successfully created dynamics tag: {tag_name}")

            # --- Potential Issue Area ---
            # RIGID_BODY_SOFTBODY might be deprecated in newer C4D versions.
            # This might need adjustment based on testing with the target C4D version.
            # A more modern approach uses RIGID_BODY_TYPE = 2
            try:
                # Try modern approach first
                if hasattr(c4d, "RIGID_BODY_TYPE"):
                    tag[c4d.RIGID_BODY_TYPE] = getattr(
                        c4d, "RIGID_BODY_TYPE_SOFTBODY", 2
                    )  # Use constant or fallback value 2
                    self.log(
                        f"[C4D SBODY] Set RIGID_BODY_TYPE to Soft Body ({tag[c4d.RIGID_BODY_TYPE]})"
                    )
                elif hasattr(c4d, "RIGID_BODY_SOFTBODY"):
                    # Fallback to older attribute if modern one doesn't exist
                    tag[c4d.RIGID_BODY_SOFTBODY] = True
                    self.log("[C4D SBODY] Set RIGID_BODY_SOFTBODY to True (legacy)")
                else:
                    self.log(
                        "[C4D SBODY] ## Warning ##: Cannot find suitable parameter to enable Soft Body mode."
                    )

                # Common properties (assuming these IDs are stable)
                tag[c4d.RIGID_BODY_DYNAMIC] = 1  # Enable dynamics
                tag[c4d.RIGID_BODY_MASS] = float(mass_val)

                # Stiffness might also have changed ID, add check
                softbody_stiffness_id = getattr(
                    c4d, "RIGID_BODY_SOFTBODY_STIFFNESS", 1110
                )  # Example ID 1110
                if tag.HasParameter(softbody_stiffness_id):
                    tag[softbody_stiffness_id] = (
                        float(stiff_val) / 100.0
                    )  # Assume 0-100 input
                    self.log(
                        f"[C4D SBODY] Set stiffness parameter ID {softbody_stiffness_id}"
                    )
                else:
                    self.log(
                        f"[C4D SBODY] ## Warning ##: Stiffness parameter ID {softbody_stiffness_id} not found."
                    )

            except AttributeError as ae:
                self.log(
                    f"[**ERROR**] Missing Dynamics attribute: {ae}. Dynamics setup might be incomplete."
                )
                # Don't raise, just log, tag might still be useful partially
            except Exception as e_tag:
                self.log(f"[**ERROR**] Error setting dynamics parameters: {e_tag}")
                # Don't raise, try inserting tag anyway

            target_obj.InsertTag(tag)
            doc.AddUndo(c4d.UNDOTYPE_NEW, tag)
            c4d.EventAdd()

            # Return context
            return {
                "object_name": obj_actual_name,
                "object_guid": str(target_obj.GetGUID()),  # Added GUID
                "tag_name": tag.GetName(),
                "stiffness_set": float(stiff_val),  # Report value requested
                "mass_set": float(mass_val),  # Report value requested
            }

        # Execute on main thread
        try:
            result = self.execute_on_main_thread(
                create_soft_body_safe,
                args=(obj, name, stiffness, mass, obj.GetName()),
            )
            # Check result structure from execute_on_main_thread
            if isinstance(result, dict) and "error" in result:
                return result  # Propagate error
            elif isinstance(result, dict) and result.get("status") == "completed_none":
                return {
                    "error": "Soft body creation function returned None unexpectedly."
                }
            else:
                return {"soft_body": result}  # Wrap successful result

        except Exception as e:
            # Catch errors related to execute_on_main_thread itself
            self.log(
                f"[**ERROR**] Failed to execute soft body creation via main thread: {e}\n{traceback.format_exc()}"
            )
            return {"error": f"Failed to queue/execute Soft Body creation: {str(e)}"}

    def handle_apply_dynamics(self, command):
        """Handle apply_dynamics command with GUID support."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        # --- MODIFIED: Identify target object ---
        identifier = None
        use_guid = False
        if command.get("guid"):
            identifier = command.get("guid")
            use_guid = True
            self.log(f"[DYNAMICS] Using GUID identifier: '{identifier}'")
        elif command.get("object_name"):
            identifier = command.get("object_name")
            use_guid = False
            self.log(f"[DYNAMICS] Using Name identifier: '{identifier}'")
        else:
            return {"error": "No object identifier ('guid' or 'object_name') provided."}

        # Find target object using the determined method
        obj = self.find_object_by_name(doc, identifier, use_guid=use_guid)
        if obj is None:
            search_type = "GUID" if use_guid else "Name"
            return {
                "error": f"Object '{identifier}' (searched by {search_type}) not found for dynamics."
            }
        # --- END MODIFIED ---

        tag_type = command.get("tag_type", "rigid_body").lower()
        params = command.get("parameters", {})
        tag_name = command.get(
            "tag_name", f"{obj.GetName()} {tag_type.replace('_',' ').title()}"
        )  # Default name

        try:
            # Use Tdynamicsbody if available, fallback to old ID
            dynamics_tag_id = getattr(c4d, "Tdynamicsbody", 180000102)
            self.log(
                f"[DYNAMICS] Using Dynamics Tag ID: {dynamics_tag_id} for type '{tag_type}'"
            )

            doc.StartUndo()  # Start undo block
            tag = obj.MakeTag(dynamics_tag_id)  # Use MakeTag for safer insertion
            if tag is None:
                raise RuntimeError(
                    f"Failed to create Dynamics tag (ID: {dynamics_tag_id}) on '{obj.GetName()}'"
                )

            tag.SetName(tag_name)
            bc = tag.GetDataInstance()
            if not bc:
                raise RuntimeError("Failed to get BaseContainer for dynamics tag")

            # Map tag_type string to RIGID_BODY_TYPE enum value
            type_map = {
                "rigid_body": getattr(c4d, "RIGID_BODY_TYPE_RIGIDBODY", 1),  # Usually 1
                "collider": getattr(c4d, "RIGID_BODY_TYPE_COLLIDER", 0),  # Usually 0
                "ghost": getattr(c4d, "RIGID_BODY_TYPE_GHOST", 3),  # Usually 3
                "soft_body": getattr(c4d, "RIGID_BODY_TYPE_SOFTBODY", 2),  # Usually 2
            }
            dynamics_type = type_map.get(tag_type)
            if dynamics_type is None:
                self.log(
                    f"Warning: Unknown dynamics tag_type '{tag_type}'. Defaulting to Collider."
                )
                dynamics_type = type_map["collider"]

            # Set dynamics type and enable
            bc[c4d.RIGID_BODY_TYPE] = dynamics_type
            bc[c4d.RIGID_BODY_ENABLED] = True

            # Apply common parameters from the 'params' dictionary safely
            if "mass" in params:
                try:
                    bc[c4d.RIGID_BODY_MASS_TYPE] = getattr(
                        c4d, "RIGID_BODY_MASS_TYPE_CUSTOM", 1
                    )
                    bc[c4d.RIGID_BODY_MASS_CUSTOM] = float(params["mass"])
                except (ValueError, TypeError, AttributeError) as e:
                    self.log(
                        f"Warning: Invalid/unsupported mass value '{params['mass']}': {e}"
                    )
            if "friction" in params:
                try:
                    bc[c4d.RIGID_BODY_FRICTION] = float(params["friction"])
                except (ValueError, TypeError, AttributeError) as e:
                    self.log(
                        f"Warning: Invalid/unsupported friction value '{params['friction']}': {e}"
                    )
            # Use BOUNCE as it's the more common ID name than ELASTICITY
            if "bounce" in params or "elasticity" in params:
                bounce_val = params.get(
                    "bounce", params.get("elasticity")
                )  # Accept either key
                try:
                    bc[c4d.RIGID_BODY_BOUNCE] = float(bounce_val)
                except (ValueError, TypeError, AttributeError) as e:
                    self.log(
                        f"Warning: Invalid/unsupported bounce/elasticity value '{bounce_val}': {e}"
                    )
            if "collision_shape" in params:
                shape_map = {
                    "auto": getattr(c4d, "RIGID_BODY_SHAPE_AUTO", 0),
                    "box": getattr(c4d, "RIGID_BODY_SHAPE_BOX", 1),
                    "sphere": getattr(c4d, "RIGID_BODY_SHAPE_SPHERE", 2),
                    "capsule": getattr(c4d, "RIGID_BODY_SHAPE_CAPSULE", 3),
                    "cylinder": getattr(c4d, "RIGID_BODY_SHAPE_CYLINDER", 4),
                    "cone": getattr(c4d, "RIGID_BODY_SHAPE_CONE", 5),
                    "static_mesh": getattr(c4d, "RIGID_BODY_SHAPE_STATICMESH", 7),
                    "moving_mesh": getattr(c4d, "RIGID_BODY_SHAPE_MOVINGMESH", 8),
                }
                shape_val = shape_map.get(str(params["collision_shape"]).lower())
                if shape_val is not None:
                    try:
                        bc[c4d.RIGID_BODY_SHAPE] = shape_val
                    except AttributeError as e:
                        self.log(f"Warning: Collision shape parameter not found: {e}")
                else:
                    self.log(
                        f"Warning: Invalid collision_shape value '{params['collision_shape']}'"
                    )
            else:  # Default collision shape if not specified
                try:
                    bc[c4d.RIGID_BODY_SHAPE] = getattr(c4d, "RIGID_BODY_SHAPE_AUTO", 0)
                except AttributeError as e:
                    self.log(
                        f"Warning: Default collision shape parameter not found: {e}"
                    )

            # No need for obj.InsertTag(tag) because MakeTag already inserts it
            doc.AddUndo(c4d.UNDOTYPE_NEW, tag)  # Add undo for the new tag
            doc.EndUndo()  # End undo block
            c4d.EventAdd()

            # --- MODIFIED: Contextual Return ---
            return {
                "dynamics": {
                    "object_name": obj.GetName(),
                    "object_guid": str(obj.GetGUID()),
                    "tag_name": tag.GetName(),
                    "tag_type_applied": tag_type,
                    "parameters_received": params,  # Echo back received params for verification
                }
            }
            # --- END MODIFIED ---

        except Exception as e:
            doc.EndUndo()  # Ensure undo is ended on error
            self.log(
                f"[**ERROR**] Error applying dynamics: {e}\n{traceback.format_exc()}"
            )
            return {
                "error": f"Failed to apply Dynamics tag: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def handle_create_abstract_shape(self, command):
        """Handle create_abstract_shape command with context and C4D 2025 compatibility."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        shape_type = command.get("shape_type", "metaball").lower()
        # Accept both "name" and "object_name"
        requested_name = command.get("name") or command.get(
            "object_name", f"{shape_type.capitalize()}"
        )
        position_list = command.get("position", [0, 0, 0])

        # Safely parse position
        position = [0.0, 0.0, 0.0]
        if isinstance(position_list, list) and len(position_list) >= 3:
            try:
                position = [float(p) for p in position_list[:3]]
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid position data {position_list}")

        self.log(
            f"[C4D ABSTRCTSHAPE] Creating abstract shape '{shape_type}' with requested name: '{requested_name}'"
        )

        shape = None  # Initialize shape variable
        try:
            shape_types = {
                "metaball": 5125,
                "blob": 5119,
                "loft": 5107,
                "sweep": 5118,
                "atom": 5168,
                "platonic": 5170,
                "cloth": 5186,
                "landscape": 5119,
                "extrude": 5116,
            }
            shape_type_id = shape_types.get(shape_type, shape_types["metaball"])
            self.log(
                f"[C4D ABSTRCTSHAPE] Creating abstract shape of type: {shape_type} (ID: {shape_type_id})"
            )

            doc.StartUndo()  # Start undo block
            shape = c4d.BaseObject(shape_type_id)
            if shape is None:
                raise RuntimeError(f"Failed to create {shape_type} object")

            shape.SetName(requested_name)
            shape.SetAbsPos(c4d.Vector(*position))

            child_objects_context = {}  # Store context for children

            # Add children based on type (using original logic)
            if shape_type in ["metaball", "blob"]:
                self.log(f"[C4D ABSTRCTSHAPE] Creating child sphere for {shape_type}")
                sphere = c4d.BaseObject(c4d.Osphere)
                if sphere:
                    child_req_name = (
                        f"{requested_name}_Sphere"  # Use requested name of parent
                    )
                    sphere.SetName(child_req_name)
                    sphere.SetAbsScale(c4d.Vector(2.0, 2.0, 2.0))  # Use floats
                    bc = sphere.GetDataInstance()
                    if bc:
                        bc.SetFloat(c4d.PRIM_SPHERE_RAD, 50.0)  # Use floats
                    sphere.InsertUnder(shape)
                    doc.AddUndo(c4d.UNDOTYPE_NEW, sphere)
                    # Add child context
                    child_actual_name = sphere.GetName()
                    child_guid = str(sphere.GetGUID())
                    child_objects_context["sphere"] = {
                        "requested_name": child_req_name,
                        "actual_name": child_actual_name,
                        "guid": child_guid,
                    }
                    self.register_object_name(sphere, child_req_name)  # Register child
                else:
                    self.log(f"Warning: Failed to create child sphere for {shape_type}")

            elif shape_type in ("loft", "sweep"):
                self.log(
                    f"[C4D ABSTRCTSHAPE] Creating profile and path splines for {shape_type}"
                )
                spline = c4d.BaseObject(c4d.Osplinecircle)
                path = c4d.BaseObject(c4d.Osplinenside)

                if spline:
                    child_req_name = f"{requested_name}_Profile"
                    spline.SetName(child_req_name)
                    spline.InsertUnder(shape)
                    doc.AddUndo(c4d.UNDOTYPE_NEW, spline)
                    child_actual_name = spline.GetName()
                    child_guid = str(spline.GetGUID())
                    child_objects_context["profile"] = {
                        "requested_name": child_req_name,
                        "actual_name": child_actual_name,
                        "guid": child_guid,
                    }
                    self.register_object_name(spline, child_req_name)
                else:
                    self.log("Warning: Failed to create profile spline")

                if path:
                    child_req_name = f"{requested_name}_Path"
                    path.SetName(child_req_name)
                    path.SetAbsPos(c4d.Vector(0, 50, 0))
                    path.InsertUnder(shape)
                    doc.AddUndo(c4d.UNDOTYPE_NEW, path)
                    child_actual_name = path.GetName()
                    child_guid = str(path.GetGUID())
                    child_objects_context["path"] = {
                        "requested_name": child_req_name,
                        "actual_name": child_actual_name,
                        "guid": child_guid,
                    }
                    self.register_object_name(path, child_req_name)
                else:
                    self.log("Warning: Failed to create path spline")

            # Insert the main shape object
            doc.InsertObject(shape)
            doc.AddUndo(c4d.UNDOTYPE_NEW, shape)
            doc.EndUndo()  # End undo block
            c4d.EventAdd()

            # --- MODIFIED: Contextual Return ---
            actual_name = shape.GetName()
            guid = str(shape.GetGUID())
            pos_vec = shape.GetAbsPos()
            shape_type_name = self.get_object_type_name(shape)  # Get user friendly name

            # Register the main shape object
            self.register_object_name(shape, requested_name)

            return {
                "shape": {
                    "requested_name": requested_name,
                    "actual_name": actual_name,
                    "guid": guid,
                    "type": shape_type_name,  # User friendly type
                    "type_id": shape.GetType(),  # C4D ID
                    "position": [pos_vec.x, pos_vec.y, pos_vec.z],
                    "child_objects": child_objects_context,  # Include context of children
                }
            }
            # --- END MODIFIED ---

        except Exception as e:
            doc.EndUndo()  # Ensure undo is ended on error
            self.log(
                f"[**ERROR**] Error creating abstract shape '{requested_name}': {str(e)}\n{traceback.format_exc()}"
            )
            # Clean up shape if created but not inserted
            if shape and not shape.GetDocument():
                try:
                    shape.Remove()
                except:
                    pass
            return {
                "error": f"Failed to create abstract shape: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def _find_by_guid_recursive(self, start_obj, guid):
        """Recursively search for an object with a specific GUID."""
        current_obj = start_obj
        while current_obj:
            if str(current_obj.GetGUID()) == guid:
                return current_obj

            # Check children recursively
            child = current_obj.GetDown()
            if child:
                result = self._find_by_guid_recursive(child, guid)
                if result:
                    return result

            current_obj = current_obj.GetNext()
        return None

    def _get_all_objects(self, doc):
        """Get all objects in the document for efficient searching.

        This method uses optimal strategies for Cinema 4D 2025 to collect all objects
        in the scene without missing anything.
        """
        all_objects = []
        found_ids = set()  # To avoid duplicates

        # Method 1: Standard hierarchy traversal
        def collect_recursive(obj):
            if obj is None:
                return

            obj_id = str(obj.GetGUID())
            if obj_id not in found_ids:
                all_objects.append(obj)
                found_ids.add(obj_id)

            # Get children
            child = obj.GetDown()
            if child:
                collect_recursive(child)

            # Get siblings
            next_obj = obj.GetNext()
            if next_obj:
                collect_recursive(next_obj)

        # Start collection from root
        collect_recursive(doc.GetFirstObject())

        # Method 2: Use GetObjects API if available in this version
        try:
            if hasattr(doc, "GetObjects"):
                objects = doc.GetObjects()
                for obj in objects:
                    obj_id = str(obj.GetGUID())
                    if obj_id not in found_ids:
                        all_objects.append(obj)
                        found_ids.add(obj_id)
        except Exception as e:
            self.log(f"[**ERROR**] Error using GetObjects API: {str(e)}")

        # Method 3: Check for any missed MoGraph objects
        try:
            # Direct check for Cloners
            if hasattr(c4d, "Omgcloner"):
                # Use object type filtering to find cloners
                for obj in all_objects[:]:  # Use a copy to avoid modification issues
                    if (
                        obj.GetType() == c4d.Omgcloner
                        and str(obj.GetGUID()) not in found_ids
                    ):
                        all_objects.append(obj)
                        found_ids.add(str(obj.GetGUID()))
        except Exception as e:
            self.log(f"[**ERROR**] Error checking for MoGraph objects: {str(e)}")

        self.log(f"[C4D] Found {len(all_objects)} objects in document")
        return all_objects

    def handle_create_light(self, command):
        """Light creation with context and EXACT 2025.0 SDK parameters"""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        light_type = command.get("type", "spot").lower()
        # Use requested name or generate one
        requested_name = (
            command.get("name")
            or command.get("object_name")
            or f"MCP_{light_type.capitalize()}Light_{int(time.time()) % 1000}"
        )
        # Handle test harness name if provided
        if not requested_name and command.get("from_test_harness"):
            requested_name = "Test_Light"

        position_list = command.get("position", [0, 100, 0])
        color_list = command.get("color", [1, 1, 1])
        intensity = command.get("intensity", 100)
        temperature = command.get("temperature", 6500)
        width = command.get("width", 200)
        height = command.get("height", 200)

        LIGHT_TYPE_MAP = {"point": 0, "spot": 1, "area": 8, "infinite": 3}
        if light_type not in LIGHT_TYPE_MAP:
            valid_types = ", ".join(LIGHT_TYPE_MAP.keys())
            return {
                "error": f"Invalid light type: '{light_type}'. Valid: {valid_types}"
            }

        light = None  # Initialize light variable
        try:
            doc.StartUndo()  # Start undo block
            light = c4d.BaseObject(c4d.Olight)
            if not light:
                raise RuntimeError("Light creation failed")

            light_code = LIGHT_TYPE_MAP[light_type]
            light[c4d.LIGHT_TYPE] = light_code
            light.SetName(requested_name)
            self.log(
                f"[C4D LIGHT] Set requested name '{requested_name}' before insertion."
            )  # Log name set

            # Safely set position, color, brightness
            try:
                light.SetAbsPos(c4d.Vector(*[float(x) for x in position_list[:3]]))
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid light position {position_list}")
            try:
                light[c4d.LIGHT_COLOR] = c4d.Vector(
                    *[max(0.0, min(1.0, float(c))) for c in color_list[:3]]
                )  # Clamp color 0-1
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid light color {color_list}")
            try:
                light[c4d.LIGHT_BRIGHTNESS] = max(
                    0.0, float(intensity) / 100.0
                )  # Clamp brightness >= 0
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid light intensity {intensity}")

            # Temperature handling
            if hasattr(c4d, "LIGHT_TEMPERATURE"):
                try:
                    light[c4d.LIGHT_TEMPERATURE] = int(float(temperature))
                except (TypeError, ValueError):
                    self.log(f"Warning: Invalid temperature '{temperature}'")

            # Area light parameters
            if light_code == 8:  # Area light
                try:
                    light[c4d.LIGHT_AREADETAILS_SIZEX] = max(
                        0.0, float(width)
                    )  # Ensure non-negative
                except (ValueError, TypeError):
                    self.log(f"Warning: Invalid area light width {width}")
                try:
                    light[c4d.LIGHT_AREADETAILS_SIZEY] = max(
                        0.0, float(height)
                    )  # Ensure non-negative
                except (ValueError, TypeError):
                    self.log(f"Warning: Invalid area light height {height}")
                try:
                    light[c4d.LIGHT_AREADETAILS_SHAPE] = 0  # Rectangle
                except AttributeError:
                    pass  # Ignore if param doesn't exist

            # Shadow parameters
            if hasattr(c4d, "LIGHT_SHADOWTYPE"):
                try:
                    light[c4d.LIGHT_SHADOWTYPE] = 1  # Soft shadows
                except AttributeError:
                    pass  # Ignore if param doesn't exist

            doc.InsertObject(light)
            doc.AddUndo(c4d.UNDOTYPE_NEW, light)  # Add undo for new light
            doc.EndUndo()  # End undo block
            c4d.EventAdd()

            # --- MODIFIED: Contextual Return ---
            actual_name = light.GetName()
            guid = str(light.GetGUID())
            pos_vec = light.GetAbsPos()
            light_type_name = self.get_object_type_name(light)  # Get user friendly name

            # Register the light object
            self.register_object_name(light, requested_name)

            return {
                "light": {  # Changed key from 'object' to 'light' for clarity
                    "requested_name": requested_name,
                    "actual_name": actual_name,
                    "guid": guid,
                    "type": light_type_name,  # User friendly type name
                    "type_id": light.GetType(),  # C4D ID
                    "position": [pos_vec.x, pos_vec.y, pos_vec.z],
                    # Optionally return other set properties for context
                    "color_set": [
                        light[c4d.LIGHT_COLOR].x,
                        light[c4d.LIGHT_COLOR].y,
                        light[c4d.LIGHT_COLOR].z,
                    ],
                    "intensity_set": light[c4d.LIGHT_BRIGHTNESS] * 100.0,
                }
            }
            # --- END MODIFIED ---

        except Exception as e:
            doc.EndUndo()  # Ensure undo is ended on error
            self.log(
                f"[**ERROR**] Error creating light '{requested_name}': {str(e)}\n{traceback.format_exc()}"
            )
            # Clean up light if created but not inserted
            if light and not light.GetDocument():
                try:
                    light.Remove()
                except:
                    pass
            return {
                "error": f"Light creation failed: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def handle_create_camera(self, command):
        """Create a new camera, optionally pointing it towards a target."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        requested_name = command.get("name", "Camera")
        position_list = command.get("position", [0, 0, 0])
        properties = command.get(
            "properties", {}
        )  # Includes focal_length, aperture, target_position etc.

        # Safely parse position
        position = [0.0, 0.0, 0.0]
        if isinstance(position_list, list) and len(position_list) >= 3:
            try:
                position = [float(p) for p in position_list[:3]]
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid camera position data {position_list}")

        camera = None
        try:
            doc.StartUndo()
            camera = c4d.BaseObject(c4d.Ocamera)
            if not camera:
                raise RuntimeError("Failed to create camera object")

            camera.SetName(requested_name)
            cam_pos_vec = c4d.Vector(*position)
            camera.SetAbsPos(cam_pos_vec)

            # --- Apply standard camera properties ---
            applied_properties = {}
            bc = camera.GetDataInstance()
            if bc:
                if "focal_length" in properties:
                    try:
                        val = float(properties["focal_length"])
                        focus_id = getattr(c4d, "CAMERAOBJECT_FOCUS", c4d.CAMERA_FOCUS)
                        bc[focus_id] = val
                        applied_properties["focal_length"] = val
                    except (ValueError, TypeError, AttributeError) as e:
                        self.log(f"Warning: Failed to set focal_length: {e}")
                if "aperture" in properties:
                    try:
                        val = float(properties["aperture"])
                        bc[c4d.CAMERAOBJECT_APERTURE] = val
                        applied_properties["aperture"] = val
                    except (ValueError, TypeError, AttributeError) as e:
                        self.log(f"Warning: Failed to set aperture: {e}")
                # Add other properties like film offset here if needed...

            # --- NEW: Handle Target Position ---
            target_pos = None
            target_list = properties.get(
                "target_position"
            )  # Expect key "target_position"
            if isinstance(target_list, list) and len(target_list) >= 3:
                try:
                    target_pos = c4d.Vector(*[float(p) for p in target_list[:3]])
                except (ValueError, TypeError):
                    self.log(f"Warning: Invalid target_position data {target_list}")
            else:
                # Default target to world origin if not specified
                target_pos = c4d.Vector(0, 0, 0)
                self.log(
                    f"No target_position provided, defaulting camera target to world origin."
                )

            if target_pos is not None:
                try:
                    # Calculate direction vector
                    direction = target_pos - cam_pos_vec
                    direction.Normalize()

                    # Calculate HPB rotation in radians
                    hpb = c4d.utils.VectorToHPB(direction)

                    # Apply rotation (SetAbsRot expects radians)
                    camera.SetAbsRot(hpb)
                    applied_properties["rotation_set_to_target"] = [
                        c4d.utils.RadToDeg(a) for a in [hpb.x, hpb.y, hpb.z]
                    ]  # Report degrees
                    self.log(
                        f"Pointed camera '{camera.GetName()}' towards target {target_list or '[0,0,0]'}"
                    )
                except Exception as e_rot:
                    self.log(
                        f"Warning: Failed to calculate or set camera rotation towards target: {e_rot}"
                    )
            # --- END NEW TARGET HANDLING ---

            doc.InsertObject(camera)
            doc.AddUndo(c4d.UNDOTYPE_NEW, camera)
            doc.SetActiveObject(camera)
            doc.EndUndo()
            c4d.EventAdd()

            self.log(f"[C4D] Created camera '{camera.GetName()}' at {position}")

            # --- Contextual Return ---
            actual_name = camera.GetName()
            guid = str(camera.GetGUID())
            pos_vec = camera.GetAbsPos()
            rot_vec_rad = camera.GetAbsRot()  # Get final rotation
            camera_type_name = self.get_object_type_name(camera)
            self.register_object_name(camera, requested_name)

            return {
                "camera": {
                    "requested_name": requested_name,
                    "actual_name": actual_name,
                    "guid": guid,
                    "type": camera_type_name,
                    "type_id": camera.GetType(),
                    "position": [pos_vec.x, pos_vec.y, pos_vec.z],
                    "rotation": [
                        c4d.utils.RadToDeg(a)
                        for a in [rot_vec_rad.x, rot_vec_rad.y, rot_vec_rad.z]
                    ],  # Return final rotation in degrees
                    "properties_applied": applied_properties,
                }
            }

        except Exception as e:
            if doc and doc.IsUndoEnabled():
                doc.EndUndo()  # Ensure undo ended
            self.log(
                f"[**ERROR**] Error creating camera '{requested_name}': {str(e)}\n{traceback.format_exc()}"
            )
            if camera and not camera.GetDocument():
                try:
                    camera.Remove()
                except:
                    pass
            return {
                "error": f"Failed to create camera: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def handle_animate_camera(self, command):
        """Handle animate_camera command with context."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        # --- MODIFIED: Identify target camera ---
        identifier = None
        use_guid = False
        if command.get("guid"):  # Check for GUID first
            identifier = command.get("guid")
            use_guid = True
            self.log(f"[ANIM CAM] Using GUID identifier: '{identifier}'")
        elif command.get("camera_name"):
            identifier = command.get("camera_name")
            use_guid = False
            self.log(f"[ANIM CAM] Using Name identifier: '{identifier}'")
        # --- END MODIFIED ---

        path_type = command.get("path_type", "linear").lower()
        positions = command.get("positions", [])
        frames = command.get("frames", [])
        create_camera = command.get("create_camera", False)
        camera_properties = command.get("camera_properties", {})  # e.g., focal_length

        camera = None
        camera_created = False
        requested_name = (
            identifier if identifier else "Animated Camera"
        )  # Use identifier as requested name if provided

        # Find existing camera if identifier provided and not creating new
        if identifier and not create_camera:
            camera = self.find_object_by_name(doc, identifier, use_guid=use_guid)
            if camera and camera.GetType() != c4d.Ocamera:
                self.log(
                    f"Warning: Object '{identifier}' found but is not a camera. Will create new."
                )
                camera = None  # Force creation
            elif camera is None:
                search_type = "GUID" if use_guid else "Name"
                self.log(
                    f"Info: Camera '{identifier}' (searched by {search_type}) not found, will create a new one."
                )

        # Create camera if needed
        if camera is None:
            doc.StartUndo()  # Start undo if creating
            camera = c4d.BaseObject(c4d.Ocamera)
            if not camera:
                return {"error": "Failed to create camera object"}

            camera.SetName(requested_name)  # Use requested name
            self.log(f"[ANIM CAM] Created new camera: {camera.GetName()}")
            camera_created = True

            # Apply properties if provided (using original logic, ensure safety)
            applied_properties = {}
            bc = camera.GetDataInstance()
            if bc:
                if "focal_length" in camera_properties:
                    try:
                        val = float(camera_properties["focal_length"])
                        focus_id = getattr(c4d, "CAMERAOBJECT_FOCUS", c4d.CAMERA_FOCUS)
                        bc[focus_id] = val
                        applied_properties["focal_length"] = val
                    except (ValueError, TypeError, AttributeError) as e:
                        self.log(f"Warning: Failed to set focal_length: {e}")
                if "aperture" in camera_properties:
                    try:
                        val = float(camera_properties["aperture"])
                        bc[c4d.CAMERAOBJECT_APERTURE] = val
                        applied_properties["aperture"] = val
                    except (ValueError, TypeError, AttributeError) as e:
                        self.log(f"Warning: Failed to set aperture: {e}")
                # Add other properties as needed...

            doc.InsertObject(camera)
            doc.AddUndo(c4d.UNDOTYPE_NEW, camera)
            doc.SetActiveObject(camera)  # Make active
            # Register the newly created camera
            self.register_object_name(camera, requested_name)
            doc.EndUndo()  # End undo block for creation
        else:
            self.log(f"[ANIM CAM] Using existing camera: '{camera.GetName()}'")

        # --- Animation Logic ---
        try:
            doc.StartUndo()  # Start undo for animation changes

            # Add default frames if only positions are provided
            if positions and not frames:
                frames = list(range(len(positions)))  # Simple frame sequence

            if not positions or not frames or len(positions) != len(frames):
                # Allow animation types without positions/frames? e.g. wiggle?
                if path_type not in ["wiggle"]:  # Add other position-less types here
                    doc.EndUndo()  # End undo as nothing happened yet
                    return {
                        "error": f"Invalid positions/frames data for animation type '{path_type}'. They must be arrays of equal length."
                    }
                else:
                    self.log(
                        f"Info: No position/frame data provided for '{path_type}', proceeding if type supports it."
                    )

            keyframe_count = 0
            frame_range_set = []

            # Set keyframes for camera positions if provided
            if positions and frames:
                for pos, frame in zip(positions, frames):
                    if isinstance(pos, list) and len(pos) >= 3:
                        # Use internal helper which already includes AddUndo
                        if self._set_position_keyframe(camera, frame, pos):
                            keyframe_count += 1
                    else:
                        self.log(
                            f"Warning: Skipping invalid position data {pos} for frame {frame}"
                        )
                if frames:
                    frame_range_set = [min(frames), max(frames)]

            # Handle spline path if requested and positions available
            path_guid = None  # Store GUID of created path
            if path_type in ["spline", "spline_oriented"] and len(positions) > 1:
                self.log("[ANIM CAM] Creating spline path and alignment tag.")
                path = c4d.BaseObject(c4d.Ospline)
                path.SetName(f"{camera.GetName()} Path")
                points = [
                    c4d.Vector(p[0], p[1], p[2])
                    for p in positions
                    if isinstance(p, list) and len(p) >= 3
                ]
                if not points:
                    self.log("Warning: No valid points for spline path creation.")
                else:
                    path.ResizeObject(len(points))
                    for i, pt in enumerate(points):
                        path.SetPoint(i, pt)

                    doc.InsertObject(path)  # Insert path into scene
                    doc.AddUndo(c4d.UNDOTYPE_NEW, path)
                    path_guid = str(path.GetGUID())  # Get GUID after insertion
                    self.register_object_name(
                        path, path.GetName()
                    )  # Register the path spline

                    # Create and apply Align to Spline tag
                    align_tag = camera.MakeTag(c4d.Talignspline)  # Use Talignspline
                    if align_tag:
                        align_tag[c4d.ALIGNTOSPLINETAG_LINK] = (
                            path  # Link the path object
                        )
                        # Set Tangential if spline_oriented? Check specific tag params if needed.
                        # align_tag[c4d.ALIGNTOSPLINETAG_TANGENTIAL] = (path_type == "spline_oriented")
                        doc.AddUndo(c4d.UNDOTYPE_NEW, align_tag)  # Add undo for new tag
                        self.log("Applied Align to Spline tag.")
                    else:
                        self.log("Warning: Failed to create Align to Spline tag.")

            # Handle other animation types (like wiggle) if needed here...
            # elif path_type == "wiggle":
            #    # Apply wiggle expression or tag... (Requires specific implementation)
            #    self.log("Info: Wiggle animation type not fully implemented in this version.")

            doc.EndUndo()  # End undo block for animation changes
            c4d.EventAdd()

            # --- MODIFIED: Contextual Return ---
            actual_camera_name = camera.GetName()
            camera_guid = str(camera.GetGUID())

            response_data = {
                "requested_name": requested_name,  # Name used to find/create
                "actual_name": actual_camera_name,  # Final name
                "guid": camera_guid,
                "camera_created": camera_created,  # Was it created by this call?
                "path_type": path_type,
                "keyframe_count": keyframe_count,
                "frame_range_set": frame_range_set,  # Frames actually keyframed
                "spline_path_guid": path_guid,  # GUID of path spline if created
                # "properties_applied": applied_properties if camera_created else {}, # Properties set during creation
            }

            return {"camera_animation": response_data}  # Keep original top-level key
            # --- END MODIFIED ---

        except Exception as e:
            doc.EndUndo()  # Ensure undo ended
            self.log(
                f"[**ERROR**] Error animating camera '{requested_name}': {str(e)}\n{traceback.format_exc()}"
            )
            # Clean up camera if created but not inserted
            if camera and not camera.GetDocument():
                try:
                    camera.Remove()
                except:
                    pass
            return {
                "error": f"Failed to animate camera: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def _get_redshift_material_id(self):
        """Detect Redshift material ID by examining existing materials.

        This function scans the active document for materials with type IDs
        in the range typical for Redshift materials (over 1,000,000).

        Returns:
            A BaseMaterial with the detected Redshift material type or None if not found
        """
        doc = c4d.documents.GetActiveDocument()

        # Look for existing Redshift materials to detect the proper ID
        for mat in doc.GetMaterials():
            mat_type = mat.GetType()
            if mat_type >= 1000000:
                self.log(
                    f"[C4D RS] Found existing Redshift material with type ID: {mat_type}"
                )
                # Try to create a material with this ID
                try:
                    rs_mat = c4d.BaseMaterial(mat_type)
                    if rs_mat and rs_mat.GetType() == mat_type:
                        self.log(
                            f"[C4D RS] Successfully created Redshift material using detected ID: {mat_type}"
                        )
                        return rs_mat
                except:
                    pass

        # If Python scripting can create Redshift materials, try this method
        try:
            # Execute a Python script to create a Redshift material
            script = """
                import c4d
                doc = c4d.documents.GetActiveDocument()
                # Try with known Redshift ID
                rs_mat = c4d.BaseMaterial(1036224)
                if rs_mat:
                    rs_mat.SetName("TempRedshiftMaterial")
                    doc.InsertMaterial(rs_mat)
                    c4d.EventAdd()
                """
            # Only try script-based approach if explicitly allowed
            if (
                hasattr(c4d, "modules")
                and hasattr(c4d.modules, "net")
                and hasattr(c4d.modules.net, "Execute")
            ):
                # Execute in a controlled way that won't affect normal operation
                import tempfile, os

                script_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
                        f.write(script.encode("utf-8"))
                        script_path = f.name

                    # Try to execute this script
                    self.execute_on_main_thread(
                        lambda: c4d.modules.net.Execute(script_path)
                    )
                finally:
                    # Always clean up the temp file
                    if script_path and os.path.exists(script_path):
                        try:
                            os.unlink(script_path)
                        except:
                            pass

            # Now look for the material we created
            temp_mat = self._find_material_by_name(doc, "TempRedshiftMaterial")
            if temp_mat and temp_mat.GetType() >= 1000000:
                self.log(
                    f"[C4D RS] Created Redshift material via script with type ID: {temp_mat.GetType()}"
                )
                # Clean up the temporary material
                doc.RemoveMaterial(temp_mat)
                c4d.EventAdd()
                # Create a fresh material with this ID
                return c4d.BaseMaterial(temp_mat.GetType())
        except Exception as e:
            self.log(
                f"[C4D RS] Script-based Redshift material creation failed: {str(e)}"
            )

        # No Redshift materials found
        return None

    def _find_material_by_name(self, doc, name):
        """Find a material by name in the document.

        Args:
            doc: The active Cinema 4D document
            name: The name of the material to find

        Returns:
            The material if found, None otherwise
        """
        if not name:
            self.log(f"[C4D] ## Warning ##: Empty material name provided")
            return None

        # Get all materials in the document
        materials = doc.GetMaterials()

        # First pass: exact match
        for mat in materials:
            if mat.GetName() == name:
                return mat

        # Second pass: case-insensitive match
        name_lower = name.lower()
        closest_match = None
        for mat in materials:
            if mat.GetName().lower() == name_lower:
                closest_match = mat
                self.log(
                    f"[C4D] Found case-insensitive match for material '{name}': '{mat.GetName()}'"
                )
                break

        if closest_match:
            return closest_match

        self.log(f"[C4D] Material not found: '{name}'")

        # If material not found, list available materials to aid debugging
        if materials:
            material_names = [mat.GetName() for mat in materials]
            self.log(f"[C4D] Available materials: {', '.join(material_names)}")

        return None

    def _is_redshift_like_material(self, mat):
        """Return True when a material looks like a Redshift/plugin-owned material."""
        if mat is None:
            return False

        mat_type = mat.GetType()
        redshift_id = getattr(c4d, "ID_REDSHIFT_MATERIAL", None)

        if redshift_id is not None and mat_type == redshift_id:
            return True

        # Existing scenes commonly store RS materials as high plugin IDs even when
        # the Redshift Python module itself is unavailable.
        return mat_type >= 1000000

    def _iter_scene_objects(self, root):
        """Yield scene objects iteratively to avoid recursion limits."""
        stack = [root] if root else []

        while stack:
            obj = stack.pop()
            while obj:
                yield obj
                child = obj.GetDown()
                if child:
                    stack.append(child)
                obj = obj.GetNext()

    def _descid_to_path(self, descid):
        """Convert a DescID into a JSON-safe list of integer IDs."""
        try:
            return [int(descid[i].id) for i in range(descid.GetDepth())]
        except Exception:
            return []

    def _serialize_material_value(self, value, depth=0):
        """Best-effort serializer for C4D/Redshift material values."""
        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, int):
            return int(value)

        if isinstance(value, float):
            if math.isfinite(value):
                return float(value)
            return str(value)

        if isinstance(value, str):
            return value

        if isinstance(value, c4d.Vector):
            return [float(value.x), float(value.y), float(value.z)]

        if isinstance(value, (list, tuple)):
            return [self._serialize_material_value(item, depth + 1) for item in value]

        if isinstance(value, c4d.BaseContainer):
            entries = []
            truncated = False
            try:
                for index, (key, child_value) in enumerate(value):
                    if index >= 10:
                        truncated = True
                        break
                    entries.append(
                        {
                            "key": int(key),
                            "value": self._serialize_material_value(
                                child_value, depth + 1
                            ),
                        }
                    )
            except Exception as exc:
                return {
                    "type": "BaseContainer",
                    "error": str(exc),
                }

            return {
                "type": "BaseContainer",
                "entries": entries,
                "truncated": truncated,
            }

        if isinstance(value, type):
            return {
                "type": "type",
                "name": value.__name__,
            }

        return {
            "type": type(value).__name__,
            "repr": repr(value),
        }

    def _sample_material_preview(self, mat, grid_size=5):
        """Sample the material preview bitmap for a representative color."""
        try:
            bmp = mat.GetPreview(0)
        except Exception as exc:
            return {"available": False, "error": str(exc)}

        if bmp is None:
            return {"available": False, "error": "no_preview"}

        width = bmp.GetBw()
        height = bmp.GetBh()
        if width <= 0 or height <= 0:
            return {"available": False, "error": "zero_size_preview"}

        center_rgb = list(bmp.GetPixel(width // 2, height // 2))

        margin_x = max(1, width // 5)
        margin_y = max(1, height // 5)
        x_start, x_end = margin_x, max(margin_x, width - margin_x)
        y_start, y_end = margin_y, max(margin_y, height - margin_y)

        samples = []
        for i in range(grid_size):
            for j in range(grid_size):
                x = x_start + (x_end - x_start) * i // max(1, grid_size - 1)
                y = y_start + (y_end - y_start) * j // max(1, grid_size - 1)
                samples.append(bmp.GetPixel(x, y))

        avg_r = sum(sample[0] for sample in samples) // len(samples)
        avg_g = sum(sample[1] for sample in samples) // len(samples)
        avg_b = sum(sample[2] for sample in samples) // len(samples)

        return {
            "available": True,
            "size": [width, height],
            "center_rgb": center_rgb,
            "average_rgb": [avg_r, avg_g, avg_b],
            "average_hex": f"#{avg_r:02x}{avg_g:02x}{avg_b:02x}",
            "sample_count": len(samples),
        }

    def _collect_material_assignments(self, doc, material):
        """Find all texture tags in the scene that reference a material."""
        assignments = []
        for obj in self._iter_scene_objects(doc.GetFirstObject()):
            tag = obj.GetFirstTag()
            while tag:
                if tag.GetType() == c4d.Ttexture:
                    try:
                        tag_material = tag[c4d.TEXTURETAG_MATERIAL]
                    except Exception:
                        tag_material = None

                    if tag_material == material:
                        assignment = {"object": obj.GetName()}
                        try:
                            assignment["projection"] = int(
                                tag[c4d.TEXTURETAG_PROJECTION]
                            )
                        except Exception:
                            pass

                        try:
                            restriction = tag[c4d.TEXTURETAG_RESTRICTION]
                            if restriction:
                                assignment["restriction"] = restriction
                        except Exception:
                            pass

                        assignments.append(assignment)

                tag = tag.GetNext()

        return assignments

    def _collect_material_description(self, mat):
        """Collect readable description entries for a material."""
        entries = []
        try:
            description = mat.GetDescription(c4d.DESCFLAGS_DESC_0)
        except Exception as exc:
            return {"error": str(exc), "entries": entries}

        for bc, descid, groupid in description:
            name = ""
            try:
                name = bc.GetString(c4d.DESC_NAME) or ""
            except Exception:
                name = ""

            path = self._descid_to_path(descid)
            record = {
                "id_path": path,
                "id": path[0] if path else None,
            }
            if name:
                record["name"] = name

            try:
                value = mat[descid]
                serialized_value = self._serialize_material_value(value)
                if serialized_value is not None:
                    record["value"] = serialized_value
            except Exception as exc:
                record["value_error"] = str(exc)

            if record.get("name") or "value" in record or "value_error" in record:
                entries.append(record)

        return {"entries": entries}

    def _collect_material_container(self, mat):
        """Collect safe values from a material's BaseContainer."""
        container = mat.GetDataInstance()
        if container is None:
            return {"entries": []}

        entries = []
        try:
            for key, value in container:
                entries.append(
                    {
                        "id": int(key),
                        "value": self._serialize_material_value(value),
                    }
                )
        except Exception as exc:
            return {
                "entries": entries,
                "error": str(exc),
            }

        return {"entries": entries}

    def _summarize_graph_nodes(self, graph, max_nodes=25):
        """Return a compact summary of graph nodes when a node graph is available."""
        try:
            import maxon
        except Exception:
            maxon = None

        node_entries = []
        for index, node in enumerate(graph.GetNodes()):
            if index >= max_nodes:
                break

            entry = {"id": str(node.GetId())}

            try:
                entry["kind"] = str(node.GetKind())
            except Exception:
                pass

            if maxon is not None:
                try:
                    asset_id_value = node.GetValue("net.maxon.node.attribute.assetid")
                    if asset_id_value:
                        asset_id = str(asset_id_value)
                        if asset_id.startswith("(") and "," in asset_id:
                            asset_id = asset_id[1:].split(",", 1)[0]
                        entry["asset_id"] = asset_id
                except Exception:
                    pass

                try:
                    node_name = node.GetValue(maxon.NODE.BASE.NAME)
                    if node_name is None:
                        node_name = node.GetValue(maxon.EffectiveName)
                    if node_name:
                        entry["name"] = str(node_name)
                except Exception:
                    pass

            try:
                entry["input_count"] = len(node.GetInputs())
            except Exception:
                pass

            try:
                entry["output_count"] = len(node.GetOutputs())
            except Exception:
                pass

            node_entries.append(entry)

        return node_entries

    def _inspect_redshift_nodespace_graph(self, mat):
        """Try to inspect the Redshift node graph via the Maxon node-space API."""
        graph_info = {
            "accessible": False,
            "candidate_spaces": [],
            "backend": "nodespace",
            "probe_strategy": "renderengine-style-node-material-reference",
            "shader_network_type_match": bool(mat.CheckType(1036224)),
        }

        try:
            import maxon
        except Exception as exc:
            graph_info["reason"] = f"maxon import failed: {exc}"
            return graph_info

        active_space = None
        try:
            active_space = c4d.GetActiveNodeSpaceId()
        except Exception:
            active_space = None

        if active_space:
            graph_info["active_node_space"] = str(active_space)

        node_material_ref = None
        try:
            node_material_ref = mat.GetNodeMaterialReference()
        except Exception as exc:
            graph_info["node_material_reference_error"] = str(exc)

        if node_material_ref is not None:
            graph_info["node_material_reference"] = repr(node_material_ref)

            for attr_name, key_name in [
                ("IsNodeBased", "is_node_based"),
                ("GetAllNimbusRefs", "nimbus_ref_count"),
            ]:
                try:
                    attr = getattr(node_material_ref, attr_name)
                    value = attr()
                    if key_name == "nimbus_ref_count":
                        value = len(value)
                    graph_info[key_name] = value
                except Exception:
                    pass

        wrapper_node_material = None
        try:
            wrapper_node_material = c4d.NodeMaterial(mat)
            graph_info["node_material_wrapper"] = repr(wrapper_node_material)
        except Exception as exc:
            graph_info["node_material_wrapper_error"] = str(exc)

        candidate_space_ids = []
        for raw_space in [
            active_space,
            "com.redshift3d.redshift4c4d.class.nodespace",
            "com.redshift3d.redshift.class.nodespace",
            "net.maxon.nodespace.standard",
        ]:
            if raw_space is None:
                continue
            space_id = str(raw_space)
            if space_id and space_id not in candidate_space_ids:
                candidate_space_ids.append(space_id)

        for space_id in candidate_space_ids:
            candidate = {"id": space_id}
            try:
                maxon_space = maxon.Id(space_id)
            except Exception as exc:
                candidate["id_error"] = str(exc)
                graph_info["candidate_spaces"].append(candidate)
                continue

            for label, ref in [
                ("reference", node_material_ref),
                ("wrapper", wrapper_node_material),
            ]:
                if ref is None:
                    continue

                try:
                    candidate[f"{label}_has_space_string"] = bool(ref.HasSpace(space_id))
                except Exception as exc:
                    candidate[f"{label}_has_space_string_error"] = str(exc)

                try:
                    candidate[f"{label}_has_space_id"] = bool(ref.HasSpace(maxon_space))
                except Exception as exc:
                    candidate[f"{label}_has_space_id_error"] = str(exc)

                for graph_mode, graph_arg in [("string", space_id), ("id", maxon_space)]:
                    try:
                        graph = ref.GetGraph(graph_arg)
                        candidate[f"{label}_graph_{graph_mode}"] = graph is not None
                        if graph is None:
                            continue

                        root = graph.GetRoot()
                        nodes = self._summarize_graph_nodes(graph)
                        candidate[f"{label}_graph_{graph_mode}_node_count"] = len(nodes)
                        candidate[f"{label}_graph_{graph_mode}_nodes"] = nodes
                        candidate[f"{label}_graph_{graph_mode}_root"] = (
                            repr(root) if root else None
                        )

                        graph_info["candidate_spaces"].append(candidate)
                        graph_info["accessible"] = True
                        graph_info["selected_space"] = space_id
                        graph_info["selected_probe"] = f"{label}:{graph_mode}"
                        graph_info["node_count"] = len(nodes)
                        graph_info["nodes"] = nodes
                        graph_info["root"] = repr(root) if root else None
                        return graph_info
                    except Exception as exc:
                        candidate[f"{label}_graph_{graph_mode}_error"] = str(exc)

            try:
                nimbus_ref = mat.GetNimbusRef(space_id)
                candidate["nimbus_ref"] = repr(nimbus_ref)
                candidate["nimbus_ref_available"] = nimbus_ref is not None
            except Exception as exc:
                candidate["nimbus_ref_error"] = str(exc)

            graph_info["candidate_spaces"].append(candidate)

        graph_info["reason"] = "No accessible Redshift node space found"
        return graph_info

    def _collect_graphview_port_entries(self, node):
        """Collect GraphView port metadata and connected destination ports."""
        port_entries = []
        outgoing_connections = []

        for direction, getter in [("input", node.GetInPorts), ("output", node.GetOutPorts)]:
            try:
                ports = list(getter())
            except Exception:
                ports = []

            for port in ports:
                entry = {
                    "direction": direction,
                }

                try:
                    entry["name"] = port.GetName(node)
                except Exception as exc:
                    entry["name_error"] = str(exc)

                try:
                    entry["main_id"] = int(port.GetMainID())
                except Exception as exc:
                    entry["main_id_error"] = str(exc)

                try:
                    entry["sub_id"] = int(port.GetSubID())
                except Exception as exc:
                    entry["sub_id_error"] = str(exc)

                try:
                    entry["connection_count"] = int(port.GetNrOfConnections())
                except Exception:
                    entry["connection_count"] = 0

                if direction == "output":
                    try:
                        destination_ports = list(port.GetDestination())
                    except Exception as exc:
                        destination_ports = []
                        entry["destination_error"] = str(exc)

                    if destination_ports:
                        entry["destination_count"] = len(destination_ports)
                        outgoing_connections.append(
                            {
                                "from_node": node.GetName(),
                                "from_port": entry.get("name"),
                                "from_main_id": entry.get("main_id"),
                                "from_sub_id": entry.get("sub_id"),
                                "destination_ports": destination_ports,
                            }
                        )

                port_entries.append(entry)

        return port_entries, outgoing_connections

    def _inspect_redshift_graphview_graph(self, mat, max_nodes=50, max_connections=200):
        """Inspect Redshift shader graphs via the legacy GraphView API when available."""
        graph_info = {
            "accessible": False,
            "backend": "redshift_graphview",
        }

        try:
            import redshift
        except Exception as exc:
            graph_info["reason"] = f"redshift import failed: {exc}"
            return graph_info

        graph_info["redshift_module_imported"] = True

        try:
            graph_info["is_instance_mrsmaterial"] = bool(
                mat.IsInstanceOf(redshift.Mrsmaterial)
            )
        except Exception as exc:
            graph_info["is_instance_mrsmaterial_error"] = str(exc)

        try:
            node_master = redshift.GetRSMaterialNodeMaster(mat)
        except Exception as exc:
            graph_info["reason"] = f"GetRSMaterialNodeMaster failed: {exc}"
            return graph_info

        if node_master is None:
            graph_info["reason"] = "GetRSMaterialNodeMaster returned None"
            return graph_info

        graph_info["node_master"] = repr(node_master)

        try:
            root = node_master.GetRoot()
        except Exception as exc:
            graph_info["reason"] = f"GvNodeMaster.GetRoot failed: {exc}"
            return graph_info

        if root is None:
            graph_info["reason"] = "GraphView root node is missing"
            return graph_info

        graph_info["root"] = {
            "name": root.GetName(),
            "operator_id": int(root.GetOperatorID()),
        }

        nodes = []
        outgoing_connections = []
        stack = [(root, 0)]
        nodes_truncated = False

        while stack:
            node, depth = stack.pop()
            if node is None:
                continue

            next_node = node.GetNext()
            if next_node is not None:
                stack.append((next_node, depth))

            child = node.GetDown()
            if child is not None:
                stack.append((child, depth + 1))

            if len(nodes) >= max_nodes:
                nodes_truncated = True
                continue

            node_entry = {
                "name": node.GetName(),
                "operator_id": int(node.GetOperatorID()),
                "depth": depth,
            }

            try:
                meta_class = node[c4d.GV_REDSHIFT_SHADER_META_CLASSNAME]
                if meta_class:
                    node_entry["meta_class"] = str(meta_class)
            except Exception:
                pass

            port_entries, node_connections = self._collect_graphview_port_entries(node)
            if port_entries:
                displayed_ports = port_entries
                if len(displayed_ports) > 8:
                    connected_ports = [
                        port
                        for port in displayed_ports
                        if port.get("connection_count", 0) > 0
                    ]
                    if connected_ports:
                        displayed_ports = connected_ports[:8]
                    else:
                        displayed_ports = displayed_ports[:8]

                    hidden_count = len(port_entries) - len(displayed_ports)
                    if hidden_count > 0:
                        node_entry["hidden_port_count"] = hidden_count

                node_entry["ports"] = displayed_ports

            outgoing_connections.extend(node_connections)
            nodes.append(node_entry)

        connections = []
        connections_truncated = False
        for connection in outgoing_connections:
            for destination_port in connection["destination_ports"]:
                if len(connections) >= max_connections:
                    connections_truncated = True
                    break

                record = {
                    "from_node": connection["from_node"],
                    "from_port": connection.get("from_port"),
                }

                if connection.get("from_main_id") is not None:
                    record["from_main_id"] = connection["from_main_id"]
                if connection.get("from_sub_id") is not None:
                    record["from_sub_id"] = connection["from_sub_id"]

                destination_node = None
                try:
                    destination_node = destination_port.GetNode()
                except Exception:
                    destination_node = None

                if destination_node is not None:
                    try:
                        record["to_node"] = destination_node.GetName()
                    except Exception:
                        pass

                    try:
                        record["to_port"] = destination_port.GetName(destination_node)
                    except Exception:
                        pass

                try:
                    record["to_main_id"] = int(destination_port.GetMainID())
                except Exception:
                    pass

                try:
                    record["to_sub_id"] = int(destination_port.GetSubID())
                except Exception:
                    pass

                if "to_node" not in record:
                    record["destination_ref"] = repr(destination_port)

                connections.append(record)

            if connections_truncated:
                break

        graph_info["accessible"] = True
        graph_info["node_count"] = len(nodes)
        graph_info["nodes"] = nodes
        graph_info["connection_count"] = len(connections)
        graph_info["connections"] = connections
        if nodes_truncated:
            graph_info["nodes_truncated"] = True
        if connections_truncated:
            graph_info["connections_truncated"] = True

        return graph_info

    def _inspect_redshift_graph(self, mat):
        """Inspect the Redshift graph through node-space and GraphView backends."""
        nodespace_info = self._inspect_redshift_nodespace_graph(mat)
        graph_info = dict(nodespace_info)
        graph_info["backend_attempts"] = ["nodespace"]

        if nodespace_info.get("accessible"):
            return graph_info

        graph_info["nodespace"] = {
            "accessible": nodespace_info.get("accessible", False),
            "reason": nodespace_info.get("reason"),
            "candidate_spaces": nodespace_info.get("candidate_spaces", []),
        }

        graphview_info = self._inspect_redshift_graphview_graph(mat)
        graph_info["backend_attempts"].append("redshift_graphview")
        graph_info["graphview"] = graphview_info

        if graphview_info.get("accessible"):
            graph_info["accessible"] = True
            graph_info["backend"] = "redshift_graphview"
            graph_info["selected_probe"] = "redshift_graphview"
            graph_info["reason"] = "GraphView fallback succeeded after nodespace probe failed"
            graph_info["node_count"] = graphview_info.get("node_count", 0)
            graph_info["nodes"] = graphview_info.get("nodes", [])
            graph_info["connection_count"] = graphview_info.get("connection_count", 0)
            graph_info["connections"] = graphview_info.get("connections", [])
            graph_info["root"] = graphview_info.get("root")
            graph_info["node_master"] = graphview_info.get("node_master")
            if graphview_info.get("nodes_truncated"):
                graph_info["nodes_truncated"] = True
            if graphview_info.get("connections_truncated"):
                graph_info["connections_truncated"] = True
            return graph_info

        graph_info["backend"] = "nodespace"
        graph_info["reason"] = (
            "No accessible Redshift node graph found via nodespace or GraphView"
        )
        return graph_info

    def handle_inspect_redshift_materials(self, command):
        """Inspect Redshift-like materials with runtime-safe fallbacks."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        material_name = command.get("material_name", "")
        include_preview = bool(command.get("include_preview", True))
        include_assignments = bool(command.get("include_assignments", True))
        include_description = bool(command.get("include_description", True))
        include_container = bool(command.get("include_container", True))
        include_graph = bool(command.get("include_graph", True))

        redshift_module_available = hasattr(c4d, "modules") and hasattr(
            c4d.modules, "redshift"
        )

        materials_to_inspect = []
        if material_name:
            target = self._find_material_by_name(doc, material_name)
            if target is None:
                return {
                    "status": "error",
                    "message": f"Material not found: {material_name}",
                }
            materials_to_inspect = [target]
        else:
            materials_to_inspect = list(doc.GetMaterials())

        inspected_materials = []
        skipped_materials = []

        for index, mat in enumerate(materials_to_inspect):
            if not self._is_redshift_like_material(mat):
                skipped_materials.append(
                    {
                        "name": mat.GetName(),
                        "type_id": mat.GetType(),
                        "reason": "not_redshift_like",
                    }
                )
                continue

            material_info = {
                "index": index,
                "name": mat.GetName(),
                "type_id": mat.GetType(),
                "redshift_like": True,
                "shader_network_type_match": bool(mat.CheckType(1036224)),
            }

            if include_preview:
                material_info["preview"] = self._sample_material_preview(mat)

            if include_assignments:
                material_info["assignments"] = self._collect_material_assignments(
                    doc, mat
                )

            description_info = None
            if include_description:
                description_info = self._collect_material_description(mat)
                material_info["description"] = description_info["entries"]
                if "error" in description_info:
                    material_info["description_error"] = description_info["error"]

            if include_container:
                container_info = self._collect_material_container(mat)
                material_info["container"] = container_info["entries"]
                if "error" in container_info:
                    material_info["container_error"] = container_info["error"]

            if include_graph:
                material_info["graph"] = self._inspect_redshift_graph(mat)

            if description_info is not None:
                for field in description_info["entries"]:
                    if field.get("id") == 21001 and field.get("name"):
                        material_info["output_label"] = field["name"]
                        break

            inspected_materials.append(material_info)

        return {
            "status": "ok",
            "scene": {
                "filename": doc.GetDocumentName(),
                "material_count": len(doc.GetMaterials()),
            },
            "capabilities": {
                "redshift_module_available": redshift_module_available,
                "redshift_material_id": getattr(c4d, "ID_REDSHIFT_MATERIAL", None),
                "preview_sampling": True,
                "graph_inspection_requested": include_graph,
                "renderengine_style_probe": True,
                "redshift_graphview_probe": True,
            },
            "materials": inspected_materials,
            "skipped_materials": skipped_materials,
        }

    def handle_validate_redshift_materials(self, command):
        """Validate Redshift node materials in the scene and fix issues when possible."""
        import maxon

        warnings = []
        fixes = []
        doc = c4d.documents.GetActiveDocument()

        try:
            # Advanced Redshift detection diagnostics
            self.log(f"[C4D] DIAGNOSTIC: Cinema 4D version: {c4d.GetC4DVersion()}")
            self.log(f"[C4D] DIAGNOSTIC: Python version: {sys.version}")

            # Check for Redshift modules more comprehensively
            redshift_module_exists = hasattr(c4d, "modules") and hasattr(
                c4d.modules, "redshift"
            )
            self.log(
                f"[C4D] DIAGNOSTIC: Redshift module exists: {redshift_module_exists}"
            )

            if redshift_module_exists:
                redshift = c4d.modules.redshift
                self.log(
                    f"[C4D] DIAGNOSTIC: Redshift module dir contents: {dir(redshift)}"
                )

                # Check for common Redshift module attributes
                for attr in [
                    "Mmaterial",
                    "MATERIAL_TYPE",
                    "GetRSMaterialNodeSpace",
                ]:
                    has_attr = hasattr(redshift, attr)
                    self.log(
                        f"[C4D] DIAGNOSTIC: Redshift module has '{attr}': {has_attr}"
                    )

            # Check if Redshift ID_REDSHIFT_MATERIAL constant exists
            has_rs_constant = hasattr(c4d, "ID_REDSHIFT_MATERIAL")
            self.log(
                f"[C4D] DIAGNOSTIC: c4d.ID_REDSHIFT_MATERIAL exists: {has_rs_constant}"
            )
            if has_rs_constant:
                self.log(
                    f"[C4D] DIAGNOSTIC: c4d.ID_REDSHIFT_MATERIAL value: {c4d.ID_REDSHIFT_MATERIAL}"
                )

            # Check all installed plugins
            plugins = c4d.plugins.FilterPluginList(c4d.PLUGINTYPE_MATERIAL, True)
            self.log(f"[C4D] DIAGNOSTIC: Found {len(plugins)} material plugins")
            for plugin in plugins:
                plugin_name = plugin.GetName()
                plugin_id = plugin.GetID()
                self.log(
                    f"[C4D] DIAGNOSTIC: Material plugin: {plugin_name} (ID: {plugin_id})"
                )

            # Continue with normal validation
            # Get the Redshift node space ID
            redshift_ns = maxon.Id("com.redshift3d.redshift4c4d.class.nodespace")

            # Log all relevant Redshift material IDs for debugging
            self.log(f"[C4D] Standard material ID: {c4d.Mmaterial}")
            self.log(
                f"[C4D] Redshift material ID (c4d.ID_REDSHIFT_MATERIAL): {c4d.ID_REDSHIFT_MATERIAL}"
            )

            # Check if Redshift module has its own material type constant
            if hasattr(c4d, "modules") and hasattr(c4d.modules, "redshift"):
                redshift = c4d.modules.redshift
                rs_material_id = getattr(redshift, "Mmaterial", None)
                if rs_material_id is not None:
                    self.log(f"[C4D] Redshift module material ID: {rs_material_id}")
                rs_material_type = getattr(redshift, "MATERIAL_TYPE", None)
                if rs_material_type is not None:
                    self.log(f"[C4D] Redshift MATERIAL_TYPE: {rs_material_type}")

            # Count of materials by type
            mat_stats = {
                "total": 0,
                "redshift": 0,
                "standard": 0,
                "fixed": 0,
                "issues": 0,
                "material_types": {},
            }

            # Validate all materials in the document
            for mat in doc.GetMaterials():
                mat_stats["total"] += 1
                name = mat.GetName()

                # Track all material types encountered
                mat_type = mat.GetType()
                if mat_type not in mat_stats["material_types"]:
                    mat_stats["material_types"][mat_type] = 1
                else:
                    mat_stats["material_types"][mat_type] += 1

                # Check if it's a Redshift node material (should be c4d.ID_REDSHIFT_MATERIAL)
                is_rs_material = mat_type == c4d.ID_REDSHIFT_MATERIAL

                # Also check for alternative Redshift material type IDs
                if not is_rs_material and mat_type >= 1000000:
                    # This is likely a Redshift material with a different ID
                    self.log(
                        f"[C4D] Found possible Redshift material with ID {mat_type}: {name}"
                    )
                    is_rs_material = True

                if not is_rs_material:
                    warnings.append(
                        f"ℹ️ '{name}': Not a Redshift node material (type: {mat.GetType()})."
                    )
                    mat_stats["standard"] += 1

                    # Auto-fix option: convert standard materials to Redshift if requested
                    if command.get("auto_convert", False):
                        try:
                            # Create new Redshift material
                            rs_mat = c4d.BaseMaterial(c4d.ID_REDSHIFT_MATERIAL)
                            rs_mat.SetName(f"RS_{name}")

                            # Copy basic properties
                            color = mat[c4d.MATERIAL_COLOR_COLOR]

                            # Set up default graph using CreateDefaultGraph
                            try:
                                rs_mat.CreateDefaultGraph(redshift_ns)
                            except Exception as e:
                                warnings.append(
                                    f"⚠️ Error creating default graph for '{name}': {str(e)}"
                                )
                                # Continue anyway and try to work with what we have

                            # Get the graph and root
                            graph = rs_mat.GetGraph(redshift_ns)
                            root = graph.GetRoot()

                            # Find the Standard Surface output
                            for node in graph.GetNodes():
                                if "StandardMaterial" in node.GetId():
                                    # Set diffuse color
                                    try:
                                        node.SetParameter(
                                            maxon.nodes.ParameterID("base_color"),
                                            maxon.Color(color.x, color.y, color.z),
                                            maxon.PROPERTYFLAGS_NONE,
                                        )
                                    except:
                                        pass
                                    break

                            # Insert the new material
                            doc.InsertMaterial(rs_mat)

                            # Find and update texture tags
                            if command.get("update_references", False):
                                obj = doc.GetFirstObject()
                                while obj:
                                    tag = obj.GetFirstTag()
                                    while tag:
                                        if tag.GetType() == c4d.Ttexture:
                                            if tag[c4d.TEXTURETAG_MATERIAL] == mat:
                                                tag[c4d.TEXTURETAG_MATERIAL] = rs_mat
                                        tag = tag.GetNext()
                                    obj = obj.GetNext()

                            fixes.append(
                                f"✅ Converted '{name}' to Redshift node material."
                            )
                            mat_stats["fixed"] += 1
                        except Exception as e:
                            warnings.append(f"❌ Failed to convert '{name}': {str(e)}")

                    continue

                # For Redshift materials, continue with validation
                if is_rs_material:
                    # It's a confirmed Redshift material
                    mat_stats["redshift"] += 1

                    # Check if it's using the Redshift node space
                    if (
                        hasattr(mat, "GetNodeMaterialSpace")
                        and mat.GetNodeMaterialSpace() != redshift_ns
                    ):
                        warnings.append(
                            f"⚠️ '{name}': Redshift material but not using correct node space."
                        )
                        mat_stats["issues"] += 1
                        continue
                else:
                    # Skip further validation for non-Redshift materials
                    continue

                # Validate the node graph
                graph = mat.GetGraph(redshift_ns)
                if not graph:
                    warnings.append(f"❌ '{name}': No node graph.")
                    mat_stats["issues"] += 1

                    # Try to fix by creating a default graph
                    if command.get("auto_fix", False):
                        try:
                            mat.CreateDefaultGraph(redshift_ns)
                            fixes.append(f"✅ Created default graph for '{name}'.")
                            mat_stats["fixed"] += 1
                        except Exception as e:
                            warnings.append(
                                f"❌ Could not create default graph for '{name}': {str(e)}"
                            )

                    continue

                # Check the root node connections
                root = graph.GetRoot()
                if not root:
                    warnings.append(f"❌ '{name}': No root node in graph.")
                    mat_stats["issues"] += 1
                    continue

                # Check if we have inputs
                inputs = root.GetInputs()
                if not inputs or len(inputs) == 0:
                    warnings.append(f"❌ '{name}': Root has no input ports.")
                    mat_stats["issues"] += 1
                    continue

                # Check the output connection
                output_port = inputs[0]  # First input is typically the main output
                output_node = output_port.GetDestination()

                if not output_node:
                    warnings.append(f"⚠️ '{name}': Output not connected.")
                    mat_stats["issues"] += 1

                    # Try to fix by creating a Standard Surface node
                    if command.get("auto_fix", False):
                        try:
                            # Create Standard Surface node
                            standard_surface = graph.CreateNode(
                                maxon.nodes.IdAndVersion(
                                    "com.redshift3d.redshift4c4d.nodes.core.standardmaterial"
                                )
                            )

                            # Connect to output
                            graph.CreateConnection(
                                standard_surface.GetOutputs()[0],  # Surface output
                                root.GetInputs()[0],  # Surface input on root
                            )

                            fixes.append(f"✅ Added Standard Surface node to '{name}'.")
                            mat_stats["fixed"] += 1
                        except Exception as e:
                            warnings.append(
                                f"❌ Could not add Standard Surface to '{name}': {str(e)}"
                            )

                    continue

                # Check that the output is connected to a Redshift Material node (Standard Surface, etc.)
                if (
                    "StandardMaterial" not in output_node.GetId()
                    and "Material" not in output_node.GetId()
                ):
                    warnings.append(
                        f"❌ '{name}': Output not connected to a Redshift Material node."
                    )
                    mat_stats["issues"] += 1
                    continue

                # Now check specific material inputs
                rs_mat_node = output_node

                # Check diffuse/base color
                base_color = None
                for input_port in rs_mat_node.GetInputs():
                    port_id = input_port.GetId()
                    if "diffuse_color" in port_id or "base_color" in port_id:
                        base_color = input_port
                        break

                if base_color is None:
                    warnings.append(f"⚠️ '{name}': No diffuse/base color input found.")
                    mat_stats["issues"] += 1
                    continue

                if not base_color.GetDestination():
                    warnings.append(
                        f"ℹ️ '{name}': Diffuse/base color input not connected."
                    )
                    # This is not necessarily an issue, just informational
                else:
                    source_node = base_color.GetDestination().GetNode()
                    source_type = "unknown"

                    # Identify the type of source
                    if "ColorTexture" in source_node.GetId():
                        source_type = "texture"
                    elif "Noise" in source_node.GetId():
                        source_type = "noise"
                    elif "Checker" in source_node.GetId():
                        source_type = "checker"
                    elif "Gradient" in source_node.GetId():
                        source_type = "gradient"
                    elif "ColorConstant" in source_node.GetId():
                        source_type = "color"

                    warnings.append(
                        f"✅ '{name}': Diffuse/base color connected to {source_type} node."
                    )

                # Check for common issues in other ports
                # Detect if there's a fresnel node present
                has_fresnel = False
                for node in graph.GetNodes():
                    if "Fresnel" in node.GetId():
                        has_fresnel = True

                        # Verify the Fresnel node has proper connections
                        inputs_valid = True
                        for input_port in node.GetInputs():
                            port_id = input_port.GetId()
                            if "ior" in port_id and not input_port.GetDestination():
                                inputs_valid = False
                                warnings.append(
                                    f"⚠️ '{name}': Fresnel node missing IOR input."
                                )
                                mat_stats["issues"] += 1

                        outputs_valid = False
                        for output_port in node.GetOutputs():
                            if output_port.GetSource():
                                outputs_valid = True
                                break

                        if not outputs_valid:
                            warnings.append(
                                f"⚠️ '{name}': Fresnel node has no output connections."
                            )
                            mat_stats["issues"] += 1

                if has_fresnel:
                    warnings.append(
                        f"ℹ️ '{name}': Contains Fresnel shader (check for potential issues)."
                    )

            # Summary stats
            summary = (
                f"Material validation complete. Found {mat_stats['total']} materials: "
                + f"{mat_stats['redshift']} Redshift, {mat_stats['standard']} Standard, "
                + f"{mat_stats['issues']} with issues, {mat_stats['fixed']} fixed."
            )

            # Update the document to apply any changes
            c4d.EventAdd()

            # Format material_types for better readability
            material_types_formatted = {}
            for type_id, count in mat_stats["material_types"].items():
                if type_id == c4d.Mmaterial:
                    name = "Standard Material"
                elif type_id == c4d.ID_REDSHIFT_MATERIAL:
                    name = "Redshift Material (using c4d.ID_REDSHIFT_MATERIAL)"
                elif type_id == 1036224:
                    name = "Redshift Material (1036224)"
                elif type_id >= 1000000:
                    name = f"Possible Redshift Material ({type_id})"
                else:
                    name = f"Unknown Type ({type_id})"

                material_types_formatted[name] = count

            # Replace the original dictionary with the formatted one
            mat_stats["material_types"] = material_types_formatted

            return {
                "status": "ok",
                "warnings": warnings,
                "fixes": fixes,
                "summary": summary,
                "stats": mat_stats,
                "ids": {
                    "standard_material": c4d.Mmaterial,
                    "redshift_material": c4d.ID_REDSHIFT_MATERIAL,
                },
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error validating materials: {str(e)}",
                "warnings": warnings,
            }

    def handle_create_material(self, command):
        """Handle create_material command with context and proper NodeMaterial support for Redshift."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        requested_name = (
            command.get("name") or command.get("material_name") or "New Material"
        )
        color_list = command.get("color", [1.0, 1.0, 1.0])  # Default to white
        properties = command.get("properties", {})
        material_type = command.get(
            "material_type", "standard"
        ).lower()  # standard, redshift
        procedural = command.get(
            "procedural", False
        )  # Currently only affects Redshift in this example
        shader_type = command.get(
            "shader_type", "noise"
        )  # Used if procedural=True for Redshift

        # Safely parse color
        color = [1.0, 1.0, 1.0]
        if isinstance(color_list, list) and len(color_list) >= 3:
            try:
                color = [
                    max(0.0, min(1.0, float(c))) for c in color_list[:3]
                ]  # Clamp 0-1
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid color data {color_list}")

        self.log(
            f"[C4D] Starting material creation: Name='{requested_name}', Type='{material_type}'"
        )

        mat = None
        has_redshift = False
        redshift_plugin_id = None
        rs_mat_id_used = None  # Store the ID actually used for RS material

        try:
            # Check for Redshift plugin
            plugins = c4d.plugins.FilterPluginList(c4d.PLUGINTYPE_MATERIAL, True)
            for plugin in plugins:
                if "redshift" in plugin.GetName().lower():
                    has_redshift = True
                    redshift_plugin_id = plugin.GetID()
                    self.log(
                        f"[C4D] Found Redshift plugin: {plugin.GetName()} (ID: {plugin_id})"
                    )
                    break  # Found it

            if material_type == "redshift" and not has_redshift:
                self.log(
                    "[C4D] ## Warning ##: Redshift requested but not found. Using standard material."
                )
                material_type = "standard"

            doc.StartUndo()  # Start undo block

            # Create material based on type
            if material_type == "redshift":
                self.log("[C4D] Attempting Redshift material creation...")
                try:
                    # Determine the Redshift material ID to use
                    rs_id = getattr(
                        c4d, "ID_REDSHIFT_MATERIAL", redshift_plugin_id or 1036224
                    )  # Prefer constant, then plugin, then default
                    rs_mat_id_used = rs_id  # Store the ID we are trying
                    self.log(f"[C4D] Using Redshift Material ID: {rs_id}")

                    mat = c4d.BaseMaterial(rs_id)
                    if not mat or mat.GetType() != rs_id:
                        raise RuntimeError(f"Failed to create material with ID {rs_id}")

                    mat.SetName(requested_name)
                    self.log(f"[C4D] Base Redshift material created: '{mat.GetName()}'")

                    # Setup node graph using maxon API (R20+)
                    try:
                        import maxon

                        redshift_ns = maxon.Id(
                            "com.redshift3d.redshift4c4d.class.nodespace"
                        )
                        node_mat = c4d.NodeMaterial(mat)  # Wrap in NodeMaterial
                        if not node_mat:
                            raise RuntimeError("Failed to create NodeMaterial wrapper")

                        # Create default graph if it doesn't exist
                        if not node_mat.HasSpace(redshift_ns):
                            graph = node_mat.CreateDefaultGraph(redshift_ns)
                            self.log("[C4D] Created default Redshift node graph")
                        else:
                            graph = node_mat.GetGraph(redshift_ns)
                            self.log("[C4D] Using existing Redshift node graph")

                        if not graph:
                            raise RuntimeError(
                                "Failed to get or create Redshift node graph"
                            )

                        # Find StandardMaterial node and set base color
                        standard_mat_node = None
                        for node in graph.GetNodes():
                            if "StandardMaterial" in node.GetId():
                                standard_mat_node = node
                                break

                        if standard_mat_node:
                            try:
                                standard_mat_node.SetParameter(
                                    maxon.nodes.ParameterID("base_color"),
                                    maxon.Color(*color),
                                    maxon.PROPERTYFLAGS_NONE,
                                )
                                self.log(f"[C4D] Set Redshift base_color to {color}")
                            except Exception as e_node:
                                self.log(
                                    f"Warning: Failed to set Redshift base_color: {e_node}"
                                )
                        else:
                            self.log(
                                "Warning: Could not find StandardMaterial node in Redshift graph to set color."
                            )

                    except ImportError:
                        self.log(
                            "Warning: 'maxon' module not found, cannot configure Redshift nodes."
                        )
                    except Exception as e_node_setup:
                        self.log(
                            f"Warning: Error setting up Redshift node graph: {e_node_setup}"
                        )

                except Exception as e_rs:
                    self.log(
                        f"[**ERROR**] Redshift material creation failed: {e_rs}\n{traceback.format_exc()}. Falling back to standard."
                    )
                    material_type = "standard"  # Fallback flag
                    mat = None  # Reset mat so standard creation runs

            # Create a standard material if needed (or if RS failed)
            if material_type == "standard":
                self.log("[C4D] Creating standard material")
                mat = c4d.BaseMaterial(c4d.Mmaterial)
                if not mat:
                    raise RuntimeError("Failed to create standard material")
                mat.SetName(requested_name)

                # Set standard material properties
                mat[c4d.MATERIAL_COLOR_COLOR] = c4d.Vector(*color)  # Set color

                # Apply additional standard properties if provided
                if (
                    "specular" in properties
                    and isinstance(properties["specular"], list)
                    and len(properties["specular"]) >= 3
                ):
                    try:
                        mat[c4d.MATERIAL_SPECULAR_COLOR] = c4d.Vector(
                            *[float(s) for s in properties["specular"][:3]]
                        )
                    except (ValueError, TypeError):
                        self.log(
                            f"Warning: Invalid specular color value {properties['specular']}"
                        )
                if "reflection" in properties:
                    try:
                        mat[c4d.MATERIAL_REFLECTION_BRIGHTNESS] = max(
                            0.0, float(properties["reflection"])
                        )  # Clamp >= 0
                    except (ValueError, TypeError):
                        self.log(
                            f"Warning: Invalid reflection value {properties['reflection']}"
                        )

            if not mat:  # Final check if creation failed completely
                raise RuntimeError("Material creation failed for unknown reason.")

            # Insert material into document
            doc.InsertMaterial(mat)
            doc.AddUndo(c4d.UNDOTYPE_NEW, mat)  # Add undo step
            doc.EndUndo()  # End undo block
            c4d.EventAdd()

            # --- MODIFIED: Contextual Return ---
            actual_name = mat.GetName()
            mat_type_id = mat.GetType()
            final_material_type = (
                "redshift" if mat_type_id == rs_mat_id_used else "standard"
            )  # Determine final type based on actual ID

            # Get final color (might differ if RS nodes failed)
            final_color = color  # Default to requested
            if final_material_type == "standard":
                try:
                    final_color = [
                        mat[c4d.MATERIAL_COLOR_COLOR].x,
                        mat[c4d.MATERIAL_COLOR_COLOR].y,
                        mat[c4d.MATERIAL_COLOR_COLOR].z,
                    ]
                except:
                    pass  # Keep requested color if read fails

            self.log(
                f"[C4D] Material created successfully: Name='{actual_name}', Type='{final_material_type}', ID={mat_type_id}"
            )

            # Note: Materials don't have GUIDs in the same way as objects, so we don't register them.
            # We return info based on the final state.
            return {
                "material": {
                    "requested_name": requested_name,
                    "actual_name": actual_name,
                    "type": final_material_type,  # Report actual type created
                    "color_set": final_color,  # Report the final color state if possible
                    "type_id": mat_type_id,
                    "redshift_available": has_redshift,
                    # Add any other relevant context about properties set
                }
            }
            # --- END MODIFIED ---

        except Exception as e:
            doc.EndUndo()  # Ensure undo ended
            error_msg = f"Failed to create material '{requested_name}': {str(e)}"
            self.log(f"[**ERROR**] {error_msg}\n{traceback.format_exc()}")
            # Clean up material if created but not inserted
            if mat and not mat.GetDocument():
                try:
                    mat.Remove()
                except:
                    pass
            return {"error": error_msg, "traceback": traceback.format_exc()}

    def handle_render_frame(
        self, command
    ):  # Renamed from handle_render_to_file to match command key
        """Render the current frame to a file, using adapted core logic."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        output_path = command.get("output_path")
        width = int(command.get("width", 640))
        height = int(command.get("height", 360))
        # Frame handling - default to current frame if not specified
        frame = command.get("frame")
        if frame is None:
            frame = doc.GetTime().GetFrame(doc.GetFps())
        else:
            try:
                frame = int(frame)
            except (ValueError, TypeError):
                self.log(f"Warning: Invalid frame value '{frame}', using current.")
                frame = doc.GetTime().GetFrame(doc.GetFps())

        self.log(
            f"[RENDER FRAME] Request: frame={frame}, size={width}x{height}, path={output_path}"
        )

        # Ensure output path is valid and directory exists
        if not output_path:
            doc_name = os.path.splitext(doc.GetDocumentName() or "Untitled")[0]
            fallback_dir = doc.GetDocumentPath() or os.path.join(
                os.path.expanduser("~"), "Desktop"
            )  # Fallback to desktop
            output_path = os.path.join(
                fallback_dir, f"{doc_name}_render_{frame:04d}.png"
            )
            self.log(
                f"[RENDER FRAME] No output path provided, using fallback: {output_path}"
            )
        else:
            output_path = os.path.normpath(os.path.expanduser(output_path))

        output_dir = os.path.dirname(output_path)
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            return {"error": f"Cannot create output directory '{output_dir}': {e}"}

        # Determine format from extension (Default to PNG)
        ext = os.path.splitext(output_path)[1].lower()
        format_map = {
            ".png": c4d.FILTER_PNG,
            ".jpg": c4d.FILTER_JPG,
            ".jpeg": c4d.FILTER_JPG,
            ".tif": c4d.FILTER_TIF,
            ".tiff": c4d.FILTER_TIF,
        }
        format_id = format_map.get(ext, c4d.FILTER_PNG)
        if format_id == c4d.FILTER_PNG and ext not in format_map:
            output_path = os.path.splitext(output_path)[0] + ".png"
            format_id = c4d.FILTER_PNG
            self.log(
                f"Warning: Unsupported output extension '{ext}', defaulting to PNG: {output_path}"
            )

        # --- Execute render task on main thread ---
        def render_task():
            bmp = None
            render_duration = 0.0
            original_rd = None  # Keep track of original RD
            rd_clone = None  # Keep track of clone RD
            temp_rd_inserted = False
            try:
                # --- Start Core Logic Adaptation ---
                if not doc:
                    return {"error": "No active document (in render_task)"}
                active_draw = doc.GetActiveBaseDraw()
                if not active_draw:
                    return {"error": "No active BaseDraw (in render_task)"}
                active_camera = (
                    active_draw.GetSceneCamera(doc) or active_draw.GetEditorCamera()
                )
                if not active_camera:
                    return {"error": "No active camera (in render_task)"}

                original_rd = doc.GetActiveRenderData()
                if not original_rd:
                    return {"error": "No active RenderData (in render_task)"}
                rd_clone = original_rd.GetClone(c4d.COPYFLAGS_NONE)
                if not rd_clone:
                    return {"error": "RenderData clone failed (in render_task)"}

                settings = rd_clone.GetDataInstance()
                if not settings:
                    raise RuntimeError("Failed to get settings instance")

                settings[c4d.RDATA_XRES] = float(width)
                settings[c4d.RDATA_YRES] = float(height)
                settings[c4d.RDATA_FRAMESEQUENCE] = c4d.RDATA_FRAMESEQUENCE_CURRENTFRAME
                settings[c4d.RDATA_SAVEIMAGE] = False  # Render to bitmap, not auto-save

                doc.InsertRenderData(rd_clone)
                temp_rd_inserted = True
                doc.SetActiveRenderData(rd_clone)

                target_time = c4d.BaseTime(frame, doc.GetFps())
                doc.SetTime(target_time)
                # --- FIXED ExecutePasses Call ---
                doc.ExecutePasses(
                    None, True, True, True, c4d.BUILDFLAGS_NONE
                )  # Use None instead of active_draw
                # --- END FIXED ---

                bmp = c4d.bitmaps.BaseBitmap()  # Use BaseBitmap
                if (
                    not bmp
                    or bmp.Init(int(width), int(height), 24) != c4d.IMAGERESULT_OK
                ):  # Use int() for dimensions
                    raise MemoryError(f"Bitmap Init failed ({width}x{height})")

                render_flags = (
                    c4d.RENDERFLAGS_EXTERNAL
                    | c4d.RENDERFLAGS_SHOWERRORS
                    | c4d.RENDERFLAGS_NODOCUMENTCLONE
                    | 0x00040000
                )
                start_time = time.time()
                result_code = c4d.documents.RenderDocument(
                    doc, settings, bmp, render_flags, None
                )
                render_duration = time.time() - start_time

                if result_code != c4d.RENDERRESULT_OK:
                    err_str = self._render_code_to_str(result_code)
                    last_c4d_err = c4d.GetLastError()
                    if last_c4d_err:
                        err_str += f" (GetLastError: {last_c4d_err})"
                    raise RuntimeError(f"RenderDocument failed: {err_str}")
                # --- End Core Logic Adaptation ---

                # Save the resulting bitmap to file
                self.log(
                    f"[RENDER FRAME] Saving bitmap to: {output_path} (Format ID: {format_id})"
                )
                save_result = bmp.Save(output_path, format_id)
                if save_result == c4d.IMAGERESULT_OK:
                    self.log(f"[RENDER FRAME] Bitmap saved successfully.")
                    return {
                        "success": True,
                        "output_path": output_path,
                        "width": width,
                        "height": height,
                        "frame": frame,
                        "render_time": render_duration,
                        "file_exists": os.path.exists(output_path),
                    }
                else:
                    return {
                        "error": f"Failed to save bitmap (Error code: {save_result})"
                    }

            except Exception as e_render:
                tb = traceback.format_exc()
                self.log(
                    f"[**ERROR**][RENDER FRAME] Error during render task: {e_render}\n{tb}"
                )
                return {
                    "error": f"Exception during render/save: {str(e_render)}",
                    "traceback": tb,
                }
            finally:
                # Cleanup render data clone
                if temp_rd_inserted and original_rd:
                    try:
                        doc.SetActiveRenderData(original_rd)
                        if rd_clone:
                            rd_clone.Remove()
                    except Exception as e_cleanup:
                        self.log(f"Warning: Error during RD cleanup: {e_cleanup}")
                # Cleanup bitmap
                if bmp:
                    try:
                        bmp.FlushAll()
                    except:
                        pass
                c4d.EventAdd()

        # Execute the task on the main thread
        response = self.execute_on_main_thread(render_task, _timeout=180)

        # Structure the final response for the tool
        if response and response.get("success"):
            return {
                "render_info": response
            }  # Return nested structure expected by server tool
        else:
            # Ensure error structure is consistent if render_task itself returns an error dict
            if isinstance(response, dict) and "error" in response:
                return response
            # Handle cases where execute_on_main_thread returned an error (like timeout)
            elif isinstance(response, dict) and "error" in response:
                return response
            else:  # Fallback for unexpected scenarios
                return {"error": "Unknown error during render frame execution."}

    def handle_apply_shader(self, command):
        """Handle apply_shader command with improved Redshift/Fresnel support and context."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        material_name = command.get("material_name", "")
        # --- MODIFIED: Identify target object ---
        identifier = None
        use_guid = False
        object_specified = False
        if command.get("guid"):  # Check for GUID first
            identifier = command.get("guid")
            use_guid = True
            object_specified = True
            self.log(f"[APPLY SHADER] Using GUID identifier for object: '{identifier}'")
        elif command.get("object_name"):
            identifier = command.get("object_name")
            use_guid = False
            object_specified = True
            self.log(f"[APPLY SHADER] Using Name identifier for object: '{identifier}'")
        # --- END MODIFIED ---

        shader_type = command.get("shader_type", "noise").lower()
        channel = command.get("channel", "color").lower()
        parameters = command.get("parameters", {})

        self.log(
            f"[APPLY SHADER] Request: Shader='{shader_type}', Channel='{channel}', Material='{material_name}', Object='{identifier}'"
        )

        mat = None
        created_new_material = False
        obj_to_apply = None

        try:
            doc.StartUndo()  # Start undo block

            # Find or create material
            if material_name:
                mat = self._find_material_by_name(doc, material_name)
            if mat is None:
                default_mat_name = (
                    material_name
                    if material_name
                    else f"{shader_type.capitalize()} Material"
                )
                mat = c4d.BaseMaterial(c4d.Mmaterial)  # Create standard by default
                if not mat:
                    raise RuntimeError("Failed to create new material")
                mat.SetName(default_mat_name)
                doc.InsertMaterial(mat)
                doc.AddUndo(c4d.UNDOTYPE_NEW, mat)
                created_new_material = True
                material_name = mat.GetName()  # Use actual name
                self.log(f"[APPLY SHADER] Created new material: '{material_name}'")

            # Find object if specified
            if object_specified:
                obj_to_apply = self.find_object_by_name(
                    doc, identifier, use_guid=use_guid
                )
                if obj_to_apply is None:
                    search_type = "GUID" if use_guid else "Name"
                    self.log(
                        f"Warning: Object '{identifier}' (searched by {search_type}) not found for shader application."
                    )
                    # Don't error out, just won't apply tag later

            # Determine if material is Redshift
            is_redshift_material = False
            rs_mat_id = getattr(
                c4d, "ID_REDSHIFT_MATERIAL", 1036224
            )  # Get RS ID safely
            if mat.GetType() == rs_mat_id:
                is_redshift_material = True
            elif mat.GetType() >= 1000000:  # General check for other RS types
                is_redshift_material = True
                self.log(
                    f"Info: Material '{material_name}' has high ID ({mat.GetType()}), treating as Redshift."
                )

            if is_redshift_material:
                self.log(
                    f"[APPLY SHADER] Applying shader to Redshift material '{material_name}'..."
                )
                # --- Redshift Node Graph Logic ---
                try:
                    import maxon

                    redshift_ns = maxon.Id(
                        "com.redshift3d.redshift4c4d.class.nodespace"
                    )
                    node_mat = c4d.NodeMaterial(mat)
                    if node_mat and node_mat.HasSpace(redshift_ns):
                        graph = node_mat.GetGraph(redshift_ns)
                        if graph:
                            with graph.BeginTransaction() as transaction:
                                # Find output node... (Simplified for brevity - assumes StandardMaterial exists)
                                material_output = None
                                for node in graph.GetNodes():
                                    if "StandardMaterial" in node.GetId():
                                        material_output = node
                                        break

                                if material_output:
                                    # Create shader node...
                                    shader_node = None
                                    shader_node_id_str = ""
                                    if shader_type == "noise":
                                        shader_node_id_str = "com.redshift3d.redshift4c4d.nodes.core.texturesampler"
                                    elif shader_type == "fresnel":
                                        shader_node_id_str = "com.redshift3d.redshift4c4d.nodes.core.fresnel"
                                    # Add more shader types here...

                                    if shader_node_id_str:
                                        shader_node = graph.AddChild(
                                            maxon.Id(), maxon.Id(shader_node_id_str)
                                        )
                                        if (
                                            shader_node and shader_type == "noise"
                                        ):  # Configure noise specific
                                            shader_node.SetParameter(
                                                maxon.nodes.ParameterID("tex0_tex"),
                                                4,
                                                maxon.PROPERTYFLAGS_NONE,
                                            )  # 4=Noise
                                            if "scale" in parameters:
                                                shader_node.SetParameter(
                                                    maxon.nodes.ParameterID(
                                                        "noise_scale"
                                                    ),
                                                    float(parameters["scale"]),
                                                    maxon.PROPERTYFLAGS_NONE,
                                                )

                                    # Connect shader node...
                                    if shader_node:
                                        # Find target port... (Simplified)
                                        target_port_id_str = (
                                            "base_color"
                                            if channel == "color"
                                            else "refl_color"
                                        )  # Example mapping
                                        target_port = material_output.GetInputs().Find(
                                            maxon.Id(target_port_id_str)
                                        )

                                        # Find source port... (Simplified)
                                        source_port_id_str = (
                                            "outcolor"
                                            if shader_type != "fresnel"
                                            else "out"
                                        )
                                        source_port = shader_node.GetOutputs().Find(
                                            maxon.Id(source_port_id_str)
                                        )

                                        if target_port and source_port:
                                            graph.CreateConnection(
                                                source_port, target_port
                                            )
                                            self.log(
                                                f"Connected RS {shader_type} node to {channel}"
                                            )
                                        else:
                                            self.log(
                                                "Warning: Could not find source/target ports for RS shader connection."
                                            )
                                    else:
                                        self.log(
                                            f"Warning: Failed to create RS {shader_type} node."
                                        )
                                else:
                                    self.log(
                                        "Warning: Could not find RS StandardMaterial output node."
                                    )
                                transaction.Commit()
                        else:
                            self.log("Warning: Could not get RS node graph.")
                    else:
                        self.log(
                            "Warning: Material is not a Redshift Node Material or lacks RS space."
                        )
                except ImportError:
                    self.log("Warning: 'maxon' module not found, cannot edit RS nodes.")
                except Exception as e_rs:
                    self.log(f"Error applying shader to RS material: {e_rs}")
                # Fallthrough to standard shader application is NOT intended here. If it's RS, we try nodes.

            else:
                # --- Standard Shader Logic (from original) ---
                self.log(
                    f"[APPLY SHADER] Applying shader to Standard material '{material_name}'..."
                )
                shader_types = {
                    "noise": 5832,
                    "gradient": 5825,
                    "fresnel": 5837,
                    "layer": 5685,
                    "checkerboard": 5831,
                }
                channel_map = {
                    "color": c4d.MATERIAL_COLOR_SHADER,
                    "luminance": c4d.MATERIAL_LUMINANCE_SHADER,
                    "transparency": c4d.MATERIAL_TRANSPARENCY_SHADER,
                    "reflection": c4d.MATERIAL_REFLECTION_SHADER,
                    "bump": c4d.MATERIAL_BUMP_SHADER,
                }  # Added bump
                shader_type_id = shader_types.get(shader_type, 5832)
                channel_id = channel_map.get(channel)

                if channel_id is None:
                    raise ValueError(f"Unsupported standard channel: {channel}")

                shader = c4d.BaseShader(shader_type_id)
                if shader is None:
                    raise RuntimeError(
                        f"Failed to create standard {shader_type} shader"
                    )

                # Apply parameters (example for noise)
                if shader_type == "noise" and hasattr(c4d, "SLA_NOISE_SCALE"):
                    if "scale" in parameters:
                        shader[c4d.SLA_NOISE_SCALE] = float(
                            parameters.get("scale", 1.0)
                        )
                    if "octaves" in parameters:
                        shader[c4d.SLA_NOISE_OCTAVES] = int(
                            parameters.get("octaves", 3)
                        )
                # Add more parameter settings for other standard shader types here...

                mat[channel_id] = shader

                # Enable the channel
                enable_map = {
                    "color": c4d.MATERIAL_USE_COLOR,
                    "luminance": c4d.MATERIAL_USE_LUMINANCE,
                    "transparency": c4d.MATERIAL_USE_TRANSPARENCY,
                    "reflection": c4d.MATERIAL_USE_REFLECTION,
                    "bump": c4d.MATERIAL_USE_BUMP,
                }
                if channel in enable_map:
                    try:
                        mat[enable_map[channel]] = True
                    except AttributeError:
                        self.log(
                            f"Warning: Could not find enable parameter for channel '{channel}'"
                        )

            mat.Update(True, True)
            doc.AddUndo(c4d.UNDOTYPE_CHANGE, mat)  # Add undo for material change

            # Apply material to object if found
            applied_to_name = "None"
            applied_to_guid = None
            if obj_to_apply:
                try:
                    # Check if object already has a texture tag for this material
                    existing_tag = None
                    for tag in obj_to_apply.GetTags():
                        if tag.GetType() == c4d.Ttexture and tag.GetMaterial() == mat:
                            existing_tag = tag
                            self.log(
                                f"Found existing texture tag for material '{material_name}' on '{obj_to_apply.GetName()}'"
                            )
                            break

                    if not existing_tag:
                        tag = obj_to_apply.MakeTag(
                            c4d.Ttexture
                        )  # Use MakeTag for safer insertion
                        if tag:
                            tag.SetMaterial(mat)
                            doc.AddUndo(c4d.UNDOTYPE_NEW, tag)
                            applied_to_name = obj_to_apply.GetName()
                            applied_to_guid = str(obj_to_apply.GetGUID())
                            self.log(
                                f"[APPLY SHADER] Applied material '{material_name}' to object '{applied_to_name}'"
                            )
                        else:
                            self.log(
                                f"Warning: Failed to create texture tag on '{obj_to_apply.GetName()}'"
                            )
                    else:
                        # Material already applied via existing tag
                        applied_to_name = obj_to_apply.GetName()
                        applied_to_guid = str(obj_to_apply.GetGUID())
                        self.log(
                            f"Material '{material_name}' was already applied to object '{applied_to_name}'"
                        )

                except Exception as e_tag:
                    self.log(
                        f"[**ERROR**] Error applying material tag to '{obj_to_apply.GetName()}': {str(e_tag)}"
                    )

            doc.EndUndo()  # End undo block
            c4d.EventAdd()

            # --- MODIFIED: Contextual Return ---
            return {
                "shader_application": {  # Changed key for clarity
                    "material_name": material_name,
                    "material_type_id": mat.GetType(),
                    "shader_type": shader_type,
                    "channel": channel,
                    "applied_to_object_name": applied_to_name,  # Name or "None"
                    "applied_to_object_guid": applied_to_guid,  # GUID or None
                    "created_new_material": created_new_material,
                    "is_redshift_material": is_redshift_material,
                }
            }
            # --- END MODIFIED ---

        except Exception as e:
            doc.EndUndo()  # Ensure undo ended
            self.log(
                f"[**ERROR**] Error applying shader: {str(e)}\n{traceback.format_exc()}"
            )
            return {
                "error": f"Failed to apply shader: {str(e)}",
                "traceback": traceback.format_exc(),
            }


    # ================================================================
    # MCP extensions — Tier 1: Introspection
    # Added 2026-04-23 for plugin development workflow (any plugin).
    # All read-only; safe to run on the worker thread without main-thread
    # synchronization (mirrors handle_get_scene_info / handle_list_objects).
    # ================================================================

    def _resolve_object(self, doc, command):
        """Resolve an object reference from a command dict.
        Accepts 'guid' (preferred) or 'object_name' / 'name' / 'identifier'.
        Returns (BaseObject_or_None, error_message_or_None)."""
        guid = command.get("guid")
        name = command.get("object_name") or command.get("identifier") or command.get("name")
        if guid:
            obj = self.find_object_by_name(doc, guid, use_guid=True)
            if not obj:
                return None, f"Object not found by GUID: {guid}"
            return obj, None
        if name:
            obj = self.find_object_by_name(doc, name, use_guid=False)
            if not obj:
                return None, f"Object not found by name: {name}"
            return obj, None
        return None, "Must provide 'guid' or 'object_name'"

    def _value_to_jsonable(self, value):
        """Convert a c4d value (Vector / Matrix / BaseTime / BaseList2D / scalar) to JSON-friendly form."""
        try:
            if value is None or isinstance(value, (int, float, str, bool)):
                return value
            if isinstance(value, c4d.Vector):
                return [value.x, value.y, value.z]
            if isinstance(value, c4d.Matrix):
                return {
                    "off": [value.off.x, value.off.y, value.off.z],
                    "v1":  [value.v1.x,  value.v1.y,  value.v1.z],
                    "v2":  [value.v2.x,  value.v2.y,  value.v2.z],
                    "v3":  [value.v3.x,  value.v3.y,  value.v3.z],
                }
            if isinstance(value, c4d.BaseTime):
                try:
                    fps = c4d.documents.GetActiveDocument().GetFps()
                    return {"frame": value.GetFrame(fps), "seconds": value.Get()}
                except Exception:
                    return {"seconds": value.Get()}
            if hasattr(value, "GetName") and hasattr(value, "GetType"):
                # BaseList2D / BaseObject / BaseShader / BaseMaterial reference
                try:
                    return {"_ref": True, "name": value.GetName(), "type_id": value.GetType()}
                except Exception:
                    return f"<{type(value).__name__}>"
            if isinstance(value, (list, tuple)):
                return [self._value_to_jsonable(v) for v in value]
            if isinstance(value, dict):
                return {str(k): self._value_to_jsonable(v) for k, v in value.items()}
            return str(value)
        except Exception as e:
            return f"<unconvertible {type(value).__name__}: {e}>"

    def _descid_to_path(self, descid):
        """Convert a c4d.DescID to a list of integer parameter IDs (one per nesting level)."""
        if descid is None:
            return []
        try:
            depth = descid.GetDepth()
        except Exception:
            return []
        path = []
        for i in range(depth):
            try:
                path.append(int(descid[i].id))
            except Exception:
                path.append(None)
        return path

    def _descid_dtype(self, descid):
        """Best-effort extraction of the dtype of the deepest level of a DescID."""
        try:
            depth = descid.GetDepth()
            if depth <= 0:
                return None
            return int(descid[depth - 1].dtype)
        except Exception:
            return None

    def handle_enumerate_descids(self, command):
        """Walk an object's full Description and return every parameter as JSON.

        This is the canonical way to discover undocumented plugin parameter IDs
        (Octane Area Light's texture/distribution input, Redshift node params, etc).
        Mirrors the workflow of C4D's 'customize palettes' attribute inspector
        but returns structured data scriptable from the MCP client side.

        Optional command params:
          name_filter (str): substring (case-insensitive) — only return params whose name contains this
          name_pattern (str): fnmatch pattern — only return params whose name matches
          include_values (bool, default True): include current_value for each param
          max_results (int, default 5000): cap on returned params
          top_level_only (bool, default False): only include params at depth 1 (skip nested groups)
        """
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}
        obj, err = self._resolve_object(doc, command)
        if err:
            return {"error": err}

        import fnmatch
        name_filter = (command.get("name_filter") or "").lower()
        name_pattern = command.get("name_pattern")
        include_values = bool(command.get("include_values", True))
        max_results = int(command.get("max_results", 5000))
        top_level_only = bool(command.get("top_level_only", False))

        try:
            desc = obj.GetDescription(c4d.DESCFLAGS_DESC_0)
        except Exception as e:
            return {"error": f"GetDescription failed: {e}"}
        if not desc:
            return {"error": "Object returned no description"}

        params = []
        truncated = False
        try:
            for entry_tuple in desc:
                # In C4D 2026 the iter() over a Description yields
                # (BaseContainer, paramid, groupid) tuples where paramid/groupid
                # may be either DescID objects OR raw tuples like (id, dtype, creator).
                # Handle both shapes defensively.
                try:
                    bc, paramid, groupid = entry_tuple
                except Exception:
                    params.append({"name": "<error>", "error": f"unexpected entry shape: {type(entry_tuple).__name__}"})
                    continue
                if not bc:
                    continue

                # Path + dtype extraction that works for both DescID and tuple shapes
                def _tuple_or_descid_path(pid):
                    if pid is None:
                        return []
                    # DescID object
                    if hasattr(pid, "GetDepth"):
                        try:
                            d = pid.GetDepth()
                            return [int(pid[i].id) for i in range(d)]
                        except Exception:
                            pass
                    # Plain tuple/list
                    try:
                        if isinstance(pid, (list, tuple)):
                            # Could be a single (id, dtype, creator) or a sequence of those
                            if len(pid) > 0 and isinstance(pid[0], (list, tuple)):
                                return [int(level[0]) for level in pid]
                            return [int(pid[0])]
                    except Exception:
                        pass
                    return [str(pid)]

                def _tuple_or_descid_dtype(pid):
                    if pid is None:
                        return None
                    if hasattr(pid, "GetDepth"):
                        try:
                            d = pid.GetDepth()
                            if d > 0:
                                return int(pid[d - 1].dtype)
                        except Exception:
                            pass
                    try:
                        if isinstance(pid, (list, tuple)) and len(pid) >= 2:
                            if isinstance(pid[0], (list, tuple)):
                                last = pid[-1]
                                if len(last) >= 2:
                                    return int(last[1])
                            return int(pid[1])
                    except Exception:
                        pass
                    return None

                try:
                    name = bc.GetString(c4d.DESC_NAME) or bc.GetString(c4d.DESC_SHORTNAME) or ""
                    short_name = bc.GetString(c4d.DESC_SHORTNAME) or ""

                    # Filtering (do BEFORE expensive value reads)
                    if name_filter and name_filter not in name.lower():
                        continue
                    if name_pattern and not fnmatch.fnmatch(name, name_pattern):
                        continue

                    path = _tuple_or_descid_path(paramid)
                    if top_level_only and len(path) > 1:
                        continue

                    entry = {
                        "path": path,
                        "name": name,
                        "short_name": short_name,
                        "dtype": _tuple_or_descid_dtype(paramid),
                        "group_path": _tuple_or_descid_path(groupid),
                    }

                    if include_values:
                        try:
                            current = obj[paramid]
                            entry["current_value"] = self._value_to_jsonable(current)
                        except Exception as e:
                            entry["current_value_error"] = str(e)

                    params.append(entry)
                    if len(params) >= max_results:
                        truncated = True
                        break
                except Exception as e:
                    params.append({"name": "<error>", "error": str(e), "traceback": traceback.format_exc()[-300:]})
        except Exception as e:
            return {
                "error": f"Description iteration failed: {e}",
                "partial_count": len(params),
                "parameters": params,
                "traceback": traceback.format_exc(),
            }

        return {
            "object": {
                "name": obj.GetName(),
                "type_id": obj.GetType(),
                "type_name": self.get_object_type_name(obj),
                "guid": str(obj.GetGUID()),
            },
            "parameter_count": len(params),
            "parameters": params,
            "truncated": truncated,
            "filter_applied": {
                "name_filter": name_filter or None,
                "name_pattern": name_pattern,
                "top_level_only": top_level_only,
            },
        }

    def handle_enumerate_userdata(self, command):
        """Enumerate user data (UserData container) on an object — separate from the regular Description."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}
        obj, err = self._resolve_object(doc, command)
        if err:
            return {"error": err}

        items = []
        try:
            ud_container = obj.GetUserDataContainer()
        except Exception as e:
            return {"error": f"GetUserDataContainer failed: {e}"}

        for descid, bc in ud_container:
            try:
                name = bc.GetString(c4d.DESC_NAME) or ""
                entry = {
                    "path": self._descid_to_path(descid),
                    "name": name,
                    "dtype": self._descid_dtype(descid),
                }
                try:
                    entry["current_value"] = self._value_to_jsonable(obj[descid])
                except Exception as e:
                    entry["current_value_error"] = str(e)
                items.append(entry)
            except Exception as e:
                items.append({"name": "<error>", "error": str(e)})

        return {
            "object": {"name": obj.GetName(), "guid": str(obj.GetGUID())},
            "userdata_count": len(items),
            "userdata": items,
        }

    def handle_find_objects(self, command):
        """Find scene objects matching one or more filters.

        Filters (any/all combine AND-style):
          name_pattern (str): fnmatch pattern against object name
          name_contains (str): case-insensitive substring of object name
          type_id (int): exact GetType() match
          type_id_min, type_id_max (int): range filter on GetType()
          max_results (int, default 200)
        """
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        import fnmatch
        name_pattern = command.get("name_pattern")
        name_contains = (command.get("name_contains") or "").lower()
        type_id = command.get("type_id")
        type_id_min = command.get("type_id_min")
        type_id_max = command.get("type_id_max")
        max_results = int(command.get("max_results", 200))

        results = []
        truncated = [False]

        def walk(obj, depth=0):
            while obj is not None:
                if len(results) >= max_results:
                    truncated[0] = True
                    return
                try:
                    name = obj.GetName()
                    tid = obj.GetType()
                    matches = True
                    if type_id is not None and tid != int(type_id):
                        matches = False
                    if matches and type_id_min is not None and tid < int(type_id_min):
                        matches = False
                    if matches and type_id_max is not None and tid > int(type_id_max):
                        matches = False
                    if matches and name_pattern and not fnmatch.fnmatch(name, name_pattern):
                        matches = False
                    if matches and name_contains and name_contains not in name.lower():
                        matches = False
                    if matches:
                        results.append({
                            "name": name,
                            "type_id": tid,
                            "type_name": self.get_object_type_name(obj),
                            "guid": str(obj.GetGUID()),
                            "depth": depth,
                        })
                    walk(obj.GetDown(), depth + 1)
                except Exception as e:
                    self.log(f"[FIND_OBJECTS] error on object: {e}")
                obj = obj.GetNext()

        walk(doc.GetFirstObject(), 0)

        return {
            "match_count": len(results),
            "matches": results,
            "truncated": truncated[0],
        }

    def handle_get_object_info(self, command):
        """Return comprehensive info on a single object: transform, visibility, layer, parent, children, tags."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}
        obj, err = self._resolve_object(doc, command)
        if err:
            return {"error": err}

        try:
            pos = obj.GetAbsPos()
            rot = obj.GetAbsRot()
            scale = obj.GetAbsScale()

            tags = []
            tag = obj.GetFirstTag()
            while tag:
                try:
                    tags.append({
                        "name": tag.GetName(),
                        "type_id": tag.GetType(),
                    })
                except Exception:
                    tags.append({"name": "<error>"})
                tag = tag.GetNext()

            child_count = 0
            ch = obj.GetDown()
            while ch:
                child_count += 1
                ch = ch.GetNext()

            parent = obj.GetUp()
            try:
                layer_obj = obj.GetLayerObject(doc)
                layer_name = layer_obj.GetName() if layer_obj else None
            except Exception:
                layer_name = None

            return {
                "name": obj.GetName(),
                "type_id": obj.GetType(),
                "type_name": self.get_object_type_name(obj),
                "guid": str(obj.GetGUID()),
                "position": [pos.x, pos.y, pos.z],
                "rotation": [rot.x, rot.y, rot.z],
                "scale": [scale.x, scale.y, scale.z],
                "editor_mode": obj.GetEditorMode(),
                "render_mode": obj.GetRenderMode(),
                "layer_name": layer_name,
                "parent_name": parent.GetName() if parent else None,
                "parent_guid": str(parent.GetGUID()) if parent else None,
                "child_count": child_count,
                "tag_count": len(tags),
                "tags": tags,
            }
        except Exception as e:
            return {"error": f"get_object_info failed: {e}", "traceback": traceback.format_exc()}

    # ---- Console log capture (Tier 2) ----

    def handle_get_console_log(self, command):
        """Return recent log entries from the MCP ring buffer.

        Combines plugin self.log() output and c4d.GePrint() output (when the
        GePrint hook is installed — happens automatically on StartServer).
        Optional filters: limit, since_ts, source ('plugin'|'c4d.GePrint'|'mcp'),
        contains (case-insensitive substring).
        """
        limit = command.get("limit")
        since_ts = command.get("since_ts")
        source_filter = command.get("source")
        contains = command.get("contains")
        try:
            entries = mcp_log_get(
                limit=int(limit) if limit else None,
                since_ts=float(since_ts) if since_ts is not None else None,
                source_filter=source_filter,
                contains=contains,
            )
        except Exception as e:
            return {"error": f"get_console_log failed: {e}"}
        return {
            "entry_count": len(entries),
            "buffer_max": MCP_LOG_BUFFER_MAX,
            "geprint_hooked": _mcp_geprint_patched,
            "entries": entries,
        }

    def handle_clear_console_log(self, command):
        """Empty the MCP console log ring buffer."""
        try:
            mcp_log_clear()
        except Exception as e:
            return {"error": f"clear_console_log failed: {e}"}
        return {"status": "ok", "buffer_cleared": True}

    # ---- Plugin lifecycle (Tier 3) ----

    def _plugin_type_name(self, type_id):
        """Map a c4d.PLUGINTYPE_* int to a human-readable label."""
        type_map = {
            c4d.PLUGINTYPE_OBJECT: "Object",
            c4d.PLUGINTYPE_TAG: "Tag",
            c4d.PLUGINTYPE_SHADER: "Shader",
            c4d.PLUGINTYPE_MATERIAL: "Material",
            c4d.PLUGINTYPE_COMMAND: "Command",
            c4d.PLUGINTYPE_TOOL: "Tool",
            c4d.PLUGINTYPE_NODE: "Node",
            c4d.PLUGINTYPE_PREFS: "Prefs",
            c4d.PLUGINTYPE_SCENESAVER: "SceneSaver",
            c4d.PLUGINTYPE_SCENELOADER: "SceneLoader",
            c4d.PLUGINTYPE_BITMAPFILTER: "BitmapFilter",
            c4d.PLUGINTYPE_BITMAPLOADER: "BitmapLoader",
            c4d.PLUGINTYPE_BITMAPSAVER: "BitmapSaver",
            c4d.PLUGINTYPE_VIDEOPOST: "VideoPost",
            c4d.PLUGINTYPE_LIBRARY: "Library",
            c4d.PLUGINTYPE_SCULPTBRUSH: "SculptBrush",
            c4d.PLUGINTYPE_FALLOFF: "Falloff",
        }
        if hasattr(c4d, "PLUGINTYPE_FIELD"):
            type_map[c4d.PLUGINTYPE_FIELD] = "Field"
        return type_map.get(type_id, f"Unknown({type_id})")

    def handle_list_installed_plugins(self, command):
        """List loaded C4D plugins matching a type filter and/or name pattern.

        Optional command params:
          plugin_type (str): one of 'object', 'tag', 'shader', 'material', 'command',
                             'tool', 'node', 'bitmapsaver', 'bitmaploader', 'videopost',
                             'sculptbrush', 'falloff', 'field' — or 'all' (default)
          plugin_id (int): exact ID match (overrides plugin_type)
          name_contains (str): case-insensitive substring on plugin name
          id_min, id_max (int): scan a plugin ID range (useful for finding Octane plugins
                                — e.g. id_min=1029525, id_max=1030000 enumerates all plugins
                                in Octane's typical ID range)
        """
        import fnmatch
        type_str = (command.get("plugin_type") or "all").lower()
        plugin_id = command.get("plugin_id")
        name_contains = (command.get("name_contains") or "").lower()
        id_min = command.get("id_min")
        id_max = command.get("id_max")

        # Defensive: not every C4D version exposes every PLUGINTYPE_* constant.
        # In C4D 2026 PLUGINTYPE_SCULPTBRUSH is missing for example. Build the
        # map with getattr fallbacks and drop unresolved entries.
        _candidate_types = {
            "object": "PLUGINTYPE_OBJECT",
            "tag": "PLUGINTYPE_TAG",
            "shader": "PLUGINTYPE_SHADER",
            "material": "PLUGINTYPE_MATERIAL",
            "command": "PLUGINTYPE_COMMAND",
            "tool": "PLUGINTYPE_TOOL",
            "node": "PLUGINTYPE_NODE",
            "bitmapsaver": "PLUGINTYPE_BITMAPSAVER",
            "bitmaploader": "PLUGINTYPE_BITMAPLOADER",
            "videopost": "PLUGINTYPE_VIDEOPOST",
            "sculptbrush": "PLUGINTYPE_SCULPTBRUSH",
            "falloff": "PLUGINTYPE_FALLOFF",
            "library": "PLUGINTYPE_LIBRARY",
            "prefs": "PLUGINTYPE_PREFS",
            "scenesaver": "PLUGINTYPE_SCENESAVER",
            "sceneloader": "PLUGINTYPE_SCENELOADER",
            "bitmapfilter": "PLUGINTYPE_BITMAPFILTER",
            "field": "PLUGINTYPE_FIELD",
        }
        type_str_to_const = {}
        for short, attr in _candidate_types.items():
            v = getattr(c4d, attr, None)
            if v is not None:
                type_str_to_const[short] = v

        if type_str == "all":
            type_filters = list(set(type_str_to_const.values()))
        else:
            t = type_str_to_const.get(type_str)
            if t is None:
                return {"error": f"Unknown plugin_type: {type_str}. Options: {sorted(type_str_to_const.keys()) + ['all']}"}
            type_filters = [t]

        results = []
        seen_ids = set()
        for ptype in type_filters:
            try:
                pl = c4d.plugins.FilterPluginList(ptype, True) or []
            except Exception as e:
                self.log(f"[LIST_PLUGINS] FilterPluginList({ptype}) failed: {e}")
                continue
            self.log(f"[LIST_PLUGINS] type={ptype} returned {len(pl)} entries")
            for p in pl:
                try:
                    pid = p.GetID()
                    # Track which type FOUND this plugin (some plugins surface via
                    # multiple types — e.g. one plugin can register as TOOL, COMMAND, and NODE).
                    # Don't dedup by id alone if the user explicitly asked for a
                    # narrower type filter; only dedup when scanning "all".
                    if type_str == "all" and pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    name = p.GetName()

                    if plugin_id is not None and pid != int(plugin_id):
                        continue
                    if name_contains and name_contains not in name.lower():
                        continue
                    if id_min is not None and pid < int(id_min):
                        continue
                    if id_max is not None and pid > int(id_max):
                        continue

                    results.append({
                        "id": pid,
                        "name": name,
                        "type_id": ptype,
                        "type_name": self._plugin_type_name(ptype),
                    })
                except Exception as e:
                    self.log(f"[LIST_PLUGINS] error reading plugin: {e}")

        results.sort(key=lambda r: (r["type_name"], r["name"]))
        return {
            "plugin_count": len(results),
            "plugins": results,
            "filter": {
                "plugin_type": type_str,
                "plugin_id": plugin_id,
                "name_contains": name_contains or None,
                "id_min": id_min,
                "id_max": id_max,
            },
        }

    # ---- Viewport / render engine (Tier 4) ----

    def _renderer_name(self, renderer_id):
        """Human-readable name for a RDATA_RENDERENGINE_* id when known."""
        known = {}
        for attr in dir(c4d):
            if attr.startswith("RDATA_RENDERENGINE_"):
                try:
                    known[getattr(c4d, attr)] = attr.replace("RDATA_RENDERENGINE_", "").lower()
                except Exception:
                    pass
        # Common third-party renderers (well-known IDs)
        third_party = {
            1029525: "Octane (octane_render)",     # OctaneRender plugin id (typical)
            1036219: "Octane (alt id seen in some builds)",
            1036220: "Redshift",
            1029988: "Arnold",
            1019642: "VRay",
            1041270: "Corona",
            1037639: "Maxwell",
            1054421: "Cycles",
        }
        return known.get(renderer_id) or third_party.get(renderer_id) or f"Unknown({renderer_id})"

    def handle_viewport_screenshot(self, command):
        """Render a quick viewport-style snapshot via the C4D render pipeline and return base64 PNG.

        Optional command params:
          width (int, default 800), height (int, default 450)
          renderer (str): 'hardware' | 'standard' | 'current' (default 'hardware')
          frame (int): which frame to render (default current)

        Renderer notes:
          - 'hardware' (default): C4D's OpenGL preview renderer. Always works,
            doesn't need a scene light, fast. Use this unless you have a reason not to.
          - 'standard': C4D's software renderer. WARNING: when Octane (or some
            other 3rd-party render engine) is installed and active, the Standard
            renderer can be hooked/intercepted and produces all-black output. We
            auto-detect a fully-black render and fall back to 'hardware' with a
            warning in the response. Standard ALSO requires at least one scene
            light to produce a visible image.
          - 'current': render through the user's currently active engine
            (Octane/Redshift/etc). Same caveats apply for those renderers.
        """
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        width = int(command.get("width", 800))
        height = int(command.get("height", 450))
        # Default changed from 'standard' to 'hardware' — Standard renderer is
        # broken in installs with Octane hooks, Hardware always works.
        renderer_pref = (command.get("renderer") or "hardware").lower()
        frame_override = command.get("frame")
        save_path = command.get("save_path")  # if set, write PNG to disk and return path

        def _resolve_renderer_id(pref):
            if pref == "standard":
                return c4d.RDATA_RENDERENGINE_STANDARD if hasattr(c4d, "RDATA_RENDERENGINE_STANDARD") else 0
            if pref == "hardware":
                return c4d.RDATA_RENDERENGINE_PREVIEWHARDWARE if hasattr(c4d, "RDATA_RENDERENGINE_PREVIEWHARDWARE") else None
            return None  # 'current' or unknown — leave as-is

        def _do_one_render(rid):
            """Run a single render pass with the given engine id (or None to leave as-is).
            Returns (bmp, settings) or raises an Exception on hard failure."""
            rd = doc.GetActiveRenderData()
            if not rd:
                raise RuntimeError("No active RenderData")
            clone = rd.GetClone()
            doc.InsertRenderData(clone)
            try:
                doc.SetActiveRenderData(clone)
                # Set engine on the clone OBJECT directly (not on a GetData() copy
                # which doesn't write back to the clone in C4D 2026).
                if rid is not None:
                    try:
                        clone[c4d.RDATA_RENDERENGINE] = rid
                    except Exception as e:
                        self.log(f"[VIEWPORT_SHOT] failed to override renderer: {e}")
                clone[c4d.RDATA_XRES] = width
                clone[c4d.RDATA_YRES] = height
                clone[c4d.RDATA_FRAMESEQUENCE] = c4d.RDATA_FRAMESEQUENCE_CURRENTFRAME
                settings = clone.GetData()

                bmp = c4d.bitmaps.MultipassBitmap(width, height, c4d.COLORMODE_RGB)
                bmp.AddChannel(True, True)

                if frame_override is not None:
                    doc.SetTime(c4d.BaseTime(int(frame_override), doc.GetFps()))
                doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_INTERNALRENDERER)

                render_flags = c4d.RENDERFLAGS_EXTERNAL | c4d.RENDERFLAGS_NODOCUMENTCLONE
                result = c4d.documents.RenderDocument(doc, settings, bmp, render_flags)
                if result != c4d.RENDERRESULT_OK:
                    raise RuntimeError(f"RenderDocument failed (result={result})")
                return bmp, settings
            finally:
                # Correct cleanup: BaseList2D.Remove() (BaseDocument has no
                # RemoveRenderData method in C4D 2026).
                try:
                    clone.Remove()
                except Exception:
                    pass
                c4d.EventAdd()

        def _is_black(bmp, w, h):
            """Cheap blackness check: sample 9 spread points; all (0,0,0) = blank.
            Used to detect Standard renderer failures from Octane pipeline hooks."""
            samples = [
                (w // 6, h // 6), (w // 2, h // 6), (5 * w // 6, h // 6),
                (w // 6, h // 2), (w // 2, h // 2), (5 * w // 6, h // 2),
                (w // 6, 5 * h // 6), (w // 2, 5 * h // 6), (5 * w // 6, 5 * h // 6),
            ]
            for x, y in samples:
                px = bmp.GetPixel(x, y)
                # px is (r, g, b) tuple/list; treat anything > 0 in any channel as non-black
                if px and any(c > 0 for c in px[:3]):
                    return False
            return True

        def _bmp_to_b64(bmp):
            mem_file = c4d.storage.MemoryFileStruct()
            mem_file.SetMemoryWriteMode()
            bmp.Save(mem_file, c4d.FILTER_PNG)
            data, _ = mem_file.GetData()
            if not data:
                raise RuntimeError("PNG encode produced empty data")
            return base64.b64encode(data).decode("ascii")

        def _render():
            bd = doc.GetActiveBaseDraw()
            cam = bd.GetSceneCamera(doc) if bd else None

            primary_rid = _resolve_renderer_id(renderer_pref)
            warnings = []
            try:
                bmp, settings = _do_one_render(primary_rid)
            except Exception as e:
                return {"error": str(e)}

            actual_rid = settings.GetInt32(c4d.RDATA_RENDERENGINE) if hasattr(settings, "GetInt32") else -1

            # Auto-fallback: if user asked for standard (or current resolved to
            # something non-hardware) and we got an all-black bitmap, retry with
            # hardware and surface a warning.
            if renderer_pref != "hardware" and _is_black(bmp, width, height):
                warnings.append(
                    f"renderer '{renderer_pref}' (engine_id={actual_rid}) produced "
                    f"all-black output — falling back to 'hardware'. "
                    f"This usually means Octane/Redshift hooks intercepted the "
                    f"Standard pipeline; use renderer='hardware' to skip this check."
                )
                hw_rid = _resolve_renderer_id("hardware")
                if hw_rid is not None and hw_rid != actual_rid:
                    try:
                        bmp, settings = _do_one_render(hw_rid)
                        actual_rid = settings.GetInt32(c4d.RDATA_RENDERENGINE) if hasattr(settings, "GetInt32") else -1
                    except Exception as e:
                        warnings.append(f"hardware fallback also failed: {e}")

            # Two output modes:
            #   save_path provided → write PNG to disk, return {path, ...}
            #   else → encode base64 inline, return {image_data, ...}
            response = {
                "width": width,
                "height": height,
                "format": "png",
                "renderer": self._renderer_name(actual_rid),
                "camera": cam.GetName() if cam else None,
            }
            if save_path:
                try:
                    parent_dir = os.path.dirname(save_path)
                    if parent_dir and not os.path.isdir(parent_dir):
                        os.makedirs(parent_dir, exist_ok=True)
                    save_result = bmp.Save(save_path, c4d.FILTER_PNG)
                    if save_result != c4d.IMAGERESULT_OK:
                        # Fall back to b64 if direct save fails
                        warnings.append(f"direct PNG save returned {save_result}, falling back to base64")
                        response["image_data"] = _bmp_to_b64(bmp)
                    else:
                        response["path"] = save_path
                        try:
                            response["file_size_bytes"] = os.path.getsize(save_path)
                        except Exception:
                            pass
                except Exception as e:
                    warnings.append(f"PNG file save failed: {e}")
                    try:
                        response["image_data"] = _bmp_to_b64(bmp)
                    except Exception as e2:
                        return {"error": f"file save failed ({e}) AND base64 fallback failed ({e2})", "warnings": warnings}
            else:
                try:
                    response["image_data"] = _bmp_to_b64(bmp)
                except Exception as e:
                    return {"error": str(e), "warnings": warnings}

            if warnings:
                response["warnings"] = warnings
            return response

        return self.execute_on_main_thread(_render, _timeout=120)

    def handle_get_viewport_state(self, command):
        """Return the active viewport's state: dims, frame rect, active camera + matrix, projection."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}
        bd = doc.GetActiveBaseDraw()
        if not bd:
            return {"error": "No active BaseDraw"}

        try:
            frame = bd.GetFrame()
            cam = bd.GetSceneCamera(doc)
            if not cam:
                cam = bd.GetEditorCamera()
            cam_info = None
            if cam:
                mg = cam.GetMg()
                cam_info = {
                    "name": cam.GetName(),
                    "type_id": cam.GetType(),
                    "guid": str(cam.GetGUID()),
                    "matrix": self._value_to_jsonable(mg),
                    "position": [mg.off.x, mg.off.y, mg.off.z],
                }
                try:
                    cam_info["focal_length_mm"] = cam[c4d.CAMERA_FOCUS]
                except Exception:
                    pass
                try:
                    cam_info["fov_horizontal_rad"] = cam[c4d.CAMERAOBJECT_FOV]
                except Exception:
                    pass

            try:
                projection = bd[c4d.BASEDRAW_DATA_PROJECTION]
            except Exception:
                projection = None

            return {
                "frame": {
                    "left": frame.get("CL"),
                    "top": frame.get("CT"),
                    "right": frame.get("CR"),
                    "bottom": frame.get("CB"),
                    "width": (frame.get("CR", 0) - frame.get("CL", 0)),
                    "height": (frame.get("CB", 0) - frame.get("CT", 0)),
                } if hasattr(frame, "get") else None,
                "raw_frame": {k: frame[k] for k in [c4d.CL, c4d.CT, c4d.CR, c4d.CB] if k in frame.GetIndexId()} if hasattr(frame, "GetIndexId") else None,
                "camera": cam_info,
                "projection_mode": projection,
                "active_renderer": self._renderer_name(doc.GetActiveRenderData()[c4d.RDATA_RENDERENGINE]) if doc.GetActiveRenderData() else None,
            }
        except Exception as e:
            return {"error": f"get_viewport_state failed: {e}", "traceback": traceback.format_exc()}

    # === Viewport shading mode + multiview ===================================

    # BaseDraw surface shading mode — written to BASEDRAW_DATA_SDISPLAYACTIVE
    # (1003). Values match c4d.BASEDRAW_SDISPLAY_* constants in C4D 2026.
    # The "_wire" variants combine surface shading with built-in wireframe
    # overlay (no need to also set the line overlay).
    _SHADING_MODE_MAP = {
        "gouraud":      0,   # BASEDRAW_SDISPLAY_GOURAUD       — full shading w/ lights
        "gouraud_wire": 1,   # BASEDRAW_SDISPLAY_GOURAUD_WIRE  — gouraud + wire overlay
        "quick":        2,   # BASEDRAW_SDISPLAY_QUICK         — quick gouraud
        "quick_wire":   3,   # BASEDRAW_SDISPLAY_QUICK_WIRE
        "flat_wire":    4,   # BASEDRAW_SDISPLAY_FLAT_WIRE     — flat shading + wire
        "hidden_line":  5,   # BASEDRAW_SDISPLAY_HIDDENLINE    — wireframe with hidden lines removed
        "noshading":    6,   # BASEDRAW_SDISPLAY_NOSHADING     — flat constant color
        "flat":         7,   # BASEDRAW_SDISPLAY_FLAT          — faceted flat shading (no smooth normals)
    }

    # Line overlay: in C4D 2026 this is split into TWO settings:
    #   BASEDRAW_DATA_LINES_ON_SHADING_ACTIVE (bool — toggles overlay on/off)
    #   BASEDRAW_DATA_WDISPLAYACTIVE          (int  — picks the overlay TYPE)
    # Values for WDISPLAYACTIVE come from c4d.BASEDRAW_WDISPLAY_*.
    _LINE_OVERLAY_MAP = {
        "none":      ("off", 0),   # turn overlay off; type doesn't matter
        "wire":      ("on",  0),   # BASEDRAW_WDISPLAY_WIREFRAME
        "isoparms":  ("on",  1),   # BASEDRAW_WDISPLAY_ISOPARMS
        "box":       ("on",  2),   # BASEDRAW_WDISPLAY_BOX
        "skeleton":  ("on",  3),   # BASEDRAW_WDISPLAY_SKELETON
    }

    # BaseDraw projection (BASEDRAW_DATA_PROJECTION) values from
    # c4d.BASEDRAW_PROJECTION_* in C4D 2026.
    _PROJECTION_MAP = {
        "perspective": 0,   # BASEDRAW_PROJECTION_PERSPECTIVE
        "parallel":    1,   # BASEDRAW_PROJECTION_PARALLEL
        "left":        2,   # BASEDRAW_PROJECTION_LEFT
        "right":       3,   # BASEDRAW_PROJECTION_RIGHT
        "front":       4,   # BASEDRAW_PROJECTION_FRONT
        "back":        5,   # BASEDRAW_PROJECTION_BACK
        "top":         6,   # BASEDRAW_PROJECTION_TOP
        "bottom":      7,   # BASEDRAW_PROJECTION_BOTTOM
        "military":    8,
        "frog":        9,
        "bird":       10,
        "dimetric":   11,
        "isometric":  12,
        "gentleman":  13,
    }

    # When line_overlay='wire' is requested on top of a base shaded mode,
    # we auto-translate to the equivalent built-in *_wire SDISPLAY mode so
    # the wireframe is visible in `viewport_screenshot` captures. C4D's
    # BASEDRAW_DATA_LINES_ON_SHADING_* settings are EDITOR-ONLY decorations —
    # they don't pass through to RenderDocument output. Other overlays
    # (isoparms / box / skeleton) have no _wire SDISPLAY equivalent and
    # remain editor-only; we surface a warning in that case.
    _MODE_WIRE_BAKED = {
        "gouraud": "gouraud_wire",
        "quick":   "quick_wire",
        "flat":    "flat_wire",
    }

    def handle_set_viewport_shading_mode(self, command):
        """Set the active viewport's shading mode + optional line overlay.

        Args (in command):
          mode (str): one of gouraud, gouraud_wire, quick, quick_wire,
                     flat_wire, hidden_line, noshading, flat
          line_overlay (str, optional): one of none, wire, isoparms, box, skeleton

        Special case: `line_overlay='wire'` combined with mode='gouraud' /
        'quick' / 'flat' is auto-translated to the built-in `*_wire` shading
        mode so the wireframe survives a viewport_screenshot. Other overlays
        (isoparms/box/skeleton) only show in the live editor — a warning is
        included in the response when those are requested.

        Returns previous values so callers can restore state.
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}
            bd = doc.GetActiveBaseDraw()
            if not bd:
                return {"error": "No active BaseDraw"}

            mode = command.get("mode")
            line = command.get("line_overlay")

            if mode is not None and mode not in self._SHADING_MODE_MAP:
                return {
                    "error": f"Unknown mode '{mode}'",
                    "valid_modes": list(self._SHADING_MODE_MAP.keys()),
                }
            if line is not None and line not in self._LINE_OVERLAY_MAP:
                return {
                    "error": f"Unknown line_overlay '{line}'",
                    "valid_line_overlays": list(self._LINE_OVERLAY_MAP.keys()),
                }

            # Read previous state — set both ACTIVE and INACTIVE so the
            # change applies to selected and unselected objects uniformly.
            prev_mode = bd[c4d.BASEDRAW_DATA_SDISPLAYACTIVE]
            prev_lines_on = bd[c4d.BASEDRAW_DATA_LINES_ON_SHADING_ACTIVE]
            prev_w = bd[c4d.BASEDRAW_DATA_WDISPLAYACTIVE]

            warnings = []
            translated = False

            # Auto-translate wire overlay → built-in *_wire SDISPLAY mode.
            effective_mode = mode
            if line == "wire" and mode in self._MODE_WIRE_BAKED:
                effective_mode = self._MODE_WIRE_BAKED[mode]
                translated = True
                # Don't set the editor-only line overlay; the new mode bakes
                # wires into the render path.
                line = "none"
            elif line in ("isoparms", "box", "skeleton"):
                warnings.append(
                    f"line_overlay='{line}' is editor-only — it will show "
                    f"in C4D's live viewport but NOT in viewport_screenshot "
                    f"captures (C4D's render pipeline ignores these overlays)."
                )

            if effective_mode is not None:
                v = self._SHADING_MODE_MAP[effective_mode]
                bd[c4d.BASEDRAW_DATA_SDISPLAYACTIVE] = v
                bd[c4d.BASEDRAW_DATA_SDISPLAYINACTIVE] = v
            if line is not None:
                on_off, w_val = self._LINE_OVERLAY_MAP[line]
                bd[c4d.BASEDRAW_DATA_LINES_ON_SHADING_ACTIVE] = (on_off == "on")
                bd[c4d.BASEDRAW_DATA_LINES_ON_SHADING_INACTIVE] = (on_off == "on")
                bd[c4d.BASEDRAW_DATA_WDISPLAYACTIVE] = w_val
                bd[c4d.BASEDRAW_DATA_WDISPLAYINACTIVE] = w_val

            c4d.EventAdd()

            inv_shade = {v: k for k, v in self._SHADING_MODE_MAP.items()}
            response = {
                "status": "ok",
                "mode": mode,
                "effective_mode": effective_mode,
                "line_overlay": line,
                "previous_mode": inv_shade.get(prev_mode, prev_mode),
                "previous_lines_on": bool(prev_lines_on),
                "previous_w_display": prev_w,
            }
            if translated:
                response["wire_overlay_translated_to_baked_mode"] = True
            if warnings:
                response["warnings"] = warnings
            return response
        except Exception as e:
            return {"error": f"set_viewport_shading_mode failed: {e}", "traceback": traceback.format_exc()}

    def handle_viewport_screenshot_multiview(self, command):
        """Capture 4 standard views (perspective, top, front, right) by toggling
        the BaseDraw projection between captures, then restore original projection.

        Args (in command):
          width  (int, default 400)
          height (int, default 300)
          renderer (str, default 'hardware') — same options as viewport_screenshot
          views (list of str, optional) — subset of perspective/top/front/right/etc.
              Default: ["perspective", "top", "front", "right"]
          save_dir (str, optional) — if provided, write each PNG to this dir
              as multiview_<viewname>.png and return file paths instead of base64

        Returns:
          {"status": "ok", "views": [{name, image_data|path, width, height}, ...]}
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}
            bd = doc.GetActiveBaseDraw()
            if not bd:
                return {"error": "No active BaseDraw"}

            width = int(command.get("width", 400))
            height = int(command.get("height", 300))
            renderer_pref = command.get("renderer", "hardware")
            requested_views = command.get("views") or ["perspective", "top", "front", "right"]
            save_dir = command.get("save_dir")

            for v in requested_views:
                if v not in self._PROJECTION_MAP:
                    return {
                        "error": f"Unknown view '{v}'",
                        "valid_views": list(self._PROJECTION_MAP.keys()),
                    }

            prev_proj = bd[c4d.BASEDRAW_DATA_PROJECTION]
            # Snapshot the per-projection display mode so we can restore it.
            # C4D's BaseDraw stores SDISPLAYACTIVE per current projection — when
            # the projection toggles to an ortho view that was previously left
            # in 'box' or 'wire' mode, the renderer falls back to bbox shading
            # and the shot looks empty. Force gouraud per-view, then restore.
            prev_sdisplay_per_view = {}
            for v in requested_views:
                bd[c4d.BASEDRAW_DATA_PROJECTION] = self._PROJECTION_MAP[v]
                prev_sdisplay_per_view[v] = bd[c4d.BASEDRAW_DATA_SDISPLAYACTIVE]

            # Auto-create save_dir if needed; previously a missing dir caused
            # every per-view file write to silently fail and the wrapper
            # response still came back as "ok".
            if save_dir:
                try:
                    os.makedirs(save_dir, exist_ok=True)
                except Exception as e:
                    return {"error": f"save_dir create failed: {e}"}

            captures = []
            try:
                for view_name in requested_views:
                    bd[c4d.BASEDRAW_DATA_PROJECTION] = self._PROJECTION_MAP[view_name]
                    bd[c4d.BASEDRAW_DATA_SDISPLAYACTIVE] = 0  # gouraud per-iter
                    c4d.EventAdd()
                    sub = self.handle_viewport_screenshot({
                        "width": width,
                        "height": height,
                        "renderer": renderer_pref,
                    })
                    if "error" in sub:
                        captures.append({"name": view_name, "error": sub["error"]})
                        continue
                    entry = {
                        "name": view_name,
                        "width": sub.get("width", width),
                        "height": sub.get("height", height),
                    }
                    if save_dir:
                        try:
                            path = os.path.join(save_dir, f"multiview_{view_name}.png")
                            with open(path, "wb") as f:
                                f.write(base64.b64decode(sub["image_data"]))
                            entry["path"] = path
                        except Exception as e:
                            entry["error"] = f"save failed: {e}"
                    else:
                        entry["image_data"] = sub.get("image_data")
                    if sub.get("warnings"):
                        entry["warnings"] = sub["warnings"]
                    captures.append(entry)
            finally:
                # Restore each per-view display mode, then projection
                for v, prev_s in prev_sdisplay_per_view.items():
                    bd[c4d.BASEDRAW_DATA_PROJECTION] = self._PROJECTION_MAP[v]
                    bd[c4d.BASEDRAW_DATA_SDISPLAYACTIVE] = prev_s
                bd[c4d.BASEDRAW_DATA_PROJECTION] = prev_proj
                c4d.EventAdd()

            return {
                "status": "ok",
                "views": captures,
                "renderer": renderer_pref,
                "save_dir": save_dir,
            }
        except Exception as e:
            return {"error": f"viewport_screenshot_multiview failed: {e}", "traceback": traceback.format_exc()}

    # === Modeling commands (SendModelingCommand wrapper) =====================

    # Maps canonical op names → MCOMMAND_* constants. Stored as integer literals
    # so missing-symbol AttributeErrors on rare builds don't kill the dispatch
    # table; resolved at runtime via getattr with these as fallbacks.
    # Pure-math ops that bypass SendModelingCommand entirely (C4D doesn't
    # expose them as MCOMMAND_*; they're CommandData / direct vert manipulation).
    _PURE_MATH_OPS = {"axis_center"}

    _MODELING_OP_MAP = {
        # Axis / origin (handled via pure math in _do_axis_center, NOT
        # SendModelingCommand — C4D 2026 has no MCOMMAND_AXIS constant.)
        "axis_center":              None,
        # Topology hygiene
        "optimize":                 "MCOMMAND_OPTIMIZE",
        # Make-editable / generator baking
        "make_editable":            "MCOMMAND_MAKEEDITABLE",
        "current_state_to_object":  "MCOMMAND_CURRENTSTATETOOBJECT",
        # Subdivision / smoothing
        "subdivide":                "MCOMMAND_SUBDIVIDE",
        "smooth":                   "MCOMMAND_SMOOTH",
        # Connect / split / join
        "connect":                  "MCOMMAND_JOIN",
        "split":                    "MCOMMAND_SPLIT",
        "disconnect":               "MCOMMAND_DISCONNECT",
        # Polygon-level edits
        "bevel":                    "MCOMMAND_BEVEL",
        "inset":                    "MCOMMAND_INNER_EXTRUDE",
        "extrude":                  "MCOMMAND_EXTRUDE",
        "delete":                   "MCOMMAND_DELETE",
        # Mesh conversion
        "polygonize":               "MCOMMAND_POLYGONIZE",
        "triangulate":              "MCOMMAND_TRIANGULATE",
        "untriangulate":            "MCOMMAND_UNTRIANGULATE",
    }

    def _do_axis_center(self, obj, params):
        """Recenter the object's axis to its bbox center (or world origin).

        Pure-math implementation. C4D's "Axis Center" tool is exposed as a
        CommandData plugin, not a SendModelingCommand op — there is no
        MCOMMAND_AXIS constant in C4D 2026. So we reproduce the operation:

          1. Compute the bbox center of the object's local-space verts (or
             use world_center / keep mode).
          2. Subtract that center from every vert (so verts are now relative
             to the new axis).
          3. Shift the object's matrix by the same offset, transformed into
             world space, so the visible position is unchanged.

        Args:
          obj: PolygonObject (or any with GetAllPoints/SetPoint)
          params:
            center_mode: 'bbox' (default) — recenter to local bbox midpoint
                         'world_center' — set axis to world origin (verts
                                          become world-positioned)
                         'keep' — no-op (returned for symmetry)

        Returns: dict with old_axis, new_axis, center_offset_local.
        """
        center_mode = (params or {}).get("center_mode", "bbox")
        if center_mode == "keep":
            return {"center_mode": "keep", "no_op": True}
        if not hasattr(obj, "GetPointCount") or obj.GetPointCount() == 0:
            return {"error": "axis_center requires a poly/spline object with points"}

        if center_mode == "world_center":
            # Move axis to world origin: shift verts by current world position
            # so they end up at world-space coords; then zero out the matrix.
            mg = obj.GetMg()
            for i in range(obj.GetPointCount()):
                p = obj.GetPoint(i)
                # Transform p by mg, then subtract origin = transform by mg
                pw = mg.Mul(p)
                obj.SetPoint(i, pw)
            obj.SetMl(c4d.Matrix())
            obj.Message(c4d.MSG_UPDATE)
            return {"center_mode": "world_center", "old_axis_world": [mg.off.x, mg.off.y, mg.off.z]}

        # Default: bbox-center
        pts = obj.GetAllPoints()
        if not pts:
            return {"error": "no points"}
        xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
        c_local = c4d.Vector((min(xs)+max(xs))*0.5, (min(ys)+max(ys))*0.5, (min(zs)+max(zs))*0.5)
        for i in range(obj.GetPointCount()):
            obj.SetPoint(i, obj.GetPoint(i) - c_local)
        # Shift the local matrix so the visible position is preserved.
        # The local offset c_local must be transformed by the matrix's basis
        # (rotation+scale, NOT translation) to become a world-space delta.
        m = obj.GetMl()
        world_shift = m.v1 * c_local.x + m.v2 * c_local.y + m.v3 * c_local.z
        m.off = m.off + world_shift
        obj.SetMl(m)
        obj.Message(c4d.MSG_UPDATE)
        return {
            "center_mode": "bbox",
            "center_offset_local": [c_local.x, c_local.y, c_local.z],
            "new_axis_world": [m.off.x, m.off.y, m.off.z],
        }

    def _build_modeling_bc(self, op, params):
        """Build the BaseContainer of MDATA_* params for a given op."""
        bc = c4d.BaseContainer()
        params = params or {}

        if op == "axis_center":
            # Recenter axis to bounding-box midpoint of geometry.
            # MDATA_AXIS_MODE: 0=keep, 1=center, 2=BoundingBox center
            mode = params.get("center_mode", "bbox")
            mode_val = {"keep": 0, "world_center": 1, "bbox": 2}.get(mode, 2)
            try:
                bc[c4d.MDATA_AXIS_MODE] = mode_val
            except Exception:
                bc[1000] = mode_val  # fallback to known constant id
        elif op == "optimize":
            bc[c4d.MDATA_OPTIMIZE_TOLERANCE] = float(params.get("tolerance", 0.01))
            bc[c4d.MDATA_OPTIMIZE_POINTS] = bool(params.get("merge_points", True))
            bc[c4d.MDATA_OPTIMIZE_POLYGONS] = bool(params.get("merge_polys", True))
            bc[c4d.MDATA_OPTIMIZE_UNUSEDPOINTS] = bool(params.get("remove_unused", True))
        elif op == "subdivide":
            bc[c4d.MDATA_SUBDIVIDE_HYPER] = bool(params.get("hyper", False))
            bc[c4d.MDATA_SUBDIVIDE_SUB] = int(params.get("levels", 1))
        elif op == "smooth":
            bc[c4d.MDATA_SMOOTH_TYPE] = int(params.get("smooth_type", 0))  # 0=Laplacian
            try:
                bc[c4d.MDATA_SMOOTH_STRENGTH] = float(params.get("strength", 0.1))
            except Exception:
                pass
            try:
                bc[c4d.MDATA_SMOOTH_ITERATIONS] = int(params.get("iterations", 1))
            except Exception:
                pass
        elif op == "bevel":
            for cname, default in [
                ("MDATA_BEVEL_OFFSET_OLD_OFFSET", float(params.get("offset", 1.0))),
                ("MDATA_BEVEL_OFFSET_OLD_SUBDIVISION", int(params.get("subdivision", 1))),
            ]:
                cid = getattr(c4d, cname, None)
                if cid is not None:
                    bc[cid] = default

        return bc

    def handle_run_modeling_command(self, command):
        """Wrapper around c4d.utils.SendModelingCommand for the most-used ops.

        Args (in command):
          op (str)            — canonical op name (see _MODELING_OP_MAP keys)
          targets (list[str]) — object names or GUIDs; if empty/None, uses
                                the current document selection
          params (dict)       — op-specific keyword args (see per-op docs in
                                _build_modeling_bc); optional
          mode (str)          — selection mode: 'all' | 'points' | 'edges' | 'polygons'
                                (default 'all')

        Returns: {status, op, results: [{target, ok, [new_objects], [error]}, ...]}
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            op = (command.get("op") or "").lower()
            if op not in self._MODELING_OP_MAP:
                return {
                    "error": f"Unknown op '{op}'",
                    "valid_ops": list(self._MODELING_OP_MAP.keys()),
                }

            # Pure-math ops (axis_center et al.) bypass SendModelingCommand
            # entirely. Resolve the C4D MCOMMAND constant only for the ops
            # that actually use it.
            is_pure_math = op in self._PURE_MATH_OPS
            mcommand = None
            if not is_pure_math:
                mcommand_const_name = self._MODELING_OP_MAP[op]
                if mcommand_const_name is None:
                    return {"error": f"Op '{op}' has no MCOMMAND mapping (mark as pure-math?)"}
                mcommand = getattr(c4d, mcommand_const_name, None)
                if mcommand is None:
                    return {"error": f"This C4D build is missing constant {mcommand_const_name}"}

            # Resolve targets
            target_names = command.get("targets") or []
            params = command.get("params") or {}
            mode_str = (command.get("mode") or "all").lower()
            mode_map = {
                "all": c4d.MODELINGCOMMANDMODE_ALL,
                "points": c4d.MODELINGCOMMANDMODE_POINTSELECTION,
                "edges": c4d.MODELINGCOMMANDMODE_EDGESELECTION,
                "polygons": c4d.MODELINGCOMMANDMODE_POLYGONSELECTION,
            }
            mcm_mode = mode_map.get(mode_str, c4d.MODELINGCOMMANDMODE_ALL)

            targets = []
            if target_names:
                for name in target_names:
                    obj = self.find_object_by_name(doc, name)
                    if obj:
                        targets.append(obj)
                    else:
                        return {"error": f"Target '{name}' not found in scene"}
            else:
                sel = doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_NONE) or []
                targets = list(sel)

            if not targets:
                return {"error": "No targets — none specified and no active selection"}

            results = []
            for obj in targets:
                try:
                    if is_pure_math:
                        # Pure-math branch: dispatch by op name.
                        if op == "axis_center":
                            pm_res = self._do_axis_center(obj, params)
                        else:
                            pm_res = {"error": f"Unhandled pure-math op '{op}'"}
                        entry = {
                            "target": obj.GetName(),
                            "ok": "error" not in pm_res,
                            **pm_res,
                        }
                        results.append(entry)
                        continue

                    bc = self._build_modeling_bc(op, params)
                    result = c4d.utils.SendModelingCommand(
                        command=mcommand,
                        list=[obj],
                        mode=mcm_mode,
                        bc=bc,
                        doc=doc,
                    )
                    entry = {
                        "target": obj.GetName(),
                        "ok": bool(result),
                    }
                    # MAKEEDITABLE / CURRENTSTATETOOBJECT return new BaseObject(s)
                    if isinstance(result, list):
                        new_names = []
                        for o in result:
                            try:
                                if hasattr(o, "GetName"):
                                    new_names.append(o.GetName())
                                    # Insert new objects into the doc so they're visible
                                    if not o.GetUp() and not o.GetDocument():
                                        doc.InsertObject(o)
                            except Exception:
                                pass
                        if new_names:
                            entry["new_objects"] = new_names
                    results.append(entry)
                except Exception as e:
                    results.append({
                        "target": obj.GetName(),
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    })

            c4d.EventAdd()
            return {"status": "ok", "op": op, "mode": mode_str, "results": results}
        except Exception as e:
            return {"error": f"run_modeling_command failed: {e}", "traceback": traceback.format_exc()}

    # === Vertex Map ops ======================================================

    def _resolve_target_object(self, command, doc, key="target"):
        """Resolve a target polygon object from command[key] (name) or selection."""
        name = command.get(key)
        if name:
            obj = self.find_object_by_name(doc, name)
            if obj is None:
                return None, f"Object '{name}' not found"
            return obj, None
        sel = doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_NONE) or []
        if not sel:
            return None, "No target specified and no active selection"
        return sel[0], None

    def _find_vertex_map_tag(self, obj, name=None):
        """Find a Vertex Map tag on obj. If name is given, find by name; else first."""
        t = obj.GetFirstTag()
        first = None
        while t:
            if t.GetType() == c4d.Tvertexmap:
                if name is None:
                    return t
                if t.GetName() == name:
                    return t
                if first is None:
                    first = t
            t = t.GetNext()
        return first if name is None else None

    def handle_vertex_map_stats(self, command):
        """Compute statistics for a Vertex Map tag.

        Args (in command):
          target (str, optional)   — object name; defaults to active selection
          vmap_name (str, optional) — vertex map tag name; defaults to first

        Returns: count, min, max, sum, mean, painted_count (weight > 0),
        zero_count, full_count (weight >= 0.999), histogram (10 bins).
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            obj, err = self._resolve_target_object(command, doc, key="target")
            if err:
                return {"error": err}
            if obj.GetType() != c4d.Opolygon:
                return {"error": f"Target '{obj.GetName()}' is not a polygon mesh"}

            vmap_name = command.get("vmap_name")
            tag = self._find_vertex_map_tag(obj, vmap_name)
            if tag is None:
                return {"error": f"No vertex map tag found on '{obj.GetName()}'" + (f" with name '{vmap_name}'" if vmap_name else "")}

            data = tag.GetAllHighlevelData()
            if data is None:
                return {"error": "Vertex map has no data"}

            data = list(data)
            n = len(data)
            if n == 0:
                return {"error": "Vertex map is empty"}

            mn = min(data)
            mx = max(data)
            s = sum(data)
            mean = s / n
            painted = sum(1 for v in data if v > 0.0)
            zeroes = sum(1 for v in data if v == 0.0)
            fulls = sum(1 for v in data if v >= 0.999)

            # 10-bin histogram of weights in [0,1]
            bins = [0] * 10
            for v in data:
                idx = max(0, min(9, int(v * 10)))
                bins[idx] += 1

            return {
                "status": "ok",
                "object": obj.GetName(),
                "vmap_name": tag.GetName(),
                "vertex_count": n,
                "min": mn,
                "max": mx,
                "sum": s,
                "mean": mean,
                "painted_count": painted,
                "painted_fraction": painted / n if n else 0.0,
                "zero_count": zeroes,
                "full_count": fulls,
                "histogram": {
                    "bins": bins,
                    "edges": [round(i * 0.1, 1) for i in range(11)],
                    "labels": [
                        f"{round(i*0.1,1)}-{round((i+1)*0.1,1)}" for i in range(10)
                    ],
                },
            }
        except Exception as e:
            return {"error": f"vertex_map_stats failed: {e}", "traceback": traceback.format_exc()}

    def handle_vertex_map_threshold_to_polygon_selection(self, command):
        """Convert a vertex map threshold into a polygon selection tag.

        For each polygon, if any vertex (default) or all vertices (require_all=True)
        of the polygon has weight >= threshold, the polygon is added to the
        selection. Creates or updates a polygon selection tag with the given name.

        Args (in command):
          target (str, optional)        — object name; defaults to active selection
          vmap_name (str, optional)     — vertex map tag name; defaults to first
          threshold (float, default 0.5)
          require_all (bool, default False) — if True, ALL of poly's verts must
                                               meet threshold; default any-vert
          selection_name (str, default 'vmap_threshold')
          replace_existing (bool, default True) — if a selection tag with this
                                                   name exists, replace its content

        Returns: object name, vmap name, threshold, polygon_count_selected,
        selection_tag_name.
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            obj, err = self._resolve_target_object(command, doc, key="target")
            if err:
                return {"error": err}
            if obj.GetType() != c4d.Opolygon:
                return {"error": f"Target '{obj.GetName()}' is not a polygon mesh"}

            vmap_name = command.get("vmap_name")
            tag = self._find_vertex_map_tag(obj, vmap_name)
            if tag is None:
                return {"error": f"No vertex map tag found on '{obj.GetName()}'"}

            threshold = float(command.get("threshold", 0.5))
            require_all = bool(command.get("require_all", False))
            selection_name = command.get("selection_name", "vmap_threshold")
            replace_existing = bool(command.get("replace_existing", True))

            weights = tag.GetAllHighlevelData()
            if weights is None or len(weights) != obj.GetPointCount():
                return {"error": f"Vertex map weight count ({len(weights) if weights else 0}) != point count ({obj.GetPointCount()})"}

            n_polys = obj.GetPolygonCount()
            selected_indices = []
            for i in range(n_polys):
                p = obj.GetPolygon(i)
                is_quad = (p.c != p.d)
                idxs = (p.a, p.b, p.c, p.d) if is_quad else (p.a, p.b, p.c)
                vals = [weights[k] for k in idxs]
                if require_all:
                    if all(v >= threshold for v in vals):
                        selected_indices.append(i)
                else:
                    if any(v >= threshold for v in vals):
                        selected_indices.append(i)

            # Find or create selection tag
            existing = None
            t = obj.GetFirstTag()
            while t:
                if t.GetType() == c4d.Tpolygonselection and t.GetName() == selection_name:
                    existing = t
                    break
                t = t.GetNext()

            if existing and not replace_existing:
                return {
                    "error": f"Polygon selection '{selection_name}' already exists; "
                             f"set replace_existing=True to overwrite",
                    "existing_count": existing.GetBaseSelect().GetCount() if existing else None,
                }

            if existing:
                sel = existing
                sel.GetBaseSelect().DeselectAll()
            else:
                sel = obj.MakeTag(c4d.Tpolygonselection)
                sel.SetName(selection_name)

            bs = sel.GetBaseSelect()
            for idx in selected_indices:
                bs.Select(idx)

            c4d.EventAdd()

            return {
                "status": "ok",
                "object": obj.GetName(),
                "vmap_name": tag.GetName(),
                "threshold": threshold,
                "require_all": require_all,
                "polygon_count_total": n_polys,
                "polygon_count_selected": len(selected_indices),
                "selection_tag_name": selection_name,
                "selection_replaced": existing is not None,
            }
        except Exception as e:
            return {
                "error": f"vertex_map_threshold_to_polygon_selection failed: {e}",
                "traceback": traceback.format_exc(),
            }

    # === UV ops ==============================================================

    def handle_uv_layout_stats(self, command):
        """Compute layout statistics for the UV tag of a polygon mesh.

        Walks all polygons, bins their UV centroids by connected components
        (UV-island detection via union-find on UV-position), measures bbox
        per island, area per island in both UV space and 3D space (giving
        a distortion / texel-density ratio), and detects overlap between
        islands by per-cell bin density.

        Args (in command):
          target (str, optional) — object name; defaults to active selection
          quantize (int, default 1000000) — UV-position quantization for
            connectivity detection (6 decimal places by default)

        Returns:
          object, polygon_count, uv_bbox_global,
          islands: [{id, polygon_count, uv_bbox, uv_area, world_area,
                     distortion (sqrt(world/uv) — texel density factor)}],
          overlap_grid: {grid_res, occupied_cells, overlap_cells (cells with
            polygons whose 3D centroids span > 15% of mesh bbox)}
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            obj, err = self._resolve_target_object(command, doc, key="target")
            if err:
                return {"error": err}
            if obj.GetType() != c4d.Opolygon:
                return {"error": f"Target '{obj.GetName()}' is not a polygon mesh"}

            uv_tag = obj.GetTag(c4d.Tuvw)
            if uv_tag is None:
                return {"error": f"'{obj.GetName()}' has no UVW tag"}

            n_polys = obj.GetPolygonCount()
            quantize = int(command.get("quantize", 1000000))
            src_pts = obj.GetAllPoints()

            # Union-find for UV-island detection (poly-level connectivity by
            # shared quantized UV vertex position)
            parent = list(range(n_polys))
            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x
            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            # Map (3D-point-idx, quantized UV) → first polygon that referenced it.
            # Keying on POINT INDEX too is the difference between correct
            # UV-seam-aware connectivity and naive UV-position dedup. Two
            # polygons with stacked UVs (e.g. primitive cube where all 6 faces
            # use [0,1]^2) are NOT in the same UV island unless they also
            # share a 3D point at that UV coord.
            uv_vert_to_poly = {}

            poly_uv_corners = []  # (poly_idx, [(ua,ub,uc,ud)], uv_area, p3d_centroid, world_area)
            uv_min_global = [1e9, 1e9]
            uv_max_global = [-1e9, -1e9]

            for i in range(n_polys):
                poly = obj.GetPolygon(i)
                uv = uv_tag.GetSlow(i)
                ua, ub, uc, ud = uv["a"], uv["b"], uv["c"], uv["d"]

                # Per-corner: union polys that share BOTH the 3D point index
                # AND the UV position at that point (within quantize tolerance).
                pids = (poly.a, poly.b, poly.c, poly.d)
                for pidx, u_pt in zip(pids, (ua, ub, uc, ud)):
                    key = (pidx, int(u_pt.x * quantize + 0.5), int(u_pt.y * quantize + 0.5))
                    if key in uv_vert_to_poly:
                        union(i, uv_vert_to_poly[key])
                    else:
                        uv_vert_to_poly[key] = i

                # UV centroid
                cu = (ua.x + ub.x + uc.x + ud.x) * 0.25
                cv = (ua.y + ub.y + uc.y + ud.y) * 0.25

                # UV area (signed shoelace, take abs)
                # Treat as quad: split into two tris
                def tri_area_2d(a, b, c):
                    return abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)) * 0.5
                uv_area = tri_area_2d(ua, ub, uc) + tri_area_2d(ua, uc, ud)

                pa, pb, pc, pd = src_pts[poly.a], src_pts[poly.b], src_pts[poly.c], src_pts[poly.d]
                # 3D centroid
                cx = (pa.x + pb.x + pc.x + pd.x) * 0.25
                cy = (pa.y + pb.y + pc.y + pd.y) * 0.25
                cz = (pa.z + pb.z + pc.z + pd.z) * 0.25
                # 3D area
                def tri_area_3d(a, b, c):
                    return ((b - a).Cross(c - a)).GetLength() * 0.5
                world_area = tri_area_3d(pa, pb, pc) + tri_area_3d(pa, pc, pd)

                poly_uv_corners.append((
                    i, cu, cv, uv_area, (cx, cy, cz), world_area,
                    (ua.x, ua.y), (ub.x, ub.y), (uc.x, uc.y), (ud.x, ud.y),
                ))

                # Update global UV bbox
                for u_pt in (ua, ub, uc, ud):
                    if u_pt.x < uv_min_global[0]: uv_min_global[0] = u_pt.x
                    if u_pt.y < uv_min_global[1]: uv_min_global[1] = u_pt.y
                    if u_pt.x > uv_max_global[0]: uv_max_global[0] = u_pt.x
                    if u_pt.y > uv_max_global[1]: uv_max_global[1] = u_pt.y

            # Group polys by island (UV-connected component). Bbox accumulated
            # over RAW CORNER UVs, not centroids — the previous version showed
            # degenerate (0.5, 0.5) bboxes for stacked-UV cubes because every
            # poly's centroid was the same point.
            islands_data = {}
            for entry in poly_uv_corners:
                pi, _cu, _cv, uv_area, _p3d, world_area = entry[:6]
                corners = entry[6:10]  # 4 (u, v) tuples
                root = find(pi)
                bucket = islands_data.setdefault(root, {
                    "polygon_count": 0,
                    "uv_min": [1e9, 1e9],
                    "uv_max": [-1e9, -1e9],
                    "uv_area": 0.0,
                    "world_area": 0.0,
                })
                bucket["polygon_count"] += 1
                for cu, cv in corners:
                    if cu < bucket["uv_min"][0]: bucket["uv_min"][0] = cu
                    if cv < bucket["uv_min"][1]: bucket["uv_min"][1] = cv
                    if cu > bucket["uv_max"][0]: bucket["uv_max"][0] = cu
                    if cv > bucket["uv_max"][1]: bucket["uv_max"][1] = cv
                bucket["uv_area"] += uv_area
                bucket["world_area"] += world_area

            islands = []
            for k, v in islands_data.items():
                distortion = (v["world_area"] / v["uv_area"]) ** 0.5 if v["uv_area"] > 1e-12 else 0.0
                islands.append({
                    "id": k,
                    "polygon_count": v["polygon_count"],
                    "uv_bbox": {
                        "u_min": v["uv_min"][0], "u_max": v["uv_max"][0],
                        "v_min": v["uv_min"][1], "v_max": v["uv_max"][1],
                    },
                    "uv_area": v["uv_area"],
                    "world_area": v["world_area"],
                    "distortion": distortion,
                })
            islands.sort(key=lambda x: -x["polygon_count"])

            # Overlap detection: bin polys into 32x32 UV grid, compare 3D-spread per cell
            GRID = 32
            bbox_diag = obj.GetRad().GetLength() * 2.0
            spread_threshold = bbox_diag * 0.15

            cells = [[[] for _ in range(GRID)] for _ in range(GRID)]
            for entry in poly_uv_corners:
                pi, cu, cv, _ua, p3d, _wa = entry[:6]
                gu = max(0, min(GRID-1, int(cu * GRID)))
                gv = max(0, min(GRID-1, int(cv * GRID)))
                cells[gv][gu].append(p3d)

            occupied = 0
            overlap = 0
            for row in cells:
                for cell in row:
                    if len(cell) < 2: continue
                    occupied += 1
                    xs = [p[0] for p in cell]
                    ys = [p[1] for p in cell]
                    zs = [p[2] for p in cell]
                    spread = max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs))
                    if spread > spread_threshold:
                        overlap += 1

            return {
                "status": "ok",
                "object": obj.GetName(),
                "polygon_count": n_polys,
                "uv_bbox_global": {
                    "u_min": uv_min_global[0], "u_max": uv_max_global[0],
                    "v_min": uv_min_global[1], "v_max": uv_max_global[1],
                },
                "island_count": len(islands),
                "islands": islands,
                "overlap_grid": {
                    "grid_res": GRID,
                    "occupied_cells": occupied,
                    "overlap_cells": overlap,
                    "overlap_fraction": overlap / occupied if occupied else 0.0,
                    "spread_threshold_3d": spread_threshold,
                    "warning": (
                        "UV islands overlap (mirrored or stacked shells)"
                        if overlap > 0 else None
                    ),
                },
            }
        except Exception as e:
            return {"error": f"uv_layout_stats failed: {e}", "traceback": traceback.format_exc()}

    def handle_sample_bitmap_at_uv(self, command):
        """Sample a bitmap at every vertex's UV coordinate, write to a vertex map.

        For each vertex, computes a representative UV (averaged from all
        polygons referencing that vertex), samples the bitmap at that UV,
        and writes a single grayscale weight (luminance) into a vertex map.

        Args (in command):
          target (str, optional)         — object name; defaults to selection
          bitmap_path (str)              — file path of the source bitmap
          vmap_name (str, default 'baked')
                                          — name of the output vertex map
          channel (str, default 'luminance')
                                          — 'red', 'green', 'blue', 'alpha',
                                            'luminance' (0.299R+0.587G+0.114B),
                                            or 'average' (R+G+B)/3
          invert (bool, default False)   — output 1.0 - sampled_value
          gamma (float, default 1.0)     — apply pow(value, gamma) before writing
          v_flip (bool, default True)    — flip V (image Y top-down vs UV bottom-up)

        Returns: vertex_count, vmap_name, sampled stats.
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            obj, err = self._resolve_target_object(command, doc, key="target")
            if err:
                return {"error": err}
            if obj.GetType() != c4d.Opolygon:
                return {"error": f"Target '{obj.GetName()}' is not a polygon mesh"}

            uv_tag = obj.GetTag(c4d.Tuvw)
            if uv_tag is None:
                return {"error": f"'{obj.GetName()}' has no UVW tag"}

            bitmap_path = command.get("bitmap_path")
            if not bitmap_path:
                return {"error": "bitmap_path is required"}
            if not os.path.isfile(bitmap_path):
                return {"error": f"Bitmap file not found: {bitmap_path}"}

            vmap_name = command.get("vmap_name", "baked")
            channel = (command.get("channel") or "luminance").lower()
            invert = bool(command.get("invert", False))
            gamma = float(command.get("gamma", 1.0))
            v_flip = bool(command.get("v_flip", True))

            # Load bitmap
            bmp = c4d.bitmaps.BaseBitmap()
            init_result = bmp.InitWith(bitmap_path)
            ok = init_result == c4d.IMAGERESULT_OK if not isinstance(init_result, tuple) else \
                 init_result[0] == c4d.IMAGERESULT_OK
            if not ok:
                return {"error": f"Failed to load bitmap: {bitmap_path}"}

            bw, bh = bmp.GetBw(), bmp.GetBh()

            def channel_value(rgb_tuple):
                r, g, b = rgb_tuple[0], rgb_tuple[1], rgb_tuple[2]
                if channel == "red":       v = r / 255.0
                elif channel == "green":   v = g / 255.0
                elif channel == "blue":    v = b / 255.0
                elif channel == "alpha":
                    a = rgb_tuple[3] if len(rgb_tuple) >= 4 else 255
                    v = a / 255.0
                elif channel == "average": v = (r + g + b) / (3.0 * 255.0)
                else:                       v = (0.299*r + 0.587*g + 0.114*b) / 255.0
                if invert: v = 1.0 - v
                if gamma != 1.0: v = pow(max(0.0, v), gamma)
                return v

            # Collect per-vertex UV by averaging across all polygons referencing it
            n_pts = obj.GetPointCount()
            vert_uv_sum = [(0.0, 0.0)] * n_pts
            vert_uv_count = [0] * n_pts
            n_polys = obj.GetPolygonCount()
            for i in range(n_polys):
                poly = obj.GetPolygon(i)
                uv = uv_tag.GetSlow(i)
                idxs = (poly.a, poly.b, poly.c, poly.d)
                pts = (uv["a"], uv["b"], uv["c"], uv["d"])
                for vi, uvpt in zip(idxs, pts):
                    su, sv = vert_uv_sum[vi]
                    vert_uv_sum[vi] = (su + uvpt.x, sv + uvpt.y)
                    vert_uv_count[vi] += 1

            # Sample bitmap at each vertex UV
            new_data = [0.0] * n_pts
            sampled = 0
            mn, mx, total = 1e9, -1e9, 0.0
            for vi in range(n_pts):
                cnt = vert_uv_count[vi]
                if cnt == 0:
                    continue
                u = vert_uv_sum[vi][0] / cnt
                v = vert_uv_sum[vi][1] / cnt
                if v_flip:
                    v = 1.0 - v
                # Clamp to [0,1] then convert to pixel coords
                u = max(0.0, min(1.0, u))
                v = max(0.0, min(1.0, v))
                px = min(bw - 1, int(u * bw))
                py = min(bh - 1, int(v * bh))
                rgb = bmp.GetPixel(px, py)
                if rgb is None:
                    continue
                w = channel_value(rgb)
                new_data[vi] = w
                sampled += 1
                if w < mn: mn = w
                if w > mx: mx = w
                total += w

            # Find or create vertex map tag with this name
            existing = self._find_vertex_map_tag(obj, vmap_name)
            if existing:
                vmap = existing
            else:
                vmap = c4d.modules.character.CAWeightTag if False else c4d.VariableTag(c4d.Tvertexmap, n_pts)
                vmap.SetName(vmap_name)
                obj.InsertTag(vmap)

            vmap.SetAllHighlevelData(new_data)
            c4d.EventAdd()

            return {
                "status": "ok",
                "object": obj.GetName(),
                "vmap_name": vmap_name,
                "vertex_count": n_pts,
                "vertices_sampled": sampled,
                "vertices_skipped_no_uv": n_pts - sampled,
                "min": mn if sampled else 0.0,
                "max": mx if sampled else 0.0,
                "mean": (total / sampled) if sampled else 0.0,
                "bitmap": {
                    "path": bitmap_path,
                    "width": bw,
                    "height": bh,
                    "channel": channel,
                    "v_flip_applied": v_flip,
                    "invert_applied": invert,
                    "gamma_applied": gamma,
                },
            }
        except Exception as e:
            return {"error": f"sample_bitmap_at_uv failed: {e}", "traceback": traceback.format_exc()}

    def handle_uv_islands_to_objects(self, command):
        """Split each UV island into its own polygon object (3D positions
        preserved — not flattened). Useful for per-island procedural workflows
        (different hole densities per panel, separate materials, etc).

        Args (in command):
          target (str, optional)         — object name; defaults to selection
          quantize (int, default 1000000) — UV-position dedup precision
          name_prefix (str, default same as source name)
          parent_name (str, optional)    — name of a Null to parent results under;
                                           created if not exists
          min_polygons (int, default 1)  — skip islands smaller than this

        Returns: list of created objects with island stats.
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            obj, err = self._resolve_target_object(command, doc, key="target")
            if err:
                return {"error": err}
            if obj.GetType() != c4d.Opolygon:
                return {"error": f"Target '{obj.GetName()}' is not a polygon mesh"}

            uv_tag = obj.GetTag(c4d.Tuvw)
            if uv_tag is None:
                return {"error": f"'{obj.GetName()}' has no UVW tag"}

            quantize = int(command.get("quantize", 1000000))
            name_prefix = command.get("name_prefix", obj.GetName())
            parent_name = command.get("parent_name")
            min_polys = int(command.get("min_polygons", 1))

            n_polys = obj.GetPolygonCount()
            src_pts = obj.GetAllPoints()

            # Union-find for UV-island detection (same as uv_layout_stats)
            parent = list(range(n_polys))
            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x
            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            # Key on (3D-point-idx, quantized-uv) — see uv_layout_stats for
            # the rationale. Stacked-UV polygons sharing only a UV position
            # but no 3D point are NOT in the same island.
            uv_vert_to_poly = {}
            for i in range(n_polys):
                poly = obj.GetPolygon(i)
                uv = uv_tag.GetSlow(i)
                pids = (poly.a, poly.b, poly.c, poly.d)
                corners = (uv["a"], uv["b"], uv["c"], uv["d"])
                for pidx, u_pt in zip(pids, corners):
                    key = (pidx, int(u_pt.x * quantize + 0.5), int(u_pt.y * quantize + 0.5))
                    if key in uv_vert_to_poly:
                        union(i, uv_vert_to_poly[key])
                    else:
                        uv_vert_to_poly[key] = i

            # Group poly indices by island root
            islands = {}
            for i in range(n_polys):
                islands.setdefault(find(i), []).append(i)

            # Optional parent null
            parent_null = None
            if parent_name:
                # Reuse existing if found at top level
                top = doc.GetFirstObject()
                while top:
                    if top.GetName() == parent_name and top.GetType() == c4d.Onull:
                        parent_null = top
                        break
                    top = top.GetNext()
                if parent_null is None:
                    parent_null = c4d.BaseObject(c4d.Onull)
                    parent_null.SetName(parent_name)
                    doc.InsertObject(parent_null)

            created = []
            # Sort islands by size desc for deterministic naming
            sorted_islands = sorted(islands.items(), key=lambda kv: -len(kv[1]))
            for island_idx, (root, poly_indices) in enumerate(sorted_islands):
                if len(poly_indices) < min_polys:
                    continue

                # Build vertex remap: source vert idx → new vert idx
                vert_remap = {}
                new_pts = []
                new_polys = []

                for pi in poly_indices:
                    p = obj.GetPolygon(pi)
                    is_quad = (p.c != p.d)
                    src_idxs = (p.a, p.b, p.c, p.d) if is_quad else (p.a, p.b, p.c)
                    new_idxs = []
                    for vi in src_idxs:
                        ni = vert_remap.get(vi)
                        if ni is None:
                            ni = len(new_pts)
                            new_pts.append(src_pts[vi])
                            vert_remap[vi] = ni
                        new_idxs.append(ni)
                    if is_quad:
                        new_polys.append(c4d.CPolygon(new_idxs[0], new_idxs[1], new_idxs[2], new_idxs[3]))
                    else:
                        new_polys.append(c4d.CPolygon(new_idxs[0], new_idxs[1], new_idxs[2], new_idxs[2]))

                out = c4d.PolygonObject(len(new_pts), len(new_polys))
                out.SetAllPoints(new_pts)
                for j, polyobj in enumerate(new_polys):
                    out.SetPolygon(j, polyobj)
                out.Message(c4d.MSG_UPDATE)
                out.SetName(f"{name_prefix}_island_{island_idx:02d}")
                out.MakeTag(c4d.Tphong)

                # Copy world transform from source
                out.SetMg(obj.GetMg())

                # Carry UVs by mapping back via the same poly_indices order
                try:
                    new_uv = c4d.UVWTag(len(new_polys))
                    out.InsertTag(new_uv)
                    for new_i, src_i in enumerate(poly_indices):
                        uv = uv_tag.GetSlow(src_i)
                        new_uv.SetSlow(new_i, uv["a"], uv["b"], uv["c"], uv["d"])
                except Exception:
                    pass

                # Carry vertex maps (remap weights via vert_remap)
                try:
                    src_vmaps = []
                    t = obj.GetFirstTag()
                    while t:
                        if t.GetType() == c4d.Tvertexmap:
                            src_vmaps.append(t)
                        t = t.GetNext()
                    for src_vm in src_vmaps:
                        src_w = src_vm.GetAllHighlevelData()
                        if src_w is None:
                            continue
                        # Build new-vert-index → source-vert-index reverse map
                        inv_remap = [0] * len(new_pts)
                        for src_vi, new_vi in vert_remap.items():
                            inv_remap[new_vi] = src_vi
                        new_w = [src_w[inv_remap[i]] for i in range(len(new_pts))]
                        new_vm = c4d.VariableTag(c4d.Tvertexmap, len(new_pts))
                        new_vm.SetName(src_vm.GetName())
                        out.InsertTag(new_vm)
                        new_vm.SetAllHighlevelData(new_w)
                except Exception:
                    pass

                if parent_null:
                    out.InsertUnder(parent_null)
                else:
                    doc.InsertObject(out)

                created.append({
                    "name": out.GetName(),
                    "polygon_count": len(new_polys),
                    "vertex_count": len(new_pts),
                    "island_id": root,
                })

            c4d.EventAdd()
            return {
                "status": "ok",
                "source": obj.GetName(),
                "island_count_total": len(islands),
                "objects_created": len(created),
                "skipped_below_min_polygons": len(islands) - len(created),
                "objects": created,
                "parent_null": parent_null.GetName() if parent_null else None,
            }
        except Exception as e:
            return {"error": f"uv_islands_to_objects failed: {e}", "traceback": traceback.format_exc()}

    # === Cross-mesh attribute transfer (Houdini/Blender "Sample UV") ========

    def _build_uv_tri_grid(self, mesh, grid_res=64):
        """Build a UV-space spatial grid of triangles for fast UV → tri lookup.

        Returns (tris, grid) where:
          tris  = list of dicts with uv0/uv1/uv2 + the source mesh's vertex
                  indices a/b/c that the tri came from (so callers can sample
                  any per-vertex attribute, not just UVs)
          grid  = grid_res × grid_res list-of-lists of triangle indices
        """
        uv_tag = mesh.GetTag(c4d.Tuvw)
        if uv_tag is None:
            return None, None
        n_polys = mesh.GetPolygonCount()
        tris = []
        for i in range(n_polys):
            poly = mesh.GetPolygon(i)
            uv = uv_tag.GetSlow(i)
            ua, ub, uc, ud = uv["a"], uv["b"], uv["c"], uv["d"]
            is_quad = (poly.c != poly.d)

            # Triangle 1: a-b-c
            tris.append({
                "uv0": ua, "uv1": ub, "uv2": uc,
                "vi0": poly.a, "vi1": poly.b, "vi2": poly.c,
                "u_min": min(ua.x, ub.x, uc.x),
                "u_max": max(ua.x, ub.x, uc.x),
                "v_min": min(ua.y, ub.y, uc.y),
                "v_max": max(ua.y, ub.y, uc.y),
            })
            if is_quad:
                tris.append({
                    "uv0": ua, "uv1": uc, "uv2": ud,
                    "vi0": poly.a, "vi1": poly.c, "vi2": poly.d,
                    "u_min": min(ua.x, uc.x, ud.x),
                    "u_max": max(ua.x, uc.x, ud.x),
                    "v_min": min(ua.y, uc.y, ud.y),
                    "v_max": max(ua.y, uc.y, ud.y),
                })
        grid = [[[] for _ in range(grid_res)] for _ in range(grid_res)]
        for ti, t in enumerate(tris):
            u_lo = max(0, int(t["u_min"] * grid_res))
            u_hi = min(grid_res - 1, int(t["u_max"] * grid_res))
            v_lo = max(0, int(t["v_min"] * grid_res))
            v_hi = min(grid_res - 1, int(t["v_max"] * grid_res))
            for vy in range(v_lo, v_hi + 1):
                for ux in range(u_lo, u_hi + 1):
                    grid[vy][ux].append(ti)
        return tris, grid

    @staticmethod
    def _bary_2d(u, v, uv0, uv1, uv2):
        """Compute barycentric weights of (u,v) within UV triangle.
        Returns (w0,w1,w2) or None if degenerate."""
        den = ((uv1.y - uv2.y) * (uv0.x - uv2.x) +
               (uv2.x - uv1.x) * (uv0.y - uv2.y))
        if abs(den) < 1e-12:
            return None
        w0 = ((uv1.y - uv2.y) * (u - uv2.x) +
              (uv2.x - uv1.x) * (v - uv2.y)) / den
        w1 = ((uv2.y - uv0.y) * (u - uv2.x) +
              (uv0.x - uv2.x) * (v - uv2.y)) / den
        return (w0, w1, 1.0 - w0 - w1)

    def _find_uv_tri_at(self, u, v, tris, grid, grid_res=64):
        """Spatial-grid lookup of the source triangle containing UV (u,v).
        Returns (tri, bary) or (None, None)."""
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return None, None
        cu = max(0, min(grid_res - 1, int(u * grid_res)))
        cv = max(0, min(grid_res - 1, int(v * grid_res)))
        for radius in (0, 1, 2):
            for dv in range(-radius, radius + 1):
                for du in range(-radius, radius + 1):
                    if radius > 0 and abs(du) != radius and abs(dv) != radius:
                        continue
                    ccu, ccv = cu + du, cv + dv
                    if not (0 <= ccu < grid_res and 0 <= ccv < grid_res):
                        continue
                    for ti in grid[ccv][ccu]:
                        t = tris[ti]
                        if u < t["u_min"] or u > t["u_max"] or v < t["v_min"] or v > t["v_max"]:
                            continue
                        bary = self._bary_2d(u, v, t["uv0"], t["uv1"], t["uv2"])
                        if bary is None:
                            continue
                        w0, w1, w2 = bary
                        eps = 1e-5
                        if w0 >= -eps and w1 >= -eps and w2 >= -eps:
                            return t, bary
        return None, None

    def handle_sample_vmap_via_uv(self, command):
        """Transfer a vertex map from a source mesh to a dest mesh via shared
        UV space — the Blender / Houdini "Sample UV" pattern.

        For each vertex in the destination mesh:
          1. Compute the vertex's representative UV (averaged over all polys
             referencing it on dest).
          2. Find the source triangle containing that UV (UV-space spatial
             grid lookup, O(1) avg).
          3. Compute barycentric weights of the UV within that source tri.
          4. Sample the source vmap weights at that tri's 3 vertices, blend
             via barycentric → write to dest vmap.

        Args (in command):
          source (str)              — source object name (must have UV + vmap)
          dest (str)                — destination object name (must have UV)
          src_vmap_name (str, opt)  — source vmap; defaults to first
          dest_vmap_name (str, opt) — dest vmap name; defaults to src name
          v_flip (bool, default False)
                                    — flip V before lookup (for hole-map cases
                                       where source UV uses inverted V axis)
          fallback_value (float, 0.0)
                                    — value written for dest verts whose UV
                                       falls outside any source island

        Returns: vertex counts (sampled, fallback), bbox of weights.
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            src_name = command.get("source")
            dst_name = command.get("dest")
            if not src_name or not dst_name:
                return {"error": "source and dest object names are required"}

            src = self.find_object_by_name(doc, src_name)
            dst = self.find_object_by_name(doc, dst_name)
            if src is None: return {"error": f"Source '{src_name}' not found"}
            if dst is None: return {"error": f"Destination '{dst_name}' not found"}
            if src.GetType() != c4d.Opolygon:
                return {"error": f"Source '{src_name}' is not a polygon mesh"}
            if dst.GetType() != c4d.Opolygon:
                return {"error": f"Destination '{dst_name}' is not a polygon mesh"}

            src_uv = src.GetTag(c4d.Tuvw)
            dst_uv = dst.GetTag(c4d.Tuvw)
            if src_uv is None: return {"error": f"Source '{src_name}' has no UV tag"}
            if dst_uv is None: return {"error": f"Destination '{dst_name}' has no UV tag"}

            src_vmap_name = command.get("src_vmap_name")
            src_vmap = self._find_vertex_map_tag(src, src_vmap_name)
            if src_vmap is None:
                return {"error": f"No vertex map tag found on source '{src_name}'"}

            src_weights = src_vmap.GetAllHighlevelData()
            if src_weights is None:
                return {"error": "Source vertex map has no data"}
            src_weights = list(src_weights)

            dest_vmap_name = command.get("dest_vmap_name") or src_vmap.GetName()
            v_flip = bool(command.get("v_flip", False))
            fallback = float(command.get("fallback_value", 0.0))

            # Build UV-space spatial grid on source
            tris, grid = self._build_uv_tri_grid(src, grid_res=64)
            if tris is None:
                return {"error": "Failed to build source UV triangle cache"}

            # Compute per-vertex UV on dest (avg over all polys using each vert)
            dst_n_pts = dst.GetPointCount()
            uv_sum = [(0.0, 0.0)] * dst_n_pts
            uv_cnt = [0] * dst_n_pts
            dst_n_polys = dst.GetPolygonCount()
            for i in range(dst_n_polys):
                p = dst.GetPolygon(i)
                uv = dst_uv.GetSlow(i)
                for vi, uvpt in zip((p.a, p.b, p.c, p.d),
                                     (uv["a"], uv["b"], uv["c"], uv["d"])):
                    su, sv = uv_sum[vi]
                    uv_sum[vi] = (su + uvpt.x, sv + uvpt.y)
                    uv_cnt[vi] += 1

            # Sample
            new_w = [fallback] * dst_n_pts
            sampled = 0
            outside = 0
            for vi in range(dst_n_pts):
                cnt = uv_cnt[vi]
                if cnt == 0:
                    outside += 1
                    continue
                u = uv_sum[vi][0] / cnt
                v = uv_sum[vi][1] / cnt
                if v_flip: v = 1.0 - v
                tri, bary = self._find_uv_tri_at(u, v, tris, grid, grid_res=64)
                if tri is None:
                    outside += 1
                    continue
                w0, w1, w2 = bary
                weight = (src_weights[tri["vi0"]] * w0 +
                          src_weights[tri["vi1"]] * w1 +
                          src_weights[tri["vi2"]] * w2)
                new_w[vi] = weight
                sampled += 1

            # Write to dest vmap
            existing = self._find_vertex_map_tag(dst, dest_vmap_name)
            if existing:
                vmap = existing
            else:
                vmap = c4d.VariableTag(c4d.Tvertexmap, dst_n_pts)
                vmap.SetName(dest_vmap_name)
                dst.InsertTag(vmap)

            vmap.SetAllHighlevelData(new_w)
            c4d.EventAdd()

            return {
                "status": "ok",
                "source": src_name,
                "dest": dst_name,
                "src_vmap_name": src_vmap.GetName(),
                "dest_vmap_name": dest_vmap_name,
                "dest_vertex_count": dst_n_pts,
                "vertices_sampled": sampled,
                "vertices_fallback": outside,
                "weight_min": min(new_w) if new_w else 0.0,
                "weight_max": max(new_w) if new_w else 0.0,
                "weight_mean": (sum(new_w) / len(new_w)) if new_w else 0.0,
            }
        except Exception as e:
            return {"error": f"sample_vmap_via_uv failed: {e}", "traceback": traceback.format_exc()}

    # === UV transfer via closest-point-on-mesh =============================

    @staticmethod
    def _closest_point_on_triangle(p, a, b, c):
        """Eberly's closest-point-on-triangle.
        Returns (closest_point: c4d.Vector, bary: (w_a, w_b, w_c))."""
        ab = b - a
        ac = c - a
        ap = p - a
        d1 = ab.Dot(ap)
        d2 = ac.Dot(ap)
        if d1 <= 0.0 and d2 <= 0.0:
            return a, (1.0, 0.0, 0.0)

        bp = p - b
        d3 = ab.Dot(bp)
        d4 = ac.Dot(bp)
        if d3 >= 0.0 and d4 <= d3:
            return b, (0.0, 1.0, 0.0)

        vc = d1 * d4 - d3 * d2
        if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
            denom = (d1 - d3)
            v = d1 / denom if abs(denom) > 1e-12 else 0.0
            return a + ab * v, (1.0 - v, v, 0.0)

        cp = p - c
        d5 = ab.Dot(cp)
        d6 = ac.Dot(cp)
        if d6 >= 0.0 and d5 <= d6:
            return c, (0.0, 0.0, 1.0)

        vb = d5 * d2 - d1 * d6
        if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
            denom = (d2 - d6)
            w = d2 / denom if abs(denom) > 1e-12 else 0.0
            return a + ac * w, (1.0 - w, 0.0, w)

        va = d3 * d6 - d5 * d4
        if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
            denom = (d4 - d3) + (d5 - d6)
            w = (d4 - d3) / denom if abs(denom) > 1e-12 else 0.0
            return b + (c - b) * w, (0.0, 1.0 - w, w)

        denom_sum = va + vb + vc
        if abs(denom_sum) < 1e-12:
            return a, (1.0, 0.0, 0.0)
        denom_inv = 1.0 / denom_sum
        v = vb * denom_inv
        w = vc * denom_inv
        return a + ab * v + ac * w, (1.0 - v - w, v, w)

    def _build_3d_tri_grid(self, mesh, grid_res=48):
        """Build a 3D spatial grid of source-mesh triangles for closest-point
        queries. Returns (tris, grid, bbox_min, bbox_max, cell_size)."""
        n_polys = mesh.GetPolygonCount()
        pts = mesh.GetAllPoints()
        mg = mesh.GetMg()

        # World-space tri data + UVs (used for downstream UV sampling)
        uv_tag = mesh.GetTag(c4d.Tuvw)
        tris = []
        for i in range(n_polys):
            poly = mesh.GetPolygon(i)
            pa = mg * pts[poly.a]
            pb = mg * pts[poly.b]
            pc = mg * pts[poly.c]
            pd = mg * pts[poly.d]
            uv = uv_tag.GetSlow(i) if uv_tag else None
            is_quad = (poly.c != poly.d)

            # Triangle 1: a-b-c
            tri = {
                "p0": pa, "p1": pb, "p2": pc,
                "uv0": uv["a"] if uv else None,
                "uv1": uv["b"] if uv else None,
                "uv2": uv["c"] if uv else None,
                "vi0": poly.a, "vi1": poly.b, "vi2": poly.c,
            }
            tri["bbox_min"] = c4d.Vector(min(pa.x, pb.x, pc.x), min(pa.y, pb.y, pc.y), min(pa.z, pb.z, pc.z))
            tri["bbox_max"] = c4d.Vector(max(pa.x, pb.x, pc.x), max(pa.y, pb.y, pc.y), max(pa.z, pb.z, pc.z))
            tris.append(tri)
            if is_quad:
                tri2 = {
                    "p0": pa, "p1": pc, "p2": pd,
                    "uv0": uv["a"] if uv else None,
                    "uv1": uv["c"] if uv else None,
                    "uv2": uv["d"] if uv else None,
                    "vi0": poly.a, "vi1": poly.c, "vi2": poly.d,
                }
                tri2["bbox_min"] = c4d.Vector(min(pa.x, pc.x, pd.x), min(pa.y, pc.y, pd.y), min(pa.z, pc.z, pd.z))
                tri2["bbox_max"] = c4d.Vector(max(pa.x, pc.x, pd.x), max(pa.y, pc.y, pd.y), max(pa.z, pc.z, pd.z))
                tris.append(tri2)

        if not tris:
            return None, None, None, None, None

        bbmin = c4d.Vector(min(t["bbox_min"].x for t in tris), min(t["bbox_min"].y for t in tris), min(t["bbox_min"].z for t in tris))
        bbmax = c4d.Vector(max(t["bbox_max"].x for t in tris), max(t["bbox_max"].y for t in tris), max(t["bbox_max"].z for t in tris))
        # Pad slightly so boundary points fall inside
        pad = (bbmax - bbmin).GetLength() * 0.001 + 0.001
        bbmin = bbmin - c4d.Vector(pad, pad, pad)
        bbmax = bbmax + c4d.Vector(pad, pad, pad)
        size = bbmax - bbmin
        cell_x = max(size.x / grid_res, 1e-6)
        cell_y = max(size.y / grid_res, 1e-6)
        cell_z = max(size.z / grid_res, 1e-6)

        grid = {}
        for ti, t in enumerate(tris):
            x_lo = max(0, int((t["bbox_min"].x - bbmin.x) / cell_x))
            x_hi = min(grid_res - 1, int((t["bbox_max"].x - bbmin.x) / cell_x))
            y_lo = max(0, int((t["bbox_min"].y - bbmin.y) / cell_y))
            y_hi = min(grid_res - 1, int((t["bbox_max"].y - bbmin.y) / cell_y))
            z_lo = max(0, int((t["bbox_min"].z - bbmin.z) / cell_z))
            z_hi = min(grid_res - 1, int((t["bbox_max"].z - bbmin.z) / cell_z))
            for x in range(x_lo, x_hi + 1):
                for y in range(y_lo, y_hi + 1):
                    for z in range(z_lo, z_hi + 1):
                        grid.setdefault((x, y, z), []).append(ti)
        return tris, grid, bbmin, bbmax, (cell_x, cell_y, cell_z)

    def _closest_tri_to_point(self, world_pt, tris, grid, bbmin, cell_size, grid_res=48, max_search_radius=4):
        """Find the source triangle closest to world_pt. Returns (tri, bary, dist)."""
        cx = int((world_pt.x - bbmin.x) / cell_size[0])
        cy = int((world_pt.y - bbmin.y) / cell_size[1])
        cz = int((world_pt.z - bbmin.z) / cell_size[2])
        cx = max(0, min(grid_res - 1, cx))
        cy = max(0, min(grid_res - 1, cy))
        cz = max(0, min(grid_res - 1, cz))

        best_dist = 1e18
        best_tri = None
        best_bary = None
        seen = set()

        for r in range(0, max_search_radius + 1):
            if best_tri is not None and r > 1:
                break  # already found something close in inner cells
            for dz in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        if r > 0 and abs(dx) != r and abs(dy) != r and abs(dz) != r:
                            continue
                        key = (cx + dx, cy + dy, cz + dz)
                        if key in seen: continue
                        seen.add(key)
                        if not (0 <= key[0] < grid_res and 0 <= key[1] < grid_res and 0 <= key[2] < grid_res):
                            continue
                        cell = grid.get(key)
                        if not cell: continue
                        for ti in cell:
                            t = tris[ti]
                            cp, bary = self._closest_point_on_triangle(world_pt, t["p0"], t["p1"], t["p2"])
                            d = (cp - world_pt).GetLengthSquared()
                            if d < best_dist:
                                best_dist = d
                                best_tri = t
                                best_bary = bary
        return best_tri, best_bary, (best_dist ** 0.5 if best_tri else None)

    def handle_uv_transfer(self, command):
        """Project UVs from a source mesh onto a destination mesh by
        closest-point-on-source for each dest vertex.

        For each dest vertex (in world space), finds the closest point on the
        source mesh, computes barycentric coords there, and samples the source
        UVs at that location. Writes UVs to dest at all polygon corners
        referencing each vertex.

        Args (in command):
          source (str)               — source object name (must have UV)
          dest (str)                  — destination object name
          create_uv_tag (bool, True)  — if True and dest has no UV tag, create
                                        one; if False and dest lacks UV, error
          grid_res (int, default 48)  — spatial grid resolution (3D)
          max_distance (float, opt)   — flag verts whose closest source point is
                                        farther than this; UV not written for them

        Returns: vertex counts (sampled, fallback), distance stats.

        Notes:
          - This is closest-point projection in 3D; it works best when source
            and dest meshes are roughly aligned spatially. For "transfer UVs
            from low-poly to sculpt of same character" — works great.
          - For totally different meshes or wildly misaligned ones — won't help.
          - Result has the same UVs at every polygon corner referencing a
            given dest vertex (no UV seams). If dest needs explicit seams,
            those have to be authored separately.
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            src_name = command.get("source")
            dst_name = command.get("dest")
            if not src_name or not dst_name:
                return {"error": "source and dest required"}

            src = self.find_object_by_name(doc, src_name)
            dst = self.find_object_by_name(doc, dst_name)
            if src is None: return {"error": f"Source '{src_name}' not found"}
            if dst is None: return {"error": f"Destination '{dst_name}' not found"}
            if src.GetType() != c4d.Opolygon: return {"error": "Source must be polygon mesh"}
            if dst.GetType() != c4d.Opolygon: return {"error": "Destination must be polygon mesh"}

            if src.GetTag(c4d.Tuvw) is None:
                return {"error": f"Source '{src_name}' has no UV tag"}

            grid_res = int(command.get("grid_res", 48))
            create_uv_tag = bool(command.get("create_uv_tag", True))
            max_distance = command.get("max_distance")  # None = no limit

            # Build 3D grid on source
            tris, grid, bbmin, bbmax, cell_size = self._build_3d_tri_grid(src, grid_res=grid_res)
            if tris is None:
                return {"error": "Failed to build source triangle cache (empty mesh?)"}

            # For each dest vertex (world space), find closest source UV
            dst_pts = dst.GetAllPoints()
            dst_mg = dst.GetMg()
            n_pts = dst.GetPointCount()
            n_polys = dst.GetPolygonCount()

            dest_vert_uv = [None] * n_pts
            dest_vert_dist = [None] * n_pts
            sampled = 0
            outside = 0
            max_d = 0.0
            sum_d = 0.0
            for vi in range(n_pts):
                world_p = dst_mg * dst_pts[vi]
                tri, bary, dist = self._closest_tri_to_point(world_p, tris, grid, bbmin, cell_size, grid_res=grid_res)
                if tri is None or tri["uv0"] is None:
                    outside += 1
                    continue
                if max_distance is not None and dist > max_distance:
                    outside += 1
                    continue
                w0, w1, w2 = bary
                # Sample UV via barycentric
                u = tri["uv0"].x * w0 + tri["uv1"].x * w1 + tri["uv2"].x * w2
                v = tri["uv0"].y * w0 + tri["uv1"].y * w1 + tri["uv2"].y * w2
                dest_vert_uv[vi] = c4d.Vector(u, v, 0.0)
                dest_vert_dist[vi] = dist
                sampled += 1
                if dist > max_d: max_d = dist
                sum_d += dist

            # Build/replace UV tag on dest
            existing_uv = dst.GetTag(c4d.Tuvw)
            if existing_uv is None:
                if not create_uv_tag:
                    return {"error": "Destination has no UV tag and create_uv_tag=False"}
                new_uv = c4d.UVWTag(n_polys)
                dst.InsertTag(new_uv)
            else:
                new_uv = existing_uv

            for i in range(n_polys):
                p = dst.GetPolygon(i)
                idxs = (p.a, p.b, p.c, p.d)
                # Build UV per corner — skip if dest_vert_uv is None for any corner
                vecs = []
                for vi in idxs:
                    uv_v = dest_vert_uv[vi]
                    vecs.append(uv_v if uv_v is not None else c4d.Vector(0, 0, 0))
                new_uv.SetSlow(i, vecs[0], vecs[1], vecs[2], vecs[3])

            c4d.EventAdd()

            return {
                "status": "ok",
                "source": src_name,
                "dest": dst_name,
                "dest_vertex_count": n_pts,
                "vertices_sampled": sampled,
                "vertices_fallback": outside,
                "max_distance_seen": max_d,
                "mean_distance": (sum_d / sampled) if sampled else 0.0,
                "max_distance_param": max_distance,
                "grid_res": grid_res,
                "source_tri_count": len(tris),
            }
        except Exception as e:
            return {"error": f"uv_transfer failed: {e}", "traceback": traceback.format_exc()}

    # === UV from procedural projection =====================================

    def handle_uv_from_projection(self, command):
        """Generate UVs for a polygon mesh via standard projection types.

        Args (in command):
          target (str, optional)       — object name; defaults to selection
          projection (str)             — required:
            'box'        : cubic projection (each face → nearest world-axis plane)
            'sphere'     : spherical (longitude/latitude)
            'cylinder'   : cylindrical
            'planar_xy'  : drop Z (top/bottom view)
            'planar_xz'  : drop Y (front/back view)
            'planar_yz'  : drop X (left/right view)
          space (str, default 'local')  — 'local' or 'world' (matrix-applied)
          tile_u, tile_v (float, def 1.0)
                                       — repeat factor; 1.0 = single span
          offset_u, offset_v (float, default 0.0)
                                       — UV offset
          up_axis (str, default 'y')   — for sphere/cylinder, which axis is up

        Returns: target name, projection type, generated UV bbox, per-poly count.
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            obj, err = self._resolve_target_object(command, doc, key="target")
            if err:
                return {"error": err}
            if obj.GetType() != c4d.Opolygon:
                return {"error": f"Target '{obj.GetName()}' is not a polygon mesh"}

            projection = (command.get("projection") or "").lower()
            valid_proj = ["box", "sphere", "cylinder", "planar_xy", "planar_xz", "planar_yz"]
            if projection not in valid_proj:
                return {"error": f"Unknown projection '{projection}'", "valid_projections": valid_proj}

            space = (command.get("space") or "local").lower()
            tile_u = float(command.get("tile_u", 1.0))
            tile_v = float(command.get("tile_v", 1.0))
            offset_u = float(command.get("offset_u", 0.0))
            offset_v = float(command.get("offset_v", 0.0))
            up_axis = (command.get("up_axis") or "y").lower()

            n_pts = obj.GetPointCount()
            n_polys = obj.GetPolygonCount()
            pts = obj.GetAllPoints()
            mg = obj.GetMg()

            # Get vertices in chosen space
            def vertex_in_space(vi):
                p = pts[vi]
                return mg * p if space == "world" else p

            # Compute bbox for normalization
            bbmin = [1e18, 1e18, 1e18]
            bbmax = [-1e18, -1e18, -1e18]
            for vi in range(n_pts):
                p = vertex_in_space(vi)
                if p.x < bbmin[0]: bbmin[0] = p.x
                if p.y < bbmin[1]: bbmin[1] = p.y
                if p.z < bbmin[2]: bbmin[2] = p.z
                if p.x > bbmax[0]: bbmax[0] = p.x
                if p.y > bbmax[1]: bbmax[1] = p.y
                if p.z > bbmax[2]: bbmax[2] = p.z

            cx = (bbmin[0] + bbmax[0]) * 0.5
            cy = (bbmin[1] + bbmax[1]) * 0.5
            cz = (bbmin[2] + bbmax[2]) * 0.5
            sx = max(bbmax[0] - bbmin[0], 1e-12)
            sy = max(bbmax[1] - bbmin[1], 1e-12)
            sz = max(bbmax[2] - bbmin[2], 1e-12)

            import math

            def planar_uv(p, axes):
                # axes in ('x','y','z') for u and v; the dropped axis is the projection direction
                ax_u, ax_v = axes
                vu = getattr(p, ax_u)
                vv = getattr(p, ax_v)
                # Normalize via bbox
                ranges = {"x": (bbmin[0], sx), "y": (bbmin[1], sy), "z": (bbmin[2], sz)}
                u_lo, u_sz = ranges[ax_u]
                v_lo, v_sz = ranges[ax_v]
                u = (vu - u_lo) / u_sz
                v = (vv - v_lo) / v_sz
                return u, v

            def spherical_uv(p):
                # u from longitude (azimuth around up axis), v from latitude
                if up_axis == "y":
                    dx, dy, dz = p.x - cx, p.y - cy, p.z - cz
                    r = max(math.sqrt(dx*dx + dy*dy + dz*dz), 1e-12)
                    u = math.atan2(dz, dx) / (2 * math.pi) + 0.5
                    v = math.asin(max(-1.0, min(1.0, dy / r))) / math.pi + 0.5
                elif up_axis == "z":
                    dx, dy, dz = p.x - cx, p.y - cy, p.z - cz
                    r = max(math.sqrt(dx*dx + dy*dy + dz*dz), 1e-12)
                    u = math.atan2(dy, dx) / (2 * math.pi) + 0.5
                    v = math.asin(max(-1.0, min(1.0, dz / r))) / math.pi + 0.5
                else:  # x up
                    dx, dy, dz = p.x - cx, p.y - cy, p.z - cz
                    r = max(math.sqrt(dx*dx + dy*dy + dz*dz), 1e-12)
                    u = math.atan2(dy, dz) / (2 * math.pi) + 0.5
                    v = math.asin(max(-1.0, min(1.0, dx / r))) / math.pi + 0.5
                return u, v

            def cylindrical_uv(p):
                if up_axis == "y":
                    dx, dz = p.x - cx, p.z - cz
                    u = math.atan2(dz, dx) / (2 * math.pi) + 0.5
                    v = (p.y - bbmin[1]) / sy
                elif up_axis == "z":
                    dx, dy = p.x - cx, p.y - cy
                    u = math.atan2(dy, dx) / (2 * math.pi) + 0.5
                    v = (p.z - bbmin[2]) / sz
                else:  # x up
                    dy, dz = p.y - cy, p.z - cz
                    u = math.atan2(dy, dz) / (2 * math.pi) + 0.5
                    v = (p.x - bbmin[0]) / sx
                return u, v

            def box_uv(p, normal_world):
                # Pick projection plane by which axis the normal points along strongest
                ax = max("x", "y", "z", key=lambda a: abs(getattr(normal_world, a)))
                if ax == "x":
                    # Project to YZ: use Y as U, Z as V (with sign for handedness)
                    u = (p.y - bbmin[1]) / sy
                    v = (p.z - bbmin[2]) / sz
                elif ax == "y":
                    u = (p.x - bbmin[0]) / sx
                    v = (p.z - bbmin[2]) / sz
                else:  # z
                    u = (p.x - bbmin[0]) / sx
                    v = (p.y - bbmin[1]) / sy
                return u, v

            # Get/create UV tag
            uv_tag = obj.GetTag(c4d.Tuvw)
            if uv_tag is None:
                uv_tag = c4d.UVWTag(n_polys)
                obj.InsertTag(uv_tag)

            # For box projection, need per-poly normal (world space if space='world')
            poly_normals = None
            if projection == "box":
                poly_normals = []
                for i in range(n_polys):
                    poly = obj.GetPolygon(i)
                    pa = vertex_in_space(poly.a)
                    pb = vertex_in_space(poly.b)
                    pc = vertex_in_space(poly.c)
                    n = (pb - pa).Cross(pc - pa)
                    if n.GetLength() > 1e-12:
                        n = n.GetNormalized()
                    else:
                        n = c4d.Vector(0, 1, 0)
                    poly_normals.append(n)

            # Per-poly: compute UVs at its 4 corners
            u_min, u_max, v_min, v_max = 1e18, -1e18, 1e18, -1e18
            for i in range(n_polys):
                poly = obj.GetPolygon(i)
                idxs = (poly.a, poly.b, poly.c, poly.d)

                if projection == "box":
                    n = poly_normals[i]
                    uvs_per_corner = []
                    for vi in idxs:
                        p = vertex_in_space(vi)
                        u, v = box_uv(p, n)
                        u = u * tile_u + offset_u
                        v = v * tile_v + offset_v
                        uvs_per_corner.append(c4d.Vector(u, v, 0))
                elif projection == "sphere":
                    uvs_per_corner = []
                    for vi in idxs:
                        u, v = spherical_uv(vertex_in_space(vi))
                        uvs_per_corner.append(c4d.Vector(u * tile_u + offset_u,
                                                          v * tile_v + offset_v, 0))
                elif projection == "cylinder":
                    uvs_per_corner = []
                    for vi in idxs:
                        u, v = cylindrical_uv(vertex_in_space(vi))
                        uvs_per_corner.append(c4d.Vector(u * tile_u + offset_u,
                                                          v * tile_v + offset_v, 0))
                else:
                    # planar_xy / planar_xz / planar_yz
                    axes_map = {"planar_xy": ("x", "y"), "planar_xz": ("x", "z"), "planar_yz": ("y", "z")}
                    axes = axes_map[projection]
                    uvs_per_corner = []
                    for vi in idxs:
                        u, v = planar_uv(vertex_in_space(vi), axes)
                        uvs_per_corner.append(c4d.Vector(u * tile_u + offset_u,
                                                          v * tile_v + offset_v, 0))

                # Track UV range
                for u_v in uvs_per_corner:
                    if u_v.x < u_min: u_min = u_v.x
                    if u_v.x > u_max: u_max = u_v.x
                    if u_v.y < v_min: v_min = u_v.y
                    if u_v.y > v_max: v_max = u_v.y

                uv_tag.SetSlow(i, uvs_per_corner[0], uvs_per_corner[1], uvs_per_corner[2], uvs_per_corner[3])

            c4d.EventAdd()

            return {
                "status": "ok",
                "object": obj.GetName(),
                "projection": projection,
                "space": space,
                "polygon_count": n_polys,
                "uv_bbox": {"u_min": u_min, "u_max": u_max, "v_min": v_min, "v_max": v_max},
                "tile": {"u": tile_u, "v": tile_v},
                "offset": {"u": offset_u, "v": offset_v},
                "up_axis": up_axis,
            }
        except Exception as e:
            return {"error": f"uv_from_projection failed: {e}", "traceback": traceback.format_exc()}

    def handle_list_render_engines(self, command):
        """List all registered render engines (VideoPost plugins of renderer kind, plus c4d.PLUGINTYPE_VIDEOPOST entries).

        Also flags which is currently active. Useful for verifying that a custom
        viewport renderer plugin (custom GLSL shader, third-party engine, etc.) registered correctly.
        """
        try:
            engines = []
            seen = set()
            try:
                vps = c4d.plugins.FilterPluginList(c4d.PLUGINTYPE_VIDEOPOST, True) or []
                for p in vps:
                    pid = p.GetID()
                    if pid in seen:
                        continue
                    seen.add(pid)
                    engines.append({
                        "id": pid,
                        "name": p.GetName(),
                        "type": "VideoPost",
                    })
            except Exception as e:
                self.log(f"[LIST_RENDER_ENGINES] VideoPost enum failed: {e}")

            doc = c4d.documents.GetActiveDocument()
            active_id = None
            if doc:
                try:
                    rd = doc.GetActiveRenderData()
                    if rd:
                        active_id = rd[c4d.RDATA_RENDERENGINE]
                except Exception:
                    pass

            for e in engines:
                e["is_active"] = (e["id"] == active_id)

            engines.sort(key=lambda e: e["name"])

            return {
                "engine_count": len(engines),
                "active_renderer_id": active_id,
                "active_renderer_name": self._renderer_name(active_id) if active_id is not None else None,
                "engines": engines,
            }
        except Exception as e:
            return {"error": f"list_render_engines failed: {e}", "traceback": traceback.format_exc()}

    def handle_get_active_renderer(self, command):
        """Return the active document's render engine id, name, and full RenderData parameter dump."""
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}
        rd = doc.GetActiveRenderData()
        if not rd:
            return {"error": "No active RenderData"}
        try:
            engine_id = rd[c4d.RDATA_RENDERENGINE]
            xres = rd[c4d.RDATA_XRES] if c4d.RDATA_XRES in rd.GetData() else None
            yres = rd[c4d.RDATA_YRES] if c4d.RDATA_YRES in rd.GetData() else None
            return {
                "renderer_id": engine_id,
                "renderer_name": self._renderer_name(engine_id),
                "render_data_name": rd.GetName(),
                "resolution": [xres, yres] if xres and yres else None,
            }
        except Exception as e:
            return {"error": f"get_active_renderer failed: {e}", "traceback": traceback.format_exc()}

    # ---- Material graph / shader / CallCommand (Tier 5: Octane workflow) ----

    def _walk_shader(self, shader, depth=0, max_depth=20, visited=None):
        """Recursively walk a shader tree. Returns a list of {depth, name, type_id, guid, params}."""
        if visited is None:
            visited = set()
        nodes = []
        cur = shader
        while cur is not None:
            try:
                key = id(cur)
                if key in visited or depth > max_depth:
                    cur = cur.GetNext() if hasattr(cur, "GetNext") else None
                    continue
                visited.add(key)
                node = {
                    "depth": depth,
                    "name": cur.GetName(),
                    "type_id": cur.GetType(),
                    "guid": str(cur.GetGUID()) if hasattr(cur, "GetGUID") else None,
                }
                # Try to extract a few common parameters by enumerating description
                try:
                    desc = cur.GetDescription(c4d.DESCFLAGS_DESC_0)
                    if desc:
                        params_summary = []
                        count = 0
                        for bc, paramid, groupid in desc:
                            if not bc:
                                continue
                            if count >= 30:  # cap per-shader to keep response manageable
                                break
                            try:
                                pname = bc.GetString(c4d.DESC_NAME)
                                if not pname:
                                    continue
                                val = self._value_to_jsonable(cur[paramid])
                                params_summary.append({
                                    "path": self._descid_to_path(paramid),
                                    "name": pname,
                                    "value": val,
                                })
                                count += 1
                            except Exception:
                                pass
                        node["params"] = params_summary
                except Exception as e:
                    node["params_error"] = str(e)

                # Children: shader.GetDown() for nested shader graphs
                child = cur.GetDown() if hasattr(cur, "GetDown") else None
                if child is not None:
                    node["children"] = self._walk_shader(child, depth + 1, max_depth, visited)
                nodes.append(node)
                cur = cur.GetNext() if hasattr(cur, "GetNext") else None
            except Exception as e:
                nodes.append({"depth": depth, "name": "<error>", "error": str(e)})
                break
        return nodes

    def handle_dump_material_graph(self, command):
        """Walk the shader tree of a material (or any object that exposes GetFirstShader).

        Required: 'material_name' or 'guid' (or 'object_name' to dump shaders attached
        to a non-material object — e.g. an Octane Area Light's emission shader).
        Optional: 'max_depth' (int, default 20).
        """
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        max_depth = int(command.get("max_depth", 20))
        material_name = command.get("material_name")

        target = None
        # Try material lookup first
        if material_name:
            for m in doc.GetMaterials():
                if m.GetName() == material_name:
                    target = m
                    break
            if target is None:
                return {"error": f"Material not found: {material_name}"}
        else:
            # Fall back to object lookup
            target, err = self._resolve_object(doc, command)
            if err:
                return {"error": err}

        try:
            first_shader = target.GetFirstShader() if hasattr(target, "GetFirstShader") else None
        except Exception as e:
            return {"error": f"GetFirstShader failed: {e}"}

        graph = self._walk_shader(first_shader, depth=0, max_depth=max_depth) if first_shader else []

        # Also dump tags if the target is an object (some shaders live on tags, e.g. Octane tag)
        tag_shaders = []
        if hasattr(target, "GetFirstTag"):
            tag = target.GetFirstTag()
            while tag:
                try:
                    ts = tag.GetFirstShader() if hasattr(tag, "GetFirstShader") else None
                    if ts:
                        tag_shaders.append({
                            "tag_name": tag.GetName(),
                            "tag_type_id": tag.GetType(),
                            "shaders": self._walk_shader(ts, depth=0, max_depth=max_depth),
                        })
                except Exception as e:
                    tag_shaders.append({"tag_name": tag.GetName(), "error": str(e)})
                tag = tag.GetNext()

        return {
            "target": {
                "name": target.GetName(),
                "type_id": target.GetType(),
                "guid": str(target.GetGUID()) if hasattr(target, "GetGUID") else None,
            },
            "shader_count": len(graph),
            "shader_graph": graph,
            "tag_shader_graphs": tag_shaders,
        }

    def handle_create_via_command(self, command):
        """Execute c4d.CallCommand(id) on the main thread and return what was newly created/selected.

        Required: 'command_id' (int) — the C4D command ID (e.g. 1033864 for Octane Area Light).
        Optional: 'object_name' (str) — rename the newly-created active object after creation.

        Returns the new active object's name + guid + type. The new object is what
        ended up selected after the command ran (most creator commands set the new
        object as active).
        """
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}
        cmd_id = command.get("command_id")
        if cmd_id is None:
            return {"error": "Required: command_id (int)"}

        rename_to = command.get("object_name")

        def _exec():
            try:
                # Snapshot what's selected before
                before_active = doc.GetActiveObject()
                before_guid = str(before_active.GetGUID()) if before_active else None

                c4d.CallCommand(int(cmd_id))

                # The new object is typically selected
                after_active = doc.GetActiveObject()
                if not after_active or (before_guid and str(after_active.GetGUID()) == before_guid):
                    # Maybe it landed at root and isn't active — check first object
                    return {
                        "warning": f"CallCommand({cmd_id}) executed but no new active object detected",
                        "before_active": before_guid,
                    }

                if rename_to:
                    try:
                        after_active.SetName(rename_to)
                    except Exception as e:
                        self.log(f"[CREATE_VIA_CMD] rename failed: {e}")

                c4d.EventAdd()
                return {
                    "status": "ok",
                    "command_id": int(cmd_id),
                    "new_object": {
                        "name": after_active.GetName(),
                        "type_id": after_active.GetType(),
                        "type_name": self.get_object_type_name(after_active),
                        "guid": str(after_active.GetGUID()),
                    },
                }
            except Exception as e:
                return {"error": f"create_via_command failed: {e}", "traceback": traceback.format_exc()}

        return self.execute_on_main_thread(_exec, _timeout=30)

    def handle_link_shader_to_parameter(self, command):
        """Create or attach a shader and link it as the value of a parameter on an object.

        Required:
          - target_name or target_guid: the object/material/light to receive the shader
          - parameter_path: list of int DescID levels (e.g. [1003] or [1003, 0])
                            OR parameter_name: we'll resolve via enumerate_descids match
          - shader_plugin_id: the plugin ID of the shader to create (e.g. Octane Image Texture)

        Optional:
          - shader_params: dict of {parameter_path_str: value} to set on the new shader
                           (e.g. {"OCTANE_IMAGETEX_FILE": "/tmp/canvas.png"}; falls back to
                           heuristic for "filename" / "path" / "image" parameter names if
                           an integer key isn't given)

        Returns the new shader's guid + the actual DescID that was set.
        """
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}

        # Resolve target
        target_name = command.get("target_name")
        target_guid = command.get("target_guid")
        target = None
        if target_guid:
            target = self.find_object_by_name(doc, target_guid, use_guid=True)
        elif target_name:
            # Try object first
            target = self.find_object_by_name(doc, target_name, use_guid=False)
            # Then material
            if not target:
                for m in doc.GetMaterials():
                    if m.GetName() == target_name:
                        target = m
                        break
        if target is None:
            return {"error": f"Target not found: {target_name or target_guid}"}

        shader_plugin_id = command.get("shader_plugin_id")
        if shader_plugin_id is None:
            return {"error": "Required: shader_plugin_id (int)"}

        param_path = command.get("parameter_path")
        param_name = command.get("parameter_name")
        if not param_path and not param_name:
            return {"error": "Required: parameter_path (list of int) or parameter_name (str)"}

        shader_params = command.get("shader_params") or {}

        def _exec():
            try:
                # Create the shader
                shader = c4d.BaseShader(int(shader_plugin_id))
                if not shader:
                    return {"error": f"Failed to create shader with plugin_id {shader_plugin_id}"}

                # Set shader_params if any
                applied_shader_params = []
                for k, v in shader_params.items():
                    try:
                        # If k is an int (or string of int), use directly
                        try:
                            kid = int(k)
                            shader[kid] = v
                            applied_shader_params.append({"id": kid, "value": v})
                            continue
                        except (TypeError, ValueError):
                            pass
                        # Otherwise enumerate shader params and find matching name
                        sdesc = shader.GetDescription(c4d.DESCFLAGS_DESC_0)
                        matched = False
                        for sbc, sparamid, _gid in sdesc:
                            if not sbc:
                                continue
                            sname = sbc.GetString(c4d.DESC_NAME) or ""
                            if k.lower() in sname.lower():
                                try:
                                    shader[sparamid] = v
                                    applied_shader_params.append({"path": self._descid_to_path(sparamid), "matched_name": sname, "value": v})
                                    matched = True
                                    break
                                except Exception as e:
                                    self.log(f"[LINK_SHADER] failed setting {sname}: {e}")
                        if not matched:
                            self.log(f"[LINK_SHADER] no shader param matched name '{k}'")
                    except Exception as e:
                        self.log(f"[LINK_SHADER] error setting shader param '{k}': {e}")

                # Insert the shader on the target so it's owned
                if hasattr(target, "InsertShader"):
                    target.InsertShader(shader)
                else:
                    return {"error": "Target does not support InsertShader (not a material/light/tag)"}

                # Resolve the parameter DescID
                resolved_descid = None
                resolved_name = None
                if param_path:
                    # Build a DescID from a list of int ids
                    levels = []
                    for pid in param_path:
                        levels.append(c4d.DescLevel(int(pid), 0, 0))
                    resolved_descid = c4d.DescID(*levels)
                else:
                    # Find by name
                    desc = target.GetDescription(c4d.DESCFLAGS_DESC_0)
                    pn_lower = param_name.lower()
                    for tbc, tparamid, _gid in desc:
                        if not tbc:
                            continue
                        tname = tbc.GetString(c4d.DESC_NAME) or ""
                        if pn_lower in tname.lower():
                            resolved_descid = tparamid
                            resolved_name = tname
                            break
                    if resolved_descid is None:
                        return {"error": f"Could not find parameter matching name '{param_name}' on target"}

                # Set the shader on the parameter
                try:
                    target[resolved_descid] = shader
                except Exception as e:
                    return {"error": f"Failed to assign shader to parameter: {e}", "resolved_descid_path": self._descid_to_path(resolved_descid)}

                c4d.EventAdd()

                return {
                    "status": "ok",
                    "target": {"name": target.GetName(), "guid": str(target.GetGUID()) if hasattr(target, "GetGUID") else None},
                    "shader": {
                        "plugin_id": int(shader_plugin_id),
                        "guid": str(shader.GetGUID()) if hasattr(shader, "GetGUID") else None,
                    },
                    "parameter": {
                        "path": self._descid_to_path(resolved_descid),
                        "name": resolved_name,
                    },
                    "applied_shader_params": applied_shader_params,
                }
            except Exception as e:
                return {"error": f"link_shader_to_parameter failed: {e}", "traceback": traceback.format_exc()}

        return self.execute_on_main_thread(_exec, _timeout=30)

    def handle_get_c4d_info(self, command):
        """Return Cinema 4D environment info: version, python, install paths, prefs path, etc.
        Useful for diagnostics, bug reports, and figuring out where to install plugins."""
        try:
            info = {
                "c4d_version_int": c4d.GetC4DVersion(),
                "c4d_version_major": c4d.GetC4DVersion() // 1000,
                "c4d_version_minor": (c4d.GetC4DVersion() // 100) % 10,
                "python_version": sys.version,
                "platform": sys.platform,
                "starter_path": str(c4d.storage.GeGetStartupPath()) if hasattr(c4d.storage, "GeGetStartupPath") else None,
                "plugins_path": str(c4d.storage.GeGetPluginPath()) if hasattr(c4d.storage, "GeGetPluginPath") else None,
                "prefs_path": str(c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS)) if hasattr(c4d.storage, "GeGetC4DPath") else None,
                "default_capture_path": str(c4d.storage.GeGetC4DPath(c4d.C4D_PATH_STARTUPWRITE)) if hasattr(c4d.storage, "GeGetC4DPath") else None,
                "mcp_geprint_hooked": _mcp_geprint_patched,
                "mcp_log_buffer_size": len(_mcp_log_buffer),
                "mcp_log_buffer_max": MCP_LOG_BUFFER_MAX,
            }
            doc = c4d.documents.GetActiveDocument()
            if doc:
                info["active_document"] = {
                    "name": doc.GetDocumentName() or "Untitled",
                    "path": str(doc.GetDocumentPath()) if doc.GetDocumentPath() else None,
                    "fps": doc.GetFps(),
                    "frame": doc.GetTime().GetFrame(doc.GetFps()),
                }
            return info
        except Exception as e:
            return {"error": f"get_c4d_info failed: {e}", "traceback": traceback.format_exc()}

    # === Phase 3: capability discovery + doctor ============================

    # Build a static set of supported command types from the dispatcher at
    # class-load time. Easier than reflection at request-time.
    _SUPPORTED_COMMANDS = (
        "get_scene_info", "list_objects", "group_objects", "execute_python",
        "save_scene", "load_scene", "set_keyframe",
        "add_primitive", "modify_object", "create_abstract_shape",
        "create_material", "apply_material", "apply_shader",
        "link_shader_to_parameter",
        "inspect_redshift_materials", "validate_redshift_materials",
        "render_frame", "render_preview", "snapshot_scene",
        "create_camera", "animate_camera", "create_light",
        "create_mograph_cloner", "add_effector",
        "apply_dynamics", "create_soft_body", "apply_mograph_fields",
        "create_via_command",
        "find_objects", "get_object_info", "list_render_engines",
        "get_active_renderer", "list_installed_plugins",
        "enumerate_descids", "enumerate_userdata",
        "dump_object_tree", "dump_material_graph",
        "set_viewport_shading_mode", "viewport_screenshot",
        "viewport_screenshot_multiview", "get_viewport_state",
        "vertex_map_stats", "vertex_map_threshold_to_polygon_selection",
        "uv_layout_stats", "uv_islands_to_objects",
        "uv_from_projection", "uv_transfer", "sample_vmap_via_uv",
        "sample_bitmap_at_uv",
        "run_modeling_command",
        "get_console_log", "clear_console_log",
        "get_c4d_info",
        "get_capabilities", "doctor", "ping",
        "scene_snapshot", "scene_diff",
    )
    # NOTE: this list reflects what the C4D PLUGIN's run() dispatcher actually
    # routes. Some MCP tool wrappers in server.py (tap_octane_log,
    # install_plugin, build_and_install_plugin) execute server-side without
    # roundtripping to C4D, and aren't part of this set. Others (e.g.
    # find_command_by_name, enumerate_octane_plugins, find_object_by_guid)
    # are convenience aliases that delegate to other plugin commands
    # (list_installed_plugins, find_objects) — they're advertised through
    # server.py's @mcp.tool() decorators, not through this list.

    # Tags for read-only vs scene-mutating tools (Phase 5 prep).
    # SAFE = read-only inspection, no scene mutation, no file write.
    # UNSAFE = mutates scene, executes arbitrary code, writes to disk,
    #          installs plugins, or otherwise has side effects.
    _SAFE_COMMANDS = frozenset({
        "get_scene_info", "list_objects",
        "inspect_redshift_materials",
        "find_objects", "get_object_info", "list_render_engines",
        "get_active_renderer", "list_installed_plugins",
        "enumerate_descids", "enumerate_userdata",
        "dump_object_tree", "dump_material_graph",
        "viewport_screenshot", "viewport_screenshot_multiview",
        "get_viewport_state",
        "vertex_map_stats", "uv_layout_stats",
        "get_console_log", "clear_console_log",
        "get_c4d_info", "get_capabilities", "doctor", "ping",
        "scene_snapshot", "scene_diff",
    })

    def handle_get_capabilities(self, command):
        """Capability/health snapshot for an MCP client.

        Returns plugin version, C4D version, available render engines, the
        list of commands this build of the plugin supports, the SAFE/UNSAFE
        partition for each, MCP_AUTH_TOKEN required state, and a snapshot
        of the active document (no per-object enumeration; cheap).

        Lets a fresh client discover what's actually available without
        trial-and-error probing — the foundation for client-side caching
        of the tool surface and for safe-mode gating.
        """
        try:
            info = {
                "plugin": {
                    "name": "cinema4d-mcp (sdimaging fork)",
                    "version": "0.2.0",
                    "plugin_id": PLUGIN_ID,
                },
                "c4d": {
                    "version_int": c4d.GetC4DVersion(),
                    "version_major": c4d.GetC4DVersion() // 1000,
                    "version_minor": (c4d.GetC4DVersion() // 100) % 10,
                    "python_version": sys.version.split()[0],
                    "platform": sys.platform,
                },
                "auth": {
                    "auth_token_required": bool(self.auth_token),
                    "host": self.host,
                    "port": self.port,
                    "safe_mode": self.safe_mode,
                },
                "transport": {
                    "framing": "newline-delimited-json",
                    "max_payload_bytes": None,
                    "request_id_supported": False,
                },
                "commands": {
                    "supported": list(self._SUPPORTED_COMMANDS),
                    "safe": sorted(self._SAFE_COMMANDS),
                    "unsafe": sorted(set(self._SUPPORTED_COMMANDS) - self._SAFE_COMMANDS),
                    "count_total": len(self._SUPPORTED_COMMANDS),
                    "count_safe": len(self._SAFE_COMMANDS),
                },
            }

            # Renderers — short, no fail-on-error
            try:
                rd = c4d.documents.GetActiveDocument().GetActiveRenderData()
                if rd:
                    rid = rd[c4d.RDATA_RENDERENGINE]
                    info["renderer"] = {
                        "active_id": rid,
                        "active_name": self._renderer_name(rid) if hasattr(self, "_renderer_name") else None,
                    }
            except Exception:
                info["renderer"] = None

            # Active document summary (counts only — cheap)
            try:
                doc = c4d.documents.GetActiveDocument()
                if doc:
                    obj_count = 0
                    op = doc.GetFirstObject()
                    while op:
                        obj_count += 1
                        # depth-first walk
                        if op.GetDown():
                            op = op.GetDown()
                            continue
                        while op and not op.GetNext():
                            op = op.GetUp()
                        if op:
                            op = op.GetNext()
                    mat_count = 0
                    m = doc.GetFirstMaterial()
                    while m:
                        mat_count += 1
                        m = m.GetNext()
                    info["document"] = {
                        "name": doc.GetDocumentName() or "Untitled",
                        "path": str(doc.GetDocumentPath()) if doc.GetDocumentPath() else None,
                        "object_count": obj_count,
                        "material_count": mat_count,
                        "fps": doc.GetFps(),
                        "frame": doc.GetTime().GetFrame(doc.GetFps()),
                    }
            except Exception as e:
                info["document"] = {"error": str(e)}

            return info
        except Exception as e:
            return {"error": f"get_capabilities failed: {e}", "traceback": traceback.format_exc()}

    def handle_ping(self, command):
        """Cheapest possible liveness probe.

        Returns {ok, server_time, plugin_version, c4d_version, echo}.
        Echoes the optional `echo` field from the command (string, max 1KB)
        so a client can correlate beyond request_id if desired.

        Use for heartbeat / connection health checks. Doesn't touch C4D
        state, doesn't acquire main thread, doesn't allocate. Safe to call
        thousands of times per minute.
        """
        echo = command.get("echo")
        if isinstance(echo, str) and len(echo) > 1024:
            echo = echo[:1024] + "...<truncated>"
        return {
            "ok": True,
            "server_time": time.time(),
            "plugin_version": "0.2.0",
            "c4d_version": c4d.GetC4DVersion(),
            "echo": echo,
        }

    def handle_doctor(self, command):
        """Health check: ping main thread, exercise the dispatch path, time
        a tiny no-op render, report the log buffer state. Useful for diagnosing
        "is the bridge actually alive and responsive?" without modifying state.

        Each check is independent — failure of one doesn't prevent the others.
        """
        try:
            checks = []

            # 1. Main-thread responsiveness — submit a no-op fn to the queue
            #    and time the round-trip.
            t0 = time.time()
            try:
                _ = self.execute_on_main_thread(lambda: True, _timeout=10)
                checks.append({
                    "name": "main_thread_responsive",
                    "ok": True,
                    "duration_ms": int((time.time() - t0) * 1000),
                })
            except Exception as e:
                checks.append({"name": "main_thread_responsive", "ok": False, "error": str(e)})

            # 2. Active doc accessible
            try:
                doc = c4d.documents.GetActiveDocument()
                checks.append({
                    "name": "active_document",
                    "ok": doc is not None,
                    "doc_name": doc.GetDocumentName() if doc else None,
                })
            except Exception as e:
                checks.append({"name": "active_document", "ok": False, "error": str(e)})

            # 3. Active basedraw / camera
            try:
                bd = doc.GetActiveBaseDraw() if doc else None
                cam = bd.GetSceneCamera(doc) if bd else None
                if cam is None and bd:
                    cam = bd.GetEditorCamera()
                checks.append({
                    "name": "viewport_ready",
                    "ok": bd is not None and cam is not None,
                    "active_camera": cam.GetName() if cam else None,
                })
            except Exception as e:
                checks.append({"name": "viewport_ready", "ok": False, "error": str(e)})

            # 4. Console log buffer
            checks.append({
                "name": "console_log_hook",
                "ok": _mcp_geprint_patched,
                "buffer_size": len(_mcp_log_buffer),
                "buffer_max": MCP_LOG_BUFFER_MAX,
            })

            # 5. Auth state
            checks.append({
                "name": "auth_configured",
                "ok": True,
                "auth_token_required": bool(self.auth_token),
                "host": self.host,
                "safe_mode": self.safe_mode,
            })

            all_ok = all(c.get("ok", False) for c in checks)
            return {
                "status": "ok" if all_ok else "warn",
                "all_ok": all_ok,
                "checks": checks,
                "plugin_version": "0.2.0",
                "c4d_version": c4d.GetC4DVersion(),
            }
        except Exception as e:
            return {"error": f"doctor failed: {e}", "traceback": traceback.format_exc()}

    # === Phase 4: GUID-first scene snapshot + diff ==========================

    # Module-level snapshot cache — keep most-recent N keyed by short id.
    # Lets a client store a snapshot once, then ask for a diff against it
    # without resending the full payload.
    _SNAPSHOT_CACHE = {}
    _SNAPSHOT_CACHE_MAX = 16

    def _matrix_to_dict(self, m):
        """Serialise a c4d.Matrix to a JSON-safe dict."""
        return {
            "off": [m.off.x, m.off.y, m.off.z],
            "v1":  [m.v1.x,  m.v1.y,  m.v1.z],
            "v2":  [m.v2.x,  m.v2.y,  m.v2.z],
            "v3":  [m.v3.x,  m.v3.y,  m.v3.z],
        }

    def _snapshot_one_object(self, obj, parent_guid, depth, detail):
        """Serialise a single BaseObject to a snapshot dict. detail in {'summary','full'}."""
        try:
            guid = str(obj.GetGUID())
        except Exception:
            guid = None

        try:
            type_id = obj.GetType()
            type_name = self.get_object_type_name(obj)
        except Exception:
            type_id, type_name = -1, "?"

        entry = {
            "guid": guid,
            "name": obj.GetName(),
            "type_id": type_id,
            "type_name": type_name,
            "parent_guid": parent_guid,
            "depth": depth,
        }

        if detail == "summary":
            return entry

        # full detail
        try:
            entry["matrix_local"] = self._matrix_to_dict(obj.GetMl())
        except Exception:
            pass

        # poly-specific fields
        if type_id == c4d.Opolygon:
            try:
                entry["point_count"] = obj.GetPointCount()
                entry["polygon_count"] = obj.GetPolygonCount()
            except Exception:
                pass

        # tags — capture type + name only (cheap)
        try:
            tags = obj.GetTags() or []
            entry["tags"] = [
                {
                    "type_id": t.GetType(),
                    "type_name": getattr(t, "GetTypeName", lambda: "?")(),
                    "name": t.GetName(),
                }
                for t in tags
            ]
        except Exception as e:
            entry["tags_error"] = str(e)

        try:
            entry["selected"] = bool(obj.GetBit(c4d.BIT_ACTIVE))
        except Exception:
            pass

        return entry

    def _walk_objects_for_snapshot(self, doc, detail):
        """Depth-first walk; yields (obj, parent_guid, depth) tuples."""
        results = []
        def walk(obj, parent_guid, depth):
            while obj is not None:
                try:
                    g = str(obj.GetGUID())
                except Exception:
                    g = None
                results.append((obj, parent_guid, depth))
                if obj.GetDown():
                    walk(obj.GetDown(), g, depth + 1)
                obj = obj.GetNext()
        walk(doc.GetFirstObject(), None, 0)
        return results

    def handle_scene_snapshot(self, command):
        """Capture a typed snapshot of the active document.

        Args:
          detail (str): 'summary' (default) — counts + per-object guid/name/
                        type/parent only; cheap. 'full' — adds local matrix,
                        tag list, point/polygon counts.
          cache (bool): if True (default), cache the snapshot under a short
                        snapshot_id and return that id; lets a client run
                        scene_diff later without resending the full payload.

        Returns: typed scene model with snapshot_id, doc summary, objects[],
                 materials[], summary counts. NEVER mutates state.
        """
        try:
            doc = c4d.documents.GetActiveDocument()
            if not doc:
                return {"error": "No active document"}

            detail = command.get("detail", "summary")
            if detail not in ("summary", "full"):
                return {"error": f"detail must be 'summary' or 'full', got {detail!r}"}
            do_cache = bool(command.get("cache", True))

            # Doc-level
            doc_info = {
                "name": doc.GetDocumentName() or "Untitled",
                "path": str(doc.GetDocumentPath()) if doc.GetDocumentPath() else None,
                "fps": doc.GetFps(),
                "frame": doc.GetTime().GetFrame(doc.GetFps()),
            }
            try:
                rd = doc.GetActiveRenderData()
                if rd:
                    rid = rd[c4d.RDATA_RENDERENGINE]
                    doc_info["active_renderer_id"] = rid
                    doc_info["active_renderer_name"] = (
                        self._renderer_name(rid) if hasattr(self, "_renderer_name") else None
                    )
            except Exception:
                pass
            try:
                bd = doc.GetActiveBaseDraw()
                cam = bd.GetSceneCamera(doc) if bd else None
                if cam is None and bd:
                    cam = bd.GetEditorCamera()
                if cam:
                    doc_info["active_camera_guid"] = str(cam.GetGUID())
                    doc_info["active_camera_name"] = cam.GetName()
            except Exception:
                pass

            # Objects
            walked = self._walk_objects_for_snapshot(doc, detail)
            objects = [
                self._snapshot_one_object(obj, parent_g, depth, detail)
                for obj, parent_g, depth in walked
            ]

            # Materials
            materials = []
            try:
                m = doc.GetFirstMaterial()
                while m:
                    try:
                        materials.append({
                            "guid": str(m.GetGUID()),
                            "name": m.GetName(),
                            "type_id": m.GetType(),
                            "type_name": getattr(m, "GetTypeName", lambda: "?")(),
                        })
                    except Exception as e:
                        materials.append({"name": "<error>", "error": str(e)})
                    m = m.GetNext()
            except Exception:
                pass

            # Summary
            summary = {
                "object_count": len(objects),
                "polygon_object_count": sum(1 for o in objects if o.get("type_id") == c4d.Opolygon),
                "material_count": len(materials),
                "tag_count": sum(len(o.get("tags", [])) for o in objects) if detail == "full" else None,
            }

            response = {
                "snapshot_version": 1,
                "timestamp": time.time(),
                "detail": detail,
                "doc": doc_info,
                "objects": objects,
                "materials": materials,
                "summary": summary,
            }

            if do_cache:
                # Generate a short hash-based id; LRU-evict to cap memory
                import hashlib
                snap_id = hashlib.md5(
                    f"{response['timestamp']}-{summary['object_count']}-{detail}".encode()
                ).hexdigest()[:12]
                C4DSocketServer._SNAPSHOT_CACHE[snap_id] = response
                # Evict oldest if over cap
                if len(C4DSocketServer._SNAPSHOT_CACHE) > C4DSocketServer._SNAPSHOT_CACHE_MAX:
                    # Simple FIFO; insertion order preserved in py3.7+
                    oldest = next(iter(C4DSocketServer._SNAPSHOT_CACHE))
                    del C4DSocketServer._SNAPSHOT_CACHE[oldest]
                response["snapshot_id"] = snap_id
                response["cache_size"] = len(C4DSocketServer._SNAPSHOT_CACHE)

            return response
        except Exception as e:
            return {"error": f"scene_snapshot failed: {e}", "traceback": traceback.format_exc()}

    def handle_scene_diff(self, command):
        """Compare two scene snapshots and return added/removed/changed sets.

        Args:
          prev_snapshot_id (str, optional) — id from a prior scene_snapshot
                                             call (must have cache=True).
          prev_snapshot (dict, optional) — full prior snapshot inline (use
                                           if you didn't cache server-side
                                           or restarted between calls).
          curr_snapshot_id (str, optional) — same, for the "current" side.
                                             Defaults to taking a fresh
                                             snapshot of the live doc.

        Exactly one of prev_snapshot_id / prev_snapshot must be provided.

        Returns:
          {
            added_objects: [{guid,name,type_name,...}, ...],
            removed_objects: [{guid,name,type_name,...}, ...],  # from prev
            transform_changed: [{guid, name, old, new}, ...],
            name_changed: [{guid, old_name, new_name}, ...],
            topology_changed: [{guid, name, old_pts, new_pts, old_polys, new_polys}, ...],
            tag_changes: [{guid, name, added_tags, removed_tags}, ...],
            material_diff: {added: [...], removed: [...]}
          }
        """
        try:
            prev_id = command.get("prev_snapshot_id")
            prev_inline = command.get("prev_snapshot")
            curr_id = command.get("curr_snapshot_id")

            # Resolve prev
            if prev_id:
                prev = C4DSocketServer._SNAPSHOT_CACHE.get(prev_id)
                if prev is None:
                    return {"error": f"prev_snapshot_id '{prev_id}' not in cache (size={len(C4DSocketServer._SNAPSHOT_CACHE)})"}
            elif prev_inline:
                prev = prev_inline
            else:
                return {"error": "Provide either prev_snapshot_id or prev_snapshot (full inline)"}

            # Resolve curr
            if curr_id:
                curr = C4DSocketServer._SNAPSHOT_CACHE.get(curr_id)
                if curr is None:
                    return {"error": f"curr_snapshot_id '{curr_id}' not in cache"}
            else:
                curr = self.handle_scene_snapshot({"detail": prev.get("detail", "summary"), "cache": False})
                if "error" in curr:
                    return curr

            # Build guid → entry maps
            prev_objs = {o.get("guid"): o for o in prev.get("objects", []) if o.get("guid")}
            curr_objs = {o.get("guid"): o for o in curr.get("objects", []) if o.get("guid")}

            added = [curr_objs[g] for g in (set(curr_objs) - set(prev_objs))]
            removed = [prev_objs[g] for g in (set(prev_objs) - set(curr_objs))]

            transform_changed = []
            name_changed = []
            topology_changed = []
            tag_changes = []

            for g in (set(curr_objs) & set(prev_objs)):
                pe, ce = prev_objs[g], curr_objs[g]
                # name
                if pe.get("name") != ce.get("name"):
                    name_changed.append({"guid": g, "old_name": pe.get("name"), "new_name": ce.get("name")})
                # transform
                pm, cm = pe.get("matrix_local"), ce.get("matrix_local")
                if pm and cm and pm != cm:
                    transform_changed.append({"guid": g, "name": ce.get("name"), "old": pm, "new": cm})
                # topology
                if (pe.get("point_count") != ce.get("point_count")
                        or pe.get("polygon_count") != ce.get("polygon_count")):
                    topology_changed.append({
                        "guid": g, "name": ce.get("name"),
                        "old_pts": pe.get("point_count"), "new_pts": ce.get("point_count"),
                        "old_polys": pe.get("polygon_count"), "new_polys": ce.get("polygon_count"),
                    })
                # tags (compare type-id sets)
                p_tags = {(t.get("type_id"), t.get("name")) for t in (pe.get("tags") or [])}
                c_tags = {(t.get("type_id"), t.get("name")) for t in (ce.get("tags") or [])}
                if p_tags != c_tags:
                    tag_changes.append({
                        "guid": g, "name": ce.get("name"),
                        "added_tags": [{"type_id": tid, "name": tn} for tid, tn in (c_tags - p_tags)],
                        "removed_tags": [{"type_id": tid, "name": tn} for tid, tn in (p_tags - c_tags)],
                    })

            # Materials
            prev_mats = {m.get("guid"): m for m in prev.get("materials", []) if m.get("guid")}
            curr_mats = {m.get("guid"): m for m in curr.get("materials", []) if m.get("guid")}
            material_diff = {
                "added":   [curr_mats[g] for g in (set(curr_mats) - set(prev_mats))],
                "removed": [prev_mats[g] for g in (set(prev_mats) - set(curr_mats))],
            }

            return {
                "status": "ok",
                "prev_timestamp": prev.get("timestamp"),
                "curr_timestamp": curr.get("timestamp"),
                "added_objects": added,
                "removed_objects": removed,
                "transform_changed": transform_changed,
                "name_changed": name_changed,
                "topology_changed": topology_changed,
                "tag_changes": tag_changes,
                "material_diff": material_diff,
                "summary": {
                    "added": len(added),
                    "removed": len(removed),
                    "transform_changed": len(transform_changed),
                    "name_changed": len(name_changed),
                    "topology_changed": len(topology_changed),
                    "tag_changes": len(tag_changes),
                    "materials_added": len(material_diff["added"]),
                    "materials_removed": len(material_diff["removed"]),
                },
            }
        except Exception as e:
            return {"error": f"scene_diff failed: {e}", "traceback": traceback.format_exc()}

    def handle_dump_object_tree(self, command):
        """Dump the scene hierarchy as a flat list of {depth, name, type_name, type_id, guid}.
        Pass 'guid' or 'object_name' to start at a specific subtree; omit both to dump from doc root.
        """
        doc = c4d.documents.GetActiveDocument()
        if not doc:
            return {"error": "No active document"}
        max_depth = int(command.get("max_depth", 100))

        root_obj = None
        if command.get("guid") or command.get("object_name") or command.get("name") or command.get("identifier"):
            root_obj, err = self._resolve_object(doc, command)
            if err:
                return {"error": err}

        nodes = []

        def walk(obj, depth):
            while obj is not None:
                if depth > max_depth:
                    obj = obj.GetNext()
                    continue
                try:
                    nodes.append({
                        "depth": depth,
                        "name": obj.GetName(),
                        "type_id": obj.GetType(),
                        "type_name": self.get_object_type_name(obj),
                        "guid": str(obj.GetGUID()),
                    })
                except Exception as e:
                    nodes.append({"depth": depth, "name": "<error>", "error": str(e)})
                walk(obj.GetDown(), depth + 1)
                obj = obj.GetNext()

        if root_obj is not None:
            # Include the root itself, then walk children
            try:
                nodes.append({
                    "depth": 0,
                    "name": root_obj.GetName(),
                    "type_id": root_obj.GetType(),
                    "type_name": self.get_object_type_name(root_obj),
                    "guid": str(root_obj.GetGUID()),
                })
            except Exception as e:
                nodes.append({"depth": 0, "name": "<error>", "error": str(e)})
            walk(root_obj.GetDown(), 1)
        else:
            walk(doc.GetFirstObject(), 0)

        return {"node_count": len(nodes), "nodes": nodes}


class SocketServerDialog(gui.GeDialog):
    """GUI Dialog to control the server and display logs."""

    def __init__(self):
        super(SocketServerDialog, self).__init__()
        self.server = None
        self.msg_queue = queue.Queue()  # Thread-safe queue
        self.SetTimer(100)  # Update UI at 10 Hz

    def CreateLayout(self):
        self.SetTitle("Socket Server Control")

        self.status_text = self.AddStaticText(
            1002, c4d.BFH_SCALEFIT, name="Server: Offline"
        )

        self.GroupBegin(1010, c4d.BFH_SCALEFIT, 2, 1)
        self.AddButton(1011, c4d.BFH_SCALE, name="Start Server")
        self.AddButton(1012, c4d.BFH_SCALE, name="Stop Server")
        self.GroupEnd()

        self.log_box = self.AddMultiLineEditText(
            1004,
            c4d.BFH_SCALEFIT,
            initw=400,
            inith=250,
            style=c4d.DR_MULTILINE_READONLY,
        )

        self.Enable(1012, False)  # Disable "Stop" button initially
        return True

    def CoreMessage(self, id, msg):
        """Handles UI updates and main thread execution triggered by SpecialEventAdd()."""
        if id == PLUGIN_ID:
            try:
                # Process all pending messages in the queue
                while not self.msg_queue.empty():
                    try:
                        # Get next message from queue with timeout to avoid potential deadlocks
                        msg_type, msg_value = self.msg_queue.get(timeout=0.1)

                        # Process based on message type
                        if msg_type == "STATUS":
                            self.UpdateStatusText(msg_value)
                        elif msg_type == "LOG":
                            self.AppendLog(msg_value)
                        elif msg_type == "EXEC":
                            # Execute function on main thread
                            if callable(msg_value):
                                try:
                                    msg_value()
                                except Exception as e:
                                    error_msg = f"[**ERROR**] Error in main thread execution: {str(e)}"
                                    self.AppendLog(error_msg)
                                    print(
                                        error_msg
                                    )  # Also print to console for debugging
                            else:
                                self.AppendLog(
                                    f"[C4D] ## Warning ##: Non-callable value received: {type(msg_value)}"
                                )
                        else:
                            self.AppendLog(
                                f"[C4D] ## Warning ##: Unknown message type: {msg_type}"
                            )
                    except queue.Empty:
                        # Queue timeout - break the loop to prevent blocking
                        break
                    except Exception as e:
                        # Handle any other exceptions during message processing
                        error_msg = f"[**ERROR**] Error processing message: {str(e)}"
                        self.AppendLog(error_msg)
                        print(error_msg)  # Also print to console for debugging
            except Exception as e:
                # Catch all exceptions to prevent Cinema 4D from crashing
                error_msg = f"[C4D] Critical error in message processing: {str(e)}"
                print(error_msg)  # Print to console as UI might be unstable
                try:
                    self.AppendLog(error_msg)
                except:
                    pass  # Ignore if we can't even log to UI

        return True

    def Timer(self, msg):
        """Periodic UI update in case SpecialEventAdd() missed something."""
        if self.server:
            if not self.server.running:  # Detect unexpected crashes
                self.UpdateStatusText("Offline")
                self.Enable(1011, True)
                self.Enable(1012, False)
        return True

    def UpdateStatusText(self, status):
        """Update server status UI."""
        self.SetString(1002, f"Server: {status}")
        self.Enable(1011, status == "Offline")
        self.Enable(1012, status == "Online")

    def AppendLog(self, message):
        """Append log messages to UI."""
        existing_text = self.GetString(1004)
        new_text = (existing_text + "\n" + message).strip()
        self.SetString(1004, new_text)

    def Command(self, id, msg):
        if id == 1011:  # Start Server button
            self.StartServer()
            return True
        elif id == 1012:  # Stop Server button
            self.StopServer()
            return True
        return False

    def StartServer(self):
        """Start the socket server thread.

        Defensive: if a previous server thread died unexpectedly (e.g.,
        Octane workspace switch wedged the main thread long enough that
        the socket worker errored out), `self.server` is still non-None
        but the thread itself is dead. Detect that and restart cleanly
        instead of becoming a no-op.
        """
        server_is_alive = (
            self.server is not None
            and hasattr(self.server, "is_alive")
            and self.server.is_alive()
        )
        if not server_is_alive:
            # Best-effort cleanup of dead reference before re-spawning.
            if self.server is not None:
                try:
                    self.server.running = False
                except Exception:
                    pass
                try:
                    if getattr(self.server, "socket", None):
                        self.server.socket.close()
                except Exception:
                    pass
                self.server = None

            mcp_install_geprint_hook()
            self.server = C4DSocketServer(msg_queue=self.msg_queue)
            self.server.start()

        self.Enable(1011, False)
        self.Enable(1012, True)

    def StopServer(self):
        """Stop the socket server."""
        if self.server:
            self.server.stop()
            self.server = None
            self.Enable(1011, True)
            self.Enable(1012, False)
            # leave the GePrint hook in place so the buffer keeps
            # capturing output even when the socket server is briefly down.


class SocketServerPlugin(c4d.plugins.CommandData):
    """Cinema 4D Plugin Wrapper"""

    PLUGIN_ID = 1057843
    PLUGIN_NAME = "Socket Server Plugin"

    def __init__(self):
        self.dialog = None

    def Execute(self, doc):
        if self.dialog is None:
            self.dialog = SocketServerDialog()
        return self.dialog.Open(
            dlgtype=c4d.DLG_TYPE_ASYNC,
            pluginid=self.PLUGIN_ID,
            defaultw=400,
            defaulth=300,
        )

    def GetState(self, doc):
        return c4d.CMD_ENABLED


# ============================================================
# module-level plugin instance + auto-start hook.
# When C4D finishes loading, automatically open the dialog and click
# "Start Server" so the socket comes up without manual intervention.
# Disable by setting env var C4D_MCP_NO_AUTOSTART=1 before launching C4D.
# ============================================================
_socket_server_plugin = SocketServerPlugin()


def PluginMessage(msg_id, data):
    """Module-level plugin message handler. C4D calls this for global plugin events."""
    try:
        program_started_const = getattr(c4d, "C4DPL_PROGRAM_STARTED", None)
        if program_started_const is not None and msg_id == program_started_const:
            if os.environ.get("C4D_MCP_NO_AUTOSTART"):
                mcp_log_append("mcp", "Socket auto-start skipped (C4D_MCP_NO_AUTOSTART env set)")
                return True
            try:
                doc = c4d.documents.GetActiveDocument()
                _socket_server_plugin.Execute(doc)
                dlg = _socket_server_plugin.dialog
                if dlg:
                    dlg.StartServer()
                    mcp_log_append("mcp", "Socket auto-started on C4DPL_PROGRAM_STARTED")
                else:
                    mcp_log_append("mcp", "Auto-start: dialog not allocated after Execute()")
            except Exception as e:
                mcp_log_append("mcp", f"Auto-start failed: {e}\n{traceback.format_exc()[-400:]}")
    except Exception as e:
        # Never let PluginMessage crash C4D
        try:
            print(f"[Cinema 4D MCP] PluginMessage error: {e}")
        except Exception:
            pass
    return True


if __name__ == "__main__":
    c4d.plugins.RegisterCommandPlugin(
        SocketServerPlugin.PLUGIN_ID,
        SocketServerPlugin.PLUGIN_NAME,
        0,
        None,
        None,
        _socket_server_plugin,
    )
