<p align="center">
  <img src="https://jul-sh.github.io/Kerberos/images/hero.png" alt="Kerberos, a text serif in the Kis–Janson–Ehrhardt line" width="100%">
</p>

<p align="center">
  <a href="https://jul-sh.github.io/Kerberos/"><b>Live specimen</b></a> ·
  <a href="https://github.com/jul-sh/Kerberos/releases/latest"><b>Download</b></a> ·
  <a href="#build">Build</a> ·
  <a href="OFL.txt">SIL Open Font License</a>
</p>

# Kerberos

A text serif in the Kis–Janson–Ehrhardt line, for long reading on paper and on screens.

**Lineage.** This Dutch-baroque genre is a cornerstone of book typography. Miklós Tótfalusi Kis cut the original types in Amsterdam around 1685. They were sold for centuries as "Janson", revived by Monotype as Ehrhardt in 1938, and reworked by Matthew Butterick as Equity in 2011. The type is compact, sturdy and economical on the page. Every member of the line has been commercial; Kerberos is free under the OFL.

**Features.**
- Ehrhardt's details: the drooping ball ear of the g, gently curved bars on A and B, the straight angled leg of R.
- Three axes: weight 300–700; optical size 8–72 pt, which browsers follow automatically; grade −50 to +100, which changes contrast and never line length. At −50 the hairlines have Ehrhardt's contrast.
- Oldstyle figures by default, plus lining, tabular, fractions and small caps.
- The full set of f-ligatures: fi fl ff ffi ffl fj ffj fb fh fk ft ffb ffh ffk fft, and Th.
- Latin, Greek and Cyrillic.

**Heritage.** Drawn on Alegreya by Juan Pablo del Peral (Huerta Tipográfica, OFL 1.1). 

<img src="https://jul-sh.github.io/Kerberos/images/text.png" alt="Kerberos roman and italic at reading size" width="100%">
<img src="https://jul-sh.github.io/Kerberos/images/weights.png" alt="One line at weights 300 to 700, roman and italic" width="100%">
<img src="https://jul-sh.github.io/Kerberos/images/axes.png" alt="Optical sizes 8, 14 and 72 pt, and grades -50, 0 and +100" width="100%">
<img src="https://jul-sh.github.io/Kerberos/images/details.png" alt="Ligatures, small caps, figures and fractions" width="100%">

## Use

[Download the latest release](https://github.com/jul-sh/Kerberos/releases/latest).
Install the variable fonts or the static fonts, not both.

```css
@font-face { font-family: "Kerberos"; src: url("Kerberos-VF.woff2"); font-weight: 300 700; }
@font-face { font-family: "Kerberos"; src: url("Kerberos-Italic-VF.woff2"); font-weight: 300 700; font-style: italic; }
.dark-mode { font-variation-settings: "GRAD" -25; }   /* optical size follows font-size */
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
[Alegreya](https://github.com/huertatipografica/Alegreya) by Juan Pablo del Peral,
Huerta Tipográfica.
