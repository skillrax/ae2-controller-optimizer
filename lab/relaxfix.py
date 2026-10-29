"""Relax, then fix: find controller shapes with the relaxed model, then wire each for real.

The relaxed model (routing="flow") finds high-scoring controller shapes far faster than the
full model, and wiring a fixed shape is quick. Its tunnel count is only an upper bound for
that shape, so every shape is re-solved with the real wiring rules and checked by the
simulator.

    python -m lab.relaxfix 5 5 5 --time 1200 --fix-time 120 [--cut cable_faces]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from ortools.sat.python import cp_model

from ae2opt import DEFAULT_PROFILE
from ae2opt.optimize import OptimizeSpec, _Model, _cells, optimize
from lab import cuts as lab_cuts
from lab.__main__ import RESULTS, RUNS, commit, library_value


class Shapes(cp_model.CpSolverSolutionCallback):
    def __init__(self, model, t0):
        super().__init__()
        self.model, self.t0, self.found = model, t0, []

    def on_solution_callback(self):
        xs = frozenset(c for c, v in self.model.x.items() if self.Value(v))
        score = int(round(self.ObjectiveValue()))
        self.found.append((score, xs))
        print(json.dumps({"t": round(time.monotonic() - self.t0, 1), "relaxed": score, "controllers": len(xs)}),
              flush=True)


def wire(box, xs, seconds, workers):
    sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=box, time_limit=seconds, workers=workers, required=xs,
                                                 forbidden=frozenset(set(_cells(box)) - xs),
                                                 relaxed_bound=False))
    return sol


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lab.relaxfix", description=__doc__.splitlines()[0])
    ap.add_argument("dims", type=int, nargs=3)
    ap.add_argument("--time", type=float, default=1200.0, help="seconds for the relaxed search")
    ap.add_argument("--fix-time", type=float, default=120.0, help="seconds to wire each shape")
    ap.add_argument("--top", type=int, default=8, help="wire this many of the best shapes")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--cut", action="append", default=[], choices=sorted(lab_cuts.CUTS))
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    box = tuple(args.dims)

    model = _Model(OptimizeSpec(size=box), DEFAULT_PROFILE, box, routing="flow")
    m = model.clone()
    model.add_search_constraints(m, symmetry=True)
    for name in args.cut:
        lab_cuts.CUTS[name](model, m)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = args.time
    solver.parameters.num_workers = args.workers
    t0 = time.monotonic()
    cb = Shapes(model, t0)
    solver.Solve(m, cb)
    relaxed_bound = int(solver.BestObjectiveBound() + 1e-9)

    shapes = sorted({xs: s for s, xs in cb.found}.items(), key=lambda kv: -kv[1])[:args.top]
    best, best_sol = None, None
    for xs, score in shapes:
        sol = wire(box, xs, args.fix_time, args.workers)
        ok = sol.report.ok and sol.tunnels_validated == sol.tunnels_model
        print(json.dumps({"relaxed": score, "controllers": len(xs), "real": sol.tunnels_validated,
                          "shape_proven": sol.status == "OPTIMAL", "sim_ok": ok}), flush=True)
        if ok and (best is None or sol.tunnels_validated > best):
            best, best_sol = sol.tunnels_validated, sol
    lib = library_value(box)
    rec = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "commit": commit(), "box": "x".join(map(str, box)),
           "model": "relaxfix", "cuts": args.cut, "T": best, "relaxed_bound": relaxed_bound,
           "seconds": round(time.monotonic() - t0, 1), "library": lib, "note": args.note}
    if best_sol is not None:
        RUNS.mkdir(exist_ok=True)
        path = RUNS / f"relaxfix-{rec['box']}-{best}.json"
        path.write_text(json.dumps(best_sol.layout.to_dict(), indent=1), "utf-8")
        rec["build"] = str(path.relative_to(RUNS.parent.parent))
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"best real build {best} (library {lib['tunnels'] if lib else '-'}), relaxed bound {relaxed_bound}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
