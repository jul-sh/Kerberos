#!/usr/bin/env python3
"""Validate the built Kerberos variable fonts across axes, tables, shaping and outlines.

Run with ``uv run python scripts/validate_fonts.py`` from any directory.
The JSON report contains independent check results and the process exits nonzero
if any required behavior fails. This is a release smoke test, not a replacement
for proofreading the specimen or a full font QA suite.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
import sys
from typing import Callable, Literal

from concurrent.futures import ProcessPoolExecutor
import itertools

import glyphsLib
from fontTools.misc.roundTools import otRound
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.sfnt import calcChecksum
from PIL import ImageFont
import uharfbuzz as hb

from build import ROOT, VARIABLE, Style

WEIGHTS = (300, 400, 450, 500, 600, 700)
NAMED_WEIGHTS = (300, 400, 500, 600, 700)
AXIS_LIMITS = {"wght": (300, 400, 700), "opsz": (6, 12, 72), "GRAD": (-50, 0, 100), "YTLC": (500, 500, 550)}
AXIS_NAMES = {"Weight": "wght", "Optical Size": "opsz", "Grade": "GRAD", "Lowercase Height": "YTLC"}
FONT_FILES = tuple(VARIABLE[style].name for style in Style)
COVERAGE = {
    "Latin": (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞß"
        "àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿŒœŁłŠšŽž"
    ),
    "Greek": "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρσςτυφχψωάέήίόύώϊϋΐΰ",
    "Cyrillic": (
        "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюяІіЇїЄєҐґ"
    ),
}
SAMPLES = {
    "Latin": "The quiet office: naïve façade, fi fl Th — 0123456789; 12/345.",
    "Greek": "Η γρήγορη αλεπού. Ελληνικά: κόσμος, άνθρωπος, ζωή.",
    "Cyrillic": "Съешь ещё этих мягких французских булок. Українська мова.",
}


@dataclass(frozen=True)
class Passed:
    name: str
    evidence: dict
    status: Literal["passed"] = field(default="passed", init=False)


@dataclass(frozen=True)
class Failed:
    name: str
    error: str
    status: Literal["failed"] = field(default="failed", init=False)


CheckResult = Passed | Failed


class ValidationFailure(Exception):
    """A font did not satisfy an observable release requirement."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def check(name: str, operation: Callable[[], dict]) -> CheckResult:
    try:
        return Passed(name, operation())
    except Exception as exc:
        return Failed(name, f"{type(exc).__name__}: {exc}")


def set_axes(font: ImageFont.FreeTypeFont, location: dict[str, float]) -> None:
    """Set a FreeType (PIL) font's variation by axis tag; unset axes stay at their defaults."""
    coordinates = []
    for axis in font.get_variation_axes():
        name = axis["name"].decode() if isinstance(axis["name"], bytes) else axis["name"]
        coordinates.append(location.get(AXIS_NAMES[name], axis["default"]))
    font.set_variation_by_axes(coordinates)


def shape(data: bytes, text: str, weight: int = 400,
          features: dict[str, int] | None = None,
          location: dict[str, float] | None = None) -> list[dict[str, int]]:
    face = hb.Face(data)
    font = hb.Font(face)
    font.scale = (face.upem, face.upem)
    font.set_variations({"wght": weight, **(location or {})})
    hb.ot_font_set_funcs(font)
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    hb.shape(font, buffer, features or {})
    return [
        {"gid": info.codepoint, "cluster": info.cluster,
         "advance": position.x_advance, "x": position.x_offset,
         "y": position.y_offset, "y_advance": position.y_advance}
        for info, position in zip(buffer.glyph_infos, buffer.glyph_positions)
    ]


def gids(shaped: list[dict[str, int]]) -> list[int]:
    return [item["gid"] for item in shaped]


def axis_check(font: TTFont) -> dict:
    axes = font["fvar"].axes
    tags = [axis.axisTag for axis in axes]
    require(tags == list(AXIS_LIMITS), f"Expected axes {list(AXIS_LIMITS)}, found {tags}")
    limits = {axis.axisTag: (axis.minValue, axis.defaultValue, axis.maxValue) for axis in axes}
    require(limits == AXIS_LIMITS, f"Unexpected axis limits/defaults: {limits}")
    weights = sorted(instance.coordinates["wght"] for instance in font["fvar"].instances)
    require(weights == list(NAMED_WEIGHTS), f"Unexpected named instance weights: {weights}")
    instances = []
    for instance in font["fvar"].instances:
        require({k: v for k, v in instance.coordinates.items() if k != "wght"}
                == {"opsz": 12, "GRAD": 0, "YTLC": 500},
                f"Named instances sit at the text optical size, normal grade and drawn x-height: "
                f"{instance.coordinates}")
        name = font["name"].getDebugName(instance.subfamilyNameID)
        require(bool(name), "Named instance has no resolvable subfamily name")
        require("alegreya" not in name.lower(), f"Stale instance identity: {name}")
        entry = {"weight": instance.coordinates["wght"], "name": name}
        if instance.postscriptNameID != 0xFFFF:
            ps = font["name"].getDebugName(instance.postscriptNameID)
            require(bool(ps) and ps.startswith("Kerberos"), f"Unexpected instance PostScript name: {ps}")
            entry["postscript_name"] = ps
        instances.append(entry)
    return {"axes": limits, "instances": instances}


