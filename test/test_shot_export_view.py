# !/usr/bin/python
# coding=utf-8
"""Tests for shot → FBX export view: clip naming, metadata schema, and the
FBX takes + metadata round-trip through an ASCII export."""
import os
import tempfile
import unittest

from base_test import MayaTkTestCase, QuickTestCase

import maya.cmds as cmds
from maya import mel

from mayatk.anim_utils.shots._shots import ShotStore, ShotBlock, resolve_clip_specs
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.env_utils.fbx_utils import FbxUtils


def _store_with(shots):
    store = ShotStore()
    store.shots = list(shots)
    return store


def _export_selected_ascii(nodes, fname="mtk_test_export.fbx"):
    """Export *nodes* to an ASCII FBX (the path ``perform_export`` uses), return
    its text, and delete the file.  Any take/option state set beforehand is
    honored, so callers assert on what actually landed on disk."""
    out = os.path.join(tempfile.gettempdir(), fname)
    try:
        mel.eval("FBXExportInAscii -v true")
        cmds.select(list(nodes), replace=True)
        cmds.file(
            out, force=True, options="v=0;", type="FBX export", exportSelected=True
        )
        with open(out, encoding="utf-8", errors="ignore") as f:
            return f.read()
    finally:
        if os.path.exists(out):
            os.remove(out)


class TestExportViewLogic(QuickTestCase):
    """Pure serializer logic — single name resolution, minimal overlap."""

    def test_default_name_only_sanitized(self):
        store = _store_with(
            [ShotBlock(0, "Intro", 1, 100), ShotBlock(1, "Door Open", 101, 200)]
        )
        names = [t["name"] for t in store.to_export_view()["fbx_takes"]]
        self.assertEqual(names, ["Intro", "Door_Open"])

    def test_sequence_strategy(self):
        store = _store_with(
            [ShotBlock(0, "Intro", 1, 100), ShotBlock(1, "Outro", 101, 200)]
        )
        names = [t["name"] for t in store.to_export_view(strategy="sequence")["fbx_takes"]]
        self.assertEqual(names, ["010_Intro", "020_Outro"])

    def test_collision_dedupe_is_deterministic(self):
        store = _store_with(
            [
                ShotBlock(0, "Shot", 1, 10),
                ShotBlock(1, "Shot", 11, 20),
                ShotBlock(2, "Shot!", 21, 30),  # also sanitizes to "Shot"
            ]
        )
        names = [t["name"] for t in store.to_export_view()["fbx_takes"]]
        self.assertEqual(names, ["Shot", "Shot_1", "Shot_2"])

    def test_clip_keys_are_unity_safe_for_stressing_names(self):
        # The clip name is the Maya take name AND the Unity AnimationClip name
        # AND the metadata join key.  Names must reduce to strictly [A-Za-z0-9_]
        # (what Unity preserves verbatim on FBX import) for the join to hold —
        # spaces, punctuation, unicode, and collisions must not leak through.
        import re

        store = _store_with(
            [
                ShotBlock(0, "Shot 01", 1, 10),  # space
                ShotBlock(1, "Shot 01", 11, 20),  # exact collision
                ShotBlock(2, "Fade-In!", 21, 30),  # punctuation
                ShotBlock(3, "café/wipe", 31, 40),  # unicode + slash
            ]
        )
        view = store.to_export_view()
        keys = [t["name"] for t in view["fbx_takes"]]

        for k in keys:
            self.assertRegex(k, r"^[A-Za-z0-9_]+$", f"{k!r} is not Unity-safe")
        # Single-resolution invariant: metadata clip == take name, in order.
        self.assertEqual(keys, [s["clip"] for s in view["shot_metadata"]["shots"]])
        # Collisions de-duped to distinct keys (no silent clip overwrite in Unity).
        self.assertEqual(len(set(keys)), len(keys))

    def test_sequence_leading_digit_key_is_unity_safe(self):
        # The 'sequence' strategy prefixes NN_, producing a leading digit — still
        # a legal AnimationClip name; confirm it stays strictly [A-Za-z0-9_].
        store = _store_with([ShotBlock(0, "Wipe Out!", 1, 10)])
        key = store.to_export_view(strategy="sequence")["fbx_takes"][0]["name"]
        self.assertEqual(key, "010_Wipe_Out")
        self.assertRegex(key, r"^[A-Za-z0-9_]+$")

    def test_join_key_matches_take_name(self):
        store = _store_with([ShotBlock(0, "A B", 1, 10, description="hi")])
        view = store.to_export_view()
        self.assertEqual(
            view["fbx_takes"][0]["name"], view["shot_metadata"]["shots"][0]["clip"]
        )
        self.assertEqual(view["fbx_takes"][0]["name"], "A_B")

    def test_minimal_overlap_range_only_in_takes(self):
        store = _store_with([ShotBlock(0, "A", 5, 25, description="d")])
        view = store.to_export_view()
        rec = view["shot_metadata"]["shots"][0]
        self.assertEqual(view["shot_metadata"]["version"], 1)
        self.assertNotIn("start", rec)
        self.assertNotIn("end", rec)
        take = view["fbx_takes"][0]
        self.assertEqual((take["start"], take["end"]), (5, 25))

    def test_objects_reduced_to_leaf_names(self):
        store = _store_with(
            [ShotBlock(0, "A", 1, 10, objects=["|grp|door_L", "|grp|door_R"])]
        )
        rec = store.to_export_view()["shot_metadata"]["shots"][0]
        self.assertEqual(rec["objects"], ["door_L", "door_R"])

    def test_empty_store(self):
        view = _store_with([]).to_export_view()
        self.assertEqual(view["fbx_takes"], [])
        self.assertEqual(view["shot_metadata"]["shots"], [])

    def test_resolve_clip_specs_orders_and_rounds(self):
        specs = resolve_clip_specs(
            [ShotBlock(0, "A", 1.4, 10.6), ShotBlock(1, "B", 11, 20)]
        )
        self.assertEqual(specs, [("A", 1, 11), ("B", 11, 20)])


