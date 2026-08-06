# !/usr/bin/python
# coding=utf-8
"""Test Suite for edit_utils geometry tool classes.

Covers:
    - Bevel.bevel (bevel.py)
    - Bridge.bridge / get_child_curves_from_bridge / cleanup (bridge.py)
    - Snap.snap_to_closest_vertex / snap_to_surface / snap_to_grid (snap.py)
    - CutOnAxis.perform_cut_on_axis (cut_on_axis.py)
    - MirrorSlots._resolve_pivot (mirror.py — static helper, only testable surface)

The Slots classes themselves are UI-bound and skipped here.
"""
import unittest
from unittest import mock

import maya.cmds as cmds
import maya.api.OpenMaya as om

from mayatk.edit_utils.bevel import Bevel, BevelSlots
from mayatk.edit_utils.bridge import Bridge
from mayatk.edit_utils.snap import Snap
from mayatk.edit_utils.cut_on_axis import CutOnAxis, CutOnAxisSlots
from mayatk.edit_utils.mirror import MirrorSlots
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.core_utils.preview import Preview, OperationError

from base_test import MayaTkTestCase, QuickTestCase, skipIfBatch


class TestBevel(MayaTkTestCase):
    """Bevel.bevel — wraps cmds.polyBevel3."""

    def test_bevel_increases_face_count(self):
        cube = cmds.polyCube(name="bvl_cube")[0]
        before = cmds.polyEvaluate(cube, face=True)

        Bevel.bevel([f"{cube}.e[0]"], width=0.2, segments=2)

        after = cmds.polyEvaluate(cube, face=True)
        self.assertGreater(after, before)

    def test_bevel_with_default_args_runs(self):
        cube = cmds.polyCube(name="bvl_default")[0]
        # Should not raise with defaults
        Bevel.bevel([f"{cube}.e[1]"])

    # ------------------------------------------------ silent no-op detection
    # Issue-driven (2026-07-25): polyBevel3 does not raise when the requested
    # width/segment combination cannot fit the adjacent geometry — it silently
    # produces NO topology change. Inside a Preview refresh that silence is
    # destructive UX: the rollback removes the visible bevel, the "successful"
    # re-run applies nothing, and the user — seeing their bevel vanish with no
    # error — reaches for Ctrl+Z and undoes real pre-preview work.

    def _counts(self, obj):
        return (
            cmds.polyEvaluate(obj, vertex=True),
            cmds.polyEvaluate(obj, edge=True),
            cmds.polyEvaluate(obj, face=True),
        )

    def _flat_cube(self, name="flat_cube"):
        """Cube squashed to 0.001 in Y (frozen): polyBevel3 handles low segment
        counts but silently no-ops at higher ones — the live repro geometry."""
        cube = cmds.polyCube(name=name)[0]
        cmds.setAttr(f"{cube}.scaleY", 0.001)
        cmds.makeIdentity(cube, apply=True, scale=True)
        cmds.delete(cube, constructionHistory=True)
        return cube

    def test_silent_noop_raises_operation_error(self):
        """seg=4 on the flat cube: polyBevel3 'succeeds' but changes nothing.
        Bevel.bevel must convert that silence into an OperationError."""
        cube = self._flat_cube()
        before = self._counts(cube)

        with self.assertRaises(OperationError) as ctx:
            Bevel.bevel([f"{cube}.e[0]"], width=0.23, segments=4)

        self.assertEqual(self._counts(cube), before)
        self.assertIn(cube, str(ctx.exception.user_message))

    def test_fitting_segments_on_same_mesh_do_not_raise(self):
        """Guard: the same flat cube DOES bevel at seg=1 — detection must not
        flag a working configuration."""
        cube = self._flat_cube(name="flat_cube_ok")
        before = self._counts(cube)
        Bevel.bevel([f"{cube}.e[0]"], width=0.23, segments=1)
        self.assertNotEqual(self._counts(cube), before)

    def test_duplicate_leaf_names_bevel_independently(self):
        """Two objects sharing a leaf name must each get their own polyBevel3
        call. Leaf-name grouping merged them into one component list, which
        polyBevel3 rejects outright ('Doesn't work with multiple objects
        selected') — probed 2026-07-25; fixed by unambiguous
        map_components_to_objects keys."""
        cmds.group(cmds.polyCube(name="dupCube")[0], name="dgrp1")
        cmds.group(cmds.polyCube(name="dupCube")[0], name="dgrp2")
        cmds.delete("dgrp1|dupCube", "dgrp2|dupCube", constructionHistory=True)
        before = {o: self._counts(o) for o in ("dgrp1|dupCube", "dgrp2|dupCube")}

        Bevel.bevel(
            ["dgrp1|dupCube.e[0]", "dgrp2|dupCube.e[0]"], width=0.23, segments=2
        )

        for obj, counts in before.items():
            self.assertNotEqual(self._counts(obj), counts, f"{obj} not beveled")

    def test_multi_object_reports_only_failed(self):
        """One healthy cube + one flat cube: the healthy bevel applies, the
        silent no-op raises and names only the failing object."""
        good = cmds.polyCube(name="bevel_good")[0]
        cmds.delete(good, constructionHistory=True)
        bad = self._flat_cube(name="bevel_bad")
        good_before = self._counts(good)

        with self.assertRaises(OperationError) as ctx:
            Bevel.bevel([f"{good}.e[0]", f"{bad}.e[0]"], width=0.23, segments=4)

        self.assertNotEqual(self._counts(good), good_before, "healthy bevel dropped")
        msg = str(ctx.exception.user_message)
        self.assertIn(bad, msg)
        self.assertNotIn(good, msg)


class TestBridge(MayaTkTestCase):
    """Bridge — connects edge borders."""

    def test_get_child_curves_from_clean_mesh_returns_empty(self):
        cube = cmds.polyCube(name="brg_clean")[0]
        result = Bridge.get_child_curves_from_bridge([cube])
        self.assertEqual(result, [])

    def test_cleanup_no_curves_does_not_raise(self):
        cube = cmds.polyCube(name="brg_no_curves")[0]
        # Should print "No child curves found" and return without error
        Bridge.cleanup_bridge_curves_and_history([cube])


class TestSnap(MayaTkTestCase):
    """Snap utilities — vertex/surface/grid snapping."""

    def test_snap_to_grid_no_objects_warns_and_returns_zero(self):
        cmds.select(clear=True)
        result = Snap.snap_to_grid()
        self.assertEqual(result, 0)

    def test_snap_to_grid_snaps_pivot(self):
        cube = cmds.polyCube(name="grid_cube")[0]
        cmds.move(2.7, 0, 1.3, cube)

        moved = Snap.snap_to_grid([cube], grid_size=1.0, axes="xyz")
        self.assertEqual(moved, 1)

        pos = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(pos[0], 3.0, places=4)
        self.assertAlmostEqual(pos[2], 1.0, places=4)

    def test_snap_to_grid_axes_subset(self):
        """Only the named axes should be snapped — others left alone."""
        cube = cmds.polyCube(name="grid_axis_cube")[0]
        cmds.move(2.7, 4.4, 1.3, cube)

        Snap.snap_to_grid([cube], grid_size=1.0, axes="x")

        pos = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(pos[0], 3.0, places=4)
        # Y and Z should be unchanged
        self.assertAlmostEqual(pos[1], 4.4, places=4)
        self.assertAlmostEqual(pos[2], 1.3, places=4)

    def test_snap_to_grid_custom_grid_size(self):
        cube = cmds.polyCube(name="grid_size_cube")[0]
        cmds.move(1.4, 0, 0, cube)

        Snap.snap_to_grid([cube], grid_size=0.5, axes="x")

        pos = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(pos[0], 1.5, places=4)

    def test_snap_to_surface_with_transform_input(self):
        """Regression: snap_to_surface's transform/mesh handling on string input.

        Bug fixed 2026-05-07: ``transform.type() == "mesh"`` (PyMEL idiom)
        crashed on cmds-style string nodes. Replaced with ``cmds.objectType``.
        """
        target = cmds.polyPlane(name="snap_target", w=4, h=4)[0]
        source = cmds.polyCube(name="snap_source")[0]
        cmds.move(0, 5, 0, source)  # source above target

        # Move some vertices below the plane to force snap movement.
        cmds.move(0, -3, 0, f"{source}.vtx[0]")

        # Should not raise — exercises the .objectType("mesh") branch transitively.
        Snap.snap_to_surface(source_meshes=source, target_mesh=target, offset=0.0)

        # Source still exists post-snap.
        self.assertTrue(cmds.objExists(source))

    def test_snap_to_surface_with_shape_input(self):
        """Regression: snap_to_surface explicitly handles mesh-shape inputs.

        Passing the shape directly used to crash on ``transform.type()``.
        Now the code calls ``cmds.objectType(transform)`` and walks up to the
        parent transform.
        """
        target = cmds.polyPlane(name="snap_target_2", w=4, h=4)[0]
        source_xform = cmds.polyCube(name="snap_src_2")[0]
        source_shape = cmds.listRelatives(source_xform, shapes=True)[0]
        cmds.move(0, -3, 0, f"{source_xform}.vtx[0]")

        # Pass the shape, not the transform — exercises the .objectType branch.
        Snap.snap_to_surface(source_meshes=source_shape, target_mesh=target, offset=0.0)

        self.assertTrue(cmds.objExists(source_xform))


