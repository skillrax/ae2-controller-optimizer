import { Viewer } from "./viewer.js";
import { buildItems, describe, DYES, DYE_NAMES, DIRS, OPPOSITE, key, add, hex } from "./scene.js";

// --------------------------------------------------------------------------- helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtTime = s => {
  s = Math.max(0, Math.round(s || 0));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}m`;
};
const fmtNum = n => n == null ? "–" : Number(n).toLocaleString();
const store = {
  get(k, d) { try { const v = localStorage.getItem("ae2opt:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ae2opt:" + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

async function api(path, body, { raw = false } = {}) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = (await res.json()).error || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return raw ? res : res.json();
}

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 7000 : 3500);
}

function busy(text) {
  $("#busyText").textContent = text || "Working…";
  $("#busy").hidden = !text;
}

function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

const slug = s => (s || "build").trim().replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 40) || "build";

// --------------------------------------------------------------------------- app state
const S = {
  server: null,
  profiles: [],
  profile: null,
  blocks: new Map(),
  analysis: null,
  checkSeq: 0,
  tool: "select",
  colour: store.get("colour", "transparent"),
  undo: [], redo: [],
  selected: null,
  marks: null,
  view: { xray: false, heat: false, slice: null },
};

// --------------------------------------------------------------------------- profiles
function profileSummary(p) {
  return `${p.normal_channel_capacity} · ${p.dense_channel_capacity} · ${p.max_controller_size.join("×")}`;
}

function setProfiles(list) {
  S.profiles = list;
  const want = S.profile?.name || store.get("profile", S.server.default_profile);
  S.profile = list.find(p => p.name === want) || list[0];
  const sel = $("#profileSelect");
  sel.innerHTML = list.map(p => `<option>${esc(p.name)}</option>`).join("");
  sel.value = S.profile.name;
  $("#profileSummary").textContent = profileSummary(S.profile);
}

function useProfile(name) {
  S.profile = S.profiles.find(p => p.name === name) || S.profile;
  store.set("profile", S.profile.name);
  $("#profileSelect").value = S.profile.name;
  $("#profileSummary").textContent = profileSummary(S.profile);
  scheduleCheck();
  Opt.syncLimits();
  Prof.renderList();
}

// --------------------------------------------------------------------------- design: layout model
const blockFrom = d => ({ ...d, pos: [...d.pos], parts: d.parts ? JSON.parse(JSON.stringify(d.parts)) : undefined });

function layoutDict() {
  const blocks = [...S.blocks.values()].sort((a, b) => a.pos[0] - b.pos[0] || a.pos[1] - b.pos[1] || a.pos[2] - b.pos[2])
    .map(b => {
      const d = { pos: b.pos, type: b.type };
      if (b.type === "controller" && b.powered) d.powered = true;
      if (b.type === "marker") d.block = b.block;
      if (b.type === "cable") {
        d.cable = b.cable || "normal";
        if (b.color && b.color !== "transparent") d.color = b.color;
        if (b.parts && Object.keys(b.parts).length) d.parts = b.parts;
      }
      return d;
    });
  return { schema: 1, blocks };
}

function setLayout(dict, { title, refit = true, keepUndo = false } = {}) {
  S.blocks = new Map((dict.blocks || []).map(b => [key(b.pos), blockFrom(b)]));
  if (!keepUndo) { S.undo = []; S.redo = []; }
  if (title != null) $("#buildTitle").value = title;
  S.selected = null;
  S.marks = null;
  S.analysis = null;
  Design.updateSlicer(true);
  Design.render({ refit });
  scheduleCheck(0);
  saveDraft();
}

function snapshot() { return JSON.stringify(layoutDict()); }

function commit(fn) {
  const before = snapshot();
  const ok = fn();
  if (ok === false) return;
  if (snapshot() === before) return;
  S.undo.push(before);
  if (S.undo.length > 200) S.undo.shift();
  S.redo = [];
  afterEdit();
}

function afterEdit() {
  Design.updateSlicer();
  Design.render();
  scheduleCheck();
  saveDraft();
}

function undo() {
  if (!S.undo.length) return;
  S.redo.push(snapshot());
  const d = JSON.parse(S.undo.pop());
  S.blocks = new Map(d.blocks.map(b => [key(b.pos), blockFrom(b)]));
  afterEdit();
}
function redo() {
  if (!S.redo.length) return;
  S.undo.push(snapshot());
  const d = JSON.parse(S.redo.pop());
  S.blocks = new Map(d.blocks.map(b => [key(b.pos), blockFrom(b)]));
  afterEdit();
}

let draftTimer = 0;
function saveDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => store.set("draft", { title: $("#buildTitle").value, layout: layoutDict() }), 400);
}

function nextFreq() {
  let m = 0;
  for (const b of S.blocks.values()) for (const p of Object.values(b.parts || {})) if (p.type === "p2p_me") m = Math.max(m, p.freq);
  return m + 1;
}

// --------------------------------------------------------------------------- checking
let checkTimer = 0;
function scheduleCheck(delay = 120) {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, delay);
}

async function runCheck() {
  const seq = ++S.checkSeq;
  if (!S.blocks.size) { S.analysis = null; Design.render(); Inspector.render(); return; }
  try {
    const res = await api("/api/check", { layout: layoutDict(), profile: S.profile });
    if (seq !== S.checkSeq) return;
    S.analysis = res;
  } catch (err) {
    if (seq !== S.checkSeq) return;
    S.analysis = null;
    toast(`Check failed: ${err.message}`, "bad");
  }
  Design.render();
  Inspector.render();
}

// --------------------------------------------------------------------------- shipped library
// Same matching as ae2opt/library.py rules_match: a result only counts for the exact rules,
// whether it's shipped or one of the user's runs. Results with equal keys are comparable.
const boxId = box => [...box].sort((a, b) => a - b).join("x");
const rulesKey = r => [r.normal, r.dense, r.model, r.exterior, r.interior, r.anchor_free, r.margin ?? 1].join("|");
const comparable = r => !r.custom && r.channels !== false && (r.margin ?? 1) === 1;
const wantedRules = opts => ({ normal: S.profile.normal_channel_capacity, dense: S.profile.dense_channel_capacity,
  model: S.server.model_version, exterior: opts.exterior, interior: opts.interior, anchor_free: opts.anchor_free, margin: 1 });

// Every box + rules combination that has a result: the shipped entry (if any), the user's
// runs, and what's known overall (most tunnels, tightest proven bound). See RunManager.known.
function resultGroups() {
  const groups = new Map();
  const group = (box, rules) => {
    const k = `${boxId(box)}|${rulesKey(rules)}`;
    if (!groups.has(k)) groups.set(k, { key: k, box: boxId(box).split("x").map(Number), rules, entry: null, runs: [] });
    return groups.get(k);
  };
  for (const e of S.server.library || []) group(e.box, e.rules).entry = e;
  for (const r of S.server.runs || []) if (r.rules && comparable(r.rules) && (r.tunnels != null || r.upper != null)) group(r.box, r.rules).runs.push(r);
  for (const g of groups.values()) Object.assign(g, groupStats(g));
  return groups;
}

// What a group knows, optionally leaving one run out ("what was known before this run").
function groupStats(g, exclude = null) {
  let tunnels = g.entry?.tunnels ?? null, upper = g.entry?.upper ?? null, run = null;
  for (const r of [...g.runs].sort((a, b) => a.modified - b.modified)) {   // ties go to the earlier result
    if (r.id === exclude) continue;
    if (r.upper != null) upper = upper == null ? r.upper : Math.min(upper, r.upper);
    if (r.tunnels != null && r.has_best && (tunnels == null || r.tunnels > tunnels)) { tunnels = r.tunnels; run = r; }
  }
  return { tunnels, upper, run, proven: tunnels != null && tunnels === upper,
    controllers: run ? run.controllers : g.entry?.controllers, trunks: run ? null : g.entry?.trunks };
}

function knownBest(box, opts) {
  if (!S.profile.channels_enabled) return null;
  return resultGroups().get(`${boxId(box)}|${rulesKey(wantedRules(opts))}`) || null;
}

// How a run stands against what was known before it.
function runStanding(r) {
  if (!r.rules || !comparable(r.rules) || r.tunnels == null) return null;
  const g = resultGroups().get(`${boxId(r.box)}|${rulesKey(r.rules)}`);
  if (!g) return null;
  const prev = groupStats(g, r.id), proved = r.upper != null && r.tunnels >= r.upper;
  const bound = prev.upper != null && r.upper != null && r.upper < prev.upper ? ` Proven maximum tightened: ${r.upper} (was ${prev.upper}).` : "";
  if (proved) return prev.proven
    ? { kind: "same", text: `Matches the known proof: ${prev.tunnels} tunnels is optimal.` }
    : { kind: "proof", text: `New proof: ${r.tunnels} tunnels is optimal for this box${prev.tunnels != null ? ` (best known was ${prev.tunnels}, proven maximum ${prev.upper})` : ""}.` };
  if (prev.tunnels == null) return { kind: "first", text: `First result for this box with these settings.${bound}` };
  const d = r.tunnels - prev.tunnels;
  if (d > 0) return { kind: "record", text: `New best known: ${d} more than the previous best (${prev.tunnels}).${bound}` };
  return { kind: d ? "below" : "tie", text: `${d ? `${-d} below` : "Ties"} the best known (${prev.tunnels}${prev.proven ? ", proven optimal" : ""}).${bound}` };
}

const libSrc = (e, box) => `library:${e.id}${box ? "@" + box.join("x") : ""}`;
const libTitle = (e, box) => `${(box || e.box).join("×")} · ${e.status === "proven" ? "proven optimal" : "best known"} (${e.tunnels} tunnels)`;
const pct = (a, b) => a != null && b ? `${(Math.min(1, a / b) * 100).toFixed(1)}%` : "–";
// Opens the build behind a group's best result, turned to `box` when that's given.
function openKnown(g, box) {
  if (g.run) return openRun(g.run.id, `${g.run.box.join("×")} · ${g.proven ? "proven optimal" : "best known"} (${g.tunnels} tunnels, your run)`);
  return openLibrary(g.entry, box);
}

// --------------------------------------------------------------------------- tools
const ICON = {
  select: '<path d="M6 3l12 7-5.2 1.6L10.5 17z" fill="currentColor" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
  controller: '<rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="8" y="8" width="8" height="8" fill="none" stroke="#4de3ff" stroke-width="1.6"/><path d="M12 8v8M8 12h8" stroke="#4de3ff" stroke-width="1"/>',
  cable: '<path d="M2 12h20" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"/><rect x="8.5" y="8.5" width="7" height="7" rx="1.5" fill="currentColor"/>',
  dense: '<path d="M2 12h20" stroke="currentColor" stroke-width="7" stroke-linecap="round"/><path d="M5 12h14" stroke="#0f1218" stroke-width="1.5" stroke-dasharray="2 2"/>',
  tunnel_in: '<rect x="15" y="4" width="4" height="16" rx="1" fill="currentColor"/><path d="M3 12h9m-3-3 3 3-3 3" stroke="#4de3ff" stroke-width="2" fill="none" stroke-linecap="round"/>',
  tunnel_out: '<rect x="5" y="4" width="4" height="16" rx="1" fill="#c99a45"/><path d="M12 12h9m-3-3 3 3-3 3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>',
  anchor: '<path d="M3 12h9" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><rect x="12" y="9.5" width="9" height="5" rx="1" fill="currentColor" opacity=".55"/>',
  energy: '<path d="M13 2 5 14h6l-1 8 8-12h-6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
  port: '<rect x="4" y="4" width="16" height="16" rx="3" fill="none" stroke="#46d39a" stroke-width="1.8" stroke-dasharray="3 2"/><circle cx="12" cy="12" r="3" fill="#46d39a"/>',
  erase: '<path d="M4 16l8-8 6 6-5 5H8z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M10 21h10" stroke="currentColor" stroke-width="1.8"/>',
};
const TOOLS = [
  { id: "select", key: "V", name: "Inspect", help: "Hover for details, click to select" },
  null,
  { id: "controller", key: "1", name: "ME Controller", help: "Click a face to add a controller next to it" },
  { id: "cable", key: "2", name: "Smart cable", help: "Pick a colour; different dyes don't connect, fluix connects to all" },
  { id: "dense", key: "3", name: "Dense smart cable", help: "32 channels; can't hold tunnels" },
  { id: "tunnel_in", key: "4", name: "P2P tunnel (input)", help: "Click a controller face: adds a cable with a tunnel facing it. Or click a cable side." },
  { id: "tunnel_out", key: "5", name: "P2P tunnel (output)", help: "Click a cable side" },
  { id: "anchor", key: "6", name: "Cable anchor", help: "Blocks one side of a cable" },
  null,
  { id: "energy", key: "7", name: "Energy acceptor", help: "Powers the network it touches" },
  { id: "port", key: "8", name: "Tunnel-network connection", help: "Stand-in for your tunnel network, used for checking" },
  null,
  { id: "erase", key: "E", name: "Remove", help: "Click a part or block to remove it (right-click works with any tool)" },
];

function setTool(id) {
  S.tool = id;
  if (Design.ghostItems) Design.ghostItems = [];
  $$(".tool").forEach(b => b.classList.toggle("active", b.dataset.tool === id));
  $("#stage").classList.toggle("select", id === "select");
  $("#palette").hidden = id !== "cable" && id !== "dense" && !(id === "tunnel_in" || id === "tunnel_out");
  Design.renderPalette();
  Design.render();
}

function setColour(c) {
  S.colour = c;
  store.set("colour", c);
  Design.renderPalette();
  $$(".tool .swatch").forEach(s => s.style.background = DYES[c]);
}

// Where a click with the current tool acts. Returns null if it can't act there.
function target(hit, px, py) {
  const tool = S.tool;
  if (!hit) {
    if (!["controller", "cable", "dense", "energy", "port"].includes(tool)) return null;
    const ys = [...S.blocks.values()].map(b => b.pos[1]);
    const y = ys.length ? Math.min(...ys) : 0;
    const g = Design.viewer.groundPick(px, py, y);
    if (!g) return null;
    const pos = [Math.floor(g[0]), y, Math.floor(g[2])];
    return S.blocks.has(key(pos)) ? null : { kind: "place", pos };
  }
  const o = hit.item.owner, b = S.blocks.get(o.key);
  const nd = Object.keys(DIRS).find(d => DIRS[d].every((v, i) => v === hit.normal[i]));
  if (tool === "erase") return o.part ? { kind: "unpart", pos: b.pos, side: o.side } : { kind: "remove", pos: b.pos };
  if (["controller", "cable", "dense", "energy", "port"].includes(tool)) {
    const pos = add(b.pos, hit.normal);
    return S.blocks.has(key(pos)) ? null : { kind: "place", pos };
  }
  if (tool === "tunnel_in" || tool === "tunnel_out" || tool === "anchor") {
    if (b.type === "controller") {
      const pos = add(b.pos, hit.normal), n = S.blocks.get(key(pos));
      if (n && !(n.type === "cable" && n.cable === "normal")) return null;
      return { kind: "part", pos, side: OPPOSITE[nd], newCable: !n };
    }
    if (b.type === "cable" && b.cable !== "dense") return { kind: "part", pos: b.pos, side: o.part ? o.side : nd };
    if (b.type === "cable" && b.cable === "dense" && tool !== "anchor") return { kind: "nope", why: "P2P tunnels can't go on dense cable" };
    if (b.type === "cable") return { kind: "part", pos: b.pos, side: o.part ? o.side : nd };
    return null;
  }
  return null;
}

function apply(t) {
  if (!t) return;
  if (t.kind === "nope") { toast(t.why, "bad"); return; }
  const tool = S.tool;
  commit(() => {
    if (t.kind === "remove") { S.blocks.delete(key(t.pos)); if (S.selected === key(t.pos)) S.selected = null; return; }
    if (t.kind === "unpart") {
      const b = S.blocks.get(key(t.pos));
      delete b.parts[t.side];
      return;
    }
    if (t.kind === "place") {
      const b = { pos: t.pos };
      if (tool === "controller") b.type = "controller";
      else if (tool === "cable") Object.assign(b, { type: "cable", cable: "normal", color: S.colour, parts: {} });
      else if (tool === "dense") Object.assign(b, { type: "cable", cable: "dense", color: S.colour, parts: {} });
      else if (tool === "energy") b.type = "energy_acceptor";
      else if (tool === "port") b.type = "port";
      S.blocks.set(key(t.pos), b);
      return;
    }
    if (t.kind === "part") {
      let b = S.blocks.get(key(t.pos));
      if (!b) { b = { pos: t.pos, type: "cable", cable: "normal", color: S.colour, parts: {} }; S.blocks.set(key(t.pos), b); }
      b.parts ||= {};
      b.parts[t.side] = tool === "anchor" ? { type: "anchor" }
        : { type: "p2p_me", freq: b.parts[t.side]?.freq ?? nextFreq(), output: tool === "tunnel_out" };
    }
  });
}

// Translucent preview of what a click would add.
function ghost(t) {
  if (!t || !["place", "part"].includes(t.kind)) return [];
  const c = t.pos.map(v => v + 0.5);
  const col = S.tool === "controller" ? [0.3, 0.88, 1, 0.3] : S.tool === "port" ? [0.3, 0.9, 0.6, 0.3]
    : S.tool === "energy" ? [1, 0.85, 0.3, 0.3] : [...hex(DYES[S.colour]).slice(0, 3), 0.45];
  if (t.kind === "place") {
    const h = S.tool === "cable" ? 0.15 : S.tool === "dense" ? 0.24 : 0.5;
    return [{ min: c.map(v => v - h), max: c.map(v => v + h), color: col, ghost: true }];
  }
  const v = DIRS[t.side], min = [], max = [];
  const half = S.tool === "anchor" ? 0.07 : 0.36;
  for (let i = 0; i < 3; i++) {
    if (!v[i]) { min.push(c[i] - half); max.push(c[i] + half); }
    else if (v[i] > 0) { min.push(c[i] + (S.tool === "anchor" ? 0.15 : 0.38)); max.push(c[i] + 0.5); }
    else { min.push(c[i] - 0.5); max.push(c[i] - (S.tool === "anchor" ? 0.15 : 0.38)); }
  }
  const pc = S.tool === "tunnel_out" ? [0.9, 0.7, 0.3, 0.55] : S.tool === "anchor" ? [0.8, 0.82, 0.88, 0.55] : [0.3, 0.88, 1, 0.55];
  const out = [{ min, max, color: pc, ghost: true }];
  if (t.newCable) out.push({ min: c.map(x => x - 0.15), max: c.map(x => x + 0.15), color: [...hex(DYES[S.colour]).slice(0, 3), 0.45], ghost: true });
  return out;
}

// --------------------------------------------------------------------------- design view
const Design = {
  viewer: null,
  hoverHit: null,
  ghostItems: [],

  init() {
    this.viewer = new Viewer($("#view"));
    const rail = $("#toolrail");
    for (const t of TOOLS) {
      if (!t) { rail.append(document.createElement("hr")); continue; }
      const b = document.createElement("button");
      b.className = "tool";
      b.dataset.tool = t.id;
      b.setAttribute("aria-label", t.name);
      b.innerHTML = `<svg viewBox="0 0 24 24">${ICON[t.id]}</svg><span class="k">${t.key}</span>` +
        (t.id === "cable" || t.id === "dense" ? `<span class="swatch" style="background:${DYES[S.colour]}"></span>` : "");
      b.onclick = () => setTool(t.id);
      b.onmouseenter = () => {
        const tip = document.createElement("div");
        tip.className = "tool-tip";
        tip.innerHTML = `<b>${t.name} <kbd>${t.key}</kbd></b><span>${t.help}</span>`;
        const r = b.getBoundingClientRect();
        tip.style.left = r.right + 8 + "px"; tip.style.top = r.top + 4 + "px";
        document.body.append(tip);
        b.onmouseleave = () => tip.remove();
      };
      rail.append(b);
    }
    const v = this.viewer;
    v.on("hover", (hit, e, px, py) => this.onHover(hit, e, px, py));
    v.on("click", (hit, e, px, py) => this.onClick(hit, e, px, py));
    $$("#viewButtons button").forEach(b => b.onclick = () => b.dataset.view === "fit" ? v.fit(true) : v.view(b.dataset.view));
    $("#tglXray").onclick = () => this.toggle("xray");
    $("#tglHeat").onclick = () => this.toggle("heat");
    $("#sliceRange").oninput = e => {
      const r = e.target, top = +r.max;
      S.view.slice = +r.value >= top ? null : +r.value;
      $("#sliceVal").textContent = S.view.slice == null ? "all" : `≤ ${+r.value - +r.min}`;
      this.render();
    };
    $("#emptyState").addEventListener("click", e => { const g = e.target.closest("[data-goto]"); if (g) showTab(g.dataset.goto); });
    setTool("select");
    setColour(S.colour);
  },

  toggle(what) {
    S.view[what] = !S.view[what];
    $("#tglXray").classList.toggle("on", S.view.xray);
    $("#tglHeat").classList.toggle("on", S.view.heat);
    $("#heatLegend").hidden = !S.view.heat;
    this.render();
  },

  renderPalette() {
    const p = $("#palette");
    p.innerHTML = `<div class="pl">${S.tool.startsWith("tunnel") ? "Colour of new cables under tunnels" : "Cable colour"} · <b>${DYE_NAMES[S.colour]}</b></div>` +
      Object.keys(DYES).map(c => `<button class="sw-btn ${c === "transparent" ? "fluix" : ""} ${c === S.colour ? "active" : ""}" data-c="${c}" title="${DYE_NAMES[c]}" style="background:${DYES[c]}"></button>`).join("");
    p.onclick = e => { const b = e.target.closest("[data-c]"); if (b) setColour(b.dataset.c); };
  },

  // Layers are the build's own: tunnel-network connections above or below it show with
  // its top or bottom layer (see buildItems) rather than as layers of their own.
  updateSlicer(reset = false) {
    const ys = [...S.blocks.values()].filter(b => b.type !== "port").map(b => b.pos[1]);
    const r = $("#sliceRange");
    $("#slicer").hidden = ys.length < 2 || Math.min(...ys) === Math.max(...ys);
    if (!ys.length) return;
    const lo = Math.min(...ys), hi = Math.max(...ys);
    const wasTop = S.view.slice == null;
    r.min = lo; r.max = hi;
    if (reset || wasTop) { r.value = hi; S.view.slice = null; $("#sliceVal").textContent = "all"; }
  },

  render({ refit = false } = {}) {
    const items = buildItems(S.blocks, { analysis: S.analysis, slice: S.view.slice, sliceTop: +$("#sliceRange").max, xray: S.view.xray, heat: S.view.heat });
    this.viewer.setItems(items.concat(this.ghostItems), { refit });
    $("#emptyState").hidden = S.blocks.size > 0;
    $("#hints").style.opacity = S.blocks.size ? 1 : 0.5;
    const marks = [];
    if (S.marks) for (const p of S.marks) marks.push({ min: p, max: p.map(v => v + 1) });
    if (S.selected && S.blocks.has(S.selected)) {
      const p = S.blocks.get(S.selected).pos;
      this.viewer.setMarks([{ min: p, max: p.map(v => v + 1) }], [0.66, 0.52, 1, 1]);
      if (marks.length) this.viewer.lines.marks.push(...marks.flatMap(m => boxLinesSimple(m)));
    } else this.viewer.setMarks(marks);
  },

  onHover(hit, e, px, py) {
    const tip = $("#tip");
    this.hoverHit = hit;
    const t = S.tool === "select" ? null : target(hit, px, py);
    const g = ghost(t);
    if (g.length || this.ghostItems.length) { this.ghostItems = g; this.render(); }
    if (!hit) { tip.hidden = true; this.viewer.setHover(null); return; }
    const o = hit.item.owner, b = S.blocks.get(o.key);
    if (!b) { tip.hidden = true; return; }
    const cellBox = { min: b.pos, max: b.pos.map(v => v + 1) };
    this.viewer.setHover(o.part ? { ...hit.item, outline: [0.3, 0.9, 1, 1] } : cellBox);
    const info = S.analysis?.cells?.[o.key];
    const origin = Design.origin();
    const d = describe(b, info, o.part ? o.side : null, origin, S.profile.dense_channel_capacity);
    const rows = d.rows.map(([k, v]) => {
      if (v && typeof v === "object" && "used" in v) {
        const f = v.capacity ? v.used / v.capacity : 0;
        const col = f >= 1 ? "var(--bad)" : f > 0.6 ? "var(--warn)" : "var(--ok)";
        return `<tr><td>${k}</td><td><div class="meter"><div class="m"><b style="width:${Math.min(100, f * 100)}%;background:${col}"></b></div><span>${v.used} / ${v.capacity}</span></div></td></tr>`;
      }
      return `<tr><td>${k}</td><td>${esc(v)}</td></tr>`;
    }).join("");
    const action = { erase: "Click to remove", tunnel_in: t ? "Click to add an input tunnel" : "", tunnel_out: t ? "Click to add an output tunnel" : "",
      anchor: t ? "Click to add an anchor" : "" }[S.tool] || "";
    tip.innerHTML = `<div class="t">${d.color ? `<i style="background:${d.color}"></i>` : ""}${esc(d.title)}</div>
      <div class="w">${d.where}</div>${d.badge ? `<span class="badge ${d.badge[1]}">${d.badge[0]}</span>` : ""}
      <table>${rows}</table>${action ? `<div class="act">${action}</div>` : ""}`;
    tip.hidden = false;
    const W = window.innerWidth, H = window.innerHeight, r = tip.getBoundingClientRect();
    let x = e.clientX + 16, y = e.clientY + 16;
    if (x + r.width > W - 8) x = e.clientX - r.width - 16;
    if (y + r.height > H - 8) y = e.clientY - r.height - 16;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  },

  onClick(hit, e, px, py) {
    $("#view").focus();
    if (e.button === 2) {
      if (!hit) return;
      const o = hit.item.owner, b = S.blocks.get(o.key);
      if (!b) return;
      commit(() => { if (o.part) delete b.parts[o.side]; else S.blocks.delete(o.key); });
      if (S.selected === o.key && !o.part) S.selected = null;
      this.ghostItems = [];
      this.onHover(this.viewer.pick(px, py), e, px, py);
      return;
    }
    if (e.button !== 0) return;
    if (S.tool === "select") {
      S.selected = hit ? hit.item.owner.key : null;
      S.marks = null;
      Inspector.render();
      this.render();
      return;
    }
    apply(target(hit, px, py));
    this.ghostItems = [];
    this.onHover(this.viewer.pick(px, py), e, px, py);
  },

  origin() {
    const ps = [...S.blocks.values()].filter(b => b.type !== "port").map(b => b.pos);
    if (!ps.length) return [0, 0, 0];
    return [0, 1, 2].map(i => Math.min(...ps.map(p => p[i])));
  },
};

function boxLinesSimple(b) {
  const a = b.min.map(v => v - 0.02), c = b.max.map(v => v + 0.02), col = [1, 0.35, 0.35, 1];
  const p = (i, j, k) => [i ? c[0] : a[0], j ? c[1] : a[1], k ? c[2] : a[2]];
  const out = [];
  for (const [i, j] of [[0, 0], [1, 0], [0, 1], [1, 1]])
    out.push([p(0, i, j), p(1, i, j), col], [p(i, 0, j), p(i, 1, j), col], [p(i, j, 0), p(i, j, 1), col]);
  return out;
}

// --------------------------------------------------------------------------- inspector
const METRIC_HELP = {
  working_input_tunnels: "Input tunnels that are powered, have a channel and feed a controller face",
};

const Inspector = {
  render() {
    this.status();
    this.selection();
    this.issues();
    this.materials();
  },

  status() {
    const a = S.analysis, el = $("#statusCard");
    if (!S.blocks.size) { el.innerHTML = `<div class="head"><span class="verdict">Empty build</span></div><p class="muted small">Nothing placed yet.</p>`; return; }
    if (!a) { el.innerHTML = `<div class="head"><span class="verdict">Checking…</span></div>`; return; }
    const m = a.metrics, dense = S.profile.dense_channel_capacity;
    const errs = a.violations.filter(v => v.severity === "error").length;
    const warns = a.violations.filter(v => v.severity === "warning").length;
    const cables = [...S.blocks.values()].filter(b => b.type === "cable").length;
    el.innerHTML = `
      <div class="head"><span class="verdict ${a.ok ? "ok" : "bad"}">${a.ok ? "Valid build" : "Invalid build"}</span>
        <span class="muted small">${errs ? `${errs} error${errs > 1 ? "s" : ""}` : ""}${errs && warns ? " · " : ""}${warns ? `${warns} warning${warns > 1 ? "s" : ""}` : ""}</span></div>
      <div class="tiles">
        <div class="tile hero" title="${METRIC_HELP.working_input_tunnels}"><div class="v">${m.working_input_tunnels} <small>tunnels</small></div>
          <div class="l">${fmtNum(m.working_input_tunnels * dense)} channels into the main network</div></div>
        <div class="tile"><div class="v">${m.controller_blocks}</div><div class="l">controllers</div></div>
        <div class="tile"><div class="v">${cables}</div><div class="l">cable cells</div></div>
        <div class="tile" title="Controller faces next to a non-controller block, and how many carry a tunnel"><div class="v">${m.faces_with_tunnel}<small>/${m.exposed_main_faces}</small></div><div class="l">faces with a tunnel</div></div>
        <div class="tile"><div class="v">${m.linked_to_an_output}</div><div class="l">linked to an output</div></div>
      </div>
      ${a.warnings?.length ? `<div class="warnings" style="margin-top:10px">${a.warnings.map(w => `<div>${esc(w)}</div>`).join("")}</div>` : ""}`;
  },

  issues() {
    const a = S.analysis, el = $("#issuesCard");
    el.hidden = !a || !a.violations.length;
    if (el.hidden) return;
    el.innerHTML = `<h3>Issues</h3><ul class="issues">${a.violations.map((v, i) => `
      <li data-i="${i}" title="${v.positions.length ? "Show where" : ""}"><span class="sev ${v.severity}">${v.rule}</span><span>${esc(v.message)}</span></li>`).join("")}</ul>`;
    el.onclick = e => {
      const li = e.target.closest("li");
      if (!li) return;
      const v = a.violations[+li.dataset.i];
      const was = li.classList.contains("on");
      $$("li", el).forEach(x => x.classList.remove("on"));
      S.marks = was || !v.positions.length ? null : v.positions;
      if (S.marks) li.classList.add("on");
      Design.render();
    };
  },

  materials() {
    const el = $("#materialsCard");
    el.hidden = !S.blocks.size;
    if (el.hidden) return;
    const count = {}, inc = (k, n = 1) => count[k] = (count[k] || 0) + n;
    for (const b of S.blocks.values()) {
      if (b.type === "controller") inc("ME Controller");
      else if (b.type === "cable") {
        if (b.cable !== "none") inc(`${b.cable === "dense" ? "dense" : "smart"}|${b.color || "transparent"}`);
        for (const p of Object.values(b.parts || {})) inc(p.type === "p2p_me" ? (p.output ? "P2P tunnel – ME (output)" : "P2P tunnel – ME (input)") : p.type === "anchor" ? "Cable anchor" : "Device part");
      } else if (b.type === "energy_acceptor") inc("Energy acceptor");
      else if (b.type === "port") inc("~Tunnel-network connections");
      else if (b.type === "marker") inc("~Marker blocks");
    }
    const rows = Object.entries(count).sort(([a], [b]) => (a.includes("|") ? 1 : 0) - (b.includes("|") ? 1 : 0) || a.localeCompare(b)).map(([k, n]) => {
      if (k.includes("|")) {
        const [t, c] = k.split("|");
        return `<tr><td><i style="background:${DYES[c]}"></i>${DYE_NAMES[c]} ${t === "dense" ? "dense smart cable" : "smart cable"}</td><td>${n}</td></tr>`;
      }
      return `<tr><td class="${k.startsWith("~") ? "muted" : ""}">${esc(k.replace("~", ""))}</td><td>${n}</td></tr>`;
    }).join("");
    el.innerHTML = `<h3>Materials</h3><table class="mats">${rows}</table>`;
  },

  selection() {
    const el = $("#selectionCard");
    const b = S.selected && S.blocks.get(S.selected);
    el.hidden = !b;
    if (!b) return;
    const info = S.analysis?.cells?.[S.selected];
    const d = describe(b, info, null, Design.origin(), S.profile.dense_channel_capacity);
    let html = `<h3>${esc(d.title)}</h3><div class="muted small" style="font-family:var(--mono)">${d.where}</div>`;
    if (d.badge) html += `<span class="badge ${d.badge[1]}" style="margin-top:6px">${d.badge[0]}</span>`;
    html += `<dl class="kv">${d.rows.filter(([, v]) => typeof v !== "object").map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}`;
    if (info?.capacity != null) html += `<dt>Channels</dt><dd>${info.used} / ${info.capacity}</dd>`;
    html += "</dl>";
    if (b.type === "cable") {
      html += `<div class="field" style="margin:10px 0 4px"><label>Colour</label><div class="sel-swatches">${Object.keys(DYES).map(c =>
        `<button class="sw-btn ${c === "transparent" ? "fluix" : ""} ${c === (b.color || "transparent") ? "active" : ""}" data-col="${c}" title="${DYE_NAMES[c]}" style="background:${DYES[c]}"></button>`).join("")}</div></div>
        <div class="seg" style="margin-bottom:6px"><button data-ct="normal" class="${b.cable === "normal" ? "active" : ""}">Smart</button><button data-ct="dense" class="${b.cable === "dense" ? "active" : ""}">Dense</button></div>`;
      const parts = Object.entries(b.parts || {});
      if (parts.length) html += `<div class="partlist">${parts.map(([s, p]) => `<div><b>${s}</b><span>${p.type === "p2p_me" ? `P2P ${p.output ? "output" : "input"} · f${p.freq}` : p.type}</span><span class="spacer"></span>
        ${p.type === "p2p_me" ? `<button class="btn" data-flip="${s}">${p.output ? "Make input" : "Make output"}</button>` : ""}<button class="btn" data-rm="${s}">✕</button></div>`).join("")}</div>`;
    }
    if (b.type === "controller") html += `<label class="switch" style="margin-top:8px"><input type="checkbox" id="selPowered" ${b.powered ? "checked" : ""}><span></span>Powered directly</label>`;
    html += `<div class="row" style="margin-top:10px"><button class="btn danger" id="selDelete">Remove block</button></div>`;
    el.innerHTML = html;
    el.onclick = e => {
      const t = e.target.closest("button");
      if (!t) return;
      if (t.dataset.col) commit(() => { b.color = t.dataset.col; });
      else if (t.dataset.ct) {
        if (t.dataset.ct === "dense" && Object.values(b.parts || {}).some(p => p.type !== "anchor")) return toast("Remove the tunnels first: they can't sit on dense cable", "bad");
        commit(() => { b.cable = t.dataset.ct; });
      } else if (t.dataset.flip) commit(() => { b.parts[t.dataset.flip].output = !b.parts[t.dataset.flip].output; });
      else if (t.dataset.rm) commit(() => { delete b.parts[t.dataset.rm]; });
      else if (t.id === "selDelete") { commit(() => { S.blocks.delete(S.selected); }); S.selected = null; }
      Inspector.render();
    };
    const pw = $("#selPowered");
    if (pw) pw.onchange = () => commit(() => { b.powered = pw.checked; });
  },
};

// --------------------------------------------------------------------------- design toolbar actions
function initDesignBar() {
  $("#btnNew").onclick = () => {
    if (S.blocks.size && !confirm("Start a new, empty build? (You can undo with Ctrl+Z.)")) return;
    const before = snapshot();
    setLayout({ blocks: [] }, { title: "Untitled build" });
    S.undo = [before];
  };
  $("#btnOpen").onclick = () => $("#openFile").click();
  $("#btnLibOpen").onclick = () => $("#openFile").click();
  $("#openFile").onchange = async e => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    try {
      const d = JSON.parse(await f.text());
      if (!Array.isArray(d.blocks)) throw new Error("not an ae2opt layout (no \"blocks\" list)");
      setLayout(d, { title: f.name.replace(/\.json$/i, "") });
      showTab("design");
      toast(`Opened ${f.name}`, "ok");
    } catch (err) { toast(`Couldn't open ${f.name}: ${err.message}`, "bad"); }
  };
  $("#btnSave").onclick = () => {
    download(new Blob([JSON.stringify(layoutDict(), null, 1)], { type: "application/json" }), slug($("#buildTitle").value) + ".json");
  };
  const menus = [["#btnExport", "#exportMenu"], ["#btnGuide", "#guideMenu"]];
  for (const [b, m] of menus) $(b).onclick = e => {
    e.stopPropagation();
    for (const [, mm] of menus) if (mm !== m) $(mm).hidden = true;
    $(m).hidden = !$(m).hidden;
  };
  document.addEventListener("click", () => menus.forEach(([, m]) => $(m).hidden = true));
  $("#exportMenu").onclick = async e => {
    const btn = e.target.closest("button");
    if (!btn || !S.blocks.size) return;
    const rig = btn.dataset.rig === "1";
    busy(rig ? "Building the test rig…" : "Writing the structure file…");
    try {
      const name = slug($("#buildTitle").value) + (rig ? "_rig" : "");
      const res = await api("/api/export", { layout: layoutDict(), profile: S.profile, rig, name }, { raw: true });
      download(await res.blob(), name + ".nbt");
      toast("Saved. Copy it into saves/<world>/structures/ and load it with a structure block.", "ok");
    } catch (err) { toast(`Export failed: ${err.message}`, "bad"); }
    busy(null);
  };
  $("#guideMenu").onclick = async e => {
    const btn = e.target.closest("button");
    if (!btn || !S.blocks.size) return;
    const tc = btn.dataset.tc === "1";
    const win = window.open("", "_blank");
    busy(tc ? "Designing the tunnel-network controller…" : "Writing the build guide…");
    try {
      const res = await api("/api/guide", { layout: layoutDict(), profile: S.profile, title: $("#buildTitle").value, tunnel_controller: tc, time: 30 }, { raw: true });
      const html = await res.text();
      if (win) { win.document.open(); win.document.write(html); win.document.close(); }
      else download(new Blob([html], { type: "text/html" }), slug($("#buildTitle").value) + "_guide.html");
    } catch (err) { if (win) win.close(); toast(`Guide failed: ${err.message}`, "bad"); }
    busy(null);
  };
  $("#buildTitle").oninput = saveDraft;
}

