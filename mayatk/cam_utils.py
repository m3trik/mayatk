# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

# from this package:
from mayatk import misc_utils


class Cam(object):
    """ """

    @staticmethod
    @misc_utils.Misc.undo
    def group_cameras(
        name="cameras", non_default=True, root_only=False, hide_group=False
    ):
        """Group cameras in the scene based on the provided parameters.

        Parameters:
                name (str): The name of the group that will contain the cameras. Default is 'cameras'.
                non_default (bool, optional): If True, only non-default cameras will be grouped. Default is True.
                root_only (bool, optional): If True, only root-level cameras (not parented to other objects) will be grouped. Default is False.
                hide_group (bool, optional): If True, the newly created group will be hidden. Default is False.

        Returns:
                PyNode: The created group node containing the cameras.
        """
        if pm.objExists(name):  # Check if the group already exists
            pm.error(f"Group '{name}' already exists.")
            return

        # Create the group and set it's visibility
        group = pm.group(empty=True, name=name)
        group.visibility.set(not hide_group)

        default_cameras = (
            "side",
            "front",
            "top",
            "persp",
            "back",
            "bottom",
            "left",
            "alignToPoly",
        )  # List of default cameras
        all_cameras = pm.ls(type="camera")  # Get all cameras in the scene
        all_camera_transforms = [
            cam.get_parent() for cam in all_cameras
        ]  # Get the parent transform nodes of the cameras

        if root_only:  # Filter cameras based on the root_only flag
            all_camera_transforms = [
                cam for cam in all_camera_transforms if cam.get_parent() is None
            ]

        # Parent cameras to the group based on the non_default flag
        for cam in all_camera_transforms:
            if non_default:
                if not any(
                    [cam.name().endswith(def_cam) for def_cam in default_cameras]
                ):
                    pm.parent(cam, group)
            else:
                pm.parent(cam, group)

        return group

    @classmethod
    def toggle_safe_frames(cls):
        """Toggle display of the film gate for the current camera."""
        camera = cls.get_current_cam()

        state = pm.camera(camera, query=1, displayResolution=1)
        if state:
            pm.camera(
                camera,
                edit=1,
                displayFilmGate=False,
                displayResolution=False,
                overscan=1.0,
            )
        else:
            pm.camera(
                camera,
                edit=1,
                displayFilmGate=False,
                displayResolution=True,
                overscan=1.3,
            )

    @staticmethod
    def get_current_cam():
        """Get the currently active camera."""
        from maya.OpenMaya import MDagPath
        from maya.OpenMayaUI import M3dView

        view = M3dView.active3dView()
        cam = MDagPath()
        view.getCamera(cam)
        camPath = cam.fullPathName()
        return camPath

    @staticmethod
    @misc_utils.Misc.undo
    def create_camera_from_view(name="camera#"):
        """Create a new camera based on the current view."""
        # Find the current modelPanel (viewport)
        current_panel = None
        for panel in misc_utils.get_panel(all=True):
            if misc_utils.get_panel(typeOf=panel) == "modelPanel":
                current_panel = panel
                break

        if current_panel:
            if misc_utils.get_panel(typeOf=current_panel) == "modelPanel":
                camera = pm.modelPanel(current_panel, q=1, cam=1)
                new_camera = pm.duplicate(camera)[0]
                pm.showHidden(new_camera)
                new_camera = pm.rename(new_camera, name)
                print(f"# Result: {new_camera} #")
                return new_camera
        else:
            print("No modelPanel found")


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------

# @classmethod
#   def matchTransformByVertexOrder(cls, source, target):
#       '''Match transform and rotation on like objects by using 3 vertices from each object.
#       The vertex order is transferred to the target object(s).

#       Parameters:
#           source (str/obj): The object to move from.
#           target (str/obj): The object to move to.
#       '''
#       pm.polyTransfer(source, alternateObject=target, vertices=2) #vertices positions are copied from the target object.

#       source_verts = [pm.ls(source, objectsOnly=1)[0].verts[i] for i in range(3)]
#       target_verts = [pm.ls(target, objectsOnly=1)[0].verts[i] for i in range(3)]

#       cls.align_using_three_points(source_verts+target_verts)

# @staticmethod
#   def getComponentPoint(component, alignToNormal=False):
#       '''Get the center point from the given component.

#       Parameters:
#           component (str/obj): Object component.
#           alignToNormal (bool): Constain to normal vector.

#       Returns:
#           (tuple) coordinate as xyz float values.
#       '''
#       if ".vtx" in str(component):
#           x = pm.polyNormalPerVertex(component, query=1, x=1)
#           y = pm.polyNormalPerVertex(component, query=1, y=1)
#           z = pm.polyNormalPerVertex(component, query=1, z=1)
#           xyz = [sum(x) / float(len(x)), sum(y) / float(len(y)), sum(z) / float(len(z))] #get average

#       elif ".e" in str(component):
#           componentName = str(component).split(".")[0]
#           vertices = pm.polyInfo (component, edgeToVertex=1)[0]
#           vertices = vertices.split()
#           vertices = [componentName+".vtx["+vertices[2]+"]",componentName+".vtx["+vertices[3]+"]"]
#           x=[];y=[];z=[]
#           for vertex in vertices:
#               x_ = pm.polyNormalPerVertex (vertex, query=1, x=1)
#               x.append(sum(x_) / float(len(x_)))
#               y_ = pm.polyNormalPerVertex (vertex, query=1, y=1)
#               x.append(sum(y_) / float(len(y_)))
#               z_ = pm.polyNormalPerVertex (vertex, query=1, z=1)
#               x.append(sum(z_) / float(len(z_)))
#           xyz = [sum(x) / float(len(x)), sum(y) / float(len(y)), sum(z) / float(len(z))] #get average

#       else:# elif ".f" in str(component):
#           xyz = pm.polyInfo (component, faceNormals=1)
#           xyz = xyz[0].split()
#           xyz = [float(xyz[2]), float(xyz[3]), float(xyz[4])]

#       if alignToNormal: #normal constraint
#           normal = pm.mel.eval("unit <<"+str(xyz[0])+", "+str(xyz[1])+", "+str(xyz[2])+">>;") #normalize value using MEL
#           # normal = [round(i-min(xyz)/(max(xyz)-min(xyz)),6) for i in xyz] #normalize and round value using python

#           constraint = pm.normalConstraint(component, object_,aimVector=normal,upVector=[0,1,0],worldUpVector=[0,1,0],worldUpType="vector") # "scene","object","objectrotation","vector","none"
#           pm.delete(constraint) #orient object_ then remove constraint.

#       vertexPoint = pm.xform (component, query=1, translation=1) #average vertex points on destination to get component center.
#       x = vertexPoint[0::3]
#       y = vertexPoint[1::3]
#       z = vertexPoint[2::3]

#       return tuple(round(sum(x) / float(len(x)),4), round(sum(y) / float(len(y)),4), round(sum(z) / float(len(z)),4))
