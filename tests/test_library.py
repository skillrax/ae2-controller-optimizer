import sys
import unittest
from pathlib import Path
from unittest import mock

from ae2opt import DEFAULT_PROFILE, check, library
from ae2opt.optimize import MAX_BOX_SIDE, MODEL_VERSION, OptimizeSpec, check_box, optimize
from ae2opt.profile import Profile
from ae2opt.rules import Report


def working(report: Report) -> int:
    return report.metrics["working_input_tunnels"]


class LibraryTests(unittest.TestCase):
    def test_every_entry_loads_and_checks_as_recorded(self):
        self.assertTrue(library.entries(), "the shipped library is empty")
        for e in library.entries():
            with self.subTest(e["id"]):
                self.assertEqual(e["rules"]["model"], MODEL_VERSION)
                report = check(library.load(e["id"]), DEFAULT_PROFILE)
                self.assertTrue(report.ok, report.format())
                self.assertEqual(working(report), e["tunnels"])
                self.assertLessEqual(e["tunnels"], e["upper"])
                if e["status"] == "proven":
                    self.assertEqual(e["tunnels"], e["upper"])

    def test_match_needs_the_same_rules(self):
        e = library.match(DEFAULT_PROFILE, (3, 3, 3))
        self.assertIsNotNone(e)
        self.assertEqual(e["tunnels"], 74)
        self.assertIsNone(library.match(DEFAULT_PROFILE.with_values(dense_channel_capacity=64), (3, 3, 3)))
        self.assertIsNone(library.match(DEFAULT_PROFILE, (3, 3, 3), anchor_free=False))
        self.assertIsNone(library.match(DEFAULT_PROFILE, (3, 3, 3), exterior=False))

    def test_rotated_box_uses_the_same_entry_with_axes_swapped(self):
        e = library.match(DEFAULT_PROFILE, (4, 3, 3))
        self.assertIsNotNone(e)
        self.assertEqual(sorted(e["box"]), [3, 3, 4])
        lay = library.load(e["id"], (4, 3, 3))
        xs = lay.controllers()
        spans = [max(c[i] for c in xs) - min(c[i] for c in xs) + 1 for i in range(3)]
        self.assertTrue(all(s <= b for s, b in zip(spans, (4, 3, 3))), spans)
        report = check(lay, DEFAULT_PROFILE)
        self.assertTrue(report.ok, report.format())
        self.assertEqual(working(report), e["tunnels"])


class GuardTests(unittest.TestCase):
    def test_boxes_over_the_optimizer_limit_are_refused(self):
        big = Profile(name="UEL max", max_controller_size=(63, 63, 63))
        check_box((MAX_BOX_SIDE,) * 3, big)
        with self.assertRaises(ValueError) as ctx:
            check_box((MAX_BOX_SIDE + 1, 3, 3), big)
        self.assertIn("too big to optimize", str(ctx.exception))
        with self.assertRaises(ValueError):
            optimize(big, OptimizeSpec(size=(20, 20, 20), time_limit=1))

    def test_box_over_profile_limit_is_refused(self):
        with self.assertRaises(ValueError):
            check_box((8, 1, 1), DEFAULT_PROFILE)


class FrozenWorkerTests(unittest.TestCase):
    def test_packaged_app_starts_itself_as_the_worker(self):
        from ae2opt.runner import worker_command
        run = Path("runs/x")
        self.assertEqual(worker_command(run)[1:3], ["-m", "ae2opt"])
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(worker_command(run), [sys.executable, "_worker", str(run)])

    def test_no_arguments_opens_the_app(self):
        from ae2opt import __main__ as cli
        with mock.patch("ae2opt.gui.serve", return_value=0) as serve:
            self.assertEqual(cli.main([]), 0)
        serve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
