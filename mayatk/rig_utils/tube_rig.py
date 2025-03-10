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
    """Handles rigging the tube, creating joints, IK handles, and additional controls."""

    def __init__(self, rig_name: str = None):
        self._rig_name = rig_name  # Store the rig name as an instance attribute
        self._rig_group = None  # Private attribute for rig group

    @property
    def rig_name(self) -> str:
        """Returns the rig name."""
        if self._rig_name is None:
            self._rig_name = CoreUtils.generate_unique_name("tube_rig_0")
        return self._rig_name

    @rig_name.setter
    def rig_name(self, new_name: str):
        """Sets the rig name."""
        self._rig_name = new_name
        self.logger.debug(f"Rig name set to: {self._rig_name}")

    @property
    def rig_group(self) -> "pm.nodetypes.Transform":
        """Returns or creates the rig group for this rig."""
        if self._rig_group is None:
            self._rig_group = pm.group(empty=True, name=f"{self.rig_name}")
            self.logger.debug(f"Created new rig group: {self._rig_group.name()}")
        return self._rig_group

    @rig_group.setter
    def rig_group(self, new_group: "pm.nodetypes.Transform"):
        """Allows setting a custom rig group."""
        if isinstance(new_group, pm.nodetypes.Transform):
            self._rig_group = new_group
            self.logger.debug(f"Rig group set to: {self._rig_group.name()}")
        else:
            self.logger.error("Provided rig group is not a valid transform node.")

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

        return joints

    def create_start_end_locators(
        self,
        joints: List["pm.nodetypes.Joint"],
        ik_handle: Optional["pm.nodetypes.Transform"] = None,
    ) -> Tuple["pm.nodetypes.Transform", "pm.nodetypes.Transform"]:
        """Creates start and end locators, correctly constraining them based on rig hierarchy."""

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

        return start_locator, end_locator

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
            return ik_handle[0]
        except Exception as e:
            self.logger.error(f"Error creating IK handle: {str(e)}")
            return None

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
        return pole_vector

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
        return skin_cluster


def main():
    """Main function to create a tube rig from selected edges."""

    # if there is an edge selection use get_centerline_using_edges
    edges = pm.filterExpand(selectionMask=32)  # Ensure selection contains edges
    if edges:
        obj = pm.ls(edges[0], objectsOnly=True)[0]
        centerline_points = TubePath.get_centerline_using_edges(edges)
    else:  # If no edge selection use get_centerline_from_bounding_box
        objects = pm.selected(flatten=True)
        if not objects:
            pm.warning("No objects selected. Please select a polygon tube.")
            return
        obj = objects[0]
        if len(objects) > 1:
            raise ValueError(f"Expected 1 object, got: {len(objects)}\n\t{objects}")

        centerline_points = TubePath.get_centerline_from_bounding_box(
            obj, smooth=True, precision=30
        )

    # Create an instance of TubeRig
    tube_rig = TubeRig(rig_name=f"{obj.name()}_RIG")

    # Generate joint chain
    joints = tube_rig.generate_joint_chain(
        centerline=centerline_points,
        num_joints=10,
        radius=1.0,
        orientation=[0, 0, 0],
        reverse=False,
    )

    # Create IK handle
    ik_handle = tube_rig.create_ik(joints, solver="ikRPsolver")

    # Create pole vector control
    mid_joint = joints[len(joints) // 2]
    pole_vector = tube_rig.create_pole_vector(ik_handle, mid_joint=mid_joint)

    # Bind joint chain to the tube mesh
    skin_cluster = tube_rig.bind_joint_chain(obj, joints)

    # Create start and end locators
    start_loc, end_loc = tube_rig.create_start_end_locators(joints, ik_handle=ik_handle)

    return tube_rig


if __name__ == "__main__":
    CoreUtils.clear_scrollfield_reporters()
    tube_rig = main()
