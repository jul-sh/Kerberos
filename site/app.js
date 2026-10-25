// Kerberos specimen. DOM is built with createElement/textContent only.
// Nothing animates on its own: every change follows a user input.
"use strict";

const $ = (sel) => document.querySelector(sel);

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
  const update = () => { if (out) out.textContent = format(input.value); onChange(Number(input.value)); };
  input.addEventListener("input", update);
  update();
}

/* OpenType optical sizes are points: 96 CSS pixels = 72 points. */
const opticalSizeForPixels = (pixels) => Math.min(72, Math.max(6, pixels * 72 / 96));
const formatOpticalSize = (points) => String(Number(points.toFixed(2)));

function refreshOpticalSizes(root = document.body) {
  // Read first, then write, so a large character grid does not force a layout per cell.
  const sizes = [root, ...root.querySelectorAll("*")].map((node) => [node,
    String(opticalSizeForPixels(parseFloat(getComputedStyle(node).fontSize)))]);
  for (const [node, points] of sizes) {
    if (node.style.getPropertyValue("--auto-opsz") !== points) node.style.setProperty("--auto-opsz", points);
  }
}

/* Title weight */
bindRange("#title-weight", (w) => $("#title").style.setProperty("--w", w));

/* Type tester */
const SAMPLES = {
  essay: [
    "Kerberos is a text serif in the Kis–Janson line: Dutch-baroque book type, compact and sturdy, first cut in Amsterdam around 1685.",
  ],
  pangram: [
    "Sphinx of black quartz, judge my vow.",
    "The quick brown fox jumps over the lazy dog.",
    "Waltz, bad nymph, for quick jigs vex.",
  ],
  figures: ["0123456789 1,234.50 ½ ¼ 18%", "§ 12 ¶ 4 No. 7 · 26.10.2026 · €49 £35 $28"],
  languages: [
    "Ærøskøbing, Świętokrzyskie, Đakovo, Ísafjörður, Şanlıurfa.",
    "Ελληνικά · Кириллица · Châteauneuf-du-Pape",
  ],
};
const tester = $("#tester");
const setSample = (name) => {
  tester.replaceChildren(...SAMPLES[name].map((t) => el("p", { text: t })));
  refreshOpticalSizes(tester);
};
$("#t-sample").addEventListener("change", (e) => setSample(e.target.value));
$("#t-style").addEventListener("change", (e) => tester.style.setProperty("--t-style", e.target.value));
/** @typedef {{ mode: 'auto' } | { mode: 'manual', value: number }} OpticalSize */
/** @type {{ wght: number, GRAD: number, YTLC: number, optical: OpticalSize }} */
const variation = { wght: 400, GRAD: 0, YTLC: 500, optical: { mode: "auto" } };
const applyOpticalMode = () => {
  const slider = $("#t-opsz");
  switch (variation.optical.mode) {
    case "auto": {
      tester.style.removeProperty("--manual-opsz");
      const points = opticalSizeForPixels(parseFloat(getComputedStyle(tester).fontSize));
      slider.value = String(points);
      slider.disabled = true;
      $("#t-opsz-out").textContent = `auto ${formatOpticalSize(points)} pt`;
      break;
    }
    case "manual":
      tester.style.setProperty("--manual-opsz", variation.optical.value);
      slider.disabled = false;
      slider.value = String(variation.optical.value);
      $("#t-opsz-out").textContent = `${formatOpticalSize(variation.optical.value)} pt`;
      break;
  }
};
const applyVariation = () => {
  tester.style.fontWeight = variation.wght;
  tester.style.setProperty("--grade", variation.GRAD);
  tester.style.setProperty("--ytlc", variation.YTLC);
  applyOpticalMode();
  refreshOpticalSizes(tester);
};
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
bindRange("#t-grad", (g) => { variation.GRAD = g; applyVariation(); });
bindRange("#t-ytlc", (y) => { variation.YTLC = y; applyVariation(); });
bindRange("#t-leading", (l) => tester.style.setProperty("--t-leading", l), (v) => Number(v).toFixed(2));
/* Every block of text is editable; controls are not, so editing can't remove them.
   Paste and drop insert plain text only. */
const EDITABLE = [".title", ".facts p:first-child", "#tester", ".note", ".label", ".wf-text", ".big",
  "#features tbody td.sample", "#essay", ".lineage dt", ".lineage dd", ".foot p:last-child"];
const makeEditable = () => {
  for (const node of document.querySelectorAll(EDITABLE.join(","))) {
    node.contentEditable = "true";
    node.spellcheck = false;
  }
};
const page = document.querySelector("main");
const inEditable = (event) => event.target instanceof Element && event.target.closest('[contenteditable="true"]');
page.addEventListener("paste", (event) => {
  if (!inEditable(event)) return;
  event.preventDefault();
  const plain = (event.clipboardData || window.clipboardData).getData("text/plain").slice(0, 5000);
  document.execCommand("insertText", false, plain);
});
page.addEventListener("drop", (event) => { if (inEditable(event)) event.preventDefault(); });
setSample("essay");

