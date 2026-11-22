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

## Verification / remaining gate

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

On 2026-09-04, after the artist released a clean C4D session, the normal Quit /
install / Start cycle completed. C4D 2026.3.1 returned as PID 6708 with build
`2026-09-04-transport-controls-1` and loaded-source SHA256
`ca395ad975cdb7d69a260e02cad05089b25cb68e8b593361ca02454812003932`.
The installer verified the expected process and loaded hash, not just disk files.
Backups are outside plugin discovery in `mcp-backups/20260904_231440_562`.

Live checks on that process:

- Unicode script output (em dash / web emoji / check mark) round-tripped, and the
  supplied request ID was echoed.
- A 70,000-character print stored exactly 65,536 characters and reported truncation;
  an opt-in 100,000-element list became `<list: 100000 items>`, not expanded data.
- A 0.1-second wait on a read-only 0.3-second script returned `running`; polling
  the same execution ID later returned `completed` and its final output.
- A second read-only script queued behind a one-second busy main thread timed out
  as `cancelled_before_start`; it remained cancelled after the first completed.
- An 800 x 800 screenshot with no path returned a unique PNG path (38,210 bytes),
  not base64. Frame override 7 restored the original document, frame 0 and the exact
  active RenderData object. This was an empty-scene transport test, not visual QA.
- The 16 unit tests and five command-contract checks were rerun and passed.

Remaining: saved multi-document reopen acceptance (this cycle began empty),
broader application soak, and actual keyboard/drop-box acceptance for Weavr.
The Computer Use native pipe is still unavailable after the PC reboot. API
parameter tests are explicitly not evidence that keyboard/Tab/Enter or UI drops
work. The hardening branch remains draft PR #2, not a claim of merge to main.
