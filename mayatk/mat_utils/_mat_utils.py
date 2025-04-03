# !/usr/bin/python
# coding=utf-8
import os
from typing import List, Tuple, Union, Dict, Any, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.node_utils import NodeUtils
from mayatk.env_utils import EnvUtils


class MatUtils(ptk.HelpMixin):
    @staticmethod
    def get_mats(objs):
        """Returns the set of materials assigned to a given list of objects or components.

        Parameters:
            objs (list): The objects or components to retrieve the material from.

        Returns:
            list: Materials assigned to the objects or components (duplicates romoved).
        """
        # Initialize an empty set to store the materials
        mats = set()

        for obj in pm.ls(objs, flatten=True):
            shading_grps = []

            if isinstance(obj, pm.MeshFace):
                all_shading_grps = pm.ls(type="shadingEngine")
                shading_grps = [
                    sg for sg in all_shading_grps if pm.sets(sg, isMember=obj)
                ]

                if not shading_grps:
                    pm.hyperShade(obj, shaderNetworksSelectMaterialNodes=True)
                    mats_ = pm.ls(pm.selected(), materials=True)
                    mats.update(mats_)
            else:
                # Check if the object has a shape node
                shape = None
                if hasattr(obj, "getShape"):
                    shape = obj.getShape()

                if shape:
                    shading_grps = pm.listConnections(shape, type="shadingEngine")

            for shading_grp in shading_grps:
                materials = pm.ls(
                    pm.listConnections(f"{shading_grp}.surfaceShader"), materials=True
                )
                mats.update(materials)

        return list(mats)

    @staticmethod
    def get_scene_mats(
        inc: Union[str, int, list] = [],
        exc: Union[str, int, list] = [],
        sort: bool = False,
        as_dict: bool = False,
    ) -> Union[List[str], Dict[str, str]]:
        """Retrieves all materials from the current scene, optionally including or excluding certain materials by name.

        Parameters:
            inc (str/int/obj/list, optional): The objects to include in the search. Supports using the '*' operator for pattern matching. Defaults to [].
            exc (str/int/obj/list, optional): The objects to exclude from the search. Supports using the '*' operator for pattern matching. Defaults to [].
            sort (bool, optional): Whether to return the materials in alphabetical order. Defaults to False.
            as_dict (bool, optional): Whether to return the materials as a dictionary. Defaults to False.

        Returns:
            list or dict: A list or dictionary of materials in the scene.
        """
        matList = pm.ls(mat=True, flatten=True)
        d = {m.name(): m for m in matList}
        filtered = ptk.filter_dict(d, keys=True, map_func=pm.nodeType, inc=inc, exc=exc)

        if as_dict:
            return dict(sorted(filtered.items())) if sort else filtered

        filtered_mats = list(filtered.values())
        return sorted(filtered_mats, key=lambda x: x.name()) if sort else filtered_mats

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
        """Creates a material based on the provided type or a random Lambert material if 'mat_type' is 'random'.

        Parameters:
            mat_type (str): The type of the material, e.g. 'lambert', 'blinn', or 'random' for a random Lambert material.
            prefix (str, optional): An optional prefix to append to the material name. Defaults to "".
            name (str, optional): The name of the material. Defaults to "".

        Returns:
            obj: The created material.
        """
        import random

        if mat_type == "random":
            rgb = [
                random.randint(0, 255) for _ in range(3)
            ]  # Generate a list containing 3 values between 0-255
            name = "{}{}_{}_{}_{}".format(
                prefix, name, str(rgb[0]), str(rgb[1]), str(rgb[2])
            )
            mat = pm.shadingNode("lambert", asShader=True, name=name)
            convertedRGB = [round(float(v) / 255, 3) for v in rgb]
            pm.setAttr(name + ".color", convertedRGB)
        else:
            name = prefix + name if name else mat_type
            mat = pm.shadingNode(mat_type, asShader=True, name=name)

        return mat

    @staticmethod
    @CoreUtils.undoable
    def assign_mat(objects, mat_name):
        """Assigns a material to a list of objects or components.

        Parameters:
            objects (str/obj/list): The objects or components to assign the material to.
            mat_name (str): The name of the material to assign.
        """
        if not objects:
            raise ValueError("No objects provided to assign material.")

        try:  # Retrieve or create material
            mat = pm.PyNode(mat_name)
        except pm.MayaNodeError:
            mat = pm.shadingNode("lambert", name=mat_name, asShader=True)

        # Check if the material has a shading engine, otherwise create one
        shading_groups = mat.listConnections(type="shadingEngine")
        if not shading_groups:
            shading_group = pm.sets(
                name=f"{mat_name}SG", renderable=True, noSurfaceShader=True, empty=True
            )
            pm.connectAttr(
                f"{mat}.outColor", f"{shading_group}.surfaceShader", force=True
            )
        else:
            shading_group = shading_groups[0]

        # Filter for valid objects and assign the material
        valid_objects = pm.ls(objects, type="transform", flatten=True)
        pm.sets(shading_group, forceElement=valid_objects)

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
        if pm.nodeType(material) == "VRayMultiSubTex":
            raise TypeError(
                "Invalid material type. If material is a multimaterial, please select a submaterial."
            )

        if not pm.objExists(material):
            print(f"Material '{material}' does not exist.")
            return []

        shading_groups = pm.listConnections(material, type="shadingEngine")
        if not shading_groups:
            print(f"No shading groups found for material '{material}'.")
            return []

        objs_with_material = []
        transform_nodes = NodeUtils.get_transform_node(objects)

        for sg in shading_groups:
            members = pm.sets(sg, query=True, noIntermediate=True)
            for member in members:
                # Check if the member is a face component directly
                if isinstance(member, pm.MeshFace):
                    if not shell:
                        objs_with_material.append(member)
                    else:
                        transform_node = member.node().getParent()
                        if transform_node not in objs_with_material:
                            objs_with_material.append(transform_node)
                else:
                    # Handle other types (like full mesh objects)
                    transform_node = NodeUtils.get_transform_node(member)
                    if objects and transform_node not in transform_nodes:
                        continue
                    if shell:
                        if transform_node not in objs_with_material:
                            objs_with_material.append(transform_node)
                    else:
                        faces = transform_node.faces
                        for face in faces:
                            if sg in pm.listSets(object=face, type=1):
                                objs_with_material.append(face)

        return objs_with_material

    @staticmethod
    @ptk.filter_results
    def collect_material_paths(
        materials: Optional[List[str]] = None,
        attributes: Optional[List[str]] = None,
        inc_material: bool = False,
        inc_path_type: bool = False,
    ) -> Union[List[str], List[Tuple[str, ...]]]:
        """Collects specified attributes file paths for given materials.

        Parameters:
            materials (Optional[List[str]]): List of material names.
            attributes (Optional[List[str]]): List of attributes to collect file paths from. Defaults to texture files.
            inc_material (bool): If True, include material name in the result.
            inc_path_type (bool): If True, include path type (Relative/Absolute) in the result.

        Returns:
            Union[List[str], List[Tuple[str, ...]]]: List of file paths or tuples containing the requested information.
        """

        materials = pm.ls(materials, mat=True) or pm.ls(mat=True)
        attributes = attributes or ["fileTextureName"]

        material_paths = []
        project_sourceimages = EnvUtils.get_env_info("sourceimages")

        for material in materials:
            for attr in attributes:
                file_nodes = pm.listConnections(material, type="file")
                for file_node in file_nodes:
                    try:
                        file_path = file_node.attr(attr).get()
                        if not file_path:
                            continue

                        # Ensure path is stored as Maya expects
                        file_path = file_path.replace("\\", "/")

                        # Determine if the path is relative or absolute
                        path_type = (
                            "Relative"
                            if file_path.startswith(project_sourceimages)
                            else "Absolute"
                        )
                        relative_path = (
                            os.path.relpath(file_path, project_sourceimages).replace(
                                "\\", "/"
                            )
                            if path_type == "Relative"
                            else file_path
                        )

                        entry = (relative_path,)
                        if inc_material:
                            entry = (material,) + entry
                        if inc_path_type:
                            entry = entry[:1] + (path_type,) + entry[1:]

                        material_paths.append(entry)
                    except pm.MayaAttributeError:
                        continue

        return material_paths

    @staticmethod
    def _remap_file_nodes(
        file_paths: List[str], target_dir: str, silent: bool = False
    ) -> List["pm.nt.File"]:
        """Internal helper to remap file nodes to target_dir, preserving relative subfolders inside sourceimages.

        Parameters:
            file_paths (List[str]): List of file paths to remap.
            target_dir (str): Target directory to remap the file nodes to.
            silent (bool): If True, suppresses output messages.

        Returns:
            List[pm.nt.File]: List of remapped file nodes.
        """
        sourceimages_dir = EnvUtils.get_env_info("sourceimages")
        sourceimages_dir_norm = os.path.normpath(sourceimages_dir).replace("\\", "/")

        file_nodes: Dict[str, pm.nt.File] = {}

        # Build lookup: rel path if under sourceimages, else just filename
        for fn in pm.ls(type="file"):
            file_path = fn.fileTextureName.get()
            if not file_path:
                continue
            file_path_norm = os.path.normpath(file_path).replace("\\", "/")
            if file_path_norm.lower().startswith(sourceimages_dir_norm.lower()):
                rel_key = (
                    os.path.relpath(file_path_norm, sourceimages_dir_norm)
                    .replace("\\", "/")
                    .lower()
                )
                file_nodes[rel_key] = fn
            else:
                filename = os.path.basename(file_path_norm).lower()
                file_nodes[filename] = fn

        remapped_nodes: List[pm.nt.File] = []
        remap_data = ptk.remap_file_paths(file_paths, target_dir, sourceimages_dir)

        for key, new_full_path, maya_path in remap_data:
            if key in file_nodes:
                if not silent:
                    pm.displayInfo(f"\n[Remap Attempt]")
                    pm.displayInfo(f"  original path: {new_full_path}")
                    pm.displayInfo(f"  lookup key:    {key}")
                    pm.displayInfo(f"  maya path:     {maya_path}")
                    pm.displayInfo(f"  remapped:      {file_nodes[key].name()}")
                file_nodes[key].fileTextureName.set(maya_path)
                remapped_nodes.append(file_nodes[key])
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
    ) -> None:
        """Remaps file texture paths for materials to new_dir, using relative paths if inside sourceimages.

        Parameters:
            materials (Optional[List[str]]): List of material names to remap. Defaults to all materials.
            new_dir (Optional[str]): Target directory to remap the file nodes to. Defaults to sourceimages.
            silent (bool): If True, suppresses output messages.
        """
        new_dir = new_dir or EnvUtils.get_env_info("sourceimages")
        if not new_dir or not os.path.isdir(new_dir):
            pm.warning(f"Invalid directory: {new_dir}")
            return

        materials = pm.ls(materials, mat=True) if materials else None
        textures = cls.collect_material_paths(materials=materials, inc_material=False)
        textures = [t[0] if isinstance(t, tuple) else t for t in textures]

        if not textures:
            pm.warning("No valid texture paths found.")
            return

        remapped_nodes = cls._remap_file_nodes(
            file_paths=textures, target_dir=new_dir, silent=silent
        )
        if not silent:
            pm.displayInfo(
                f"// Result: Remapped {len(remapped_nodes)}/{len(textures)} texture paths."
            )

    @classmethod
    @CoreUtils.undoable
    def migrate_textures(
        cls,
        materials: Optional[List[str]] = None,
        old_dir: Optional[str] = None,
        new_dir: Optional[str] = None,
        silent: bool = False,
    ) -> None:
        """Copies texture files from an old directory to a new one and remaps file nodes accordingly.

        Parameters:
            materials (Optional[List[str]]): List of material names to process. Defaults to all materials.
            old_dir (Optional[str]): Source directory containing the original texture files.
            new_dir (Optional[str]): Target directory to copy the texture files to.
            silent (bool): If True, suppresses output messages.
        """

        for label, path in (("old_dir", old_dir), ("new_dir", new_dir)):
            if not path or not os.path.exists(path) or not os.path.isdir(path):
                pm.warning(f"{label} is invalid: {path}")
                return

        textures = cls.collect_material_paths(materials=materials, inc_material=False)
        filenames = set(
            os.path.basename(tex[0] if isinstance(tex, tuple) else tex)
            for tex in textures
            if tex
        )

        copied_files: Dict[str, str] = {}
        copied_count = 0

        for filename in filenames:
            src_path = os.path.join(old_dir, filename)
            if not os.path.isfile(src_path):
                if not silent:
                    pm.warning(f"// Skipped copy: {filename} not found in old_dir.")
                continue

            try:
                copied_path = ptk.copy_file(src_path, new_dir, create_dir=True)
                copied_files[filename] = copied_path
                copied_count += 1
                if not silent:
                    pm.displayInfo(f"// Copied: {src_path} -> {copied_path}")
            except Exception as e:
                pm.warning(f"// Failed to copy {filename}: {e}")

        if not silent:
            pm.displayInfo(f"// Result: Copied {copied_count} textures.")

        if copied_files:
            remapped = cls._remap_file_nodes(
                file_paths=[os.path.join(old_dir, fname) for fname in copied_files],
                target_dir=new_dir,
                silent=silent,
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
        materials: Optional[List["pm.nt.DependNode"]] = None,
        strict: bool = False,
    ) -> Dict["pm.nt.DependNode", List["pm.nt.DependNode"]]:
        """Find duplicate materials based on their texture file names or full paths.

        Parameters:
            materials (Optional[List[pm.nt.DependNode]]): List of material nodes.
            strict (bool): Whether to compare using full paths (True) or just file names (False).

        Returns:
            Dict[pm.nt.DependNode, List[pm.nt.DependNode]]:
                Each key is the original material, and each value is a list of duplicate materials.
        """

        def get_texture_name(path: str) -> str:
            """Extracts the core file name without the path or extension."""
            filename = os.path.basename(path).lower()
            return os.path.splitext(filename)[0]

        materials = pm.ls(materials, mat=True) if materials else pm.ls(mat=True)
        # Dictionary to store relevant material data (texture names or paths)
        material_data = {}
        for material in materials:
            # Collect file nodes connected to the material or its shading engine
            file_nodes = pm.listConnections(
                material, source=True, destination=False, type="file"
            )
            # Check shading engine connections if no direct file nodes
            if not file_nodes:
                shading_engines = pm.listConnections(material, type="shadingEngine")
                file_nodes = [
                    file
                    for engine in shading_engines
                    for file in pm.listConnections(
                        engine, source=True, destination=False, type="file"
                    )
                ]

            if not file_nodes:  # Skip materials without file nodes
                continue

            # Collect texture paths or names based on 'strict' flag
            if strict:  # Use full paths for comparison when strict is True
                texture_names = [
                    pm.getAttr(f"{file_node}.fileTextureName").lower()
                    for file_node in file_nodes
                    if pm.objExists(f"{file_node}.fileTextureName")
                ]
            else:  # Use only the texture names without paths or extensions
                texture_names = [
                    get_texture_name(pm.getAttr(f"{file_node}.fileTextureName"))
                    for file_node in file_nodes
                    if pm.objExists(f"{file_node}.fileTextureName")
                ]
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
                materials_list.sort(key=lambda x: (len(x.name()), x.name()))
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
        if materials:  # Filter out invalid objects and warn about them
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
            original_sgs = original.shadingGroups()
            if not original_sgs:
                continue
            original_sg = original_sgs[0]

            # Reassign all duplicates to the original material
            for duplicate in duplicates:
                try:  # Get the shading groups of the duplicate material
                    duplicate_sgs = duplicate.shadingGroups()
                    for dup_sg in duplicate_sgs:
                        # Get the members (faces or objects) of the duplicate shading group
                        members = pm.sets(dup_sg, q=True)
                        if members:
                            # Reassign the faces or objects to the original shading group
                            pm.sets(original_sg, forceElement=members)
                            print(
                                f"Reassigned material from {duplicate} to {original} on members: {members}"
                            )
                    # Add the duplicate material to the deletion list
                    duplicates_to_delete.append(duplicate)
                except pm.MayaAttributeError as e:
                    print(f"Error processing material {duplicate}: {e}")
                    continue
                except pm.MayaNodeError as e:
                    print(f"Error with shading group nodes for {duplicate}: {e}")
                    continue
        if delete:  # Delete all duplicate materials after successful reassignment
            for duplicate in duplicates_to_delete:
                try:
                    pm.delete(duplicate)
                    print(f"Deleted duplicate material: {duplicate}")
                except pm.MayaAttributeError as e:
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
    ):
        """Reloads textures connected to specified materials with inclusion/exclusion filters.

        Parameters:
            materials (str/obj/list): Material or list of materials to process. Defaults to all materials in the scene.
            inc (str/list): Inclusion patterns for filtering textures.
            exc (str/list): Exclusion patterns for filtering textures.
            log (bool): Whether to log the textures being reloaded.
            refresh_viewport (bool): Whether to refresh the viewport.
            refresh_hypershade (bool): Whether to refresh the Hypershade panel.
        """
        materials = pm.ls(materials, mat=True) if materials else pm.ls(mat=True)

        texture_types = ["file", "aiImage", "pxrTexture", "imagePlane"]
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
            # convert from 0-1 to 0-255 value and then to an integer
            r = int(pm.getAttr(f"{matName}.colorR") * 255)
            g = int(pm.getAttr(f"{matName}.colorG") * 255)
            b = int(pm.getAttr(f"{matName}.colorB") * 255)
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
    def calculate_uv_padding(
        map_size: int, normalize: bool = False, factor: int = 128
    ) -> float:
        """Calculate the UV padding for a given map size to ensure consistent texture padding across different resolutions.
        Optionally return the padding as a normalized value relative to the map size.

        Parameters:
        map_size (int): The size of the map for which to calculate UV padding, typically the width or height in pixels.
        normalize (bool): If True, returns the padding as a normalized value. Default is False.
        factor (int): The factor by which to divide the map size to calculate the padding. Default is 128.

        Returns:
        float: The calculated padding in pixels or normalized units. Ensures that a 4K (4096 pixels) map gets exactly 32 pixels of padding.

        Expected Output:
        - For a 1024 pixel map: 8.0 pixels of padding or 0.0078125 if normalized
        - For a 2048 pixel map: 16.0 pixels of padding or 0.0078125 if normalized
        - For a 4096 pixel map: 32.0 pixels of padding or 0.0078125 if normalized
        - For a 8192 pixel map: 64.0 pixels of padding or 0.0078125 if normalized

        Example:
        >>> calculate_uv_padding(4096, normalize=True)
        0.0078125
        """
        padding = map_size / factor
        if normalize:
            return padding / map_size
        return padding


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
