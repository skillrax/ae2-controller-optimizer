"""HTTP API behind the GUI page. Standard library only, bound to 127.0.0.1.

The page talks JSON to these endpoints:

  GET  /api/state                 profiles, limits, examples, runs
  POST /api/check                 {layout, profile} -> rules report + per-cell details
  POST /api/profiles              {profile} save          POST /api/profiles/delete {name}
  POST /api/import-cfg            {text, name} -> profile + per-field status
  GET  /api/layout?src=...        example:<file> | run:<id>
  POST /api/export                {layout, profile, rig} -> .nbt bytes
  POST /api/guide                 {layout, profile, title, tunnel_controller} -> HTML
  GET  /api/runs                  run list            GET /api/runs/<id>   live state + history
  POST /api/runs                  {profile, settings} -> start a run
  POST /api/runs/<id>/<action>    pause | resume | stop | continue

Optimizer runs use the same run folders and worker process as `ae2opt run`,
so a run started here can be resumed from the command line and the reverse.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import runner
from ..cfgimport import import_text
from ..geometry import Direction
from ..layout import CableBus, Controller, Layout, LayoutError, Marker, P2PTunnel
from ..profile import DEFAULT_PROFILE, KNOWN_CAPACITY_MAX, KNOWN_SIZE_MAX, Profile, ProfileError, ProfileStore
from ..rules import check

STATIC = Path(__file__).with_name("static")
REPO_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "ae2opt"


# --------------------------------------------------------------------------- layout analysis

def _key(pos) -> str:
    return ",".join(map(str, pos))


def analyze(layout: Layout, profile: Profile) -> dict:
    """The rules report plus what the 3D view shows for each cell (tooltips, colours)."""
    report = check(layout, profile)
    sim = report.sim
    g = sim.graph
    tunnels = {t.key: t for t in sim.tunnels()}
    face_use = sim.face_usage()
    fed: dict = {}   # controller -> tunnels facing it
    for t in tunnels.values():
        if t.faces_controller is not None and not t.output:
            fed.setdefault(t.faces_controller, []).append(t)
    cells = {}
    for pos, b in layout.blocks.items():
        c: dict = {"kind": b.kind}
        if isinstance(b, Controller):
            node = g.controller_nodes[pos]
            c["valid"] = g.controller_validity[pos]
            c["state"] = sim.grid(node).state
            c["powered"] = b.powered
            c["faces"] = {d.name.lower(): face_use[(pos, d)] for d in Direction if (pos, d) in face_use}
            ts = fed.get(pos, [])
            c["tunnels"] = len(ts)
            c["tunnels_online"] = sum(t.active for t in ts)
            c["open_faces"] = sum(1 for d in Direction if (pos, d) in face_use)
        elif isinstance(b, CableBus):
            c["cable"] = b.cable.value
            c["color"] = b.color
            node = g.centers.get(pos)
            if node is not None:
                c["used"] = node.used
                c["capacity"] = node.capacity
                c["state"] = sim.grid(node).state
                sides = []
                for conn in node.conns:
                    if conn.kind == "adjacent":
                        o = conn.other(node).pos
                        sides.append(Direction((o[0] - pos[0], o[1] - pos[1], o[2] - pos[2])).name.lower())
                c["sides"] = sides
            parts = {}
            for d, part in b.parts.items():
                p: dict = {"kind": part.kind}
                if isinstance(part, P2PTunnel):
                    t = tunnels.get((pos, d))
                    p.update(freq=part.freq, output=part.output)
                    if t is not None:
                        p["state"] = "online" if t.active else ("missing" if t.powered else "offline")
                        p["linked"] = t.linked
                        p["faces_controller"] = t.faces_controller is not None
                elif part.kind == "device":
                    node = g.part_nodes.get((pos, d))
                    p["state"] = "online" if node is not None and sim.satisfied(node) and sim.grid(node).powered \
                        else "missing"
                parts[d.name.lower()] = p
            c["parts"] = parts
        elif isinstance(b, Marker):
            c["block"] = b.block
        else:
            node = g.part_nodes.get((pos, None))
            if node is not None:
                c["state"] = sim.grid(node).state
                if b.kind == "device_block":
                    c["online"] = sim.satisfied(node) and sim.grid(node).powered
        cells[_key(pos)] = c
    return {
        "ok": report.ok,
        "metrics": report.metrics,
        "violations": [{"rule": v.rule, "severity": v.severity, "message": v.message,
                        "positions": [list(p) for p in v.positions]} for v in report.violations],
        "cells": cells,
        "warnings": profile.warnings(),
    }


# --------------------------------------------------------------------------- optimizer runs

def _settings_to_spec(settings: dict, profile: Profile):
    from ..optimize import OptimizeSpec
    box = tuple(int(v) for v in settings.get("box") or profile.max_controller_size)
    if any(b > m for b, m in zip(box, profile.max_controller_size)):
        raise ValueError(f"box {box} is larger than the profile's controller limit {profile.max_controller_size}")
    prove = settings.get("mode", "improve") == "prove"
    target = settings.get("target")
    return OptimizeSpec(
        size=box,
        time_limit=math.inf if prove else float(settings.get("main", 300)),
        lns_time=0.0 if prove else math.inf,
        workers=max(1, int(settings.get("workers", os.cpu_count() or 8))),
        exterior_tunnels=bool(settings.get("exterior", True)),
        interior_tunnels=bool(settings.get("interior", True)),
        anchor_free=bool(settings.get("anchor_free", True)),
        symmetry_breaking=bool(settings.get("symmetry", True)),
        target_tunnels=int(target) if target else None,
    )


def _history(run_dir: Path) -> list[dict]:
    """Best tunnels and proven bound over active time, across sessions."""
    events, _ = runner._read_events(run_dir / runner.PROGRESS_FILE, 0)
    out, offset, session, last_t = [], 0.0, None, 0.0
    best = upper = None
    for ev in events:
        if ev.get("session") != session:
            offset += last_t
            session, last_t = ev.get("session"), 0.0
        t = float(ev.get("time") or 0.0)
        last_t = max(last_t, t)
        kind = ev.get("kind")
        if kind == "solution" and (best is None or ev["tunnels"] > best):
            best = ev["tunnels"]
        elif kind == "bound" and ev.get("tunnels") is not None:
            upper = ev["tunnels"] if upper is None else min(upper, ev["tunnels"])
        elif kind == "result":
            best, upper = ev["tunnels"], ev["upper"]
        else:
            continue
        out.append({"t": round(offset + t, 2), "best": best, "upper": upper})
    return out


class LiveRun:
    """One worker process plus the state the page polls; mirrors runner.control()."""

    def __init__(self, run_dir: Path, budget_s: float | None, low_priority: bool = True):
        self.dir = run_dir
        data = runner.read_run(run_dir)
        profile = Profile.from_dict(data["profile"])
        self.panel = runner.Panel(run_dir.name, profile.dense_channel_capacity, budget_s,
                                  data.get("active_seconds", 0.0), data.get("best_upper"), data.get("best_key"))
        self.budget = budget_s
        self.active = 0.0
        self.paused = self.stopping = self.done = False
        self.lock = threading.Lock()
        path = run_dir / runner.PROGRESS_FILE
        self.offset = path.stat().st_size if path.exists() else 0
        self.proc = runner.start_worker(run_dir, low_priority)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        last = time.monotonic()
        while True:
            with self.lock:
                now = time.monotonic()
                if not self.paused:
                    self.active += now - last
                last = now
                events, self.offset = runner._read_events(self.dir / runner.PROGRESS_FILE, self.offset)
                for ev in events:
                    self.panel.apply(ev, now)
                if self.budget and self.active >= self.budget and not self.stopping:
                    self._stop()
                    self.panel.phase = "time budget reached"
                if self.proc.poll() is not None:
                    for ev in runner._read_events(self.dir / runner.PROGRESS_FILE, self.offset)[0]:
                        self.panel.apply(ev, now)
                    self._finish()
                    return
            time.sleep(0.5)

    def _finish(self) -> None:
        self.done = True
        p = self.panel
        p.state = "STOPPED" if self.stopping else "FINISHED"
        if p.result and p.result.get("status") == "OPTIMAL":
            p.state = "OPTIMAL"
        data = runner.read_run(self.dir)
        data["active_seconds"] = data.get("active_seconds", 0.0) + self.active
        if p.upper is not None:
            data["best_upper"] = p.upper if data.get("best_upper") is None else min(data["best_upper"], p.upper)
        if self.stopping and self.proc.returncode == 0:
            data["status"] = "STOPPED"
        elif self.proc.returncode:
            data["status"] = "ERROR"
            p.state = "ERROR"
            p.error = p.error or f"solver exited with code {self.proc.returncode} (see worker.log)"
        runner.write_run(self.dir, data)

    def _stop(self) -> None:
        if self.paused:
            runner._resume(self.proc)
            self.paused = False
        (self.dir / runner.STOP_FILE).touch()
        self.stopping = True
        self.panel.state = "STOPPING"

    def action(self, what: str) -> None:
        with self.lock:
            if self.done:
                return
            if what == "pause" and not self.paused and not self.stopping:
                runner._suspend(self.proc)
                self.paused = True
                self.panel.state = "PAUSED"
            elif what == "resume" and self.paused:
                runner._resume(self.proc)
                self.paused = False
                self.panel.state = "RUNNING"
            elif what == "stop" and not self.stopping:
                self._stop()

    def shutdown(self) -> None:
        with self.lock:
            if not self.done and not self.stopping:
                self._stop()
        try:
            self.proc.wait(timeout=180)   # the worker tidies the wiring before it saves
        except Exception:
            self.proc.kill()

    def status(self) -> dict:
        p = self.panel
        return {"state": p.state, "phase": p.phase, "tunnels": p.tunnels, "controllers": p.controllers,
                "upper": p.upper, "error": p.error, "paused": self.paused, "live": not self.done,
                "active": p.active_before + self.active, "session_active": self.active, "budget": self.budget}


class Polisher:
    """Tidies the wiring of runs' best-so-far checkpoints before they are shown.

    The worker saves raw checkpoints as it finds better solutions; their wiring is
    untidied (many separate exits, little dense cable). This re-wires a checkpoint
    with the same controllers and tunnels (optimize.polish_layout) in a background
    thread, caches the result per checkpoint, and, once the run is no longer
    running, saves it back as the run's best.json."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[int, dict]] = {}       # run id -> (best.json mtime_ns, layout)
        self.jobs: dict[str, tuple[int, threading.Thread]] = {}

    def get(self, run_dir: Path, live: bool, wait: bool) -> dict:
        best = run_dir / runner.BEST_FILE
        if not best.is_file():
            raise KeyError(f"{run_dir.name} has no saved layout yet")
        data = runner.read_run(run_dir)
        mtime = best.stat().st_mtime_ns
        raw = Layout.load(best).to_dict()
        # Runs from before this flag existed count as untidied unless they finished.
        polished = data.get("best_polished", data.get("status") in ("OPTIMAL", "FEASIBLE", "FINISHED"))
        if polished or not _has_ortools():
            return {**raw, "polished": bool(polished), "polishing": False}
        rid = run_dir.name
        with self.lock:
            hit = self.cache.get(rid)
            if hit and hit[0] == mtime:
                return {**hit[1], "polished": True, "polishing": False}
            job = self.jobs.get(rid)
            if job is None or job[0] != mtime:
                t = threading.Thread(target=self._work, args=(run_dir, mtime, live), daemon=True)
                self.jobs[rid] = (mtime, t)
                t.start()
            thread = self.jobs[rid][1]
        if wait:
            thread.join(timeout=180)
            with self.lock:
                hit = self.cache.get(rid)
            # A stopped run's tidied layout is saved back, so best.json may be newer now.
            if hit and hit[0] in (mtime, best.stat().st_mtime_ns):
                return {**hit[1], "polished": True, "polishing": False}
        return {**raw, "polished": False, "polishing": thread.is_alive()}

    def _work(self, run_dir: Path, mtime: int, live: bool) -> None:
        from ..optimize import polish_layout
        rid = run_dir.name
        try:
            data = runner.read_run(run_dir)
            profile = Profile.from_dict(data["profile"])
            spec = runner.spec_from_dict(data["spec"])
            # Leave most of the CPU to a running solver.
            workers = 2 if live else max(1, os.cpu_count() or 8)
            lay, info = polish_layout(profile, spec, Layout.load(run_dir / runner.BEST_FILE), workers=workers)
            out = lay.to_dict()
            out["tidy"] = info
            with self.lock:
                self.cache[rid] = (mtime, out)
            best = run_dir / runner.BEST_FILE
            if not live and info["changed"] and best.stat().st_mtime_ns == mtime:
                tmp = run_dir / (runner.BEST_FILE + ".tmp")
                lay.save(tmp)
                os.replace(tmp, best)
                data = runner.read_run(run_dir)
                data["best_polished"] = True
                runner.write_run(run_dir, data)
                with self.lock:
                    self.cache[rid] = (best.stat().st_mtime_ns, out)
        except Exception as err:  # show the raw checkpoint rather than nothing
            with self.lock:
                self.cache[rid] = (mtime, {**Layout.load(run_dir / runner.BEST_FILE).to_dict(),
                                           "tidy": {"changed": False, "error": f"{type(err).__name__}: {err}"}})


