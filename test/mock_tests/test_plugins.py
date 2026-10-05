# coding=utf-8
"""Mock tests for ``mayatk.core_utils.plugins`` -- the one plug-in door.

GUI Maya holds a plug-in from outside the folders it started with at a modal
"Untrusted Plugin Loading" prompt; standalone never prompts, so no headless run
can see one (the measurements are in the module docstring). These pin, on a
mocked Maya, which load the door issues in each case -- and that nothing in
mayatk loads a plug-in around it. The real loads are ``test/test_plugins.py``.
"""

import ast
import contextlib
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REAL_MAYA_LOADED = "maya.cmds" in sys.modules and not isinstance(
    sys.modules.get("maya.cmds"), MagicMock
)

import pythontk as ptk  # noqa: E402

from mayatk.core_utils.plugins import _plugins  # noqa: E402
from mayatk.core_utils.plugins._plugins import Plugins  # noqa: E402

_PKG = Path(__file__).resolve().parents[2] / "mayatk"
#: A plug-in file a studio folder on Maya's path would hold.
TOOL = "studio_tool"


def setUpModule():
    if _REAL_MAYA_LOADED:
        raise unittest.SkipTest("Mock-based suite -- skipped when real Maya is loaded.")


class _DoorCase(unittest.TestCase):
    """A mocked Maya: interactive, nothing loaded, an empty plug-in path."""

    def setUp(self):
        self.loaded = set()
        self.search = []
        self.cmds = MagicMock()
        self.cmds.pluginInfo.side_effect = lambda name, **_kw: name in self.loaded
        self.cmds.about.return_value = False  # an interactive session
        mel = MagicMock()
        mel.eval.side_effect = lambda _command: os.pathsep.join(self.search)
        for attr, value in (("cmds", self.cmds), ("mel", mel)):
            patcher = patch.object(_plugins, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class TestLoad(_DoorCase):
    def test_mayas_own_plugin_loads_by_name(self):
        Plugins.load("fbxmaya")
        self.cmds.loadPlugin.assert_called_once_with("fbxmaya", quiet=True)

    def test_a_loaded_plugin_is_left_alone(self):
        self.loaded.add("fbxmaya")
        Plugins.load("fbxmaya")
        self.cmds.loadPlugin.assert_not_called()

    def test_a_path_is_refused_before_maya_sees_it(self):
        """Loaded by path, GUI Maya prompts -- the shape this door exists to end."""
        with self.assertRaises(Plugins.LoadError):
            Plugins.load(os.path.join(os.sep, "studio", "plug-ins", f"{TOOL}.py"))
        self.cmds.loadPlugin.assert_not_called()

    def test_a_failed_load_is_what_either_old_caller_caught(self):
        """``EnvUtils.load_plugin`` raised ValueError, ``cmds.loadPlugin`` raises
        RuntimeError: one error is both, so no ``except`` changed meaning."""
        self.cmds.loadPlugin.side_effect = RuntimeError("not on MAYA_PLUG_IN_PATH")
        for caught in (ValueError, RuntimeError):
            with self.assertRaises(caught):
                Plugins.load("not_a_real_plugin_xyz123")


class TestSearchPath(_DoorCase):
    def setUp(self):
        super().setUp()
        artifacts = ptk.TempArtifacts("plugins_search_path", policy="scoped")
        self.addCleanup(artifacts.cleanup)
        self.folder = artifacts.dir_path()
        Path(self.folder, f"{TOOL}.py").write_text("", encoding="utf-8")

    def test_available_finds_an_unloaded_file_on_the_path_without_loading(self):
        self.assertFalse(Plugins.available(TOOL))
        self.search = [self.folder]
        self.assertTrue(Plugins.available(TOOL))
        self.assertTrue(Plugins.available(f"{TOOL}.py"))
        self.cmds.loadPlugin.assert_not_called()

    def test_an_unknown_plugin_is_not_available(self):
        self.search = [self.folder]
        self.assertFalse(Plugins.available("not_a_real_plugin_xyz123"))

    def test_a_loaded_plugin_is_available(self):
        self.loaded.add("mtoa")
        self.assertTrue(Plugins.available("mtoa"))


class TestImportWithoutMaya(unittest.TestCase):
    def test_the_import_is_silent_and_leaves_cmds_none(self):
        """Imports have no side effects: the registry generator, the docs
        tooling and these mock tests import the door without Maya, and its
        guard printed the file and the ImportError to stdout every time."""
        spec = importlib.util.spec_from_file_location(
            "_plugins_without_maya", _plugins.__file__
        )
        module = importlib.util.module_from_spec(spec)
        printed = io.StringIO()
        no_maya = {"maya": None, "maya.cmds": None, "maya.mel": None}
        with patch.dict(sys.modules, no_maya), contextlib.redirect_stdout(printed):
            spec.loader.exec_module(module)
        self.assertEqual(printed.getvalue(), "")
        self.assertIsNone(module.cmds)
        self.assertIsNone(module.mel)


class TestOneDoor(unittest.TestCase):
    def test_nothing_in_mayatk_loads_a_plugin_around_the_door(self):
        """Every load goes through ``Plugins.load``, where the by-name rule
        lives. Exempt: exec-templates (they run in a host of their own) and the
        plug-in files themselves."""
        door = Path(_plugins.__file__).resolve()
        offenders = []
        for path in sorted(_PKG.rglob("*.py")):
            if path.resolve() == door or {"templates", "plugin_src"} & set(path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                direct = getattr(node.func, "attr", None) == "loadPlugin"
                via_mel = any(
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and "loadPlugin" in arg.value
                    for arg in node.args
                )
                if direct or via_mel:
                    offenders.append(f"{path.relative_to(_PKG)}:{node.lineno}")
        self.assertEqual(offenders, [], "load through mayatk.Plugins.load instead")


if __name__ == "__main__":
    unittest.main()
