"""Candidate cuts for the relaxed model, each with the argument that it is valid.

A cut here must hold for some optimal real build (it may remove builds, as long as every
build can be turned into one that satisfies it without losing tunnels), so adding it keeps
the relaxed optimum an upper bound. Each function takes the base `_Model` (for its
variables) and the model clone to add to. A cut only moves to the app once its argument
is written up in docs/RESEARCH.md.
"""

from ae2opt.geometry import Direction, step


def _box_cable_pairs(model, m):
    """One boolean per pair of adjacent box cells that are both cable."""
    r, k = model.r, model.k
    pairs = {}
    for n in r.box:
        if n not in k:
            continue
        for d in (Direction.EAST, Direction.UP, Direction.SOUTH):
            o = step(n, d)
            if o in r.box_set and o in k:
                e = m.NewBoolVar(f"ek{n}{d.name}")
                m.AddImplication(e, k[n])
                m.AddImplication(e, k[o])
                m.AddBoolOr([k[n].Not(), k[o].Not(), e])
                pairs[(n, o)] = pairs[(o, n)] = e
    return pairs


def cable_faces(model, m):
    """The cable half of Theorem 1 (docs/RESEARCH.md section 2).

    Per box cable cell: its tunnels, its box-cable neighbours and its box-surface faces
    are different faces, so they add up to at most 6. Over the box: the box cable is a
    graph with at least (cells - components) edges, and by A2 every component has a
    surface cell, so edges >= cells - surface cells.

    Valid: the per-cell count holds for every build. A2 holds once cable that carries no
    tunnel is deleted, which loses nothing; every other box cable cell lies on a route to
    the tunnel network, and that route leaves the box through a surface cell.
    """
    r, k = model.r, model.k
    pairs = _box_cable_pairs(model, m)
    tunnels_into = {n: [] for n in r.box}
    for (c, d), f in model.f.items():
        n = step(c, d)
        if n in tunnels_into:
            tunnels_into[n].append(f)
    surface = {n: sum(1 for d in Direction if step(n, d) not in r.box_set) for n in r.box}
    for n in r.box:
        if n not in k:
            continue
        nbrs = [pairs[(n, step(n, d))] for d in Direction if (n, step(n, d)) in pairs]
        m.Add(sum(tunnels_into[n]) + sum(nbrs) + surface[n] * k[n] <= 6 * k[n])
    edges = sum(set(pairs.values()))
    m.Add(edges >= sum(k[n] for n in r.box if n in k) - sum(k[n] for n in r.box if n in k and surface[n]))


CUTS = {"cable_faces": cable_faces}
