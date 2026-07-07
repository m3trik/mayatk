#!/usr/bin/env python
# coding=utf-8
import math
from typing import Dict, List, Tuple, Optional, Union
from abc import ABC, abstractmethod
from dataclasses import dataclass

try:
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    mel = None
    om = None
    print(__file__, error)
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.rig_utils._rig_utils import RigUtils
from mayatk.rig_utils.controls import Controls
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.edit_utils.naming._naming import Naming
from mayatk.core_utils._core_utils import leaf_name, short_name


def _xform_t_ws(node) -> List[float]:
    """World-space translation as a 3-list (replaces ``node.getTranslation(space='world')``)."""
    return cmds.xform(str(node), q=True, ws=True, t=True)


def _set_t_ws(node, pos) -> None:
    """Write world-space translation (replaces ``node.setTranslation(p, space='world')``)."""
    cmds.xform(str(node), ws=True, t=(float(pos[0]), float(pos[1]), float(pos[2])))


def _set_r_ws(node, rot) -> None:
    """Write world-space rotation in degrees (replaces ``node.setRotation(r, space='world')``)."""
    cmds.xform(str(node), ws=True, ro=(float(rot[0]), float(rot[1]), float(rot[2])))


def _long_path(node) -> Optional[str]:
    """Return the long DAG path for *node*, or the input unchanged if unresolvable."""
    if node is None:
        return None
    s = str(node)
    if not s:
        return s
    res = cmds.ls(s, long=True) or []
    return res[0] if res else s


def _parent_to(child, parent) -> str:
    """Parent ``child`` under ``parent`` (or world if ``parent is None``) and return
    the new long path. No-op if the child is already under the intended parent.
    Wraps ``cmds.parent`` and returns the new long path.
    """
    child_s = str(child)
    current = NodeUtils.get_parent(child_s, type=None, full_path=True)  # already a long path or None
    if parent is None:
        if current is None:
            return _long_path(child_s)
        result = cmds.parent(child_s, world=True)
        return result[0] if result else _long_path(child_s)
    parent_long = _long_path(parent)
    if current and parent_long and current == parent_long:
        return _long_path(child_s)
    try:
        result = cmds.parent(child_s, str(parent))
        return result[0] if result else _long_path(child_s)
    except RuntimeError:
        return _long_path(child_s)


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
    def get_edge_loop_centers(mesh) -> Tuple[List[om.MPoint], int]:
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
        all_edges = cmds.ls(
            cmds.polyListComponentConversion(mesh, toEdge=True), flatten=True
        )
        if not all_edges:
            return [], 0

        # Start with first edge, get its edge loop (one circular cross-section).
        # all_edges[0] is a component path string like "mesh.e[12]" — extract the index.
        first_edge_idx = int(str(all_edges[0]).rsplit("[", 1)[-1].rstrip("]"))
        first_loop = cmds.polySelect(mesh, q=True, edgeLoop=first_edge_idx)
        if not first_loop:
            return [], 0

        # Get the edge ring from the first loop edge — yields one edge per cross-section.
        ring_edges = cmds.polySelect(mesh, q=True, edgeRing=first_loop[0])
        if not ring_edges:
            return [], 0

        # Cache the leaf name once for component-string construction below.
        mesh_short = short_name(mesh)

        visited_loops = set()
        loop_centers = []

        for edge_idx in ring_edges:
            loop_edges = cmds.polySelect(mesh, q=True, edgeLoop=edge_idx)
            if not loop_edges:
                continue

            loop_key = tuple(sorted(loop_edges))
            if loop_key in visited_loops:
                continue
            visited_loops.add(loop_key)

            # Collect unique vertex names in this loop.
            loop_vert_names = set()
            for e_idx in loop_edges:
                edge = f"{mesh_short}.e[{e_idx}]"
                verts = cmds.polyListComponentConversion(
                    edge, fromEdge=True, toVertex=True
                )
                for v in cmds.ls(verts, flatten=True):
                    # Store vertex name string (hashable) to avoid MeshVertex type error
                    loop_vert_names.add(str(v))

            # Calculate center of this loop. cmds.pointPosition returns a plain
            # 3-list, so accumulate via MVector and divide to get the centroid.
            if loop_vert_names:
                accum = om.MVector(0.0, 0.0, 0.0)
                for v in loop_vert_names:
                    p = cmds.pointPosition(v, world=True)
                    accum += om.MVector(p[0], p[1], p[2])
                count = len(loop_vert_names)
                center = om.MPoint(
                    accum.x / count, accum.y / count, accum.z / count
                )
                loop_centers.append(center)

        # Sort centers to form a continuous path along the tube
        if loop_centers:
            # 1. Arrange points
            loop_centers = ptk.Polyline.order_points(loop_centers)

            # 2. Filter duplicates/near-coincident points
            # Edge loops on high-res geometry or bevels can result in points that are virtually identical.
            # This causes joint orientation failures (zero-length bone vectors).
            filtered_centers = [loop_centers[0]]
            min_dist_sq = 0.001 * 0.001
            for i in range(1, len(loop_centers)):
                prev = om.MPoint(filtered_centers[-1])
                curr = om.MPoint(loop_centers[i])
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
    ) -> List[om.MPoint]:
        """Extracts the centerline points from selected edges of the tube."""
        centerline_points = []

        for edge in edge_selection:
            # Convert edge to vertices
            vertices = cmds.polyListComponentConversion(
                edge, fromEdge=True, toVertex=True
            )
            vertices = cmds.ls(vertices, flatten=True)

            # Get the positions of the vertices along the edge as dt.Point objects
            point1 = cmds.pointPosition(vertices[0], world=True)  # dt.Point
            point2 = cmds.pointPosition(vertices[1], world=True)  # dt.Point

            # Append dt.Point objects directly to the list
            centerline_points.append(point1)
            centerline_points.append(point2)

        # Sort the centerline points to form a continuous path
        centerline_points = ptk.Polyline.order_points(centerline_points)

        return centerline_points

    # ------------------------------------------------------------------
    # Algorithm: Surface-normal opposing-hit averaging
    # ------------------------------------------------------------------

    @staticmethod
    def get_centerline_from_surface_normals(
        mesh,
        num_points: int = 10,
        iterations: int = 3,
    ) -> List[om.MPoint]:
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
            List of centerline points as ``om.MPoint``.
        """
        mesh = NodeUtils.get_transform_node(mesh)
        if not mesh:
            raise ValueError(f"Invalid object: `{mesh}` {type(mesh)}")

        bbox = cmds.exactWorldBoundingBox(mesh)
        min_pt = om.MPoint(bbox[0], bbox[1], bbox[2])
        max_pt = om.MPoint(bbox[3], bbox[4], bbox[5])
        bbox_size = max_pt - min_pt
        largest_axis = max(range(3), key=lambda i: bbox_size[i])

        # Utility node for fast surface queries
        cpom = cmds.createNode("closestPointOnMesh")
        mesh_shape = NodeUtils.get_shape(mesh)
        cmds.connectAttr(f"{mesh_shape}.outMesh", f"{cpom}.inMesh")
        cmds.connectAttr(f"{mesh_shape}.worldMatrix[0]", f"{cpom}.inputMatrix")

        try:
            # Seed: sample evenly along the largest bbox axis through bbox center
            # MPoint + MPoint is not supported in OM2; midpoint via vector sum.
            bbox_center = om.MPoint(
                (min_pt.x + max_pt.x) / 2,
                (min_pt.y + max_pt.y) / 2,
                (min_pt.z + max_pt.z) / 2,
            )
            step = bbox_size[largest_axis] / (num_points + 1)

            centers = []
            for i in range(1, num_points + 1):
                pt = om.MPoint(bbox_center)
                pt[largest_axis] = min_pt[largest_axis] + i * step
                centers.append(pt)

            # Iteratively refine via opposing-surface-hit averaging
            for _ in range(iterations):
                refined = []
                for center in centers:
                    cmds.setAttr(
                        f"{cpom}.inPosition",
                        center.x, center.y, center.z,
                        type="double3",
                    )
                    pos_arr = cmds.getAttr(f"{cpom}.position")[0]
                    surface_pt = om.MPoint(pos_arr[0], pos_arr[1], pos_arr[2])

                    # Direction from current estimate to nearest surface
                    to_surface = om.MVector(surface_pt - center)
                    radius_est = to_surface.length()
                    if radius_est < 1e-6:
                        refined.append(center)
                        continue

                    direction = to_surface.normal()

                    # Query from the opposite side — overshoot past the far wall
                    opposite_query = center - direction * (radius_est * 3)
                    cmds.setAttr(
                        f"{cpom}.inPosition",
                        opposite_query.x, opposite_query.y, opposite_query.z,
                        type="double3",
                    )
                    pos_arr2 = cmds.getAttr(f"{cpom}.position")[0]
                    surface_pt2 = om.MPoint(pos_arr2[0], pos_arr2[1], pos_arr2[2])

                    # Midpoint of opposing surface hits ≈ true center (component-wise).
                    refined.append(om.MPoint(
                        (surface_pt.x + surface_pt2.x) / 2,
                        (surface_pt.y + surface_pt2.y) / 2,
                        (surface_pt.z + surface_pt2.z) / 2,
                    ))

                centers = refined

            # Order as a continuous path
            centers = ptk.Polyline.order_points(centers)
            return centers

        finally:
            cmds.delete(cpom)

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
            list: Centerline points as a list of ``om.MPoint``.
        """
        obj = NodeUtils.get_transform_node(obj)
        if not obj:
            raise ValueError(f"Invalid object: `{obj}` {type(obj)}")

        # Calculate the bounding box of the object
        bbox = cmds.exactWorldBoundingBox(obj)
        min_point = om.MPoint(bbox[0], bbox[1], bbox[2])
        max_point = om.MPoint(bbox[3], bbox[4], bbox[5])

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
            vertices = cmds.ls(
                cmds.polyListComponentConversion(obj, toVertex=True), flatten=True
            )
            slice_vertices = [
                vtx
                for vtx in vertices
                if abs(cmds.pointPosition(vtx)[largest_axis] - slice_pos) < step / 2
            ]

            if not slice_vertices:
                continue

            # Calculate the centroid of the slice (cmds.pointPosition returns a
            # plain 3-list, so accumulate via MVector and divide).
            accum = om.MVector(0.0, 0.0, 0.0)
            for vtx in slice_vertices:
                p = cmds.pointPosition(vtx)
                accum += om.MVector(p[0], p[1], p[2])
            count = len(slice_vertices)
            center_point = om.MPoint(
                accum.x / count, accum.y / count, accum.z / count
            )
            centerline_points.append(center_point)

        # Apply smoothing if requested
        if smooth and centerline_points:
            centerline_points = ptk.Polyline.smooth(centerline_points, window_size)

        return centerline_points


