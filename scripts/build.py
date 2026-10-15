#!/usr/bin/env python3
"""Build Kerberos from sources/Kerberos.glyphs and sources/Kerberos-Italic.glyphs.

    make build      (or: uv run python scripts/build.py)

Four axes, with Google Fonts semantics:

    wght  300-700  Light, Regular and Bold masters (Axis Mappings place Medium, SemiBold)
    opsz  8-72     Caption and Display masters; 14 is the text default
    GRAD  -50-100  Grade masters: thin strokes (and, upward, stems) change, layout never
    YTLC  500-550  Lowercase height: 500 is the drawn design, 550 a 10% taller x-height

Each source has fifteen masters: the three weights, and Caption, Display, Grade -50
and Grade 100 on each of them (every axis master follows its own weight's drawing).
The tall-x-height masters are not stored: xheight_axis.py derives one from each weight
master at build time, so they always follow the drawings.

fontmake compiles one variable font per style; this script then sets naming,
named instances (weights only, Google Fonts style), STAT and gasp, and derives
the static fonts (at the default optical size, grade and x-height) and the web fonts.
"""
from __future__ import annotations

import enum
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import glyphsLib
from fontTools.otlLib.builder import buildStatTable
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._f_v_a_r import NamedInstance
from fontTools.varLib.instancer import instantiateVariableFont

from xheight_axis import add_xheight_axis

