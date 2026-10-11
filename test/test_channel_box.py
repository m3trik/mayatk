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

    This matters because ``select_visual`` can only highlight a filled box:
    on an unfilled one it retries on idle, which never comes inside the
    harness call (``TestSelectVisualWaitsForRefill`` drives that path with
    a fake view instead).

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


class _FakeChannelBoxView:
    """Stand-in for the ``mainChannelBox`` QTableView: a QtCore model only.

    Mirrors the state measured on Maya 2025 right after the box's node changes:
    the old row count survives but every cell reads ``""`` until Maya's idle
    refill fills them in.
    """

    def __init__(self, texts, visible=True):
        from qtpy import QtCore

        class _Model(QtCore.QStringListModel):
            # PySide6's binding makes ``parent`` mandatory here; the real
            # channel-box model takes the QAbstractItemModel default.
            def columnCount(self, parent=QtCore.QModelIndex()):
                return 1

        self._model = _Model(list(texts))
        self._sel = QtCore.QItemSelectionModel(self._model)
        self.visible = visible

    def refill(self, texts):
        self._model.setStringList(list(texts))

    def isVisible(self):
        return self.visible

    def model(self):
        return self._model

    def selectionModel(self):
        return self._sel

    def selected_texts(self):
        return sorted(self._model.data(i) for i in self._sel.selectedRows(0))


