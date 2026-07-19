# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.core_utils.diagnostics submodules.

Covers:
    - AnimCurveDiagnostics (animation_diag.py)
    - MeshDiagnostics (mesh_diag.py)
    - TransformDiagnostics (transform_diag.py)
    - UvDiagnostics + UvSetCleanupResult (uv_diag.py)
"""
import unittest
import math

import maya.cmds as cmds

from mayatk.core_utils.diagnostics.animation_diag import AnimCurveDiagnostics
from mayatk.core_utils.diagnostics.mesh_diag import MeshDiagnostics
from mayatk.core_utils.diagnostics.transform_diag import TransformDiagnostics
from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics, UvSetCleanupResult

from base_test import MayaTkTestCase, QuickTestCase


class TestAnimCurveDiagnostics(MayaTkTestCase):
    """AnimCurveDiagnostics — corruption detection / repair."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="adc_cube")[0]

    def _key_curve(self, attr):
        cmds.setKeyframe(self.cube, attribute=attr, time=1, value=0.0)
        cmds.setKeyframe(self.cube, attribute=attr, time=10, value=1.0)
        connected = cmds.listConnections(
            f"{self.cube}.{attr}", type="animCurve", s=True, d=False
        )
        return connected[0] if connected else None

    def test_collect_anim_curves_empty_scene(self):
        # Fresh scene with no keys — nothing to collect from this object
        result = AnimCurveDiagnostics._collect_anim_curves([self.cube], recursive=False)
        self.assertEqual(result, [])

    def test_collect_anim_curves_finds_keyed_curves(self):
        self._key_curve("translateX")
        result = AnimCurveDiagnostics._collect_anim_curves([self.cube], recursive=False)
        self.assertTrue(len(result) >= 1)
        # All returned items should be animCurve nodes
        for n in result:
            self.assertTrue(cmds.nodeType(n).startswith("animCurve"))

    def test_repair_corrupted_curves_clean_scene_returns_zero(self):
        self._key_curve("translateX")
        stats = AnimCurveDiagnostics.repair_corrupted_curves(
            objects=[self.cube], quiet=True
        )
        self.assertEqual(stats["corrupted_found"], 0)
        self.assertEqual(stats["curves_repaired"], 0)
        self.assertEqual(stats["keys_fixed"], 0)

    def test_repair_corrupted_curves_no_curves_returns_empty_stats(self):
        stats = AnimCurveDiagnostics.repair_corrupted_curves(
            objects=[self.cube], quiet=True
        )
        self.assertEqual(stats["corrupted_found"], 0)
        self.assertIn("details", stats)
        self.assertIsInstance(stats["details"], list)

    def test_repair_corrupted_curves_extreme_value_detected(self):
        curve = self._key_curve("translateX")
        # Inject an extreme value via keyframe edit
        cmds.keyframe(curve, edit=True, valueChange=1e9, index=(0, 0))

        stats = AnimCurveDiagnostics.repair_corrupted_curves(
            objects=[self.cube],
            value_threshold=1e6,
            delete_corrupted=False,
            quiet=True,
        )
        self.assertGreaterEqual(stats["corrupted_found"], 1)

    def test_repair_visibility_tangents_returns_int(self):
        # No visibility curves yet — should return 0
        result = AnimCurveDiagnostics.repair_visibility_tangents(
            objects=[self.cube], quiet=True
        )
        self.assertIsInstance(result, int)
        self.assertEqual(result, 0)


