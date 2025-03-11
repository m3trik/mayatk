# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.node_utils import NodeUtils
from mayatk.rig_utils import RigUtils


class TubePath:
    """Handles tube-like path extraction for joint chain generation."""

    @staticmethod
    def get_centerline_using_edges(
        edge_selection: List[str],
    ) -> List[pm.datatypes.Point]:
        """Extracts the centerline points from selected edges of the tube."""
        centerline_points = []

        for edge in edge_selection:
            # Convert edge to vertices
            vertices = pm.polyListComponentConversion(
                edge, fromEdge=True, toVertex=True
            )
            vertices = pm.ls(vertices, flatten=True)

            # Get the positions of the vertices along the edge as dt.Point objects
            point1 = pm.pointPosition(vertices[0], world=True)  # dt.Point
            point2 = pm.pointPosition(vertices[1], world=True)  # dt.Point

            # Append dt.Point objects directly to the list
            centerline_points.append(point1)
            centerline_points.append(point2)

        # Sort the centerline points to form a continuous path
        centerline_points = ptk.arrange_points_as_path(centerline_points)

        return centerline_points

    def get_centerline_from_bounding_box(
        obj, precision=10, return_curve=False, smooth=False, window_size=1
    ):
        """Calculate the centerline of an object using the cross-section of its largest bounding box axis.

        Parameters:
            obj (str/obj/list): The object to calculate the centerline for.
            precision (int): The percentage of the largest axis length to determine the number of cross-sections.
            return_curve (bool): Whether to return the centerline curve along with the points.
            smooth (bool): Whether to apply smoothing to the centerline points.
            window_size (int): The size of the moving window for smoothing.

        Returns:
            (tuple/curve): Centerline points, or (optionally) the centerline curve.
        """
        obj = NodeUtils.get_transform_node(obj)
        if not obj:
            raise ValueError(f"Invalid object: `{obj}` {type(obj)}")

        # Calculate the bounding box of the object
        bbox = pm.exactWorldBoundingBox(obj)
        min_point = pm.datatypes.Point(bbox[0], bbox[1], bbox[2])
        max_point = pm.datatypes.Point(bbox[3], bbox[4], bbox[5])

        # Determine the largest axis of the bounding box
        bbox_size = max_point - min_point
        largest_axis = max(range(3), key=lambda i: bbox_size[i])

        # Calculate the number of slices based on the precision
        slice_count = max(1, int(bbox_size[largest_axis] * (precision / 100)))

        # Generate cross-sections along the largest axis
        centerline_points = []
        step = bbox_size[largest_axis] / slice_count
        for i in range(slice_count + 1):
            slice_pos = min_point[largest_axis] + i * step

            # Find vertices within the slice
            vertices = pm.ls(
                pm.polyListComponentConversion(obj, toVertex=True), flatten=True
            )
            slice_vertices = [
                vtx
                for vtx in vertices
                if abs(pm.pointPosition(vtx)[largest_axis] - slice_pos) < step / 2
            ]

            if not slice_vertices:
                continue

            # Calculate the center point of the slice
            center_point = sum(
                (pm.pointPosition(vtx) for vtx in slice_vertices), pm.datatypes.Point()
            ) / len(slice_vertices)
            centerline_points.append(center_point)

        # Apply smoothing if requested
        if smooth and centerline_points:
            centerline_points = ptk.smooth_points(centerline_points, window_size)

        # Create a curve from the centerline points if requested
        centerline_curve = None
        if return_curve and centerline_points:
            centerline_curve = pm.curve(p=centerline_points, d=3)
            return centerline_curve

        return centerline_points


