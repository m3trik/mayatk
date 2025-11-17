# !/usr/bin/python
# coding=utf-8
import importlib
import sys
import unittest

import pymel.core as pm
import mayatk as mtk
from pythontk.core_utils import ModuleReloader

# Verbose levels: 0=silent, 1=basic (module names), 2=detailed (with skip reasons)
MODULE_RELOADER = ModuleReloader(include_submodules=False, verbose=0)
MODULE_NAME = "mayatk.core_utils.auto_instancer"
MAT_UTILS_MODULE = (
    "mayatk.mat_utils._mat_utils"  # Reload the actual implementation module
)
FACE_COLORS = [
    (0.9, 0.1, 0.1),
    (0.1, 0.5, 0.9),
    (0.1, 0.8, 0.3),
    (0.9, 0.6, 0.1),
    (0.6, 0.2, 0.8),
    (0.1, 0.9, 0.8),
    (0.9, 0.9, 0.1),
    (0.6, 0.6, 0.6),
]
_FACE_SHADER_CACHE = {}


class AutoInstancerTest(unittest.TestCase):
    """Unit tests for the AutoInstancer prototype."""

    def setUp(self):
        # Reload mat_utils with submodules to get _mat_utils.py changes
        MODULE_RELOADER.reload("mayatk.mat_utils", include_submodules=True)
        global MatUtils
        from mayatk.mat_utils import MatUtils

        previous_module = sys.modules.get(MODULE_NAME)
        previous_count = getattr(previous_module, "RELOAD_COUNTER", 0)

        MODULE_RELOADER.reload(MODULE_NAME)
        self.auto_instancer_module = importlib.import_module(MODULE_NAME)
        current_count = getattr(self.auto_instancer_module, "RELOAD_COUNTER", 0)

        if previous_module is not None:
            self.assertGreater(
                current_count,
                previous_count,
                "AutoInstancer module reload counter did not change; reload likely failed.",
            )

        self.reload_counter = current_count
        pm.mel.file(new=True, force=True)
        # Clear shader cache after new scene to avoid stale references
        _FACE_SHADER_CACHE.clear()

    def tearDown(self):
        pm.mel.file(new=True, force=True)

    @staticmethod
    def _create_cubes(count: int):
        cubes = [pm.polyCube(name=f"autoInst_cube_{i}")[0] for i in range(count)]
        for cube in cubes:
            AutoInstancerTest._apply_face_palette(cube)
        return cubes

    @staticmethod
    def _apply_face_palette(mesh: pm.nodetypes.Transform) -> None:
        shape = mesh.getShape()
        if not shape or shape.nodeType() != "mesh":
            return
        faces = shape.f

        def _shader_for_color(color):
            key = f"autoInstTest_{int(color[0]*255)}_{int(color[1]*255)}_{int(color[2]*255)}"
            sg = _FACE_SHADER_CACHE.get(key)
            if sg:
                return sg

            sg = MatUtils._create_standard_shader(
                name=key, color=color, return_type="shading_group"
            )
            _FACE_SHADER_CACHE[key] = sg
            return sg

        for index in range(faces.count()):
            color = FACE_COLORS[index % len(FACE_COLORS)]
            sg = _shader_for_color(color)
            pm.sets(sg, forceElement=faces[index])

    @staticmethod
    def _world_matrix(node: pm.nodetypes.Transform):
        return pm.datatypes.Matrix(pm.xform(node, q=True, ws=True, matrix=True))

    def _assert_world_matrix_close(
        self, node: pm.nodetypes.Transform, expected_matrix, places: int = 4
    ) -> None:
        actual = self._world_matrix(node)
        for actual_value, expected_value in zip(actual, expected_matrix):
            self.assertAlmostEqual(actual_value, expected_value, places=places)

    def _create_instancer(self, **kwargs):
        return self.auto_instancer_module.AutoInstancer(**kwargs)

    def test_instances_created_for_identical_meshes(self):
        cubes = self._create_cubes(3)
        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        report = instancer.run([cube.name() for cube in cubes])

        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["instance_count"], len(cubes) - 1)

        # All original transform names should still exist after replacement
        for cube in cubes:
            self.assertTrue(pm.objExists(cube.name()))

        # Non-prototype transforms should now be instanced copies
        for cube in cubes[1:]:
            shape = pm.listRelatives(cube, shapes=True)[0]
            self.assertTrue(shape.isInstanced())

    def test_material_mismatch_skips_instancing(self):
        cubes = self._create_cubes(2)
        # Assign a unique material to the second cube so materials differ
        custom_mat = pm.shadingNode("lambert", asShader=True, name="autoInst_mat")
        shading_group = pm.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name="autoInst_matSG",
        )
        custom_mat.outColor.connect(shading_group.surfaceShader, force=True)
        pm.sets(shading_group, forceElement=cubes[1])

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        report = instancer.run([cube.name() for cube in cubes])

        # No instancing should occur when materials don't match
        self.assertEqual(len(report), 0)

    def test_instances_preserve_world_alignment(self):
        cubes = self._create_cubes(3)
        transforms = [
            {"t": (0, 0, 0), "r": (0, 0, 0), "s": (1, 1, 1)},
            {"t": (5, 2, -3), "r": (15, 45, 5), "s": (1.5, 0.5, 2)},
            {"t": (-7, 4, 1), "r": (90, 0, 30), "s": (0.75, 0.75, 0.75)},
        ]

        for cube, values in zip(cubes, transforms):
            pm.xform(cube, ws=True, translation=values["t"])
            pm.xform(cube, ws=True, rotation=values["r"])
            pm.xform(cube, ws=True, scale=values["s"])

        expected_matrices = {cube.name(): self._world_matrix(cube) for cube in cubes}

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        instancer.run([cube.name() for cube in cubes])

        for cube in cubes:
            self.assertTrue(pm.objExists(cube.name()))
            self._assert_world_matrix_close(
                pm.PyNode(cube.name()), expected_matrices[cube.name()]
            )

    def test_instances_under_parents_keep_alignment(self):
        parent_a = pm.group(em=True, name="autoInst_parent_A")
        parent_b = pm.group(em=True, name="autoInst_parent_B")

        pm.xform(parent_a, ws=True, translation=(3, 0, 0))
        pm.xform(parent_b, ws=True, translation=(-4, 6, 2))

        cubes = self._create_cubes(4)
        pm.parent(cubes[0], parent_a)
        pm.parent(cubes[1], parent_a)
        pm.parent(cubes[2], parent_b)
        pm.parent(cubes[3], parent_b)

        offsets = [
            {"t": (1, 0, 0), "r": (0, 0, 0)},
            {"t": (-2, 1, 0), "r": (0, 90, 0)},
            {"t": (0, -1, 2), "r": (45, 0, 45)},
            {"t": (2, 3, -1), "r": (10, 20, 30)},
        ]

        for cube, values in zip(cubes, offsets):
            pm.xform(cube, translation=values["t"], rotation=values["r"], os=True)

        expected_matrices = {cube.name(): self._world_matrix(cube) for cube in cubes}
        expected_parents = {cube.name(): cube.getParent() for cube in cubes}

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        instancer.run([cube.name() for cube in cubes])

        for cube in cubes:
            node = pm.PyNode(cube.name())
            self._assert_world_matrix_close(node, expected_matrices[cube.name()])
            self.assertEqual(node.getParent(), expected_parents[cube.name()])

    @staticmethod
    def _get_vertex_world_positions(mesh: pm.nodetypes.Transform) -> list:
        """Get world-space positions of all vertices on a mesh.

        Returns:
            list: [(x, y, z), ...] for each vertex in order
        """
        shape = mesh.getShape()
        if not shape or shape.nodeType() != "mesh":
            return []

        positions = []
        num_verts = pm.polyEvaluate(mesh, vertex=True)

        for i in range(num_verts):
            vtx = f"{mesh}.vtx[{i}]"
            pos = pm.xform(vtx, q=True, ws=True, translation=True)
            positions.append(tuple(pos))

        return positions

    def test_vertex_positions_match_after_instancing(self):
        """Verify that vertex world positions are preserved after instancing."""
        # Create plain cubes with IDENTICAL geometry but different transforms
        cubes = [pm.polyCube(name=f"autoInst_vtx_cube_{i}")[0] for i in range(3)]

        # Ensure all cubes use the same material by explicitly assigning
        # Note: polyCube connects to initialShadingGroup but each gets its own connection
        for cube in cubes:
            shape = cube.getShape()
            # Clear existing shading group connections
            existing_sgs = pm.listConnections(shape, type="shadingEngine")
            for sg in existing_sgs or []:
                pm.sets(sg, remove=shape)
            # Assign to initial shading group
            pm.sets(pm.PyNode("initialShadingGroup"), forceElement=shape)

        # Apply only translation (no rotation/scale that might affect matching)
        transforms = [
            {"t": (0, 0, 0)},
            {"t": (5, 0, 0)},
            {"t": (-5, 0, 0)},
        ]

        for cube, values in zip(cubes, transforms):
            pm.xform(cube, ws=True, translation=values["t"])

        original_positions = {
            cube.name(): self._get_vertex_world_positions(cube) for cube in cubes
        }

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        report = instancer.run([cube.name() for cube in cubes])

        self.assertEqual(
            len(report), 1, "Should create instances from identical geometry"
        )
        self.assertEqual(report[0]["instance_count"], len(cubes) - 1)

        for original_name, expected_positions in original_positions.items():
            self.assertTrue(
                pm.objExists(original_name), f"{original_name} should still exist"
            )

            mesh = pm.PyNode(original_name)
            actual_positions = self._get_vertex_world_positions(mesh)

            self.assertEqual(
                len(actual_positions),
                len(expected_positions),
                f"{original_name} vertex count changed",
            )

            for i, (actual, expected) in enumerate(
                zip(actual_positions, expected_positions)
            ):
                for axis, (a, e) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(
                        a,
                        e,
                        places=4,
                        msg=f"{original_name}.vtx[{i}] axis {axis}: {a} != {e}",
                    )

    def test_vertex_positions_with_parent_hierarchies(self):
        """Verify vertex alignment with parented meshes."""
        parent_a = pm.group(em=True, name="autoInst_parent_A")
        parent_b = pm.group(em=True, name="autoInst_parent_B")

        pm.xform(parent_a, ws=True, translation=(3, 0, 0), rotation=(0, 45, 0))
        pm.xform(parent_b, ws=True, translation=(-4, 6, 2), rotation=(30, 0, 15))

        # Create plain cubes with IDENTICAL geometry
        cubes = [pm.polyCube(name=f"autoInst_vtx_parent_cube_{i}")[0] for i in range(4)]

        # Assign same material to all cubes
        initial_sg = pm.PyNode("initialShadingGroup")
        for cube in cubes:
            pm.sets(initial_sg, forceElement=cube)

        pm.parent(cubes[0], parent_a)
        pm.parent(cubes[1], parent_a)
        pm.parent(cubes[2], parent_b)
        pm.parent(cubes[3], parent_b)

        # Apply local transforms WITHOUT scaling
        offsets = [
            {"t": (1, 0, 0), "r": (0, 0, 0)},
            {"t": (-2, 1, 0), "r": (0, 90, 0)},
            {"t": (0, -1, 2), "r": (45, 0, 45)},
            {"t": (2, 3, -1), "r": (10, 20, 30)},
        ]

        for cube, values in zip(cubes, offsets):
            pm.xform(cube, translation=values["t"], rotation=values["r"], os=True)

        original_positions = {
            cube.name(): self._get_vertex_world_positions(cube) for cube in cubes
        }

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        instancer.run([cube.name() for cube in cubes])

        for original_name, expected_positions in original_positions.items():
            mesh = pm.PyNode(original_name)
            actual_positions = self._get_vertex_world_positions(mesh)

            for i, (actual, expected) in enumerate(
                zip(actual_positions, expected_positions)
            ):
                for axis, (a, e) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(
                        a,
                        e,
                        places=4,
                        msg=f"{original_name}.vtx[{i}] axis {axis}: {a} != {e}",
                    )

    def test_frozen_transforms_preserve_alignment(self):
        """Verify instancing works correctly with frozen transforms."""
        cubes = [pm.polyCube(name=f"autoInst_frozen_cube_{i}")[0] for i in range(3)]

        initial_sg = pm.PyNode("initialShadingGroup")
        for cube in cubes:
            pm.sets(initial_sg, forceElement=cube)

        # All cubes start at origin - freeze them there so geometry is identical
        for cube in cubes:
            pm.makeIdentity(cube, apply=True, translate=True, rotate=False, scale=False)

        # NOW move them to different positions (after freezing)
        pm.xform(cubes[0], ws=True, translation=(0, 0, 0))
        pm.xform(cubes[1], ws=True, translation=(10, 0, 0))
        pm.xform(cubes[2], ws=True, translation=(20, 0, 0))

        # Capture positions after repositioning
        original_positions = {
            cube.name(): self._get_vertex_world_positions(cube) for cube in cubes
        }

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        report = instancer.run([cube.name() for cube in cubes])

        self.assertEqual(len(report), 1, "Should create instances from frozen cubes")

        for original_name, expected_positions in original_positions.items():
            mesh = pm.PyNode(original_name)
            actual_positions = self._get_vertex_world_positions(mesh)

            for i, (actual, expected) in enumerate(
                zip(actual_positions, expected_positions)
            ):
                for axis, (a, e) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(a, e, places=4)

    def test_negative_scale_mirrored_objects(self):
        """Verify handling of mirrored objects with negative scale."""
        cubes = [pm.polyCube(name=f"autoInst_mirror_cube_{i}")[0] for i in range(3)]

        initial_sg = pm.PyNode("initialShadingGroup")
        for cube in cubes:
            pm.sets(initial_sg, forceElement=cube)

        # Normal cube
        pm.xform(cubes[0], ws=True, translation=(0, 0, 0))

        # Mirrored cube (negative scale)
        pm.xform(cubes[1], ws=True, translation=(5, 0, 0), scale=(-1, 1, 1))

        # Another normal cube
        pm.xform(cubes[2], ws=True, translation=(10, 0, 0))

        original_positions = {
            cube.name(): self._get_vertex_world_positions(cube) for cube in cubes
        }

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        report = instancer.run([cube.name() for cube in cubes])

        # Should create instances only for non-mirrored cubes (0 and 2)
        # Mirrored cube has different geometry due to negative scale
        for original_name, expected_positions in original_positions.items():
            mesh = pm.PyNode(original_name)
            actual_positions = self._get_vertex_world_positions(mesh)

            for i, (actual, expected) in enumerate(
                zip(actual_positions, expected_positions)
            ):
                for axis, (a, e) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(a, e, places=4)

    def test_mixed_hierarchy_depths(self):
        """Verify instancing with objects at different hierarchy depths."""
        # Root level cube
        cube_root = pm.polyCube(name="autoInst_depth_root")[0]

        # 2 levels deep
        parent_1 = pm.group(em=True, name="autoInst_depth_p1")
        cube_level1 = pm.polyCube(name="autoInst_depth_l1")[0]
        pm.parent(cube_level1, parent_1)
        pm.xform(parent_1, ws=True, translation=(5, 0, 0))

        # 3 levels deep
        parent_2a = pm.group(em=True, name="autoInst_depth_p2a")
        parent_2b = pm.group(em=True, name="autoInst_depth_p2b")
        cube_level2 = pm.polyCube(name="autoInst_depth_l2")[0]
        pm.parent(parent_2b, parent_2a)
        pm.parent(cube_level2, parent_2b)
        pm.xform(parent_2a, ws=True, translation=(10, 0, 0))

        cubes = [cube_root, cube_level1, cube_level2]

        initial_sg = pm.PyNode("initialShadingGroup")
        for cube in cubes:
            pm.sets(initial_sg, forceElement=cube)

        original_positions = {
            cube.name(): self._get_vertex_world_positions(cube) for cube in cubes
        }
        expected_parents = {cube.name(): cube.getParent() for cube in cubes}

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        instancer.run([cube.name() for cube in cubes])

        # Verify positions and parent relationships preserved
        for original_name in original_positions.keys():
            mesh = pm.PyNode(original_name)
            self.assertEqual(mesh.getParent(), expected_parents[original_name])

            actual_positions = self._get_vertex_world_positions(mesh)
            expected_positions = original_positions[original_name]

            for i, (actual, expected) in enumerate(
                zip(actual_positions, expected_positions)
            ):
                for axis, (a, e) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(a, e, places=4)

    def test_different_pivot_points(self):
        """Verify alignment with different pivot point positions."""
        cubes = [pm.polyCube(name=f"autoInst_pivot_cube_{i}")[0] for i in range(3)]

        initial_sg = pm.PyNode("initialShadingGroup")
        for cube in cubes:
            pm.sets(initial_sg, forceElement=cube)

        # Set different pivot points
        pm.xform(cubes[0], ws=True, pivots=(0, 0, 0))
        pm.xform(cubes[1], ws=True, pivots=(1, 0, 0))
        pm.xform(cubes[2], ws=True, pivots=(0, 1, 0))

        # Move to different positions
        pm.xform(cubes[0], ws=True, translation=(0, 0, 0))
        pm.xform(cubes[1], ws=True, translation=(5, 0, 0))
        pm.xform(cubes[2], ws=True, translation=(10, 0, 0))

        original_positions = {
            cube.name(): self._get_vertex_world_positions(cube) for cube in cubes
        }

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        instancer.run([cube.name() for cube in cubes])

        for original_name, expected_positions in original_positions.items():
            mesh = pm.PyNode(original_name)
            actual_positions = self._get_vertex_world_positions(mesh)

            for i, (actual, expected) in enumerate(
                zip(actual_positions, expected_positions)
            ):
                for axis, (a, e) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(a, e, places=4)

    def test_construction_history_variations(self):
        """Verify instancing works with and without construction history."""
        cube_with_history = pm.polyCube(name="autoInst_hist_with")[0]
        cube_no_history = pm.polyCube(name="autoInst_hist_without")[0]
        cube_with_history2 = pm.polyCube(name="autoInst_hist_with2")[0]

        # Delete history on one cube
        pm.delete(cube_no_history, constructionHistory=True)

        cubes = [cube_with_history, cube_no_history, cube_with_history2]

        initial_sg = pm.PyNode("initialShadingGroup")
        for cube in cubes:
            pm.sets(initial_sg, forceElement=cube)

        pm.xform(cubes[0], ws=True, translation=(0, 0, 0))
        pm.xform(cubes[1], ws=True, translation=(5, 0, 0))
        pm.xform(cubes[2], ws=True, translation=(10, 0, 0))

        original_positions = {
            cube.name(): self._get_vertex_world_positions(cube) for cube in cubes
        }

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        report = instancer.run([cube.name() for cube in cubes])

        # All should instance together regardless of history
        self.assertEqual(len(report), 1)

        for original_name, expected_positions in original_positions.items():
            mesh = pm.PyNode(original_name)
            actual_positions = self._get_vertex_world_positions(mesh)

            for i, (actual, expected) in enumerate(
                zip(actual_positions, expected_positions)
            ):
                for axis, (a, e) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(a, e, places=4)

    def test_non_mesh_objects_skipped(self):
        """Verify non-mesh objects are gracefully skipped."""
        cube = pm.polyCube(name="autoInst_mixed_cube")[0]
        locator = pm.spaceLocator(name="autoInst_mixed_loc")
        camera = pm.camera(name="autoInst_mixed_cam")[0]

        # Should not crash, just skip non-mesh objects
        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        report = instancer.run([cube.name(), locator.name(), camera.name()])

        # No instances created (only one mesh)
        self.assertEqual(len(report), 0)

    def test_large_scale_performance(self):
        """Verify performance with larger number of objects."""
        num_cubes = 50
        cubes = [
            pm.polyCube(name=f"autoInst_perf_cube_{i}")[0] for i in range(num_cubes)
        ]

        initial_sg = pm.PyNode("initialShadingGroup")
        for cube in cubes:
            pm.sets(initial_sg, forceElement=cube)

        # Distribute in grid
        import math

        grid_size = int(math.sqrt(num_cubes))
        for i, cube in enumerate(cubes):
            x = (i % grid_size) * 3
            z = (i // grid_size) * 3
            pm.xform(cube, ws=True, translation=(x, 0, z))

        # Sample a few vertex positions to verify
        sample_cubes = [cubes[0], cubes[num_cubes // 2], cubes[-1]]
        original_positions = {
            cube.name(): self._get_vertex_world_positions(cube) for cube in sample_cubes
        }

        instancer = self._create_instancer(tolerance=0.99, require_same_material=True)
        report = instancer.run([cube.name() for cube in cubes])

        # Should create many instances
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["instance_count"], num_cubes - 1)

        # Verify sample positions preserved
        for original_name, expected_positions in original_positions.items():
            mesh = pm.PyNode(original_name)
            actual_positions = self._get_vertex_world_positions(mesh)

            for i, (actual, expected) in enumerate(
                zip(actual_positions, expected_positions)
            ):
                for axis, (a, e) in enumerate(zip(actual, expected)):
                    self.assertAlmostEqual(a, e, places=4)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    mtk.clear_scrollfield_reporters()

    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromTestCase(AutoInstancerTest)

    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
