# !/usr/bin/python
# coding=utf-8
"""Tests for ``DataNodes`` — shared scene data node management.

Covers node creation, idempotency, and the internal/export
string channels.
"""
import unittest
import json

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from base_test import MayaTkTestCase
from mayatk.node_utils.data_nodes import DataNodes


# ── ensure_internal ──────────────────────────────────────────────────────


class TestEnsureInternal(MayaTkTestCase):
    """DataNodes.ensure_internal() creates and returns the network node."""

    def test_creates_network_node(self):
        node = DataNodes.ensure_internal()
        self.assertTrue(cmds.objExists(DataNodes.INTERNAL))
        self.assertEqual(cmds.nodeType(str(node)), "network")

    def test_idempotent(self):
        n1 = DataNodes.ensure_internal()
        n2 = DataNodes.ensure_internal()
        self.assertEqual(str(n1), str(n2))

    def test_name_is_locked(self):
        DataNodes.ensure_internal()
        locked = cmds.lockNode(DataNodes.INTERNAL, q=True, lockName=True)[0]
        self.assertTrue(locked, "Node name should be locked")

    def test_node_is_not_fully_locked(self):
        """Attrs must be writable — node itself should not be locked."""
        DataNodes.ensure_internal()
        locked = cmds.lockNode(DataNodes.INTERNAL, q=True, lock=True)[0]
        self.assertFalse(locked, "Node should not be fully locked")

    def test_migrates_fully_locked_node(self):
        """Old scenes may have the node fully locked — ensure_internal unlocks."""
        node = cmds.createNode("network", name=DataNodes.INTERNAL)
        cmds.lockNode(str(node), lock=True)
        result = DataNodes.ensure_internal()
        locked = cmds.lockNode(str(result), q=True, lock=True)[0]
        self.assertFalse(locked)


# ── ensure_export ────────────────────────────────────────────────────────


class TestEnsureExport(MayaTkTestCase):
    """DataNodes.ensure_export() creates and returns the locked transform."""

    def test_creates_transform(self):
        node = DataNodes.ensure_export()
        self.assertTrue(cmds.objExists(DataNodes.EXPORT))
        self.assertEqual(cmds.nodeType(str(node)), "transform")

    def test_idempotent(self):
        n1 = DataNodes.ensure_export()
        n2 = DataNodes.ensure_export()
        self.assertEqual(str(n1), str(n2))

    def test_has_locator_shape(self):
        DataNodes.ensure_export()
        shapes = cmds.listRelatives(DataNodes.EXPORT, shapes=True) or []
        self.assertTrue(len(shapes) > 0, "Should have a locator shape")
        self.assertEqual(cmds.nodeType(shapes[0]), "locator")

    def test_locator_stamped(self):
        DataNodes.ensure_export()
        shapes = cmds.listRelatives(DataNodes.EXPORT, shapes=True) or []
        self.assertTrue(
            cmds.attributeQuery(DataNodes._LOCATOR_ATTR, node=shapes[0], exists=True),
            "Locator shape should be stamped with marker attr",
        )

    def test_transform_channels_locked(self):
        DataNodes.ensure_export()
        for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
            self.assertTrue(
                cmds.getAttr(f"{DataNodes.EXPORT}.{attr}", lock=True),
                f"{attr} should be locked",
            )

    def test_name_is_locked(self):
        DataNodes.ensure_export()
        locked = cmds.lockNode(DataNodes.EXPORT, q=True, lockName=True)[0]
        self.assertTrue(locked)

    def test_hidden_in_outliner(self):
        """The carrier is pipeline plumbing — it must never draw an Outliner row."""
        DataNodes.ensure_export()
        self.assertTrue(cmds.getAttr(f"{DataNodes.EXPORT}.hiddenInOutliner"))
        for shape in cmds.listRelatives(DataNodes.EXPORT, shapes=True) or []:
            self.assertTrue(
                cmds.getAttr(f"{shape}.hiddenInOutliner"),
                f"{shape} should also be hidden (Outliner 'Show Shapes')",
            )

    def test_heals_visible_outliner_node(self):
        """Scenes authored before the flag existed get hidden on next ensure."""
        cmds.group(empty=True, name=DataNodes.EXPORT)
        cmds.setAttr(f"{DataNodes.EXPORT}.hiddenInOutliner", 0)
        DataNodes.ensure_export()
        self.assertTrue(cmds.getAttr(f"{DataNodes.EXPORT}.hiddenInOutliner"))

    def test_heals_unprotected_existing_node(self):
        """A pre-existing plain transform (hand-authored or imported) heals to
        the full protection contract on ensure — locator shape (so *Optimize
        Scene Size* can't delete it), locked channels, locked name."""
        cmds.group(empty=True, name=DataNodes.EXPORT)
        DataNodes.ensure_export()
        shapes = cmds.listRelatives(DataNodes.EXPORT, shapes=True) or []
        self.assertTrue(shapes, "Heal should add the protective locator shape")
        self.assertEqual(cmds.nodeType(shapes[0]), "locator")
        for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
            self.assertTrue(cmds.getAttr(f"{DataNodes.EXPORT}.{attr}", lock=True))
        self.assertTrue(cmds.lockNode(DataNodes.EXPORT, q=True, lockName=True)[0])

    def test_migrates_fully_locked_node(self):
        """Old scenes may carry the carrier fully locked — ensure unlocks so
        attrs stay writable (same migration ensure_internal performs)."""
        node = cmds.group(empty=True, name=DataNodes.EXPORT)
        cmds.lockNode(str(node), lock=True)
        DataNodes.ensure_export()
        self.assertFalse(cmds.lockNode(DataNodes.EXPORT, q=True, lock=True)[0])
        DataNodes.set_export_string("probe_channel", "writable")
        self.assertEqual(DataNodes.get_export_string("probe_channel"), "writable")

    def test_adopts_sole_nested_carrier(self):
        """With only a nested carrier in the scene (imported under a group,
        no root node), ensure adopts it rather than creating a second one —
        the shallowest existing path is canonical."""
        grp = cmds.group(empty=True, name="imported_grp")
        cmds.createNode("transform", name=DataNodes.EXPORT, parent=grp)
        node = DataNodes.ensure_export()
        self.assertEqual(cmds.ls(node, long=True), ["|imported_grp|data_export"])
        self.assertEqual(len(cmds.ls(DataNodes.EXPORT, long=True)), 1)

    def test_hidden_carrier_still_exportable(self):
        """Display-only flag: the node stays listable/selectable for export sets."""
        DataNodes.set_export_string("probe_channel", "payload")
        self.assertEqual(cmds.ls(DataNodes.EXPORT), [DataNodes.EXPORT])
        cmds.select(DataNodes.EXPORT, replace=True)
        self.assertIn(DataNodes.EXPORT, cmds.ls(selection=True))


