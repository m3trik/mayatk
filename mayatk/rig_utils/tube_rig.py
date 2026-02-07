#!/usr/bin/env python
# coding=utf-8
from typing import List, Tuple, Optional, Union
from abc import ABC, abstractmethod
from dataclasses import dataclass

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.rig_utils._rig_utils import RigUtils
from mayatk.rig_utils.controls import Controls
from mayatk.edit_utils.naming import Naming


class TubePath:
    """Pure geometry analysis for tube-like meshes.

    Extracts centerline paths from polygon tube meshes using different
    algorithms. All methods are static and produce only point data —
    no Maya scene objects (curves, joints, etc.) are created.

    Use ``get_centerline`` as the main entry point, which selects the
    best algorithm based on the ``num_joints`` hint.
    """

    @staticmethod
    def get_centerline(
        mesh,
        num_joints: int = 10,
        precision: int = 10,
        edges: list = None,
        use_surface_normals: bool = True,
    ) -> Tuple[List, int]:
        """Unified centerline dispatcher — picks the best algorithm.

        Parameters:
            mesh: The tube mesh object.
            num_joints: Requested joint count. ``-1`` = auto (uses edge loops).
            precision: Bounding-box precision (only used when num_joints > 0).
            edges: Optional pre-selected edges to derive centerline from.
            use_surface_normals: When True (default), uses the surface-normal
                opposing-hit method instead of axis-aligned bounding-box slicing.
                More accurate for curved or diagonal tubes.

        Returns:
            Tuple of (centerline_points, resolved_num_joints).
            When ``num_joints == -1`` the resolved count equals the number of
            edge-loop cross-sections found.
        """
        if edges:
            pts = TubePath.get_centerline_using_edges(edges)
            return pts, (len(pts) if num_joints == -1 else num_joints)

        if num_joints == -1:
            pts, loop_count = TubePath.get_edge_loop_centers(mesh)
            if pts:
                return pts, loop_count
            # Fallback when edge-loop detection fails
            fallback_count = 10
            if use_surface_normals:
                pts = TubePath.get_centerline_from_surface_normals(
                    mesh, num_points=fallback_count
                )
            else:
                pts = TubePath.get_centerline_from_bounding_box(
                    mesh, precision=precision, smooth=True
                )
            return pts, fallback_count

        if use_surface_normals:
            pts = TubePath.get_centerline_from_surface_normals(
                mesh, num_points=num_joints
            )
        else:
            pts = TubePath.get_centerline_from_bounding_box(
                mesh, precision=precision, smooth=True
            )
        return pts, num_joints

    # ------------------------------------------------------------------
    # Algorithm: Edge-loop centres (topology-accurate)
    # ------------------------------------------------------------------

    @staticmethod
    def get_edge_loop_centers(mesh) -> Tuple[List[pm.datatypes.Point], int]:
        """Extract centerline by finding all edge loops (cross-sections) of a tube mesh.

        This provides a more accurate centerline than bounding box approximation,
        and the number of edge loops determines the natural joint count.

        Parameters:
            mesh: The tube mesh object.

        Returns:
            Tuple of (centerline_points, num_loops) where:
                - centerline_points: List of center points for each edge loop
                - num_loops: Number of edge loops found (natural joint count)
        """
        mesh = NodeUtils.get_transform_node(mesh)
        if not mesh:
            return [], 0

        # Get all edges of the mesh
        all_edges = pm.ls(
            pm.polyListComponentConversion(mesh, toEdge=True), flatten=True
        )
        if not all_edges:
            return [], 0

        # Start with first edge, get its edge loop (one circular cross-section)
        first_loop = pm.polySelect(mesh, q=True, edgeLoop=all_edges[0].index())
        if not first_loop:
            return [], 0

        # Get first edge from the loop
        first_loop_edge = pm.PyNode(f"{mesh.name()}.e[{first_loop[0]}]")

        # Get the edge ring from this edge (all parallel edges along tube length)
        # This gives us one edge from each cross-section
        ring_edges = pm.polySelect(mesh, q=True, edgeRing=first_loop_edge.index())
        if not ring_edges:
            return [], 0

        # For each edge in the ring, get its edge loop and compute center
        visited_loops = set()
        loop_centers = []

        for edge_idx in ring_edges:
            # Get the full edge loop for this edge
            loop_edges = pm.polySelect(mesh, q=True, edgeLoop=edge_idx)
            if not loop_edges:
                continue

            # Create a hashable identifier for this loop
            loop_key = tuple(sorted(loop_edges))
            if loop_key in visited_loops:
                continue
            visited_loops.add(loop_key)

            # Get all vertices in this loop
            # Use set of vertex string names (hashable) instead of PyNode objects
            loop_vert_names = set()
            for e_idx in loop_edges:
                edge = pm.PyNode(f"{mesh.name()}.e[{e_idx}]")
                verts = pm.polyListComponentConversion(
                    edge, fromEdge=True, toVertex=True
                )
                for v in pm.ls(verts, flatten=True):
                    # Store vertex name string (hashable) to avoid MeshVertex type error
                    loop_vert_names.add(str(v))

            # Calculate center of this loop
            if loop_vert_names:
                # Reconstruct PyNode objects from names for position queries
                loop_verts = [pm.PyNode(v_name) for v_name in loop_vert_names]
                center = sum(
                    (pm.pointPosition(v, world=True) for v in loop_verts),
                    pm.datatypes.Point(),
                ) / len(loop_verts)
                loop_centers.append(center)

        # Sort centers to form a continuous path along the tube
        if loop_centers:
            # 1. Arrange points
            loop_centers = ptk.arrange_points_as_path(loop_centers)

            # 2. Filter duplicates/near-coincident points
            # Edge loops on high-res geometry or bevels can result in points that are virtually identical.
            # This causes joint orientation failures (zero-length bone vectors).
            filtered_centers = [loop_centers[0]]
            min_dist_sq = 0.001 * 0.001
            for i in range(1, len(loop_centers)):
                prev = pm.datatypes.Point(filtered_centers[-1])
                curr = pm.datatypes.Point(loop_centers[i])
                if prev.distanceTo(curr) > 0.001:
                    filtered_centers.append(curr)

            loop_centers = filtered_centers

        return loop_centers, len(loop_centers)

    # ------------------------------------------------------------------
    # Algorithm: User-selected edges (manual override)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Algorithm: Surface-normal opposing-hit averaging
    # ------------------------------------------------------------------

    @staticmethod
    def get_centerline_from_surface_normals(
        mesh,
        num_points: int = 10,
        iterations: int = 3,
    ) -> List[pm.datatypes.Point]:
        """Calculate centerline by iteratively averaging opposing surface hits.

        For each sample along the tube this method:

        1. Queries ``closestPointOnMesh`` from an interior estimate.
        2. Uses the direction to the nearest surface to infer the radial axis.
        3. Queries again from the opposite side so both tube walls are sampled.
        4. Averages the two surface points to obtain the true cross-section center.

        Multiple iterations converge the estimate even when the initial seed
        is off-center.  Unlike bounding-box slicing this works regardless of
        tube orientation or curvature.

        Parameters:
            mesh: The tube mesh object.
            num_points: Number of centerline samples to generate.
            iterations: Refinement passes (2–3 is usually sufficient).

        Returns:
            List of centerline points as ``pm.datatypes.Point``.
        """
        mesh = NodeUtils.get_transform_node(mesh)
        if not mesh:
            raise ValueError(f"Invalid object: `{mesh}` {type(mesh)}")

        bbox = pm.exactWorldBoundingBox(mesh)
        min_pt = pm.datatypes.Point(bbox[0], bbox[1], bbox[2])
        max_pt = pm.datatypes.Point(bbox[3], bbox[4], bbox[5])
        bbox_size = max_pt - min_pt
        largest_axis = max(range(3), key=lambda i: bbox_size[i])

        # Utility node for fast surface queries
        cpom = pm.createNode("closestPointOnMesh")
        mesh_shape = mesh.getShape()
        mesh_shape.outMesh >> cpom.inMesh
        mesh_shape.worldMatrix[0] >> cpom.inputMatrix

        try:
            # Seed: sample evenly along the largest bbox axis through bbox center
            bbox_center = (min_pt + max_pt) / 2
            step = bbox_size[largest_axis] / (num_points + 1)

            centers = []
            for i in range(1, num_points + 1):
                pt = pm.datatypes.Point(bbox_center)
                pt[largest_axis] = min_pt[largest_axis] + i * step
                centers.append(pt)

            # Iteratively refine via opposing-surface-hit averaging
            for _ in range(iterations):
                refined = []
                for center in centers:
                    cpom.inPosition.set(center)
                    surface_pt = pm.datatypes.Point(cpom.position.get())

                    # Direction from current estimate to nearest surface
                    to_surface = pm.datatypes.Vector(surface_pt - center)
                    radius_est = to_surface.length()
                    if radius_est < 1e-6:
                        refined.append(center)
                        continue

                    direction = to_surface.normal()

                    # Query from the opposite side — overshoot past the far wall
                    opposite_query = center - direction * (radius_est * 3)
                    cpom.inPosition.set(opposite_query)
                    surface_pt2 = pm.datatypes.Point(cpom.position.get())

                    # Midpoint of opposing surface hits ≈ true center
                    refined.append((surface_pt + surface_pt2) / 2)

                centers = refined

            # Order as a continuous path
            centers = ptk.arrange_points_as_path(centers)
            return centers

        finally:
            pm.delete(cpom)

    # ------------------------------------------------------------------
    # Algorithm: Bounding-box slicing (approximate, works on any mesh)
    # ------------------------------------------------------------------

    @staticmethod
    def get_centerline_from_bounding_box(
        obj, precision=10, smooth=False, window_size=1
    ):
        """Calculate the centerline of an object using the cross-section of its largest bounding box axis.

        Parameters:
            obj (str/obj/list): The object to calculate the centerline for.
            precision (int): The percentage of the largest axis length to determine the number of cross-sections.
            smooth (bool): Whether to apply smoothing to the centerline points.
            window_size (int): The size of the moving window for smoothing.

        Returns:
            list: Centerline points as a list of ``pm.datatypes.Point``.
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

        return centerline_points


# ======================================================================
# Data Containers
# ======================================================================


@dataclass
class TubeRigBundle:
    rig_group: "pm.nodetypes.Transform"
    joints: List["pm.nodetypes.Joint"]
    ik_handle: Optional["pm.nodetypes.Transform"] = None
    curve: Optional["pm.nodetypes.Transform"] = None
    anchors: Optional[List["pm.nodetypes.Joint"]] = None
    controls: Optional[List["pm.nodetypes.Transform"]] = None


# ======================================================================
# Build Strategies (orchestrate TubeRig methods)
# ======================================================================


class TubeStrategy(ABC):
    @abstractmethod
    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        pass


class FKChainStrategy(TubeStrategy):
    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig.logger.info("Building FK Chain Rig...")
        num_joints = kwargs.get("num_joints", 10)
        radius = kwargs.get("radius", 1.0)

        centerline, num_joints = TubePath.get_centerline(rig.mesh, num_joints)

        # 1. Main Tube Skeleton
        joints = rig.generate_joint_chain(
            centerline, num_joints=num_joints, radius=radius
        )

        # Orient Joints (Standard FK Aim)
        if joints:
            rig.logger.debug("Orienting joints for FK (X-down)...")
            # Use RigUtils to ensure clean orientation
            pm.select(joints[0], hierarchy=True)
            pm.joint(e=True, oj="xyz", sao="yup", ch=True, zso=True)
            # Find the last joint and zero its orient (it has no child to aim at)
            joints[-1].jointOrient.set((0, 0, 0))

        # 2. Create FK Controls
        controls = []
        parent_ctrl = rig.rig_group

        # Create control hierarchy
        for i, jnt in enumerate(joints):
            # Skip end joint control usually, or keep it?
            # For a tail, you usually control up to the last segment.
            # But let's create controls for all to be safe/flexible.

            ctrl_name = f"{rig.rig_name}_{i+1}_CTRL"
            # Visual size tapers down?
            scale = radius * 3

            # Create control at joint position/orient
            nodes = Controls.circle(
                name=ctrl_name,
                normal=(1, 0, 0),  # Aim down X
                radius=scale,
                color=(1, 1, 0),
                return_nodes=True,
            )
            ctrl = nodes.control
            grp = nodes.group if nodes.group else ctrl

            # Match joint transform
            # We use parentConstraint for matching to ensure clean matrix transfer
            temp_const = pm.parentConstraint(jnt, grp)
            pm.delete(temp_const)

            # Parent
            grp.setParent(parent_ctrl)

            # Constrain Joint to Control
            # Use parent constraint? or matrix connection?
            # Standard FK: Joint follows control
            pm.parentConstraint(
                ctrl, jnt, mo=True
            )  # mo=True just in case of slight offset

            # Feedback loop protection:
            # Controls drive joints. Joints drive mesh.

            controls.append(ctrl)
            parent_ctrl = ctrl

        # 3. Skin Mesh
        try:
            pm.skinCluster(joints, rig.mesh, toSelectedBones=True)
        except Exception as e:
            rig.logger.warning(f"Failed to skin mesh: {e}")

        rig.logger.info("FK Chain Build Complete.")
        return TubeRigBundle(rig_group=rig.rig_group, joints=joints, controls=controls)


class SplineIKStrategy(TubeStrategy):
    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig.logger.info("Building Spline IK Rig...")
        num_joints = kwargs.get("num_joints", 10)
        num_controls = kwargs.get("num_controls", 3)
        radius = kwargs.get("radius", 1.0)
        enable_stretch = kwargs.get("enable_stretch", True)
        enable_squash = kwargs.get("enable_squash", True)
        enable_volume = kwargs.get("enable_volume", True)
        enable_twist = kwargs.get("enable_twist", True)
        enable_auto_bend = kwargs.get("enable_auto_bend", False)

        centerline, num_joints = TubePath.get_centerline(rig.mesh, num_joints)

        # 1. Main Tube Skeleton
        joints = rig.generate_joint_chain(
            centerline, num_joints=num_joints, radius=radius
        )

        # Fix Orientation: X down the chain
        if joints:
            rig.logger.debug("Orienting joints matching Spline IK (X-down)...")
            pm.select(joints[:-1])
            pm.joint(e=True, oj="xyz", sao="yup", ch=False)
            pm.select(joints[-1])
            pm.joint(e=True, oj="none", ch=False)

        # 2. Logic Curve
        curve = rig.create_logic_curve(centerline)

        # 3. Create IK Spline using RigUtils
        ik_name = f"{rig.rig_name}_ikHandle"
        ik_handle = RigUtils.create_ik_handle(
            joints[0],
            joints[-1],
            solver="ikSplineSolver",
            name=ik_name,
            parent=rig.rig_group,
            curve=curve,
            createCurve=False,
        )
        ik_handle.visibility.set(False)

        # 4. Driver System (Enhanced with Visual Tangents or N-Point)
        controls, driver_joints, up_locs = rig.create_spline_drivers(
            centerline, radius, num_controls
        )
        rig.skin_curve_to_drivers(curve, driver_joints)

        # Unpack controls
        start_ctrl = controls[0]
        end_ctrl = controls[-1]
        # Mid control is the middle-most control, or None if only 2 (unlikely for spline)
        mid_idx = int(len(controls) / 2)
        mid_ctrl = controls[mid_idx] if len(controls) > 2 else None

        start_up_loc, end_up_loc = up_locs

        # 5. Advanced Twist Setup (optional)
        if enable_twist:
            rig.setup_spline_twist(
                ik_handle, start_ctrl, end_ctrl, start_up_loc, end_up_loc
            )

        # 6. Auto Bend (Mid Control Logic) - Only supports 3-point system properly for now
        if enable_auto_bend and num_controls == 3 and mid_ctrl:
            rig.setup_auto_bend(start_ctrl, mid_ctrl, end_ctrl)
        elif enable_auto_bend and num_controls != 3:
            rig.logger.warning("Auto Bend is only available with 3 controls. Skipping.")

        # 7. Setup Stretch / Squash
        if enable_stretch or enable_squash:
            rig.setup_spline_stretch(
                curve,
                joints,
                enable_stretch,
                enable_squash,
                enable_volume,
                main_control=start_ctrl,
            )

        # Skin Mesh
        try:
            pm.skinCluster(joints, rig.mesh, toSelectedBones=True)
        except Exception as e:
            rig.logger.warning(f"Failed to skin mesh: {e}")

        rig.logger.info("Spline IK Build Complete.")
        return TubeRigBundle(
            rig_group=rig.rig_group,
            joints=joints,
            ik_handle=ik_handle,
            curve=curve,
            controls=controls,
        )


class AnchorStrategy(TubeStrategy):
    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig.logger.info("Building Anchor Rig...")
        centerline, _ = TubePath.get_centerline(rig.mesh, num_joints=2)
        if not centerline:
            raise ValueError("Could not determine centerline")

        start_pos = pm.datatypes.Vector(centerline[0])
        end_pos = pm.datatypes.Vector(centerline[-1])

        radius = kwargs.get("radius", 1.0)
        enable_stretch = kwargs.get("enable_stretch", True)

        # Calculate tube direction for control orientation
        tube_dir = (end_pos - start_pos).normal()

        # Build rotation matrix: X-axis = tube direction
        world_up = pm.datatypes.Vector(0, 1, 0)
        if abs(tube_dir.dot(world_up)) > 0.99:
            world_up = pm.datatypes.Vector(0, 0, 1)
        z_axis = tube_dir.cross(world_up).normal()
        y_axis = z_axis.cross(tube_dir).normal()

        rot_matrix = pm.datatypes.Matrix(
            [
                [tube_dir.x, tube_dir.y, tube_dir.z, 0],
                [y_axis.x, y_axis.y, y_axis.z, 0],
                [z_axis.x, z_axis.y, z_axis.z, 0],
                [0, 0, 0, 1],
            ]
        )
        start_rot = pm.datatypes.TransformationMatrix(rot_matrix).euler

        end_rot_matrix = pm.datatypes.Matrix(
            [
                [-tube_dir.x, -tube_dir.y, -tube_dir.z, 0],
                [y_axis.x, y_axis.y, y_axis.z, 0],
                [-z_axis.x, -z_axis.y, -z_axis.z, 0],
                [0, 0, 0, 1],
            ]
        )
        end_rot = pm.datatypes.TransformationMatrix(end_rot_matrix).euler

        # Create Controls (oriented along tube axis)
        start_nodes = Controls.box(
            name=f"{rig.rig_name}_start",
            scale=radius * 4,
            color=(0, 1, 1),
            return_nodes=True,
        )
        if start_nodes.group:
            start_nodes.group.setTranslation(start_pos, space="world")
            start_nodes.group.setRotation(start_rot, space="world")
            start_nodes.group.setParent(rig.rig_group)
        else:
            start_nodes.control.setTranslation(start_pos, space="world")
            start_nodes.control.setRotation(start_rot, space="world")
            start_nodes.control.setParent(rig.rig_group)
        start_ctrl = start_nodes.control

        end_nodes = Controls.box(
            name=f"{rig.rig_name}_end",
            scale=radius * 4,
            color=(0, 1, 1),
            return_nodes=True,
        )
        if end_nodes.group:
            end_nodes.group.setTranslation(end_pos, space="world")
            end_nodes.group.setRotation(end_rot, space="world")
            end_nodes.group.setParent(rig.rig_group)
        else:
            end_nodes.control.setTranslation(end_pos, space="world")
            end_nodes.control.setRotation(end_rot, space="world")
            end_nodes.control.setParent(rig.rig_group)
        end_ctrl = end_nodes.control

        # Create joint group (separate from controls for clean export)
        joint_grp = pm.group(empty=True, name=f"{rig.rig_name}_joints_GRP")
        joint_grp.setParent(rig.rig_group)

        # Create Joints in their own hierarchy
        pm.select(clear=True)
        j1 = pm.createNode("joint", name=f"{rig.rig_name}_start_jnt")
        j1.setTranslation(start_pos, space="world")
        j1.setParent(joint_grp)
        j1.radius.set(radius)

        pm.select(clear=True)
        j2 = pm.createNode("joint", name=f"{rig.rig_name}_end_jnt")
        j2.setTranslation(end_pos, space="world")
        j2.setParent(joint_grp)
        j2.radius.set(radius)

        joints = [j1, j2]

        # Constrain joints to follow control position and rotation
        pm.pointConstraint(start_ctrl, j1, mo=True)
        pm.pointConstraint(end_ctrl, j2, mo=True)

        # Orient constraints: joints follow control rotation (allows rotatable tube ends)
        pm.orientConstraint(start_ctrl, j1, mo=True)
        pm.orientConstraint(end_ctrl, j2, mo=True)

        # Scale logic: Distance-based stretch (optional)
        if enable_stretch:
            # Use simple distanceBetween node, but compensate for rig scale to avoid double transforms
            # We achieve this by measuring distance in the Rig's local space (using multMatrix)
            # local_pos = ctrl.worldMatrix * rig.worldInverseMatrix

            start_local_mm = pm.createNode(
                "multMatrix", name=f"{rig.rig_name}_start_local_MM"
            )
            start_ctrl.worldMatrix[0].connect(start_local_mm.matrixIn[0])
            rig.rig_group.worldInverseMatrix[0].connect(start_local_mm.matrixIn[1])

            end_local_mm = pm.createNode(
                "multMatrix", name=f"{rig.rig_name}_end_local_MM"
            )
            end_ctrl.worldMatrix[0].connect(end_local_mm.matrixIn[0])
            rig.rig_group.worldInverseMatrix[0].connect(end_local_mm.matrixIn[1])

            dist_node = pm.createNode("distanceBetween", name=f"{rig.rig_name}_dist")
            start_local_mm.matrixSum.connect(dist_node.inMatrix1)
            end_local_mm.matrixSum.connect(dist_node.inMatrix2)

            initial_dist = start_pos.distanceTo(end_pos)

            norm_md = pm.createNode("multiplyDivide", name=f"{rig.rig_name}_scale_MD")
            norm_md.operation.set(2)  # Divide
            dist_node.distance.connect(norm_md.input1X)
            norm_md.input2X.set(initial_dist)

            # Start joint scales to stretch toward end
            norm_md.outputX.connect(j1.scaleX)

        # Smooth Skin the mesh to the joints
        try:
            pm.skinCluster(joints, rig.mesh, toSelectedBones=True)
        except Exception as e:
            rig.logger.warning(f"Failed to skin mesh: {e}")

        rig.logger.info("Anchor Rig Build Complete.")
        return TubeRigBundle(
            rig_group=rig.rig_group,
            joints=joints,
            anchors=None,
            controls=[start_ctrl, end_ctrl],
        )


# ======================================================================
# Rig Engine
# ======================================================================


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
        self._rig_group = rig_group  # Only assigned if explicitly passed (else will be handled by property)
        obj = NodeUtils.get_transform_node(obj)
        if not obj:
            raise ValueError(f"Invalid object: `{obj}` {type(obj)}")
        elif isinstance(obj, (set, list, tuple)):
            obj = obj[0]
        self.mesh = obj
        try:
            self.mesh.rig = self  # Allow access to the rig instance via mesh attribute
        except Exception:
            pass
        self.joints = None
        self.ik_handle = None
        self.pole_vector = None
        self.skin_cluster = None
        self.start_loc = None
        self.end_loc = None
        self.bundle = None

    # ------------------------------------------------------------------
    # Properties / Rig Infrastructure
    # ------------------------------------------------------------------

    @property
    def rig_name(self) -> str:
        """Returns the rig name."""
        if not self._rig_name:
            self._rig_name = Naming.generate_unique_name("tube_rig_0")
        return self._rig_name

    @property
    def rig_group(self) -> "pm.nodetypes.Transform":
        if not self._rig_group:
            rig_name = f"{self.rig_name}_GRP"
            if pm.objExists(rig_name):
                self.logger.info(f"Found rig group: {rig_name}")
                self._rig_group = pm.ls(rig_name)[0]
            else:
                self.logger.info(f"Creating rig group: {rig_name}")
                self._rig_group = pm.group(empty=True, name=rig_name)
                pm.makeIdentity(self._rig_group, apply=True, t=1, r=1, s=1, n=0)
                try:
                    self._rig_group.rig = self
                except Exception:
                    pass
                self.logger.debug(f"Created/Found rig group: {self._rig_group.name()}")
        return NodeUtils.get_transform_node(self._rig_group)

    @rig_group.setter
    def rig_group(self, new_group: "pm.nodetypes.Transform"):
        """Allows setting a custom rig group."""
        if new_group and isinstance(new_group, pm.nodetypes.Transform):
            self._rig_group = new_group
            self.logger.debug(f"Rig group set to: {self._rig_group.name()}")
        else:
            self._rig_group = None  # Will trigger auto-create if accessed
            self.logger.debug("Rig group reset (None); will be auto-created on access.")

    def build(self, strategy: str = "spline", **kwargs):
        """Builds the rig using the specified strategy.

        Args:
            strategy (str): The rigging strategy to use ("spline", "anchor", "fk").
            **kwargs: Additional arguments for the build process.
        """
        if strategy == "spline":
            strat = SplineIKStrategy()
        elif strategy == "anchor":
            strat = AnchorStrategy()
        elif strategy == "fk":
            strat = FKChainStrategy()
        else:
            raise NotImplementedError(f"Strategy '{strategy}' not implemented.")

        self.bundle = strat.build(self, **kwargs)

        # Populate legacy attributes for backward compatibility
        self.joints = self.bundle.joints

        # Bundle might have ik_handle or controls
        if self.bundle.ik_handle:
            self.ik_handle = self.bundle.ik_handle
        if self.bundle.anchors:
            self.anchors = self.bundle.anchors

        # Add controls to legacy attributes if supported in future or just use bundle
        # However, to be nice to consumers:
        if self.bundle.controls:
            # Just expose the main start/end controls
            self.start_loc = self.bundle.controls[0]
            self.end_loc = self.bundle.controls[-1]

        return self

    # ------------------------------------------------------------------
    # Joint Creation
    # ------------------------------------------------------------------

    @CoreUtils.undoable
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
            centerline: List of points defining the tube's path.
            num_joints: Number of joints to create. If -1, creates one joint per
                centerline point (useful when centerline is from edge loops).
            reverse: If True, reverses the joint chain direction.
        """
        radius: float = kwargs.pop("radius", 1.0)
        orientation: List[float] = kwargs.pop("orientation", [0, 0, 0])

        if num_joints == -1:
            # Use centerline points directly as joint positions
            joint_positions = list(centerline)
            if reverse:
                joint_positions = joint_positions[::-1]
        else:
            joint_positions = ptk.dist_points_along_centerline(
                centerline, num_joints, reverse
            )
        joints = []
        parent_joint = None

        for i, pos in enumerate(joint_positions):
            self.logger.debug(
                f"Generating joint {i+1}, position: {pos}, radius: {radius}, orientation: {orientation}"
            )
            # Always clear selection before joint creation to avoid Maya's implicit parenting
            pm.select(clear=True)
            jnt = pm.createNode(
                "joint",
                name=f"{self.rig_name}_jnt_{i+1}",
            )
            pm.xform(jnt, ws=True, t=pos)
            jnt.radius.set(radius)
            # Orientation (if needed)
            if orientation:
                jnt.jointOrient.set(orientation)
            # Parent
            if i == 0:
                pm.parent(jnt, self.rig_group)
            else:
                pm.parent(jnt, parent_joint)
            parent_joint = jnt
            joints.append(jnt)

        self.logger.debug(f"Generated joints: {[jnt.name() for jnt in joints]}")
        self.joints = joints
        return joints

    # ------------------------------------------------------------------
    # Curves & IK
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_logic_curve(
        self, centerline: List[List[float]]
    ) -> "pm.nodetypes.NurbsCurve":
        """Creates the logic curve for Spline IK."""
        degree = 3 if len(centerline) >= 4 else 1
        curve_name = f"{self.rig_name}_ik_curve"
        curve = pm.curve(p=centerline, d=degree, name=curve_name)
        curve.setParent(self.rig_group)
        curve.inheritsTransform.set(False)  # Prevent double transform
        curve.visibility.set(False)
        return curve

    # ------------------------------------------------------------------
    # Spline IK Driver System (controls, tangents, up locators)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_spline_drivers(
        self, centerline: List[List[float]], radius: float = 1.0, num_controls: int = 3
    ) -> Tuple[List["pm.nodetypes.Transform"], List["pm.nodetypes.Joint"], List]:
        """Creates the driver system (controls and joints) for the Spline IK curve."""
        start_pos = pm.datatypes.Vector(centerline[0])
        end_pos = pm.datatypes.Vector(centerline[-1])
        tube_length = start_pos.distanceTo(end_pos)

        # Calculate orientation frames
        tube_dir = (end_pos - start_pos).normal()
        world_up = pm.datatypes.Vector(0, 1, 0)
        if abs(tube_dir.dot(world_up)) > 0.99:
            world_up = pm.datatypes.Vector(0, 0, 1)
        z_axis = tube_dir.cross(world_up).normal()
        y_axis = z_axis.cross(tube_dir).normal()

        # Rotation matrices
        rot_matrix = pm.datatypes.Matrix(
            [
                [tube_dir.x, tube_dir.y, tube_dir.z, 0],
                [y_axis.x, y_axis.y, y_axis.z, 0],
                [z_axis.x, z_axis.y, z_axis.z, 0],
                [0, 0, 0, 1],
            ]
        )
        start_rot = pm.datatypes.TransformationMatrix(rot_matrix).euler

        end_rot_matrix = pm.datatypes.Matrix(
            [
                [-tube_dir.x, -tube_dir.y, -tube_dir.z, 0],
                [y_axis.x, y_axis.y, y_axis.z, 0],
                [-z_axis.x, -z_axis.y, -z_axis.z, 0],
                [0, 0, 0, 1],
            ]
        )
        end_rot = pm.datatypes.TransformationMatrix(end_rot_matrix).euler

        # Helper
        def _create_ctrl(
            name, pos, rot=None, scale=1.0, color=(1, 1, 0), shape="box", parent=None
        ):
            if shape == "box":
                nodes = Controls.box(
                    name=name, scale=scale, color=color, return_nodes=True
                )
            elif shape == "sphere":
                nodes = Controls.sphere(
                    name=name, radius=scale, color=color, return_nodes=True
                )
            else:
                nodes = Controls.box(
                    name=name, scale=scale, color=color, return_nodes=True
                )

            grp = nodes.group if nodes.group else nodes.control
            grp.setTranslation(pos, space="world")
            if rot:
                grp.setRotation(rot, space="world")

            if parent:
                grp.setParent(parent)
            else:
                grp.setParent(self.rig_group)
            return nodes.control

        driver_grp = pm.group(empty=True, name=f"{self.rig_name}_driver_GRP")
        driver_grp.setParent(self.rig_group)
        driver_grp.visibility.set(False)

        controls = []
        driver_joints = []
        start_up_loc = None
        end_up_loc = None

        if num_controls == 3:
            # ------------------------------------------------------------------
            # Standard 3-Point System (Start, Mid, End + Tangents)
            # ------------------------------------------------------------------
            mid_pos = (start_pos + end_pos) / 2
            start_ctrl = _create_ctrl(
                f"{self.rig_name}_start", start_pos, start_rot, radius * 3
            )
            mid_ctrl = _create_ctrl(f"{self.rig_name}_mid", mid_pos, None, radius * 2.5)
            end_ctrl = _create_ctrl(
                f"{self.rig_name}_end", end_pos, end_rot, radius * 3
            )

            controls = [start_ctrl, mid_ctrl, end_ctrl]

            # Tangent Controls
            tan_offset = tube_length * 0.2

            # Start Tangent
            start_tan_pos = (
                start_pos
                + pm.datatypes.Vector(
                    rot_matrix[0][0], rot_matrix[0][1], rot_matrix[0][2]
                )
                * tan_offset
            )
            start_tan_ctrl = _create_ctrl(
                f"{self.rig_name}_start_tan",
                start_tan_pos,
                rot=start_rot,
                scale=radius * 0.5,
                color=(1, 0.5, 0),  # Orange
                shape="sphere",
                parent=start_ctrl,
            )

            # End Tangent
            end_tan_pos = (
                end_pos
                + pm.datatypes.Vector(
                    end_rot_matrix[0][0], end_rot_matrix[0][1], end_rot_matrix[0][2]
                )
                * tan_offset
            )
            end_tan_ctrl = _create_ctrl(
                f"{self.rig_name}_end_tan",
                end_tan_pos,
                rot=end_rot,
                scale=radius * 0.5,
                color=(1, 0.5, 0),  # Orange
                shape="sphere",
                parent=end_ctrl,
            )

            # Driver Joints (5 joints for 3 controls + 2 tangents)
            driver_sources = [
                (start_ctrl, "start"),
                (start_tan_ctrl, "start_tan"),
                (mid_ctrl, "mid"),
                (end_tan_ctrl, "end_tan"),
                (end_ctrl, "end"),
            ]

            for source, suffix in driver_sources:
                jnt = pm.createNode(
                    "joint", name=f"{self.rig_name}_driver_{suffix}_jnt"
                )
                jnt.setTranslation(source.getTranslation(space="world"), space="world")
                jnt.setParent(driver_grp)
                jnt.radius.set(radius * 1.5)
                pm.parentConstraint(source, jnt, mo=True)
                driver_joints.append(jnt)

        else:
            # ------------------------------------------------------------------
            # Distributed N-Point System
            # ------------------------------------------------------------------
            positions = ptk.dist_points_along_centerline(centerline, num_controls)

            for i, pos in enumerate(positions):
                name = f"{self.rig_name}_ctrl_{i+1}"

                # Determine rotation (Match start/end, others identity/world)
                rot = None
                if i == 0:
                    rot = start_rot
                elif i == len(positions) - 1:
                    rot = end_rot

                ctrl = _create_ctrl(
                    name, pos, rot=rot, scale=radius * 2.5, color=(1, 1, 0), shape="box"
                )
                controls.append(ctrl)

                # Driver Joint (1:1)
                jnt = pm.createNode("joint", name=f"{self.rig_name}_driver_{i+1}_jnt")
                jnt.setTranslation(ctrl.getTranslation(space="world"), space="world")
                jnt.setParent(driver_grp)
                jnt.radius.set(radius * 1.5)
                pm.parentConstraint(ctrl, jnt, mo=True)
                driver_joints.append(jnt)

        # Up Locators (Start/End Twist Anchors)
        up_offset = tube_length * 0.1  # 10% of tube length

        start_up_loc = pm.spaceLocator(name=f"{self.rig_name}_start_up_loc")
        if isinstance(start_up_loc, list):
            start_up_loc = start_up_loc[0]
        start_up_loc.setParent(driver_grp)
        # Position at control + world Y offset
        s_ctrl = controls[0]
        s_pos = s_ctrl.getTranslation(space="world")
        start_up_loc.setTranslation(
            [s_pos[0], s_pos[1] + up_offset, s_pos[2]], space="world"
        )
        pm.pointConstraint(s_ctrl, start_up_loc, mo=True)
        start_up_loc.visibility.set(False)

        end_up_loc = pm.spaceLocator(name=f"{self.rig_name}_end_up_loc")
        if isinstance(end_up_loc, list):
            end_up_loc = end_up_loc[0]
        end_up_loc.setParent(driver_grp)
        # Position at control + world Y offset
        e_ctrl = controls[-1]
        e_pos = e_ctrl.getTranslation(space="world")
        end_up_loc.setTranslation(
            [e_pos[0], e_pos[1] + up_offset, e_pos[2]], space="world"
        )
        pm.pointConstraint(e_ctrl, end_up_loc, mo=True)
        end_up_loc.visibility.set(False)

        return (
            controls,
            driver_joints,
            [start_up_loc, end_up_loc],
        )

    @CoreUtils.undoable
    def skin_curve_to_drivers(self, curve, driver_joints):
        try:
            pm.skinCluster(driver_joints, curve, toSelectedBones=True)
        except Exception as e:
            self.logger.warning(f"Failed to skin curve: {e}")

    @CoreUtils.undoable
    def setup_spline_twist(
        self, ik_handle, start_ctrl, end_ctrl, start_up_loc=None, end_up_loc=None
    ):
        """Setup advanced twist for IK Spline.

        Args:
            ik_handle: The IK Spline handle.
            start_ctrl: Start control transform.
            end_ctrl: End control transform.
            start_up_loc: Optional up locator for start (child of start_ctrl). If None, uses control.
            end_up_loc: Optional up locator for end (child of end_ctrl). If None, uses control.
        """
        ik_handle.dTwistControlEnable.set(True)

        if start_up_loc and end_up_loc:
            # Use Object Up (Start/End) - more stable when controls translate
            # dWorldUpType = 4 uses Y-axis of the up objects
            ik_handle.dWorldUpType.set(4)  # Object Rotation Up (Start/End)
            ik_handle.dWorldUpAxis.set(0)  # Positive Y
            ik_handle.dWorldUpVectorY.set(1)
            ik_handle.dWorldUpVectorEndY.set(1)
            start_up_loc.worldMatrix[0].connect(ik_handle.dWorldUpMatrix)
            end_up_loc.worldMatrix[0].connect(ik_handle.dWorldUpMatrixEnd)
        else:
            # Fallback to control matrices (original behavior)
            ik_handle.dWorldUpType.set(4)  # Object Rotation Up (Start/End)
            start_ctrl.worldMatrix[0].connect(ik_handle.dWorldUpMatrix)
            end_ctrl.worldMatrix[0].connect(ik_handle.dWorldUpMatrixEnd)

        # Add roll attribute if it doesn't exist
        if not end_ctrl.hasAttr("roll"):
            pm.addAttr(end_ctrl, ln="roll", at="double", k=True)
        end_ctrl.roll.connect(ik_handle.roll)

    @CoreUtils.undoable
    def setup_auto_bend(self, start_ctrl, mid_ctrl, end_ctrl):
        """Setup automatic bending of the mid control based on compression distance."""
        # Create settings attribute
        if not start_ctrl.hasAttr("autoBend"):
            pm.addAttr(
                start_ctrl, ln="autoBend", at="double", min=0, max=5, dv=0.0, k=True
            )

        # Identify the mid control's offset group (the one parented to rig_group)
        # mid_ctrl is the curve. mid_ctrl.getParent() is usually the offset group.
        offset_grp = mid_ctrl.getParent()
        if not offset_grp or offset_grp == self.rig_group:
            # Fallback if no offset group exists (unlikely given _create_ctrl)
            offset_grp = mid_ctrl

        # Create AutoBend Group
        auto_bend_grp = pm.group(empty=True, name=f"{self.rig_name}_mid_autoBend_GRP")

        # Match transform of the offset group (which is at mid position)
        pm.delete(pm.parentConstraint(offset_grp, auto_bend_grp))

        # Insert into hierarchy: RigGroup -> AutoBend -> Offset -> Control
        current_parent = offset_grp.getParent()
        if current_parent:
            auto_bend_grp.setParent(current_parent)
        offset_grp.setParent(auto_bend_grp)

        # Logic: (Initial_Length - Current_Dist) * autoBend -> translateY
        dist_node = pm.createNode("distanceBetween", name=f"{self.rig_name}_ab_dist")
        start_ctrl.worldMatrix[0].connect(dist_node.inMatrix1)
        end_ctrl.worldMatrix[0].connect(dist_node.inMatrix2)

        start_pos = start_ctrl.getTranslation(space="world")
        end_pos = end_ctrl.getTranslation(space="world")
        initial_length = start_pos.distanceTo(end_pos)

        # Calculate compression: initial_length - current_dist
        pma = pm.createNode("plusMinusAverage", name=f"{self.rig_name}_ab_sub")
        pma.operation.set(2)  # Subtract
        pma.input1D[0].set(initial_length)
        dist_node.distance.connect(pma.input1D[1])

        # Clamp min 0 (ignore stretching, only bend on compression)
        clamp = pm.createNode("clamp", name=f"{self.rig_name}_ab_clamp")
        clamp.minR.set(0)
        clamp.maxR.set(10000)
        pma.output1D.connect(clamp.inputR)

        # Multiply by autoBend factor
        md = pm.createNode("multiplyDivide", name=f"{self.rig_name}_ab_mult")
        clamp.outputR.connect(md.input1X)
        start_ctrl.autoBend.connect(md.input2X)

        # Apply to Y translation of the auto_bend_grp (assuming Y is "up" relative to layout)
        # Note: Ideally this would be vector-based, but Y-up is standard for this rig type.
        md.outputX.connect(auto_bend_grp.translateY)

    @CoreUtils.undoable
    def setup_spline_stretch(
        self,
        curve,
        joints,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        main_control=None,
    ):
        curve_info = pm.createNode("curveInfo", name=f"{self.rig_name}_curveInfo")
        curve.getShape().worldSpace[0].connect(curve_info.inputCurve)
        initial_length = curve_info.arcLength.get()

        scale_comp_md = pm.createNode(
            "multiplyDivide", name=f"{self.rig_name}_scale_comp_MD"
        )
        scale_comp_md.operation.set(2)  # Divide
        curve_info.arcLength.connect(scale_comp_md.input1X)
        self.rig_group.scaleX.connect(scale_comp_md.input2X)

        norm_md = pm.createNode("multiplyDivide", name=f"{self.rig_name}_norm_MD")
        norm_md.operation.set(2)
        scale_comp_md.outputX.connect(norm_md.input1X)
        norm_md.input2X.set(initial_length)

        # Clamp logic for separate stretch/squash control
        min_limit = 0.001 if enable_squash else 1.0
        max_limit = 10000.0 if enable_stretch else 1.0

        scale_val_src = norm_md.outputX
        if not enable_squash or not enable_stretch:
            clamp_node = pm.createNode("clamp", name=f"{self.rig_name}_scale_clamp")
            clamp_node.minR.set(min_limit)
            clamp_node.maxR.set(max_limit)
            norm_md.outputX.connect(clamp_node.inputR)
            scale_val_src = clamp_node.outputR

        # ----------------------------------------------------------------------
        # Attribute Setup (User Controls)
        # ----------------------------------------------------------------------
        stretch_output = scale_val_src

        if main_control:
            # Add Separator if not present (shared by vol and stretch)
            if not main_control.hasAttr("separator_opt"):
                pm.addAttr(
                    main_control, ln="separator_opt", at="enum", en="____", k=True
                )
                main_control.separator_opt.setLocked(True)

            # 1. Stretch blending (Animator toggles stretch effect)
            if enable_stretch or enable_squash:
                if not main_control.hasAttr("stretchFactor"):
                    pm.addAttr(
                        main_control,
                        ln="stretchFactor",
                        at="double",
                        min=0,
                        max=1,
                        dv=1.0,
                        k=True,
                    )

                # Blend between Calculated Stretch (Color1) and 1.0 (Color2)
                blend_stretch = pm.createNode(
                    "blendColors", name=f"{self.rig_name}_stretch_BLEND"
                )
                main_control.stretchFactor.connect(blend_stretch.blender)
                scale_val_src.connect(blend_stretch.color1R)
                blend_stretch.color2R.set(1.0)

                stretch_output = blend_stretch.outputR

        # ----------------------------------------------------------------------
        # Volume Preservation
        # ----------------------------------------------------------------------
        # scaleY = scaleZ = scaleX ^ -0.5
        vol_output = None

        if enable_volume:
            # Power node: scaleX ^ -0.5
            # We use stretch_output here so volume reacts to the blended stretch
            vol_pow = pm.createNode("multiplyDivide", name=f"{self.rig_name}_vol_POW")
            vol_pow.operation.set(3)  # Power
            stretch_output.connect(vol_pow.input1X)  # Base
            vol_pow.input2X.set(-0.5)  # Exponent

            vol_output = vol_pow.outputX

            # Logic: Blend between 1.0 and volumeResult based on volumeFactor
            if main_control:
                if not main_control.hasAttr("volumeFactor"):
                    pm.addAttr(
                        main_control,
                        ln="volumeFactor",
                        at="double",
                        min=0,
                        max=2,
                        dv=1.0,
                        k=True,
                    )

                # BlendColors: Blender=Factor, Color1=Volume, Color2=1.0
                blend_vol = pm.createNode(
                    "blendColors", name=f"{self.rig_name}_vol_BLEND"
                )
                main_control.volumeFactor.connect(blend_vol.blender)
                vol_pow.outputX.connect(blend_vol.color1R)
                blend_vol.color2R.set(1.0)

                vol_output = blend_vol.outputR

        for jnt in joints:
            stretch_output.connect(jnt.scaleX)
            if enable_volume and vol_output:
                vol_output.connect(jnt.scaleY)
                vol_output.connect(jnt.scaleZ)

    # ------------------------------------------------------------------
    # RP IK / Legacy Controls (Now delegated to RigUtils)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_start_end_locators(
        self,
        joints: List["pm.nodetypes.Joint"],
        ik_handle: Optional["pm.nodetypes.Transform"] = None,
    ) -> Tuple["pm.nodetypes.Transform", "pm.nodetypes.Transform"]:
        joints = pm.ls(joints, type="joint", flatten=True)
        if len(joints) < 2:
            self.logger.error("Not enough joints to create locators.")
            return None, None

        start_locator = pm.spaceLocator(name=f"{self.rig_name}_start_LOC")
        end_locator = pm.spaceLocator(name=f"{self.rig_name}_end_LOC")

        start_position = joints[0].getTranslation(space="world")
        end_position = joints[-1].getTranslation(space="world")

        start_locator.setTranslation(start_position, space="world")
        end_locator.setTranslation(end_position, space="world")

        pm.makeIdentity(start_locator, apply=True, t=1, r=1, s=1, n=0)
        pm.makeIdentity(end_locator, apply=True, t=1, r=1, s=1, n=0)

        start_locator.setParent(self.rig_group)
        end_locator.setParent(self.rig_group)

        # Constrain joints directly to locators without offsets
        pm.pointConstraint(start_locator, joints[0], maintainOffset=False)

        if ik_handle:
            # IK follows end_locator without offset
            pm.pointConstraint(end_locator, ik_handle, maintainOffset=False)

        RigUtils.set_attr_lock_state(
            (start_locator, end_locator), rotate=True, scale=True
        )

        self.start_loc = start_locator
        self.end_loc = end_locator
        return start_locator, end_locator

    @CoreUtils.undoable
    def create_ik(
        self, joints: List["pm.nodetypes.Joint"], **kwargs
    ) -> Optional["pm.nodetypes.Transform"]:
        # Wrapper for RigUtils.create_ik_handle to maintain API compatibility
        joints = pm.ls(joints, type="joint", flatten=True)
        if len(joints) < 2:
            self.logger.error("Insufficient joints to create IK handle.")
            return None

        name = kwargs.pop("name", f"{self.rig_name}_ikHandle")
        return RigUtils.create_ik_handle(
            start_joint=joints[0],
            end_joint=joints[-1],
            name=name,
            parent=self.rig_group,
            **kwargs,
        )

    @CoreUtils.undoable
    def create_pole_vector(
        self, ik_handle, mid_joint: "pm.nodetypes.Joint", offset=(0, 5, 0)
    ) -> "pm.nodetypes.Transform":
        # Wrapper for RigUtils.create_pole_vector
        # Note: RigUtils uses 'distance' float while old method used vector offset tuple?
        # Old signature: offset=(0,5,0).
        # RigUtils expects distance.
        # We'll adapt.
        dist = pm.datatypes.Vector(offset).length()
        pv = RigUtils.create_pole_vector(
            ik_handle=ik_handle,
            mid_joint=mid_joint,
            distance=dist,
            name=f"{self.rig_name}_poleVector_LOC",
            parent=self.rig_group,
        )
        self.pole_vector = pv
        return pv

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Skinning
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def bind_joint_chain(
        self, obj, joints: List["pm.nodetypes.Joint"]
    ) -> Optional["pm.nodetypes.DependNode"]:
        """Binds the joint chain to a polygon tube with smooth skinning."""
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

        if not joints:
            self.logger.error("No joints to bind to the tube.")
            return None

        for attr in ("translate", "rotate", "scale"):
            for axis in "XYZ":
                try:
                    transform.attr(f"{attr}{axis}").setLocked(False)
                except Exception:
                    pass

        rig_group = self.rig_group
        tube_parent = transform.getParent()
        if tube_parent:
            rig_group.setParent(tube_parent)
            transform.setParent(world=True)

        for j in joints:
            if not j.getParent():
                j.setParent(rig_group)

        self.logger.debug(
            f"Creating skinCluster with joints: {[jnt.name() for jnt in joints]}, and tube: {transform.name()}"
        )

        try:
            skin_cluster = pm.skinCluster(
                joints,
                transform,
                toSelectedBones=True,
                maximumInfluences=4,
                weightDistribution=0.5,
            )
            self.logger.debug(f"SkinCluster created: {skin_cluster}")
        except Exception as e:
            self.logger.error(f"Error creating skinCluster: {str(e)}")
            return None

        self.skin_cluster = skin_cluster
        return skin_cluster

    # ------------------------------------------------------------------
    # Anchor Constraints
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def constrain_end_with_falloff(
        self,
        joints: List[pm.nt.Joint],
        anchor: pm.nt.Transform,
        falloff: float = 5.0,
        joint_index: int = -1,
    ) -> Optional[pm.nt.Joint]:
        """
        Constrains a joint in the chain to an anchor and applies distance-based skin weight falloff.

        Parameters:
            joints (List[pm.nt.Joint]): The hose joint chain.
            anchor (pm.nt.Transform): The transform the joint should follow.
            falloff (float): World-space distance over which anchor weight fades.
            joint_index (int): Index of the joint to constrain. Use 0 for start, -1 for end.

        Returns:
            pm.nt.Joint: The newly created anchor joint.
        """
        if not joints:
            self.logger.error("No joints provided.")
            return None

        constrained_joint = joints[joint_index]
        anchor_pos = anchor.getTranslation(space="world")

        # Create anchor joint at anchor location
        joint_name = Naming.generate_unique_name(f"{self.rig_name}_anchor_jnt")
        anchor_joint = pm.createNode("joint", name=joint_name)
        anchor_joint.translate.set(anchor_pos)
        anchor_joint.radius.set(constrained_joint.radius.get())
        pm.makeIdentity(anchor_joint, apply=True, t=True, r=True, s=True)
        pm.xform(anchor_joint, ws=True, t=anchor_pos)

        # Fully constrain anchor_joint to the anchor geo (position + orientation)
        pm.parentConstraint(anchor, anchor_joint, mo=False)

        # Avoid driving the joint directly if it's IK-controlled
        is_end_joint = (
            self.ik_handle
            and pm.ikHandle(self.ik_handle, q=True, ee=True) == constrained_joint
        )
        if is_end_joint:
            pm.parentConstraint(anchor_joint, self.ik_handle, mo=False)
        else:
            pm.parentConstraint(anchor_joint, constrained_joint, mo=False)

        # Add falloff skin weighting from anchor_joint to constrained_joint
        if self.skin_cluster:
            if anchor_joint not in self.skin_cluster.influenceObjects():
                pm.skinCluster(
                    self.skin_cluster, edit=True, addInfluence=anchor_joint, weight=0.0
                )

            try:
                verts = self.mesh.vtx[:]
                for v in verts:
                    pos = v.getPosition(space="world")
                    d = pos.distanceTo(anchor_pos)
                    if d > falloff:
                        continue

                    w = max(min(1.0 - (d / falloff), 1.0), 0.0)

                    pm.skinPercent(
                        self.skin_cluster,
                        v,
                        transformValue=[
                            (anchor_joint, w),
                            (constrained_joint, 1.0 - w),
                        ],
                    )
                self.logger.debug(
                    f"Applied falloff weights from {anchor_joint} (to joint index {joint_index}) over distance {falloff}"
                )
            except Exception as e:
                self.logger.warning(f"Skin weighting failed: {e}")

        # Ensure anchor joint is parented under the rig group
        if anchor_joint.getParent() != self.rig_group:
            anchor_joint.setParent(self.rig_group)

        return anchor_joint


