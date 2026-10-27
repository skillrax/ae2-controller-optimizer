"""CP-SAT optimizer for main-controller + tunnel-cell layouts.

The model places, inside a box (default: the profile's max controller size):
  * controller blocks             x[c]
  * input tunnels on their faces  f[c, d]   (tunnel in cell c+d, facing c)
  * tunnel-network cable cells    k[n]      (box gaps and a 1-block margin)
and routes every tunnel's tunnel-network channel to the outside with an
integer flow that respects cable capacities. Rules from MECHANICS.md section 5:

  R1  box size                    by construction
  R2  one connected structure     single-commodity flow from a root controller
  R3  cross rule                  at most one full opposite pair per controller
  R4  a face counts only with a tunnel on it      objective counts f
  R5  tunnel cells hold a normal cable            f <= k, cells next to a face are normal
  R6  uncapped faces merge networks               every non-tunnel face next to a
                                                  cable cell gets a cable anchor
  R7  tunnel cells need an exit   flow conservation: channels must leave the cell
  R8  normal cable carries <= normal capacity     node capacity on the flow
  R9-R11  hold for generated builds (no nesting, separate powered networks)

The flow model is an upper bound on AE2's greedy pathing, so every solution is
re-checked with the simulator. If the simulator delivers fewer tunnels, that
exact tunnel set is cut and the model is re-solved, keeping the best layout the
simulator confirms.
"""

from __future__ import annotations

import itertools
import math
import random
import threading
import time
from dataclasses import dataclass, field

from .geometry import Direction, Pos, step
from .layout import CableAnchor, CableType, Layout, P2PTunnel
from .patterns import lattice_shapes
from .profile import DEFAULT_PROFILE, Profile
from .rules import Report, check
from .sim import ONLINE

try:
    from ortools.sat.python import cp_model
except ImportError:  # pragma: no cover - reported when the optimizer is used
    cp_model = None


@dataclass(frozen=True)
class OptimizeSpec:
    # Box to fill. None = the profile's maximum controller size.
    size: tuple[int, int, int] | None = None
    time_limit: float = 60.0           # seconds per solve round
    workers: int = 8
    # Allow tunnel cells in the 1-block layer around the box.
    exterior_tunnels: bool = True
    # Allow tunnel cells and cable corridors in gaps inside the box.
    interior_tunnels: bool = True
    # Among layouts with the most tunnels, prefer fewer controller blocks.
    minimize_controllers: bool = True
    # If set: use the fewest controllers that still reach this many tunnels.
    target_tunnels: int | None = None
    # Box cells (box coordinates) that may not hold a controller / must hold one.
    forbidden: frozenset[Pos] = frozenset()
    required: frozenset[Pos] = frozenset()
    # Cells (box or margin) that can hold nothing at all, e.g. terrain.
    blocked: frozenset[Pos] = frozenset()
    # Warm start: evaluate this many repeating lattice shapes (plus `start`, if
    # given) with a quick fixed-shape solve, and hint the best one. Off by
    # default: on 5x5x5 the best lattices route only 235 tunnels vs 285+ from a
    # plain solve, because they leave too few exits for interior tunnels.
    seed_patterns: int = 0
    seed_time: float = 10.0            # seconds per seed shape
    start: frozenset[Pos] | None = None  # controller positions to start from (e.g. a saved layout)
    # Mirror / axis-order symmetry breaking on the main solve (ignored with seeds or
    # a start layout). With the cuts below it roughly halves proof time on 4x4x3.
    symmetry_breaking: bool = True
    # Extra constraints that only tighten the solver's bound (valid for every real
    # layout): "cap" capacity scaling, "exit" tunnel-cell exits, "tree" n-1 pairs,
    # "degree" every controller touches another. "tree" is what makes 4x4x3
    # provable (unproven after 240 s without it, 77 s with all cuts + symmetry).
    cuts: frozenset[str] = frozenset({"cap", "exit", "tree", "degree"})
    # Raw CP-SAT parameters, e.g. (("linearization_level", 2),).
    solver_params: tuple = ()
    # Large-neighbourhood search after the main solve: repeatedly free the
    # controllers in a small sub-box, keep the rest, and re-solve.
    lns_time: float = 0.0              # total seconds (0 = off)
    lns_step: float = 10.0             # seconds per neighbourhood
    random_seed: int = 0
    # A proven upper bound on tunnels from an earlier run of the same spec. Proven
    # bounds stay valid, so a resumed run reports min(this, its own bound).
    known_upper: int | None = None
    log: bool = False