# ======================================================================
# Data Containers
# ======================================================================


@dataclass
class TubeRigBundle:
    rig_group: str
    joints: List[str]
    ik_handle: Optional[str] = None
    curve: Optional[str] = None
    anchors: Optional[List[str]] = None
    controls: Optional[List[str]] = None


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
            cmds.select(joints[0], hierarchy=True)
            cmds.joint(e=True, oj="xyz", sao="yup", ch=True, zso=True)
            # Find the last joint and zero its orient (it has no child to aim at)
            cmds.setAttr(f"{joints[-1]}.jointOrient", 0, 0, 0, type="double3")

        # 2. Create FK Controls
        controls = []
        parent_ctrl = str(rig.rig_group)

        # Create control hierarchy
        for i, jnt in enumerate(joints):
            ctrl_name = f"{rig.rig_name}_{i+1}_CTRL"
            scale = radius * 3

            nodes = Controls.create(
                "diamond",
                name=ctrl_name,
                size=scale,
                axis="x",
                color=(1, 1, 0),
                return_nodes=True,
            )
            ctrl = nodes.control
            grp = nodes.group if nodes.group else ctrl

            # Match joint transform via parentConstraint (clean matrix transfer)
            temp_const = cmds.parentConstraint(str(jnt), str(grp))
            cmds.delete(temp_const)

            grp = _parent_to(grp, parent_ctrl)

            # Standard FK: joint follows control
            cmds.parentConstraint(str(ctrl), str(jnt), mo=True)

            controls.append(ctrl)
            parent_ctrl = str(ctrl)

        # 3. Skin Mesh
        try:
            cmds.skinCluster(joints, rig.mesh, toSelectedBones=True)
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
            cmds.select(joints[:-1])
            cmds.joint(e=True, oj="xyz", sao="yup", ch=False)
            cmds.select(joints[-1])
            cmds.joint(e=True, oj="none", ch=False)

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
        cmds.setAttr(f"{ik_handle}.visibility", False)

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
            cmds.skinCluster(joints, rig.mesh, toSelectedBones=True)
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

        start_pos = om.MVector(centerline[0])
        end_pos = om.MVector(centerline[-1])

        radius = kwargs.get("radius", 1.0)
        enable_stretch = kwargs.get("enable_stretch", True)

        # Calculate tube direction for control orientation
        tube_dir = (end_pos - start_pos).normal()

        # Build rotation matrix: X-axis = tube direction
        world_up = om.MVector(0, 1, 0)
        # OM2 MVector dot product is the * operator (returns float).
        if abs(tube_dir * world_up) > 0.99:
            world_up = om.MVector(0, 0, 1)
        # OM2 MVector cross product: ^ operator (returns MVector).
        z_axis = (tube_dir ^ world_up).normal()
        y_axis = (z_axis ^ tube_dir).normal()

        rot_matrix = om.MMatrix(
            [
                [tube_dir.x, tube_dir.y, tube_dir.z, 0],
                [y_axis.x, y_axis.y, y_axis.z, 0],
                [z_axis.x, z_axis.y, z_axis.z, 0],
                [0, 0, 0, 1],
            ]
        )
        # OM2 MTransformationMatrix exposes rotation() returning MEulerRotation.
        start_rot = om.MTransformationMatrix(rot_matrix).rotation()

        end_rot_matrix = om.MMatrix(
            [
                [-tube_dir.x, -tube_dir.y, -tube_dir.z, 0],
                [y_axis.x, y_axis.y, y_axis.z, 0],
                [-z_axis.x, -z_axis.y, -z_axis.z, 0],
                [0, 0, 0, 1],
            ]
        )
        end_rot = om.MTransformationMatrix(end_rot_matrix).rotation()

        # Create Controls (oriented along tube axis). NB: ``Controls.create``
        # exposes ``size=`` for the uniform scale; ``scale=`` is silently
        # absorbed by the preset builder's ``**_`` kwargs and does nothing.
        start_nodes = Controls.box(
            name=f"{rig.rig_name}_start",
            size=radius * 4,
            color=(0, 1, 1),
            return_nodes=True,
        )
        rig_grp = str(rig.rig_group)
        rot_xyz = (
            math.degrees(start_rot.x),
            math.degrees(start_rot.y),
            math.degrees(start_rot.z),
        )
        target = str(start_nodes.group) if start_nodes.group else str(start_nodes.control)
        cmds.xform(
            target,
            ws=True,
            t=(start_pos.x, start_pos.y, start_pos.z),
            ro=rot_xyz,
        )
        try:
            cmds.parent(target, rig_grp)
        except RuntimeError:
            pass
        start_ctrl = start_nodes.control

        end_nodes = Controls.box(
            name=f"{rig.rig_name}_end",
            size=radius * 4,
            color=(0, 1, 1),
            return_nodes=True,
        )
        end_rot_xyz = (
            math.degrees(end_rot.x),
            math.degrees(end_rot.y),
            math.degrees(end_rot.z),
        )
        end_target = str(end_nodes.group) if end_nodes.group else str(end_nodes.control)
        cmds.xform(
            end_target,
            ws=True,
            t=(end_pos.x, end_pos.y, end_pos.z),
            ro=end_rot_xyz,
        )
        try:
            cmds.parent(end_target, rig_grp)
        except RuntimeError:
            pass
        end_ctrl = end_nodes.control

        # Create joint group (separate from controls for clean export)
        joint_grp = cmds.group(empty=True, name=f"{rig.rig_name}_joints_GRP")
        try:
            cmds.parent(joint_grp, rig_grp)
        except RuntimeError:
            pass

        # Create Joints in their own hierarchy
        cmds.select(clear=True)
        j1 = cmds.createNode("joint", name=f"{rig.rig_name}_start_jnt")
        cmds.xform(j1, ws=True, t=(start_pos.x, start_pos.y, start_pos.z))
        try:
            cmds.parent(j1, joint_grp)
        except RuntimeError:
            pass
        cmds.setAttr(f"{j1}.radius", radius)

        cmds.select(clear=True)
        j2 = cmds.createNode("joint", name=f"{rig.rig_name}_end_jnt")
        cmds.xform(j2, ws=True, t=(end_pos.x, end_pos.y, end_pos.z))
        try:
            cmds.parent(j2, joint_grp)
        except RuntimeError:
            pass
        cmds.setAttr(f"{j2}.radius", radius)

        joints = [j1, j2]

        # Constrain joints to follow control position and rotation
        cmds.pointConstraint(start_ctrl, j1, mo=True)
        cmds.pointConstraint(end_ctrl, j2, mo=True)

        # Orient constraints: joints follow control rotation (allows rotatable tube ends)
        cmds.orientConstraint(start_ctrl, j1, mo=True)
        cmds.orientConstraint(end_ctrl, j2, mo=True)

        # Scale logic: Distance-based stretch (optional)
        if enable_stretch:
            # Use simple distanceBetween node, but compensate for rig scale
            # to avoid double transforms.  Measure distance in the rig's
            # local space via multMatrix.
            start_ctrl_s = str(start_ctrl)
            end_ctrl_s = str(end_ctrl)
            rig_grp_s = str(rig.rig_group)

            start_local_mm = cmds.createNode(
                "multMatrix", name=f"{rig.rig_name}_start_local_MM"
            )
            cmds.connectAttr(
                f"{start_ctrl_s}.worldMatrix[0]",
                f"{start_local_mm}.matrixIn[0]",
                force=True,
            )
            cmds.connectAttr(
                f"{rig_grp_s}.worldInverseMatrix[0]",
                f"{start_local_mm}.matrixIn[1]",
                force=True,
            )

            end_local_mm = cmds.createNode(
                "multMatrix", name=f"{rig.rig_name}_end_local_MM"
            )
            cmds.connectAttr(
                f"{end_ctrl_s}.worldMatrix[0]",
                f"{end_local_mm}.matrixIn[0]",
                force=True,
            )
            cmds.connectAttr(
                f"{rig_grp_s}.worldInverseMatrix[0]",
                f"{end_local_mm}.matrixIn[1]",
                force=True,
            )

            dist_node = cmds.createNode("distanceBetween", name=f"{rig.rig_name}_dist")
            cmds.connectAttr(
                f"{start_local_mm}.matrixSum", f"{dist_node}.inMatrix1", force=True
            )
            cmds.connectAttr(
                f"{end_local_mm}.matrixSum", f"{dist_node}.inMatrix2", force=True
            )

            initial_dist = (end_pos - start_pos).length()

            norm_md = cmds.createNode("multiplyDivide", name=f"{rig.rig_name}_scale_MD")
            cmds.setAttr(f"{norm_md}.operation", 2)  # Divide
            cmds.connectAttr(f"{dist_node}.distance", f"{norm_md}.input1X", force=True)
            cmds.setAttr(f"{norm_md}.input2X", initial_dist)

            # Start joint scales to stretch toward end
            cmds.connectAttr(f"{norm_md}.outputX", f"{j1}.scaleX", force=True)

        # Smooth Skin the mesh to the joints
        try:
            cmds.skinCluster(joints, rig.mesh, toSelectedBones=True)
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
        mesh = cmds.ls(selection=True, )
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

    # Class-level back-reference cache. cmds-based code uses plain node-path
    # strings, which can't carry an attached ``.rig`` attribute the way object
    # wrappers did. Keyed by the mesh's Maya UUID — stable across rename and
    # reparent (path strings are not).
    _instances: Dict[str, "TubeRig"] = {}

    def __init__(self, obj, rig_name: str = None, rig_group: str = None):
        self._rig_name = rig_name
        self._rig_group = rig_group  # Only assigned if explicitly passed (else will be handled by property)
        if isinstance(obj, (set, list, tuple)):
            obj = next(iter(obj))
        obj = NodeUtils.get_transform_node(obj)
        if not obj:
            raise ValueError(f"Invalid object: `{obj}` {type(obj)}")
        if isinstance(obj, (set, list, tuple)):
            obj = obj[0]
        self.mesh = str(obj)
        self._mesh_uuid = TubeRig._uuid(self.mesh)
        if self._mesh_uuid:
            TubeRig._instances[self._mesh_uuid] = self
        self.joints = None
        self.ik_handle = None
        self.pole_vector = None
        self.skin_cluster = None
        self.start_loc = None
        self.end_loc = None
        self.bundle = None

    @staticmethod
    def _uuid(node) -> Optional[str]:
        """Return the Maya UUID for *node*, or None if it doesn't exist."""
        if node is None:
            return None
        s = str(node)
        if not s or not cmds.objExists(s):
            return None
        res = cmds.ls(s, uuid=True) or []
        return res[0] if res else None

    @classmethod
    def for_mesh(cls, mesh) -> Optional["TubeRig"]:
        """Look up an existing TubeRig instance bound to *mesh*, or return None.

        Resolves *mesh* to a transform node, then to its UUID, before doing the
        cache lookup — so callers can pass a shape, a stale path, or the mesh
        transform interchangeably.
        """
        if mesh is None:
            return None
        target = NodeUtils.get_transform_node(mesh) or mesh
        uuid = cls._uuid(target)
        if not uuid:
            return None
        rig = cls._instances.get(uuid)
        if rig is None:
            return None
        # Refresh the stored path if it went stale (mesh was renamed / reparented).
        if not cmds.objExists(rig.mesh):
            refreshed = cmds.ls(uuid, long=True) or []
            if refreshed:
                rig.mesh = refreshed[0]
            else:
                cls._instances.pop(uuid, None)
                return None
        return rig

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
    def rig_group(self) -> str:
        if not self._rig_group:
            rig_name = f"{self.rig_name}_GRP"
            if cmds.objExists(rig_name):
                self.logger.info(f"Found rig group: {rig_name}")
                self._rig_group = cmds.ls(rig_name)[0]
            else:
                self.logger.info(f"Creating rig group: {rig_name}")
                self._rig_group = cmds.group(empty=True, name=rig_name)
                cmds.makeIdentity(self._rig_group, apply=True, t=1, r=1, s=1, n=0)
                self.logger.debug(
                    f"Created/Found rig group: "
                    f"{str(self._rig_group).split('|')[-1].split(':')[-1]}"
                )
        return NodeUtils.get_transform_node(self._rig_group)

    @rig_group.setter
    def rig_group(self, new_group: "object"):
        """Allows setting a custom rig group."""
        if new_group and cmds.objExists(str(new_group)) and cmds.objectType(
            str(new_group), isAType="transform"
        ):
            self._rig_group = new_group
            self.logger.debug(f"Rig group set to: {self._rig_group}")
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
    ) -> List[str]:
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
            joint_positions = ptk.Polyline.resample(
                centerline, num_joints, reverse
            )
        joints = []
        parent_joint = None

        for i, pos in enumerate(joint_positions):
            self.logger.debug(
                f"Generating joint {i+1}, position: {pos}, radius: {radius}, orientation: {orientation}"
            )
            # Always clear selection before joint creation to avoid Maya's implicit parenting
            cmds.select(clear=True)
            jnt = cmds.createNode(
                "joint",
                name=f"{self.rig_name}_jnt_{i+1}",
            )
            # ``pos`` may be ``om.MPoint``; cmds.xform's ``t=`` flag wants
            # a 3-tuple of plain floats.
            t_xyz = (float(pos[0]), float(pos[1]), float(pos[2]))
            cmds.xform(jnt, ws=True, t=t_xyz)
            cmds.setAttr(f"{jnt}.radius", radius)
            # Orientation (if needed)
            if orientation:
                cmds.setAttr(f"{jnt}.jointOrient", *orientation, type="double3")
            # Parent
            if i == 0:
                cmds.parent(jnt, str(self.rig_group))
            else:
                cmds.parent(jnt, parent_joint)
            parent_joint = jnt
            joints.append(jnt)

        self.logger.debug(f"Generated joints: {[str(jnt).split('|')[-1] for jnt in joints]}")
        self.joints = joints
        return joints

    # ------------------------------------------------------------------
    # Curves & IK
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_logic_curve(
        self, centerline: List[List[float]]
    ) -> str:
        """Creates the logic curve for Spline IK."""
        degree = 3 if len(centerline) >= 4 else 1
        curve_name = f"{self.rig_name}_ik_curve"
        # ``centerline`` may contain ``om.MPoint`` instances; cmds.curve
        # wants flat (x, y, z) tuples.
        cv_points = [
            (float(p[0]), float(p[1]), float(p[2])) for p in centerline
        ]
        curve = cmds.curve(p=cv_points, d=degree, name=curve_name)
        curve = cmds.parent(curve, str(self.rig_group))[0]
        cmds.setAttr(f"{curve}.inheritsTransform", False)  # Prevent double transform
        cmds.setAttr(f"{curve}.visibility", False)
        return curve

    # ------------------------------------------------------------------
    # Spline IK Driver System (controls, tangents, up locators)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_spline_drivers(
        self, centerline: List[List[float]], radius: float = 1.0, num_controls: int = 3
    ) -> Tuple[List[str], List[str], List]:
        """Creates the driver system (controls and joints) for the Spline IK curve."""
        start_pos = om.MVector(centerline[0])
        end_pos = om.MVector(centerline[-1])
        tube_length = (end_pos - start_pos).length()

        # Calculate orientation frames
        tube_dir = (end_pos - start_pos).normal()
        world_up = om.MVector(0, 1, 0)
        if abs(tube_dir * world_up) > 0.99:
            world_up = om.MVector(0, 0, 1)
        # OM2 MVector cross product: ^ operator (returns MVector).
        z_axis = (tube_dir ^ world_up).normal()
        y_axis = (z_axis ^ tube_dir).normal()

        # Rotation matrices
        rot_matrix = om.MMatrix(
            [
                [tube_dir.x, tube_dir.y, tube_dir.z, 0],
                [y_axis.x, y_axis.y, y_axis.z, 0],
                [z_axis.x, z_axis.y, z_axis.z, 0],
                [0, 0, 0, 1],
            ]
        )
        # OM2 MTransformationMatrix exposes rotation() returning MEulerRotation.
        start_rot = om.MTransformationMatrix(rot_matrix).rotation()

        end_rot_matrix = om.MMatrix(
            [
                [-tube_dir.x, -tube_dir.y, -tube_dir.z, 0],
                [y_axis.x, y_axis.y, y_axis.z, 0],
                [-z_axis.x, -z_axis.y, -z_axis.z, 0],
                [0, 0, 0, 1],
            ]
        )
        end_rot = om.MTransformationMatrix(end_rot_matrix).rotation()

        rig_grp = str(self.rig_group)

        def _euler_to_deg(euler):
            return (
                math.degrees(euler.x),
                math.degrees(euler.y),
                math.degrees(euler.z),
            )

        def _create_ctrl(
            name, pos, rot=None, scale=1.0, color=(1, 1, 0), shape="box", parent=None
        ):
            # ``Controls.create`` accepts ``size=`` for the uniform scale.
            # ``scale=``/``radius=`` are absorbed by preset-builder ``**_`` and silently no-op.
            if shape == "sphere":
                nodes = Controls.sphere(
                    name=name, size=scale, color=color, return_nodes=True
                )
            else:
                nodes = Controls.box(
                    name=name, size=scale, color=color, return_nodes=True
                )

            grp = nodes.group if nodes.group else nodes.control
            _set_t_ws(grp, (pos[0], pos[1], pos[2]))
            if rot is not None:
                _set_r_ws(grp, _euler_to_deg(rot))

            grp = _parent_to(grp, parent if parent else rig_grp)
            return nodes.control

        driver_grp = cmds.group(empty=True, name=f"{self.rig_name}_driver_GRP")
        driver_grp = _parent_to(driver_grp, rig_grp)
        cmds.setAttr(f"{driver_grp}.visibility", False)

        controls: List[str] = []
        driver_joints: List[str] = []

        if num_controls == 3:
            # ------------------------------------------------------------------
            # Standard 3-Point System (Start, Mid, End + Tangents)
            # ------------------------------------------------------------------
            mid_pos = (start_pos + end_pos) / 2
            start_ctrl = _create_ctrl(
                f"{self.rig_name}_start", start_pos, start_rot, radius * 3
            )
            mid_ctrl = _create_ctrl(
                f"{self.rig_name}_mid", mid_pos, None, radius * 2.5
            )
            end_ctrl = _create_ctrl(
                f"{self.rig_name}_end", end_pos, end_rot, radius * 3
            )

            controls = [start_ctrl, mid_ctrl, end_ctrl]

            # Tangent Controls
            tan_offset = tube_length * 0.2

            # OM2 MMatrix indexes via .getElement(row, col); row 0 is the X axis.
            start_tan_pos = (
                start_pos
                + om.MVector(
                    rot_matrix.getElement(0, 0),
                    rot_matrix.getElement(0, 1),
                    rot_matrix.getElement(0, 2),
                )
                * tan_offset
            )
            start_tan_ctrl = _create_ctrl(
                f"{self.rig_name}_start_tan",
                start_tan_pos,
                rot=start_rot,
                scale=radius * 0.5,
                color=(1, 0.5, 0),
                shape="sphere",
                parent=start_ctrl,
            )

            end_tan_pos = (
                end_pos
                + om.MVector(
                    end_rot_matrix.getElement(0, 0),
                    end_rot_matrix.getElement(0, 1),
                    end_rot_matrix.getElement(0, 2),
                )
                * tan_offset
            )
            end_tan_ctrl = _create_ctrl(
                f"{self.rig_name}_end_tan",
                end_tan_pos,
                rot=end_rot,
                scale=radius * 0.5,
                color=(1, 0.5, 0),
                shape="sphere",
                parent=end_ctrl,
            )

            driver_sources = [
                (start_ctrl, "start"),
                (start_tan_ctrl, "start_tan"),
                (mid_ctrl, "mid"),
                (end_tan_ctrl, "end_tan"),
                (end_ctrl, "end"),
            ]

            for source, suffix in driver_sources:
                cmds.select(clear=True)
                jnt = cmds.createNode(
                    "joint", name=f"{self.rig_name}_driver_{suffix}_jnt"
                )
                _set_t_ws(jnt, _xform_t_ws(source))
                jnt = _parent_to(jnt, driver_grp)
                cmds.setAttr(f"{jnt}.radius", radius * 1.5)
                cmds.parentConstraint(str(source), jnt, mo=True)
                driver_joints.append(jnt)

        else:
            # ------------------------------------------------------------------
            # Distributed N-Point System
            # ------------------------------------------------------------------
            positions = ptk.Polyline.resample(centerline, num_controls)

            for i, pos in enumerate(positions):
                name = f"{self.rig_name}_ctrl_{i+1}"

                rot = None
                if i == 0:
                    rot = start_rot
                elif i == len(positions) - 1:
                    rot = end_rot

                ctrl = _create_ctrl(
                    name,
                    pos,
                    rot=rot,
                    scale=radius * 2.5,
                    color=(1, 1, 0),
                    shape="box",
                )
                controls.append(ctrl)

                cmds.select(clear=True)
                jnt = cmds.createNode(
                    "joint", name=f"{self.rig_name}_driver_{i+1}_jnt"
                )
                _set_t_ws(jnt, _xform_t_ws(ctrl))
                jnt = _parent_to(jnt, driver_grp)
                cmds.setAttr(f"{jnt}.radius", radius * 1.5)
                cmds.parentConstraint(str(ctrl), jnt, mo=True)
                driver_joints.append(jnt)

        # Up Locators (Start/End Twist Anchors)
        up_offset = tube_length * 0.1

        start_up_loc = cmds.spaceLocator(name=f"{self.rig_name}_start_up_loc")[0]
        start_up_loc = _parent_to(start_up_loc, driver_grp)
        s_pos = _xform_t_ws(controls[0])
        _set_t_ws(start_up_loc, (s_pos[0], s_pos[1] + up_offset, s_pos[2]))
        cmds.pointConstraint(controls[0], start_up_loc, mo=True)
        cmds.setAttr(f"{start_up_loc}.visibility", False)

        end_up_loc = cmds.spaceLocator(name=f"{self.rig_name}_end_up_loc")[0]
        end_up_loc = _parent_to(end_up_loc, driver_grp)
        e_pos = _xform_t_ws(controls[-1])
        _set_t_ws(end_up_loc, (e_pos[0], e_pos[1] + up_offset, e_pos[2]))
        cmds.pointConstraint(controls[-1], end_up_loc, mo=True)
        cmds.setAttr(f"{end_up_loc}.visibility", False)

        return (controls, driver_joints, [start_up_loc, end_up_loc])

    @CoreUtils.undoable
    def skin_curve_to_drivers(self, curve, driver_joints):
        try:
            cmds.skinCluster(driver_joints, curve, toSelectedBones=True)
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
        ik_handle = str(ik_handle)
        start_ctrl = str(start_ctrl)
        end_ctrl = str(end_ctrl)

        cmds.setAttr(f"{ik_handle}.dTwistControlEnable", True)

        if start_up_loc and end_up_loc:
            # Object Rotation Up (Start/End) — more stable than control matrices
            # when controls translate.
            cmds.setAttr(f"{ik_handle}.dWorldUpType", 4)
            cmds.setAttr(f"{ik_handle}.dWorldUpAxis", 0)  # Positive Y
            cmds.setAttr(f"{ik_handle}.dWorldUpVectorY", 1)
            cmds.setAttr(f"{ik_handle}.dWorldUpVectorEndY", 1)
            cmds.connectAttr(
                f"{str(start_up_loc)}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrix",
                force=True,
            )
            cmds.connectAttr(
                f"{str(end_up_loc)}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrixEnd",
                force=True,
            )
        else:
            cmds.setAttr(f"{ik_handle}.dWorldUpType", 4)
            cmds.connectAttr(
                f"{start_ctrl}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrix",
                force=True,
            )
            cmds.connectAttr(
                f"{end_ctrl}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrixEnd",
                force=True,
            )

        if not cmds.attributeQuery("roll", node=end_ctrl, exists=True):
            cmds.addAttr(end_ctrl, ln="roll", at="double", k=True)
        cmds.connectAttr(f"{end_ctrl}.roll", f"{ik_handle}.roll", force=True)

    @CoreUtils.undoable
    def setup_auto_bend(self, start_ctrl, mid_ctrl, end_ctrl):
        """Setup automatic bending of the mid control based on compression distance."""
        start_ctrl = str(start_ctrl)
        mid_ctrl = str(mid_ctrl)
        end_ctrl = str(end_ctrl)
        rig_grp = str(self.rig_group)

        if not cmds.attributeQuery("autoBend", node=start_ctrl, exists=True):
            cmds.addAttr(
                start_ctrl, ln="autoBend", at="double", min=0, max=5, dv=0.0, k=True
            )

        # Identify the mid control's offset group (parented to rig_group).
        offset_grp = NodeUtils.get_parent(mid_ctrl, type=None, full_path=True)
        if not offset_grp or short_name(offset_grp) == short_name(rig_grp):
            offset_grp = mid_ctrl

        auto_bend_grp = cmds.group(empty=True, name=f"{self.rig_name}_mid_autoBend_GRP")

        # Match transform of the offset group (which is at mid position)
        cmds.delete(cmds.parentConstraint(offset_grp, auto_bend_grp))

        # Insert into hierarchy: RigGroup -> AutoBend -> Offset -> Control
        current_parent = NodeUtils.get_parent(offset_grp, type=None, full_path=True)
        if current_parent:
            auto_bend_grp = _parent_to(auto_bend_grp, current_parent)
        offset_grp = _parent_to(offset_grp, auto_bend_grp)

        # Logic: (Initial_Length - Current_Dist) * autoBend -> translateY
        dist_node = cmds.createNode("distanceBetween", name=f"{self.rig_name}_ab_dist")
        cmds.connectAttr(f"{start_ctrl}.worldMatrix[0]", f"{dist_node}.inMatrix1", force=True)
        cmds.connectAttr(f"{end_ctrl}.worldMatrix[0]", f"{dist_node}.inMatrix2", force=True)

        start_pos = om.MVector(*_xform_t_ws(start_ctrl))
        end_pos = om.MVector(*_xform_t_ws(end_ctrl))
        initial_length = (end_pos - start_pos).length()

        # Calculate compression: initial_length - current_dist
        pma = cmds.createNode("plusMinusAverage", name=f"{self.rig_name}_ab_sub")
        cmds.setAttr(f"{pma}.operation", 2)  # Subtract
        cmds.setAttr(f"{pma}.input1D[0]", initial_length)
        cmds.connectAttr(f"{dist_node}.distance", f"{pma}.input1D[1]", force=True)

        # Clamp min 0 (ignore stretching, only bend on compression)
        clamp = cmds.createNode("clamp", name=f"{self.rig_name}_ab_clamp")
        cmds.setAttr(f"{clamp}.minR", 0)
        cmds.setAttr(f"{clamp}.maxR", 10000)
        cmds.connectAttr(f"{pma}.output1D", f"{clamp}.inputR", force=True)

        # Multiply by autoBend factor
        md = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_ab_mult")
        cmds.connectAttr(f"{clamp}.outputR", f"{md}.input1X", force=True)
        cmds.connectAttr(f"{start_ctrl}.autoBend", f"{md}.input2X", force=True)

        # Apply to Y translation of the auto_bend_grp (Y-up is standard for this rig).
        cmds.connectAttr(f"{md}.outputX", f"{auto_bend_grp}.translateY", force=True)

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
        curve = str(curve)
        rig_grp = str(self.rig_group)
        main_control = str(main_control) if main_control else None

        curve_shape = NodeUtils.get_shape(curve)
        if not curve_shape:
            self.logger.warning(f"setup_spline_stretch: no shape under {curve}")
            return

        curve_info = cmds.createNode("curveInfo", name=f"{self.rig_name}_curveInfo")
        cmds.connectAttr(
            f"{curve_shape}.worldSpace[0]", f"{curve_info}.inputCurve", force=True
        )
        initial_length = cmds.getAttr(f"{curve_info}.arcLength")

        scale_comp_md = cmds.createNode(
            "multiplyDivide", name=f"{self.rig_name}_scale_comp_MD"
        )
        cmds.setAttr(f"{scale_comp_md}.operation", 2)  # Divide
        cmds.connectAttr(
            f"{curve_info}.arcLength", f"{scale_comp_md}.input1X", force=True
        )
        cmds.connectAttr(
            f"{rig_grp}.scaleX", f"{scale_comp_md}.input2X", force=True
        )

        norm_md = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_norm_MD")
        cmds.setAttr(f"{norm_md}.operation", 2)
        cmds.connectAttr(
            f"{scale_comp_md}.outputX", f"{norm_md}.input1X", force=True
        )
        cmds.setAttr(f"{norm_md}.input2X", initial_length)

        # Clamp logic for separate stretch/squash control
        min_limit = 0.001 if enable_squash else 1.0
        max_limit = 10000.0 if enable_stretch else 1.0

        scale_val_src = f"{norm_md}.outputX"
        if not enable_squash or not enable_stretch:
            clamp_node = cmds.createNode("clamp", name=f"{self.rig_name}_scale_clamp")
            cmds.setAttr(f"{clamp_node}.minR", min_limit)
            cmds.setAttr(f"{clamp_node}.maxR", max_limit)
            cmds.connectAttr(f"{norm_md}.outputX", f"{clamp_node}.inputR", force=True)
            scale_val_src = f"{clamp_node}.outputR"

        # ----------------------------------------------------------------------
        # Attribute Setup (User Controls)
        # ----------------------------------------------------------------------
        stretch_output = scale_val_src

        if main_control:
            # Add Separator if not present (shared by vol and stretch)
            if not cmds.attributeQuery("separator_opt", node=main_control, exists=True):
                cmds.addAttr(
                    main_control, ln="separator_opt", at="enum", en="____", k=True
                )
                cmds.setAttr(f"{main_control}.separator_opt", lock=True)

            # 1. Stretch blending (Animator toggles stretch effect)
            if enable_stretch or enable_squash:
                if not cmds.attributeQuery("stretchFactor", node=main_control, exists=True):
                    cmds.addAttr(
                        main_control,
                        ln="stretchFactor",
                        at="double",
                        min=0,
                        max=1,
                        dv=1.0,
                        k=True,
                    )

                # Blend between Calculated Stretch (Color1) and 1.0 (Color2)
                blend_stretch = cmds.createNode(
                    "blendColors", name=f"{self.rig_name}_stretch_BLEND"
                )
                cmds.connectAttr(
                    f"{main_control}.stretchFactor",
                    f"{blend_stretch}.blender",
                    force=True,
                )
                cmds.connectAttr(
                    scale_val_src, f"{blend_stretch}.color1R", force=True
                )
                cmds.setAttr(f"{blend_stretch}.color2R", 1.0)

                stretch_output = f"{blend_stretch}.outputR"

        # ----------------------------------------------------------------------
        # Volume Preservation: scaleY = scaleZ = scaleX ^ -0.5
        # ----------------------------------------------------------------------
        vol_output = None

        if enable_volume:
            vol_pow = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_vol_POW")
            cmds.setAttr(f"{vol_pow}.operation", 3)  # Power
            cmds.connectAttr(stretch_output, f"{vol_pow}.input1X", force=True)
            cmds.setAttr(f"{vol_pow}.input2X", -0.5)

            vol_output = f"{vol_pow}.outputX"

            if main_control:
                if not cmds.attributeQuery("volumeFactor", node=main_control, exists=True):
                    cmds.addAttr(
                        main_control,
                        ln="volumeFactor",
                        at="double",
                        min=0,
                        max=2,
                        dv=1.0,
                        k=True,
                    )

                blend_vol = cmds.createNode(
                    "blendColors", name=f"{self.rig_name}_vol_BLEND"
                )
                cmds.connectAttr(
                    f"{main_control}.volumeFactor",
                    f"{blend_vol}.blender",
                    force=True,
                )
                cmds.connectAttr(
                    f"{vol_pow}.outputX", f"{blend_vol}.color1R", force=True
                )
                cmds.setAttr(f"{blend_vol}.color2R", 1.0)

                vol_output = f"{blend_vol}.outputR"

        for jnt in joints:
            jnt = str(jnt)
            cmds.connectAttr(stretch_output, f"{jnt}.scaleX", force=True)
            if enable_volume and vol_output:
                cmds.connectAttr(vol_output, f"{jnt}.scaleY", force=True)
                cmds.connectAttr(vol_output, f"{jnt}.scaleZ", force=True)

    # ------------------------------------------------------------------
    # RP IK / Legacy Controls (Now delegated to RigUtils)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_start_end_locators(
        self,
        joints: List[str],
        ik_handle: Optional[str] = None,
    ) -> Tuple[str, str]:
        joints = cmds.ls(joints, type="joint", flatten=True)
        if len(joints) < 2:
            self.logger.error("Not enough joints to create locators.")
            return None, None

        start_locator = cmds.spaceLocator(name=f"{self.rig_name}_start_LOC")[0]
        end_locator = cmds.spaceLocator(name=f"{self.rig_name}_end_LOC")[0]

        start_position = _xform_t_ws(joints[0])
        end_position = _xform_t_ws(joints[-1])

        _set_t_ws(start_locator, start_position)
        _set_t_ws(end_locator, end_position)

        cmds.makeIdentity(start_locator, apply=True, t=1, r=1, s=1, n=0)
        cmds.makeIdentity(end_locator, apply=True, t=1, r=1, s=1, n=0)

        start_locator = _parent_to(start_locator, self.rig_group)
        end_locator = _parent_to(end_locator, self.rig_group)

        cmds.pointConstraint(start_locator, joints[0], maintainOffset=False)
        if ik_handle:
            cmds.pointConstraint(end_locator, str(ik_handle), maintainOffset=False)

        Attributes.set_lock_state((start_locator, end_locator), rotate=True, scale=True)

        self.start_loc = start_locator
        self.end_loc = end_locator
        return start_locator, end_locator

    @CoreUtils.undoable
    def create_ik(
        self, joints: List[str], **kwargs
    ) -> Optional[str]:
        # Wrapper for RigUtils.create_ik_handle to maintain API compatibility
        joints = cmds.ls(joints, type="joint", flatten=True)
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
        self, ik_handle, mid_joint: str, offset=(0, 5, 0)
    ) -> str:
        # Wrapper for RigUtils.create_pole_vector
        # Note: RigUtils uses 'distance' float while old method used vector offset tuple?
        # Old signature: offset=(0,5,0).
        # RigUtils expects distance.
        # We'll adapt.
        dist = om.MVector(offset).length()
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
        self, obj, joints: List[str]
    ) -> Optional[str]:
        """Binds the joint chain to a polygon tube with smooth skinning."""
        self.logger.debug(f"Tube mesh: {obj} ({type(obj).__name__})")
        objs = list(dict.fromkeys(cmds.ls(obj, objectsOnly=True, flatten=True) or []))
        if not objs:
            self.logger.error(f"Invalid tube mesh: {obj}")
            return None
        first = objs[0]

        transform = NodeUtils.get_transform_node(first)
        if not transform:
            self.logger.error(f"Invalid transform node: {transform}")
            return None
        transform = str(transform)

        if not joints or not isinstance(joints, (list, tuple)):
            self.logger.error(f"Invalid joint list: {joints}")
            return None
        joints = [str(j) for j in joints]
        if not all(cmds.objExists(j) and cmds.objectType(j) == "joint" for j in joints):
            self.logger.error(f"Invalid joint list: {joints}")
            return None

        # Unlock TRS so the skinCluster bind doesn't fail on locked plugs.
        for attr in ("translate", "rotate", "scale"):
            for axis in "XYZ":
                plug = f"{transform}.{attr}{axis}"
                try:
                    cmds.setAttr(plug, lock=False)
                except Exception:
                    pass

        rig_group = str(self.rig_group)
        tube_parent = NodeUtils.get_parent(transform, type=None, full_path=True)
        if tube_parent:
            rig_group = _parent_to(rig_group, tube_parent)
            transform = _parent_to(transform, None)  # unparent to world

        for j in joints:
            if not NodeUtils.get_parent(j, type=None, full_path=True):
                _parent_to(j, rig_group)

        self.logger.debug(
            f"Creating skinCluster with {len(joints)} joints on {leaf_name(transform)}"
        )

        try:
            skin_cluster_result = cmds.skinCluster(
                joints,
                transform,
                toSelectedBones=True,
                maximumInfluences=4,
                weightDistribution=0.5,
            )
            skin_cluster = (
                skin_cluster_result[0]
                if isinstance(skin_cluster_result, (list, tuple))
                else skin_cluster_result
            )
            self.logger.debug(f"SkinCluster created: {skin_cluster}")
        except Exception as e:
            self.logger.error(f"Error creating skinCluster: {e}")
            return None

        self.skin_cluster = skin_cluster
        return skin_cluster

    # ------------------------------------------------------------------
    # Anchor Constraints
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def constrain_end_with_falloff(
        self,
        joints: "List[str]",
        anchor: str,
        falloff: float = 5.0,
        joint_index: int = -1,
    ) -> "Optional[str]":
        """
        Constrains a joint in the chain to an anchor and applies distance-based skin weight falloff.

        Parameters:
            joints (List[str]): The hose joint chain.
            anchor (str): The transform the joint should follow.
            falloff (float): World-space distance over which anchor weight fades.
            joint_index (int): Index of the joint to constrain. Use 0 for start, -1 for end.

        Returns:
            str: The newly created anchor joint.
        """
        if not joints:
            self.logger.error("No joints provided.")
            return None

        constrained_joint = str(joints[joint_index])
        anchor = str(anchor)
        anchor_pos = _xform_t_ws(anchor)
        anchor_pos_v = om.MVector(*anchor_pos)

        # Create anchor joint at anchor location
        joint_name = Naming.generate_unique_name(f"{self.rig_name}_anchor_jnt")
        cmds.select(clear=True)
        anchor_joint = cmds.createNode("joint", name=joint_name)
        cmds.setAttr(
            f"{anchor_joint}.translate",
            anchor_pos[0], anchor_pos[1], anchor_pos[2],
            type="double3",
        )
        cmds.setAttr(
            f"{anchor_joint}.radius",
            cmds.getAttr(f"{constrained_joint}.radius"),
        )
        cmds.makeIdentity(anchor_joint, apply=True, t=True, r=True, s=True)
        cmds.xform(anchor_joint, ws=True, t=anchor_pos)

        # Fully constrain anchor_joint to the anchor geo (position + orientation)
        cmds.parentConstraint(anchor, anchor_joint, mo=False)

        # Avoid driving the joint directly if it's IK-controlled.
        is_end_joint = False
        if self.ik_handle:
            ik_handle_s = str(self.ik_handle)
            ee = cmds.ikHandle(ik_handle_s, q=True, ee=True)
            if ee:
                ee_parents = cmds.listRelatives(ee, parent=True, fullPath=True) or []
                end_jnt = ee_parents[0] if ee_parents else ee
                is_end_joint = (
                    end_jnt == constrained_joint
                    or short_name(end_jnt) == short_name(constrained_joint)
                )
        if is_end_joint:
            cmds.parentConstraint(anchor_joint, str(self.ik_handle), mo=False)
        else:
            cmds.parentConstraint(anchor_joint, constrained_joint, mo=False)

        # Add falloff skin weighting from anchor_joint to constrained_joint
        if self.skin_cluster:
            skin_cluster = str(self.skin_cluster)
            influences = (
                cmds.skinCluster(skin_cluster, q=True, influence=True) or []
            )
            inf_short = {short_name(i) for i in influences}
            if short_name(anchor_joint) not in inf_short:
                cmds.skinCluster(
                    skin_cluster,
                    edit=True,
                    addInfluence=anchor_joint,
                    weight=0.0,
                )

            try:
                mesh_shape = NodeUtils.get_shape(self.mesh)
                if not mesh_shape:
                    raise RuntimeError(f"No shape under mesh {self.mesh}")
                vert_count = cmds.polyEvaluate(mesh_shape, vertex=True) or 0
                for i in range(vert_count):
                    v = f"{mesh_shape}.vtx[{i}]"
                    pos = cmds.pointPosition(v, world=True)
                    pos_v = om.MVector(pos[0], pos[1], pos[2])
                    d = (pos_v - anchor_pos_v).length()
                    if d > falloff:
                        continue
                    w = max(min(1.0 - (d / falloff), 1.0), 0.0)
                    cmds.skinPercent(
                        skin_cluster,
                        v,
                        transformValue=[
                            (anchor_joint, w),
                            (constrained_joint, 1.0 - w),
                        ],
                    )
                self.logger.debug(
                    f"Applied falloff weights from {anchor_joint} "
                    f"(joint index {joint_index}) over distance {falloff}"
                )
            except Exception as e:
                self.logger.warning(f"Skin weighting failed: {e}")

        # Ensure anchor joint is parented under the rig group
        rig_grp = str(self.rig_group)
        current_parent = NodeUtils.get_parent(anchor_joint, type=None, full_path=True)
        if not current_parent or short_name(current_parent) != short_name(rig_grp):
            anchor_joint = _parent_to(anchor_joint, rig_grp)

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

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Tube Rig",
                body="Generate joint rigs along tube-shaped meshes. The tool "
                "auto-detects the tube's centerline via edge loops or surface "
                "normals.",
                steps=[
                    "Select a tube mesh.",
                    "Pick a <b>Mode</b> preset — each mode preconfigures joint "
                    "count, controls, and constraint topology.",
                    "Adjust <b>Joints</b> (or set to <i>Auto</i> to derive from "
                    "edge loops) and the mode-specific parameters.",
                    "Press <b>Build</b>.",
                ],
                notes=[
                    "<b>Joints = Auto</b> reads the tube's longitudinal edge "
                    "loops and places one joint per loop.",
                ],
            )
        )

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
        if obj is None:
            return None
        obj_s = str(obj)
        # Resolve shapes / components down to their transform so the cache key
        # space stays consistent.
        target = NodeUtils.get_transform_node(obj_s) or obj_s

        # 1. Direct UUID-based lookup.
        rig = TubeRig.for_mesh(target)
        if rig is not None:
            return rig

        # 2. Walk ancestors (e.g. user clicked a joint or a child group).
        if cmds.objExists(target):
            parent = NodeUtils.get_parent(target, type=None, full_path=True)
            while parent:
                rig = TubeRig.for_mesh(parent)
                if rig is not None:
                    return rig
                parent = NodeUtils.get_parent(parent, type=None, full_path=True)

        # 3. Fallback: instantiate a new TubeRig on the resolved transform.
        rig_name = self.ui.txt000.text() or f"{short_name(target)}_RIG"
        return TubeRig(target, rig_name=rig_name)

    def create_joints_from_tube(self, obj):
        """Creates a joint chain from a tube mesh."""
        num_joints = self.ui.s000.value()
        edges = cmds.filterExpand(selectionMask=32)  # optional user edge selection

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
        # Order the joints by hierarchy
        joints = cmds.ls(joints, type="joint", flatten=True)
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
            obj, *_ = cmds.ls(selection=True, objectsOnly=True, flatten=True)
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
            obj, *_ = cmds.ls(selection=True, objectsOnly=True, flatten=True)
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
            sel = cmds.ls(selection=True, flatten=True)
            joints = cmds.ls(sel, type="joint")
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
            centerline = [_xform_t_ws(j) for j in joints]
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
            cmds.setAttr(f"{ik_handle}.visibility", False)

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

            ctrl_names = ", ".join(leaf_name(c) for c in controls)
            self.sb.message_box(
                f"Spline IK Rig created on {len(joints)} joints.\nControls: {ctrl_names}"
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

    @CoreUtils.undoable
    def b003(self):
        """Macros: Bind Joint Chain to Tube."""
        try:
            *joints, obj = cmds.ls(selection=True, flatten=True)
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

        # Order the joints by hierarchy
        joints = cmds.ls(joints, type="joint", flatten=True)
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
        sel = cmds.ls(selection=True, flatten=True)
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
            "Both ends constrained:\n"
            f"  Start: {leaf_name(start_result) if start_result else 'failed'}\n"
            f"  End: {leaf_name(end_result) if end_result else 'failed'}"
        )

    # -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("tube_rig", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
