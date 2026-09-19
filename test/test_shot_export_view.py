# !/usr/bin/python
# coding=utf-8
"""Tests for shot → FBX export view: clip naming, metadata schema, and the
FBX takes + metadata round-trip through an ASCII export."""

import os
import pythontk as ptk
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


def _clips(view):
    """The clip names of an export view, in order (the join keys)."""
    return [s["clip"] for s in view["shot_metadata"]["shots"]]


def _takes(view):
    """The take list a reader derives from an export view -- through the ONE
    reader of the take list, as the FBX split and the GLB appliers do."""
    return ptk.SceneRecords.declared_takes(view.get)


def _export_selected_ascii(nodes, fname="mtk_test_export.fbx"):
    """Export *nodes* to an ASCII FBX (the path ``perform_export`` uses), return
    its text, and delete the file.  Any take/option state set beforehand is
    honored, so callers assert on what actually landed on disk.

    The filename is qualified by PID and lands in the harness's own
    ``temp_tests/`` rather than the shared system temp dir.  A FIXED path in
    ``gettempdir()`` is one file for the whole machine: the runner chunks a
    scoped run across concurrent mayapy processes, so two of them exported
    over each other and the read-back was the OTHER process's FBX -- which is
    how this module failed inside a multi-module run and passed on its own.
    """
    stem, ext = os.path.splitext(fname)
    out = MayaTkTestCase.temp_path(f"{stem}_{os.getpid()}{ext}")
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
    """Pure serializer logic — single name resolution, one record per clip."""

    def test_default_name_only_sanitized(self):
        store = _store_with(
            [ShotBlock(0, "Intro", 1, 100), ShotBlock(1, "Door Open", 101, 200)]
        )
        self.assertEqual(_clips(store.to_export_view()), ["Intro", "Door_Open"])

    def test_sequence_strategy(self):
        store = _store_with(
            [ShotBlock(0, "Intro", 1, 100), ShotBlock(1, "Outro", 101, 200)]
        )
        names = _clips(store.to_export_view(strategy="sequence"))
        self.assertEqual(names, ["010_Intro", "020_Outro"])

    def test_collision_dedupe_is_deterministic(self):
        store = _store_with(
            [
                ShotBlock(0, "Shot", 1, 10),
                ShotBlock(1, "Shot", 11, 20),
                ShotBlock(2, "Shot!", 21, 30),  # also sanitizes to "Shot"
            ]
        )
        names = _clips(store.to_export_view())
        self.assertEqual(names, ["Shot", "Shot_1", "Shot_2"])

    def test_clip_keys_are_unity_safe_for_stressing_names(self):
        # The clip name is the Maya take name AND the Unity AnimationClip name
        # AND the metadata join key.  Names must reduce to strictly [A-Za-z0-9_]
        # (what Unity preserves verbatim on FBX import) for the join to hold —
        # spaces, punctuation, unicode, and collisions must not leak through.
        store = _store_with(
            [
                ShotBlock(0, "Shot 01", 1, 10),  # space
                ShotBlock(1, "Shot 01", 11, 20),  # exact collision
                ShotBlock(2, "Fade-In!", 21, 30),  # punctuation
                ShotBlock(3, "café/wipe", 31, 40),  # unicode + slash
            ]
        )
        view = store.to_export_view()
        keys = [t["name"] for t in _takes(view)]

        for k in keys:
            self.assertRegex(k, r"^[A-Za-z0-9_]+$", f"{k!r} is not Unity-safe")
        # Single-resolution invariant: the take a reader derives == the clip
        # join key, in order -- the clip entry IS the take.
        self.assertEqual(keys, _clips(view))
        # Collisions de-duped to distinct keys (no silent clip overwrite in Unity).
        self.assertEqual(len(set(keys)), len(keys))

    def test_sequence_leading_digit_key_is_unity_safe(self):
        # The 'sequence' strategy prefixes NN_, producing a leading digit — still
        # a legal AnimationClip name; confirm it stays strictly [A-Za-z0-9_].
        store = _store_with([ShotBlock(0, "Wipe Out!", 1, 10)])
        (key,) = _clips(store.to_export_view(strategy="sequence"))
        self.assertEqual(key, "010_Wipe_Out")
        self.assertRegex(key, r"^[A-Za-z0-9_]+$")

    def test_join_key_matches_take_name(self):
        store = _store_with([ShotBlock(0, "A B", 1, 10, description="hi")])
        view = store.to_export_view()
        self.assertEqual(_takes(view)[0]["name"], _clips(view)[0])
        self.assertEqual(_clips(view), ["A_B"])

    def test_each_clip_carries_its_own_range_in_one_record(self):
        """The ranges ride the clips, so the take list cannot disagree with the
        join keys: there is no second list (``fbx_takes`` is no longer
        written) and no ``takes`` key on the record."""
        store = _store_with([ShotBlock(0, "A", 5, 25, description="d")])
        view = store.to_export_view()
        self.assertEqual(list(view), ["shot_metadata"])
        meta = view["shot_metadata"]
        self.assertEqual(meta["version"], 1)
        self.assertNotIn("takes", meta)
        (rec,) = meta["shots"]
        self.assertEqual((rec["clip"], rec["start"], rec["end"]), ("A", 5, 25))
        self.assertEqual(rec["description"], "d")
        self.assertEqual(_takes(view), [{"name": "A", "start": 5, "end": 25}])

    def test_empty_description_and_section_are_omitted_objects_are_not(self):
        """Unity's ShotRecord reads ``objects`` as an array, so it is always
        present; an empty ``description`` / ``section`` is left out."""
        store = _store_with([ShotBlock(0, "A", 1, 10)])
        (rec,) = store.to_export_view()["shot_metadata"]["shots"]
        self.assertEqual(set(rec), {"clip", "start", "end", "objects"})
        self.assertEqual(rec["objects"], [])

    def test_objects_reduced_to_leaf_names(self):
        store = _store_with(
            [ShotBlock(0, "A", 1, 10, objects=["|grp|door_L", "|grp|door_R"])]
        )
        rec = store.to_export_view()["shot_metadata"]["shots"][0]
        self.assertEqual(rec["objects"], ["door_L", "door_R"])

    def test_empty_store(self):
        view = _store_with([]).to_export_view()
        self.assertEqual(list(view), ["shot_metadata"])
        self.assertEqual(view["shot_metadata"]["shots"], [])
        self.assertEqual(_takes(view), [])
        self.assertIsNone(_store_with([]).export_records(), "an empty store clears")

    def test_export_records_is_the_one_shot_record_with_the_clip_mode(self):
        store = _store_with([ShotBlock(0, "A", 1, 10)])
        records = store.export_records(ptk.ExportContext(clip_mode="shots"))
        self.assertEqual([r.key for r in records], [ptk.SceneRecords.SHOTS.key])
        self.assertEqual(records[0].payload["clip_mode"], "shots")
        self.assertEqual([s["clip"] for s in records[0].payload["shots"]], ["A"])

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
        ShotStore._auto_export_disabled = False  # session opt-out — start clean

    def tearDown(self):
        # Authoring a store auto-registers the "shots" preparer — tear the
        # session hook down so it can't leak into other suites.
        ShotStore.disable_auto_export()
        ShotStore._auto_export_disabled = False
        ShotStore.clear_active()
        super().tearDown()

    def test_publish_export_view_writes_channels(self):
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")
        store.define_shot("Outro", 51, 100)

        store.publish_export_view()

        self.assertNodeExists(DataNodes.EXPORT)
        meta_raw = DataNodes.read(ptk.Scope.DELIVERABLE, DataNodes.SHOT_METADATA)
        self.assertIn("opening", meta_raw)
        # ONE record: each clip carries its range; the take list is not
        # written beside it (the attr is never even created).
        meta = ptk.SceneRecords.SHOTS.load(DataNodes)
        self.assertEqual(
            [(s["clip"], s["start"], s["end"]) for s in meta["shots"]],
            [("Intro", 1, 50), ("Outro", 51, 100)],
        )
        self.assertFalse(
            cmds.attributeQuery(DataNodes.FBX_TAKES, node=DataNodes.EXPORT, exists=True)
        )

    def test_refresh_export_view_publishes_with_shots(self):
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")
        ShotStore.refresh_export_view()  # canonical no-arg preparer
        self.assertNodeExists(DataNodes.EXPORT)
        self.assertIn("opening", DataNodes.read(ptk.Scope.DELIVERABLE, DataNodes.SHOT_METADATA))

    def test_refresh_export_view_noop_without_shots(self):
        ShotStore.set_active(ShotStore())  # active but empty
        ShotStore.refresh_export_view()
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))  # no empty carrier left

    def test_refresh_export_view_clears_stale_channels(self):
        """Deleting the last shot must clear the published channels — stale
        takes must not ride into the next export."""
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50)
        store.publish_export_view()
        self.assertIsNotNone(
            DataNodes.read(ptk.Scope.DELIVERABLE, DataNodes.SHOT_METADATA)
        )

        store.remove_shot(store.shots[0].shot_id)
        ShotStore.refresh_export_view()
        self.assertIsNone(DataNodes.read(ptk.Scope.DELIVERABLE, DataNodes.SHOT_METADATA))
        self.assertEqual(FbxUtils.apply_takes_from_node(), 0)

    def test_a_legacy_take_list_is_read_until_the_next_publish_clears_it(self):
        """A scene published before the ranges moved onto the clips carries the
        ``fbx_takes`` list and no ranged clip: it still realizes its takes (the
        one reader, ``SceneRecords.declared_takes``, falls back to it), and the
        next shots publish clears it -- the successor was produced, so the old
        list must not ride beside it into the next export. An EMPTY store's
        publish clears it too."""
        ptk.SceneRecords.FBX_TAKES.save(
            DataNodes, [{"name": "Legacy", "start": 1, "end": 20}]
        )
        self.assertEqual(FbxUtils.apply_takes_from_node(), 1)
        q = mel.eval("FBXExportSplitAnimationIntoTakes -q") or []
        self.assertTrue(any("Legacy" in x for x in q), q)
        FbxUtils.reset_takes()

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50)
        store.publish_export_view()
        self.assertIsNone(DataNodes.read(ptk.Scope.DELIVERABLE, DataNodes.FBX_TAKES))
        self.assertEqual(FbxUtils.apply_takes_from_node(), 1)
        q = mel.eval("FBXExportSplitAnimationIntoTakes -q") or []
        self.assertFalse(any("Legacy" in x for x in q), q)

        ptk.SceneRecords.FBX_TAKES.save(
            DataNodes, [{"name": "Legacy", "start": 1, "end": 20}]
        )
        ShotStore.set_active(ShotStore())
        ShotStore.refresh_export_view()
        self.assertIsNone(DataNodes.read(ptk.Scope.DELIVERABLE, DataNodes.FBX_TAKES))
        self.assertEqual(FbxUtils.apply_takes_from_node(), 0)

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
            # Authoring the store auto-registered the "shots" preparer, which
            # also holds the hook — drop it too before asserting teardown.
            ShotStore.disable_auto_export()
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
            self.assertIn(
                "LateAdd", DataNodes.read(ptk.Scope.DELIVERABLE, DataNodes.SHOT_METADATA)
            )
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

    def test_exporter_task_adds_nothing_when_no_shots_are_declared(self):
        """The task is default-on, so its no-op has to be a REAL no-op.

        The carrier ships with the clips its metadata names. Folded in before
        the take count was known, this task handed ``data_export`` back to a
        user who had deliberately unchecked *Export Scene Data Node* — on a
        scene with no shots at all, where there is nothing to describe.
        """
        import logging
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        cube = self.create_test_cube("noShotCube")
        # A carrier EXISTS (another producer's channel) but declares no takes,
        # which is the only case that can tell the two orderings apart.
        DataNodes.write(ptk.Scope.DELIVERABLE, "lightmap_metadata", '{"records": []}')
        ShotStore.set_active(ShotStore())

        tm = TaskManager(logging.getLogger("test_takes_noop"))
        tm.objects = [cmds.ls(cube, long=True)[0]]
        FbxUtils.reset_takes()

        tm.apply_declared_takes()

        self.assertFalse(
            any(o.endswith(DataNodes.EXPORT) for o in tm.objects),
            "the carrier joined the export set without a take to justify it",
        )
        self.assertFalse(mel.eval("FBXExportSplitAnimationIntoTakes -q"))

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
        # temp_tests/, PID-qualified: the harness owns teardown there, and a
        # scoped run's concurrent processes cannot collide on the name.
        path = self.temp_path(f"shot_export_view_{os.getpid()}.csv")
        with open(path, "w", encoding="utf-8") as f:
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

        # Export view carries it, keyed by clip name, each clip with its range.
        view = store.to_export_view()
        self.assertEqual(
            [(t["name"], t["start"], t["end"]) for t in _takes(view)],
            [("A01", 1, 50), ("A02", 51, 100)],
        )
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
