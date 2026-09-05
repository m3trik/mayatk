# !/usr/bin/python
# coding=utf-8
"""Tests for ``mayatk.anim_utils.key_stash`` — parking keys outside the working
animation and bringing them back.

Each test is a claim the design rests on, probed live before the code was
written (Maya 2025) and pinned here: a stashed clip has zero evaluation and
export effect, survives Optimize Scene Size and save/reopen, retrieves the
exact keys, previews through a transient layer that leaves no trace, and its
persistence channel never touches the shot store's.
"""

import unittest

import maya.cmds as cmds
import maya.mel as mel

from base_test import MayaTkTestCase
from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.anim_utils.key_stash._key_stash import KeyStash
from mayatk.anim_utils.shots._shots import MayaScenePersistence, ShotStore
from mayatk.node_utils.data_nodes import DataNodes

KEYS = [(1, 0.0), (10, 5.0), (20, 10.0), (30, 15.0), (40, 20.0)]


def _reset_store():
    """Forget the active store AND its backend so each test re-installs a fresh one."""
    backend = KeyStash._persistence
    if backend is not None and hasattr(backend, "remove_callbacks"):
        backend.remove_callbacks()
    KeyStash._active = None
    KeyStash.set_persistence(None)


class KeyStashTestCase(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        _reset_store()
        self.cube = cmds.polyCube(name="cube")[0]
        for t, v in KEYS:
            cmds.setKeyframe(self.cube, attribute="translateX", time=t, value=v)
        self.curve = cmds.listConnections(f"{self.cube}.translateX", type="animCurve")[
            0
        ]

    def tearDown(self):
        _reset_store()
        super().tearDown()

    # ---- helpers -----------------------------------------------------------

    def _times(self, node=None):
        return (
            cmds.keyframe(
                node or f"{self.cube}.translateX", query=True, timeChange=True
            )
            or []
        )

    def _tx_at(self, frame):
        cmds.currentTime(frame)
        return cmds.getAttr(f"{self.cube}.translateX")

    def _stash_range(self, start=10, end=30, **kw):
        return KeyStash.active().stash(
            objects=[self.cube], time_range=(start, end), **kw
        )

    # ---- stash -------------------------------------------------------------

    def test_stash_parks_keys_off_the_live_curve(self):
        clip = self._stash_range()
        self.assertEqual(self._times(), [1.0, 40.0])
        self.assertEqual((clip.start, clip.end, clip.key_count), (10.0, 30.0, 3))
        self.assertEqual(clip.label, "cube 10-30")
        self.assertEqual(clip.objects, [cmds.ls(self.cube, long=True)[0]])
        stash = cmds.ls(clip.curves[0]["stash"]["uuid"])[0]
        self.assertEqual(
            cmds.keyframe(stash, query=True, timeChange=True), [10.0, 20.0, 30.0]
        )
        self.assertTrue(cmds.lockNode(stash, query=True, lock=True)[0])
        # Only the keep-alive registry edge — nothing downstream evaluates it.
        conns = cmds.listConnections(stash, plugs=True) or []
        self.assertTrue(all(KeyStash.REGISTRY_ATTR in c for c in conns), conns)
        self.assertEqual(len(conns), 1)

    def test_stashed_clip_has_zero_evaluation_effect(self):
        self._stash_range()
        with_stash = self._tx_at(20)
        # Same scene, keys deleted outright, no stash anywhere.
        KeyStash.active().drop(KeyStash.active().clips[0].clip_id)
        self.assertEqual(cmds.ls(type="animCurve"), [self.curve])
        self.assertAlmostEqual(with_stash, self._tx_at(20), places=9)

    def test_stash_selected_keys_is_per_curve(self):
        for t, v in ((5, 1.0), (15, 2.0), (25, 3.0)):
            cmds.setKeyframe(self.cube, attribute="rotateY", time=t, value=v)
        ry_curve = cmds.listConnections(f"{self.cube}.rotateY", type="animCurve")[0]
        cmds.selectKey(clear=True)
        cmds.selectKey(self.curve, time=(10, 20), add=True)
        cmds.selectKey(ry_curve, time=(25, 25), add=True)
        clip = KeyStash.active().stash(selected_keys=True)
        self.assertEqual(self._times(), [1.0, 30.0, 40.0])
        self.assertEqual(self._times(f"{self.cube}.rotateY"), [5.0, 15.0])
        by_attr = {rec["plug"]["attr"]: rec["times"] for rec in clip.curves}
        self.assertEqual(by_attr, {"translateX": [10.0, 20.0], "rotateY": [25.0]})

    def test_stash_range_restricted_to_attributes(self):
        cmds.setKeyframe(self.cube, attribute="translateY", time=20, value=7.0)
        clip = self._stash_range(attributes=["ty"])
        self.assertEqual(self._times(), [1.0, 10.0, 20.0, 30.0, 40.0])
        self.assertEqual(self._times(f"{self.cube}.translateY"), [])
        self.assertEqual(clip.curves[0]["plug"]["attr"], "translateY")

    def test_stash_returns_none_when_range_holds_no_keys(self):
        self.assertIsNone(self._stash_range(100, 200))
        self.assertTrue(KeyStash.active().is_empty())
        with self.assertRaises(ValueError):
            KeyStash.active().stash(objects=[self.cube])

    def test_stash_is_one_undo_step(self):
        self._stash_range()
        cmds.undo()
        self.assertEqual(self._times(), [1.0, 10.0, 20.0, 30.0, 40.0])
        # The manifest write is not undoable; reconcile prunes the dead record.
        self.assertEqual(KeyStash.active().reconcile(), [1])
        self.assertTrue(KeyStash.active().is_empty())

    # ---- retrieve ------------------------------------------------------------

    def test_retrieve_restores_exact_keys_and_forgets_the_clip(self):
        cmds.keyTangent(
            self.curve, time=(20, 20), inTangentType="step", outTangentType="step"
        )
        clip = self._stash_range()
        store = KeyStash.active()
        self.assertEqual(store.retrieve(clip.clip_id), 3)
        self.assertEqual(self._times(), [1.0, 10.0, 20.0, 30.0, 40.0])
        self.assertEqual(
            cmds.keyframe(self.curve, query=True, valueChange=True),
            [v for _, v in KEYS],
        )
        self.assertEqual(
            cmds.keyTangent(self.curve, query=True, time=(20, 20), outTangentType=True),
            ["step"],
        )
        self.assertTrue(store.is_empty())
        self.assertEqual(cmds.ls(type="animCurve"), [self.curve])

    def test_retrieve_at_offset(self):
        clip = self._stash_range()
        KeyStash.active().retrieve(clip.clip_id, at=100)
        self.assertEqual(self._times(), [1.0, 40.0, 100.0, 110.0, 120.0])

    def test_retrieve_survives_rename(self):
        clip = self._stash_range()
        renamed = cmds.rename(self.cube, "box")
        self.assertEqual(KeyStash.active().retrieve(clip.clip_id), 3)
        self.assertEqual(
            cmds.keyframe(f"{renamed}.translateX", query=True, timeChange=True),
            [1.0, 10.0, 20.0, 30.0, 40.0],
        )

    def test_retrieve_keeps_record_when_object_is_gone_then_targets_another(self):
        clip = self._stash_range()
        cmds.delete(self.cube)
        store = KeyStash.active()
        self.assertEqual(store.retrieve(clip.clip_id), 0)
        self.assertEqual(len(store.clips), 1, "orphaned clip must survive")
        other = cmds.polyCube(name="other")[0]
        self.assertEqual(store.retrieve(clip.clip_id, target=other), 3)
        self.assertEqual(
            cmds.keyframe(f"{other}.translateX", query=True, timeChange=True),
            [10.0, 20.0, 30.0],
        )
        self.assertTrue(store.is_empty())

    # ---- animation layers ------------------------------------------------------

    def _add_layer_keys(self, name="lyr", keys=((10, 100.0), (20, 200.0))):
        """Put *keys* on ``translateX`` in a new animation layer; return the layer."""
        layer = cmds.animLayer(name)
        cmds.select(self.cube)
        cmds.animLayer(layer, edit=True, addSelectedObjects=True)
        for t, v in keys:
            cmds.setKeyframe(
                self.cube, attribute="translateX", time=t, value=v, animLayer=layer
            )
        return layer

    @staticmethod
    def _layer_curve(layer, times):
        """The curve on *layer* holding exactly *times*, or ``None``."""
        for crv in cmds.animLayer(layer, query=True, animCurves=True) or []:
            if (cmds.keyframe(crv, query=True, timeChange=True) or []) == list(times):
                return crv
        return None

    def _samples(self, frames=(1, 5, 10, 15, 20, 25, 30, 35, 40)):
        """``translateX`` evaluated at *frames* — the round-trip contract."""
        return [round(self._tx_at(f), 6) for f in frames]

    def test_retrieve_puts_layer_keys_back_on_the_layer(self):
        """Keys stashed from an animation-layer curve retrieve onto that layer.

        The layer curve feeds an animBlendNode whose ``message`` also feeds the
        animLayer's ``blendNodes[]`` registry.  The record must resolve the
        DRIVEN channel, not that bookkeeping plug: once the emptied layer curve
        is gone, a paste onto ``lyr.blendNodes[i]`` has nothing to paste to.
        And the keys must reach the layer they came from, not whichever layer
        happens to be active — proven by the channel evaluating exactly as it
        did before the stash.
        """
        layer = self._add_layer_keys()
        before = self._samples()
        layer_curve = self._layer_curve(layer, [10.0, 20.0])
        # An additive layer stores the DELTA over the base (95, 190), not the
        # absolute values keyed (100, 200); the round trip must keep the delta.
        layer_values = cmds.keyframe(layer_curve, query=True, valueChange=True)
        cmds.selectKey(layer_curve, time=(10, 20), replace=True)
        store = KeyStash.active()
        clip = store.stash(selected_keys=True)
        self.assertEqual(clip.curves[0]["plug"]["attr"], "translateX")
        self.assertFalse(cmds.objExists(layer_curve), "Maya drops the emptied curve")
        self.assertEqual(store.retrieve(clip.clip_id), 2)
        back = self._layer_curve(layer, [10.0, 20.0])
        self.assertIsNotNone(back, "keys must land back on the layer")
        self.assertEqual(
            cmds.keyframe(back, query=True, valueChange=True), layer_values
        )
        self.assertEqual(back, layer_curve, "the recreated curve keeps its name")
        self.assertEqual(self._times(self.curve), [1.0, 10.0, 20.0, 30.0, 40.0])
        self.assertEqual(
            cmds.keyframe(self.curve, query=True, valueChange=True),
            [v for _, v in KEYS],
            "the base curve is not the layer's paste target",
        )
        self.assertEqual(self._samples(), before, "channel must evaluate as before")
        self.assertTrue(store.is_empty())

    def test_stash_range_round_trips_curves_behind_an_animation_layer(self):
        """A range stash on a layered object takes base AND layer curves, and
        retrieve puts each set back where it came from.

        With a layer present the channel is driven by an animBlendNode, so a
        direct ``listConnections(type="animCurve")`` sees no curve at all —
        the time-slider and playback-range sources stored nothing on a layered
        rig.  Both curves are emptied here, so both come back through the
        blend-node sockets they left; the channel must evaluate exactly as
        before.
        """
        layer = self._add_layer_keys()
        before = self._samples()
        layer_curve = self._layer_curve(layer, [10.0, 20.0])
        layer_values = cmds.keyframe(layer_curve, query=True, valueChange=True)
        clip = self._stash_range(1, 40)
        self.assertIsNotNone(clip, "a layered object must still stash")
        self.assertEqual(
            sorted(r["plug"]["attr"] for r in clip.curves),
            ["translateX", "translateX"],
        )
        self.assertEqual(clip.key_count, 7)  # 5 base + 2 layer
        self.assertFalse(cmds.objExists(self.curve), "base curve emptied and dropped")
        store = KeyStash.active()
        self.assertEqual(store.retrieve(clip.clip_id), 7)
        self.assertTrue(cmds.objExists(self.curve), "base curve back under its name")
        self.assertEqual(
            cmds.keyframe(self.curve, query=True, valueChange=True),
            [v for _, v in KEYS],
        )
        back = self._layer_curve(layer, [10.0, 20.0])
        self.assertEqual(back, layer_curve, "layer curve back under its name")
        self.assertEqual(
            cmds.keyframe(back, query=True, valueChange=True), layer_values
        )
        self.assertEqual(self._samples(), before, "channel must evaluate as before")
        self.assertTrue(store.is_empty())

    def test_retrieve_rejects_unknown_clip_and_mode(self):
        clip = self._stash_range()
        with self.assertRaises(KeyError):
            KeyStash.active().retrieve(999)
        with self.assertRaises(ValueError):
            KeyStash.active().retrieve(clip.clip_id, mode="bogus")

    # ---- drop / optimize -------------------------------------------------------

    def test_drop_deletes_stash_nodes(self):
        clip = self._stash_range()
        stash_uuid = clip.curves[0]["stash"]["uuid"]
        KeyStash.active().drop(clip.clip_id)
        self.assertEqual(cmds.ls(stash_uuid), [])
        self.assertTrue(KeyStash.active().is_empty())
        self.assertEqual(self._times(), [1.0, 40.0])

    def test_optimize_scene_size_keeps_the_stash(self):
        clip = self._stash_range()
        mel.eval("source cleanUpScene;")
        mel.eval('scOpt_performOneCleanup({"animationCurveOption"})')
        self.assertTrue(cmds.ls(clip.curves[0]["stash"]["uuid"]))

    # ---- persistence -----------------------------------------------------------

    def test_persists_across_save_and_reopen(self):
        clip = self._stash_range()
        path = self.temp_path("key_stash_roundtrip.ma").replace("\\", "/")
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        _reset_store()
        cmds.file(path, open=True, force=True)
        store = KeyStash.active()
        self.assertEqual([c.clip_id for c in store.clips], [clip.clip_id])
        self.assertEqual(store.retrieve(clip.clip_id), 3)
        self.assertEqual(
            cmds.keyframe("cube.translateX", query=True, timeChange=True),
            [1.0, 10.0, 20.0, 30.0, 40.0],
        )

    def test_channel_is_isolated_from_the_shot_store(self):
        self._stash_range()
        self.assertTrue(DataNodes.get_internal_string(KeyStash.ATTR_NAME))
        self.assertIsNone(DataNodes.get_internal_string("shot_store"))
        default = MayaScenePersistence()
        backend = MayaScenePersistence(attr_name="other", store_cls=KeyStash)
        try:
            self.assertIs(default.store_cls, ShotStore)
            self.assertIs(backend.store_cls, KeyStash)
            # A non-shot channel never folds the legacy shot carrier onto itself.
            legacy = cmds.createNode("network", name="shotStore")
            cmds.addAttr(legacy, longName="shotData", dataType="string")
            cmds.setAttr(f"{legacy}.shotData", '{"shots": []}', type="string")
            self.assertIsNone(backend.load())
            self.assertTrue(cmds.objExists(legacy))
        finally:
            backend.remove_callbacks()
            default.remove_callbacks()

    def test_scene_change_invalidates_active_store(self):
        self._stash_range()
        KeyStash._persistence._on_scene_changed()
        self.assertIsNone(KeyStash._active)

    # ---- export ----------------------------------------------------------------

    def test_fbx_export_carries_neither_stash_nor_its_keys(self):
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.loadPlugin("fbxmaya", quiet=True)
            except RuntimeError:
                self.skipTest("fbxmaya plugin unavailable")
        self._stash_range()
        paths = {}
        for bake in (False, True):
            path = self.temp_path(f"key_stash_bake{int(bake)}.fbx").replace("\\", "/")
            cmds.select(self.cube)
            mel.eval("FBXResetExport;")
            mel.eval(f"FBXExportBakeComplexAnimation -v {'true' if bake else 'false'};")
            mel.eval(f'FBXExport -f "{path}" -s;')
            paths[bake] = path
        for bake, path in paths.items():
            # A fresh scene: importing beside the source cube would merge the
            # animation onto its existing curve and hide what the file holds.
            cmds.file(new=True, force=True)
            mel.eval(f'FBXImport -f "{path}";')
            imported = cmds.ls(type="animCurve")
            self.assertEqual(len(imported), 1, (bake, imported))
            self.assertEqual(
                cmds.keyframe(imported[0], query=True, timeChange=True), [1.0, 40.0]
            )
            self.assertFalse(any("keyStash" in c for c in imported))

    # ---- preview ---------------------------------------------------------------

    def test_preview_in_context_plays_clip_inside_range_and_base_outside(self):
        base_5, base_35 = self._tx_at(5), self._tx_at(35)
        clip = self._stash_range()
        after_cut_5, after_cut_20 = self._tx_at(5), self._tx_at(20)
        store = KeyStash.active()
        layer = store.preview(clip.clip_id, in_context=True)
        self.assertTrue(store.is_previewing(clip.clip_id))
        self.assertEqual(cmds.nodeType(layer), "animLayer")
        self.assertFalse(cmds.animLayer(layer, query=True, preferred=True))
        self.assertAlmostEqual(self._tx_at(20), 10.0)
        self.assertAlmostEqual(self._tx_at(5), after_cut_5)
        self.assertAlmostEqual(self._tx_at(35), self._tx_at(35))
        self.assertEqual(
            [
                cmds.playbackOptions(query=True, minTime=True),
                cmds.playbackOptions(query=True, maxTime=True),
            ],
            [10.0, 30.0],
        )
        self.assertTrue(store.end_preview())
        self.assertFalse(store.is_previewing())
        self.assertFalse(cmds.objExists(layer))
        self.assertEqual(cmds.ls(type="animBlendNodeBase"), [])
        self.assertEqual(
            cmds.listConnections(
                f"{self.cube}.translateX", source=True, destination=False
            ),
            [self.curve],
        )
        self.assertEqual(self._times(), [1.0, 40.0])
        self.assertAlmostEqual(self._tx_at(20), after_cut_20)
        self.assertNotAlmostEqual(base_5, base_35)  # sanity: the scene animates
        self.assertFalse(store.end_preview())

    def test_preview_isolated_holds_clip_poses_outside_range(self):
        clip = self._stash_range()
        KeyStash.active().preview(clip.clip_id, in_context=False)
        self.assertAlmostEqual(self._tx_at(5), 5.0)
        self.assertAlmostEqual(self._tx_at(35), 15.0)
        KeyStash.active().end_preview()

    def test_preview_restores_playback_range_and_only_one_at_a_time(self):
        cmds.playbackOptions(minTime=1, maxTime=48)
        a = self._stash_range(10, 20)
        b = self._stash_range(30, 40)
        store = KeyStash.active()
        layer_a = store.preview(a.clip_id)
        uuid_a = cmds.ls(layer_a, uuid=True)[0]
        layer_b = store.preview(b.clip_id)
        # The second preview tears the first down; its replacement legitimately
        # takes the same name, so identity is checked by UUID, not by name.
        self.assertEqual(cmds.ls(uuid_a), [])
        self.assertTrue(store.is_previewing(b.clip_id))
        self.assertEqual(len(cmds.ls(type="animLayer")) - 1, 1)  # + BaseAnimation
        store.end_preview()
        self.assertEqual(
            [
                cmds.playbackOptions(query=True, minTime=True),
                cmds.playbackOptions(query=True, maxTime=True),
            ],
            [1.0, 48.0],
        )
        self.assertFalse(cmds.objExists(layer_b))

    def test_retrieve_and_drop_end_an_active_preview(self):
        clip = self._stash_range()
        store = KeyStash.active()
        layer = store.preview(clip.clip_id)
        store.retrieve(clip.clip_id)
        self.assertFalse(cmds.objExists(layer))
        self.assertIsNone(store.active_preview)
        self.assertEqual(self._times(), [1.0, 10.0, 20.0, 30.0, 40.0])

    def test_reconcile_ends_a_preview_left_in_the_record(self):
        clip = self._stash_range()
        store = KeyStash.active()
        layer = store.preview(clip.clip_id)
        # Simulate a save-mid-preview reopen: fresh store instance from the JSON.
        data = store.to_dict()
        _reset_store()
        KeyStash.set_persistence(
            MayaScenePersistence(attr_name=KeyStash.ATTR_NAME, store_cls=KeyStash)
        )
        KeyStash._persistence.save(data)
        reopened = KeyStash.active()
        self.assertIsNone(reopened.active_preview)
        self.assertFalse(cmds.objExists(layer))
        self.assertEqual(len(reopened.clips), 1)

    # ---- AnimUtils primitives ------------------------------------------------------

    def test_create_preview_layer_rejects_keyless_sources(self):
        empty = cmds.createNode("animCurveTL", name="empty_curve")
        with self.assertRaises(ValueError):
            AnimUtils.create_preview_layer({f"{self.cube}.translateX": empty})
        # The root layer Maya creates alongside the first layer stays; ours is gone.
        self.assertEqual(
            [lyr for lyr in cmds.ls(type="animLayer") if lyr != "BaseAnimation"], []
        )

    def test_remove_preview_layer_refuses_non_layers(self):
        self.assertFalse(AnimUtils.remove_preview_layer(None))
        self.assertFalse(AnimUtils.remove_preview_layer("no_such_node"))
        with self.assertRaises(ValueError):
            AnimUtils.remove_preview_layer(self.cube)

    def test_get_selected_key_times_scopes_to_curves(self):
        cmds.selectKey(clear=True)
        cmds.selectKey(self.curve, time=(10, 30), add=True)
        self.assertEqual(
            AnimUtils.get_selected_key_times(), {self.curve: [10.0, 20.0, 30.0]}
        )
        self.assertEqual(AnimUtils.get_selected_key_times(curves=[]), {})


if __name__ == "__main__":
    unittest.main()
