import unittest

from ae2opt import DEFAULT_PROFILE, Layout, nbt
from ae2opt.export import to_structure
from ae2opt.ingame import _tunnel_states, colour_test


class ColourTests(unittest.TestCase):
    def test_dyed_group_is_cut_off(self):
        states = _tunnel_states(colour_test("orange"), DEFAULT_PROFILE)
        self.assertEqual(states["A"], {"online": 8, "missing": 0, "offline": 0})
        self.assertEqual(states["B"], {"online": 0, "missing": 0, "offline": 8})

    def test_fluix_group_merges_and_overloads(self):
        states = _tunnel_states(colour_test("transparent"), DEFAULT_PROFILE)
        self.assertEqual(states["A"]["online"] + states["B"]["online"], 8)
        self.assertEqual(states["A"]["missing"] + states["B"]["missing"], 8)

    def test_same_dye_also_merges(self):
        states = _tunnel_states(colour_test("white"), DEFAULT_PROFILE)
        self.assertEqual(states["A"]["online"] + states["B"]["online"], 8)

    def test_markers_export_as_vanilla_blocks(self):
        root = to_structure(colour_test("orange"))
        names = {p["Name"] for p in root["palette"].items}
        self.assertTrue({"minecraft:gold_block", "minecraft:lapis_block"} <= names)
        lay = Layout.from_dict(colour_test("orange").to_dict())
        self.assertEqual(lay.blocks, colour_test("orange").blocks)


if __name__ == "__main__":
    unittest.main()
