# !/usr/bin/python
# coding=utf-8
import os
import logging
import contextlib
from typing import Optional, Dict, Any, List, Iterable, Callable, Tuple, Set

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
    _explicit_auto_takes = False  # enable_auto_takes() with no producer or stager
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

    @staticmethod
    def drop_rig_apparatus(
        file_path: str,
        scope: Optional[Iterable[str]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> Optional[Dict[str, Any]]:
        """Remove the apparatus of the rigs a written FBX baked, in place.

        A carrier ships every node of the scene, so a baked rig arrives twice:
        its motion on the joints and meshes, and the controls, IK, constraint
        helpers, dead joints and the groups holding only those -- each still
        animated, and each baked again by FBX2glTF at every frame (measured on a
        production assembly: ~600 of ~2480 GLB nodes, half its animation data).
        This is the FBX half of the rule the Blender bridge applies after its
        import: :meth:`RigGraphExtractor.machinery` names what this scene's rigs
        left inert, and ``ptk.FbxMedia.drop_apparatus`` removes what the file
        agrees on -- refusing, by name, anything it can see is still
        load-bearing (a mesh below, a live skin influence, a scene-record
        carrier). The Scene Exporter's Exclude Rig Helpers row and the WebXR
        preview both run it between their write and the GLB conversion.

        The scene is never touched: the apparatus still drives the motion the
        write sampled, and the next export needs it again. Never raises -- a
        failure is a warning and the file stays as written: a cleanup never
        costs the deliverable.

        Parameters:
            file_path: The FBX just written from this scene.
            scope: The export's DAG roots -- what was selected for the write;
                ``None`` reads the whole scene.
            logger: Where the outcome is said; this module's otherwise.

        Returns:
            ``ptk.FbxMedia.drop_apparatus``'s report (``"models"``, ``"kinds"``,
            ``"refused"``, ``"objects"``, ``"connections"``) plus ``"kept"``:
            the apparatus kept because a surviving node shares its short name.
            ``None`` when the pass failed. The file is not rewritten when
            nothing qualifies.
        """
        from mayatk.rig_utils.rig_graph_extract import RigGraphExtractor

        log = logger or logging.getLogger(__name__)
        try:
            section, kept = RigGraphExtractor().machinery(
                scope=None if scope is None else list(scope)
            )
            report = ptk.FbxMedia.drop_apparatus(file_path, section=section)
        except Exception as error:  # noqa: BLE001 -- a cleanup, never the export
            log.warning(f"Rig helpers: the FBX keeps them -- the pass failed: {error}")
            log.debug("Rig-helper pass failed.", exc_info=True)
            return None
        report["kept"] = list(kept)
        if report["models"]:
            kinds = ", ".join(f"{n} {kind}" for kind, n in report["kinds"].items())
            log.info(
                f"Rig helpers: excluded {report['models']} node(s) from the FBX "
                f"({kinds}); their motion is already on what they drove."
            )
        else:
            log.debug("Rig helpers: the export carries none.")
        spared = sorted(set(report["refused"]) | set(report["kept"]))
        if spared:
            log.info(
                f"Rig helpers: kept {len(spared)} node(s) something still needs "
                "(a mesh below, a live skin, scene data, or a name a kept node "
                "shares): "
                + ", ".join(spared[:10])
                + (" …" if len(spared) > 10 else "")
            )
        return report

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
            takes: Sequence of ``{"name","start","end"}`` mappings (what
                ``ptk.SceneRecords.declared_takes`` returns) or
                ``(name, start, end)`` tuples.

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
    def declared_takes() -> list:
        """The takes the scene's records declare -- each ``shot_metadata``
        clip's range, else a legacy ``fbx_takes`` list
        (``ptk.SceneRecords.declared_takes`` over the carrier): what
        :meth:`apply_takes_from_node` realizes by default, and so what an
        export will split.  ``[]`` when nothing is declared."""
        from mayatk.node_utils.data_nodes import DataNodes

        return (
            ptk.SceneRecords.declared_takes(
                lambda key: ptk.SceneRecords.resolve(key).load(DataNodes)
            )
            or []
        )

    @staticmethod
    def apply_takes_from_node(
        node: Optional[str] = None, attr: Optional[str] = None
    ) -> int:
        """Realize the takes the scene declares into FBX export state.

        Defaults to the shot record on the shared carrier -- each
        ``shot_metadata`` clip's range, or the legacy ``fbx_takes`` channel of
        a scene published before 0.17.0 (``ptk.SceneRecords.declared_takes``)
        -- so this is shot-agnostic: it realizes whatever takes the scene
        declares.  An explicit *node* / *attr* reads a JSON take list off any
        node instead.

        Returns:
            int: Number of takes defined (0 if nothing is declared).
        """
        import json
        from mayatk.node_utils.data_nodes import DataNodes

        if node is None and attr is None:
            defs = FbxUtils.declared_takes()
        else:
            node = node or DataNodes.get_export_node(create=False)
            attr = attr or ptk.SceneRecords.FBX_TAKES.key
            if (
                node is None
                or not cmds.objExists(node)
                or not cmds.attributeQuery(attr, node=node, exists=True)
            ):
                return 0
            raw = cmds.getAttr(f"{node}.{attr}")
            try:
                defs = json.loads(raw) if raw else None
            except (ValueError, TypeError):
                logger.warning(f"Could not parse take defs from {node}.{attr}")
                return 0
        if not defs:
            return 0
        return FbxUtils.apply_takes(defs)

    # ------------------------------------------------------------------
    # Export metadata: producers, stagers, the bracket and the session hook
    # ------------------------------------------------------------------
    #
    # A PRODUCER computes one scene record from live scene state and RETURNS
    # it; it never writes.  ``ptk.ExportSnapshot`` orders the producers by
    # the records' declared dependencies, hands each the ``ptk.ExportContext``
    # (the exporter's decisions as input, plus every record produced before
    # it) and commits the carrier ONCE, handoff block included -- so an
    # exporter's decision (the clip mode, the clip origin) is an input the
    # producer reads, never a patch applied after it that a second run would
    # overwrite.  A STAGER mutates the scene for the write (the curve-proxy
    # transport, a preview standing down) and undoes it after; it produces
    # no record.  The export bracket owns both lifecycles.  The session hook
    # runs the subsystems that opted in for ANY FBX export (File > Export, the
    # Game Exporter, a raw ``cmds.file``), so their records ride out fresh.

    #: The records this DCC produces: ``ptk.SceneRecords`` spec -> (module,
    #: class, classmethod taking the ExportContext).  Add a producer HERE and
    #: declare its record THERE; an unregistered key fails
    #: ``SceneRecords.check_producers`` (pinned by test_fbx_export_preparers).
    #: Resolved lazily, so an uninstalled subsystem is skipped rather than
    #: blocking an export.  Order is irrelevant: the snapshot orders by the
    #: records' ``after``.
    PRODUCERS: Dict[Any, Tuple[str, str, str]] = {
        ptk.SceneRecords.SHOTS: (
            "mayatk.anim_utils.shots._shots",
            "ShotStore",
            "produce_export_records",
        ),
        ptk.SceneRecords.VISIBILITY: (
            "mayatk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "export_record",
        ),
        ptk.SceneRecords.AUDIO: (
            "mayatk.audio_utils.audio_clips._audio_clips",
            "AudioClips",
            "export_record",
        ),
        ptk.SceneRecords.SHADOWS: (
            "mayatk.rig_utils.shadow_rig",
            "ShadowRig",
            "export_record",
        ),
        ptk.SceneRecords.EMISSIVE_GROUPS: (
            "mayatk.mat_utils.emissive_groups",
            "EmissiveGroups",
            "export_record",
        ),
        ptk.SceneRecords.LIGHTMAPS: (
            "mayatk.light_utils.lightmap_baker.lightmap_records",
            "LightmapRecords",
            "export_record",
        ),
    }

    #: Export stagers: name -> (module, class, prepare, finish).  ``prepare``
    #: runs when a bracket opens (after which the producers see the staged
    #: scene), ``finish`` when it closes -- AFTER the FBX write and the GLB
    #: conversion that follows it, in reverse order, so the artist gets the
    #: viewport back as it was.
    STAGERS: Dict[str, Tuple[str, str, str, str]] = {
        "render_effects": (
            "mayatk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "prepare_for_export",
            "finish_export",
        ),
    }

    #: Record keys whose producer runs on the any-export session hook
    #: (``enable_export_producer``): authoring a shot, creating an audio
    #: track opts that subsystem in, so a File > Export carries its record.
    _session_producers: Set[str] = set()
    #: Session stagers: name -> (prepare, finish), either may be None.  Run
    #: by every bracket AND by the session hook (a preview that must detach
    #: before the write registers here).
    _session_stagers: Dict[str, Tuple[Optional[Callable], Optional[Callable]]] = {}
    @staticmethod
    def _resolve_row(
        label: str, module_path: str, cls_name: str, *methods: str
    ) -> Optional[Tuple[Optional[Callable], ...]]:
        """The callables a :attr:`PRODUCERS` / :attr:`STAGERS` row names.

        ``None`` when the row's module or class cannot be imported -- an
        uninstalled subsystem is fine, and only debug-logged.  A method the
        class does not have resolves to ``None`` with a WARNING: that is a
        misspelt row, and skipping it quietly would stop a record shipping (or
        a stager finishing) with no sign of why.
        """
        import importlib

        try:
            owner = getattr(importlib.import_module(module_path), cls_name)
        except Exception:  # an uninstalled subsystem is fine
            logger.debug("%s unavailable; skipped.", label, exc_info=True)
            return None
        resolved = []
        for method in methods:
            fn = getattr(owner, method, None)
            if fn is None:
                logger.warning(
                    "%s names %s.%s.%s, which does not exist; skipped.",
                    label,
                    module_path,
                    cls_name,
                    method,
                )
            resolved.append(fn)
        return tuple(resolved)

    @classmethod
    def producers(cls, only: Optional[Iterable[Any]] = None) -> Dict[Any, Callable]:
        """:attr:`PRODUCERS` resolved to callables, unimportable ones skipped;
        *only* (specs or keys) narrows the table."""
        wanted = (
            None if only is None else {ptk.SceneRecords.resolve(k).key for k in only}
        )
        table: Dict[Any, Callable] = {}
        for spec, row in cls.PRODUCERS.items():
            if wanted is not None and spec.key not in wanted:
                continue
            resolved = cls._resolve_row(f"Producer for {spec.key!r}", *row)
            if resolved and resolved[0] is not None:
                table[spec] = resolved[0]
        return table

    @classmethod
    def export_context(
        cls, mode: str = ptk.ExportContext.PIPELINE, **decisions
    ) -> ptk.ExportContext:
        """A context for this scene: provenance filled in, *decisions*
        (``clip_mode``, ``clip_span``) as given."""
        from mayatk.env_utils._env_utils import EnvUtils

        source = {"application": "maya"}
        try:  # ``cmds`` is unbound outside Maya (the module's import guard)
            source["version"] = cmds.about(version=True)
            # Provenance, not identity -- see ``SceneRecords.handoff_block``.
            source["scene"] = (
                os.path.basename(EnvUtils.saved_scene_path() or "") or None
            )
        except Exception:  # noqa: BLE001 - provenance never costs an export
            pass
        return ptk.ExportContext(mode=mode, source=source, **decisions)

    @classmethod
    def publish(
        cls,
        ctx: Optional[ptk.ExportContext] = None,
        only: Optional[Iterable[Any]] = None,
    ) -> ptk.ExportSnapshot:
        """Assemble every producer's record and commit the carrier ONCE.

        The one call an export pipeline makes to bring the carrier current.
        *ctx* carries the exporter's decisions (a pipeline context for this
        scene by default); *only* narrows the run to the named records.  A
        hand-off context refreshes only the DERIVED records: a producer with
        nothing to say clears its record, and a bridge that merely ships the
        carrier is not the authority on a bake the scene's markers no longer
        describe (measured: a preview push wiped a lightmap manifest and
        previewed the asset unlit).  Each producer is isolated -- one failing
        subsystem never blocks the others, and its record is left as stored.

        Producers always see the STAGED scene: outside a bracket, the session
        stagers' ``prepare`` runs first (a shadow preview detaches before the
        shadow record is read -- the preview must never reach the record); a
        bracket that follows runs them again, which is why a stager's
        ``prepare`` must be idempotent, and runs their ``finish`` after the
        write.  An empty *only* still commits, so the handoff block is
        restamped to describe exactly what the carrier holds.

        The selection is preserved around the commit: the selection IS the
        export set for a selected-only write, and creating the carrier must
        not replace it (measured: a bracketed ``FBXExport -s`` shipped only
        ``data_export``).

        Returns:
            ptk.ExportSnapshot: What was produced and written -- the sidecar,
            the export log and the verifier read this object, not the node.
        """
        from mayatk.core_utils._core_utils import CoreUtils
        from mayatk.node_utils.data_nodes import DataNodes

        ctx = ctx or cls.export_context()
        with CoreUtils.preserved_selection():
            if not cls._export_depth:
                cls._run_stagers("prepare", dict(cls._session_stagers))
            snapshot = ptk.ExportSnapshot.assemble(cls.producers(only), ctx)
            snapshot.commit(DataNodes)
        return snapshot

    @classmethod
    def publish_authored(cls, records: Dict[Any, Any]) -> ptk.ExportSnapshot:
        """Commit a tool's own records at AUTHORING time, with this scene's
        provenance.

        *records* maps a record spec (or key) to a ``ptk.Record``, a payload,
        or a falsy value (the record is cleared) -- what
        ``ptk.ExportSnapshot.publish`` takes; this adds the context
        (:meth:`export_context` in ``AUTHORING`` mode), so the handoff block
        it restamps names the application and scene rather than ``null``.
        Runs no stager: an authoring publish is not a write.

        Returns:
            ptk.ExportSnapshot: The committed snapshot.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        return ptk.ExportSnapshot.publish(
            DataNodes, records, cls.export_context(mode=ptk.ExportContext.AUTHORING)
        )

    # -- stagers ---------------------------------------------------------

    @classmethod
    def stagers(
        cls, names: Optional[Iterable[str]] = None
    ) -> Dict[str, Tuple[Optional[Callable], Optional[Callable]]]:
        """The stager table for a bracket: the known stagers (*names* narrows
        them; ``None`` = all) resolved to callables, then every session stager
        -- those always run, a preview that must detach before the write is
        one."""
        table: Dict[str, Tuple[Optional[Callable], Optional[Callable]]] = {}
        for name, row in cls.STAGERS.items():
            if names is not None and name not in names:
                continue
            resolved = cls._resolve_row(f"Stager {name!r}", *row)
            if resolved is not None:
                table[name] = resolved
        table.update(cls._session_stagers)
        return table

    @classmethod
    def stage(
        cls, names: Optional[Iterable[str]] = None
    ) -> Dict[str, Tuple[Optional[Callable], Optional[Callable]]]:
        """Run every stager's ``prepare`` now and return the table that ran.

        The scene as the write will see it, without opening a bracket: the
        Scene Exporter stages from its publishing task, so the checks that
        run after that task and the hierarchy baseline the write records see
        the same nodes (the curve-proxy transport). The bracket that follows
        stages again -- ``prepare`` is idempotent -- and finishes after the
        write. *names* narrows the known stagers; session stagers always run.
        """
        table = cls.stagers(names)
        cls._run_stagers("prepare", table)
        return table

    @staticmethod
    def _run_stagers(phase: str, table) -> None:
        """Run one *phase* (``"prepare"`` / ``"finish"``) of every stager in
        *table*, each isolated; ``finish`` runs in reverse order (LIFO).

        The selection is preserved: it IS the export set of a selected-only
        write, and a stager that creates a node (a preview's shader) would
        replace it -- before the write, or the user's own after it.
        """
        from mayatk.core_utils._core_utils import CoreUtils

        items = list(table.items())
        if phase == "finish":
            items.reverse()
        with CoreUtils.preserved_selection():
            for name, (prepare, finish) in items:
                fn = prepare if phase == "prepare" else finish
                if fn is None:
                    continue
                try:
                    fn()
                except Exception:  # one subsystem's failure must not block others
                    logger.warning(
                        "Export stager %r failed to %s.", name, phase, exc_info=True
                    )

    # -- the bracket -------------------------------------------------------

    @classmethod
    def begin_export(
        cls,
        ctx: Optional[ptk.ExportContext] = None,
        only: Optional[Iterable[Any]] = None,
        stagers: Optional[Iterable[str]] = None,
    ) -> Optional[ptk.ExportSnapshot]:
        """Open an export bracket (outermost only): stage the scene, then --
        when a *ctx* or *only* is given -- publish.

        Pair with :meth:`end_export` in a ``finally``; :meth:`export_prepared`
        is the context-manager form.  While a bracket is open the session's
        before/after hooks stand down -- the bracket owns the lifecycle.  A
        pipeline that published from its own task opens the bracket with no
        context: the stagers run, nothing is produced twice.  A hand-off
        passes a HANDOFF context and gets its derived records refreshed.
        *stagers* names the known stagers to run (``None`` = all); session
        stagers always run.

        *only* narrows the publish to those records (specs or keys); given
        without a *ctx* it publishes with a pipeline context for this scene,
        which is what ``only`` meant before the bracket took a context.  Stager
        names belong in *stagers*, not here.

        Returns:
            The snapshot published here, or ``None``.
        """
        if cls._bracket_depth_add(1) != 1:
            return None
        try:
            if isinstance(ctx, (list, tuple, set, frozenset)):
                ctx, only = None, ctx  # the pre-2026-09-18 positional ``only``
            if only is not None:
                only = list(only)
                ctx = ctx or cls.export_context()
            cls._bracket_state()["stager_table"] = cls.stage(stagers)
            return cls.publish(ctx, only) if ctx is not None else None
        except BaseException:
            # The caller's ``finally: end_export()`` is not reached when the
            # bracket fails to OPEN: finish what was staged and leave the
            # depth as it was found.
            cls._bracket_depth_add(-1)
            table = cls._bracket_state().pop("stager_table", None)
            if table is not None:
                cls._run_stagers("finish", table)
            raise

    @classmethod
    def end_export(cls) -> None:
        """Close an export bracket: run the stagers' finish (outermost only)."""
        if cls._export_depth <= 0:
            return
        if cls._bracket_depth_add(-1) == 0:
            table = cls._bracket_state().pop("stager_table", None)
            cls._run_stagers("finish", table if table is not None else cls.stagers())

    @classmethod
    @contextlib.contextmanager
    def export_prepared(
        cls,
        ctx: Optional[ptk.ExportContext] = None,
        only: Optional[Iterable[Any]] = None,
        stagers: Optional[Iterable[str]] = None,
    ):
        """Stage the scene (and publish, given a *ctx* or *only* -- see
        :meth:`begin_export`) for an export; finish on exit -- AFTER
        everything inside the block, so an FBX write followed by a GLB
        conversion both see the staged scene.  Nested use is fine; the
        outermost bracket owns the lifecycle.  Yields the snapshot
        :meth:`begin_export` published, or ``None``."""
        snapshot = cls.begin_export(ctx, only, stagers)
        try:
            yield snapshot
        finally:
            cls.end_export()

    @staticmethod
    @contextlib.contextmanager
    def scratch_export():
        """Bracket for a THROWAWAY FBX write: the session hooks stand down.

        A UV round-trip's duplicates or a bake source is not a deliverable,
        so nothing inside prepares the scene for one: no stager runs, no
        producer publishes, no declared take is applied -- the scene is left
        exactly as the caller found it. Measured 2026-09-04: with a Shots
        producer enabled, the RizomUV round-trip's plain ``cmds.file`` export
        created ``data_export`` in the user's scene and undo brought it back.
        Nests inside :meth:`export_prepared` (an outer bracket keeps
        ownership); an ``export_prepared`` opened INSIDE it prepares nothing,
        which is what "scratch" means.
        """
        FbxUtils._bracket_depth_add(1)
        try:
            yield
        finally:
            FbxUtils._bracket_depth_add(-1)

    # -- the session hook: opt-in producers and stagers for ANY export --------

    @classmethod
    def enable_export_producer(cls, spec) -> None:
        """Run *spec*'s producer before every FBX export this session
        (installs the shared hook).  Idempotent; see
        :meth:`disable_export_producer`."""
        cls._session_producers.add(ptk.SceneRecords.resolve(spec).key)
        cls._sync_auto_export_hook()

    @classmethod
    def disable_export_producer(cls, spec) -> None:
        """Opt *spec* out of the session hook; the hook is torn down when
        nothing needs it."""
        cls._session_producers.discard(ptk.SceneRecords.resolve(spec).key)
        cls._sync_auto_export_hook()

    @classmethod
    def register_export_stager(
        cls,
        name: str,
        prepare: Optional[Callable[[], Any]] = None,
        finish: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Run *prepare* before and *finish* after every FBX export this
        session, and in every bracket.  *prepare* must be idempotent: a
        publish outside a bracket runs it so producers see the staged scene,
        and the bracket that follows runs it again.  Registering *name* again
        replaces the half given and keeps the other; see
        :meth:`unregister_export_stager`."""
        old_prepare, old_finish = cls._session_stagers.get(name, (None, None))
        cls._session_stagers[name] = (prepare or old_prepare, finish or old_finish)
        cls._sync_auto_export_hook()

    @classmethod
    def unregister_export_stager(cls, name: str) -> None:
        cls._session_stagers.pop(name, None)
        cls._sync_auto_export_hook()

    @staticmethod
    def enable_auto_takes() -> None:
        """Realize declared takes on **every** FBX export -- shot-agnostic.

        Installs the shared before-export hook directly: it applies whatever
        takes the shot record already declares.  A producer that must
        regenerate its record fresh at export time opts in through
        :meth:`enable_export_producer` instead (``ShotStore.enable_auto_export``
        does).  Idempotent.
        """
        FbxUtils._explicit_auto_takes = True
        FbxUtils._sync_auto_export_hook()

    @staticmethod
    def disable_auto_takes() -> None:
        """Clear the explicit enable; removes the hook if nothing else holds it."""
        FbxUtils._explicit_auto_takes = False
        FbxUtils._sync_auto_export_hook()

    @staticmethod
    def _sync_auto_export_hook() -> None:
        """Install/remove the shared hook to match the registries + explicit flag."""
        want = (
            FbxUtils._explicit_auto_takes
            or bool(FbxUtils._session_producers)
            or bool(FbxUtils._session_stagers)
        )
        if want and not FbxUtils._auto_takes_are_current():
            # Not just "is anything installed": a pair left by a previous copy
            # of this module is installed and useless, calling a handler whose
            # registries no longer exist.
            FbxUtils._install_auto_export_hook()
        elif not want and FbxUtils._auto_takes_ids:
            FbxUtils._remove_auto_export_hook()

    @staticmethod
    def _on_before_export(*_):
        """Publish the opted-in records (the session stagers stage first,
        inside :meth:`publish`), then realize the declared takes.  Always
        publishes, even with no producer opted in: the commit restamps the
        handoff block, so an FBX written by File > Export or the Game Exporter
        describes exactly the channels it carries.  Stands down while a
        bracket is open -- the bracket already did all of it."""
        if FbxUtils._export_depth:
            return
        FbxUtils.publish(only=sorted(FbxUtils._session_producers))
        FbxUtils.apply_takes_from_node()

    @staticmethod
    def _on_after_export(*_):
        """Clear take state and undo the session stagers' staging."""
        FbxUtils.reset_takes()
        if FbxUtils._export_depth:
            return
        FbxUtils._run_stagers("finish", dict(FbxUtils._session_stagers))

    #: Depth of :meth:`export_prepared` / :meth:`scratch_export` brackets.
    #: While one is open it owns the stage/finish lifecycle, and the
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
            "Auto-export hook enabled (%d session producer(s)).",
            len(FbxUtils._session_producers),
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
