import pymel.core as pm
import maya.cmds as cmds
import math

try:
    from base_test import MayaTkTestCase
except ImportError:
    from mayatk.test.base_test import MayaTkTestCase
from mayatk.xform_utils._xform_utils import XformUtils


class TestPivotTransferScenarios(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.thresh = 0.001

    def _get_pivot_matrix(self, obj):
        # We can construct the pivot matrix from rp and ra?
        # Or simpler: Query the world transformation of the pivot?
        # xform -q -matrix returns the object's matrix.
        # We specifically want the orientation of the PIVOT.
        # If we use matchTransform, we expect the axes of the manip handle to match.

        # We can create a temporary locator, match it to the pivot, and get its matrix?
        loc = pm.spaceLocator()
        pm.matchTransform(loc, obj, piv=True, pos=True, rot=True)
        # Note: If obj has rotateAxis, matchTransform -piv includes that in the rotation of the locator?
        # Let's verify.
        mat = pm.xform(loc, q=True, ws=True, matrix=True)
        pm.delete(loc)
        return mat

    def _assert_matrices_close(self, mat1, mat2):
        for i in range(16):
            if abs(mat1[i] - mat2[i]) > self.thresh:
                self.fail(f"Matrices differ at index {i}: {mat1[i]} vs {mat2[i]}")

    def test_transfer_from_rotated_source(self):
        """Source is rotated. Target is identity."""
        print("\n--- test_transfer_from_rotated_source ---")
        s = pm.polyCube(n="source")[0]
        t = pm.polyCube(n="target")[0]

        pm.rotate(s, 45, 45, 0)

        # Transfer
        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        # Check
        m1 = self._get_pivot_matrix(s)
        m2 = self._get_pivot_matrix(t)
        self._assert_matrices_close(m1, m2)

    def test_transfer_to_frozen_target(self):
        """Source is rotated. Target is frozen (geometry rotated, transform identity)."""
        print("\n--- test_transfer_to_frozen_target ---")
        s = pm.polyCube(n="source")[0]
        t = pm.polyCube(n="target")[0]

        pm.rotate(s, 45, 0, 0)

        # Rotate and freeze target
        pm.rotate(t, 0, 90, 0)
        pm.makeIdentity(t, apply=True, r=True)

        # Transfer
        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        m1 = self._get_pivot_matrix(s)
        m2 = self._get_pivot_matrix(t)
        self._assert_matrices_close(m1, m2)

    def test_transfer_from_frozen_with_edited_pivot(self):
        """Source is frozen but has modified pivot (rotateAxis). Target is identity."""
        print("\n--- test_transfer_from_frozen_with_edited_pivot ---")
        s = pm.polyCube(n="source")[0]
        t = pm.polyCube(n="target")[0]

        # Rotate source and freeze
        pm.rotate(s, 0, 45, 0)
        pm.makeIdentity(s, apply=True, r=True)
        # Pivot is now 0,0,0 (World aligned). Object appears rotated.

        # Manually edit source pivot to align with 'something' (e.g. 45 deg)
        # We can set rotateAxis
        pm.xform(s, ra=[0, 45, 0])
        # Now source pivot is rotated 45 deg, but transform rotate is 0.

        # Transfer to target
        XformUtils.transfer_pivot([s, t], rotate=True, world_space=True)

        m1 = self._get_pivot_matrix(s)
        m2 = self._get_pivot_matrix(t)
        self._assert_matrices_close(m1, m2)

    def test_transfer_preserve_target_pos(self):
        """Ensure transfer pivot (Rotation only) doesn't move Pivot Position if translate=False."""
        print("\n--- test_transfer_preserve_target_pos ---")
        s = pm.polyCube(n="source")[0]
        t = pm.polyCube(n="target")[0]

        pm.move(s, 10, 0, 0)
        pm.rotate(s, 0, 45, 0)

        pm.move(t, -10, 0, 0)

        orig_pos = pm.xform(t, q=True, ws=True, rp=True)

        XformUtils.transfer_pivot(
            [s, t], rotate=True, translate=False, world_space=True
        )

        new_pos = pm.xform(t, q=True, ws=True, rp=True)

        # Orientation should match
        m1 = self._get_pivot_matrix(s)
        m2 = self._get_pivot_matrix(t)

        # Checking rotation part only (upper 3x3)
        # Indices: 0,1,2, 4,5,6, 8,9,10
        for i in [0, 1, 2, 4, 5, 6, 8, 9, 10]:
            if abs(m1[i] - m2[i]) > self.thresh:
                self.fail(f"Rotation Matrices differ at index {i}: {m1[i]} vs {m2[i]}")

        # Position should NOT match source, should match original
        self.assertAlmostEqual(new_pos[0], orig_pos[0])
