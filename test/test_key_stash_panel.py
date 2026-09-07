# !/usr/bin/python
# coding=utf-8
"""Panel-wiring tests for the ``key_stash.ui`` panel + KeyStashSlots.

GUI-only (registered in ``run_tests.GUI_REQUIRED``): ``MayaUiHandler`` is
constructed against a GUI Maya (see ``test_maya_ui_handler``), so the panel
cannot load under mayapy.

Where ``test_key_stash.py`` covers the engine, this covers what the engine
tests can't see: the ``.ui`` parses with the layout the other tool windows
use (header / titled groups / footer, 19 px rows, a four-column clip list),
the mode combos populate with their display prefixes, the header carries
refresh / collapse / pin plus help, selection gates the clip actions, and
every control drives the store end to end against a real scene: Store,
Retrieve (original frames and current time), Drop through ``sb.message_box``,
the Preview checkbox, double-click-to-select, and the header refresh pruning
a clip whose stash node was deleted behind the record's back.

Buttons are driven the way the sibling panel suites drive them (a direct
``slots.<name>()`` call). The Preview checkbox, the tree double-click and
the header refresh are driven through the widget instead: their wiring is
part of what this polish changed, so the test proves the toggle reaches
the slot even though the panel is never shown.
"""

import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.anim_utils.key_stash._key_stash import KeyStash
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

KEYS = ((1, 0.0), (10, 5.0), (20, 10.0), (30, 15.0), (40, 20.0))
COLUMNS = ["Clip", "Objects", "Range", "Stored"]


def _reset_store():
    """Forget the active store AND its backend so each test re-installs a fresh one.

    A local copy of the engine suite's helper on purpose: the harness purges
    mayatk between modules, so importing it from ``test_key_stash`` would
    reset an earlier module's copy of the class, not the one this panel uses.
    """
    backend = KeyStash._persistence
    if backend is not None and hasattr(backend, "remove_callbacks"):
        backend.remove_callbacks()
    KeyStash._active = None
    KeyStash.set_persistence(None)


