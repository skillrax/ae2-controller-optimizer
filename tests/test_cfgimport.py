import json
import tempfile
import unittest
from pathlib import Path

from ae2opt.cfgimport import DEFAULT, FROM_FILE, UNPARSABLE, import_file, import_text

UEL_CFG = """\
# Configuration file

general {
    # Max channel number may not exceed 256
    I:denseChannelCapacity=64
    I:normalChannelCapacity=16
    S:someList <
        minecraft:stone
        normalChannelCapacity=999
     >
}

ControllerSize {
    I:maxControllerSizeX=9
    I:maxControllerSizeY=5
    I:maxControllerSizeZ=9
}

features {
    networkfeatures {
        B:Channels=true
    }
}
"""

RV6_CFG = """\
general {
    I:craftingCalculationTimePerTick=5
}
features {
    networkfeatures {
        B:Channels=true
        B:QuartzFiber=true
    }
}
"""


class CfgImportTests(unittest.TestCase):
    def test_uel_config(self):
        r = import_text(UEL_CFG, "pack")
        self.assertTrue(r.recognized)
        p = r.profile
        self.assertEqual((p.normal_channel_capacity, p.dense_channel_capacity), (16, 64))
        self.assertEqual(p.max_controller_size, (9, 5, 9))
        self.assertTrue(p.channels_enabled)
        self.assertTrue(all(f.status == FROM_FILE for f in r.fields.values()))
        self.assertIn("ControllerSize", r.fields["size_x"].location)

    def test_list_contents_are_ignored(self):
        r = import_text(UEL_CFG)
        self.assertEqual(r.fields["normal_channel_capacity"].value, 16)
        self.assertFalse(any("Conflicting" in w for w in r.warnings))

    def test_rv6_config_without_uel_keys_uses_defaults(self):
        r = import_text(RV6_CFG)
        self.assertTrue(r.recognized)  # Channels flag was found
        self.assertEqual(r.fields["normal_channel_capacity"].status, DEFAULT)
        self.assertEqual(r.profile.dense_channel_capacity, 32)
        self.assertEqual(r.profile.max_controller_size, (7, 7, 7))

    def test_moved_category_and_quoted_keys(self):
        text = 'somefork {\n  nested {\n    I:"denseChannelCapacity"=48\n  }\n}\n'
        r = import_text(text)
        self.assertEqual(r.profile.dense_channel_capacity, 48)
        self.assertEqual(r.fields["dense_channel_capacity"].status, FROM_FILE)

    def test_single_size_key_fills_missing_axes(self):
        r = import_text("maxControllerSize = 11\nmaxControllerSizeY = 3\n")
        self.assertEqual(r.profile.max_controller_size, (11, 3, 11))

    def test_unrecognized_file(self):
        r = import_text("this is not a config\nfoo=bar\n")
        self.assertFalse(r.recognized)
        self.assertEqual(r.profile.max_controller_size, (7, 7, 7))
        self.assertIn("No AE2", r.summary)

    def test_unparsable_value_falls_back(self):
        r = import_text("general {\n I:denseChannelCapacity=lots\n}\n")
        self.assertEqual(r.fields["dense_channel_capacity"].status, UNPARSABLE)
        self.assertEqual(r.profile.dense_channel_capacity, 32)
        self.assertTrue(r.warnings)

    def test_zero_value_falls_back(self):
        r = import_text("I:normalChannelCapacity=0\n")
        self.assertEqual(r.profile.normal_channel_capacity, 8)
        self.assertEqual(r.fields["normal_channel_capacity"].status, UNPARSABLE)

    def test_conflicting_values_warn(self):
        r = import_text("a {\n I:denseChannelCapacity=64\n}\nb {\n I:denseChannelCapacity=128\n}\n")
        self.assertEqual(r.profile.dense_channel_capacity, 64)
        self.assertTrue(any("Conflicting" in w for w in r.warnings))

    def test_channels_disabled(self):
        r = import_text("B:Channels=false\n")
        self.assertFalse(r.profile.channels_enabled)

    def test_json_config(self):
        text = json.dumps({"channels": {"normalChannelCapacity": 12, "dense_channel_capacity": 40},
                           "controller": {"maxControllerSizeX": 8}})
        r = import_text(text)
        self.assertEqual((r.profile.normal_channel_capacity, r.profile.dense_channel_capacity), (12, 40))
        self.assertEqual(r.profile.max_controller_size, (8, 7, 7))

    def test_import_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AppliedEnergistics2.cfg"
            path.write_text(UEL_CFG, "utf-8")
            r = import_file(path)
            self.assertEqual(r.profile.name, "Imported: AppliedEnergistics2.cfg")
            self.assertEqual(r.profile.dense_channel_capacity, 64)


if __name__ == "__main__":
    unittest.main()
