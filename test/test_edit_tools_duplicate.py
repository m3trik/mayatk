# !/usr/bin/python
# coding=utf-8
"""Test Suite for edit_utils duplicate tool classes.

Covers:
    - DuplicateLinear.duplicate_linear (duplicate_linear.py)
    - DuplicateRadial.duplicate_radial + _validate_inputs (duplicate_radial.py)
    - DuplicateGrid.duplicate_grid (duplicate_grid.py)
    - Preview integration semantics for all three slots classes
      (MUTATES_SELECTION contract).
"""
import unittest

import maya.cmds as cmds

from mayatk.edit_utils.duplicate_linear import DuplicateLinear, DuplicateLinearSlots
from mayatk.edit_utils.duplicate_radial import DuplicateRadial, DuplicateRadialSlots
from mayatk.edit_utils.duplicate_grid import DuplicateGrid
from mayatk.core_utils.preview import Preview

from base_test import MayaTkTestCase, QuickTestCase


class TestDuplicateLinear(MayaTkTestCase):
    """DuplicateLinear.duplicate_linear — copies arranged on a linear progression."""

    def test_returns_dict_keyed_by_original(self):
        cube = cmds.polyCube(name="dl_cube")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube],
            num_copies=3,
            translate=(2, 0, 0),
            instance=False,
        )
        self.assertIn(cube, result)
        self.assertEqual(len(result[cube]), 3)

    def test_creates_correct_number_of_copies(self):
        cube = cmds.polyCube(name="dl_count")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube],
            num_copies=5,
            translate=(1, 0, 0),
            instance=True,
        )
        self.assertEqual(len(result[cube]), 5)
        for copy in result[cube]:
            self.assertTrue(cmds.objExists(copy))

    def test_zero_copies_returns_empty(self):
        cube = cmds.polyCube(name="dl_zero")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube],
            num_copies=0,
            translate=(1, 0, 0),
        )
        self.assertEqual(result[cube], [])

    def test_instance_creates_real_instances(self):
        cube = cmds.polyCube(name="dl_inst")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube], num_copies=2, translate=(1, 0, 0), instance=True
        )
        # Instances share the original's shape node — same shape object
        # parented under multiple transforms. Compare by UUID, not DAG path.
        orig_shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        orig_uuid = cmds.ls(orig_shape, uuid=True)[0]
        for copy in result[cube]:
            copy_shapes = cmds.listRelatives(copy, shapes=True, fullPath=True) or []
            copy_uuids = {cmds.ls(s, uuid=True)[0] for s in copy_shapes}
            self.assertIn(orig_uuid, copy_uuids)

    def test_does_not_delete_original(self):
        """duplicate_linear must preserve the input transforms."""
        cube = cmds.polyCube(name="dl_keep_orig")[0]
        DuplicateLinear.duplicate_linear(
            objects=[cube], num_copies=3, translate=(1, 0, 0), instance=False
        )
        self.assertTrue(cmds.objExists(cube))

    def test_translate_is_applied_along_axis(self):
        cube = cmds.polyCube(name="dl_xlate")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube],
            num_copies=2,
            translate=(5, 0, 0),
            calculation_mode="linear",
            instance=False,
        )
        # Last copy should be displaced from origin in +X.
        last = result[cube][-1]
        pos = cmds.xform(last, q=True, ws=True, t=True)
        self.assertGreater(pos[0], 0.0)


class TestDuplicateRadialValidation(QuickTestCase):
    """DuplicateRadial._validate_inputs is pure-Python — no Maya needed."""

    def test_invalid_axis_raises(self):
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("w", 0.5, 0.5)

    def test_weight_bias_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("y", 1.5, 0.5)
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("y", -0.1, 0.5)

    def test_weight_curve_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("y", 0.5, 1.5)
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("y", 0.5, -0.1)

    def test_valid_inputs_pass(self):
        DuplicateRadial._validate_inputs("x", 0.0, 0.0)
        DuplicateRadial._validate_inputs("y", 0.5, 0.5)
        DuplicateRadial._validate_inputs("z", 1.0, 1.0)


