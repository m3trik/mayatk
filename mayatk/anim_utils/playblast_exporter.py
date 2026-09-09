# !/usr/bin/python
# coding=utf-8
"""Playblast capture, encoding, and preview-render exports for Maya.

Design
------
Maya's half of :class:`pythontk.SequenceExporter`: this module supplies the
pixels and the timeline, and inherits the plan, the target vocabulary and the
ffmpeg encode. The split is what lets the WebXR preview's page recorder --
which captures a browser canvas, not a viewport -- produce the same mp4 through
the same code (:class:`pythontk.SequenceEncoder`).

Maya supplies four primitives:

- ``capture_sequence`` — viewport capture to a numbered image sequence
  (the single source of pixels for every encoded output).
- ``capture_still`` — a single-frame viewport capture to an exact filepath.
- ``capture_movie`` — passthrough to Maya's native movie playblast (legacy
  ``avi``; QuickTime-era ``qt`` support was dropped).
- ``render_with_arnold`` — an Arnold frame-range render.

The shared ``export`` orchestrator plans the requested
:data:`~PlayblastExporter.TARGETS` so the viewport is captured **once** and
every encoded output (mp4/mov/...) is derived from that same lossless sequence
via ffmpeg; the two kinds only Maya has (``native``, ``arnold``) are produced
through the base's ``_export_extra_target`` seam rather than by branching the
shared plan.

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
    mel = None
    om = None

import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union

import pythontk as ptk

#: Re-exported so ``mayatk.anim_utils.playblast_exporter.ExportTarget`` (and the
#: ``mtk.`` names it feeds) keep resolving now that the definitions live in
#: pythontk, shared with every other host of the exporter.
from pythontk import CaptureResult, ExportResult, ExportTarget  # noqa: F401


class PlayblastExporter(ptk.SequenceExporter):
    """Viewport capture and preview-render exports.

    The Maya host of :class:`pythontk.SequenceExporter`: it adds the two
    output kinds only Maya has (its native movie playblast and an Arnold
    render), the scene's timeline and audio, and the camera/panel handling a
    viewport capture needs.

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

    #: Registry of exportable outputs: the shared set, plus the two kinds only
    #: Maya can produce. Appended rather than re-declared, so a target added to
    #: the base reaches this picker without being written twice — and the
    #: shared entries keep their order, which UI pickers persist by index.
    TARGETS: Dict[str, ExportTarget] = {
        **ptk.SequenceExporter.TARGETS,
        **{
            t.name: t
            for t in (
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
        },
    }

    #: Native playblast format -> file extension.
    NATIVE_EXTENSIONS: Dict[str, str] = {"avi": ".avi", "movie": ".avi"}

    #: Frame-range modes accepted by :meth:`resolve_frame_range` — the shared
    #: ``custom`` plus the three the scene's timeline answers.
    RANGE_MODES: Tuple[str, ...] = ("playback", "animation", "current", "custom")

    #: ``export`` keys the Maya targets own, on top of the shared set: the
    #: native movie playblast is handed each of these explicitly, so a caller
    #: repeating one in ``**overrides`` would TypeError that target mid-plan.
    _RESERVED_OVERRIDES: Tuple[str, ...] = ptk.SequenceExporter._RESERVED_OVERRIDES + (
        "sound",
        "fmt",
        "compression",
    )

    #: What an :meth:`export` that names no range means in Maya: the timeline's
    #: playback range, as it always has. The shared default is ``custom``, which
    #: is the only mode an exporter with no timeline has -- and which raises for
    #: want of bounds rather than quietly exporting the wrong thing.
    DEFAULT_RANGE_MODE: str = "playback"

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
        super().__init__(
            width=width,
            height=height,
            quality=quality,
            frame_padding=frame_padding,
            include_audio=include_audio,
            **kwargs,
        )
        self.camera = camera
        self.percent = int(percent)
        self.off_screen = bool(off_screen)
        self.show_ornaments = bool(show_ornaments)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @staticmethod
    def scene_name() -> str:
        """Basename of the current scene without extension; ``"playblast"``
        for an unsaved scene. ``EnvUtils.saved_scene_path`` owns the
        phantom-``untitled`` rule."""
        from mayatk.env_utils._env_utils import EnvUtils  # deferred: avoid import cycle

        scene = EnvUtils.saved_scene_path()
        if scene:
            return os.path.basename(scene).rsplit(".", 1)[0]
        return "playblast"

    @staticmethod
    def scene_fps() -> float:
        """The scene frame rate as a float."""
        return ptk.VidUtils.get_frame_rate(cmds.currentUnit(q=True, time=True))

    # The shared core asks for "the sequence's" name and rate; in Maya both are
    # the scene's. Bound here rather than renamed, because the scene-flavoured
    # names are the public ones and are what a Maya caller looks for.
    def sequence_name(self) -> str:
        return self.scene_name()

    def sequence_fps(self) -> float:
        return self.scene_fps()

    @classmethod
    def _frame_range_for_mode(cls, mode: str) -> Tuple[float, float]:
        """The scene's ``(start, end)`` for a timeline range mode.

        Modes: ``playback`` (timeline min/max), ``animation`` (animation
        start/end), ``current`` (single current frame). ``custom`` never
        reaches here — the shared :meth:`resolve_frame_range` answers it.
        """
        if mode == "animation":
            return (
                cmds.playbackOptions(q=True, animationStartTime=True),
                cmds.playbackOptions(q=True, animationEndTime=True),
            )
        if mode == "current":
            now = cmds.currentTime(query=True)
            return now, now
        return (
            cmds.playbackOptions(q=True, minTime=True),
            cmds.playbackOptions(q=True, maxTime=True),
        )

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

    def _sound_source(self) -> Optional[str]:
        """The shared plan's name for :meth:`resolve_sound_node`."""
        return self.resolve_sound_node()

    def _notify(self, message: str) -> None:
        """Route the shared core's produced-file notices to Maya's message line."""
        if om is not None:
            om.MGlobal.displayInfo(message)
        else:  # mayapy without initialize, or an import-guarded environment
            super()._notify(message)

    def _warn(self, message: str) -> None:
        """Route the shared plan's per-target failures to Maya's warning line."""
        if cmds is not None:
            cmds.warning(message)
        else:
            super()._warn(message)

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
        self._remove_frames(directory, prefix, image_format)

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
                f"(prefix {prefix!r}) -- an interrupted playblast (Esc) stops early."
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
        self._notify(f"Still frame captured: {filepath}")
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
        self._notify(f"Playblast movie created: {produced}")
        return produced

    # ------------------------------------------------------------------
    # Host-only targets
    # ------------------------------------------------------------------
    def _export_extra_target(
        self,
        spec: ExportTarget,
        output_dir: str,
        name: str,
        start: int,
        end: int,
        camera: Optional[str],
        sound: Optional[str],
        overrides: Dict[str, Any],
    ) -> Union[str, List[str]]:
        """Produce the two kinds only Maya has, for the shared plan.

        The OCP seam :class:`pythontk.SequenceExporter` leaves open: neither of
        these comes off the shared image capture (Maya's movie playblast writes
        its own container; Arnold renders rather than reads the viewport), so
        they cannot be ``encode`` targets — and the shared plan must not learn
        their names to run them.
        """
        if spec.kind == "native":
            return self.capture_movie(
                os.path.join(output_dir, f"{name}.{spec.extension}"),
                fmt=spec.native_format,
                compression=spec.native_compression,
                start=start,
                end=end,
                camera=camera,
                sound=sound,
                **overrides,
            )
        if spec.kind == "arnold":
            return self.render_with_arnold(
                output_dir=os.path.join(output_dir, f"{name}_arnold"),
                start=start,
                end=end,
                camera=camera,
                prefix=name,
            )
        return super()._export_extra_target(
            spec,
            output_dir=output_dir,
            name=name,
            start=start,
            end=end,
            camera=camera,
            sound=sound,
            overrides=overrides,
        )

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
        padding = int(
            frame_padding if frame_padding is not None else self.frame_padding
        )
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
        self._notify(
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
        for reserved in (
            "filename",
            "completeFilename",
            "startTime",
            "endTime",
            "frame",
        ):
            kwargs.pop(reserved, None)
        return kwargs

    def _resolve_audio_source(self) -> Tuple[Optional[str], float]:
        """``(audio filepath, offset in frames)`` of the timeline's armed sound.

        The rebase onto the capture's first frame is the shared core's — this
        answers only the part that needs Maya.
        """
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
        return filepath, float(offset_frames)

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