@dataclass
class Solution:
    layout: Layout                 # the build itself (controllers, cables, tunnels, anchors)
    harness: Layout                # layout + tunnel-network ports, as simulated
    report: Report                 # rules report on the harness
    status: str                    # OPTIMAL / FEASIBLE (of the last model solve)
    controllers: int
    tunnels_model: int
    tunnels_validated: int
    upper_bound: int               # proven max tunnels for this spec (solver bound)
    anchors: int
    cable_cells: int
    exit_cells: list[Pos]          # cable cells that must connect to your tunnel network
    channels: int
    tunnel_network_channels: int   # inputs + one output each
    rounds: int
    seconds: float
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        proven = "proven optimal" if self.status == "OPTIMAL" and self.tunnels_validated == self.tunnels_model \
            else f"best found (upper bound {self.upper_bound} tunnels)"
        lines = [
            f"{self.tunnels_validated} working tunnels = {self.channels} channels  [{proven}]",
            f"  controller blocks: {self.controllers}",
            f"  cable cells: {self.cable_cells} (exits to tunnel network: {len(self.exit_cells)})",
            f"  cable anchors: {self.anchors}",
            f"  tunnel network needs {self.tunnel_network_channels} channels (inputs + outputs)",
            f"  model {self.tunnels_model} / simulator {self.tunnels_validated}, "
            f"{self.rounds} round(s), {self.seconds:.1f}s",
        ]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


class OptimizerUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------- geometry

def _cells(size) -> list[Pos]:
    return [(x, y, z) for x in range(size[0]) for y in range(size[1]) for z in range(size[2])]


class _Region:
    def __init__(self, size, exterior: bool):
        self.size = size
        self.box = _cells(size)
        self.box_set = set(self.box)
        if exterior:
            m = [(x - 1, y - 1, z - 1) for x, y, z in _cells((size[0] + 2, size[1] + 2, size[2] + 2))]
            self.cells = m
        else:
            self.cells = list(self.box)
        self.cell_set = set(self.cells)

    def in_box(self, p: Pos) -> bool:
        return p in self.box_set

    def outward(self, p: Pos) -> list[Direction]:
        return [d for d in Direction if step(p, d) not in self.cell_set]


# --------------------------------------------------------------------------- model

