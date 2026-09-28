# !/usr/bin/python
# coding=utf-8
"""Texture files on disk behind :class:`mayatk.MatUtils`.

Finding textures under a folder, staging them into ``sourceimages`` with
project-relative paths, copying / moving / migrating them and rebinding their
``file`` nodes, reloading them, and telling two files apart by CONTENT (size +
partial hash) rather than by name -- the guard every collision decision here
rests on.
Reached through :class:`mayatk.MatUtils`; nothing here is called directly.
"""

import hashlib
import os
from typing import List, Tuple, Dict, Any, Optional

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None

import pythontk as ptk

from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.env_utils._env_utils import EnvUtils


class _TextureFilesInternal:
    """Private helpers and ``MatUtils`` method bodies; see the module docstring."""

    @classmethod
    def _texture_content_id(cls, path: str) -> Optional[tuple]:
        """(size, partial-hash) identity of the file behind *path*.

        Resolves the way Maya does (env vars + workspace, then any tile/frame
        token collapsed to its probe file via
        :meth:`probe_texture_path`) and hashes the first and last 64 KB —
        enough to tell same-named different-content textures apart without
        reading multi-hundred-MB maps whole.  None when the file doesn't
        resolve on disk.
        """
        resolved = cls.resolve_path(path, search=False)
        if not resolved:
            return None
        probe = cls.probe_texture_path(resolved)
        if not probe:
            return None
        try:
            size = os.path.getsize(probe)
            h = hashlib.md5()
            with open(probe, "rb") as f:
                h.update(f.read(65536))
                if size > 131072:
                    f.seek(-65536, os.SEEK_END)
                    h.update(f.read(65536))
            return (size, h.hexdigest())
        except OSError:
            return None

    @classmethod
    def _textures_identical(cls, path_a: str, path_b: str) -> bool:
        """True when two stored texture paths denote the same image CONTENT.

        Same normalized path is trivially identical; different paths are
        identical only when both resolve and their (size, partial-hash) ids
        match — the consolidation case (external copy vs sourceimages copy)
        the loose basename fingerprint exists for, minus its false positives.
        """

        def norm(p):
            return os.path.normcase(os.path.normpath(p))

        if norm(path_a) == norm(path_b):
            return True
        id_a = cls._texture_content_id(path_a)
        return id_a is not None and id_a == cls._texture_content_id(path_b)

    @staticmethod
    def _unique_ordered(nodes: List[Any]) -> List[Any]:
        """Return nodes with original order preserved and duplicates removed."""
        ordered = []
        seen = set()
        for node in nodes or []:
            if not node:
                continue
            if node in seen:
                continue
            ordered.append(node)
            seen.add(node)
        return ordered

    @classmethod
    def _remap_file_nodes(
        cls, file_paths, target_dir, silent, limit_to_nodes, as_strings
    ):
        """Body of :meth:`MatUtils.remap_file_nodes`."""
        sourceimages_dir = EnvUtils.get_env_info("sourceimages")
        sourceimages_dir_norm = os.path.normpath(sourceimages_dir).replace("\\", "/")

        if limit_to_nodes:
            node_names = cls._to_strs(limit_to_nodes)
            nodes_to_process = cmds.ls(node_names, type="file") or []
        else:
            nodes_to_process = cmds.ls(type="file") or []

        file_nodes: Dict[str, List[str]] = {}

        for fn in nodes_to_process:
            try:
                file_path = cmds.getAttr(f"{fn}.fileTextureName")
            except Exception:
                continue

            if not file_path:
                continue

            file_path_norm = os.path.normpath(file_path).replace("\\", "/")

            key = None
            if ptk.FileUtils.is_under(file_path_norm, sourceimages_dir_norm):
                key = (
                    os.path.relpath(file_path_norm, sourceimages_dir_norm)
                    .replace("\\", "/")
                    .lower()
                )
            else:
                key = os.path.basename(file_path_norm).lower()

            if key:
                file_nodes.setdefault(key, []).append(fn)

        remapped_nodes: List[str] = []
        remap_data = ptk.remap_file_paths(file_paths, target_dir, sourceimages_dir)

        for key, new_full_path, maya_path in remap_data:
            if key in file_nodes:
                for fn_name in file_nodes[key]:
                    current_val = cmds.getAttr(f"{fn_name}.fileTextureName")
                    if current_val != maya_path:
                        if not silent:
                            print("\n[Remap Attempt]")
                            print(f"  original path: {new_full_path}")
                            print(f"  lookup key:    {key}")
                            print(f"  maya path:     {maya_path}")
                            print(f"  remapped:      {fn_name}")

                        Attributes.set_plug_literal(
                            f"{fn_name}.fileTextureName", maya_path
                        )
                        remapped_nodes.append(fn_name)
            else:
                if not silent:
                    cmds.warning(
                        f"// Skipping: No file node found for key '{key}' (original: {new_full_path})"
                    )
        return remapped_nodes

    @classmethod
    def _remap_texture_paths(
        cls, materials, new_dir, silent, file_nodes, objects, as_strings
    ):
        """Body of :meth:`MatUtils.remap_texture_paths`."""
        new_dir = new_dir or EnvUtils.get_env_info("sourceimages")
        if not new_dir or not os.path.isdir(new_dir):
            cmds.warning(f"Invalid directory: {new_dir}")
            return

        scope = cls._resolve_texture_targets(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            fallback_to_scene=True,
            as_strings=True,
        )
        resolved_nodes = scope["file_nodes"]

        if not resolved_nodes:
            cmds.warning("No valid file nodes found to remap.")
            return

        textures = cls._paths_from_file_nodes(resolved_nodes)
        if not textures:
            cmds.warning("No valid texture paths found.")
            return

        remapped_nodes = cls.remap_file_nodes(
            file_paths=textures,
            target_dir=new_dir,
            silent=silent,
            limit_to_nodes=resolved_nodes,
        )
        if not silent:
            print(
                f"// Result: Remapped {len(remapped_nodes)}/{len(textures)} texture paths."
            )

    @classmethod
    def _stage_textures_relative(cls, file_nodes, sourceimages, external_mode, scope):
        """Body of :meth:`MatUtils.stage_textures_relative`."""
        import shutil

        if external_mode not in ("copy", "move", "skip"):
            raise ValueError(
                f"Unknown external_mode {external_mode!r}; "
                "expected 'copy', 'move' or 'skip'."
            )
        if scope not in ("sourceimages", "project"):
            raise ValueError(
                f"Unknown scope {scope!r}; expected 'sourceimages' or 'project'."
            )

        results: Dict[str, str] = {}
        src_dir = sourceimages or EnvUtils.get_env_info("sourceimages")
        if not src_dir:
            cmds.warning("No sourceimages directory resolved — nothing staged.")
            return {n: "skipped:no-sourceimages" for n in file_nodes}
        src_dir = os.path.normpath(src_dir)
        os.makedirs(src_dir, exist_ok=True)

        # Resolved for BOTH scopes: the root is what the emitted form is
        # relative to, what decides whether an already-relative path is in
        # that form or the legacy rule-relative one, and whether a namesake
        # there would shadow the out-of-root fallback. Only
        # ``scope="project"`` REQUIRES one (it is also the set of paths it
        # relativizes).
        workspace = EnvUtils.get_env_info("workspace") or ""
        stage_resolvable = True
        if scope == "project":
            if not workspace:
                cmds.warning("Project workspace is not set — nothing relativized.")
                return {n: "skipped:no-workspace" for n in file_nodes}
            # Does staging into sourceimages actually buy a relative form? If
            # not, the relocation would buy nothing but a broken rebind, and
            # externals stay on their absolute paths (warned per node below).
            # This USED to be the out-of-project-rule case: an absolute
            # ``sourceImages`` rule had no ROOT-relative form that found it.
            # The rule-relative form does find it — measured resolving and
            # surviving two save/open generations with an absolute rule
            # outside the project — so what is left here is the residual case
            # where the form is refused anyway (a same-named file at the
            # project root would shadow it). Kept as the question rather than
            # the answer: the converter owns which forms exist.
            stage_resolvable = cls.to_project_relative(
                os.path.join(src_dir, "probe.png"), workspace, src_dir
            ) != os.path.normpath(os.path.join(src_dir, "probe.png")).replace("\\", "/")

        _MAX_VARIANTS = 99

        def _variant_name(basename: str, index: int) -> str:
            """``rock_Base_Color.png`` → ``rock_1_Base_Color.png``.

            The index goes on the BASE NAME, never after the map-type token: a
            suffix appended last hides that token from every consumer of the
            taxonomy (``rock_Base_Color_1`` ends in ``_1``, not in any alias),
            so the map classifies as nothing and is silently left unwired.
            ``MapFactory.get_base_texture_name`` is what decides where the base
            ends — the same rule the resolver reads the type by, so the two
            cannot disagree. A name with no map suffix has no token to protect
            and keeps the plain ``<stem>_N`` form.

            Tile tokens ride in the remainder, so a token pattern and its tiles
            still transform identically (``rock.<UDIM>.png`` →
            ``rock_1.<UDIM>.png``, ``rock.1001.png`` → ``rock_1.1001.png``) and
            the stored path expands onto exactly the tiles staged.
            """
            if not index:
                return basename
            stem, ext = os.path.splitext(basename)
            base = ptk.MapFactory.get_base_texture_name(stem)
            if base and base != stem and stem.startswith(base):
                return f"{base}_{index}{stem[len(base) :]}{ext}"
            return f"{stem}_{index}{ext}"

        def _dst_for(src: str, index: int) -> str:
            """Where *src* lands in sourceimages at variant *index*."""
            return os.path.join(src_dir, _variant_name(os.path.basename(src), index))

        def _slot_fits(sources: List[str], index: int) -> bool:
            """True when every source may occupy *index*: the destination is
            free, is the source itself, or holds provably identical content —
            the reuse case that keeps repeat exports from stacking variants.

            ``_textures_identical`` is the shared rule, and its refusal to call
            two *unreadable* files a match is load-bearing here: a locked file
            or a cloud placeholder that won't hydrate (sourceimages on a synced
            drive is routine) yields no content id, and treating one unknown as
            equal to another would rebind the node to the wrong texture — the
            exact failure this whole guard exists to prevent.
            """
            for src in sources:
                dst = _dst_for(src, index)
                if os.path.exists(dst) and not cls._textures_identical(src, dst):
                    return False
            return True

        # Every sourceimages file some ``file`` node in the scene still reads.
        # Built once, and only if a variant is actually taken -- the clean path
        # never pays for the scan.
        scene_refs: Optional[set] = None

        #: Stored path -> staged relative form, MOVE mode only. A second node
        #: storing the SAME external path is routine (duplicated file nodes),
        #: and after the first node's move the source is gone -- re-checking
        #: the disk would misread "shared" as "missing" and strand the second
        #: node absolute on a deleted file. It rebinds to the twin the first
        #: move staged instead.
        moved_this_run: Dict[str, str] = {}

        def _unread_by_scene(path: str) -> bool:
            """True when no ``file`` node in the scene reads *path*.

            A resident file nothing reads is almost always the PREVIOUS export
            of the same texture, which is worth saying out loud: the ``_N``
            copy staged beside it is otherwise indistinguishable from a real
            clash between two different textures, and quietly accumulates.
            Token patterns expand to the tiles they stand for, or every UDIM
            set would read as unreferenced.
            """
            nonlocal scene_refs
            if scene_refs is None:
                scene_refs = set()
                for stored in cls._paths_from_file_nodes(
                    cmds.ls(type="file") or [], absolute=True
                ):
                    # ``or [stored]``: a referenced file that is MISSING still
                    # counts as read by the scene — dropping it would let the
                    # staging call its name free.
                    for tile in cls.texture_tiles(stored) or [stored]:
                        scene_refs.add(os.path.normcase(os.path.abspath(tile)))
            return os.path.normcase(os.path.abspath(path)) not in scene_refs

        def _store_relative(node: str, rel_form: str) -> bool:
            """Returns False when the plug can't be written (locked /
            referenced) — a per-node skip, never a task abort."""
            plug_name = f"{node}.fileTextureName"
            try:
                # Undo anchor + literal write past the file node's auto-expand.
                # One home for that trap (MatSnapshot.restore_network needs the
                # same thing when it puts a relativized path back).
                Attributes.set_plug_literal(plug_name, rel_form)
                return True
            except RuntimeError as e:
                cmds.warning(f"Cannot write '{plug_name}' (locked/referenced?): {e}")
                return False

        for node in file_nodes:
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                results[node] = "skipped:no-attribute"
                continue
            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                results[node] = "skipped:empty-path"
                continue

            expanded = os.path.expandvars(path)
            if not (os.path.isabs(expanded) or os.path.splitdrive(expanded)[0]):
                # Relative — but not necessarily in the form this module
                # emits. The legacy RULE-relative ``foo.png`` (2026-08-18 to
                # 2026-08-25) resolves in Maya but names no folder, and the
                # FBX plug-in — which resolves against the process CWD, not
                # the workspace — cannot locate it at write time, so it is
                # upgraded in place to ``sourceimages/foo.png`` rather than
                # reported as a form it is not. A path resolving to nothing is
                # left alone: that is Resolve Missing Textures' job, not this
                # one's.
                resolved = cls.to_absolute(expanded, workspace, src_dir)
                upgraded = cls.to_project_relative(resolved, workspace, src_dir)
                if (
                    upgraded != expanded
                    and not os.path.isabs(upgraded)
                    and cls.texture_tiles(resolved)
                ):
                    _store_relative(node, upgraded)
                    results[node] = "relativized"
                else:
                    results[node] = "already-relative"
                continue

            norm = os.path.normpath(expanded)
            if scope == "project":
                # Anything under the project ROOT is portable where it sits and
                # relativizes in place — through the round-trip-guarded
                # converter, never a hardcoded ``sourceimages/`` prefix (a
                # nested ``sourceImages`` rule like ``assets/sourceimages``
                # needs the full root-relative form). A file under an
                # OUT-OF-ROOT sourceimages rule is NOT in-project: it falls
                # through to the external handling below, where
                # ``stage_resolvable`` already refused relocation.
                if ptk.FileUtils.is_under(norm, workspace, inclusive=False):
                    if not cls.texture_tiles(norm):
                        # Same rule the external branch applies below, asked
                        # the same way (the TILES, never the 1001 probe): a
                        # rewrite whose result names nothing buys no
                        # portability and reports a red row as a success.
                        # Resolve Missing Textures is the command for this.
                        results[node] = "skipped:missing-source"
                        continue
                    rel_form = cls.to_project_relative(norm, workspace, src_dir)
                    if rel_form == os.path.normpath(norm).replace("\\", "/"):
                        # The round-trip guard refused it (no resolvable form).
                        results[node] = "skipped:no-relative-form"
                        continue
                    _store_relative(node, rel_form)
                    results[node] = "relativized"
                    continue
            elif ptk.FileUtils.is_under(norm, src_dir):
                # Inside sourceimages — relativize in place, subfolders kept.
                if not cls.texture_tiles(norm):
                    results[node] = "skipped:missing-source"
                    continue
                # Through the primitive, not a hardcoded ``sourceimages/``
                # prefix: that spelling names nothing under a NESTED rule
                # (``assets/sourceimages``), which the converter spells in
                # full. One definition of "relative" for both scopes.
                _store_relative(node, cls.to_project_relative(norm, workspace, src_dir))
                results[node] = "relativized"
                continue

            if external_mode == "skip":
                # Consolidation declined: the node keeps its absolute path, so
                # the external link still resolves. Relativizing without the
                # copy would point at a file that isn't under sourceimages and
                # silently break the material on import — the one outcome worse
                # than an absolute path.
                results[node] = "skipped:external"
                continue

            if not stage_resolvable:
                cmds.warning(
                    f"'{node}': sourceimages lies outside the project root, so "
                    "a staged copy would have no working relative form — the "
                    "absolute path is kept."
                )
                results[node] = "skipped:external"
                continue

            # External — stage the file(s) into the sourceimages root first.
            basename = os.path.basename(norm)
            sources = cls.texture_tiles(norm)

            if not sources:
                moved_rel = moved_this_run.get(os.path.normcase(norm))
                if moved_rel is not None:
                    _store_relative(node, moved_rel)
                    results[node] = "moved+relativized"
                    continue
                results[node] = "skipped:missing-source"
                continue

            # Claim a destination name the node can own outright: its own, or
            # the first `_N` variant whose slot is free or already holds this
            # exact content (so a repeat export converges instead of stacking
            # _1, _2, _3 …).
            index = next(
                (i for i in range(_MAX_VARIANTS + 1) if _slot_fits(sources, i)), None
            )
            if index is None:
                cmds.warning(
                    f"Not staging '{norm}': every '{basename}' slot through "
                    f"_{_MAX_VARIANTS} is held by different content — '{node}' "
                    "keeps its absolute path."
                )
                results[node] = "skipped:name-collision"
                continue

            copy_failed = False
            for src in sources:
                dst = _dst_for(src, index)
                if os.path.exists(dst):
                    # Either src IS the destination, or the slot search proved
                    # the resident file is this same texture.
                    continue
                try:
                    shutil.copy2(src, dst)
                except OSError as e:
                    cmds.warning(f"Copy failed for '{src}': {e}")
                    copy_failed = True
                    break

            if copy_failed:
                results[node] = "skipped:copy-failed"
                continue

            if external_mode == "move":
                # Every tile's staged twin is in place, so the external
                # original is redundant — including when an identical-content
                # resident made the copy itself unnecessary. A removal failure
                # downgrades the move to a copy, loudly, and never blocks the
                # rebind: the staged file is what the node reads from here on.
                for src in sources:
                    dst = _dst_for(src, index)
                    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(
                        os.path.abspath(dst)
                    ):
                        continue
                    try:
                        os.remove(src)
                    except OSError as e:
                        cmds.warning(
                            f"Move: staged copy of '{src}' is in place but the "
                            f"original could not be removed (kept): {e}"
                        )

            staged = _variant_name(basename, index)
            if index:
                # Name the residents that pushed it down, and whether the scene
                # still reads them. "Stale" is reported, never acted on: this
                # scene not reading a file is no proof another scene in the
                # project doesn't, and overwriting it would be unrecoverable
                # where an extra file is not.
                blocked = (_dst_for(sources[0], i) for i in range(index))
                stale = [
                    os.path.basename(d)
                    for d in blocked
                    if os.path.exists(d) and _unread_by_scene(d)
                ]
                if stale:
                    cmds.warning(
                        f"'{node}' staged as '{staged}': {', '.join(stale)} "
                        f"already hold that name in sourceimages, and NOTHING in "
                        f"this scene reads them — most likely earlier exports of "
                        f"this same texture. Delete them (once you have checked "
                        f"no other scene uses them) and re-run to get "
                        f"'{basename}' back."
                    )
                else:
                    cmds.warning(
                        f"A DIFFERENT '{basename}' already exists in sourceimages "
                        f"and is still in use — '{node}' staged as '{staged}' "
                        "rather than being rebound to the wrong file (check "
                        "whether the two are really distinct)."
                    )
            # Relative form of the staged file — for ``scope="project"``
            # ``stage_resolvable`` above already proved one exists. Both scopes
            # go through the primitive: the literal ``sourceimages/`` prefix
            # this used to paste on names nothing under a nested rule, and Maya
            # expands it back to absolute on the next load.
            rel_form = cls.to_project_relative(
                os.path.join(src_dir, staged), workspace, src_dir
            )
            _store_relative(node, rel_form)
            if external_mode == "move":
                moved_this_run[os.path.normcase(norm)] = rel_form
            if index:
                results[node] = "variant+relativized"
            else:
                results[node] = (
                    "moved+relativized"
                    if external_mode == "move"
                    else "copied+relativized"
                )

        return results

    @classmethod
    def _reload_textures(
        cls,
        materials,
        inc,
        exc,
        log,
        refresh_viewport,
        refresh_hypershade,
        texture_types,
    ):
        """Body of :meth:`MatUtils.reload_textures`."""
        if texture_types is None:
            texture_types = ["file", "aiImage", "pxrTexture", "imagePlane"]

        if materials is None:
            materials = cmds.ls(mat=True) or []
        else:
            materials = cmds.ls(cls._to_strs(materials), mat=True) or []

        file_nodes: List[str] = []
        for material in materials:
            history = cmds.listHistory(material, pruneDagObjects=True) or []
            for tex_type in texture_types:
                file_nodes.extend(cmds.ls(history, type=tex_type) or [])

        file_nodes = list(set(file_nodes))

        if inc or exc:
            file_nodes = ptk.filter_list(
                file_nodes,
                inc=inc,
                exc=exc,
                map_func=lambda fn: cmds.getAttr(f"{fn}.fileTextureName"),
            )

        for fn in file_nodes:
            try:
                file_path = cmds.getAttr(f"{fn}.fileTextureName")
                # The write-back is what forces the re-read from disk, so it
                # stays a plain setAttr — but setAttr EXPANDS a resolvable
                # relative path to absolute, so reloading a relativized scene
                # used to flatten every path it touched. Put the string back
                # verbatim when that happened.
                cmds.setAttr(f"{fn}.fileTextureName", file_path, type="string")
                if cmds.getAttr(f"{fn}.fileTextureName") != file_path:
                    Attributes.set_plug_literal(f"{fn}.fileTextureName", file_path)
                if log:
                    print(f"Reloaded texture: {file_path}")
            except Exception:
                if log:
                    print(f"Skipped non-file node: {fn}")

        if refresh_viewport:
            cmds.refresh(force=True)

        if refresh_hypershade:
            cmds.refreshEditorTemplates()
            mel.eval(
                'hypershadePanelMenuCommand("hyperShadePanel1", "refreshAllSwatches");'
            )

    @classmethod
    def _move_texture_files(
        cls,
        found_files,
        new_dir,
        delete_old,
        create_dir,
        per_file_timeout,
        max_workers,
        progress_callback,
    ):
        """Body of :meth:`MatUtils.move_texture_files`."""
        import shutil
        from concurrent.futures import (
            ThreadPoolExecutor,
            as_completed,
            TimeoutError as FuturesTimeout,
        )

        if not found_files:
            cmds.warning("No texture files provided for moving.")
            return []

        if create_dir:
            os.makedirs(new_dir, exist_ok=True)

        src_entries = []
        for entry in found_files:
            if isinstance(entry, tuple):
                dir_path, filename = entry
                src_path = os.path.join(dir_path, filename).replace("\\", "/")
            else:
                src_path = entry.replace("\\", "/")
                filename = os.path.basename(src_path)

            if not os.path.isfile(src_path):
                cmds.warning(f"Source file does not exist: {src_path}")
                continue
            src_entries.append((src_path, filename))

        if not src_entries:
            return []

        def _copy_one(src_path, filename):
            """``(src, dst, status)`` -- ``"copied"``, ``"uptodate"`` or ``"collision"``.

            The Texture Path Editor's documented collision policy, applied by
            the primitive itself (Set Directory had its own copy of it; this
            one silently overwrote until 2026-08-26): a destination already
            holding a SAME-SIZE file of that name is reused without rewriting
            -- hundreds of files the user already copied would otherwise make a
            cloud-sync client re-hash and re-upload every one -- and one holding
            a DIFFERENT-SIZE file is refused, in either mode: overwriting it
            would destroy an external, and the caller's repath would then bind
            to bytes it never chose. Same size is the equivalence test, as the
            policy states; the warning is raised by the caller on the main
            thread -- ``cmds`` is not thread-safe.
            """
            dst_path = os.path.join(new_dir, filename)
            if ptk.FileUtils.is_same_file(src_path, dst_path):
                # The destination IS the source, however it is spelled -- a
                # junction, a subst or mapped drive naming the same folder.
                # Nothing to copy, and nothing to delete: in Move mode the
                # "redundant source" below was the only copy.
                return src_path, dst_path, "uptodate"
            if os.path.exists(dst_path):
                # Size is a cheap NEGATIVE only. Two different textures of the
                # same name routinely share a byte count -- uncompressed TGA/DDS/
                # EXR/BMP at a fixed resolution always do -- and in Move mode the
                # short-circuit DELETES the source, so equality decided on size
                # alone destroys the artist's only copy and rebinds the node to a
                # different image, reported as 'already up-to-date'. Disk work sits
                # outside the undo chunk, so there is nothing to undo.
                try:
                    same_size = os.path.getsize(src_path) == os.path.getsize(dst_path)
                except OSError:
                    same_size = None  # unreadable stat -- let the copy raise
                if same_size is False:
                    return src_path, dst_path, "collision"
                if same_size:
                    # Same size: now prove the CONTENT matches before anything is
                    # removed. _textures_identical is the class's own check (size +
                    # first/last 64 KB md5) and is what stage_textures_relative
                    # already uses. Safe off the main thread here: both paths are
                    # known-existing absolutes, so resolve_path returns on its first
                    # tier and never reaches cmds.workspace.
                    if cls._textures_identical(src_path, dst_path):
                        if delete_old:
                            os.remove(src_path)  # proven identical; src redundant
                        return src_path, dst_path, "uptodate"
                    return src_path, dst_path, "collision"
            shutil.copy2(src_path, dst_path)
            if delete_old:
                os.remove(src_path)
            return src_path, dst_path, "copied"

        workers = max(1, min(max_workers, len(src_entries)))
        copied: List[Tuple[str, str]] = []
        skipped = 0
        collisions: List[Tuple[str, str]] = []
        errors = []
        timed_out = []
        cancelled = False

        # Explicit executor management — a `with` block would call
        # shutdown(wait=True) on exit, which defeats the timeout by
        # blocking the main thread on stuck workers. On the cancelled
        # path we shutdown(wait=False) so Maya gets the UI back even if
        # a copy is permanently wedged inside the filesystem driver.
        executor = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {
                executor.submit(_copy_one, src, fn): src for src, fn in src_entries
            }
            total = len(futures)
            done = 0
            for future in as_completed(futures):
                src = futures[future]
                try:
                    src_p, dst_p, status = future.result(timeout=per_file_timeout)
                    if status == "collision":
                        collisions.append((src_p, dst_p))
                    else:
                        copied.append((src_p, dst_p))
                        if status == "uptodate":
                            skipped += 1
                except FuturesTimeout:
                    timed_out.append(src)
                    cmds.warning(
                        f"Copy timed out after {per_file_timeout:.0f}s "
                        f"on {src}; abandoning remaining workers."
                    )
                    cancelled = True
                except Exception as e:
                    errors.append((src, e))

                done += 1
                if progress_callback is not None and not cancelled:
                    try:
                        keep_going = progress_callback(
                            done, total, os.path.basename(src)
                        )
                    except Exception:
                        keep_going = True
                    if not keep_going:
                        cancelled = True

                if cancelled:
                    for f in futures:
                        f.cancel()  # only cancels not-yet-started futures
                    break
        finally:
            # cancel_futures=True drops anything not yet started.
            # wait=not cancelled: normal completion drains workers cleanly;
            # cancelled path returns immediately, leaking any wedged threads.
            executor.shutdown(wait=not cancelled, cancel_futures=True)

        for src_path, dst_path in copied:
            print(f"// Copied: {src_path} -> {dst_path}")
            if delete_old:
                print(f"// Deleted original: {src_path}")
        for src_path, err in errors:
            cmds.warning(f"// Failed to copy {src_path}: {err}")
        for src_path, dst_path in collisions:
            cmds.warning(
                f"// '{os.path.basename(dst_path)}' already exists at the destination "
                f"with a different size; skipped to avoid a wrong-file rebind: {dst_path}"
            )

        print(
            f"// Result: {len(copied)} texture(s) ok "
            f"({skipped} already up-to-date, "
            f"{len(collisions)} skipped on a different-size collision, "
            f"{len(errors)} errors, "
            f"{len(timed_out)} timed out"
            f"{', cancelled' if cancelled and not timed_out else ''})."
        )
        return copied

    @classmethod
    def _copy_textures_to_sourceimages(
        cls, objects, materials, file_nodes, sourceimages_dir, delete_old
    ):
        """Body of :meth:`MatUtils.copy_textures_to_sourceimages`."""
        sourceimages_dir = sourceimages_dir or EnvUtils.get_env_info("sourceimages")
        if not sourceimages_dir:
            cmds.warning("sourceimages directory is not set; cannot copy textures.")
            return []
        si_abs = os.path.abspath(sourceimages_dir).replace("\\", "/")

        scope = cls._resolve_texture_targets(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            fallback_to_scene=True,
            as_strings=True,
        )
        resolved_nodes = scope["file_nodes"]
        if not resolved_nodes:
            return []

        # Absolute on-disk paths for the resolved file nodes.
        paths = cls._paths_from_file_nodes(resolved_nodes, absolute=True)

        to_copy: List[str] = []
        claimed: Dict[str, str] = {}  # dst basename (lower) -> the source chosen for it
        for path in paths:
            norm = os.path.normpath(path).replace("\\", "/")
            if cls._PATH_TOKEN_RE.search(norm):
                continue  # multi-tile/frame token — no single file to copy
            if not os.path.isfile(norm):
                continue  # missing on disk — resolve_invalid_texture_paths handles this
            # is_under normalizes separators on both sides; the `+ "/"` compare
            # this replaces only ever matched the forward-slashed spelling, so a
            # backslash path read as external and was re-copied into the root.
            if ptk.FileUtils.is_under(norm, si_abs):
                continue  # already under sourceimages

            base = os.path.basename(norm)
            base_key = base.lower()
            dst = os.path.join(si_abs, base).replace("\\", "/")

            # Same-basename collision — against a file already in sourceimages
            # OR against another external already queued for the same basename
            # (the copy is flat, so both would land on one destination). Size
            # is a cheap proxy for "same file" (matches the texture-path
            # editor's policy): same size → the relative path resolves to the
            # single copy, nothing more to do; different size → refuse to
            # clobber / rebind to the wrong texture.
            rival = dst if os.path.exists(dst) else claimed.get(base_key)
            if rival:
                try:
                    same = os.path.getsize(norm) == os.path.getsize(rival)
                except OSError:
                    same = False
                if not same:
                    cmds.warning(
                        f"'{base}' resolves to more than one texture of differing "
                        f"size; keeping the first and skipping '{norm}' to avoid a "
                        f"wrong-file rebind."
                    )
                continue

            claimed[base_key] = norm
            to_copy.append(norm)

        if not to_copy:
            return []

        return cls.move_texture_files(to_copy, si_abs, delete_old=delete_old)

    @classmethod
    def _find_texture_files(
        cls,
        objects,
        source_dir,
        recursive,
        return_dir,
        quiet,
        file_nodes,
        materials,
        progress_callback,
        filenames,
    ):
        """Body of :meth:`MatUtils.find_texture_files`."""
        if not os.path.isdir(source_dir):
            cmds.warning(f"Invalid source directory: {source_dir}")
            return []

        if file_nodes and not objects and not materials:
            texture_nodes = cls._to_strs(file_nodes)
        elif objects or materials or file_nodes:
            scope = cls._resolve_texture_targets(
                objects=objects,
                materials=materials,
                file_nodes=file_nodes,
                fallback_to_scene=False,
                as_strings=True,
            )
            texture_nodes = scope["file_nodes"]
        else:
            texture_nodes = []

        if not texture_nodes and not filenames:
            cmds.warning(
                "No objects, materials, or file nodes provided to find textures."
            )
            return []

        import fnmatch

        names: List[str] = []
        for node_name in texture_nodes:
            try:
                path = cmds.getAttr(f"{node_name}.fileTextureName")
            except Exception:
                continue
            if path:
                names.append(os.path.basename(path))
        names.extend(os.path.basename(str(n)) for n in (filenames or []) if n)

        target_filenames = set()
        token_patterns = []
        for filename in names:
            lower_name = filename.lower()
            if not lower_name:
                continue
            if cls.has_path_token(lower_name):
                token_patterns.append(cls.token_wildcard(lower_name, None).lower())
            else:
                target_filenames.add(lower_name)

        if not target_filenames and not token_patterns:
            cmds.warning("No texture names available for lookup.")
            return []

        results = []

        # Never into a folder of stale copies -- sync caches, the OS's trash and
        # volume folders, VCS, bytecode, ``_superseded`` -- compared without
        # case: the one pruning every texture walk shares with blendertk's
        # (``ptk.FileDependencies.walk``).
        for root, _dirs, files in ptk.FileDependencies.walk(source_dir):
            if progress_callback:
                progress_callback(len(results), 0, f"Scanning: {root}")

            for file in files:
                lower_file = file.lower()
                matched = lower_file in target_filenames
                if not matched and token_patterns:
                    matched = any(
                        fnmatch.fnmatchcase(lower_file, p) for p in token_patterns
                    )
                if matched:
                    full_path = os.path.join(root, file).replace("\\", "/")
                    if return_dir:
                        results.append((root.replace("\\", "/"), file))
                    else:
                        results.append(full_path)

            if not recursive:
                break

        if not quiet:
            print("\n[Texture Files Found]")
            if return_dir:
                max_dir_len = max(len(d) for d, _ in results) if results else 0
                for dir_path, filename in results:
                    print(f"  {dir_path.ljust(max_dir_len)}  {filename}")
            else:
                for filepath in results:
                    print(f"  {filepath}")
        return results

    @classmethod
    def _migrate_textures(
        cls,
        materials,
        old_dir,
        new_dir,
        silent,
        delete_old,
        objects,
        file_nodes,
        progress_callback,
    ):
        """Body of :meth:`MatUtils.migrate_textures`."""
        for label, path in (("old_dir", old_dir), ("new_dir", new_dir)):
            if not path or not os.path.exists(path) or not os.path.isdir(path):
                cmds.warning(f"{label} is invalid: {path}")
                return

        scope = cls._resolve_texture_targets(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            fallback_to_scene=False,
        )
        resolved_nodes = scope["file_nodes"]
        if not resolved_nodes:
            cmds.warning("No file nodes found for migration.")
            return

        filenames = cls._unique_ordered(cls._filenames_from_file_nodes(resolved_nodes))
        if not filenames:
            cmds.warning("No texture names available for migration.")
            return

        found_files = [(old_dir, filename) for filename in filenames]

        cls.move_texture_files(
            found_files=found_files,
            new_dir=new_dir,
            delete_old=delete_old,
            create_dir=True,
            progress_callback=progress_callback,
        )

        if found_files:
            cls.remap_file_nodes(
                file_paths=[os.path.join(old_dir, filename) for filename in filenames],
                target_dir=new_dir,
                silent=silent,
                limit_to_nodes=resolved_nodes,
            )

    @classmethod
    def _move_unused_textures(cls, source_dir, output_dir):
        """Body of :meth:`MatUtils.move_unused_textures`."""
        import shutil

        project_sourceimages = source_dir or EnvUtils.get_env_info("sourceimages")
        unused_folder = output_dir or os.path.join(project_sourceimages, "unused")

        if not os.path.exists(unused_folder):
            os.makedirs(unused_folder)

        all_textures = {
            file
            for file in os.listdir(project_sourceimages)
            if os.path.isfile(os.path.join(project_sourceimages, file))
        }
        used_textures = {
            os.path.basename(path[0]) for path in cls.collect_material_paths()
        }

        unused_textures = all_textures - used_textures

        print(f"Moving {len(unused_textures)} to: {output_dir} ..")
        for texture in unused_textures:
            src_path = os.path.join(project_sourceimages, texture)
            dest_path = os.path.join(unused_folder, texture)
            shutil.move(src_path, dest_path)
            print(f"Moved {texture} to {unused_folder}")
