#!/usr/bin/env python3
"""Validate the built Kerberos variable, SerifOnly, and static fonts.

Run with ``uv run python scripts/validate_fonts.py`` from any directory.
The JSON report contains independent check results and the process exits nonzero
if any required behavior fails. This is a release smoke test, not a replacement
for proofreading the specimen or a full font QA suite.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
import enum
from hashlib import sha256
from io import BytesIO
import itertools
import json
import math
from pathlib import Path
import re
import sys
from typing import Callable, Literal

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

from build import (
    FAMILY,
    JOINED_LIGATURES,
    ROOT,
    VARIABLE,
    VARIABLE_SERIFONLY,
    VERSION,
    Style,
    WidthFamily,
)

WEIGHTS = (300, 400, 450, 500, 600, 700)
NAMED_WEIGHTS = (300, 400, 500, 600, 700)
AXIS_LIMITS = {
    "wght": (300, 400, 700),
    "opsz": (6, 12, 72),
    "GRAD": (-50, 0, 100),
    "YTLC": (500, 500, 550),
    "CALE": (0, 0, 100),
}
AXIS_NAMES = {
    "Weight": "wght",
    "Optical Size": "opsz",
    "Grade": "GRAD",
    "Lowercase Height": "YTLC",
    "Caleuche": "CALE",
}
REQUIRED_COPYRIGHT_AUTHORS = (
    "The Kerberos Project Authors",
    "The Alegreya Project Authors",
    "The Crimson Pro Project Authors",
    "The Iosevka Project Authors",
    "The Iosevka Charon Project Authors",
)
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


class StaticWeight(enum.Enum):
    LIGHT = (300, "Light")
    REGULAR = (400, "Regular")
    BOLD = (700, "Bold")

    def __init__(self, weight_value: int, weight_label: str) -> None:
        self.weight_value = weight_value
        self.weight_label = weight_label


class RibbiSubfamily(enum.Enum):
    REGULAR = ("Regular", 0x40, 0x0)
    BOLD = ("Bold", 0x20, 0x1)
    ITALIC = ("Italic", 0x01, 0x2)
    BOLD_ITALIC = ("Bold Italic", 0x21, 0x3)

    def __init__(self, label: str, fs_selection_bits: int, mac_style_bits: int) -> None:
        self.label = label
        self.fs_selection_bits = fs_selection_bits
        self.mac_style_bits = mac_style_bits

    @classmethod
    def resolve(cls, style: Style, weight: StaticWeight) -> RibbiSubfamily:
        match (style, weight):
            case (Style.ROMAN, StaticWeight.LIGHT | StaticWeight.REGULAR):
                return cls.REGULAR
            case (Style.ROMAN, StaticWeight.BOLD):
                return cls.BOLD
            case (Style.ITALIC, StaticWeight.LIGHT | StaticWeight.REGULAR):
                return cls.ITALIC
            case (Style.ITALIC, StaticWeight.BOLD):
                return cls.BOLD_ITALIC


@dataclass(frozen=True)
class VariableFontTarget:
    style: Style
    width_family: WidthFamily
    path: Path


@dataclass(frozen=True)
class StaticFontTarget:
    style: Style
    weight: StaticWeight
    path: Path


FontTarget = VariableFontTarget | StaticFontTarget


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


def shape(data: bytes, text: str, weight: int | None = 400,
          features: dict[str, int] | None = None,
          location: dict[str, float] | None = None) -> list[dict[str, int]]:
    face = hb.Face(data)
    font = hb.Font(face)
    font.scale = (face.upem, face.upem)
    variations = dict(location or {})
    if weight is not None:
        variations = {"wght": weight, **variations}
    if variations:
        font.set_variations(variations)
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


def _fixture_key_for_style(style: Style) -> str:
    match style:
        case Style.ROMAN:
            return "Kerberos[GRAD,YTLC,opsz,wght].ttf"
        case Style.ITALIC:
            return "Kerberos-Italic[GRAD,YTLC,opsz,wght].ttf"


def _verify_copyright_and_license(table) -> str:
    copyright_text = table.getDebugName(0) or ""
    missing = [author for author in REQUIRED_COPYRIGHT_AUTHORS if author not in copyright_text]
    require(
        not missing,
        f"Upstream copyright attribution missing in name ID 0: {missing}; got {copyright_text!r}",
    )
    require(
        len(copyright_text) <= 500,
        f"name ID 0 exceeds 500-char Google Fonts limit ({len(copyright_text)} chars)",
    )
    require("Open Font License" in (table.getDebugName(13) or ""), "Missing OFL license metadata")
    return copyright_text


def ofl_license_file_check(ofl_path: Path) -> dict:
    require(ofl_path.exists(), f"Missing {ofl_path}")
    text = ofl_path.read_text(encoding="utf-8")
    missing = [author for author in REQUIRED_COPYRIGHT_AUTHORS if author not in text]
    require(not missing, f"OFL.txt missing upstream copyright attributions: {missing}")
    require("SIL OPEN FONT LICENSE Version 1.1" in text, "OFL.txt missing SIL OFL 1.1 body")
    return {"path": str(ofl_path.relative_to(ROOT)), "authors_verified": list(REQUIRED_COPYRIGHT_AUTHORS)}


def cross_file_identity_check(targets: list[FontTarget]) -> dict:
    """Assert distinct family, full, PostScript, and variation-PS names across variable/ and serifonly/."""
    var_targets = [t for t in targets if isinstance(t, VariableFontTarget)]
    static_targets = [t for t in targets if isinstance(t, StaticFontTarget)]

    families_by_mode: dict[WidthFamily, set[str]] = {
        WidthFamily.PROPORTIONAL: set(),
        WidthFamily.SERIF_ONLY: set(),
    }
    var_id3: dict[str, str] = {}
    var_id4: dict[str, str] = {}
    var_id6: dict[str, str] = {}
    var_id25: dict[str, str] = {}
    instance_ps_names: dict[str, str] = {}

    for target in var_targets:
        require(target.path.exists(), f"Missing variable font: {target.path}")
        label = str(target.path.relative_to(target.path.parents[2]))
        with TTFont(target.path, lazy=True) as font:
            nm = font["name"]
            fam = nm.getDebugName(16) or nm.getDebugName(1) or ""
            id3 = nm.getDebugName(3) or ""
            id4 = nm.getDebugName(4) or ""
            id6 = nm.getDebugName(6) or ""
            id25 = nm.getDebugName(25) or ""
            families_by_mode[target.width_family].add(fam)
            for slot, val, desc in (
                (var_id3, id3, "nameID 3"),
                (var_id4, id4, "nameID 4"),
                (var_id6, id6, "nameID 6"),
                (var_id25, id25, "nameID 25"),
            ):
                require(bool(val), f"Empty {desc} in {label}")
                require(
                    val not in slot,
                    f"Duplicate {desc} {val!r} shared by {slot.get(val)} and {label}",
                )
                slot[val] = label
            for inst in font["fvar"].instances:
                require(inst.postscriptNameID != 0xFFFF, f"Missing instance postscriptNameID in {label}")
                ps = nm.getDebugName(inst.postscriptNameID) or ""
                require(bool(ps), f"Empty instance PostScript name in {label}")
                require(
                    ps not in instance_ps_names,
                    f"Duplicate fvar instance PostScript name {ps!r} shared by "
                    f"{instance_ps_names.get(ps)} and {label}",
                )
                instance_ps_names[ps] = label

    prop_fams = families_by_mode[WidthFamily.PROPORTIONAL]
    so_fams = families_by_mode[WidthFamily.SERIF_ONLY]
    if prop_fams and so_fams:
        require(
            prop_fams.isdisjoint(so_fams),
            f"Proportional and SerifOnly variable families collide in nameID 1/16: {prop_fams} vs {so_fams}",
        )

    static_id3: dict[str, str] = {}
    static_id4: dict[str, str] = {}
    static_id6: dict[str, str] = {}
    for target in static_targets:
        require(target.path.exists(), f"Missing static font: {target.path}")
        label = target.path.name
        with TTFont(target.path, lazy=True) as font:
            nm = font["name"]
            for slot, nid, desc in (
                (static_id3, 3, "static nameID 3"),
                (static_id4, 4, "static nameID 4"),
                (static_id6, 6, "static nameID 6"),
            ):
                val = nm.getDebugName(nid) or ""
                require(bool(val), f"Empty {desc} in {label}")
                require(val not in slot, f"Duplicate {desc} {val!r} in {slot.get(val)} and {label}")
                slot[val] = label
            require(
                (nm.getDebugName(6) or "") not in var_id6 or nm.getDebugName(1) == FAMILY,
                f"Unexpected static PostScript name in {label}",
            )
            require(
                (nm.getDebugName(16) or nm.getDebugName(1)) not in so_fams,
                f"Static font {label} collides with SerifOnly family {so_fams}",
            )

    return {
        "proportional_families": sorted(prop_fams),
        "serifonly_families": sorted(so_fams),
        "unique_variable_postscript_names": sorted(var_id6),
        "unique_variation_ps_prefixes": sorted(var_id25),
        "unique_fvar_instance_ps_count": len(instance_ps_names),
        "unique_static_postscript_names": sorted(static_id6),
    }


def axis_check(font: TTFont, width_family: WidthFamily) -> dict:
    axes = font["fvar"].axes
    tags = [axis.axisTag for axis in axes]
    require(tags == list(AXIS_LIMITS), f"Expected axes {list(AXIS_LIMITS)}, found {tags}")
    limits = {axis.axisTag: (axis.minValue, axis.defaultValue, axis.maxValue) for axis in axes}
    require(limits == AXIS_LIMITS, f"Unexpected axis limits/defaults: {limits}")
    weights = sorted(instance.coordinates["wght"] for instance in font["fvar"].instances)
    require(weights == list(NAMED_WEIGHTS), f"Unexpected named instance weights: {weights}")
    expected_prefix = f"{width_family.postscript_stem}-"
    instances = []
    for instance in font["fvar"].instances:
        require({k: v for k, v in instance.coordinates.items() if k != "wght"}
                == {"opsz": 12, "GRAD": 0, "YTLC": 500, "CALE": 0},
                f"Named instances sit at the text optical size, normal grade and drawn x-height: "
                f"{instance.coordinates}")
        name = font["name"].getDebugName(instance.subfamilyNameID)
        require(bool(name), "Named instance has no resolvable subfamily name")
        require("alegreya" not in name.lower(), f"Stale instance identity: {name}")
        require(instance.postscriptNameID != 0xFFFF, f"Instance {name} missing postscriptNameID")
        ps = font["name"].getDebugName(instance.postscriptNameID) or ""
        require(
            ps.startswith(expected_prefix),
            f"Expected instance PostScript name starting with {expected_prefix!r}, got {ps!r}",
        )
        instances.append({"weight": instance.coordinates["wght"], "name": name, "postscript_name": ps})
    return {"axes": limits, "instances": instances}


def names_check(font: TTFont, style: Style, width_family: WidthFamily) -> dict:
    table = font["name"]
    identity_ids = {1, 2, 3, 4, 5, 6, 16, 17, 21, 22, 25}
    identity = {}
    for record in table.names:
        if record.nameID not in identity_ids:
            continue
        value = record.toUnicode()
        require("alegreya" not in value.lower(), f"Stale Alegreya identity in name ID {record.nameID}: {value}")
        identity.setdefault(str(record.nameID), set()).add(value)
    sub = style.subfamily_name(400)
    family = table.getDebugName(16) or table.getDebugName(1)
    require(family == width_family.family_name, f"Expected family {width_family.family_name!r}, found {family!r}")
    expected_records = {
        1: width_family.family_name,
        2: sub,
        3: f"{VERSION};NONE;{width_family.postscript_stem}-{sub}",
        4: f"{width_family.family_name} {sub}",
        5: f"Version {VERSION}",
        6: f"{width_family.postscript_stem}-{sub}",
        25: f"{width_family.postscript_stem}{style.variation_ps_suffix}",
    }
    for name_id, expected_val in expected_records.items():
        actual_val = table.getDebugName(name_id)
        require(
            actual_val == expected_val,
            f"Name ID {name_id}: expected {expected_val!r}, got {actual_val!r}",
        )
    postscript = table.getDebugName(6) or ""
    require(bool(re.fullmatch(r"[!-~]+", postscript)), f"Invalid PostScript name: {postscript}")
    require(not any(char in postscript for char in " [](){}<>/%"), f"Invalid PostScript name: {postscript}")
    copyright_text = _verify_copyright_and_license(table)
    return {"identity_names": {key: sorted(values) for key, values in identity.items()},
            "copyright": copyright_text}


def remapped_weight(original: float) -> float:
    """Locate an original design weight on the rescaled public axis."""
    if original <= 380:
        return 300 + (original - 300) * 100 / 80
    return 400 + (original - 380) * 300 / 320


def text_drawing_check(font: TTFont, source_path: Path) -> dict:
    """At 12 pt, the original Regular and Bold designs retain their drawn text masters.
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
        glyphset = font.getGlyphSet(location={"wght": remapped_weight(weight), "opsz": 12, "GRAD": 0, "YTLC": 500})
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


