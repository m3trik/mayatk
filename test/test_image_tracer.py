import unittest
import os
import sys

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

try:
    import pymel.core as pm
except ImportError:
    pm = None

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

        # Check for user provided image
        user_image = r"C:\Users\m3tri\Desktop\heat_sink_1.png"
        if os.path.exists(user_image):
            self.test_image_path = user_image
            self.using_user_image = True
        else:
            self.using_user_image = False
            try:
                base_dir = os.path.dirname(__file__)
            except NameError:
                import tempfile

                base_dir = tempfile.gettempdir()
            self.test_image_path = os.path.join(base_dir, "test_shape.png")
            self.create_test_image()

    def tearDown(self):
        if not getattr(self, "using_user_image", False) and os.path.exists(
            self.test_image_path
        ):
            try:
                os.remove(self.test_image_path)
            except OSError:
                pass

        # Cleanup scene if not keeping it
        if not getattr(self, "keep_scene", False):
            # We can't easily track what the class created unless we store it
            # But since we are running in a test environment, we might want to rely on the user manually cleaning or newFile
            pass

    def create_test_image(self):
        if cv2 is None or np is None:
            return
        img = np.zeros((512, 512, 3), np.uint8)
        cv2.rectangle(img, (100, 100), (300, 300), (255, 255, 255), -1)
        cv2.circle(img, (400, 400), 50, (255, 255, 255), -1)
        cv2.imwrite(self.test_image_path, img)

    def test_trace_curves(self):
        if ImageTracer is None:
            print("Skipping test: ImageTracer module not found")
            return

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
            # curves[0] is a Transform, we need the shape to check degree
            shape = curves[0].getShape()
            self.assertEqual(shape.degree(), 1, "Curve should be degree 1 (linear)")

        # Cleanup for this specific test if we want to be strict,
        # but we are keeping scene for now.
        if self.keep_scene:
            grp = pm.group(curves, name="test_trace_curves_grp")

    def test_create_mesh(self):
        if ImageTracer is None:
            return

        tracer = ImageTracer(self.test_image_path, scale=0.1)
        result_grp = tracer.create_mesh(name="positive_mesh")
        self.assertTrue(pm.objExists(result_grp), "Result group should exist")

    def test_create_negative_space(self):
        if ImageTracer is None:
            return

        tracer = ImageTracer(self.test_image_path, scale=0.1)
        result_grp = tracer.create_negative_space_mesh(name="negative_mesh")
        self.assertTrue(pm.objExists(result_grp), "Result group should exist")

    def test_project_on_plane(self):
        if ImageTracer is None:
            return

        tracer = ImageTracer(self.test_image_path, scale=0.1)
        result_grp = tracer.project_on_plane(name="projected_curves")
        self.assertTrue(pm.objExists(result_grp), "Result group should exist")

    def test_blue_pencil_tracing(self):
        if ImageTracer is None:
            print("Skipping test: ImageTracer module not found")
            return
        if cv2 is None:
            print("Skipping test: OpenCV not found")
            return

        # Create a dummy zip file with a png inside
        import zipfile
        import tempfile
        import shutil
        from unittest.mock import MagicMock, patch

        # Create a test PNG
        png_path = self.test_image_path
        if not os.path.exists(png_path):
            self.create_test_image()

        # Create a zip file containing the PNG
        temp_dir = tempfile.mkdtemp()
        zip_source_path = os.path.join(temp_dir, "test_bp_export.zip")
        with zipfile.ZipFile(zip_source_path, "w") as zf:
            zf.write(png_path, "test_stroke.png")

        # Mock pm.bluePencilFrame
        module_name = ImageTracer.__module__

        def side_effect(exportArchive=None, **kwargs):
            if exportArchive:
                shutil.copy2(zip_source_path, exportArchive)

        with patch(f"{module_name}.pm") as mock_pm:
            # Setup the mock to behave like pymel.core
            mock_pm.pluginInfo.return_value = True  # Plugin loaded

            # Fail Attempt 1 (Native Command)
            mock_pm.mel.exists.return_value = False

            # Attempt 2 (Export Archive)
            mock_pm.bluePencilFrame.side_effect = side_effect

            # Mock curve creation
            mock_curve = MagicMock()
            mock_curve.getParent.return_value = MagicMock()  # Transform
            mock_pm.curve.return_value = mock_curve
            mock_pm.ls.return_value = []  # For existing curves check

            tracer = ImageTracer(use_blue_pencil=True)
            curves = tracer.trace_curves()

            self.assertTrue(
                mock_pm.bluePencilFrame.called, "bluePencilFrame should be called"
            )
            self.assertTrue(len(curves) > 0, "Should return curves")

        # Cleanup
        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    pm.newFile(f=True)
    import mayatk as mtk

    mtk.clear_scrollfield_reporters()
    unittest.main(exit=False)
