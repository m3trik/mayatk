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

    #: Rows of a dry-run listing shown in full before it collapses to a count.
    #: Long enough to recognise the operation, short enough that the plan
    #: stays one screenful next to the form that produced it.
    _PLAN_PREVIEW_ROWS = 12

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

        *relocate_mode* controls disk behavior for nodes whose current path
        resolves to an existing file:
          - ``"rewrite"`` -- path-only (no file movement).
          - ``"copy"`` -- copy the file to *target_dir* then rebind.
          - ``"move"`` -- move the file to *target_dir* then rebind.

        A node is repathed only when the texture is ACTUALLY at the destination
        afterwards -- tile/frame tokens resolved, so a UDIM set counts as present
        when its tiles are. Every mode used to rewrite the plug regardless, which
        is how picking sourceimages in the default (path-only) mode filled the
        table with red rows naming files that were never at the destination.
        *allow_missing* restores the blind rewrite for the one case that wants
        it: pointing a batch at a folder you are about to fill.

        Tile sets relocate per tile (``MatUtils.texture_tiles``) -- the same rule
        ``MatUtils.stage_textures_relative`` stages them by.

        Collision policy: same-name + same-size at destination is a safe rebind
        (no overwrite); different size is skipped + warned. Records prior paths
        in :attr:`previous_paths`. Returns the number of file nodes updated.
        """
        if relocate_mode not in self.RELOCATE_MODES:
            raise ValueError(
                f"Unknown relocate_mode {relocate_mode!r}; expected one of "
                f"{sorted(self.RELOCATE_MODES)}."
            )
        if not file_nodes:
            return 0
        node_names = [str(n) for n in file_nodes]
        target_dir_norm = os.path.normpath(target_dir).replace("\\", "/")
        workspace, source_images = self._project_roots()

        # Phase 1 -- collect (node, old_path, new_path, source tiles, new_abs).
        plan = []
        for node_name in node_names:
            old_path = self.stored_path(node_name)
            if not old_path:
                continue
            new_abs = os.path.normpath(
                os.path.join(target_dir_norm, os.path.basename(old_path))
            ).replace("\\", "/")
            new_path = MatUtils.to_project_relative(new_abs, workspace, source_images)
            if new_path == old_path:
                continue
            # The whole set, not one name: a token path denotes every tile on
            # disk, and each has to travel for the repathed pattern to resolve.
            sources = (
                MatUtils.texture_tiles(
                    MatUtils.to_absolute(old_path, workspace, source_images)
                )
                if relocate_mode != "rewrite"
                else []
            )
            plan.append((node_name, old_path, new_path, sources, new_abs))

        # Phase 2 -- relocations outside the undo chunk (disk ops aren't undoable).
        relocated = 0
        collision_skipped = 0
        skipped_nodes = set()
        if relocate_mode in ("copy", "move"):
            import shutil

            for node_name, _old_path, _new_path, sources, new_abs in plan:
                dst_dir = os.path.dirname(new_abs)
                for src in sources:
                    dst = os.path.join(dst_dir, os.path.basename(src))
                    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(
                        os.path.abspath(dst)
                    ):
                        continue  # already at the destination -- nothing to do
                    try:
                        if os.path.exists(dst):
                            try:
                                same = os.path.getsize(src) == os.path.getsize(dst)
                            except OSError:
                                same = False
                            if not same:
                                self._log(
                                    f"{node_name}: '{os.path.basename(dst)}' already "
                                    "exists at destination with different size; "
                                    "skipping to avoid wrong-file rebind.",
                                    "warning",
                                )
                                collision_skipped += 1
                                skipped_nodes.add(node_name)
                                break
                            if relocate_mode == "move":
                                try:
                                    os.remove(src)
                                except OSError as e:
                                    self._log(
                                        f"{node_name}: equivalent at dst, but could "
                                        f"not remove '{src}': {e}",
                                        "warning",
                                    )
                        else:
                            os.makedirs(dst_dir, exist_ok=True)
                            if relocate_mode == "move":
                                shutil.move(src, dst)
                            else:
                                shutil.copy2(src, dst)
                        relocated += 1
                    except Exception as e:
                        self._log(
                            f"{node_name}: failed to {relocate_mode}: {e}", "warning"
                        )
                        skipped_nodes.add(node_name)
                        break

        # Phase 3 -- path updates (undoable).
        count = 0
        missing_at_target = 0
        cmds.undoInfo(openChunk=True, chunkName="Set Texture Directory")
        try:
            for node_name, old_path, new_path, _sources, new_abs in plan:
                if node_name in skipped_nodes:
                    continue
                # The destination is what the node will READ from, so it is
                # what has to hold a file -- never "did a copy run?" (a texture
                # already sitting in the target needs no copy and must still be
                # rebound to the shorter form).
                if not (allow_missing or self._texture_on_disk(new_abs)):
                    missing_at_target += 1
                    continue
                try:
                    Attributes.set_plug_literal(
                        f"{node_name}.fileTextureName", new_path
                    )
                    self.previous_paths[node_name] = old_path
                    count += 1
                except Exception as e:
                    self._log(f"{node_name}: failed to set path: {e}", "warning")
        finally:
            cmds.undoInfo(closeChunk=True)

        if relocate_mode != "rewrite":
            self._log(
                f"Set Directory — relocated: {relocated} file(s); "
                f"collision skipped: {collision_skipped}; mode: {relocate_mode}."
            )
        if missing_at_target:
            self._log(
                f"Set Directory: {missing_at_target} file node(s) left on their "
                f"current path — '{target_dir_norm}' holds no texture of that "
                "name, and repathing them would only spell the breakage "
                "differently. Use copy/move to bring the files along, or tick "
                "'Allow Missing Targets' to point them there anyway.",
                "warning",
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
            then rebound relative; ``"move"`` as copy, the original removed.
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
                matches = by_stem.get(match_name) or []
                if not matches:
                    no_match += 1
                    continue
                if len(matches) > 1:
                    ambiguous += 1
                    self._log(
                        f"{node}: '{match_name}' resolves to {len(matches)} files, "
                        "skipped.",
                        "warning",
                    )
                    continue
                new_path = MatUtils.to_project_relative(
                    matches[0], workspace, sourceimages
                )
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
    # Find & Copy
    # ------------------------------------------------------------------

    def _partition_resolved_sources(self, node_names):
        """Split nodes into ``({basename: source path}, [unresolved nodes])``.

        A node whose stored path resolves to a file on disk already *is* its own
        source: a recursive walk can only rediscover the same bytes. Only what
        does not resolve has anything to find.

        A token path (``<UDIM>``, ``<uvtile>``, ``<f>``...) is never a literal
        file, so those nodes always land in ``unresolved`` and go through the
        search, which expands the token (``MatUtils.has_path_token`` is the one
        classifier).
        """
        workspace, source_images = self._project_roots()
        resolved = {}
        unresolved = []
        for node in node_names:
            path = self.stored_path(node)
            abs_path = (
                MatUtils.to_absolute(path, workspace, source_images) if path else ""
            )
            if (
                abs_path
                and not MatUtils.has_path_token(abs_path)
                and os.path.isfile(abs_path)
            ):
                resolved.setdefault(os.path.basename(abs_path).lower(), abs_path)
            else:
                unresolved.append(node)
        return resolved, unresolved

    def unresolved_file_nodes(self, file_nodes):
        """The nodes whose stored path does not resolve -- what a Find & Copy
        search folder is for. Re-read on every call: a Resolve Missing pass since
        must not still count the textures it fixed."""
        return self._partition_resolved_sources([str(n) for n in file_nodes])[1]

    def find_and_copy_textures(
        self, file_nodes, answers, lightmaps=(), dry_run=False, progress=None
    ) -> bool:
        """Collect sources, relocate them into one directory, repath the nodes.

        The answers arrive as a plain dict -- from a panel, from a test, from
        anything that can name the values -- so the work is reachable and
        checkable without a window in front of it. A source row with no folder
        in it skips the unresolved nodes and relocates the rest: with 48 of 50
        paths valid, those 48 must not be lost to the two that are broken.

        *dry_run* reports the same decision without acting on it: the search
        still runs (reading the disk is the only way to know what would move),
        but no folder is created, no file is relocated and no plug is written.
        Both passes derive the destination paths through the one
        :meth:`_plan_remap` call, so a preview cannot promise a path the commit
        would not write.

        Parameters:
            file_nodes: The nodes to relocate textures for.
            answers: ``source_dir``, ``dest_dir``, ``mode`` (``"Copy"`` /
                ``"Move"``, any case).
            lightmaps: The lightmap dependency records in scope
                (:meth:`lightmap_dependencies`): relocated after the textures,
                by the same folders, their bake markers repointed.
            dry_run: Report the plan; change nothing.
            progress: ``progress(text)`` -> a context manager yielding a
                ``callback(done, total)`` for the search and the copy; ``None``:
                no progress.

        Returns:
            bool: whether there was anything to do -- the signal a panel's Apply
            button is armed from, so a preview that found nothing offers nothing.
        """
        node_names = [str(n) for n in file_nodes]
        source_dir = answers.get("source_dir") or ""
        dest_dir = answers.get("dest_dir") or ""
        # Normalized HERE: a mode read straight off a combo ("Copy" / "Move")
        # has to mean the same thing on every path in.
        relocate_mode = (
            "move" if str(answers.get("mode", "")).lower() == "move" else "copy"
        )
        lightmaps = list(lightmaps or [])
        resolved, unresolved = self._partition_resolved_sources(node_names)

        found_textures = []
        if unresolved and source_dir:
            # find_texture_files ticks per directory so a marquee advances
            # during the walk.
            with self._progress(progress, f"Searching {source_dir}…") as tick:
                found_textures = MatUtils.find_texture_files(
                    file_nodes=unresolved,
                    source_dir=source_dir,
                    recursive=True,
                    progress_callback=tick,
                )
        elif unresolved:
            self._log(
                f"No search folder — skipping {len(unresolved)} unresolved "
                f"texture(s); continuing with {len(resolved)} valid path(s)."
            )

        if unresolved and source_dir:
            # Name what the search did NOT find, by node: a run that found 46 of
            # 48 read as a success, and the export then failed on the two.
            found_names = {os.path.basename(p).lower() for p in found_textures}
            not_found = [
                node
                for node in unresolved
                if not self._landed(
                    os.path.basename(self.stored_path(node)), found_names
                )
            ]
            if not_found:
                self._log(
                    f"{len(not_found)} of {len(unresolved)} unresolved "
                    f"texture(s) not found under {source_dir} — their file "
                    "nodes keep their current path",
                    "warning",
                    items=[
                        f"{node}:  "
                        f"{os.path.basename(self.stored_path(node)) or '<no path>'}"
                        for node in not_found
                    ],
                )

        if not resolved and not found_textures and not lightmaps:
            self._log("No textures found.", "warning")
            return False

        # Dedup by basename, newest mtime wins: walks can return several matches
        # per name (versioned copies, sync-client conflict copies), and two
        # workers racing on one destination deadlock against a sync client. A
        # valid path outranks every walk hit of that name.
        by_basename = {}
        for fpath in found_textures:
            bn = os.path.basename(fpath).lower()
            if bn in resolved:
                continue
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                mtime = 0.0
            existing = by_basename.get(bn)
            if existing is None or mtime > existing[0]:
                by_basename[bn] = (mtime, fpath)
        if found_textures and len(by_basename) < len(found_textures):
            self._log(
                f"{len(found_textures)} candidates found, deduped to "
                f"{len(by_basename)} unique basenames (newest wins)."
            )
        deduped = list(resolved.values()) + [v[1] for v in by_basename.values()]

        # Created if missing: a folder that does not exist yet is a normal
        # answer to "put them here".
        if not dry_run:
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as e:
                self._log(f"Cannot create '{dest_dir}': {e}", "error")
                return False

        # Sources already in the destination need no file op -- a Move would be
        # a self-copy, which shutil rejects -- and skip straight to the repath.
        dest_key = os.path.normcase(os.path.abspath(dest_dir))
        in_place, to_relocate = [], []
        for src in deduped:
            same_dir = (
                os.path.normcase(os.path.dirname(os.path.abspath(src))) == dest_key
            )
            (in_place if same_dir else to_relocate).append(src)

        if dry_run:
            return self._report_find_and_copy_plan(
                node_names,
                dest_dir,
                relocate_mode,
                in_place,
                to_relocate,
                lightmap_plan=self._plan_lightmaps(
                    lightmaps, source_dir, dest_dir, relocate_mode
                ),
            )

        copied = []
        if to_relocate:
            relocate_verb = "Moving" if relocate_mode == "move" else "Copying"
            with self._progress(progress, f"{relocate_verb} textures…") as tick:
                copied = MatUtils.move_texture_files(
                    found_files=to_relocate,
                    new_dir=dest_dir,
                    delete_old=(relocate_mode == "move"),
                    progress_callback=tick,
                )
            if not copied and not in_place and not lightmaps:
                self._log(f"No textures {relocate_verb.lower()}.", "warning")
                return False

        if deduped:
            self._log(
                f"Find & {relocate_mode.title()} — sources: {len(resolved)} from "
                f"valid path(s), {len(by_basename)} found by search; "
                f"{len(copied)} relocated, {len(in_place)} already at destination."
            )

        landed = [dst for _src, dst in copied] + list(in_place)
        copied_basenames = {
            os.path.basename(p).lower(): os.path.basename(p) for p in landed
        }
        remap = self._plan_remap(node_names, dest_dir, copied_basenames)

        if remap:
            cmds.undoInfo(openChunk=True, chunkName="Remap Found Textures")
            try:
                count = 0
                unchanged = 0
                for node_name, path, final_path in remap:
                    if final_path == path:
                        # Already the stored form -- rewriting it would dirty
                        # every plug, reload every texture and report them all
                        # as remapped.
                        unchanged += 1
                        continue
                    Attributes.set_plug_literal(
                        f"{node_name}.fileTextureName", final_path
                    )
                    self.previous_paths[node_name] = path
                    count += 1
                self._log(
                    f"Remapped {count} file nodes.", "success" if count else "info"
                )
                if unchanged:
                    self._log(f"{unchanged} already pointed at the destination.")
            finally:
                cmds.undoInfo(closeChunk=True)
        elif deduped:
            self._log("No file nodes matched the copied textures.", "warning")

        if lightmaps:
            self._relocate_lightmaps(lightmaps, source_dir, dest_dir, relocate_mode)

        # The honest close: what is STILL broken after this run, by node,
        # re-partitioned from the live plugs.
        if node_names:
            _still_ok, still_unresolved = self._partition_resolved_sources(node_names)
            if still_unresolved:
                self._log(
                    f"{len(still_unresolved)} texture(s) still unresolved after "
                    "this run — the export's path check will fail on them",
                    "warning",
                    items=[
                        f"{node}:  {self.stored_path(node) or '<no path>'}"
                        for node in still_unresolved
                    ],
                )
        return True

    @staticmethod
    def _progress(progress, text):
        """``progress(text)``, or a context yielding no callback when there is none."""
        if progress is not None:
            return progress(text)
        import contextlib

        return contextlib.nullcontext(None)

    def _plan_remap(self, node_names, dest_dir, basenames):
        """``[(node, stored path, final path)]`` for every node a relocation repaths.

        The ONE place a destination path is derived, so a preview cannot promise
        a path the committed pass would not write. Flattened deliberately: the
        files land in *dest_dir*'s root.

        *basenames* maps each landed file's lower-case basename to its name as
        it landed. The search matches names case-insensitively, so a stored
        ``WOOD.png`` can land as ``Wood.png``; the node takes the landed spelling,
        the only one a case-sensitive filesystem opens.
        """
        workspace, source_images = self._project_roots()
        plan = []
        for node_name in node_names:
            try:
                path = cmds.getAttr(f"{node_name}.fileTextureName")
            except Exception:
                continue
            name = os.path.basename(path or "")
            if not name or not self._landed(name, basenames):
                continue
            final_path = MatUtils.to_project_relative(
                os.path.join(dest_dir, basenames.get(name.lower(), name)),
                workspace,
                source_images,
            )
            plan.append((node_name, path, final_path))
        return plan

    @staticmethod
    def _landed(basename: str, landed) -> bool:
        """Did a file for *basename* land -- literally, or as tiles of its set?

        *landed* holds the lower-case basenames of the files copied or already
        at the destination. A tokened stored name (``rock.<UDIM>.png``) is never
        among them literally -- its TILES are -- so it is matched as the pattern
        the token table spells (:meth:`MatUtils.token_wildcard`).
        """
        lower = basename.lower()
        if not MatUtils.has_path_token(lower):
            return lower in landed
        import fnmatch

        pattern = MatUtils.token_wildcard(lower, None).lower()
        return any(fnmatch.fnmatchcase(name, pattern) for name in landed)

    def _report_find_and_copy_plan(
        self,
        node_names,
        dest_dir,
        relocate_mode,
        in_place,
        to_relocate,
        lightmap_plan=None,
    ):
        """Report what a live pass WOULD do, having written nothing.

        Every line comes from the same values the commit acts on -- the
        partitioned source lists, :meth:`_plan_remap`, and the lightmap plan the
        engine's own dry run returned.
        """
        verb = "Move" if relocate_mode == "move" else "Copy"
        basenames = {
            os.path.basename(p).lower(): os.path.basename(p)
            for p in (*in_place, *to_relocate)
        }
        remap = self._plan_remap(node_names, dest_dir, basenames)
        changed = [row for row in remap if row[2] != row[1]]
        lightmaps_change = bool(
            lightmap_plan and (lightmap_plan["relocate"] or lightmap_plan["in_place"])
        )

        if not to_relocate and not changed and not lightmaps_change:
            self._log(
                "Dry run — nothing would change: every texture is already at "
                "the destination and every path already stored in its final form.",
                "warning",
            )
            self._report_lightmap_plan(lightmap_plan, dest_dir, verb)
            return False

        if to_relocate:
            self._log(
                f"Dry run — would {verb.lower()} {len(to_relocate)} texture(s) "
                f"into {dest_dir}"
                + (f" ({len(in_place)} already there)" if in_place else ""),
                items=self._plan_lines(
                    f"{os.path.basename(p)}  ←  {os.path.dirname(p)}"
                    for p in to_relocate
                ),
            )
        elif in_place:
            self._log(
                f"Dry run — all {len(in_place)} texture(s) are already at the "
                "destination; only the stored paths would change."
            )

        if changed:
            self._log(
                f"Would repath {len(changed)} file node(s)",
                items=self._plan_lines(
                    f"{node}:  {old}  →  {new}" for node, old, new in changed
                ),
            )
        unchanged = len(remap) - len(changed)
        if unchanged:
            self._log(f"{unchanged} node(s) already point at the destination.")

        self._report_lightmap_plan(lightmap_plan, dest_dir, verb)

        self._log(
            f"Nothing has been written — press Apply to {verb.lower()} and "
            "repath exactly this.",
            "warning",
        )
        return True

    @classmethod
    def _plan_lines(cls, lines):
        """*lines* capped at :attr:`_PLAN_PREVIEW_ROWS`, with the remainder counted.

        A truncated listing that does not SAY it was truncated reads as the whole
        plan, which is the one thing a preview must never do.
        """
        listed = list(lines)
        if len(listed) <= cls._PLAN_PREVIEW_ROWS:
            return listed
        hidden = len(listed) - cls._PLAN_PREVIEW_ROWS
        return listed[: cls._PLAN_PREVIEW_ROWS] + [f"… and {hidden} more"]

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

    def _plan_lightmaps(self, lightmaps, source_dir, dest_dir, relocate_mode):
        """The records' dry run over *lightmaps*, or ``None`` when none are in scope."""
        if not lightmaps:
            return None
        try:
            return self._lightmap_records().relocate_lightmaps(
                dest_dir,
                source_dir=source_dir,
                mode=relocate_mode,
                maps=self._lightmap_maps(lightmaps),
                dry_run=True,
            )
        except Exception as e:  # noqa: BLE001 -- a preview must not raise
            self._log(f"Lightmaps not planned: {e}", "error")
            return None

    def _report_lightmap_plan(self, plan, dest_dir, verb) -> None:
        """The dry-run lines for the lightmaps -- same shape as the texture ones."""
        if not plan:
            return
        if plan["relocate"]:
            self._log(
                f"Dry run — would {verb.lower()} {len(plan['relocate'])} "
                f"lightmap(s) into {dest_dir} and repoint their bake markers"
                + (
                    f" ({len(plan['in_place'])} already there)"
                    if plan["in_place"]
                    else ""
                ),
                items=self._plan_lines(
                    f"{os.path.basename(src)}  ←  {os.path.dirname(src)}"
                    for src, _dst in plan["relocate"]
                ),
            )
        elif plan["in_place"]:
            self._log(
                f"Dry run — all {len(plan['in_place'])} lightmap(s) are already "
                "at the destination; only the bake markers would change."
            )
        if plan["missing"]:
            self._log(
                f"{len(plan['missing'])} lightmap(s) found nowhere — their "
                "markers would keep pointing at the recorded folder",
                "warning",
                items=[
                    f"{dep['map']}  (recorded in {dep['dir'] or '<no folder>'})"
                    + (f"  {dep['note']}" if dep.get("note") else "")
                    for dep in plan["missing"]
                ],
            )

    def _relocate_lightmaps(self, lightmaps, source_dir, dest_dir, relocate_mode):
        """Relocate *lightmaps* for real and report; returns whether any landed."""
        try:
            result = self._lightmap_records().relocate_lightmaps(
                dest_dir,
                source_dir=source_dir,
                mode=relocate_mode,
                maps=self._lightmap_maps(lightmaps),
            )
        except Exception as e:  # noqa: BLE001 -- the textures already landed
            self._log(f"Lightmaps not relocated: {e}", "error")
            return False
        landed = len(result["copied"]) + len(result["in_place"])
        if landed:
            self._log(
                f"Lightmaps — {len(result['copied'])} relocated, "
                f"{len(result['in_place'])} already at destination; "
                f"{result['updated']} bake marker(s) repointed, manifest "
                "republished.",
                "success",
            )
        failed = len(result["relocate"]) - len(result["copied"])
        if failed:
            self._log(
                f"{failed} lightmap(s) did not copy — see the script editor.",
                "warning",
            )
        if result["missing"]:
            self._log(
                f"{len(result['missing'])} lightmap(s) found nowhere — the "
                "export's path check will fail on them",
                "warning",
                items=[
                    f"{dep['map']}  (recorded in {dep['dir'] or '<no folder>'})"
                    + (f"  {dep['note']}" if dep.get("note") else "")
                    for dep in result["missing"]
                ],
            )
        return bool(landed)

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
