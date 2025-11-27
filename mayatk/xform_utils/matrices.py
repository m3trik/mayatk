# !/usr/bin/python
# coding=utf-8
"""Matrix utilities for Maya rigging and animation.

This module provides clean, focused helpers for working with matrices in Maya,
including SRT composition/decomposition, space transformations, and node graph
construction patterns for modern matrix-based rigs.

Key concepts:
    - World vs Local spaces: child_world = child_local * parent_world
    - offsetParentMatrix: Drive transforms without buffer groups
    - Right-to-left multiplication: A * B means "B then A"
    - Points translate, vectors don't: Use appropriate matrix components

References:
    https://www.riggingdojo.com/2025/07/17/mastering-matrices-for-3d-animation-and-rigging/
"""

from __future__ import annotations
import math
from typing import Iterable, Optional, Tuple, List, Union, TYPE_CHECKING

if TYPE_CHECKING:
    import pymel.core as pm
    from maya.api.OpenMaya import (
        MMatrix,
        MTransformationMatrix,
        MVector,
        MQuaternion,
        MSpace,
        MEulerRotation,
    )
else:
    try:
        import pymel.core as pm
    except ImportError as error:
        print(__file__, error)
        pm = None

    try:
        from maya.api.OpenMaya import (
            MMatrix,
            MTransformationMatrix,
            MVector,
            MQuaternion,
            MSpace,
            MEulerRotation,
        )
    except ImportError as error:
        print(__file__, error)
        MMatrix = MTransformationMatrix = MVector = MQuaternion = None
        MSpace = MEulerRotation = None

import pythontk as ptk

# Space constants - fall back to 0 if Maya API unavailable
SPACE_OBJECT = MSpace.kObject if MSpace is not None else 0
SPACE_WORLD = MSpace.kWorld if MSpace is not None else 0


def _quat_to_euler_xyz_deg(quat: "MQuaternion") -> Tuple[float, float, float]:
    """Convert an MQuaternion into XYZ Euler angles in degrees without PyMEL."""

    if quat is None:
        return 0.0, 0.0, 0.0

    x = quat.x
    y = quat.y
    z = quat.z
    w = quat.w

    # Roll (X-axis rotation)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    # Pitch (Y-axis rotation)
    sinp = 2.0 * (w * y - z * x)
    if abs(sinp) >= 1.0:
        pitch = math.copysign(math.pi / 2.0, sinp)
    else:
        pitch = math.asin(sinp)

    # Yaw (Z-axis rotation)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return (
        math.degrees(roll),
        math.degrees(pitch),
        math.degrees(yaw),
    )


# --------------------------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------------------------


class MatricesError(RuntimeError):
    """Base exception for matrix utility operations."""

    pass


# --------------------------------------------------------------------------------------------
# Internal Helper Classes
# --------------------------------------------------------------------------------------------


