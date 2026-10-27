# !/usr/bin/python
# coding=utf-8
import os
import re
import shutil
import logging
import contextlib
from typing import (
    Optional,
    Dict,
    Any,
    List,
    Iterable,
    Callable,
    NamedTuple,
    Tuple,
    Set,
)

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None

import pythontk as ptk

from mayatk.core_utils.plugins._plugins import Plugins

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
    # (bake_enabled, bake_start, bake_end, animation_flipped, resample_prior),
    # or None when nothing is pending. The fourth member is whether
    # apply_takes had to turn the Animation include group
    # (:attr:`ANIMATION_INCLUDE_PROPERTY`) ON -- it has to guarantee that or
    # every other member is a no-op. Recorded as the CHANGE rather than the
    # prior value so the restore writes only what was actually changed; the
    # fifth likewise -- Resample All's value before apply_takes changed it,
    # None when it did not.
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

    #: Where :meth:`import_scene` parks a take that disagrees with the takes it
    #: plays: ``(module, class, classmethod)`` naming the store whose
    #: ``stash(targets=, label=, metadata=)`` keeps it -- Key Stash. Resolved
    #: lazily (:meth:`_resolve_row`), as :attr:`PRODUCERS` are: the store sits
    #: in a layer above this one, and a store that cannot be imported leaves the
    #: take out with a warning instead of failing the import.
    TAKE_STASH: Tuple[str, str, str] = (
        "mayatk.anim_utils.key_stash._key_stash",
        "KeyStash",
        "active",
    )

    #: The tool code this module reaches, one ``(module, class, method)`` row
    #: per role, resolved lazily (:meth:`hook`): a tool that moves out of this
    #: library takes its row with it and registers it as an entry point
    #: (``ptk.SceneRecords.hook_row``), so these calls never import upward.
    HOOKS: Dict[str, Tuple[str, str, str]] = {
        "take_stash": TAKE_STASH,
        "dependency_dirs": ("mayatk.light_utils.lightmap_baker.lightmap_records", "LightmapRecords", "search_dirs"),
        "dependency_root": ("mayatk.light_utils.lightmap_baker.lightmap_records", "LightmapRecords", "walk_root"),
        "offset_fold": ("mayatk.anim_utils.smart_bake._smart_bake", "SmartBake", "fold_offsets"),
    }

    @classmethod
    def hook(cls, role: str) -> Optional[Callable]:
        """The callable :attr:`HOOKS` names for *role*, else the one an installed
        package registers for :attr:`HOST`; ``None`` when neither resolves (the
        caller then does without it, as for an unimportable producer)."""
        row = ptk.SceneRecords.hook_row(cls.HOOKS.get(role), cls.HOST, role)
        resolved = cls._resolve_row(f"Hook {role!r}", *row) if row else None
        return resolved[0] if resolved else None

    @staticmethod
    def load_plugin():
        """Ensure the fbxmaya plugin is loaded."""
        Plugins.load("fbxmaya")

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
        FbxUtils._default_import_take()

    @staticmethod
    def _default_import_take(if_loaded: bool = False) -> None:
        """Leave the importer's sticky take selector at ``-1``, which it
        resolves per file at read time (:meth:`reset_import`): the last take,
        as a fresh session reads it. Any fixed index binds every later read of
        every file -- one past a file's take count reads NOTHING, silently.

        Parameters:
            if_loaded: Only when the plug-in is already loaded -- before a read
                that MAY reach an FBX (a scene that references one): a plug-in
                that loads for it starts at the fresh-session choice anyway.
        """
        if if_loaded and not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            return
        FbxUtils.load_plugin()
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
        plan: Optional["FbxUtils.WritePlan"] = None,
    ) -> str:
        """Export geometry to an FBX file.

        A write that does not bake ships every ``offsetParentMatrix`` folded
        into its node's own channels, and every write ships a node that
        ignores its parent re-anchored under it (:meth:`offsets_folded`), put
        back once the file is written.

        Parameters:
            file_path: Destination ``.fbx`` path (directories are created automatically).
            objects: Nodes to export.  If *None*, the current selection is used.
            preset_file: Optional FBX export preset to load before exporting.
            options: Additional FBX MEL options applied *after* the preset.
            selection_only: If True export selected; if False export entire scene.
            plan: The writer's :meth:`write_plan`, already applied; None reads
                what the settings hold once *preset_file* and *options* are in.

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
        shipped = None if selection_only else cmds.ls(assemblies=True, long=True) or []
        with cls.embed_media_write_cwd(), cls.offsets_folded(shipped, plan):
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

    @staticmethod
    def embed_dependencies(
        file_path: str,
        search_dirs: Optional[Iterable[str]] = None,
        files: Optional[Iterable[Any]] = None,
        logger: Optional[logging.Logger] = None,
        walk_root: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Embed every file a written FBX's scene records name, in place.

        The records name their files by file name -- ``lightmaps`` its
        maps and reflection probe, ``shadows`` its silhouette, atlas
        and horizon maps -- and a lighting-only bake binds its maps to no
        material, so the FBX carried none of them and every consumer had them
        copied in beside it. ``ptk.FbxMedia.embed_dependencies`` puts each
        inside the file under its name, the form Unity's importer extracts,
        resolved where this scene keeps it: *search_dirs*, else
        :meth:`LightmapRecords.search_dirs` -- the folders the GLB build joins
        the same names against; what those miss is looked for anywhere
        under *walk_root*, the folder the exporter's path check walks. The
        audio the file's ``audio`` events play rides in too, each
        track's file under its event's name (:meth:`AudioUtils.track_files`).
        The last of the passes :meth:`finish_deliverable` runs.

        Never raises -- a failure is a warning and the file stays as written.
        A file that is not a binary FBX (an ASCII preset, which ships no
        carrier) is left alone.

        Parameters:
            file_path: The FBX just written from this scene.
            search_dirs: Folders to resolve the names against, in priority
                order; ``None`` asks the scene.
            walk_root: Where a name they miss is searched for, recursively;
                ``None`` takes the scene's texture root, ``""`` none.
            files: Further files to embed (paths or ``(name, path)`` pairs);
                ``None`` takes the audio the file's events play.
            logger: Where the outcome is said; this module's otherwise.

        Returns:
            ``ptk.FbxMedia.embed_dependencies``'s report (``"named"``,
            ``"embedded"``, ``"present"``, ``"missing"``, ``"bytes"``), or
            ``None`` when the file is not a binary FBX or the pass failed.
        """
        log = logger or logging.getLogger(__name__)
        if not ptk.FbxFile.is_fbx(file_path):
            return None
        try:
            search_dirs, walk_root, files = FbxUtils._dependency_inputs(
                file_path, search_dirs, walk_root, files, log
            )
            return ptk.FbxMedia.embed_dependencies(
                file_path,
                search_dirs=search_dirs,
                walk_root=walk_root,
                files=files,
                logger=log,
            )
        except Exception as error:  # noqa: BLE001 -- the deliverable already shipped
            log.warning(f"FBX dependencies: not embedded -- the pass failed: {error}")
            log.debug("Dependency pass failed.", exc_info=True)
            return None

    @staticmethod
    def finish_deliverable(
        file_path: str,
        glb: Optional[str] = None,
        search_dirs: Optional[Iterable[str]] = None,
        logger: Optional[logging.Logger] = None,
        walk_root: Optional[str] = None,
        files: Optional[Iterable[Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """The passes a written FBX deliverable takes, in place.

        What the Scene Exporter and the Unity bridge run on the FBX they ship,
        after the GLB built from it (*glb*, whose highlight copies it then
        shares; ``None`` builds them): an instance whose pivots differ from
        its siblings' gets a mesh of its own -- Unity moved the shared one by
        another instance's pivots -- StingrayPBS's environment maps leave the
        file, the reflection probe's highlight copies go in, and every file
        the records name is embedded (:meth:`embed_dependencies`'s folders
        and audio). ``ptk.MeshConvert.finish_fbx_deliverable`` is the
        sequence.

        Never raises -- a pass that fails is a warning and the next still runs.

        Parameters:
            file_path: The FBX just written from this scene.
            glb: The GLB this export built from it, if any.
            search_dirs: Folders the records' files are resolved against;
                ``None`` asks the scene.
            walk_root: Where a name they miss is searched for, recursively;
                ``None`` takes the scene's texture root, ``""`` none.
            files: Further files to embed; ``None`` takes the events' audio.
            logger: Where the outcome is said; this module's otherwise.

        Returns:
            ``ptk.MeshConvert.finish_fbx_deliverable``'s report, or ``None``
            when the file is not a binary FBX. A scene that cannot say where
            its files are is warned about, and the passes run on what was
            given: the split, the copies and the drop need none of it.
        """
        log = logger or logging.getLogger(__name__)
        if not ptk.FbxFile.is_fbx(file_path):
            return None
        try:
            search_dirs, walk_root, files = FbxUtils._dependency_inputs(
                file_path, search_dirs, walk_root, files, log
            )
        except Exception as error:  # noqa: BLE001 -- the deliverable already shipped
            log.warning(
                f"FBX deliverable: the scene's files not resolved ({error}); "
                "finishing with the folders and files given."
            )
            log.debug("Deliverable inputs failed.", exc_info=True)
            search_dirs, walk_root, files = list(search_dirs or ()), walk_root or "", files or ()
        return ptk.MeshConvert.finish_fbx_deliverable(
            file_path,
            glb,
            search_dirs=search_dirs,
            walk_root=walk_root,
            files=files,
            logger=log,
        )

    @staticmethod
    def _dependency_inputs(
        file_path: str,
        search_dirs: Optional[Iterable[str]],
        walk_root: Optional[str],
        files: Optional[Iterable[Any]],
        log: logging.Logger,
    ) -> Tuple[List[str], str, Iterable[Any]]:
        """:meth:`embed_dependencies`'s inputs, each ``None`` asked of the scene."""
        if search_dirs is None:
            dirs = FbxUtils.hook("dependency_dirs")
            search_dirs = dirs() if dirs else []
        if walk_root is None:
            root = FbxUtils.hook("dependency_root")
            walk_root = root() if root else ""  # "": no walk
        if files is None:
            files = FbxUtils._shipped_audio(file_path, log)
        return list(search_dirs), walk_root, files

    @staticmethod
    def _shipped_audio(file_path: str, log) -> List[Tuple[str, str]]:
        """``(file name, path)`` of the audio the FBX's events play.

        Read off the ``audio`` the file itself carries (each event
        names its track: ``ptk.FbxMedia.audio_events``), resolved through the
        scene's file map; a track whose file is missing is said once and left
        out.
        """
        from mayatk.audio_utils._audio_utils import AudioUtils

        names = ptk.FbxMedia.audio_events(file_path)
        found = AudioUtils.track_files(names)
        missing = sorted(set(names) - {os.path.splitext(n)[0] for n, _ in found})
        if missing:
            log.warning(
                "FBX dependencies: no audio file for event(s) "
                f"{', '.join(missing)}; the deliverable ships without them."
            )
        return found

    @classmethod
    def import_scene(
        cls,
        file_path: str,
        namespace: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        return_new_nodes: bool = True,
        *,
        stash: bool = True,
        file_options: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> List[str]:
        """Import an FBX file -- ALL of its animation -- optionally into a namespace.

        Maya's FBX translator **ignores** the ``cmds.file(namespace=...)``
        flag, but it honors the *active* namespace: setting it before the
        import cleanly isolates every imported node — transforms, shapes,
        materials and shading engines — under that namespace (verified on
        Maya 2025). This is the same native isolation ``.ma/.mb`` imports get
        for free, so no manual per-node namespace moves are needed. The active
        namespace is always restored afterward, even on failure.

        **Every take.** The importer plays the ONE take ``FBXImportSetTake``
        names and no flag merges takes, so a file whose animation is split
        across takes lost all but one: a Shots Only export imported its last
        shot, a Blender push (its scene stack beside a stack per shot) its last
        shot window, a file of per-object stacks only what the last stack
        moves. :meth:`pythontk.FbxFile.take_layout` sorts the takes and the
        import follows it:

        - ``none`` / ``single``: the file as it is.
        - ``covered``: the one take covering the others (they are its slices),
          picked by index.
        - ``merged``: the takes that agree, made ONE take
          (:meth:`pythontk.FbxMedia.merge_takes`) in a scoped copy that is
          imported and deleted. The importer extracts embedded media beside the
          file it reads and finds a texture whose recorded path is gone from
          that file's folder, so both are pointed where an import of
          *file_path* itself puts them (:meth:`_rehome_files`).
        - ``conflicted``: the takes that agree play as above, and each take
          that disagrees with them -- same channel, overlapping frames,
          different motion: a clip library's alternatives -- is parked WHOLE
          in Key Stash (:attr:`TAKE_STASH`) as a clip named after it:
          export-inert, previewable, retrievable onto the imported objects.
          Parking is not undoable, as the import itself is not.

        A file pythontk cannot read (ASCII) imports the take whose span covers
        every other take's (:meth:`_covering_take`), else its last take with a
        warning naming the rest. One INFO line says what was imported. The
        take selector is sticky, so it is left at :meth:`reset_import`'s
        per-file ``-1``.

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
            return_new_nodes: Return the new nodes; ``False`` returns ``[]``.
            stash: Park conflicting takes in Key Stash. ``False`` for an import
                that is thrown away afterwards (a sandbox compare): they are
                left out, with a warning naming them, and the scene's Key Stash
                is not written.
            file_options: ``cmds.file`` flags overriding this method's own
                (``i``, ``type``, ``ignoreVersion``, ``returnNewNodes`` ...).
            logger: Where the take report and its warnings go; defaults to
                this module's.

        Returns:
            The newly created node names (namespace-prefixed when *namespace*
            is given), or ``[]``.

        Raises:
            FileNotFoundError: If *file_path* does not exist.
            RuntimeError: On import failure.
        """
        log = logger or logging.getLogger(__name__)
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
            log.debug("FBXResetImport unavailable (OK).")
        cls._apply_import_options(
            options if options is not None else cls._DEFAULT_IMPORT_OPTIONS
        )

        flags: Dict[str, Any] = {
            "i": True,
            "type": "FBX",
            "ignoreVersion": True,
            "returnNewNodes": True,  # the take steps need them, whatever is returned
            "mergeNamespacesOnClash": False,
            "preserveReferences": False,
        }
        flags.update(file_options or {})

        def load(path: str) -> List[str]:
            nodes = cmds.file(path.replace("\\", "/"), **flags)
            # cmds.file returns the new-node list only with returnNewNodes;
            # without it the return is the filename string.
            return nodes if isinstance(nodes, list) else []

        restore_ns = None
        if namespace:
            if not cmds.namespace(exists=namespace):
                cmds.namespace(add=namespace)
            restore_ns = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
            cmds.namespace(setNamespace=namespace)
        try:
            new_nodes, report = cls._import_takes(file_path, load, stash, log)
        finally:
            if restore_ns is not None:
                cmds.namespace(setNamespace=restore_ns)

        log.info(
            f"Imported FBX: {file_path.replace(os.sep, '/')}"
            + (f" into namespace '{namespace}'" if namespace else "")
            + f" -- takes: {report}."
        )
        return new_nodes if return_new_nodes else []

    @classmethod
    def _import_takes(
        cls,
        file_path: str,
        load: Callable[[str], List[str]],
        stash: bool,
        log: logging.Logger,
    ) -> Tuple[List[str], str]:
        """Run *load* so the scene gets every take of *file_path*.

        :meth:`import_scene`'s take rules. *load* imports the path it is
        handed with the caller's flags, under the take selector this pins,
        and returns the new nodes.

        Returns:
            ``(new nodes, report)`` -- *report* the clause the INFO line ends
            with.
        """
        fbx, layout = cls._take_layout(file_path, log)
        index, path = -1, file_path
        with ptk.TempArtifacts("fbx_takes", policy="scoped") as scratch:
            if layout is None:
                index, report = cls._unread_take(file_path, log)
            else:
                report = cls._take_report(layout)
                live = layout["live"]
                if len(live) > 1:
                    # Named as the source: a clash on import prefixes the new
                    # nodes with the file's name.
                    copy = os.path.join(scratch.dir_path(), os.path.basename(file_path))
                    try:
                        ptk.FbxMedia.merge_takes(file_path, copy, takes=live)
                        path = ptk.AppLauncher.ansi_safe_path(copy)
                    except (OSError, ValueError) as error:
                        # One live take, by index: the importer's own pick is
                        # the file's LAST take, which can be one parked below.
                        first = live[0]
                        index = next(
                            t["index"] for t in layout["takes"] if t["name"] == first
                        )
                        log.warning(
                            f"FBX takes could not be merged ({error}): imported "
                            f"{first!r} only; left out: "
                            + ", ".join(repr(t) for t in live[1:])
                        )
                        report = f"merge failed, {first!r} imported"
                elif live and len(layout["takes"]) > 1:
                    index = next(
                        t["index"] for t in layout["takes"] if t["name"] == live[0]
                    )
            mel.eval(f"FBXImportSetTake -ti {index}")
            try:
                nodes = load(path)
            finally:
                # Sticky: a pinned take would shape every later raw import.
                mel.eval("FBXImportSetTake -ti -1")
            if path != file_path:
                cls._rehome_files(nodes, path, file_path, fbx, log)
        if layout and layout["stash"]:
            reasons = "; ".join(layout["conflicts"].get(t, t) for t in layout["stash"])
            names = ", ".join(repr(t) for t in layout["stash"])
            if not stash:
                log.warning(
                    f"FBX {os.path.basename(file_path)}: {names} left out of this "
                    "import, which parks nothing in Key Stash -- they disagree "
                    f"with the takes it plays: {reasons}"
                )
                report += f"; left out: {names}"
            else:
                parked = cls._park_takes(file_path, layout, nodes, log)
                if parked:
                    report += (
                        "; parked in Key Stash: "
                        + ", ".join(repr(t) for t in parked)
                        + f" ({reasons})"
                    )
                missed = [t for t in layout["stash"] if t not in parked]
                if missed:  # each already warned about
                    report += "; NOT parked: " + ", ".join(repr(t) for t in missed)
        return nodes, report

    @staticmethod
    def _take_layout(file_path: str, log: logging.Logger) -> Tuple[Any, Any]:
        """``(FbxFile, take_layout())`` of a binary FBX, its curve arrays read
        once; ``(None, None)`` for a file pythontk cannot read (ASCII, damaged).
        """
        if not ptk.FbxFile.is_fbx(file_path):
            return None, None
        try:
            fbx = ptk.FbxFile.load(
                file_path, decode_arrays=ptk.FbxFile.CURVE_ARRAYS, raw_payloads=False
            )
            return fbx, fbx.take_layout()
        except Exception as error:  # noqa: BLE001 - only refines the import
            # A file the parser trips on still imports as it did before the
            # layout was read: the importer's own take rules (_unread_take).
            log.warning(
                f"FBX takes not read ({error}); the importer picks one.",
                exc_info=not isinstance(error, ValueError),
            )
            return None, None

    @staticmethod
    def _take_report(layout: Dict[str, Any]) -> str:
        """What a :meth:`pythontk.FbxFile.take_layout` imports, in a clause."""
        live, redundant = layout["live"], layout["redundant"]
        kind = layout["kind"]
        if not live:
            return f"{kind}, nothing animated"
        if len(live) > 1:
            text = f"{len(live)} takes merged into one (" + ", ".join(live) + ")"
        else:
            text = f"{live[0]!r} imported"
        if redundant:
            text += ", covering " + ", ".join(repr(t) for t in redundant)
        return f"{kind}: {text}"

    @classmethod
    def _unread_take(cls, file_path: str, log: logging.Logger) -> Tuple[int, str]:
        """``(take index, report)`` for a file pythontk cannot read, by the
        importer's own take spans (:meth:`_covering_take`)."""
        try:
            index, names = cls._covering_take(mel, file_path)
        except Exception as error:  # noqa: BLE001 - the import itself still runs
            log.debug(f"FBX take spans unreadable ({error}).")
            return -1, "not read"
        if len(names) < 2:
            return -1, f"unread, {len(names)} take(s)"
        if index != -1:
            others = [n for i, n in enumerate(names, 1) if i != index]
            return index, f"unread: {names[index - 1]!r} imported, covering " + (
                ", ".join(repr(n) for n in others)
            )
        log.warning(
            f"FBX {os.path.basename(file_path)} cannot be read by pythontk "
            f"(ASCII?), so its {len(names)} takes cannot be merged and none "
            f"covers the rest: imported {names[-1]!r} only; left out: "
            + ", ".join(repr(n) for n in names[:-1])
        )
        return -1, f"unread: {names[-1]!r} imported, {len(names) - 1} left out"

    #: :meth:`_native_read_gap` verdicts by file identity (path, mtime, size):
    #: a large file's take layout takes seconds to read.
    _NATIVE_READ_MEMO: Dict[str, Optional[str]] = {}

    @staticmethod
    def _native_read_key(file_path: str) -> str:
        return ptk.CachedArtifact.key(files=[os.path.abspath(file_path)])

    @classmethod
    def _native_read_known(cls, file_path: str) -> bool:
        """True when :meth:`_native_read` answers *file_path* without reading it."""
        return cls._native_read_key(file_path) in cls._NATIVE_READ_MEMO

    @classmethod
    def _native_read(cls, file_path: str, log: logging.Logger) -> Optional[str]:
        """Ready Maya's own reader for *file_path*: pin the per-file take
        (:meth:`_default_import_take`) and return what that read misses
        (:meth:`_native_read_gap`), read once per version of the file."""
        key = cls._native_read_key(file_path)
        if key not in cls._NATIVE_READ_MEMO:
            cls._NATIVE_READ_MEMO[key] = cls._native_read_gap(file_path, log)
        cls._default_import_take()
        return cls._NATIVE_READ_MEMO[key]

    @classmethod
    def _native_read_gap(cls, file_path: str, log: logging.Logger) -> Optional[str]:
        """What Maya's own FBX reader misses of *file_path* that
        :meth:`import_scene` imports, as a report clause; ``None`` for nothing.

        The native reader -- a reference, which re-reads its file at every
        load, reload and scene open -- plays the ONE take the session's sticky
        selector names when it reads, never a merge, and ``-1`` (the last
        take, :meth:`_default_import_take`) is the only selector a session can
        be relied on to hold. So a file reads whole natively when nothing in it
        is keyed, or when its last take is the one the import plays and no
        take is parked; a file whose import merges takes, parks one, or plays
        a cover listed before its slices reads whole under no single take. A
        file pythontk cannot read (ASCII) is judged by the importer's own spans
        (:meth:`_covering_take`), as its import is.
        """
        _fbx, layout = cls._take_layout(file_path, log)
        if layout is not None:
            live, stash = layout["live"], layout["stash"]
            if not stash and (not live or live == [layout["takes"][-1]["name"]]):
                return None
            parks = ", ".join(repr(t) for t in stash)
            return cls._take_report(layout) + (f"; parks {parks}" if parks else "")
        try:
            cls.load_plugin()
            index, names = cls._covering_take(mel, file_path)
        except Exception as error:  # noqa: BLE001 - its import falls back to -1 too
            log.debug(f"FBX take spans unreadable ({error}).")
            return None
        if index in (-1, len(names)):
            return None
        return (
            f"unread: {names[index - 1]!r} covers the rest, but the last take is "
            f"{names[-1]!r}"
        )

    @staticmethod
    def _covering_take(mel, path):
        """``(index, names)``: the 1-based index of the take whose span covers
        every other take's span, else ``-1`` (the importer's own pick: the last
        take); *names* are the file's takes, in file order.

        The rule for a file pythontk cannot read (ASCII): the spans are the
        ones the importer itself reports (``FBXGetTakeLocalTimeSpan``), and a
        whole-timeline take beside its shot slices -- Maya's ``Take 001``, a
        Blender push's scene stack -- covers them. ``FBXRead`` parses the file
        once more to say so. The bake template
        (``blender_bridge/templates/_bake_scene.py``) carries this body
        token-for-token as ``covering_take``, for a child without mayatk; a
        test holds the two equal. blendertk's Maya-bridge templates vendor it
        too, held to this body by blendertk's ``test_maya_bridge``.
        """
        mel.eval('FBXRead -f "{}"'.format(path.replace("\\", "/")))
        try:
            count = int(mel.eval("FBXGetTakeCount") or 0)
            names = [
                mel.eval("FBXGetTakeName {}".format(i)) for i in range(1, count + 1)
            ]
            spans = [
                mel.eval("FBXGetTakeLocalTimeSpan {}".format(i))
                for i in range(1, count + 1)
            ]
        finally:
            mel.eval("FBXClose")
        for index, (start, end) in enumerate(spans, 1):
            if count > 1 and all(start <= s and e <= end for s, e in spans):
                return index, names
        return -1, names

    @staticmethod
    def _source_media_dir(source: str, project: Optional[str] = None) -> Optional[str]:
        """Where a project keeps the media of the scene file *source* that has
        no home of its own: ``<sourceimages>/<source stem>`` (the stem in
        ``[0-9A-Za-z_]``) in *project*, default the open scene's
        (``DataNodes.scene_source_images_dir``) -- or None without a project.
        The ONE folder convention for a source's media: an FBX's embedded
        media an import cannot extract beside it, and the textures an import
        or bake would read from temp (``BlenderSceneImport._media_home``)."""
        stem = re.sub(
            r"[^0-9A-Za-z_]", "_", os.path.splitext(os.path.basename(source))[0]
        )
        if project:
            return os.path.join(ptk.Workspace.load(project).source_images_dir, stem)
        from mayatk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_source_images_dir(stem) or None

    @staticmethod
    def _rehome_files(
        nodes: List[str], copy: str, source: str, fbx: Any, log: logging.Logger
    ) -> None:
        """Point what an import of the scoped *copy* placed or found relative
        to itself where an import of *source* places or finds it.

        The importer extracts embedded media to ``<file>.fbm`` beside the file
        it reads, and finds a texture whose recorded path is gone at its
        recorded RELATIVE path from that file's folder (measured, Maya 2025:
        imported from a copy elsewhere, the nodes kept the copy's ``.fbm`` --
        deleted with it -- or the stale absolute path). So the extracted files
        are copied to ``<source>.fbm`` and every filename attribute of the new
        nodes follows them, and a path that is gone is resolved as the import
        of *source* resolves it, from *fbx*'s ``RelativeFilename`` records.
        When ``<source>.fbm`` cannot be written (a read-only delivery folder)
        the media goes to ``<sourceimages>/<source stem>`` in the open
        scene's project (:meth:`_source_media_dir`) -- never temp, which the
        nodes would name in a saved scene -- with a warning; to temp only when
        no project resolves.
        """

        def key(path: str) -> str:
            return os.path.normcase(os.path.normpath(path))

        copy_fbm = os.path.splitext(copy)[0] + ".fbm"
        media = os.path.splitext(source)[0] + ".fbm"
        if os.path.isdir(copy_fbm):
            try:
                shutil.copytree(copy_fbm, media, dirs_exist_ok=True)
            except OSError as error:
                media = FbxUtils._source_media_dir(source)
                if media is not None:
                    shutil.copytree(copy_fbm, media, dirs_exist_ok=True)
                    log.warning(
                        f"Embedded media could not be extracted beside {source} "
                        f"({error}); it is in the project's {media}."
                    )
                else:
                    media = ptk.TempArtifacts("fbx_media", policy="detached").dir_path()
                    shutil.copytree(copy_fbm, media, dirs_exist_ok=True)
                    log.warning(
                        f"Embedded media could not be extracted beside {source} "
                        f"({error}) and no project resolves; it is in {media}, "
                        "which is cleaned up within days -- copy it somewhere "
                        "permanent."
                    )
        relative: Dict[str, str] = {}
        for record in fbx.iter_objects() if fbx is not None else ():
            if record["name"] not in ("Texture", "Video"):
                continue
            found = {}
            for child in record["children"]:
                if child["name"] in ("FileName", "Filename", "RelativeFilename"):
                    value = child["props"][0] if child["props"] else None
                    if isinstance(value, (bytes, bytearray)):
                        value = bytes(value).decode("utf-8", "replace")
                    found[child["name"]] = value
            absolute = found.get("FileName") or found.get("Filename")
            if absolute and found.get("RelativeFilename"):
                relative[key(absolute)] = found["RelativeFilename"]
        inside = key(copy_fbm) + os.sep
        for node in cmds.ls(nodes) or []:
            for attr in cmds.listAttr(node, usedAsFilename=True) or []:
                plug = f"{node}.{attr}"
                try:
                    value = cmds.getAttr(plug)
                except (RuntimeError, ValueError):
                    continue
                if not isinstance(value, str) or not value:
                    continue
                new = None
                if key(value).startswith(inside):
                    new = os.path.join(media, os.path.relpath(value, copy_fbm))
                elif not os.path.exists(value) and key(value) in relative:
                    found_path = os.path.normpath(
                        os.path.join(os.path.dirname(source), relative[key(value)])
                    )
                    new = found_path if os.path.exists(found_path) else None
                if new:
                    try:
                        cmds.setAttr(plug, new, type="string")
                    except RuntimeError as error:
                        log.debug(f"{plug} not repointed to {new}: {error}")

    @classmethod
    def _park_takes(
        cls, file_path: str, layout: Dict[str, Any], nodes: List[str], log
    ) -> List[str]:
        """Park each of *layout*'s ``stash`` takes WHOLE in Key Stash, as a
        clip on the just-imported *nodes*; return the takes parked.

        Each take is imported on its own into a throwaway namespace, its
        curves are paired with the imported nodes (:meth:`_take_counterparts`)
        and stand in for the live ones while :attr:`TAKE_STASH`'s ``stash``
        copies them (copy-before-cut) -- the live curves are back right after.
        Unrecorded, as the import is. A take that cannot be parked is named in
        a warning; the import never fails for it.
        """
        from mayatk.core_utils._core_utils import CoreUtils

        names = ", ".join(repr(t) for t in layout["stash"])
        active = cls.hook("take_stash")
        if active is None:
            log.warning(f"Key Stash is unavailable: {names} left out of the import.")
            return []
        store = active()
        index = {take["name"]: take["index"] for take in layout["takes"]}
        parked = []
        with CoreUtils.undo_disabled():
            for take in layout["stash"]:
                try:
                    if cls._park_take(store, file_path, take, index[take], nodes, log):
                        parked.append(take)
                except Exception as error:  # noqa: BLE001 - the import already landed
                    log.warning(
                        f"FBX take {take!r} could not be parked in Key Stash "
                        f"({error}); it is not in the scene.",
                        exc_info=True,
                    )
        return parked

    @classmethod
    def _park_take(
        cls,
        store: Any,
        file_path: str,
        take: str,
        index: int,
        nodes: List[str],
        log,
    ) -> bool:
        """:meth:`_park_takes` for one take; True when its clip was stored."""
        sandbox, n = "_fbx_take", 1
        while cmds.namespace(exists=f":{sandbox}"):
            sandbox, n = f"_fbx_take{n}", n + 1
        prior_ns = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
        prior_mode = mel.eval("FBXImportMode -q")
        cmds.namespace(add=sandbox, parent=":")
        try:
            cmds.namespace(setNamespace=f":{sandbox}")
            # "add": a merge would update the same-named imported nodes.
            mel.eval("FBXImportMode -v add")
            mel.eval(f"FBXImportSetTake -ti {index}")
            try:
                take_nodes = (
                    cmds.file(
                        file_path.replace("\\", "/"),
                        i=True,
                        type="FBX",
                        ignoreVersion=True,
                        returnNewNodes=True,
                    )
                    or []
                )
            finally:
                # The namespace first: the outer finally removes the sandbox,
                # which must not be current if a restore below raises.
                cmds.namespace(setNamespace=prior_ns)
                mel.eval("FBXImportSetTake -ti -1")
                mel.eval(f"FBXImportMode -v {prior_mode}")
            pairs = cls._take_counterparts(nodes, take_nodes)
            plan, unplaced = [], []
            for curve in cmds.ls(take_nodes, type="animCurve") or []:
                for dst in (
                    cmds.listConnections(
                        f"{curve}.output",
                        source=False,
                        destination=True,
                        plugs=True,
                        skipConversionNodes=True,
                    )
                    or []
                ):
                    node, _, attr = dst.partition(".")
                    mine = pairs.get((cmds.ls(node, long=True) or [node])[0])
                    if mine:
                        plan.append((curve, mine, attr))
                    else:
                        unplaced.append(dst.split(":", 1)[-1])
            if unplaced:
                log.warning(
                    f"FBX take {take!r}: no imported node to park "
                    + ", ".join(sorted(set(unplaced)))
                    + " on; those curves are not in the scene."
                )
            if not plan:
                return False
            return cls._stash_take_curves(store, file_path, take, plan)
        finally:
            if cmds.namespace(exists=f":{sandbox}"):
                cmds.namespace(
                    removeNamespace=f":{sandbox}", deleteNamespaceContent=True
                )

    @staticmethod
    def _stash_take_curves(
        store: Any, file_path: str, take: str, plan: List[Tuple[str, str, str]]
    ) -> bool:
        """Store a take's curves as ONE Key Stash clip on the imported nodes.

        Each ``(curve, node, attr)`` of *plan* drives ``node.attr`` for the
        stash, under the name of the live curve it stands in for -- so the
        clip's record names that curve, as a stash of the live animation
        would, and a retrieve pastes back onto it. The live wiring, names and
        static values are restored afterwards, whatever the stash did.
        """
        swaps: List[Dict[str, Any]] = []
        targets: Dict[str, Tuple[List[str], Any, Any]] = {}
        clip = None
        try:
            for curve, node, attr in plan:
                plug = f"{node}.{attr}"
                source = (
                    cmds.listConnections(
                        plug,
                        source=True,
                        destination=False,
                        plugs=True,
                        skipConversionNodes=True,
                    )
                    or [None]
                )[0]
                live = source.partition(".")[0] if source else None
                if live and not cmds.nodeType(live).startswith("animCurve"):
                    live = None
                swap: Dict[str, Any] = {
                    "plug": plug,
                    "source": source,
                    "value": None if source else cmds.getAttr(plug),
                    "live": cmds.ls(live, uuid=True)[0] if live else None,
                    "name": live,
                    "curve": None,
                }
                swaps.append(swap)
                if live:
                    cmds.rename(live, f"{live}_fbxTakeLive")
                    curve = cmds.rename(curve, live)
                else:
                    space = node.rsplit("|", 1)[-1].rpartition(":")[0]
                    curve = cmds.rename(curve, f"{space}:{curve.rpartition(':')[2]}")
                swap["curve"] = cmds.ls(curve, uuid=True)[0]
                cmds.connectAttr(f"{curve}.output", plug, force=True)
                times = cmds.keyframe(curve, query=True, timeChange=True) or []
                attrs, start, end = targets.get(node, ([], None, None))
                attrs.append(attr)
                if times:
                    start = min(times) if start is None else min(start, min(times))
                    end = max(times) if end is None else max(end, max(times))
                targets[node] = (attrs, start, end)
            clip = store.stash(
                targets=[
                    (node, attrs, start, end)
                    for node, (attrs, start, end) in targets.items()
                    if start is not None
                ],
                label=take,
                metadata={
                    "source": "fbx",
                    "path": os.path.basename(file_path),
                    "take": take,
                },
            )
        finally:
            for swap in reversed(swaps):
                left = cmds.ls(swap["curve"]) if swap["curve"] else []
                if left:  # a curve the stash did not empty
                    cmds.delete(left)
                live = cmds.ls(swap["live"]) if swap["live"] else []
                if live and live[0] != swap["name"]:
                    cmds.rename(live[0], swap["name"])
                if swap["source"]:
                    if not cmds.isConnected(swap["source"], swap["plug"]):
                        cmds.connectAttr(swap["source"], swap["plug"], force=True)
                elif swap["value"] is not None:
                    try:
                        cmds.setAttr(swap["plug"], swap["value"])
                    except RuntimeError:
                        pass
        return clip is not None

    @staticmethod
    def _take_counterparts(nodes: List[str], take_nodes: List[str]) -> Dict[str, str]:
        """``{take-import node: imported node}`` for two imports of one file.

        Paired by node type in import order -- the importer creates a file's
        nodes in one order whichever take it plays, and only the animation
        curves differ between takes -- and kept where the leaf names agree
        once namespaces, Maya's clash prefix (``<file name>_``) and trailing
        numbers are set aside: a pair that disagrees means the two orders did
        not line up, and its curves are reported rather than parked on the
        wrong node. Both sides are long names (DAG) as ``cmds.ls`` spells them.
        """

        def typed(names: List[str]) -> Dict[str, List[str]]:
            out: Dict[str, List[str]] = {}
            for name in names or []:
                if cmds.objExists(name):
                    kind = cmds.nodeType(name)
                    if not kind.startswith("animCurve"):
                        out.setdefault(kind, []).append(name)
            return out

        def leaf(name: str) -> str:
            return name.rsplit("|", 1)[-1].rpartition(":")[2].rstrip("0123456789")

        ours, theirs = typed(nodes), typed(take_nodes)
        pairs: Dict[str, str] = {}
        for kind, names in theirs.items():
            mine = ours.get(kind, [])
            if len(mine) != len(names):
                continue
            for take_node, node in zip(names, mine):
                a, b = leaf(take_node), leaf(node)
                if a == b or b.endswith("_" + a):
                    long = (cmds.ls(take_node, long=True) or [take_node])[0]
                    pairs[long] = (cmds.ls(node, long=True) or [node])[0]
        return pairs

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
        return FbxUtils.export_flag("FBXExportBakeComplexAnimation")

    @staticmethod
    def export_flag(command: str) -> bool:
        """An ``FBXExport*`` switch's current value, read the one safe way.

        The plugin answers some of these queries with the STRING
        ``"true"``/``"false"`` (see :meth:`baking_enabled`), others with an
        int, so a direct ``bool(mel.eval(...))`` reads every string answer as
        ON. Unreadable reads False.
        """
        try:
            FbxUtils.load_plugin()
            value = mel.eval(f"{command} -q")
        except Exception as e:
            logger.debug(f"Could not read the FBX flag {command}: {e}")
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

    class WritePlan(NamedTuple):
        """What ONE FBX write does with animation, decided before it.

        :meth:`FbxUtils.write_plan` builds it, and every reader of "does this
        write bake, over what, resampled?" reads it rather than the plugin's
        live flags, which the write's own configuration moves part-way through
        a run: a take split turns the bake on, and any write of the bake flag
        resets the range (:meth:`FbxUtils.apply_write_plan`).
        """

        #: Whether the write bakes complex animation
        #: (``FBXExportBakeComplexAnimation``).
        bakes: bool
        #: Why, in words a log line can carry.
        reason: str = ""
        #: The ``(start, end)`` frames it bakes over; None when it does not
        #: bake, or sets no range of its own.
        range: Optional[Tuple[float, float]] = None
        #: Whether it keys every animated curve on every frame of the range
        #: (:attr:`FbxUtils.RESAMPLE_ALL`).
        resample_all: bool = False

    @staticmethod
    def write_plan(
        bakes: Optional[bool] = None,
        reason: str = "",
        takes: Optional[Tuple[float, float]] = None,
        span: Optional[Tuple[float, float]] = None,
        resample_all: Optional[bool] = None,
    ) -> "FbxUtils.WritePlan":
        """THE plan of one FBX write: whether it bakes, why, over what, resampled.

        A take split always bakes (:meth:`apply_takes`), over at least the
        takes' union. Otherwise the writer says so (*bakes*), or, given None,
        the FBX settings as they stand do -- a loaded preset's, or a reset's
        factory OFF. The Scene Exporter builds one per run (its task manager's
        ``write_plan``) and the hand-off writers one per send; both put the
        settings in place from it and read it back, never the live flags.

        Parameters:
            bakes: Whether the writer bakes; None reads the current settings.
            reason: Why, for the log; derived when empty.
            takes: The ``(start, end)`` union a take split realizes, or None.
            span: The range the writer sets -- its Bake Range reading, a
                hand-off's scene range -- widened to cover *takes*. None keeps
                what the settings hold when they decide, and sets no range when
                *bakes* is given.
            resample_all: Resample All for the write; None takes a split's
                default (on: every take an exact slice) or the settings' value.

        Returns:
            FbxUtils.WritePlan
        """
        if takes:
            start, end = takes
            if span:
                start, end = min(start, span[0]), max(end, span[1])
            return FbxUtils.WritePlan(
                True,
                reason or "it splits the declared takes",
                (start, end),
                True if resample_all is None else bool(resample_all),
            )
        if bakes is None:
            if not FbxUtils.baking_enabled():
                return FbxUtils.WritePlan(
                    False, reason or "the FBX settings do not bake"
                )
            return FbxUtils.WritePlan(
                True,
                reason or "the FBX settings bake",
                span or FbxUtils.bake_range(),
                FbxUtils.export_flag(FbxUtils.RESAMPLE_ALL)
                if resample_all is None
                else bool(resample_all),
            )
        if not bakes:
            return FbxUtils.WritePlan(False, reason or "the writer does not bake")
        return FbxUtils.WritePlan(
            True, reason or "the writer bakes", span, bool(resample_all)
        )

    @staticmethod
    def apply_write_plan(plan: "FbxUtils.WritePlan") -> None:
        """Put *plan* into the FBX settings: the bake flag FIRST, then its range.

        The order is the point. Any write of ``FBXExportBakeComplexAnimation``
        -- on, off, or the value it already holds -- resets the bake range to
        the scene's animation range (measured on Maya 2025 / FBX 2020.3.6: a
        7-9 range read 2-310 after it), so a range set before the flag is lost.
        The hand-off pinned the flag after its take split had set the shots'
        union, and every split hand-off baked its whole animation range.
        """
        FbxUtils.load_plugin()
        mel.eval(
            f"FBXExportBakeComplexAnimation -v {'true' if plan.bakes else 'false'}"
        )
        if not plan.bakes:
            return
        if plan.range:
            mel.eval(f"FBXExportBakeComplexStart -v {plan.range[0]}")
            mel.eval(f"FBXExportBakeComplexEnd -v {plan.range[1]}")
        resample = "true" if plan.resample_all else "false"
        mel.eval(f"{FbxUtils.RESAMPLE_ALL} -v {resample}")

    @staticmethod
    def planned_bake_range() -> Optional[Tuple[float, float]]:
        """The range the write in progress bakes over: the open bracket's plan's
        (:meth:`begin_export` *plan*), else the settings' (:meth:`bake_range`).

        What a producer publishing inside the bracket reads: a hand-off's
        bracket publishes before the write is configured, while the settings
        still hold whatever ran last.
        """
        plan = FbxUtils._bracket_state().get("write_plan")
        if plan is None:
            return FbxUtils.bake_range()
        return tuple(plan.range) if plan.bakes and plan.range else None

    @classmethod
    @contextlib.contextmanager
    def offsets_folded(
        cls,
        objects: Optional[Iterable[str]] = None,
        plan: Optional["FbxUtils.WritePlan"] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """Around ONE FBX write: ship every node where Maya draws it.

        The plug-in writes a node's TRS and never its offset. A write that
        bakes folds the offset into the baked channels; one that does not
        drops it, and the node ships displaced by it -- measured on Maya 2025 /
        FBX 2020.3.6, a static translate-3 offset at world 0 and a keyed prop
        under a driven-locator rig 3.3 units off. And FBX has no "do not
        inherit": a node with ``inheritsTransform`` off ships as though it
        inherited -- its world keyed as its local in a baking write, its TRS
        alone in a plain one -- and lands its parent's world away (measured
        2026-10-17 through the Unity, Blender and WebXR hand-offs: a pinned
        rig mesh 98-161 cm off).

        So for the write, and put back when the block exits (on an exception
        too), :meth:`SmartBake.fold_offsets` re-anchors each node that ignores
        a parent off the origin under it, inheriting, whatever *plan* (read
        from the current settings when None) says; and when the write does not
        bake it folds every other non-identity offset into its node's own
        channels. A static node takes plain values, no key; a moving one is
        keyed per frame. The bake flag and a loaded preset are never touched:
        turning the bake on instead keys every static offset node on every
        frame, and overrules a preset that chose not to bake. The scope is
        what the plug-in writes: the export set's subtrees and the ancestors
        it carries along. What cannot fold exactly is warned about by name: it
        ships displaced. The Scene Exporter's Flatten Sheared Chains
        re-anchors before its write, which leaves this nothing to do.

        Undo is not recorded, and the selection (the export set), the current
        frame and a held viewport are kept.

        Parameters:
            objects: The export set's roots; None reads the selection.
            plan: This write's :meth:`write_plan`.
            logger: Where the outcome is said; this module's otherwise.

        Yields:
            The nodes folded for the write.
        """
        from mayatk.core_utils._core_utils import CoreUtils

        log = logger or logging.getLogger(__name__)
        plan = plan if plan is not None else cls.write_plan()
        roots = cmds.ls(selection=True, long=True) if objects is None else objects
        shipped = cls._shipped_transforms(roots) if roots else []
        bake_range = None
        if plan.bakes and shipped:
            # The plug-in folds every offset itself: a node that ignores its
            # parent is all a baking write can still ship displaced.
            shipped = [
                node
                for node in shipped
                if not cmds.getAttr(f"{node}.inheritsTransform")
            ]
            if shipped:
                from mayatk.anim_utils._anim_utils import AnimUtils

                bake_range = (
                    plan.range or cls.bake_range() or AnimUtils.scene_animation_range()
                )
        restore = None
        folded: List[str] = []
        fold = cls.hook("offset_fold") if shipped else None
        if shipped and fold is None:
            log.warning(
                "offsetParentMatrix: no offset fold is installed, so this write "
                "ships every offset and every node that ignores its parent as the "
                "plug-in writes them."
            )
        if fold is not None:
            try:
                with contextlib.ExitStack() as held:
                    held.enter_context(CoreUtils.undo_disabled())
                    held.enter_context(CoreUtils.preserved_selection())
                    held.enter_context(CoreUtils.suspended_refresh())
                    result, restore = fold(shipped, bake_range=bake_range)
            except Exception as error:  # noqa: BLE001 -- the write still ships
                log.warning(
                    "offsetParentMatrix: the fold failed and was undone, so this "
                    "write ships every offset and every node that ignores its "
                    f"parent as the plug-in writes them ({error})."
                )
            else:
                declined = result.declined
                folded = [node for node in result.baked if node not in declined]
                anchored = [node for node in folded if node in result.flattened]
                if anchored:
                    log.info(
                        f"Re-anchored {len(anchored)} node(s) that ignore their "
                        "parent (inheritsTransform off) under it for this write "
                        "-- FBX has no 'do not inherit'; put back after it."
                    )
                if len(anchored) < len(folded):
                    log.info(
                        "Folded the offsetParentMatrix of "
                        f"{len(folded) - len(anchored)} node(s) into their own "
                        f"channels for this write -- it does not bake "
                        f"({plan.reason}); put back after it."
                    )
                if declined:
                    cls._warn_offsets_dropped(declined, plan, log)
        try:
            yield folded
        finally:
            if restore is not None:
                try:
                    with CoreUtils.undo_disabled(), CoreUtils.preserved_selection():
                        for warning in restore():
                            log.warning(f"offsetParentMatrix restore: {warning}")
                except Exception as error:  # noqa: BLE001 -- never masks the write's
                    log.error(f"offsetParentMatrix: the fold was not undone: {error}")

    @staticmethod
    def _shipped_transforms(roots: Iterable[str]) -> List[str]:
        """Every transform a write of *roots* ships: their subtrees, and the
        ancestors the plug-in carries along (measured on Maya 2025 / FBX
        2020.3.6: a selected child shipped with its parent)."""
        roots = cmds.ls(list(roots), long=True) or []
        if not roots:
            return []
        above = {
            "|".join(parts[:depth])
            for parts in (path.split("|") for path in roots)
            for depth in range(2, len(parts))
        }
        nodes = []
        if above:  # handed nothing, ``ls`` reads the whole scene
            nodes += cmds.ls(sorted(above), type="transform", long=True) or []
        nodes += cmds.ls(roots, dag=True, type="transform", long=True) or []
        return list(dict.fromkeys(nodes))

    @staticmethod
    def _warn_offsets_dropped(
        declined: Dict[str, str], plan: "FbxUtils.WritePlan", log
    ) -> None:
        """Name each node the write ships displaced, and why: one that ignores
        its parent, or one whose offset an unbaked write drops."""
        link = getattr(log, "log_link", None)

        def named(items: List[Tuple[str, str]]) -> str:
            entries = []
            for node, why in items[:10]:
                leaf = node.rsplit("|", 1)[-1]
                name = link(leaf, "select", node=node) if link else leaf
                why = why.replace("matrix: ", "").replace("inheritance: ", "")
                entries.append(f"{name} ({why})")
            more = f" … and {len(items) - 10} more" if len(items) > 10 else ""
            return f"{'; '.join(entries)}{more}"

        loose = [(n, w) for n, w in declined.items() if w.startswith("inheritance:")]
        offsets = [(n, w) for n, w in declined.items() if (n, w) not in loose]
        if loose:
            log.warning(
                f"This export ships {len(loose)} node(s) that ignore their parent "
                f"(inheritsTransform off) displaced -- {named(loose)}: FBX has no "
                "'do not inherit', so each lands its parent's world away. Parent "
                "it where it should ride, or export through the Scene Exporter "
                "with Flatten Sheared Chains, which re-targets it."
            )
        if offsets:
            log.warning(
                f"This export drops the offsetParentMatrix of {len(offsets)} "
                f"node(s) it could not fold exactly -- {named(offsets)}: the FBX "
                f"write does not bake ({plan.reason}), so each ships displaced by "
                "its offset. A preset with Bake Animation on folds it; a USD "
                "export keeps it."
            )

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
        Animation include group :meth:`apply_takes` turns on, and the Resample
        All it sets for the write, are restored with them, for the same reason
        and from the same capture -- but only when that call actually CHANGED
        them, so a property this build could not read is never written back on
        a guess.
        """
        FbxUtils.load_plugin()
        mel.eval("FBXExportSplitAnimationIntoTakes -c")
        saved = FbxUtils._saved_bake_state
        if saved is not None:
            FbxUtils._saved_bake_state = None
            enabled, start, end, animation_flipped, resample_prior = saved
            mel.eval(
                f"FBXExportBakeComplexAnimation -v {'true' if enabled else 'false'}"
            )
            mel.eval(f"FBXExportBakeComplexStart -v {start}")
            mel.eval(f"FBXExportBakeComplexEnd -v {end}")
            if animation_flipped:
                FbxUtils.set_animation_export(False)
            if resample_prior is not None:
                prior = "true" if resample_prior else "false"
                mel.eval(f"{FbxUtils.RESAMPLE_ALL} -v {prior}")

    #: The plugin's "Resample All": bake EVERY animated curve on every frame
    #: of the bake range, not only the complex ones. What makes a take split
    #: exact (:meth:`apply_takes`).
    RESAMPLE_ALL = "FBXExportBakeResampleAnimation"

    @staticmethod
    def apply_takes(takes: Iterable[Any], resample: bool = True) -> int:
        """Configure FBX export to emit one AnimStack (Unity clip) per take.

        Enables bake-complex, sets the **union** bake range over all takes (safe
        regardless of whether Maya bakes per-take or clips from the global
        range), clears prior take state, then declares each take.

        With *resample* (the default) every take is an exact slice of the
        animation. ``FBXExportSplitAnimationIntoTakes`` restricts each curve to
        a take's window before writing it and bake-complex bakes only complex
        animation, so a plainly keyed curve with no key inside a take
        contributes NO channel to it -- the node plays its rest pose for that
        whole shot -- and a take's keys stop at its last in-window key. Unity
        plays those takes as the shot clips: measured in Unity 6000.3, a
        50-frame shot imported 40 frames long with two of its three nodes
        frozen and the shot after it as a clip with no curves at all, while
        the whole-timeline take was right. Resample All
        (:attr:`RESAMPLE_ALL`) keys every animated curve on every frame of the
        bake range first, and the split then carries every channel across
        every take's window (measured: 0 channels missing, every take keyed on
        each of its frames). The whole-timeline take then starts on the bake
        range's first frame even where the scene's first key comes later --
        what an export's clip origin has to say. Off only for a write whose
        takes nothing reads (a GLB's own intermediate, whose clips are cut
        from the whole-timeline take) -- and then OFF, not merely left alone:
        a loaded preset's Resample All would still resample that write,
        opening its whole-timeline take on the bake range while the published
        clip origin says the first key.

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
            resample: Resample All for the write, whatever a preset left:
                on, so every take carries every channel; off, so the curves
                ship as keyed. Restored by :meth:`reset_takes`.

        Returns:
            int: Number of takes defined.  Empty input only clears state.
        """
        FbxUtils.reset_takes()  # also ensures the fbxmaya plugin is loaded

        norm = FbxUtils._normalized_takes(takes)
        if not norm:
            return 0

        union_start, union_end = FbxUtils.takes_span(norm)
        # Capture the user's bake-complex settings once (reset_takes restores
        # them); reset_takes above already consumed any prior capture, so a
        # repeated apply never overwrites the true pre-takes state.
        # The include group is recorded as "did THIS call turn it on", not as
        # its prior value: reset_takes then writes only what was actually
        # changed, so a property this build could not read is never written
        # back on a guess (`animation_export_enabled` answers True when it
        # cannot read it). Resample All likewise: its prior value, kept only
        # when this call changes it.
        flipping = not FbxUtils.animation_export_enabled()
        resample = bool(resample)
        resample_prior = (
            not resample
            if FbxUtils.export_flag(FbxUtils.RESAMPLE_ALL) != resample
            else None
        )
        if FbxUtils._saved_bake_state is None:
            FbxUtils._saved_bake_state = (
                FbxUtils.baking_enabled(),
                mel.eval("FBXExportBakeComplexStart -q"),
                mel.eval("FBXExportBakeComplexEnd -q"),
                flipping,
                resample_prior,
            )
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval(f"FBXExportBakeComplexStart -v {union_start}")
        mel.eval(f"FBXExportBakeComplexEnd -v {union_end}")
        if resample_prior is not None:
            value = "true" if resample else "false"
            mel.eval(f"{FbxUtils.RESAMPLE_ALL} -v {value}")
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
    def _normalized_takes(takes: Iterable[Any]) -> List[Tuple[str, int, int]]:
        """*takes* (``{"name","start","end"}`` mappings or ``(name, start,
        end)`` tuples) as ``(name, start, end)`` on whole frames -- the form
        :meth:`apply_takes` declares them in."""
        norm = []
        for take in takes or []:
            if isinstance(take, dict):
                name, start, end = take["name"], take["start"], take["end"]
            else:
                name, start, end = take
            norm.append((str(name), int(round(start)), int(round(end))))
        return norm

    @staticmethod
    def takes_span(takes: Iterable[Any]) -> Optional[Tuple[int, int]]:
        """The ``(start, end)`` union of *takes* -- the bake range
        :meth:`apply_takes` sets for them -- or None when there are none.

        Parameters:
            takes: What :meth:`apply_takes` takes (or its normalized triples).
        """
        norm = FbxUtils._normalized_takes(takes)
        if not norm:
            return None
        return min(s for _, s, _ in norm), max(e for _, _, e in norm)

    @staticmethod
    def declared_takes() -> list:
        """The takes the scene's records declare -- each clip's range in
        the shot record (``ptk.SceneRecords.declared_takes`` over the
        carrier): what
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
        node: Optional[str] = None, attr: Optional[str] = None, resample: bool = True
    ) -> int:
        """Realize the takes the scene declares into FBX export state.

        Defaults to the shot record on the shared carrier -- each clip's
        range (``ptk.SceneRecords.declared_takes``) -- so this is
        shot-agnostic: it realizes whatever takes the scene declares.  An
        explicit *attr* reads a JSON take list off *node* (the carrier when
        ``None``) instead; a *node* alone names no list: it arms nothing, and
        warns why.

        Parameters:
            node: Node to read a JSON take list from (with *attr*).
            attr: Attribute holding that list.
            resample: Forwarded to :meth:`apply_takes` -- whether every take
                is an exact slice (on unless nothing reads the takes).

        Returns:
            int: Number of takes defined (0 if nothing is declared).
        """
        import json
        from mayatk.node_utils.data_nodes import DataNodes

        if node is None and attr is None:
            defs = FbxUtils.declared_takes()
        else:
            if not attr:
                # Until 0.28.0 a node alone read the retired ``fbx_takes``
                # channel; say why nothing is armed rather than return 0.
                logger.warning(
                    f"apply_takes_from_node(node={node!r}) armed no takes: a take "
                    "list is read only from an explicit attr=, and the retired "
                    "'fbx_takes' channel is no default."
                )
                return 0
            node = node or DataNodes.get_export_node(create=False)
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
        return FbxUtils.apply_takes(defs, resample=resample)

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
    #: The host a tool outside this library registers its producers, stagers
    #: and record owners under (``ptk.SceneRecords.registered_hooks``): an
    #: installed one joins :attr:`PRODUCERS` and :attr:`STAGERS` unimported.
    HOST = "maya"

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
            "mayatk.rig_utils.shadow_rig._shadow_rig",
            "ShadowRig",
            "export_record",
        ),
        ptk.SceneRecords.ARTICULATION: (
            "mayatk.rig_utils.articulated_rig._articulated_rig",
            "ArticulatedRig",
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
        ptk.SceneRecords.MATERIALS: (
            "mayatk.env_utils.material_records",
            "MaterialRecords",
            "export_record",
        ),
    }

    #: Export stagers: name -> (module, class, prepare, finish).  ``prepare``
    #: runs when a bracket opens (after which the producers see the staged
    #: scene), ``finish`` when it closes -- AFTER the FBX write and the GLB
    #: conversion that follows it, in reverse order, so the artist gets the
    #: viewport back as it was.  A ``None`` finish is a one-way stage.
    STAGERS: Dict[str, Tuple[str, str, str, Optional[str]]] = {
        "render_effects": (
            "mayatk.mat_utils.render_opacity.render_effects",
            "RenderEffects",
            "prepare_for_export",
            "finish_export",
        ),
        # One-way: a marker baked before 2026-09-23 still carries its map's
        # folder, and markers ride every FBX -- lifted into the private record
        # before the write, so an old scene ships clean without a re-bake.
        "lightmap_folder_hints": (
            "mayatk.light_utils.lightmap_baker.lightmap_records",
            "LightmapRecords",
            "migrate_folder_hints",
            None,
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
        label: str, module_path: str, cls_name: str, *methods: Optional[str]
    ) -> Optional[Tuple[Optional[Callable], ...]]:
        """The callables a :attr:`PRODUCERS` / :attr:`STAGERS` row names.

        ``None`` when the row's module or class cannot be imported -- an
        uninstalled subsystem is fine, and only debug-logged.  A method the
        class does not have resolves to ``None`` with a WARNING: that is a
        misspelt row, and skipping it quietly would stop a record shipping (or
        a stager finishing) with no sign of why.  A column the row leaves
        ``None`` resolves to ``None`` quietly: a declared one-way stage.
        """
        import importlib

        try:
            owner = getattr(importlib.import_module(module_path), cls_name)
        except Exception:  # an uninstalled subsystem is fine
            logger.debug("%s unavailable; skipped.", label, exc_info=True)
            return None
        resolved = []
        for method in methods:
            if not method:  # a column the row leaves empty: a one-way stage
                resolved.append(None)
                continue
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
        for spec, row in ptk.SceneRecords.producer_rows(cls.PRODUCERS, cls.HOST).items():
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
        (``clip_mode``, ``clip_span``, ``rendering``, ``scope``) as given."""
        from mayatk.env_utils._env_utils import EnvUtils

        source = {"application": "maya"}
        try:  # ``cmds`` is None outside Maya (the module's import guard)
            source["version"] = cmds.about(version=True)
            # Provenance, not identity -- see ``HandoffSource`` (ptk.HandoffRecord).
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

        Outside the undo queue unless *ctx* is AUTHORING (a tool's own
        record, which undoes with the tool's edit): an export's records are
        bookkeeping, and the restore after the write may delete the carrier
        outside the queue (``DataNodes.discard_if_empty``) -- an undo of a
        recorded commit then replayed onto the dead node and crashed Maya
        (measured 2026-10-24). The Scene Exporter's run is undo-disabled
        already; a hand-off's bracket is not.

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
            with (
                contextlib.nullcontext()
                if ctx.mode == ptk.ExportContext.AUTHORING
                else CoreUtils.undo_disabled()
            ):
                snapshot = ptk.ExportSnapshot.assemble(cls.producers(only), ctx)
                snapshot.commit(DataNodes)
        if cls._export_depth and snapshot.scene_wide:
            # Written for the bracket's write alone (a transient record, a
            # narrowed one): the bracket puts the scene's own copy back.
            cls._bracket_state().setdefault("snapshots", []).append(snapshot)
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
        for name, row in ptk.SceneRecords.stager_rows(cls.STAGERS, cls.HOST).items():
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
        plan: Optional["FbxUtils.WritePlan"] = None,
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

        *plan* is the bracketed write's :meth:`write_plan`, set before the
        publish so a producer reads the range the write will bake
        (:meth:`planned_bake_range`) rather than what the settings held when
        the bracket opened.

        Returns:
            The snapshot published here, or ``None``.
        """
        if cls._bracket_depth_add(1) != 1:
            return None
        cls._bracket_state()["write_plan"] = plan
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
            cls._bracket_state().pop("write_plan", None)
            table = cls._bracket_state().pop("stager_table", None)
            if table is not None:
                cls._run_stagers("finish", table)
            raise

    @classmethod
    def end_export(cls) -> None:
        """Close an export bracket (outermost only): put back the scene's own
        copy of what every publish inside it wrote for the write alone -- a
        transient record cleared, a narrowed one whole
        (``ptk.ExportSnapshot.restore_scene_wide``), newest first, outside the
        undo queue as the publish was (:meth:`publish`) -- then run the
        stagers' finish."""
        if cls._export_depth <= 0:
            return
        if cls._bracket_depth_add(-1) == 0:
            cls._bracket_state().pop("write_plan", None)
            snapshots = cls._bracket_state().pop("snapshots", None) or []
            if snapshots:
                from mayatk.core_utils._core_utils import CoreUtils
                from mayatk.node_utils.data_nodes import DataNodes

                for snapshot in reversed(snapshots):
                    try:
                        with CoreUtils.undo_disabled():
                            snapshot.restore_scene_wide(DataNodes)
                    except Exception:  # noqa: BLE001 - a restore never costs the finish
                        logger.warning(
                            "Scene records not restored after the write.",
                            exc_info=True,
                        )
            table = cls._bracket_state().pop("stager_table", None)
            cls._run_stagers("finish", table if table is not None else cls.stagers())

    @classmethod
    @contextlib.contextmanager
    def export_prepared(
        cls,
        ctx: Optional[ptk.ExportContext] = None,
        only: Optional[Iterable[Any]] = None,
        stagers: Optional[Iterable[str]] = None,
        plan: Optional["FbxUtils.WritePlan"] = None,
    ):
        """Stage the scene (and publish, given a *ctx* or *only* -- see
        :meth:`begin_export`, which takes *plan* too) for an export; finish on
        exit -- AFTER everything inside the block, so an FBX write followed by
        a GLB conversion both see the staged scene.  Nested use is fine; the
        outermost bracket owns the lifecycle.  Yields the snapshot
        :meth:`begin_export` published, or ``None``."""
        snapshot = cls.begin_export(ctx, only, stagers, plan=plan)
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
        bracket is open -- the bracket already did all of it.

        A write that splits takes also stages the curve-proxy transport, as
        the bracketed writers do: the split resamples every curve
        (:meth:`apply_takes`), which leaves the fades Unity rebuilt from
        sparse visibility pairs one frame long, so they ride the proxies
        instead.  :meth:`_on_after_export` finishes that table.  A write in
        ASCII is warned about (:meth:`_warn_ascii_carrier`)."""
        if FbxUtils._export_depth:
            return
        FbxUtils._bracket_state()["hook_snapshot"] = FbxUtils.publish(
            only=sorted(FbxUtils._session_producers)
        )
        FbxUtils._warn_ascii_carrier()
        if FbxUtils.apply_takes_from_node():
            FbxUtils._bracket_state()["hook_stagers"] = FbxUtils.stage(
                ("render_effects",)
            )

    @staticmethod
    def _warn_ascii_carrier() -> None:
        """Warn when a write is about to go out in ASCII from a scene holding
        the ``data_export`` carrier.

        Unity's ASCII FBX reader drops the scene, or truncates the string,
        once a carrier channel passes ~6 KB (measured in batch Unity 6000.3),
        and the handoff record alone is past that. The Scene Exporter pins
        binary over a preset (``SceneExporter._pin_carrier_requirements``); a
        File > Export writes the format its dialog chose, and this hook cannot
        tell whether the write ships the carrier at all (an Export Selection
        without it does not), so it says so rather than overruling the
        dialog.
        """
        if not FbxUtils.export_flag("FBXExportInAscii"):
            return
        from mayatk.node_utils.data_nodes import DataNodes

        if DataNodes.get_export_node(create=False) is None:
            return
        logger.warning(
            f"FBX export is ASCII and the scene holds '{DataNodes.EXPORT}': if "
            "this file ships it, Unity's ASCII FBX reader loses its metadata "
            "(the shots, the handoff) past ~6 KB. Export binary for Unity."
        )

    @staticmethod
    def _on_after_export(*_):
        """Clear take state and undo the hook's staging: the records its
        publish wrote for the write alone (a transient one cleared; outside
        the undo queue, as :meth:`publish` wrote them), then the table a split
        staged (:meth:`_on_before_export`), else the session stagers'."""
        FbxUtils.reset_takes()
        if FbxUtils._export_depth:
            return
        snapshot = FbxUtils._bracket_state().pop("hook_snapshot", None)
        if snapshot is not None:
            from mayatk.core_utils._core_utils import CoreUtils
            from mayatk.node_utils.data_nodes import DataNodes

            try:
                with CoreUtils.undo_disabled():
                    snapshot.restore_scene_wide(DataNodes)
            except Exception:  # noqa: BLE001 - never costs the export
                logger.warning("Scene records not restored after the write.", exc_info=True)
        table = FbxUtils._bracket_state().pop("hook_stagers", None)
        FbxUtils._run_stagers(
            "finish", table if table is not None else dict(FbxUtils._session_stagers)
        )

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
