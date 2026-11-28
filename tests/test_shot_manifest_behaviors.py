# coding=utf-8
"""Shot Manifest behavior template schema + discovery tests, the effect
recipe's round trip through a real scene, and how a doc name finds its node.

The schema and dispatch tests are Maya-free (``_behaviors`` guards ``cmds``);
:class:`RecipeRoundTripTest` and :class:`MemberResolutionTest` build in a live
scene. All run via mayapy alongside the rest of the suite.  Templates are JSON
(the pythontk engine's store, shared with blendertk).

    & $MAYAPY mayatk\\tests\\test_shot_manifest_behaviors.py
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
        for name in ("show", "hide", "set_clip"):
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
        for n in ("show", "hide", "set_clip"):
            self.assertIn(n, names)

    def test_kind_filter(self):
        self.assertIn("set_clip", Behaviors.list_behaviors(kind="audio"))
        self.assertNotIn("show", Behaviors.list_behaviors(kind="audio"))
        self.assertIn("show", Behaviors.list_behaviors(kind="scene"))

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
            [self._shot([{"name": "cube", "behavior": "show"}])],
            apply_fn,
            exists_fn=lambda name: True,
            has_keys_fn=lambda name, s, e: False,
        )
        self.assertEqual(calls, [("cube", "show", 0, 30)])
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
                        {"name": "door", "behavior": "hide"},
                        {"name": "lid", "behavior": "show"},
                        {"name": "lid", "behavior": "hide"},
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
                ("door", "hide"): None,
                ("lid", "show"): 0.0,
                ("lid", "hide"): 1.0,
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
                [self._shot([{"name": "cube", "behavior": "show"}])],
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
        return ShotManifest(self.store, disabled_behaviors=()).sync(steps, ranges=self.RANGE)

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
        stale = ShotManifest(self.store, disabled_behaviors=()).assess(steps)
        self.assertEqual(stale[0].objects[0].status, "stale_behavior")
        self.assertEqual(stale[0].objects[0].stale_behaviors, ["highlight"])
        self.assertTrue(stale[0].needs_build)

        _, _, rebuilt = self._build(steps)
        self.assertEqual(rebuilt[0].objects[0].status, "valid")
        self.assertEqual(self._keys("highlight"), self._pulse_plan())

    def test_a_behavior_the_doc_drops_takes_its_keys(self):
        self._build(self._steps(("door", ["highlight", "show"], "scene")))
        self.assertTrue(self._keys("highlight"))
        # The animator's key on the same channel is not the manifest's.
        cmds.setKeyframe(f"{self.door}.highlight", time=1000, value=0.5)

        steps = self._steps(("door", ["show"], "scene"))
        dropped = ShotManifest(self.store, disabled_behaviors=()).assess(steps)
        self.assertEqual(dropped[0].dropped_behaviors, [["door", "highlight"]])
        self.assertTrue(dropped[0].needs_build)

        self._build(steps)
        self.assertEqual(self._keys("highlight"), [(1000.0, 0.5)])
        self.assertTrue(self._keys("opacity"))
        self.assertFalse(self.store.edit_ledger.authored(behavior="highlight"))

    def test_the_unused_tail_ends_at_a_faded_members_own_keys(self):
        """Assess's "Nf unused" is what Trim Trailing Space would cut
        (``ShotSequencer.content_end``): a door that fades in AND slides until
        frame 130 leaves the frames after it, though its curve holds on to
        a key at 1000, past the shot. Measured before: all but the fade read
        as unused (the keys of an object with a behavior were never read).
        Fixed: 2026-11-08"""
        steps = self._steps(("door", ["show"], "scene"))
        self._build(steps)
        for frame, value in ((0, 0.0), (130, 5.0), (1000, 5.0)):
            cmds.setKeyframe(f"{self.door}.translateX", time=frame, value=value)
        [status] = ShotManifest(self.store, disabled_behaviors=()).assess(steps)
        shot = self.store.sorted_shots()[0]
        self.assertEqual(status.shrinkable_frames, shot.end - 130.0, (shot.start, shot.end))

    def test_an_animators_key_inside_the_shot_keeps_the_fade_off(self):
        """The ownership guard where it matters: the animator's key sits
        INSIDE the shot (the drop test's key at 1000 is outside it)."""
        from mayatk.mat_utils.render_opacity.attribute_mode import (
            OpacityAttributeMode,
        )

        OpacityAttributeMode.create([self.door])
        cmds.setKeyframe(f"{self.door}.opacity", time=100, value=0.5)
        steps = self._steps(("door", ["show"], "scene"))

        _, beh, built = self._build(steps)
        self.assertEqual(
            [(r["object"], r["behavior"]) for r in beh["skipped"]],
            [("door", "show")],
        )
        self.assertEqual(beh["applied"], [])
        self.assertEqual(self._keys("opacity"), [(100.0, 0.5)])
        self.assertFalse(self.store.edit_ledger.authored(behavior="show"))
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


class PlacingHelpersTest(MayaTkTestCase):
    """A build makes the helper a placing row names in ``where`` where the
    scene has none: a camera for a picture, a locator for a viewpoint or a
    pointer's tip, at ``ptk.ManifestPlacement``'s plain first pose, riding what it is
    for.  The real task's viewpoints, its pointer and its pictures' cameras were
    made by hand, each by a script of its own."""

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")
        ShotStore._active = None
        self.store = ShotStore()
        # A delivery: a cabinet 2 m tall on the floor, a latch on its front (+Z), 1.1 m up; centimetres, Y up.
        self.root = cmds.group(empty=True, name="DELIVERY")
        body = cmds.polyCube(name="BODY", width=100, height=200, depth=60)[0]
        cmds.xform(body, translation=(0, 100, 0))
        self.latch = cmds.polyCube(name="LATCH", width=10, height=10, depth=4)[0]
        cmds.xform(self.latch, translation=(20, 110, 32))
        cmds.parent(body, self.latch, self.root)

    def tearDown(self):
        ShotStore._active = None
        super().tearDown()

    @staticmethod
    def _steps():
        rows = [
            {"word": "travel", "places": "stand", "assets": ["BODY"], "settings": {"where": "VIEW_A01"}},
            {"word": "indicate", "places": "point", "assets": ["LATCH"], "settings": {"where": "POINT_LATCH"}},
            {"word": "picture", "places": "view", "assets": ["LATCH"], "settings": {"where": "CAM_A01"}},
            {"word": "picture", "places": "view", "assets": ["NO_SUCH_PART"], "settings": {"where": "CAM_NONE"}},
        ]
        return [BuilderStep("A01", "A", "t", "", rows=rows)]

    @staticmethod
    def _forward(node, axis):
        """*node*'s local *axis* in the world, as a unit vector."""
        m = cmds.xform(node, query=True, worldSpace=True, matrix=True)
        v = [sum(axis[r] * m[r * 4 + c] for r in range(3)) for c in range(3)]
        length = sum(x * x for x in v) ** 0.5
        return [x / length for x in v]

    def test_a_build_makes_each_named_helper_at_its_plain_first_pose_and_never_again(self):
        from pythontk.core_utils.engines.shots.manifest.manifest_placement import ManifestPlacement

        mani = ShotManifest(self.store)
        placed = mani.place_helpers(self._steps())
        self.assertEqual(placed["made"], ["VIEW_A01", "POINT_LATCH", "CAM_A01"])
        self.assertEqual([name for name, _ in placed["skipped"]], ["CAM_NONE"], "a part the scene lacks: said, nothing made")

        # A viewpoint: a locator on the floor, the stand-off out of the delivery's middle... the body IS the middle, so +Z.
        view = cmds.ls("VIEW_A01", long=True)[0]
        self.assertEqual(view, "|DELIVERY|VIEW_A01", "under the delivery")
        at = cmds.xform(view, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(at[1], 0.0, places=3)
        # The floor is the scene's, not the delivery group's own lowest point: a floor slab in a group of its own,
        # below a delivery lifted 24 cm, is where the next viewpoint stands -- on its top, ground under its feet.
        cmds.delete(view)
        cmds.xform(self.root, translation=(0, 24, 0))
        cmds.xform(cmds.polyCube(name="ROOM_FLOOR", width=1000, height=2, depth=1000)[0], translation=(0, -1, 0))
        mani.place_helpers([BuilderStep("A02", "A", "t", "", rows=[{"word": "travel", "places": "stand", "assets": ["BODY"], "settings": {"where": "VIEW_A02"}}])])
        self.assertAlmostEqual(cmds.xform("VIEW_A02", query=True, worldSpace=True, translation=True)[1], 0.0, places=3,
                               msg="on the floor slab's top")
        cmds.delete("VIEW_A02", "ROOM_FLOOR")
        cmds.xform(self.root, translation=(0, 0, 0))
        mani.place_helpers(self._steps())
        view = cmds.ls("VIEW_A01", long=True)[0]
        at = cmds.xform(view, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(at[2], 30.0 + ManifestPlacement.STAND_OFF * 100.0, delta=1.0, msg="1.5 m outside its front")
        self.assertLess(self._forward(view, (0, 0, 1))[2], -0.99, "its +Z, the way the user faces, turned to the cabinet")

        # A pointer's tip: a locator by the latch, its +Z pointing at it, riding the latch. Not straight above it: the
        # cabinet's face is closer than a pointer's room to turn, so it leans out toward the eyes.
        pointer = cmds.ls("POINT_LATCH", long=True)[0]
        self.assertEqual(pointer, "|DELIVERY|LATCH|POINT_LATCH")
        tip = cmds.xform(pointer, query=True, worldSpace=True, translation=True)
        to = [c - t for c, t in zip((20.0, 110.0, 32.0), tip)]
        far = sum(x * x for x in to) ** 0.5
        self.assertGreater(sum(a * b / far for a, b in zip(self._forward(pointer, (0, 0, 1)), to)), 0.999, "it points at the latch")
        self.assertGreater(tip[2], 30.0 + ManifestPlacement.POINTER * 30.0, "clear of the cabinet's face by its room")

        # A picture's camera: looking at the latch from its outer side, far enough to frame it, with the picture's lens.
        cam = cmds.ls("CAM_A01", long=True)[0]
        self.assertEqual(cam, "|DELIVERY|LATCH|CAM_A01")
        shape = cmds.listRelatives(cam, shapes=True, fullPath=True)[0]
        self.assertEqual(cmds.nodeType(shape), "camera")
        eye = cmds.xform(cam, query=True, worldSpace=True, translation=True)
        look = self._forward(cam, (0, 0, -1))  # a camera looks down its -Z
        to = [c - e for c, e in zip((20.0, 110.0, 32.0), eye)]
        far = sum(x * x for x in to) ** 0.5
        self.assertGreater(sum(a * b / far for a, b in zip(look, to)), 0.999, "it looks at the latch")
        self.assertGreater(far, 20.0, "from far enough to frame it")
        import math

        film = cmds.getAttr(shape + ".verticalFilmAperture") * 25.4
        angle = math.degrees(2 * math.atan(film / 2 / cmds.getAttr(shape + ".focalLength")))
        self.assertAlmostEqual(angle, ManifestPlacement.VIEW_ANGLE, places=2, msg="the picture's field of view")

        # A helper is the truth once made: moved by hand, a second build neither makes nor moves it.
        cmds.xform(cam, worldSpace=True, translation=(60.0, 120.0, 90.0))
        again = mani.place_helpers(self._steps())
        self.assertEqual((again["made"], sorted(again["kept"])), ([], ["POINT_LATCH", "CAM_A01", "VIEW_A01"]))
        self.assertEqual([round(x, 3) for x in cmds.xform(cam, query=True, worldSpace=True, translation=True)], [60.0, 120.0, 90.0])

    def test_a_helper_is_placed_as_the_scene_stands_at_its_steps_shot_and_rides_its_part(self):
        # The latch slides 50 cm up over the shot before; the camera is made where the latch stands as its step starts.
        cmds.setKeyframe(self.latch, attribute="translateY", time=0, value=110)
        cmds.setKeyframe(self.latch, attribute="translateY", time=100, value=160)
        self.store.define_shot("A01", 100, 200, metadata={"step": "A01"})
        cmds.currentTime(0)
        mani = ShotManifest(self.store)
        rows = [{"word": "picture", "places": "view", "assets": ["LATCH"], "settings": {"where": "CAM_A01"}}]
        placed = mani.place_helpers([BuilderStep("A01", "A", "t", "", rows=rows)])
        self.assertEqual(placed["made"], ["CAM_A01"])
        self.assertEqual(cmds.currentTime(query=True), 0.0, "the playhead is left where it was")
        cmds.currentTime(100)
        over = cmds.xform("CAM_A01", query=True, worldSpace=True, translation=True)[1] - 160.0
        self.assertTrue(0.0 <= over < 30.0, "a little above the latch as its step finds it (%s)" % over)
        cmds.currentTime(0)
        self.assertAlmostEqual(cmds.xform("CAM_A01", query=True, worldSpace=True, translation=True)[1] - 110.0, over, places=2,
                               msg="and it rides the latch")

    def test_a_picture_stands_where_its_part_is_seen_never_behind_what_hides_it(self):
        # Dials on the back of a panel behind a door, the delivery reaching out behind them: by bounds alone the outer
        # side for the dials is the door's, where the panel and the door hide them -- the real task's dials camera stood
        # just there. In a metre scene as in a centimetre one (Maya's rays answer in centimetres).
        for linear, scale in (("cm", 1.0), ("m", 0.01)):
            with self.subTest(linear):
                cmds.file(new=True, force=True)
                cmds.currentUnit(linear=linear)
                ShotStore._active = None
                root = cmds.group(empty=True, name="CABINET")
                for name, size, at in (
                    ("DOOR", (100, 200, 6), (0, 100, 0)),
                    ("PANEL", (80, 60, 3), (0, 150, -41.5)),
                    ("DIALS", (10, 10, 3), (0, 150, -44.5)),
                ):
                    made = cmds.polyCube(name=name, width=size[0] * scale, height=size[1] * scale, depth=size[2] * scale)[0]
                    cmds.xform(made, translation=[c * scale for c in at])
                    cmds.parent(made, root)
                reach = cmds.curve(degree=1, point=[(-300 * scale, 0, -600 * scale), (300 * scale, 250 * scale, -60 * scale)])
                cmds.parent(reach, root)  # a curve: it spans the delivery's bounds, and no ray stops at it
                rows = [{"word": "picture", "places": "view", "assets": ["DIALS"], "settings": {"where": "CAM_D01"}}]
                made = ShotManifest(ShotStore()).place_helpers([BuilderStep("D01", "D", "t", "", rows=rows)])["made"]
                self.assertEqual(made, ["CAM_D01"])
                eye = cmds.xform("CAM_D01", query=True, worldSpace=True, translation=True)
                self.assertLess(eye[2], -46.0 * scale, "from the side the dials face, never through the panel")
                to = [c - e for c, e in zip((0.0, 150.0 * scale, -44.5 * scale), eye)]
                far = sum(x * x for x in to) ** 0.5
                look = self._forward("CAM_D01", (0, 0, -1))
                self.assertGreater(sum(a * b / far for a, b in zip(look, to)), 0.999, "it looks at the dials")
        cmds.currentUnit(linear="cm")

    def test_a_target_with_no_geometry_is_placed_by_where_it_stands(self):
        """Bug: a Target with no shape under it (an empty group) was measured by
        ``exactWorldBoundingBox`` of the node, an empty group's inverted at 1e20, so
        its helper stood nowhere near it (blendertk made none). Fixed: 2026-11-26 --
        both place it by its world position."""
        mark = cmds.group(empty=True, name="MARK", parent=self.root)
        cmds.xform(mark, worldSpace=True, translation=(20.0, 150.0, 32.0))
        rows = [{"word": "picture", "places": "view", "assets": ["MARK"], "settings": {"where": "CAM_MARK"}}]
        placed = ShotManifest(self.store).place_helpers([BuilderStep("A01", "A", "t", "", rows=rows)])
        self.assertEqual(placed["made"], ["CAM_MARK"])
        self.assertEqual(cmds.ls("CAM_MARK", long=True), ["|DELIVERY|MARK|CAM_MARK"])
        eye = cmds.xform("CAM_MARK", query=True, worldSpace=True, translation=True)
        to = [c - e for c, e in zip((20.0, 150.0, 32.0), eye)]
        far = sum(x * x for x in to) ** 0.5
        self.assertLess(far, 300.0, "by the mark (%s)" % eye)
        look = self._forward("CAM_MARK", (0, 0, -1))
        self.assertGreater(sum(a * b / far for a, b in zip(look, to)), 0.999, "looking at it")

    def test_a_locator_is_drawn_a_tenth_of_a_metre_in_any_unit(self):
        """A locator's ``localScale`` is a distance (``doubleLinear``): set in the
        scene's unit, it draws 10 cm in a centimetre scene and in a metre one."""
        for linear, size in (("cm", 10.0), ("m", 0.1)):
            with self.subTest(linear):
                cmds.currentUnit(linear=linear)
                rows = [{"word": "travel", "places": "stand", "assets": ["BODY"], "settings": {"where": "VIEW_" + linear}}]
                ShotManifest(ShotStore()).place_helpers([BuilderStep("A01", "A", "t", "", rows=rows)])
                shape = cmds.listRelatives("VIEW_" + linear, shapes=True, fullPath=True)[0]
                self.assertEqual(cmds.nodeType(shape), "locator")
                self.assertAlmostEqual(cmds.getAttr(shape + ".localScaleX"), size, places=6)
        cmds.currentUnit(linear="cm")

    def test_a_build_leaves_the_selection_and_nothing_it_did_not_keep(self):
        """Bug: ``cmds.camera`` and ``spaceLocator`` select what they make, so a Build
        left the user's selection on its last helper; and a failure partway left the
        helper and its aim locator in the scene. Fixed: 2026-11-26"""
        from unittest import mock

        cmds.select(self.latch, replace=True)
        mani = ShotManifest(self.store)
        mani.place_helpers(self._steps())
        self.assertEqual(cmds.ls(selection=True, long=True), ["|DELIVERY|LATCH"], "the user's selection, kept")
        top = set(cmds.ls(assemblies=True, long=True))
        self.assertEqual(top, {"|DELIVERY", "|persp", "|top", "|front", "|side"}, "no stray node")
        rows = [{"word": "picture", "places": "view", "assets": ["BODY"], "settings": {"where": "CAM_FAIL"}}]
        with mock.patch.object(cmds, "aimConstraint", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                mani.place_helpers([BuilderStep("A02", "A", "t", "", rows=rows)])
        self.assertFalse(cmds.objExists("CAM_FAIL"), "a helper it could not finish is gone")
        self.assertEqual(set(cmds.ls(assemblies=True, long=True)), top, "and so is its aim")
        self.assertEqual(cmds.ls(selection=True, long=True), ["|DELIVERY|LATCH"])


class SetStatesTest(MayaTkTestCase):
    """A build makes sure what a ``set`` row is about has the state it names:
    its emissive group takes the tool's default states where it has none, and
    one with states keeps them.  The real task's indicators were given theirs
    by a script written for that task."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        ShotStore._active = None
        self.store = ShotStore()
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        self.groups = EmissiveGroups
        board = cmds.group(empty=True, name="BOARD")
        self.led = cmds.polyCube(name="LED_GREEN")[0]
        self.plain = cmds.polyCube(name="PLAIN_PART")[0]
        cmds.parent(self.led, self.plain, board)
        mat = cmds.shadingNode("standardSurface", asShader=True, name="ledMat")
        cmds.setAttr(mat + ".emission", 1.0)
        cmds.setAttr(mat + ".emissionColor", 1.0, 1.0, 1.0, type="double3")
        MatUtils.assign_mat([self.led], mat)
        EmissiveGroups.add_group("led_green", [self.led + ".f[0:5]"])

    def tearDown(self):
        ShotStore._active = None
        super().tearDown()

    @staticmethod
    def _steps(*rows):
        return [BuilderStep("A01", "A", "t", "", rows=[dict(r) for r in rows])]

    def test_a_build_gives_a_set_rows_group_its_states_and_keeps_those_it_has(self):
        self.assertEqual(self.groups.states("led_green"), {}, "the group has no states yet")
        mani = ShotManifest(self.store)
        steps = self._steps(
            {"word": "set", "assets": ["LED_GREEN"], "settings": {"state": "off"}},
            {"word": "set", "assets": ["BOARD"], "settings": {"state": "green"}},  # a group above the indicator
            {"word": "set", "assets": ["LED_GREEN"], "settings": {"state": "purple"}},
            {"word": "set", "assets": ["PLAIN_PART"], "settings": {"state": "off"}},
        )
        states = mani.set_states(steps)
        self.assertEqual(states["given"], [("LED_GREEN", "off")], "the tool's default states, stored on its group")
        self.assertEqual(states["kept"], [("BOARD", "green")], "found by the faces its copies wear under the Target")
        self.assertEqual(list(self.groups.states("led_green")), ["off", "green", "blue", "amber", "red"])
        self.assertEqual([(t, s) for t, s, _ in states["skipped"]], [("LED_GREEN", "purple"), ("PLAIN_PART", "off")])
        self.assertIn("no state 'purple'", states["skipped"][0][2])
        self.assertIn("no emissive group", states["skipped"][1][2])

        # Its own states are the truth: a second build changes none of them.
        self.groups.set_states("led_green", {"off": {"weight": 0.0}, "amber": {"tint": [1.0, 0.5, 0.0], "weight": 1.0}})
        again = mani.set_states(self._steps({"word": "set", "assets": ["LED_GREEN"], "settings": {"state": "amber"}}))
        self.assertEqual((again["given"], again["kept"]), ([], [("LED_GREEN", "amber")]))
        self.assertEqual(list(self.groups.states("led_green")), ["off", "amber"])

    def test_a_target_whose_groups_cannot_all_take_the_state_changes_none(self):
        """Bug: the groups under a Target were given their defaults one by one,
        so where a later one lacked the state the build said skipped and had
        stored the earlier ones' anyway. Fixed: 2026-11-26 -- checked whole
        first (``EmissiveGroups.ensure_state``)."""
        from mayatk.mat_utils._mat_utils import MatUtils

        red = cmds.polyCube(name="LED_RED")[0]
        cmds.parent(red, "BOARD")
        mat = cmds.shadingNode("standardSurface", asShader=True, name="redMat")
        cmds.setAttr(mat + ".emission", 1.0)
        MatUtils.assign_mat([red], mat)
        self.groups.add_group("led_red", [red + ".f[0:5]"])
        self.groups.set_states("led_red", {"off": {"weight": 0.0}, "on": {"weight": 1.0}})
        states = ShotManifest(self.store).set_states(
            self._steps({"word": "set", "assets": ["BOARD"], "settings": {"state": "green"}})
        )
        self.assertEqual([(t, s) for t, s, _ in states["skipped"]], [("BOARD", "green")])
        self.assertIn("'led_red' has no state 'green'", states["skipped"][0][2])
        self.assertEqual(self.groups.states("led_green"), {}, "refused whole: nothing given")


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
        step.objects.append(BuilderObject(name=name, behaviors=["show"]))
        return ShotManifest(self.store, disabled_behaviors=()).sync([step], ranges=self.RANGE)

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
        lids = [
            self._cube("lid_geo", parent="setA"),
            self._cube("lid_geo", parent="setB"),
        ]
        self.assertEqual(self.store.resolve_member("lid_geo"), ("lid_geo", "ambiguous"))
        self.assertEqual(sorted(self.store.member_nodes("lid_geo")), sorted(lids))

    def test_a_path_tail_finds_its_node_in_a_namespace_an_exact_one_first(self):
        """A doc tells two alike-named nodes apart by its path's tail
        (``SOLENOID_LOC|LIGHTS``), and the scene holds them in a referenced
        asset's namespace: found there, as blendertk reads a tail
        (``ptk.NodeNames.ends``, namespace-blind); a node the tail names
        exactly answers first, as a plain name's does. Added: 2026-11-26"""
        cmds.namespace(add="AC")
        for parent in ("AC:SOLENOID_LOC", "AC:SPARE"):
            cmds.group(empty=True, name="AC:LIGHTS", parent=cmds.group(empty=True, name=parent))
        self.assertEqual(
            self.store.resolve_member("SOLENOID_LOC|LIGHTS"), ("|AC:SOLENOID_LOC|AC:LIGHTS", "found")
        )
        self.assertEqual(self.store.resolve_member("LIGHTS"), ("LIGHTS", "ambiguous"))
        cmds.group(empty=True, name="LIGHTS", parent=cmds.group(empty=True, name="SOLENOID_LOC"))
        self.assertEqual(
            self.store.resolve_member("SOLENOID_LOC|LIGHTS"), ("|SOLENOID_LOC|LIGHTS", "found"), "exact first"
        )

    def test_discovery_leaves_out_only_the_members_a_name_names(self):
        """Bug: what a shot holds was left out of its scene discovery by leaf
        (``ShotStore.member_key``), so naming ``PANEL|LATCH`` left out nothing,
        or (by its leaf) an animated ``DOOR|LATCH`` too. Fixed: 2026-11-26 --
        ``ShotStore.names_member``: a path's tail leaves out the members it
        ends, a bare leaf every member of it."""
        latches = []
        for parent in ("PANEL", "DOOR"):
            latch = self._cube("LATCH", parent=parent)
            cmds.setKeyframe(latch, attribute="translateX", time=0, value=0)
            cmds.setKeyframe(latch, attribute="translateX", time=10, value=5)
            latches.append(latch)

        def discover(known):
            mani = ShotManifest(self.store)
            return sorted(cmds.ls(n, long=True)[0] for n in mani._discover_scene_objects(0, 10, set(known)))

        self.assertEqual(discover({"PANEL|LATCH"}), [latches[1]], "the door's latch alone")
        self.assertEqual(discover({latches[0]}), [latches[1]], "a member by its own path")
        self.assertEqual(discover({"LATCH"}), [], "a bare leaf: every member of it")

    def test_assess_gives_discovery_names_so_a_member_hides_only_its_own_node(self):
        """Bug: Assess gave discovery the leaves of the shot's members and the
        step's names (``{member_key(...)}``), so a shot holding
        ``|PANEL|LATCH`` hid an animated ``|DOOR|LATCH``. Fixed: 2026-11-26 --
        the doc names and the members as stored; a member's lineage is its long
        name (``MayaShotsHost.member_lineage``)."""
        latches = []
        for parent in ("PANEL", "DOOR"):
            latch = self._cube("LATCH", parent=parent)
            cmds.setKeyframe(latch, attribute="translateX", time=0, value=0)
            cmds.setKeyframe(latch, attribute="translateX", time=10, value=5)
            latches.append(latch)
        self.assertEqual(self.store.host.member_lineage(latches[0]), latches[0].split("|"))
        self.store.define_shot("A01", 0, 10, objects=[latches[0]], metadata={"step": "A01"})
        step = BuilderStep("A01", "A", "t", "", [BuilderObject("PANEL|LATCH")])
        (status,) = ShotManifest(self.store, disabled_behaviors=()).assess([step])
        self.assertEqual([cmds.ls(n, long=True)[0] for n in status.additional_objects], [latches[1]])

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