// --------------------------------------------------------------------------- optimize tab
const BUDGETS = [60, 120, 300, 600, 900, 1800, 2700, 3600, 7200, 10800, 14400, 21600, 28800, 43200, 86400, 0];
const budgetLabel = s => s === 0 ? "until stopped" : s < 3600 ? `${s / 60} min` : `${s / 3600} h`;

const Opt = {
  current: null,
  pollTimer: 0,
  lastBest: null,
  mini: null,
  announced: new Set(),

  init() {
    $("#workers").max = S.server.cpu_count;
    $("#workers").value = store.get("workers", S.server.cpu_count);
    $("#budget").value = store.get("budget", 4);
    $("#mainSolve").value = store.get("mainSolve", 300);
    for (const id of ["budget", "workers", "mainSolve"]) $("#" + id).addEventListener("input", () => { store.set(id, +$("#" + id).value); this.labels(); });
    $$("#modeSeg button").forEach(b => b.onclick = () => this.setMode(b.dataset.mode));
    this.setMode(store.get("mode", "improve"));
    $("#boxLock").onchange = () => this.renderBox();
    for (const id of ["optExterior", "optInterior", "optAnchorFree"]) $("#" + id).addEventListener("change", () => this.renderKnown());
    $("#btnStart").onclick = () => this.start();
    $("#btnPause").onclick = () => this.act("pause");
    $("#btnResume").onclick = () => this.act("resume");
    $("#btnStop").onclick = () => this.act("stop");
    $("#btnContinue").onclick = () => this.act("continue");
    $("#btnOpenBest").onclick = () => this.openBest();
    $("#runsTable tbody").onclick = e => {
      const tr = e.target.closest("tr");
      if (tr) this.select(tr.dataset.id);
    };
    $("#runsDir").textContent = `Run folders are kept in ${S.server.runs_dir}. A run can also be continued from the command line with: python -m ae2opt run --resume <folder>`;
    if (!S.server.optimizer) {
      $("#btnStart").disabled = true;
      $("#startNote").innerHTML = "The optimizer needs OR-Tools. Install it with <code>pip install ortools</code> and restart.";
    }
    this.mini = new Viewer($("#runView"), { interactive: true });
    this.syncLimits();
    this.labels();
    this.renderRuns(S.server.runs);
    const live = S.server.runs.find(r => r.live) || S.server.runs[0];
    if (live) this.select(live.id);
    setInterval(() => this.refreshList(), 5000);
  },

  setMode(m) {
    this.mode = m;
    store.set("mode", m);
    $$("#modeSeg button").forEach(b => b.classList.toggle("active", b.dataset.mode === m));
    $("#modeNote").textContent = m === "prove"
      ? "One open-ended solve until the best build is proven. Practical up to about 4×4×3; bigger boxes may never finish, but the best so far is always saved."
      : "A main solve, then neighbourhood search that keeps improving the build. Best for big boxes like 7×7×7; it doesn't prove optimality.";
    $("#mainSolve").closest("details").style.display = "";
  },

  // The box can't exceed the profile's controller size, nor the optimizer's own limit.
  maxBox() { return S.profile.max_controller_size.map(v => Math.min(v, S.server.max_box_side)); },

  syncLimits() {
    if (!this.box) this.box = store.get("box", null);
    const max = this.maxBox();
    this.box = (this.box || [...max]).map((v, i) => Math.min(v, max[i]));
    const cfg = S.profile.max_controller_size;
    $("#optProfileNote").innerHTML = `Uses <b>${esc(S.profile.name)}</b>: ${S.profile.normal_channel_capacity} channels per smart cable, ${S.profile.dense_channel_capacity} per dense, controllers up to ${cfg.join("×")}.`
      + (cfg.some(v => v > S.server.max_box_side) ? ` The optimizer stops at ${S.server.max_box_side} per side: bigger searches don't finish usefully, so repeat a smaller build instead.` : "");
    this.renderBox();
  },

  renderBox() {
    const max = this.maxBox(), lock = $("#boxLock").checked, el = $("#boxSliders");
    if (lock) { const m = Math.min(...max); const v = Math.min(Math.max(...this.box), m); this.box = [v, v, v].map((x, i) => Math.min(x, max[i])); }
    const axes = lock ? [["Size", 0, Math.min(...max)]] : [["X", 0, max[0]], ["Y", 1, max[1]], ["Z", 2, max[2]]];
    el.innerHTML = axes.map(([n, i, m]) => `<div class="axis"><b>${n === "Size" ? "↔" : n}</b><input type="range" min="1" max="${m}" value="${this.box[i]}" data-i="${i}"><output>${this.box[i]} / ${m}</output></div>`).join("");
    el.oninput = e => {
      const i = +e.target.dataset.i, v = +e.target.value;
      if (lock) this.box = this.box.map((_, j) => Math.min(v, max[j])); else this.box[i] = v;
      e.target.nextElementSibling.textContent = `${v} / ${e.target.max}`;
      store.set("box", this.box);
      this.renderBoxPreview();
    };
    this.renderBoxPreview();
  },

  renderBoxPreview() {
    const [x, y, z] = this.box, s = 30 / Math.max(x, y, z, 1);
    const w = x * s, h = y * s, d = z * s * 0.5;
    const ox = 6, oy = 50;
    const P = (a, b) => `${ox + a},${oy - b}`;
    const front = [P(0, 0), P(w, 0), P(w, h), P(0, h)].join(" ");
    const top = [P(0, h), P(w, h), P(w + d, h + d), P(d, h + d)].join(" ");
    const side = [P(w, 0), P(w + d, d), P(w + d, h + d), P(w, h)].join(" ");
    const vol = x * y * z;
    $("#boxPreview").innerHTML = `<svg viewBox="0 0 64 56"><polygon points="${top}" fill="#4a5262"/><polygon points="${side}" fill="#2d3442"/><polygon points="${front}" fill="#373e4a" stroke="#4de3ff" stroke-width=".8"/></svg>
      <div><b style="color:var(--text)">${x}×${y}×${z}</b> · ${vol} cells<br>${vol <= 48 ? "Small: proofs usually take minutes." : vol <= 64 ? "Medium: a proof can take hours." : vol <= 343 ? "Large: expect a very good build, not a proof." : "Very large: slow to search; a run needs hours to get good."}</div>`;
    this.renderKnown();
  },

  // What's already known for this box and these settings: the shipped library and the
  // user's own runs, whichever has more tunnels (and the tightest proven bound of all).
  renderKnown() {
    const el = $("#knownNote"), g = knownBest(this.box, this.settings());
    this.known = g?.tunnels != null ? g : null;
    const k = this.known;
    $("#btnStart").textContent = k?.proven ? "Run anyway" : "Start optimizing";
    const from = k?.run ? ` Found by your run <span class="mono">${esc(k.run.id)}</span>${k.entry ? ` (the shipped build has ${k.entry.tunnels})` : ""}.` : "";
    const parts = k && [k.controllers != null && `${k.controllers} controllers`, k.trunks != null && `${k.trunks} trunk${k.trunks === 1 ? "" : "s"}`].filter(Boolean).join(", ");
    const html = !k ? "" : k.proven
      ? `<b>Already proven:</b> ${k.tunnels} tunnels is the best possible for this box with these settings${parts ? ` (${parts})` : ""}. Running it again can't find more.${from}
         <div class="row"><button class="btn primary" id="btnOpenKnown">Open it</button></div>`
      : `<b>Best known:</b> ${k.tunnels} tunnels, ${pct(k.tunnels, k.upper)} of the proven maximum (${k.upper})${parts ? `; ${parts}` : ""}.${from}
         <div class="row"><button class="btn" id="btnOpenKnown">Open it</button>
         <label class="check"><input type="checkbox" id="startFromKnown" ${store.get("startKnown", true) ? "checked" : ""}> start the run from it</label></div>`;
    el.hidden = !k;
    if (html === this.knownHtml) return;   // runs refresh every few seconds; keep the note still
    this.knownHtml = html;
    if (!k) return;
    el.className = `known ${k.proven ? "proven" : ""}`;
    el.innerHTML = html;
    $("#btnOpenKnown").onclick = () => openKnown(this.known, this.box);
    const cb = $("#startFromKnown");
    if (cb) cb.onchange = () => store.set("startKnown", cb.checked);
  },

  labels() {
    const b = BUDGETS[+$("#budget").value];
    $("#budgetOut").textContent = budgetLabel(b);
    $("#workersOut").textContent = `${$("#workers").value} of ${S.server.cpu_count}`;
    $("#mainOut").textContent = fmtTime(+$("#mainSolve").value);
  },

  settings() {
    const b = BUDGETS[+$("#budget").value];
    return {
      box: this.box, mode: this.mode, budget_minutes: b ? b / 60 : null,
      workers: +$("#workers").value, main: +$("#mainSolve").value,
      exterior: $("#optExterior").checked, interior: $("#optInterior").checked,
      anchor_free: $("#optAnchorFree").checked, symmetry: $("#optSymmetry").checked,
      normal_priority: $("#optPriority").checked,
      start_known: !!(this.known && !this.known.proven && $("#startFromKnown")?.checked),
    };
  },

  async start() {
    if (!$("#optExterior").checked && !$("#optInterior").checked) return toast("Allow tunnel cells inside or outside the box (or both).", "bad");
    $("#btnStart").disabled = true;
    try {
      const { id } = await api("/api/runs", { profile: S.profile, settings: this.settings() });
      toast(`Run ${id} started`, "ok");
      await this.refreshList();
      this.select(id);
    } catch (err) { toast(`Couldn't start: ${err.message}`, "bad"); }
    $("#btnStart").disabled = !S.server.optimizer;
  },

  async act(action) {
    if (!this.current) return;
    try {
      const r = await api(`/api/runs/${encodeURIComponent(this.current)}/${action}`, { settings: this.settings() });
      this.show(r);
      if (action === "continue") this.poll();
    } catch (err) { toast(err.message, "bad"); }
  },

  async refreshList() {
    try { this.renderRuns(await api("/api/runs")); } catch { /* server gone */ }
  },

  renderRuns(runs) {
    this.runs = S.server.runs = runs;
    $("#liveDot").hidden = !runs.some(r => r.live);
    Lib.renderRuns(runs);
    if (this.box) this.renderKnown();
    const tb = $("#runsTable tbody");
    if (!runs.length) { tb.innerHTML = `<tr><td colspan="7" class="none">No runs yet.</td></tr>`; return; }
    tb.innerHTML = runs.map(r => `<tr data-id="${esc(r.id)}" class="${r.id === this.current ? "sel" : ""}">
      <td class="num">${r.box.join("×")}</td><td>${r.mode === "prove" ? "Prove" : "Improve"}</td>
      <td class="num">${r.tunnels ?? "–"} ${runTag(r)}</td><td class="num">${r.upper ?? "–"}</td><td class="num">${fmtTime(r.active)}</td>
      <td>${statePill(r)}</td><td>${r.has_best ? "▸" : ""}</td></tr>`).join("");
  },

  async select(id) {
    this.current = id;
    this.lastBest = null;
    $$("#runsTable tr").forEach(tr => tr.classList.toggle("sel", tr.dataset.id === id));
    clearTimeout(this.pollTimer);
    await this.poll();
  },

  async poll() {
    clearTimeout(this.pollTimer);
    if (!this.current) return;
    try {
      const r = await api(`/api/runs/${encodeURIComponent(this.current)}`);
      this.show(r);
      if (r.live) this.pollTimer = setTimeout(() => this.poll(), document.hidden ? 5000 : 1000);
      else this.refreshList();
    } catch (err) { toast(err.message, "bad"); }
  },

  show(r) {
    $("#runEmpty").hidden = true;
    $("#runBody").hidden = false;
    const dense = r.dense || S.profile.dense_channel_capacity;
    $("#runTitle").textContent = `${r.box.join("×")} · ${r.mode === "prove" ? "prove the best" : "find a great build"}`;
    $("#runSub").textContent = `${r.profile} · session ${r.sessions} · ${r.id}`;
    $("#runState").outerHTML = statePill(r, "runState");
    const gap = r.tunnels != null && r.upper ? Math.max(0, (r.upper - r.tunnels) / r.upper * 100) : null;
    $("#runStats").innerHTML = [
      ["Best found", r.tunnels ?? "–", "tunnels"],
      ["Channels", r.tunnels != null ? fmtNum(r.tunnels * dense) : "–", ""],
      ["Controllers", r.controllers ?? "–", ""],
      ["Proven max", r.upper ?? "–", "tunnels"],
      ["Active time", fmtTime(r.active), r.budget ? `of ${fmtTime(r.budget)} this session` : ""],
    ].map(([l, v, s]) => `<div class="tile"><div class="v">${v}</div><div class="l">${l}${s ? ` · ${s}` : ""}</div></div>`).join("");
    const frac = r.tunnels != null && r.upper ? Math.min(1, r.tunnels / r.upper) : 0;
    $("#runBar").style.width = `${frac * 100}%`;
    $("#runBarText").textContent = r.tunnels == null ? "searching…" : r.upper ? (gap <= 0 ? "proven optimal" : `${(frac * 100).toFixed(1)}% of proven max · gap ${gap.toFixed(1)}%`) : `${r.tunnels} tunnels`;
    const st = runStanding(r), stEl = $("#runStanding");
    stEl.hidden = !st;
    if (st) { stEl.className = `standing ${st.kind}`; stEl.textContent = st.text; }
    // Say so once when a running search beats what was known.
    if (st && r.live && (st.kind === "record" || st.kind === "proof")) {
      const k = `${r.id}:${st.kind}:${r.tunnels}`;
      if (!this.announced.has(k)) { this.announced.add(k); toast(`${r.box.join("×")}: ${st.text}`, "ok"); }
    }
    $("#runPhase").textContent = r.error ? `Error: ${r.error}` : r.live ? `▸ ${r.phase || ""}` : (r.result || "").split("\n")[0];
    $("#btnPause").hidden = !r.live || r.paused;
    $("#btnResume").hidden = !r.live || !r.paused;
    $("#btnStop").hidden = !r.live;
    $("#btnStop").disabled = r.state === "STOPPING";
    $("#btnContinue").hidden = r.live || r.status === "OPTIMAL";
    $("#btnOpenBest").disabled = !r.has_best && r.tunnels == null;
    drawChart($("#runChart"), r.history || [], r);
    if (r.tunnels != null && r.tunnels !== this.lastBest) {
      this.lastBest = r.tunnels;
      this.loadMini(r.id);
    }
  },

  // The preview shows the best-so-far checkpoint; the server tidies its wiring in the
  // background (fewest trunks), so show the raw one meanwhile and swap when ready.
  async loadMini(id, refit = true) {
    clearTimeout(this.miniTimer);
    try {
      const lay = await api(`/api/layout?src=run:${encodeURIComponent(id)}`);
      if (id !== this.current) return;
      const m = new Map(lay.blocks.map(b => [key(b.pos), b]));
      let analysis = null;
      try { analysis = await api("/api/check", { layout: lay, profile: S.profile }); } catch { /* still show it */ }
      this.mini.setItems(buildItems(m, { analysis }), { refit });
      const exits = lay.blocks.filter(b => b.type === "port").length;
      $("#runViewLabel").textContent = lay.polishing ? "best so far · tidying the wiring…"
        : `best so far · ${exits} tunnel-network connection${exits === 1 ? "" : "s"} · drag to rotate`;
      if (lay.polishing) this.miniTimer = setTimeout(() => this.loadMini(id, false), 2000);
    } catch { $("#runViewLabel").textContent = "no layout saved yet"; }
  },

  async openBest() {
    if (this.current) await openRun(this.current, `Run ${this.current}`);
  },
};

