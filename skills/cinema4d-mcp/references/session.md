# Session safety and deployment

Inspect all documents before restarting. Preserve artist files, unsaved scenes,
timeline state and test ownership. Use private/unlisted clones for numeric
experiments and owned temporary documents for visible QA. C4D can retire an
empty untitled document on a switch; retained wrappers may be dead. If scene
preservation cannot be verified, stop the restart and request the missing save,
not blanket permission for routine steps already authorized.

For a requested update: build with the pinned SDK, preserve documents, quit
normally, verify C4D fully exited, back up the installed DLL/resources outside
scanned plugin directories, copy to the explicit installation and compare
hashes. Use the project's existing startup/sync scripts; keep helper windows
hidden unless needed interactively. Verify new PID, bridge build/source hash
and plugin registration. Never force-kill to bypass a save prompt or claim a
running DLL changed just because its on-disk file did. Prefer normal shutdown
over loaded-file rename tricks.

Distinguish timeout states: cancelled-before-start, running, completed and
expired/unknown. Query execution status when available; never blindly retry
a mutation that may still run. A responsive ping with a main-thread timeout
does not prove a native deadlock. Bound script work/output; use files for bulky
artifacts. Do not log secrets or scene-scale locals. Wrap scripts in functions;
globals do not normally persist between bridge calls.

Screenshots need explicit paths, bounded dimensions and image inspection.
The bridge's viewport screenshot evaluates/renders and posts events. It cannot
certify an untouched frozen frame. Preserve artist/native screen observations
before probing timing bugs. If desktop automation is unavailable, say so; do
not substitute a render for an OS screenshot or bypass disabled UI controls.

If a test-owned document acquired artist QA edits, save them to a new checkpoint
and verify success before closing it. Restore the original document after tests;
only remove objects/documents the test allocated. Never delete artist objects
by a loose name search. Save scenes outside public fixture/release commits.
