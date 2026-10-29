"""Independent exact model of the AE2 controller + P2P tunnel problem.

Written from MECHANICS.md (R1-R11, W1-W5) and docs/RESEARCH.md section 1 (D1-D4) only,
without reading the project's own CP-SAT model. It maximises the number of working input
tunnels around an a x b x c controller box under the default config (normal 8, dense 32),
coloured anchor-free wiring, margin 1, tunnels inside or around the box.

Interpretation (see docs/INDEPENDENT_CHECK.md for the reasoning):

* Cells: the box B = [0,a) x [0,b) x [0,c) and the region R = B grown by one cell in every
  direction, edges and corners included. Each cell of R is empty, smart (normal) cable or
  dense cable; cells of B may instead be a controller.
* Controllers (R1-R3): at least one, face-connected, and no controller has controller
  pairs on two or more axes (the cross rule).
* Tunnels (R4, W5): every face between a controller and a smart cable carries an input
  tunnel; that is the objective. Dense cable never touches a controller (R8, W3).
* Smart groups (W1, W2): every smart cell has exactly one outlet: a smart parent, an
  attachment to exactly one dense neighbour (it is then the group's only cell touching
  dense cable), or an exit to the tunnel network beyond the margin (only for cells
  outside the box, D3). The parent links form trees; the tunnels routed through any
  cell, its own included, fit in 8, so each tree (group) holds at most 8.
* Dense trunks (W3): dense cells form trees by parent links, every pair of touching dense
  cells is a parent/child pair (an induced tree, so trunks never touch), the root exits
  beyond the margin (so it lies outside the box) and a trunk carries at most 32.
* Colours (W4): touching trees get different dyes. This is not encoded; a 16-colouring
  of the touching graph is found after the solve and the build is rejected without one.

Proofs use mirror and axis-swap symmetry breaking and the face-count cut
T + 2 E_X <= 6n, E_X >= n - 1 (validity arguments in docs/INDEPENDENT_CHECK.md).

Run:  python -m lab.independent 2 2 2 [--time 600] [--out build.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

from ortools.sat.python import cp_model

NORMAL, DENSE = 8, 32
DIRS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
DYES = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
        "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black")


def add(p, d):
    return (p[0] + d[0], p[1] + d[1], p[2] + d[2])


class Model:
    def __init__(self, a: int, b: int, c: int, symmetry: bool = True, cut: bool = True,
                 cable_cut: bool = False):
        self.dims = (a, b, c)
        self.box = [p for p in itertools.product(range(a), range(b), range(c))]
        self.region = [p for p in itertools.product(range(-1, a + 1), range(-1, b + 1), range(-1, c + 1))]
        boxset, regset = set(self.box), set(self.region)
        self.inbox = boxset.__contains__
        self.nbr = {p: [q for q in (add(p, d) for d in DIRS) if q in regset] for p in self.region}
        m = self.m = cp_model.CpModel()

        # Cell contents.
        self.x = {p: m.NewBoolVar(f"x{p}") for p in self.box}
        self.s = {p: m.NewBoolVar(f"s{p}") for p in self.region}
        self.d = {p: m.NewBoolVar(f"d{p}") for p in self.region}
        for p in self.region:
            m.Add(self.s[p] + self.d[p] + self.x.get(p, 0) <= 1)

        # ---------------------------------------------------------------- controllers
        m.Add(sum(self.x.values()) >= 1)
        # Cross rule (R3): a controller may have a full opposite pair on at most one axis.
        for p in self.box:
            pairs = []
            for ax in range(3):
                e = tuple(1 if i == ax else 0 for i in range(3))
                lo, hi = add(p, tuple(-v for v in e)), add(p, e)
                if lo in boxset and hi in boxset:
                    pr = m.NewBoolVar("")
                    m.AddBoolAnd([self.x[lo], self.x[hi]]).OnlyEnforceIf(pr)
                    m.AddBoolOr([self.x[lo].Not(), self.x[hi].Not()]).OnlyEnforceIf(pr.Not())
                    pairs.append(pr)
            if len(pairs) >= 2:
                m.Add(sum(pairs) <= 1).OnlyEnforceIf(self.x[p])
        # Face connectivity (R2): one controller is the source; it sends one unit of flow
        # to every other controller along controller-controller faces.
        n = len(self.box)
        src = {p: m.NewBoolVar("") for p in self.box}
        m.AddExactlyOne(src.values())
        supply = {}
        for p in self.box:
            m.AddImplication(src[p], self.x[p])
            supply[p] = m.NewIntVar(0, n, "")
            m.Add(supply[p] <= n * src[p])
        cf = {}
        for p in self.box:
            for q in self.nbr[p]:
                if q in boxset:
                    v = cf[p, q] = m.NewIntVar(0, n - 1, "")
                    m.Add(v <= (n - 1) * self.x[p])
                    m.Add(v <= (n - 1) * self.x[q])
        for p in self.box:
            out = sum(cf[p, q] for q in self.nbr[p] if q in boxset)
            inn = sum(cf[q, p] for q in self.nbr[p] if q in boxset)
            m.Add(out - inn == supply[p] - self.x[p])

        # ---------------------------------------------------------------- tunnels
        self.y = {}   # (controller cell, smart cell) -> tunnel on that face
        for q in self.region:
            for p in self.nbr[q]:
                if p in boxset:
                    t = self.y[p, q] = m.NewBoolVar("")
                    m.AddMultiplicationEquality(t, [self.x[p], self.s[q]])
                    # dense never touches a controller
                    m.AddBoolOr([self.x[p].Not(), self.d[q].Not()])
        self.tun = {q: sum(self.y[p, q] for p in self.nbr[q] if p in boxset) for q in self.region}
        self.T = sum(self.y.values())

        # ---------------------------------------------------------------- smart groups
        self.sp = {}    # smart parent link q -> r (bool), with flow gs
        gs = {}
        self.att = {}   # smart q attached to dense r
        ga = {}
        self.sx = {}    # smart exit (q outside the box)
        sxf = {}
        for q in self.region:
            for r in self.nbr[q]:
                v = self.sp[q, r] = m.NewBoolVar("")
                m.AddImplication(v, self.s[q])
                m.AddImplication(v, self.s[r])
                g = gs[q, r] = m.NewIntVar(0, NORMAL, "")
                m.Add(g <= NORMAL * v)
                # every smart-dense contact is an attachment of the smart cell
                w = self.att[q, r] = m.NewBoolVar("")
                m.AddMultiplicationEquality(w, [self.s[q], self.d[r]])
                g = ga[q, r] = m.NewIntVar(0, NORMAL, "")
                m.Add(g <= NORMAL * w)
            if not self.inbox(q):
                v = self.sx[q] = m.NewBoolVar("")
                m.AddImplication(v, self.s[q])
                sxf[q] = m.NewIntVar(0, NORMAL, "")
                m.Add(sxf[q] <= NORMAL * v)
        for q in self.region:
            outlets = [self.sp[q, r] for r in self.nbr[q]] + [self.att[q, r] for r in self.nbr[q]]
            if q in self.sx:
                outlets.append(self.sx[q])
            m.Add(sum(outlets) == self.s[q])   # exactly one outlet; at most one dense contact
            out = (sum(gs[q, r] for r in self.nbr[q]) + sum(ga[q, r] for r in self.nbr[q])
                   + sxf.get(q, 0))
            inn = sum(gs[r, q] for r in self.nbr[q])
            m.Add(out == self.tun[q] + inn)
            m.Add(out >= self.s[q])   # every smart cell carries something (no idle loops)

        # ---------------------------------------------------------------- dense trunks
        self.dp = {}
        gd = {}
        self.dx = {}
        dxf = {}
        for p in self.region:
            for r in self.nbr[p]:
                v = self.dp[p, r] = m.NewBoolVar("")
                m.AddImplication(v, self.d[p])
                m.AddImplication(v, self.d[r])
                g = gd[p, r] = m.NewIntVar(0, DENSE, "")
                m.Add(g <= DENSE * v)
            if not self.inbox(p):
                v = self.dx[p] = m.NewBoolVar("")
                m.AddImplication(v, self.d[p])
                dxf[p] = m.NewIntVar(0, DENSE, "")
                m.Add(dxf[p] <= DENSE * v)
        for p in self.region:
            m.Add(sum(self.dp[p, r] for r in self.nbr[p]) + self.dx.get(p, 0) == self.d[p])
            out = sum(gd[p, r] for r in self.nbr[p]) + dxf.get(p, 0)
            inn = sum(gd[r, p] for r in self.nbr[p]) + sum(ga[r, p] for r in self.nbr[p])
            m.Add(out == inn)
            m.Add(out >= self.d[p])
            for r in self.nbr[p]:
                if p < r:   # touching dense cells are parent and child (induced tree)
                    m.AddBoolOr([self.d[p].Not(), self.d[r].Not(), self.dp[p, r], self.dp[r, p]])

        # ---------------------------------------------------------------- redundant cut
        # Each controller face faces another controller, a tunnel or nothing useful, so
        # T + 2 E_X <= 6 n; and a connected structure has E_X >= n - 1. Both hold for every
        # solution, so this only helps the bound.
        if cut:
            ex = []
            for p in self.box:
                for q in self.nbr[p]:
                    if q in boxset and p < q:
                        e = m.NewBoolVar("")
                        m.AddMultiplicationEquality(e, [self.x[p], self.x[q]])
                        ex.append(e)
            nx = sum(self.x.values())
            m.Add(self.T + 2 * sum(ex) <= 6 * nx)
            m.Add(sum(ex) >= nx - 1)
        # Cable side: a box cable cell's six faces hold its tunnels, its tree links to
        # other box cells (each on its own face) and its box-surface faces. Two cells that
        # are each other's parent form a rootless loop, which carries nothing and can be
        # deleted without changing T, so forbidding it keeps an optimum.
        if cable_cut:
            for q in self.box:
                cab = self.s[q] + self.d[q]
                links, surf = [], 0
                for r in self.nbr[q]:
                    if r in boxset:
                        pair = [self.sp[q, r], self.sp[r, q], self.att[q, r], self.att[r, q],
                                self.dp[q, r], self.dp[r, q]]
                        if q < r:
                            m.Add(sum(pair) <= 1)
                        links += [self.sp[q, r], self.sp[r, q], self.att[q, r], self.att[r, q],
                                  self.dp[q, r], self.dp[r, q]]
                    else:
                        surf += 1
                m.Add(self.tun[q] + sum(links) + surf * cab <= 6 * cab)

        if symmetry:
            self._break_symmetry()
        m.Maximize(self.T)

    def _break_symmetry(self):
        """Mirrors: for each axis, at least as many controllers in the low half as in the
        high half. Any build has a mirror image that satisfies this (mirror each axis whose
        high half is heavier); mirroring one axis leaves the other axes' half-counts alone.

        Axis swaps, for axes of equal length i < j taken consecutively: at least as many
        controllers with p_i < p_j as with p_i > p_j. After mirroring, permute the equal
        axes along a Hamiltonian path of the tournament "i beats j if #(p_i<p_j) >=
        #(p_i>p_j)" (every tournament has one). Swapping equal-length axes maps the mirror
        constraints onto each other, so they still hold.

        Every rule in the model is invariant under mirrors and axis permutations, so each
        image is a build with the same count."""
        m = self.m
        for ax in range(3):
            L = self.dims[ax]
            lo = [self.x[p] for p in self.box if p[ax] < L // 2]
            hi = [self.x[p] for p in self.box if p[ax] >= L - L // 2]
            if lo:
                m.Add(sum(lo) >= sum(hi))
        for L in set(self.dims):
            axes = [i for i in range(3) if self.dims[i] == L]
            for i, j in zip(axes, axes[1:]):
                less = [self.x[p] for p in self.box if p[i] < p[j]]
                more = [self.x[p] for p in self.box if p[i] > p[j]]
                m.Add(sum(less) >= sum(more))

    # ------------------------------------------------------------------ decode
    def decode(self, solver: cp_model.CpSolver) -> dict:
        V = lambda v: solver.Value(v) if not isinstance(v, int) else v
        ctrl = [p for p in self.box if V(self.x[p])]
        smart = {q for q in self.region if V(self.s[q])}
        dense = {p for p in self.region if V(self.d[p])}
        sparent, droot_of = {}, {}
        for (q, r), v in self.sp.items():
            if V(v):
                sparent[q] = ("s", r)
        for (q, r), v in self.att.items():
            if V(v):
                sparent[q] = ("d", r)
        for q, v in self.sx.items():
            if V(v):
                sparent[q] = ("exit", None)
        dparent = {}
        for (p, r), v in self.dp.items():
            if V(v):
                dparent[p] = r
        for p, v in self.dx.items():
            if V(v):
                dparent[p] = None
        tunnels = {q: [p for p in self.nbr[q] if p in self.x and V(self.y[p, q])] for q in smart}
        return dict(dims=self.dims, T=int(solver.ObjectiveValue()), controllers=ctrl, smart=smart,
                    dense=dense, sparent=sparent, dparent=dparent, tunnels=tunnels)


# ---------------------------------------------------------------------- build output

def group_roots(sol):
    """Follow parent links to each smart cell's tree root; drop idle cycles (never carry
    tunnels, see the model's flow argument) and report them."""
    root, dropped = {}, set()
    for q in sol["smart"]:
        seen, cur = [], q
        while True:
            kind, r = sol["sparent"][cur]
            if kind != "s":
                break
            if cur in seen:
                cur = None
                break
            seen.append(cur)
            cur = r
        if cur is None:
            dropped.add(q)
        else:
            root[q] = cur
    assert all(not sol["tunnels"][q] for q in dropped), "tunnels on a rootless smart cycle"
    return root, dropped


