"""Run a model on a box and log the result.

Every run appends one line to lab/results.jsonl: the commit it ran on, the model and its
settings, the tunnels found, the proven bound, the time, the simulator's verdict and the
library's value for comparison. Builds go to lab/runs/ (not committed) with --save.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ae2opt import DEFAULT_PROFILE, library
from ae2opt.bounds import counting_bound

LAB = Path(__file__).resolve().parent
RESULTS = LAB / "results.jsonl"
RUNS = LAB / "runs"
MODELS = ("app", "independent")


def commit() -> str:
    """Short hash of HEAD, with + when the working tree has changes."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=LAB, capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short", "HEAD") + ("+" if git("status", "--porcelain", "--", ".") else "")


def library_value(box) -> dict | None:
    want = sorted(box)
    for e in library.entries():
        if sorted(e["box"]) == want:
            return {"tunnels": e["tunnels"], "upper": e["upper"], "status": e["status"]}
    return None


def solve_app(box, seconds: float, workers: int) -> tuple[dict, dict]:
    """The app's optimizer, bare: relaxed bound and cuts on, polish and LNS off."""
    from ae2opt.optimize import OptimizeSpec, optimize
    sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=tuple(box), time_limit=seconds, workers=workers,
                                                 polish_time=0, lns_time=0))
    res = {"T": sol.tunnels_validated, "upper": sol.upper_bound, "controllers": sol.controllers,
           "sim_ok": sol.report.ok and sol.tunnels_validated == sol.tunnels_model}
    return res, sol.layout.to_dict()


def solve_independent(box, seconds: float, workers: int) -> tuple[dict, dict | None]:
    from lab.independent import solve
    r = solve(*box, time_limit=seconds, workers=workers)
    build = r.pop("build", None)
    res = {"T": r.get("T"), "upper": r["bound"], "controllers": r.get("controllers"),
           "sim_ok": r.get("sim_ok", False) and r.get("sim_working") == r.get("T")}
    return res, build


SOLVERS = {"app": solve_app, "independent": solve_independent}


def run(box, model: str, seconds: float, workers: int, note: str = "", save: bool = False,
        log: bool = True) -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.monotonic()
    res, build = SOLVERS[model](box, seconds, workers)
    res["upper"] = min(res["upper"], counting_bound(box))
    rec = {"when": started, "commit": commit(), "box": "x".join(map(str, box)), "model": model,
           "workers": workers, "time_limit": seconds, **res,
           "proven": res["T"] is not None and res["T"] >= res["upper"],
           "seconds": round(time.monotonic() - t0, 1), "counting_bound": counting_bound(box),
           "library": library_value(box), "note": note}
    if save and build:
        RUNS.mkdir(exist_ok=True)
        path = RUNS / f"{rec['box']}-{model}-{started[:19].replace(':', '')}.json"
        path.write_text(json.dumps(build, indent=1), "utf-8")
        rec["build"] = str(path.relative_to(LAB.parent))
    if log:
        with RESULTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


def verdict(rec: dict) -> str:
    lib = rec["library"]
    found = f"{rec['T']}" + (" proven" if rec["proven"] else f", at most {rec['upper']}")
    if not rec["sim_ok"]:
        found += "  SIMULATOR REJECTS IT"
    if lib is None:
        return found
    if rec["T"] is not None and rec["T"] > lib["tunnels"]:
        return found + f"  NEW BEST (library {lib['tunnels']})"
    if rec["proven"] and lib["status"] == "proven" and rec["T"] != lib["tunnels"]:
        return found + f"  DISAGREES with library {lib['tunnels']}"
    return found + f"  (library {lib['tunnels']}, {lib['status']})"


def print_log(last: int):
    if not RESULTS.exists():
        print("no results yet")
        return
    recs = [json.loads(l) for l in RESULTS.read_text("utf-8").splitlines() if l.strip()]
    for r in recs[-last:]:
        if r["model"] == "split":
            print(f"{r['when'][:16]}  {r['commit']:9} {r['box']:6} split       T >= {r['target']}: "
                  + ("impossible, proven" if r["proven"] else f"open for n = {r['open']}"))
            continue
        if r["model"] == "relaxed-trace":
            print(f"{r['when'][:16]}  {r['commit']:9} {r['box']:6} relaxed     {r['seconds']:>8}s  "
                  f"best {r['best']}, bound {r['bound']}" + (f", cuts {r['cuts']}" if r["cuts"] else "")
                  + (f"  - {r['note']}" if r["note"] else ""))
            continue
        if r["model"] == "relaxfix":
            lib = r["library"]["tunnels"] if r["library"] else "-"
            print(f"{r['when'][:16]}  {r['commit']:9} {r['box']:6} relaxfix    {r['seconds']:>8}s  "
                  f"real {r['T']} (library {lib}), relaxed bound {r['relaxed_bound']}"
                  + (f"  - {r['note']}" if r["note"] else ""))
            continue
        print(f"{r['when'][:16]}  {r['commit']:9} {r['box']:6} {r['model']:11} {r['seconds']:>8}s  "
              f"{verdict(r)}" + (f"  - {r['note']}" if r["note"] else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lab", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("solve", help="solve a box and log the result")
    s.add_argument("dims", type=int, nargs=3)
    s.add_argument("--model", choices=(*MODELS, "both"), default="app")
    s.add_argument("--time", type=float, default=600.0, help="seconds per model")
    s.add_argument("--workers", type=int, default=16)
    s.add_argument("--note", default="", help="what this run is testing")
    s.add_argument("--save", action="store_true", help="keep the build in lab/runs/")
    s.add_argument("--no-log", action="store_true", help="don't append to lab/results.jsonl")
    lg = sub.add_parser("log", help="show logged results")
    lg.add_argument("--last", type=int, default=30)
    args = ap.parse_args(argv)

    if args.cmd == "log":
        print_log(args.last)
        return 0
    for model in (MODELS if args.model == "both" else (args.model,)):
        rec = run(args.dims, model, args.time, args.workers, args.note, args.save, not args.no_log)
        print(f"{rec['box']} {model}: {verdict(rec)} in {rec['seconds']} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
