# !/usr/bin/python
# coding=utf-8
import os
import logging
import contextlib
from typing import Optional, Dict, Any, List, Iterable, Callable, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    pass

import pythontk as ptk

logger = logging.getLogger(__name__)


class _BracketDepth:
    """Class-attribute view of the process-wide export-bracket depth.

    Reads answer from :meth:`FbxUtils._bracket_state`, so every copy of the
    class a reload leaves behind reports the same number; writes go through
    :meth:`FbxUtils._bracket_depth_add` (assigning the attribute would replace
    this descriptor).
    """

    def __get__(self, obj, owner=None) -> int:
        return FbxUtils._bracket_state()["depth"]


class _AutoTakesIds:
    """Class-attribute view of the process-wide auto-export callback ids.

    Same reason as :class:`_BracketDepth`, one step further. A live dev reload
    rebinds this class AND ``ScriptJobManager`` -- whose singleton is a CLASS
    attribute, so the new manager holds none of the previous copy's
    subscriptions. The install path's reload guard unsubscribed through that
    manager and therefore removed nothing, leaving Maya firing BOTH copies'
    ``kBeforeExport`` callbacks: measured, every preparer ran twice per export
    after one reload. Holding the raw callback ids where a reload cannot reach
    lets the incoming copy remove the outgoing one's callbacks by id, which
    needs no surviving object at all.

    Writes go through :meth:`FbxUtils._set_auto_takes_ids`; assigning the
    attribute would replace this descriptor.
    """

    def __get__(self, obj, owner=None) -> tuple:
        return tuple(FbxUtils._bracket_state()["auto_takes_ids"])


