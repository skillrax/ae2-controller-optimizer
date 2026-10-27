"""What is placed where: the input to the simulator and the rules checker.

Only states that can exist in game are representable. Placing a P2P tunnel or
other bus part on dense cable raises LayoutError, because AE2 refuses that
placement (AEBasePart.canBePlacedOn only accepts CABLE). A bus with parts and no
centre cable *is* allowed: it happens in game when the cable is removed, and
the rules report it (R5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .geometry import Direction, Pos

COLORS = (
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
    "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black",
    "transparent",
)
_COLOR_ALIASES = {"fluix": "transparent", "none": "transparent", "": "transparent"}


def normalize_color(color: str) -> str:
    c = _COLOR_ALIASES.get(color.strip().lower(), color.strip().lower())
    if c not in COLORS:
        raise LayoutError(f"unknown cable colour {color!r}")
    return c


class LayoutError(ValueError):
    pass


class CableType(Enum):
    NONE = "none"      # bus holding parts with no centre cable
    NORMAL = "normal"  # glass / covered / smart: normal channel capacity
    DENSE = "dense"    # dense / dense smart: dense channel capacity, no bus parts


# ---------------------------------------------------------------- parts (on a bus side)

@dataclass(frozen=True)
class P2PTunnel:
    """ME P2P tunnel. Inner node on the bus's network, outer node faces out."""
    freq: int
    output: bool = False
    kind = "p2p_me"


@dataclass(frozen=True)
class CableAnchor:
    """Caps a side so the centre cable does not connect through it."""
    kind = "anchor"


@dataclass(frozen=True)
class DevicePart:
    """Any channel-using bus part (terminal, interface part, bus...)."""
    kind = "device"


Part = P2PTunnel | CableAnchor | DevicePart


# ---------------------------------------------------------------- blocks (one per cell)

@dataclass(frozen=True)
class Controller:
    # True if power is fed straight into this controller block (it accepts FE/AE).
    powered: bool = False
    kind = "controller"


@dataclass(frozen=True)
class CableBus:
    cable: CableType = CableType.NORMAL
    color: str = "transparent"
    parts: dict[Direction, Part] = field(default_factory=dict)
    kind = "cable"

    def __hash__(self):  # parts dict makes the default hash fail
        return hash((self.cable, self.color, tuple(sorted((d.ordinal, p) for d, p in self.parts.items()))))


@dataclass(frozen=True)
class DeviceBlock:
    """A full-block machine that needs a channel (ME drive, interface block...)."""
    kind = "device_block"


@dataclass(frozen=True)
class EnergyAcceptor:
    """Powers the grid it is on. Does not need a channel."""
    kind = "energy_acceptor"


@dataclass(frozen=True)
class TunnelPort:
    """Modelling stand-in for "the rest of the tunnel network": a powered channel
    source with its own (unconstrained) controller elsewhere. Used to check a
    controller build without modelling the tunnel network's own controller. Each
    connection still carries at most the dense capacity. Not a real block."""
    kind = "port"


@dataclass(frozen=True)
class Marker:
    """A plain vanilla block that only marks a spot for a tester (no AE2 node)."""
    block: str = "minecraft:gold_block"
    kind = "marker"


Block = Controller | CableBus | DeviceBlock | EnergyAcceptor | TunnelPort | Marker


class Layout:
    def __init__(self) -> None:
        self.blocks: dict[Pos, Block] = {}

    # ------------------------------------------------------------ editing
    def place(self, pos: Pos, block: Block) -> "Layout":
        pos = tuple(int(c) for c in pos)
        if isinstance(block, CableBus):
            block = CableBus(block.cable, normalize_color(block.color), dict(block.parts))
            for d, part in block.parts.items():
                if not isinstance(d, Direction):
                    raise LayoutError(f"part side must be a Direction, got {d!r}")
                if block.cable is CableType.DENSE and not isinstance(part, CableAnchor):
                    raise LayoutError(
                        f"{type(part).__name__} at {pos} {d.name}: AE2 does not allow this part on dense cable")
        self.blocks[pos] = block
        return self

    def controller(self, pos: Pos, powered: bool = False) -> "Layout":
        return self.place(pos, Controller(powered))

    def cable(self, pos: Pos, cable: CableType = CableType.NORMAL, color: str = "transparent",
              parts: dict[Direction, Part] | None = None) -> "Layout":
        return self.place(pos, CableBus(cable, color, dict(parts or {})))

    def device(self, pos: Pos) -> "Layout":
        return self.place(pos, DeviceBlock())

    def energy(self, pos: Pos) -> "Layout":
        return self.place(pos, EnergyAcceptor())

    def port(self, pos: Pos) -> "Layout":
        return self.place(pos, TunnelPort())

    def marker(self, pos: Pos, block: str = "minecraft:gold_block") -> "Layout":
        return self.place(pos, Marker(block))

    def remove(self, pos: Pos) -> "Layout":
        self.blocks.pop(tuple(pos), None)
        return self

    # ------------------------------------------------------------ queries
    def get(self, pos: Pos) -> Block | None:
        return self.blocks.get(pos)

    def positions(self) -> list[Pos]:
        return sorted(self.blocks)

    def controllers(self) -> list[Pos]:
        return [p for p in self.positions() if isinstance(self.blocks[p], Controller)]

    def is_controller(self, pos: Pos) -> bool:
        return isinstance(self.blocks.get(pos), Controller)

    # ------------------------------------------------------------ JSON
    def to_dict(self) -> dict:
        out = []
        for pos in self.positions():
            b = self.blocks[pos]
            d: dict = {"pos": list(pos), "type": b.kind}
            if isinstance(b, Controller) and b.powered:
                d["powered"] = True
            if isinstance(b, Marker):
                d["block"] = b.block
            if isinstance(b, CableBus):
                d["cable"] = b.cable.value
                if b.color != "transparent":
                    d["color"] = b.color
                if b.parts:
                    d["parts"] = {side.name.lower(): _part_to_dict(p)
                                  for side, p in sorted(b.parts.items(), key=lambda kv: kv[0].ordinal)}
            out.append(d)
        return {"schema": 1, "blocks": out}

    @classmethod
    def from_dict(cls, data: dict) -> "Layout":
        lay = cls()
        for d in data.get("blocks", []):
            pos = tuple(d["pos"])
            t = d["type"]
            if t == "controller":
                lay.controller(pos, bool(d.get("powered", False)))
            elif t == "cable":
                parts = {Direction.parse(s): _part_from_dict(p) for s, p in d.get("parts", {}).items()}
                lay.cable(pos, CableType(d.get("cable", "normal")), d.get("color", "transparent"), parts)
            elif t == "device_block":
                lay.device(pos)
            elif t == "energy_acceptor":
                lay.energy(pos)
            elif t == "marker":
                lay.marker(pos, d.get("block", "minecraft:gold_block"))
            elif t == "port":
                lay.port(pos)
            else:
                raise LayoutError(f"unknown block type {t!r} at {pos}")
        return lay

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1), "utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Layout":
        return cls.from_dict(json.loads(Path(path).read_text("utf-8")))


def _part_to_dict(p: Part) -> dict:
    if isinstance(p, P2PTunnel):
        return {"type": p.kind, "freq": p.freq, "output": p.output}
    return {"type": p.kind}


def _part_from_dict(d: dict) -> Part:
    t = d["type"]
    if t == "p2p_me":
        return P2PTunnel(int(d["freq"]), bool(d.get("output", False)))
    if t == "anchor":
        return CableAnchor()
    if t == "device":
        return DevicePart()
    raise LayoutError(f"unknown part type {t!r}")
