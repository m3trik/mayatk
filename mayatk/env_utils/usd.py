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
import logging
from typing import Any, Dict, List, Optional

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
    # UsdPreviewSurface conversion so materials survive the hop, instances
    # kept instanceable, transform+shape merged into the single prim other
    # DCCs expect. Callers override any key via ``options``.
    _DEFAULT_EXPORT_OPTIONS = {
        "shadingMode": "useRegistry",
        "convertMaterialsTo": ["UsdPreviewSurface"],
        "exportInstances": True,
        "mergeTransformAndShape": True,
        "exportUVs": True,
        "exportVisibility": True,
    }

    @staticmethod
    def load_plugin():
        """Ensure the ``mayaUsdPlugin`` plugin is loaded."""
        if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
            cmds.loadPlugin("mayaUsdPlugin")

    @staticmethod
    def is_usd_file(file_path: str) -> bool:
        """True when *file_path* is a USD layer/package (delegates to pythontk)."""
        return ptk.is_usd_file(file_path)

    @classmethod
    def export(
        cls,
        file_path: str,
        objects: Optional[List] = None,
        options: Optional[Dict[str, Any]] = None,
        selection_only: bool = True,
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
                cmds.mayaUSDExport(
                    file=tmp_layer, selection=selection_only, **opts
                )
                result = ptk.UsdzPackager.from_layer(tmp_layer, file_path)
            finally:
                store.cleanup()
            logger.info(f"Exported USDZ: {result}")
            return result

        cmds.mayaUSDExport(file=file_path, selection=selection_only, **opts)
        logger.info(f"Exported USD: {file_path}")
        return file_path

    @classmethod
    def import_scene(
        cls,
        file_path: str,
        namespace: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        return_new_nodes: bool = True,
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
            options: Translator options appended to the ``cmds.file`` options
                string as ``key=value`` pairs (bools serialized as 0/1), e.g.
                ``{"readAnimData": True, "primPath": "/"}``.
            return_new_nodes: Passed to ``cmds.file(returnNewNodes=...)``.

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

        parts = []
        for key, value in (options or {}).items():
            if isinstance(value, bool):
                value = int(value)
            parts.append(f"{key}={value}")
        options_string = ";".join(parts)

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
