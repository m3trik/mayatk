# !/usr/bin/python
# coding=utf-8
"""Window-behaviour regression for the Macro Manager panel (mock UI load).

The panel is a resizable table: the user drags the window taller to see more
rows. uitk's ``MainWindow.showEvent`` runs ``fit_height_to_content`` on the
first show of every session (by design, to trim trailing dead space), which —
left enabled — snaps the restored height back to the table's tiny content
minimum (a ``QTableWidget``'s ``sizeHint`` ignores row count), so the window
"resizes each show" instead of keeping the saved size. ``MacroManagerSlots``
opts the window out (``fit_to_content_on_show = False``) so the restored
geometry's height is authoritative.

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
    """The loaded panel must opt out of on-show height fitting."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        cls.sb = Switchboard()
        cls.handler = MayaUiHandler(switchboard=cls.sb)

    def test_opts_out_of_fit_to_content_on_show(self):
        """A restored taller height must survive — fitting is disabled.

        Regression: the window snapped back to content height on every first
        show, discarding the user's saved size ("resizes each show").
        """
        ui = self.handler.get("macro_manager")
        self.assertIsNotNone(ui, "macro_manager UI failed to load")
        self.assertFalse(
            ui.fit_to_content_on_show,
            "macro_manager must opt out of fit_to_content_on_show so the "
            "user's saved window height persists across shows",
        )
        # restore_window_size stays at the default True so the saved geometry
        # is actually restored (the half that fit-on-show was then undoing).
        self.assertTrue(
            ui.restore_window_size,
            "macro_manager must keep geometry persistence enabled",
        )


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1], exit=False, verbosity=2)
