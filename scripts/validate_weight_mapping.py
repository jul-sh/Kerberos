#!/usr/bin/env python3
"""Compare built fonts with their pre-remapping compiler output.

Run after make build. Check every glyph at the fixed endpoints and new Regular,
plus intermediate weights across optical size, grade and lowercase height.
"""
from concurrent.futures import ProcessPoolExecutor
from itertools import product

from fontTools.ttLib import TTFont

from build import ROOT, VARIABLE, Style
from validate_fonts import _outline_coordinates, require, shape, SAMPLES


def validate_style(style: Style) -> str:
    original = TTFont(ROOT / "build" / style.value / f"{style.value}-raw.ttf")
    remapped = TTFont(VARIABLE[style])
    original_data = (ROOT / "build" / style.value / f"{style.value}-raw.ttf").read_bytes()
    remapped_data = VARIABLE[style].read_bytes()
    names = original.getGlyphOrder()
    maximum = 0.0
    locations = [dict(zip(("opsz", "GRAD", "YTLC"), values))
                 for values in product((6, 12, 72), (-50, 0, 100), (500, 550))]
    for weight in (300, 350, 400, 500, 600, 700):
        old_weight = (300 + (weight - 300) * 0.8 if weight <= 400
                      else 380 + (weight - 400) * 320 / 300)
        for location in locations:
            before = original.getGlyphSet(location={"wght": old_weight, **location})
            after = remapped.getGlyphSet(location={"wght": weight, **location})
            for name in names:
                require(abs(before[name].width - after[name].width) <= 1,
                        f"{style.value} {weight} {location} {name}: advance changed")
            old_topology, old_points = _outline_coordinates(before, names)
            new_topology, new_points = _outline_coordinates(after, names)
            require(old_topology == new_topology,
                    f"{style.value} {weight} {location}: topology changed")
            error = max(abs(a - b) for a, b in zip(old_points, new_points))
            maximum = max(maximum, error)
            # Master coordinates and variation supports are stored at font precision.
            require(error <= 2.0,
                    f"{style.value} {weight} {location}: coordinate error {error:.3f}")
            for sample in SAMPLES.values():
                old_shape = shape(original_data, sample, old_weight, location=location)
                new_shape = shape(remapped_data, sample, weight, location=location)
                require(len(old_shape) == len(new_shape), "Shaping length changed")
                for old, new in zip(old_shape, new_shape):
                    require(old["gid"] == new["gid"] and old["cluster"] == new["cluster"],
                            "Glyph substitution changed")
                    require(all(abs(old[key] - new[key]) <= 2
                                for key in ("advance", "x", "y", "y_advance")),
                            f"{style.value} {weight} {location}: shaping position changed")
    return (f"{style.value}: all glyphs at {len(locations) * 6} locations agree within font-unit rounding; "
            f"maximum coordinate difference {maximum:.3f} units")


def main() -> None:
    with ProcessPoolExecutor(max_workers=len(Style)) as pool:
        for result in pool.map(validate_style, Style):
            print(result, flush=True)


if __name__ == "__main__":
    main()
