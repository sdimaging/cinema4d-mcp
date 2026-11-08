# Debugging and acceptance

Preserve the failing state before restarting, nudging the camera, changing
controls or forcing evaluation. Separate committed parameter, generated/managed
geometry, evaluated cache and the actually presented viewport frame.

For linear fixtures compare finite point counts AND hashes. B-Spline control
points and tessellation differ: compare evaluated curves. Matching old buffers
do not prove a new parameter took effect. Preserve early asynchronous reads,
check bounded convergence, then stable idle. Read-only bridge requests still
wake C4D; use passive opt-in traces or actual artist/native screen evidence
first when timing is the bug.

Run first-pass and multi-pass gates. ExecutePasses can hide SceneHook ordering
defects. Actual typing, steppers, menus and object drops need direct acceptance;
API edits are a separate result. Locate the stale layer before adding dirtiness.

GVO/SceneHook/deformer callbacks may be threaded. Use immutable published
snapshots and safe main-thread loaded-hierarchy writes. Preflight pending work
before queuing a publisher and again before stopping threads: a no-op
StopAllThreads can cancel the draw with no replacement event. Preserve needed
ownership repair, cloning, Undo/Redo and manual-edit recovery. Never rewrite
C4D-owned caches or recursively evaluate from a draw/expression callback.

Test complete evaluated inputs: caps, multiple pieces, deformations, transforms,
and every consumer role. Source support does not prove collider support. Validate
topology before applying original map/selection indices to generated geometry.
Track child, tag/Field and cache dependencies; test edits without a second
control nudge. Test full segment crossings, not just points, for cutters/volumes.
Report arbitrary-mesh, interpolation and renderer limits explicitly.

Separate cold build, warm deformation, input evaluation, collision cost and
viewport presentation. ExecutePasses timing is not viewport FPS. Compare looks
against tracked references at shipped sampling density; invariants are not art
approval. Require outer execution success, assertions AND cleanup. Reacquire
objects after Undo/Redo. Run lifecycle last after scene-mutating QA and preserve
rejected raw results and DLL provenance. Bounded fixtures do not certify every
renderer, background context or long simulation.
