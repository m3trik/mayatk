# !/usr/bin/python
# coding=utf-8
"""Render Effects panel (render_effects.ui + RenderEffectsSlots), loaded for real.

Mocked ``maya.cmds`` (this dir's conftest) + a real ``Switchboard`` /
``MayaUiHandler`` load through real (offscreen) Qt -- the convention of
test_naming_panel.py. Covers what a stub UI cannot see: the pages the init
slots build, how their fields bind to the scene's effect recipe, the readouts
they keep, and the focus the Shot Manifest opens. The recipe lives on a pure
``ShotStore`` here (the Maya store needs a scene); keying against real nodes is
covered by test_render_opacity.py (run under mayapy).
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

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
class TestRenderEffectsPanel(unittest.TestCase):
    """The panel loads through the real discovery + compile path."""

    @classmethod
    def setUpClass(cls):
        import pythontk as ptk
        from pythontk import TestSandbox

        sandbox = TestSandbox.user_config()
        sandbox.__enter__()
        cls.addClassCleanup(sandbox.__exit__, None, None, None)

        # The scene's recipe on a pure store of its own (class-level ``_active``).
        class Store(ptk.ShotStore):
            _active = None
            _persistence = None
            _invalidation_listeners = []

        cls.Store = Store
        from mayatk.mat_utils.render_opacity import render_effects_slots as slots_mod
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        for patcher in (
            patch.object(
                slots_mod.RenderEffectsSlots, "_store_cls", staticmethod(lambda: Store)
            ),
            patch.object(
                RenderEffects,
                "scene_recipe",
                staticmethod(lambda: Store.active().effect_recipe),
            ),
        ):
            patcher.start()
            cls.addClassCleanup(patcher.stop)

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        cls.handler = MayaUiHandler(switchboard=Switchboard())
        cls.ui = cls.handler.get("render_effects")
        for _ in range(5):
            cls.app.processEvents()
        cls.slots = cls.handler.sb.get_slots_instance(cls.ui)

    def setUp(self):
        self.Store.active().update_effect_recipe(**self.Store().effect_recipe.to_dict())

    def test_the_picker_shows_one_page_per_effect(self):
        self.assertEqual(
            [self.ui.cmb_effect.itemText(i) for i in range(self.ui.cmb_effect.count())],
            ["Opacity Fade", "Highlight Pulse"],
        )
        self.ui.cmb_effect.setCurrentIndex(1)
        self.addCleanup(self.ui.cmb_effect.setCurrentIndex, 0)
        self.assertIs(self.ui.stk_effects.currentWidget(), self.ui.page_pulse)

    def test_each_page_opens_on_create(self):
        """The help promises each page opens on Create: a mode restored from
        the user's settings would reopen a page in Revise."""
        for name in ("cmb_mode_opacity", "cmb_mode_highlight"):
            combo = getattr(self.ui, name)
            self.assertFalse(combo.restore_state, name)
            self.assertEqual(combo.currentData(), "create", name)

    def test_the_fields_are_the_scenes_recipe(self):
        """The fade's frames and the pulse's cadence show the scene's recipe,
        and an edit writes it -- what the manifest's Build keys with."""
        self.assertEqual(self.ui.s000.value(), 15)
        self.assertAlmostEqual(self.ui.s002.value(), 2.86)
        self.assertEqual(self.ui.s003.value(), 59)
        self.ui.s002.setValue(2.0)
        self.ui.s003.setValue(40)
        recipe = self.Store.active().effect_recipe
        self.assertEqual((recipe.pulse_period, recipe.pulse_duty), (2.0, 0.4))

    def test_a_recipe_changed_elsewhere_shows_here(self):
        self.Store.active().update_effect_recipe(fade_frames=24, pulse_lead_in=0.5)
        self.assertEqual(self.ui.s000.value(), 24)
        self.assertAlmostEqual(self.ui.s004.value(), 0.5)

    def test_recipe_fields_never_restore_from_qsettings(self):
        """A restore lands after the recipe was read and would write the last
        value set in ANY scene over this one's."""
        for name in ("s000", "s002", "s003", "s004", "s005"):
            self.assertFalse(getattr(self.ui, name).restore_state, name)
        # The panel's own fields still persist per user.
        self.assertTrue(self.ui.s001.restore_state)

    def test_the_cycle_readout_counts_the_train_between_the_leads(self):
        """The leads come out of the length. At the defaults (4 s, a 2.86 s
        period, 0.72 s leads) the keys hold ONE bright beat; the readout,
        dividing the whole length by the period, said 1.4."""
        self.assertIn("0.9", self.ui.lbl_cycles.text())
        self.ui.s004.setValue(0.0)  # the two leads move together
        self.app.processEvents()
        self.assertEqual(self.ui.s005.value(), 0.0, "the leads are linked")
        self.assertIn("1.4", self.ui.lbl_cycles.text())

    def test_create_shows_the_recipe_colours_and_writes_them(self):
        self.Store.active().update_effect_recipe(pulse_dim=(0.2, 0.0, 0.0))
        bright, dim = self.slots._pulse_ramp.decided()
        self.assertEqual(tuple(round(c, 3) for c in dim), (0.2, 0.0, 0.0))

    def test_focus_names_the_object_and_keys_through_the_manifest(self):
        calls = []

        def apply():
            calls.append("apply")
            return "Re-applied Red_Door's behaviors in S03."

        self.slots.focus(
            "highlight",
            [],
            title="Red_Door · S03",
            apply=apply,
            apply_text="Apply to 'Red_Door' in S03",
        )
        self.addCleanup(self.slots.unfocus)
        self.assertTrue(self.ui.cmb_effect.isHidden())
        self.assertIs(self.ui.stk_effects.currentWidget(), self.ui.page_pulse)
        self.assertIn("RED_DOOR", self.ui.header.title())
        self.assertEqual(self.ui.b000.text(), "Apply to 'Red_Door' in S03")

        self.slots.b000()
        self.assertEqual(calls, ["apply"])
        self.assertIn("Red_Door", self.ui.footer.statusText())

        self.slots.unfocus()
        self.assertFalse(self.ui.cmb_effect.isHidden())
        self.assertEqual(self.ui.b000.text(), "Key Highlight Pulse")

    def test_hiding_the_panel_ends_the_focus(self):
        self.slots.focus("opacity", [], title="Lid · S01", apply=lambda: "")
        self.ui.on_hide.emit()
        self.assertIsNone(self.slots._focus)
        self.assertFalse(self.ui.cmb_effect.isHidden())

    # -- one action row, and only shared options in the header -------------

    def test_one_action_row_serves_whichever_page_shows(self):
        """Key, the remove and WebXR are the window's, under the pages; Key
        says which effect it keys."""
        for index, text in ((0, "Key Opacity Fade"), (1, "Key Highlight Pulse")):
            self.ui.cmb_effect.setCurrentIndex(index)
            self.assertEqual(self.ui.b000.text(), text)
            self.assertIsNone(
                self.ui.stk_effects.currentWidget().findChild(
                    QtWidgets.QPushButton, "b000"
                )
            )
        self.ui.cmb_effect.setCurrentIndex(0)
        self.assertEqual(self.ui.btn_remove.text(), "Remove")

    def test_key_is_twice_a_row_and_set_apart(self):
        """Key reads as the panel's one action: twice the picker's height,
        with the same gap above it as under the picker."""
        self.assertEqual(
            self.ui.b000.maximumHeight(), 2 * self.ui.cmb_effect.maximumHeight()
        )
        side = self.ui.centralWidget().findChild(
            QtWidgets.QVBoxLayout, "sideActionsLayout"
        )
        self.assertLess(
            side.indexOf(self.ui.btn_webxr), side.indexOf(self.ui.btn_remove)
        )
        self.assertEqual(side.spacing(), 0)
        self.assertEqual(
            self.ui.btn_webxr.maximumHeight() + self.ui.btn_remove.maximumHeight(),
            self.ui.b000.maximumHeight(),
            "WebXR over Remove is exactly Key's height",
        )
        layout = self.ui.centralWidget().layout()
        gaps = {
            layout.itemAt(i).spacerItem().sizeHint().height()
            for i in range(layout.count())
            if layout.itemAt(i).spacerItem() is not None
            and layout.itemAt(i).spacerItem().sizePolicy().verticalPolicy()
            == QtWidgets.QSizePolicy.Fixed
        }
        self.assertEqual(gaps, {10})

    def test_the_header_holds_only_options_every_effect_shares(self):
        """Delete Visibility Keys acts only when a fade first makes the opacity
        channel, so it is the Fade page's -- and only in Create."""
        menu = self.ui.header.menu
        self.assertIsNotNone(getattr(menu, "chk_last_selected", None))
        self.assertIsNone(getattr(menu, "chk_delete_vis_keys", None))
        option = self.slots.ui_field("chk_delete_vis_keys")
        self.assertTrue(self.ui.page_fade.isAncestorOf(option))
        self.ui.cmb_mode_opacity.setCurrentIndex(1)  # Revise
        self.addCleanup(self.ui.cmb_mode_opacity.setCurrentIndex, 0)
        self.assertTrue(option.isHidden())

    # -- the window follows what the panel shows ---------------------------

    def _shown(self, page=0):
        """The panel on screen at *page* (a hidden window is left to the fit
        its show makes)."""
        self.ui.cmb_effect.setCurrentIndex(page)
        self.addCleanup(self.ui.cmb_effect.setCurrentIndex, 0)
        self.ui.show()
        self.addCleanup(self.ui.hide)
        self._turn()

    def _turn(self):
        """Long enough for a deferred window fit to land."""
        from qtpy import QtCore

        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(30, loop.quit)
        loop.exec_() if hasattr(loop, "exec_") else loop.exec()

    def test_the_window_follows_the_page_it_shows(self):
        """A stack is as tall as its tallest page, so the Fade page sat in a
        window sized for the Pulse page, whichever was picked."""
        self._shown(page=0)
        fade = self.ui.height()
        self.ui.cmb_effect.setCurrentIndex(1)
        self._turn()
        self.assertGreater(self.ui.height(), fade)
        self.ui.cmb_effect.setCurrentIndex(0)
        self._turn()
        self.assertEqual(self.ui.height(), fade)

    # -- a page folds to its mode and Key ----------------------------------

    def _fold(self, channel):
        """*channel*'s Settings fold, opened again when the test ends."""
        group = getattr(self.slots._pages[channel], f"grp_settings_{channel}")
        self.addCleanup(group.setChecked, True)
        return group

    def test_a_page_folds_to_its_mode_and_key(self):
        """Live report (2026-10-03): set once, a page's fields still stood
        between its mode and Key for every keying after."""
        self._shown(page=1)
        opened = self.ui.height()
        fold = self._fold("highlight")
        fold.setChecked(False)
        self._turn()
        self.assertLess(self.ui.height(), opened)
        self.assertFalse(self.ui.s001.isVisible())
        self.assertTrue(self.ui.cmb_mode_highlight.isVisible(), "the mode stays")
        self.assertTrue(self.ui.b000.isVisible())
        fold.setChecked(True)
        self._turn()
        self.assertEqual(self.ui.height(), opened)

    def test_a_folded_page_stays_folded_through_a_mode_switch(self):
        """A mode shows its fields INSIDE the fold: folded, the page shows
        none of them, and opening it shows that mode's, at that mode's height."""
        from uitk.managers.window_height import WindowHeight

        self._shown(page=1)
        fold = self._fold("highlight")
        fold.setChecked(False)
        self._turn()
        folded = self.ui.height()
        colours = self.slots.ui_field("pulse_colors")
        self.ui.cmb_mode_highlight.setCurrentIndex(1)  # Revise
        self.addCleanup(self.ui.cmb_mode_highlight.setCurrentIndex, 0)
        self._turn()
        self.assertFalse(colours.isVisible())
        self.assertEqual(self.ui.height(), folded)
        fold.setChecked(True)
        self._turn()
        self.assertTrue(colours.isVisible())
        self.assertFalse(self.ui.s001.isVisible(), "Revise's fields, not Create's")
        opened = self.ui.height()
        WindowHeight.fit_host(self.ui)
        self.assertEqual(self.ui.height(), opened, "already fitted")

    def test_the_colours_open_their_advanced_rows_inside_the_fold(self):
        """One Advanced across both ends, folded inside Settings -- not a fold
        per colour column."""
        from uitk.widgets.separator import Separator

        ramp = self.slots._pulse_ramp
        folds = [s for s in ramp.findChildren(Separator) if s.isCheckable()]
        self.assertEqual(folds, [ramp._disclosure])
        fold = self.slots._pages["highlight"].grp_settings_highlight
        self.assertTrue(fold.isAncestorOf(ramp._disclosure))

    def test_ending_the_focus_puts_back_the_readout_and_the_height(self):
        """A hide ends the focus and the selection job skips a hidden panel,
        so the manifest's line outlived it."""
        self._shown(page=1)
        before = self.ui.height()
        self.slots.focus("highlight", [], title="Red_Door · S03", apply=lambda: "")
        self.addCleanup(self.slots.unfocus)
        self._turn()
        self.assertIn("Re-keys", self.ui.footer.getDefaultStatusText())

        self.ui.on_hide.emit()
        self._turn()
        self.assertNotIn("Re-keys", self.ui.footer.getDefaultStatusText())
        self.assertEqual(self.ui.height(), before)

    # -- the footer says what Key will do ----------------------------------

    def test_the_footer_rests_on_what_the_shown_pages_key_will_do(self):
        """The line that stood under the selector, one short line in the
        footer, its counts in bold -- for the page on show only."""
        self._shown(page=1)
        self.assertEqual(self.ui.footer.getDefaultStatusText(), "Needs a selection")
        self.ui.cmb_mode_highlight.setCurrentIndex(1)  # Revise
        self.addCleanup(self.ui.cmb_mode_highlight.setCurrentIndex, 0)
        self.assertEqual(
            self.ui.footer.getDefaultStatusText(),
            "Re-colours <b>every</b> highlight object in the scene",
        )
        self.assertIsNone(getattr(self.ui, "lbl_apply_highlight", None))

    def test_a_report_stands_until_the_next_page_or_mode(self):
        """A report is about the action that made it; switching page or mode
        is a new question, which the resting line answers."""
        self._shown(page=0)
        self.ui.footer.setText("Fade In: 2 object(s), frames 10-25")
        self.slots._on_selection_changed()  # what a tool runs after its write
        self.assertEqual(
            self.ui.footer.statusText(), "Fade In: 2 object(s), frames 10-25"
        )
        self.ui.cmb_effect.setCurrentIndex(1)
        self.assertEqual(self.ui.footer.statusText(), "")
        self.ui.footer.setText("Highlight pulse: 2 object(s)")
        self.ui.cmb_mode_highlight.setCurrentIndex(1)
        self.addCleanup(self.ui.cmb_mode_highlight.setCurrentIndex, 0)
        self.assertEqual(self.ui.footer.statusText(), "")

    def test_a_pick_in_the_scene_ends_the_report(self):
        self._shown(page=0)
        self.ui.footer.setText("Opacity removed from 2 object(s)")
        self.slots._on_scene_selection()  # the selection job
        self.assertEqual(self.ui.footer.statusText(), "")
        self.assertEqual(self.ui.footer.text(), "Needs a selection")


if __name__ == "__main__":
    unittest.main()
