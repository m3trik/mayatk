# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.anim_utils.playblast_exporter (2026-07 overhaul).

``PlayblastExporter`` is ``ptk.SequenceExporter`` bound to Maya's host adapter
(``MayaPlayblastHost``). Covers the target registry, frame-range resolution,
capture primitives, the single-capture/multi-encode export planner, what the
exporter hands ``cmds.playblast`` and ffmpeg (recorded, so it runs headless),
and regressions:
- encoded outputs from ranges not starting near frame 0 (ffmpeg start_number)
- 'still' captures exactly one frame (was a full sequence)
- render_with_arnold returns only files the run wrote (was: any stale
  prefix-matching file in the output dir)
- an encode-only export whose capture raised part-way left its frames
  behind (cleanup keyed on a CaptureResult that never existed)
- an encode target with no ffmpeg captured every frame before failing
- odd capture dimensions (a scaled playblast) made ffmpeg write 0 bytes
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
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
import mayatk.anim_utils.playblast_exporter as playblast_module
from mayatk.anim_utils.playblast_exporter import (
    CaptureResult,
    ExportTarget,
    MayaPlayblastHost,
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


class _RecordedPlayblast:
    """``cmds.playblast`` replaced by a recorder that writes the files Maya
    would (frames ``<filename>.<frame>.<ext>``, a still at its exact path, a
    movie at its filename), ffmpeg's call recorded, and Maya's message and
    warning lines captured -- so what the exporter hands each is pinned
    headless.

    ``editorPanelName`` is left out of :attr:`calls`: a GUI session has a
    panel to name, batch has none.
    """

    def __init__(self, offered=("avi", "image", "qt")):
        self.offered = list(offered)
        self.calls, self.compress, self.notes, self.warnings = [], [], [], []
        self._patches = [
            patch.object(
                playblast_module.cmds, "playblast", side_effect=self._playblast
            ),
            patch.object(ptk.VidUtils, "compress_video", side_effect=self._compress),
            patch.object(ptk.VidUtils, "resolve_ffmpeg", return_value="ffmpeg"),
            patch.object(ptk.SequenceEncoder, "_audio_peak_db", return_value=None),
            patch.object(
                playblast_module,
                "om",
                SimpleNamespace(MGlobal=SimpleNamespace(displayInfo=self.notes.append)),
            ),
            patch.object(
                playblast_module.cmds, "warning", side_effect=self.warnings.append
            ),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()

    def _playblast(self, *args, **kwargs):
        if kwargs.get("query"):
            return self.offered if kwargs.get("format") else None
        kwargs.pop("editorPanelName", None)
        self.calls.append(dict(kwargs))
        if kwargs.get("format") == "image":
            if "completeFilename" in kwargs:
                with open(kwargs["completeFilename"], "wb") as handle:
                    handle.write(b"still")
                return kwargs["completeFilename"]
            for frame in range(int(kwargs["startTime"]), int(kwargs["endTime"]) + 1):
                name = f"{kwargs['filename']}.{frame:0{kwargs['framePadding']}d}"
                with open(f"{name}.{kwargs['compression']}", "wb") as handle:
                    handle.write(b"frame")
            return kwargs["filename"]
        with open(kwargs["filename"], "wb") as handle:
            handle.write(b"movie")
        return kwargs["filename"]

    def _compress(self, **kwargs):
        self.compress.append(dict(kwargs))
        with open(kwargs["output_filepath"], "wb") as handle:
            handle.write(b"video")
        return kwargs["output_filepath"]


#: The flags every capture hands ``cmds.playblast`` from the exporter's defaults.
_DEFAULT_FLAGS = {
    "forceOverwrite": True,
    "viewer": False,
    "offScreen": True,
    "showOrnaments": True,
    "percent": 100,
    "quality": 100,
    "widthHeight": (1920, 1080),
}


def _write_png(path, width, height, gray):
    """Write a solid 8-bit grayscale PNG without PIL (mayapy ships none)."""
    import struct
    import zlib

    def chunk(tag, data):
        body = tag + data
        crc = zlib.crc32(body) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + body + struct.pack(">I", crc)

    # No escape sequences on purpose (patch tooling collapses them).
    signature = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    no_filter = bytes([0])
    raw = b"".join(no_filter + bytes([gray]) * width for _ in range(height))
    with open(path, "wb") as handle:
        handle.write(signature)
        handle.write(
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        )
        handle.write(chunk(b"IDAT", zlib.compress(raw)))
        handle.write(chunk(b"IEND", b""))


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
            PlayblastExporter._quality_to_crf(50)
            > PlayblastExporter._quality_to_crf(90)
        )

    def test_resolve_frame_range_modes(self):
        cmds.playbackOptions(min=5, max=20, animationStartTime=1, animationEndTime=30)
        cmds.currentTime(7)
        self.assertEqual(PlayblastExporter.resolve_frame_range("playback"), (5, 20))
        self.assertEqual(PlayblastExporter.resolve_frame_range("animation"), (1, 30))
        self.assertEqual(PlayblastExporter.resolve_frame_range("current"), (7, 7))
        self.assertEqual(PlayblastExporter.resolve_frame_range("custom", 3, 9), (3, 9))
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

    def test_a_format_this_maya_lacks_is_refused_before_capturing(self):
        """AVI playblast is Windows-only: Linux Maya's ``playblast`` offers no
        ``avi``, and the AVI target failed inside the capture instead of
        saying so."""
        from unittest import mock

        import mayatk.anim_utils.playblast_exporter as module

        real = module.cmds.playblast

        def linux_formats(*args, **kwargs):
            if kwargs.get("query") and kwargs.get("format"):
                return ["image", "qt"]
            return real(*args, **kwargs)

        with mock.patch.object(module.cmds, "playblast", side_effect=linux_formats):
            with self.assertRaisesRegex(RuntimeError, "cannot playblast 'avi'"):
                PlayblastExporter().capture_movie(
                    os.path.join(self.tmp, "x.avi"), fmt="avi"
                )

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
            self.tmp,
            name="shot",
            targets=["mp4", "mov", "png_sequence"],
            start=1,
            end=3,
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
            self.tmp,
            name="mix",
            targets=["mp4", "png_sequence", "still"],
            start=1,
            end=2,
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
            self.tmp,
            name="prog",
            targets="mp4",
            start=1,
            end=2,
            progress_callback=lambda i, total, text: seen.append((i, total, text)),
        )
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], seen[-1][1], "final tick must be (total, total)")
        self.assertEqual(seen[-1][2], "Done")

    def test_export_cleans_frames_of_a_capture_that_raised(self):
        """Regression: a playblast interrupted part-way (Esc) raised out of
        capture_sequence with its frames on disk, and the cleanup keyed on a
        CaptureResult that never existed -- an mp4 request ended as a folder
        of PNGs beside an error."""
        exporter = PlayblastExporter()
        write = _fake_capture()

        def interrupted(directory, prefix=None, start=None, end=None, **kwargs):
            write(directory, prefix=prefix, start=start, end=start + 1, **kwargs)
            raise RuntimeError(f"Playblast wrote 2/{end - start + 1} frames")

        exporter.capture_sequence = interrupted
        exporter.encode_sequence = _fake_encode()
        results = exporter.export(self.tmp, name="cut", targets="mp4", start=1, end=10)
        self.assertIn("2/10", results[0].error)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "cut_png_tmp")),
            "partial frames must not survive a failed encode-only export",
        )

    def test_export_without_ffmpeg_fails_before_capturing(self):
        """An encode target must not spend minutes on a viewport capture that
        nothing can consume: a missing ffmpeg errors the target up front and
        the shared PNG capture is dropped from the plan."""
        exporter = PlayblastExporter()
        captured = []
        exporter.capture_sequence = _fake_capture(captured)
        exporter.encode_sequence = _fake_encode()
        with patch.object(
            ptk.VidUtils,
            "resolve_ffmpeg",
            side_effect=FileNotFoundError("FFmpeg is required but was not found"),
        ):
            results = exporter.export(
                self.tmp, name="noff", targets=["mp4", "jpg_sequence"], start=1, end=3
            )
        by_target = {r.target: r for r in results}
        self.assertIn("FFmpeg", by_target["mp4"].error)
        self.assertTrue(by_target["jpg_sequence"].ok, by_target["jpg_sequence"].error)
        self.assertEqual(
            captured, ["jpg"], "no PNG capture for an encode that can't run"
        )
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "noff_png_tmp")))

    def test_encode_sequence_rounds_odd_dimensions_to_even(self):
        """Regression: H.264/yuv420p rejects odd sizes; a 979x551 capture (a
        51% scaled 1080p playblast) made ffmpeg write a 0-byte file."""
        if not FFMPEG_AVAILABLE:
            self.skipTest("ffmpeg not available")
        for f in (1, 2, 3):
            _write_png(os.path.join(self.tmp, f"odd.{f:04d}.png"), 979, 551, f * 40)
        capture = CaptureResult(
            directory=self.tmp,
            prefix="odd",
            image_format="png",
            start=1,
            end=3,
            padding=4,
            frames=[],
            fps=24.0,
        )
        out = PlayblastExporter().encode_sequence(
            capture, os.path.join(self.tmp, "odd.mp4")
        )
        self.assertGreater(os.path.getsize(out), 0)

    def test_export_honours_an_ambient_cancel(self):
        """A cancelled CancelScope (the Cancelable slot machinery) stops the
        plan at its next step and the intermediate frames are still cleaned."""
        exporter = PlayblastExporter()
        exporter.capture_sequence = _fake_capture()
        exporter.encode_sequence = _fake_encode()
        scope = ptk.CancelScope("test")
        with scope:
            scope.cancel("user")
            with self.assertRaises(ptk.OperationCancelled):
                exporter.export(self.tmp, name="stop", targets="mp4", start=1, end=2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "stop_png_tmp")))

    # ------------------------------------------------------------------
    # What the exporter produces (cmds.playblast and ffmpeg recorded)
    # ------------------------------------------------------------------
    def _files(self):
        return sorted(
            os.path.relpath(os.path.join(root, name), self.tmp).replace(os.sep, "/")
            for root, _dirs, names in os.walk(self.tmp)
            for name in names
        )

    def test_export_layout_and_capture_flags_are_pinned(self):
        """One image playblast of the range into ``<name>_png/`` (requested, so
        kept) feeds both movies, ``<dir>/<name>.<ext>``: the exporter's
        settings reach ``cmds.playblast``, the scene's rate and the capture's
        first frame reach ffmpeg, and each movie is announced on Maya's
        message line."""
        exporter = PlayblastExporter(
            camera="persp",
            width=320,
            height=240,
            percent=50,
            quality=80,
            off_screen=False,
            show_ornaments=False,
        )
        with _RecordedPlayblast() as recorded:
            results = exporter.export(
                self.tmp,
                name="shot",
                targets=["mp4", "mov", "png_sequence"],
                range_mode="custom",
                start=3,
                end=5,
            )

        self.assertTrue(all(r.ok for r in results), [r.error for r in results])
        self.assertEqual(
            self._files(),
            [
                "shot.mov",
                "shot.mp4",
                "shot_png/shot.0003.png",
                "shot_png/shot.0004.png",
                "shot_png/shot.0005.png",
            ],
        )
        png_dir = ptk.format_path(os.path.join(self.tmp, "shot_png"))
        self.assertEqual(
            recorded.calls,
            [
                {
                    **_DEFAULT_FLAGS,
                    "offScreen": False,
                    "showOrnaments": False,
                    "percent": 50,
                    "quality": 80,
                    "widthHeight": (320, 240),
                    "clearCache": True,
                    "format": "image",
                    "compression": "png",
                    "framePadding": 4,
                    "filename": os.path.join(png_dir, "shot"),
                    "startTime": 3,
                    "endTime": 5,
                }
            ],
        )
        self.assertEqual(
            [
                {k: v for k, v in call.items() if k != "output_filepath"}
                for call in recorded.compress
            ],
            [
                {
                    "input_filepath": f"{png_dir}/shot.%04d.png",
                    "frame_rate": 24.0,
                    "start_number": 3,
                    "audio_filepath": None,
                    "audio_offset": 0.0,
                    "crf": 21,
                    "vf": "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "movflags": "+faststart",
                }
            ]
            * 2,
        )
        self.assertEqual(
            recorded.notes,
            [f"Encoded movie created: {r.output}" for r in results[:2]],
        )

    def test_export_muxes_the_armed_sound_at_its_rebased_offset(self):
        """The scene's sole sound, armed at frame 10, lands 5 frames into a
        movie captured from frame 5 (24 fps): the encode muxes its file there,
        and the native AVI is handed the node itself."""
        cmds.playbackOptions(min=5, max=8)
        wav = os.path.join(self.tmp, "track.wav").replace(os.sep, "/")
        self._write_wav(wav, 0.5)
        node = cmds.createNode("audio", name="track")
        cmds.setAttr(f"{node}.filename", wav, type="string")
        cmds.setAttr(f"{node}.offset", 10)

        with _RecordedPlayblast() as recorded:
            results = PlayblastExporter(include_audio=True).export(
                self.tmp, name="aud", targets=["mp4", "avi"]
            )

        self.assertTrue(all(r.ok for r in results), [r.error for r in results])
        self.assertEqual(
            os.path.normcase(recorded.compress[0]["audio_filepath"]),
            os.path.normcase(wav),
        )
        self.assertAlmostEqual(recorded.compress[0]["audio_offset"], 5 / 24.0)
        movie = recorded.calls[-1]
        self.assertEqual(
            (movie["format"], movie["compression"], movie["sound"]),
            ("avi", "none", node),
        )
        self.assertEqual(
            movie["filename"], ptk.format_path(os.path.join(self.tmp, "aud.avi"))
        )

    def test_without_include_audio_nothing_is_muxed(self):
        cmds.createNode("audio", name="track")
        with _RecordedPlayblast() as recorded:
            PlayblastExporter().export(self.tmp, name="mute", targets=["mp4", "avi"])
        self.assertIsNone(recorded.compress[0]["audio_filepath"])
        self.assertNotIn("sound", recorded.calls[-1])

    def test_a_missing_audio_file_is_a_warning_and_no_audio(self):
        node = cmds.createNode("audio", name="gone")
        cmds.setAttr(
            f"{node}.filename", os.path.join(self.tmp, "gone.wav"), type="string"
        )
        with _RecordedPlayblast() as recorded:
            PlayblastExporter(include_audio=True).export(
                self.tmp, name="gone", targets="mp4"
            )
        self.assertIsNone(recorded.compress[0]["audio_filepath"])
        self.assertTrue(
            any("not found" in w for w in recorded.warnings), recorded.warnings
        )

    def test_capture_still_hands_playblast_one_frame_and_the_exact_file(self):
        cmds.currentTime(6)
        target = os.path.join(self.tmp, "st", "one.png")
        with _RecordedPlayblast() as recorded:
            result = PlayblastExporter().capture_still(target)

        self.assertEqual(result, ptk.format_path(target))
        self.assertEqual(
            recorded.calls,
            [
                {
                    **_DEFAULT_FLAGS,
                    "format": "image",
                    "compression": "png",
                    "frame": [6],
                    "completeFilename": result,
                }
            ],
        )
        self.assertEqual(recorded.notes, [f"Still frame captured: {result}"])

    def test_capture_movie_appends_the_formats_extension(self):
        with _RecordedPlayblast() as recorded:
            result = PlayblastExporter().capture_movie(
                os.path.join(self.tmp, "mv", "movie"), start=2, end=4
            )

        self.assertEqual(
            result, ptk.format_path(os.path.join(self.tmp, "mv", "movie.avi"))
        )
        self.assertEqual(
            recorded.calls,
            [
                {
                    **_DEFAULT_FLAGS,
                    "clearCache": True,
                    "format": "avi",
                    "compression": "none",
                    "filename": result,
                    "startTime": 2,
                    "endTime": 4,
                }
            ],
        )
        self.assertEqual(recorded.notes, [f"Playblast movie created: {result}"])

    def test_export_overrides_reach_playblast_and_reserved_keys_do_not(self):
        """``cmds.playblast`` overrides pass through; the plan's own keys and
        the Maya targets' are never handed twice; ``frame_padding`` sets the
        frames' digits and reaches no other playblast (the movie's refused an
        unknown flag)."""
        cmds.currentTime(4)
        with _RecordedPlayblast() as recorded:
            results = PlayblastExporter().export(
                self.tmp,
                name="cur",
                targets=["png_sequence", "still", "avi"],
                range_mode="current",
                viewer=True,
                filename="x",
                sound="s",
                fmt="f",
                compression="c",
                frame_padding=5,
            )

        self.assertTrue(all(r.ok for r in results), [r.error for r in results])
        sequence, still, movie = recorded.calls
        self.assertEqual(
            (
                sequence["viewer"],
                sequence["framePadding"],
                sequence["compression"],
                sequence["filename"],
            ),
            (
                True,
                5,
                "png",
                os.path.join(ptk.format_path(os.path.join(self.tmp, "cur_png")), "cur"),
            ),
        )
        self.assertNotIn("sound", sequence)
        self.assertEqual((still["frame"], still["viewer"]), ([4], True))
        self.assertNotIn("frame_padding", still)
        self.assertEqual((movie["format"], movie["viewer"]), ("avi", True))
        self.assertNotIn("frame_padding", movie)
        self.assertNotIn("sound", movie)
        self.assertEqual(
            self._files(), ["cur.avi", "cur.png", "cur_png/cur.00004.png"]
        )

    def test_mayas_frame_padding_spelling_names_the_shared_one(self):
        """``framePadding`` (cmds.playblast's spelling) once set a sequence's
        digits; the exporter's is ``frame_padding``, shared with Blender. The
        old spelling is refused by name, never silently ignored."""
        with _RecordedPlayblast() as recorded:
            with self.assertRaisesRegex(ValueError, "frame_padding"):
                PlayblastExporter().capture_sequence(
                    os.path.join(self.tmp, "seq"), prefix="p", start=1, end=2,
                    framePadding=6,
                )
        self.assertEqual(recorded.calls, [])

    def test_the_scene_answers_name_and_rate_class_level(self):
        """A panel reads both before it builds an exporter."""
        cmds.currentUnit(time="ntsc")
        self.assertEqual(PlayblastExporter.scene_fps(), 30.0)
        self.assertEqual(PlayblastExporter().sequence_name(), "playblast")
        self.assertIsInstance(PlayblastExporter.HOST, MayaPlayblastHost)

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

    def _write_wav(self, path, amplitude, seconds=1.0, rate=8000):
        """Write a mono 16-bit sine WAV; amplitude 0.0 writes digital silence."""
        import math
        import struct
        import wave

        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(
                b"".join(
                    struct.pack("<h", int(amplitude * 32767 * math.sin(i * 0.3)))
                    for i in range(int(rate * seconds))
                )
            )

    def test_audio_peak_db_discriminates_silence(self):
        if not FFMPEG_AVAILABLE:
            self.skipTest("ffmpeg not available")
        silent = os.path.join(self.tmp, "silent.wav")
        loud = os.path.join(self.tmp, "loud.wav")
        self._write_wav(silent, 0.0)
        self._write_wav(loud, 0.8)
        silent_peak = PlayblastExporter._audio_peak_db(silent)
        loud_peak = PlayblastExporter._audio_peak_db(loud)
        self.assertIsNotNone(silent_peak)
        self.assertIsNotNone(loud_peak)
        self.assertLess(silent_peak, PlayblastExporter._SILENT_PEAK_DB)
        self.assertGreater(loud_peak, PlayblastExporter._SILENT_PEAK_DB)
        # Unmeasurable input (no audio stream) -> None, never an exception.
        self.assertIsNone(
            PlayblastExporter._audio_peak_db(os.path.join(self.tmp, "missing.wav"))
        )

    def test_encode_sequence_warns_on_silent_audio(self):
        """A muxed-but-silent track must be reported, not passed silently."""
        wav = os.path.join(self.tmp, "mux.wav")
        self._write_wav(wav, 0.0)
        capture = CaptureResult(
            directory=self.tmp,
            prefix="s",
            image_format="png",
            start=1,
            end=5,
            padding=4,
            frames=[],
            fps=24.0,
        )

        def fake_compress(**kwargs):
            with open(kwargs["output_filepath"], "w") as handle:
                handle.write("video")
            return kwargs["output_filepath"]

        exporter = PlayblastExporter()
        with (
            patch.object(ptk.VidUtils, "compress_video", side_effect=fake_compress),
            patch.object(PlayblastExporter, "_audio_peak_db", return_value=-91.0),
        ):
            with self.assertLogs(exporter.logger, level="WARNING") as logs:
                exporter.encode_sequence(
                    capture, os.path.join(self.tmp, "s.mp4"), audio=wav
                )
        self.assertTrue(
            any("silent" in message for message in logs.output), logs.output
        )

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
            patch("mayatk.core_utils.plugins._plugins.Plugins.load"),
            patch(
                "mayatk.anim_utils.playblast_exporter.RenderUtils.prepare_arnold_preview"
            ),
            patch(f"{pe_path}.workspace", return_value="images"),
            patch(f"{pe_path}.setAttr"),
            patch(f"{pe_path}.getAttr", side_effect=fake_getattr),
            patch(
                f"{pe_path}.editRenderLayerGlobals",
                create=True,
                return_value="defaultRenderLayer",
            ),
            patch.object(
                MayaPlayblastHost, "_resolve_camera_shape", return_value="perspShape"
            ),
        ):
            frames = exporter.render_with_arnold(out_dir, start=1, end=2, prefix="shot")

        basenames = sorted(os.path.basename(f) for f in frames)
        self.assertEqual(basenames, ["shot.0001.exr", "shot.0002.exr"])

    def test_render_with_arnold_prepares_the_scene_first(self):
        """An Arnold sequence renders what the viewport shows: game shaders
        bridged and viewport culling mirrored BEFORE the first frame (was:
        StingrayPBS rendered error magenta, cutaway walls black)."""
        out_dir = os.path.join(self.tmp, "arnold_prep")
        order = []
        exporter = PlayblastExporter()
        pe_path = "mayatk.anim_utils.playblast_exporter"
        with (
            patch(
                f"{pe_path}.RenderUtils.prepare_arnold_preview",
                side_effect=lambda: order.append("prepare"),
            ),
            patch(
                f"{pe_path}.cmds.arnoldRender",
                create=True,
                side_effect=lambda **kw: order.append("render"),
            ),
            patch("mayatk.core_utils.plugins._plugins.Plugins.load"),
            patch(f"{pe_path}.cmds.workspace", return_value="images"),
            patch(f"{pe_path}.cmds.setAttr"),
            patch(
                f"{pe_path}.cmds.getAttr",
                side_effect=lambda attr, *a, **k: (
                    "exr" if attr.endswith("ai_translator") else 0
                ),
            ),
            patch(
                f"{pe_path}.cmds.editRenderLayerGlobals",
                create=True,
                return_value="defaultRenderLayer",
            ),
            patch.object(
                MayaPlayblastHost, "_resolve_camera_shape", return_value="perspShape"
            ),
        ):
            exporter.render_with_arnold(out_dir, start=1, end=1, prefix="shot")
        self.assertEqual(order, ["prepare", "render"])

    def test_import_guard_nulls_names_without_maya(self):
        """The ImportError guard must null cmds, mel AND om — not just cmds."""
        from base_test import import_without_maya

        result = import_without_maya(
            "mayatk.anim_utils.playblast_exporter", ("cmds", "mel", "om")
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_arnold_extension_translator_map(self):
        pe_path = "mayatk.anim_utils.playblast_exporter.cmds"
        for translator, expected in (
            ("jpeg", "jpg"),
            ("exr", "exr"),
            ("deepexr", "exr"),
            (None, "exr"),
        ):
            with patch(f"{pe_path}.getAttr", return_value=translator):
                self.assertEqual(MayaPlayblastHost._arnold_extension(), expected)


if __name__ == "__main__":
    unittest.main()
