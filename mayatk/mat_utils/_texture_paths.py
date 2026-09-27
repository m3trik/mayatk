# !/usr/bin/python
# coding=utf-8
"""Texture paths behind :class:`mayatk.MatUtils`.

What a stored ``fileTextureName`` denotes: env-var, workspace and project-rule
resolution in Maya's own order; the tile / frame tokens (the one vocabulary,
``ptk.TiledPath``) collapsed to a representative or expanded to the whole set;
the UV-tiling mode a tile path names; the absolute and project-relative forms
and the round trip between them; and the texture paths a scope holds.
Reached through :class:`mayatk.MatUtils`; nothing here is called directly.
"""

import os
from typing import List, Tuple, Dict, Any, Optional

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

import pythontk as ptk

from mayatk.env_utils._env_utils import EnvUtils


class _TexturePathsInternal:
    """Private helpers and ``MatUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _expand_texture_path(path: str) -> str:
        """Expand a stored ``fileTextureName`` the way Maya itself does.

        Environment variables first, then ``workspace -expandName``, which
        resolves a relative value against the **project root** — the rule
        folder is part of the stored value (``sourceimages/tex.png``), not
        something to join on top of it.

        Returns an absolute path whether or not the file exists, so a broken
        link is still reported at the location Maya looks in. '' for empty
        input.
        """
        if not path:
            return ""
        expanded = os.path.expandvars(path)
        if os.path.isabs(expanded):
            return expanded
        try:
            return cmds.workspace(expandName=expanded) or expanded
        except Exception:
            return expanded

    @classmethod
    def _absolute_texture_path(cls, file_path: str, sourceimages: str) -> str:
        """Absolute on-disk path for one raw ``fileTextureName`` value.

        Resolution proper is :meth:`MatUtils.resolve_path` with ``search=False``
        — env vars, the :attr:`_PATH_TOKENS` tile/frame tokens, and
        ``workspace -expandName``, which resolves a relative value against the
        **project root**. ``search=False`` on
        purpose: the repair hunt's basename match would silently retarget this
        at a same-named file the node does not point at, and callers here go on
        to *overwrite* what they are handed.

        The one extra step is an existence-checked ``sourceimages`` join, for
        values stored relative to the texture folder rather than the root. It
        must stay a **fallback**: applied first (as it was), a value already
        carrying the rule folder doubles it —
        ``<root>/sourceimages/sourceimages/tex.png`` — a path that exists
        nowhere, so every consumer (converter scopes, the Marmoset/Substance
        manifests, the sourceimages copier) silently dropped the texture as
        missing.

        Unresolvable values come back as Maya's own expansion rather than ''
        so callers can report *which* path failed.
        """
        if not file_path:
            return ""
        resolved = cls.resolve_path(file_path, search=False)
        if resolved:
            return os.path.abspath(resolved)
        if sourceimages and not os.path.isabs(file_path):
            fallback = os.path.join(sourceimages, file_path)
            if cls._texture_exists(fallback):
                return os.path.abspath(fallback)
        return os.path.abspath(cls._expand_texture_path(file_path))

    # ------------------------------------------------------------------
    # File-node path tokens — ONE table, every consumer
    # ------------------------------------------------------------------
    #: Every token Maya can leave in a ``fileTextureName`` -> ``(stand-in,
    #: glob)``: the one table, :attr:`pythontk.TiledPath.TOKENS` (the
    #: vocabulary both DCC packages and ``ptk.MapFactory`` read). Kept under
    #: this name for the callers below.
    #:
    #: ONE sanctioned copy of these globs exists, in the blender_bridge worker
    #: templates (``_import_scene.py`` / ``_import_scene_usd.py``
    #: ``_TILE_TOKENS``): those exec inside Blender against whatever pythontk
    #: it has installed. They carry ``<UDIM>``/``<UVTILE>`` only, and the
    #: values agree with this table -- change one, check the other.
    _PATH_TOKENS: Dict[str, Tuple[Optional[str], str]] = ptk.TiledPath.TOKENS

    _PATH_TOKEN_RE = ptk.TiledPath.TOKEN_RE

    @classmethod
    def probe_texture_path(cls, path: str) -> Optional[str]:
        """The one concrete file *path*'s tile/frame pattern denotes, or None.

        The public counterpart to :meth:`resolve_path`, which deliberately
        keeps the token so a caller can write it back. A caller that has to
        TOUCH the file -- an existence probe, a size read, an FBX write-time
        check -- needs the token collapsed instead, and every one that rolled
        its own collapsed only ``<UDIM>``, so ``<uvtile>`` / ``<u>_<v>`` /
        ``<f>`` / ``<frame>`` paths were misreported as missing or as a
        working-directory problem.

        The token vocabulary lives in one table (``ptk.TiledPath.TOKENS``);
        this is ``ptk.TiledPath.representative`` under the name Maya callers
        know.

        Fixed tokens are substituted from :attr:`_PATH_TOKENS`. A glob-only
        token (``<f>``) makes the whole path a glob pattern instead — every
        literal segment escaped, so a folder named ``sh[ot]_01`` cannot
        silently swallow the match — and the first hit in sorted order is
        returned.

        A FIXED token prefers its stand-in (``1001``) but does not require
        it: when that tile is not on disk the set's first real tile is
        returned instead, so a set running 1002-1005 is not reported missing
        by everything built on this. Nothing on disk at all still comes back
        as the stand-in, which is the honest "where it was looked for".

        Returns:
            str|None: The probe path. A token-free path comes back unchanged
            (existing or not -- this resolves the pattern, it does not judge
            it), so ``probe == path`` is also how a caller tells "no token"
            from "token". None means an empty input, or a frame pattern with
            nothing on disk.
        """
        return ptk.TiledPath.representative(path)

    @classmethod
    def has_path_token(cls, path: str) -> bool:
        """Does *path* carry a tile/frame token — i.e. does it name a SET?

        The classifier that belongs beside :meth:`probe_texture_path` (the
        representative) and :meth:`texture_tiles` (the set): every caller that
        has to branch on "one file or many" was asking it privately, and each
        listed its own subset of the vocabulary — the Scene Exporter matched
        three of the six, so a ``<u>_<v>`` or ``<frame>`` node resolved
        upstream and then arrived UNTILED, past the representative collapse
        and out unclassified; the Texture Path Editor tested the literal
        string ``"<udim>"``, so every other spelling read as a plain path.

        A pure string test over the one table — no disk access, so a frame
        pattern with nothing on disk is still a token path (which is the
        answer a caller wanting "can I treat this as ONE file?" needs).

        Parameters:
            path: A stored texture path, or just its basename.
        """
        return ptk.TiledPath.has_token(path)

    @classmethod
    def is_frame_sequence(cls, path: str) -> bool:
        """Does *path*'s token count FRAMES (``<f>`` / ``<frame>``), not tiles?

        The split a cost has to make: a UDIM / UV-tile set is loaded whole, a
        frame sequence one frame at a time, so :meth:`texture_tiles` answers
        with every file of both but only a tile set's count multiplies memory.
        The same table (:attr:`_PATH_TOKENS`) decides: a token with no fixed
        stand-in is a frame counter. A pure string test.

        Parameters:
            path: A stored texture path, or just its basename.
        """
        return ptk.TiledPath.is_frame_sequence(path)

    @classmethod
    def token_wildcard(cls, path: str, wildcard: Optional[str] = "*") -> str:
        """*path* with every tile/frame token replaced by *wildcard*.

        The name-matching companion to :meth:`texture_tiles`: that one builds
        a filesystem glob (per-token vocabulary, literals escaped) to find the
        files; this one produces a plain pattern for matching NAMES already in
        hand — an ``fnmatch`` over an index, a display string — where there is
        no directory to escape and no disk to touch.

        It exists so the vocabulary stays in one place. Three separate
        ``re.compile(r"<udim>|<f>|<uvtile>")`` literals had accumulated across
        the exporter and this module, each listing half the table, so whether
        a ``<u>_<v>`` or ``<frame>`` path counted as a tile set depended on
        which copy happened to ask.

        Parameters:
            path: A stored texture path, or just its basename.
            wildcard: What each token becomes. ``"*"`` suits ``fnmatch``.
                ``None`` substitutes each token's OWN glob vocabulary from
                :attr:`_PATH_TOKENS` (``<UDIM>`` -> four digits, ``<uvtile>``
                -> ``u*_v*``) and glob-escapes the literal segments, so a
                ``fnmatch`` over names is exactly as strict as the disk glob
                :meth:`texture_tiles` runs -- ``rock.<UDIM>.png`` never
                collects ``rock.thumb.png``.
        """
        return ptk.TiledPath.wildcard(path, wildcard)

    @classmethod
    def texture_tiles(cls, path: str) -> List[str]:
        """Every file on disk *path*'s tile/frame pattern denotes, sorted.

        The SET counterpart of :meth:`probe_texture_path`, which answers with
        the single representative an existence / size / hash check needs. A
        caller that has to MOVE a texture needs all of them, and each one that
        rolled its own glob substituted only ``<UDIM>`` -- so a ``<uvtile>`` /
        ``<u>_<v>`` / ``<f>`` set relocated nothing while its node was
        repathed to the destination regardless, landing on a folder holding no
        tile of that name (the Texture Path Editor's Set Texture Directory,
        reported 2026-08-25).

        Each token globs by its OWN vocabulary (the glob half of
        :attr:`_PATH_TOKENS`), so a ``<UDIM>`` pattern never collects
        ``<uvtile>``-spelled tiles sitting beside it. A token-free path yields
        itself when it is on disk and nothing when it is not, so the list
        doubles as the existence verdict and a caller never has to ask twice.
        Literal segments are glob-escaped -- a folder named ``sh[ot]_01``
        cannot swallow the match -- which the ad-hoc ``sub("*")`` globs could
        not claim, since they escaped nothing and pasted an unescaped
        directory in front.

        This is the answer to "is the source there?" for a caller that needs
        the WHOLE set -- anything about to MOVE or REBIND a texture asks here.
        :meth:`_texture_exists` answers the narrower "is any of it there?"
        through :meth:`probe_texture_path`, which prefers the fixed stand-in
        (``1001``) so a staged representative keeps naming the same file. The
        two agreed on nothing but 1001-based sets until 2026-08-25, when the
        probe gained this method as its fallback: a set running 1002-1005 read
        as MISSING through the probe while reading as present here, which is
        how the Scene Exporter came to reject textures this panel called fine.

        Parameters:
            path: A stored texture path, absolute or already resolved. Tokens
                come from the one table (:attr:`_PATH_TOKENS`).

        Returns:
            list: Forward-slashed paths, sorted; empty when nothing matches.
        """
        return ptk.TiledPath.tiles(path)

    @classmethod
    def apply_uv_tiling(cls, file_nodes) -> List[str]:
        """Switch each file node reading ONE tile of a set to that set's tiling mode.

        A node given a real tile (``rock.1001.png``) renders that tile alone until
        ``uvTilingMode`` names the scheme; once set, Maya computes the pattern and
        finds every sibling tile itself (measured: a ``.1001`` path at UDIM reads
        back as ``.<UDIM>``). The scheme comes from the file name's tile token
        (``ptk.MapFactory.get_tile_token``), classified by the one tile
        vocabulary (``ptk.TiledPath.scheme``): a four-digit tile or ``<UDIM>``
        is UDIM (Mari); a ``u#_v#`` tile is 0-based (ZBrush) when either index
        is 0 and 1-based (Mudbox) otherwise, as a ``<UVTILE>`` or ``<u>_<v>``
        placeholder is (the table's stand-in for both is ``u1_v1``). Modes are
        matched by Maya's LABEL, not by enum index. A node is left as it is when
        its name carries no tile token, when it is already tiling, or when its
        tile is the only one on disk (``ptk.MapFactory.get_tile_paths``): a lone
        tile is one image, and tiled, a ``.1024`` map leaves 0-1 UVs for tile
        1024 and renders black, where untiled it renders wherever the UVs sit.

        Parameters:
            file_nodes: ``file`` node names.

        Returns:
            list: The nodes whose mode was set.
        """
        labels: Optional[List[str]] = None
        changed: List[str] = []
        for node in ptk.make_iterable(file_nodes):
            if not cmds.attributeQuery("uvTilingMode", node=node, exists=True):
                continue
            if cmds.getAttr(f"{node}.uvTilingMode"):
                continue  # already tiling: that choice stands
            stored = cmds.getAttr(f"{node}.fileTextureName") or ""
            spelled = ptk.MapFactory.get_tile_token(stored).lstrip("._").lower()
            if not spelled:
                continue
            resolved = cls.resolve_path(stored, search=False)
            if not resolved or len(ptk.MapFactory.get_tile_paths(resolved)) < 2:
                continue  # one tile alone is one image, not a set
            if ptk.TiledPath.scheme(spelled) == "uvtile":
                # Maya splits the UV-tile family by where numbering starts; only
                # a concrete ``u#_v#`` says, and a placeholder is 1-based.
                indices = spelled[1:].split("_v", 1) if spelled[0] == "u" else ()
                scheme = "0-based" if "0" in indices else "1-based"
            else:
                scheme = "UDIM"
            if labels is None:
                labels = cmds.attributeQuery("uvTilingMode", node=node, listEnum=True)[
                    0
                ].split(":")
            index = next((i for i, text in enumerate(labels) if scheme in text), None)
            if index is None:
                continue
            cmds.setAttr(f"{node}.uvTilingMode", index)
            changed.append(node)
        return changed

    @classmethod
    def _texture_exists(cls, path: str) -> bool:
        """``os.path.exists`` for a stored path, tile/frame tokens resolved.

        The shared validity primitive behind :meth:`MatUtils.resolve_path`
        (both the ``search=False`` verdict and the repair hunt),
        :meth:`_absolute_texture_path` and the exporter's ``check_valid_paths``
        — which is why it resolves via the :attr:`_PATH_TOKENS` table rather
        than the single case-sensitive ``"<UDIM>"`` substitution it used to do.

        Agrees with :meth:`texture_tiles` on whether a SET is there: the probe
        prefers the fixed stand-in but falls back to the set's first real tile,
        so a set that does not start at ``1001`` is no longer missing here and
        present there.
        """
        probe = cls.probe_texture_path(path)
        return bool(probe) and os.path.exists(probe)

    @classmethod
    def _paths_from_file_nodes(
        cls, file_nodes: List[Any], absolute: bool = False
    ) -> List[str]:
        project_sourceimages = EnvUtils.get_env_info("sourceimages")
        project_sourceimages = (
            os.path.abspath(project_sourceimages) if project_sourceimages else ""
        )
        sourceimages_name = (
            os.path.basename(project_sourceimages).replace("\\", "/")
            if project_sourceimages
            else ""
        )

        textures: List[str] = []
        for node in file_nodes or []:
            try:
                file_path = cmds.getAttr(f"{node}.fileTextureName")
            except Exception:
                continue
            if not file_path:
                continue
            file_path = file_path.replace("\\", "/")

            if not project_sourceimages:
                textures.append(file_path)
                continue

            abs_path = cls._absolute_texture_path(file_path, project_sourceimages)

            if absolute:
                textures.append(abs_path)
                continue

            if os.path.normcase(abs_path).startswith(
                os.path.normcase(project_sourceimages) + os.sep
            ):
                rel_path = os.path.relpath(abs_path, project_sourceimages).replace(
                    "\\", "/"
                )
                if sourceimages_name and not rel_path.startswith(
                    sourceimages_name + "/"
                ):
                    rel_path = f"{sourceimages_name}/{rel_path}"
                textures.append(rel_path)
            else:
                textures.append(abs_path)

        return textures

    @staticmethod
    def _filenames_from_file_nodes(file_nodes: List[Any]) -> List[str]:
        filenames: List[str] = []
        for node in file_nodes or []:
            try:
                file_path = cmds.getAttr(f"{node}.fileTextureName")
            except Exception:
                continue
            if not file_path:
                continue
            filenames.append(os.path.basename(file_path))
        return filenames

    @classmethod
    def _resolve_path(cls, path, search):
        """Body of :meth:`MatUtils.resolve_path`."""
        if not path:
            return None

        check_exists = cls._texture_exists

        expanded = os.path.expandvars(path)
        if check_exists(expanded):
            return expanded

        # Maya resolves a relative ``.ftn`` against the project ROOT first and
        # the ``sourceImages`` FILE RULE second; ``to_absolute`` is the single
        # place that encodes that order. This was a bare
        # ``cmds.workspace(expandName=...)``, which only ever prefixes the ROOT
        # — so a RULE-relative path (``foo.png``, the durable form this module
        # emitted from 2026-08-18) resolved nowhere, and every consumer of this
        # verdict called a texture Maya loads fine "missing": the exporter's
        # ``check_valid_paths`` reported it, and ``resolve_invalid_texture_paths``
        # rebound the node by basename onto an absolute path, undoing the
        # panel's normalization on every export.
        try:
            ws_path = cls.to_absolute(path)
            if ws_path and check_exists(ws_path):
                return ws_path
        except Exception:
            pass

        # ``expandName`` is kept as a last resort rather than replaced: it is
        # Maya's own resolver, and anything it knows that the converter does
        # not stays resolvable. It runs only when the tiers above have already
        # failed, so the common path still costs one lookup.
        try:
            ws_path = cmds.workspace(expandName=path)
            if check_exists(ws_path):
                return ws_path
        except Exception:
            pass

        if not search:
            return None

        try:
            # The texture folder is whatever the ``sourceImages`` file rule
            # names -- it can be ``textures/`` or an absolute path outside the
            # project. Hardcoding ``<root>/sourceimages`` meant the repair hunt
            # looked in a folder such a project doesn't have, so nothing was
            # ever found there.
            source_images = EnvUtils.source_images_dir()
            if not source_images:
                return None

            si_path = os.path.join(source_images, path)
            if check_exists(si_path):
                return si_path

            basename = os.path.basename(path)
            si_basename_path = os.path.join(source_images, basename)
            if check_exists(si_basename_path):
                return si_basename_path
        except Exception:
            pass

        return None

    @staticmethod
    def _is_bundled_texture(path):
        """Body of :meth:`MatUtils.is_bundled_texture`."""
        install = EnvUtils.get_env_info("install_path")
        if not (install and path):
            return False
        try:
            return os.path.normcase(os.path.abspath(path)).startswith(
                os.path.normcase(os.path.abspath(install)) + os.sep
            )
        except (TypeError, ValueError):  # unresolvable path — not ours to claim
            return False

    @classmethod
    def _get_texture_paths(
        cls, objects, materials, file_nodes, texture_names, absolute, exclude_bundled
    ):
        """Body of :meth:`MatUtils.get_texture_paths`."""
        # ``_resolve_texture_targets`` already guards the scene fallback
        # against scoped queries (objects/materials/file_nodes); we only
        # need to additionally suppress it when the caller passed
        # ``texture_names`` as their sole scope.
        targets = cls._resolve_texture_targets(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            fallback_to_scene=(texture_names is None),
            as_strings=True,
        )
        paths = cls._paths_from_file_nodes(targets["file_nodes"], absolute=absolute)
        if texture_names:
            paths.extend(texture_names)
        # Filtered after ``texture_names`` are folded in, so an explicitly
        # passed path is judged by the same rule as a discovered one.
        if exclude_bundled:
            paths = [p for p in paths if not cls.is_bundled_texture(p)]
        return list(dict.fromkeys(p for p in paths if p))

    @staticmethod
    def _collect_material_paths(
        materials, attributes, inc_mat_name, inc_path_type, resolve_full_path
    ):
        """Body of :meth:`MatUtils.collect_material_paths`."""
        if materials is None:
            materials = cmds.ls(mat=True) or []
        else:
            materials = [str(m) for m in materials]
            materials = cmds.ls(materials, mat=True) or []

        attributes = attributes or ["fileTextureName"]

        material_paths = []
        try:
            project_sourceimages = os.path.abspath(
                EnvUtils.get_env_info("sourceimages")
            )
        except Exception:
            project_sourceimages = ""

        sourceimages_name = (
            os.path.basename(project_sourceimages).replace("\\", "/")
            if project_sourceimages
            else "sourceimages"
        )

        for material in materials:
            file_nodes = cmds.listConnections(material, type="file") or []
            for attr in attributes:
                for file_node in file_nodes:
                    if not cmds.attributeQuery(attr, node=file_node, exists=True):
                        continue

                    file_path = cmds.getAttr(f"{file_node}.{attr}")
                    if not file_path:
                        continue

                    file_path = file_path.replace("\\", "/")

                    if project_sourceimages:
                        abs_file_path = (
                            os.path.abspath(
                                os.path.join(project_sourceimages, file_path)
                            )
                            if not os.path.isabs(file_path)
                            else os.path.abspath(file_path)
                        )

                        path_type = (
                            "Relative"
                            if abs_file_path.startswith(project_sourceimages)
                            else "Absolute"
                        )
                    else:
                        abs_file_path = os.path.abspath(file_path)
                        path_type = "Absolute"

                    if path_type == "Relative":
                        rel_path = os.path.relpath(
                            abs_file_path, project_sourceimages
                        ).replace("\\", "/")
                        if not rel_path.startswith(sourceimages_name + "/"):
                            rel_path = f"{sourceimages_name}/{rel_path}"
                        path_out = abs_file_path if resolve_full_path else rel_path
                    else:
                        path_out = abs_file_path

                    entry = (path_out,)
                    if inc_mat_name:
                        entry = (material,) + entry
                    if inc_path_type:
                        entry = entry[:1] + (path_type,) + entry[1:]

                    material_paths.append(entry)

        return material_paths

    @classmethod
    def _to_absolute(cls, path, workspace, sourceimages):
        """Body of :meth:`MatUtils.to_absolute`."""
        if not path:
            return ""
        path = os.path.expandvars(path)
        # ``splitdrive`` too, so a drive-relative ``C:foo.png`` is not joined
        # onto the root — the same classification the engine makes.
        if os.path.isabs(path) or os.path.splitdrive(path)[0]:
            return os.path.normpath(path).replace("\\", "/")

        if workspace is None:
            workspace = EnvUtils.get_env_info("workspace") or ""
        if sourceimages is None:
            # The rule belongs to the project, so no root means no rule to
            # resolve through — an explicit ``workspace=""`` says "don't fall
            # back to the live project" and that has to bind BOTH lookups, or
            # the caller who ruled the project out still gets its sourceimages.
            sourceimages = (
                (EnvUtils.get_env_info("sourceimages") or "") if workspace else ""
            )

        root_form = os.path.normpath(
            os.path.join(workspace, path) if workspace else path
        ).replace("\\", "/")
        if workspace and cls.texture_tiles(root_form):
            return root_form
        if sourceimages:
            rule_form = os.path.normpath(os.path.join(sourceimages, path)).replace(
                "\\", "/"
            )
            if cls.texture_tiles(rule_form):
                return rule_form
        return root_form

    @classmethod
    def _to_project_relative(cls, path, workspace, sourceimages):
        """Body of :meth:`MatUtils.to_project_relative`."""
        if workspace is None:
            workspace = EnvUtils.get_env_info("workspace") or ""
        if sourceimages is None:
            # Unlike ``to_absolute``, an empty workspace does NOT suppress
            # this: the rule is what the preferred form is built against, and
            # a project-less scene still relativizes against it.
            sourceimages = EnvUtils.get_env_info("sourceimages") or ""
        norm = os.path.normpath(path).replace("\\", "/")

        # Under the project ROOT — the form Maya resolves first and spells
        # itself. Guarded by the round trip, which is what keeps a nested rule
        # honest: ``assets/sourceimages/foo.png`` must come back as this exact
        # file, never as whatever the RULE branch of ``to_absolute`` would find
        # under the same relative name. A texture not yet ON DISK still passes
        # — ``to_absolute`` falls back to the root form — which Set Directory
        # depends on: it plans the relative form in phase 1 and only copies in
        # phase 2, so a strict existence gate would store an absolute path for
        # every texture about to land in sourceimages.
        if workspace and ptk.FileUtils.is_under(norm, workspace, inclusive=False):
            rel = os.path.relpath(norm, workspace).replace("\\", "/")
            absolute = cls.to_absolute(rel, workspace, sourceimages)
            if os.path.normcase(absolute) == os.path.normcase(norm):
                return rel

        # Outside the root, but under an out-of-root ``sourceImages`` rule:
        # the rule-relative form is the only relative one that finds it.
        if sourceimages and ptk.FileUtils.is_under(norm, sourceimages, inclusive=False):
            rel = os.path.relpath(norm, sourceimages).replace("\\", "/")
            shadow = (
                os.path.normpath(os.path.join(workspace, rel)).replace("\\", "/")
                if workspace
                else ""
            )
            # Only a shadow that is a DIFFERENT file ON DISK disqualifies the
            # form. The identity test is for a rule pointing AT the root
            # (``workspace -fr "sourceImages" "."``), where the "shadow" IS
            # the texture; the disk test is because a texture not yet staged
            # has no shadow to lose to.
            if not (
                shadow
                and os.path.normcase(shadow) != os.path.normcase(norm)
                and cls.texture_tiles(shadow)
            ):
                return rel
        return norm