def dense_alive(sol):
    alive = set()
    for p in sol["dense"]:
        seen, cur = set(), p
        while cur is not None and cur not in seen:
            seen.add(cur)
            cur = sol["dparent"][cur]
        if cur is None:
            alive.add(p)
    return alive


def colour_groups(sol, root):
    """Greedy (largest-degree first) colouring of the touching graph of smart trees."""
    adj = {}
    for q in root:
        adj.setdefault(root[q], set())
        for d in DIRS:
            r = add(q, d)
            if r in root and root[r] != root[q]:
                adj[root[q]].add(root[r])
    colour = {}
    for g in sorted(adj, key=lambda g: -len(adj[g])):
        used = {colour[h] for h in adj[g] if h in colour}
        free = [i for i in range(len(DYES)) if i not in used]
        if not free:
            raise RuntimeError("touching graph of groups needs more than 16 dyes")
        colour[g] = free[0]
    return colour


def outward(p, dims):
    for ax in range(3):
        if p[ax] < 0:
            return tuple(p[i] - (i == ax) for i in range(3))
        if p[ax] >= dims[ax]:
            return tuple(p[i] + (i == ax) for i in range(3))
    raise ValueError(f"{p} is inside the box")


def to_build(sol) -> dict:
    """Schema-1 layout JSON (the format ae2opt.layout.Layout reads), with a tunnel-network
    port beyond every exit so the simulator can check the file on its own."""
    names = {(0, -1, 0): "down", (0, 1, 0): "up", (0, 0, -1): "north", (0, 0, 1): "south",
             (-1, 0, 0): "west", (1, 0, 0): "east"}
    root, dropped = group_roots(sol)
    colour = colour_groups(sol, root)
    dense = dense_alive(sol)
    blocks = []
    for i, p in enumerate(sorted(sol["controllers"])):
        blk = {"pos": list(p), "type": "controller"}
        if i == 0:
            blk["powered"] = True
        blocks.append(blk)
    freq = 0
    ports = set()
    for q in sorted(root):
        parts = {}
        for p in sol["tunnels"][q]:
            freq += 1
            parts[names[(p[0] - q[0], p[1] - q[1], p[2] - q[2])]] = {"type": "p2p_me", "freq": freq, "output": False}
        blk = {"pos": list(q), "type": "cable", "cable": "normal", "color": DYES[colour[root[q]]]}
        if parts:
            blk["parts"] = parts
        blocks.append(blk)
        if sol["sparent"][q][0] == "exit":
            ports.add(outward(q, sol["dims"]))
    for p in sorted(dense):
        blocks.append({"pos": list(p), "type": "cable", "cable": "dense"})
        if sol["dparent"][p] is None:
            ports.add(outward(p, sol["dims"]))
    for p in sorted(ports):
        blocks.append({"pos": list(p), "type": "port"})
    return {"schema": 1, "blocks": blocks}


