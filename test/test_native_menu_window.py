"""End-to-end verification of Maya native menu wrapper styling and sizing.

Run via the test harness in a fresh Maya GUI instance. Validates that the
maya_native_menus wrappers match the visual contract of regular tentacle
windows (translucentBgWithBorder, frameless, top-level) and rigid-fit
their content (no dead space, no first-show flicker).
"""

import sys
import unittest

from qtpy import QtCore, QtWidgets

try:
    from base_test import MayaTkTestCase
except ImportError:
    from mayatk.test.base_test import MayaTkTestCase


SAMPLE_KEYS = ["edit", "select", "display"]


class TestNativeMenuWrapper(MayaTkTestCase):
    """Verify wrapper window styling, flags, and content fit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Marking menu pulls in the maya UI handler under a switchboard.
        from uitk import Switchboard
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        cls.sb = Switchboard()
        cls.handler = MayaUiHandler(switchboard=cls.sb)

    def setUp(self):
        super().setUp()
        self.created_uis = []

    def tearDown(self):
        for ui in self.created_uis:
            try:
                ui.close()
                ui.deleteLater()
            except Exception:
                pass
        QtWidgets.QApplication.processEvents()
        super().tearDown()

    def _wrap_menu(self, key):
        ui = self.handler.get(key)
        self.assertIsNotNone(ui, f"Wrapper UI is None for menu '{key}'")
        self.created_uis.append(ui)
        return ui

    def test_synchronous_population(self):
        """get_menu must return a populated wrapper (not empty + deferred)."""
        ui = self._wrap_menu("edit")
        embedded = ui.centralWidget()
        action_count = len(embedded.menu.actions())
        self.assertGreater(
            action_count, 3, "Menu was empty; deferred populate did not block"
        )

    def test_rigid_fit_to_content(self):
        """Window must open at exact content size (min == max == size)."""
        ui = self._wrap_menu("edit")
        ui.show()
        QtWidgets.QApplication.processEvents()

        embedded = ui.centralWidget()
        target = embedded.content_size()

        chrome_w = max(0, ui.width() - embedded.width())
        chrome_h = max(0, ui.height() - embedded.height())
        expected = QtCore.QSize(
            target.width() + chrome_w, target.height() + chrome_h
        )

        # Allow tiny variance for layout rounding / DPI.
        self.assertAlmostEqual(
            ui.height(), expected.height(), delta=8,
            msg=f"Window height {ui.height()} doesn't match content {expected.height()}",
        )
        # Locked: min == max
        self.assertEqual(
            ui.minimumSize(), ui.maximumSize(),
            f"Window not size-locked (min={ui.minimumSize()}, max={ui.maximumSize()})",
        )

    def test_window_flags_top_level_frameless(self):
        """Wrapper must be a frameless top-level window."""
        ui = self._wrap_menu("select")
        flags = ui.windowFlags()
        self.assertTrue(
            bool(flags & QtCore.Qt.Window),
            f"Window flag missing — flags={int(flags):#x}",
        )
        self.assertTrue(
            bool(flags & QtCore.Qt.FramelessWindowHint),
            f"FramelessWindowHint missing — flags={int(flags):#x}",
        )

    def test_translucent_background_attribute(self):
        """Wrapper must have WA_TranslucentBackground set (matches regular windows)."""
        ui = self._wrap_menu("display")
        self.assertTrue(
            ui.testAttribute(QtCore.Qt.WA_TranslucentBackground),
            "WA_TranslucentBackground not set on wrapper",
        )

    def test_border_class_applied(self):
        """Style class 'translucentBgWithBorder' must be on wrapper AND central widget."""
        ui = self._wrap_menu("edit")
        self.assertEqual(
            ui.property("class"), "translucentBgWithBorder",
            f"Wrapper class={ui.property('class')!r}",
        )
        self.assertEqual(
            ui.centralWidget().property("class"), "translucentBgWithBorder",
            f"Central class={ui.centralWidget().property('class')!r}",
        )

    def test_styled_background_paints(self):
        """Central widget must have WA_StyledBackground so the QSS actually paints.

        Without it the .translucentBgWithBorder rule matches but Qt skips
        painting on plain QWidgets — the wrapper appears borderless.
        """
        ui = self._wrap_menu("edit")
        central = ui.centralWidget()
        self.assertTrue(
            central.testAttribute(QtCore.Qt.WA_StyledBackground),
            "Central widget missing WA_StyledBackground — border won't paint",
        )

    def test_menu_does_not_cover_border(self):
        """The QMenu must be inset from wrapper edges so the painted border is visible."""
        ui = self._wrap_menu("edit")
        ui.show()
        QtWidgets.QApplication.processEvents()

        central = ui.centralWidget()
        menu = central.menu
        # 1 px layout contentsMargin reserves a strip for the painted border.
        self.assertGreater(menu.x(), 0, "Menu hugs left edge — border hidden")
        self.assertLess(
            menu.x() + menu.width(), central.width(),
            "Menu hugs right edge — border hidden",
        )
        self.assertLess(
            menu.y() + menu.height(), central.height(),
            "Menu hugs bottom edge — border hidden",
        )

    def test_header_present_with_buttons(self):
        """Header must be attached and configured with default buttons."""
        ui = self._wrap_menu("edit")
        header = getattr(ui, "header", None)
        self.assertIsNotNone(header, "Header missing")
        # Default header_buttons = ('menu', 'collapse', 'pin')
        self.assertGreater(
            len(getattr(header, "buttons", {})), 0,
            "Header buttons not configured",
        )

    def test_no_action_size_drift(self):
        """Multiple shows / hides must not progressively change the size."""
        ui = self._wrap_menu("edit")

        ui.show()
        QtWidgets.QApplication.processEvents()
        first = (ui.width(), ui.height())

        ui.hide()
        QtWidgets.QApplication.processEvents()
        ui.show()
        QtWidgets.QApplication.processEvents()
        second = (ui.width(), ui.height())

        self.assertEqual(first, second, "Size drifted across hide/show cycle")


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1], exit=False, verbosity=2)
