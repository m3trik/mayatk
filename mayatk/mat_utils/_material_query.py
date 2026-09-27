# !/usr/bin/python
# coding=utf-8
"""Material queries behind :class:`mayatk.MatUtils`.

Which materials a selection, scope or scene holds (through the shading
engines, so a shader built with ``createNode`` still counts), the role
classification that tells a shader from a utility node, the ``file``-node
inventory and scope resolution every texture operation starts from, and the
per-material / per-texture info report.
Reached through :class:`mayatk.MatUtils`; nothing here is called directly.
"""

import os
from typing import List, Dict, Any, Optional

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils


class _MaterialQueryInternal:
    """Private helpers and ``MatUtils`` method bodies; see the module docstring."""

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
            names = _MaterialQueryInternal._to_strs(nodes)
            return cmds.ls(names, long=True, flatten=True) or []

        no_scope_passed = objects is None and materials is None and file_nodes is None

        resolved_objects = to_long(objects) if objects else []

        resolved_materials_set = set()
        if materials:
            mats = cmds.ls(to_long(materials), mat=True, long=True) or []
            resolved_materials_set.update(mats)

        if resolved_objects:
            found_mats = cls.get_mats(resolved_objects)
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

    @classmethod
    def _get_mats(cls, objs, as_strings, mat_type, include_displacement):
        """Body of :meth:`MatUtils.get_mats`."""
        sg_slots = list(cls._SG_SHADER_SLOTS)
        if include_displacement:
            sg_slots += list(cls._SG_EXTRA_SHADER_SLOTS)

        def _sg_mats(sg):
            return cls._sg_shaders(sg, sg_slots)

        if objs is None:
            objs = cmds.ls(selection=True, long=True) or []

        if not objs:
            return []

        if not isinstance(objs, (list, tuple, set)):
            objs = [objs]

        objs = [str(o) for o in objs]

        target_objs = cmds.ls(objs, long=True, flatten=True) or []
        # Ordered sets (dict keys), not sets: a set iterates in the process's
        # hash order, so the same objects listed their materials differently
        # every run -- and the exporter's texture pass walks this list.
        mats = {}

        faces = [obj for obj in target_objs if ".f[" in obj]
        objects = [obj for obj in target_objs if ".f[" not in obj]

        if objects:
            potential_mats = cmds.ls(objects, mat=True, long=True) or []
            if potential_mats:
                mats.update(dict.fromkeys(potential_mats))
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
                shading_engines = {}
                for shape in shapes:
                    sgs = cmds.listSets(object=shape, type=1) or []
                    if not sgs:
                        sgs = cmds.listConnections(shape, type="shadingEngine") or []
                    shading_engines.update(dict.fromkeys(sgs))

                for sg in shading_engines:
                    mats.update(dict.fromkeys(_sg_mats(sg)))

        if faces:
            for face in faces:
                face_sgs = cmds.listSets(object=face, type=1) or []
                if face_sgs:
                    for sg in face_sgs:
                        mats.update(dict.fromkeys(_sg_mats(sg)))
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
                            mats.update(dict.fromkeys(_sg_mats(sg)))

        if mat_type:
            mats = {m: None for m in mats if m and cmds.nodeType(m) == mat_type}

        return list(mats)

    @classmethod
    def _get_texture_info(cls, objects, materials, file_nodes, texture_names):
        """Body of :meth:`MatUtils.get_texture_info`."""
        paths = cls.get_texture_paths(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            texture_names=texture_names,
        )
        return ptk.ImgUtils.get_image_info(paths)

    @classmethod
    def _get_mat_info(
        cls,
        materials,
        objects,
        optimize_check,
        progress_callback,
        exclude_defaults,
        exclude_unassigned,
        include_textures,
        include_image_metadata,
        **optimize_kwargs,
    ):
        """Body of :meth:`MatUtils.get_mat_info`."""
        # Resolve the material scope. Materials passed explicitly take
        # precedence over object-derived materials; both fall through to the
        # entire scene when nothing was supplied. An explicit empty iterable
        # means "no scope" and short-circuits to an empty result rather than
        # letting ``cmds.ls(mat=True)`` fall back to the whole scene.
        if materials is not None:
            mat_strs = cls._to_strs(materials)
            resolved_materials = (
                sorted({m for m in (cmds.ls(mat_strs, mat=True) or []) if m})
                if mat_strs
                else []
            )
        elif objects is not None:
            obj_strs = cls._to_strs(objects)
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

    @classmethod
    def _format_texture_info_text(cls, info_list):
        """Body of :meth:`MatUtils.format_texture_info_text`."""
        return ptk.MatReport.format_texture_info_text(info_list)

    @classmethod
    def _format_texture_info_html(cls, info_list):
        """Body of :meth:`MatUtils.format_texture_info_html`."""
        return ptk.MatReport.format_texture_info_html(info_list)

    @classmethod
    def _format_mat_info_text(cls, records):
        """Body of :meth:`MatUtils.format_mat_info_text`."""
        return ptk.MatReport.format_mat_info_text(records)

    @classmethod
    def _format_mat_info_html(cls, records):
        """Body of :meth:`MatUtils.format_mat_info_html`."""
        return ptk.MatReport.format_mat_info_html(records)

    @classmethod
    def _get_scene_mats(
        cls,
        inc,
        exc,
        node_type,
        sort,
        as_dict,
        exclude_defaults,
        exclude_utility_nodes,
        exc_classification,
        **filter_kwargs,
    ):
        """Body of :meth:`MatUtils.get_scene_mats`."""
        # Maya's own list, plus anything wired into a shading engine that never
        # made it into defaultShaderList1 (see _shading_engine_shaders) — a
        # material assigned to geometry must be listed whichever way it was built.
        mat_list = cmds.ls(materials=True, flatten=True) or []
        seen = set(mat_list)
        for shader in cls._shading_engine_shaders():
            if shader not in seen:
                seen.add(shader)
                mat_list.append(shader)

        if exclude_defaults and mat_list:
            default_nodes = cls._default_material_names()
            mat_list = [
                m for m in mat_list if CoreUtils.short_name(m) not in default_nodes
            ]

        if exclude_utility_nodes and mat_list:
            mat_list = [m for m in mat_list if not cls._is_utility_node(m)]

        if exc_classification and mat_list:
            # Keep the material when none of its classification tokens matches.
            mat_list = [
                m
                for m in mat_list
                if not ptk.filter_list(
                    cls._classification_tokens(m), inc=exc_classification
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

        return cls._unique_name_map(mat_list) if as_dict else mat_list

    @classmethod
    def _get_connected_shaders(cls, file_nodes):
        """Body of :meth:`MatUtils.get_connected_shaders`."""
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

    @classmethod
    def _get_mats_by_scope(cls, scope, mat_type):
        """Body of :meth:`MatUtils.get_mats_by_scope`."""
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

    @classmethod
    def _get_file_nodes(cls, materials, raw, return_type, exc_classification):
        """Body of :meth:`MatUtils.get_file_nodes`."""
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
    def _get_fav_mats():
        """Body of :meth:`MatUtils.get_fav_mats`."""
        import os.path
        import maya.app.general.tlfavorites as _fav

        # Maya's own prefs dir: honors MAYA_APP_DIR, and is ~/maya/<ver>/prefs
        # on Linux, where %USERPROFILE% never expanded.
        path = os.path.join(
            cmds.internalVar(userPrefDir=True), "renderNodeTypeFavorites"
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

    @classmethod
    def _filter_materials_by_objects(cls, objects, as_strings, include_displacement):
        """Body of :meth:`MatUtils.filter_materials_by_objects`."""
        return cls.get_mats(objects, include_displacement=include_displacement)

    @staticmethod
    def _get_mat_swatch_icon(mat, size, fallback_to_blank):
        """Body of :meth:`MatUtils.get_mat_swatch_icon`."""
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
