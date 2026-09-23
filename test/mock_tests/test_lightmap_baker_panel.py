# !/usr/bin/python
# coding=utf-8
"""Structural load test for the Lightmap Baker panel (lightmap_baker.ui + slots).

Mocked ``maya.cmds`` (this dir's conftest) + a real ``Switchboard`` /
``MayaUiHandler`` load through real (offscreen) Qt -- the convention
``test_smart_bake_panel.py`` documents: real Maya and real Qt widgets in one
process crash natively.

What it is here for: the panel's four booleans are option-box switches
(``LightmapBakerSlots._TOGGLES``), and hanging one WRAPS its field -- the
widget is reparented into an ``OptionBoxContainer``. Under the runtime loader
a reparent can invalidate a ``QUiLoader``-built widget's shiboken wrapper, so
the stub-UI tests in ``test_lightmap_baker.py`` (which never build a real
widget) cannot answer whether ``self.ui.cmb_scope`` is still alive afterwards.
This builds the real thing and reads every wrapped field back.
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
class TestLightmapBakerPanelLoads(unittest.TestCase):
    """The panel loads, wraps its four fields, and still reads them."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uitk import Switchboard
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        cls.sb = Switchboard()
        cls.handler = MayaUiHandler(switchboard=cls.sb)
        cls.ui = cls.handler.get("lightmap_baker")
        # Flush the QTimer.singleShot(0, self._initialize_ui) deferred in
        # __init__ — Maya's own event loop does this immediately in real use.
        for _ in range(5):
            cls.app.processEvents()
        cls.slots = cls.ui.slots

    def _toggle(self, key):
        toggle = self.slots._toggle(key)
        self.assertIsNotNone(toggle, f"{key} has no toggle on its field")
        return toggle

    def test_ui_loads_as_the_slots_class(self):
        from mayatk.light_utils.lightmap_baker.lightmap_baker_slots import (
            LightmapBakerSlots,
        )

        self.assertIsNotNone(self.ui, "lightmap_baker UI failed to load")
        self.assertIsInstance(self.slots, LightmapBakerSlots)

    def test_every_switch_is_wired_to_its_field(self):
        from uitk.widgets.optionBox.options.toggle import ToggleOption
        from mayatk.light_utils.lightmap_baker.lightmap_baker_slots import (
            LightmapBakerSlots,
        )

        for key, (field, _default) in LightmapBakerSlots._TOGGLES.items():
            with self.subTest(key):
                on_field = getattr(self.ui, field).option_box.find_option(ToggleOption)
                self.assertIs(self._toggle(key), on_field)
                # The STATE is persisted per user, so only its type is pinned
                # here; the shipped defaults are the .ui's and are covered by
                # the stub tests.
                self.assertIsInstance(self.slots._toggle_state(key), bool)

    def test_a_wrapped_field_is_still_readable(self):
        """The wrap reparents the field; a dead wrapper raises RuntimeError
        here rather than at bake time. Types and ranges, not values -- the
        panel restores each field's last session."""
        self.assertIn(self.slots._scope(), ("selected", "visible", "scene"))
        self.assertIn(self.slots._resolution(), self.slots._RESOLUTIONS)
        self.assertGreaterEqual(self.ui.spn_samples.value(), 1)
        self.assertIsInstance(self.ui.txt_output_dir.text(), str)
        self.assertIn(self.slots._packing(), ("atlas", "per_object"))
        self.assertIn(self.slots._device(), ("AUTO", "GPU", "CPU"))

    def test_a_wrapped_field_is_still_state_managed(self):
        """The wrap must not drop the field out of the window's state -- that
        is what restores Resolution and Samples next session, and what Reset to
        Defaults puts them back to. Read through ``default_for``, not
        ``has_default``: a wrap can hand back a new wrapper, which is why
        ``StateManager`` mirrors the default onto the C++ object."""
        from uitk.managers.state_manager import StateManager

        state = StateManager.for_widget(self.ui)
        for field in ("cmb_scope", "spn_samples", "cmb_resolution", "txt_output_dir"):
            with self.subTest(field):
                self.assertIsNotNone(state.default_for(getattr(self.ui, field)))

    def test_a_wrapped_field_keeps_its_place_in_the_panel(self):
        """Its container stands where the field stood, under the same group --
        so the sections the .ui lays out survive the wrap."""
        for field, group in (
            ("cmb_scope", "central_widget"),
            ("spn_samples", "quality_group"),
            ("cmb_resolution", "quality_group"),
            ("txt_output_dir", "output_group"),
        ):
            with self.subTest(field):
                widget = getattr(self.ui, field)
                container = widget.option_box.container
                self.assertIs(container.parent(), getattr(self.ui, group))

    def test_a_panel_reset_reaches_the_switches(self):
        """Reset to Defaults resets FIELDS, and a switch is part of its field --
        this is the per-field pass ``StateManager.reset_all`` makes. Not the
        whole panel: that would write over this machine's saved panel state."""
        toggle = self._toggle("denoise")
        was, default = toggle.is_on, toggle._default_on()
        try:
            toggle.set_on(not default)
            self.ui.cmb_resolution.option_box.restore_option_defaults()
            self.assertEqual(toggle.is_on, default)
        finally:
            toggle.set_on(was)

    def test_the_adaptive_switch_greys_out_on_a_cpu_bake(self):
        """A CPU bake spends its samples the other way, so the switch says so.
        The rule targets the toggle's BUTTON: the Samples field stays live."""
        button = self._toggle("adaptive").widget
        self.ui.cmb_device.setCurrentIndex(0)  # Auto
        self.app.processEvents()
        self.assertTrue(button.isEnabled())

        self.ui.cmb_device.setCurrentIndex(2)  # CPU
        self.app.processEvents()
        self.assertEqual(self.slots._device(), "CPU")
        self.assertFalse(button.isEnabled(), "adaptive does not apply on the CPU")
        self.assertTrue(self.ui.spn_samples.isEnabled(), "Samples still do")

        self.ui.cmb_device.setCurrentIndex(0)  # Auto
        self.app.processEvents()
        self.assertTrue(button.isEnabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
