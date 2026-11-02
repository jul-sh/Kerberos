// Kerberos specimen. DOM is built with createElement/textContent only.
// Nothing animates on its own: every change follows a user input.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") node.textContent = value;
    else if (key === "style") Object.assign(node.style, value);
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

function bindRange(id, onChange, format = (v) => v) {
  const input = $(id);
  const out = $(`${id}-out`);
  const update = () => {
    if (out) out.textContent = format(input.value);
    onChange(Number(input.value));
  };
  input.addEventListener("input", update);
  update();
}

/* An eased glide between two values, started by a click; returns its cancel function. */
/** @typedef {'stepping' | 'done'} TweenPhase */
function tween(from, to, onFrame, duration = 560) {
  if (reducedMotion.matches || from === to) {
    onFrame(to, "done");
    return () => {};
  }
  const start = performance.now();
  let frame = requestAnimationFrame(function step(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
    /** @type {TweenPhase} */
    const phase = t < 1 ? "stepping" : "done";
    onFrame(from + (to - from) * eased, phase);
    if (phase === "stepping") frame = requestAnimationFrame(step);
  });
  return () => cancelAnimationFrame(frame);
}

function mountStops(container, stopsSpec, min, max, toSlider, labelPrefix, onSelect) {
  return stopsSpec.map(([stopValue, label, rank]) => {
    const p = (toSlider(stopValue) - min) / (max - min);
    const button = el("button", {
      type: "button",
      class: "mono stop",
      text: label,
      "aria-label": `${labelPrefix}: ${label}`,
    });
    button.style.setProperty("--p", String(p));
    if (p <= 0) button.classList.add("is-start");
    if (p >= 1) button.classList.add("is-end");
    if (rank === "minor") button.dataset.minor = "";
    button.addEventListener("click", () => onSelect(stopValue));
    container.append(button);
    return { value: stopValue, button };
  });
}

/* OpenType optical sizes are points: 96 CSS pixels = 72 points. */
const OPSZ_MIN = 6;
const OPSZ_MAX = 72;
const opticalSizeForPixels = (pixels) => clamp(pixels * 72 / 96, OPSZ_MIN, OPSZ_MAX);
/* Optical-size sliders run 0–1000 on a logarithmic scale, so 6, 12, 24 and 48 pt are evenly spaced. */
const opszFromSlider = (position) => OPSZ_MIN * (OPSZ_MAX / OPSZ_MIN) ** (position / 1000);
const sliderFromOpsz = (points) => 1000 * Math.log(points / OPSZ_MIN) / Math.log(OPSZ_MAX / OPSZ_MIN);
const formatPoints = (points) => `${Number(points.toFixed(points < 10 ? 1 : 0))} pt`;
const formatSigned = (value) => {
  const rounded = Math.round(Number(value));
  return rounded > 0 ? `+${rounded}` : rounded < 0 ? `−${-rounded}` : "0";
};

function refreshOpticalSizes(root = document.body) {
  // Read first, then write, so a large character grid does not force a layout per cell.
  const sizes = [root, ...root.querySelectorAll("*")].map((node) => [node,
    String(opticalSizeForPixels(parseFloat(getComputedStyle(node).fontSize)))]);
  for (const [node, points] of sizes) {
    if (node.style.getPropertyValue("--auto-opsz") !== points) node.style.setProperty("--auto-opsz", points);
  }
}

/* Vertical metrics in font units at display size, Regular, for the guide lines.
   data/meta.json carries the values measured from the current build. */
/** @typedef {'roman' | 'italic'} Style */
const metrics = {
  roman: { unitsPerEm: 1000, ascender: 749, capHeight: 703, xHeight: 461, xHeightTall: 507, descender: -233 },
  italic: { unitsPerEm: 1000, ascender: 747, capHeight: 695, xHeight: 464, xHeightTall: 510, descender: -231 },
};
const xHeightAt = (m, ytlc) => m.xHeight + (m.xHeightTall - m.xHeight) * (ytlc - 500) / 50;
const guideRefreshers = new Set();
const refreshGuides = () => guideRefreshers.forEach((refresh) => refresh());

