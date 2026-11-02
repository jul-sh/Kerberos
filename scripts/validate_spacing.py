#!/usr/bin/env python3
"""Check that no two letters touch: minimum clearance between adjacent pairs.

For each style and width family (`Kerberos` proportional and `Kerberos SerifOnly`
constant-width), adjacent glyph pairs are shaped with HarfBuzz (kerning and default
ligatures on) across weights, optical sizes, lowercase heights, and Caleuche (`CALE`)
stops (`0`, `50`, `100`):

* At `CALE = 0`, the full Kerberos scan (`LEFT x RIGHT` + interior regression probes)
  enforces >= 15u at reading sizes and >= 10u at display size (72 pt), allowing only
  deliberate joins (`f.l`, `f.t`, `f.f` and the classical `Q` tail).
* At `CALE = 50` and `CALE = 100`, both Proportional and `SerifOnly` variable fonts are
  checked across reading weights and lowercase heights, verifying Caleuche 2D optical
  kerning, overhanging `f`/capitals/accents, `j` hooks, and `« » ‹ ›` guillemets.

    make test        (or: uv run python scripts/validate_spacing.py)
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import enum
from io import BytesIO
import json
import math
from pathlib import Path
import sys

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
import uharfbuzz as hb

from build import ROOT, VARIABLE, VARIABLE_SERIFONLY, Style, WidthFamily

# Grade never changes spacing (validate_fonts checks that), so it is not scanned here.
LOCATIONS = ([{"wght": w, "opsz": 12} for w in (300, 400, 500, 600, 700)]
             + [{"wght": w, "opsz": o} for o in (6, 72) for w in (300, 400, 700)]
             + [{"wght": w, "opsz": o, "YTLC": 550} for o in (6, 12) for w in (300, 400, 700)]
             + [{"wght": 400, "opsz": o} for o in (8, 10)])
FLOOR = {"text": 15, "display": 10}   # font units; kerning targets 20+, interpolation may dip a little

REGRESSION_PROBES: dict[Style, tuple[tuple[dict[str, float], tuple[str, ...]], ...]] = {
    Style.ROMAN: (
        ({"wght": 400, "opsz": 10.4}, ("Yē", "Yī")),
        ({"wght": 450, "opsz": 9.4}, ("Yï",)),
        ({"wght": 400, "opsz": 11}, ("Vī", "Wī")),
        ({"wght": 700, "opsz": 12}, ("ζΥ", "ζΐ", "Υΐ", "Γΐ", "ΛΑ", "ΛΛ", "ДЈ", "Дј", "Қу", "Жу", "Ґї", "74", "47", "7.", "£7", "€4", "7°", "7%", "f¹", "f²", "7⁴", "7º")),
        ({"wght": 300, "opsz": 72}, ("ζΥ", "Υΐ", "ҚJe", "ДJe", "74", "47", "£7", "€4", "f¹", "f²")),
    ),
    Style.ITALIC: (
        ({"wght": 326, "opsz": 72, "YTLC": 515}, ("fō",)),
        ({"wght": 330, "opsz": 72, "YTLC": 515}, ("fī",)),
        ({"wght": 355, "opsz": 6}, ("S’",)),
        ({"wght": 325, "opsz": 10}, ("Xī", "Yē", "Yī")),
        ({"wght": 325, "opsz": 10.5}, ("fō",)),
        *(({"wght": weight, "opsz": 9.5}, ("S’", "Xö", "Xī", "Yë", "Yï", "Yÿ", "Yē", "Yī", "fō"))
          for weight in (300, 325, 350, 375, 400)),
        *(({"wght": weight, "opsz": size}, ("S’", "Xö", "Xī", "Yë", "Yï", "Yÿ", "Yē", "Yī", "fō"))
          for weight in (375, 390, 400, 410, 425) for size in (8, 10, 12)),
        *(({"wght": weight, "opsz": 72, "YTLC": height}, ("fī", "fō"))
          for weight in (300, 320, 350, 375, 390, 400, 410, 425) for height in (500, 520, 550)),
        *(({"wght": 300, "opsz": 72, "YTLC": height}, ("b’",))
          for height in (510, 520, 525, 530, 540, 550)),
        ({"wght": 350, "opsz": 72, "YTLC": 525}, ("b’",)),
        ({"wght": 350, "opsz": 72, "YTLC": 550}, ("b’",)),
        ({"wght": 390, "opsz": 72, "YTLC": 510}, ("b’",)),
        ({"wght": 400, "opsz": 72, "YTLC": 512}, ("b’",)),
        ({"wght": 403, "opsz": 72, "YTLC": 509}, ("b’",)),
        ({"wght": 410, "opsz": 72, "YTLC": 512}, ("b’",)),
        ({"wght": 450, "opsz": 72, "YTLC": 514}, ("b’",)),
        ({"wght": 300, "opsz": 10}, ("c’", "s’")),
        ({"wght": 300, "opsz": 12, "YTLC": 525}, ("Xō",)),
        ({"wght": 325, "opsz": 12, "YTLC": 520}, ("s’",)),
        ({"wght": 460, "opsz": 12}, ("Xō",)),
        ({"wght": 400, "opsz": 8.9}, ("c’", "s’")),
        ({"wght": 300, "opsz": 10.5}, ("u’",)),
        ({"wght": 300, "opsz": 9.5}, ("Yō",)),
        ({"wght": 700, "opsz": 12}, ("ζΥ", "ζΐ", "Υΐ", "Γΐ", "ΛΑ", "ДЈ", "Дј", "Қу", "Жу", "74", "47", "7’", "5’", "£7", "€4", "7°", "7%", "f¹", "f²", "Y²", "7⁴", "7º")),
        ({"wght": 300, "opsz": 6}, ("Қу", "Ңу", "Жу", "Дј", "74", "47", "£7", "€4", "7°", "7%", "f¹", "f²", "Y²", "7⁴", "7º")),
    ),
}

STEP = 4            # scanline spacing in font units
LETTERS = "abcdefghijklmnopqrstuvwxyzßABCDEFGHIJKLMNOPQRSTUVWXYZ"
LEFT = LETTERS + ".,;:’'\")?!-"
RIGHT_BASE = LETTERS + ".,;:’'\")?!-"
JOINING = (".l", ".t", ".f")     # f forms designed to connect to the next letter

# Targeted Caleuche kerning & collision-avoidance pairs across CALE = 50 and CALE = 100
CALEUCHE_PROPORTIONAL_PROBES = (
    *(f"f{ch}" for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789?!\"')"),
    "f\u201d", "f\u2019",
    "fï", "fî", "fī", "fĩ", "fì", "fí", "fĵ", "fÿ", "fã", "fõ", "fñ", "fö", "fë",
    "gj", "jj", "qj", "[j", "]j", "(j", ",j", "ęj", "įj", "ji",
    "Tï", "Vï", "Wï", "Yï", "Tî", "Vî", "Wî", "Yî", "Tī", "Wī", "Tĩ", "Wĩ",
    "ęx", "Ąy", "ęy",
    "Wa", "Wo", "We", "Ta", "To", "Va", "Ya", "AV", "AW", "AY", "fo", "fa", "fe",
    "«a", "«e", "«o", "a»", "e»", "o»", "»«",
)

CALEUCHE_SERIFONLY_PROBES = (
    "gj", "jj", "ji", "[j", ",j",
    "«a", "«e", "«o", "«s", "a»", "c»", "e»", "g»", "o»", "s»", "»«", "»‹", "‹a", "a›",
)


class CaleucheStop(enum.Enum):
    MID = (50, 4, 3)
    FULL = (100, 5, 4)

    def __init__(self, cale: int, prop_probe_floor: int, serifonly_probe_floor: int) -> None:
        self.cale = cale
        self.prop_probe_floor = prop_probe_floor
        self.serifonly_probe_floor = serifonly_probe_floor


@dataclass(frozen=True)
class SpacingTarget:
    style: Style
    width_family: WidthFamily
    path: Path

    @property
    def label(self) -> str:
        style_slug = "roman" if self.style is Style.ROMAN else "italic"
        match self.width_family:
            case WidthFamily.PROPORTIONAL:
                return style_slug
            case WidthFamily.SERIF_ONLY:
                return f"serifonly-{style_slug}"


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


def profiles(glyphset, name: str) -> tuple[dict[int, float], dict[int, float]]:
    """Leftmost and rightmost ink x at each horizontal scanline y."""
    pen = FlattenPen(glyphset)
    glyphset[name].draw(pen)
    left: dict[int, float] = {}
    right: dict[int, float] = {}
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


def _evaluate_cases(
    label: str,
    tt: TTFont,
    hb_face: hb.Face,
    cases: list[tuple[dict[str, float], tuple[str, ...], int]],
) -> tuple[list[dict], int]:
    failures: list[dict] = []
    checked = 0
    for loc, pairs, floor in cases:
        full_loc = {"YTLC": 500, "CALE": 0, **loc, "GRAD": 0}
        font = hb.Font(hb_face)
        font.set_variations(full_loc)
        glyphset = tt.getGlyphSet(location=full_loc)
        cache: dict[str, tuple[dict[int, float], dict[int, float]]] = {}
        for pair in pairs:
            b = hb.Buffer()
            b.add_str(pair)
            b.guess_segment_properties()
            hb.shape(font, b, {})
            if len(b.glyph_infos) != 2:          # ligature: nothing to space
                continue
            names = [font.glyph_to_string(i.codepoint) for i in b.glyph_infos]
            if allowed(names[0], pair[0]):
                continue
            for n in names:
                if n not in cache:
                    cache[n] = profiles(glyphset, n)
            adv = b.glyph_positions[0].x_advance + b.glyph_positions[1].x_offset
            ra, lc = cache[names[0]][1], cache[names[1]][0]
            gaps = [lc[y] + adv - ra[y] for y in ra if y in lc]
            checked += 1
            if gaps and min(gaps) < floor:
                failures.append({
                    "style": label,
                    **full_loc,
                    "pair": pair,
                    "gap": round(min(gaps), 1),
                    "floor": floor,
                })
    return failures, checked


def scan_target(target: SpacingTarget) -> tuple[list[dict], int]:
    raw_bytes = target.path.read_bytes()
    tt = TTFont(BytesIO(raw_bytes))
    cmap = tt.getBestCmap()
    hb_face = hb.Face(raw_bytes)
    cases: list[tuple[dict[str, float], tuple[str, ...], int]] = []

    base_letter_pairs = tuple(
        a + c for a in LETTERS for c in LETTERS if a != "f" and c != "j"
    )

    match target.width_family:
        case WidthFamily.PROPORTIONAL:
            accented = [
                chr(u) for u in cmap if 0xDF < u <= 0x17F and chr(u).islower() and u != 0x17F
            ]
            right = list(RIGHT_BASE) + accented
            full_pairs = tuple(a + c for a in LEFT for c in right)
            for loc in LOCATIONS:
                floor = FLOOR["display" if loc["opsz"] >= 72 else "text"]
                cases.append((loc, full_pairs, floor))
            for loc, probe_pairs in REGRESSION_PROBES[target.style]:
                floor = FLOOR["display" if loc["opsz"] >= 72 else "text"]
                cases.append((loc, probe_pairs, floor))
            for stop in CaleucheStop:
                # Regular reading alphabet check at CALE = 50 and CALE = 100
                cases.append((
                    {"wght": 400, "opsz": 12, "YTLC": 500, "CALE": stop.cale},
                    base_letter_pairs,
                    7,
                ))
                # Targeted Caleuche probes across weights (300, 400, 700) and YTLC (500, 550)
                for w in (300, 400, 700):
                    for ytlc in (500, 550):
                        cases.append((
                            {"wght": w, "opsz": 12, "YTLC": ytlc, "CALE": stop.cale},
                            CALEUCHE_PROPORTIONAL_PROBES,
                            stop.prop_probe_floor,
                        ))

        case WidthFamily.SERIF_ONLY:
            for stop in CaleucheStop:
                cases.append((
                    {"wght": 400, "opsz": 12, "YTLC": 500, "CALE": stop.cale},
                    base_letter_pairs,
                    7,
                ))
                for w in (300, 400, 700):
                    for ytlc in (500, 550):
                        cases.append((
                            {"wght": w, "opsz": 12, "YTLC": ytlc, "CALE": stop.cale},
                            CALEUCHE_SERIFONLY_PROBES,
                            stop.serifonly_probe_floor,
                        ))

    return _evaluate_cases(target.label, tt, hb_face, cases)


def default_targets(fonts_root: Path) -> list[SpacingTarget]:
    targets: list[SpacingTarget] = []
    for style in Style:
        targets.append(
            SpacingTarget(
                style=style,
                width_family=WidthFamily.PROPORTIONAL,
                path=fonts_root / "variable" / VARIABLE[style].name,
            )
        )
    for style in Style:
        targets.append(
            SpacingTarget(
                style=style,
                width_family=WidthFamily.SERIF_ONLY,
                path=fonts_root / "serifonly" / VARIABLE_SERIFONLY[style].name,
            )
        )
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonts-root", type=Path, default=ROOT / "fonts")
    parser.add_argument("--report", type=Path, default=ROOT / "build/spacing-validation.json")
    args = parser.parse_args()
    targets = default_targets(args.fonts_root)
    failures: list[dict] = []
    checked = 0
    with ProcessPoolExecutor(max_workers=len(targets)) as pool:
        for f, n in pool.map(scan_target, targets):
            failures += f
            checked += n
    for f in sorted(failures, key=lambda x: x["gap"])[:50]:
        print(
            f"FAIL {f['style']} wght {f['wght']} opsz {f['opsz']} YTLC {f['YTLC']} "
            f"CALE {f['CALE']} {f['pair']!r}: {f['gap']} units (floor {f['floor']})"
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {
                "floor": FLOOR,
                "locations": LOCATIONS,
                "checked": checked,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{checked - len(failures)} passed, {len(failures)} failed (pair clearance). Report: {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
