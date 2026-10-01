# GlyphGrid collections

A **collection** is a folder of glyph images. GlyphGrid builds every 1/4/9/16-up plate from it.

```
plates/collections/
  shapes/                 <- example, 16 glyphs
    01_circle.png         <- priority 1 (the 1-up plate uses only this one)
    02_square.png         <- the 4-up plate uses 01-04
    ...                   <- the 9-up plate uses 01-09
    16_smiley.png         <- the 16-up plate uses 01-16
  my_brands/
    01_mybrand.png
    02_partner.png
    ...
```

**How it works**

- **Images:** any png/jpg/tif/webp. They're sorted by filename, so number them in priority order. Transparency is respected, everything sits on black, and each glyph is trimmed, scaled uniformly and centred in its cell.
- **Fewer images than cells:** the images repeat. A single logo on black works at every grid size: 1-up is the logo and 16-up is the logo 16 times.
- **Hand-made plates win:** put `anything_4up.png` (or `_1up`, `_9up`, `_16up`) in the folder and it's used as-is for that grid. Missing sizes are still built from the images.
- **Plates only, no single images:** the largest plate is cut into its cells and those become the glyphs. So a folder with just a `_16up.png` also works at 1/4/9-up (cells 1, 1-4 and 1-9).
- **Cache:** built plates go into `_built/` and rebuild automatically when you add, remove or edit a file.
- **Mixed mode:** for Grid Mode = Mixed, the 1/4/9/16-up plates are packed into one composite (TL 1-up, TR 4-up, BL 9-up, BR 16-up).

**In Cinema 4D:** set Plate tab → Plate Style = **9 Collection**, and Collection = the folder name (e.g. `shapes`) or an absolute path.

**From Terminal:** `python glyphgrid_plates.py collection --out plates/collections/my_brands` writes all four plates.

Only use logos and artwork you have the rights to use.
