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

# Make the sibling ecosystem repos importable when a test file runs outside
# the runner (whose _suite_driver pins these paths itself). Derived from this
# file's location so any checkout works. Insert the REPO roots, never the bare
# workspace root: the workspace root resolves each sibling as an empty
# namespace package whenever the real one isn't importable elsewhere.
_SCRIPTS_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
for _pkg in ("mayatk", "pythontk", "uitk", "tentacle", "unitytk"):
    _pkg_root = os.path.join(_SCRIPTS_ROOT, _pkg)
    if os.path.isdir(_pkg_root) and _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)

try:
    import maya.cmds as cmds
    from maya import mel
except ImportError as error:
    print(f"Warning: {error}")

import mayatk as mtk


#: Root of the machine-local scenes a few extended tests replay. Those are
#: production files that cannot ship, so the location is supplied per machine
#: rather than hardcoded (which would also put a studio's folder layout in a
#: public repo): point ``MAYATK_TEST_ASSETS`` at the folder holding them.
#: Unset, it falls back to a sentinel that CANNOT exist, so every existence
#: guard skips. The sentinel matters: ``""`` would betray any guard written
#: with pathlib, because ``Path("").exists()`` is ``Path(".").exists()`` --
#: True -- while ``os.path.exists("")`` is False.
TEST_ASSETS = os.environ.get("MAYATK_TEST_ASSETS", "") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "__missing_test_asset__"
)


def asset_path(*parts: str) -> str:
    """A path under :data:`TEST_ASSETS`; can't-exist when that root is unset.

    Safe under either guard style -- ``os.path.exists`` and
    ``Path(...).exists()`` both return False for the unset-root sentinel.
    """
    return os.path.join(TEST_ASSETS, *parts)


#: Lazily-allocated per-process wav fixture dir (see :func:`make_temp_wav`).
#: The store must stay referenced so its session-exit cleanup fires.
_wav_store = None
_wav_dir = None


def make_temp_wav(name: str, duration_sec: float = 0.5, sr: int = 22050) -> str:
    """Write a silent WAV fixture and return its forward-slash path.

    One writer for the seven audio suites' hand-rolled ``_make_wav`` copies.
    The file lands in a per-process ``ptk.TempArtifacts`` directory under the
    SYSTEM temp dir, not ``test/temp_tests``: the copies shared fixed
    friendly-named paths on the cloud-synced O: drive, where a concurrent run
    or a scanner holding a just-written file made durations intermittently
    unreadable -- the 2026-08-02 full-suite-only flake where the assertion
    landed exactly on the caller's ``default_duration`` (and single moving
    failures reproduced during the 2026-08-14 fix itself). The BASENAME stays
    clean because several suites derive track ids from the wav stem; the
    session policy cleans up at interpreter exit and age-sweeps leftovers of
    killed runs.
    """
    import struct
    import wave

    global _wav_store, _wav_dir
    if _wav_dir is None:
        import pythontk as ptk

        _wav_store = ptk.TempArtifacts(
            "mayatk_test_wav", policy="session", max_age_days=1
        )
        _wav_dir = _wav_store.dir_path()
    path = os.path.join(_wav_dir, f"{name}.wav").replace("\\", "/")
    n = int(sr * duration_sec)
    data = struct.pack(f"<{n}h", *([0] * n))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data)
    return path


def import_without_maya(module_name, null_attrs):
    """Import *module_name* in a subprocess with ``maya`` blocked.

    Verifies the module's ImportError guards define each name in
    *null_attrs* as ``None`` (graceful degrade) instead of leaving it
    undefined (NameError at first use). Returns the CompletedProcess;
    callers assert ``returncode == 0``.
    """
    import subprocess

    checks = "\n".join(
        f"assert m.{attr} is None, '{attr} not nulled'" for attr in null_attrs
    )
    script = (
        "import sys, importlib.abc\n"
        "class _NoMaya(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] == 'maya':\n"
        "            raise ImportError('maya blocked')\n"
        "sys.meta_path.insert(0, _NoMaya())\n"
        f"import {module_name} as m\n"
        f"{checks}\n"
    )
    env = os.environ.copy()
    roots = [
        os.path.join(_SCRIPTS_ROOT, pkg)
        for pkg in ("mayatk", "pythontk", "uitk", "tentacle")
    ]
    env["PYTHONPATH"] = os.pathsep.join(roots + [env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env
    )


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
