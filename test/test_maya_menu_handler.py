# !/usr/bin/python
# coding=utf-8
import unittest
import time
from unittest import mock
from qtpy import QtWidgets, QtCore, QtGui
try:
    from base_test import MayaTkTestCase
except ImportError:
    from mayatk.test.base_test import MayaTkTestCase

from mayatk.ui_utils.maya_native_menus import MayaNativeMenus as MayaMenuHandler, EmbeddedMenuWidget
import maya.cmds as cmds


class TestNativeMenuFailFast(MayaTkTestCase):
    """A native-menu mapping that's stale for this Maya version must fail fast.

    Several MENU_MAPPING entries are version-specific and raise on newer Maya
    (e.g. ``buildToonMenu`` removed, ``buildHelpMenu`` arity changed,
    ``mainCacheMenu`` shell gone). When the init command raises, ``get_menu``
    must bail immediately and return ``None`` — NOT build a placeholder and spin
    the (now 6-attempt) ``processEvents`` populate loop, which was the bulk of
    the multi-second launch stall a broken menu caused.
    """

    def setUp(self):
        super().setUp()
        self.handler = MayaMenuHandler()

    def test_init_failure_returns_none_without_populating(self):
        # "edit" normally builds fine; force its init command to raise so the
        # test is deterministic and independent of which mappings are stale.
        with mock.patch(
            "mayatk.ui_utils.maya_native_menus.mel.eval",
            side_effect=RuntimeError("simulated stale menu proc"),
        ), mock.patch.object(
            self.handler,
            "_populate_menu",
            side_effect=AssertionError("populate loop must not run on init failure"),
        ):
            result = self.handler.get_menu("edit")

        self.assertIsNone(result, "get_menu must return None when init raises")
        self.assertNotIn(
            "edit", self.handler.menus, "no placeholder may be cached on failure"
        )

    def test_init_failure_is_fast(self):
        # Without the early-out this spun 15x processEvents (seconds). Bail
        # makes it near-instant; allow generous headroom for the menu-set switch.
        with mock.patch(
            "mayatk.ui_utils.maya_native_menus.mel.eval",
            side_effect=RuntimeError("simulated stale menu proc"),
        ):
            t0 = time.time()
            result = self.handler.get_menu("edit")
            elapsed = time.time() - t0

        self.assertIsNone(result)
        self.assertLess(
            elapsed, 3.0, f"failed native-menu build must be fast, took {elapsed:.2f}s"
        )


