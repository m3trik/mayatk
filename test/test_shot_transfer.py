# !/usr/bin/python
# coding=utf-8
"""Test Suite for the Maya shot store's hand-off transfer surface
(``anim_utils/shots/_shots.py``: ``ShotStore.export_transfer`` /
``ShotStore.apply_transfer`` and the curve helpers under them).

The codec itself is pythontk's ``ShotTransfer`` (its own suite); this pins what
Maya contributes: how a ledger claim keyed by animCurve becomes an object +
channel (through a unitConversion), how a section's names and claims land back
on real nodes and curves, that the record persists on the scene carrier, and
that times follow the scene clock.

Run inside a live Maya session via ``run_tests.py`` (``run_tests.py shot_transfer``).
"""

import os
import pythontk as ptk

import maya.cmds as cmds

from mayatk.anim_utils.shots._shots import ShotStore
from base_test import MayaTkTestCase


def _section(objects, keys=None, **store):
    """A ``shots`` section as blendertk's producer would write it (Blender names)."""
    data = {
        "shots": [
            {
                "shot_id": 0,
                "name": "Intro",
                "start": 1.0,
                "end": 24.0,
                "objects": list(objects),
                "metadata": {},
                "locked": False,
                "description": "opening",
            }
        ],
        "hidden_objects": [],
        "pinned_objects": [],
        "markers": [{"time": 5.0, "note": "beat"}],
        "gap": 4.0,
        "locked_gaps": [],
        "scene_fps": 24.0,
        "snap_whole_frames": True,
    }
    data.update(store)
    return {"version": 1, "store": data, "ledger": {"steps": {}, "keys": keys or {}}}


