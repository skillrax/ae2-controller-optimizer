"""Compute the shipped library (ae2opt/library/): proofs for small boxes and
best-known builds for big cubes, for the default profile (8 / 32) with default
optimizer settings (coloured wiring, tunnels inside and outside the box).

    python tools/build_library.py              # compute what's missing, then write the index
    python tools/build_library.py --index-only
    python tools/build_library.py --add-run <run folder>...   # fold in an app/CLI run

Each box writes <id>.json (the build plus tunnel-network ports) and a line in
_results.jsonl; index.json is generated from those lines. Delete a box's line to
recompute it. Bump optimize.MODEL_VERSION (and rebuild) when the model changes.
"""

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from ae2opt import DEFAULT_PROFILE, Layout, check, library, runner  # noqa: E402
from ae2opt.bounds import counting_bound  # noqa: E402
from ae2opt.optimize import MODEL_VERSION, OptimizeSpec, optimize  # noqa: E402
from ae2opt.patterns import canonical  # noqa: E402

OUT = REPO / "ae2opt" / "library"
RESULTS = OUT / "_results.jsonl"

# Proofs: every box with sides <= 4 (sorted), except 4x4x4, which doesn't prove in this
# budget. Its proof (164) came from a ~1 h app run (see --add-run), so 4x4x4 is skipped
# below while its line in _results.jsonl stays.
PROVE = [b for b in itertools.combinations_with_replacement((1, 2, 3, 4), 3) if b != (4, 4, 4)]
# Best known: (main solve s, LNS s, optional start layout, optional known upper bound)
IMPROVE = {
    (4, 4, 4): (300, 900, None, None),
    (5, 5, 5): (300, 1200, None, None),
    (6, 6, 6): (300, 1500, None, None),
    (7, 7, 7): (60, 1800, REPO / "examples/best_7x7x7_coloured.json", None),
}


def results() -> list[dict]:
    if not RESULTS.exists():
        return []
    return [json.loads(line) for line in RESULTS.read_text("utf-8").splitlines() if line.strip()]


def record(box, sol, mode, seconds, workers, extra=None):
    bid = "x".join(map(str, box))
    sol.harness.save(OUT / f"{bid}.json")
    dense = sum(1 for b in sol.layout.blocks.values() if b.kind == "cable" and b.cable.value == "dense")
    row = {"id": bid, "box": list(box), "status": "proven" if sol.status == "OPTIMAL" else "best_known",
           "tunnels": sol.tunnels_validated, "upper": sol.upper_bound, "controllers": sol.controllers,
           "trunks": len(sol.exit_cells), "dense_cells": dense, "cable_cells": sol.cable_cells,
           "anchors": sol.anchors, "mode": mode, "seconds": round(seconds), "workers": workers, **(extra or {})}
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)


def compute(workers: int) -> None:
    OUT.mkdir(exist_ok=True)
    done = {r["id"] for r in results()}
    for box in PROVE:
        if "x".join(map(str, box)) in done:
            continue
        t = time.time()
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=box, time_limit=1200, workers=workers))
        record(box, sol, "prove", time.time() - t, workers)
    for box, (main, lns, start_path, upper) in IMPROVE.items():
        if "x".join(map(str, box)) in done:
            continue
        start = None
        if start_path and Path(start_path).exists():
            start = canonical(frozenset(Layout.load(start_path).controllers()), box)
        t = time.time()
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=box, time_limit=main, lns_time=lns, lns_step=20,
                                                     workers=workers, start=start, seed_time=60,
                                                     known_upper=upper))
        record(box, sol, "improve", time.time() - t, workers,
               {"started_from": Path(start_path).name if start_path else None})


def add_run(run_dir: Path) -> None:
    """Fold a run from the app or the CLI into the library. Its build replaces the entry's
    when it has more tunnels (or as many with fewer trunks); its proven bound tightens the
    entry either way. The build is re-checked with the simulator first."""
    data = runner.read_run(run_dir)
    if not library.rules_match(runner.run_rules(data), DEFAULT_PROFILE):
        raise SystemExit(f"{run_dir.name}: not run under the library's rules")
    box = list(data["spec"]["size"])
    stored = sorted(box)
    bid = "x".join(map(str, stored))
    lay = library.orient(Layout.load(run_dir / runner.BEST_FILE), box, stored)
    report = check(lay, DEFAULT_PROFILE)
    tunnels = report.metrics["working_input_tunnels"]
    if not report.ok or tunnels != data["best_tunnels"]:
        raise SystemExit(f"{run_dir.name}: the simulator counts {tunnels} working tunnels, the run says "
                         f"{data['best_tunnels']}\n{report.format()}")
    blocks = list(lay.to_dict()["blocks"])
    cables = [b for b in blocks if b["type"] == "cable"]
    stats = {"tunnels": tunnels, "controllers": sum(b["type"] == "controller" for b in blocks),
             "trunks": sum(b["type"] == "port" for b in blocks),
             "dense_cells": sum(b.get("cable") == "dense" for b in cables), "cable_cells": len(cables),
             "anchors": sum(p.get("type") == "anchor" for b in cables for p in b.get("parts", {}).values())}
    old = {r["id"]: r for r in results()}.get(bid)
    upper = min(u for u in (data.get("best_upper"), old and old["upper"]) if u is not None)
    better = old is None or (tunnels, -stats["trunks"]) > (old["tunnels"], -old["trunks"])
    if not better and upper >= old["upper"]:
        print(f"{run_dir.name}: nothing new for {bid} ({tunnels} tunnels, at most {data.get('best_upper')})")
        return
    run = {"mode": "run", "seconds": round(data.get("active_seconds", 0)), "workers": data["spec"]["workers"],
           "run": run_dir.name}
    if better:
        lay.save(OUT / f"{bid}.json")
        row = {"id": bid, "box": stored, **stats, **run}
    else:
        row = {**old, "bound_from": run_dir.name}
    row["upper"] = upper
    row["status"] = "proven" if row["tunnels"] >= upper else "best_known"
    order = ["id", "box", "status", "tunnels", "upper"]
    row = {**{k: row[k] for k in order}, **{k: v for k, v in row.items() if k not in order}}
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)


def write_index() -> None:
    p = DEFAULT_PROFILE
    rules = {"normal": p.normal_channel_capacity, "dense": p.dense_channel_capacity, "model": MODEL_VERSION,
             "exterior": True, "interior": True, "anchor_free": True, "margin": 1}
    latest = {}
    for r in results():
        latest[r["id"]] = r   # a later line for the same box replaces an earlier one
    entries = []
    for r in sorted(latest.values(), key=lambda r: (r["box"][0] * r["box"][1] * r["box"][2], r["box"])):
        counted = counting_bound(r["box"])
        if counted < r["upper"]:
            r = {**r, "upper": counted, "bound_from": "counting bound (docs/RESEARCH.md)"}
            r["status"] = "proven" if r["tunnels"] >= counted else "best_known"
        entries.append({**r, "file": f"{r['id']}.json", "rules": rules})
    (OUT / "index.json").write_text(json.dumps({"schema": 1, "entries": entries}, indent=1), "utf-8")
    print(f"index.json: {len(entries)} entries "
          f"({sum(e['status'] == 'proven' for e in entries)} proven)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--add-run", nargs="+", type=Path, metavar="RUN_DIR")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 8)
    args = ap.parse_args()
    if args.add_run:
        for d in args.add_run:
            add_run(d)
    elif not args.index_only:
        compute(args.workers)
    write_index()
