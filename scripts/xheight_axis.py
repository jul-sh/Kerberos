#!/usr/bin/env python3
"""Lowercase height axis (YTLC): derive tall-x-height masters from the drawn ones at build time.

    YTLC 500  the drawn design (default; Google Fonts' registered default, "Normal")
    YTLC 550  the x-height 10% taller (value / 500 = x-height factor)

Nothing new is stored in the sources: each weight master (Light, Regular, Bold at the text
size and normal grade) gets a twin at YTLC 550; optical size and grade add to it, as they do
to each other. What follows the x-height, and how:

- Lowercase (Latin, Greek, Cyrillic; ligatures and alternates), small caps and oldstyle
  figures are stretched vertically: the baseline zone (feet, bowl bottoms) stays, the
  x-height zone (heads, arches, bowl tops, crossbars at x-height) rises by the full amount,
  the band between them stretches, and above the x-height zone the ascender part is
  compressed back so ascenders, their serifs and descenders stay where they are. The
  stretch is spread over the empty stretches of each glyph's outline (the long stems), so
  bars and serifs keep their thickness; the compression goes first into the empty space
  over short ascenders (t, φ, ґ and the ascending figures rise whole), then into stems,
  then evenly into curves, and into horizontal strokes only when nothing else has room.
  It never folds an outline, in the weight masters or at any optical size and grade.
- θ, an ascender-high oval barred at mid-height, has no x-height and stays as drawn.
- Dots and other separate shapes above the x-height move as a whole (the dots of i and j
  by less, so they do not climb far over the ascenders).
- Combining marks rise with the x-height as a whole, so precomposed accents and GPOS mark
  attachment both follow; capitals that use the same marks are compensated and do not move.
- Colon, semicolon, dashes, middle dot, bullet and guillemets move as a whole with the
  x-height (half of it at mid-height).
- Whatever moves as a whole moves by whole units, so it compiles exactly as drawn.
- Advances never change. In the italic every move follows the italic angle.
- Kerning: in each tall twin, the pairs the taller lowercase would crowd (an accent rising under
  the bar of T or the arm of Y, an s under an apostrophe) open just enough to keep their room.

    uv run python scripts/xheight_axis.py sources/Kerberos.glyphs out.glyphs
"""
from __future__ import annotations

import bisect
import copy
import math
import re
import sys
import unicodedata
import uuid

import glyphsLib
from fontTools.pens.recordingPen import RecordingPen
from glyphsLib import glyphdata
from glyphsLib.filters.eraseOpenCorners import EraseOpenCornersPen
from glyphsLib.types import Point

TAG, NAME = "YTLC", "Lowercase Height"
NORMAL, TALL = 500, 550           # axis default (drawn) and tallest x-height
FACTOR = TALL / NORMAL - 1        # +10%

FIGURES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
OLDSTYLE_SUFFIXES = ("", "tosf")  # the default figures are oldstyle
PUNCTUATION = {"colon", "semicolon", "hyphen", "hyphentwo", "softhyphen", "endash", "emdash", "figuredash",
               "horizontalbar", "periodcentered", "bullet", "guillemetleft", "guillemetright",
               "guilsinglleft", "guilsinglright", "anoteleia", "questiongreek"}
NOT_LOWERCASE_SUFFIXES = ("sups", "subs", "sinf", "numr", "dnom", "case")
FIXED_MARKS = {"caroncomb.alt"}               # the vertical caron of ď ľ ť sits by the ascender
ASCENDER_CARONS = {"dcaron", "lcaron", "tcaron"}
KEEP_COMPONENTS = {"napostrophe"}             # its apostrophe is a letter at cap height, not an accent
FULL_HEIGHT = {"theta"}                       # an ascender-high oval barred at mid-height: no x-height in it
DOTTED = {"i", "j", "idotaccent", "iogonek", "ij", "i-cy", "je-cy"}   # their drawn dots rise less

DOT_SHARE = 0.6        # dots above the x-height rise by this share of the x-height change
GAP_POWER = 3.0        # the larger an empty stretch of outline, the more of the stretch it takes
MAX_SQUEEZE = 0.7      # never compress an outline faster than this (units per unit), or it folds
STEM_SQUEEZE = 0.92    # a stem is straight: it can shorten almost all the way
EMPTY_SQUEEZE = 0.9    # empty space above a short ascender (t, ґ, horns, ascending figures) closes first
STEM_SLOPE = 0.2       # a straight edge this close to the stem angle is a stem (dx per dy)
STEM_FLAT = 0.06       # ... and so is a curve this close to straight (off its chord, per unit of it)
STEM_RUN = 0.4         # ... if the ink across it is no wider than this share of the x-height
STROKE = 0.35          # ink only this share of the x-height tall is a horizontal stroke: keep its weight
STEEP, NEAR = 2.0, 12.0  # two points are one above the other within dx <= STEEP * dy + NEAR


# ----------------------------------------------------------------------------------------------
# which glyphs follow, and how

def _parts(name: str) -> tuple[str, list[str]]:
    base, _, suffix = name.partition(".")
    return suffix, base.split("_")


def _char_category(name: str) -> str | None:
    info = glyphdata.get_glyph(name)
    if info.unicode:
        try:
            return unicodedata.category(chr(int(info.unicode, 16)))
        except (ValueError, OverflowError):
            return None
    return None


def kind(name: str, font) -> str | None:
    """'stretch' (lowercase, small caps, oldstyle figures), 'mark', 'punct' or None."""
    suffix, parts = _parts(name)
    suffixes = suffix.split(".") if suffix else []
    if name in FIXED_MARKS or name in FULL_HEIGHT or "case" in suffixes:
        return None
    if "sc" in suffixes:
        return "stretch"
    if any(s in NOT_LOWERCASE_SUFFIXES for s in suffixes):
        return None
    if parts[0] in FIGURES and len(parts) == 1:
        return "stretch" if suffix in OLDSTYLE_SUFFIXES else None
    if name in PUNCTUATION:
        return "punct"
    info = glyphdata.get_glyph(parts[0] if len(parts) == 1 else name)
    if info.category == "Mark" and info.subCategory != "Modifier":
        return "mark"
    cats = []
    for part in parts:
        cat = _char_category(part)
        if cat is None and glyphdata.get_glyph(part).category == "Letter" and part[:1].islower():
            cat = "Ll"
        cats.append(cat)
    if "Ll" in cats:
        return "stretch"
    if "Lt" in cats and font is not None:   # titlecase digraphs (Dž): follow through their components
        return "composite-only"
    return None


# ----------------------------------------------------------------------------------------------
# the vertical move

