# !/usr/bin/python
# coding=utf-8
"""Maya-side selection + export hooks shared by the hand-off bridge engines.

:class:`MayaExportMixin` supplies the two DCC-specific :class:`pythontk.HandoffBridge`
hooks that every Maya-originating bridge shares -- read the selection and export it
in the request's carrier, FBX (including the strip-materials path) or USD -- so the
Blender bridge, the Unity bridge, and any future Maya->X bridge don't each
re-implement them.

Per-bridge specifics (target discovery, delivery, export option tweaks) stay on the
bridge subclass; only the genuinely shared Maya plumbing lives here. ``import
maya.cmds`` is deferred so the engine surface still resolves headlessly; ``FbxUtils``
/ ``UsdUtils`` / ``CoreUtils`` / ``NodeUtils`` are import-safe without a running Maya.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import maya.cmds as cmds
except ModuleNotFoundError as error:
    print(__file__, error)

from pythontk import Payload

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.env_utils.usd import UsdUtils


class MayaExportMixin:
    """The Maya producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).

    Supplies the two DCC-specific :class:`pythontk.HandoffBridge` steps every
    Maya-originating bridge shares -- read the selection and produce the
    :class:`pythontk.Payload` in the request's carrier (FBX, incl. the
    strip-materials path, or USD). Bridges needing side artifacts (manifests,
    staged textures) override :meth:`_produce` and call :meth:`_export_payload`
    themselves with a path whose extension names the carrier.
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

    #: What the USD carrier does with an instanced selection. USD leaves Maya FLAT
    #: (``exportInstances`` off -- see :class:`UsdUtils`: the native instancing
    #: collapses material export), so shape sharing does not survive the hop and
    #: the target receives N independent meshes. ``False`` (default) REFUSES the
    #: send with a pointer at FBX, which carries instancing natively: for a scene
    #: hand-off a flat copy is a silent structural loss an artist finds hours later
    #: (what reverted a USD default on 2026-08-02). ``True`` lets it through flat
    #: with a warning -- right for a target that never hands geometry back
    #: (texturing / baking), where every instance wants its own textures anyway.
    usd_flattens_instances: bool = False

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
        """Export the selection to a temp payload in the request's carrier."""
        path = self._make_payload_path(self.payload_extension(request))
        self._export_payload(objects, path, request.params)
        return Payload(primary=path)

    def _payload_writers(self) -> Dict[str, Callable[[List[str], str, Dict[str, Any]], None]]:
        """``{carrier: writer(transforms, path, params)}`` -- the Strategy table.

        A new carrier is one entry here plus its writer; a bridge that needs a
        different surface for one carrier overrides the entry, not the dispatch.
        """
        return {"fbx": self._export_fbx, "usd": self._export_usd}

    def _export_payload(
        self, transforms: List[str], path: str, params: Dict[str, Any]
    ) -> None:
        """Export *transforms* to *path* in the carrier its extension names.

        The ONE dispatch, keyed on the extension (:meth:`pythontk.HandoffBridge.carrier_of`)
        rather than the params, so a bridge that builds its own payload path (a
        bake staged under a name its target expects) only has to name it right.
        """
        self._payload_writers()[self.carrier_of(path)](transforms, path, params)

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

    def _usd_options(
        self, params: Dict[str, Any], transforms: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """``cmds.mayaUSDExport`` flags derived from the bridge params.

        :attr:`UsdUtils.INTERCHANGE_EXPORT_OPTIONS` (the pull route's live-verified
        set, reasons documented there), not a new guess.

        Params map where USD has a native answer and are reported where it has
        none: ``INCLUDE_MATERIALS`` off is ``shadingMode='none'`` (no strip
        duplicates needed); ``INCLUDE_ANIMATION`` samples only the frames that
        carry motion (:meth:`UsdUtils.sampling_frame_range` -- ``frameRange`` is a
        direct multiplier on export cost); ``EMBED_TEXTURES`` / ``TRIANGULATE``
        have no USD export flag and are logged as inert rather than silently
        dropped. Bridges needing a different surface override this.
        """
        options = dict(
            UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
            shadingMode=(
                "useRegistry" if bool(params.get("INCLUDE_MATERIALS", True)) else "none"
            ),
        )
        if bool(params.get("INCLUDE_ANIMATION", False)):
            frame_range = UsdUtils.sampling_frame_range(transforms)
            if frame_range:
                options["frameRange"] = frame_range
        if params.get("TRIANGULATE"):
            self.logger.warning(
                "TRIANGULATE has no USD export flag and is not applied on the USD carrier."
            )
        if not bool(params.get("EMBED_TEXTURES", True)):
            self.logger.info(
                "USD references texture files by absolute path; EMBED_TEXTURES "
                "does not apply (a .usdz package would embed them)."
            )
        return options

    def _export_usd(
        self, transforms: List[str], usd_path: str, params: Dict[str, Any]
    ) -> None:
        """Export *transforms* to *usd_path*; restore the PRIOR selection afterwards.

        The USD twin of :meth:`_export_fbx`. No strip path: ``INCLUDE_MATERIALS``
        off exports with ``shadingMode='none'``, so the originals are never
        touched. The ``data_export`` carrier joins the export set exactly as it
        does for FBX.

        Refuses an instanced selection unless :attr:`usd_flattens_instances`:
        the export is flat (see :attr:`UsdUtils._DEFAULT_EXPORT_OPTIONS`), so
        every instance would arrive as its own independent mesh -- the silent
        structural loss FBX never has, and the reason the USD carrier stays
        opt-in. The refusal names the shapes and the route that works.
        """
        # Sharing WITHIN the exported hierarchy only (the mirror of the Blender
        # twin's export-set scope): an instance whose siblings stay behind leaves
        # as one mesh, which is no loss. Maya's export-selection ships every
        # descendant, so "within" means under any exported root.
        exported = set(cmds.ls(transforms, long=True) or [])  # full paths, like the parents
        roots = tuple(f"{t}|" for t in exported)
        instanced: Dict[str, List[str]] = {}
        for shape, parents in self._instanced_shapes(transforms).items():
            inside = [p for p in parents if p in exported or p.startswith(roots)]
            if len(inside) > 1:
                instanced[shape] = inside
        if instanced:
            copies = sum(len(parents) for parents in instanced.values())
            detail = (
                f"{copies} transform(s) share {len(instanced)} instanced shape(s): "
                + ", ".join(sorted(s.split("|")[-1] for s in instanced))
            )
            if not self.usd_flattens_instances:
                raise RuntimeError(
                    "The USD carrier exports FLAT -- instancing would not survive "
                    f"the hand-off ({detail}). Send via FBX, which carries instances "
                    "natively, or un-instance the selection first."
                )
            self.logger.warning(
                f"USD carrier: instancing is flattened for this hand-off ({detail})."
            )

        options = self._usd_options(params, transforms)
        carrier = self._data_export_carrier()
        if carrier:
            self.logger.warning(
                "The data_export carrier rides the USD payload as custom attributes; "
                "whether the target reads them as userProperties is not yet verified "
                "on this route."
            )
        prior = cmds.ls(selection=True, long=True) or []

        Path(usd_path).parent.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Exporting {len(transforms)} object(s) to {usd_path}")
        try:
            UsdUtils.export(
                file_path=usd_path,
                objects=list(transforms) + carrier,
                options=options,
                selection_only=True,
            )
        finally:
            existing = cmds.ls(prior, long=True) if prior else []
            if existing:
                cmds.select(existing, replace=True)
            else:
                cmds.select(clear=True)

    @staticmethod
    def _instanced_shapes(transforms: List[str]) -> Dict[str, List[str]]:
        """``{shape: [instance transform, ...]}`` for shapes anywhere under
        *transforms* worn more than once -- deduped by the shape's instance GROUP,
        not its path (an instanced shape has one full DAG path PER instance, so
        keying on the path would count one shared wall N times).

        The scan walks DESCENDANTS, because every caller is guarding an export and
        Maya's export-selection ships the whole subtree. ``get_instanced_shapes``
        reads a node's DIRECT child shapes, so a scan of the handed-in transforms
        alone is blind to the common case: select a GROUP of instanced walls and it
        reports nothing, and the flat USD lands as N independent meshes with no
        refusal and no warning. ``save_as`` hits this every time -- it hands DAG
        roots.
        """
        out: Dict[str, List[str]] = {}
        seen: set = set()
        for obj in transforms or []:
            for shape in (
                NodeUtils.get_instanced_shapes(str(obj), descendants=True) or []
            ):
                parents = (
                    cmds.listRelatives(shape, allParents=True, fullPath=True) or []
                )
                key = tuple(sorted(parents))
                if key in seen:
                    continue
                seen.add(key)
                out[shape] = parents
        return out
