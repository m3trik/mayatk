# !/usr/bin/python
# coding=utf-8
"""Tests for ``DataNodes`` — shared scene data node management.

Covers node creation, idempotency, proxy attr mirroring,
animation curve visibility through proxies, and legacy carrier migration.
"""
import unittest
import sys
import os

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError:
    pm = cmds = None

from base_test import MayaTkTestCase
from mayatk.node_utils.data_nodes import DataNodes


# ── ensure_internal ──────────────────────────────────────────────────────


class TestEnsureInternal(MayaTkTestCase):
    """DataNodes.ensure_internal() creates and returns the network node."""

    def test_creates_network_node(self):
        node = DataNodes.ensure_internal()
        self.assertTrue(pm.objExists(DataNodes.INTERNAL))
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
        node = pm.createNode("network", name=DataNodes.INTERNAL)
        cmds.lockNode(str(node), lock=True)
        result = DataNodes.ensure_internal()
        locked = cmds.lockNode(str(result), q=True, lock=True)[0]
        self.assertFalse(locked)


# ── ensure_export ────────────────────────────────────────────────────────


class TestEnsureExport(MayaTkTestCase):
    """DataNodes.ensure_export() creates and returns the locked transform."""

    def test_creates_transform(self):
        node = DataNodes.ensure_export()
        self.assertTrue(pm.objExists(DataNodes.EXPORT))
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


# ── mirror_attr ──────────────────────────────────────────────────────────


class TestMirrorAttr(MayaTkTestCase):
    """mirror_attr creates attr on internal + proxy on export."""

    def test_creates_enum_attr(self):
        DataNodes.mirror_attr(
            "audio_trigger",
            attributeType="enum",
            enumName="None:footstep",
            keyable=True,
        )
        self.assertTrue(
            cmds.attributeQuery("audio_trigger", node=DataNodes.INTERNAL, exists=True)
        )
        self.assertTrue(
            cmds.attributeQuery("audio_trigger", node=DataNodes.EXPORT, exists=True)
        )

    def test_creates_string_attr(self):
        DataNodes.mirror_attr("shot_manifest", dataType="string")
        self.assertTrue(
            cmds.attributeQuery("shot_manifest", node=DataNodes.INTERNAL, exists=True)
        )
        self.assertTrue(
            cmds.attributeQuery("shot_manifest", node=DataNodes.EXPORT, exists=True)
        )

    def test_proxy_reads_internal_value_enum(self):
        DataNodes.mirror_attr(
            "audio_trigger",
            attributeType="enum",
            enumName="None:footstep",
            keyable=True,
        )
        cmds.setAttr(f"{DataNodes.INTERNAL}.audio_trigger", 1)
        val = cmds.getAttr(f"{DataNodes.EXPORT}.audio_trigger")
        self.assertEqual(val, 1, "Export proxy should reflect internal value")

    def test_proxy_reads_internal_value_string(self):
        DataNodes.mirror_attr("shot_manifest", dataType="string")
        cmds.setAttr(f"{DataNodes.INTERNAL}.shot_manifest", "test_data", type="string")
        val = cmds.getAttr(f"{DataNodes.EXPORT}.shot_manifest")
        self.assertEqual(val, "test_data")

    def test_proxy_reads_internal_value_float(self):
        DataNodes.mirror_attr("export_version", attributeType="float")
        cmds.setAttr(f"{DataNodes.INTERNAL}.export_version", 3.14)
        val = cmds.getAttr(f"{DataNodes.EXPORT}.export_version")
        self.assertAlmostEqual(val, 3.14, places=2)

    def test_idempotent(self):
        """Calling mirror_attr twice doesn't error or duplicate."""
        DataNodes.mirror_attr("audio_trigger", attributeType="enum", enumName="None")
        DataNodes.mirror_attr("audio_trigger", attributeType="enum", enumName="None")
        self.assertTrue(
            cmds.attributeQuery("audio_trigger", node=DataNodes.INTERNAL, exists=True)
        )

    def test_anim_curve_visible_through_proxy(self):
        """Keying internal attr should be readable from export proxy."""
        DataNodes.mirror_attr(
            "audio_trigger",
            attributeType="enum",
            enumName="None:footstep:reload",
            keyable=True,
        )
        cmds.currentTime(1)
        cmds.setAttr(f"{DataNodes.INTERNAL}.audio_trigger", 0)
        cmds.setKeyframe(DataNodes.INTERNAL, attribute="audio_trigger")

        cmds.currentTime(10)
        cmds.setAttr(f"{DataNodes.INTERNAL}.audio_trigger", 2)
        cmds.setKeyframe(DataNodes.INTERNAL, attribute="audio_trigger")

        # Read from export at frame 10
        cmds.currentTime(10)
        val = cmds.getAttr(f"{DataNodes.EXPORT}.audio_trigger")
        self.assertEqual(val, 2, "Keyed value should be visible through proxy")

    def test_multiple_attrs(self):
        """Multiple attrs can coexist."""
        DataNodes.mirror_attr("audio_trigger", attributeType="enum", enumName="None")
        DataNodes.mirror_attr("shot_manifest", dataType="string")
        DataNodes.mirror_attr("export_version", attributeType="float")

        for attr in ("audio_trigger", "shot_manifest", "export_version"):
            self.assertTrue(
                cmds.attributeQuery(attr, node=DataNodes.INTERNAL, exists=True),
                f"{attr} missing on internal",
            )
            self.assertTrue(
                cmds.attributeQuery(attr, node=DataNodes.EXPORT, exists=True),
                f"{attr} missing on export",
            )