class TestMeshDiagnostics(MayaTkTestCase):
    """MeshDiagnostics — clean_geometry + get_ngons."""

    def test_clean_geometry_empty_objects_raises(self):
        with self.assertRaises(ValueError):
            MeshDiagnostics.clean_geometry(objects=[])

    def test_clean_geometry_none_raises(self):
        with self.assertRaises(ValueError):
            MeshDiagnostics.clean_geometry(objects=None)

    def test_clean_geometry_runs_on_valid_mesh(self):
        cube = cmds.polyCube(name="mesh_diag_cube")[0]
        # Should not raise
        MeshDiagnostics.clean_geometry(
            objects=cube, repair=False, quads=True, nsided=True
        )

    def test_clean_geometry_select_mode_returns_and_keeps_component_selection(self):
        # Regression: Select mode (repair=False) must return the matched problem components AND
        # leave them selected. A trailing cmds.select(objects) used to clobber the diagnostic
        # selection, making "select only" a silent no-op. A single 5-sided facet is an n-gon.
        facet = cmds.polyCreateFacet(
            p=[(0, 0, 0), (2, 0, 0), (2, 2, 0), (1, 3, 0), (0, 2, 0)]
        )
        transform = facet[0]
        result = MeshDiagnostics.clean_geometry(transform, repair=False, nsided=True)
        self.assertIsInstance(result, list)
        self.assertTrue(result, "n-gon should be matched and returned in select mode")
        current = cmds.ls(selection=True, flatten=True) or []
        self.assertEqual(set(current), set(result))  # returned == what's left selected
        self.assertNotIn(transform, current)  # components, not the bare transform

    def test_clean_geometry_repair_mode_returns_empty(self):
        # Repair mode replaces geometry rather than selecting it: returns [] and reselects objects.
        cube = cmds.polyCube(name="mesh_diag_repair_cube")[0]
        result = MeshDiagnostics.clean_geometry(cube, repair=True, nsided=True)
        self.assertEqual(result, [])

    def test_get_ngons_returns_list(self):
        cube = cmds.polyCube(name="ngon_cube")[0]
        result = MeshDiagnostics.get_ngons(objects=cube, repair=False)
        # Plain quad cube has no n-gons
        self.assertIsInstance(result, list)

    def test_get_ngons_none_uses_selection(self):
        facet = cmds.polyCreateFacet(
            p=[(0, 0, 0), (2, 0, 0), (2, 2, 0), (1, 3, 0), (0, 2, 0)]
        )
        cmds.select(facet[0])
        result = MeshDiagnostics.get_ngons(objects=None, repair=False)
        self.assertTrue(result, "selection fallback should find the n-gon")

    def test_get_ngons_empty_selection_raises(self):
        cmds.select(clear=True)
        with self.assertRaises(ValueError):
            MeshDiagnostics.get_ngons(objects=None)


class TestTransformDiagnostics(MayaTkTestCase):
    """TransformDiagnostics — get_sheared + fix_non_orthogonal_axes."""

    def test_no_shear_no_action(self):
        cube = cmds.polyCube(name="td_clean")[0]
        # Cube has zero shear; running should be a no-op returning []
        result = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[cube], quiet=True
        )
        self.assertEqual(result, [])

        # Shear remains zero
        shear = cmds.xform(cube, q=True, shear=True)
        for s in shear:
            self.assertAlmostEqual(s, 0.0, places=6)

    def test_get_sheared_detects(self):
        clean = cmds.polyCube(name="td_gs_clean")[0]
        bad = cmds.polyCube(name="td_gs_bad")[0]
        cmds.xform(bad, shear=(0.5, 0.0, 0.0))

        sheared = TransformDiagnostics.get_sheared(objects=[clean, bad])
        self.assertEqual(sheared, [bad])

    def test_dry_run_reports_but_does_not_modify(self):
        cube = cmds.polyCube(name="td_dry")[0]
        cmds.xform(cube, shear=(0.5, 0.0, 0.0))
        before = cmds.xform(cube, q=True, shear=True)

        result = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[cube], dry_run=True, quiet=True
        )
        self.assertEqual(result, [cube])  # would-fix list

        after = cmds.xform(cube, q=True, shear=True)
        for a, b in zip(before, after):
            self.assertAlmostEqual(a, b, places=6)

    def test_fixes_shear_and_returns_fixed(self):
        cube = cmds.polyCube(name="td_fix")[0]
        cmds.xform(cube, shear=(0.5, 0.0, 0.0))

        result = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[cube], dry_run=False, quiet=True
        )
        self.assertEqual([r.split("|")[-1] for r in result], [cube])

        # After freeze, shear should be reduced near zero
        new_shear = cmds.xform(cube, q=True, shear=True)
        for s in new_shear:
            self.assertAlmostEqual(s, 0.0, places=4)

    def test_fixes_shear_on_instance(self):
        # Instanced transforms must be uninstanced before freezing; the
        # sibling instance keeps the original (shared) shape untouched.
        cube = cmds.polyCube(name="td_inst_src")[0]
        inst = cmds.instance(cube, name="td_inst_copy")[0]
        cmds.xform(inst, shear=(0.5, 0.0, 0.0))

        result = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[inst], quiet=True
        )
        self.assertEqual(len(result), 1)

        new_shear = cmds.xform(result[0], q=True, shear=True)
        for s in new_shear:
            self.assertAlmostEqual(s, 0.0, places=4)
        # The uninstanced transform no longer shares a shape with the source.
        self.assertFalse(
            set(cmds.listRelatives(result[0], shapes=True, fullPath=True) or [])
            & set(cmds.listRelatives(cube, shapes=True, fullPath=True) or [])
        )