class TestSelectVisualWaitsForRefill(MayaTkTestCase):
    """select_visual on a channel box that has not refilled yet.

    Repro (Channels panel, fresh Maya 2025): the first row click after the
    box's node changed logged "Qt path unavailable" and set no highlight; the
    same click a moment later worked.  The box still had its 16 rows, all
    blank, so nothing matched.  It should wait for the refill, not warn.
    """

    POPULATED = ["cubeA", "Translate X", "Translate Y", "Translate Z"]

    def setUp(self):
        super().setUp()
        cmds.select(cmds.polyCube(name="cubeA")[0], replace=True)
        self.deferred = []
        self.warnings = []
        import logging
        from unittest import mock
        import mayatk.ui_utils.channel_box as cb_mod

        test = self

        class _Warn(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.WARNING:
                    test.warnings.append(record.getMessage())

        handler = _Warn()
        logger = logging.getLogger(cb_mod.__name__)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        for target, attr, kwargs in (
            # Idle is ours to run: queue the deferred callables.
            (cb_mod.cmds, "evalDeferred", {"side_effect": self._queue}),
            # The command path (``select``) is tested on its own below; here it
            # reports "did not land" unless a test says otherwise.
            (ChannelBox, "select", {"return_value": False}),
        ):
            patcher = mock.patch.object(target, attr, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _queue(self, fn, *args, **kwargs):
        self.deferred.append(fn)

    def _use_view(self, view):
        from unittest import mock

        patcher = mock.patch.object(
            ChannelBox, "_main_view", side_effect=lambda *a, **k: view
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_idle(self, limit=50):
        """Drain the deferred queue the way Maya's idle loop would."""
        ran = 0
        while self.deferred and ran < limit:
            self.deferred.pop(0)()
            ran += 1
        return ran

    def test_blank_model_waits_for_refill_instead_of_warning(self):
        view = _FakeChannelBoxView([""] * 4)
        self._use_view(view)

        ChannelBox.select_visual(["translateX"])
        self.assertEqual(self.warnings, [])
        self.assertTrue(self.deferred, "no retry scheduled for the unfilled box")

        view.refill(self.POPULATED)
        self._run_idle()
        self.assertEqual(view.selected_texts(), ["Translate X"])
        self.assertEqual(self.warnings, [])

    def test_warns_once_when_the_box_never_fills(self):
        self._use_view(_FakeChannelBoxView([""] * 4))

        ChannelBox.select_visual(["translateX"])
        ran = self._run_idle()
        self.assertLess(ran, 50, "retries must be bounded")
        self.assertEqual(len(self.warnings), 1, self.warnings)

    def test_newer_request_supersedes_a_pending_retry(self):
        view = _FakeChannelBoxView([""] * 4)
        self._use_view(view)

        ChannelBox.select_visual(["translateX"])
        view.refill(self.POPULATED)
        ChannelBox.select_visual(["translateY"])
        self._run_idle()
        self.assertEqual(view.selected_texts(), ["Translate Y"])

    def test_a_hidden_box_selects_through_the_command_without_retrying(self):
        """Repro (Channels, Maya 2025): with the box tabbed behind the Attribute
        Editor, table rows never reached ``-sma``, so Set Selected Keys keyed
        nothing.  Hidden it stays empty until shown -- but the command lands."""
        self._use_view(_FakeChannelBoxView([""] * 4, visible=False))
        ChannelBox.select.return_value = True

        ChannelBox.select_visual(["translateX"])
        self.assertEqual(self.deferred, [])
        ChannelBox.select.assert_called_once_with(["translateX"])
        self.assertEqual(self.warnings, [])

    def test_a_hidden_box_the_command_misses_stays_quiet(self):
        """Tabbed away after being shown, Maya empties the box and it takes
        nothing; the panel stands in for it (``TestStandIn``), so a click in
        the panel is no reason to warn."""
        self._use_view(_FakeChannelBoxView([""] * 4, visible=False))

        ChannelBox.select_visual(["translateX"])
        ChannelBox.select.assert_called_once_with(["translateX"])
        self.assertEqual(self.warnings, [])

    def test_attr_not_shown_warns_without_retrying(self):
        self._use_view(_FakeChannelBoxView(self.POPULATED))

        ChannelBox.select_visual(["visibility"])
        self.assertEqual(self.deferred, [])
        self.assertEqual(len(self.warnings), 1, self.warnings)


class TestConnectToAnEmptyBox(MayaTkTestCase):
    """The Channels panel connects when it opens, often to an empty box.

    Measured on Maya 2025: the box keeps the same model and selection model
    from empty to filled, and a connection made while it was empty receives
    the later selections.  Refusing it left the panel's box-to-table sync
    dead until the next scene selection change.
    """

    def test_connects_while_the_box_has_no_rows(self):
        from unittest import mock

        view = _FakeChannelBoxView([])

        def main_view(require_rows=True):
            if require_rows and view.model().rowCount() == 0:
                return None
            return view

        hits = []

        def slot(selected, deselected):
            hits.append(1)

        with mock.patch.object(ChannelBox, "_main_view", side_effect=main_view):
            self.assertTrue(ChannelBox.connect_selection_changed(slot))
            view.refill(["cubeA", "Translate X"])
            view.selectionModel().select(
                view.model().index(1, 0),
                view.selectionModel().SelectionFlag.Select,
            )
            ChannelBox.disconnect_selection_changed(slot)
        self.assertEqual(hits, [1])


class TestSelectByPlug(MayaTkTestCase):
    """``channelBox -e -select`` takes ``node.attr`` plugs on the box's lead.

    Measured on Maya 2025 (fresh GUI): bare names ("translateX", "tx") select
    nothing -- what left ``select`` "dead" -- while plugs select whether the box
    is shown or hidden, and highlight the row when shown.  With two objects
    selected only the lead's plug (``-mol`` lists it first) selects.  Neither
    ``select=""`` nor ``select=[]`` clears; the Qt selection model does.
    """

    def setUp(self):
        super().setUp()
        from unittest import mock
        import mayatk.ui_utils.channel_box as cb_mod

        self.lead = cmds.polyCube(name="cubeB")[0]
        self.other = cmds.polyCube(name="cubeA")[0]
        self.edits = []
        self.sma = []

        def channel_box(*args, **kwargs):
            if kwargs.get("e") or kwargs.get("edit"):
                self.edits.append(kwargs)
                return None
            if kwargs.get("mol"):
                return [self.lead, self.other]
            if kwargs.get("sma"):
                return list(self.sma)
            return None

        for target, attr, kw in (
            (cb_mod.cmds, "channelBox", {"side_effect": channel_box}),
            (ChannelBox, "_control_name", {"return_value": "mainChannelBox"}),
        ):
            patcher = mock.patch.object(target, attr, **kw)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_bare_names_become_plugs_on_the_lead_object(self):
        self.sma = ["tx", "ry"]
        landed = ChannelBox.select(["translateX", f"{self.other}.rotateY"])
        self.assertEqual(
            [e.get("select") for e in self.edits],
            [[f"{self.lead}.translateX", f"{self.other}.rotateY"]],
        )
        self.assertTrue(landed)

    def test_reports_a_selection_that_did_not_land(self):
        self.sma = ["tx"]
        self.assertFalse(ChannelBox.select(["translateX", "rotateY"]))

    def test_an_empty_list_clears_through_the_qt_model(self):
        from unittest import mock

        view = _FakeChannelBoxView(["cubeB", "Translate X"], visible=False)
        view.selectionModel().select(
            view.model().index(1, 0), view.selectionModel().SelectionFlag.Select
        )
        with mock.patch.object(ChannelBox, "_main_view", return_value=view):
            self.assertTrue(ChannelBox.select([]))
        self.assertEqual(view.selected_texts(), [])
        self.assertEqual(self.edits, [], "the command cannot clear")


class TestStandIn(MayaTkTestCase):
    """A panel listing channels in the box's place stands in for its selection.

    Repro (Channels panel, Maya 2025, box tabbed behind the Outliner): rows
    selected in the panel, Set Selected Keys keyed nothing.  Measured: Maya
    empties a box tabbed away after being shown -- rows, ``-mol`` and the
    selection itself -- and takes no ``-select`` until shown again, so the
    selection cannot be put there; the panel's is read instead.
    """

    def setUp(self):
        super().setUp()
        from unittest import mock
        from mayatk.node_utils.attributes._attributes import Attributes

        self.Attributes = Attributes
        self.box = []
        patcher = mock.patch.object(
            ChannelBox, "get_selected_attrs", side_effect=lambda *a, **k: self.box
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        saved = list(ChannelBox._stand_ins)
        ChannelBox._stand_ins[:] = []
        self.addCleanup(lambda: ChannelBox._stand_ins.__setitem__(slice(None), saved))

    def test_the_panel_selection_counts_while_the_box_has_none(self):
        ChannelBox.add_stand_in(lambda: ["ty", "tz"])
        self.assertEqual(self.Attributes.get_selected_channels(), ["ty", "tz"])

    def test_the_box_selection_wins_when_it_has_one(self):
        ChannelBox.add_stand_in(lambda: ["ty"])
        self.box = ["rx"]
        self.assertEqual(self.Attributes.get_selected_channels(), ["rx"])

    def test_the_first_stand_in_with_a_selection_answers(self):
        def broken():
            raise RuntimeError("panel torn down")

        for source in (broken, lambda: [], lambda: ["sx"], lambda: ["sy"]):
            ChannelBox.add_stand_in(source)
        self.assertEqual(ChannelBox.stand_in_selection(), ["sx"])

    def test_a_removed_stand_in_is_not_asked(self):
        def source():
            return ["ty"]

        ChannelBox.add_stand_in(source)
        ChannelBox.add_stand_in(source)  # registered once
        ChannelBox.remove_stand_in(source)
        ChannelBox.remove_stand_in(source)  # unknown: ignored
        self.assertEqual(ChannelBox.stand_in_selection(), [])


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

        def cb(sel, desel):
            calls.append(1)

        result = ChannelBox.connect_selection_changed(cb)
        self.assertTrue(result)
        ChannelBox.disconnect_selection_changed(cb)


class TestWatchUnwatch(MayaTkTestCase):
    """Tests for watch_selection / unwatch_selection."""

    @skipUnlessExtended
    def test_watch_returns_job_id(self):
        def cb(attrs):
            pass

        job_id = ChannelBox.watch_selection(cb)
        self.assertIsNotNone(job_id)
        ChannelBox.unwatch_selection(cb)


if __name__ == "__main__":
    unittest.main()
