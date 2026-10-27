"""Channel simulator: a port of AE2's pathfinding (rv6 / UEL).

  * me/cache/PathGridCache.java     controller state, ad-hoc rules, segment seeding
  * me/pathfinding/PathSegment.java greedy layer-by-layer BFS and channel commits
  * me/pathfinding/ControllerValidator.java   bounding box + "found every controller"
  * parts/p2p/PartP2PTunnelME.java  tunnel links appear once tunnels are active

ME P2P links change the main grid, which changes pathing, which can change
which tunnels are active. The simulator therefore iterates build -> path ->
link until the link set stops changing, the same settling the game does over
a few ticks.

Deliberate simplifications (documented in MECHANICS.md):
  * Power is boolean: a grid is powered if it has an energy acceptor or a
    controller marked ``powered``. Power amounts are not simulated.
  * Multiblock devices (crafting CPUs, spatial pylons) are not modelled.
  * In game, connection order depends on placement and chunk-load order. Here it
    is fixed: positions in sorted order, sides in EnumFacing order. When
    capacity is contested the in-game winner may differ; totals that are not
    contested are exact.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .geometry import Direction, Pos
from .graph import (
    CANNOT_CARRY_COMPRESSED, COMPRESSED_CHANNEL, REQUIRE_CHANNEL, Graph, Node,
    TunnelKey, build_graph,
)
from .layout import Layout
from .profile import DEFAULT_PROFILE, Profile

ONLINE = "ONLINE"
CONFLICT = "CONFLICT"
AD_HOC = "AD_HOC"
AD_HOC_OVERLOADED = "AD_HOC_OVERLOADED"
CHANNELS_DISABLED = "CHANNELS_DISABLED"

MAX_ITERATIONS = 32


@dataclass
class GridResult:
    id: int
    nodes: list[Node]
    state: str = ""
    powered: bool = False
    controllers: list[Pos] = field(default_factory=list)
    channels_in_use: int = 0
    adhoc_required: int = 0
    conflict_reason: str = ""

    def has_kind(self, kind: str) -> bool:
        return any(n.kind == kind for n in self.nodes)


@dataclass
class TunnelStatus:
    key: TunnelKey
    freq: int
    output: bool
    inner_grid: int
    outer_grid: int
    has_channel: bool
    powered: bool
    linked: bool
    faces_controller: Pos | None  # main controller its outer side connects to

    @property
    def active(self) -> bool:
        return self.has_channel and self.powered


@dataclass
class SimResult:
    profile: Profile
    graph: Graph
    grids: list[GridResult]
    grid_of: dict[int, GridResult]   # node id -> grid
    links: frozenset
    iterations: int
    converged: bool

    def grid(self, node: Node) -> GridResult:
        return self.grid_of[node.id]

    def satisfied(self, node: Node) -> bool:
        if not node.has(REQUIRE_CHANNEL) or not self.profile.channels_enabled:
            return True
        return node.used > 0

    def tunnels(self) -> list[TunnelStatus]:
        out = []
        g = self.graph
        linked = {k for pair in self.links for k in pair}
        for key in sorted(g.tunnels, key=lambda k: (k[0], k[1].ordinal)):
            t = g.tunnels[key]
            inner, outer = g.part_nodes[key], g.outer_nodes[key]
            faces = None
            for c in outer.conns:
                o = c.other(outer)
                if o.kind == "controller":
                    faces = o.pos
            out.append(TunnelStatus(
                key, t.freq, t.output, self.grid(inner).id, self.grid(outer).id,
                self.satisfied(inner), self.grid(inner).powered, key in linked, faces))
        return out

    def devices(self) -> list[tuple[Node, bool]]:
        return [(n, self.satisfied(n) and self.grid(n).powered)
                for n in self.graph.nodes if n.kind in ("device", "device_block")]

    def face_usage(self) -> dict[tuple[Pos, Direction], int]:
        """Channels leaving each controller face that connects to a non-controller."""
        out = {}
        for pos, cn in self.graph.controller_nodes.items():
            for c in cn.conns:
                o = c.other(cn)
                d = _dir_from(pos, o.pos)
                if o.kind != "controller" and d is not None:
                    out[(pos, d)] = c.used
        return out


def _dir_from(a: Pos, b: Pos) -> Direction | None:
    delta = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    for d in Direction:
        if d.value == delta:
            return d
    return None


# --------------------------------------------------------------------------- grids

def _components(g: Graph) -> list[GridResult]:
    seen: set[int] = set()
    grids = []
    for n in g.nodes:
        if n.id in seen:
            continue
        members = []
        q = deque([n])
        seen.add(n.id)
        while q:
            x = q.popleft()
            members.append(x)
            for y in x.neighbors():
                if y.id not in seen:
                    seen.add(y.id)
                    q.append(y)
        members.sort(key=lambda m: m.id)
        grids.append(GridResult(len(grids), members))
    return grids


def _validate_controllers(grid: GridResult, profile: Profile) -> tuple[bool, str]:
    """ControllerValidator + the found == controllers.size() check."""
    ctrls = [n for n in grid.nodes if n.kind == "controller"]
    start = min(ctrls, key=lambda n: n.pos)
    found = {start.id: start}
    q = deque([start])
    while q:  # visitNode returns false for non-controllers: walk controllers only
        x = q.popleft()
        for y in x.neighbors():
            if y.kind == "controller" and y.id not in found:
                found[y.id] = y
                q.append(y)
    xs, ys, zs = zip(*(n.pos for n in found.values()))
    spans = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    if any(s >= limit for s, limit in zip(spans, profile.max_controller_size)):
        return False, (f"controller structure spans {spans[0]+1}x{spans[1]+1}x{spans[2]+1}, "
                       f"limit is {'x'.join(map(str, profile.max_controller_size))}")
    if len(found) != len(ctrls):
        return False, (f"{len(ctrls) - len(found)} controller block(s) on this network are not "
                       f"face-connected to the rest (more than one controller structure)")
    return True, ""


def _set_all(grid: GridResult, value: int) -> None:
    """AdHocChannelUpdater: every node and connection gets the same count."""
    for n in grid.nodes:
        n.used = value
        for c in n.conns:
            c.used = value


def _path_grid(grid: GridResult, profile: Profile) -> None:
    nodes = grid.nodes
    grid.powered = any(n.powered for n in nodes)
    grid.controllers = sorted(n.pos for n in nodes if n.kind == "controller")

    if not profile.channels_enabled:
        grid.state = CHANNELS_DISABLED
        return

    ports = [n for n in nodes if n.kind == "port"]
    if ports and grid.controllers:
        # A port stands for a separate controller structure: sharing a grid with
        # another structure is a controller conflict.
        grid.state = CONFLICT
        grid.conflict_reason = "tunnel-network port shares a network with a controller structure"
        _set_all(grid, 0)
        return

    if not grid.controllers and not ports:
        # PathGridCache.calculateRequiredChannels
        block_dense = any(n.has(CANNOT_CARRY_COMPRESSED) for n in nodes)
        required = 0
        for n in nodes:
            if n.has(REQUIRE_CHANNEL):
                if n.has(COMPRESSED_CHANNEL) and block_dense:
                    required = 9
                    break
                required += 1
        grid.adhoc_required = required
        if required > profile.normal_channel_capacity:
            grid.state = AD_HOC_OVERLOADED
            _set_all(grid, 0)
        else:
            grid.state = AD_HOC
            _set_all(grid, required)
            grid.channels_in_use = required
        return

    ok, reason = _validate_controllers(grid, profile) if grid.controllers else (True, "")
    if not ok:
        grid.state = CONFLICT
        grid.conflict_reason = reason
        _set_all(grid, 0)
        return

    grid.state = ONLINE
    closed: set[int] = set()            # ids of path items (nodes and conns share via id())
    segments: list[list] = []
    roots = ("controller", "port")
    for cn in sorted((n for n in nodes if n.kind in roots), key=lambda n: n.pos):
        closed.add(id(cn))
        for c in list(cn.conns):
            if c.other(cn).kind not in roots:
                closed.add(id(c))
                c.set_route(cn)
                segments.append([c])

    def allocate(start: Node, dense: bool) -> bool:
        pi = start
        while pi is not None:
            if not pi.can_support() or (dense and pi.has(CANNOT_CARRY_COMPRESSED)):
                start.blocked_by.append(pi)
                return False
            pi = pi.route()
        pi = start
        while pi is not None:
            pi.used += 1
            pi = pi.route()
        grid.channels_in_use += 1
        return True

    # PathGridCache.onUpdateTick: each tick, every live segment advances one BFS layer.
    while segments:
        alive = []
        for open_list in segments:
            new_open = []
            for item in open_list:
                for pi in item.options():
                    if id(pi) in closed:
                        continue
                    pi.set_route(item)
                    if pi.has(REQUIRE_CHANNEL):
                        allocate(pi, pi.has(COMPRESSED_CHANNEL))
                    closed.add(id(pi))
                    new_open.append(pi)
            if new_open:
                open_list[:] = new_open
                alive.append(open_list)
        segments = alive


# --------------------------------------------------------------------------- links

def _compute_links(g: Graph, grid_of: dict[int, GridResult], profile: Profile) -> frozenset:
    def active(key) -> bool:
        inner = g.part_nodes[key]
        grid = grid_of[inner.id]
        has_channel = (not profile.channels_enabled) or inner.used > 0
        return has_channel and grid.powered

    by_grid: dict[int, dict[int, dict[str, list]]] = {}
    for key in sorted(g.tunnels, key=lambda k: (k[0], k[1].ordinal)):
        t = g.tunnels[key]
        gid = grid_of[g.part_nodes[key].id].id
        slot = by_grid.setdefault(gid, {}).setdefault(t.freq, {"in": [], "out": []})
        slot["out" if t.output else "in"].append(key)

    links = set()
    for freqs in by_grid.values():
        for slot in freqs.values():
            if not slot["in"]:
                continue
            inp = slot["in"][0]  # P2PCache keeps one input per frequency
            if not active(inp):
                continue
            for out in slot["out"]:
                if active(out):
                    links.add((inp, out))
    return frozenset(links)


def simulate(layout: Layout, profile: Profile = DEFAULT_PROFILE) -> SimResult:
    links: frozenset = frozenset()
    seen = []
    for it in range(1, MAX_ITERATIONS + 1):
        g = build_graph(layout, profile, links)
        grids = _components(g)
        grid_of = {n.id: gr for gr in grids for n in gr.nodes}
        for gr in grids:
            _path_grid(gr, profile)
        new_links = _compute_links(g, grid_of, profile)
        if new_links == links:
            return SimResult(profile, g, grids, grid_of, links, it, True)
        if new_links in seen:  # oscillating; report the current state
            return SimResult(profile, g, grids, grid_of, links, it, False)
        seen.append(links)
        links = new_links
    return SimResult(profile, g, grids, grid_of, links, MAX_ITERATIONS, False)
