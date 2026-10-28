"""Keep grade changes closer in color by borrowing a small weight adjustment.

Only grade endpoints move. Grade-zero masters, advances, anchors and kerning
stay fixed, including the existing optical-size compensation. The adjustment
uses the neighboring neutral weight drawings, centered in each original advance.
"""
from __future__ import annotations

import copy

from fontTools.pens.areaPen import AreaPen

from xheight_axis import _apply_grade, _delta, _eoc_sig, _master_layers

SAMPLE = "Hamburgefonstv"
REMAINING_COLOR_CHANGE = 0.15
MAX_WEIGHT_ADJUSTMENT = 60
# Deliberate overlaps and narrow joins can cross when shifted along the weight
# direction. Keep these forms at their drawn grades, including their components.
KEEP_DRAWN_GRADE = {"el-cy", "el-cy.sc", "lje-cy", "lje-cy.sc", "l.ss01", "f.f", "f.t"}


def compensate_grade(font) -> list[dict]:
    tags = [axis["Tag"] for axis in font.customParameters["Axes"]]
    locations = {master.id: dict(zip(tags, master.axes)) for master in font.masters}
    neutral = {tuple(sorted(locations[master.id].items())): master
               for master in font.masters if locations[master.id]["GRAD"] == 0}
    evidence = []

    def at_weight(location, weight):
        return neutral[tuple(sorted({**location, "wght": weight, "GRAD": 0}.items()))]

    def area(layers):
        total = 0.0
        for name in SAMPLE:
            pen = AreaPen(layers)
            layers[name].draw(pen)
            total += abs(pen.value)
        return total

    for master in font.masters:
        location = locations[master.id]
        if location["GRAD"] == 0:
            continue
        baseline = at_weight(location, location["wght"])
        base_layers = {glyph.name: glyph.layers[baseline.id] for glyph in font.glyphs}
        grade_layers = {glyph.name: glyph.layers[master.id] for glyph in font.glyphs}
        target, before = area(base_layers), area(grade_layers)
        # Select the adjacent interval in the direction of the compensation.
        # At Light and Bold, continue the nearest interval a short distance.
        weight = location["wght"]
        if weight == 300 or (weight == 400 and before > target):
            lower_weight, upper_weight = 300, 400
        else:
            lower_weight, upper_weight = 400, 700
        lower, upper = at_weight(location, lower_weight), at_weight(location, upper_weight)
        span = upper_weight - lower_weight
        deltas = {}
        signatures = {}
        for glyph in font.glyphs:
            lefts, rights = _master_layers(glyph, lower.id), _master_layers(glyph, upper.id)
            grades = _master_layers(glyph, master.id)
            for bracket, layer in grades.items():
                if bracket not in lefts or bracket not in rights:
                    continue
                left, right = lefts[bracket], rights[bracket]
                delta = _delta(left, right)
                width_change = delta["width"]
                # Recenter a heavier/lighter drawing inside its unchanged advance.
                delta["paths"] = [[(dx - width_change / 2, dy) for dx, dy in path]
                                  for path in delta["paths"]]
                for component, change in zip(left.components, delta["components"]):
                    child = font.glyphs[component.name]
                    child_change = child.layers[upper.id].width - child.layers[lower.id].width
                    change[4] += (component.transform[0] * child_change - width_change) / 2
                    change[5] += component.transform[1] * child_change / 2
                delta["width"] = 0
                delta["anchors"] = {}
                key = (glyph.name, bracket)
                deltas[key] = delta
                signatures[key] = _eoc_sig(layer)

        def trial(coefficient):
            layers = dict(grade_layers)
            for name in SAMPLE:
                original = grade_layers[name]
                layer = copy.deepcopy(original, {id(font): font, id(original.parent): original.parent})
                key = (name, "")
                _apply_grade(layer, deltas[key], coefficient, signatures[key], name)
                layers[name] = layer
            return area(layers)

        desired = target + REMAINING_COLOR_CHANGE * (before - target)
        limit = MAX_WEIGHT_ADJUSTMENT / span
        low, high = ((0.0, limit) if before < target else (-limit, 0.0))
        # Evaluate actual rounded, guarded outlines, including at the bounds.
        candidates = [(abs(trial(value) - desired), value) for value in (low, high, 0.0)]
        for _ in range(9):
            middle = (low + high) / 2
            actual = trial(middle)
            candidates.append((abs(actual - desired), middle))
            if actual < desired:
                low = middle
            else:
                high = middle
        _, coefficient = min(candidates)
        for glyph in font.glyphs:
            if glyph.name in KEEP_DRAWN_GRADE:
                continue
            for bracket, layer in _master_layers(glyph, master.id).items():
                key = (glyph.name, bracket)
                if key in deltas:
                    _apply_grade(layer, deltas[key], coefficient, signatures[key], glyph.name)
        after = area(grade_layers)
        evidence.append({**location, "weight_adjustment": round(coefficient * span, 2),
                         "original_ink_change": round(before / target - 1, 4),
                         "compensated_ink_change": round(after / target - 1, 4)})
    return evidence
