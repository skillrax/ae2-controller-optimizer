import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from ae2opt.__main__ import main

from .builds import basic_p2p
from .test_cfgimport import UEL_CFG


def run(*argv) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(argv))
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def test_check_valid_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "build.json"
            basic_p2p().save(path)
            code, out = run("check", str(path))
            self.assertEqual(code, 0)
            self.assertIn("VALID build", out)
            self.assertIn("dense=32", out)

    def test_check_with_cfg_and_manual_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout, cfg = Path(tmp) / "build.json", Path(tmp) / "ae2.cfg"
            basic_p2p().save(layout)
            cfg.write_text(UEL_CFG, "utf-8")
            code, out = run("check", str(layout), "--cfg", str(cfg), "--normal", "12")
            self.assertEqual(code, 0)
            self.assertIn("normal=12 dense=64 size=9x5x9", out)

    def test_import_cfg_lists_field_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "ae2.cfg"
            cfg.write_text("I:denseChannelCapacity=64\n", "utf-8")
            code, out = run("import-cfg", str(cfg))
            self.assertEqual(code, 0)
            self.assertIn("from file", out)
            self.assertIn("default (key not found)", out)

    def test_optimize_small_box_and_show(self):
        try:
            import ortools  # noqa: F401
        except ImportError:
            self.skipTest("OR-Tools not installed")
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "best.json"
            code, out = run("optimize", "--box", "2", "1", "1", "--time", "10", "--out", str(out_path))
            self.assertEqual(code, 0, out)
            self.assertIn("10 working tunnels = 320 channels", out)
            code, shown = run("show", str(out_path))
            self.assertEqual(code, 0)
            self.assertIn("# #", shown)

    def test_invalid_layout_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "build.json"
            lay = basic_p2p()
            lay.controller((4, 0, 0), powered=False)
            lay.save(path)
            code, out = run("check", str(path))
            self.assertEqual(code, 1)
            self.assertIn("R11", out)


if __name__ == "__main__":
    unittest.main()
