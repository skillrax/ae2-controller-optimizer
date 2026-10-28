"""Long optimizer runs: a run folder, a solver worker process, and a live panel.

    python -m ae2opt run --box 5 5 5 --hours 8 --dir runs/5x5x5
    python -m ae2opt run --resume runs/5x5x5 --hours 4

The solver runs in its own process so the panel can control it:
  * p  pause   -- the worker process is frozen by the OS (no CPU use, nothing lost)
  * r  resume  -- it continues exactly where it was
  * q  save and stop -- the worker finishes cleanly; resume later with --resume

Run folder contents:
  run.json        profile + optimizer settings, active time, best bound so far
  best.json       best layout so far (with tunnel-network ports), checkable on its own
  progress.jsonl  one line per event: solutions, bound updates, phases
  result.txt      final summary
  worker.log      solver output
  stop            created to ask the worker to stop (removed on start)

best.json is overwritten with each better solution as the search finds it; those
checkpoints have untidied wiring (run.json "best_polished": false). The final
save, including after "save and stop", tidies the wiring first (fewest trunks,
then fewest cables); the GUI tidies a checkpoint before showing it.

Stopping and resuming keeps the best layout and the best proven bound (a proven
bound stays valid); the solver's internal search tree cannot be saved, so the
proof work itself starts over on resume.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, fields
from pathlib import Path

from .layout import Layout
from .optimize import OptimizeSpec, layout_from_vector, optimize
from .patterns import canonical
from .profile import Profile

RUN_FILE = "run.json"
BEST_FILE = "best.json"
PROGRESS_FILE = "progress.jsonl"
RESULT_FILE = "result.txt"
LOG_FILE = "worker.log"
STOP_FILE = "stop"


# --------------------------------------------------------------------------- settings I/O

def spec_to_dict(spec: OptimizeSpec) -> dict:
    d = asdict(spec)
    for k, v in d.items():
        if isinstance(v, (frozenset, set)):
            d[k] = sorted(list(x) if isinstance(x, tuple) else x for x in v)
        elif isinstance(v, float) and math.isinf(v):
            d[k] = "inf"
    return d


def spec_from_dict(d: dict) -> OptimizeSpec:
    names = {f.name for f in fields(OptimizeSpec)}
    d = {k: v for k, v in d.items() if k in names}
    for k in ("forbidden", "required", "blocked"):
        if k in d:
            d[k] = frozenset(tuple(c) for c in d[k])
    if d.get("start") is not None:
        d["start"] = frozenset(tuple(c) for c in d["start"])
    if "cuts" in d:
        d["cuts"] = frozenset(d["cuts"])
    if d.get("size") is not None:
        d["size"] = tuple(d["size"])
    if "solver_params" in d:
        d["solver_params"] = tuple(tuple(p) for p in d["solver_params"])
    for k in ("time_limit", "lns_time"):
        if d.get(k) == "inf":
            d[k] = math.inf
    return OptimizeSpec(**d)


def run_rules(data: dict) -> dict:
    """A run's rules in the shape of a library entry's (see library.rules_match)."""
    spec, prof = data["spec"], data["profile"]
    return {"normal": prof["normal_channel_capacity"], "dense": prof["dense_channel_capacity"],
            "channels": prof.get("channels_enabled", True),
            "model": data.get("model", 2),   # runs from before the field was added used model 2
            "exterior": spec.get("exterior_tunnels", True), "interior": spec.get("interior_tunnels", True),
            "anchor_free": spec.get("anchor_free", True), "margin": spec.get("margin", 1),
            "custom": bool(spec.get("target_tunnels") or spec.get("forbidden") or spec.get("required")
                           or spec.get("blocked"))}


def read_run(run_dir: Path) -> dict:
    return json.loads((run_dir / RUN_FILE).read_text("utf-8"))


def write_run(run_dir: Path, data: dict) -> None:
    tmp = run_dir / (RUN_FILE + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), "utf-8")
    os.replace(tmp, run_dir / RUN_FILE)


# --------------------------------------------------------------------------- worker (child process)

def worker_main(run_dir: str | Path) -> int:
    run_dir = Path(run_dir)
    data = read_run(run_dir)
    profile = Profile.from_dict(data["profile"])
    spec = spec_from_dict(data["spec"])
    stop = threading.Event()
    state = {"model": None, "best_key": data.get("best_key")}
    progress_path = run_dir / PROGRESS_FILE
    session = data.get("sessions", 1)

    def watch_stop():
        while not stop.is_set():
            if (run_dir / STOP_FILE).exists():
                stop.set()
            time.sleep(0.5)

    threading.Thread(target=watch_stop, daemon=True).start()

    def log(event: dict) -> None:
        with progress_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**event, "session": session}) + "\n")

    def progress(ev: dict) -> None:
        kind = ev["kind"]
        if kind == "start":
            state["model"] = ev["model"]
            log({"kind": "start", "time": ev["time"], "size": list(ev["size"])})
            return
        if kind == "solution":
            vec = ev.pop("vec")
            # Checkpoint the best layout so far -- but never replace a better one from
            # an earlier session (a resumed run's first solutions can be weaker).
            key = [ev["tunnels"], -ev["controllers"]]
            if state["best_key"] is None or key > state["best_key"]:
                state["best_key"] = key
                lay = layout_from_vector(state["model"], vec)
                tmp = run_dir / (BEST_FILE + ".tmp")
                lay.save(tmp)
                os.replace(tmp, run_dir / BEST_FILE)
                d = read_run(run_dir)
                d["best_key"], d["best_tunnels"] = key, ev["tunnels"]
                d["best_polished"] = False   # a raw checkpoint: wiring not tidied yet
                write_run(run_dir, d)
        log(ev)

    try:
        sol = optimize(profile, spec, progress=progress, stop=stop)
    except Exception as err:  # reported to the panel through the progress log
        log({"kind": "error", "time": 0, "message": f"{type(err).__name__}: {err}"})
        raise
    data = read_run(run_dir)
    key = [sol.tunnels_validated, -sol.controllers]
    prev_key = data.get("best_key")
    if prev_key is None or key >= prev_key:
        # The final layout is simulator-checked and repaired, so it replaces the
        # checkpoint of the same solution.
        sol.harness.save(run_dir / BEST_FILE)
        data["best_key"], data["best_tunnels"] = key, sol.tunnels_validated
        data["best_polished"] = True
        (run_dir / RESULT_FILE).write_text(sol.summary() + "\n", "utf-8")
    else:
        (run_dir / RESULT_FILE).write_text(
            f"This session's best ({sol.tunnels_validated} tunnels) did not beat the saved best "
            f"({prev_key[0]} tunnels); best.json is unchanged.\n", "utf-8")
    prev = data.get("best_upper")
    data["best_upper"] = sol.upper_bound if prev is None else min(prev, sol.upper_bound)
    best_tunnels = data["best_key"][0]
    data["status"] = "OPTIMAL" if best_tunnels >= data["best_upper"] and spec.target_tunnels is None else sol.status
    write_run(run_dir, data)
    log({"kind": "result", "time": sol.seconds, "tunnels": best_tunnels, "upper": data["best_upper"],
         "status": data["status"], "controllers": -data["best_key"][1]})
    return 0


# --------------------------------------------------------------------------- process control

def _suspend(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0800, False, proc.pid)  # PROCESS_SUSPEND_RESUME
        ctypes.windll.ntdll.NtSuspendProcess(handle)
        ctypes.windll.kernel32.CloseHandle(handle)
    else:
        import signal
        os.kill(proc.pid, signal.SIGSTOP)


def _resume(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0800, False, proc.pid)
        ctypes.windll.ntdll.NtResumeProcess(handle)
        ctypes.windll.kernel32.CloseHandle(handle)
    else:
        import signal
        os.kill(proc.pid, signal.SIGCONT)


def worker_command(run_dir: Path) -> list[str]:
    """How to start the solver process. A packaged app (PyInstaller) has no
    `python -m`, so it starts its own executable in worker mode."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "_worker", str(run_dir)]
    return [sys.executable, "-m", "ae2opt", "_worker", str(run_dir)]


