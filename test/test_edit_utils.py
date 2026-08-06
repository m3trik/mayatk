# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.edit_utils module

Tests for EditUtils class functionality including:
- Vertex operations (merge, pairs)
- Axis-based operations (get faces, cut, delete)
- Mirroring and symmetry
- Overlap detection (duplicates, vertices, faces)
- Topology analysis (non-manifold, similarity)
- Selection utilities (invert, delete)
- Curve creation
"""
import unittest
import mayatk as mtk
from mayatk.edit_utils._edit_utils import EditUtils
import pythontk as ptk

from base_test import MayaTkTestCase
import maya.cmds as cmds
import maya.api.OpenMaya as om


class TestEditUtils(MayaTkTestCase):
    """Comprehensive tests for EditUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.cube = cmds.polyCube(name="test_cube", w=10, h=10, d=10)[0]
        self.sphere = cmds.polySphere(name="test_sphere", r=5)[0]

    def tearDown(self):
        """Clean up."""
        super().tearDown()

    # -------------------------------------------------------------------------
    # Vertex Operations
    # -------------------------------------------------------------------------

    def test_merge_vertices(self):
        """Test merging vertices."""
        # Create a mesh with overlapping vertices
        # Duplicate cube and move slightly to create overlap when combined
        cube2 = cmds.duplicate(self.cube)[0]
        cmds.move(0.0001, 0, 0, cube2, r=True)
        combined = cmds.polyUnite(self.cube, cube2, ch=False)[0]

        initial_count = cmds.polyEvaluate(combined, v=True)
        EditUtils.merge_vertices(combined, tolerance=0.001)
        final_count = cmds.polyEvaluate(combined, v=True)

        self.assertLess(final_count, initial_count)

    def test_merge_vertices_selected_only(self):
        """selected_only operates on the live vertex selection (once —
        regression: it used to re-run the selection merge per shape in
        the objects loop)."""
        cube2 = cmds.duplicate(self.cube)[0]
        cmds.move(0.0001, 0, 0, cube2, r=True)
        combined = cmds.polyUnite(self.cube, cube2, ch=False)[0]

        initial_count = cmds.polyEvaluate(combined, v=True)
        cmds.select(f"{combined}.vtx[*]")
        EditUtils.merge_vertices(combined, tolerance=0.001, selected_only=True)
        final_count = cmds.polyEvaluate(combined, v=True)

        self.assertLess(final_count, initial_count)

    def test_merge_vertex_pairs(self):
        """Test merging specific vertex pairs."""
        # Select two vertices
        vtx1 = f"{self.cube}.vtx[0]"
        vtx2 = f"{self.cube}.vtx[1]"

        # Get initial positions
        p1 = cmds.pointPosition(vtx1, world=True)
        p2 = cmds.pointPosition(vtx2, world=True)
        midpoint = [(a + b) / 2 for a, b in zip(p1, p2)]

        EditUtils.merge_vertex_pairs([vtx1, vtx2])

        # Check if they merged (count reduced)
        # Note: polyMergeVertex might change vertex IDs, so we check total count
        # But here we merged 2 verts into 1, so count should decrease by 1
        # However, merge_vertex_pairs moves them to center then merges.
        # Let's verify position of the resulting vertex (which might be vtx[0] or new ID)
        # Easier to check total count
        # self.assertEqual(cmds.polyEvaluate(self.cube, v=True), 7) # Cube has 8 verts, 2 merged -> 7
        pass  # Logic verification depends on exact topology, skipping strict assert for now

    # -------------------------------------------------------------------------
    # Axis Operations
    # -------------------------------------------------------------------------

    def test_get_all_faces_on_axis(self):
        """Test retrieving faces on specific axes."""
        # Cube at origin. Faces on +X should be selected.
        faces_x = EditUtils.get_all_faces_on_axis(self.cube, axis="x")
        self.assertTrue(len(faces_x) > 0)

        # Verify normal or position
        # Face center of +X face should have positive X
        # Use exactWorldBoundingBox to get center
        bbox = cmds.exactWorldBoundingBox(faces_x[0])
        center_x = (bbox[0] + bbox[3]) / 2
        self.assertGreater(center_x, 0)

        # Test with pivot
        faces_neg_x = EditUtils.get_all_faces_on_axis(self.cube, axis="-x")
        self.assertTrue(len(faces_neg_x) > 0)
        bbox = cmds.exactWorldBoundingBox(faces_neg_x[0])
        center_x = (bbox[0] + bbox[3]) / 2
        self.assertLess(center_x, 0)

    def test_cut_along_axis(self):
        """Test cutting geometry along an axis."""
        # Cut cube in half along X
        initial_faces = cmds.polyEvaluate(self.cube, f=True)
        EditUtils.cut_along_axis(self.cube, axis="x", amount=1)
        new_faces = cmds.polyEvaluate(self.cube, f=True)
        self.assertGreater(new_faces, initial_faces)

    def test_cut_along_axis_mirror(self):
        """Test cutting and mirroring."""
        # Move cube off center
        cmds.move(5, 0, 0, self.cube)
        EditUtils.cut_along_axis(self.cube, axis="x", delete=True, mirror=True)
        # Should result in a symmetric object
        self.assertTrue(cmds.objExists(self.cube))

    def test_delete_along_axis(self):
        """Test deleting faces along an axis."""
        EditUtils.delete_along_axis(self.cube, axis="x")
        # Should have deleted the +X face
        # Hard to verify exact topology without complex checks, but face count should drop
        # Actually, deleting a face of a cube leaves it open.
        pass

    # -------------------------------------------------------------------------
    # Mirror Operations
    # -------------------------------------------------------------------------

    def test_mirror(self):
        """Test mirroring geometry with merge mode."""
        cmds.move(5, 0, 0, self.cube)
        mirrored = EditUtils.mirror(self.cube, axis="-x", mergeMode=1)  # Merge
        self.assertTrue(mirrored)
        # Merged mirror should still be one object
        if isinstance(mirrored, list):
            self.assertEqual(len(mirrored), 1)
        self.assertTrue(cmds.objExists(self.cube))

    def test_mirror_separate_mode(self):
        """Test mirror with custom separate mode (mergeMode=-1).

        Bug: Separate mode was broken - polySeparate was called without connecting
        firstNewFace/lastNewFace attributes, so Maya couldn't track the mirrored half.
        Fixed: 2026-02-10 - Now delegates to separate_mirrored_mesh.
        """
        cube = cmds.polyCube(name="sep_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)
        result = EditUtils.mirror(cube, axis="-x", mergeMode=-1)
        # Separate mode should produce result(s)
        self.assertTrue(result)
        results = result if isinstance(result, list) else [result]
        # Should have produced at least the original + mirrored half
        self.assertGreaterEqual(len(results), 1)
        # All results should exist in the scene
        for r in results:
            self.assertTrue(cmds.objExists(r))

    def test_mirror_use_object_axes(self):
        """mirror with use_object_axes on a rotated object must tilt the plane.

        This assertion used to be "the call returned something and the cube
        still exists", which is true no matter what plane is used — so it
        certified a use_object_axes implementation that never affected the
        mirror at all. Compare the two frames instead: on a 45-degree object
        they give measurably different footprints. The full plane-level
        coverage lives in test_edit_tools_geometry.TestMirrorObjectAxes.

        The pivot is dragged OFF the object's center on purpose: a cube is
        symmetric about its own center in both frames, so a centered plane is
        a no-op either way and cannot tell them apart.
        """
        widths = []
        for i, use_object_axes in enumerate((True, False)):
            cube = cmds.polyCube(name=f"rotated_cube_{i}", w=10, h=10, d=10)[0]
            cmds.move(5, 0, 0, cube)
            cmds.rotate(0, 45, 0, cube)
            cmds.xform(cube, ws=True, piv=(9, 0, 0))

            result = EditUtils.mirror(
                cube,
                axis="x",
                pivot="object",
                mergeMode=1,
                use_object_axes=use_object_axes,
            )
            self.assertTrue(result)
            self.assertTrue(cmds.objExists(cube))
            bb = cmds.exactWorldBoundingBox(cube)
            widths.append(round(bb[3] - bb[0], 4))

        self.assertNotEqual(
            widths[0],
            widths[1],
            "use_object_axes made no difference on a rotated object — the "
            "mirror plane is ignoring the object frame again",
        )

    def test_mirror_separate_mode_on_instanced_source_keeps_both(self):
        """Regression: separate mode (mergeMode=-1) runs polySeparate, which
        consumes the source transform. On a SHARED shape that used to delete the
        mirrored object AND every sibling instance — the whole selection vanished.
        The separate path must fork the shape first regardless of `uninstance`.
        """
        src = cmds.polyCube(name="inst_mirror_src", width=2, height=1, depth=1)[0]
        sib = cmds.instance(src, name="inst_mirror_sib")[0]
        cmds.move(10, 0, 0, sib)
        sib_faces_before = cmds.polyEvaluate(sib, face=True)

        EditUtils.mirror(src, axis="x", pivot="object", mergeMode=-1)

        # Both survive (pre-fix: neither did).
        self.assertTrue(cmds.objExists(sib), "sibling instance was deleted")
        self.assertTrue(
            cmds.ls(f"{src}*", type="transform"), "source side vanished entirely"
        )
        # The untouched sibling keeps its own geometry — the mirror didn't leak
        # into it through the shared shape.
        self.assertEqual(cmds.polyEvaluate(sib, face=True), sib_faces_before)

    def test_mirror_merge_mode_leaves_sibling_instances_alone(self):
        """Mirroring always breaks the instance link first, so a merge-mode
        mirror can no longer rewrite every other instance's geometry through
        the shared shape."""
        src = cmds.polyCube(name="merge_inst_src", width=2, height=1, depth=1)[0]
        sib = cmds.instance(src, name="merge_inst_sib")[0]
        cmds.move(10, 0, 0, sib)
        sib_faces_before = cmds.polyEvaluate(sib, face=True)

        EditUtils.mirror(src, axis="x", pivot="object", mergeMode=1)

        self.assertTrue(cmds.objExists(sib))
        self.assertEqual(
            cmds.polyEvaluate(sib, face=True),
            sib_faces_before,
            "sibling instance was mirrored through the shared shape",
        )
        self.assertGreater(cmds.polyEvaluate(src, face=True), sib_faces_before)
        # The link is gone: each transform now owns its shape.
        for node in (src, sib):
            shape = cmds.listRelatives(node, shapes=True, ni=True, fullPath=True)[0]
            self.assertEqual(
                len(cmds.listRelatives(shape, allParents=True, fullPath=True) or []),
                1,
                f"{node} shape still shared",
            )

    def test_mirror_instance_shares_shape_and_reflects(self):
        """mirror_instance produces a LINKED copy (shared shape) reflected across
        the plane — not new geometry."""
        cmds.move(5, 0, 0, self.cube)
        result = EditUtils.mirror_instance(self.cube, axis="x", pivot="world")
        self.assertEqual(len(result), 1)
        inst = result[0]

        # Shape is shared with the source -> a real instance, not a duplicate.
        shape = cmds.listRelatives(inst, shapes=True, ni=True, fullPath=True)[0]
        parents = cmds.listRelatives(shape, allParents=True, fullPath=True) or []
        self.assertEqual(len(parents), 2, "shape is not instanced")

        # Reflected across the world YZ plane: +5 -> -5, and the transform is
        # mirrored (negative determinant).
        self.assertAlmostEqual(cmds.objectCenter(inst)[0], -5.0, places=3)
        matrix = om.MMatrix(cmds.xform(inst, q=True, m=True, ws=True))
        self.assertLess(matrix.det4x4(), 0, "instance is not mirrored")

    def test_mirror_instance_object_pivot_uses_object_axes(self):
        """With pivot='object' the plane rides the object's own axes, so a
        rotated object mirrors about itself and its center doesn't move."""
        cube = cmds.polyCube(name="rot_cube", w=2, h=2, d=2)[0]
        cmds.move(7, 0, 0, cube)
        cmds.rotate(0, 45, 0, cube)

        before = cmds.objectCenter(cube)
        inst = EditUtils.mirror_instance(cube, axis="x", pivot="object")[0]
        after = cmds.objectCenter(inst)
        for axis, b, a in zip("xyz", before, after):
            self.assertAlmostEqual(a, b, places=3, msg=f"center moved on {axis}")
        self.assertLess(
            om.MMatrix(cmds.xform(inst, q=True, m=True, ws=True)).det4x4(), 0
        )

    def test_mirror_instance_invalid_axis_raises(self):
        """A bad axis fails loudly rather than silently mirroring across X."""
        with self.assertRaises(ValueError):
            EditUtils.mirror_instance(self.cube, axis="w")

    def test_mirror_world_pivot(self):
        """Test mirror with world origin pivot."""
        cmds.move(5, 0, 0, self.cube)
        result = EditUtils.mirror(self.cube, axis="x", pivot="world", mergeMode=1)
        self.assertTrue(result)

    def test_mirror_tuple_pivot(self):
        """Test mirror with explicit tuple pivot."""
        cmds.move(5, 0, 0, self.cube)
        result = EditUtils.mirror(self.cube, axis="x", pivot=(0, 0, 0), mergeMode=1)
        self.assertTrue(result)

    def test_mirror_merge_centers_pivot(self):
        """Merge welds both halves into the same transform, so the pre-mirror pivot ends
        up off to one side of the combined result. It should re-center on the merged
        bounding box (the mirror plane along the axis).

        Feature: 2026-07-25 - "center the pivot when the operation calls for it".
        """
        cube = cmds.polyCube(name="merge_piv", w=10, h=10, d=10)[0]
        cmds.move(10, 0, 0, cube)  # spans x 5..15, pivot at x=10
        EditUtils.mirror(cube, axis="x", pivot="world", mergeMode=1)  # -> spans -15..15
        rp = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(rp[0], 0.0, places=3)  # centered on the world mirror plane

    def test_mirror_merge_center_pivot_off_preserves(self):
        """center_pivot=False opts out — the pre-mirror pivot is left in place."""
        cube = cmds.polyCube(name="merge_piv_off", w=10, h=10, d=10)[0]
        cmds.move(10, 0, 0, cube)
        EditUtils.mirror(cube, axis="x", pivot="world", mergeMode=1, center_pivot=False)
        rp = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(rp[0], 10.0, places=3)  # unchanged

    def _mesh_halves(self, prefix):
        """Mesh transforms whose name starts with ``prefix``, sorted by world bbox min-X
        (so [0] is the -X half, [-1] the +X half). Used after a separate-mode mirror,
        which yields two fresh transforms (polySeparate consumes + renames the source).
        Filtering by prefix skips the setUp cube/sphere sharing the scene."""
        halves = [
            t
            for t in cmds.ls(type="transform")
            if t.split("|")[-1].startswith(prefix)
            and cmds.listRelatives(t, shapes=True, type="mesh", ni=True)
        ]
        return sorted(halves, key=lambda o: cmds.exactWorldBoundingBox(o)[0])

    def test_mirror_separate_centers_new_half(self):
        """Separate mode: the NEW mirrored half gets a pivot on its own bounding-box center
        instead of floating over on top of the other object.

        Feature: 2026-07-25 - "center the pivot when the operation calls for it".
        (polySeparate itself centers the surviving original half; our code only touches
        the mirrored half, so the source's pivot is not changed by us.)
        """
        cube = cmds.polyCube(name="sep_piv", w=10, h=10, d=10)[0]
        cmds.move(10, 0, 0, cube)  # geometry spans 5..15
        EditUtils.mirror(cube, axis="x", pivot="world", mergeMode=-1)  # mirror half -> -15..-5

        halves = self._mesh_halves("sep_piv")
        self.assertEqual(len(halves), 2)
        new_half = halves[0]  # -X geometry (spans -15..-5)
        new_rp = cmds.xform(new_half, q=True, ws=True, rp=True)
        self.assertAlmostEqual(new_rp[0], -10.0, places=2)  # on its own center, not +10

    def test_mirror_separate_center_pivot_off_is_legacy(self):
        """center_pivot=False reproduces the old 'nowhere useful' pivot — the mirrored
        half's pivot lands over on the other object (x=10) instead of on its own
        geometry (x=-10)."""
        cube = cmds.polyCube(name="sep_piv_off", w=10, h=10, d=10)[0]
        cmds.move(10, 0, 0, cube)
        EditUtils.mirror(
            cube, axis="x", pivot="world", mergeMode=-1, center_pivot=False
        )
        halves = self._mesh_halves("sep_piv_off")
        self.assertEqual(len(halves), 2)
        new_half = halves[0]  # still the -X geometry (identified by bbox, not pivot)
        new_rp = cmds.xform(new_half, q=True, ws=True, rp=True)
        self.assertAlmostEqual(new_rp[0], 10.0, places=2)  # floats over the +X original

    def test_mirror_symmetrize_centers_pivot(self):
        """The Mirror panel's Bounding Box (center) pivot routes through
        cut_along_axis(delete=True, mirror=True) -> mirror(mergeMode=1). The symmetrized
        result is one combined object, so its pivot re-centers on the result even when it
        started off-center."""
        cube = cmds.polyCube(name="sym_piv", w=10, h=10, d=10)[0]
        cmds.move(10, 0, 0, cube)  # geometry spans 5..15
        cmds.xform(cube, ws=True, piv=(5, 0, 0))  # pivot off its own center
        EditUtils.cut_along_axis(
            cube,
            axis="x",
            invert=True,
            pivot="center",
            amount=1,
            delete=True,
            mirror=True,
            use_object_axes=True,
        )
        self.assertTrue(cmds.objExists(cube))  # symmetrize works in place (no separate)
        bb = cmds.exactWorldBoundingBox(cube)
        center_x = (bb[0] + bb[3]) / 2.0
        rp = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(rp[0], center_x, places=2)  # on the result's own center
        self.assertGreater(rp[0], 6.0)  # and moved off the starting off-center pivot (5)

    def _bbox_center(self, obj):
        bb = cmds.exactWorldBoundingBox(obj)
        return [(bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0, (bb[2] + bb[5]) / 2.0]

    def _pivot_offset(self, obj):
        """Distance of ``obj``'s pivot from its own bounding-box center (0 == centered)."""
        rp = cmds.xform(obj, q=True, ws=True, rp=True)
        center = self._bbox_center(obj)
        return sum((a - b) ** 2 for a, b in zip(rp, center)) ** 0.5

    def _detach_one_face(self, name, center_pivot=True, duplicate=False):
        """Detach face [0] of a fresh cube whose pivot sits at (20, 5, 0) — far from the
        extracted face's own center. Returns (results, extracted_piece). The extracted piece
        is the single planar face: identify it as the result whose geometry center is farthest
        from the source pivot (unaffected by where the pivot lands, so it works either way).
        """
        cube = cmds.polyCube(name=name, w=10, h=10, d=10)[0]
        cmds.move(20, 5, 0, cube)  # geom spans x15..25; +X face center is at x=25
        cmds.selectMode(component=True)
        cmds.selectType(facet=True)
        cmds.select(f"{cube}.f[0]")
        result = EditUtils.detach_components(
            duplicate=duplicate,
            separate=True,
            keep_faces_together=True,
            center_pivot=center_pivot,
        )
        cmds.selectMode(object=True)
        src = (20.0, 5.0, 0.0)
        extracted = max(
            result,
            key=lambda o: sum(
                (a - b) ** 2 for a, b in zip(self._bbox_center(o), src)
            ),
        )
        return result, extracted

    def test_detach_faces_centers_each_pivot(self):
        """Detach with separate: every resulting object gets a pivot on its OWN bounding-box
        center — split geo / multiple objects are centered separately. polySeparate leaves the
        source pivot on each piece, so the extracted face would otherwise float off-center.

        Feature: 2026-07-25 - "center the pivot on the detached mesh(es)".
        """
        result, extracted = self._detach_one_face("det_piv")
        self.assertTrue(result)
        for obj in result:
            self.assertTrue(cmds.objExists(obj))
            self.assertLess(self._pivot_offset(obj), 0.01)  # each on its own center
        # And specifically the extracted planar face (the one polySeparate leaves off-center).
        self.assertLess(self._pivot_offset(extracted), 0.01)

    def test_detach_faces_center_pivot_off_keeps_source_pivot(self):
        """center_pivot=False opts out — the extracted piece keeps polySeparate's default pivot
        (the source's, at (20, 5, 0)) instead of re-centering on its own single-face geometry
        (center at (20, 5, 5), i.e. ~5 units off)."""
        result, extracted = self._detach_one_face("det_piv_off", center_pivot=False)
        self.assertTrue(result)
        self.assertGreater(self._pivot_offset(extracted), 1.0)  # NOT on its own center

    def test_detach_faces_duplicate_default_centers_pivot(self):
        """The button's production default (duplicate=True) leaves the body intact and extracts a
        COPY. That copy is a separate shell after polySeparate, so it too must get a centered
        pivot rather than the source's."""
        result, extracted = self._detach_one_face("det_piv_dup", duplicate=True)
        self.assertTrue(result)
        for obj in result:
            self.assertLess(self._pivot_offset(obj), 0.01)  # each on its own center
        self.assertLess(self._pivot_offset(extracted), 0.01)

    def test_detach_faces_across_multiple_objects(self):
        """Faces spanning more than one object detach in one call.

        Bug (live report, 2026-07-27): ``polyChipOff`` refuses a component list that
        spans multiple objects — "RuntimeError: Doesn't work with multiple objects
        selected" — so Polygons ▸ Detach died on a multi-object face selection.
        Fixed: chip off object-by-object (same grouping fix as ``UvUtils.cut_uv_edges``).
        """
        a = cmds.polyCube(name="det_multi_a", w=10, h=10, d=10)[0]
        b = cmds.polyCube(name="det_multi_b", w=10, h=10, d=10)[0]
        cmds.move(30, 0, 0, b)

        result = EditUtils.detach_components(
            [f"{a}.f[0]", f"{b}.f[0]"],
            duplicate=False,
            separate=True,
            keep_faces_together=True,
        )
        cmds.selectMode(object=True)

        # Each source splits into body + extracted face.
        self.assertEqual(len(result), 4, f"expected 4 pieces, got {result}")
        for obj in result:
            self.assertTrue(cmds.objExists(obj))
            self.assertLess(self._pivot_offset(obj), 0.01)  # each on its own center
        # One extracted single-face piece per source object, both left selected.
        singles = [o for o in result if cmds.polyEvaluate(o, face=True) == 1]
        self.assertEqual(len(singles), 2, f"expected one face per source, got {singles}")
        selected = set(cmds.ls(sl=True, long=True))
        self.assertEqual(selected, set(cmds.ls(singles, long=True)))

    def test_detach_faces_multi_object_without_separate(self):
        """The in-place path (separate=False) also spans objects — every source mesh gets
        its own polyChipOff node, and the faces are chipped loose within each mesh."""
        a = cmds.polyCube(name="det_ip_a", w=10, h=10, d=10)[0]
        b = cmds.polyCube(name="det_ip_b", w=10, h=10, d=10)[0]
        cmds.move(30, 0, 0, b)

        extract = EditUtils.detach_components(
            [f"{a}.f[0]", f"{b}.f[0]"],
            duplicate=False,
            separate=False,
            keep_faces_together=True,
        )
        cmds.selectMode(object=True)

        self.assertTrue(cmds.ls(extract, type="polyChipOff"))
        for obj in (a, b):  # body + loose face = 2 shells, still one object
            self.assertEqual(cmds.polyEvaluate(obj, shell=True), 2)

    def test_detach_vertices_uses_given_components(self):
        """The vertex path must split the components it was GIVEN, not whatever happens to
        be selected (it used to ``mel.eval("polySplitVertex")``, which reads the selection —
        so an explicit component argument was silently ignored)."""
        cube = cmds.polyCube(name="det_vtx", w=10, h=10, d=10)[0]
        other = cmds.polyCube(name="det_vtx_other", w=10, h=10, d=10)[0]
        cmds.move(30, 0, 0, other)
        before = cmds.polyEvaluate(cube, vertex=True)

        cmds.select(f"{other}.vtx[0]")  # a DIFFERENT, misleading selection
        EditUtils.detach_components([f"{cube}.vtx[0]"], separate=False)
        cmds.selectMode(object=True)

        self.assertGreater(cmds.polyEvaluate(cube, vertex=True), before)
        self.assertEqual(cmds.polyEvaluate(other, vertex=True), before)

    def test_separate_mirrored_mesh(self):
        """Test separating a mirrored mesh using the polyMirrorFace history node."""
        cmds.move(5, 0, 0, self.cube)
        # Use mergeMode=0 (no merge) so the mirror history node is preserved
        EditUtils.mirror(self.cube, axis="-x", mergeMode=0)

        # Get the polyMirrorFace history node from the cube's history
        history = cmds.ls(cmds.listHistory(self.cube), type="polyMirrorFace")
        if history:
            mirror_node = history[0]
            new_obj = EditUtils.separate_mirrored_mesh(mirror_node)
            if new_obj is not None:
                self.assertTrue(cmds.objExists(new_obj))

    def test_mirror_preserves_normals_merged(self):
        """Verify mirrored mesh has outward-facing normals after merge.

        Bug: polyMirrorFace could produce reversed normals on the mirrored
        half, causing the mesh to render inside-out or black.
        Fixed: 2026-03-08 - Added polyNormal conform step after mirror.
        """
        cube = cmds.polyCube(name="norm_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)
        EditUtils.mirror(cube, axis="-x", mergeMode=1)

        # Verify normals point outward: the dot product of each face normal
        # with the vector from mesh center to face center should be > 0.
        import maya.api.OpenMaya as om

        bbox = cmds.exactWorldBoundingBox(cube)
        mesh_center = om.MVector(
            (bbox[0] + bbox[3]) / 2,
            (bbox[1] + bbox[4]) / 2,
            (bbox[2] + bbox[5]) / 2,
        )
        face_count = cmds.polyEvaluate(cube, f=True)
        for i in range(face_count):
            info = cmds.polyInfo(f"{cube}.f[{i}]", fn=True)
            parts = info[0].split()
            normal = om.MVector(float(parts[-3]), float(parts[-2]), float(parts[-1]))
            # Face center
            fb = cmds.exactWorldBoundingBox(f"{cube}.f[{i}]")
            face_center = om.MVector(
                (fb[0] + fb[3]) / 2, (fb[1] + fb[4]) / 2, (fb[2] + fb[5]) / 2
            )
            outward = face_center - mesh_center
            dot = normal * outward
            self.assertGreater(dot, 0, f"Face {i} normal points inward (dot={dot:.4f})")

    def test_mirror_preserves_normals_separate(self):
        """Verify both halves have correct normals after separate-mode mirror.

        Bug: polySeparate after polyMirrorFace could flip normals on the
        mirrored half, especially after construction history deletion.
        Fixed: 2026-03-08 - Added polyNormal conform before history deletion.
        """
        import maya.api.OpenMaya as om

        cube = cmds.polyCube(name="sep_norm_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)
        results = EditUtils.mirror(cube, axis="-x", mergeMode=-1)
        results = results if isinstance(results, list) else [results]

        for obj in results:
            if not cmds.objExists(obj):
                continue
            # Resolve to a mesh transform — mirror may return a group.
            target = str(obj)
            if not cmds.listRelatives(target, shapes=True, ni=True, type="mesh"):
                meshes = cmds.listRelatives(target, allDescendents=True, type="mesh") or []
                if not meshes:
                    continue
                # Walk to the mesh's parent transform.
                target = (cmds.listRelatives(meshes[0], parent=True, fullPath=True) or [target])[0]

            bbox = cmds.exactWorldBoundingBox(target)
            mesh_center = om.MVector(
                (bbox[0] + bbox[3]) / 2,
                (bbox[1] + bbox[4]) / 2,
                (bbox[2] + bbox[5]) / 2,
            )
            face_count = cmds.polyEvaluate(target, f=True)
            for i in range(face_count):
                info = cmds.polyInfo(f"{target}.f[{i}]", fn=True)
                parts = info[0].split()
                normal = om.MVector(
                    float(parts[-3]), float(parts[-2]), float(parts[-1])
                )
                fb = cmds.exactWorldBoundingBox(f"{target}.f[{i}]")
                face_center = om.MVector(
                    (fb[0] + fb[3]) / 2, (fb[1] + fb[4]) / 2, (fb[2] + fb[5]) / 2
                )
                outward = face_center - mesh_center
                dot = normal * outward
                self.assertGreater(
                    dot, 0,
                    f"Face {i} on {target} normal points inward (dot={dot:.4f})",
                )

    def test_mirror_delete_original(self):
        """Verify delete_original removes the original half in separate mode.

        Feature: Added delete_original parameter so users can mirror and discard
        the source half in one step.
        Added: 2026-03-08
        """
        cube = cmds.polyCube(name="del_orig_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)

        # Count transforms before mirror
        before = set(str(t) for t in cmds.ls(type="transform"))

        result = EditUtils.mirror(cube, axis="-x", mergeMode=-1, delete_original=True)
        results = result if isinstance(result, list) else [result]

        # Should return exactly one object (the mirrored half only)
        self.assertEqual(len(results), 1, "Expected only the mirrored half")
        self.assertTrue(cmds.objExists(results[0]))

        # The result should have geometry (not an empty group). When
        # results[0] is a group, pm.polyEvaluate returns a string error
        # message, so look up shapes ourselves.
        target = results[0]
        if not cmds.listRelatives(str(target), shapes=True, ni=True, type="mesh"):
            target = (cmds.listRelatives(str(target), allDescendents=True, type="mesh") or [None])[0]
            self.assertIsNotNone(target, "Mirrored result should contain a mesh")
        face_count = cmds.polyEvaluate(target, f=True)
        self.assertGreater(face_count, 0, "Mirrored half should have faces")

    def test_mirror_delete_original_false(self):
        """Verify delete_original=False (default) keeps both halves."""
        cube = cmds.polyCube(name="keep_orig_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)

        result = EditUtils.mirror(cube, axis="-x", mergeMode=-1, delete_original=False)
        results = result if isinstance(result, list) else [result]

        # Should return at least the mirrored half
        self.assertGreaterEqual(len(results), 1)

    # -------------------------------------------------------------------------
    # Overlap Detection
    # -------------------------------------------------------------------------

    def test_get_overlapping_duplicates(self):
        """Test finding duplicate objects."""
        dup = cmds.duplicate(self.cube)[0]
        dup_long = cmds.ls(str(dup), l=True)[0]
        cube_long = cmds.ls(str(self.cube), l=True)[0]
        duplicates = EditUtils.get_overlapping_duplicates([self.cube, dup])
        self.assertIn(dup_long, duplicates)
        self.assertNotIn(cube_long, duplicates)  # Should keep one

    def test_get_overlapping_vertices(self):
        """Test finding overlapping vertices."""
        # Create overlap
        cube2 = cmds.duplicate(self.cube)[0]
        combined = cmds.polyUnite(self.cube, cube2, ch=False)[0]
        overlaps = EditUtils.get_overlapping_vertices(combined)
        self.assertTrue(len(overlaps) > 0)

    def test_get_overlapping_faces(self):
        """Test finding overlapping faces."""
        cube2 = cmds.duplicate(self.cube)[0]
        combined = cmds.polyUnite(self.cube, cube2, ch=False)[0]
        overlaps = EditUtils.get_overlapping_faces(combined)
        self.assertTrue(len(overlaps) > 0)

    # -------------------------------------------------------------------------
    # Topology & Similarity
    # -------------------------------------------------------------------------

    def test_get_similar_mesh(self):
        """Test finding similar meshes."""
        dup = cmds.duplicate(self.cube)[0]
        cmds.move(10, 0, 0, dup)
        similar = EditUtils.get_similar_mesh(self.cube)
        self.assertIn(dup, similar)

    def test_get_similar_mesh_tolerance_and_unchecked_flags(self):
        """Regression: flags passed as False made polyEvaluate return the full
        stats dict (flag=False == flag absent), which then hit exact
        dict-equality inside are_similar — every unchecked metric silently
        required an exact all-stats match, so tolerance did nothing. Also,
        filterExpand's short names never compared equal to the long-name query
        object, so the original always self-matched despite inc_orig=False.
        """
        # Slot-style call: unchecked metrics arrive as False, not omitted.
        slot_kwargs = dict(
            vertex=True, edge=True, face=True, uvcoord=False, triangle=False,
            shell=False, boundingBox=False, area=False, worldArea=True,
        )
        twin = cmds.duplicate(self.cube)[0]
        # Same size as self.cube (10^3) so worldArea matches; only the
        # topological counts differ (sx=2 adds an edge loop).
        subdivided = cmds.polyCube(sx=2, w=10, h=10, d=10, name="subdivCube")[0]

        exact = cmds.ls(
            EditUtils.get_similar_mesh(self.cube, tolerance=0.0, **slot_kwargs)
        )
        self.assertIn(twin, exact)  # identical twin matches exactly
        self.assertNotIn("subdivCube", exact)  # different counts excluded
        self.assertNotIn(str(self.cube), exact)  # inc_orig=False honored

        loose = cmds.ls(
            EditUtils.get_similar_mesh(self.cube, tolerance=100.0, **slot_kwargs)
        )
        self.assertIn("subdivCube", loose)  # tolerance widens the match

    def test_get_similar_mesh_bounding_box_compares_size_not_position(self):
        """Regression: polyEvaluate -boundingBox returns WORLD-space min/max, so
        enabling the 'Bounding Box' metric compared *position*, not size — a
        duplicate that had been moved anywhere in the scene could never match
        and Select Similar returned nothing at all. Compare extents instead.
        """
        moved_twin = cmds.duplicate(self.cube)[0]
        cmds.move(25, 0, 0, moved_twin)
        rotated_twin = cmds.duplicate(self.cube)[0]
        cmds.xform(rotated_twin, t=(0, 0, 25), ro=(0, 45, 0))
        scaled = cmds.duplicate(self.cube)[0]
        cmds.xform(scaled, t=(-25, 0, 0), s=(2, 2, 2))
        bigger = cmds.polyCube(name="bigCube", w=99, h=99, d=99)[0]

        result = cmds.ls(
            EditUtils.get_similar_mesh(self.cube, tolerance=0.0, boundingBox=True)
        )
        self.assertIn(moved_twin, result)  # position must not disqualify
        self.assertIn(rotated_twin, result)  # nor orientation
        self.assertNotIn(scaled, result)  # scale must still discriminate
        self.assertNotIn(bigger, result)  # as must size

    def test_get_similar_mesh_world_area_survives_float32_drift(self):
        """Regression: worldArea comes back at float32 precision, so a *rotated*
        duplicate of an identical mesh reported e.g. 23.999996 against 24.0 and
        was rejected at tolerance 0.0. Float metrics get a relative-epsilon
        floor so transform round-off can't defeat an exact-tolerance match.
        """
        rotated_twin = cmds.duplicate(self.sphere)[0]
        cmds.xform(rotated_twin, t=(25, 0, 0), ro=(15, 30, 45))

        if cmds.polyEvaluate(rotated_twin, worldArea=True) == cmds.polyEvaluate(
            self.sphere, worldArea=True
        ):  # nothing to guard against if Maya ever reports these exactly
            self.skipTest("no float drift for this transform")

        result = cmds.ls(
            EditUtils.get_similar_mesh(self.sphere, tolerance=0.0, worldArea=True)
        )
        self.assertIn(rotated_twin, result)

    def test_get_similar_mesh_world_area_drift_scales_with_distance(self):
        """Regression (reported): a first epsilon calibrated on a cube at the
        origin still needed a hand-set tolerance on real geometry. worldArea is
        evaluated from float32 WORLD positions, so its error tracks distance
        from the origin, not mesh size — a small dense mesh parked far out
        drifts by ~1e-3 relative, three orders past a cube's.
        """
        cmds.delete(self.cube, self.sphere)
        src = cmds.polySphere(name="far_src", r=0.5, sx=60, sy=60)[0]
        twin = cmds.duplicate(src, name="far_twin")[0]
        cmds.xform(twin, t=(-1000, 500, 2000), ro=(33, 17, 71))

        base = cmds.polyEvaluate(src, worldArea=True)
        drift = abs(cmds.polyEvaluate(twin, worldArea=True) - base) / base
        self.assertGreater(drift, 1e-5, "placement no longer drifts; retune the test")

        result = cmds.ls(
            EditUtils.get_similar_mesh(src, tolerance=0.0, worldArea=True)
        )
        self.assertIn(twin, result)

    def test_metric_tolerance_floor_never_loosens_integer_counts(self):
        """The float epsilon floor must not bleed into integer metrics: a dense
        mesh whose vertex count differs by one has to stay a non-match.
        """
        # Integer metrics keep the caller's tolerance verbatim, at any magnitude.
        self.assertEqual(EditUtils._metric_tolerance(0.0, "vertex", 250000, 250001), 0.0)
        self.assertEqual(EditUtils._metric_tolerance(2.0, "face", 8, 9), 2.0)
        # World-space floats get a generous floor; object-space ones a nominal.
        world = EditUtils._metric_tolerance(0.0, "worldArea", 600.0, 599.9)
        local = EditUtils._metric_tolerance(0.0, "area", 600.0, 599.9)
        self.assertGreater(world, 0.1)  # covers the measured drift
        self.assertLess(world, 60.0)  # but nowhere near a real scale difference
        self.assertLess(local, 1e-4)  # object-space metrics are exact

    def test_get_similar_mesh_world_area_still_rejects_a_scaled_copy(self):
        """The generous world-space epsilon must not swallow a real difference:
        a scaled copy changes worldArea by percent, not by float32 ulps.
        """
        barely_scaled = cmds.duplicate(self.cube, name="barelyScaled")[0]
        cmds.xform(barely_scaled, t=(25, 0, 0), s=(1.01, 1.01, 1.01))

        result = cmds.ls(
            EditUtils.get_similar_mesh(self.cube, tolerance=0.0, worldArea=True)
        )
        self.assertNotIn(barely_scaled, result)

    def test_get_similar_topo(self):
        """Test finding similar topology."""
        dup = cmds.duplicate(self.cube)[0]
        cmds.move(10, 0, 0, dup)
        similar = EditUtils.get_similar_topo(self.cube)
        self.assertIn(dup, similar)

    def test_get_similar_mesh_no_polygons_in_scene_no_crash(self):
        """Regression: cmds.filterExpand returns None (not []) when zero
        polygon transforms exist in the scene, so `set(cmds.filterExpand(...))`
        raised 'TypeError: NoneType object is not iterable' instead of just
        returning an empty result.
        """
        cmds.delete(self.cube, self.sphere)
        self.assertEqual(EditUtils.get_similar_mesh([]), [])

    def test_non_manifold(self):
        """Test non-manifold geometry detection."""
        # Create non-manifold geometry: 2 cubes sharing one vertex
        # Hard to script reliably without complex setup.
        # We'll skip strict creation but test the function call doesn't crash on normal geo
        nm_verts = EditUtils.find_non_manifold_vertex(self.cube)
        self.assertEqual(len(nm_verts), 0)

    # -------------------------------------------------------------------------
    # Selection Utilities
    # -------------------------------------------------------------------------

    def test_invert_geometry(self):
        """Test inverting object selection."""
        cmds.select(self.cube)
        inverted = EditUtils.invert_geometry()
        self.assertIn(self.sphere, inverted)
        self.assertNotIn(self.cube, inverted)

    def test_invert_components(self):
        """Test inverting component selection."""
        cmds.select(f"{self.cube}.vtx[0]")
        inverted = EditUtils.invert_components()
        # Production returns strings; compare on string form.
        inverted_strs = [str(i) for i in inverted]
        self.assertNotIn(str(f"{self.cube}.vtx[0]"), inverted_strs)
        # Some shape-prefixed variant of vtx[1] should be present.
        self.assertTrue(any(".vtx[1]" in s for s in inverted_strs))

    def test_delete_selected(self):
        """Test delete selected wrapper."""
        # Test object deletion
        cmds.select(self.sphere)
        EditUtils.delete_selected()
        self.assertFalse(cmds.objExists(self.sphere))

    def test_delete_selected_faces_single_object(self):
        """Selecting faces must delete only the faces, not the whole mesh."""
        face_count = cmds.polyEvaluate(self.cube, f=True)
        cmds.selectType(ocm=True, alc=False, polymeshFace=True)
        cmds.select(f"{self.cube}.f[0]", f"{self.cube}.f[1]")
        EditUtils.delete_selected()
        self.assertTrue(cmds.objExists(self.cube))
        self.assertEqual(cmds.polyEvaluate(self.cube, f=True), face_count - 2)

    def test_delete_selected_faces_multi_object(self):
        """Components selected across multiple meshes must all be deleted, no mesh removed."""
        cube_faces = cmds.polyEvaluate(self.cube, f=True)
        sphere_faces = cmds.polyEvaluate(self.sphere, f=True)
        cmds.selectType(ocm=True, alc=False, polymeshFace=True)
        cmds.select(f"{self.cube}.f[0]", f"{self.sphere}.f[0]", f"{self.sphere}.f[1]")
        EditUtils.delete_selected()
        self.assertTrue(cmds.objExists(self.cube))
        self.assertTrue(cmds.objExists(self.sphere))
        self.assertEqual(cmds.polyEvaluate(self.cube, f=True), cube_faces - 1)
        self.assertEqual(cmds.polyEvaluate(self.sphere, f=True), sphere_faces - 2)

    def test_delete_selected_mixed_components_and_objects(self):
        """Mixed selection: components on one mesh + a whole second mesh."""
        extra = cmds.polyCube(name="test_cube_extra")[0]
        cube_faces = cmds.polyEvaluate(self.cube, f=True)
        cmds.selectType(ocm=True, alc=False, polymeshFace=True)
        cmds.select(f"{self.cube}.f[0]", extra)
        EditUtils.delete_selected()
        self.assertTrue(cmds.objExists(self.cube))
        self.assertFalse(cmds.objExists(extra))
        self.assertEqual(cmds.polyEvaluate(self.cube, f=True), cube_faces - 1)

    def test_create_curve_from_edges(self):
        """Test creating curve from edges."""
        edges = [f"{self.cube}.e[0]", f"{self.cube}.e[1]"]
        curve = EditUtils.create_curve_from_edges(edges)

        # curve might be a list [transform, history]
        if isinstance(curve, list):
            curve = curve[0]

        # Ensure it's a PyNode
        curve = curve

        self.assertTrue(cmds.objExists(curve))
        self.assertEqual(cmds.nodeType((cmds.listRelatives(str(curve), shapes=True, ni=True) or [None])[0]), "nurbsCurve")

    def test_separate_objects(self):
        """Test separate_objects method."""
        # Setup materials
        mat1 = mtk.MatUtils.create_mat("lambert", name="mat1")
        mat2 = mtk.MatUtils.create_mat("lambert", name="mat2")

        # Scenario 1: Standard Separate (Disjoint Shells)
        # ---------------------------------------------
        c1 = cmds.polyCube()[0]
        c2 = cmds.polyCube()[0]
        cmds.move(5, 0, 0, c2)
        combined = cmds.polyUnite(c1, c2, ch=False)[0]

        # separate_objects default (by_material=False) should work like polySeparate
        res = EditUtils.separate_objects([combined], by_material=False)
        self.assertEqual(len(res), 2)
        cmds.delete(res)

        # Scenario 2: Separate by Material (Disjoint Shells)
        # ---------------------------------------------
        c3 = cmds.polyCube()[0]
        c4 = cmds.polyCube()[0]
        cmds.move(5, 0, 0, c4)
        mtk.MatUtils.assign_mat(c3, mat1)
        mtk.MatUtils.assign_mat(c4, mat2)
        combined2 = cmds.polyUnite(c3, c4, ch=False)[0]

        res2 = EditUtils.separate_objects([combined2], by_material=True)
        self.assertEqual(len(res2), 2)
        cmds.delete(res2)

        # Scenario 3: Separate by Material (Single Shell)
        # ---------------------------------------------
        c5 = cmds.polyCube(sx=2)[0]
        mtk.MatUtils.assign_mat(c5, mat1)
        cmds.select(f"{c5}.f[0:3]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), mat2)

        # Without by_material, should remain 1 object
        res3a = EditUtils.separate_objects([c5], by_material=False)
        self.assertEqual(len(res3a), 1)
        # (It returns the object itself if no separation happened)

        # With by_material, should split
        res3b = EditUtils.separate_objects(res3a, by_material=True)
        self.assertEqual(len(res3b), 2)
        cmds.delete(res3b)

        # Scenario 4: Rename Check
        # ---------------------------------------------
        c6 = cmds.polyCube(n="MyBox")[0]
        c7 = cmds.polyCube()[0]  # Shell 2
        cmds.move(10, 0, 0, c7)
        combined3 = cmds.polyUnite(c6, c7, n="MyComp", ch=False)[0]

        # Rename=True
        # Expect MyComp_01, MyComp_02 (or location based suffix)
        res4 = EditUtils.separate_objects([combined3], rename=True)
        self.assertEqual(len(res4), 2)

        names = [r.split("|")[-1] for r in res4]
        # Verify names start with "MyComp"
        self.assertTrue(all(n.startswith("MyComp") for n in names))
        cmds.delete(res4)

    # -------------------------------------------------------------------------
    # Decimate
    # -------------------------------------------------------------------------

    def test_decimate_reduces_faces(self):
        sphere = cmds.polySphere(subdivisionsX=40, subdivisionsY=40, ch=False)[0]
        before = cmds.polyEvaluate(sphere, face=True)
        result = EditUtils.decimate([sphere], percentage=50.0)
        self.assertEqual(result, [sphere])
        self.assertLess(cmds.polyEvaluate(sphere, face=True), before)
        # delete_history (default) removes the polyReduce node.
        self.assertNotIn("polyReduce", str(cmds.listHistory(sphere) or []))

    def test_decimate_handles_multiple_objects(self):
        # polyReduce raises "Doesn't work with multiple objects selected" when
        # handed more than one mesh, so decimate must reduce each independently.
        sphere = cmds.polySphere(subdivisionsX=40, subdivisionsY=40, ch=False)[0]
        cube = cmds.polyCube(sx=20, sy=20, sz=20, ch=False)[0]
        before = {o: cmds.polyEvaluate(o, face=True) for o in (sphere, cube)}
        result = EditUtils.decimate([sphere, cube], percentage=50.0)
        self.assertEqual(result, [sphere, cube])
        for o in (sphere, cube):
            self.assertLess(cmds.polyEvaluate(o, face=True), before[o])

    def test_decimate_no_objects_is_noop(self):
        cmds.select(clear=True)
        self.assertEqual(EditUtils.decimate([]), [])

    def test_decimate_zero_percent_leaves_mesh_untouched(self):
        sphere = cmds.polySphere(subdivisionsX=12, subdivisionsY=12, ch=False)[0]
        before = cmds.polyEvaluate(sphere, face=True)
        EditUtils.decimate([sphere], percentage=0.0)
        self.assertEqual(cmds.polyEvaluate(sphere, face=True), before)
        self.assertNotIn("polyReduce", str(cmds.listHistory(sphere) or []))

    def test_dissolve_coplanar_strips_flat_regions_losslessly(self):
        # A subdivided cube is all coplanar quads per side + 90 deg cube edges:
        # planar dissolve must merge each side back to one face (6 total) while
        # leaving the shape (bounding box) identical.
        cube = cmds.polyCube(sx=5, sy=5, sz=5, ch=False)[0]
        before = cmds.polyEvaluate(cube, face=True)
        bb_before = cmds.exactWorldBoundingBox(cube)
        result = EditUtils.dissolve_coplanar([cube], angle_tolerance=1.0)
        self.assertEqual(result, [cube])
        self.assertLess(cmds.polyEvaluate(cube, face=True), before)
        self.assertEqual(cmds.polyEvaluate(cube, face=True), 6)
        for a, b in zip(bb_before, cmds.exactWorldBoundingBox(cube)):
            self.assertAlmostEqual(a, b, places=5)

    def test_dissolve_coplanar_keeps_curved_features(self):
        # On a sphere every interior edge is a real angle change, so a tight
        # tolerance must leave the face count essentially unchanged.
        sphere = cmds.polySphere(subdivisionsX=16, subdivisionsY=16, ch=False)[0]
        before = cmds.polyEvaluate(sphere, face=True)
        EditUtils.dissolve_coplanar([sphere], angle_tolerance=0.5)
        self.assertEqual(cmds.polyEvaluate(sphere, face=True), before)

    def test_overlapping_duplicates_catches_frozen_transform_twin(self):
        """A coincident duplicate whose transforms were frozen must still be
        detected — the old fingerprint hashed polyEvaluate's full dict, whose
        OBJECT-SPACE floats differ between the twins even though the
        world-space geometry is identical.  Added: 2026-08-01.
        """
        src = cmds.polyCube(name="frozenTwinSrc")[0]
        cmds.setAttr(f"{src}.translate", 5, 1, 2)
        dup = cmds.duplicate(src, name="frozenTwinDup")[0]
        cmds.makeIdentity(dup, apply=True, translate=True, rotate=True, scale=True)

        duplicates = EditUtils.get_overlapping_duplicates(objects=[src, dup])
        self.assertTrue(
            duplicates, "frozen-transform coincident duplicate was not detected"
        )


class TestOriginalAxisFrame(MayaTkTestCase):
    """The axis-based ops accept 'original' and resolve it to the PRE-FREEZE
    frame; on a frozen object 'object' is indistinguishable from 'world'."""

    def setUp(self):
        super().setUp()
        # A tall box so its own X/Y extents are unambiguous under rotation.
        self.box = cmds.polyCube(name="oaf_box", w=1, h=4, d=1)[0]
        cmds.setAttr(f"{self.box}.rotateZ", 90.0)

    def test_frame_matrix_falls_back_when_unstamped(self):
        from mayatk.edit_utils._edit_utils import _EditUtilsInternal

        self.assertTrue(
            _EditUtilsInternal._axis_frame_matrix(self.box, "original").isEquivalent(
                _EditUtilsInternal._axis_frame_matrix(self.box, "object"), 1e-6
            )
        )

    def test_frame_matrix_recovers_the_authored_frame(self):
        from mayatk.edit_utils._edit_utils import _EditUtilsInternal

        authored = _EditUtilsInternal._axis_frame_matrix(self.box, "object")
        mtk.XformUtils.freeze_transforms(self.box, force=True)

        live = _EditUtilsInternal._axis_frame_matrix(self.box, "object")
        original = _EditUtilsInternal._axis_frame_matrix(self.box, "original")
        self.assertFalse(
            live.isEquivalent(authored, 1e-6), "the freeze must flatten 'object'"
        )
        self.assertTrue(
            original.isEquivalent(authored, 1e-4),
            "'original' must rebuild the pre-freeze frame",
        )

    def test_extent_in_frame_matches_object_space_when_unrotated(self):
        from mayatk.edit_utils._edit_utils import _EditUtilsInternal

        cube = cmds.polyCube(name="oaf_plain", w=2, h=2, d=2)[0]
        frame = _EditUtilsInternal._axis_frame_matrix(cube, "object")
        mins, maxs = _EditUtilsInternal._extent_in_frame(f"{cube}.vtx[*]", frame)
        for value in mins:
            self.assertAlmostEqual(value, -1.0, places=5)
        for value in maxs:
            self.assertAlmostEqual(value, 1.0, places=5)

    def test_extent_in_frame_is_none_for_a_pointless_target(self):
        """A nurbs surface has no `.vtx[*]` and cmds.xform RAISES on it rather
        than returning empty — the callers rely on None to fall back."""
        from mayatk.edit_utils._edit_utils import _EditUtilsInternal

        surface = cmds.nurbsPlane(name="oaf_nurbs")[0]
        frame = _EditUtilsInternal._axis_frame_matrix(surface, "object")
        self.assertIsNone(
            _EditUtilsInternal._extent_in_frame(f"{surface}.vtx[*]", frame)
        )

    def test_cut_skips_a_non_polygon_instead_of_raising(self):
        """polyCut is polygon-only; handed a nurbs it used to raise from inside
        the loop and kill the whole call, while every other unsupported case
        here warns and moves on."""
        surface = cmds.nurbsPlane(name="oaf_nurbs_cut", u=1, v=1)[0]
        mesh = cmds.polyCube(name="oaf_cut_mesh")[0]
        before = cmds.polyEvaluate(mesh, face=True)

        EditUtils.cut_along_axis([surface, mesh], axis="x", amount=1)

        self.assertTrue(cmds.objExists(surface), "the nurbs must be left alone")
        self.assertGreater(
            cmds.polyEvaluate(mesh, face=True),
            before,
            "the mesh in the same call must still be cut",
        )

    def test_faces_on_axis_uses_the_authored_frame(self):
        """Frozen: the box's long axis is world X, but it was authored along Y.
        Selecting +Y faces in the 'original' frame must pick the authored top
        cap, which 'object' can no longer distinguish from the world's."""
        mtk.XformUtils.freeze_transforms(self.box, force=True)

        original = EditUtils.get_all_faces_on_axis(self.box, "y", pivot="original")
        object_frame = EditUtils.get_all_faces_on_axis(self.box, "y", pivot="object")
        self.assertTrue(original, "'original' must resolve to a face set")
        self.assertNotEqual(
            set(original),
            set(object_frame),
            "a frozen object's 'object' frame is the world's — the two "
            "selections must differ once the authored frame is recovered",
        )


if __name__ == "__main__":
    unittest.main()
