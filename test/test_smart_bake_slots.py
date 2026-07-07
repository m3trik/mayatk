# !/usr/bin/python
# coding=utf-8
"""Tests for SmartBakeSlots — the smart_bake.ui panel controller.

Stubs self.ui/self.sb (a bare SmartBakeSlots.__new__, no Qt/UI load — see
_make_slots) against REAL maya.cmds/SmartBake, mirroring test_hdr_manager.py's
stub pattern. Panel structural/.ui-load coverage (widget presence, combo
items, toggle wiring) lives in mock_tests/test_smart_bake_panel.py instead —
that needs real Qt but mocked cmds, and the two needs don't fit one process
(real maya.standalone + real Qt widgets together crashes natively; see that
file's docstring).
"""
import logging
import unittest


class _StubCheckbox:
    """Also stands in for a PushButton (isEnabled/setEnabled/setToolTip)."""

    def __init__(self, checked=False):
        self._checked = checked
        self._enabled = True
        self.tooltip = ""

    def isChecked(self):
        return self._checked

    def setChecked(self, value):
        self._checked = value

    def isEnabled(self):
        return self._enabled

    def setEnabled(self, value):
        self._enabled = bool(value)

    def setDisabled(self, value):
        self._enabled = not value

    def setToolTip(self, text):
        self.tooltip = text


class _StubCombo:
    def __init__(self, index=0):
        self._index = index

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        self._index = index


class _StubSpin:
    def __init__(self, value=1):
        self._value = value

    def value(self):
        return self._value


class _StubProgress:
    """Context manager stand-in for Footer.progress()."""

    def __enter__(self):
        return lambda value=None, text=None: True

    def __exit__(self, *exc):
        return False


class _StubFooter:
    def __init__(self):
        self.last_text = None
        self.last_level = None

    def setText(self, text, level=None):
        self.last_text = text or ""
        self.last_level = level

    setStatusText = setText

    def progress(self, total=None, text=""):
        return _StubProgress()


class _StubUi:
    def __init__(self):
        self.cmb_scope = _StubCombo(0)
        self.spn_sample_by = _StubSpin(1)
        self.chk_preserve_outside = _StubCheckbox(True)
        self.chk_optimize = _StubCheckbox(False)
        self.chk_bake_blendshapes = _StubCheckbox(True)
        self.chk_inherited_vis = _StubCheckbox(False)
        self.chk_override_layer = _StubCheckbox(True)
        self.chk_mute_drivers = _StubCheckbox(False)
        self.chk_delete_inputs = _StubCheckbox(False)
        self.cmb_backup = _StubCombo(0)
        self.b000 = _StubCheckbox()
        self.b001 = _StubCheckbox()
        self.footer = _StubFooter()


class _StubSb:
    def __init__(self):
        self.message_box_calls = []

    def message_box(self, string, *buttons, **kwargs):
        self.message_box_calls.append((string, buttons))
        return "Ok"


def _make_slots(**checkbox_overrides):
    """A bare SmartBakeSlots with stub self.ui/self.sb — real methods, real
    maya.cmds/SmartBake underneath. SmartBakeSlots.logger is a class-level
    property (no __init__ needed), matching test_hdr_manager.py's pattern.
    """
    from mayatk.anim_utils.smart_bake.smart_bake_slots import SmartBakeSlots

    s = SmartBakeSlots.__new__(SmartBakeSlots)
    s.ui = _StubUi()
    s.sb = _StubSb()
    for name, value in checkbox_overrides.items():
        getattr(s.ui, name)._checked = value
    SmartBakeSlots.logger.setLevel(logging.CRITICAL)
    return s


def _maya_available():
    try:
        from maya import standalone

        try:
            standalone.initialize(name="python")
        except (RuntimeError, TypeError):
            pass
        return True
    except ImportError:
        return False


class TestScopeAndBackupMapping(unittest.TestCase):
    """Pure glue logic against a real (near-empty) scene."""

    @classmethod
    def setUpClass(cls):
        cls.maya_available = _maya_available()

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        from maya import cmds

        cmds.file(new=True, force=True)

    def tearDown(self):
        if self.maya_available:
            from maya import cmds

            cmds.file(new=True, force=True)

    def test_scope_selected_returns_selection(self):
        from maya import cmds

        cube = cmds.polyCube(name="scope_cube")[0]
        cmds.select(cube)
        s = _make_slots()
        s.ui.cmb_scope.setCurrentIndex(1)  # Selected
        self.assertEqual(s._scope_objects(), cmds.ls(selection=True, long=True))

    def test_scope_selected_with_nothing_selected_returns_empty(self):
        """Empty list (not None) — None means Auto and would whole-scene bake."""
        from maya import cmds

        cmds.select(clear=True)
        s = _make_slots()
        s.ui.cmb_scope.setCurrentIndex(1)  # Selected
        self.assertEqual(s._scope_objects(), [])

    def test_scope_auto_returns_none_regardless_of_selection(self):
        from maya import cmds

        cube = cmds.polyCube(name="scope_cube2")[0]
        cmds.select(cube)
        s = _make_slots()
        # Auto (Whole Scene) is index 0, the default.
        self.assertIsNone(s._scope_objects())

    def test_backup_value_mapping(self):
        s = _make_slots()
        for index, expected in enumerate((None, True, False)):  # Auto/Always/Never
            s.ui.cmb_backup.setCurrentIndex(index)
            self.assertEqual(s._backup_value(), expected)


