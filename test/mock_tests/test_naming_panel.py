# !/usr/bin/python
# coding=utf-8
"""Structural + file-scope test for the Naming panel (naming.ui + NamingSlots).

Mocked ``maya.cmds`` (this dir's conftest) + a real ``Switchboard`` /
``MayaUiHandler`` load through real (offscreen) Qt — the convention of
test_smart_bake_panel.py. Exercises the header Scope combo (Selection / Scene /
Directory / Files) + Dry Run / Base Names toggles, the output pane wiring (the
engine's report lands in ``txt002``), the suffix-by-type option box (19 fields
from the shared table), the footer Apply button a dry run arms, and the
Directory / Files workflow end to end on a temp directory — none of which needs
Maya. Scene-scope behavior against real nodes is covered by
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
        self.set_scope("Selection")
        self.menu.chk_dry_run.setChecked(False)
        self.menu.chk_base_names.setChecked(False)
        self.slots._disarm_apply()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def listing(self):
        return sorted(os.listdir(self.tmp))

    def set_scope(self, name):
        """Change the Scope combo the way a user does.

        uitk's ComboBox silences ``setCurrentText`` (its restore-by-text
        convention), so only an index change reaches the slots' scope handler.
        """
        self.menu.cmb_scope.setCurrentIndex(self.slots.SCOPES.index(name))

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

    # -- base names / apply --------------------------------------------------

    def texture_set(self):
        """Replace the temp dir's contents with one multi-map texture set."""
        for name in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, name))
        for name in ("rock_Normal.png", "rock_AO.png", "rock_ORM.1001.png", "note.txt"):
            with open(os.path.join(self.tmp, name), "wb") as f:
                f.write(b"x")
        self.set_scope("Directory")
        self.sb.dir_dialog = lambda *a, **k: self.tmp
        self.slots._files = []
        self.menu.chk_dry_run.setChecked(False)
        self.slots._disarm_apply()

    def test_base_names_is_gated_on_a_file_scope(self):
        """The option is meaningless outside a file scope, and says so."""
        for scope, enabled in (
            ("Selection", False),
            ("Scene", False),
            ("Directory", True),
            ("Files", True),
        ):
            with self.subTest(scope=scope):
                self.set_scope(scope)
                self.assertIs(self.menu.chk_base_names.isEnabled(), enabled)
        # Ticked but out of scope: the property, not the widget, is the gate.
        self.menu.chk_base_names.setChecked(True)
        self.set_scope("Scene")
        self.assertFalse(self.slots.base_names)
        self.set_scope("Directory")
        self.assertTrue(self.slots.base_names)

    def test_rename_on_base_names_carries_the_texture_set(self):
        self.texture_set()
        self.menu.chk_base_names.setChecked(True)
        self.ui.txt000.setText("rock")
        self.slots.txt000(self.ui.txt000)
        self.ui.txt001.setText("stone")
        self.slots.txt001(self.ui.txt001)
        self.assertEqual(
            self.listing(),
            ["note.txt", "stone_AO.png", "stone_Normal.png", "stone_ORM.1001.png"],
        )

    def test_dry_run_arms_the_footer_apply_button(self):
        self.texture_set()
        self.assertIsNotNone(self.slots._apply_btn)
        self.assertTrue(self.slots._apply_btn.isHidden())

        self.menu.chk_dry_run.setChecked(True)
        self.menu.chk_base_names.setChecked(True)
        self.ui.txt000.setText("")
        self.slots.txt000(self.ui.txt000)
        self.ui.txt001.setText("**_v2")
        self.slots.txt001(self.ui.txt001)
        before = self.listing()
        self.assertIn("DRY RUN", self.ui.txt002.toPlainText())
        self.assertFalse(self.slots._apply_btn.isHidden())
        self.assertIn("Apply to commit", self.ui.footer.text())
        self.assertEqual(self.listing(), before)

        # Apply commits the plan that was previewed and stands down.
        self.slots._apply_btn.click()
        self.assertTrue(self.slots._apply_btn.isHidden())
        self.assertIsNone(self.slots._pending)
        self.assertEqual(
            self.listing(),
            [
                "note_v2.txt",
                "rock_v2_AO.png",
                "rock_v2_Normal.png",
                "rock_v2_ORM.1001.png",
            ],
        )
        # The working set follows the renames, exactly as a live run does...
        self.assertEqual(
            sorted(os.path.basename(f) for f in self.slots._files), self.listing()
        )
        # ...and so does the report: an applied run is the same call, hooks included.
        self.assertIn("Directory:", self.ui.txt002.toPlainText())

    def test_apply_commits_the_preview_not_the_current_fields(self):
        """The armed call is frozen: editing the field after a preview cannot change it."""
        self.texture_set()
        self.menu.chk_dry_run.setChecked(True)
        self.ui.txt000.setText("")
        self.slots.txt000(self.ui.txt000)
        self.ui.txt001.setText("**_v2")
        self.slots.txt001(self.ui.txt001)

        self.ui.txt001.setText("**_WRONG")  # changed after the preview
        self.slots._apply_btn.click()
        self.assertEqual(
            self.listing(),
            [
                "note_v2.txt",
                "rock_AO_v2.png",
                "rock_Normal_v2.png",
                "rock_ORM.1001_v2.png",
            ],
        )

    def test_an_aborted_operation_supersedes_an_armed_preview(self):
        """A new operation always drops the last plan -- including one that aborts."""
        self.texture_set()
        self.menu.chk_dry_run.setChecked(True)
        self.ui.txt000.setText("")
        self.slots.txt000(self.ui.txt000)
        self.ui.txt001.setText("**_v2")
        self.slots.txt001(self.ui.txt001)
        self.assertFalse(self.slots._apply_btn.isHidden())

        # The working set is gone and the browser is cancelled: nothing to do.
        for name in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, name))
        self.sb.dir_dialog = lambda *a, **k: ""
        self.slots.txt001(self.ui.txt001)
        self.assertTrue(self.slots._apply_btn.isHidden())
        self.assertIsNone(self.slots._pending)

    def test_a_live_run_and_a_scope_change_disarm_apply(self):
        self.texture_set()
        for stale in ("live run", "scope change"):
            with self.subTest(stale=stale):
                self.menu.chk_dry_run.setChecked(True)
                self.ui.txt000.setText("")
                self.slots.txt000(self.ui.txt000)
                self.ui.txt001.setText("**_v2")
                self.slots.txt001(self.ui.txt001)
                self.assertFalse(self.slots._apply_btn.isHidden())

                if stale == "live run":
                    self.menu.chk_dry_run.setChecked(False)
                    self.slots.txt001(self.ui.txt001)
                else:
                    self.set_scope("Selection")
                self.assertTrue(self.slots._apply_btn.isHidden())
                self.assertIsNone(self.slots._pending)
                self.texture_set()


if __name__ == "__main__":
    unittest.main()