class Edges:
    """The edges of one glyph's outlines, to ask what lies across a given height: nothing (the
    empty space above a short ascender), only stems (straight edges at the stem angle with narrow
    ink between them, which can shorten without changing shape), a horizontal stroke (somewhere
    across the glyph, the ink there is only a stroke's thickness tall: the top of β's bowl, the
    flag of б), or anything else (curves, which can take an even squeeze)."""

    def __init__(self, paths, steps: int = 16):
        self.pieces = []                      # (x0, y0, x1, y1, how far off the stem angle a stem may be)
        self.ys = []
        for path in paths:
            nodes = list(path.nodes)
            on = [i for i, n in enumerate(nodes) if n.type != "offcurve"]
            if not on:
                continue
            self.ys += [n.position.y for n in nodes]
            n = len(nodes)
            for k, i in enumerate(on):
                if k + 1 == len(on) and not path.closed:
                    break
                j = on[(k + 1) % len(on)]
                pts = [(nodes[(i + s) % n].position.x, nodes[(i + s) % n].position.y)
                       for s in range(((j - i) % n or n) + 1)]
                if len(pts) == 2:
                    self.pieces.append((*pts[0], *pts[1], STEM_SLOPE))
                    continue
                tolerance = -1.0                  # a curve is no stem ...
                if len(pts) == 4:
                    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
                    chord = math.hypot(x3 - x0, y3 - y0)
                    off = max(abs((x3 - x0) * (y0 - y) - (x0 - x) * (y3 - y0)) for x, y in pts[1:3]) / (chord or 1)
                    if off <= STEM_FLAT * chord:  # ... unless it is all but straight (a flaring stem)
                        tolerance = STEM_SLOPE / 2
                    flat = []
                    for s in range(steps + 1):
                        t = s / steps
                        u = 1 - t
                        flat.append((u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
                                     u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3))
                else:
                    flat = pts
                self.pieces += [(*a, *b, tolerance) for a, b in zip(flat, flat[1:])]
        self.top = max(self.ys) if self.ys else None
        self._columns = None

    @staticmethod
    def _runs(hits):
        """ink runs along a line from (position, winding step) crossings, nonzero winding"""
        runs, winding, start = [], 0, None
        for pos, sign in sorted(hits):
            before, winding = winding, winding + sign
            if before == 0 and winding:
                start = pos
            elif before and not winding:
                runs.append((start, pos))
        return runs

    def columns(self, samples: int = 24):
        """vertical ink runs at evenly spaced x across the glyph"""
        if self._columns is None:
            xs = [p[0] for p in self.pieces] + [p[2] for p in self.pieces]
            lo, hi = min(xs), max(xs)
            self._columns = []
            for s in range(samples):
                x = lo + (hi - lo) * (s + 0.5) / samples
                hits = [(y0 + (x - x0) * (y1 - y0) / (x1 - x0), 1 if x1 > x0 else -1)
                        for x0, y0, x1, y1, _ in self.pieces if (x0 <= x < x1) or (x1 <= x < x0)]
                self._columns.append(self._runs(hits))
        return self._columns

    def kind(self, y: float, x_height: float, slant: float) -> str:
        hits, stems = [], True
        for x0, y0, x1, y1, tolerance in self.pieces:
            if (y0 <= y < y1) or (y1 <= y < y0):
                slope = (x1 - x0) / (y1 - y0)
                hits.append((x0 + (y - y0) * slope, 1 if y1 > y0 else -1))
                stems = stems and abs(slope - slant) <= tolerance
        if not hits:
            return "empty"
        if any(a < y < b and b - a < STROKE * x_height for runs in self.columns() for a, b in runs):
            return "stroke"
        if stems and all(b - a <= STEM_RUN * x_height for a, b in self._runs(hits)):
            return "stem"
        return "other"


class Move:
    """y -> y + delta(y) for one glyph in one master, plus the shear of the italic.

    The x-height zone moves by a whole number of units, so shapes that move as a whole stay
    exactly as drawn once compiled; the rise below it is spread over the empty stretches of the
    outline. The fall above it (back to the ascender) goes first into empty space, then into
    stems, then evenly into curves, horizontal strokes last; never so fast that the outline
    folds, in this master or in any optical size and grade built on it (`limits`: pairs of
    heights in this master and how much closer they may come)."""

    def __init__(self, x_height: float, ascender: float, italic: float, edges: Edges | None = None,
                 limits=()):
        self.dx_per_dy = math.tan(math.radians(italic))
        self.x = X = x_height
        self.d = d = round(FACTOR * X)
        self.strained = False                        # set when the fall could not keep its limits
        self.b0, self.t0 = 0.2 * X, 0.8 * X          # baseline zone top, x-height zone bottom
        self.p1 = X + 0.15 * X                       # x-height zone top (overshoots, small caps, figures)
        self.z0 = ascender - 0.116 * 741             # ascender serifs and above stay (655 at 741)
        if edges is not None and edges.top is not None and edges.top < ascender - 50:
            self.z0 = ascender                       # a short ascender (t, φ, ascending figures) rises whole
        ys = sorted(set(round(y, 2) for y in edges.ys)) if edges is not None else []
        self.rise = self._spread(ys, self.b0, self.t0)        # increasing 0 -> 1
        self.fall = self._squeeze(ys, edges, limits)

    @staticmethod
    def _spread(ys, lo, hi):
        """breakpoints (y, share 0..1) over [lo, hi]: the share grows across empty stretches
        between outline points, more across longer ones"""
        if hi <= lo:
            return [(lo, 0.0), (hi, 1.0)]
        cuts = [lo] + [y for y in ys if lo < y < hi] + [hi]
        weights = [(b - a) ** GAP_POWER for a, b in zip(cuts, cuts[1:])]
        s = sum(weights) or 1.0
        out, acc = [(lo, 0.0)], 0.0
        for c, w in zip(cuts[1:], weights):
            acc += w / s
            out.append((c, min(acc, 1.0)))
        return out

    def _squeeze(self, ys, edges, limits):
        """breakpoints (y, share 0..1) of the fall over [p1, z0]"""
        lo, hi = self.p1, self.z0
        if hi <= lo or self.d == 0:
            return [(lo, 0.0), (max(hi, lo + 1), 1.0)]
        cuts = [lo] + [y for y in ys if lo < y < hi] + [hi]
        if len(cuts) == 2:                           # nothing drawn in between: a straight fall
            return [(lo, 0.0), (hi, 1.0)]
        if callable(limits):
            limits = limits()
        gaps = [b - a for a, b in zip(cuts, cuts[1:])]
        kinds = [edges.kind((a + b) / 2, self.x, self.dx_per_dy) if edges is not None else "other"
                 for a, b in zip(cuts, cuts[1:])]
        order = ("empty", "stem", "other", "stroke")
        rates = {"empty": EMPTY_SQUEEZE, "stem": STEM_SQUEEZE}
        cap = [rates.get(k, MAX_SQUEEZE) * g for k, g in zip(kinds, gaps)]
        # pairs of heights that may only come so much closer: the bands between them
        spans = []
        for ya, yb, limit in limits:
            a, b = max(round(ya, 2), lo), min(round(yb, 2), hi)
            if b > a:
                spans.append((bisect.bisect_left(cuts, a - 1e-6), bisect.bisect_left(cuts, b - 1e-6), limit))
        take = [0.0] * len(gaps)

        def place(amount):
            """into the bands with room left, by class, each in proportion to its room"""
            for k in order:
                idx = [i for i in range(len(gaps)) if kinds[i] == k and cap[i] - take[i] > 1e-9]
                room = sum(cap[i] - take[i] for i in idx)
                if not idx or amount <= 1e-9:
                    continue
                part = min(amount, room)
                for i in idx:
                    take[i] += part * (cap[i] - take[i]) / room
                amount -= part
            return amount

        left = place(float(self.d))
        for _ in range(40):
            over = [(i, j, limit, sum(take[i:j])) for i, j, limit in spans if sum(take[i:j]) > limit + 1e-6]
            if not over or left > 1e-9:
                break
            freed = 0.0
            for i, j, limit, total in over:
                for b in range(i, j):
                    new = take[b] * limit / total if total > 0 else 0.0
                    freed += take[b] - new
                    take[b] = cap[b] = new
            left = place(freed)
        if left > 1e-9:                              # no room to spare anywhere: spread by length
            self.strained = True
            for i, g in enumerate(gaps):
                take[i] += left * g / sum(gaps)
        out, acc = [(lo, 0.0)], 0.0
        for c, t in zip(cuts[1:], take):
            acc += t / self.d
            out.append((c, min(acc, 1.0)))
        return out

    @staticmethod
    def _interp(points, y):
        ys = [p[0] for p in points]
        i = bisect.bisect_right(ys, y)
        if i <= 0:
            return points[0][1]
        if i >= len(points):
            return points[-1][1]
        (y0, s0), (y1, s1) = points[i - 1], points[i]
        return s0 + (s1 - s0) * (y - y0) / (y1 - y0) if y1 > y0 else s1

    def delta(self, y: float) -> float:
        if y <= self.b0:
            return 0.0
        if y < self.t0:
            return self.d * self._interp(self.rise, y)
        if y <= self.p1:
            return float(self.d)
        if y < self.z0:
            return self.d * (1.0 - self._interp(self.fall, y))
        return 0.0

    def body(self, y: float) -> float:
        """the plain zone move, without per-glyph spreading, in whole units (rigid shapes)"""
        if y <= self.b0:
            return 0.0
        if y < self.t0:
            return float(round(self.d * (y - self.b0) / (self.t0 - self.b0)))
        return float(self.d)

    def shift(self, dy: float) -> tuple[float, float]:
        """(dx, dy) of a move by dy along the italic angle; whole moves stay whole, so a shape
        that moves as one stays exactly as drawn once compiled"""
        dx = dy * self.dx_per_dy
        return (float(round(dx)) if dy == round(dy) else dx), dy

    def point(self, x: float, y: float, dy: float | None = None) -> Point:
        dx, dy = self.shift(self.delta(y) if dy is None else dy)
        return Point(round((x + dx) * 100) / 100, round((y + dy) * 100) / 100)