class TestBakeAndUnbakeThroughSlots(unittest.TestCase):
    """b000/b001 drive the real SmartBake engine against a real scene."""

    @classmethod
    def setUpClass(cls):
        cls.maya_available = _maya_available()

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        from maya import cmds

        cmds.file(new=True, force=True)
        cmds.playbackOptions(minTime=1, maxTime=10)

    def tearDown(self):
        if self.maya_available:
            from maya import cmds

            cmds.file(new=True, force=True)

    def _constrained_scene(self, prefix="slots"):
        from maya import cmds

        cube = cmds.polyCube(name=f"{prefix}_cube")[0]
        loc = cmds.spaceLocator(name=f"{prefix}_loc")[0]
        cmds.setKeyframe(loc, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(loc, attribute="translateX", time=10, value=5)
        constraint = cmds.parentConstraint(loc, cube)[0]
        cmds.select(cube)
        return cube, loc, constraint

    def test_bake_reports_success_and_enables_unbake(self):
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube, loc, constraint = self._constrained_scene()
        s = _make_slots()
        s.b000(None)

        self.assertEqual(s.ui.footer.last_level, "success")
        self.assertIn("Baked", s.ui.footer.last_text)
        self.assertTrue(s.ui.b001.isEnabled())
        self.assertTrue(SmartBake.list_sessions())

        cmds.currentTime(10)
        self.assertAlmostEqual(cmds.getAttr(f"{cube}.tx"), 5.0, places=3)

    def test_bake_nothing_to_bake_warns_and_leaves_unbake_disabled(self):
        from maya import cmds

        cube = cmds.polyCube(name="plain_cube")[0]
        cmds.select(cube)
        s = _make_slots()
        s.b000(None)

        self.assertEqual(s.ui.footer.last_level, "warning")
        self.assertIn("Nothing to bake", s.ui.footer.last_text)
        self.assertFalse(s.ui.b001.isEnabled())

    def test_bake_selected_scope_empty_selection_bails_without_baking(self):
        """Selected scope + empty selection must warn, NOT whole-scene bake."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # A bakeable constraint setup exists in the scene but is NOT selected.
        self._constrained_scene(prefix="unselected")
        cmds.select(clear=True)
        s = _make_slots()
        s.ui.cmb_scope.setCurrentIndex(1)  # Selected
        s.b000(None)

        self.assertEqual(s.ui.footer.last_level, "warning")
        self.assertIn("Nothing selected", s.ui.footer.last_text)
        # No bake ran: no session recorded, no override layer created.
        self.assertEqual(SmartBake.list_sessions(), [])
        self.assertFalse(
            [l for l in (cmds.ls(type="animLayer") or []) if "SmartBake" in l]
        )

    def test_unbake_restores_and_disables_itself(self):
        from maya import cmds

        cube, loc, constraint = self._constrained_scene()
        s = _make_slots()
        s.b000(None)
        self.assertTrue(s.ui.b001.isEnabled())

        s.b001(None)
        self.assertEqual(s.ui.footer.last_level, "success")
        self.assertIn("Restored", s.ui.footer.last_text)
        self.assertFalse(s.ui.b001.isEnabled())

        cmds.currentTime(10)
        self.assertAlmostEqual(cmds.getAttr(f"{cube}.tx"), 5.0, places=3)

    def test_unbake_with_nothing_pending_warns(self):
        s = _make_slots()
        s.b001(None)
        self.assertEqual(s.ui.footer.last_level, "warning")

    def test_delete_inputs_bake_is_destructive_and_unbake_reports_it(self):
        from maya import cmds

        cube, loc, constraint = self._constrained_scene(prefix="destructive")
        s = _make_slots(chk_override_layer=False, chk_delete_inputs=True)
        s.ui.cmb_backup.setCurrentIndex(2)  # Never — keep the test disk-free
        s.b000(None)

        self.assertEqual(s.ui.footer.last_level, "success")
        self.assertFalse(cmds.objExists(constraint))  # actually deleted, not just muted

        # The non-restorable session stays clickable — the click surfaces
        # the destructive-session warning and pops the dead entry.
        self.assertTrue(s.ui.b001.isEnabled())
        s.b001(None)
        self.assertEqual(s.ui.footer.last_level, "warning")
        self.assertIn("destructive", s.ui.footer.last_text)
        self.assertFalse(s.ui.b001.isEnabled())


if __name__ == "__main__":
    unittest.main()
