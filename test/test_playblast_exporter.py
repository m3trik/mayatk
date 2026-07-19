# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.anim_utils.playblast_exporter (2026-07 overhaul).

Covers the target registry, frame-range resolution, capture primitives, the
single-capture/multi-encode export planner, and regressions:
- encoded outputs from ranges not starting near frame 0 (ffmpeg start_number)
- 'still' captures exactly one frame (was a full sequence)
- render_with_arnold returns only files the run wrote (was: any stale
  prefix-matching file in the output dir)
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

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

import maya.cmds as cmds

import pythontk as ptk
from mayatk.anim_utils.playblast_exporter import (
    CaptureResult,
    ExportTarget,
    PlayblastExporter,
)

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

FFMPEG_AVAILABLE = ptk.VidUtils.resolve_ffmpeg(required=False) is not None

from base_test import MayaTkTestCase


def _fake_capture(calls=None):
    """A capture_sequence stand-in that writes placeholder frames."""

    def capture(
        directory,
        prefix=None,
        start=None,
        end=None,
        camera=None,
        image_format="png",
        **kwargs,
    ):
        if calls is not None:
            calls.append(image_format)
        os.makedirs(directory, exist_ok=True)
        frames = []
        for f in range(start, end + 1):
            path = os.path.join(directory, f"{prefix}.{f:04d}.{image_format}")
            with open(path, "w") as handle:
                handle.write("frame")
            frames.append(path)
        return CaptureResult(
            directory=directory,
            prefix=prefix,
            image_format=image_format,
            start=start,
            end=end,
            padding=4,
            frames=frames,
            fps=24.0,
        )

    return capture


def _fake_encode(encoded=None):
    """An encode_sequence stand-in that writes the output file."""

    def encode(capture, output_filepath, **kwargs):
        if encoded is not None:
            encoded.append(output_filepath)
        with open(output_filepath, "w") as handle:
            handle.write("video")
        return output_filepath

    return encode


