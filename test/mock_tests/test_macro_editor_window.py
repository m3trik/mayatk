# !/usr/bin/python
# coding=utf-8
"""Offscreen build of the Macro Manager editor (mock ``maya.cmds``).

Replaces the retired ``macro_manager`` panel's window test: the Macro Manager
is now the unified uitk ``ShortcutEditor`` launched over the ``Macros``
controller (``Macros.show_editor`` → ``RegistrySwitchboardFacade``). This
pins the launch contract — branding, category grouping, hidden scope column,
the preset row over the macro store, and the window cache.

Mock-only: it needs a mocked ``maya.cmds`` (this dir's ``conftest`` provides
it and sandboxes ``QSettings``), so under the real-Maya runner (where the
conftest isn't loaded) it skips. Presets are sandboxed via
``UITK_PRESETS_ROOT`` so the row never touches the developer's live store.
"""
import os
import sys
import tempfile
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
class TestMacroEditorWindow(unittest.TestCase):
    """``Macros.show_editor`` opens the ONE unified ShortcutEditor, branded and
    grouped for macros — not a bespoke panel."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls._tmp = tempfile.mkdtemp(prefix="mayatk_macro_editor_presets_")
        cls._old_root = os.environ.get("UITK_PRESETS_ROOT")
        os.environ["UITK_PRESETS_ROOT"] = cls._tmp
        # Keep the shared module-level mock deterministic for this class:
        # no live hotkeys (assignCommand raises → empty live map) and no
        # registered runTimeCommands (categories fall back to the mixins).
        cls._old_assign = mock_cmds.assignCommand.side_effect
        cls._old_rtc = mock_cmds.runTimeCommand.side_effect
        mock_cmds.assignCommand.side_effect = RuntimeError("mock: headless")
        mock_cmds.runTimeCommand.side_effect = lambda *a, **kw: (
            False if kw.get("exists") else ""
        )

    @classmethod
    def tearDownClass(cls):
        mock_cmds.assignCommand.side_effect = cls._old_assign
        mock_cmds.runTimeCommand.side_effect = cls._old_rtc
        if cls._old_root is None:
            os.environ.pop("UITK_PRESETS_ROOT", None)
        else:
            os.environ["UITK_PRESETS_ROOT"] = cls._old_root
        import shutil

        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        # Explicit parent: with cmds mocked, ``UiUtils.get_main_window()``
        # would wrap a garbage MagicMock "pointer" (int(MagicMock()) == 1)
        # into an invalid QMainWindow → access violation. Live Maya returns
        # a real window or None; only the mock needs this bypass.
        self.host = QtWidgets.QWidget()

    def tearDown(self):
        from mayatk.edit_utils.macros import Macros

        self.host.deleteLater()
        if Macros._editor is not None:
            try:
                Macros._editor.close()
                Macros._editor.deleteLater()
            except RuntimeError:
                pass
            Macros._editor = None
        QtWidgets.QApplication.processEvents()

    def _rows(self, ed):
        return [
            ed.table.item(r, 0).text()
            for r in range(ed.table.rowCount())
            if ed.table.item(r, 0) and ed.table.columnSpan(r, 0) == 1
        ]

    def test_launch_contract(self):
        from mayatk.edit_utils.macros import Macros

        ed = Macros.show_editor(parent=self.host)
        self.assertIsNotNone(ed)
        ed._set_show_hidden(False)

        # Branding + facade mode.
        self.assertEqual(ed.windowTitle(), "Macro Manager")
        self.assertTrue(ed._manager_mode)

        # Native macro hotkeys are DCC-global — the Scope column is dropped.
        self.assertTrue(ed.table.isColumnHidden(ed.COL_SCOPE))
        self.assertEqual(
            ed.table.horizontalHeaderItem(ed.COL_UI).text(), "Category"
        )

        # The category combobox is the group filter (mixin-derived here —
        # the mock has no live custom categories).
        combo = [ed.cmb_ui.itemText(i) for i in range(ed.cmb_ui.count())]
        self.assertEqual(combo, Macros.editor_categories())

        # Every discoverable macro renders (in show-all) or in its group;
        # honour whatever view the persisted pref selected, then force
        # show-all for the full count.
        ed._set_show_all(True)
        labels = self._rows(ed)
        self.assertEqual(len(labels), len(Macros.list_available_macros()))
        self.assertIn("Grid", labels)  # humanized label, not m_grid

        # The preset row fronts the macro store (mayatk/macro_manager),
        # not the editor's own shortcut_presets domain.
        self.assertIsNotNone(ed._preset_mgr)
        self.assertIn(
            os.path.join("mayatk", "macro_manager"), str(ed.preset_dir)
        )

        # A Maya-wide collision checker is registered alongside the built-in.
        self.assertEqual(len(ed._collision_checkers), 2)

    def test_window_is_cached_and_rebuilt_after_destroy(self):
        from mayatk.edit_utils.macros import Macros

        ed1 = Macros.show_editor(parent=self.host)
        ed2 = Macros.show_editor(parent=self.host)
        self.assertIs(ed1, ed2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
