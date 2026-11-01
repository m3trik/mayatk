# !/usr/bin/python
# coding=utf-8
"""Texture Path Editor engine: the scene's texture paths and the commands that fix them.

:class:`TexturePathEditor` lists the scene's file nodes with their stored paths
(:meth:`~TexturePathEditor.rows`) and the baked lightmaps the bake markers name
(:meth:`~TexturePathEditor.lightmap_dependencies`), and runs every command the
editor offers over them: Set Directory, Find & Copy, Normalize Paths, Make Paths
Absolute, Resolve Missing Textures, the renames (Keep Names In Sync included),
selection and deletion. Its panel is tentacle's shared Texture Path Editor
(``slots/tools/texture_path_editor.py``), which drives blendertk's twin over the
same names -- mayatk's are the reference.

Reporting goes through one seam, ``_log``: into :attr:`TexturePathEditor.active_logger`
when a caller lends one (the Find & Copy panel's pane), else Maya's own channels.
"""

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    cmds = None

import os

from pythontk import (
    FileCollect,
    FileDependencies,
    FileUtils,
    FuzzyMatcher,
    ImgUtils,
    MapFactory,
    StrUtils,
)

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils.attributes._attributes import Attributes


class TexturePathEditor:
    """The Texture Path Editor's scene half: file nodes, their paths, and the
    commands that relocate, respell, find and rename them.

    One instance per editor: it keeps :attr:`previous_paths` (the path each node
    had before this session's last repath -- the table tooltip's "Previous:" line)
    and :attr:`active_logger` (where a command reports while a panel lends one).
    """

    #: The host's words for the table's two name columns and the row actions
    #: that name a node (tentacle's panel reads them; blendertk has its own).
    LABELS = {
        "shader": "Shader",
        "file_node": "File Node",
        "texture_dir": "Source Images",
        "select_file_node": "Select File Node",
        "graph": "Show in Hypershade",
        "delete": "Delete File Node",
    }

    #: Whether the scene can hold Arnold preview shaders, whose dedicated file
    #: nodes the panel's "Exclude Arnold Nodes" toggle hides.
    EXCLUDE_ARNOLD = True
    _ARNOLD_CLASSIFICATION = "rendernode/arnold*"

    #: Scene events the table refreshes on, each with whether only a SHOWN
    #: table follows it (every undo in the scene fires Undo / Redo).
    SCENE_EVENTS = (
        ("SceneOpened", False),
        ("NewSceneOpened", False),
        ("SceneImported", False),
        ("workspaceChanged", False),
        ("Undo", True),
        ("Redo", True),
    )

    #: Browse for File's filter: what a file node reads.
    FILE_TYPES = (
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.tga",
        "*.tif",
        "*.tiff",
        "*.exr",
        "*.hdr",
        "*.bmp",
        "*.psd",
        "*.iff",
        "*.tx",
        "*.*",
    )

    #: Set Directory's file handling, Normalize Paths' external-texture
    #: handling and Resolve Missing's strategies (cascade order: safest first).
    RELOCATE_MODES = ("rewrite", "copy", "move")
    NORMALIZE_MODES = ("rewrite", "copy", "move")
    RESOLVE_STRATEGY_ORDER = ("stem", "texture", "fuzzy")

    #: Said when Keep Names In Sync leaves a rename to the file or node edited
    #: (:meth:`_sync_carries`).
    _SYNC_ALONE = (
        "Keep Names In Sync: {name} was renamed alone -- {material} follows "
        "only a new base name for its own texture set."
    )

    def __init__(self):
        #: node -> its path before this session's last repath (the tooltip's
        #: "Previous:" line); the panel clears it when the scene changes.
        self.previous_paths = {}
        #: Where ``_log`` reports while a caller lends a logger (a panel's
        #: pane); ``None``: Maya's own channels.
        self.active_logger = None

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------

    def texture_dir(self) -> str:
        """The project's texture folder (the ``sourceImages`` rule), ``""`` if none."""
        return EnvUtils.get_env_info("sourceimages") or ""

    def _project_roots(self):
        """``(workspace, sourceimages)`` -- both roots the path converters take.

        ``MatUtils.to_absolute`` / ``to_project_relative`` look either one up
        themselves when it is not handed over, and that lookup goes through
        ``cmds.workspace``. Resolving the pair ONCE, here, is what keeps a
        per-row Maya call out of a table refresh and keeps ``cmds`` off the
        thread pool in :meth:`rows` -- so callers that loop pass both down
        rather than letting the converters ask per path.
        """
        return (EnvUtils.get_env_info("workspace") or "", self.texture_dir())

    # ------------------------------------------------------------------
    # Path validity -- one token-aware rule
    # ------------------------------------------------------------------

    @staticmethod
    def _texture_on_disk(path: str) -> bool:
        """Is there a file behind *path*? Tile/frame tokens resolved first.

        Every "does this texture exist?" here goes through this. ``os.path.exists``
        is the wrong question for a stored ``.ftn`` value: a ``rock.<UDIM>.png``
        names no literal file, so a good tile set read as missing, was swept up
        by Select Broken Paths and fed to Resolve Missing Textures -- which
        repaths, so a healthy set could be rebound to whichever single file
        matched its stem.

        It asks ``MatUtils.texture_tiles`` and not the ``1001``-probing
        ``_texture_exists``: the probe is pinned to the exporter's representative
        tile, so a set running 1002-1005 reads as missing through it -- and this
        same verdict gates Set Directory's repath.

        Parameters:
            path: An ABSOLUTE path -- resolve a stored value through
                ``MatUtils.to_absolute`` first.
        """
        return bool(MatUtils.texture_tiles(path))

    def resolve_path(self, path: str, file_node=None):
        """``(exists, absolute path)`` for a stored or typed *path*.

        *file_node* is accepted for the hosts' one signature (blendertk resolves
        a linked image's ``//`` path against its library); Maya resolves every
        path against the project.
        """
        abs_path = MatUtils.to_absolute(path, *self._project_roots()) if path else ""
        return bool(abs_path) and self._texture_on_disk(abs_path), abs_path

    @staticmethod
    def is_absolute_path(path: str) -> bool:
        """Whether *path* is stored absolute -- a Normalize Paths candidate."""
        return bool(path) and os.path.isabs(path)

    # ------------------------------------------------------------------
    # The table's rows
    # ------------------------------------------------------------------

    def rows(self, exclude_arnold: bool = False):
        """One row per (shader, file node): what the editor's table lists.

        The stored ``.ftn`` verbatim (no relativized display): Make Paths
        Absolute, Select Absolute Paths and a typed path all depend on the cell
        showing the path the node actually holds.

        Parameters:
            exclude_arnold: Leave out file nodes only an Arnold shader uses (a
                preview shader owns a duplicate file node per texture).

        Returns:
            list[dict]: ``shader`` (the label), ``shader_node`` (the shader a
            rename of that cell renames; Keep Names In Sync's material),
            ``materials`` (every shader reading the texture), ``shader_editable``,
            ``path`` (stored), ``file_node``, ``exists`` (tiles resolved) and
            ``abspath``.
        """
        found = MatUtils.get_file_nodes(
            return_type="shaderName|path|fileNodeName",
            exc_classification=(
                self._ARNOLD_CLASSIFICATION if exclude_arnold else None
            ),
        )
        workspace, sourceimages = self._project_roots()
        verdicts = self._resolve_paths(
            {str(path).strip() for _s, path, _n in found if path},
            workspace,
            sourceimages,
        )
        rows = []
        for shader, path, file_node in found:
            path = str(path or "").strip()
            exists, abs_path = verdicts.get(path, (False, ""))
            rows.append(
                {
                    "shader": shader or "",
                    "shader_node": shader or "",
                    "materials": [shader] if shader else [],
                    "shader_editable": True,
                    "path": path,
                    "file_node": file_node or "",
                    "exists": exists,
                    "abspath": abs_path,
                }
            )
        return rows

    def _resolve_paths(self, paths, workspace, sourceimages):
        """``{path: (exists, absolute path)}``; a thread pool past 50 paths.

        The roots are resolved by the caller, on the main thread: the pool
        below must not touch ``cmds``.
        """

        def resolve_and_check(path):
            abs_path = MatUtils.to_absolute(path, workspace, sourceimages)
            return path, self._texture_on_disk(abs_path), abs_path

        cache = {}
        if len(paths) > 50:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(resolve_and_check, p): p for p in paths}
                for future in as_completed(futures):
                    try:
                        path, exists, abs_path = future.result(timeout=5)
                        cache[path] = (exists, abs_path)
                    except Exception:
                        path = futures[future]
                        cache[path] = (
                            False,
                            MatUtils.to_absolute(path, workspace, sourceimages),
                        )
        else:
            for path in paths:
                _, exists, abs_path = resolve_and_check(path)
                cache[path] = (exists, abs_path)
        return cache

    def file_nodes(self, exclude_arnold: bool = False):
        """Every file node in the scene -- the "all" scope of a path command.

        Parameters:
            exclude_arnold: Leave out the ones only an Arnold shader uses, so a
                command never touches rows the table is hiding.
        """
        if exclude_arnold:
            return MatUtils.get_file_nodes(
                return_type="fileNode", exc_classification=self._ARNOLD_CLASSIFICATION
            )
        return cmds.ls(type="file") or []

    @staticmethod
    def node_exists(node) -> bool:
        """Whether *node* (a file node or shader name) is still in the scene."""
        return bool(node) and bool(cmds.objExists(str(node)))

    @staticmethod
    def stored_path(node) -> str:
        """The node's stored ``.ftn``, or "" when the plug cannot be read.

        Shared by every reader so none can disagree about a node whose plug has
        gone away mid-command (a referenced file node unloaded under it).
        """
        try:
            return cmds.getAttr(f"{node}.fileTextureName") or ""
        except Exception:
            return ""

    def selected_file_nodes(self):
        """The file nodes the scene selection's materials read.

        Returns:
            ``None`` when nothing is selected, else the file nodes (``[]``: the
            selection carries no material, or its materials no file node).
        """
        selection = cmds.ls(sl=True, flatten=True)
        if not selection:
            return None
        nodes = []
        for mat in MatUtils.get_mats(selection) or []:
            try:
                nodes.extend(cmds.ls(cmds.listHistory(mat) or [], type="file") or [])
            except Exception:
                pass
        return list(dict.fromkeys(nodes))

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------

    @staticmethod
    def reload_textures() -> None:
        """Force Maya to re-read every scene texture from disk."""
        MatUtils.reload_textures(refresh_viewport=True)

    # ------------------------------------------------------------------
    # Set Directory
    # ------------------------------------------------------------------

    def set_texture_directory(
        self,
        file_nodes,
        target_dir: str,
        relocate_mode: str = "rewrite",
        allow_missing: bool = False,
    ) -> int:
        """Repath each file node so its texture lives directly under *target_dir*.

        The file work is ``ptk.FileCollect.set_directory`` over this host (a
        FLAT Collect: the copy and move engine Find & Copy runs on), the repath
        one undo step here (the disk work is not undoable). *relocate_mode*:
          - ``"rewrite"`` -- nothing on disk: the path only.
          - ``"copy"`` / ``"move"`` -- each file brought into *target_dir* under
            its name, a tile set whole, identical bytes already there reused; a
            move takes a source away only once its copy compares whole --
            never one that IS the destination however spelled (a junction, a
            subst or mapped drive), never one a texture reader outside
            *file_nodes* still reads (that one is copied). A name a different
            file holds at the destination, or that two different files of the
            batch bring, leaves those nodes on their path, warned: Set
            Directory keeps nothing apart in subfolders and never rebinds a
            node to bytes it did not bring. (Its own loop took a same-SIZE file
            for the copy and deleted the only one.)

        A node is repathed only when its texture is ACTUALLY at the destination
        afterwards -- tile/frame tokens resolved, so a UDIM set counts as present
        when its tiles are. Every mode used to rewrite the plug regardless, which
        is how picking sourceimages in the default (path-only) mode filled the
        table with red rows naming files that were never at the destination.
        *allow_missing* restores the blind rewrite for the one case that wants
        it: pointing a batch at a folder you are about to fill. A node whose
        texture is on disk nowhere has nothing to bring, in any mode, and is
        repathed by that rule.

        Records prior paths in :attr:`previous_paths`. Returns the number of
        file nodes updated.
        """
        if relocate_mode not in self.RELOCATE_MODES:
            raise ValueError(
                f"Unknown relocate_mode {relocate_mode!r}; expected one of "
                f"{sorted(self.RELOCATE_MODES)}."
            )
        if not file_nodes:
            return 0
        target = os.path.normpath(target_dir).replace("\\", "/")
        done = FileCollect(self).set_directory(
            [str(n) for n in file_nodes],
            target,
            relocate_mode,
            allow_missing=allow_missing,
            copy=self._copy_into,
            kind="texture",
        )
        for level, message, items in done["report"]:
            self._log(f"Set Directory: {message}", level, items)
        with CoreUtils.undo_chunk("Set Texture Directory"):
            count = self._repath_nodes(done["repath"])
        if done["absent"]:
            self._log(
                f"Set Directory: {len(done['absent'])} file node(s) left on their "
                f"current path — '{target}' holds no texture of that "
                "name, and repathing them would only spell the breakage "
                "differently. Use copy/move to bring the files along, or tick "
                "'Allow Missing Targets' to point them there anyway.",
                "warning",
                items=done["absent"],
            )
        return count

    # ------------------------------------------------------------------
    # Make Paths Absolute / Normalize Paths
    # ------------------------------------------------------------------

    def make_paths_absolute(self, file_nodes) -> int:
        """Rewrite relative texture paths to absolute -- inverse of Normalize Paths.

        Each relative path is resolved against the project root (the same rule
        Maya and the table's validity check use) and written back absolute. Pure
        path rewrite -- no files move; UDIM tokens live in the basename, so they
        survive the prefix join. Missing files are still rewritten (the absolute
        form points where Maya would have looked); Resolve Missing Textures is
        the command for finding them.

        "Relative" is judged on the value with environment variables EXPANDED,
        so a ``$TEXDIR/foo.png`` counts as already absolute and is left intact.

        Returns:
            int: How many paths were rewritten.

        Raises:
            ValueError: No project workspace to resolve against.
        """
        workspace = EnvUtils.get_env_info("workspace") or ""
        if not workspace:
            raise ValueError(
                "Project workspace not set; cannot resolve relative paths."
            )
        source_images = self.texture_dir()

        rewritten = 0
        already_absolute = 0
        cmds.undoInfo(openChunk=True, chunkName="Make Texture Paths Absolute")
        try:
            for node in [str(n) for n in file_nodes]:
                try:
                    path = cmds.getAttr(f"{node}.fileTextureName") or ""
                except Exception:
                    continue
                if not path:
                    continue
                expanded = os.path.expandvars(path)
                if os.path.isabs(expanded) or os.path.splitdrive(expanded)[0]:
                    already_absolute += 1
                    continue
                new_path = MatUtils.to_absolute(path, workspace, source_images)
                try:
                    Attributes.set_plug_literal(f"{node}.fileTextureName", new_path)
                    self.previous_paths[node] = path
                    rewritten += 1
                except Exception as e:
                    self._log(f"{node}: failed to set path: {e}", "warning")
        finally:
            cmds.undoInfo(closeChunk=True)

        self._log(
            f"Make Paths Absolute — rewritten: {rewritten}; "
            f"already absolute: {already_absolute}."
        )
        return rewritten

    def normalize_paths(self, file_nodes, external_mode: str = "rewrite") -> int:
        """Rewrite (selected) paths inside the project to relative.

        A thin driver over :meth:`MatUtils.stage_textures_relative`
        (``scope="project"``) -- the same engine behind the Scene Exporter's
        Convert To Relative Paths task, so the editor and the exporter cannot
        drift on what "relative" means. This method owns only the mode mapping,
        the :attr:`previous_paths` bookkeeping and the summary line.

        "Inside" means under the project ROOT, not under sourceimages: a texture
        already in the project is portable where it sits, and *external_mode*
        reads on "external" textures only. Under sourceimages a path is made
        relative to that rule (the form Maya keeps across a reload); elsewhere in
        the project, relative to the root.

        Per node (engine semantics):
          - already relative -> no-op, unless it is in the legacy
            ``sourceimages/...`` spelling, which is upgraded in place.
          - absolute inside the project -> rewritten relative; tokens survive.
          - absolute inside the project, file missing -> left untouched.
          - absolute outside the project, by mode: ``"rewrite"`` left untouched;
            ``"copy"`` staged into sourceimages (a token set stages every tile),
            then rebound relative; ``"move"`` as copy, the original removed
            once no texture reader reads it (``ptk.FileCollect.finish_move``:
            one outside the batch keeps it).
          - absolute outside the project, file missing -> left untouched.

        Collision policy (the engine's): a same-named sourceimages resident is
        reused only when its CONTENT provably matches; a different file under
        that name is staged alongside it under an ``_N`` index, loudly.

        Returns:
            int: How many paths changed.
        """
        if external_mode not in self.NORMALIZE_MODES:
            raise ValueError(
                f"Unknown external_mode {external_mode!r}; "
                f"expected one of {sorted(self.NORMALIZE_MODES)}."
            )

        nodes = [str(n) for n in file_nodes]
        before = {node: self.stored_path(node) for node in nodes}

        # The engine is ``@CoreUtils.undoable`` -- one undo step, no chunk here.
        results = MatUtils.stage_textures_relative(
            nodes,
            external_mode={"rewrite": "skip"}.get(external_mode, external_mode),
            scope="project",
        )

        changed = 0
        for node in nodes:
            try:
                after = cmds.getAttr(f"{node}.fileTextureName") or ""
            except Exception:
                continue
            if before.get(node) and after != before[node]:
                self.previous_paths[node] = before[node]
                changed += 1

        rewritten = already_relative = 0
        external_relocated = variant_staged = 0
        external_left = missing_left = 0
        for status in results.values():
            if status == "relativized":
                rewritten += 1
            elif status == "already-relative":
                already_relative += 1
            elif status in ("copied+relativized", "moved+relativized"):
                external_relocated += 1
            elif status == "variant+relativized":
                external_relocated += 1
                variant_staged += 1
            elif status == "skipped:external":
                external_left += 1
            elif status == "skipped:missing-source":
                missing_left += 1
            # Any other skipped:* was already warned about by the engine.

        relocate_label = {"rewrite": "—", "copy": "copied", "move": "moved"}[
            external_mode
        ]
        self._log(
            f"Normalize Paths — rewritten: {rewritten}; "
            f"already relative: {already_relative}; "
            f"external {relocate_label}: {external_relocated}"
            + (f" ({variant_staged} as _N variants)" if variant_staged else "")
            + f"; external left as-is: {external_left}; "
            f"missing left as-is: {missing_left}."
        )
        return changed

    # ------------------------------------------------------------------
    # Resolve Missing
    # ------------------------------------------------------------------

    def _strategies_for_modes(self, modes, index_stems):
        """Concatenate strategy lists for each enabled mode, dedup-preserving order.

        ``exact`` is the first tier of every mode and gets deduplicated so it
        only runs once at the head of the pipeline.
        """
        pipeline = []
        seen = set()
        for mode in modes:
            for s in self._strategies_for_mode(mode, index_stems):
                key = id(s) if callable(s) else s
                if key in seen:
                    continue
                seen.add(key)
                pipeline.append(s)
        return pipeline

    def _strategies_for_mode(self, mode: str, index_stems):
        if mode == "stem":
            return ["exact"]
        if mode == "fuzzy":
            # use_base_name is intentionally NOT in the pipeline: numbered
            # variants like texture_001 / texture_002 should not auto-fuse.
            return ["exact", "substring", "ratio"]
        if mode == "texture":
            return [
                "exact",
                self._texture_aware_strategy(index_stems),
                "substring",
                "ratio",
            ]
        raise ValueError(f"Unknown resolve mode: {mode!r}")

    @staticmethod
    def _texture_aware_strategy(index_stems):
        """Custom strategy: filter by map type, then fuzzy-match base name.

        For a missing ``asset01_..._ao``, this restricts candidates to other
        ``_AO`` files, so an ``_AO`` file node can never get repathed to a
        ``_DIFF`` / ``_NORM`` / ``_SPEC`` file.
        """
        candidate_meta = []
        for stem in index_stems:
            try:
                map_type = MapFactory.resolve_map_type(stem + ".png", key=True)
            except Exception:
                map_type = None
            try:
                base = ImgUtils.get_base_texture_name(stem + ".png").lower()
            except Exception:
                base = stem
            candidate_meta.append((stem, base, map_type))

        def texture(target, candidates):
            try:
                target_map = MapFactory.resolve_map_type(target + ".png", key=True)
            except Exception:
                target_map = None
            if not target_map:
                return None, 0.0, "no_match"
            try:
                target_base = ImgUtils.get_base_texture_name(target + ".png").lower()
            except Exception:
                target_base = target

            same_map_stems = []
            same_map_bases = []
            for stem, base, map_type in candidate_meta:
                if map_type == target_map:
                    same_map_stems.append(stem)
                    same_map_bases.append(base)

            if not same_map_bases:
                return None, 0.0, "no_match"

            base_match, score, status = FuzzyMatcher.find_unique_match(
                target_base,
                same_map_bases,
                score_threshold=0.5,
                ambiguity_delta=0.05,
                use_base_name=False,
                use_substring=True,
                use_prefix=False,
                use_ratio=False,
            )
            if status == "no_match":
                return None, 0.0, "no_match"
            idx = same_map_bases.index(base_match)
            return same_map_stems[idx], score, status

        return texture

    def resolve_missing_textures(self, modes, file_nodes=None) -> int:
        """Rebind missing textures to files found under sourceimages.

        The enabled *modes* run as one cascade, safest first (``stem`` exact
        name, ``texture`` same map type + base name, ``fuzzy`` similar name);
        the first unique hit wins. A token path is never rebound (this binds ONE
        file, which would flatten a tile set).

        Parameters:
            modes: Mode names drawn from :attr:`RESOLVE_STRATEGY_ORDER`.
            file_nodes: The file nodes to restrict to; ``None``: every one.

        Returns:
            int: How many were rebound.
        """
        if not modes:
            raise ValueError("At least one mode is required.")
        unknown = set(modes) - set(self.RESOLVE_STRATEGY_ORDER)
        if unknown:
            raise ValueError(f"Unknown resolve mode(s): {sorted(unknown)}")

        sourceimages = EnvUtils.get_env_info("sourceimages")
        if not sourceimages or not os.path.isdir(sourceimages):
            self._log(f"sourceimages directory not found: {sourceimages}", "warning")
            return 0

        workspace = EnvUtils.get_env_info("workspace") or ""
        if file_nodes is None:
            all_file_nodes = cmds.ls(type="file") or []
        else:
            # Preserve namespaces; stripping breaks cmds.getAttr/setAttr.
            all_file_nodes = [str(n) for n in file_nodes]
        if not all_file_nodes:
            self._log("No file nodes to process.", "warning")
            return 0

        missing = []
        for node in all_file_nodes:
            try:
                path = cmds.getAttr(f"{node}.fileTextureName") or ""
            except Exception:
                continue
            # A token path is skipped whether or not its tiles are there: this
            # command rebinds to ONE concrete file, which would flatten a tile
            # set to a single tile.
            if not path or MatUtils.has_path_token(path):
                continue
            if self._texture_on_disk(
                MatUtils.to_absolute(path, workspace, sourceimages)
            ):
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem:
                missing.append((node, path, stem))

        if not missing:
            self._log("No missing textures to resolve.")
            return 0

        index = []
        # Never into a folder of stale copies (a sync cache, the Recycle Bin,
        # _superseded): a lone copy there was bound as the missing texture.
        for root, _, files in FileDependencies.walk(sourceimages):
            for f in files:
                stem = os.path.splitext(f)[0]
                if stem:
                    index.append((stem.lower(), os.path.join(root, f)))
        if not index:
            self._log("No files in sourceimages to match against.", "warning")
            return 0

        by_stem = {}
        for stem_key, abs_path in index:
            by_stem.setdefault(stem_key, []).append(abs_path)
        index_stems = list(by_stem.keys())

        strategies = self._strategies_for_modes(modes, index_stems)

        resolved = 0
        ambiguous = 0
        no_match = 0
        cmds.undoInfo(openChunk=True, chunkName="Resolve Missing Textures")
        try:
            for node, current_path, stem in missing:
                match_name, _score, status, strat_name = (
                    FuzzyMatcher.find_with_fallbacks(
                        stem.lower(),
                        index_stems,
                        strategies=strategies,
                        score_threshold=0.6,
                        ambiguity_delta=0.05,
                    )
                )
                if status == "no_match":
                    no_match += 1
                    continue
                if status == "ambiguous":
                    ambiguous += 1
                    self._log(
                        f"{node}: ambiguous {strat_name} match for '{stem}', skipped.",
                        "warning",
                    )
                    continue
                # The one rule a lookup by name follows (Find & Copy's search
                # and the lightmap heal too): identical copies are one file,
                # several different ones are never settled by walk order.
                match, several = FileDependencies.choose(by_stem.get(match_name) or [])
                if several:
                    ambiguous += 1
                    self._log(
                        f"{node}: '{match_name}' resolves to {len(several)} different "
                        "files, skipped",
                        "warning",
                        items=several,
                    )
                    continue
                if not match:
                    no_match += 1
                    continue
                new_path = MatUtils.to_project_relative(match, workspace, sourceimages)
                try:
                    Attributes.set_plug_literal(f"{node}.fileTextureName", new_path)
                    self.previous_paths[node] = current_path
                    resolved += 1
                    self._log(f"{node}: '{current_path}' -> '{new_path}'")
                except Exception as e:
                    self._log(f"{node}: failed to set path: {e}", "warning")
        finally:
            cmds.undoInfo(closeChunk=True)

        self._log(
            f"Resolved {resolved}/{len(missing)} missing "
            f"(no match: {no_match}, ambiguous: {ambiguous}); "
            f"strategies: {', '.join(modes)}."
        )
        return resolved

    # ------------------------------------------------------------------
    # Find & Copy -- Resolve, then Collect
    # ------------------------------------------------------------------

    def unresolved_file_nodes(self, file_nodes):
        """The nodes whose stored path does not resolve -- what a Find & Copy
        search folder is for. Re-read on every call: a Resolve Missing pass since
        must not still count the textures it fixed."""
        return [
            node
            for node in (str(n) for n in file_nodes)
            if not self.resolve_path(self.stored_path(node), node)[0]
        ]

    @staticmethod
    def _find_copy_answers(answers):
        """``(source_dir, dest_dir, mode)`` of a Find & Copy answer dict. The
        mode is normalized HERE: one read straight off a combo ("Copy" /
        "Move") has to mean the same thing on every path in."""
        mode = "move" if str(answers.get("mode", "")).lower() == "move" else "copy"
        return answers.get("source_dir") or "", answers.get("dest_dir") or "", mode

    def plan_find_and_copy(
        self, file_nodes, answers, lightmaps=(), choices=None, progress=None
    ):
        """What Find & Copy would do: one row per file node and per lightmap.

        Find & Copy is a Resolve, then a Collect (``ptk.FileCollect``). A node
        whose stored path resolves is its own source -- a search could only
        rediscover the bytes the scene renders with. One that does not is
        searched for by name under ``answers["source_dir"]``, and a name
        several different files carry is AMBIGUOUS: its row lists the
        candidates and waits for *choices*, never the newest. Each file keeps
        its name and lands flat in ``answers["dest_dir"]``, unless a different
        file of that name is there or gathered with it: then under the
        shortest run of its own parent folders that tells it apart
        (``dest/wood/diffuse.png``). Identical files share one copy, a file
        already under the destination stays where it is, and a tile set
        travels whole. Nothing is written: this is the ONE call a preview and
        its commit derive every destination through.

        Parameters:
            file_nodes: The nodes in scope.
            answers: ``source_dir``, ``dest_dir``, ``mode`` (``"Copy"`` /
                ``"Move"``, any case).
            lightmaps: The lightmap dependency records in scope
                (:meth:`lightmap_dependencies`), planned with the textures so
                the two can never claim one destination.
            choices: ``{row key: path}`` -- the file picked for an ambiguous
                row, keyed by ``ptk.FileCollect.row_key`` (``("texture",
                node)``, ``("lightmap", map name)``).
            progress: ``progress(text)`` -> a context manager yielding a
                ``callback(done, total)`` for the search; ``None``: none.

        Returns:
            list[dict]: ``ptk.FileCollect.plan`` rows (``owner``, ``source``,
            ``candidates``, ``destination``, ``folder``, ``status``), each with
            its ``kind``: ``"texture"`` (with its stored ``path``) or
            ``"lightmap"``.
        """
        source_dir, dest_dir, mode = self._find_copy_answers(answers)
        with self._progress(progress, f"Searching {source_dir or 'the paths'}…") as tick:
            return FileCollect(self).plan_nodes(
                [str(n) for n in file_nodes],
                dest_dir,
                search_dir=source_dir,
                find_files=self._texture_walk(tick),
                choices=choices,
                kind="texture",
                extra=self._lightmap_entries(lightmaps, source_dir, dest_dir, mode),
            )

    def find_and_copy_textures(
        self, file_nodes, answers, lightmaps=(), dry_run=False, progress=None, plan=None
    ) -> bool:
        """Find & Copy: gather the scope's textures into one folder and repath.

        The answers arrive as a plain dict -- from a panel, a test, anything
        that can name the values -- so the work is reachable and checkable
        without a window in front of it. The plan is
        :meth:`plan_find_and_copy`'s: a node found nowhere keeps its path (no
        search folder, or nothing found), an ambiguous one too until a pick
        names its file, and the rest relocate -- with 48 of 50 paths valid,
        those 48 must not be lost to the two that are broken. The report, the
        file work and the honest close (what is STILL unresolved, by node) are
        ``ptk.FileCollect.run``'s; the repath is :meth:`repath`, one undo step.

        Parameters:
            file_nodes: The nodes to relocate textures for.
            answers: ``source_dir``, ``dest_dir``, ``mode`` (``"Copy"`` /
                ``"Move"``, any case).
            lightmaps: The lightmap dependency records in scope
                (:meth:`lightmap_dependencies`): relocated with the textures,
                their bake markers repointed.
            dry_run: Report the plan; change nothing (no folder created, no
                file relocated, no plug written).
            progress: ``progress(text)`` -> a context manager yielding a
                ``callback(done, total)`` for the search and the copy; ``None``:
                no progress.
            plan: :meth:`plan_find_and_copy` rows to commit as they were shown
                (a preview's, the user's picks applied) instead of planning.

        Returns:
            bool: whether there was anything to do -- the signal a panel's Apply
            button is armed from, so a preview that found nothing offers nothing.
        """
        source_dir, dest_dir, mode = self._find_copy_answers(answers)
        rows = list(plan) if plan is not None else self.plan_find_and_copy(
            file_nodes, answers, lightmaps=lightmaps, progress=progress
        )
        return FileCollect(self, report=self._log).run(
            rows,
            dest_dir,
            mode=mode,
            dry_run=dry_run,
            search_dir=source_dir,
            copy=self._copy_into,
            progress=progress,
        )

    def read_elsewhere(self, nodes):
        """The files the scene reads through texture readers other than *nodes*
        (``ptk.CollectHost``): every ``file`` and ``aiImage`` node in the scene,
        in the table's scope or not, absolute (``MatUtils._texture_reader_paths``,
        which Normalize Paths' move keeps by too). A Move copies these instead
        of taking them away -- moved, an Arnold-only file node or an unselected
        row reading the same file was left on a deleted path."""
        return MatUtils._texture_reader_paths(nodes, *self._project_roots())

    def stored_form(self, path: str, file_node=None) -> str:
        """The path a file node stores for the absolute *path*: project-relative
        when it lies inside the project (``MatUtils.to_project_relative``), the
        form every repath here writes. *file_node* is accepted for the hosts'
        one signature (``ptk.CollectHost``)."""
        return MatUtils.to_project_relative(path, *self._project_roots())

    def repath(self, rows) -> int:
        """Point each landed Collect row at its destination (``ptk.CollectHost``),
        one undo step: a texture row's file node (:meth:`_repath_nodes`), a
        lightmap row's bake markers (each at the folder its map landed in; the
        manifest republished). Returns how many nodes and markers changed."""
        maps = [r for r in rows if r.get("kind") == "lightmap"]
        textures = [r for r in rows if r.get("kind") != "lightmap"]
        with CoreUtils.undo_chunk("Remap Found Textures"):
            return self._repath_nodes(textures) + self._repoint_lightmaps(maps)

    def _repoint_lightmaps(self, rows) -> int:
        """Repoint the markers of the landed lightmap rows, each map at the
        folder it landed in (``LightmapRecords.landed_dirs``)."""
        if not rows:
            return 0
        records = self._lightmap_records()
        dirs = records.landed_dirs(r["destination"] for r in rows)
        markers = records.repath_lightmaps(dirs)
        self._log(f"{markers} bake marker(s) repointed.", "success")
        return markers

    def _repath_nodes(self, rows) -> int:
        """Repath each texture row's file node to its destination (inside
        :meth:`repath`'s one undo step).

        A plug already holding its final form is left alone -- rewriting it
        would dirty every plug, reload every texture and report them all as
        remapped.
        """
        count = unchanged = 0
        for row in rows:
            node, current = row["owner"], self.stored_path(row["owner"])
            final = self.stored_form(row["destination"], node)
            if final == current:
                unchanged += 1
                continue
            try:
                Attributes.set_plug_literal(f"{node}.fileTextureName", final)
            except Exception as e:
                self._log(f"{node}: failed to set path: {e}", "warning")
                continue
            self.previous_paths[node] = current
            count += 1
        self._log(f"Remapped {count} file nodes.", "success" if count else "info")
        if unchanged:
            self._log(f"{unchanged} already pointed at the destination.")
        return count

    @staticmethod
    def _progress(progress, text):
        """``progress(text)``, or a context yielding no callback when there is none."""
        if progress is not None:
            return progress(text)
        import contextlib

        return contextlib.nullcontext(None)

    @staticmethod
    def _texture_walk(tick=None):
        """The search's walk: Maya's texture walk (``MatUtils.find_texture_files``:
        the shared skip list, tile tokens), ticking *tick* per folder so a
        marquee advances while it runs."""

        def walk(names, root):
            return MatUtils.find_texture_files(
                filenames=list(names),
                source_dir=root,
                recursive=True,
                quiet=True,
                progress_callback=tick,
            )

        return walk

    @staticmethod
    def _copy_into(sources, dest_dir, mode, progress=None):
        """The Collect's copier: ``MatUtils.move_texture_files`` (threaded, a
        timeout per file, never over a different file), ticking *progress*
        per file -- a False back cancels. ``ptk.FileCollect.apply`` asks for
        copies and takes a moved source away itself, once it landed."""
        tick = None
        if progress is not None:
            tick = lambda done, total, _name=None: progress(done, total) is not False  # noqa: E731
        return MatUtils.move_texture_files(
            found_files=list(sources),
            new_dir=dest_dir,
            delete_old=mode == "move",
            progress_callback=tick,
        )

    def _lightmap_entries(self, lightmaps, source_dir, dest_dir, mode):
        """The lightmap rows to plan beside the textures: the records' own dry
        Collect (``LightmapRecords.relocate_lightmaps``: their search under
        *source_dir*, the maps a move must keep)."""
        if not lightmaps:
            return []
        planned = self._lightmap_records().relocate_lightmaps(
            dest_dir,
            source_dir=source_dir,
            mode=mode,
            maps=self._lightmap_maps(lightmaps),
            dry_run=True,
        )
        return [dict(row, kind="lightmap") for row in planned["plan"]]

    # ------------------------------------------------------------------
    # Repath one node / a typed path
    # ------------------------------------------------------------------

    def set_texture_path(self, file_node, path: str, relative: bool = False) -> str:
        """Point *file_node* at *path* -- Browse for File, or a typed path.

        Parameters:
            file_node: The node to repath.
            path: The new path.
            relative: Store *path* in its project-relative form when it has one
                (a picked file); ``False`` stores it as given (a typed path).

        Returns:
            str: The path now stored.

        Raises:
            ValueError: The node is gone.
            RuntimeError: The plug refused the path.
        """
        if not self.node_exists(file_node):
            raise ValueError(f"File node '{file_node}' no longer exists.")
        current = self.stored_path(file_node)
        new_path = MatUtils.to_project_relative(path) if relative else path
        with CoreUtils.undo_chunk("Set Texture Path"):
            Attributes.set_plug_literal(f"{file_node}.fileTextureName", new_path)
        if current and current != new_path:
            self.previous_paths[str(file_node)] = current
        self._log(f"{file_node}: '{current}' -> '{new_path}'")
        return new_path

    def is_file_rename(self, stored: str, typed: str, file_node=None) -> bool:
        """Whether a typed path renames *stored*'s file rather than repointing.

        Same folder, another name, the old file on disk and the new one not:
        there is nothing to point at, so the edit means "call it this". A typed
        path naming a file that exists stays a repoint, and so does an edit of a
        file outside the scene's project (``DataNodes.scene_project_root``, the
        boundary Keep Names In Sync keeps) -- another project may read it, so a
        path edit must not rename a shared library texture; the warning says so.
        Rename File, which asks for the rename, still renames it.

        *file_node* is accepted for the hosts' one signature (blendertk resolves
        a linked image against its library).
        """
        if not stored or not typed:
            return False
        workspace, sourceimages = self._project_roots()
        old = MatUtils.to_absolute(stored, workspace, sourceimages)
        new = MatUtils.to_absolute(typed, workspace, sourceimages)
        if not (
            FileUtils.is_same_file(os.path.dirname(old), os.path.dirname(new))
            and os.path.basename(old) != os.path.basename(new)
            and self._texture_on_disk(old)
            and not self._texture_on_disk(new)
        ):
            return False
        project = DataNodes.scene_project_root()
        if project and FileUtils.is_under(os.path.abspath(old), project):
            return True
        self._log(
            f"{os.path.basename(old)} is outside this project -- another may read "
            "it, so a typed path does not rename it (Rename File does).",
            "warning",
        )
        return False

    def apply_path_edit(self, file_node, typed: str, shader=None, sync=None) -> str:
        """A path typed into *file_node*'s row: rename its file, or repoint it.

        Only the file name changed, to one nothing is at (:meth:`is_file_rename`):
        the file is renamed and every reader follows (:meth:`rename_texture`;
        with *sync*, Keep Names In Sync follows *shader*, the row's). Anything
        else repoints the node at the typed path, as typed.

        Returns:
            str: ``"renamed"`` or ``"repathed"``.

        Raises:
            ValueError, OSError, RuntimeError: The rename or the repath was
                refused; nothing changed.
        """
        typed = str(typed or "").strip()
        stored = self.stored_path(file_node)
        if self.is_file_rename(stored, typed, file_node):
            name = os.path.basename(typed.replace("\\", "/"))
            self.rename_texture(
                {"file_node": file_node, "shader_node": shader}, name, sync=sync
            )
            return "renamed"
        self.set_texture_path(file_node, typed)
        return "repathed"

    # ------------------------------------------------------------------
    # Renaming -- a texture file, nodes, and the names kept in sync
    # ------------------------------------------------------------------

    def rename_texture(self, context, new_name: str, sync=None) -> bool:
        """Rename the texture file *context* names to *new_name*.

        With *sync* -- Keep Names In Sync's ``(shader affix, file node affix)``,
        each ``(prefix, suffix)`` -- a new base name for a file of the material's
        own texture set renames the whole material instead (:meth:`_sync_names`);
        any other rename -- another set's map, a new map type -- renames that
        file alone and says so (:meth:`_sync_carries`). A lightmap row's bake
        markers are re-stamped with the name (:meth:`rename_lightmap`).

        Parameters:
            context: ``{"file_node", "shader_node"}`` (a texture row: the shader
                Keep Names In Sync follows, ``None``: none), or ``{"lightmap":
                record}``.
            new_name: The new file NAME, no folder.
            sync: ``None`` (Keep Names In Sync off) or the two affixes.

        Returns:
            bool: ``True`` (a refusal raises).

        Raises:
            ValueError, OSError, RuntimeError: The name was refused; nothing
                changed.
        """
        new_name = str(new_name or "").strip()
        dep = context.get("lightmap")
        if dep is not None:
            self.rename_lightmap(dep, new_name)
            return True
        node = context["file_node"]
        stored = self.stored_path(node)
        shader = context.get("shader_node")
        syncing = bool(sync) and bool(shader)
        base = MapFactory.get_base_texture_name(new_name)
        if syncing and self._sync_carries(
            shader, base, sync, texture=(stored, new_name)
        ):
            self._sync_names(shader, base, sync)
            return True
        result = MatUtils.rename_texture_file(stored, new_name)
        self._log(
            f"Renamed {os.path.basename(stored)} -> {new_name}; "
            f"repointed {len(result['nodes'])} file node(s)."
        )
        if syncing:
            self._log(
                self._SYNC_ALONE.format(name=os.path.basename(stored), material=shader),
                "warning",
            )
        return True

    def can_rename_shader(self, shader) -> bool:
        """Whether a shader cell may rename *shader* (any shader in the scene)."""
        return self.node_exists(shader)

    def rename_shader(self, shader, new_name: str, sync=None) -> str:
        """Rename *shader*; with *sync*, its whole material to the new base.

        Keep Names In Sync takes the typed name with the shader affix stripped
        as the base the material, its texture set and their file nodes follow.

        Returns:
            str: The shader's name now (Maya may make it unique).

        Raises:
            ValueError, OSError, RuntimeError: Refused; nothing changed.
        """
        new_name = str(new_name or "").strip()
        if sync:
            base = StrUtils.strip_known_affix(new_name, *sync[0]).strip("_")
            plan = self._sync_names(shader, base, sync)
            return (plan.get("material") or (shader, shader))[1]
        return self._rename_node(shader, new_name, "shader")

    def rename_file_node(self, file_node, new_name: str, shader=None, sync=None) -> str:
        """Rename *file_node*; with *sync*, its material when the name is a new base.

        A file node of the material's own texture set given a new base name
        renames the whole material (with or without the file node suffix typed);
        any other name renames the node alone and says so.

        Returns:
            str: The node's name now.

        Raises:
            ValueError, OSError, RuntimeError: Refused; nothing changed.
        """
        new_name = str(new_name or "").strip()
        if sync and shader:
            base = MapFactory.get_base_texture_name(
                StrUtils.strip_known_affix(new_name, *sync[1])
            ).strip("_")
            if self._sync_carries(shader, base, sync, node=(file_node, new_name)):
                plan = self._sync_names(shader, base, sync)
                return dict(plan["file_nodes"]).get(file_node, file_node)
            actual = self._rename_node(file_node, new_name, "file node")
            self._log(
                self._SYNC_ALONE.format(name=file_node, material=shader), "warning"
            )
            return actual
        return self._rename_node(file_node, new_name, "file node")

    def _rename_node(self, name, new_name: str, label: str) -> str:
        """``cmds.rename`` with the editor's refusals; returns the name it got."""
        if not new_name or new_name == name:
            return name
        if not self.node_exists(name):
            raise ValueError(f"{label} '{name}' no longer exists; cannot rename.")
        actual = cmds.rename(name, new_name)
        self._log(f"Renamed {label} '{name}' -> '{actual}'")
        return actual

    def _sync_plan(self, shader, base: str, sync, dry_run: bool = False) -> dict:
        """``MatUtils.sync_material_names`` for *shader* at *base*, with *sync*'s
        affixes and the lightmap records (a real run is one undo chunk)."""
        shader_affix, node_affix = sync
        return MatUtils.sync_material_names(
            shader,
            base,
            material_affix=tuple(shader_affix),
            file_node_affix=tuple(node_affix),
            dry_run=dry_run,
            lightmaps=self._lightmap_records(),
        )

    def _sync_carries(self, shader, base: str, sync, texture=None, node=None) -> bool:
        """Whether Keep Names In Sync renames *shader*'s whole material for an
        edit: the sync at *base* (a dry run) gives the edited thing exactly the
        name typed (case aside) -- *texture* ``(stored path, new file name)`` or
        *node* ``(file node, new node name)``.

        That holds for a file of the material's own texture set (or its node)
        whose base alone changed. Syncing to the typed name's base, whatever was
        typed, renamed the set after an environment cube's new name while the
        cube kept its own, and a new map type (the base unchanged) renamed
        nothing.

        Raises:
            ValueError: The sync refuses *base* (``MatUtils.sync_material_names``).
        """
        plan = self._sync_plan(shader, base, sync, dry_run=True)
        if texture is not None:
            stored, name = texture
            path = MatUtils.to_absolute(stored, *self._project_roots())
            planned = next(
                (n for old, n in plan["textures"] if FileUtils.is_same_file(old, path)),
                None,
            )
        else:
            edited, name = node
            planned = dict(plan["file_nodes"]).get(edited)
            name = StrUtils.apply_affix(name, *sync[1])
        return bool(planned) and planned.lower() == name.lower()

    def _sync_names(self, shader, base: str, sync) -> dict:
        """Name *shader*'s material, its texture set, lightmap and nodes for
        *base* (``MatUtils.sync_material_names``), and report it."""
        plan = self._sync_plan(shader, base, sync)
        changed = int(bool(plan["material"])) + sum(
            len(plan[key])
            for key in ("companions", "textures", "file_nodes", "lightmaps")
        )
        self._log(
            f"Names synced to {base!r}: {changed} rename(s) "
            f"({len(plan['textures'])} texture file(s), "
            f"{len(plan['lightmaps'])} lightmap(s))."
        )
        for reason in plan["skipped"]:
            self._log(f"Sync Names: {reason}", "warning")
        return plan

    # ------------------------------------------------------------------
    # Selection / graph / delete
    # ------------------------------------------------------------------

    def select_objects(self, shaders=(), lightmaps=()) -> int:
        """Select the objects wearing *shaders* and those *lightmaps* bind.

        Returns:
            int: How many objects were selected (none: the selection is left).
        """
        objects = []
        for dep in lightmaps:
            objects.extend(o for o in dep.get("objects") or [] if cmds.objExists(o))
        for shader in shaders:
            if not shader:
                continue
            try:
                objects.extend(MatUtils.find_by_mat_id(shader, shell=True) or [])
            except Exception as e:
                self._log(f"Failed to query objects for '{shader}': {e}", "warning")
        objects = list(dict.fromkeys(objects))
        if objects:
            cmds.select(objects, r=True)
        return len(objects)

    def select_file_nodes(self, file_nodes) -> int:
        """Select *file_nodes* (replacing the selection); returns how many."""
        nodes = [n for n in dict.fromkeys(file_nodes) if n]
        if nodes:
            cmds.select(nodes, r=True)
        return len(nodes)

    @staticmethod
    def graph_file_nodes(file_nodes) -> None:
        """Graph *file_nodes* in the Hypershade."""
        nodes = [n for n in file_nodes if n]
        if nodes:
            MatUtils.graph_materials(nodes)

    def delete_consequences(self, file_nodes):
        """What deleting *file_nodes* would unwire, as sentences for a confirm.

        Names the materials they feed (through any intermediates), else the
        other nodes of a live graph (a bump chain that never reaches a surface
        shader breaks all the same); nothing for an orphan texture.
        """
        shaders, other = self._downstream_consumers(file_nodes)
        if shaders:
            shown = ", ".join(shaders[:4])
            if len(shaders) > 4:
                shown += f", +{len(shaders) - 4} more"
            return [
                f"Connected to {len(shaders)} material(s): {shown}.",
                "Those connections will be broken.",
            ]
        if other:
            return [
                f"Connected to {len(other)} node(s) in the shading graph.",
                "Those connections will be broken.",
            ]
        return []

    def _downstream_consumers(self, file_nodes) -> tuple:
        """Return ``(shaders, other)``: what deleting *file_nodes* would unwire.

        ``shaders`` are the surface shaders the textures feed, through any
        intermediates (``MatUtils.get_connected_shaders``). ``other`` catches
        the rest of a live graph, and is reported only when no shader was found.

        Excluded: the nodes being deleted (they take each other down) and Maya's
        default registries (``defaultTextureList1`` ...), which EVERY file node
        hangs off -- counting those would flag an orphan texture as connected.

        Returns:
            tuple: ``(sorted shader names, sorted other consumer names)``.
        """

        # Guard the empty case explicitly rather than lean on what ``cmds.ls``
        # makes of an empty list.
        def _long(nodes):
            nodes = list(nodes)
            return set(cmds.ls(nodes, long=True) or []) if nodes else set()

        doomed = _long(file_nodes)
        shaders = set(MatUtils.get_connected_shaders(file_nodes))
        shader_longs = _long(shaders)

        other = set()
        for node in file_nodes:
            for dst in cmds.listConnections(node, source=False, destination=True) or []:
                dst_long = (cmds.ls(dst, long=True) or [dst])[0]
                if dst_long in doomed or dst_long in shader_longs:
                    continue
                if cmds.ls(dst, defaultNodes=True):
                    continue
                other.add(dst)

        return sorted(shaders), sorted(other)

    def delete_file_nodes(self, file_nodes) -> int:
        """Delete *file_nodes* (one undo step); returns how many."""
        nodes = [n for n in dict.fromkeys(file_nodes) if n]
        if not nodes:
            return 0
        cmds.delete(nodes)
        self._log(f"Deleted {len(nodes)} file node(s).")
        return len(nodes)

    # ------------------------------------------------------------------
    # Lightmap dependencies -- rows with no file node behind them
    # ------------------------------------------------------------------
    #
    # A committed lightmap is referenced by a bake marker (map basename + the
    # folder it was baked into), never by a file node, so every file-node
    # command was blind to it: a migration that copied every texture left the
    # EXRs behind and the export shipped unlit. The records are
    # ``LightmapRecords`` (list / heal / relocate + repoint); the editor shows
    # them as rows and hands the relocation half to Find & Copy.

    @staticmethod
    def _lightmap_records():
        """The lightmap record (a class used as a namespace), imported on use:
        listing file nodes should not load the lightmap package until a
        lightmap is asked for."""
        from mayatk.light_utils.lightmap_baker.lightmap_records import (
            LightmapRecords,
        )

        return LightmapRecords

    def lightmap_dependencies(self):
        """The scene's lightmap dependency records (``LightmapRecords``):
        ``map``, ``dir`` (the recorded folder), ``objects``, ``path`` (where it
        was found, ``None``: nowhere), ``found_by``, ``note``."""
        return self._lightmap_records().lightmap_dependencies()

    def lightmap_labels(self, dep):
        """``(shader, node)`` labels for a lightmap row, read like a texture row.

        Shader column: the material(s) the lightmapped objects wear -- an atlas
        by material is named after its material, and a per-object map still
        belongs to one. Node column: the map's stem, what a file node for it
        would be called (``ROOM_ENV_LightMap``), since none exists. ``""`` for
        the shader when none is found (the panel shows its own label).
        """
        try:
            mats = MatUtils.get_mats(
                [o for o in dep.get("objects") or [] if cmds.objExists(o)]
            )
        except Exception:  # noqa: BLE001 -- a label must never cost the table
            mats = []
        mats = list(dict.fromkeys(str(m) for m in mats or []))
        shader = ", ".join(mats[:2])
        if len(mats) > 2:
            shader += f" (+{len(mats) - 2})"
        return shader, os.path.splitext(dep["map"])[0]

    @staticmethod
    def _lightmap_objects(lightmaps):
        """The transforms the records name -- the records' scope argument."""
        return list(
            dict.fromkeys(o for dep in lightmaps for o in (dep.get("objects") or []))
        )

    @staticmethod
    def _lightmap_maps(lightmaps):
        """The file names the records carry -- a relocation's scope: a row IS
        its file, and the reflection probe's row names no object at all."""
        return list(dict.fromkeys(dep["map"] for dep in lightmaps))

    def normalize_lightmaps(self, lightmaps, relative: bool) -> int:
        """Re-spell the scoped lightmap markers' folders (Normalize / Make Absolute).

        Files never move: inside the project the recorded folder becomes
        workspace-relative (``relative=True``) -- what lets a teammate's copy of
        the project, mounted on another drive, still resolve it -- or is
        expanded to absolute (``relative=False``). A scope with no lightmaps is
        a silent no-op.
        """
        if not lightmaps:
            return 0
        try:
            count = self._lightmap_records().normalize_lightmap_paths(
                self._lightmap_objects(lightmaps), relative=relative
            )
        except Exception as e:  # noqa: BLE001 -- the texture half already ran
            self._log(f"Lightmap folders not rewritten: {e}", "warning")
            return 0
        if count:
            self._log(
                f"{count} lightmap marker(s) now record their folder "
                f"{'relative to the project' if relative else 'as an absolute path'}."
            )
        return count

    def repath_lightmap(self, dep, folder: str) -> bool:
        """Point every marker naming *dep*'s map at *folder*.

        The manual counterpart of Find & Copy's relocation (Browse for File and
        a typed path on a lightmap row): files are not touched, the markers'
        recorded folder changes and the FBX manifest is republished.
        """
        folder = str(folder or "").replace("\\", "/").rstrip("/")
        if not folder:
            self._log("A lightmap path needs a folder.", "warning")
            return False
        try:
            count = self._lightmap_records().repath_lightmaps(
                {dep["map"].lower(): folder}, dep.get("objects")
            )
        except Exception as e:  # noqa: BLE001
            self._log(f"Failed to repath lightmap {dep['map']}: {e}", "warning")
            return False
        self._log(f"{dep['map']}: lightmap folder -> '{folder}' ({count} marker(s))")
        return True

    def rename_lightmap(self, dep, new_name: str) -> None:
        """Rename a lightmap's file and re-stamp the markers that bind it by name.

        Raises:
            ValueError, OSError: The map is not on disk, or the name was refused.
        """
        if not dep.get("path"):
            raise ValueError(f"{dep['map']} is not on disk to rename.")
        with CoreUtils.undo_chunk("Rename Lightmap"):
            MatUtils.rename_texture_file(dep["path"], new_name)
            self._lightmap_records().rename_lightmap(dep["map"], new_name)
        self._log(f"Renamed lightmap {dep['map']} -> {new_name}")

    def lightmap_revert_scope(self, deps):
        """``(objects, kept)``: the objects reverting *deps* would clear, and those
        it must leave (none in Maya; blendertk leaves a linked library's)."""
        return self._lightmap_objects([d for d in deps if d.get("objects")]), []

    def revert_lightmaps(self, objects):
        """The Lightmap Baker's Revert Selected on *objects*: their markers are
        cleared and the manifest republished, so nothing is left bound to the
        map (a legacy atlas UV layout is restored too); the map stays on disk.
        One undo step. Returns the objects cleared."""
        return self._lightmap_records().revert(list(objects))

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    #: ``_log`` level -> the Maya channel that carries it when no logger is
    #: lent. ``cmds.warning`` / ``displayError`` are what put a line in front of
    #: a user who is not looking at the script editor, so a warning must not
    #: quietly degrade to an info line.
    _MAYA_LOG_CHANNELS = {
        "warning": "warning",
        "error": "error",
    }

    @staticmethod
    def _joined(message: str, items) -> str:
        """*message* with its *items* indented under it, as one plain string --
        for a sink with no ``log_group``, where the whole report has to arrive
        as one string or as N lines that read as N separate events."""
        return "\n".join([message, *(f"  {item}" for item in items)])

    def _log(self, message: str, level: str = "info", items=None) -> None:
        """Report one line -- or one titled list -- of a command's result.

        Goes to :attr:`active_logger` when a caller lent one (a panel's pane IS
        the report) and to Maya's own channels otherwise, so the same call site
        works headless and from a test.

        ``items`` is a list belonging under *message*. It goes through
        ``log_group`` so the whole thing is ONE record: a widget handler
        appends a block per record, so a line-per-item loop renders as N
        separate paragraphs instead of a list.
        """
        logger = self.active_logger
        items = list(items) if items else []
        if logger is not None:
            if items and hasattr(logger, "log_group"):
                logger.log_group(message, items, level=level)
                return
            if items:
                message = self._joined(message, items)
            getattr(logger, level, logger.info)(message)
            return
        if items:
            message = self._joined(message, items)
        if cmds is None:  # headless / unit test outside Maya
            return
        channel = self._MAYA_LOG_CHANNELS.get(level)
        if channel == "warning":
            cmds.warning(message)
        elif channel == "error":
            om.MGlobal.displayError(message)
        else:
            om.MGlobal.displayInfo(message)


# --------------------------------------------------------------------------------------------

# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
