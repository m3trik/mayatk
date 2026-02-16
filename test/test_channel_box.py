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

from base_test import MayaTkTestCase, skipUnlessExtended
from mayatk.env_utils.channel_box import ChannelBox


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
        self.assertEqual(result, ["translateX", "rotateY"])

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
        # polyCube has history attrs like width/height
        if attrs:
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
    def test_select_visual_roundtrip(self):
        """select_visual should highlight attrs that then appear in get_selected_attrs."""
        cube = cmds.polyCube(name="sv_cube")[0]
        cmds.select(cube)
        ChannelBox.select_visual(["translateX", "translateY"])
        import maya.api.OpenMaya as om

        om.MGlobal.executeCommandOnIdle('python("pass")')  # flush event loop
        from qtpy.QtWidgets import QApplication

        QApplication.processEvents()
        sel = ChannelBox.get_selected_attrs()
        # The selection should contain at least translateX
        self.assertIn("translateX", sel)


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
    def test_connect_and_disconnect(self):
        """Should connect and disconnect without error."""
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