class TestCutOnAxis(MayaTkTestCase):
    """CutOnAxis.perform_cut_on_axis — wraps EditUtils.cut_along_axis."""

    def test_zero_cuts_is_noop(self):
        cube = cmds.polyCube(name="cut_noop")[0]
        before_faces = cmds.polyEvaluate(cube, face=True)
        # cuts=0 should short-circuit with no operation
        CutOnAxis.perform_cut_on_axis([cube], axis="x", cuts=0)
        after_faces = cmds.polyEvaluate(cube, face=True)
        self.assertEqual(before_faces, after_faces)

    def test_one_cut_increases_geometry(self):
        cube = cmds.polyCube(name="cut_one", sx=1, sy=1, sz=1)[0]
        before = cmds.polyEvaluate(cube, face=True)
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="center", use_object_axes=True
        )
        after = cmds.polyEvaluate(cube, face=True)
        # A single cut through the middle of the cube splits the +X and -X
        # faces in half each: 6 → 8 faces.
        self.assertGreater(after, before)

    def test_manip_pivot_does_not_crash(self):
        """Regression: tool default pivot was 'manip' but PyMel-style
        node.getMatrix() crashed immediately on string nodes.
        """
        cube = cmds.polyCube(name="cut_manip")[0]
        cmds.select(cube)
        # Should complete without raising.
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="manip", use_object_axes=True
        )
        self.assertTrue(cmds.objExists(cube))

    def test_manip_pivot_falls_back_to_rotate_pivot_on_moved_cube(self):
        """Regression: cmds.manipPivot returns (0,0,0) when no transform tool
        is active, so a moved primitive's manip-pivot cut was happening at
        world origin instead of at the object's pivot. We now fall back to
        the object's rotate pivot when manipPivot reports the default origin.
        """
        cube = cmds.polyCube(name="cut_manip_moved", w=2, h=1, d=1)[0]
        cmds.move(5, 0, 0, cube)  # Cube center at world (5, 0, 0)
        cmds.select(cube)

        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="manip",
            delete=True, use_object_axes=True,
        )
        # The cut should be at the cube's center (world X=5), so deleting the
        # +X half leaves a cube spanning [4, 5] in X — not a slice through
        # world X=0 that would either be a no-op or destroy the whole cube.
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertAlmostEqual(bbox[3], 5.0, places=3,
            msg=f"Expected xmax≈5 (cube center), got {bbox[3]}")
        self.assertAlmostEqual(bbox[0], 4.0, places=3,
            msg=f"Expected xmin≈4, got {bbox[0]}")

    def test_all_six_axes_work(self):
        for axis in ("x", "-x", "y", "-y", "z", "-z"):
            with self.subTest(axis=axis):
                cube = cmds.polyCube(name=f"cut_{axis.replace('-', 'n')}")[0]
                before = cmds.polyEvaluate(cube, face=True)
                CutOnAxis.perform_cut_on_axis(
                    [cube], axis=axis, cuts=1, pivot="center", use_object_axes=True
                )
                after = cmds.polyEvaluate(cube, face=True)
                self.assertGreater(after, before, f"Cut along {axis} failed")

    def test_delete_removes_half(self):
        """A single center cut + delete on a unit cube should remove one half."""
        cube = cmds.polyCube(name="cut_del", w=2, h=2, d=2)[0]
        # 6 faces initially. Cut at center along X with delete=True:
        # the +X face is removed and the cap from the cut closes the body, so
        # final face count should be < initial.
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="center", delete=True, use_object_axes=True
        )
        bbox = cmds.exactWorldBoundingBox(cube)
        # +X half deleted, so the cube extent should be only on the -X side.
        self.assertLess(bbox[3], 0.01, f"Expected xmax≈0 after deleting +X, got {bbox[3]}")
        self.assertAlmostEqual(bbox[0], -1.0, places=3)

    def test_delete_negative_axis_removes_other_half(self):
        cube = cmds.polyCube(name="cut_del_neg", w=2, h=2, d=2)[0]
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="-x", cuts=1, pivot="center", delete=True, use_object_axes=True
        )
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertGreater(bbox[0], -0.01, f"Expected xmin≈0 after deleting -X, got {bbox[0]}")
        self.assertAlmostEqual(bbox[3], 1.0, places=3)

    def test_multi_cuts_evenly_spaced(self):
        cube = cmds.polyCube(name="cut_multi", w=4, h=1, d=1)[0]
        before = cmds.polyEvaluate(cube, face=True)
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=3, pivot="center", use_object_axes=True
        )
        after = cmds.polyEvaluate(cube, face=True)
        # 3 cuts split each of the +X and -X faces into 4 strips: net +6 faces.
        self.assertGreater(after, before + 4, "3 cuts should add several faces")

    def test_offset_shifts_cut(self):
        """Offset along positive axis should push the cut toward +X."""
        cube_a = cmds.polyCube(name="cut_off_a", w=2, h=1, d=1)[0]
        cube_b = cmds.polyCube(name="cut_off_b", w=2, h=1, d=1)[0]
        # Cut+delete with no offset
        CutOnAxis.perform_cut_on_axis(
            [cube_a], axis="x", cuts=1, pivot="center",
            cut_offset=0.0, delete=True, use_object_axes=True,
        )
        # Cut+delete with positive offset
        CutOnAxis.perform_cut_on_axis(
            [cube_b], axis="x", cuts=1, pivot="center",
            cut_offset=0.3, delete=True, use_object_axes=True,
        )
        bbox_a = cmds.exactWorldBoundingBox(cube_a)
        bbox_b = cmds.exactWorldBoundingBox(cube_b)
        # b's +X side should be offset further from -X (i.e., wider remaining half).
        self.assertGreater(bbox_b[3], bbox_a[3])

    def test_rotated_cube_object_axis_cut(self):
        """Cut along rotated object's local X axis — should bisect along its
        own X (which points to world -Z), not world X.
        """
        cube = cmds.polyCube(name="cut_rotated", w=2, h=1, d=1)[0]
        cmds.rotate(0, 90, 0, cube)  # Local +X now points along world -Z

        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="object",
            delete=True, use_object_axes=True,
        )
        # After deleting the +local-X half (which is world -Z), the remaining
        # half should sit on the +world-Z side (zmax > 0, zmin ≈ 0).
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertGreater(bbox[5], 0.5,
            f"Expected +world-Z half to remain, got zmax={bbox[5]}")
        self.assertGreater(bbox[2], -0.01,
            f"Expected zmin≈0, got {bbox[2]}")

    def test_rotated_cube_world_axis_cut(self):
        """With use_object_axes=False, cut should follow world axis even on
        a rotated object.
        """
        cube = cmds.polyCube(name="cut_rotated_world", w=2, h=1, d=1)[0]
        cmds.rotate(0, 90, 0, cube)

        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="world",
            delete=True, use_object_axes=False,
        )
        # World X cut at world origin removes everything with X > 0. After 90°
        # Y rotation, the cube spans X in [-0.5, 0.5] (since local Z=±0.5
        # rotates to world X=∓0.5). Deleting +world-X removes the world-+X
        # half, leaving xmax ≈ 0.
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertLess(bbox[3], 0.01,
            f"Expected xmax≈0 after world-X delete, got {bbox[3]}")

    def test_world_pivot(self):
        """Cube offset from origin, cut at world origin should slice off only
        the half that crosses world X=0.
        """
        cube = cmds.polyCube(name="cut_world", w=2, h=1, d=1)[0]
        cmds.move(0.3, 0, 0, cube)  # Cube spans X in [-0.7, 1.3]
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="world",
            delete=True, use_object_axes=False,
        )
        # +X half (relative to world origin) deleted: keep [-0.7, 0]
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertLess(bbox[3], 0.01)
        self.assertAlmostEqual(bbox[0], -0.7, places=3)

    def test_spacing_controls_span(self):
        """An explicit spacing fixes the span between cuts, independent of the
        object size. For 2 cuts centered on the pivot the outermost cut sits at
        +spacing/2, so deleting the +X half leaves xmax≈spacing/2.
        """
        for spacing, expected_xmax in ((4.0, 2.0), (6.0, 3.0)):
            with self.subTest(spacing=spacing):
                cube = cmds.polyCube(
                    name=f"cut_space_{int(spacing)}", w=10, h=1, d=1
                )[0]  # spans X in [-5, 5], center pivot at 0
                CutOnAxis.perform_cut_on_axis(
                    [cube], axis="x", cuts=2, pivot="center",
                    cut_spacing=spacing, delete=True, use_object_axes=False,
                )
                bbox = cmds.exactWorldBoundingBox(cube)
                self.assertAlmostEqual(
                    bbox[3], expected_xmax, places=3,
                    msg=f"spacing={spacing} expected xmax≈{expected_xmax}, got {bbox[3]}",
                )

    def test_distribution_runs_and_cuts(self):
        """Every interpolation mode should produce cuts without raising."""
        for mode in ("linear", "ease_in", "ease_out", "weighted", "smooth_step"):
            with self.subTest(mode=mode):
                cube = cmds.polyCube(name=f"cut_dist_{mode}", w=6, h=1, d=1)[0]
                before = cmds.polyEvaluate(cube, face=True)
                CutOnAxis.perform_cut_on_axis(
                    [cube], axis="x", cuts=3, pivot="center",
                    distribution=mode, weight_curve=3.0, use_object_axes=False,
                )
                after = cmds.polyEvaluate(cube, face=True)
                self.assertGreater(after, before, f"{mode} produced no cuts")