ROOT = Path(__file__).resolve().parents[1]
FAMILY = "Kerberos"
VERSION = "1.205"  # font version X.YYY; released as 1.2.5
WEIGHTS = {300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold"}
STATICS = (300, 400, 700)
AXES = "GRAD,YTLC,opsz,wght"     # file-name order: Google Fonts sorts axis tags
DEFAULTS = {"opsz": 14, "GRAD": 0, "YTLC": 500}
# Google Fonts axis-registry names for optical sizes inside the 8-72 range.
OPSZ_VALUES = (8, 9, 10, 11, 12, 14, 16, 17, 18, 20, 24, 28, 36, 48, 60, 72)


class Style(enum.Enum):
    ROMAN = "Kerberos"
    ITALIC = "Kerberos-Italic"


VARIABLE = {style: ROOT / "fonts/variable" / f"{style.value}[{AXES}].ttf" for style in Style}
WEB = {style: ROOT / "fonts/web" / f"{style.value}[{AXES}].woff2" for style in Style}


# Glyphs whose deliberately overlapping hairline corners must not be erased
# (erasing them in one master only would break interpolation).
KEEP_OPEN_CORNERS = {
    Style.ROMAN: [],
    Style.ITALIC: ["A", "Aringacute", "M", "Q", "R", "Y", "c", "entail-cy", "idotless", "j",
                   "jdotless", "kaiSymbol", "kappa.sups", "n", "p", "t", "tbar", "ustrait-cy", "x"],
}


def style_name(style: Style, weight: int) -> str:
    if style is Style.ROMAN:
        return WEIGHTS[weight]
    return "Italic" if weight == 400 else f"{WEIGHTS[weight]} Italic"


def stat_axes(style: Style) -> list[dict]:
    """STAT design axes and values. Optical sizes are never elided (Google Fonts)."""
    values = []
    for weight, label in WEIGHTS.items():
        value = {"value": weight, "name": label}
        if weight == 400:
            value.update(flags=2, linkedValue=700)
        values.append(value)
    italic = 1 if style is Style.ITALIC else 0
    return [
        {"tag": "GRAD", "name": "Grade", "ordering": 0, "values": [
            {"value": 0, "name": "Normal", "flags": 2}]},
        {"tag": "opsz", "name": "Optical Size", "ordering": 1, "values": [
            {"value": size, "name": f"{size}pt"}
            for size in OPSZ_VALUES]},
        {"tag": "wght", "name": "Weight", "ordering": 2, "values": values},
        {"tag": "YTLC", "name": "Lowercase Height", "ordering": 3, "values": [
            {"value": 500, "name": "Normal", "flags": 2}]},
        {"tag": "ital", "name": "Italic", "ordering": 4, "values": [
            {"value": italic, "name": "Italic" if italic else "Roman", "flags": 0 if italic else 2,
             **({} if italic else {"linkedValue": 1})}]},
    ]


def finish(font: TTFont, style: Style) -> None:
    """Google Fonts naming, named instances, STAT and gasp for the variable font."""
    names = font["name"]
    sub = style_name(style, 400)
    for name_id in (1, 2, 3, 4, 5, 6, 16, 17, 25):
        names.removeNames(nameID=name_id)
    for name_id, value in {
        1: FAMILY, 2: sub, 3: f"{VERSION};NONE;{FAMILY}-{sub}", 4: f"{FAMILY} {sub}",
        5: f"Version {VERSION}", 6: f"{FAMILY}-{sub}",
        25: FAMILY + ("Italic" if style is Style.ITALIC else "Roman"),
    }.items():
        names.setName(value, name_id, 3, 1, 0x409)
    fvar = font["fvar"]
    for axis in fvar.axes:      # Google Fonts: parametric axes are hidden from axis pickers
        if axis.axisTag == "YTLC":
            axis.flags = 0x1
    fvar.instances = []
    for weight in WEIGHTS:
        instance = NamedInstance()
        instance.coordinates = {"wght": weight, **DEFAULTS}
        label = style_name(style, weight)
        instance.subfamilyNameID = names.addName(label)
        instance.postscriptNameID = names.addName(f"{FAMILY}-{label.replace(' ', '')}")
        fvar.instances.append(instance)
    buildStatTable(font, stat_axes(style))
    font["OS/2"].usWeightClass = 400
    font["head"].fontRevision = float(VERSION)
    for name in ("f_f", "f_f_i", "f_f_l"):
        if name not in font["glyf"]:
            continue
        glyph = font["glyf"][name]
        if glyph.numberOfContours > 0:
            # Overlap flag: the joined f-ligature contours overlap on purpose.
            glyph.flags[0] |= 0x40
    font["gasp"] = newTable("gasp")
    font["gasp"].gaspRange = {65535: 15}


def build(style: Style) -> str:
    stem = style.value
    work = ROOT / "build" / stem
    work.mkdir(parents=True, exist_ok=True)
    raw = work / f"{stem}-raw.ttf"
    # The lowercase height axis: one tall-x-height master derived from each weight master.
    source = work / f"{stem}-ytlc.glyphs"
    glyphs = glyphsLib.GSFont(str(ROOT / "sources" / f"{stem}.glyphs"))
    add_xheight_axis(glyphs)
    glyphs.save(str(source))
    command = [sys.executable, "-m", "fontmake", "-g", str(source),
               "-o", "variable", "--flatten-components", "--output-path", str(raw),
               "--master-dir", str(work / "masters"), "--instance-dir", str(work / "instances")]
    keep = KEEP_OPEN_CORNERS[style]
    if keep:
        command += ["--filter", "glyphsLib.filters.eraseOpenCorners::EraseOpenCornersFilter"
                                f"(exclude={keep!r}, pre=True)"]
    log = work / "fontmake.log"
    with log.open("w") as output:
        if subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT).returncode:
            raise SystemExit(f"{stem}: fontmake failed; see {log}\n{log.read_text()[-3000:]}")
    font = TTFont(raw)
    finish(font, style)
    out = VARIABLE[style]
    out.parent.mkdir(parents=True, exist_ok=True)
    font.save(out)
    WEB[style].parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(WEB[style])
    font.flavor = None
    # The statics come from the text cut (default optical size, grade and x-height), a
    # weight-only family: its STAT names them "Kerberos Bold", not "Kerberos 14pt Bold".
    text = instantiateVariableFont(font, DEFAULTS, inplace=False)
    buildStatTable(text, [axis for axis in stat_axes(style) if axis["tag"] in ("wght", "ital")])
    for weight in STATICS:
        static = instantiateVariableFont(text, {"wght": weight}, inplace=False,
                                         updateFontNames=True)
        label = style_name(style, weight).replace(" ", "")
        static["name"].setName(f"{FAMILY}-{label}", 6, 3, 1, 0x409)
        static["name"].setName(f"{VERSION};NONE;{FAMILY}-{label}", 3, 3, 1, 0x409)
        target = ROOT / "fonts/static" / f"{FAMILY}-{style_name(style, weight).replace(' ', '')}.ttf"
        target.parent.mkdir(parents=True, exist_ok=True)
        static.save(target)
    return f"Built {out.relative_to(ROOT)} ({len(font.getGlyphOrder())} glyphs)"


def main() -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        for line in pool.map(build, Style):
            print(line, flush=True)


if __name__ == "__main__":
    main()