async function openLibrary(e, box) {
  try {
    setLayout(await api(`/api/layout?src=${encodeURIComponent(libSrc(e, box))}`), { title: libTitle(e, box) });
    showTab("design");
  } catch (err) { toast(err.message, "bad"); }
}

// Opens a run's best layout in the designer, waiting for its wiring to be tidied.
async function openRun(id, title) {
  busy("Tidying the wiring (fewest trunks)…");
  try {
    const lay = await api(`/api/layout?src=run:${encodeURIComponent(id)}&wait=1`);
    setLayout(lay, { title });
    showTab("design");
    const t = lay.tidy;
    if (t?.changed) {
      const b = t.before, a = t.after;
      toast(`Wiring tidied: ${b.exits} → ${a.exits} tunnel-network connections, ${b.dense} → ${a.dense} dense cables, ${b.cables} → ${a.cables} cable cells.`, "ok");
    } else if (t?.error) toast(`Couldn't tidy the wiring (${t.error}); showing it as saved.`, "bad");
  } catch (err) { toast(`No saved layout yet: ${err.message}`, "bad"); }
  busy(null);
}

// A run's standing in lists: it proved its box, or it holds the best known result.
function runTag(r) {
  if (r.tunnels == null) return "";
  if (r.upper != null && r.tunnels >= r.upper) return `<span class="tag proven">proven</span>`;
  const g = r.rules && comparable(r.rules) && resultGroups().get(`${boxId(r.box)}|${rulesKey(r.rules)}`);
  return g && g.run?.id === r.id ? `<span class="tag best">best known</span>` : "";
}

