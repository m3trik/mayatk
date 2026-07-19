# coding=utf-8
"""Shot Manifest behavior template schema + discovery tests.

Maya-free (``_behaviors`` guards ``cmds``), but kept under the mayatk test tree
and run via mayapy alongside the rest of the suite.  Templates are JSON (the
pythontk engine's store, shared with blendertk).

    & $MAYAPY mayatk\\test\\test_shot_manifest_behaviors.py
"""
import json
import tempfile
import unittest
from pathlib import Path

from mayatk.anim_utils.shots.shot_manifest.behaviors import (
    BehaviorSpec,
    load_behavior,
    list_behaviors,
    apply_to_shots,
)
from mayatk.anim_utils.shots._shots import ShotBlock


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
            (Path(d) / "custom.json").write_text(
                json.dumps({"description": "a custom one", "kind": ["scene"]}),
                encoding="utf-8",
            )
            self.assertEqual(list_behaviors(search_path=Path(d)), ["custom"])
            self.assertEqual(
                load_behavior("custom", Path(d))["description"], "a custom one"
            )


class ApplyToShotsDispatchTest(unittest.TestCase):
    """Signature adaptation in ``apply_to_shots``.

    Regression: the old adapters probed callables by calling them inside
    ``except TypeError``, so a genuine TypeError raised *inside* a modern
    applier was misread as "legacy signature", silently re-invoked the
    applier with reduced arguments, and could report the entry as applied.
    """

    def _shot(self, behaviors):
        shot = ShotBlock(shot_id=1, name="A01", start=0, end=30)
        shot.metadata["behaviors"] = behaviors
        return shot

    def test_legacy_four_arg_apply_fn_still_supported(self):
        calls = []

        def apply_fn(obj, behavior, start, end):
            calls.append((obj, behavior, start, end))

        result = apply_to_shots(
            [self._shot([{"name": "cube", "behavior": "fade_in"}])],
            apply_fn,
            exists_fn=lambda name: True,
            has_keys_fn=lambda name, s, e: False,
        )
        self.assertEqual(calls, [("cube", "fade_in", 0, 30)])
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(result["failed"], [])

    def test_internal_typeerror_is_a_failure_not_a_retry(self):
        calls = []

        def apply_fn(obj, behavior, start, end, source_path="", anchor_override=None):
            calls.append(obj)
            raise TypeError("boom from inside the applier")

        result = apply_to_shots(
            [
                self._shot(
                    [
                        {
                            "name": "clip",
                            "behavior": "set_clip",
                            "kind": "audio",
                            "source_path": "x.wav",
                        }
                    ]
                )
            ],
            apply_fn,
            exists_fn=lambda name, entry=None: True,
            has_keys_fn=lambda name, s, e, entry=None: False,
        )
        # One invocation only — no silent reduced-signature retry — and
        # the entry lands in "failed", never "applied".
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertIn("boom", result["failed"][0]["error"])

    def test_source_only_apply_fn_still_receives_source_path(self):
        """An applier with ``source_path`` but no ``anchor_override`` must
        get the source path in the audio pass (middle dispatch tier)."""
        calls = []

        def apply_fn(obj, behavior, start, end, source_path=""):
            calls.append(source_path)

        result = apply_to_shots(
            [
                self._shot(
                    [
                        {
                            "name": "clip",
                            "behavior": "set_clip",
                            "kind": "audio",
                            "source_path": "x.wav",
                        }
                    ]
                )
            ],
            apply_fn,
            exists_fn=lambda name, entry=None: True,
            has_keys_fn=lambda name, s, e, entry=None: False,
        )
        self.assertEqual(calls, ["x.wav"])
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(result["failed"], [])

    def test_legacy_exists_fn_internal_typeerror_propagates_unmasked(self):
        def exists_fn(name, entry):
            raise TypeError("real bug in exists_fn")

        with self.assertRaises(TypeError) as ctx:
            apply_to_shots(
                [self._shot([{"name": "cube", "behavior": "fade_in"}])],
                lambda o, b, s, e: None,
                exists_fn=exists_fn,
                has_keys_fn=lambda name, s, e: False,
            )
        self.assertIn("real bug", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
