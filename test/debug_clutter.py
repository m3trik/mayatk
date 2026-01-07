import unittest
import pymel.core as pm
import mayatk.core_utils.instancing.auto_instancer as auto_instancer
from mayatk import AutoInstancer
import logging
import random
import sys
import os

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("mayatk.core_utils.instancing")
logger.setLevel(logging.DEBUG)


class DebugClutter(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)

    def create_canister(self, name_prefix="Canister"):
        """Creates a simple canister assembly (Body + Lid)."""
        # Body: Cylinder
        body = pm.polyCylinder(r=1, h=4, name=f"{name_prefix}_Body")[0]
        # Lid: Flattened Sphere
        lid = pm.polySphere(r=1, name=f"{name_prefix}_Lid")[0]
        lid.setTranslation([0, 2.5, 0])
        lid.setScale([1, 0.2, 1])

        # Group
        grp = pm.group(body, lid, name=f"{name_prefix}_Grp")
        return grp, body, lid

    def randomize_transform(self, transform, pos_range=20):
        """Applies random position and rotation."""
        x = random.uniform(-pos_range, pos_range)
        y = random.uniform(0, pos_range / 2)  # Keep somewhat above ground
        z = random.uniform(-pos_range, pos_range)

        rx = random.uniform(0, 360)
        ry = random.uniform(0, 360)
        rz = random.uniform(0, 360)

        transform.setTranslation([x, y, z])
        transform.setRotation([rx, ry, rz])

    def test_clutter_rejection(self):
        """Test that random clutter is rejected and only valid assemblies are recovered."""
        # Create 3 identical canisters (should be instanced)
        for i in range(3):
            grp, _, _ = self.create_canister(f"Canister_{i}")
            self.randomize_transform(grp)
            pm.parent(grp.getChildren(), world=True)
            pm.delete(grp)

        # Add some random junk
        for i in range(10):
            junk = pm.polyCone(name=f"Junk_{i}")[0]
            self.randomize_transform(junk)

        # Combine
        shapes = [
            t for t in pm.ls(type="transform") if t.getShape() and not t.isReadOnly()
        ]
        combined = pm.polyUnite(shapes, name="Combined_Clutter", ch=False)[0]

        # Run
        # Increased tolerance to handle baked transform noise
        instancer = AutoInstancer(
            separate_combined=True, verbose=True, is_static=False, tolerance=0.1
        )
        instancer.run([combined])

        # Verify
        assemblies = pm.ls("Assembly_*", type="transform")
        print(f"DEBUG: Found assemblies: {assemblies}")
        for a in assemblies:
            print(f"  {a}: {a.getChildren()}")

        canister_assemblies = [a for a in assemblies if len(a.getChildren()) == 2]
        print(f"DEBUG: Canister assemblies: {canister_assemblies}")

        self.assertGreaterEqual(
            len(canister_assemblies), 3, "Should have recovered canister assemblies"
        )


if __name__ == "__main__":
    # Create a dummy test suite to run just this test
    suite = unittest.TestSuite()
    suite.addTest(DebugClutter("test_clutter_rejection"))

    # Run
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)
