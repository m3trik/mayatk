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
from typing import Any, Callable, Dict, List, Optional, Tuple

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

    #: ``FbxUtils._KNOWN_PRODUCERS`` keys whose channel is COMPUTED from live
    #: scene state rather than merely republished from authored state, and so
    #: must be rebuilt before a hand-off ships the carrier. A producer with
    #: nothing to publish clears its channel, so this must NOT be the whole set:
    #: see the refresh in :meth:`_data_export_carrier` for what that cost.
    #: ``visibility_tracks`` reads the visibility curves themselves, which an
    #: artist edits between one preview push and the next.
    #:
    #: A bridge whose consumer READS the render-effects transport (the GLB
    #: route strips the curve proxies, the Unity importer rebinds them) adds
    #: ``"render_effects"``: that preparer suspends the viewport material
    #: bindings and stages one proxy child per keyed channel for the write, and
    #: its finalizer puts both back. Not the default: a bake or DCC hand-off
    #: (Marmoset, Substance, the Blender bridge) has no consumer for a
    #: ``<node>__opacity`` child and would ship it as a stray transform.
    refresh_producers: Tuple[str, ...] = ("visibility",)

    def lightmap_search_dirs(self) -> List[str]:
        """Where Maya's map files live now (:class:`pythontk.PreviewBridge` hook).

        Answers the question the FBX's lightmap manifest cannot: it records the
        folder the bake was COMMITTED from, and a project reorganised since (or
        opened on another machine) leaves every EXR lookup missing -- which
        previews as an unlit push and reads as a broken bake. Supplied here, on
        the mixin every Maya-originating bridge already carries, so it is one
        answer rather than one per bridge; the exporter's GLB conversion reaches
        the same :meth:`LightmapBaker.search_dirs` -- the workspace's texture
        folders plus wherever the markers' maps were actually found, so a map
        the walk had to go looking for still reaches a consumer that can only
        join a basename against a list.
        """
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker.search_dirs()

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

    @staticmethod
    def _visible_objects() -> List[str]:
        """Transform paths of every CURRENTLY VISIBLE mesh (the Visible Only scope).

        Sibling of :meth:`_scene_objects`, and the same kind of host read: the two
        widening scopes ``uitk.bridge.Parameters.scope_spec`` declares. Static
        because it consults only the scene -- which lets the bridge-slots resolver
        (``MayaBridgeSlotsBase.resolve_scope_objects``) call it for panels whose
        bridge has no export mixin at all, so there is one implementation of
        "visible" rather than one per caller.

        Leaf transforms, not DAG roots: a root is only whole-scene's unit because
        the subtree has to travel with it. Here the whole point is that hidden
        members of a visible parent do NOT.
        """
        from mayatk.display_utils._display_utils import DisplayUtils

        # inherit_parent_visibility=True is what actually walks the transform
        # chain and drops hidden geometry (without it the helper returns every
        # renderable shape regardless of visibility).
        shapes = (
            DisplayUtils.get_visible_geometry(
                shapes=True, inherit_parent_visibility=True
            )
            or []
        )

        # Expand each shape to ALL its parent paths, not the first: an instanced
        # shape is one node worn by many transforms, and the shape->transform
        # coercion keeps only the first parent -- which would silently drop every
        # instance sibling from the export set (the same trap as
        # NodeUtils.list_transforms' shape dedup). Each path is visibility-checked
        # on its own: one sibling being visible must not smuggle a hidden one in.
        def _path_visible(path: str) -> bool:
            node = str(path)
            while node and node != "|":
                try:
                    if not cmds.getAttr(f"{node}.visibility"):
                        return False
                except Exception:  # noqa: BLE001 -- no visibility attr
                    pass
                node = node.rsplit("|", 1)[0]
            return True

        out: List[str] = []
        for shape in shapes:
            for parent in cmds.listRelatives(
                str(shape), allParents=True, fullPath=True
            ) or [str(shape)]:
                if parent not in out and _path_visible(parent):
                    out.append(parent)
        return out

    def _produce(self, objects, request) -> Payload:
        """Export the selection to a temp payload in the request's carrier."""
        path = self._make_payload_path(self.payload_extension(request))
        self._export_payload(objects, path, request.params)
        return Payload(primary=path)

    def _payload_writers(
        self,
    ) -> Dict[str, Callable[[List[str], str, Dict[str, Any]], None]]:
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
        """Every ``data_export`` carrier this bridge ships, else ``[]``.

        Never manufactures an EMPTY one: a scene with no in-band metadata gets
        no carrier, because a stray null in the deliverable is worse than an
        absent one. The export bracket's refresh (which ran before this) can
        still bring a carrier into being -- but only by writing a channel, i.e.
        only when the scene turned out to have metadata after all, which is
        the case the rule was never about.
        Always a list -- callers concatenate rather than
        branch, and an assembly legitimately has several; a whole-scene ``save_as``
        already passes every DAG root -- the carriers among them -- and the
        resulting repeat is harmless, since the export realizes the list as a
        Maya selection.
        """
        if not self.include_data_export:
            return []
        from mayatk.node_utils.data_nodes import DataNodes

        # The DERIVED channels were made current by the export bracket the
        # writer opened around this call (:attr:`refresh_producers`, narrowed
        # rather than a full refresh because a producer with nothing to publish
        # CLEARS its channel: refreshing everything wiped a ``lightmap_metadata``
        # whose markers the scene no longer carried and previewed the asset
        # unlit). An export PIPELINE is the authority on every channel; a
        # hand-off that merely ships the carrier is not.
        #
        # EVERY carrier, not the canonical one: an assembly's referenced modules
        # each publish onto their own ``NS:data_export``, and shipping only the
        # root carrier left a referenced module's whole lightmap manifest out of
        # the deliverable (measured: a selection export of a lit module previewed
        # unlit, its bake sitting one namespace away). ``get_export_nodes``
        # documents why several are safe to ship.
        return DataNodes.get_export_nodes()

    def _fbx_options(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Maya ``FBXExport*`` flags derived from the bridge params.

        The default suits a mesh hand-off to another DCC (smoothing groups on; no
        cameras / lights). Bridges that need a different surface (e.g. tangents)
        override this.
        """
        return {
            "FBXExportSmoothingGroups": True,
            # Pinned like the smoothing groups, and for the same kind of reason:
            # the factory value is OFF, and a normal-mapped asset that ships no
            # TANGENT leaves its tangent basis for the receiver to invent.
            # Receivers disagree -- three.js swaps in a screen-space derivative
            # basis and flips green to compensate, and a baker (Substance,
            # Marmoset) wants the SAME basis the asset was authored against or
            # its bake will not match. blendertk's twin has always set
            # ``use_tspace``.
            "FBXExportTangents": True,
            "FBXExportEmbeddedTextures": bool(params.get("EMBED_TEXTURES", True)),
            "FBXExportTriangulate": bool(params.get("TRIANGULATE", False)),
            "FBXExportBakeComplexAnimation": bool(
                params.get("INCLUDE_ANIMATION", False)
            ),
            "FBXExportAnimationOnly": False,
            "FBXExportCameras": False,
            "FBXExportLights": False,
            # Pinned, not inherited: FBX plugin options are STICKY session state and
            # the substance bridge sets this False for its own exports -- without the
            # pin, running that bridge first would silently de-instance every later
            # hand-off (the lightmap round trip depends on instancing surviving).
            "FBXExportInstances": True,
        }

    def _export_fbx(
        self, transforms: List[str], fbx_path: str, params: Dict[str, Any]
    ) -> None:
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
        # Inside the export bracket, like the Scene Exporter: the preparers run
        # on entry (:attr:`refresh_producers`), the finalizers on exit, AFTER the
        # file exists -- so the deliverable carries the prepared scene and the
        # artist gets the viewport back as it was.
        with FbxUtils.export_prepared(only=self.refresh_producers):
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
            # Reset BEFORE anything arms state, so the write starts from the factory
            # baseline and only :meth:`_fbx_options` moves it. Pinning alone is not
            # enough: the plugin's export flags are sticky for the life of the
            # session and the ones this does not name still decide the deliverable's
            # CONTENT -- ``FBXExportReferencedAssetsContent`` settles whether a
            # referenced module ships at all. Without this, whoever exported last
            # decided part of the hand-off, and the same scene pushed twice in one
            # session could carry different geometry -- the preview-vs-deliverable
            # divergence this mixin exists to remove. The Scene Exporter has always
            # done exactly this (``_apply_default_fbx_options``).
            #
            # Here rather than in ``FbxUtils.export``: that is the shared writer,
            # and the Scene Exporter arms its bake range and take split BEFORE
            # calling it -- a reset down there would wipe both. For the same reason
            # this sits ABOVE the ``apply_takes_from_node`` call below.
            #
            # Best-effort, like the import twin in the Rizom bridge: a baseline that
            # cannot be established is a worse deliverable, not a failed one, and
            # refusing to export because the plugin would not answer would turn a
            # determinism improvement into an outage.
            try:
                FbxUtils.reset_export()
            except Exception:  # noqa: BLE001
                self.logger.debug("FBX export options not reset.", exc_info=True)
            # Guards the TAKE reset in the ``finally`` (not the option reset above)
            # on having ATTEMPTED the split rather than on having armed one:
            # ``apply_takes`` writes sticky MEL state per take, so a raise partway
            # through its loop leaves a partial split armed while the count that
            # would trigger the cleanup was never assigned.
            wants_animation = bool(params.get("INCLUDE_ANIMATION", False))
            try:
                if wants_animation:
                    # Realize the shots the scene DECLARES as named AnimStacks, so
                    # every animated hand-off carries per-shot clips rather than one
                    # whole-timeline "Take 001" a consumer has to slice by hand.
                    #
                    # Here, not in a caller: the session hook that does this for
                    # File > Export is opt-in (``enable_auto_takes`` / a registered
                    # preparer) and nothing installs it headless, so the take split
                    # reached only the Scene Exporter -- which calls it explicitly.
                    # Two writers of the same deliverable disagreeing about whether
                    # shots survive is the divergence this mixin exists to remove:
                    # measured on a 12-shot production assembly, the exporter's GLB
                    # carried 12 clips and the preview's carried one.
                    #
                    # Declared, never regenerated: this realizes whatever is already
                    # on the carrier (the same contract ``enable_auto_takes``
                    # documents) rather than running the producers, so a preview
                    # push stays free of scene side effects. Idempotent alongside
                    # the hook -- ``apply_takes`` clears prior take state first.
                    takes = FbxUtils.apply_takes_from_node()
                    if takes:
                        self.logger.info(
                            f"Animation: realized {takes} declared take(s)."
                        )
                    else:
                        # No shots to set a union range, and the reset above left the
                        # plugin's factory 1-48 -- which would ship 48 frames of
                        # whatever timeline this scene actually has.
                        #
                        # NOT best-effort, unlike the reset: that one establishes a
                        # baseline and a missing baseline still exports correctly,
                        # while a range that failed to apply exports the WRONG
                        # animation and says nothing. Same reason its sibling
                        # ``apply_takes_from_node`` is unguarded.
                        start, end = FbxUtils.set_bake_range_from_scene()
                        self.logger.debug(
                            f"Animation: no declared takes; bake range {start}-{end}."
                        )
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
                            # Static copies (full paths, no deformer wiring). The
                            # export set is ANY transform -- ``save_as`` hands over
                            # DAG roots -- so a group's subtree is the payload and
                            # must come along. The strip then has to reach every
                            # mesh UNDER the copies, not just the roots: forcing
                            # only the roots left a group's child meshes with their
                            # original materials, the one thing this path exists
                            # to remove.
                            for orig in transforms:
                                duplicates.append(
                                    NodeUtils.static_copy(orig, strip_children=False)
                                )
                            copied_meshes = (
                                cmds.listRelatives(
                                    duplicates,
                                    allDescendents=True,
                                    type="mesh",
                                    fullPath=True,
                                    noIntermediate=True,
                                )
                                or []
                            )
                            cmds.sets(
                                copied_meshes or duplicates,
                                edit=True,
                                forceElement="initialShadingGroup",
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
                if wants_animation:
                    # Take splits and the bake-complex range they set are STICKY
                    # global exporter state: left armed they leak into every later
                    # export this session, including the user's own File > Export.
                    # Idempotent, so running it for a scene that declared no takes
                    # costs one MEL call and clears anything a previous run left.
                    FbxUtils.reset_takes()
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
        exported = set(
            cmds.ls(transforms, long=True) or []
        )  # full paths, like the parents
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

        with FbxUtils.export_prepared(only=self.refresh_producers):
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
