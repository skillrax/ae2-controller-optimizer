// A small WebGL2 voxel viewer: instanced boxes, orbit camera, ray picking.
// No dependencies, so the app works offline.
//
// Scene items are boxes: {min:[x,y,z], max:[x,y,z], color:[r,g,b,a], glow:[r,g,b,pattern],
// owner:any, pick:bool}. Patterns: 0 plain, 1 inset frame (controllers), 2 emissive
// (tunnel lights), 3 stripes (energy). Coordinates are Minecraft's: x east, y up, z south.

const BOX_VS = `#version 300 es
in vec3 a_pos; in vec3 a_nrm;
in vec3 i_min; in vec3 i_size; in vec4 i_color; in vec4 i_glow;
uniform mat4 u_vp;
out vec3 v_nrm; out vec3 v_local; out vec3 v_size; out vec4 v_color; out vec4 v_glow; out vec3 v_world;
void main() {
  vec3 w = i_min + a_pos * i_size;
  v_world = w; v_local = a_pos; v_size = i_size; v_nrm = a_nrm; v_color = i_color; v_glow = i_glow;
  gl_Position = u_vp * vec4(w, 1.0);
}`;

const BOX_FS = `#version 300 es
precision highp float;
in vec3 v_nrm; in vec3 v_local; in vec3 v_size; in vec4 v_color; in vec4 v_glow; in vec3 v_world;
uniform vec3 u_eye;
out vec4 o;
void main() {
  vec3 n = normalize(v_nrm);
  vec3 l = v_local * v_size;
  vec3 d = min(l, v_size - l);
  vec3 an = abs(n);
  vec2 f = an.x > 0.5 ? vec2(d.y, d.z) : (an.y > 0.5 ? vec2(d.x, d.z) : vec2(d.x, d.y));
  vec2 fs = an.x > 0.5 ? v_size.yz : (an.y > 0.5 ? v_size.xz : v_size.xy);
  float e = min(f.x, f.y);
  // Minecraft-style face shading, plus a soft key light so shapes read in 3D.
  float shade = n.y > 0.5 ? 1.0 : (n.y < -0.5 ? 0.52 : (an.x > 0.5 ? 0.80 : 0.66));
  vec3 L = normalize(vec3(0.45, 0.85, 0.3));
  shade *= 0.82 + 0.18 * max(dot(n, L), 0.0);
  vec3 col = v_color.rgb * shade;
  float ew = clamp(0.06 * min(fs.x, fs.y), 0.012, 0.045);
  col *= 1.0 - 0.38 * (1.0 - smoothstep(0.0, ew, e));
  float p = v_glow.a;
  if (p > 0.5 && p < 1.5) {
    // inset frame + centre cross, like a controller's light strips
    float inset = 0.16 * min(fs.x, fs.y);
    float frame = 1.0 - smoothstep(0.0, 0.035, abs(e - inset));
    vec2 c = abs(f - fs * 0.5);
    float cross = (c.x < 0.03 || c.y < 0.03) && e > inset ? 0.55 : 0.0;
    col = mix(col, v_glow.rgb, max(frame, cross));
  } else if (p > 1.5 && p < 2.5) {
    col = mix(v_glow.rgb, v_glow.rgb * shade, 0.35);
  } else if (p > 2.5) {
    float s = step(0.5, fract((v_world.x + v_world.y + v_world.z) * 4.0));
    col = mix(col, v_glow.rgb * shade, 0.35 * s * smoothstep(0.0, 0.08, e));
  }
  o = vec4(col, v_color.a);
}`;

const LINE_VS = `#version 300 es
in vec3 a_pos; in vec4 a_col; uniform mat4 u_vp; out vec4 v_col;
void main() { v_col = a_col; gl_Position = u_vp * vec4(a_pos, 1.0); }`;
const LINE_FS = `#version 300 es
precision highp float; in vec4 v_col; uniform float u_alpha; out vec4 o;
void main() { o = vec4(v_col.rgb, v_col.a * u_alpha); }`;

// ------------------------------------------------------------------ math
const V = {
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  scale: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  norm: a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
};

