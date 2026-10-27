"""Block positions and the six facings, in Minecraft's EnumFacing order."""

from __future__ import annotations

from enum import Enum

Pos = tuple[int, int, int]


class Direction(Enum):
    # Declaration order == EnumFacing ordinal order (D, U, N, S, W, E). AE2 iterates
    # sides in this order, so keeping it makes the simulation deterministic in the
    # same way.
    DOWN = (0, -1, 0)
    UP = (0, 1, 0)
    NORTH = (0, 0, -1)
    SOUTH = (0, 0, 1)
    WEST = (-1, 0, 0)
    EAST = (1, 0, 0)

    @property
    def offset(self) -> Pos:
        return self.value

    @property
    def opposite(self) -> "Direction":
        return _OPPOSITE[self]

    @property
    def axis(self) -> str:
        return "xyz"[[abs(c) for c in self.value].index(1)]

    @property
    def ordinal(self) -> int:
        return _ORDER.index(self)

    @classmethod
    def parse(cls, name: str) -> "Direction":
        return cls[name.strip().upper()]


_ORDER = list(Direction)
_OPPOSITE = {
    Direction.DOWN: Direction.UP, Direction.UP: Direction.DOWN,
    Direction.NORTH: Direction.SOUTH, Direction.SOUTH: Direction.NORTH,
    Direction.WEST: Direction.EAST, Direction.EAST: Direction.WEST,
}


def step(pos: Pos, d: Direction) -> Pos:
    dx, dy, dz = d.value
    return (pos[0] + dx, pos[1] + dy, pos[2] + dz)


def neighbors(pos: Pos):
    for d in Direction:
        yield d, step(pos, d)