# ── migrate_legacy_carriers ──────────────────────────────────────────────


class TestMigrateLegacyCarriers(MayaTkTestCase):
    """migrate_legacy_carriers moves old carrier data to the new nodes."""

    def _make_legacy_carrier(self, name="audio_events", enum_str="None:footstep"):
        """Create a carrier matching the old _create_audio_carrier pattern."""
        node = cmds.group(empty=True, name=name)
        cmds.addAttr(
            node,
            longName="audio_trigger",
            attributeType="enum",
            enumName=enum_str,
            keyable=True,
        )
        cmds.lockNode(node, lock=False, lockName=True)
        return node

    def test_migrates_single_carrier(self):
        carrier = self._make_legacy_carrier()
        cmds.currentTime(1)
        cmds.setKeyframe(carrier, attribute="audio_trigger", value=0)
        cmds.currentTime(10)
        cmds.setKeyframe(carrier, attribute="audio_trigger", value=1)

        migrated = DataNodes.migrate_legacy_carriers()

        self.assertEqual(migrated, [carrier])
        self.assertFalse(cmds.objExists(carrier), "Old carrier should be deleted")
        self.assertTrue(pm.objExists(DataNodes.INTERNAL))
        self.assertTrue(
            cmds.attributeQuery("audio_trigger", node=DataNodes.INTERNAL, exists=True)
        )

        # Anim curves should be on internal now
        curves = (
            cmds.listConnections(
                f"{DataNodes.INTERNAL}.audio_trigger", type="animCurve"
            )
            or []
        )
        self.assertTrue(len(curves) > 0, "Anim curves should be reconnected")

    def test_migrates_string_attrs(self):
        carrier = self._make_legacy_carrier()
        cmds.addAttr(carrier, longName="audio_file_map", dataType="string")
        cmds.setAttr(
            f"{carrier}.audio_file_map", "footstep=/sfx/step.wav", type="string"
        )

        DataNodes.migrate_legacy_carriers()

        self.assertTrue(
            cmds.attributeQuery("audio_file_map", node=DataNodes.INTERNAL, exists=True)
        )
        val = cmds.getAttr(f"{DataNodes.INTERNAL}.audio_file_map")
        self.assertEqual(val, "footstep=/sfx/step.wav")

    def test_does_not_touch_data_export(self):
        """data_export should not be considered a legacy carrier."""
        DataNodes.ensure_export()
        DataNodes.mirror_attr("audio_trigger", attributeType="enum", enumName="None")

        migrated = DataNodes.migrate_legacy_carriers()
        self.assertEqual(migrated, [], "data_export should not be migrated")
        self.assertTrue(cmds.objExists(DataNodes.EXPORT))

    def test_no_op_on_clean_scene(self):
        migrated = DataNodes.migrate_legacy_carriers()
        self.assertEqual(migrated, [])

    def test_migrates_multiple_carriers(self):
        c1 = self._make_legacy_carrier("audio_events")
        c2 = self._make_legacy_carrier("audio_events1", "None:gunshot")

        migrated = DataNodes.migrate_legacy_carriers()

        self.assertEqual(len(migrated), 2)
        self.assertFalse(cmds.objExists(c1))
        self.assertFalse(cmds.objExists(c2))
        self.assertTrue(pm.objExists(DataNodes.INTERNAL))

    def test_creates_proxy_on_export(self):
        """Migration should set up the mirror_attr proxy."""
        self._make_legacy_carrier()
        DataNodes.migrate_legacy_carriers()

        self.assertTrue(pm.objExists(DataNodes.EXPORT))
        self.assertTrue(
            cmds.attributeQuery("audio_trigger", node=DataNodes.EXPORT, exists=True),
            "Proxy should exist on export node after migration",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
