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
from mayatk.xform_utils._xform_utils import XformUtils

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


class TestTransformDiagnosticsInheritedShear(MayaTkTestCase):
    """get_non_orthogonal — the world-space (FBX-facing) check.

    An object under a non-uniformly scaled AND rotated ancestor evaluates to
    non-perpendicular world axes while carrying zero shear of its own. That is
    the usual source of the FBX "Non-orthogonal matrix support" warning, and
    the local-only ``get_sheared`` check cannot see it.
    """

    def _sheared_hierarchy(self, prefix):
        """Build ``grp`` (non-uniform scale) -> ``child`` (rotated). Returns both."""
        grp = cmds.group(empty=True, name=f"{prefix}_grp")
        cmds.setAttr(f"{grp}.scaleX", 3.0)
        child = cmds.polyCube(name=f"{prefix}_child")[0]
        cmds.parent(child, grp)
        cmds.setAttr(f"{child}.rotateZ", 35.0)
        return grp, child

    @staticmethod
    def _world_points(node):
        return cmds.xform(f"{node}.vtx[*]", q=True, ws=True, t=True)

    def test_inherited_shear_detected_but_not_by_get_sheared(self):
        _grp, child = self._sheared_hierarchy("td_inh")

        self.assertEqual(TransformDiagnostics.get_sheared(objects=[child]), [])
        self.assertEqual(
            TransformDiagnostics.get_non_orthogonal(objects=[child]), [child]
        )

    def test_detailed_reports_cause_and_skew(self):
        grp, child = self._sheared_hierarchy("td_cause")
        own = cmds.polyCube(name="td_cause_own")[0]
        cmds.xform(own, shear=(0.4, 0.0, 0.0))

        detail = TransformDiagnostics.get_non_orthogonal(
            objects=[grp, child, own], detailed=True
        )
        self.assertEqual(detail[child]["cause"], "inherited")
        self.assertEqual(detail[own]["cause"], "shear")
        self.assertGreater(detail[child]["skew"], 0.0)
        self.assertNotIn(grp, detail)  # non-uniform scale alone is orthogonal

    def test_uniform_and_rotated_hierarchy_is_not_flagged(self):
        """Non-uniform scale WITHOUT a rotated descendant keeps axes perpendicular."""
        cube = cmds.polyCube(name="td_clean_hier")[0]
        cmds.setAttr(f"{cube}.rotateY", 45.0)
        cmds.setAttr(f"{cube}.scaleX", 2.0)
        grp = cmds.group(cube, name="td_clean_hier_grp")
        cmds.setAttr(f"{grp}.rotateZ", 20.0)

        self.assertEqual(
            TransformDiagnostics.get_non_orthogonal(objects=[grp, cube]), []
        )

    def test_fix_clears_inherited_shear_without_moving_geometry(self):
        grp, child = self._sheared_hierarchy("td_inh_fix")
        before = self._world_points(child)

        fixed = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[child], quiet=True
        )

        self.assertEqual([f.split("|")[-1] for f in fixed], [child])
        self.assertEqual(TransformDiagnostics.get_non_orthogonal(objects=[child]), [])
        # Freezing bakes the local matrix into the points, so the composite
        # result — where the vertices actually sit — must not change.
        for a, b in zip(before, self._world_points(child)):
            self.assertAlmostEqual(a, b, places=5)
        # ...and the object stays in its hierarchy (unlike an unparent-to-world fix).
        self.assertEqual(cmds.listRelatives(child, parent=True), [grp])

    def test_fix_processes_top_down_and_skips_cleared_descendants(self):
        """Freezing an ancestor clears its descendants — they aren't frozen twice."""
        root = cmds.group(empty=True, name="td_chain_root")
        cmds.setAttr(f"{root}.scaleX", 2.0)
        mid = cmds.group(empty=True, name="td_chain_mid")
        cmds.parent(mid, root)
        cmds.setAttr(f"{mid}.rotateZ", 30.0)
        cmds.setAttr(f"{mid}.scaleY", 3.0)
        leaf = cmds.polyCube(name="td_chain_leaf")[0]
        cmds.parent(leaf, mid)
        cmds.setAttr(f"{leaf}.rotateY", 20.0)

        chain = [root, mid, leaf]
        self.assertEqual(
            set(TransformDiagnostics.get_non_orthogonal(objects=chain)), {mid, leaf}
        )
        before = self._world_points(leaf)

        fixed = TransformDiagnostics.fix_non_orthogonal_axes(objects=chain, quiet=True)

        self.assertEqual(TransformDiagnostics.get_non_orthogonal(objects=chain), [])
        # Only the ancestor needed freezing; the leaf came out clean with it.
        self.assertEqual([f.split("|")[-1] for f in fixed], [mid])
        for a, b in zip(before, self._world_points(leaf)):
            self.assertAlmostEqual(a, b, places=5)

    def test_dry_run_lists_inherited_offenders_without_modifying(self):
        _grp, child = self._sheared_hierarchy("td_inh_dry")
        before = cmds.getAttr(f"{child}.rotateZ")

        result = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[child], dry_run=True, quiet=True
        )

        self.assertEqual(result, [child])
        self.assertAlmostEqual(cmds.getAttr(f"{child}.rotateZ"), before, places=6)


