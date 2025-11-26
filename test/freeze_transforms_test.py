# !/usr/bin/python
# coding=utf-8
"""Unit tests for freeze_transforms pivot preservation behavior.

Run via mayapy:
    $env:PYTHONPATH = "o:\\Cloud\\Code\\_scripts\\mayatk"
    & "C:\\Program Files\\Autodesk\\Maya2025\\bin\\mayapy.exe" o:\\Cloud\\Code\\_scripts\\mayatk\\test\\freeze_transforms_test.py
"""
import unittest
import pymel.core as pm
import mayatk as mtk


class FreezeTransformsPivotPreservationTest(unittest.TestCase):
    """Tests that freeze_transforms preserves custom pivot positions."""

    TOLERANCE = 1e-4

    def setUp(self):
        """Create a fresh scene for each test."""
        pm.mel.file(new=True, force=True)

    def tearDown(self):
        """Clean up after each test."""
        pm.mel.file(new=True, force=True)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _vectors_equal(self, a, b, tol=None):
        """Return True if two 3-component vectors are within tolerance."""
        tol = tol if tol is not None else self.TOLERANCE
        return all(abs(ai - bi) < tol for ai, bi in zip(a, b))

    def _create_transformed_cube(self, name="testCube"):
        """Create a cube with non-identity TRS and an offset pivot."""
        cube = pm.polyCube(name=name)[0]
        cube.translate.set((5, 2, -3))
        cube.rotate.set((15, 30, -10))
        cube.scale.set((1.5, 0.8, 1.2))
        # Set a custom world-space pivot
        pm.xform(cube, ws=True, rp=(3, 4, 2))
        pm.xform(cube, ws=True, sp=(3, 4, 2))
        return cube

    # -------------------------------------------------------------------------
    # Tests
    # -------------------------------------------------------------------------

    def test_freeze_translate_preserves_pivot(self):
        """Freezing translation should not move the pivot in world space."""
        cube = self._create_transformed_cube("freezeTCube")
        pivot_before = pm.xform(cube, q=True, ws=True, rp=True)

        mtk.freeze_transforms(cube, t=True)

        pivot_after = pm.xform(cube, q=True, ws=True, rp=True)
        self.assertTrue(
            self._vectors_equal(pivot_before, pivot_after),
            f"Pivot moved after freeze translate: before={pivot_before}, after={pivot_after}",
        )

    def test_freeze_rotate_preserves_pivot(self):
        """Freezing rotation should not move the pivot in world space."""
        cube = self._create_transformed_cube("freezeRCube")
        pivot_before = pm.xform(cube, q=True, ws=True, rp=True)

        mtk.freeze_transforms(cube, r=True)

        pivot_after = pm.xform(cube, q=True, ws=True, rp=True)
        self.assertTrue(
            self._vectors_equal(pivot_before, pivot_after),
            f"Pivot moved after freeze rotate: before={pivot_before}, after={pivot_after}",
        )

    def test_freeze_scale_preserves_pivot(self):
        """Freezing scale should not move the pivot in world space."""
        cube = self._create_transformed_cube("freezeSCube")
        pivot_before = pm.xform(cube, q=True, ws=True, rp=True)

        mtk.freeze_transforms(cube, s=True)

        pivot_after = pm.xform(cube, q=True, ws=True, rp=True)
        self.assertTrue(
            self._vectors_equal(pivot_before, pivot_after),
            f"Pivot moved after freeze scale: before={pivot_before}, after={pivot_after}",
        )

    def test_freeze_all_preserves_pivot(self):
        """Freezing TRS together should not move the pivot in world space."""
        cube = self._create_transformed_cube("freezeAllCube")
        pivot_before = pm.xform(cube, q=True, ws=True, rp=True)

        mtk.freeze_transforms(cube, t=True, r=True, s=True)

        pivot_after = pm.xform(cube, q=True, ws=True, rp=True)
        self.assertTrue(
            self._vectors_equal(pivot_before, pivot_after),
            f"Pivot moved after freeze all: before={pivot_before}, after={pivot_after}",
        )

    def test_freeze_preserves_geometry_world_position(self):
        """Freezing should keep geometry vertices at the same world positions."""
        cube = self._create_transformed_cube("freezeGeoCube")
        # Record world positions of all vertices
        verts = pm.ls(cube.vtx, flatten=True)
        positions_before = [pm.xform(v, q=True, ws=True, t=True) for v in verts]

        mtk.freeze_transforms(cube, t=True, r=True, s=True)

        positions_after = [pm.xform(v, q=True, ws=True, t=True) for v in verts]
        for before, after in zip(positions_before, positions_after):
            self.assertTrue(
                self._vectors_equal(before, after),
                f"Vertex moved: before={before}, after={after}",
            )

    def test_freeze_respects_rotate_pivot_translate(self):
        """World pivot position should be preserved when rotatePivotTranslate is set.

        Note: Maya's makeIdentity automatically adjusts rotatePivotTranslate to
        maintain the world pivot position, so we verify world position not raw values.
        """
        cube = self._create_transformed_cube("freezeRPTCube")
        cube.rotatePivotTranslate.set((1, 2, 3))
        pivot_before = pm.xform(cube, q=True, ws=True, rp=True)

        mtk.freeze_transforms(cube, t=True, r=True, s=True)

        pivot_after = pm.xform(cube, q=True, ws=True, rp=True)
        self.assertTrue(
            self._vectors_equal(pivot_before, pivot_after),
            f"Pivot world position changed: before={pivot_before}, after={pivot_after}",
        )

    def test_freeze_respects_scale_pivot_translate(self):
        """World pivot position should be preserved when scalePivotTranslate is set.

        Note: Maya's makeIdentity automatically adjusts scalePivotTranslate to
        maintain the world pivot position, so we verify world position not raw values.
        """
        cube = self._create_transformed_cube("freezeSPTCube")
        cube.scalePivotTranslate.set((0.5, 1.5, -0.5))
        pivot_before = pm.xform(cube, q=True, ws=True, sp=True)

        mtk.freeze_transforms(cube, t=True, r=True, s=True)

        pivot_after = pm.xform(cube, q=True, ws=True, sp=True)
        self.assertTrue(
            self._vectors_equal(pivot_before, pivot_after),
            f"Scale pivot world position changed: before={pivot_before}, after={pivot_after}",
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Clear any existing script editors / reporters
    try:
        mtk.clear_scrollfield_reporters()
    except Exception:
        pass

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(FreezeTransformsPivotPreservationTest)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with proper code for CI/CD
    import sys

    sys.exit(0 if result.wasSuccessful() else 1)
