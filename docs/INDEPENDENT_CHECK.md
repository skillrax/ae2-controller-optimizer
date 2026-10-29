# Independent check of the proven optima

This is item 1 of the research plan ([RESEARCH.md §7](RESEARCH.md)): a second exact model,
written without seeing the first, re-solves the boxes the library calls proven. If the
first model forbade valid builds (§4.2, completeness), the second one would find more
tunnels somewhere.

**How it was written.** The model is [`lab/independent.py`](../lab/independent.py).
It did not open, grep or import `ae2opt/optimize.py` or any other file that builds the
CP-SAT model. What it did read:

- [MECHANICS.md](../MECHANICS.md) (R1–R11, W1–W5) and [RESEARCH.md](RESEARCH.md).
- `ae2opt/layout.py` and `ae2opt/geometry.py`, for the build file format.
- The CLI text in `ae2opt/__main__.py` and the headers of `ae2opt/rules.py`, to learn how
  `check` is called and what it reports.
- `ae2opt/library/index.json`, for the target values (it holds results, not model code).

It runs `ae2opt.rules.check` (simulator and rules checker) only on its own builds.

**One caveat on independence.** All of RESEARCH.md was read before the model was written,
not only §1. Its §3 names the first model's shared variables (x, k, s, d, f, h, sink) and
sketches its routing constraints: h ≤ 32·par, parents summing to k, a sink per exit. The
new model also routes with parent links and capped loads. That is the natural encoding of
W1–W3 as written, but it is not a blind choice. A completeness bug shared through that idea
would not be caught here. Everything else (cell types, the attachment rule, the induced-tree
rule for trunks, connectivity, symmetry breaking, the margin reading) was written from the
rules alone.

## Verdict

**No disagreement.** The library calls 24 boxes proven:

- **23 re-proven, same optimum.** These are the 1×1×c lines (4c + 2) through 3×4×4 (126),
  each re-proven with the library's value.
- **4×4×4 not re-proven.** The model found a 164-tunnel build, the library's value, but
  after the 4-hour limit its bound was still 168. So 164 is reachable under this reading of
  the rules, but this model has not shown that 165–168 are impossible. That box is **not
  independently checked**.
- **Simulator:** every build the model returned is valid, with all its tunnels working.
- **Counting bound:** no value is above it.

Nothing disagreed, so there was nothing to explain.

| Box | This model | Library | Counting bound | Verdict | Simulator | Time (s) | Time, mirrors only, no cut (s) |
|---|---|---|---|---|---|---|---|
| 1×1×1 | **6** | 6 | 6 | agree | valid, 6 working | 0.1 | 0.1 |
| 1×1×2 | **10** | 10 | 10 | agree | valid, 10 working | 0.1 | 0.1 |
| 1×1×3 | **14** | 14 | 14 | agree | valid, 14 working | 0.2 | 0.2 |
| 1×1×4 | **18** | 18 | 18 | agree | valid, 18 working | 0.2 | 0.2 |
| 1×2×2 | **16** | 16 | 17 | agree | valid, 16 working | 0.2 | 0.1 |
| 1×2×3 | **22** | 22 | 24 | agree | valid, 22 working | 0.3 | 0.3 |
| 2×2×2 | **24** | 24 | 29 | agree | valid, 24 working | 0.3 | 0.3 |
| 1×2×4 | **28** | 28 | 31 | agree | valid, 28 working | 0.3 | 0.3 |
| 1×3×3 | **32** | 32 | 34 | agree | valid, 32 working | 0.4 | 0.4 |
| 2×2×3 | **36** | 36 | 41 | agree | valid, 36 working | 0.5 | 0.5 |
| 1×3×4 | **40** | 40 | 44 | agree | valid, 40 working | 0.5 | 0.5 |
| 1×4×4 | **52** | 52 | 57 | agree | valid, 52 working | 0.6 | 0.6 |
| 2×2×4 | **46** | 46 | 53 | agree | valid, 46 working | 1.1 | 1.2 |
| 2×3×3 | **52** | 52 | 58 | agree | valid, 52 working | 1.0 | 1.4 |
| 1×4×7 | **88** | 88 | 96 | agree | valid, 88 working | 1.0 | 1.1 |
| 2×3×4 | **68** | 68 | 75 | agree | valid, 68 working | 1.9 | 3.5 |
| 3×3×3 | **74** | 74 | 82 | agree | valid, 74 working | 6.4 | 8.5 |
| 2×2×7 | **78** | 78 | 89 | agree | valid, 78 working | 10.4 | 46.3 |
| 1×5×7 | **108** | 108 | 118 | agree | valid, 108 working | 6.1 | 4.3 |
| 2×4×4 | **88** | 88 | 97 | agree | valid, 88 working | 16.7 | 51.2 |
| 3×3×4 | **96** | 96 | 106 | agree | valid, 96 working | 131.0 | 1046.0 |
| 1×7×7 | **146** | 146 | 162 | agree | valid, 146 working | 31.6 | 3.7 |
| 3×4×4 | **126** | 126 | 137 | agree | valid, 126 working | 1331.0 | — |
| 4×4×4 | 164 found, ≤ 168 | 164 | 177 | not proven | valid, 164 working | 14401.2 | — |

