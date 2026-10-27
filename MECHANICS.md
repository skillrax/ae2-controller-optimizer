# AE2 1.12.2 Mechanics — Controllers, Cables, P2P

Everything here was read from source:
- **rv6** = `AppliedEnergistics/Applied-Energistics-2` branch `rv6-1.12`
- **UEL** = `AE2-UEL/Applied-Energistics-2` (Unofficial Extended Life), `master`

File references are relative to `src/main/java/appeng/`. Unless marked otherwise, a rule is
identical in both. Items marked **[in-game check]** follow from the code but should be confirmed
with a test build.

---

## 0. Terminology used in this project

A P2P controller build involves **two separate ME grids** that touch physically but must never
merge:

| Term | What it is | What lives on it |
|---|---|---|
| **Main network** (channel source) | The big controller multiblock whose faces supply channels. | Main controllers, the **outer** side of every ME P2P tunnel, and everything downstream of the output tunnels (the actual machines). |
| **Tunnel network** (carrier) | The grid the P2P tunnel *parts* sit on. It gives each tunnel its own channel and power, and it holds the frequency registry. | Cable inside every tunnel's block, the **inner** side of every ME P2P tunnel (inputs and outputs), its own controller or ad-hoc setup, and its own power. |

(If "P2P controller network" and "carrier network" mean something different to you, the two rows
above are the two things to map onto.)

---

## 1. ME Controller

| Rule | Source |
|---|---|
| Flags: `CANNOT_CARRY, DENSE_CAPACITY`. Idle power **3 AE/t** per block. | `tile/networking/TileController.java:56-57` |
| **Cross rule:** a block is invalid if it has a full opposite pair of controller neighbours on 2 or more axes. Pairs count only if *both* opposite neighbours are loaded controllers. Lines and L/T corners are fine. | `TileController.onNeighborChange` |
| An invalid block disables **all its sides**. It becomes a dead block that connects to nothing and can split the structure. | same, `setValidSides(none)` |
| **Bounding box:** every controller in a grid must satisfy `max − min < 7` on each axis (7×7×7 maximum). UEL: configurable per axis, `[ControllerSize] maxControllerSizeX/Y/Z`, default 7, cap 63. | `me/pathfinding/ControllerValidator.java` |
| **All controllers must form one face-connected cluster.** The validator only walks controller→controller, and the grid requires `found == controllers.size()`. A second cluster joined to the same grid by cable means **CONTROLLER_CONFLICT**, which gives the whole grid 0 channels. | `me/cache/PathGridCache.recalcController` |
| Controllers are uncoloured (transparent), so **any** cable colour connects to them. Colour cannot isolate a cable from a controller face. | `me/GridNode.java:536` (`AEColor.matches`) |

### Channel output per face
- Pathing seeds one BFS segment for **every connection from a controller to a non-controller
  node** (`PathGridCache`, ~line 150).
- The controller node is `CANNOT_CARRY`. A route walking back toward the controller stops at the
  connection (`GridConnection.getControllerRoute` returns null), so the controller node itself is
  never a bottleneck.
- Each `GridConnection` caps at **32** (`GridConnection.canSupportMoreChannels`: `< 32 // max, PERIOD`).
  UEL: `< denseChannelCapacity` (config, default 32, max 256).
- **⇒ Every controller face that connects to a non-controller node is an independent 32-channel
  source.** A face touching another controller, or touching nothing connectable, gives nothing.

---

## 2. Cables

| Type | Node capacity | Can hold parts? | Source |
|---|---|---|---|
| Glass / Covered / **Smart** | **8** (UEL: `normalChannelCapacity`) | Yes (`BusSupport.CABLE`) | `parts/networking/PartCable.java:70,76` |
| Dense / **Dense Smart** | **32** (UEL: `denseChannelCapacity`) | **Only parts that allow `DENSE_CABLE`** | `parts/networking/PartDenseCable.java:46,50` |
| Quartz fiber | 0 (`CANNOT_CARRY`). Shares power only, never channels. | — | `parts/networking/PartQuartzFiber.java:63-65` |

- "Smart" is purely visual: smart = normal (8), dense smart = dense (32).
- Node capacity comes from flags: `CANNOT_CARRY` → 0, `DENSE_CAPACITY` → 32, otherwise 8
  (`me/GridNode.java:213`). Every node on a route, plus every connection (32), must have room.
- Both cable types are `PREFERRED`. Pathing visits connections to preferred nodes first
  (`GridNode.ConnectionComparator`, ~line 743).
- Cables connect if their colours match or either side is fluix/transparent (`GridNode.canConnect`).
- Cables draw 0 idle power.

### Parts and the cable bus (critical for P2P)
- A part's own grid node has **no external sides** (`parts/AEBasePart.java:95`,
  `setValidSides(none)`). It connects **only** to the cable in the centre of its own block
  (`parts/CableBusContainer.java`, internal connection on placement and in `addToWorld`).
  **A part in a block with no centre cable is isolated: no power, no channel.**