function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}
function lookAt(eye, target, up) {
  const z = V.norm(V.sub(eye, target)), x = V.norm(V.cross(up, z)), y = V.cross(z, x);
  return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
    -V.dot(x, eye), -V.dot(y, eye), -V.dot(z, eye), 1];
}
function mul(a, b) {
  const o = new Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s;
  }
  return o;
}

// Unit cube: 6 faces, 2 triangles each, position + normal.
function cubeMesh() {
  const faces = [
    [[1, 0, 0], [[1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]]],
    [[-1, 0, 0], [[0, 0, 1], [0, 1, 1], [0, 1, 0], [0, 0, 0]]],
    [[0, 1, 0], [[0, 1, 1], [1, 1, 1], [1, 1, 0], [0, 1, 0]]],
    [[0, -1, 0], [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]]],
    [[0, 0, 1], [[1, 0, 1], [1, 1, 1], [0, 1, 1], [0, 0, 1]]],
    [[0, 0, -1], [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]]],
  ];
  const out = [];
  for (const [n, q] of faces) for (const i of [0, 1, 2, 0, 2, 3]) out.push(...q[i], ...n);
  return new Float32Array(out);
}

function compile(gl, vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  return p;
}

// ------------------------------------------------------------------ viewer
export class Viewer {
  constructor(canvas, { interactive = true, preserve = false } = {}) {
    this.canvas = canvas;
    const gl = canvas.getContext("webgl2", { antialias: true, alpha: true, preserveDrawingBuffer: preserve });
    if (!gl) throw new Error("WebGL2 is not available in this browser");
    this.gl = gl;
    this.boxProg = compile(gl, BOX_VS, BOX_FS);
    this.lineProg = compile(gl, LINE_VS, LINE_FS);
    this.items = [];
    this.lines = { grid: [], outline: [], marks: [] };
    this.hover = null;
    this.cam = { target: [0, 0, 0], yaw: -0.75, pitch: 0.55, dist: 12 };
    this.goal = null;
    this.bounds = { min: [0, 0, 0], max: [1, 1, 1] };
    this.listeners = {};
    this._initBuffers();
    this.resizeObserver = new ResizeObserver(() => this.requestDraw());
    this.resizeObserver.observe(canvas);
    if (interactive) this._initInput();
    this.requestDraw();
  }

  on(event, fn) { (this.listeners[event] ||= []).push(fn); }
  emit(event, ...args) { for (const fn of this.listeners[event] || []) fn(...args); }

