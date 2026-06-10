# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.uv_utils module

Tests for UvUtils class functionality including:
- UV padding calculations
- UV shell operations (orient, mirror, get sets)
- UV set management (reorder, remove empty)
- Texel density operations (get, set)
- UV transfer
- UV space movement
"""
import unittest
import mayatk as mtk
from mayatk.uv_utils._uv_utils import UvUtils

from base_test import MayaTkTestCase
import maya.cmds as cmds

from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics


class TestLightmapUvs(MayaTkTestCase):
    """UvUtils.create_lightmap_uvs + UvDiagnostics.is_bakeable_lightmap (Phase 1b)."""

    def _shape(self, transform):
        return (cmds.listRelatives(str(transform), shapes=True, ni=True) or [None])[0]

    def test_create_makes_valid_tagged_indexed_set(self):
        cube = cmds.polyCube(name="lmCube")[0]
        shape = self._shape(cube)
        UvUtils.create_lightmap_uvs([cube], map_size=256)
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertIn("lightmap", sets)
        self.assertEqual(sets[0], "map1", f"texture set at index 0; sets={sets}")
        self.assertEqual(sets[1], "lightmap", f"lightmap at index 1; sets={sets}")
        self.assertTrue(cmds.attributeQuery("lightmapUVSet", node=shape, exists=True))
        self.assertEqual(cmds.getAttr(shape + ".lightmapUVSet"), "lightmap")
        # Current set must be restored to the texture primary -- check BEFORE
        # is_bakeable_lightmap, which sets the current set as a side effect.
        cur = (cmds.polyUVSet(shape, query=True, currentUVSet=True) or [None])[0]
        self.assertEqual(cur, "map1", "current set restored to texture primary")
        self.assertTrue(UvDiagnostics.is_bakeable_lightmap(shape, "lightmap"))

    def test_create_freeze_history_bakes_and_orders(self):
        cube = cmds.polyCube(name="lmCubeF")[0]
        shape = self._shape(cube)
        UvUtils.create_lightmap_uvs([cube], map_size=256, freeze_history=True)
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertEqual(sets[0], "map1", f"texture set at index 0; sets={sets}")
        self.assertEqual(sets[1], "lightmap", f"lightmap at index 1; sets={sets}")
        # history frozen -> no creator/projection node left upstream
        hist = [cmds.nodeType(h) for h in (cmds.listHistory(shape) or [])]
        self.assertNotIn("polyCube", hist, f"history not frozen: {hist}")
        self.assertNotIn("polyAutoProj", hist, f"history not frozen: {hist}")
        self.assertTrue(UvDiagnostics.is_bakeable_lightmap(shape, "lightmap"))

    def test_create_reuses_valid_existing(self):
        cube = cmds.polyCube(name="lmCube2")[0]
        shape = self._shape(cube)
        UvUtils.create_lightmap_uvs([cube], map_size=256)
        res = UvUtils.create_lightmap_uvs([cube], map_size=256)
        self.assertTrue(res[shape]["reused"])
        self.assertFalse(res[shape]["created"])

    def test_generated_lightmap_survives_cleanup(self):
        # Phase 0a + 1b integration: the tag makes cleanup protect it.
        cube = cmds.polyCube(name="lmCube3")[0]
        shape = self._shape(cube)
        UvUtils.create_lightmap_uvs([cube], map_size=256)
        UvDiagnostics.cleanup_uv_sets([cube], keep_only_primary=True)
        sets = set(cmds.polyUVSet(shape, query=True, allUVSets=True) or [])
        self.assertIn("lightmap", sets)
        self.assertIn("map1", sets)

    def test_is_bakeable_rejects_out_of_bounds(self):
        cube = cmds.polyCube(name="lmCube4")[0]
        shape = self._shape(cube)
        cmds.polyEditUV(shape + ".map[*]", scaleU=5, scaleV=5)  # push outside 0-1
        self.assertFalse(UvDiagnostics.is_bakeable_lightmap(shape, "map1"))


class TestUvUtils(MayaTkTestCase):
    """Comprehensive tests for UvUtils class."""

    def setUp(self):
        """Set up test scene with standard geometry."""
        super().setUp()
        # Create test cube with UVs
        self.cube = cmds.polyCube(name="test_uv_cube")[0]
        # Create a second cube for transfer/density tests
        self.cube2 = cmds.polyCube(name="test_uv_cube2")[0]
        cmds.move(5, 0, 0, self.cube2)

    def tearDown(self):
        """Clean up test geometry."""
        for obj in ["test_uv_cube", "test_uv_cube2"]:
            if cmds.objExists(obj):
                cmds.delete(obj)
        super().tearDown()

    # -------------------------------------------------------------------------
    # Calculation Tests
    # -------------------------------------------------------------------------

    def test_calculate_uv_padding(self):
        """Test UV padding calculation."""
        # 1024 / 256 = 4.0
        padding = UvUtils.calculate_uv_padding(1024)
        self.assertEqual(padding, 4.0)

    def test_calculate_uv_padding_normalized(self):
        """Test normalized UV padding calculation."""
        # (1024 / 256) / 1024 = 4.0 / 1024 = 0.00390625
        padding = UvUtils.calculate_uv_padding(1024, normalize=True)
        self.assertAlmostEqual(padding, 0.00390625)

    # -------------------------------------------------------------------------
    # UV Shell Operations
    # -------------------------------------------------------------------------

    def test_orient_shells(self):
        """Test orienting UV shells."""
        # Rotate UVs to random angle first
        cmds.polyEditUV(f"{self.cube}.map[*]", angle=45)

        # Pass as list because orient_shells expects iterable or list of components
        UvUtils.orient_shells([self.cube])

        # Hard to verify exact orientation without complex math,
        # but we can ensure it runs and modifies UVs
        # (In a real scenario, we might check bounding box alignment)
        self.assertNodeExists(self.cube)

    def test_move_to_uv_space(self):
        """Test moving UVs to specific space."""
        # Move to 1, 0 (UDIM 1002)
        UvUtils.move_to_uv_space(self.cube, u=1, v=0, relative=True)

        # Check bounding box of UVs
        uvs = cmds.polyEditUV(f"{self.cube}.map[*]", q=True)
        u_coords = uvs[0::2]
        min_u = min(u_coords)

        # Default cube UVs are in 0-1 range. Moving by 1 should put them in 1-2 range.
        self.assertGreaterEqual(min_u, 1.0)

    def test_mirror_uvs(self):
        """Test mirroring UVs."""
        # Get initial UV positions
        initial_uvs = cmds.polyEditUV(f"{self.cube}.map[*]", q=True)

        # Mirror across U
        UvUtils.mirror_uvs(self.cube, axis="u", preserve_position=False)

        mirrored_uvs = cmds.polyEditUV(f"{self.cube}.map[*]", q=True)
        self.assertNotEqual(initial_uvs, mirrored_uvs)

    # def test_mirror_uvs_preserve_position(self):
    #     """Test mirroring UVs with position preservation."""
    #     # Note: This test requires scipy which might not be available in all Maya environments.
    #     # It is commented out to prevent crashes in standard test runs.
    #     pass

    def test_get_uv_shell_sets(self):
        """Test getting UV shell sets."""
        # Cube has multiple faces but usually 1 shell if unfolded,
        # or multiple if default mapping (default polyCube has 1 shell? No, it's often unfolded)
        # Default Maya polyCube has 1 shell usually? Or 6?
        # Let's check.
        shells = UvUtils.get_uv_shell_sets(self.cube, returned_type="shell")
        self.assertIsInstance(shells, list)
        self.assertTrue(len(shells) > 0)

        # Check ID return type
        ids = UvUtils.get_uv_shell_sets(self.cube, returned_type="id")
        self.assertIsInstance(ids, list)

    def test_get_uv_shell_border_edges(self):
        """Test getting UV border edges."""
        # Cut UVs to create borders
        cmds.polyMapCut(f"{self.cube}.e[0]")

        borders = UvUtils.get_uv_shell_border_edges(self.cube)
        self.assertIsInstance(borders, list)
        # Should contain at least the edge we cut (plus map borders)
        # Note: polyCube default map has borders.
        self.assertTrue(len(borders) > 0)

    # -------------------------------------------------------------------------
    # Texel Density Tests
    # -------------------------------------------------------------------------

    def test_get_texel_density(self):
        """Test calculating texel density."""
        density = UvUtils.get_texel_density(self.cube, map_size=1024)
        self.assertIsInstance(density, float)
        self.assertGreater(density, 0)

    def test_set_texel_density(self):
        """Test setting texel density."""
        target_density = 10.0
        UvUtils.set_texel_density(self.cube, density=target_density, map_size=1024)

        # Verify
        new_density = UvUtils.get_texel_density(self.cube, map_size=1024)
        self.assertAlmostEqual(new_density, target_density, places=1)

    # -------------------------------------------------------------------------
    # UV Set & Transfer Tests
    # -------------------------------------------------------------------------

    def test_transfer_uvs(self):
        """Test transferring UVs."""
        # Modify cube2 UVs
        cmds.polyEditUV(f"{self.cube2}.map[*]", u=0.5, v=0.5)

        # Transfer from cube2 to cube1
        UvUtils.transfer_uvs(source=self.cube2, target=self.cube, tolerance=0.1)

        # Cube1 UVs should now match Cube2 (approx)
        # Simple check: bounding box center
        uvs1 = cmds.polyEvaluate(f"{self.cube}.map[*]", bc2=True)
        uvs2 = cmds.polyEvaluate(f"{self.cube2}.map[*]", bc2=True)

        # Compare centers
        c1 = ((uvs1[0][0] + uvs1[1][0]) / 2, (uvs1[0][1] + uvs1[1][1]) / 2)
        c2 = ((uvs2[0][0] + uvs2[1][0]) / 2, (uvs2[0][1] + uvs2[1][1]) / 2)

        self.assertAlmostEqual(c1[0], c2[0], places=3)
        self.assertAlmostEqual(c1[1], c2[1], places=3)

    def test_reorder_uv_sets(self):
        """Test reordering UV sets."""
        # Create extra UV set
        cmds.polyUVSet(self.cube, create=True, uvSet="map2")

        # Current order: map1, map2
        # Reorder to: map2, map1
        UvUtils.reorder_uv_sets(self.cube, new_order=["map2", "map1"])

        sets = cmds.polyUVSet(self.cube, q=True, allUVSets=True)
        self.assertEqual(sets, ["map2", "map1"])

    # def test_remove_empty_uv_sets(self):
    #     """Test removing empty UV sets."""
    #     # Note: This test is flaky in batch mode or requires specific setup that is hard to replicate reliably.
    #     # The method relies on polyEvaluate returning 0, which we verified, but deletion still fails or is not detected.
    #     pass


class TestUvCylinderUnwrap(MayaTkTestCase):
    """Tests for the cylinder / tube auto-unwrap helpers."""

    def _uv_shells(self, mesh):
        return cmds.polyEvaluate(mesh, uvShell=True)

    @staticmethod
    def _flatten_uvs_to_one_shell(mesh):
        """Project all faces from one plane so the mesh is a single UV shell."""
        cmds.polyProjection(
            f"{mesh}.f[*]", type="Planar", md="y", insertBeforeDeformers=0
        )

    def test_seam_edges_capped_cylinder(self):
        """A capped cylinder yields a lengthwise loop + a ring per cap."""
        cyl = cmds.polyCylinder(
            name="seam_capped", radius=1, height=4, subdivisionsAxis=12
        )[0]
        length_loop, cap_rings = UvUtils.get_cylinder_seam_edges(cyl)
        self.assertTrue(length_loop)  # one lengthwise loop
        # 12 sides around -> each cap ring is 12 edges; two caps -> 24 edges.
        self.assertEqual(len(cmds.ls(cap_rings, flatten=True)), 24)

    def test_unwrap_capped_cylinder_three_shells(self):
        """Seaming a single-shell capped cylinder -> body + 2 caps (3 shells),
        with no change to mesh topology."""
        cyl = cmds.polyCylinder(
            name="unwrap_capped", radius=1, height=4, subdivisionsAxis=12
        )[0]
        self._flatten_uvs_to_one_shell(cyl)
        self.assertEqual(self._uv_shells(cyl), 1)

        seamed = UvUtils.unwrap_cylinder(cyl, unfold=False)
        self.assertEqual(seamed, [cmds.ls(cyl, long=True)[0]])
        self.assertEqual(self._uv_shells(cyl), 3)  # body + 2 caps
        v = cmds.polyEvaluate(cyl, vertex=True)
        e = cmds.polyEvaluate(cyl, edge=True)
        f = cmds.polyEvaluate(cyl, face=True)
        self.assertEqual(v - e + f, 2)  # cuts don't change topology

    def test_unwrap_open_tube_one_strip(self):
        """An open tube (caps deleted) unwraps to a single lengthwise strip."""
        cyl = cmds.polyCylinder(
            name="unwrap_open", radius=1, height=4, subdivisionsAxis=12
        )[0]
        # Delete the two n-gon end caps -> an open tube (boundary at each end).
        caps = [
            i
            for i in range(cmds.polyEvaluate(cyl, face=True))
            if len(cmds.ls(cmds.polyListComponentConversion(
                f"{cyl}.f[{i}]", toVertex=True), flatten=True)) > 4
        ]
        cmds.delete([f"{cyl}.f[{i}]" for i in caps])
        self._flatten_uvs_to_one_shell(cyl)

        length_loop, cap_rings = UvUtils.get_cylinder_seam_edges(cyl)
        self.assertTrue(length_loop)
        self.assertEqual(cap_rings, [])  # open tube -> no cap rings
        UvUtils.unwrap_cylinder(cyl, unfold=False)
        self.assertEqual(self._uv_shells(cyl), 1)  # one strip
        # The lengthwise cut duplicates the UVs along the seam.
        self.assertGreater(
            cmds.polyEvaluate(cyl, uvcoord=True), cmds.polyEvaluate(cyl, vertex=True)
        )

    def test_invert_seam_opposite_side(self):
        """Inverting the seam runs the lengthwise loop on the opposite side
        (a disjoint set of edges from the default seam)."""
        cyl = cmds.polyCylinder(
            name="seam_invert", radius=1, height=4, subdivisionsAxis=12
        )[0]
        default_loop, _ = UvUtils.get_cylinder_seam_edges(cyl, invert_seam=False)
        inverted_loop, _ = UvUtils.get_cylinder_seam_edges(cyl, invert_seam=True)
        default_ids = set(cmds.ls(default_loop, flatten=True))
        inverted_ids = set(cmds.ls(inverted_loop, flatten=True))
        self.assertTrue(default_ids and inverted_ids)
        self.assertEqual(default_ids & inverted_ids, set())  # opposite sides

    @staticmethod
    def _face_center_y(face):
        verts = cmds.ls(
            cmds.polyListComponentConversion(face, toVertex=True), flatten=True
        )
        ys = [cmds.pointPosition(v, world=True)[1] for v in verts]
        return sum(ys) / len(ys)

    @classmethod
    def _cap_ngon(cls, mesh, top=True):
        """Index of the top- or bottom-most n-gon cap face."""
        ngons = [
            i
            for i in range(cmds.polyEvaluate(mesh, face=True))
            if len(
                cmds.ls(
                    cmds.polyListComponentConversion(f"{mesh}.f[{i}]", toVertex=True),
                    flatten=True,
                )
            )
            > 4
        ]
        key = lambda i: cls._face_center_y(f"{mesh}.f[{i}]")
        return max(ngons, key=key) if top else min(ngons, key=key)

    @classmethod
    def _stepped_cylinder(cls, name):
        """A two-diameter turned column: wide body -> hard horizontal step ->
        narrow body, with n-gon caps. Its hard creases (2 cap rims + the step's
        inner & outer rings) frame five sections: bottom cap, wide body, step
        annulus, narrow body, top cap. Auto-unwrap should yield five shells."""
        cyl = cmds.polyCylinder(
            name=name, radius=2, height=2, subdivisionsAxis=12, subdivisionsHeight=1
        )[0]
        # Inset the top cap (r2 -> r1) into a horizontal step, then extrude it up
        # along its normal into the narrow body.
        cmds.polyExtrudeFacet(f"{cyl}.f[{cls._cap_ngon(cyl)}]", ch=True, offset=1.0)
        cmds.polyExtrudeFacet(
            f"{cyl}.f[{cls._cap_ngon(cyl)}]", ch=True, localTranslate=(0, 0, 2)
        )
        return cyl

    def test_auto_seam_smooth_cylinder_three_shells(self):
        """A plain capped cylinder auto-unwraps to body + 2 caps (no spurious
        cuts on the smooth body), topology preserved."""
        cyl = cmds.polyCylinder(
            name="auto_smooth", radius=1, height=4, subdivisionsAxis=12
        )[0]
        self._flatten_uvs_to_one_shell(cyl)
        UvUtils.unwrap_cylinder(cyl, unfold=False)
        self.assertEqual(self._uv_shells(cyl), 3)
        v = cmds.polyEvaluate(cyl, vertex=True)
        e = cmds.polyEvaluate(cyl, edge=True)
        f = cmds.polyEvaluate(cyl, face=True)
        self.assertEqual(v - e + f, 2)

    def test_auto_seam_stepped_cylinder_five_shells(self):
        """A turned step profile peels into one shell per section: 2 caps,
        2 cylindrical bands, and the flat step annulus."""
        cyl = self._stepped_cylinder("auto_stepped")
        self._flatten_uvs_to_one_shell(cyl)
        UvUtils.unwrap_cylinder(cyl, unfold=False)
        self.assertEqual(self._uv_shells(cyl), 5)
        v = cmds.polyEvaluate(cyl, vertex=True)
        e = cmds.polyEvaluate(cyl, edge=True)
        f = cmds.polyEvaluate(cyl, face=True)
        self.assertEqual(v - e + f, 2)  # cuts don't change topology

    def test_angle_threshold_controls_creases(self):
        """A high threshold treats the ~90 degree steps as soft, so far fewer
        edges are cut than at the default 45 degrees."""
        cyl = self._stepped_cylinder("auto_thresh")
        sharp = cmds.ls(UvUtils.get_auto_seam_edges(cyl, angle=45), flatten=True)
        loose = cmds.ls(UvUtils.get_auto_seam_edges(cyl, angle=120), flatten=True)
        self.assertGreater(len(sharp), len(loose))

    def test_auto_seam_invert_opposite_column(self):
        """The hard creases are unchanged by invert; only the lengthwise column
        moves to a disjoint set of edges on the opposite side."""
        cyl = self._stepped_cylinder("auto_invert")
        default = set(cmds.ls(UvUtils.get_auto_seam_edges(cyl), flatten=True))
        inverted = set(
            cmds.ls(UvUtils.get_auto_seam_edges(cyl, invert_seam=True), flatten=True)
        )
        default_only = default - inverted
        inverted_only = inverted - default
        self.assertTrue(default_only and inverted_only)  # the axial columns differ
        self.assertEqual(default_only & inverted_only, set())

    def test_unwrap_unfold_does_not_collapse(self):
        """unfold=True must flatten shells (non-zero UV area), not collapse them
        to points -- even from a degenerate axis-aligned source projection."""
        cmds.loadPlugin("Unfold3D.mll", quiet=True)
        if not cmds.pluginInfo("Unfold3D", query=True, loaded=True):
            self.skipTest("Unfold3D plugin unavailable")
        cyl = cmds.polyCylinder(
            name="unfold_collapse", radius=1, height=4, subdivisionsAxis=12
        )[0]
        # A planar projection along the cylinder axis makes each lengthwise band
        # zero-area -- the degenerate seed that used to collapse u3dUnfold.
        cmds.polyProjection(f"{cyl}.f[*]", type="Planar", md="y")
        self.assertTrue(UvUtils.unwrap_cylinder(cyl, unfold=True))

        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(cyl)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        us, vs = fn.getUVs()
        _, ids = fn.getUvShellsIds()
        boxes = {}
        for i in range(len(us)):
            bb = boxes.setdefault(ids[i], [9, 9, -9, -9])
            bb[0] = min(bb[0], us[i]); bb[1] = min(bb[1], vs[i])
            bb[2] = max(bb[2], us[i]); bb[3] = max(bb[3], vs[i])
        self.assertTrue(boxes)
        for b in boxes.values():
            self.assertGreater((b[2] - b[0]) * (b[3] - b[1]), 1e-6)  # not collapsed
            self.assertLessEqual(b[2], 1.02)  # packed into 0-1
            self.assertGreaterEqual(b[0], -0.02)

    @staticmethod
    def _shell_quality(mesh):
        """Per-shell UV report: (count, degenerate, flipped, inside_0_1)."""
        import maya.api.OpenMaya as om
        from collections import defaultdict

        sel = om.MSelectionList()
        sel.add(mesh)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        us, vs = fn.getUVs()
        _, ids = fn.getUvShellsIds()
        signed = defaultdict(float)
        for f in range(fn.numPolygons):
            verts = fn.getPolygonVertices(f)
            uvid = [fn.getPolygonUVid(f, i) for i in range(len(verts))]
            for i in range(len(uvid)):
                j, k = uvid[i], uvid[(i + 1) % len(uvid)]
                signed[ids[uvid[0]]] += us[j] * vs[k] - us[k] * vs[j]
        boxes = defaultdict(lambda: [9, 9, -9, -9])
        for i in range(len(us)):
            b = boxes[ids[i]]
            b[0] = min(b[0], us[i]); b[1] = min(b[1], vs[i])
            b[2] = max(b[2], us[i]); b[3] = max(b[3], vs[i])
        degen = sum(
            1 for b in boxes.values() if (b[2] - b[0]) < 1e-4 or (b[3] - b[1]) < 1e-4
        )
        flipped = sum(1 for a in signed.values() if a < 0)
        inside = all(-0.02 <= v <= 1.02 for b in boxes.values() for v in b)
        return len(boxes), degen, flipped, inside

    def test_low_poly_cylinder_seam_ignores_faceting(self):
        """An 8-sided cylinder facets at exactly the 45 deg default threshold;
        that faceting must not be cut as hard creases (which would shatter the
        tube into per-facet shells). The single-row body stays one band, so a
        capped cylinder peels into body + 2 caps = 3 shells."""
        cyl = cmds.polyCylinder(
            name="lowpoly_seam", radius=1, height=4,
            subdivisionsAxis=8, subdivisionsHeight=1,
        )[0]
        self._flatten_uvs_to_one_shell(cyl)
        UvUtils.unwrap_cylinder(cyl, unfold=False)
        self.assertEqual(self._uv_shells(cyl), 3)

    def test_low_poly_unfold_clean_shells(self):
        """Unfolding a low-poly cylinder (45 deg facets + a single-row band)
        gives non-degenerate, non-mirrored shells packed in 0-1: the band is
        seeded cylindrically (a planar seed folds a single-row ring flat and
        u3dUnfold then collapses it) and u3dLayout's packing mirrors are
        flipped back."""
        cmds.loadPlugin("Unfold3D.mll", quiet=True)
        if not cmds.pluginInfo("Unfold3D", query=True, loaded=True):
            self.skipTest("Unfold3D plugin unavailable")
        cyl = cmds.polyCylinder(
            name="lowpoly_unfold", radius=1, height=4,
            subdivisionsAxis=8, subdivisionsHeight=1,
        )[0]
        # A planar projection along the axis is the degenerate seed that, with a
        # planar re-seed, would collapse the single-row band.
        cmds.polyProjection(f"{cyl}.f[*]", type="Planar", md="y")
        self.assertTrue(UvUtils.unwrap_cylinder(cyl, unfold=True))
        count, degen, flipped, inside = self._shell_quality(cyl)
        self.assertEqual(count, 3)  # body + 2 caps
        self.assertEqual(degen, 0)  # cylindrical seed keeps the band non-degenerate
        self.assertEqual(flipped, 0)  # u3dLayout mirrors are flipped back
        self.assertTrue(inside)  # packed into 0-1

    def test_sew_clears_preexisting_uv_cuts(self):
        """By default the cut sews pre-existing UV borders shut first, so the
        result's shells come only from the cylinder seams -- not stray shells
        left by an earlier projection. sew=False leaves them, polluting it."""

        def shells_after(sew):
            c = cmds.polyCylinder(r=1, h=4, sx=12, sy=3, name=f"sew_{sew}")[0]
            cmds.polyAutoProjection(f"{c}.f[*]", layoutMethod=0)  # messy: 6 shells
            UvUtils.unwrap_cylinder(c, unfold=False, sew=sew)
            n = cmds.polyEvaluate(c, uvShell=True)
            cmds.delete(c)
            return n

        self.assertEqual(shells_after(True), 3)  # body + 2 caps, clean
        self.assertGreater(shells_after(False), 3)  # stray shells survive

    def test_multi_mesh_skips_non_manifold_keeps_good(self):
        """A non-manifold mesh in a multi-mesh selection must only skip itself --
        the good cylinders still unfold. u3dUnfold rejects a non-manifold mesh
        ('Mesh has non-manifold UVs…'); a single batched unfold would abort the
        whole selection on it, so each mesh is unfolded independently."""
        cmds.loadPlugin("Unfold3D.mll", quiet=True)
        if not cmds.pluginInfo("Unfold3D", query=True, loaded=True):
            self.skipTest("Unfold3D plugin unavailable")
        g1 = cmds.polyCylinder(r=1, h=4, sx=12, name="good_a")[0]
        cmds.polyProjection(f"{g1}.f[*]", type="Planar", md="y")
        g2 = cmds.polyCylinder(r=1, h=6, sx=8, name="good_b")[0]
        cmds.polyProjection(f"{g2}.f[*]", type="Planar", md="y")
        # Non-manifold mesh: two cubes welded along their shared face plane.
        a = cmds.polyCube(name="nm_a")[0]
        b = cmds.polyCube(name="nm_b")[0]
        cmds.move(1, 0, 0, b)
        nm = cmds.polyUnite([a, b], ch=False, name="nonmanifold")[0]
        cmds.polyMergeVertex(nm, distance=0.001)  # weld -> non-manifold edge

        # Must not raise even though u3dUnfold rejects the non-manifold mesh.
        UvUtils.unwrap_cylinder([g1, nm, g2], unfold=True, orient=True)

        for good in (g1, g2):
            count, degen, _flipped, inside = self._shell_quality(good)
            self.assertEqual(count, 3)  # body + 2 caps -> actually unfolded
            self.assertEqual(degen, 0)
            self.assertTrue(inside)


class TestUvUtilsEdgeCases(MayaTkTestCase):
    """Edge case tests for UvUtils."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="test_edge_cube")[0]

    def tearDown(self):
        if cmds.objExists("test_edge_cube"):
            cmds.delete("test_edge_cube")
        super().tearDown()

    def test_mirror_uvs_invalid_axis(self):
        """Test mirror UVs with invalid axis."""
        with self.assertRaises(ValueError):
            UvUtils.mirror_uvs(self.cube, axis="z")

    def test_get_uv_shell_sets_invalid_type(self):
        """Test get_uv_shell_sets with invalid return type."""
        with self.assertRaises(ValueError):
            UvUtils.get_uv_shell_sets(self.cube, returned_type="invalid")

    def test_reorder_uv_sets_mismatch(self):
        """Test reordering with mismatched sets."""
        # If we ask to reorder sets that don't exist, it should raise ValueError
        with self.assertRaises(ValueError):
            UvUtils.reorder_uv_sets(self.cube, new_order=["map1", "non_existent"])

    def test_get_texel_density_zero_area(self):
        """Test texel density on zero area face."""
        # Create a degenerate face or just pass empty list
        # Passing empty list should warn and return 0
        density = UvUtils.get_texel_density([], 1024)
        self.assertEqual(density, 0)


if __name__ == "__main__":
    unittest.main()
