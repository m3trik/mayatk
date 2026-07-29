# !/usr/bin/python
# coding=utf-8
"""Slot-layer tests for the Shell Xform panel (``mayatk/uv_utils/shell_xform.py``).

The engine helpers are covered in ``test_uv_utils.py``; this file covers the
*slot* glue the engine tests can't see — that each arrow is wired to the right
axis and sign, and that the move-scope combobox / snap toggle resolve to the
offset they claim. A swapped ``b024``/``b025`` or an inverted snap direction
passes every engine test and still ships a broken panel.

The slots are built via ``__new__`` with a stubbed switchboard and UI, so no Qt
is involved (mayapy + offscreen Qt segfaults building a QMainWindow). The Blender
twin's equivalent lives in ``blendertk/test/shell_xform_slot_check.py``.
"""
import unittest
from types import SimpleNamespace as NS

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.uv_utils.shell_xform import ShellXformSlots


class ShellXformMovePadTest(MayaTkTestCase):
    """The four move-to-UV-space arrows, across every scope and snap state."""

    def setUp(self):
        super().setUp()
        self.slot = ShellXformSlots.__new__(ShellXformSlots)
        self.slot.sb = NS(message_box=lambda *a, **k: None)
        self.set_scope("Tile")

    def set_scope(self, text, snap=False):
        """Stand in for the panel's `cmb_move_scope` + its option-box snap toggle.

        The item data is looked up in the real `_MOVE_SCOPES` table (the same one
        `cmb_move_scope_init` populates the combo from), so a scope renamed there
        fails these tests loudly instead of silently testing a stale label.
        """
        data = ShellXformSlots._MOVE_SCOPES[text]
        self.slot.ui = NS(
            cmb_move_scope=NS(currentText=lambda: text, currentData=lambda: data)
        )
        self.slot._snap_toggle = NS(is_on=snap)

    def test_scope_table_shape(self):
        """The combo is built from this table, so it is the panel's item list.

        Exactly one scope must carry the `None` sentinel (derive from selection);
        a second would make `_move_step` read `bounds` for a fixed-step scope.
        """
        scopes = ShellXformSlots._MOVE_SCOPES
        self.assertEqual(
            list(scopes), ["Tile", "Half Tile", "Quarter Tile", "Selection Bounds"]
        )
        self.assertEqual([k for k, v in scopes.items() if v is None], ["Selection Bounds"])
        self.assertTrue(all(v > 0 for v in scopes.values() if v is not None))

    def make_plane(self, u_offset=0.0, v_offset=0.0):
        """A selected 0-1 UV plane, optionally nudged off the origin."""
        plane = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        if u_offset or v_offset:
            UvUtils.move_to_uv_space(plane, u_offset, v_offset)
        cmds.select(plane)
        return plane

    # ------------------------------------------------------------------ scope
    def test_scope_sets_the_step(self):
        for scope, expected in (
            ("Tile", 1.0),
            ("Half Tile", 0.5),
            ("Quarter Tile", 0.25),
        ):
            with self.subTest(scope=scope):
                cmds.file(new=True, force=True)
                plane = self.make_plane()
                before = UvUtils.get_uv_bounds(plane)
                self.set_scope(scope)

                self.slot.b025()  # up

                after = UvUtils.get_uv_bounds(plane)
                self.assertAlmostEqual(after[1], before[1] + expected, places=5)
                self.assertAlmostEqual(after[0], before[0], places=5)  # U untouched

    def test_selection_bounds_scope_steps_by_the_selections_own_size(self):
        plane = self.make_plane()
        before = UvUtils.get_uv_bounds(plane)
        width = before[2] - before[0]
        self.set_scope("Selection Bounds")

        self.slot.b026()  # right

        after = UvUtils.get_uv_bounds(plane)
        self.assertAlmostEqual(after[0], before[0] + width, places=5)

    # ------------------------------------------------------------------ axis / sign
    def test_opposite_arrows_cancel(self):
        """Guards an axis or sign swap between the four arrow slots."""
        plane = self.make_plane()
        before = UvUtils.get_uv_bounds(plane)
        self.set_scope("Half Tile")

        self.slot.b023()  # left
        self.slot.b026()  # right
        self.slot.b024()  # down
        self.slot.b025()  # up

        after = UvUtils.get_uv_bounds(plane)
        for axis, (a, b) in enumerate(zip(after, before)):
            self.assertAlmostEqual(a, b, places=5, msg=f"bounds[{axis}]")

    def test_arrows_move_the_axis_they_name(self):
        plane = self.make_plane()
        before = UvUtils.get_uv_bounds(plane)

        self.slot.b026()  # right -> +U only
        after = UvUtils.get_uv_bounds(plane)
        self.assertAlmostEqual(after[0], before[0] + 1.0, places=5)
        self.assertAlmostEqual(after[1], before[1], places=5)

        self.slot.b024()  # down -> -V only
        final = UvUtils.get_uv_bounds(plane)
        self.assertAlmostEqual(final[0], after[0], places=5)
        self.assertAlmostEqual(final[1], after[1] - 1.0, places=5)

    # ------------------------------------------------------------------ snap
    def test_snap_lands_an_off_grid_shell_on_the_padded_grid(self):
        """The reported case: a shell sitting in one half of a UDIM steps to the
        next half line, rather than carrying its sub-tile drift along.

        It lands one border margin *inside* the line, not on it — a shell flush
        against a tile seam bleeds across it at render time.
        """
        margin = self.slot._border_margin()
        plane = self.make_plane(v_offset=0.6)  # V bounds 0.6 .. 1.6
        self.set_scope("Half Tile", snap=True)

        self.slot.b025()  # up
        self.assertAlmostEqual(
            UvUtils.get_uv_bounds(plane)[1], 1.0 + margin, places=5
        )

        self.slot.b024()  # back down
        self.assertAlmostEqual(
            UvUtils.get_uv_bounds(plane)[1], 0.5 + margin, places=5
        )

    def test_snap_is_reversible_from_a_padded_position(self):
        """Every press moves a full step once the shell is on the padded grid.

        Padding the *result* rather than the anchor would strand the reverse
        press on the margin it had just added, and the arrow would read as dead.
        """
        margin = self.slot._border_margin()
        plane = self.make_plane(v_offset=0.6)
        self.set_scope("Tile", snap=True)

        self.slot.b025()  # onto the padded grid
        landed = UvUtils.get_uv_bounds(plane)[1]
        self.assertAlmostEqual(landed % 1.0, margin, places=5)

        self.slot.b025()
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], landed + 1.0, places=5)
        self.slot.b024()
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], landed, places=5)

    def test_snap_off_preserves_sub_tile_drift(self):
        plane = self.make_plane(v_offset=0.6)
        self.set_scope("Half Tile", snap=False)

        self.slot.b025()

        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], 1.1, places=5)

    # ------------------------------------------------------------------ gather
    def test_gather_slot_pulls_a_stray_into_the_majority_tile(self):
        """The Gather button is wired to the engine and acts on the selection.

        Two residents define the target tile, so only the stray travels — with a
        lone shell there is by definition no stray, and the button no-ops.
        """
        residents = [self.make_plane(), self.make_plane()]
        stray = self.make_plane(u_offset=3.0, v_offset=2.0)
        cmds.select(residents + [stray])
        before = [UvUtils.get_uv_bounds(r) for r in residents]

        self.slot.gather_to_udim()

        u_min, v_min, u_max, v_max = UvUtils.get_uv_bounds(stray)
        self.assertAlmostEqual(u_min, 0.0, places=5)
        self.assertAlmostEqual(v_min, 0.0, places=5)
        for bounds, resident in zip(before, residents):
            for b, a in zip(bounds, UvUtils.get_uv_bounds(resident)):
                self.assertAlmostEqual(b, a, places=6)

    def test_gather_slot_with_nothing_selected_warns(self):
        """No selection is a message box, not a traceback."""
        messages = []
        self.slot.sb = NS(message_box=lambda *a, **k: messages.append(a))
        cmds.select(clear=True)

        self.slot.gather_to_udim()

        self.assertTrue(messages)

    def test_snap_toggle_defaults_off_when_unset(self):
        """A slots instance that never ran `cmb_move_scope_init` must not crash."""
        del self.slot._snap_toggle
        self.assertFalse(self.slot._snap_enabled())

    # ------------------------------------------------------------------ selection
    def test_uv_component_selection_moves(self):
        plane = self.make_plane()
        before = UvUtils.get_uv_bounds(plane)
        cmds.select(cmds.ls(f"{plane}.map[*]", flatten=True))

        self.slot.b026()

        after = UvUtils.get_uv_bounds(plane)
        self.assertAlmostEqual(after[0], before[0] + 1.0, places=5)

    def test_empty_selection_warns_instead_of_raising(self):
        messages = []
        self.slot.sb = NS(message_box=lambda msg, *a, **k: messages.append(msg))
        cmds.select(clear=True)

        self.slot.b025()

        self.assertTrue(messages, "expected a message box for an empty selection")


if __name__ == "__main__":
    unittest.main(exit=False)
