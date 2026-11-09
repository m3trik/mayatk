# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter bridge -- export Maya selection and hand off to Painter.

The Maya half of the split, mirroring :mod:`mayatk.mat_utils.marmoset_bridge`:

* :class:`SubstanceBridge` (this module) -- a
  :class:`._substance_engine.SubstanceEngine` that supplies only the scene I/O
  the engine's produce step is written against: the FBX / USD writers, the
  selection, the material manifest, the textures assigned to the selection, the
  bake-source export and the scene ``fileInfo`` record of the last export.
* :mod:`_substance_engine` -- ``SubstanceEngine``, the DCC-free Painter half:
  script header parsing, finding the Painter a script runs in (by its instance
  record, or a launch), texture staging. Vendored
  byte-identical into blendertk.
* :mod:`templates/*.py` -- the built-in scripts, run in Painter.
* :mod:`parameters` -- UI-tunable knob registry referenced by templates.
* :mod:`connection` -- live process I/O (stdout / log tail / RPC).
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional

try:
    from maya import cmds
except ImportError:
    pass

import pythontk as ptk

from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.env_utils.usd import UsdUtils
from mayatk.mat_utils.mat_manifest import MatManifest

# The scene's bake source is a cross-tool concept (the Marmoset bake consumes
# the same set), so the class lives in the shared :mod:`mayatk.mat_utils.bake_sets`.
from mayatk.mat_utils.bake_sets import BakeSourceSet

# The DCC-free engine, plus the names the tests import from this module.
import mayatk.mat_utils.substance_bridge._substance_engine as _engine
from mayatk.mat_utils.substance_bridge._substance_engine import (  # noqa: F401
    SubstanceEngine,
    _TEMPLATE_DEFAULTS,
    _TEMPLATE_DIR,
)
from mayatk.mat_utils.substance_bridge.connection import (  # noqa: F401
    APP,
    SubstanceConnection,
)

logger = logging.getLogger(__name__)

# The mode / Painter vocabulary is the engine's. Bound here as assignments (not a
# bare import) because it is this module's public surface too: the subpackage
# ``__init__`` and the tests import it from here.
SEND_TO = _engine.SEND_TO
ROUND_TRIP = _engine.ROUND_TRIP
PAINTER_AUTO = _engine.PAINTER_AUTO
PAINTER_NEW = _engine.PAINTER_NEW


# USD options tuned for Substance Painter (the USD carrier): the shared interchange
# set (Painter's texture sets come from the ``UsdPreviewSurface`` bindings; the
# bridge stages the textures Painter needs itself), geometry only like the FBX set.
_DEFAULT_USD_OPTIONS: Dict[str, Any] = dict(
    UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
    exportBlendShapes=False,
    exportSkels="none",
    exportSkin="none",
)

# FBX options tuned for Substance Painter (same as the pre-restructure bridge).
_DEFAULT_FBX_OPTIONS: Dict[str, Any] = {
    "FBXExportSmoothingGroups": True,
    "FBXExportTangents": True,
    "FBXExportTriangulate": False,
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
    # True ships the base cage of a Smooth Mesh Preview object, as the USD
    # carrier and the Scene Exporter do; False shipped its subdivided mesh.
    "FBXExportSmoothMesh": True,
    "FBXExportUpAxis": "y",
    "FBXExportUseSceneName": False,
}


# -- Bridge ----------------------------------------------------------------