class _MatrixMath:
    """Pure math operations using Maya API (no nodes created)."""

    @staticmethod
    def identity() -> "MMatrix":
        """Return a 4x4 identity matrix.

        Returns:
            Identity MMatrix.

        Example:
            >>> mx = Matrices.identity()
        """
        return MMatrix()

    @staticmethod
    def to_mmatrix(
        matrix_like: Union["pm.nt.Transform", "pm.datatypes.Matrix", "MMatrix", list],
    ) -> "MMatrix":
        """Convert various matrix representations to MMatrix.

        Parameters:
            matrix_like: Transform node, PyMEL Matrix, MMatrix, or 16-element list.

        Returns:
            MMatrix representation.

        Example:
            >>> node = pm.PyNode("pCube1")
            >>> world_mx = Matrices.to_mmatrix(node)
            >>> # Or from PyMEL matrix:
            >>> pm_mx = node.worldMatrix.get()
            >>> api_mx = Matrices.to_mmatrix(pm_mx)
        """
        # Already an MMatrix
        if isinstance(matrix_like, MMatrix):
            return matrix_like
        # PyMEL Matrix
        if hasattr(matrix_like, "__melobject__"):
            return MMatrix(matrix_like)
        # Transform node - get world matrix
        if hasattr(matrix_like, "worldMatrix"):
            return MMatrix(matrix_like.worldMatrix.get())
        # List/tuple of 16 values
        if hasattr(matrix_like, "__len__") and len(matrix_like) == 16:
            return MMatrix(matrix_like)
        raise TypeError(
            f"Cannot convert {type(matrix_like).__name__} to MMatrix. "
            "Expected Transform, pm.datatypes.Matrix, MMatrix, or 16-element list."
        )

    @staticmethod
    def local_matrix(node: "pm.nt.Transform") -> "MMatrix":
        """Get a transform's local matrix as MMatrix.

        Parameters:
            node: PyMEL transform node.

        Returns:
            Local matrix as MMatrix.

        Example:
            >>> node = pm.PyNode("pCube1")
            >>> local_mx = Matrices.local_matrix(node)
        """
        return MMatrix(node.matrix.get())

    @staticmethod
    def from_srt(
        translate: Iterable[float] = (0.0, 0.0, 0.0),
        rotate_euler_deg: Iterable[float] = (0.0, 0.0, 0.0),
        scale: Iterable[float] = (1.0, 1.0, 1.0),
        rotate_order: str = "xyz",
    ) -> "MMatrix":
        """Compose an MMatrix from separate scale, rotation, and translation components.

        Parameters:
            translate: Translation values (x, y, z).
            rotate_euler_deg: Rotation in degrees (x, y, z).
            scale: Scale values (x, y, z).
            rotate_order: Rotation order ("xyz", "yzx", "zxy", "xzy", "yxz", "zyx").

        Returns:
            Composed MMatrix.

        Example:
            >>> mx = Matrices.from_srt(
            ...     translate=(5.0, 0.0, 0.0),
            ...     rotate_euler_deg=(0.0, 45.0, 0.0),
            ...     scale=(1.0, 1.0, 1.0)
            ... )
        """
        t = MTransformationMatrix()
        t.setTranslation(MVector(*translate), SPACE_OBJECT)

        # Convert euler to quaternion for rotation
        euler = pm.datatypes.EulerRotation(
            pm.util.degreesToRadians(rotate_euler_deg[0]),
            pm.util.degreesToRadians(rotate_euler_deg[1]),
            pm.util.degreesToRadians(rotate_euler_deg[2]),
            rotate_order.lower(),
        )
        t.setRotation(euler.asQuaternion())
        t.setScale(scale, SPACE_OBJECT)

        return t.asMatrix()

    @staticmethod
    def decompose(
        m: "MMatrix",
        rotate_order: str = "xyz",
    ) -> Tuple[
        Tuple[float, float, float],
        Tuple[float, float, float],
        Tuple[float, float, float],
    ]:
        """Decompose an MMatrix into translation, rotation (degrees), and scale components.

        Parameters:
            m: MMatrix to decompose.
            rotate_order: Rotation order ("xyz", "yzx", "zxy", "xzy", "yxz", "zyx").

        Returns:
            Tuple of (translation, rotation_degrees, scale) where each is a 3-tuple of floats.

        Example:
            >>> mx = Matrices.from_srt(translate=(5, 0, 0))
            >>> t, r, s = Matrices.decompose(mx)
            >>> print(t)  # (5.0, 0.0, 0.0)
        """
        tm = MTransformationMatrix(m)
        t = tm.translation(SPACE_OBJECT)
        s = tm.scale(SPACE_OBJECT)

        # Get rotation - prefer euler via OpenMaya API if available
        if MEulerRotation is not None:
            try:
                euler = tm.rotation()
                rotation_deg = (
                    math.degrees(euler.x),
                    math.degrees(euler.y),
                    math.degrees(euler.z),
                )
            except Exception:
                # Fallback to quaternion conversion
                try:
                    quat = tm.rotation(asQuaternion=True)
                    rotation_deg = _quat_to_euler_xyz_deg(quat)
                except Exception:
                    rotation_deg = (0.0, 0.0, 0.0)
        else:
            # MEulerRotation not available - use quaternion fallback
            try:
                quat = tm.rotation(asQuaternion=True)
                rotation_deg = _quat_to_euler_xyz_deg(quat)
            except Exception:
                rotation_deg = (0.0, 0.0, 0.0)

        return (t.x, t.y, t.z), rotation_deg, (s[0], s[1], s[2])

    @staticmethod
    def inverse(m: "MMatrix") -> "MMatrix":
        """Calculate the inverse of a matrix.

        Use inverse to "subtract" a parent space or convert between coordinate systems.

        Parameters:
            m: MMatrix to invert.

        Returns:
            Inverted MMatrix.

        Example:
            >>> parent_world = Matrices.to_mmatrix(pm.PyNode("parent"))
            >>> parent_world_inv = Matrices.inverse(parent_world)
        """
        return m.inverse()

    @staticmethod
    def mult(*mats: "MMatrix") -> "MMatrix":
        """Multiply matrices right-to-left.

        Matrix multiplication order matters: mult(A, B) returns A * B,
        meaning "apply B's transform, then A's transform".

        Parameters:
            *mats: Variable number of MMatrix objects to multiply.

        Returns:
            Result of multiplying all matrices.

        Example:
            >>> # To get child in world space: child_local * parent_world
            >>> result = Matrices.mult(child_local, parent_world)
        """
        if not mats:
            return MMatrix()

        result = mats[0]
        for m in mats[1:]:
            result = result * m
        return result

    @staticmethod
    def world_to_local(
        world_matrix: "MMatrix", parent_world_matrix: "MMatrix"
    ) -> "MMatrix":
        """Convert a world-space matrix to local space relative to a parent.

        Formula: local = world * inverse(parent_world)

        Parameters:
            world_matrix: Matrix in world space.
            parent_world_matrix: Parent's world matrix.

        Returns:
            Matrix in local space.

        Example:
            >>> child_world = Matrices.to_mmatrix(pm.PyNode("child"))
            >>> parent_world = Matrices.to_mmatrix(pm.PyNode("parent"))
            >>> child_local = Matrices.world_to_local(child_world, parent_world)
        """
        return world_matrix * parent_world_matrix.inverse()

    @staticmethod
    def local_to_world(
        local_matrix: "MMatrix", parent_world_matrix: "MMatrix"
    ) -> "MMatrix":
        """Convert a local-space matrix to world space.

        Formula: world = local * parent_world

        Parameters:
            local_matrix: Matrix in local space.
            parent_world_matrix: Parent's world matrix.

        Returns:
            Matrix in world space.

        Example:
            >>> child_local = Matrices.local_matrix(pm.PyNode("child"))
            >>> parent_world = Matrices.to_mmatrix(pm.PyNode("parent"))
            >>> child_world = Matrices.local_to_world(child_local, parent_world)
        """
        return local_matrix * parent_world_matrix

    @staticmethod
    def extract_translation(m: "MMatrix") -> Tuple[float, float, float]:
        """Extract just the translation component from a matrix.

        Parameters:
            m: MMatrix to extract translation from.

        Returns:
            Translation as (x, y, z) tuple.

        Example:
            >>> mx = Matrices.from_srt(translate=(5, 10, 15))
            >>> t = Matrices.extract_translation(mx)
            >>> print(t)  # (5.0, 10.0, 15.0)
        """
        tm = MTransformationMatrix(m)
        t = tm.translation(SPACE_OBJECT)
        return (t.x, t.y, t.z)

    @staticmethod
    def is_identity(m: "MMatrix", tolerance: float = 1e-9) -> bool:
        """Check if a matrix is approximately equal to the identity matrix.

        Parameters:
            m: MMatrix to check.
            tolerance: Maximum difference allowed per element.

        Returns:
            True if matrix is identity within tolerance.

        Example:
            >>> mx = Matrices.identity()
            >>> Matrices.is_identity(mx)  # True
        """
        identity = MMatrix()
        for i in range(4):
            for j in range(4):
                if abs(m.getElement(i, j) - identity.getElement(i, j)) > tolerance:
                    return False
        return True

    # --------------------------------------------------------------------------------------------
    # DAG Transform Utilities
    # --------------------------------------------------------------------------------------------


