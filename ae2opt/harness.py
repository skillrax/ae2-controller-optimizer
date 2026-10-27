"""In-game test rigs: a generated build plus everything needed to test it.

For a build occupying box [0, size) with tunnel cells in the 1-block margin:

  * shell      -- every cell 2 blocks out from the box is dense fluix cable. It
                  touches the margin only where exit cells left a side open (all
                  other outer sides carry anchors), so it joins every exit to
                  the tunnel network.
  * tunnel net -- a snake-shaped controller (rows + end connectors, valid under
                  the cross rule, <= 7 wide) under the shell, touching it from
                  below, powered by a creative energy cell under the snake.
  * main power -- a creative energy cell on a main-controller face: a free face
                  if there is one, otherwise it replaces one outer tunnel cell.
                  Every cable next to it gets an anchor on that side so it can
                  only touch the controller (otherwise it would merge networks).
  * output rigs -- on top of the shell: a normal cable with an output tunnel on
                  the same frequency as a chosen input, feeding a dense trunk with
                  40 terminals. Exactly 32 should get channels (one face = one
                  tunnel = 32).

The rig is simulated with the same code as everything else; build_rig()
returns the numbers the in-game test should reproduce.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import Direction, Pos, step
from .layout import (
    CableAnchor, CableBus, CableType, Controller, DevicePart, EnergyAcceptor, Layout,
    P2PTunnel, TunnelPort,
)
from .profile import DEFAULT_PROFILE, Profile
from .rules import Report, check
from .sim import ONLINE

D = Direction


@dataclass
class OutputRig:
    freq: int
    input_at: tuple[Pos, Direction]
    output_at: tuple[Pos, Direction]
    devices: int
    devices_active: int = 0


@dataclass
class TestRig:
    name: str
    layout: Layout
    report: Report
    size: tuple[int, int, int]
    main_power: Pos
    tunnel_power: Pos
    tunnel_controllers: list[Pos]
    outputs: list[OutputRig]
    removed_tunnel: tuple[Pos, Direction] | None
    expected: dict = field(default_factory=dict)


def _layer(p: Pos, size) -> int:
    """Chebyshev distance from the box [0, size)."""
    return max(max(-c, c - (s - 1), 0) for c, s in zip(p, size))


def _devices_except(side: Direction) -> dict:
    return {d: DevicePart() for d in Direction if d is not side}


def _anchor_neighbours(lay: Layout, pos: Pos) -> None:
    """Cap every cable bus side that faces `pos`."""
    for d in Direction:
        o = step(pos, d)
        b = lay.get(o)
        if isinstance(b, CableBus) and d.opposite not in b.parts:
            parts = dict(b.parts)
            parts[d.opposite] = CableAnchor()
            lay.cable(o, b.cable, b.color, parts)


def _connected_cable_sides(lay: Layout, pos: Pos) -> list[Direction]:
    b = lay.get(pos)
    out = []
    for d in Direction:
        o = lay.get(step(pos, d))
        if d in b.parts:
            continue
        if isinstance(o, CableBus) and d.opposite not in o.parts:
            out.append(d)
    return out


def build_rig(name: str, build: Layout, size, profile: Profile = DEFAULT_PROFILE,
              output_rigs: int = 2) -> TestRig:
    size = tuple(size)
    lay = Layout()
    for pos, b in build.blocks.items():
        if isinstance(b, TunnelPort):
            continue
        if isinstance(b, Controller):
            if not all(0 <= c < s for c, s in zip(pos, size)):
                raise ValueError(f"controller {pos} is outside the box {size}")
            lay.controller(pos)  # power comes from a real cell below, not a flag
        else:
            if _layer(pos, size) > 1:
                raise ValueError(f"{pos} is more than one block outside the box; "
                                 "test rigs need builds made with exterior tunnel cells")
            lay.place(pos, b)

    # 1. Main-network power.
    removed = None
    main_power = None
    for c in lay.controllers():
        for d in Direction:
            n = step(c, d)
            if _layer(n, size) == 1 and lay.get(n) is None:
                main_power = n
                break
        if main_power:
            break
    if main_power is None:
        # Sacrifice one outer tunnel cell that routes nothing but itself.
        for pos in lay.positions():
            b = lay.get(pos)
            if not (isinstance(b, CableBus) and _layer(pos, size) == 1):
                continue
            tunnels = [(d, p) for d, p in b.parts.items() if isinstance(p, P2PTunnel)]
            if len(tunnels) == 1 and not _connected_cable_sides(lay, pos):
                removed = (pos, tunnels[0][0])
                main_power = pos
                lay.remove(pos)
                break
    if main_power is None:
        raise ValueError("no place for the main network's power cell")
    lay.energy(main_power)
    _anchor_neighbours(lay, main_power)

    # 2. Dense shell two blocks out.
    lo, hi = [-2, -2, -2], [s + 1 for s in size]
    for x in range(lo[0], hi[0] + 1):
        for y in range(lo[1], hi[1] + 1):
            for z in range(lo[2], hi[2] + 1):
                p = (x, y, z)
                if _layer(p, size) == 2:
                    lay.cable(p, CableType.DENSE)
    _anchor_neighbours(lay, main_power)  # the shell cell beside the cell too

    # 3. Tunnel-network controller: a snake under the shell, plus its power cell.
    w, depth = min(7, size[0] + 4), min(7, size[2] + 4)
    snake = []
    for row, dz in enumerate(range(0, depth, 2)):
        snake += [(lo[0] + dx, lo[1] - 1, lo[2] + dz) for dx in range(w)]
        if dz + 2 < depth:
            snake.append((lo[0] + (w - 1 if row % 2 == 0 else 0), lo[1] - 1, lo[2] + dz + 1))
    for p in snake:
        lay.controller(p)
    tunnel_power = (lo[0], lo[1] - 2, lo[2])
    lay.energy(tunnel_power)

    # 4. Output rigs on top of the shell, for inputs that face a controller.
    inputs = []
    for pos in lay.positions():
        b = lay.get(pos)
        if isinstance(b, CableBus):
            for d, p in b.parts.items():
                if isinstance(p, P2PTunnel) and not p.output and lay.is_controller(step(pos, d)):
                    inputs.append((0 if _layer(pos, size) == 0 else 1, pos, d, p.freq))
    inputs.sort(key=lambda t: (t[0], t[1], t[2].ordinal))
    # One interior tunnel (if any) and one exterior tunnel make the most useful pair.
    chosen = []
    for want in (0, 1):
        chosen += [t for t in inputs if t[0] == want][:1]
    for t in inputs:
        if len(chosen) >= output_rigs:
            break
        if t not in chosen:
            chosen.append(t)
    outputs = []
    top = hi[1] + 1
    for j, (_, pos, d, freq) in enumerate(chosen[:output_rigs]):
        x, z = lo[0] + 1 + 4 * j, 0
        if x > hi[0]:
            break
        lay.cable((x, top, z), CableType.NORMAL, parts={D.UP: P2PTunnel(freq, output=True)})
        for i in range(1, 5):
            lay.cable((x, top + i, z), CableType.DENSE)
            lay.cable((x, top + i, z - 1), CableType.NORMAL, parts=_devices_except(D.SOUTH))
            lay.cable((x, top + i, z + 1), CableType.NORMAL, parts=_devices_except(D.NORTH))
        outputs.append(OutputRig(freq, (pos, d), ((x, top, z), D.UP), devices=40))

    report = check(lay, profile)
    rig = TestRig(name, lay, report, size, main_power, tunnel_power, snake, outputs, removed)
    rig.expected = expectations(rig, profile)
    return rig


def expectations(rig: TestRig, profile: Profile) -> dict:
    """What the simulator predicts; the in-game test should match these."""
    sim = rig.report.sim
    g = sim.graph
    main_ctrl = [p for p in rig.layout.controllers() if all(0 <= c < s for c, s in zip(p, rig.size))]
    main_grid = sim.grid(g.controller_nodes[main_ctrl[0]]) if main_ctrl else None
    tunnel_grid = sim.grid(g.controller_nodes[rig.tunnel_controllers[0]])
    tunnels = sim.tunnels()
    inputs = [t for t in tunnels if not t.output]
    for o in rig.outputs:
        o.devices_active = 0
    device_nodes = [(n, ok) for n, ok in sim.devices()]
    for o in rig.outputs:
        x, _, z = o.output_at[0]
        o.devices_active = sum(1 for n, ok in device_nodes if ok and n.pos[0] == x and abs(n.pos[2] - z) <= 1)
    loads = [n.used for n in g.nodes if n.kind == "cable" and n.capacity == profile.normal_channel_capacity
             and n.pos in rig.layout.blocks and _layer(n.pos, rig.size) <= 1]
    return {
        "main_controllers": len(main_ctrl),
        "main_network": main_grid.state if main_grid else None,
        "tunnel_controllers": len(rig.tunnel_controllers),
        "tunnel_network": tunnel_grid.state,
        "same_network": main_grid is tunnel_grid,
        "input_tunnels": len(inputs),
        "input_tunnels_active": sum(1 for t in inputs if t.active),
        "output_tunnels_active": sum(1 for t in tunnels if t.output and t.active),
        "tunnel_network_channels": tunnel_grid.channels_in_use,
        "main_network_channels": main_grid.channels_in_use if main_grid else 0,
        "busiest_tunnel_cable": max(loads) if loads else 0,
        "rigs": [{"freq": o.freq, "devices": o.devices, "active": o.devices_active} for o in rig.outputs],
        "valid": rig.report.ok,
        "errors": [str(v) for v in rig.report.errors()],
    }


def standalone_rig(name: str, lay: Layout, profile: Profile = DEFAULT_PROFILE) -> dict:
    """Expectations for a small hand-made layout (negative tests)."""
    report = check(lay, profile)
    sim = report.sim
    tunnels = [t for t in sim.tunnels() if not t.output]
    grids = {sim.grid(n).id: sim.grid(n) for n in sim.graph.controller_nodes.values()}
    return {
        "networks": sorted(g.state for g in grids.values()),
        "input_tunnels": len(tunnels),
        "input_tunnels_active": sum(1 for t in tunnels if t.active),
        "errors": [str(v) for v in report.errors()],
        "report": report,
    }
