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
    Style.ITALIC: ['A', 'A-cy', 'Aacute', 'Abreve', 'Abreveacute', 'Abrevedotbelow', 'Abrevegrave', 'Abrevehookabove', 'Abrevetilde', 'Acaron', 'Acircumflex', 'Acircumflexacute', 'Acircumflexdotbelow', 'Acircumflexgrave', 'Acircumflexhookabove', 'Acircumflextilde', 'Adblgrave', 'Adieresis', 'Adotbelow', 'Agrave', 'Ahookabove', 'Ainvertedbreve', 'Alpha', 'Amacron', 'Aogonek', 'Aring', 'Aringacute', 'Atilde', 'B', 'Beta', 'C', 'Cacute', 'Ccaron', 'Ccedilla', 'Ccedillaacute', 'Ccircumflex', 'Cdotaccent', 'Chi', 'D', 'DZcaron', 'Dcaron', 'Ddotbelow', 'Dlinebelow', 'Dzcaron', 'Dze-cy', 'E', 'Eacute', 'Ebreve', 'Ecaron', 'Ecedillabreve', 'Ecircumflex', 'Ecircumflexacute', 'Ecircumflexdotbelow', 'Ecircumflexgrave', 'Ecircumflexhookabove', 'Ecircumflextilde', 'Edblgrave', 'Edieresis', 'Edotaccent', 'Edotbelow', 'Egrave', 'Ehookabove', 'Einvertedbreve', 'Em-cy', 'Emacron', 'Emacronacute', 'Emacrongrave', 'En-cy', 'Eogonek', 'Epsilon', 'Er-cy', 'Es-cy', 'Eta', 'Etilde', 'F', 'G', 'Gbreve', 'Gcaron', 'Gcircumflex', 'Gcommaaccent', 'Gdieresis', 'Gdotaccent', 'Germandbls.alt', 'Ghestroke-cy.loclBSH', 'Gmacron', 'Gtilde', 'H', 'Ha-cy', 'Hbrevebelow', 'Hcaron', 'Hcircumflex', 'Hdotbelow', 'I', 'I-cy', 'IJ', 'Iacute', 'Iacute_J.loclNLD', 'Ibreve', 'Icaron', 'Icircumflex', 'Idblgrave', 'Idieresis', 'Idieresisacute', 'Idotaccent', 'Idotbelow', 'Ie-cy', 'Igrave', 'Ihookabove', 'Iinvertedbreve', 'Imacron', 'Iogonek', 'Iota', 'Itilde', 'J', 'Jcircumflex', 'Je-cy', 'K', 'Kappa', 'Kcaron', 'Kcommaaccent', 'L', 'LJ', 'Lacute', 'Lcommaaccent', 'Ldot', 'Ldotbelow', 'Lj', 'Llinebelow', 'M', 'Mdotbelow', 'Mu', 'N', 'NJ', 'Nacute', 'Ncaron', 'Ncommaaccent', 'Ndotaccent', 'Ndotbelow', 'Nj', 'Nlinebelow', 'Ntilde', 'Nu', 'O', 'O-cy', 'Oacute', 'Obreve', 'Ocaron', 'Ocircumflex', 'Ocircumflexacute', 'Ocircumflexdotbelow', 'Ocircumflexgrave', 'Ocircumflexhookabove', 'Ocircumflextilde', 'Odblgrave', 'Odieresis', 'Odieresismacron', 'Odotaccentmacron', 'Odotbelow', 'Ograve', 'Ohookabove', 'Ohorn', 'Ohungarumlaut', 'Oinvertedbreve', 'Omacron', 'Omacronacute', 'Omacrongrave', 'Omicron', 'Oogonek', 'Otilde', 'Otildeacute', 'Otildedieresis', 'Otildemacron', 'P', 'Palochka-cy', 'Q', 'Qa-cy', 'R', 'Racute', 'Rcaron', 'Rcommaaccent', 'Rdblgrave', 'Rdotbelow', 'Rho', 'Rinvertedbreve', 'Rlinebelow', 'S', 'Sacute', 'Sacutedotaccent', 'Scaron', 'Scarondotaccent', 'Scedilla', 'Scircumflex', 'Scommaaccent', 'Sdotaccent', 'Sdotbelow', 'Sdotbelowdotaccent', 'T', 'Tau', 'Tbar', 'Tcaron', 'Tcedilla', 'Tcommaaccent', 'Tdotbelow', 'Te-cy', 'Tlinebelow', 'U', 'Uacute', 'Ubreve', 'Ucaron', 'Ucircumflex', 'Udblgrave', 'Udieresis', 'Udieresisacute', 'Udieresiscaron', 'Udieresisgrave', 'Udieresismacron', 'Udotbelow', 'Ugrave', 'Uhookabove', 'Uhorn', 'Uhungarumlaut', 'Uinvertedbreve', 'Umacron', 'Umacrondieresis', 'Uogonek', 'Uring', 'Ustrait-cy', 'Ustraitstroke-cy', 'Utilde', 'Utildeacute', 'V', 'Ve-cy', 'W', 'Wacute', 'Wcircumflex', 'Wdieresis', 'We-cy', 'Wgrave', 'X', 'Y', 'Yacute', 'Ycircumflex', 'Ydieresis', 'Ydotaccent', 'Ydotbelow', 'Yeru-cy', 'Ygrave', 'Yhookabove', 'Yi-cy', 'Ymacron', 'Ytilde', 'Z', 'Zacute', 'Zcaron', 'Zdotaccent', 'Zdotbelow', 'Zeta', 'a', 'a-cy', 'aacute', 'abreve', 'abreveacute', 'abrevedotbelow', 'abrevegrave', 'abrevehookabove', 'abrevetilde', 'acaron', 'acircumflex', 'acircumflexacute', 'acircumflexgrave', 'acircumflexhookabove', 'acircumflextilde', 'adblgrave', 'adieresis', 'adotbelow', 'agrave', 'ahookabove', 'ainvertedbreve', 'amacron', 'aogonek', 'aring', 'aringacute', 'atilde', 'b', 'c', 'cacute', 'ccaron', 'ccedilla', 'ccedillaacute', 'ccircumflex', 'cdotaccent', 'd', 'dcaron', 'ddotbelow', 'de-cy.loclSRB', 'dlinebelow', 'dzcaron', 'dze-cy', 'dzhe-cy', 'e', 'e-cy', 'eacute', 'ebreve', 'ecaron', 'ecedillabreve', 'ecircumflex', 'ecircumflexacute', 'ecircumflexgrave', 'ecircumflexhookabove', 'ecircumflextilde', 'edblgrave', 'edieresis', 'edotaccent', 'edotbelow', 'egrave', 'ehookabove', 'einvertedbreve', 'emacron', 'emacronacute', 'emacrongrave', 'entail-cy', 'eogonek', 'er-cy', 'es-cy', 'etilde', 'f', 'f_b', 'f_f_b', 'f_f_h', 'f_f_k', 'f_f_t', 'f_h', 'f_k', 'f_t', 'g', 'gbreve', 'gcaron', 'gcircumflex', 'gcommaaccent', 'gdieresis', 'gdotaccent', 'gje-cy.loclMKD', 'gmacron', 'gtilde', 'h', 'ha-cy', 'hbrevebelow', 'hcaron', 'hcircumflex', 'hdotbelow', 'i', 'iacute', 'iacute_j.loclNLD', 'ibreve', 'icaron', 'icircumflex', 'idblgrave', 'idieresis', 'idieresisacute', 'idotless', 'ie-cy', 'igrave', 'ihookabove', 'ii-cy', 'iinvertedbreve', 'ij', 'imacron', 'itilde', 'iu-cy', 'izhitsa-cy', 'j', 'jcircumflex', 'jdotless', 'je-cy', 'k', 'kaiSymbol', 'kappa.sups', 'kcaron', 'kcommaaccent', 'l', 'lacute', 'lcaron', 'lcommaaccent', 'ldot', 'ldotbelow', 'lj', 'llinebelow', 'm', 'mdotbelow', 'n', 'nacute', 'ncaron', 'ncommaaccent', 'ndotaccent', 'ndotbelow', 'nj', 'nlinebelow', 'ntilde', 'o', 'o-cy', 'oacute', 'obreve', 'ocaron', 'ocircumflex', 'ocircumflexacute', 'ocircumflexgrave', 'ocircumflexhookabove', 'ocircumflextilde', 'odblgrave', 'odieresis', 'odieresismacron', 'odotaccentmacron', 'odotbelow', 'ograve', 'ohookabove', 'ohorn', 'ohungarumlaut', 'oinvertedbreve', 'omacron', 'omacronacute', 'omacrongrave', 'oogonek', 'otilde', 'otildeacute', 'otildedieresis', 'otildemacron', 'p', 'palochka-cy', 'q', 'qa-cy', 'r', 'racute', 'rcaron', 'rcommaaccent', 'rdblgrave', 'rdotbelow', 'rinvertedbreve', 'rlinebelow', 's', 'sacute', 'sacutedotaccent', 'scaron', 'scarondotaccent', 'scedilla', 'schwa', 'scircumflex', 'scommaaccent', 'sdotaccent', 'sdotbelow', 'sdotbelowdotaccent', 'shha-cy', 't', 'tbar', 'tcaron', 'tcedilla', 'tcommaaccent', 'tdieresis', 'tdotbelow', 'te-cy', 'tlinebelow', 'tse-cy', 'u', 'u-cy', 'uacute', 'ubreve', 'ucaron', 'ucircumflex', 'udblgrave', 'udieresis', 'udieresisacute', 'udieresiscaron', 'udieresisgrave', 'udieresismacron', 'udotbelow', 'ugrave', 'uhookabove', 'uhorn', 'uhungarumlaut', 'uinvertedbreve', 'umacron', 'umacrondieresis', 'uogonek', 'uring', 'ustrait-cy', 'utilde', 'utildeacute', 'v', 'w', 'wacute', 'wcircumflex', 'wdieresis', 'we-cy', 'wgrave', 'x', 'y', 'yacute', 'ycircumflex', 'ydieresis', 'ydotaccent', 'ydotbelow', 'ygrave', 'yhookabove', 'yi-cy', 'ymacron', 'ytilde', 'z', 'zacute', 'zcaron', 'zdotaccent', 'zdotbelow'],
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
