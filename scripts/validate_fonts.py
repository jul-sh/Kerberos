#!/usr/bin/env python3
"""Exercise the shipped variable fonts, without any proprietary reference font.

Run with ``.venv/bin/python scripts/validate_fonts.py`` from any directory.
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

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.sfnt import calcChecksum
from PIL import ImageFont
import uharfbuzz as hb

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = (300, 400, 450, 500, 600, 700)
NAMED_WEIGHTS = (300, 400, 500, 600, 700)
FONT_FILES = ("Kerberos[wght].ttf", "Kerberos-Italic[wght].ttf")
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


def shape(data: bytes, text: str, weight: int = 400,
          features: dict[str, int] | None = None) -> list[dict[str, int]]:
    face = hb.Face(data)
    font = hb.Font(face)
    font.scale = (face.upem, face.upem)
    font.set_variations({"wght": weight})
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
    require(len(axes) == 1 and axes[0].axisTag == "wght", "Expected exactly one wght axis")
    axis = axes[0]
    limits = (axis.minValue, axis.defaultValue, axis.maxValue)
    require(limits == (300, 400, 700), f"Unexpected wght limits/default: {limits}")
    weights = sorted(instance.coordinates["wght"] for instance in font["fvar"].instances)
    require(weights == list(NAMED_WEIGHTS), f"Unexpected named instance weights: {weights}")
    instances = []
    for instance in font["fvar"].instances:
        name = font["name"].getDebugName(instance.subfamilyNameID)
        require(bool(name), "Named instance has no resolvable subfamily name")
        require("alegreya" not in name.lower(), f"Stale instance identity: {name}")
        entry = {"weight": instance.coordinates["wght"], "name": name}
        if instance.postscriptNameID != 0xFFFF:
            ps = font["name"].getDebugName(instance.postscriptNameID)
            require(bool(ps) and ps.startswith("Kerberos"), f"Unexpected instance PostScript name: {ps}")
            entry["postscript_name"] = ps
        instances.append(entry)
    return {"axis": axis.axisTag, "min": limits[0], "default": limits[1], "max": limits[2],
            "instances": instances}


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
    require("The Alegreya Project Authors" in copyright_text,
            "Upstream copyright attribution must be retained in name ID 0")
    require("Open Font License" in (table.getDebugName(13) or ""), "Missing OFL license metadata")
    return {"identity_names": {key: sorted(values) for key, values in identity.items()},
            "copyright": copyright_text}


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
        # The recut roman t is taller than its source. An unchanged source
        # anchor can attach the circumflex correctly yet collide with its tip.
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
        # This conservative band separation is specific to the revised roman.
        # Slanted italic contours can be clear even when their y ranges overlap.
        if font["post"].italicAngle == 0:
            require(clearance > 0,
                    f"Circumflex enters the revised roman t ascender band at {weight}: clearance {clearance}")
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
        raster_font.set_variation_by_axes([weight])
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


def validate(path: Path) -> list[CheckResult]:
    try:
        data = path.read_bytes()
        font = TTFont(BytesIO(data), lazy=False, checkChecksums=2)
    except Exception as exc:
        return [Failed("load_font", f"{type(exc).__name__}: {exc}")]
    try:
        return [
            check("weight_axis_and_instances", lambda: axis_check(font)),
            check("renamed_identity_and_license", lambda: names_check(font)),
            check("style_metadata", lambda: style_check(font, "-Italic" in path.name)),
            check("vertical_metrics_and_vendor", lambda: metrics_check(font)),
            check("layout_and_variation_tables", lambda: tables_check(font)),
            check("script_coverage_and_shaping", lambda: coverage_check(font, data)),
            check("functional_opentype_features", lambda: features_check(data)),
            check("default_lining_and_numeric_features", lambda: default_figures_check(data)),
            check("combining_mark_positioning", lambda: marks_check(font, data)),
            check("accented_base_advance_consistency", lambda: composite_width_check(font)),
            check("intermediate_outlines_and_rasterization", lambda: outlines_check(font, data)),
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