function statePill(r, id = "") {
  const st = r.live ? (r.paused ? "PAUSED" : r.state) : (r.status || r.state || "");
  const map = { OPTIMAL: ["Proven optimal", "optimal"], PAUSED: ["Paused", "paused"], RUNNING: ["Running", "running"], STARTING: ["Starting", "running"],
    STOPPING: ["Saving…", "paused"], STOPPED: ["Stopped", ""], FINISHED: ["Finished", ""], FEASIBLE: ["Finished", ""], ERROR: ["Error", "error"] };
  const [label, cls] = map[st] || [st || "–", ""];
  return `<span class="pill ${cls}" ${id ? `id="${id}"` : ""}>${label}</span>`;
}

function drawChart(canvas, hist, r) {
  const dpr = window.devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, W, H);
  const pts = hist.slice();
  if (r.live && pts.length) pts.push({ t: r.active, best: r.tunnels, upper: r.upper });
  const L = 42, R = 10, T = 10, B = 22;
  g.font = "11px system-ui, sans-serif";
  g.fillStyle = "#8d97aa";
  if (!pts.length) { g.fillText("waiting for the first solution…", L, H / 2); return; }
  const tMax = Math.max(r.live ? 10 : 1, ...pts.map(p => p.t));
  const vals = pts.flatMap(p => [p.best, p.upper]).filter(v => v != null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 4) { lo -= 2; hi += 2; }
  const pad = (hi - lo) * 0.08; lo = Math.max(0, lo - pad); hi += pad;
  const X = t => L + (t / tMax) * (W - L - R), Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  g.strokeStyle = "#2a3242"; g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, y = Y(v);
    g.beginPath(); g.moveTo(L, y); g.lineTo(W - R, y); g.stroke();
    g.fillText(Math.round(v), 6, y + 4);
  }
  for (let i = 0; i <= 4; i++) g.fillText(fmtTime(tMax * i / 4), X(tMax * i / 4) - (i === 4 ? 30 : i ? 14 : 0), H - 6);
  const line = (field, color, dash) => {
    g.strokeStyle = color; g.lineWidth = 2; g.setLineDash(dash || []);
    g.beginPath();
    let started = false, prev = null;
    for (const p of pts) {
      if (p[field] == null) continue;
      const x = X(p.t), y = Y(p[field]);
      if (!started) { g.moveTo(x, y); started = true; } else { g.lineTo(x, Y(prev)); g.lineTo(x, y); }
      prev = p[field];
    }
    if (started) g.lineTo(X(tMax), Y(prev));
    g.stroke(); g.setLineDash([]);
  };
  line("upper", "#4de3ff", [5, 4]);
  line("best", "#a985ef");
}

