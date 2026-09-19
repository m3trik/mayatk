# !/usr/bin/python
# coding=utf-8
"""Tests for ``DataNodes`` -- the Maya scene store behind ``ptk.SceneStoreBase``.

Covers the carrier lifecycle (creation, idempotency, protection, the keep-alive
input), the store contract (``read`` / ``write`` / ``values`` per scope and
the inherited ``dump``), the record layer on top of it, and the retired
channel methods for the one release they keep working.
"""

import json
import pathlib
import unittest
import warnings

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk
from base_test import MayaTkTestCase
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils.data_nodes import DataNodes

PRIVATE, DELIVERABLE = ptk.Scope.PRIVATE, ptk.Scope.DELIVERABLE


# -- ensure_internal ------------------------------------------------------------


class TestEnsureInternal(MayaTkTestCase):
    """DataNodes.ensure_internal() creates and returns the network node."""

    def test_creates_network_node(self):
        node = DataNodes.ensure_internal()
        self.assertTrue(cmds.objExists(DataNodes.INTERNAL))
        self.assertEqual(cmds.nodeType(str(node)), "network")

    def test_idempotent(self):
        self.assertEqual(DataNodes.ensure_internal(), DataNodes.ensure_internal())

    def test_name_is_locked(self):
        DataNodes.ensure_internal()
        self.assertTrue(cmds.lockNode(DataNodes.INTERNAL, q=True, lockName=True)[0])

    def test_node_is_not_fully_locked(self):
        DataNodes.ensure_internal()
        self.assertFalse(cmds.lockNode(DataNodes.INTERNAL, q=True, lock=True)[0])

    def test_migrates_fully_locked_node(self):
        node = cmds.createNode("network", name=DataNodes.INTERNAL)
        cmds.lockNode(str(node), lock=True)
        result = DataNodes.ensure_internal()
        self.assertFalse(cmds.lockNode(str(result), q=True, lock=True)[0])

    def test_an_undo_never_deletes_the_carrier_with_other_records(self):
        """The carrier is bookkeeping, created outside the undo queue. A tool
        that first touched ``data_internal`` inside its own undo chunk used to
        own the node's lifetime: undoing that chunk deleted it with every record
        written outside the queue since (measured 2026-09-15).
        Added: 2026-09-15
        """
        cmds.undoInfo(state=True, infinity=True)
        self.assertFalse(cmds.objExists(DataNodes.INTERNAL))
        with CoreUtils.undo_chunk("Tool Edit"):
            DataNodes.write(PRIVATE, "tool_record", "tool")
        with CoreUtils.undo_disabled():
            DataNodes.write(PRIVATE, "shot_store", "shots")
        cmds.undo()
        self.assertEqual(DataNodes.read(PRIVATE, "shot_store"), "shots")
        self.assertIsNone(DataNodes.read(PRIVATE, "tool_record"))
        cmds.redo()
        self.assertEqual(DataNodes.read(PRIVATE, "tool_record"), "tool")

    def test_deleting_the_only_keyed_curve_does_not_delete_the_carrier(self):
        """Maya deletes a network node when the source of its ONLY input is
        deleted. A keyed audio-track enum whose animCurve was the carrier's
        sole input took the carrier -- and every record on it -- with it when
        that curve was cut (measured 2026-09-18, fresh mayapy 2025). The
        keep-alive input from ``time1`` makes the rule unreachable.
        Added: 2026-09-18
        """
        node = DataNodes.ensure_internal()
        DataNodes.write(PRIVATE, "shot_store", '{"shots": []}')
        cmds.addAttr(
            node,
            longName="audio_clip_probe",
            attributeType="enum",
            enumName="off:on",
            keyable=True,
        )
        cmds.setKeyframe(node, attribute="audio_clip_probe", time=1, value=1)
        curves = cmds.listConnections(f"{node}.audio_clip_probe", type="animCurve")
        self.assertTrue(curves, "the probe must be keyed through an animCurve")
        cmds.delete(curves)
        self.assertTrue(cmds.objExists(DataNodes.INTERNAL), "carrier survived")
        self.assertEqual(DataNodes.read(PRIVATE, "shot_store"), '{"shots": []}')

    def test_keep_alive_is_hidden_from_the_store(self):
        DataNodes.ensure_internal()
        self.assertNotIn(DataNodes._KEEP_ALIVE_ATTR, DataNodes.values(PRIVATE))
        self.assertEqual(DataNodes.dump()[DataNodes.INTERNAL], {})


# -- ensure_export --------------------------------------------------------------