  _initBuffers() {
    const gl = this.gl;
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    const mesh = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, mesh);
    gl.bufferData(gl.ARRAY_BUFFER, cubeMesh(), gl.STATIC_DRAW);
    const loc = n => gl.getAttribLocation(this.boxProg, n);
    gl.enableVertexAttribArray(loc("a_pos")); gl.vertexAttribPointer(loc("a_pos"), 3, gl.FLOAT, false, 24, 0);
    gl.enableVertexAttribArray(loc("a_nrm")); gl.vertexAttribPointer(loc("a_nrm"), 3, gl.FLOAT, false, 24, 12);
    this.inst = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.inst);
    const attrs = [["i_min", 3, 0], ["i_size", 3, 12], ["i_color", 4, 24], ["i_glow", 4, 40]];
    for (const [n, size, off] of attrs) {
      const l = loc(n);
      gl.enableVertexAttribArray(l);
      gl.vertexAttribPointer(l, size, gl.FLOAT, false, 56, off);
      gl.vertexAttribDivisor(l, 1);
    }
    gl.bindVertexArray(null);
    this.lineVao = gl.createVertexArray();
    gl.bindVertexArray(this.lineVao);
    this.lineBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.lineBuf);
    const ll = n => gl.getAttribLocation(this.lineProg, n);
    gl.enableVertexAttribArray(ll("a_pos")); gl.vertexAttribPointer(ll("a_pos"), 3, gl.FLOAT, false, 28, 0);
    gl.enableVertexAttribArray(ll("a_col")); gl.vertexAttribPointer(ll("a_col"), 4, gl.FLOAT, false, 28, 12);
    gl.bindVertexArray(null);
  }

  // ---------------------------------------------------------------- scene
  setItems(items, { refit = false } = {}) {
    this.items = items;
    this.opaque = items.filter(it => it.color[3] >= 0.999);
    this.clear = items.filter(it => it.color[3] < 0.999);
    const solid = items.filter(it => !it.ghost);
    if (solid.length) {
      const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
      for (const it of solid) for (let i = 0; i < 3; i++) {
        min[i] = Math.min(min[i], it.min[i]); max[i] = Math.max(max[i], it.max[i]);
      }
      this.bounds = { min, max };
    }
    this._makeGrid();
    if (refit) this.fit(true);
    this.requestDraw();
  }

  _makeGrid() {
    const { min, max } = this.bounds, y = min[1] - 0.002, pad = 3;
    const x0 = Math.floor(min[0]) - pad, x1 = Math.ceil(max[0]) + pad;
    const z0 = Math.floor(min[2]) - pad, z1 = Math.ceil(max[2]) + pad;
    const g = [], c = [0.55, 0.62, 0.75, 0.13];
    for (let x = x0; x <= x1; x++) g.push([[x, y, z0], [x, y, z1], c]);
    for (let z = z0; z <= z1; z++) g.push([[x0, y, z], [x1, y, z], c]);
    // north marker: a short bright line pointing to -z
    const cx = (min[0] + max[0]) / 2;
    g.push([[cx, y, z0], [cx, y, z0 - 1.5], [0.95, 0.45, 0.45, 0.9]]);
    this.lines.grid = g;
    this.north = [cx, y, z0 - 2.1];
  }

  setHover(item) { if (item !== this.hover) { this.hover = item; this._outline(); this.requestDraw(); } }

  setMarks(boxes, color = [1, 0.35, 0.35, 1]) {
    this.lines.marks = boxes.flatMap(b => boxLines(b.min, b.max, color, 0.02));
    this.requestDraw();
  }

  _outline() {
    const h = this.hover;
    this.lines.outline = h ? boxLines(h.min, h.max, h.outline || [1, 1, 1, 1], 0.012) : [];
  }

  fit(animate = false) {
    const { min, max } = this.bounds;
    const center = V.scale(V.add(min, max), 0.5);
    const radius = Math.max(1.5, Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2);
    const dist = radius / Math.sin(0.4) * 1.05;
    const goal = { target: center, dist, yaw: this.cam.yaw, pitch: this.cam.pitch };
    if (animate) this.goal = goal; else Object.assign(this.cam, goal);
    this.requestDraw();
  }

  view(name) {
    const views = { iso: [-0.75, 0.55], top: [this.cam.yaw, 1.53], front: [0, 0.05], side: [Math.PI / 2, 0.05] };
    const [yaw, pitch] = views[name] || views.iso;
    this.goal = { ...this.cam, ...(this.goal || {}), yaw, pitch };
    this.requestDraw();
  }

  // ---------------------------------------------------------------- camera
  eye() {
    const c = this.cam, cp = Math.cos(c.pitch);
    return [c.target[0] + c.dist * cp * Math.sin(c.yaw), c.target[1] + c.dist * Math.sin(c.pitch),
      c.target[2] + c.dist * cp * Math.cos(c.yaw)];
  }

  matrices() {
    const w = this.canvas.clientWidth || 1, h = this.canvas.clientHeight || 1;
    const proj = perspective(0.8, w / h, 0.05, 500);
    const view = lookAt(this.eye(), this.cam.target, [0, 1, 0]);
    return { vp: mul(proj, view), w, h };
  }

  ray(px, py) {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const eye = this.eye();
    const fwd = V.norm(V.sub(this.cam.target, eye));
    const right = V.norm(V.cross(fwd, [0, 1, 0]));
    const up = V.cross(right, fwd);
    const t = Math.tan(0.4), nx = (px / w * 2 - 1) * t * w / h, ny = (1 - py / h * 2) * t;
    return { o: eye, d: V.norm(V.add(fwd, V.add(V.scale(right, nx), V.scale(up, ny)))) };
  }

  project(p) {
    const { vp, w, h } = this.matrices();
    const x = vp[0] * p[0] + vp[4] * p[1] + vp[8] * p[2] + vp[12];
    const y = vp[1] * p[0] + vp[5] * p[1] + vp[9] * p[2] + vp[13];
    const ww = vp[3] * p[0] + vp[7] * p[1] + vp[11] * p[2] + vp[15];
    if (ww <= 0) return null;
    return [(x / ww + 1) / 2 * w, (1 - y / ww) / 2 * h];
  }

  pick(px, py) {
    const { o, d } = this.ray(px, py);
    let best = null;
    for (const it of this.items) {
      if (it.pick === false || it.ghost) continue;
      const hit = rayBox(o, d, it.min, it.max);
      if (hit && (!best || hit.t < best.t)) best = { ...hit, item: it };
    }
    if (best) best.point = V.add(o, V.scale(d, best.t));
    return best;
  }

  groundPick(px, py, y) {
    const { o, d } = this.ray(px, py);
    if (Math.abs(d[1]) < 1e-6) return null;
    const t = (y - o[1]) / d[1];
    return t > 0 ? V.add(o, V.scale(d, t)) : null;
  }

  _initInput() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener("contextmenu", e => e.preventDefault());
    c.addEventListener("pointerdown", e => {
      c.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, button: e.button, moved: false, shift: e.shiftKey };
    });
    c.addEventListener("pointermove", e => {
      const r = c.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) > 4) drag.moved = true;
        if (drag.moved) {
          this.goal = null;
          const pan = drag.button === 1 || drag.button === 2 || drag.shift;
          if (pan) this._pan(dx, dy); else {
            this.cam.yaw -= dx * 0.008;
            this.cam.pitch = Math.max(-1.45, Math.min(1.53, this.cam.pitch + dy * 0.008));
          }
          drag.x = e.clientX; drag.y = e.clientY;
          this.requestDraw();
          this.emit("hover", null, e);
          return;
        }
      }
      this.emit("hover", this.pick(px, py), e, px, py);
    });
    c.addEventListener("pointerup", e => {
      const r = c.getBoundingClientRect();
      if (drag && !drag.moved) this.emit("click", this.pick(e.clientX - r.left, e.clientY - r.top), e,
        e.clientX - r.left, e.clientY - r.top);
      drag = null;
    });
    c.addEventListener("pointerleave", e => { if (!drag) this.emit("hover", null, e); });
    c.addEventListener("wheel", e => {
      e.preventDefault();
      this.goal = null;
      this.cam.dist = Math.max(1.5, Math.min(300, this.cam.dist * Math.exp(e.deltaY * 0.0012)));
      this.requestDraw();
    }, { passive: false });
    c.addEventListener("dblclick", () => this.fit(true));
  }

  _pan(dx, dy) {
    const eye = this.eye();
    const fwd = V.norm(V.sub(this.cam.target, eye));
    const right = V.norm(V.cross(fwd, [0, 1, 0]));
    const up = V.cross(right, fwd);
    const s = this.cam.dist * 0.0016;
    this.cam.target = V.add(this.cam.target, V.add(V.scale(right, -dx * s), V.scale(up, dy * s)));
  }

  // ---------------------------------------------------------------- drawing
  requestDraw() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.draw(); });
  }

  _instanceData(items) {
    const a = new Float32Array(items.length * 14);
    items.forEach((it, i) => {
      const o = i * 14;
      a.set(it.min, o);
      a[o + 3] = it.max[0] - it.min[0]; a[o + 4] = it.max[1] - it.min[1]; a[o + 5] = it.max[2] - it.min[2];
      a.set(it.color, o + 6);
      a.set(it.glow || [0, 0, 0, 0], o + 10);
    });
    return a;
  }

  draw() {
    const gl = this.gl, c = this.canvas;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = Math.round(c.clientWidth * dpr), H = Math.round(c.clientHeight * dpr);
    if (!W || !H) return;
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    if (this.goal) {
      const g = this.goal, k = 0.2;
      let dyaw = g.yaw - this.cam.yaw;
      dyaw = Math.atan2(Math.sin(dyaw), Math.cos(dyaw));
      this.cam.yaw += dyaw * k;
      this.cam.pitch += (g.pitch - this.cam.pitch) * k;
      this.cam.dist += (g.dist - this.cam.dist) * k;
      this.cam.target = V.add(this.cam.target, V.scale(V.sub(g.target, this.cam.target), k));
      if (Math.abs(dyaw) < 1e-3 && Math.abs(g.dist - this.cam.dist) < 1e-3) this.goal = null;
      this.requestDraw();
    }
    gl.viewport(0, 0, W, H);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.CULL_FACE);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    const { vp } = this.matrices();
    const eye = this.eye();

    this._lines(this.lines.grid, vp, 1, true);

    gl.useProgram(this.boxProg);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.boxProg, "u_vp"), false, vp);
    gl.uniform3fv(gl.getUniformLocation(this.boxProg, "u_eye"), eye);
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.inst);
    if (this.opaque?.length) {
      gl.bufferData(gl.ARRAY_BUFFER, this._instanceData(this.opaque), gl.DYNAMIC_DRAW);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, this.opaque.length);
    }
    if (this.clear?.length) {
      const dist = it => {
        const m = V.scale(V.add(it.min, it.max), 0.5);
        return V.dot(V.sub(m, eye), V.sub(m, eye));
      };
      const sorted = [...this.clear].sort((a, b) => dist(b) - dist(a));
      gl.depthMask(false);
      gl.bufferData(gl.ARRAY_BUFFER, this._instanceData(sorted), gl.DYNAMIC_DRAW);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, sorted.length);
      gl.depthMask(true);
    }
    gl.bindVertexArray(null);

    // Marks and the hover outline show through blocks faintly, and fully where visible.
    for (const set of [this.lines.marks, this.lines.outline]) {
      if (!set.length) continue;
      gl.disable(gl.DEPTH_TEST);
      this._lines(set, vp, 0.35, false);
      gl.enable(gl.DEPTH_TEST);
      this._lines(set, vp, 1, false);
    }
    this.emit("drawn");
  }

  _lines(segs, vp, alpha, depthWrite) {
    if (!segs.length) return;
    const gl = this.gl;
    const a = new Float32Array(segs.length * 14);
    segs.forEach(([p, q, c], i) => { a.set(p, i * 14); a.set(c, i * 14 + 3); a.set(q, i * 14 + 7); a.set(c, i * 14 + 10); });
    gl.useProgram(this.lineProg);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.lineProg, "u_vp"), false, vp);
    gl.uniform1f(gl.getUniformLocation(this.lineProg, "u_alpha"), alpha);
    gl.bindVertexArray(this.lineVao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.lineBuf);
    gl.bufferData(gl.ARRAY_BUFFER, a, gl.DYNAMIC_DRAW);
    gl.depthMask(depthWrite);
    gl.drawArrays(gl.LINES, 0, segs.length * 2);
    gl.depthMask(true);
    gl.bindVertexArray(null);
  }

  snapshot(width, height) {
    // Renders the current scene into the canvas at a fixed size and returns a data URL.
    const c = this.canvas;
    c.style.width = width + "px"; c.style.height = height + "px";
    this.draw();
    return c.toDataURL("image/png");
  }
}