# ── internal string channels ─────────────────────────────────────────────


class TestInternalStrings(MayaTkTestCase):
    """set_internal_string / get_internal_string on data_internal."""

    def test_set_creates_attr_and_returns_node(self):
        node = DataNodes.set_internal_string("probe_channel", "hello")
        self.assertEqual(node, DataNodes.INTERNAL)
        self.assertTrue(
            cmds.attributeQuery("probe_channel", node=DataNodes.INTERNAL, exists=True)
        )

    def test_get_round_trips(self):
        DataNodes.set_internal_string("probe_channel", "payload")
        self.assertEqual(DataNodes.get_internal_string("probe_channel"), "payload")

    def test_get_missing_returns_none(self):
        self.assertIsNone(DataNodes.get_internal_string("never_set"))
        DataNodes.ensure_internal()
        self.assertIsNone(DataNodes.get_internal_string("never_set"))

    def test_get_empty_returns_none(self):
        DataNodes.set_internal_string("probe_channel", "")
        self.assertIsNone(DataNodes.get_internal_string("probe_channel"))

    def test_overwrite(self):
        DataNodes.set_internal_string("probe_channel", "one")
        DataNodes.set_internal_string("probe_channel", "two")
        self.assertEqual(DataNodes.get_internal_string("probe_channel"), "two")

    def test_not_mirrored_to_export(self):
        """Internal channels must never leak onto the FBX export node."""
        DataNodes.set_internal_string("probe_channel", "secret")
        if cmds.objExists(DataNodes.EXPORT):
            self.assertFalse(
                cmds.attributeQuery("probe_channel", node=DataNodes.EXPORT, exists=True)
            )

    def test_empty_value_does_not_create_carrier(self):
        """Clearing must never create data_internal just to hold '' —
        same contract as the export side (and the blendertk mirror)."""
        result = DataNodes.set_internal_string("probe_channel", "")
        self.assertIsNone(result)
        self.assertFalse(cmds.objExists(DataNodes.INTERNAL))


# ── node access (resolve without creating) ───────────────────────────────


