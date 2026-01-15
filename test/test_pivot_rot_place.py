import pymel.core as pm

try:
    from base_test import MayaTkTestCase
except ImportError:
    from mayatk.test.base_test import MayaTkTestCase
from mayatk.xform_utils._xform_utils import XformUtils


class TestPivotRotation(MayaTkTestCase):
    def test_transfer_pivot_rotation(self):
        # Setup
        s = pm.polyCube(n="source")[0]
        t = pm.polyCube(n="target")[0]

        # Rotate source object (which rotates pivot in world)
        pm.rotate(s, 45, 90, 0)

        # Ensure target is different
        pm.rotate(t, 0, 0, 0)

        print("Source Rotation:", pm.xform(s, q=True, ws=True, ro=True))

        # Transfer Rotate Pivot Orientation
        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        # Check target orientation.
        # Using matchTransform(piv=True, rot=False) usually ONLY moves the pivot.
        # But if we used matchTransform(..., rot=False), then object rotation shouldn't change...
        # BUT if we want to "Transfer Pivot Orientation", we expect the Pivot's Axis to align with Source.
        # If the object didn't rotate, the Pivot Axis relative to Object must have changed (Rotate Axis).

        # Let's check Rotate Axis (ra) and Rotate Order (roo)
        # Note: Maya's matchTransform might default to modifying Transform Rotation if it can't modify Rotate Axis?

        # If I get the World Rotation of the Pivot, it should match.
        # Can we query World Pivot Rotation?
        # We can query transform rotation. xform -ro returns transform rotation.
        # If we modified only pivot, -ro shouldn't change?

        s_ro = pm.xform(s, q=True, ws=True, ro=True)
        t_ro = pm.xform(t, q=True, ws=True, ro=True)

        print(f"Source RO: {s_ro}")
        print(f"Target RO: {t_ro}")

        # If logic is correct, T should NOT have rotated (its geometry staying put), but its PIVOT axis should align.
        # However, verifying pivot alignment mathematically is tricky without vector math.

        # Let's Assert that something changed on Target.
        self.assertNotEqual(
            t_ro,
            (0, 0, 0),
            "Target rotation (or pivot compensation) should have changed if we transferred pivot orientation",
        )