def names_check(font: TTFont) -> dict:
    table = font["name"]
    identity_ids = {1, 2, 3, 4, 6, 16, 17, 21, 22, 25}
    identity = {}
    for record in table.names:
        if record.nameID not in identity_ids:
            continue
        value = record.toUnicode()
        require("alegreya" not in value.lower(), f"Stale Alegreya identity in name ID {record.nameID}: {value}")
        identity.setdefault(str(record.nameID), set()).add(value)
    family = table.getDebugName(16) or table.getDebugName(1)
    require(family == "Kerberos", f"Unexpected family: {family}")
    for name_id in (1, 3, 4, 6):
        value = table.getDebugName(name_id)
        require(bool(value) and "kerberos" in value.lower(), f"Name ID {name_id} does not identify Kerberos: {value}")
    postscript = table.getDebugName(6)
    require(bool(re.fullmatch(r"[!-~]+", postscript)), f"Invalid PostScript name: {postscript}")
    require(not any(char in postscript for char in " [](){}<>/%"), f"Invalid PostScript name: {postscript}")
    copyright_text = table.getDebugName(0) or ""
    require("The Alegreya Project Authors" in copyright_text and "The Crimson Pro Project Authors" in copyright_text,
            "Upstream copyright attribution must be retained in name ID 0")
    require("Open Font License" in (table.getDebugName(13) or ""), "Missing OFL license metadata")
    return {"identity_names": {key: sorted(values) for key, values in identity.items()},
            "copyright": copyright_text}


def text_drawing_check(font: TTFont, source_path: Path) -> dict:
    """At 12 pt, Regular and Bold use their drawn text masters, with no size compensation.
    Light intentionally keeps its separate grade correction and is checked in grade_check."""
    source = glyphsLib.GSFont(str(source_path))
    require(source.upm == font["head"].unitsPerEm, "Source and compiled font units differ")
    tags = [axis["Tag"] for axis in source.customParameters["Axes"]]
    evidence = {}
    for weight in (400, 700):
        masters = [master for master in source.masters
                   if dict(zip(tags, list(master.axes) + [0] * len(tags)))
                   == {"wght": weight, "opsz": 12, "GRAD": 0}]
        require(len(masters) == 1, f"Expected one drawn 12 pt source master at weight {weight}")
        layers = {glyph.name: glyph.layers[masters[0].id] for glyph in source.glyphs}
        glyphset = font.getGlyphSet(location={"wght": weight, "opsz": 12, "GRAD": 0, "YTLC": 500})
        measurements = {}
        for name in ("H", "n", "o", "x"):
            layer = layers[name]
            require(glyphset[name].width == otRound(layer.width),
                    f"12 pt {name} width differs from its drawn source at weight {weight}: "
                    f"{glyphset[name].width} vs {layer.width}")
            drawn, compiled = BoundsPen(layers), BoundsPen(glyphset)
            layer.draw(drawn)
            glyphset[name].draw(compiled)
            require(drawn.bounds is not None and compiled.bounds is not None,
                    f"Empty 12 pt {name} outline at weight {weight}")
            error = max(abs(a - b) for a, b in zip(drawn.bounds, compiled.bounds))
            require(error <= 1.0,
                    f"12 pt {name} outline differs from its drawn source at weight {weight}: "
                    f"{compiled.bounds} vs {drawn.bounds}")
            measurements[name] = {"width": glyphset[name].width, "max_bound_error": round(error, 3)}
        evidence[str(weight)] = measurements
    return evidence


def style_check(font: TTFont, italic: bool) -> dict:
    selection = font["OS/2"].fsSelection
    mac_style = font["head"].macStyle
    angle = font["post"].italicAngle
    require(bool(selection & 1) == italic, "OS/2 italic bit disagrees with the face")
    require(bool(mac_style & 2) == italic, "head italic bit disagrees with the face")
    require(not selection & (1 << 9), "The italic design must not be labeled oblique")
    require(not selection & (1 << 5) and not mac_style & 1, "Default 400 face must not carry bold flags")
    require(bool(selection & (1 << 6)) == (not italic), "OS/2 regular flag disagrees with the face")
    require((angle < 0) if italic else (angle == 0), f"Unexpected italic angle: {angle}")
    require(font["OS/2"].usWeightClass == 400, "Default OS/2 weight class must be 400")
    subfamily = font["name"].getDebugName(17) or font["name"].getDebugName(2) or ""
    require(("italic" in subfamily.lower()) == italic, f"Unexpected subfamily: {subfamily}")
    return {"italic": italic, "fsSelection": selection, "macStyle": mac_style,
            "italic_angle": angle, "subfamily": subfamily}


def metrics_check(font: TTFont) -> dict:
    os2 = font["OS/2"]
    post = font["post"]
    require(os2.achVendID == "NONE", f"Unexpected vendor identity: {os2.achVendID!r}")
    require(post.underlinePosition < 0,
            f"Underline must sit below the baseline: {post.underlinePosition}")
    require(post.underlineThickness > 0,
            f"Underline must have positive thickness: {post.underlineThickness}")
    require(os2.usWinAscent > 0 and os2.usWinDescent > 0,
            "Windows vertical metrics must reserve space above and below the baseline")
    return {"vendor": os2.achVendID, "underline_position": post.underlinePosition,
            "underline_thickness": post.underlineThickness,
            "windows_ascent": os2.usWinAscent, "windows_descent": os2.usWinDescent}


def tables_check(font: TTFont) -> dict:
    required = {"cmap", "glyf", "loca", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post",
                "fvar", "gvar", "STAT", "GSUB", "GPOS", "GDEF"}
    require(required <= set(font.keys()), f"Missing tables: {sorted(required - set(font.keys()))}")
    features = {}
    for tag in ("GSUB", "GPOS"):
        feature_list = font[tag].table.FeatureList
        features[tag] = sorted({record.FeatureTag for record in feature_list.FeatureRecord})
    require({"liga", "smcp", "onum", "lnum", "tnum", "frac"} <= set(features["GSUB"]),
            f"Missing required substitution features: {features['GSUB']}")
    require({"kern", "mark", "mkmk"} <= set(features["GPOS"]),
            f"Missing required positioning features: {features['GPOS']}")
    return {"tables": sorted(font.keys()), "features": features}


