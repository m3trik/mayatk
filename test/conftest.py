# coding=utf-8
"""Shared Maya mock setup for mayatk tests.

pytest loads conftest.py before any test module, so placing the mock
injection here guarantees that ``sys.modules["maya.cmds"]`` etc. are
populated before any ``import mayatk`` statement.  All test files that
need mocked Maya should import the mock objects from here.
"""
import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Canonical mock objects — every test file must share these instances so
# that sys.modules["maya.cmds"] always points to the same MagicMock.
# ---------------------------------------------------------------------------

mock_pm = MagicMock()
mock_pm.objExists.return_value = True
mock_pm.playbackOptions.return_value = 0.0
mock_pm.currentTime.return_value = 1.0
mock_pm.select = MagicMock()
mock_pm.displayInfo = MagicMock()

mock_undo_chunk = MagicMock()
mock_undo_chunk.__enter__ = MagicMock(return_value=None)
mock_undo_chunk.__exit__ = MagicMock(return_value=False)
mock_pm.UndoChunk.return_value = mock_undo_chunk

mock_pm.scriptJob.return_value = 999
mock_pm.scriptJob.side_effect = lambda **kw: 999 if "event" in kw else True

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

# ---------------------------------------------------------------------------
# Inject into sys.modules — must happen before any mayatk import.
# ---------------------------------------------------------------------------

sys.modules.setdefault("pymel", types.ModuleType("pymel"))
sys.modules["pymel.core"] = mock_pm
sys.modules.setdefault("maya", types.ModuleType("maya"))
sys.modules.setdefault("maya.api", types.ModuleType("maya.api"))
sys.modules["maya.api.OpenMaya"] = mock_om2
sys.modules.setdefault("maya.api.OpenMayaAnim", MagicMock())
sys.modules["maya.cmds"] = mock_cmds
sys.modules.setdefault("maya.mel", MagicMock())
sys.modules.setdefault("maya.OpenMaya", MagicMock())
sys.modules.setdefault("maya.OpenMayaUI", MagicMock())
