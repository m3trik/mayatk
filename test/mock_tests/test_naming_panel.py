# !/usr/bin/python
# coding=utf-8
"""Structural + file-scope test for the Naming panel (naming.ui + NamingSlots).

Mocked ``maya.cmds`` (this dir's conftest) + a real ``Switchboard`` /
``MayaUiHandler`` load through real (offscreen) Qt — the convention of
test_smart_bake_panel.py. Exercises the header Scope combo (Selection / Scene /
Directory / Files) + Dry Run toggle, the output pane wiring (the engine's report
lands in ``txt002``), the suffix-by-type option box (19 fields from the shared
table), and the Directory / Files workflow end to end on a temp directory —
none of which needs Maya. Scene-scope behavior against real nodes is covered by
test_naming.py (run under mayapy).
"""

import os
import shutil
import sys
import tempfile
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
class TestNamingPanel(unittest.TestCase):
    """The panel loads through the real discovery + compile path and its file scopes work."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        cls.sb = Switchboard()
        cls.handler = MayaUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("naming")
        for _ in range(5):
            cls.app.processEvents()
        cls.slots = cls.ui.slots
        # The offscreen load skips header_init; drive the init entry points.
        cls.slots.header_init(cls.ui.header)
        for w in ("txt000", "txt001", "tb000", "tb001", "tb002", "tb003"):
            getattr(cls.slots, f"{w}_init")(getattr(cls.ui, w))
        cls.menu = cls.ui.header.menu

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="naming_panel_")
        for name in ("pCube1.png", "pCube2.PNG", "sphere.txt"):
            with open(os.path.join(self.tmp, name), "wb") as f:
                f.write(b"x")
        self.slots._files = []
        self.menu.chk_dry_run.setChecked(False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def listing(self):
        return sorted(os.listdir(self.tmp))

    # -- structure -----------------------------------------------------------

    def test_resolves_to_slots_class(self):
        self.assertEqual(type(self.ui.slots).__name__, "NamingSlots")

    def test_header_scope_and_dry_run(self):
        scopes = [
            self.menu.cmb_scope.itemText(i) for i in range(self.menu.cmb_scope.count())
        ]
        self.assertEqual(scopes, ["Selection", "Scene", "Directory", "Files"])
        self.assertFalse(self.menu.chk_dry_run.isChecked())

    def test_output_pane(self):
        self.assertTrue(hasattr(self.ui, "txt002"))
        self.assertIn("Dry Run", self.slots.msg_intro)
        self.assertIs(self.ui.txt002.restore_state, False)
        # The engine's report is routed into the pane through the class logger.
        self.slots.logger.result("wired")
        self.assertIn("wired", self.ui.txt002.toPlainText())

    def test_suffix_fields(self):
        m = self.ui.tb003.option_box.menu
        names = list(self.slots.SUFFIX_FIELDS.values())
        self.assertEqual(len(names), 19)
        self.assertEqual([n for n in names if not hasattr(m, n)], [])
        self.assertEqual(m.tb003_txt003.text(), "_GEO")
        self.assertEqual(m.tb003_txt009.text(), "_SRF")
        self.assertEqual(m.tb003_txt018.text(), "_SET")
        self.assertIn("_IKH", self.slots.valid_suffixes)

    # -- file scopes ---------------------------------------------------------

    def test_directory_scope_workflow(self):
        self.menu.cmb_scope.setCurrentText("Directory")
        browsed = []
        self.sb.dir_dialog = lambda *a, **k: (browsed.append(1), self.tmp)[1]
        self.assertTrue(self.slots.file_scope)

        self.ui.txt000.setText("pCube*")
        self.slots.txt000(self.ui.txt000)
        self.assertEqual(browsed, [1])
        self.assertEqual(
            sorted(os.path.basename(f) for f in self.slots._files),
            ["pCube1.png", "pCube2.PNG"],
        )
        self.assertIn("Find — 2 of 3 files", self.ui.txt002.toPlainText())

        self.menu.chk_dry_run.setChecked(True)
        self.ui.txt001.setText("*box*")
        self.slots.txt001(self.ui.txt001)
        out = self.ui.txt002.toPlainText()
        self.assertIn("DRY RUN", out)
        self.assertIn("pCube1 → box1", out)
        self.assertEqual(self.listing(), ["pCube1.png", "pCube2.PNG", "sphere.txt"])
        self.assertEqual(browsed, [1])  # the operations reuse the working set

        self.menu.chk_dry_run.setChecked(False)
        self.slots.txt001(self.ui.txt001)
        self.assertEqual(self.listing(), ["box1.png", "box2.PNG", "sphere.txt"])
        self.assertEqual(
            sorted(os.path.basename(f) for f in self.slots._files),
            ["box1.png", "box2.PNG"],
        )

        self.ui.tb000.option_box.menu.cmb001.setCurrentText("upper")
        self.slots.tb000(self.ui.tb000)
        self.assertEqual(self.listing(), ["BOX1.png", "BOX2.PNG", "sphere.txt"])

        self.ui.tb002.option_box.menu.s000.setValue(1)
        self.ui.tb002.option_box.menu.cmb002.setCurrentText("Leading")
        self.slots.tb002(self.ui.tb002)
        self.assertEqual(self.listing(), ["OX1.png", "OX2.PNG", "sphere.txt"])

    def test_scene_only_operations_report_in_file_scope(self):
        self.menu.cmb_scope.setCurrentText("Files")
        self.sb.file_dialog = lambda *a, **k: [os.path.join(self.tmp, "sphere.txt")]
        self.slots.tb003(self.ui.tb003)
        self.assertIn("scene objects only", self.ui.txt002.toPlainText())
        self.slots.tb001(self.ui.tb001)
        self.assertIn("scene objects only", self.ui.txt002.toPlainText())
        self.assertEqual(self.listing(), ["pCube1.png", "pCube2.PNG", "sphere.txt"])

    def test_files_scope_and_cancelled_browser(self):
        self.menu.cmb_scope.setCurrentText("Files")
        picked = [os.path.join(self.tmp, "sphere.txt")]
        self.sb.file_dialog = lambda *a, **k: picked
        self.ui.txt000.setText("")
        self.slots.txt000(self.ui.txt000)
        self.assertEqual(self.slots._files, picked)

        self.sb.file_dialog = lambda *a, **k: []
        self.slots.txt000(self.ui.txt000)
        self.assertEqual(self.slots._files, [])
        self.assertIn("No files chosen", self.ui.txt002.toPlainText())


if __name__ == "__main__":
    unittest.main()
