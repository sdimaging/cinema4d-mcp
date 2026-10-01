#!/usr/bin/env python3
"""
GlyphGrid plate generator: N x N atlases that match glyphgrid_core cell order
(cell 0 = top-left, left->right, top->bottom).

Plate kinds
  ascii     characters sorted by ink coverage (sparse -> dense) = brightness ramp
  chars     your own string, in the order given (or --sort for ink order)
  bayer     ordered dither levels (4x4 Bayer => 16 exact levels, perfect for 16up)
  halftone  dot-size ramp (round / square / diamond / line)
  logo      one or more images fitted into cells (optional tint / invert)
  noise     blue-ish noise threshold levels (stochastic dither ramp)

Sequences (looping image sequences for the "plates change over time" look)
  --frames F                write plate_0000.png ... plate_F-1.png
  --shimmer                 every frame re-picks each cell's glyph from the
                            characters of *similar ink density*, so a value-driven
                            mapping keeps its tone while the glyphs boil
  --flipbook                also pack all F frames into ONE atlas
                            (ceil(sqrt F) x ceil(sqrt F) tiles of N x N cells),
                            the layout glyphgrid_atlas.osl reads for per-polygon
                            time offsets

Examples
  python glyphgrid_plates.py ascii --grid 4 --out plates/ascii16.png
  python glyphgrid_plates.py ascii --grid 4 --frames 12 --shimmer --flipbook --out plates/ascii16_seq.png
  python glyphgrid_plates.py bayer --grid 4 --dots 8 --out plates/bayer16.png
  python glyphgrid_plates.py halftone --grid 3 --shape round --out plates/dots9.png
  python glyphgrid_plates.py chars --grid 4 --text "SDIMAGING0123456" --out plates/brand16.png
  python glyphgrid_plates.py logo --grid 2 --images a.png b.png c.png d.png --out plates/logos4.png
  python glyphgrid_plates.py sheet --out plates/contact.png  (all kinds, 1-4 up)
"""

import argparse
import math
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

DEFAULT_RAMP = " .'`^,:;-~_\"!il|/\\()1{}[]?+<>tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"

FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/Library/Fonts/Courier New Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consolab.ttf",
]


def find_font(path=None):
    for p in ([path] if path else []) + FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def load_font(path, size):
    p = find_font(path)
    return ImageFont.truetype(p, size) if p else ImageFont.load_default()


