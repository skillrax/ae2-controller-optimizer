import tempfile
import unittest

from ae2opt import DEFAULT_PROFILE, Profile, ProfileError, ProfileStore


class ProfileTests(unittest.TestCase):
    def test_defaults_work_out_of_the_box(self):
        p = DEFAULT_PROFILE
        self.assertEqual((p.normal_channel_capacity, p.dense_channel_capacity), (8, 32))
        self.assertEqual(p.max_controller_size, (7, 7, 7))
        self.assertTrue(p.channels_enabled)
        self.assertEqual(p.warnings(), [])

    def test_rejects_meaningless_values(self):
        for bad in ({"normal_channel_capacity": 0}, {"dense_channel_capacity": -4},
                    {"max_controller_size": (7, 0, 7)}, {"normal_channel_capacity": "8"},
                    {"normal_channel_capacity": True}, {"max_controller_size": (7, 7)}):
            with self.subTest(bad=bad), self.assertRaises(ProfileError):
                Profile(**bad)

    def test_unusual_values_warn_but_are_allowed(self):
        p = Profile(name="odd fork", normal_channel_capacity=300, dense_channel_capacity=16,
                    max_controller_size=(80, 7, 7))
        w = " ".join(p.warnings())
        self.assertIn("256", w)
        self.assertIn("63", w)
        self.assertIn("higher than dense", w)

    def test_channels_disabled_warns(self):
        self.assertTrue(Profile(channels_enabled=False).warnings())

    def test_dict_round_trip(self):
        p = Profile(name="UEL 16/64", normal_channel_capacity=16, dense_channel_capacity=64,
                    max_controller_size=(9, 9, 9), source="manual")
        self.assertEqual(Profile.from_dict(p.to_dict()), p)

    def test_store_save_list_get_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(tmp)
            self.assertEqual(store.list(), [DEFAULT_PROFILE])
            p = Profile(name="My pack", dense_channel_capacity=64)
            store.save(p)
            self.assertEqual(store.get("My pack"), p)
            self.assertEqual([x.name for x in store.list()], [DEFAULT_PROFILE.name, "My pack"])
            store.delete("My pack")
            with self.assertRaises(KeyError):
                store.get("My pack")

    def test_store_default_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(tmp)
            with self.assertRaises(ProfileError):
                store.save(DEFAULT_PROFILE.with_values(dense_channel_capacity=64))
            with self.assertRaises(ProfileError):
                store.delete(DEFAULT_PROFILE.name)
            self.assertIs(store.get(DEFAULT_PROFILE.name), DEFAULT_PROFILE)


if __name__ == "__main__":
    unittest.main()
