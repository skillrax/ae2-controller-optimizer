# In-game results log

Each entry records what was observed, on which setup, and against which version of the
test rig. TESTING.md is regenerated, so results live here.

## Run 1: T1, solid-shell rig (commit c0e541e)

**Setup:** plain AE2 rv6 with JEI and WAILA, nothing else installed.

| Check | Expected | Observed |
|---|---|---|
| Main controllers (6 blocks): state | online | online |
| Tunnel-network controllers (20 blocks): state | online | online |
| Input tunnels lit | 23 of 23 | unsure; the Network Tool lists 25 P2P tunnels on the tunnel network |
| Output tunnels lit | 2 of 2 | 2 of 2 |
| Network Tool on the shell: channels used | 25 | 25, but that's the number of P2P **parts** listed, not channels |
| Network Tool on a rig's dense cable: channels used | 64 | ? (not shown) |
| Rig terminals | 32 of 40 each | hard to tell; terminals don't visibly go offline, but those without a channel show nothing in their GUI |

**What this establishes:**
- **Loads cleanly:** the structure loads, and every cable bus, part and controller comes in.
- **No merge:** both networks come online, separately.
- **Every tunnel is on the tunnel network:** all 25 tunnels (23 inputs + 2 outputs) are there, so none are cut off.
- **Outputs pair up:** both outputs are lit, which means the input/output frequencies paired after loading.

**What it doesn't establish:** whether every input actually has a channel, and whether a rig carries exactly 32.

**Problems with the rig, both fixed after this run:**
- **The Network Tool doesn't show channel counts**, only a parts list.
- **The solid dense shell hid the build**, so nothing could be checked by eye.

**Changes:**
- **Sparse collector trees** replace the shell.
- **The checklist uses WAILA readings** (`X of 32 Channels`, `Device Online` / `Device Missing Channel`).
- **The builds themselves are unchanged.**

## Run 2: all tests, collector-tree rig (commit 3d98604)

**Setup:** plain AE2 rv6 with JEI and WAILA. T1 was checked reading by reading. T2–T4 got a
faster check of the important parts. N1 and N2 were checked fully. The filled-in checklist is
TESTING.md at the commit that records this run.

| Test | Result |
|---|---|
| T1 2×2×2 | **Every reading matches.** All 7 collector roots match their predicted counts exactly (4, 3, 6, 2, 1, 6, 3 = 25). Both rigs: output cable 1 of 8, trunk 32 of 32, terminals as predicted. All visible tunnels `Device Online` (inputs show `Unlinked`, as expected without outputs). |
| T2 3×1×3 ring | Controllers online; 33 total; both rigs exact (1 of 8, 32 of 32, terminals OK); visible tunnels online. Individual roots not read. |
| T3 3×3×3 | Controllers online; 75 total; visible tunnels online. Rigs and individual roots not read. |
| T4 4×4×3 | Controllers online; 128 total; visible tunnels online. The tunnel cable carrying exactly 8 seemed to work with no failed tunnels, but it's hard to see for sure. Rigs and individual roots not read. |
| N1 capacity | **Matches.** Normal cable `8 of 8 Channels`; 8 tunnels online, exactly 1 `Device Missing Channel` (the south bus, which is also the simulator's pick). |
| N2 uncapped face | **Matches.** Controllers red (conflicted), tunnels `Device Missing Channel`, roots all 0, terminals offline. |

**What this confirms about the model:**
- **Cable-bus parts:** a tunnel needs a cable in its own block, and a cable anchor (or any part) stops the cable connecting on that side (T1–T4, N2).
- **Uncapped faces merge networks:** an uncapped controller face next to a tunnel cable joins the two networks, and the two controller structures then conflict (N2).
- **Normal cable carries 8, and the 9th fails** (N1, exact).
- **Tunnel channels:**
  - each ME P2P tunnel takes 1 tunnel-network channel
  - one input carries exactly 32 to its output
  - a dense cable carries 32 (the T1 rigs)
- **Anchored routes behave as modelled:** per-tree channel counts match exactly (T1).
- **Generated builds work in game** at 2×2×2, a 3×1×3 ring, 3×3×3 and 4×4×3.

**Weaker spots:**
- **T2–T4's per-root readings weren't read.** Their totals matched, but where the channels went wasn't checked.
- **T4's 8-load tunnel cable wasn't confirmed directly.** N1 covers the same 8-channel limit exactly.

**Tester feedback:** finding blocks by relative coordinate is far too slow. The useful checks
are the totals, the rigs (once per rig design is enough), "any `Device Missing Channel`?",
and the negative tests.
