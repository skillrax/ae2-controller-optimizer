import io
import json
import math
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ae2opt import DEFAULT_PROFILE, Layout, check
from ae2opt.patterns import canonical, exposed_faces, is_valid_shape

try:
    import ortools  # noqa: F401
    from ae2opt import runner
    from ae2opt.optimize import OptimizeSpec, _Model, optimize
    HAVE_ORTOOLS = True
except ImportError:
    HAVE_ORTOOLS = False


@unittest.skipUnless(HAVE_ORTOOLS, "OR-Tools not installed")
class CanonicalTests(unittest.TestCase):
    def test_canonical_shape_meets_symmetry_rules(self):
        size = (4, 4, 3)
        model = _Model(OptimizeSpec(size=size), DEFAULT_PROFILE, size)
        shapes = [
            frozenset({(3, 3, 2), (3, 2, 2), (2, 2, 2), (2, 2, 1)}),
            frozenset({(1, 3, 0), (1, 3, 1), (1, 3, 2), (0, 3, 2), (2, 3, 0)}),
            frozenset({(3, 0, 0)}),
        ]
        for xs in shapes:
            with self.subTest(xs=sorted(xs)):
                c = canonical(xs, size)
                self.assertEqual(len(c), len(xs))
                self.assertEqual(exposed_faces(c), exposed_faces(xs))
                self.assertEqual(is_valid_shape(c), is_valid_shape(xs))
                self.assertTrue(all(0 <= p[a] < size[a] for p in c for a in range(3)))
                self.assertTrue(model.satisfies_symmetry(c))

    def test_spec_round_trip(self):
        spec = OptimizeSpec(size=(4, 4, 4), time_limit=math.inf, lns_time=math.inf,
                            forbidden=frozenset({(0, 0, 0)}), start=frozenset({(1, 1, 1), (1, 2, 1)}),
                            solver_params=(("linearization_level", 2),))
        d = json.loads(json.dumps(runner.spec_to_dict(spec)))
        self.assertEqual(runner.spec_from_dict(d), spec)


@unittest.skipUnless(HAVE_ORTOOLS, "OR-Tools not installed")
class ProgressTests(unittest.TestCase):
    def test_events_are_emitted(self):
        events = []
        optimize(DEFAULT_PROFILE, OptimizeSpec(size=(3, 2, 1), time_limit=20), progress=events.append)
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds[0], "start")
        self.assertIn("solution", kinds)
        self.assertEqual(kinds[-1], "done")
        sols = [e for e in events if e["kind"] == "solution"]
        self.assertTrue(all("vec" in e and e["tunnels"] > 0 for e in sols))
        self.assertEqual(sols[-1]["tunnels"], 22)  # brute-force optimum for 3x2x1

    def test_stop_returns_best_so_far(self):
        stop = threading.Event()

        def progress(ev):
            if ev["kind"] == "solution" and ev["tunnels"] >= 100:
                stop.set()

        t = time.monotonic()
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=(5, 5, 5), time_limit=math.inf, workers=4),
                       progress=progress, stop=stop)
        self.assertLess(time.monotonic() - t, 60)
        self.assertGreaterEqual(sol.tunnels_validated, 100)
        self.assertTrue(sol.report.ok)
        self.assertIn("stopped early", " ".join(sol.notes))

    def test_known_upper_bound_proves_optimality(self):
        # No main solve: a start layout that meets a bound proven earlier is optimal.
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=(1, 1, 1), time_limit=0,
                                                     start=frozenset({(0, 0, 0)}), known_upper=6))
        self.assertEqual((sol.tunnels_validated, sol.status, sol.upper_bound), (6, "OPTIMAL", 6))


