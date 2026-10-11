# !/usr/bin/python
# coding=utf-8
"""Panel-wiring tests for the ``shots.ui`` panel + ShotsSlots.

GUI-only (registered in ``run_tests.GUI_REQUIRED``): ``MayaUiHandler`` is
constructed against a GUI Maya, so the panel cannot load under mayapy.

Where ``test_shot_plan.py`` and ``test_sequencer.py`` cover the engine, this
covers what an engine test cannot see: the ``.ui`` carries the controls the
slots reach for, the option-box menus build, and every button SURVIVES BEING
PRESSED. That last one is the point of the module. A slot reads its
option-box widgets through helpers that live on the CONTROLLER, and nothing
about a widget existing says the slot can reach them -- a `self._option_checked`
written where `self` is the slots object raises only when the button is
pressed, and shipped once (2026-09-09, Apply Gap) behind assertions that only
read widgets.

Buttons are driven the way the sibling panel suites drive them (a direct
``slots.<name>()`` call), with an EMPTY store so every controller returns
early: this module is about the slots' own attribute access and the panel's
shape, not about moving keys.
"""

import unittest

from base_test import MayaTkTestCase
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

# Every button the panel's option boxes and groups own. Pressing each one is
# the cheapest guard against a slot that cannot reach what it reads.
BUTTONS = (
    "btn_apply_gap",
    "btn_shift_all",
    "btn_trim_all",
    "btn_trim_all_leading",
    "btn_trim_all_trailing",
    "btn_trim_all_both",
    "btn_move_shot",
    "btn_trim_empty",
    "btn_trim_leading",
    "btn_trim_trailing",
    "btn_trim_both",
    "btn_add_leading_space",
    "btn_add_trailing_space",
    "btn_delete_shots",
)