/** Draws each guide line at a height in font units (of metrics `m`) above the baseline of
    `sample`. The stage's empty .probe sits on that baseline. */
function placeGuides(stage, sample, heights, m) {
  const guides = $(".guides", stage);
  const fontSize = parseFloat(getComputedStyle(sample).fontSize);
  const baseline = $(".probe", stage).getBoundingClientRect().top - stage.getBoundingClientRect().top;
  for (const [selector, units] of Object.entries(heights)) {
    const y = baseline - units / m.unitsPerEm * fontSize;
    $(selector, guides).style.transform = `translateY(${y.toFixed(2)}px)`;
  }
  guides.classList.add("is-ready");
}

/* Axes: each block drives one axis of its sample from a slider and a row of named stops. */
const AXES = {
  wght: {
    initial: 400,
    toSlider: (v) => v,
    fromSlider: (s) => s,
    apply: (node, v) => { node.style.fontWeight = String(v); },
    format: (v) => String(Math.round(v)),
    stops: [[300, "Light"], [400, "Regular"], [500, "Medium", "minor"], [600, "SemiBold", "minor"], [700, "Bold"]],
  },
  opsz: {
    initial: 72,
    toSlider: sliderFromOpsz,
    fromSlider: opszFromSlider,
    apply: (node, v) => node.style.setProperty("--manual-opsz", String(v)),
    format: formatPoints,
    stops: [[6, "6"], [12, "12"], [24, "24", "minor"], [48, "48", "minor"], [72, "72"]],
  },
  GRAD: {
    initial: 0,
    toSlider: (v) => v,
    fromSlider: (s) => s,
    apply: (node, v) => node.style.setProperty("--grade", String(v)),
    format: formatSigned,
    stops: [[-50, "−50"], [0, "0"], [50, "+50", "minor"], [100, "+100"]],
  },
  YTLC: {
    initial: 500,
    toSlider: (v) => v,
    fromSlider: (s) => s,
    apply: (node, v) => node.style.setProperty("--ytlc", String(v)),
    format: (v) => String(Math.round(v)),
    stops: [[500, "500"], [525, "525", "minor"], [550, "550"]],
    guides: (v, m) => ({ ".guide-cap": m.capHeight, ".guide-x": xHeightAt(m, v), ".guide-base": 0 }),
  },
  CALE: {
    initial: 0,
    toSlider: (v) => v,
    fromSlider: (s) => s,
    apply: (node, v) => node.style.setProperty("--cale", String(v)),
    format: (v) => String(Math.round(v)),
    stops: [[0, "Kerberos (0)"], [25, "25", "minor"], [50, "50"], [75, "75", "minor"], [100, "Caleuche (100)"]],
  },
};