class TestCutOffsets(unittest.TestCase):
    """EditUtils._cut_offsets — pure cut-distribution math (no scene)."""

    def test_linear_is_even_and_centered(self):
        # 3 cuts across a span of 5 -> -2.5, 0, 2.5 (symmetric, even gaps).
        off = EditUtils._cut_offsets(3, 5.0, "linear")
        self.assertEqual([round(x, 4) for x in off], [-2.5, 0.0, 2.5])

    def test_legacy_even_fill_positions_preserved(self):
        # Auto span L*(n-1)/(n+1) must reproduce the historical cut_spacing
        # (L/(n+1)) placement: cube length 10, 3 cuts -> gap 2.5.
        length, n = 10.0, 3
        span = length * (n - 1) / (n + 1)
        off = EditUtils._cut_offsets(n, span, "linear")
        self.assertEqual([round(x, 4) for x in off], [-2.5, 0.0, 2.5])

    def test_spacing_sets_gap(self):
        # 2 cuts, span = spacing*(n-1) = 4 -> -2, 2 (gap of 4).
        off = EditUtils._cut_offsets(2, 4.0, "linear")
        self.assertEqual([round(x, 4) for x in off], [-2.0, 2.0])

    def test_single_cut_sits_on_pivot(self):
        self.assertEqual(EditUtils._cut_offsets(1, 999.0, "weighted"), [0.0])

    def test_zero_amount_is_empty(self):
        self.assertEqual(EditUtils._cut_offsets(0, 5.0), [])

    def test_nonlinear_keeps_endpoints_biases_interior(self):
        # ease_out keeps the endpoints at ±span/2 but pushes the middle cut off
        # center toward the +end (density biased toward the pivot side).
        off = EditUtils._cut_offsets(3, 10.0, "ease_out", weight_curve=3.0)
        self.assertAlmostEqual(off[0], -5.0, places=4)
        self.assertAlmostEqual(off[-1], 5.0, places=4)
        self.assertGreater(off[1], 0.0, "interior cut should shift toward +end")
        self.assertNotAlmostEqual(off[1], 0.0, places=4)


class TestPivotRetrieval(MayaTkTestCase):
    """XformUtils.get_operation_axis_pos — 'object' vs 'manip' retrieval.

    Guards the report that the pivot helper 'returns the manip pivot for the
    object pivot'. Verified in a live GUI Maya (see test/temp_tests probes):
    the resolver honors each pivot type. Headless, manipPivot reports the
    default origin, so 'manip' deliberately falls back to the rotate pivot —
    'object' must always return the rotate pivot itself.
    """

    def test_object_returns_rotate_pivot(self):
        cube = cmds.polyCube(name="piv_obj", w=2, h=2, d=2)[0]
        cmds.move(5, 0, 0, cube)
        rp = cmds.xform(cube, q=True, ws=True, rp=True)
        obj = XformUtils.get_operation_axis_pos(cube, "object")
        self.assertEqual([round(v, 4) for v in obj], [round(v, 4) for v in rp])
        self.assertAlmostEqual(obj[0], 5.0, places=4)

    def test_object_follows_edited_rotate_pivot(self):
        cube = cmds.polyCube(name="piv_edit", w=2, h=2, d=2)[0]
        cmds.xform(cube, piv=(3, 0, 0), ws=True)  # move only the pivot
        obj = XformUtils.get_operation_axis_pos(cube, "object")
        self.assertAlmostEqual(obj[0], 3.0, places=4)
        # center is the bbox center (still at origin) — distinct from object.
        center = XformUtils.get_operation_axis_pos(cube, "center")
        self.assertAlmostEqual(center[0], 0.0, places=4)

    def test_manip_falls_back_to_rotate_pivot_when_uncustomized(self):
        # Headless: no custom manip -> manip must equal the object pivot, not
        # the world origin (regression: cuts were happening at world 0).
        cube = cmds.polyCube(name="piv_manip", w=2, h=2, d=2)[0]
        cmds.move(5, 0, 0, cube)
        cmds.select(cube, replace=True)
        obj = XformUtils.get_operation_axis_pos(cube, "object")
        manip = XformUtils.get_operation_axis_pos(cube, "manip")
        self.assertEqual(
            [round(v, 4) for v in manip], [round(v, 4) for v in obj]
        )

    @skipIfBatch("manipPivot override is GUI-only")
    def test_custom_manip_diverges_from_object(self):
        # GUI pass: a dragged custom manip pivot must NOT leak into 'object'.
        cube = cmds.polyCube(name="piv_custom", w=2, h=2, d=2)[0]
        cmds.move(5, 0, 0, cube)
        cmds.select(cube, replace=True)
        cmds.setToolTo("moveSuperContext")
        cmds.manipPivot(p=(2.0, 0.0, 0.0))
        obj = XformUtils.get_operation_axis_pos(cube, "object")
        manip = XformUtils.get_operation_axis_pos(cube, "manip")
        self.assertAlmostEqual(obj[0], 5.0, places=3, msg="object must stay at rp")
        self.assertAlmostEqual(manip[0], 2.0, places=3, msg="manip must read custom")


