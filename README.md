<p align="center">
  <img src="https://jul-sh.github.io/Kerberos/images/hero.png" alt="Kerberos, a text serif in the Kis–Janson line" width="100%">
</p>

<p align="center">
  <a href="https://jul-sh.github.io/Kerberos/"><b>Live specimen</b></a> ·
  <a href="https://github.com/jul-sh/Kerberos/releases/latest"><b>Download</b></a> ·
  <a href="#build">Build</a> ·
  <a href="OFL.txt">SIL Open Font License</a>
</p>

# Kerberos

A text serif in the Kis–Janson line, for long reading on paper and on screens.

**Lineage.** This Dutch-baroque genre is a cornerstone of book typography. Miklós Tótfalusi Kis cut the original types in Amsterdam around 1685, which were sold for centuries as "Janson". The type is compact, sturdy and economical on the page. Every historical member of the line has been commercial; Kerberos is free under the OFL.

**Features.**
- Dutch-baroque details: the drooping ball ear of the g, gently curved bars on A and B, the straight angled leg of R.
- Four axes: weight 300–700; optical size 8–72 pt, which browsers follow automatically and which below 20 pt also raises the x-height (like `YTLC` 530 at 12 pt, 517 at 16 pt); grade −50 to +100, which changes contrast and never line length (at −50 the hairlines have Dutch-baroque contrast); and lowercase height (`YTLC`) 500–550, which makes the x-height up to 10% taller, with small caps and oldstyle figures, and keeps every letter's width.
- Oldstyle figures by default, plus lining, tabular, fractions and small caps.
- The full set of f-ligatures: fi fl ff ffi ffl fj ffj fb fh fk ft ffb ffh ffk fft, and Th.
- Latin, Greek and Cyrillic.

**Heritage.** Drawn on Alegreya by Juan Pablo del Peral (Huerta Tipográfica, OFL 1.1) and Crimson Pro by Jacques Le Bailly and Sebastian Kosch (OFL 1.1). 

<img src="https://jul-sh.github.io/Kerberos/images/text.png" alt="Kerberos roman and italic at reading size" width="100%">
<img src="https://jul-sh.github.io/Kerberos/images/weights.png" alt="One line at weights 300 to 700, roman and italic" width="100%">
<img src="https://jul-sh.github.io/Kerberos/images/axes.png" alt="Optical sizes 8, 14 and 72 pt, grades -50, 0 and +100, and lowercase heights 500, 525 and 550" width="100%">
<img src="https://jul-sh.github.io/Kerberos/images/details.png" alt="Ligatures, small caps, figures and fractions" width="100%">

## Use

[Download the latest release](https://github.com/jul-sh/Kerberos/releases/latest).
Install the variable fonts or the static fonts, not both.

```css
@font-face { font-family: "Kerberos"; src: url("Kerberos-VF.woff2"); font-weight: 300 700; }
@font-face { font-family: "Kerberos"; src: url("Kerberos-Italic-VF.woff2"); font-weight: 300 700; font-style: italic; }
.dark-mode { font-variation-settings: "GRAD" -25; }   /* optical size follows font-size */
.ui-small { font-variation-settings: "YTLC" 550; }    /* 10% taller x-height */
```

## Build

Requires [Nix](https://nixos.org/download) or Docker; `make` enters the dev shell.

```sh
make build   # fonts/ from sources/*.glyphs
make test    # outline, naming and shaping checks
make lint    # Google Fonts QA (fontspector)
make site    # specimen and README images in _site/
make all     # everything, plus release zip
```

CI builds every push to `main`. It publishes the release, the
[specimen](https://jul-sh.github.io/Kerberos/) and the images above.

## License

[SIL Open Font License 1.1](OFL.txt). Built from
[Alegreya](https://github.com/huertatipografica/Alegreya) by Juan Pablo del Peral
(Huerta Tipográfica) and [Crimson Pro](https://github.com/Fonthausen/CrimsonPro)
by Jacques Le Bailly and Sebastian Kosch.