def coverage_check(font: TTFont, data: bytes) -> dict:
    cmap = font.getBestCmap()
    evidence = {}
    for script, alphabet in COVERAGE.items():
        missing = sorted({f"U+{ord(char):04X}" for char in alphabet if ord(char) not in cmap})
        require(not missing, f"Missing {script} characters: {', '.join(missing)}")
        counts = {}
        for weight in WEIGHTS:
            shaped = shape(data, SAMPLES[script], weight)
            require(shaped and all(gids(shaped)), f"Missing glyph in {script} shaping at weight {weight}")
            require(sum(item["advance"] for item in shaped) > 0,
                    f"Nonpositive text advance for {script} at weight {weight}")
            counts[str(weight)] = len(shaped)
        evidence[script] = {"required_characters": len(set(alphabet)), "shaped_glyph_counts": counts}
    return {"encoded_characters": len(cmap), "scripts": evidence}


def features_check(data: bytes) -> dict:
    evidence = {}
    for weight in (400, 600, 700):
        weight_result = {}
        off = shape(data, "office affinity fi fl Th", weight, {"liga": 0, "clig": 0})
        on = shape(data, "office affinity fi fl Th", weight, {"liga": 1, "clig": 0})
        require(all(gids(on)) and len(on) < len(off), f"liga does not form ligatures at {weight}")
        weight_result["liga"] = {"disabled_glyphs": len(off), "enabled_glyphs": len(on)}

        off = shape(data, "aenorz", weight, {"smcp": 0})
        on = shape(data, "aenorz", weight, {"smcp": 1})
        require(len(on) == len(off) == 6 and all(gids(on))
                and all(a != b for a, b in zip(gids(off), gids(on))),
                f"smcp does not substitute every sample lowercase at {weight}")
        weight_result["smcp"] = {"substituted_glyphs": len(on)}

        oldstyle = shape(data, "0123456789", weight, {"onum": 1, "lnum": 0, "tnum": 0})
        lining = shape(data, "0123456789", weight, {"lnum": 1, "onum": 0, "tnum": 0})
        require(len(oldstyle) == len(lining) == 10 and all(gids(oldstyle)) and all(gids(lining))
                and gids(oldstyle) != gids(lining), f"onum/lnum do not select distinct figure sets at {weight}")
        weight_result["onum_lnum"] = {"distinct_figure_glyphs": sum(a != b for a, b in zip(gids(oldstyle), gids(lining)))}
        tabular_result = {}
        for figure in ("lnum", "onum"):
            tabular = shape(data, "0123456789", weight,
                            {"lnum": int(figure == "lnum"), "onum": int(figure == "onum"), "tnum": 1, "kern": 0})
            widths = {item["advance"] for item in tabular}
            require(len(tabular) == 10 and all(gids(tabular)) and len(widths) == 1 and min(widths) > 0,
                    f"tnum {figure} widths are not equal at {weight}: {sorted(widths)}")
            tabular_result[figure] = next(iter(widths))
        weight_result["tnum"] = tabular_result

        off = shape(data, "12/345", weight, {"frac": 0})
        on = shape(data, "12/345", weight, {"frac": 1})
        require(len(on) == len(off) == 6 and all(gids(on)) and gids(off) != gids(on),
                f"frac does not substitute fraction glyphs at {weight}")
        fraction_advance = sum(item["advance"] for item in on)
        regular_advance = sum(item["advance"] for item in off)
        require(0 < fraction_advance < regular_advance, f"frac does not produce a compact fraction at {weight}")
        hb_font = hb.Font(hb.Face(data))
        hb.ot_font_set_funcs(hb_font)
        hb_font.set_variations({"wght": weight})
        numerator = [hb_font.get_glyph_extents(item["gid"]).y_bearing + item["y"] for item in on[:2]]
        denominator = [hb_font.get_glyph_extents(item["gid"]).y_bearing + item["y"] for item in on[3:]]
        require(min(numerator) > max(denominator), f"Fraction numerators are not above denominators at {weight}")
        weight_result["frac"] = {"regular_advance": regular_advance, "fraction_advance": fraction_advance}
        evidence[str(weight)] = weight_result
    return evidence


def default_figures_check(data: bytes) -> dict:
    """Default digits are oldstyle; lnum selects lining and numeric GSUB takes both."""
    evidence = {}
    digits = "0123456789"
    for weight in (400, 600, 700):
        default = shape(data, digits, weight)
        lining = shape(data, digits, weight, {"lnum": 1, "onum": 0, "tnum": 0})
        oldstyle = shape(data, digits, weight, {"onum": 1, "lnum": 0, "tnum": 0})
        require(len(default) == len(lining) == len(oldstyle) == 10
                and all(gids(default)) and all(gids(oldstyle)),
                f"Default or alternate figure set is incomplete at {weight}")
        require(default == oldstyle,
                f"Default digits differ from explicitly selected oldstyle figures at {weight}")
        require(all(a != b for a, b in zip(gids(default), gids(lining))),
                f"lnum does not select a lining alternate for every default digit at {weight}")
        numeric_features = {}
        for feature in ("numr", "dnom", "sups", "subs"):
            alternate = shape(data, digits, weight, {feature: 1})
            require(len(alternate) == 10 and all(gids(alternate))
                    and all(a != b for a, b in zip(gids(default), gids(alternate))),
                    f"{feature} does not substitute every default digit at {weight}")
            numeric_features[feature] = {"substituted_glyphs": len(alternate),
                                         "glyph_ids": gids(alternate)}
        evidence[str(weight)] = {"default_matches_onum": True,
                                 "lining_alternates": len(lining),
                                 "numeric_features": numeric_features}
    return evidence