class _Model:
    def __init__(self, spec: OptimizeSpec, profile: Profile, size):
        self.spec, self.profile = spec, profile
        self.r = r = _Region(size, spec.exterior_tunnels)
        self.m = m = cp_model.CpModel()
        N = len(r.box)
        normal, dense = profile.normal_channel_capacity, profile.dense_channel_capacity
        blocked = set(spec.blocked)
        cuts = spec.cuts

        # controllers
        self.x = {}
        for c in r.box:
            v = m.NewBoolVar(f"x{c}")
            if c in spec.forbidden or c in blocked:
                m.Add(v == 0)
            if c in spec.required:
                m.Add(v == 1)
            self.x[c] = v

        def X(p):
            return self.x.get(p)

        # cable cells
        self.k = {}
        for n in r.cells:
            v = m.NewBoolVar(f"k{n}")
            if n in blocked or (r.in_box(n) and not spec.interior_tunnels):
                m.Add(v == 0)
            if X(n) is not None:
                m.AddImplication(v, X(n).Not())
            self.k[n] = v

        # tunnels on faces
        self.f = {}
        into: dict[Pos, list] = {n: [] for n in r.cells}
        for c in r.box:
            for d in Direction:
                n = step(c, d)
                if n not in r.cell_set:
                    continue
                v = m.NewBoolVar(f"f{c}{d.name}")
                m.AddImplication(v, self.x[c])
                m.AddImplication(v, self.k[n])
                self.f[(c, d)] = v
                into[n].append(v)

        # R3 cross rule
        for c in r.box:
            pairs = []
            for d in (Direction.EAST, Direction.UP, Direction.SOUTH):
                a, b = step(c, d), step(c, d.opposite)
                if X(a) is not None and X(b) is not None:
                    q = m.NewBoolVar(f"q{c}{d.axis}")
                    m.AddBoolOr([X(a).Not(), X(b).Not(), q])
                    pairs.append(q)
            if len(pairs) >= 2:
                m.Add(sum(pairs) <= 1).OnlyEnforceIf(self.x[c])

        # R2 connectivity: root = first controller in scan order, flow to every controller
        prefix_prev = None
        roots = {}
        for i, c in enumerate(r.box):
            p = m.NewBoolVar(f"p{i}")
            m.Add(p >= self.x[c])
            if prefix_prev is None:
                m.Add(p <= self.x[c])
                roots[c] = p
            else:
                m.Add(p >= prefix_prev)
                m.Add(p <= prefix_prev + self.x[c])
                rt = m.NewBoolVar(f"root{c}")
                m.Add(rt == p - prefix_prev)
                roots[c] = rt
            prefix_prev = p
        m.Add(prefix_prev == 1)  # at least one controller
        self.roots = roots
        total_x = sum(self.x.values())
        gflow = {}
        for c in r.box:
            for d in Direction:
                n = step(c, d)
                if n in r.box_set:
                    v = m.NewIntVar(0, N, f"g{c}{d.name}")
                    m.Add(v <= N * self.x[c])
                    m.Add(v <= N * self.x[n])
                    gflow[(c, n)] = v
        supply = {}
        for c in r.box:
            s = m.NewIntVar(0, N, f"s{c}")
            m.Add(s <= N * roots[c])
            supply[c] = s
        m.Add(sum(supply.values()) == total_x)
        for c in r.box:
            out = [gflow[(c, n)] for _, n in ((d, step(c, d)) for d in Direction) if (c, n) in gflow]
            inn = [gflow[(n, c)] for _, n in ((d, step(c, d)) for d in Direction) if (n, c) in gflow]
            m.Add(sum(out) - sum(inn) == supply[c] - self.x[c])

        # R5/R7/R8: tunnel-network routing flow from tunnel cells to outside the region
        self.cap = {}
        for n in r.cells:
            touches_box = any(step(n, d) in r.box_set for d in Direction)
            self.cap[n] = normal if touches_box else dense
        # Routing is a forest: every cable cell has exactly one parent (a neighbouring
        # cable cell, or the outside). Extraction caps every other cable-to-cable
        # side with an anchor, so AE2's greedy BFS has exactly one route per
        # tunnel and the simulator agrees with the model.
        self.h, self.par = {}, {}
        for n in r.cells:
            for d in Direction:
                o = step(n, d)
                if o in r.cell_set:
                    p = m.NewBoolVar(f"par{n}{d.name}")
                    m.AddImplication(p, self.k[o])
                    v = m.NewIntVar(0, dense, f"h{n}{d.name}")
                    # cut "cap": an edge carries at most what either end node can hold
                    m.Add(v <= (min(self.cap[n], self.cap[o]) if "cap" in cuts else dense) * p)
                    self.h[(n, o)] = v
                    self.par[(n, o)] = p
        for (n, o), p in self.par.items():
            if n < o:
                m.AddBoolOr([p.Not(), self.par[(o, n)].Not()])
        self.sink, self.par_sink = {}, {}
        for n in r.cells:
            outs = r.outward(n)
            if outs:
                p = m.NewBoolVar(f"parsink{n}")
                v = m.NewIntVar(0, dense * len(outs), f"sink{n}")
                m.Add(v <= (self.cap[n] if "cap" in cuts else dense * len(outs)) * p)
                self.sink[n], self.par_sink[n] = v, p
        for n in r.cells:
            parents = [self.par[(n, step(n, d))] for d in Direction if (n, step(n, d)) in self.par]
            if n in self.par_sink:
                parents.append(self.par_sink[n])
            m.Add(sum(parents) == self.k[n])
        self.T = {}
        for n in r.cells:
            t = sum(into[n]) if into[n] else 0
            self.T[n] = t
            out = [self.h[(n, step(n, d))] for d in Direction if (n, step(n, d)) in self.h]
            inn = [self.h[(step(n, d), n)] for d in Direction if (step(n, d), n) in self.h]
            if out or inn or n in self.sink or into[n]:
                sink = self.sink[n] if n in self.sink else 0
                m.Add(sum(out) + sink - sum(inn) == t)
            if inn or into[n]:
                # cut "cap": scale by k so a fractional cable cell gets fractional capacity
                m.Add(t + sum(inn) <= self.cap[n] * (self.k[n] if "cap" in cuts else 1))
            if "exit" in cuts and into[n]:
                # a tunnel cell keeps one side free for its parent
                m.Add(t <= 5 * self.k[n])

        if "exit" in cuts:
            # a cell whose six neighbours are all controllers cannot be a cable cell
            for n in r.cells:
                nbrs = [self.x[step(n, d)] for d in Direction if step(n, d) in self.x]
                if len(nbrs) == 6:
                    m.Add(sum(nbrs) + self.k[n] <= 6)

        if "tree" in cuts:
            # A connected shape of n blocks has >= n-1 adjacent controller pairs, and
            # a face with a tunnel faces a non-controller: tunnels <= 6n - 2*pairs <= 4n + 2.
            self.e = {}
            for c in r.box:
                for d in (Direction.EAST, Direction.UP, Direction.SOUTH):
                    o = step(c, d)
                    if o in r.box_set:
                        e = m.NewBoolVar(f"e{c}{d.name}")
                        m.AddImplication(e, self.x[c])
                        m.AddImplication(e, self.x[o])
                        m.AddBoolOr([self.x[c].Not(), self.x[o].Not(), e])
                        self.e[(c, o)] = self.e[(o, c)] = e
                        if (c, d) in self.f:
                            m.Add(self.f[(c, d)] <= self.x[c] - e)
                        if (o, d.opposite) in self.f:
                            m.Add(self.f[(o, d.opposite)] <= self.x[o] - e)
            pairs = sum(set(self.e.values()))
            m.Add(pairs >= total_x - 1)
            m.Add(sum(self.f.values()) <= 4 * total_x + 2)

        if "degree" in cuts and "tree" in cuts:
            # With 2+ controllers, each one touches another (connectivity, per block).
            multi = m.NewBoolVar("multi")
            m.Add(total_x >= 2).OnlyEnforceIf(multi)
            m.Add(total_x <= 1).OnlyEnforceIf(multi.Not())
            for c in r.box:
                es = [self.e[(c, step(c, d))] for d in Direction if (c, step(c, d)) in self.e]
                m.Add(sum(es) >= self.x[c] + multi - 1)

        self.tunnels = sum(self.f.values())
        self.total_x = total_x
        # Every variant (seed, main, LNS) is a clone of this model plus extra
        # constraints, so variable indices -- and solutions as hints -- carry over.
        # `score` is always "bigger is better" so variants compare uniformly.
        if spec.target_tunnels is not None:
            m.Add(self.tunnels >= spec.target_tunnels)
            m.Minimize(total_x)
            self.weight = None
            self.score = -total_x
        elif spec.minimize_controllers:
            self.weight = N + 1
            self.score = self.weight * self.tunnels - total_x
            m.Maximize(self.score)
        else:
            self.weight = 1
            self.score = self.tunnels
            m.Maximize(self.score)

    # ------------------------------------------------------------ variants
    # A clone shares variable indices with the base model, and CP-SAT reads
    # expressions by index, so base-model variables are used directly on clones.
    def clone(self):
        return self.m.Clone()

    def fix_shape(self, target, xs: frozenset[Pos], cells=None) -> None:
        """Fix controller cells (all box cells, or just `cells`) to the shape xs."""
        for c in (self.r.box if cells is None else cells):
            target.Add(self.x[c] == int(c in xs))

    def add_search_constraints(self, target, symmetry: bool) -> None:
        """Constraints that only remove equivalent solutions (main solve only)."""
        spec, r = self.spec, self.r
        if spec.forbidden or spec.required or spec.blocked:
            return  # the box is not uniform, so shifted / mirrored layouts differ
        X = self.x
        # Translation: touch the low face on every axis.
        for axis in range(3):
            target.Add(sum(X[c] for c in r.box if c[axis] == 0) >= 1)
        if symmetry:
            # Mirror: centre of mass in the low half on every axis. Combined with the
            # translation rule this is still valid: mirror first, then shift down.
            for axis in range(3):
                target.Add(sum(X[c] * (2 * c[axis] - (r.size[axis] - 1)) for c in r.box) <= 0)
            # Axis order for equal box sides (axis permutations keep the rules above).
            for a, b in ((0, 1), (1, 2), (0, 2)):
                if r.size[a] == r.size[b]:
                    target.Add(sum(X[c] * c[a] for c in r.box) <= sum(X[c] * c[b] for c in r.box))

    def satisfies_symmetry(self, xs: frozenset[Pos]) -> bool:
        """Does the shape meet add_search_constraints(symmetry=True)?"""
        size = self.r.size
        if not xs:
            return False
        if any(min(c[a] for c in xs) != 0 for a in range(3)):
            return False
        if any(sum(2 * c[a] - (size[a] - 1) for c in xs) > 0 for a in range(3)):
            return False
        sums = [sum(c[a] for c in xs) for a in range(3)]
        return all(sums[a] <= sums[b] for a, b in ((0, 1), (1, 2), (0, 2)) if size[a] == size[b])

    def at_least(self, target, score: int) -> None:
        """Only accept solutions at least as good as `score`."""
        target.Add(self.score >= score)