class TestMayaMenuHandlerExtended(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.handler = MayaMenuHandler()
        self.test_menu_name = "TestMenu"
        self.menu_key = "test_menu"
        self.top_windows = []

    def tearDown(self):
        for win in self.top_windows:
            try:
                win.close()
                win.deleteLater()
            except:
                pass
        super().tearDown()

    def create_mock_mainwindow_structure(self, embedded_widget):
        """Creates a structure resembling the user's setup: MainWindow -> Central -> Layout -> Menu + Footer"""
        window = QtWidgets.QMainWindow()
        central = QtWidgets.QWidget()
        window.setCentralWidget(central)

        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Add the menu widget
        layout.addWidget(embedded_widget)

        # Add a footer
        footer = QtWidgets.QLabel("Footer Content")
        footer.setFixedHeight(30)
        footer.setStyleSheet("background-color: blue;")
        layout.addWidget(footer)

        window.resize(300, 400)  # Initial size
        return window

    def test_size_hints_logic(self):
        """
        Test that minimumSizeHint matches sizeHint content size.
        """
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)
        self.top_windows.append(widget)

        # Case 0: Check Minimum Size Hint (Should match base size of sizeHint fallback)
        min_hint = widget.minimumSizeHint()
        self.assertTrue(min_hint.isValid())
        self.assertEqual(
            min_hint.height(), 100, "Empty menu should have base minimum height (100px)"
        )

        # Case 1: Check Size Hint with Items (Should grow OR equal floor)
        # Real Maya menus return ~24-26px per row via actionGeometry; in
        # offscreen test env actionGeometry returns ~20px, so 5 items = 100,
        # equal to the empty floor. Either way, the hint must be >= floor.
        for i in range(5):
            menu.addAction(f"Item {i}")

        size_hint_5 = widget.sizeHint()
        self.assertGreaterEqual(
            size_hint_5.height(), 100, "Size hint should be >= empty floor"
        )
        self.assertLess(size_hint_5.height(), 200, "5 items should not exceed 200px")

        # Minimum size hint == size hint (rigid-fit contract)
        min_hint_5 = widget.minimumSizeHint()
        self.assertEqual(
            min_hint_5, size_hint_5,
            "minimumSizeHint must equal sizeHint (rigid-fit)",
        )

    def test_window_resizing_with_footer(self):
        """
        Simulate the exact scenario of populating a menu inside a window with a footer,
        ensure it expands and doesn't collapse.
        """
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)

        window = self.create_mock_mainwindow_structure(widget)
        self.top_windows.append(window)
        window.show()
        QtWidgets.QApplication.processEvents()

        initial_height = window.height()
        # Verify initial state
        self.assertGreater(initial_height, 0)

        # --- SIMULATE LOADING ---
        # Add a LOT of items to ensure it exceeds current window size
        items_to_add = 20  # 20 * 26 = 520px
        for i in range(items_to_add):
            menu.addAction(f"Deferred Item {i}")

        # The widget should now WANT to be bigger, but might not be yet.
        # Per-row pixel varies by env: real Maya ~26, offscreen ~20.
        required_menu_height = items_to_add * 18  # Conservative floor
        recommended_hint = widget.sizeHint()
        self.assertGreater(
            recommended_hint.height(),
            required_menu_height,
            "Size hint should reflect large item count",
        )

        # --- EXECUTE RESIZE LOGIC (Mirroring handler code) ---
        widget.updateGeometry()  # Tell layout system widget has changed

        if window.layout():
            window.layout().activate()

        window.adjustSize()

        # Process resulting events
        QtWidgets.QApplication.processEvents()

        final_height = window.height()

        print(
            f"\n[TestWithFooter] Initial: {initial_height}, Final: {final_height}, Menu Req: {required_menu_height}"
        )

        # 1. Window must have grown
        self.assertGreater(
            final_height, initial_height, "Window did not expand to fit content"
        )

        # 2. Window must be taller than just the menu (menu + footer + margins)
        # Footer is 30px
        min_expected_total = required_menu_height + 30
        self.assertGreaterEqual(
            final_height, min_expected_total, "Window is cutting off content/footer"
        )

    def test_resize_logic_does_not_shrink(self):
        """
        Ensure that if we have a small menu, the window doesn't unexpectedly shrink
        to a tiny size (unusable).
        """
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)
        window = self.create_mock_mainwindow_structure(widget)
        self.top_windows.append(window)
        # Start large
        window.resize(500, 600)
        window.show()
        QtWidgets.QApplication.processEvents()

        start_height = window.height()

        # Add just 1 item
        menu.addAction("One Item")

        # Execute resize logic
        widget.updateGeometry()
        window.adjustSize()
        QtWidgets.QApplication.processEvents()

        final_height = window.height()
        print(f"\n[TestNoShrink] Start: {start_height}, Final: {final_height}")

        # Should shrink to fit, BUT not below the safety minimum of the widget
        # Widget min safety is 100px. Footer is 30px. Total ~130px.
        self.assertGreaterEqual(final_height, 130, "Window shrank too much!")

    def test_deferred_behavior_simulation(self):
        """
        Simulate the timing interaction using a timer to act like the real handler.
        """
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)
        window = self.create_mock_mainwindow_structure(widget)
        self.top_windows.append(window)
        window.resize(300, 200)
        window.show()

        # Define the deferred callback similar to the handler
        def deferred_populate():
            # Add items
            for i in range(15):
                menu.addAction(f"Async Item {i}")

            # Trigger resize logic
            widget.updateGeometry()
            window.adjustSize()

        # Schedule it
        QtCore.QTimer.singleShot(100, deferred_populate)

        # Wait loop
        start_time = time.time()
        while time.time() - start_time < 2.0:  # Wait up to 2.0s
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)

        final_height = window.height()
        expected_min = (15 * 20) + 30  # Items + Footer (Conservative estimate)

        print(f"\n[TestDeferred] Final: {final_height}, Expected Min: {expected_min}")
        self.assertGreaterEqual(
            final_height, expected_min, "Deferred resize failed to expand window"
        )

    def test_content_resizing_behavior(self):
        """
        Verify that the embedded menu actually expands to fill the hosting widget.
        The user reported contents do not resize with the window.
        """
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)
        window = QtWidgets.QMainWindow()
        window.setCentralWidget(widget)
        window.resize(400, 600)
        window.show()
        self.top_windows.append(window)

        # Add a test item
        menu.addAction("Test Item")

        QtWidgets.QApplication.processEvents()

        # Check widths
        # The central widget (our EmbeddedMenuWidget) should be close to 400
        container_width = widget.width()
        # The embedded QMenu should also be close to 400
        menu_width = menu.width()

        print(
            f"\n[TestResizing] Container Width: {container_width}, Menu Width: {menu_width}"
        )

        # We expect the menu to fill the width (delta allows for scrollbars/frames)
        # If QMenu behaves like a fixed-width popup, this will fail
        self.assertAlmostEqual(
            menu_width,
            container_width,
            delta=20,
            msg="Menu width should match container width (contents not resizing)",
        )

    def test_rapid_update_stability(self):
        """
        Simulate rapid/repeated calls to the resize logic which might cause
        race conditions resulting in an empty (zero size) window.
        """
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)
        window = self.create_mock_mainwindow_structure(widget)
        self.top_windows.append(window)
        window.show()

        # Fill with items
        item_count = 10
        for i in range(item_count):
            menu.addAction(f"Item {i}")

        initial_height = window.height()
        # Per-row varies by env (real Maya ~26, offscreen ~20), so just
        # ensure the size is stable across rapid updates.
        for _ in range(5):
            widget.updateGeometry()
            if window.layout():
                window.layout().activate()
            window.adjustSize()
            QtWidgets.QApplication.processEvents()

        final_height = window.height()
        print(f"\n[TestRapidUpdate] Height: {final_height}")

        # Stability: not collapsed; accommodates content + footer.
        self.assertGreater(
            final_height, 100, "Window collapsed after rapid updates"
        )
        # Idempotent: another round of updates shouldn't change the size.
        for _ in range(3):
            widget.updateGeometry()
            if window.layout():
                window.layout().activate()
            window.adjustSize()
            QtWidgets.QApplication.processEvents()
        self.assertEqual(
            window.height(), final_height,
            "Repeated updates should be idempotent",
        )

    def test_menu_visibility_retention(self):
        """
        Test that the menu remains visible after resizing operations.
        Reproducing 'seemingly empty window' (if contents hide).
        """
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)
        window = self.create_mock_mainwindow_structure(widget)
        self.top_windows.append(window)
        window.show()

        menu.addAction("Test Item")

        widget.updateGeometry()
        window.adjustSize()
        QtWidgets.QApplication.processEvents()

        self.assertTrue(menu.isVisible(), "Menu became hidden after resize!")
        self.assertFalse(menu.isHidden(), "Menu is explicitly hidden!")
        # Check if geometry has non-zero area
        self.assertGreater(menu.width() * menu.height(), 0, "Menu has zero area!")

    def test_resize_constraint(self):
        """Verify the rigid-fit contract: minimumSizeHint == sizeHint == content size."""
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)
        window = self.create_mock_mainwindow_structure(widget)
        self.top_windows.append(window)
        window.show()

        for i in range(50):
            menu.addAction(f"Item {i}")

        content_hint = widget.sizeHint()
        print(f"Calculated Content Hint: {content_hint}")

        # 50 items * (~20-26)px per row → hint floor ~ 1000 (offscreen 20px row).
        self.assertGreaterEqual(
            content_hint.height(), 1000, "Content hint should be large for 50 items"
        )

        # Rigid-fit: min hint == size hint.
        min_hint = widget.minimumSizeHint()
        self.assertEqual(
            min_hint, content_hint,
            "minimumSizeHint must equal sizeHint (rigid-fit)",
        )

        # adjustSize honors the size hint: window grows to fit content.
        window.adjustSize()
        QtWidgets.QApplication.processEvents()

        current_height = window.height()
        print(f"adjustSize Height: {current_height}")
        self.assertGreaterEqual(
            current_height, content_hint.height(),
            "Window should grow to at least content hint after adjustSize",
        )

    def test_expand_and_shrink(self):
        """
        Test that the window can be expanded and then shrunk back horizontally.
        Regression test for 'setMinimumWidth(self.width())' locking.
        """
        menu = QtWidgets.QMenu()
        widget = EmbeddedMenuWidget(menu)
        window = self.create_mock_mainwindow_structure(widget)
        self.top_windows.append(window)
        # Start small
        window.resize(300, 400)
        window.show()
        QtWidgets.QApplication.processEvents()

        initial_width = window.width()

        # 1. Expand
        window.resize(800, 400)
        QtWidgets.QApplication.processEvents()

        expanded_width = window.width()
        # Verify it expanded
        self.assertEqual(expanded_width, 800)

        # Verify menu expanded (per previous fix)
        # self.assertAlmostEqual(menu.width(), 800, delta=20)

        # 2. Check Min Size Hint
        # The min size hint should NOT be locked to 800
        min_hint = widget.minimumSizeHint()
        print(f"Min Hint after expansion: {min_hint.width()}")
        self.assertLess(
            min_hint.width(), 700, "Minimum width hint ballooned after expansion!"
        )

        # 3. Shrink
        window.resize(400, 400)
        QtWidgets.QApplication.processEvents()

        shrunk_width = window.width()
        print(f"Shrunk Width: {shrunk_width}")

        self.assertLess(shrunk_width, 500, "Window refused to shrink back!")
