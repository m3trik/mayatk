# !/usr/bin/python
# coding=utf-8
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
from mayatk.core_utils._core_utils import CoreUtils, short_name as _short_name
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils._env_utils import EnvUtils

# Directory names pruned during recursive texture searches. Keeps the walk
# off Dropbox/OneDrive sync caches, Windows system folders, version control
# noise, and Python bytecode caches — all of which can hold stale duplicates
# of legitimate textures that would otherwise pollute the candidate set.
_TEXTURE_WALK_SKIP_DIRS = frozenset({
    ".dropbox.cache", ".dropbox",
    "$RECYCLE.BIN", "System Volume Information",
    ".git", ".svn", ".hg",
    "node_modules", "__pycache__",
})


def _to_strs(nodes) -> List[str]:
    """Coerce a node/node/iterable to a list of plain string names."""
    if nodes is None:
        return []
    if isinstance(nodes, (list, tuple, set)):
        return [str(n) for n in nodes if n is not None]
    return [str(nodes)]


class MatUtilsInternals(ptk.HelpMixin):
    """Internal helper utilities shared across MatUtils operations."""

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
            names = _to_strs(nodes)
            return cmds.ls(names, long=True, flatten=True) or []

        no_scope_passed = (
            objects is None and materials is None and file_nodes is None
        )

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
    def _paths_from_file_nodes(
        file_nodes: List[Any], absolute: bool = False
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

            abs_path = (
                os.path.abspath(os.path.join(project_sourceimages, file_path))
                if not os.path.isabs(file_path)
                else os.path.abspath(file_path)
            )

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
        if not sources:
            return None

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
                result = MatUtilsInternals.get_texture_file_node(node, inp, _depth + 1)
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


class MatUtils(MatUtilsInternals):
    @staticmethod
    def resolve_path(path: str) -> Union[str, None]:
        """Resolves a texture path by expanding env vars, checking workspace, and handling UDIMs."""
        if not path:
            return None

        def check_exists(p):
            check_p = p.replace("<UDIM>", "1001") if "<UDIM>" in p else p
            return os.path.exists(check_p)

        expanded = os.path.expandvars(path)
        if check_exists(expanded):
            return expanded

        try:
            ws_path = cmds.workspace(expandName=path)
            if check_exists(ws_path):
                return ws_path
        except Exception:
            pass

        try:
            ws_root = cmds.workspace(q=True, rd=True)
            source_images = os.path.join(ws_root, "sourceimages")
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

        Returns:
            list[str]: Materials assigned to the objects or components (duplicates removed).
        """
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

            shapes = cmds.listRelatives(objects, shapes=True, fullPath=True) or []
            for obj in objects:
                if cmds.nodeType(obj) in ["mesh", "nurbsSurface", "subdiv"]:
                    shapes.append(obj)

            shapes = list(set(shapes))

            if shapes:
                shading_engines = set()
                for shape in shapes:
                    sgs = cmds.listSets(object=shape, type=1) or []
                    if not sgs:
                        sgs = cmds.listConnections(shape, type="shadingEngine") or []
                    shading_engines.update(sgs)

                for sg in shading_engines:
                    connections = (
                        cmds.listConnections(
                            f"{sg}.surfaceShader", source=True, destination=False
                        )
                        or []
                    )
                    mats.update(connections)

        if faces:
            for face in faces:
                face_sgs = cmds.listSets(object=face, type=1) or []
                if face_sgs:
                    for sg in face_sgs:
                        connections = (
                            cmds.listConnections(
                                f"{sg}.surfaceShader", source=True, destination=False
                            )
                            or []
                        )
                        mats.update(connections)
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
                            connections = (
                                cmds.listConnections(
                                    f"{sg}.surfaceShader",
                                    source=True,
                                    destination=False,
                                )
                                or []
                            )
                            mats.update(connections)

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
            if cmds.nodeType(obj) in ("mesh", "nurbsSurface", "subdiv"):
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

        objects = cmds.ls(_to_strs(objects), long=True) or []
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

    @classmethod
    def get_texture_paths(
        cls,
        objects: Optional[List[Any]] = None,
        materials: Optional[List[Any]] = None,
        file_nodes: Optional[List[Any]] = None,
        texture_names: Optional[List[str]] = None,
        absolute: bool = True,
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
            mat_strs = _to_strs(materials)
            resolved_materials = (
                sorted({m for m in (cmds.ls(mat_strs, mat=True) or []) if m})
                if mat_strs
                else []
            )
        elif objects is not None:
            obj_strs = _to_strs(objects)
            resolved_materials = (
                sorted(cls.get_mats(obj_strs)) if obj_strs else []
            )
        else:
            resolved_materials = (
                cls.get_scene_mats(sort=True, exclude_defaults=False) or []
            )

        if exclude_defaults and resolved_materials:
            default_nodes = cls._default_material_names()
            resolved_materials = [
                m for m in resolved_materials if _short_name(m) not in default_nodes
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
                    size_bytes = (
                        os.path.getsize(path) if os.path.exists(path) else None
                    )

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
                            tex_entries.append({
                                "file_node": fn,
                                "path": path,
                                "name": os.path.basename(path),
                                "size": size_bytes,
                                "error": f"Failed to read image: {e}",
                            })
                            continue

                    info: Dict[str, Any] = {
                        "file_node": fn,
                        "path": path,
                        "name": os.path.basename(path),
                        "size": size_bytes,
                    }
                    if include_image_metadata:
                        info.update({
                            "width": width,
                            "height": height,
                            "mode": mode,
                            "format": img_format,
                            "bit_depth": ptk.ImgUtils.format_bit_depth(mode),
                        })
                    if optimize_check:
                        info["optimization"] = (
                            ptk.MapOptimizer.assess(
                                path, image=pil_image, **optimize_kwargs
                            )
                        )
                    tex_entries.append(info)

            results.append({
                "material": mat_str,
                "type": mat_type,
                "textures": tex_entries,
            })

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
        **filter_kwargs,
    ):
        """Retrieves all materials from the current scene, with flexible name/type filtering.

        ``exclude_defaults`` (default True) drops Maya's built-in defaults
        (``lambert1``, ``particleCloud1``, ``shaderGlow1``, ``standardSurface1``,
        plus anything reported by ``cmds.ls(defaultNodes=True)``). Pass
        ``exclude_defaults=False`` to include them.
        """
        mat_list = cmds.ls(materials=True, flatten=True) or []

        if exclude_defaults and mat_list:
            default_nodes = MatUtils._default_material_names()
            mat_list = [m for m in mat_list if _short_name(m) not in default_nodes]

        d = {_short_name(m): m for m in mat_list}
        filtered = ptk.filter_dict(d, keys=True, inc=inc, exc=exc, **filter_kwargs)

        mats = list(filtered.values())

        if node_type:
            mats = ptk.filter_list(mats, inc=node_type, map_func=cmds.nodeType)

        if as_dict:
            dct = {_short_name(m): m for m in mats}
            return dict(sorted(dct.items())) if sort else dct

        return sorted(mats, key=_short_name) if sort else mats

    @staticmethod
    def get_connected_shaders(file_nodes) -> List[str]:
        """Return surface shaders connected to one or more file nodes, ignoring intermediates."""
        file_nodes = cmds.ls(_to_strs(file_nodes), flatten=True) or []
        visited = set()
        shaders = set()

        # Maya classifies shaders via the ``shader/surface`` classification.
        def _is_surface_shader(node):
            try:
                cls_strs = cmds.getClassification(cmds.nodeType(node)) or []
                return any("shader/surface" in c for c in cls_strs)
            except Exception:
                return False

        def _traverse(node):
            if node in visited:
                return
            visited.add(node)

            outputs = (
                cmds.listConnections(node, source=False, destination=True) or []
            )
            for out in outputs:
                # Skip non-shading nodes — only follow shader graph nodes.
                if cmds.nodeType(out) == "shadingEngine":
                    continue
                if _is_surface_shader(out):
                    shaders.add(out)
                _traverse(out)

        for file_node in file_nodes:
            _traverse(file_node)

        return list(shaders)

    @classmethod
    def get_file_nodes(
        cls,
        materials: Optional[List[str]] = None,
        raw: bool = False,
        return_type: str = "fileNode",
    ) -> list:
        """Returns file node info in any column order based on return_type."""
        file_node_names = cmds.ls(type="file") or []
        if not file_node_names:
            return []

        workspace_dir = cmds.workspace(q=True, rd=True) or ""
        columns = return_type.split("|")
        needs_shader = (
            "shader" in columns or "shaderName" in columns or materials is not None
        )

        file_to_shader_name = {}
        file_to_shaders = {}
        if needs_shader:
            shading_engines = cmds.ls(type="shadingEngine") or []
            shader_attrs = ["surfaceShader", "volumeShader", "displacementShader"]
            processed_shaders = set()

            for sg in shading_engines:
                for attr_name in shader_attrs:
                    try:
                        connections = cmds.listConnections(
                            f"{sg}.{attr_name}", source=True, destination=False
                        )
                        if connections:
                            shader_name = connections[0]
                            if shader_name in processed_shaders:
                                continue
                            processed_shaders.add(shader_name)
                            history = (
                                cmds.listHistory(shader_name, pruneDagObjects=True)
                                or []
                            )
                            file_nodes_in_history = cmds.ls(history, type="file") or []
                            for node in file_nodes_in_history:
                                file_to_shaders.setdefault(node, set()).add(
                                    shader_name
                                )
                                if node not in file_to_shader_name:
                                    file_to_shader_name[node] = shader_name
                    except Exception:
                        pass

        if materials:
            mat_names = {str(m) for m in materials}
            file_node_names = [
                fn
                for fn in file_node_names
                if mat_names & file_to_shaders.get(fn, set())
            ]

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
            shading_engines = (
                cmds.listConnections(mat_str, type="shadingEngine") or []
            )
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

        objects = _to_strs(objects)
        valid_objects = cmds.ls(objects, flatten=True) or []
        if valid_objects:
            cmds.sets(valid_objects, edit=True, forceElement=shading_group)

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
            for m in cmds.ls(cmds.sets(sg, q=True) or [], long=True, flatten=True) or []:
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
        whole = [sg for sg, f in assignments.items() if f is None and cmds.objExists(sg)]

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
        "none": "Standard.sfx",          # opaque
        "masked": "Standard_Masked.sfx",  # alpha test / cutout (clean VP2.0 preview, hard edges)
        "transparent": "Standard_Transparent.sfx",  # alpha blend (soft edges)
    }

    @staticmethod
    def create_stingray_shader(name, opacity=False, opacity_mode=None):
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
        if opacity_mode is None:
            opacity_mode = "transparent" if opacity else "none"
        # Back-compat with the old experimental "lightweight" / "transparent_graph" names
        opacity_mode = {
            "transparent_graph": "transparent",
            "lightweight": "transparent",
        }.get(opacity_mode, opacity_mode)

        graph_name = MatUtils.STINGRAY_GRAPHS.get(
            opacity_mode, MatUtils.STINGRAY_GRAPHS["none"]
        )

        EnvUtils.load_plugin("shaderFXPlugin")
        shader = NodeUtils.create_render_node(
            "StingrayPBS", name=name, create_shading_group=False
        )

        maya_install = EnvUtils.get_env_info("install_path")
        graph = os.path.join(
            maya_install, "presets", "ShaderFX", "Scenes", "StingrayPBS", graph_name
        )
        if os.path.exists(graph):
            cmds.shaderfx(sfxnode=str(shader), loadGraph=graph)

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
            objects = _to_strs(objects)
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
            node_names = _to_strs(limit_to_nodes)
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
    ) -> Dict[str, List[str]]:
        """Find duplicate materials based on their texture file names or full paths."""
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
        for material in materials:
            mat_type = cmds.nodeType(material)

            history = cmds.listHistory(material, pruneDagObjects=True) or []
            file_nodes = cmds.ls(history, type="file") or []
            if not file_nodes:
                continue

            history_set = set(history)

            attr_texture_pairs = []
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
                    dest_plugs = cmds.listConnections(
                        node,
                        source=False,
                        destination=True,
                        plugs=True,
                    ) or []
                    for plug in dest_plugs:
                        plug_node = plug.split(".")[0]
                        if plug_node == material:
                            mat_attrs.add(_parent_attr(plug))
                        elif (
                            plug_node not in visited
                            and plug_node in history_set
                        ):
                            frontier.append(plug_node)

                if mat_attrs:
                    for attr in mat_attrs:
                        attr_texture_pairs.append((attr, tex_id))
                else:
                    attr_texture_pairs.append(("_unresolved", tex_id))

            if not attr_texture_pairs:
                continue

            fingerprint = (mat_type, frozenset(attr_texture_pairs))
            material_data[material] = fingerprint

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
                duplicates[original] = materials_list[1:]

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
    ) -> None:
        """Find duplicate materials, remove duplicates, and reassign them to the original material."""
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
            collected_materials, strict=strict
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
        objects: List[str], as_strings: bool = True
    ) -> List[str]:
        """Filter materials assigned to the given objects."""
        return MatUtils.get_mats(objects, as_strings=True)

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
            materials = cmds.ls(_to_strs(materials), mat=True) or []

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
            # copied previously, which would otherwise force Dropbox /
            # OneDrive to re-hash and re-upload every one of them.
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
                executor.submit(_copy_one, src, fn): src
                for src, fn in src_entries
            }
            total = len(futures)
            done = 0
            for future in as_completed(futures):
                src = futures[future]
                try:
                    src_p, dst_p, was_skipped = future.result(
                        timeout=per_file_timeout
                    )
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
            if lower.startswith(si_abs.lower() + "/"):
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
            texture_nodes = _to_strs(file_nodes)
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

        base_name = node_name or f"{_short_name(bump_node)}_normal"
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

        materials_list = _to_strs(materials)
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