# --------------------------------------------------------------------------- extraction

def _extract(model: _Model, val) -> tuple[Layout, Layout, set, list[Pos]]:
    """Build the layout from a solution; `val(var)` reads a variable's value."""
    r = model.r
    xs = {c for c, v in model.x.items() if val(v)}
    faces = {k for k, v in model.f.items() if val(v)}
    # The model may switch on cable cells it does not need; keep only cells that
    # hold a tunnel or carry routing flow.
    busy = {k[1] for k, v in model.h.items() if val(v)} | \
           {k[0] for k, v in model.h.items() if val(v)} | \
           {n for n, v in model.sink.items() if val(v)} | \
           {step(c, d) for c, d in faces}
    cables = {n for n, v in model.k.items() if val(v) and n in busy}
    root = min(xs)

    tree = {(n, o) for (n, o), p in model.par.items()
            if val(p) and n in cables and o in cables}
    exits = sorted(n for n in cables if n in model.par_sink and val(model.par_sink[n]))

    lay = Layout()
    for c in sorted(xs):
        lay.controller(c, powered=(c == root))
    freq = itertools.count(1)
    for n in sorted(cables):
        parts = {}
        for d in Direction:
            o = step(n, d)
            if o in xs:
                # R6: every controller face next to a cable cell holds a part.
                parts[d] = P2PTunnel(next(freq)) if (o, d.opposite) in faces else CableAnchor()
            elif o in cables and (n, o) not in tree and (o, n) not in tree and n < o:
                # Not a routing edge: cap it so AE2 cannot path through it.
                parts[d] = CableAnchor()
            elif o not in r.cell_set and n not in exits:
                # Only exit cells may touch the outside (where the tunnel network is).
                parts[d] = CableAnchor()
        has_bus_parts = any(not isinstance(p, CableAnchor) for p in parts.values())
        dense_ok = not has_bus_parts and model.cap[n] > model.profile.normal_channel_capacity
        lay.cable(n, CableType.DENSE if dense_ok else CableType.NORMAL, parts=parts)

    return lay, _harness(lay, r, exits), faces, exits