function boxLines(min, max, color, pad) {
  const a = min.map(v => v - pad), b = max.map(v => v + pad);
  const p = (i, j, k) => [i ? b[0] : a[0], j ? b[1] : a[1], k ? b[2] : a[2]];
  const out = [];
  for (const [i, j] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
    out.push([p(0, i, j), p(1, i, j), color], [p(i, 0, j), p(i, 1, j), color], [p(i, j, 0), p(i, j, 1), color]);
  }
  return out;
}

function rayBox(o, d, min, max) {
  let t0 = -Infinity, t1 = Infinity, axis = 0, sign = 0;
  for (let i = 0; i < 3; i++) {
    if (Math.abs(d[i]) < 1e-12) {
      if (o[i] < min[i] || o[i] > max[i]) return null;
      continue;
    }
    let a = (min[i] - o[i]) / d[i], b = (max[i] - o[i]) / d[i], s = -1;
    if (a > b) { [a, b] = [b, a]; s = 1; }
    if (a > t0) { t0 = a; axis = i; sign = s; }
    if (b < t1) t1 = b;
    if (t0 > t1) return null;
  }
  if (t1 < 0) return null;
  const normal = [0, 0, 0];
  normal[axis] = sign;
  return { t: Math.max(t0, 0), normal };
}
