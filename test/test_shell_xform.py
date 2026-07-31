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

    def set_scope(self, text, snap=ShellXformSlots._SNAP_OFF):
        """Stand in for the panel's `cmb_move_scope` + its option-box snap button.

        The item data is looked up in the real `_MOVE_SCOPES` table (the same one
        `cmb_move_scope_init` populates the combo from), so a scope renamed there
        fails these tests loudly instead of silently testing a stale label.
        *snap* is a `_SNAP_*` index, matching the tri-state button's cycle.
        """
        data = ShellXformSlots._MOVE_SCOPES[text]
        self.slot.ui = NS(
            cmb_move_scope=NS(currentText=lambda: text, currentData=lambda: data)
        )
        self.slot._snap_action = NS(current_state=snap)

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
        self.set_scope("Half Tile", snap=ShellXformSlots._SNAP_GRID)

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
        self.set_scope("Tile", snap=ShellXformSlots._SNAP_GRID)

        self.slot.b025()  # onto the padded grid
        landed = UvUtils.get_uv_bounds(plane)[1]
        self.assertAlmostEqual(landed % 1.0, margin, places=5)

        self.slot.b025()
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], landed + 1.0, places=5)
        self.slot.b024()
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], landed, places=5)

    def test_snap_off_preserves_sub_tile_drift(self):
        plane = self.make_plane(v_offset=0.6)
        self.set_scope("Half Tile", snap=ShellXformSlots._SNAP_OFF)

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

    def test_snap_mode_defaults_off_when_unset(self):
        """A slots instance that never ran `cmb_move_scope_init` must not crash."""
        del self.slot._snap_action
        self.assertEqual(self.slot._snap_mode(), ShellXformSlots._SNAP_OFF)

    def test_snap_states_match_the_mode_constants(self):
        """The cycle index IS the mode, so the states must be in `_SNAP_*` order
        and each must be visually distinct from its neighbours."""
        states = self.slot._snap_states()
        self.assertEqual(len(states), 3)
        self.assertEqual(
            [ShellXformSlots._SNAP_OFF, ShellXformSlots._SNAP_GRID,
             ShellXformSlots._SNAP_SHELL],
            [0, 1, 2],
        )
        # Every state is tinted, and no two share a tint — colour alone has to
        # separate the two enabled modes.
        colors = [s["color"] for s in states]
        self.assertTrue(all(colors))
        self.assertEqual(len(set(colors)), 3)
        # Shell snap also gets its own icon; grid on/off share one deliberately.
        self.assertNotEqual(
            states[ShellXformSlots._SNAP_SHELL]["icon"],
            states[ShellXformSlots._SNAP_GRID]["icon"],
        )
        self.assertTrue(all(s["tooltip"] for s in states))

    # ------------------------------------------------------------------ shell snap
    def neighbor_plane(self, u_offset=0.0, v_offset=0.0):
        """An unselected plane parked at an offset — a shell to snap against."""
        plane = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        UvUtils.move_to_uv_space(plane, u_offset, v_offset)
        return plane

    def test_shell_snap_parks_against_the_neighbor(self):
        margin = self.slot._border_margin()
        neighbor = self.neighbor_plane(v_offset=3.0)  # V 3.0 .. 4.0
        plane = self.make_plane()  # V 0.0 .. 1.0, selected
        self.set_scope("Tile", snap=ShellXformSlots._SNAP_SHELL)

        self.slot.b025()  # up

        # Its top edge lands one margin under the neighbour's bottom.
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[3], 3.0 - margin, places=5)
        # The neighbour did not move.
        self.assertAlmostEqual(UvUtils.get_uv_bounds(neighbor)[1], 3.0, places=5)

    def test_shell_snap_ignores_the_scope(self):
        """The neighbour sets the distance, so every scope lands identically."""
        margin = self.slot._border_margin()
        landings = []
        for scope in ("Tile", "Quarter Tile", "Selection Bounds"):
            cmds.file(new=True, force=True)
            self.neighbor_plane(v_offset=3.0)
            plane = self.make_plane()
            self.set_scope(scope, snap=ShellXformSlots._SNAP_SHELL)
            self.slot.b025()
            landings.append(round(UvUtils.get_uv_bounds(plane)[3], 5))
        self.assertEqual(len(set(landings)), 1, landings)
        self.assertAlmostEqual(landings[0], 3.0 - margin, places=5)

    def test_shell_snap_falls_back_to_the_grid_with_no_neighbor(self):
        """Nothing ahead must still move the shell — a dead arrow reads as a bug."""
        margin = self.slot._border_margin()
        plane = self.make_plane(v_offset=0.6)
        self.set_scope("Half Tile", snap=ShellXformSlots._SNAP_SHELL)

        self.slot.b025()

        # Exactly the grid-snap result.
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], 1.0 + margin, places=5)

    def test_shell_snap_does_not_park_against_its_own_shell(self):
        """The moving selection must not appear in its own blocker pool.

        Its own box sits at the box's position, so a self-blocker would offset
        the shell by its own height instead of falling back to the grid.
        """
        margin = self.slot._border_margin()
        plane = self.make_plane(v_offset=0.6)
        self.set_scope("Half Tile", snap=ShellXformSlots._SNAP_SHELL)

        self.assertEqual(UvUtils.get_neighbor_shell_bounds(plane), [])
        self.slot.b025()
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], 1.0 + margin, places=5)

    def test_shell_snap_ignores_a_neighbor_out_of_the_lane(self):
        margin = self.slot._border_margin()
        self.neighbor_plane(u_offset=3.0, v_offset=3.0)  # off to the side
        plane = self.make_plane(v_offset=0.6)
        self.set_scope("Half Tile", snap=ShellXformSlots._SNAP_SHELL)

        self.slot.b025()

        # No lane blocker -> grid fallback, not a jump to the stray.
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], 1.0 + margin, places=5)

    def test_shell_snap_walks_past_a_neighbor_on_the_second_press(self):
        margin = self.slot._border_margin()
        self.neighbor_plane(v_offset=3.0)  # V 3.0 .. 4.0
        plane = self.make_plane()
        self.set_scope("Tile", snap=ShellXformSlots._SNAP_SHELL)

        self.slot.b025()
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[3], 3.0 - margin, places=5)

        self.slot.b025()  # clear it rather than sitting still
        self.assertAlmostEqual(UvUtils.get_uv_bounds(plane)[1], 4.0 + margin, places=5)

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