def marks_check(font: TTFont, data: bytes) -> dict:
    # q has no precomposed acute/circumflex form, so these exercise GPOS rather
    # than only Unicode normalization to a precomposed character.
    probes = ("q\u0301", "q\u0302\u0301", "q\u0323\u0301",
              "g\u030b", "g\u0323\u0301", "Ж\u0301", "t\u0302")
    evidence = {}
    for weight in WEIGHTS:
        attached = []
        for text in probes:
            off = shape(data, text, weight, {"mark": 0, "mkmk": 0})
            on = shape(data, text, weight, {"mark": 1, "mkmk": 1})
            require(all(gids(on)) and len(on) >= 2, f"Combining mark missing at {weight}: {text!r}")
            require(all(item["advance"] == 0 for item in on[1:]),
                    f"Combining mark unexpectedly advances at {weight}: {text!r}")
            if on != off and any(item["x"] or item["y"] for item in on[1:]):
                attached.append(text)
        require(len(attached) == len(probes),
                f"GPOS did not attach every mark probe at {weight}: {attached}")
        # Check that the circumflex clears the Roman t ascender tip, not only
        # that GPOS attaches the mark.
        shaped_t = shape(data, "t\u0302", weight)
        require(len(shaped_t) == 2, f"Expected separate t and circumflex at {weight}")
        hb_font = hb.Font(hb.Face(data))
        hb.ot_font_set_funcs(hb_font)
        hb_font.set_variations({"wght": weight})
        base, mark = [hb_font.get_glyph_extents(item["gid"]) for item in shaped_t]
        require(base is not None and mark is not None,
                f"Missing t/circumflex outline extents at {weight}")
        clearance = (mark.y_bearing + mark.height + shaped_t[1]["y"]
                     - base.y_bearing - shaped_t[0]["y"])
        # This vertical band check applies to Roman; slanted Italic contours
        # can clear horizontally even when their y ranges overlap.
        if font["post"].italicAngle == 0:
            require(clearance > 0,
                    f"Circumflex enters the roman t ascender band at {weight}: clearance {clearance}")
        evidence[str(weight)] = {"positioned_samples": attached,
                                 "t_circumflex_clearance": clearance}
    return evidence


def outlines_check(font: TTFont, data: bytes) -> dict:
    cmap = font.getBestCmap()
    encoded_names = set(cmap.values())
    fingerprints = {character: [] for character in "Hon"}
    raster_hashes = []
    evidence = {}
    for weight in WEIGHTS:
        glyphset = font.getGlyphSet(location={"wght": weight})
        nonempty = 0
        encoded_nonempty = 0
        bounds_union = None
        # Include unencoded alternates, small caps and combining glyphs: they
        # can exceed the extents of characters addressed directly by cmap.
        for name in glyphset:
            pen = BoundsPen(glyphset)
            glyphset[name].draw(pen)
            if pen.bounds is None:
                continue
            require(all(math.isfinite(value) for value in pen.bounds),
                    f"Nonfinite outline in {name} at {weight}")
            x_min, y_min, x_max, y_max = pen.bounds
            require(x_min <= x_max and y_min <= y_max, f"Invalid outline bounds in {name} at {weight}")
            nonempty += 1
            if name in encoded_names:
                encoded_nonempty += 1
            if bounds_union is None:
                bounds_union = list(pen.bounds)
            else:
                bounds_union = [min(bounds_union[0], x_min), min(bounds_union[1], y_min),
                                max(bounds_union[2], x_max), max(bounds_union[3], y_max)]
        require(encoded_nonempty > 500,
                f"Too few drawable encoded glyphs at {weight}: {encoded_nonempty}")
        require(bounds_union[3] <= font["OS/2"].usWinAscent,
                f"Windows ascent {font['OS/2'].usWinAscent} clips true glyph ymax "
                f"{bounds_union[3]:.3f} at weight {weight}")
        require(-bounds_union[1] <= font["OS/2"].usWinDescent,
                f"Windows descent {font['OS/2'].usWinDescent} clips true glyph ymin "
                f"{bounds_union[1]:.3f} at weight {weight}")
        for character in fingerprints:
            pen = DecomposingRecordingPen(glyphset)
            glyphset[cmap[ord(character)]].draw(pen)
            fingerprints[character].append(sha256(repr(pen.value).encode()).hexdigest())
        raster_font = ImageFont.truetype(BytesIO(data), size=42, layout_engine=ImageFont.Layout.BASIC)
        set_axes(raster_font, {"wght": weight})
        raster = raster_font.getmask("Honeymoon 0123456789 Ελληνικά Українська", mode="L")
        require(raster.getbbox() is not None and raster.size[0] > 0 and raster.size[1] > 0,
                f"FreeType produced an empty raster at {weight}")
        digest = sha256(bytes(raster)).hexdigest()
        raster_hashes.append(digest)
        evidence[str(weight)] = {"drawable_encoded_glyphs": encoded_nonempty,
                                 "drawable_glyphs": nonempty,
                                 "all_outline_bounds": [round(value, 3) for value in bounds_union],
                                 "raster_size": list(raster.size), "raster_sha256": digest}
    for character, hashes in fingerprints.items():
        require(len(set(hashes)) == len(WEIGHTS), f"{character} does not vary between every tested weight")
    require(len(set(raster_hashes)) == len(WEIGHTS), "Rendered sample does not vary between every tested weight")
    return {"weights": evidence, "independently_varying_outlines": sorted(fingerprints)}


def roundtrip_check(font: TTFont, data: bytes) -> dict:
    require(calcChecksum(data) == 0xB1B0AFBA, "Original font checksum is invalid")
    font.ensureDecompiled()
    output = BytesIO()
    font.save(output)
    saved = output.getvalue()
    require(calcChecksum(saved) == 0xB1B0AFBA, "Roundtrip font checksum is invalid")
    reopened = TTFont(BytesIO(saved), lazy=False, checkChecksums=2)
    try:
        reopened.ensureDecompiled()
        require(set(reopened.keys()) == set(font.keys()), "Table set changed on save/reopen")
        require(reopened.getBestCmap() == font.getBestCmap(), "Character mapping changed on save/reopen")
        require(reopened.getGlyphOrder() == font.getGlyphOrder(), "Glyph order changed on save/reopen")
        for weight in WEIGHTS:
            for sample in SAMPLES.values():
                require(shape(data, sample, weight) == shape(saved, sample, weight),
                        f"Shaping changed after save/reopen at {weight}")
    finally:
        reopened.close()
    return {"source_bytes": len(data), "roundtrip_bytes": len(saved),
            "shaping_comparisons": len(WEIGHTS) * len(SAMPLES), "checksum": "0xB1B0AFBA"}


