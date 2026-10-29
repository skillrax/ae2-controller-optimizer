import unittest

from lab.__main__ import run

# Boxes each model proves in a few seconds. The simulator only shows a build works; a model
# that wrongly forbids builds would prove a lower optimum, and only the other model, written
# without reading it, would notice (docs/INDEPENDENT_CHECK.md).
BOXES = ((1, 1, 3), (1, 2, 3), (2, 2, 2), (1, 3, 3), (2, 2, 3), (2, 2, 4), (2, 3, 3), (2, 3, 4))


class AgreementTests(unittest.TestCase):
    def test_both_models_prove_the_library_optima(self):
        for box in BOXES:
            got = {m: run(box, m, seconds=60, workers=8, log=False) for m in ("app", "independent")}
            for m, rec in got.items():
                with self.subTest(box=box, model=m):
                    self.assertTrue(rec["proven"], rec)
                    self.assertTrue(rec["sim_ok"], rec)
                    self.assertEqual(rec["T"], rec["library"]["tunnels"])


if __name__ == "__main__":
    unittest.main()
