# !/usr/bin/python
# coding=utf-8
"""Tests for ``DataNodes`` -- the Maya scene store behind ``ptk.SceneStoreBase``.

Covers the carrier lifecycle (creation, idempotency, protection, the keep-alive
input), the store contract (``read`` / ``write`` / ``values`` per scope and
the inherited ``dump``), and the record layer on top of it.
"""

import json
import os
import pathlib
import unittest
from unittest import mock

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


# -- crossings: another scene's carriers meeting this scene's -----------------------


class TestCarrierCrossings(MayaTkTestCase):
    """``merge_carriers`` / ``discard_carriers``: an imported reference's own
    ``data_internal`` / ``data_export`` -- read by nothing once local -- merge
    into this scene's by each record's rule, or go; never stay behind."""

    @staticmethod
    def _carrier(kind, name, records):
        node = cmds.createNode(kind, name=name, skipSelect=True)
        for key, payload in records.items():
            cmds.addAttr(node, longName=key, dataType="string")
            cmds.setAttr(f"{node}.{key}", json.dumps(payload), type="string")
        return node

    def _foreign(self, private=None, deliverable=None):
        """Carriers as an import leaves them: clash-renamed, holding another
        scene's records."""
        carriers = {}
        if private is not None:
            carriers[PRIVATE] = self._carrier("network", "data_internal1", private)
        if deliverable is not None:
            carriers[DELIVERABLE] = self._carrier(
                "transform", "data_export1", deliverable
            )
        return carriers

    def test_records_merge_by_their_rules_and_the_carriers_go(self):
        SR = ptk.SceneRecords
        DataNodes.ensure_internal()
        SR.AUDIO_FILE_MAP.save(DataNodes, {"1": "mine.wav"})
        SR.HIERARCHY_BASELINE.save(DataNodes, {"format": 1, "paths": ["mine"]})
        carriers = self._foreign(
            private={
                "audio_file_map": {"1": "theirs.wav", "2": "b.wav"},
                "hierarchy_baseline": {"format": 1, "paths": ["theirs"]},
                "shot_store": {
                    "shots": [
                        {
                            "shot_id": 1,
                            "name": "door",
                            "start": 0,
                            "end": 5,
                            "objects": ["|door"],
                        }
                    ]
                },
            }
        )
        ctx = DataNodes.merge_carriers(
            carriers, rename={"|door": "|door1"}.get, source="MOD"
        )
        self.assertEqual(
            SR.AUDIO_FILE_MAP.load(DataNodes), {"1": "mine.wav", "2": "b.wav"}
        )
        self.assertEqual(SR.HIERARCHY_BASELINE.load(DataNodes)["paths"], ["mine"])
        (shot,) = SR.SHOT_STORE.load(DataNodes)["shots"]
        # Its members respell to where the import put them; its name is its own.
        self.assertEqual((shot["name"], shot["objects"]), ("door", ["|door1"]))
        self.assertFalse(cmds.objExists("data_internal1"))
        self.assertTrue(any("'1'" in n for n in ctx.notes), ctx.notes)

    def test_the_other_carriers_keyed_attributes_move_with_their_curves(self):
        node = DataNodes.ensure_internal()
        cmds.addAttr(node, longName="trackB", attributeType="enum", enumName="off:on")
        (foreign,) = self._foreign(private={}).values()
        for attr in ("trackA", "trackB"):
            cmds.addAttr(
                foreign,
                longName=attr,
                attributeType="enum",
                enumName="off:on",
                keyable=True,
            )
            cmds.setKeyframe(foreign, attribute=attr, t=1, v=1)
        # A ranged double: the clone keeps its hard AND soft range.
        cmds.addAttr(
            foreign,
            longName="gain",
            attributeType="double",
            minValue=0,
            maxValue=10,
            softMaxValue=5,
            defaultValue=2,
            keyable=True,
        )
        cmds.setKeyframe(foreign, attribute="gain", t=1, v=3)
        ctx = DataNodes.merge_carriers({PRIVATE: foreign}, source="MOD")
        self.assertTrue(
            cmds.listConnections(f"{node}.trackA", type="animCurve"),
            "the keyed attr arrives with its curve",
        )
        self.assertTrue(cmds.listConnections(f"{node}.gain", type="animCurve"))
        self.assertEqual(
            (
                cmds.attributeQuery("gain", node=node, minimum=True),
                cmds.attributeQuery("gain", node=node, maximum=True),
                cmds.attributeQuery("gain", node=node, softMax=True),
                cmds.attributeQuery("gain", node=node, listDefault=True),
            ),
            ([0.0], [10.0], [5.0], [2.0]),
        )
        self.assertFalse(cmds.objExists(foreign))
        self.assertTrue(any("trackB" in n for n in ctx.notes), ctx.notes)

    def test_deliverables_are_produced_again_never_merged(self):
        """The other copy spells names as its own scene did; the merged scene
        publishes afresh (here: no lightmap markers, so no manifest)."""
        carriers = self._foreign(
            deliverable={
                "lightmap_metadata": {"version": 1, "objects": [{"name": "x"}]}
            }
        )
        plan = DataNodes.merge_plan(carriers)
        self.assertTrue(plan.is_empty, "a deliverable is no question to ask")
        DataNodes.merge_carriers(carriers, source="MOD")
        self.assertIsNone(ptk.SceneRecords.LIGHTMAPS.load(DataNodes))
        self.assertFalse(cmds.objExists("data_export1"))

    def test_the_plan_names_what_a_merge_would_keep(self):
        carriers = self._foreign(private={"audio_file_map": {"2": "b.wav"}})
        plan = DataNodes.merge_plan(carriers)
        self.assertFalse(plan.is_empty)
        self.assertEqual(plan.summary(), ["Audio Clips: 1 entry"])

    def test_a_carrier_the_import_adopted_is_merged_into_nothing(self):
        """This scene had no carrier, so the import's IS the scene's own now."""
        node = self._carrier("network", "data_internal", {"audio_file_map": {"2": "b"}})
        DataNodes.merge_carriers({PRIVATE: node}, source="MOD")
        self.assertTrue(cmds.objExists(node))
        self.assertEqual(ptk.SceneRecords.AUDIO_FILE_MAP.load(DataNodes), {"2": "b"})

    def test_discard_drops_the_records_with_their_carriers(self):
        carriers = self._foreign(private={"audio_file_map": {"2": "b.wav"}})
        ctx = DataNodes.discard_carriers(carriers, source="MOD")
        self.assertIsNone(ptk.SceneRecords.AUDIO_FILE_MAP.load(DataNodes))
        self.assertFalse(cmds.objExists("data_internal1"))
        self.assertTrue(any("Audio Clips" in n for n in ctx.notes), ctx.notes)
        # ...even the carrier an import adopted: dropping its data drops it.
        node = self._carrier("network", "data_internal", {"audio_file_map": {"3": "c"}})
        DataNodes.discard_carriers({PRIVATE: node}, source="MOD")
        self.assertFalse(cmds.objExists(node))

    def test_a_discard_leaves_none_of_its_records_in_the_session(self):
        """This scene had no carrier, so the import's WAS the scene's own: a
        panel that read the shot store or the key stash in between holds the
        module's shots and clips.  A discard takes them out of the session as
        well -- left cached, the next write would put them back."""
        from mayatk.anim_utils.key_stash._key_stash import KeyStash
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        cube = cmds.polyCube()[0]
        cmds.setKeyframe(cube, attribute="tx", t=1, v=1)
        curve = cmds.listConnections(f"{cube}.tx", type="animCurve")[0]
        parked = cmds.duplicate(curve, name="parked__keyStash")[0]
        clip = {
            "clip_id": 1,
            "label": "a",
            "objects": [cube],
            "curves": [{"times": [1.0], "stash": BakeSessionStore.node_ref(parked)}],
        }
        shot = {"shot_id": 1, "name": "door", "start": 0, "end": 5, "objects": []}
        node = self._carrier(
            "network",
            "data_internal",
            {
                "shot_store": {"shots": [shot], "scene_fps": 24.0},
                "key_stash": {"schema": 1, "scene_fps": 24.0, "clips": [clip]},
            },
        )
        ShotStore.clear_active()
        KeyStash.invalidate()
        self.assertEqual([s.name for s in ShotStore.active().shots], ["door"])
        self.assertEqual(len(KeyStash.active().clips), 1)
        DataNodes.discard_carriers({PRIVATE: node}, source="MOD")
        self.assertFalse(cmds.objExists(node))
        self.assertEqual(ShotStore.active().shots, [])
        self.assertEqual(KeyStash.active().clips, [])

    def test_a_legacy_stash_registration_moves_before_the_carrier_goes(self):
        """A stash is never without a registration: one the other scene kept on
        its carrier (saved before the registries moved) re-registers here."""
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        (foreign,) = self._foreign(private={}).values()
        cmds.addAttr(
            foreign,
            longName=BakeSessionStore.STASH_REGISTRY_ATTR,
            attributeType="message",
            multi=True,
            indexMatters=False,
        )
        cube = cmds.polyCube()[0]
        cmds.setKeyframe(cube, attribute="tx", t=1, v=1)
        curve = cmds.listConnections(f"{cube}.tx", type="animCurve")[0]
        stash = cmds.duplicate(curve, name="parked")[0]
        cmds.connectAttr(
            f"{stash}.message",
            f"{foreign}.{BakeSessionStore.STASH_REGISTRY_ATTR}",
            nextAvailable=True,
        )
        cmds.lockNode(stash, lock=True)
        DataNodes.merge_carriers({PRIVATE: foreign}, source="MOD")
        self.assertFalse(cmds.objExists(foreign))
        registry = cmds.listConnections(f"{stash}.message", plugs=True) or []
        self.assertTrue(
            any(BakeSessionStore.STASH_REGISTRY_ATTR in p for p in registry), registry
        )

    def test_a_discarded_key_stash_takes_its_parked_curves(self):
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        cube = cmds.polyCube()[0]
        cmds.setKeyframe(cube, attribute="tx", t=1, v=1)
        curve = cmds.listConnections(f"{cube}.tx", type="animCurve")[0]
        parked = cmds.duplicate(curve, name="parked__keyStash")[0]
        cmds.lockNode(parked, lock=True)
        clip = {
            "clip_id": 1,
            "label": "a",
            "objects": [cube],
            "curves": [{"times": [1.0], "stash": BakeSessionStore.node_ref(parked)}],
        }
        carriers = self._foreign(
            private={"key_stash": {"schema": 1, "scene_fps": 24, "clips": [clip]}}
        )
        DataNodes.discard_carriers(carriers, source="MOD")
        self.assertFalse(cmds.objExists(parked))

    def test_carriers_in_a_namespace(self):
        cmds.namespace(add="MOD")
        self._carrier("network", "MOD:data_internal", {})
        self._carrier("transform", "MOD:data_export", {})
        found = DataNodes.carriers_in("MOD")
        self.assertEqual(sorted(s.value for s in found), ["deliverable", "private"])
        self.assertEqual(DataNodes.carriers_in("NOPE"), {})


