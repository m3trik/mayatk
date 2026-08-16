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

    #: Ship the shared ``data_export`` carrier alongside the exported meshes.
    #:
    #: ``data_export`` is the in-band metadata surface -- lightmap manifests, shots,
    #: audio, emissive groups all stamp string channels onto that one hidden node, and
    #: FBX carries them as user properties. A *selection* export omits it (it is not
    #: under the selected roots), so a bridge whose consumer READS that metadata must
    #: opt in or its deliverable silently arrives bare. Off by default: to a bridge
    #: that only wants geometry (a DCC hand-off) the carrier is a stray empty in the
    #: target's outliner, and it should not pay for a channel it never reads.
    include_data_export: bool = False

    def _resolve_objects(self, objects):
        """Return the transform nodes to export; ``None`` -> current selection."""
        if objects is None:
            objects = cmds.ls(selection=True, long=True) or []
        return NodeUtils.get_transform_node(objects) if objects else []

    def _scene_objects(self) -> List[str]:
        """Every DAG root except Maya's startup cameras (the whole-scene hand-off).

        Used by ``save_as``, where "save the scene as ..." means the scene rather than
        the selection. The four startup cameras are dropped by name-independent query
        (a renamed ``persp`` is still a startup camera) -- they are Maya's viewport
        furniture, not content, and the FBX export excludes cameras anyway; keeping
        them would only make the exported set lie about what is being saved.
        """
        keep = []
        for node in cmds.ls(assemblies=True, long=True) or []:
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
            if any(
                cmds.nodeType(shape) == "camera"
                and cmds.camera(shape, query=True, startupCamera=True)
                for shape in shapes
            ):
                continue
            keep.append(node)
        return keep

    def _produce(self, objects, request) -> Payload:
        """Export the selection to a temp FBX and wrap it as a :class:`pythontk.Payload`."""
        fbx_path = self._make_payload_path()
        self._export_fbx(objects, fbx_path, request.params)
        return Payload(primary=fbx_path)

    def _data_export_carrier(self) -> List[str]:
        """``[data_export]`` when this bridge ships it and the scene has one, else ``[]``.

        Never *creates* the node: an absent carrier means the scene has no in-band
        metadata to ship, and manufacturing an empty one would only put a stray null
        in the deliverable. Returned as a list so callers concatenate rather than
        branch; a whole-scene ``save_as`` already passes every DAG root -- the
        carrier among them -- and the resulting repeat is harmless, since the export
        realizes the list as a Maya selection.
        """
        if not self.include_data_export:
            return []
        from mayatk.node_utils.data_nodes import DataNodes

        # get_export_node applies the duplicate-name tie-break (root carrier
        # wins), where a bare ``cmds.ls(...)[:1]`` would take whichever match
        # sorts first — possibly the imported copy the producers never wrote.
        node = DataNodes.get_export_node(create=False)
        return [str(node)] if node else []

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
            # Pinned, not inherited: FBX plugin options are STICKY session state and
            # the substance bridge sets this False for its own exports -- without the
            # pin, running that bridge first would silently de-instance every later
            # hand-off (the lightmap round trip depends on instancing surviving).
            "FBXExportInstances": True,
        }

    def _export_fbx(self, transforms: List[str], fbx_path: str, params: Dict[str, Any]) -> None:
        """Export *transforms* to *fbx_path*; restore the PRIOR selection afterwards.

        When ``INCLUDE_MATERIALS`` is False the selection is duplicated, the copies
        are forced onto ``initialShadingGroup``, exported, then deleted -- the
        originals are untouched (FBX has no "exclude materials" export flag). The
        whole strip runs inside an undo chunk.

        The ``data_export`` carrier (when :attr:`include_data_export`) joins the
        export set but never the strip duplication -- it is a locked, hidden,
        shapeless node, so duplicating it and forcing it into a shading group would
        be nonsense; only the meshes need stripping.
        """
        options = self._fbx_options(params)
        carrier = self._data_export_carrier()
        # What the USER had selected, captured before the export selects anything.
        # Restoring *transforms* instead would silently hand the artist a different
        # selection whenever the exported set isn't the selection -- an explicit
        # ``send(objects=...)``, and every ``save_as``, which defaults to the whole
        # scene. Mirror of the Blender exporter, which already restores the prior
        # selection.
        prior = cmds.ls(selection=True, long=True) or []

        Path(fbx_path).parent.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Exporting {len(transforms)} object(s) to {fbx_path}")

        # Live Maya doesn't always pre-load fbxmaya -- load before exporting.
        FbxUtils.load_plugin()
        try:
            if bool(params.get("INCLUDE_MATERIALS", True)):
                FbxUtils.export(
                    file_path=fbx_path,
                    objects=list(transforms) + carrier,
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
                            # Resolve the new node's unambiguous full path from its
                            # known parent (duplicate places the copy as a sibling of
                            # orig). The bare dup name could otherwise re-resolve to a
                            # same-named node elsewhere and get deleted below; the
                            # selection isn't reliable here either (shader/set ops can
                            # leave an unrelated node selected).
                            parents = cmds.listRelatives(orig, parent=True, fullPath=True)
                            prefix = parents[0] if parents else ""
                            duplicates.append(cmds.ls(f"{prefix}|{dup}", long=True)[0])
                        cmds.sets(
                            duplicates, edit=True, forceElement="initialShadingGroup"
                        )
                        FbxUtils.export(
                            file_path=fbx_path,
                            objects=duplicates + carrier,
                            options=options,
                            selection_only=True,
                        )
                    finally:
                        if duplicates:
                            cmds.delete(duplicates)
        finally:
            # FbxUtils.export selects what it exports (and the strip path deletes its
            # temp copies), so put the user's own selection back. Filtered through
            # ``ls`` because a node captured before the export may be gone by now
            # (a stripped duplicate, or anything the export chain removed) and
            # ``select`` raises on a missing node -- which would mask the real error
            # when this finally runs on an exception path.
            existing = cmds.ls(prior, long=True) if prior else []
            if existing:
                cmds.select(existing, replace=True)
            else:
                cmds.select(clear=True)
