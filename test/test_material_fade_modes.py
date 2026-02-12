# !/usr/bin/python
# coding=utf-8
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock maya modules BEFORE importing mayatk
sys.modules["maya"] = MagicMock()
sys.modules["maya.cmds"] = MagicMock()
sys.modules["pymel"] = MagicMock()
sys.modules["pymel.core"] = MagicMock()

# Now we can import the module under test
# Note: we import the implementation directly or via the package if we mock the lazy loader
# For simplicity, let's test the facade class structure
from mayatk.mat_utils.material_fade._material_fade import MaterialFade
from mayatk.mat_utils.material_fade.attribute_mode import FadeAttributeMode
from mayatk.mat_utils.material_fade.material_mode import FadeMaterialMode


class TestMaterialFadeRefactor(unittest.TestCase):
    """Verify that MaterialFade delegates correctly to sub-modules."""

    @patch.object(FadeAttributeMode, "setup")
    def test_setup_delegates_to_attribute_mode(self, mock_setup):
        """Test that setup(mode="attribute") calls FadeAttributeMode.setup."""
        mock_objects = ["obj1", "obj2"]
        MaterialFade.setup(
            objects=mock_objects, mode="attribute", start_frame=1, end_frame=10
        )
        mock_setup.assert_called_once()
        # Check arguments roughly
        args, kwargs = mock_setup.call_args
        self.assertEqual(args[0], mock_objects)

    @patch.object(FadeMaterialMode, "setup")
    def test_setup_delegates_to_material_mode(self, mock_setup):
        """Test that setup(mode="material") calls FadeMaterialMode.setup."""
        mock_objects = ["obj1", "obj2"]
        MaterialFade.setup(
            objects=mock_objects, mode="material", start_frame=1, end_frame=10
        )
        mock_setup.assert_called_once()

    @patch.object(FadeAttributeMode, "bake")
    def test_bake_delegates_to_attribute_mode(self, mock_bake):
        MaterialFade.bake(mode="attribute")
        mock_bake.assert_called_once()

    @patch.object(FadeMaterialMode, "bake")
    def test_bake_delegates_to_material_mode(self, mock_bake):
        MaterialFade.bake(mode="material")
        mock_bake.assert_called_once()

    @patch.object(FadeAttributeMode, "remove")
    def test_remove_delegates_to_attribute_mode(self, mock_remove):
        MaterialFade.remove(mode="attribute")
        mock_remove.assert_called_once()

    @patch.object(FadeMaterialMode, "remove")
    def test_remove_delegates_to_material_mode(self, mock_remove):
        MaterialFade.remove(mode="material")
        mock_remove.assert_called_once()

    def test_constants_are_exposed(self):
        """Verify that constants are mapped correctly."""
        self.assertEqual(MaterialFade.ATTR_NAME, FadeAttributeMode.ATTR_NAME)
        self.assertEqual(MaterialFade.FADE_SUFFIX, FadeMaterialMode.FADE_SUFFIX)


if __name__ == "__main__":
    unittest.main()
