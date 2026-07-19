# !/usr/bin/python
# coding=utf-8
"""Playblast capture, encoding, and preview-render exports for Maya.

Design
------
Everything reduces to four primitives plus one orchestrator:

- ``capture_sequence`` — viewport capture to a numbered image sequence
  (the single source of pixels for every encoded output).
- ``capture_still`` — a single-frame viewport capture to an exact filepath.
- ``capture_movie`` — passthrough to Maya's native movie playblast (legacy
  ``avi``; QuickTime-era ``qt`` support was dropped).
- ``render_with_arnold`` — an Arnold frame-range render.
- ``export`` — plans the requested :data:`~PlayblastExporter.TARGETS` so the
  viewport is captured **once** and every encoded output (mp4/mov/...) is
  derived from that same lossless sequence via ffmpeg.

Extend by registering a new :class:`ExportTarget` in
``PlayblastExporter.TARGETS`` — UIs build their pickers from
``available_targets()``.
"""
from __future__ import annotations

try:
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMaya as om
except ImportError:
    cmds = None

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import pythontk as ptk


@dataclass(frozen=True)
class ExportTarget:
    """One entry in the playblast target registry.

    Attributes:
        name: Registry key (e.g. ``"mp4"``).
        label: Human-readable label for UI pickers.
        kind: ``"encode"`` (ffmpeg from the shared capture), ``"sequence"``
            (numbered image frames), ``"still"`` (single frame),
            ``"native"`` (Maya's own movie playblast), or ``"arnold"``.
        image_format: Capture compression for image-based kinds.
        extension: Output extension without the dot.
        encoder_options: Extra ffmpeg options for ``encode`` targets.
        native_format: ``cmds.playblast`` format for ``native`` targets.
        native_compression: ``cmds.playblast`` compression for ``native`` targets.
    """

    name: str
    label: str
    kind: str
    image_format: str = "png"
    extension: str = ""
    encoder_options: Dict[str, Any] = field(default_factory=dict)
    native_format: str = ""
    native_compression: str = ""


@dataclass
class CaptureResult:
    """A captured image sequence on disk."""

    directory: str
    prefix: str
    image_format: str
    start: int
    end: int
    padding: int
    frames: List[str]
    fps: float

    @property
    def pattern(self) -> str:
        """printf-style pattern for the sequence (ffmpeg input)."""
        return ptk.format_path(
            os.path.join(
                self.directory,
                f"{self.prefix}.%0{self.padding}d.{self.image_format}",
            )
        )


