"""Plain-text layer view of a layout, one Y level at a time (x right, z down)."""

from __future__ import annotations

from .layout import CableBus, CableType, Controller, Layout, P2PTunnel

LEGEND = ("#  controller   1-6  cable cell with that many tunnels   "
          "-  normal cable (no tunnels)   =  dense cable   P  tunnel-network port   .  empty")


def _glyph(b) -> str:
    if b is None:
        return "."
    if isinstance(b, Controller):
        return "#"
    if isinstance(b, CableBus):
        n = sum(1 for p in b.parts.values() if isinstance(p, P2PTunnel))
        if n:
            return str(n)
        return "=" if b.cable is CableType.DENSE else "-"
    if b.kind == "port":
        return "P"
    return "?"


def render_layers(layout: Layout) -> str:
    if not layout.blocks:
        return "(empty)"
    xs, ys, zs = zip(*layout.blocks)
    out = [LEGEND]
    for y in range(max(ys), min(ys) - 1, -1):
        out.append(f"\ny={y}")
        for z in range(min(zs), max(zs) + 1):
            out.append(" ".join(_glyph(layout.get((x, y, z))) for x in range(min(xs), max(xs) + 1)))
    return "\n".join(out)
