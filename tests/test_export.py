import tempfile
import unittest
from pathlib import Path

from ae2opt import CableAnchor, CableType, DEFAULT_PROFILE, Direction, Layout, P2PTunnel, nbt
from ae2opt.export import ExportError, cable_bus_nbt, save_structure, to_structure
from ae2opt.layout import CableBus

from .builds import basic_p2p

try:
    import ortools  # noqa: F401
    HAVE_ORTOOLS = True
except ImportError:
    HAVE_ORTOOLS = False

D = Direction


def values(lst):
    """Plain numbers from an nbt.List, whether built in Python (ints) or read back (Int)."""
    return [getattr(v, "value", v) for v in lst.items]


class NbtTests(unittest.TestCase):
    def test_round_trip(self):
        root = {"a": nbt.Byte(1), "b": nbt.Short(-2), "c": 7, "d": "text", "e": 1.5,
                "f": nbt.List(nbt.INT, [1, 2, 3]), "g": {"nested": nbt.Long(2 ** 40)},
                "h": nbt.List(nbt.COMPOUND, [{"x": nbt.Int(1)}]), "empty": nbt.List(nbt.COMPOUND, [])}
        back = nbt.loads(nbt.dumps(root))
        self.assertEqual(back["a"], nbt.Byte(1))
        self.assertEqual(back["b"], nbt.Short(-2))
        self.assertEqual(back["c"], nbt.Int(7))
        self.assertEqual(back["d"], "text")
        self.assertEqual(back["e"], nbt.Double(1.5))
        self.assertEqual(values(back["f"]), [1, 2, 3])
        self.assertEqual(back["g"]["nested"], nbt.Long(2 ** 40))
        self.assertEqual(back["h"].items[0]["x"], nbt.Int(1))
        self.assertEqual(back["empty"].items, [])

    def test_gzip_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.nbt"
            nbt.save(path, {"v": nbt.Int(3)})
            self.assertEqual(path.read_bytes()[:2], b"\x1f\x8b")
            self.assertEqual(nbt.load(path)["v"], nbt.Int(3))


class ExportTests(unittest.TestCase):
    def test_cable_bus_parts_and_ids(self):
        bus = CableBus(CableType.NORMAL, "transparent",
                       {D.WEST: P2PTunnel(7), D.UP: CableAnchor(), D.NORTH: P2PTunnel(9, output=True)})
        data = cable_bus_nbt(bus)
        self.assertEqual(data["def:6"]["Damage"], nbt.Short(56))          # fluix smart cable
        self.assertEqual(data["def:4"]["Damage"], nbt.Short(460))         # WEST = side 4: ME P2P
        self.assertEqual(data["extra:4"], {"output": nbt.Byte(0), "freq": nbt.Short(7)})
        self.assertEqual(data["def:1"]["Damage"], nbt.Short(120))         # UP = side 1: anchor
        self.assertEqual(data["extra:2"], {"output": nbt.Byte(1), "freq": nbt.Short(9)})
        self.assertTrue(all(d["id"] == "appliedenergistics2:part" for k, d in data.items() if k.startswith("def:")))

    def test_dense_and_coloured_cable_damage(self):
        self.assertEqual(cable_bus_nbt(CableBus(CableType.DENSE))["def:6"]["Damage"], nbt.Short(76))
        self.assertEqual(cable_bus_nbt(CableBus(CableType.NORMAL, "red"))["def:6"]["Damage"], nbt.Short(54))

    def test_bus_without_cable_has_no_centre(self):
        data = cable_bus_nbt(CableBus(CableType.NONE, parts={D.UP: P2PTunnel(1)}))
        self.assertNotIn("def:6", data)

    def test_structure_covers_every_cell(self):
        root = to_structure(basic_p2p())
        size = values(root["size"])
        self.assertEqual(root["DataVersion"], nbt.Int(1343))
        self.assertEqual(len(root["blocks"].items), size[0] * size[1] * size[2])
        names = [p["Name"] for p in root["palette"].items]
        self.assertIn("appliedenergistics2:controller", names)
        self.assertIn("minecraft:air", names)
        positions = {tuple(values(b["pos"])) for b in root["blocks"].items}
        self.assertEqual(len(positions), len(root["blocks"].items))
        self.assertTrue(all(0 <= p[i] < size[i] for p in positions for i in range(3)))

    def test_rejects_ports_and_oversized(self):
        with self.assertRaises(ExportError):
            to_structure(Layout().port((0, 0, 0)))
        with self.assertRaises(ExportError):
            to_structure(Layout().controller((0, 0, 0)).controller((40, 0, 0)))
        with self.assertRaises(ExportError):
            to_structure(Layout().cable((0, 0, 0), parts={D.UP: P2PTunnel(40000)}))

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = save_structure(basic_p2p(), Path(tmp) / "b.nbt")
            root = nbt.load(Path(tmp) / "b.nbt")
            self.assertEqual(tuple(values(root["size"])), info["size"])


