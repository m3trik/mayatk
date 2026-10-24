# coding=utf-8
"""Maya mock setup for the mock-only test suite.

Mirrors ``mayatk/test/conftest.py`` so pytest can load this dir as a
self-contained conftest and so that ``from conftest import mock_cmds, ...``
resolves locally without circular-import issues.

mayatk is fully migrated to ``maya.cmds``; pymel is no longer mocked.
"""

import sys
import types
from unittest.mock import MagicMock

mock_om2 = MagicMock()
# Named like the modules they stand in for: under mayapy, PySide6's import
# hook (shiboken ``feature_imported``) reads ``__name__`` on every
# ``from maya... import``, which a bare MagicMock raises for.
mock_om2.__name__ = "maya.api.OpenMaya"
mock_om2.MEventMessage.addEventCallback.return_value = 1
mock_om2.MMessage.removeCallback = MagicMock()

mock_cmds = MagicMock()
mock_cmds.__name__ = "maya.cmds"
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

sys.modules.setdefault("maya", types.ModuleType("maya"))
sys.modules.setdefault("maya.api", types.ModuleType("maya.api"))
sys.modules["maya.api.OpenMaya"] = mock_om2
sys.modules.setdefault("maya.api.OpenMayaAnim", MagicMock())
sys.modules["maya.cmds"] = mock_cmds
sys.modules.setdefault("maya.mel", MagicMock())
sys.modules.setdefault("maya.OpenMaya", MagicMock())
# Only a mock needs telling. Under mayapy (``run_tests.py --mocks``) the real
# API-1 module stays and answers this itself; assigning onto its builtin
# raised, so no mock module loaded there.
if isinstance(sys.modules["maya.OpenMaya"], MagicMock):
    sys.modules["maya.OpenMaya"].MFileIO.isReadingFile.return_value = False
sys.modules.setdefault("maya.OpenMayaUI", MagicMock())

# Force-sync each submodule as an attribute of its parent package. Plain
# ``sys.modules`` injection doesn't do this, so ``import maya.cmds as cmds``
# (attribute-based binding) would otherwise keep resolving to whatever
# ``test/conftest.py`` (the ancestor conftest, always loaded first by pytest)
# already set — its own sync loop is ``hasattr``-guarded to protect a real
# Maya install, which means it never yields to a second, more-specific mock.
# This conftest is mock-only by design, so it overwrites unconditionally.
for _parent, _child in (
    ("maya", "cmds"),
    ("maya", "mel"),
    ("maya", "OpenMaya"),
    ("maya", "OpenMayaUI"),
    ("maya", "api"),
    ("maya.api", "OpenMaya"),
    ("maya.api", "OpenMayaAnim"),
):
    setattr(sys.modules[_parent], _child, sys.modules[f"{_parent}.{_child}"])


def _sandbox_qsettings() -> None:
    """Keep the mock suite off the real ``QSettings`` store.

    Any test that constructs a ``uitk`` ``Switchboard`` builds a
    ``SettingsManager``, whose ``__init__`` runs a legacy-registry migration
    that *writes* to the live per-user store (``HKCU\\Software\\uitk`` on
    Windows). The redirect itself (rewriting the registry-bound overloads to
    explicit ``IniFormat``) is the ecosystem SSoT ``uitk.testing.TestSandbox``
    — this file used to carry a hand-rolled mirror of it. Guarded so the
    Qt-free mock tests in this suite still run if Qt/uitk is unavailable.
    """
    try:
        from qtpy import QtCore  # noqa: F401
    except Exception:  # pragma: no cover - Qt not installed
        return

    # Past the guard, failures must be LOUD: swallowing one here would run the
    # suite unsandboxed against the live per-user store.
    from uitk.testing import TestSandbox

    TestSandbox.qsettings()


# The Qt-free half first -- no real browser launch, one throwaway temp root --
# so the QSettings ini files below land inside that root.
import pythontk as ptk  # noqa: E402

ptk.TestSandbox.activate()
_sandbox_qsettings()

# And the presets root, which needs no Qt: a direct run (``python
# mock_tests/test_x.py``) loads only this conftest, never the parent one that
# redirects it, and the panel tests here save presets.
try:
    from uitk.testing import TestSandbox as _UitkSandbox
except ImportError:  # pragma: no cover - uitk not installed
    pass
else:
    _UitkSandbox.presets()