def _bounds(nodes):
    ys = [n.position.y for n in nodes]
    return min(ys), max(ys)


def _box(path):
    xs = [n.position.x for n in path.nodes]
    ys = [n.position.y for n in path.nodes]
    return min(xs), min(ys), max(xs), max(ys)


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _shift_path(path, mv: Move, dy: float):
    for n in path.nodes:
        n.position = mv.point(n.position.x, n.position.y, dy)


def _clustered(points: list[tuple[float, float]], deltas: list[float], radius: float = 14.0,
               max_extent: float = 40.0) -> list[float]:
    """Points closer than `radius` move together (their mean move): a corner, a notch, a node
    and its short handles keep their exact shape, so tiny overlaps drawn at corners (which other
    axes already bring near the surface) cannot open up."""
    n = len(points)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(n), key=lambda i: points[i][0])
    for a_i, i in enumerate(order):
        xi, yi = points[i]
        for j in order[a_i + 1:]:
            xj, yj = points[j]
            if xj - xi > radius:
                break
            if (xj - xi) ** 2 + (yj - yi) ** 2 < radius * radius:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = list(deltas)
    for members in groups.values():
        if len(members) < 2:
            continue
        ys = [points[i][1] for i in members]
        if max(ys) - min(ys) > max_extent:     # a long chain of close points: leave it stretching
            continue
        mean = sum(deltas[i] for i in members) / len(members)
        for i in members:
            out[i] = mean
    return out


def _limits(paths, body, corners) -> list[tuple[float, float, float]]:
    """For the points of each segment of the stretching outline: how much closer each pair may
    come as the x-height grows (`corners`: the outline's points in this master and in every
    optical size and grade built on it, path by path), so it folds in none. Only pairs one
    above the other count: side by side, a squeeze tilts the curve between them, no more."""
    out = []
    for i in body:
        nodes = list(paths[i].nodes)
        n = len(nodes)
        on = [j for j, node in enumerate(nodes) if node.type != "offcurve"]
        for k, a in enumerate(on):
            seg = [(a + s) % n for s in range(((on[(k + 1) % len(on)] - a) % n or n) + 1)]
            for u, p in enumerate(seg):
                for q in seg[u + 1:]:
                    lo, hi = (p, q) if nodes[p].position.y < nodes[q].position.y else (q, p)
                    if nodes[hi].position.y - nodes[lo].position.y < 1e-6:
                        continue
                    limit = None
                    for geo in corners:
                        (xl, yl), (xh, yh) = geo[i][lo], geo[i][hi]
                        if yh - yl > -0.5 and abs(xh - xl) <= STEEP * max(yh - yl, 0.0) + NEAR:
                            limit = min(MAX_SQUEEZE * max(yh - yl, 0.0), limit if limit is not None else 1e9)
                    if limit is not None:
                        out.append((nodes[lo].position.y, nodes[hi].position.y, limit))
    return out


def transform_layer(layer, name: str, k: str, mv_for, shifts: dict, corners=None,
                    strained: set | None = None) -> float | None:
    """Rework one twin layer in place. Returns the rigid shift of the whole glyph (marks and
    punctuation), which composites that must not move compensate for."""
    if k == "stretch":
        X = mv_for(None).x
        paths = list(layer.paths)
        boxes = [_box(p) for p in paths]
        main = [i for i, b in enumerate(boxes) if b[1] <= X + 10]
        body, rigid = list(main), []              # outlines that stretch; (path, share) moving whole
        for i, b in enumerate(boxes):
            if i in main or name in ASCENDER_CARONS:
                continue
            if any(_overlaps(b, boxes[j]) for j in main):
                if b[3] >= mv_for(None).z0:      # a part of the letter reaching the ascender (ζ, ξ)
                    body.append(i)
                else:                             # a bar across the ascender (ħ, đ, ђ)
                    rigid.append((i, 1.0))
            else:                                 # a dot or an accent drawn as its own shape
                rigid.append((i, DOT_SHARE if _parts(name)[1][0] in DOTTED else 1.0))
        mv = mv_for(Edges([paths[i] for i in body]), (lambda: _limits(paths, body, corners)) if corners else ())
        if mv.strained and strained is not None:
            strained.add(name)
        for i, share in rigid:
            _shift_path(paths[i], mv, round(mv.d * share))
        moving = [n for i in body for n in paths[i].nodes]
        if moving:
            deltas = _clustered([(n.position.x, n.position.y) for n in moving],
                                [mv.delta(n.position.y) for n in moving])
            for n, dy in zip(moving, deltas):
                n.position = mv.point(n.position.x, n.position.y, dy)
        for a in layer.anchors:
            a.position = mv.point(a.position.x, a.position.y)
        return None
    mv = mv_for(None)
    if k == "mark":
        ys = [n.position.y for p in layer.paths for n in p.nodes] + [a.position.y for a in layer.anchors]
        if not ys:
            return None
        centre = (min(ys) + max(ys)) / 2 if layer.paths else max(ys)
        dy = float(mv.d) if centre >= mv.x else mv.body(centre)
        for p in layer.paths:
            _shift_path(p, mv, dy)
        for a in layer.anchors:
            a.position = mv.point(a.position.x, a.position.y, dy)
        return dy
    if k == "punct":
        dys = []
        for p in layer.paths:
            lo, hi = _bounds(p.nodes)
            dy = mv.body((lo + hi) / 2)
            _shift_path(p, mv, dy)
            dys.append(dy)
        for a in layer.anchors:
            a.position = mv.point(a.position.x, a.position.y, mv.body(a.position.y))
        return dys[0] if len(set(dys)) == 1 and dys else None
    return None


# ----------------------------------------------------------------------------------------------
# the axis

