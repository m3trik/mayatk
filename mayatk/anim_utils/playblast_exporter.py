# !/usr/bin/python
# coding=utf-8
"""Utilities for creating playblasts and alternative preview renders in Maya."""
from __future__ import annotations

import os
import glob
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya environment only
    print(__file__, error)

import pythontk as ptk


@dataclass
class PlayblastExporter:
    """High-level helper for producing playblast outputs and Arnold previews.

    Parameters:
        start_frame: Optional override for the playblast start frame. Defaults to the
            current playback minimum when omitted.
        end_frame: Optional override for the playblast end frame. Defaults to the
            current playback maximum when omitted.
        camera_name: Optional camera to use for playblast operations. If omitted,
            playblasts will respect Maya's current viewport camera.
    """

    start_frame: Optional[int] = None
    end_frame: Optional[int] = None
    camera_name: Optional[str] = None
    _scene_name: Optional[str] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        playback_min = pm.playbackOptions(q=True, minTime=True)
        playback_max = pm.playbackOptions(q=True, maxTime=True)

        self.start_frame = (
            int(self.start_frame) if self.start_frame is not None else int(playback_min)
        )
        self.end_frame = (
            int(self.end_frame) if self.end_frame is not None else int(playback_max)
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def scene_name(self) -> str:
        if self._scene_name is None:
            scene = pm.sceneName()
            if scene:
                self._scene_name = os.path.basename(scene).rsplit(".", 1)[0]
            else:
                self._scene_name = "playblast"
        return self._scene_name

    @staticmethod
    def _get_scene_fps() -> float:
        """Get the current scene frames per second."""
        unit = pm.currentUnit(q=True, time=True)
        return ptk.VidUtils.get_frame_rate(unit)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def create_playblast(
        self,
        filepath: Optional[str] = None,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None,
        camera_name: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Create a playblast using Maya's viewport capture."""

        # Validate camera if provided
        if camera_name and not pm.objExists(camera_name):
            raise ValueError(f"Camera '{camera_name}' does not exist.")

        user_kwargs = dict(kwargs)
        playblast_format = user_kwargs.get("format", "avi")
        format_lower = (
            playblast_format.lower() if isinstance(playblast_format, str) else "avi"
        )

        compression_value = user_kwargs.get("compression")
        compression_lower = (
            compression_value.lower() if isinstance(compression_value, str) else None
        )

        extension_map = {
            "avi": ".avi",
            "movie": ".mov",
            "qt": ".mov",
            "qtmovie": ".mov",
            "qt_movie": ".mov",
            "iff": ".iff",
            "tga": ".tga",
            "jpg": ".jpg",
            "jpeg": ".jpg",
            "png": ".png",
            "gif": ".gif",
        }

        if format_lower == "image" and compression_lower:
            target_extension = extension_map.get(compression_lower)
        else:
            target_extension = extension_map.get(format_lower)

        filepath = ptk.format_path(filepath) if filepath else None
        resolved_filepath = self._resolve_filepath(
            filepath, format_lower, target_extension
        )

        output_dir = os.path.dirname(resolved_filepath)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        camera_override = camera_name if camera_name is not None else self.camera_name
        camera_panels, original_cameras = self._override_model_panels(camera_override)

        start = int(start_frame) if start_frame is not None else self.start_frame
        end = int(end_frame) if end_frame is not None else self.end_frame

        playblast_params = self._default_playblast_kwargs()
        playblast_params.update(user_kwargs)
        playblast_params.pop("filename", None)

        print(
            "[PlayblastExporter] Invoking pm.playblast with:",
            {
                "filepath": resolved_filepath,
                "start": start,
                "end": end,
                "format": playblast_params.get("format"),
                "compression": playblast_params.get("compression"),
            },
        )

        try:
            playblast_result = pm.playblast(
                filename=resolved_filepath,
                startTime=start,
                endTime=end,
                **playblast_params,
            )
        finally:
            self._restore_model_panels(camera_panels, original_cameras)

        generated_paths = (
            [ptk.format_path(str(path)) for path in playblast_result]
            if isinstance(playblast_result, (list, tuple, set))
            else [ptk.format_path(str(playblast_result))]
        )

        valid_path = next(
            (path for path in generated_paths if self._is_valid_file(path)), None
        )

        if format_lower == "image":
            sequence_dir = (
                os.path.dirname(valid_path)
                if valid_path
                else os.path.dirname(resolved_filepath)
            )
            pm.displayInfo(f"Playblast image sequence created under: {sequence_dir}")
            return ptk.format_path(sequence_dir)

        if not valid_path:
            raise RuntimeError(
                "Playblast failed; Maya did not report a valid output file."
            )

        pm.displayInfo(f"Playblast video created at: {valid_path}")
        return valid_path

    def render_with_arnold(
        self,
        output_dir: str,
        start_frame: Optional[int] = None,
        end_frame: Optional[int] = None,
        camera_name: Optional[str] = None,
        prefix: Optional[str] = None,
        frame_padding: int = 4,
        render_layer: Optional[str] = None,
        **kwargs: Any,
    ) -> List[str]:
        """Render a frame range using Arnold."""

        output_dir = ptk.format_path(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        output_dir = os.path.abspath(output_dir)

        try:
            if not pm.pluginInfo("mtoa", query=True, loaded=True):
                pm.loadPlugin("mtoa")
        except Exception:  # pragma: no cover - Maya dependency
            pass

        camera_override = camera_name if camera_name is not None else self.camera_name
        camera_shape = self._resolve_camera_shape(camera_override)
        if not camera_shape:
            raise ValueError("Could not resolve a valid camera for Arnold rendering.")

        prefix = prefix or self.scene_name
        start = int(start_frame) if start_frame is not None else self.start_frame
        end = int(end_frame) if end_frame is not None else self.end_frame
        layer = render_layer or pm.editRenderLayerGlobals(
            query=True, currentRenderLayer=True
        )

        old_workspace_images = pm.workspace(fileRuleEntry="images")
        old_animation = pm.getAttr("defaultRenderGlobals.animation")
        old_start = pm.getAttr("defaultRenderGlobals.startFrame")
        old_end = pm.getAttr("defaultRenderGlobals.endFrame")
        old_padding = pm.getAttr("defaultRenderGlobals.framePadding")
        old_prefix = pm.getAttr("defaultRenderGlobals.imageFilePrefix")

        try:
            pm.workspace(fileRule=("images", output_dir))
            pm.setAttr("defaultRenderGlobals.imageFilePrefix", prefix, type="string")
            pm.setAttr("defaultRenderGlobals.animation", True)
            pm.setAttr("defaultRenderGlobals.startFrame", start)
            pm.setAttr("defaultRenderGlobals.endFrame", end)
            pm.setAttr("defaultRenderGlobals.framePadding", frame_padding)

            pm.arnoldRender(
                seq=True,
                startFrame=start,
                endFrame=end,
                camera=camera_shape,
                layer=layer,
                **kwargs,
            )
        finally:
            pm.workspace(fileRule=("images", old_workspace_images or "images"))
            pm.setAttr(
                "defaultRenderGlobals.imageFilePrefix", old_prefix, type="string"
            )
            pm.setAttr("defaultRenderGlobals.animation", old_animation)
            pm.setAttr("defaultRenderGlobals.startFrame", old_start)
            pm.setAttr("defaultRenderGlobals.endFrame", old_end)
            pm.setAttr("defaultRenderGlobals.framePadding", old_padding)

        driver = pm.PyNode("defaultArnoldDriver")
        extension = driver.ai_translator.get() or "exr"

        rendered_files: List[str] = []
        for root, _, files in os.walk(output_dir):
            for filename in files:
                if not filename.endswith(f".{extension}"):
                    continue
                if not filename.startswith(prefix):
                    continue
                full_path = os.path.join(root, filename)
                rendered_files.append(ptk.format_path(full_path))

        rendered_files.sort()

        pm.displayInfo(
            f"Arnold render completed: {len(rendered_files)} frame(s) written to {output_dir}"
        )
        return rendered_files

    def export_variations(
        self,
        output_path: str,
        base_kwargs: Optional[Dict[str, Any]] = None,
        scene_name: Optional[str] = None,
        variations: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Produce multiple playblast outputs (formats, sequences, Arnold)."""

        base_kwargs = dict(base_kwargs or self._default_playblast_kwargs())
        scene_name = scene_name or self.scene_name
        variation_specs = variations or self._default_variations()

        results: List[Dict[str, Any]] = []
        for variation in variation_specs:
            variation = dict(variation)
            label = variation["label"]
            summary: Dict[str, Any] = {"label": label}

            try:
                if variation.get("renderer") == "arnold":
                    target_dir = variation.get("target_dir", f"{output_path}_{label}")
                    frames = self.render_with_arnold(
                        output_dir=target_dir,
                        start_frame=self.start_frame,
                        end_frame=self.end_frame,
                        prefix=scene_name,
                        frame_padding=variation.get("framePadding", 4),
                    )
                    summary.update({"output": frames, "type": "arnold_sequence"})
                    print(
                        f"Arnold render ({label}) created {len(frames)} frame(s) at {target_dir}"
                    )
                    results.append(summary)
                    continue

                variant_base = f"{output_path}_{label}"
                target_path = variant_base
                if variation.get("make_directory"):
                    os.makedirs(variant_base, exist_ok=True)
                    target_path = os.path.join(variant_base, scene_name)

                playblast_kwargs = dict(base_kwargs)
                playblast_kwargs.update(variation["playblast"])

                playblast_output = self.create_playblast(
                    filepath=target_path,
                    start_frame=self.start_frame,
                    end_frame=self.end_frame,
                    camera_name=self.camera_name,
                    **playblast_kwargs,
                )

                summary.update(
                    {
                        "output": playblast_output,
                        "type": playblast_kwargs.get("format", "avi"),
                    }
                )

                if playblast_kwargs.get("format") == "image":
                    print(f"Image sequence ({label}) created under: {playblast_output}")
                else:
                    print(f"Playblast ({label}) created: {playblast_output}")

                if variation.get("post") == "mp4":
                    input_path = playblast_output
                    output_path_mp4 = None
                    fps = self._get_scene_fps()
                    is_sequence = playblast_kwargs.get("format") == "image"

                    if is_sequence:
                        ext = playblast_kwargs.get("compression", "png")
                        padding = playblast_kwargs.get("framePadding", 4)

                        # Determine the prefix used for the sequence
                        # If make_directory was used, the prefix is scene_name (as target_path was dir/scene_name)
                        # Otherwise, it's the basename of target_path (e.g. output_path_label)
                        prefix = os.path.basename(target_path)

                        # Construct pattern: path/to/dir/Prefix.%04d.ext
                        pattern = os.path.join(
                            playblast_output, f"{prefix}.%0{padding}d.{ext}"
                        )
                        input_path = pattern
                        # Output in the same directory
                        output_path_mp4 = os.path.join(
                            playblast_output, f"{prefix}.mp4"
                        )

                    mp4_path = ptk.compress_video(
                        input_filepath=input_path,
                        output_filepath=output_path_mp4,
                        frame_rate=fps,
                        delete_original=not is_sequence,  # Manual cleanup for sequences
                    )

                    if is_sequence and mp4_path and os.path.exists(mp4_path):
                        # Cleanup image sequence
                        search_pattern = input_path.replace(f"%0{padding}d", "*")
                        for f in glob.glob(search_pattern):
                            try:
                                os.remove(f)
                            except OSError:
                                pass

                    if mp4_path:
                        summary["compressed"] = mp4_path
                        print(f"Compressed MP4 created for {label}: {mp4_path}")

            except Exception as exc:  # noqa: BLE001
                summary["error"] = str(exc)
                pm.warning(f"Playblast variant '{label}' failed: {exc}")

            results.append(summary)

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _default_variations() -> List[Dict[str, Any]]:
        variations: List[Dict[str, Any]] = [
            {
                "label": "video",
                "playblast": {
                    "format": "image",
                    "compression": "png",
                    "framePadding": 4,
                    "offScreen": True,
                },
                "post": "mp4",
                "make_directory": True,
            },
        ]

        if pm.optionVar.get("tentacleEnablePlayblastSequence", 0):
            variations.append(
                {
                    "label": "png_sequence",
                    "playblast": {
                        "format": "image",
                        "compression": "png",
                        "offScreen": False,
                        "framePadding": 4,
                    },
                    "make_directory": True,
                }
            )

        if pm.optionVar.get("tentacleEnableArnoldPlayblast", 0):
            variations.append(
                {
                    "label": "arnold_sequence",
                    "renderer": "arnold",
                    "framePadding": 4,
                }
            )
        return variations

    @staticmethod
    def _default_playblast_kwargs() -> Dict[str, Any]:
        return {
            "format": "avi",
            "compression": "none",
            "forceOverwrite": True,
            "viewer": False,
            "widthHeight": (1920, 1080),
            "quality": 100,
            "showOrnaments": True,
            "percent": 100,
            "clearCache": True,
            "offScreen": True,
        }

    @staticmethod
    def _is_valid_file(path: str) -> bool:
        return os.path.exists(path) and os.path.getsize(path) > 0

    def _resolve_filepath(
        self,
        filepath: Optional[str],
        playblast_format: str,
        target_extension: Optional[str],
    ) -> str:
        scene_name = self.scene_name

        if not filepath or os.path.isdir(filepath):
            if playblast_format == "image":
                return ptk.format_path(os.path.join(filepath or "", scene_name))
            extension = target_extension or ""
            return ptk.format_path(
                os.path.join(filepath or "", f"{scene_name}{extension}")
            )

        base, ext = os.path.splitext(filepath)
        if playblast_format == "image":
            if ext and target_extension and ext.lower() != target_extension:
                raise ValueError(
                    f"File extension '{ext}' does not match image compression '{target_extension}'."
                )
            return ptk.format_path(filepath)

        if ext:
            if target_extension and ext.lower() != target_extension:
                raise ValueError(
                    f"File extension '{ext}' does not match playblast format '{playblast_format}'."
                )
            return ptk.format_path(filepath)

        if not target_extension:
            raise ValueError(
                "Filepath must include an extension when using this playblast format."
            )

        return ptk.format_path(f"{base}{target_extension}")

    def _override_model_panels(self, camera_name: Optional[str]):
        if not camera_name:
            return [], {}

        panels = pm.getPanel(type="modelPanel")
        original = {
            panel: pm.modelEditor(panel, q=True, camera=True) for panel in panels
        }
        for panel in panels:
            if pm.control(panel, exists=True):
                pm.modelEditor(panel, e=True, camera=camera_name)
        return panels, original

    @staticmethod
    def _restore_model_panels(
        panels: List[str], original_cameras: Dict[str, str]
    ) -> None:
        for panel in panels:
            if pm.control(panel, exists=True):
                pm.modelEditor(panel, e=True, camera=original_cameras.get(panel))

    def _resolve_camera_shape(self, camera_name: Optional[str]) -> Optional[pm.nt.Camera]:  # type: ignore[name-defined]
        target_camera = camera_name or self._active_viewport_camera()
        if not target_camera:
            return None

        camera_nodes = pm.ls(target_camera, dag=True, type="camera")
        if camera_nodes:
            return camera_nodes[0]

        try:
            cam_node = pm.PyNode(target_camera)
            shapes = cam_node.getShapes()
            if shapes:
                return shapes[0]
        except pm.MayaNodeError:
            return None
        return None

    @staticmethod
    def _active_viewport_camera() -> Optional[str]:
        active_panel = pm.getPanel(withFocus=True)
        if active_panel and pm.getPanel(typeOf=active_panel) == "modelPanel":
            return pm.modelPanel(active_panel, query=True, camera=True)
        return pm.lookThru(query=True)