class TestEnsureExport(MayaTkTestCase):
    """DataNodes.ensure_export() creates and returns the locked transform."""

    def test_creates_transform(self):
        node = DataNodes.ensure_export()
        self.assertTrue(cmds.objExists(DataNodes.EXPORT))
        self.assertEqual(cmds.nodeType(str(node)), "transform")

    def test_idempotent(self):
        self.assertEqual(DataNodes.ensure_export(), DataNodes.ensure_export())

    def test_creating_either_carrier_keeps_the_selection(self):
        """A record written mid-tool creates its carrier; the carrier is
        bookkeeping and must not become the user's selection (``cmds.group``
        selected the new transform)."""
        cube = cmds.polyCube(name="picked")[0]
        cmds.select(cube, replace=True)
        DataNodes.ensure_export()
        DataNodes.ensure_internal()
        self.assertEqual(cmds.ls(selection=True), [cube])

    def test_has_stamped_locator_shape(self):
        DataNodes.ensure_export()
        shapes = cmds.listRelatives(DataNodes.EXPORT, shapes=True) or []
        self.assertTrue(shapes, "Should have a locator shape")
        self.assertEqual(cmds.nodeType(shapes[0]), "locator")
        self.assertTrue(
            cmds.attributeQuery(DataNodes._LOCATOR_ATTR, node=shapes[0], exists=True)
        )

    def test_transform_channels_locked(self):
        DataNodes.ensure_export()
        for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
            self.assertTrue(cmds.getAttr(f"{DataNodes.EXPORT}.{attr}", lock=True), attr)

    def test_name_is_locked(self):
        DataNodes.ensure_export()
        self.assertTrue(cmds.lockNode(DataNodes.EXPORT, q=True, lockName=True)[0])

    def test_hidden_in_outliner(self):
        DataNodes.ensure_export()
        self.assertTrue(cmds.getAttr(f"{DataNodes.EXPORT}.hiddenInOutliner"))
        for shape in cmds.listRelatives(DataNodes.EXPORT, shapes=True) or []:
            self.assertTrue(cmds.getAttr(f"{shape}.hiddenInOutliner"), shape)

    def test_heals_unprotected_existing_node(self):
        """A pre-existing plain transform (hand-authored or imported) heals to
        the full protection contract on ensure."""
        cmds.group(empty=True, name=DataNodes.EXPORT)
        cmds.setAttr(f"{DataNodes.EXPORT}.hiddenInOutliner", 0)
        DataNodes.ensure_export()
        shapes = cmds.listRelatives(DataNodes.EXPORT, shapes=True) or []
        self.assertTrue(shapes, "Heal should add the protective locator shape")
        self.assertTrue(cmds.getAttr(f"{DataNodes.EXPORT}.hiddenInOutliner"))
        self.assertTrue(cmds.lockNode(DataNodes.EXPORT, q=True, lockName=True)[0])

    def test_migrates_fully_locked_node(self):
        node = cmds.group(empty=True, name=DataNodes.EXPORT)
        cmds.lockNode(str(node), lock=True)
        DataNodes.ensure_export()
        self.assertFalse(cmds.lockNode(DataNodes.EXPORT, q=True, lock=True)[0])
        DataNodes.write(DELIVERABLE, "probe_channel", "writable")
        self.assertEqual(DataNodes.read(DELIVERABLE, "probe_channel"), "writable")

    def test_adopts_sole_nested_carrier(self):
        grp = cmds.group(empty=True, name="imported_grp")
        cmds.createNode("transform", name=DataNodes.EXPORT, parent=grp)
        node = DataNodes.ensure_export()
        self.assertEqual(cmds.ls(node, long=True), ["|imported_grp|data_export"])
        self.assertEqual(len(cmds.ls(DataNodes.EXPORT, long=True)), 1)

    def test_hidden_carrier_still_exportable(self):
        DataNodes.write(DELIVERABLE, "probe_channel", "payload")
        self.assertEqual(cmds.ls(DataNodes.EXPORT), [DataNodes.EXPORT])
        cmds.select(DataNodes.EXPORT, replace=True)
        self.assertIn(DataNodes.EXPORT, cmds.ls(selection=True))


# -- node access ------------------------------------------------------------------


class TestGetNode(MayaTkTestCase):
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