for (const panel of $$(".axis")) {
  const spec = AXES[panel.dataset.axis];
  const input = $("input[type=range]", panel);
  const sample = $(".axis-sample", panel);
  const output = $(".axis-value", panel);
  const stage = $(".axis-stage", panel);
  // The axis name without its section number, which is hidden from assistive technology.
  const name = [...$(".label", panel).childNodes]
    .filter((node) => !(node instanceof Element && node.getAttribute("aria-hidden") === "true"))
    .map((node) => node.textContent).join("").trim();
  const min = Number(input.min);
  const max = Number(input.max);
  const toggle = $(".toggle:not(.toggle-width)", panel);
  const widthToggle = $(".toggle-width", panel);
  let value = spec.initial;
  /** @type {Style} */
  let style = "roman";
  /** @type {'proportional' | 'serif-only'} */
  let widthMode = "proportional";
  let cancel = () => {};

  const placeAxisGuides = () => {
    if (spec.guides) placeGuides(stage, sample, spec.guides(value, metrics[style]), metrics[style]);
  };
  const render = (next, position = spec.toSlider(next)) => {
    value = next;
    spec.apply(sample, next);
    output.textContent = spec.format(next);
    input.value = String(position);
    input.setAttribute("aria-valuetext", spec.format(next));
    for (const stop of stops) stop.button.classList.toggle("is-active", Math.abs(stop.value - next) < 0.5);
    placeAxisGuides();
  };
  const glideTo = (target) => {
    cancel();
    const to = spec.toSlider(target);
    cancel = tween(spec.toSlider(value), to, (position, phase) => {
      switch (phase) {
        case "stepping": render(spec.fromSlider(position), position); break;
        case "done": render(target, to); break;
      }
    });
  };
  const stops = mountStops($(".stops", panel), spec.stops, min, max, spec.toSlider, name, glideTo);
  input.addEventListener("input", () => {
    cancel();
    const position = Number(input.value);
    render(spec.fromSlider(position), position);
  });
  toggle.setAttribute("aria-label", `Italic ${name.toLowerCase()} sample`);
  toggle.addEventListener("click", () => {
    switch (style) {
      case "roman": style = "italic"; break;
      case "italic": style = "roman"; break;
    }
    toggle.setAttribute("aria-pressed", String(style === "italic"));
    sample.classList.toggle("is-italic", style === "italic");
    placeAxisGuides();
  });
  if (widthToggle) {
    widthToggle.setAttribute("aria-label", "Fixed width (serifness only) Caleuche sample");
    widthToggle.addEventListener("click", () => {
      switch (widthMode) {
        case "proportional": widthMode = "serif-only"; break;
        case "serif-only": widthMode = "proportional"; break;
      }
      widthToggle.setAttribute("aria-pressed", String(widthMode === "serif-only"));
      sample.classList.toggle("is-serif-only", widthMode === "serif-only");
    });
  }
  if (spec.guides) guideRefreshers.add(placeAxisGuides);
  render(value);
}

