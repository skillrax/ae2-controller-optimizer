import unittest

from ae2opt import DEFAULT_PROFILE, library
from ae2opt.bounds import counting_bound, waste_breakdown
from ae2opt.layout import Layout
from ae2opt.optimize import OptimizeSpec, optimize

TERMS = ("loops", "cable_loops", "extra_exits", "surface_faces", "unused", "other")


class CountingBoundTests(unittest.TestCase):
    def test_formula(self):
        self.assertEqual(counting_bound((1, 1, 1)), 6)
        self.assertEqual(counting_bound((4, 4, 4)), 177)
        self.assertEqual(counting_bound((6, 6, 6)), 541)
        self.assertEqual(counting_bound((7, 7, 7)), 834)

    def test_identity_on_every_library_build(self):
        for e in library.entries():
            with self.subTest(e["id"]):
                w = waste_breakdown(library.load(e["id"]))
                self.assertEqual(w["tunnels"], e["tunnels"])
                self.assertEqual(w["stranded"], 0)
                for k in TERMS:
                    self.assertGreaterEqual(w[k], 0, k)
                self.assertEqual(w["waste"], sum(w[k] for k in TERMS))
                self.assertLessEqual(e["upper"], counting_bound(e["box"]))

    def test_lines_reach_the_bound(self):
        for e in library.entries():
            if sorted(e["box"])[:2] == [1, 1]:
                self.assertEqual(e["tunnels"], counting_bound(e["box"]), e["id"])

    def test_identity_counts_empty_cells(self):
        # Emptying any cell of a proven build: the terms still add up to the waste
        # (waste_breakdown asserts the identity itself), and none goes negative.
        base = library.load("3x3x4")
        xs = base.controllers()
        lo = tuple(min(p[i] for p in xs) for i in range(3))
        size = tuple(max(p[i] for p in xs) - lo[i] + 1 for i in range(3))
        for pos in [p for p in base.positions() if base.get(p).kind == "cable"]:
            lay = Layout.from_dict(base.to_dict()).remove(pos)
            w = waste_breakdown(lay, lo, size)
            for k in TERMS:
                self.assertGreaterEqual(w[k], 0, (pos, k))
            self.assertEqual(w["waste"], sum(w[k] for k in TERMS) - w["stranded"])

    def test_optimizer_reports_it(self):
        events = []
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=(2, 2, 2), time_limit=10, workers=8),
                       progress=events.append)
        self.assertIn({"kind": "bound", "phase": "counting bound", "tunnels": 29},
                      [{k: e.get(k) for k in ("kind", "phase", "tunnels")} for e in events])
        self.assertLessEqual(sol.upper_bound, 29)


if __name__ == "__main__":
    unittest.main()