def style_check(font: TTFont, style: Style, weight: StaticWeight = StaticWeight.REGULAR) -> dict:
    selection = font["OS/2"].fsSelection
    mac_style = font["head"].macStyle
    angle = font["post"].italicAngle
    ribbi = RibbiSubfamily.resolve(style, weight)
    require(not selection & (1 << 9), "The italic design must not be labeled oblique")
    require(
        (selection & 0x61) == ribbi.fs_selection_bits,
        f"OS/2 fsSelection RIBBI bits 0x{selection & 0x61:02x} != 0x{ribbi.fs_selection_bits:02x} for {ribbi}",
    )
    require(
        (mac_style & 0x3) == ribbi.mac_style_bits,
        f"head macStyle bits 0x{mac_style & 0x3:x} != 0x{ribbi.mac_style_bits:x} for {ribbi}",
    )
    match style:
        case Style.ROMAN:
            require(angle == 0, f"Unexpected Roman italic angle: {angle}")
        case Style.ITALIC:
            require(angle < 0, f"Unexpected Italic angle: {angle}")
    require(
        font["OS/2"].usWeightClass == weight.weight_value,
        f"Expected OS/2 weight class {weight.weight_value}, got {font['OS/2'].usWeightClass}",
    )
    subfamily = font["name"].getDebugName(17) or font["name"].getDebugName(2) or ""
    expected_sub = style.subfamily_name(weight.weight_value)
    require(subfamily == expected_sub, f"Expected subfamily {expected_sub!r}, got {subfamily!r}")
    return {"style": style.name, "weight": weight.weight_value, "fsSelection": selection,
            "macStyle": mac_style, "italic_angle": angle, "subfamily": subfamily}


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
    require(abs(font["head"].fontRevision - float(VERSION)) < 1e-4,
            f"Unexpected head.fontRevision: {font['head'].fontRevision}")
    require("gasp" in font and font["gasp"].gaspRange == {65535: 15},
            "Missing or unexpected gasp range")
    return {"vendor": os2.achVendID, "underline_position": post.underlinePosition,
            "underline_thickness": post.underlineThickness,
            "windows_ascent": os2.usWinAscent, "windows_descent": os2.usWinDescent}


