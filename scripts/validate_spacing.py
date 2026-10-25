#!/usr/bin/env python3
"""Check that no two letters touch: minimum clearance between every adjacent pair.

For each style, at weights across the axis and at the optical-size extremes, every
pair from LEFT x RIGHT is shaped with HarfBuzz (kerning and default ligatures on),
and the gap between the left glyph's right profile and the right glyph's left
profile is measured at every height. Any pair closer than the floor fails (display
sizes are set tighter on purpose and get a lower one), unless it is a deliberate join:

* the f joining forms (f.l, f.t, f.f) reach into the next letter on purpose;
* the Q tail runs under the next letter, as in classical book faces.

Targeted pair probes also cover interior interpolation minima between the full scans.

    make test        (or: uv run python scripts/validate_spacing.py)
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import sys

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
import uharfbuzz as hb

from build import ROOT, VARIABLE, Style

FONTS = {"roman": VARIABLE[Style.ROMAN], "italic": VARIABLE[Style.ITALIC]}
# Grade never changes spacing (validate_fonts checks that), so it is not scanned here.
LOCATIONS = ([{"wght": w, "opsz": 12} for w in (300, 400, 500, 600, 700)]
             + [{"wght": w, "opsz": o} for o in (6, 72) for w in (300, 400, 700)]
             + [{"wght": w, "opsz": o, "YTLC": 550} for o in (6, 12) for w in (300, 400, 700)]
             + [{"wght": 400, "opsz": o} for o in (8, 10)])
FLOOR = {"text": 15, "display": 10}   # font units; kerning targets 20+, interpolation may dip a little
REGRESSION_PROBES = {
    "roman": (
        ({"wght": 400, "opsz": 10.4}, ("Yē", "Yī")),
        ({"wght": 450, "opsz": 9.4}, ("Yï",)),
        ({"wght": 400, "opsz": 11}, ("Vī", "Wī")),
    ),
    "italic": (
        ({"wght": 300, "opsz": 10}, ("c’", "s’")),
        ({"wght": 300, "opsz": 12, "YTLC": 525}, ("Xō",)),
        ({"wght": 325, "opsz": 12, "YTLC": 520}, ("s’",)),
        ({"wght": 460, "opsz": 12}, ("Xō",)),
        ({"wght": 400, "opsz": 8.9}, ("c’", "s’")),
        ({"wght": 300, "opsz": 10.5}, ("u’",)),
        ({"wght": 300, "opsz": 9.5}, ("Yō",)),
    ),
}
STEP = 4            # scanline spacing in font units
LEFT = "abcdefghijklmnopqrstuvwxyzß" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" ".,;:’'\")?!-"
RIGHT_BASE = "abcdefghijklmnopqrstuvwxyzß" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" ".,;:’'\")?!-"
JOINING = (".l", ".t", ".f")     # f forms designed to connect to the next letter


class FlattenPen(BasePen):
    """Collect contours as polylines (curves flattened)."""

    def __init__(self, glyphset):
        super().__init__(glyphset)
        self.polys, self.cur = [], []

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, a, b, c):
        p0 = self.cur[-1]
        for k in range(1, 13):
            t = k / 12
            self.cur.append(tuple((1 - t) ** 3 * p0[i] + 3 * (1 - t) ** 2 * t * a[i]
                                  + 3 * (1 - t) * t * t * b[i] + t ** 3 * c[i] for i in (0, 1)))

    def _qCurveToOne(self, a, b):
        p0 = self.cur[-1]
        for k in range(1, 9):
            t = k / 8
            self.cur.append(tuple((1 - t) ** 2 * p0[i] + 2 * (1 - t) * t * a[i] + t * t * b[i] for i in (0, 1)))

    def _closePath(self):
        if self.cur:
            self.polys.append(self.cur)
        self.cur = []

    _endPath = _closePath


def profile(glyphset, name):
    pen = FlattenPen(glyphset)
    glyphset[name].draw(pen)
    left, right = {}, {}
    for y in range(-300, 1000, STEP):
        xs = []
        for poly in pen.polys:
            for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
                if (y0 - y) * (y1 - y) < 0:
                    xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        if xs:
            left[y], right[y] = min(xs), max(xs)
    return left, right


def allowed(first_name: str, left_char: str) -> bool:
    return first_name.startswith("f") and first_name.endswith(JOINING) or left_char == "Q"


def scan(style: str, path: Path) -> tuple[list, int]:
    cmap = TTFont(path).getBestCmap()
    accented = [chr(u) for u in cmap if 0xDF < u <= 0x17F and chr(u).islower() and u != 0x17F]
    right = list(RIGHT_BASE) + accented
    full_pairs = tuple(a + c for a in LEFT for c in right)
    cases = [(loc, full_pairs) for loc in LOCATIONS] + list(REGRESSION_PROBES[style])
    failures, checked = [], 0
    for loc, pairs in cases:
        inst = instantiateVariableFont(TTFont(path), {**loc, "GRAD": 0})
        floor = FLOOR["display" if loc["opsz"] >= 72 else "text"]
        buf = BytesIO(); inst.save(buf)
        font = hb.Font(hb.Face(buf.getvalue()))
        glyphset = inst.getGlyphSet()
        cache = {}
        for pair in pairs:
            b = hb.Buffer(); b.add_str(pair); b.guess_segment_properties(); hb.shape(font, b, {})
            if len(b.glyph_infos) != 2:          # ligature: nothing to space
                continue
            names = [font.glyph_to_string(i.codepoint) for i in b.glyph_infos]
            if allowed(names[0], pair[0]):
                continue
            for n in names:
                if n not in cache:
                    cache[n] = profile(glyphset, n)
            adv = b.glyph_positions[0].x_advance + b.glyph_positions[1].x_offset
            ra, lc = cache[names[0]][1], cache[names[1]][0]
            gaps = [lc[y] + adv - ra[y] for y in ra if y in lc]
            checked += 1
            if gaps and min(gaps) < floor:
                failures.append({"style": style, **loc, "pair": pair, "gap": round(min(gaps)),
                                 "floor": floor})
    return failures, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "build/spacing-validation.json")
    args = parser.parse_args()
    failures, checked = [], 0
    for style, path in FONTS.items():
        f, n = scan(style, path)
        failures += f; checked += n
    for f in sorted(failures, key=lambda x: x["gap"])[:50]:
        print(f"FAIL {f['style']} wght {f['wght']} opsz {f['opsz']} {f['pair']!r}: "
              f"{f['gap']} units (floor {f['floor']})")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"floor": FLOOR, "locations": LOCATIONS, "probes": REGRESSION_PROBES,
                                       "checked": checked,
                                       "failures": failures}, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"{checked - len(failures)} passed, {len(failures)} failed (pair clearance). Report: {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
