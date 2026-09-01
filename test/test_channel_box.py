# !/usr/bin/python
# coding=utf-8
"""
Test Suite for ChannelBox

Tests for the stateless ChannelBox helper that queries, selects,
and hooks into Maya's Channel Box.

Most methods need a live GUI — those are gated behind
``@skipUnlessExtended``.  Pure-logic helpers are tested directly.
"""

import unittest
import maya.cmds as cmds

from base_test import MayaTkTestCase, skipUnlessExtended, skipIfBatch
from mayatk.ui_utils.channel_box import ChannelBox


def _await_channel_box(node=None):
    """Show the channel box, select *node*, and return its view if populated.

    Maya refills the channel box only when it returns to its idle loop, and a
    launched Maya starts with the Channel Box UI component hidden.  Measured
    on Maya 2025: after ``file -new`` + ``polyCube`` + ``select`` the model is
    still empty no matter how much you ``refresh`` / ``processEvents`` /
    ``channelBox -e -update`` within the SAME call — it fills only on the next
    call, once Maya has actually idled.  The in-session GUI harness runs a
    whole module inside one call, so a test that resets the scene cannot get
    the channel box back; hence a truthful None rather than a pump loop that
    cannot succeed.

    This matters because an empty model makes ``ChannelBox._main_view()``
    return None, and ``select_visual`` then falls back to ``cmds.channelBox
    -select``, measured to leave ``-q -sma`` empty — the highlight never lands.

    Returns:
        QTableView|None: the view, or None if the channel box is not populated.
    """
    import maya.mel as mel
    from qtpy.QtWidgets import QApplication

    try:  # absent in batch; harmless if the component is already up
        mel.eval("setChannelsLayersVisible(true);")
        mel.eval("setChannelsVisible(true);")
    except Exception:
        pass

    if node is not None:
        cmds.select(node)

    cmds.refresh()
    QApplication.processEvents()
    return ChannelBox._main_view()


# =========================================================================
# Pure helpers (no GUI required)
# =========================================================================


class TestResolveDisplayNames(MayaTkTestCase):
    """Tests for ChannelBox._resolve_display_names."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="rdn_cube")[0]
        cmds.select(self.cube)

    def test_long_names_returned_unchanged(self):
        result = ChannelBox._resolve_display_names(["translateX", "rotateY"])
        # Returns a superset (long + short + nice variants).
        self.assertIn("translateX", result)
        self.assertIn("rotateY", result)

    def test_nice_names_resolved(self):
        """Translate X (nice) -> translateX (long)."""
        result = ChannelBox._resolve_display_names(["Translate X"])
        self.assertIn("translateX", result)


class TestGetAllAttrs(MayaTkTestCase):
    """Tests for ChannelBox.get_all_attrs."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="gaa_cube")[0]
        cmds.select(self.cube)

    def test_main_section_returns_standard(self):
        attrs = ChannelBox.get_all_attrs(self.cube, section="main")
        self.assertIn("translateX", attrs)
        self.assertIn("visibility", attrs)

    def test_shape_section(self):
        attrs = ChannelBox.get_all_attrs(self.cube, section="shape")
        # Shape section varies, but should at least return a list
        self.assertIsInstance(attrs, list)

    def test_history_section(self):
        attrs = ChannelBox.get_all_attrs(self.cube, section="history")
        # polyCube always has a polyCube history node with width/height/depth.
        # If the list is empty, the section query is broken — fail loudly.
        self.assertTrue(attrs, "history section returned empty for polyCube")
        self.assertIn("width", attrs)

    def test_no_selection_returns_empty(self):
        cmds.select(clear=True)
        attrs = ChannelBox.get_all_attrs(node=None, section="main")
        self.assertEqual(attrs, [])