def tables_check(font: TTFont) -> dict:
    required = {"cmap", "glyf", "loca", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post",
                "gasp", "fvar", "gvar", "STAT", "GSUB", "GPOS", "GDEF"}
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


def coverage_check(font: TTFont, data: bytes, weights: tuple[int | None, ...] = WEIGHTS) -> dict:
    cmap = font.getBestCmap()
    evidence = {}
    for script, alphabet in COVERAGE.items():
        missing = sorted({f"U+{ord(char):04X}" for char in alphabet if ord(char) not in cmap})
        require(not missing, f"Missing {script} characters: {', '.join(missing)}")
        counts = {}
        for weight in weights:
            shaped = shape(data, SAMPLES[script], weight)
            require(shaped and all(gids(shaped)), f"Missing glyph in {script} shaping at weight {weight}")
            require(sum(item["advance"] for item in shaped) > 0,
                    f"Nonpositive text advance for {script} at weight {weight}")
            counts[str(weight or "static")] = len(shaped)
        evidence[script] = {"required_characters": len(set(alphabet)), "shaped_glyph_counts": counts}
    return {"encoded_characters": len(cmap), "scripts": evidence}


def features_check(data: bytes, weights: tuple[int | None, ...] = (400, 600, 700)) -> dict:
    evidence = {}
    for weight in weights:
        key = str(weight or "static")
        weight_result = {}
        off = shape(data, "office affinity fi fl Th", weight, {"liga": 0, "clig": 0})
        on = shape(data, "office affinity fi fl Th", weight, {"liga": 1, "clig": 0})
        require(all(gids(on)) and len(on) < len(off), f"liga does not form ligatures at {key}")
        weight_result["liga"] = {"disabled_glyphs": len(off), "enabled_glyphs": len(on)}

        off = shape(data, "aenorz", weight, {"smcp": 0})
        on = shape(data, "aenorz", weight, {"smcp": 1})
        require(len(on) == len(off) == 6 and all(gids(on))
                and all(a != b for a, b in zip(gids(off), gids(on))),
                f"smcp does not substitute every sample lowercase at {key}")
        weight_result["smcp"] = {"substituted_glyphs": len(on)}

        oldstyle = shape(data, "0123456789", weight, {"onum": 1, "lnum": 0, "tnum": 0})
        lining = shape(data, "0123456789", weight, {"lnum": 1, "onum": 0, "tnum": 0})
        require(len(oldstyle) == len(lining) == 10 and all(gids(oldstyle)) and all(gids(lining))
                and gids(oldstyle) != gids(lining), f"onum/lnum do not select distinct figure sets at {key}")
        weight_result["onum_lnum"] = {"distinct_figure_glyphs": sum(a != b for a, b in zip(gids(oldstyle), gids(lining)))}
        tabular_result = {}
        for figure in ("lnum", "onum"):
            tabular = shape(data, "0123456789", weight,
                            {"lnum": int(figure == "lnum"), "onum": int(figure == "onum"), "tnum": 1, "kern": 0})
            widths = {item["advance"] for item in tabular}
            require(len(tabular) == 10 and all(gids(tabular)) and len(widths) == 1 and min(widths) > 0,
                    f"tnum {figure} widths are not equal at {key}: {sorted(widths)}")
            tabular_result[figure] = next(iter(widths))
        weight_result["tnum"] = tabular_result

        off = shape(data, "12/345", weight, {"frac": 0})
        on = shape(data, "12/345", weight, {"frac": 1})
        require(len(on) == len(off) == 6 and all(gids(on)) and gids(off) != gids(on),
                f"frac does not substitute fraction glyphs at {key}")
        fraction_advance = sum(item["advance"] for item in on)
        regular_advance = sum(item["advance"] for item in off)
        require(0 < fraction_advance < regular_advance, f"frac does not produce a compact fraction at {key}")
        hb_font = hb.Font(hb.Face(data))
        hb.ot_font_set_funcs(hb_font)
        if weight is not None:
            hb_font.set_variations({"wght": weight})
        numerator = [hb_font.get_glyph_extents(item["gid"]).y_bearing + item["y"] for item in on[:2]]
        denominator = [hb_font.get_glyph_extents(item["gid"]).y_bearing + item["y"] for item in on[3:]]
        require(min(numerator) > max(denominator), f"Fraction numerators are not above denominators at {key}")
        weight_result["frac"] = {"regular_advance": regular_advance, "fraction_advance": fraction_advance}
        evidence[key] = weight_result
    return evidence


def default_figures_check(data: bytes, weights: tuple[int | None, ...] = (400, 600, 700)) -> dict:
    """Default digits are oldstyle; lnum selects lining and numeric GSUB takes both."""
    evidence = {}
    digits = "0123456789"
    for weight in weights:
        key = str(weight or "static")
        default = shape(data, digits, weight)
        lining = shape(data, digits, weight, {"lnum": 1, "onum": 0, "tnum": 0})
        oldstyle = shape(data, digits, weight, {"onum": 1, "lnum": 0, "tnum": 0})
        require(len(default) == len(lining) == len(oldstyle) == 10
                and all(gids(default)) and all(gids(oldstyle)),
                f"Default or alternate figure set is incomplete at {key}")
        require(default == oldstyle,
                f"Default digits differ from explicitly selected oldstyle figures at {key}")
        require(all(a != b for a, b in zip(gids(default), gids(lining))),
                f"lnum does not select a lining alternate for every default digit at {key}")
        numeric_features = {}
        for feature in ("numr", "dnom", "sups", "subs"):
            alternate = shape(data, digits, weight, {feature: 1})
            require(len(alternate) == 10 and all(gids(alternate))
                    and all(a != b for a, b in zip(gids(default), gids(alternate))),
                    f"{feature} does not substitute every default digit at {key}")
            numeric_features[feature] = {"substituted_glyphs": len(alternate),
                                         "glyph_ids": gids(alternate)}
        evidence[key] = {"default_matches_onum": True,
                         "lining_alternates": len(lining),
                         "numeric_features": numeric_features}
    return evidence


