import unittest
import os
import maya.cmds as cmds

import pythontk as ptk

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

#: Scratch artifacts go in the gitignored ``test/temp_tests/``, never beside this
#: module in the tracked tree. ``None`` (exec'd without a file, as from the Script
#: Editor) falls back to the system temp folder.
try:
    TEMP_TESTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_tests")
except NameError:
    TEMP_TESTS = None

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

# Try to initialize QApplication to avoid "Cannot create a QWidget without QApplication" error
# which might be triggered by mayatk imports
try:
    from PySide2.QtWidgets import QApplication

    if not QApplication.instance():
        app = QApplication([])
except ImportError:
    try:
        from PySide6.QtWidgets import QApplication

        if not QApplication.instance():
            app = QApplication([])
    except ImportError:
        pass

# Import the new module
try:
    from mayatk.nurbs_utils.image_tracer import ImageTracer
except ImportError as e:
    # Fallback: try importing directly if mayatk package fails (e.g. due to UI issues)
    # This requires the path to be set up such that nurbs_utils is importable
    try:
        from nurbs_utils.image_tracer import ImageTracer
    except ImportError:
        print(f"Failed to import ImageTracer: {e}")
        ImageTracer = None


class TestImageTracer(unittest.TestCase):
    def setUp(self):
        self.keep_scene = True
        artifacts = ptk.TempArtifacts("image_tracer", policy="scoped", dir=TEMP_TESTS)
        self.addCleanup(artifacts.cleanup)
        self.scratch = artifacts.dir_path()
        self.test_image_path = os.path.join(self.scratch, "test_shape.png")
        self.create_test_image()

    def create_test_image(self):
        if cv2 is None or np is None:
            return
        img = np.zeros((512, 512, 3), np.uint8)
        cv2.rectangle(img, (100, 100), (300, 300), (255, 255, 255), -1)
        cv2.circle(img, (400, 400), 50, (255, 255, 255), -1)
        cv2.imwrite(self.test_image_path, img)

    def test_trace_curves(self):
        if ImageTracer is None:
            self.skipTest("ImageTracer module not found")

        # Test with simplification
        tracer = ImageTracer(
            self.test_image_path,
            scale=0.1,
            simplify=1.0,
        )
        curves = tracer.trace_curves()
        self.assertTrue(len(curves) > 0, "Should have created curves")

        # Verify degree is 1 (linear) as smoothing is disabled
        if curves:
            # curves[0] is a transform string; use cmds to query the shape
            shapes = (
                cmds.listRelatives(str(curves[0]), shapes=True, fullPath=True) or []
            )
            self.assertTrue(shapes, "Curve should have a shape child")
            self.assertEqual(
                cmds.getAttr(f"{shapes[0]}.degree"),
                1,
                "Curve should be degree 1 (linear)",
            )

        # Cleanup for this specific test if we want to be strict,
        # but we are keeping scene for now.
        if self.keep_scene:
            cmds.group(curves, name="test_trace_curves_grp")

    def test_trace_an_image_in_a_non_ascii_folder(self):
        """``cv2.imread`` opens no path that is not ASCII on Windows: an image
        in a folder named "Résumé ★" raised "Failed to read image" though it
        was there. Read through ``ptk.ImgUtils.cv2_read`` now. Fixed: 2026-10-05."""
        if ImageTracer is None:
            self.skipTest("ImageTracer module not found")
        if cv2 is None:
            self.skipTest("OpenCV not found")
        import shutil

        folder = os.path.join(self.scratch, "Résumé ★")
        os.makedirs(folder)
        path = os.path.join(folder, "test_shape.png")
        shutil.copy2(self.test_image_path, path)

        curves = ImageTracer(path, scale=0.1).trace_curves()
        self.assertTrue(curves, "no curves traced from the image")

    def test_create_mesh(self):
        if ImageTracer is None:
            self.skipTest("ImageTracer module not found")

        tracer = ImageTracer(self.test_image_path, scale=0.1)
        result_grp = tracer.create_mesh(name="positive_mesh")
        self.assertTrue(cmds.objExists(result_grp), "Result group should exist")

    def test_create_negative_space(self):
        if ImageTracer is None:
            self.skipTest("ImageTracer module not found")

        tracer = ImageTracer(self.test_image_path, scale=0.1)
        result_grp = tracer.create_negative_space_mesh(name="negative_mesh")
        self.assertTrue(cmds.objExists(result_grp), "Result group should exist")

    def test_project_on_plane(self):
        if ImageTracer is None:
            self.skipTest("ImageTracer module not found")

        tracer = ImageTracer(self.test_image_path, scale=0.1)
        result_grp = tracer.project_on_plane(name="projected_curves")
        self.assertTrue(cmds.objExists(result_grp), "Result group should exist")

    def test_blue_pencil_tracing(self):
        if ImageTracer is None:
            self.skipTest("ImageTracer module not found")
        if cv2 is None:
            self.skipTest("OpenCV not found")

        # Create a dummy zip file with a png inside
        import zipfile
        import shutil
        from unittest.mock import patch

        # Create a test PNG
        png_path = self.test_image_path
        if not os.path.exists(png_path):
            self.create_test_image()

        # Create a zip file containing the PNG
        zip_source_path = os.path.join(self.scratch, "test_bp_export.zip")
        with zipfile.ZipFile(zip_source_path, "w") as zf:
            zf.write(png_path, "test_stroke.png")

        # Mock cmds.bluePencilFrame (production now uses cmds, not pm)
        module_name = ImageTracer.__module__

        def side_effect(exportArchive=None, **kwargs):
            if exportArchive:
                shutil.copy2(zip_source_path, exportArchive)

        with patch(f"{module_name}.cmds") as mock_cmds:
            mock_cmds.pluginInfo.return_value = True  # Plugin loaded
            mock_cmds.bluePencilFrame.side_effect = side_effect
            mock_cmds.curve.return_value = "curve1"
            mock_cmds.ls.return_value = []  # For existing curves check
            mock_cmds.listRelatives.return_value = ["curve1"]

            tracer = ImageTracer(use_blue_pencil=True)
            curves = tracer.trace_curves()

            self.assertTrue(
                mock_cmds.bluePencilFrame.called, "bluePencilFrame should be called"
            )
            self.assertTrue(len(curves) > 0, "Should return curves")

    def test_open_blue_pencil_opens_the_maya_tool(self):
        """The panel's Open Blue Pencil is the library's: Maya's Blue Pencil tool."""
        if ImageTracer is None:
            self.skipTest("ImageTracer module not found")
        from unittest.mock import patch

        with patch(f"{ImageTracer.__module__}.mel") as mel:
            ImageTracer.open_blue_pencil()
        mel.eval.assert_called_once_with("OpenBluePencil")


if __name__ == "__main__":
    cmds.file(new=True, force=True)
    import mayatk as mtk

    mtk.clear_scrollfield_reporters()
    unittest.main(exit=False)
