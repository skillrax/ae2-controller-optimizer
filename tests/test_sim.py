import unittest

from ae2opt import CableAnchor, CableType, Direction, Layout, LayoutError, P2PTunnel, Profile, simulate
from ae2opt.sim import AD_HOC, AD_HOC_OVERLOADED, CHANNELS_DISABLED, CONFLICT, ONLINE

from .builds import DENSE, NORMAL, basic_p2p, dense_trunk_with_devices, devices_except

D = Direction


def active_devices(sim) -> int:
    return sum(ok for _, ok in sim.devices())


def grid_at(sim, pos):
    """Grid of the controller / centre cable / block node at pos."""
    g = sim.graph
    node = g.controller_nodes.get(pos) or g.centers.get(pos) or g.part_nodes.get((pos, None))
    return sim.grid(node)


class ControllerTests(unittest.TestCase):
    def test_single_controller_feeds_a_bus(self):
        lay = Layout().controller((0, 0, 0), powered=True).cable((1, 0, 0), NORMAL, parts=devices_except(D.WEST))
        sim = simulate(lay)
        self.assertEqual(grid_at(sim, (0, 0, 0)).state, ONLINE)
        self.assertEqual(active_devices(sim), 5)
        self.assertEqual(sim.face_usage()[((0, 0, 0), D.EAST)], 5)

    def test_one_face_carries_at_most_32(self):
        sim = simulate(dense_trunk_with_devices(4))  # 40 devices behind one face
        self.assertEqual(active_devices(sim), 32)
        self.assertEqual(sim.face_usage()[((0, 0, 0), D.EAST)], 32)

    def test_two_faces_carry_64(self):
        lay = dense_trunk_with_devices(4)
        for i in range(1, 5):
            lay.cable((-i, 0, 0), DENSE)
            lay.cable((-i, 0, -1), NORMAL, parts=devices_except(D.SOUTH))
            lay.cable((-i, 0, 1), NORMAL, parts=devices_except(D.NORTH))
        sim = simulate(lay)
        self.assertEqual(active_devices(sim), 64)

    def test_face_capacity_follows_profile(self):
        sim = simulate(dense_trunk_with_devices(8), Profile(name="uel", dense_channel_capacity=64))
        self.assertEqual(active_devices(sim), 64)

    def test_normal_cable_carries_at_most_8(self):
        lay = Layout().controller((0, 0, 0), powered=True).cable((1, 0, 0), NORMAL).cable((2, 0, 0), DENSE)
        lay.cable((2, 0, -1), NORMAL, parts=devices_except(D.SOUTH))
        lay.cable((2, 0, 1), NORMAL, parts=devices_except(D.NORTH))
        self.assertEqual(active_devices(simulate(lay)), 8)

    def test_line_of_seven_is_valid_eight_conflicts(self):
        for length, state in ((7, ONLINE), (8, CONFLICT)):
            with self.subTest(length=length):
                lay = Layout()
                for x in range(length):
                    lay.controller((x, 0, 0), powered=True)
                lay.cable((length, 0, 0), NORMAL, parts=devices_except(D.WEST))
                sim = simulate(lay)
                self.assertEqual(grid_at(sim, (0, 0, 0)).state, state)
                self.assertEqual(active_devices(sim), 5 if state == ONLINE else 0)

    def test_controller_size_follows_profile(self):
        lay = Layout()
        for x in range(8):
            lay.controller((x, 0, 0), powered=True)
        sim = simulate(lay, Profile(name="big", max_controller_size=(9, 7, 7)))
        self.assertEqual(grid_at(sim, (0, 0, 0)).state, ONLINE)

    def test_cross_rule(self):
        plus = Layout()
        for p in ((1, 0, 1), (0, 0, 1), (2, 0, 1), (1, 0, 0), (1, 0, 2)):
            plus.controller(p)
        sim = simulate(plus)
        self.assertFalse(sim.graph.controller_validity[(1, 0, 1)])
        self.assertEqual(sim.graph.controller_nodes[(1, 0, 1)].conns, [])

        tee = Layout()
        for p in ((1, 0, 1), (0, 0, 1), (2, 0, 1), (1, 0, 0)):
            tee.controller(p)
        self.assertTrue(all(simulate(tee).graph.controller_validity.values()))

        corner3d = Layout()  # pairs on x and y through (1,1,0)
        for p in ((1, 1, 0), (0, 1, 0), (2, 1, 0), (1, 0, 0), (1, 2, 0)):
            corner3d.controller(p)
        self.assertFalse(simulate(corner3d).graph.controller_validity[(1, 1, 0)])

    def test_two_structures_on_one_network_conflict(self):
        lay = Layout().controller((0, 0, 0), powered=True).controller((3, 0, 0))
        lay.cable((1, 0, 0), NORMAL).cable((2, 0, 0), NORMAL)
        lay.cable((1, 1, 0), NORMAL, parts=devices_except(D.DOWN))
        sim = simulate(lay)
        self.assertEqual(grid_at(sim, (0, 0, 0)).state, CONFLICT)
        self.assertEqual(active_devices(sim), 0)

    def test_channels_disabled(self):
        lay = dense_trunk_with_devices(4)
        sim = simulate(lay, Profile(name="off", channels_enabled=False))
        self.assertEqual(grid_at(sim, (0, 0, 0)).state, CHANNELS_DISABLED)
        self.assertEqual(active_devices(sim), 40)


