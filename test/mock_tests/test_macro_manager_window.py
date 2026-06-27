# !/usr/bin/python
# coding=utf-8
"""Window-behaviour regression for the Macro Manager panel (mock UI load).

The panel is a resizable table: the user drags the window taller to see more
rows, so that height is real content, not trailing dead space. It used to need
a per-window opt-out (``fit_to_content_on_show = False``) because uitk's
``MainWindow.showEvent`` re-ran ``fit_height_to_content`` after restoring the
saved geometry, snapping the height back to the table's tiny content minimum
("resizes each show").

That opt-out is gone: uitk's MainWindow now treats a restored geometry as
authoritative (it skips the on-show fit whenever a saved size was restored, for
*every* window), so the panel keeps its hand-expanded height across sessions
via the general mechanism. This test pins that the panel relies on that
mechanism and carries no per-window patch.

Exercised through the real ``MayaUiHandler`` load path. Mock-only: it needs a
mocked ``maya.cmds`` (this dir's ``conftest`` provides it and sandboxes
``QSettings`` so constructing a ``Switchboard`` can't touch the developer's
live store), so under the real-Maya runner (where conftest isn't loaded) it
skips. The window is never shown, so no geometry is persisted.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# conftest injects mock_cmds + sandboxes QSettings under pytest; under
# run_tests.py (real Maya) it isn't loaded, so detect and skip cleanly.
mock_cmds = sys.modules.get("maya.cmds")
_CMDS_IS_MOCKED = isinstance(mock_cmds, MagicMock)

try:
    from qtpy import QtWidgets
except Exception:  # pragma: no cover - Qt not installed
    QtWidgets = None


@unittest.skipUnless(
    _CMDS_IS_MOCKED and QtWidgets is not None,
    "Mock + Qt test — run via pytest, not run_tests.py",
)
class TestMacroManagerWindowFit(unittest.TestCase):
    """The loaded panel relies on uitk's general restore, not a fit opt-out."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        cls.sb = Switchboard()
        cls.handler = MayaUiHandler(switchboard=cls.sb)

    def test_relies_on_general_restore_no_fit_opt_out(self):
        """No per-window fit opt-out — persistence comes from uitk's MainWindow.

        Regression: the window snapped back to content height on every first
        show, discarding the user's saved size. The fix lives in uitk (a
        restored geometry is authoritative, so the on-show fit is skipped), so
        the panel must keep the defaults and not re-introduce a local patch.
        """
        ui = self.handler.get("macro_manager")
        self.assertIsNotNone(ui, "macro_manager UI failed to load")
        # The opt-out is gone: the panel keeps the default (fit enabled) and
        # depends on uitk skipping the fit when a saved size was restored.
        self.assertTrue(
            ui.fit_to_content_on_show,
            "macro_manager should NOT re-add a fit_to_content_on_show=False "
            "patch — uitk's restored-geometry-authoritative fix handles it",
        )
        # Geometry persistence must stay enabled for the saved size to restore.
        self.assertTrue(
            ui.restore_window_size,
            "macro_manager must keep geometry persistence enabled",
        )


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1], exit=False, verbosity=2)
