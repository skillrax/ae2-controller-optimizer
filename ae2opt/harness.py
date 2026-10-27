"""In-game test rigs: a generated build plus everything needed to test it.

For a build occupying box [0, size) with tunnel cells in the 1-block margin:

  * collector  -- separate trees of dense smart cable in the layers 2-3 blocks out
                  from the box, one per exit group, each ending in one root cable
                  that sits on a tunnel-network controller. Cells that touch but
                  are not parent/child get anchors, so routes are fixed, and the
                  build stays visible. WAILA on the roots reads each tree's total.
  * tunnel net -- a snake-shaped controller (rows + end connectors, valid under
                  the cross rule, <= 7 wide) under the collector roots, powered by a creative energy cell under the snake.
  * main power -- a creative energy cell on a main-controller face: a free face
                  if there is one, otherwise it replaces one outer tunnel cell.
                  Every cable next to it gets an anchor on that side so it can
                  only touch the controller (otherwise it would merge networks).
  * output rigs -- on top: a normal cable with an output tunnel on
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
from .graph import color_matches
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
    margin: int = 1
    collector_roots: list[Pos] = field(default_factory=list)
    exits: list[Pos] = field(default_factory=list)
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
        if isinstance(o, CableBus) and d.opposite not in o.parts and color_matches(b.color, o.color):
            out.append(d)
    return out


def _tree_load(lay: Layout, exit_pos: Pos, size, margin: int = 1) -> int:
    """Input tunnels in the cable component (inside the build) that leaves via this exit."""
    seen, todo, tunnels = set(), [exit_pos], 0
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        b = lay.get(n)
        tunnels += sum(1 for p in b.parts.values() if isinstance(p, P2PTunnel) and not p.output)
        for d in _connected_cable_sides(lay, n):
            o = step(n, d)
            if _layer(o, size) <= margin:
                todo.append(o)
    return tunnels


@dataclass
class _Forest:
    parent: dict = field(default_factory=dict)   # cell -> next cell toward its root (None at a root)
    load: dict = field(default_factory=dict)      # cell -> channels passing through it


def _collector(attach, contacts: set, size, cap: int, blocked: set, layers=(2, 3)) -> _Forest:
    """Connect every attach cell to a snake contact with dense-cable trees in the
    layers 2 and 3 blocks out from the box (minus `blocked` cells), keeping
    each cell's load within `cap`.
    Heaviest attach points go first; each takes the shortest path that either
    starts a new tree at a free contact or joins an existing tree with room."""
    from collections import deque
    f = _Forest()
    reserved = {p for p, _, _ in attach}

    def root_path(n):
        while n is not None:
            yield n
            n = f.parent[n]

    for start, load, what in sorted(attach, key=lambda a: (-a[1], a[0])):
        if start in f.parent:
            raise ValueError(f"{what}: collector cell {start} is already used")
        reserved.discard(start)
        prev = {start: None}
        q = deque([start])
        found = None
        while q and found is None:
            n = q.popleft()
            for d in Direction:
                o = step(n, d)
                if o in prev or _layer(o, size) not in layers or o in reserved or o in blocked:
                    continue
                if o in f.parent:
                    if all(f.load[c] + load <= cap for c in root_path(o)):
                        prev[o] = n
                        found = o
                        break
                    continue                          # full tree: do not route through it
                prev[o] = n
                if o in contacts:
                    found = o
                    break
                q.append(o)
        if start in contacts and start not in f.parent:
            found, prev = start, {start: None}
        if found is None:
            raise ValueError(f"{what}: no room to connect it to the tunnel network")
        # Walk back from the junction / contact to the start, linking cells.
        joined = found in f.parent
        path = []
        n = found
        while n is not None:
            path.append(n)
            n = prev[n]
        # path = [found, ..., start]
        if not joined:
            f.parent[found], f.load[found] = None, 0
        for i in range(1, len(path)):
            f.parent[path[i]] = path[i - 1]
            f.load[path[i]] = 0
        for c in root_path(start):
            f.load[c] += load
    return f


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
            lay.place(pos, b)
    # The build's cable space reaches `m` blocks out; the rig starts beyond it.
    m = max([_layer(p, size) for p, b in lay.blocks.items() if not isinstance(b, Controller)] + [1])

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
        # Sacrifice one outer tunnel cell at the end of its group: one tunnel, at
        # most one connected cable, and no side open to the outside (not an exit).
        for pos in lay.positions():
            b = lay.get(pos)
            if not (isinstance(b, CableBus) and _layer(pos, size) == 1):
                continue
            tunnels = [(d, p) for d, p in b.parts.items() if isinstance(p, P2PTunnel)]
            exit_side = any(_layer(step(pos, d), size) == m + 1 and d not in b.parts for d in Direction)
            if len(tunnels) == 1 and len(_connected_cable_sides(lay, pos)) <= 1 and not exit_side:
                removed = (pos, tunnels[0][0])
                main_power = pos
                lay.remove(pos)
                break
    if main_power is None:
        raise ValueError("no place for the main network's power cell")
    lay.energy(main_power)
    _anchor_neighbours(lay, main_power)

    lo, hi = [-(m + 1)] * 3, [s + m for s in size]

    # 2. Tunnel-network controller: a snake under the collector, plus its power cell.
    w, depth = min(7, size[0] + 2 * m + 2), min(7, size[2] + 2 * m + 2)
    snake = []
    for row, dz in enumerate(range(0, depth, 2)):
        snake += [(lo[0] + dx, lo[1] - 1, lo[2] + dz) for dx in range(w)]
        if dz + 2 < depth:
            snake.append((lo[0] + (w - 1 if row % 2 == 0 else 0), lo[1] - 1, lo[2] + dz + 1))
    for p in snake:
        lay.controller(p)
    tunnel_power = (lo[0], lo[1] - 2, lo[2])
    lay.energy(tunnel_power)
    contacts = {(x, y + 1, z) for x, y, z in snake}  # collector cells sitting on a snake block

    # 3. Output rigs on top (placed below, after the exits), for inputs that face a controller.
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
    # 3b. Exits: margin cable cells with a side open to the outside. Each keeps one
    #    open side (down if possible); the rest get anchors.
    exits: dict[Pos, Pos] = {}
    for pos in lay.positions():
        b = lay.get(pos)
        if not (isinstance(b, CableBus) and _layer(pos, size) == m):
            continue
        open_out = [d for d in Direction if _layer(step(pos, d), size) == m + 1 and d not in b.parts]
        if not open_out:
            continue
        keep = D.DOWN if D.DOWN in open_out else open_out[0]
        parts = dict(b.parts)
        for d in open_out:
            if d is not keep:
                parts[d] = CableAnchor()
        lay.cable(pos, b.cable, b.color, parts)
        exits[pos] = step(pos, keep)

    outputs = []
    attach: list[tuple[Pos, int, str]] = []   # (collector cell, channels, what)
    for e, p in exits.items():
        attach.append((p, _tree_load(lay, e, size, m), f"exit {e}"))
    # Rig spots on the top face: not an exit's collector cell, and 3+ blocks apart.
    top = hi[1] + 1
    taken = set(exits.values())
    spots = []
    for x in range(lo[0] + 1, hi[0]):
        for z in range(lo[2] + 1, hi[2]):
            if (x, hi[1], z) in taken or any(abs(x - a) < 3 and abs(z - c) < 3 for a, c in spots):
                continue
            spots.append((x, z))
    for j, (_, pos, d, freq) in enumerate(chosen[:output_rigs]):
        if j >= len(spots):
            break
        x, z = spots[j]
        lay.cable((x, top, z), CableType.NORMAL, parts={D.UP: P2PTunnel(freq, output=True)})
        for i in range(1, 5):
            lay.cable((x, top + i, z), CableType.DENSE)
            lay.cable((x, top + i, z - 1), CableType.NORMAL, parts=_devices_except(D.SOUTH))
            lay.cable((x, top + i, z + 1), CableType.NORMAL, parts=_devices_except(D.NORTH))
        outputs.append(OutputRig(freq, (pos, d), ((x, top, z), D.UP), devices=40))
        attach.append(((x, hi[1], z), 1, f"output rig {j + 1}"))

    # 5. Collector: one dense-cable tree per snake contact, built greedily, each
    #    carrying at most the dense capacity. Cells that touch but are not
    #    parent/child get anchors, so every route is fixed and readable.
    # Collector cells must not touch the snake (except contacts, from above), the
    # rigs, or the power cells.
    blocked = set()
    for pos, b in lay.blocks.items():
        if _layer(pos, size) >= m + 1:
            blocked.add(pos)
            blocked.update(step(pos, d) for d in Direction)
    blocked -= contacts
    blocked -= {p for p, _, _ in attach}
    tree = _collector(attach, contacts, size, profile.dense_channel_capacity, blocked, layers=(m + 1, m + 2))
    cells = set(tree.parent)
    for n in cells:
        lay.cable(n, CableType.DENSE)
    for n in sorted(cells):
        parts = {}
        for d in Direction:
            o = step(n, d)
            linked = tree.parent.get(n) == o or tree.parent.get(o) == n
            if o in cells:
                if not linked and n < o:
                    parts[d] = CableAnchor()
            elif _layer(o, size) <= m and lay.get(o) is not None:
                if exits.get(o) != n:
                    parts[d] = CableAnchor()          # a margin cell that is not this cell's exit
            elif lay.is_controller(o):
                if not (n in contacts and tree.parent.get(n) is None and d is D.DOWN):
                    parts[d] = CableAnchor()
        if parts:
            lay.cable(n, CableType.DENSE, parts=parts)
    _anchor_neighbours(lay, main_power)
    collector = {"roots": sorted(r for r in cells if tree.parent.get(r) is None), "cells": len(cells)}

    report = check(lay, profile)
    rig = TestRig(name, lay, report, size, main_power, tunnel_power, snake, outputs, removed, margin=m,
                  collector_roots=collector["roots"], exits=sorted(exits))
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
             and n.pos in rig.layout.blocks and _layer(n.pos, rig.size) <= rig.margin]

    # What WAILA / The One Probe shows on a smart cable: the busiest of its
    # connections (PartCable.writeToNBT "usedChannels").
    xs, ys, zs = zip(*rig.layout.blocks)
    origin = (min(xs), min(ys), min(zs))

    def reading(pos):
        node = g.centers[pos]
        return max((c.used for c in node.conns), default=0)

    def rel(pos):
        return tuple(c - o for c, o in zip(pos, origin))

    probe_roots = [{"pos": r, "rel": rel(r), "channels": reading(r)} for r in rig.collector_roots]
    probe_exits = [{"pos": e, "rel": rel(e), "channels": reading(e)} for e in rig.exits]
    rig_probe = []
    for o in rig.outputs:
        c = o.output_at[0]
        trunk = (c[0], c[1] + 1, c[2])
        rig_probe.append({"output_cable": rel(c), "output_cable_channels": reading(c),
                          "trunk": rel(trunk), "trunk_channels": reading(trunk)})
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
        "rigs": [{"freq": o.freq, "devices": o.devices, "active": o.devices_active, **p}
                 for o, p in zip(rig.outputs, rig_probe)],
        "probe_roots": probe_roots,
        "probe_roots_total": sum(r["channels"] for r in probe_roots),
        "probe_exits": probe_exits,
        "origin": origin,
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
