# !/usr/bin/python
# coding=utf-8
from typing import List

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import node_utils


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
    def get_scene_mats(inc=[], exc=[]):
        """Retrieves all materials from the current scene, optionally including or excluding certain objects.

        Parameters:
            inc (str/int/obj/list, optional): The objects to include in the search. Supports using the '*' operator for pattern matching. Defaults to [].
            exc (str/int/obj/list, optional): The objects to exclude from the search. Supports using the '*' operator for pattern matching. Defaults to [].

        Returns:
            list: A list of materials in the scene.
        """
        matList = pm.ls(mat=1, flatten=1)

        # convert to dictionary to filter material names and types.
        d = {m.name(): pm.nodeType(m) for m in matList}
        filtered = ptk.filter_dict(d, inc, exc, keys=True, values=True)

        # use the filtered results to reconstruct a filtered list of actual materials.
        return [m for m in matList if m.name() in filtered]

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

        Args:
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
        objects = node_utils.NodeUtils.get_transform_node(objects)

        # Find the shading groups associated with the material
        shading_groups = pm.listConnections(material, type="shadingEngine")

        objs_with_material = []
        for sg in shading_groups:
            connected_objs = pm.sets(sg, query=True, noIntermediate=True)
            flattened = pm.ls(connected_objs, flatten=True)
            # Only add objects to the list if they are in the specified objects list
            for obj in flattened:
                transform_node = node_utils.NodeUtils.get_transform_node(obj)
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
    def reload_textures(materials=None, inc=None, exc=None):
        """Reloads textures connected to specified materials with inclusion/exclusion filters.

        Parameters:
            materials (str/obj/list): Material or list of materials to process. Defaults to all materials in the scene.
            inc (str/list): Inclusion patterns for filtering textures.
            exc (str/list): Exclusion patterns for filtering textures.
        """
        materials = pm.ls(materials) if materials else pm.ls(mat=True)

        file_nodes = []
        for material in materials:
            # Traverse the connections to find file nodes
            file_nodes.extend(pm.listConnections(material, type="file"))

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
            # Reload the texture by resetting the file path
            file_path = fn.fileTextureName.get()
            fn.fileTextureName.set(file_path)

    @staticmethod
    def get_mat_swatch_icon(mat, size=[20, 20]):
        """Get an icon with a color fill matching the given materials RBG value.

        Parameters:
            mat (obj)(str): The material or the material's name.
            size (list): Desired icon size.

        Returns:
            (obj) pixmap icon.
        """
        from PySide2.QtGui import QPixmap, QColor, QIcon

        try:
            # get the string name if a mat object is given.
            matName = mat.name() if not isinstance(mat, (str)) else mat
            # convert from 0-1 to 0-255 value and then to an integer
            r = int(pm.getAttr(matName + ".colorR") * 255)
            g = int(pm.getAttr(matName + ".colorG") * 255)
            b = int(pm.getAttr(matName + ".colorB") * 255)
            pixmap = QPixmap(size[0], size[1])
            pixmap.fill(QColor.fromRgb(r, g, b))

            return QIcon(pixmap)

        except Exception:
            pass

    @staticmethod
    def calculate_uv_padding(map_size, normalize=False):
        """Calculate the UV padding for a given map size to ensure consistent texture padding across different resolutions.
        Optionally return the padding as a normalized value relative to the map size.

        Parameters:
        map_size (int): The size of the map for which to calculate UV padding, typically the width or height in pixels.
        normalize (bool): If True, returns the padding as a normalized value. Default is False.

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
        padding = map_size / 128
        if normalize:
            return padding / map_size
        return padding


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