class FbxUtils(ptk.HelpMixin):
    """Low-level utilities for FBX import/export operations in Maya.

    This module owns the MEL-level FBX commands (plugin loading, preset
    application, option setting, and the ``cmds.file`` import/export call).
    Higher-level orchestration (task management, UI, logging to files,
    namespace sandboxing) belongs in ``SceneExporter``, ``NamespaceSandbox``
    or calling code.
    """

    _AUTO_TAKES_OWNER = "fbx.auto_takes"  # stable owner key for SJM teardown
    #: (before_id, after_id) when the hook is active -- process-wide, so a
    #: reload can still find and remove the previous copy's callbacks.
    _auto_takes_ids = _AutoTakesIds()
    _export_preparers = {}  # name -> callable, run before each auto FBX export
    _explicit_auto_takes = False  # enable_auto_takes() called with no preparers
    # Exporter state captured by apply_takes, restored by reset_takes:
    # (bake_enabled, bake_start, bake_end, animation_flipped), or None when
    # nothing is pending. The fourth member is whether apply_takes had to turn
    # the Animation include group (:attr:`ANIMATION_INCLUDE_PROPERTY`) ON --
    # it has to guarantee that or every other member is a no-op. Recorded as
    # the CHANGE rather than the prior value so the restore writes only what
    # was actually changed.
    _saved_bake_state = None

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
        from mayatk.env_utils._env_utils import EnvUtils

        if not EnvUtils.is_plugin_loaded("fbxmaya"):
            cmds.loadPlugin("fbxmaya")

    @staticmethod
    @contextlib.contextmanager
    def embed_media_write_cwd():
        """Yield with the process CWD at the workspace root when the live FBX
        settings embed media; restore the caller's CWD afterward.

        The fbxmaya plugin locates embed-media textures with plain OS path
        resolution — relative ``fileTextureName`` values resolve against the
        process CWD, never the workspace (probe-proven 2026-08-04: with a
        correct workspace and a foreign CWD every relative texture is silently
        dropped from the embed; with a foreign workspace and the CWD at the
        project root, embedding succeeds).  GUI Maya never chdirs on Set
        Project, so any embed-media write with project-relative texture paths
        needs this.  Wrap the actual FBX write (``cmds.file(type="FBX
        export")`` or MEL ``FBXExport``) *after* presets/options are applied,
        since the gate queries the live ``FBXExportEmbeddedTextures`` value.
        No-op when embedding is off or the workspace root is unavailable.
        """
        original_cwd = os.getcwd()
        try:
            embed = bool(mel.eval("FBXExportEmbeddedTextures -q"))
        except Exception:
            embed = False
        ws_root = cmds.workspace(query=True, rootDirectory=True) if embed else ""
        try:
            if ws_root and os.path.isdir(ws_root):
                os.chdir(ws_root)
            yield
        finally:
            os.chdir(original_cwd)

    @staticmethod
    def reset_import():
        """Reset the FBX plugin's global IMPORT options to factory defaults.

        Import options are sticky across the session -- whatever the last
        (often interactive) FBX import set silently shapes every later
        ``cmds.file(i=True, type="FBX")``. Call before applying options so an
        import starts deterministic: the import twin of ``FBXResetExport``.
        Note the factory default mode is ``merge`` ("add and update
        animation"), which can retarget animation onto same-named nodes
        already in the scene -- pair with ``FBXImportMode: add`` (the
        :attr:`_DEFAULT_IMPORT_OPTIONS` choice) when pre-existing scene state
        must never be touched.
        """
        FbxUtils.load_plugin()
        mel.eval("FBXResetImport")
        # Maya 2025 / FBX 2020.3.6 quirk (probed 2026-08-14): the factory
        # state ``FBXResetImport`` restores selects the **"No Animation"**
        # import take (``Import|IncludeGrp|Animation|ExtraGrp|Take``), where a
        # fresh session imports the file's last take — so after any reset,
        # every raw FBX import silently drops its animCurves while the attrs
        # themselves land. Re-select the take with index **-1**, which the
        # importer resolves per file at import time (probed 2026-08-17):
        # last take when the file has any (the fresh-session choice, also for
        # multi-take files), and simply "none" for a takeless file. A FIXED
        # index (``-ti 1``, index 0 being the "No Animation" entry) is NOT
        # equivalent: it is a hard requirement at import time, so a file with
        # zero takes — every static-asset export with animation off — aborts
        # with ``FBXImport error: take not found`` and ``cmds.file`` returns
        # NO nodes without raising, silently emptying every later import
        # (the hierarchy-sync "won't load an FBX reference" failure).
        mel.eval("FBXImportSetTake -ti -1")

    @staticmethod
    def reset_export():
        """Reset the FBX plugin's global EXPORT options to factory defaults.

        The twin :meth:`reset_import` already named. Export options are sticky
        across the session in the same way import options are, and the ones
        that leak decide a deliverable's STRUCTURE rather than a detail of it:
        ``FBXExportInstances`` is the measured case -- the substance bridge
        turns it off for its own exports, which is exactly why
        :meth:`MayaExportMixin._fbx_options` pins it back. A writer that pins
        nothing then inherits whatever ran before it, and the same scene
        exported twice in one session ships a different mesh count.

        Call before applying options (or before an unconfigured write) so an
        export starts deterministic. Unlike :meth:`reset_import` there is no
        Maya-2025 quirk to repair afterwards: the factory export state is the
        one a fresh session has.
        """
        FbxUtils.load_plugin()
        mel.eval("FBXResetExport")

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

        # Write from the workspace root when embedding media, so
        # project-relative texture paths resolve exactly as Maya resolves
        # them (see embed_media_write_cwd).
        with cls.embed_media_write_cwd():
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
        file_path = os.path.abspath(
            os.path.expandvars(os.path.expanduser(str(file_path)))
        )
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"FBX not found: {file_path}")

        cls.load_plugin()
        # Factory baseline first: sticky options OUTSIDE the applied dict
        # (scale factor, axis conversion, ...) must not leak into this import.
        try:
            cls.reset_import()
        except RuntimeError:
            logger.debug("FBXResetImport unavailable (OK).")
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
                    cmds.scriptEditorInfo(suppressErrors=False, suppressWarnings=False)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Animation takes (generic — any tool can declare takes on a node)
    # ------------------------------------------------------------------

    @staticmethod
    def set_bake_range_from_scene() -> Tuple[float, float]:
        """Point the bake range at the scene's ANIMATION range.

        ``animationStartTime``/``animationEndTime`` -- the authored extent --
        not ``minTime``/``maxTime``, which is the playback slider the artist
        happens to have narrowed. An export wants everything that was authored,
        and a scrubbed-in slider is not a statement about the deliverable.

        ``FBXResetExport`` leaves the range at the plugin's factory **1-48**
        (measured on Maya 2025), which is not the scene's anything: an animated
        export that nothing else configured ships 48 frames of whatever
        timeline it actually had. :meth:`apply_takes` sets a union range
        whenever the scene DECLARES takes, so this is the fallback for the case
        it cannot cover -- animation requested, no shots declared.

        Returns:
            The ``(start, end)`` it set, so a caller can report it.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        FbxUtils.load_plugin()
        start, end = AnimUtils.scene_animation_range()
        mel.eval(f"FBXExportBakeComplexStart -v {start}")
        mel.eval(f"FBXExportBakeComplexEnd -v {end}")
        return start, end

    @staticmethod
    def baking_enabled() -> bool:
        """Whether the next write will BAKE complex animation.

        Reads through this, never ``bool(mel.eval(...))``: the FBX plugin
        answers this query with the STRING ``"true"``/``"false"`` (measured on
        Maya 2025), and ``bool("false")`` is **True** -- so every direct
        truthiness test on it silently read as ON. That made
        ``set_bake_animation_range``'s "baking is disabled, skipping" branch
        unreachable, and made the take-apply capture record a bake flag that
        was off as on, so restoring it turned baking ON for the user.
        ``FBXExportEmbeddedTextures`` answers with an int and is unaffected.
        """
        try:
            FbxUtils.load_plugin()
            value = mel.eval("FBXExportBakeComplexAnimation -q")
        except Exception as e:
            logger.debug(f"Could not read the FBX bake flag: {e}")
            return False
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1")
        return bool(value)

    @staticmethod
    def bake_range() -> Optional[Tuple[float, float]]:
        """The ``(start, end)`` frames the next write will actually BAKE.

        The reader for what :meth:`set_bake_range_from_scene` and
        :meth:`apply_takes` (its declared-take union) put there -- and NOT
        ``playbackOptions``, which stays wherever the scene left it and
        routinely starts earlier than anything that ships.

        Anyone describing the exported stack's ORIGIN needs this one: the bake
        writes a key on every frame of the range, so its start IS the stack's
        first key, and a glTF converter rebases every stack onto its first
        key. Publishing the scene's earliest key instead slid every shot in a
        production deliverable by 33 frames (see the 2026-09-01 CHANGELOG
        entry).

        Returns:
            The range, or None when the write will not bake -- the file then
            carries the scene's own keys and there is no single range to name.
        """
        try:
            if not FbxUtils.baking_enabled():
                return None
            FbxUtils.load_plugin()
            return (
                float(mel.eval("FBXExportBakeComplexStart -q")),
                float(mel.eval("FBXExportBakeComplexEnd -q")),
            )
        except Exception as e:
            logger.debug(f"Could not read the FBX bake range: {e}")
            return None

    #: The plugin property that decides whether an export carries ANY animation.
    #: It is not one of the ``FBXExport*`` commands -- it is the Include-group
    #: checkbox ("Animation" in the export dialog), reachable only through
    #: ``FBXProperty``. Measured on Maya 2025: with it off, an export writes
    #: **zero** AnimationStacks whatever the bake flags say, so every
    #: ``FBXExportBakeComplexAnimation``/``SplitAnimationIntoTakes`` call is
    #: silently discarded. ``FBXResetExport`` restores it to on, but a loaded
    #: EXPORT PRESET carries its own value and bypasses that reset entirely --
    #: which is how a production assembly shipped a 70 MB FBX declaring 12
    #: shots and containing no animation at all (2026-08-30).
    ANIMATION_INCLUDE_PROPERTY = "Export|IncludeGrp|Animation"

    @staticmethod
    def animation_export_enabled() -> bool:
        """Whether this export will carry animation at all.

        The one question every animation option depends on and none of them
        ask. Best-effort: an unreadable property answers ``True``, because the
        factory value is on and a false alarm on every static export is worse
        than the silence this exists to end.
        """
        FbxUtils.load_plugin()
        try:
            return bool(
                mel.eval(f"FBXProperty {FbxUtils.ANIMATION_INCLUDE_PROPERTY} -q")
            )
        except Exception as error:  # noqa: BLE001 — a probe must not fail a write
            logger.debug(
                f"Could not read {FbxUtils.ANIMATION_INCLUDE_PROPERTY}: {error}"
            )
            return True

    @staticmethod
    def set_animation_export(enabled: bool) -> None:
        """Turn the Animation include group on or off (see :attr:`ANIMATION_INCLUDE_PROPERTY`)."""
        FbxUtils.load_plugin()
        value = "true" if enabled else "false"
        mel.eval(f"FBXProperty {FbxUtils.ANIMATION_INCLUDE_PROPERTY} -v {value}")

    @staticmethod
    def reset_takes() -> None:
        """Clear FBX take definitions and restore pre-takes bake-complex state.

        Take splits *and* the bake-complex enable/range are global, sticky
        exporter options: without the restore, the ``-v true`` + union range
        that :meth:`apply_takes` set would leak into every later export this
        session (and flip ``set_bake_animation_range``'s enabled check). The
        Animation include group :meth:`apply_takes` has to guarantee is
        restored with them, for the same reason and from the same capture --
        but only when that call actually FLIPPED it, so a property this build
        could not read is never written back on a guess.
        """
        FbxUtils.load_plugin()
        mel.eval("FBXExportSplitAnimationIntoTakes -c")
        saved = FbxUtils._saved_bake_state
        if saved is not None:
            FbxUtils._saved_bake_state = None
            enabled, start, end, animation_flipped = saved
            mel.eval(
                f"FBXExportBakeComplexAnimation -v {'true' if enabled else 'false'}"
            )
            mel.eval(f"FBXExportBakeComplexStart -v {start}")
            mel.eval(f"FBXExportBakeComplexEnd -v {end}")
            if animation_flipped:
                FbxUtils.set_animation_export(False)

    @staticmethod
    def apply_takes(takes: Iterable[Any]) -> int:
        """Configure FBX export to emit one AnimStack (Unity clip) per take.

        Enables bake-complex, sets the **union** bake range over all takes (safe
        regardless of whether Maya bakes per-take or clips from the global
        range), clears prior take state, then declares each take.

        Also guarantees the Animation include group
        (:attr:`ANIMATION_INCLUDE_PROPERTY`), because without it every line
        above is discarded by the plugin: an export preset that excludes
        animation made this method configure 12 takes, log that it had, and
        ship a file with zero AnimationStacks (measured on a production
        assembly, 2026-08-30). Forcing it is the same call this method already
        makes for bake-complex -- a caller asking for animation TAKES has asked
        for animation -- and :meth:`reset_takes` restores the user's value with
        the rest of the capture. The flip is WARNED rather than silent: it
        means the loaded preset disagrees with the export about what ships.

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
        # Capture the user's bake-complex settings once (reset_takes restores
        # them); reset_takes above already consumed any prior capture, so a
        # repeated apply never overwrites the true pre-takes state.
        # The include group is recorded as "did THIS call turn it on", not as
        # its prior value: reset_takes then writes only what was actually
        # changed, so a property this build could not read is never written
        # back on a guess (`animation_export_enabled` answers True when it
        # cannot read it).
        flipping = not FbxUtils.animation_export_enabled()
        if FbxUtils._saved_bake_state is None:
            FbxUtils._saved_bake_state = (
                FbxUtils.baking_enabled(),
                mel.eval("FBXExportBakeComplexStart -q"),
                mel.eval("FBXExportBakeComplexEnd -q"),
                flipping,
            )
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval(f"FBXExportBakeComplexStart -v {union_start}")
        mel.eval(f"FBXExportBakeComplexEnd -v {union_end}")
        if flipping:
            # The one setting that makes everything above a no-op. Warned, not
            # whispered: it is the loaded preset overruling the export, and the
            # only visible symptom is a deliverable that plays nothing.
            FbxUtils.set_animation_export(True)
            logger.warning(
                "FBX animation export was DISABLED "
                f"({FbxUtils.ANIMATION_INCLUDE_PROPERTY} off -- an export preset "
                f"that excludes animation); enabled it for the {len(norm)} "
                "declared take(s), which would otherwise have shipped a file "
                "with no animation at all. Restored after the write."
            )

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

        # The default resolves through DataNodes so a duplicate carrier short
        # name (imported copy under a group) can't ambiguate the plug reads.
        node = node or DataNodes.get_export_node(create=False)
        attr = attr or DataNodes.FBX_TAKES

        if (
            node is None
            or not cmds.objExists(node)
            or not cmds.attributeQuery(attr, node=node, exists=True)
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
        # After "shots": it reads back the fbx_takes and fps that shots has
        # just republished, to place each gate against its own clip's zero.
        "visibility": (
            "mayatk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "refresh_export_metadata",
        ),
        # After "visibility": stages the curve-proxy transport for every keyed
        # channel and suspends the viewport bindings, so the FBX carries the
        # authored materials and one per-object curve per channel.
        "render_effects": (
            "mayatk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "prepare_for_export",
        ),
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
        "emissive_groups": (
            "mayatk.mat_utils.emissive_groups",
            "EmissiveGroups",
            "refresh_export_metadata",
        ),
        "lightmap": (
            "mayatk.light_utils.lightmap_baker.lightmap_baker",
            "LightmapBaker",
            "refresh_export_metadata",
        ),
    }

    @staticmethod
    def run_export_preparers(
        include_known: bool = True, only: Optional[Iterable[str]] = None
    ) -> None:
        """Refresh every producer's ``data_export`` channel once, right now.

        Runs each registered session preparer, then (when *include_known*)
        every :attr:`_KNOWN_PRODUCERS` entry not already covered by a
        registered preparer of the same name.  Each producer is isolated —
        one failing or unimportable subsystem never blocks the others — and
        each no-ops when it has nothing to write, so a metadata-free scene
        leaves no carrier behind.  This is the one call an export pipeline
        needs to make the carrier current.

        *only* narrows the run to the named producers.  Because a producer with
        nothing to publish CLEARS its channel, refreshing the whole set is safe
        only where the producers are the authority on every channel — an export
        pipeline.  A hand-off that merely SHIPS the carrier must not clear a
        manifest it cannot regenerate (measured: refreshing the full set from a
        bridge wiped a ``lightmap_metadata`` the scene's markers no longer
        described, and the preview then shipped that asset unlit), so it names
        the channels derived from live scene state and leaves the rest as
        authored.
        """
        wanted = None if only is None else set(only)
        # The selection IS the export set for a selected-only write, and a
        # producer that creates a node (the carrier, a proxy) can replace it --
        # measured: a bracketed ``FBXExport -s`` shipped only ``data_export``.
        # Restored on the way out, whatever the producers did.
        selection = cmds.ls(selection=True, long=True) or []
        try:
            FbxUtils._run_preparers(wanted, include_known)
        finally:
            try:
                if selection:
                    cmds.select(selection, replace=True, noExpand=True)
                else:
                    cmds.select(clear=True)
            except Exception:  # a producer may have deleted a selected node
                logger.debug("Selection not restored after preparers.", exc_info=True)

    @staticmethod
    def _run_preparers(wanted, include_known: bool) -> None:
        """The preparer loop of :meth:`run_export_preparers` (selection-agnostic)."""
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
            if wanted is not None and name not in wanted:
                continue
            ran.add(name)
            try:
                prepare()
            except Exception:  # one subsystem's failure must not block others
                logger.warning("Export preparer %r failed.", name, exc_info=True)
        # A conditional block rather than an early return, so the finalizer
        # below is reached on BOTH paths. The session hook calls this with
        # include_known=False, and an early return here left every File > Export
        # / Game Exporter FBX carrying channels with nothing describing them --
        # exactly the gap the finalizer exists to close.
        if include_known:
            for name, (
                module_path,
                cls_name,
                method,
            ) in FbxUtils._KNOWN_PRODUCERS.items():
                if name in ran or (wanted is not None and name not in wanted):
                    continue
                try:
                    producer = getattr(importlib.import_module(module_path), cls_name)
                    refresh = getattr(producer, method)
                except Exception:
                    # Producers are speculative — an uninstalled subsystem is fine.
                    logger.debug(
                        "Producer %r unavailable; skipped.", name, exc_info=True
                    )
                    continue
                try:
                    refresh()
                except Exception:
                    # But a resolvable producer that fails would silently ship
                    # stale channels — surface it like a registered preparer.
                    logger.warning("Producer %r refresh failed.", name, exc_info=True)
        FbxUtils._stamp_export_handoff()

    # Export FINALIZERS: the after-export twin of the preparers. A producer
    # that stages transient scene state for the write (curve-proxy transport
    # nodes, suspended viewport bindings) undoes it here. Known finalizers
    # mirror ``_KNOWN_PRODUCERS`` so the Scene Exporter's full pass needs no
    # registration; the session hook runs registered ones only.
    _KNOWN_FINALIZERS = {
        "render_effects": (
            "mayatk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "finish_export",
        ),
    }
    _export_finalizers = {}  # name -> callable, run after each FBX export
    #: Depth of :meth:`export_prepared` / :meth:`scratch_export` brackets.
    #: While one is open it owns the prepare/finalize lifecycle, and the
    #: session's before/after hooks step aside -- otherwise the FBX write
    #: inside the context would finalize (re-binding previews, deleting
    #: proxies) before the GLB conversion that follows it has read the scene.
    #: ONE counter for the whole process, not a class attribute: a module
    #: reload (or the test harness's between-module purge) rebinds
    #: ``FbxUtils`` to a new class while the hooks an earlier copy registered
    #: with OpenMaya keep running, and a depth kept on the class was invisible
    #: across copies -- measured 2026-09-05, the RizomUV round-trip's
    #: bracketed write still ran the shots preparer. Read here, written
    #: through :meth:`_bracket_depth_add`.
    _export_depth = _BracketDepth()

    @staticmethod
    def _bracket_state() -> dict:
        """The process-wide bracket state, kept where a reload cannot reach."""
        import __main__

        state = getattr(__main__, "_mayatk_fbx_bracket_state", None)
        if state is None:
            state = {"depth": 0}
            __main__._mayatk_fbx_bracket_state = state
        # setdefault, not a literal: a session that started on a build without
        # the ids already has a state dict, and replacing it would lose the depth.
        state.setdefault("auto_takes_ids", [])
        return state

    @staticmethod
    def _set_auto_takes_ids(ids, handler=None) -> None:
        """Record the live callback ids and WHOSE handler they call.

        The handler is the identity check: ids alone cannot say whether the
        registered callbacks belong to this copy of the module or to one a
        reload left behind.
        """
        state = FbxUtils._bracket_state()
        state["auto_takes_ids"] = list(ids or [])
        state["auto_takes_handler"] = handler

    @staticmethod
    def _auto_takes_are_current() -> bool:
        """Do the live callbacks call THIS copy's handler?

        Every reload shape produces a new function object, so identity answers
        it for both: `importlib.reload` (the live dev path) re-executes into the
        same module dict, and a purge-and-reimport (what the test harness does
        between modules) builds a new one. Either way the pair Maya still holds
        calls the outgoing copy, and reinstalling is the only way to make the
        hook run current code.
        """
        state = FbxUtils._bracket_state()
        return bool(state["auto_takes_ids"]) and (
            state.get("auto_takes_handler") is FbxUtils._on_before_export
        )

    @staticmethod
    def _bracket_depth_add(delta: int) -> int:
        """Move the bracket depth by *delta* (floored at 0); return the new depth."""
        state = FbxUtils._bracket_state()
        state["depth"] = max(state["depth"] + delta, 0)
        return state["depth"]

    @staticmethod
    def register_export_finalizer(name: str, finish: Callable[[], Any]) -> None:
        """Run *finish* after every FBX export this session (installs the hook).

        Re-registering the same *name* replaces it; see
        :func:`unregister_export_finalizer`.
        """
        FbxUtils._export_finalizers[name] = finish
        FbxUtils._sync_auto_export_hook()

    @staticmethod
    def unregister_export_finalizer(name: str) -> None:
        FbxUtils._export_finalizers.pop(name, None)
        FbxUtils._sync_auto_export_hook()

    @staticmethod
    def run_export_finalizers(include_known: bool = True) -> None:
        """Undo every producer's export-time staging, right now.

        Each finalizer is isolated and idempotent -- a finalizer with nothing
        staged no-ops -- so running the set after an export that prepared
        nothing is safe.
        """
        import importlib

        ran = set()
        for name, finish in list(FbxUtils._export_finalizers.items()):
            ran.add(name)
            try:
                finish()
            except Exception:
                logger.warning("Export finalizer %r failed.", name, exc_info=True)
        if not include_known:
            return
        for name, (module_path, cls_name, method) in FbxUtils._KNOWN_FINALIZERS.items():
            if name in ran:
                continue
            try:
                finish = getattr(
                    getattr(importlib.import_module(module_path), cls_name), method
                )
            except Exception:
                logger.debug("Finalizer %r unavailable; skipped.", name, exc_info=True)
                continue
            try:
                finish()
            except Exception:
                logger.warning("Finalizer %r failed.", name, exc_info=True)

    @staticmethod
    def begin_export(only: Optional[Iterable[str]] = None) -> None:
        """Open an export bracket: run the preparers (outermost bracket only).

        Pair with :meth:`end_export` in a ``finally``; :meth:`export_prepared`
        is the context-manager form. While a bracket is open the session's
        before/after hooks stand down -- the bracket owns the lifecycle.
        """
        if FbxUtils._bracket_depth_add(1) == 1:
            try:
                FbxUtils.run_export_preparers(only=only)
            except BaseException:
                # The caller's ``finally: end_export()`` is not reached when
                # the bracket fails to OPEN; leave the depth as it was found.
                FbxUtils._bracket_depth_add(-1)
                raise

    @staticmethod
    def end_export() -> None:
        """Close an export bracket: run the finalizers (outermost bracket only)."""
        if FbxUtils._export_depth <= 0:
            return
        if FbxUtils._bracket_depth_add(-1) == 0:
            FbxUtils.run_export_finalizers()

    @staticmethod
    @contextlib.contextmanager
    def export_prepared(only: Optional[Iterable[str]] = None):
        """Prepare the carrier and scene for an export; finalize on exit.

        The one bracket an export pipeline needs: preparers run on entry
        (:meth:`run_export_preparers`), finalizers on exit
        (:meth:`run_export_finalizers`) -- AFTER everything inside the block,
        so an FBX write followed by a GLB conversion both see the prepared
        scene. Nested use is fine; the outermost bracket owns the lifecycle
        and the session before/after hooks stand down while one is open.
        """
        FbxUtils.begin_export(only=only)
        try:
            yield
        finally:
            FbxUtils.end_export()

    @staticmethod
    @contextlib.contextmanager
    def scratch_export():
        """Bracket for a THROWAWAY FBX write: the session hooks stand down.

        A UV round-trip's duplicates or a bake source is not a deliverable,
        so nothing inside prepares the scene for one: no registered preparer
        runs, no producer stamps the shared ``data_export`` carrier, no
        declared take is applied, and no finalizer follows -- the scene is
        left exactly as the caller found it. Measured 2026-09-04: with a
        Shots preparer armed, the RizomUV round-trip's plain ``cmds.file``
        export created ``data_export`` in the user's scene and undo brought
        it back. Nests inside :meth:`export_prepared` (an outer bracket keeps
        ownership); an ``export_prepared`` opened INSIDE it prepares nothing,
        which is what "scratch" means.
        """
        FbxUtils._bracket_depth_add(1)
        try:
            yield
        finally:
            FbxUtils._bracket_depth_add(-1)

    @staticmethod
    def _stamp_export_handoff() -> None:
        """Publish the standalone-reader contract describing the carrier's channels.

        A FINALIZER, not a producer, which is why it is called here rather than
        added to :attr:`_KNOWN_PRODUCERS`: it describes what the producers
        wrote, so it has to run after all of them, and it has to run on BOTH
        entry points — the Scene Exporter's full pass and the session hook's
        ``include_known=False`` pass — where a ``_KNOWN_PRODUCERS`` entry would
        be skipped by the latter and ship channels with nothing explaining
        them.

        Text and schema come from ``ptk.MeshConvert.build_fbx_handoff`` so the
        FBX's account of the pipeline cannot drift from the GLB's (blendertk
        reaches the same builder; the two packages cannot import each other).
        The channel LIST is read back off the carrier, so the block describes
        the file that is actually about to ship.

        Never creates the carrier and never stamps an empty one: an absent
        ``data_export`` means the scene has no in-band metadata, and a node
        holding only a handoff that describes nothing is worse than no node.
        Fully best-effort — self-description must not be able to fail an export.
        """
        try:
            from mayatk.env_utils._env_utils import EnvUtils
            from mayatk.node_utils.data_nodes import DataNodes

            if DataNodes.get_export_node(create=False) is None:
                return
            channels = (DataNodes.dump(decode=False) or {}).get("data_export") or {}
            block = ptk.MeshConvert.build_fbx_handoff(
                channels,
                source={
                    "application": "maya",
                    "version": cmds.about(version=True),
                    # Provenance, not identity — see the builder's docstring.
                    "scene": os.path.basename(EnvUtils.saved_scene_path() or "")
                    or None,
                },
            )
            DataNodes.set_export_json(ptk.MeshConvert.FBX_HANDOFF_CHANNEL, block)
        except Exception:  # noqa: BLE001 — a missing description never costs the export
            logger.debug("Export handoff block not stamped.", exc_info=True)

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
        want = (
            FbxUtils._explicit_auto_takes
            or bool(FbxUtils._export_preparers)
            or bool(FbxUtils._export_finalizers)
        )
        if want and not FbxUtils._auto_takes_are_current():
            # Not just "is anything installed": a pair left by a previous copy
            # of this module is installed and useless, calling a handler whose
            # preparer registry no longer exists.
            FbxUtils._install_auto_export_hook()
        elif not want and FbxUtils._auto_takes_ids:
            FbxUtils._remove_auto_export_hook()

    @staticmethod
    def _on_before_export(*_):
        """Run every registered preparer (isolated), then realize declared takes.

        Registered-only (no known-producer fallback): the session hook is
        opt-in per subsystem, so a producer that unregistered stays out.
        Stands down while an :meth:`export_prepared` bracket is open -- that
        bracket already ran the preparers and owns the finalize.
        """
        if FbxUtils._export_depth:
            return
        FbxUtils.run_export_preparers(include_known=False)
        FbxUtils.apply_takes_from_node()

    @staticmethod
    def _on_after_export(*_):
        """Clear take state and undo export-time staging (registered finalizers)."""
        FbxUtils.reset_takes()
        if FbxUtils._export_depth:
            return
        FbxUtils.run_export_finalizers(include_known=False)

    @staticmethod
    def _install_auto_export_hook() -> None:
        import maya.api.OpenMaya as om

        # Reload guard, and it has to work without a surviving object: whatever
        # a previous copy of this module left registered is removed by ID from
        # the process-wide store. Going through ScriptJobManager did not, because
        # a live reload rebinds that class too and its singleton is a class
        # attribute -- the new manager holds nothing, unsubscribes nothing, and
        # both copies' hooks stay live (every preparer ran twice per export).
        FbxUtils._remove_auto_export_hook(quiet=True)
        # Registered straight with OpenMaya rather than through the manager: the
        # ids ARE the handle, this hook is session-scoped with no widget to hang
        # a lifetime on, and the owner key was never used outside this module.
        FbxUtils._set_auto_takes_ids(
            [
                om.MSceneMessage.addCallback(
                    om.MSceneMessage.kBeforeExport, FbxUtils._on_before_export
                ),
                om.MSceneMessage.addCallback(
                    om.MSceneMessage.kAfterExport, FbxUtils._on_after_export
                ),
            ],
            handler=FbxUtils._on_before_export,
        )
        logger.info(
            "Auto-export hook enabled (%d preparer(s)).",
            len(FbxUtils._export_preparers),
        )

    @staticmethod
    def _remove_auto_export_hook(quiet: bool = False) -> None:
        """Remove the live pair by id. *quiet* suppresses the log for a reinstall."""
        import maya.api.OpenMaya as om

        live = FbxUtils._bracket_state()["auto_takes_ids"]
        for cb_id in list(live):
            try:
                om.MMessage.removeCallback(cb_id)
            except Exception:  # noqa: BLE001 - a stale id is already gone
                logger.debug("auto-export callback %r already removed", cb_id)
        FbxUtils._set_auto_takes_ids([])
        # A session that reloaded ACROSS this change still has the old pair in a
        # surviving manager under the owner key; clear it once so the upgrade
        # does not leave a hook behind. Harmless when there is nothing to clear.
        try:
            from mayatk.core_utils.script_job_manager import ScriptJobManager

            ScriptJobManager.instance().unsubscribe_all(FbxUtils._AUTO_TAKES_OWNER)
        except Exception:  # noqa: BLE001 - the manager is optional to this path
            pass
        if not quiet:
            logger.info("Auto-export hook disabled.")

    @staticmethod
    def is_auto_takes_enabled() -> bool:
        """Return whether the auto-takes export hook is currently registered."""
        return bool(FbxUtils._auto_takes_ids)
