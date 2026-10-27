import unittest

from ae2opt import CableType, Direction, Layout, P2PTunnel, check

from .builds import DENSE, NORMAL, basic_p2p, devices_except

D = Direction


def rules(report, severity="error"):
    return {v.rule for v in report.violations if v.severity == severity}


class RuleTests(unittest.TestCase):
    def test_basic_build_is_valid(self):
        r = check(basic_p2p())
        self.assertTrue(r.ok, r.format())
        self.assertEqual(r.metrics["working_input_tunnels"], 1)
        self.assertEqual(r.metrics["channel_capacity"], 32)
        self.assertEqual(r.metrics["channels_in_use_through_tunnels"], 5)
        self.assertEqual(r.metrics["exposed_main_faces"], 6)
        self.assertEqual(r.metrics["faces_with_tunnel"], 1)
        self.assertIn("R4", rules(r, "info"))

    def test_r1_bounding_box(self):
        lay = Layout()
        for x in range(8):
            lay.controller((x, 0, 0), powered=True)
        self.assertIn("R1", rules(check(lay)))

    def test_r2_two_structures(self):
        lay = Layout().controller((0, 0, 0), powered=True).controller((3, 0, 0))
        lay.cable((1, 0, 0), NORMAL).cable((2, 0, 0), NORMAL)
        self.assertIn("R2", rules(check(lay)))

    def test_r3_cross(self):
        lay = Layout()
        for p in ((1, 0, 1), (0, 0, 1), (2, 0, 1), (1, 0, 0), (1, 0, 2)):
            lay.controller(p, powered=True)
        r = check(lay)
        self.assertIn("R3", rules(r))
        self.assertEqual(r.by_rule("R3")[0].positions, ((1, 0, 1),))

    def test_r5_tunnel_without_cable(self):
        lay = basic_p2p()
        lay.cable((1, 0, 0), CableType.NONE, parts={D.WEST: P2PTunnel(1)})
        self.assertIn("R5", rules(check(lay)))

    def test_r6_uncapped_face_merges_networks(self):
        lay = basic_p2p()
        lay.controller((0, 1, 0), powered=True).controller((1, 1, 0))  # touches the tunnel cell's UP side
        r = check(lay)
        self.assertIn("R6", rules(r))
        self.assertEqual(r.by_rule("R6")[0].positions, ((1, 0, 0),))

    def test_tunnel_network_controller_next_to_tunnel_cell_is_fine(self):
        lay = Layout().controller((0, 0, 0), powered=True)
        lay.cable((1, 0, 0), NORMAL, parts={D.WEST: P2PTunnel(1)})
        lay.controller((1, 1, 0), powered=True)                       # tunnel-network controller
        lay.cable((1, -1, 0), NORMAL, parts={D.DOWN: P2PTunnel(1, output=True)})
        lay.cable((1, -2, 0), NORMAL, parts=devices_except(D.UP))
        r = check(lay)
        self.assertTrue(r.ok, r.format())
        self.assertNotIn("R6", rules(r))

    def test_r7_enclosed_tunnel_cell(self):
        lay = Layout()
        lay.cable((0, 0, 0), NORMAL, parts={d: P2PTunnel(i + 1) for i, d in enumerate(D)})
        for d in D:
            lay.controller(d.offset, powered=True)
        self.assertIn("R7", rules(check(lay)))

    def tunnel_bottleneck(self, with_controller: bool) -> Layout:
        lay = Layout()
        if with_controller:
            lay.controller((0, 0, 0), powered=True)
        else:
            lay.energy((0, 0, 0))
        lay.cable((1, 0, 0), NORMAL).cable((2, 0, 0), DENSE)
        lay.cable((2, 0, -1), NORMAL, parts={d: P2PTunnel(i + 1) for i, d in
                                             enumerate(x for x in D if x is not D.SOUTH)})
        lay.cable((2, 0, 1), NORMAL, parts={d: P2PTunnel(i + 6) for i, d in
                                            enumerate([x for x in D if x is not D.NORTH][:4])})
        return lay

    def test_r8_tunnel_route_capacity(self):
        r = check(self.tunnel_bottleneck(with_controller=True))
        self.assertIn("R8", rules(r))
        self.assertIn("(1, 0, 0)", r.by_rule("R8")[0].message)

    def test_r9_nested_tunnel(self):
        lay = basic_p2p()
        parts = devices_except(D.SOUTH)
        parts[D.NORTH] = P2PTunnel(2)
        lay.cable((2, 0, -1), NORMAL, parts=parts)
        self.assertIn("R9", rules(check(lay)))

    def test_r10_adhoc_tunnel_network_overloaded(self):
        r = check(self.tunnel_bottleneck(with_controller=False))
        self.assertIn("R10", rules(r))

    def test_r10_adhoc_warning(self):
        r = check(basic_p2p(tunnel_controller=False))
        self.assertTrue(r.ok, r.format())
        self.assertIn("R10", rules(r, "warning"))

    def test_r11_unpowered(self):
        lay = basic_p2p()
        lay.controller((4, 0, 0), powered=False)
        self.assertIn("R11", rules(check(lay)))

    def test_duplicate_input_frequency(self):
        lay = basic_p2p()
        lay.cable((3, 0, 0), NORMAL, parts={D.NORTH: P2PTunnel(1)})
        self.assertIn("FREQ", rules(check(lay)))


if __name__ == "__main__":
    unittest.main()
