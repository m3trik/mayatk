# coding=utf-8
"""Shot Manifest behavior template schema + discovery tests.

Maya-free (``_behaviors`` guards ``cmds``), but kept under the mayatk test tree
and run via mayapy alongside the rest of the suite. Requires PyYAML.

    & $MAYAPY mayatk\\test\\test_shot_manifest_behaviors.py
"""
import tempfile
import unittest
from pathlib import Path

from mayatk.anim_utils.shots.shot_manifest.behaviors import (
    BehaviorSpec,
    load_behavior,
    list_behaviors,
)


class BehaviorSpecTest(unittest.TestCase):
    def test_shipped_behaviors_validate_clean(self):
        for name in ("fade_in", "fade_out", "set_clip"):
            res = BehaviorSpec.validate(load_behavior(name))
            self.assertTrue(res.ok, f"{name}: errors={res.errors}")

    def test_skeleton_is_valid(self):
        self.assertTrue(BehaviorSpec.validate(BehaviorSpec.skeleton()).ok)

    def test_bad_verify_mode_is_error(self):
        self.assertFalse(BehaviorSpec.validate({"verify": {"mode": "nope"}}).ok)

    def test_from_source_duration_ok_but_bad_string_errors(self):
        self.assertTrue(BehaviorSpec.validate({"duration": "from_source"}).ok)
        self.assertTrue(BehaviorSpec.validate({"duration": 30}).ok)
        self.assertFalse(BehaviorSpec.validate({"duration": "later"}).ok)

    def test_bad_attributes_structure_is_error(self):
        res = BehaviorSpec.validate({"attributes": {"visibility": {"bad_phase": {}}}})
        self.assertFalse(res.ok)


class BehaviorDiscoveryTest(unittest.TestCase):
    def test_list_includes_builtins(self):
        names = list_behaviors()
        for n in ("fade_in", "fade_out", "set_clip"):
            self.assertIn(n, names)

    def test_kind_filter(self):
        self.assertIn("set_clip", list_behaviors(kind="audio"))
        self.assertNotIn("fade_in", list_behaviors(kind="audio"))
        self.assertIn("fade_in", list_behaviors(kind="scene"))

    def test_search_path_override_is_single_tier(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "custom.yaml").write_text(
                "description: a custom one\nkind: [scene]\n", encoding="utf-8"
            )
            self.assertEqual(list_behaviors(search_path=Path(d)), ["custom"])
            self.assertEqual(
                load_behavior("custom", Path(d))["description"], "a custom one"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