class TestShotsPanel(MayaTkTestCase):
    """The Shots panel loads, and its controls are reachable from its slots."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ui = MayaUiHandler.instance().get("shots")
        cls.slots = cls.ui.slots
        cls.sb = cls.slots.sb
        app = cls.sb.QtWidgets.QApplication.instance()
        for _ in range(50):
            app.processEvents()

    @classmethod
    def tearDownClass(cls):
        # The handler keeps the panel for the process, as a Maya session does,
        # but its store subscriptions must end with this class: a live panel
        # re-syncs every store a later module invalidates, and wrote a gap it
        # derived from the shot layout into test_shot_transfer's merged store
        # when that module ran after this one in one mayapy.
        cls.slots.controller.remove_callbacks()
        super().tearDownClass()

    def _store(self):
        return self.slots.controller._active_store()

    def test_panel_resolves_to_its_slots_and_controller(self):
        self.assertEqual(type(self.slots).__name__, "ShotsSlots")
        self.assertEqual(type(self.slots.controller).__name__, "ShotsController")

    def test_the_ui_carries_the_widgets_the_slots_reach_for(self):
        expected = (
            "cmb_detection_mode",
            "spn_detection",
            "spn_initial_length",
            "cmb_fit_mode",
            "chk_snap_whole_frames",
            "cmb_shot_select",
            "txt_shot_name",
            "spn_shot_start",
            "spn_shot_end",
            "txt_shot_desc",
            "spn_move_to",
            "spn_space",
            "btn_trim_empty",
            # All Shots group
            "btn_apply_gap",
            "btn_shift_all",
            "btn_trim_all",
            "btn_delete_shots",
        )
        missing = [n for n in expected if getattr(self.ui, n, None) is None]
        self.assertEqual(missing, [])

    def test_delete_shots_is_offered_while_the_store_has_shots(self):
        """All Shots > Delete Shots, on its default scope (Stale): enabled
        while the store holds a shot, and the click decides. Deleting an
        object raises no store event, so a state judged at the last one went
        stale -- the export's Open Shots link opened the panel with the button
        greyed out. With none stale it says so and deletes nothing; with one
        (every member gone, nothing keyed in its frames) it asks with the
        names and deletes that record only -- the live shot and its keys
        stay."""
        import maya.cmds as cmds
        from unittest import mock
        from pythontk import ShotBlock
        from qtpy import QtWidgets

        live = cmds.ls(cmds.polyCube(name="panelLive")[0], long=True)[0]
        gone = cmds.ls(cmds.polyCube(name="panelGone")[0], long=True)[0]
        for node, (first, last) in ((live, (1, 20)), (gone, (40, 60))):
            for t, v in ((first, 0), (last, 5)):
                cmds.setKeyframe(node, attribute="translateX", t=t, v=v)
        store = self._store()
        store.shots = [
            ShotBlock(1, "Live", 0, 20, [live]),
            ShotBlock(2, "Gone", 40, 60, [gone]),
        ]
        ctrl = self.slots.controller
        asked = []

        def answer(*args, **kwargs):
            asked.append(args[2])
            return QtWidgets.QMessageBox.Yes

        try:
            ctrl.refresh_state()
            self.ui.cmb_delete_scope.setCurrentIndex(0)
            self.assertTrue(self.ui.btn_delete_shots.isEnabled())
            with mock.patch.object(
                QtWidgets.QMessageBox, "question", new=staticmethod(answer)
            ):
                self.slots.btn_delete_shots()  # nothing stale yet
                self.assertEqual((asked, len(store.shots)), ([], 2))
                cmds.delete(gone)  # its keys go with it; no store event
                self.slots.btn_delete_shots()
            self.assertIn("Gone [40–60]", asked[0])
            self.assertEqual([s.name for s in store.shots], ["Live"])
            self.assertEqual(
                cmds.keyframe(live, query=True, timeChange=True), [1.0, 20.0]
            )
            store.shots = []
            ctrl.refresh_state()
            self.assertFalse(self.ui.btn_delete_shots.isEnabled())
        finally:
            store.shots = []
            store.set_active_shot(None)  # the re-sync picked the survivor

    def test_the_scope_decides_what_delete_shots_takes(self):
        """One button, three scopes, picked in its option box and named on
        the button (2026-10-07: "consolidate to one global delete shots
        button ... delete stale, delete all, delete empty")."""
        import maya.cmds as cmds
        from unittest import mock
        from pythontk import ShotBlock
        from qtpy import QtWidgets

        live = cmds.ls(cmds.polyCube(name="scopeLive")[0], long=True)[0]
        cmds.setKeyframe(live, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(live, attribute="translateX", t=20, v=5)
        store = self._store()
        cmb = self.ui.cmb_delete_scope
        self.assertEqual(
            [cmb.itemData(i) for i in range(cmb.count())], ["stale", "empty", "all"]
        )
        answer = staticmethod(lambda *a, **kw: QtWidgets.QMessageBox.Yes)
        try:
            for scope, label, left in (
                ("empty", "Delete Empty Shots", ["Live"]),
                ("all", "Delete All Shots", []),
            ):
                store.shots = [
                    ShotBlock(1, "Live", 0, 20, [live]),
                    ShotBlock(2, "Empty", 40, 60),
                ]
                cmb.setCurrentIndex(cmb.findData(scope))
                self.assertEqual(self.ui.btn_delete_shots.text(), label)
                with mock.patch.object(QtWidgets.QMessageBox, "question", new=answer):
                    self.slots.btn_delete_shots()
                self.assertEqual([s.name for s in store.shots], left, scope)
                self.assertEqual(
                    cmds.keyframe(live, query=True, timeChange=True), [1.0, 20.0]
                )
        finally:
            cmb.setCurrentIndex(0)
            store.shots = []
            store.set_active_shot(None)

    def test_the_option_box_menus_build_their_widgets(self):
        """uitk registers option-box widgets on the ui by objectName."""
        expected = (
            "chk_delete_contents",
            "chk_close_gap",
            "spn_gap",
            "cmb_gap_scope",
            "chk_override_locks",
            "cmb_delete_scope",
            "spn_shift_all",
        ) + BUTTONS
        missing = [n for n in expected if getattr(self.ui, n, None) is None]
        self.assertEqual(missing, [])

    def test_every_button_survives_being_pressed(self):
        """An empty store, so this is about the slot's own attribute access."""
        self._store().shots = []
        for name in BUTTONS:
            slot = getattr(self.slots, name, None)
            self.assertIsNotNone(slot, f"{name} has no slot method")
            with self.subTest(button=name):
                slot()

    def test_shift_all_defaults_to_zero(self):
        """A re-base target is about THIS sequence, not the last one."""
        self.assertAlmostEqual(self.ui.spn_shift_all.value(), 0.0)
        self.assertFalse(self.ui.spn_shift_all.restore_state)

    def test_override_locked_gaps_is_off_by_default(self):
        """A lock is honoured unless the user says otherwise."""
        self.assertFalse(self.ui.chk_override_locks.isChecked())

    def test_the_gap_amount_lives_in_apply_gaps_option_box(self):
        """Apply Gap is the group's button; the amount is one of its options
        (2026-10-07: "convert the global apply gap spinbox into a pushbutton
        and move the gap amount spinbox to its menu options")."""
        from qtpy import QtWidgets

        btn = self.ui.btn_apply_gap
        self.assertIsInstance(btn, QtWidgets.QPushButton)
        self.assertTrue(self.ui.all_shots.isAncestorOf(btn))
        menu = btn.option_box.menu
        self.assertTrue(menu.isAncestorOf(self.ui.spn_gap))
        self.assertTrue(menu.isAncestorOf(self.ui.cmb_gap_scope))
        # The store owns the amount: a QSettings restore would carry the last
        # scene's gap into this one.
        self.assertFalse(self.ui.spn_gap.restore_state)

    def test_apply_gap_names_the_amount_it_applies(self):
        """The amount is out of sight in the option box, so the button says it."""
        from pythontk import ShotBlock

        store = self._store()
        store.shots = [ShotBlock(1, "A", 0, 20, []), ShotBlock(2, "B", 32, 50, [])]
        old_gap = store.gap
        try:
            store.gap = 12.0
            self.slots.controller._sync_from_store()
            self.assertEqual(self.ui.spn_gap.value(), 12)
            self.assertEqual(self.ui.btn_apply_gap.text(), "Apply Gap: 12")
            self.ui.spn_gap.setValue(5)
            self.assertEqual(self.ui.btn_apply_gap.text(), "Apply Gap: 5")
        finally:
            store.gap = old_gap
            store.shots = []

    def test_gap_scope_combo_offers_every_scope(self):
        cmb = self.ui.cmb_gap_scope
        self.assertEqual(
            [cmb.itemData(i) for i in range(cmb.count())],
            ["all", "start", "end", "start_end"],
        )

    def test_override_locked_gaps_reaches_the_controller(self):
        """The checkbox has to arrive as ``respect_locks``, inverted."""
        seen = {}
        ctrl = self.slots.controller
        real = ctrl.on_gap_changed
        ctrl.on_gap_changed = lambda v, **kw: seen.update(kw)
        try:
            self.ui.chk_override_locks.setChecked(True)
            self.slots.btn_apply_gap()
            self.assertIs(seen.get("respect_locks"), False, seen)
            self.ui.chk_override_locks.setChecked(False)
            self.slots.btn_apply_gap()
            self.assertIs(seen.get("respect_locks"), True, seen)
        finally:
            ctrl.on_gap_changed = real
            self.ui.chk_override_locks.setChecked(False)

    def test_shift_to_is_a_button_naming_the_frame_in_its_option_box(self):
        """2026-10-10: "shift to should be a pushbutton in the same style as
        gap and delete buttons" -- Apply Gap's shape."""
        from qtpy import QtWidgets

        btn = self.ui.btn_shift_all
        self.assertIsInstance(btn, QtWidgets.QPushButton)
        self.assertTrue(self.ui.all_shots.isAncestorOf(btn))
        self.assertTrue(btn.option_box.menu.isAncestorOf(self.ui.spn_shift_all))
        try:
            self.ui.spn_shift_all.setValue(24)
            self.assertEqual(btn.text(), "Shift To: 24")
        finally:
            self.ui.spn_shift_all.setValue(0)
        self.assertEqual(btn.text(), "Shift To: 0")

    def _three_shots(self):
        from pythontk import ShotBlock

        store = self._store()
        store.shots = [
            ShotBlock(1, "A", 0, 20, []),
            ShotBlock(2, "B", 30, 50, []),
            ShotBlock(3, "C", 60, 80, []),
        ]
        store.set_active_shot(1)
        self.slots.controller._sync_from_store()
        return store

    def _rows(self):
        cmb = self.ui.cmb_shot_select
        return [(cmb.itemData(i), cmb.itemText(i)) for i in range(cmb.count())]

    def test_the_dropdown_follows_a_shot_picked_elsewhere(self):
        """2026-10-10: "the shots combobox doesn't update properly and is often
        missing shots during editing".  A pick in the Shot Sequencer reaches
        this panel as ActiveShotChanged, and the sync relabelled the row the
        dropdown still showed -- another shot's -- with the active one: C was
        listed twice and A was gone."""
        store = self._three_shots()
        try:
            store.set_active_shot(3)  # the Sequencer's pick
            self.assertEqual(
                self._rows(),
                [(1, "A  [0–20]"), (2, "B  [30–50]"), (3, "C  [60–80]")],
            )
            self.assertEqual(self.ui.cmb_shot_select.currentData(), 3)
            store.update_shot(1, name="A2")  # a shot that is not the active one
            self.assertEqual(self._rows()[0], (1, "A2  [0–20]"))
        finally:
            store.set_active_shot(None)
            store.shots = []

    def test_previous_and_next_step_through_the_shots(self):
        """2026-10-10: the Shot Sequencer dropdown's option-box buttons."""
        store = self._three_shots()
        ctrl = self.slots.controller
        nav = self.ui.cmb_shot_select._shots_nav_options
        try:
            self.assertEqual(sorted(nav), ["new", "next", "prev"])
            self.assertFalse(nav["prev"].widget.isEnabled(), "first shot")
            self.assertTrue(nav["next"].widget.isEnabled())
            ctrl.on_navigate_shot(1)
            ctrl.on_navigate_shot(1)
            self.assertEqual(store.active_shot_id, 3)
            self.assertEqual(self.ui.cmb_shot_select.currentData(), 3)
            self.assertFalse(nav["next"].widget.isEnabled(), "last shot")
            ctrl.on_navigate_shot(1)  # past the end: nothing
            self.assertEqual(store.active_shot_id, 3)
            ctrl.on_navigate_shot(-1)
            self.assertEqual(store.active_shot_id, 2)
        finally:
            store.set_active_shot(None)
            store.shots = []

    def test_new_shot_appends_one_initial_length_long(self):
        """The Shot Sequencer's New Shot, here: the gap after the last shot,
        Initial Length long, and it becomes the active shot.  Clickable with
        no shot at all -- the dropdown is disabled then."""
        store = self._store()
        old = (store.gap, store.initial_shot_length)
        nav = self.ui.cmb_shot_select._shots_nav_options
        try:
            store.shots = []
            self.slots.controller._sync_from_store()
            self.assertFalse(self.ui.cmb_shot_select.isEnabled())
            self.assertTrue(nav["new"].widget.isEnabled())
            store.gap, store.initial_shot_length = 10.0, 50.0
            self.slots.controller.on_new_shot()
            self.slots.controller.on_new_shot()
            first, second = store.sorted_shots()
            self.assertEqual(second.start - first.end, 10.0)
            self.assertEqual(second.end - second.start, 50.0)
            self.assertEqual(store.active_shot_id, second.shot_id)
            self.assertEqual(self.ui.cmb_shot_select.currentData(), second.shot_id)
        finally:
            store.gap, store.initial_shot_length = old
            store.set_active_shot(None)
            store.shots = []

    def test_shift_all_reports_a_sequence_already_where_it_is_asked_for(self):
        from pythontk import ShotBlock

        store = self._store()
        store.shots = [ShotBlock(1, "A", 0, 20, [])]
        self.ui.spn_shift_all.setValue(0)
        self.slots.btn_shift_all()
        self.assertEqual([(s.start, s.end) for s in store.sorted_shots()], [(0, 20)])

    def test_a_name_the_export_would_respell_is_refused_where_it_is_typed(self):
        """The name IS the exported clip name.  Typed into the field, one the
        export would respell is never stored: the field says why, and Enter
        gives the shot its own name back.  A legal one is stored as typed.

        Typed with ``keyClicks`` and handed to the field's slot directly, as
        the switchboard's debounce would: nothing here spins the event loop,
        so no idle-time work can land mid-test (setUp's new scene queues a
        script job that swaps the active store when Maya next idles).
        """
        from qtpy import QtCore, QtTest

        txt = self.ui.txt_shot_name
        store = self._store()
        store.shots = []
        shot = store.define_shot("Intro", 0, 10)
        store.set_active_shot(shot.shot_id)

        def type_name(text):
            txt.selectAll()
            QtTest.QTest.keyClicks(txt, text)
            self.slots.txt_shot_name()

        try:
            self.slots.controller._sync_shot_editor(store)
            self.assertEqual(txt.text(), "Intro")
            type_name("Intro 2")
            self.assertEqual(shot.name, "Intro")
            self.assertEqual(txt.property("actionState"), "invalid")
            self.assertIn("a space", txt.toolTip())
            QtTest.QTest.keyClick(txt, QtCore.Qt.Key_Return)
            self.assertEqual(txt.text(), "Intro")
            self.assertNotEqual(txt.property("actionState"), "invalid")
            type_name("Intro__2")
            self.assertEqual(shot.name, "Intro__2")
            self.assertNotEqual(txt.property("actionState"), "invalid")
        finally:
            # Leave the store as the other cases expect it: empty, no active shot.
            store.set_active_shot(None)
            store.shots = []


if __name__ == "__main__":
    unittest.main()
