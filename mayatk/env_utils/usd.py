# !/usr/bin/python
# coding=utf-8
"""USD import / export over Maya's native ``mayaUsd`` runtime.

The USD sibling of :class:`~mayatk.env_utils.fbx_utils.FbxUtils` (same module
shape, same surface verbs), mirrored by ``blendertk.env_utils.usd`` per the
ecosystem parity rule (``mtk.UsdUtils`` ↔ ``btk.UsdUtils``, name + behavior).

Maya 2025 ships ``mayaUsdPlugin`` (``mayaUSDExport`` / the *USD Import* file
translator), which already handles the conversions the FBX pipeline needs
side-channels for: materials → ``UsdPreviewSurface``, Maya instances →
instanceable prims, custom attributes → USD userProperties. This module only
configures and drives that native runtime — it does not re-author USD itself.
The zero-dep floor (format sniffing, USDZ packaging) is shared upstream in
``pythontk.file_utils.usd``.

``.usdz`` export composes :meth:`pythontk.UsdzPackager.from_layer`: the scene
is exported as a temp text layer, its on-disk texture references are pulled
in-package, and the result is a self-contained, QuickLook-ready archive.
"""

import os
import re
import math
import logging
import contextlib
from typing import Any, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    pass

import pythontk as ptk

from mayatk.core_utils.plugins._plugins import Plugins
from mayatk.core_utils.units import Units

logger = logging.getLogger(__name__)


class UsdReadRefused(RuntimeError):
    """A stage refused for a LIVE read -- a reference or an open -- because its
    skins crash mayaUsd's reader (:meth:`UsdUtils.live_read_options`).

    Raised instead of handing Maya the stage: Maya dies on it, so there is nothing
    to catch afterwards. The stage itself is readable -- :meth:`UsdUtils.import_scene`
    brings it in safely -- which is what a caller can offer instead. *prims* are
    the crashing skins' prim paths.
    """

    def __init__(self, message: str, prims: List[str]):
        super().__init__(message)
        self.prims = list(prims)