class TestTransformDiagnosticsConnections(MayaTkTestCase):
    """Connection-aware fix behavior.

    Only rotate/scale/shear are frozen (translation never contributes to axis
    skew), so translate connections survive untouched. Driven rotate/scale
    channels are skipped by default — reconnecting after a freeze re-drives
    the channel the freeze zeroed (double-transform + the skew returns,
    probe-measured), so there is no accurate restore; breaking the driver is
    explicit opt-in via ``break_connections``.
    """

    def _sheared_child(self, prefix):
        grp = cmds.group(empty=True, name=f"{prefix}_grp")
        cmds.setAttr(f"{grp}.scaleX", 3.0)
        child = cmds.polyCube(name=f"{prefix}_child")[0]
        cmds.parent(child, grp)
        cmds.setAttr(f"{child}.rotateZ", 35.0)
        return grp, child

    def test_translate_connection_survives_the_fix(self):
        _grp, child = self._sheared_child("tdc_t")
        drv = cmds.spaceLocator(name="tdc_t_drv")[0]
        cmds.setAttr(f"{drv}.translate", 1.0, 2.0, 3.0)
        cmds.connectAttr(f"{drv}.translate", f"{child}.translate")

        fixed = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[child], quiet=True
        )

        self.assertEqual([f.split("|")[-1] for f in fixed], [child])
        self.assertEqual(TransformDiagnostics.get_non_orthogonal([child]), [])
        self.assertEqual(
            cmds.listConnections(f"{child}.translate", source=True, plugs=True),
            [f"{drv}.translate"],
        )

    def test_driven_rotate_skipped_by_default_and_reported(self):
        _grp, child = self._sheared_child("tdc_r")
        drv = cmds.spaceLocator(name="tdc_r_drv")[0]
        cmds.setAttr(f"{drv}.rotateZ", 35.0)
        cmds.connectAttr(f"{drv}.rotateZ", f"{child}.rotateZ", force=True)

        detail = TransformDiagnostics.get_non_orthogonal([child], detailed=True)
        self.assertEqual(detail[child]["driven"], [f"{drv}.rotateZ"])

        fixed = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[child], quiet=True
        )
        self.assertEqual(fixed, [])
        # Untouched: still flagged, connection intact.
        self.assertEqual(TransformDiagnostics.get_non_orthogonal([child]), [child])
        self.assertEqual(
            cmds.listConnections(f"{child}.rotateZ", source=True, plugs=True),
            [f"{drv}.rotateZ"],
        )
        # Dry run excludes it too — the return is the would-fix list.
        self.assertEqual(
            TransformDiagnostics.fix_non_orthogonal_axes(
                objects=[child], dry_run=True, quiet=True
            ),
            [],
        )

    def test_break_connections_fixes_and_disconnects(self):
        _grp, child = self._sheared_child("tdc_b")
        drv = cmds.spaceLocator(name="tdc_b_drv")[0]
        cmds.setAttr(f"{drv}.rotateZ", 35.0)
        cmds.connectAttr(f"{drv}.rotateZ", f"{child}.rotateZ", force=True)

        fixed = TransformDiagnostics.fix_non_orthogonal_axes(
            objects=[child], quiet=True, break_connections=True
        )

        self.assertEqual([f.split("|")[-1] for f in fixed], [child])
        self.assertEqual(TransformDiagnostics.get_non_orthogonal([child]), [])
        self.assertFalse(
            cmds.listConnections(f"{child}.rotateZ", source=True, plugs=True)
        )

    def test_store_restore_contract_survives_the_fix(self):
        """A frozen object later fixed still restores fully.

        ``freeze_transforms`` stamps the bake history itself (store=True by
        default), so the panel flow no longer calls ``store_transforms``
        alongside it — doing both would double-compose the history.
        """
        cube = cmds.polyCube(name="tdc_store")[0]
        cmds.setAttr(f"{cube}.rotateZ", 25.0)
        cmds.xform(cube, shear=(0.4, 0.0, 0.0))
        XformUtils.freeze_transforms(cube, force=True)
        # New shear + rotation for the fix to bake.
        cmds.xform(cube, shear=(0.3, 0.0, 0.0))
        cmds.setAttr(f"{cube}.rotateZ", 10.0)

        TransformDiagnostics.fix_non_orthogonal_axes(objects=[cube], quiet=True)
        self.assertEqual(TransformDiagnostics.get_non_orthogonal([cube]), [])

        XformUtils.restore_transforms(cube)
        # Both freezes' rotations compose: 25 (panel) + 10 (fix).
        self.assertAlmostEqual(cmds.getAttr(f"{cube}.rotateZ"), 35.0, places=3)


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


