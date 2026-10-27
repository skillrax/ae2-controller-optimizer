import itertools
import unittest

from ae2opt import Profile
from ae2opt.geometry import Direction, step
from ae2opt.graph import controller_valid
from ae2opt.layout import Layout

try:
    from ae2opt.optimize import OptimizeSpec, optimize
    import ortools  # noqa: F401
    HAVE_ORTOOLS = True
except ImportError:
    HAVE_ORTOOLS = False


def brute_force(size) -> tuple[int, int]:
    """(max exposed faces, fewest controllers achieving it) over every valid
    controller shape in the box. With the exterior margin and default
    capacities every exposed face of a box this small can hold a working tunnel."""
    cells = [(x, y, z) for x in range(size[0]) for y in range(size[1]) for z in range(size[2])]
    best = (0, 0)
    for mask in range(1, 1 << len(cells)):
        xs = {c for i, c in enumerate(cells) if mask >> i & 1}
        lay = Layout()
        for c in xs:
            lay.controller(c)
        if not all(controller_valid(lay, c) for c in xs):
            continue
        seen, todo = set(), [min(xs)]
        while todo:
            c = todo.pop()
            if c in seen:
                continue
            seen.add(c)
            todo += [step(c, d) for d in Direction if step(c, d) in xs]
        if seen != xs:
            continue
        faces = sum(1 for c in xs for d in Direction if step(c, d) not in xs)
        if (faces, -len(xs)) > (best[0], -best[1]):
            best = (faces, len(xs))
    return best


@unittest.skipUnless(HAVE_ORTOOLS, "OR-Tools not installed")
class OptimizeTests(unittest.TestCase):
    def solve(self, profile=Profile(), **spec):
        spec.setdefault("time_limit", 30)
        sol = optimize(profile, OptimizeSpec(**spec))
        self.assertEqual(sol.tunnels_validated, sol.tunnels_model, sol.summary())
        self.assertTrue(sol.report.ok, sol.report.format())
        return sol

    def test_single_controller(self):
        sol = self.solve(size=(1, 1, 1))
        self.assertEqual((sol.tunnels_validated, sol.controllers, sol.channels), (6, 1, 192))
        self.assertEqual(sol.status, "OPTIMAL")

    def test_matches_brute_force_on_small_boxes(self):
        for size in ((2, 1, 1), (3, 1, 1), (2, 2, 1), (3, 2, 1), (2, 2, 2)):
            with self.subTest(size=size):
                faces, ctrls = brute_force(size)
                sol = self.solve(size=size)
                self.assertEqual(sol.tunnels_validated, faces)
                self.assertEqual(sol.controllers, ctrls)

    def test_no_exterior_cells_means_no_outer_faces(self):
        sol = self.solve(size=(1, 1, 1), exterior_tunnels=False)
        self.assertEqual(sol.tunnels_validated, 0)

    def test_blocked_cell_removes_a_face(self):
        sol = self.solve(size=(1, 1, 1), blocked=frozenset({(1, 0, 0)}))
        self.assertEqual(sol.tunnels_validated, 5)

    def ring(self):
        ring = frozenset(c for c in itertools.product(range(3), [0], range(3)) if c != (1, 0, 1))
        return dict(size=(3, 1, 3), required=ring, forbidden=frozenset({(1, 0, 1)}))

    def test_ring_uses_inner_gap(self):
        sol = self.solve(**self.ring())
        self.assertEqual(sol.tunnels_validated, 32)  # 28 outside + 4 in the centre gap
        centre = sol.layout.get((1, 0, 1))
        self.assertEqual(sum(1 for p in centre.parts.values() if p.kind == "p2p_me"), 4)

    def test_normal_capacity_limits_gap_cells(self):
        sol = self.solve(profile=Profile(name="cap3", normal_channel_capacity=3), **self.ring())
        self.assertEqual(sol.tunnels_validated, 31)

    def test_target_minimizes_controllers(self):
        sol = self.solve(size=(2, 2, 2), target_tunnels=20)
        self.assertGreaterEqual(sol.tunnels_validated, 20)
        self.assertEqual(sol.controllers, 5)

    def test_cuts_and_symmetry_keep_the_optimum(self):
        for size in ((2, 2, 2), (3, 2, 1), (2, 2, 1)):
            for variant in (dict(cuts=frozenset(), symmetry_breaking=False),
                            dict(cuts=frozenset({"cap", "exit", "tree", "degree"}), symmetry_breaking=True)):
                with self.subTest(size=size, **variant):
                    faces, _ = brute_force(size)
                    sol = self.solve(size=size, **variant)
                    self.assertEqual(sol.tunnels_validated, faces)
                    self.assertEqual(sol.status, "OPTIMAL")

    def test_3x3x3_is_proven(self):
        sol = self.solve(size=(3, 3, 3), time_limit=60)
        self.assertEqual((sol.tunnels_validated, sol.status), (74, "OPTIMAL"))

    def test_start_layout_is_never_lost(self):
        # A start shape alone (no main solve) is evaluated and returned.
        start = frozenset({(0, 0, 0), (1, 0, 0)})
        sol = self.solve(size=(2, 2, 1), start=start, seed_patterns=0, time_limit=0)
        self.assertEqual(set(sol.layout.controllers()), set(start))
        self.assertEqual(sol.tunnels_validated, 10)
        self.assertIn("seed start layout: 10 tunnels", " ".join(sol.notes))

    def test_lns_improves_a_poor_start(self):
        start = frozenset({(0, 0, 0), (1, 0, 0)})            # 10 tunnels; 3x2x1 allows 14
        sol = self.solve(size=(3, 2, 1), start=start, seed_patterns=0, time_limit=0,
                         lns_time=4, lns_step=1)
        self.assertEqual(sol.tunnels_validated, brute_force((3, 2, 1))[0])
        self.assertIn("LNS", " ".join(sol.notes))

    def test_lattice_seeds_are_valid_shapes(self):
        from ae2opt.patterns import is_valid_shape, lattice_shapes
        shapes = lattice_shapes((5, 5, 5), limit=5)
        self.assertTrue(shapes)
        self.assertTrue(all(is_valid_shape(s) for s in shapes))
        self.assertTrue(all(min(c[i] for c in s) == 0 for s in shapes for i in range(3)))

    def test_rejects_box_larger_than_profile(self):
        with self.assertRaises(ValueError):
            optimize(Profile(), OptimizeSpec(size=(8, 1, 1)))


if __name__ == "__main__":
    unittest.main()
