# !/usr/bin/python
# coding=utf-8
import os
from typing import List, Tuple, Union, Dict, Any, Optional

try:
    import pymel.core as pm
    from maya import cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils._env_utils import EnvUtils


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
        """Normalize objects/materials/file nodes for texture operations."""
        from maya import cmds

        # Helper to get long names
        def to_long(nodes):
            if not nodes:
                return []
            # Handle PyNodes or strings
            names = [str(n) for n in nodes]
            return cmds.ls(names, long=True, flatten=True) or []

        resolved_objects = to_long(objects) if objects else []

        resolved_materials_set = set()
        if materials:
            mats = cmds.ls(to_long(materials), mat=True, long=True) or []
            resolved_materials_set.update(mats)

        if resolved_objects:
            # Use optimized get_mats with as_strings=True
            found_mats = cls.get_mats(resolved_objects, as_strings=True)
            resolved_materials_set.update(found_mats)

        resolved_materials = sorted(list(resolved_materials_set))

        resolved_file_nodes_set = set()

        if resolved_materials:
            # cmds.listConnections on a list of nodes works and is fast
            files = cmds.listConnections(resolved_materials, type="file") or []
            resolved_file_nodes_set.update(files)

        if file_nodes:
            files = cmds.ls(to_long(file_nodes), type="file", long=True) or []
            resolved_file_nodes_set.update(files)

        if not resolved_file_nodes_set and fallback_to_scene:
            files = cmds.ls(type="file", long=True) or []
            resolved_file_nodes_set.update(files)

        if as_strings:
            return {
                "objects": resolved_objects,
                "materials": resolved_materials,
                "file_nodes": sorted(list(resolved_file_nodes_set)),
            }

        # Convert to PyNodes at the end to maintain API compatibility
        return {
            "objects": [pm.PyNode(o) for o in resolved_objects],
            "materials": [pm.PyNode(m) for m in resolved_materials],
            "file_nodes": [pm.PyNode(f) for f in sorted(list(resolved_file_nodes_set))],
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
                if hasattr(node, "fileTextureName"):
                    file_path = node.fileTextureName.get()
                else:
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

            if abs_path.startswith(project_sourceimages):
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
                file_path = node.fileTextureName.get()
            except Exception:
                continue
            if not file_path:
                continue
            filenames.append(os.path.basename(file_path))
        return filenames

    @staticmethod
    def _create_standard_shader(name=None, color=None, return_type="type"):
        """Create or get the preferred shader type, with optional node creation.

        Parameters:
            name (str, optional): Name for the shader node (only used when return_type != 'type').
            color (tuple, optional): RGB color tuple (0-1 range) to apply to the shader.
            return_type (str): What to return:
                - 'type': shader type string ('standardSurface' or 'lambert') [default]
                - 'shader': created shader node name
                - 'shading_group': created shading group name
                - 'both': tuple of (shader_name, shading_group_name)

        Returns:
            str or tuple: Depends on return_type parameter.
        """
        from maya import cmds

        # Determine the preferred shader type
        try:
            # Check if standardSurface is available
            if cmds.pluginInfo("mtoa", query=True, loaded=True) or cmds.nodeType(
                "standardSurface", isTypeName=True
            ):
                shader_type = "standardSurface"
            else:
                # Try creating one to be sure (some versions might have it without plugin?)
                # Actually, checking nodeType is safer.
                # If standardSurface is not a valid node type, fallback.
                # But nodeType(isTypeName=True) might not work for plugins not yet loaded?
                # Let's stick to the try-create pattern but with cmds
                try:
                    test = cmds.shadingNode("standardSurface", asShader=True)
                    cmds.delete(test)
                    shader_type = "standardSurface"
                except Exception:
                    shader_type = "lambert"
        except Exception:
            shader_type = "lambert"

        # If only type requested, return early
        if return_type == "type":
            return shader_type

        # Create the shader node
        shader_name = name or f"material_{shader_type}"
        shader = cmds.shadingNode(shader_type, asShader=True, name=shader_name)

        # Apply color if provided
        if color:
            color_attr = "baseColor" if shader_type == "standardSurface" else "color"
            cmds.setAttr(
                f"{shader}.{color_attr}", color[0], color[1], color[2], type="double3"
            )

        # Return based on requested type
        if return_type == "shader":
            return shader

        # Create shading group
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
    def get_mats(objs=None, as_strings=False) -> List[Union[str, "pm.PyNode"]]:
        """Returns the set of materials assigned to a given list of objects or components.

        Parameters:
            objs (list): The objects or components to retrieve the material from.
                        If None, current selection will be used.
            as_strings (bool): If True, returns list of strings instead of PyNodes.
        Returns:
            list: Materials assigned to the objects or components (duplicates removed).
        """
        from maya import cmds

        if objs is None:
            objs = cmds.ls(selection=True, long=True)

        if not objs:
            return []

        # Ensure we have a flat list of long names
        # Handle PyNodes passed in input
        if objs:
            if not isinstance(objs, (list, tuple, set)):
                objs = [objs]

            # Convert PyNodes or other objects to strings safely
            objs = [o.name() if hasattr(o, "name") else str(o) for o in objs]

        target_objs = cmds.ls(objs, long=True, flatten=True) or []
        mats = set()

        # Separate faces and objects for optimized processing
        faces = [obj for obj in target_objs if ".f[" in obj]
        objects = [obj for obj in target_objs if ".f[" not in obj]

        # Process objects (transforms/shapes)
        if objects:
            # Get shapes from transforms
            # Use listRelatives for direct children shapes
            shapes = cmds.listRelatives(objects, shapes=True, fullPath=True) or []
            # Also include objects that are already shapes
            for obj in objects:
                if cmds.nodeType(obj) in ["mesh", "nurbsSurface", "subdiv"]:
                    shapes.append(obj)

            shapes = list(set(shapes))

            # Get shading engines connected to shapes
            if shapes:
                # Batch query shading engines
                shading_engines = set()
                for shape in shapes:
                    # listSets is the primary way to check set membership
                    sgs = cmds.listSets(object=shape, type=1) or []
                    if not sgs:
                        # Fallback to connections
                        sgs = cmds.listConnections(shape, type="shadingEngine") or []

                    shading_engines.update(sgs)

                # Get materials from shading engines
                for sg in shading_engines:
                    # ShadingEngine.surfaceShader -> Material is a source connection
                    connections = (
                        cmds.listConnections(
                            f"{sg}.surfaceShader", source=True, destination=False
                        )
                        or []
                    )
                    mats.update(connections)

        # Process faces
        if faces:
            for face in faces:
                # listSets is faster than checking membership in all shading engines
                face_sgs = (
                    cmds.listSets(object=face, type=1) or []
                )  # type=1 = shadingEngine
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
                    # Fallback for faces with no direct shading group assignment
                    # This is tricky with cmds, but we can try to get the object's material
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

        if as_strings:
            return list(mats)

        # Convert to PyNodes at the very end
        return [pm.PyNode(m) for m in mats if m]

    @classmethod
    def get_texture_info(
        cls,
        objects=None,
        materials=None,
        file_nodes=None,
        texture_names=None,
    ):
        """Get information about texture files.

        Parameters:
            objects (list): Objects to derive textures from.
            materials (list): Materials to derive textures from.
            file_nodes (list): File nodes to derive textures from.
            texture_names (list): Direct list of texture file paths.

        Returns:
            list[dict]: List of dictionaries containing texture info.
        """
        # Resolve targets
        targets = cls._resolve_texture_targets(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            fallback_to_scene=(not texture_names),
            as_strings=True,
        )

        resolved_file_nodes = targets["file_nodes"]

        # Get paths from file nodes
        file_paths = cls._paths_from_file_nodes(resolved_file_nodes, absolute=True)

        # Add explicit texture names
        if texture_names:
            file_paths.extend(texture_names)

        # Remove duplicates
        file_paths = list(set(file_paths))

        return ptk.ImgUtils.get_image_info(file_paths)

    @staticmethod
    def get_scene_mats(
        inc=None,
        exc=None,
        node_type=None,
        sort: bool = False,
        as_dict: bool = False,
        **filter_kwargs,
    ):
        """Retrieves all materials from the current scene, with flexible name/type filtering.

        Parameters:
            inc, exc: Inclusion/exclusion patterns (applies to names).
            node_type (str/list/callable, optional): Material node type(s) to restrict results, e.g. "StingrayPBS".
            sort (bool): Sort result by material name.
            as_dict (bool): Return as dict {name: node}.
            **filter_kwargs: Additional keyword args passed to ptk.filter_dict (e.g. map_func, ignore_case).

        Returns:
            list or dict: Filtered materials.
        """
        mat_list = pm.ls(mat=True, flatten=True)
        d = {m.name(): m for m in mat_list}
        filtered = ptk.filter_dict(d, keys=True, inc=inc, exc=exc, **filter_kwargs)

        mats = list(filtered.values())

        # Node type filtering (after name filtering)
        if node_type:
            # Callable or string/list support via filter_list
            mats = ptk.filter_list(mats, inc=node_type, map_func=pm.nodeType)

        if as_dict:
            dct = {m.name(): m for m in mats}
            return dict(sorted(dct.items())) if sort else dct

        return sorted(mats, key=lambda x: x.name()) if sort else mats

    @staticmethod
    def get_connected_shaders(
        file_nodes: Union[
            str, "pm.nt.DependNode", List[Union[str, "pm.nt.DependNode"]]
        ],
    ) -> List["pm.nt.ShadingDependNode"]:
        """Return surface shaders connected to one or more file nodes, ignoring intermediates."""
        file_nodes = pm.ls(file_nodes, flatten=True)
        visited = set()
        shaders = set()

        def _traverse(node):
            if node in visited:
                return
            visited.add(node)

            if isinstance(node, pm.nt.DependNode):
                for out in node.outputs():
                    if not isinstance(out, pm.nt.ShadingDependNode):
                        continue
                    for sg in out.outputs(type="shadingEngine"):
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
        """Returns file node info in any column order based on return_type:
        e.g. 'shader|shaderName|path|fileNode|fileNodeName'

        Parameters:
            materials (Optional[List[str]]): List of material names to filter file nodes by.
            raw (bool): If True, returns relative paths instead of absolute paths.
            return_type (str): Pipe-separated string defining the columns to return.
                               Options: 'shader', 'shaderName', 'path', 'fileNode', 'fileNodeName'.
        Returns:
            list: List of tuples or single values based on return_type.
                  Each tuple contains the requested columns in the specified order.
        """
        from maya import cmds

        # Use cmds for faster batch queries (via PyMEL's internal reference)
        file_node_names = cmds.ls(type="file") or []
        if not file_node_names:
            return []

        workspace_dir = cmds.workspace(q=True, rd=True) or ""
        columns = return_type.split("|")
        needs_shader = (
            "shader" in columns or "shaderName" in columns or materials is not None
        )

        # Build file_node -> shader mapping only if needed
        file_to_shader_name = {}
        if needs_shader:
            # OPTIMIZATION: Build mapping by traversing from shading engines once
            # Instead of calling listHistory for every shader, we:
            # 1. Get all shading engines
            # 2. For each, get the connected shader
            # 3. Get upstream file nodes via listHistory (one call per shader, not per file node)
            shading_engines = cmds.ls(type="shadingEngine") or []
            shader_attrs = ["surfaceShader", "volumeShader", "displacementShader"]

            for sg in shading_engines:
                for attr_name in shader_attrs:
                    try:
                        connections = cmds.listConnections(
                            f"{sg}.{attr_name}", source=True, destination=False
                        )
                        if connections:
                            shader_name = connections[0]
                            # Get all file nodes in this shader's history
                            history = (
                                cmds.listHistory(shader_name, pruneDagObjects=True)
                                or []
                            )
                            for node in history:
                                if cmds.nodeType(node) == "file":
                                    if node not in file_to_shader_name:
                                        file_to_shader_name[node] = shader_name
                    except Exception:
                        pass

        # Filter by materials if specified
        if materials:
            mat_names = set(
                m if isinstance(m, str) else m.name() if hasattr(m, "name") else str(m)
                for m in materials
            )
            # Keep only file nodes connected to the specified materials
            filtered_nodes = [
                fn for fn in file_node_names if file_to_shader_name.get(fn) in mat_names
            ]
            file_node_names = filtered_nodes

        # Batch get all file paths at once using cmds (faster than PyMEL)
        file_paths = {}
        for fn in file_node_names:
            try:
                path = cmds.getAttr(f"{fn}.fileTextureName") or ""
                if raw and path.startswith(workspace_dir):
                    path = os.path.relpath(path, workspace_dir)
                file_paths[fn] = path
            except Exception:
                file_paths[fn] = ""

        # Build result rows
        file_info = []
        needs_pynode_shader = "shader" in columns
        needs_pynode_file = "fileNode" in columns

        # Cache PyNode conversions for reuse
        pynode_cache = {}

        def get_pynode(name):
            if name not in pynode_cache:
                try:
                    pynode_cache[name] = pm.PyNode(name)
                except Exception:
                    pynode_cache[name] = None
            return pynode_cache[name]

        for file_node_name in file_node_names:
            shader_name = file_to_shader_name.get(file_node_name, "")
            file_path = file_paths.get(file_node_name, "")

            row = []
            for col in columns:
                if col == "shader":
                    row.append(get_pynode(shader_name) if shader_name else None)
                elif col == "shaderName":
                    row.append(shader_name)
                elif col == "path":
                    row.append(file_path)
                elif col == "fileNode":
                    row.append(get_pynode(file_node_name))
                elif col == "fileNodeName":
                    row.append(file_node_name)
                else:
                    row.append("")
            file_info.append(tuple(row) if len(row) > 1 else row[0])

        return file_info

    @staticmethod
    def get_fav_mats():
        """Retrieves the list of favorite materials in Maya.

        Returns:
            list: A list of favorite materials.
        """
        import os.path
        import maya.app.general.tlfavorites as _fav

        version = pm.about(version=True).split(" ")[-1]  # Get the Maya version year
        path = os.path.expandvars(
            f"%USERPROFILE%/Documents/maya/{version}/prefs/renderNodeTypeFavorites"
        )
        renderNodeTypeFavorites = _fav.readFavorites(path)
        materials = [i for i in renderNodeTypeFavorites if "/" not in i]
        del _fav

        return materials

    @staticmethod
    def is_connected(mat: object, delete: bool = False) -> bool:
        """Checks if a given material is assigned and optionally deletes it.

        Parameters:
            mat (str/obj/list): The material to check.
            delete (bool): If True, delete the material if it is not assigned to any other objects.

        Returns:
            bool: True if the material was deleted or is not assigned to any other objects, False otherwise.
        """
        try:
            mat = pm.ls(mat, type="shadingDependNode", flatten=True)[0]
        except (IndexError, TypeError):
            print(f"Error: Material {mat} not found or invalid.")
            return False

        connected_shading_groups = pm.listConnections(
            f"{mat}.outColor", type="shadingEngine"
        )
        if not connected_shading_groups:
            if delete:
                pm.delete(mat)
            return True

        return False

    @staticmethod
    @CoreUtils.undoable
    def create_mat(mat_type, prefix="", name=""):
        """Creates a material based on the provided type or a random material if 'mat_type' is 'random'.
        Prefers standardSurface over lambert when available.

        Parameters:
            mat_type (str): The type of the material, e.g. 'lambert', 'blinn', 'standardSurface', or 'random' for a random material.
            prefix (str, optional): An optional prefix to append to the material name. Defaults to "".
            name (str, optional): The name of the material. Defaults to "".

        Returns:
            str: The created material name.
        """
        import random
        from maya import cmds

        if mat_type == "random":
            # Use preferred material type (standardSurface if available, otherwise lambert)
            preferred_type = MatUtils._create_standard_shader(return_type="type")
            rgb = [
                random.randint(0, 255) for _ in range(3)
            ]  # Generate a list containing 3 values between 0-255
            name = "{}{}_{}_{}_{}".format(
                prefix, name, str(rgb[0]), str(rgb[1]), str(rgb[2])
            )
            mat = cmds.shadingNode(preferred_type, asShader=True, name=name)
            convertedRGB = [round(float(v) / 255, 3) for v in rgb]
            # Set color attribute (works for both lambert and standardSurface)
            color_attr = (
                f"{name}.baseColor"
                if preferred_type == "standardSurface"
                else f"{name}.color"
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
        """Assigns a material to a list of objects or components.
        If the material doesn't exist, creates a new one using the preferred material type.

        Parameters:
            objects (str/obj/list): The objects or components to assign the material to.
            mat_name (str): The name of the material to assign.
        """
        from maya import cmds

        if not objects:
            raise ValueError("No objects provided to assign material.")

        # Retrieve or create material
        if cmds.objExists(mat_name):
            mat = mat_name
        else:
            # Use preferred material type (standardSurface if available, otherwise lambert)
            preferred_type = MatUtils._create_standard_shader(return_type="type")
            mat = cmds.shadingNode(preferred_type, name=mat_name, asShader=True)

        # Check if the material has a shading engine, otherwise create one
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

        # Filter for valid objects and assign the material
        # Handle PyNodes if passed
        if objects:
            if not isinstance(objects, (list, tuple, set)):
                objects = [objects]
            objects = [o.name() if hasattr(o, "name") else str(o) for o in objects]

        valid_objects = cmds.ls(objects, flatten=True)
        if valid_objects:
            cmds.sets(valid_objects, forceElement=shading_group)

    @classmethod
    def find_by_mat_id(
        cls, material: str, objects: Optional[List[str]] = None, shell: bool = False
    ) -> List[str]:
        """Find objects or faces by the material ID.

        This method searches for objects or faces that are assigned a specific material.
        It supports filtering by specific objects and can operate in two modes: shell mode
        where it only returns the transform nodes, or full mode where it returns individual faces.

        Parameters:
            material (str): The name of the material to search for.
            objects (Optional[List[str]], optional): A list of objects to filter the search.
                                                     Only objects in this list will be considered.
                                                     Defaults to None.
            shell (bool, optional): If True, only the transform nodes of the objects will be returned.
                                    If False, individual faces assigned the material will be returned.
                                    Defaults to False.
        Returns:
            List[str]: A list of objects or faces that are assigned the given material. The list
                       contains transform nodes if `shell` is True, otherwise it contains individual
                       faces.
        """
        if cmds.nodeType(material) == "VRayMultiSubTex":
            raise TypeError(
                "Invalid material type. If material is a multimaterial, please select a submaterial."
            )

        if not cmds.objExists(material):
            print(f"Material '{material}' does not exist.")
            return []

        shading_groups = cmds.listConnections(material, type="shadingEngine")
        if not shading_groups:
            print(f"No shading groups found for material '{material}'.")
            return []

        objs_with_material = []

        # Prepare target transforms set for fast lookup
        target_transforms = set()
        if objects:
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
            for member in members:
                # Determine transform
                if "." in member:
                    node = member.split(".")[0]
                else:
                    node = member

                # Get transform name
                if cmds.nodeType(node) == "transform":
                    transform = node
                else:
                    parents = cmds.listRelatives(node, parent=True, fullPath=True)
                    transform = parents[0] if parents else node

                # Filter
                if objects and transform not in target_transforms:
                    continue

                if shell:
                    if transform not in objs_with_material:
                        objs_with_material.append(transform)
                else:
                    objs_with_material.append(member)

        # Convert to PyNodes for backward compatibility
        return [pm.PyNode(obj) for obj in objs_with_material]

    @staticmethod
    @ptk.filter_results
    def collect_material_paths(
        materials: Optional[List[str]] = None,
        attributes: Optional[List[str]] = None,
        inc_mat_name: bool = False,
        inc_path_type: bool = False,
        resolve_full_path: bool = False,
    ) -> Union[List[str], List[Tuple[str, ...]]]:
        """Collects specified attributes file paths for given materials.

        Parameters:
            materials (Optional[List[str]]): List of material names.
            attributes (Optional[List[str]]): List of attributes to collect file paths from.
            inc_mat_name (bool): If True, include material name in the result.
            inc_path_type (bool): If True, include path type (Relative/Absolute) in the result.
            resolve_full_path (bool): If True, return absolute full path instead of relative.

        Returns:
            Union[List[str], List[Tuple[str, ...]]]: List of file paths or tuples containing requested info.
        """
        if materials is None:
            materials = cmds.ls(mat=True)
        else:
            # Ensure strings
            materials = [m.name() if hasattr(m, "name") else m for m in materials]
            materials = cmds.ls(materials, mat=True)

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
            for attr in attributes:
                file_nodes = cmds.listConnections(material, type="file") or []
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
        limit_to_nodes: Optional[List[Union[str, "pm.nt.File"]]] = None,
    ) -> List["pm.nt.File"]:
        """Internal helper to remap file nodes to target_dir, preserving relative subfolders inside sourceimages.

        Parameters:
            file_paths (List[str]): List of file paths to remap.
            target_dir (str): Target directory to remap the file nodes to.
            silent (bool): If True, suppresses output messages.
            limit_to_nodes (Optional[List[str/pm.nt.File]]): Restrict remapping to
                the provided file nodes instead of the entire scene.

        Returns:
            List[pm.nt.File]: List of remapped file nodes.
        """
        sourceimages_dir = EnvUtils.get_env_info("sourceimages")
        sourceimages_dir_norm = os.path.normpath(sourceimages_dir).replace("\\", "/")

        # Optimization: Use cmds instead of pm for faster iteration
        if limit_to_nodes:
            # Ensure we have strings for cmds
            node_names = []
            for n in limit_to_nodes:
                if hasattr(n, "name"):
                    node_names.append(n.name())
                else:
                    node_names.append(str(n))

            # Filter for valid file nodes
            nodes_to_process = cmds.ls(node_names, type="file") or []
        else:
            nodes_to_process = cmds.ls(type="file") or []

        file_nodes: Dict[str, List[str]] = {}

        # Build lookup: rel path if under sourceimages, else just filename
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
                if key not in file_nodes:
                    file_nodes[key] = []
                file_nodes[key].append(fn)

        remapped_nodes: List[pm.nt.File] = []
        remap_data = ptk.remap_file_paths(file_paths, target_dir, sourceimages_dir)

        for key, new_full_path, maya_path in remap_data:
            if key in file_nodes:
                for fn_name in file_nodes[key]:
                    # Check if update is needed
                    current_val = cmds.getAttr(f"{fn_name}.fileTextureName")
                    if current_val != maya_path:
                        if not silent:
                            pm.displayInfo(f"\n[Remap Attempt]")
                            pm.displayInfo(f"  original path: {new_full_path}")
                            pm.displayInfo(f"  lookup key:    {key}")
                            pm.displayInfo(f"  maya path:     {maya_path}")
                            pm.displayInfo(f"  remapped:      {fn_name}")

                        cmds.setAttr(
                            f"{fn_name}.fileTextureName", maya_path, type="string"
                        )
                        remapped_nodes.append(pm.PyNode(fn_name))
            else:
                pm.warning(
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
        file_nodes: Optional[List[Union[str, "pm.nt.File"]]] = None,
        objects: Optional[List[str]] = None,
    ) -> None:
        """Remaps file texture paths for materials to new_dir, using relative paths if inside sourceimages.

        Parameters:
            materials (Optional[List[str]]): List of material names to remap. Defaults to all materials.
            new_dir (Optional[str]): Target directory to remap the file nodes to. Defaults to sourceimages.
            silent (bool): If True, suppresses output messages.
            file_nodes (Optional[List[Union[str, pm.nt.File]]]): Specific file nodes to remap. When provided,
                only these nodes are processed unless materials are also supplied.
            objects (Optional[List[str]]): Scene objects whose assigned materials should be remapped.
        """
        new_dir = new_dir or EnvUtils.get_env_info("sourceimages")
        if not new_dir or not os.path.isdir(new_dir):
            pm.warning(f"Invalid directory: {new_dir}")
            return

        fallback_to_scene = objects is None and materials is None and file_nodes is None

        scope = cls._resolve_texture_targets(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            fallback_to_scene=fallback_to_scene,
            as_strings=True,
        )
        resolved_nodes = scope["file_nodes"]

        if not resolved_nodes:
            pm.warning("No valid file nodes found to remap.")
            return

        textures = cls._paths_from_file_nodes(resolved_nodes)
        if not textures:
            pm.warning("No valid texture paths found.")
            return

        remapped_nodes = cls.remap_file_nodes(
            file_paths=textures,
            target_dir=new_dir,
            silent=silent,
            limit_to_nodes=resolved_nodes,
        )
        if not silent:
            pm.displayInfo(
                f"// Result: Remapped {len(remapped_nodes)}/{len(textures)} texture paths."
            )

    @staticmethod
    def is_duplicate_material(material1: str, material2: str) -> bool:
        """Check if two materials are duplicates based on their textures.

        Parameters:
            material1 (str): Name of the first material.
            material2 (str): Name of the second material.

        Returns:
            bool: True if materials are duplicates, False otherwise.
        """
        textures1 = set(pm.listConnections(pm.listHistory(material1), type="file"))
        textures2 = set(pm.listConnections(pm.listHistory(material2), type="file"))
        return textures1 == textures2

    @classmethod
    def find_materials_with_duplicate_textures(
        cls,
        materials: Optional[List[Union[str, "pm.nt.DependNode"]]] = None,
        strict: bool = False,
    ) -> Dict[str, List[str]]:
        """Find duplicate materials based on their texture file names or full paths.

        Parameters:
            materials (Optional[List[str]]): List of material nodes.
            strict (bool): Whether to compare using full paths (True) or just file names (False).

        Returns:
            Dict[str, List[str]]:
                Each key is the original material, and each value is a list of duplicate materials.
        """

        def get_texture_name(path: str) -> str:
            """Extracts the core file name without the path or extension."""
            filename = os.path.basename(path).lower()
            return os.path.splitext(filename)[0]

        if materials is None:
            materials = cmds.ls(mat=True)
        else:
            materials = [m.name() if hasattr(m, "name") else m for m in materials]
            materials = cmds.ls(materials, mat=True)

        # Dictionary to store relevant material data (texture names or paths)
        material_data = {}
        for material in materials:
            # Collect file nodes connected to the material or its shading engine
            file_nodes = (
                cmds.listConnections(
                    material, source=True, destination=False, type="file"
                )
                or []
            )

            # Check shading engine connections if no direct file nodes
            if not file_nodes:
                shading_engines = (
                    cmds.listConnections(material, type="shadingEngine") or []
                )
                for engine in shading_engines:
                    engine_files = (
                        cmds.listConnections(
                            engine, source=True, destination=False, type="file"
                        )
                        or []
                    )
                    file_nodes.extend(engine_files)

            if not file_nodes:  # Skip materials without file nodes
                continue

            # Collect texture paths or names based on 'strict' flag
            texture_names = []
            for file_node in file_nodes:
                if cmds.objExists(f"{file_node}.fileTextureName"):
                    path = cmds.getAttr(f"{file_node}.fileTextureName")
                    if path:
                        if strict:
                            texture_names.append(path.lower())
                        else:
                            texture_names.append(get_texture_name(path))

            if not texture_names:
                continue

            # Store the texture names or paths for duplicate checking
            texture_set = frozenset(texture_names)
            material_data[material] = texture_set

        # Identify duplicates by comparing texture sets
        duplicates = {}
        seen_materials = {}
        for material, texture_set in material_data.items():
            match_found = False
            for seen_texture_set, seen_material_list in seen_materials.items():
                if texture_set == seen_texture_set:
                    seen_material_list.append(material)
                    match_found = True
                    break
            if not match_found:
                seen_materials[texture_set] = [material]

        # Process duplicates
        for materials_list in seen_materials.values():
            if len(materials_list) > 1:
                # Sort by name length then name to prefer shorter names (often original)
                materials_list.sort(key=lambda x: (len(x), x))
                original = materials_list[0]
                duplicates[original] = materials_list[1:]  # Always exclude the original

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
        """Find duplicate materials, remove duplicates, and reassign them to the original material.

        Parameters:
            materials (Optional[List[str]]): List of material names.
            delete (bool): Whether to delete the duplicate materials after reassignment.
            strict (bool): Whether to compare using full paths (True) or just file names (False).
        """
        if materials is not None:  # Filter out invalid objects and warn about them
            valid_objects = []
            for m in materials:
                if pm.objExists(m):
                    valid_objects.append(m)
                else:
                    pm.warning(f"Object '{m}' does not exist or is not valid.")

            # Collect valid materials
            collected_materials = pm.ls(valid_objects, mat=True)
            if not collected_materials:
                raise ValueError(f"No valid materials found in {materials}")
        else:  # Collect all materials in the scene
            collected_materials = pm.ls(mat=True)

        # Find duplicates using the updated format
        duplicate_to_original = cls.find_materials_with_duplicate_textures(
            collected_materials, strict=strict
        )
        duplicates_to_delete = []
        for original, duplicates in duplicate_to_original.items():
            # Get the shading group of the original material
            original_sgs = cmds.listConnections(original, type="shadingEngine")
            if not original_sgs:
                continue
            original_sg = original_sgs[0]

            # Reassign all duplicates to the original material
            for duplicate in duplicates:
                try:  # Get the shading groups of the duplicate material
                    duplicate_sgs = cmds.listConnections(
                        duplicate, type="shadingEngine"
                    )
                    if not duplicate_sgs:
                        continue

                    for dup_sg in duplicate_sgs:
                        # Get the members (faces or objects) of the duplicate shading group
                        members = cmds.sets(dup_sg, q=True)
                        if members:
                            # Reassign the faces or objects to the original shading group
                            cmds.sets(members, forceElement=original_sg)
                            print(
                                f"Reassigned material from {duplicate} to {original} on members: {members}"
                            )
                    # Add the duplicate material to the deletion list
                    duplicates_to_delete.append(duplicate)
                except Exception as e:
                    print(f"Error processing material {duplicate}: {e}")
                    continue

        if delete:  # Delete all duplicate materials after successful reassignment
            for duplicate in duplicates_to_delete:
                try:
                    if cmds.objExists(duplicate):
                        cmds.delete(duplicate)
                        print(f"Deleted duplicate material: {duplicate}")
                except Exception as e:
                    print(f"Error deleting material {duplicate}: {e}")
                except pm.MayaNodeError as e:
                    print(f"Error deleting node for material {duplicate}: {e}")

    @staticmethod
    def filter_materials_by_objects(objects: List[str]) -> List[str]:
        """Filter materials assigned to the given objects.

        Parameters:
            objects (List[str]): List of object names.

        Returns:
            List[str]: List of material names assigned to the given objects.
        """
        assigned_materials = set()
        for obj in objects:  # Get shape nodes if the object is a transform
            shapes = pm.listRelatives(obj, shapes=True, fullPath=True) or [obj]
            for shape in shapes:
                shading_groups = pm.listConnections(shape, type="shadingEngine")
                for sg in shading_groups:
                    materials = pm.listConnections(f"{sg}.surfaceShader")
                    assigned_materials.update(materials)
        return list(assigned_materials)

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
        """Reloads textures connected to specified materials with inclusion/exclusion filters.

        Parameters:
            materials (str/obj/list): Material or list of materials to process. Defaults to all materials in the scene.
            inc (str/list): Inclusion patterns for filtering textures.
            exc (str/list): Exclusion patterns for filtering textures.
            log (bool): Whether to log the textures being reloaded.
            refresh_viewport (bool): Whether to refresh the viewport.
            refresh_hypershade (bool): Whether to refresh the Hypershade panel.
            texture_types (List[str]): List of texture types to filter by.
        """
        if texture_types is None:
            texture_types = ["file", "aiImage", "pxrTexture", "imagePlane"]

        materials = pm.ls(mat=True) if materials is None else pm.ls(materials, mat=True)

        file_nodes = []
        for material in materials:
            for tex_type in texture_types:
                file_nodes.extend(pm.listConnections(material, type=tex_type))

        # Remove duplicates
        file_nodes = list(set(file_nodes))

        # Apply inclusion and exclusion filters using ptk.filter_list
        if inc or exc:
            file_nodes = ptk.filter_list(
                file_nodes,
                inc=inc,
                exc=exc,
                map_func=lambda fn: fn.fileTextureName.get(),
            )

        for fn in file_nodes:
            try:
                # Reload the texture by resetting the file path
                file_path = fn.fileTextureName.get()
                fn.fileTextureName.set(file_path)
                if log:
                    print(f"Reloaded texture: {file_path}")
            except AttributeError:
                if log:
                    print(f"Skipped non-file node: {fn}")

        # Refresh viewport if requested
        if refresh_viewport:
            pm.refresh(force=True)

        # Refresh Hypershade if requested
        if refresh_hypershade:
            pm.refreshEditorTemplates()
            pm.mel.eval(
                'hypershadePanelMenuCommand("hyperShadePanel1", "refreshAllSwatches");'
            )

    @classmethod
    def move_texture_files(
        cls,
        found_files: List[Union[str, Tuple[str, str]]],
        new_dir: str,
        delete_old: bool = False,
        create_dir: bool = True,
    ) -> None:
        """Move or copy found texture files to a new directory.

        Parameters:
            found_files (List): List of filepaths or (dir, filename) tuples.
            new_dir (str): Target directory to move/copy textures to.
            delete_old (bool): If True, delete original files after copying.
            create_dir (bool): If True, create the destination directory if it doesn't exist.
        """
        if not found_files:
            pm.warning("No texture files provided for moving.")
            return

        if not ptk.is_valid(new_dir, "dir") and create_dir:
            ptk.FileUtils.create_dir(new_dir)

        copied_count = 0

        for entry in found_files:
            if isinstance(entry, tuple):
                dir_path, filename = entry
                src_path = os.path.join(dir_path, filename).replace("\\", "/")
            else:
                src_path = entry.replace("\\", "/")
                filename = os.path.basename(src_path)

            if not os.path.isfile(src_path):
                pm.warning(f"Source file does not exist: {src_path}")
                continue

            try:
                copied_path = ptk.FileUtils.copy_file(
                    src_path, destination=new_dir, overwrite=True, create_dir=create_dir
                )
                copied_count += 1
                pm.displayInfo(f"// Copied: {src_path} -> {copied_path}")

                if delete_old:
                    os.remove(src_path)
                    pm.displayInfo(f"// Deleted original: {src_path}")

            except Exception as e:
                pm.warning(f"// Failed to copy {src_path}: {e}")

        pm.displayInfo(f"// Result: Copied {copied_count} texture(s).")

    @classmethod
    def find_texture_files(
        cls,
        objects: Optional[List[str]] = None,
        source_dir: str = "",
        recursive: bool = True,
        return_dir: bool = False,
        quiet: bool = False,
        file_nodes: Optional[List[Union[str, "pm.nt.File"]]] = None,
        materials: Optional[List[str]] = None,
    ) -> List[Union[str, Tuple[str, str]]]:
        """Find texture files for given objects' materials inside source_dir.

        Parameters:
            objects (List[str]): List of object names to search textures for.
            source_dir (str): Directory to search.
            recursive (bool): If True, search subdirectories.
            return_dir (bool): If True, return (dir, filename) tuples instead of filepaths.
            quiet (bool): If False, print the found results in a readable format.
            file_nodes (Optional[List[Union[str, pm.nt.File]]]): Specific file nodes to resolve texture
                filenames from when no scene objects are provided.
            materials (Optional[List[str]]): Material names to include in the search scope.

        Returns:
            List[str] or List[Tuple[str, str]]: Filepaths or (dir, filename) based on return_dir.
        """
        if not ptk.is_valid(source_dir, "dir"):
            pm.warning(f"Invalid source directory: {source_dir}")
            return []

        scope = cls._resolve_texture_targets(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            fallback_to_scene=False,
        )

        texture_nodes = scope["file_nodes"]
        if not texture_nodes:
            pm.warning(
                "No objects, materials, or file nodes provided to find textures."
            )
            return []

        texture_filenames = set(
            filename
            for filename in cls._filenames_from_file_nodes(texture_nodes)
            if filename
        )

        if not texture_filenames:
            pm.warning("No texture names available for lookup.")
            return []

        all_files = ptk.FileUtils.get_dir_contents(
            source_dir, content="filepath", recursive=recursive
        )

        filename_to_path = {os.path.basename(fp): fp for fp in all_files}

        results = []
        for tex_file in texture_filenames:
            match = filename_to_path.get(tex_file)
            if match:
                dir_path = os.path.dirname(match).replace("\\", "/")
                file_name = os.path.basename(match)
                if return_dir:
                    results.append((dir_path, file_name))
                else:
                    results.append(match.replace("\\", "/"))

        if not quiet:
            pm.displayInfo("\n[Texture Files Found]")
            if return_dir:
                max_dir_len = max(len(d) for d, _ in results) if results else 0
                for dir_path, filename in results:
                    pm.displayInfo(f"  {dir_path.ljust(max_dir_len)}  {filename}")
            else:
                for filepath in results:
                    pm.displayInfo(f"  {filepath}")
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
        file_nodes: Optional[List[Union[str, "pm.nt.File"]]] = None,
    ) -> None:
        """Copies texture files from an old directory to a new one, remaps file nodes, and optionally deletes old files.

        Parameters:
            materials (Optional[List[str]]): Material names to include.
            old_dir (Optional[str]): Source directory containing the files to migrate.
            new_dir (Optional[str]): Destination directory for the migrated textures.
            silent (bool): When True, suppresses informational log output.
            delete_old (bool): Delete the source file after a successful copy when True.
            objects (Optional[List[str]]): Scene objects whose assigned textures should be migrated.
            file_nodes (Optional[List[str/pm.nt.File]]): Explicit file nodes to migrate.
        """
        for label, path in (("old_dir", old_dir), ("new_dir", new_dir)):
            if not path or not os.path.exists(path) or not os.path.isdir(path):
                pm.warning(f"{label} is invalid: {path}")
                return

        scope = cls._resolve_texture_targets(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            fallback_to_scene=False,
        )
        resolved_nodes = scope["file_nodes"]
        if not resolved_nodes:
            pm.warning("No file nodes found for migration.")
            return

        filenames = cls._unique_ordered(cls._filenames_from_file_nodes(resolved_nodes))
        if not filenames:
            pm.warning("No texture names available for migration.")
            return

        found_files = [(old_dir, filename) for filename in filenames]

        cls.move_texture_files(
            found_files=found_files,
            new_dir=new_dir,
            delete_old=delete_old,
            create_dir=True,
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
        """Move unused textures to a specified directory.

        Parameters:
            source_dir (str): The directory to search for textures. Default is Maya's sourceimages directory.
            output_dir (str): The directory to move unused textures to. Default is a subfolder 'unused' in sourceimages.
        """
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
        """Get an icon with a color fill matching the given material's RGB value.
        Supports both lambert and standardSurface materials.

        Parameters:
            mat (obj)(str): The material or the material's name.
            size (list): Desired icon size.
            fallback_to_blank (bool): Whether to generate a blank swatch if fetching the material color fails.

        Returns:
            (obj) QIcon: The pixmap icon.
        """
        from qtpy.QtGui import QPixmap, QColor, QIcon

        try:
            # get the string name if a mat object is given.
            matName = mat.name() if not isinstance(mat, str) else mat

            # Determine the correct color attribute based on material type
            mat_type = pm.nodeType(matName)
            if mat_type == "standardSurface":
                color_attr = "baseColor"
            else:
                color_attr = "color"

            # convert from 0-1 to 0-255 value and then to an integer
            r = int(pm.getAttr(f"{matName}.{color_attr}R") * 255)
            g = int(pm.getAttr(f"{matName}.{color_attr}G") * 255)
            b = int(pm.getAttr(f"{matName}.{color_attr}B") * 255)
            pixmap = QPixmap(size[0], size[1])
            pixmap.fill(QColor.fromRgb(r, g, b))
        except Exception:
            if fallback_to_blank:
                pixmap = QPixmap(size[0], size[1])
                pixmap.fill(QColor(255, 255, 255, 0))  # Transparent blank swatch
            else:
                raise

        return QIcon(pixmap)

    @staticmethod
    @CoreUtils.undoable
    def convert_bump_to_normal(
        bump_file_node: Union[str, "pm.nt.File"],
        output_path: Optional[str] = None,
        intensity: float = 1.0,
        format_type: str = "opengl",
        filter_type: str = "3x3",
        wrap_mode: str = "black",
        create_file_node: bool = True,
        node_name: Optional[str] = None,
    ) -> Optional["pm.nt.File"]:
        """Convert a bump/height map to a normal map using Maya's bump2d node.

        This method creates a Maya node network to convert height/bump information
        into tangent-space normal maps compatible with various rendering pipelines.

        Parameters:
            bump_file_node (str/pm.nt.File): The source bump/height map file node.
            output_path (Optional[str]): Path to save the generated normal map.
                                       If None, creates in-memory conversion only.
            intensity (float): Bump depth/intensity. Higher values create more pronounced normals.
                              Range typically 0.1-5.0. Default is 1.0.
            format_type (str): Target format convention:
                              'opengl' - Standard OpenGL (Y+ up, typical for most engines)
                              'directx' - DirectX convention (Y- up, flipped green channel)
                              Default is 'opengl'.
            filter_type (str): Filtering method for normal calculation:
                              '3x3' - Standard 3x3 Sobel filter (balanced quality/performance)
                              '5x5' - 5x5 filter (higher quality, smoother gradients)
                              Default is '3x3'.
            wrap_mode (str): Edge handling for height sampling:
                            'black' - Treat edges as zero height
                            'clamp' - Extend edge pixels
                            'repeat' - Tile/wrap the texture
                            Default is 'black'.
            create_file_node (bool): If True, creates a file node for the output normal map.
                                   If False, returns the bump2d conversion node only.
            node_name (Optional[str]): Custom name for created nodes. Auto-generated if None.

        Returns:
            Optional[pm.nt.File]: The created file node for the normal map, or None if creation failed.

        Raises:
            ValueError: If bump_file_node doesn't exist or output_path is invalid.
            RuntimeError: If Maya node creation fails.

        Example:
            >>> # Convert existing bump map to OpenGL normal map
            >>> bump_node = pm.ls('file1')[0]  # Existing file node
            >>> normal_node = MatUtils.convert_bump_to_normal(
            ...     bump_node,
            ...     output_path="C:/textures/wall_normal.exr",
            ...     intensity=2.0,
            ...     format_type="opengl"
            ... )

            >>> # Create DirectX normal map with higher quality filtering
            >>> normal_node = MatUtils.convert_bump_to_normal(
            ...     "wallBumpFile",
            ...     intensity=1.5,
            ...     format_type="directx",
            ...     filter_type="5x5"
            ... )
        """
        # Validate and get the bump file node
        try:
            bump_node = pm.PyNode(bump_file_node)
            if not isinstance(bump_node, pm.nt.File):
                raise ValueError(f"Node {bump_file_node} is not a file node")
        except pm.MayaNodeError:
            raise ValueError(f"Bump file node {bump_file_node} does not exist")

        # Validate parameters
        if format_type not in ["opengl", "directx"]:
            raise ValueError("format_type must be 'opengl' or 'directx'")

        if filter_type not in ["3x3", "5x5"]:
            raise ValueError("filter_type must be '3x3' or '5x5'")

        if wrap_mode not in ["black", "clamp", "repeat"]:
            raise ValueError("wrap_mode must be 'black', 'clamp', or 'repeat'")

        if not 0.1 <= intensity <= 10.0:
            pm.warning(f"Intensity {intensity} is outside recommended range (0.1-10.0)")

        # Generate node names
        base_name = node_name or f"{bump_node.name()}_normal"
        bump2d_name = f"{base_name}_bump2d"

        try:
            # Create bump2d node for conversion
            bump2d_node = pm.shadingNode("bump2d", asUtility=True, name=bump2d_name)

            # Configure bump2d node parameters
            bump2d_node.bumpInterp.set(1)  # Bump mode (0=bump, 1=tangent space normal)
            bump2d_node.bumpDepth.set(intensity)

            # Set filtering method
            if filter_type == "5x5":
                # Use higher quality filtering if supported
                if hasattr(bump2d_node, "bumpFilter"):
                    bump2d_node.bumpFilter.set(1)  # 5x5 filter

            # Configure wrap mode for UV sampling
            wrap_value = {"black": 0, "clamp": 1, "repeat": 2}.get(wrap_mode, 0)

            # Connect the bump file to bump2d
            # Use outAlpha as standard for bump maps (height maps)
            pm.connectAttr(
                f"{bump_node.name()}.outAlpha", f"{bump2d_node.name()}.bumpValue"
            )

            # Handle format-specific adjustments
            if format_type == "directx":
                # DirectX typically needs Y-channel (green) flipped
                # Create a reverse node to flip the green channel
                reverse_name = f"{base_name}_reverse"
                reverse_node = pm.shadingNode(
                    "reverse", asUtility=True, name=reverse_name
                )

                # Create a channel mixer or use component masking to flip only green
                # For simplicity, we'll use a luminance node approach
                if hasattr(bump2d_node, "normalCamera"):
                    # Connect through reverse for Y-flip
                    pm.connectAttr(
                        f"{bump2d_node.name()}.outNormal",
                        f"{reverse_node.name()}.input",
                    )
                    output_attr = f"{reverse_node.name()}.output"
                else:
                    output_attr = f"{bump2d_node.name()}.outNormal"
            else:
                # OpenGL - use direct output
                output_attr = f"{bump2d_node.name()}.outNormal"

            # Create output file node if requested
            if create_file_node:
                if output_path:
                    # Validate output path
                    output_dir = os.path.dirname(output_path)
                    if output_dir and not os.path.exists(output_dir):
                        try:
                            os.makedirs(output_dir)
                        except OSError as e:
                            raise RuntimeError(
                                f"Cannot create output directory {output_dir}: {e}"
                            )

                # Create file node for the normal map
                normal_file_name = f"{base_name}_file"
                normal_file_node = pm.shadingNode(
                    "file", asTexture=True, name=normal_file_name
                )

                # Set file node properties for normal maps
                normal_file_node.colorSpace.set(
                    "Raw"
                )  # Important: don't color-correct normal data
                normal_file_node.alphaIsLuminance.set(False)

                if output_path:
                    normal_file_node.fileTextureName.set(output_path)

                    # If we need to bake/render the normal map, we'd use Maya's render setup here
                    # For now, we create the network and let users handle baking manually
                    pm.displayInfo(f"// Normal map conversion network created.")
                    pm.displayInfo(
                        f"// To bake the normal map, use Maya's Render > Batch Render"
                    )
                    pm.displayInfo(
                        f"// or Hypershade > Utilities > Surface Sampler Info"
                    )

                return normal_file_node
            else:
                return bump2d_node

        except Exception as e:
            pm.warning(f"Failed to create bump-to-normal conversion: {e}")
            return None

    @staticmethod
    def validate_normal_map_setup(
        normal_file_node: Union[str, "pm.nt.File"],
        material: Optional[Union[str, "pm.nt.DependNode"]] = None,
    ) -> Dict[str, Any]:
        """Validate normal map file node setup and provide recommendations.

        Parameters:
            normal_file_node (str/pm.nt.File): The normal map file node to validate.
            material (Optional[str/pm.nt.DependNode]): Associated material to check connections.

        Returns:
            Dict[str, Any]: Validation results with recommendations and issues found.
        """
        try:
            normal_node = pm.PyNode(normal_file_node)
            if not isinstance(normal_node, pm.nt.File):
                return {
                    "valid": False,
                    "error": f"Node {normal_file_node} is not a file node",
                }
        except pm.MayaNodeError:
            return {
                "valid": False,
                "error": f"Normal file node {normal_file_node} does not exist",
            }

        results = {
            "valid": True,
            "warnings": [],
            "recommendations": [],
            "color_space": None,
            "connected_to_normal": False,
            "file_exists": False,
        }

        # Check color space
        color_space = normal_node.colorSpace.get()
        results["color_space"] = color_space
        if color_space.lower() not in ["raw", "linear", "utility - raw"]:
            results["warnings"].append(
                f"Color space is '{color_space}'. Normal maps should use 'Raw' or 'Linear' "
                "to avoid gamma correction that corrupts normal data."
            )
            results["recommendations"].append("Set colorSpace to 'Raw'")

        # Check if file exists
        file_path = normal_node.fileTextureName.get()
        if file_path and os.path.exists(file_path):
            results["file_exists"] = True
        elif file_path:
            results["warnings"].append(f"Normal map file does not exist: {file_path}")

        # Check material connections if provided
        if material:
            try:
                mat_node = pm.PyNode(material)
                # Check if connected to bump or normal attributes
                connections = pm.listConnections(normal_node.outColor, plugs=True)
                normal_connections = [
                    c
                    for c in connections
                    if "normal" in c.name().lower() or "bump" in c.name().lower()
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

            except pm.MayaNodeError:
                results["warnings"].append(f"Material {material} does not exist")

        return results

    @staticmethod
    def graph_materials(
        materials: Union[str, List[str], object], mode: str = "showUpAndDownstream"
    ) -> None:
        """Open the Hypershade and graph the specified materials.

        Parameters:
            materials (str/list): The material(s) to graph.
            mode (str): The graphing mode.
                    Options: "graphMaterials", "addSelected", "showUpstream", "showDownstream", "showUpAndDownstream"
        """
        # Ensure materials are selected
        if not materials:
            return

        pm.select(materials)

        # Open Hypershade
        pm.mel.HypershadeWindow()

        # Graph the materials
        pm.evalDeferred(
            lambda: pm.mel.hyperShadePanelGraphCommand("hyperShadePanel1", mode)
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
