# Octane for Cinema 4D from Python: headless reference

Measured on Cinema 4D 2026.4 with the Octane plugin 2026.x, driven from `c4dpy` and the Script Manager, while porting the NOCTERNA watch (Blender 5.2 / Cycles) to Octane in November 2026. Everything here was checked by rendering, not taken from docs. Numbered C4D API gotchas found on the way are in `c4d_2026_api_gotchas.md` (#272–#283; earlier Octane entries: #254 OSL Texture compile, #266 per-polygon colour / masks / Live Viewer). For OSL *texture* shaders (time, projection, mapping) see [`octane_osl_practical_notes.md`](octane_osl_practical_notes.md).

## 1. Parameter IDs: read them from the plugin, don't hard-code from memory

The Octane plugin ships its description headers in `/Applications/Maxon Cinema 4D 2026/plugins/Octane/res/**/*.h`. Parse those (`enum { NAME = 1234, ... }`) into a dict per resource file and look names up; that survives plugin updates. A live dump (`GetDescription()` on a fresh tag/material, writing id, name, ident, dtype, default and the cycle options) is the second source of truth. Both together answered every question below.

Plugin / object IDs: Octane material 1029501, render videopost 1029525, object tag 1029603, camera tag 1029524, light tag 1029526, environment tag 1029643, image texture 1029508, RGB spectrum 1029504, float texture 1029506, texture emission 1029642, round edges 1053013, spotlight distribution 1056196, texture projection 1031460, multiply 1029516, mix material 1029622, OSL texture 1039813.

## 2. Materials (Universal, type 2509 = 2516)

| what | id |
|---|---|
| material type | 2509 (2510 diffuse, 2513 specular, 2516 universal) |
| BRDF model | 2585 (6 = GGX energy preserving) |
| albedo colour / link | 2515 / 2517 |
| metallic float / link | 2613 / 2587 |
| roughness float / link | 2532 / 2533 |
| anisotropy | 2590 |
| coating float / link / roughness / IOR | 2620 / 2615 / 2616 / 2617 |
| sheen float / link / roughness | 2624 / 2605 / 2611 |
| index (IOR) / dielectric IOR | 2551 / 2612 |
| transmission colour / float / type | 2554 / 2556 / 2632 (0 specular, 2 thin wall) |
| emission link | 2557 |
| normal link / bump link | 2542 / 2539 |
| round-edges link | 2582 |
| fake shadows | 2562 |
| thin wall | 2628 |
| reflection colour (specular type) | 2526 |
| dispersion coefficient B | 2552 |
| mix material: texture 1 / 2 / amount | 1271 / 1272 / 1277 |

- A glass pane that is not a closed solid renders black as solid glass: set thin wall.
- A clear coat on a metal reads silvery in Octane where Cycles' coat did not: leave coat 0 on metals.
- Brushing on metals: put the brushed noise in roughness (and anisotropy), not in bump. UV-projected bump pinches where UVs converge and shows as dimples.

## 3. Textures

Image texture: file 1100, legacy gamma 1102, power 1109, projection link 1111, colour space 1118 (0 non-colour data, 1 sRGB, 2 linear sRGB), border mode U 1112 / V 1122 (3 = clamp). Masks and data maps must be colour space 0. Texture emission (1029642): texture/colour link 2170, power 2171, surface brightness 2175 (on for "glow" panels). Texture projection 1360: 2 = mesh UV, 12 = "Sample position to UV" (screen space).

**Screen-space backplate:** image texture with a projection shader set to 12, on an environment tag in the *visible* slot: the image maps to the film plane exactly.

## 4. Lights (light tag 1029526 on a C4D area light)

| what | id |
|---|---|
| light type select | 1159 (1 = texture emission) |
| power | 1151 |
| **texture / colour slot** | **1155** (1150 is something else) |
| distribution (e.g. spotlight distribution) | 1154 |
| surface brightness | 1166 |
| camera visibility / shadow visibility | 1163 / 1164 |
| light pass ID | 1165 |
| double sided | 1172 |
| motion data | 1188 |

- An area light's UV is rotated 180° from a mesh card's: a falloff texture lands mirrored. Negate the light matrix's v1 and v2 (or rotate the texture).
- Spotlight distribution: orientation surface normal, cone angle, hardness 0 for a soft spread.

## 5. Object tag (1029603)

Light mask: use light mask 1326 (1327 enable / 1328 disable), sun 1329, environment 1330, light IDs 1–8 = 1331–1338, 9–20 = 1345–1356. General visibility 1282, camera visibility 1283, shadow visibility 1284, motion data 1285 (0 off, 1 transform, 2 transform + vertex, 3 vertex speed), force updates 1307 (needed for Python generators in the Live Viewer).

- Masks on an assembly null reach every child.
- A mask with none of the IDs ticked keeps scene lights off an object (it is still lit by what it reflects): handy for "only reflects" objects like lens-filter flakes.

## 6. Environment tag (1029643)

Texture 1150, power 1151, slot 1162 (0 primary, 1 visible / backplate), backplate 1159, visible in reflections 1160, refractions 1161.

**Sky objects with an Octane environment tag are live whatever the object's visibility.** Several skies in one scene (one per "shot") all contribute. Keep exactly one primary + one visible environment and override their texture/colour/power per take.

## 7. Camera tag (1029524)

| what | id |
|---|---|
| camera type | 1439 (0 thin lens, 1 panoramic, 2 baking, 3 OSL, 4 OSL baking, 5 universal, 6 realistic lens) |
| aperture (cm) / **f-stop** | 1412 / **1457** |
| autofocus | 1419 |
| imager enable / exposure / gamma / response | 1423 / 1424 / 1427 / 1428 (5400 linear, 5401 sRGB) |
| neutral response / dithering | 1469 / 1434 |
| ACES tone mapping | 1558 |
| denoiser enable | 1482 |
| motion blur enable / shutter type / frame length / alignment | 1448 / 1560 / 1561 / 1451 |
| OSL code / auto compile / log / parameters group | 1901 / 1905 / 1903 / 1715 |
| universal camera: use aperture texture / aperture texture | 1506 / 1507 |

- **Write the f-stop last.** Writing aperture (1412) re-derives the f-stop from a 35 mm focal length, so a later aperture write turns f/13 into f/3.
- Portrait frames: C4D fits the sensor to the width, Blender to the long side. Sensor width = 36 × w / h for a portrait port.
- Octane focuses on a plane at the focus distance along the view axis.

## 8. Render settings (videopost 1029525)

Kernel 3001 (2 = path tracing), max samples 3063, diffuse depth 3060, specular depth 3069, GI clamp 3070 (these store LONGs), adaptive sampling 3130, buffer type 1010 (1 = HDR float), colour space 1028 (1 = linear sRGB), use denoised beauty 1036, motion blur 1013 (0 off, 1 camera, 2 full), time sampling per frame 1014.

- **Scene-linear output from `RenderDocument`:** colour space 1028 = 1, buffer 1010 = 1, camera imager enabled with response linear (5400) and dithering off, `RDATA_FORMATDEPTH_32`, bitmap `COLORMODE_RGBf` with a linear profile. With the imager disabled you get the default sRGB response instead. An EXR saved from that bitmap still carries an sRGB→linear curve: undo it when you read it.
- Lens-filter flakes and other tiny mirrors throw back highlights far brighter than white: raise GI clamp (e.g. 1000) or they disappear.

## 9. OSL camera (camera type 3)

The code goes into 1901. It compiles on the first `RenderDocument` (gotcha #275); after that the inputs exist as parameters 3300+ under group 1715.

What the shader sees (measured with probe shaders):
- Output: `shader OslCamera(..., output point pos = P, output vector dir = 0, output float tMax = 1.0/0.0)`. `dir = 0` kills the ray (black pixel).
- `P` is the camera position, `I` the forward axis, `N` the up axis; `u`, `v` the film position 0..1. Build the frame as `fw = normalize(I)`, `up = normalize(N)`, `rt = normalize(cross(fw, up))`; that gives an un-mirrored image whose framing matches the thin-lens camera.
- **The scene arrives in metres**: a C4D scene in mm needs `UNIT = 0.001` on every lens-plane offset (`pos = P + UNIT * (lx * rt + ly * up)`).
- `getattribute("camera:resolution", int res[2])` works; `getattribute("camera:dofrandom", float r[2])` gives the two aperture random numbers, so a thin lens with real depth of field can be rebuilt (aperture point from `sqrt(r[0])`, `2π r[1]`, aim at the same point on the focus plane), and filters can be layered on top (exact refraction through facets with Snell, scatter, star streaks).
- An OSL camera bends rays only: it cannot add or tint light. Anything that must add light (glitter, reflections) has to be geometry in front of the lens.

**Real glass in front of the lens changes the scene behind it.** A ribbed glass plate or a thin-wall diffusion disc parented in front of the camera made a violet glare appear on the watch crystal that the direct view did not have; it looks like light masks / camera-invisible lights stop being respected behind a refraction (not confirmed). The same filter inside the OSL camera left the scene untouched. Geometry that only reflects (flakes) did not cause it.

## 10. Calibration Cycles → Octane (same scene, both renderers, linear comparison)

- Emission (surface brightness) = 1.43 × Blender emission strength.
- Area lamp: Octane power = P / (4π A) × 6.8 × (1 + 3.2 / tan²(spread / 2)), A in mm² (Cycles keeps a lamp's total power when its spread narrows, Octane's spotlight distribution just masks it).
- Point lamp: P / (4π³ r²) × 7.4.
- Roughness 1:1 with GGX energy preserving (checked at 0.15 / 0.30 / 0.50).
- Anisotropy has the opposite sign (and flips again when the Blender aniso rotation is 0.25).
- Imager: exposure 2^stops with ACES tone mapping and sRGB response.
- Space: Blender (x, y, z) mm → C4D (x, z, y), 1 unit = 1 mm, if the FBX root's axis conversion is undone.

## 11. Workflow that held up

- Scripts are the source of truth: one builder that makes the whole scene from exported data in seconds, one verifier that renders through every take, and comparison sheets against the original renderer. The live C4D session (MCP) is for opening, probing and small tweaks on copies.
- Shader balls: render every material on the same ball rig in both renderers before touching the shots; it separates material errors from light errors.
- `c4dpy` jobs launched in the background with a `DONE` marker and a log; poll the marker.
- Collect with "Save Project with Assets" (gotcha #282) for every machine-to-machine handoff.