class TestGetExportNodes(MayaTkTestCase):
    """EVERY carrier, including a reference's namespaced one."""

    def test_empty_scene_has_none(self):
        self.assertEqual(DataNodes.get_export_nodes(), [])

    def test_finds_the_namespaced_carrier_a_reference_brings(self):
        DataNodes.ensure_export()
        cmds.namespace(add="MODULE")
        cmds.createNode("transform", name="MODULE:data_export")
        self.assertEqual(
            DataNodes.get_export_nodes(), ["|data_export", "|MODULE:data_export"]
        )
        self.assertEqual(DataNodes.get_export_node(create=False), DataNodes.EXPORT)

    def test_does_not_match_the_locator_shape(self):
        DataNodes.ensure_export()
        for node in DataNodes.get_export_nodes():
            self.assertEqual(cmds.nodeType(node), "transform")

    def test_dump_export_nodes_reads_every_carrier(self):
        """The plural dump: one entry per shipped carrier -- the namespaced one
        ``dump`` cannot see included -- decoded, cleared channels skipped."""
        self.assertEqual(DataNodes.dump_export_nodes(), {})
        DataNodes.write(ptk.Scope.DELIVERABLE, "own", '{"a": [1, 2]}')
        DataNodes.write(ptk.Scope.DELIVERABLE, "cleared", "x")
        DataNodes.write(ptk.Scope.DELIVERABLE, "cleared", "")
        cmds.namespace(add="MODULE")
        module = cmds.createNode("transform", name="MODULE:data_export")
        cmds.addAttr(module, longName="lightmaps", dataType="string")
        cmds.setAttr(f"{module}.lightmaps", '{"b": 3}', type="string")

        dumped = DataNodes.dump_export_nodes()
        self.assertEqual(list(dumped), ["|data_export", "|MODULE:data_export"])
        self.assertEqual(dumped["|data_export"], {"own": {"a": [1, 2]}})
        self.assertEqual(dumped["|MODULE:data_export"], {"lightmaps": {"b": 3}})
        self.assertEqual(
            DataNodes.dump_export_nodes(decode=False)["|data_export"],
            {"own": '{"a": [1, 2]}'},
        )
        # dump() sees only the canonical carrier -- why the plural exists.
        self.assertNotIn("lightmaps", DataNodes.dump()[DataNodes.EXPORT])


# -- the store contract -----------------------------------------------------------