class TestGetNode(MayaTkTestCase):
    """get_internal_node / get_export_node — resolve, optionally create."""

    def test_no_create_returns_none_on_empty_scene(self):
        self.assertIsNone(DataNodes.get_internal_node(create=False))
        self.assertIsNone(DataNodes.get_export_node(create=False))
        self.assertFalse(cmds.objExists(DataNodes.INTERNAL))
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))

    def test_create_delegates_to_ensure(self):
        self.assertEqual(DataNodes.get_internal_node(), DataNodes.INTERNAL)
        self.assertEqual(DataNodes.get_export_node(), DataNodes.EXPORT)

    def test_no_create_resolves_duplicate_to_root(self):
        DataNodes.ensure_export()
        grp = cmds.group(empty=True, name="imported_grp")
        cmds.createNode("transform", name=DataNodes.EXPORT, parent=grp)
        node = DataNodes.get_export_node(create=False)
        self.assertEqual(cmds.ls(node, long=True), ["|data_export"])


# ── set_export_json ──────────────────────────────────────────────────────


class TestSetExportJson(MayaTkTestCase):
    """set_export_json — the one-call producer publish/clear idiom."""

    def test_publishes_serialized_payload(self):
        DataNodes.set_export_json("probe_channel", {"version": 1, "items": [1, 2]})
        self.assertEqual(
            json.loads(DataNodes.get_export_string("probe_channel")),
            {"version": 1, "items": [1, 2]},
        )

    def test_falsy_payload_clears_channel(self):
        DataNodes.set_export_json("probe_channel", {"version": 1})
        DataNodes.set_export_json("probe_channel", None)
        self.assertIsNone(DataNodes.get_export_string("probe_channel"))

    def test_falsy_payload_never_creates_carrier(self):
        self.assertIsNone(DataNodes.set_export_json("probe_channel", {}))
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))


# ── export string channels ───────────────────────────────────────────────


class TestExportStrings(MayaTkTestCase):
    """set_export_string / get_export_string on data_export."""

    def test_set_creates_attr_and_returns_node(self):
        node = DataNodes.set_export_string("probe_channel", "hello")
        self.assertEqual(node, DataNodes.EXPORT)
        self.assertTrue(
            cmds.attributeQuery("probe_channel", node=DataNodes.EXPORT, exists=True)
        )

    def test_get_round_trips(self):
        DataNodes.set_export_string("probe_channel", "payload")
        self.assertEqual(DataNodes.get_export_string("probe_channel"), "payload")

    def test_get_missing_returns_none(self):
        self.assertIsNone(DataNodes.get_export_string("never_set"))
        DataNodes.ensure_export()
        self.assertIsNone(DataNodes.get_export_string("never_set"))

    def test_empty_value_does_not_create_carrier(self):
        """Clearing a channel must never create data_export just to hold ''."""
        result = DataNodes.set_export_string("probe_channel", "")
        self.assertIsNone(result)
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))

    def test_empty_value_clears_existing_channel(self):
        DataNodes.set_export_string("probe_channel", "payload")
        node = DataNodes.set_export_string("probe_channel", "")
        self.assertEqual(node, DataNodes.EXPORT)
        self.assertIsNone(DataNodes.get_export_string("probe_channel"))
        # The attr itself stays (carrier not torn down), only the value clears.
        self.assertTrue(
            cmds.attributeQuery("probe_channel", node=DataNodes.EXPORT, exists=True)
        )

    def test_overwrite(self):
        DataNodes.set_export_string("probe_channel", "one")
        DataNodes.set_export_string("probe_channel", "two")
        self.assertEqual(DataNodes.get_export_string("probe_channel"), "two")


# ── duplicate carrier short name ─────────────────────────────────────────


class TestDuplicateCarrierName(MayaTkTestCase):
    """A second ``data_export`` at another DAG level (an imported copy parented
    under a group) must not break the API — every plug query keyed on the bare
    short name is ambiguous then (``attributeQuery`` → TypeError, ``setAttr`` →
    RuntimeError, ``getAttr`` silently returns a *list*). The scene's canonical
    carrier is the shallowest path (root wins)."""

    def _add_duplicate(self):
        """Parent a second transform named ``data_export`` under a group."""
        grp = cmds.group(empty=True, name="imported_grp")
        dup = cmds.createNode("transform", name=DataNodes.EXPORT, parent=grp)
        return cmds.ls(dup, long=True)[0]

    def test_set_targets_root_carrier(self):
        DataNodes.ensure_export()
        dup = self._add_duplicate()
        node = DataNodes.set_export_string("probe_channel", "payload")
        self.assertEqual(cmds.getAttr("|data_export.probe_channel"), "payload")
        self.assertFalse(cmds.attributeQuery("probe_channel", node=dup, exists=True))
        self.assertEqual(cmds.ls(node, long=True), ["|data_export"])

    def test_get_reads_root_carrier(self):
        DataNodes.set_export_string("probe_channel", "payload")
        self._add_duplicate()
        self.assertEqual(DataNodes.get_export_string("probe_channel"), "payload")

    def test_clear_with_duplicate_present(self):
        DataNodes.set_export_string("probe_channel", "payload")
        self._add_duplicate()
        DataNodes.set_export_string("probe_channel", "")
        self.assertIsNone(DataNodes.get_export_string("probe_channel"))

    def test_ensure_export_returns_canonical(self):
        DataNodes.ensure_export()
        self._add_duplicate()
        node = DataNodes.ensure_export()
        self.assertEqual(cmds.ls(node, long=True), ["|data_export"])

    def test_dump_with_duplicate_present(self):
        DataNodes.set_export_string("probe_channel", "payload")
        self._add_duplicate()
        data = DataNodes.dump()
        self.assertEqual(data[DataNodes.EXPORT], {"probe_channel": "payload"})