// --------------------------------------------------------------------------- library
const Lib = {
  thumbs: new Map(),
  queue: [],
  thumbViewer: null,

  init() {
    this.thumbViewer = new Viewer($("#thumbCanvas"), { interactive: false, preserve: true });
    this.renderKnown();
    this.renderExamples(S.server.examples);
  },

  card(src, title, meta, folder, status, extra = "", group = "") {
    const t = this.thumbs.get(src);
    const TAGS = { proven: "Proven optimal", best: "Best known" };
    const tag = TAGS[status] ? `<span class="tag ${status}">${TAGS[status]}</span>` : "";
    return `<div class="lcard" data-src="${esc(src)}" data-title="${esc(title)}"${group ? ` data-group="${esc(group)}"` : ""}><div class="thumb">${t ? `<img src="${t}" alt="">` : `<div class="spinner"></div>`}</div>
      <div class="body">${tag}${folder ? `<div class="folder">${esc(folder)}</div>` : ""}<div class="name">${esc(title)}</div><div class="meta">${meta}</div>${extra}</div></div>`;
  },

  // A thin bar: how close the best known build is to the proven maximum.
  meter(tunnels, upper) {
    if (tunnels == null || !upper) return "";
    return `<div class="meter" title="${tunnels} of at most ${upper} tunnels"><i style="width:${Math.min(1, tunnels / upper) * 100}%"></i></div>`;
  },

  // The shipped results, updated with anything the user's runs proved or beat
  // (same rules as the library: default capacities, coloured wiring).
  renderKnown() {
    const el = $("#libKnown");
    const libKey = S.server.library?.length ? rulesKey(S.server.library[0].rules) : null;
    const groups = [...resultGroups().values()].filter(g => g.tunnels != null && (g.entry || rulesKey(g.rules) === libKey))
      .sort((a, b) => (a.proven ? 0 : 1) - (b.proven ? 0 : 1) || a.box[0] * a.box[1] * a.box[2] - b.box[0] * b.box[1] * b.box[2]);
    this.known = new Map(groups.map(g => [g.key, g]));
    const proven = groups.filter(g => g.proven).length, yours = groups.filter(g => g.run).length;
    $("#libKnownNote").textContent = groups.length ? `· default config (8 / 32), coloured wiring · ${proven} proven, ${groups.length - proven} best known${yours ? ` · ${yours} from your runs` : ""}` : "";
    const html = groups.length ? groups.map(g => {
      const src = g.run ? `run:${g.run.id}` : libSrc(g.entry);
      const meta = [g.proven ? `${g.tunnels} tunnels` : `${g.tunnels} tunnels (max ${g.upper})`,
        g.controllers != null && `${g.controllers} controllers`, g.trunks != null && `${g.trunks} trunk${g.trunks === 1 ? "" : "s"}`].filter(Boolean).join(" · ");
      const folder = g.run ? (g.entry ? `your run · shipped ${g.entry.status === "proven" ? "proof" : "best"} was ${g.entry.tunnels}${g.entry.upper !== g.upper ? ` (max ${g.entry.upper})` : ""}` : "your run") : "";
      return this.card(src, g.box.join("×"), meta, folder, g.proven ? "proven" : "best", g.proven ? "" : this.meter(g.tunnels, g.upper), g.key);
    }).join("") : `<p class="none">No library bundled.</p>`;
    if (html !== this.knownHtml) { el.innerHTML = this.knownHtml = html; this.thumbAll(el); }
    el.onclick = ev => {
      const c = ev.target.closest(".lcard");
      if (c) openKnown(this.known.get(c.dataset.group));
    };
  },

  renderExamples(list) {
    const el = $("#libExamples");
    $("#libExamplesHead").hidden = el.hidden = !list.length;
    el.innerHTML = list.length ? list.map(x => this.card(`example:${x.id}`, x.name,
      `${x.controllers} controllers · ${x.tunnels} input tunnels`, x.folder === "." ? "" : x.folder)).join("")
      : `<p class="none">No example files found.</p>`;
    el.onclick = e => this.open(e);
    this.thumbAll(el);
  },

  // The user's runs, grouped by box and settings, each group headed by what's known for it.
  renderRuns(runs) {
    const el = $("#libRuns"), groups = resultGroups();
    const byGroup = new Map();
    for (const r of runs.filter(r => r.has_best)) {
      const k = r.rules && comparable(r.rules) ? `${boxId(r.box)}|${rulesKey(r.rules)}` : `custom|${r.id}`;
      if (!byGroup.has(k)) byGroup.set(k, []);
      byGroup.get(k).push(r);
    }
    const latest = list => Math.max(...list.map(r => r.modified));
    const order = [...byGroup.entries()].sort((a, b) => latest(b[1]) - latest(a[1]));
    const html = order.length ? order.map(([k, list]) => {
      const g = groups.get(k), r0 = list[0];
      const head = !g ? `<b>${r0.box.join("×")}</b><span class="muted">custom settings</span>`
        : `<b>${g.box.join("×")}</b>${g.proven ? `<span class="tag proven">Proven optimal</span>` : `<span class="tag best">Best known</span>`}
           <span>${g.tunnels} tunnels${g.proven ? "" : ` of at most ${g.upper} · ${pct(g.tunnels, g.upper)}`}${g.run ? " · your run" : g.entry ? " · shipped" : ""}</span>
           ${g.proven ? "" : this.meter(g.tunnels, g.upper)}<span class="muted">${esc(this.rulesLabel(g.rules))}</span>`;
      list.sort((a, b) => (b.tunnels ?? -1) - (a.tunnels ?? -1) || b.modified - a.modified);
      const cards = list.map(r => {
        const proved = r.upper != null && r.tunnels >= r.upper, holds = g && g.run?.id === r.id;
        const d = g && g.tunnels != null && r.tunnels != null ? g.tunnels - r.tunnels : null;
        const behind = !proved && !holds && d != null ? ` · ${d ? `${d} below best` : "ties best"}` : "";
        return this.card(`run:${r.id}`, `${r.tunnels ?? "?"} tunnels`,
          `${r.upper && !proved ? `${pct(r.tunnels, r.upper)} of max ${r.upper} · ` : ""}${fmtTime(r.active)}${behind}`,
          r.id, proved ? "proven" : holds ? "best" : "");
      }).join("");
      return `<div class="rgroup"><div class="rgroup-head">${head}</div><div class="cards">${cards}</div></div>`;
    }).join("") : `<p class="none">Results from the Optimize tab appear here.</p>`;
    if (html !== this.runsHtml) { el.innerHTML = this.runsHtml = html; this.thumbAll(el); }
    el.onclick = e => this.open(e);
    if (this.knownHtml !== undefined) this.renderKnown();
  },

  rulesLabel(r) {
    const lib = S.server.library?.[0]?.rules;
    if (lib && rulesKey(r) === rulesKey(lib)) return "default config";
    const where = r.exterior && r.interior ? "" : r.exterior ? ", outside only" : ", inside only";
    return `${r.normal} / ${r.dense}${where}${r.anchor_free ? "" : ", anchors allowed"}`;
  },

  async open(e) {
    const c = e.target.closest(".lcard");
    if (!c) return;
    if (c.dataset.src.startsWith("run:")) return openRun(c.dataset.src.slice(4), c.dataset.title);
    try {
      const lay = await api(`/api/layout?src=${encodeURIComponent(c.dataset.src)}`);
      setLayout(lay, { title: c.dataset.title });
      showTab("design");
    } catch (err) { toast(err.message, "bad"); }
  },

  thumbAll(el) {
    for (const c of $$(".lcard", el)) if (!this.thumbs.has(c.dataset.src)) this.queue.push(c.dataset.src);
    if (!this.running) this.pump();
  },

  async pump() {
    this.running = true;
    while (this.queue.length) {
      const src = this.queue.shift();
      if (this.thumbs.has(src)) continue;
      try {
        const lay = await api(`/api/layout?src=${encodeURIComponent(src)}`);
        const m = new Map(lay.blocks.map(b => [key(b.pos), b]));
        const v = this.thumbViewer;
        v.setItems(buildItems(m, {}), { refit: true });
        v.cam.yaw = -0.75; v.cam.pitch = 0.55;
        v.fit(false);
        this.thumbs.set(src, v.snapshot(320, 200));
      } catch { this.thumbs.set(src, ""); }
      for (const c of $$(`.lcard[data-src="${CSS.escape(src)}"] .thumb`)) {
        const t = this.thumbs.get(src);
        c.innerHTML = t ? `<img src="${t}" alt="">` : "";
      }
    }
    this.running = false;
  },
};

