# coding=utf-8
"""Maya mock setup for the mock-only test suite.

Mirrors ``mayatk/test/conftest.py`` so pytest can load this dir as a
self-contained conftest and so that ``from conftest import mock_pm, ...``
resolves locally without circular-import issues.
"""
import sys
import types
from unittest.mock import MagicMock

mock_pm = MagicMock()
mock_pm.objExists.return_value = True
mock_pm.playbackOptions.return_value = 0.0
mock_pm.currentTime.return_value = 1.0
mock_pm.select = MagicMock()
mock_pm.displayInfo = MagicMock()
mock_pm.undoInfo = MagicMock()
mock_pm.ls.return_value = []
mock_pm.keyframe.return_value = []

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
