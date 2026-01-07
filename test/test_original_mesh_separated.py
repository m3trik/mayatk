import pymel.core as pm
import sys
import os

# Add test directory to path to import base_test
test_dir = os.path.dirname(__file__)
if test_dir not in sys.path:
    sys.path.append(test_dir)

import base_test
from mayatk.core_utils.instancing.auto_instancer import AutoInstancer


class TestOriginalMeshSeparated(base_test.QuickTestCase):
    def test_original_mesh_separated(self):
        """
        Test instancing on a group named 'original_mesh_separated' containing identical objects.
        """
        # 1. Setup: Create the group and objects
        group = pm.group(em=True, n="original_mesh_separated")

        # Create a prototype cube
        proto = pm.polyCube(w=10, h=10, d=10, n="Cube_Proto")[0]
        # Add some detail to make it unique/identifiable if needed, but simple cube is fine for basic test

        # Create duplicates inside the group
        cubes = []
        for i in range(5):
            dup = pm.duplicate(proto, n=f"Cube_{i}")[0]
            pm.parent(dup, group)
            pm.move(dup, i * 20, 0, 0)
            cubes.append(dup)

        pm.delete(proto)  # Cleanup prototype, we only want the group content

        # 2. Execution: Run AutoInstancer
        # Pass children of the group, as AutoInstancer expects leaf meshes by default
        children = group.getChildren(type="transform")
        instancer = AutoInstancer()
        instancer.run(nodes=children)

        # 3. Verification
        # We expect the cubes to be instanced.

        # Check that we have instances
        shapes = pm.ls(dag=True, leaf=True, type="mesh")
        # Filter for shapes in our group
        group_shapes = [s for s in shapes if s.isChildOf(group)]

        # Check if the shapes are instances
        instance_count = 0
        for shape in group_shapes:
            # getAllParents() returns all parents of the shape
            parents = shape.getAllParents()
            if len(parents) > 1:
                instance_count += 1

        # We expect all of them to be instances (sharing the shape)
        # If we have 5 cubes, and they are all instanced to 1 shape,
        # that 1 shape has 5 parents.

        first_shape = group_shapes[0]

        # Check instances using listRelatives(allParents=True)
        # Note: We need to pass the MObject or ensure we get all parents of the underlying node
        parents = pm.listRelatives(first_shape, allParents=True)

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