/* Type tester */
const SAMPLES = {
  pangram: [
    "Sphinx of black quartz, judge my vow.",
    "The quick brown fox jumps over the lazy dog.",
    "Waltz, bad nymph, for quick jigs vex.",
  ],
  alphabet: [
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz 0123456789",
  ],
  essay: [
    "Kerberos is a text serif in the Kis–Janson line: Dutch-baroque book type, compact and sturdy, first cut in Amsterdam around 1685.",
  ],
  ligatures: [
    "The baffled fjord · affluent, flagship, offshoot, fifty-five, fjords, kafkaesque.",
  ],
  figures: [
    "0123456789 1,234.50 ½ ¼ 18%",
    "§ 12 ¶ 4 No. 7 · 26.10.2026 · €49 £35 $28",
  ],
  languages: [
    "Ærøskøbing, Świętokrzyskie, Đakovo, Ísafjörður, Şanlıurfa.",
    "Ελληνικά · Кириллица · Châteauneuf-du-Pape",
  ],
};
const tester = $("#tester");
if (tester) {
  const setSample = (name) => {
    const lines = SAMPLES[name] || SAMPLES.pangram;
    tester.replaceChildren(...lines.map((t) => el("p", { text: t })));
    refreshOpticalSizes(tester);
  };
  $("#t-sample").addEventListener("change", (e) => setSample(e.target.value));
  $("#t-style").addEventListener("change", (e) => tester.style.setProperty("--t-style", e.target.value));
  const caleModeSelect = $("#t-cale-mode");
  if (caleModeSelect) {
    caleModeSelect.addEventListener("change", (e) => {
      tester.classList.toggle("is-serif-only", e.target.value === "serif-only");
    });
  }
  /** @typedef {{ mode: 'auto' } | { mode: 'manual', value: number }} OpticalSize */
  /** @type {{ wght: number, CALE: number, GRAD: number, YTLC: number, optical: OpticalSize }} */
  const variation = { wght: 400, CALE: 0, GRAD: 0, YTLC: 500, optical: { mode: "auto" } };
  const formatTesterOpsz = (points) => String(Number(points.toFixed(2)));
  const applyOpticalMode = () => {
    const slider = $("#t-opsz");
    switch (variation.optical.mode) {
      case "auto": {
        tester.style.removeProperty("--manual-opsz");
        const points = opticalSizeForPixels(parseFloat(getComputedStyle(tester).fontSize));
        slider.value = String(points);
        slider.disabled = true;
        $("#t-opsz-out").textContent = `auto ${formatTesterOpsz(points)} pt`;
        break;
      }
      case "manual":
        tester.style.setProperty("--manual-opsz", String(variation.optical.value));
        slider.disabled = false;
        slider.value = String(variation.optical.value);
        $("#t-opsz-out").textContent = `${formatTesterOpsz(variation.optical.value)} pt`;
        break;
    }
  };
  const applyVariation = () => {
    tester.style.fontWeight = String(variation.wght);
    tester.style.setProperty("--cale", String(variation.CALE));
    tester.style.setProperty("--grade", String(variation.GRAD));
    tester.style.setProperty("--ytlc", String(variation.YTLC));
    applyOpticalMode();
    refreshOpticalSizes(tester);
  };
  const caleInput = $("#t-cale");
  const caleOut = $("#t-cale-out");
  let cancelCale = () => {};
  const renderCale = (next) => {
    variation.CALE = next;
    const formatted = AXES.CALE.format(next);
    tester.style.setProperty("--cale", String(next));
    caleInput.value = String(next);
    caleInput.setAttribute("aria-valuetext", formatted);
    caleOut.textContent = formatted;
    for (const stop of caleStops) {
      stop.button.classList.toggle("is-active", Math.abs(stop.value - next) < 0.5);
    }
  };
  const glideCale = (target) => {
    cancelCale();
    cancelCale = tween(variation.CALE, target, (position, phase) => {
      switch (phase) {
        case "stepping": renderCale(position); break;
        case "done": renderCale(target); break;
      }
    });
  };
  const caleStops = mountStops(
    $("#t-cale-stops"),
    AXES.CALE.stops,
    Number(caleInput.min),
    Number(caleInput.max),
    AXES.CALE.toSlider,
    "Caleuche",
    glideCale,
  );
  caleInput.addEventListener("input", () => {
    cancelCale();
    renderCale(Number(caleInput.value));
  });
  bindRange("#t-weight", (w) => { variation.wght = w; applyVariation(); });
  bindRange("#t-size", (s) => { tester.style.setProperty("--t-size", `${s}px`); applyVariation(); });
  $("#t-optical-mode").addEventListener("change", (event) => {
    switch (event.target.value) {
      case "auto": variation.optical = { mode: "auto" }; break;
      case "manual": variation.optical = { mode: "manual", value: Number($("#t-opsz").value) }; break;
    }
    applyVariation();
  });
  $("#t-opsz").addEventListener("input", (event) => {
    variation.optical = { mode: "manual", value: Number(event.target.value) };
    applyVariation();
  });
  bindRange("#t-grad", (g) => { variation.GRAD = g; applyVariation(); }, formatSigned);
  bindRange("#t-ytlc", (y) => { variation.YTLC = y; applyVariation(); });
  bindRange("#t-leading", (l) => tester.style.setProperty("--t-leading", String(l)), (v) => Number(v).toFixed(2));
  setSample("pangram");
  renderCale(variation.CALE);
  applyVariation();
}

/* Every specimen text is editable; controls are not, so editing can't remove them.
   Paste and drop insert plain text only; single-line samples stay on one line. */
const EDITABLE = ["#title", "#tester", ".axis-sample", ".styles span", "#big", "#essay"];
const makeEditable = () => {
  for (const node of document.querySelectorAll(EDITABLE.join(","))) {
    node.contentEditable = "true";
    node.spellcheck = false;
  }
};
const editableHost = (event) =>
  event.target instanceof Element ? event.target.closest('[contenteditable="true"]') : null;
document.addEventListener("paste", (event) => {
  const host = editableHost(event);
  if (!host) return;
  event.preventDefault();
  let plain = (event.clipboardData || window.clipboardData).getData("text/plain").slice(0, 5000);
  if (host.hasAttribute("data-single-line")) plain = plain.replace(/\s+/g, " ");
  document.execCommand("insertText", false, plain);
});
document.addEventListener("drop", (event) => { if (editableHost(event)) event.preventDefault(); });
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  const host = editableHost(event);
  if (host && host.hasAttribute("data-single-line")) {
    event.preventDefault();
    host.blur();
  }
});

/* Features: each tile shows the feature on; a click compares it with the feature off. */
/** @typedef {{ kind: 'whole', lines: string[], on: object, off: object }
              | { kind: 'parts', parts: Array<string | { text: string, on: object }> }} FeatureSample */
