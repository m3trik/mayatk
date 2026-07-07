# !/usr/bin/python
# coding=utf-8
"""Structural load test for the Smart Bake panel (smart_bake.ui + SmartBakeSlots).

Real maya.standalone + real Qt widgets in one process crashes natively (not a
supported combination in this codebase — see test_macro_manager_window.py's
docstring), so this mirrors that file's convention instead: mocked
``maya.cmds`` (this dir's conftest) + a real ``Switchboard``/``MayaUiHandler``
load through real (offscreen) Qt. Exercises .ui compilation, customwidgets
resolution, and that ``SmartBakeSlots`` (not the bare ``SmartBake`` engine
class) is what actually gets discovered and wired.

Bake/Unbake behavior against real Maya state is covered separately in
test_smart_bake_slots.py (stub-UI pattern, run under mayapy).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
class TestSmartBakePanelLoads(unittest.TestCase):
    """The panel loads through the real discovery + compile path."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        cls.sb = Switchboard()
        cls.handler = MayaUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("smart_bake")
        # Flush the QTimer.singleShot(0, self._initialize_ui) deferred in
        # __init__ — Maya's own event loop does this immediately in real use.
        for _ in range(5):
            cls.app.processEvents()

    def test_ui_loads(self):
        self.assertIsNotNone(self.ui, "smart_bake UI failed to load")

    def test_resolves_to_slots_class_not_bare_engine(self):
        """SmartBake (the engine) is also a registered mayatk class — confirm
        the Slots suffix disambiguates it during discovery."""
        self.assertEqual(type(self.ui.slots).__name__, "SmartBakeSlots")

    def test_all_referenced_widgets_exist(self):
        expected = [
            "cmb_scope",
            "spn_sample_by",
            "chk_preserve_outside",
            "chk_optimize",
            "chk_bake_blendshapes",
            "chk_inherited_vis",
            "chk_override_layer",
            "chk_mute_drivers",
            "chk_delete_inputs",
            "cmb_backup",
            "b000",
            "b001",
            "txt000",
            "footer",
            "header",
        ]
        missing = [w for w in expected if not hasattr(self.ui, w)]
        self.assertEqual(missing, [])

    def test_scope_combo_items(self):
        items = [self.ui.cmb_scope.itemText(i) for i in range(self.ui.cmb_scope.count())]
        self.assertEqual(items, ["Auto (Whole Scene)", "Selected"])

    def test_backup_combo_items(self):
        items = [self.ui.cmb_backup.itemText(i) for i in range(self.ui.cmb_backup.count())]
        self.assertEqual(items, ["Auto", "Always", "Never"])

    def test_override_layer_defaults_checked(self):
        self.assertTrue(self.ui.chk_override_layer.isChecked())

    def test_delete_inputs_disabled_when_override_layer_checked(self):
        self.ui.chk_override_layer.setChecked(True)
        self.assertFalse(self.ui.chk_delete_inputs.isEnabled())

    def test_delete_inputs_enabled_when_override_layer_unchecked(self):
        self.ui.chk_override_layer.setChecked(False)
        self.assertTrue(self.ui.chk_delete_inputs.isEnabled())
        self.ui.chk_override_layer.setChecked(True)  # restore default

    def test_unbake_disabled_with_no_pending_sessions(self):
        # mock_cmds.getAttr returns a MagicMock, not JSON — BakeSessionStore
        # treats that as a corrupt/missing manifest and falls back to [].
        self.assertFalse(self.ui.b001.isEnabled())


if __name__ == "__main__":
    unittest.main()
