"""Build-validity rules R1-R11 (MECHANICS.md section 5) plus frequency checks.

Some rules are structural and checked directly on the layout (R1, R3, R4, R5,
R7, the static half of R6). The rest depend on how channels actually route
and are read from a simulation (R2, R6, R8, R9, R10, R11).
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from .geometry import Direction, Pos, step
from .graph import CANNOT_CARRY_COMPRESSED, REQUIRE_CHANNEL, Node
from .layout import CableBus, Layout, P2PTunnel
from .profile import DEFAULT_PROFILE, Profile
from .sim import AD_HOC, AD_HOC_OVERLOADED, CONFLICT, ONLINE, SimResult, simulate

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass
class Violation:
    rule: str
    severity: str
    message: str
    positions: tuple[Pos, ...] = ()

    def __str__(self) -> str:
        where = f"  at {', '.join(map(str, self.positions[:6]))}" + ("..." if len(self.positions) > 6 else "") \
            if self.positions else ""
        return f"[{self.rule} {self.severity}] {self.message}{where}"


@dataclass
class Report:
    violations: list[Violation]
    metrics: dict
    sim: SimResult
    clusters: list[list[Pos]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(v.severity == ERROR for v in self.violations)

    def by_rule(self, rule: str) -> list[Violation]:
        return [v for v in self.violations if v.rule == rule]

    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == ERROR]

    def format(self) -> str:
        lines = [f"{'VALID' if self.ok else 'INVALID'} build"]
        for k, v in self.metrics.items():
            lines.append(f"  {k}: {v}")
        order = {ERROR: 0, WARNING: 1, INFO: 2}

        def rule_key(rule: str):
            m = re.fullmatch(r"([A-Z]+)(\d*)", rule)
            return (m.group(1), int(m.group(2) or 0)) if m else (rule, 0)

        for v in sorted(self.violations, key=lambda v: (order[v.severity], rule_key(v.rule))):
            lines.append(str(v))
        return "\n".join(lines)


def _controller_clusters(sim: SimResult) -> list[list[Pos]]:
    """Controllers joined by working controller-controller connections."""
    g = sim.graph
    seen: set[Pos] = set()
    clusters = []
    for pos in sorted(g.controller_nodes):
        if pos in seen:
            continue
        cl = []
        q = deque([g.controller_nodes[pos]])
        seen.add(pos)
        while q:
            n = q.popleft()
            cl.append(n.pos)
            for o in n.neighbors():
                if o.kind == "controller" and o.pos not in seen:
                    seen.add(o.pos)
                    q.append(o)
        clusters.append(sorted(cl))
    return clusters


def check(layout: Layout, profile: Profile = DEFAULT_PROFILE, sim: SimResult | None = None) -> Report:
    sim = sim or simulate(layout, profile)
    g = sim.graph
    v: list[Violation] = []

    clusters = _controller_clusters(sim)
    cluster_of = {p: i for i, cl in enumerate(clusters) for p in cl}

    # R3 cross rule
    bad = [p for p, ok in sorted(g.controller_validity.items()) if not ok]
    if bad:
        v.append(Violation("R3", ERROR, f"{len(bad)} controller block(s) have controllers on both sides along "
                           "2+ axes; they disable all their sides and act as dead blocks", tuple(bad)))

    # R1 bounding box, per structure
    limit = profile.max_controller_size
    for cl in clusters:
        if len(cl) == 1 and not g.controller_validity[cl[0]]:
            continue
        xs, ys, zs = zip(*cl)
        spans = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, max(zs) - min(zs) + 1)
        if any(s > m for s, m in zip(spans, limit)):
            v.append(Violation("R1", ERROR, f"controller structure is {spans[0]}x{spans[1]}x{spans[2]}, "
                               f"limit is {limit[0]}x{limit[1]}x{limit[2]}", tuple(cl)))

    # R2 one structure per network
    for gr in sim.grids:
        ids = {cluster_of[p] for p in gr.controllers}
        if len(ids) > 1:
            v.append(Violation("R2", ERROR, f"one network contains {len(ids)} separate controller structures "
                               "(joined through cable or tunnels): controller conflict, 0 channels",
                               tuple(clusters[i][0] for i in sorted(ids))))

    tunnels = sim.tunnels()
    tunnel_by_key = {t.key: t for t in tunnels}

    # Which structures are "main" (faced by tunnel outer sides)?
    main_clusters = {cluster_of[t.faces_controller] for t in tunnels if t.faces_controller is not None}

    # R4 faces of main structures that carry no tunnel
    exposed, tunnel_faces, unused = 0, 0, []
    for ci in sorted(main_clusters):
        for pos in clusters[ci]:
            if not g.controller_validity[pos]:
                continue
            for d in Direction:
                n = step(pos, d)
                if layout.is_controller(n):
                    continue
                exposed += 1
                b = layout.get(n)
                if isinstance(b, CableBus) and isinstance(b.parts.get(d.opposite), P2PTunnel):
                    tunnel_faces += 1
                else:
                    unused.append(pos)
    if unused:
        v.append(Violation("R4", INFO, f"{len(unused)} exposed main-controller face(s) have no tunnel on them",
                           tuple(sorted(set(unused)))))

    # Per tunnel-cell checks: R5, R6 (static), R7
    static_merge: set[Pos] = set()
    for pos in layout.positions():
        b = layout.get(pos)
        if not isinstance(b, CableBus):
            continue
        sides = [d for d, p in b.parts.items() if isinstance(p, P2PTunnel)]
        if not sides:
            continue
        center = g.centers.get(pos)
        if center is None:
            v.append(Violation("R5", ERROR, "tunnel block has no centre cable: its tunnels connect to nothing "
                               "(no power, no channel)", (pos,)))
            continue
        adj = [c for c in center.conns if c.kind == "adjacent"]
        if not adj:
            v.append(Violation("R7", ERROR, "tunnel block has no open side leading to the tunnel network "
                               "(every side is a part or faces nothing that connects)", (pos,)))
        faced = {cluster_of[tunnel_by_key[(pos, d)].faces_controller] for d in sides
                 if tunnel_by_key[(pos, d)].faces_controller is not None}
        touching = {cluster_of[c.other(center).pos] for c in adj if c.other(center).kind == "controller"}
        if faced & touching:
            static_merge.add(pos)
            v.append(Violation("R6", ERROR, "tunnel block's cable connects straight to the controller structure "
                               "its tunnels face; cap that side with a tunnel or cable anchor or the tunnel "
                               "network merges into the main network", (pos,)))

    # R6 (simulated): any tunnel whose two sides ended up on the same network
    merged = sorted({t.key[0] for t in tunnels
                     if t.inner_grid == t.outer_grid and t.key[0] not in static_merge})
    if merged:
        v.append(Violation("R6", ERROR, "tunnel network and main network are the same network here",
                           tuple(merged)))

    # R8 / R9: tunnels that did not get a channel on an online tunnel network
    cap_fail, nest_fail, bottlenecks = [], [], set()
    for t in tunnels:
        inner = g.part_nodes[t.key]
        if t.has_channel or sim.grid(inner).state != ONLINE:
            continue
        if any(item.has(CANNOT_CARRY_COMPRESSED) for item in inner.blocked_by):
            nest_fail.append(t.key[0])
        else:
            cap_fail.append(t.key[0])
            for item in inner.blocked_by:
                if isinstance(item, Node):
                    bottlenecks.add(item.pos)
                else:
                    bottlenecks.add(item.a.pos)
    if cap_fail:
        v.append(Violation("R8", ERROR, f"{len(cap_fail)} tunnel(s) got no channel because a cable on their route "
                           f"to the tunnel-network controller is full (bottleneck at {sorted(bottlenecks)})",
                           tuple(sorted(set(cap_fail)))))
    if nest_fail:
        v.append(Violation("R9", ERROR, f"{len(nest_fail)} tunnel(s) can only reach a controller through another "
                           "ME tunnel; ME tunnels cannot be channelled through ME tunnels",
                           tuple(sorted(set(nest_fail)))))

    # R10: the tunnel network itself
    for gr in sim.grids:
        inners = [n for n in gr.nodes if n.kind == "p2p_inner"]
        if not inners:
            continue
        where = tuple(sorted({n.pos for n in inners}))
        if gr.state == CONFLICT:
            v.append(Violation("R10", ERROR, f"tunnel network is in controller conflict: {gr.conflict_reason}", where))
        elif gr.state == AD_HOC_OVERLOADED:
            v.append(Violation("R10", ERROR, f"tunnel network has no controller and needs {gr.adhoc_required} "
                               f"channels; without a controller the limit is {profile.normal_channel_capacity}. "
                               "Give it its own controller", where))
        elif gr.state == AD_HOC:
            v.append(Violation("R10", WARNING, f"tunnel network has no controller: works, but is limited to "
                               f"{profile.normal_channel_capacity} tunnels in total", where))

    # R11 power
    for gr in sim.grids:
        needs = [n for n in gr.nodes if n.has(REQUIRE_CHANNEL)]
        if needs and not gr.powered:
            kinds = "tunnel network" if any(n.kind == "p2p_inner" for n in needs) else "network"
            v.append(Violation("R11", ERROR, f"a {kinds} has no power source (energy acceptor or powered controller)",
                               tuple(sorted({n.pos for n in needs}))[:12]))

    # Frequencies
    groups: dict[tuple[int, int], dict[str, list]] = {}
    for t in tunnels:
        slot = groups.setdefault((t.inner_grid, t.freq), {"in": [], "out": []})
        slot["out" if t.output else "in"].append(t.key[0])
    no_in: list[tuple[int, Pos]] = []
    for (_, freq), slot in sorted(groups.items()):
        if len(slot["in"]) > 1:
            v.append(Violation("FREQ", ERROR, f"frequency {freq} has {len(slot['in'])} inputs on one network; "
                               "AE2 keeps only one", tuple(slot["in"])))
        # Inputs without an output are not reported: a controller build is designed
        # on its own, and its outputs live at the far end of the tunnel network.
        if slot["out"] and not slot["in"]:
            no_in += [(freq, p) for p in slot["out"]]
    def freqs(items):
        fs = sorted({f for f, _ in items})
        return ", ".join(map(str, fs[:10])) + (f", ... ({len(fs)} total)" if len(fs) > 10 else "")

    if no_in:
        v.append(Violation("FREQ", WARNING, f"{len(no_in)} output(s) have no input (frequencies "
                           f"{freqs(no_in)})", tuple(sorted({p for _, p in no_in}))))

    if not sim.converged:
        v.append(Violation("SIM", WARNING, f"tunnel links did not settle after {sim.iterations} iterations; "
                           "results show the last state"))

    # Metrics
    face_use = sim.face_usage()
    feeding = [t for t in tunnels if not t.output and t.faces_controller is not None
               and cluster_of[t.faces_controller] in main_clusters]
    working = [t for t in feeding if t.active]   # powered and channelled; output not required
    linked = [t for t in working if t.linked]
    delivered = sum(face_use.get((t.faces_controller, t.key[1].opposite), 0) for t in linked)
    devices = sim.devices()
    metrics = {
        "controller_blocks": len(g.controller_nodes),
        "main_structure_blocks": sum(len(clusters[i]) for i in main_clusters),
        "exposed_main_faces": exposed,
        "faces_with_tunnel": tunnel_faces,
        "working_input_tunnels": len(working),
        "linked_to_an_output": len(linked),
        "channel_capacity": len(working) * profile.dense_channel_capacity,
        "channels_in_use_through_tunnels": delivered,
        "devices_active": f"{sum(ok for _, ok in devices)}/{len(devices)}",
    }
    return Report(v, metrics, sim, clusters)