class TestPlayblastExporter(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.tmp = tempfile.mkdtemp(prefix="playblast_test_")
        self.cube = cmds.polyCube(name="testCube")[0]
        cmds.setKeyframe(self.cube, t=1, v=0, at="tx")
        cmds.setKeyframe(self.cube, t=10, v=10, at="tx")
        cmds.playbackOptions(min=1, max=10)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _skip_if_batch_capture_failed(self, exc):
        if cmds.about(batch=True):
            self.skipTest(f"playblast unavailable in batch mode: {exc}")
        raise exc

    # ------------------------------------------------------------------
    # Registry / pure logic
    # ------------------------------------------------------------------
    def test_target_registry_integrity(self):
        targets = PlayblastExporter.available_targets()
        names = [name for name, _ in targets]
        self.assertEqual(len(names), len(set(names)), "duplicate target names")
        for name, spec in PlayblastExporter.TARGETS.items():
            self.assertIsInstance(spec, ExportTarget)
            self.assertEqual(spec.name, name)
            self.assertIn(
                spec.kind, ("encode", "sequence", "still", "native", "arnold")
            )
            self.assertTrue(spec.extension, f"{name} has no extension")
            if spec.kind == "native":
                self.assertIn(spec.native_format, PlayblastExporter.NATIVE_EXTENSIONS)
        # The QuickTime-era 'qt' targets must be gone from the registry.
        self.assertNotIn("mov_animation", PlayblastExporter.TARGETS)

    def test_quality_to_crf_mapping(self):
        self.assertEqual(PlayblastExporter._quality_to_crf(100), 16)
        self.assertEqual(PlayblastExporter._quality_to_crf(0), 40)
        self.assertEqual(PlayblastExporter._quality_to_crf(999), 16)  # clamped
        self.assertTrue(
            PlayblastExporter._quality_to_crf(50) > PlayblastExporter._quality_to_crf(90)
        )

    def test_resolve_frame_range_modes(self):
        cmds.playbackOptions(min=5, max=20, animationStartTime=1, animationEndTime=30)
        cmds.currentTime(7)
        self.assertEqual(PlayblastExporter.resolve_frame_range("playback"), (5, 20))
        self.assertEqual(PlayblastExporter.resolve_frame_range("animation"), (1, 30))
        self.assertEqual(PlayblastExporter.resolve_frame_range("current"), (7, 7))
        self.assertEqual(
            PlayblastExporter.resolve_frame_range("custom", 3, 9), (3, 9)
        )
        # Explicit values override the mode individually.
        self.assertEqual(
            PlayblastExporter.resolve_frame_range("playback", start=8), (8, 20)
        )
        with self.assertRaises(ValueError):
            PlayblastExporter.resolve_frame_range("custom")  # needs both
        with self.assertRaises(ValueError):
            PlayblastExporter.resolve_frame_range("custom", 10, 2)  # inverted
        with self.assertRaises(ValueError):
            PlayblastExporter.resolve_frame_range("bogus")

    def test_scene_name(self):
        self.assertEqual(PlayblastExporter.scene_name(), "playblast")
        cmds.file(rename=os.path.join(self.tmp, "my_shot.ma"))
        self.assertEqual(PlayblastExporter.scene_name(), "my_shot")

    def test_capture_movie_validation(self):
        exporter = PlayblastExporter()
        with self.assertRaises(ValueError):
            exporter.capture_movie(os.path.join(self.tmp, "x.avi"), fmt="qtx")
        with self.assertRaises(ValueError):  # extension/format mismatch
            exporter.capture_movie(os.path.join(self.tmp, "x.mov"), fmt="avi")

    def test_invalid_camera_raises(self):
        exporter = PlayblastExporter()
        with self.assertRaises(ValueError):
            exporter.capture_sequence(
                self.tmp, prefix="x", camera="non_existent_camera_999"
            )

    def test_export_unknown_target_raises(self):
        with self.assertRaises(ValueError):
            PlayblastExporter().export(self.tmp, targets=["mp4", "nope"])

    def test_resolve_sound_node(self):
        # No audio nodes -> nothing to resolve.
        self.assertIsNone(PlayblastExporter.resolve_sound_node())
        first = cmds.createNode("audio", name="trackA")
        self.assertEqual(PlayblastExporter.resolve_sound_node(), first)
        cmds.createNode("audio", name="trackB")
        # Two nodes and no active timeline sound -> ambiguous.
        if cmds.about(batch=True):
            self.assertIsNone(PlayblastExporter.resolve_sound_node())

    # ------------------------------------------------------------------
    # Export planner (mocked primitives — no viewport/ffmpeg needed)
    # ------------------------------------------------------------------
    def test_export_single_capture_feeds_all_encodes(self):
        """mp4 + mov + png_sequence must trigger exactly ONE viewport capture."""
        exporter = PlayblastExporter()
        capture_calls, encoded = [], []
        exporter.capture_sequence = _fake_capture(capture_calls)
        exporter.encode_sequence = _fake_encode(encoded)

        results = exporter.export(
            self.tmp, name="shot", targets=["mp4", "mov", "png_sequence"],
            start=1, end=3,
        )

        self.assertEqual(capture_calls, ["png"], "expected exactly one capture")
        self.assertEqual(len(encoded), 2)
        by_target = {r.target: r for r in results}
        self.assertTrue(all(r.ok for r in results), [r.error for r in results])
        self.assertEqual(len(by_target["png_sequence"].output), 3)
        self.assertTrue(by_target["mp4"].output.endswith("shot.mp4"))
        self.assertTrue(by_target["mov"].output.endswith("shot.mov"))
        # Shared frames live in the png_sequence deliverable dir — kept.
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "shot_png")))

    def test_export_encode_only_cleans_intermediate_frames(self):
        exporter = PlayblastExporter()
        exporter.capture_sequence = _fake_capture()
        exporter.encode_sequence = _fake_encode()

        results = exporter.export(self.tmp, name="clip", targets="mp4", start=1, end=2)
        self.assertTrue(results[0].ok, results[0].error)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "clip_png_tmp")),
            "intermediate frames must be cleaned up",
        )

        results = exporter.export(
            self.tmp, name="clip2", targets="mp4", start=1, end=2, keep_frames=True
        )
        self.assertTrue(results[0].ok)
        kept = os.path.join(self.tmp, "clip2_png_tmp")
        self.assertTrue(os.path.isdir(kept), "keep_frames=True must retain frames")
        self.assertEqual(len(os.listdir(kept)), 2)

    def test_export_capture_failure_isolates_targets(self):
        """A failed shared capture errors its dependents; independent targets
        still run."""
        exporter = PlayblastExporter()

        def broken_capture(*args, **kwargs):
            raise RuntimeError("viewport exploded")

        def fake_still(filepath, **kwargs):
            with open(filepath, "w") as handle:
                handle.write("still")
            return filepath

        exporter.capture_sequence = broken_capture
        exporter.capture_still = fake_still

        results = exporter.export(
            self.tmp, name="mix", targets=["mp4", "png_sequence", "still"],
            start=1, end=2,
        )
        by_target = {r.target: r for r in results}
        self.assertIn("viewport exploded", by_target["mp4"].error)
        self.assertIn("viewport exploded", by_target["png_sequence"].error)
        self.assertTrue(by_target["still"].ok, by_target["still"].error)

    def test_export_progress_reaches_done(self):
        exporter = PlayblastExporter()
        exporter.capture_sequence = _fake_capture()
        exporter.encode_sequence = _fake_encode()
        seen = []
        exporter.export(
            self.tmp, name="prog", targets="mp4", start=1, end=2,
            progress_callback=lambda i, total, text: seen.append((i, total, text)),
        )
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], seen[-1][1], "final tick must be (total, total)")
        self.assertEqual(seen[-1][2], "Done")

    # ------------------------------------------------------------------
    # Capture primitives (real playblast — batch-tolerant)
    # ------------------------------------------------------------------
    def test_capture_sequence_keeps_real_frame_numbers(self):
        cmds.playbackOptions(min=101, max=105)
        exporter = PlayblastExporter(width=320, height=240)
        try:
            capture = exporter.capture_sequence(self.tmp, prefix="rangecheck")
        except RuntimeError as exc:
            self._skip_if_batch_capture_failed(exc)
        self.assertEqual((capture.start, capture.end), (101, 105))
        self.assertEqual(len(capture.frames), 5)
        self.assertTrue(capture.frames[0].endswith("rangecheck.0101.png"))
        self.assertIn("%04d", capture.pattern)

    def test_capture_sequence_clears_stale_frames(self):
        """Regression: frames left by an earlier/wider run passed the frame
        count check and got encoded past the requested end (ffmpeg reads a
        printf pattern contiguously)."""
        cmds.playbackOptions(min=101, max=103)
        for stale in (102, 106):  # one inside, one beyond the range
            with open(
                os.path.join(self.tmp, f"rangecheck.{stale:04d}.png"), "w"
            ) as handle:
                handle.write("stale")
        exporter = PlayblastExporter(width=320, height=240)
        try:
            capture = exporter.capture_sequence(self.tmp, prefix="rangecheck")
        except RuntimeError as exc:
            self._skip_if_batch_capture_failed(exc)
        self.assertEqual(
            sorted(os.listdir(self.tmp)),
            [f"rangecheck.{n:04d}.png" for n in (101, 102, 103)],
            "stale out-of-range frame must be removed",
        )
        with open(capture.frames[1], "rb") as handle:
            self.assertNotEqual(
                handle.read(5), b"stale", "in-range stale frame must be recaptured"
            )

    def test_collect_frames_sorts_numerically(self):
        """Regression: lexicographic sort misordered frames across a padding
        boundary (\"10000\" < \"9999\")."""
        for n in (9999, 10000, 10001):
            with open(os.path.join(self.tmp, f"seq.{n:04d}.png"), "w") as handle:
                handle.write("x")
        frames = PlayblastExporter._collect_frames(self.tmp, "seq", "png", 9999, 10001)
        numbers = [int(os.path.basename(f).split(".")[1]) for f in frames]
        self.assertEqual(numbers, [9999, 10000, 10001])
        # Unbounded collection returns everything, still ordered.
        self.assertEqual(
            len(PlayblastExporter._collect_frames(self.tmp, "seq", "png")), 3
        )

    def test_capture_still_writes_exactly_one_file(self):
        """Regression: the old 'png_still' preset captured the whole range."""
        target = os.path.join(self.tmp, "single.png")
        exporter = PlayblastExporter(width=320, height=240)
        try:
            result = exporter.capture_still(target, frame=3)
        except RuntimeError as exc:
            self._skip_if_batch_capture_failed(exc)
        self.assertEqual(result, ptk.format_path(target))
        written = os.listdir(self.tmp)
        self.assertEqual(
            written, ["single.png"], f"expected one file, found: {written}"
        )

    def test_export_mp4_from_offset_range(self):
        """Regression: encoding a range not starting near 0 used to fail
        (ffmpeg image2 only auto-detects start numbers 0-4)."""
        if not FFMPEG_AVAILABLE:
            self.skipTest("ffmpeg not available")
        cmds.playbackOptions(min=101, max=105)
        exporter = PlayblastExporter(width=320, height=240)
        results = exporter.export(self.tmp, name="offset", targets="mp4")
        result = results[0]
        if not result.ok:
            self._skip_if_batch_capture_failed(RuntimeError(result.error))
        self.assertTrue(os.path.exists(result.output))
        self.assertGreater(os.path.getsize(result.output), 0)
        if CV2_AVAILABLE:
            cap = cv2.VideoCapture(result.output)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            self.assertEqual(frame_count, 5)

    def test_export_mp4_honors_scene_fps(self):
        if not (FFMPEG_AVAILABLE and CV2_AVAILABLE):
            self.skipTest("ffmpeg/cv2 not available")
        cmds.currentUnit(time="game")  # 15 fps
        cmds.playbackOptions(min=1, max=15)
        cube = cmds.polyCube()[0]
        cmds.setKeyframe(cube, t=1, v=0, at="tx")
        cmds.setKeyframe(cube, t=15, v=10, at="tx")
        exporter = PlayblastExporter(width=320, height=240)
        results = exporter.export(self.tmp, name="lowfps", targets="mp4")
        result = results[0]
        if not result.ok:
            self._skip_if_batch_capture_failed(RuntimeError(result.error))
        cap = cv2.VideoCapture(result.output)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        self.assertAlmostEqual(fps, 15.0, delta=0.1)
        duration = frame_count / fps if fps else 0
        self.assertAlmostEqual(duration, 1.0, delta=0.2)

    def test_capture_movie_avi_smoke(self):
        exporter = PlayblastExporter(width=320, height=240)
        target = os.path.join(self.tmp, "smoke.avi")
        try:
            result = exporter.capture_movie(target, start=1, end=5)
        except RuntimeError as exc:
            self._skip_if_batch_capture_failed(exc)
        self.assertTrue(os.path.exists(result))
        self.assertGreater(os.path.getsize(result), 0)

    # ------------------------------------------------------------------
    # Arnold (mocked — no real render)
    # ------------------------------------------------------------------
    def test_render_with_arnold_returns_only_new_files(self):
        """Regression: stale prefix-matching files from earlier renders were
        returned as if this run produced them."""
        out_dir = os.path.join(self.tmp, "arnold")
        os.makedirs(out_dir)
        stale = os.path.join(out_dir, "shot.0099.exr")
        with open(stale, "w") as handle:
            handle.write("old render")
        old_time = os.path.getmtime(stale) - 1000
        os.utime(stale, (old_time, old_time))

        def fake_render(**kwargs):
            for frame in (1, 2):
                with open(os.path.join(out_dir, f"shot.{frame:04d}.exr"), "w") as f:
                    f.write("new render")

        def fake_getattr(attr, *args, **kwargs):
            values = {
                "defaultArnoldDriver.ai_translator": "exr",
                "defaultRenderGlobals.imageFilePrefix": "",
            }
            return values.get(attr, 0)

        exporter = PlayblastExporter()
        pe_path = "mayatk.anim_utils.playblast_exporter.cmds"
        with (
            patch(f"{pe_path}.arnoldRender", create=True, side_effect=fake_render),
            patch("mayatk.env_utils._env_utils.EnvUtils.load_plugin"),
            patch(f"{pe_path}.workspace", return_value="images"),
            patch(f"{pe_path}.setAttr"),
            patch(f"{pe_path}.getAttr", side_effect=fake_getattr),
            patch(
                f"{pe_path}.editRenderLayerGlobals",
                create=True,
                return_value="defaultRenderLayer",
            ),
            patch.object(exporter, "_resolve_camera_shape", return_value="perspShape"),
        ):
            frames = exporter.render_with_arnold(
                out_dir, start=1, end=2, prefix="shot"
            )

        basenames = sorted(os.path.basename(f) for f in frames)
        self.assertEqual(basenames, ["shot.0001.exr", "shot.0002.exr"])

    def test_arnold_extension_translator_map(self):
        pe_path = "mayatk.anim_utils.playblast_exporter.cmds"
        for translator, expected in (
            ("jpeg", "jpg"),
            ("exr", "exr"),
            ("deepexr", "exr"),
            (None, "exr"),
        ):
            with patch(f"{pe_path}.getAttr", return_value=translator):
                self.assertEqual(PlayblastExporter._arnold_extension(), expected)


if __name__ == "__main__":
    unittest.main()
