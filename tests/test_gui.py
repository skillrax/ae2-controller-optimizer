import gzip
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from ae2opt import DEFAULT_PROFILE, ProfileStore
from ae2opt.gui.server import analyze, make_server

from .builds import basic_p2p
from .test_cfgimport import UEL_CFG


class GuiServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.server = make_server(0, store=ProfileStore(root / "profiles"), runs_dir=root / "runs")
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.read(), r.headers

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(), r.headers

    def post_json(self, path, body):
        return json.loads(self.post(path, body)[0])

    def test_page_and_scripts_are_served_without_external_resources(self):
        page, headers = self.get("/")
        self.assertIn("text/html", headers["Content-Type"])
        for name in ("app.js", "viewer.js", "scene.js", "style.css"):
            body, _ = self.get(f"/static/{name}")
            self.assertNotIn(b"https://", body, name)   # offline: nothing loaded from the web
        self.assertNotIn(b"https://", page)
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/static/../server.py")

    def test_state_lists_profiles_library_and_test_structures(self):
        from ae2opt import __version__
        state = json.loads(self.get("/api/state")[0])
        self.assertEqual(state["profiles"][0]["name"], DEFAULT_PROFILE.name)
        self.assertEqual(state["limits"]["size_max"], 63)
        self.assertEqual(state["version"], __version__)
        self.assertEqual(state["max_box_side"], 9)
        lib = {e["id"]: e for e in state["library"]}
        self.assertEqual(lib["3x3x3"]["tunnels"], 74)
        ids = {e["id"] for e in state["examples"]}
        self.assertIn("basic_p2p.json", ids)
        self.assertFalse(any(i.startswith("best_") for i in ids))   # those are in the library

    def test_ping_identifies_the_app(self):
        res = json.loads(self.get("/api/ping")[0])
        self.assertEqual(res["app"], "ae2opt")

    def test_library_layout_can_be_rotated_to_the_requested_box(self):
        lay = json.loads(self.get("/api/layout?src=library:3x3x4@4x3x3")[0])
        xs = [b["pos"] for b in lay["blocks"] if b["type"] == "controller"]
        self.assertEqual(max(p[0] for p in xs) - min(p[0] for p in xs) + 1, 4)
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/layout?src=library:3x3x4@5x3x3")

    def test_check_reports_cells_for_tooltips(self):
        res = self.post_json("/api/check", {"layout": basic_p2p().to_dict()})
        self.assertTrue(res["ok"])
        kinds = {c["kind"] for c in res["cells"].values()}
        self.assertIn("controller", kinds)
        tunnel_cells = [c for c in res["cells"].values() if any(p["kind"] == "p2p_me" for p in c.get("parts", {}).values())]
        self.assertTrue(tunnel_cells)
        part = next(p for c in tunnel_cells for p in c["parts"].values())
        self.assertIn(part["state"], ("online", "missing", "offline"))
        self.assertTrue(all("used" in c and "sides" in c for c in tunnel_cells))

    def test_analyze_matches_example_metrics(self):
        from ae2opt import Layout
        lay = Layout.load(Path(__file__).resolve().parents[1] / "examples" / "best_3x3x3.json")
        res = analyze(lay, DEFAULT_PROFILE)
        self.assertEqual(res["metrics"]["working_input_tunnels"], 74)
        controllers = [c for c in res["cells"].values() if c["kind"] == "controller"]
        self.assertEqual(sum(c["tunnels"] for c in controllers), 74)

    def test_bad_layout_is_a_400_not_a_crash(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/check", {"layout": {"blocks": [{"pos": [0, 0, 0], "type": "nonsense"}]}})
        self.assertEqual(ctx.exception.code, 400)

    def test_import_cfg_and_save_delete_profile(self):
        res = self.post_json("/api/import-cfg", {"text": UEL_CFG, "name": "UEL"})
        self.assertTrue(res["recognized"])
        self.assertEqual(res["profile"]["dense_channel_capacity"], 64)
        saved = self.post_json("/api/profiles", {"profile": res["profile"]})
        self.assertIn("UEL", [p["name"] for p in saved["profiles"]])
        left = self.post_json("/api/profiles/delete", {"name": "UEL"})
        self.assertNotIn("UEL", [p["name"] for p in left["profiles"]])
        with self.assertRaises(urllib.error.HTTPError):
            self.post("/api/profiles", {"profile": DEFAULT_PROFILE.to_dict()})   # read-only

    def test_export_returns_a_structure_file(self):
        body, headers = self.post("/api/export", {"layout": basic_p2p().to_dict(), "name": "my build"})
        self.assertIn('filename="my_build.nbt"', headers["Content-Disposition"])
        self.assertEqual(body[:2], b"\x1f\x8b")
        self.assertTrue(gzip.decompress(body))

    def test_guide_is_html(self):
        body, headers = self.post("/api/guide", {"layout": basic_p2p().to_dict(), "title": "T"})
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"<html", body[:200].lower())

    def test_layout_sources(self):
        lay = json.loads(self.get("/api/layout?src=example:basic_p2p.json")[0])
        self.assertTrue(lay["blocks"])
        for src in ("example:../pyproject.toml", "run:../x", "nope:1"):
            with self.assertRaises(urllib.error.HTTPError):
                self.get(f"/api/layout?src={src}")

    def test_run_checkpoint_is_tidied_before_it_is_opened(self):
        try:
            import ortools  # noqa: F401
        except ImportError:
            self.skipTest("needs OR-Tools")
        from ae2opt import runner
        from ae2opt.optimize import OptimizeSpec, layout_from_vector, optimize
        events = []
        spec = OptimizeSpec(size=(3, 3, 3), time_limit=20, workers=8, polish_time=0)
        optimize(DEFAULT_PROFILE, spec, progress=events.append)
        model = next(ev["model"] for ev in events if ev["kind"] == "start")
        raw = layout_from_vector(model, next(ev for ev in events if ev["kind"] == "solution")["vec"])
        run_dir = self.server.app.runs.root / "3x3x3-checkpoint"
        runner.new_run(run_dir, DEFAULT_PROFILE, spec)
        raw.save(run_dir / runner.BEST_FILE)
        data = runner.read_run(run_dir)
        data.update(best_polished=False, status="STOPPED")
        runner.write_run(run_dir, data)

        lay = json.loads(self.get("/api/layout?src=run:3x3x3-checkpoint&wait=1")[0])
        self.assertTrue(lay["polished"])
        tidy = lay["tidy"]
        self.assertGreaterEqual(tidy["after"]["tunnels"], tidy["before"]["tunnels"])
        self.assertLessEqual(tidy["after"]["exits"], tidy["before"]["exits"])
        if tidy["changed"]:   # a stopped run keeps the tidied layout
            self.assertTrue(runner.read_run(run_dir)["best_polished"])

    def test_run_box_cannot_exceed_profile_limit(self):
        from ae2opt.gui.server import _settings_to_spec
        with self.assertRaises(ValueError):
            _settings_to_spec({"box": [8, 1, 1]}, DEFAULT_PROFILE)
        spec = _settings_to_spec({"box": [3, 2, 3], "mode": "prove"}, DEFAULT_PROFILE)
        self.assertEqual(spec.size, (3, 2, 3))
        self.assertEqual(spec.lns_time, 0.0)


class QuitTests(unittest.TestCase):
    def test_quit_stops_the_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_server(0, store=ProfileStore(Path(tmp) / "p"), runs_dir=Path(tmp) / "runs")
            stopped = threading.Event()
            server.app.on_quit = lambda: (stopped.set(), threading.Thread(target=server.shutdown).start())
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/api/quit"
            req = urllib.request.Request(url, b"{}", {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertTrue(json.loads(r.read())["ok"])
            self.assertTrue(stopped.wait(10))
            t.join(10)
            self.assertFalse(t.is_alive())
            server.server_close()


if __name__ == "__main__":
    unittest.main()
