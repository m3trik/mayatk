# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import node_utils


class Mat(object):
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

        # Iterate over each object or component in the list
        for obj in pm.ls(objs, flatten=True):
            if isinstance(obj, pm.MeshFace):
                all_shading_grps = pm.ls(type="shadingEngine")
                shading_grps = [
                    sg for sg in all_shading_grps if pm.sets(sg, isMember=obj)
                ]
                if not shading_grps:  # Resort to the hacky method
                    # select the material node
                    pm.hyperShade(obj, shaderNetworksSelectMaterialNodes=1)
                    mats_ = pm.ls(pm.selected(), materials=1)
                    mats.update(mats_)
            else:
                shape = obj.getShape()
                shading_grps = pm.listConnections(shape, type="shadingEngine")

            for shading_grp in shading_grps:
                materials = pm.ls(
                    pm.listConnections(shading_grp + ".surfaceShader"), materials=True
                )
                for material in materials:
                    mats.add(material)

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
        filtered = ptk.Iter.filter_dict(d, inc, exc, keys=True, values=True)

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

        path = os.path.expandvars(
            r"%USERPROFILE%/Documents/maya/2022/prefs/renderNodeTypeFavorites"
        )
        renderNodeTypeFavorites = _fav.readFavorites(path)
        materials = [i for i in renderNodeTypeFavorites if "/" not in i]
        del _fav

        return materials

    @staticmethod
    def create_random_mat(name="", prefix=""):
        """Creates a random Lambert material with a random color.

        Parameters:
            name (str, optional): The name of the material. Defaults to "".
            prefix (str, optional): An optional prefix to append to the material name. Defaults to "".

        Returns:
            obj: The created material.
        """
        import random

        rgb = [
            random.randint(0, 255) for _ in range(3)
        ]  # generate a list containing 3 values between 0-255

        name = "{}{}_{}_{}_{}".format(
            prefix, name, str(rgb[0]), str(rgb[1]), str(rgb[2])
        )

        # create shader
        mat = pm.shadingNode("lambert", asShader=1, name=name)
        # convert RGB to 0-1 values and assign to shader
        convertedRGB = [round(float(v) / 255, 3) for v in rgb]
        pm.setAttr(name + ".color", convertedRGB)
        # assign to selected geometry
        # pm.select(selection) #initial selection is lost upon node creation
        # pm.hyperShade(assign=mat)

        return mat

    @staticmethod
    def assign_mat(objects, mat):
        """Assigns a material to a list of objects or components.

        Parameters:
            objects (list): The objects or components to assign the material to.
            mat (obj): The material to assign.
        """
        try:  # if the mat is a not a known type; try and create the material.
            pm.nodeType(mat)
        except Exception:
            mat = pm.shadingNode(mat, asShader=1)

        shading_group = pm.sets(renderable=True, noSurfaceShader=True, empty=True)
        pm.connectAttr(f"{mat}.outColor", f"{shading_group}.surfaceShader", force=True)

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
            objects = set(pm.ls(type="transform", objectsOnly=True, flatten=True))
        else:
            # Ensure the objects list only contains the names of the objects
            objects = set(pm.ls(objects, objectsOnly=True, flatten=True))

        # Find the shading groups associated with the material
        shading_groups = pm.listConnections(material, type="shadingEngine")

        objs_with_material = []
        for sg in shading_groups:
            connected_objs = pm.sets(sg, query=True, noIntermediate=True)
            flattened = pm.ls(connected_objs, flatten=True)
            # Only add objects to the list if they are in the specified objects list
            for obj in flattened:
                transform_node = node_utils.Node.get_transform_node(obj)
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


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------