class TestStoreContract(MayaTkTestCase):
    """read / write / values per scope -- what ``ptk.SceneStoreBase`` asks of
    a DCC, and all the record layer ever calls."""

    def test_write_creates_carrier_and_attr_and_round_trips(self):
        self.assertEqual(DataNodes.write(PRIVATE, "probe", "hello"), DataNodes.INTERNAL)
        self.assertEqual(DataNodes.read(PRIVATE, "probe"), "hello")
        self.assertEqual(
            DataNodes.write(DELIVERABLE, "probe", "there"), DataNodes.EXPORT
        )
        self.assertEqual(DataNodes.read(DELIVERABLE, "probe"), "there")

    def test_scopes_do_not_mix(self):
        DataNodes.write(PRIVATE, "probe", "secret")
        self.assertIsNone(DataNodes.read(DELIVERABLE, "probe"))
        if cmds.objExists(DataNodes.EXPORT):
            self.assertFalse(
                cmds.attributeQuery("probe", node=DataNodes.EXPORT, exists=True)
            )

    def test_read_missing_or_cleared_is_none(self):
        self.assertIsNone(DataNodes.read(PRIVATE, "never_set"))
        DataNodes.ensure_internal()
        self.assertIsNone(DataNodes.read(PRIVATE, "never_set"))
        DataNodes.write(PRIVATE, "probe", "x")
        DataNodes.write(PRIVATE, "probe", None)
        self.assertIsNone(DataNodes.read(PRIVATE, "probe"))
        # The attr itself stays (carrier not torn down), only the value clears.
        self.assertTrue(
            cmds.attributeQuery("probe", node=DataNodes.INTERNAL, exists=True)
        )

    def test_clear_never_creates_a_carrier(self):
        self.assertIsNone(DataNodes.write(PRIVATE, "probe", ""))
        self.assertIsNone(DataNodes.write(DELIVERABLE, "probe", None))
        self.assertFalse(cmds.objExists(DataNodes.INTERNAL))
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))

    def test_clear_of_an_unknown_key_on_an_existing_carrier_is_none(self):
        DataNodes.ensure_export()
        self.assertIsNone(DataNodes.write(DELIVERABLE, "probe", ""))

    def test_a_non_string_attr_is_not_a_channel_but_is_a_value(self):
        """The audio tool keys enums on the carrier; ``read`` says they are not
        channels, ``values`` (and so ``dump``) still reports them."""
        internal = DataNodes.ensure_internal()
        cmds.addAttr(
            internal,
            longName="audio_clip_voice",
            attributeType="enum",
            enumName="off:on",
            keyable=True,
            hidden=True,
        )
        cmds.setAttr(f"{internal}.audio_clip_voice", 1)
        DataNodes.write(PRIVATE, "payload", "keep")
        self.assertIsNone(DataNodes.read(PRIVATE, "audio_clip_voice"))
        self.assertEqual(
            DataNodes.values(PRIVATE), {"audio_clip_voice": 1, "payload": "keep"}
        )
        self.assertEqual(DataNodes.dump()[DataNodes.INTERNAL]["audio_clip_voice"], 1)
        self.assertEqual(
            json.loads(DataNodes.format_dump())[DataNodes.INTERNAL]["audio_clip_voice"],
            1,
        )

    def test_duplicate_carrier_short_name_resolves_to_root(self):
        """An imported copy of ``data_export`` under a group makes every
        bare-name plug query ambiguous; the shallowest path is canonical."""
        DataNodes.ensure_export()
        grp = cmds.group(empty=True, name="imported_grp")
        dup = cmds.createNode("transform", name=DataNodes.EXPORT, parent=grp)
        dup = cmds.ls(dup, long=True)[0]
        DataNodes.write(DELIVERABLE, "probe", "payload")
        self.assertEqual(cmds.getAttr("|data_export.probe"), "payload")
        self.assertFalse(cmds.attributeQuery("probe", node=dup, exists=True))
        self.assertEqual(DataNodes.read(DELIVERABLE, "probe"), "payload")
        self.assertEqual(DataNodes.dump()[DataNodes.EXPORT], {"probe": "payload"})
        DataNodes.write(DELIVERABLE, "probe", "")
        self.assertIsNone(DataNodes.read(DELIVERABLE, "probe"))

    def _retired_proxy_pair(self, key: str = "probe") -> None:
        """The shape the retired ``mirror_attr`` left: a record authored on
        the internal carrier and Maya-proxied onto the export carrier."""
        internal = DataNodes.ensure_internal()
        cmds.addAttr(internal, longName=key, dataType="string")
        cmds.addAttr(DataNodes.ensure_export(), longName=key, proxy=f"{internal}.{key}")
        cmds.setAttr(f"{internal}.{key}", "1:legacy", type="string")

    def test_a_write_replaces_a_retired_proxy_with_a_plain_channel(self):
        self._retired_proxy_pair()
        DataNodes.write(DELIVERABLE, "probe", "fresh")
        self.assertEqual(DataNodes.read(DELIVERABLE, "probe"), "fresh")
        self.assertFalse(
            cmds.addAttr(f"{DataNodes.EXPORT}.probe", query=True, usedAsProxy=True)
        )
        self.assertFalse(
            cmds.attributeQuery("probe", node=DataNodes.INTERNAL, exists=True),
            "the proxy's private source goes with it",
        )

    def test_a_clear_drops_a_retired_proxy_pair(self):
        self._retired_proxy_pair()
        self.assertEqual(DataNodes.write(DELIVERABLE, "probe", None), DataNodes.EXPORT)
        self.assertIsNone(DataNodes.read(DELIVERABLE, "probe"))
        for node in (DataNodes.EXPORT, DataNodes.INTERNAL):
            self.assertFalse(cmds.attributeQuery("probe", node=node, exists=True))

    def test_dump_groups_by_node_and_decodes(self):
        self.assertEqual(
            DataNodes.dump(), {DataNodes.INTERNAL: {}, DataNodes.EXPORT: {}}
        )
        self.assertEqual(DataNodes.format_dump(), "")
        DataNodes.write(PRIVATE, "app_state", '{"open": true}')
        DataNodes.write(DELIVERABLE, "wire", "abc")
        DataNodes.write(PRIVATE, "dead", "y")
        DataNodes.write(PRIVATE, "dead", "")
        data = DataNodes.dump()
        self.assertEqual(data[DataNodes.INTERNAL], {"app_state": {"open": True}})
        self.assertEqual(data[DataNodes.EXPORT], {"wire": "abc"})
        self.assertEqual(
            DataNodes.dump(decode=False)[DataNodes.INTERNAL]["app_state"],
            '{"open": true}',
        )
        self.assertEqual(
            json.loads(DataNodes.format_dump())[DataNodes.INTERNAL]["app_state"],
            {"open": True},
        )


