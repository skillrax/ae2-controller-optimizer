# AE2 1.12.2 Controller Optimizer — Project Plan

Goal: an offline app to **build**, **test** (simulate), and **optimize** ME Controller
multiblocks for P2P channel distribution in Applied Energistics 2 rv6 (1.12.2).

---

## 1. Verdict: methodical (exact solver + faithful simulator), not ML/GA

| Approach | Fit for this problem |
|---|---|
| ML generative model | Poor. No training data. It can't guarantee validity or optimality. It solves a problem we don't have. |
| Genetic / mutation search | Workable but weak. The fitness landscape is jagged: one block flips validity (cross rule) or connectivity. It gives no proof of optimality and it's slow to converge on a 343-cell binary space. |
| **Exact constraint optimization (CP-SAT / ILP)** | **Best.** The search space is small and fully specified (≤ 7×7×7 cells, rules known from source). The objective is linear. It returns a proven optimum and the top-K alternatives. |
| Local search (simulated annealing) | Useful as a **secondary** tool for messy objectives the exact model can't express cleanly, seeded from CP-SAT solutions. |

The core problem is a small, exactly defined combinatorial problem. A solver like Google
OR-Tools CP-SAT solves it to proven optimality, and anything stochastic only approximates
that. A faithful simulator stays the source of truth: every candidate the optimizer finds is
re-checked by the simulator.

---

## 2. Game rules (verified from AE2 `rv6-1.12` source)

These rules decide the model. Each one becomes a unit test in the simulator.

1. **Bounding box** (`ControllerValidator`): all controllers in a grid must fit in a box
   with `max - min < 7` on every axis, so 7×7×7 at most.
2. **All controllers must be face-connected to each other** (`ControllerValidator` only
   walks controller→controller). `PathGridCache.recalcController` requires
   `found == controllers.size()`. Two controller clusters joined only through cable give
   `CONTROLLER_CONFLICT`, which means zero channels for the whole network.
3. **Cross rule** (`TileController.onNeighborChange`): a controller is invalid if it has
   a *full opposite pair* of controller neighbours on **2 or more axes** (for example a plus
   shape). An invalid controller disables all its sides, so it becomes a dead block and can
   split the structure. A pair on one axis (a line) or L/T corners without opposite pairs are
   fine.
4. **Per-face capacity**: each `GridConnection` caps at **32 channels**
   (`canSupportMoreChannels: < 32 // max, PERIOD`). Pathing starts a BFS from every
   non-controller connection of every controller. So each exposed controller face is an
   independent 32-channel source.
5. **Node capacity** (`GridNode`): 0 / 8 / 32 by flags (CANNOT_CARRY / normal / DENSE).
   A dense cable in a gap cell therefore caps at 32 total, even if it touches 6 controller
   faces. That is why builds put **parts** (P2P tunnels) directly on each face instead.
6. **Allocation is greedy BFS, first-come-first-served** (`PathSegment`). Channel results
   can depend on order. The simulator must copy the same visiting order, not compute an
   ideal max-flow.

### Phase 1 result → see [MECHANICS.md](MECHANICS.md)
Resolved from source: ME P2P tunnels need 1 tunnel-network channel each. Parts with no
centre cable are isolated. P2P tunnels can't sit on dense cable. The controller cap is per
face. Extended Life makes the capacities and box size configurable. The build-validity rules
are R1–R11 in MECHANICS.md §5.

---

## 3. The optimization model

Decision variable: `x[c] ∈ {0,1}` for every cell `c` in the 7×7×7 box (343 booleans).

> **Superseded in part by MECHANICS.md §5 (R5–R8).** A face only counts if its tunnel can be
> routed out to the tunnel network through cable cells capped at 8 channels each. The model
> therefore also needs per-gap-cell variables (cable / tunnel count / anchor) and an integer
> flow from tunnel cells to the box boundary, with node capacity 8. The face-count objective
> below is now only an **upper bound**.

