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
from zipfile import ZIP_DEFLATED, ZipFile

from fontTools.ttLib import TTFont

import build

ROOT = Path(__file__).resolve().parents[1]
VARIABLE = {"roman": build.VARIABLE[build.Style.ROMAN], "italic": build.VARIABLE[build.Style.ITALIC]}
WEB = {"Kerberos-Roman.woff2": build.WEB[build.Style.ROMAN],
       "Kerberos-Italic.woff2": build.WEB[build.Style.ITALIC]}
GROUPS = [
    ("uppercase", "A–Z"),
    ("lowercase", "a–z"),
    ("accented", "Accented Latin"),
    ("extended", "Extended Latin"),
    ("figures", "Figures"),
    ("punctuation", "Punctuation & symbols"),
    ("greek", "Greek"),
    ("cyrillic", "Cyrillic"),
]
SYMBOLS = "¡¿«»‹›‘’‚“”„–—…·•†‡§¶©®™°±×÷¢£¥€№ªº‰′″"



def semver(version: str) -> str:
    """Font version X.YZZ (Google Fonts style) as release version X.Y.Z, e.g. 1.100 -> 1.1.0."""
    major, minor = version.split(".")
    return f"{int(major)}.{int(minor) // 100}.{int(minor) % 100}"

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


def metadata() -> dict:
    with TTFont(VARIABLE["roman"]) as font:
        version = font["name"].getDebugName(5).replace("Version", "").strip().split(";")[0]
        glyphs = len(font.getGlyphOrder())
        features = len({r.FeatureTag for r in font["GSUB"].table.FeatureList.FeatureRecord})
    commit = os.environ.get("GITHUB_SHA", "")
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = ""
    build_id = hashlib.sha256(
        b"".join(WEB[name].read_bytes() for name in sorted(WEB))
    ).hexdigest()[:8]
    return {"version": semver(version), "glyphs": glyphs, "features": features,
            "commit": commit, "date": dt.date.today().isoformat(), "build": build_id}


def archive(out: Path, version: str) -> None:
    with ZipFile(out, "w", ZIP_DEFLATED) as bundle:
        for sub in ("variable", "static", "web"):
            for path in sorted((ROOT / "fonts" / sub).glob("*")):
                bundle.write(path, f"Kerberos-{version}/{sub}/{path.name}")
        bundle.write(ROOT / "OFL.txt", f"Kerberos-{version}/OFL.txt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="_site")
    out = (ROOT / parser.parse_args().out).resolve()
    if out == ROOT or ROOT not in out.parents:
        raise SystemExit("output directory must be inside the repository")
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
    meta = metadata()
    (out / "data/characters.json").write_text(json.dumps(catalogue(), ensure_ascii=False), encoding="utf-8")
    (out / "data/meta.json").write_text(json.dumps(meta), encoding="utf-8")
    # Keep site/index.html and _site/index.html synced with the built version and glyph count
    for page in (ROOT / "site/index.html", out / "index.html"):
        html = page.read_text(encoding="utf-8")
        for key, value in (("version", meta["version"]), ("foot-version", meta["version"]),
                           ("glyphs", str(meta["glyphs"]))):
            html = re.sub(rf'(<span id="{key}">)[^<]*(</span>)', rf"\g<1>{value}\g<2>", html)
        page.write_text(html, encoding="utf-8")
    # Stamp built web font URLs with version + build hash so new releases never hit stale caches
    cache_tag = f"?v={meta['version']}-{meta['build']}"
    for asset in (out / "index.html", out / "style.css"):
        text = asset.read_text(encoding="utf-8")
        for font_name in WEB:
            text = text.replace(f"fonts/{font_name}", f"fonts/{font_name}{cache_tag}")
        asset.write_text(text, encoding="utf-8")
    # README images are rendered by `make proof` and served from Pages, so the
    # README always shows the current build: https://jul-sh.github.io/Kerberos/images/
    images = ROOT / "build/images"
    if images.is_dir():
        shutil.copytree(images, out / "images")
        shutil.copy2(images / "hero.png", out / "specimen.png")
    archive(out / "Kerberos.zip", meta["version"])
    (out / ".nojekyll").write_text("")
    print(f"Site written to {out.relative_to(ROOT)} (Kerberos {meta['version']})")


if __name__ == "__main__":
    main()