class TubeRig(ptk.LoggingMixin):
    """Handles rigging the tube, creating joints, IK handles, and additional controls.

    Parameters:
        obj (str/obj): The polygon tube mesh to rig.
        rig_name (str): The name of the rig.
        rig_group (str): The group node for the rig.

    Attributes:
        rig_name (str): The name of the rig.
        rig_group (pm.nodetypes.Transform): The group node for the rig.
        mesh (pm.nodetypes.Transform): The tube mesh to bind the joints to.
        joints (List[pm.nodetypes.Joint]): The joint chain for the tube.
        ik_handle (pm.nodetypes.Transform): The IK handle for the joint chain.
        pole_vector (pm.nodetypes.Transform): The pole vector control for the IK handle.
        skin_cluster (pm.nodetypes.DependNode): The skinCluster node for the tube mesh.
        start_loc (pm.nodetypes.Transform): The start locator for the tube rig.
        end_loc (pm.nodetypes.Transform): The end locator for the tube rig.

    Example:
        mesh = pm.selected()
        tube_rig = TubeRig(mesh)
        joints = tube_rig.generate_joint_chain(centerline, num_joints=10)
        tube_rig.create_ik(joints)
        tube_rig.create_pole_vector(joints, mid_joint=joints[5])
        tube_rig.bind_joint_chain(tube, joints)

        # Later, the rig and it's attributes can be accessed using the mesh
        mesh.rig.joints
        mesh.rig.ik_handle
        mesh.rig.pole_vector
        mesh.rig.skin_cluster
        mesh.rig.start_loc
        mesh.rig.end_loc
    """

    def __init__(self, obj, rig_name: str = None, rig_group: str = None):
        self._rig_name = rig_name
        self._rig_group = rig_group

        # Ensure the object is a valid transform node
        obj = NodeUtils.get_transform_node(obj)
        if not obj:
            raise ValueError(f"Invalid object: `{obj}` {type (obj)}")
        elif isinstance(obj, (set, list, tuple)):
            obj = obj[0]

        self.mesh = obj
        self.mesh.rig = self  # Allow access to the rig instance via mesh attribute

        self.joints = None
        self.ik_handle = None
        self.pole_vector = None
        self.skin_cluster = None
        self.start_loc = None
        self.end_loc = None

    @property
    def rig_name(self) -> str:
        """Returns the rig name."""
        if not self._rig_name:
            self._rig_name = CoreUtils.generate_unique_name("tube_rig_0")
        return self._rig_name

    @rig_name.setter
    def rig_name(self, new_name: str):
        """Sets the rig name."""
        self._rig_name = new_name
        self.logger.debug(f"Rig name set to: {self._rig_name}")

    @property
    def rig_group(self) -> "pm.nodetypes.Transform":
        """Returns the rig group."""
        if not self._rig_group:
            rig_name = f"{self.rig_name}_GRP"
            if pm.objExists(rig_name):
                self._rig_group = pm.PyNode(rig_name)
            else:
                self._rig_group = pm.group(empty=True, name=rig_name)
            self.logger.debug(f"Created/Found rig group: {self._rig_group.name()}")
        return NodeUtils.get_transform_node(self._rig_group)

    @rig_group.setter
    def rig_group(self, new_group: "pm.nodetypes.Transform"):
        """Allows setting a custom rig group."""
        if isinstance(new_group, pm.nodetypes.Transform):
            self._rig_group = new_group
            self.logger.debug(f"Rig group set to: {self._rig_group.name()}")
        else:
            self.logger.error("Provided rig group is not a valid transform node.")

    @CoreUtils.undo
    def generate_joint_chain(
        self,
        centerline: List[List[float]],
        num_joints: int,
        reverse: bool = False,
        **kwargs,
    ) -> List["pm.nodetypes.Joint"]:
        """
        Generates joints along the tube's centerline.

        Parameters:
            centerline (List[List[float]]): List of centerline points to generate joints along.
            num_joints (int): Number of joints to generate.
            reverse (bool): Reverse the order of joints.
            **kwargs: Additional keyword arguments to pass to pm.joint.

        Returns:
            List[pm.nodetypes.Joint]: The generated joint chain.
        """
        radius: float = kwargs.pop("radius", 1.0)
        orientation: List[float] = kwargs.pop("orientation", [0, 0, 0])

        joint_positions = ptk.dist_points_along_centerline(
            centerline, num_joints, reverse
        )
        joints = []
        parent_joint = None

        for i, pos in enumerate(joint_positions):
            self.logger.debug(
                f"Generating joint {i+1}, position: {pos}, radius: {radius}, orientation: {orientation}"
            )
            jnt = pm.joint(
                p=pos,
                n=f"{self.rig_name}_jnt_{i+1}",
                radius=radius,
                orientation=orientation,
                **kwargs,
            )
            if i == 0:
                jnt.setParent(self.rig_group)
            else:
                jnt.setParent(parent_joint)
            parent_joint = jnt
            joints.append(jnt)

        self.logger.debug(f"Generated joints: {[jnt.name() for jnt in joints]}")
        self.joints = joints
        return joints

    @CoreUtils.undo
    def create_start_end_locators(
        self,
        joints: List["pm.nodetypes.Joint"],
        ik_handle: Optional["pm.nodetypes.Transform"] = None,
    ) -> Tuple["pm.nodetypes.Transform", "pm.nodetypes.Transform"]:
        """Creates start and end locators, correctly constraining them based on rig hierarchy.

        Parameters:
            joints (str/list): The joint chain to create locators for.
            ik_handle (pm.nodetypes.Transform): The IK handle to follow the end locator.

        Returns:
            Tuple[pm.nodetypes.Transform, pm.nodetypes.Transform]: The start and end locators.
        """
        joints = pm.ls(joints, type="joint", flatten=True)
        if len(joints) < 2:
            self.logger.error("Not enough joints to create locators.")
            return None, None

        start_locator = pm.spaceLocator(name=f"{self.rig_name}_start_LOC")
        end_locator = pm.spaceLocator(name=f"{self.rig_name}_end_LOC")

        # Get world positions of the first and last joint
        start_position = joints[0].getTranslation(space="world")
        end_position = joints[-1].getTranslation(space="world")

        # Set locator positions
        start_locator.setTranslation(start_position, space="world")
        end_locator.setTranslation(end_position, space="world")

        # Reset locator transformations
        pm.makeIdentity(start_locator, apply=True, t=1, r=1, s=1, n=0)
        pm.makeIdentity(end_locator, apply=True, t=1, r=1, s=1, n=0)

        # Parent to the rig group
        start_locator.setParent(self.rig_group)
        end_locator.setParent(self.rig_group)

        # Constrain the first joint to the start locator
        pm.pointConstraint(start_locator, joints[0], maintainOffset=False)

        # Constrain the end locator
        if ik_handle:  # Ik handle follows end locator
            pm.pointConstraint(end_locator, ik_handle, maintainOffset=True)
        else:  # End locator follows last joint
            pm.pointConstraint(joints[-1], end_locator, maintainOffset=True)

        # Lock unnecessary attributes on locators
        RigUtils.set_attr_lock_state(
            (start_locator, end_locator), rotate=True, scale=True
        )

        self.logger.debug(
            f"Created start and end locators: {start_locator.name()}, {end_locator.name()}"
        )
        self.start_loc = start_locator
        self.end_loc = end_locator
        return start_locator, end_locator

    @CoreUtils.undo
    def create_ik(
        self, joints: List["pm.nodetypes.Joint"], **kwargs
    ) -> Optional["pm.nodetypes.Transform"]:
        """Creates an IK handle for the given list of joints with additional options from kwargs.

        Parameters:
            joints (List[pm.nodetypes.Joint]): List of joints to create IK handle for.
            **kwargs: Additional keyword arguments to pass to pm.ikHandle.
                note: startJoint and endEffector can be overridden using kwargs.
                solver (str): The IK solver to use. ikRPsolver, ikSCsolver, ikSplineSolver
        Returns:
            pm.nodetypes.Transform: The created IK handle.
        """
        joints = pm.ls(joints, type="joint", flatten=True)
        if len(joints) < 2:
            self.logger.error(
                f"Insufficient joints to create IK handle. Required: 2, Provided: {len(joints)}"
            )
            return None

        start_joint = joints[0]
        end_joint = joints[-1]

        self.logger.debug(
            f"Creating IK handle for joints: {[jnt.name() for jnt in joints]}"
        )

        # Allow overriding start and end joints via kwargs
        start_joint = kwargs.pop("startJoint", start_joint)
        end_joint = kwargs.pop("endEffector", end_joint)
        name = kwargs.pop("name", f"{self.rig_name}_ikHandle")

        try:
            ik_handle = pm.ikHandle(
                startJoint=start_joint, endEffector=end_joint, name=name, **kwargs
            )
            ik_handle[0].setParent(self.rig_group)
            self.logger.debug(f"IK handle created: {ik_handle[0].name()}")
            self.ik_handle = ik_handle[0]
            return ik_handle[0]
        except Exception as e:
            self.logger.error(f"Error creating IK handle: {str(e)}")
            return None

    @CoreUtils.undo
    def create_pole_vector(
        self, ik_handle, mid_joint: "pm.nodetypes.Joint", offset=(0, 5, 0)
    ) -> "pm.nodetypes.Transform":
        """Creates a pole vector control using the mid joint position.

        Parameters:
            ik_handle (pm.nodetypes.Transform): The IK handle to attach the pole vector to.
            mid_joint (pm.nodetypes.Joint): The middle joint to calculate the pole vector position.
            offset (Tuple[int, int, int]): The offset to apply to the pole vector position.

        Returns:
            pm.nodetypes.Transform: The created pole vector
        """
        mid_pos = mid_joint.getTranslation(space="world")
        pole_vector = pm.spaceLocator(name=f"{self.rig_name}_poleVector_LOC")
        pole_vector.setTranslation(
            mid_pos + pm.datatypes.Vector(*offset), space="world"
        )
        pm.makeIdentity(pole_vector, apply=True, t=1, r=1, s=1, n=0)
        pole_vector.setParent(self.rig_group)

        # Create a pole vector constraint
        pm.poleVectorConstraint(pole_vector, ik_handle)

        # Lock unnecessary attributes on pole vector
        RigUtils.set_attr_lock_state(pole_vector, rotate=True, scale=True)

        self.logger.debug(f"Created pole vector: {pole_vector.name()}")
        self.pole_vector = pole_vector
        return pole_vector

    @CoreUtils.undo
    def bind_joint_chain(
        self, obj, joints: List["pm.nodetypes.Joint"]
    ) -> Optional["pm.nodetypes.DependNode"]:
        """Binds the joint chain to a polygon tube with smooth skinning.

        Parameters:
            obj (pm.nodetypes.Transform): The tube mesh to bind the joints to.
            joints (List[pm.nodetypes.Joint]): The joint chain to bind to the tube.

        Returns:
            pm.nodetypes.DependNode: The created skinCluster
        """
        self.logger.debug(f"Tube mesh type: {type(obj)}")
        self.logger.debug(f"Tube mesh: {obj}")
        obj = list(set(pm.ls(obj, objectsOnly=True, flatten=True)))
        try:
            obj = obj[0]
        except IndexError:
            self.logger.error(f"Invalid tube mesh: {obj}")
            return None

        transform = NodeUtils.get_transform_node(obj)
        if not transform:
            self.logger.error(f"Invalid transform node: {transform}")
            return None

        if not isinstance(joints, list) or not all(
            isinstance(j, pm.nodetypes.Joint) for j in joints
        ):
            self.logger.error(f"Invalid joint list: {joints}")
            return None

        self.logger.debug(f"Joints to bind: {[jnt.name() for jnt in joints]}")

        if not pm.objExists(obj):
            self.logger.error(f"Tube mesh {obj.name()} does not exist in the scene.")
            return None

        if not joints:
            self.logger.error("No joints to bind to the tube.")
            return None

        self.logger.debug(
            f"Creating skinCluster with joints: {[jnt.name() for jnt in joints]}, and tube: {obj.name()}"
        )

        try:
            skin_cluster = pm.skinCluster(
                joints,
                obj,
                toSelectedBones=True,
                maximumInfluences=4,
                weightDistribution=0.5,
            )
            self.logger.debug(f"SkinCluster created: {skin_cluster}")
        except Exception as e:
            self.logger.error(f"Error creating skinCluster: {str(e)}")
            return None

        if skin_cluster:
            self.logger.debug(
                f"SkinCluster successfully created and bound: {skin_cluster}"
            )
        self.logger.debug(f"SkinCluster attributes: {skin_cluster.listAttr()}")
        self.skin_cluster = skin_cluster
        return skin_cluster