def _harness(lay: Layout, r: "_Region", exits: list[Pos]) -> Layout:
    """The build plus an ideal tunnel network: a port beyond every exit side."""
    harness = Layout()
    harness.blocks = dict(lay.blocks)
    for n in exits:
        for d in r.outward(n):
            harness.port(step(n, d))
    return harness


def _anchors(lay: Layout) -> int:
    return sum(1 for b in lay.blocks.values() if b.kind == "cable"
               for p in b.parts.values() if isinstance(p, CableAnchor))


def _repair(lay: Layout, r: "_Region", exits: list[Pos], profile: Profile) -> tuple[Layout, Layout, Report, int]:
    """Replace tunnels that get no channel with anchors until every tunnel works.
    Removing a tunnel only lowers load, so this terminates quickly."""
    removed = 0
    for _ in range(64):
        harness = _harness(lay, r, exits)
        report = check(harness, profile)
        dead = [t.key for t in report.sim.tunnels() if not t.output and not t.has_channel]
        if not dead:
            return lay, harness, report, removed
        for pos, side in dead:
            b = lay.get(pos)
            parts = dict(b.parts)
            parts[side] = CableAnchor()
            lay.cable(pos, b.cable, b.color, parts)
            removed += 1
    return lay, harness, report, removed


def _validated(report: Report) -> int:
    sim = report.sim
    n = 0
    for t in sim.tunnels():
        if t.output or not t.active or t.faces_controller is None:
            continue
        main = sim.grid(sim.graph.controller_nodes[t.faces_controller])
        if main.state == ONLINE and t.inner_grid != t.outer_grid:
            n += 1
    return n


