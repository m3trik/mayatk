# !/usr/bin/python
# coding=utf-8
"""Tests for ``DataNodes`` — shared scene data node management.

Covers node creation, idempotency, and the internal/export
string channels.
"""
import unittest
import sys
import os

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