/** @type {Array<{ name: string, tag: string, sample: FeatureSample }>} */
const FEATURES = [
  { name: "Ligatures", tag: "liga", sample: { kind: "whole", lines: ["The baffled fjord"], on: {},
    off: { fontVariantLigatures: "no-common-ligatures", fontFeatureSettings: "\"liga\" 0, \"clig\" 0" } } },
  { name: "Small caps", tag: "smcp", sample: { kind: "whole", lines: ["Kerberos in Small Caps"],
    on: { fontVariantCaps: "small-caps" }, off: {} } },
  { name: "Lining figures", tag: "lnum", sample: { kind: "whole", lines: ["Since 1879: 2,048 pages"],
    on: { fontVariantNumeric: "lining-nums" }, off: {} } },
  { name: "Tabular figures", tag: "tnum", sample: { kind: "whole", lines: ["1,111.11", "8,080.88"],
    on: { fontVariantNumeric: "tabular-nums" }, off: {} } },
  { name: "Fractions", tag: "frac", sample: { kind: "whole", lines: ["1/2 3/4 7/8 15/16"],
    on: { fontVariantNumeric: "diagonal-fractions" }, off: {} } },
  { name: "Ordinals", tag: "ordn", sample: { kind: "whole", lines: ["1st 2nd 3rd No 4"],
    on: { fontVariantNumeric: "ordinal" }, off: {} } },
  { name: "Super- and subscripts", tag: "sups subs", sample: { kind: "parts", parts: [
    "E = mc", { text: "2", on: { fontVariantPosition: "super" } },
    " · H", { text: "2", on: { fontVariantPosition: "sub" } }, "O"] } },
  { name: "Roman numerals", tag: "ss01", sample: { kind: "whole", lines: ["MMXXVI · XIV"],
    on: { fontFeatureSettings: "\"ss01\" 1" }, off: {} } },
];
const RESET = { fontVariantLigatures: "", fontVariantCaps: "", fontVariantNumeric: "",
  fontVariantPosition: "", fontFeatureSettings: "" };
/** @typedef {'on' | 'off'} FeatureState */
for (const feature of FEATURES) {
  const sample = el("span", { class: "feature-sample" });
  /** @type {(mode: FeatureState) => void} */
  let apply = () => {};
  switch (feature.sample.kind) {
    case "whole": {
      const { lines, on, off } = feature.sample;
      sample.append(...lines.map((line) => el("span", { class: "line", text: line })));
      apply = (mode) => Object.assign(sample.style, RESET, mode === "on" ? on : off);
      break;
    }
    case "parts": {
      const targets = [];
      for (const part of feature.sample.parts) {
        if (typeof part === "string") {
          sample.append(part);
        } else {
          const span = el("span", { text: part.text });
          sample.append(span);
          targets.push([span, part.on]);
        }
      }
      apply = (mode) => {
        for (const [span, on] of targets) Object.assign(span.style, RESET, mode === "on" ? on : {});
      };
      break;
    }
  }
  const state = el("span", { class: "state", "aria-hidden": "true" });
  const tile = el("button", { type: "button", class: "feature", "aria-pressed": "true", title: feature.tag }, [
    el("span", { class: "mono feature-head" }, [el("span", { text: feature.name }), state]),
    sample,
  ]);
  /** @type {FeatureState} */
  let mode = "on";
  const renderFeature = (next) => {
    mode = next;
    switch (mode) {
      case "on":
        tile.setAttribute("aria-pressed", "true");
        state.textContent = "On";
        break;
      case "off":
        tile.setAttribute("aria-pressed", "false");
        state.textContent = "Off";
        break;
    }
    apply(mode);
  };
  tile.addEventListener("click", () => renderFeature(mode === "on" ? "off" : "on"));
  renderFeature("on");
  $("#feature-grid").append(tile);
}