class TestDuplicateRadial(MayaTkTestCase):
    """DuplicateRadial.duplicate_radial — radial pattern."""

    def test_creates_expected_number_of_copies(self):
        cube = cmds.polyCube(name="dr_cube")[0]
        cmds.move(5, 0, 0, cube)

        result = DuplicateRadial.duplicate_radial(
            objects=[cube],
            num_copies=4,
            start_angle=0,
            end_angle=360,
            rotate_axis="y",
            keep_original=True,
            instance=True,
            suffix=False,
        )
        self.assertIn(cube, result)
        self.assertEqual(len(result[cube]), 4)
        for copy in result[cube]:
            self.assertTrue(cmds.objExists(copy))

    def test_invalid_axis_raises(self):
        cube = cmds.polyCube(name="dr_bad")[0]
        with self.assertRaises(ValueError):
            DuplicateRadial.duplicate_radial(
                objects=[cube],
                num_copies=2,
                rotate_axis="bogus",
            )

    def test_keep_original_true_preserves_input(self):
        cube = cmds.polyCube(name="dr_keep")[0]
        cmds.move(5, 0, 0, cube)
        DuplicateRadial.duplicate_radial(
            objects=[cube],
            num_copies=3,
            rotate_axis="y",
            keep_original=True,
            instance=True,
            suffix=False,
        )
        self.assertTrue(cmds.objExists(cube))

    def test_keep_original_false_deletes_input(self):
        """Regression: keep_original=False deletes the input transform.

        This is the documented behavior that broke Preview refresh — a
        second refresh re-targets the captured name and fails. The fix
        lives on DuplicateRadialSlots.MUTATES_SELECTION; this test pins
        the underlying deletion contract.
        """
        cube = cmds.polyCube(name="dr_del")[0]
        cmds.move(5, 0, 0, cube)
        DuplicateRadial.duplicate_radial(
            objects=[cube],
            num_copies=3,
            rotate_axis="y",
            keep_original=False,
            instance=True,
            suffix=False,
        )
        self.assertFalse(cmds.objExists(cube))

    def test_combine_returns_single_mesh(self):
        cube = cmds.polyCube(name="dr_combine")[0]
        cmds.move(5, 0, 0, cube)
        result = DuplicateRadial.duplicate_radial(
            objects=[cube],
            num_copies=4,
            rotate_axis="y",
            keep_original=False,
            instance=False,  # combine requires real geometry, not instances
            combine=True,
            suffix=False,
        )
        # combine=True returns a single combined mesh per original.
        self.assertEqual(len(result[cube]), 1)
        self.assertTrue(cmds.objExists(result[cube][0]))

    def test_full_circle_does_not_overlap_first_and_last(self):
        """Regression: a full 360 sweep must not stack the last copy on the
        first.

        With the inclusive-endpoint divisor (num_copies - 1), num_copies=6
        over 0-360 placed copies at 0, 72, ... 360 -- and 360 == 0, so the
        first and last landed in the same spot: "count of 6 but I see 5, and
        the doubled start object won't translate as one." A whole revolution
        must drop the shared endpoint and yield num_copies distinct positions.
        """
        cube = cmds.polyCube(name="dr_overlap")[0]
        cmds.move(5, 0, 0, cube)  # off the pivot so each angle is a new spot
        result = DuplicateRadial.duplicate_radial(
            objects=[cube],
            num_copies=6,
            start_angle=0,
            end_angle=360,
            rotate_axis="y",
            pivot="world",
            keep_original=False,
            instance=True,
            suffix=False,
        )
        copies = result[cube]
        self.assertEqual(len(copies), 6)
        positions = {
            tuple(round(v, 3) for v in cmds.xform(c, q=True, ws=True, t=True))
            for c in copies
        }
        self.assertEqual(
            len(positions), 6, "full-circle copies overlap (first == last)"
        )

    def test_partial_arc_keeps_both_endpoints(self):
        """A non-revolution sweep must remain endpoint-inclusive: 3 copies
        over 0-180 belong at 0, 90, 180 -- dropping the endpoint here would
        regress the arc behavior the full-circle fix must not touch."""
        cube = cmds.polyCube(name="dr_arc")[0]
        cmds.move(5, 0, 0, cube)
        result = DuplicateRadial.duplicate_radial(
            objects=[cube],
            num_copies=3,
            start_angle=0,
            end_angle=180,
            rotate_axis="y",
            pivot="world",
            keep_original=False,
            instance=True,
            suffix=False,
        )
        copies = result[cube]
        self.assertEqual(len(copies), 3)
        xs = sorted(cmds.xform(c, q=True, ws=True, t=True)[0] for c in copies)
        # 0 deg -> +5x, 90 deg -> 0x, 180 deg -> -5x. Endpoints inclusive.
        self.assertAlmostEqual(xs[0], -5.0, places=3)
        self.assertAlmostEqual(xs[-1], 5.0, places=3)

    def test_invalid_weight_bias_raises_through_public_api(self):
        cube = cmds.polyCube(name="dr_bias")[0]
        with self.assertRaises(ValueError):
            DuplicateRadial.duplicate_radial(
                objects=[cube],
                num_copies=2,
                weight_bias=2.0,  # out of [0, 1]
            )


