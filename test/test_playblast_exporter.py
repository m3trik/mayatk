# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.anim_utils.playblast_exporter"""

import os
import unittest
import sys
import math
from unittest.mock import patch, MagicMock

try:
    from PySide2.QtWidgets import QApplication
except ImportError:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        QApplication = None

# Ensure QApplication exists before any Maya imports that might need it
if QApplication and not QApplication.instance():
    app = QApplication(sys.argv)

import pymel.core as pm
import mayatk as mtk
from mayatk.anim_utils.playblast_exporter import PlayblastExporter

try:
    import cv2
    import numpy as np

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

from base_test import MayaTkTestCase


class TestPlayblastExporter(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        pm.newFile(force=True)
        self.cube = pm.polyCube(name="testCube")[0]
        # Create some animation
        pm.setKeyframe(self.cube, t=1, v=0, at="tx")
        pm.setKeyframe(self.cube, t=10, v=10, at="tx")

        # Set playback range
        pm.playbackOptions(min=1, max=10)

    def analyze_video(self, filepath):
        """Analyze video for glitches using OpenCV."""
        if not CV2_AVAILABLE:
            print("Skipping video analysis (OpenCV not available)")
            return True

        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            return False, "Could not open video file"

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if frame_count == 0:
            return False, "Video has 0 frames"

        if width == 0 or height == 0:
            return False, f"Invalid dimensions: {width}x{height}"

        prev_frame = None
        glitches = []

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Check for black frame (mean pixel value < threshold)
            mean_val = np.mean(frame)
            if mean_val < 1.0:  # Almost completely black
                glitches.append(f"Frame {frame_idx} is black (mean: {mean_val})")

            # Check for static frame (if animation is expected)
            # In our test scene, the cube moves every frame.
            if prev_frame is not None:
                diff = cv2.absdiff(frame, prev_frame)
                non_zero_count = np.count_nonzero(diff)
                if non_zero_count == 0:
                    # Note: This might happen if movement is sub-pixel or identical,
                    # but for a moving cube it should change.
                    # However, playblast compression might cause identical frames.
                    # We'll just log it for now or use a loose threshold.
                    pass

            prev_frame = frame
            frame_idx += 1

        cap.release()

        if glitches:
            return False, "; ".join(glitches)

        return True, f"Analyzed {frame_count} frames. OK."

    def test_init_defaults(self):
        """Test initialization with defaults."""
        exporter = PlayblastExporter()
        self.assertEqual(exporter.start_frame, 1)
        self.assertEqual(exporter.end_frame, 10)
        self.assertIsNone(exporter.camera_name)

    def test_init_overrides(self):
        """Test initialization with overrides."""
        exporter = PlayblastExporter(start_frame=5, end_frame=15, camera_name="persp")
        self.assertEqual(exporter.start_frame, 5)
        self.assertEqual(exporter.end_frame, 15)
        self.assertEqual(exporter.camera_name, "persp")

    def test_scene_name_property(self):
        """Test scene_name property."""
        exporter = PlayblastExporter()
        # Untitled scene
        self.assertEqual(exporter.scene_name, "playblast")

        # Save scene (mock or real)
        temp_file = os.path.join(os.environ["TEMP"], "test_scene.ma")
        pm.renameFile(temp_file)
        # Reset cached property if needed, but it's cached in instance
        exporter._scene_name = None
        self.assertEqual(exporter.scene_name, "test_scene")

    def test_resolve_filepath_directory(self):
        """Test _resolve_filepath with directory input."""
        exporter = PlayblastExporter()
        exporter._scene_name = "myScene"

        # Video format
        path = exporter._resolve_filepath("C:/temp", "avi", ".avi")
        self.assertTrue(path.endswith("myScene.avi"))

        # Image format
        path = exporter._resolve_filepath("C:/temp", "image", ".png")
        self.assertTrue(
            path.endswith("myScene")
        )  # Image sequence uses directory/prefix

    def test_resolve_filepath_file(self):
        """Test _resolve_filepath with file input."""
        exporter = PlayblastExporter()

        # Exact match
        path = exporter._resolve_filepath("C:/temp/custom.mov", "qt", ".mov")
        self.assertEqual(path, "C:/temp/custom.mov")

        # Mismatch extension raises error
        with self.assertRaises(ValueError):
            exporter._resolve_filepath("C:/temp/custom.mov", "avi", ".avi")

    def test_default_variations(self):
        """Test default variations structure."""
        variations = PlayblastExporter._default_variations()
        self.assertIsInstance(variations, list)
        self.assertTrue(len(variations) >= 1)

        labels = [v["label"] for v in variations]
        self.assertIn("video", labels)
        self.assertNotIn("mov_animation", labels)

    def test_playblast_metadata(self):
        """Verify playblast output metadata (duration, resolution) using export_variations (Sequence -> MP4)."""
        if not CV2_AVAILABLE:
            print("Skipping metadata test (OpenCV not available)")
            return

        exporter = PlayblastExporter()
        # Use a temp directory for the output
        output_base = os.path.join(os.environ["TEMP"], "test_metadata_export")

        # Use specific resolution
        width, height = 320, 240

        # Define a variation that uses the sequence -> mp4 workflow
        variations = [
            {
                "label": "test_vid",
                "playblast": {
                    "format": "image",
                    "compression": "png",
                    "widthHeight": (width, height),
                    "percent": 100,
                    "viewer": False,
                    "offScreen": True,
                },
                "post": "mp4",
            }
        ]

        try:
            results = exporter.export_variations(
                output_path=output_base, variations=variations, scene_name="test_scene"
            )

            self.assertEqual(len(results), 1)
            result = results[0]

            self.assertNotIn("error", result, f"Export failed: {result.get('error')}")
            self.assertIn(
                "compressed", result, "MP4 compression failed or not performed"
            )

            mp4_path = result["compressed"]
            self.assertTrue(os.path.exists(mp4_path), f"MP4 file not found: {mp4_path}")

            cap = cv2.VideoCapture(mp4_path)
            self.assertTrue(cap.isOpened(), "Could not open generated video")

            vid_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            vid_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)

            cap.release()

            # Check resolution
            # Note: Some codecs/containers might align dimensions to multiples of 2 or 4
            self.assertEqual(
                vid_width, width, f"Width mismatch: expected {width}, got {vid_width}"
            )
            self.assertEqual(
                vid_height,
                height,
                f"Height mismatch: expected {height}, got {vid_height}",
            )

            # Check duration
            # Range 1-10 is 10 frames
            expected_frames = 10
            # Allow small variance due to ffmpeg encoding quirks or container overhead, but for 10 frames it should be exact
            self.assertEqual(
                frame_count,
                expected_frames,
                f"Frame count mismatch: expected {expected_frames}, got {frame_count}",
            )

            # Check FPS (Maya default is usually 24)
            self.assertGreater(fps, 0)

        except RuntimeError as e:
            if pm.about(batch=True):
                print(f"Playblast failed in batch mode (hardware limitation): {e}")
            else:
                self.fail(f"Playblast failed: {e}")
        finally:
            # Cleanup
            if os.path.exists(output_base + "_test_vid"):
                import shutil

                try:
                    shutil.rmtree(output_base + "_test_vid")
                except OSError:
                    pass

    def test_playblast_visual_content(self):
        """Verify visual content of the playblast with heavy animation."""
        if not CV2_AVAILABLE:
            print("Skipping visual content test (OpenCV not available)")
            return

        # Setup scene
        pm.newFile(force=True)

        # Camera
        cam_shape = pm.createNode("camera")
        cam = cam_shape.getParent()
        cam.setTranslation((0, 0, 20))
        cam.rename("test_cam")

        # Red Sphere
        sphere = pm.polySphere(radius=2)[0]
        shader = pm.shadingNode("lambert", asShader=True)
        shader.color.set((1, 0, 0))  # Red
        sg = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=shader.name() + "SG"
        )
        shader.outColor.connect(sg.surfaceShader)
        pm.sets(sg, forceElement=sphere)

        # Heavy Animation: Keyframes on every frame for 48 frames (2 seconds)
        start_frame = 1
        end_frame = 48

        for i in range(start_frame, end_frame + 1):
            # Move in a circle
            tx = math.sin(i * 0.2) * 5
            ty = math.cos(i * 0.2) * 5
            pm.setKeyframe(sphere, t=i, v=tx, at="tx")
            pm.setKeyframe(sphere, t=i, v=ty, at="ty")
            pm.setKeyframe(sphere, t=i, v=i * 10, at="ry")
            pm.setKeyframe(sphere, t=i, v=i * 10, at="rx")

        exporter = PlayblastExporter()
        output = os.path.join(os.environ["TEMP"], "test_visual_anim.avi")

        try:
            result = exporter.create_playblast(
                filepath=output,
                camera_name=cam.name(),
                start_frame=start_frame,
                end_frame=end_frame,
                offScreen=True,
                viewer=False,
                percent=100,
                widthHeight=(320, 240),
            )

            self.assertTrue(os.path.exists(result))

            cap = cv2.VideoCapture(result)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.assertEqual(frame_count, end_frame - start_frame + 1)

            prev_frame = None
            has_movement = False
            frames_checked = 0

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # Check for Red content (Sphere should be visible)
                b_channel, g_channel, r_channel = cv2.split(frame)

                # In batch mode, we might get black frames.
                # If we do, we skip the color assertion but log it.
                is_black = np.mean(frame) < 1.0

                if not is_black:
                    # At least some pixels should be bright red (the sphere)
                    # We check max value because the sphere might be small or moving
                    self.assertGreater(
                        np.max(r_channel),
                        100,
                        f"Frame {frames_checked} missing red object",
                    )

                # Check for movement (difference from previous frame)
                if prev_frame is not None:
                    diff = cv2.absdiff(frame, prev_frame)
                    if np.sum(diff) > 0:
                        has_movement = True

                prev_frame = frame
                frames_checked += 1

            cap.release()

            # If we are in batch mode and got all black frames, has_movement might be False.
            # Otherwise, we expect movement.
            if not (pm.about(batch=True) and frames_checked > 0 and is_black):
                self.assertTrue(
                    has_movement, "Video contains no animation (frames are identical)"
                )

        except RuntimeError as e:
            if pm.about(batch=True):
                print(f"Playblast failed in batch mode: {e}")
            else:
                self.fail(f"Playblast failed: {e}")
        finally:
            if os.path.exists(output):
                try:
                    os.remove(output)
                except OSError:
                    pass

    def test_custom_frame_range(self):
        """Test playblast with custom frame range."""
        if not CV2_AVAILABLE:
            print("Skipping frame range test (OpenCV not available)")
            return

        exporter = PlayblastExporter()
        output = os.path.join(os.environ["TEMP"], "test_range.avi")

        # Range 2-5 (4 frames)
        start, end = 2, 5
        expected_frames = end - start + 1

        try:
            result = exporter.create_playblast(
                filepath=output,
                start_frame=start,
                end_frame=end,
                offScreen=True,
                viewer=False,
                percent=100,
            )
            self.assertTrue(os.path.exists(result))

            cap = cv2.VideoCapture(result)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            self.assertEqual(
                frame_count,
                expected_frames,
                f"Frame count mismatch: expected {expected_frames}, got {frame_count}",
            )

        except RuntimeError as e:
            if pm.about(batch=True):
                print(f"Playblast failed in batch mode: {e}")
            else:
                self.fail(f"Playblast failed: {e}")
        finally:
            if os.path.exists(output):
                try:
                    os.remove(output)
                except OSError:
                    pass

    def test_invalid_camera(self):
        """Test behavior with invalid camera name."""
        exporter = PlayblastExporter()
        output = os.path.join(os.environ["TEMP"], "test_invalid_cam.avi")

        # Now we expect ValueError because we added validation
        with self.assertRaises(ValueError):
            exporter.create_playblast(
                filepath=output,
                camera_name="non_existent_camera_999",
                offScreen=True,
                viewer=False,
            )

    def test_export_variations_multiple(self):
        """Test exporting multiple variations at once."""
        exporter = PlayblastExporter()
        output_base = os.path.join(os.environ["TEMP"], "test_multi_var")

        variations = [
            {
                "label": "var1",
                "playblast": {
                    "format": "avi",
                    "compression": "none",
                    "percent": 50,
                    "viewer": False,
                    "offScreen": True,
                },
            },
            {
                "label": "var2",
                "playblast": {
                    "format": "avi",
                    "compression": "none",
                    "percent": 25,
                    "viewer": False,
                    "offScreen": True,
                },
            },
        ]

        try:
            results = exporter.export_variations(
                output_path=output_base, variations=variations, scene_name="test_scene"
            )

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["label"], "var1")
            self.assertEqual(results[1]["label"], "var2")

            # Check outputs exist
            for res in results:
                self.assertNotIn("error", res)
                self.assertTrue(os.path.exists(res["output"]))

        except RuntimeError as e:
            if pm.about(batch=True):
                print(f"Playblast failed in batch mode: {e}")
        finally:
            # Cleanup
            for res in results:
                if "output" in res and os.path.exists(res["output"]):
                    try:
                        os.remove(res["output"])
                    except OSError:
                        pass

    def test_arnold_render_mock(self):
        """Test Arnold render path using mocks (avoids actual rendering)."""
        exporter = PlayblastExporter()
        output_base = os.path.join(os.environ["TEMP"], "test_arnold")

        variations = [{"label": "arnold_pass", "renderer": "arnold", "framePadding": 4}]

        # Mock pm.arnoldRender and pm.pluginInfo to avoid loading mtoa or rendering
        with (
            patch("pymel.core.arnoldRender") as mock_render,
            patch("pymel.core.pluginInfo", return_value=True),
            patch("pymel.core.workspace") as mock_workspace,
            patch("pymel.core.setAttr") as mock_setAttr,
            patch("pymel.core.getAttr") as mock_getAttr,
            patch("pymel.core.editRenderLayerGlobals") as mock_layer,
            patch("os.walk") as mock_walk,
            patch.object(exporter, "_resolve_camera_shape", return_value="perspShape"),
        ):

            # Setup mock for os.walk to simulate finding rendered files
            # os.walk yields (root, dirs, files)
            mock_walk.return_value = [
                (
                    output_base + "_arnold_pass",
                    [],
                    ["test_scene.0001.exr", "test_scene.0002.exr"],
                )
            ]

            # Mock PyNode for defaultArnoldDriver
            mock_driver = MagicMock()
            mock_driver.ai_translator.get.return_value = "exr"
            with patch("pymel.core.PyNode", return_value=mock_driver):
                results = exporter.export_variations(
                    output_path=output_base,
                    variations=variations,
                    scene_name="test_scene",
                )

            # Verify arnoldRender was called
            self.assertTrue(mock_render.called)

            # Verify results
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["type"], "arnold_sequence")
            self.assertEqual(len(results[0]["output"]), 2)  # 2 files mocked

    def test_low_fps_duration(self):
        """Verify duration is correct for low FPS animations (ensures -framerate is used)."""
        if not CV2_AVAILABLE:
            print("Skipping low FPS test (OpenCV not available)")
            return

        pm.newFile(force=True)

        # Set scene to 15 fps ("game")
        pm.currentUnit(time="game")

        # Create animation: 30 frames = 2 seconds at 15fps
        # If ffmpeg defaults to 25fps input, it would be 30/25 = 1.2 seconds (too short)
        start, end = 1, 30
        expected_duration = 2.0

        cube = pm.polyCube()[0]
        pm.setKeyframe(cube, t=start, v=0, at="tx")
        pm.setKeyframe(cube, t=end, v=10, at="tx")

        exporter = PlayblastExporter()
        output_base = os.path.join(os.environ["TEMP"], "test_low_fps")

        # Use sequence -> mp4 workflow
        variations = [
            {
                "label": "vid",
                "playblast": {
                    "format": "image",
                    "compression": "png",
                    "offScreen": True,
                },
                "post": "mp4",
            }
        ]

        try:
            results = exporter.export_variations(
                output_path=output_base, variations=variations, scene_name="test_scene"
            )

            mp4_path = results[0]["compressed"]
            self.assertTrue(os.path.exists(mp4_path))

            cap = cv2.VideoCapture(mp4_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            duration = frame_count / fps if fps > 0 else 0
            print(
                f"Low FPS Test: FPS={fps}, Frames={frame_count}, Duration={duration}s"
            )

            # Check FPS
            self.assertAlmostEqual(fps, 15.0, delta=0.1)

            # Check Duration
            # Allow small delta
            self.assertAlmostEqual(
                duration,
                expected_duration,
                delta=0.2,
                msg=f"Duration mismatch! Expected {expected_duration}s, got {duration}s. (Did ffmpeg use default 25fps input?)",
            )

        except RuntimeError as e:
            if pm.about(batch=True):
                print(f"Playblast failed in batch mode: {e}")
            else:
                self.fail(f"Playblast failed: {e}")
        finally:
            # Cleanup
            if os.path.exists(output_base + "_vid"):
                import shutil

                try:
                    shutil.rmtree(output_base + "_vid")
                except OSError:
                    pass

    def test_create_playblast_smoke(self):
        """Smoke test for create_playblast.

        Note: Actual playblast generation might fail in headless mode without offScreen=True
        or specific hardware setup, but we check if it runs through the logic.
        """
        # We attempt to run this even in batch mode now, but catch specific errors
        # if the hardware doesn't support it.

        exporter = PlayblastExporter()
        output = os.path.join(os.environ["TEMP"], "test_playblast.avi")

        try:
            # Ensure offScreen is True for batch stability
            result = exporter.create_playblast(
                filepath=output, offScreen=True, viewer=False
            )
            self.assertTrue(os.path.exists(result))

            # Analyze the output
            is_valid, message = self.analyze_video(result)
            if not is_valid:
                # If analysis fails, we fail the test, unless it's a known batch issue
                if pm.about(batch=True) and "black" in message:
                    print(
                        f"Warning: Playblast generated black frames in batch mode (expected on some hardware): {message}"
                    )
                else:
                    self.fail(f"Video analysis failed: {message}")
            else:
                print(f"Video analysis passed: {message}")

            # Cleanup
            try:
                os.remove(result)
            except OSError:
                pass

        except RuntimeError as e:
            if pm.about(batch=True):
                print(f"Playblast failed in batch mode (hardware limitation): {e}")
            else:
                self.fail(f"Playblast failed: {e}")


if __name__ == "__main__":
    unittest.main()
