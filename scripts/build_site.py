"""Assemble the live specimen site into ``_site/`` for GitHub Pages.

    uv run python scripts/build_site.py [--out _site]

Copies ``site/`` and the built web fonts, generates the character catalogue
and build metadata from the compiled fonts, and packages a download archive.
Nothing here modifies the fonts; run the font build first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

import build
from package_release import semver, write_archive

ROOT = Path(__file__).resolve().parents[1]
VARIABLE = {"roman": build.VARIABLE[build.Style.ROMAN], "italic": build.VARIABLE[build.Style.ITALIC]}
WEB = {"Kerberos-Roman.woff2": build.WEB[build.Style.ROMAN],
       "Kerberos-Italic.woff2": build.WEB[build.Style.ITALIC],
       "Kerberos-SerifOnly-Roman.woff2": build.WEB_SERIFONLY[build.Style.ROMAN],
       "Kerberos-SerifOnly-Italic.woff2": build.WEB_SERIFONLY[build.Style.ITALIC]}
GROUPS = [
    ("uppercase", "Uppercase"),
    ("lowercase", "Lowercase"),
    ("accented", "Accented Latin"),
    ("extended", "Extended Latin"),
    ("figures", "Figures"),
    ("punctuation", "Punctuation & symbols"),
    ("greek", "Greek"),
    ("cyrillic", "Cyrillic"),
]
SYMBOLS = "¡¿«»‹›‘’‚“”„–—…·•⁂⁎⁑†‡§¶©®™°±×÷∗¢£¥€№ªº‰′″"
WEIGHT_KNOTS = (300, 400, 500, 600, 700)


def group_of(point: int) -> str | None:
    char = chr(point)
    name = unicodedata.name(char, "")
    category = unicodedata.category(char)
    if 0x41 <= point <= 0x5A:
        return "uppercase"
    if 0x61 <= point <= 0x7A:
        return "lowercase"
    if 0x30 <= point <= 0x39:
        return "figures"
    if category.startswith("L") and "LATIN" in name:
        return "accented" if point <= 0x17F else "extended"
    if category.startswith("L") and "GREEK" in name:
        return "greek"
    if category.startswith("L") and "CYRILLIC" in name:
        return "cyrillic"
    if (0x21 <= point <= 0x7E and category[0] in "PS") or char in SYMBOLS:
        return "punctuation"
    return None


def catalogue() -> dict:
    points: set[int] = set()
    for path in VARIABLE.values():
        with TTFont(path, lazy=True) as font:
            points |= set(font.getBestCmap() or {})
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    chars = []
    for point in sorted(points):
        group = group_of(point)
        if group:
            chars.append({"char": chr(point), "codepoint": point,
                          "name": unicodedata.name(chr(point), ""), "group": group})
    chars.sort(key=lambda c: (order[c["group"]], c["codepoint"]))
    groups = [{"id": g, "label": label, "count": sum(c["group"] == g for c in chars)}
              for g, label in GROUPS]
    return {"groups": [g for g in groups if g["count"]], "characters": chars}


def guide_metrics(font: TTFont) -> dict:
    """Heights in font units for the specimen's guide lines in one style: Regular at
    display size (with x-height at both ends of YTLC), plus a (wght, CALE) grid for
    the Character Inspector."""
    cmap = font.getBestCmap()
    axes = {axis.axisTag: axis for axis in font["fvar"].axes}
    cale_min = axes["CALE"].minValue if "CALE" in axes else 0.0
    cale_max = axes["CALE"].maxValue if "CALE" in axes else 0.0
    display = {
        "wght": 400,
        "opsz": axes["opsz"].maxValue,
        "GRAD": 0,
        "YTLC": axes["YTLC"].minValue,
        "CALE": cale_min,
    }

    def bounds(char: str, **location: float) -> tuple[float, float, float, float]:
        glyphs = font.getGlyphSet(location={**display, **location})
        pen = BoundsPen(glyphs)
        glyphs[cmap[ord(char)]].draw(pen)
        return pen.bounds

    def sample_point(wght: float, cale: float) -> dict[str, float]:
        return {
            "ascender": round(bounds("h", wght=wght, CALE=cale)[3], 1),
            "capHeight": round(bounds("H", wght=wght, CALE=cale)[3], 1),
            "xHeight": round(bounds("x", wght=wght, CALE=cale)[3], 1),
            "descender": round(bounds("p", wght=wght, CALE=cale)[1], 1),
        }

    inspector = {
        str(w): {"0": sample_point(w, cale_min), "100": sample_point(w, cale_max)}
        for w in WEIGHT_KNOTS
    }
    return {
        "unitsPerEm": font["head"].unitsPerEm,
        "ascender": round(bounds("h")[3]),
        "capHeight": round(bounds("H")[3]),
        "xHeight": round(bounds("x")[3]),
        "xHeightTall": round(bounds("x", YTLC=axes["YTLC"].maxValue)[3]),
        "descender": round(bounds("p")[1]),
        "inspector": inspector,
    }


def metadata(catalogue_bytes: bytes) -> dict:
    with TTFont(VARIABLE["roman"]) as font:
        version = font["name"].getDebugName(5).replace("Version", "").strip().split(";")[0]
        glyphs = len(font.getGlyphOrder())
        features = len({r.FeatureTag for r in font["GSUB"].table.FeatureList.FeatureRecord})
    guides = {}
    for style, path in VARIABLE.items():
        with TTFont(path) as font:
            guides[style] = guide_metrics(font)
    commit = os.environ.get("GITHUB_SHA", "")
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = ""
    digest = hashlib.sha256()
    for name in sorted(WEB):
        digest.update(WEB[name].read_bytes())
    for extra in (ROOT / "site/style.css", ROOT / "site/app.js", ROOT / "OFL.txt", ROOT / "README.md"):
        digest.update(extra.read_bytes())
    digest.update(catalogue_bytes)
    build_id = digest.hexdigest()[:8]
    return {"version": semver(version), "glyphs": glyphs, "features": features, "metrics": guides,
            "commit": commit, "date": dt.date.today().isoformat(), "build": build_id}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="_site")
    raw_out = Path(parser.parse_args().out)
    out = raw_out.resolve() if raw_out.is_absolute() else (ROOT / raw_out).resolve()
    protected_roots = tuple((ROOT / d).resolve() for d in ("site", "fonts", "sources", "scripts", ".git"))
    if (
        out == ROOT
        or out in ROOT.parents
        or out in protected_roots
        or any(p in out.parents for p in protected_roots)
        or len(out.parts) < 3
    ):
        raise SystemExit(f"refusing unsafe output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    for child in list(out.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(ROOT / "site", out, dirs_exist_ok=True)
    (out / "fonts").mkdir(exist_ok=True)
    for name, source in WEB.items():
        shutil.copy2(source, out / "fonts" / name)
    (out / "data").mkdir(exist_ok=True)
    catalogue_text = json.dumps(catalogue(), ensure_ascii=False)
    meta = metadata(catalogue_text.encode("utf-8"))
    (out / "data/characters.json").write_text(catalogue_text, encoding="utf-8")
    (out / "data/meta.json").write_text(json.dumps(meta), encoding="utf-8")
    # Keep site/index.html and _site/index.html synced with the built version and glyph count
    canonical_out = (ROOT / "_site").resolve()
    pages_to_sync = (ROOT / "site/index.html", out / "index.html") if out == canonical_out else (out / "index.html",)
    for page in pages_to_sync:
        html = page.read_text(encoding="utf-8")
        for key, value in (("version", meta["version"]), ("foot-version", meta["version"]),
                           ("glyphs", f"{meta['glyphs']:,}")):
            html = re.sub(rf'(<span id="{key}">)[^<]*(</span>)', rf"\g<1>{value}\g<2>", html)
        page.write_text(html, encoding="utf-8")
    # Stamp built web fonts, CSS, JS, archive and character data with version + build hash
    cache_tag = f"?v={meta['version']}-{meta['build']}"
    html_text = (out / "index.html").read_text(encoding="utf-8")
    for font_name in WEB:
        html_text = html_text.replace(f'"fonts/{font_name}"', f'"fonts/{font_name}{cache_tag}"')
    html_text = (
        html_text
        .replace('href="style.css"', f'href="style.css{cache_tag}"')
        .replace('src="app.js"', f'src="app.js{cache_tag}"')
        .replace('href="Kerberos.zip"', f'href="Kerberos.zip{cache_tag}"')
    )
    (out / "index.html").write_text(html_text, encoding="utf-8")

    css_text = (out / "style.css").read_text(encoding="utf-8")
    for font_name in WEB:
        css_text = css_text.replace(f'"fonts/{font_name}"', f'"fonts/{font_name}{cache_tag}"')
    (out / "style.css").write_text(css_text, encoding="utf-8")

    js_text = (out / "app.js").read_text(encoding="utf-8")
    js_text = js_text.replace('"data/characters.json"', f'"data/characters.json{cache_tag}"')
    (out / "app.js").write_text(js_text, encoding="utf-8")

    # README images are rendered by `make proof` and served from Pages, so the
    # README always shows the current build: https://jul-sh.github.io/Kerberos/images/
    images = ROOT / "build/images"
    if images.is_dir():
        shutil.copytree(images, out / "images")
        shutil.copy2(images / "hero.png", out / "specimen.png")
    write_archive(out / "Kerberos.zip", meta["version"])
    (out / "results").mkdir(exist_ok=True)
    (out / ".nojekyll").write_text("")
    shown = out.relative_to(ROOT) if ROOT in out.parents else out
    print(f"Site written to {shown} (Kerberos {meta['version']})")


if __name__ == "__main__":
    main()