# ── dump / format_dump ───────────────────────────────────────────────────


class TestDump(MayaTkTestCase):
    """DataNodes.dump / format_dump — read every channel a scene carries."""

    def test_empty_scene_returns_empty_groups(self):
        """No nodes → both groups present but empty; format_dump is falsy."""
        data = DataNodes.dump()
        self.assertEqual(data, {DataNodes.INTERNAL: {}, DataNodes.EXPORT: {}})
        self.assertEqual(DataNodes.format_dump(), "")

    def test_groups_channels_by_node(self):
        DataNodes.set_internal_string("app_state", "running")
        DataNodes.set_export_string("wire", "abc")
        data = DataNodes.dump()
        self.assertEqual(data[DataNodes.INTERNAL], {"app_state": "running"})
        self.assertEqual(data[DataNodes.EXPORT], {"wire": "abc"})

    def test_decodes_json_values(self):
        DataNodes.set_export_string("shot_metadata", '{"take": 3, "clips": [1, 2]}')
        data = DataNodes.dump()  # decode=True default
        self.assertEqual(data[DataNodes.EXPORT]["shot_metadata"], {"take": 3, "clips": [1, 2]})

    def test_decode_false_keeps_raw_strings(self):
        DataNodes.set_export_string("shot_metadata", '{"take": 3}')
        data = DataNodes.dump(decode=False)
        self.assertEqual(data[DataNodes.EXPORT]["shot_metadata"], '{"take": 3}')

    def test_non_json_value_kept_as_string(self):
        DataNodes.set_internal_string("note", "just a plain string")
        data = DataNodes.dump()
        self.assertEqual(data[DataNodes.INTERNAL]["note"], "just a plain string")

    def test_skips_empty_channels(self):
        """A created-then-cleared channel is present as an attr but has no value."""
        DataNodes.set_internal_string("live", "x")
        DataNodes.set_internal_string("dead", "y")
        DataNodes.set_internal_string("dead", "")  # clear (attr stays, value empty)
        data = DataNodes.dump()
        self.assertIn("live", data[DataNodes.INTERNAL])
        self.assertNotIn("dead", data[DataNodes.INTERNAL])

    def test_includes_non_string_channels(self):
        """Non-string channels (the audio tool's per-track enum attrs) are real stored data —
        dump keeps them alongside the JSON string channels, and format_dump serializes them."""
        internal = DataNodes.ensure_internal()
        cmds.addAttr(
            str(internal),
            longName="audio_clip_voice",
            attributeType="enum",
            enumName="off:on",
            keyable=True,
            hidden=True,  # matches AudioClips.ensure_track_attr — must still be discovered
        )
        cmds.setAttr(f"{internal}.audio_clip_voice", 1)
        DataNodes.set_internal_string("payload", "keep")
        data = DataNodes.dump()
        self.assertEqual(data[DataNodes.INTERNAL]["payload"], "keep")
        self.assertEqual(data[DataNodes.INTERNAL]["audio_clip_voice"], 1)
        # format_dump must serialize the mixed string + non-string channels without error.
        self.assertEqual(json.loads(DataNodes.format_dump())[DataNodes.INTERNAL]["audio_clip_voice"], 1)

    def test_format_dump_is_valid_json_round_trip(self):
        DataNodes.set_internal_string("app_state", '{"open": true}')
        DataNodes.set_export_string("wire", "abc")
        report = DataNodes.format_dump()
        parsed = json.loads(report)
        self.assertEqual(parsed[DataNodes.INTERNAL]["app_state"], {"open": True})
        self.assertEqual(parsed[DataNodes.EXPORT]["wire"], "abc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