@dataclass
class ExportResult:
    """Outcome of one export target."""

    target: str
    kind: str
    output: Optional[Union[str, List[str]]] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class PlayblastExporter(ptk.LoggingMixin):
    """Viewport capture and preview-render exports.

    Instance attributes hold capture *defaults*; every public method accepts
    per-call overrides. Frame ranges are resolved at call time (never cached),
    so the exporter tracks timeline changes made after construction.

    Parameters:
        camera: Default camera (transform or shape) for captures. ``None``
            keeps whatever the capture panel is looking through.
        width/height: Capture resolution.
        percent: Maya's playblast scale percentage.
        quality: 0-100; drives native playblast quality and the ffmpeg CRF
            for encoded targets.
        off_screen: Capture offscreen (avoids viewport redraw artifacts).
        show_ornaments: Include HUD / ornaments.
        frame_padding: Digits for image-sequence frame numbers.
        include_audio: Attach the scene's active sound to movie outputs
            (native ``sound`` flag; ffmpeg mux for encoded targets).
    """

    #: Registry of exportable outputs. Extend with new ExportTarget entries.
    TARGETS: Dict[str, ExportTarget] = {
        t.name: t
        for t in (
            ExportTarget(
                "mp4",
                "MP4 (H.264)",
                "encode",
                extension="mp4",
                encoder_options={"movflags": "+faststart"},
            ),
            ExportTarget(
                "mov",
                "MOV (H.264)",
                "encode",
                extension="mov",
                encoder_options={"movflags": "+faststart"},
            ),
            ExportTarget("png_sequence", "PNG Sequence", "sequence", extension="png"),
            ExportTarget(
                "jpg_sequence",
                "JPEG Sequence",
                "sequence",
                image_format="jpg",
                extension="jpg",
            ),
            ExportTarget(
                "tif_sequence",
                "TIFF Sequence",
                "sequence",
                image_format="tif",
                extension="tif",
            ),
            ExportTarget(
                "tga_sequence",
                "TGA Sequence",
                "sequence",
                image_format="tga",
                extension="tga",
            ),
            ExportTarget("still", "PNG Still (Current Frame)", "still", extension="png"),
            ExportTarget(
                "avi",
                "AVI (Uncompressed)",
                "native",
                extension="avi",
                native_format="avi",
                native_compression="none",
            ),
            ExportTarget("arnold", "Arnold Sequence", "arnold", extension="exr"),
        )
    }

    #: Native playblast format -> file extension.
    NATIVE_EXTENSIONS: Dict[str, str] = {"avi": ".avi", "movie": ".avi", "qt": ".mov"}

    #: Frame-range modes accepted by :meth:`resolve_frame_range`.
    RANGE_MODES: Tuple[str, ...] = ("playback", "animation", "current", "custom")

    def __init__(
        self,
        camera: Optional[str] = None,
        width: int = 1920,
        height: int = 1080,
        percent: int = 100,
        quality: int = 100,
        off_screen: bool = True,
        show_ornaments: bool = True,
        frame_padding: int = 4,
        include_audio: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.camera = camera
        self.width = int(width)
        self.height = int(height)
        self.percent = int(percent)
        self.quality = int(quality)
        self.off_screen = bool(off_screen)
        self.show_ornaments = bool(show_ornaments)
        self.frame_padding = int(frame_padding)
        self.include_audio = bool(include_audio)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @classmethod
    def available_targets(cls) -> List[Tuple[str, str]]:
        """(name, label) pairs in registry order — for building UI pickers."""
        return [(t.name, t.label) for t in cls.TARGETS.values()]

    @staticmethod
    def scene_name() -> str:
        """Basename of the current scene without extension; ``"playblast"``
        for an unsaved scene (batch reports a phantom extensionless
        ``untitled`` path — a real scene file always has an extension)."""
        scene = cmds.file(query=True, sceneName=True)
        if scene and os.path.splitext(scene)[1]:
            return os.path.basename(scene).rsplit(".", 1)[0]
        return "playblast"

    @staticmethod
    def scene_fps() -> float:
        """The scene frame rate as a float."""
        return ptk.VidUtils.get_frame_rate(cmds.currentUnit(q=True, time=True))

    @classmethod
    def resolve_frame_range(
        cls,
        mode: str = "playback",
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> Tuple[int, int]:
        """Resolve a frame range from a mode, with explicit overrides.

        Modes: ``playback`` (timeline min/max), ``animation`` (animation
        start/end), ``current`` (single current frame), ``custom`` (both
        ``start`` and ``end`` required). Explicit ``start``/``end`` override
        the mode's values individually.
        """
        if mode not in cls.RANGE_MODES:
            raise ValueError(f"Unknown range mode {mode!r}; expected one of {cls.RANGE_MODES}.")
        if mode == "custom":
            if start is None or end is None:
                raise ValueError("Custom range mode requires both start and end.")
            mode_start, mode_end = start, end
        elif mode == "animation":
            mode_start = cmds.playbackOptions(q=True, animationStartTime=True)
            mode_end = cmds.playbackOptions(q=True, animationEndTime=True)
        elif mode == "current":
            mode_start = mode_end = cmds.currentTime(query=True)
        else:  # playback
            mode_start = cmds.playbackOptions(q=True, minTime=True)
            mode_end = cmds.playbackOptions(q=True, maxTime=True)

        resolved_start = int(start if start is not None else mode_start)
        resolved_end = int(end if end is not None else mode_end)
        if resolved_start > resolved_end:
            raise ValueError(
                f"Start frame {resolved_start} is after end frame {resolved_end}."
            )
        return resolved_start, resolved_end

    @staticmethod
    def resolve_sound_node() -> Optional[str]:
        """The timeline's active audio node, or the scene's sole audio node.

        Returns None when there is no unambiguous sound source (no GUI
        timeline and zero or multiple audio nodes).
        """
        try:
            slider = mel.eval("$_playblast_tmp = $gPlayBackSlider")
            sound = cmds.timeControl(slider, q=True, sound=True)
            if sound:
                return sound
        except Exception:  # batch mode: no playback slider
            pass
        nodes = cmds.ls(type="audio") or []
        return nodes[0] if len(nodes) == 1 else None

    # ------------------------------------------------------------------
    # Capture primitives
    # ------------------------------------------------------------------
    def capture_sequence(
        self,
        directory: str,
        prefix: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        camera: Optional[str] = None,
        image_format: str = "png",
        **overrides: Any,
    ) -> CaptureResult:
        """Capture the frame range as a numbered image sequence.

        Frames keep their real scene frame numbers
        (``<prefix>.<frame>.<ext>``). Raises RuntimeError when Maya writes
        fewer frames than requested.
        """
        directory = ptk.format_path(os.path.abspath(directory))
        os.makedirs(directory, exist_ok=True)
        prefix = prefix or self.scene_name()
        start, end = self.resolve_frame_range("playback", start, end)

        kwargs = self._playblast_kwargs(overrides)
        kwargs.update(
            format="image",
            compression=image_format,
            framePadding=overrides.get("framePadding", self.frame_padding),
        )
        padding = kwargs["framePadding"]

        # directory+prefix identifies THIS capture: frames left by an
        # earlier/wider run would pass the count check below and — worse —
        # ffmpeg reads a printf pattern contiguously past ``end``, encoding
        # stale frames into the movie.
        for stale in self._collect_frames(directory, prefix, image_format):
            try:
                os.remove(stale)
            except OSError as exc:
                self.logger.warning(f"Could not remove stale frame {stale!r}: {exc}")

        with self._camera_view(camera) as panel:
            if panel:
                kwargs.setdefault("editorPanelName", panel)
            self.logger.debug(
                f"capture_sequence {prefix} [{start}-{end}] -> {directory} ({kwargs})"
            )
            cmds.playblast(
                filename=os.path.join(directory, prefix),
                startTime=start,
                endTime=end,
                **kwargs,
            )

        frames = self._collect_frames(directory, prefix, image_format, start, end)
        expected = end - start + 1
        if len(frames) < expected:
            raise RuntimeError(
                f"Playblast wrote {len(frames)}/{expected} frames under {directory!r} "
                f"(prefix {prefix!r})."
            )
        return CaptureResult(
            directory=directory,
            prefix=prefix,
            image_format=image_format,
            start=start,
            end=end,
            padding=padding,
            frames=frames,
            fps=self.scene_fps(),
        )

    def capture_still(
        self,
        filepath: str,
        frame: Optional[int] = None,
        camera: Optional[str] = None,
        image_format: str = "png",
        **overrides: Any,
    ) -> str:
        """Capture a single frame to an exact filepath (default: current frame)."""
        filepath = ptk.format_path(os.path.abspath(filepath))
        output_dir = os.path.dirname(filepath)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        frame = int(frame if frame is not None else cmds.currentTime(query=True))

        kwargs = self._playblast_kwargs(overrides)
        kwargs.update(format="image", compression=image_format)
        kwargs.pop("clearCache", None)  # incompatible with single-frame capture

        with self._camera_view(camera) as panel:
            if panel:
                kwargs.setdefault("editorPanelName", panel)
            cmds.playblast(frame=[frame], completeFilename=filepath, **kwargs)

        if not self._is_valid_file(filepath):
            raise RuntimeError(f"Still capture failed; no file at {filepath!r}.")
        om.MGlobal.displayInfo(f"Still frame captured: {filepath}")
        return filepath

    def capture_movie(
        self,
        filepath: str,
        fmt: str = "avi",
        compression: str = "none",
        start: Optional[int] = None,
        end: Optional[int] = None,
        camera: Optional[str] = None,
        sound: Optional[str] = None,
        **overrides: Any,
    ) -> str:
        """Capture with Maya's native movie playblast (``avi``/``movie``).

        The filepath extension is enforced against the format; a missing
        extension is appended.
        """
        extension = self.NATIVE_EXTENSIONS.get(fmt.lower())
        if not extension:
            raise ValueError(
                f"Unknown native movie format {fmt!r}; expected one of "
                f"{sorted(self.NATIVE_EXTENSIONS)}."
            )
        filepath = ptk.format_path(os.path.abspath(filepath))
        base, ext = os.path.splitext(filepath)
        if not ext:
            filepath = base + extension
        elif ext.lower() != extension:
            raise ValueError(
                f"Extension {ext!r} does not match playblast format {fmt!r} "
                f"({extension})."
            )
        output_dir = os.path.dirname(filepath)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        start, end = self.resolve_frame_range("playback", start, end)

        kwargs = self._playblast_kwargs(overrides)
        kwargs.update(format=fmt, compression=compression)
        if sound:
            kwargs.setdefault("sound", sound)

        with self._camera_view(camera) as panel:
            if panel:
                kwargs.setdefault("editorPanelName", panel)
            self.logger.debug(f"capture_movie [{start}-{end}] -> {filepath} ({kwargs})")
            result = cmds.playblast(
                filename=filepath, startTime=start, endTime=end, **kwargs
            )

        produced = ptk.format_path(str(result)) if result else filepath
        if not self._is_valid_file(produced):
            raise RuntimeError(
                "Playblast failed; Maya did not report a valid output file."
            )
        om.MGlobal.displayInfo(f"Playblast movie created: {produced}")
        return produced

    def encode_sequence(
        self,
        capture: Union[CaptureResult, str],
        output_filepath: str,
        fps: Optional[float] = None,
        audio: Optional[Union[bool, str]] = None,
        quality: Optional[int] = None,
        **ffmpeg_options: Any,
    ) -> str:
        """Encode a captured image sequence to a movie via ffmpeg.

        Parameters:
            capture: A :class:`CaptureResult` or a printf-style pattern.
            audio: True resolves the scene's active sound node; a string is
                an audio filepath used as-is.
            quality: 0-100, mapped onto the H.264 CRF scale (100 -> 16).
        """
        if isinstance(capture, CaptureResult):
            pattern = capture.pattern
            start_number: Optional[int] = capture.start
            fps = fps if fps is not None else capture.fps
            capture_start = capture.start
        else:
            pattern = capture
            start_number = None
            capture_start = None
        fps = fps if fps is not None else self.scene_fps()

        audio_filepath, audio_offset = None, 0.0
        if audio:
            audio_filepath, audio_offset = self._resolve_audio(
                audio, capture_start, fps
            )

        quality = self.quality if quality is None else int(quality)
        ffmpeg_options.setdefault("crf", self._quality_to_crf(quality))

        output_filepath = ptk.format_path(os.path.abspath(output_filepath))
        encoded = ptk.VidUtils.compress_video(
            input_filepath=pattern,
            output_filepath=output_filepath,
            frame_rate=fps,
            start_number=start_number,
            audio_filepath=audio_filepath,
            audio_offset=audio_offset,
            **ffmpeg_options,
        )
        if not encoded or not self._is_valid_file(encoded):
            raise RuntimeError(
                f"ffmpeg encode failed for {pattern!r} -> {output_filepath!r}."
            )
        om.MGlobal.displayInfo(f"Encoded movie created: {encoded}")
        return encoded

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------
    def export(
        self,
        output_dir: str,
        name: Optional[str] = None,
        targets: Union[str, Sequence[str]] = ("mp4",),
        range_mode: str = "playback",
        start: Optional[int] = None,
        end: Optional[int] = None,
        camera: Optional[str] = None,
        keep_frames: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        **overrides: Any,
    ) -> List[ExportResult]:
        """Produce one or more registered targets from a single plan.

        The viewport is captured once per required image format; every
        ``encode`` target reuses the lossless PNG capture. When no
        ``png_sequence`` target is requested, the intermediate frames live in
        ``<output_dir>/<name>_png_tmp`` and are deleted afterward unless
        ``keep_frames`` is True.

        Outputs: ``<dir>/<name>.<ext>`` for movies and the still;
        ``<dir>/<name>_<fmt>/`` for sequences; ``<dir>/<name>_arnold/`` for
        Arnold. Per-target failures are captured on the returned
        :class:`ExportResult`\\ s rather than raised.
        """
        if isinstance(targets, str):
            targets = [targets]
        unknown = [t for t in targets if t not in self.TARGETS]
        if unknown:
            raise ValueError(
                f"Unknown export target(s) {unknown}; available: {sorted(self.TARGETS)}."
            )
        ordered = list(dict.fromkeys(targets))  # dedupe, keep order
        specs = [self.TARGETS[t] for t in ordered]

        # Owned by export's named parameters / per-target planning — a stray
        # duplicate in **overrides would TypeError one target mid-plan.
        for owned in ("image_format", "camera", "start", "end", "prefix", "sound",
                      "fmt", "compression", "filepath", "directory"):
            overrides.pop(owned, None)

        output_dir = ptk.format_path(os.path.abspath(output_dir))
        os.makedirs(output_dir, exist_ok=True)
        name = name or self.scene_name()
        start, end = self.resolve_frame_range(range_mode, start, end)
        camera = camera if camera is not None else self.camera

        sound_node = self.resolve_sound_node() if self.include_audio else None

        encode_specs = [s for s in specs if s.kind == "encode"]
        sequence_specs = [s for s in specs if s.kind == "sequence"]

        # One capture per required image format. Encodes ride on the png
        # capture — shared with a requested png_sequence when present.
        capture_formats = {s.image_format for s in sequence_specs}
        needs_tmp_png = bool(encode_specs) and "png" not in capture_formats
        plan_formats = sorted(capture_formats | ({"png"} if needs_tmp_png else set()))

        total_steps = len(plan_formats) + sum(
            1 for s in specs if s.kind in ("encode", "native", "still", "arnold")
        )
        step = 0

        def progress(label: str) -> None:
            nonlocal step
            if progress_callback:
                progress_callback(step, total_steps, label)
            step += 1

        results: Dict[str, ExportResult] = {
            s.name: ExportResult(target=s.name, kind=s.kind) for s in specs
        }
        captures: Dict[str, CaptureResult] = {}
        tmp_png_dir = ptk.format_path(os.path.join(output_dir, f"{name}_png_tmp"))

        try:
            for fmt in plan_formats:
                progress(f"Capturing {fmt} frames")
                is_tmp = fmt == "png" and needs_tmp_png
                seq_dir = (
                    tmp_png_dir
                    if is_tmp
                    else ptk.format_path(os.path.join(output_dir, f"{name}_{fmt}"))
                )
                try:
                    captures[fmt] = self.capture_sequence(
                        directory=seq_dir,
                        prefix=name,
                        start=start,
                        end=end,
                        camera=camera,
                        image_format=fmt,
                        **overrides,
                    )
                except Exception as exc:  # noqa: BLE001 - isolate per plan step
                    self.logger.warning(f"Capture ({fmt}) failed: {exc}")
                    dependents = [s for s in sequence_specs if s.image_format == fmt]
                    if fmt == "png":
                        dependents += encode_specs
                    for spec in dependents:
                        results[spec.name].error = str(exc)

            for spec in specs:
                result = results[spec.name]
                if result.error is not None:
                    continue
                try:
                    if spec.kind == "sequence":
                        capture = captures.get(spec.image_format)
                        if capture is None:  # invariant: errored above otherwise
                            raise RuntimeError(
                                f"{spec.image_format} capture unavailable."
                            )
                        result.output = capture.frames
                    elif spec.kind == "encode":
                        capture = captures.get("png")
                        if capture is None:
                            raise RuntimeError("Shared PNG capture unavailable.")
                        progress(f"Encoding {spec.label}")
                        result.output = self.encode_sequence(
                            capture,
                            os.path.join(output_dir, f"{name}.{spec.extension}"),
                            audio=bool(sound_node),
                            **dict(spec.encoder_options),
                        )
                    elif spec.kind == "native":
                        progress(f"Capturing {spec.label}")
                        result.output = self.capture_movie(
                            os.path.join(output_dir, f"{name}.{spec.extension}"),
                            fmt=spec.native_format,
                            compression=spec.native_compression,
                            start=start,
                            end=end,
                            camera=camera,
                            sound=sound_node,
                            **overrides,
                        )
                    elif spec.kind == "still":
                        progress(f"Capturing {spec.label}")
                        result.output = self.capture_still(
                            os.path.join(output_dir, f"{name}.{spec.extension}"),
                            camera=camera,
                            image_format=spec.image_format,
                            **overrides,
                        )
                    elif spec.kind == "arnold":
                        progress(f"Rendering {spec.label}")
                        result.output = self.render_with_arnold(
                            output_dir=os.path.join(output_dir, f"{name}_arnold"),
                            start=start,
                            end=end,
                            camera=camera,
                            prefix=name,
                        )
                except Exception as exc:  # noqa: BLE001 - isolate per target
                    result.error = str(exc)
                    cmds.warning(f"Playblast target '{spec.name}' failed: {exc}")
        finally:
            if not keep_frames and needs_tmp_png:
                self._remove_capture(captures.get("png"), tmp_png_dir)

        if progress_callback:
            progress_callback(total_steps, total_steps, "Done")
        return [results[s.name] for s in specs]

    # ------------------------------------------------------------------
    # Arnold
    # ------------------------------------------------------------------
    def render_with_arnold(
        self,
        output_dir: str,
        start: Optional[int] = None,
        end: Optional[int] = None,
        camera: Optional[str] = None,
        prefix: Optional[str] = None,
        frame_padding: Optional[int] = None,
        render_layer: Optional[str] = None,
        **kwargs: Any,
    ) -> List[str]:
        """Render a frame range with Arnold; returns the files this run wrote.

        Render globals touched for the run are restored afterward.
        """
        output_dir = ptk.format_path(os.path.abspath(output_dir))
        os.makedirs(output_dir, exist_ok=True)

        from mayatk.env_utils._env_utils import EnvUtils  # deferred: avoid import cycle

        EnvUtils.load_plugin("mtoa")  # raises ValueError when unavailable

        camera_shape = self._resolve_camera_shape(camera or self.camera)
        if not camera_shape:
            raise ValueError("Could not resolve a valid camera for Arnold rendering.")

        prefix = prefix or self.scene_name()
        start, end = self.resolve_frame_range("playback", start, end)
        padding = int(frame_padding if frame_padding is not None else self.frame_padding)
        layer = render_layer or cmds.editRenderLayerGlobals(
            query=True, currentRenderLayer=True
        )
        extension = self._arnold_extension()
        preexisting = self._snapshot_files(output_dir, prefix, extension)

        old_workspace_images = cmds.workspace(fileRuleEntry="images")
        old_animation = cmds.getAttr("defaultRenderGlobals.animation")
        old_start = cmds.getAttr("defaultRenderGlobals.startFrame")
        old_end = cmds.getAttr("defaultRenderGlobals.endFrame")
        old_padding = cmds.getAttr("defaultRenderGlobals.framePadding")
        old_prefix = cmds.getAttr("defaultRenderGlobals.imageFilePrefix")

        try:
            cmds.workspace(fileRule=("images", output_dir))
            cmds.setAttr("defaultRenderGlobals.imageFilePrefix", prefix, type="string")
            cmds.setAttr("defaultRenderGlobals.animation", True)
            cmds.setAttr("defaultRenderGlobals.startFrame", start)
            cmds.setAttr("defaultRenderGlobals.endFrame", end)
            cmds.setAttr("defaultRenderGlobals.framePadding", padding)

            cmds.arnoldRender(
                seq=True,
                startFrame=start,
                endFrame=end,
                camera=camera_shape,
                layer=layer,
                **kwargs,
            )
        finally:
            cmds.workspace(fileRule=("images", old_workspace_images or "images"))
            # getAttr returns None when the prefix was never set; setAttr
            # rejects None for string attrs — and this is a finally block.
            cmds.setAttr(
                "defaultRenderGlobals.imageFilePrefix", old_prefix or "", type="string"
            )
            cmds.setAttr("defaultRenderGlobals.animation", old_animation)
            cmds.setAttr("defaultRenderGlobals.startFrame", old_start)
            cmds.setAttr("defaultRenderGlobals.endFrame", old_end)
            cmds.setAttr("defaultRenderGlobals.framePadding", old_padding)

        # Only files this run created or rewrote — a shared output dir can
        # hold frames from earlier renders with the same prefix.
        rendered = [
            path
            for path, mtime in self._snapshot_files(
                output_dir, prefix, extension
            ).items()
            if path not in preexisting or preexisting[path] != mtime
        ]
        rendered.sort()
        om.MGlobal.displayInfo(
            f"Arnold render completed: {len(rendered)} frame(s) written to {output_dir}"
        )
        return rendered

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _playblast_kwargs(self, overrides: Dict[str, Any]) -> Dict[str, Any]:
        """Instance defaults merged with per-call cmds.playblast overrides."""
        kwargs: Dict[str, Any] = {
            "forceOverwrite": True,
            "viewer": False,
            "offScreen": self.off_screen,
            "showOrnaments": self.show_ornaments,
            "percent": self.percent,
            "quality": self.quality,
            "widthHeight": (self.width, self.height),
            "clearCache": True,
        }
        kwargs.update(overrides)
        # Owned by the calling method's explicit arguments.
        for reserved in ("filename", "completeFilename", "startTime", "endTime", "frame"):
            kwargs.pop(reserved, None)
        return kwargs

    @staticmethod
    def _quality_to_crf(quality: int) -> int:
        """Map 0-100 quality onto the H.264 CRF scale (100 -> 16, 0 -> 40)."""
        quality = max(0, min(100, int(quality)))
        return round(40 - quality * 0.24)

    @staticmethod
    def _is_valid_file(path: Optional[str]) -> bool:
        return bool(path) and os.path.exists(path) and os.path.getsize(path) > 0

    @staticmethod
    def _collect_frames(
        directory: str,
        prefix: str,
        image_format: str,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> List[str]:
        """Frames on disk matching ``<prefix>.<number>.<ext>``, numerically
        sorted; ``start``/``end`` bound the frame numbers when given."""
        regex = re.compile(
            rf"{re.escape(prefix)}\.(\d+)\.{re.escape(image_format)}$", re.IGNORECASE
        )
        numbered = []
        try:
            entries = os.listdir(directory)
        except OSError:
            return []
        for entry in entries:
            match = regex.match(entry)
            if not match:
                continue
            number = int(match.group(1))
            if (start is None or number >= start) and (end is None or number <= end):
                numbered.append((number, ptk.format_path(os.path.join(directory, entry))))
        return [path for _, path in sorted(numbered)]

    def _remove_capture(
        self, capture: Optional[CaptureResult], directory: str
    ) -> None:
        """Delete intermediate frames (and their dir when it ends up empty)."""
        if capture:
            for frame in capture.frames:
                try:
                    os.remove(frame)
                except OSError:
                    pass
        try:
            if os.path.isdir(directory) and not os.listdir(directory):
                os.rmdir(directory)
        except OSError:
            pass

    def _resolve_audio(
        self,
        audio: Union[bool, str],
        capture_start: Optional[int],
        fps: float,
    ) -> Tuple[Optional[str], float]:
        """(audio filepath, offset seconds) for an encode; (None, 0) if unresolvable."""
        if isinstance(audio, str):
            return (audio if os.path.isfile(audio) else None), 0.0
        node = self.resolve_sound_node()
        if not node:
            return None, 0.0
        try:
            filepath = cmds.getAttr(f"{node}.filename")
            offset_frames = cmds.getAttr(f"{node}.offset") or 0.0
        except Exception:
            return None, 0.0
        if not filepath or not os.path.isfile(filepath):
            self.logger.warning(f"Audio file for {node!r} not found; skipping audio.")
            return None, 0.0
        offset_seconds = (
            (float(offset_frames) - capture_start) / fps
            if capture_start is not None and fps
            else 0.0
        )
        return filepath, offset_seconds

    # --- camera / panel -------------------------------------------------
    @staticmethod
    def _find_capture_panel() -> Optional[str]:
        """The model panel a playblast will read: focused, else first visible."""
        focused = cmds.getPanel(withFocus=True)
        if focused and cmds.getPanel(typeOf=focused) == "modelPanel":
            return focused
        for panel in cmds.getPanel(visiblePanels=True) or []:
            if cmds.getPanel(typeOf=panel) == "modelPanel":
                return panel
        panels = cmds.getPanel(type="modelPanel") or []
        return panels[0] if panels else None

    @contextmanager
    def _camera_view(self, camera: Optional[str]):
        """Temporarily aim the capture panel at ``camera``; yields the panel.

        Only the panel being captured is touched (never every model panel),
        and its original camera is restored on exit.
        """
        camera = camera if camera is not None else self.camera
        if camera and not cmds.objExists(camera):
            raise ValueError(f"Camera '{camera}' does not exist.")
        panel = self._find_capture_panel()
        if not camera or not panel:
            if camera:  # batch/headless: no panel to retarget
                self.logger.warning(
                    f"No model panel available; camera override {camera!r} not applied."
                )
            yield panel
            return
        original = cmds.modelPanel(panel, q=True, camera=True)
        cmds.modelEditor(panel, e=True, camera=camera)
        try:
            yield panel
        finally:
            if cmds.control(panel, exists=True) and original:
                cmds.modelEditor(panel, e=True, camera=original)

    def _resolve_camera_shape(self, camera: Optional[str]) -> Optional[str]:
        """Resolve a camera transform/shape name to its shape node."""
        target = camera or self._active_viewport_camera()
        if not target:
            return None
        camera_nodes = cmds.ls(str(target), dag=True, type="camera")
        if camera_nodes:
            return camera_nodes[0]
        try:
            shapes = (
                cmds.listRelatives(
                    str(target), shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            return shapes[0] if shapes else None
        except Exception:
            return None

    @staticmethod
    def _active_viewport_camera() -> Optional[str]:
        active_panel = cmds.getPanel(withFocus=True)
        if active_panel and cmds.getPanel(typeOf=active_panel) == "modelPanel":
            return cmds.modelPanel(active_panel, query=True, camera=True)
        try:
            return cmds.lookThru(query=True)
        except Exception:
            return None

    # --- arnold ---------------------------------------------------------
    #: Arnold ai_translator -> written file extension.
    _ARNOLD_EXTENSIONS: Dict[str, str] = {
        "jpeg": "jpg",
        "png": "png",
        "exr": "exr",
        "deepexr": "exr",
        "tif": "tif",
        "maya": "iff",
    }

    @classmethod
    def _arnold_extension(cls) -> str:
        """File extension the Arnold driver will write (default ``exr``)."""
        try:
            translator = cmds.getAttr("defaultArnoldDriver.ai_translator")
        except Exception:
            return "exr"
        if not isinstance(translator, str) or not translator:
            return "exr"
        return cls._ARNOLD_EXTENSIONS.get(translator.lower(), translator.lower())

    @staticmethod
    def _snapshot_files(
        output_dir: str, prefix: str, extension: str
    ) -> Dict[str, float]:
        """{path: mtime} of files under output_dir matching prefix + extension."""
        snapshot: Dict[str, float] = {}
        for root, _, files in os.walk(output_dir):
            for filename in files:
                if filename.startswith(prefix) and filename.endswith(f".{extension}"):
                    path = ptk.format_path(os.path.join(root, filename))
                    try:
                        snapshot[path] = os.path.getmtime(path)
                    except OSError:
                        continue
        return snapshot
