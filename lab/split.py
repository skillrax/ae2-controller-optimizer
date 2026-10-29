"""Case split on the controller count: is T >= target possible with exactly n controllers?

Uses the app's relaxed model (routing="flow", Theorem 2 in docs/RESEARCH.md), so an
INFEASIBLE case is a proof for that n. If every n is INFEASIBLE, no build reaches the
target. The tree cut gives T <= 4n + 2, so n starts at ceil((target - 2) / 4).

    python -m lab.split 4 4 5 --target 201 --time 300
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time

from ortools.sat.python import cp_model

from ae2opt import DEFAULT_PROFILE
from ae2opt.optimize import OptimizeSpec, _Model
from lab.__main__ import RESULTS, commit


def case(box, target: int, n: int, seconds: float, workers: int) -> dict:
    model = _Model(OptimizeSpec(size=tuple(box)), DEFAULT_PROFILE, tuple(box), routing="flow")
    m = model.clone()
    model.add_search_constraints(m, symmetry=True)
    m.Add(model.total_x == n)
    m.Add(model.tunnels >= target)
    m.ClearObjective()
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_workers = workers
    t0 = time.monotonic()
    status = solver.StatusName(solver.Solve(m))
    return {"n": n, "status": status, "seconds": round(time.monotonic() - t0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lab.split", description=__doc__.splitlines()[0])
    ap.add_argument("dims", type=int, nargs=3)
    ap.add_argument("--target", type=int, required=True)
    ap.add_argument("--time", type=float, default=300.0, help="seconds per case")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--n", type=int, nargs="*", help="only these controller counts")
    args = ap.parse_args(argv)
    box = args.dims
    ns = args.n or range(math.ceil((args.target - 2) / 4), box[0] * box[1] * box[2] + 1)
    open_cases, empty_run = [], 0
    for n in ns:
        r = case(box, args.target, n, args.time, args.workers)
        print(json.dumps(r), flush=True)
        if r["status"] == "FEASIBLE":
            print(f"n = {n} reaches {args.target} in the relaxed model; the split can't prove it")
            open_cases.append(n)
        elif r["status"] != "INFEASIBLE":
            open_cases.append(n)
        # Past the densest useful shape every case is infeasible at once; stop after 3.
        empty_run = empty_run + 1 if r["status"] == "INFEASIBLE" and r["seconds"] < 1 else 0
        if empty_run >= 3 and not args.n:
            break
    rec = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "commit": commit(), "box": "x".join(map(str, box)),
           "model": "split", "target": args.target, "time_per_case": args.time,
           "open": open_cases, "proven": not open_cases}
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"proven: no build reaches {args.target}" if not open_cases else f"open cases: {open_cases}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