class _PanelCase(MayaTkTestCase):
    """Load the panel once; each test gets a clean scene, store and cube."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ui = MayaUiHandler.instance().get("key_stash")
        cls.slots = cls.ui.slots
        cls.sb = cls.slots.sb
        # The slots populate on the next event-loop tick (a test body runs
        # with the loop parked): give it that tick, bounded.
        app = cls.sb.QtWidgets.QApplication.instance()
        for _ in range(50):
            if cls.slots._initialized:
                break
            app.processEvents()
        if not cls.slots._initialized:
            cls.slots._initialize_ui()
        # uitk persists the mode combos and the In Context box; the tests
        # drive them, so hand the developer's panel back as it was found.
        cls._saved_state = (
            cls.ui.cmb000.currentIndex(),
            cls.ui.cmb001.currentIndex(),
            cls.ui.chk000.isChecked(),
        )

    @classmethod
    def tearDownClass(cls):
        source_index, retrieve_index, in_context = cls._saved_state
        cls.ui.cmb000.setCurrentIndex(source_index)
        cls.ui.cmb001.setCurrentIndex(retrieve_index)
        cls.ui.chk000.setChecked(in_context)
        super().tearDownClass()

    def setUp(self):
        super().setUp()  # scene reset
        _reset_store()
        self.messages = []
        self.answer = "Yes"
        self._orig_message_box = self.sb.message_box
        self.sb.message_box = self._message_box
        self.cube = cmds.polyCube(name="stash_panel_cube")[0]
        for t, v in KEYS:
            cmds.setKeyframe(self.cube, attribute="translateX", time=t, value=v)
        cmds.currentTime(1)
        self.ui.cmb000.setCurrentText("Playback Range")
        self.ui.cmb001.setCurrentText("Original Frames")
        self.ui.chk000.setChecked(True)
        self.slots.refresh()

    def tearDown(self):
        if self.slots.store.is_previewing():
            self.slots.store.end_preview()
        self.sb.message_box = self._orig_message_box
        _reset_store()
        super().tearDown()

    # ---- helpers -----------------------------------------------------------

    def _message_box(self, text, *buttons, **_kwargs):
        self.messages.append(text)
        return self.answer if buttons else None

    def _times(self):
        return (
            cmds.keyframe(f"{self.cube}.translateX", query=True, timeChange=True) or []
        )

    def _store_range(self, start=10, end=30):
        cmds.select(self.cube)
        cmds.playbackOptions(minTime=start, maxTime=end)
        self.slots.b000()
        clips = self.slots.store.clips
        self.assertTrue(clips, self.ui.footer.statusText())
        return clips[-1]

    def _rows(self):
        tree = self.ui.tree000
        return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]

    def _select_row(self, clip_id):
        for item in self._rows():
            if item.data(0, self.sb.QtCore.Qt.UserRole) == clip_id:
                self.ui.tree000.setCurrentItem(item)
                item.setSelected(True)
                return item
        self.fail(f"no row for clip {clip_id}")


class TestPanelSurface(_PanelCase):
    """The .ui parses into the house layout and every widget the slots address exists."""

    def test_widgets_resolve(self):
        for name in (
            "header",
            "store_group",
            "cmb000",
            "b000",
            "tree000",
            "clip_group",
            "cmb001",
            "b001",
            "b003",
            "chk000",
            "chk001",
            "footer",
        ):
            self.assertIsNotNone(getattr(self.ui, name, None), name)

    def test_titled_groups_frame_the_actions(self):
        QtWidgets = self.sb.QtWidgets
        self.assertIsInstance(self.ui.store_group, QtWidgets.QGroupBox)
        self.assertIsInstance(self.ui.clip_group, QtWidgets.QGroupBox)
        self.assertEqual(self.ui.store_group.title(), "Store")
        self.assertEqual(self.ui.clip_group.title(), "Selected Clip")
        self.assertIs(self.ui.cmb000.parentWidget(), self.ui.store_group)
        self.assertIs(self.ui.b001.parentWidget(), self.ui.clip_group)
        self.assertIs(self.ui.chk001.parentWidget(), self.ui.clip_group)

    def test_controls_use_the_19px_row(self):
        for name in ("cmb000", "b000", "cmb001", "b001", "b003", "chk000", "chk001"):
            self.assertEqual(getattr(self.ui, name).maximumHeight(), 19, name)

    def test_preview_and_in_context_are_checkboxes(self):
        QtWidgets = self.sb.QtWidgets
        self.assertIsInstance(self.ui.chk001, QtWidgets.QCheckBox)
        self.assertIsInstance(self.ui.chk000, QtWidgets.QCheckBox)
        self.assertEqual(self.ui.chk001.text(), "Preview")
        self.assertEqual(self.ui.chk000.text(), "In Context")

    def test_mode_combos_populate_with_display_prefixes(self):
        cmb000, cmb001 = self.ui.cmb000, self.ui.cmb001
        self.assertEqual(
            [cmb000.itemText(i) for i in range(cmb000.count())],
            list(self.slots.SOURCES),
        )
        self.assertEqual(
            [cmb001.itemText(i) for i in range(cmb001.count())],
            list(self.slots.RETRIEVE_AT),
        )
        self.assertEqual(cmb000.current_text_prefix, "Source:  ")
        self.assertEqual(cmb001.current_text_prefix, "Retrieve At:  ")

    def test_clip_list_shape(self):
        tree = self.ui.tree000
        self.assertEqual(type(tree).__name__, "TreeWidget")
        self.assertEqual(
            [tree.headerItem().text(i) for i in range(tree.columnCount())], COLUMNS
        )
        self.assertEqual(
            tree.selectionMode(), self.sb.QtWidgets.QAbstractItemView.SingleSelection
        )
        self.assertFalse(tree.rootIsDecorated())

    def test_header_buttons_and_help(self):
        header = self.ui.header
        for name in ("refresh", "collapse", "pin", "help"):
            self.assertIn(name, header.buttons, name)
        self.assertIn("Key Stash", header.help_text())

    def test_idle_footer_reports_the_clip_count(self):
        self.assertEqual(self.ui.footer.getDefaultStatusText(), "No stored clips")
        self._store_range()
        self.assertEqual(self.ui.footer.getDefaultStatusText(), "1 stored clip")

    def test_clip_actions_gate_on_selection(self):
        for name in ("b001", "b003", "chk001"):
            self.assertFalse(getattr(self.ui, name).isEnabled(), name)
        clip = self._store_range()
        self._select_row(clip.clip_id)
        for name in ("b001", "b003", "chk001"):
            self.assertTrue(getattr(self.ui, name).isEnabled(), name)


class TestPanelWorkflow(_PanelCase):
    """Every control drives the store, and the list reflects it."""

    def test_store_lists_the_clip(self):
        clip = self._store_range()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        item = rows[0]
        self.assertEqual(item.text(0), clip.label)
        self.assertEqual(item.text(1), "stash_panel_cube")
        self.assertEqual(item.text(2), "10 - 30")
        self.assertEqual(item.data(0, self.sb.QtCore.Qt.UserRole), clip.clip_id)
        self.assertEqual(self._times(), [1, 40])
        self.assertIn("Stored 3 keys", self.ui.footer.statusText())

    def test_store_without_a_selection_warns(self):
        cmds.select(clear=True)
        self.slots.b000()
        self.assertEqual(self.slots.store.clips, [])
        self.assertIn("Select the object", self.ui.footer.statusText())

    def test_retrieve_restores_the_keys_and_forgets_the_clip(self):
        clip = self._store_range()
        self._select_row(clip.clip_id)
        self.slots.b001()
        self.assertEqual(self._times(), [1, 10, 20, 30, 40])
        self.assertEqual(self.slots.store.clips, [])
        self.assertEqual(self._rows(), [])
        self.assertIn("Retrieved 3 keys", self.ui.footer.statusText())

    def test_retrieve_at_current_time(self):
        clip = self._store_range()
        self._select_row(clip.clip_id)
        self.ui.cmb001.setCurrentText("Current Time")
        cmds.currentTime(50)
        self.slots.b001()
        self.assertEqual(self._times(), [1, 40, 50, 60, 70])

    def test_drop_confirms_through_message_box(self):
        clip = self._store_range()
        stash_uuid = clip.curves[0]["stash"]["uuid"]
        self._select_row(clip.clip_id)
        self.answer = "No"
        self.slots.b003()
        self.assertEqual(len(self.messages), 1)
        self.assertIn(clip.label, self.messages[0])
        self.assertEqual(len(self.slots.store.clips), 1)
        self.assertTrue(cmds.ls(stash_uuid))
        self.answer = "Yes"
        self.slots.b003()
        self.assertEqual(self.slots.store.clips, [])
        self.assertFalse(cmds.ls(stash_uuid))
        self.assertEqual(self._rows(), [])

    def test_preview_checkbox_drives_the_store(self):
        """The checkbox's toggle reaches the slot (Switchboard wiring), both ways."""
        clip = self._store_range()
        self._select_row(clip.clip_id)
        self.ui.chk001.setChecked(True)
        store = self.slots.store
        self.assertTrue(store.is_previewing(clip.clip_id))
        self.assertTrue(self.ui.chk001.isChecked())
        self.assertFalse(self.ui.chk000.isEnabled())
        self.assertTrue(self._rows()[0].text(0).endswith("(previewing)"))
        self.ui.chk001.setChecked(False)
        self.assertFalse(store.is_previewing())
        self.assertFalse(self.ui.chk001.isChecked())
        self.assertTrue(self.ui.chk000.isEnabled())
        self.assertEqual(self._rows()[0].text(0), clip.label)

    def test_preview_without_a_selection_unchecks_itself(self):
        self.ui.chk001.setChecked(True)
        self.assertFalse(self.ui.chk001.isChecked())
        self.assertFalse(self.slots.store.is_previewing())
        self.assertIn("Select a stored clip", self.ui.footer.statusText())

    def test_double_click_selects_the_clip_objects(self):
        clip = self._store_range()
        cmds.select(clear=True)
        item = self._select_row(clip.clip_id)
        self.ui.tree000.itemDoubleClicked.emit(item, 0)
        self.assertEqual(cmds.ls(selection=True), [self.cube])

    def test_header_refresh_prunes_a_clip_whose_node_is_gone(self):
        clip = self._store_range()
        for node in cmds.ls([rec["stash"]["uuid"] for rec in clip.curves]):
            cmds.lockNode(node, lock=False)
            cmds.delete(node)
        self.assertEqual(len(self.slots.store.clips), 1)  # the record lags the scene
        self.ui.header.trigger_refresh()
        self.assertEqual(self.slots.store.clips, [])
        self.assertEqual(self._rows(), [])
        self.assertIn("Pruned 1 clip", self.ui.footer.statusText())

    def test_header_refresh_reports_a_clean_scene(self):
        self._store_range()
        self.ui.header.trigger_refresh()
        self.assertEqual(len(self.slots.store.clips), 1)
        self.assertIn("in sync", self.ui.footer.statusText())


if __name__ == "__main__":
    unittest.main()
