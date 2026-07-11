# coding=utf-8
"""Shared Maya mock setup for mayatk tests.

pytest loads conftest.py before any test module, so placing the mock
injection here guarantees that ``sys.modules["maya.cmds"]`` etc. are
populated before any ``import mayatk`` statement.  All test files that
need mocked Maya should import the mock objects from here.

mayatk is fully migrated to ``maya.cmds``; pymel is no longer mocked.
"""
import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Canonical mock objects — every test file must share these instances so
# that sys.modules["maya.cmds"] always points to the same MagicMock.
# ---------------------------------------------------------------------------

mock_om2 = MagicMock()
mock_om2.MEventMessage.addEventCallback.return_value = 1
mock_om2.MMessage.removeCallback = MagicMock()

mock_cmds = MagicMock()
mock_cmds.currentTime.return_value = 1.0
mock_cmds.playbackOptions.return_value = 0.0
mock_cmds.objExists.return_value = True
mock_cmds.ls.return_value = []
mock_cmds.nodeType.return_value = "transform"
mock_cmds.listConnections.return_value = []
mock_cmds.listRelatives.return_value = []
mock_cmds.keyframe.return_value = []
mock_cmds.keyTangent.return_value = []
mock_cmds.scaleKey = MagicMock()
mock_cmds.displayInfo = MagicMock()
mock_cmds.scriptJob.side_effect = lambda **kw: 999 if "event" in kw else True
mock_cmds.undoInfo = MagicMock()

# ---------------------------------------------------------------------------
# Inject into sys.modules — must happen before any mayatk import, and ONLY
# when a real Maya is not importable (plain pytest). Under mayapy/Maya the
# injection is session-wide sabotage: ``import maya.cmds as cmds`` resolves
# through the maya PACKAGE ATTRIBUTE first, and in a fresh test session where
# nothing has imported cmds yet the hasattr guard below sees no attribute and
# binds the MOCK onto the real maya package — with no restore path, every
# test module that runs later in the suite silently gets a MagicMock cmds.
# The import probe itself closes that window: importing the real maya.cmds
# also sets the package attribute via the normal import machinery.
# ---------------------------------------------------------------------------


def _real_maya_importable() -> bool:
    existing = sys.modules.get("maya.cmds")
    if existing is not None:
        return not isinstance(existing, MagicMock)
    try:
        import importlib

        importlib.import_module("maya.cmds")
        return True
    except Exception:
        return False


_HAVE_REAL_MAYA = _real_maya_importable()

if not _HAVE_REAL_MAYA:
    sys.modules.setdefault("maya", types.ModuleType("maya"))
    sys.modules.setdefault("maya.api", types.ModuleType("maya.api"))
    sys.modules["maya.api.OpenMaya"] = mock_om2
    sys.modules.setdefault("maya.api.OpenMayaAnim", MagicMock())
    sys.modules["maya.cmds"] = mock_cmds
    sys.modules.setdefault("maya.mel", MagicMock())
    sys.modules.setdefault("maya.OpenMaya", MagicMock())
    sys.modules.setdefault("maya.OpenMayaUI", MagicMock())

    # Bind each injected submodule as an attribute on its parent package so
    # that attribute access (``import maya`` then ``maya.cmds``) resolves to
    # the same mock as the ``sys.modules`` entry.  Direct ``sys.modules``
    # injection bypasses the import machinery that normally sets this
    # attribute, so without it ``maya.cmds`` raises ``AttributeError`` under
    # the mock even though ``import maya.cmds`` works.
    for _parent, _child in (
        ("maya", "cmds"),
        ("maya", "mel"),
        ("maya", "OpenMaya"),
        ("maya", "OpenMayaUI"),
        ("maya", "api"),
        ("maya.api", "OpenMaya"),
        ("maya.api", "OpenMayaAnim"),
    ):
        _parent_mod = sys.modules[_parent]
        if not hasattr(_parent_mod, _child):
            setattr(_parent_mod, _child, sys.modules[f"{_parent}.{_child}"])


# ---------------------------------------------------------------------------
# Sandbox the ecosystem user-config root so tests never read or write the real
# PresetStore / TemplateSet locations (last-used pointers, user presets, and
# user mapping/behavior templates). Mirrors test_preset_store's redirect; see
# the "test suite wiped live QSettings" lesson — never touch a live user store.
# ---------------------------------------------------------------------------
import os  # noqa: E402
import tempfile  # noqa: E402

from pythontk.core_utils.user_config import CONFIG_ROOT_ENV_VAR  # noqa: E402

os.environ[CONFIG_ROOT_ENV_VAR] = tempfile.mkdtemp(prefix="mayatk_test_config_")
