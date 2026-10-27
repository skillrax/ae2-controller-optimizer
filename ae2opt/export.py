"""Export a Layout as a Minecraft 1.12.2 structure file (.nbt) for a structure block.

IDs and NBT layout come from the AE2 source, identical in rv6 and Unofficial
Extended Life (paths under src/main/java/appeng/):
  * blocks: appliedenergistics2:controller / cable_bus / creative_energy_cell
    (core/api/definitions/ApiBlocks.java)
  * every bus part is item appliedenergistics2:part with damage = base + colour
    (items/parts/PartType.java; AEColor order WHITE..BLACK = 0..15, fluix = 16):
      smart cable 40+c, dense smart cable 60+c, cable anchor 120,
      terminal 380, ME P2P tunnel 460
  * a cable bus tile entity stores each part as "def:<side>" (the item) and
    "extra:<side>" (the part's own data); side 0-5 = D,U,N,S,W,E, 6 = centre
    (parts/CableBusContainer.java)
  * an ME P2P tunnel's data holds "output" (byte) and "freq" (short)
    (parts/p2p/PartP2PTunnel.java); the frequency registers when the tunnel
    joins its grid (me/cache/P2PCache.addNode), so loaded tunnels pair up.

Controllers and energy cells need no tile-entity data: placing the block
creates it. Every empty cell in the bounding box is written as air so the
structure clears its area when loaded.
"""

from __future__ import annotations

from pathlib import Path

from . import nbt
from .geometry import Direction
from .layout import (
    COLORS, CableAnchor, CableBus, CableType, Controller, DeviceBlock, DevicePart,
    EnergyAcceptor, Layout, Marker, P2PTunnel, TunnelPort,
)

DATA_VERSION_1_12_2 = 1343
MOD = "appliedenergistics2"

SMART_CABLE = 40
DENSE_SMART_CABLE = 60
CABLE_ANCHOR = 120
TERMINAL = 380
P2P_TUNNEL_ME = 460

# AEPartLocation ordinals: EnumFacing order, then INTERNAL.
CENTER_SIDE = 6


class ExportError(ValueError):
    pass


def _item(damage: int) -> dict:
    return {"id": f"{MOD}:part", "Count": nbt.Byte(1), "Damage": nbt.Short(damage)}


def _cable_damage(b: CableBus) -> int:
    base = DENSE_SMART_CABLE if b.cable is CableType.DENSE else SMART_CABLE
    return base + COLORS.index(b.color)


def _part_nbt(part) -> tuple[dict, dict]:
    if isinstance(part, P2PTunnel):
        if not 1 <= part.freq <= 32767:
            raise ExportError(f"P2P frequency {part.freq} does not fit AE2's short frequency")
        return _item(P2P_TUNNEL_ME), {"output": nbt.Byte(int(part.output)), "freq": nbt.Short(part.freq)}
    if isinstance(part, CableAnchor):
        return _item(CABLE_ANCHOR), {}
    if isinstance(part, DevicePart):
        return _item(TERMINAL), {}
    raise ExportError(f"cannot export part {part!r}")


def cable_bus_nbt(b: CableBus) -> dict:
    data: dict = {"id": f"{MOD}:blockcablebus"}
    if b.cable is not CableType.NONE:
        data[f"def:{CENTER_SIDE}"] = _item(_cable_damage(b))
        data[f"extra:{CENTER_SIDE}"] = {}
    for side in sorted(b.parts, key=lambda d: d.ordinal):
        item, extra = _part_nbt(b.parts[side])
        data[f"def:{side.ordinal}"] = item
        data[f"extra:{side.ordinal}"] = extra
    return data


def _state(b) -> tuple[dict, dict | None]:
    """(palette entry, tile-entity data or None) for one block."""
    if isinstance(b, Controller):
        return {"Name": f"{MOD}:controller", "Properties": {"state": "offline", "type": "block"}}, None
    if isinstance(b, CableBus):
        return {"Name": f"{MOD}:cable_bus"}, cable_bus_nbt(b)
    if isinstance(b, EnergyAcceptor):
        # Test builds use a creative energy cell as the power source.
        return {"Name": f"{MOD}:creative_energy_cell"}, None
    if isinstance(b, DeviceBlock):
        return {"Name": f"{MOD}:interface"}, None
    if isinstance(b, Marker):
        return {"Name": b.block}, None
    if isinstance(b, TunnelPort):
        raise ExportError("layout still contains tunnel-network ports (P); build a test rig "
                          "(ae2opt.harness) or replace them with your tunnel network first")
    raise ExportError(f"cannot export block {b!r}")


def to_structure(layout: Layout, author: str = "ae2opt") -> dict:
    if not layout.blocks:
        raise ExportError("layout is empty")
    xs, ys, zs = zip(*layout.blocks)
    lo = (min(xs), min(ys), min(zs))
    size = (max(xs) - lo[0] + 1, max(ys) - lo[1] + 1, max(zs) - lo[2] + 1)
    if any(s > 32 for s in size):
        raise ExportError(f"structure is {size[0]}x{size[1]}x{size[2]}; a 1.12 structure block "
                          "loads at most 32x32x32")
    palette: list[dict] = []
    index: dict[str, int] = {}

    def state_id(entry: dict) -> int:
        key = repr(sorted((k, sorted(v.items()) if isinstance(v, dict) else v) for k, v in entry.items()))
        if key not in index:
            index[key] = len(palette)
            palette.append(entry)
        return index[key]

    blocks = []
    for x in range(size[0]):
        for y in range(size[1]):
            for z in range(size[2]):
                pos = (x + lo[0], y + lo[1], z + lo[2])
                b = layout.get(pos)
                if b is None:
                    entry, te = {"Name": "minecraft:air"}, None
                else:
                    entry, te = _state(b)
                block = {"pos": nbt.List(nbt.INT, [x, y, z]), "state": nbt.Int(state_id(entry))}
                if te is not None:
                    block["nbt"] = te
                blocks.append(block)

    return {
        "DataVersion": nbt.Int(DATA_VERSION_1_12_2),
        "author": author,
        "size": nbt.List(nbt.INT, list(size)),
        "palette": nbt.List(nbt.COMPOUND, palette),
        "blocks": nbt.List(nbt.COMPOUND, blocks),
        "entities": nbt.List(nbt.COMPOUND, []),
    }


def save_structure(layout: Layout, path: str | Path, author: str = "ae2opt") -> dict:
    """Write the .nbt file. Returns {"size": ..., "origin": ...} for instructions."""
    root = to_structure(layout, author)
    nbt.save(path, root)
    xs, ys, zs = zip(*layout.blocks)
    return {"size": tuple(root["size"].items), "origin": (min(xs), min(ys), min(zs))}