class TubeRigSlots:
    def __init__(self, **kwargs):
        # Initialize the switchboard and UI here
        self.sb = kwargs.get("switchboard")
        self.ui = self.sb.loaded_ui.tube_rig

    def get_tube_rig(self, obj):
        """Get the tube rig instance for the given object."""
        if not hasattr(obj, "rig"):  # Instantiate the tube rig
            rig_name = self.ui.txt000.text() or f"{obj.name()}_RIG"
            tube_rig = TubeRig(obj, rig_name=rig_name)
            return tube_rig
        return obj.rig

    def create_joints_from_tube(self, obj):
        """Creates a joint chain from a tube mesh."""
        # if there is an edge selection use get_centerline_using_edges
        edges = pm.filterExpand(selectionMask=32)  # Ensure selection contains edges
        if edges:
            centerline_points = TubePath.get_centerline_using_edges(edges)
        else:  # If no edge selection use get_centerline_from_bounding_box
            centerline_points = TubePath.get_centerline_from_bounding_box(
                obj, smooth=True, precision=self.ui.s001.value()
            )

        # Get the tube rig instance
        tube_rig = self.get_tube_rig(obj)

        # Generate the joint chain
        joints = tube_rig.generate_joint_chain(
            centerline=centerline_points,
            num_joints=self.ui.s000.value(),
            radius=self.ui.s002.value(),
            orientation=[0, 0, 0],
            reverse=self.ui.chk000.isChecked(),
        )
        return joints

    def create_rig_from_joints(self, obj, joints):
        """Creates a tube rig from an existing joint chain."""
        # Order the joints by hierarchy using pymel
        joints = pm.ls(joints, type="joint", flatten=True)
        if len(joints) < 2:  # Get the entire chain using root joint
            joints = RigUtils.get_joint_chain_from_root(joints[0])

        # Reverse the joint chain if requested
        if self.ui.chk000.isChecked():
            joints = RigUtils.invert_joint_chain(joints[0], keep_original=False)

        # Get the tube rig instance
        tube_rig = self.get_tube_rig(obj)

        # Create IK handle
        ik_handle = tube_rig.create_ik(joints, solver="ikRPsolver")

        # Create pole vector control with mid joint offset
        mid_joint_index = int(len(joints) / 2)
        mid_joint = joints[mid_joint_index]
        tube_rig.create_pole_vector(ik_handle, mid_joint=mid_joint)

        # Bind joint chain to the tube mesh
        tube_rig.bind_joint_chain(obj, joints)

        # Create start and end locators
        tube_rig.create_start_end_locators(joints, ik_handle=ik_handle)
        return tube_rig

    @CoreUtils.undo
    def b000(self):
        """Create Tube Rig."""
        try:
            obj, *_ = pm.selected(objectsOnly=True, flatten=True)
        except ValueError:
            self.sb.message_box("Select a single polygon tube mesh to create a rig.")
            return

        joints = self.create_joints_from_tube(obj)
        tube_rig = self.create_rig_from_joints(obj, joints)

        self.sb.message_box(f"Tube rig created: {tube_rig.rig_name}")

    @CoreUtils.undo
    def b001(self):
        """Create Joints from Tube."""
        try:
            obj, *_ = pm.selected(objectsOnly=True, flatten=True)
        except ValueError:
            self.sb.message_box("Select a single polygon tube mesh to create a rig.")
            return

        joints = self.create_joints_from_tube(obj)
        self.sb.message_box(f"Joints created: {len(joints)}")

    @CoreUtils.undo
    def b002(self):
        """Macros: Create Rig from Joints."""
        try:
            *joints, obj = pm.selected(flatten=True)
        except ValueError:
            self.sb.message_box(
                "Select the root joint and then a tube mesh to create a rig."
            )
            return

        tube_rig = self.create_rig_from_joints(obj, joints)
        self.sb.message_box(f"Tube rig created: {tube_rig.rig_name}")


class TubeRigUi:
    def __new__(self):
        """Get the Rig Tube UI."""
        import os
        from mayatk.ui_utils.ui_manager import UiManager

        ui_file = os.path.join(os.path.dirname(__file__), "tube_rig.ui")
        ui = UiManager.get_ui(ui_source=ui_file, slot_source=TubeRigSlots)
        return ui


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    TubeRigUi().show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