class AdHocTests(unittest.TestCase):
    def build(self, north: int, south: int) -> Layout:
        lay = Layout().energy((0, 0, 0)).cable((1, 0, 0), DENSE)
        lay.cable((1, 0, -1), NORMAL, parts=devices_except(D.SOUTH, north))
        lay.cable((1, 0, 1), NORMAL, parts=devices_except(D.NORTH, south))
        return lay

    def test_eight_devices_work(self):
        sim = simulate(self.build(5, 3))
        self.assertEqual(grid_at(sim, (0, 0, 0)).state, AD_HOC)
        self.assertEqual(active_devices(sim), 8)

    def test_nine_devices_all_fail(self):
        sim = simulate(self.build(5, 4))
        self.assertEqual(grid_at(sim, (0, 0, 0)).state, AD_HOC_OVERLOADED)
        self.assertEqual(active_devices(sim), 0)

    def test_adhoc_limit_follows_normal_capacity(self):
        sim = simulate(self.build(5, 4), Profile(name="uel", normal_channel_capacity=16))
        self.assertEqual(active_devices(sim), 9)

    def test_tunnel_with_tunnel_outer_on_adhoc_grid_counts_nine(self):
        # A bus holding an ME tunnel, fed by another tunnel's outer side: the
        # compressed request plus a CANNOT_CARRY_COMPRESSED node makes it need 9.
        lay = Layout().energy((0, 0, 0))
        lay.cable((1, 0, 0), NORMAL, parts={D.EAST: P2PTunnel(1)})
        lay.cable((2, 0, 0), NORMAL, parts={D.NORTH: P2PTunnel(2)})
        sim = simulate(lay)
        self.assertEqual(grid_at(sim, (2, 0, 0)).state, AD_HOC_OVERLOADED)
        self.assertEqual(grid_at(sim, (2, 0, 0)).adhoc_required, 9)


class P2PTests(unittest.TestCase):
    def test_basic_build_delivers_channels(self):
        sim = simulate(basic_p2p())
        self.assertTrue(sim.converged)
        self.assertEqual(active_devices(sim), 5)
        tunnels = {t.key: t for t in sim.tunnels()}
        inp, out = tunnels[((1, 0, 0), D.WEST)], tunnels[((2, 0, 0), D.NORTH)]
        self.assertTrue(inp.active and out.active and inp.linked and out.linked)
        self.assertEqual(inp.faces_controller, (0, 0, 0))
        self.assertNotEqual(inp.inner_grid, inp.outer_grid)
        self.assertEqual(grid_at(sim, (4, 0, 0)).state, ONLINE)        # tunnel network
        self.assertEqual(grid_at(sim, (4, 0, 0)).channels_in_use, 2)   # input + output
        self.assertEqual(sim.face_usage()[((0, 0, 0), D.EAST)], 5)

    def test_adhoc_tunnel_network_works(self):
        sim = simulate(basic_p2p(tunnel_controller=False))
        self.assertEqual(grid_at(sim, (4, 0, 0)).state, AD_HOC)
        self.assertEqual(active_devices(sim), 5)

    def test_unpowered_tunnel_network_links_nothing(self):
        lay = basic_p2p()
        lay.controller((4, 0, 0), powered=False)
        sim = simulate(lay)
        self.assertEqual(sim.links, frozenset())
        self.assertEqual(active_devices(sim), 0)

    def test_tunnel_carries_at_most_one_face(self):
        # Output feeds a dense trunk with 40 devices: the tunnel caps at 32.
        lay = basic_p2p()
        lay.remove((2, 0, -1))
        for i in range(4):
            z = -1 - i
            lay.cable((2, 0, z), DENSE)
            lay.cable((1, 0, z), NORMAL, parts=devices_except(D.EAST))
            lay.cable((3, 0, z), NORMAL, parts=devices_except(D.WEST))
        sim = simulate(lay)
        self.assertEqual(active_devices(sim), 32)

    def test_part_on_dense_cable_is_rejected(self):
        with self.assertRaises(LayoutError):
            Layout().cable((0, 0, 0), CableType.DENSE, parts={D.UP: P2PTunnel(1)})
        Layout().cable((0, 0, 0), CableType.DENSE, parts={D.UP: CableAnchor()})  # anchors are fine

    def test_layout_json_round_trip(self):
        lay = basic_p2p()
        again = Layout.from_dict(lay.to_dict())
        self.assertEqual(again.blocks, lay.blocks)


if __name__ == "__main__":
    unittest.main()
