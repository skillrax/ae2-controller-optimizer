# Changelog

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