class SubstanceBridge(SubstanceEngine):
    """Export Maya selection to Substance Painter via a chosen template.

    The Maya half of the bridge. :class:`SubstanceEngine` (vendored, DCC-free)
    owns the hand-off itself -- the ``resolve -> preflight -> produce ->
    deliver`` skeleton, script header parsing, finding the Painter a script
    runs in (by its instance record, or a launch), texture staging. This
    class supplies the scene I/O the engine calls: the FBX / USD writers, the
    selection, the material manifest, the textures assigned to the selection,
    the :class:`BakeSourceSet` export and the scene ``fileInfo`` record of the
    last export.

    A send queues the script in Painter and returns its job (``send_to``);
    ``round_trip`` waits for the job's end (:meth:`SubstanceEngine.wait`).

    Usage::

        SubstanceBridge().send()                       # default: New Project
        SubstanceBridge().send(template="bake_lighting")  # the open project

    Backward-compatible with the pre-restructure API: legacy kwargs
    (``headless``, ``enable_remote``) are accepted and ignored if not
    meaningful to the template-driven model.
    """

    # Scratch namespace of a send with no Output Dir
    # (``<temp>/maya_substance_bridge_handoff``; see ``HandoffBridge._scratch_dir``).
    payload_prefix = "maya_substance_bridge"

    DEFAULT_FBX_OPTIONS = _DEFAULT_FBX_OPTIONS

    #: Suffix appended to the export stem for the companion high-poly file.
    #: Sourced from the shared set class so every bridge derives the same
    #: companion filename (see :meth:`BakeSourceSet.companion_path`).
    HIGH_POLY_SUFFIX = BakeSourceSet.FILE_SUFFIX

    #: The bundled ``templates/`` folder: what a panel lists and describes
    #: (:meth:`list_template_modes` / :meth:`parse_template` read the same one).
    TEMPLATE_DIR = _TEMPLATE_DIR

    @classmethod
    def params_registry(cls):
        """The panel's parameter registry (``parameters.Parameters``, a
        ``uitk.bridge.ParamRegistry``): what tentacle's Substance Bridge builds
        its rows from. Imported on use: the registry is uitk's, and the engine
        surface imports without it."""
        from . import parameters as _params

        return _params.Parameters

    # -- Public API -------------------------------------------------------

    def send(
        self,
        objects: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        painter_exe: Optional[str] = None,
        fbx_options: Optional[Dict[str, Any]] = None,
        preset_file: Optional[str] = None,
        template: str = "new_project",
        mode: str = SEND_TO,
        target: str = PAINTER_AUTO,
        params: Optional[Dict[str, Any]] = None,
        background: Optional[Callable[..., None]] = None,
        on_queued: Optional[Callable[[Dict[str, Any]], None]] = None,
        **legacy_kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Export *objects*, render *template* in *mode*, hand off to Painter.

        Parameters:
            objects: Nodes to export. Defaults to current selection.
            output_dir: Where the FBX (and optional manifest) lands.
                Defaults to ``<temp>/maya_substance_bridge_handoff``.
            output_name: Base filename without extension. Defaults to the
                Maya scene name or ``"untitled"``.
            painter_exe: Explicit ``Adobe Substance 3D Painter.exe`` override.
            fbx_options: FBX MEL overrides merged on top of defaults.
            preset_file: Optional FBX export preset path.
            template: Script stem (``"new_project"``, ``"bake_lighting"``, a person's own).
            mode: ``"send_to"`` (fire-and-forget) or ``"round_trip"``.
                Must match one of the template's declared
                :data:`BRIDGE_MODES`.
            target: The Painter (the Painter row): ``"auto"`` (default) picks
                by what the script works on (``SubstanceEngine.pick_painter``),
                ``"new"`` launches one, an instance record's id is that Painter.
            params: Placeholder overrides, e.g. ``{"PAINTER_RESOLUTION": 4096}``.
            background: ``(work, then)``: runs *work* off the UI thread, then
                ``then(work())`` on it (a panel's ``BackgroundTask``). With it, the
                waits (a launched Painter serving, a project opening) run there and
                the result says ``pending``; *on_queued* then gets the result.
            on_queued: Called with the result once the script is queued (or the
                run stopped) after a wait in *background*.
            **legacy_kwargs: Swallowed (``headless``, ``enable_remote``) for
                backward compatibility with the pre-restructure API.

        Returns:
            A result dict with ``fbx`` (the mesh), ``mode``, ``connection`` (the
            :class:`SubstanceConnection`, or *None* when no Painter fits),
            ``painter`` (its instance record), ``job`` (the queued script's id),
            ``output_dir``, ``high_poly`` and ``manifest`` (when written),
            ``delivered`` and, for ``round_trip``, ``status`` (the job's end).
            *None* when the send stopped before Painter.
        """
        return self._handoff(
            objects,
            template=template,
            mode=mode,
            target=target,
            params=params,
            legacy_kwargs=legacy_kwargs,
            output_dir=output_dir,
            output_name=output_name,
            painter_exe=painter_exe,
            fbx_options=fbx_options,
            preset_file=preset_file,
            background=background,
            on_queued=on_queued,
        )

    # -- Scene I/O (the engine's DCC hooks) -------------------------------

    def _export_model_usd(
        self,
        path: str,
        objects: List[str],
        request: ptk.HandoffRequest,
        fbx_options: Dict[str, Any],
    ) -> None:
        options = dict(_DEFAULT_USD_OPTIONS)
        options.update(request.get("usd_options") or {})
        UsdUtils.export(
            file_path=path, objects=objects, options=options, selection_only=True
        )

    def _export_model_fbx(
        self,
        path: str,
        objects: List[str],
        request: ptk.HandoffRequest,
        fbx_options: Dict[str, Any],
    ) -> None:
        # ``FbxUtils.export`` loads the fbxmaya plugin itself; the caller's
        # ``preset_file`` extra rides beside the merged Painter flag set.
        FbxUtils.export(
            file_path=path,
            objects=objects,
            preset_file=request.get("preset_file"),
            options=fbx_options,
            selection_only=True,
        )

    def _selected_objects(self) -> List[str]:
        """The Maya selection (long names), read only when the scope needs it."""
        return cmds.ls(selection=True, long=True) or []

    def _material_manifest(self, objects: List[str]) -> Dict[str, Any]:
        return MatManifest.build(objects)

    def _assigned_texture_paths(self, objects: List[str]) -> List[str]:
        """Every texture file the shading networks of *objects* reference."""
        from mayatk.mat_utils._mat_utils import MatUtils

        return MatUtils.get_texture_paths(objects=objects, absolute=True)

    def _scene_workspace(self):
        """The open scene's project (``DataNodes.scene_workspace``): where a
        send with no Output Dir keeps the mesh Painter re-reads
        (``cache/substance``) and a render's output. Mirror of blendertk's."""
        from mayatk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_workspace()

    @classmethod
    def _recorded_export_path(cls) -> Optional[str]:
        """Return the FBX path recorded by the last export, or ``None``."""
        try:
            values = cmds.fileInfo(cls.EXPORT_RECORD_KEY, query=True)
        except Exception:  # noqa: BLE001 -- no Maya / no scene
            return None
        if not values or not values[0]:
            return None
        return values[0].replace("\\", "/")

    @classmethod
    def _record_export_path(cls, fbx_path: str) -> None:
        """Persist *fbx_path* in the scene's fileInfo (forward slashes)."""
        try:
            cmds.fileInfo(cls.EXPORT_RECORD_KEY, fbx_path.replace("\\", "/"))
        except Exception as e:  # noqa: BLE001 -- recording is best-effort
            logger.debug("Could not record export path in fileInfo: %s", e)

    @classmethod
    def source_model_path_for(cls, fbx_path: str) -> str:
        """``.../asset.fbx`` -> ``.../asset_source.fbx``.

        Derived from the main export rather than re-resolved, so a
        ``REUSE_RECORDED_EXPORT`` template's high-poly file lands beside
        the exact mesh the open Painter project was built from. Delegates
        to the shared convention on :class:`BakeSourceSet`.
        """
        return BakeSourceSet.companion_path(fbx_path)

    def _export_bake_source(
        self,
        fbx_path: str,
        fbx_options: Dict[str, Any],
        referenced: set,
        request: ptk.HandoffRequest,
    ) -> Optional[str]:
        """Export :class:`BakeSourceSet`'s members to ``<stem>_source.fbx``.

        Returns the written path, or ``None`` when the template doesn't claim
        the Bake Source row, the scene has no set, or the export failed. A
        failure here is logged and swallowed: the main mesh is already on disk
        and the handoff is still worth making -- Painter simply opens without
        a bake source.

        **The set's contents are the switch.** There is no companion checkbox:
        a scene that has defined a bake source has, by defining it, said to
        ship it, and a scene that hasn't ships nothing. The pairing this
        replaces (a set plus an "Export Bake Source" tick) had two ways to
        spell "off" and one silent failure mode -- a set defined, the box left
        clear -- which is the state a user reads as a bug. Same contract as the
        Marmoset bridge, which never had the second control.

        The scene is never modified. Hidden members export exactly like
        visible ones (FBX carries the geometry regardless), which is also
        why this can't disturb a "Visible Only" scope: it reads the set,
        not the selection.
        """
        if "BAKE_SOURCE_SET" not in referenced:
            return None

        members = BakeSourceSet.members()
        if not members:
            # Not a warning: no bake source is the ordinary case for a plain
            # texturing hand-off, and a scene-state note per send would be noise.
            self.logger.debug(
                "No bake source defined in this scene; nothing to export. "
                "Define one with the panel's 'Set From Selection'."
            )
            return None

        # Texture embedding is for the paintable mesh; the bake source is
        # geometry only, and embedding would bloat a dense mesh for nothing.
        options = dict(fbx_options)
        options["FBXExportEmbeddedTextures"] = False

        high_path = self.source_model_path_for(fbx_path)
        self.logger.info(f"Exporting bake source ({len(members)} object(s)) ...")
        # ``FbxUtils.export`` selects what it exports, so this leg would
        # otherwise leave the bake-source set selected instead of whatever the
        # main export left behind -- the one bit of state it does disturb.
        restore = cmds.ls(selection=True, long=True) or []
        try:
            self._export_model(high_path, members, request, options)
        except Exception as e:  # noqa: BLE001 -- optional leg, never fatal
            self.logger.error(f"Bake-source export failed: {e}")
            return None
        finally:
            cmds.select(restore, replace=True) if restore else cmds.select(clear=True)
        self.logger.info(
            f"Bake source written: "
            f'<a href="action://open?path={high_path}">{high_path}</a>'
        )
        return high_path

    @staticmethod
    def _scene_path() -> str:
        """The open scene's file (``DataNodes.scene_path``), ``""`` while
        unsaved. Mirror of blendertk's."""
        from mayatk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_path()

    @staticmethod
    def _scene_base_name() -> str:
        """Return the current scene's base name (no extension), or ``'untitled'``."""
        scene = cmds.file(query=True, sceneName=True)
        if scene:
            return os.path.splitext(os.path.basename(scene))[0]
        return "untitled"


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    SubstanceBridge().send()