# -- the record layer over the store ------------------------------------------------


class TestRecords(MayaTkTestCase):
    """What producers and consumers actually call: ``ptk.SceneRecords`` over
    ``DataNodes``, and ``ptk.ExportSnapshot`` committing to it."""

    def test_save_load_round_trip_with_the_declared_version(self):
        ptk.SceneRecords.LIGHTMAPS.save(DataNodes, {"objects": [{"name": "a"}]})
        payload = ptk.SceneRecords.LIGHTMAPS.load(DataNodes)
        self.assertEqual(payload["objects"], [{"name": "a"}])
        self.assertEqual(payload["version"], ptk.SceneRecords.LIGHTMAPS.version)
        self.assertEqual(
            json.loads(cmds.getAttr(f"{DataNodes.EXPORT}.lightmap_metadata"))[
                "version"
            ],
            ptk.SceneRecords.LIGHTMAPS.version,
        )

    def test_falsy_payload_clears_and_never_creates(self):
        self.assertIsNone(ptk.SceneRecords.LIGHTMAPS.save(DataNodes, {}))
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))
        ptk.SceneRecords.LIGHTMAPS.save(DataNodes, {"objects": []})
        self.assertEqual(
            ptk.SceneRecords.LIGHTMAPS.save(DataNodes, None), DataNodes.EXPORT
        )
        self.assertIsNone(ptk.SceneRecords.LIGHTMAPS.load(DataNodes))

    def test_private_and_deliverable_records_with_one_key_stay_apart(self):
        ptk.SceneRecords.EMISSIVE_REGISTRY.save(DataNodes, {"slots": {"a": 0}})
        self.assertIsNone(ptk.SceneRecords.EMISSIVE_GROUPS.load(DataNodes))
        self.assertEqual(
            ptk.SceneRecords.EMISSIVE_REGISTRY.load(DataNodes)["slots"], {"a": 0}
        )
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))

    def test_non_native_value_is_recorded_as_its_string(self):
        ptk.SceneRecords.LIGHTMAPS.save(
            DataNodes,
            {"objects": [{"name": "a", "map": pathlib.PurePosixPath("maps/a.exr")}]},
        )
        (entry,) = ptk.SceneRecords.LIGHTMAPS.load(DataNodes)["objects"]
        self.assertEqual(entry["map"], "maps/a.exr")

    def test_publish_commits_and_stamps_the_handoff(self):
        ptk.ExportSnapshot.publish(
            DataNodes,
            {
                ptk.SceneRecords.LIGHTMAPS: {"objects": []},
                ptk.SceneRecords.SHADOWS: None,
            },
        )
        handoff = ptk.SceneRecords.HANDOFF.load(DataNodes)
        self.assertEqual(list(handoff["reads"]), ["data_export.lightmap_metadata"])
        self.assertEqual(handoff["source"], None, "no provenance was given")
        ptk.ExportSnapshot.publish(DataNodes, {ptk.SceneRecords.LIGHTMAPS: None})
        self.assertFalse(ptk.SceneRecords.HANDOFF.is_present(DataNodes))


# -- retired channel methods (one release) ------------------------------------------


class TestRetiredChannelMethods(MayaTkTestCase):
    """The pre-2026-09-18 string/JSON channel methods keep working, and warn."""

    def test_string_methods_alias_the_store(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.assertEqual(
                DataNodes.set_internal_string("probe", "one"), DataNodes.INTERNAL
            )
            self.assertEqual(DataNodes.get_internal_string("probe"), "one")
            self.assertEqual(
                DataNodes.set_export_string("probe", "two"), DataNodes.EXPORT
            )
            self.assertEqual(DataNodes.get_export_string("probe"), "two")
        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught), "must warn"
        )
        self.assertEqual(DataNodes.read(PRIVATE, "probe"), "one")

    def test_json_methods_alias_the_store(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            DataNodes.set_export_json("probe", {"version": 1, "items": [1, 2]})
            self.assertEqual(
                json.loads(DataNodes.get_export_string("probe")),
                {"version": 1, "items": [1, 2]},
            )
            self.assertIsNone(DataNodes.set_export_json("probe2", {}))
            DataNodes.set_internal_json("rec", {"a": 1})
            self.assertEqual(DataNodes.get_internal_json("rec"), {"a": 1})
            self.assertEqual(DataNodes.get_internal_json("nope", default=[]), [])
        self.assertFalse(
            cmds.attributeQuery("probe2", node=DataNodes.EXPORT, exists=True),
            "a falsy payload never creates the attr",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
