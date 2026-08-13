# !/usr/bin/python
# coding=utf-8
import hashlib
import os
import re
from typing import List, Tuple, Union, Dict, Any, Optional, Callable

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    cmds = None
    mel = None
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils

from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils._env_utils import EnvUtils

# Directory names pruned during recursive texture searches. Keeps the walk
# off cloud-sync caches, Windows system folders, version control
# noise, and Python bytecode caches — all of which can hold stale duplicates
# of legitimate textures that would otherwise pollute the candidate set.
_TEXTURE_WALK_SKIP_DIRS = frozenset(
    {
        ".dropbox.cache",
        ".dropbox",
        "$RECYCLE.BIN",
        "System Volume Information",
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "__pycache__",
    }
)


class _MatUtilsInternal(ptk.HelpMixin):
    """Internal helper utilities shared across MatUtils operations."""

    @staticmethod
    def _is_clash_variant(candidate: str, want: str) -> bool:
        """True when *candidate* is *want* modulo Maya's clash-rename digit suffix."""
        if candidate == want:
            return True
        return candidate.startswith(want) and candidate[len(want) :].isdigit()

    # UV-placement attrs that change how a texture reads on the surface —
    # the axes the duplicate-material fingerprint is blind to.
    _PLACE2D_SIG_ATTRS = (
        "repeatU",
        "repeatV",
        "offsetU",
        "offsetV",
        "rotateUV",
        "mirrorU",
        "mirrorV",
        "wrapU",
        "wrapV",
        "stagger",
        "coverageU",
        "coverageV",
        "translateFrameU",
        "translateFrameV",
        "rotateFrame",
    )

    @classmethod
    def _placement_signature(cls, file_node: str) -> tuple:
        """UV-placement signature of the place2dTexture feeding *file_node*.

        Two file nodes reading the same image with different tiling/offset
        produce visually different materials — this is what lets the
        duplicate verifier tell a shared atlas apart from a true duplicate.
        Empty tuple when no place2d is connected (two bare nodes match).
        """
        p2d = (
            cmds.listConnections(
                file_node, source=True, destination=False, type="place2dTexture"
            )
            or []
        )
        if not p2d:
            return ()
        sig = []
        for attr in cls._PLACE2D_SIG_ATTRS:
            try:
                sig.append(round(cmds.getAttr(f"{p2d[0]}.{attr}"), 5))
            except Exception:
                sig.append(None)
        return tuple(sig)

    @staticmethod
    def _texture_content_id(path: str) -> Optional[tuple]:
        """(size, partial-hash) identity of the file behind *path*.

        Resolves the way Maya does (env vars + workspace, ``<UDIM>``
        collapsed to the 1001 probe tile) and hashes the first and last
        64 KB — enough to tell same-named different-content textures apart
        without reading multi-hundred-MB maps whole.  None when the file
        doesn't resolve on disk.
        """
        resolved = MatUtils.resolve_path(path, search=False)
        if not resolved:
            return None
        probe = (
            resolved.replace("<UDIM>", "1001") if "<UDIM>" in resolved else resolved
        )
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

    @classmethod
    def _materials_are_verified_duplicates(
        cls, mat_a: str, mat_b: str, slots_a: dict, slots_b: dict
    ) -> bool:
        """Pairwise proof that two fingerprint-matched materials are truly
        interchangeable: equal unconnected scalar attribute values, and per
        texture slot identical placement, color space, and image content.

        The fingerprint is a cheap GROUPING heuristic (node type + attr →
        basename); this gate is what makes feeding the result to a
        destructive merge safe.  Conservative by design: anything that can't
        be positively verified fails the pair.
        """
        # 1. Unconnected scalar attributes — generic: both materials are the
        #    same nodeType, so the attribute lists are identical.  Connected
        #    plugs are skipped (their value is texture-driven; the slot check
        #    below owns those).
        for attr in cmds.listAttr(mat_a, settable=True, scalar=True) or []:
            plug_a, plug_b = f"{mat_a}.{attr}", f"{mat_b}.{attr}"
            try:
                if cmds.listConnections(
                    plug_a, source=True, destination=False
                ) or cmds.listConnections(plug_b, source=True, destination=False):
                    continue
                va, vb = cmds.getAttr(plug_a), cmds.getAttr(plug_b)
            except Exception:
                continue  # multi/message/unreadable plug — not comparable
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                if abs(va - vb) > 1e-5:
                    return False
            elif va != vb:
                return False

        # 2. Per-slot texture verification.
        if set(slots_a) != set(slots_b):
            return False

        def profile(nodes):
            out = []
            for n in nodes:
                path = cmds.getAttr(f"{n}.fileTextureName") or ""
                try:
                    cspace = cmds.getAttr(f"{n}.colorSpace")
                except Exception:
                    cspace = None
                out.append((path, cspace, cls._placement_signature(n)))
            return sorted(out, key=repr)

        for slot, nodes_a in slots_a.items():
            nodes_b = slots_b[slot]
            if len(nodes_a) != len(nodes_b):
                return False
            for (pa, ca, sa), (pb, cb, sb) in zip(profile(nodes_a), profile(nodes_b)):
                if sa != sb or ca != cb or not cls._textures_identical(pa, pb):
                    return False
        return True

    @classmethod
    def _find_opacity_map_on_disk(
        cls, mat: str, dir_cache: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Optional[str]:
        """Path to an Opacity map sitting beside the material's other textures.

        Covers the common case where the set ships an opacity map that never
        made it into the shading network — the material was built before the
        map existed, or from a shader graph with no opacity slot.

        Parameters:
            mat (str): Material node.
            dir_cache (dict): Scan cache, ``{directory: {base name: path}}``.
                Scene-wide callers should pass one in — every material in a set
                shares a texture folder, and the scan is a full directory walk.
        """
        bases_by_dir: Dict[str, set] = {}
        for file_node in cmds.ls(cmds.listHistory(mat) or [], type="file") or []:
            raw = cmds.getAttr(f"{file_node}.fileTextureName") or ""
            # search=False: we want the folder Maya actually reads this texture
            # from — the basename hunt could point us at an unrelated same-named
            # file in some other set's folder.
            path = cls.resolve_path(raw, search=False) or raw
            directory = os.path.dirname(path)
            if not directory or not os.path.isdir(directory):
                continue
            base = ptk.MapFactory.get_base_texture_name(path)
            if base:
                bases_by_dir.setdefault(directory, set()).add(base)

        cache = {} if dir_cache is None else dir_cache
        for directory, bases in bases_by_dir.items():
            if directory not in cache:
                # base name → the set's Opacity map, one scan per folder.
                found: Dict[str, str] = {}
                for entry in sorted(os.listdir(directory)):
                    full = os.path.join(directory, entry)
                    if not os.path.isfile(full):
                        continue
                    if ptk.MapFactory.resolve_map_type(full) != "Opacity":
                        continue
                    base = ptk.MapFactory.get_base_texture_name(full)
                    if base:
                        found.setdefault(base, full)
                cache[directory] = found

            for base in bases:
                if base in cache[directory]:
                    return cache[directory][base]
        return None

    @staticmethod
    def _slot_inputs(node: str, attr: str) -> List[str]:
        """Source plugs driving `attr` or any of its channel children."""
        found: List[str] = []
        for plug in [attr] + [f"{attr}{s}" for s in ("R", "G", "B", "X", "Y", "Z")]:
            if not cmds.attributeQuery(plug, node=node, exists=True):
                continue
            found += (
                cmds.listConnections(
                    f"{node}.{plug}", source=True, destination=False, plugs=True
                )
                or []
            )
        return found

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
    def _resolve_texture_targets(
        cls,
        objects: Optional[List[Any]] = None,
        materials: Optional[List[Any]] = None,
        file_nodes: Optional[List[Any]] = None,
        fallback_to_scene: bool = False,
        as_strings: bool = False,
    ) -> Dict[str, List[Any]]:
        """Normalize objects/materials/file nodes for texture operations.

        The ``fallback_to_scene`` flag means "return every ``file`` node in
        the scene when the caller passed *no scope at all*". An empty list
        (``objects=[]``) counts as "user explicitly scoped to nothing" —
        the caller gets an empty result, never the entire scene.
        """

        def to_long(nodes):
            if not nodes:
                return []
            names = _MatUtilsInternal._to_strs(nodes)
            return cmds.ls(names, long=True, flatten=True) or []

        no_scope_passed = objects is None and materials is None and file_nodes is None

        resolved_objects = to_long(objects) if objects else []

        resolved_materials_set = set()
        if materials:
            mats = cmds.ls(to_long(materials), mat=True, long=True) or []
            resolved_materials_set.update(mats)

        if resolved_objects:
            found_mats = cls.get_mats(resolved_objects, as_strings=True)
            resolved_materials_set.update(found_mats)

        resolved_materials = sorted(list(resolved_materials_set))

        resolved_file_nodes_set = set()

        if resolved_materials:
            history = cmds.listHistory(resolved_materials, pruneDagObjects=True) or []
            files = cmds.ls(history, type="file") or []
            resolved_file_nodes_set.update(files)

        if file_nodes:
            files = cmds.ls(to_long(file_nodes), type="file", long=True) or []
            resolved_file_nodes_set.update(files)

        if fallback_to_scene and no_scope_passed and not resolved_file_nodes_set:
            files = cmds.ls(type="file", long=True) or []
            resolved_file_nodes_set.update(files)

        # All return values are now plain string names — the previous
        # ``as_strings=False`` path used to wrap in ``str``; with the
        # callers must consume strings.
        return {
            "objects": resolved_objects,
            "materials": resolved_materials,
            "file_nodes": sorted(list(resolved_file_nodes_set)),
        }

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
        — env vars, ``<UDIM>``, and ``workspace -expandName``, which resolves a
        relative value against the **project root**. ``search=False`` on
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

    @staticmethod
    def _texture_exists(path: str) -> bool:
        """``os.path.exists`` with ``<UDIM>`` resolved to its first tile."""
        return bool(path) and os.path.exists(path.replace("<UDIM>", "1001"))

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

    @staticmethod
    def get_texture_file_node(material, attr_name, _depth=0):
        """Locate the file texture node feeding a material attribute."""
        if _depth > 10 or not material or not attr_name:
            return None

        full_attr = f"{material}.{attr_name}"
        if not cmds.objExists(full_attr):
            return None

        files = cmds.listConnections(
            full_attr, source=True, destination=False, type="file"
        )
        if files:
            return files[0]

        sources = cmds.listConnections(full_attr, source=True, destination=False)
        if sources:
            node = sources[0]
            ntype = cmds.nodeType(node)

            _FOLLOW = {
                "bump2d": ["bumpValue"],
                "aiNormalMap": ["input"],
                "projection": ["image"],
                "stencil": ["image"],
                "gammaCorrect": ["value"],
                "luminance": ["value"],
                "reverse": ["input"],
                "clamp": ["input"],
                "colorCorrect": ["color", "inColor", "input"],
                "aiColorCorrect": ["input"],
                "remapHsv": ["color", "inColor"],
                "remapColor": ["color", "inColor"],
                "remapValue": ["inputValue", "color"],
            }

            candidates = _FOLLOW.get(ntype, ["input", "color", "inColor"])
            for inp in candidates:
                if cmds.objExists(f"{node}.{inp}"):
                    result = _MatUtilsInternal.get_texture_file_node(
                        node, inp, _depth + 1
                    )
                    if result:
                        return result

        # LAST resort: a PACKED map is wired per channel, into the compound's
        # CHILD plugs -- `outColorG -> TEX_roughness_mapX/Y/Z` -- and
        # `listConnections` on the parent reports none of them. Measured on a
        # production room whose one ORM feeds three StingrayPBS slots: only the
        # AO slot took the whole `outColor`, so only AO resolved,
        # `_read_metallic_roughness` refused an occlusion-only entry
        # (correctly), and the GLB shipped FBX2glTF's scalar fallback --
        # roughness flat 0.329 and metallic flat 0.0 against a source carrying
        # 223 and 256 distinct values.
        #
        # Deliberately AFTER the parent's own follow-chain rather than before
        # it. Maya does NOT forbid a compound and its children being connected
        # at once -- probe-measured on 2025: connecting a child after the parent
        # is ALLOWED and both survive -- so ordering is a real decision, not a
        # moot one. The parent binding is the primary one and keeps precedence;
        # descending only once it has yielded nothing makes this purely additive
        # to every path that already worked. Recursing per child rather than
        # querying inline reuses the whole resolution path, so a packed map
        # behind a colorCorrect resolves the same way a plain one does.
        try:
            children = (
                cmds.attributeQuery(attr_name, node=material, listChildren=True) or []
            )
        except (RuntimeError, TypeError):
            children = []
        for child in children:
            result = _MatUtilsInternal.get_texture_file_node(
                material, child, _depth + 1
            )
            if result:
                return result

        return None

    @staticmethod
    def _create_standard_shader(name=None, color=None, return_type="type"):
        """Create or get the preferred shader type, with optional node creation."""
        try:
            if cmds.pluginInfo("mtoa", query=True, loaded=True) or cmds.nodeType(
                "standardSurface", isTypeName=True
            ):
                shader_type = "standardSurface"
            else:
                try:
                    test = cmds.shadingNode("standardSurface", asShader=True)
                    cmds.delete(test)
                    shader_type = "standardSurface"
                except Exception:
                    shader_type = "lambert"
        except Exception:
            shader_type = "lambert"

        if return_type == "type":
            return shader_type

        shader_name = name or f"material_{shader_type}"
        shader = cmds.shadingNode(shader_type, asShader=True, name=shader_name)

        if color:
            color_attr = "baseColor" if shader_type == "standardSurface" else "color"
            cmds.setAttr(
                f"{shader}.{color_attr}", color[0], color[1], color[2], type="double3"
            )

        if return_type == "shader":
            return shader

        sg_name = f"{shader_name}_SG" if name else f"{shader}_SG"
        sg = cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name=sg_name,
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)

        if return_type == "shading_group":
            return sg
        elif return_type == "both":
            return shader, sg
        else:
            raise ValueError(
                f"Invalid return_type: {return_type}. Must be 'type', 'shader', 'shading_group', or 'both'."
            )

    @staticmethod
    def _classification_tokens(node) -> List[str]:
        """Role classifications of *node* (an instance, not a type name).

        Resolves the node's type and delegates to
        :meth:`NodeUtils.get_classification_tokens`, which owns the parsing.
        """
        try:
            node_type = cmds.nodeType(str(node))
        except Exception:
            return []
        return NodeUtils.get_classification_tokens(node_type)

    @staticmethod
    def _has_role(tokens, *prefixes: str) -> bool:
        """Whether any classification token starts with one of *prefixes*."""
        return any(t.startswith(prefixes) for t in tokens)

    @classmethod
    def _is_surface_shader(cls, node) -> bool:
        """Whether *node*'s type is classified as a surface shader."""
        return cls._has_role(cls._classification_tokens(node), "shader/surface")

    @classmethod
    def _is_utility_node(cls, node) -> bool:
        """Whether *node* is positively classified as a non-shader utility/texture.

        ``shadingNode -asShader`` parks ANY node type in ``defaultShaderList1``,
        and that list is exactly what ``cmds.ls(materials=True)`` reports — so a
        mis-flagged ``aiMultiply`` / ``bump2d`` reads as a material. This is the
        test that filters them back out.

        Deliberately conservative: a node is rejected only when it *claims* a
        ``utility/`` / ``texture/`` / ``math/`` role and claims no shader role,
        so an unclassified custom-plugin shader is still treated as a material.
        The shader test comes first because a real shader may carry "utility"
        deeper in its path — ``StingrayPBS`` and ``surfaceShader`` are both
        classified ``shader/surface/utility``.
        """
        tokens = cls._classification_tokens(node)
        if not tokens or cls._has_role(tokens, "shader/"):
            return False
        return cls._has_role(tokens, "utility/", "texture/", "math/")

    #: Shading-engine plugs a material can be wired into. Surface only by default
    #: — the rest are opt-in (see ``get_mats(include_displacement=True)``).
    _SG_SHADER_SLOTS = ("surfaceShader",)
    _SG_EXTRA_SHADER_SLOTS = ("displacementShader", "volumeShader", "aiSurfaceShader")

    @classmethod
    def _sg_shaders(cls, sg, slots=None) -> List[str]:
        """Shaders connected to *sg*'s shader plugs (default: ``surfaceShader``).

        The one place the shading-engine -> material hop is written. Plugs are
        existence-checked because the optional ones are plugin-supplied
        (``aiSurfaceShader`` exists only with mtoa loaded).
        """
        found = []
        for slot in slots or cls._SG_SHADER_SLOTS:
            plug = f"{sg}.{slot}"
            if not cmds.objExists(plug):
                continue
            found.extend(
                cmds.listConnections(plug, source=True, destination=False) or []
            )
        return found

    @classmethod
    def _shading_engine_shaders(cls) -> List[str]:
        """Surface shaders wired into a shading engine, in scene order.

        ``cmds.ls(materials=True)`` reports ``defaultShaderList1``, and only
        ``shadingNode -asShader`` registers a node there — so a shader built
        with ``createNode``, or one an importer/plugin wired straight into a
        shading engine, is assigned to geometry yet invisible to Maya's own
        materials query. This is the second source :meth:`get_scene_mats`
        unions in so such a material is still a scene material.
        """
        shaders = []
        for sg in cmds.ls(type="shadingEngine") or []:
            shaders.extend(cls._sg_shaders(sg))
        return shaders

    @staticmethod
    def _unique_name_map(materials) -> dict:
        """``{display_name: material}`` that never drops a material.

        Keyed on the short name, which is NOT unique across namespaces
        (``nsA:mat`` and ``nsB:mat`` both shorten to ``mat``) — a plain dict
        comprehension silently keeps only the last of each colliding group and
        the others become unreachable in every list built from it. Every member
        of a colliding group is therefore keyed on its namespace-qualified leaf
        name instead, which IS unique (materials are DG nodes, so the qualified
        name is the whole name), so the pair reads as ``nsA:mat`` / ``nsB:mat``.
        """
        counts = {}
        for m in materials:
            short = CoreUtils.short_name(m)
            counts[short] = counts.get(short, 0) + 1

        return {
            (
                CoreUtils.short_name(m)
                if counts[CoreUtils.short_name(m)] == 1
                else CoreUtils.leaf_name(m)
            ): m
            for m in materials
        }

    @staticmethod
    def _to_strs(nodes) -> List[str]:
        """Coerce a node/node/iterable to a list of plain string names."""
        if nodes is None:
            return []
        if isinstance(nodes, (list, tuple, set)):
            return [str(n) for n in nodes if n is not None]
        return [str(nodes)]


