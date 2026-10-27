"""Reusable test layouts."""

from ae2opt import CableType, Direction, DevicePart, Layout, P2PTunnel

D = Direction
NORMAL, DENSE = CableType.NORMAL, CableType.DENSE

def devices_except(side: Direction, count: int = 5) -> dict:
    """Device parts on up to 5 sides of a bus, leaving ``side`` open for its connection."""
    return {d: DevicePart() for d in [d for d in Direction if d is not side][:count]}


def basic_p2p(tunnel_controller: bool = True, devices: int = 5) -> Layout:
    """The smallest correct P2P build.

        y=0, looking down (x right, z down):

          z=-1          . . [B] . .      B = bus with devices, fed by the output tunnel
          z= 0  [M] [I] [C] [C] [T]

    M  main controller (powered)        I  tunnel cell: input tunnel facing M
    C  tunnel-network cable; (2,0,0) has the output tunnel on its NORTH side
    T  tunnel-network controller (powered), or an energy acceptor if ad-hoc
    """
    lay = Layout()
    lay.controller((0, 0, 0), powered=True)
    lay.cable((1, 0, 0), NORMAL, parts={D.WEST: P2PTunnel(1)})
    lay.cable((2, 0, 0), NORMAL, parts={D.NORTH: P2PTunnel(1, output=True)})
    lay.cable((3, 0, 0), NORMAL)
    if tunnel_controller:
        lay.controller((4, 0, 0), powered=True)
    else:
        lay.energy((4, 0, 0))
    lay.cable((2, 0, -1), NORMAL, parts=devices_except(D.SOUTH, devices))
    return lay


def dense_trunk_with_devices(length: int, origin_x: int = 1) -> Layout:
    """Controller at origin feeding a dense trunk along +x; each trunk cell has a
    normal-cable bus to its north and south carrying 5 device parts each
    (10 devices per trunk cell)."""
    lay = Layout()
    lay.controller((0, 0, 0), powered=True)
    for i in range(length):
        x = origin_x + i
        lay.cable((x, 0, 0), DENSE)
        lay.cable((x, 0, -1), NORMAL, parts=devices_except(D.SOUTH))
        lay.cable((x, 0, 1), NORMAL, parts=devices_except(D.NORTH))
    return lay
