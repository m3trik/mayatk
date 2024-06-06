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
    def find_by_mat_id(cls, material, objects=None, shell=False):
        """Find objects or faces by the material ID in a 3D scene.

        This function takes as input a material and a set of objects (or entire scene by default) and
        returns a list of objects or faces that are associated with the input material.

        If the 'shell' parameter is set to True, this function returns the complete objects that have
        the input material. If 'shell' is set to False, the function returns the individual faces that
        have the input material.

        Note: If the material is a multi-material (such as VRayMultiSubTex), an error will be raised.

        Parameters:
            cls: The class object. This argument is implicitly passed and doesn't need to be provided by the user.
            material (str): The material (e.g. 'lambert1') used to find the associated objects/faces.
            objects (list, optional): List of objects (e.g. ['pCube1', 'pCube2']) to be considered.
                                      If not provided, all objects in the scene will be considered.
            shell (bool, optional): Determines whether to return complete objects (True) or individual faces (False).
                                    Default is False.

        Raises:
            TypeError: If material is a multimaterial.

        Returns:
            list: A list of objects or faces (depending on 'shell' parameter) that are associated with the input material.

        """
        if pm.nodeType(material) == "VRayMultiSubTex":
            raise TypeError(
                "Invalid material type. If material is a multimaterial, please select a submaterial."
            )
        # If objects are not specified, consider all objects in the scene
        if not objects:
            objects = pm.ls(geometry=True)  # Get all mesh objects

        # Convert mesh shapes to transform nodes
        objects = NodeUtils.get_transform_node(objects)

        # Find the shading groups associated with the material
        shading_groups = pm.listConnections(material, type="shadingEngine")

        objs_with_material = []
        for sg in shading_groups:
            connected_objs = pm.sets(sg, query=True, noIntermediate=True)
            flattened = pm.ls(connected_objs, flatten=True)
            # Only add objects to the list if they are in the specified objects list
            for obj in flattened:
                transform_node = NodeUtils.get_transform_node(obj)
                if transform_node in objects:
                    # Check if the object is a face. If not, convert to faces
                    if not isinstance(obj, pm.MeshFace):
                        shape_node = pm.listRelatives(transform_node, shapes=True)[0]
                        face_count = pm.polyEvaluate(shape_node, f=True)
                        faces = [f"{shape_node}.f[{i}]" for i in range(face_count)]
                        objs_with_material.extend(faces)
                    else:
                        objs_with_material.append(obj)

        if shell:
            objs_with_material = set(pm.ls(objs_with_material, objectsOnly=True))

        return objs_with_material

    @staticmethod
    def collect_material_paths(
        materials=None, ignore: List[str] = None
    ) -> List[Tuple[str, str, str]]:
        """Collect the file paths of textures connected to the given materials.

        Parameters:
            materials (List[str]): List of material names.
            ignore (List[str], optional): List of strings to check if the file path ends with. Defaults to None.

        Returns:
            List[Tuple[str, str, str]]: List of tuples containing the material name,
                                        path type ('Relative' or 'Absolute'), and the file path.
        """
        materials = pm.ls(materials) if materials else pm.ls(mat=True)

        ignore = ignore or []
        results = []
        source_images_dir = CoreUtils.get_maya_info("sourceimages")

        for material in materials:
            file_nodes = pm.listConnections(material, type="file")
            if file_nodes:
                for file_node in file_nodes:
                    file_path = pm.getAttr(f"{file_node}.fileTextureName").strip()
                    if file_path and not any(file_path.endswith(i) for i in ignore):
                        if os.path.isabs(file_path):
                            if file_path.startswith(source_images_dir):
                                relative_path = os.path.relpath(
                                    file_path, source_images_dir
                                )
                                results.append((material, "Relative", relative_path))
                            else:
                                results.append((material, "Absolute", file_path))
                        else:
                            results.append((material, "Relative", file_path))

        return results

    @staticmethod
    def get_material_properties(material: str) -> Dict[str, Any]:
        """Get the properties of a given material including shader type, attributes, and texture paths.

        Parameters:
            material (str): The name of the material.

        Returns:
            Dict[str, Any]: Dictionary containing the shader type, attributes, and texture paths.
        """
        properties = {"shader_type": pm.nodeType(material), "attributes": {}}
        common_attrs = [
            "color",
            "transparency",
            "ambientColor",
            "incandescence",
            "specularColor",
        ]
        for attr in common_attrs:
            if pm.attributeQuery(attr, node=material, exists=True):
                properties["attributes"][attr] = pm.getAttr(f"{material}.{attr}")

        file_textures = pm.listConnections(material, type="file")
        texture_paths = [
            pm.getAttr(f"{file}.fileTextureName") for file in file_textures
        ]
        properties["textures"] = sorted(texture_paths)

        return properties

    @classmethod
    def find_duplicate_materials(cls, materials=None) -> List[str]:
        """Find duplicate materials based on their properties.

        Parameters:
            materials (List[str]): List of material names.

        Returns:
            List[str]: List of duplicate material names.
        """
        materials = pm.ls(materials) if materials else pm.ls(mat=True)
        material_properties = [
            cls.get_material_properties(material) for material in materials
        ]
        duplicates = []
        for i, mat_props in enumerate(material_properties):
            for j in range(i + 1, len(material_properties)):
                if mat_props == material_properties[j]:
                    duplicates.append(materials[j])

        return duplicates

    @classmethod
    @CoreUtils.undo
    def remove_and_reassign_duplicates(cls, materials: List[str] = None) -> None:
        """Find duplicate materials, remove duplicates, and reassign them to the original material.

        Parameters:
            materials (List[str]): List of material names.
        """
        materials = pm.ls(materials) if materials else pm.ls(mat=True)
        duplicates = cls.find_duplicate_materials(materials)

        # Create a mapping from duplicate to original
        duplicate_to_original = {}
        for duplicate in duplicates:
            for material in materials:
                if material != duplicate and cls.get_material_properties(
                    material
                ) == cls.get_material_properties(duplicate):
                    duplicate_to_original[duplicate] = material
                    break

        for duplicate, original in duplicate_to_original.items():
            # Find all objects assigned the duplicate material and reassign to the original material
            shading_engines = pm.listConnections(duplicate, type="shadingEngine")
            for shading_engine in shading_engines:
                connected_objects = pm.sets(shading_engine, query=True)
                if connected_objects:
                    pm.hyperShade(objects=duplicate)
                    pm.hyperShade(assign=original)
                    print(
                        f"Reassigned {len(connected_objects)} objects from {duplicate} to {original}"
                    )

            # Remove the duplicate material
            pm.delete(duplicate)
            print(f"Deleted duplicate material: {duplicate}")

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
        materials=None,
        copy_missing_files: bool = False,
        use_workspace_drive: bool = False,
    ) -> None:
        """Convert absolute file paths to relative paths for file texture nodes.

        This function processes file texture nodes to convert their
        absolute file paths to relative paths based on the current workspace's 'sourceimages' directory.
        If the file does not exist in the 'sourceimages' directory, it can optionally copy the file from
        the absolute path and use the drive letter of the current workspace.

        Parameters:
            materials (List[str], optional): List of material names to filter. If None, all materials are processed.
            copy_missing_files (bool): If True, attempts to copy missing files to the 'sourceimages' directory if they do not exist.
            use_workspace_drive (bool): If True, substitutes the drive letter of the absolute path with the drive letter of the current workspace.

        Raises:
            FileNotFoundError: If the 'sourceimages' directory does not exist.
        """
        import shutil

        workspace_path = pm.workspace.path
        sourceimages_path = os.path.join(workspace_path, "sourceimages")

        if not os.path.exists(sourceimages_path):
            raise FileNotFoundError(
                f"The 'sourceimages' directory does not exist: {sourceimages_path}"
            )

        absolute_paths_found = False

        materials = pm.ls(materials) if materials else pm.ls(mat=True)

        for material in materials:
            file_nodes = pm.listConnections(material, type="file")

            for file_node in file_nodes:
                file_path = file_node.fileTextureName.get()

                file_name = os.path.basename(file_path)
                relative_path = os.path.join("sourceimages", file_name)
                expected_relative_path = os.path.join(sourceimages_path, file_name)

                # Check if the file path is already relative by comparing with the expected relative path
                if os.path.abspath(file_path) == os.path.abspath(
                    expected_relative_path
                ):
                    # Silently set the relative path just to be safe.
                    file_node.fileTextureName.set(relative_path)
                    continue

                absolute_paths_found = True

                # Convert to relative path
                if os.path.isabs(file_path):
                    if os.path.exists(os.path.join(sourceimages_path, file_name)):
                        file_node.fileTextureName.set(relative_path)
                        print(
                            f"Set relative path for node {file_node}: {relative_path}"
                        )
                    else:
                        if copy_missing_files and os.path.exists(file_path):
                            shutil.copy(file_path, sourceimages_path)
                            file_node.fileTextureName.set(relative_path)
                            print(
                                f"Copied and set relative path for node {file_node}: {relative_path}"
                            )
                        elif copy_missing_files:
                            print(f"File not found to copy: {file_path}")

                        if use_workspace_drive:
                            workspace_drive_letter = workspace_path[0]
                            absolute_drive_letter = file_path[0]
                            new_file_path = file_path.replace(
                                absolute_drive_letter, workspace_drive_letter, 1
                            )  # Check if path needs to be updated
                            if file_path != new_file_path:
                                file_node.fileTextureName.set(new_file_path)
                                print(
                                    f"Set new file path for node {file_node} using workspace drive: {new_file_path}"
                                )

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
        materials = pm.ls(materials) if materials else pm.ls(mat=True)

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