class UsdUtils(ptk.HelpMixin):
    """Low-level USD import/export utilities over the ``mayaUsd`` plugin.

    Owns plugin loading and the ``cmds.mayaUSDExport`` / ``cmds.file`` (USD
    translator) calls. Higher-level orchestration (task pipelines, UI,
    namespace sandboxing) belongs to ``SceneExporter`` / ``NamespaceSandbox``
    or calling code — the same contract as ``FbxUtils``.
    """

    #: Extensions the USD runtime reads/writes (shared SSoT with pythontk).
    EXTENSIONS = ptk.USD_EXTENSIONS

    # Interchange-quality defaults for mayaUSDExport. Chosen for the hand-off
    # cases (Blender / engines / QuickLook): registry shading with a
    # UsdPreviewSurface conversion so materials survive the hop, transform+shape
    # merged into the single prim other DCCs expect. Callers override any key
    # via ``options``.
    #
    # ``exportInstances`` is DELIBERATELY off. Measured on Maya 2025 / mayaUsd:
    # when the scene holds instanced shapes, material export degrades badly and
    # can collapse outright -- on a probe scene whose only instances were two
    # cubes, `def Material` went 3 -> 0 and `material:binding` 4 -> 0 with the
    # flag on, taking a material bound ONLY to a non-instanced mesh with it.
    # Per-instance material assignments are lost regardless (all instances share
    # one prototype), and consumers receive instanceable prototypes rather than
    # editable meshes. Materials are the point of an interchange default, so
    # instances are flattened here. Flattening need not lose the relationship:
    # the Maya->Blender bridge records Maya's instance sets in its conversion
    # sidecar and rebuilds native shared mesh data on import (755 objects ->
    # 628 datablocks, matching its FBX route) -- a consumer that needs sharing
    # should carry the grouping the same way. Pass
    # ``options={"exportInstances": True}`` if a consumer genuinely wants USD
    # prototypes and can live without shading.
    _DEFAULT_EXPORT_OPTIONS = {
        "shadingMode": "useRegistry",
        "convertMaterialsTo": ["UsdPreviewSurface"],
        "exportInstances": False,
        "mergeTransformAndShape": True,
        "exportUVs": True,
        "exportVisibility": True,
        # Without this every material is DROPPED when a root is namespaced (a
        # referenced asset) -- see INTERCHANGE_EXPORT_OPTIONS for the probe.
        "legacyMaterialScope": True,
        # Maya's primary set travels by NAME -- see INTERCHANGE_EXPORT_OPTIONS.
        "preserveUVSetNames": True,
    }

    #: The hand-off set every USD carrier composes from -- the Maya->Blender pull
    #: route's live-verified ``mayaUSDExport`` flags, measured reason by reason in
    #: its conversion template: registry shading to ``UsdPreviewSurface`` (a
    #: MaterialX network is a per-site override), one prim per object, polys
    #: left as polys (``defaultMeshScheme='none'`` -- the exporter's catmullClark
    #: default would have a consumer subdivide-smooth them), skin / blendshapes
    #: on auto, absolute texture references (a scratch payload resolves nowhere
    #: relative; a deliverable beside its scene overrides to ``automatic``), and
    #: instancing flattened (see :attr:`_DEFAULT_EXPORT_OPTIONS`). Compose, never
    #: mutate: ``dict(UsdUtils.INTERCHANGE_EXPORT_OPTIONS, exportSkels="none")``.
    INTERCHANGE_EXPORT_OPTIONS: Dict[str, Any] = {
        "shadingMode": "useRegistry",
        "convertMaterialsTo": ["UsdPreviewSurface"],
        "exportInstances": False,
        "mergeTransformAndShape": True,
        "exportUVs": True,
        "exportVisibility": True,
        "exportBlendShapes": True,
        "exportSkels": "auto",
        "exportSkin": "auto",
        "defaultMeshScheme": "none",
        "exportRelativeTextures": "absolute",
        # The default materials-scope placement nests ``mtl`` under a root prim
        # and DROPS EVERY MATERIAL when that root is namespaced ("Cannot append
        # child 'mtl' to path ''" -- mayaUsd 0.30, probed on a referenced-style
        # ``ns:cube``; stripNamespaces / materialsScopeName don't help). The
        # legacy placement keeps them in every case (under the single root, or
        # beside the roots), and a referenced asset is namespaced by definition.
        "legacyMaterialScope": True,
        # The exporter's default rewrites ``map1`` to ``st``; USD stores primvars
        # alphabetically, so a consumer can't tell the primary set by position
        # once a second set (``lightmap``) sorts ahead of it. ``map1`` by name
        # is what every receiver keys on (Blender activates it, Maya gets it
        # back verbatim), the FBX route's spelling.
        "preserveUVSetNames": True,
    }

    #: The interchange set for a deliverable beside its scene: textures
    #: referenced relative to the layer where possible (the Scene Exporter's
    #: USD format and Export Scene's).
    DELIVERABLE_EXPORT_OPTIONS: Dict[str, Any] = dict(
        INTERCHANGE_EXPORT_OPTIONS,
        exportRelativeTextures="automatic",
    )

    #: *USD Import* translator options every hand-off importer starts from:
    #: time samples as keys (the translator's default is OFF -- every animated
    #: prim arrived static, measured) and Blender's ``st`` -- what its exporter
    #: names the render-active UV map -- landing as Maya's ``map1``, the FBX
    #: route's spelling. Serialized by :meth:`options_string`; the bridge
    #: templates carry the string itself (pinned equal).
    INTERCHANGE_IMPORT_OPTIONS: Dict[str, Any] = {
        "readAnimData": True,
        "remapUVSetsTo": [["st", "map1"]],
    }

    #: What a material's name sits on: ``material_names`` of :meth:`export` and
    #: :meth:`import_scene`.
    MATERIAL_NAMES = ("shader", "shading_group")

    #: The ``cmds.file`` translator mayaUsd registers for READING a layer: an import,
    #: a reference and an open all go through it (:meth:`file_options`).
    IMPORT_TRANSLATOR = "USD Import"

    #: Node types whose motion is not derivable from key times: their presence
    #: makes :meth:`sampling_frame_range` fall back to the full playback range.
    _UNKEYED_DRIVERS = (
        "parentConstraint",
        "pointConstraint",
        "orientConstraint",
        "scaleConstraint",
        "aimConstraint",
        "poleVectorConstraint",
        "geometryConstraint",
        "normalConstraint",
        "tangentConstraint",
        "expression",
        "ikHandle",
        "motionPath",
    )

    @staticmethod
    def load_plugin():
        """Ensure the ``mayaUsdPlugin`` plugin is loaded."""
        Plugins.load("mayaUsdPlugin")

    @staticmethod
    def is_usd_file(file_path: str) -> bool:
        """True when *file_path* is a USD layer/package (delegates to pythontk)."""
        return ptk.UsdFile.is_usd_file(file_path)

    @staticmethod
    def sanitize_prim_name(name: str) -> str:
        """*name* as ``mayaUSDExport`` spells the prim (probe-verified: ``ref:nsCube``
        -> ``ref_nsCube``): every char outside ``[A-Za-z0-9_]`` becomes ``_``, and
        a leading digit is PREFIXED with ``_``. What a sidecar must record to match
        what the exporter actually writes. Mirror of blendertk's
        ``UsdUtils.sanitize_prim_name``; the pull templates carry dependency-free
        copies kept in step by hand."""
        if not name:
            return "_"
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)
        if name[0].isdigit():
            name = "_" + name
        return name

    @classmethod
    def export(
        cls,
        file_path: str,
        objects: Optional[List] = None,
        options: Optional[Dict[str, Any]] = None,
        selection_only: bool = True,
        material_names: str = "shader",
        prune_static: bool = False,
        single_root: bool = False,
    ) -> str:
        """Export to a USD file (``.usd``/``.usda``/``.usdc``/``.usdz``).

        An animated export (``frameRange`` in *options*) writes the layer one
        ``mayaUSDExport`` pass would, faster: see :meth:`_maya_usd_export`.

        Parameters:
            file_path: Destination path (``.usd`` appended when no USD
                extension is given; directories are created automatically).
                A ``.usdz`` destination exports a temp text layer and packages
                it self-contained via :meth:`pythontk.UsdzPackager.from_layer`.
            objects: Nodes to export. If *None*, the current selection is used.
            options: ``cmds.mayaUSDExport`` keyword overrides, merged over
                :attr:`_DEFAULT_EXPORT_OPTIONS` (e.g. ``frameRange=(1, 120)``
                for animation, ``stripNamespaces=True``).
            selection_only: If True export selected; if False the whole scene.
            material_names: What a ``Material`` prim is named after --
                ``"shader"`` (default; the surface shader, the identity FBX
                carries, so a material is ``crate_mat`` on every carrier) or
                ``"shading_group"`` (``mayaUSDExport``'s own ``crate_matSG``,
                what a Maya-to-Maya round trip through mayaUsd expects). See
                :meth:`name_materials_after_shaders`.
            prune_static: Also leave the subtrees that never move out of the
                sampled pass, by marking them ``intermediateObject`` for its
                duration (:meth:`_static_roots`). The layer is the same; the
                flags are put back, but each flip is an undoable edit, and a
                reference edit on a referenced node -- so it is for a scene
                opened to be converted, not a user's working scene.
            single_root: Put the layer's root prims under ONE root prim named
                after the file, made the default prim, so a stage that
                references the file receives all of it (:meth:`single_root`).
                For a deliverable; a bridge payload keeps its flat layout.

        Returns:
            The absolute path of the exported file.

        Raises:
            RuntimeError: Nothing selected for a selection export, or export failure.
            ValueError: An unknown *material_names*.
        """
        cls._check_material_names(material_names)
        cls.load_plugin()

        file_path = os.path.abspath(os.path.expandvars(file_path))
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in cls.EXTENSIONS:
            file_path += ".usd"
            ext = ".usd"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        if objects:
            cmds.select([str(o) for o in objects], replace=True)
        if selection_only and not cmds.ls(selection=True):
            raise RuntimeError(
                "Export requested for selection, but nothing is selected."
            )

        opts = dict(cls._DEFAULT_EXPORT_OPTIONS)
        opts.update(options or {})

        if ext == ".usdz":
            # Native mayaUSDExport has no self-contained usdz path; compose
            # the shared packager over a temp TEXT layer (its asset refs are
            # rewritten in-package). Geometry/material fidelity is identical —
            # only the container differs.
            store = ptk.TempArtifacts("mtk_usdz_export", policy="scoped")
            tmp_layer = store.path(extension=".usda")
            try:
                cls._maya_usd_export(tmp_layer, selection_only, opts, prune_static)
                if material_names == "shader":
                    cls.name_materials_after_shaders(tmp_layer)
                if single_root:  # named after the package, not the scratch layer
                    cls.single_root(
                        tmp_layer, os.path.splitext(os.path.basename(file_path))[0]
                    )
                result = ptk.UsdzPackager.from_layer(tmp_layer, file_path)
            finally:
                store.cleanup()
            logger.info(f"Exported USDZ: {result}")
            return result

        cls._maya_usd_export(file_path, selection_only, opts, prune_static)
        if material_names == "shader":
            cls.name_materials_after_shaders(file_path)
        if single_root:
            cls.single_root(file_path)
        logger.info(f"Exported USD: {file_path}")
        return file_path

    @classmethod
    def name_materials_after_shaders(
        cls, file_path: str, mapping: Optional[Dict[str, str]] = None
    ) -> int:
        """Rename the layer's ``Material`` prims from their SHADING GROUP to their
        SURFACE SHADER; return the rename count.

        ``mayaUSDExport`` names a Material prim after the shading engine
        (``crate_matSG``), but every other carrier and consumer identifies a
        material by its shader (``crate_mat``): FBX, the manifests, Toolbag's
        texture sets and Painter's (probed live: a ``.usd`` payload baked as
        ``crate_matSG_AO.png`` and painted under a ``crate_matSG`` set while the
        FBX gave ``crate_mat``). One spelling per material across carriers, or a
        re-bake on the other carrier can never overwrite its own maps.

        *mapping* is ``{material prim name: new name}``; by default it is read
        off the open scene's shading engines, spelled as the exporter spells
        them (:meth:`sanitize_prim_name` -- a namespace survives as ``ns_x``,
        it is not stripped). A rename whose target already exists in the layer,
        or that another rename in the same pass also wants (two shaders
        sanitizing alike), is skipped with a warning rather than merged or
        failed. An ``Sdf`` namespace rename moves the
        subtree but fixes up NOTHING that points at it (probed), so every
        relationship target and attribute connection in the layer is remapped
        here -- ``material:binding`` on meshes and subsets, the material's own
        ``outputs:surface`` connection into its shader.
        """
        from pxr import Sdf, Usd

        if mapping is None:
            mapping = {}
            for sg in cmds.ls(type="shadingEngine") or []:
                shaders = (
                    cmds.listConnections(
                        f"{sg}.surfaceShader", source=True, destination=False
                    )
                    or []
                )
                if shaders:
                    mapping[cls.sanitize_prim_name(sg)] = cls.sanitize_prim_name(
                        shaders[0]
                    )
        layer = Sdf.Layer.FindOrOpen(str(file_path))
        if layer is None or not mapping:
            return 0
        stage = Usd.Stage.Open(layer)
        renames: List[Tuple[Sdf.Path, Sdf.Path]] = []
        claimed: set = set()
        for prim in stage.Traverse():
            if prim.GetTypeName() != "Material":
                continue
            new_name = mapping.get(prim.GetName())
            if not new_name or new_name == prim.GetName():
                continue
            new_path = prim.GetPath().GetParentPath().AppendChild(new_name)
            if stage.GetPrimAtPath(new_path) or new_path in claimed:
                logger.warning(
                    f"USD material {prim.GetPath()} keeps its shading-group name: "
                    f"{new_path} is already taken in the layer."
                )
                continue
            claimed.add(new_path)
            renames.append((prim.GetPath(), new_path))
        if not renames:
            return 0

        edit = Sdf.BatchNamespaceEdit()
        for old, new in renames:
            edit.Add(Sdf.NamespaceEdit.Rename(old, new.name))
        if not layer.Apply(edit):
            raise RuntimeError("USD material rename failed to apply.")

        def remap(path: Sdf.Path) -> Sdf.Path:
            prim_path = path.GetPrimPath()
            for old, new in renames:
                if prim_path == old or prim_path.HasPrefix(old):
                    return path.ReplacePrefix(old, new)
            return path

        for prim in stage.Traverse():
            for rel in prim.GetRelationships():
                targets = rel.GetTargets()
                fixed = [remap(t) for t in targets]
                if fixed != targets:
                    rel.SetTargets(fixed)
            for attr in prim.GetAttributes():
                sources = attr.GetConnections()
                if not sources:
                    continue
                fixed = [remap(s) for s in sources]
                if fixed != sources:
                    attr.SetConnections(fixed)
        layer.Save()
        return len(renames)

    #: ``mayaUSDExport`` flags that put prims somewhere other than their DAG path,
    #: sample other than every whole frame, hide what moves from the one-frame
    #: pass, or run caller code once per pass. The sampled export
    #: (:meth:`_sampled_export`) finds prims BY DAG path, resamples whole frames
    #: and reads motion off the one-frame pass's samples (``staticSingleSample``
    #: writes each lone sample as a default: an expression-driven subtree then
    #: read as static and shipped frozen), and it runs two passes (a callback
    #: would run twice), so any of these sends an export through one plain pass.
    _UNSPLIT_FLAGS = (
        "exportRoots",
        "rootPrim",
        "rootPrimType",
        "parentScope",
        "stripNamespaces",
        "worldspace",
        "exportInstances",
        "frameStride",
        "frameSample",
        "staticSingleSample",
        "melPerFrameCallback",
        "melPostCallback",
        "pythonPerFrameCallback",
        "pythonPostCallback",
    )

    @classmethod
    def _maya_usd_export(
        cls,
        file_path: str,
        selection_only: bool,
        opts: Dict[str, Any],
        prune_static: bool = False,
    ):
        """``cmds.mayaUSDExport`` with per-flag tolerance across mayaUsd versions,
        and an animated export split so that prims which never move stop costing
        time at every frame.

        mayaUsd (0.30, read from its source) runs EVERY prim writer at EVERY
        sampled frame, moving or not, and two interchange flags make that real
        work: ``exportBlendShapes`` walks each mesh's whole upstream graph for a
        blendShape (and warns when it finds none), and ``exportVisibility`` asks
        each writer whether its parent merges, iterating the parent's children.
        So ``exportBlendShapes`` is dropped when the scene holds no blendShape
        (no mesh could export one: the layer is the same), and a sampled export
        goes through :meth:`_sampled_export`, which writes what one pass would
        and falls back to that pass whenever it cannot prove it.

        ``cmds`` rejects the whole call on ONE unknown flag (``TypeError``), so a
        flag this mayaUsd doesn't know is dropped with a log line and the call
        retried (:meth:`_mayausd`) -- the mirror of blendertk's
        ``_filter_op_options`` (Blender renames USD kwargs between majors;
        mayaUsd adds flags between releases). Never drops ``file``.
        """
        opts = dict(opts)
        if opts.get("exportBlendShapes") and not cmds.ls(type="blendShape"):
            opts["exportBlendShapes"] = False
        if opts.get("frameRange") and cls._sampled_export(
            file_path, selection_only, opts, prune_static
        ):
            return None
        return cls._mayausd(file_path, selection_only, opts)

    @staticmethod
    def _mayausd(
        file_path: str, selection_only: bool, opts: Dict[str, Any], **overrides
    ):
        """One ``cmds.mayaUSDExport`` of *opts* with *overrides*. A flag this mayaUsd
        does not know is dropped from *opts* too, so a later pass of the same
        export neither retries nor re-reports it."""
        call = dict(opts, **overrides)
        while True:
            try:
                return cmds.mayaUSDExport(
                    file=file_path, selection=selection_only, **call
                )
            except TypeError as error:
                match = re.search(r"'(\w+)'", str(error))
                key = match.group(1) if match else None
                if key and key in call:
                    logger.warning(
                        f"USD export flag unknown to this mayaUsd dropped: {key}"
                    )
                    del call[key]
                    opts.pop(key, None)
                    continue
                raise

    @classmethod
    def _sampled_export(
        cls,
        file_path: str,
        selection_only: bool,
        opts: Dict[str, Any],
        prune_static: bool = False,
    ) -> bool:
        """Write *file_path* as one sampled ``mayaUSDExport`` pass would, in three
        steps; ``False`` (nothing proven, *file_path* to be overwritten) whenever
        the result cannot be shown to be that pass's.

        1. A ONE-frame pass with every flag: the whole layer as the full pass
           writes it, but for the length of each sampled attribute's samples.
        2. The sampled pass WITHOUT visibility -- and, with *prune_static*,
           without the subtrees that never move (:meth:`_static_roots`).
        3. The first layer, with what the second wrote for every prim it holds,
           and the prims whose visibility mayaUsd samples resampled here
           (:meth:`_animated_visibility`) -- :meth:`_merge_samples`, which also
           holds every check.

        Measured on a production module (4742 frames, 5775 prims): 776-944 s in
        one pass, 638 s with visibility apart, 198 s with *prune_static* too
        (2722 of 3754 DAG nodes left out; the sampled pass at 38 ms a frame), the
        layer the same to the last sample.
        """
        if any(opts.get(flag) for flag in cls._UNSPLIT_FLAGS):
            return False
        visibility = opts.get("exportVisibility", True)
        if not (visibility or prune_static):
            return False
        try:
            from pxr import Sdf  # noqa: F401 -- Maya's own USD
        except ImportError:
            return False
        start, end = (float(v) for v in opts["frameRange"])
        # mayaUsd's own time samples: the start, then every whole frame to the end.
        frames = [start + i for i in range(int(math.floor(end - start + 1e-6)) + 1)]
        current = cmds.currentTime(query=True)
        store = ptk.TempArtifacts("mtk_usd_sampled", policy="scoped")
        flipped: Dict[str, Any] = {}
        try:
            return cls._split_export(
                file_path,
                store.path(extension=".usd"),
                selection_only,
                opts,
                frames,
                current,
                flipped if prune_static else None,
            )
        except Exception:  # noqa: BLE001 -- a shortcut never fails an export
            logger.warning("USD: split export failed; one pass instead.", exc_info=True)
            return False
        finally:
            for node, value in flipped.items():
                try:
                    cmds.setAttr(f"{node}.intermediateObject", value)
                except RuntimeError:
                    logger.warning(f"USD: {node} kept intermediateObject on.")
            cmds.currentTime(current, update=True)
            store.cleanup()

    @classmethod
    def _split_export(
        cls,
        file_path: str,
        reference: str,
        selection_only: bool,
        opts: Dict[str, Any],
        frames: List[float],
        current: float,
        flipped: Optional[Dict[str, Any]],
    ) -> bool:
        """:meth:`_sampled_export`'s steps. Its own frame, so every layer it opens is
        released before the caller removes the reference file. *flipped*, when
        given, receives ``{node: its intermediateObject}`` for each subtree left
        out of the sampled pass (the caller puts them back)."""
        from pxr import Sdf

        cls._mayausd(reference, selection_only, opts, frameRange=(frames[0],) * 2)
        ref = Sdf.Layer.FindOrOpen(reference)
        scope = cls._export_scope() if selection_only else None
        writers: Dict[str, List[str]] = {}
        if opts.get("exportVisibility", True):
            writers = cls._animated_visibility(ref, scope)
            if writers is None:
                logger.info("USD: an animated visibility has no prim; one pass.")
                return False
        if flipped is not None:
            for node in cls._static_roots(ref, writers, scope):
                try:
                    flipped[node] = cmds.getAttr(f"{node}.intermediateObject")
                    cmds.setAttr(f"{node}.intermediateObject", True)
                except RuntimeError:
                    flipped.pop(node, None)  # locked or connected: it stays in
        cls._mayausd(file_path, selection_only, opts, exportVisibility=False)
        for node, value in list(flipped.items()) if flipped else []:
            cmds.setAttr(f"{node}.intermediateObject", value)
            del flipped[node]
        # mayaUsd writes through this process's layer registry (a layer held open
        # at the path is cleared and rewritten -- probed), so this is current.
        sampled = Sdf.Layer.FindOrOpen(file_path)
        merged = cls._merge_samples(ref, sampled, writers, frames, current)
        if merged is None:
            return False
        times = sampled.startTimeCode, sampled.endTimeCode
        sampled.TransferContent(merged)
        sampled.startTimeCode, sampled.endTimeCode = times
        sampled.Save()
        logger.info(
            f"USD: sampled {len(frames)} frame(s) in a split export"
            + (f", {len(writers)} visibility track(s) resampled" if writers else "")
        )
        return True

    @classmethod
    def _merge_samples(
        cls,
        ref: Any,
        sampled: Any,
        writers: Dict[str, List[str]],
        frames: List[float],
        current: float,
    ) -> Optional[Any]:
        """The full pass's layer, from its two halves.

        *ref* (the one-frame pass) gives every prim and every default the full
        pass writes at the default time. Each attribute of a prim *sampled* (the
        full-range pass) also wrote is taken whole from *sampled*: its samples,
        and any default a writer re-authors at every frame (blendshape weights end
        on the LAST frame's). A SkelRoot's ``extent`` keeps *ref*'s default --
        every mesh writes it there, so a mesh left out changes which wrote last --
        and takes *sampled*'s samples, written only by a mesh whose bounds change.
        Each *writers* prim's visibility is resampled (:meth:`_visibility_layer`).
        A prim only *sampled* wrote (a materials scope that follows the first
        root) contributes nothing. ``None`` unless each of these holds:

        * a prim both wrote carries the same attributes in both, visibility aside
          (leaving a subtree out cannot have re-merged a shape into its parent);
        * each attribute *ref* samples (visibility aside) is sampled in *sampled*,
          the same at the first frame, where *ref*'s one sample lies;
        * every visibility *ref* samples belongs to *writers*, and theirs,
          resampled at the first frame, is *ref*'s exactly.
        """
        from pxr import Sdf

        start = frames[0]
        found = []
        ref.Traverse(ref.pseudoRoot.path, found.append)
        in_ref = {
            p for p in found if p.IsPropertyPath() and ref.GetNumTimeSamplesForPath(p)
        }
        tracks = {Sdf.Path(w).AppendProperty("visibility") for w in writers}
        for path in sorted(in_ref - tracks):
            if path.name == "visibility":
                logger.info(f"USD: {path} is sampled unasked; one pass.")
                return None
            if not sampled.GetNumTimeSamplesForPath(path) or not cls._same(
                ref.QueryTimeSample(path, start),
                sampled.QueryTimeSample(path, start),
            ):
                logger.info(f"USD: {path} differs at frame {start:g}; one pass.")
                return None
        shared = []
        for path in (p for p in found if p.IsPrimPath()):
            theirs = sampled.GetPrimAtPath(path)
            if theirs is None:
                continue  # left out: all of it is in *ref*
            names = set(theirs.attributes.keys())
            if names != set(ref.GetPrimAtPath(path).attributes.keys()) - {"visibility"}:
                logger.info(f"USD: {path} is written differently apart; one pass.")
                return None
            shared.append((path, theirs.typeName == "SkelRoot", names))

        merged = Sdf.Layer.CreateAnonymous(".usd")
        merged.TransferContent(ref)
        for prim, skel_root, names in shared:
            for name in names:
                path = prim.AppendProperty(name)
                if skel_root and name == "extent":
                    merged.GetAttributeAtPath(path).ClearInfo("timeSamples")
                    for time in sampled.ListTimeSamplesForPath(path):
                        merged.SetTimeSample(
                            path, time, sampled.QueryTimeSample(path, time)
                        )
                elif not Sdf.CopySpec(sampled, path, merged, path):
                    return None
        if writers:
            first = cls._visibility_layer(ref, writers, frames[:1], current)
            if any(
                cls._attr_state(first, p) != cls._attr_state(ref, p) for p in tracks
            ):
                logger.info("USD: resampled visibility differs; one pass.")
                return None
            full = cls._visibility_layer(ref, writers, frames, current)
            for path in tracks:
                if full.GetAttributeAtPath(path):
                    if not Sdf.CopySpec(full, path, merged, path):
                        return None
                elif merged.GetAttributeAtPath(path):
                    prim = merged.GetPrimAtPath(path.GetPrimPath())
                    prim.RemoveProperty(prim.properties["visibility"])
        return merged

    @staticmethod
    def _same(a: Any, b: Any) -> bool:
        """``a == b`` for two USD values, arrays included (a Vt array may compare
        element-wise)."""
        try:
            return bool(a == b)
        except (TypeError, ValueError):
            return list(a) == list(b)

    @staticmethod
    def _export_scope() -> set:
        """Every DAG path a selection export writes: the selected nodes, all their
        descendants, and every ancestor (written as the path to them)."""
        selected = cmds.ls(selection=True, long=True, type="dagNode") or []
        scope = set(selected)
        scope.update(
            cmds.listRelatives(selected, allDescendents=True, fullPath=True) or []
            if selected
            else []
        )
        for node in selected:
            parts = node.split("|")
            scope.update("|".join(parts[:i]) for i in range(2, len(parts)))
        return scope

    @classmethod
    def _prim_path(cls, dag_path: str) -> str:
        """*dag_path*'s prim path as ``mayaUSDExport`` writes it (merged, not
        re-rooted): each segment through :meth:`sanitize_prim_name`."""
        return "/" + "/".join(
            cls.sanitize_prim_name(s) for s in dag_path.split("|") if s
        )

    @classmethod
    def _animated_visibility(
        cls, layer: Any, scope: Optional[set] = None
    ) -> Optional[Dict[str, List[str]]]:
        """``{prim path: [visibility plugs]}`` for every prim whose visibility mayaUsd
        samples, or ``None`` when one of them has no prim in *layer*. *scope*
        limits the nodes asked (a selection export's :meth:`_export_scope`).

        mayaUsd 0.30's ``UsdMayaPrimWriter::Write``, restated from its source: a
        prim's visibility is its node's ``visibility``, AND its transform's when
        the prim is a shape merged into it, and it is sampled when either plug is
        animated by ``UsdMayaUtil::isPlugAnimated`` -- ``MAnimUtil`` on the plug
        or on the node driving it (so an expression drives nothing, and a flat
        curve still does). A joint writes none (the skeleton writer). Merged or
        not is read off *layer*: an unmerged shape keeps a prim of its own.

        Proven on mayaUsd 0.30.0 only, and a missed prim whose first value equals
        the fallback would pass the frame-one check: ``test_usd`` pins that
        version, so an upgrade fails until the equivalence proof is re-run.
        """
        import maya.api.OpenMaya as om
        import maya.api.OpenMayaAnim as oma

        def animated(plug):
            if oma.MAnimUtil.isAnimated(plug):
                return True
            if plug.isDestination:
                source = plug.source()
                return not source.isNull and oma.MAnimUtil.isAnimated(source.node())
            return False

        nodes = cmds.ls(dag=True, long=True, allPaths=True, noIntermediate=True) or []
        if scope is not None:
            nodes = [n for n in nodes if n in scope]
        shapes = set(cmds.ls(nodes, shapes=True, long=True) or [])
        joints = set(cmds.ls(nodes, type="joint", long=True) or [])
        writers: Dict[str, set] = {}
        for node in nodes:
            if node in joints:
                continue
            selection = om.MSelectionList()
            selection.add(node)
            try:
                plug = om.MFnDependencyNode(selection.getDependNode(0)).findPlug(
                    "visibility", True
                )
            except RuntimeError:
                continue
            if not animated(plug):
                continue
            plugs = [node]
            if node in shapes:
                path = cls._prim_path(node)
                if not layer.GetPrimAtPath(path):  # merged into its transform's
                    path = cls._prim_path(node.rsplit("|", 1)[0])
                    plugs.append(node.rsplit("|", 1)[0])
            else:
                path = cls._prim_path(node)
                children = (
                    cmds.listRelatives(
                        node, shapes=True, noIntermediate=True, fullPath=True
                    )
                    or []
                )
                if len(children) == 1 and not layer.GetPrimAtPath(
                    cls._prim_path(children[0])
                ):
                    plugs.append(children[0])  # merged: its shape writes the prim
            if not layer.GetPrimAtPath(path):
                return None
            writers.setdefault(path, set()).update(p + ".visibility" for p in plugs)
        return {path: sorted(plugs) for path, plugs in writers.items()}

    @classmethod
    def _static_roots(
        cls,
        layer: Any,
        writers: Dict[str, List[str]],
        scope: Optional[set] = None,
    ) -> List[str]:
        """The topmost transforms whose whole subtree can sit out a sampled pass.

        Read off *layer*, the one-frame pass: a subtree qualifies when no prim
        in it has a sample there or a visibility track (*writers*), none is a
        skeleton prim or bound to one, no node in it is animated to
        ``MAnimUtil`` or a joint, and it hangs under no shape's transform
        (leaving it out would make that transform mergeable, moving its shape's
        prim). A transform with several parents, or whose prim path another
        node shares, stays in; so does every node a selection names, and its
        ancestors.
        """
        import maya.api.OpenMaya as om
        import maya.api.OpenMayaAnim as oma
        from pxr import Sdf

        found = []
        layer.Traverse(layer.pseudoRoot.path, found.append)
        hot = {Sdf.Path(w) for w in writers}
        for path in found:
            if path.IsPropertyPath():
                if layer.GetNumTimeSamplesForPath(path) or (
                    path.name == "skel:skeleton"
                    or path.name.startswith("primvars:skel:")
                ):
                    hot.add(path.GetPrimPath())
            elif path.IsPrimPath() and layer.GetPrimAtPath(path).typeName in (
                "SkelRoot",
                "Skeleton",
                "SkelAnimation",
            ):
                hot.add(path)
        warm = set()
        for path in hot:
            warm.update(path.GetAncestorsRange())

        transforms = cmds.ls(type="transform", long=True) or []
        if scope is not None:
            transforms = [t for t in transforms if t in scope]
        pinned = set()
        for node in cmds.ls(type="joint", long=True) or []:
            parts = node.split("|")
            pinned.update("|".join(parts[:i]) for i in range(2, len(parts) + 1))
        for node in (
            cmds.ls(selection=True, long=True, type="dagNode") or [] if scope else []
        ):
            parts = node.split("|")
            pinned.update("|".join(parts[:i]) for i in range(2, len(parts) + 1))
        for node in cmds.ls(type="shape", long=True, noIntermediate=True) or []:
            pinned.update(
                c
                for c in cmds.listRelatives(
                    node.rsplit("|", 1)[0],
                    children=True,
                    type="transform",
                    fullPath=True,
                )
                or []
            )
        # Anything MAnimUtil calls animated stays in, whatever the one frame shows
        # (a track that starts on its default writes nothing at the first frame).
        for node in transforms + (cmds.ls(shapes=True, long=True) or []):
            selection = om.MSelectionList()
            selection.add(node)
            if oma.MAnimUtil.isAnimated(selection.getDependNode(0)):
                parts = node.split("|")
                pinned.update("|".join(parts[:i]) for i in range(2, len(parts) + 1))

        owners: Dict[str, int] = {}
        for node in transforms:
            key = cls._prim_path(node)
            owners[key] = owners.get(key, 0) + 1
        roots = []
        chosen = set()
        for node in sorted(transforms, key=lambda n: n.count("|")):
            path = cls._prim_path(node)
            if (
                node in pinned
                or owners[path] != 1
                or not layer.GetPrimAtPath(path)
                or Sdf.Path(path) in warm
                or len(cmds.listRelatives(node, allParents=True) or []) > 1
                or any(node.startswith(r + "|") for r in chosen)
            ):
                continue
            chosen.add(node)
            roots.append(node)
        return roots

    @staticmethod
    def _visibility_layer(
        layer: Any,
        writers: Dict[str, List[str]],
        frames: List[float],
        current: float,
    ) -> Any:
        """A scratch layer holding each *writers* prim's visibility as mayaUsd
        authors it: read at *current* for the default (mayaUsd reads it before
        stepping), then at each of *frames*, all through
        ``UsdUtils.SparseValueWriter`` -- the writer mayaUsd authors through, so a
        fallback-equal default and a held value come out the same. Prims keep
        *layer*'s types: the fallback is the schema's."""
        from pxr import Sdf, Usd, UsdGeom
        from pxr import UsdUtils as PxrUsdUtils

        scratch = Sdf.Layer.CreateAnonymous(".usda")
        stage = Usd.Stage.Open(scratch)
        attrs = {}
        for path in writers:
            prim = stage.DefinePrim(path, layer.GetPrimAtPath(path).typeName)
            attrs[path] = UsdGeom.Imageable(prim).CreateVisibilityAttr(None, True)
        writer = PxrUsdUtils.SparseValueWriter()
        for time in [None] + list(frames):
            cmds.currentTime(current if time is None else time, update=True)
            code = Usd.TimeCode.Default() if time is None else Usd.TimeCode(time)
            for path, plugs in writers.items():
                visible = all(cmds.getAttr(plug) for plug in plugs)
                token = (
                    UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible
                )
                writer.SetAttribute(attrs[path], token, code)
        return scratch

    @staticmethod
    def _attr_state(layer: Any, path: Any) -> Optional[tuple]:
        """``(default, [(time, value)...])`` authored for *path* in *layer* -- ``None``
        when nothing is (no spec, or a spec holding neither)."""
        spec = layer.GetAttributeAtPath(path)
        samples = [
            (t, layer.QueryTimeSample(path, t))
            for t in layer.ListTimeSamplesForPath(path)
        ]
        if not spec or (not spec.HasDefaultValue() and not samples):
            return None
        return (spec.default if spec.HasDefaultValue() else None, samples)

    @classmethod
    def sampling_frame_range(
        cls, objects: Optional[List[str]] = None
    ) -> Optional[Tuple[float, float]]:
        """The frames a USD export is worth sampling, or ``None`` for a static one.

        USD has no animation *curves* -- ``frameRange`` makes ``mayaUSDExport``
        write a time sample per frame for EVERY prim, so the range is a direct
        multiplier on export cost (measured on a 755-mesh production module with
        a 1-200 playback range: 234s with the full range vs 1.8s static,
        byte-identical result). So sample only what moves:

        * nothing animated -> ``None`` (static export)
        * unkeyed drivers present (constraints, expressions, IK, motion paths)
          -> the full playback range: their motion can't be read off key times
        * plain keyframes only -> the keys' own span, clamped to the playback
          range (a stray far-out key must not multiply the sample count, and
          frames outside the scene's own time are not what the user sees)

        Only TIME-based curves count (``animCurveT*``): ``animCurveU*`` are
        set-driven keys whose "times" are driver *values*, not frames.

        Parameters:
            objects: Scope the question to these nodes' hierarchies (a selection
                send). ``None`` asks the whole scene.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        playback = AnimUtils.scene_animation_range()
        if objects:
            scope = list(objects) + (
                cmds.listRelatives(objects, allDescendents=True, fullPath=True) or []
            )
            # Deformer weights (blendShape / skinCluster) are keyed on nodes in
            # the shapes' HISTORY, not on the DAG nodes themselves.
            scope += cmds.listHistory(scope, pruneDagObjects=True) or []
            drivers = cmds.ls(scope, type=cls._UNKEYED_DRIVERS) or []
            curves = (
                cmds.listConnections(
                    scope, source=True, destination=False, type="animCurve"
                )
                or []
            )
            curves = (
                cmds.ls(
                    curves,
                    type=("animCurveTA", "animCurveTL", "animCurveTU", "animCurveTT"),
                )
                or []
            )
        else:
            drivers = cmds.ls(type=cls._UNKEYED_DRIVERS) or []
            curves = (
                cmds.ls(
                    type=("animCurveTA", "animCurveTL", "animCurveTU", "animCurveTT")
                )
                or []
            )
        if drivers:
            return playback
        if not curves:
            return None
        # ONE query for every curve -- per-curve calls cost a round trip each.
        times = cmds.keyframe(curves, query=True, timeChange=True) or []
        if not times:
            return None
        # floor/ceil, not int(): int() truncates toward zero, clipping a key at
        # 20.5 down to 20 (losing motion) and mis-rounding negative frames.
        start = max(playback[0], math.floor(min(times)))
        end = min(playback[1], math.ceil(max(times)))
        if end < start:  # keys live entirely outside the scene's own time
            return None
        return (float(start), float(end))

    @staticmethod
    def options_string(options: Dict[str, Any]) -> str:
        """*options* as a ``cmds.file`` translator options string: ``key=value``
        pairs joined by ``;``, bools as ``0``/``1``, a list as the translator's
        bracket grammar (``[[st,map1]]`` -- no spaces, no quotes)."""

        def spell(value: Any) -> str:
            if isinstance(value, bool):
                return str(int(value))
            if isinstance(value, (list, tuple)):
                return "[" + ",".join(spell(v) for v in value) + "]"
            return str(value)

        return ";".join(f"{key}={spell(value)}" for key, value in options.items())

    @classmethod
    def file_options(
        cls, options: Optional[Dict[str, Any]] = None, read_animation: bool = True
    ) -> Dict[str, str]:
        """The ``cmds.file`` keywords that read a USD layer the interchange way --
        ``{"type": IMPORT_TRANSLATOR, "options": ...}`` -- for an import, a reference
        or an open alike.

        Name the translator on every read: left to pick one by extension, Maya reads
        with the translator's own defaults, and ``readAnimData`` defaults OFF --
        measured, an untyped reference of a keyed stage arrived static. A reference
        or an open also STORES both (the saved scene records ``-typ`` / ``-op``), so
        every reload reads the stage the same way.

        Parameters:
            options: Translator options merged over
                :attr:`INTERCHANGE_IMPORT_OPTIONS`; an explicit ``readAnimData``
                entry wins over *read_animation*.
            read_animation: Read the stage's time samples as keys.

        Returns:
            The keywords to splat into ``cmds.file``.
        """
        merged = dict(cls.INTERCHANGE_IMPORT_OPTIONS, readAnimData=bool(read_animation))
        merged.update(options or {})
        return {"type": cls.IMPORT_TRANSLATOR, "options": cls.options_string(merged)}

    @staticmethod
    def author_lod_variants(usd_path: str, plan: List[Dict[str, Any]]) -> List[str]:
        """State each LOD group of *plan* as a variant set ``LOD`` in the layer
        at *usd_path*, in place: variants ``LOD0..n`` (the convention engines'
        USD importers read), each holding its one level, LOD0 selected -- what
        a reader without LOD support composes. *plan* is
        ``ptk.LodChain.variant_plan``.

        A group is found by name among the layer's prims, its levels among its
        children (each host's writer re-roots or sanitizes its paths its own
        way); a group found nowhere is left out of the return, for the caller
        to say. A layer this session already holds is read again from disk
        first (the writer just replaced it), and a group whose levels the
        layer cannot move is left as written. Identical in mayatk and
        blendertk (``check_dcc_twins.py``): pure pxr.

        Returns:
            The groups stated, by prim path.
        """
        from pxr import Sdf

        layer = Sdf.Layer.Find(str(usd_path))
        if layer is not None:
            layer.Reload(True)  # forced: an unforced reload trusts the file's time stamp
        else:
            layer = Sdf.Layer.FindOrOpen(str(usd_path))
        if layer is None:
            return []
        prims: List[Any] = []
        layer.Traverse(
            Sdf.Path.absoluteRootPath,
            lambda path: prims.append(path)
            if path.IsPrimPath() and not path.ContainsPrimVariantSelection()
            else None,
        )
        def children(p):
            spec = layer.GetPrimAtPath(p)
            return set(spec.nameChildren.keys()) if spec is not None else set()

        # Every group found before any is edited, then the deepest first: an
        # outer group's edit moves the inner one's prims into its variant, so
        # read after it they were gone (2026-11-10 review: nested groups raised).
        found = []
        for entry in plan:
            levels = list(entry["levels"])
            path = next((p for p in prims if p.name == entry["group"] and set(levels) <= children(p)), None)
            if path is not None:
                found.append((path, entry, levels))
        found.sort(key=lambda item: -item[0].pathElementCount)
        stated = []
        for path, entry, levels in found:
            spec = layer.GetPrimAtPath(path)
            if spec is None or "LOD" in spec.variantSets:
                continue
            edit = Sdf.BatchNamespaceEdit()
            for level in levels:
                edit.Add(path.AppendChild(level), Sdf.Path.emptyPath)
            if not layer.CanApply(edit):
                continue
            variant_set = Sdf.VariantSetSpec(spec, "LOD")
            for variant, level in zip(entry["variants"], levels):
                Sdf.VariantSpec(variant_set, variant)
                Sdf.CopySpec(
                    layer,
                    path.AppendChild(level),
                    layer,
                    path.AppendVariantSelection("LOD", variant).AppendChild(level),
                )
            spec.variantSetNameList.Prepend("LOD")
            layer.Apply(edit)
            spec.variantSelections["LOD"] = entry["variants"][0]
            stated.append(str(path))
        if stated:
            layer.Save()
        return stated

    @staticmethod
    def expand_lod_variants(usd_path: str, dst: Optional[str] = None) -> Optional[str]:
        """A copy of the layer at *usd_path* with every ``LOD`` variant set
        expanded into its levels as plain children, for a host import that
        would otherwise read the selected variant alone.

        A set is expanded only when every variant is named ``LOD<n>`` within
        ``ptk.LodChain.MAX_LEVELS`` (any other set called ``LOD`` is no level
        set, and stays). A variant holding one prim gives it the level's name,
        ``<group>_LOD<n>``; one holding several keeps their names, each name
        another variant already took getting ``_LOD<n>`` -- so no level
        overwrites another. Read off the layer's own specs (no stage is
        composed), written to *dst* (default a ``ptk.TempArtifacts`` file),
        its asset paths made absolute so the copy reads the source's textures
        and sublayers from anywhere. Identical in mayatk and blendertk
        (``check_dcc_twins.py``): pure pxr.

        Returns:
            The expanded copy's path, or None when the layer states no LOD
            variant set (import the source as it is).
        """
        import os
        import re

        from pxr import Sdf
        from pxr import UsdUtils as PxrUsdUtils

        layer = Sdf.Layer.FindOrOpen(str(usd_path))
        if layer is None:
            return None
        named = re.compile(r"^LOD(\d+)$")

        def levels_of(spec) -> Optional[List[Tuple[int, Any]]]:
            if spec is None or "LOD" not in spec.variantSets:
                return None
            variants = spec.variantSets["LOD"].variants
            indices = {name: named.match(name) for name in variants.keys()}
            if not indices or not all(indices.values()):
                return None
            levels = sorted((int(m.group(1)), variants[name]) for name, m in indices.items())
            return levels if levels[-1][0] < ptk.LodChain.MAX_LEVELS else None

        found: List[Any] = []
        layer.Traverse(
            Sdf.Path.absoluteRootPath,
            lambda path: found.append(path)
            if path.IsPrimPath()
            and not path.ContainsPrimVariantSelection()
            and levels_of(layer.GetPrimAtPath(path))
            else None,
        )
        if not found:
            return None
        copy = Sdf.Layer.CreateAnonymous(".usda")
        copy.TransferContent(layer)
        for path in found:
            spec = copy.GetPrimAtPath(path)
            taken = set(spec.nameChildren.keys())
            for index, variant in levels_of(spec):
                children = list(variant.primSpec.nameChildren.values())
                for child in children:
                    if len(children) == 1:
                        target = ptk.LodChain.level_name(path.name, index)
                    else:
                        target = child.name if child.name not in taken else f"{child.name}_LOD{index}"
                    if target in taken:
                        continue  # a direct child of that name stays as authored
                    taken.add(target)
                    Sdf.CopySpec(copy, child.path, copy, path.AppendChild(target))
            del spec.variantSets["LOD"]
            if "LOD" in spec.variantSelections:
                del spec.variantSelections["LOD"]
            names = spec.variantSetNameList
            for items in (names.explicitItems, names.prependedItems, names.appendedItems):
                if "LOD" in items:
                    items.remove("LOD")
        PxrUsdUtils.ModifyAssetPaths(
            copy,
            lambda asset: layer.ComputeAbsolutePath(asset)
            if asset and not os.path.isabs(asset)
            else asset,
        )
        dst = dst or ptk.TempArtifacts("usd_lods").path(extension=".usda")
        copy.Export(dst)
        return dst

    @classmethod
    def single_root(cls, usd_path: str, name: Optional[str] = None) -> Optional[str]:
        """Put every root prim of the layer at *usd_path* under ONE root prim,
        in place, and make it the layer's default prim; return its path, or
        None when the layer was already so.

        A stage that references a file receives its default prim alone.
        ``mayaUSDExport`` leaves one root per exported root object and puts a
        material several roots share beside them, so a reference dropped the
        other roots and every material bound across them (measured 2026-11-13:
        1 of 2 meshes, no material). Opening or importing the file was never
        affected. For a layer this module's export wrote.

        The root is an ``Xform`` named after the file (*name*, spelled by
        :meth:`sanitize_prim_name`). A layer with one root keeps it: only its
        default prim is stated where none was. Several roots all move, one
        already bearing the name included (``/crate`` becomes ``/crate/crate``:
        one rule, whatever the roots are called). Every path that pointed at a
        moved prim follows it -- relationship targets, attribute connections,
        inherits, specializes, internal references and payloads (one naming no
        prim meant the default prim, and now names where that went), inside
        variants too. Not rewritten: layer metadata that names a prim
        (``relocates``, a render-settings path) and path expressions; no
        exporter here writes them. The file is read again from disk first (the
        writer just replaced it), so a held layer's unsaved edits are dropped.

        A pass after the write, not ``mayaUSDExport``'s ``rootPrim``: that flag
        moves prims, which sends an animated export through one slow pass
        (:attr:`_UNSPLIT_FLAGS`). Copies, not a namespace edit: moving the
        roots one by one is quadratic in their count (10,000 roots: 7.4 s
        edited, 0.8 s copied). blendertk uses its exporter's own
        ``root_prim_path``.

        Parameters:
            usd_path: The layer to edit (not a ``.usdz``: pxr cannot save into
                a package).
            name: The root's name; default the file's stem.

        Returns:
            The root prim's path, or None when nothing changed.

        Raises:
            ValueError: The layer composes sublayers: their prims are not this
                layer's to move.
        """
        from pxr import Sdf

        layer = Sdf.Layer.Find(str(usd_path))
        if layer is not None:
            layer.Reload(True)  # forced: an unforced reload trusts the file's time stamp
        else:
            layer = Sdf.Layer.FindOrOpen(str(usd_path))
        if layer is None:
            return None
        if layer.subLayerPaths:
            raise ValueError(
                f"{usd_path} composes sublayers, whose prims are not this layer's "
                "to move: flatten the stage first."
            )
        roots = [spec.name for spec in layer.rootPrims]
        if not roots:
            return None
        default = layer.defaultPrim.strip("/")
        if len(roots) == 1:
            if default.split("/")[0] == roots[0]:
                return None  # the root, or a prim under it, by the author's choice
            layer.defaultPrim = roots[0]
            layer.Save()
            return f"/{roots[0]}"
        name = cls.sanitize_prim_name(
            name or os.path.splitext(os.path.basename(str(usd_path)))[0]
        )
        top = Sdf.Path.absoluteRootPath
        # Staged under a name no root bears: a path already under it is one the
        # copy remapped, and every other still names a prim where the writer
        # left it. A root bearing the file's name is then told from the new root.
        holder = name
        while holder in roots:
            holder = f"_{holder}"
        staged = top.AppendChild(holder)
        was_default = staged.AppendPath(default) if default else None

        def moved(path):
            if not path.IsAbsolutePath() or path.HasPrefix(staged):
                return path
            return path.ReplacePrefix(top, staged)

        def moved_arc(arc, kind):
            if arc.assetPath:
                return arc  # another file's prim
            if arc.primPath.isEmpty:  # the default prim: the new root itself, left so
                if was_default is None:
                    return arc
                target = was_default
            else:
                target = moved(arc.primPath)
            if kind is Sdf.Payload:
                return Sdf.Payload("", target, arc.layerOffset)
            return Sdf.Reference("", target, arc.layerOffset, arc.customData)

        staging = Sdf.Layer.CreateAnonymous(".usda")
        with Sdf.ChangeBlock():
            Sdf.PrimSpec(staging, holder, Sdf.SpecifierDef, "Xform")
            for child in roots:  # the copy remaps what points inside each root
                Sdf.CopySpec(
                    layer, top.AppendChild(child), staging, staged.AppendChild(child)
                )
        specs: List[Any] = []
        staging.Traverse(top, lambda path: specs.append(path))
        with Sdf.ChangeBlock():  # and this, what points from one root into another
            for path in specs:
                if path.IsPrimPath() or path.IsPrimVariantSelectionPath():
                    spec = staging.GetPrimAtPath(path)
                    if spec is None:
                        continue
                    spec.inheritPathList.ModifyItemEdits(moved)
                    spec.specializesList.ModifyItemEdits(moved)
                    if spec.hasReferences:
                        spec.referenceList.ModifyItemEdits(
                            lambda arc: moved_arc(arc, Sdf.Reference)
                        )
                    if spec.hasPayloads:
                        spec.payloadList.ModifyItemEdits(
                            lambda arc: moved_arc(arc, Sdf.Payload)
                        )
                elif path.IsPropertyPath():
                    spec = staging.GetPropertyAtPath(path)
                    if isinstance(spec, Sdf.RelationshipSpec):
                        spec.targetPathList.ModifyItemEdits(moved)
                    elif isinstance(spec, Sdf.AttributeSpec):
                        spec.connectionPathList.ModifyItemEdits(moved)
        with Sdf.ChangeBlock():
            layer.rootPrims.clear()
            # Copied back under its own name, which carries every staged path over.
            Sdf.CopySpec(staging, staged, layer, top.AppendChild(name))
            layer.defaultPrim = name
        layer.Save()
        return f"/{name}"

    @staticmethod
    def skinning_methods(usd_path: str) -> Dict[str, str]:
        """``{prim path: skinning method}`` for every prim of the stage that authors
        one -- ``"dualQuaternion"`` or ``"classicLinear"`` (``UsdSkelBindingAPI``'s
        ``skinningMethod``). ``{}`` when the stage opens to nothing; a layer pxr
        cannot read raises its ``Tf.ErrorException`` (a ``RuntimeError``) --
        measured on damaged, truncated and empty layers alike.

        The mirror of ``btk.UsdUtils.skinning_methods``, and the pull side of the
        same contract: mayaUsd WRITES this attribute for a dual-quaternion
        skinCluster but does not read it back, so an incoming skin binds linear
        whatever the layer says. :meth:`BlenderSceneImport._apply_skinning_methods`
        is what closes that.
        """
        from pxr import Usd, UsdSkel

        stage = Usd.Stage.Open(str(usd_path))
        if stage is None:
            return {}
        out: Dict[str, str] = {}
        for prim in stage.Traverse():
            attr = UsdSkel.BindingAPI(prim).GetSkinningMethodAttr()
            if attr and attr.HasAuthoredValue():
                out[str(prim.GetPath())] = str(attr.Get())
        return out

    #: The ``skinningMethod`` value mayaUsd 0.30's importer CRASHES on -- see
    #: :meth:`dq_safe_source`. Maya segfaults; there is no exception to catch.
    _CRASHING_SKINNING_METHOD = "dualQuaternion"

    @classmethod
    def _crashing_prims(cls, methods: Dict[str, str]) -> List[str]:
        """The prim paths in *methods* (``{prim path: method}``) whose skin crashes
        the reader, sorted."""
        return sorted(
            path
            for path, method in methods.items()
            if method == cls._CRASHING_SKINNING_METHOD
        )

    @classmethod
    def crashing_skins(cls, usd_path: str) -> List[str]:
        """Prim paths of the stage whose skin mayaUsd 0.30's reader crashes on
        (``skinningMethod = dualQuaternion`` -- see :meth:`dq_safe_source`).

        Parameters:
            usd_path: The layer to scan.

        Returns:
            The prim paths, sorted; ``[]`` when there are none.

        Raises:
            RuntimeError: pxr cannot read the layer (:meth:`skinning_methods`).
        """
        return cls._crashing_prims(cls.skinning_methods(usd_path))

    @classmethod
    def live_read_options(
        cls,
        file_path: str,
        options: Optional[Dict[str, Any]] = None,
        read_animation: bool = True,
    ) -> Dict[str, str]:
        """:meth:`file_options` for reading *file_path* LIVE -- a reference or an
        open -- once the stage is proven safe to hand the reader. Loads the plugin.

        Two stages are refused, both measured. One whose skins crash the reader:
        a live read has no way around them -- the overlay :meth:`import_scene`
        composes instead (:meth:`dq_safe_source`) is a session temp file, and a
        reference or an open is read again from its path on every load -- and a
        plain reference of a DQ-skinned layer took mayapy down with an access
        violation, exactly as the import does. And a layer pxr cannot read at all:
        the translator does not fail on it, it leaves an EMPTY reference node
        behind (damaged, truncated and empty layers alike).

        Parameters:
            file_path: The USD layer or package.
            options, read_animation: As :meth:`file_options`.

        Returns:
            The ``cmds.file`` keywords for the read.

        Raises:
            FileNotFoundError: *file_path* does not exist.
            UsdReadRefused: The stage authors a skin the reader crashes on; the
                message names the prims, and :meth:`import_scene` reads it safely.
            RuntimeError: pxr cannot read the layer (damaged, empty, not fully
                synced) -- an import cannot either.
        """
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(file_path))))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"USD file not found: {path}")
        name = os.path.basename(path)
        cls.load_plugin()
        try:
            crashing = cls.crashing_skins(path)
        except RuntimeError as error:  # pxr's Tf.ErrorException
            raise RuntimeError(
                f"{name} is not a readable USD layer -- damaged, empty or not fully "
                f"synced? ({cls._error_detail(error)})"
            ) from error
        if crashing:
            shown = ", ".join(crashing[:3])
            if len(crashing) > 3:
                shown += f" and {len(crashing) - 3} more"
            raise UsdReadRefused(
                f"{name} authors {len(crashing)} dual-quaternion skin(s) ({shown}), "
                "which crash mayaUsd's USD reader, so it cannot be referenced or "
                "opened. Import it instead: the import neutralizes those skins and "
                "restores dual quaternion afterwards.",
                crashing,
            )
        return cls.file_options(options, read_animation)

    @staticmethod
    def _error_detail(error: Exception) -> str:
        """The readable part of a pxr error: its first line, less the C++ source
        location pxr prefixes (``Error in '<function>' at line N in file X : 'why'``)."""
        lines = [ln.strip() for ln in str(error).splitlines() if ln.strip()]
        first = lines[0] if lines else type(error).__name__
        return first.split(" : ", 1)[-1].strip("'")

    @classmethod
    def dq_safe_source(cls, usd_path: str) -> Tuple[str, Dict[str, str]]:
        """``(path safe to hand mayaUsd, {prim path: real skinning method})``.

        mayaUsd 0.30 SEGFAULTS importing a UsdSkel skin whose ``skinningMethod``
        is ``dualQuaternion`` -- measured on a production module, and on a stage
        mayaUsd ITSELF wrote (its exporter authors that value for a DQ
        skinCluster, so it cannot read back its own output). Maya dies; nothing
        raises, so nothing can catch it.

        The source is therefore composed through a temporary OVERLAY layer that
        sublayers it and overrides the token to ``classicLinear``, and
        :meth:`apply_skinning_methods` puts the real method back on the
        skinClusters afterwards. An overlay rather than a rewritten copy for two
        reasons: a caller's own ``.usd`` is never modified, and the overlay is
        ~1.7 KB whatever the stage weighs (measured against a 24 MB payload).

        A stage authoring no dangerous value is returned unchanged, so the common
        path allocates nothing.
        """
        methods = cls.skinning_methods(usd_path)
        risky = cls._crashing_prims(methods)
        if not risky:
            return usd_path, methods
        from pxr import Sdf, Usd, UsdSkel

        # "session", not "scoped": mayaUsd CACHES the layer, so the consumer reads
        # it for as long as Maya runs, and there is no `with` here to close --
        # scoped would have meant "never deleted deterministically" under a
        # label that says otherwise. atexit is the only safe deterministic point.
        overlay = ptk.TempArtifacts("mtk_usd_dq_overlay", policy="session").path(
            ".usda"
        )
        layer = Sdf.Layer.CreateNew(overlay)
        # ABSOLUTE: a sublayer path resolves against the layer holding it, and
        # that layer lives in the temp dir, not the caller's working directory.
        layer.subLayerPaths.append(os.path.abspath(str(usd_path)).replace("\\", "/"))
        stage = Usd.Stage.Open(layer)
        for prim_path in risky:
            UsdSkel.BindingAPI(
                stage.OverridePrim(prim_path)
            ).CreateSkinningMethodAttr().Set("classicLinear")
        layer.Save()
        logger.info(
            f"{len(risky)} dual-quaternion skin(s) neutralized for the import "
            "(mayaUsd 0.30 crashes on them); the method is restored afterwards."
        )
        return overlay, methods

    @staticmethod
    def apply_skinning_methods(nodes: List[str], methods: Dict[str, str]) -> int:
        """Set each imported skinCluster's ``skinningMethod`` from *methods*
        (``{prim path: method}``, from :meth:`skinning_methods`). Returns the
        number changed.

        mayaUsd writes the attribute but never reads it back, so without this an
        incoming skin binds ``classicLinear`` whatever the layer says -- measured
        at 4.03 mm of SHAPE error against 0.22 mm of placement on a production
        module, worst where the rig bends.

        Matched by PATH, not by name: mayaUsd names a node after its prim, so a
        prim's path IS its transform chain. Namespaces are stripped per segment
        so this also works inside an isolation namespace, where a name match
        would fail and a leaf-name match would pick the wrong mesh in any scene
        that repeats a name (production scenes do).
        """
        import maya.cmds as cmds

        if not methods:
            return 0
        wanted = {
            tuple(p for p in str(path).split("/") if p): method
            for path, method in methods.items()
        }
        applied = 0
        for shape in (
            cmds.ls(nodes or [], type="mesh", long=True, noIntermediate=True) or []
        ):
            # The shape's own name is not a prim; its transform chain is.
            parts = [p.rsplit(":", 1)[-1] for p in shape.split("|") if p]
            method = wanted.get(tuple(parts[:-1]))
            if method is None:
                continue
            cluster = (cmds.ls(cmds.listHistory(shape), type="skinCluster") or [None])[
                0
            ]
            if not cluster:
                continue
            value = 1 if str(method) == "dualQuaternion" else 0
            if cmds.getAttr(f"{cluster}.skinningMethod") != value:
                cmds.setAttr(f"{cluster}.skinningMethod", value)
                applied += 1
            if value and cmds.attributeQuery(
                "dqsSupportNonRigid", node=cluster, exists=True
            ):
                # Dual quaternions are RIGID transforms; without this flag a
                # scaled or sheared joint is skinned as though it were not, and
                # the skin leaves the rig entirely. Measured on a production
                # module of stretched, scale-compensated chains: linear 4.08 mm,
                # dual-quaternion alone 1264.97 mm, dual-quaternion with this
                # flag 0.06 mm. Set WITH the method rather than carried
                # separately -- no interchange format has a slot for it, and a
                # DQ skin that needs it and does not have it is not a fidelity
                # loss, it is a destroyed scene.
                cmds.setAttr(f"{cluster}.dqsSupportNonRigid", 1)
        if applied:
            logger.info(f"Skinning method restored on {applied} skinCluster(s).")
        return applied

    @classmethod
    def stage_conform(cls, usd_path: str) -> Optional[Tuple[float, float]]:
        """``(scale, rotate_x)`` that brings *usd_path*'s stage into this scene's
        linear unit and up axis, or ``None`` when it already matches.

        mayaUsd 0.30 converts neither when it reads a layer -- its importer has
        no unit or up-axis option (checked 2026-09-23) -- so a metre / Z-up
        layer, which is Blender's default USD export, lands 100x small and
        lying on its side, through Maya's own File > Create Reference too.
        *scale* is the stage's metres-per-unit over Maya's INTERNAL unit, the
        centimetre, whatever unit the scene works in: the reader writes every
        distance in it ("All distance values will be imported in Maya's
        internal distance unit" -- mayaUsd 0.30), so a Maya-authored layer
        needs nothing in a metre scene either, and a metre layer needs 100x in
        every scene. *rotate_x* turns the stage's up axis onto the scene's (-90
        for a Z-up stage in a Y-up scene, +90 the other way). An unauthored
        value reads as USD's own fallback (centimetres, Y-up), i.e. as a
        Maya-authored layer.

        Parameters:
            usd_path: The layer (or package) to read.

        Returns:
            The conform :meth:`conform_roots` applies, or ``None``.
        """
        from pxr import Usd, UsdGeom

        # Stage metadata lives on the root layer: no payload needs loading.
        stage = Usd.Stage.Open(str(usd_path), Usd.Stage.LoadNone)
        if stage is None:
            return None
        stage_mpu = UsdGeom.GetStageMetersPerUnit(stage) or 0.01
        # mayaUsd writes every distance it reads in Maya's INTERNAL unit, the
        # centimetre, whatever unit the scene works in.
        scale = stage_mpu / Units.INTERNAL_M
        stage_up = str(UsdGeom.GetStageUpAxis(stage)).upper()
        scene_up = str(cmds.upAxis(query=True, axis=True)).upper()
        rotate_x = 0.0
        if stage_up != scene_up:
            rotate_x = -90.0 if stage_up == "Z" else 90.0
        if abs(scale - 1.0) < 1e-9 and not rotate_x:
            return None
        return scale, rotate_x

    @staticmethod
    def conform_roots(
        roots: List[str], conform: Optional[Tuple[float, float]], name: str
    ) -> Optional[str]:
        """Parent *roots* under a new world-level group carrying *conform*
        (:meth:`stage_conform`); return the group, or ``None`` when there is
        nothing to conform.

        A group rather than a rewrite of the roots: an animated root's keys
        would have to be composed with the conform, and on a live reference
        the parenting is a reference edit Maya re-applies on every reload --
        an unlink keeps the group with its content. Relative parenting keeps
        each root's own values; the group supplies the unit and axis.

        Parameters:
            roots: The world-level transforms to conform (:meth:`top_transforms`).
            conform: ``(scale, rotate_x)``, *rotate_x* in degrees; ``None`` or
                an empty *roots* builds nothing.
            name: The group's name (Maya uniquifies a taken one).

        Returns:
            The group, or ``None``.
        """
        if not conform or not roots:
            return None
        scale, rotate_x = conform
        group = cmds.group(empty=True, world=True, name=name)
        if cmds.currentUnit(query=True, angle=True) == "rad":
            rotate_x = math.radians(rotate_x)  # setAttr reads the working unit
        cmds.setAttr(f"{group}.rotateX", rotate_x)
        cmds.setAttr(f"{group}.scale", scale, scale, scale, type="double3")
        for root in roots:
            cmds.parent(root, group, relative=True)
        return group

    @staticmethod
    def top_transforms(nodes: List[str]) -> List[str]:
        """The world-level transforms among *nodes* (long names)."""
        return [
            node
            for node in cmds.ls(nodes or [], type="transform", long=True) or []
            if not cmds.listRelatives(node, parent=True)
        ]

    @classmethod
    def _check_material_names(cls, value: str) -> None:
        """Refuse a ``material_names`` outside :attr:`MATERIAL_NAMES`: a misspelt
        one would otherwise pass for ``"shading_group"`` and say nothing."""
        if value not in cls.MATERIAL_NAMES:
            raise ValueError(
                f"Unknown material_names {value!r}: expected one of {cls.MATERIAL_NAMES}."
            )

    @staticmethod
    @contextlib.contextmanager
    def _linear_key_defaults():
        """Maya's global default key tangents set to ``linear`` for the block,
        put back after it.

        USD interpolates linearly between time samples, and mayaUsd's reader
        creates its keys with the session's default tangents (stock ``auto``):
        a two-sample move 100 -> 200 over frames 1-10 read 103.429 at frame 2
        where the layer says 111.111 (measured 2026-11-13, mayaUsd 0.30). Read
        under a linear default the curve is the layer's, at no cost per key.
        Set around the read rather than on the new curves afterwards: the
        import's node list is optional (``return_new_nodes``), and that pass
        costs a call per curve.
        """
        prior_in = (cmds.keyTangent(query=True, g=True, inTangentType=True) or ["auto"])[0]
        prior_out = (cmds.keyTangent(query=True, g=True, outTangentType=True) or ["auto"])[0]
        try:
            cmds.keyTangent(g=True, inTangentType="linear", outTangentType="linear")
            yield
        finally:
            cmds.keyTangent(g=True, inTangentType=prior_in, outTangentType=prior_out)

    @staticmethod
    def _material_prim_names(usd_path: Optional[str]) -> set:
        """The names of the ``Material`` prims the stage at *usd_path* composes
        (empty when there is no readable stage)."""
        if not usd_path:
            return set()
        from pxr import Usd

        stage = Usd.Stage.Open(str(usd_path))
        if stage is None:
            return set()
        return {p.GetName() for p in stage.Traverse() if p.GetTypeName() == "Material"}

    @classmethod
    def name_shaders_after_materials(
        cls, nodes: List[str], usd_path: Optional[str] = None
    ) -> Dict[str, str]:
        """Give each material among the imported *nodes* Maya's own spelling:
        its surface shader takes the material's name, its shading group
        ``<name>SG``. The inverse of :meth:`name_materials_after_shaders`.

        mayaUsd names the shading group after the ``Material`` prim and the
        shader after the ``Shader`` prim inside it, a name that says nothing
        (``surface``, ``Principled_BSDF``; the next material's is ``surface1``).
        Everything else here identifies a material by its SHADER -- FBX, the
        manifests, :meth:`export` -- so a plain import then export wrote
        ``Material surface`` and ``surface1`` (measured 2026-11-13).

        A group already spelled ``<shader>SG`` is left alone. Where a Material
        and its Shader share a name (what :meth:`export` writes) Maya gives the
        shader the name and the group a digit (``steel1``): that group becomes
        ``steelSG``. A material whose own name ends in a digit reads the same
        way, so the layer is read to tell them apart, only when that pattern
        shows. A shader this import did not make, or one a group of this pass
        already named, keeps its name. New names stay in the node's namespace
        (a bare name would move it into the current one, measured).

        Parameters:
            nodes: The nodes the import created.
            usd_path: The layer they came from; without it a group named
                ``<shader><digits>`` is taken for Maya's clash rename.

        Returns:
            ``{old name: new name}`` for every node renamed.
        """
        if not nodes:  # `cmds.ls([])` lists the whole scene
            return {}
        fresh = set(nodes)
        renamed: Dict[str, str] = {}
        named: set = set()
        materials: Optional[set] = None
        for group in cmds.ls(nodes, exactType="shadingEngine") or []:
            shaders = (
                cmds.listConnections(
                    f"{group}.surfaceShader", source=True, destination=False
                )
                or []
            )
            if not shaders:
                continue
            shader = shaders[0]
            if shader in named or shader not in fresh:
                continue
            space, _, group_name = group.rpartition(":")
            shader_name = shader.rpartition(":")[2]
            if group_name == f"{shader_name}SG":
                continue
            name = group_name
            if name.endswith("SG") and len(name) > 2:
                name = name[:-2]
            if name != shader_name and name.rstrip("0123456789") == shader_name:
                if materials is None:
                    materials = cls._material_prim_names(usd_path)
                if name not in materials:
                    name = shader_name  # Maya's clash rename, not a material's name
            absolute = f":{space}:" if space else ":"
            renamed[group] = cmds.rename(group, f"{absolute}{name}SG")
            if shader_name != name:
                renamed[shader] = cmds.rename(shader, f"{absolute}{name}")
                shader = renamed[shader]
            named.add(shader)
        if renamed:
            logger.info(
                f"{len(renamed)} imported shading node(s) named after their material."
            )
        return renamed

    @classmethod
    def import_scene(
        cls,
        file_path: str,
        namespace: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        return_new_nodes: bool = True,
        read_animation: bool = True,
        conform: bool = False,
        material_names: str = "shader",
    ) -> List[str]:
        """Import a USD file, optionally isolated into a namespace.

        Runs through the ``cmds.file`` *USD Import* translator (not
        ``mayaUSDImport``) so the import honors the same native namespace
        isolation the ``.ma``/FBX paths get: the *active* namespace is set
        before the import and every created node lands under it, then the
        prior namespace is restored — the exact contract of
        :meth:`FbxUtils.import_scene`. Keys are read with linear tangents, as
        USD interpolates between its samples (:meth:`_linear_key_defaults`);
        the session's default tangents are put back.

        Parameters:
            file_path: Source USD file (``$VAR``/``~`` expanded).
            namespace: If given, created if absent and set active for the
                import. If *None*, imports into the current namespace.
            options: Translator options merged over
                :attr:`INTERCHANGE_IMPORT_OPTIONS` and appended to the
                ``cmds.file`` options string (see :meth:`options_string`), e.g.
                ``{"primPath": "/"}``.
            return_new_nodes: Passed to ``cmds.file(returnNewNodes=...)``.
            read_animation: Import the stage's time samples as keys
                (``readAnimData``). The translator's own default is OFF, which
                silently drops every animated prim — measured: a Blender scene
                pulled via USD arrived static. An explicit ``options`` entry
                wins over this flag.
            conform: Parent the imported roots under a group that brings a
                stage in another unit or up axis into this scene's
                (:meth:`stage_conform`, :meth:`conform_roots`) -- for a foreign
                layer. Off for a layer the bridge wrote for this scene.
            material_names: What carries an imported material's name --
                ``"shader"`` (default): the surface shader takes the Material
                prim's name and its group ``<name>SG``, Maya's own spelling and
                the identity :meth:`export` writes back
                (:meth:`name_shaders_after_materials`; it needs the node list,
                so *return_new_nodes*); ``"shading_group"``: mayaUsd's own
                names, the group after the Material prim and the shader after
                its Shader prim.

        Returns:
            The newly created node names (namespace-prefixed when *namespace*
            is given; shading nodes by the names *material_names* gave them) --
            after a conform, the DAG nodes by their paths under its group, the
            group included -- or ``[]``.

        Raises:
            FileNotFoundError: If *file_path* does not exist.
            RuntimeError: On import failure.
            ValueError: An unknown *material_names*.
        """
        cls._check_material_names(material_names)
        file_path = os.path.abspath(
            os.path.expandvars(os.path.expanduser(str(file_path)))
        )
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"USD file not found: {file_path}")

        cls.load_plugin()
        read = cls.file_options(options, read_animation)

        usd_path = file_path.replace("\\", "/")
        # BEFORE the namespace is touched: this reads the file and can raise on a
        # corrupt one, and everything that restores the namespace is below. A
        # raise between setNamespace and the try would leave Maya INSIDE the
        # isolation namespace -- caught by
        # test_usd_leg_cleans_namespace_when_import_itself_fails.
        # A layer stating LOD groups as variant sets (Unreal's, this exporter's)
        # imports one variant alone; expand them so every level arrives.
        expanded = cls.expand_lod_variants(usd_path)
        if expanded:
            usd_path = expanded.replace("\\", "/")
        source, skin_methods = cls.dq_safe_source(usd_path)

        restore_ns = None
        if namespace:
            if not cmds.namespace(exists=namespace):
                cmds.namespace(add=namespace)
            restore_ns = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
            cmds.namespace(setNamespace=namespace)
        try:
            with cls._linear_key_defaults():
                new_nodes = cmds.file(
                    source,
                    i=True,
                    returnNewNodes=return_new_nodes,
                    ignoreVersion=True,
                    **read,
                )
        finally:
            if restore_ns is not None:
                cmds.namespace(setNamespace=restore_ns)

        logger.info(
            f"Imported USD: {usd_path}"
            + (f" into namespace '{namespace}'" if namespace else "")
        )
        # cmds.file returns the new-node list only with returnNewNodes; without
        # it the return is the filename string — honor the List[str] contract.
        new_nodes = new_nodes if isinstance(new_nodes, list) else []
        if material_names == "shader" and new_nodes:
            renamed = cls.name_shaders_after_materials(new_nodes, source)
            new_nodes = [renamed.get(node, node) for node in new_nodes]
        if skin_methods and not new_nodes:
            # Nothing to apply the method TO. Scanning the scene instead would
            # touch skins this import did not create, so say so rather than
            # guess -- a silently linear skin is the defect this restores.
            logger.warning(
                f"{len(skin_methods)} skinning method(s) could not be restored: "
                "the import returned no node list (return_new_nodes=False). The "
                "skins bind classicLinear."
            )
        cls.apply_skinning_methods(new_nodes, skin_methods)
        to_conform = cls.stage_conform(usd_path) if conform and new_nodes else None
        if to_conform:
            from mayatk.core_utils._core_utils import CoreUtils

            # Taken before the group re-parents the roots: the DAG paths the
            # import returned name nothing once the roots sit under it.
            handles = CoreUtils.node_handles(new_nodes)
            stem = os.path.splitext(os.path.basename(usd_path))[0]
            group = cls.conform_roots(
                cls.top_transforms(new_nodes),
                to_conform,
                f"{namespace or stem}_conform",
            )
            if group:
                new_nodes = CoreUtils.resolve_handles(handles) + [group]
        return new_nodes