class _TransferCase(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        ShotStore.clear_active()
        ShotStore._auto_export_disabled = False
        self._time_unit = cmds.currentUnit(query=True, time=True)

    def tearDown(self):
        cmds.currentUnit(time=self._time_unit)
        ShotStore.disable_auto_export()
        ShotStore._auto_export_disabled = False
        ShotStore.clear_active()
        super().tearDown()

    @staticmethod
    def _keyed_cube(name, frames=(1, 12, 24)):
        cube = cmds.polyCube(name=name)[0]
        for f in frames:
            cmds.setKeyframe(cube, attribute="translateX", t=f, v=f * 0.5)
        return cmds.ls(cube, long=True)[0]

    @staticmethod
    def _curve(node, attr):
        return cmds.listConnections(
            f"{node}.{attr}", type="animCurve", s=True, d=False
        )[0]


class TestExportTransfer(_TransferCase):
    def test_names_are_spelled_and_claims_become_object_channels(self):
        cube = self._keyed_cube("xfer_cube")
        # A channel driven THROUGH a unitConversion, the way rig curves often are.
        cmds.setKeyframe(cube, attribute="translateZ", t=24, v=1.0)
        tz = self._curve(cube, "translateZ")
        conv = cmds.createNode("unitConversion", name="xfer_conv")
        cmds.disconnectAttr(f"{tz}.output", f"{cube}.translateZ")
        cmds.connectAttr(f"{tz}.output", f"{conv}.input")
        cmds.connectAttr(f"{conv}.output", f"{cube}.translateZ")
        store = ShotStore.active()
        shot = store.define_shot("Intro", 1, 24, objects=[cube], description="d")
        store.edit_ledger.record_key(
            self._curve(cube, "translateX"), 24.0, shot.shot_id, "end"
        )
        store.edit_ledger.record_step(tz, 24.0, "linear", "linear")
        store.set_object_hidden(cube, True)

        section = ShotStore.export_transfer()

        shots = section["store"]["shots"]
        self.assertEqual(
            [(s["name"], s["objects"], s["description"]) for s in shots],
            [("Intro", ["xfer_cube"], "d")],
        )
        self.assertEqual(section["store"]["hidden_objects"], ["xfer_cube"])
        self.assertEqual(
            section["ledger"]["keys"],
            {"xfer_cube": {"translateX": [[24.0, shot.shot_id, "end"]]}},
        )
        self.assertEqual(
            section["ledger"]["steps"],
            {"xfer_cube": {"translateZ": [[24.0, "linear", "linear"]]}},
        )

    def test_scope_and_spelling_follow_the_send(self):
        cmds.namespace(add="ref")
        cube = self._keyed_cube("ref:xfer_cube")
        stayed = cmds.polyCube(name="stayed")[0]
        store = ShotStore.active()
        shot = store.define_shot("Intro", 1, 24, objects=[cube, stayed])
        store.edit_ledger.record_key(
            self._curve(cube, "translateX"), 24.0, shot.shot_id, "end"
        )

        fbx = ShotStore.export_transfer(objects=[cube])
        self.assertEqual(fbx["store"]["shots"][0]["objects"], ["ref:xfer_cube"])
        self.assertEqual(list(fbx["ledger"]["keys"]), ["ref:xfer_cube"])

        from mayatk.env_utils.blender_bridge._blender_bridge import BlenderBridge

        usd = ShotStore.export_transfer(
            spell=BlenderBridge._manifest_spelling("usd"), objects=[cube]
        )
        self.assertEqual(usd["store"]["shots"][0]["objects"], ["ref_xfer_cube"])
        self.assertEqual(list(usd["ledger"]["keys"]), ["ref_xfer_cube"])

        # Scoped to a node the shot never held: the shot still travels, empty.
        outside = ShotStore.export_transfer(objects=[stayed])
        self.assertEqual(outside["store"]["shots"][0]["objects"], ["stayed"])
        self.assertEqual(outside["ledger"], {"steps": {}, "keys": {}})

    def test_an_empty_store_has_nothing_to_say(self):
        ShotStore.active()
        self.assertIsNone(ShotStore.export_transfer())


class TestChannelsAndAudioTransfer(_TransferCase):
    """Render-effect channels, ad-hoc keyed attributes and audio clips cross
    with the shots: neither carrier animates a custom attribute or holds a sound."""

    def setUp(self):
        super().setUp()
        import tempfile
        import wave

        self.tmp = tempfile.mkdtemp(prefix="mtk_xfer_audio_")
        self.wav = os.path.join(self.tmp, "footstep.wav")
        with wave.open(self.wav, "wb") as fh:  # one second of silence at 44.1 kHz
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(44100)
            fh.writeframes(b"\x00\x00" * 44100)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _authored_scene(self):
        from mayatk.audio_utils._audio_utils import AudioUtils
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        cube = self._keyed_cube("fx_cube")
        RenderEffects.key_fade(objects=[cube], start=1, end=24, direction="in")
        RenderEffects.create([cube], channel="highlight")
        cmds.setKeyframe(cube, attribute="highlight", t=1, v=0.0, outTangentType="step")
        cmds.setKeyframe(cube, attribute="highlight", t=12, v=1.0)
        RenderEffects.set_channel_color([cube], (1.0, 0.0, 0.0), channel="highlight")
        cmds.addAttr(cube, longName="wobble", attributeType="double", keyable=True)
        cmds.setKeyframe(cube, attribute="wobble", t=5, v=2.0)
        AudioUtils.add_clip(self.wav, 10, name="footstep", frame_end=30)
        AudioUtils.sync()
        ShotStore.active().define_shot("Intro", 1, 48, objects=[cube])
        return cube

    def test_channels_and_audio_are_recorded(self):
        self._authored_scene()
        section = ShotStore.export_transfer()
        rec = section["channels"]["fx_cube"]
        self.assertEqual(
            rec["opacity"]["keys"], [[1.0, 0.0, "linear"], [24.0, 1.0, "linear"]]
        )
        self.assertEqual(
            rec["highlight"]["keys"], [[1.0, 0.0, "step"], [12.0, 1.0, "smooth"]]
        )
        self.assertEqual(rec["highlightColorR"]["value"], 1.0)
        self.assertEqual(rec["wobble"]["keys"], [[5.0, 2.0, "smooth"]])
        self.assertNotIn("visibility", rec)  # a standard attribute, the carrier's own
        self.assertEqual(
            section["audio"],
            [
                {
                    "name": "footstep",
                    # The carrier stores the path forward-slashed.
                    "file": self.wav.replace("\\", "/"),
                    "start": 10.0,
                    "end": 30.0,
                    "offset": 0.0,
                }
            ],
        )
        # Scoped to the send like every other payload.
        other = cmds.polyCube(name="other")[0]
        self.assertNotIn("channels", ShotStore.export_transfer(objects=[other]))

    def test_channels_and_audio_land_and_a_reapply_doubles_nothing(self):
        from mayatk.audio_utils._audio_utils import AudioUtils

        self._authored_scene()
        section = ShotStore.export_transfer()
        cmds.file(new=True, force=True)
        ShotStore.clear_active()
        cube = self._keyed_cube("fx_cube")

        for _ in range(2):  # the second apply is a no-op for what is already here
            ShotStore.apply_transfer(section)
            self.assertEqual(cmds.keyframe(f"{cube}.opacity", q=True), [1.0, 24.0])
            self.assertEqual(
                cmds.keyTangent(f"{cube}.opacity", q=True, outTangentType=True),
                ["linear", "linear"],
            )
            self.assertEqual(cmds.keyframe(f"{cube}.highlight", q=True), [1.0, 12.0])
            self.assertEqual(
                cmds.keyTangent(f"{cube}.highlight", q=True, outTangentType=True)[0],
                "step",
            )
            self.assertEqual(cmds.getAttr(f"{cube}.highlightColorR"), 1.0)
            self.assertEqual(cmds.keyframe(f"{cube}.wobble", q=True), [5.0])
            self.assertTrue(cmds.getAttr(f"{cube}.wobble", keyable=True))
            events = AudioUtils.read_events("footstep")
            self.assertEqual([(e.start, e.stop) for e in events], [(10.0, 30.0)])
            self.assertEqual(AudioUtils.list_tracks(), ["footstep"])
            self.assertIsNotNone(AudioUtils.find_dg_node_for_track("footstep"))
        # The second apply merges the shot again; the arrival is numbered, as
        # the store refuses two shots one clip name (ShotTransfer.merge).
        self.assertEqual(
            [s.name for s in ShotStore.active().shots], ["Intro", "Intro_2"]
        )


class TestApplyTransfer(_TransferCase):
    def test_rebuilds_the_store_and_persists_it(self):
        cube = self._keyed_cube("landed")
        section = _section(
            ["landed", "never_arrived"],
            keys={
                "landed": {
                    # 18 has no key on the receiving curve; rotateY has no curve at all.
                    "translateX": [[24.0, 0, "end"], [18.0, 0, "end"]],
                    "rotateY": [[24.0, 0, "end"]],
                },
                "never_arrived": {"translateX": [[24.0, 0, "end"]]},
            },
            hidden_objects=["never_arrived", "landed"],
            pinned_objects=["never_arrived"],
        )

        store = ShotStore.apply_transfer(section)

        self.assertEqual(
            [(s.name, s.start, s.end, s.objects, s.description) for s in store.shots],
            [("Intro", 1.0, 24.0, [cube], "opening")],
        )
        self.assertEqual(store.hidden_objects, {cube})
        # A pinned name that never arrived stays tracked -- pinning is for the missing.
        self.assertEqual(store.pinned_objects, {"never_arrived"})
        self.assertEqual(store.markers, [{"time": 5.0, "note": "beat"}])
        self.assertEqual(store.gap, 4.0)
        tx = self._curve(cube, "translateX")
        self.assertEqual(store.edit_ledger.key_times(tx), [24.0])
        self.assertEqual(store.edit_ledger.curves, {tx})

        # On the scene carrier, and the scene-open path reads it back.
        from mayatk.node_utils.data_nodes import DataNodes

        self.assertIn("Intro", DataNodes.read(ptk.Scope.PRIVATE, "shot_store") or "")
        ShotStore.invalidate()
        self.assertEqual([s.name for s in ShotStore.active().shots], ["Intro"])

    def test_merges_after_existing_shots_unless_told_to_replace(self):
        cube = self._keyed_cube("landed")
        own = ShotStore.active().define_shot("Own", 30, 60, objects=[cube])
        section = _section(["landed"])

        merged = ShotStore.apply_transfer(section)
        self.assertEqual(
            sorted((s.shot_id, s.name) for s in merged.shots),
            [(own.shot_id, "Own"), (own.shot_id + 1, "Intro")],
        )
        self.assertEqual(merged.gap, 0.0)  # the scene's own settings stand

        replaced = ShotStore.apply_transfer(section, replace=True)
        self.assertEqual([s.name for s in replaced.shots], ["Intro"])
        self.assertEqual(replaced.gap, 4.0)

    def test_times_follow_the_scene_clock(self):
        cmds.currentUnit(time="ntsc")  # 30 fps; the section was authored at 24
        cube = self._keyed_cube("landed", frames=(1, 30))
        store = ShotStore.apply_transfer(
            _section(["landed"], keys={"landed": {"translateX": [[24.0, 0, "end"]]}})
        )
        self.assertEqual((store.shots[0].start, store.shots[0].end), (1.0, 30.0))
        self.assertEqual(store.gap, 5.0)
        self.assertEqual(store.markers[0]["time"], 6.25)
        self.assertEqual(store.scene_fps, 30.0)
        # The claim landed on frame 30, where the receiving curve has its key.
        self.assertEqual(
            store.edit_ledger.key_times(self._curve(cube, "translateX")), [30.0]
        )

    def test_a_scoped_resolver_never_claims_a_bystander(self):
        self._keyed_cube("landed")
        store = ShotStore.apply_transfer(
            _section(["landed"]), resolve=lambda name: None
        )
        self.assertEqual(store.shots[0].objects, [])
        self.assertIsNone(ShotStore.apply_transfer({}))