// --------------------------------------------------------------------------- profiles tab
const Prof = {
  editing: null,

  init() {
    $("#btnNewProfile").onclick = () => this.edit({ ...S.profiles[0], name: "My pack", source: "manual" });
    for (const [range, num] of [["#peNormal", "#peNormalNum"], ["#peDense", "#peDenseNum"]]) {
      $(range).max = S.server.limits.capacity_max;
      $(range).oninput = () => { $(num).value = $(range).value; this.changed(); };
      $(num).oninput = () => { $(range).value = $(num).value; this.changed(); };
    }
    $("#peName").oninput = () => this.changed();
    $("#peChannels").onchange = () => this.changed();
    $("#btnSaveProfile").onclick = () => this.save();
    $("#btnUseProfile").onclick = () => { if (this.editing) useProfile(this.editing.name); toast(`Using ${this.editing.name}`, "ok"); };
    $("#btnDeleteProfile").onclick = () => this.remove();
    const drop = $("#cfgDrop");
    $("#cfgFile").onchange = e => { const f = e.target.files[0]; e.target.value = ""; if (f) this.importFile(f); };
    drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) this.importFile(f); });
    this.renderList();
    this.edit(S.profile);
  },

  renderList() {
    $("#profileList").innerHTML = S.profiles.map(p => `<button class="pitem ${this.editing?.name === p.name ? "sel" : ""}" data-n="${esc(p.name)}">
      ${p.name === S.profile.name ? `<span class="act">IN USE</span>` : ""}${esc(p.name)}<small>${profileSummary(p)}${p.channels_enabled ? "" : " · channels off"}</small></button>`).join("");
    $("#profileList").onclick = e => { const b = e.target.closest(".pitem"); if (b) this.edit(S.profiles.find(p => p.name === b.dataset.n)); };
  },

  edit(p) {
    this.editing = { ...p, max_controller_size: [...p.max_controller_size] };
    const ro = p.name === S.server.default_profile;
    $("#peTitle").textContent = ro ? "Default profile (read-only: save a copy to change it)" : "Edit profile";
    $("#peName").value = ro ? `${p.name.replace(/ \(.*\)$/, "")} copy` : p.name;
    if (ro) $("#peName").value = "My pack";
    $("#peNormal").value = $("#peNormalNum").value = p.normal_channel_capacity;
    $("#peDense").value = $("#peDenseNum").value = p.dense_channel_capacity;
    $("#peChannels").checked = p.channels_enabled;
    $("#btnDeleteProfile").hidden = ro;
    $("#peNote").textContent = p.source ? `Source: ${p.source}` : "";
    this.renderSize();
    this.renderList();
    this.changed(false);
  },

  renderSize() {
    const s = this.editing.max_controller_size, m = S.server.limits.size_max;
    $("#peSize").innerHTML = ["X", "Y", "Z"].map((n, i) => `<div class="axis"><b>${n}</b><input type="range" min="1" max="${m}" value="${s[i]}" data-i="${i}"><output>${s[i]}</output></div>`).join("");
    $("#peSize").oninput = e => {
      this.editing.max_controller_size[+e.target.dataset.i] = +e.target.value;
      e.target.nextElementSibling.textContent = e.target.value;
      this.changed();
    };
  },

  values() {
    return {
      name: $("#peName").value.trim(), normal_channel_capacity: +$("#peNormalNum").value,
      dense_channel_capacity: +$("#peDenseNum").value, max_controller_size: this.editing.max_controller_size,
      channels_enabled: $("#peChannels").checked, source: this.editing.source || "manual",
    };
  },

  changed() {
    const v = this.values(), w = [];
    if (v.normal_channel_capacity > v.dense_channel_capacity) w.push("Smart cable carries more than dense cable, which is unusual.");
    if (!v.channels_enabled) w.push("Channels are off: every device works without a controller, so there is nothing to optimize.");
    if (Math.max(...v.max_controller_size) > 7) w.push("Controllers bigger than 7 per side make optimizing very slow; keep optimizer boxes small.");
    if (v.name === S.server.default_profile) w.push("Pick a new name: the default profile can't be overwritten.");
    $("#peWarnings").innerHTML = w.map(x => `<div>${esc(x)}</div>`).join("");
  },

  async save() {
    const v = this.values();
    if (!v.name) return toast("Give the profile a name.", "bad");
    try {
      const r = await api("/api/profiles", { profile: { ...v, source: v.source === "built-in default" ? "manual" : v.source } });
      setProfiles(r.profiles);
      useProfile(v.name);
      this.edit(S.profiles.find(p => p.name === v.name));
      toast(`Saved “${v.name}” and switched to it`, "ok");
    } catch (err) { toast(err.message, "bad"); }
  },

  async remove() {
    const n = this.editing?.name;
    if (!n || !confirm(`Delete the profile “${n}”?`)) return;
    try {
      const r = await api("/api/profiles/delete", { name: n });
      if (S.profile.name === n) S.profile = null;
      setProfiles(r.profiles);
      useProfile(S.profile.name);
      this.edit(S.profile);
    } catch (err) { toast(err.message, "bad"); }
  },

  async importFile(f) {
    try {
      const text = await f.text();
      const r = await api("/api/import-cfg", { text, name: f.name.replace(/\.[^.]+$/, "") });
      const label = { normal_channel_capacity: "Smart cable channels", dense_channel_capacity: "Dense cable channels", size_x: "Controller size X",
        size_y: "Controller size Y", size_z: "Controller size Z", channels_enabled: "Channels enabled" };
      $("#cfgResult").innerHTML = `<p class="small" style="margin-top:12px">${esc(r.summary)}</p>
        <table class="imp">${Object.entries(r.fields).map(([k, fd]) => `<tr><td>${label[k] || k}</td><td><span class="chip ${fd.status === "from file" ? "from" : fd.status.startsWith("couldn") ? "bad" : ""}" title="${esc(fd.location || "")}">${esc(fd.status)}</span></td><td class="v">${esc(fd.value)}</td></tr>`).join("")}</table>
        ${r.warnings.map(w => `<div class="warnings"><div>${esc(w)}</div></div>`).join("")}
        <button class="btn primary" id="btnLoadImport">Load into the editor</button>`;
      $("#btnLoadImport").onclick = () => {
        this.edit({ ...r.profile, name: r.profile.name || "Imported config" });
        $("#peName").value = r.profile.name;
        toast("Loaded. Check the values, then Save profile.", "ok");
      };
    } catch (err) { toast(`Import failed: ${err.message}`, "bad"); }
  },
};

