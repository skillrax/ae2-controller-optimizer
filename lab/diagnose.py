"""Which wiring rule costs the relaxed model its extra tunnels on a fixed controller shape?

Starts from the relaxed model (plain flow between cable cells) with the controllers fixed,
adds the real wiring rules back one at a time, and solves each step to optimality:

  forest      every cable cell has exactly one parent (a cable neighbour or the outside)
  attach      a smart cell touching dense cable has that dense cell as its parent
  trunks      touching dense cells are parent and child (trunks are induced trees)
  dense_up    a dense cell's parent is dense (or the outside)
  no_2cycle   no two cells are each other's parent

All of them together are the app's forest model. Each rule is also tried alone on top of
"forest", to see its own cost.

    python -m lab.diagnose 5x5x5            # a library build's shape
    python -m lab.diagnose lab/builds/4x4x5-202.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from ortools.sat.python import cp_model

from ae2opt import DEFAULT_PROFILE, library
from ae2opt.geometry import Direction, step
from ae2opt.layout import Layout
from ae2opt.optimize import OptimizeSpec, _cells, _Model
from lab.__main__ import RESULTS, commit

RULES = ("attach", "trunks", "dense_up", "no_2cycle")


def shape_of(arg: str):
    if arg.endswith(".json"):
        data = json.load(open(arg))
        # a relaxed solution saved by lab.trace ({"x": [...]}) or a build
        xs = [tuple(c) for c in data["x"]] if "x" in data else Layout.from_dict(data).controllers()
    else:
        xs = library.load(arg).controllers()
    lo = [min(p[i] for p in xs) for i in range(3)]
    shape = frozenset(tuple(p[i] - lo[i] for i in range(3)) for p in xs)
    box = tuple(max(p[i] for p in shape) + 1 for i in range(3))
    return box, shape


def build(box, shape, rules) -> tuple[cp_model.CpModel, _Model]:
    """The relaxed model plus `rules`, on a fixed shape (or, with shape None, over all
    shapes with the app's symmetry breaking)."""
    if shape is None:
        model = _Model(OptimizeSpec(size=box), DEFAULT_PROFILE, box, routing="flow")
        m = model.clone()
        model.add_search_constraints(m, symmetry=True)
    else:
        spec = OptimizeSpec(size=box, required=shape, forbidden=frozenset(set(_cells(box)) - shape))
        model = _Model(spec, DEFAULT_PROFILE, box, routing="flow")
        m = model.clone()
    if not rules:
        return m, model
    dense = DEFAULT_PROFILE.dense_channel_capacity
    k, s, d, h, r = model.k, model.s, model.d, model.h, model.r
    par = {}
    for (n, o), v in h.items():
        p = m.NewBoolVar(f"P{n}{o}")
        m.AddImplication(p, k[o])
        m.Add(v <= dense * p)
        par[(n, o)] = p
    for n in r.cells:
        ps = []
        if n in model.sink:
            p = m.NewBoolVar(f"PS{n}")
            m.Add(model.sink[n] <= dense * len(r.outward(n)) * p)
            ps.append(p)
        ps += [par[(n, step(n, dd))] for dd in Direction if (n, step(n, dd)) in par]
        m.Add(sum(ps) == k[n])
    for (n, o), p in par.items():
        if "no_2cycle" in rules and n < o:
            m.AddBoolOr([p.Not(), par[(o, n)].Not()])
        if "trunks" in rules and n < o:
            m.AddBoolOr([d[n].Not(), d[o].Not(), p, par[(o, n)]])
        if "attach" in rules:
            m.AddBoolOr([s[n].Not(), d[o].Not(), p])
        if "dense_up" in rules:
            m.AddBoolOr([d[n].Not(), p.Not(), d[o]])
    return m, model


def solve(box, shape, rules, seconds, workers) -> dict:
    m, model = build(box, shape, rules)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_workers = workers
    t0 = time.monotonic()
    status = solver.StatusName(solver.Solve(m))
    out = {"rules": "+".join(rules) if rules else "relaxed", "status": status,
           "T": int(round(solver.ObjectiveValue())) if status in ("OPTIMAL", "FEASIBLE") else None,
           "bound": int(solver.BestObjectiveBound() + 1e-9), "seconds": round(time.monotonic() - t0, 1)}
    if status in ("OPTIMAL", "FEASIBLE"):
        out["dense_cells"] = sum(solver.Value(v) for v in model.d.values())
        out["box_cable"] = sum(solver.Value(model.k[n]) for n in model.r.box if n in model.k)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lab.diagnose", description=__doc__.splitlines()[0])
    ap.add_argument("shape", help="library id (e.g. 5x5x5) or a build .json")
    ap.add_argument("--time", type=float, default=300.0, help="seconds per step")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args(argv)
    box, shape = shape_of(args.shape)
    steps = [(), ("forest",)] + [("forest", r) for r in RULES]
    steps += [("forest",) + RULES[:i] for i in range(2, len(RULES) + 1)]
    rows = []
    for rules in steps:
        row = solve(box, shape, rules, args.time, args.workers)
        rows.append(row)
        print(json.dumps(row), flush=True)
    rec = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "commit": commit(), "box": "x".join(map(str, box)),
           "model": "diagnose", "shape": args.shape, "controllers": len(shape), "steps": rows}
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