def composite_width_check(font: TTFont) -> dict:
    """A redrawn base must not leave its fixed accented composites at old widths."""
    pairs = {'A': 'ÁÀÄÂÅ', 'C': 'ÇĆ', 'E': 'ÉÈËÊ', 'G': 'ĞĢ',
             'H': 'Ĥ', 'M': 'Ḿ', 'N': 'ÑŃ', 'O': 'ÓÒÖÔ',
             'R': 'ŔŘ', 'S': 'ŚŠ', 'U': 'ÚÙÜÛ', 'W': 'ẂŴ', 'Y': 'ÝŸ',
             'a': 'áàäâå', 'c': 'çć', 'e': 'éèëê', 'g': 'ğģ',
             'n': 'ñń', 'o': 'óòöô', 'u': 'úùüû', 'y': 'ýÿ'}
    cmap = font.getBestCmap()
    count = 0
    for weight in (400, 600, 700):
        glyphset = font.getGlyphSet(location={'wght': weight})
        for base, accented in pairs.items():
            expected = glyphset[cmap[ord(base)]].width
            for character in accented:
                if ord(character) not in cmap:
                    continue  # Some extended combinations are provided by mark shaping.
                actual = glyphset[cmap[ord(character)]].width
                require(abs(actual - expected) <= 1,
                        f'{character} width {actual:.2f} differs from {base} {expected:.2f} at {weight}')
                count += 1
    return {'checked_pairs_across_weights': count, 'weights': [400, 600, 700]}


class _Polylines(BasePen):
    """Closed contours of a glyph as polylines (curves flattened)."""

    def __init__(self, glyphset):
        super().__init__(glyphset)
        self.polys, self.current = [], []

    def _moveTo(self, p):
        self.current = [p]

    def _lineTo(self, p):
        self.current.append(p)

    def _qCurveToOne(self, a, b):
        p0 = self.current[-1]
        for k in range(1, 7):
            t = k / 6
            self.current.append(tuple((1 - t) ** 2 * p0[i] + 2 * (1 - t) * t * a[i] + t * t * b[i]
                                      for i in (0, 1)))

    def _curveToOne(self, a, b, c):
        p0 = self.current[-1]
        for k in range(1, 9):
            t = k / 8
            self.current.append(tuple((1 - t) ** 3 * p0[i] + 3 * (1 - t) ** 2 * t * a[i]
                                      + 3 * (1 - t) * t * t * b[i] + t ** 3 * c[i] for i in (0, 1)))

    def _closePath(self):
        if self.current and self.current[-1] == self.current[0]:
            self.current.pop()
        if len(self.current) > 2:
            self.polys.append(self.current)
        self.current = []

    _endPath = _closePath


LOOP_AREA = 25  # square units: a self-intersection that encloses less is invisible (rounding)