// --------------------------------------------------------------------------- tabs, keys, boot
function showTab(name) {
  $$(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === `tab-${name}`));
  store.set("tab", name);
  if (name === "design") Design.viewer.requestDraw();
  if (name === "optimize") { Opt.mini.requestDraw(); Opt.poll(); }
  if (name === "library") Lib.thumbAll($("#libExamples"));
}

function initKeys() {
  document.addEventListener("keydown", e => {
    if (e.target.matches("input, select, textarea")) return;
    if (!$("#tab-design").classList.contains("active")) return;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); redo(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = TOOLS.find(t => t && t.key.toLowerCase() === k);
    if (t) { setTool(t.id); return; }
    if (k === "f") Design.viewer.fit(true);
    else if (k === "x") Design.toggle("xray");
    else if (k === "h") Design.toggle("heat");
    else if (k === "escape") { setTool("select"); S.selected = null; S.marks = null; Inspector.render(); Design.render(); }
    else if ((k === "delete" || k === "backspace") && S.selected) { commit(() => { S.blocks.delete(S.selected); }); S.selected = null; Inspector.render(); }
    else if (k === "[" || k === "]") {
      const r = $("#sliceRange");
      r.value = Math.max(+r.min, Math.min(+r.max, +r.value + (k === "]" ? 1 : -1)));
      r.dispatchEvent(new Event("input"));
    }
  });
}