- The centre cable **does not connect out of any side that holds a part**
  (`CableBusContainer.updateConnections`, ~line 572). A part, including a **cable anchor**,
  therefore *caps* that side.
- Facing a neighbour, a bus block exposes the part's `getExternalFacingNode()` if a part is on
  that side, otherwise the centre cable (`CableBusContainer.getGridNode(side)`).
- Default `canBePlacedOn` is `CABLE` only (`AEBasePart.java:516`). **P2P tunnels cannot go on
  dense cable.** Cable anchors can go on both (`parts/misc/PartCableAnchor.java:238`).

---

## 3. ME P2P Tunnel

Each tunnel has **two grid nodes** (`parts/p2p/PartP2PTunnelME.java:71-72`):

| Node | Flags | Connects to | Network |
|---|---|---|---|
| **Inner** (main proxy) | `REQUIRE_CHANNEL, COMPRESSED_CHANNEL`. Capacity 8. | Only the centre cable of its own block. | **Tunnel network** |
| **Outer** proxy | `DENSE_CAPACITY, CANNOT_CARRY_COMPRESSED`. Capacity 32. | Only the block it faces (`setValidSides(facing)`). | **Main network** |

- **The inner node needs 1 channel** from the tunnel network. It is a "compressed" request
  (`PathSegment.useDenseChannel`): the route back to a tunnel-network controller must have room
  at every node and **must not pass through any `CANNOT_CARRY_COMPRESSED` node**, which means
  no other ME tunnel's outer side. **You can't power or channel a tunnel through another ME tunnel.**
- A tunnel is **active** only when its inner node is powered and has a channel. While active, an
  **input** creates one `GridConnection` from its outer node to the outer node of **every active
  output on the same grid and frequency** (`updateConnections`). When it goes inactive or
  unpowered, those connections are destroyed.
- Frequencies live in `P2PCache`, which exists **per grid**. **Input and outputs must be on the
  same tunnel network.** There is one input per frequency (`P2PCache.inputs`).
- Channel flow: device → … → output outer (32) → tunnel connection (32) → input outer (32) →
  controller-face connection (32). **One input with several outputs still shares a single 32**,
  because the input's outer node caps it. So the useful ratio is 1 input : 1 output per 32 channels.
- Each tunnel draws the default proxy idle power (1 AE/t, `me/helpers/AENetworkProxy.java:71`) on
  its inner node, and the outer proxy likely draws 1 AE/t on the main network
  **[in-game check]**. The grid also pays `channelsByBlocks / 128` AE/t for channel use
  (`PathGridCache`).

### Tunnel-network channel budget
- Every tunnel, **input and output**, uses 1 tunnel-network channel. A build with N faces in use
  needs **2N** tunnel-network channels.
- **Ad-hoc** tunnel network (no controller): at most **8** channel users in total, otherwise 0
  for everyone. UEL: `normalChannelCapacity`. If the grid contains any tunnel outer nodes, a
  compressed request counts as 9, so the network fails immediately (`PathGridCache.calculateRequiredChannels`).
- **⇒ Any real build needs a separate controller for the tunnel network**, sized for 2N channels,
  and that controller must not touch the main controller cluster.

---

## 4. Pathfinding algorithm (what the simulator must reproduce)

From `me/cache/PathGridCache.java` and `me/pathfinding/PathSegment.java`:

1. On any change, `repath()`. If there is a controller and it is valid → seed one segment per
   controller→non-controller connection. All controllers start out closed.
2. Every tick, every segment does one **BFS layer**. For each newly reached item (nodes *and*
   connections are both path items): set its route to the parent, and if it is `REQUIRE_CHANNEL`,
   try to allocate. Allocation walks the route to the controller, **checks** every item for room,
   then **commits** +1 along it.
3. An item is **closed when first reached**. Its route is fixed even if allocation failed, and
   the search keeps expanding through it. There is no rerouting.
4. **Greedy, first-come-first-served, order-dependent.** Order = segment order × the order of each
   node's connections (preferred first). The results are not a max-flow.

The simulator must copy this exactly. A max-flow model is only an *upper bound*.

---

## 5. Rules for a successful P2P controller build

Definitions: **C** = main controller cells. A **tunnel cell** is a non-controller cell holding
one or more input tunnels, each facing a main-controller face.