# --------------------------------------------------------------------------- search

@dataclass
class _Incumbent:
    vec: list[int]        # value of every model variable, by index
    score: int
    source: str

    def xs(self, model: _Model) -> frozenset[Pos]:
        return frozenset(c for c, v in model.x.items() if self.vec[v.Index()])


class _Search:
    """Shared state for one optimize() call: progress events and the stop signal."""

    def __init__(self, model: _Model, spec: OptimizeSpec, progress, stop: threading.Event | None):
        self.model, self.spec = model, spec
        self.progress = progress
        self.stop = stop or threading.Event()
        self.started = time.monotonic()
        self.best_score: int | None = None

    def emit(self, kind: str, **data) -> None:
        if self.progress is not None:
            self.progress({"kind": kind, "time": time.monotonic() - self.started, **data})

    def tunnels_bound(self, score_bound: float) -> int | None:
        if not self.model.weight:
            return None
        # score = weight*tunnels - controllers, with controllers < weight
        return math.ceil(score_bound / self.model.weight - 1e-9)

    def solution(self, vec: list[int], score: int, phase: str) -> None:
        if self.best_score is not None and score <= self.best_score:
            return
        self.best_score = score
        m = self.model
        self.emit("solution", phase=phase, score=score, vec=vec,
                  tunnels=sum(vec[v.Index()] for v in m.f.values()),
                  controllers=sum(vec[v.Index()] for v in m.x.values()))

    def run(self, target, seconds: float, hint: _Incumbent | None = None, phase: str = "",
            report_bound: bool = False):
        """Solve `target`; returns (status, solver, vec, score)."""
        spec, model = self.spec, self.model
        if hint is not None:
            target.ClearHints()
            h = target.Proto().solution_hint
            h.vars.extend(range(len(hint.vec)))
            h.values.extend(hint.vec)
        solver = cp_model.CpSolver()
        if math.isfinite(seconds):
            solver.parameters.max_time_in_seconds = max(seconds, 0.1)
        solver.parameters.num_workers = spec.workers
        solver.parameters.random_seed = spec.random_seed
        solver.parameters.log_search_progress = spec.log
        for name, value in spec.solver_params:
            setattr(solver.parameters, name, value)
        if report_bound:
            solver.best_bound_callback = lambda b: self.emit("bound", phase=phase, tunnels=self.tunnels_bound(b))

        search = self

        class Callback(cp_model.CpSolverSolutionCallback):
            def OnSolutionCallback(self):
                vec = list(self.response_proto.solution)
                search.solution(vec, int(round(self.Value(model.score))), phase)

        done = threading.Event()

        def watch():
            while not done.is_set():
                if self.stop.wait(0.25):
                    solver.StopSearch()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            status = solver.Solve(target, Callback())
        finally:
            done.set()
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return status, solver, list(solver.ResponseProto().solution), int(round(solver.Value(model.score)))
        return status, solver, None, None


