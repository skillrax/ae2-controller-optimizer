"""CP-SAT optimizer for main-controller + tunnel-cell layouts.

The model places, inside a box (default: the profile's max controller size):
  * controller blocks             x[c]
  * input tunnels on their faces  f[c, d]   (tunnel in cell c+d, facing c)
  * tunnel-network cable cells    k[n] = s[n] (smart) + d[n] (dense)
                                  (box gaps and a `margin`-block layer around it)
and routes every tunnel's tunnel-network channel to the outside with an
integer flow that respects cable capacities. Rules from MECHANICS.md section 5
(wiring rules W1-W4 from section 5b):

  R1  box size                    by construction
  R2  one connected structure     single-commodity flow from a root controller
  R3  cross rule                  at most one full opposite pair per controller
  R4  a face counts only with a tunnel on it      objective counts f
  R5  tunnel cells hold a normal cable            f <= k, cells next to a face are normal
  R6  uncapped faces merge networks               anchor_free (default): a smart cable
                                                  next to a controller face carries a
                                                  tunnel on it; otherwise an anchor
  R7  tunnel cells need an exit   flow conservation: channels must leave the cell
  R8  normal cable carries <= normal capacity     node capacity on the flow
  R9-R11  hold for generated builds (no nesting, separate powered networks)
  W1-W4  coloured groups of <= normal capacity, fluix dense trunks of <= dense
         capacity that never touch controllers, single attachments: see section 5b

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


# Bump when a change to the model can change which build is optimal (the shipped
# library's proofs record it). 1: anchored wiring; 2: coloured groups + dense trunks.
MODEL_VERSION = 2

# The optimizer's limit, separate from what a config allows (Extended Life goes up to
# 63). 7x7x7 is already far beyond proving; past 9 per side the model gets so big
# that searches don't finish usefully, so bigger boxes are refused.
MAX_BOX_SIDE = 9


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
    # Layers of cable space around the box. 2 adds a layer where dense collectors
    # can merge groups into fuller trunks, but the bigger model searches much worse
    # (7x7x7: 660 vs 701 tunnels in 15 min) for only 1-2 fewer trunks, so 1.
    margin: int = 1
    # Every controller face next to a cable carries a tunnel, so the build needs no
    # cable anchors at all. Off: unused faces next to cables get anchors instead.
    anchor_free: bool = True
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
    # Final pass: keep controllers and tunnels, fewest trunks then fewest cable cells
    # (seconds; 0 = off; None = scale with the box: about 10 s for 3x3x3 up to 60 s for 7x7x7).
    # It also runs when the search is stopped, so saved results are always tidied.
    polish_time: float | None = None
    # A proven upper bound on tunnels from an earlier run of the same spec. Proven
    # bounds stay valid, so a resumed run reports min(this, its own bound).
    known_upper: int | None = None
    # Solve the relaxed model (routing="flow") for a fast upper bound: alongside the main
    # solve with 12+ workers, before it with 6-11, not with fewer. A build that reaches
    # the bound is proven optimal, so the main solve stops there (then minimizes
    # controllers briefly). 16 workers: 3x4x4 proven in 33 s instead of 104 s, 4x4x4
    # from scratch in 31 min (the full model's own bound stalls at 172).
    relaxed_bound: bool = True
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
    def __init__(self, size, exterior: bool, margin: int = 1):
        self.size = size
        self.margin = margin if exterior else 0
        self.box = _cells(size)
        self.box_set = set(self.box)
        if exterior:
            g = margin
            m = [(x - g, y - g, z - g) for x, y, z in _cells((size[0] + 2 * g, size[1] + 2 * g, size[2] + 2 * g))]
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
    def __init__(self, spec: OptimizeSpec, profile: Profile, size, routing: str = "forest"):
        self.spec, self.profile = spec, profile
        self.routing = routing
        self.r = r = _Region(size, spec.exterior_tunnels, spec.margin)
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

        # cable cells: k = smart (s, normal capacity, holds tunnels) or dense (d)
        self.k, self.s, self.d = {}, {}, {}
        for n in r.cells:
            v = m.NewBoolVar(f"k{n}")
            sv, dv = m.NewBoolVar(f"s{n}"), m.NewBoolVar(f"d{n}")
            m.Add(v == sv + dv)
            if n in blocked or (r.in_box(n) and not spec.interior_tunnels):
                m.Add(v == 0)
            if X(n) is not None:
                m.AddImplication(v, X(n).Not())
            # A dense cable next to a controller would connect to it (no anchors).
            for d in Direction:
                if X(step(n, d)) is not None:
                    m.AddImplication(dv, X(step(n, d)).Not())
            self.k[n], self.s[n], self.d[n] = v, sv, dv

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
                m.AddImplication(v, self.s[n])
                if spec.anchor_free:
                    # a smart cable next to a controller face carries a tunnel on it
                    m.AddBoolOr([self.x[c].Not(), self.s[n].Not(), v])
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

        # R5/R7/R8: tunnel-network routing flow from tunnel cells to outside the region.
        # Routing is a forest: every cable cell has exactly one parent (a neighbouring
        # cable cell, or the outside). A smart cell's subtree of smart cells is a
        # "group" (at most the normal capacity in total); a dense cell's tree is a
        # trunk (at most the dense capacity). Wiring rules below make every group
        # touch dense cable only at its own attachment and keep trunks apart, and
        # extraction gives touching groups different colours, so whatever route
        # AE2's pathing picks, no cable can exceed its capacity. No cable anchors.
        #
        # routing="flow" is a relaxation for bounds only: the same capacities, but a
        # plain flow between cable cells (no parents, no wiring rules). Every real
        # layout is a solution of it, so its optimum bounds the real one, and it
        # solves far faster (3x4x4: bound 126 proven in 28 s vs 332 s).
        self.h, self.par = {}, {}
        forest = routing == "forest"
        for n in r.cells:
            for d in Direction:
                o = step(n, d)
                if o in r.cell_set:
                    v = m.NewIntVar(0, dense, f"h{n}{d.name}")
                    self.h[(n, o)] = v
                    if forest:
                        p = m.NewBoolVar(f"par{n}{d.name}")
                        m.AddImplication(p, self.k[o])
                        m.Add(v <= dense * p)
                        self.par[(n, o)] = p
                    else:
                        m.Add(v <= dense * self.k[n])
                        m.Add(v <= dense * self.k[o])
        for (n, o), p in self.par.items():
            if n < o:
                m.AddBoolOr([p.Not(), self.par[(o, n)].Not()])
                # dense next to dense only as parent/child (trunks never touch)
                m.AddBoolOr([self.d[n].Not(), self.d[o].Not(), p, self.par[(o, n)]])
            # smart next to dense only at the smart cell's own attachment
            m.AddBoolOr([self.s[n].Not(), self.d[o].Not(), p])
            # a dense cell's parent is dense (or the outside)
            m.AddBoolOr([self.d[n].Not(), p.Not(), self.d[o]])
        self.sink, self.par_sink = {}, {}
        for n in r.cells:
            outs = r.outward(n)
            if outs:
                v = m.NewIntVar(0, dense * len(outs), f"sink{n}")
                self.sink[n] = v
                if forest:
                    p = m.NewBoolVar(f"parsink{n}")
                    m.Add(v <= dense * p)
                    self.par_sink[n] = p
                else:
                    m.Add(v <= dense * self.k[n])
        for n in r.cells if forest else ():
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
                m.Add(t + sum(inn) <= normal * self.s[n] + dense * self.d[n])
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
        if not forest:
            self.weight = 1    # a bound on tunnels; controllers don't matter
            self.score = self.tunnels
            m.Maximize(self.score)
        elif spec.target_tunnels is not None:
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

DYES = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
        "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black")


def _colour_groups(groups: dict, adjacent: dict) -> dict:
    """Give every smart group a dye so touching groups differ (largest degree
    first, smallest free colour). Returns {group: dye or None if it ran out}."""
    colour = {}
    for g in sorted(groups, key=lambda g: (-len(adjacent.get(g, ())), g)):
        used = {colour[o] for o in adjacent.get(g, ()) if o in colour}
        colour[g] = next((c for c in DYES if c not in used), None)
    return colour


def _extract(model: _Model, val) -> tuple[Layout, Layout, set, list[Pos]]:
    """Build the layout from a solution; `val(var)` reads a variable's value.

    Smart cells become normal smart cable coloured per group; dense cells become
    fluix dense smart cable. Touching groups get different dyes, so the only cable
    anchors left are on controller faces without a tunnel (none when anchor_free)."""
    r = model.r
    xs = {c for c, v in model.x.items() if val(v)}
    faces = {k for k, v in model.f.items() if val(v)}
    # Keep only cables on a tunnel's route to the outside: start at every tunnel
    # cell and follow parents to an exit. The model may switch on other cells, and
    # may even leave a loop of parents circulating flow that no tunnel uses
    # (conservation allows it); neither is part of the build.
    all_parent = {n: o for (n, o), p in model.par.items() if val(p)}
    roots = {n for n, p in model.par_sink.items() if val(p)}
    cables = set()
    for start in {step(c, d) for c, d in faces}:
        n, path = start, []
        while n not in cables and n not in path:
            path.append(n)
            if n in roots or n not in all_parent:
                break
            n = all_parent[n]
        cables.update(path)
    dense = {n for n in cables if val(model.d[n])}
    root = min(xs)
    parent = {n: o for n, o in all_parent.items() if n in cables and o in cables}
    exits = sorted(n for n in cables if n in roots)

    # Smart groups: follow parents while they stay smart.
    def group_of(n):
        seen = set()
        while n in parent and parent[n] not in dense and n not in seen:
            seen.add(n)
            n = parent[n]
        return n
    group = {n: group_of(n) for n in cables if n not in dense}
    adjacent: dict = {}
    for n in group:
        for d in Direction:
            o = step(n, d)
            if o in group and group[o] != group[n]:
                adjacent.setdefault(group[n], set()).add(group[o])
    colour = _colour_groups(set(group.values()), adjacent)

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
            elif n in group and o in group and group[o] != group[n] \
                    and (colour[group[n]] is None or colour[group[o]] is None) and n < o:
                parts[d] = CableAnchor()   # out of dyes (very rare): separate with an anchor
        if n in dense:
            lay.cable(n, CableType.DENSE, "transparent", parts)
        else:
            lay.cable(n, CableType.NORMAL, colour[group[n]] or "transparent", parts)

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
        self.best_tunnels: int | None = None
        self.relaxed_upper: int | None = None
        self.reached = threading.Event()   # a build has reached the relaxed bound

    def relaxed(self, upper: int) -> None:
        """A proven bound from the relaxed model."""
        if self.relaxed_upper is not None and upper >= self.relaxed_upper:
            return
        self.relaxed_upper = upper
        self.emit("bound", phase="relaxed bound", tunnels=upper)
        if self.best_tunnels is not None and self.best_tunnels >= upper:
            self.reached.set()

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
        tunnels = sum(vec[v.Index()] for v in m.f.values())
        self.best_tunnels = max(tunnels, self.best_tunnels or 0)
        self.emit("solution", phase=phase, score=score, vec=vec, tunnels=tunnels,
                  controllers=sum(vec[v.Index()] for v in m.x.values()))
        if self.relaxed_upper is not None and tunnels >= self.relaxed_upper:
            self.reached.set()

    def run(self, target, seconds: float, hint: _Incumbent | None = None, phase: str = "",
            report_bound: bool = False, stop: threading.Event | None = None,
            also_stop: tuple[threading.Event, ...] = (), workers: int | None = None):
        """Solve `target`; returns (status, solver, vec, score). `stop` replaces the
        search's own stop signal (polish runs after a stop, on its own signal);
        `also_stop` events end this solve too, without stopping the search."""
        spec, model = self.spec, self.model
        stop = stop or self.stop
        if hint is not None:
            target.ClearHints()
            h = target.Proto().solution_hint
            h.vars.extend(range(len(hint.vec)))
            h.values.extend(hint.vec)
        solver = cp_model.CpSolver()
        if math.isfinite(seconds):
            solver.parameters.max_time_in_seconds = max(seconds, 0.1)
        solver.parameters.num_workers = workers or spec.workers
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
                if stop.wait(0.25) or any(e.is_set() for e in also_stop):
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


class _RelaxedBound:
    """Solves the relaxed model (routing="flow") in a thread, reporting its bound to
    the search. Every real layout is a solution of it, so the bound is proven."""

    def __init__(self, search: _Search, profile: Profile, size, workers: int):
        self.search = search
        self.model = _Model(search.spec, profile, size, routing="flow")
        self.workers = workers
        self.finished = threading.Event()   # proved its own optimum (or failed)
        self.quit = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        search, model = self.search, self.model
        try:
            target = model.clone()
            model.add_search_constraints(target, symmetry=search.spec.symmetry_breaking)
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = self.workers
            solver.parameters.random_seed = search.spec.random_seed
            solver.best_bound_callback = lambda b: search.relaxed(math.floor(b + 1e-9))
            done = threading.Event()

            def watch():
                while not done.is_set():
                    if self.quit.wait(0.25) or search.stop.is_set():
                        solver.StopSearch()
                        return

            threading.Thread(target=watch, daemon=True).start()
            try:
                status = solver.Solve(target)
            finally:
                done.set()
            if status == cp_model.OPTIMAL:
                search.relaxed(int(round(solver.ObjectiveValue())))
            elif status == cp_model.INFEASIBLE:
                search.relaxed(0)
        finally:
            self.finished.set()

    def close(self) -> None:
        self.quit.set()
        self.thread.join()


def _main_solve(search: _Search, spec: OptimizeSpec, profile: Profile, size, best: _Incumbent | None):
    """The main solve, with the relaxed bound alongside it (when on). Returns
    (status name, best incumbent, upper bound or None, finished: optimal or reached)."""
    model = search.model
    relaxed = None
    workers = spec.workers
    started = time.monotonic()
    # CP-SAT only runs its LP-based workers (what makes the relaxed model fast) with 6+
    # workers: with fewer it stalls (3x3x4: no proof in 90 s on 4, 3 s on 6). So it runs
    # alongside the main solve given 12+ workers, first on all of them given 6-11, and not
    # at all below that.
    if spec.relaxed_bound and spec.target_tunnels is None and spec.workers >= 6:
        if spec.workers >= 12:
            relaxed = _RelaxedBound(search, profile, size, max(6, spec.workers // 2))
            workers = spec.workers - relaxed.workers
        else:
            search.emit("phase", phase="relaxed bound")
            pre = _RelaxedBound(search, profile, size, spec.workers)
            pre.finished.wait(min(60.0, spec.time_limit / 4))
            pre.close()
            search.emit("phase", phase="main solve")
    use_symmetry = spec.symmetry_breaking and (best is None or model.satisfies_symmetry(best.xs(model)))
    status_name, upper, finished = "NOT RUN", None, False
    try:
        while True:
            left = spec.time_limit - (time.monotonic() - started)
            target = model.clone()
            model.add_search_constraints(target, symmetry=use_symmetry)
            if best is not None:
                model.at_least(target, best.score)
            if search.relaxed_upper is not None:
                target.Add(model.tunnels <= search.relaxed_upper)
            stops = (search.reached,) + ((relaxed.finished,) if relaxed and not relaxed.finished.is_set() else ())
            status, solver, vec, score = search.run(target, left, hint=best, phase="main solve", report_bound=True,
                                                    also_stop=stops, workers=workers)
            status_name = solver.StatusName(status)
            if vec is not None and (best is None or score >= best.score):
                best = _Incumbent(vec, score, "main solve")
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                b = search.tunnels_bound(solver.BestObjectiveBound())
                upper = b if upper is None else min(upper, b)
            finished = status == cp_model.OPTIMAL or search.reached.is_set()
            # The relaxed model proved its optimum: go on with every worker and its bound
            # as a constraint (the main solve can't take new constraints while it runs).
            if (not finished and relaxed is not None and relaxed.finished.is_set() and workers < spec.workers
                    and not search.stop.is_set() and spec.time_limit - (time.monotonic() - started) > 1):
                workers = spec.workers
                search.emit("phase", phase="main solve (relaxed bound proved; all workers)")
                continue
            break
    finally:
        if relaxed is not None:
            relaxed.close()
    if search.relaxed_upper is not None:
        upper = search.relaxed_upper if upper is None else min(upper, search.relaxed_upper)
    if search.reached.is_set() and not search.stop.is_set():
        # Tunnels are proven; the fewest controllers for that many isn't yet. Fix the
        # tunnels and minimize controllers, for at most a quarter of the time so far.
        left = spec.time_limit - (time.monotonic() - started)
        budget = min(left, max(10.0, (time.monotonic() - started) / 4))
        target = model.clone()
        model.add_search_constraints(target, symmetry=use_symmetry)
        target.Add(model.tunnels == upper)
        model.at_least(target, best.score)
        search.emit("phase", phase="fewest controllers for the proven tunnels")
        status, solver, vec, score = search.run(target, budget, hint=best, phase="main solve")
        if vec is not None and score >= best.score:
            best = _Incumbent(vec, score, "main solve")
        status_name = solver.StatusName(status)   # OPTIMAL here: controllers proven minimal too
    return status_name, best, upper, finished


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


def check_box(size, profile: Profile) -> None:
    """Refuse boxes over the profile's controller limit or the optimizer's limit."""
    if any(s > m for s, m in zip(size, profile.max_controller_size)):
        raise ValueError(f"box {'x'.join(map(str, size))} is larger than the profile's controller size limit "
                         f"{'x'.join(map(str, profile.max_controller_size))}")
    if any(s > MAX_BOX_SIDE for s in size):
        raise ValueError(f"box {'x'.join(map(str, size))} is too big to optimize: the limit is {MAX_BOX_SIDE} per side. "
                         "Bigger controllers are legal in Extended Life, but the search can't do anything useful "
                         "with them; repeat a smaller optimized build instead.")


def _polish_seconds(spec: OptimizeSpec, model: _Model) -> float:
    if spec.polish_time is not None:
        return spec.polish_time
    return min(60.0, max(10.0, len(model.k) / 12))


def _polish_target(model: _Model, xs, faces):
    """Fixed controllers and tunnel faces; fewest trunks first, then fewest cables."""
    target = model.clone()
    model.fix_shape(target, xs)
    for key, v in model.f.items():
        target.Add(v == int(key in faces))
    target.Minimize((len(model.k) + 1) * sum(model.par_sink.values()) + sum(model.k.values()))
    return target


def _polish(search: _Search, best: _Incumbent, seconds: float, notes: list[str]) -> _Incumbent:
    model = search.model
    search.emit("phase", phase="polish: fewest trunks, then fewest cables")
    faces = {k for k, v in model.f.items() if best.vec[v.Index()]}
    target = _polish_target(model, best.xs(model), faces)
    _, _, vec, score = search.run(target, seconds, hint=best, phase="polish", stop=threading.Event())
    if vec is None or score != best.score:
        return best

    def count(v_, vars_):
        return sum(v_[x.Index()] for x in vars_)
    b_t, a_t = count(best.vec, model.par_sink.values()), count(vec, model.par_sink.values())
    b_c, a_c = count(best.vec, model.k.values()), count(vec, model.k.values())
    if (a_t, a_c) < (b_t, b_c):
        notes.append(f"polish: trunks {b_t} -> {a_t}, cable cells {b_c} -> {a_c}")
    return _Incumbent(vec, score, best.source)


def polish_layout(profile: Profile, spec: OptimizeSpec, layout: Layout, seconds: float | None = None,
                  workers: int | None = None, stop: threading.Event | None = None) -> tuple[Layout, dict]:
    """Tidy the wiring of a saved layout (e.g. a run's best-so-far checkpoint):
    same controllers and tunnels, fewest trunks, then fewest cables.

    Returns (harness layout, info). If the layout cannot be re-wired under the
    model's rules, or the result channels fewer tunnels, the input comes back
    unchanged with info["changed"] False."""
    if cp_model is None:
        raise OptimizerUnavailable("OR-Tools is not installed: pip install ortools")
    from dataclasses import replace as _replace
    size = spec.size or profile.max_controller_size
    if workers is not None:
        spec = _replace(spec, workers=workers)
    model = _Model(spec, profile, size)
    xs = frozenset(c for c in layout.controllers() if c in model.r.box_set)
    faces = set()
    for pos, b in layout.blocks.items():
        if b.kind != "cable":
            continue
        for d, part in b.parts.items():
            if isinstance(part, P2PTunnel) and not part.output and (step(pos, d), d.opposite) in model.f:
                if step(pos, d) in xs:
                    faces.add((step(pos, d), d.opposite))

    def stats(lay: Layout) -> dict:
        report = check(lay, profile)
        cables = [b for b in lay.blocks.values() if b.kind == "cable"]
        return {"tunnels": _validated(report), "exits": sum(1 for b in lay.blocks.values() if b.kind == "port"),
                "dense": sum(1 for b in cables if b.cable is CableType.DENSE), "cables": len(cables)}

    before = stats(layout)
    info = {"changed": False, "before": before, "after": before}
    target = _polish_target(model, xs, faces)
    search = _Search(model, spec, None, None)
    seconds = _polish_seconds(spec, model) if seconds is None else seconds
    _, _, vec, _ = search.run(target, seconds, stop=stop or threading.Event())
    if vec is None:
        return layout, info
    lay, _, _, exits = _extract(model, lambda v: vec[v.Index()])
    lay, harness, _, _ = _repair(lay, model.r, exits, profile)
    after = stats(harness)
    if after["tunnels"] < before["tunnels"]:
        return layout, info
    info.update(changed=True, after=after)
    return harness, info


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
    check_box(size, profile)
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
    status_name, upper, finished = "NOT RUN", None, False
    if spec.time_limit > 0 and not search.stop.is_set():
        search.emit("phase", phase="main solve")
        status_name, best, upper, finished = _main_solve(search, spec, profile, size, best)
        if search.reached.is_set():
            notes.append(f"tunnels proven by the relaxed bound ({search.relaxed_upper})"
                         + ("" if status_name == "OPTIMAL" else "; the controller count is not proven minimal"))
    if spec.known_upper is not None:
        upper = spec.known_upper if upper is None else min(upper, spec.known_upper)
    if best is None:
        raise RuntimeError(f"no layout found ({status_name}); check forbidden/required/blocked cells")

    # 3. Large-neighbourhood search.
    proven = upper is not None and _tunnels_of(model, best) >= upper
    if spec.lns_time > 0 and not search.stop.is_set() and not proven:
        best = _lns(search, best, spec.lns_time, notes)
    if search.stop.is_set():
        notes.append("stopped early; this is the best layout found so far")

    # 3b. Polish: same controllers and tunnels, fewest trunks then fewest cables.
    #     Cannot change the score. Runs after a stop too (on its own stop signal),
    #     so a stopped run still saves tidy wiring.
    polish = _polish_seconds(spec, model)
    if polish > 0:
        seconds = min(polish, max(2.0, time.monotonic() - search.started))
        best = _polish(search, best, seconds, notes)

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