class _MockSignal:
    """Minimal Qt-signal stand-in so Preview can be driven Qt-free."""

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


class _CutPreviewOp:
    """Stand-in for CutOnAxisSlots' preview contract: holds mutable params
    (like the UI widgets would) and forwards to CutOnAxis.perform_cut_on_axis.

    Mirrors the real slots class' PRESERVE_GEOMETRY opt-in so the Preview
    contract snapshots geometry for in-place-mutation rollback.
    """

    PRESERVE_GEOMETRY = True

    def __init__(self, **params):
        self.params = dict(
            axis="-x", pivot="object", cuts=1, cut_offset=0,
            delete=False, mirror=False, use_object_axes=True,
        )
        self.params.update(params)

    def perform_operation(self, objects, contract):
        CutOnAxis.perform_cut_on_axis(objects, **self.params)


class TestCutOnAxisPreviewRollback(MayaTkTestCase):
    """Regression: the Cut-on-Axis preview must roll back the previous cut
    before producing a new one when a value changes, even on meshes with no
    upstream construction history (frozen / imported).

    Bug: polyCut(ch=True) on a historyless mesh creates an intermediate
    orig-shape that holds the only pristine copy. The hermetic preview's
    node-diff rollback deleted that orig-shape along with the polyCut node,
    which BAKED the cut into the visible mesh instead of reverting it. Each
    value change therefore stacked another cut ("creating multiple cuts
    instead of undoing and creating a new cut"). Verified in Maya before fix.
    """

    @staticmethod
    def _counts(node):
        return (
            cmds.polyEvaluate(node, vertex=True),
            cmds.polyEvaluate(node, edge=True),
            cmds.polyEvaluate(node, face=True),
        )

    def _make_preview(self, op):
        chk, btn = _MockWidget(), _MockWidget()
        pv = Preview(op, chk, btn, message_func=lambda *a: None)
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

    def _historyless_cube(self, name="cut_preview"):
        cube = cmds.polyCube(name=name)[0]
        cmds.delete(cube, constructionHistory=True)  # drop upstream history
        return cube

    def _clean_cut_counts(self, cuts, name):
        """Counts from a single fresh preview-enable of `cuts` on a
        historyless cube (the reference a refresh sequence must match)."""
        ref = self._historyless_cube(name)
        pv = self._make_preview(_CutPreviewOp(cuts=cuts))
        cmds.select(ref)
        pv.enable()
        result = self._counts(ref)
        pv.disable()
        return result

    def test_slots_class_opts_into_geometry_preservation(self):
        """CutOnAxisSlots must declare PRESERVE_GEOMETRY so the preview
        snapshots geometry for robust rollback."""
        self.assertTrue(
            getattr(CutOnAxisSlots, "PRESERVE_GEOMETRY", False),
            "CutOnAxisSlots must set PRESERVE_GEOMETRY = True",
        )

    def test_refresh_does_not_accumulate_on_historyless_mesh(self):
        cube = self._historyless_cube()
        original = self._counts(cube)

        # Reference: a clean 2-cut preview on a fresh historyless cube.
        clean_two_cut = self._clean_cut_counts(2, "cut_preview_ref")

        # Live tool: enable with 1 cut, then "change the value" -> refresh
        # with 2 cuts. The 2-cut result must match the clean reference,
        # i.e. the 1-cut preview was rolled back rather than stacked.
        op = _CutPreviewOp(cuts=1)
        pv = self._make_preview(op)
        cmds.select(cube)
        pv.enable()
        after_one = self._counts(cube)
        self.assertNotEqual(after_one, original, "1-cut preview did nothing")

        op.params["cuts"] = 2
        pv.refresh()
        after_two = self._counts(cube)

        self.assertEqual(
            after_two, clean_two_cut,
            f"Cuts accumulated across refresh: got {after_two}, "
            f"expected a clean 2-cut {clean_two_cut}",
        )

        # Disabling the preview must restore the mesh to its original state.
        pv.disable()
        self.assertEqual(
            self._counts(cube), original,
            "Disabling preview did not restore the original mesh",
        )

    def test_repeated_refresh_does_not_leak_geometry(self):
        """Many value changes in a row must not stack cuts or leave stray
        intermediate shapes on a historyless mesh."""
        cube = self._historyless_cube("cut_preview_repeat")
        original = self._counts(cube)
        shapes_before = len(cmds.ls(type="mesh") or [])

        op = _CutPreviewOp(cuts=1)
        pv = self._make_preview(op)
        cmds.select(cube)
        pv.enable()

        for n in (2, 3, 4, 1, 5):
            op.params["cuts"] = n
            pv.refresh()

        # Final preview is 5 cuts; compare against a clean 5-cut reference.
        clean_five = self._clean_cut_counts(5, "cut_preview_repeat_ref")

        self.assertEqual(
            self._counts(cube), clean_five,
            "Repeated refresh accumulated geometry instead of replacing it",
        )

        pv.disable()
        self.assertEqual(self._counts(cube), original)
        # No leaked intermediate shapes under the restored cube.
        self.assertEqual(
            cmds.listRelatives(cube, shapes=True, type="mesh") or [],
            cmds.listRelatives(cube, shapes=True, type="mesh", noIntermediate=True) or [],
            "Rollback left a stray intermediate shape on the mesh",
        )

    def test_with_history_mesh_keeps_construction_history(self):
        """On a mesh WITH upstream history, node-diff already reverts the cut,
        so the in-place restore must be SKIPPED — otherwise rollback would
        strip the user's legitimate construction history. Guards the
        signature-divergence shortcut against false positives."""
        cube = cmds.polyCube(name="cut_with_hist")[0]  # keeps polyCube history

        def poly_creators():
            return [
                h for h in (cmds.listHistory(cube, pruneDagObjects=True) or [])
                if cmds.nodeType(h) == "polyCube"
            ]

        original = self._counts(cube)
        self.assertTrue(poly_creators(), "fixture should have polyCube history")

        op = _CutPreviewOp(cuts=1)
        pv = self._make_preview(op)
        cmds.select(cube)
        pv.enable()
        op.params["cuts"] = 2
        pv.refresh()
        pv.disable()

        self.assertEqual(self._counts(cube), original, "geometry not reverted")
        self.assertTrue(
            poly_creators(),
            "Rollback stripped the mesh's construction history (false-positive "
            "divergence baked the mesh instead of skipping the restore)",
        )


class _BevelPreviewOp:
    """Stand-in for BevelSlots' preview contract: holds the mutable width/
    segments params (like the UI spinboxes) and forwards to Bevel.bevel.

    Mirrors the real slots class' PRESERVE_GEOMETRY opt-in so the Preview
    contract snapshots geometry for in-place-mutation rollback.
    """

    PRESERVE_GEOMETRY = True

    def __init__(self, **params):
        self.params = dict(width=0.2, segments=1)
        self.params.update(params)

    def perform_operation(self, objects, contract):
        Bevel.bevel(objects, **self.params)