class RunManager:
    def __init__(self, root: Path):
        self.root = root
        self.live: dict[str, LiveRun] = {}
        self.polisher = Polisher()

    def _dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
            raise ValueError("bad run id")
        d = self.root / run_id
        if not (d / runner.RUN_FILE).is_file():
            raise KeyError(run_id)
        return d

    def summary(self, d: Path) -> dict:
        data = runner.read_run(d)
        spec = data["spec"]
        prof = data["profile"]
        box = spec.get("size") or prof["max_controller_size"]
        out = {"id": d.name, "box": box, "profile": prof["name"], "status": data.get("status"),
               "tunnels": data.get("best_tunnels"), "upper": data.get("best_upper"),
               "controllers": -data["best_key"][1] if data.get("best_key") else None,
               "active": data.get("active_seconds", 0.0), "sessions": data.get("sessions", 1),
               "mode": "prove" if spec.get("lns_time") in (0, 0.0) else "improve",
               "dense": prof["dense_channel_capacity"], "has_best": (d / runner.BEST_FILE).exists(),
               "modified": (d / runner.RUN_FILE).stat().st_mtime}
        live = self.live.get(d.name)
        if live is not None:
            out.update(live.status())
        else:
            out["live"] = False
            if out["status"] == "RUNNING":   # the app closed while it ran
                out["status"] = "STOPPED"
        return out

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        runs = []
        for d in self.root.iterdir():
            if (d / runner.RUN_FILE).is_file():
                try:
                    runs.append(self.summary(d))
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(runs, key=lambda r: -r["modified"])

    def detail(self, run_id: str) -> dict:
        d = self._dir(run_id)
        out = self.summary(d)
        out["history"] = _history(d)
        result = d / runner.RESULT_FILE
        out["result"] = result.read_text("utf-8") if result.exists() else None
        return out

    def start(self, profile: Profile, settings: dict) -> str:
        spec = _settings_to_spec(settings, profile)
        box = "x".join(map(str, spec.size))
        run_id = f"{box}-{time.strftime('%Y%m%d-%H%M%S')}"
        d = self.root / run_id
        runner.new_run(d, profile, spec)
        self.live[run_id] = LiveRun(d, self._budget(settings), not settings.get("normal_priority"))
        return run_id

    @staticmethod
    def _budget(settings: dict) -> float | None:
        minutes = settings.get("budget_minutes")
        return float(minutes) * 60 if minutes else None

    def act(self, run_id: str, action: str, settings: dict) -> None:
        d = self._dir(run_id)
        live = self.live.get(run_id)
        if action == "continue":
            if live is not None and not live.done:
                raise ValueError("this run is still going")
            runner.prepare_resume(d)
            self.live[run_id] = LiveRun(d, self._budget(settings), not settings.get("normal_priority"))
        elif live is None:
            raise ValueError("this run is not running")
        else:
            live.action(action)

    def shutdown(self) -> None:
        for live in list(self.live.values()):
            live.shutdown()