class TestFixNonOrthogonalInstances(MayaTkTestCase):
    """fix_non_orthogonal_axes on instanced objects — instancing preserved.

    Regression for the multi-instance failure: previously every flagged
    member was permanently uninstanced one at a time.  With the default
    ``instance_strategy="preserve"``, a group whose members share the same
    skew is fixed by one master freeze + sibling compensation; only members
    whose own divergent skew survives compensation fall back to uninstance.
    """

    def _make_sheared_group(self, shears, name="nio"):
        src = cmds.polyCube(name=f"{name}_m0")[0]
        members = [src]
        for i in range(1, len(shears)):
            members.append(cmds.instance(src, name=f"{name}_m{i}")[0])
        for i, (m, sh) in enumerate(zip(members, shears)):
            cmds.setAttr(f"{m}.shearXY", sh)
            cmds.setAttr(f"{m}.translateX", i * 3.0)
        return cmds.ls(members, long=True)

    def _world_verts(self, obj, count=8):
        return [
            cmds.xform(f"{obj}.vtx[{i}]", q=True, ws=True, t=True)
            for i in range(count)
        ]

    def _shared_parent_count(self, member):
        shape = cmds.listRelatives(member, shapes=True, fullPath=True)[0]
        return len(cmds.listRelatives(shape, allParents=True) or [])

    def test_shared_skew_group_fixed_in_place(self):
        members = self._make_sheared_group([0.4, 0.4, 0.4])
        self.assertEqual(
            len(TransformDiagnostics.get_non_orthogonal(members)), 3
        )
        before = {m: self._world_verts(m) for m in members}

        fixed = TransformDiagnostics.fix_non_orthogonal_axes(members, quiet=True)

        self.assertTrue(fixed)
        self.assertEqual(TransformDiagnostics.get_non_orthogonal(members), [])
        for m in cmds.ls(members, long=True):
            self.assertEqual(self._shared_parent_count(m), 3)
            for va, vb in zip(before[m], self._world_verts(m)):
                for x, y in zip(va, vb):
                    self.assertAlmostEqual(x, y, places=3)

    def test_divergent_skew_falls_back_per_member(self):
        members = self._make_sheared_group([0.4, 0.4, -0.6], name="niod")
        before = {m: self._world_verts(m) for m in members}

        TransformDiagnostics.fix_non_orthogonal_axes(members, quiet=True)

        self.assertEqual(TransformDiagnostics.get_non_orthogonal(members), [])
        for m in cmds.ls(members, long=True):
            for va, vb in zip(before[m], self._world_verts(m)):
                for x, y in zip(va, vb):
                    self.assertAlmostEqual(x, y, places=3)
        # m0/m1 (shared skew) stay instanced together; m2 was forked off.
        self.assertEqual(self._shared_parent_count(members[0]), 2)
        self.assertEqual(self._shared_parent_count(members[1]), 2)
        self.assertEqual(self._shared_parent_count(members[2]), 1)

    def test_uninstance_strategy_matches_legacy_behavior(self):
        members = self._make_sheared_group([0.4, 0.4], name="niol")

        TransformDiagnostics.fix_non_orthogonal_axes(
            members, quiet=True, instance_strategy="uninstance"
        )

        self.assertEqual(TransformDiagnostics.get_non_orthogonal(members), [])
        for m in cmds.ls(members, long=True):
            self.assertEqual(self._shared_parent_count(m), 1)


