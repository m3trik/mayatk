# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import node_utils


class MatUtils(object):
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
    def assign_mat(objects, mat):
        """Assigns a material to a list of objects or components.

        Parameters:
            objects (list): The objects or components to assign the material to.
            mat (obj): The material to assign.
        """
        try:
            pm.nodeType(mat)
        except Exception:
            mat = pm.shadingNode(mat, asShader=True)

        # Check for existing shading group connected to the material
        shading_groups = mat.listConnections(type="shadingEngine")
        if shading_groups:
            shading_group = shading_groups[0]
        else:
            shading_group = pm.sets(renderable=True, noSurfaceShader=True, empty=True)
            pm.connectAttr(
                f"{mat}.outColor", f"{shading_group}.surfaceShader", force=True
            )

        for obj in pm.ls(objects):
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

        #  The following is a version that attempts to build a more accurate material swatch icon, but is crashing Maya
        # """Generates a QIcon representing the color swatch of a given Maya material node.

        # Parameters:
        #     mat (str or PyNode): The name or PyNode object of the Maya material. Must be of type 'shadingDependNode'.
        #     size (list of int): A list [width, height] specifying the dimensions of the QIcon in pixels. Default is [20, 20].

        # Returns:
        #     QIcon: A QIcon object filled with the color and transparency attributes of the specified material.
        #            Returns None if the material is invalid or an exception occurs.
        # Example:
        #     icon = get_mat_swatch_icon('lambert1', [20, 20])
        # """
        # from PySide2.QtGui import QPixmap, QPainter, QColor, QIcon

        # try:
        #     # Check if the given 'mat' is a valid Maya material node
        #     if not pm.objectType(mat, isAType="shadingDependNode"):
        #         print(
        #             f"Not a valid material. Expected 'shadingDependNode', got '{mat}' of type '{type(mat).__name__}'."
        #         )
        #         return None

        #     # Initialize QPixmap
        #     pixmap = QPixmap(size[0], size[1])
        #     pixmap.fill(QColor(0, 0, 0, 0))  # Transparent Background

        #     # Initialize QPainter
        #     painter = QPainter(pixmap)

        #     # Fetch material attributes
        #     colorR = pm.getAttr(f"{mat}.colorR") * 255
        #     colorG = pm.getAttr(f"{mat}.colorG") * 255
        #     colorB = pm.getAttr(f"{mat}.colorB") * 255
        #     transparency = pm.getAttr(
        #         f"{mat}.transparencyR"
        #     )  # Assuming R, G, B are the same for simplicity

        #     # Set QColor based on material attributes
        #     brushColor = QColor(colorR, colorG, colorB)
        #     brushColor.setAlpha(255 * (1 - transparency))

        #     # Draw swatch
        #     painter.setBrush(brushColor)
        #     painter.drawRect(0, 0, size[0], size[1])
        #     painter.end()

        #     return QIcon(pixmap)

        # except Exception as e:
        #     print(f"Exception: {e}")
        #     return None


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
