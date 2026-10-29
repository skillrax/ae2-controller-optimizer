# Changelog

## Unreleased

- **Counting bound:** face counting proves that an a×b×c box holds at most
  2abc + ab + bc + ca + 1 tunnels, under any config. Runs show it from the start. It tightens
  6×6×6 to at most 541 (was 549). Proof and research notes:
  [docs/RESEARCH.md](docs/RESEARCH.md).
- **New known results:** 4×4×5 holds 202 tunnels (at most 205), and 5×5×5 is at most 318
  (was 320).
- **Independent check:** a second model, written from the rules without reading the
  optimizer, re-proves 23 of the 24 proven results
  ([docs/INDEPENDENT_CHECK.md](docs/INDEPENDENT_CHECK.md)).

## 1.2.0

- **Faster proofs.** The optimizer also solves a relaxed model (the same controllers and
  channel capacities, simplified cable routing) for a fast upper bound; a build that reaches
  it is proven optimal. On 16 threads: 3×4×4 is proven in 42 s instead of 104 s, and 4×4×4
  from scratch in 31 min, where the full model alone stalls. Needs 6+ threads.
- Wiring tidy-up gets at least 10 s, so fast proofs still end with the fewest trunks.
- **6×6×6 bound:** the relaxed model shows 6×6×6 holds at most 549 tunnels (was 590).

## 1.1.0

- **Your runs count as known results.** A run that proves its box, or finds more tunnels than
  the best known build, is recognised as such: the run panel says how it stands against
  what was known before it (and announces a new best while it runs), the Optimize tab's
  "best known" and "start the run from it" use it, and the tightest proven maximum from any
  run applies.
- **Library:** your runs are grouped by box and settings, each group headed by its best
  result and how close it is to the proven maximum. Known results show your proofs and
  records in place of the shipped ones.
- **Designer:** the layer slider covers the build's own layers; the tunnel-network
  connection markers show with the top and bottom layers instead of as layers of their own.
- **New known results:** 7×7×7 best known is now 719 tunnels (at most 834), 5×5×5 is at most
  320, and 1×4×7, 1×5×7, 1×7×7 and 2×2×7 are proven.
- `tools/build_library.py --add-run <run folder>` folds a run into the shipped library.

## 1.0.1

- **4×4×4 is now proven optimal:** 164 tunnels (5,248 channels) with 41 controllers and 6
  trunks. The bundled build also uses one cable fewer. Every box up to 4 per side is now
  proven.

## 1.0.0

First release.

- **App** (`AE2ControllerOptimizer.exe`, or `python -m ae2opt`): a 3D designer with live
  rules checking and in-game-style tooltips, an optimizer with pause, resume and continue,
  a library of known results, and config profiles with `.cfg` import. It runs entirely on
  your PC and works offline.
- **Known results** for the default config (8 / 32): every box up to 4 per side proven
  optimal (except 4×4×4), and best-known builds for 4×4×4 to 7×7×7.
- **Optimizer:**
  - CP-SAT search with coloured, anchor-free wiring: smart cable groups of up to 8 tunnels
    feeding the fewest fluix dense trunks.
  - Every result is checked by a simulator that follows AE2's channel pathfinding.
  - Boxes are limited to 9 per side.
- **Export:** structure-block `.nbt` files, optionally with an in-game test rig, and an HTML
  build guide.
- **In-game validation:** checked on AE2 rv6 in three test rounds. All readings matched the
  simulator.
