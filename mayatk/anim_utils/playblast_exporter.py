# !/usr/bin/python
# coding=utf-8
"""Playblast capture, encoding, and preview-render exports for Maya.

Design
------
The plan, the target vocabulary, the frame bookkeeping and the ffmpeg encode
are :class:`pythontk.SequenceExporter`'s, shared with blendertk and with the
WebXR preview's page recorder (:class:`pythontk.SequenceEncoder`). What reaches
Maya is its host adapter, :class:`MayaPlayblastHost` (a
:class:`pythontk.SequenceHost`):

- ``capture_frames`` -- a viewport playblast to a numbered image sequence
  (the single source of pixels for every encoded output);
- ``capture_still`` -- a single-frame playblast to an exact filepath;
- ``capture_movie`` -- Maya's native movie playblast (``avi``; QuickTime-era
  ``qt`` support was dropped);
- ``export_target`` -- the ``arnold`` kind: an Arnold frame-range render
  (:meth:`MayaPlayblastHost.render_with_arnold`);
- the timeline's range modes, the scene's name and rate, the armed sound, and
  Maya's message and warning lines.

:class:`PlayblastExporter` is the shared class bound to it: configuration (its
``TARGETS`` add ``avi`` and ``arnold``) plus Maya's own public calls
(``capture_movie``, ``render_with_arnold``, ``resolve_sound_node``). Extend by
registering a new :class:`ExportTarget` in ``PlayblastExporter.TARGETS`` --
UIs build their pickers from ``available_targets()``.
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

from mayatk.render_utils._render_utils import RenderUtils


class MayaPlayblastHost(ptk.PureSequenceHost):
    """What the sequence exporter asks of Maya (:class:`pythontk.SequenceHost`):
    the viewport, the timeline, the armed sound, and the two output kinds only
    Maya has (its native movie playblast, an Arnold render)."""

    #: Native playblast format -> file extension.
    NATIVE_EXTENSIONS: Dict[str, str] = {"avi": ".avi", "movie": ".avi"}

    #: The render-options plug an unlicensed batch render reads. mtoa creates
    #: the options with it on, so the batch render aborts; it is off for the
    #: run, so a preview watermarks as Maya's interactive Render View does.
    _ABORT_ON_LICENSE_FAIL: str = "defaultArnoldRenderOptions.abortOnLicenseFail"

    #: Arnold ai_translator -> written file extension.
    _ARNOLD_EXTENSIONS: Dict[str, str] = {
        "jpeg": "jpg",
        "png": "png",
        "exr": "exr",
        "deepexr": "exr",
        "tif": "tif",
        "maya": "iff",
    }

    # ------------------------------------------------------------------
    # The scene
    # ------------------------------------------------------------------
    @staticmethod
    def frame_range(mode: str) -> Tuple[float, float]:
        """The scene's ``(start, end)`` for a timeline range mode.

        Modes: ``playback`` (timeline min/max), ``animation`` (animation
        start/end), ``current`` (single current frame). ``custom`` never
        reaches here -- the shared :meth:`resolve_frame_range` answers it.
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
    def sequence_name() -> str:
        """Basename of the current scene without extension; ``"playblast"``
        for an unsaved scene. ``EnvUtils.saved_scene_path`` owns the
        phantom-``untitled`` rule."""
        from mayatk.env_utils._env_utils import EnvUtils  # deferred: avoid import cycle

        scene = EnvUtils.saved_scene_path()
        if scene:
            return os.path.basename(scene).rsplit(".", 1)[0]
        return "playblast"

    @staticmethod
    def sequence_fps() -> float:
        """The scene frame rate as a float."""
        return ptk.VidUtils.get_frame_rate(cmds.currentUnit(q=True, time=True))

    @staticmethod
    def sound_source() -> Optional[str]:
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

    def audio_source(self) -> Tuple[Optional[str], float]:
        """``(audio filepath, offset in frames)`` of the timeline's armed sound.

        The rebase onto the capture's first frame is the shared core's -- this
        answers only the part that needs Maya. A node whose file is gone is a
        warning on Maya's warning line, and no audio.
        """
        node = self.sound_source()
        if not node:
            return None, 0.0
        try:
            filepath = cmds.getAttr(f"{node}.filename")
            offset_frames = cmds.getAttr(f"{node}.offset") or 0.0
        except Exception:
            return None, 0.0
        if not filepath or not os.path.isfile(filepath):
            self.warn(f"Audio file for {node!r} not found; skipping audio.")
            return None, 0.0
        return filepath, float(offset_frames)

    @staticmethod
    def notify(message: str) -> bool:
        """Maya's message line (none for mayapy without Maya's libraries)."""
        if om is None:
            return False
        om.MGlobal.displayInfo(message)
        return True

    @staticmethod
    def warn(message: str) -> bool:
        """Maya's warning line."""
        if cmds is None:
            return False
        cmds.warning(message)
        return True

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------
    def capture_frames(
        self,
        exporter: ptk.SequenceExporter,
        directory: str,
        prefix: str,
        start: int,
        end: int,
        camera: Optional[str],
        image_format: str,
        padding: int,
        overrides: Dict[str, Any],
    ) -> None:
        """A viewport playblast of ``start..end`` to ``<prefix>.<frame>.<ext>``.

        Raises:
            ValueError: *overrides* spell the padding ``framePadding``
                (cmds.playblast's): the exporter's is ``frame_padding``.
        """
        if "framePadding" in overrides:
            raise ValueError(
                "An image sequence's digits are the exporter's frame_padding "
                "(shared with Blender), not cmds.playblast's framePadding."
            )
        kwargs = self._playblast_kwargs(exporter, overrides)
        kwargs.update(format="image", compression=image_format, framePadding=padding)

        with self._camera_view(exporter, camera) as panel:
            if panel:
                kwargs.setdefault("editorPanelName", panel)
            exporter.logger.debug(
                f"capture_sequence {prefix} [{start}-{end}] -> {directory} ({kwargs})"
            )
            cmds.playblast(
                filename=os.path.join(directory, prefix),
                startTime=start,
                endTime=end,
                **kwargs,
            )

    def capture_still(
        self,
        exporter: ptk.SequenceExporter,
        filepath: str,
        frame: int,
        camera: Optional[str],
        image_format: str,
        overrides: Dict[str, Any],
    ) -> None:
        """A one-frame playblast to exactly *filepath*."""
        kwargs = self._playblast_kwargs(exporter, overrides)
        kwargs.update(format="image", compression=image_format)
        kwargs.pop("clearCache", None)  # incompatible with single-frame capture

        with self._camera_view(exporter, camera) as panel:
            if panel:
                kwargs.setdefault("editorPanelName", panel)
            cmds.playblast(frame=[frame], completeFilename=filepath, **kwargs)

    def capture_movie(
        self,
        exporter: ptk.SequenceExporter,
        filepath: str,
        fmt: str,
        compression: str,
        start: int,
        end: int,
        camera: Optional[str],
        sound: Optional[str],
        overrides: Dict[str, Any],
    ) -> str:
        """Maya's native movie playblast (``avi``/``movie``).

        The filepath extension is enforced against the format; a missing
        extension is appended. A format this Maya's playblast does not offer
        (AVI is Windows-only: Linux Maya has none) raises before capturing.
        """
        extension = self.NATIVE_EXTENSIONS.get(fmt.lower())
        if not extension:
            raise ValueError(
                f"Unknown native movie format {fmt!r}; expected one of "
                f"{sorted(self.NATIVE_EXTENSIONS)}."
            )
        base, ext = os.path.splitext(filepath)
        if not ext:
            filepath = base + extension
        elif ext.lower() != extension:
            raise ValueError(
                f"Extension {ext!r} does not match playblast format {fmt!r} "
                f"({extension})."
            )
        offered = cmds.playblast(query=True, format=True) or []
        if offered and fmt.lower() not in [str(f).lower() for f in offered]:
            raise RuntimeError(
                f"This Maya cannot playblast {fmt!r} (it offers {sorted(offered)}): "
                "export an image sequence or an encoded movie instead."
            )

        kwargs = self._playblast_kwargs(exporter, overrides)
        kwargs.update(format=fmt, compression=compression)
        if sound:
            kwargs.setdefault("sound", sound)

        with self._camera_view(exporter, camera) as panel:
            if panel:
                kwargs.setdefault("editorPanelName", panel)
            exporter.logger.debug(
                f"capture_movie [{start}-{end}] -> {filepath} ({kwargs})"
            )
            result = cmds.playblast(
                filename=filepath, startTime=start, endTime=end, **kwargs
            )
        return ptk.format_path(str(result)) if result else filepath

    def export_target(
        self,
        exporter: ptk.SequenceExporter,
        spec: ExportTarget,
        output_dir: str,
        name: str,
        start: int,
        end: int,
        camera: Optional[str],
        sound: Optional[str],
        overrides: Dict[str, Any],
    ) -> Union[str, List[str]]:
        """The ``arnold`` kind: it renders rather than reads the viewport, so
        it cannot come off the shared capture -- and the shared plan must not
        learn its name to run it."""
        if spec.kind == "arnold":
            return self.render_with_arnold(
                exporter,
                output_dir=os.path.join(output_dir, f"{name}_arnold"),
                start=start,
                end=end,
                camera=camera,
                prefix=name,
            )
        return super().export_target(
            exporter, spec, output_dir, name, start, end, camera, sound, overrides
        )

    # ------------------------------------------------------------------
    # Arnold
    # ------------------------------------------------------------------
    def render_with_arnold(
        self,
        exporter: ptk.SequenceExporter,
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

        The scene is first readied to render what the viewport shows
        (:meth:`RenderUtils.prepare_arnold_preview`: game shaders bridged,
        viewport backface culling mirrored); that setup is Arnold-only and
        stays. The render is mtoa's batch render (never the Render View) of the
        current render layer through *camera*, at the exporter's resolution,
        with the range, naming and image folder set for it; every setting it
        touches is put back afterward.

        Raises:
            ValueError: No camera resolves, the range runs backwards, or
                *render_layer* names a layer other than the current one
                (mtoa's render has no layer flag) -- each before the scene is
                touched.
            Plugins.LoadError: mtoa is not installed.
            RuntimeError: Arnold wrote no frame (its log in the Script Editor
                says why).
        """
        prefix = prefix or exporter.sequence_name()
        start, end = exporter.resolve_frame_range("playback", start, end)
        padding = int(
            frame_padding if frame_padding is not None else exporter.frame_padding
        )
        current_layer = cmds.editRenderLayerGlobals(
            query=True, currentRenderLayer=True
        )
        if render_layer and render_layer != current_layer:
            raise ValueError(
                f"Arnold renders the current render layer ({current_layer!r}); "
                f"make {render_layer!r} current first."
            )
        camera_shape = self._resolve_camera_shape(camera or exporter.camera)
        if not camera_shape:
            raise ValueError("Could not resolve a valid camera for Arnold rendering.")

        # mtoa, and the render options a never-rendered scene lacks.
        RenderUtils.ensure_arnold_options()
        output_dir = ptk.format_path(os.path.abspath(output_dir))
        os.makedirs(output_dir, exist_ok=True)
        RenderUtils.prepare_arnold_preview()  # only once the render will run

        extension = self._arnold_extension()
        preexisting = self._snapshot_files(output_dir, prefix, extension)

        width, height = int(exporter.width), int(exporter.height)
        # Every plug the render reads, set for it and put back after:
        # - the name, the range, every frame of it, none skipped as existing;
        # - the padding: ``extensionPadding`` (Maya has no ``framePadding``);
        # - ``name.#.ext``: with animation on, Maya's default names a frame
        #   ``name.ext.#``, which no image-sequence reader takes for a frame
        #   (nor :meth:`_snapshot_files`);
        # - the exporter's resolution, square pixels: a batch render reads the
        #   scene's (arnoldRender's own width/height flags do not reach it).
        plugs: Dict[str, Any] = {
            "defaultRenderGlobals.imageFilePrefix": prefix,
            "defaultRenderGlobals.animation": True,
            "defaultRenderGlobals.startFrame": start,
            "defaultRenderGlobals.endFrame": end,
            "defaultRenderGlobals.byFrameStep": 1,
            "defaultRenderGlobals.skipExistingFrames": False,
            "defaultRenderGlobals.extensionPadding": padding,
            "defaultRenderGlobals.outFormatControl": 0,  # the extension is written
            "defaultRenderGlobals.putFrameBeforeExt": True,
            "defaultRenderGlobals.periodInExt": 1,  # dot-separated
            "defaultResolution.width": width,
            "defaultResolution.height": height,
            "defaultResolution.deviceAspectRatio": width / float(height),
            "defaultResolution.pixelAspect": 1.0,
        }
        if cmds.objExists(self._ABORT_ON_LICENSE_FAIL):
            plugs[self._ABORT_ON_LICENSE_FAIL] = False
        saved = {plug: cmds.getAttr(plug) for plug in plugs}
        old_workspace_images = cmds.workspace(fileRuleEntry="images")

        try:
            cmds.workspace(fileRule=("images", output_dir))
            for plug, value in plugs.items():
                self._set_plug(plug, value)
            # Batch, flagged as mtoa's own batch render is: the globals' range
            # written under the images rule, no Render View. arnoldRender has
            # no start/end/layer flags (it ignores unknown ones), and its
            # interactive ``seq`` renders into the Render View's tmp/ folder.
            flags = {"batch": True, "camera": camera_shape, "width": width}
            flags["height"] = height
            flags.update(kwargs)  # a caller's own arnoldRender flags win
            cmds.arnoldRender(**flags)
        finally:
            cmds.workspace(fileRule=("images", old_workspace_images or "images"))
            for plug, value in saved.items():
                self._set_plug(plug, value)

        # Only files this run created or rewrote -- a shared output dir can
        # hold frames from earlier renders with the same prefix.
        rendered = [
            path
            for path, mtime in self._snapshot_files(
                output_dir, prefix, extension
            ).items()
            if path not in preexisting or preexisting[path] != mtime
        ]
        rendered.sort()
        if not rendered:
            raise RuntimeError(
                f"Arnold wrote no frames to {output_dir!r}: the render failed or "
                "was aborted, and its log in the Script Editor says why."
            )
        exporter._notify(
            f"Arnold render completed: {len(rendered)} frame(s) written to {output_dir}"
        )
        return rendered

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _playblast_kwargs(
        exporter: ptk.SequenceExporter, overrides: Dict[str, Any]
    ) -> Dict[str, Any]:
        """The exporter's defaults merged with per-call cmds.playblast overrides."""
        kwargs: Dict[str, Any] = {
            "forceOverwrite": True,
            "viewer": False,
            "offScreen": exporter.off_screen,
            "showOrnaments": exporter.show_ornaments,
            "percent": exporter.percent,
            "quality": exporter.quality,
            "widthHeight": (exporter.width, exporter.height),
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
    def _camera_view(self, exporter: ptk.SequenceExporter, camera: Optional[str]):
        """Temporarily aim the capture panel at ``camera``; yields the panel.

        Only the panel being captured is touched (never every model panel),
        and its original camera is restored on exit.
        """
        if camera and not cmds.objExists(camera):
            raise ValueError(f"Camera '{camera}' does not exist.")
        panel = self._find_capture_panel()
        if not camera or not panel:
            if camera:  # batch/headless: no panel to retarget
                exporter.logger.warning(
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
    @staticmethod
    def _set_plug(plug: str, value: Any) -> None:
        """Set *plug*. The image prefix is a string, and getAttr reads an unset
        one as None, which setAttr rejects -- and this runs in a finally."""
        if plug.endswith(".imageFilePrefix"):
            cmds.setAttr(plug, value or "", type="string")
        else:
            cmds.setAttr(plug, value)

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


class PlayblastExporter(ptk.SequenceExporter):
    """Viewport capture and preview-render exports.

    :class:`pythontk.SequenceExporter` bound to :class:`MayaPlayblastHost`:
    the two output kinds only Maya has (its native movie playblast and an
    Arnold render) in its registry, the timeline's range modes, and Maya's own
    public calls. Mirror of blendertk's ``PlayblastExporter``.

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

    HOST = MayaPlayblastHost()

    #: Registry of exportable outputs: the shared set, plus the two kinds only
    #: Maya can produce. Appended rather than re-declared, so a target added to
    #: the base reaches this picker without being written twice -- and the
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

    #: Native playblast format -> file extension (the host's).
    NATIVE_EXTENSIONS: Dict[str, str] = MayaPlayblastHost.NATIVE_EXTENSIONS

    #: Frame-range modes accepted by :meth:`resolve_frame_range` -- the shared
    #: ``custom`` plus the three the scene's timeline answers.
    RANGE_MODES: Tuple[str, ...] = ("playback", "animation", "current", "custom")

    #: ``export`` keys the Maya targets own, on top of the shared set: the
    #: native movie playblast is handed each of these explicitly, so a caller
    #: repeating one in ``**overrides`` would reach ``cmds.playblast`` beside
    #: (or over) the plan's own.
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

    @classmethod
    def resolve_sound_node(cls) -> Optional[str]:
        """The timeline's active audio node, or the scene's sole audio node
        (:meth:`MayaPlayblastHost.sound_source`); None when ambiguous."""
        return cls.HOST.sound_source()

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
        extension is appended. A format this Maya's playblast does not offer
        (AVI is Windows-only: Linux Maya has none) raises before capturing.
        """
        return self._capture_movie(
            filepath, fmt, compression, start, end, camera, sound, overrides
        )

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
        """Render a frame range with Arnold; returns the files this run wrote
        (:meth:`MayaPlayblastHost.render_with_arnold`)."""
        return self.HOST.render_with_arnold(
            self,
            output_dir,
            start=start,
            end=end,
            camera=camera,
            prefix=prefix,
            frame_padding=frame_padding,
            render_layer=render_layer,
            **kwargs,
        )