def marks_check(data: bytes, style: Style, weights: tuple[int | None, ...] = WEIGHTS) -> dict:
    # q has no precomposed acute/circumflex form, so these exercise GPOS rather
    # than only Unicode normalization to a precomposed character.
    probes = ("q\u0301", "q\u0302\u0301", "q\u0323\u0301",
              "g\u030b", "g\u0323\u0301", "Ж\u0301", "t\u0302", "a\u0331", "a\u0324")
    evidence = {}
    for weight in weights:
        key = str(weight or "static")
        attached = []
        for text in probes:
            off = shape(data, text, weight, {"mark": 0, "mkmk": 0})
            on = shape(data, text, weight, {"mark": 1, "mkmk": 1})
            require(all(gids(on)) and len(on) >= 2, f"Combining mark missing at {key}: {text!r}")
            require(all(item["advance"] == 0 for item in on[1:]),
                    f"Combining mark unexpectedly advances at {key}: {text!r}")
            if on != off and any(item["x"] or item["y"] for item in on[1:]):
                attached.append(text)
        require(len(attached) == len(probes),
                f"GPOS did not attach every mark probe at {key}: {attached}")
        # Check that the circumflex clears the Roman t ascender tip, not only
        # that GPOS attaches the mark.
        shaped_t = shape(data, "t\u0302", weight)
        require(len(shaped_t) == 2, f"Expected separate t and circumflex at {key}")
        hb_font = hb.Font(hb.Face(data))
        hb.ot_font_set_funcs(hb_font)
        if weight is not None:
            hb_font.set_variations({"wght": weight})
        base, mark = [hb_font.get_glyph_extents(item["gid"]) for item in shaped_t]
        require(base is not None and mark is not None,
                f"Missing t/circumflex outline extents at {key}")
        clearance = (mark.y_bearing + mark.height + shaped_t[1]["y"]
                     - base.y_bearing - shaped_t[0]["y"])
        match style:
            case Style.ROMAN:
                require(clearance > 0,
                        f"Circumflex enters the roman t ascender band at {key}: clearance {clearance}")
            case Style.ITALIC:
                pass
        evidence[key] = {"positioned_samples": attached,
                         "t_circumflex_clearance": clearance}
    return evidence


def _verify_below_mark_alignment(data: bytes, loc: dict[str, float]) -> dict[str, float]:
    """Verify below-baseline marks (`a\\u0331`, `a\\u0324`, `ḇ`, `ṳ`) center beneath their base."""
    face = hb.Face(data)
    hb_font = hb.Font(face)
    hb_font.scale = (face.upem, face.upem)
    hb_font.set_variations(loc)
    hb.ot_font_set_funcs(hb_font)
    errors: dict[str, float] = {}
    for seq in ("a\u0331", "a\u0324", "\u1e07", "\u1e73"):
        buf = hb.Buffer()
        buf.add_str(seq)
        buf.guess_segment_properties()
        hb.shape(hb_font, buf)
        infos, positions = buf.glyph_infos, buf.glyph_positions
        require(len(infos) == 2, f"Expected base + below-mark for {seq!r} at {loc}, got {len(infos)} glyphs")
        b_ext = hb_font.get_glyph_extents(infos[0].codepoint)
        m_ext = hb_font.get_glyph_extents(infos[1].codepoint)
        require(b_ext is not None and m_ext is not None, f"Missing extents for {seq!r} at {loc}")
        b_x0 = positions[0].x_offset + b_ext.x_bearing
        b_x1 = b_x0 + b_ext.width
        m_x0 = positions[0].x_advance + positions[1].x_offset + m_ext.x_bearing
        m_x1 = m_x0 + m_ext.width
        b_cx = 0.5 * (b_x0 + b_x1)
        m_cx = 0.5 * (m_x0 + m_x1)
        m_top = positions[0].y_advance + positions[1].y_offset + m_ext.y_bearing
        dx = m_cx - b_cx
        require(
            m_x1 > b_x0 and m_x0 < b_x1 and abs(dx) <= 135.0,
            f"Misaligned MarkAnchor on {seq!r} at {loc}: base=[{b_x0:.1f},{b_x1:.1f}] "
            f"mark=[{m_x0:.1f},{m_x1:.1f}] dx={dx:+.1f}u",
        )
        require(
            m_top < 20.0,
            f"Below-baseline mark {seq!r} sits too high at {loc}: top={m_top:.1f}",
        )
        errors[seq] = round(dx, 1)
    return errors


def composite_and_multiaxis_bounds_check(font: TTFont) -> dict:
    """Check all simple and composite (`isComposite()`) glyphs for runaway bounds and
    `usWinAscent`/`usWinDescent` clipping across multi-axis corners (`CALE = 0, 50, 100`)."""
    glyf = font["glyf"]
    glyph_order = font.getGlyphOrder()
    composite_names = [name for name in glyph_order if glyf[name].isComposite()]
    win_asc = font["OS/2"].usWinAscent
    win_desc = font["OS/2"].usWinDescent
    base_corners = (
        {"wght": 300, "opsz": 6, "GRAD": 0, "YTLC": 500},
        {"wght": 300, "opsz": 12, "GRAD": 0, "YTLC": 500},
        {"wght": 400, "opsz": 12, "GRAD": 0, "YTLC": 500},
        {"wght": 400, "opsz": 12, "GRAD": 0, "YTLC": 550},
        {"wght": 700, "opsz": 6, "GRAD": 0, "YTLC": 550},
        {"wght": 700, "opsz": 12, "GRAD": 0, "YTLC": 500},
        {"wght": 700, "opsz": 12, "GRAD": 0, "YTLC": 550},
        {"wght": 700, "opsz": 72, "GRAD": 0, "YTLC": 550},
    )
    problems: list[str] = []
    locations_checked = 0
    for base_loc in base_corners:
        for cale in (0, 50, 100):
            loc = {**base_loc, "CALE": cale}
            locations_checked += 1
            gs = font.getGlyphSet(location=loc)
            loc_str = f"w={base_loc['wght']},o={base_loc['opsz']},y={base_loc['YTLC']},CALE={cale}"
            for name in glyph_order:
                pen = BoundsPen(gs)
                gs[name].draw(pen)
                if pen.bounds is None:
                    continue
                x0, y0, x1, y1 = pen.bounds
                if y1 > win_asc or -y0 > win_desc:
                    problems.append(
                        f"{name} @ {loc_str}: vertical clipping ({y0:.1f}..{y1:.1f}) vs (-{win_desc}..{win_asc})"
                    )
                if glyf[name].isComposite():
                    adv = gs[name].width
                    if adv > 0 and (x1 <= 0 or x0 < -240 or x1 - adv > 300):
                        problems.append(
                            f"{name} @ {loc_str}: runaway composite bounds x=[{x0:.1f},{x1:.1f}] adv={adv:.1f}"
                        )
    require(not problems, f"{len(problems)} composite/clipping bounds errors, e.g. {problems[:5]}")
    return {"composites": len(composite_names), "locations_checked": locations_checked}


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


