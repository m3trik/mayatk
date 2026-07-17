# !/usr/bin/python
# coding=utf-8
import os
import logging
from typing import Optional, Dict, Any, List, Iterable, Callable

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    pass

import pythontk as ptk

logger = logging.getLogger(__name__)


class FbxUtils(ptk.HelpMixin):
    """Low-level utilities for FBX import/export operations in Maya.

    This module owns the MEL-level FBX commands (plugin loading, preset
    application, option setting, and the ``cmds.file`` import/export call).
    Higher-level orchestration (task management, UI, logging to files,
    namespace sandboxing) belongs in ``SceneExporter``, ``NamespaceSandbox``
    or calling code.
    """

    _AUTO_TAKES_OWNER = "fbx.auto_takes"  # stable owner key for SJM teardown
    _auto_takes_ids = None  # (before_id, after_id) when the hook is active
    _export_preparers = {}  # name -> callable, run before each auto FBX export
    _explicit_auto_takes = False  # enable_auto_takes() called with no preparers

    # Sensible defaults applied by import_scene when no options are supplied.
    # Only commands that exist on Maya 2025 are listed — materials/textures
    # import unconditionally (there is no FBXImportMaterials command). Callers
    # override via ``options``.
    _DEFAULT_IMPORT_OPTIONS = {
        "FBXImportMode": "add",
        "FBXImportConvertDeformingNullsToJoint": True,
        "FBXImportMergeAnimationLayers": True,
        "FBXImportConstraints": True,
        "FBXImportCameras": True,
        "FBXImportLights": True,
        "FBXImportGenerateLog": False,
        "FBXImportUpAxis": "y",
    }

    @staticmethod
    def load_plugin():
        """Ensure the fbxmaya plugin is loaded."""
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya")

    @staticmethod
    def set_fbx_options(options: Dict[str, Any]):
        """Apply FBX export options via MEL commands.

        Maya's FBX setters use inconsistent syntax. Most accept a bare value
        (``FBXExportUpAxis y``); some require ``-v`` (``FBXExportQuaternion
        -v euler``); and a few reject the quoted form entirely — e.g.
        ``FBXExportQuaternion -v "euler"`` errors, only ``-v euler`` works.
        For non-bool values we try bare, then unquoted ``-v``, then quoted
        ``-v`` to cover all observed variants.

        Parameters:
            options: Mapping of FBX MEL command names to values.
        """
        for option, value in options.items():
            if isinstance(value, bool):
                mel.eval(f"{option} -v {'true' if value else 'false'}")
            elif isinstance(value, (int, float)):
                try:
                    mel.eval(f"{option} {value}")
                except RuntimeError:
                    mel.eval(f"{option} -v {value}")
            else:
                try:
                    mel.eval(f'{option} "{value}"')
                except RuntimeError:
                    try:
                        mel.eval(f"{option} -v {value}")
                    except RuntimeError:
                        mel.eval(f'{option} -v "{value}"')

    @staticmethod
    def load_preset(preset_path: str):
        """Load an FBX export preset file.

        Parameters:
            preset_path: Absolute path to the ``.fbxexportpreset`` file.

        Raises:
            FileNotFoundError: If *preset_path* does not exist.
            RuntimeError: If the MEL command fails.
        """
        if not os.path.isfile(preset_path):
            raise FileNotFoundError(f"FBX preset not found: {preset_path}")
        formatted = preset_path.replace("\\", "/")
        mel.eval(f'FBXLoadExportPresetFile -f "{formatted}"')
        logger.info(f"Loaded FBX export preset: {formatted}")

    @classmethod
    def export(
        cls,
        file_path: str,
        objects: Optional[List] = None,
        preset_file: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        selection_only: bool = True,
    ) -> str:
        """Export geometry to an FBX file.

        Parameters:
            file_path: Destination ``.fbx`` path (directories are created automatically).
            objects: Nodes to export.  If *None*, the current selection is used.
            preset_file: Optional FBX export preset to load before exporting.
            options: Additional FBX MEL options applied *after* the preset.
            selection_only: If True export selected; if False export entire scene.

        Returns:
            The absolute path of the exported file.

        Raises:
            RuntimeError: On export failure.
        """
        cls.load_plugin()

        file_path = os.path.abspath(os.path.expandvars(file_path))
        if not file_path.lower().endswith(".fbx"):
            file_path += ".fbx"

        export_dir = os.path.dirname(file_path)
        os.makedirs(export_dir, exist_ok=True)

        if objects:
            names = [str(o) for o in objects]
            cmds.select(names, replace=True)

        if selection_only and not cmds.ls(selection=True):
            raise RuntimeError(
                "Export requested for selection, but nothing is selected."
            )

        if preset_file:
            cls.load_preset(preset_file)

        if options:
            cls.set_fbx_options(options)

        kwargs = {"force": True, "options": "v=0;", "type": "FBX export"}
        if selection_only:
            kwargs["exportSelected"] = True
        else:
            kwargs["exportAll"] = True

        cmds.file(file_path, **kwargs)
        logger.info(f"Exported FBX: {file_path}")
        return file_path

    @classmethod
    def import_scene(
        cls,
        file_path: str,
        namespace: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        return_new_nodes: bool = True,
    ) -> List[str]:
        """Import an FBX file, optionally isolated into a namespace.

        Maya's FBX translator **ignores** the ``cmds.file(namespace=...)``
        flag, but it honors the *active* namespace: setting it before the
        import cleanly isolates every imported node — transforms, shapes,
        materials and shading engines — under that namespace (verified on
        Maya 2025). This is the same native isolation ``.ma/.mb`` imports get
        for free, so no manual per-node namespace moves are needed. The active
        namespace is always restored afterward, even on failure.

        Parameters:
            file_path: Source ``.fbx`` path (``$VAR``/``~`` expanded).
            namespace: If given, it is created if absent and set active so the
                whole import lands under it. If *None*, imports into the
                current namespace (usually root).
            options: FBX import MEL options applied before importing (see
                :func:`set_fbx_options`). Defaults to
                :attr:`_DEFAULT_IMPORT_OPTIONS`. Applied best-effort — a
                version-specific option that is unavailable never blocks the
                import.
            return_new_nodes: Passed to ``cmds.file(returnNewNodes=...)``.

        Returns:
            The newly created node names (namespace-prefixed when *namespace*
            is given), or ``[]``.

        Raises:
            FileNotFoundError: If *file_path* does not exist.
            RuntimeError: On import failure.
        """
        file_path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(file_path))))
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"FBX not found: {file_path}")

        cls.load_plugin()
        cls._apply_import_options(
            options if options is not None else cls._DEFAULT_IMPORT_OPTIONS
        )

        fbx_path = file_path.replace("\\", "/")
        restore_ns = None
        if namespace:
            if not cmds.namespace(exists=namespace):
                cmds.namespace(add=namespace)
            restore_ns = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
            cmds.namespace(setNamespace=namespace)
        try:
            new_nodes = cmds.file(
                fbx_path,
                i=True,
                type="FBX",
                returnNewNodes=return_new_nodes,
                mergeNamespacesOnClash=False,
                preserveReferences=False,
            )
        finally:
            if restore_ns is not None:
                cmds.namespace(setNamespace=restore_ns)

        logger.info(
            f"Imported FBX: {fbx_path}"
            + (f" into namespace '{namespace}'" if namespace else "")
        )
        # cmds.file returns the new-node list only with returnNewNodes; without
        # it the return is the filename string — honor the List[str] contract.
        return new_nodes if isinstance(new_nodes, list) else []

    @classmethod
    def _apply_import_options(cls, options: Dict[str, Any]) -> None:
        """Apply FBX import options best-effort and quietly.

        Import setters share the export setters' inconsistent syntax
        (``FBXImportMode -v add`` but bare ``FBXImportUpAxis y``), so each is
        delegated to :func:`set_fbx_options` — the single owner of that
        syntax-probing — rather than reimplemented here. Each is applied in
        isolation so a command absent on this Maya version (there is no
        ``FBXImportMaterials``, for instance) is skipped instead of blocking the
        rest, and the script-editor error noise the probing emits is
        suppressed.
        """
        suppressed = False
        try:
            cmds.scriptEditorInfo(suppressErrors=True, suppressWarnings=True)
            suppressed = True
        except Exception:
            pass
        try:
            for opt, val in options.items():
                try:
                    cls.set_fbx_options({opt: val})
                except Exception:
                    logger.debug("FBX import option %r unavailable (OK).", opt)
        finally:
            if suppressed:
                try:
                    cmds.scriptEditorInfo(
                        suppressErrors=False, suppressWarnings=False
                    )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Animation takes (generic — any tool can declare takes on a node)
    # ------------------------------------------------------------------

    @staticmethod
    def reset_takes() -> None:
        """Clear all FBX export take definitions (global, sticky exporter state)."""
        FbxUtils.load_plugin()
        mel.eval("FBXExportSplitAnimationIntoTakes -c")

    @staticmethod
    def apply_takes(takes: Iterable[Any]) -> int:
        """Configure FBX export to emit one AnimStack (Unity clip) per take.

        Enables bake-complex, sets the **union** bake range over all takes (safe
        regardless of whether Maya bakes per-take or clips from the global
        range), clears prior take state, then declares each take.

        Parameters:
            takes: Sequence of ``{"name","start","end"}`` mappings (the
                ``fbx_takes`` channel shape) or ``(name, start, end)`` tuples.

        Returns:
            int: Number of takes defined.  Empty input only clears state.
        """
        FbxUtils.reset_takes()  # also ensures the fbxmaya plugin is loaded

        norm = []
        for t in takes or []:
            if isinstance(t, dict):
                name, start, end = t["name"], t["start"], t["end"]
            else:
                name, start, end = t
            norm.append((str(name), int(round(start)), int(round(end))))

        if not norm:
            return 0

        union_start = min(s for _, s, _ in norm)
        union_end = max(e for _, _, e in norm)
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval(f"FBXExportBakeComplexStart -v {union_start}")
        mel.eval(f"FBXExportBakeComplexEnd -v {union_end}")

        for name, start, end in norm:
            safe = name.replace('"', "")  # MEL string guard
            mel.eval(f'FBXExportSplitAnimationIntoTakes -v "{safe}" {start} {end}')

        logger.info(
            f"Configured {len(norm)} FBX take(s); bake range {union_start}-{union_end}."
        )
        return len(norm)

    @staticmethod
    def apply_takes_from_node(
        node: Optional[str] = None, attr: Optional[str] = None
    ) -> int:
        """Read take defs from a JSON string channel on *node* and apply them.

        Defaults to the shared ``data_export`` node's ``fbx_takes`` channel, so
        this is shot-agnostic — it realizes whatever takes the scene declares.

        Returns:
            int: Number of takes defined (0 if the channel is absent/empty).
        """
        import json
        from mayatk.node_utils.data_nodes import DataNodes

        node = node or DataNodes.EXPORT
        attr = attr or DataNodes.FBX_TAKES

        if not cmds.objExists(node) or not cmds.attributeQuery(
            attr, node=node, exists=True
        ):
            return 0
        raw = cmds.getAttr(f"{node}.{attr}")
        if not raw:
            return 0
        try:
            defs = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning(f"Could not parse take defs from {node}.{attr}")
            return 0
        return FbxUtils.apply_takes(defs)

    # ------------------------------------------------------------------
    # Auto-prepare + apply declared takes on ANY FBX export (Phase 2)
    # ------------------------------------------------------------------
    #
    # One shared kBeforeExport hook runs every registered *export preparer*
    # (each stamps a subsystem's data onto the shared ``data_export`` node —
    # Shots' ``publish_export_view``, Audio's ``prepare_for_export``, …) and
    # then realizes whatever takes the scene declares.  A kAfterExport hook
    # clears take state so nothing leaks into a later export.  Subsystems
    # compose: each registers once, and the hook lifecycle is reference-counted
    # off the registry (installed on the first preparer / explicit enable,
    # removed when the last is gone).

    # The declarative list of known metadata producers that stamp the shared
    # ``data_export`` carrier: name → (module, class, no-arg refresh method).
    # ``run_export_preparers`` falls back to these for any producer without a
    # registered session preparer, so callers like the Scene Exporter refresh
    # every subsystem without naming them. Add new producers HERE — nothing
    # else needs to change. Resolved lazily; an unimportable producer is
    # skipped (never blocks an export).
    _KNOWN_PRODUCERS = {
        "shots": ("mayatk.anim_utils.shots._shots", "ShotStore", "refresh_export_view"),
        "audio": (
            "mayatk.audio_utils.audio_clips._audio_clips",
            "AudioClips",
            "prepare_for_export",
        ),
        "shadow": (
            "mayatk.rig_utils.shadow_rig",
            "ShadowRig",
            "refresh_export_metadata",
        ),
    }

    @staticmethod
    def run_export_preparers(include_known: bool = True) -> None:
        """Refresh every producer's ``data_export`` channel once, right now.

        Runs each registered session preparer, then (when *include_known*)
        every :attr:`_KNOWN_PRODUCERS` entry not already covered by a
        registered preparer of the same name.  Each producer is isolated —
        one failing or unimportable subsystem never blocks the others — and
        each no-ops when it has nothing to write, so a metadata-free scene
        leaves no carrier behind.  This is the one call an export pipeline
        needs to make the carrier current.
        """
        import importlib

        # Canonical run order: producers named in _KNOWN_PRODUCERS first, in
        # that dict's order, so same-pass channel consumers read fresh data —
        # audio scopes its events against the fbx_takes that shots has just
        # republished. Unknown preparers follow in registration order (stable
        # sort).
        known_rank = {n: i for i, n in enumerate(FbxUtils._KNOWN_PRODUCERS)}
        ordered = sorted(
            FbxUtils._export_preparers.items(),
            key=lambda kv: known_rank.get(kv[0], len(known_rank)),
        )

        ran = set()
        for name, prepare in ordered:
            ran.add(name)
            try:
                prepare()
            except Exception:  # one subsystem's failure must not block others
                logger.warning("Export preparer %r failed.", name, exc_info=True)
        if not include_known:
            return
        for name, (module_path, cls_name, method) in FbxUtils._KNOWN_PRODUCERS.items():
            if name in ran:
                continue
            try:
                producer = getattr(importlib.import_module(module_path), cls_name)
                getattr(producer, method)()
            except Exception:
                logger.debug("Producer %r refresh skipped.", name, exc_info=True)

    @staticmethod
    def register_export_preparer(name: str, prepare: Callable[[], Any]) -> None:
        """Run *prepare* before every FBX export this session (installs the hook).

        A preparer stamps a subsystem's data onto the shared ``data_export``
        node so it rides into **any** FBX export (File ▸ Export, Game Exporter,
        scripts).  Multiple subsystems compose — each preparer runs once per
        export, known producers first in :attr:`_KNOWN_PRODUCERS` order
        (shots before audio, so audio can scope events against the takes
        shots just republished), other names in registration order; then
        declared takes are realized.
        Re-registering the same *name* replaces it.  Use
        :func:`unregister_export_preparer` to remove it.
        """
        FbxUtils._export_preparers[name] = prepare
        FbxUtils._sync_auto_export_hook()

    @staticmethod
    def unregister_export_preparer(name: str) -> None:
        """Remove a preparer; the hook is torn down when the last one is gone."""
        FbxUtils._export_preparers.pop(name, None)
        FbxUtils._sync_auto_export_hook()

    @staticmethod
    def enable_auto_takes() -> None:
        """Realize declared takes on **every** FBX export — shot-agnostic, no preparer.

        Installs the shared before-export hook directly: it applies whatever is
        already on the ``data_export`` ``fbx_takes`` channel.  For a producer that
        must regenerate the channel fresh at export time, register a preparer via
        :func:`register_export_preparer` instead (e.g.
        ``ShotStore.enable_auto_export``).  Idempotent.
        """
        FbxUtils._explicit_auto_takes = True
        FbxUtils._sync_auto_export_hook()

    @staticmethod
    def disable_auto_takes() -> None:
        """Clear the explicit enable; removes the hook if no preparers remain."""
        FbxUtils._explicit_auto_takes = False
        FbxUtils._sync_auto_export_hook()

    @staticmethod
    def _sync_auto_export_hook() -> None:
        """Install/remove the shared hook to match the registry + explicit flag."""
        want = FbxUtils._explicit_auto_takes or bool(FbxUtils._export_preparers)
        if want and not FbxUtils._auto_takes_ids:
            FbxUtils._install_auto_export_hook()
        elif not want and FbxUtils._auto_takes_ids:
            FbxUtils._remove_auto_export_hook()

    @staticmethod
    def _on_before_export(*_):
        """Run every registered preparer (isolated), then realize declared takes.

        Registered-only (no known-producer fallback): the session hook is
        opt-in per subsystem, so a producer that unregistered stays out.
        """
        FbxUtils.run_export_preparers(include_known=False)
        FbxUtils.apply_takes_from_node()

    @staticmethod
    def _install_auto_export_hook() -> None:
        from mayatk.core_utils.script_job_manager import ScriptJobManager
        import maya.api.OpenMaya as om

        mgr = ScriptJobManager.instance()
        before = mgr.add_om_callback(
            om.MSceneMessage.addCallback,
            om.MSceneMessage.kBeforeExport,
            FbxUtils._on_before_export,
            owner=FbxUtils._AUTO_TAKES_OWNER,
        )
        after = mgr.add_om_callback(
            om.MSceneMessage.addCallback,
            om.MSceneMessage.kAfterExport,
            lambda *_: FbxUtils.reset_takes(),
            owner=FbxUtils._AUTO_TAKES_OWNER,
        )
        FbxUtils._auto_takes_ids = (before, after)
        logger.info(
            "Auto-export hook enabled (%d preparer(s)).",
            len(FbxUtils._export_preparers),
        )

    @staticmethod
    def _remove_auto_export_hook() -> None:
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        ScriptJobManager.instance().unsubscribe_all(FbxUtils._AUTO_TAKES_OWNER)
        FbxUtils._auto_takes_ids = None
        logger.info("Auto-export hook disabled.")

    @staticmethod
    def is_auto_takes_enabled() -> bool:
        """Return whether the auto-takes export hook is currently registered."""
        return bool(FbxUtils._auto_takes_ids)