def start_worker(run_dir: Path, low_priority: bool) -> subprocess.Popen:
    (run_dir / STOP_FILE).unlink(missing_ok=True)
    log = (run_dir / LOG_FILE).open("a", encoding="utf-8")
    kwargs = {}
    if os.name == "nt":
        flags = 0x00000200  # CREATE_NEW_PROCESS_GROUP: Ctrl+C goes to the panel, not the solver
        if low_priority:
            flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
        if getattr(sys, "frozen", False):
            flags |= 0x08000000  # CREATE_NO_WINDOW: no console window for the solver
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
        if low_priority:
            kwargs["preexec_fn"] = lambda: os.nice(10)
    try:
        return subprocess.Popen(worker_command(run_dir),
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
    finally:
        log.close()  # the worker has its own handle


# --------------------------------------------------------------------------- setup

def new_run(run_dir: Path, profile: Profile, spec: OptimizeSpec) -> None:
    if (run_dir / RUN_FILE).exists():
        raise FileExistsError(f"{run_dir} already holds a run; use --resume {run_dir} or another --dir")
    run_dir.mkdir(parents=True, exist_ok=True)
    from .optimize import MODEL_VERSION
    write_run(run_dir, {"profile": profile.to_dict(), "spec": spec_to_dict(spec), "model": MODEL_VERSION,
                        "active_seconds": 0.0, "sessions": 1, "best_upper": None, "best_tunnels": None,
                        "status": "RUNNING"})


def prepare_resume(run_dir: Path) -> dict:
    """Point the next session at the best layout so far and the best proven bound."""
    data = read_run(run_dir)
    spec = spec_from_dict(data["spec"])
    size = spec.size or tuple(data["profile"]["max_controller_size"])
    best_path = run_dir / BEST_FILE
    start = None
    if best_path.exists():
        xs = frozenset(Layout.load(best_path).controllers())
        uniform = not (spec.forbidden or spec.required or spec.blocked)
        start = canonical(xs, size) if uniform else xs
    upper = data.get("best_upper")
    for ev in _read_events(run_dir / PROGRESS_FILE, 0)[0]:
        if ev.get("kind") == "bound" and ev.get("tunnels") is not None:
            upper = ev["tunnels"] if upper is None else min(upper, ev["tunnels"])
    from dataclasses import replace
    spec = replace(spec, start=start, seed_time=max(spec.seed_time, 30.0), known_upper=upper)
    data["spec"] = spec_to_dict(spec)
    data["best_upper"] = upper
    data["sessions"] = data.get("sessions", 1) + 1
    data["status"] = "RUNNING"
    write_run(run_dir, data)
    return data


def _read_events(path: Path, offset: int) -> tuple[list[dict], int]:
    if not path.exists():
        return [], offset
    with path.open("r", encoding="utf-8") as f:
        f.seek(offset)
        chunk = f.read()
    # Only consume complete lines; a partial last line is read next time.
    end = chunk.rfind("\n") + 1
    events = []
    for line in chunk[:end].splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events, offset + len(chunk[:end].encode("utf-8"))


# --------------------------------------------------------------------------- panel

def _fmt_time(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _fmt_ago(s: float) -> str:
    if s < 60:
        return f"{int(s)}s ago"
    if s < 3600:
        return f"{int(s // 60)}m{int(s % 60):02d}s ago"
    return f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m ago"


class Panel:
    """State + rendering for the live view. Pure, so it can be tested."""

    WIDTH = 40

    def __init__(self, title: str, dense: int, budget: float | None, active_before: float, upper: int | None,
                 best_key: list | None = None, unicode: bool = True):
        self.title, self.dense, self.budget = title, dense, budget
        # Consoles/pipes that cannot encode the bar glyphs get an ASCII bar.
        self.full, self.empty, self.dot = ("█", "░", "·") if unicode else ("#", "-", "-")
        self.active_before = active_before
        self.state = "STARTING"
        self.phase = "starting solver"
        self.tunnels, self.controllers = (best_key[0], -best_key[1]) if best_key else (None, None)
        self.upper = upper
        self.last_improvement: float | None = None
        self.error: str | None = None
        self.result: dict | None = None

    def apply(self, ev: dict, now: float) -> None:
        kind = ev.get("kind")
        if kind == "phase":
            self.phase = ev["phase"]
            if self.state == "STARTING":
                self.state = "RUNNING"
        elif kind == "solution":
            new = (ev["tunnels"], -ev["controllers"])
            old = (self.tunnels, -(self.controllers if self.controllers is not None else 10 ** 9)) \
                if self.tunnels is not None else None
            if old is None or new > old:
                self.last_improvement = now
                self.tunnels, self.controllers = ev["tunnels"], ev["controllers"]
        elif kind == "bound" and ev.get("tunnels") is not None:
            self.upper = ev["tunnels"] if self.upper is None else min(self.upper, ev["tunnels"])
        elif kind == "error":
            self.error = ev["message"]
        elif kind == "result":
            self.result = ev
            self.tunnels, self.controllers = ev["tunnels"], ev["controllers"]
            self.upper = ev["upper"]

    def lines(self, active: float, now: float) -> list[str]:
        # Active time counts every session; the budget applies to this session.
        total = self.active_before + active
        budget = f"   (this session {_fmt_time(active)} / {_fmt_time(self.budget)})" if self.budget else ""
        ago = _fmt_ago(now - self.last_improvement) if self.last_improvement else "-"
        found = (f"{self.tunnels} tunnels = {self.tunnels * self.dense:,} channels"
                 + (f" {self.dot} {self.controllers} controllers" if self.controllers is not None else "")
                 if self.tunnels is not None else "searching...")
        if self.tunnels is not None and self.upper:
            frac = min(1.0, self.tunnels / self.upper)
            gap = (self.upper - self.tunnels) / self.upper * 100
            filled = int(round(frac * self.WIDTH))
            bar = f"[{self.full * filled}{self.empty * (self.WIDTH - filled)}] {frac * 100:5.1f}% of proven max"
            upper = f"{self.upper} tunnels   gap {gap:.1f}%" + ("   PROVEN OPTIMAL" if gap <= 0 else "")
        else:
            bar = f"[{self.empty * self.WIDTH}]"
            upper = f"{self.upper} tunnels" if self.upper else "not known yet"
        out = [
            f" AE2 controller optimizer {self.dot} {self.title}",
            f" state        {self.state}",
            f" active time  {_fmt_time(total)}{budget}",
            f" improved     {ago}",
            f" best found   {found}",
            f" proven max   {upper}",
            f" {bar}",
            f" phase        {self.phase}",
        ]
        if self.error:
            out.append(f" ERROR        {self.error}")
        if not self.state.startswith(("FINISHED", "STOPPING", "STOPPED")):
            out.append(f" p pause {self.dot} r resume {self.dot} q save & stop")
        return out


class _Keys:
    """Non-blocking single-key input (Windows console or POSIX tty)."""

    def __init__(self):
        self.enabled = sys.stdin is not None and sys.stdin.isatty()
        self._old = None
        if self.enabled and os.name != "nt":
            import termios
            import tty
            self._old = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())

    def get(self) -> str | None:
        if not self.enabled:
            return None
        if os.name == "nt":
            import msvcrt
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                return ch.lower()
            return None
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1).lower()
        return None

    def close(self):
        if self._old is not None:
            import termios
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old)


