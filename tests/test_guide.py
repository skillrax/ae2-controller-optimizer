import json
import re
import unittest

from ae2opt import DEFAULT_PROFILE, CableType, Direction, Layout
from ae2opt.guide import _pack, bill_of_materials, build_plan, check_outlets, plan_json, render_html

from .builds import basic_p2p

try:
    import ortools  # noqa: F401
    HAVE_ORTOOLS = True
except ImportError:
    HAVE_ORTOOLS = False

D = Direction


class GuideBasics(unittest.TestCase):
    def test_bill_of_materials(self):
        bom = bill_of_materials(basic_p2p())
        self.assertEqual(bom["controllers"], 2)
        self.assertEqual(bom["p2p_input"], 1)
        self.assertEqual(bom["p2p_output"], 1)
        self.assertEqual(bom["smart_cable"], 4)

    def test_pack_respects_capacity(self):
        bins = _pack([(8, "a"), (8, "b"), (8, "c"), (8, "d"), (5, "e"), (3, "f")], 32)
        self.assertEqual(sorted(sum(s for s, _ in b) for b in bins), [8, 32])
        self.assertTrue(all(sum(s for s, _ in b) <= 32 for b in bins))

    def test_plan_without_optimizer(self):
        plan = build_plan(basic_p2p(), DEFAULT_PROFILE, design_tunnel_controller=False)
        self.assertEqual(plan.inputs[0]["label"], "I1")
        self.assertEqual(plan.origin, (0, 0, -1))
        self.assertEqual(plan.tunnel_network["channels"], 2)
        json.dumps(plan_json(plan))  # serializable

    def test_html_is_self_contained(self):
        page = render_html(build_plan(basic_p2p(), DEFAULT_PROFILE, design_tunnel_controller=False))
        self.assertIn("<title>AE2 controller build</title>", page)
        self.assertNotRegex(page, r"(src|href)=\"https?://")   # works offline
        data = re.search(r"const PLAN = (\{.*?\});\n", page, re.S).group(1)
        self.assertEqual(json.loads(data)["bom"]["controllers"], 2)

    def test_outlet_check_flags_shared_faces(self):
        lay = Layout().controller((0, 0, 0)).controller((2, 0, 0))
        lay.cable((1, 0, 0), CableType.DENSE)          # touches two controller faces
        _, problems = check_outlets(lay, DEFAULT_PROFILE)
        self.assertTrue(problems)


@unittest.skipUnless(HAVE_ORTOOLS, "OR-Tools not installed")
class GuideWithOptimizer(unittest.TestCase):
    def test_plan_for_optimized_build(self):
        from ae2opt.optimize import OptimizeSpec, optimize
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=(3, 3, 3), time_limit=30))
        plan = build_plan(sol.harness, DEFAULT_PROFILE, time_limit=20)
        n = sol.tunnels_validated
        self.assertEqual(len(plan.inputs), n)
        self.assertEqual(sum(e["channels"] for e in plan.exits), n)      # every tunnel leaves via an exit
        self.assertTrue(all(e["channels"] <= 8 for e in plan.exits))
        self.assertTrue(all(t["channels"] <= 32 for t in plan.trunks))
        self.assertEqual(plan.tunnel_network["channels"], 2 * n)
        tc = plan.tunnel_controller
        self.assertIsNotNone(tc, plan.notes)
        self.assertGreaterEqual(tc["outlets"], plan.tunnel_network["outlets"])
        self.assertEqual(check_outlets(tc["layout"], DEFAULT_PROFILE)[1], [])

    def test_exits_inferred_without_ports(self):
        from ae2opt.optimize import OptimizeSpec, optimize
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=(2, 2, 2), time_limit=20))
        with_ports = build_plan(sol.harness, DEFAULT_PROFILE, design_tunnel_controller=False)
        without = build_plan(sol.layout, DEFAULT_PROFILE, design_tunnel_controller=False)
        self.assertEqual(sorted(map(tuple, (e["pos"] for e in with_ports.exits))),
                         sorted(map(tuple, (e["pos"] for e in without.exits))))
        self.assertEqual(sum(e["channels"] for e in without.exits), sol.tunnels_validated)


if __name__ == "__main__":
    unittest.main()
