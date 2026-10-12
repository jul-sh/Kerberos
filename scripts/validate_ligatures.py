#!/usr/bin/env python3
"""Check Kerberos Roman's ff/ffi/ffl ligature behavior across its weight axis.

HarfBuzz checks word substitutions and mark composition; FreeType rasterizes
encoded ligatures at text and display sizes.
Run after rebuilding with ``uv run python scripts/validate_ligatures.py``.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import sys
import unicodedata

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from PIL import ImageFont
import uharfbuzz as hb

from build import VARIABLE, Style
from validate_fonts import Failed, Passed, check, require, set_axes, shape

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = (300, 400, 450, 550, 650, 700)
ENCODED = {"\ufb00": "f_f", "\ufb03": "f_f_i", "\ufb04": "f_f_l"}
WORDS = {
    "diff": ("d", "i", "f_f"),
    "difference": ("d", "i", "f_f", "e", "r", "e", "n", "c", "e"),
    "difficult": ("d", "i", "f_f_i", "c", "u", "l", "t"),
    "office": ("o", "f_f_i", "c", "e"),
    "affinity": ("a", "f_f_i", "n", "i", "t", "y"),
    "ffl": ("f_f_l",),
    "fff": ("f_f", "f"),
}


def signature(shaped: list[dict[str, int]]) -> list[tuple[int, ...]]:
    """Compare visible shaping while allowing equivalent text cluster indices."""
    return [tuple(item[key] for key in ("gid", "advance", "x", "y", "y_advance"))
            for item in shaped]


def encoding_check(font: TTFont, data: bytes) -> dict:
    cmap = font.getBestCmap()
    order = font.getGlyphOrder()
    evidence = {}
    for character, expected in ENCODED.items():
        codepoint = f"U+{ord(character):04X}"
        require(cmap.get(ord(character)) == expected,
                f"{codepoint} must map to {expected}; found {cmap.get(ord(character))}")
        for weight in WEIGHTS:
            shaped = shape(data, character, weight, {"liga": 0, "clig": 0})
            names = [order[item["gid"]] for item in shaped]
            require(names == [expected],
                    f"Encoded {codepoint} unexpectedly shapes to {names} at {weight}")
        evidence[codepoint] = expected
    return evidence


def mark_tables_check(font: TTFont) -> dict:
    require("GDEF" in font and font["GDEF"].table.GlyphClassDef is not None,
            "Missing GDEF glyph classification")
    classes = font["GDEF"].table.GlyphClassDef.classDefs
    expected_components = {"f_f": 2, "f_f_i": 3, "f_f_l": 3}
    for name in expected_components:
        require(classes.get(name) == 2,
                f"{name} must have GDEF ligature class 2; found {classes.get(name)}")
    require("GPOS" in font, "Missing mark positioning table")
    coverage = {name: [] for name in expected_components}
    for lookup_index, lookup in enumerate(font["GPOS"].table.LookupList.Lookup):
        match lookup.LookupType:
            case 5:
                subtables = lookup.SubTable
            case 9:
                subtables = [extension.ExtSubTable for extension in lookup.SubTable
                             if extension.ExtensionLookupType == 5]
            case _:
                continue
        for subtable in subtables:
            for name, attachment in zip(subtable.LigatureCoverage.glyphs,
                                        subtable.LigatureArray.LigatureAttach):
                if name not in expected_components:
                    continue
                require(attachment.ComponentCount == expected_components[name],
                        f"{name} has {attachment.ComponentCount} mark-attachment components; "
                        f"expected {expected_components[name]}")
                require(all(any(anchor is not None for anchor in component.LigatureAnchor)
                            for component in attachment.ComponentRecord),
                        f"{name} has an unanchored component in lookup {lookup_index}")
                coverage[name].append(lookup_index)
    for name, lookups in coverage.items():
        require(bool(lookups), f"{name} is absent from MarkToLigature positioning")
    return {name: {"gdef_class": classes[name], "mark_to_ligature_lookups": lookups}
            for name, lookups in coverage.items()}


def mark_positioning_check(font: TTFont, data: bytes, weight: int) -> dict:
    order = font.getGlyphOrder()
    acute = font.getBestCmap()[0x0301]
    hb_font = hb.Font(hb.Face(data))
    hb_font.set_variations({"wght": weight})
    hb.ot_font_set_funcs(hb_font)
    evidence = {}
    # Plain ffi + acute normalizes to ff + iacute before liga, so encoded ffi
    # exercises the third ligature component without testing normalization.
    samples = {"ff\u0301": "f_f", "\ufb00\u0301": "f_f",
               "\ufb03\u0301": "f_f_i", "\ufb04\u0301": "f_f_l"}
    for text, ligature in samples.items():
        on = shape(data, text, weight, {"mark": 1, "mkmk": 1})
        off = shape(data, text, weight, {"mark": 0, "mkmk": 0})
        names = [order[item["gid"]] for item in on]
        require(names == [ligature, acute],
                f"Expected {ligature} plus acute in {text!r} at {weight}; found {names}")
        require([item["gid"] for item in on] == [item["gid"] for item in off],
                f"Disabling marks unexpectedly changes glyphs in {text!r} at {weight}")
        mark_on, mark_off = on[-1], off[-1]
        require((mark_on["x"], mark_on["y"]) != (mark_off["x"], mark_off["y"]),
                f"No effective mark positioning in {text!r} at {weight}")
        require(mark_on["advance"] == 0 and mark_on["y_advance"] == 0,
                f"Combining acute advances the pen in {text!r} at {weight}")
        base_ink = hb_font.get_glyph_extents(on[0]["gid"])
        mark_ink = hb_font.get_glyph_extents(mark_on["gid"])
        require(base_ink is not None and mark_ink is not None,
                f"Missing ligature or acute ink extents in {text!r} at {weight}")
        clearance = (mark_ink.y_bearing + mark_ink.height + mark_on["y"]
                     - base_ink.y_bearing - on[0]["y"])
        require(clearance > 0,
                f"Acute enters the ligature hook band in {text!r} at {weight}: "
                f"{clearance} units clearance")
        evidence[text] = {"glyphs": names,
                          "mark_enabled_offset": [mark_on["x"], mark_on["y"]],
                          "mark_disabled_offset": [mark_off["x"], mark_off["y"]],
                          "acute_ink_clearance": clearance}
    return evidence


def words_check(font: TTFont, data: bytes, weight: int) -> dict:
    cmap = font.getBestCmap()
    order = font.getGlyphOrder()
    evidence = {}
    for text, expected in WORDS.items():
        on = shape(data, text, weight)
        names = tuple(order[item["gid"]] for item in on)
        require(names == expected,
                f"{text!r} shapes to {names}; expected {expected} at {weight}")
        require(all(item["gid"] and item["advance"] > 0 for item in on),
                f"Missing glyph or nonpositive letter advance in {text!r} at {weight}")
        require([item["cluster"] for item in on] == sorted(item["cluster"] for item in on),
                f"Reordered Latin clusters in {text!r} at {weight}")
        off = shape(data, text, weight, {"liga": 0, "clig": 0})
        off_names = tuple(order[item["gid"]] for item in off)
        expected_off = tuple(cmap[ord(character)] for character in text)
        require(off_names == expected_off,
                f"liga=0 fails to restore separate letters in {text!r} at {weight}: {off_names}")
        require(all(item["gid"] and item["advance"] > 0 for item in off),
                f"Invalid unligated letter advance in {text!r} at {weight}")
        evidence[text] = {"glyphs": list(names),
                          "advance": sum(item["advance"] for item in on),
                          "unligated_advance": sum(item["advance"] for item in off)}
    return evidence


def composition_check(font: TTFont, data: bytes, weight: int) -> dict:
    order = font.getGlyphOrder()
    evidence = {}
    # Compatibility ligature characters stay usable independently of liga,
    # and canonical accent decomposition must preserve glyph positioning.
    equivalents = (("diff", "di\ufb00"), ("difficult", "di\ufb03cult"),
                   ("office", "o\ufb03ce"), ("ffl", "\ufb04"),
                   ("díff", unicodedata.normalize("NFD", "díff")))
    for plain, equivalent in equivalents:
        left = shape(data, plain, weight)
        right = shape(data, equivalent, weight)
        require(all(item["gid"] for item in left + right),
                f"Missing glyph in equivalent samples {plain!r}/{equivalent!r} at {weight}")
        require(signature(left) == signature(right),
                f"Equivalent samples {plain!r}/{equivalent!r} shape differently at {weight}")
        evidence[equivalent] = [order[item["gid"]] for item in right]
    for text in ("f\u0301f", "ff\u0301", "fï", "ffï", "dîff", "dĩff"):
        shaped = shape(data, text, weight)
        require(all(item["gid"] for item in shaped),
                f"Missing glyph in accented context {text!r} at {weight}")
        require(sum(item["advance"] for item in shaped) > 0,
                f"Invalid advance in accented context {text!r} at {weight}")
        evidence[text] = [order[item["gid"]] for item in shaped]
    return evidence


def drawing_check(font: TTFont, data: bytes) -> dict:
    fingerprints = {name: set() for name in ENCODED.values()}
    evidence = {}
    for weight in WEIGHTS:
        glyphset = font.getGlyphSet(location={"wght": weight})
        raster_fonts = {size: ImageFont.truetype(BytesIO(data), size=size,
                       layout_engine=ImageFont.Layout.BASIC) for size in (24, 96)}
        for raster_font in raster_fonts.values():
            set_axes(raster_font, {"wght": weight})
        samples = {}
        for character, name in ENCODED.items():
            require(name in glyphset, f"Missing drawing {name} at {weight}")
            pen = BoundsPen(glyphset)
            glyphset[name].draw(pen)
            require(pen.bounds is not None, f"Empty outline {name} at {weight}")
            require(all(math.isfinite(value) for value in pen.bounds),
                    f"Nonfinite outline bounds {name} at {weight}")
            left, bottom, right, top = pen.bounds
            require(right > left and top > bottom,
                    f"Degenerate outline bounds {name} at {weight}: {pen.bounds}")
            require(top <= font["OS/2"].usWinAscent and -bottom <= font["OS/2"].usWinDescent,
                    f"Vertical metrics clip {name} at {weight}")
            rasters = {}
            for size, raster_font in raster_fonts.items():
                raster = raster_font.getmask(character, mode="L")
                require(raster.getbbox() is not None,
                        f"FreeType renders blank {name} at weight {weight}, size {size}")
                digest = sha256(bytes(raster)).hexdigest()
                rasters[str(size)] = {"size": list(raster.size), "sha256": digest}
                if size == 96:
                    fingerprints[name].add(digest)
            samples[name] = {"bounds": [round(value, 3) for value in pen.bounds],
                             "rasters": rasters}
        evidence[str(weight)] = samples
    for name, digests in fingerprints.items():
        require(len(digests) == len(WEIGHTS),
                f"{name} does not visibly vary at every sampled weight")
    return evidence


def validate(path: Path):
    data = path.read_bytes()
    with TTFont(BytesIO(data)) as font:
        family = font["name"].getDebugName(16) or font["name"].getDebugName(1)
        require(family == "Kerberos" and font["post"].italicAngle == 0,
                "This check expects the Kerberos Roman variable font")
        require("fvar" in font and [axis.axisTag for axis in font["fvar"].axes] == ["wght", "opsz", "GRAD"],
                "Expected the Kerberos Roman variable font (wght, opsz, GRAD)")
        return [check("encoded_ligatures", lambda: encoding_check(font, data)),
                check("ligature_mark_tables", lambda: mark_tables_check(font)),
                *[check(f"word_substitution_{weight}",
                        lambda weight=weight: words_check(font, data, weight)) for weight in WEIGHTS],
                *[check(f"accent_and_compatibility_{weight}",
                        lambda weight=weight: composition_check(font, data, weight)) for weight in WEIGHTS],
                *[check(f"ligature_mark_positioning_{weight}",
                        lambda weight=weight: mark_positioning_check(font, data, weight)) for weight in WEIGHTS],
                check("ligature_outlines_and_rasterization", lambda: drawing_check(font, data))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=VARIABLE[Style.ROMAN])
    parser.add_argument("--report", type=Path, default=ROOT / "build/ligature-validation.json")
    args = parser.parse_args()
    try:
        checks = validate(args.font)
    except Exception as exc:
        checks = [Failed("font_input", f"{type(exc).__name__}: {exc}")]
    passed = failed = 0
    for result in checks:
        match result:
            case Passed():
                passed += 1
            case Failed(name=name, error=error):
                failed += 1
                print(f"FAIL {name}: {error}")
    report = {"font": str(args.font.resolve()), "weights": list(WEIGHTS),
              "status": "failed" if failed else "passed", "passed_checks": passed,
              "failed_checks": failed, "checks": [asdict(result) for result in checks]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{passed} passed, {failed} failed. Report: {args.report}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
