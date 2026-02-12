# !/usr/bin/python
# coding=utf-8
import os
from typing import Dict, List, Optional
import pythontk as ptk

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError:
    pass

from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.env_utils._env_utils import EnvUtils


class FadeMaterialMode(ptk.LoggingMixin):
    """
    Implements the 'material' mode for MaterialFade.

    This mode keys native StingrayPBS attributes (base_color, opacity) directly.
    All objects sharing that material will fade together.
    """

    FADE_ATTRS = ("base_colorR", "base_colorG", "base_colorB", "opacity")
    """StingrayPBS attributes keyframed in ``"material"`` mode."""

    FADE_SUFFIX = "_Fade"
    """Suffix appended to material names in ``"material"`` mode."""

    @classmethod
    def get_stingray_mats(cls, objects: Optional[List] = None) -> List:
        """Return unique StingrayPBS materials assigned to *objects*."""
        return MatUtils.get_mats(objects, mat_type="StingrayPBS")

    @classmethod
    def ensure_transparent_graph(cls, mat) -> bool:
        """Load Standard_Transparent.sfx onto a StingrayPBS node if needed."""
        if mat.hasAttr("use_opacity_map"):
            return True

        EnvUtils.load_plugin("shaderFXPlugin")
        maya_path = EnvUtils.get_env_info("install_path")
        graph = os.path.join(
            maya_path,
            "presets",
            "ShaderFX",
            "Scenes",
            "StingrayPBS",
            "Standard_Transparent.sfx",
        )
        if os.path.exists(graph):
            cmds.shaderfx(sfxnode=mat.name(), loadGraph=graph)
            cls.logger.info(f"Loaded Standard_Transparent.sfx onto {mat.name()}")
            return True
        else:
            cls.logger.warning(f"Transparent graph not found: {graph}")
            return False

    @classmethod
    def setup(
        cls, objects, start_frame, end_frame, val_start, val_end
    ) -> Dict[str, Dict]:
        """Key StingrayPBS material attributes directly."""
        materials = cls.get_stingray_mats(objects)
        if not materials:
            cls.logger.warning("No StingrayPBS materials found on selection.")
            return {}

        results = {}

        for mat in materials:
            mat_name = mat.name()
            cls.logger.info(f"Processing: {mat_name}")
            keyed = []

            # -- 1. Key base_color toward black (Opaque fallback) --
            if mat.hasAttr("base_color"):
                orig = mat.base_color.get()
                start_data = {
                    "base_colorR": orig[0] * val_start,
                    "base_colorG": orig[1] * val_start,
                    "base_colorB": orig[2] * val_start,
                }
                end_data = {
                    "base_colorR": orig[0] * val_end,
                    "base_colorG": orig[1] * val_end,
                    "base_colorB": orig[2] * val_end,
                }
                AnimUtils.set_keys_for_attributes(
                    [mat], target_times=[start_frame], **start_data
                )
                AnimUtils.set_keys_for_attributes(
                    [mat], target_times=[end_frame], **end_data
                )
                keyed.extend(f"{mat_name}.{ch}" for ch in start_data)
                cls.logger.info(
                    f"  Keyed base_color RGB: {orig} over {start_frame}-{end_frame}"
                )
            else:
                cls.logger.warning(
                    f"  {mat_name} has no 'base_color' attr -- skipping color fade."
                )

            # -- 2. Load transparent graph and key opacity --
            if cls.ensure_transparent_graph(mat):
                mat.use_opacity_map.set(0)  # Use float, not texture

                if mat.hasAttr("opacity"):
                    AnimUtils.set_keys_for_attributes(
                        [mat], target_times=[start_frame], opacity=val_start
                    )
                    AnimUtils.set_keys_for_attributes(
                        [mat], target_times=[end_frame], opacity=val_end
                    )
                    keyed.append(f"{mat_name}.opacity")
                    cls.logger.info(f"  Keyed opacity: {val_start} -> {val_end}")
                else:
                    cls.logger.warning(
                        f"  {mat_name} has no 'opacity' attr after loading transparent graph."
                    )

            # -- 3. Rename with _Fade suffix for Unity auto-detection --
            if not mat_name.endswith(cls.FADE_SUFFIX):
                new_name = f"{mat_name}{cls.FADE_SUFFIX}"
                mat.rename(new_name)
                cls.logger.info(f"  Renamed: {mat_name} -> {new_name}")

            results[mat.name()] = {"attrs_keyed": keyed}

        return results

    @classmethod
    def bake(cls, objects, frame_range, sample_by, optimize):
        materials = cls.get_stingray_mats(objects)
        if not materials:
            cls.logger.warning("No StingrayPBS materials found.")
            return

        baked = AnimUtils.bake(
            materials,
            attributes=list(cls.FADE_ATTRS),
            time_range=(frame_range[0], frame_range[1]),
            sample_by=sample_by,
            preserve_outside_keys=True,
            simulation=False,
            only_keyed=True,
        )

        for curve in baked:
            cls.logger.info(f"Baked material curve: {curve}")

        if optimize and baked:
            AnimUtils.optimize_keys(materials)

    @classmethod
    def remove(cls, objects):
        materials = cls.get_stingray_mats(objects)
        for mat in materials:
            mat_name = mat.name()

            # Delete anim curves on fade attrs
            for attr_name in cls.FADE_ATTRS:
                if mat.hasAttr(attr_name):
                    curves = pm.listConnections(mat.attr(attr_name), type="animCurve")
                    if curves:
                        pm.delete(curves)

            # Restore defaults
            if mat.hasAttr("base_color"):
                mat.base_color.set(1, 1, 1)
            if mat.hasAttr("opacity"):
                mat.opacity.set(1)

            # Strip _Fade suffix
            if mat_name.endswith(cls.FADE_SUFFIX):
                restored = mat_name[: -len(cls.FADE_SUFFIX)]
                mat.rename(restored)
                cls.logger.info(f"Restored: {mat_name} -> {restored}")
