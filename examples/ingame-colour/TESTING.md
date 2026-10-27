# Colour wiring check

Three quick structures. Load each with a structure block as before (copy the `.nbt` files into `saves/<world>/structures/`). Use WAILA: tunnels show `Device Online`, `Device Missing Channel`, or `Device Offline` (no power).

Profile: Default (8/32/7x7x7). Plain AE2 rv6 or Extended Life.

## C1: colours keep groups apart (`ae2opt_c1`)

A controller, then one **white** smart cable, then group **A**: 2 white cable cells with 8 tunnels, above the **gold** block. Then group **B**: 2 **orange** cells with 8 tunnels, above the **lapis** block. B touches A, but has no route of its own.

| Check | Expected | In game |
|---|---|---|
| Group A tunnels (over gold) | **8 of 8** `Device Online` | yes |
| Group B tunnels (over lapis) | **8 of 8** `Device Offline` (cut off: no power, no channel) | yes |

## C2: control, same build with B in fluix (`ae2opt_c2`)

Fluix connects to every colour, so A and B merge and 16 tunnels share the one white cable, which carries 8.

| Check | Expected | In game |
|---|---|---|
| Tunnels online, A and B together | **8 of 16** `Device Online`, the rest `Device Missing Channel` | yes |
| The white cable next to the controller | `8 of 8 Channels` | yes |

If C1 shows B online, or C2 shows all 16 online, colours don't behave as the model assumes. Stop there and tell me.

## T5: coloured, anchor-free 3x3x3 (`ae2opt_t5`, 7x16x10)

The coloured 3x3x3 optimum in the same kind of test rig as before. The optimizer's build has no cable anchors; the rig may add one or two where it attaches to the build. Gold blocks mark the collector roots; emerald blocks mark each rig's dense cable.

| Check | Expected | In game |
|---|---|---|
| All controllers (build and snake) | online, none red | yes |
| WAILA on the cable next to each of the 3 **gold** blocks | `23`, `30`, `22` (any order), adding up to **75** | yes |
| WAILA on the cable next to each **emerald** block | `32 of 32 Channels` | yes |
| Any tunnel you can see showing `Device Missing Channel` | **none** | none |

- The main network's power cell replaces one outer tunnel, so the build has 73 inputs. The total is those plus the 2 rig outputs.
- Cable colours in the build: smart cables 35 white, 21 orange; 7 dense cables, all fluix. The rig's own cables (collector, outputs, terminals) are fluix too.
