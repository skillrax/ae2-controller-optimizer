# Lab

The research side of the project: proving exact optima for the boxes the app can only
approximate (4×4×5 and up). The questions, the theorems so far and the open problems are in
[docs/RESEARCH.md](../docs/RESEARCH.md).

The app (`ae2opt/`) is built for players: it polishes the wiring so builds are easy to
make, and never ships without that. The lab only cares about the tunnel count and its proven
bound, so it runs the solvers bare and logs everything. Nothing here is in the app build.

## Running

From the repository root:

```bash
python -m lab solve 4 4 5 --model app --time 3600 --note "baseline before window cuts"
python -m lab solve 3 3 4 --model both --time 600
python -m lab log
python -m unittest discover -s lab/tests -t .
```

- `--model app`: the app's optimizer (relaxed bound, cuts, symmetry) with polish and LNS off.
- `--model independent`: [`independent.py`](independent.py), the second model written
  without reading the app's ([docs/INDEPENDENT_CHECK.md](../docs/INDEPENDENT_CHECK.md)).
- `--save` keeps the build in `lab/runs/` (not committed). Fold a new record into the
  library with `tools/build_library.py --add-run`.

Every run appends a line to [`results.jsonl`](results.jsonl): the commit (`+` if the tree
had changes), model, settings, tunnels, proven bound, time, the simulator's verdict and the
library's value. `log` flags any run that disagrees with a proven library value or beats it.

## Ground rules

1. **Predict first.** Write the prediction in RESEARCH.md §6 before the run.
2. **Every build goes through the simulator.** A result it rejects doesn't count.
3. **Every cut needs a written argument** that it never removes an optimal build (a
   theorem in RESEARCH.md or an argument next to the code). An unproven cut can make a
   wrong optimum look proven.
4. **Keep the two models apart.** Don't read `ae2opt/optimize.py` while changing
   `independent.py`, or the reverse; the agreement test is only worth something if they
   were written separately.
5. **Log failures too.** A run that didn't prove anything still bounds the time it takes.

## When to check with the independent model

Full independent runs are expensive (3×4×4: 22 min, 4×4×4: over 4 h), so they are kept for
proof claims:

| What happened | What it needs |
|---|---|
| A better build | The simulator. A working build proves itself. |
| Day-to-day experiments | The log. |
| A change to the app's model (cut, relaxation, encoding) | The agreement test (11 s). |
| A new **proven optimum**, or a new cut used in a proof | A full independent run on that box before it goes in the library or RESEARCH.md as proven. |
| The two models disagree | Stop and find out which is wrong. |

A theorem is checked by its written proof and by tests against every known build (as
Theorem 1 is in `tests/test_bounds.py`), not by a second model. If the independent model
falls too far behind to check a claim, give it its own speed-up, derived from its own
encoding.
