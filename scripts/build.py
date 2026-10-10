#!/usr/bin/env python3
"""Build Kerberos from sources/Kerberos.glyphs and sources/Kerberos-Italic.glyphs.

    make build      (or: uv run python scripts/build.py)

Each source has three masters, Light (300), Regular (400) and Bold (700);
Axis Mappings in the sources place Medium (500) and SemiBold (600). fontmake
compiles one variable font per style; this script then sets naming, STAT and
gasp, and derives the static and web fonts.
"""
from __future__ import annotations

import enum
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fontTools.otlLib.builder import buildStatTable
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._f_v_a_r import NamedInstance
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[1]
FAMILY = "Kerberos"
VERSION = "1.101"  # font version X.YYY; released as 1.1.1
WEIGHTS = {300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold"}
STATICS = (300, 400, 700)


class Style(enum.Enum):
    ROMAN = "Kerberos"
    ITALIC = "Kerberos-Italic"


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
    fvar.instances = []
    for weight in WEIGHTS:
        instance = NamedInstance()
        instance.coordinates = {"wght": weight}
        label = style_name(style, weight)
        instance.subfamilyNameID = names.addName(label)
        instance.postscriptNameID = names.addName(f"{FAMILY}-{label.replace(' ', '')}")
        fvar.instances.append(instance)
    values = []
    for weight, label in WEIGHTS.items():
        value = {"value": weight, "name": label}
        if weight == 400:
            value.update(flags=2, linkedValue=700)
        values.append(value)
    italic = 1 if style is Style.ITALIC else 0
    buildStatTable(font, [
        {"tag": "wght", "name": "Weight", "ordering": 0, "values": values},
        {"tag": "ital", "name": "Italic", "ordering": 1, "values": [
            {"value": italic, "name": "Italic" if italic else "Roman", "flags": 0 if italic else 2,
             **({} if italic else {"linkedValue": 1})}]},
    ])
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
    command = [sys.executable, "-m", "fontmake", "-g", str(ROOT / "sources" / f"{stem}.glyphs"),
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
    out = ROOT / "fonts/variable" / f"{stem}[wght].ttf"
    out.parent.mkdir(parents=True, exist_ok=True)
    font.save(out)
    web = ROOT / "fonts/web" / f"{stem}[wght].woff2"
    web.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(web)
    font.flavor = None
    for weight in STATICS:
        static = instantiateVariableFont(font, {"wght": weight}, inplace=False, updateFontNames=True)
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
