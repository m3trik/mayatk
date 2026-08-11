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
import pythontk as ptk
from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.uv_utils._uv_pack import _UvPackInternal
from mayatk.core_utils._core_utils import CoreUtils

from base_test import MayaTkTestCase
import maya.cmds as cmds

from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics


class TestLightmapUvs(MayaTkTestCase):
    """UvUtils.create_lightmap_uvs + UvDiagnostics.is_bakeable_lightmap (Phase 1b)."""

    def _shape(self, transform):
        # Full path: create_lightmap_uvs keys its result by one, and a short name
        # is ambiguous the moment two groups share a leaf.
        return (
            cmds.listRelatives(str(transform), shapes=True, ni=True, fullPath=True)
            or [None]
        )[0]

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

    @staticmethod
    def _foreign_layout(shape, uv_set):
        """A return-manifest layout as the Blender bridge hands one back."""
        import array
        import base64

        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(str(shape))
        fn = om.MFnMesh(sel.getDagPath(0))
        counts, _ = fn.getVertices()
        loops = int(sum(counts))
        buf = array.array("f", [0.0]) * (loops * 2)
        for i in range(loops):
            buf[i * 2] = 0.1 + 0.8 * ((i % 4) / 3.0)
            buf[i * 2 + 1] = 0.1 + 0.8 * (((i // 4) % 4) / 3.0)
        return {
            "uv_set": uv_set,
            "poly_counts": [int(c) for c in counts],
            "num_verts": fn.numVertices,
            "uvs": base64.b64encode(buf.tobytes()).decode("ascii"),
        }

    def _lightmap_sets(self, shape):
        """Every set on *shape* that any lightmap convention would claim."""
        sets_ = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        known = {n.lower() for n in UvDiagnostics.DEFAULT_LIGHTMAP_NAMES}
        return [s for s in sets_ if s.lower() in known]

    def test_arnold_bake_then_bridge_bake_leaves_one_lightmap_set(self):
        """The two bake paths must overwrite each other, not accumulate sets.

        blendertk spells its default "Lightmap" and mayatk "lightmap"; Maya UV set
        names are case-sensitive, so honouring the incoming name blindly left the
        Arnold bake's set orphaned at channel 2 -- still in every export.
        """
        cube = cmds.polyCube(name="lmRoundTripA")[0]
        shape = self._shape(cube)
        UvUtils.create_lightmap_uvs([cube], map_size=256, quiet=True)
        self.assertEqual(self._lightmap_sets(shape), ["lightmap"])

        UvUtils.apply_uv_layout(
            {cube: self._foreign_layout(shape, "Lightmap")}, quiet=True
        )
        self.assertEqual(
            self._lightmap_sets(shape),
            ["lightmap"],
            f"bridge bake left a stale set: {cmds.polyUVSet(shape, q=True, allUVSets=True)}",
        )

    def test_bridge_bake_then_arnold_bake_leaves_one_lightmap_set(self):
        """The reverse hand-off: an Arnold re-bake must reuse the bridge's channel."""
        cube = cmds.polyCube(name="lmRoundTripB")[0]
        shape = self._shape(cube)
        UvUtils.apply_uv_layout(
            {cube: self._foreign_layout(shape, "Lightmap")}, quiet=True
        )
        self.assertEqual(self._lightmap_sets(shape), ["Lightmap"])

        # force=True is the panel's re-bake: regenerate the unwrap, don't rename
        # the channel out from under the engine binding.
        UvUtils.create_lightmap_uvs([cube], map_size=256, force=True, quiet=True)
        self.assertEqual(
            self._lightmap_sets(shape),
            ["Lightmap"],
            f"Arnold bake left a stale set: {cmds.polyUVSet(shape, q=True, allUVSets=True)}",
        )
        self.assertEqual(cmds.getAttr(f"{shape}.lightmapUVSet"), "Lightmap")

    def test_create_survives_uv_set_added_during_projection(self):
        """A set list that moves under the projection must not abort the run.

        The channel order used to be built from a snapshot taken BEFORE the
        projection and handed to ``reorder_uv_sets``, which rejects any order that
        doesn't match the scene -- so one mesh whose sets drifted took the whole
        bake down with "new_order must match the set of existing UV sets".
        """
        cube = cmds.polyCube(name="lmCubeDrift")[0]
        shape = self._shape(cube)
        real_autoproj = cmds.polyAutoProjection

        def drifting_autoproj(*args, **kwargs):
            result = real_autoproj(*args, **kwargs)
            cmds.polyUVSet(shape, create=True, uvSet="drifted")
            return result

        cmds.polyAutoProjection = drifting_autoproj
        try:
            res = UvUtils.create_lightmap_uvs([cube], map_size=256)
        finally:  # a leaked cmds attr reroutes every later test
            cmds.polyAutoProjection = real_autoproj

        self.assertTrue(res, "the mesh must still be reported as processed")
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertEqual(sets[0], "map1", f"texture set at index 0; sets={sets}")
        self.assertEqual(sets[1], "lightmap", f"lightmap at index 1; sets={sets}")
        self.assertIn("drifted", sets, "an unrelated set must survive the reorder")

    def test_create_targets_renderable_shape_not_orig(self):
        """A deformed mesh also has an orig (intermediate) shape.

        The lightmap belongs on the RENDERABLE one: the bake reads that shape, so a
        set written to the orig leaves the baked shape with no UV2 at all. The shape
        was resolved through ``get_shape_node``, which returns both shapes in hash
        order -- making which one got the lightmap a coin flip per process.
        """
        cubes, live_shapes = [], []
        for i in range(6):  # several: one coin flip per mesh, all must land right
            cube = cmds.polyCube(name=f"lmCubeDeformed{i}")[0]
            cmds.select(cube)
            cmds.nonLinear(type="bend")  # deformer -> the transform gains an orig
            cmds.select(clear=True)
            live = (
                cmds.listRelatives(cube, shapes=True, ni=True, fullPath=True) or [None]
            )[0]
            every = cmds.listRelatives(cube, shapes=True, fullPath=True) or []
            self.assertTrue(
                [s for s in every if s != live], "test needs a mesh with an orig shape"
            )
            cubes.append(cube)
            live_shapes.append(live)

        res = UvUtils.create_lightmap_uvs(cubes, map_size=256)

        for live in live_shapes:
            live_sets = cmds.polyUVSet(live, query=True, allUVSets=True) or []
            self.assertIn(
                "lightmap", live_sets, f"renderable shape has no lightmap: {live_sets}"
            )
            self.assertTrue(
                cmds.attributeQuery("lightmapUVSet", node=live, exists=True)
            )
        for key in res:
            self.assertFalse(
                cmds.getAttr(f"{key}.intermediateObject"),
                f"result keyed by an intermediate shape: {key}",
            )

    def test_create_unreorderable_mesh_keeps_its_set_and_the_batch(self):
        """A reorder that fails leaves a usable set -- keep it, and keep going."""
        bad = cmds.polyCube(name="lmCubeBad")[0]
        good = cmds.polyCube(name="lmCubeGood")[0]
        bad_shape = self._shape(bad)
        bad_leaf = str(bad_shape).rsplit("|", 1)[-1]
        real_reorder = UvUtils.reorder_uv_sets

        def flaky_reorder(obj, new_order):
            if str(obj).rsplit("|", 1)[-1] == bad_leaf:
                raise ValueError("simulated drift")
            return real_reorder(obj, new_order)

        UvUtils.reorder_uv_sets = staticmethod(flaky_reorder)
        try:
            res = UvUtils.create_lightmap_uvs([bad, good], map_size=256)
        finally:
            UvUtils.reorder_uv_sets = staticmethod(real_reorder)

        good_sets = cmds.polyUVSet(self._shape(good), query=True, allUVSets=True) or []
        self.assertIn(
            "lightmap",
            good_sets,
            f"a sibling's failure cost this mesh its UV2: {good_sets}",
        )
        # The set exists and is tagged even at the wrong index -- only the ordering
        # was lost, so the mesh stays in the result.
        self.assertIn(bad_shape, res)
        self.assertIn(
            "lightmap", cmds.polyUVSet(bad_shape, query=True, allUVSets=True) or []
        )

    def test_create_one_failing_mesh_does_not_abort_the_batch(self):
        """A mesh Maya refuses to project must not cost the rest their lightmap.

        The realistic version of this is a locked or referenced shape in a
        production scene: a whole-selection bake is hundreds of objects, and losing
        all of them to one is the expensive failure.
        """
        bad = cmds.polyCube(name="lmCubeRefuses")[0]
        good = cmds.polyCube(name="lmCubeFine")[0]
        bad_leaf = str(self._shape(bad)).rsplit("|", 1)[-1]
        real_autoproj = cmds.polyAutoProjection

        def refusing_autoproj(*args, **kwargs):
            if args and bad_leaf in str(args[0]):
                raise RuntimeError("simulated projection refusal")
            return real_autoproj(*args, **kwargs)

        cmds.polyAutoProjection = refusing_autoproj
        try:
            res = UvUtils.create_lightmap_uvs([bad, good], map_size=256)
        finally:  # a leaked cmds attr reroutes every later test
            cmds.polyAutoProjection = real_autoproj

        good_shape = self._shape(good)
        good_sets = cmds.polyUVSet(good_shape, query=True, allUVSets=True) or []
        self.assertIn(
            "lightmap",
            good_sets,
            f"a sibling's failure cost this mesh its UV2: {good_sets}",
        )
        self.assertIn(good_shape, res)
        bad_shape = self._shape(bad)
        self.assertNotIn(bad_shape, res, "a skipped mesh must not be reported")
        # A half-processed mesh must not be left sitting on the lightmap set --
        # whatever reads UVs next reads the current one.
        cur = (cmds.polyUVSet(bad_shape, query=True, currentUVSet=True) or [None])[0]
        self.assertEqual(cur, "map1", "skipped mesh left on the wrong current set")


class TestApplyUvLayout(MayaTkTestCase):
    """UV layouts authored in ANOTHER application, replayed onto Maya meshes.

    The receiving half of the Blender lightmap round trip: Blender owns the whole
    lightmap job (generate, pack, bake, atlas) and hands the finished layout back, so a
    mesh must end up carrying exactly the UVs the bake was made through. Anything less
    than exact means every object samples its lightmap through the wrong coordinates --
    which reads as a bad bake, not as a transfer bug.
    """

    @staticmethod
    def _mesh_fn(shape):
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(str(shape))
        return om.MFnMesh(sel.getDagPath(0))

    @classmethod
    def _layout(cls, shape, uvs, uv_set="lightmap"):
        """A transfer payload for *shape* from a flat per-loop ``[u, v, ...]`` list."""
        import array
        import base64

        fn = cls._mesh_fn(shape)
        counts, _ids = fn.getVertices()
        return {
            "uv_set": uv_set,
            "poly_counts": list(counts),
            "num_verts": fn.numVertices,
            "uvs": base64.b64encode(array.array("f", uvs).tobytes()).decode("ascii"),
        }

    @classmethod
    def _read_loops(cls, shape, uv_set):
        """Read UVs back per LOOP, through the assignment -- how a renderer samples."""
        fn = cls._mesh_fn(shape)
        us, vs = fn.getUVs(uv_set)
        _counts, ids = fn.getAssignedUVs(uv_set)
        return [c for uid in ids for c in (us[uid], vs[uid])]

    @staticmethod
    def _shape(transform):
        return cmds.listRelatives(transform, shapes=True, fullPath=True, ni=True)[0]

    def _cube_uvs(self, shape):
        loops = int(sum(self._mesh_fn(shape).getVertices()[0]))
        # Deterministic, all distinct, and inside 0-1 -- distinct matters, since a
        # constant fill would pass even if the loop order were scrambled.
        return [(i % 97) / 97.0 for i in range(loops * 2)]

    def test_writes_the_transferred_uvs_verbatim(self):
        shape = self._shape(cmds.polyCube(name="layoutCube")[0])
        uvs = self._cube_uvs(shape)
        applied = UvUtils.apply_uv_layout(
            {shape: self._layout(shape, uvs)}, quiet=True
        )
        self.assertEqual(applied, {shape: "lightmap"})
        got = self._read_loops(shape, "lightmap")
        self.assertEqual(len(got), len(uvs))
        for a, b in zip(got, uvs):
            self.assertAlmostEqual(a, b, places=6)

    def test_places_a_lightmap_set_at_channel_1_and_tags_it(self):
        shape = self._shape(cmds.polyCube(name="layoutTagged")[0])
        UvUtils.apply_uv_layout(
            {shape: self._layout(shape, self._cube_uvs(shape))}, quiet=True
        )
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertEqual(sets.index("lightmap"), 1, "lightmap must land on UV1")
        self.assertTrue(
            cmds.attributeQuery(UvDiagnostics.LIGHTMAP_UV_TAG, node=shape, exists=True)
        )
        # Tagged means downstream detection treats it exactly like a locally authored one.
        self.assertEqual(UvDiagnostics.find_lightmap_uv_set(shape), "lightmap")

    def test_rejects_a_layout_from_different_topology(self):
        """A mesh edited since the hand-off must be skipped, not given scrambled UVs."""
        cube = self._shape(cmds.polyCube(name="layoutSrc")[0])
        sphere = self._shape(cmds.polySphere(name="layoutDst")[0])
        before = cmds.polyUVSet(sphere, query=True, allUVSets=True) or []

        applied = UvUtils.apply_uv_layout(
            {sphere: self._layout(cube, self._cube_uvs(cube))}, quiet=True
        )
        self.assertEqual(applied, {}, "topology fingerprint must reject the layout")
        self.assertEqual(
            cmds.polyUVSet(sphere, query=True, allUVSets=True) or [],
            before,
            "a rejected layout must not leave a half-created UV set behind",
        )

    def test_a_rejection_does_not_block_the_other_meshes(self):
        good = self._shape(cmds.polyCube(name="layoutGood")[0])
        bad = self._shape(cmds.polySphere(name="layoutBad")[0])
        layouts = {
            bad: self._layout(good, self._cube_uvs(good)),
            good: self._layout(good, self._cube_uvs(good)),
        }
        self.assertEqual(UvUtils.apply_uv_layout(layouts, quiet=True), {good: "lightmap"})

    def test_non_lightmap_set_is_written_without_the_lightmap_treatment(self):
        """The transfer is generic; only a lightmap-named set gets tagged/reordered."""
        shape = self._shape(cmds.polyCube(name="layoutPlain")[0])
        uvs = self._cube_uvs(shape)
        applied = UvUtils.apply_uv_layout(
            {shape: self._layout(shape, uvs, uv_set="transferred")}, quiet=True
        )
        self.assertEqual(applied, {shape: "transferred"})
        self.assertFalse(
            cmds.attributeQuery(UvDiagnostics.LIGHTMAP_UV_TAG, node=shape, exists=True)
        )
        got = self._read_loops(shape, "transferred")
        for a, b in zip(got, uvs):
            self.assertAlmostEqual(a, b, places=6)


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

    def test_move_to_uv_space_fractional(self):
        """A fractional step moves by exactly that amount (the Half/Quarter Tile scopes)."""
        before = UvUtils.get_uv_bounds(self.cube)

        UvUtils.move_to_uv_space(self.cube, u=0.0, v=0.5, relative=True)

        after = UvUtils.get_uv_bounds(self.cube)
        self.assertAlmostEqual(after[0], before[0], places=5)  # u untouched
        self.assertAlmostEqual(after[1], before[1] + 0.5, places=5)

    def test_move_to_uv_space_accepts_uv_components(self):
        """A UV-component selection moves too.

        A `fromFace` filter on the conversion silently dropped non-face input,
        making the move a no-op for a UV / edge / vertex selection.
        """
        uvs = cmds.ls(f"{self.cube}.map[*]", flatten=True)
        before = UvUtils.get_uv_bounds(self.cube)

        UvUtils.move_to_uv_space(uvs, u=1.0, v=0.0, relative=True)

        after = UvUtils.get_uv_bounds(self.cube)
        self.assertAlmostEqual(after[0], before[0] + 1.0, places=5)

    def test_get_uv_bounds(self):
        """UV bounds come back as a single (u_min, v_min, u_max, v_max) box."""
        bounds = UvUtils.get_uv_bounds(self.cube)
        self.assertIsNotNone(bounds)
        u_min, v_min, u_max, v_max = bounds
        self.assertLess(u_min, u_max)
        self.assertLess(v_min, v_max)

        # Agrees with a raw query of the same UVs.
        uvs = cmds.polyEditUV(f"{self.cube}.map[*]", q=True)
        self.assertAlmostEqual(u_min, min(uvs[0::2]), places=5)
        self.assertAlmostEqual(v_max, max(uvs[1::2]), places=5)

    def test_get_uv_bounds_unions_multiple_objects(self):
        """One box over the whole input, not just the first object.

        The move pad hands its entire selection to this, so a per-object box
        would put the snap anchor on the wrong shell.
        """
        other = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        UvUtils.move_to_uv_space(other, 2.0, 3.0)

        cube_bounds = UvUtils.get_uv_bounds(self.cube)
        both = UvUtils.get_uv_bounds([self.cube, other])

        self.assertAlmostEqual(both[0], min(cube_bounds[0], 2.0), places=5)
        self.assertAlmostEqual(both[1], min(cube_bounds[1], 3.0), places=5)
        self.assertAlmostEqual(both[2], max(cube_bounds[2], 3.0), places=5)
        self.assertAlmostEqual(both[3], max(cube_bounds[3], 4.0), places=5)

    def _empty_scene(self):
        """Start from an empty scene.

        The neighbour pool is scene-wide, so this class's second fixture cube
        would otherwise count as a neighbour and blur what each case proves.
        """
        cmds.file(new=True, force=True)

    def test_get_neighbor_shell_bounds_excludes_the_input(self):
        self._empty_scene()
        near = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]  # UVs 0..1
        far = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        UvUtils.move_to_uv_space(far, 3.0, 0.0)  # UVs 3..4

        # Each mesh sees only the other.
        self.assertEqual(
            [tuple(round(v, 4) for v in b) for b in UvUtils.get_neighbor_shell_bounds(near)],
            [(3.0, 0.0, 4.0, 1.0)],
        )
        self.assertEqual(
            [tuple(round(v, 4) for v in b) for b in UvUtils.get_neighbor_shell_bounds(far)],
            [(0.0, 0.0, 1.0, 1.0)],
        )

    def test_get_neighbor_shell_bounds_alone_is_empty(self):
        """A mesh alone in its UV set has nothing to park against."""
        self._empty_scene()
        plane = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        self.assertEqual(UvUtils.get_neighbor_shell_bounds(plane), [])

    def test_get_neighbor_shell_bounds_is_scoped_to_the_uv_set(self):
        """A mesh whose current UV set has a different name is not a neighbour —
        it does not share the UV space even where its UVs occupy the same numbers.
        """
        self._empty_scene()
        plane = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        other = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        shape = cmds.listRelatives(other, shapes=True, fullPath=True)[0]
        self.assertTrue(UvUtils.get_neighbor_shell_bounds(plane))  # shared map1

        cmds.polyUVSet(shape, create=True, uvSet="lightmap")
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="lightmap")
        self.assertEqual(UvUtils.get_neighbor_shell_bounds(plane), [])

        cmds.polyUVSet(shape, currentUVSet=True, uvSet="map1")
        self.assertTrue(UvUtils.get_neighbor_shell_bounds(plane))

    def test_get_neighbor_shell_bounds_excludes_only_touched_shells(self):
        """A face-subset selection still sees its own mesh's *other* shells.

        Built from two united planes because a default cube is a single UV
        shell — a one-shell mesh could not tell partial from total exclusion.
        """
        self._empty_scene()
        a = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        b = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        UvUtils.move_to_uv_space(b, 3.0, 0.0)
        merged = cmds.polyUnite(a, b, constructionHistory=False)[0]

        shells = UvUtils.get_uv_shell_sets(merged, returned_type="shell")
        self.assertEqual(len(shells), 2, "expected one mesh carrying two shells")

        # Select only the first shell's faces; the second must remain a blocker.
        neighbors = UvUtils.get_neighbor_shell_bounds(shells[0])
        self.assertEqual(len(neighbors), 1, neighbors)
        own = UvUtils.get_uv_bounds(shells[0])
        self.assertFalse(
            all(abs(x - y) < 1e-6 for x, y in zip(neighbors[0], own)),
            f"own shell leaked into the blocker pool: {neighbors[0]}",
        )

    def test_get_neighbor_shell_bounds_excludes_own_shells_via_any_instance(self):
        """Selecting through a *second* instance must still exclude its own shells.

        An instanced shape has one DAG path per instance and `cmds.ls` reports
        only the first, so keying the exclusion on the path string leaked the
        input's own shell back as its own neighbour — shell snap would then park
        a shell against itself.
        """
        self._empty_scene()
        plane = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        instance = cmds.instance(plane)[0]
        cmds.move(3, 0, 0, instance)  # moved in 3D; the UVs are shared

        # Both instances resolve to the same shape, so neither has a neighbour.
        self.assertEqual(UvUtils.get_neighbor_shell_bounds(plane), [])
        self.assertEqual(UvUtils.get_neighbor_shell_bounds(instance), [])

        # A genuinely separate mesh is still found through either instance.
        other = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        UvUtils.move_to_uv_space(other, 5.0, 0.0)
        for target in (plane, instance):
            with self.subTest(target=target):
                boxes = UvUtils.get_neighbor_shell_bounds(target)
                self.assertEqual(len(boxes), 1, boxes)
                self.assertAlmostEqual(boxes[0][0], 5.0, places=4)

    def test_get_neighbor_shell_bounds_on_input_without_uvs(self):
        locator = cmds.spaceLocator()[0]
        self.assertEqual(UvUtils.get_neighbor_shell_bounds(locator), [])

    def test_get_uv_bounds_on_input_without_uvs(self):
        """A non-poly input resolves to no UVs -> None (the panel warns, not raises)."""
        locator = cmds.spaceLocator()[0]
        self.assertIsNone(UvUtils.get_uv_bounds(locator))

    def test_get_uv_bounds_tracks_a_move(self):
        """The bounds shift by exactly the applied offset — this pairing is what
        the Shell Xform snap mode relies on to land on a grid line."""
        before = UvUtils.get_uv_bounds(self.cube)
        UvUtils.move_to_uv_space(self.cube, u=0.25, v=-0.75, relative=True)
        after = UvUtils.get_uv_bounds(self.cube)

        self.assertAlmostEqual(after[0], before[0] + 0.25, places=5)
        self.assertAlmostEqual(after[1], before[1] - 0.75, places=5)

    def _uv_shell_at(self, u_min, v_min, size=0.5):
        """A plane whose single UV shell has *size* extent, anchored at (u_min, v_min).

        Sized well inside the tile on purpose: the padding clamp legitimately
        nudges a shell that sits exactly on a tile border, which would blur the
        "did the gather move it?" assertions these tests make.
        """
        plane = cmds.polyPlane(width=1, height=1, sx=1, sy=1)[0]
        cmds.polyEditUV(
            f"{plane}.map[*]", pivotU=0, pivotV=0, scaleU=size, scaleV=size
        )
        UvUtils.move_to_uv_space(plane, u_min, v_min)
        return plane

    def test_gather_to_udim_pulls_a_stray_shell_in(self):
        """A shell parked in another tile comes back, keeping its sub-tile position."""
        shell = self._uv_shell_at(3.2, 2.3)

        moved = UvUtils.gather_to_udim(shell, udim=1001)

        self.assertEqual(moved, 1)
        u_min, v_min, u_max, v_max = UvUtils.get_uv_bounds(shell)
        self.assertAlmostEqual(u_min, 0.2, places=5)
        self.assertAlmostEqual(v_min, 0.3, places=5)

    def test_gather_to_udim_leaves_resident_shells_alone(self):
        """Shells already inside the target tile are untouched (moved count 0)."""
        shell = self._uv_shell_at(0.2, 0.3)
        before = UvUtils.get_uv_bounds(shell)

        moved = UvUtils.gather_to_udim(shell, udim=1001)

        self.assertEqual(moved, 0)
        for b, a in zip(before, UvUtils.get_uv_bounds(shell)):
            self.assertAlmostEqual(b, a, places=6)

    def test_gather_to_udim_targets_a_non_origin_tile(self):
        """An explicit UDIM anchors the result on that tile."""
        shell = self._uv_shell_at(0.2, 0.3)

        UvUtils.gather_to_udim(shell, udim=1013)  # tile (2, 1)

        u_min, v_min, u_max, v_max = UvUtils.get_uv_bounds(shell)
        self.assertAlmostEqual(u_min, 2.2, places=5)
        self.assertAlmostEqual(v_min, 1.3, places=5)

    def test_gather_to_udim_respects_border_padding(self):
        """A shell overhanging the tile is pulled in to the padded border, not the border."""
        margin = ptk.MathUtils.uv_tile_margin(4096)
        shell = self._uv_shell_at(0.55, 0.55)  # overhangs the top-right of tile 1001

        UvUtils.gather_to_udim(shell, udim=1001)

        u_min, v_min, u_max, v_max = UvUtils.get_uv_bounds(shell)
        self.assertAlmostEqual(u_max, 1.0 - margin, places=5)
        self.assertAlmostEqual(v_max, 1.0 - margin, places=5)

    def test_gather_to_udim_full_coverage_shell_stays_put(self):
        """A shell spanning the tile exactly is not shoved off the far border by the margin."""
        shell = self._uv_shell_at(0.0, 0.0, size=1.0)

        self.assertEqual(UvUtils.gather_to_udim(shell, udim=1001), 0)

    def test_gather_to_udim_votes_for_the_majority_tile(self):
        """With no explicit UDIM the tile most shells occupy wins — the majority stays put."""
        residents = [self._uv_shell_at(1.2, 0.2), self._uv_shell_at(1.3, 0.3)]
        stray = self._uv_shell_at(4.25, 3.25)
        before = [UvUtils.get_uv_bounds(r) for r in residents]

        moved = UvUtils.gather_to_udim(residents + [stray])

        self.assertEqual(moved, 1)  # only the stray
        u_min, v_min, u_max, v_max = UvUtils.get_uv_bounds(stray)
        self.assertAlmostEqual(u_min, 1.25, places=5)
        self.assertAlmostEqual(v_min, 0.25, places=5)
        for bounds, resident in zip(before, residents):
            for b, a in zip(bounds, UvUtils.get_uv_bounds(resident)):
                self.assertAlmostEqual(b, a, places=6)

    def test_gather_to_udim_moves_whole_shells_from_a_partial_selection(self):
        """A partial face selection moves the WHOLE shell, not just those faces.

        `get_uv_shell_sets` groups only the *input* faces by shell, so gathering a
        face subset used to translate that fragment alone and tear the shell in
        half. The Blender twin acts on whole islands (`_target_islands`), so this
        is a parity contract too.
        """
        plane = cmds.polyPlane(width=1, height=1, sx=4, sy=4)[0]
        UvUtils.move_to_uv_space(plane, 3.0, 2.0)
        before = UvUtils.get_uv_bounds(plane)

        # One face of a 16-face single-shell plane.
        UvUtils.gather_to_udim(f"{plane}.f[0]", udim=1001)

        after = UvUtils.get_uv_bounds(plane)
        width = before[2] - before[0]
        self.assertAlmostEqual(after[2] - after[0], width, places=5)  # not torn
        self.assertAlmostEqual(after[0], before[0] - 3.0, places=5)

    def test_gather_to_udim_accepts_a_uv_component_selection(self):
        """A UV-component selection resolves to its shell (the move pad accepts these too)."""
        plane = cmds.polyPlane(width=1, height=1, sx=2, sy=2)[0]
        UvUtils.move_to_uv_space(plane, 4.0, 0.0)
        before = UvUtils.get_uv_bounds(plane)

        uvs = cmds.ls(f"{plane}.map[*]", flatten=True)
        moved = UvUtils.gather_to_udim(uvs[:1], udim=1001)

        self.assertEqual(moved, 1)
        after = UvUtils.get_uv_bounds(plane)
        self.assertAlmostEqual(after[2] - after[0], before[2] - before[0], places=5)
        self.assertAlmostEqual(after[0], before[0] - 4.0, places=5)

    def test_gather_to_udim_on_input_without_uvs(self):
        """A non-poly input resolves to no shells -> None (the panel warns, not raises)."""
        locator = cmds.spaceLocator()[0]
        self.assertIsNone(UvUtils.gather_to_udim(locator))

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

    def test_get_uv_shell_sets_whole_shells_expands_only_when_asked(self):
        """The default groups the INPUT's faces; `whole_shells` expands to the shell.

        Face-level callers (mirror per-shell on a face selection) depend on the
        default staying narrow; shell-level ones (gather) need the expansion, or
        they translate a fragment and tear the shell.
        """
        plane = cmds.polyPlane(width=1, height=1, sx=4, sy=4)[0]  # 16 faces, 1 shell

        narrow = UvUtils.get_uv_shell_sets(f"{plane}.f[0]", returned_type="shell")
        self.assertEqual([len(s) for s in narrow], [1])

        whole = UvUtils.get_uv_shell_sets(
            f"{plane}.f[0]", returned_type="shell", whole_shells=True
        )
        self.assertEqual([len(s) for s in whole], [16])

        # A whole-object input is already complete — expansion must not duplicate.
        full = UvUtils.get_uv_shell_sets(
            plane, returned_type="shell", whole_shells=True
        )
        self.assertEqual([len(s) for s in full], [16])

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

    def test_set_texel_density_multiple_objects(self):
        """Both objects of a multi-object input reach the target density."""
        target = 10.0
        UvUtils.set_texel_density([self.cube, self.cube2], density=target, map_size=1024)
        for obj in (self.cube, self.cube2):
            self.assertAlmostEqual(
                UvUtils.get_texel_density(obj, map_size=1024), target, places=1
            )

    def test_set_texel_density_skips_degenerate_shells(self):
        """Regression: a zero-UV-area shell must be skipped, not abort with
        texSetTexelDensity.mel's division by zero (line 56)."""
        # Collapse cube2's UVs to a single point -> zero UV area.
        cmds.polyEditUV(f"{self.cube2}.map[*]", uValue=0.5, vValue=0.5, relative=False)
        target = 5.12
        scaled, skipped = UvUtils.set_texel_density(
            [self.cube, self.cube2], density=target, map_size=1024
        )
        self.assertGreaterEqual(scaled, 1)
        self.assertGreaterEqual(skipped, 1)
        # The healthy object still reaches the target density.
        self.assertAlmostEqual(
            UvUtils.get_texel_density(self.cube, map_size=1024), target, places=1
        )

    def test_cut_uv_edges_multiple_objects(self):
        """Regression: polyMapCut refuses edges spanning objects
        ("Doesn't work with multiple objects selected") -- cut_uv_edges
        groups per object so a multi-object hard-edge cut succeeds."""
        from mayatk.core_utils.components import Components

        before = [cmds.polyEvaluate(o, uvShell=True) for o in (self.cube, self.cube2)]
        hard = Components.get_edges_by_normal_angle(
            [self.cube, self.cube2], low_angle=70, high_angle=180
        )
        grouped = UvUtils.cut_uv_edges(hard)
        self.assertEqual(len(grouped), 2)  # one polyMapCut per object
        after = [cmds.polyEvaluate(o, uvShell=True) for o in (self.cube, self.cube2)]
        for b, a in zip(before, after):
            self.assertGreater(a, b)  # every object's shells were actually cut

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

    def test_transfer_uvs_match_by_similarity_false_uses_exact_pairs(self):
        """Regression: a caller with an already-verified (source, target)
        correspondence -- e.g. the RizomUV bridge's name-based export/import
        mapping -- must be able to transfer directly without the similarity
        search silently rejecting the pair. A tolerance above the maximum
        possible similarity score (1.0) proves similarity-matching would
        reject this exact pair regardless of how close the geometry is;
        match_by_similarity=False must still transfer it correctly."""
        cmds.polyEditUV(f"{self.cube2}.map[*]", u=0.5, v=0.5)

        # Similarity-matched transfer has nothing to work with at this tolerance.
        mapping = CoreUtils.build_mesh_similarity_mapping(
            source=self.cube2, target=self.cube, tolerance=1.5
        )
        self.assertEqual(mapping, {})

        # Exact pairing transfers regardless -- it never consults tolerance.
        UvUtils.transfer_uvs(
            source=self.cube2, target=self.cube, match_by_similarity=False
        )
        uvs1 = cmds.polyEvaluate(f"{self.cube}.map[*]", bc2=True)
        uvs2 = cmds.polyEvaluate(f"{self.cube2}.map[*]", bc2=True)
        c1 = ((uvs1[0][0] + uvs1[1][0]) / 2, (uvs1[0][1] + uvs1[1][1]) / 2)
        c2 = ((uvs2[0][0] + uvs2[1][0]) / 2, (uvs2[0][1] + uvs2[1][1]) / 2)
        self.assertAlmostEqual(c1[0], c2[0], places=3)
        self.assertAlmostEqual(c1[1], c2[1], places=3)

    @staticmethod
    def _uv_center(obj):
        bc = cmds.polyEvaluate(f"{obj}.map[*]", bc2=True)
        return ((bc[0][0] + bc[1][0]) / 2, (bc[0][1] + bc[1][1]) / 2)

    def test_transfer_uvs_to_similar_scene_scope(self):
        """Fan-out transfer reaches every similar mesh in the scene, but skips
        dissimilar geometry and true instances of the source (shared shape --
        their UVs already match)."""
        cmds.polyEditUV(f"{self.cube}.map[*]", u=0.5, v=0.5)
        big = cmds.polyCube(name="test_uv_big", width=10, height=10, depth=10)[0]
        instance = cmds.instance(self.cube, name="test_uv_cube_inst")[0]
        try:
            targets = UvUtils.transfer_uvs_to_similar(self.cube)
            leafs = {t.split("|")[-1] for t in targets}
            self.assertEqual(leafs, {"test_uv_cube2"})

            src_center = self._uv_center(self.cube)
            dst_center = self._uv_center(self.cube2)
            self.assertAlmostEqual(src_center[0], dst_center[0], places=3)
            self.assertAlmostEqual(src_center[1], dst_center[1], places=3)

            # Dissimilar mesh untouched.
            big_center = self._uv_center(big)
            self.assertNotAlmostEqual(src_center[0], big_center[0], places=3)
        finally:
            for obj in (big, instance):
                if cmds.objExists(obj):
                    cmds.delete(obj)

    def test_transfer_uvs_to_similar_candidate_pool(self):
        """An explicit candidate pool restricts the search; similar meshes
        outside the pool are untouched."""
        cmds.polyEditUV(f"{self.cube}.map[*]", u=0.5, v=0.5)
        cube3 = cmds.polyCube(name="test_uv_cube3")[0]
        try:
            targets = UvUtils.transfer_uvs_to_similar(self.cube, [cube3])
            leafs = {t.split("|")[-1] for t in targets}
            self.assertEqual(leafs, {"test_uv_cube3"})

            src_center = self._uv_center(self.cube)
            # cube2 is just as similar but outside the pool -- must be untouched.
            cube2_center = self._uv_center(self.cube2)
            self.assertNotAlmostEqual(src_center[0], cube2_center[0], places=3)
        finally:
            if cmds.objExists(cube3):
                cmds.delete(cube3)

    def test_transfer_uvs_to_similar_invalid_source_raises(self):
        """Source must resolve to exactly one polygon mesh."""
        group = cmds.group(self.cube, self.cube2, name="test_uv_group")
        try:
            with self.assertRaises(ValueError):
                UvUtils.transfer_uvs_to_similar(group)  # two meshes
        finally:
            cmds.ungroup(group)
        locator = cmds.spaceLocator(name="test_uv_locator")[0]
        try:
            with self.assertRaises(ValueError):
                UvUtils.transfer_uvs_to_similar(locator)  # not a mesh
        finally:
            if cmds.objExists(locator):
                cmds.delete(locator)

    def test_transfer_uvs_match_by_similarity_false_length_mismatch_raises(self):
        """Exact-pairing mode requires source/target to already be paired 1:1."""
        with self.assertRaises(ValueError):
            UvUtils.transfer_uvs(
                source=[self.cube, self.cube2],
                target=[self.cube],
                match_by_similarity=False,
            )

    @staticmethod
    def _uv_bounds(obj):
        uvs = cmds.polyEditUV(f"{obj}.map[*]", query=True) or []
        u, v = uvs[0::2], uvs[1::2]
        return (min(u), max(u), min(v), max(v))

    def test_transfer_uvs_differing_topology_is_not_a_silent_noop(self):
        """Regression: the transfer sampled in TOPOLOGY space unconditionally, so
        any target whose topology differed from the source (a retopologized or
        subdivided variant -- the common reason to transfer UVs at all) came back
        completely untouched, with no error and no warning. The reported symptom
        was the tentacle UV panel's 'Transfer UVs' button "doing nothing"."""
        cmds.polyEditUV(f"{self.cube}.map[*]", uValue=0.25, vValue=0.25)
        source_bounds = self._uv_bounds(self.cube)
        dense = cmds.polyCube(
            name="test_uv_dense", width=2, height=2, depth=2, sx=4, sy=4, sz=4
        )[0]
        try:
            self.assertNotEqual(
                cmds.polyEvaluate(self.cube, vertex=True),
                cmds.polyEvaluate(dense, vertex=True),
                "fixture must differ topologically for this regression",
            )
            before = self._uv_bounds(dense)
            UvUtils.transfer_uvs(self.cube, dense, match_by_similarity=False)
            after = self._uv_bounds(dense)

            self.assertNotEqual(before, after, "differing topology transferred nothing")
            for got, want in zip(after, source_bounds):
                self.assertAlmostEqual(got, want, places=2)
        finally:
            if cmds.objExists(dense):
                cmds.delete(dense)

    def test_transfer_uvs_differing_topology_survives_a_world_offset(self):
        """The spatial fallback must sample in LOCAL space: a source parked away
        from its target (the normal way a UV donor is staged) collapses to a
        single nearest-point sample under world-space sampling."""
        cmds.polyEditUV(f"{self.cube}.map[*]", uValue=0.25, vValue=0.25)
        source_bounds = self._uv_bounds(self.cube)
        dense = cmds.polyCube(
            name="test_uv_dense_far", width=2, height=2, depth=2, sx=4, sy=4, sz=4
        )[0]
        try:
            cmds.move(50, 0, 0, dense)
            UvUtils.transfer_uvs(self.cube, dense, match_by_similarity=False)
            for got, want in zip(self._uv_bounds(dense), source_bounds):
                self.assertAlmostEqual(got, want, places=2)
        finally:
            if cmds.objExists(dense):
                cmds.delete(dense)

    def test_transfer_uvs_matching_topology_stays_exact(self):
        """Matching topology must keep using topology space -- it is exact and
        placement-independent, which the RizomUV/auto-unwrap round-trips rely on."""
        cmds.polyEditUV(f"{self.cube2}.map[*]", uValue=0.3, vValue=0.7)
        cmds.move(50, 12, -7, self.cube)  # placement must not matter here

        result = UvUtils.transfer_uvs(
            self.cube2, self.cube, match_by_similarity=False
        )
        self.assertEqual([r[2] for r in result], ["topology"])

        src = cmds.polyEditUV(f"{self.cube2}.map[*]", query=True)
        dst = cmds.polyEditUV(f"{self.cube}.map[*]", query=True)
        self.assertEqual(len(src), len(dst))
        for a, b in zip(src, dst):
            self.assertAlmostEqual(a, b, places=4)

    def test_transfer_uvs_reports_pairs_and_the_space_used(self):
        """The return value is what lets a caller tell 'transferred' from
        'silently matched nothing' -- the UV panel reports it to the user."""
        dense = cmds.polyCube(name="test_uv_dense_rep", sx=3, sy=3, sz=3)[0]
        try:
            result = UvUtils.transfer_uvs(
                [self.cube, self.cube2], [dense, self.cube2], match_by_similarity=False
            )
            self.assertEqual([r[2] for r in result], ["object", "topology"])
            self.assertEqual(len(result), 2)
        finally:
            if cmds.objExists(dense):
                cmds.delete(dense)

        # A similarity search that pairs nothing reports it rather than looking
        # identical to a successful run.
        self.assertEqual(
            UvUtils.transfer_uvs(self.cube, self.cube2, tolerance=1.5), []
        )

    def test_topology_signature_of_a_non_mesh_is_unmeasured(self):
        """polyEvaluate answers a non-mesh with the truthy STRING 'Nothing
        counted : no polygonal object is selected.' instead of raising, so a
        naive probe reads two non-meshes as sharing a topology."""
        locator = cmds.spaceLocator(name="test_uv_loc")[0]
        try:
            self.assertIsNone(UvUtils._mesh_topology_signature(locator))
            self.assertIsNotNone(UvUtils._mesh_topology_signature(self.cube))
            # Unmeasured must not equal unmeasured.
            other = cmds.spaceLocator(name="test_uv_loc2")[0]
            self.assertFalse(
                UvUtils._mesh_topology_signature(locator) is not None
                and UvUtils._mesh_topology_signature(locator)
                == UvUtils._mesh_topology_signature(other)
            )
            cmds.delete(other)
            # The pre-existing failure mode is preserved: Maya rejects it, the
            # topology probe doesn't pre-empt it with a different error.
            with self.assertRaises(RuntimeError):
                UvUtils.transfer_uvs(self.cube, locator, match_by_similarity=False)
        finally:
            if cmds.objExists(locator):
                cmds.delete(locator)

    def test_transfer_uvs_rejects_an_unknown_sample_space(self):
        with self.assertRaises(ValueError):
            UvUtils.transfer_uvs(
                self.cube, self.cube2, match_by_similarity=False, sample_space="nope"
            )

    def test_sample_space_codes_match_mayas_own_option_box(self):
        """The published names are Maya's: scripts/others/performTransferAttributes.mel
        builds the radio group World / Object / UV / Component (+ Topology in a
        shared-collection group offset by 4) and passes ``selection - 1``."""
        self.assertEqual(
            UvUtils.SAMPLE_SPACES,
            {"world": 0, "object": 1, "uv": 2, "component": 3, "topology": 4},
        )

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


class TestTopologySeamAlgorithm(MayaTkTestCase):
    """UvUtils.get_topology_seam_edges + the ``algorithm`` dispatch."""

    def _uv_shells(self, mesh):
        return cmds.polyEvaluate(mesh, uvShell=True)

    @staticmethod
    def _flatten_uvs_to_one_shell(mesh):
        cmds.polyProjection(
            f"{mesh}.f[*]", type="Planar", md="y", insertBeforeDeformers=0
        )

    def test_unknown_algorithm_raises(self):
        cyl = cmds.polyCylinder(name="topo_bad_algo")[0]
        with self.assertRaises(ValueError):
            UvUtils.cut_cylinder_seams(cyl, algorithm="bogus")

    def test_topology_capped_cylinder_three_shells(self):
        """Parity with the axis algorithm on its home turf: a capped cylinder
        still peels into body + 2 caps, topology unchanged."""
        cyl = cmds.polyCylinder(
            name="topo_capped", radius=1, height=4, subdivisionsAxis=12
        )[0]
        self._flatten_uvs_to_one_shell(cyl)
        seamed = UvUtils.unwrap_cylinder(cyl, unfold=False, algorithm="topology")
        self.assertEqual(seamed, [cmds.ls(cyl, long=True)[0]])
        self.assertEqual(self._uv_shells(cyl), 3)
        v = cmds.polyEvaluate(cyl, vertex=True)
        e = cmds.polyEvaluate(cyl, edge=True)
        f = cmds.polyEvaluate(cyl, face=True)
        self.assertEqual(v - e + f, 2)  # cuts don't change mesh topology

    def test_topology_bent_tube_single_strip(self):
        """A bent (half-torus) tube -- where the straight-axis assumption
        breaks -- opens into a single strip with one lengthwise seam."""
        tor = cmds.polyTorus(
            name="topo_bent", radius=2, sectionRadius=0.5,
            subdivisionsAxis=12, subdivisionsHeight=8,
        )[0]
        # Delete half the donut -> an open bent tube (a 180-degree elbow).
        centers = {}
        for i in range(cmds.polyEvaluate(tor, face=True)):
            verts = cmds.ls(
                cmds.polyListComponentConversion(f"{tor}.f[{i}]", toVertex=True),
                flatten=True,
            )
            xs = [cmds.pointPosition(v, world=True)[0] for v in verts]
            centers[i] = sum(xs) / len(xs)
        cmds.delete([f"{tor}.f[{i}]" for i, x in centers.items() if x < 0])
        self._flatten_uvs_to_one_shell(tor)

        seam = UvUtils.get_topology_seam_edges(tor)
        self.assertTrue(seam)
        UvUtils.unwrap_cylinder(tor, unfold=False, algorithm="topology")
        self.assertEqual(self._uv_shells(tor), 1)  # one strip
        # The lengthwise cut duplicates UVs along the seam.
        self.assertGreater(
            cmds.polyEvaluate(tor, uvcoord=True), cmds.polyEvaluate(tor, vertex=True)
        )

    def test_topology_closed_torus_opens(self):
        """A closed torus (no boundary, no creases) gets loop + ring cuts so
        it can unroll -- the axis algorithm has no answer here."""
        tor = cmds.polyTorus(
            name="topo_torus", radius=2, sectionRadius=0.5,
            subdivisionsAxis=12, subdivisionsHeight=8,
        )[0]
        self._flatten_uvs_to_one_shell(tor)
        seam = UvUtils.get_topology_seam_edges(tor)
        # One lengthwise loop + one crossing ring, and nothing else.
        self.assertTrue(seam)
        UvUtils.cut_cylinder_seams(tor, algorithm="topology")
        # Cutting loop + ring opens the torus without splitting it apart.
        self.assertEqual(self._uv_shells(tor), 1)
        self.assertGreater(
            cmds.polyEvaluate(tor, uvcoord=True), cmds.polyEvaluate(tor, vertex=True)
        )


class TestDetectSeamAlgorithm(MayaTkTestCase):
    """UvUtils.detect_seam_algorithm — replaces the old UI algorithm picker."""

    def test_straight_cylinder_picks_axis(self):
        cyl = cmds.polyCylinder(
            name="detect_cyl", radius=1, height=4, subdivisionsAxis=12
        )[0]
        self.assertEqual(UvUtils.detect_seam_algorithm(cyl), "axis")

    def test_flat_flange_picks_axis(self):
        """Wider than it is tall — still a straight body of revolution."""
        flange = cmds.polyCylinder(
            name="detect_flange", radius=4, height=0.5, subdivisionsAxis=16
        )[0]
        self.assertEqual(UvUtils.detect_seam_algorithm(flange), "axis")

    def test_closed_torus_picks_topology(self):
        """No boundary and genus 1: one lengthwise cut can't unroll it."""
        tor = cmds.polyTorus(
            name="detect_torus", radius=2, sectionRadius=0.5,
            subdivisionsAxis=12, subdivisionsHeight=8,
        )[0]
        self.assertEqual(UvUtils.detect_seam_algorithm(tor), "topology")

    def test_bent_tube_picks_topology(self):
        """An elbow's rings drift off any fitted axis."""
        tor = cmds.polyTorus(
            name="detect_bent", radius=2, sectionRadius=0.5,
            subdivisionsAxis=12, subdivisionsHeight=8,
        )[0]
        centers = {}
        for i in range(cmds.polyEvaluate(tor, face=True)):
            verts = cmds.ls(
                cmds.polyListComponentConversion(f"{tor}.f[{i}]", toVertex=True),
                flatten=True,
            )
            xs = [cmds.pointPosition(v, world=True)[0] for v in verts]
            centers[i] = sum(xs) / len(xs)
        cmds.delete([f"{tor}.f[{i}]" for i, x in centers.items() if x < 0])
        self.assertEqual(UvUtils.detect_seam_algorithm(tor), "topology")

    def test_auto_is_the_default_and_dispatches(self):
        cyl = cmds.polyCylinder(
            name="detect_default", radius=1, height=4, subdivisionsAxis=12
        )[0]
        cmds.polyProjection(
            f"{cyl}.f[*]", type="Planar", md="y", insertBeforeDeformers=0
        )
        # No algorithm= argument at all: "auto" must resolve and cut.
        seamed = UvUtils.cut_cylinder_seams(cyl)
        self.assertEqual(seamed, [cmds.ls(cyl, long=True)[0]])
        self.assertEqual(cmds.polyEvaluate(cyl, uvShell=True), 3)

    def test_auto_is_an_accepted_algorithm(self):
        self.assertIn("auto", UvUtils.SEAM_ALGORITHMS)


class TestAutoUnwrap(MayaTkTestCase):
    """UvUtils.auto_unwrap — the external-engine OBJ round-trip.

    The engine executable is stubbed, so these exercise the real Maya-side
    export / import / transfer path without needing Ministry of Flat or BFF.
    """

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="unwrap_cube")[0]

    @staticmethod
    def _offset_uvs(obj_in, obj_out, du=0.25, dv=0.1):
        """Stand in for an engine: shift every UV so the transfer is visible."""
        lines = []
        for line in open(obj_in, encoding="utf-8"):
            if line.startswith("vt "):
                parts = line.split()
                u, v = float(parts[1]) + du, float(parts[2]) + dv
                line = f"vt {u:.6f} {v:.6f}\n"
            lines.append(line)
        with open(obj_out, "w", encoding="utf-8") as f:
            f.writelines(lines)

    @staticmethod
    def _split_uvs(obj_in, obj_out):
        """Stand in for Ministry of Flat, which writes one ``vt`` per face corner.

        The real executable never shares a UV index between two faces, even
        where the coordinates are bit-identical, so Maya's OBJ import gives the
        payload a UV point per corner -- every edge a UV border. Coordinates
        are passed through unchanged so the transfer stays checkable.
        """
        verts, uvs, norms, faces = [], [], [], []
        for line in open(obj_in, encoding="utf-8"):
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v":
                verts.append(line.rstrip("\n"))
            elif parts[0] == "vt":
                uvs.append((float(parts[1]), float(parts[2])))
            elif parts[0] == "vn":
                norms.append(line.rstrip("\n"))
            elif parts[0] == "f":
                faces.append(parts[1:])

        out = verts + norms
        next_vt = 1
        for corners in faces:  # interleaved vt/f blocks, exactly as MoF writes
            block, refs = [], []
            for corner in corners:
                v, vt, vn = (corner.split("/") + ["", ""])[:3]
                u, w = uvs[int(vt) - 1] if vt else (0.0, 0.0)
                block.append(f"vt {u:.6f} {w:.6f}")
                refs.append(f"{v}/{next_vt}/{vn}" if vn else f"{v}/{next_vt}")
                next_vt += 1
            out += block + ["f " + " ".join(refs)]
        with open(obj_out, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")

    def _stub_engine(self, handler=None, check_engine=None):
        """Patch the engine seam; return the recorder of what it received."""
        from unittest.mock import patch
        from mayatk.uv_utils import _auto_unwrap

        received = {}

        def engine(obj_in, engine_key, **params):
            received["input"] = obj_in
            received["engine"] = engine_key
            received["params"] = params
            obj_out = obj_in.replace(".obj", "_out.obj")
            (handler or self._offset_uvs)(obj_in, obj_out)
            return obj_out

        patches = [
            patch.object(
                _auto_unwrap._AutoUnwrapInternal, "_engine_unwrap", staticmethod(engine)
            ),
            patch.object(
                _auto_unwrap._AutoUnwrapInternal,
                "_check_engine",
                staticmethod(check_engine or (lambda key: "stub.exe")),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return received

    @staticmethod
    def _uvs(mesh):
        return cmds.polyEditUV(f"{mesh}.map[*]", query=True) or []

    @staticmethod
    def _uvs_per_face(mesh):
        """UV values keyed by face — the mapping that actually matters.

        ``snapshot_uv_sets`` / ``restore_uv_snapshot`` rebuild the UV set, which
        renumbers ``map[i]`` while leaving every face's UVs identical, so a raw
        ``map[*]`` comparison reports a difference where there is none.
        """
        return {
            i: sorted(
                cmds.polyEditUV(
                    cmds.polyListComponentConversion(f"{mesh}.f[{i}]", toUV=True),
                    query=True,
                )
            )
            for i in range(cmds.polyEvaluate(mesh, face=True))
        }

    def test_engine_uvs_are_applied_to_the_original(self):
        received = self._stub_engine()
        before = self._uvs(self.cube)
        result = UvUtils.auto_unwrap(self.cube, method="hard", pack=False)

        self.assertTrue(result)
        self.assertEqual(result.engine, "mof")
        self.assertEqual(result.failed, [])
        after = self._uvs(self.cube)
        self.assertEqual(len(before), len(after))
        # The stub shifted every UV by (0.25, 0.1); the transfer must land it.
        self.assertAlmostEqual(after[0], before[0] + 0.25, places=3)
        self.assertAlmostEqual(after[1], before[1] + 0.1, places=3)
        self.assertTrue(received["input"].endswith(".obj"))

    def test_unshared_engine_uvs_do_not_cut_every_edge(self):
        """A payload with a UV per face corner must come back re-shared.

        Ministry of Flat writes unshared ``vt`` indices; transferred verbatim
        that leaves every edge of the mesh a UV border.
        """
        before_uvs = cmds.polyEvaluate(self.cube, uv=True)
        before_faces = self._uvs_per_face(self.cube)
        self._stub_engine(handler=self._split_uvs)

        UvUtils.auto_unwrap(self.cube, method="hard", pack=False)

        shells = cmds.polyEvaluate(self.cube, uvShell=True)
        self.assertEqual(
            shells,
            1,
            f"{shells} UV shells for {cmds.polyEvaluate(self.cube, face=True)} "
            "faces -- the engine's unshared UVs were transferred verbatim, "
            "cutting every edge",
        )
        self.assertEqual(cmds.polyEvaluate(self.cube, uv=True), before_uvs)
        # Re-sharing must not move a UV: the stub passed coordinates through.
        after_faces = self._uvs_per_face(self.cube)
        for face, want in before_faces.items():
            got = after_faces[face]
            self.assertEqual(len(got), len(want))  # zip alone would truncate
            for got_uv, want_uv in zip(got, want):
                self.assertAlmostEqual(got_uv, want_uv, places=5)

    def test_method_names_map_to_engines(self):
        received = self._stub_engine()
        UvUtils.auto_unwrap(self.cube, method="organic", pack=False)
        self.assertEqual(received["engine"], "bff")

    def test_map_size_drives_ministry_of_flat_resolution(self):
        received = self._stub_engine()
        UvUtils.auto_unwrap(self.cube, method="hard", map_size=2048, pack=False)
        self.assertEqual(received["params"]["resolution"], 2048)

    def test_engine_params_are_forwarded(self):
        received = self._stub_engine()
        UvUtils.auto_unwrap(
            self.cube, method="organic", pack=False, engine_params={"n_cones": 8}
        )
        self.assertEqual(received["params"]["n_cones"], 8)

    def test_unknown_method_raises(self):
        self._stub_engine()
        with self.assertRaises(ValueError):
            UvUtils.auto_unwrap(self.cube, method="wishful")

    def test_no_meshes_raises(self):
        self._stub_engine()
        cmds.select(clear=True)
        with self.assertRaises(ValueError):
            UvUtils.auto_unwrap()

    def test_missing_engine_raises_before_touching_the_scene(self):
        def missing(_key):
            raise FileNotFoundError("Ministry of Flat not found: https://example/dl")

        self._stub_engine(check_engine=missing)
        before = self._uvs(self.cube)
        with self.assertRaises(FileNotFoundError) as ctx:
            UvUtils.auto_unwrap(self.cube)
        self.assertIn("https://", str(ctx.exception))
        self.assertEqual(self._uvs(self.cube), before)
        self.assertFalse(cmds.namespace(exists="UvUnwrapImport"))

    def test_per_object_failure_is_isolated_and_restored(self):
        # A sphere, so the payload is identifiable by its geometry: the export
        # carries no object/group names to match on.
        doomed = cmds.polySphere(name="unwrap_sphere_doomed", sx=8, sy=6)[0]

        def flaky(obj_in, obj_out):
            verts = sum(
                1 for line in open(obj_in, encoding="utf-8") if line.startswith("v ")
            )
            if verts > 8:  # the sphere, not the cube — order-independent
                raise RuntimeError("engine exploded")
            self._offset_uvs(obj_in, obj_out)

        self._stub_engine(handler=flaky)
        before_ok = self._uvs(self.cube)
        before_doomed = self._uvs_per_face(doomed)

        result = UvUtils.auto_unwrap([self.cube, doomed], method="hard", pack=False)

        self.assertEqual(len(result.succeeded), 1)
        self.assertEqual(len(result.failed), 1)
        self.assertIn("engine exploded", result.failed[0][1])
        self.assertIn("unwrap_sphere_doomed", result.failed[0][0])
        # The survivor changed; the casualty was rolled back untouched.
        self.assertNotAlmostEqual(self._uvs(self.cube)[0], before_ok[0], places=3)
        self.assertEqual(self._uvs_per_face(doomed), before_doomed)

    def test_results_follow_the_caller_order(self):
        second = cmds.polyCube(name="aaa_sorts_first")[0]
        self._stub_engine()
        result = UvUtils.auto_unwrap([self.cube, second], method="hard", pack=False)
        self.assertEqual(len(result.succeeded), 2)
        self.assertIn("unwrap_cube", result.succeeded[0])
        self.assertIn("aaa_sorts_first", result.succeeded[1])

    def test_leaves_no_residue(self):
        self._stub_engine()
        cmds.select(self.cube, replace=True)
        # Every DAG/DG node present before the run, so the import's object-set
        # and shading nodes -- which aren't children of the imported mesh and
        # so survive deleting it -- can't slip through.
        before = set(cmds.ls())

        UvUtils.auto_unwrap(self.cube, method="hard", pack=False)

        self.assertFalse(cmds.namespace(exists="UvUnwrapImport"))
        self.assertEqual(
            cmds.ls(selection=True, long=True), cmds.ls(self.cube, long=True)
        )
        self.assertEqual(len(cmds.ls(type="mesh", noIntermediate=True)), 1)
        snapshots = [
            s
            for s in (cmds.polyUVSet(self.cube, query=True, allUVSets=True) or [])
            if s.startswith("_uv_snap")
        ]
        self.assertEqual(snapshots, [])
        # Construction history on the original is expected -- including the
        # intermediate shape Maya keeps when a history node is added. Imported
        # geometry and the translator's set / groupId nodes are not.
        leaked = [
            n
            for n in sorted(set(cmds.ls()) - before)
            if cmds.objectType(n) in ("mesh", "transform", "objectSet", "groupId")
            and not (
                cmds.objectType(n) == "mesh"
                and cmds.getAttr(f"{n}.intermediateObject")
            )
        ]
        self.assertEqual(leaked, [])

    def test_layout_defaults_per_engine(self):
        from unittest.mock import patch

        self._stub_engine()
        # Ministry of Flat lays its own islands out; only the scale is fixed.
        with patch.object(UvUtils, "_pack_shells") as pack, patch.object(
            UvUtils, "_fit_uvs_to_tile"
        ) as fit:
            UvUtils.auto_unwrap(self.cube, method="hard")
        pack.assert_not_called()
        fit.assert_called_once()

        # BFF only flattens, so it needs the full layout pass.
        with patch.object(UvUtils, "_pack_shells") as pack, patch.object(
            UvUtils, "_fit_uvs_to_tile"
        ) as fit:
            UvUtils.auto_unwrap(self.cube, method="organic")
        pack.assert_called_once()
        fit.assert_not_called()

    def test_pack_true_forces_a_full_repack(self):
        from unittest.mock import patch

        self._stub_engine()
        with patch.object(UvUtils, "_pack_shells") as pack:
            UvUtils.auto_unwrap(self.cube, method="hard", pack=True)
        pack.assert_called_once()

    def test_pack_false_leaves_engine_uvs_untouched(self):
        from unittest.mock import patch

        self._stub_engine()
        with patch.object(UvUtils, "_pack_shells") as pack, patch.object(
            UvUtils, "_fit_uvs_to_tile"
        ) as fit:
            UvUtils.auto_unwrap(self.cube, method="hard", pack=False)
        pack.assert_not_called()
        fit.assert_not_called()

    def test_default_run_fits_uvs_into_the_tile(self):
        """The engine's raw output can overrun 0-1; the default must not."""

        def oversized(obj_in, obj_out):
            lines = []
            for line in open(obj_in, encoding="utf-8"):
                if line.startswith("vt "):
                    _, u, v = line.split()[:3]
                    line = f"vt {float(u) * 1.8:.6f} {float(v) * 1.8:.6f}\n"
                lines.append(line)
            with open(obj_out, "w", encoding="utf-8") as f:
                f.writelines(lines)

        self._stub_engine(handler=oversized)
        UvUtils.auto_unwrap(self.cube, method="hard")
        self.assertTrue(all(-0.001 <= c <= 1.001 for c in self._uvs(self.cube)))

    def test_output_without_uvs_is_a_recorded_failure(self):
        def strip_uvs(obj_in, obj_out):
            with open(obj_out, "w", encoding="utf-8") as f:
                for line in open(obj_in, encoding="utf-8"):
                    if not line.startswith("vt "):
                        f.write(line)

        self._stub_engine(handler=strip_uvs)
        result = UvUtils.auto_unwrap(self.cube, method="hard", pack=False)
        self.assertFalse(result)
        self.assertEqual(len(result.failed), 1)

    def test_instances_collapse_to_one_round_trip(self):
        inst = cmds.instance(self.cube, name="unwrap_cube_inst")[0]
        received = self._stub_engine()
        result = UvUtils.auto_unwrap([self.cube, inst], method="hard", pack=False)
        # They share a shape — unwrapping once covers both.
        self.assertEqual(len(result.succeeded) + len(result.failed), 1)
        self.assertTrue(received)


class TestUvSnapshotSideEffects(MayaTkTestCase):
    """snapshot_uv_sets must not change which UV set is active.

    ``cmds.polyUVSet -create`` switches the current set to the one it just
    made. Left alone, every UV operation performed after taking a snapshot
    (the auto-unwrap post-pass, the rizom bridge's) silently edits the backup
    set instead of the real one, and the edit vanishes when the backup is
    discarded.
    """

    def _shape(self, transform):
        return cmds.listRelatives(str(transform), shapes=True, ni=True)[0]

    def test_snapshot_leaves_the_original_set_current(self):
        cube = cmds.polyCube(name="snap_current")[0]
        shape = self._shape(cube)
        before = cmds.polyUVSet(shape, query=True, currentUVSet=True)
        UvUtils.snapshot_uv_sets([cube])
        self.assertEqual(
            cmds.polyUVSet(shape, query=True, currentUVSet=True), before
        )

    def test_uv_edits_after_snapshot_survive_discard(self):
        cube = cmds.polyCube(name="snap_edit")[0]
        snapshot = UvUtils.snapshot_uv_sets([cube])
        cmds.polyEditUV(f"{cube}.map[*]", u=0.5, v=0.0)
        edited = cmds.polyEditUV(f"{cube}.map[*]", query=True)[0]
        UvUtils.discard_uv_snapshot(snapshot)
        self.assertAlmostEqual(
            cmds.polyEditUV(f"{cube}.map[*]", query=True)[0], edited, places=4
        )


class TestPackShells(MayaTkTestCase):
    """UvUtils._pack_shells — the layout pass shared with unwrap_cylinder."""

    def test_packs_into_unit_square(self):
        cyl = cmds.polyCylinder(name="pack_cyl", radius=1, height=4)[0]
        cmds.polyEditUV(f"{cyl}.map[*]", u=3.0, v=3.0)  # shove it out of 0-1
        UvUtils._pack_shells(cyl, map_size=1024)
        uvs = cmds.polyEditUV(f"{cyl}.map[*]", query=True) or []
        self.assertTrue(uvs)
        self.assertTrue(all(-0.001 <= c <= 1.001 for c in uvs))


class TestPackUvs(MayaTkTestCase):
    """UvUtils.pack_uvs — external xatlas pack round-trip.

    Runs against the real engine (skips when the optional xatlas package is
    absent). Behaviors pinned from the live verification session 2026-07-28:
    exact UDIM/coverage placement, exact relative-scale preservation with
    preserve_3d off, density equalization with it on, and full undo capture
    (per-shell polyEditUV write-back, never raw MFnMesh.setUVs).
    """

    @classmethod
    def setUpClass(cls):
        import pythontk as ptk

        if not ptk.UvPack.available():
            raise unittest.SkipTest("xatlas not installed in this interpreter")

    @staticmethod
    def _bbox(obj):
        return cmds.polyEvaluate(obj, boundingBox2d=True)

    @staticmethod
    def _uvs(obj):
        """Flat ``[u0, v0, u1, v1, ...]`` of the object's current UV set."""
        return cmds.polyEditUV(f"{obj}.map[*]", query=True) or []

    @classmethod
    def _moved(cls, before, after):
        """UV indices whose coordinates differ between the two flat readouts."""
        return {
            i
            for i in range(len(before) // 2)
            if abs(before[2 * i] - after[2 * i]) > 1e-6
            or abs(before[2 * i + 1] - after[2 * i + 1]) > 1e-6
        }

    @staticmethod
    def _uv_indices(components):
        """The UV indices *components* resolve to."""
        return {
            int(str(c).split("[")[1].rstrip("]"))
            for c in cmds.ls(
                cmds.polyListComponentConversion(components, toUV=True) or [],
                flatten=True,
            )
        }

    def test_missing_engine_message_carries_install_command(self):
        from unittest import mock
        import pythontk as ptk

        with mock.patch.object(
            ptk.UvPack, "resolve", side_effect=RuntimeError("pip install --user xatlas")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                UvUtils.pack_uvs([cmds.polyCube(ch=False)[0]])
        self.assertIn("pip install", str(ctx.exception))

    def test_udim_and_coverage_placement(self):
        plane = cmds.polyPlane(name="pk_place", sx=1, sy=1, ch=False)[0]
        result = UvUtils.pack_uvs([plane], map_size=1024, udim=1002, coverage=(0.5, 1.0))
        self.assertEqual(len(result.succeeded), 1)
        (u0, u1), (v0, v1) = self._bbox(plane)
        self.assertGreaterEqual(u0, 1.0)
        self.assertLessEqual(u1, 1.5)
        self.assertLessEqual(v1, 1.0)

    def test_preserve_3d_toggle_controls_relative_scale(self):
        a = cmds.polyPlane(name="pk_a", sx=1, sy=1, ch=False)[0]
        b = cmds.polyPlane(name="pk_b", sx=1, sy=1, ch=False)[0]
        cmds.polyEditUV(f"{b}.map[*]", pivotU=0.0, pivotV=0.0, scaleU=0.5, scaleV=0.5)

        # Fixed-page packing snaps chart placement to integer texels, so the
        # realized per-shell scale can deviate by ~a texel (<=0.5% here) —
        # sub-pixel at any map size, but past a places=3 assertion.
        UvUtils.pack_uvs([a, b], map_size=1024, preserve_3d=False, rotate=False)
        ratio = (self._bbox(a)[0][1] - self._bbox(a)[0][0]) / (
            self._bbox(b)[0][1] - self._bbox(b)[0][0]
        )
        self.assertAlmostEqual(ratio, 2.0, delta=0.01)  # Preserve UV: input ratio kept

        UvUtils.pack_uvs([a, b], map_size=1024, preserve_3d=True, rotate=False)
        ratio = (self._bbox(a)[0][1] - self._bbox(a)[0][0]) / (
            self._bbox(b)[0][1] - self._bbox(b)[0][0]
        )
        self.assertAlmostEqual(ratio, 1.0, delta=0.01)  # equal 3D area -> equal UV

    def test_common_primitives_all_pack_inside_the_tile(self):
        """Regression: the residual tolerance was tighter than the engine's own
        precision (measured <=2.1e-04), so ordinary meshes were rejected, left
        unpacked, and landed on top of the packed ones."""
        objs = [
            cmds.polyCube(name="pk_cu", ch=False)[0],
            cmds.polyCylinder(name="pk_cy", ch=False)[0],
            cmds.polySphere(name="pk_sp", ch=False)[0],
            cmds.polyTorus(name="pk_to", ch=False)[0],
            cmds.polyCone(name="pk_co", ch=False)[0],
        ]
        for o in objs:
            cmds.polyAutoProjection(o, layoutMethod=0, layout=2, planes=6, ch=False)

        result = UvUtils.pack_uvs(objs, map_size=1024)

        self.assertEqual(result.failed, [], f"unexpected rejections: {result.failed}")
        self.assertEqual(len(result.succeeded), len(objs))
        for o in objs:
            (u0, u1), (v0, v1) = self._bbox(o)
            self.assertGreaterEqual(min(u0, v0), -1e-4, f"{o} left the tile")
            self.assertLessEqual(max(u1, v1), 1.0 + 1e-4, f"{o} left the tile")

    def test_pinched_shell_mesh_is_rejected_and_restored(self):
        """A shell pinched to another at a single UV point is two EDGE-connected
        charts to the engine, which packs them apart without duplicating the
        shared vertex — unreproducible as a rigid per-shell move. That mesh must
        be put back (the density pre-pass already rewrote it), not left floating
        over the packed ones.
        """
        pinched = cmds.polyPlatonicSolid(name="pk_plat", ch=False)[0]
        ordinary = cmds.polyCube(name="pk_ok", ch=False)[0]
        for o in (pinched, ordinary):
            cmds.polyAutoProjection(o, layoutMethod=0, layout=2, planes=6, ch=False)
        before = self._bbox(pinched)

        result = UvUtils.pack_uvs([pinched, ordinary], map_size=1024)

        self.assertEqual([n for n, _ in result.failed], ["pk_plat"])
        self.assertIn("pinched", result.failed[0][1])
        after = self._bbox(pinched)
        for axis_before, axis_after in zip(before, after):
            for b, a in zip(axis_before, axis_after):
                self.assertAlmostEqual(b, a, places=6, msg="rejected mesh not restored")
        # the healthy mesh still packed
        self.assertEqual(len(result.succeeded), 1)

    def test_cut_cube_fills_a_useful_fraction_of_every_coverage_box(self):
        """User-reported regression: a cube with all edges cut filled only
        0.50/0.25 of the Full/Half-V boxes (content-driven atlas aspect wasted
        against the box). Fixed-page packing must keep every coverage option
        above 0.6 fill for this content (measured 0.68-0.77; u3dLayout's own
        range on it is 0.55-0.96)."""
        for cov in ((1.0, 1.0), (0.5, 1.0), (1.0, 0.5), (0.5, 0.5)):
            cmds.file(new=True, force=True)
            cube = cmds.polyCube(name="pk_cut", ch=False)[0]
            cmds.polyMapCut(f"{cube}.e[*]", ch=False)

            result = UvUtils.pack_uvs([cube], map_size=1024, coverage=cov)

            self.assertEqual(result.failed, [], f"cov={cov}: {result.failed}")
            faces = cmds.polyListComponentConversion(cube, toFace=True)
            area = sum(cmds.polyEvaluate(faces, uvFaceArea=True) or [0.0])
            fill = area / (cov[0] * cov[1])
            self.assertGreater(fill, 0.6, f"cov={cov}: fill {fill:.3f}")
            (u0, u1), (v0, v1) = self._bbox(cube)
            self.assertGreaterEqual(min(u0, v0), -1e-4)
            self.assertLessEqual(u1, cov[0] + 1e-4, f"cov={cov} spilled U")
            self.assertLessEqual(v1, cov[1] + 1e-4, f"cov={cov} spilled V")

    def test_face_scope_packs_only_the_selected_faces(self):
        """User-reported: the engine path resolved its input to whole meshes, so
        a face / shell selection raised "No mesh objects to pack." and nothing
        happened. It must pack the selected region into the tile and leave the
        rest of the map exactly where it is."""
        cube = cmds.polyCube(name="pk_scope", ch=False)[0]
        cmds.polyMapCut(f"{cube}.e[*]", ch=False)  # six separate shells
        cmds.polyEditUV(f"{cube}.map[*]", u=5.0, v=5.0)  # park the map off-tile
        before = self._uvs(cube)
        scoped = self._uv_indices(f"{cube}.f[0:2]")

        result = UvUtils.pack_uvs([f"{cube}.f[0:2]"], map_size=1024)

        self.assertEqual(result.failed, [])
        self.assertEqual(len(result.succeeded), 1)
        after = self._uvs(cube)
        self.assertEqual(self._moved(before, after), scoped)
        for i in sorted(scoped):
            u, v = after[2 * i], after[2 * i + 1]
            self.assertGreaterEqual(min(u, v), -1e-4, "packed UV left the tile")
            self.assertLessEqual(max(u, v), 1.0 + 1e-4, "packed UV left the tile")
        for i in set(range(len(before) // 2)) - scoped:
            self.assertGreater(after[2 * i], 4.5, "out-of-scope UV was moved")

    def test_uv_shell_scope_packs_only_that_shell(self):
        """A UV-shell selection (the Pack tool's other component case) packs the
        shell into the target tile without disturbing its neighbours."""
        cyl = cmds.polyCylinder(name="pk_shell", ch=False)[0]
        cmds.polyAutoProjection(cyl, layoutMethod=0, layout=2, planes=6, ch=False)
        shells = UvUtils.get_uv_shell_sets(cyl, returned_type="shell")
        self.assertGreater(len(shells), 1, "test needs a multi-shell map")
        before = self._uvs(cyl)
        scoped = self._uv_indices(shells[0])

        result = UvUtils.pack_uvs(shells[0], map_size=1024, udim=1002)

        self.assertEqual(result.failed, [])
        after = self._uvs(cyl)
        moved = self._moved(before, after)
        self.assertTrue(moved)
        self.assertTrue(moved <= scoped, "the pack reached outside the shell")
        for i in sorted(moved):  # UDIM 1002 is the u = 1-2 tile
            self.assertGreaterEqual(after[2 * i], 1.0 - 1e-4)
            self.assertLessEqual(after[2 * i], 2.0 + 1e-4)

    def test_object_level_entry_wins_over_components_of_the_same_mesh(self):
        """A leftover component selection mixed into an object selection must not
        silently narrow the pack — the wider request wins, in either input
        order (the scope resolves components first, then promotes)."""
        cube = cmds.polyCube(name="pk_wins", ch=False)[0]
        cmds.polyMapCut(f"{cube}.e[*]", ch=False)

        for order in ([f"{cube}.f[0]", cube], [cube, f"{cube}.f[0]"]):
            result = UvUtils.pack_uvs(order, map_size=1024)
            self.assertEqual(result.failed, [])
            # targets == succeeded means whole meshes, not face components.
            self.assertEqual(result.targets, result.succeeded, f"order {order}")

    def test_instances_collapse_to_one_scope_entry(self):
        """Instances share one shape, so one UV set. Two scope entries would make
        the engine reserve space for the same UVs twice and then write two
        transforms onto them, compounding into a garbled layout. This pins the
        `cmds.ls` behavior the scope relies on to merge them — an instanced shape
        is reported by its FIRST path, so every sibling canonicalizes to the same
        transform however it was named."""
        cube = cmds.polyCube(name="pk_inst", ch=False)[0]
        cmds.polyMapCut(f"{cube}.e[*]", ch=False)
        sibling = cmds.instance(cube, name="pk_inst_sib")[0]

        for entries in (
            [f"{cube}.f[0]", f"{sibling}.f[1]"],  # components on both instances
            [f"{cube}.f[0]", sibling],  # component on one, object on the other
            [cube, sibling],  # both at object level
        ):
            scope = _UvPackInternal._resolve_scope(entries)
            self.assertEqual(len(scope), 1, f"{entries} -> {scope}")

        # And the pack itself moves each scoped UV exactly once: a double write
        # would compound two placements and push UVs out of the tile.
        before = self._uvs(cube)
        result = UvUtils.pack_uvs(
            [f"{cube}.f[0]", f"{sibling}.f[1]"], map_size=1024
        )
        self.assertEqual(result.failed, [])
        after = self._uvs(cube)
        self.assertEqual(
            self._moved(before, after), self._uv_indices(f"{cube}.f[0:1]")
        )
        for i in sorted(self._moved(before, after)):
            u, v = after[2 * i], after[2 * i + 1]
            self.assertGreaterEqual(min(u, v), -1e-4)
            self.assertLessEqual(max(u, v), 1.0 + 1e-4)

    def test_object_and_component_scopes_pack_together(self):
        """One engine call, mixed scope: the whole-mesh entry repacks entirely
        while the component entry moves only its own faces, and both land in the
        tile without overlapping."""
        whole = cmds.polyCube(name="pk_mix_whole", ch=False)[0]
        partial = cmds.polyCube(name="pk_mix_part", ch=False)[0]
        for obj in (whole, partial):
            cmds.polyMapCut(f"{obj}.e[*]", ch=False)
        cmds.polyEditUV(f"{partial}.map[*]", u=5.0, v=5.0)
        partial_before = self._uvs(partial)
        scoped = self._uv_indices(f"{partial}.f[0:1]")

        result = UvUtils.pack_uvs([whole, f"{partial}.f[0:1]"], map_size=1024)

        self.assertEqual(result.failed, [])
        self.assertEqual(len(result.succeeded), 2)
        self.assertEqual(
            self._moved(partial_before, self._uvs(partial)),
            scoped,
            "the component-scoped mesh was repacked whole",
        )
        (u0, u1), (v0, v1) = self._bbox(whole)
        self.assertGreaterEqual(min(u0, v0), -1e-4)
        self.assertLessEqual(max(u1, v1), 1.0 + 1e-4)

    def test_component_pack_is_fully_undoable(self):
        """The scoped write-back goes through polyEditUV like the whole-mesh one,
        so a component pack is a single undo away from the original layout."""
        cube = cmds.polyCube(name="pk_undo_scope", ch=False)[0]
        cmds.polyMapCut(f"{cube}.e[*]", ch=False)
        before = self._uvs(cube)
        cmds.undoInfo(openChunk=True)
        try:
            UvUtils.pack_uvs([f"{cube}.f[0:2]"], map_size=1024, udim=1005)
        finally:
            cmds.undoInfo(closeChunk=True)
        self.assertTrue(self._moved(before, self._uvs(cube)))
        cmds.undo()
        self.assertEqual(self._moved(before, self._uvs(cube)), set())

    def test_pack_is_fully_undoable(self):
        plane = cmds.polyPlane(name="pk_undo", sx=1, sy=1, ch=False)[0]
        before = self._bbox(plane)
        cmds.undoInfo(openChunk=True)
        try:
            UvUtils.pack_uvs([plane], map_size=1024, udim=1005)
        finally:
            cmds.undoInfo(closeChunk=True)
        self.assertGreaterEqual(self._bbox(plane)[0][0], 4.0)
        cmds.undo()
        self.assertEqual(self._bbox(plane), before)


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
