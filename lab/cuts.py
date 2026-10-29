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


def _tunnels_on(model):
    """Per cable cell: the tunnel variables on its faces."""
    into = {n: [] for n in model.r.cells}
    for (c, d), f in model.f.items():
        into[step(c, d)].append(f)
    return {n: sum(v) if v else 0 for n, v in into.items()}


def parent_room(model, m):
    """Every cable cell has a parent with room for it (O6: no splitting).

    In a real build each cable cell n sends its whole load L_n (its own tunnels plus its
    subtree's) to one parent: a cable neighbour o, or the outside. If o is smart, its load
    L_o >= L_n + t_o >= t_n + t_o, and L_o <= the smart capacity. So n has a neighbour o that
    is dense, or smart with t_n + t_o <= capacity, or n exits. A choice variable g per
    candidate parent encodes this.

    Valid: it is implied by the forest model for every build, with g = the real parent.
    """
    r, k, s = model.r, model.k, model.s
    cap = model.profile.normal_channel_capacity
    t = _tunnels_on(model)
    for n in r.cells:
        options = []
        if n in model.sink:
            options.append(k[n])          # an exit always has room
        for d in Direction:
            o = step(n, d)
            if o not in k:
                continue
            g = m.NewBoolVar(f"room{n}{d.name}")
            m.AddImplication(g, k[o])
            if not (isinstance(t[n], int) and isinstance(t[o], int)):
                m.Add(t[n] + t[o] <= cap).OnlyEnforceIf([g, s[o]])
            options.append(g)
        m.AddBoolOr(options + [k[n].Not()])


def load_chain(model, m):
    """Loads grow along the route to the outside (O6: no splitting, any number of hops).

    Each cable cell gets a load L_n with t_n <= L_n <= its capacity, and must have a
    parent: the outside (if it can exit), or a cable neighbour o with L_o >= L_n + t_o.

    Valid: in a real build, take L_n = the cell's real load (own tunnels plus its subtree)
    and the real parent. The load is within capacity, and a parent carries its child's
    whole load plus its own tunnels. (Real loads also add up over several children; this
    cut only asks for one child at a time, so it is weaker than the forest model.)
    """
    r, k, d = model.r, model.k, model.d
    normal = model.profile.normal_channel_capacity
    dense = model.profile.dense_channel_capacity
    t = _tunnels_on(model)
    L = {}
    for n in r.cells:
        L[n] = m.NewIntVar(0, dense, f"L{n}")
        m.Add(L[n] <= normal * k[n] + (dense - normal) * d[n])
        m.Add(L[n] >= t[n])
    for n in r.cells:
        options = [k[n].Not()]
        if n in model.sink:
            options.append(k[n])
        for dd in Direction:
            o = step(n, dd)
            if o not in k:
                continue
            g = m.NewBoolVar(f"chain{n}{dd.name}")
            m.AddImplication(g, k[o])
            m.Add(L[o] >= L[n] + t[o]).OnlyEnforceIf(g)
            options.append(g)
        m.AddBoolOr(options)


CUTS = {"cable_faces": cable_faces, "parent_room": parent_room, "load_chain": load_chain}
