# Octane OSL: animation, mapping and caustic texture notes

Recorded 2026-10-15 during review of the user's
`Projects/OctaneOSL/CausticNoise/CausticNoise.osl` (initially 119 lines;
concurrent mapping revisions grew it to 126 and then 135 lines).

## Evidence and limits

- **Documented by OTOY:** the Octane input, projection and language rules below.
- **Checked numerically:** a CPU float64 mirror of the reviewed shader's formulas,
  parameter extremes, search neighborhood and UV boundary repeat.
  Reproducer and raw results: [evidence folder](evidence/octane_osl_caustic_2026-10-15/).
- **Existing external evidence:** that shader's folder includes a Blender/Cycles
  OSL compiler check, an `.oso`, `blender_t0.png` and a Python motion preview.
  These establish a separate OSL/preview path, not Octane GPU acceptance.
- **Checked natively in C4D afterwards (same day, through the MCP bridge):** the
  shader loaded from file into Octane's OSL texture node (plugin id 1039813) on an
  area light's Distribution, compiled ("Compilation OK"), and exposed the pins
  listed under "How C4D shows OSL inputs" below.
- **Still not established:** an inspected Octane GPU render, animated light
  projection, C4D keyframe UI behavior or GPU temporal filtering.
  During the review itself the MCP connector returned "Not connected to Cinema 4D"
  while C4D was running; do not confuse connector state with application state.

Keep these evidence classes separate in handoffs. A CPU preview, stock OSL
compile and native Octane render are different checks.

## Octane's global time is a subframe value

OTOY documents global `time` as a value in 0..1 within a subframe, not the
absolute scene timeline. Expose an explicit float input such as `Time`, and
drive that input with keyframes or an established host time connection. For
constant-speed evolution, use linear interpolation on the Time animation.

At Speed 1, Time 0 at frame 0 and Time 10 at frame 300 describes ten seconds
at 30 fps. Motion blur/subframe interpolation must still be tested in the
native host; exposing Time alone does not establish motion-blur support.