class TestDuplicateGrid(MayaTkTestCase):
    """DuplicateGrid.duplicate_grid — 3D grid duplication.

    Output is selected by ``mode``: ``"instance"`` / ``"copy"`` return the
    container group (str), ``"combine"`` returns a single-element list holding
    the merged mesh. All three name the result after the source object.
    """

    @staticmethod
    def _copies(group):
        """The duplicate transforms live as children of the returned group."""
        return cmds.listRelatives(group, children=True, fullPath=True) or []

    def test_empty_objects_returns_empty(self):
        result = DuplicateGrid.duplicate_grid(objects=[], dimensions=(2, 2, 2))
        self.assertEqual(result, [])

    def test_invalid_mode_raises(self):
        cube = cmds.polyCube(name="dg_badmode")[0]
        with self.assertRaises(ValueError):
            DuplicateGrid.duplicate_grid(
                objects=[cube], dimensions=(2, 1, 1), mode="bogus"
            )

    def test_grid_one_in_each_axis(self):
        cube = cmds.polyCube(name="dg_min")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(1, 1, 1), spacing=0, mode="instance"
        )
        # instance/copy return the container group name (str).
        self.assertIsInstance(result, str)
        self.assertTrue(cmds.objExists(result))

    def test_grid_count_matches_x_y_z_product(self):
        cube = cmds.polyCube(name="dg_count")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube],
            dimensions=(2, 3, 2),
            spacing=1,
            mode="instance",
        )
        copies = self._copies(result)
        # Expect x * y * z = 12 duplicates under the group.
        self.assertEqual(len(copies), 2 * 3 * 2)
        for d in copies:
            self.assertTrue(cmds.objExists(d))

    def test_grid_zero_x_dimension_returns_empty(self):
        """Regression: cmds.ungroup raises 'Can't ungroup leaf-level transforms'
        when any dimension is 0. Early return prevents the crash.
        """
        cube = cmds.polyCube(name="dg_zx")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(0, 2, 2), spacing=0, mode="instance"
        )
        self.assertEqual(result, [])
        # Originals must survive — early return runs before any scene mutation.
        self.assertTrue(cmds.objExists(cube))

    def test_grid_zero_y_dimension_returns_empty(self):
        cube = cmds.polyCube(name="dg_zy")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 0, 2), spacing=0, mode="instance"
        )
        self.assertEqual(result, [])
        self.assertTrue(cmds.objExists(cube))

    def test_grid_zero_z_dimension_returns_empty(self):
        cube = cmds.polyCube(name="dg_zz")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 2, 0), spacing=0, mode="instance"
        )
        self.assertEqual(result, [])
        self.assertTrue(cmds.objExists(cube))

    def test_grid_negative_dimension_creates_copies(self):
        """Negative dims should produce the absolute number of copies
        in the opposite direction (see abs() in the loop)."""
        cube = cmds.polyCube(name="dg_neg")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube],
            dimensions=(-2, 2, 1),
            spacing=1,
            mode="instance",
        )
        self.assertEqual(len(self._copies(result)), 2 * 2 * 1)

    def test_grid_preserves_original(self):
        """duplicate_grid must leave the source transform untouched."""
        cube = cmds.polyCube(name="dg_keep")[0]
        DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 2, 1), spacing=1, mode="instance"
        )
        self.assertTrue(cmds.objExists(cube))

    def test_grid_does_not_reparent_or_rename_original(self):
        """The source must stay a world-level, unambiguous transform — the old
        temp-group/flatten reparented it and let a copy steal its name."""
        cube = cmds.polyCube(name="dg_src_identity")[0]
        cube_uuid = cmds.ls(cube, uuid=True)[0]
        DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(3, 2, 1), spacing=1, mode="instance"
        )
        # Same node (UUID), still named dg_src_identity, still at world, unique.
        self.assertEqual(cmds.ls(cube_uuid, long=True), ["|dg_src_identity"])
        self.assertEqual(len(cmds.ls("dg_src_identity") or []), 1)
        self.assertIsNone(cmds.listRelatives("dg_src_identity", parent=True))

    def test_grid_positions_form_a_grid(self):
        """Copies land on the bbox-stepped lattice (unit cube, base 1, spacing 1
        -> step 2)."""
        cube = cmds.polyCube(name="dg_pos")[0]  # spans -0.5..0.5
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 2, 1), spacing=1, mode="instance"
        )
        xy = sorted(
            tuple(round(v, 3) for v in cmds.xform(o, q=True, ws=True, t=True)[:2])
            for o in self._copies(result)
        )
        self.assertEqual(xy, [(0.0, 0.0), (0.0, 2.0), (2.0, 0.0), (2.0, 2.0)])

    def test_grid_negative_count_lays_out_in_opposite_direction(self):
        cube = cmds.polyCube(name="dg_negdir")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(-2, 1, 1), spacing=1, mode="instance"
        )
        xs = sorted(
            round(cmds.xform(o, q=True, ws=True, t=True)[0], 3)
            for o in self._copies(result)
        )
        self.assertEqual(xs, [-2.0, 0.0])

    def test_combine_returns_single_named_mesh(self):
        """combine merges every copy into one mesh named after the source."""
        cube = cmds.polyCube(name="dg_comb")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 2, 1), spacing=1, mode="combine"
        )
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        combined = result[0]
        self.assertTrue(cmds.objExists(combined))
        # Exactly one mesh shape, and the source still exists.
        self.assertEqual(
            len(cmds.listRelatives(combined, shapes=True, type="mesh") or []), 1
        )
        self.assertIn("dg_comb", combined)
        self.assertTrue(cmds.objExists(cube))

    def test_combine_single_object_does_not_crash(self):
        """A 1x1x1 combine yields one copy — polyUnite rejects a lone input, so
        the lift-and-rename path must handle it."""
        cube = cmds.polyCube(name="dg_comb1")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(1, 1, 1), spacing=0, mode="combine"
        )
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertTrue(cmds.objExists(result[0]))
        self.assertIn("dg_comb1", result[0])
        self.assertTrue(cmds.objExists(cube))

    def test_result_named_after_source(self):
        """instance/copy return a group whose name derives from the source."""
        cube = cmds.polyCube(name="dg_named")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 1, 1), spacing=1, mode="instance"
        )
        self.assertIn("dg_named", result)

    def test_instance_mode_shares_one_shape(self):
        """instance copies all share a single shape node."""
        cube = cmds.polyCube(name="dg_inst")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 2, 1), spacing=1, mode="instance"
        )
        shape_uuids = {
            cmds.ls(cmds.listRelatives(c, shapes=True, fullPath=True)[0], uuid=True)[0]
            for c in self._copies(result)
        }
        self.assertEqual(len(shape_uuids), 1)

    def test_copy_mode_makes_unique_geometry(self):
        """copy mode gives every duplicate its own (distinct) shape node."""
        cube = cmds.polyCube(name="dg_uniq")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 2, 1), spacing=1, mode="copy"
        )
        copies = self._copies(result)
        shape_uuids = {
            cmds.ls(cmds.listRelatives(c, shapes=True, fullPath=True)[0], uuid=True)[0]
            for c in copies
        }
        self.assertEqual(len(shape_uuids), len(copies))

    def test_instance_mode_leaves_source_shape_pristine(self):
        """Regression: instance mode used to instance the SOURCE's own shape, and
        reparenting the copies made Maya renumber that shared shape (e.g.
        srcShape -> srcShape4). That mutated the user's node and corrupted the
        preview's path-based rollback, which could then delete the shared shape
        and leave a husk of empty transforms when switching modes mid-preview.
        The grid must share a fresh shape across the COPIES and leave the
        source's shape untouched (same node, same name, not shared)."""
        cube = cmds.polyCube(name="dg_pristine")[0]
        src_shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        src_name = src_shape.split("|")[-1]
        src_uuid = cmds.ls(src_shape, uuid=True)[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 2, 1), spacing=1, mode="instance"
        )
        alive = cmds.ls(src_uuid, long=True)
        self.assertTrue(alive, "source shape node was deleted")
        self.assertEqual(
            alive[0].split("|")[-1], src_name, "source shape was renamed/mutated"
        )
        # Copies are still instanced (one shared shape) but NOT off the source.
        copy_uuids = {
            cmds.ls(cmds.listRelatives(c, shapes=True, fullPath=True)[0], uuid=True)[0]
            for c in self._copies(result)
        }
        self.assertEqual(len(copy_uuids), 1)
        self.assertNotIn(src_uuid, copy_uuids)

    def test_grid_slots_declares_mutates_selection(self):
        """MUTATES_SELECTION=True keeps Preview's UUID-restore backstop (a cheap
        defensive guard, matching linear/radial) even though the grid no longer
        mutates the source."""
        from mayatk.edit_utils.duplicate_grid import DuplicateGridSlots

        self.assertTrue(getattr(DuplicateGridSlots, "MUTATES_SELECTION", False))


