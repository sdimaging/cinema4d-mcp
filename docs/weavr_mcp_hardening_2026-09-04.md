# Weavr-driven MCP hardening — 2026-09-04

## Changes

- Byte-framed UTF-8 requests, including a limit on unterminated messages.
- No raw request/script/auth-token logging; bounded response encoding.
- Atomic cancellation before queued work starts. Running timeouts return an
  execution ID for get_execution_status; they are not permission to repeat edits.
- Bounded stdout capture and opt-in safe variable previews.
- File-first screenshots, capped inline output, explicit save errors, and
  restoration of timeline / active render settings on failure as well as success.
- Loaded-source hash and PID in ping, so disk-copy success is not mistaken for
  a new live build.
- Explicit-target Windows/WSL installer. Graceful restart refuses dirty documents,
  keeps backups outside the scanned plugin folder, verifies copied hashes and
  waits for the expected live PID/hash.
- Capability registry repaired; gotchas #125–130 added; #97/#107 corrected.

## Compatibility

execute_python defaults to include_variables=false. Use print for concise
results or include_variables=true for bounded previews. stdout is capped at
65,536 characters. timeout_seconds defaults to 60 and is clamped to 0.1–110;
the client waits 120 seconds. A cancelled_before_start result cannot execute
later; running must be polled. Unknown/expired execution IDs are NOT proof of
non-execution. Recent timed-out execution history is bounded to 64 records.

viewport_screenshot now returns a saved path by default, using a unique host-temp
PNG when save_path is omitted. Set inline=true explicitly for small inline images
(maximum 384 KiB before base64). Dimensions are limited to 1..2048 pixels.
Safe mode requires inline=true and no file path. Frame overrides restore timeline
and render settings, but cannot promise to rewind stateful simulation history.

C4D_MCP_AUTOSTART=1 is opt-in. The obsolete NO_AUTOSTART documentation was wrong.
The installer never uses Reload Python Plugins or force-kills C4D. If a document
is dirty, save it and release C4D from other automation before requesting restart.

## Verification / open gate

- python -B tests/test_contract.py: **5/5**, 110 tool command types / dispatcher
  branches / advertised commands.
- Existing WSL environment: unittest discovery **16/16** (4 client + 12 plugin
  safety tests). Plugin methods are extracted from the actual .pyp by AST,
  not a reimplementation.
- Screenshot failure tests verify timeline/render-data restoration and refusal
  to fall back to inline on a failed save.
- Installer PowerShell syntax: PASS.
- Live dirty-document guard: PASS; refused an unsaved Circuitry validation scene
  before any quit or file replacement.

Full installation, new ping identity, success-path screenshots and restart/reopen
acceptance are **pending a safe C4D window**. The currently running C4D session
still has the previous MCP build. This is a review candidate, not a claim of
completed live deployment.

