# ae2opt

An offline tool for designing ME controller / P2P builds for Applied Energistics 2 on
Minecraft 1.12.2 (rv6 and Unofficial Extended Life).

- [PLAN.md](PLAN.md): overall design and phases
- [MECHANICS.md](MECHANICS.md): the game rules, traced to AE2 source, and build rules R1–R11

## Status

Working: config profiles, config-file import, layout model, channel simulator, rules
checker, and the CP-SAT optimizer. There's no UI yet. The core is pure Python 3.11+;
only the optimizer needs OR-Tools (`pip install ortools`).

| Module | What it does |
|---|---|
| `ae2opt/profile.py` | Game-config profile (default 8 / 32 / 7×7×7), validation, and named profiles saved as JSON |
| `ae2opt/cfgimport.py` | Tolerant import of `AppliedEnergistics2.cfg` (Forge cfg, key=value, or JSON). Shows each field as from file / default / couldn't parse |
| `ae2opt/layout.py` | Blocks and parts in cells, with JSON save/load. Rejects placements AE2 itself refuses |
| `ae2opt/graph.py` | Layout → AE2 grid nodes and connections (flags, capacities, cross rule, bus sides) |
| `ae2opt/sim.py` | Port of AE2's pathfinding (greedy BFS, ad-hoc, conflict). Settles ME P2P links |
| `ae2opt/rules.py` | R1–R11 plus frequency checks, and metrics (faces, tunnels, channels) |
| `ae2opt/optimize.py` | CP-SAT model: controllers + tunnel faces + tunnel-network routing. Every result is re-checked by the simulator |
| `ae2opt/render.py` | Text view of a layout, layer by layer |

## How the optimizer works

