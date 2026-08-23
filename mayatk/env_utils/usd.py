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
from typing import Any, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    pass

import pythontk as ptk

logger = logging.getLogger(__name__)


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
        from mayatk.env_utils._env_utils import EnvUtils

        if not EnvUtils.is_plugin_loaded("mayaUsdPlugin"):
            cmds.loadPlugin("mayaUsdPlugin")

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
    ) -> str:
        """Export to a USD file (``.usd``/``.usda``/``.usdc``/``.usdz``).

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

        Returns:
            The absolute path of the exported file.

        Raises:
            RuntimeError: Nothing selected for a selection export, or export failure.
        """
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
                cls._maya_usd_export(tmp_layer, selection_only, opts)
                if material_names == "shader":
                    cls.name_materials_after_shaders(tmp_layer)
                result = ptk.UsdzPackager.from_layer(tmp_layer, file_path)
            finally:
                store.cleanup()
            logger.info(f"Exported USDZ: {result}")
            return result

        cls._maya_usd_export(file_path, selection_only, opts)
        if material_names == "shader":
            cls.name_materials_after_shaders(file_path)
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

    @staticmethod
    def _maya_usd_export(file_path: str, selection_only: bool, opts: Dict[str, Any]):
        """``cmds.mayaUSDExport`` with per-flag tolerance across mayaUsd versions.

        ``cmds`` rejects the whole call on ONE unknown flag (``TypeError``), so a
        flag this mayaUsd doesn't know is dropped with a log line and the call
        retried -- the mirror of blendertk's ``_filter_op_options`` (Blender
        renames USD kwargs between majors; mayaUsd adds flags between releases).
        Never drops ``file``.
        """
        opts = dict(opts)
        while True:
            try:
                return cmds.mayaUSDExport(
                    file=file_path, selection=selection_only, **opts
                )
            except TypeError as error:
                match = re.search(r"'(\w+)'", str(error))
                key = match.group(1) if match else None
                if key and key in opts:
                    logger.warning(
                        f"USD export flag unknown to this mayaUsd dropped: {key}"
                    )
                    del opts[key]
                    continue
                raise

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
        playback = (
            cmds.playbackOptions(query=True, animationStartTime=True),
            cmds.playbackOptions(query=True, animationEndTime=True),
        )
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
    def import_scene(
        cls,
        file_path: str,
        namespace: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        return_new_nodes: bool = True,
        read_animation: bool = True,
    ) -> List[str]:
        """Import a USD file, optionally isolated into a namespace.

        Runs through the ``cmds.file`` *USD Import* translator (not
        ``mayaUSDImport``) so the import honors the same native namespace
        isolation the ``.ma``/FBX paths get: the *active* namespace is set
        before the import and every created node lands under it, then the
        prior namespace is restored — the exact contract of
        :meth:`FbxUtils.import_scene`.

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

        Returns:
            The newly created node names (namespace-prefixed when *namespace*
            is given), or ``[]``.

        Raises:
            FileNotFoundError: If *file_path* does not exist.
            RuntimeError: On import failure.
        """
        file_path = os.path.abspath(
            os.path.expandvars(os.path.expanduser(str(file_path)))
        )
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"USD file not found: {file_path}")

        cls.load_plugin()

        merged = dict(cls.INTERCHANGE_IMPORT_OPTIONS, readAnimData=bool(read_animation))
        merged.update(options or {})
        options_string = cls.options_string(merged)

        usd_path = file_path.replace("\\", "/")
        restore_ns = None
        if namespace:
            if not cmds.namespace(exists=namespace):
                cmds.namespace(add=namespace)
            restore_ns = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
            cmds.namespace(setNamespace=namespace)
        try:
            new_nodes = cmds.file(
                usd_path,
                i=True,
                type="USD Import",
                returnNewNodes=return_new_nodes,
                ignoreVersion=True,
                options=options_string,
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
        return new_nodes if isinstance(new_nodes, list) else []