class _DagTransforms:
    """DAG transform utilities for managing nodes and offsetParentMatrix."""

    @staticmethod
    def set_offset_parent_matrix(node: "pm.nt.Transform", m: "MMatrix") -> None:
        """Apply a matrix to a node's offsetParentMatrix attribute.

        offsetParentMatrix acts like a built-in parent constraint, allowing you
        to drive transforms without creating buffer groups.

        Parameters:
            node: PyMEL transform node.
            m: MMatrix to set.

        Example:
            >>> ctl = pm.PyNode("arm_CTL")
            >>> world_mx = Matrices.to_mmatrix(pm.PyNode("driver"))
            >>> Matrices.set_offset_parent_matrix(ctl, world_mx)
        """
        node.offsetParentMatrix.set(pm.datatypes.Matrix(m))

    @staticmethod
    def bake_world_matrix_to_transform(
        node: "pm.nt.Transform",
        m: Union["MMatrix", "pm.datatypes.Matrix", list],
        reset_offset_parent_matrix: bool = True,
    ) -> None:
        """Set a node's translate, rotate, and scale so its worldMatrix matches the given matrix.

        Parameters:
            node: PyMEL transform node.
            m: Target world matrix (MMatrix, PyMEL Matrix, or 16-element list).
            reset_offset_parent_matrix: If True, resets offsetParentMatrix to identity first.

        Example:
            >>> node = pm.PyNode("pCube1")
            >>> target_mx = Matrices.from_srt(translate=(10, 0, 0))
            >>> Matrices.bake_world_matrix_to_transform(node, target_mx)
        """
        # Convert to MMatrix if needed
        if not isinstance(m, MMatrix):
            m = _MatrixMath.to_mmatrix(m)

        # Reset offsetParentMatrix if requested
        if reset_offset_parent_matrix:
            node.offsetParentMatrix.set(pm.datatypes.Matrix())

        tm = MTransformationMatrix(m)
        t = tm.translation(SPACE_WORLD)
        s = tm.scale(SPACE_WORLD)

        # Get rotation - prefer euler via OpenMaya API if available
        if MEulerRotation is not None:
            try:
                euler = tm.rotation()
                rotation_deg = (
                    math.degrees(euler.x),
                    math.degrees(euler.y),
                    math.degrees(euler.z),
                )
            except Exception:
                try:
                    quat = tm.rotation(asQuaternion=True)
                    rotation_deg = _quat_to_euler_xyz_deg(quat)
                except Exception:
                    rotation_deg = (0.0, 0.0, 0.0)
        else:
            try:
                quat = tm.rotation(asQuaternion=True)
                rotation_deg = _quat_to_euler_xyz_deg(quat)
            except Exception:
                rotation_deg = (0.0, 0.0, 0.0)

        node.t.set((t.x, t.y, t.z))
        node.r.set(rotation_deg)
        node.s.set(s)

    @staticmethod
    def freeze_to_offset_parent_matrix(node: "pm.nt.Transform") -> None:
        """Zero a node's translate, rotate, and scale by baking current world transform into offsetParentMatrix.

        This maintains the world position while resetting local TRS values.

        Parameters:
            node: PyMEL transform node.

        Example:
            >>> ctl = pm.PyNode("offset_CTL")
            >>> Matrices.freeze_to_offset_parent_matrix(ctl)
            >>> # Now ctl.t, ctl.r, ctl.s are zero but world position unchanged
        """
        wm = node.worldMatrix.get()
        parent_inv = pm.datatypes.Matrix(node.parentInverseMatrix.get())

        # Compute local matrix: local = world * parent_inverse
        local_m = wm * parent_inv

        # Zero out local TRS
        node.t.set((0.0, 0.0, 0.0))
        node.r.set((0.0, 0.0, 0.0))
        node.s.set((1.0, 1.0, 1.0))

        # Bake into offsetParentMatrix
        node.offsetParentMatrix.set(local_m)


