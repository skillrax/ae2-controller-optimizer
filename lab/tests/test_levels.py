import unittest

from ortools.sat.python import cp_model

from lab.cuts import levels
from lab.diagnose import build, shape_of

# Theorem 3 (docs/RESEARCH.md): the load-level model can only over-estimate. With the
# controllers fixed it should give exactly the real wiring's count, including on the
# 5x5x5 shape where plain flow splits channels through the core (302 relaxed, 279 real).
SHAPES = {"3x3x4": 96, "5x5x5": 298, "lab/builds/4x4x5-202.json": 202,
          "lab/builds/5x5x5-splitting-shape.json": 279}


def solve(box, shape, seconds=120):
    m, model = build(box, shape, ())
    levels(model, m)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_workers = 8
    status = solver.Solve(m)
    return solver.StatusName(status), round(solver.ObjectiveValue())


class LevelTests(unittest.TestCase):
    def test_fixed_shapes_give_the_real_count(self):
        for name, real in SHAPES.items():
            with self.subTest(name):
                self.assertEqual(solve(*shape_of(name)), ("OPTIMAL", real))

    def test_bounds_a_proven_box(self):
        self.assertEqual(solve((2, 4, 4), None), ("OPTIMAL", 88))


if __name__ == "__main__":
    unittest.main()