class TestRecordHandoff(MayaTkTestCase):
    """``transfer_sections`` / ``receive_sections``: the portable records as a
    hand-off sidecar's sections, landed through the same engine."""

    def _group_on_a_cube(self):
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        cube = cmds.polyCube(name="cube")[0]
        EmissiveGroups.add_group("glow", [f"{cube}.f[1]", f"{cube}.f[3]"])
        return cube

    def _sections(self):
        leaf = lambda name: str(name).split("|")[-1]  # noqa: E731 - the FBX spelling
        return DataNodes.transfer_sections(spell=leaf)

    def test_an_emissive_group_crosses_with_its_membership(self):
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        self._group_on_a_cube()
        sections = json.loads(json.dumps(self._sections()))  # as the sidecar holds it
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="cube")[0]
        ctx = DataNodes.receive_sections(
            sections, resolve={"cube": f"|{cube}"}.get, source="send"
        )
        self.assertEqual(EmissiveGroups.list_groups()["glow"]["faces"], 2, ctx.notes)

    def test_a_member_whose_face_count_changed_keeps_no_membership(self):
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        self._group_on_a_cube()
        sections = self._sections()
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="cube", subdivisionsX=3)[0]  # 14 faces, not 6
        ctx = DataNodes.receive_sections(sections, resolve={"cube": f"|{cube}"}.get)
        groups = EmissiveGroups.list_groups()
        self.assertIn("glow", groups, "the registry still merges")
        self.assertTrue(groups["glow"]["missing"], "no set: no faces were claimed")
        self.assertTrue(any("faces" in n for n in ctx.notes), ctx.notes)


