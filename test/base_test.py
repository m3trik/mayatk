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
    import maya.cmds as cmds
    from maya import mel
except ImportError as error:
    print(f"Warning: {error}")

import mayatk as mtk


def skipUnlessExtended(func):
    """Decorator to skip tests unless MAYATK_EXTENDED_TESTS is set."""
    return unittest.skipUnless(
        os.environ.get("MAYATK_EXTENDED_TESTS") == "1",
        "Extended test (skipped unless --extended flag is used)",
    )(func)


def _is_batch() -> bool:
    try:
        return bool(cmds.about(batch=True))
    except Exception:
        return False


def skipIfBatch(reason: str = "Interactive-only (needs GUI Maya)"):
    """Decorator to skip a test under mayapy/batch (headless) sessions.

    For tests exercising interactive-only Maya features (e.g. MEL commands
    sourced only by the GUI, viewport-dependent behavior).  The default
    headless runner skips them; they still run in the GUI pass / --gui runs.
    """
    return unittest.skipIf(_is_batch(), reason)


class MayaTkTestCase(unittest.TestCase):
    """Base class for all mayatk test cases."""

    @classmethod
    def setUpClass(cls):
        """Set up once for all tests in the class."""
        cls.test_messages = []

    def setUp(self):
        """Set up clean Maya scene for each test.

        This is the ONLY per-test scene reset — tearDown intentionally does
        not wipe again (setUp of the next test does, and the suite driver
        resets the scene between modules).  One wipe per test instead of two
        saves minutes on a full run.
        """
        try:
            cmds.file(new=True, force=True)
        except Exception as e:
            print(f"Warning: Could not create new scene: {e}")

    def assertNodeExists(self, node_name: str, msg: str = None):
        """Assert that a Maya node exists."""
        exists = cmds.objExists(str(node_name))
        if not exists:
            msg = msg or f"Node '{node_name}' does not exist"
            raise AssertionError(msg)

    def assertNodeType(self, node, expected_type: str, msg: str = None):
        """Assert that a node is of the expected type."""
        actual_type = cmds.nodeType(str(node))
        if actual_type != expected_type:
            msg = msg or f"Expected node type '{expected_type}', got '{actual_type}'"
            raise AssertionError(msg)

    def assertNodesConnected(self, source, destination, msg: str = None):
        """Assert that two attributes/plugs are connected."""
        src = str(source)
        dst = str(destination)
        try:
            connections = cmds.listConnections(dst, source=True, plugs=True) or []
            if src in connections or any(c.split(".")[0] == src.split(".")[0] for c in connections):
                return
            raise AssertionError(msg or f"'{src}' is not connected to '{dst}'")
        except Exception as e:
            raise AssertionError(msg or f"Error checking connection: {e}")

    def create_test_cube(self, name: str = "test_cube"):
        """Create a test cube for testing. Returns transform name (str)."""
        return cmds.polyCube(name=name)[0]

    def create_test_sphere(self, name: str = "test_sphere"):
        """Create a test sphere for testing. Returns transform name (str)."""
        return cmds.polySphere(name=name)[0]

    def create_test_cylinder(self, name: str = "test_cylinder"):
        """Create a test cylinder for testing. Returns transform name (str)."""
        return cmds.polyCylinder(name=name)[0]

    def get_test_callback(self):
        """Get a test callback function that captures messages."""

        def callback(msg, progress=None):
            self.test_messages.append(msg)

        return callback


class QuickTestCase(MayaTkTestCase):
    """
    Quick test case that skips the per-test scene reset.
    Use for tests that don't need a clean scene.
    """

    def setUp(self):
        """Skip scene setup for speed."""
        pass


def skip_if_no_maya(func):
    """Decorator to skip test if Maya is not available."""

    def wrapper(*args, **kwargs):
        try:
            import maya.cmds as cmds

            cmds.about(version=True)
            return func(*args, **kwargs)
        except Exception:
            import unittest

            raise unittest.SkipTest("Maya not available")

    return wrapper