class TestDuplicateSlotsContract(QuickTestCase):
    """Regression: Preview contract for ops that delete or invalidate the
    captured selection between refreshes.

    Pure-Python attribute check — no Maya needed.
    """

    def test_radial_slots_declares_mutates_selection(self):
        """DuplicateRadial deletes the original when keep_original=False.
        Without MUTATES_SELECTION=True, Preview's second refresh re-targets
        the deleted name and cmds.duplicate returns NULL.
        """
        self.assertTrue(getattr(DuplicateRadialSlots, "MUTATES_SELECTION", False))

    def test_linear_slots_declares_mutates_selection(self):
        """Defensive: linear shouldn't delete its input, but the captured
        selection can be invalidated by external mutations between
        refreshes. MUTATES_SELECTION=True turns those into recoverable
        no-ops via rollback's UUID restore path.
        """
        self.assertTrue(getattr(DuplicateLinearSlots, "MUTATES_SELECTION", False))


class _MockSignal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *args):
        for fn in list(self._slots):
            fn(*args)


class _MockWidget:
    """Mock checkbox / button exposing only what Preview touches."""

    def __init__(self):
        self.toggled = _MockSignal()
        self.clicked = _MockSignal()
        self._checked = False
        self._enabled = True
        self.exclude_from_reset = False
        self.restore_state = True

    def setChecked(self, v):
        self._checked = bool(v)

    def isChecked(self):
        return self._checked

    def setEnabled(self, v):
        self._enabled = bool(v)

    def isEnabled(self):
        return self._enabled

    def blockSignals(self, v):
        return False

    def window(self):
        return None


