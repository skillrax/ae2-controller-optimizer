"""Turn a Layout into AE2's grid-node graph.

Mirrors the rv6 / UEL classes (paths relative to src/main/java/appeng/):
  * me/GridNode.java        node capacity from flags, connection ordering
  * me/GridConnection.java  per-connection capacity, route direction
  * parts/CableBusContainer.java  which node faces which side
  * tile/networking/TileController.java  per-block validity (cross rule)

Nodes and connections are both "path items" (IPathItem in AE2): pathfinding
walks through both and both have a channel capacity.
"""

from __future__ import annotations

from .geometry import Direction, Pos, step
from .layout import (
    CableAnchor, CableBus, CableType, Controller, DeviceBlock, DevicePart,
    EnergyAcceptor, Layout, P2PTunnel, TunnelPort,
)
from .profile import Profile

# GridFlags
REQUIRE_CHANNEL = "REQUIRE_CHANNEL"
COMPRESSED_CHANNEL = "COMPRESSED_CHANNEL"
CANNOT_CARRY = "CANNOT_CARRY"
DENSE_CAPACITY = "DENSE_CAPACITY"
CANNOT_CARRY_COMPRESSED = "CANNOT_CARRY_COMPRESSED"
PREFERRED = "PREFERRED"

ALL_SIDES = frozenset(Direction)
NO_SIDES = frozenset()

# AEBasePart / CableBusContainer.addToWorld install the centre first, then the
# sides from ordinal 5 down to 0. Internal connections are created in that order.
_PART_INSTALL_ORDER = sorted(Direction, key=lambda d: -d.ordinal)


class Node:
    """A GridNode."""

    def __init__(self, nid: int, kind: str, pos: Pos, side: Direction | None,
                 flags: frozenset[str], color: str, valid_sides: frozenset[Direction],
                 capacity: int, powered: bool = False):
        self.id = nid
        self.kind = kind          # controller | cable | p2p_inner | p2p_outer | device | device_block | energy
        self.pos = pos
        self.side = side          # for parts: the bus side the part sits on
        self.flags = flags
        self.color = color
        self.valid_sides = valid_sides
        self.capacity = capacity
        self.powered = powered    # a power source for its grid
        self.conns: list[Conn] = []
        # pathfinding state
        self.used = 0
        self.blocked_by: list = []   # path items that refused this node a channel

    # --- IPathItem
    def options(self):
        return list(self.conns)

    def set_route(self, parent: "Conn") -> None:
        self.used = 0
        if parent in self.conns:
            i = self.conns.index(parent)
            if i > 0:
                self.conns.insert(0, self.conns.pop(i))

    def route(self):
        if not self.conns or CANNOT_CARRY in self.flags:
            return None
        return self.conns[0]

    def can_support(self) -> bool:
        return self.used < self.capacity

    def has(self, flag: str) -> bool:
        return flag in self.flags

    def neighbors(self):
        for c in self.conns:
            yield c.other(self)

    def label(self) -> str:
        where = f"{self.pos}" + (f" {self.side.name}" if self.side else "")
        return f"{self.kind}@{where}"

    def __repr__(self) -> str:
        return f"<Node {self.label()} used={self.used}/{self.capacity}>"


class Conn:
    """A GridConnection. ``direction`` is None for in-block and tunnel links."""

    flags = frozenset()

    def __init__(self, a: Node, b: Node, direction: Direction | None, capacity: int, kind: str):
        self.a = a
        self.b = b
        self.direction = direction
        self.capacity = capacity
        self.kind = kind  # internal | adjacent | tunnel
        self.used = 0

    def other(self, n: Node) -> Node:
        return self.b if n is self.a else self.a

    # --- IPathItem
    def options(self):
        return [self.a, self.b]

    def set_route(self, parent: Node) -> None:
        self.used = 0
        if self.b is parent:
            self.a, self.b = self.b, self.a

    def route(self):
        if CANNOT_CARRY in self.a.flags:
            return None
        return self.a

    def can_support(self) -> bool:
        return self.used < self.capacity

    def has(self, flag: str) -> bool:
        return False

    def label(self) -> str:
        return f"link {self.a.label()} <-> {self.b.label()}"

    def __repr__(self) -> str:
        return f"<Conn {self.label()} used={self.used}/{self.capacity}>"


def color_matches(a: str, b: str) -> bool:
    return a == b or a == "transparent" or b == "transparent"


def controller_valid(layout: Layout, pos: Pos) -> bool:
    """TileController.onNeighborChange: invalid with full opposite pairs on 2+ axes."""
    pairs = 0
    for d in (Direction.EAST, Direction.UP, Direction.SOUTH):
        if layout.is_controller(step(pos, d)) and layout.is_controller(step(pos, d.opposite)):
            pairs += 1
    return pairs <= 1


TunnelKey = tuple[Pos, Direction]


