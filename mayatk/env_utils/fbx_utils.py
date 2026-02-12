# !/usr/bin/python
# coding=utf-8
import os
import logging
from typing import Optional, Dict, Any, List

try:
    import pymel.core as pm
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk

logger = logging.getLogger(__name__)


class FbxUtils(ptk.HelpMixin):
    """Low-level utilities for FBX export operations in Maya.

    This module owns the MEL-level FBX commands (plugin loading, preset
    application, option setting, and the ``cmds.file`` call).  Higher-level
    orchestration (task management, UI, logging to files) belongs in
    ``SceneExporter`` or calling code.
    """

    @staticmethod
    def load_plugin():
        """Ensure the fbxmaya plugin is loaded."""
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya")

    @staticmethod
    def set_fbx_options(options: Dict[str, Any]):
        """Apply FBX export options via MEL commands.

        Parameters:
            options: Mapping of FBX MEL command names to values.
                     Booleans/ints use the ``-v`` flag; strings are appended directly.
        """
        for option, value in options.items():
            if isinstance(value, bool):
                pm.mel.eval(f"{option} -v {'true' if value else 'false'}")
            elif isinstance(value, int):
                pm.mel.eval(f"{option} -v {value}")
            else:
                pm.mel.eval(f'{option} "{value}"')

    @staticmethod
    def load_preset(preset_path: str):
        """Load an FBX export preset file.

        Parameters:
            preset_path: Absolute path to the ``.fbxexportpreset`` file.

        Raises:
            FileNotFoundError: If *preset_path* does not exist.
            RuntimeError: If the MEL command fails.
        """
        if not os.path.isfile(preset_path):
            raise FileNotFoundError(f"FBX preset not found: {preset_path}")
        formatted = preset_path.replace("\\", "/")
        pm.mel.eval(f'FBXLoadExportPresetFile -f "{formatted}"')
        logger.info(f"Loaded FBX export preset: {formatted}")

    @classmethod
    def export(
        cls,
        file_path: str,
        objects: Optional[List] = None,
        preset_file: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        selection_only: bool = True,
    ) -> str:
        """Export geometry to an FBX file.

        Parameters:
            file_path: Destination ``.fbx`` path (directories are created automatically).
            objects: Nodes to export.  If *None*, the current selection is used.
            preset_file: Optional FBX export preset to load before exporting.
            options: Additional FBX MEL options applied *after* the preset.
            selection_only: If True export selected; if False export entire scene.

        Returns:
            The absolute path of the exported file.

        Raises:
            RuntimeError: On export failure.
        """
        cls.load_plugin()

        file_path = os.path.abspath(os.path.expandvars(file_path))
        if not file_path.lower().endswith(".fbx"):
            file_path += ".fbx"

        export_dir = os.path.dirname(file_path)
        os.makedirs(export_dir, exist_ok=True)

        if objects:
            names = [str(o) for o in objects]
            cmds.select(names, replace=True)

        if selection_only and not cmds.ls(selection=True):
            raise RuntimeError(
                "Export requested for selection, but nothing is selected."
            )

        if preset_file:
            cls.load_preset(preset_file)

        if options:
            cls.set_fbx_options(options)

        kwargs = {"force": True, "options": "v=0;", "type": "FBX export"}
        if selection_only:
            kwargs["exportSelected"] = True
        else:
            kwargs["exportAll"] = True

        cmds.file(file_path, **kwargs)
        logger.info(f"Exported FBX: {file_path}")
        return file_path
