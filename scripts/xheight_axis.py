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

    uv run python scripts/xheight_axis.py sources/Kerberos.glyphs out.glyphs
"""
from __future__ import annotations

import bisect
import copy
import math
import sys
import unicodedata
import uuid

import glyphsLib
from glyphsLib import glyphdata
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


def main(src: str, dst: str) -> None:
    font = glyphsLib.GSFont(src)
    stats = add_xheight_axis(font)
    font.save(dst)
    print(f"{len(font.masters)} masters; stretched {stats['stretch']}, marks {stats['mark']}, "
          f"punctuation {stats['punct']}; accents lifted {stats['accents']}, components compensated "
          f"{stats['compensated']}; non-following glyphs using stretched components: "
          f"{sorted(stats['unresolved'])[:30]}; squeezed past their limits: {sorted(stats['strained'])}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
