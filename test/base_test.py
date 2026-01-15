# !/usr/bin/python
# coding=utf-8
"""
Base Test Class for MayaTk Tests

Provides common functionality for all mayatk test cases including
Maya scene setup, cleanup, and utility methods.
"""
import unittest
import sys
import os

# Ensure mayatk is in path
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    import pymel.core as pm
except ImportError as error:
    print(f"Warning: {error}")

import mayatk as mtk


def skipUnlessExtended(func):
    """Decorator to skip tests unless MAYATK_EXTENDED_TESTS is set."""
    return unittest.skipUnless(
        os.environ.get("MAYATK_EXTENDED_TESTS") == "1",
        "Extended test (skipped unless --extended flag is used)",
    )(func)


class MayaTkTestCase(unittest.TestCase):
    """Base class for all mayatk test cases."""

    @classmethod
    def setUpClass(cls):
        """Set up once for all tests in the class."""
        cls.test_messages = []

    def setUp(self):
        """Set up clean Maya scene for each test."""
        try:
            pm.mel.file(new=True, force=True)
        except Exception as e:
            print(f"Warning: Could not create new scene: {e}")

    def tearDown(self):
        """Clean up after each test."""
        try:
            pm.mel.file(new=True, force=True)
        except Exception:
            pass

    def assertNodeExists(self, node_name: str, msg: str = None):
        """Assert that a Maya node exists."""
        exists = pm.objExists(node_name)
        if not exists:
            msg = msg or f"Node '{node_name}' does not exist"
            raise AssertionError(msg)

    def assertNodeType(self, node, expected_type: str, msg: str = None):
        """Assert that a node is of the expected type."""
        actual_type = pm.nodeType(node)
        if actual_type != expected_type:
            msg = msg or f"Expected node type '{expected_type}', got '{actual_type}'"
            raise AssertionError(msg)

    def assertNodesConnected(self, source, destination, msg: str = None):
        """Assert that two nodes/attributes are connected."""
        try:
            if isinstance(source, str):
                source = pm.PyNode(source)
            if isinstance(destination, str):
                destination = pm.PyNode(destination)

            connections = destination.listConnections(source=True, plugs=True)
            is_connected = any(conn == source for conn in connections)

            if not is_connected:
                msg = msg or f"'{source}' is not connected to '{destination}'"
                raise AssertionError(msg)
        except Exception as e:
            msg = msg or f"Error checking connection: {e}"
            raise AssertionError(msg)

    def create_test_cube(self, name: str = "test_cube"):
        """Create a test cube for testing."""
        cube = pm.polyCube(name=name)[0]
        return cube

    def create_test_sphere(self, name: str = "test_sphere"):
        """Create a test sphere for testing."""
        sphere = pm.polySphere(name=name)[0]
        return sphere

    def create_test_cylinder(self, name: str = "test_cylinder"):
        """Create a test cylinder for testing."""
        cylinder = pm.polyCylinder(name=name)[0]
        return cylinder

    def get_test_callback(self):
        """Get a test callback function that captures messages."""

        def callback(msg, progress=None):
            self.test_messages.append(msg)

        return callback


class QuickTestCase(MayaTkTestCase):
    """
    Quick test case that skips scene setup/teardown.
    Use for tests that don't need a clean scene.
    """

    def setUp(self):
        """Skip scene setup for speed."""
        pass

    def tearDown(self):
        """Skip scene teardown for speed."""
        pass


def skip_if_no_maya(func):
    """Decorator to skip test if Maya is not available."""

    def wrapper(*args, **kwargs):
        try:
            import pymel.core as pm

            pm.about(version=True)
            return func(*args, **kwargs)
        except Exception:
            import unittest

            raise unittest.SkipTest("Maya not available")

    return wrapper