class TestNonOrthogonalNamesTheFreeze(MayaTkTestCase):
    """The diagnosis reads the node's stored bake rather than only measuring
    the symptom, so a report can say WHICH freeze introduced the shear."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="nonf_cube")[0]

    def test_unstamped_shear_reports_plain_shear(self):
        cmds.setAttr(f"{self.cube}.shear", 0.4, 0.0, 0.0, type="double3")

        info = TransformDiagnostics.get_non_orthogonal([self.cube], detailed=True)
        entry = next(iter(info.values()))
        self.assertEqual(entry["cause"], "shear")
        self.assertFalse(entry["frozen"])
        self.assertIsNone(entry["baked_scale"])

    def test_baked_nonuniform_scale_is_named(self):
        # A freeze that consumed a non-uniform scale is the classic way shear
        # gets manufactured; the bake history is the evidence.
        cmds.setAttr(f"{self.cube}.scale", 2.0, 1.0, 1.0, type="double3")
        XformUtils.freeze_transforms(self.cube, scale=True, force=True)
        cmds.setAttr(f"{self.cube}.shear", 0.4, 0.0, 0.0, type="double3")

        info = TransformDiagnostics.get_non_orthogonal([self.cube], detailed=True)
        entry = next(iter(info.values()))
        self.assertEqual(entry["cause"], "baked-shear")
        self.assertTrue(entry["frozen"])
        self.assertAlmostEqual(entry["baked_scale"][0], 2.0, places=5)

    def test_note_renders_only_for_a_baked_cause(self):
        self.assertEqual(
            TransformDiagnostics._baked_scale_note(
                {"cause": "shear", "baked_scale": [2.0, 1.0, 1.0]}
            ),
            "",
        )
        self.assertIn(
            "baked scale",
            TransformDiagnostics._baked_scale_note(
                {"cause": "baked-shear", "baked_scale": [2.0, 1.0, 1.0]}
            ),
        )


class TestSceneDiagnosticsMangledNames(MayaTkTestCase):
    """SceneDiagnostics.repair_mangled_names — scratch/mangled name repair.

    Regression (2026-08-04): the scene exporter's ``check_mangled_names``
    scans transforms AND all descendants, but its designated repair
    (``Naming.conform_shape_names``) only renames shapes — a mangled
    TRANSFORM name survives every repair pass, so the check can never be
    cleared by the task.  The repair must clean the offending names
    themselves, then conform shapes.
    """

    def _mangled(self, name):
        from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics

        return bool(SceneDiagnostics.MANGLED_NAME_RE.search(name))

    def test_regex_matches_task_manager_signatures(self):
        # Single source of truth: the exporter check must use THIS regex.
        from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        self.assertIs(TaskManager.MANGLED_NAME_RE, SceneDiagnostics.MANGLED_NAME_RE)
        for bad in ("a__uninst_tmp", "b__RZTMP2", "cFBXASC046d", "e___f"):
            self.assertTrue(SceneDiagnostics.MANGLED_NAME_RE.search(bad), bad)
        self.assertFalse(SceneDiagnostics.MANGLED_NAME_RE.search("clean_name1"))

    def test_repairs_mangled_transform_names(self):
        from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics

        cmds.polyCube(name="gear__uninst_tmp")
        cmds.polyCube(name="pipe___heavy")
        cmds.polyCube(name="boltFBXASC046a")

        result = SceneDiagnostics.repair_mangled_names()
        self.assertGreaterEqual(len(result["renamed"]), 3)

        leaves = [
            n.split("|")[-1] for n in cmds.ls(dag=True, long=True) or []
        ]
        offenders = [leaf for leaf in leaves if self._mangled(leaf)]
        self.assertEqual(offenders, [])

    def test_repairs_mangled_shape_and_conforms(self):
        from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics

        xform = cmds.polyCube(name="clean_part")[0]
        shape = cmds.listRelatives(xform, shapes=True)[0]
        cmds.rename(shape, "clean_part__RZTMPShape")

        SceneDiagnostics.repair_mangled_names()
        shapes = cmds.listRelatives("clean_part", shapes=True) or []
        self.assertEqual(len(shapes), 1)
        self.assertFalse(self._mangled(shapes[0]))
        self.assertTrue(shapes[0].startswith("clean_partShape"))

    def test_scoped_to_objects(self):
        from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics

        inside = cmds.polyCube(name="in__uninst_tmp")[0]
        outside = cmds.polyCube(name="out__uninst_tmp")[0]

        SceneDiagnostics.repair_mangled_names(objects=[inside])
        self.assertFalse(cmds.objExists("in__uninst_tmp"))
        self.assertTrue(cmds.objExists("out__uninst_tmp"))

    def test_dry_run_changes_nothing(self):
        from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics

        cmds.polyCube(name="probe__uninst_tmp")
        result = SceneDiagnostics.repair_mangled_names(dry_run=True)
        self.assertTrue(cmds.objExists("probe__uninst_tmp"))
        self.assertGreaterEqual(len(result["renamed"]), 1)

    def test_empty_objects_is_noop(self):
        # The exporter passes `self.objects or []` — an empty export set
        # must NOT fall back to the whole scene.
        from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics

        cmds.polyCube(name="stay__uninst_tmp")
        result = SceneDiagnostics.repair_mangled_names(objects=[])
        self.assertEqual(result["renamed"], [])
        self.assertTrue(cmds.objExists("stay__uninst_tmp"))

    def test_repair_clears_exporter_check(self):
        # End to end: the repair must clear the very check that flags it.
        from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        import pythontk as ptk

        # check_mangled_names' offender branch renders _obj_link entries,
        # which need the exporter's LoggingMixin logger (log_link).
        xform = cmds.polyCube(name="rig__uninst_tmp")[0]
        tm = TaskManager(ptk.LoggingMixin().logger)
        tm.objects = cmds.ls(xform, long=True)
        ok, _ = tm.check_mangled_names()
        self.assertFalse(ok)

        SceneDiagnostics.repair_mangled_names(objects=tm.objects)
        tm.objects = cmds.ls(type="transform", long=True)
        ok, messages = tm.check_mangled_names()
        self.assertTrue(ok, messages)


if __name__ == "__main__":
    unittest.main()
