"""Regenerates res/icons/glyphit.png (the Glyph It object icon: dot-grid G, lime -> teal)."""
import os
from PIL import Image, ImageDraw, ImageFont
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
C0, C1 = (200, 255, 80), (40, 200, 190)        # lime -> teal
BG = (26, 27, 32)
def lerp(a, b, t): return tuple(int(x + (y - x) * t) for x, y in zip(a, b))

def dot_g(size, n=12, plate=True, min_r=0.18, max_r=0.48, bg=BG, pad=0.055):
    """Dot-grid G. For small sizes the dots are fatter (min_r up) so they don't vanish."""
    SS = 4; S = size * SS
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    if plate:
        d.rounded_rectangle((pad * S, pad * S, S - pad * S, S - pad * S), int(S * 0.19), fill=bg)
    mask = Image.new("L", (S, S), 0); md = ImageDraw.Draw(mask)
    md.text((S * 0.492, S * 0.523), "G", font=ImageFont.truetype(BOLD, int(S * 0.78)), anchor="mm", fill=255)
    x0 = S * 0.109; cell = S * 0.781 / n
    for j in range(n):
        for i in range(n):
            cx, cy = x0 + (i + 0.5) * cell, x0 + (j + 0.5) * cell
            if mask.getpixel((int(cx), int(cy))) < 128:
                continue
            t = (i + j) / (2 * n - 2)
            rad = cell * (min_r + (max_r - min_r) * t)
            d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=lerp(C0, C1, t))
    return im.resize((size, size), Image.LANCZOS)

# app / marketing icon
# C4D object icons: 64 + 32, fatter dots, no plate (C4D draws its own background) and with plate
# C4D icon file used by the plugin (64 px, with plate)
dot_g(64, min_r=0.30, max_r=0.50).save(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "res", "icons", "glyphit.png"))
print("icon written")