class _NodeBuilders:
    """Node graph builders for creating matrix-based rig systems."""

    @staticmethod
    def ensure_node(node_type: str, name: Optional[str] = None) -> "pm.nt.DependNode":
        """Create a node of the specified type.

        Parameters:
            node_type: Maya node type (e.g., "multMatrix", "decomposeMatrix").
            name: Optional name for the node.

        Returns:
            Created PyMEL node.

        Example:
            >>> mmx = Matrices.ensure_node("multMatrix", name="arm_MMX")
        """
        return pm.createNode(node_type, name=name) if name else pm.createNode(node_type)

    @staticmethod
    def build_mult_matrix_chain(
        mats: List["pm.Attribute"], name: str = "mmx_chain"
    ) -> Tuple["pm.nt.MultMatrix", "pm.nt.DecomposeMatrix"]:
        """Create a multMatrix node chain that multiplies matrices and decomposes the result.

        Connects matrix attributes in order (right-to-left multiplication).
        Result is decomposed into translate, rotate, scale outputs.

        Parameters:
            mats: List of PyMEL attributes that output 4x4 matrices.
            name: Base name for created nodes.

        Returns:
            Tuple of (multMatrix node, decomposeMatrix node).

        Example:
            >>> driver = pm.PyNode("driver")
            >>> target = pm.PyNode("target")
            >>> mmx, dcmp = Matrices.build_mult_matrix_chain(
            ...     [driver.worldMatrix[0], target.parentInverseMatrix[0]],
            ...     name="driver_to_target"
            ... )
            >>> dcmp.outputTranslate.connect(target.t)
        """
        mmx = _NodeBuilders.ensure_node("multMatrix", name=f"{name}_MMX")
        for i, src in enumerate(mats):
            src.connect(mmx.matrixIn[i], force=True)

        dcmp = _NodeBuilders.ensure_node("decomposeMatrix", name=f"{name}_DCMP")
        mmx.matrixSum.connect(dcmp.inputMatrix, force=True)

        return mmx, dcmp

    @staticmethod
    def drive_with_offset_parent_matrix(
        driver_world: "pm.nt.Transform",
        driven_ctl: "pm.nt.Transform",
        name: str = "drive_opm",
    ) -> "pm.nt.MultMatrix":
        """Drive a control's offsetParentMatrix from another transform's world matrix.

        This creates a clean parent-like relationship without creating hierarchy.
        Replaces traditional constraint + offset group patterns.

        Parameters:
            driver_world: Source transform (uses worldMatrix).
            driven_ctl: Target control (driven via offsetParentMatrix).
            name: Base name for created nodes.

        Returns:
            Created multMatrix node.

        Example:
            >>> driver = pm.PyNode("driver_GRP")
            >>> ctl = pm.PyNode("arm_CTL")
            >>> Matrices.drive_with_offset_parent_matrix(driver, ctl, name="arm_drive")
        """
        mmx = _NodeBuilders.ensure_node("multMatrix", name=f"{name}_MMX")
        driver_world.worldMatrix[0].connect(mmx.matrixIn[0], force=True)
        driven_ctl.parentInverseMatrix[0].connect(mmx.matrixIn[1], force=True)
        mmx.matrixSum.connect(driven_ctl.offsetParentMatrix, force=True)

        return mmx

    @staticmethod
    def build_space_switch(
        control: "pm.nt.Transform",
        space_parents: List["pm.nt.Transform"],
        attr_owner: Optional["pm.nt.Transform"] = None,
        attr_name: str = "space",
        name: str = "space_switch",
    ) -> "pm.nt.BlendMatrix":
        """Create a multi-space switch system using blendMatrix.

        Creates an enum attribute to select between different parent spaces,
        and drives the control's offsetParentMatrix accordingly.

        Parameters:
            control: Control to drive.
            space_parents: List of transforms representing different spaces.
            attr_owner: Optional node to hold the space switch attribute (defaults to control).
            attr_name: Name for the space switch attribute.
            name: Base name for created nodes.

        Returns:
            Created blendMatrix node.

        Example:
            >>> hand = pm.PyNode("hand_CTL")
            >>> spaces = [pm.PyNode("world_CTR"), pm.PyNode("chest_CTL"), pm.PyNode("head_CTL")]
            >>> Matrices.build_space_switch(hand, spaces, attr_name="space")
        """
        owner = attr_owner or control
        enum_names = [sp.name() for sp in space_parents]

        # Create enum attribute for space selection
        if not owner.hasAttr(attr_name):
            owner.addAttr(
                attr_name,
                attributeType="enum",
                enumName=":".join(enum_names),
                keyable=True,
            )

        # Create blendMatrix node
        blnd = _NodeBuilders.ensure_node("blendMatrix", name=f"{name}_BLND")

        # Set up each space as a blend target
        for i, sp in enumerate(space_parents):
            # Convert space to local: space_world * control_parent_inverse
            mmx = _NodeBuilders.ensure_node("multMatrix", name=f"{name}_{i:02d}_MMX")
            sp.worldMatrix[0].connect(mmx.matrixIn[0], force=True)
            control.parentInverseMatrix[0].connect(mmx.matrixIn[1], force=True)
            mmx.matrixSum.connect(blnd.target[i].targetMatrix, force=True)

            # First space is active by default
            blnd.target[i].weight.set(1.0 if i == 0 else 0.0)

        # Connect blendMatrix output to control
        blnd.outputMatrix.connect(control.offsetParentMatrix, force=True)

        # Create condition nodes to drive blend weights based on enum selection
        for i in range(len(space_parents)):
            cond = _NodeBuilders.ensure_node("condition", name=f"{name}_{i:02d}_COND")
            owner.attr(attr_name).connect(cond.firstTerm, force=True)
            cond.secondTerm.set(i)
            cond.operation.set(0)  # equal
            cond.colorIfTrueR.set(1.0)
            cond.colorIfFalseR.set(0.0)
            cond.outColorR.connect(blnd.target[i].weight, force=True)

        return blnd

    @staticmethod
    def build_aim_matrix(
        source: "pm.nt.Transform",
        target: "pm.nt.Transform",
        up_object: Optional["pm.nt.Transform"] = None,
        primary_axis: Tuple[float, float, float] = (1.0, 0.0, 0.0),
        secondary_axis: Tuple[float, float, float] = (0.0, 1.0, 0.0),
        secondary_mode: str = "align",
        name: str = "aim_mx",
    ) -> "pm.nt.AimMatrix":
        """Create a node-based aim constraint using aimMatrix.

        More robust and predictable than traditional aim constraints.
        Output matrix can be fed into offsetParentMatrix or blend chains.

        Parameters:
            source: Transform to orient (provides input matrix).
            target: Transform to aim at (primary target).
            up_object: Optional transform for up vector (secondary target).
            primary_axis: Axis that points at target (x, y, z).
            secondary_axis: Axis that aligns with up vector.
            secondary_mode: "align" or "aim" for secondary axis behavior.
            name: Base name for created node.

        Returns:
            Created aimMatrix node.

        Example:
            >>> aim = Matrices.build_aim_matrix(
            ...     source=pm.PyNode("upperArm_GDE"),
            ...     target=pm.PyNode("lowerArm_GDE"),
            ...     up_object=pm.PyNode("armUp_GDE"),
            ...     primary_axis=(1, 0, 0),
            ...     secondary_axis=(0, 1, 0),
            ...     secondary_mode="align"
            ... )
        """
        aim = _NodeBuilders.ensure_node("aimMatrix", name=f"{name}_AIM")

        # Connect inputs
        source.worldMatrix[0].connect(aim.inputMatrix, force=True)
        target.worldMatrix[0].connect(aim.primaryTargetMatrix, force=True)
        aim.primaryInputAxis.set(primary_axis)

        # Secondary axis setup
        aim.secondaryMode.set(1 if secondary_mode == "align" else 0)  # 1=align, 0=aim
        aim.secondaryInputAxis.set(secondary_axis)

        if up_object:
            up_object.worldMatrix[0].connect(aim.secondaryTargetMatrix, force=True)

        return aim

    @staticmethod
    def build_ikfk_blend(
        ik_mx_attr: "pm.Attribute",
        fk_mx_attr: "pm.Attribute",
        parent_inv_attr: "pm.Attribute",
        out_target_ctl: "pm.nt.Transform",
        switch_attr_owner: "pm.nt.Transform",
        switch_attr: str = "ikFk",
        name: str = "ikfk_blend",
    ) -> "pm.nt.BlendMatrix":
        """Create an IK/FK blend system using blendMatrix in local space.

        Blends between IK and FK matrices and drives a control via offsetParentMatrix.
        Creates a 0-1 switch attribute where 0=FK, 1=IK.

        Parameters:
            ik_mx_attr: Attribute outputting IK matrix (e.g., multMatrix.matrixSum).
            fk_mx_attr: Attribute outputting FK matrix.
            parent_inv_attr: Parent inverse matrix attribute for localization.
            out_target_ctl: Control to drive with blended result.
            switch_attr_owner: Node to hold the IK/FK switch attribute.
            switch_attr: Name for the switch attribute.
            name: Base name for created nodes.

        Returns:
            Created blendMatrix node.

        Example:
            >>> blnd = Matrices.build_ikfk_blend(
            ...     ik_mx_attr=pm.PyNode("ikChain_MMX").matrixSum,
            ...     fk_mx_attr=pm.PyNode("fkChain_MMX").matrixSum,
            ...     parent_inv_attr=pm.PyNode("wrist_CTL").parentInverseMatrix[0],
            ...     out_target_ctl=pm.PyNode("wrist_CTL"),
            ...     switch_attr_owner=pm.PyNode("settings_CTL"),
            ...     switch_attr="ikFk"
            ... )
        """
        # Create blendMatrix
        blnd = _NodeBuilders.ensure_node("blendMatrix", name=f"{name}_BLND")
        fk_mx_attr.connect(blnd.inputMatrix, force=True)
        blnd.target[0].weight.set(1.0)

        # Localize the blended result by multiplying with parent inverse
        mmx = _NodeBuilders.ensure_node("multMatrix", name=f"{name}_POST_MMX")
        blnd.outputMatrix.connect(mmx.matrixIn[0], force=True)
        parent_inv_attr.connect(mmx.matrixIn[1], force=True)

        # Create switch attribute if it doesn't exist
        if not switch_attr_owner.hasAttr(switch_attr):
            switch_attr_owner.addAttr(
                switch_attr,
                attributeType="double",
                minValue=0.0,
                maxValue=1.0,
                defaultValue=0.0,
                keyable=True,
            )

        # Set up IK as blend target
        blnd.addTargetAtIndex(1)
        ik_mx_attr.connect(blnd.target[1].targetMatrix, force=True)
        switch_attr_owner.attr(switch_attr).connect(blnd.target[1].weight, force=True)

        # Inverse weight for FK (when IK increases, FK decreases)
        rev = _NodeBuilders.ensure_node("reverse", name=f"{name}_REV")
        switch_attr_owner.attr(switch_attr).connect(rev.inputX, force=True)
        rev.outputX.connect(blnd.target[0].weight, force=True)

        # Drive control
        mmx.matrixSum.connect(out_target_ctl.offsetParentMatrix, force=True)

        return blnd