# ======================================================================
# Rig Configuration (UI Logic)
# ======================================================================


@dataclass
class RigModeConfig:
    """Defines a rig mode's strategy and available options."""

    name: str
    strategy: str

    num_joints: int
    num_controls: int
    enable_stretch: bool
    enable_squash: bool
    enable_volume: bool
    enable_auto_bend: bool
    enable_twist: bool

    # UI State (Default: Editable)
    num_joints_editable: bool = True
    num_controls_editable: bool = True
    stretch_editable: bool = True
    squash_editable: bool = True
    volume_editable: bool = True
    auto_bend_editable: bool = True
    twist_editable: bool = True


# Rig Mode Registry
RIG_MODES: List[RigModeConfig] = [
    RigModeConfig(
        name="Spline (Hose/Cable)",
        strategy="spline",
        num_joints=-1,
        num_controls=3,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        enable_auto_bend=False,
        enable_twist=True,
    ),
    RigModeConfig(
        name="Anchor (Piston/Hydraulic)",
        strategy="anchor",
        num_joints=2,
        num_controls=2,
        enable_stretch=True,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        num_joints_editable=False,
        num_controls_editable=False,
        twist_editable=False,
        volume_editable=True,
    ),
    RigModeConfig(
        name="FK Chain (Tail/Tentacle)",
        strategy="fk",
        num_joints=-1,
        num_controls=-1,
        enable_stretch=False,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        stretch_editable=False,
        squash_editable=False,
        volume_editable=False,
        auto_bend_editable=False,
        twist_editable=False,
        num_controls_editable=False,
    ),
]


