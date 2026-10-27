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
