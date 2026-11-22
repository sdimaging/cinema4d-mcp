---
name: c4d-octane-headless
description: Build, light and render Octane scenes in Cinema 4D 2026 from Python (c4dpy or Script Manager): IDs, takes, OSL cameras, motion blur, calibration from Cycles, collecting for transfer.
---

# Octane for Cinema 4D from Python

Full ID tables and the numbered API gotchas live in Spenser's repo `sdimaging/cinema4d-mcp`: `docs/octane_c4d_headless_reference.md` and `docs/c4d_2026_api_gotchas.md` (#272–#283). Read those before writing parameters you haven't verified. Everything below was measured on C4D 2026.4 + Octane 2026.x (Nov 2026, NOCTERNA port).

## Running

- Headless: `"/Applications/Maxon Cinema 4D 2026/c4dpy.app/Contents/MacOS/c4dpy" /abs/script.py`, arguments via env vars, write a `DONE` marker (traceback on failure), poll it. 2026.4 runs beside the GUI and in parallel.
- Scripts are the source of truth (builder + verifier + comparison sheets). The live C4D (MCP) is for opening, probing, small tweaks on copies. Always check `doc.GetDocumentName()` and never edit the user's open scene.
- Get Octane IDs by parsing `plugins/Octane/res/**/*.h` (enums) plus a live `GetDescription()` dump; don't trust memory.

## Must-know behaviour

- Camera tag: write the **f-stop (1457) last**; writing aperture (1412) re-derives the f-stop from a 35 mm focal.
- Light tag texture slot is **1155**; an area light's UV is rotated 180° vs a mesh card.
- Every sky object with an Octane environment tag is live regardless of visibility: keep one primary + one visible env and override per take.
- Screen-space backplate: image texture + projection 12 on a visible-slot env tag.
- Masks on assembly nulls reach children. Glass that isn't a closed solid: thin wall. No clear coat on metals. Brushing in roughness/anisotropy, not bump.
- Linear output from `RenderDocument`: VP colour space 1028 = 1, HDR buffer 1010 = 1, imager on with linear response 5400, dithering off, 32-bit; saved EXRs carry an sRGB→linear curve.
- `RenderDocument` renders the whole range if the render data says "all frames": force current frame in loops. Set the take's camera on the render BaseDraw yourself.
- Takes: `FindOrAddOverrideParam` + `UpdateSceneNode`; works for visibility, PSR, shader params and user data.
- Tree walkers: use a true `subtree()`; a walker following `GetNext()` from its start also eats the siblings.

## OSL camera (type 3)

- Code in 1901; it compiles on the first `RenderDocument` (Compile button does nothing headless). Only then do inputs exist: floats 3300+ in declaration order under group 1715, named after the variable; drive them by id afterwards.
- Scene arrives in **metres** (mm scene → `UNIT = 0.001` on lens offsets). `getattribute("camera:dofrandom", float r[2])` and `"camera:resolution"` work. Frame: `fw = normalize(I)`, `up = normalize(N)`, `rt = normalize(cross(fw, up))`.
- It only bends rays. Anything that adds or tints light must be geometry. Real glass in front of the lens changed the lighting of the scene behind it (glare on the subject): put refractive filters in the OSL camera instead.
- Ready shaders (thin lens + window prism / smear / mist / star) and a lens kit: `Nocterna/c4d/tools/osl/`, `Nocterna/c4d/lens_kit/LENS_KIT.c4d`.

## Motion blur / shutter drag in one exposure

- Render setting 1013 = 2 (full), 1014 = time samples per frame (24–32); camera tag 1448 on, shutter type 1560 = 1 (frame length), length 1561, alignment 1451 (1 = Before: shutter ends on the frame; set the project start before 0 for frame 0).
- Objects need an Octane Object tag on themselves or a parent, or they render frozen.
- A Python tag never sees sub-frame time (`doc.GetTime()` stays whole) but keyed tracks do: drive procedural motion from a keyed looping "Clock" user-data track. A hold inside the shutter (travel for 40 %, sit still for 60 %) gives streaks + a crisp subject in one render.

## Cycles → Octane calibration (linear, same scene)

Emission ×1.43; area lamp P/(4πA) × 6.8 × (1 + 3.2/tan²(spread/2)) (A in mm²); point lamp P/(4π³r²) × 7.4; roughness 1:1 (GGX energy preserving); anisotropy opposite sign; imager exposure 2^stops with ACES + sRGB response; portrait sensor = 36 × w/h; Blender (x,y,z) mm → C4D (x,z,y).

## Handoff

Collect with `c4d.documents.SaveProject(doc, SAVEPROJECT_ASSETS | SAVEPROJECT_SCENEFILE | SAVEPROJECT_DONTTOUCHDOCUMENT, folder, assets, missing)`: copies every texture (Octane included) into `tex/` with relative paths. Verify by reloading.