/* Weights */
const NAMES = { 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold" };
for (const w of [300, 400, 500, 600, 700]) {
  $("#waterfall").append(el("div", { class: "wf" }, [
    el("span", { class: "mono", text: `${w} ${NAMES[w]}` }),
    el("span", { class: "wf-text", style: { fontWeight: w } },
      [document.createTextNode("Handgloves 1879 "), el("i", { text: "and italic" })]),
  ]));
}

/* Characters */
(async () => {
  const grid = $("#grid");
  let data;
  try {
    const response = await fetch("data/characters.json", { credentials: "same-origin" });
    if (!response.ok) throw new Error(String(response.status));
    data = await response.json();
  } catch {
    grid.replaceChildren(el("p", { class: "mono", text: "Character list unavailable." }));
    return;
  }
  const chars = Array.isArray(data.characters) ? data.characters : [];
  $("#glyph-count").textContent = `(${chars.length})`;
  for (const g of data.groups) $("#g-group").append(el("option", { value: g.id, text: g.label }));

  const glyphTargets = [grid, $("#big")];
  bindRange("#g-weight", (w) => glyphTargets.forEach((n) => n.style.setProperty("--g-wght", w)));
  $("#g-style").addEventListener("change", (e) => glyphTargets.forEach((n) => n.style.setProperty("--g-style", e.target.value)));

  const show = (item, cell) => {
    for (const c of grid.querySelectorAll("[aria-selected='true']")) c.setAttribute("aria-selected", "false");
    if (cell) cell.setAttribute("aria-selected", "true");
    $("#big").textContent = item.char;
    $("#big-name").textContent = item.name || "";
    $("#big-code").textContent = `U+${item.codepoint.toString(16).toUpperCase().padStart(4, "0")}`;
  };
  const draw = () => {
    const q = $("#g-search").value.trim();
    const lower = q.toLowerCase();
    const hex = lower.replace(/^u\+/, "").replace(/^0+/, "");
    const group = $("#g-group").value;
    const items = chars.filter((c) => (group === "all" || c.group === group) && (!q || c.char === q ||
      (c.name || "").toLowerCase().includes(lower) || (/^[0-9a-f]{2,6}$/.test(hex) && c.codepoint.toString(16) === hex)));
    const frag = document.createDocumentFragment();
    for (const item of items) {
      const cell = el("button", { type: "button", class: "cell", role: "option", "aria-selected": "false",
                                  "aria-label": item.name || item.char, text: item.char });
      cell.addEventListener("click", () => show(item, cell));
      frag.append(cell);
    }
    grid.replaceChildren(frag);
    if (items.length) show(items[0], grid.firstElementChild);
    refreshOpticalSizes(grid);
  };
  $("#g-search").addEventListener("input", draw);
  $("#g-group").addEventListener("change", draw);
  draw();
})();

/* Features: one sample per feature with an on/off toggle */
const FEATURES = [
  ["Ligatures", "liga", "fifty baffled fjord The", { fontVariantLigatures: "no-common-ligatures", fontFeatureSettings: "\"liga\" 0, \"clig\" 0" }, {}],
  ["Small caps", "smcp", "Kerberos in Small Caps", {}, { fontVariantCaps: "small-caps" }],
  ["Lining figures", "lnum", "Since 1879: 2,048 pages", {}, { fontVariantNumeric: "lining-nums" }],
  ["Tabular figures", "tnum", "1111 7070 4141", {}, { fontVariantNumeric: "tabular-nums" }],
  ["Fractions", "frac", "1/2 3/4 7/8", {}, { fontVariantNumeric: "diagonal-fractions" }],
  ["Roman numerals", "ss01", "MMXXVI XIV", {}, { fontFeatureSettings: '"ss01" 1' }],
];
const RESET = { fontVariantLigatures: "", fontVariantCaps: "", fontVariantNumeric: "", fontFeatureSettings: "" };
const tbody = $("#features tbody");
for (const [name, tag, sample, off, on] of FEATURES) {
  const cell = el("td", { text: sample, class: "sample" });
  const toggle = el("button", { type: "button", class: "toggle mono", "aria-pressed": "true", "aria-label": `${name} (${tag})` });
  const apply = (state) => {
    Object.assign(cell.style, RESET, state ? on : off);
    toggle.setAttribute("aria-pressed", String(state));
    toggle.textContent = state ? "On" : "Off";
  };
  toggle.addEventListener("click", () => apply(toggle.getAttribute("aria-pressed") !== "true"));
  apply(true);
  tbody.append(el("tr", {}, [
    el("td", {}, [document.createTextNode(name), el("span", { text: tag })]),
    cell,
    el("td", { class: "switch" }, [toggle]),
  ]));
}

/* Reading */
const essay = $("#essay");
bindRange("#r-size", (s) => {
  essay.style.setProperty("--r-size", `${s}px`);
  refreshOpticalSizes(essay);
});
bindRange("#r-weight", (w) => essay.style.setProperty("--r-wght", w));

makeEditable();

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
        applyOpticalMode();
      });
      break;
  }
};
refreshOpticalSizes();
window.addEventListener("resize", scheduleOpticalRefresh);
const opticalResizeObserver = new ResizeObserver(scheduleOpticalRefresh);
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
      if (Number.isFinite(m.glyphs)) $("#glyphs").textContent = String(m.glyphs);
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