It places three things in the box (default: the profile's controller size limit):
- controller blocks
- input tunnels on controller faces
- normal-cable "tunnel cells", in gaps inside the box and in a 1-block layer around it

Every tunnel's tunnel-network channel must be routed out of that region, through cable
cells capped at the normal capacity (rules R5–R8).

**Routes are trees.** Each cable cell has exactly one parent. The exported build puts a
cable anchor on every cable-to-cable side that isn't a route, so AE2's greedy pathing
can only follow the planned routes. Controller faces next to a cable cell that carry no
tunnel get an anchor too (R6).

**Exit cells** are where your tunnel network (its own controller, dense trunks) must
connect. Every other outer side is anchored. The simulator models the tunnel network as
ideal "ports" beyond the exits, so check that your real trunk can carry the reported
"tunnel network needs N channels".

**Results:**
- Small boxes are solved to proven optimum.
- Large boxes report the best layout found within the time limit, plus the solver's upper bound.
- If the simulator ever disagrees with the model, tunnels that get no channel are swapped
  for anchors, so the returned build always passes the rules check.

**Search phases:**
1. **Optional warm start:** score a saved layout (`--start`) and/or repeating lattice
   shapes (`--seeds N`), each with its shape held fixed, and use the best as a hint.
2. **Main CP-SAT solve** (`--time`).
3. **Optional LNS** (`--lns`): repeatedly free the controllers in a random small box,
   slab, or scattered set of cells, keep the rest, and re-solve, accepting only layouts
   at least as good. On 7×7×7 this is what makes progress.

**Proving optimality.** The model adds valid "cuts" that tighten the solver's upper bound
without removing any real layout:
- `cap`: capacity scales with how much a cell is a cable.
- `exit`: a tunnel cell keeps one side free for its route out.
- `tree`: n connected blocks have at least n−1 touching pairs, so tunnels ≤ 4n+2.
- `degree`: every controller touches another.

Symmetry breaking (mirror plus axis order) is also on. Measured on 16 workers:

| Box | Result | Time |
|---|---|---|
| 3×3×3 | proven optimal, 74 tunnels | 5 s |
| 4×3×3 | proven optimal, 97 tunnels | 8 s (was 54 s without cuts) |
| 4×4×3 | proven optimal, 127 tunnels | 77 s (unproven without cuts) |
| 4×4×4 | 164 found, at most 168 (2.4% gap) | not proven in 15 min |
| 5×5×5 | 298 found, at most 314 (5.4% gap) | not proven in 15 min |

**Best known 7×7×7 (default profile):** 708 tunnels = 22,656 channels with 179
controllers, in [examples/best_7x7x7.json](examples/best_7x7x7.json). It comes from 5 min
of solving plus 20 min of LNS, and LNS was still improving at the end. It isn't proven
optimal; the solver's best upper bound is 886.

## Long runs: live progress, pause and resume

`run` puts the solver in its own process, shows a live panel, and checkpoints every
improvement to a run folder:

```bash
python -m ae2opt run --box 5 5 5 --workers 16 --hours 8 --dir runs/5x5x5
```

```
 AE2 controller optimizer · 5x5x5 · Default (8/32/7x7x7) · session 1
 state        RUNNING
 active time  01:23:45   (this session 01:23:45 / 08:00:00)
 improved     4m10s ago
 best found   298 tunnels = 9,536 channels · 72 controllers
 proven max   314 tunnels   gap 5.4%
 [██████████████████████████████████████░░]  94.9% of proven max
 phase        main solve
 p pause · r resume · q save & stop
```

**Keys:**
- **p — pause:** the solver process is frozen by the OS. It uses no CPU, loses nothing, and
  continues exactly where it was. Its memory stays allocated.
- **r — resume:** continue a paused solver.
- **q — save and stop** (Ctrl+C does the same): the solver stops cleanly and the best layout
  is saved.

**Defaults that keep your PC usable:** the solver runs at below-normal priority, so other
programs come first (`--normal-priority` turns this off). `--hours` limits active time;
paused time doesn't count.

**Continue later**, even after a reboot:

```bash
python -m ae2opt run --resume runs/5x5x5 --hours 4
```

A resumed run starts from the best layout so far and keeps the best proven bound, since a
proven bound stays valid. The solver's internal search can't be saved, so the proof work
itself starts over each session. `best.json` is never replaced by a worse layout.

**Modes:**
- `--mode prove` (default) is one open-ended solve that ends when it's proven optimal. Use
  it for boxes up to about 5×5×5.
- `--mode improve` runs `--main` seconds of main solve, then neighbourhood search until you
  stop it. Use it for 7×7×7, where a proof isn't realistic.

**Run folder:**
- `best.json`: best layout so far, checkable with `check`
- `run.json`: settings, best result, best bound, active time
- `progress.jsonl`: every improvement and bound change, with timestamps
- `result.txt`: final summary
- `worker.log`: solver output

## Build guide

```bash
python -m ae2opt guide best.json --out best.html
```

This writes one self-contained HTML page (it works offline). Open it in any browser:
- **Bill of materials:** controllers, smart and dense cables, input tunnels, cable anchors,
  and the output tunnels you'll need.
- **Layer viewer:** step through the build one layer at a time. Parts are drawn on the edge
  of the cell they face. Click a cell for its parts and **world coordinates**: type your
  build's corner position once and every cell shows where it goes. Tick layers off as you
  build them.
- **Input list for linking:** inputs are labelled I1…IN in build order with their world
  positions. Tick each one off as you link it with a memory card; ticks are saved in the browser.
- **Tunnel network:** the channels it needs (one per input and output), how the build's
  exits group into dense trunks of 32, and a **suggested tunnel-network controller**
  designed by the same optimizer. Each 32-channel outlet gets its own dense cable and route
  out, so no two outlets share a cable.

The optimizer also ends every run with a short polish step. It keeps the controllers and
tunnels and shortens cable routes, which cuts the cable and anchor count.

Example: [examples/best_7x7x7.html](examples/best_7x7x7.html) is the guide for the
708-tunnel layout. Its tunnel network needs 1,416 channels, which means 46 outlets from a
12–13 controller tunnel-network controller.

## Exporting to Minecraft (.nbt structures)

`export` writes any layout as a 1.12.2 structure file, which you load with a structure block.
The block and part IDs and the cable-bus data come from the AE2 source and are the same in
rv6 and Extended Life. P2P tunnels keep their frequencies, so inputs and outputs pair up when
loaded.

```bash
python -m ae2opt export best.json best.nbt --rig --box 7 7 7
```

`--rig` wraps the build in a self-contained test rig:
- a few separate trees of dense smart cable that collect the exits, each ending in one
  "root" cable on a snake-shaped tunnel-network controller
- creative energy cells
- two output tunnels feeding 40 terminals each

The build stays visible. The rig also prints what the simulator expects the game to show.
Results are read with WAILA/HWYLA or The One Probe (AE2 shows `X of 32 Channels` on smart
cables, and `Device Online` / `Device Missing Channel` on tunnels and terminals). The roots'
readings add up to the tunnel network's channel total.

## In-game validation

```bash
python -m ae2opt export-tests --out examples/ingame
```

This generates six structures plus [examples/ingame/TESTING.md](examples/ingame/TESTING.md),
a fill-in checklist. T1–T4 are builds that should work exactly as predicted (2×2×2, a
3×1×3 ring, 3×3×3, 4×4×3). N1 and N2 should fail in specific ways: a capacity overload,
and an uncapped controller face merging the networks. **Until these pass in game, optimizer
results are unverified.**

## Usage

Find a layout for the default 7×7×7 controller, then keep improving it with LNS:

```bash
python -m ae2opt optimize --time 300 --workers 16 --out best.json
```

```bash
python -m ae2opt optimize --start best.json --time 60 --lns 1800 --lns-step 20 --workers 16 --out best.json
```

Find the fewest controllers that still give 100 tunnels in a 5×5×5 box:

```bash
python -m ae2opt optimize --box 5 5 5 --target 100
```

Print a saved layout layer by layer:

```bash
python -m ae2opt show best.json
```

Check a layout:

```bash
python -m ae2opt check examples/basic_p2p.json
```

The same check with a pack's config and a manual override (later options override earlier ones):

```bash
python -m ae2opt check examples/basic_p2p.json --cfg path/to/AppliedEnergistics2.cfg --dense 64
```

Preview what a config file imports as, and optionally save it as a named profile:

```bash
python -m ae2opt import-cfg path/to/AppliedEnergistics2.cfg --save "My pack"
```

Run the tests:

```bash
python -m unittest discover -s tests -t .
```

## Simulator limits (also in sim.py)

- Power is on or off. A grid is powered by an energy acceptor or a controller marked
  `powered`. Power amounts aren't simulated.
- In game, the order connections are found depends on placement and chunk-load order. The
  simulator uses a fixed order. Totals are exact unless two routes compete for the same
  capacity; then the in-game winner can differ.
- Multiblock devices (crafting CPUs, spatial) aren't modelled.
- Nothing has been checked against an in-game build yet (PLAN.md Phase 5).
