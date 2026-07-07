import maya.cmds as cmds
import sys
import os

# Add test directory to path to import base_test
test_dir = os.path.dirname(__file__)
if test_dir not in sys.path:
    sys.path.append(test_dir)

import base_test
from mayatk.core_utils.auto_instancer._auto_instancer import AutoInstancer


class TestOriginalMeshSeparated(base_test.QuickTestCase):
    def test_original_mesh_separated(self):
        """
        Test instancing on a group named 'original_mesh_separated' containing identical objects.
        """
        # 1. Setup: Create the group and objects
        group = cmds.group(em=True, n="original_mesh_separated")

        # Create a prototype cube. Subdivided well above MICRO_TRI_THRESHOLD
        # (300) so the group instances rather than being deferred to the
        # remainder-combine (below the threshold, repeated duplicates merge
        # instead — see test_strategy_micro_mesh_duplicates_merge_when_combining).
        proto = cmds.polyCube(w=10, h=10, d=10, sx=6, sy=6, sz=6, n="Cube_Proto")[0]

        # Create duplicates inside the group
        cubes = []
        for i in range(5):
            dup = cmds.duplicate(proto, n=f"Cube_{i}")[0]
            cmds.parent(dup, group)
            cmds.move(i * 20, 0, 0, dup)
            cubes.append(dup)

        cmds.delete(proto)  # Cleanup prototype, we only want the group content

        # 2. Execution: Run AutoInstancer
        # Pass children of the group, as AutoInstancer expects leaf meshes by default.
        # separate_combined=True enables the second-pass leaf instancer; without
        # it, low-triangle meshes are routed to the COMBINE strategy and the
        # first pass (GPU_INSTANCE only) skips them.
        children = (cmds.listRelatives(str(group), children=True, type="transform") or [])
        instancer = AutoInstancer(separate_combined=True)
        instancer.run(nodes=children)

        # 3. Verification
        # We expect the cubes to be instanced.

        # Check that we have instances
        shapes = cmds.ls(dag=True, leaf=True, type="mesh")
        # Filter for shapes in our group (any DAG path under the group)
        group_long = cmds.ls(group, long=True)[0]
        group_shapes = []
        for s in shapes:
            paths = cmds.ls(s, long=True) or []
            if any(p.startswith(group_long + "|") for p in paths):
                group_shapes.append(s)

        # Check if the shapes are instances
        instance_count = 0
        for shape in group_shapes:
            parents = cmds.listRelatives(shape, allParents=True) or []
            if len(parents) > 1:
                instance_count += 1

        # We expect all of them to be instances (sharing the shape)
        # If we have 5 cubes, and they are all instanced to 1 shape,
        # that 1 shape has 5 parents.

        first_shape = group_shapes[0]

        # Check instances using listRelatives(allParents=True)
        # Note: We need to pass the MObject or ensure we get all parents of the underlying node
        parents = cmds.listRelatives(first_shape, allParents=True)

        print(f"Shape: {first_shape}")
        print(f"Shape parents (listRelatives): {len(parents)}")
        print(f"Parents: {parents}")

        # Check if all shapes in the group are actually the same object
        # PyNodes compare equal if they point to the same underlying object
        unique_shapes = list(set(group_shapes))
        print(f"Unique PyNodes (set): {len(unique_shapes)}")
        print(f"Total PyNodes found via ls: {len(group_shapes)}")

        self.assertTrue(
            len(parents) >= 5, "Expected at least 5 instances (parents) for the shape"
        )
