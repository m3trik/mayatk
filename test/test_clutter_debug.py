import unittest
import pymel.core as pm
import sys
import os
import random
import math

# Add current directory to path to allow importing sibling tests
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from mayatk.core_utils.instancing.auto_instancer import AutoInstancer
from mayatk.core_utils.instancing.geometry_matcher import GeometryMatcher
from base_test import MayaTkTestCase


class TestClutterDebug(MayaTkTestCase):
    def create_canister(self, name_prefix="Canister"):
        """Create a canister assembly (Cylinder Body + Torus Handle)."""
        # Body
        body = pm.polyCylinder(
            r=1, h=4, sx=20, sy=1, sz=1, ax=(0, 1, 0), name=f"{name_prefix}_Body"
        )[0]
        # Handle (Torus on top)
        handle = pm.polyTorus(
            r=0.5, sr=0.1, ax=(0, 0, 1), name=f"{name_prefix}_Handle"
        )[0]
        pm.move(handle, 0, 2, 0)

        # Group
        grp = pm.group(body, handle, name=name_prefix)
        return grp, body, handle

    def randomize_transform(self, node):
        """Apply random rotation and translation."""
        rx = random.uniform(-180, 180)
        ry = random.uniform(-180, 180)
        rz = random.uniform(-180, 180)
        tx = random.uniform(-50, 50)
        ty = random.uniform(-50, 50)
        tz = random.uniform(-50, 50)

        pm.rotate(node, rx, ry, rz)
        pm.move(node, tx, ty, tz)

    def test_canisters_random_rotation(self):
        """Test 10 canisters with random rotations combined into one mesh."""
        # 1. Create 10 canisters with random rotations
        random.seed(42)

        num_canisters = 10
        canisters = []
        for i in range(num_canisters):
            grp, _, _ = self.create_canister(name_prefix=f"Assembly_{i+1}")
            self.randomize_transform(grp)
            canisters.append(grp)

        # 2. Combine them into one mesh
        meshes = []
        for c in canisters:
            meshes.extend(c.getChildren(type="transform"))

        combined_mesh = pm.polyUnite(meshes, n="Combined_Canisters", ch=False)[0]
        combined_mesh = pm.PyNode(combined_mesh)

        # Delete the original empty groups
        for c in canisters:
            if pm.objExists(c):
                pm.delete(c)

        # 3. Run AutoInstancer
        instancer = AutoInstancer(
            tolerance=0.01,
            search_radius_mult=2.0,
            separate_combined=True,
            combine_assemblies=True,
            check_hierarchy=True,
            verbose=True,
        )
        instancer.run(combined_mesh)

        # 4. Verify
        # Get all assemblies (groups containing meshes)
        # We expect 10 groups, each containing the reconstructed parts

        # Filter for top-level groups that look like assemblies
        all_transforms = pm.ls(type="transform")
        assemblies = []
        for t in all_transforms:
            # Skip cameras and default nodes
            if t.name() in ["persp", "top", "front", "side", "Combined_Canisters"]:
                continue
            # Skip shapes
            if t.getShape():
                # If it has a shape, it might be a combined assembly (single mesh)
                # Check if it's one of our assemblies
                if "Assembly_" in t.name():
                    assemblies.append(t)
                continue

            # If it has children, it might be a group assembly
            if t.getChildren():
                if "Assembly_" in t.name():
                    assemblies.append(t)
                continue

        print(f"Found {len(assemblies)} assemblies: {assemblies}")
        for a in assemblies:
            print(f"  {a}: {a.getChildren()}")

        self.assertEqual(
            len(assemblies),
            10,
            f"Should have reconstructed 10 assemblies, found {len(assemblies)}",
        )


if __name__ == "__main__":
    unittest.main()