Source: [Octane features and limitations](https://docs.otoy.com/osl/features/).

## Explicitly expose projection and transform inputs

The initial snapshot read only `P[0]` and `P[1]`. The shader now takes
`point Projection = point(u, v, 0)` plus its own Scale, Rotate Z and Offset
inputs (the inverse of Octane's T·R·S, so an Image Texture's transform values
carry over: Scale 0.2 gives a pattern 5x smaller). The general documented
mapping rule follows.

Reading only `P[0]` and `P[1]` does not expose the documented Octane
Projection and UVW Transform input pins. Do not describe such a shader as
having user-selectable UV mapping by default. A 2D slice of positional
coordinates can also collapse on a surface whose selected coordinate is
constant.

For a 2D texture with normal Octane image-style mapping, OTOY documents:

```osl
point Projection = point(u, v, 0)
    [[string variant = "image", string inputType = "projection"]],
matrix UVWTransform = 1
    [[string type = "uvw", int dim = 2]],
```

Inside the shader, the centered inverse-transform convention is:

```osl
point q = transform(1 / UVWTransform,
                    Projection - vector(0.5, 0.5, 0))
          + vector(0.5, 0.5, 0);
```

Use `q[0]` and `q[1]` for the 2D field.

### How C4D shows OSL inputs (checked natively)

Reading the compiled node's description in C4D:

| OSL input type | What C4D shows | Parameter ID |
|---|---|---|
| `float` | value field | 3300, 3301, … in declaration order; **16 slots (3300–3315)** |
| `int` | value field / checkbox | 3100, … |
| `color` | **link pin** (shader input), not a colour swatch | 2000, … |
| `point` (Projection, label-only metadata) | link pin: wire a Projection node into it | 3700 |
| `vector` | value field | 3400, … |
| `matrix` with only a `label` | **nothing**: no pin or field appears | — |

So a matrix declared with only a label can't be driven from C4D. OTOY's
documented `[[string type = "uvw", int dim = 2]]` annotation, shown above, has
**not** been tested in C4D yet; it may expose a transform pin. Until that's
checked, give the shader explicit scale/rotate/offset inputs.

Two more host behaviours:
- Parameter values are stored by slot, not by name. When code is swapped into an
  existing node, each slot keeps its old value, so a parameter can show another
  parameter's stale number (for example Brightness 0). Append new floats after
  the existing ones, and reset values after reordering.
- When the node loads code from a file, the path is stored in 1907 and 1898 and
  the source field 1901 stays empty. The Compile button (3709) re-reads the file
  and can be pressed with `c4d.CallButton(node, 3709)`.

Sources: [input types](https://docs.otoy.com/osl/input%20types/),
[projections](https://docs.otoy.com/osl/projections/).

## Do not generalize a constant noise probe to all Octane noise

The reviewed shader and README claim that Octane's built-in `noise()`
returns a constant. No native probe output supporting that blanket claim
was present in the reviewed shader folder.

OTOY documents ordinary noise/texturing support, with restrictions including
no 4D noise and no built-in simplex/gabor noise. Its extensions list
Perlin and several cellular noise types. A constant result needs its exact
coordinates, overload, evaluation path, Octane version and native render
recorded before assigning a cause. A fixed input coordinate can itself
produce a fixed noise sample.

Sources: [features and limitations](https://docs.otoy.com/osl/features/),
[noise types](https://docs.otoy.com/osl/extras/#noise-types).

Other documented restrictions relevant here: one output for a texture node,
no material closures, no structs and no renderer derivative functions.
Analytically differentiating a wave expression with arithmetic is different
from calling an unsupported derivative built-in.

## CausticNoise review: input ranges reveal energy bugs

Both energy bugs below were fixed in the shader after the review (clamp the
variation modulator at 0; clamp each layer at 4 before the blend). At default
settings a Blender/Cycles OSL render of the fixed shader is pixel-identical to
the previous one (PSNR infinite), so the default look is unchanged.

### Filament Variation becomes negative

The `vary` expression uses `(1-Variation) + Variation*sin(...)`.
Its mathematical minimum
is `1-2*Variation`, so values above 0.5 can make filament energy negative.
The slider allows 1.0. In the 512-square CPU sample, Variation 1 made the
modulator negative at 50.648% of points; a sampled layer became negative and
the final output clamped to black.

Remap the sine into 0..1 before mixing, or clamp the modulator explicitly.
Remapping changes the default look and should be retuned deliberately.

### The screen-like blend reverses at high brightness

The `web` expression uses `a+b-a*b/4` with unbounded layer values. Its derivative with
respect to `a` is `1-b/4`: once b exceeds 4, increasing a darkens the result.
For a=b=10 the blend is -5.

Within the exposed ranges (Variation=0, Hotspots=10, SecondLayer=1,
EdgeWidth=0.5), the CPU sample found 7.302% negative blended pixels.
One actual sampled pair was a=11.2596, b=11.3124, producing web=-9.2712
and a black output after the final clamp. Add nonnegative energy and use
an explicit tone rolloff, or bound both inputs consistently before a screen
blend. A later clamp does not fix this reversal.

### A 3x3 search can miss the third-nearest feature

Hotspots use the third-nearest distance, not just F2-F1. Comparing 3x3 and
5x5 searches in four 40,000-point CPU cases found third-distance differences
in 0.0075%..0.055% of samples, with a maximum gap error of 0.12592.
At defaults, Time 0, x=0.90431624 and y=0.41625987, the junction gap was
0.86958920 in 3x3 and 0.74366751 in 5x5. The nearest-edge gap was unchanged.

This is a demonstrated search approximation, not demonstrated visible
animation popping in Octane. Widen the neighborhood if exact F3 behavior
is required, and measure its GPU cost before recommending it as free.

### Positive results

The independent moving feature centers and slowly evolving domain warp
produce deformation rather than a simple texture translation. At defaults,
the CPU boundary repeat error was 1.29e-14 and mean image change over a
Time step of 0.001 was 0.00025669 on a 0..1 scale. These are useful local
checks, not a global proof of no popping over all seeds and times.

The sine hash is precision sensitive: CPU float64 preview values need not
match OSL/GPU float32 feature positions exactly. Do not use a prototype's
measured percentile as a universal exposure calibration across backends.

## Visual direction

The reviewed shader is explicitly a two-layer warped cellular F2-F1 web,
with an F3-F1 junction boost. Its existing default render has relatively
uniform, fine intersecting lines. The supplied caustic reference has more
uneven ribbon widths, darker open interiors and localized broad hot spots.
Consider stronger width/energy variation and less uniform haze; alternatively
investigate wave-focusing/fold fields for a different pattern character.
Those are art-direction suggestions, not native-render findings.

## Native acceptance still needed

Compile and exposed pins are now verified in C4D (above). Still to verify in
the installed Octane build: the light projection as rendered, the
`type = "uvw"` matrix annotation, animated Time at subframes, parameter
extremes and a short actual render sequence. Preserve artist documents and
use an owned test scene. Existing plugin-ID guidance remains in gotcha 17;
discover the registered shader ID rather than using a description-resource ID.