def control(run_dir: Path, budget_hours: float | None, low_priority: bool, out=None) -> int:
    """Start the worker and show the live panel until it finishes."""
    out = out or sys.stdout
    data = read_run(run_dir)
    profile = Profile.from_dict(data["profile"])
    spec = spec_from_dict(data["spec"])
    size = spec.size or profile.max_controller_size
    try:
        "█░·".encode(getattr(out, "encoding", None) or "utf-8")
        unicode = True
    except UnicodeEncodeError:
        unicode = False
    sep = " · " if unicode else " - "
    title = f"{'x'.join(map(str, size))}{sep}{profile.name}{sep}session {data.get('sessions', 1)}"
    budget = budget_hours * 3600 if budget_hours else None
    panel = Panel(title, profile.dense_channel_capacity, budget, data.get("active_seconds", 0.0),
                  data.get("best_upper"), data.get("best_key"), unicode=unicode)
    progress_path = run_dir / PROGRESS_FILE
    offset = progress_path.stat().st_size if progress_path.exists() else 0

    proc = start_worker(run_dir, low_priority)
    keys = _Keys()
    interactive = keys.enabled and out.isatty()
    paused = stopping = False
    active, last_tick = 0.0, time.monotonic()
    drawn = 0
    last_plain = 0.0

    def request_stop():
        nonlocal stopping, paused
        if paused:
            _resume(proc)
            paused = False
        (run_dir / STOP_FILE).touch()
        stopping = True
        panel.state = "STOPPING"

    try:
        while True:
            now = time.monotonic()
            if not paused:
                active += now - last_tick
            last_tick = now
            events, offset = _read_events(progress_path, offset)
            for ev in events:
                panel.apply(ev, now)

            key = keys.get()
            if key == "p" and not paused and not stopping:
                _suspend(proc)
                paused = True
                panel.state = "PAUSED (solver frozen, no CPU use)"
            elif key == "r" and paused:
                _resume(proc)
                paused = False
                panel.state = "RUNNING"
            elif key == "q" and not stopping:
                request_stop()
            if budget and active >= budget and not stopping:
                request_stop()
                panel.phase = "time budget reached"

            done = proc.poll() is not None
            if done:
                for ev in _read_events(progress_path, offset)[0]:
                    panel.apply(ev, now)
                panel.state = "FINISHED" if not stopping else "STOPPED (resume with --resume)"
                if panel.result and panel.result.get("status") == "OPTIMAL":
                    panel.state = f"FINISHED {panel.dot} PROVEN OPTIMAL"

            lines = panel.lines(active, now)
            if interactive:
                if drawn:
                    out.write(f"\x1b[{drawn}F")
                for line in lines:
                    out.write("\x1b[2K" + line + "\n")
                drawn = len(lines)
                out.flush()
            elif now - last_plain >= 30 or done:
                out.write(" | ".join(" ".join(l.split()) for l in lines[1:8] if l.strip()) + "\n")
                out.flush()
                last_plain = now
            if done:
                break
            time.sleep(0.5)
    except BaseException:
        # Ctrl+C, or the panel itself failing: never leave the solver running
        # unattended -- ask it to save and stop, and wait for it.
        if proc.poll() is None:
            request_stop()
            proc.wait()
        if not isinstance(sys.exc_info()[1], KeyboardInterrupt):
            raise
    finally:
        keys.close()
        if paused and proc.poll() is None:
            _resume(proc)
        data = read_run(run_dir)
        data["active_seconds"] = data.get("active_seconds", 0.0) + active
        if panel.upper is not None:
            data["best_upper"] = panel.upper if data.get("best_upper") is None else min(data["best_upper"], panel.upper)
        if stopping and proc.returncode == 0:
            data["status"] = "STOPPED"
        write_run(run_dir, data)

    result = run_dir / RESULT_FILE
    if result.exists():
        out.write("\n" + result.read_text("utf-8"))
    out.write(f"\nRun folder: {run_dir}\n  best layout: {run_dir / BEST_FILE}\n")
    if stopping:
        out.write(f"  continue later:  python -m ae2opt run --resume {run_dir}\n")
    return proc.returncode or 0