def _contours(glyphset, name: str) -> list[tuple[float, int, int]]:
    """(signed area, self-intersections, visible ones) of each contour of a simple glyph."""
    pen = _Polylines(glyphset)
    glyphset[name].draw(pen)
    out = []

    def shoelace(points):
        return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1])) / 2

    for poly in pen.polys:
        n = len(poly)
        edges = sorted((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), i, a, b)
                       for i, (a, b) in enumerate(zip(poly, poly[1:] + poly[:1])) if a != b)

        def orient(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        crossings, visible, active = 0, 0, []
        for e in edges:
            active = [g for g in active if g[1] >= e[0]]
            for g in active:
                if g[3] < e[2] or e[3] < g[2] or abs(e[4] - g[4]) in (0, 1, n - 1):
                    continue
                a, b, c, d = e[5], e[6], g[5], g[6]
                if orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0:
                    i, j = sorted((e[4], g[4]))
                    loop = min(abs(shoelace(poly[i + 1:j + 1])), abs(shoelace(poly[j + 1:] + poly[:i + 1])))
                    crossings += 1
                    visible += loop >= LOOP_AREA
            active.append(e)
        out.append((shoelace(poly), crossings, visible))
    return out


def _corner_problems(data: bytes, corner: dict[str, float]) -> list[str]:
    """Simple glyphs at a corner versus the drawn masters: orientation and area against the
    same weight at text size and normal grade; visible self-intersections against all those
    any drawn weight has (overlaps drawn on purpose, in some letters in one master only)."""
    font = TTFont(BytesIO(data))
    glyf = font["glyf"]
    drawn = {w: font.getGlyphSet(location={"wght": w, "opsz": 12, "GRAD": 0}) for w in (300, 400, 700)}
    glyphset = font.getGlyphSet(location=corner)
    label = ",".join(f"{tag}={value:g}" for tag, value in corner.items())
    problems = []
    for name in font.getGlyphOrder():
        if glyf[name].numberOfContours <= 0:
            continue
        references = {w: _contours(gs, name) for w, gs in drawn.items()}
        before, after = references[corner["wght"]], _contours(glyphset, name)
        if len(before) != len(after):
            problems.append(f"{name} @ {label}: {len(after)} contours, {len(before)} drawn")
            continue
        for k, ((a0, _, _), (a1, _, x1)) in enumerate(zip(before, after)):
            if (a1 > 0) != (a0 > 0) or abs(a1) < 0.1 * abs(a0):
                problems.append(f"{name}#{k} @ {label}: contour area {a1:.0f}, drawn {a0:.0f}")
            x0 = max(ref[k][1] for ref in references.values())
            if x1 > x0:
                problems.append(f"{name}#{k} @ {label}: {x1} self-intersections, drawn {x0}")
    return problems


def corner_outlines_check(data: bytes) -> dict:
    """Every contour keeps its orientation, a tenth of its area and its drawn topology at all
    51 corners where optical size, grade or lowercase height leave their defaults, at each
    drawn weight."""
    corners = [{"wght": w, "opsz": o, "GRAD": g, "YTLC": y}
               for w, o, g, y in itertools.product((300, 400, 700), (6, 12, 72), (-50, 0, 100), (500, 550))
               if (o, g, y) != (12, 0, 500)]
    with ProcessPoolExecutor() as pool:
        problems = [line for found in pool.map(_corner_problems, [data] * len(corners), corners)
                    for line in found]
    require(not problems, f"{len(problems)} outline problems, e.g. {problems[:5]}")
    return {"corners": len(corners)}


def _ink(glyphset, names: list[str]) -> float:
    total = 0.0
    for name in names:
        pen = AreaPen(glyphset)
        glyphset[name].draw(pen)
        total += abs(pen.value)
    return total


def _runs(glyphset, name: str) -> dict[str, float]:
    """Stem of l (the first ink run across its middle) or hairline of o (the thinnest top
    run over the middle of its width, where an oblique stress puts it), with nonzero winding
    so overlapping contours count once."""
    pen = _Polylines(glyphset)
    glyphset[name].draw(pen)

    def ink_runs(axis: str, at: float) -> list[float]:
        hits = []
        for poly in pen.polys:
            for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
                if axis == "y" and (y0 <= at < y1 or y1 <= at < y0):
                    hits.append((x0 + (at - y0) * (x1 - x0) / (y1 - y0), 1 if y1 > y0 else -1))
                if axis == "x" and (x0 <= at < x1 or x1 <= at < x0):
                    hits.append((y0 + (at - x0) * (y1 - y0) / (x1 - x0), 1 if x1 > x0 else -1))
        runs, winding, start = [], 0, None
        for value, sign in sorted(hits):
            before, winding = winding, winding + sign
            if before == 0 and winding:
                start = value
            elif before and not winding:
                runs.append(value - start)
        return runs

    bounds = BoundsPen(glyphset)
    glyphset[name].draw(bounds)
    x_min, y_min, x_max, y_max = bounds.bounds
    if name == "l":
        return {"stem": ink_runs("y", (y_min + y_max) / 2)[0]}
    width = x_max - x_min
    return {"hairline": min(ink_runs("x", x_min + width * (0.3 + 0.4 * k / 16))[-1] for k in range(17))}


def _outline_coordinates(glyphset, names: list[str]) -> tuple[list, list[float]]:
    topology, coordinates = [], []
    for name in names:
        pen = DecomposingRecordingPen(glyphset)
        glyphset[name].draw(pen)
        topology.append((name, [(op, [point is None for point in args]) for op, args in pen.value]))
        coordinates.extend(value for _, args in pen.value for point in args
                           if point is not None for value in point)
    return topology, coordinates


DEFAULT_GRADE = {
    300: {6: 0.50, 8: 5 / 12, 10: 1 / 3, 11: 7 / 24,
          12: 0.25, 14: 0.25, 16: 0.25, 20: 0.25, 72: 0.25},
    350: {6: 0.50, 8: 0.375, 10: 0.25, 11: 0.1875,
          12: 0.125, 14: 0.125, 16: 0.125, 20: 0.125, 72: 0.125},
    400: {6: 0.50, 8: 1 / 3, 10: 1 / 6, 11: 1 / 12,
          12: 0.0, 14: 0.0, 16: 0.0, 20: 0.0, 72: 0.0},
    550: {6: 0.50, 8: 1 / 3, 10: 1 / 6, 11: 1 / 12,
          12: 0.0, 14: 0.0, 16: 0.0, 20: 0.0, 72: 0.0},
    700: {6: 0.50, 8: 1 / 3, 10: 1 / 6, 11: 1 / 12,
          12: 0.0, 14: 0.0, 16: 0.0, 20: 0.0, 72: 0.0},
}


def grade_check(font: TTFont, data: bytes) -> dict:
    """Grade is layout-neutral (identical advances and shaping; a thin stroke at a glyph's
    edge changes about its centre line, so ink extents move by at most half a hairline's
    change) and works on contrast: -50 gives the hairlines Dutch-baroque contrast with the stems
    unchanged, +100 thickens both. At every weight, GRAD 0 reaches half the positive grade
    at 6 pt; the size-dependent boost tapers away by 12 pt. The separate Light correction
    remains grade 25 (interpolated to 0 at weight 400) at 12 pt and above."""
    cmap = font.getBestCmap()
    letters = [cmap[ord(c)] for c in "Hamburgefonstiv"]
    l, o = cmap[ord("l")], cmap[ord("o")]
    samples = list(SAMPLES.values()) + ["AVATAR Wolf, To Ty P. — \u201cquoted\u201d"]
    evidence = {}
    for weight, opsz in ((300, 6), (400, 12), (400, 14), (700, 72), (400, 72), (700, 6)):
        base_loc = {"wght": weight, "opsz": opsz, "GRAD": 0}
        base = font.getGlyphSet(location=base_loc)
        ink = {0: _ink(base, letters)}
        extents = 0.0
        for grade in (-50, 100):
            gs = font.getGlyphSet(location={**base_loc, "GRAD": grade})
            moved = [name for name in font.getGlyphOrder() if gs[name].width != base[name].width]
            require(not moved, f"GRAD {grade} changes advances at {base_loc}: {moved[:5]}")
            for text in samples:
                require(shape(data, text, weight, location={"opsz": opsz, "GRAD": grade})
                        == shape(data, text, weight, location={"opsz": opsz, "GRAD": 0}),
                        f"GRAD {grade} changes shaping at {base_loc}: {text!r}")
            for name in letters:
                b0, b1 = BoundsPen(base), BoundsPen(gs)
                base[name].draw(b0)
                gs[name].draw(b1)
                extents = max(extents, abs(b1.bounds[0] - b0.bounds[0]), abs(b1.bounds[2] - b0.bounds[2]))
            ink[grade] = _ink(gs, letters)
        require(ink[-50] < ink[0] < ink[100], f"Grade does not order the colour at {base_loc}: {ink}")
        require(extents < 32, f"Grade moves ink extents by {extents:.1f} units at {base_loc}")
        evidence[f"{weight}/{opsz}"] = {"ink": {str(k): round(v) for k, v in ink.items()},
                                        "max_extent_shift": round(extents, 1)}
    runs = {g: {**_runs(font.getGlyphSet(location={"wght": 400, "opsz": 12, "GRAD": g}), l),
                **_runs(font.getGlyphSet(location={"wght": 400, "opsz": 12, "GRAD": g}), o)}
            for g in (-50, 0, 100)}
    require(abs(runs[-50]["stem"] - runs[0]["stem"]) <= 0.04 * runs[0]["stem"],
            f"GRAD -50 must keep the stems: {runs}")
    require(runs[-50]["hairline"] <= 0.75 * runs[0]["hairline"], f"GRAD -50 must thin the hairlines: {runs}")
    require(runs[-50]["hairline"] <= 0.4 * runs[-50]["stem"],
            f"GRAD -50 hairlines must have Dutch-baroque contrast (at most 0.4 of the stem): {runs}")
    require(runs[100]["stem"] > runs[0]["stem"] and runs[100]["hairline"] >= 1.2 * runs[0]["hairline"],
            f"GRAD +100 must thicken stems and hairlines: {runs}")
    evidence["strokes"] = {str(g): {k: round(v, 1) for k, v in r.items()} for g, r in runs.items()}
    for weight, targets in DEFAULT_GRADE.items():
        def movement_at(opsz):
            outlines = [_outline_coordinates(font.getGlyphSet(
                location={"wght": weight, "opsz": opsz, "GRAD": grade}), letters) for grade in (0, 100)]
            require(outlines[0][0] == outlines[1][0],
                    f"grade changes outline topology at {opsz} pt, weight {weight}")
            return outlines[0][0], [b - a for a, b in zip(outlines[0][1], outlines[1][1])]

        base_share = targets[12]
        topology, base_movement = movement_at(12)
        full = [value / (1.0 - base_share) for value in base_movement]
        magnitude = sum(value * value for value in full)
        require(magnitude > 0, f"positive grade has no outline movement at weight {weight}")
        shares = {}
        for opsz, want in targets.items():
            local_topology, movement = movement_at(opsz)
            require(local_topology == topology, f"optical size changes grade topology at {opsz} pt, weight {weight}")
            # Grade adds point offsets. Their ink area varies with optical geometry, so project
            # the remaining offsets onto the full grade instead of comparing ink differences.
            share = 1.0 - sum(a * b for a, b in zip(movement, full)) / magnitude
            shares[opsz] = round(share, 2)
            require(abs(share - want) <= 0.04,
                    f"default grade at {opsz} pt, weight {weight}: {share:.2f} of Grade 100, want {want}")
        evidence[f"{weight} default grade"] = shares
    return evidence


def optical_size_check(font: TTFont) -> dict:
    """Small sizes are larger on the body, wider and lower in contrast; display sizes are
    narrower and finer; stems stay close."""
    cmap = font.getBestCmap()
    x, n, o, l = cmap[ord("x")], cmap[ord("n")], cmap[ord("o")], cmap[ord("l")]
    measured = {}
    for opsz in (6, 8, 12, 14, 72):
        gs = font.getGlyphSet(location={"wght": 400, "opsz": opsz, "GRAD": 0})
        bounds = BoundsPen(gs)
        gs[x].draw(bounds)
        measured[opsz] = {"x_height": bounds.bounds[3], "n_advance": gs[n].width,
                          **_runs(gs, l), **_runs(gs, o)}
    m6, m8, m12, m72 = (measured[o] for o in (6, 8, 12, 72))
    require(m6["x_height"] > m8["x_height"] > m12["x_height"], f"small-size x-height order: {measured}")
    # Neutral text and display x-heights can differ by sub-unit curve rounding after compilation.
    require(m12["x_height"] + 1 >= m72["x_height"], f"text/display x-height order: {measured}")
    require(m6["n_advance"] > m8["n_advance"] > m12["n_advance"] > m72["n_advance"], f"width order: {measured}")
    require(m6["hairline"] > m8["hairline"] > m12["hairline"] > m72["hairline"], f"hairline order: {measured}")
    for opsz in (6, 8, 72):
        require(abs(measured[opsz]["stem"] - m12["stem"]) <= 0.14 * m12["stem"],
                f"stems must stay within 14% across optical sizes: {measured}")
    return {str(k): {name: round(v, 1) for name, v in values.items()} for k, values in measured.items()}


def _top(glyphset, name: str) -> tuple[float, float]:
    """bottom and top of a glyph's ink"""
    pen = BoundsPen(glyphset)
    glyphset[name].draw(pen)
    return (pen.bounds[1], pen.bounds[3]) if pen.bounds else (0.0, 0.0)


SMALL_SIZE_RAISE = {6: 0.50, 8: 1 / 3, 10: 1 / 6, 11: 1 / 12,
                    12: 0.0, 14: 0.0, 16: 0.0, 20: 0.0, 30: 0.0, 72: 0.0}   # share of the tall x-height


def xheight_check(font: TTFont) -> dict:
    """The lowercase height axis keeps every advance and moves only what sits on the x-height:
    at YTLC 550 the x-height is 8-12% taller, accents, dots, small caps and oldstyle figures
    follow, while capitals (also those that share marks with the lowercase), ascenders and
    descenders stay where they are. Optical size adds up to half the tall x-height at 6 pt,
    tapering to no added boost at 12 pt and above; YTLC 550 stays the tallest at every size."""
    cmap = font.getBestCmap()
    order = set(font.getGlyphOrder())
    g = lambda ch: cmap[ord(ch)]
    evidence = {}
    for weight, opsz in ((300, 12), (400, 12), (700, 12), (400, 20),
                         (400, 72), (400, 14), (400, 6), (400, 8)):
        loc = {"wght": weight, "opsz": opsz, "GRAD": 0}
        sets = {y: font.getGlyphSet(location={**loc, "YTLC": y}) for y in (500, 550)}
        moved = [n for n in font.getGlyphOrder() if sets[500][n].width != sets[550][n].width]
        require(not moved, f"YTLC 550 changes advances at {loc}: {moved[:5]}")
        for ch in "HAOl":     # capitals and ascenders stay
            require(_top(sets[500], g(ch)) == _top(sets[550], g(ch)), f"YTLC moves {ch} at {loc}")
        require(_top(sets[500], g("p"))[0] == _top(sets[550], g("p"))[0], f"YTLC moves the descender at {loc}")
        for ch in "ÁẤẢ":      # capitals whose marks the lowercase shares too
            if ord(ch) in cmap:
                a, b = _top(sets[500], g(ch)), _top(sets[550], g(ch))
                require(abs(a[1] - b[1]) < 1, f"YTLC moves the accent of {ch} at {loc}: {a} -> {b}")
        (_, x0), (_, x1) = _top(sets[500], g("x")), _top(sets[550], g("x"))
        rise = x1 - x0
        if opsz < 12:         # the size raised it already: the axis adds the rest
            evidence[f"{weight}/{opsz}"] = {"x_height": [round(x0), round(x1)]}
            continue
        require(0.08 * x0 <= rise <= 0.12 * x0, f"x-height at YTLC 550 not 8-12% taller at {loc}: {x0} -> {x1}")
        follows = {}
        for ch in "áoiö:":    # accents and dots go up with the x-height, the colon's upper dot too
            a, b = _top(sets[500], g(ch)), _top(sets[550], g(ch))
            follows[ch] = round(b[1] - a[1], 1)
            require(0.5 * rise <= b[1] - a[1] <= 1.15 * rise, f"{ch} does not follow the x-height at {loc}: "
                                                             f"top {a[1]} -> {b[1]}, x rise {rise:.1f}")
        hyphen = [_top(sets[y], g("-")) for y in (500, 550)]
        require(0.3 * rise <= hyphen[1][0] - hyphen[0][0] <= 0.7 * rise, f"hyphen not centred on the taller "
                                                                         f"x-height at {loc}: {hyphen}")
        for name in ("a.sc", "zero"):   # small caps and oldstyle figures grow with it
            if name in order:
                a, b = _top(sets[500], name), _top(sets[550], name)
                require(0.7 * rise <= b[1] - a[1] <= 1.15 * rise, f"{name} does not follow at {loc}: {a} -> {b}")
        evidence[f"{weight}/{opsz}"] = {"x_height": [round(x0), round(x1)], "follow": follows}
    # small sizes: the share of the tall x-height reached at YTLC 500 (the tall one less the
    # axis's full rise, measured where the size adds nothing, is the drawn one)
    for weight in (300, 400, 700):
        top = lambda opsz, y: _top(font.getGlyphSet(location={"wght": weight, "opsz": opsz, "GRAD": 0,
                                                              "YTLC": y}), g("x"))[1]
        full = top(12, 550) - top(12, 500)
        shares = {}
        for opsz, want in SMALL_SIZE_RAISE.items():
            share = (top(opsz, 500) - (top(opsz, 550) - full)) / full
            shares[opsz] = round(share, 2)
            require(abs(share - want) <= 0.06, f"x-height at {opsz} pt, weight {weight}: {share:.2f} of the "
                                               f"tall x-height at YTLC 500, want {want}")
        evidence[f"{weight} small sizes"] = shares
    return evidence


def validate(path: Path) -> list[CheckResult]:
    try:
        data = path.read_bytes()
        font = TTFont(BytesIO(data), lazy=False, checkChecksums=2)
    except Exception as exc:
        return [Failed("load_font", f"{type(exc).__name__}: {exc}")]
    try:
        return [
            check("axes_and_instances", lambda: axis_check(font)),
            check("font_identity_and_license", lambda: names_check(font)),
            check("text_size_matches_drawn_masters", lambda: text_drawing_check(
                font, ROOT / "sources" / ("Kerberos-Italic.glyphs" if "-Italic" in path.name else "Kerberos.glyphs"))),
            check("style_metadata", lambda: style_check(font, "-Italic" in path.name)),
            check("vertical_metrics_and_vendor", lambda: metrics_check(font)),
            check("layout_and_variation_tables", lambda: tables_check(font)),
            check("script_coverage_and_shaping", lambda: coverage_check(font, data)),
            check("functional_opentype_features", lambda: features_check(data)),
            check("default_lining_and_numeric_features", lambda: default_figures_check(data)),
            check("combining_mark_positioning", lambda: marks_check(font, data)),
            check("accented_base_advance_consistency", lambda: composite_width_check(font)),
            check("intermediate_outlines_and_rasterization", lambda: outlines_check(font, data)),
            check("grade_layout_and_contrast", lambda: grade_check(font, data)),
            check("optical_size_proportions_and_contrast", lambda: optical_size_check(font)),
            check("lowercase_height_axis", lambda: xheight_check(font)),
            check("outlines_at_design_space_corners", lambda: corner_outlines_check(data)),
            check("compile_save_reopen", lambda: roundtrip_check(font, data)),
        ]
    finally:
        font.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path, default=ROOT / "fonts" / "variable")
    parser.add_argument("--report", type=Path, default=ROOT / "build" / "validation.json")
    args = parser.parse_args()
    reports = []
    failed = 0
    passed = 0
    for filename in FONT_FILES:
        path = args.font_dir / filename
        print(f"Validating {filename} ...", flush=True)
        checks = validate(path)
        for result in checks:
            match result:
                case Passed():
                    passed += 1
                case Failed(name=name, error=error):
                    failed += 1
                    print(f"  FAIL {name}: {error}", flush=True)
        reports.append({"file": str(path.resolve()), "checks": [asdict(result) for result in checks]})
    report = {"status": "failed" if failed else "passed", "passed_checks": passed,
              "failed_checks": failed, "fonts": reports}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{passed} passed, {failed} failed. Report: {args.report}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
