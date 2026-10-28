// Turns a layout (+ the server's analysis) into viewer boxes, and describes cells for tooltips.

export const DIRS = {
  down: [0, -1, 0], up: [0, 1, 0], north: [0, 0, -1], south: [0, 0, 1], west: [-1, 0, 0], east: [1, 0, 0],
};
export const OPPOSITE = { down: "up", up: "down", north: "south", south: "north", west: "east", east: "west" };

export const DYES = {
  white: "#f0f0f0", orange: "#f9801d", magenta: "#c74ebd", light_blue: "#3ab3da", yellow: "#fed83d",
  lime: "#80c71f", pink: "#f38baa", gray: "#5a6266", light_gray: "#9d9d97", cyan: "#169c9c",
  purple: "#8932b8", blue: "#3c44aa", brown: "#835432", green: "#5e7c16", red: "#b02e26",
  black: "#26262b", transparent: "#a985ef",
};
export const DYE_NAMES = Object.fromEntries(Object.keys(DYES).map(k => [k,
  k === "transparent" ? "Fluix" : k.split("_").map(w => w[0].toUpperCase() + w.slice(1)).join(" ")]));

const MARKERS = {
  "minecraft:gold_block": "#e8c33c", "minecraft:lapis_block": "#2f5bc8",
  "minecraft:emerald_block": "#2fbf71", "minecraft:diamond_block": "#5fe0d8",
};

export function hex(c, a = 1) {
  const n = parseInt(c.slice(1), 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255, a];
}
export const key = p => p.join(",");
export const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

const STATE_GLOW = {
  online: hex("#4de3ff"), missing: hex("#ff5d5d"), offline: hex("#3a3f48"),
};
const CONTROLLER_GLOW = {
  ONLINE: hex("#4de3ff"), CONFLICT: hex("#ff4d4d"), AD_HOC: hex("#ffd54d"),
  AD_HOC_OVERLOADED: hex("#ff9a3d"), CHANNELS_DISABLED: hex("#9aa3b3"),
};

export function heat(used, cap) {
  if (!cap) return hex("#3a3f48");
  const f = Math.min(1, used / cap);
  if (used === 0) return hex("#33405a");
  // teal -> yellow -> red
  const stops = [[0, [0.15, 0.75, 0.75]], [0.6, [0.98, 0.84, 0.25]], [1, [1, 0.3, 0.3]]];
  for (let i = 1; i < stops.length; i++) if (f <= stops[i][0]) {
    const [a, ca] = stops[i - 1], [b, cb] = stops[i], t = (f - a) / (b - a);
    return [0, 1, 2].map(j => ca[j] + (cb[j] - ca[j]) * t).concat(1);
  }
  return [1, 0.3, 0.3, 1];
}

// A box from `from` to `to` (distances from the cell centre, 0..0.5) toward direction d,
// `half` wide across.
function arm(pos, d, from, to, half) {
  const v = DIRS[d], c = pos.map(x => x + 0.5), min = [], max = [];
  for (let i = 0; i < 3; i++) {
    if (v[i] === 0) { min.push(c[i] - half); max.push(c[i] + half); }
    else if (v[i] > 0) { min.push(c[i] + from); max.push(c[i] + to); }
    else { min.push(c[i] - to); max.push(c[i] - from); }
  }
  return { min, max };
}
const cube = (pos, h = 0.5) => ({ min: pos.map(x => x + 0.5 - h), max: pos.map(x => x + 0.5 + h) });

// Which sides a cable connects on, guessed locally (used until the server's analysis arrives).
function guessSides(blocks, b) {
  const out = [];
  for (const [d, v] of Object.entries(DIRS)) {
    if (b.parts?.[d]) continue;
    const n = blocks.get(key(add(b.pos, v)));
    if (!n) continue;
    if (n.type === "controller" || n.type === "energy_acceptor" || n.type === "port" || n.type === "device_block") out.push(d);
    else if (n.type === "cable" && n.cable !== "none" && !n.parts?.[OPPOSITE[d]]) {
      const a = b.color || "transparent", c = n.color || "transparent";
      if (a === c || a === "transparent" || c === "transparent") out.push(d);
    } else if (n.type === "cable" && n.parts?.[OPPOSITE[d]]?.type === "p2p_me") out.push(d);
  }
  return out;
}

