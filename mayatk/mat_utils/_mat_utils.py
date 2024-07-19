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


class MatUtils(ptk.HelpMixin):
    """ """

    @staticmethod
    def get_mats(objs):
        """Returns the set of materials assigned to a given list of objects or components.

        Parameters:
            objs (list): The objects or components to retrieve the material from.

        Returns:
            set: The set of materials assigned to the objects or components.
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

        return mats

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
    @CoreUtils.undo
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
    @CoreUtils.undo
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
        for obj in valid_objects:
            pm.sets(shading_group, forceElement=obj)

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
        include_material: bool = False,
        include_path_type: bool = False,
    ) -> Union[List[str], List[Tuple[str, ...]]]:
        """Collects specified attributes file paths for given materials.

        Parameters:
            materials (Optional[List[str]]): List of material names.
            attributes (Optional[List[str]]): List of attributes to collect file paths from. Defaults to texture files.
            include_material (bool): If True, include material name in the result.
            include_path_type (bool): If True, include path type (Relative/Absolute) in the result.

        Returns:
            Union[List[str], List[Tuple[str, ...]]]: List of file paths or tuples containing the requested information.
        """

        def strip_drive_and_filename(path: str) -> str:
            """Strip the drive letter and filename from a given path."""
            drive, path_without_drive = os.path.splitdrive(path)
            directory = os.path.dirname(path_without_drive)
            return directory.replace("\\", "/").lower()  # Normalize for comparison

        materials = pm.ls(materials, mat=True) or pm.ls(mat=True)
        attributes = attributes or ["fileTextureName"]

        material_paths = []
        project_sourceimages = CoreUtils.get_maya_info("sourceimages")

        stripped_project_path = strip_drive_and_filename(project_sourceimages)

        for material in materials:
            for attr in attributes:
                file_nodes = pm.listConnections(material, type="file")
                for file_node in file_nodes:
                    try:
                        file_path = file_node.attr(attr).get()
                        if not file_path:
                            continue

                        # Strip the drive letter and filename
                        stripped_file_path = strip_drive_and_filename(file_path)

                        # Determine if the path is relative or absolute
                        if stripped_file_path.startswith(stripped_project_path):
                            path_type = "Relative"
                            relative_path = os.path.relpath(
                                file_path, project_sourceimages
                            )
                        else:
                            path_type = "Absolute"
                            relative_path = file_path

                        entry = (relative_path,)
                        if include_material:
                            entry = (material,) + entry
                        if include_path_type:
                            entry = entry[:1] + (path_type,) + entry[1:]
                        material_paths.append(entry)
                    except pm.MayaAttributeError:
                        continue

        return material_paths

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
        cls, materials: Optional[List[object]] = None
    ) -> Dict[object, List[object]]:
        """Find duplicate materials based on their texture file paths.

        Parameters:
            materials (Optional[List[pm.nodetypes.ShadingNode]]): List of material nodes.

        Returns:
            Dict[pm.nodetypes.ShadingNode, List[pm.nodetypes.ShadingNode]]: Dictionary mapping original material nodes to lists of duplicate material nodes.
        """
        materials = pm.ls(materials, mat=True) if materials else pm.ls(mat=True)
        material_paths = cls.collect_material_paths(
            materials, include_material=True, include_path_type=True
        )

        # Create a dictionary to track unique texture sets
        material_textures: Dict[object, set] = {}
        duplicates: Dict[object, List[object]] = {}

        for material, _, file_name in material_paths:
            if material not in material_textures:
                material_textures[material] = set()
            material_textures[material].add(file_name)

        # Find duplicates using the is_duplicate method
        texture_sets = {}
        for material, textures in material_textures.items():
            textures_tuple = tuple(sorted(textures))  # Sort to ensure consistency
            if textures_tuple in texture_sets:
                texture_sets[textures_tuple].append(material)
            else:
                texture_sets[textures_tuple] = [material]

        for textures, materials in texture_sets.items():
            if len(materials) > 1:
                # Sort by name length first, then alphabetically
                materials.sort(key=lambda x: (len(x.name()), x.name()))
                original = materials[0]
                duplicates[original] = materials[1:]  # All others are duplicates

        return duplicates

    @classmethod
    @CoreUtils.undo
    def reassign_duplicates(
        cls, materials: List[str] = None, delete: bool = False
    ) -> None:
        """Find duplicate materials, remove duplicates, and reassign them to the original material.

        Parameters:
            materials (List[str]): List of material names.
            delete (bool): Whether to delete the duplicate materials after reassignment.
        """
        materials = pm.ls(materials, mat=True) if materials else pm.ls(mat=True)
        duplicate_to_original = cls.find_materials_with_duplicate_textures(materials)
        duplicates_to_delete = []

        for original, duplicates in duplicate_to_original.items():
            for duplicate in duplicates:
                try:  # Find all faces assigned the duplicate material and reassign to the original material
                    faces_with_duplicate = cls.find_by_mat_id(duplicate, shell=False)
                    print("faces_with_duplicate:", faces_with_duplicate)
                    if faces_with_duplicate:
                        pm.hyperShade(assign=original, objects=faces_with_duplicate)
                        print(
                            f"Reassigned material from {duplicate} to {original} on faces: {faces_with_duplicate}"
                        )
                        # Add the duplicate material to the deletion list
                        duplicates_to_delete.append(duplicate)
                except pm.MayaAttributeError as e:
                    print(f"Error processing material {duplicate}: {e}")
                    continue

        if delete:  # Delete all duplicate materials after reassignments
            for duplicate in duplicates_to_delete:
                try:
                    pm.delete(duplicate)
                    print(f"Deleted duplicate material: {duplicate}")
                except pm.MayaAttributeError as e:
                    print(f"Error deleting material {duplicate}: {e}")

    @staticmethod
    def filter_materials_by_objects(objects: List[str]) -> List[str]:
        """Filter materials assigned to the given objects.

        Parameters:
            objects (List[str]): List of object names.

        Returns:
            List[str]: List of material names assigned to the given objects.
        """
        assigned_materials = set()
        for obj in objects:
            # Get shape nodes if the object is a transform
            shapes = pm.listRelatives(obj, shapes=True, fullPath=True) or [obj]
            for shape in shapes:
                shading_groups = pm.listConnections(shape, type="shadingEngine")
                for sg in shading_groups:
                    materials = pm.listConnections(f"{sg}.surfaceShader")
                    assigned_materials.update(materials)
        return list(assigned_materials)

    @staticmethod
    @CoreUtils.undo
    def convert_to_relative_paths(
        items: Optional[List[str]] = None,
    ) -> None:
        """Convert absolute file paths to relative paths for file texture nodes.

        This function processes file texture nodes to convert their
        absolute file paths to relative paths based on the current workspace's 'sourceimages' directory.

        Parameters:
            items (List[str], optional): List of material or file node names to filter. If None, all items are processed.

        Raises:
            FileNotFoundError: If the 'sourceimages' directory does not exist.
        """
        sourceimages_path = CoreUtils.get_maya_info("sourceimages")

        if not os.path.exists(sourceimages_path):
            raise FileNotFoundError(
                f"The 'sourceimages' directory does not exist: {sourceimages_path}"
            )

        absolute_paths_found = False

        # Get all materials and file nodes if items are not provided
        if not items:
            items = pm.ls(type="file")

        file_nodes = []
        for item in items:
            if pm.nodeType(item) == "file":
                file_nodes.append(item)
            else:  # Assume it's a material and find connected file nodes
                file_nodes.extend(pm.listConnections(item, type="file"))

        # Remove duplicates
        file_nodes = list(set(file_nodes))
        for file_node in file_nodes:
            file_path = file_node.fileTextureName.get()

            file_name = os.path.basename(file_path)
            relative_path = os.path.join("sourceimages", file_name)
            expected_relative_path = os.path.join(sourceimages_path, file_name)

            # Check if the file path is already relative by comparing with the expected relative path
            if os.path.abspath(file_path) == os.path.abspath(expected_relative_path):
                # Silently set the relative path just to be safe.
                file_node.fileTextureName.set(relative_path)
                continue

            absolute_paths_found = True

            # Convert to relative path
            if os.path.isabs(file_path) and os.path.exists(
                os.path.join(sourceimages_path, file_name)
            ):
                file_node.fileTextureName.set(relative_path)
                print(f"Set relative path for node {file_node}: {relative_path}")

        if not absolute_paths_found:
            print("No absolute paths found.")

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

        project_sourceimages = source_dir or CoreUtils.get_maya_info("sourceimages")
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
        from PySide2.QtGui import QPixmap, QColor, QIcon

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