@unittest.skipUnless(HAVE_ORTOOLS, "OR-Tools not installed")
class HarnessTests(unittest.TestCase):
    def test_rig_for_optimized_build(self):
        from ae2opt.harness import build_rig
        from ae2opt.optimize import OptimizeSpec, optimize
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=(2, 2, 2), time_limit=20))
        rig = build_rig("t", sol.harness, (2, 2, 2))
        e = rig.expected
        self.assertTrue(e["valid"], e["errors"])
        self.assertEqual((e["main_network"], e["tunnel_network"], e["same_network"]), ("ONLINE", "ONLINE", False))
        self.assertEqual(e["input_tunnels_active"], e["input_tunnels"])
        self.assertEqual(e["input_tunnels"], sol.tunnels_validated - (1 if rig.removed_tunnel else 0))
        self.assertEqual(e["tunnel_network_channels"], e["input_tunnels"] + 2)
        self.assertEqual([r["active"] for r in e["rigs"]], [32, 32])
        self.assertEqual(e["main_network_channels"], 64)
        to_structure(rig.layout)  # exportable

        # Collector: the roots' WAILA readings add up to every tunnel-network channel,
        # no root is over capacity, and no collector cable touches a main controller.
        self.assertEqual(e["probe_roots_total"], e["tunnel_network_channels"])
        self.assertTrue(all(r["channels"] <= 32 for r in e["probe_roots"]))
        self.assertEqual([r["trunk_channels"] for r in e["rigs"]], [32, 32])
        from ae2opt.harness import _layer
        main = {p for p in rig.layout.controllers() if _layer(p, (2, 2, 2)) == 0}
        collector = [p for p, b in rig.layout.blocks.items() if b.kind == "cable" and _layer(p, (2, 2, 2)) >= 2]
        for p in collector:
            for d in D:
                n = (p[0] + d.offset[0], p[1] + d.offset[1], p[2] + d.offset[2])
                self.assertNotIn(n, main)

    def test_existing_builds_are_reused(self):
        from ae2opt.ingame import _existing_build
        from ae2opt.optimize import OptimizeSpec, optimize
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=(2, 2, 2), time_limit=20))
        with tempfile.TemporaryDirectory() as tmp:
            sol.harness.save(Path(tmp) / "ae2opt_t1_build.json")
            build = _existing_build(Path(tmp), "T1", (2, 2, 2))
            self.assertEqual(set(build.controllers()), set(sol.layout.controllers()))
            self.assertIsNone(_existing_build(Path(tmp), "T2", (3, 1, 3)))

    def test_negative_tests_fail_as_designed(self):
        from ae2opt.harness import build_rig, standalone_rig
        from ae2opt.ingame import capacity_overload, uncapped_face
        from ae2opt.optimize import OptimizeSpec, optimize
        n1 = standalone_rig("N1", capacity_overload())
        self.assertEqual((n1["input_tunnels"], n1["input_tunnels_active"]), (9, 8))
        sol = optimize(DEFAULT_PROFILE, OptimizeSpec(size=(2, 2, 2), time_limit=20))
        t1 = build_rig("T1", sol.harness, (2, 2, 2))
        n2 = standalone_rig("N2", uncapped_face(t1))
        self.assertEqual(n2["networks"], ["CONFLICT"])
        self.assertEqual(n2["input_tunnels_active"], 0)


if __name__ == "__main__":
    unittest.main()
