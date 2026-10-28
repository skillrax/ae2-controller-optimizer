"""The counting bound on tunnels, and the exact waste identity behind it.

For any build whose controllers sit in an a x b x c box,

    tunnels = 2abc + ab + bc + ca + 1 - waste,

where waste is a sum of non-negative defect counts (see waste_breakdown and
docs/RESEARCH.md for the proof). So 2abc + ab + bc + ca + 1 bounds every build,
whatever the channel capacities, colours, anchors or margin. The argument only
uses that the controllers form one connected structure (R2), that tunnels sit on
controller faces, and that every cable cell in the box belongs to cable that
reaches the outside of the box.
"""

from __future__ import annotations

from .geometry import Direction, Pos, step
from .layout import CableBus, Controller, Layout, P2PTunnel


def counting_bound(size) -> int:
    """Most tunnels any build in a box of this size can have."""
    a, b, c = size
    return 2 * a * b * c + a * b + b * c + c * a + 1


def waste_breakdown(layout: Layout, box_min: Pos | None = None, size=None) -> dict:
    """Split counting_bound(size) - tunnels into its defect counts. The box defaults to
    the controllers' bounding box. Every term is >= 0 for a build that obeys R2 and has
    all box cable connected to the outside; "stranded" counts cable groups that are not.

      loops           controller adjacencies beyond a tree (adjacent pairs - (n - 1))
      cable_loops     cable adjacencies beyond a forest, one tree per cable group
      extra_exits     box-surface cable cells beyond one per cable group
      surface_faces   box-surface faces of cable cells beyond one per surface cell
      unused          controller faces on cable or the outside without a tunnel
      other           half the faces next to non-cable, non-controller cells, plus 2
                      per such cell
    """
    xs = set(layout.controllers())
    if not xs:
        raise ValueError("no controllers")
    if box_min is None or size is None:
        box_min = tuple(min(p[i] for p in xs) for i in range(3))
        size = tuple(max(p[i] for p in xs) - box_min[i] + 1 for i in range(3))
    hi = tuple(box_min[i] + size[i] - 1 for i in range(3))

    def inside(p):
        return all(box_min[i] <= p[i] <= hi[i] for i in range(3))

    box = [(x, y, z) for x in range(box_min[0], hi[0] + 1)
           for y in range(box_min[1], hi[1] + 1) for z in range(box_min[2], hi[2] + 1)]
    if not all(inside(p) for p in xs):
        raise ValueError("controllers outside the box")
    cable = {p for p in box if isinstance(layout.blocks.get(p), CableBus)}
    other = [p for p in box if p not in xs and p not in cable]

    tunnels = 0
    for p, blk in layout.blocks.items():
        if isinstance(blk, CableBus):
            tunnels += sum(1 for d, part in blk.parts.items()
                           if isinstance(part, P2PTunnel) and step(p, d) in xs)

    def pairs(cells):
        return sum(1 for p in cells for d in (Direction.EAST, Direction.UP, Direction.SOUTH)
                   if step(p, d) in cells)

    def surface_faces(p):
        return sum(1 for d in Direction if not inside(step(p, d)))

    groups, seen = [], set()
    for p in cable:
        if p in seen:
            continue
        seen.add(p)
        todo, group = [p], []
        while todo:
            q = todo.pop()
            group.append(q)
            for d in Direction:
                r = step(q, d)
                if r in cable and r not in seen:
                    seen.add(r)
                    todo.append(r)
        groups.append(group)
    on_surface = [p for p in cable if surface_faces(p)]
    stranded = sum(1 for g in groups if not any(surface_faces(p) for p in g))

    faces_on = {"x": 0, "k": 0}   # controller / cable faces on box-cable-or-outside
    for p in xs:
        faces_on["x"] += sum(1 for d in Direction if step(p, d) in cable or not inside(step(p, d)))
    other_set = set(other)
    other_faces = sum(1 for p in xs | cable for d in Direction if step(p, d) in other_set) \
        + sum(surface_faces(p) for p in other)

    n, m = len(xs), len(cable)
    terms = {
        "loops": pairs(xs) - (n - 1),
        "cable_loops": pairs(cable) - (m - len(groups)),
        "extra_exits": len(on_surface) - (len(groups) - stranded),
        "surface_faces": sum(surface_faces(p) for p in on_surface) - len(on_surface),
        "unused": faces_on["x"] - tunnels,
        "other": other_faces / 2 + 2 * len(other),
        "stranded": stranded,
    }
    bound = counting_bound(size)
    waste = bound - tunnels
    # The identity: 2 * waste equals twice the sum of the terms, less one per stranded
    # group (it has no exit, so it is where the counting would credit one).
    assert 2 * waste == 2 * sum(v for k, v in terms.items() if k != "stranded") - 2 * stranded, (waste, terms)
    return {"size": tuple(size), "tunnels": tunnels, "bound": bound, "waste": waste, **terms}
