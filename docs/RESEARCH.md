# Research notes: how many tunnels fit around a controller?

This is the research side of the project: what is proven, how, what is only observed, and
what is open. The goal is an exact answer for every box up to 7×7×7 (84 boxes up to
rotation), each backed by a build and by a bound argument small enough for someone else to
check. Brute force cannot get there (the solver's proof times grow roughly 25× per 16 extra
cells; 4×4×4 took 31 min, and 7×7×7 has 343 cells), so the bound side has to come from
structure.

Contents: [the problem](#1-the-problem) · [the counting identity](#2-the-counting-identity) ·
[the relaxed model](#3-the-relaxed-model-is-a-relaxation) ·
[what "proven" means](#4-what-proven-means) · [data](#5-data) ·
[predictions](#6-prediction-log) · [open problems](#7-open-problems-and-plan)

## 1. The problem

A **build** places blocks in and around an a×b×c box: ME controllers (only inside the box),
cable cells, and P2P tunnels on cable faces. The game's rules R1–R11 are in
[MECHANICS.md](../MECHANICS.md). The ones used below:

- **R2:** the controllers form one face-connected structure.
- **R4:** a tunnel counts only on a cable face that touches a controller.
- **R7/R10:** a tunnel cell's cable must continue to the tunnel network, whose own controller
  doesn't touch the main one.

The optimizer and the library also fix some **design rules**. These are choices, not game
rules, and a result is "proven" only *within* them:

| | Design rule |
|---|---|
| D1 | **Anchor-free:** every controller face that touches cable carries a tunnel. |
| D2 | **Coloured wiring:** tunnels sit on smart cable in colour groups of at most 8 (the normal capacity). Each group joins a fluix dense trunk (at most 32) at a single attachment. Trunks never touch, and touching groups get different colours. |
| D3 | **Margin 1:** cable cells are inside the box or one cell outside it. The tunnel network continues from exit cells outside the box. |
| D4 | Tunnels are allowed both inside and outside the box. |

**T\*(a, b, c)** is the most tunnels over all builds that obey R1–R11 and D1–D4 (default
profile: 8 / 32).

## 2. The counting identity

**Theorem 1.** Take any build whose controllers lie in an a×b×c box, where:

- **(A1)** the controllers are face-connected (R2);
- **(A2)** every cable cell in the box is joined, through cable cells in the box, to a cable
  cell on the box's surface.

Then

> **T = 2abc + ab + bc + ca + 1 − (ℓ + λ + ρ + φ + u + ω)**

where every term in the brackets is ≥ 0. In particular, **T ≤ 2abc + ab + bc + ca + 1**.

The terms (the "waste"):

| | Name in `bounds.py` | Counts |
|---|---|---|
| ℓ | `loops` | Adjacent controller pairs beyond a tree: E_X − (n − 1). |
| λ | `cable_loops` | Adjacent cable pairs in the box beyond a forest: E_K − (m − g). |
| ρ | `extra_exits` | Cable cells on the box surface beyond one per cable component: \|K_s\| − g. |
| φ | `surface_faces` | Box-surface faces of cable cells beyond one per surface cable cell. |
| u | `unused` | Controller faces on cable or the outside that carry no tunnel. |
| ω | `other` | Half the faces touching other in-box cells (air, anything else), plus 2 per such cell. |

**Proof.** The setup:

- V = abc, and S = 2(ab + bc + ca) is the number of cell faces on the box surface.
- X is the set of controllers (n of them), K the cable cells in the box (m), and O the other
  cells in the box (e). So n + m + e = V.
- E_X and E_K are the adjacent pairs within X and within K. F_XK, F_XO and F_KO are the
  faces between the named sets. σ_X, σ_K and σ_O are the surface faces of each set.
- g is the number of components of K (face adjacency), and K_s the cable cells on the
  surface.

Count each face of each cell once:

1. **Controller faces:** 6n = 2E_X + F_XK + F_XO + σ_X.
2. **Cable faces:** 6m = 2E_K + F_XK + F_KO + σ_K.
3. **The surface:** σ_X + σ_K + σ_O = S.

A tunnel occupies a controller face that touches box cable or the outside, at most one
tunnel per face. So T = F_XK + σ_X − u with u ≥ 0.

Each defect is non-negative:

- ℓ ≥ 0 by A1: a connected graph on n vertices has at least n − 1 edges.
- λ ≥ 0: a graph with g components on m vertices has at least m − g edges.
- ρ ≥ 0 by A2: every component has a surface cell.
- φ ≥ 0: every surface cell has at least one surface face.

From (1): T = 6n − 2E_X − F_XO − u = 4n + 2 − 2ℓ − F_XO − u.

From (2) and (3): F_XK = 4m + 2g − 2λ − F_KO − σ_K, and σ_X = S − σ_K − σ_O. With
σ_K = g + ρ + φ:

T = F_XK + σ_X − u = 4m + S − 2λ − 2ρ − 2φ − F_KO − σ_O − u.

Add the two expressions for T, substitute n + m = V − e, and halve. ∎

**Notes.**

- **Few rules used.** The proof uses only A1, A2 and the fact that a tunnel occupies a
  controller face. It doesn't use channel capacities, colours, anchors, the cross rule or
  the margin. So the bound also holds with anchors allowed, under any config.
- **A2 holds for every build the app makes.** Cable leaves the box through its surface to a
  tunnel network outside (D3). A tunnel-network controller placed *inside* the box would
  break A2. That case is outside this problem, and the bound hasn't been checked for it.
- **It is tight.** A 1×1×c line of controllers with every face tunnelled reaches it.
- **What it means.** An optimal build is as close as it can get to two interlocking trees: a
  tree of controllers, and a forest of cable in which each tree touches the surface at
  exactly one face. Every deviation from that costs exactly its count.
- **Results.** It gives **6×6×6 ≤ 541** (the solver's best was 549). The solver's 7×7×7
  bound (834) is exactly this count. For 5×5×5 the solver does better (320 vs 326).
- **Checks.** `tests/test_bounds.py` checks the identity term by term on every shipped build.
  `waste_breakdown` asserts it for any layout you give it.

## 3. The relaxed model is a relaxation

**Theorem 2.** The flow model (`routing="flow"`) can only over-estimate. Restrict any
solution of the full model (`routing="forest"`) to the shared variables
(x, k, s, d, f, h, sink): the result is feasible in the flow model and has the same
tunnel count.

**Proof** (`_Model` in `optimize.py`). Both models share every constraint except the
routing ones:

- The full model has h(n→o) ≤ 32·par(n→o), par(n→o) ⇒ k(o), Σ parents of n = k(n), and
  sink(n) ≤ 32·par_sink(n), plus the wiring rules.
- The flow model replaces these with h(n→o) ≤ 32·k(n), h(n→o) ≤ 32·k(o) and
  sink(n) ≤ 32·k(n).

Take a full solution:

- h(n→o) ≤ 32·par(n→o) ≤ 32·k(o).
- If k(n) = 0, n has no parent, so every par(n→·) and par_sink(n) is 0. That forces
  h(n→·) = 0 and sink(n) = 0, so h(n→o) ≤ 32·k(n) and sink(n) ≤ 32·k(n) hold.
- The wiring rules are only dropped.
- Both solves add the same symmetry-breaking constraints. Any full solution has a mirrored
  or rotated image that meets them, with the same tunnel count, and that image restricts to
  a flow solution.

The tunnel count is the same expression (Σ f) in both, so the flow optimum is at least the
full optimum. ∎

This proves the code as written. `test_relaxed_model_bounds_the_real_one` also checks it
numerically.

## 4. What "proven" means

A library entry marked proven rests on three things.

1. **Soundness:** every build the model returns works in the game. The simulator (a port
   of AE2's channel pathing) checks every result, and in-game tests confirmed the simulator
   (`examples/ingame/RESULTS.md`).
2. **Completeness:** every build allowed by R1–R11 and D1–D4 is a solution of the model.
   An encoding that is stricter than the rules would make "proven" optima too low. A
   second model, written from the rules without reading `optimize.py`, re-proved 23 of the
   24 proven boxes with the same optimum ([INDEPENDENT_CHECK.md](INDEPENDENT_CHECK.md)).
   On 4×4×4 it found 164 but did not close its bound (168) in 4 h, so that box is not
   independently checked.
3. **Solver correctness:** CP-SAT's OPTIMAL status. It comes with **no checkable
   certificate**.

Theorem 1 depends on none of these. Item 2 is open problem 1 below.

## 5. Data

The waste split (Theorem 1) for every build we have, 2026-10-28. Results marked † were
proven during this research and aren't in the shipped library yet. "Most" is the best proven
bound, shown where the build isn't proven optimal.

| Box | T | Most | 2abc+ab+bc+ca+1 | Waste | ℓ | λ | ρ | φ | u + ω |
|---|---|---|---|---|---|---|---|---|---|
| 1×1×c | 4c+2 | | 4c+2 | 0 | 0 | 0 | 0 | 0 | 0 |
| 1×2×2 | 16 | | 17 | 1 | 1 | 0 | 0 | 0 | 0 |
| 1×2×3 | 22 | | 24 | 2 | 0 | 0 | 0 | 2 | 0 |
| 1×2×4 | 28 | | 31 | 3 | 1 | 0 | 0 | 2 | 0 |
| 2×2×2 | 24 | | 29 | 5 | 1 | 0 | 0 | 4 | 0 |
| 1×3×3 | 32 | | 34 | 2 | 1 | 0 | 0 | 1 | 0 |
| 1×3×4 | 40 | | 44 | 4 | 1 | 0 | 1 | 2 | 0 |
| 2×2×3 | 36 | | 41 | 5 | 1 | 0 | 0 | 4 | 0 |
| 1×4×4 | 52 | | 57 | 5 | 1 | 0 | 0 | 4 | 0 |
| 2×2×4 | 46 | | 53 | 7 | 2 | 0 | 0 | 5 | 0 |
| 2×3×3 | 52 | | 58 | 6 | 1 | 0 | 1 | 4 | 0 |
| 2×3×4 | 68 | | 75 | 7 | 1 | 0 | 0 | 6 | 0 |
| 3×3×3 | 74 | | 82 | 8 | 0 | 0 | 3 | 5 | 0 |
| 1×4×7 | 88 | | 96 | 8 | 1 | 0 | 0 | 7 | 0 |
| 2×2×7 | 78 | | 89 | 11 | 2 | 0 | 1 | 8 | 0 |
| 2×4×4 | 88 | | 97 | 9 | 3 | 0 | 1 | 5 | 0 |
| 1×5×7 | 108 | | 118 | 10 | 1 | 0 | 0 | 9 | 0 |
| 3×3×4 | 96 | | 106 | 10 | 1 | 0 | 4 | 5 | 0 |
| 3×4×4 | 126 | | 137 | 11 | 0 | 0 | 5 | 6 | 0 |
| 1×7×7 | 146 | | 162 | 16 | 0 | 0 | 2 | 14 | 0 |
| 4×4×4 | 164 | | 177 | 13 | 1 | 0 | 5 | 7 | 0 |
| 3×3×5 † | 120 | | 130 | 10 | 1 | 0 | 4 | 5 | 0 |
| 3×4×5 † | 158 | | 168 | 10 | 2 | 0 | 3 | 5 | 0 |
| 3×5×5 † | 192 | | 206 | 14 | 1 | 0 | 5 | 8 | 0 |
| 4×4×5 | 200 | 205 | 217 | 17 | 1 | 1 | 9 | 6 | 0 |
| 5×5×5 | 298 | 320 | 326 | 28 | 0 | 1 | 17 | 10 | 0 |
| 6×6×6 | 484 | 541 | 541 | 57 | 3 | 9 | 33 | 12 | 0 |
| 7×7×7 | 719 | 834 | 834 | 115 | 4 | 36 | 59 | 11 | 5 |

**Observations** (data, not theorems):

- **O1.** In every proven build, u = ω = 0: no controller face goes unused, and no box cell is
  neither controller nor cable. So T = 4n + 2 − 2ℓ, and the problem amounts to "most
  controllers in a nearly-tree shape whose complement can be wired".
- **O2.** **No proven build has a cable loop (λ = 0).** Cable loops appear only in the
  unproven builds (4×4×5: 1, 5×5×5: 1, 6×6×6: 9, 7×7×7: 36). Either optimal builds avoid
  them and those builds can be improved, or deep cores force them (open problem 4).
- **O3.** In the proven boxes the waste is surface faces (φ) and extra exits (ρ): edge and
  corner effects. The fit waste ≈ 1.35·(a+b+c) − 4.2, less 2.4 for 1-thick slabs, matches
  the proven boxes to 1.2 tunnels RMS when each box is predicted from the others.
- **O4.** Cells one layer in from the surface cost nothing extra. A term for their number
  fits a coefficient of 0.03.
- **O5. Families.** Proven values along a fixed cross-section (box a×b×c, c = length):

  | Cross-section | c = 2 | 3 | 4 | 5 | 6 | 7 | Pattern so far |
  |---|---|---|---|---|---|---|---|
  | 2×2 | 24 | 36 | 46 | 58 | 68 | 78 | steps 12, 10, 12, 10, 10: none yet |
  | 2×3 | 36 | 52 | 68 | 84 | 100 | 116 | **16c + 4** for c = 2–7 |
  | 3×3 | 52 | 74 | 96 | 120 | 142 | 166 | **24c + 4 − 2⌊c/2⌋** for c = 3–7 |
  | 3×4 | 68 | 96 | 126 | 158 | 186 | 214–216 | steps 28, 30, 32, 28; c = 7 open |

  (1×1×c lines reach the counting bound, 4c + 2, for c = 1–4.) These patterns are
  conjectures for longer boxes, checked up to the 7-block limit. The 2×2 row breaks the
  12/10 alternation at 2×2×7 = 78, which was re-proven with the current code (22 s, through
  the relaxed bound). Longer families need a controller-size config above 7 (Extended Life
  allows up to 63) to follow them further.

## 6. Prediction log

Each prediction was written down before its run. They are made from the O3 fit and rounded
to one decimal. Tunnel counts have always been even (O1), so a prediction of x means one of
the even numbers next to it.

| Made | Box | Predicted | Result | Run |
|---|---|---|---|---|
| 2026-10-28 | 3×3×5 | 119.1 | **120**, proven | 26 s, 16 threads |
| 2026-10-28 | 3×4×5 | 155.7 | **158**, proven | 10 min |
| 2026-10-28 | 3×5×5 | 192.4 | **192**, proven | 24 min |
| 2026-10-28 | 4×4×5 | 203.4 | 200 found, at most 205; later **202** (relax, then fix) | stopped at 2 h; 202 from a 52 s relaxed solve |
| 2026-10-28 22:14 | 2×2×5 | 57.0 | **58**, proven | 13 s |
| 2026-10-28 22:14 | 2×2×6 | 67.7 | **68**, proven | 14 s |
| 2026-10-28 22:14 | 2×3×5 | 82.7 | **84**, proven | 17 s |
| 2026-10-28 22:14 | 2×3×6 | 98.3 | **100**, proven | 17 s |
| 2026-10-28 22:14 | 2×3×7 | 114.0 | **116**, proven | 23 s |
| 2026-10-28 22:14 | 3×3×6 | 142.0 | **142**, proven | 35 s |
| 2026-10-28 22:14 | 3×3×7 | 164.7 | **166**, proven | 3.3 min |
| 2026-10-28 22:14 | 3×4×6 | 185.7 | **186**, proven | 8.5 min |
| 2026-10-28 22:14 | 3×4×7 | 215.3 | 214 found, at most 216 | stopped at 1.5 h |

## 7. Open problems and plan

1. **An independent encoding.** *Done for 23 of 24 boxes*
   ([INDEPENDENT_CHECK.md](INDEPENDENT_CHECK.md)): no disagreement. Still open: 4×4×4
   (164 found, at most 168 after 4 h).
2. **Box families.** For a fixed cross-section a×b, waste should become linear in c,
   possibly with a repeating wobble (subadditivity): W(a,b,c) ≈ w(a,b)·c + constant. The
   family proofs in the prediction log measure w(a,b). For thin cross-sections, a
   transfer-matrix method (dynamic programming over slices) would prove the formula for
   every length at once. That is how the domination number of every grid graph was settled
   (Gonçalves, Pinlou, Rao, Thomassé 2011).
3. **Local lower bounds.** The waste is a sum of local defects. The plan:
   - Solve small "window" problems (a corner, k cells of edge, with an open boundary) for
     the least waste each forces. Adding them up gives a bound for any box, and the cost
     doesn't grow with the box.
   - **First test:** can this recover 4×4×4 = 164 and 3×3×5 = 120?
   - **The difficulty:** ℓ, λ and ρ depend on how the whole build connects, so splitting
     them into local pieces needs care.
4. **The core.** Cells two or more layers deep exist only in boxes that are at least 5 on
   every side.
   - **The capacity argument:** a cable tree of j cells touches about 4j + 1 controller
     faces, but D1 + D2 cap a group at 8 tunnels. That suggests a real waste cost per deep
     cell.
   - **Pinning it down:** a lower bound here plus problem 3 would give 5×5×5–7×7×7 bounds
     far below today's.
   - **On the build side:** O2 suggests trying 5×5×5 builds without cable loops.
5. **Certificates.** A proof log that an independent checker can verify (e.g. a MaxSAT
   encoding with VeriPB proof logging) for the small boxes, to replace "the solver said so".

## Reproducing

Experiments run from the lab ([lab/README.md](../lab/README.md)), which logs every run to
`lab/results.jsonl`:

```bash
python -m lab solve 3 3 5 --model both --time 1800
python -m unittest discover -s lab/tests -t .
python -m unittest tests.test_bounds
python -c "from ae2opt import library; from ae2opt.bounds import waste_breakdown; print(waste_breakdown(library.load('4x4x4')))"
```