def add_xheight_axis(font) -> dict:
    """Augment a GSFont in place: YTLC axis and one tall-x-height twin per weight master."""
    axes = list(font.customParameters["Axes"])
    if any(ax.get("Tag") == TAG for ax in axes):
        raise ValueError("font already has a lowercase height axis")
    n_axes = len(axes)
    tags = [ax["Tag"] for ax in axes]
    font.customParameters["Axes"] = axes + [{"Name": NAME, "Tag": TAG}]
    mappings = font.customParameters["Axis Mappings"]
    if mappings is not None:
        font.customParameters["Axis Mappings"] = {**dict(mappings), TAG: {str(NORMAL): NORMAL, str(TALL): TALL}}

    def values(v, ytlc):
        return (list(v) + [0] * n_axes)[:n_axes] + [ytlc]

    def at_text_normal(m):
        v = (list(m.axes) + [0] * n_axes)[:n_axes]
        loc = dict(zip(tags, v))
        return loc.get("opsz", 14) == 14 and loc.get("GRAD", 0) == 0

    drawn = list(font.masters)
    weights = [m for m in drawn if at_text_normal(m)]
    twins = {}
    for m in drawn:
        m.axes = values(m.axes, NORMAL)
    for m in weights:
        twin = copy.deepcopy(m, {id(font): font})
        twin.id = f"{m.id}-TALLX"
        twin.name = f"{m.name} Tall x"
        twin.axes = values(m.axes[:n_axes], TALL)
        twin.xHeight = m.xHeight + round(FACTOR * m.xHeight)
        twins[m.id] = twin
    for twin in twins.values():
        font.masters.append(twin)         # also gives every glyph an empty layer for it
    for mid, twin in twins.items():       # kerning is stored per master: the twins keep theirs
        if mid in font.kerning:
            font.kerning[twin.id] = copy.deepcopy(font.kerning[mid])
    for inst in font.instances:
        inst.axes = values(inst.axes, NORMAL)

    # each weight's drawn optical sizes and grades: the tall twin adds to all of them
    def loc(m):
        return dict(zip(tags, (list(m.axes) + [0] * n_axes)[:n_axes]))

    family = {}
    for w in weights:
        lw = loc(w)
        same = [m for m in drawn if m is not w and loc(m).get("wght") == lw.get("wght")]
        family[w.id] = ([m.id for m in same if loc(m).get("GRAD", 0) == lw.get("GRAD", 0)],
                        [m.id for m in same if loc(m).get("opsz", 14) == lw.get("opsz", 14)])

    def corners(glyph, wid):
        """the glyph's outline points in the weight master and at every corner built on it"""
        def points(mid):
            layer = glyph.layers[mid]
            return [[(n.position.x, n.position.y) for n in p.nodes] for p in layer.paths] \
                if layer is not None else None

        base = points(wid)
        shape = [len(p) for p in base]
        out = [base]
        opsz, grade = family[wid]
        for o in [None] + opsz:
            for g in [None] + grade:
                if o is None and g is None:
                    continue
                po, pg = (points(o) if o else base), (points(g) if g else base)
                if po is None or pg is None or [len(p) for p in po] != shape or [len(p) for p in pg] != shape:
                    continue
                out.append([[(a[0] + b[0] - w[0], a[1] + b[1] - w[1]) for w, a, b in zip(cw, co, cg)]
                            for cw, co, cg in zip(base, po, pg)])
        return out

    by_id = {m.id: m for m in font.masters}
    kinds = {g.name: kind(g.name, font) for g in font.glyphs}
    origin = font.customParameters["Variable Font Origin"] or font.masters[0].id
    for g in font.glyphs:   # unencoded composites (g with dieresis) follow their base letter
        comps = g.layers[origin].components if g.layers[origin] is not None else []
        if kinds[g.name] is None and comps and kinds.get(comps[0].name) == "stretch":
            kinds[g.name] = "stretch"
    stats = {"stretch": 0, "mark": 0, "punct": 0, "compensated": 0, "accents": 0, "unresolved": set(),
             "strained": set()}
    shifts: dict[tuple[str, str], tuple[float, float]] = {}   # (glyph, twin master) -> rigid (dx, dy)
    made = []
    for glyph in font.glyphs:
        k = kinds[glyph.name]
        for layer in list(glyph.layers):
            master = layer.associatedMasterId or layer.layerId
            if master not in twins:
                continue
            new = copy.deepcopy(layer, {id(glyph): glyph, id(font): font})
            new.associatedMasterId = twins[master].id
            new.layerId = twins[master].id if layer.layerId == master else str(uuid.uuid4()).upper()
            m = by_id[master]
            mv_for = (lambda edges, limits=(), m=m: Move(m.xHeight, m.ascender, m.italicAngle or 0.0, edges, limits))
            if k in ("stretch", "mark", "punct") and new.paths:
                geos = corners(glyph, master) if k == "stretch" and layer.layerId == master else None
                dy = transform_layer(new, glyph.name, k, mv_for, shifts, geos, stats["strained"])
                stats[k] += layer.layerId == master
                if dy is not None and layer.layerId == master:
                    shifts[(glyph.name, twins[master].id)] = mv_for(None).shift(dy)
            elif k == "stretch" and new.anchors:
                mv = mv_for(None)
                for a in new.anchors:
                    a.position = mv.point(a.position.x, a.position.y)
            glyph.layers.append(new)
            made.append((glyph, layer, new, twins[master].id, master))

    # marks and punctuation built from other marks (the spacing acute, guillemets, oxia) follow
    # their parts; record how far they move, for the glyphs below that must cancel it
    for _ in range(3):
        for glyph, old, new, twin_id, master in made:
            if kinds[glyph.name] not in ("mark", "punct") or new.paths or not new.components \
                    or (glyph.name, twin_id) in shifts:
                continue
            vecs = []
            for comp in new.components:
                s = shifts.get((comp.name, twin_id))
                if s is None:
                    vecs = None
                    break
                t = comp.transform
                vecs.append((t[0] * s[0] + t[2] * s[1], t[1] * s[0] + t[3] * s[1]))
            if vecs and all(abs(v[0] - vecs[0][0]) < 1e-6 and abs(v[1] - vecs[0][1]) < 1e-6 for v in vecs):
                shifts[(glyph.name, twin_id)] = vecs[0]

    # components: glyphs that do not follow cancel the move of rigid parts they borrow; glyphs
    # that follow lift borrowed accents that are not marks (the comma of ģ)
    for glyph, old, new, twin_id, master in made:
        k = kinds[glyph.name]
        if k in ("mark", "punct"):
            continue
        m = by_id[master]
        mv = Move(m.xHeight, m.ascender, m.italicAngle or 0.0)
        for comp in new.components:
            shift = shifts.get((comp.name, twin_id))
            ck = kinds.get(comp.name)
            t = list(comp.transform)
            if k in ("stretch", "composite-only"):
                if k == "stretch" and ck is None and glyph.name not in KEEP_COMPONENTS and \
                        _above_x(font, comp, master, m.xHeight, mv.z0):
                    dx, dy = mv.shift(float(mv.d))
                    t[4] += dx
                    t[5] += dy
                    comp.transform = t
                    stats["accents"] += 1
                continue
            if shift is not None:
                # the shift as it appears through the component's own scale (Ấ scales its mark)
                t[4] -= t[0] * shift[0] + t[2] * shift[1]
                t[5] -= t[1] * shift[0] + t[3] * shift[1]
                comp.transform = t
                stats["compensated"] += 1
            elif ck == "stretch":
                stats["unresolved"].add(glyph.name)
    stats["kerning"] = tall_kerning(font, twins, family)
    stats["small sizes"] = small_sizes(font, twins, family, tags, values)
    return stats


# ----------------------------------------------------------------------------------------------
# small optical sizes and light weight: a taller x-height and a fuller default grade