# ------------------------------------------------------------- glyph cells ---
def render_glyph(ch, cell, font_path=None, fill=0.86, fg=255, bg=0, bold=0):
    """Render one character centred in a square cell, scaled to `fill` of it."""
    big = cell * 4
    font = load_font(font_path, int(big * 0.9))
    img = Image.new("L", (big, big), bg)
    if ch.strip():
        def tight(c):
            lay = Image.new("L", (big * 2, big * 2), 0)
            ImageDraw.Draw(lay).text((big // 2, big // 2), c, font=font, fill=255,
                                     stroke_width=bold, stroke_fill=255)
            return lay, lay.getbbox()
        layer, bb = tight(ch)
        if bb:
            glyph = layer.crop(bb)
            # one scale for every glyph (cap height of "M") so '.' stays small and 'B' stays big
            _, rb = tight("M")
            refh = rb[3] - rb[1]
            s = (fill * big) / max(refh, glyph.size[0], glyph.size[1], 1)
            glyph = glyph.resize((max(1, int(glyph.size[0] * s)), max(1, int(glyph.size[1] * s))), Image.LANCZOS)
            gx, gy = glyph.size
            img.paste(Image.new("L", glyph.size, fg), ((big - gx) // 2, (big - gy) // 2), glyph)
    return img.resize((cell, cell), Image.LANCZOS)


def ink(img):
    h = img.histogram()
    return sum(i * c for i, c in enumerate(h)) / (255.0 * img.size[0] * img.size[1])


def ink_sorted(chars, font_path=None, probe=48, bold=0):
    uniq = []
    for c in chars:
        if c not in uniq:
            uniq.append(c)
    scored = [(ink(render_glyph(c, probe, font_path, bold=bold)), c) for c in uniq]
    scored.sort()
    return scored


def pick_ramp(scored, n):
    """n characters spread evenly across the ink range (always includes ends)."""
    if n == 1:
        return [scored[len(scored) // 2][1]]
    lo, hi = scored[0][0], scored[-1][0]
    out, used = [], set()
    for k in range(n):
        target = lo + (hi - lo) * k / (n - 1)
        best = min((abs(s - target), c) for s, c in scored if c not in used)
        out.append(best[1]); used.add(best[1])
    out.sort(key=lambda c: dict((c2, s) for s, c2 in scored)[c])
    return out


def density_neighbours(scored, ch, k=4):
    s0 = dict((c, s) for s, c in scored)[ch]
    near = sorted(scored, key=lambda sc: abs(sc[0] - s0))[:k]
    return [c for _, c in near]


# --------------------------------------------------------------- patterns ---
def bayer_matrix(n):
    m = [[0]]
    while len(m) < n:
        s = len(m)
        m = [[4 * m[y % s][x % s] + [[0, 2], [3, 1]][y // s][x // s] for x in range(2 * s)] for y in range(2 * s)]
    return m


def bayer_cell(level, levels, cell, dots=4, fg=255, bg=0, round_dots=True):
    """Level k of `levels`: a dots x dots ordered-dither tile drawn as crisp dots."""
    mtx = bayer_matrix(max(2, 1 << math.ceil(math.log2(max(2, math.isqrt(levels - 1) + 1)))))
    size = len(mtx)
    thr = level / max(1, levels - 1)
    img = Image.new("L", (cell * 4, cell * 4), bg)
    d = ImageDraw.Draw(img)
    step = cell * 4 / dots
    for y in range(dots):
        for x in range(dots):
            t = (mtx[y % size][x % size] + 0.5) / (size * size)
            if t < thr:
                x0, y0 = x * step, y * step
                if round_dots:
                    pad = step * 0.08
                    d.ellipse([x0 + pad, y0 + pad, x0 + step - pad, y0 + step - pad], fill=fg)
                else:
                    d.rectangle([x0, y0, x0 + step - 1, y0 + step - 1], fill=fg)
    return img.resize((cell, cell), Image.LANCZOS)


def halftone_cell(level, levels, cell, shape="round", fg=255, bg=0, grid=1):
    v = level / max(1, levels - 1)
    big = cell * 4
    img = Image.new("L", (big, big), bg)
    d = ImageDraw.Draw(img)
    sub = big / grid
    for gy in range(grid):
        for gx in range(grid):
            cx, cy = (gx + 0.5) * sub, (gy + 0.5) * sub
            if shape == "line":
                h = sub * v
                d.rectangle([gx * sub, cy - h / 2, (gx + 1) * sub, cy + h / 2], fill=fg)
                continue
            if shape == "square":
                r = sub * 0.5 * math.sqrt(v)
                d.rectangle([cx - r, cy - r, cx + r, cy + r], fill=fg)
            elif shape == "diamond":
                r = sub * 0.5 * math.sqrt(2 * v) if v < 0.5 else sub * 0.5 * (2 - math.sqrt(2 * (1 - v)))
                r = min(r, sub)
                d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=fg)
            else:  # round: area-proportional radius, overlaps into the corners near 1.0
                r = sub * math.sqrt(v / math.pi) * 1.13
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fg)
    return img.resize((cell, cell), Image.LANCZOS)


def noise_cell(level, levels, cell, seed=7, fg=255, bg=0, res=24):
    rng = random.Random(seed)
    thr = level / max(1, levels - 1)
    vals = list(range(res * res))
    rng.shuffle(vals)
    img = Image.new("L", (res, res), bg)
    px = img.load()
    for i, v in enumerate(vals):
        if (v + 0.5) / (res * res) < thr:
            px[i % res, i // res] = fg
    return img.resize((cell, cell), Image.NEAREST)


def logo_cell(path, cell, fill=0.86, invert=False, mono=True):
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    im = Image.alpha_composite(bg, im)
    im = im.convert("L") if mono else im.convert("RGB")
    if invert:
        im = ImageOps.invert(im)
    im.thumbnail((int(cell * fill), int(cell * fill)), Image.LANCZOS)
    out = Image.new(im.mode, (cell, cell), 0)
    out.paste(im, ((cell - im.size[0]) // 2, (cell - im.size[1]) // 2))
    return out.convert("L") if mono else out


# ------------------------------------------------------------------ atlas ---
def assemble(cells, grid, cell, guides=False, color=None):
    W = grid * cell
    mode = "RGB" if color else "L"
    atlas = Image.new(mode, (W, W), 0)
    for i, im in enumerate(cells[: grid * grid]):
        if color:
            im = ImageOps.colorize(im.convert("L"), (0, 0, 0), color)
        atlas.paste(im, ((i % grid) * cell, (i // grid) * cell))
    if guides:
        d = ImageDraw.Draw(atlas)
        for k in range(1, grid):
            d.line([(k * cell, 0), (k * cell, W)], fill=(60, 60, 60) if color else 60)
            d.line([(0, k * cell), (W, k * cell)], fill=(60, 60, 60) if color else 60)
    return atlas


def flipbook(frames):
    F = len(frames)
    c = math.ceil(math.sqrt(F))
    r = math.ceil(F / c)
    w, h = frames[0].size
    out = Image.new(frames[0].mode, (c * w, r * h), 0)
    for i, f in enumerate(frames):
        out.paste(f, ((i % c) * w, (i // c) * h))
    return out, c, r


def make_cells(kind, grid, cell, a, frame=0, scored=None, base=None):
    n = grid * grid
    if n == 1 and kind in ("bayer", "halftone", "noise"):
        # 1up: a single mid-tone tile instead of the (empty) first level
        mk = {"bayer": lambda: bayer_cell(1, 3, cell, a.dots, round_dots=a.shape != "square"),
              "halftone": lambda: halftone_cell(1, 3, cell, a.shape, grid=max(1, a.dots_per_cell)),
              "noise": lambda: noise_cell(1, 3, cell, seed=a.seed + frame)}
        return [mk[kind]()]
    if kind in ("ascii", "chars"):
        chars = base
        if a.shimmer and frame > 0:
            rng = random.Random(a.seed * 1000 + frame)
            chars = [rng.choice(density_neighbours(scored, ch, a.shimmer_k)) if ch.strip() else ch for ch in base]
        return [render_glyph(ch, cell, a.font, a.fill, bold=a.bold) for ch in chars]
    if kind == "bayer":
        return [bayer_cell(k, n, cell, a.dots, round_dots=a.shape != "square") for k in range(n)]
    if kind == "halftone":
        return [halftone_cell(k, n, cell, a.shape, grid=a.dots_per_cell) for k in range(n)]
    if kind == "noise":
        return [noise_cell(k, n, cell, seed=a.seed + frame) for k in range(n)]
    if kind == "logo":
        imgs = a.images or []
        return [logo_cell(imgs[i % len(imgs)], cell, a.fill, a.invert) for i in range(n)] if imgs else []
    raise ValueError(kind)


def build(kind, grid, a):
    cell = a.size // grid
    scored, base = None, None
    if kind == "ascii":
        scored = ink_sorted(a.ramp or DEFAULT_RAMP, a.font, bold=a.bold)
        base = pick_ramp(scored, grid * grid)
    elif kind == "chars":
        text = (a.text or "0123456789ABCDEF")
        scored = ink_sorted(DEFAULT_RAMP + text, a.font, bold=a.bold)
        base = [text[i % len(text)] for i in range(grid * grid)]
        if a.sort:
            sc = dict((c, s) for s, c in scored)
            base.sort(key=lambda c: sc[c])
    frames = []
    for f in range(max(1, a.frames)):
        cells = make_cells(kind, grid, cell, a, f, scored, base)
        frames.append(assemble(cells, grid, cell, a.guides, a.color))
    return frames, base


def parse_color(s):
    if not s:
        return None
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["ascii", "chars", "bayer", "halftone", "noise", "logo", "sheet", "library"])
    ap.add_argument("--grid", type=int, default=4, help="N -> N*N cells (2=4up, 3=9up, 4=16up)")
    ap.add_argument("--size", type=int, default=2048, help="atlas size in px")
    ap.add_argument("--out", default="plate.png")
    ap.add_argument("--font", default=None)
    ap.add_argument("--ramp", default=None, help="characters to choose the ASCII ramp from")
    ap.add_argument("--text", default=None, help="chars kind: the characters, cell order")
    ap.add_argument("--sort", action="store_true", help="chars kind: sort by ink density")
    ap.add_argument("--fill", type=float, default=0.86)
    ap.add_argument("--bold", type=int, default=0, help="stroke width (px at 4x) to embolden")
    ap.add_argument("--dots", type=int, default=4, help="bayer: dots per cell side")
    ap.add_argument("--dots-per-cell", type=int, default=1, help="halftone: dot grid per cell")
    ap.add_argument("--shape", default="round", choices=["round", "square", "diamond", "line"])
    ap.add_argument("--images", nargs="*")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--color", default=None, help="hex tint e.g. ff3355 (default white on black)")
    ap.add_argument("--guides", action="store_true", help="draw cell borders (preview only)")
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--shimmer", action="store_true")
    ap.add_argument("--shimmer-k", type=int, default=4)
    ap.add_argument("--flipbook", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    a.color = parse_color(a.color)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    root, ext = os.path.splitext(a.out)
    ext = ext or ".png"

    if a.kind == "sheet":
        return contact_sheet(a, root + ext)
    if a.kind == "library":
        return library(a, a.out if os.path.splitext(a.out)[1] == "" else os.path.dirname(a.out))

    frames, base = build(a.kind, a.grid, a)
    if len(frames) == 1:
        frames[0].save(root + ext)
        print("wrote", root + ext, ("ramp: %r" % "".join(base)) if base else "")
    else:
        for i, f in enumerate(frames):
            f.save("%s_%04d%s" % (root, i, ext))
        print("wrote %d frames %s_####%s" % (len(frames), root, ext))
        if a.flipbook:
            fb, c, r = flipbook(frames)
            fb.save("%s_flipbook_%dx%d%s" % (root, c, r, ext))
            print("wrote flipbook %dx%d -> %s_flipbook_%dx%d%s" % (c, r, root, c, r, ext))


LIBRARY_STYLES = [  # (file key, kind, overrides) -- the GlyphGrid generator's "Plate Style" list
    ("ascii", "ascii", {}),
    ("bayer", "bayer", {"dots": 8}),
    ("dots", "halftone", {"shape": "round"}),
    ("squares", "halftone", {"shape": "square"}),
    ("noise", "noise", {}),
    ("hex", "chars", {"text": "0123456789ABCDEF", "sort": True}),
    ("binary", "chars", {"text": "01", "sort": False}),
]


def library(a, folder):
    """Every style x 1/4/9/16-up as <style>_<cells>up.png, the layout the generator swaps between."""
    os.makedirs(folder, exist_ok=True)
    for key, kind, ov in LIBRARY_STYLES:
        for g in (1, 2, 3, 4):
            b = argparse.Namespace(**vars(a))
            b.frames, b.guides, b.shimmer = 1, False, False
            for k, v in ov.items():
                setattr(b, k, v)
            fr, base = build(kind, g, b)
            path = os.path.join(folder, "%s_%dup.png" % (key, g * g))
            fr[0].save(path)
            print("wrote", path, ("".join(base) if base else ""))


def contact_sheet(a, path):
    """Rows = Plate Styles (same order as the generator), columns = 1 / 4 / 9 / 16-up."""
    tile, pad, lab = 300, 20, 120
    rows = LIBRARY_STYLES
    sheet = Image.new("L", (lab + pad + 4 * (tile + pad), pad + len(rows) * (tile + pad) + 40), 18)
    d = ImageDraw.Draw(sheet)
    font = load_font(None, 26)
    for g in range(1, 5):
        d.text((lab + pad + (g - 1) * (tile + pad) + tile // 2 - 30, 8), "%d-up" % (g * g), fill=200, font=font)
    for r, (key, kind, ov) in enumerate(rows):
        y = 40 + pad + r * (tile + pad)
        d.text((10, y + tile // 2 - 14), "%d %s" % (r + 1, key), fill=200, font=font)
        for g in range(1, 5):
            b = argparse.Namespace(**vars(a))
            b.size, b.frames, b.guides, b.shimmer = tile, 1, True, False
            for k, v in ov.items():
                setattr(b, k, v)
            fr, _ = build(kind, g, b)
            sheet.paste(fr[0].convert("L"), (lab + pad + (g - 1) * (tile + pad), y))
    sheet.save(path)
    print("wrote", path)


if __name__ == "__main__":
    sys.exit(main())