/**
 * opts: {analysis, slice (max y shown or null), xray, heat, dense (capacity)}
 */
export function buildItems(blocks, opts = {}) {
  const items = [];
  const cells = opts.analysis?.cells || {};
  for (const b of blocks.values()) {
    // A connection above the build belongs to its top layer (`sliceTop`); one below, to the bottom.
    const y = b.type === "port" && opts.sliceTop != null ? Math.min(b.pos[1], opts.sliceTop) : b.pos[1];
    if (opts.slice != null && y > opts.slice) continue;
    const k = key(b.pos), info = cells[k] || {};
    const owner = { pos: b.pos, key: k };
    const push = (box, color, glow, extra = {}) =>
      items.push({ ...box, color, glow, pick: extra.pick, owner: { ...owner, ...(extra.owner || {}) } });
    switch (b.type) {
      case "controller": {
        const valid = info.valid !== false;
        const glow = !valid ? [0.4, 0.42, 0.46, 1] : (CONTROLLER_GLOW[info.state] || hex("#6b7380"));
        const base = opts.xray ? [0.36, 0.42, 0.52, 0.13] : hex(valid ? "#373e4a" : "#2a2d33");
        push(cube(b.pos), base, opts.xray ? [0, 0, 0, 0] : [...glow.slice(0, 3), 1], { pick: !opts.xray });
        break;
      }
      case "cable": {
        const dense = b.cable === "dense";
        const half = dense ? 0.24 : 0.15;
        let color = hex(DYES[b.color || "transparent"]);
        if (opts.heat && b.cable !== "none") color = heat(info.used || 0, info.capacity || 0);
        const sides = info.sides || guessSides(blocks, b);
        if (b.cable !== "none") {
          push(cube(b.pos, half), color, dense ? [...color.slice(0, 3), 0] : [0, 0, 0, 0]);
          for (const d of sides) push(arm(b.pos, d, half, 0.5, half * 0.82), color, [0, 0, 0, 0]);
        }
        for (const [d, part] of Object.entries(b.parts || {})) {
          const pinfo = info.parts?.[d] || {};
          const pOwner = { owner: { side: d, part: true } };
          if (part.type === "p2p_me") {
            const plate = part.output ? hex("#8a6a2e") : hex("#4a5262");
            if (b.cable !== "none") push(arm(b.pos, d, half, 0.38, 0.16), hex("#2e333d"), [0, 0, 0, 0], pOwner);
            // The status light is a lit frame on the plate, visible from the cable side.
            const glow = STATE_GLOW[pinfo.state] || STATE_GLOW.offline;
            push(arm(b.pos, d, 0.38, 0.5, 0.36), plate, [...glow.slice(0, 3), 1], pOwner);
          } else if (part.type === "anchor") {
            push(arm(b.pos, d, half, 0.5, 0.07), hex("#8f96a3"), [0, 0, 0, 0], pOwner);
          } else {
            push(arm(b.pos, d, half, 0.38, 0.12), hex("#2e333d"), [0, 0, 0, 0], pOwner);
            push(arm(b.pos, d, 0.38, 0.5, 0.36), hex("#39414d"), [0, 0, 0, 0], pOwner);
            const on = pinfo.state === "online";
            push(arm(b.pos, d, 0.5, 0.505, 0.27), on ? hex("#4dff9a") : hex("#20302a"), [0.3, 1, 0.6, on ? 2 : 0], { ...pOwner, pick: false });
          }
        }
        break;
      }
      case "energy_acceptor":
        push(cube(b.pos), hex("#5b606b"), [0.98, 0.84, 0.3, 3]);
        break;
      case "port":
        push(cube(b.pos, 0.13), [0.3, 0.9, 0.6, 0.5], [0, 0, 0, 0]);
        break;
      case "device_block":
        push(cube(b.pos), hex("#646b78"), [...(info.online ? hex("#4dff9a") : hex("#ff5d5d")).slice(0, 3), 1]);
        break;
      case "marker":
        push(cube(b.pos), hex(MARKERS[b.block] || "#9aa0a6"), [0, 0, 0, 0]);
        break;
    }
  }
  return items;
}

