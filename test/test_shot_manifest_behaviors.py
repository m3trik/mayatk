# coding=utf-8
"""Shot Manifest behavior template schema + discovery tests, the effect
recipe's round trip through a real scene, and how a doc name finds its node.

The schema and dispatch tests are Maya-free (``_behaviors`` guards ``cmds``);
:class:`RecipeRoundTripTest` and :class:`MemberResolutionTest` build in a live
scene. All run via mayapy alongside the rest of the suite.  Templates are JSON
(the pythontk engine's store, shared with blendertk).

    & $MAYAPY mayatk\\test\\test_shot_manifest_behaviors.py
"""

import json
import tempfile
import unittest
from pathlib import Path

import maya.cmds as cmds

from base_test import MayaTkTestCase, make_temp_wav
from mayatk.anim_utils.shots.shot_manifest.behaviors import (
    Behaviors,
    BehaviorSpec,
)
from mayatk.anim_utils.shots._shots import ShotBlock, ShotStore
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    BuilderObject,
    BuilderStep,
    ShotManifest,
)
from mayatk.audio_utils._audio_utils import AudioUtils


class BehaviorSpecTest(unittest.TestCase):
    def test_shipped_behaviors_validate_clean(self):
        for name in ("fade_in", "fade_out", "set_clip"):
            res = BehaviorSpec.validate(Behaviors.load_behavior(name))
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
        names = Behaviors.list_behaviors()
        for n in ("fade_in", "fade_out", "set_clip"):
            self.assertIn(n, names)

    def test_kind_filter(self):
        self.assertIn("set_clip", Behaviors.list_behaviors(kind="audio"))
        self.assertNotIn("fade_in", Behaviors.list_behaviors(kind="audio"))
        self.assertIn("fade_in", Behaviors.list_behaviors(kind="scene"))

    def test_search_path_override_is_single_tier(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "custom.json").write_text(
                json.dumps({"description": "a custom one", "kind": ["scene"]}),
                encoding="utf-8",
            )
            self.assertEqual(Behaviors.list_behaviors(search_path=Path(d)), ["custom"])
            self.assertEqual(
                Behaviors.load_behavior("custom", Path(d))["description"],
                "a custom one",
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

        result = Behaviors.apply_to_shots(
            [self._shot([{"name": "cube", "behavior": "fade_in"}])],
            apply_fn,
            exists_fn=lambda name: True,
            has_keys_fn=lambda name, s, e: False,
        )
        self.assertEqual(calls, [("cube", "fade_in", 0, 30)])
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(result["failed"], [])

    def test_a_highlight_beside_a_fade_keeps_its_own_anchors(self):
        """Bug: an object's behaviors were spread 0.0 .. 1.0 over the shot
        whatever they were, so a highlight beside a fade had both of its ramps
        forced to anchor 0.0 -- collapsed onto the same frames, glowing for the
        whole timeline before the shot.  Points still spread in doc order.
        Fixed: 2026-10-03
        """
        anchors = {}

        def apply_fn(obj, behavior, start, end, source_path="", anchor_override=None):
            anchors[(obj, behavior)] = anchor_override

        Behaviors.apply_to_shots(
            [
                self._shot(
                    [
                        {"name": "door", "behavior": "highlight"},
                        {"name": "door", "behavior": "fade_out"},
                        {"name": "lid", "behavior": "fade_in"},
                        {"name": "lid", "behavior": "fade_out"},
                    ]
                )
            ],
            apply_fn,
            exists_fn=lambda name, entry=None: True,
            has_keys_fn=lambda name, s, e, entry=None: False,
        )
        self.assertEqual(
            anchors,
            {
                ("door", "highlight"): None,
                ("door", "fade_out"): None,
                ("lid", "fade_in"): 0.0,
                ("lid", "fade_out"): 1.0,
            },
        )

    def test_internal_typeerror_is_a_failure_not_a_retry(self):
        calls = []

        def apply_fn(obj, behavior, start, end, source_path="", anchor_override=None):
            calls.append(obj)
            raise TypeError("boom from inside the applier")

        result = Behaviors.apply_to_shots(
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

        result = Behaviors.apply_to_shots(
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
            Behaviors.apply_to_shots(
                [self._shot([{"name": "cube", "behavior": "fade_in"}])],
                lambda o, b, s, e: None,
                exists_fn=exists_fn,
                has_keys_fn=lambda name, s, e: False,
            )
        self.assertIn("real bug", str(ctx.exception))


class RecipeRoundTripTest(MayaTkTestCase):
    """The scene's effect recipe through a real build: Build keys the recipe,
    a recipe change reads as stale until a Build re-keys it, and a behavior
    the doc drops takes its keys with it -- never the animator's."""

    RANGE = {"A01": (0.0, 240.0)}

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")  # 24 fps
        ShotStore._active = None
        self.store = ShotStore()
        self.door = cmds.polyCube(name="door")[0]

    def tearDown(self):
        ShotStore._active = None
        super().tearDown()

    @staticmethod
    def _steps(*objects):
        """One step, A01, listing ``(name, [behaviors], kind)`` objects."""
        step = BuilderStep(
            step_id="A01", section="A", section_title="Sec", description="d"
        )
        for name, behaviors, kind in objects:
            step.objects.append(
                BuilderObject(name=name, behaviors=list(behaviors), kind=kind)
            )
        return [step]

    def _build(self, steps):
        return ShotManifest(self.store).sync(steps, ranges=self.RANGE)

    def _keys(self, attr):
        plug = f"{self.door}.{attr}"
        return list(
            zip(
                cmds.keyframe(plug, q=True, tc=True) or [],
                cmds.keyframe(plug, q=True, vc=True) or [],
            )
        )

    def _pulse_plan(self):
        shot = self.store.sorted_shots()[0]
        return self.store.effect_recipe.plan("pulse", shot.start, shot.end, 24)

    def test_a_build_keys_the_scene_recipe(self):
        self.store.update_effect_recipe(pulse_period=2.0, pulse_duty=0.5)
        self._build(self._steps(("door", ["highlight"], "scene")))
        self.assertEqual(self._keys("highlight"), self._pulse_plan())

    def test_a_recipe_change_is_stale_until_a_build_rekeys_it(self):
        steps = self._steps(("door", ["highlight"], "scene"))
        _, _, built = self._build(steps)
        self.assertEqual(built[0].objects[0].status, "valid")
        self.assertFalse(built[0].needs_build)

        self.store.update_effect_recipe(pulse_period=2.0)
        stale = ShotManifest(self.store).assess(steps)
        self.assertEqual(stale[0].objects[0].status, "stale_behavior")
        self.assertEqual(stale[0].objects[0].stale_behaviors, ["highlight"])
        self.assertTrue(stale[0].needs_build)

        _, _, rebuilt = self._build(steps)
        self.assertEqual(rebuilt[0].objects[0].status, "valid")
        self.assertEqual(self._keys("highlight"), self._pulse_plan())

    def test_a_behavior_the_doc_drops_takes_its_keys(self):
        self._build(self._steps(("door", ["highlight", "fade_in"], "scene")))
        self.assertTrue(self._keys("highlight"))
        # The animator's key on the same channel is not the manifest's.
        cmds.setKeyframe(f"{self.door}.highlight", time=1000, value=0.5)

        steps = self._steps(("door", ["fade_in"], "scene"))
        dropped = ShotManifest(self.store).assess(steps)
        self.assertEqual(dropped[0].dropped_behaviors, [["door", "highlight"]])
        self.assertTrue(dropped[0].needs_build)

        self._build(steps)
        self.assertEqual(self._keys("highlight"), [(1000.0, 0.5)])
        self.assertTrue(self._keys("opacity"))
        self.assertFalse(self.store.edit_ledger.authored(behavior="highlight"))

    def test_an_animators_key_inside_the_shot_keeps_the_fade_off(self):
        """The ownership guard where it matters: the animator's key sits
        INSIDE the shot (the drop test's key at 1000 is outside it)."""
        from mayatk.mat_utils.render_opacity.attribute_mode import (
            OpacityAttributeMode,
        )

        OpacityAttributeMode.create([self.door])
        cmds.setKeyframe(f"{self.door}.opacity", time=100, value=0.5)
        steps = self._steps(("door", ["fade_in"], "scene"))

        _, beh, built = self._build(steps)
        self.assertEqual(
            [(r["object"], r["behavior"]) for r in beh["skipped"]],
            [("door", "fade_in")],
        )
        self.assertEqual(beh["applied"], [])
        self.assertEqual(self._keys("opacity"), [(100.0, 0.5)])
        self.assertFalse(self.store.edit_ledger.authored(behavior="fade_in"))
        # Unverified over keys the animator owns: a conflict, not a fix.
        self.assertEqual(built[0].objects[0].status, "behavior_conflict")

    def test_a_build_leaves_a_hand_placed_clip_of_its_track(self):
        """The build used to clear the whole track before keying its clip."""
        tid = AudioUtils.normalize_track_id("A01_Hello")
        AudioUtils.ensure_track_attr(tid)
        AudioUtils.set_path(tid, make_temp_wav("round_trip_hello", 1.0))
        AudioUtils.write_key(tid, 500, 1)  # placed by hand, later on

        self._build(self._steps(("A01_Hello", ["set_clip"], "audio")))
        start = self.store.sorted_shots()[0].start
        self.assertEqual(
            AudioUtils.read_keys(tid),
            [(start, 1.0), (start + 24.0, 0.0), (500.0, 1.0)],
        )
        # ...and claims only what it keyed.
        self.assertEqual(
            [t for _c, t in self.store.edit_ledger.authored(behavior="set_clip")],
            [start, start + 24.0],
        )


class MemberResolutionTest(MayaTkTestCase):
    """A doc name finds its scene node the one way every consumer does
    (``ShotStore.resolve_member``): exactly, then inside a namespace (a
    referenced asset), and a name several nodes answer to is a finding --
    reported, never keyed on a guess."""

    RANGE = {"A01": (0.0, 240.0)}

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")  # 24 fps
        ShotStore._active = None
        self.store = ShotStore()

    def tearDown(self):
        ShotStore._active = None
        super().tearDown()

    @staticmethod
    def _cube(name: str, parent: str = "") -> str:
        """A cube named *name* under world group *parent* (or the world); its
        long name -- spelled out, since a short name may now be shared."""
        cmds.polyCube(name="tmp_geo")
        path = "|tmp_geo"
        if parent:
            if not cmds.objExists(f"|{parent}"):
                cmds.group(empty=True, world=True, name=parent)
            cmds.parent(path, f"|{parent}")
            path = f"|{parent}|tmp_geo"
        cmds.rename(path, name)
        return f"{path.rpartition('|')[0]}|{name}"

    @staticmethod
    def _in_namespace(namespace: str, name: str) -> str:
        if not cmds.namespace(exists=namespace):
            cmds.namespace(add=namespace)
        return cmds.ls(cmds.polyCube(name=f"{namespace}:{name}")[0], long=True)[0]

    def _build(self, name: str):
        step = BuilderStep(
            step_id="A01", section="A", section_title="Sec", description="d"
        )
        step.objects.append(BuilderObject(name=name, behaviors=["fade_in"]))
        return ShotManifest(self.store).sync([step], ranges=self.RANGE)

    def test_a_name_is_found_exactly_then_in_a_namespace(self):
        lid = self._cube("lid_geo")
        door = self._in_namespace("AC", "door_geo")
        self.assertEqual(self.store.resolve_member("lid_geo"), (lid, "found"))
        self.assertEqual(self.store.resolve_member("door_geo"), (door, "found"))
        self.assertEqual(
            self.store.resolve_member("hinge_geo"), ("hinge_geo", "missing")
        )

    def test_a_leaf_two_nodes_answer_to_is_ambiguous(self):
        self._in_namespace("AC", "door_geo")
        self._in_namespace("BC", "door_geo")
        self.assertEqual(
            self.store.resolve_member("door_geo"), ("door_geo", "ambiguous")
        )
        self._cube("lid_geo", parent="setA")
        self._cube("lid_geo", parent="setB")
        self.assertEqual(self.store.resolve_member("lid_geo"), ("lid_geo", "ambiguous"))

    def test_a_build_keys_the_namespaced_node_a_doc_name_finds(self):
        door = self._in_namespace("AC", "door_geo")
        _, beh, built = self._build("door_geo")
        self.assertEqual(len(beh["applied"]), 1)
        self.assertTrue(cmds.keyframe(f"{door}.opacity", q=True))
        self.assertEqual(built[0].objects[0].status, "valid")

    def test_an_ambiguous_name_is_keyed_on_neither(self):
        """Bug: the build's existence check was ``cmds.objExists``, true for a
        name several transforms share, so the fade keyed the first match
        (``cmds.ls(...)[0]``) and Assess called the name ambiguous only after.
        Fixed: 2026-10-04
        """
        doors = [self._cube("door", parent=p) for p in ("setA", "setB")]
        self.assertEqual(len(cmds.ls("door", long=True)), 2)

        _, beh, built = self._build("door")
        self.assertEqual(beh["applied"], [])
        for door in doors:
            self.assertFalse(cmds.keyframe(door, q=True), door)
        self.assertEqual(built[0].objects[0].status, "ambiguous_object")


if __name__ == "__main__":
    unittest.main(verbosity=2)