class Graph:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.nodes: list[Node] = []
        self.conns: list[Conn] = []
        self.controller_nodes: dict[Pos, Node] = {}
        self.controller_validity: dict[Pos, bool] = {}
        self.centers: dict[Pos, Node] = {}
        self.part_nodes: dict[TunnelKey, Node] = {}     # p2p inner or device part
        self.outer_nodes: dict[TunnelKey, Node] = {}    # p2p outer
        self.tunnels: dict[TunnelKey, P2PTunnel] = {}

    def node_capacity(self, flags: frozenset[str]) -> int:
        if CANNOT_CARRY in flags:
            return 0
        if DENSE_CAPACITY in flags:
            return self.profile.dense_channel_capacity
        return self.profile.normal_channel_capacity

    def add_node(self, kind, pos, side, flags, color="transparent", valid_sides=NO_SIDES, powered=False) -> Node:
        flags = frozenset(flags)
        n = Node(len(self.nodes), kind, pos, side, flags, color, frozenset(valid_sides),
                 self.node_capacity(flags), powered)
        self.nodes.append(n)
        return n

    def connect(self, a: Node, b: Node, direction: Direction | None, kind: str) -> Conn:
        c = Conn(a, b, direction, self.profile.dense_channel_capacity, kind)
        self.conns.append(c)
        a.conns.append(c)
        b.conns.append(c)
        return c

    def finish(self) -> None:
        # GridNode.addConnection re-sorts with a stable "preferred first" comparator
        # after every add; a single stable sort of the insertion order is equivalent.
        for n in self.nodes:
            n.conns.sort(key=lambda c, n=n: 0 if PREFERRED in c.other(n).flags else 1)

    def side_node(self, layout: Layout, pos: Pos, d: Direction) -> Node | None:
        """The node a block exposes toward side d (IGridHost.getGridNode(side))."""
        b = layout.get(pos)
        if isinstance(b, Controller):
            return self.controller_nodes[pos]
        if isinstance(b, (DeviceBlock, EnergyAcceptor, TunnelPort)):
            return self.part_nodes.get((pos, None))
        if isinstance(b, CableBus):
            if d in b.parts and (pos, d) in self.outer_nodes:
                return self.outer_nodes[(pos, d)]
            return self.centers.get(pos)
        return None


def build_graph(layout: Layout, profile: Profile, links: frozenset[tuple[TunnelKey, TunnelKey]] = frozenset()) -> Graph:
    g = Graph(profile)

    for pos in layout.positions():
        b = layout.blocks[pos]
        if isinstance(b, Controller):
            valid = controller_valid(layout, pos)
            g.controller_validity[pos] = valid
            g.controller_nodes[pos] = g.add_node(
                "controller", pos, None, {CANNOT_CARRY, DENSE_CAPACITY},
                valid_sides=ALL_SIDES if valid else NO_SIDES, powered=b.powered)
        elif isinstance(b, DeviceBlock):
            g.part_nodes[(pos, None)] = g.add_node("device_block", pos, None, {REQUIRE_CHANNEL}, valid_sides=ALL_SIDES)
        elif isinstance(b, EnergyAcceptor):
            g.part_nodes[(pos, None)] = g.add_node("energy", pos, None, set(), valid_sides=ALL_SIDES, powered=True)
        elif isinstance(b, TunnelPort):
            g.part_nodes[(pos, None)] = g.add_node("port", pos, None, {CANNOT_CARRY, DENSE_CAPACITY},
                                                   valid_sides=ALL_SIDES, powered=True)
        elif isinstance(b, CableBus):
            center = None
            if b.cable is not CableType.NONE:
                flags = {PREFERRED} | ({DENSE_CAPACITY} if b.cable is CableType.DENSE else set())
                # CableBusContainer.updateConnections: no connection out of a side holding a part.
                g.centers[pos] = center = g.add_node(
                    "cable", pos, None, flags, b.color, ALL_SIDES - set(b.parts))
            for d in _PART_INSTALL_ORDER:
                part = b.parts.get(d)
                if isinstance(part, P2PTunnel):
                    inner = g.add_node("p2p_inner", pos, d, {REQUIRE_CHANNEL, COMPRESSED_CHANNEL})
                    outer = g.add_node("p2p_outer", pos, d, {DENSE_CAPACITY, CANNOT_CARRY_COMPRESSED},
                                       valid_sides={d})
                    g.part_nodes[(pos, d)] = inner
                    g.outer_nodes[(pos, d)] = outer
                    g.tunnels[(pos, d)] = part
                    if center:
                        g.connect(center, inner, None, "internal")
                elif isinstance(part, DevicePart):
                    node = g.add_node("device", pos, d, {REQUIRE_CHANNEL})
                    g.part_nodes[(pos, d)] = node
                    if center:
                        g.connect(center, node, None, "internal")
                elif isinstance(part, CableAnchor):
                    pass  # no grid node; only blocks the side

    # Adjacent-block connections (GridNode.findConnections): both sides must
    # accept the direction and colours must match.
    for pos in layout.positions():
        for d in Direction:
            other = step(pos, d)
            if other <= pos or other not in layout.blocks:
                continue
            a = g.side_node(layout, pos, d)
            b = g.side_node(layout, other, d.opposite)
            if a is None or b is None:
                continue
            if d in a.valid_sides and d.opposite in b.valid_sides and color_matches(a.color, b.color):
                g.connect(a, b, d, "adjacent")

    # ME P2P links: input outer <-> output outer (PartP2PTunnelME.updateConnections).
    for inp, out in sorted(links, key=lambda l: (l[0][0], l[0][1].ordinal, l[1][0], l[1][1].ordinal)):
        g.connect(g.outer_nodes[inp], g.outer_nodes[out], None, "tunnel")

    g.finish()
    return g