class TestExportRoundTrip(MayaTkTestCase):
    """Channels published to data_export and the takes/metadata reaching an FBX."""

    def setUp(self):
        super().setUp()
        FbxUtils.reset_takes()  # global FBX state — start clean
        ShotStore.clear_active()

    def tearDown(self):
        ShotStore.clear_active()
        super().tearDown()

    def test_publish_export_view_writes_channels(self):
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")
        store.define_shot("Outro", 51, 100)

        store.publish_export_view()

        self.assertNodeExists(DataNodes.EXPORT)
        takes_raw = DataNodes.get_export_string(DataNodes.FBX_TAKES)
        meta_raw = DataNodes.get_export_string(DataNodes.SHOT_METADATA)
        self.assertIn("Intro", takes_raw)
        self.assertIn("opening", meta_raw)

    def test_refresh_export_view_publishes_with_shots(self):
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")
        ShotStore.refresh_export_view()  # canonical no-arg preparer
        self.assertNodeExists(DataNodes.EXPORT)
        self.assertIn("opening", DataNodes.get_export_string(DataNodes.SHOT_METADATA))

    def test_refresh_export_view_noop_without_shots(self):
        ShotStore.set_active(ShotStore())  # active but empty
        ShotStore.refresh_export_view()
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))  # no empty carrier left

    def test_apply_takes_from_node(self):
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50)
        store.define_shot("Outro", 51, 100)
        store.publish_export_view()

        n = FbxUtils.apply_takes_from_node()
        self.assertEqual(n, 2)
        q = mel.eval("FBXExportSplitAnimationIntoTakes -q") or []
        self.assertTrue(any("Intro" in x for x in q))

    def test_no_shots_yields_no_takes(self):
        ShotStore.set_active(ShotStore())
        self.assertEqual(FbxUtils.apply_takes_from_node(), 0)

    def test_auto_takes_hook_applies_on_plain_export(self):
        cube = self.create_test_cube("hookCube")
        for t, v in ((1, 0), (50, 5), (100, 0)):
            cmds.setKeyframe(cube, attribute="translateX", t=t, v=v)
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50)
        store.define_shot("Outro", 51, 100)
        store.publish_export_view()

        FbxUtils.enable_auto_takes()
        try:
            self.assertTrue(FbxUtils.is_auto_takes_enabled())
            FbxUtils.reset_takes()  # none predefined — the hook must set them

            text = _export_selected_ascii([cube, DataNodes.EXPORT], "mtk_hook.fbx")

            self.assertIn("Intro", text)  # take set by the before-export hook
            self.assertIn("Outro", text)
            # kAfterExport cleared global take state.
            self.assertFalse(mel.eval("FBXExportSplitAnimationIntoTakes -q"))
        finally:
            FbxUtils.disable_auto_takes()
        self.assertFalse(FbxUtils.is_auto_takes_enabled())

    def test_enable_auto_export_republishes_fresh(self):
        cube = self.create_test_cube("freshCube")
        for t, v in ((1, 0), (50, 5), (100, 0)):
            cmds.setKeyframe(cube, attribute="translateX", t=t, v=v)
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50)
        store.publish_export_view()  # initial projection: only "Intro"

        ShotStore.enable_auto_export()
        try:
            self.assertTrue(FbxUtils.is_auto_takes_enabled())
            # Mutate AFTER enabling and WITHOUT republishing — the before-export
            # hook must regenerate both channels from the live store, so the late
            # shot can't be missing (the staleness fix).
            store.define_shot("LateAdd", 51, 100)
            FbxUtils.reset_takes()

            text = _export_selected_ascii([cube, DataNodes.EXPORT], "mtk_fresh.fbx")

            self.assertIn("LateAdd", text)  # fresh take, not the stale node
            self.assertIn("LateAdd", DataNodes.get_export_string(DataNodes.SHOT_METADATA))
        finally:
            ShotStore.disable_auto_export()
        self.assertFalse(FbxUtils.is_auto_takes_enabled())

    def test_exporter_task_publishes_includes_node_and_applies(self):
        import logging
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        cube = self.create_test_cube("taskCube")
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=50, v=5)

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50)

        tm = TaskManager(logging.getLogger("test_takes"))
        tm.objects = [cmds.ls(cube, long=True)[0]]
        FbxUtils.reset_takes()

        tm.apply_declared_takes()

        # data_export carrier added to the export set, and takes realized.
        self.assertTrue(any(o.endswith(DataNodes.EXPORT) for o in tm.objects))
        self.assertTrue(mel.eval("FBXExportSplitAnimationIntoTakes -q"))

    def test_full_roundtrip_ascii_fbx(self):
        cube = self.create_test_cube("rtCube")
        for t, v in ((1, 0), (50, 5), (100, 0)):
            cmds.setKeyframe(cube, attribute="translateX", t=t, v=v)

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, objects=[cube], description="opening")
        store.define_shot("Outro", 51, 100, description="closing")
        store.publish_export_view()

        self.assertEqual(FbxUtils.apply_takes_from_node(), 2)

        text = _export_selected_ascii([cube, DataNodes.EXPORT], "mtk_rt.fbx")

        self.assertIn("Intro", text)
        self.assertIn("Outro", text)
        self.assertIn("shot_metadata", text)  # metadata attr name survives
        self.assertIn("opening", text)  # metadata value survives