OPSZ_RAISE = ((8, 0.78), (12, 0.60), (14, 0.51), (20, 0.0))   # share of the tall x-height at YTLC 500
# Masters at 8, 12, 14 and 20 pt put the x-height at 530 at 12 pt, 517 at 16 and the drawn 500
# from 20 pt up. YTLC 550 stays the tallest at every size (twins at 8, 12, 14, 20 and 72 pt), so
# the axis has less to add where the size already raised it.
LIGHT_GRADE = 0.25   # share of Grade 100 reached at GRAD 0 in Light (300) from 20 pt up
SMALL_GRADE = 0.90   # share of Grade 100 reached at GRAD 0 at 12 pt and below (every weight)


def grade_at(wght: float, opsz: float) -> float:
    """Share of Grade 100 that GRAD 0 reaches at (wght, opsz): 0.25 in Light from 20 pt up (0 at
    400 and 700), 0.90 at 12 pt and below, interpolated between 12 and 20 pt."""
    base = LIGHT_GRADE if wght == 300 else 0.0
    if opsz <= 12:
        return SMALL_GRADE
    if opsz >= 20:
        return base
    return base + (20 - opsz) / (20 - 12) * (SMALL_GRADE - base)


def _bracket(name):
    found = re.search(r"\[.*\]", name or "")
    return found.group(0) if found else None


def _master_layers(glyph, mid) -> dict:
    """a master's layer ('') and its bracket layers, by bracket"""
    out = {}
    for layer in glyph.layers:
        if layer.layerId == mid:
            out[""] = layer
        elif layer.associatedMasterId == mid and _bracket(layer.name):
            out[_bracket(layer.name)] = layer
    return out


def _delta(a, b) -> dict:
    """b - a for two compatible layers"""
    return {"paths": [[(nb.position.x - na.position.x, nb.position.y - na.position.y)
                       for na, nb in zip(pa.nodes, pb.nodes)] for pa, pb in zip(a.paths, b.paths)],
            "components": [[tb - ta for ta, tb in zip(ca.transform, cb.transform)]
                           for ca, cb in zip(a.components, b.components)],
            "anchors": {x.name: (y.position.x - x.position.x, y.position.y - x.position.y)
                        for x in a.anchors for y in b.anchors if y.name == x.name},
            "width": b.width - a.width}


def _apply(layer, d: dict, f: float = 1.0):
    """layer += f * d, in place, in whole units (so it compiles as an exact offset of the drawn
    master: no rounding jitter between the masters it is combined with)"""
    for p, dp in zip(layer.paths, d["paths"]):
        for n, (dx, dy) in zip(p.nodes, dp):
            n.position = Point(n.position.x + round(f * dx), n.position.y + round(f * dy))
    for c, dc in zip(layer.components, d["components"]):
        c.transform = [t + (round(f * x) if i >= 4 else f * x) for i, (t, x) in enumerate(zip(c.transform, dc))]
    for a in layer.anchors:
        if a.name in d["anchors"]:
            dx, dy = d["anchors"][a.name]
            a.position = Point(a.position.x + round(f * dx), a.position.y + round(f * dy))
    layer.width = layer.width + round(f * d["width"])


def _mix(layer, d: dict, t: float):
    """layer += t * d, in place (interpolation between masters: not in whole units)"""
    r = lambda v: round(v * 100) / 100
    for p, dp in zip(layer.paths, d["paths"]):
        for n, (dx, dy) in zip(p.nodes, dp):
            n.position = Point(r(n.position.x + t * dx), r(n.position.y + t * dy))
    for c, dc in zip(layer.components, d["components"]):
        c.transform = [x + t * y for x, y in zip(c.transform, dc)]
    for a in layer.anchors:
        if a.name in d["anchors"]:
            dx, dy = d["anchors"][a.name]
            a.position = Point(r(a.position.x + t * dx), r(a.position.y + t * dy))
    layer.width = r(layer.width + t * d["width"])


NO_EXTRA_GRADE = {"sigma.sups", "m.ss01", "peseta", "ringcomb", "hadescender-cy.sc", "zedescender-cy.sc"}


def _eoc_sig(layer):
    out = RecordingPen()
    pen = EraseOpenCornersPen(out)
    for path in layer.paths:
        path.draw(pen)
    return (pen.affected, len(out.value))


def _apply_grade(layer, d: dict, f: float, sig, gname: str = ""):
    """Apply f * d in place unless EraseOpenCornersPen would change segment count on this layer."""
    if gname in NO_EXTRA_GRADE:
        return
    if not layer.paths or not any(dx or dy for dp in d["paths"] for dx, dy in dp):
        _apply(layer, d, f)
        return
    pts0 = [[n.position for n in p.nodes] for p in layer.paths]
    comps0 = [list(c.transform) for c in layer.components]
    ancs0 = [a.position for a in layer.anchors]
    w0 = layer.width
    for s in (1.0, 0.85, 0.7, 0.5, 0.25):
        _apply(layer, d, s * f)
        if _eoc_sig(layer) == sig:
            return
        for p, p0 in zip(layer.paths, pts0):
            for n, pos0 in zip(p.nodes, p0):
                n.position = pos0
        for c, c0 in zip(layer.components, comps0):
            c.transform = list(c0)
        for a, a0 in zip(layer.anchors, ancs0):
            a.position = a0
        layer.width = w0


def _snap(base, twin):
    """twin = base + its change rounded to whole units"""
    d = _delta(base, twin)
    for p, pb, dp in zip(twin.paths, base.paths, d["paths"]):
        for n, nb, (dx, dy) in zip(p.nodes, pb.nodes, dp):
            n.position = Point(nb.position.x + round(dx), nb.position.y + round(dy))
    for c, cb, dc in zip(twin.components, base.components, d["components"]):
        c.transform = [x + (round(y) if i >= 4 else y) for i, (x, y) in enumerate(zip(cb.transform, dc))]
    for a in twin.anchors:
        for ab in base.anchors:
            if ab.name == a.name:
                a.position = Point(ab.position.x + round(a.position.x - ab.position.x),
                                   ab.position.y + round(a.position.y - ab.position.y))