# --------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------


class Matrices(_MatrixMath, _DagTransforms, _NodeBuilders, ptk.HelpMixin):
    """Matrix utilities for Maya rigging and animation.

    Provides pure math operations (using Maya API) and node graph builders
    for creating matrix-based rigs with clean, predictable evaluation.

    This class inherits functionality from:
    - _MatrixMath: Pure API math operations
    - _DagTransforms: DAG transform utilities
    - _NodeBuilders: Node graph construction
    """

    pass


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------

"""
Key Patterns for Matrix-Based Rigging:

1. offsetParentMatrix vs Traditional Hierarchies:
   - offsetParentMatrix acts like a built-in parent constraint
   - Keeps hierarchies flat and evaluation predictable
   - Use: Matrices.drive_with_offset_parent_matrix()

2. Space Conversion Formula:
   - To move from space A to space B:
     result = object_in_A * inverse(A_world) * B_world
   - Example: parent space to world:
     local * parent_world = world

3. Right-to-Left Multiplication:
   - Matrix multiplication order: A * B means "apply B first, then A"
   - Child world position: child_local * parent_world

4. Points vs Vectors:
   - Points (positions): Use full 4x4 matrix with translation
   - Vectors (directions): Use only 3x3 rotation/scale portion

5. Common Node Chains:
   - multMatrix: Combine transforms, remove parents
   - decomposeMatrix: Extract T/R/S from matrix
   - composeMatrix: Build matrix from T/R/S
   - blendMatrix: Blend between spaces (IK/FK, space switching)
   - aimMatrix: Node-based aiming (more stable than aim constraint)

6. Blend in Correct Space:
   - World-space blend can cause issues with parenting
   - Prefer local-space blend: blend first, then apply parent
   - Use parentInverseMatrix to localize results

7. Performance Tips:
   - Fewer nodes = faster evaluation
   - Matrix connections evaluate more predictably than constraints
   - Keep skin joint hierarchies flat when possible
"""