def _neighbourhood(rng: random.Random, size) -> tuple[str, list[Pos]]:
    cells = _cells(size)
    kind = rng.choice(("box", "box", "slab", "scatter"))
    if kind == "box":
        dims = [min(s, rng.randint(2, 4)) for s in size]
        lo = [rng.randint(0, s - d) for s, d in zip(size, dims)]
        free = [c for c in cells if all(lo[i] <= c[i] < lo[i] + dims[i] for i in range(3))]
        return f"box {dims[0]}x{dims[1]}x{dims[2]} at {tuple(lo)}", free
    if kind == "slab":
        axis = rng.randrange(3)
        t = min(size[axis], rng.randint(1, 2))
        at = rng.randint(0, size[axis] - t)
        free = [c for c in cells if at <= c[axis] < at + t]
        return f"slab {'xyz'[axis]}={at}..{at + t - 1}", free
    k = min(len(cells), max(8, len(cells) // 8))
    return f"{k} scattered cells", rng.sample(cells, k)


def _lns(search: _Search, inc: _Incumbent, budget: float, notes: list[str]) -> _Incumbent:
    model, spec = search.model, search.spec
    rng = random.Random(spec.random_seed)
    deadline = time.monotonic() + budget
    steps = improved = 0
    start_score = inc.score
    while (left := deadline - time.monotonic()) > 0.5 and not search.stop.is_set():
        label, free = _neighbourhood(rng, model.r.size)
        target = model.clone()
        free_set = set(free)
        model.fix_shape(target, inc.xs(model), [c for c in model.r.box if c not in free_set])
        model.at_least(target, inc.score)
        search.emit("phase", phase=f"LNS step {steps + 1}: {label}")
        _, _, vec, score = search.run(target, min(spec.lns_step, left), hint=inc, phase="LNS")
        steps += 1
        if vec is None:
            continue
        if score > inc.score:
            improved += 1
            if spec.log:
                print(f"LNS step {steps} ({label}): score {inc.score} -> {score}", flush=True)
        if score >= inc.score:
            inc = _Incumbent(vec, score, "LNS")
    notes.append(f"LNS: {steps} neighbourhoods, {improved} improvements"
                 + (f" (score {start_score} -> {inc.score})" if improved else ""))
    return inc


def _tunnels_of(model: _Model, inc: _Incumbent) -> int:
    return sum(inc.vec[v.Index()] for v in model.f.values())


def layout_from_vector(model: _Model, vec: list[int]) -> Layout:
    """The checkable layout (build + tunnel-network ports) for a solution vector."""
    lay, _, _, exits = _extract(model, lambda v: vec[v.Index()])
    return _harness(lay, model.r, exits)


# --------------------------------------------------------------------------- entry point

def optimize(profile: Profile = DEFAULT_PROFILE, spec: OptimizeSpec = OptimizeSpec(),
             progress=None, stop: threading.Event | None = None) -> Solution:
    """Seed shapes -> main CP-SAT solve -> optional LNS -> simulator check/repair.

    progress: optional callable receiving event dicts ("start", "phase",
      "solution" with the full solution vector, "bound") as the search runs.
    stop: optional threading.Event; setting it ends the search early and the
      best layout found so far is returned.
    time_limit may be math.inf for an open-ended main solve (stop it via `stop`).
    """
    if cp_model is None:
        raise OptimizerUnavailable("OR-Tools is not installed: pip install ortools")
    size = spec.size or profile.max_controller_size
    if any(s > m for s, m in zip(size, profile.max_controller_size)):
        raise ValueError(f"box {size} is larger than the profile's controller size limit "
                         f"{profile.max_controller_size}")
    model = _Model(spec, profile, size)
    search = _Search(model, spec, progress, stop)
    search.emit("start", model=model, size=size)
    notes: list[str] = []
    best: _Incumbent | None = None

    # 1. Warm-start seeds: fixed-shape solves are quick (only routing is free).
    uniform = not (spec.forbidden or spec.required or spec.blocked)
    shapes: list[tuple[str, frozenset[Pos]]] = []
    if spec.start:
        shapes.append(("start layout", frozenset(c for c in spec.start if c in model.r.box_set)))
    if spec.seed_patterns and uniform:
        shapes += [(f"lattice pattern {i + 1}", s)
                   for i, s in enumerate(lattice_shapes(size, limit=spec.seed_patterns))]
    for label, xs in shapes:
        if search.stop.is_set():
            break
        search.emit("phase", phase=f"warm start: {label}")
        target = model.clone()
        model.fix_shape(target, xs)
        _, _, vec, score = search.run(target, spec.seed_time, phase="warm start")
        if vec is not None:
            inc = _Incumbent(vec, score, label)
            notes.append(f"seed {label}: {_tunnels_of(model, inc)} tunnels")
            if best is None or inc.score > best.score:
                best = inc

    # 2. Main solve over everything, hinted with the best seed. Symmetry breaking
    #    stays on when the seed already satisfies it (e.g. a canonical start shape).
    status_name, upper = "NOT RUN", None
    if spec.time_limit > 0 and not search.stop.is_set():
        search.emit("phase", phase="main solve")
        target = model.clone()
        use_symmetry = spec.symmetry_breaking and (best is None or model.satisfies_symmetry(best.xs(model)))
        model.add_search_constraints(target, symmetry=use_symmetry)
        if best is not None:
            model.at_least(target, best.score)
        status, solver, vec, score = search.run(target, spec.time_limit, hint=best, phase="main solve",
                                                report_bound=True)
        status_name = solver.StatusName(status)
        if vec is not None and (best is None or score >= best.score):
            best = _Incumbent(vec, score, "main solve")
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            upper = search.tunnels_bound(solver.BestObjectiveBound())
    if spec.known_upper is not None:
        upper = spec.known_upper if upper is None else min(upper, spec.known_upper)
    if best is None:
        raise RuntimeError(f"no layout found ({status_name}); check forbidden/required/blocked cells")

    # 3. Large-neighbourhood search.
    if spec.lns_time > 0 and not search.stop.is_set():
        best = _lns(search, best, spec.lns_time, notes)
    if search.stop.is_set():
        notes.append("stopped early; this is the best layout found so far")

    # 4. Build, simulate, and repair anything AE2's pathing would not channel.
    search.emit("phase", phase="checking with the simulator")
    lay, _, faces, exits = _extract(model, lambda v: best.vec[v.Index()])
    lay, harness, report, removed = _repair(lay, model.r, exits, profile)
    tunnels_model = len(faces)
    validated = _validated(report)
    if removed:
        notes.append(f"model placed {tunnels_model} tunnels but AE2's pathing channels only {validated}; "
                     f"{removed} replaced by anchors")
    optimal = spec.target_tunnels is None and (
        (status_name == "OPTIMAL" and best.source == "main solve")
        or (upper is not None and tunnels_model >= upper))
    sol = Solution(
        layout=lay, harness=harness, report=report, status="OPTIMAL" if optimal else "FEASIBLE",
        controllers=len(lay.controllers()), tunnels_model=tunnels_model, tunnels_validated=validated,
        upper_bound=max(upper if upper is not None else validated, validated),
        anchors=_anchors(lay), cable_cells=sum(1 for b in lay.blocks.values() if b.kind == "cable"),
        exit_cells=exits, channels=validated * profile.dense_channel_capacity,
        tunnel_network_channels=2 * validated, rounds=1, seconds=time.monotonic() - search.started,
        notes=notes)
    search.emit("done", tunnels=sol.tunnels_validated, upper=sol.upper_bound, status=sol.status)
    return sol