def small_sizes(font, twins: dict, family: dict, tags: list, values) -> dict:
    """Raise the x-height of the small optical sizes with the tall twins' change (OPSZ_RAISE), add
    the masters that keep 20 pt and up at the drawn x-height and YTLC 550 the tallest everywhere."""
    lgroup = {g.name: g.rightKerningGroup for g in font.glyphs}
    rgroup = {g.name: g.leftKerningGroup for g in font.glyphs}

    def eff(kern, a, b):
        lefts = [a] + ([f"@MMK_L_{lgroup[a]}"] if not a.startswith("@") and lgroup.get(a) else [])
        rights = [b] + ([f"@MMK_R_{rgroup[b]}"] if not b.startswith("@") and rgroup.get(b) else [])
        for x in lefts:
            for y in rights:
                if y in kern.get(x, {}):
                    return kern[x][y]
        return 0

    def loc(m):
        return dict(zip(tags, m.axes))

    raise_at = dict(OPSZ_RAISE)
    by_id = {m.id: m for m in font.masters}
    stats = {"masters": 0}
    wgroups = []
    for wid, twin in twins.items():
        W = by_id[wid]
        wght = loc(W)["wght"]
        opsz_ids, grade_ids = family[wid]
        by_opsz = {loc(by_id[m])["opsz"]: by_id[m] for m in opsz_ids}
        by_grade = {loc(by_id[m])["GRAD"]: by_id[m] for m in grade_ids}
        C, D = by_opsz.get(min(by_opsz)), by_opsz.get(max(by_opsz))
        Gm50, G100 = by_grade.get(-50), by_grade.get(100)
        if C is None or D is None or loc(C)["opsz"] >= 14 or loc(D)["opsz"] <= 20:
            continue
        lo, hi = loc(W)["opsz"], loc(D)["opsz"]
        t12 = (12 - lo) / (loc(C)["opsz"] - lo)
        t20 = (20 - lo) / (hi - lo)
        kern_w, kern_t = font.kerning.get(wid, {}), font.kerning.get(twin.id, {})
        # the twin's kerning change, key by key, before anything moves
        dkern = {}
        for a, row in kern_t.items():
            for b, v in row.items():
                d = v - eff(kern_w, a, b)
                if d:
                    dkern[(a, b)] = d
        deltas, g100_deltas, gm50_deltas, eoc_sigs = {}, {}, {}, {}
        for g in font.glyphs:
            src, dst = _master_layers(g, wid), _master_layers(g, twin.id)
            for k in src:
                if k in dst:
                    _snap(src[k], dst[k])            # the twin moves in whole units too
                if src[k].paths:
                    eoc_sigs[(g.name, k)] = _eoc_sig(src[k])
            deltas[g.name] = {k: _delta(src[k], dst[k]) for k in src if k in dst}
            if G100 is not None:
                g100 = _master_layers(g, G100.id)
                g100_deltas[g.name] = {k: _delta(src[k], g100[k]) for k in src if k in g100}
            if Gm50 is not None:
                gm50 = _master_layers(g, Gm50.id)
                gm50_deltas[g.name] = {k: _delta(src[k], gm50[k]) for k in src if k in gm50}

        def new_master(base, mid, name, axes):
            m = copy.deepcopy(base, {id(font): font})
            m.id, m.name, m.axes = mid, name, axes
            font.masters.append(m)
            by_id[mid] = m
            stats["masters"] += 1
            return m

        def copy_layers(src_id, dst_id, delta=0.0, gdelta=None, mix=None):
            for g in font.glyphs:
                for k, layer in _master_layers(g, src_id).items():
                    new = copy.deepcopy(layer, {id(g): g, id(font): font})
                    new.associatedMasterId = dst_id
                    new.layerId = dst_id if k == "" else str(uuid.uuid4()).upper()
                    if mix is not None:                    # interpolate towards another master
                        other = _master_layers(g, mix[0]).get(k)
                        if other is not None:
                            _mix(new, _delta(layer, other), mix[1])
                    if delta and k in deltas[g.name]:
                        _apply(new, deltas[g.name][k], delta)
                    if gdelta is not None and k in gdelta[0].get(g.name, {}):
                        _apply_grade(new, gdelta[0][g.name][k], gdelta[1], eoc_sigs.get((g.name, k)), g.name)
                    g.layers.append(new)

        def interp_master(pt, suffix, other, t):
            M = new_master(W, f"{wid}-{suffix}", f"{W.name} {pt}pt", values([wght, pt, 0], NORMAL))
            for attr in ("xHeight", "capHeight", "ascender", "descender"):
                setattr(M, attr, round(getattr(W, attr) + t * (getattr(other, attr) - getattr(W, attr))))
            copy_layers(wid, M.id, mix=(other.id, t))
            keys = {(a, b) for k in (kern_w, font.kerning.get(other.id, {})) for a, row in k.items() for b in row}
            font.kerning[M.id] = {}
            for a, b in keys:
                kw, ko = eff(kern_w, a, b), eff(font.kerning.get(other.id, {}), a, b)
                font.kerning[M.id].setdefault(a, {})[b] = round(kw + t * (ko - kw))
            return M

        # 12 pt (where the small-size grade reaches 90) and 20 pt (where the drawn x-height and grade start)
        M12 = interp_master(12, "OPSZ12", C, t12)
        M20 = interp_master(20, "OPSZ20", D, t20)
        # YTLC 550 at every optical size: each size plus the twin's change (and its default grade)
        for base in (C, M12, M20, D):
            o = loc(base)["opsz"]
            gf = grade_at(wght, o)
            T = new_master(base, f"{base.id}-TALLX", f"{base.name} Tall x", values([wght, o, 0], TALL))
            T.xHeight = W.xHeight + round(FACTOR * W.xHeight) + (base.xHeight - W.xHeight)
            copy_layers(base.id, T.id, delta=1.0, gdelta=(g100_deltas, gf) if gf and G100 is not None else None)
            k = copy.deepcopy(font.kerning.get(base.id, {}))
            for (a, b), d in dkern.items():
                k.setdefault(a, {})[b] = eff(font.kerning.get(base.id, {}), a, b) + d
            font.kerning[T.id] = k
        # the small sizes themselves rise; their kerning opens where the raised lowercase crowds
        room, moved = PairRoom(font), _changed(font, wid, twin.id)
        kstats = {"keys": 0, "exceptions": 0, "new pairs": 0}
        for mid in [C.id, M12.id, wid]:
            f = raise_at.get(loc(by_id[mid])["opsz"], 0.0)
            if f:
                kern = font.kerning.setdefault(mid, {})
                room.open([(Shapes(font, wid, mid, twin.id), kern, f)], moved, copy.deepcopy(kern), kern, kstats)
        for mid in grade_ids:                              # grades keep the text master's kerning
            font.kerning[mid] = copy.deepcopy(font.kerning.get(wid, {}))
        # Grade -50 and +100 at every optical size: keep their full drawn reach wherever GRAD 0 was raised
        g_pairs = [(wid, mid) for mid in grade_ids]
        if Gm50 is not None and G100 is not None:
            for base in (C, M12, M20, D):
                o = loc(base)["opsz"]
                f = raise_at.get(o, 0.0)
                for gval, gname, gd in ((-50, "GM50", gm50_deltas), (100, "G100", g100_deltas)):
                    G = new_master(base, f"{base.id}-{gname}", f"{base.name} Grade {gval}",
                                   values([wght, o, gval], NORMAL))
                    G.xHeight = base.xHeight + round(f * FACTOR * W.xHeight)
                    copy_layers(base.id, G.id, delta=f, gdelta=(gd, 1.0))
                    font.kerning[G.id] = copy.deepcopy(font.kerning.get(base.id, {}))
                    g_pairs.append((base.id, G.id))
        stats.setdefault("kerning", []).append(kstats)
        for mid in [C.id, M12.id, wid] + grade_ids:
            m = by_id[mid]
            f = raise_at.get(loc(m)["opsz"], 0.0)
            if not f:
                continue
            m.xHeight = m.xHeight + round(f * FACTOR * W.xHeight)
            for g in font.glyphs:
                for k, layer in _master_layers(g, mid).items():
                    if k in deltas[g.name]:
                        _apply(layer, deltas[g.name][k], f)
        if G100 is not None:
            for mid in [C.id, M12.id, wid, M20.id, D.id, twin.id]:
                gf = grade_at(wght, loc(by_id[mid])["opsz"])
                if not gf:
                    continue
                for g in font.glyphs:
                    for k, layer in _master_layers(g, mid).items():
                        if k in g100_deltas[g.name]:
                            _apply_grade(layer, g100_deltas[g.name][k], gf, eoc_sigs.get((g.name, k)), g.name)
        wgroups.append((wid, twin.id, C.id, M12.id, M20.id, D.id, t12, t20, dkern, g_pairs))

    # Open any pair across all masters whose post-grade clearance dips below the optical-size floor
    room = PairRoom(font)
    for wid, twin_id, c_id, m12_id, m20_id, d_id, t12, t20, dkern, g_pairs in wgroups:
        for mid, floor in ((c_id, 20), (wid, 20), (d_id, 13), (f"{c_id}-TALLX", 20), (twin_id, 20), (f"{d_id}-TALLX", 13)):
            room.ensure_floor(Shapes(font, mid, mid, mid), font.kerning.setdefault(mid, {}), floor)
        kw_dict = font.kerning.get(wid, {})
        for m_id, other_id, t in ((m12_id, c_id, t12), (m20_id, d_id, t20)):
            ko_dict = font.kerning.get(other_id, {})
            keys = {(a, b) for k in (kw_dict, ko_dict) for a, row in k.items() for b in row}
            font.kerning[m_id] = {}
            for a, b in keys:
                kw, ko = eff(kw_dict, a, b), eff(ko_dict, a, b)
                font.kerning[m_id].setdefault(a, {})[b] = round(kw + t * (ko - kw))
            room.ensure_floor(Shapes(font, m_id, m_id, m_id), font.kerning.setdefault(m_id, {}), 20)
            kt = copy.deepcopy(font.kerning.get(m_id, {}))
            for (a, b), d in dkern.items():
                kt.setdefault(a, {})[b] = eff(font.kerning.get(m_id, {}), a, b) + d
            font.kerning[f"{m_id}-TALLX"] = kt
            room.ensure_floor(Shapes(font, f"{m_id}-TALLX", f"{m_id}-TALLX", f"{m_id}-TALLX"),
                              font.kerning.setdefault(f"{m_id}-TALLX", {}), 20)

    if any(m.italicAngle for m in font.masters):
        for m in font.masters:
            kdict = font.kerning.setdefault(m.id, {})
            kdict.setdefault("t", {})["quoteright"] = max(55, eff(kdict, "t", "quoteright"))
            kdict.setdefault("f", {})["otilde"] = eff(kdict, "f", "otilde") + 6
    all_kern_keys = {(a, b) for kdict in font.kerning.values() for a, row in kdict.items() for b in row}
    for m in font.masters:
        kdict = font.kerning.setdefault(m.id, {})
        for a, b in all_kern_keys:
            if b not in kdict.get(a, {}):
                kdict.setdefault(a, {})[b] = eff(kdict, a, b)
    for _, _, _, _, _, _, _, _, _, g_pairs in wgroups:
        for base_id, gmid in g_pairs:
            font.kerning[gmid] = copy.deepcopy(font.kerning.get(base_id, {}))
    return stats


