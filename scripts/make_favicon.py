"""Favicons for the specimen site, drawn from Kerberos's own italic K.

    site/favicon.svg           the K alone, black on a clear ground, white when the browser is in dark mode
    site/favicon.png           32 x 32 fallback: white K on a black rounded square (reads on light and dark tabs)
    site/apple-touch-icon.png  180 x 180, opaque white K on black (iOS rounds the corners)

The small icons use Bold at optical size 8 and grade 100, the design meant for the smallest sizes; the touch
icon uses optical size 24. The favicon is drawn on a 64-unit square (16 px x 4): baseline and cap height sit
on whole pixels at 16 and 32 px. Run after `make build`:

    uv run --frozen python scripts/make_favicon.py
"""
from __future__ import annotations

from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageChops, ImageDraw

from build import ROOT, VARIABLE, Style  # noqa: E402

FONT = VARIABLE[Style.ITALIC]
SITE = ROOT / "site"
CHAR = "K"
SMALL = {"wght": 700, "opsz": 8, "GRAD": 100}
LARGE = {"wght": 700, "opsz": 24, "GRAD": 0}
SUPERSAMPLE = 16


def glyph(location):
    font = instantiateVariableFont(TTFont(FONT), location, inplace=False)
    glyphs = font.getGlyphSet()
    return glyphs, glyphs[font.getBestCmap()[ord(CHAR)]]


def fit(glyphs, g, size, top, baseline):
    """affine (font units, y up) -> (square, y down) placing the ink between top and baseline, centred across"""
    pen = BoundsPen(glyphs)
    g.draw(pen)
    x0, _, x1, y1 = pen.bounds
    s = (baseline - top) / y1
    return (s, 0, 0, -s, size / 2 - (x0 + x1) / 2 * s, baseline)


class PolygonPen(BasePen):
    """flattens outlines into closed polygons (curves split into short straight pieces)"""

    def __init__(self, glyphset, steps=24):
        super().__init__(glyphset)
        self.polys, self.cur, self.steps = [], [], steps

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            self.cur.append((mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0],
                             mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]))

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            self.cur.append((mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0],
                             mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]))

    def _closePath(self):
        if len(self.cur) > 2:
            self.polys.append(self.cur)
        self.cur = []

    _endPath = _closePath


def glyph_mask(glyphs, g, matrix, px, size):
    """anti-aliased coverage of the glyph on a px x px canvas (even-odd across contours)"""
    big = px * SUPERSAMPLE
    k = big / size
    pen = PolygonPen(glyphs)
    g.draw(TransformPen(pen, tuple(v * k for v in matrix)))
    mask = Image.new("L", (big, big), 0)
    for poly in pen.polys:
        layer = Image.new("L", (big, big), 0)
        ImageDraw.Draw(layer).polygon(poly, fill=255)
        mask = ImageChops.logical_xor(mask.convert("1"), layer.convert("1")).convert("L")
    return mask.resize((px, px), Image.LANCZOS)


def tile(glyphs, g, px, cap_share, radius_share):
    """white glyph on a black square (rounded unless radius_share is 0), opaque where the square is"""
    size = 64
    top = size / 2 - cap_share * size / 2 - 0.015 * size
    matrix = fit(glyphs, g, size, top, top + cap_share * size)
    big = px * SUPERSAMPLE
    square = Image.new("L", (big, big), 0)
    ImageDraw.Draw(square).rounded_rectangle([0, 0, big - 1, big - 1], radius=radius_share * big, fill=255)
    square = square.resize((px, px), Image.LANCZOS)
    ink = glyph_mask(glyphs, g, matrix, px, size)
    rgb = Image.merge("RGB", [ink, ink, ink])          # white where the K is, black elsewhere
    out = rgb.convert("RGBA")
    out.putalpha(square)
    return out


def favicon_svg(glyphs, g):
    size = 64
    matrix = fit(glyphs, g, size, top=8, baseline=56)    # cap height 12 px at 16 px, 24 px at 32 px
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    g.draw(TransformPen(pen, matrix))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
            "<style>path{fill:#000}@media (prefers-color-scheme:dark){path{fill:#fff}}</style>"
            f'<path d="{pen.getCommands()}"/></svg>\n')


def main():
    glyphs, g = glyph(SMALL)
    (SITE / "favicon.svg").write_text(favicon_svg(glyphs, g))
    tile(glyphs, g, 32, cap_share=0.58, radius_share=0.22).save(SITE / "favicon.png", optimize=True)
    glyphs, g = glyph(LARGE)
    tile(glyphs, g, 180, cap_share=0.52, radius_share=0).convert("RGB").save(SITE / "apple-touch-icon.png",
                                                                             optimize=True)
    for name in ("favicon.svg", "favicon.png", "apple-touch-icon.png"):
        print(f"wrote site/{name} ({(SITE / name).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
