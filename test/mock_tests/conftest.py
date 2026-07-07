# coding=utf-8
"""Maya mock setup for the mock-only test suite.

Mirrors ``mayatk/test/conftest.py`` so pytest can load this dir as a
self-contained conftest and so that ``from conftest import mock_cmds, ...``
resolves locally without circular-import issues.

mayatk is fully migrated to ``maya.cmds``; pymel is no longer mocked.
"""
import sys
import types
import atexit
import shutil
import tempfile
from unittest.mock import MagicMock

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

sys.modules.setdefault("maya", types.ModuleType("maya"))
sys.modules.setdefault("maya.api", types.ModuleType("maya.api"))
sys.modules["maya.api.OpenMaya"] = mock_om2
sys.modules.setdefault("maya.api.OpenMayaAnim", MagicMock())
sys.modules["maya.cmds"] = mock_cmds
sys.modules.setdefault("maya.mel", MagicMock())
sys.modules.setdefault("maya.OpenMaya", MagicMock())
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
    Windows) — so without isolation a single ``pytest`` run mutates the
    developer's real widget/marking-menu state. On Windows the ``(org, app)``
    overload is registry-bound and ignores ``setPath``/``setDefaultFormat``,
    so it is rewritten to the explicit ``IniFormat`` constructor pointed at a
    throwaway dir. Mirrors ``uitk``'s test conftest; guarded so the Qt-free
    mock tests in this suite still run if Qt is unavailable.
    """
    try:
        from qtpy import QtCore
    except Exception:  # pragma: no cover - Qt not installed
        return

    tmp = tempfile.mkdtemp(prefix="mayatk_mock_qsettings_")
    real = QtCore.QSettings
    ini, user = real.IniFormat, real.UserScope
    for scope in (real.UserScope, real.SystemScope):
        real.setPath(ini, scope, tmp)
    real.setDefaultFormat(ini)

    class _SandboxedQSettings(real):
        def __init__(self, *args, **kwargs):
            if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
                # (org, app[, parent]) -> explicit IniFormat
                super().__init__(ini, user, *args, **kwargs)
            elif (
                len(args) >= 3
                and isinstance(args[1], str)
                and isinstance(args[2], str)
            ):
                # (scope, org, app[, parent]) -> explicit IniFormat
                super().__init__(ini, *args, **kwargs)
            else:
                super().__init__(*args, **kwargs)

    QtCore.QSettings = _SandboxedQSettings
    atexit.register(lambda: shutil.rmtree(tmp, ignore_errors=True))


_sandbox_qsettings()