// --------------------------------------------------------------------------- tooltips

const STATE_TEXT = {
  ONLINE: ["Online", "ok"], CONFLICT: ["Controller conflict", "bad"], AD_HOC: ["No controller (ad-hoc)", "warn"],
  AD_HOC_OVERLOADED: ["Ad-hoc, overloaded", "bad"], CHANNELS_DISABLED: ["Channels off", "warn"],
};
const PART_STATE = { online: ["Device Online", "ok"], missing: ["Device Missing Channel", "bad"], offline: ["Device Offline", "warn"] };
const cap = s => s[0].toUpperCase() + s.slice(1);

export function describe(b, info, side, origin, dense = 32) {
  const rel = b.pos.map((v, i) => v - origin[i]);
  const where = `x ${rel[0]} · y ${rel[1]} · z ${rel[2]}`;
  const rows = [];
  let title = "", badge = null, color = null;
  const st = info?.state && STATE_TEXT[info.state];
  if (side && b.parts?.[side]) {
    const p = b.parts[side], pi = info?.parts?.[side] || {};
    if (p.type === "p2p_me") {
      title = `ME P2P tunnel · ${p.output ? "output" : "input"}`;
      badge = PART_STATE[pi.state];
      rows.push(["Frequency", p.freq], ["Faces", cap(side)]);
      if (pi.faces_controller && !p.output) rows.push(["Feeds", "a controller face (32-channel link)"]);
    } else if (p.type === "anchor") {
      title = "Cable anchor"; rows.push(["Blocks", `${cap(side)} side of the cable`]);
    } else {
      title = "Device part"; badge = PART_STATE[pi.state]; rows.push(["Faces", cap(side)]);
    }
    return { title, badge, rows, where };
  }
  switch (b.type) {
    case "controller": {
      title = "ME Controller";
      badge = info?.valid === false ? ["Disabled (cross rule)", "bad"] : st;
      if (info?.tunnels != null) {
        rows.push(["Tunnels facing it", `${info.tunnels} of ${info.open_faces} open face${info.open_faces === 1 ? "" : "s"}`]);
        if (info.tunnels) rows.push(["Brings in", `${info.tunnels_online} × ${dense} channels`]);
      }
      const used = Object.entries(info?.faces || {}).filter(([, n]) => n > 0);
      if (used.length) rows.push(["Channels out", used.map(([d, n]) => `${cap(d)} ${n}`).join(" · ")]);
      if (b.powered) rows.push(["Power", "fed directly"]);
      break;
    }
    case "cable": {
      const dense = b.cable === "dense";
      title = `${dense ? "Dense smart cable" : b.cable === "none" ? "Cable bus (no cable)" : "Smart cable"}`;
      color = DYES[b.color || "transparent"];
      rows.push(["Colour", DYE_NAMES[b.color || "transparent"]]);
      if (info?.capacity != null) rows.push(["Channels", { used: info.used, capacity: info.capacity }]);
      if (info?.sides) rows.push(["Connects", info.sides.length ? info.sides.map(cap).join(", ") : "nothing"]);
      const n = Object.values(b.parts || {}).filter(p => p.type === "p2p_me").length;
      if (n) rows.push(["Tunnels", n]);
      badge = st;
      break;
    }
    case "energy_acceptor": title = "Energy acceptor / power"; badge = st; break;
    case "port": title = "Tunnel-network connection";
      rows.push(["What", "stand-in for your tunnel network (not a real block)"]); break;
    case "device_block": title = "ME device"; badge = info?.online ? ["Online", "ok"] : ["No channel", "bad"]; break;
    case "marker": title = cap((b.block || "").replace("minecraft:", "").replace(/_/g, " ")); rows.push(["What", "test marker"]); break;
  }
  return { title, badge, rows, where, color };
}
