"""Build instructions for a layout: bill of materials, layer-by-layer guide,
input list (for linking with a memory card) and tunnel-network sizing.

    python -m ae2opt guide examples/best_7x7x7.json --out guide.html

The data comes from build_plan(); render_html() turns it into one
self-contained HTML file (no internet needed).

Tunnel-network sizing. Every input tunnel and every output tunnel uses one
tunnel-network channel, so N inputs with one output each need 2N. They reach
the tunnel network through:
  * the build's exit cells: each carries the channels of the tunnels routed
    through it (at most the normal capacity). Exits are grouped first-fit
    decreasing into trunks of at most the dense capacity.
  * outputs: grouped into trunks of the dense capacity as well.
Each trunk needs one 32-channel outlet on the tunnel network's own controller.
A dense cable can carry only one face's 32 channels, so every outlet needs its
own route out. That is the same layout problem as the main build, with every
cable limited to one face, so the optimizer designs the tunnel-network
controller too (outlet mode: capacities 1, in units of a face).
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, field

from .geometry import Direction, Pos, step
from .layout import CableAnchor, CableBus, CableType, Controller, Layout, P2PTunnel, TunnelPort
from .profile import DEFAULT_PROFILE, Profile

D = Direction
SIDE_NAMES = {D.DOWN: "down", D.UP: "up", D.NORTH: "north", D.SOUTH: "south", D.WEST: "west", D.EAST: "east"}


@dataclass
class Plan:
    title: str
    profile: Profile
    origin: Pos                     # minimum corner of the build (relative coordinates start here)
    size: tuple[int, int, int]
    cells: list[dict]               # every block, relative coordinates, with parts per side
    bom: dict
    inputs: list[dict]
    exits: list[dict]
    trunks: list[dict]
    tunnel_network: dict
    tunnel_controller: dict | None = None   # recommended tunnel-network controller (its own cells)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- analysis

def _cell_record(pos: Pos, b, origin: Pos, ports: set[Pos], blocks: dict) -> dict:
    rel = [p - o for p, o in zip(pos, origin)]
    rec = {"pos": rel, "kind": b.kind}
    if isinstance(b, CableBus):
        rec["cable"] = b.cable.value
        rec["color"] = b.color
        sides = {}
        for d in Direction:
            part = b.parts.get(d)
            if isinstance(part, P2PTunnel):
                sides[SIDE_NAMES[d]] = {"part": "output" if part.output else "tunnel", "freq": part.freq}
            elif isinstance(part, CableAnchor):
                sides[SIDE_NAMES[d]] = {"part": "anchor"}
            elif part is not None:
                sides[SIDE_NAMES[d]] = {"part": part.kind}
            elif step(pos, d) in ports:
                sides[SIDE_NAMES[d]] = {"part": "exit"}
            elif isinstance(blocks.get(step(pos, d)), Controller):
                sides[SIDE_NAMES[d]] = {"part": "outlet"}   # open toward a controller face
        rec["sides"] = sides
    return rec


def bill_of_materials(lay: Layout) -> dict:
    counts = {"controllers": 0, "smart_cable": 0, "dense_smart_cable": 0, "p2p_input": 0,
              "p2p_output": 0, "cable_anchor": 0, "other": 0}
    for b in lay.blocks.values():
        if isinstance(b, Controller):
            counts["controllers"] += 1
        elif isinstance(b, CableBus):
            if b.cable is CableType.DENSE:
                counts["dense_smart_cable"] += 1
            elif b.cable is CableType.NORMAL:
                counts["smart_cable"] += 1
            for p in b.parts.values():
                if isinstance(p, P2PTunnel):
                    counts["p2p_output" if p.output else "p2p_input"] += 1
                elif isinstance(p, CableAnchor):
                    counts["cable_anchor"] += 1
        elif not isinstance(b, TunnelPort):
            counts["other"] += 1
    return counts


def _pack(items: list[tuple[int, object]], cap: int) -> list[list]:
    """First-fit decreasing bin packing of (size, item) into bins of `cap`."""
    bins: list[list] = []
    loads: list[int] = []
    for size, item in sorted(items, key=lambda t: -t[0]):
        for i, load in enumerate(loads):
            if load + size <= cap:
                bins[i].append((size, item))
                loads[i] += size
                break
        else:
            bins.append([(size, item)])
            loads.append(size)
    return bins


def build_plan(layout: Layout, profile: Profile = DEFAULT_PROFILE, title: str = "AE2 controller build",
               design_tunnel_controller: bool = True, time_limit: float = 60.0, workers: int = 8) -> Plan:
    from .rules import check

    ports = {p for p, b in layout.blocks.items() if isinstance(b, TunnelPort)}
    build_cells = {p: b for p, b in layout.blocks.items() if not isinstance(b, TunnelPort)}
    if not build_cells:
        raise ValueError("layout is empty")
    xs, ys, zs = zip(*build_cells)
    origin = (min(xs), min(ys), min(zs))
    size = (max(xs) - origin[0] + 1, max(ys) - origin[1] + 1, max(zs) - origin[2] + 1)
    if not ports:
        # No saved ports: an exit is a cable side left open toward outside the build.
        inside = lambda p: all(origin[i] <= p[i] < origin[i] + size[i] for i in range(3))
        for pos, b in build_cells.items():
            if isinstance(b, CableBus) and b.cable is not CableType.NONE:
                ports.update(step(pos, d) for d in Direction
                             if d not in b.parts and not inside(step(pos, d)))
        layout = Layout()
        layout.blocks = dict(build_cells)
        for p in ports:
            layout.port(p)
    build = Layout()
    build.blocks = dict(build_cells)
    notes = []

    cells = [_cell_record(p, b, origin, ports, build_cells) for p, b in sorted(build_cells.items())]
    bom = bill_of_materials(build)

    # Inputs, in build order (bottom layer first), labelled I1..IN.
    inputs = []
    for pos in sorted(build_cells, key=lambda p: (p[1], p[2], p[0])):
        b = build_cells[pos]
        if not isinstance(b, CableBus):
            continue
        for d in Direction:
            part = b.parts.get(d)
            if isinstance(part, P2PTunnel) and not part.output:
                faced = step(pos, d)
                inputs.append({"label": f"I{len(inputs) + 1}", "pos": [p - o for p, o in zip(pos, origin)],
                               "side": SIDE_NAMES[d], "freq": part.freq,
                               "faces_controller": isinstance(build_cells.get(faced), Controller)})

    # Exit loads from the simulator: needs the tunnel-network ports saved with the layout.
    exits = []
    if ports:
        report = check(layout, profile)
        g = report.sim.graph
        for pos in sorted(build_cells):
            b = build_cells[pos]
            if not isinstance(b, CableBus):
                continue
            out_sides = [d for d in Direction if step(pos, d) in ports and d not in b.parts]
            if not out_sides:
                continue
            node = g.centers.get(pos)
            load = max((c.used for c in node.conns), default=0) if node else 0
            exits.append({"pos": [p - o for p, o in zip(pos, origin)], "sides": [SIDE_NAMES[d] for d in out_sides],
                          "channels": load})
        if not report.ok:
            notes.append("the layout does not pass the rules check: " +
                         "; ".join(str(v) for v in report.errors()[:3]))
    else:
        notes.append("this layout has no tunnel-network ports (P), so exit loads are unknown; "
                     "save layouts with `optimize --out` to include them")

    dense = profile.dense_channel_capacity
    n_in = len(inputs)
    trunks = []
    for i, group in enumerate(_pack([(e["channels"], j) for j, e in enumerate(exits)], dense), 1):
        trunks.append({"label": f"T{i}", "channels": sum(s for s, _ in group),
                       "exits": [exits[j]["pos"] for _, j in group]})
        for _, j in group:
            exits[j]["trunk"] = f"T{i}"
    out_trunks = math.ceil(n_in / dense) if n_in else 0
    tunnel_network = {
        "inputs": n_in,
        "outputs": n_in,
        "channels": 2 * n_in,
        "input_trunks": len(trunks),
        "output_trunks": out_trunks,
        "outlets": len(trunks) + out_trunks,
        "main_channels": n_in * dense,
    }

    plan = Plan(title, profile, origin, size, cells, bom, inputs, exits, trunks, tunnel_network, notes=notes)
    if design_tunnel_controller and tunnel_network["outlets"]:
        try:
            plan.tunnel_controller = design_outlet_controller(tunnel_network["outlets"], profile,
                                                              time_limit=time_limit, workers=workers)
        except Exception as err:  # optimizer missing or no solution: the guide still works
            notes.append(f"no tunnel-network controller design: {err}")
    return plan


# --------------------------------------------------------------------------- tunnel-network controller

def outlet_layout(sol_harness: Layout) -> Layout:
    """Turn an outlet-mode optimizer result into a real build: every tunnel cell
    becomes a dense cable left open toward its one controller face (the outlet),
    every other controller-facing side keeps its anchor, and routes are dense."""
    lay = Layout()
    for pos, b in sol_harness.blocks.items():
        if isinstance(b, CableBus):
            parts = {d: p for d, p in b.parts.items() if isinstance(p, CableAnchor)}
            lay.cable(pos, CableType.DENSE, b.color, parts)
        elif isinstance(b, Controller):
            lay.controller(pos)
        else:
            lay.place(pos, b)
    return lay


def check_outlets(lay: Layout, profile: Profile) -> tuple[int, list[str]]:
    """Structural check of an outlet design: every cable touching a controller
    touches exactly one face, and no route cable touches one. Returns
    (outlets, problems)."""
    from .graph import build_graph
    g = build_graph(lay, profile)
    outlets, problems = 0, []
    for pos, node in g.centers.items():
        faces = [c for c in node.conns if c.other(node).kind == "controller"]
        if len(faces) == 1:
            outlets += 1
        elif len(faces) > 1:
            problems.append(f"cable at {pos} touches {len(faces)} controller faces (shares one 32-channel cable)")
    return outlets, problems


def design_outlet_controller(outlets: int, profile: Profile, time_limit: float = 60.0, workers: int = 8) -> dict:
    from .optimize import OptimizeSpec, optimize
    unit = Profile(name="outlets", normal_channel_capacity=1, dense_channel_capacity=1,
                   max_controller_size=profile.max_controller_size)
    sol = optimize(unit, OptimizeSpec(target_tunnels=outlets, time_limit=time_limit, workers=workers))
    lay = outlet_layout(sol.harness)
    found, problems = check_outlets(lay, profile)
    if problems:
        raise RuntimeError("; ".join(problems[:3]))
    build = {p: b for p, b in lay.blocks.items() if not isinstance(b, TunnelPort)}
    ports = {p for p, b in lay.blocks.items() if isinstance(b, TunnelPort)}
    xs, ys, zs = zip(*build)
    origin = (min(xs), min(ys), min(zs))
    cells = [_cell_record(p, b, origin, ports, build) for p, b in sorted(build.items())]
    sub = Layout()
    sub.blocks = build
    return {
        "outlets": found,
        "needed": outlets,
        "channels": found * profile.dense_channel_capacity,
        "size": [max(xs) - origin[0] + 1, max(ys) - origin[1] + 1, max(zs) - origin[2] + 1],
        "cells": cells,
        "bom": bill_of_materials(sub),
        "layout": lay,
    }


# --------------------------------------------------------------------------- HTML

def plan_json(plan: Plan) -> dict:
    tc = None
    if plan.tunnel_controller:
        tc = {k: v for k, v in plan.tunnel_controller.items() if k != "layout"}
    return {
        "title": plan.title,
        "profile": {"name": plan.profile.name, "normal": plan.profile.normal_channel_capacity,
                    "dense": plan.profile.dense_channel_capacity,
                    "size": list(plan.profile.max_controller_size)},
        "origin": list(plan.origin), "size": list(plan.size), "cells": plan.cells, "bom": plan.bom,
        "inputs": plan.inputs, "exits": plan.exits, "trunks": plan.trunks,
        "tunnel_network": plan.tunnel_network, "tunnel_controller": tc, "notes": plan.notes,
    }


def render_html(plan: Plan) -> str:
    data = json.dumps(plan_json(plan), separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__TITLE__", html.escape(plan.title)).replace("__DATA__", data)


def save_html(plan: Plan, path) -> None:
    from pathlib import Path
    Path(path).write_text(render_html(plan), "utf-8")


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1d2330;--muted:#5d6678;--line:#dde1e8;--accent:#6b46c1;
--ctrl:#2d3446;--ctrl-ink:#e9ecf3;--cable:#13a3a3;--dense:#7d4fd6;--tunnel:#f08c00;--anchor:#8a93a6;
--exit:#2f9e44;--output:#d6336c;--sel:#ffd43b;--done:#e6f4ea}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#11141b;--panel:#1a1f2a;--ink:#e6e9f0;
--muted:#9aa3b5;--line:#2c3342;--ctrl:#c9cfdc;--ctrl-ink:#11141b;--anchor:#6b7385;--done:#1f3326}}
:root[data-theme=dark]{--bg:#11141b;--panel:#1a1f2a;--ink:#e6e9f0;--muted:#9aa3b5;--line:#2c3342;--ctrl:#c9cfdc;
--ctrl-ink:#11141b;--anchor:#6b7385;--done:#1f3326}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.15rem;margin:1.6em 0 .5em}
.muted{color:var(--muted)}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin:10px 0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stat b{display:block;font-size:1.4rem}.stat span{color:var(--muted);font-size:.85rem}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);font-size:.9rem}
th{color:var(--muted);font-weight:600}tr.done td{background:var(--done)}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
button,input,select{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:4px 10px}
button{cursor:pointer}button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
input.coord{width:5.2em}
.viewer{display:grid;grid-template-columns:minmax(0,auto) minmax(220px,1fr);gap:14px;align-items:start}
@media (max-width:760px){.viewer{grid-template-columns:1fr}}
.gridwrap{overflow:auto;max-width:100%}
svg.grid{display:block}svg.grid text{font:600 11px system-ui,sans-serif}
.legend{display:flex;flex-wrap:wrap;gap:10px;font-size:.85rem;color:var(--muted)}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin-right:4px}
.detail h3{margin:.1em 0 .4em;font-size:1rem}.detail ul{margin:.2em 0;padding-left:1.2em}
.layerbtns button{min-width:2.6em}.ok{color:var(--exit)}.warn{color:var(--output)}
details summary{cursor:pointer;color:var(--accent)}
</style>
</head>
<body>
<main>
<h1 id="title"></h1>
<div class="muted" id="subtitle"></div>
<div class="panel stats" id="stats"></div>
<div id="notes"></div>

<div class="panel">
<b>Where is your build?</b> <span class="muted">Enter the world position of the build's lowest north-west corner
(the cell at x=0, layer 0, z=0 in this guide). Every cell then shows its world coordinates.</span>
<div class="toolbar">X <input class="coord" id="ox" type="number" value="0"> Y <input class="coord" id="oy" type="number" value="64">
Z <input class="coord" id="oz" type="number" value="0"></div>
</div>

<h2>1. Bill of materials</h2>
<div class="panel"><table id="bom"></table>
<p class="muted">Counts are AE2 parts and blocks. Their crafting cost depends on your pack's recipes (check JEI).
Use fluix (uncoloured) cables unless noted.</p></div>

<h2>2. Build it layer by layer</h2>
<div class="panel">
<ol>
<li>Start at layer 0 and work up. On each layer, place the <b>cable cells first</b>, with all their parts, then the <b>controllers</b>.</li>
<li>Put each part on the side shown. Hold the part and right-click that side of the cable, or right-click the neighbouring controller's face.</li>
<li>Parts on a cell's <b>up</b> side face the next layer: place them before you build that layer.</li>
<li>Every controller face next to a cable cell needs a part: a <b>tunnel</b> or a <b>cable anchor</b>. A missing anchor merges the two networks.</li>
<li><span class="ok">Exit</span> sides stay open. That's where your tunnel network's cable connects.</li>
</ol>
<div class="legend">
<span><i class="sw" style="background:var(--ctrl)"></i>controller</span>
<span><i class="sw" style="background:var(--cable)"></i>smart cable cell</span>
<span><i class="sw" style="background:var(--dense)"></i>dense smart cable</span>
<span><i class="sw" style="background:var(--tunnel)"></i>P2P tunnel on that side</span>
<span><i class="sw" style="background:var(--anchor)"></i>cable anchor</span>
<span><i class="sw" style="background:var(--exit)"></i>exit (leave open)</span>
<span>▲/▼ part on the up/down side &nbsp; number = tunnels in the cell</span>
</div>
<div id="viewer-main"></div>
</div>

<h2>3. Link the input tunnels</h2>
<div class="panel">
<p>Each input carries one controller face's channels (<b id="facech"></b>) to its output.
Shift-right-click an input with a <b>Memory Card</b> to store its frequency, then right-click the output tunnel
(where you want the channels) to link it. Tick each input when it's done: ticks are saved in this browser.</p>
<div class="toolbar"><label>Layer <select id="inlayer"><option value="">all</option></select></label>
<span id="inprogress" class="muted"></span></div>
<div class="gridwrap"><table id="inputs"></table></div>
</div>

<h2>4. Tunnel network</h2>
<div class="panel" id="tn"></div>
<div id="viewer-tc"></div>
</main>

<script>
const PLAN = __DATA__;
const SIDES = ["north","east","south","west"];
const $ = (id) => document.getElementById(id);
const store = {
  get(k, d){ try { const v = localStorage.getItem("ae2opt:" + PLAN.title + ":" + k); return v === null ? d : JSON.parse(v); } catch(e){ return d; } },
  set(k, v){ try { localStorage.setItem("ae2opt:" + PLAN.title + ":" + k, JSON.stringify(v)); } catch(e){} }
};
const origin = () => [+$("ox").value || 0, +$("oy").value || 0, +$("oz").value || 0];
const world = (p) => { const o = origin(); return [p[0] + o[0], p[1] + o[1], p[2] + o[2]]; };
const fmt = (p) => p.join(", ");
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

$("title").textContent = PLAN.title;
const pr = PLAN.profile;
$("subtitle").textContent = `Profile ${pr.name}: normal cable ${pr.normal}, dense ${pr.dense}, controller size ${pr.size.join("×")}. Build is ${PLAN.size.join(" × ")} blocks (x × layers × z).`;
$("facech").textContent = pr.dense + " channels";
const tn = PLAN.tunnel_network;
$("stats").innerHTML = [
  [PLAN.inputs.length, "input tunnels"],
  [(PLAN.inputs.length * pr.dense).toLocaleString(), "channels for your main network"],
  [PLAN.bom.controllers, "controllers"],
  [tn.channels.toLocaleString(), "tunnel-network channels needed"],
].map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
$("notes").innerHTML = PLAN.notes.map(n => `<div class="panel warn">Note: ${n}</div>`).join("");
const ox = store.get("origin", null);
if (ox) { $("ox").value = ox[0]; $("oy").value = ox[1]; $("oz").value = ox[2]; }
for (const id of ["ox","oy","oz"]) $(id).addEventListener("input", () => { store.set("origin", origin()); refresh(); });

const BOM_LABELS = {controllers:"ME Controller", smart_cable:"ME Smart Cable (fluix), one per cable cell",
  dense_smart_cable:"ME Dense Smart Cable (fluix)", p2p_input:"ME P2P Tunnel: inputs (in the build)",
  p2p_output:"ME P2P Tunnel: outputs", cable_anchor:"Cable Anchor", other:"Other blocks"};
function bomRows(b){ return Object.entries(b).filter(([k,v]) => v).map(([k,v]) => `<tr><td>${BOM_LABELS[k]||k}</td><td>${v.toLocaleString()}</td></tr>`).join(""); }
$("bom").innerHTML = "<tr><th>Item</th><th>Count</th></tr>" + bomRows(PLAN.bom) +
  `<tr><td>ME P2P Tunnel: outputs to place where you need channels (1 per input)</td><td>${PLAN.inputs.length.toLocaleString()}</td></tr>` +
  `<tr><td>Memory Card (for linking)</td><td>1</td></tr>`;

// ------------------------------------------------------------------ layer viewer
function makeViewer(host, cells, size, key){
  const byLayer = new Map();
  for (const c of cells){ const y = c.pos[1]; if (!byLayer.has(y)) byLayer.set(y, []); byLayer.get(y).push(c); }
  const ys = [...byLayer.keys()].sort((a,b) => a-b);
  const xs = cells.map(c => c.pos[0]), zs = cells.map(c => c.pos[2]);
  const minx = Math.min(...xs), maxx = Math.max(...xs), minz = Math.min(...zs), maxz = Math.max(...zs);
  let layer = store.get(key + ":layer", ys[0]); if (!byLayer.has(layer)) layer = ys[0];
  let selected = null;
  host.innerHTML = `<div class="toolbar"><span>Layer</span><span class="layerbtns"></span>
    <button data-step="-1">◀</button><button data-step="1">▶</button>
    <label><input type="checkbox" class="donebox"> layer done</label><span class="muted lp"></span></div>
    <div class="viewer"><div class="gridwrap"><svg class="grid"></svg></div><div class="detail panel"></div></div>`;
  const btns = host.querySelector(".layerbtns"), svg = host.querySelector("svg"), detail = host.querySelector(".detail");
  const donebox = host.querySelector(".donebox"), lp = host.querySelector(".lp");
  btns.innerHTML = ys.map(y => `<button data-y="${y}">${y}</button>`).join("");
  btns.addEventListener("click", e => { if (e.target.dataset.y !== undefined){ layer = +e.target.dataset.y; selected = null; draw(); }});
  host.querySelectorAll("[data-step]").forEach(b => b.addEventListener("click", () => {
    const i = ys.indexOf(layer) + (+b.dataset.step); if (i >= 0 && i < ys.length){ layer = ys[i]; selected = null; draw(); }}));
  donebox.addEventListener("change", () => { const d = store.get(key + ":done", {}); d[layer] = donebox.checked; store.set(key + ":done", d); draw(); });
  const S = 34, PAD = 26;
  function cellAt(x, z){ return (byLayer.get(layer) || []).find(c => c.pos[0] === x && c.pos[2] === z); }
  function draw(){
    store.set(key + ":layer", layer);
    btns.querySelectorAll("button").forEach(b => b.classList.toggle("on", +b.dataset.y === layer));
    const done = store.get(key + ":done", {}); donebox.checked = !!done[layer];
    lp.textContent = `${Object.values(done).filter(Boolean).length} of ${ys.length} layers done`;
    const W = (maxx - minx + 1) * S + PAD, H = (maxz - minz + 1) * S + PAD;
    svg.setAttribute("width", W); svg.setAttribute("height", H); svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    let s = "";
    for (let x = minx; x <= maxx; x++) s += `<text x="${PAD + (x-minx)*S + S/2}" y="14" text-anchor="middle" fill="${css("--muted")}">${x}</text>`;
    for (let z = minz; z <= maxz; z++) s += `<text x="10" y="${PAD + (z-minz)*S + S/2 + 4}" text-anchor="middle" fill="${css("--muted")}">${z}</text>`;
    s += `<text x="${W-4}" y="14" text-anchor="end" fill="${css("--muted")}">x →  N ↑</text>`;
    for (let x = minx; x <= maxx; x++) for (let z = minz; z <= maxz; z++){
      const c = cellAt(x, z), X = PAD + (x-minx)*S, Y = PAD + (z-minz)*S;
      s += `<rect x="${X+1}" y="${Y+1}" width="${S-2}" height="${S-2}" rx="4" fill="none" stroke="${css("--line")}"/>`;
      if (!c) continue;
      const sel = selected && selected.pos[0] === x && selected.pos[2] === z;
      if (c.kind === "controller"){
        s += `<rect data-x="${x}" data-z="${z}" x="${X+3}" y="${Y+3}" width="${S-6}" height="${S-6}" rx="3" fill="${css("--ctrl")}" stroke="${sel ? css("--sel") : "none"}" stroke-width="3"/>`;
        continue;
      }
      if (c.kind !== "cable"){
        s += `<rect data-x="${x}" data-z="${z}" x="${X+6}" y="${Y+6}" width="${S-12}" height="${S-12}" rx="3" fill="${css("--muted")}"/>`;
        continue;
      }
      const col = c.cable === "dense" ? css("--dense") : css("--cable");
      s += `<rect data-x="${x}" data-z="${z}" x="${X+8}" y="${Y+8}" width="${S-16}" height="${S-16}" rx="5" fill="${col}" stroke="${sel ? css("--sel") : "none"}" stroke-width="3"/>`;
      const partCol = (p) => p.part === "tunnel" ? css("--tunnel") : p.part === "anchor" ? css("--anchor") : p.part === "exit" ? css("--exit") : css("--output");
      const sd = c.sides || {};
      const bar = {north:[X+8,Y+2,S-16,5], south:[X+8,Y+S-7,S-16,5], west:[X+2,Y+8,5,S-16], east:[X+S-7,Y+8,5,S-16]};
      for (const k of SIDES) if (sd[k]) { const [a,b,w,h] = bar[k]; s += `<rect x="${a}" y="${b}" width="${w}" height="${h}" rx="2" fill="${partCol(sd[k])}"/>`; }
      if (sd.up) s += `<text x="${X+S-5}" y="${Y+13}" text-anchor="end" fill="${partCol(sd.up)}">▲</text>`;
      if (sd.down) s += `<text x="${X+5}" y="${Y+S-4}" fill="${partCol(sd.down)}">▼</text>`;
      const n = Object.values(sd).filter(p => p.part === "tunnel").length;
      if (n) s += `<text x="${X+S/2}" y="${Y+S/2+4}" text-anchor="middle" fill="#fff">${n}</text>`;
    }
    svg.innerHTML = s;
    showDetail();
  }
  function showDetail(){
    if (!selected){ detail.innerHTML = `<h3>Layer ${layer}</h3><p class="muted">Click a cell for its parts and world position.</p>` + layerSummary(); return; }
    const c = selected, w = world(c.pos);
    let h = `<h3>${c.kind === "controller" ? "Controller" : c.kind === "cable" ? (c.cable === "dense" ? "Dense smart cable" : "Smart cable") : c.kind}</h3>
      <div>Cell x ${c.pos[0]}, layer ${c.pos[1]}, z ${c.pos[2]}</div><div><b>World ${fmt(w)}</b></div>`;
    if (c.sides){
      const names = {tunnel:"P2P tunnel (input)", output:"P2P tunnel (output)", anchor:"cable anchor", exit:"exit: leave open", outlet:"outlet: leave open toward the controller", device:"device"};
      h += "<ul>" + ["up","down","north","south","west","east"].filter(k => c.sides[k]).map(k => {
        const p = c.sides[k]; const inp = p.part === "tunnel" ? PLAN.inputs.find(i => i.pos.join() === c.pos.join() && i.side === k) : null;
        return `<li><b>${k}</b>: ${names[p.part] || p.part}${inp ? " (" + inp.label + ")" : ""}</li>`; }).join("") + "</ul>";
      const ex = PLAN.exits.find(e => e.pos.join() === c.pos.join());
      if (ex) h += `<div class="ok">Exit: carries ${ex.channels} tunnel-network channel(s), trunk ${ex.trunk}</div>`;
    }
    detail.innerHTML = h;
  }
  function layerSummary(){
    const cs = byLayer.get(layer) || [];
    const n = (f) => cs.filter(f).length;
    const tunnels = cs.reduce((a, c) => a + Object.values(c.sides || {}).filter(p => p.part === "tunnel").length, 0);
    const anchors = cs.reduce((a, c) => a + Object.values(c.sides || {}).filter(p => p.part === "anchor").length, 0);
    return `<ul><li>${n(c => c.kind === "controller")} controllers</li><li>${n(c => c.kind === "cable" && c.cable !== "dense")} smart cable cells</li>` +
      `${n(c => c.cable === "dense") ? `<li>${n(c => c.cable === "dense")} dense cable cells</li>` : ""}<li>${tunnels} tunnels, ${anchors} anchors</li></ul>`;
  }
  svg.addEventListener("click", e => {
    const t = e.target.closest("[data-x]"); if (!t) return;
    selected = cellAt(+t.dataset.x, +t.dataset.z); draw();
  });
  draw();
  return { refresh: draw };
}

// ------------------------------------------------------------------ inputs
const linked = new Set(store.get("linked", []));
const layers = [...new Set(PLAN.inputs.map(i => i.pos[1]))].sort((a,b) => a-b);
$("inlayer").innerHTML += layers.map(y => `<option value="${y}">${y}</option>`).join("");
$("inlayer").addEventListener("change", drawInputs);
function drawInputs(){
  const f = $("inlayer").value;
  const rows = PLAN.inputs.filter(i => f === "" || i.pos[1] === +f);
  $("inputs").innerHTML = "<tr><th></th><th>Input</th><th>Layer</th><th>Cell (x, z)</th><th>Side</th><th>World</th></tr>" +
    rows.map(i => `<tr class="${linked.has(i.label) ? "done" : ""}"><td><input type="checkbox" data-l="${i.label}" ${linked.has(i.label) ? "checked" : ""}></td>
      <td>${i.label}</td><td>${i.pos[1]}</td><td>${i.pos[0]}, ${i.pos[2]}</td><td>${i.side}</td><td>${fmt(world(i.pos))}</td></tr>`).join("");
  $("inprogress").textContent = `${linked.size} of ${PLAN.inputs.length} linked`;
}
$("inputs").addEventListener("change", e => {
  const l = e.target.dataset.l; if (!l) return;
  e.target.checked ? linked.add(l) : linked.delete(l); store.set("linked", [...linked]); drawInputs();
});

// ------------------------------------------------------------------ tunnel network
function drawTN(){
  let h = `<p>Every input tunnel and every output tunnel needs <b>one tunnel-network channel</b>:
    ${tn.inputs.toLocaleString()} inputs + ${tn.outputs.toLocaleString()} outputs = <b>${tn.channels.toLocaleString()} channels</b>.
    The tunnel network is a <b>separate</b> AE2 network with its own controller and power. It must never touch the main controllers.</p>
    <ul><li><b>${tn.input_trunks}</b> dense trunk(s) collect the build's ${PLAN.exits.length} exits (each trunk ≤ ${pr.dense} channels).</li>
    <li><b>${tn.output_trunks}</b> dense trunk(s) feed the outputs (${pr.dense} outputs per trunk).</li>
    <li>That's <b>${tn.outlets}</b> outlets of ${pr.dense} channels on the tunnel network's controller.</li></ul>`;
  if (PLAN.trunks.length){
    h += `<details><summary>Exit grouping (${PLAN.exits.length} exits → ${PLAN.trunks.length} trunks)</summary><table>
      <tr><th>Trunk</th><th>Channels</th><th>Exits (x, layer, z)</th></tr>` +
      PLAN.trunks.map(t => `<tr><td>${t.label}</td><td>${t.channels}</td><td>${t.exits.map(fmt).map(s => "(" + s + ")").join(" ")}</td></tr>`).join("") +
      `</table><p class="muted">Connect each exit's open side with smart or dense cable to its trunk. Exits in different trunks
      must not touch: use different cable colours or cable anchors. Each exit cable carries at most ${pr.normal}.</p></details>`;
  }
  const tc = PLAN.tunnel_controller;
  if (tc){
    h += `<h3>Suggested tunnel-network controller</h3>
      <p>${tc.bom.controllers} controllers in a ${tc.size.join(" × ")} block area give <b>${tc.outlets} outlets</b>
      (${tc.channels.toLocaleString()} channels; ${tc.needed} needed). Each outlet is a dense smart cable on one controller face,
      with cable anchors on every other controller face it touches. Every outlet has its own route out to an <span class="ok">exit</span>.
      Connect each exit to one trunk. Power this controller separately.</p>
      <p class="muted">Bill of materials: ${Object.entries(tc.bom).filter(([k,v]) => v).map(([k,v]) => `${v} × ${BOM_LABELS[k]||k}`).join(", ")}.</p>`;
  }
  $("tn").innerHTML = h;
}

const viewers = [makeViewer($("viewer-main"), PLAN.cells, PLAN.size, "main")];
if (PLAN.tunnel_controller){
  $("viewer-tc").className = "panel";
  viewers.push(makeViewer($("viewer-tc"), PLAN.tunnel_controller.cells, PLAN.tunnel_controller.size, "tc"));
}
function refresh(){ viewers.forEach(v => v.refresh()); drawInputs(); }
drawTN(); drawInputs();
</script>
</body>
</html>
"""