**Objective (primary, original):** maximize usable faces = `6·Σx − 2·Σ_adjacent-pairs x[a]·x[b]`
(products linearized with the standard AND constraint). Channels = 32 × usable faces.

Insight: a block with ≤2 controller neighbours adds net faces, 3 adds nothing, and 4+ is
a net loss. The optimum is a sparse, branching, lattice-like shape. That is related to
"maximum induced tree" / snake-in-the-box problems, which are hard for heuristics and easy
for CP-SAT at this size.

**Hard constraints:**
- Cross rule per cell: `x[c] ⇒ ¬(pairX ∧ pairY) ∧ ¬(pairX ∧ pairZ) ∧ ¬(pairY ∧ pairZ)`.
- Connectivity: single-commodity flow from a chosen root over controller cells.
- Symmetry breaking: fix the root and lexicographic ordering across the 48 cube
  symmetries. This makes solving much faster.

**Secondary objectives / user knobs (lexicographic or weighted):**
- Minimize controller count for a target channel count, which saves resources (fluix,
  engineering processors).
- Pareto front: channels vs controller count vs footprint (for example a 7×7×3 limit for
  a wall build).
- Face exclusions: faces reserved for the P2P feed network, power input, or blocked by
  terrain. The user can mark cells as forbidden or required.
- Reachability: optionally require every used gap cell to be reachable from outside
  during construction, which matters for build order.

**Outputs:** the proven optimum, plus top-K distinct solutions (re-solve with
no-good cuts) so the user can pick an aesthetically or practically nicer one.

---

## 4. Architecture

```
core/            pure Python, no UI
  rules.py       controller validity, bbox, connectivity  (ported from AE2 source)
  sim.py         channel simulator: AE2-order BFS, 8/32 caps, P2P links
  model.py       CP-SAT model builder (OR-Tools)
  search.py      top-K enumeration, Pareto sweeps, annealing refiner
  io.py          JSON layout format, .nbt structure / schematic export, BOM
app/             local web UI (served offline by a tiny local server)
  three.js 3D voxel editor: place/remove controllers, mark gaps/P2P
  live validity + channel overlay from sim.py
  "Optimize" panel: constraints, objective, progress, results gallery
tests/
  unit tests per rule (cross shapes, bbox edge 7 vs 8, split clusters…)
  golden layouts with known in-game channel counts
```

**Stack recommendation: Python 3.11+ with OR-Tools for the core, plus a local web UI
(three.js, vendored so it works offline).** OR-Tools' CP-SAT is the strongest free solver,
and its Python bindings are first-class. A browser UI is the easiest way to get a good 3D
voxel editor. Ship it as `python -m ae2opt` (opens the browser) and optionally freeze it
with PyInstaller into a single .exe.

---

## 4a. Game config profiles

No rule constant is hardcoded. Every rule in MECHANICS.md reads from an active **profile**:

| Field | rv6 | UEL key (`AppliedEnergistics2.cfg`) |
|---|---|---|
| `normalChannelCapacity` | 8 (fixed) | `general.normalChannelCapacity` (≤256) |
| `denseChannelCapacity` | 32 (fixed) | `general.denseChannelCapacity` (≤256) |
| `maxControllerSize` X/Y/Z | 7/7/7 (fixed) | `ControllerSize.maxControllerSizeX/Y/Z` (1–63) |
| `channelsEnabled` | `features.Channels` | same |

- **Works out of the box:** the default profile is **8 / 32 / 7×7×7**, channels on. That is
  the same for rv6 and for an untouched UEL config, so no setup is needed.
- **Two ways to change it.** Both produce the same profile object:
  1. **Manual input** (always available, the fallback): a form with normal capacity, dense
     capacity, controller size X/Y/Z, and channels on/off. Values are validated (capacity
     1–256, size 1–63), and out-of-range or unusual values get a warning, not a block.
  2. **Point to a config file:** pick an `AppliedEnergistics2.cfg` (or any file). The parser
     is tolerant:
     - It reads Forge `.cfg` syntax (`category { I:key=value }`) and matches keys **by name
       anywhere in the file**, so a fork that moves a key to another category still works.
     - It also accepts a small alias list for known forks. Adding a fork means adding an
       alias, not code.
     - The result is shown as a **pre-filled manual form**, with each field marked *from file*,
       *default (key not found)*, or *couldn't parse*. The user reviews it and confirms.
     - If nothing is recognized (a different format, or a fork that doesn't expose these
       settings), the app says so and opens the manual form with the defaults. Import never
       fails silently or half-applies.
