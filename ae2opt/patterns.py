"""Repeating lattice patterns, used as warm-start shapes for the optimizer.

A pattern is "cell is a controller iff (a*x + b*y + c*z + offset) mod p is in S".
That family contains planes, diagonal layers and checkerboard-like lattices.
Most members are disconnected or break the cross rule; the valid ones are
ranked by exposed faces, an upper bound on the tunnels they can hold.
"""

from __future__ import annotations

import itertools

from .geometry import Direction, Pos, step

_AXES = ((Direction.EAST, Direction.WEST), (Direction.UP, Direction.DOWN), (Direction.SOUTH, Direction.NORTH))


def is_valid_shape(xs: frozenset[Pos]) -> bool:
    """Cross rule holds for every block and the blocks are face-connected."""
    if not xs:
        return False
    for c in xs:
        if sum(1 for a, b in _AXES if step(c, a) in xs and step(c, b) in xs) >= 2:
            return False
    seen, todo = set(), [next(iter(xs))]
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        todo.extend(n for n in (step(c, d) for d in Direction) if n in xs and n not in seen)
    return len(seen) == len(xs)


def exposed_faces(xs: frozenset[Pos]) -> int:
    return sum(1 for c in xs for d in Direction if step(c, d) not in xs)


def normalize(xs) -> frozenset[Pos]:
    """Shift so the shape touches the low face on every axis."""
    lo = [min(c[i] for c in xs) for i in range(3)]
    return frozenset((c[0] - lo[0], c[1] - lo[1], c[2] - lo[2]) for c in xs)


def canonical(xs, size) -> frozenset[Pos]:
    """The symmetric copy of a shape that satisfies the optimizer's symmetry rules:
    centre of mass in the low half on every axis (mirror), touching the low face on
    every axis (shift), and axis sums ordered for equal box sides (permute). Used
    to resume a run from a saved layout with symmetry breaking still on."""
    pts = [list(c) for c in xs]
    for a in range(3):
        if sum(2 * p[a] - (size[a] - 1) for p in pts) > 0:
            for p in pts:
                p[a] = size[a] - 1 - p[a]
    lo = [min(p[a] for p in pts) for a in range(3)]
    pts = [[p[a] - lo[a] for a in range(3)] for p in pts]
    # Sort axes by coordinate sum within each group of equal box sides.
    order = list(range(3))
    sums = [sum(p[a] for p in pts) for a in range(3)]
    for i in range(3):
        for j in range(i + 1, 3):
            if size[order[i]] == size[order[j]] and sums[order[i]] > sums[order[j]]:
                order[i], order[j] = order[j], order[i]
    return frozenset((p[order[0]], p[order[1]], p[order[2]]) for p in pts)


def lattice_shapes(size, periods=(2, 3, 4, 5, 6), limit: int = 10) -> list[frozenset[Pos]]:
    """The best `limit` distinct valid lattice shapes for a box, most faces first."""
    cells = [(x, y, z) for x in range(size[0]) for y in range(size[1]) for z in range(size[2])]
    seen: set[frozenset] = set()
    ranked = []
    for p in periods:
        for a, b, c in itertools.product(range(p), repeat=3):
            for r in range(1, p):
                for keep in itertools.combinations(range(p), r):
                    for off in range(p):
                        xs = frozenset(cell for cell in cells
                                       if (a * cell[0] + b * cell[1] + c * cell[2] + off) % p in keep)
                        if not xs or xs in seen:
                            continue
                        seen.add(xs)
                        if is_valid_shape(xs):
                            ranked.append((exposed_faces(xs), -len(xs), sorted(xs), xs))
    ranked.sort(reverse=True)
    out, shapes = [], set()
    for *_, xs in ranked:
        n = normalize(xs)
        if n not in shapes:
            shapes.add(n)
            out.append(n)
        if len(out) >= limit:
            break
    return out
