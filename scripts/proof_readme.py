#!/usr/bin/env python3
"""Render the README specimen images into build/images/ (CI publishes them on Pages).

    make proof        (or: uv run python scripts/proof_readme.py)

Specimen text is set in the freshly built Kerberos variable fonts; labels in
Kerberos small caps, matching the specimen site: black on white, hard rules.
Optical size follows the displayed size, as browsers do (1 CSS px = 1 opsz).
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from build import ROOT, VARIABLE, Style

OUT = ROOT / "build/images"
FONTS = {"roman": VARIABLE[Style.ROMAN], "italic": VARIABLE[Style.ITALIC]}
SC = ["smcp", "c2sc", "lnum"]  # labels: small caps, as on the site
PAPER = (255, 255, 255)
INK = (0, 0, 0)
MUTED = (102, 102, 102)
W = 2400       # rendered at 2x; GitHub scales it down crisply
PAD = 80
RULE = 4       # the site's 2px rule, at 2x
HAIR = 2


def face(size: int, weight: float = 400, style: str = "roman", opsz: float | None = None,
         grade: float = 0, ytlc: float = 500) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONTS[style]), size, layout_engine=ImageFont.Layout.RAQM)
    if opsz is None:
        opsz = min(72, max(8, size / 2))  # displayed at half size
    values = {"Weight": weight, "Optical Size": opsz, "Grade": grade, "Lowercase Height": ytlc}
    font.set_variation_by_axes([values[axis["name"].decode() if isinstance(axis["name"], bytes)
                                       else axis["name"]] for axis in font.get_variation_axes()])
    return font


def canvas(height: int, title: str | None = None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """A block of the specimen: side rules, bottom rule, an uppercase mono label."""
    image = Image.new("RGB", (W, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, RULE - 1, height), fill=INK)
    draw.rectangle((W - RULE, 0, W, height), fill=INK)
    draw.rectangle((0, height - RULE, W, height), fill=INK)
    if title:
        draw.text((PAD, PAD), title.upper(), font=face(36, 700), features=SC, fill=INK)
    return image, draw


def hline(draw: ImageDraw.ImageDraw, y: int, width: int = HAIR, x0: int = PAD, x1: int = W - PAD) -> None:
    draw.rectangle((x0, y, x1, y + width - 1), fill=INK)


def hero() -> Image.Image:
    image, draw = canvas(980)
    draw.rectangle((0, 0, W, RULE - 1), fill=INK)
    draw.text((PAD - 14, 600), "Kerberos", font=face(520, 400), fill=INK, anchor="ls")
    hline(draw, 700, RULE)
    draw.text((PAD, 790), "A text serif in the", font=face(62, 400), fill=INK, anchor="ls")
    draw.text((PAD, 870), "Kis–Janson–Ehrhardt line.", font=face(62, 400), fill=INK, anchor="ls")
    facts = "WEIGHT · OPTICAL SIZE · GRADE · X-HEIGHT · OFL"
    draw.text((W - PAD, 790), facts, font=face(38, 500), features=SC, fill=INK, anchor="rs")
    return image


def text() -> Image.Image:
    image, draw = canvas(1080, "01 Reading")
    draw.text((PAD, 300), "Kis, Janson, Ehrhardt.", font=face(140, 400), fill=INK, anchor="ls")
    draw.text((PAD, 440), "The Dutch-baroque book face.", font=face(104, 400, "italic"), fill=INK, anchor="ls")
    hline(draw, 510, RULE)
    body = ("Dutch-baroque book type: cut by Miklós Tótfalusi Kis in Amsterdam "
            "around 1685, sold for centuries as Janson, revived by Monotype as "
            "Ehrhardt in 1938. Compact, sturdy and economical on the page; a "
            "cornerstone of book typography, now free under the OFL.")
    f = face(52, 400)
    words, line, y = body.split(), "", 610
    for word in words:
        trial = f"{line} {word}".strip()
        if f.getlength(trial) > 1480 and line:
            draw.text((PAD, y), line, font=f, fill=INK, anchor="ls")
            y += 70
            line = word
        else:
            line = trial
    draw.text((PAD, y), line, font=f, fill=INK, anchor="ls")
    # Figures, in a ruled side column.
    x = 1700
    draw.rectangle((x - 60, 540, x - 60 + HAIR - 1, 960), fill=INK)
    draw.text((x, 600), "OLDSTYLE (DEFAULT)", font=face(34, 500), features=SC, fill=MUTED, anchor="ls")
    draw.text((x, 690), "1879 2,048", font=face(64, 400), fill=INK, anchor="ls")
    draw.text((x, 780), "LINING", font=face(34, 500), features=SC, fill=MUTED, anchor="ls")
    draw.text((x, 870), "1879 2,048", font=face(64, 400), fill=INK, anchor="ls", features=["lnum"])
    return image


def weights() -> Image.Image:
    rows = ((300, "Light"), (400, "Regular"), (500, "Medium"), (600, "SemiBold"), (700, "Bold"))
    image, draw = canvas(220 + 150 * len(rows), "02 Weights")
    hline(draw, 180, RULE)
    for i, (w, name) in enumerate(rows):
        y = 300 + i * 150
        draw.text((PAD, y), f"{w} {name}".upper(), font=face(34, 500), features=SC, fill=INK, anchor="ls")
        draw.text((520, y), "Handgloves 1879", font=face(104, w), fill=INK, anchor="ls")
        draw.text((1500, y), "and italic", font=face(104, w, "italic"), fill=INK, anchor="ls")
        hline(draw, y + 40)
    return image


def axes() -> Image.Image:
    """Optical size, grade and lowercase height at one rendered size: what each axis changes."""
    sample = "Handgloves 1879"
    rows = [("Optical size", [(f"{o} pt", dict(opsz=o)) for o in (8, 14, 72)]),
            ("Grade", [(f"{g:+d}" if g else "0", dict(opsz=14, grade=g)) for g in (-50, 0, 100)]),
            ("Lowercase height", [(str(y), dict(opsz=14, ytlc=y)) for y in (500, 525, 550)])]
    rh, gap = 150, 110
    image, draw = canvas(220 + len(rows) * (gap + 3 * rh), "03 Optical size, grade and x-height")
    hline(draw, 180, RULE)
    y = 180
    for title, variants in rows:
        y += gap
        draw.text((PAD, y - 20), title.upper(), font=face(34, 700), features=SC, fill=INK, anchor="ls")
        for label, axes_values in variants:
            y += rh
            draw.text((PAD, y), label.upper(), font=face(34, 500), features=SC, fill=MUTED, anchor="ls")
            f = face(104, **axes_values)
            draw.text((520, y), sample, font=f, fill=INK, anchor="ls")
            if "grade" in axes_values or "ytlc" in axes_values:  # equal widths: the text never moves
                x = 520 + round(f.getlength(sample)) + 24
                draw.rectangle((x, y - 90, x + HAIR - 1, y + 10), fill=INK)
            hline(draw, y + 40)
    return image


def details() -> Image.Image:
    rows = (
        ("Ligatures", "liga", "fifty baffled fjord The", ["liga"], ["-liga"]),
        ("Small caps", "smcp", "Kerberos in Small Caps", ["smcp"], None),
        ("Lining figures", "lnum", "Since 1879: 2,048", ["lnum"], None),
        ("Fractions", "frac", "1/2 3/4 7/8", ["frac"], None),
    )
    top, rh = 200, 150
    image, draw = canvas(top + 70 + rh * len(rows) + PAD, "04 OpenType features")
    x0, x1, x2, x3 = PAD, 640, 1520, W - PAD
    # table frame and header
    y = top
    draw.text((x0 + 24, y + 48), "FEATURE", font=face(34, 700), features=SC, fill=INK, anchor="ls")
    draw.text((x1 + 24, y + 48), "ON", font=face(34, 700), features=SC, fill=INK, anchor="ls")
    draw.text((x2 + 24, y + 48), "OFF", font=face(34, 700), features=SC, fill=INK, anchor="ls")
    head = y + 70
    bottom = head + rh * len(rows)
    draw.rectangle((x0, y, x3, bottom), outline=INK, width=HAIR)
    hline(draw, head, RULE, x0, x3)
    for x in (x1, x2):
        draw.rectangle((x, y, x + HAIR - 1, bottom), fill=INK)
    for i, (name, tag, sample, on, off) in enumerate(rows):
        y = head + i * rh
        if i:
            hline(draw, y, HAIR, x0, x3)
        draw.text((x0 + 24, y + 70), name.upper(), font=face(34, 500), features=SC, fill=INK, anchor="ls")
        draw.text((x0 + 24, y + 110), tag.upper(), font=face(34, 500), features=SC, fill=MUTED, anchor="ls")
        draw.text((x1 + 24, y + 100), sample, font=face(66, 400), fill=INK, anchor="ls", features=on)
        draw.text((x2 + 24, y + 100), sample, font=face(66, 400), fill=INK, anchor="ls", features=off)
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, render in (("hero", hero), ("text", text), ("weights", weights), ("axes", axes),
                         ("details", details)):
        path = OUT / f"{name}.png"
        render().save(path, optimize=True)
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