- **Saved profiles:** save by name (JSON in the app's data folder) and switch between them.
  The default profile is read-only.
- Every saved layout records the profile it was optimized under. Opening it under a different
  profile triggers a re-validation with a warning.
- **Solver strategy depends on box size:** exact CP-SAT for small boxes (about ≤ 9³), and
  repeating unit-cell patterns plus local search for large ones (up to 63³).
- If `channelsEnabled = false`, channels don't matter at all and the app says so instead
  of optimizing.

## 5. "Test": how the simulator is validated against the real game

1. Unit tests per rule (above), written straight from the source behaviour.
2. **Golden in-game tests**: export layouts as `.nbt` structures and build them in a
   1.12.2 dev environment with AE2 (`clean-devEnv-FG7` already exists alongside this
   project). Record channels used and controller state, then commit them as fixtures.
   Optional later step: a tiny dev-only mod or command that loads a layout, fills faces
   with P2P plus channel-hungry dummy parts, and dumps per-face channel counts to JSON for
   fully automated cross-checks.
3. Every optimizer result is re-run through `sim.py` before it is shown.

---

## 6. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| 1. Rules research | Close the open questions in §2. Write `RULES.md` with source citations. | Every rule has a code reference. |
| 2. Core + sim | `rules.py`, `sim.py`, JSON format, unit tests | Hand-built layouts score correctly. Known-bad shapes are rejected. **Done: see README.md (57 tests).** |
| 3. Optimizer | CP-SAT model, symmetry breaking, top-K, Pareto sweep (CLI) | Proven optimum for 7³ and smaller boxes, with solve time reported. **In progress:** the model, tree routing with anchors, simulator check and repair, target mode, and CLI are done; small boxes are proven optimal and cross-checked by brute force. Still to do: top-K, Pareto sweep, and faster solving on 7³. First results (16 workers, default profile): 5³ gives 288 tunnels in 120 s (bound 342); 7³ gives 685 tunnels in 300 s (bound 886). LNS then took 7³ to **708 tunnels = 22,656 channels, 179 controllers** (20 min, still improving; `examples/best_7x7x7.json`). Findings: the limit is routing, not shape (shapes with 806+ exposed faces exist). Lattice seeds and symmetry breaking didn't help on 5³, so both are off by default; LNS is what works. Proving optimality at 7³ looks out of reach: even the routing-free version keeps a large gap (806 found vs 1,125 bound). **Cuts** (cap / exit / tree / degree) plus symmetry breaking made 4×4×3 provable (77 s) and shrank the gaps: 4×4×4 is 164 vs at most 168; 5×5×5 is 298 vs at most 314, down from an ~18% gap to 5.4% (see README). |
| 4. UI | 3D editor, live validation, optimize panel, layer-by-layer build view | Build, test, and optimize all work from the UI, offline. |
| 5. Game validation | `.nbt` export, golden in-game fixtures, optional dump mod | Sim matches the game on all fixtures. **In progress:** `.nbt` export, test rigs, and a 6-test suite with checklist (`examples/ingame/`) are done; waiting on the in-game results. |
| 6. Extras | Bill of materials, P2P frequency plan, annealing for custom objectives, .exe packaging | **Build guide done:** HTML guide with bill of materials, layer viewer with world coordinates, input-linking checklist, exit-to-trunk grouping, and an optimizer-designed tunnel-network controller (outlet mode). Polish step trims cable routes. Packaging still to do. |

The highest-risk item is Phase 1 (the P2P semantics). It decides what "a usable face" means,
so it comes first. Everything after it is straightforward engineering.