def _above_x(font, comp, master, x_height, ceiling) -> bool:
    """whether a borrowed component sits above the x-height in its composite, as an accent on
    the lowercase does (not above an ascender, where the accents of ĺ ľ ḧ sit, which stay)"""
    g = font.glyphs[comp.name]
    if g is None:
        return False
    layer = g.layers[master]
    ys = [n.position.y for p in layer.paths for n in p.nodes]
    if not ys:
        return False
    t = comp.transform
    bottom = min(ys) * t[3] + t[5]
    return x_height + 10 < bottom < ceiling


# ----------------------------------------------------------------------------------------------
# kerning at the tall x-height

KERN_ROOM = 25         # at the tall x-height no pair comes closer than this (or than it was drawn)
KERN_FLOOR = 20        # ... and never closer than this, whatever the drawn pair does at its tips
KERN_STEP = 4          # scanline spacing of the clearance measure, font units
CHECKED_LEFT = "abcdefghijklmnopqrstuvwxyzß" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" ".,;:’'\")?!-"
CHECKED_RIGHT = CHECKED_LEFT + "".join(chr(u) for u in range(0xE0, 0x180) if chr(u).islower() and u != 0x17F)
JOINING = (".l", ".t", ".f")      # f forms that reach into the next letter on purpose


def _polyline(pts, steps: int = 12):
    """(x, y, node type) of a closed path -> points along it"""
    on = [i for i, p in enumerate(pts) if p[2] != "offcurve"]
    n, out = len(pts), []
    for k, i in enumerate(on):
        seg = [pts[(i + s) % n] for s in range(((on[(k + 1) % len(on)] - i) % n or n) + 1)]
        if len(seg) == 4:
            (x0, y0, _), (x1, y1, _), (x2, y2, _), (x3, y3, _) = seg
            for s in range(steps):
                t = s / steps
                u = 1 - t
                px = u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3
                py = u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3
                if s and py % KERN_STEP == 0:
                    py += 1e-9
                out.append((px, py))
        else:
            out.append(seg[0][:2])
    return out


class Shapes:
    """Glyph outlines at one optical-size master of a weight, with a share of the tall twin's
    change added (0: as drawn, 1: the corner the variable font makes at YTLC 550), and their
    profiles."""

    def __init__(self, font, weight: str, master: str, twin: str):
        self.font, self.w, self.o, self.t = font, weight, master, twin
        self._polys, self._prof = {}, {}

    def polys(self, name: str, share: float):
        key = (name, share)
        if key not in self._polys:
            g = self.font.glyphs[name]
            lo, lw, lt = g.layers[self.o], g.layers[self.w], g.layers[self.t]
            out = []
            for i, po in enumerate(lo.paths):
                pts = [(a.position.x + share * (c.position.x - b.position.x),
                        a.position.y + share * (c.position.y - b.position.y), a.type)
                       for a, b, c in zip(po.nodes, lw.paths[i].nodes, lt.paths[i].nodes)]
                if any(p[2] != "offcurve" for p in pts):
                    out.append(_polyline(pts))
            for i, co in enumerate(lo.components):
                t = [a + share * (c - b) for a, b, c in zip(co.transform, lw.components[i].transform,
                                                            lt.components[i].transform)]
                if self.font.glyphs[co.name] is None:
                    continue
                for poly in self.polys(co.name, share):
                    out.append([(t[0] * x + t[2] * y + t[4], t[1] * x + t[3] * y + t[5]) for x, y in poly])
            self._polys[key] = out
        return self._polys[key]

    def profile(self, name: str, share: float):
        """(leftmost, rightmost minus advance) ink on each scanline"""
        key = (name, share)
        if key not in self._prof:
            left, right = {}, {}
            for poly in self.polys(name, share):
                for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
                    if y0 == y1:
                        continue
                    lo, hi = (y0, y1) if y0 < y1 else (y1, y0)
                    for k in range(math.floor(lo / KERN_STEP) + 1, math.ceil(hi / KERN_STEP)):
                        y = k * KERN_STEP
                        x = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                        if y in left:
                            left[y] = min(left[y], x)
                            right[y] = max(right[y], x)
                        else:
                            left[y] = right[y] = x
            width = self.font.glyphs[name].layers[self.o].width
            self._prof[key] = (left, {y: x - width for y, x in right.items()})
        return self._prof[key]

    def gap(self, L: str, R: str, kern: float, share: float) -> float:
        over, ink = self.profile(L, share)[1], self.profile(R, share)[0]
        return min((ink[y] - over[y] for y in over if y in ink), default=math.inf) + kern


def _changed(font, weight: str, twin: str) -> dict:
    """glyph name -> whether the tall twin moved anything of it"""
    out = {}

    def moved(name):
        if name in out:
            return out[name]
        out[name] = False
        g = font.glyphs[name]
        if g is None:
            return False
        lw, lt = g.layers[weight], g.layers[twin]
        if lw is None or lt is None:
            return False
        res = any(abs(a.position.x - b.position.x) > 1e-6 or abs(a.position.y - b.position.y) > 1e-6
                  for pa, pb in zip(lw.paths, lt.paths) for a, b in zip(pa.nodes, pb.nodes))
        res = res or any(list(ca.transform) != list(cb.transform) or moved(ca.name)
                         for ca, cb in zip(lw.components, lt.components))
        out[name] = res
        return res

    for g in font.glyphs:
        moved(g.name)
    return out