class TestBevelPreviewRollback(MayaTkTestCase):
    """Regression: the Bevel preview must roll back the previous bevel before
    producing a new one on a value change, restoring the mesh topology AND its
    material.

    Bug (exposed once the panel stopped crashing on open): polyBevel3 mutates
    the mesh in place with construction history. Without a geometry snapshot the
    node-diff rollback baked the bevel in and dropped the material, so each
    value change stacked another bevel; and because beveling renumbers edges,
    the captured edge index (e[0]) pointed at a *different* physical edge on the
    next refresh. Fixed by BevelSlots.PRESERVE_GEOMETRY = True (mirrors Bridge /
    Cut On Axis).
    """

    @staticmethod
    def _counts(node):
        return (
            cmds.polyEvaluate(node, vertex=True),
            cmds.polyEvaluate(node, edge=True),
            cmds.polyEvaluate(node, face=True),
        )

    @staticmethod
    def _shading_engines(node):
        shape = cmds.listRelatives(node, shapes=True, noIntermediate=True)[0]
        return set(cmds.listConnections(shape, type="shadingEngine") or [])

    @staticmethod
    def _green_face_count(node):
        """Number of faces not assigned to any shading group — Maya renders
        these bright green (the 'lost material' symptom)."""
        shape = cmds.listRelatives(node, shapes=True, noIntermediate=True)[0]
        total = cmds.polyEvaluate(shape, face=True)
        owners = set(cmds.ls(node, long=True) or []) | set(cmds.ls(shape, long=True) or [])
        covered = set()
        for sg in cmds.ls(type="shadingEngine"):
            for m in cmds.ls(cmds.sets(sg, q=True) or [], long=True, flatten=True) or []:
                if m.split(".f[")[0] in owners:
                    if ".f[" in m:
                        covered.add(int(m.split(".f[")[1].rstrip("]")))
                    else:
                        covered.update(range(total))
        return total - len(covered)

    @staticmethod
    def _e0_midpoint(node):
        """World midpoint of edge 0 — identifies *which physical edge* e[0] is.
        Cube counts are symmetric, so this is what catches a baked rollback that
        renumbered the edges (the "bevels a different edge" symptom)."""
        vtx = cmds.polyListComponentConversion(
            f"{node}.e[0]", fromEdge=True, toVertex=True
        )
        pts = cmds.xform(vtx, q=True, ws=True, t=True)
        n = len(pts) // 3
        return tuple(round(sum(pts[i::3]) / n, 4) for i in range(3))

    def _make_preview(self, op):
        chk, btn = _MockWidget(), _MockWidget()
        pv = Preview(op, chk, btn, message_func=lambda *a: None)
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

    def _assign_material(self, node, name="bvlMat"):
        shader = cmds.shadingNode("lambert", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(node, edit=True, forceElement=sg)
        return sg

    @staticmethod
    def _historyless_cube(name):
        """A cube with its upstream construction history dropped (as a frozen /
        imported / combined production mesh would be). This is the condition
        that exposes the rollback bug: polyBevel3's auto-created orig-shape
        holds the only pristine copy, so node-diff rollback bakes the bevel in."""
        cube = cmds.polyCube(name=name)[0]
        cmds.delete(cube, constructionHistory=True)
        return cube

    def _clean_bevel_counts(self, width, name):
        """Counts from a single fresh preview-enable of a `width` bevel on
        e[0] of a fresh historyless cube — the reference a refresh must match."""
        ref = self._historyless_cube(name)
        pv = self._make_preview(_BevelPreviewOp(width=width))
        cmds.select(f"{ref}.e[0]")
        pv.enable()
        result = self._counts(ref)
        pv.disable()
        return result

    def test_slots_class_opts_into_geometry_preservation(self):
        """BevelSlots must declare PRESERVE_GEOMETRY so the preview snapshots
        geometry for robust rollback."""
        self.assertTrue(
            getattr(BevelSlots, "PRESERVE_GEOMETRY", False),
            "BevelSlots must set PRESERVE_GEOMETRY = True",
        )

    def test_refresh_reverts_topology_and_preserves_material(self):
        cube = self._historyless_cube("bvl_preview")
        sg = self._assign_material(cube)
        original = self._counts(cube)
        original_e0 = self._e0_midpoint(cube)

        # Reference: a clean single 0.4 bevel of e[0] on a fresh cube.
        clean = self._clean_bevel_counts(0.4, "bvl_ref")

        # Live tool: enable at 0.2, then "change the value" -> refresh at 0.4.
        # The result must match the clean reference, i.e. the 0.2 bevel was
        # rolled back (topology + edge numbering restored) rather than stacked
        # and re-beveled on a shifted edge.
        op = _BevelPreviewOp(width=0.2)
        pv = self._make_preview(op)
        cmds.select(f"{cube}.e[0]")
        pv.enable()
        self.assertNotEqual(self._counts(cube), original, "0.2 bevel did nothing")

        op.params["width"] = 0.4
        pv.refresh()

        self.assertEqual(
            self._counts(cube), clean,
            f"Bevel accumulated / re-beveled a shifted edge across refresh: "
            f"got {self._counts(cube)}, expected clean {clean}",
        )
        # Material must survive the in-place rollback.
        self.assertIn(
            sg, self._shading_engines(cube),
            "Bevel preview lost the mesh material on rollback",
        )

        # Disabling restores the original mesh topology (including edge
        # numbering, so e[0] is the same physical edge) and its material.
        pv.disable()
        self.assertEqual(self._counts(cube), original, "disable did not restore mesh")
        self.assertEqual(
            self._e0_midpoint(cube), original_e0,
            "rollback renumbered edges — e[0] moved, so a refresh would bevel a "
            "different edge",
        )
        self.assertIn(sg, self._shading_engines(cube), "disable lost the material")

    def test_preview_preserves_multi_material_through_commit(self):
        """A multi-material (per-face) mesh must keep ALL its shading through the
        live preview, a value change, and the commit. The hermetic preview's
        in-place geometry rollback drops per-face (multi-material) shading, and
        it can't be restored in place -- reasserting per-face shading on the
        bare rebuilt mesh leaves malformed shading groups that the next poly op
        collapses (the whole mesh renders bright green, 'lost the material on
        the object'). Preview snapshots the shading at enable and reasserts it
        AFTER each clean op -- the forward preview op and the commit replay --
        where the assignment sticks; the dominant material base-coats so the
        bevel's new faces are shaded too."""
        cube = self._historyless_cube("bvl_multimat")
        sg_a = self._assign_material(cube, "bvlMatA")            # whole object
        sg_b = self._assign_material(f"{cube}.f[1]", "bvlMatB")  # one face -> 2nd mat
        self.assertEqual(self._green_face_count(cube), 0, "fixture should be fully shaded")

        op = _BevelPreviewOp(width=0.2)
        pv = self._make_preview(op)
        cmds.select(f"{cube}.e[0]")

        # Live preview (forward op on the shaded mesh) must not green out.
        pv.enable()
        self.assertEqual(
            self._green_face_count(cube), 0, "live preview greened a multi-material mesh"
        )

        # Value change -> rollback (in-place restore) + re-preview. The rollback
        # is where the shading was being dropped; the restored mesh must stay
        # fully shaded for the new preview to display correctly.
        op.params["width"] = 0.4
        pv.refresh()
        self.assertEqual(
            self._green_face_count(cube), 0, "rollback dropped per-face shading on refresh"
        )

        pv.finalize_changes()  # commit

        sgs = set(self._shading_engines(cube))
        self.assertIn(sg_a, sgs, "committed mesh lost the primary material")
        self.assertIn(sg_b, sgs, "committed mesh lost the per-face (second) material")
        self.assertEqual(
            self._green_face_count(cube), 0,
            "committed mesh has unshaded (bright green) faces",
        )


class TestMirrorResolvePivot(QuickTestCase):
    """MirrorSlots._resolve_pivot is a static helper — pure Python."""

    def test_index_to_label_mapping(self):
        self.assertEqual(MirrorSlots._resolve_pivot(0, "x"), "manip")
        self.assertEqual(MirrorSlots._resolve_pivot(1, "x"), "object")
        self.assertEqual(MirrorSlots._resolve_pivot(2, "x"), "world")
        self.assertEqual(MirrorSlots._resolve_pivot(3, "x"), "center")

    def test_axis_aware_index_4(self):
        # Border pivot: +axis -> max face, -axis -> min face. The sign FLIPS the
        # side the geometry doubles toward (was always xmax — the '-' was a no-op).
        self.assertEqual(MirrorSlots._resolve_pivot(4, "x"), "xmax")
        self.assertEqual(MirrorSlots._resolve_pivot(4, "-x"), "xmin")
        self.assertEqual(MirrorSlots._resolve_pivot(4, "y"), "ymax")
        self.assertEqual(MirrorSlots._resolve_pivot(4, "-y"), "ymin")
        self.assertEqual(MirrorSlots._resolve_pivot(4, "z"), "zmax")
        self.assertEqual(MirrorSlots._resolve_pivot(4, "-z"), "zmin")

    def test_index_4_unknown_axis_falls_back(self):
        self.assertEqual(MirrorSlots._resolve_pivot(4, "bogus"), "xmax")

    def test_unknown_index_defaults_manip(self):
        self.assertEqual(MirrorSlots._resolve_pivot(99, "x"), "manip")

    def test_axis_sign_relevant_only_for_bbox_pivots(self):
        # The '-' toggle is enabled only for the bounding-box pivots (Center 3,
        # Border 4); Manip/Object/World reflect across a fixed plane (no-op sign).
        self.assertFalse(MirrorSlots._axis_sign_relevant(0))  # manip
        self.assertFalse(MirrorSlots._axis_sign_relevant(1))  # object
        self.assertFalse(MirrorSlots._axis_sign_relevant(2))  # world
        self.assertTrue(MirrorSlots._axis_sign_relevant(3))  # bbox center
        self.assertTrue(MirrorSlots._axis_sign_relevant(4))  # bbox border


class TestEditUtilsMirror(MayaTkTestCase):
    """EditUtils.mirror — the actual mirror operation (not just _resolve_pivot).

    The audit flagged mirror as having only static-helper coverage;
    these tests exercise the real polyMirrorFace dispatch path.

    NOTE: polyMirrorFace with mergeMode=-1 (separate) reorganizes the DAG —
    the original transform may be renamed or replaced. Tests verify
    aggregate scene state (mesh count, vertex sums) rather than naming.
    """

    def _mesh_count(self):
        return len(cmds.ls(type="mesh", noIntermediate=True) or [])

    def _total_vertices(self):
        meshes = cmds.ls(type="mesh", noIntermediate=True) or []
        return sum(cmds.polyEvaluate(m, vertex=True) for m in meshes)

    def test_mirror_creates_additional_geometry(self):
        """A simple cube mirrored at world should add vertices."""
        cube = cmds.polyCube(name="mirror_x_cube")[0]
        cmds.move(2, 0, 0, cube)
        before_verts = self._total_vertices()

        EditUtils.mirror([cube], axis="x", pivot="world", mergeMode=-1)

        after_verts = self._total_vertices()
        self.assertGreater(after_verts, before_verts)

    def test_mirror_invalid_axis_raises(self):
        cube = cmds.polyCube(name="mirror_bad")[0]
        with self.assertRaises(ValueError):
            EditUtils.mirror([cube], axis="w")  # not in {x,-x,y,-y,z,-z}

    def test_mirror_all_six_axes_accepted(self):
        """Each documented axis literal should work without raising."""
        for axis in ("x", "-x", "y", "-y", "z", "-z"):
            cube = cmds.polyCube(name=f"mirror_axis_{axis.replace('-', 'n')}")[0]
            cmds.move(1, 1, 1, cube)
            # Should not raise — that's the contract under test
            EditUtils.mirror([cube], axis=axis, pivot="world")

    def test_mirror_with_tuple_pivot(self):
        """A literal (x, y, z) pivot tuple should be honored without error."""
        cube = cmds.polyCube(name="mirror_tup_piv")[0]
        cmds.move(5, 0, 0, cube)
        before_meshes = self._mesh_count()

        EditUtils.mirror([cube], axis="x", pivot=(0, 0, 0))

        # Mirror produces a result — mesh count should be at least preserved
        self.assertGreaterEqual(self._mesh_count(), before_meshes)

    def test_mirror_multiple_objects_each_processed(self):
        """Passing multiple objects mirrors each one — total vertex count grows."""
        cube_a = cmds.polyCube(name="mirror_multi_a")[0]
        cube_b = cmds.polyCube(name="mirror_multi_b")[0]
        cmds.move(3, 0, 0, cube_a)
        cmds.move(-3, 0, 0, cube_b)
        before_verts = self._total_vertices()

        EditUtils.mirror([cube_a, cube_b], axis="x", pivot="world")

        # Both should have been mirrored — vertex count should increase
        # significantly (not just one cube's worth).
        self.assertGreater(self._total_vertices(), before_verts)

    def test_border_pivot_sign_flips_side(self):
        """Border pivot: the axis sign must reflect to opposite sides.

        Regression: _resolve_pivot used to map both 'x' and '-x' to 'xmax', so
        the '-' toggle was a no-op for the bounding-box border pivot. With the
        fix, +X doubles toward +X (across the max face) and -X toward -X (min
        face), via the same _resolve_pivot the slot uses.
        """
        cube_pos = cmds.polyCube(name="border_pos")[0]
        cmds.move(2, 0, 0, cube_pos)  # x in [1, 3]
        EditUtils.mirror(
            [cube_pos], axis="x", pivot=MirrorSlots._resolve_pivot(4, "x"), mergeMode=1
        )
        pos_bb = cmds.exactWorldBoundingBox(cube_pos)

        cube_neg = cmds.polyCube(name="border_neg")[0]
        cmds.move(2, 0, 0, cube_neg)  # x in [1, 3]
        EditUtils.mirror(
            [cube_neg], axis="-x", pivot=MirrorSlots._resolve_pivot(4, "-x"), mergeMode=1
        )
        neg_bb = cmds.exactWorldBoundingBox(cube_neg)

        # +X reflects across xmax -> reaches farther in +X; -X across xmin ->
        # farther in -X. Distinct footprints prove the sign is honored.
        self.assertGreater(pos_bb[3], neg_bb[3])
        self.assertLess(neg_bb[0], pos_bb[0])

    def test_center_symmetrize_sign_convention(self):
        """Pin the cut_along_axis convention the center symmetrize relies on.

        MirrorSlots routes the 'Bounding Box (center)' pivot through
        cut_along_axis(delete=True, mirror=True) and INVERTS the UI sign because
        cut_along_axis's 'x' keeps the -X half while '-x' keeps the +X half. If
        that convention ever changes, this fails — update the inversion in
        MirrorSlots.perform_operation to match.
        """

        def tall_plus_x(name):
            t = cmds.polyCube(w=4, h=2, d=2, name=name)[0]
            cmds.move(2, 0, 0, t)  # x in [0, 4], center x=2
            for v in cmds.ls(f"{t}.vtx[*]", flatten=True):
                p = cmds.pointPosition(v, world=True)
                if p[0] > 3.5 and p[1] > 0:  # +X face, top corners
                    cmds.move(0, 6, 0, v, relative=True, worldSpace=True)
            return t

        # The cut at center x=2 crosses the sloped top edge at y=4, so the short
        # (-X) half tops out at y~4 and the tall (+X) half at y~7 — distinct
        # halves, threshold at the midpoint (5.5).
        a = tall_plus_x("sym_x")
        EditUtils.cut_along_axis(
            a, axis="x", pivot="center", amount=1, delete=True, mirror=True
        )
        # 'x' keeps the short -X half -> tall corners discarded -> low y-max.
        self.assertLess(cmds.exactWorldBoundingBox(a)[4], 5.5)

        b = tall_plus_x("sym_negx")
        EditUtils.cut_along_axis(
            b, axis="-x", pivot="center", amount=1, delete=True, mirror=True
        )
        # '-x' keeps the tall +X half -> tall corners survive -> high y-max.
        self.assertGreater(cmds.exactWorldBoundingBox(b)[4], 5.5)


class TestMirrorPivotFidelity(MayaTkTestCase):
    """The mirror plane must pass through the pivot the user actually sees.

    Regression: ``_mirror_pivot_point`` special-cased the object-frame pivots
    to the object's LOCAL ORIGIN (``MPoint(0,0,0) * worldMatrix``) instead of
    its rotate pivot. On a fresh cube the two coincide, so the bug was
    invisible; the moment the pivot was dragged — or the object was frozen,
    which leaves the rotate pivot behind in world space while the local origin
    snaps to the world origin — the mirror plane jumped somewhere else.

    The plane is inferred from the post-mirror world bounding box: the union
    of a point set and its reflection is symmetric about the mirror plane.
    """

    @staticmethod
    def _plane(obj, axis_index=0):
        bb = cmds.exactWorldBoundingBox(obj)
        return (bb[axis_index] + bb[axis_index + 3]) / 2.0

    def test_object_pivot_follows_dragged_pivot(self):
        """A dragged pivot must move the mirror plane with it."""
        cube = cmds.polyCube(name="piv_dragged", w=1, h=1, d=1)[0]
        cmds.xform(cube, ws=True, t=(5, 0, 0))
        cmds.xform(cube, ws=True, piv=(7, 0, 0))

        EditUtils.mirror(cube, axis="x", pivot="object", mergeMode=0)

        self.assertAlmostEqual(self._plane(cube), 7.0, places=4)

    def test_object_pivot_on_frozen_object(self):
        """Freezing keeps the pivot in world space — the mirror must follow it.

        After ``makeIdentity`` the transform is identity, so the local origin
        is the world origin while the pivot stays out at x=5. Mirroring about
        "the object" must use x=5, not x=0.
        """
        cube = cmds.polyCube(name="piv_frozen", w=1, h=1, d=1)[0]
        cmds.xform(cube, ws=True, t=(5, 0, 0))
        cmds.makeIdentity(cube, apply=True, t=1, r=1, s=1)
        self.assertAlmostEqual(cmds.xform(cube, q=True, ws=True, rp=True)[0], 5.0)

        EditUtils.mirror(cube, axis="x", pivot="object", mergeMode=0)

        self.assertAlmostEqual(self._plane(cube), 5.0, places=4)

    def test_object_pivot_matches_get_operation_axis_pos(self):
        """``object`` must resolve to the same point every other op uses.

        ``cut_along_axis`` / ``delete_along_axis`` / ``duplicate_radial`` all
        read the pivot through ``XformUtils.get_operation_axis_pos``; mirror
        diverging from it is what made the panel unpredictable.
        """
        cube = cmds.polyCube(name="piv_agree", w=1, h=1, d=1)[0]
        cmds.xform(cube, ws=True, t=(5, 2, -3))
        cmds.xform(cube, ws=True, piv=(7, 1, 4))

        for pivot in ("object", "manip", "baked", "original"):
            with self.subTest(pivot=pivot):
                expected = list(XformUtils.get_operation_axis_pos(cube, pivot))
                resolved = EditUtils._mirror_pivot_point(cube, pivot)
                for got, want in zip(resolved, expected):
                    self.assertAlmostEqual(got, want, places=4)

    def test_object_pivot_unaffected_by_use_object_axes_flag(self):
        """Both flag states must land the plane on the same pivot."""
        planes = []
        for i, uoa in enumerate((True, False)):
            cube = cmds.polyCube(name=f"piv_flag_{i}", w=1, h=1, d=1)[0]
            cmds.xform(cube, ws=True, t=(5, 0, 0))
            cmds.xform(cube, ws=True, piv=(7, 0, 0))
            EditUtils.mirror(
                cube,
                axis="x",
                pivot="object",
                mergeMode=0,
                use_object_axes=uoa,
            )
            planes.append(self._plane(cube))

        self.assertAlmostEqual(planes[0], planes[1], places=4)
        self.assertAlmostEqual(planes[0], 7.0, places=4)

    def test_mirror_instance_honors_dragged_pivot(self):
        """``mirror_instance`` shares the pivot resolution — and the bug."""
        cube = cmds.polyCube(name="piv_inst", w=1, h=1, d=1)[0]
        cmds.xform(cube, ws=True, t=(5, 0, 0))
        cmds.xform(cube, ws=True, piv=(7, 0, 0))

        instance = EditUtils.mirror_instance(cube, axis="x", pivot="object")[0]

        # Source spans 4.5..5.5; reflected about x=7 -> 8.5..9.5.
        bb = cmds.exactWorldBoundingBox(instance)
        self.assertAlmostEqual(bb[0], 8.5, places=4)
        self.assertAlmostEqual(bb[3], 9.5, places=4)


class TestMirrorObjectAxes(MayaTkTestCase):
    """``use_object_axes`` must actually tilt the mirror plane.

    Regression: the flag was accepted but never reached the plane — the mirror
    was always world-axis-aligned (``polyMirrorFace`` was hard-coded to
    ``worldSpace=True``, and the instance path built an axis-aligned reflection
    matrix), so a rotated object mirrored about the world instead of its own
    axis. ``cut_along_axis`` in the same module already documented and honored
    the object frame for exactly these pivots.
    """

    TOL = 1e-3

    @staticmethod
    def _points(obj):
        flat = cmds.xform(f"{obj}.vtx[*]", q=True, ws=True, t=True)
        return [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]

    @classmethod
    def _reflect(cls, p, normal, point):
        n = om.MVector(*normal).normal()
        v = om.MVector(p[0] - point[0], p[1] - point[1], p[2] - point[2])
        r = v - n * (2.0 * (v * n))
        return (point[0] + r[0], point[1] + r[1], point[2] + r[2])

    @classmethod
    def _near(cls, a, b):
        return max(abs(a[i] - b[i]) for i in range(3)) < cls.TOL

    @classmethod
    def _covered_by(cls, points, cloud):
        """Every point in *points* has a partner in *cloud* within TOL."""
        return all(any(cls._near(p, q) for q in cloud) for p in points)

    def _asymmetric_cube(self, name, rotation=(0, 45, 0), translation=(5, 0, 0)):
        """A cube whose local +X face is pushed out, so it is NOT self-symmetric
        about its own X plane — otherwise a mirror about the center is a no-op
        and can't distinguish the two frames."""
        cube = cmds.polyCube(name=name, w=1, h=2, d=4, ch=False)[0]
        for v in cmds.ls(f"{cube}.vtx[*]", flatten=True):
            if cmds.pointPosition(v, local=True)[0] > 0:
                cmds.move(1.5, 0, 0, v, relative=True, objectSpace=True)
        cmds.xform(cube, ws=True, t=translation)
        cmds.xform(cube, ro=rotation)
        return cube

    @staticmethod
    def _frame(obj):
        return om.MMatrix(cmds.xform(obj, q=True, m=True, ws=True))

    def test_rotated_object_mirrors_about_its_own_axis(self):
        cube = self._asymmetric_cube("oax_geo")
        m = self._frame(cube)
        origin = (m[12], m[13], m[14])
        local_x = (m[0], m[1], m[2])
        before = self._points(cube)
        want_object = [self._reflect(p, local_x, origin) for p in before]
        want_world = [self._reflect(p, (1, 0, 0), origin) for p in before]

        EditUtils.mirror(cube, axis="x", pivot="object", mergeMode=0)

        after = self._points(cube)
        self.assertTrue(
            self._covered_by(want_object, after),
            "mirrored geometry does not lie on the object's own X plane",
        )
        # The world-X images are a genuinely different cloud here (45 deg).
        self.assertFalse(
            self._covered_by(want_world, after),
            "mirror still landed on the WORLD X plane",
        )

    def test_object_axes_under_a_rotated_parent(self):
        """Parented geometry is the normal production case, and the risky one:
        the frame is the object's WORLD matrix (parent included), while
        polyMirrorFace's worldSpace=False operates in the node's own local
        space. If those two disagreed, the pivot conversion would be wrong for
        every grouped object — which is most of a real scene."""
        cube = self._asymmetric_cube(
            "oax_child", rotation=(0, 20, 0), translation=(2, 0, 0)
        )
        group = cmds.group(cube, name="oax_grp")
        cmds.xform(group, ro=(0, 35, 0), t=(1, 0, 3))
        cube = cmds.ls(cube, long=True)[0]

        m = self._frame(cube)
        origin = (m[12], m[13], m[14])
        local_x = (m[0], m[1], m[2])
        before = self._points(cube)
        want = [self._reflect(p, local_x, origin) for p in before]

        EditUtils.mirror(cube, axis="x", pivot="object", mergeMode=0)

        self.assertTrue(
            self._covered_by(want, self._points(cube)),
            "a parented object mirrored about the wrong plane — the local "
            "space polyMirrorFace uses disagrees with the world-matrix frame",
        )

    def test_use_object_axes_false_forces_world(self):
        cube = self._asymmetric_cube("oax_world")
        m = self._frame(cube)
        origin = (m[12], m[13], m[14])
        before = self._points(cube)
        want_world = [self._reflect(p, (1, 0, 0), origin) for p in before]

        EditUtils.mirror(
            cube, axis="x", pivot="object", mergeMode=0, use_object_axes=False
        )

        self.assertTrue(self._covered_by(want_world, self._points(cube)))

    def test_world_pivot_ignores_object_rotation(self):
        """"world" is not an object-frame pivot — it stays world-aligned."""
        cube = self._asymmetric_cube("oax_wpiv")
        before = self._points(cube)
        want_world = [self._reflect(p, (1, 0, 0), (0, 0, 0)) for p in before]

        EditUtils.mirror(cube, axis="x", pivot="world", mergeMode=0)

        self.assertTrue(self._covered_by(want_world, self._points(cube)))

    def test_unrotated_object_unchanged_by_the_frame_switch(self):
        """No regression for the common case: an axis-aligned object must give
        the same result through the object-space and world-space paths."""
        results = []
        for i, uoa in enumerate((True, False)):
            cube = self._asymmetric_cube(f"oax_same_{i}", rotation=(0, 0, 0))
            EditUtils.mirror(
                cube, axis="x", pivot="object", mergeMode=0, use_object_axes=uoa
            )
            results.append(sorted(cmds.exactWorldBoundingBox(cube)))
        for a, b in zip(*results):
            self.assertAlmostEqual(a, b, places=4)

    def test_mirror_instance_follows_object_axis(self):
        """The instance path must agree with the geometry path on the plane."""
        cube = self._asymmetric_cube("oax_inst")
        m = self._frame(cube)
        origin = (m[12], m[13], m[14])
        local_x = (m[0], m[1], m[2])
        want = [self._reflect(p, local_x, origin) for p in self._points(cube)]

        instance = EditUtils.mirror_instance(cube, axis="x", pivot="object")[0]

        self.assertTrue(
            self._covered_by(want, self._points(instance)),
            "instance was not reflected across the object's own X plane",
        )

    def test_mirror_instance_world_pivot_stays_world(self):
        cube = self._asymmetric_cube("oax_inst_w")
        want = [self._reflect(p, (1, 0, 0), (0, 0, 0)) for p in self._points(cube)]

        instance = EditUtils.mirror_instance(cube, axis="x", pivot="world")[0]

        self.assertTrue(self._covered_by(want, self._points(instance)))

    def test_separate_mode_works_in_the_object_frame(self):
        """mergeMode=-1 routes through polySeparate — exercise it in the tilted
        frame too, since that path rebuilds the DAG rather than one mesh."""
        cube = self._asymmetric_cube("oax_sep")
        m = self._frame(cube)
        origin = (m[12], m[13], m[14])
        local_x = (m[0], m[1], m[2])
        want = [self._reflect(p, local_x, origin) for p in self._points(cube)]

        EditUtils.mirror(cube, axis="x", pivot="object", mergeMode=-1)

        # The new half is a separate transform; find every mesh point in the scene.
        cloud = []
        for mesh in cmds.ls(type="mesh", noIntermediate=True) or []:
            transform = cmds.listRelatives(mesh, parent=True, fullPath=True)[0]
            cloud.extend(self._points(transform))
        self.assertTrue(
            self._covered_by(want, cloud),
            "separated half is not on the object's own X plane",
        )

    def test_original_pivot_falls_back_with_a_warning(self):
        """polyMirrorFace cannot express the pre-freeze frame — the geometry
        path must ANNOUNCE the fallback rather than silently mirroring about
        the wrong plane, and must still produce a mirror instead of raising."""
        cube = self._asymmetric_cube("oax_orig", rotation=(0, 35, 0))
        XformUtils.freeze_transforms(cube)  # stamps the pre-freeze frame
        self.assertIsNotNone(
            XformUtils.get_stored_transforms(cube),
            "freeze stamped no bake history — the fallback path is untested",
        )
        before = len(self._points(cube))

        with mock.patch.object(cmds, "warning") as warned:
            EditUtils.mirror(cube, axis="x", pivot="original", mergeMode=0)

        self.assertTrue(
            any("pre-freeze" in str(c) for c in warned.call_args_list),
            "the pre-freeze fallback was not announced",
        )
        self.assertGreater(
            len(self._points(cube)), before, "no geometry was mirrored at all"
        )

    def test_mirror_instance_honors_the_pre_freeze_frame(self):
        """The instance path conjugates the reflection with whatever frame it
        is handed, so unlike the geometry path it CAN mirror about the
        pre-freeze axes — the documented difference between the two."""
        cube = self._asymmetric_cube("oax_orig_inst", rotation=(0, 35, 0))
        pre_freeze = self._frame(cube)
        XformUtils.freeze_transforms(cube)
        self.assertFalse(
            self._frame(cube).isEquivalent(pre_freeze, 1e-4),
            "freezing left the live frame rotated — nothing to distinguish",
        )
        pivot = tuple(cmds.xform(cube, q=True, ws=True, rp=True))
        pre_freeze_x = (pre_freeze[0], pre_freeze[1], pre_freeze[2])
        want = [self._reflect(p, pre_freeze_x, pivot) for p in self._points(cube)]

        instance = EditUtils.mirror_instance(cube, axis="x", pivot="original")[0]

        self.assertTrue(
            self._covered_by(want, self._points(instance)),
            "instance was not reflected across the PRE-FREEZE X plane",
        )

    def test_object_axis_mirror_still_honors_the_dragged_pivot(self):
        """Frame and pivot must compose: tilted plane THROUGH the real pivot."""
        cube = self._asymmetric_cube("oax_piv")
        cmds.xform(cube, ws=True, piv=(7, 0, 2))
        m = self._frame(cube)
        world_pivot = tuple(cmds.xform(cube, q=True, ws=True, rp=True))
        local_x = (m[0], m[1], m[2])
        before = self._points(cube)
        want = [self._reflect(p, local_x, world_pivot) for p in before]

        EditUtils.mirror(cube, axis="x", pivot="object", mergeMode=0)

        self.assertTrue(self._covered_by(want, self._points(cube)))


if __name__ == "__main__":
    unittest.main()