class _DupLinearPreviewOp:
    """Stand-in for DuplicateLinearSlots: mutable params forwarding to
    DuplicateLinear.duplicate_linear. Mirrors the real MUTATES_SELECTION opt-in."""

    MUTATES_SELECTION = True

    def __init__(self, **params):
        self.params = dict(num_copies=2, translate=(3, 0, 0), instance=True)
        self.params.update(params)

    def perform_operation(self, objects, contract):
        DuplicateLinear.duplicate_linear(objects, **self.params)


class TestDuplicateLinearPreviewRollback(MayaTkTestCase):
    """Regression: instance-mode preview must not destroy the original's shape
    on rollback.

    Bug: cmds.instance shares the ORIGINAL's shape node across the new instance
    transforms. The hermetic preview's node-diff (cmds.ls allPaths=True) captured
    the instanced shape PATH as 'created' and deleted it on rollback, which
    orphaned the original -- the transform survived (so the MUTATES_SELECTION
    UUID restore saw it as alive) but its shape was gone. "Losing the object I'm
    trying to duplicate." Verified in Maya before fix.
    """

    @staticmethod
    def _has_shape(node):
        return bool(cmds.listRelatives(node, shapes=True, fullPath=True))

    def _make_preview(self, op):
        pv = Preview(op, _MockWidget(), _MockWidget(), message_func=lambda *a: None)
        self._previews.append(pv)
        return pv

    def setUp(self):
        super().setUp()
        self._previews = []

    def tearDown(self):
        for pv in self._previews:
            try:
                pv.cleanup()
            except Exception:
                pass
        Preview.cleanup_all_instances()
        super().tearDown()

    def test_instance_preview_disable_keeps_original(self):
        cube = cmds.polyCube(name="dl_inst_keep")[0]
        orig_faces = cmds.polyEvaluate(cube, face=True)

        op = _DupLinearPreviewOp(instance=True)
        pv = self._make_preview(op)
        cmds.select(cube)
        pv.enable()
        self.assertTrue(self._has_shape(cube), "preview lost the shape on enable")

        pv.disable()
        self.assertTrue(
            self._has_shape(cube), "rollback destroyed the original's shape"
        )
        self.assertEqual(cmds.polyEvaluate(cube, face=True), orig_faces)

    def test_instance_preview_refresh_keeps_original(self):
        cube = cmds.polyCube(name="dl_inst_refresh")[0]
        orig_faces = cmds.polyEvaluate(cube, face=True)

        op = _DupLinearPreviewOp(instance=True, num_copies=2)
        pv = self._make_preview(op)
        cmds.select(cube)
        pv.enable()

        op.params["num_copies"] = 4  # "change the count" -> refresh
        pv.refresh()
        self.assertTrue(
            self._has_shape(cube), "refresh destroyed the original's shape"
        )

        pv.disable()
        self.assertTrue(self._has_shape(cube))
        self.assertEqual(cmds.polyEvaluate(cube, face=True), orig_faces)