class TestProjectRelativePaths(MayaTkTestCase):
    """BACKLOG 2026-09-22, decided 2026-09-23: the path records are spelled
    from the scene FILE's own project -- ``../`` chains included -- so a Save As
    into another project re-spells them (``DataNodes.install_path_rebase``)."""

    def setUp(self):
        super().setUp()
        self._tmp = ptk.TempArtifacts("mtk_dn_paths", policy="scoped")
        self.addCleanup(self._tmp.cleanup)
        root = self._tmp.dir_path()
        self.proj_a = os.path.join(root, "shows", "a")
        self.proj_b = os.path.join(root, "shows", "deeper", "b")
        for proj in (self.proj_a, self.proj_b):
            os.makedirs(os.path.join(proj, "scenes"), exist_ok=True)
            with open(os.path.join(proj, "workspace.mel"), "w") as fh:
                fh.write("//Maya 2025 Project Definition\n")
        self.lib = os.path.join(root, "library", "lm")
        self.maps = os.path.join(self.proj_a, "sourceimages", "lm")
        for folder in (self.lib, self.maps):
            os.makedirs(folder, exist_ok=True)
        DataNodes.install_path_rebase()
        self.addCleanup(DataNodes.remove_path_rebase)
        # Registered last, so it runs first: off the files before they go.
        self.addCleanup(cmds.file, new=True, force=True)

    @staticmethod
    def _save_as(path):
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)

    @staticmethod
    def _same(a, b):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
            os.path.normpath(b)
        )

    def test_the_project_is_the_one_the_scene_file_lives_in(self):
        self.assertIsNone(DataNodes.project_root(), "unsaved: no project")
        self._save_as(os.path.join(self.proj_a, "scenes", "shot.ma"))
        self.assertTrue(self._same(DataNodes.project_root(), self.proj_a))

    def test_a_copy_saved_into_another_project_still_names_the_baselines_source(
        self,
    ):
        """The hierarchy baseline names the scene that recorded it (2026-09-24),
        and that stamp is a path: left spelled from the SOURCE's project, the
        copy resolved it to a file not there -- "renamed" -- and diffed its first
        export against the source's hierarchy, the bug the stamp exists for."""
        from mayatk.env_utils.hierarchy_sync.hierarchy_baseline import (
            HierarchyBaseline,
        )

        source = os.path.join(self.proj_a, "scenes", "source_module.ma")
        self._save_as(source)
        self.assertTrue(HierarchyBaseline.write({"GRP", "GRP|part"}))
        self.assertIsNone(HierarchyBaseline.inherited_from(), "the source's own")

        self._save_as(os.path.join(self.proj_b, "scenes", "copy_module.ma"))
        inherited = HierarchyBaseline.inherited_from()
        self.assertIsNotNone(inherited, "the copy must not own its source's")
        self.assertTrue(self._same(os.path.join(self.proj_b, inherited), source))
        self.assertEqual(HierarchyBaseline.read(), set())

    def test_a_save_as_into_another_project_respells_every_path(self):
        spec = ptk.SceneRecords.LIGHTMAP_DIRS
        self._save_as(os.path.join(self.proj_a, "scenes", "shot.ma"))
        spec.save(
            DataNodes,
            {
                "in.exr": ptk.FileUtils.portable_path(self.maps, self.proj_a),
                "lib.exr": ptk.FileUtils.portable_path(self.lib, self.proj_a),
            },
        )
        self.assertEqual(
            spec.load(DataNodes),
            {"in.exr": "sourceimages/lm", "lib.exr": "../../library/lm"},
        )
        moved_to = os.path.join(self.proj_b, "scenes", "shot.ma")
        self._save_as(moved_to)
        moved = spec.load(DataNodes)
        self.assertEqual(moved["lib.exr"], "../../../library/lm")
        self.assertTrue(
            self._same(os.path.join(self.proj_b, moved["in.exr"]), self.maps), moved
        )
        # What was written is the re-spelled record.
        cmds.file(new=True, force=True)
        cmds.file(moved_to, open=True, force=True)
        self.assertEqual(spec.load(DataNodes), moved)

    def test_a_plain_save_normalizes_an_entry_that_arrived_absolute(self):
        spec = ptk.SceneRecords.AUDIO_FILE_MAP
        self._save_as(os.path.join(self.proj_a, "scenes", "shot.ma"))
        spec.save(DataNodes, {"1": os.path.join(self.lib, "hit.wav")})
        cmds.file(save=True, force=True)
        self.assertEqual(spec.load(DataNodes), {"1": "../../library/lm/hit.wav"})

    def test_an_untitled_scenes_autosave_respells_nothing(self):
        """An autosave writes an UNTITLED scene elsewhere without naming it:
        the open scene still has no project, so its paths stay absolute and
        the remembered project stays none. (The GUI's untitled scene name is
        empty; mocked alike here, since batch reports a phantom instead.)"""
        import maya.OpenMaya as om1

        spec = ptk.SceneRecords.LIGHTMAP_DIRS
        spelled = ptk.FileUtils.portable_path(self.lib, None)
        spec.save(DataNodes, {"lib.exr": spelled})
        autosave = os.path.join(self.proj_a, "autosave", "untitled.0001.ma")
        real_file = cmds.file

        def scene_file(*args, **kwargs):
            if kwargs.get("query") and kwargs.get("sceneName"):
                return ""
            return real_file(*args, **kwargs)

        with (
            mock.patch.object(
                om1.MFileIO, "beforeSaveFilename", return_value=autosave, create=True
            ),
            mock.patch.object(cmds, "file", side_effect=scene_file),
        ):
            DataNodes._rebase_before_save()
        self.assertEqual(spec.load(DataNodes), {"lib.exr": spelled})
        self.assertIsNone(DataNodes._rebase_state()["base"])

    def test_install_is_idempotent_and_remove_takes_it_out(self):
        DataNodes.install_path_rebase()
        self.assertEqual(len(DataNodes._rebase_state()["ids"]), 4)
        DataNodes.remove_path_rebase()
        self.assertEqual(DataNodes._rebase_state()["ids"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
