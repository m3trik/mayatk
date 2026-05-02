# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.core_utils.mash module.

Most of MashToolkit's surface depends on the MASH plugin being available.
These tests cover the parts that are testable without MASH (container class,
metaclass forwarding fallback, plugin guard, object filtering) and skip the
rest gracefully.
"""
import unittest

import maya.cmds as cmds

from mayatk.core_utils.mash import MashNetworkNodes, MashToolkit
from mayatk.core_utils import mash as mash_module

from base_test import MayaTkTestCase, QuickTestCase


def _mash_available():
    try:
        return bool(int(cmds.pluginInfo("MASH", q=True, l=1)))
    except Exception:
        return False


class TestMashNetworkNodes(QuickTestCase):
    """MashNetworkNodes is a plain container — no Maya required."""

    def test_default_init_all_none(self):
        nodes = MashNetworkNodes()
        self.assertIsNone(nodes.waiter)
        self.assertIsNone(nodes.instancer)
        self.assertIsNone(nodes.distribute)

    def test_init_with_values(self):
        nodes = MashNetworkNodes(waiter="w", instancer="i", distribute="d")
        self.assertEqual(nodes.waiter, "w")
        self.assertEqual(nodes.instancer, "i")
        self.assertEqual(nodes.distribute, "d")

    def test_as_tuple_order(self):
        nodes = MashNetworkNodes(waiter="w", instancer="i", distribute="d")
        self.assertEqual(nodes.as_tuple(), ("w", "i", "d"))

    def test_uses_slots_no_dict(self):
        nodes = MashNetworkNodes()
        with self.assertRaises(AttributeError):
            nodes.extra_field = 1


class TestMashToolkitMetaclass(QuickTestCase):
    """Test the lazy attribute forwarding fallback."""

    def test_unknown_attribute_raises_when_mash_unavailable(self):
        # Cache a None _MASH_API to force the lookup path. Restore at end.
        original = mash_module._MASH_API
        mash_module._MASH_API = None
        try:
            # Patch import_module to simulate MASH being unavailable.
            import importlib

            real = importlib.import_module

            def fake(name, *args, **kwargs):
                if name == "MASH.api":
                    raise ImportError("simulated")
                return real(name, *args, **kwargs)

            importlib.import_module = fake
            try:
                with self.assertRaises(AttributeError):
                    _ = MashToolkit.NoSuchSymbol
            finally:
                importlib.import_module = real
        finally:
            mash_module._MASH_API = original


class TestFilterObjects(MayaTkTestCase):
    """``_filter_objects`` is a static helper that uses cmds.ls."""

    def test_mesh_filter_keeps_mesh_shapes(self):
        cube = cmds.polyCube(name="filt_cube")[0]
        result = MashToolkit._filter_objects([cube], "Mesh")
        # Should return at least one mesh shape under the cube
        self.assertTrue(any("Shape" in r for r in result))

    def test_non_mesh_filter_returns_ls_passthrough(self):
        loc = cmds.spaceLocator(name="filt_loc")[0]
        result = MashToolkit._filter_objects([loc], "Particle")
        self.assertIn(loc, result)


class TestEnsurePluginLoaded(MayaTkTestCase):
    """``ensure_plugin_loaded`` should be idempotent when MASH is present."""

    @unittest.skipUnless(_mash_available(), "MASH plugin not available")
    def test_idempotent_when_already_loaded(self):
        before = bool(int(cmds.pluginInfo("MASH", q=True, l=1)))
        MashToolkit.ensure_plugin_loaded()
        after = bool(int(cmds.pluginInfo("MASH", q=True, l=1)))
        self.assertEqual(before, after)
        self.assertTrue(after)


class TestCreateNetworkValidation(MayaTkTestCase):
    """Argument validation for create_network — runs without MASH."""

    def test_missing_objects_raises(self):
        with self.assertRaises(ValueError):
            MashToolkit.create_network(objects=None)
        with self.assertRaises(ValueError):
            MashToolkit.create_network(objects=[])


if __name__ == "__main__":
    unittest.main()