class PairRoom:
    """The kerning classes and the letters the spacing check sets side by side, and how to open
    pairs that a taller lowercase crowds."""

    def __init__(self, font):
        self.font = font
        self.lgroup = {g.name: g.rightKerningGroup for g in font.glyphs}      # class as the left of a pair
        self.rgroup = {g.name: g.leftKerningGroup for g in font.glyphs}
        self.cmap = {}
        for g in font.glyphs:
            for u in (g.unicodes or []):
                self.cmap.setdefault(chr(int(u, 16)), g.name)
        self.f_skip_right = {"f", "i", "l", "j", "b", "h", "k", "t"}
        for cls in font.classes:
            if cls.name in ("f.l", "f.t") and cls.code:
                self.f_skip_right.update(cls.code.split())

    def lookup(self, kern, L, R):
        gl, gr = self.lgroup.get(L), self.rgroup.get(R)
        for a, b in ((L, R), (L, f"@MMK_R_{gr}" if gr else None), (f"@MMK_L_{gl}" if gl else None, R),
                     (f"@MMK_L_{gl}" if gl else None, f"@MMK_R_{gr}" if gr else None)):
            if a and b and b in kern.get(a, {}):
                return kern[a][b], (a, b)
        return 0, None

    def pairs(self, moved: dict | None = None):
        for cl in CHECKED_LEFT:
            L = self.cmap.get(cl)
            if L is None or (L.startswith("f") and L.endswith(JOINING)) or L == "Q":
                continue
            for cr in CHECKED_RIGHT:
                R = self.cmap.get(cr)
                if R is None:
                    continue
                if L == "f" and R in self.f_skip_right:
                    continue
                if moved is None or moved.get(L) or moved.get(R):
                    yield L, R

    def ensure_floor(self, shapes: Shapes, kern: dict, floor: float):
        """Ensure every non-joining pair in CHECKED_LEFT x CHECKED_RIGHT has gap >= floor in `kern`."""
        crowded = {}
        for L, R in self.pairs(None):
            k, key = self.lookup(kern, L, R)
            n = floor - shapes.gap(L, R, k, 0.0)
            if n > 0.25:
                crowded.setdefault(key, {})[(L, R)] = n
        for key, pairs in crowded.items():
            if key is None:
                for (L, R), n in pairs.items():
                    kern.setdefault(L, {})[R] = math.ceil(n)
                continue
            a, b = key
            value = kern[a][b]
            if b.startswith("@MMK_R_"):
                kl = a[7:] if a.startswith("@MMK_L_") else a
                base = max((n for (L, R), n in pairs.items() if R == b[7:] and L in (kl, a)), default=0.0)
            else:
                base = max(pairs.values())
            if base > 0.25:
                kern.setdefault(a, {})[b] = value + math.ceil(base)
            for R in sorted({R for _, R in pairs}):
                most = max(n for (L, R2), n in pairs.items() if R2 == R)
                if most > base + 0.5 and R != b:
                    kern.setdefault(a, {})[R] = value + math.ceil(most)
        # Second pass: resolve any remaining pair (e.g. L != kl when R == b[7:], or GPOS glyph-to-class vs class-to-glyph precedence)
        for L, R in self.pairs(None):
            gl, gr = self.lgroup.get(L), self.rgroup.get(R)
            # In ufo2ft GPOS ClassPairPos, (L, @MMK_R_gr) takes precedence over (@MMK_L_gl, R)
            k_l_gr = kern.get(L, {}).get(f"@MMK_R_{gr}") if gr else None
            k_gl_r = kern.get(f"@MMK_L_{gl}", {}).get(R) if gl else None
            k_l_r = kern.get(L, {}).get(R)
            if k_l_r is not None:
                k_eff = k_l_r
            elif k_l_gr is not None:
                k_eff = k_l_gr
            elif k_gl_r is not None:
                k_eff = k_gl_r
            else:
                k_eff = self.lookup(kern, L, R)[0]
            g = shapes.gap(L, R, k_eff, 0.0)
            if g < floor:
                kern.setdefault(L, {})[R] = k_eff + math.ceil(floor - g)

    def open(self, geometries, moved: dict, kern_ref: dict, kern_out: dict, stats: dict):
        """geometries: (shapes, kerning there, share of the tall change). Pairs that come closer
        than KERN_ROOM (or than drawn, never below KERN_FLOOR) at any of them open in kern_out,
        relative to kern_ref: a class pair as far as its own letters need (T with o), members
        that need more by exceptions."""
        crowded = {}                                       # kerning key (or None) -> {(L, R): need}
        for L, R in self.pairs(moved):
            n = 0.0
            for shapes, kern, share in geometries:
                k = self.lookup(kern, L, R)[0]
                target = max(KERN_FLOOR, min(KERN_ROOM, shapes.gap(L, R, k, 0.0)))
                n = max(n, target - shapes.gap(L, R, k, share))
            if n > 0.5:
                crowded.setdefault(self.lookup(kern_ref, L, R)[1], {})[(L, R)] = n
        for key, pairs in crowded.items():
            if key is None:                                # no kerning at all yet
                for (L, R), n in pairs.items():
                    kern_out.setdefault(L, {})[R] = math.ceil(n)
                    stats["new pairs"] += 1
                continue
            a, b = key
            value = kern_ref[a][b]
            if b.startswith("@MMK_R_"):
                kl = a[7:] if a.startswith("@MMK_L_") else a
                base = max((n for (L, R), n in pairs.items() if R == b[7:] and L in (kl, a)), default=0.0)
            else:
                base = max(pairs.values())
            if base > 0.5:
                kern_out.setdefault(a, {})[b] = value + math.ceil(base)
                stats["keys"] += 1
            for R in sorted({R for _, R in pairs}):
                most = max(n for (L, R2), n in pairs.items() if R2 == R)
                if most > base + 1 and R != b:
                    kern_out.setdefault(a, {})[R] = value + math.ceil(most)
                    stats["exceptions"] += 1


def tall_kerning(font, twins: dict, family: dict) -> dict:
    """In each tall twin, weaken the kerning of the pairs the taller lowercase would crowd (an
    accent rising under the bar of T or the arm of Y, the s rising under an apostrophe), just
    enough that no pair comes closer than KERN_ROOM, or than it does at the drawn x-height, at
    any optical size. Class pairs move for their own letters (T with o); members that need more
    get exceptions in the twin only, which the other masters resolve to their class values."""
    room = PairRoom(font)
    stats = {"keys": 0, "exceptions": 0, "new pairs": 0}
    for wid, twin in twins.items():
        masters = [wid] + family[wid][0]                  # the weight and its optical sizes
        geometries = [(Shapes(font, wid, o, twin.id), font.kerning.get(o, {}), 1.0) for o in masters]
        room.open(geometries, _changed(font, wid, twin.id), font.kerning.get(wid, {}),
                  font.kerning.setdefault(twin.id, {}), stats)
    return stats


def main(src: str, dst: str) -> None:
    font = glyphsLib.GSFont(src)
    stats = add_xheight_axis(font)
    font.save(dst)
    print(f"{len(font.masters)} masters; stretched {stats['stretch']}, marks {stats['mark']}, "
          f"punctuation {stats['punct']}; accents lifted {stats['accents']}, components compensated "
          f"{stats['compensated']}; non-following glyphs using stretched components: "
          f"{sorted(stats['unresolved'])[:30]}; squeezed past their limits: {sorted(stats['strained'])}; "
          f"tall-x kerning: {stats['kerning']}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
