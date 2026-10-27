import threading
import unittest

from ae2opt import DEFAULT_PROFILE
from ae2opt.optimize import OptimizeSpec, layout_from_vector, optimize, polish_layout

try:
    import ortools  # noqa: F401
    HAVE_ORTOOLS = True
except ImportError:
    HAVE_ORTOOLS = False


def _ports(lay):
    return sum(1 for b in lay.blocks.values() if b.kind == "port")


@unittest.skipUnless(HAVE_ORTOOLS, "needs OR-Tools")
class PolishTests(unittest.TestCase):
    def test_polish_layout_tidies_a_raw_checkpoint_without_losing_tunnels(self):
        events = []
        spec = OptimizeSpec(size=(3, 3, 3), time_limit=20, workers=8, polish_time=0)
        sol = optimize(DEFAULT_PROFILE, spec, progress=lambda ev: events.append(ev))
        model = next(ev["model"] for ev in events if ev["kind"] == "start")
        # The first solution the search reports: a raw checkpoint, as best.json holds mid-run.
        first = next(ev for ev in events if ev["kind"] == "solution")
        raw = layout_from_vector(model, first["vec"])
        tidy, info = polish_layout(DEFAULT_PROFILE, spec, raw, seconds=10)
        self.assertGreaterEqual(info["after"]["tunnels"], info["before"]["tunnels"])
        self.assertLessEqual(info["after"]["exits"], info["before"]["exits"])
        self.assertEqual(len(tidy.controllers()), len(raw.controllers()))
        self.assertGreater(sol.tunnels_validated, 0)

    def test_stopped_search_still_polishes_before_returning(self):
        stop = threading.Event()
        phases = []

        def progress(ev):
            if ev["kind"] == "solution":
                stop.set()   # "save & stop" as soon as there is anything to save
            if ev["kind"] == "phase":
                phases.append(ev["phase"])

        spec = OptimizeSpec(size=(4, 4, 3), time_limit=120, workers=8, polish_time=10)
        sol = optimize(DEFAULT_PROFILE, spec, progress=progress, stop=stop)
        self.assertTrue(any(p.startswith("polish") for p in phases), phases)
        self.assertTrue(sol.report.ok)
        # 32 channels per trunk: a tidy result needs few tunnel-network connections.
        self.assertLessEqual(len(sol.exit_cells), sol.tunnels_validated // 32 + 3)

    def test_polish_time_scales_with_the_box(self):
        from ae2opt.optimize import _Model, _polish_seconds
        small = _Model(OptimizeSpec(size=(3, 3, 3)), DEFAULT_PROFILE, (3, 3, 3))
        big = _Model(OptimizeSpec(size=(7, 7, 7)), DEFAULT_PROFILE, (7, 7, 7))
        self.assertAlmostEqual(_polish_seconds(small.spec, small), 10.4, delta=0.1)
        self.assertEqual(_polish_seconds(big.spec, big), 60.0)
        self.assertEqual(_polish_seconds(OptimizeSpec(polish_time=3), small), 3)


if __name__ == "__main__":
    unittest.main()
