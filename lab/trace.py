"""Trace the relaxed model's bound and best solution over time, with optional extra cuts.

    python -m lab.trace 3 4 5 --time 900 [--cut NAME ...] [--log FILE]

Prints one JSON line per bound or solution improvement, then a summary, and saves the
best relaxed solution's controller and cable cells (in box coordinates) for analysis.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from ortools.sat.python import cp_model

from ae2opt import DEFAULT_PROFILE
from ae2opt.optimize import OptimizeSpec, _Model
from lab import cuts as lab_cuts
from lab.__main__ import RESULTS, RUNS, commit
from lab.diagnose import build


class Trace(cp_model.CpSolverSolutionCallback):
    def __init__(self, model, t0):
        super().__init__()
        self.model, self.t0, self.best, self.events, self.cells = model, t0, None, [], None

    def on_solution_callback(self):
        v = int(round(self.ObjectiveValue()))
        if self.best is None or v > self.best:
            self.best = v
            self.cells = {"x": [c for c, var in self.model.x.items() if self.Value(var)],
                          "k": [n for n, var in self.model.k.items() if self.Value(var)],
                          "d": [n for n, var in self.model.d.items() if self.Value(var)]}
            self.emit("best", v)

    def emit(self, kind, v):
        e = {"t": round(time.monotonic() - self.t0, 1), kind: v}
        self.events.append(e)
        print(json.dumps(e), flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lab.trace", description=__doc__.splitlines()[0])
    ap.add_argument("dims", type=int, nargs=3)
    ap.add_argument("--time", type=float, default=900.0)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--cut", action="append", default=[], choices=sorted(lab_cuts.CUTS))
    ap.add_argument("--rules", nargs="*", default=[], help="real wiring rules to add (lab.diagnose), "
                    "e.g. forest")
    ap.add_argument("--log", help="write CP-SAT's search log here")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    box = tuple(args.dims)

    m, model = build(box, None, tuple(args.rules))
    for name in args.cut:
        lab_cuts.CUTS[name](model, m)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = args.time
    solver.parameters.num_workers = args.workers
    if args.log:
        solver.parameters.log_search_progress = True
        solver.parameters.log_to_stdout = False
        logf = open(args.log, "w", encoding="utf-8")
        solver.log_callback = lambda line: logf.write(line + "\n")
    t0 = time.monotonic()
    cb = Trace(model, t0)
    last = [None]

    def on_bound(b):
        b = int(b + 1e-9)
        if last[0] is None or b < last[0]:
            last[0] = b
            cb.emit("bound", b)
    solver.best_bound_callback = on_bound
    status = solver.StatusName(solver.Solve(m, cb))
    rec = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "commit": commit(), "box": "x".join(map(str, box)),
           "model": "relaxed-trace", "cuts": args.cut + args.rules, "status": status, "best": cb.best,
           "bound": int(solver.BestObjectiveBound() + 1e-9), "seconds": round(time.monotonic() - t0, 1),
           "trace": cb.events, "note": args.note}
    print(json.dumps({k: rec[k] for k in ("status", "best", "bound", "seconds")}))
    if cb.cells:
        RUNS.mkdir(exist_ok=True)
        tag = "-".join([rec["box"], *args.cut, *args.rules])
        (RUNS / f"relaxed-{tag}.json").write_text(json.dumps(cb.cells), "utf-8")
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
