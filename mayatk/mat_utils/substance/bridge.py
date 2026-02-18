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
from pythontk.str_utils._str_utils import StrUtils

from mayatk.env_utils.fbx_utils import FbxUtils

logger = logging.getLogger(__name__)

# Default FBX options tuned for Substance Painter
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "FBXExportSmoothingGroups": True,
    "FBXExportTangents": True,
    "FBXExportTriangulate": False,  # Painter handles triangulation usually, but safer off? 
    "FBXExportEmbeddedTextures": False,
    "FBXExportSkins": False,
    "FBXExportCameras": False,
    "FBXExportLights": False,
    "FBXExportAnimationOnly": False,
    "FBXExportApplyConstantKeyReducer": False,
    "FBXExportBakeComplexAnimation": False,
    "FBXExportCacheFile": False,
    "FBXExportConstraints": False,
    "FBXExportConvertUnitString": "cm",
    "FBXExportEmbeddedTextures": False,
    "FBXExportFileVersion": "FBX202000",
    "FBXExportGenerateLog": False,
    "FBXExportHardEdges": False,
    "FBXExportInAscii": False,
    "FBXExportIncludeChildren": True,
    "FBXExportInputConnections": False,
    "FBXExportInstances": False,
    "FBXExportQuaternion": "euler",
    "FBXExportReferencedAssetsContent": False,
    "FBXExportScaleFactor": 1.0,
    "FBXExportShapes": False,
    "FBXExportSkins": False,
    "FBXExportSmoothMesh": False,
    "FBXExportSmoothingGroups": True,
    "FBXExportTangents": True,
    "FBXExportTriangulate": False,
    "FBXExportUpAxis": "y",
    "FBXExportUseSceneName": False,
}


class SubstanceBridge(ptk.LoggingMixin):
    """
    Bridge for sending assets from Maya to Adobe Substance 3D Painter.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def send(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        painter_exe: Optional[str] = None,
        fbx_options: Optional[Dict[str, Any]] = None,
        headless: bool = False,
        enable_remote: bool = True,
    ) -> Optional[str]:
        """Export objects and launch Substance Painter.

        Parameters:
            objects: Nodes to export. Defaults to current selection.
            output_dir: Directory for the FBX. Defaults to <temp>/maya_substance_bridge.
            output_name: Base filename (without extension). Defaults to the Maya scene name or "untitled".
            painter_exe: Explicit path to 'Adobe Substance 3D Painter.exe'. If None, AppLauncher searches.
            fbx_options: Override default FBX export options.
            headless: If True, launches Painter but keeps window minimized/hidden if possible (Painter has no true headless mode).
            enable_remote: If True, enables remote scripting API (JSON-RPC) on launch.

        Returns:
            The path to the exported FBX file if successful, else None.
        """
        if output_dir is None:
            output_dir = os.path.join(tempfile.gettempdir(), "maya_substance_bridge")

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        if output_name is None:
            scene_name = cmds.file(q=True, sceneName=True, shortName=True)
            if scene_name:
                output_name = os.path.splitext(scene_name)[0]
            else:
                output_name = "untitled"

        base = StrUtils.cleanup_filename(output_name)
        fbx_path = os.path.join(output_dir, f"{base}.fbx")

        # -- Export FBX -----------------------------------------------------
        merged_options = dict(_DEFAULT_FBX_OPTIONS)
        if fbx_options:
            merged_options.update(fbx_options)

        self.logger.info("Exporting FBX to: %s", fbx_path)
        try:
            FbxUtils.export(
                file_path=fbx_path,
                objects=objects,
                options=merged_options,
                selection_only=True,
            )
        except Exception as e:
            self.logger.error(f"FBX export failed: {e}")
            return None

        # -- Launch Substance Painter ---------------------------------------
        self.logger.info("Launching Substance Painter …")
        proc = self._launch_painter(
            fbx_path, 
            exe=painter_exe, 
            enable_remote=enable_remote,
            headless=headless
        )
        
        if proc:
            self.logger.info("Substance Painter launched successfully.")
            return fbx_path
        else:
            self.logger.error(
                "Could not launch Substance Painter. "
                "Pass painter_exe= or ensure it is in your PATH / Registry."
            )
            return None

    @staticmethod
    def _launch_painter(
        mesh_path: str, 
        exe: Optional[str] = None, 
        enable_remote: bool = True,
        headless: bool = False
    ):
        """Attempt to launch Substance Painter with the mesh loaded.

        Tries, in order:
        1. Explicit *exe* path.
        2. "Adobe Substance 3D Painter" via AppLauncher (PATH / registry).
        """
        candidates = []
        if exe:
            candidates.append(exe)
        # Add common executable names
        candidates.extend([
            "Adobe Substance 3D Painter",
            "Adobe Substance 3D Painter.exe",
            "Painter" 
        ])

        # Arguments
        # Using --mesh to auto-load the mesh into a project creation wizard
        args = ["--mesh", mesh_path]

        if enable_remote:
            args.append("--enable-remote-scripting")
        
        # Note: Painter doesn't have a standard --headless flag.
        # Automation usually implies launching with remote scripting enabled and minimal interaction.
        
        cwd = os.path.dirname(mesh_path)

        for name in candidates:
            # We use detached=True so Maya doesn't freeze waiting for Painter
            proc = AppLauncher.launch(name, args=args, cwd=cwd, detached=True)
            if proc:
                return proc
        
        return None