class TestCsvToFbxPipeline(MayaTkTestCase):
    """End-to-end: shot-manifest CSV → ShotStore → exporter task → FBX.

    Verifies the full chain a Unity import depends on: shot names become
    AnimStacks, and the CSV-sourced description/section land in the embedded
    ``shot_metadata``.
    """

    CSV = (
        "SECTION A: Intro Sequence\n"
        "Step,Step Contents,Asset Names,Voice Support\n"
        "A01.),Open the hangar doors,door_L,Doors opening\n"
        "A02.),Raise the platform,platform,Platform rising\n"
    )

    def setUp(self):
        super().setUp()
        FbxUtils.reset_takes()
        ShotStore.clear_active()

    def tearDown(self):
        ShotStore.clear_active()
        super().tearDown()

    def _write_csv(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.CSV)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_csv_to_fbx_carries_names_descriptions_sections(self):
        from mayatk.anim_utils.shots.shot_manifest._shot_manifest import ShotManifest
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager
        import logging

        # Scene objects matching the CSV asset names, animated.
        door = self.create_test_cube("door_L")
        plat = self.create_test_cube("platform")
        for obj in (door, plat):
            cmds.setKeyframe(obj, attribute="translateY", t=1, v=0)
            cmds.setKeyframe(obj, attribute="translateY", t=100, v=5)

        # CSV → ShotStore (the real manifest ingestion path).
        store = ShotStore()
        ShotStore.set_active(store)
        builder, steps = ShotManifest.from_csv(self._write_csv(), store)
        builder.sync(
            steps,
            apply_behaviors=False,
            ranges={"A01": (1.0, 50.0), "A02": (51.0, 100.0)},
        )

        # Store populated from the CSV.
        self.assertEqual({s.name for s in store.shots}, {"A01", "A02"})
        a01 = store.shot_by_name("A01")
        self.assertEqual(a01.description, "Open the hangar doors")
        self.assertEqual(a01.metadata.get("section"), "A")

        # Export view carries it, keyed by clip name.
        view = store.to_export_view()
        self.assertEqual([t["name"] for t in view["fbx_takes"]], ["A01", "A02"])
        rec = view["shot_metadata"]["shots"][0]
        self.assertEqual(rec["clip"], "A01")
        self.assertEqual(rec["description"], "Open the hangar doors")
        self.assertEqual(rec["section"], "A")

        # Exporter task: publish → include carrier → realize takes.
        tm = TaskManager(logging.getLogger("test_pipeline"))
        tm.objects = cmds.ls([door, plat], long=True)
        tm.apply_declared_takes()
        self.assertTrue(any(o.endswith(DataNodes.EXPORT) for o in tm.objects))

        # Export to ASCII FBX and confirm everything Unity needs is present.
        text = _export_selected_ascii(tm.objects, "mtk_pipeline.fbx")

        # Two named AnimStacks (→ Unity clips).
        self.assertIn("A01", text)
        self.assertIn("A02", text)
        # Embedded metadata: attr name + CSV-sourced description + section field
        # all survive inside the FBX user-property JSON (escaping-agnostic).
        self.assertIn("shot_metadata", text)
        self.assertIn("Open the hangar doors", text)
        self.assertIn("section", text)


if __name__ == "__main__":
    unittest.main()