- **Times** are wall-clock on 16 threads (CP-SAT 9.15). The final runs used mirror and
  axis symmetry plus the face-count cut. Some of the small boxes ran while a variant check
  shared the machine, so their times are inflated (1×7×7: 31.6 s vs 3.7 s alone).
- **Last column:** the first version of the model, with mirror symmetry only and no cut,
  for the boxes it reached before it was stopped. It agrees on all 22. Its proof of 3×3×4
  took 1046 s, against 97–131 s with the axis swap and the cut. It was stopped during
  3×4×4.
- **Builds:** the optimum the model returned for each box is in
  [`docs/independent_check/`](independent_check/), with `results.jsonl` (final runs) and
  `results_v1_mirror_only.jsonl` (first version). Re-check one with
  `python -m ae2opt check docs/independent_check/3x4x4.json`.
- **4×4×4** would need a longer run (the library's own proof took 57 min from a warm
  start at 164). `python -m lab.independent 4 4 4 --time 43200` is the next step.

### Cuts and symmetry breaking used, and why each is valid

Every final proof used exactly these three. Each holds for **every** solution of the model,
or removes only solutions that have an equal-count image that stays.

1. **Mirror symmetry.** For each axis, the low half of the box holds at least as many
   controllers as the high half. *Valid:* every rule above is invariant under mirroring one
   axis of the box, and the margin is too. Mirroring an axis whose high half is heavier
   fixes that axis and leaves the other two half-counts unchanged. So every build has an
   image, with the same T, that meets all three.
2. **Axis-swap symmetry** (only between axes of equal length, taken in order). The count of
   controllers with p_i < p_j is at least the count with p_i > p_j. *Valid:* after the
   mirrors, call axis i ahead of axis j if #(p_i < p_j) ≥ #(p_i > p_j). Every tournament has
   a Hamiltonian path (Rédei), so the equal axes can be permuted to put each one ahead of the
   next. Permuting equal-length axes maps the mirror constraints onto each other, so they
   still hold. The rules are invariant under axis permutations of the box.