def roundtrip_check(font: TTFont, data: bytes, weights: tuple[int | None, ...] = WEIGHTS) -> dict:
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
        for weight in weights:
            for sample in SAMPLES.values():
                require(shape(data, sample, weight) == shape(saved, sample, weight),
                        f"Shaping changed after save/reopen at {weight}")
    finally:
        reopened.close()
    return {"source_bytes": len(data), "roundtrip_bytes": len(saved),
            "shaping_comparisons": len(weights) * len(SAMPLES), "checksum": "0xB1B0AFBA"}


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
                    continue
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
        before, after = references[int(corner["wght"])], _contours(glyphset, name)
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
        problems = [line for found in pool.map(_corner_problems, itertools.repeat(data), corners)
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


def grade_check(font: TTFont, data: bytes) -> dict:
    """Grade keeps layout fixed and changes contrast with compensated text color."""
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
    contrast = {grade: value["hairline"] / value["stem"] for grade, value in runs.items()}
    require(contrast[-50] < contrast[0] < contrast[100],
            f"Grade must order hairline-to-stem ratios: {runs}")
    require(runs[-50]["hairline"] < runs[0]["hairline"],
            f"Negative grade must retain finer hairlines: {runs}")
    require(runs[100]["hairline"] > runs[0]["hairline"],
            f"Positive grade must retain fuller hairlines: {runs}")
    evidence["strokes"] = {str(g): {k: round(v, 1) for k, v in r.items()} for g, r in runs.items()}
    return evidence


def grade_color_check(font: TTFont, style: Style) -> dict:
    """Compare neutral appearance and endpoint color drift with release 1.4.9."""
    reference = json.loads((Path(__file__).parent / "fixtures/grade-color-1.4.9.json").read_text())
    ref_entry = reference["fonts"][_fixture_key_for_style(style)]
    names = list(reference["sample"])
    before_total, after_total = 0.0, 0.0
    maximum_neutral_error = 0.0
    for sample in ref_entry["samples"]:
        location = {tag: sample[tag] for tag in ("wght", "opsz", "YTLC")}
        ink = {grade: _ink(font.getGlyphSet(location={**location, "GRAD": grade}), names)
               for grade in (-50, 0, 100)}
        old = sample["ink"]
        neutral_error = abs(ink[0] / old["0"] - 1)
        maximum_neutral_error = max(maximum_neutral_error, neutral_error)
        require(neutral_error <= 0.003,
                f"Grade 0 color changed at {location}: {neutral_error:.2%}")
        for grade in (-50, 100):
            before = abs(old[str(grade)] / old["0"] - 1)
            after = abs(ink[grade] / ink[0] - 1)
            require(after <= 0.75 * before + 0.003,
                    f"Insufficient grade compensation at {location}, GRAD {grade}: "
                    f"{before:.2%} became {after:.2%}")
            before_total += before
            after_total += after
    require(after_total <= 0.4 * before_total,
            f"Aggregate grade color drift remains {after_total / before_total:.1%} of its old value")
    return {"reference_release": reference["reference_release"],
            "samples": len(ref_entry["samples"]),
            "color_drift_reduction_percent": round(100 * (1 - after_total / before_total), 2),
            "maximum_neutral_ink_change_percent": round(100 * maximum_neutral_error, 4)}


def display_grade_reference_check(font: TTFont, style: Style) -> dict:
    """Compare guarded display glyphs with the independent Grade -35 reference."""
    reference = json.loads((Path(__file__).parent / "fixtures/display-grade-minus-35.json").read_text())
    ref_entry = reference["fonts"][_fixture_key_for_style(style)]
    samples = ref_entry["samples"]
    maximum_area_percent, maximum_bounds = 0.0, 0.0
    for sample in samples:
        location = {"wght": remapped_weight(sample["wght"]), "opsz": 72, "GRAD": 0, "YTLC": sample["YTLC"]}
        glyphset = font.getGlyphSet(location=location)
        area, bounds = AreaPen(glyphset), BoundsPen(glyphset)
        glyphset[sample["glyph"]].draw(area)
        glyphset[sample["glyph"]].draw(bounds)
        area_error = abs(abs(area.value) - sample["ink_area"])
        area_percent = 100 * area_error / sample["ink_area"]
        require(area_error <= max(150, 0.01 * sample["ink_area"]),
                f"display Grade -35 reference {sample['label']} at {location}: "
                f"ink area differs by {area_percent:.2f}% ({area_error:.1f} square units)")
        require(bounds.bounds is not None,
                f"display Grade -35 reference {sample['label']} has no outline at {location}")
        bounds_error = max(abs(actual - expected)
                           for actual, expected in zip(bounds.bounds, sample["bounds"]))
        require(bounds_error <= 1.25,
                f"display Grade -35 reference {sample['label']} at {location}: "
                f"bounds differ by {bounds_error:.2f} font units")
        maximum_area_percent = max(maximum_area_percent, area_percent)
        maximum_bounds = max(maximum_bounds, bounds_error)
    return {"reference": reference["reference"], "samples": len(samples),
            "maximum_ink_area_difference_percent": round(maximum_area_percent, 4),
            "maximum_bounds_difference_units": round(maximum_bounds, 4)}


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
    """The lowercase height axis keeps every advance and moves only what sits on the x-height."""
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
        for ch in "HAOl":
            require(_top(sets[500], g(ch)) == _top(sets[550], g(ch)), f"YTLC moves {ch} at {loc}")
        require(_top(sets[500], g("p"))[0] == _top(sets[550], g("p"))[0], f"YTLC moves the descender at {loc}")
        for ch in "ÁẤẢ":
            if ord(ch) in cmap:
                a, b = _top(sets[500], g(ch)), _top(sets[550], g(ch))
                require(abs(a[1] - b[1]) < 1, f"YTLC moves the accent of {ch} at {loc}: {a} -> {b}")
        (_, x0), (_, x1) = _top(sets[500], g("x")), _top(sets[550], g("x"))
        rise = x1 - x0
        if opsz < 12:
            evidence[f"{weight}/{opsz}"] = {"x_height": [round(x0), round(x1)]}
            continue
        require(0.08 * x0 <= rise <= 0.12 * x0, f"x-height at YTLC 550 not 8-12% taller at {loc}: {x0} -> {x1}")
        follows = {}
        for ch in "áoiö:":
            a, b = _top(sets[500], g(ch)), _top(sets[550], g(ch))
            follows[ch] = round(b[1] - a[1], 1)
            require(0.5 * rise <= b[1] - a[1] <= 1.15 * rise, f"{ch} does not follow the x-height at {loc}: "
                                                             f"top {a[1]} -> {b[1]}, x rise {rise:.1f}")
        hyphen = [_top(sets[y], g("-")) for y in (500, 550)]
        require(0.3 * rise <= hyphen[1][0] - hyphen[0][0] <= 0.7 * rise, f"hyphen not centred on the taller "
                                                                         f"x-height at {loc}: {hyphen}")
        for name in ("a.sc", "zero"):
            if name in order:
                a, b = _top(sets[500], name), _top(sets[550], name)
                require(0.7 * rise <= b[1] - a[1] <= 1.15 * rise, f"{name} does not follow at {loc}: {a} -> {b}")
        evidence[f"{weight}/{opsz}"] = {"x_height": [round(x0), round(x1)], "follow": follows}
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


def _caleuche_wy_problems(args: tuple[bytes, int, int]) -> list[str]:
    data, wght, ytlc = args
    font = TTFont(BytesIO(data))
    glyf = font["glyf"]
    simple = [name for name in font.getGlyphOrder() if glyf[name].numberOfContours > 0]
    gs0 = font.getGlyphSet(location={"wght": wght, "opsz": 12, "GRAD": 0, "YTLC": ytlc, "CALE": 0})
    base_map = {
        name: [(a, cr, vis) for a, cr, vis in _contours(gs0, name) if abs(a) >= 2.0]
        for name in simple
    }
    problems = []
    for cale in (25, 50, 75, 100):
        loc = {"wght": wght, "opsz": 12, "GRAD": 0, "YTLC": ytlc, "CALE": cale}
        gs = font.getGlyphSet(location=loc)
        label = f"wght={wght:g},YTLC={ytlc:g},CALE={cale:g}"
        for name in simple:
            k_active = base_map[name]
            active = [(a, cr, vis) for a, cr, vis in _contours(gs, name) if abs(a) >= 2.0]
            if k_active and not active:
                problems.append(f"{name} @ {label}: all {len(k_active)} active contours collapsed to zero area")
                continue
            for idx, (_, _, vis) in enumerate(active):
                k_vis = k_active[idx][2] if len(active) == len(k_active) else 0
                if cale == 100 and vis > k_vis:
                    problems.append(f"{name}#{idx} @ {label}: {vis} visible self-intersections (base={k_vis})")
            if cale == 100 and len(active) == len(k_active):
                for idx, ((ka, _, _), (a, _, _)) in enumerate(zip(k_active, active)):
                    if (a > 0) != (ka > 0):
                        problems.append(f"{name}#{idx} @ {label}: winding inverted ({ka:.0f} -> {a:.0f})")
    return problems


def caleuche_axis_check(font: TTFont, data: bytes, style: Style, width_family: WidthFamily) -> dict:
    """Validate CALE (0..100) interpolation, Caleuche weight/YTLC axes, MarkAnchors, and width mode."""
    cmap = font.getBestCmap()
    g = lambda ch: cmap[ord(ch)]
    glyph_order = font.getGlyphOrder()
    win_asc = font["OS/2"].usWinAscent
    win_desc = font["OS/2"].usWinDescent

    # 1. CALE interpolation steps (0, 25, 50, 75, 100) produce distinct outlines and valid bounds
    cale_hashes = []
    for cale in (0, 25, 50, 75, 100):
        gs = font.getGlyphSet(location={"wght": 400, "opsz": 12, "GRAD": 0, "YTLC": 500, "CALE": cale})
        for name in glyph_order:
            pen = BoundsPen(gs)
            gs[name].draw(pen)
            if pen.bounds is None:
                continue
            x0, y0, x1, y1 = pen.bounds
            require(x0 <= x1 and y0 <= y1, f"Invalid bounds for {name} at CALE={cale}")
            require(y1 <= win_asc and -y0 <= win_desc,
                    f"Vertical clipping for {name} at CALE={cale}: ({y0}, {y1})")
        rec = DecomposingRecordingPen(gs)
        for ch in "Honeymoon":
            gs[g(ch)].draw(rec)
        cale_hashes.append(sha256(repr(rec.value).encode()).hexdigest())
    require(len(set(cale_hashes)) == 5, "Outlines do not vary across CALE = 0, 25, 50, 75, 100")

    # 2. At CALE=100, GRAD (-50, 0, 100) and display opsz (72) leave Caleuche outlines invariant
    gs_c0 = font.getGlyphSet(location={"wght": 400, "opsz": 12, "GRAD": 0, "YTLC": 500, "CALE": 100})
    probe_glyphs = [g(c) for c in "HamburgefonstivwWyg"]
    for w_check in (300, 400, 700):
        gs_ref = font.getGlyphSet(location={"wght": w_check, "opsz": 12, "GRAD": 0, "YTLC": 500, "CALE": 100})
        top0, pts0 = _outline_coordinates(gs_ref, probe_glyphs)
        for o_check in (12, 72):
            for grade in (-50, 0, 100):
                gs_cg = font.getGlyphSet(location={"wght": w_check, "opsz": o_check, "GRAD": grade, "YTLC": 500, "CALE": 100})
                top_g, pts_g = _outline_coordinates(gs_cg, probe_glyphs)
                require(top_g == top0 and pts_g == pts0,
                        f"GRAD {grade}/opsz {o_check} changes Caleuche outlines at wght={w_check},CALE=100")
                match width_family:
                    case WidthFamily.PROPORTIONAL:
                        moved = [n for n in glyph_order if gs_cg[n].width != gs_ref[n].width]
                        require(
                            not moved,
                            f"GRAD {grade}/opsz {o_check} changes Caleuche advances at wght={w_check},CALE=100: {moved[:5]}",
                        )
                    case WidthFamily.SERIF_ONLY:
                        if o_check == 12:
                            moved = [n for n in glyph_order if gs_cg[n].width != gs_ref[n].width]
                            require(
                                not moved,
                                f"GRAD {grade} changes SerifOnly advances at wght={w_check},CALE=100: {moved[:5]}",
                            )

    # 3. At CALE=100, weights (300, 400, 500, 700) vary ink monotonically
    inks = {}
    sample_names = [g(c) for c in "Hamburgefonstiv"]
    for w in (300, 400, 500, 700):
        gs_w = font.getGlyphSet(location={"wght": w, "opsz": 12, "GRAD": 0, "YTLC": 500, "CALE": 100})
        inks[w] = _ink(gs_w, sample_names)
        match width_family:
            case WidthFamily.PROPORTIONAL:
                adv_diff = [n for n in glyph_order if gs_w[n].width != gs_c0[n].width]
                require(not adv_diff, f"Caleuche weight {w} changes advances relative to 400: {adv_diff[:5]}")
            case WidthFamily.SERIF_ONLY:
                gs_k = font.getGlyphSet(location={"wght": w, "opsz": 12, "GRAD": 0, "YTLC": 500, "CALE": 0})
                adv_diff = [n for n in glyph_order if gs_w[n].width != gs_k[n].width]
                require(not adv_diff, f"SerifOnly weight {w} drifted from Kerberos advance: {adv_diff[:5]}")
    require(inks[300] < inks[400] < inks[500] < inks[700], f"Caleuche weight inks not monotonic: {inks}")

    # 4. At CALE=100, YTLC=550 raises x-height by 8-12% while keeping capitals/ascenders/descenders fixed
    gs_ytlc = font.getGlyphSet(location={"wght": 400, "opsz": 12, "GRAD": 0, "YTLC": 550, "CALE": 100})
    (_, x0), (_, x1) = _top(gs_c0, g("x")), _top(gs_ytlc, g("x"))
    rise = x1 - x0
    require(0.08 * x0 <= rise <= 0.12 * x0, f"Caleuche YTLC=550 x-height rise out of range: {x0} -> {x1}")
    for ch in "HAOl":
        require(_top(gs_c0, g(ch)) == _top(gs_ytlc, g(ch)), f"Caleuche YTLC=550 moves {ch}")
    require(_top(gs_c0, g("p"))[0] == _top(gs_ytlc, g("p"))[0], "Caleuche YTLC=550 moves p descender")

    # 5. Combining mark positioning (`t\u0302`, `a\u0331`, `a\u0324`, `ḇ`, `ṳ`) across CALE = 0, 50, 100
    shaped_t = shape(data, "t\u0302", 400, location={"CALE": 100})
    hb_font = hb.Font(hb.Face(data))
    hb.ot_font_set_funcs(hb_font)
    hb_font.set_variations({"wght": 400, "CALE": 100})
    base_ext, mark_ext = [hb_font.get_glyph_extents(item["gid"]) for item in shaped_t]
    t_clearance = mark_ext.y_bearing + mark_ext.height + shaped_t[1]["y"] - base_ext.y_bearing - shaped_t[0]["y"]
    match style:
        case Style.ROMAN:
            require(t_clearance > 0, f"Circumflex collides with Caleuche t at CALE=100: clearance {t_clearance}")
        case Style.ITALIC:
            pass
    below_mark_errors = {
        str(cale): _verify_below_mark_alignment(
            data, {"wght": 400, "opsz": 12, "GRAD": 0, "YTLC": 500, "CALE": cale}
        )
        for cale in (0, 50, 100)
    }

    # 6. Width-mode specific PairPos kerning & advance invariance
    match width_family:
        case WidthFamily.PROPORTIONAL:
            cale_kern_pairs = ("AV", "To", "Wa", "fo", "Ta", "Va", "Yo")
            for pair in cale_kern_pairs:
                shaped_p = shape(data, pair, 400, location={"opsz": 12, "GRAD": 0, "YTLC": 500, "CALE": 100})
                expected_adv = gs_c0[g(pair[0])].width
                kerned_adv = shaped_p[0]["advance"]
                require(
                    kerned_adv < expected_adv,
                    f"Optical Caleuche PairPos kerning missing at CALE=100 for {pair!r}: {kerned_adv} >= {expected_adv}",
                )
                pen_l, pen_r = _Polylines(gs_c0), _Polylines(gs_c0)
                gs_c0[g(pair[0])].draw(pen_l)
                gs_c0[g(pair[1])].draw(pen_r)
                pts_l = [pt for poly in pen_l.polys for pt in poly]
                pts_r = [(pt[0] + kerned_adv, pt[1]) for poly in pen_r.polys for pt in poly]
                min_pair_dist = min(
                    math.hypot(xl - xr, yl - yr)
                    for xl, yl in pts_l
                    for xr, yr in pts_r
                    if abs(yl - yr) < 60.0
                )
                require(
                    min_pair_dist >= 32.0,
                    f"Caleuche kerning collision at CALE=100 for {pair!r}: clearance {min_pair_dist:.1f}u",
                )
                for o_k in (12, 72):
                    for gr_k in (-50, 0, 100):
                        shaped_go = shape(
                            data,
                            pair,
                            400,
                            location={"opsz": o_k, "GRAD": gr_k, "YTLC": 500, "CALE": 100},
                        )
                        require(
                            shaped_go == shaped_p,
                            f"Caleuche kerning for {pair!r} drifted at opsz={o_k}, GRAD={gr_k}: "
                            f"{shaped_go} != {shaped_p}",
                        )
        case WidthFamily.SERIF_ONLY:
            so_test_locs = (
                {"wght": 400, "opsz": 12, "GRAD": 0, "YTLC": 500},
                {"wght": 700, "opsz": 72, "GRAD": 50, "YTLC": 550},
                {"wght": 300, "opsz": 6, "GRAD": -50, "YTLC": 525},
            )
            so_kern_samples = (
                "AV",
                "To",
                "Y\u00f5",
                "R\u0129",
                "AVATAR Wolf, To Ty P. \u2014 \u201cquoted\u201d",
                SAMPLES["Latin"],
            )
            for so_loc in so_test_locs:
                so_gs_0 = font.getGlyphSet(location={**so_loc, "CALE": 0})
                so_shaped_0 = {
                    text: [
                        (item["gid"], item["advance"], item["x"])
                        for item in shape(data, text, int(so_loc["wght"]), location={**so_loc, "CALE": 0})
                    ]
                    for text in so_kern_samples
                }
                for cale_step in (25, 50, 75, 100):
                    so_gs_c = font.getGlyphSet(location={**so_loc, "CALE": cale_step})
                    width_drifts = [n for n in glyph_order if so_gs_c[n].width != so_gs_0[n].width]
                    require(
                        not width_drifts,
                        f"SerifOnly advance width drifted at {so_loc}, CALE={cale_step}: {width_drifts[:5]}",
                    )
                    for text in so_kern_samples:
                        shaped_c = [
                            (item["gid"], item["advance"], item["x"])
                            for item in shape(data, text, int(so_loc["wght"]), location={**so_loc, "CALE": cale_step})
                        ]
                        require(
                            shaped_c == so_shaped_0[text],
                            f"SerifOnly PairPos kerning/advance drifted for {text!r} at {so_loc}, CALE={cale_step}",
                        )

    # 7. Outline topology & zero excess visible self-intersections across all 32 (wght, YTLC, CALE) locations
    wy_pairs = [(data, w, y) for w, y in itertools.product((300, 400, 500, 700), (500, 550))]
    with ProcessPoolExecutor(max_workers=8) as pool:
        cale_problems = [line for found in pool.map(_caleuche_wy_problems, wy_pairs)
                         for line in found]
    require(not cale_problems, f"{len(cale_problems)} Caleuche outline problems, e.g. {cale_problems[:5]}")

    return {
        "cale_hashes": len(set(cale_hashes)),
        "caleuche_weight_inks": {str(w): round(v) for w, v in inks.items()},
        "caleuche_x_height": [round(x0), round(x1)],
        "t_circumflex_clearance_at_cale_100": t_clearance,
        "below_mark_center_dx": below_mark_errors,
        "cale_outline_locations_checked": len(wy_pairs) * 4,
        "width_family": width_family.name,
    }


def static_identity_and_tables_check(font: TTFont, style: Style, weight: StaticWeight) -> dict:
    forbidden = {"fvar", "gvar", "avar", "HVAR", "MVAR", "cvar"}
    leaked = sorted(forbidden & set(font.keys()))
    require(not leaked, f"Static font leaked variation tables: {leaked}")
    required = {"cmap", "glyf", "loca", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post",
                "gasp", "STAT", "GSUB", "GPOS", "GDEF"}
    require(required <= set(font.keys()), f"Static font missing tables: {sorted(required - set(font.keys()))}")
    require(getattr(font["GDEF"].table, "VarStore", None) is None, "Static font leaked GDEF.VarStore")
    stat_axes = [ax.AxisTag for ax in font["STAT"].table.DesignAxisRecord.Axis]
    require(stat_axes == ["wght", "ital"], f"Unexpected static STAT axes: {stat_axes}")

    table = font["name"]
    require(table.getDebugName(25) is None, "Static font must not carry nameID 25")
    sub = style.subfamily_name(weight.weight_value)
    compact = sub.replace(" ", "")
    require((table.getDebugName(16) or table.getDebugName(1)) == FAMILY, "Unexpected static typographic family")
    require(table.getDebugName(3) == f"{VERSION};NONE;{FAMILY}-{compact}", "Unexpected static nameID 3")
    require(table.getDebugName(4) == f"{FAMILY} {sub}", "Unexpected static nameID 4")
    require(table.getDebugName(5) == f"Version {VERSION}", "Unexpected static nameID 5")
    require(table.getDebugName(6) == f"{FAMILY}-{compact}", "Unexpected static nameID 6")
    copyright_text = _verify_copyright_and_license(table)

    # Verify clean static contours (no 1-contour zero-width Caleuche bridges leaked into Italic statics)
    glyf = font["glyf"]
    match style:
        case Style.ITALIC:
            require(
                glyf["w"].numberOfContours >= 2,
                f"Static Italic 'w' leaked 1-contour Caleuche bridge ({glyf['w'].numberOfContours} contours)",
            )
            for lig in ("f_f", "f_f_i", "f_f_l"):
                require(
                    glyf[lig].numberOfContours >= 2,
                    f"Static Italic {lig!r} leaked 1-contour Caleuche bridge",
                )
        case Style.ROMAN:
            pass
    for lig in JOINED_LIGATURES:
        if lig in glyf and glyf[lig].numberOfContours > 0:
            require(bool(glyf[lig].flags[0] & 0x40), f"Missing overlap flag 0x40 on static ligature {lig}")

    win_asc = font["OS/2"].usWinAscent
    win_desc = font["OS/2"].usWinDescent
    gs = font.getGlyphSet()
    for gname in font.getGlyphOrder():
        bp = BoundsPen(gs)
        gs[gname].draw(bp)
        if bp.bounds is None:
            continue
        x0, y0, x1, y1 = bp.bounds
        require(x0 <= x1 and y0 <= y1, f"Invalid static bounds in {gname}")
        require(
            y1 <= win_asc and -y0 <= win_desc,
            f"Static vertical clipping in {gname}: ({y0:.1f}, {y1:.1f}) vs (-{win_desc}, {win_asc})",
        )

    return {
        "postscript_name": table.getDebugName(6),
        "stat_axes": stat_axes,
        "copyright_length": len(copyright_text),
    }


def validate_target(target: FontTarget) -> list[CheckResult]:
    try:
        data = target.path.read_bytes()
        font = TTFont(BytesIO(data), lazy=False, checkChecksums=2)
    except Exception as exc:
        return [Failed("load_font", f"{type(exc).__name__}: {exc}")]
    try:
        match target:
            case VariableFontTarget(style=style, width_family=width_family):
                return [
                    check("axes_and_instances", lambda: axis_check(font, width_family)),
                    check("font_identity_and_license", lambda: names_check(font, style, width_family)),
                    check("text_size_matches_drawn_masters", lambda: text_drawing_check(font, style.glyphs_source)),
                    check("style_metadata", lambda: style_check(font, style)),
                    check("vertical_metrics_and_vendor", lambda: metrics_check(font)),
                    check("layout_and_variation_tables", lambda: tables_check(font)),
                    check("script_coverage_and_shaping", lambda: coverage_check(font, data)),
                    check("functional_opentype_features", lambda: features_check(data)),
                    check("default_lining_and_numeric_features", lambda: default_figures_check(data)),
                    check("combining_mark_positioning", lambda: marks_check(data, style)),
                    check("accented_base_advance_consistency", lambda: composite_width_check(font)),
                    check("composite_and_multiaxis_bounds", lambda: composite_and_multiaxis_bounds_check(font)),
                    check("intermediate_outlines_and_rasterization", lambda: outlines_check(font, data)),
                    check("grade_layout_and_contrast", lambda: grade_check(font, data)),
                    check("grade_color_compensation", lambda: grade_color_check(font, style)),
                    check("display_grade_matches_minus_35_reference", lambda: display_grade_reference_check(font, style)),
                    check("optical_size_proportions_and_contrast", lambda: optical_size_check(font)),
                    check("lowercase_height_axis", lambda: xheight_check(font)),
                    check("caleuche_interpolation_axis", lambda: caleuche_axis_check(font, data, style, width_family)),
                    check("outlines_at_design_space_corners", lambda: corner_outlines_check(data)),
                    check("compile_save_reopen", lambda: roundtrip_check(font, data)),
                ]
            case StaticFontTarget(style=style, weight=weight):
                return [
                    check("static_identity_and_tables", lambda: static_identity_and_tables_check(font, style, weight)),
                    check("style_metadata", lambda: style_check(font, style, weight)),
                    check("vertical_metrics_and_vendor", lambda: metrics_check(font)),
                    check("script_coverage_and_shaping", lambda: coverage_check(font, data, (None,))),
                    check("functional_opentype_features", lambda: features_check(data, (None,))),
                    check("default_lining_and_numeric_features", lambda: default_figures_check(data, (None,))),
                    check("combining_mark_positioning", lambda: marks_check(data, style, (None,))),
                    check("compile_save_reopen", lambda: roundtrip_check(font, data, (None,))),
                ]
    finally:
        font.close()


def default_targets(fonts_root: Path) -> list[FontTarget]:
    targets: list[FontTarget] = []
    for style in Style:
        targets.append(
            VariableFontTarget(
                style=style,
                width_family=WidthFamily.PROPORTIONAL,
                path=fonts_root / "variable" / VARIABLE[style].name,
            )
        )
    for style in Style:
        targets.append(
            VariableFontTarget(
                style=style,
                width_family=WidthFamily.SERIF_ONLY,
                path=fonts_root / "serifonly" / VARIABLE_SERIFONLY[style].name,
            )
        )
    for style in Style:
        for static_weight in StaticWeight:
            label = style.subfamily_name(static_weight.weight_value).replace(" ", "")
            targets.append(
                StaticFontTarget(
                    style=style,
                    weight=static_weight,
                    path=fonts_root / "static" / f"{FAMILY}-{label}.ttf",
                )
            )
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonts-root", type=Path, default=ROOT / "fonts")
    parser.add_argument("--ofl-path", type=Path, default=ROOT / "OFL.txt")
    parser.add_argument("--report", type=Path, default=ROOT / "build" / "validation.json")
    args = parser.parse_args()

    targets = default_targets(args.fonts_root)
    reports = []
    failed = 0
    passed = 0

    suite_checks = [
        check("ofl_license_copyright_notice", lambda: ofl_license_file_check(args.ofl_path)),
        check("cross_file_identity_uniqueness", lambda: cross_file_identity_check(targets)),
    ]
    print("Validating suite-level OFL & cross-file identity ...", flush=True)
    for result in suite_checks:
        match result:
            case Passed():
                passed += 1
            case Failed(name=name, error=error):
                failed += 1
                print(f"  FAIL {name}: {error}", flush=True)
    reports.append({"file": "suite", "checks": [asdict(result) for result in suite_checks]})

    for target in targets:
        rel_name = (
            str(target.path.relative_to(args.fonts_root))
            if args.fonts_root in target.path.parents
            else target.path.name
        )
        print(f"Validating {rel_name} ...", flush=True)
        checks = validate_target(target)
        for result in checks:
            match result:
                case Passed():
                    passed += 1
                case Failed(name=name, error=error):
                    failed += 1
                    print(f"  FAIL {name}: {error}", flush=True)
        reports.append({"file": str(target.path.resolve()), "checks": [asdict(result) for result in checks]})

    report = {"status": "failed" if failed else "passed", "passed_checks": passed,
              "failed_checks": failed, "fonts": reports}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{passed} passed, {failed} failed. Report: {args.report}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