class PanelTests(unittest.TestCase):
    def test_panel_shows_progress_and_gap(self):
        p = runner.Panel("5x5x5", 32, 3600, 0.0, None) if HAVE_ORTOOLS else None
        if p is None:
            self.skipTest("OR-Tools not installed")
        p.apply({"kind": "phase", "phase": "main solve"}, 0)
        p.apply({"kind": "solution", "tunnels": 290, "controllers": 75}, 10)
        p.apply({"kind": "bound", "tunnels": 320}, 11)
        p.apply({"kind": "solution", "tunnels": 288, "controllers": 70}, 12)   # worse: ignored
        text = "\n".join(p.lines(active=125, now=20))
        self.assertIn("290 tunnels = 9,280 channels · 75 controllers", text)
        self.assertIn("320 tunnels   gap 9.4%", text)
        self.assertIn("00:02:05", text)
        self.assertIn("10s ago", text)
        self.assertIn("main solve", text)


@unittest.skipUnless(HAVE_ORTOOLS, "OR-Tools not installed")
class RunFolderTests(unittest.TestCase):
    def test_prove_run_finishes_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            runner.new_run(run_dir, DEFAULT_PROFILE, OptimizeSpec(size=(3, 2, 1), time_limit=math.inf, workers=4))
            # A cp1252 stream, like a redirected Windows console: no bar glyphs.
            raw = io.BytesIO()
            stream = io.TextIOWrapper(raw, encoding="cp1252")
            code = runner.control(run_dir, None, low_priority=True, out=stream)
            stream.flush()
            out = io.StringIO(raw.getvalue().decode("cp1252"))
            self.assertEqual(code, 0, out.getvalue())
            data = runner.read_run(run_dir)
            self.assertEqual((data["status"], data["best_tunnels"], data["best_upper"]), ("OPTIMAL", 22, 22))
            self.assertTrue(check(Layout.load(run_dir / runner.BEST_FILE)).ok)
            self.assertIn("22 working tunnels", out.getvalue())
            with self.assertRaises(FileExistsError):
                runner.new_run(run_dir, DEFAULT_PROFILE, OptimizeSpec())

    def test_budget_stop_then_resume_keeps_best(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            spec = OptimizeSpec(size=(4, 4, 4), time_limit=20, lns_time=math.inf, lns_step=3, workers=4)
            runner.new_run(run_dir, DEFAULT_PROFILE, spec)
            runner.control(run_dir, 30 / 3600, low_priority=True, out=io.StringIO())
            first = runner.read_run(run_dir)
            self.assertEqual(first["status"], "STOPPED")
            self.assertIsNotNone(first["best_key"])
            self.assertGreaterEqual(first["active_seconds"], 25)

            runner.prepare_resume(run_dir)
            resumed = runner.spec_from_dict(runner.read_run(run_dir)["spec"])
            self.assertIsNotNone(resumed.start)
            out = io.StringIO()
            runner.control(run_dir, 25 / 3600, low_priority=True, out=out)
            second = runner.read_run(run_dir)
            self.assertEqual(second["sessions"], 2)
            self.assertGreaterEqual(second["best_key"], first["best_key"])
            self.assertGreater(second["active_seconds"], first["active_seconds"])
            best = Layout.load(run_dir / runner.BEST_FILE)
            self.assertTrue(check(best).ok)
            self.assertEqual(len(best.controllers()), -second["best_key"][1])

    def test_suspend_and_resume_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            runner.new_run(run_dir, DEFAULT_PROFILE,
                           OptimizeSpec(size=(4, 4, 4), time_limit=math.inf, workers=2))
            proc = runner.start_worker(run_dir, low_priority=True)
            try:
                time.sleep(3)
                runner._suspend(proc)
                size_before = (run_dir / runner.PROGRESS_FILE).stat().st_size \
                    if (run_dir / runner.PROGRESS_FILE).exists() else 0
                time.sleep(3)
                self.assertIsNone(proc.poll())  # frozen, not dead
                size_frozen = (run_dir / runner.PROGRESS_FILE).stat().st_size \
                    if (run_dir / runner.PROGRESS_FILE).exists() else 0
                self.assertEqual(size_before, size_frozen)  # nothing happens while frozen
                runner._resume(proc)
                (run_dir / runner.STOP_FILE).touch()
                self.assertEqual(proc.wait(timeout=120), 0)
            finally:
                if proc.poll() is None:
                    runner._resume(proc)
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