| # | Rule | Why (source) |
|---|---|---|
| R1 | C fits in the bounding box (7×7×7 by default). | §1 bbox |
| R2 | C is one face-connected cluster. | §1 conflict |
| R3 | No cell in C has full opposite pairs on 2 or more axes. | §1 cross rule |
| R4 | A face counts only if the neighbouring cell is not in C. **Its channels are only usable if that cell is a tunnel cell with a tunnel on that face.** | §1, §3 |
| R5 | **Every tunnel cell needs a normal (non-dense) centre cable.** | §2: parts are isolated without one; P2P can't sit on dense |
| R6 | **Every main-controller face adjacent to a tunnel-network cable cell must be capped by a part**, either a tunnel or a cable anchor. Otherwise the cable connects to the controller and **the two networks merge**. | §2 `updateConnections`, §1 colour |
| R7 | **Every tunnel cell needs at least one open side** that continues the tunnel-network cable toward the tunnel-network controller. Cells with all 6 sides facing main controllers are **unusable**: 6 parts leave no exit, and 5 parts leave the 6th side to merge into the main network (R6). This is your enclosed-block rule. | R5 + R6 |
| R8 | **Capacity:** every cable cell that touches a main-controller face must be normal cable (to hold caps), so its **node limit is 8**. All tunnels whose routes pass through a cell, its own tunnels included, must fit in 8. Dense (32) trunks are only allowed in cells that touch no main-controller face. | §2, §3 |
| R9 | No tunnel-network route passes through an ME tunnel's outer side. | §3 compressed |
| R10 | The tunnel network has its own controller with enough channels for 2N (inputs + outputs), and it doesn't touch C. | §3 budget |
| R11 | Both networks are powered. The main network powers controllers, outer nodes, and channel cost. The tunnel network powers the inner nodes. | §3 |

### What R7–R8 do to the optimization
The earlier plan treated the objective as "maximize exposed faces". R7–R8 change that:
- A gap deep inside a dense lattice is only worth something if a **chain of cable cells**
  can reach the outside, and each cell in the chain carries **at most 8 tunnels' channels**,
  including the tunnels in cells further in.
- So the real objective is **channels = 32 × (number of tunnels that can be routed to the
  tunnel network)**. That is a max-flow with node capacity 8 out of the lattice, solved
  **together with** where the controllers go.
- Expected effect: optimal shapes become "comb" or "fin" structures with open corridors running
  to the boundary, not fully packed 3D lattices. CP-SAT still handles this: add flow variables
  on the gap cells with capacity 8 per node.

---

## 5b. Wiring without anchors (coloured groups, dense trunks)

AE2 picks routes greedily (§4). So the optimizer uses wiring where **any** route stays
within capacity. That removes the need to force routes with anchors.

| # | Rule | Why it's safe |
|---|---|---|
| W1 | **Smart group:** a connected set of same-colour smart cables, with at most the normal capacity (8) of tunnels in total. | Every channel in a smart cable belongs to one of the group's own tunnels, so no smart cable can exceed 8. |
| W2 | A group touches dense cable **only at its own attachment** (one smart cell, one dense parent), or leaves the build directly. | Nothing can route *through* a group, so W1 holds. |
| W3 | **Dense trunk:** fluix dense smart cables in a tree with one exit, at most the dense capacity (32) in total, **never next to a controller**, and only touching other dense cable as parent/child. | Every dense cable carries at most its trunk's total; trunks never merge, and a dense cable never connects to a controller. |
| W4 | **Groups that touch get different dyes.** Fluix connects to every colour, so dense cable may only touch its own groups (W2). | Cables of different dyes don't connect (`AEColor.matches`, §2), so groups stay separate without anchors. Controllers are uncoloured, so W5 is still needed. |
| W5 | **Anchor-free** (default): a smart cable next to a controller face carries a tunnel on that face. | R6 without anchors. |

In-game status: confirmed (examples/ingame/RESULTS.md, run 3). Dyed groups stay apart (C1),
fluix merges them (C2), and the coloured anchor-free 3x3x3 reads exactly as predicted (T5).

## 6. AE2 Unofficial Extended Life differences

The mechanics are the same, but these are config-driven (`core/AEConfig.java`):

| Setting | Default | Effect |
|---|---|---|
| `normalChannelCapacity` | 8 (max 256) | Normal cable capacity, tunnel-cell limit (R8), ad-hoc limit |
| `denseChannelCapacity` | 32 (max 256) | Dense cable **and the per-face controller limit** |
| `maxControllerSizeX/Y/Z` | 7 (1–63) | Bounding box. At large sizes, exact solving of the full box gets expensive, so tiling or periodic patterns are needed. |

**The app should load these as parameters**, with rv6 defaults.

---

## 7. Open questions → in-game tests

1. **[in-game check]** R6 consequence: if the networks *do* merge, does the build fail
   outright, or does it just lose channels? (We treat it as forbidden either way.)
2. **[in-game check]** Power draw of a tunnel's outer node on the main network.
3. **[in-game check]** Whether chunk borders inside the controller block cause
   `checkController` to count unloaded neighbours as missing, which could make blocks invalid
   or valid on chunk load. Builds should avoid chunk borders or be tested across them.
4. **Out of scope for the controller layout, but it matters:** on the output side, if several
   outputs feed one interconnected network, the greedy BFS can overload one tunnel while others
   sit idle. Keep each output's downstream separate, or simulate it.
5. **Tunnel-network controller placement and sizing:** a second, smaller optimization problem.
   It might share the same solver.
