# !/usr/bin/python
# coding=utf-8
"""Maya-side selection + FBX-export hooks shared by the hand-off bridge engines.

:class:`MayaExportMixin` supplies the two DCC-specific :class:`pythontk.HandoffBridge`
hooks that every Maya-originating bridge shares -- read the selection and export it
to FBX (including the strip-materials path) -- so the Blender bridge, the Unity
bridge, and any future Maya->X bridge don't each re-implement them.

Per-bridge specifics (target discovery, delivery, FBX option tweaks) stay on the
bridge subclass; only the genuinely shared Maya plumbing lives here. ``import
maya.cmds`` is deferred so the engine surface still resolves headlessly; ``FbxUtils``
/ ``CoreUtils`` / ``NodeUtils`` are import-safe without a running Maya.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

try:
    import maya.cmds as cmds
except ModuleNotFoundError as error:
    print(__file__, error)

from pythontk import Payload

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils.fbx_utils import FbxUtils


class MayaExportMixin:
    """The Maya producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).

    Supplies the two DCC-specific :class:`pythontk.HandoffBridge` steps every
    Maya-originating bridge shares -- read the selection and produce the FBX
    :class:`pythontk.Payload` (incl. the strip-materials path). Bridges needing side
    artifacts (manifests, staged textures) override :meth:`_produce` and call
    :meth:`_export_fbx` themselves.
    """

    def _resolve_objects(self, objects):
        """Return the transform nodes to export; ``None`` -> current selection."""
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        return NodeUtils.get_transform_node(objects) if objects else []

    def _produce(self, objects, request) -> Payload:
        """Export the selection to a temp FBX and wrap it as a :class:`pythontk.Payload`."""
        fbx_path = self._make_payload_path()
        self._export_fbx(objects, fbx_path, request.params)
        return Payload(primary=fbx_path)

    def _fbx_options(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Maya ``FBXExport*`` flags derived from the bridge params.

        The default suits a mesh hand-off to another DCC (smoothing groups on; no
        cameras / lights). Bridges that need a different surface (e.g. tangents)
        override this.
        """
        return {
            "FBXExportSmoothingGroups": True,
            "FBXExportEmbeddedTextures": bool(params.get("EMBED_TEXTURES", True)),
            "FBXExportTriangulate": bool(params.get("TRIANGULATE", False)),
            "FBXExportBakeComplexAnimation": bool(params.get("INCLUDE_ANIMATION", False)),
            "FBXExportAnimationOnly": False,
            "FBXExportCameras": False,
            "FBXExportLights": False,
        }

    def _export_fbx(self, transforms: List[str], fbx_path: str, params: Dict[str, Any]) -> None:
        """Export *transforms* to *fbx_path*; restore the selection afterwards.

        When ``INCLUDE_MATERIALS`` is False the selection is duplicated, the copies
        are forced onto ``initialShadingGroup``, exported, then deleted -- the
        originals are untouched (FBX has no "exclude materials" export flag). The
        whole strip runs inside an undo chunk.
        """
        options = self._fbx_options(params)

        Path(fbx_path).parent.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Exporting {len(transforms)} object(s) to {fbx_path}")

        # Live Maya doesn't always pre-load fbxmaya -- load before exporting.
        FbxUtils.load_plugin()
        try:
            if bool(params.get("INCLUDE_MATERIALS", True)):
                FbxUtils.export(
                    file_path=fbx_path,
                    objects=transforms,
                    options=options,
                    selection_only=True,
                )
            else:
                with CoreUtils.undo_chunk("Handoff: strip materials"):
                    duplicates = []
                    try:
                        for orig in transforms:
                            dup = cmds.duplicate(
                                orig, returnRootsOnly=True, inputConnections=False
                            )[0]
                            duplicates.append(cmds.ls(dup, long=True)[0])
                        cmds.sets(
                            duplicates, edit=True, forceElement="initialShadingGroup"
                        )
                        FbxUtils.export(
                            file_path=fbx_path,
                            objects=duplicates,
                            options=options,
                            selection_only=True,
                        )
                    finally:
                        if duplicates:
                            cmds.delete(duplicates)
        finally:
            # FbxUtils.export selects what it exports (and the strip path deletes its
            # temp copies), so re-select the originals to leave the user's selection
            # intact.
            cmds.select(transforms, replace=True)