class MatUtils(_MatUtilsInternal):
    @staticmethod
    def resolve_path(path: str, search: bool = True) -> Union[str, None]:
        """Resolve a texture path, expanding env vars and ``<UDIM>`` tokens.

        Parameters:
            path: The stored ``fileTextureName`` value.
            search: When True (default) fall back to *hunting* for the texture
                under the project's ``sourceimages`` — by relative path, then by
                bare basename. That is what makes this a repair primitive
                (``resolve_invalid_texture_paths`` writes the result back).

                Pass ``search=False`` to answer the narrower question "does this
                path resolve the way **Maya** will resolve it" — env-var
                expansion plus ``workspace(expandName=...)`` only. Validity
                checks must use this: the basename hunt happily matches a
                same-named file the node does not point at, so a genuinely
                broken link would read as valid and ship broken.

        Returns:
            str|None: The resolved path, or None when it does not resolve.
        """
        if not path:
            return None

        check_exists = MatUtils._texture_exists

        expanded = os.path.expandvars(path)
        if check_exists(expanded):
            return expanded

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
    def get_mats(
        objs=None,
        as_strings=True,
        mat_type=None,
        include_displacement=False,
    ) -> List[str]:
        """Returns the set of materials assigned to a given list of objects or components.

        Parameters:
            objs (list): The objects or components to retrieve the material from.
                If None, the current selection is used.
            as_strings (bool): Retained for API compatibility — always returns
                strings now. Default is ``True``.
            mat_type (str, optional): Maya node type to filter by
                (e.g. ``"StingrayPBS"``, ``"lambert"``, ``"aiStandardSurface"``).
                If None, all material types are returned.
            include_displacement (bool): Also follow each shading engine's
                ``displacementShader`` / ``volumeShader`` / ``aiSurfaceShader``
                connections.  Default False keeps the historical
                surface-shader-only contract; the scene exporter opts in so
                displacement/volume textures are validated and staged like
                every other map.

        Returns:
            list[str]: Materials assigned to the objects or components (duplicates removed).
        """
        sg_slots = list(MatUtils._SG_SHADER_SLOTS)
        if include_displacement:
            sg_slots += list(MatUtils._SG_EXTRA_SHADER_SLOTS)

        def _sg_mats(sg):
            return MatUtils._sg_shaders(sg, sg_slots)

        if objs is None:
            objs = cmds.ls(selection=True, long=True) or []

        if not objs:
            return []

        if not isinstance(objs, (list, tuple, set)):
            objs = [objs]

        objs = [str(o) for o in objs]

        target_objs = cmds.ls(objs, long=True, flatten=True) or []
        mats = set()

        faces = [obj for obj in target_objs if ".f[" in obj]
        objects = [obj for obj in target_objs if ".f[" not in obj]

        if objects:
            potential_mats = cmds.ls(objects, mat=True, long=True) or []
            if potential_mats:
                mats.update(potential_mats)
                potential_mats_set = set(potential_mats)
                objects = [o for o in objects if o not in potential_mats_set]

            # ``descend=True``: a selected GROUP counts as its contents.
            # Production scenes nest geometry several transforms deep
            # (|STATIC|SOURCE|WALLS|WALL_A) and an artist picks the group in the
            # Outliner, not the mesh buried inside it -- a direct-children-only
            # lookup resolved zero materials for every such selection. Shapes
            # passed directly resolve through the same call.
            shapes = NodeUtils.get_shapes(objects, descend=True)

            if shapes:
                shading_engines = set()
                for shape in shapes:
                    sgs = cmds.listSets(object=shape, type=1) or []
                    if not sgs:
                        sgs = cmds.listConnections(shape, type="shadingEngine") or []
                    shading_engines.update(sgs)

                for sg in shading_engines:
                    mats.update(_sg_mats(sg))

        if faces:
            for face in faces:
                face_sgs = cmds.listSets(object=face, type=1) or []
                if face_sgs:
                    for sg in face_sgs:
                        mats.update(_sg_mats(sg))
                else:
                    obj_name = face.split(".")[0]
                    obj_shapes = (
                        cmds.listRelatives(obj_name, shapes=True, fullPath=True) or []
                    )
                    for shape in obj_shapes:
                        sgs = (
                            cmds.listConnections(
                                shape,
                                type="shadingEngine",
                                source=False,
                                destination=True,
                            )
                            or []
                        )
                        for sg in sgs:
                            mats.update(_sg_mats(sg))

        if mat_type:
            mats = {m for m in mats if m and cmds.nodeType(m) == mat_type}

        return list(mats)

    @staticmethod
    def _cluster_objects_by_distance(objects, threshold):
        """Clusters objects by spatial proximity (flood-fill, threshold-linked).

        Delegates the proximity flood-fill to the DCC-agnostic
        ``ptk.PointCloud.cluster_by_distance`` (shared with the Blender port);
        this only supplies each object's bounding-box centre and maps the
        returned index-clusters back to objects.
        """
        obj_list = list(objects)
        if not obj_list:
            return []
        if len(obj_list) == 1:
            return [obj_list]

        positions = []
        for obj in obj_list:
            xmin, ymin, zmin, xmax, ymax, zmax = cmds.xform(
                obj, q=True, ws=True, bb=True
            )
            positions.append(
                ((xmin + xmax) * 0.5, (ymin + ymax) * 0.5, (zmin + zmax) * 0.5)
            )

        index_clusters = ptk.PointCloud.cluster_by_distance(positions, threshold)
        return [[obj_list[i] for i in cluster] for cluster in index_clusters]

    @staticmethod
    def _materials_by_object(objects: List[str]) -> Dict[str, List[str]]:
        """Map each object to its assigned material(s) in a single scene pass.

        Batched equivalent of calling :meth:`get_mats` once per object: resolves
        shading-engine membership for the whole scene once (a handful of cmds
        calls) instead of issuing ~5 calls per object. Returns
        ``{obj_long_name: [material, ...]}`` for every input object.
        """
        objects = cmds.ls(objects, long=True) or []
        if not objects:
            return {}

        # Resolve each input transform to its shape(s) and build a reverse map.
        shape_to_obj: Dict[str, str] = {}
        for obj in objects:
            if cmds.nodeType(obj) in NodeUtils.SURFACE_TYPES:
                shapes = [obj]
            else:
                shapes = (
                    cmds.listRelatives(
                        obj, shapes=True, fullPath=True, noIntermediate=True
                    )
                    or []
                )
            for shape in shapes:
                shape_to_obj[shape] = obj

        result: Dict[str, set] = {obj: set() for obj in objects}
        obj_set = set(objects)
        if not shape_to_obj:
            return {obj: [] for obj in objects}

        # One pass over the scene's shading engines: for each, find which of our
        # shapes it touches (whole-object or per-face), then attribute its
        # surface shader to those objects.
        for sg in cmds.ls(type="shadingEngine") or []:
            members = cmds.sets(sg, q=True) or []
            if not members:
                continue

            touched = set()
            for member in cmds.ls(members, long=True) or []:
                base = member.split(".")[0]  # strip any .f[...] component
                if base in shape_to_obj:
                    touched.add(shape_to_obj[base])
                elif base in obj_set:  # member is a transform we were given
                    touched.add(base)

            if not touched:
                continue

            mats = (
                cmds.listConnections(
                    f"{sg}.surfaceShader", source=True, destination=False
                )
                or []
            )
            for obj in touched:
                result[obj].update(mats)

        return {obj: list(mats) for obj, mats in result.items()}

    @staticmethod
    def group_objects_by_material(
        objects, cluster_by_distance=False, threshold=10000.0
    ):
        """Groups objects based on their assigned material(s)."""
        groups = {}

        objects = cmds.ls(_MatUtilsInternal._to_strs(objects), long=True) or []
        mats_by_obj = MatUtils._materials_by_object(objects)

        for obj in objects:
            mats = mats_by_obj.get(obj, [])

            if not mats:
                key = "None"
            elif len(mats) > 1:
                key = tuple(sorted(mats))
            else:
                key = mats[0]

            if key not in groups:
                groups[key] = []
            groups[key].append(obj)

        if cluster_by_distance:
            clustered_groups = {}
            for mat_key, objs in groups.items():
                clusters = MatUtils._cluster_objects_by_distance(objs, threshold)
                for i, cluster in enumerate(clusters):
                    new_key = (mat_key, i) if len(clusters) > 1 else mat_key
                    clustered_groups[new_key] = cluster
            return clustered_groups
        return groups

    @staticmethod
    def is_bundled_texture(path: str) -> bool:
        """Does *path* live inside Maya's own installation?

        Those are the images Autodesk ships — StingrayPBS' ``diffuse_cube.dds`` /
        ``specular_cube.dds`` environment maps and the rest of
        ``presets/ShaderFX/Images`` — wired onto real file nodes, so every
        material-scoped query returns them alongside the user's maps. They are
        not project assets: the install tree is read-only, so a tool that writes
        (optimize, repath, repack) can only fail on them.

        Path-only and side-effect free, so callers can filter a list without
        touching the scene. False when ``MAYA_LOCATION`` is unset.
        """
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
    def get_texture_paths(
        cls,
        objects: Optional[List[Any]] = None,
        materials: Optional[List[Any]] = None,
        file_nodes: Optional[List[Any]] = None,
        texture_names: Optional[List[str]] = None,
        absolute: bool = True,
        exclude_bundled: bool = False,
    ) -> List[str]:
        """Resolve unique texture file paths for the given scope.

        Lightweight counterpart to :meth:`get_texture_info` — reads only the
        ``fileTextureName`` attribute from each resolved ``file`` node, so it
        is safe to call from interactive UI providers on selections with many
        high-resolution textures (no PIL decoding).

        Parameters:
            objects: Scene objects (transforms / shapes / face components).
                Materials are resolved from their assigned shading engines.
            materials: Materials to scope by directly.
            file_nodes: Pre-resolved ``file`` nodes to read paths from.
            texture_names: Extra raw texture paths to include verbatim.
            absolute: If True (default), paths are made absolute against the
                project ``sourceimages`` directory; if False, relative when
                the texture lives under ``sourceimages``.
            exclude_bundled: Drop textures shipped with Maya itself (see
                :meth:`is_bundled_texture`). Off by default — a query that
                inventories the scene wants every wired map. Tools that
                *write* should turn it on: the install tree is read-only, so
                a StingrayPBS material's preset cube maps can only fail them.

        Returns:
            list[str]: Unique non-empty paths in resolution order.
        """
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

    @classmethod
    def get_texture_info(
        cls,
        objects=None,
        materials=None,
        file_nodes=None,
        texture_names=None,
    ):
        """Get image metadata (size, mode, format) for texture files in scope.

        Heavy: opens every texture with PIL. For path-only callers, use
        :meth:`get_texture_paths` instead.
        """
        paths = cls.get_texture_paths(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            texture_names=texture_names,
        )
        return ptk.ImgUtils.get_image_info(paths)

    @classmethod
    def get_mat_info(
        cls,
        materials: Optional[List[Any]] = None,
        objects: Optional[List[Any]] = None,
        optimize_check: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        exclude_defaults: bool = False,
        exclude_unassigned: bool = False,
        include_textures: bool = True,
        include_image_metadata: bool = True,
        **optimize_kwargs,
    ) -> List[Dict[str, Any]]:
        """Aggregate per-material info: name, type, textures + image metadata.

        Each entry bundles the material's identity with one record per file
        node it drives. When ``optimize_check`` is True, each texture record
        also gets an ``optimization`` block from
        :meth:`ptk.MapOptimizer.assess` — useful for spotting oversized /
        wrong-mode textures from a UI report.

        Parameters:
            materials: Materials to scope by. None falls back to the entire
                scene unless ``objects`` is supplied.
            objects: Scene nodes whose assigned materials should be scoped.
            optimize_check: If True, run optimization analysis per texture.
                Opens each texture once and reuses the loaded PIL image for
                both the metadata and the assessment.
            exclude_defaults: Drop Maya's built-in default materials
                (``lambert1``, ``standardSurface1``, etc.) from the result.
            exclude_unassigned: Drop materials whose shading engines have
                no DAG members (see :meth:`is_mat_assigned`).
            include_textures: If False, omit the per-file-node texture work
                entirely and emit each material with ``textures: []``.
            include_image_metadata: If False, omit width/height/mode/format/
                bit_depth from texture records. PIL is only opened when this
                or ``optimize_check`` requires it.
            **optimize_kwargs: Forwarded to
                ``ptk.MapOptimizer.assess`` (``max_size``, ``force_pot``,
                ``optimize_bit_depth``, ``map_type``, ``allow_palette``).

        Returns:
            list[dict]: Per material:
                {
                    "material": str,        # material node name
                    "type": str,            # cmds.nodeType
                    "textures": [           # one entry per file node
                        {
                            "file_node": str,
                            "path": str,
                            "name": str,
                            "size": int,    # bytes
                            "width": int,
                            "height": int,
                            "mode": str,
                            "format": str,
                            "bit_depth": str,   # e.g. "32bit (8x4)"
                            "optimization": {...}  # only when optimize_check
                        },
                        ...
                    ],
                }
        """
        # Resolve the material scope. Materials passed explicitly take
        # precedence over object-derived materials; both fall through to the
        # entire scene when nothing was supplied. An explicit empty iterable
        # means "no scope" and short-circuits to an empty result rather than
        # letting ``cmds.ls(mat=True)`` fall back to the whole scene.
        if materials is not None:
            mat_strs = _MatUtilsInternal._to_strs(materials)
            resolved_materials = (
                sorted({m for m in (cmds.ls(mat_strs, mat=True) or []) if m})
                if mat_strs
                else []
            )
        elif objects is not None:
            obj_strs = _MatUtilsInternal._to_strs(objects)
            resolved_materials = sorted(cls.get_mats(obj_strs)) if obj_strs else []
        else:
            resolved_materials = (
                cls.get_scene_mats(sort=True, exclude_defaults=False) or []
            )

        if exclude_defaults and resolved_materials:
            default_nodes = cls._default_material_names()
            resolved_materials = [
                m
                for m in resolved_materials
                if CoreUtils.short_name(m) not in default_nodes
            ]

        if exclude_unassigned and resolved_materials:
            resolved_materials = [
                m for m in resolved_materials if cls.is_mat_assigned(m)
            ]

        need_image = include_image_metadata or optimize_check

        results: List[Dict[str, Any]] = []
        total = len(resolved_materials)
        for i, mat in enumerate(resolved_materials):
            mat_str = str(mat)
            if progress_callback:
                progress_callback(i, total, f"Reading material: {mat_str}")
            try:
                mat_type = cmds.nodeType(mat_str)
            except Exception:
                mat_type = "unknown"

            tex_entries: List[Dict[str, Any]] = []
            if include_textures:
                # Restrict file nodes to those connected to this specific
                # material so shared-file-node cases don't double-count.
                file_nodes = cls.get_file_nodes(materials=[mat_str]) or []
                for fn in file_nodes:
                    paths = cls._paths_from_file_nodes([fn], absolute=True)
                    if not paths:
                        continue
                    path = paths[0]
                    size_bytes = os.path.getsize(path) if os.path.exists(path) else None

                    pil_image = None
                    width = height = None
                    mode = img_format = None
                    if need_image:
                        try:
                            with ptk.ImgUtils.allow_large_images():
                                pil_image = ptk.ImgUtils.ensure_image(path)
                            width, height = pil_image.size
                            mode = pil_image.mode
                            img_format = pil_image.format
                        except Exception as e:
                            tex_entries.append(
                                {
                                    "file_node": fn,
                                    "path": path,
                                    "name": os.path.basename(path),
                                    "size": size_bytes,
                                    "error": f"Failed to read image: {e}",
                                }
                            )
                            continue

                    info: Dict[str, Any] = {
                        "file_node": fn,
                        "path": path,
                        "name": os.path.basename(path),
                        "size": size_bytes,
                    }
                    if include_image_metadata:
                        info.update(
                            {
                                "width": width,
                                "height": height,
                                "mode": mode,
                                "format": img_format,
                                "bit_depth": ptk.ImgUtils.format_bit_depth(mode),
                            }
                        )
                    if optimize_check:
                        info["optimization"] = ptk.MapOptimizer.assess(
                            path, image=pil_image, **optimize_kwargs
                        )
                    tex_entries.append(info)

            results.append(
                {
                    "material": mat_str,
                    "type": mat_type,
                    "textures": tex_entries,
                }
            )

        if progress_callback and total:
            progress_callback(total, total, "Done")
        return results

    # ---- Formatters ---------------------------------------------------

    # The pure record→text/HTML formatting lives in ``pythontk.MatReport`` (DCC-agnostic SSoT,
    # shared with blendertk); these classmethods stay for back-compat and delegate to it.
    @classmethod
    def format_texture_info_text(cls, info_list: List[Dict[str, Any]]) -> str:
        """Render :meth:`get_texture_info` output as a plain-text report (``pythontk.MatReport``)."""
        return ptk.MatReport.format_texture_info_text(info_list)

    @classmethod
    def format_texture_info_html(cls, info_list: List[Dict[str, Any]]) -> str:
        """Render :meth:`get_texture_info` output as styled HTML (``pythontk.MatReport``)."""
        return ptk.MatReport.format_texture_info_html(info_list)

    @classmethod
    def format_mat_info_text(cls, records: List[Dict[str, Any]]) -> str:
        """Render :meth:`get_mat_info` output as a plain-text report (``pythontk.MatReport``)."""
        return ptk.MatReport.format_mat_info_text(records)

    @classmethod
    def format_mat_info_html(cls, records: List[Dict[str, Any]]) -> str:
        """Render :meth:`get_mat_info` output as styled HTML (``pythontk.MatReport``)."""
        return ptk.MatReport.format_mat_info_html(records)

    @staticmethod
    def get_scene_mats(
        inc=None,
        exc=None,
        node_type=None,
        sort: bool = False,
        as_dict: bool = False,
        exclude_defaults: bool = True,
        exclude_utility_nodes: bool = True,
        exc_classification=None,
        **filter_kwargs,
    ):
        """Retrieves all materials from the current scene, with flexible name/type filtering.

        The source is ``cmds.ls(materials=True)`` UNIONED with the shaders wired
        into the scene's shading engines (:meth:`_shading_engine_shaders`), because
        Maya's query only reports ``defaultShaderList1`` — a shader built with
        ``createNode``, or wired up directly by an importer/plugin, is assigned to
        geometry yet absent from it.

        Parameters:
            inc/exc (str/list): Name patterns to keep / drop (shell wildcards,
                matched against the short name).
            node_type (str/list): Keep only these node types.
            sort (bool): Sort by short name.
            as_dict (bool): Return ``{display_name: material}`` instead of a list.
                The key is the short name, except where several materials share
                one (across namespaces) — then every member of that group is keyed
                on its namespace-qualified name, so none is dropped.
            exclude_defaults (bool): Drop Maya's built-in defaults (``lambert1``,
                ``particleCloud1``, ``shaderGlow1``, ``standardSurface1``, plus
                anything reported by ``cmds.ls(defaultNodes=True)``). Default True.
            exclude_utility_nodes (bool): Drop nodes that Maya reports as materials
                only because something registered them with ``shadingNode -asShader``
                while their classification claims a ``utility/`` / ``texture/`` /
                ``math/`` role — e.g. an ``aiMultiply`` or ``bump2d`` helper from
                inside a shading network.
                Default True; pass False for Maya's raw ``ls(materials=True)`` view.
            exc_classification (str/list): Drop materials whose classification
                matches these patterns (shell wildcards, matched per classification
                token). ``"rendernode/arnold*"`` hides Arnold shaders,
                ``"rendernode/redshift*"`` Redshift's, and so on.
            filter_kwargs: Forwarded to ``ptk.filter_list`` alongside inc/exc
                (``ignore_case``, ``match_all``, ``negate_prefix``, ...).
        """
        # Maya's own list, plus anything wired into a shading engine that never
        # made it into defaultShaderList1 (see _shading_engine_shaders) — a
        # material assigned to geometry must be listed whichever way it was built.
        mat_list = cmds.ls(materials=True, flatten=True) or []
        seen = set(mat_list)
        for shader in MatUtils._shading_engine_shaders():
            if shader not in seen:
                seen.add(shader)
                mat_list.append(shader)

        if exclude_defaults and mat_list:
            default_nodes = MatUtils._default_material_names()
            mat_list = [
                m for m in mat_list if CoreUtils.short_name(m) not in default_nodes
            ]

        if exclude_utility_nodes and mat_list:
            mat_list = [m for m in mat_list if not MatUtils._is_utility_node(m)]

        if exc_classification and mat_list:
            # Keep the material when none of its classification tokens matches.
            mat_list = [
                m
                for m in mat_list
                if not ptk.filter_list(
                    MatUtils._classification_tokens(m), inc=exc_classification
                )
            ]

        # Name filtering runs over the LIST (matched on the short name), not over
        # a ``{short_name: material}`` dict: that dict collapses materials that
        # share a short name across namespaces, so building one here dropped
        # them from every return path, filtered or not.
        if inc or exc or filter_kwargs:
            mat_list = ptk.filter_list(
                mat_list,
                inc=inc,
                exc=exc,
                map_func=CoreUtils.short_name,
                **filter_kwargs,
            )

        if node_type:
            mat_list = ptk.filter_list(mat_list, inc=node_type, map_func=cmds.nodeType)

        if sort:
            mat_list = sorted(mat_list, key=CoreUtils.short_name)

        return MatUtils._unique_name_map(mat_list) if as_dict else mat_list

    @classmethod
    def get_connected_shaders(cls, file_nodes) -> List[str]:
        """Return surface shaders connected to one or more file nodes, ignoring intermediates."""
        file_nodes = cmds.ls(cls._to_strs(file_nodes), flatten=True) or []
        visited = set()
        shaders = set()

        def _traverse(node):
            if node in visited:
                return
            visited.add(node)

            outputs = cmds.listConnections(node, source=False, destination=True) or []
            for out in outputs:
                # Skip non-shading nodes — only follow shader graph nodes.
                if cmds.nodeType(out) == "shadingEngine":
                    continue
                if cls._is_surface_shader(out):
                    shaders.add(out)
                _traverse(out)

        for file_node in file_nodes:
            _traverse(file_node)

        return list(shaders)

    @staticmethod
    def connect_to_channels(source_plug: str, node: str, attr: str) -> bool:
        """Connect a single-channel `source_plug` into a (possibly compound) slot.

        Color/vector slots (``TEX_ao_map``, ``opacity``, ``transparency``, …)
        must be driven per channel when a scalar source feeds them. Any existing
        input on the parent is broken first, so the parent and its children can
        never end up driven by two different textures.

        Parameters:
            source_plug (str): Source plug, e.g. ``"file1.outAlpha"``.
            node (str): Target node.
            attr (str): Target attribute (parent name).

        Returns:
            bool: True if the connection was made.
        """
        if not cmds.attributeQuery(attr, node=node, exists=True):
            return False

        children = [
            f"{attr}{s}"
            for s in ("R", "G", "B")
            if cmds.attributeQuery(f"{attr}{s}", node=node, exists=True)
        ] or [
            f"{attr}{s}"
            for s in ("X", "Y", "Z")
            if cmds.attributeQuery(f"{attr}{s}", node=node, exists=True)
        ]

        if len(children) >= 3:
            # Break the parent so it can't stay bound to a previous texture
            # while the children are driven by this one.
            parent_sources = (
                cmds.listConnections(
                    f"{node}.{attr}", plugs=True, source=True, destination=False
                )
                or []
            )
            for src in parent_sources:
                cmds.disconnectAttr(src, f"{node}.{attr}")
            # All-or-nothing: a child that refuses (locked, wrong type) must not
            # leave a HALF-driven compound behind, nor raise out of a method whose
            # contract is a bool -- both callers treat False as "report and move
            # on". Undo the children made, then restore the parent input broken
            # above, so a failure is a genuine no-op.
            connected = []
            try:
                for child in children[:3]:
                    cmds.connectAttr(source_plug, f"{node}.{child}", force=True)
                    connected.append(child)
            except RuntimeError:
                for child in connected:
                    try:
                        cmds.disconnectAttr(source_plug, f"{node}.{child}")
                    except RuntimeError:
                        pass
                for src in parent_sources:
                    try:
                        cmds.connectAttr(src, f"{node}.{attr}", force=True)
                    except RuntimeError:
                        pass
                return False
            return True

        try:  # scalar slot
            cmds.connectAttr(source_plug, f"{node}.{attr}", force=True)
            return True
        except RuntimeError:
            return False

    @classmethod
    def get_mats_by_scope(
        cls, scope: str = "selected", mat_type: Optional[str] = None
    ) -> List[str]:
        """Materials in the given scope.

        The scope primitive behind material tools that offer a Selected /
        Visible / Scene choice, so each one resolves the same way.

        Parameters:
            scope (str): ``"selected"`` — materials on the current selection.
                ``"visible"`` — materials on visible geometry.
                ``"scene"`` — every scene material, assigned or not.
            mat_type (str, optional): Maya node type filter (e.g.
                ``"StingrayPBS"``).

        Returns:
            list[str]: Material names (duplicates removed).
        """
        scope = (scope or "selected").strip().lower()

        if scope == "scene":
            mats = cls.get_scene_mats(node_type=mat_type) or []
            return [str(m) for m in mats]

        if scope == "visible":
            from mayatk.display_utils._display_utils import DisplayUtils

            objects = (
                DisplayUtils.get_visible_geometry(inherit_parent_visibility=True) or []
            )
        else:
            objects = cmds.ls(selection=True, long=True) or []

        if not objects:
            return []
        return cls.get_mats(objects, mat_type=mat_type)

    # Shader type → (slot that drives cutout/blend in the viewport, sense).
    # ``"opacity"`` slots take the alpha straight; anything not listed here
    # (lambert, blinn, phong, …) drives ``transparency``, which is inverted.
    OPACITY_INPUTS = {
        "StingrayPBS": ("opacity", "opacity"),
        "standardSurface": ("opacity", "opacity"),
        "openPBRSurface": ("geometryOpacity", "opacity"),
        "aiStandardSurface": ("opacity", "opacity"),
        "usdPreviewSurface": ("opacity", "opacity"),
    }

    # Map types accepted as an opacity source, best first.
    OPACITY_MAP_TYPES = ("Opacity", "Albedo_Transparency")

    @classmethod
    def find_opacity_source(cls, mat: str) -> Optional[str]:
        """The file node in `mat`'s network that carries its opacity.

        Recognizes a dedicated Opacity map, or the packed alpha of an
        Albedo_Transparency map. Returns None when the material has neither —
        the test for "is this an opacity material".

        Parameters:
            mat (str): Material node.

        Returns:
            str | None: The file node, or None.
        """
        file_nodes = cmds.ls(cmds.listHistory(mat) or [], type="file") or []
        by_type: Dict[str, str] = {}
        for file_node in file_nodes:
            path = cmds.getAttr(f"{file_node}.fileTextureName") or ""
            map_type = ptk.MapFactory.resolve_map_type(path) if path else None
            if map_type in cls.OPACITY_MAP_TYPES:
                by_type.setdefault(map_type, file_node)

        for map_type in cls.OPACITY_MAP_TYPES:
            if map_type in by_type:
                return by_type[map_type]
        return None

    @classmethod
    @CoreUtils.undoable
    def enable_viewport_opacity(
        cls,
        materials=None,
        transparency_algorithm: Optional[str] = None,
        search_disk: bool = True,
    ) -> Dict[str, str]:
        """Wire every opacity map in `materials` so it shows in the viewport.

        A texture set can carry an opacity map that never reaches the shader —
        the material was built before the map existed, or from a shader graph
        with no opacity slot. This finds each material's opacity map and drives
        the right slot for its shader type: alpha into ``opacity`` for the PBR
        shaders, inverted into ``transparency`` for the classic ones. StingrayPBS
        materials are switched to the transparent ShaderFX graph first (their
        opacity slots don't otherwise exist), preserving existing textures.

        Parameters:
            materials: Materials **or** objects (objects are resolved to their
                materials). If None, the current selection is used.
            transparency_algorithm (str, optional): Viewport 2.0 transparency
                mode to apply — ``"simple"``, ``"object_sorting"``,
                ``"weighted_average"`` or ``"depth_peeling"``. Left alone when
                None. Depth peeling sorts overlapping transparent faces
                correctly (decals, foliage) at some viewport cost.
            search_disk (bool): When the network holds no opacity map, look for
                one beside the material's other textures and import it.

        Returns:
            dict: ``{material: status}`` where status is ``"enabled"``,
            ``"already enabled"``, ``"no opacity map"`` or ``"unsupported: …"``.
        """
        from mayatk.mat_utils.mat_snapshot import MatSnapshot

        results: Dict[str, str] = {}

        # Materials in a set share a texture folder; scan each folder once.
        dir_cache: Dict[str, Dict[str, str]] = {}

        for mat in cls.get_mats(materials):
            mat = str(mat)
            name = CoreUtils.short_name(mat)
            source = cls.find_opacity_source(mat)
            if not source and search_disk:
                path = cls._find_opacity_map_on_disk(mat, dir_cache)
                if path:
                    source = NodeUtils.create_render_node(
                        "file",
                        fileTextureName=path,
                        name=ptk.format_path(path, section="name"),
                    )
            if not source:
                results[mat] = "no opacity map"
                continue

            node_type = cmds.nodeType(mat)
            attr, sense = cls.OPACITY_INPUTS.get(
                node_type, ("transparency", "transparency")
            )

            # StingrayPBS: the opacity slots live on the transparent graph only,
            # and loading it wipes the network — snapshot, swap, restore.
            if node_type == "StingrayPBS" and not cmds.attributeQuery(
                attr, node=mat, exists=True
            ):
                snapshot = MatSnapshot.capture(name)
                if not cls.ensure_transparent_graph(mat):
                    results[mat] = "unsupported: no transparent graph available"
                    continue
                MatSnapshot.restore(name, snapshot)
                source = cls.find_opacity_source(mat) or source

            if not cmds.attributeQuery(attr, node=mat, exists=True):
                results[mat] = f"unsupported: {node_type} has no '{attr}' slot"
                continue

            # Already driven by this very map (possibly through a reverse) —
            # leave it be, so a re-run can't stack duplicate conversion nodes.
            if any(
                plug.split(".")[0] == source
                or source in (cmds.listHistory(plug.split(".")[0]) or [])
                for plug in cls._slot_inputs(mat, attr)
            ):
                results[mat] = "already enabled"
                continue

            # Where the mask lives decides how the file node must read alpha: a
            # dedicated Opacity map is grayscale (luminance yields a usable mask
            # even with no alpha channel), while a packed Albedo_Transparency
            # map carries the real thing — reading luminance there would drive
            # opacity from the albedo's brightness.
            if cmds.attributeQuery("alphaIsLuminance", node=source, exists=True):
                path = cmds.getAttr(f"{source}.fileTextureName") or ""
                map_type = ptk.MapFactory.resolve_map_type(path)
                if map_type in cls.OPACITY_MAP_TYPES:
                    cmds.setAttr(
                        f"{source}.alphaIsLuminance", int(map_type == "Opacity")
                    )

            plug = f"{source}.outAlpha"
            if sense == "transparency":  # classic shaders: 1 - alpha
                reverse = cmds.shadingNode(
                    "reverse", asUtility=True, name=f"{name}_invertOpacity"
                )
                cmds.connectAttr(plug, f"{reverse}.inputX", force=True)
                plug = f"{reverse}.outputX"

            if not cls.connect_to_channels(plug, mat, attr):
                results[mat] = f"unsupported: could not drive '{attr}'"
                continue

            # StingrayPBS gates the opacity input behind its own toggle.
            if cmds.attributeQuery("use_opacity_map", node=mat, exists=True):
                use_plug = f"{mat}.use_opacity_map"
                if not cmds.getAttr(use_plug, lock=True):
                    cmds.setAttr(use_plug, 1)

            results[mat] = "enabled"

        if transparency_algorithm:
            cls.set_transparency_algorithm(transparency_algorithm)

        return results

    # Viewport 2.0 transparency modes, in ``transparencyAlgorithm`` order.
    TRANSPARENCY_ALGORITHMS = (
        "simple",
        "object_sorting",
        "weighted_average",
        "depth_peeling",
    )

    @classmethod
    def set_transparency_algorithm(cls, algorithm: str) -> bool:
        """Set the Viewport 2.0 transparency mode.

        Parameters:
            algorithm (str): One of :attr:`TRANSPARENCY_ALGORITHMS`.

        Returns:
            bool: True if the mode was applied.
        """
        key = str(algorithm).strip().lower().replace(" ", "_")
        if key not in cls.TRANSPARENCY_ALGORITHMS:
            return False
        cmds.setAttr(
            "hardwareRenderingGlobals.transparencyAlgorithm",
            cls.TRANSPARENCY_ALGORITHMS.index(key),
        )
        return True

    @classmethod
    def ensure_transparent_graph(cls, mat: str) -> bool:
        """Load ``Standard_Transparent.sfx`` onto a StingrayPBS node if needed.

        The opacity slots (``opacity`` / ``use_opacity_map``) only exist on the
        transparent ShaderFX graph — a StingrayPBS built from the standard graph
        has nowhere to plug an opacity map.

        .. note:: ``loadGraph`` drops the node's existing connections; callers
           that need them preserved must snapshot first (see ``MatSnapshot``).

        Parameters:
            mat (str): StingrayPBS material.

        Returns:
            bool: True if the material now exposes the opacity slots.
        """
        if cmds.attributeQuery("use_opacity_map", node=mat, exists=True):
            return True
        return cls.load_stingray_graph(mat, "transparent")

    @classmethod
    def get_file_nodes(
        cls,
        materials: Optional[List[str]] = None,
        raw: bool = False,
        return_type: str = "fileNode",
        exc_classification=None,
    ) -> list:
        """Returns file node info in any column order based on return_type.

        ``exc_classification`` (str/list, shell wildcards) drops file nodes used
        *exclusively* by shaders whose classification matches — e.g.
        ``"rendernode/arnold*"`` hides the duplicate rows an Arnold preview
        shader contributes (it owns dedicated file nodes per texture). A file
        node shared with a non-matching shader is kept, so hiding a renderer
        never hides a texture something else still uses.
        """
        file_node_names = cmds.ls(type="file") or []
        if not file_node_names:
            return []

        workspace_dir = cmds.workspace(q=True, rd=True) or ""
        columns = return_type.split("|")
        needs_shader = (
            "shader" in columns
            or "shaderName" in columns
            or materials is not None
            or bool(exc_classification)
        )

        file_to_shader_name = {}
        file_to_shaders = {}
        if needs_shader:
            shading_engines = cmds.ls(type="shadingEngine") or []
            shader_attrs = ["surfaceShader", "volumeShader", "displacementShader"]
            processed_shaders = set()

            def _record_shader(shader_name):
                """Map every file node upstream of *shader_name* onto it."""
                if not shader_name or shader_name in processed_shaders:
                    return
                processed_shaders.add(shader_name)
                try:
                    history = cmds.listHistory(shader_name, pruneDagObjects=True) or []
                    file_nodes_in_history = cmds.ls(history, type="file") or []
                except Exception:
                    return
                for node in file_nodes_in_history:
                    file_to_shaders.setdefault(node, set()).add(shader_name)
                    file_to_shader_name.setdefault(node, shader_name)

            for sg in shading_engines:
                # Classic slots first, so a file node driven by several shaders
                # is still *reported* under the surface shader.
                for attr_name in shader_attrs:
                    try:
                        connections = cmds.listConnections(
                            f"{sg}.{attr_name}", source=True, destination=False
                        )
                    except Exception:
                        continue
                    if connections:
                        _record_shader(connections[0])
                # Renderer-specific slots (Arnold's aiSurfaceShader, and its
                # equivalents) hold shaders the classic three never see. An
                # Arnold preview shader owns dedicated file nodes, so missing
                # it leaves those textures looking like unowned orphans.
                try:
                    sources = cmds.listConnections(sg, source=True, destination=False)
                except Exception:
                    sources = None
                for src in sources or []:
                    if src not in processed_shaders and cls._is_surface_shader(src):
                        _record_shader(src)

        if materials:
            mat_names = {str(m) for m in materials}
            file_node_names = [
                fn
                for fn in file_node_names
                if mat_names & file_to_shaders.get(fn, set())
            ]

        if exc_classification:
            excluded = {}  # shader -> verdict, so each shader is classified once
            kept = []
            for fn in file_node_names:
                shaders = file_to_shaders.get(fn) or set()
                for shader in shaders:
                    if shader not in excluded:
                        excluded[shader] = bool(
                            ptk.filter_list(
                                cls._classification_tokens(shader),
                                inc=exc_classification,
                            )
                        )
                allowed = [s for s in shaders if not excluded[s]]
                if shaders and not allowed:
                    continue
                # An unused file node has no shader to judge it by — keep it,
                # an orphan texture is exactly what this editor exists to surface.
                kept.append(fn)
                # Don't label the row with a shader the caller asked to hide.
                if allowed and file_to_shader_name.get(fn) not in allowed:
                    file_to_shader_name[fn] = sorted(allowed)[0]
            file_node_names = kept

        file_paths = {}
        for fn in file_node_names:
            try:
                path = cmds.getAttr(f"{fn}.fileTextureName") or ""
                if raw and path.startswith(workspace_dir):
                    path = os.path.relpath(path, workspace_dir)
                file_paths[fn] = path
            except Exception:
                file_paths[fn] = ""

        # ``shader``/``fileNode`` historically returned nodes; with the
        # All forms now return strings.  The columns are
        # kept for API compatibility but produce the same string value as
        # their *Name counterparts.
        file_info = []
        for file_node_name in file_node_names:
            shader_name = file_to_shader_name.get(file_node_name, "")
            file_path = file_paths.get(file_node_name, "")

            row = []
            for col in columns:
                if col in ("shader", "shaderName"):
                    row.append(shader_name if shader_name else None)
                elif col == "path":
                    row.append(file_path)
                elif col in ("fileNode", "fileNodeName"):
                    row.append(file_node_name)
                else:
                    row.append("")
            file_info.append(tuple(row) if len(row) > 1 else row[0])

        return file_info

    @staticmethod
    def get_fav_mats():
        """Retrieves the list of favorite materials in Maya."""
        import os.path
        import maya.app.general.tlfavorites as _fav

        version = cmds.about(version=True).split(" ")[-1]
        path = os.path.expandvars(
            f"%USERPROFILE%/Documents/maya/{version}/prefs/renderNodeTypeFavorites"
        )
        renderNodeTypeFavorites = _fav.readFavorites(path)
        materials = [i for i in renderNodeTypeFavorites if "/" not in i]
        del _fav

        return materials

    @staticmethod
    def _default_material_names() -> set:
        """Names of materials treated as Maya built-in defaults.

        Combines ``cmds.ls(defaultNodes=True)`` with the four hard-coded
        defaults that aren't always tagged by Maya's default-nodes API
        (``lambert1``, ``particleCloud1``, ``shaderGlow1``,
        ``standardSurface1``). Single source of truth for the
        ``exclude_defaults`` filter shared by :meth:`get_scene_mats` and
        :meth:`get_mat_info`.
        """
        defaults = set(cmds.ls(defaultNodes=True) or [])
        defaults.update(
            {"lambert1", "particleCloud1", "shaderGlow1", "standardSurface1"}
        )
        return defaults

    @staticmethod
    def is_mat_assigned(mat: object) -> bool:
        """True iff *mat*'s shading engines contain at least one DAG member.

        A material is considered "assigned" when geometry is bound to one of
        its shading engines (the same condition Maya's *Delete Unused
        Materials* targets). Orphan shading engines and unconnected shaders
        both return False.

        Works for surface, displacement, and volume shaders alike — follows
        all connections instead of probing a specific output attribute,
        which only exists on surface shaders.
        """
        mat_str = str(mat)
        try:
            shading_engines = cmds.listConnections(mat_str, type="shadingEngine") or []
        except Exception:
            return False
        for sg in set(shading_engines):
            try:
                members = cmds.sets(sg, query=True) or []
            except Exception:
                continue
            if members:
                return True
        return False

    @staticmethod
    def is_connected(mat: object, delete: bool = False) -> bool:
        """Checks if a given material is assigned and optionally deletes it."""
        try:
            mat_list = cmds.ls(str(mat), type="shadingDependNode", flatten=True) or []
            mat = mat_list[0]
        except (IndexError, TypeError):
            print(f"Error: Material {mat} not found or invalid.")
            return False

        connected_shading_groups = cmds.listConnections(
            f"{mat}.outColor", type="shadingEngine"
        )
        if not connected_shading_groups:
            if delete:
                cmds.delete(mat)
            return True

        return False

    @staticmethod
    @CoreUtils.undoable
    def create_mat(mat_type, prefix="", name=""):
        """Creates a material based on the provided type or a random material if 'mat_type' is 'random'."""
        import random

        if mat_type == "random":
            preferred_type = MatUtils._create_standard_shader(return_type="type")
            rgb = [random.randint(0, 255) for _ in range(3)]
            name = "{}{}_{}_{}_{}".format(
                prefix, name, str(rgb[0]), str(rgb[1]), str(rgb[2])
            )
            mat = cmds.shadingNode(preferred_type, asShader=True, name=name)
            convertedRGB = [round(float(v) / 255, 3) for v in rgb]
            color_attr = (
                f"{mat}.baseColor"
                if preferred_type == "standardSurface"
                else f"{mat}.color"
            )
            cmds.setAttr(
                color_attr,
                convertedRGB[0],
                convertedRGB[1],
                convertedRGB[2],
                type="double3",
            )
        else:
            name = prefix + name if name else mat_type
            mat = cmds.shadingNode(mat_type, asShader=True, name=name)

        return mat

    @staticmethod
    @CoreUtils.undoable
    def assign_mat(objects, mat_name):
        """Assigns a material to a list of objects or components."""
        if not objects:
            raise ValueError("No objects provided to assign material.")

        mat_name = str(mat_name)

        if cmds.objExists(mat_name):
            mat = mat_name
        else:
            preferred_type = MatUtils._create_standard_shader(return_type="type")
            mat = cmds.shadingNode(preferred_type, name=mat_name, asShader=True)

        shading_groups = cmds.listConnections(mat, type="shadingEngine")
        if not shading_groups:
            shading_group = cmds.sets(
                name=f"{mat_name}SG", renderable=True, noSurfaceShader=True, empty=True
            )
            cmds.connectAttr(
                f"{mat}.outColor", f"{shading_group}.surfaceShader", force=True
            )
        else:
            shading_group = shading_groups[0]

        objects = _MatUtilsInternal._to_strs(objects)
        valid_objects = cmds.ls(objects, flatten=True) or []
        if valid_objects:
            cmds.sets(valid_objects, edit=True, forceElement=shading_group)

    @staticmethod
    def claim_material_name(shading_group: str, desired: str) -> str:
        """Rename a rebuilt network to *desired* once that name is free.

        A rebuild is necessarily created while the material it replaces still
        owns the name, so Maya hands it the clash spelling (``M_x`` ->
        ``M_x1``); the old one is retired moments later and the name falls
        free. Reclaiming it is what keeps a repeated hand-off non-destructive
        -- downstream (Unity, a shader library, an FBX round-trip) binds by
        material NAME, and the digit compounds on every re-send.

        Shared by every path that swaps a material in under an existing name:
        the Blender scene import (rebuilding an FBX-carried material) and the
        Marmoset bake roundtrip (replacing the previous bake's material).

        Yields silently whenever the name is still taken -- the replaced
        material may still be assigned elsewhere and keeps its claim. Cosmetic
        and best-effort; the caller's material is already correctly assigned.

        Parameters:
            shading_group: The rebuilt network's shading engine.
            desired: The name its surface shader should carry.

        Returns:
            The shading group's name, which the rename may have changed.
        """
        if not desired:
            return shading_group
        shaders = (
            cmds.listConnections(
                f"{shading_group}.surfaceShader", source=True, destination=False
            )
            or []
        )
        if not shaders:
            return shading_group
        shader = shaders[0]
        old = CoreUtils.short_name(shader)
        if old == desired or cmds.objExists(desired):
            return shading_group
        try:
            cmds.rename(shader, desired)
        except RuntimeError:
            return shading_group
        # Carry the shading group along so the pair stays legible ("M_xSG" for
        # "M_x"). Two conventions reach here: named after the CREATED shader
        # ("M_x1SG") or after the REQUESTED name plus Maya's own clash digits
        # ("M_xSG1"). Both resolve to "M_xSG"; any other spelling is left alone
        # rather than renamed on a guess.
        short_sg = CoreUtils.short_name(shading_group)
        wanted = ""
        if short_sg.startswith(old):
            wanted = desired + short_sg[len(old) :]
        elif _MatUtilsInternal._is_clash_variant(short_sg, f"{desired}SG"):
            wanted = f"{desired}SG"
        if wanted and wanted != short_sg and not cmds.objExists(wanted):
            try:
                return cmds.rename(shading_group, wanted)
            except RuntimeError:
                pass
        return shading_group

    @staticmethod
    def get_shading_assignments(obj) -> Dict[str, Optional[List[int]]]:
        """Snapshot a mesh's shading-group membership as plain data.

        Returns a mapping ``{shading_group: faces}`` where *faces* is ``None``
        for a whole-object (single-material) assignment or a list of int face
        indices for a per-face (multi-material) assignment. The data form is
        decoupled from the live node graph, so it survives operations that
        corrupt or strip the in-scene component groups (see
        :meth:`apply_shading_assignments`).
        """
        shape = NodeUtils.get_shape(obj, no_intermediate=True)
        if not shape:
            return {}
        # Long paths the set members may be expressed under: component sets
        # reference the transform, whole-object sets the shape.
        owners = set(cmds.ls(shape, long=True) or [])
        owners.update(cmds.listRelatives(shape, parent=True, fullPath=True) or [])

        result: Dict[str, Optional[List[int]]] = {}
        for sg in set(cmds.listConnections(shape, type="shadingEngine") or []):
            whole = False
            faces: List[int] = []
            for m in (
                cmds.ls(cmds.sets(sg, q=True) or [], long=True, flatten=True) or []
            ):
                if m.split(".f[")[0] not in owners:
                    continue  # a different object that shares this shading group
                if ".f[" in m:
                    faces.append(int(m.split(".f[", 1)[1].rstrip("]")))
                else:
                    whole = True
            if whole and not faces:
                result[sg] = None
            elif faces:
                result[sg] = faces
        return result

    @staticmethod
    def apply_shading_assignments(obj, assignments: Dict[str, Optional[List[int]]]):
        """Apply a :meth:`get_shading_assignments` snapshot onto *obj*.

        *obj* must share the snapshot's face indexing (same topology, or an op
        like ``polyBevel3`` that keeps the original faces' indices and only
        appends new ones — those new faces are base-coated with the dominant
        material). Restores per-face material after an in-place rebuild (e.g.
        ``delete(ch=True)``) or a hermetic-preview op drops it, which otherwise
        leaves a multi-material mesh unshaded (renders bright green).

        Single-material snapshots are applied as a whole-object assignment;
        multi-material snapshots are applied entirely through face components
        (see the body for why a whole-object base coat corrupts the result).
        """
        if not assignments:
            return
        shape = NodeUtils.get_shape(obj, no_intermediate=True)
        if not shape:
            return
        tf = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]

        per_face = {sg: f for sg, f in assignments.items() if f and cmds.objExists(sg)}
        whole = [
            sg for sg, f in assignments.items() if f is None and cmds.objExists(sg)
        ]

        # Pure single-material (no per-face overrides): a whole-object assignment
        # is the natural, cleanest form -- set it directly and return.
        if not per_face:
            for sg in whole:
                try:
                    cmds.sets(shape, edit=True, forceElement=sg)
                except Exception:
                    pass
            return

        if not tf:
            return

        # Multi-material: assign EVERY material as face components, never as a
        # whole-object set. After an in-place geometry rebuild (outMesh->inMesh +
        # delete(ch=True)) a whole-object forceElement followed by a per-face
        # split does NOT convert the whole assignment to a remainder -- it leaves
        # the object in BOTH the whole-object set AND the component sets at once.
        # That overlap is silently tolerated until the next poly op, which
        # resolves it to the whole-object material and drops every other one (a
        # multi-material mesh loses its extra materials -- the "neon green"
        # regression). Driving everything through components keeps the assignment
        # unambiguous so it survives the op.
        try:
            total = cmds.polyEvaluate(shape, face=True)
        except Exception:
            total = 0
        # The base material covers faces the snapshot doesn't (e.g. a bevel's new
        # chamfer faces, indexed past the originals): the whole-object SG if the
        # snapshot had one, else the per-face SG covering the most faces.
        base = whole[0] if whole else max(per_face, key=lambda s: len(per_face[s]))
        covered = set().union(*per_face.values())
        uncovered = [i for i in range(total) if i not in covered]

        def _force(faces, sg):
            try:
                cmds.sets([f"{tf}.f[{i}]" for i in faces], edit=True, forceElement=sg)
            except Exception:
                pass

        # Clean slate: park ALL faces on a neutral SG (as components) first, so
        # any stale/overlapping groups left by the rebuild are collapsed before
        # the real assignments land. A single range expression keeps this O(1) in
        # command size -- this runs on every preview refresh, so building one
        # string per face would lag a value-drag on a dense mesh.
        if total:
            try:
                cmds.sets(
                    f"{tf}.f[0:{total - 1}]",
                    edit=True,
                    forceElement="initialShadingGroup",
                )
            except Exception:
                pass
        for sg, faces in per_face.items():
            _force(faces, sg)
        if uncovered:
            _force(uncovered, base)

    # ------------------------------------------------------------------
    # Shared material-graph helpers
    # ------------------------------------------------------------------

    @staticmethod
    def create_file_node(image_path, name=None, color_space=None):
        """Create a ``file`` texture node with a wired ``place2dTexture``.

        Returns:
            tuple[str, str]: ``(file_node_name, place2d_node_name)``.
        """
        from pathlib import Path

        if name is None:
            name = Path(image_path).stem

        file_node = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(f"{file_node}.fileTextureName", image_path, type="string")

        if color_space:
            cmds.setAttr(f"{file_node}.colorSpace", color_space, type="string")

        place2d = cmds.shadingNode(
            "place2dTexture", asUtility=True, name=f"{name}_place2d"
        )

        connections = [
            ("outUV", "uvCoord"),
            ("outUvFilterSize", "uvFilterSize"),
            ("coverage", "coverage"),
            ("translateFrame", "translateFrame"),
            ("rotateFrame", "rotateFrame"),
            ("mirrorU", "mirrorU"),
            ("mirrorV", "mirrorV"),
            ("stagger", "stagger"),
            ("wrapU", "wrapU"),
            ("wrapV", "wrapV"),
            ("repeatUV", "repeatUV"),
            ("vertexUvOne", "vertexUvOne"),
            ("vertexUvTwo", "vertexUvTwo"),
            ("vertexUvThree", "vertexUvThree"),
            ("vertexCameraOne", "vertexCameraOne"),
            ("noiseUV", "noiseUV"),
            ("offset", "offset"),
            ("rotateUV", "rotateUV"),
        ]
        for src, dst in connections:
            cmds.connectAttr(f"{place2d}.{src}", f"{file_node}.{dst}", force=True)

        return file_node, place2d

    @staticmethod
    def create_shading_group(shader, name=None, assign_to=None):
        """Create a shading group for *shader* and optionally assign objects."""
        shader_name = str(shader)
        sg_name = name or f"{shader_name}_SG"

        sg = cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name=sg_name,
        )
        cmds.connectAttr(f"{shader_name}.outColor", f"{sg}.surfaceShader", force=True)

        if assign_to is not None:
            items = (
                assign_to if isinstance(assign_to, (list, tuple, set)) else [assign_to]
            )
            items = [str(i) for i in items]
            cmds.sets(items, edit=True, forceElement=sg)

        return sg

    STINGRAY_GRAPHS = {
        "none": "Standard.sfx",  # opaque
        "masked": "Standard_Masked.sfx",  # alpha test / cutout (clean VP2.0 preview, hard edges)
        "transparent": "Standard_Transparent.sfx",  # alpha blend (soft edges)
    }

    # Back-compat with the old experimental graph names.
    _STINGRAY_GRAPH_ALIASES = {
        "transparent_graph": "transparent",
        "lightweight": "transparent",
    }

    @classmethod
    def resolve_opacity_mode(cls, opacity_mode=None, opacity: bool = False) -> str:
        """Normalize an opacity-mode argument to a :attr:`STINGRAY_GRAPHS` key.

        Parameters:
            opacity_mode: ``None`` / ``"none"`` / ``"masked"`` / ``"transparent"``
                (legacy aliases accepted). Unknown values fall back to
                ``"none"``.
            opacity (bool): Legacy boolean; used only when *opacity_mode* is
                None. ``True`` → ``"transparent"``.

        Returns:
            str: One of ``"none"``, ``"masked"``, ``"transparent"``.
        """
        if opacity_mode is None:
            opacity_mode = "transparent" if opacity else "none"
        opacity_mode = cls._STINGRAY_GRAPH_ALIASES.get(opacity_mode, opacity_mode)
        return opacity_mode if opacity_mode in cls.STINGRAY_GRAPHS else "none"

    @classmethod
    def resolve_stingray_graph(cls, opacity_mode=None, opacity: bool = False):
        """Absolute path to the ShaderFX preset for *opacity_mode*.

        Returns:
            str | None: The ``.sfx`` path, or None when it isn't installed.
        """
        graph = os.path.join(
            EnvUtils.get_env_info("install_path"),
            "presets",
            "ShaderFX",
            "Scenes",
            "StingrayPBS",
            cls.STINGRAY_GRAPHS[cls.resolve_opacity_mode(opacity_mode, opacity)],
        )
        return graph if os.path.exists(graph) else None

    @classmethod
    def load_stingray_graph(cls, mat, opacity_mode=None, opacity: bool = False) -> bool:
        """Load the ShaderFX preset for *opacity_mode* onto a StingrayPBS node.

        The one place a ``.sfx`` reaches ``cmds.shaderfx`` — a StingrayPBS
        node's attributes come from its loaded graph, so every route that needs
        a particular slot set (network build, opacity enable, shader
        conversion) resolves the graph the same way.

        .. note:: ``loadGraph`` DROPS the node's existing connections; callers
           that need them preserved must snapshot first (see ``MatSnapshot``).

        Parameters:
            mat: StingrayPBS node.
            opacity_mode: See :meth:`resolve_opacity_mode`.
            opacity (bool): Legacy boolean form of *opacity_mode*.

        Returns:
            bool: True if a graph was loaded.
        """
        graph = cls.resolve_stingray_graph(opacity_mode, opacity)
        if not graph:
            return False
        EnvUtils.load_plugin("shaderFXPlugin")
        cmds.shaderfx(sfxnode=CoreUtils.short_name(mat), loadGraph=graph)
        return True

    @classmethod
    def create_stingray_shader(cls, name, opacity=False, opacity_mode=None):
        """Create a StingrayPBS shader by loading a ShaderFX preset graph.

        StingrayPBS node attrs are graph-dependent — a bare ``StingrayPBS``
        node has none of ``base_color`` / ``TEX_color_map`` / ``opacity`` etc.,
        so a graph must be loaded.

        Parameters:
            name: Shader node name.
            opacity: Deprecated bool. ``True`` → ``opacity_mode="transparent"``.
                Kept for backward compatibility.
            opacity_mode: One of:
                * ``None`` / ``"none"``: opaque, ``Standard.sfx``.
                * ``"masked"``: alpha cutout, ``Standard_Masked.sfx``.
                  Caller wires alpha to ``TEX_mask_map`` and tunes
                  ``mask_threshold``; clean VP2.0 preview, hard edges.
                * ``"transparent"``: alpha blend, ``Standard_Transparent.sfx``.
                  Caller wires alpha to scalar ``opacity``; soft edges,
                  but VP2.0 preview shows a faint tint over the quad.
        """
        EnvUtils.load_plugin("shaderFXPlugin")
        shader = NodeUtils.create_render_node(
            "StingrayPBS", name=name, create_shading_group=False
        )
        cls.load_stingray_graph(shader, opacity_mode, opacity)
        return shader

    @classmethod
    def find_by_mat_id(
        cls, material: str, objects: Optional[List[str]] = None, shell: bool = False
    ) -> List[str]:
        """Find objects or faces by the material ID."""
        material = str(material)

        if not cmds.objExists(material):
            print(f"Material '{material}' does not exist.")
            return []

        if cmds.nodeType(material) == "VRayMultiSubTex":
            raise TypeError(
                "Invalid material type. If material is a multimaterial, please select a submaterial."
            )

        shading_groups = cmds.listConnections(material, type="shadingEngine")
        if not shading_groups:
            print(f"No shading groups found for material '{material}'.")
            return []

        objs_with_material = []

        target_transforms = set()
        if objects:
            objects = _MatUtilsInternal._to_strs(objects)
            objects = cmds.ls(objects, long=True) or []

            for obj in objects:
                if cmds.objExists(obj):
                    if cmds.nodeType(obj) == "transform":
                        target_transforms.add(obj)
                    else:
                        parents = cmds.listRelatives(obj, parent=True, fullPath=True)
                        if parents:
                            target_transforms.add(parents[0])

        for sg in shading_groups:
            members = cmds.sets(sg, query=True, noIntermediate=True) or []
            members = cmds.ls(members, long=True) or []

            for member in members:
                node = member.split(".")[0] if "." in member else member

                if cmds.nodeType(node) == "transform":
                    transform = node
                else:
                    parents = cmds.listRelatives(node, parent=True, fullPath=True)
                    transform = parents[0] if parents else node

                if objects and transform not in target_transforms:
                    continue

                if shell:
                    if transform not in objs_with_material:
                        objs_with_material.append(transform)
                else:
                    objs_with_material.append(member)

        return objs_with_material

    @classmethod
    def find_unassigned(
        cls, objects: Optional[List[str]] = None, include_default: bool = True
    ) -> List[str]:
        """Objects carrying no material — the complement of :meth:`find_by_mat_id`.

        Maya has no true "no material" state for renderable geometry: new meshes
        join ``initialShadingGroup``, so they report the default shader
        (``standardSurface1`` on 2025+, ``lambert1`` before it) rather than
        nothing. Two distinct states therefore read as "unassigned" to a user:

        - **Default-shaded** — every shading engine the shape belongs to carries
          only a default material (``include_default``, the common case: geometry
          nobody has shaded yet).
        - **Orphaned** — the shape belongs to no shading engine at all (import
          artifacts, or an explicit ``sets -remove``). Always included; such
          geometry renders black and is otherwise hard to find.

        Object-level by design: a *partially* assigned mesh (some faces on a real
        material, the rest default) counts as assigned — its unshaded faces are a
        different question than "which objects did I forget to shade".

        Parameters:
            objects: Transforms, groups, shapes, or components to test. None (or
                empty) tests every renderable mesh in the scene — same convention
                as :meth:`find_by_mat_id`.
            include_default: Count default-shaded geometry as unassigned.
                False restricts the result to orphaned shapes.

        Returns:
            list[str]: Full-path transforms, in scene order.
        """
        if objects:
            # ``objectsOnly`` first so a component selection resolves to its object
            # (like find_by_mat_id, which accepts components); ``dag=True`` then
            # walks at-or-below each input, so groups reach their shapes.
            objs = cmds.ls([str(o) for o in objects], objectsOnly=True, long=True) or []
            if not objs:  # scoped to nothing — an argless ls would scan the scene
                return []
            shapes = (
                cmds.ls(objs, dag=True, type="mesh", noIntermediate=True, long=True)
                or []
            )
        else:
            shapes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []

        defaults = cls._default_material_names()
        unassigned = []
        for shape in shapes:
            shading_groups = set(
                cmds.listConnections(shape, type="shadingEngine") or []
            )
            if shading_groups and not include_default:
                continue
            mats = cls.get_mats(shape)
            # No materials at all -> orphaned. Otherwise unassigned only when every
            # material found is one of Maya's built-in defaults.
            if mats and not all(
                CoreUtils.short_name(m) in defaults for m in mats
            ):
                continue
            parents = cmds.listRelatives(shape, parent=True, fullPath=True)
            transform = parents[0] if parents else shape
            if transform not in unassigned:
                unassigned.append(transform)

        return unassigned

    @staticmethod
    @ptk.filter_results
    def collect_material_paths(
        materials: Optional[List[str]] = None,
        attributes: Optional[List[str]] = None,
        inc_mat_name: bool = False,
        inc_path_type: bool = False,
        resolve_full_path: bool = False,
    ) -> Union[List[str], List[Tuple[str, ...]]]:
        """Collects specified attributes file paths for given materials."""
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

    @staticmethod
    def remap_file_nodes(
        file_paths: List[str],
        target_dir: str,
        silent: bool = False,
        limit_to_nodes: Optional[List[str]] = None,
        as_strings: bool = True,
    ) -> List[str]:
        """Internal helper to remap file nodes to target_dir, preserving relative subfolders inside sourceimages.

        Returns a list of remapped file-node names (strings).  ``as_strings``
        is retained for API compatibility — strings are always returned.
        """
        sourceimages_dir = EnvUtils.get_env_info("sourceimages")
        sourceimages_dir_norm = os.path.normpath(sourceimages_dir).replace("\\", "/")

        if limit_to_nodes:
            node_names = _MatUtilsInternal._to_strs(limit_to_nodes)
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
            if file_path_norm.lower().startswith(sourceimages_dir_norm.lower()):
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

                        cmds.setAttr(
                            f"{fn_name}.fileTextureName", maya_path, type="string"
                        )
                        remapped_nodes.append(fn_name)
            else:
                if not silent:
                    cmds.warning(
                        f"// Skipping: No file node found for key '{key}' (original: {new_full_path})"
                    )
        return remapped_nodes

    @classmethod
    @CoreUtils.undoable
    def remap_texture_paths(
        cls,
        materials: Optional[List[str]] = None,
        new_dir: Optional[str] = None,
        silent: bool = False,
        file_nodes: Optional[List[str]] = None,
        objects: Optional[List[str]] = None,
        as_strings: bool = True,
    ) -> None:
        """Remaps file texture paths for materials to new_dir."""
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
    @CoreUtils.undoable
    def stage_textures_relative(
        cls,
        file_nodes: List[str],
        sourceimages: Optional[str] = None,
    ) -> Dict[str, str]:
        """Stage textures under sourceimages and store project-relative paths.

        Single per-node pass replacing the old copy-then-remap pair, whose
        basename-keyed handshake could rebind a node to an unrelated
        same-named file the copy step had refused, flattened valid
        ``sourceimages/sub/…`` paths, and remapped UDIM sets whose tiles were
        never copied.  Here every decision is made per node, atomically:

        - already-relative paths are left untouched;
        - absolute paths under sourceimages are relativized IN PLACE, with
          subfolders preserved;
        - external files (UDIM tile sets included, via token glob) are copied
          into sourceimages first — a destination collision is only reused
          when the CONTENT matches (size + partial hash); a different file
          with the same name is staged alongside it as ``<stem>_N.<ext>``,
          loudly, so the node is neither rebound to the wrong texture nor
          abandoned on an absolute path (which used to leak a cross-project
          path into both the scene and the export).  ``_N`` is reused when it
          already holds the same content, so repeat exports converge instead
          of stacking variants.

        The relative form is written with ``om.MPlug.setString`` — plain
        ``cmds.setAttr`` auto-expands a resolvable relative path back to
        absolute (verified in mayapy), which is why the old remap never
        actually stored relative paths.  A ``cmds.setAttr`` of the original
        value runs first as the undo anchor, so undo still restores the
        pre-conversion path.

        Parameters:
            file_nodes: ``file`` nodes to process.
            sourceimages: Override the project's sourceimages directory.

        Returns:
            {file_node: status} where status is one of ``relativized``,
            ``copied+relativized``, ``variant+relativized``,
            ``already-relative``, or ``skipped:<reason>``.
        """
        import shutil
        import glob as _glob
        import maya.api.OpenMaya as om

        results: Dict[str, str] = {}
        src_dir = sourceimages or EnvUtils.get_env_info("sourceimages")
        if not src_dir:
            cmds.warning("No sourceimages directory resolved — nothing staged.")
            return {n: "skipped:no-sourceimages" for n in file_nodes}
        src_dir = os.path.normpath(src_dir)
        os.makedirs(src_dir, exist_ok=True)

        _TOKEN_RE = re.compile(r"<udim>|<f>|<uvtile>", re.IGNORECASE)
        _MAX_VARIANTS = 99

        def _variant_name(basename: str, index: int) -> str:
            """``tex.png`` → ``tex_1.png``.  The suffix goes before the final
            extension, so a token pattern and its tiles transform identically
            (``tex.<UDIM>.png`` → ``tex.<UDIM>_1.png``, ``tex.1001.png`` →
            ``tex.1001_1.png``) and the stored path still expands onto the
            tiles actually staged."""
            if not index:
                return basename
            stem, ext = os.path.splitext(basename)
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

        def _store_relative(node: str, rel_form: str) -> bool:
            """Returns False when the plug can't be written (locked /
            referenced) — a per-node skip, never a task abort."""
            plug_name = f"{node}.fileTextureName"
            try:
                # Undo anchor: capture the original value on the queue…
                cmds.setAttr(plug_name, cmds.getAttr(plug_name), type="string")
                # …then force the relative form past the file node's
                # auto-expand.
                sel = om.MSelectionList()
                sel.add(node)
                plug = om.MFnDependencyNode(sel.getDependNode(0)).findPlug(
                    "fileTextureName", False
                )
                plug.setString(rel_form)
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
                results[node] = "already-relative"
                continue

            norm = os.path.normpath(expanded)
            if ptk.FileUtils.is_under(norm, src_dir):
                # Inside sourceimages — relativize in place, subfolders kept.
                rel = os.path.relpath(norm, src_dir).replace("\\", "/")
                _store_relative(node, f"sourceimages/{rel}")
                results[node] = "relativized"
                continue

            # External — stage the file(s) into the sourceimages root first.
            basename = os.path.basename(norm)
            directory = os.path.dirname(norm)
            has_token = bool(_TOKEN_RE.search(basename))
            if has_token:
                pattern = _TOKEN_RE.sub("*", basename)
                sources = sorted(_glob.glob(os.path.join(directory, pattern)))
            else:
                sources = [norm] if os.path.isfile(norm) else []

            if not sources:
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

            staged = _variant_name(basename, index)
            if index:
                cmds.warning(
                    f"A DIFFERENT '{basename}' already exists in sourceimages — "
                    f"'{node}' staged as '{staged}' rather than being rebound to "
                    "the wrong file (check whether the two are really distinct)."
                )
            _store_relative(node, f"sourceimages/{staged}")
            results[node] = "variant+relativized" if index else "copied+relativized"

        return results

    @staticmethod
    def is_duplicate_material(material1: str, material2: str) -> bool:
        """Check if two materials are duplicates based on their textures."""
        material1 = str(material1)
        material2 = str(material2)
        history1 = cmds.listHistory(material1) or []
        history2 = cmds.listHistory(material2) or []
        textures1 = set(cmds.listConnections(history1, type="file") or [])
        textures2 = set(cmds.listConnections(history2, type="file") or [])
        return textures1 == textures2

    @classmethod
    def find_materials_with_duplicate_textures(
        cls,
        materials: Optional[List[str]] = None,
        strict: bool = False,
        verify: bool = True,
    ) -> Dict[str, List[str]]:
        """Find duplicate materials based on their texture file names or full paths.

        Two-phase.  Phase 1 groups CANDIDATES by a cheap fingerprint —
        ``(node type, {(attribute, texture id)})`` where the non-strict
        texture id is the lowercased basename stem, so same-named textures
        from different folders (``brick/albedo.png`` vs ``wood/albedo.png``)
        and same-texture-different-tiling setups land in one group.  Phase 2
        (``verify=True``, the default) pairwise-verifies every candidate
        against its group's keeper before it is reported: equal unconnected
        scalar attribute values, identical place2d placement and color space
        per texture slot, and texture CONTENT identity (size + partial hash)
        when the stored paths differ.  Only verified duplicates are returned
        — the verification is what makes the result safe to feed
        :meth:`reassign_duplicate_materials`' destructive merge.

        Parameters:
            materials: Materials to scan.  None scans every scene material.
            strict: Fingerprint on the full lowercased path instead of the
                basename stem (narrows phase 1's candidate net).
            verify: Skip phase 2 when False — candidates are returned
                unverified (the pre-2026-08 behavior; false-positive-prone).
        """

        def _texture_id(path: str) -> str:
            if strict:
                return path.lower()
            return os.path.splitext(os.path.basename(path))[0].lower()

        def _parent_attr(plug: str) -> str:
            parts = plug.split(".", 1)
            if len(parts) < 2:
                return plug
            attr_path = parts[1]
            attr_path = re.sub(r"\[\d+\]", "", attr_path)
            root_attr = attr_path.split(".")[0]
            root_attr = re.sub(r"[RGBXYZA]$", "", root_attr)
            return root_attr or attr_path.split(".")[0]

        if materials is None:
            materials = cmds.ls(mat=True) or []
        else:
            materials = [str(m) for m in materials]
            materials = cmds.ls(materials, mat=True) or []

        material_data = {}
        slot_maps: Dict[str, Dict[tuple, List[str]]] = {}
        for material in materials:
            mat_type = cmds.nodeType(material)

            history = cmds.listHistory(material, pruneDagObjects=True) or []
            file_nodes = cmds.ls(history, type="file") or []
            if not file_nodes:
                continue

            history_set = set(history)

            attr_texture_pairs = []
            slots: Dict[tuple, List[str]] = {}
            for file_node in file_nodes:
                if not cmds.objExists(f"{file_node}.fileTextureName"):
                    continue
                path = cmds.getAttr(f"{file_node}.fileTextureName")
                if not path:
                    continue
                tex_id = _texture_id(path)

                visited = set()
                frontier = [file_node]
                mat_attrs = set()
                while frontier:
                    node = frontier.pop()
                    if node in visited:
                        continue
                    visited.add(node)
                    dest_plugs = (
                        cmds.listConnections(
                            node,
                            source=False,
                            destination=True,
                            plugs=True,
                        )
                        or []
                    )
                    for plug in dest_plugs:
                        plug_node = plug.split(".")[0]
                        if plug_node == material:
                            mat_attrs.add(_parent_attr(plug))
                        elif plug_node not in visited and plug_node in history_set:
                            frontier.append(plug_node)

                if mat_attrs:
                    for attr in mat_attrs:
                        attr_texture_pairs.append((attr, tex_id))
                        slots.setdefault((attr, tex_id), []).append(file_node)
                else:
                    attr_texture_pairs.append(("_unresolved", tex_id))
                    slots.setdefault(("_unresolved", tex_id), []).append(file_node)

            if not attr_texture_pairs:
                continue

            fingerprint = (mat_type, frozenset(attr_texture_pairs))
            material_data[material] = fingerprint
            slot_maps[material] = slots

        seen = {}
        for material, fingerprint in material_data.items():
            match_found = False
            for seen_fp, seen_list in seen.items():
                if fingerprint == seen_fp:
                    seen_list.append(material)
                    match_found = True
                    break
            if not match_found:
                seen[fingerprint] = [material]

        duplicates = {}
        for materials_list in seen.values():
            if len(materials_list) > 1:
                materials_list.sort(key=lambda x: (len(x), x))
                original = materials_list[0]
                dups = materials_list[1:]
                if verify:
                    dups = [
                        d
                        for d in dups
                        if cls._materials_are_verified_duplicates(
                            original, d, slot_maps[original], slot_maps[d]
                        )
                    ]
                if dups:
                    duplicates[original] = dups

        if duplicates:
            print(f"{len(duplicates)} Duplicate material groups found:")
            for original, dup_list in duplicates.items():
                print(f"Original: {original}, Duplicates: {dup_list}")
        return duplicates

    @classmethod
    @CoreUtils.undoable
    def reassign_duplicate_materials(
        cls,
        materials: Optional[List[str]] = None,
        delete: bool = False,
        strict: bool = False,
        verify: bool = True,
    ) -> None:
        """Find duplicate materials, remove duplicates, and reassign them to the original material.

        ``verify`` (default True) gates the merge on the pairwise
        verification pass — see :meth:`find_materials_with_duplicate_textures`.
        Only pass False deliberately: unverified candidates include
        same-basename-different-content and same-texture-different-tiling
        near-misses, and this method deletes what it merges.
        """
        if materials is not None:
            valid_objects = []
            for m in materials:
                m = str(m)
                if cmds.objExists(m):
                    valid_objects.append(m)
                else:
                    cmds.warning(f"Object '{m}' does not exist or is not valid.")

            collected_materials = cmds.ls(valid_objects, mat=True) or []
            if not collected_materials:
                cmds.warning(f"No valid materials found in {materials}")
                return
        else:
            collected_materials = cmds.ls(mat=True) or []

        duplicate_to_original = cls.find_materials_with_duplicate_textures(
            collected_materials, strict=strict, verify=verify
        )
        duplicates_to_delete = []
        for original, duplicates in duplicate_to_original.items():
            original_sgs = cmds.listConnections(original, type="shadingEngine")
            if not original_sgs:
                continue
            original_sg = original_sgs[0]

            for duplicate in duplicates:
                try:
                    duplicate_sgs = cmds.listConnections(
                        duplicate, type="shadingEngine"
                    )
                    if not duplicate_sgs:
                        continue

                    for dup_sg in duplicate_sgs:
                        members = cmds.sets(dup_sg, q=True)
                        if members:
                            cmds.sets(members, edit=True, forceElement=original_sg)
                            print(
                                f"Reassigned material from {duplicate} to {original} on members: {members}"
                            )
                    duplicates_to_delete.append(duplicate)
                except Exception as e:
                    print(f"Error processing material {duplicate}: {e}")
                    continue

        if delete:
            for duplicate in duplicates_to_delete:
                try:
                    if cmds.objExists(duplicate):
                        cmds.delete(duplicate)
                        print(f"Deleted duplicate material: {duplicate}")
                except Exception as e:
                    print(f"Error deleting material {duplicate}: {e}")

    @staticmethod
    def filter_materials_by_objects(
        objects: List[str],
        as_strings: bool = True,
        include_displacement: bool = False,
    ) -> List[str]:
        """Filter materials assigned to the given objects."""
        return MatUtils.get_mats(
            objects, as_strings=as_strings, include_displacement=include_displacement
        )

    @staticmethod
    def reload_textures(
        materials=None,
        inc=None,
        exc=None,
        log=False,
        refresh_viewport=False,
        refresh_hypershade=False,
        texture_types: Optional[List[str]] = None,
    ):
        """Reloads textures connected to specified materials with inclusion/exclusion filters."""
        if texture_types is None:
            texture_types = ["file", "aiImage", "pxrTexture", "imagePlane"]

        if materials is None:
            materials = cmds.ls(mat=True) or []
        else:
            materials = cmds.ls(_MatUtilsInternal._to_strs(materials), mat=True) or []

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
                cmds.setAttr(f"{fn}.fileTextureName", file_path, type="string")
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
    def move_texture_files(
        cls,
        found_files: List[Union[str, Tuple[str, str]]],
        new_dir: str,
        delete_old: bool = False,
        create_dir: bool = True,
        per_file_timeout: float = 120.0,
        max_workers: int = 8,
        progress_callback: Optional[Callable[[int, int, str], bool]] = None,
    ) -> List[Tuple[str, str]]:
        """Move or copy found texture files to a new directory.

        Returns the list of (src, dst) pairs that completed successfully
        (including those skipped as already up-to-date when delete_old is
        False). Failed/timed-out files are omitted.

        per_file_timeout: max seconds to wait for any single copy before
            abandoning the pool. Python cannot kill a worker thread blocked
            inside shutil.copy2, so on timeout we stop dispatching, cancel
            pending futures, and shutdown(wait=False) — in-flight workers
            leak until the OS unblocks them (or Maya exits). The win is
            that Maya gets the UI back instead of hanging forever.
        progress_callback: optional fn(done, total, last_filename) called
            from the main thread after each future completes. Return False
            to request early termination. Exceptions raised from the
            callback are swallowed and treated as "keep going".
        """
        import shutil
        import filecmp
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
            dst_path = os.path.join(new_dir, filename)
            # Skip when the destination already matches the source.
            # filecmp.cmp(shallow=True) compares st_mode + st_size + st_mtime;
            # this avoids rewriting hundreds of files that the user already
            # copied previously, which would otherwise force a cloud-sync
            # client to re-hash and re-upload every one of them.
            if not delete_old and os.path.exists(dst_path):
                try:
                    if filecmp.cmp(src_path, dst_path, shallow=True):
                        return src_path, dst_path, True  # was_skipped
                except OSError:
                    pass  # fall through to copy
            shutil.copy2(src_path, dst_path)
            if delete_old:
                os.remove(src_path)
            return src_path, dst_path, False

        workers = max(1, min(max_workers, len(src_entries)))
        copied: List[Tuple[str, str]] = []
        skipped = 0
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
                    src_p, dst_p, was_skipped = future.result(timeout=per_file_timeout)
                    copied.append((src_p, dst_p))
                    if was_skipped:
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

        print(
            f"// Result: {len(copied)} texture(s) ok "
            f"({skipped} already up-to-date, "
            f"{len(errors)} errors, "
            f"{len(timed_out)} timed out"
            f"{', cancelled' if cancelled and not timed_out else ''})."
        )
        return copied

    @classmethod
    def copy_textures_to_sourceimages(
        cls,
        objects: Optional[List[str]] = None,
        materials: Optional[List[str]] = None,
        file_nodes: Optional[List[str]] = None,
        sourceimages_dir: Optional[str] = None,
        delete_old: bool = False,
    ) -> List[Tuple[str, str]]:
        """Copy referenced textures that live outside ``sourceimages`` into it.

        This is the prerequisite for converting texture paths to relative: a
        project-relative path only resolves if the file physically lives under
        ``sourceimages``.  Remapping an external texture to a relative path
        *without* first copying the file in silently breaks the link — the
        exported asset then points at a texture that isn't there.  Use this
        before :meth:`remap_texture_paths` whenever the destination is
        ``sourceimages`` and inputs may be stored elsewhere.

        Only files that exist on disk and are not already under
        ``sourceimages`` are copied.  A file whose basename already exists in
        ``sourceimages`` is left untouched: identical size is treated as the
        same asset (the relative path will resolve to it), while a different
        size is a name collision — skipped with a warning rather than
        clobbering a different texture or silently rebinding to the wrong one.
        UDIM/sequence tokens (``<udim>``/``<f>``) are skipped (no single file
        to copy); the token is preserved by the subsequent remap.

        Parameters:
            objects/materials/file_nodes: Scope to resolve textures from. When
                all are None, every ``file`` node in the scene is considered.
            sourceimages_dir: Destination; defaults to the project's
                ``sourceimages`` directory.
            delete_old: Forwarded to :meth:`move_texture_files` — True moves
                the external file in instead of copying it.

        Returns:
            The (src, dst) pairs that were copied/moved (empty when nothing
            needed copying).
        """
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
            lower = norm.lower()
            if "<udim>" in lower or "<f>" in lower:
                continue  # multi-tile token — no single file to copy
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
    def find_texture_files(
        cls,
        objects: Optional[List[str]] = None,
        source_dir: str = "",
        recursive: bool = True,
        return_dir: bool = False,
        quiet: bool = False,
        file_nodes: Optional[List[str]] = None,
        materials: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Union[str, Tuple[str, str]]]:
        """Find texture files for given objects' materials inside source_dir."""
        if not os.path.isdir(source_dir):
            cmds.warning(f"Invalid source directory: {source_dir}")
            return []

        if file_nodes and not objects and not materials:
            texture_nodes = _MatUtilsInternal._to_strs(file_nodes)
        else:
            scope = cls._resolve_texture_targets(
                objects=objects,
                materials=materials,
                file_nodes=file_nodes,
                fallback_to_scene=False,
                as_strings=True,
            )
            texture_nodes = scope["file_nodes"]

        if not texture_nodes:
            cmds.warning(
                "No objects, materials, or file nodes provided to find textures."
            )
            return []

        import re as _re

        target_filenames = set()
        udim_patterns = []
        for node_name in texture_nodes:
            try:
                path = cmds.getAttr(f"{node_name}.fileTextureName")
                if path:
                    filename = os.path.basename(path)
                    if filename:
                        lower_name = filename.lower()
                        if "<udim>" in lower_name:
                            pattern = _re.escape(lower_name).replace(
                                _re.escape("<udim>"), r"\d{4}"
                            )
                            udim_patterns.append(_re.compile(pattern))
                        else:
                            target_filenames.add(lower_name)
            except Exception:
                continue

        if not target_filenames and not udim_patterns:
            cmds.warning("No texture names available for lookup.")
            return []

        results = []

        for root, dirs, files in os.walk(source_dir):
            # Prune sync caches / system / VCS dirs in-place so os.walk
            # never descends into them. Skip noise + stale duplicates.
            dirs[:] = [d for d in dirs if d not in _TEXTURE_WALK_SKIP_DIRS]

            if progress_callback:
                progress_callback(len(results), 0, f"Scanning: {root}")

            for file in files:
                lower_file = file.lower()
                matched = lower_file in target_filenames
                if not matched and udim_patterns:
                    matched = any(p.fullmatch(lower_file) for p in udim_patterns)
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
    @CoreUtils.undoable
    def migrate_textures(
        cls,
        materials: Optional[List[str]] = None,
        old_dir: Optional[str] = None,
        new_dir: Optional[str] = None,
        silent: bool = False,
        delete_old: bool = False,
        objects: Optional[List[str]] = None,
        file_nodes: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[int, int, str], bool]] = None,
    ) -> None:
        """Copies texture files from an old directory to a new one."""
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

    @staticmethod
    def move_unused_textures(source_dir: str = None, output_dir: str = None) -> None:
        """Move unused textures to a specified directory."""
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
            os.path.basename(path[0]) for path in MatUtils.collect_material_paths()
        }

        unused_textures = all_textures - used_textures

        print(f"Moving {len(unused_textures)} to: {output_dir} ..")
        for texture in unused_textures:
            src_path = os.path.join(project_sourceimages, texture)
            dest_path = os.path.join(unused_folder, texture)
            shutil.move(src_path, dest_path)
            print(f"Moved {texture} to {unused_folder}")

    @staticmethod
    def get_mat_swatch_icon(
        mat: Union[str, object],
        size: List[int] = [20, 20],
        fallback_to_blank: bool = True,
    ) -> object:
        """Get an icon with a color fill matching the given material's RGB value."""
        from qtpy.QtGui import QPixmap, QColor, QIcon

        try:
            matName = str(mat)

            mat_type = cmds.nodeType(matName)
            if mat_type == "standardSurface":
                color_attr = "baseColor"
            else:
                color_attr = "color"

            r = int(cmds.getAttr(f"{matName}.{color_attr}R") * 255)
            g = int(cmds.getAttr(f"{matName}.{color_attr}G") * 255)
            b = int(cmds.getAttr(f"{matName}.{color_attr}B") * 255)
            pixmap = QPixmap(size[0], size[1])
            pixmap.fill(QColor.fromRgb(r, g, b))
        except Exception:
            if fallback_to_blank:
                pixmap = QPixmap(size[0], size[1])
                pixmap.fill(QColor(255, 255, 255, 0))
            else:
                raise

        return QIcon(pixmap)

    @staticmethod
    @CoreUtils.undoable
    def convert_bump_to_normal(
        bump_file_node,
        output_path: Optional[str] = None,
        intensity: float = 1.0,
        format_type: str = "opengl",
        create_file_node: bool = True,
        node_name: Optional[str] = None,
    ) -> Optional[str]:
        """Convert a bump/height file node's texture to a normal map on disk.

        The image conversion is delegated to
        :meth:`pythontk.MapFactory.convert_bump_to_normal` (Sobel-based,
        writes a real normal map next to the source unless ``output_path``
        is given). The previous implementation built a bump2d/reverse
        shading network that never produced a file and inverted all three
        channels for "directx" — it was unused and has been replaced.

        Parameters:
            bump_file_node: A ``file`` node whose ``fileTextureName`` points
                at the bump/height texture.
            output_path: Explicit output file path. Defaults to a
                ``_Normal_<Format>`` sibling of the source.
            intensity: Height depth multiplier passed to the converter.
            format_type: ``"opengl"`` or ``"directx"``.
            create_file_node: When True (default), also create a wired
                ``file``/``place2dTexture`` pair for the result with
                colorSpace ``Raw``.

        Returns:
            Optional[str]: The created file-node name, or the written image
            path when ``create_file_node=False``; ``None`` on failure.
        """
        bump_node = str(bump_file_node)
        if not cmds.objExists(bump_node):
            raise ValueError(f"Bump file node {bump_file_node} does not exist")
        if cmds.nodeType(bump_node) != "file":
            raise ValueError(f"Node {bump_file_node} is not a file node")
        if format_type not in ("opengl", "directx"):
            raise ValueError("format_type must be 'opengl' or 'directx'")

        source = cmds.getAttr(f"{bump_node}.fileTextureName") or ""
        if not source or not os.path.exists(source):
            raise ValueError(
                f"Bump file node {bump_node} has no existing texture file "
                f"(fileTextureName={source!r})"
            )

        try:
            written = ptk.MapFactory.convert_bump_to_normal(
                source,
                output_path=output_path,
                intensity=intensity,
                output_format=format_type,
                save=True,
            )
        except Exception as e:
            cmds.warning(f"Bump-to-normal conversion failed for {source}: {e}")
            return None

        if not create_file_node:
            return written

        base_name = node_name or f"{CoreUtils.short_name(bump_node)}_normal"
        normal_file_node, _place2d = MatUtils.create_file_node(
            written, name=base_name, color_space="Raw"
        )
        # Normal data is non-color; never treat its alpha as luminance.
        cmds.setAttr(f"{normal_file_node}.alphaIsLuminance", False)
        return normal_file_node

    @staticmethod
    def validate_normal_map_setup(
        normal_file_node,
        material=None,
    ) -> Dict[str, Any]:
        """Validate normal map file node setup and provide recommendations."""
        normal_node = str(normal_file_node)
        if not cmds.objExists(normal_node):
            return {
                "valid": False,
                "error": f"Normal file node {normal_file_node} does not exist",
            }
        if cmds.nodeType(normal_node) != "file":
            return {
                "valid": False,
                "error": f"Node {normal_file_node} is not a file node",
            }

        results = {
            "valid": True,
            "warnings": [],
            "recommendations": [],
            "color_space": None,
            "connected_to_normal": False,
            "file_exists": False,
        }

        color_space = cmds.getAttr(f"{normal_node}.colorSpace") or ""
        results["color_space"] = color_space
        if color_space.lower() not in ["raw", "linear", "utility - raw"]:
            results["warnings"].append(
                f"Color space is '{color_space}'. Normal maps should use 'Raw' or 'Linear' "
                "to avoid gamma correction that corrupts normal data."
            )
            results["recommendations"].append("Set colorSpace to 'Raw'")

        file_path = cmds.getAttr(f"{normal_node}.fileTextureName") or ""
        if file_path and os.path.exists(file_path):
            results["file_exists"] = True
        elif file_path:
            results["warnings"].append(f"Normal map file does not exist: {file_path}")

        if material:
            material = str(material)
            if not cmds.objExists(material):
                results["warnings"].append(f"Material {material} does not exist")
            else:
                connections = (
                    cmds.listConnections(
                        f"{normal_node}.outColor",
                        plugs=True,
                        source=False,
                        destination=True,
                    )
                    or []
                )
                normal_connections = [
                    c
                    for c in connections
                    if "normal" in c.lower() or "bump" in c.lower()
                ]

                if normal_connections:
                    results["connected_to_normal"] = True
                else:
                    results["warnings"].append(
                        "Normal map not connected to material normal/bump input"
                    )
                    results["recommendations"].append(
                        "Connect to material normalCamera or bump input"
                    )

        return results

    @staticmethod
    def graph_materials(
        materials: Union[str, List[str], object], mode: str = "showUpAndDownstream"
    ) -> None:
        """Open the Hypershade and graph the specified materials."""
        if not materials:
            return

        materials_list = _MatUtilsInternal._to_strs(materials)
        cmds.select(materials_list)

        mel.eval("HypershadeWindow")

        cmds.evalDeferred(
            f'maya.mel.eval(\'hyperShadePanelGraphCommand "hyperShadePanel1" "{mode}"\')'
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