# --------------------------------------------------------------------------- HTTP

class App:
    def __init__(self, store: ProfileStore, runs_dir: Path, examples: Path | None):
        self.store = store
        self.runs = RunManager(runs_dir)
        self.examples = examples

    def example_files(self) -> list[dict]:
        if not self.examples or not self.examples.is_dir():
            return []
        out = []
        for p in sorted(self.examples.rglob("*.json")):
            if p.name in ("expected.json",):
                continue
            try:
                data = json.loads(p.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or "blocks" not in data:
                continue
            kinds = [b.get("type") for b in data["blocks"]]
            tunnels = sum(pp.get("type") == "p2p_me" and not pp.get("output")
                          for b in data["blocks"] for pp in b.get("parts", {}).values())
            out.append({"id": p.relative_to(self.examples).as_posix(), "name": p.stem,
                        "folder": p.parent.relative_to(self.examples).as_posix(),
                        "controllers": kinds.count("controller"), "blocks": len(kinds), "tunnels": tunnels})
        return out

    def state(self) -> dict:
        return {
            "profiles": [p.to_dict() for p in self.store.list()],
            "default_profile": DEFAULT_PROFILE.name,
            "limits": {"capacity_max": KNOWN_CAPACITY_MAX, "size_max": KNOWN_SIZE_MAX},
            "cpu_count": os.cpu_count() or 8,
            "examples": self.example_files(),
            "runs": self.runs.list(),
            "runs_dir": str(self.runs.root.resolve()),
            "optimizer": _has_ortools(),
        }

    def load_layout(self, src: str, wait: bool = False) -> dict:
        kind, _, ref = src.partition(":")
        if kind == "example" and self.examples:
            path = (self.examples / ref).resolve()
            if self.examples.resolve() not in path.parents:
                raise ValueError("bad example path")
        elif kind == "run":
            live = self.runs.live.get(ref)
            return self.runs.polisher.get(self.runs._dir(ref), live is not None and not live.done, wait)
        else:
            raise ValueError(f"unknown layout source {src!r}")
        return Layout.load(path).to_dict()


def _has_ortools() -> bool:
    try:
        import ortools  # noqa: F401
        return True
    except ImportError:
        return False


def _profile(d: dict | None) -> Profile:
    return Profile.from_dict(d) if d else DEFAULT_PROFILE


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by make_server
    server_version = "ae2opt"

    def log_message(self, fmt, *args):  # quiet console
        pass

    # ------------------------------------------------------------ helpers
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, default=str).encode("utf-8"), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _guard(self, fn) -> None:
        try:
            fn()
        except KeyError as err:
            self._json({"error": f"not found: {err}"}, 404)
        except (ValueError, LayoutError, ProfileError, FileExistsError, RuntimeError) as err:
            self._json({"error": str(err)}, 400)
        except Exception as err:  # keep the server alive; show the error in the page
            self._json({"error": f"{type(err).__name__}: {err}"}, 500)

    # ------------------------------------------------------------ routes
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/" or path.startswith("/static/"):
            name = "index.html" if path == "/" else path[len("/static/"):]
            f = (STATIC / name).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            return self._send(200, f.read_bytes(), _TYPES.get(f.suffix, "application/octet-stream"))
        app = self.app
        if path == "/api/state":
            return self._guard(lambda: self._json(app.state()))
        if path == "/api/layout":
            q = parse_qs(url.query)
            src, wait = q.get("src", [""])[0], q.get("wait", ["0"])[0] == "1"
            return self._guard(lambda: self._json(app.load_layout(src, wait)))
        if path == "/api/runs":
            return self._guard(lambda: self._json(app.runs.list()))
        m = re.fullmatch(r"/api/runs/([^/]+)", path)
        if m:
            return self._guard(lambda: self._json(app.runs.detail(m.group(1))))
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        app = self.app

        def route():
            body = self._body()
            if path == "/api/check":
                lay = Layout.from_dict(body["layout"])
                return self._json(analyze(lay, _profile(body.get("profile"))))
            if path == "/api/profiles":
                p = Profile.from_dict(body["profile"])
                app.store.save(p)
                return self._json({"saved": p.name, "warnings": p.warnings(),
                                   "profiles": [x.to_dict() for x in app.store.list()]})
            if path == "/api/profiles/delete":
                app.store.delete(body["name"])
                return self._json({"profiles": [x.to_dict() for x in app.store.list()]})
            if path == "/api/import-cfg":
                res = import_text(body.get("text", ""), name=body.get("name") or "Imported config")
                return self._json({"profile": res.profile.to_dict(), "summary": res.summary,
                                   "recognized": res.recognized, "warnings": res.warnings,
                                   "fields": {k: {"status": f.status, "value": f.value, "raw": f.raw,
                                                  "location": f.location} for k, f in res.fields.items()}})
            if path == "/api/export":
                return self._export(body)
            if path == "/api/guide":
                from ..guide import build_plan, render_html
                plan = build_plan(Layout.from_dict(body["layout"]), _profile(body.get("profile")),
                                  title=body.get("title") or "AE2 controller build",
                                  design_tunnel_controller=bool(body.get("tunnel_controller")) and _has_ortools(),
                                  time_limit=float(body.get("time", 30)), workers=os.cpu_count() or 8)
                return self._send(200, render_html(plan).encode("utf-8"), _TYPES[".html"])
            if path == "/api/runs":
                if not _has_ortools():
                    raise RuntimeError("the optimizer needs OR-Tools: pip install ortools")
                run_id = app.runs.start(_profile(body.get("profile")), body.get("settings", {}))
                return self._json({"id": run_id})
            m = re.fullmatch(r"/api/runs/([^/]+)/(pause|resume|stop|continue)", path)
            if m:
                app.runs.act(m.group(1), m.group(2), body.get("settings", {}))
                return self._json(app.runs.detail(m.group(1)))
            self._send(404, b"not found", "text/plain")

        self._guard(route)

    def _export(self, body: dict) -> None:
        from ..export import save_structure
        lay = Layout.from_dict(body["layout"])
        expected = None
        if body.get("rig"):
            from ..harness import build_rig
            ctrls = lay.controllers()
            if not ctrls:
                raise ValueError("a test rig needs at least one controller")
            box = tuple(max(c[i] for c in ctrls) - min(c[i] for c in ctrls) + 1 for i in range(3))
            rig = build_rig("gui", lay, box, _profile(body.get("profile")))
            lay, expected = rig.layout, rig.expected
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "build.nbt"
            save_structure(lay, out)
            data = out.read_bytes()
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", body.get("name") or "ae2opt_build")[:40] + ".nbt"
        extra = {"Content-Disposition": f'attachment; filename="{name}"'}
        if expected is not None:
            extra["X-Expected"] = json.dumps(expected, default=str)[:4000]
        self._send(200, data, "application/octet-stream", extra)


def make_server(port: int = 0, store: ProfileStore | None = None, runs_dir: Path | None = None,
                examples: Path | None = REPO_EXAMPLES) -> ThreadingHTTPServer:
    app = App(store or ProfileStore(), runs_dir or data_dir() / "runs", examples)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    server.app = app
    return server


def serve(port: int = 0, open_browser: bool = True, runs_dir: Path | None = None) -> int:
    server = make_server(port, runs_dir=runs_dir)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"AE2 controller optimizer is running at {url}")
    print("Leave this window open while you use the app; press Ctrl+C here to quit.")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping: saving any running optimizer runs...")
    finally:
        server.app.runs.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