/* Characters: one grid in groups; arrow keys move the selection. */
(async () => {
  const grid = $("#grid");
  let data;
  try {
    const response = await fetch("data/characters.json", { credentials: "same-origin" });
    if (!response.ok) throw new Error(String(response.status));
    data = await response.json();
  } catch {
    grid.replaceChildren(el("p", { class: "mono grid-empty", text: "Character list unavailable." }));
    return;
  }
  const chars = Array.isArray(data.characters) ? data.characters : [];
  const groups = Array.isArray(data.groups) ? data.groups : [];
  $("#glyph-count").textContent = chars.length.toLocaleString("en-US");

  const big = $("#big");
  const stage = $(".big-stage");
  /** @type {Style} */
  let glyphStyle = "roman";
  const placeBigGuides = () => {
    const m = metrics[glyphStyle];
    placeGuides(stage, big, {
      ".guide-asc": m.ascender, ".guide-cap": m.capHeight, ".guide-x": m.xHeight,
      ".guide-base": 0, ".guide-desc": m.descender,
    }, m);
  };
  guideRefreshers.add(placeBigGuides);
  const glyphTargets = [grid, big];
  bindRange("#g-weight", (w) => glyphTargets.forEach((n) => n.style.setProperty("--g-wght", String(w))));
  bindRange("#g-cale", (c) => glyphTargets.forEach((n) => n.style.setProperty("--g-cale", String(c))));
  for (const radio of $$('input[name="g-style"]')) {
    radio.addEventListener("change", () => {
      glyphStyle = radio.value === "italic" ? "italic" : "roman";
      glyphTargets.forEach((n) => n.style.setProperty("--g-style", radio.value));
      placeBigGuides();
    });
  }

  const items = new WeakMap();
  // "LATIN SMALL LETTER E WITH ACUTE" is also found as "eacute", the way glyphs are named.
  const glyphKeys = new Map(chars.map((c) => [c, (c.name || "").toLowerCase()
    .replace(/\b(latin|greek|cyrillic|small|capital|letter|with|and)\b/g, "").replace(/\s+/g, "")]));
  const baseLetter = (char) => char.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
  /** @typedef {{ kind: 'none' } | { kind: 'selected', cell: HTMLElement }} GridSelection */
  /** @type {GridSelection} */
  let selection = { kind: "none" };
  const select = (cell) => {
    const item = items.get(cell);
    switch (selection.kind) {
      case "selected":
        selection.cell.setAttribute("aria-selected", "false");
        selection.cell.tabIndex = -1;
        break;
      case "none":
        break;
    }
    selection = { kind: "selected", cell };
    cell.setAttribute("aria-selected", "true");
    cell.tabIndex = 0;
    big.textContent = item.char;
    $("#big-name").textContent = item.name || "";
    $("#big-code").textContent = `U+${item.codepoint.toString(16).toUpperCase().padStart(4, "0")}`;
    placeBigGuides();
  };
  const draw = () => {
    const q = $("#g-search").value.trim();
    const lower = q.toLowerCase();
    const compact = lower.replace(/\s+/g, "");
    const hex = lower.replace(/^u\+/, "").replace(/^0+/, "");
    // One letter finds its accented forms in both cases; longer queries search names and code points.
    const matches = (c) => {
      if (!q) return true;
      if ([...q].length === 1) return c.char === q || baseLetter(c.char) === baseLetter(q);
      return (c.name || "").toLowerCase().includes(lower) || glyphKeys.get(c).includes(compact) ||
        (/^[0-9a-f]{2,6}$/.test(hex) && c.codepoint.toString(16) === hex);
    };
    const frag = document.createDocumentFragment();
    for (const group of groups) {
      const members = chars.filter((c) => c.group === group.id && matches(c));
      if (!members.length) continue;
      const box = el("div", { class: "grid-group", role: "group", "aria-label": group.label },
        [el("div", { class: "mono grid-label", "aria-hidden": "true", text: group.label })]);
      for (const item of members) {
        const cell = el("button", { type: "button", class: "cell", role: "option", "aria-selected": "false",
                                    tabindex: "-1", "aria-label": item.name || item.char, text: item.char });
        items.set(cell, item);
        box.append(cell);
      }
      frag.append(box);
    }
    grid.replaceChildren(frag);
    selection = { kind: "none" };
    const first = $(".cell", grid);
    if (first) select(first);
    else grid.append(el("p", { class: "mono grid-empty", text: "No characters match." }));
    refreshOpticalSizes(grid);
  };
  grid.addEventListener("click", (event) => {
    const cell = event.target instanceof Element && event.target.closest(".cell");
    if (cell) select(cell);
  });
  grid.addEventListener("keydown", (event) => {
    const cells = $$(".cell", grid);
    const index = cells.indexOf(document.activeElement);
    if (index < 0) return;
    const columns = getComputedStyle(grid).gridTemplateColumns.split(" ").length;
    const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -columns, ArrowDown: columns,
      Home: -Infinity, End: Infinity };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const target = cells[clamp(index + moves[event.key], 0, cells.length - 1)];
    select(target);
    target.focus();
  });
  $("#g-search").addEventListener("input", draw);
  draw();
})();