class ShellXformDistributeTest(MayaTkTestCase):
    """tb006 — Distribute leaves the panel's tile spacing between shells.

    The slot used to hand ``texDistributeShells`` a hardcoded ``0``, which butts
    every shell against its neighbour: a distributed row bleeds across its own
    seams at render time and cannot be packed without a repack pass. The gap has
    to be the same ``_border_margin`` the move pad's shell snap and Gather use,
    so the panel speaks with one spacing value.
    """

    def setUp(self):
        super().setUp()
        self.slot = ShellXformSlots.__new__(ShellXformSlots)
        self.slot.sb = NS(message_box=lambda *a, **k: None)

    @staticmethod
    def widget(axis):
        """Stand in for tb006's option box — one radio button per axis."""
        return NS(
            option_box=NS(
                menu=NS(
                    chk023=NS(isChecked=lambda: axis == "u"),
                    chk024=NS(isChecked=lambda: axis == "v"),
                )
            )
        )

    def spread_planes(self, axis, count=3):
        """*count* separated 1x1 UV shells, selected, strung out along *axis*."""
        planes = []
        for i in range(count):
            plane = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
            step = i * 3.0
            UvUtils.move_to_uv_space(
                plane, step if axis == "u" else 0.0, step if axis == "v" else 0.0
            )
            planes.append(plane)
        cmds.select([f"{p}.f[*]" for p in planes], replace=True)
        return planes

    def gaps(self, planes, axis):
        """Inter-shell gaps along *axis*, in layout order, after a distribute."""
        lo, hi = (0, 2) if axis == "u" else (1, 3)
        spans = sorted(
            (bounds[lo], bounds[hi])
            for bounds in (UvUtils.get_uv_bounds(p) for p in planes)
        )
        return [spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1)]

    def assert_gaps(self, axis, expected):
        """Distribute along *axis* and assert every resulting gap is *expected*."""
        planes = self.spread_planes(axis)

        self.slot.tb006(self.widget(axis))

        gaps = self.gaps(planes, axis)
        self.assertEqual(len(gaps), len(planes) - 1)
        for gap in gaps:
            self.assertAlmostEqual(gap, expected, places=6)

    def test_distribute_u_leaves_the_tile_margin_between_shells(self):
        self.assert_gaps("u", self.slot._border_margin())

    def test_distribute_v_leaves_the_tile_margin_between_shells(self):
        self.assert_gaps("v", self.slot._border_margin())

    def test_the_gap_follows_the_panels_spacing_rather_than_a_constant(self):
        """The two tests above still pass if the margin is re-hardcoded to its
        current value. This one overrides `_border_margin` so only a slot that
        actually reads the panel's spacing can satisfy it."""
        self.slot._border_margin = lambda: 0.05

        self.assert_gaps("u", 0.05)


if __name__ == "__main__":
    unittest.main(exit=False)