class TestUvSetCleanupResult(QuickTestCase):
    """UvSetCleanupResult dataclass — pure Python, no Maya needed."""

    def test_default_values(self):
        r = UvSetCleanupResult(shape="pCubeShape1")
        self.assertEqual(r.shape, "pCubeShape1")
        self.assertEqual(r.initial_sets, [])
        self.assertIsNone(r.primary_set)
        self.assertEqual(r.sets_to_delete, [])
        self.assertEqual(r.final_name, "map1")
        self.assertFalse(r.success)
        self.assertIsNone(r.error)

    def test_str_with_error(self):
        r = UvSetCleanupResult(shape="X", error="boom")
        s = str(r)
        self.assertIn("ERROR", s)
        self.assertIn("boom", s)

    def test_str_without_error(self):
        r = UvSetCleanupResult(
            shape="X",
            initial_sets=["map1", "uvSet2"],
            primary_set="map1",
            sets_to_delete=["uvSet2"],
            final_name="map1",
        )
        s = str(r)
        self.assertIn("map1", s)
        self.assertIn("uvSet2", s)


class TestUvDiagnostics(MayaTkTestCase):
    """UvDiagnostics.cleanup_uv_sets — operates on real meshes."""

    def test_cleanup_default_mesh_is_noop(self):
        cube = cmds.polyCube(name="uv_clean_cube")[0]

        results = UvDiagnostics.cleanup_uv_sets([cube], dry_run=True, quiet=True)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].shape, cmds.listRelatives(cube, shapes=True)[0])

    def test_cleanup_extra_uv_set_dry_run_marks_for_deletion(self):
        cube = cmds.polyCube(name="uv_extra_cube")[0]
        shape = cmds.listRelatives(cube, shapes=True)[0]
        # Add an extra UV set
        cmds.polyUVSet(shape, create=True, uvSet="extraSet")

        results = UvDiagnostics.cleanup_uv_sets([cube], dry_run=True, quiet=True)
        self.assertEqual(len(results), 1)
        # Extra set should be present in initial sets
        self.assertIn("extraSet", results[0].initial_sets)

    def test_cleanup_extra_uv_set_actually_removes(self):
        cube = cmds.polyCube(name="uv_remove_cube")[0]
        shape = cmds.listRelatives(cube, shapes=True)[0]
        cmds.polyUVSet(shape, create=True, uvSet="extraSet")

        UvDiagnostics.cleanup_uv_sets([cube], dry_run=False, quiet=True)

        remaining = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        self.assertNotIn("extraSet", remaining)


if __name__ == "__main__":
    unittest.main()