# ======================================================================
# UI Slots (thin event handlers — delegates to TubeRig / TubePath)
# ======================================================================


class TubeRigSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        # Bind to the UI that corresponds to this slots class (tube_rig.ui)
        self.ui = self.sb.loaded_ui.tube_rig

        # Configure SpinBox custom display
        # -1 indicates "Auto" mode where joint count is derived from edge loops
        self.ui.s000.setCustomDisplayValues(-1, "Auto")

        # Config UI Elements
        # Use uitk ComboBox text overlay features
        if hasattr(self.ui.cmb_preset, "setTextOverlay"):
            self.ui.cmb_preset.setTextOverlay("Mode:")

        # Connect mode combobox change signal
        # Re-populate combobox with new mode names
        self.ui.cmb_preset.clear()
        for mode in RIG_MODES:
            self.ui.cmb_preset.addItem(mode.name, mode)

        self.ui.cmb_preset.currentIndexChanged.connect(self.apply_mode)
        # Apply initial mode
        if len(RIG_MODES) > 0:
            self.apply_mode(0)

    def apply_mode(self, index: int):
        """Apply mode values and constraints to UI widgets."""
        mode = self.ui.cmb_preset.itemData(index)
        if not mode:
            # Fallback if somehow data is missing or index invalid (shouldn't happen with correct usage)
            mode = RIG_MODES[0] if RIG_MODES else None

        if not mode:
            return

        # Step 1: Joints
        self.ui.s000.setValue(mode.num_joints)
        self.ui.s000.setEnabled(mode.num_joints_editable)

        # Step 1.5: Controls Count
        if hasattr(self.ui, "s001"):
            self.ui.s001.setValue(mode.num_controls)
            self.ui.s001.setEnabled(mode.num_controls_editable)

        # Step 2: Controls
        self.ui.chk_stretch.setChecked(mode.enable_stretch)
        self.ui.chk_stretch.setEnabled(mode.stretch_editable)

        self.ui.chk_squash.setChecked(mode.enable_squash)
        self.ui.chk_squash.setEnabled(mode.squash_editable)

        self.ui.chk_volume.setChecked(mode.enable_volume)
        self.ui.chk_volume.setEnabled(mode.volume_editable)

        self.ui.chk_auto_bend.setChecked(mode.enable_auto_bend)
        self.ui.chk_auto_bend.setEnabled(mode.auto_bend_editable)

        self.ui.chk_twist.setChecked(mode.enable_twist)
        self.ui.chk_twist.setEnabled(mode.twist_editable)

    def get_mode(self) -> RigModeConfig:
        """Get the current rig mode config."""
        mode = self.ui.cmb_preset.currentData()
        return mode if mode else (RIG_MODES[0] if RIG_MODES else None)

    def get_strategy(self) -> str:
        """Get the current strategy from the mode combobox."""
        return self.get_mode().strategy

    def get_tube_rig(self, obj):
        """Get the tube rig instance for the given object, its parent, or mesh ancestor."""
        # If the object has a rig attribute, return it
        if hasattr(obj, "rig"):
            return obj.rig

        # If the object is a joint, check its parent for .rig
        if pm.nodeType(obj) == "joint":
            parent = obj.getParent()
            # print(f"Parent: {parent}")
            if parent and hasattr(parent, "rig"):
                # print(f"Found Parent rig: {parent.rig}")
                return parent.rig

        # Otherwise, instantiate a new TubeRig (fallback, but should rarely happen)
        rig_name = self.ui.txt000.text() or f"{obj.name()}_RIG"
        tube_rig = TubeRig(obj, rig_name=rig_name)
        return tube_rig

    def create_joints_from_tube(self, obj):
        """Creates a joint chain from a tube mesh."""
        num_joints = self.ui.s000.value()
        edges = pm.filterExpand(selectionMask=32)  # optional user edge selection

        centerline_points, num_joints = TubePath.get_centerline(
            obj,
            num_joints=num_joints,
            precision=50,
            edges=edges,
        )

        if not centerline_points or len(centerline_points) < 2:
            self.sb.message_box(
                "Failed to extract a valid centerline from the tube mesh."
            )
            return []

        tube_rig = self.get_tube_rig(obj)
        joints = tube_rig.generate_joint_chain(
            centerline=centerline_points,
            num_joints=num_joints,
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

    @CoreUtils.undoable
    def b000(self):
        """Create Tube Rig (Full Pipeline)."""
        try:
            obj, *_ = pm.selected(objectsOnly=True, flatten=True)
        except ValueError:
            self.sb.message_box("Select a single polygon tube mesh to create a rig.")
            return

        # Determine strategy from preset
        strategy = self.get_strategy()

        tube_rig = self.get_tube_rig(obj)

        try:
            tube_rig.build(
                strategy=strategy,
                num_joints=self.ui.s000.value(),
                # Retrieve num_controls from s001 if available, else default to 3
                num_controls=self.ui.s001.value() if hasattr(self.ui, "s001") else 3,
                radius=self.ui.s002.value(),
                enable_stretch=self.ui.chk_stretch.isChecked(),
                enable_squash=self.ui.chk_squash.isChecked(),
                enable_volume=self.ui.chk_volume.isChecked(),
                enable_auto_bend=self.ui.chk_auto_bend.isChecked(),
                enable_twist=self.ui.chk_twist.isChecked(),
            )
            self.sb.message_box(f"Tube rig ({strategy}) created: {tube_rig.rig_name}")
        except Exception as e:
            self.sb.message_box(f"Build failed: {e}")
            self.sb.logger.error(f"Build Error: {e}", exc_info=True)

    @CoreUtils.undoable
    def b001(self):
        """Create Joints from Tube."""
        try:
            obj, *_ = pm.selected(objectsOnly=True, flatten=True)
        except ValueError:
            self.sb.message_box("Select a single polygon tube mesh to create a rig.")
            return

        joints = self.create_joints_from_tube(obj)
        self.sb.message_box(f"Joints created: {len(joints)}")

    @CoreUtils.undoable
    def b002(self):
        """Create IK / Controls (Preset Dependent)."""
        # Determine strategy from preset
        strategy = self.get_strategy()

        try:
            sel = pm.selected(flatten=True)
            joints = pm.ls(sel, type="joint")
        except ValueError:
            self.sb.message_box("Select the root joint.")
            return

        if not joints:
            self.sb.message_box("No joints selected.")
            return

        # Ensure we have the full chain
        if len(joints) < 2:
            joints = RigUtils.get_joint_chain_from_root(joints[0])

        # Get rig instance
        tube_rig = self.get_tube_rig(joints[0])
        # Ensure tube_rig knows about these joints (if they were created manually or via b001)
        tube_rig.joints = joints

        if strategy == "spline":
            # 1. Get centerline from joints for perfect alignment
            centerline = [j.getTranslation(space="world") for j in joints]
            radius = self.ui.s002.value()
            enable_stretch = self.ui.chk_stretch.isChecked()
            enable_twist = self.ui.chk_twist.isChecked()

            # 2. Create Curve
            curve = tube_rig.create_logic_curve(centerline)

            # 3. Create IK Handle
            ik_name = f"{tube_rig.rig_name}_ikHandle"
            ik_handle = tube_rig.create_ik(
                joints,
                solver="ikSplineSolver",
                curve=curve,
                createCurve=False,
                name=ik_name,
            )
            ik_handle.visibility.set(False)

            # 4. Create Drivers
            controls, driver_joints, up_locs = tube_rig.create_spline_drivers(
                centerline, radius
            )
            tube_rig.skin_curve_to_drivers(curve, driver_joints)

            # 5. Setup Advanced Systems (based on user options)
            start_ctrl, mid_ctrl, end_ctrl = controls
            start_up_loc, end_up_loc = up_locs
            if enable_twist:
                tube_rig.setup_spline_twist(
                    ik_handle, start_ctrl, end_ctrl, start_up_loc, end_up_loc
                )
            if enable_stretch:
                tube_rig.setup_spline_stretch(curve, joints)

            self.sb.message_box(
                f"Spline IK Rig created on {len(joints)} joints.\nControls: {', '.join([c.name() for c in controls])}"
            )

        elif strategy == "anchor":
            # Implement Granular Anchor Logic if needed, or warn
            # For now, replicate AnchorStrategy logic roughly, or assume user uses b000 for Anchor.
            # But let's try to support it using standard methods if possible.
            # Anchor wraps start/end creation.
            self.sb.message_box(
                "Anchor mode split-operation not yet fully implemented in b002. Use 'Full Rig' (b000) for Anchor rigs."
            )

        else:
            # Fallback to Legacy RP Solver (standard 3-joint IK)
            # Create IK handle
            ik_handle = tube_rig.create_ik(joints, solver="ikRPsolver")

            # Create pole vector control with mid joint offset
            mid_joint_index = int(len(joints) / 2)
            mid_joint = joints[mid_joint_index]
            tube_rig.create_pole_vector(ik_handle, mid_joint=mid_joint)
            self.sb.message_box("Standard RP IK & Pole Vector created.")

    def b003(self):
        """Macros: Bind Joint Chain to Tube."""
        try:
            *joints, obj = pm.selected(flatten=True)
        except ValueError:
            self.sb.message_box(
                "Select at least one joint and then a tube mesh.\nUsage: [Root Joint] + [Tube Mesh]"
            )
            return

        if not joints:
            self.sb.message_box(
                "No joints selected. Select the root joint and then a tube mesh."
            )
            return

        # Order the joints by hierarchy using pymel
        joints = pm.ls(joints, type="joint", flatten=True)
        if not joints:
            self.sb.message_box(
                "No joint objects found in selection. Select joints, not other object types."
            )
            return

        if len(joints) < 2:
            joints = RigUtils.get_joint_chain_from_root(joints[0])
        if self.ui.chk000.isChecked():
            joints = RigUtils.invert_joint_chain(joints[0], keep_original=False)

        tube_rig = self.get_tube_rig(obj)
        if not tube_rig:
            self.sb.message_box("No tube rig found for the selected object.")
            return

        # Bind joint chain to the tube mesh
        skin_cluster = tube_rig.bind_joint_chain(obj, joints)
        if not skin_cluster:
            self.sb.message_box("Failed to bind joint chain to the tube.")
            return
        self.sb.message_box(f"Tube rig created: {tube_rig.rig_name}")

    @CoreUtils.undoable
    def b004(self):
        """Macros: Constrain Both Ends of Hose to Anchors."""
        sel = pm.selected(flatten=True)
        if len(sel) < 3:
            self.sb.message_box("Select root joint, start anchor, and end anchor.")
            return
        *joints, start_anchor, end_anchor = sel

        tube_rig = self.get_tube_rig(joints[0])
        joints = RigUtils.get_joint_chain_from_root(joints[0])

        falloff = 0.3  # fixed falloff

        start_result = tube_rig.constrain_end_with_falloff(
            joints, start_anchor, falloff=falloff, joint_index=0
        )
        end_result = tube_rig.constrain_end_with_falloff(
            joints, end_anchor, falloff=falloff, joint_index=-1
        )

        self.sb.message_box(
            f"Both ends constrained:\n  Start: {start_result.name()}\n  End: {end_result.name()}"
        )

    # -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.mayatk_ui_manager import UiManager

    ui = UiManager.instance().get("tube_rig", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