3. **Face-count cut** (Theorem 1's controller side, RESEARCH.md §2). T + 2·E_X ≤ 6n and
   E_X ≥ n − 1, where n is the number of controllers and E_X the number of touching
   controller pairs. *Valid for every solution:* each controller face touches another
   controller (counted twice in 2·E_X), carries one tunnel, or carries none. A
   face-connected set of n cells has a spanning tree, so it has at least n − 1 touching
   pairs.

The model also contains an optional **cable-face cut** (`--cable-cut`). It forbids two cells
being each other's parent and bounds each box cable cell's faces. It was **not used** for
any result above, because it didn't help (3×3×4: 100.8 s with it vs 97.1 s without). Its
argument: a 2-cycle of parent links has no root, so it carries no tunnels and touches no
controller or dense cable. Deleting it leaves a valid build with the same T. But it is
not needed.

### Checking the margin reading

D3 doesn't say whether "one cell outside" includes the edge and corner cells of the shell.
The main model includes them. A variant with only the face-adjacent shell cells
(`FACE_MARGIN`, mirror symmetry only, 4 threads) re-proved 1×1×1, 1×1×4, 1×2×2, 1×2×3,
2×2×2, 1×3×3, 2×2×3, 1×3×4, 2×3×3, 2×3×4 and 3×3×3 with the **same optima**. On these boxes
the reading makes no difference. The variant is not in the repository; it is the one-line
region filter described here.

### What agreement does and does not show

- It is evidence of **completeness** (RESEARCH.md §4.2): a second encoding, written from the
  rules, finds no build the first one missed on any proven box up to 3×4×4. Where the two encodings
  differ in form, they agree in value. Those forms are: the attachment rule as "every
  smart–dense contact is the group root's attachment", the trunk as an induced tree,
  exits only outside the box, and connectivity by flow.
- It does **not** check the rules themselves (MECHANICS.md against the game), or the
  design rules D1–D4.
- It does **not** remove the trust in CP-SAT. Both proofs use the same solver, and OPTIMAL
  carries no certificate.
- As noted above, the routing idea (parent links with capped loads) was not chosen blind.

## Interpretation of the rules

The model is a CP-SAT model. Every point below is a reading of the written rules; where the
text left a choice, the choice is stated.

**Cells.** The box B is a×b×c. The region R is B grown by one cell in every direction,
**edges and corners included**, so R is (a+2)×(b+2)×(c+2) (D3, "one cell outside it").
Every cell of R is empty, smart (normal) cable or dense cable. Cells of B may instead hold a
controller. Nothing is placed outside R except the tunnel network's continuation beyond the
exits, which is not modelled.

**Controllers.**

- At least one controller, all inside B (D-rules), face-connected (R2). Encoded as a
  single-commodity flow: one chosen controller sends one unit to every other controller
  along controller–controller faces.
- Cross rule (R3): no controller has both opposite neighbours on two or more axes.
- R1 (7×7×7) holds automatically for the boxes checked.
- Controllers need not fill the box. T\* is then monotone in the box by definition.

**Tunnels (R4, R6, W5, D1).** Every face between a controller and a smart cable cell carries
one input tunnel (anchor-free: no face is capped with an anchor, and an uncapped face would
merge the networks). Each such tunnel is counted. A controller face facing empty space or
another controller gives nothing. The objective is the number of tunnels.

**Dense cable never touches a controller** (R8, W3), so no tunnel ever sits on dense cable.

**Smart groups (R5, R7, R8, W1, W2).** Every smart cell has exactly one *outlet*:

1. a smart **parent** (a touching smart cell of the same group), or
2. an **attachment**: it touches exactly one dense cell, and that dense cell takes its load.
   Any smart cell that touches dense cable must be an attachment, so in each group only the
   root touches dense cable, and only one dense cell (W2: "one smart cell, one dense
   parent").
3. an **exit**: the cell lies outside B, and the tunnel network continues from it outward,
   beyond the margin (D3). Exits are not allowed inside B.

A flow of tunnels runs along the outlets. Each cell sends on its own tunnels plus what its
children send it, and every outlet carries at most 8. So every group's root carries the
whole group, and **a group holds at most 8 tunnels** (W1). Each smart cell must send at
least 1, so there is no idle smart cable. R7 (an open side) follows: the outlet is a side
that faces no controller. A cell with six controller neighbours has no outlet and can't be
cable. A tunnel's route never passes through another tunnel's outer side (R9), because routes
run only through centre cables.

**Groups may contain extra internal contacts** (a cycle of same-group cells). This is
harmless: W1 bounds the group total, not a route. The model counts a group as the tree of
parent links.

**Dense trunks (W3).** Every dense cell has a dense parent or is the trunk's root. The root
exits beyond the margin, so it lies outside B. **Every two touching dense cells are parent
and child**, so each trunk is an induced tree and two trunks never touch. Dense cells take the
loads of their attached groups and of their dense children, and every link carries at most
32, so a trunk holds at most 32. Each dense cell must carry at least 1.

**Colours (W4).** Touching groups need different dyes. The model doesn't encode this. After
each solve, the touching graph of the groups is coloured greedily with the 16 dyes, and a build
without a 16-colouring would be rejected. (Never needed: every build was coloured, most with
few dyes.) Dense cells are fluix. A smart cell of one group touching a smart cell of another
is fine once they differ in colour. A group touching a dense cell other than its attachment
is not, because fluix connects to every dye; the attachment rule forbids it.

**Beyond the margin.** Each exit (a group's exit cell or a trunk's root) is assumed to reach
the tunnel network through the cell just outside it, away from the box. Those cells touch
only their own exit cell and are not otherwise constrained. For the simulator, each one
becomes a tunnel-network port (`type: "port"`). No limit is put on the number of exits.

**What is not modelled.** The tunnel-network controller (R10) and power (R11) are outside
the build, as in the library. One controller is marked powered for the simulator.

**Why the loads rule out phantom cycles.** Parent links could in principle close a loop
with no root. A loop carries no tunnels: summing "out = own tunnels + in" around a loop and
the trees feeding it gives zero tunnels for all of them, and the "carry at least 1" rule then
rules out the feeding trees. A bare loop of tunnel-free cable can remain. The decoder drops
it, and it asserts that the loop holds no tunnel. Such a loop can't touch dense cable or a
controller: touching dense cable would make a loop cell an attachment, and touching a
controller would give it a tunnel.

**Symmetry breaking and cuts** are listed with their validity arguments under the verdict.