class _DupGridPreviewOp:
    """Stand-in for DuplicateGridSlots forwarding to DuplicateGrid.duplicate_grid.
    Mirrors the real MUTATES_SELECTION opt-in."""

    MUTATES_SELECTION = True

    def __init__(self, **params):
        self.params = dict(dimensions=(3, 3, 1), spacing=1, mode="instance")
        self.params.update(params)

    def perform_operation(self, objects, contract):
        DuplicateGrid.duplicate_grid(objects, **self.params)


class TestDuplicateGridPreviewRollback(MayaTkTestCase):
    """Regression: the grid preview must not delete the user's source object on
    rollback.

    Bug: duplicate_grid grouped the originals into a temp node and flattened the
    copies through world, which vacated the originals' short names and let a copy
    collide with them ("More than one object matches name"). Preview's path-based
    diff then could not tell them apart and deleted the original (and its shape)
    when the preview was switched off -- in BOTH instance and non-instance modes.
    Verified by UUID in Maya before the fix (the source's UUID was gone after
    disable). The fix never reparents the originals.
    """

    @staticmethod
    def _uuid(node):
        return cmds.ls(node, uuid=True)[0]

    @staticmethod
    def _alive(uuid):
        return bool(cmds.ls(uuid))

    def _make_preview(self, op):
        pv = Preview(op, _MockWidget(), _MockWidget(), message_func=lambda *a: None)
        self._previews.append(pv)
        return pv

    def setUp(self):
        super().setUp()
        self._previews = []

    def tearDown(self):
        for pv in self._previews:
            try:
                pv.cleanup()
            except Exception:
                pass
        Preview.cleanup_all_instances()
        super().tearDown()

    def _assert_survives_cycle(self, mode):
        cube = cmds.polyCube(name="dg_src")[0]
        cube_uuid = self._uuid(cube)
        shape_uuid = self._uuid(cmds.listRelatives(cube, shapes=True)[0])

        op = _DupGridPreviewOp(mode=mode)
        pv = self._make_preview(op)
        cmds.select(cube)
        pv.enable()
        # No name collision: exactly one node named dg_src (the original).
        self.assertEqual(len(cmds.ls("dg_src") or []), 1, "a copy collided with the source name")

        pv.disable()
        self.assertTrue(self._alive(cube_uuid), "rollback deleted the source object")
        self.assertTrue(self._alive(shape_uuid), "rollback deleted the source's shape")
        # Scene is clean: only the source survives (plus default cameras).
        leftover = [
            t for t in cmds.ls(type="transform")
            if t not in ("persp", "top", "front", "side")
        ]
        self.assertEqual(leftover, ["dg_src"], f"scene not clean after rollback: {leftover}")

    def test_instance_preview_disable_keeps_source(self):
        self._assert_survives_cycle(mode="instance")

    def test_copy_preview_disable_keeps_source(self):
        self._assert_survives_cycle(mode="copy")

    def test_combine_preview_disable_keeps_source(self):
        self._assert_survives_cycle(mode="combine")

    def test_refresh_keeps_source(self):
        cube = cmds.polyCube(name="dg_refresh")[0]
        cube_uuid = self._uuid(cube)
        op = _DupGridPreviewOp(mode="instance", dimensions=(2, 2, 1))
        pv = self._make_preview(op)
        cmds.select(cube)
        pv.enable()
        op.params["dimensions"] = (4, 4, 1)  # "change the count" -> refresh
        pv.refresh()
        self.assertTrue(self._alive(cube_uuid), "refresh deleted the source")
        pv.disable()
        self.assertTrue(self._alive(cube_uuid))


if __name__ == "__main__":
    unittest.main()