makeEditable();

/* Collapsed blocks are laid out only once opened, so their sizes and guides are measured then. */
for (const fold of $$("details.fold")) {
  fold.addEventListener("toggle", () => {
    if (!fold.open) return;
    refreshOpticalSizes(fold);
    refreshGuides();
  });
}

/* Recompute responsive and inherited sizes without depending on device-pixel density. */
/** @type {'idle' | 'scheduled'} */
let opticalRefresh = "idle";
const scheduleOpticalRefresh = () => {
  switch (opticalRefresh) {
    case "scheduled": return;
    case "idle":
      opticalRefresh = "scheduled";
      requestAnimationFrame(() => {
        opticalRefresh = "idle";
        refreshOpticalSizes();
        refreshGuides();
      });
      break;
  }
};
refreshOpticalSizes();
window.addEventListener("resize", scheduleOpticalRefresh);
// Only width changes can resize text; a taller page (an opened block) needs no refresh.
const observedWidths = new WeakMap();
const opticalResizeObserver = new ResizeObserver((entries) => {
  for (const entry of entries) {
    if (observedWidths.get(entry.target) === entry.contentRect.width) continue;
    observedWidths.set(entry.target, entry.contentRect.width);
    scheduleOpticalRefresh();
  }
});
opticalResizeObserver.observe(document.documentElement);
opticalResizeObserver.observe(document.body);
new MutationObserver(scheduleOpticalRefresh).observe(document.documentElement,
  { attributes: true, attributeFilter: ["style", "class"] });
document.fonts.ready.then(scheduleOpticalRefresh);

/* Build metadata */
(async () => {
  /** @typedef {{ status: 'unloaded' } | { status: 'loaded', key: string }} BuildMetaState */
  /** @type {BuildMetaState} */
  let buildState = { status: "unloaded" };
  const checkMeta = async () => {
    try {
      const response = await fetch("data/meta.json", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const m = await response.json();
      const buildKey = `${m.version || ""}:${m.build || m.commit || ""}`;
      switch (buildState.status) {
        case "unloaded":
          buildState = { status: "loaded", key: buildKey };
          break;
        case "loaded":
          if (buildKey !== buildState.key) {
            location.reload();
            return;
          }
          break;
      }
      if (typeof m.version === "string") { $("#version").textContent = m.version; $("#foot-version").textContent = m.version; }
      if (Number.isFinite(m.glyphs)) $("#glyphs").textContent = m.glyphs.toLocaleString("en-US");
      if (m.metrics && typeof m.metrics === "object") {
        for (const [style, known] of Object.entries(metrics)) {
          const measured = m.metrics[style];
          if (!measured || typeof measured !== "object") continue;
          for (const key of Object.keys(known)) if (Number.isFinite(measured[key])) known[key] = measured[key];
        }
        refreshGuides();
      }
      if (typeof m.commit === "string" && /^[0-9a-f]{7,40}$/.test(m.commit)) {
        $("#build").textContent = `Build ${m.commit.slice(0, 7)}${m.date ? `, ${m.date}` : ""}.`;
      }
    } catch { /* metadata is optional */ }
  };
  await checkMeta();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkMeta(); });
  if (location.hostname === "127.0.0.1" || location.hostname === "localhost") {
    setInterval(checkMeta, 3000);
  }
})();
