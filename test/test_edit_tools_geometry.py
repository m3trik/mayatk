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

import maya.cmds as cmds

from mayatk.edit_utils.bevel import Bevel, BevelSlots
from mayatk.edit_utils.bridge import Bridge
from mayatk.edit_utils.snap import Snap
from mayatk.edit_utils.cut_on_axis import CutOnAxis, CutOnAxisSlots
from mayatk.edit_utils.mirror import MirrorSlots
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.core_utils.preview import Preview

from base_test import MayaTkTestCase, QuickTestCase


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


if __name__ == "__main__":
    unittest.main()
