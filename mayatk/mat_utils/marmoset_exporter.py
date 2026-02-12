# !/usr/bin/python
# coding=utf-8
import os
import json
import logging
import tempfile
from typing import List, Optional, Dict, Any

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk
from pythontk.core_utils.app_launcher import AppLauncher

from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.mat_utils.mat_manifest import MatManifest

logger = logging.getLogger(__name__)

# Default FBX options tuned for Marmoset Toolbag
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "FBXExportSmoothingGroups": True,
    "FBXExportTangents": True,
    "FBXExportTriangulate": False,
    "FBXExportEmbeddedTextures": False,
    "FBXExportSkins": False,
    "FBXExportCameras": False,
    "FBXExportLights": False,
    "FBXExportAnimationOnly": False,
    "FBXExportBakeComplexAnimation": False,
}

# Template lives next to this module
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_TEMPLATE_PATH = os.path.join(_TEMPLATE_DIR, "marmoset_import.py")


class MarmosetExporter(ptk.LoggingMixin):
    """Export Maya selection to Marmoset Toolbag with automatic material setup.

    Usage::

        MarmosetExporter().send(
            objects=cmds.ls(sl=True),
            toolbag_exe=r"C:/Program Files/Marmoset/Toolbag 4/toolbag.exe",
        )
    """

    def send(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        toolbag_exe: Optional[str] = None,
        fbx_options: Optional[Dict[str, Any]] = None,
        preset_file: Optional[str] = None,
        headless: bool = False,
    ) -> Optional[str]:
        """Export objects and launch Toolbag.

        Parameters:
            objects: Nodes to export.  Defaults to current selection.
            output_dir: Directory for the FBX/JSON/script artefacts.
                        Defaults to ``<temp>/maya_marmoset_bridge``.
            output_name: Base filename (without extension).
                         Defaults to the Maya scene name or ``"untitled"``.
            toolbag_exe: Explicit path to ``toolbag.exe``.
                         If *None*, ``AppLauncher`` searches PATH / registry.
            fbx_options: Additional FBX MEL overrides merged on top of defaults.
            preset_file: Optional FBX export preset path.
            headless: If True, Toolbag will save the scene and quit automatically.

        Returns:
            The path to the generated Toolbag script, or *None* on failure.
        """
        # -- Resolve objects ------------------------------------------------
        if not objects:
            objects = cmds.ls(selection=True, long=True)
        if not objects:
            self.logger.warning("Nothing selected to export.")
            return None

        # -- Paths ----------------------------------------------------------
        if not output_dir:
            output_dir = os.path.join(tempfile.gettempdir(), "maya_marmoset_bridge")
        os.makedirs(output_dir, exist_ok=True)

        base = output_name or self._scene_base_name()
        fbx_path = os.path.join(output_dir, f"{base}.fbx")
        manifest_path = os.path.join(output_dir, f"{base}.materials.json")
        script_path = os.path.join(output_dir, f"{base}_load.py")

        # -- Export FBX -----------------------------------------------------
        merged_options = dict(_DEFAULT_FBX_OPTIONS)
        if fbx_options:
            merged_options.update(fbx_options)

        self.logger.info("Exporting FBX …")
        try:
            FbxUtils.export(
                file_path=fbx_path,
                objects=objects,
                preset_file=preset_file,
                options=merged_options,
                selection_only=True,
            )
        except Exception as e:
            self.logger.error(f"FBX export failed: {e}")
            return None

        # -- Build manifest -------------------------------------------------
        self.logger.info("Building material manifest …")
        manifest = MatManifest.build(objects)

        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        self.logger.info(f"Manifest written: {manifest_path}")

        # -- Generate Toolbag script ----------------------------------------
        if not os.path.isfile(_TEMPLATE_PATH):
            self.logger.error(f"Template missing: {_TEMPLATE_PATH}")
            return None

        with open(_TEMPLATE_PATH, "r", encoding="utf-8") as fh:
            script = fh.read()

        # Determine automation variables
        save_path = ""
        should_quit = False

        if headless:
            # When headless, automatically save next to the FBX and quit.
            # Example: "my_scene.tbscene"
            tb_scene_path = os.path.splitext(fbx_path)[0] + ".tbscene"
            save_path = tb_scene_path.replace("\\", "/")
            should_quit = True

        script = script.replace("__FBX_PATH__", fbx_path.replace("\\", "/"))
        script = script.replace("__MANIFEST_PATH__", manifest_path.replace("\\", "/"))
        script = script.replace("__SAVE_PATH__", save_path)
        script = script.replace("__SHOULD_QUIT__", str(should_quit))

        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(script)
        self.logger.info(f"Toolbag script written: {script_path}")

        # -- Launch Toolbag ------------------------------------------------
        self.logger.info("Launching Marmoset Toolbag …")
        proc = self._launch_toolbag(script_path, toolbag_exe)
        if proc:
            self.logger.info("Toolbag launched successfully.")
        else:
            self.logger.error(
                "Could not launch Marmoset Toolbag. "
                "Pass toolbag_exe= or add toolbag to your PATH."
            )
        return script_path

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _scene_base_name() -> str:
        """Return the current scene's base name (no extension), or ``'untitled'``."""
        scene = cmds.file(query=True, sceneName=True)
        if scene:
            return os.path.splitext(os.path.basename(scene))[0]
        return "untitled"

    @staticmethod
    def _launch_toolbag(script_path: str, exe: Optional[str] = None):
        """Attempt to launch Toolbag with the ``-run`` flag.

        Tries, in order:
        1. Explicit *exe* path.
        2. ``"toolbag"`` via AppLauncher (PATH / registry).
        3. ``"Marmoset Toolbag 4"`` via AppLauncher.

        Returns:
            The ``subprocess.Popen`` object or *None*.
        """
        candidates = []
        if exe:
            candidates.append(exe)
        candidates.extend(["toolbag", "Marmoset Toolbag 4"])

        for name in candidates:
            proc = AppLauncher.launch(name, args=["-run", script_path])
            if proc:
                return proc
        return None
