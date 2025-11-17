#!/usr/bin/python
# coding=utf-8
"""Regression tests for the InstanceSeparator assembly workflow."""

from collections import Counter
import unittest

import pymel.core as pm

from mayatk.core_utils import InstanceSeparator
from mayatk.xform_utils import XformUtils


class InstanceSeparatorAssemblyTest(unittest.TestCase):
    """Builds container-like assemblies to stress assembly grouping."""

    def setUp(self):
        pm.mel.file(new=True, force=True)

    # ------------------------------------------------------------------
    # Scene builders
    # ------------------------------------------------------------------
    def _make_cube(self, name, size, offset):
        cube = pm.polyCube(name=name, width=size[0], height=size[1], depth=size[2])[0]
        pm.move(offset[0], offset[1], offset[2], cube, absolute=True)
        return cube

    def _make_cylinder(self, name, radius, height, offset):
        cyl = pm.polyCylinder(
            name=name, radius=radius, height=height, subdivisionsX=14
        )[0]
        pm.move(offset[0], offset[1], offset[2], cyl, absolute=True)
        return cyl

    def _build_sparse_cluster(self, name, translate=(0.0, 0.0, 0.0)):
        parts = []
        tx, ty, tz = translate
        anchor = pm.polySphere(name=f"{name}_anchor", radius=0.12, subdivisionsX=10)[0]
        pm.move(tx, ty + 0.25, tz, anchor, absolute=True)
        far_block = self._make_cube(
            f"{name}_block",
            size=(2.5, 2.0, 2.5),
            offset=(tx + 5.0, ty + 1.0, tz),
        )
        upper_plate = self._make_cube(
            f"{name}_plate",
            size=(1.5, 0.4, 3.0),
            offset=(tx + 5.5, ty + 2.6, tz + 1.0),
        )
        parts.extend([anchor, far_block, upper_plate])

        combined = pm.polyUnite(parts, mergeUVSets=True, ch=False, name=name)[0]
        existing_parts = [part for part in parts if pm.objExists(part)]
        if existing_parts:
            pm.delete(existing_parts)
        pm.delete(combined, ch=True)
        pm.makeIdentity(combined, apply=True, t=1, r=1, s=1, n=0)
        return combined

    def _build_container_mesh(self, name, translate=(0.0, 0.0, 0.0), add_unique=False):
        """Create a multi-shell mesh resembling the provided container cluster."""

        parts = []
        tx, ty, tz = translate

        base_left = self._make_cube(
            f"{name}_baseA", size=(1.2, 4.0, 1.2), offset=(tx, ty + 2.0, tz)
        )
        base_right = self._make_cube(
            f"{name}_baseB", size=(1.4, 2.0, 1.4), offset=(tx + 1.6, ty + 1.0, tz + 0.6)
        )
        platform = self._make_cube(
            f"{name}_platform",
            size=(4.0, 0.35, 3.0),
            offset=(tx + 1.0, ty + 3.9, tz + 0.9),
        )
        suitcase = self._make_cube(
            f"{name}_suitcase",
            size=(2.0, 0.7, 1.2),
            offset=(tx + 1.8, ty + 4.7, tz + 0.5),
        )
        parts.extend([base_left, base_right, platform, suitcase])

        can_offsets = [
            (tx + 0.2, ty + 4.6, tz + 0.1),
            (tx + 1.2, ty + 4.6, tz + 0.2),
            (tx + 0.3, ty + 4.6, tz + 1.5),
            (tx + 1.4, ty + 4.6, tz + 1.4),
            (tx + 2.4, ty + 4.6, tz + 0.8),
            (tx + 2.6, ty + 4.6, tz + 1.7),
        ]
        for idx, offset in enumerate(can_offsets):
            parts.append(
                self._make_cylinder(
                    f"{name}_can_{idx}", radius=0.35, height=1.3, offset=offset
                )
            )

        if add_unique:
            parts.append(
                self._make_cylinder(
                    f"{name}_unique_lid",
                    radius=0.5,
                    height=1.6,
                    offset=(tx + 3.4, ty + 4.8, tz + 0.2),
                )
            )

        component_count = len(parts)
        unite_result = pm.polyUnite(parts, mergeUVSets=True, ch=False, name=name)
        combined = (
            unite_result[0] if isinstance(unite_result, (list, tuple)) else unite_result
        )

        existing_parts = [part for part in parts if pm.objExists(part)]
        if existing_parts:
            pm.delete(existing_parts)

        if pm.objExists(combined):
            pm.delete(combined, ch=True)
            pm.makeIdentity(combined, apply=True, t=1, r=1, s=1, n=0)
        return combined, component_count

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------
    def test_combined_mesh_matches_multi_transform_layout(self):
        """Combined atlas splits into duplicates plus a near-duplicate variant."""

        cluster_a, comp_count = self._build_container_mesh(
            "clusterA", translate=(0.0, 0.0, 0.0)
        )
        cluster_b, _ = self._build_container_mesh("clusterB", translate=(8.0, 0.0, 0.0))
        cluster_variant, variant_count = self._build_container_mesh(
            "clusterVariant", translate=(16.0, 0.0, 0.0), add_unique=True
        )

        separator = InstanceSeparator(
            tolerance=0.99,
            require_same_material=False,
            split_shells=True,
            rebuild_instances=False,
            template_position_tolerance=0.5,
            template_rotation_tolerance=12.0,
        )

        split_result = separator.separate([cluster_a, cluster_b, cluster_variant])
        self.assertEqual(len(split_result.assemblies), 3)
        assembly_signatures = [
            descriptor.signature for descriptor in split_result.assemblies
        ]
        signature_counts = Counter(assembly_signatures)
        self.assertEqual(len(signature_counts), 2)
        self.assertEqual(signature_counts.most_common(1)[0][1], 2)

        expected_counts = Counter()
        for descriptor in split_result.assemblies:
            expected_counts.update(descriptor.signature)

        combine_inputs = [
            pm.duplicate(cluster_a, rr=True)[0],
            pm.duplicate(cluster_b, rr=True)[0],
            pm.duplicate(cluster_variant, rr=True)[0],
        ]
        combined_result = pm.polyUnite(
            combine_inputs,
            mergeUVSets=True,
            ch=False,
            name="containersCombinedAtlas",
        )
        combined_atlas = (
            combined_result[0]
            if isinstance(combined_result, (list, tuple))
            else combined_result
        )
        if pm.objExists(combined_atlas):
            pm.delete(combined_atlas, ch=True)
            pm.makeIdentity(combined_atlas, apply=True, t=1, r=1, s=1, n=0)

        atlas_result = separator.separate([combined_atlas])
        self.assertEqual(len(atlas_result.assemblies), 1)

        atlas_descriptor = atlas_result.assemblies[0]
        self.assertEqual(
            len(atlas_descriptor.components), comp_count * 2 + variant_count
        )

        atlas_counts = Counter(atlas_descriptor.signature)
        self.assertEqual(
            atlas_counts,
            expected_counts,
            msg="Combined atlas should contain the duplicate containers plus the near duplicate variant",
        )

    def test_container_like_assemblies_form_instances(self):
        """Ensure duplicate assemblies are detected while uniques remain untouched."""

        duplicate_a, component_count = self._build_container_mesh(
            "containerA", translate=(0.0, 0.0, 0.0)
        )
        duplicate_b, _ = self._build_container_mesh(
            "containerB", translate=(12.0, 0.0, 0.0)
        )
        unique_container, unique_component_count = self._build_container_mesh(
            "uniqueContainer", translate=(24.0, 0.0, 0.0), add_unique=True
        )
        lone_can = self._make_cylinder(
            "loose_can", radius=0.45, height=1.2, offset=(36.0, 4.6, 0.2)
        )

        separator = InstanceSeparator(
            tolerance=0.99,
            require_same_material=False,
            split_shells=True,
            rebuild_instances=False,
            template_position_tolerance=0.5,
            template_rotation_tolerance=12.0,
        )
        result = separator.separate(
            [duplicate_a, duplicate_b, unique_container, lone_can]
        )

        expected_payloads = component_count * 2 + unique_component_count + 1
        self.assertEqual(result.payload_count, expected_payloads)

        assembly_groups = result.instantiable_assembly_groups
        self.assertEqual(
            len(assembly_groups), 1, msg="Duplicate assemblies should group together"
        )
        assembly_sources = {assembly_groups[0].prototype.source_transform}
        assembly_sources.update(
            member.source_transform for member in assembly_groups[0].members
        )
        self.assertSetEqual(assembly_sources, {duplicate_a, duplicate_b})

        unique_sources = {
            descriptor.source_transform for descriptor in result.unique_assemblies
        }
        self.assertIn(unique_container, unique_sources)

        lone_payloads = {payload.transform for payload in result.unique_payloads}
        self.assertIn(lone_can, lone_payloads)

    def test_combined_atlas_rebuilds_into_instantiable_meshes(self):
        """Anchor-based detection should rebuild combined meshes into instantiable outputs."""

        duplicate_a, _ = self._build_container_mesh(
            "rebuildA", translate=(0.0, 0.0, 0.0)
        )
        duplicate_b, _ = self._build_container_mesh(
            "rebuildB", translate=(10.0, 0.0, 0.0)
        )
        unique_variant, _ = self._build_container_mesh(
            "rebuildVariant", translate=(20.0, 0.0, 0.0), add_unique=True
        )

        combined_inputs = [duplicate_a, duplicate_b, unique_variant]
        combined_result = pm.polyUnite(
            combined_inputs, mergeUVSets=True, ch=False, name="combinedAtlas"
        )
        combined_atlas = (
            combined_result[0]
            if isinstance(combined_result, (list, tuple))
            else combined_result
        )
        if pm.objExists(combined_atlas):
            pm.delete(combined_atlas, ch=True)
            pm.makeIdentity(combined_atlas, apply=True, t=1, r=1, s=1, n=0)

        separator = InstanceSeparator(
            tolerance=0.99,
            require_same_material=False,
            split_shells=True,
            rebuild_instances=True,
            template_position_tolerance=0.5,
            template_rotation_tolerance=12.0,
            anchor_capture_multiplier=6.0,
        )

        result = separator.separate([combined_atlas])
        inst_groups = result.instantiable_assembly_groups
        self.assertEqual(len(inst_groups), 1)

        group = inst_groups[0]
        rebuilt_descriptors = [group.prototype] + list(group.members)
        self.assertGreaterEqual(len(rebuilt_descriptors), 2)

        _, reference_size = XformUtils.get_bounding_box(duplicate_a, "center|size")
        self.assertTrue(reference_size)

        for descriptor in rebuilt_descriptors:
            self.assertEqual(
                len(descriptor.components),
                1,
                msg="Rebuilt assemblies should collapse into a single combined mesh",
            )
            payload = descriptor.components[0]
            self.assertTrue(pm.objExists(payload.transform))
            _, rebuilt_size = XformUtils.get_bounding_box(
                payload.transform, "center|size"
            )
            for axis in range(3):
                self.assertAlmostEqual(
                    reference_size[axis],
                    rebuilt_size[axis],
                    places=1,
                    msg="Combined mesh should match reference container dimensions",
                )

        unique_assemblies = result.unique_assemblies
        self.assertGreaterEqual(len(unique_assemblies), 1)

    def test_sparse_clusters_use_adaptive_capture_radius(self):
        """Very small anchors with distant parts should still rebuild into combined assemblies."""

        cluster_a = self._build_sparse_cluster("sparseA", translate=(0.0, 0.0, 0.0))
        cluster_b = self._build_sparse_cluster("sparseB", translate=(20.0, 0.0, 0.0))

        combined_result = pm.polyUnite(
            [cluster_a, cluster_b], mergeUVSets=True, ch=False, name="sparseCombined"
        )
        combined_atlas = (
            combined_result[0]
            if isinstance(combined_result, (list, tuple))
            else combined_result
        )
        if pm.objExists(combined_atlas):
            pm.delete(combined_atlas, ch=True)
            pm.makeIdentity(combined_atlas, apply=True, t=1, r=1, s=1, n=0)

        separator = InstanceSeparator(
            tolerance=0.99,
            require_same_material=False,
            split_shells=True,
            rebuild_instances=True,
            template_position_tolerance=0.4,
            template_rotation_tolerance=10.0,
        )

        result = separator.separate([combined_atlas])
        inst_groups = result.instantiable_assembly_groups
        self.assertEqual(len(inst_groups), 1)

        rebuilt = [inst_groups[0].prototype] + list(inst_groups[0].members)
        self.assertEqual(len(rebuilt), 2)
        for descriptor in rebuilt:
            self.assertEqual(
                len(descriptor.components),
                1,
                msg="Adaptive capture radius should collapse each sparse cluster",
            )


if __name__ == "__main__":
    unittest.main()
