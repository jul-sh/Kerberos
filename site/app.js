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
function tween(from, to, onFrame, duration = 560) {
  if (reducedMotion.matches || from === to) {
    onFrame(to, true);
    return () => {};
  }
  const start = performance.now();
  let frame = requestAnimationFrame(function step(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
    onFrame(from + (to - from) * eased, t === 1);
    if (t < 1) frame = requestAnimationFrame(step);
  });
  return () => cancelAnimationFrame(frame);
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
  const toggle = $(".toggle", panel);
  let value = spec.initial;
  /** @type {Style} */
  let style = "roman";
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
    cancel = tween(spec.toSlider(value), to, (position, done) =>
      render(done ? target : spec.fromSlider(position), done ? to : position));
  };
  const stops = spec.stops.map(([stopValue, label, rank]) => {
    const p = (spec.toSlider(stopValue) - min) / (max - min);
    const button = el("button", { type: "button", class: "mono stop", text: label, "aria-label": `${name}: ${label}` });
    button.style.setProperty("--p", String(p));
    if (p <= 0) button.classList.add("is-start");
    if (p >= 1) button.classList.add("is-end");
    if (rank === "minor") button.dataset.minor = "";
    button.addEventListener("click", () => glideTo(stopValue));
    $(".stops", panel).append(button);
    return { value: stopValue, button };
  });
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
  if (spec.guides) guideRefreshers.add(placeAxisGuides);
  render(value);
}

/* Every specimen text is editable; controls are not, so editing can't remove them.
   Paste and drop insert plain text only; single-line samples stay on one line. */
const EDITABLE = ["#title", ".axis-sample", ".styles span", "#big", "#essay"];
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
for (const feature of FEATURES) {
  const sample = el("span", { class: "feature-sample" });
  let apply = () => {};
  switch (feature.sample.kind) {
    case "whole": {
      const { lines, on, off } = feature.sample;
      sample.append(...lines.map((line) => el("span", { class: "line", text: line })));
      apply = (state) => Object.assign(sample.style, RESET, state ? on : off);
      break;
    }
    case "parts": {
      const targets = feature.sample.parts.map((part) => {
        if (typeof part === "string") {
          sample.append(part);
          return null;
        }
        const span = el("span", { text: part.text });
        sample.append(span);
        return [span, part.on];
      }).filter(Boolean);
      apply = (state) => { for (const [span, on] of targets) Object.assign(span.style, RESET, state ? on : {}); };
      break;
    }
  }
  const state = el("span", { class: "state", "aria-hidden": "true" });
  const tile = el("button", { type: "button", class: "feature", "aria-pressed": "true", title: feature.tag }, [
    el("span", { class: "mono feature-head" }, [el("span", { text: feature.name }), state]),
    sample,
  ]);
  const set = (on) => {
    tile.setAttribute("aria-pressed", String(on));
    state.textContent = on ? "On" : "Off";
    apply(on);
  };
  tile.addEventListener("click", () => set(tile.getAttribute("aria-pressed") !== "true"));
  set(true);
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
  let selected = null;
  const select = (cell, focus = false) => {
    const item = items.get(cell);
    if (selected) {
      selected.setAttribute("aria-selected", "false");
      selected.tabIndex = -1;
    }
    selected = cell;
    cell.setAttribute("aria-selected", "true");
    cell.tabIndex = 0;
    if (focus) cell.focus();
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
    selected = null;
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
    select(cells[clamp(index + moves[event.key], 0, cells.length - 1)], true);
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
  let currentBuild = null;
  const checkMeta = async () => {
    try {
      const response = await fetch("data/meta.json", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const m = await response.json();
      const buildKey = `${m.version || ""}:${m.build || m.commit || ""}`;
      if (currentBuild !== null && buildKey !== currentBuild) {
        location.reload();
        return;
      }
      currentBuild = buildKey;
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