async function quitApp() {
  const live = (Opt.runs || []).filter(r => r.live).length;
  if (!confirm(live ? `Quit? ${live} running optimizer run${live > 1 ? "s" : ""} will be saved and stopped; you can continue ${live > 1 ? "them" : "it"} later.` : "Quit the app?")) return;
  try { await api("/api/quit", {}); } catch { /* already gone */ }
  document.body.innerHTML = `<div class="closed"><h2>AE2 Controller Optimizer has closed</h2>
    <p>${live ? "Your runs are being saved. " : ""}You can close this tab. Start the app again to come back.</p></div>`;
}

async function boot() {
  try { S.server = await api("/api/state"); }
  catch (err) { document.body.innerHTML = `<div class="empty"><h2>Can't reach the app server</h2><p>${esc(err.message)}. Start it with <code>python -m ae2opt gui</code>.</p></div>`; return; }
  setProfiles(S.server.profiles);
  $("#appVersion").textContent = `v${S.server.version}`;
  $("#profileSelect").onchange = e => useProfile(e.target.value);
  // Tells the server a page is open; with none open (and no run going) it exits.
  setInterval(() => api("/api/heartbeat", {}).catch(() => {}), 20000);
  $("#btnQuit").onclick = quitApp;
  $$(".tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
  Design.init();
  initDesignBar();
  initKeys();
  Lib.init();
  Opt.init();
  Prof.init();
  const draft = store.get("draft", null);
  if (draft?.layout?.blocks?.length) setLayout(draft.layout, { title: draft.title });
  else {
    const e = (S.server.library || []).find(x => x.id === "3x3x3");
    if (e) setLayout(await api(`/api/layout?src=${encodeURIComponent(libSrc(e))}`), { title: libTitle(e) });
    else setLayout({ blocks: [] });
  }
  showTab(store.get("tab", "design"));
}

boot();