def simulate(build: dict):
    """Validate with the project's simulator and rules checker (validation only)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ae2opt.layout import Layout
    from ae2opt.rules import check
    rep = check(Layout.from_dict(build))
    return rep.ok, rep.metrics.get("working_input_tunnels"), [str(v) for v in rep.errors()]


def counting_bound(a, b, c):
    return 2 * a * b * c + a * b + b * c + c * a + 1


def solve(a, b, c, time_limit=600.0, workers=16, symmetry=True, cut=True, cable_cut=False, log=False):
    t0 = time.time()
    mod = Model(a, b, c, symmetry=symmetry, cut=cut, cable_cut=cable_cut)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    solver.parameters.log_search_progress = log
    st = solver.Solve(mod.m)
    dt = time.time() - t0
    res = dict(box=f"{a}x{b}x{c}", symmetry=symmetry, cut=cut, cable_cut=cable_cut, status=solver.StatusName(st), time=round(dt, 1),
               bound=int(solver.BestObjectiveBound()), counting_bound=counting_bound(a, b, c))
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        sol = mod.decode(solver)
        res["T"] = sol["T"]
        build = to_build(sol)
        ok, working, errors = simulate(build)
        res.update(sim_ok=ok, sim_working=working, sim_errors=errors[:5],
                   controllers=len(sol["controllers"]))
        res["build"] = build
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dims", type=int, nargs=3)
    ap.add_argument("--time", type=float, default=600.0)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--no-symmetry", action="store_true")
    ap.add_argument("--no-cut", action="store_true", help="drop the redundant face-count cut")
    ap.add_argument("--cable-cut", action="store_true", help="add the (unneeded) cable-face cut")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--out", help="save the build JSON here")
    ap.add_argument("--results", help="append a one-line JSON result here")
    args = ap.parse_args(argv)
    res = solve(*args.dims, time_limit=args.time, workers=args.workers,
                symmetry=not args.no_symmetry, cut=not args.no_cut,
                cable_cut=args.cable_cut, log=args.log)
    build = res.pop("build", None)
    if build and args.out:
        Path(args.out).write_text(json.dumps(build, indent=1), "utf-8")
    print(json.dumps(res))
    if args.results:
        with open(args.results, "a", encoding="utf-8") as f:
            f.write(json.dumps(res) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