class TestGetAttrProperties(MayaTkTestCase):
    """Tests for ChannelBox.get_attr_properties."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="gap_cube")[0]
        cmds.select(self.cube)

    def test_returns_list_of_dicts(self):
        props = ChannelBox.get_attr_properties(self.cube)
        self.assertIsInstance(props, list)
        if props:
            self.assertIn("name", props[0])
            self.assertIn("type", props[0])
            self.assertIn("locked", props[0])

    def test_specific_attrs(self):
        props = ChannelBox.get_attr_properties(
            self.cube, attrs=["translateX", "visibility"]
        )
        names = [p["name"] for p in props]
        self.assertIn("translateX", names)


# =========================================================================
# GUI-dependent tests (require interactive Maya session)
# =========================================================================


class TestControlName(MayaTkTestCase):
    """Tests for ChannelBox._control_name."""

    @skipUnlessExtended
    def test_returns_string(self):
        name = ChannelBox._control_name()
        self.assertIsInstance(name, str)
        self.assertTrue(len(name) > 0)


class TestGetSelectedAttrs(MayaTkTestCase):
    """Tests for ChannelBox.get_selected_attrs."""

    @skipUnlessExtended
    def test_no_selection_returns_empty(self):
        cmds.select(clear=True)
        attrs = ChannelBox.get_selected_attrs()
        self.assertEqual(attrs, [])

    @skipUnlessExtended
    def test_returns_list(self):
        cube = cmds.polyCube(name="gsa_cube")[0]
        cmds.select(cube)
        attrs = ChannelBox.get_selected_attrs()
        self.assertIsInstance(attrs, list)


class TestGetSelectedObjects(MayaTkTestCase):
    """Tests for ChannelBox.get_selected_objects."""

    @skipUnlessExtended
    def test_no_selection_empty(self):
        cmds.select(clear=True)
        objs = ChannelBox.get_selected_objects()
        self.assertEqual(objs, [])


class TestGetSelectedPlugs(MayaTkTestCase):
    """Tests for ChannelBox.get_selected_plugs."""

    @skipUnlessExtended
    def test_returns_list(self):
        plugs = ChannelBox.get_selected_plugs()
        self.assertIsInstance(plugs, list)


class TestSelect(MayaTkTestCase):
    """Tests for ChannelBox.select and select_visual."""

    @skipUnlessExtended
    def test_select_empty_list(self):
        """Selecting an empty list should not raise."""
        cube = cmds.polyCube(name="sel_cube")[0]
        cmds.select(cube)
        ChannelBox.select([])

    @skipUnlessExtended
    @skipIfBatch("select_visual needs a live channel box QTableView")
    def test_select_visual_roundtrip(self):
        """select_visual should highlight attrs that then appear in get_selected_attrs."""
        cube = cmds.polyCube(name="sv_cube")[0]
        if _await_channel_box(cube) is None:
            self.skipTest(
                "channel box not populated -- it refills only on Maya's idle turn, "
                "which the in-session harness cannot yield; the standing probe "
                "test/temp_tests/_probe_setkey_channelbox.py covers this roundtrip"
            )

        ChannelBox.select_visual(["translateX", "translateY"])
        from qtpy.QtWidgets import QApplication

        QApplication.processEvents()
        sel = ChannelBox.get_selected_attrs()
        # get_selected_attrs returns SHORT names -- `channelBox -q -sma` reports
        # "tx", not "translateX" (measured on Maya 2025).  This asserted the long
        # name and so could only ever fail; it never ran because the extended
        # gate kept it skipped.
        self.assertIn("tx", sel)


class TestClearSelection(MayaTkTestCase):
    """Tests for ChannelBox.clear_selection."""

    @skipUnlessExtended
    def test_clear(self):
        cube = cmds.polyCube(name="clr_cube")[0]
        cmds.select(cube)
        ChannelBox.clear_selection()
        from qtpy.QtWidgets import QApplication

        QApplication.processEvents()
        sel = ChannelBox.get_selected_attrs()
        self.assertEqual(sel, [])


class TestConnectDisconnectSignal(MayaTkTestCase):
    """Tests for connect/disconnect_selection_changed."""

    @skipUnlessExtended
    @skipIfBatch("needs the channel box's live QItemSelectionModel")
    def test_connect_and_disconnect(self):
        """Should connect and disconnect without error."""
        cube = cmds.polyCube(name="sig_cube")[0]
        if _await_channel_box(cube) is None:
            self.skipTest("channel box not populated (see _await_channel_box)")

        calls = []
        cb = lambda sel, desel: calls.append(1)
        result = ChannelBox.connect_selection_changed(cb)
        self.assertTrue(result)
        ChannelBox.disconnect_selection_changed(cb)


class TestWatchUnwatch(MayaTkTestCase):
    """Tests for watch_selection / unwatch_selection."""

    @skipUnlessExtended
    def test_watch_returns_job_id(self):
        cb = lambda attrs: None
        job_id = ChannelBox.watch_selection(cb)
        self.assertIsNotNone(job_id)
        ChannelBox.unwatch_selection(cb)


if __name__ == "__main__":
    unittest.main()
