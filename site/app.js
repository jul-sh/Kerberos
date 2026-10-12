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
const setSample = (name) => tester.replaceChildren(...SAMPLES[name].map((t) => el("p", { text: t })));
$("#t-sample").addEventListener("change", (e) => setSample(e.target.value));
$("#t-style").addEventListener("change", (e) => tester.style.setProperty("--t-style", e.target.value));
/* Weight, grade and optical size go through font-variation-settings; optical size
   "auto" follows the text size (1 CSS px = 1 opsz unit, clamped to the 8-72 range). */
const variation = { wght: 400, GRAD: 0, opsz: null, size: 48 };
const applyVariation = () => {
  const opsz = variation.opsz ?? Math.min(72, Math.max(8, variation.size));
  tester.style.fontVariationSettings = `"wght" ${variation.wght}, "GRAD" ${variation.GRAD}, "opsz" ${opsz}`;
  $("#t-opsz-out").textContent = variation.opsz === null ? `auto ${opsz}` : String(opsz);
};
bindRange("#t-weight", (w) => { variation.wght = w; applyVariation(); });
bindRange("#t-size", (s) => { tester.style.setProperty("--t-size", `${s}px`); variation.size = s; applyVariation(); });
bindRange("#t-opsz", (o) => { variation.opsz = o < 8 ? null : o; applyVariation(); }, () => "");
bindRange("#t-grad", (g) => { variation.GRAD = g; applyVariation(); });
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
    el("span", { class: "wf-text", style: { fontVariationSettings: `"wght" ${w}` } },
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
bindRange("#r-size", (s) => essay.style.setProperty("--r-size", `${s}px`));
bindRange("#r-weight", (w) => essay.style.setProperty("--r-wght", w));

makeEditable();

/* Build metadata */
(async () => {
  try {
    const response = await fetch("data/meta.json", { credentials: "same-origin" });
    if (!response.ok) return;
    const m = await response.json();
    if (typeof m.version === "string") { $("#version").textContent = m.version; $("#foot-version").textContent = m.version; }
    if (Number.isFinite(m.glyphs)) $("#glyphs").textContent = String(m.glyphs);
    if (typeof m.commit === "string" && /^[0-9a-f]{7,40}$/.test(m.commit)) {
      $("#build").textContent = `Build ${m.commit.slice(0, 7)}${m.date ? `, ${m.date}` : ""}.`;
    }
  } catch { /* metadata is optional */ }
})();
