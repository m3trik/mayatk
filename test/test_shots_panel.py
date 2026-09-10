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
            "spn_gap",
            "spn_shift_all",
            "btn_trim_all",
            "btn_delete_all",
        )
        missing = [n for n in expected if getattr(self.ui, n, None) is None]
        self.assertEqual(missing, [])

    def test_the_option_box_menus_build_their_widgets(self):
        """uitk registers option-box widgets on the ui by objectName."""
        expected = (
            "chk_delete_contents",
            "chk_close_gap",
            "cmb_gap_scope",
            "chk_override_locks",
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

    def test_override_locked_gaps_is_off_by_default(self):
        """A lock is honoured unless the user says otherwise."""
        self.assertFalse(self.ui.chk_override_locks.isChecked())

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

    def test_shift_all_reports_a_sequence_already_where_it_is_asked_for(self):
        from pythontk import ShotBlock

        store = self._store()
        store.shots = [ShotBlock(1, "A", 0, 20, [])]
        self.ui.spn_shift_all.setValue(0)
        self.slots.btn_shift_all()
        self.assertEqual([(s.start, s.end) for s in store.sorted_shots()], [(0, 20)])


if __name__ == "__main__":
    unittest.main()
