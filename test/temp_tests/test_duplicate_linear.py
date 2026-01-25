import unittest
import pymel.core as pm
import sys
import os

# Adjust path to find base_test
test_dir = r"O:\Cloud\Code\_scripts\mayatk\test"
if test_dir not in sys.path:
    sys.path.append(test_dir)
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.append(scripts_dir)

from base_test import MayaTkTestCase
from mayatk.edit_utils.duplicate_linear import DuplicateLinear


class TestDuplicateLinear(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.cube = pm.polyCube(name="test_cube")[0]

    def test_duplicate_as_copies(self):
        """Test default behavior (creating copies)."""
        # Linear duplication of 3 copies
        copies_dict = DuplicateLinear.duplicate_linear(
            objects=[self.cube], num_copies=3, translate=(2, 0, 0), instance=False
        )

        copies = copies_dict[self.cube]
        self.assertEqual(len(copies), 3)

        # Verify they are copies, not instances
        # Instances share the same shape node
        shape_original = self.cube.getShape()
        for i, copy in enumerate(copies):
            shape_copy = copy.getShape()
            self.assertNotEqual(
                shape_original, shape_copy, f"Copy {i} is an instance of original"
            )

            # Verify translation
            pos = copy.getTranslation(space="world")
            # It should have moved along X
            self.assertNotAlmostEqual(pos.x, 0)

    def test_duplicate_as_instances(self):
        """Test instance creation."""
        copies_dict = DuplicateLinear.duplicate_linear(
            objects=[self.cube], num_copies=3, translate=(2, 0, 0), instance=True
        )

        copies = copies_dict[self.cube]
        self.assertEqual(len(copies), 3)

        # Verify they are instances
        # Check if the shape has multiple parents
        shape_original = self.cube.getShape()
        # Ensure the shape is instanced
        self.assertTrue(
            shape_original.isInstanced(), "Original shape should be instanced"
        )

        # Check that copies share the same shape instance
        for i, copy in enumerate(copies):
            shape_copy = copy.getShape()
            # Compare underlying MObjects to verify they are the same node
            self.assertEqual(
                shape_original.__apimobject__(),
                shape_copy.__apimobject__(),
                f"Copy {i} does not share the same shape MObject",
            )


if __name__ == "__main__":
    unittest.main()
