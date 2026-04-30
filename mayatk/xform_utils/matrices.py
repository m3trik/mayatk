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
from typing import Iterable, Optional, Tuple, List, Union

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
    cmds = None

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

# Euler rotation order mapping for MEulerRotation constructor
_EULER_ORDER = {"xyz": 0, "yzx": 1, "zxy": 2, "xzy": 3, "yxz": 4, "zyx": 5}

# Matrix plugs that are SINGULAR (not multi-instance). Read with
# ``cmds.getAttr("node.attr")`` — passing an index raises. Anything else
# (worldMatrix, parentInverseMatrix, …) is multi-instance and requires
# ``[index]`` to disambiguate.
_SINGULAR_MATRIX_ATTRS = frozenset(
    {
        "matrix",
        "inverseMatrix",
        "xformMatrix",
        "offsetParentMatrix",
        "diffPointMatrix",
    }
)


def get_matrix(node: str, attr: str = "worldMatrix", index: int = 0) -> List[float]:
    """Return a 16-element flat list for a matrix attribute on *node*.

    Multi-instance matrix attrs (``worldMatrix``, ``parentInverseMatrix``,
    etc.) are indexed with ``[index]`` automatically — calling
    ``cmds.getAttr("node.worldMatrix")`` without an index is ambiguous and
    warns/errors in modern Maya. Singular plugs (``matrix``,
    ``offsetParentMatrix``) are read directly.
    """
    if attr in _SINGULAR_MATRIX_ATTRS:
        plug = f"{node}.{attr}"
    else:
        plug = f"{node}.{attr}[{index}]"
    return cmds.getAttr(plug)


def set_matrix(node: str, attr: str, value, index: int = 0) -> None:
    """Set a matrix attribute on *node* from an MMatrix or 16-element iterable.

    *value* may be:

    * an ``MMatrix`` (anything exposing ``getElement(r, c)``), or
    * any iterable of 16 floats (``list``, ``tuple``,
      ``MTransformationMatrix.asMatrix()`` flattened upstream, …).

    Multi-instance attrs are indexed via *index* (default 0); singular
    attrs ignore the index.
    """
    if attr in _SINGULAR_MATRIX_ATTRS:
        plug = f"{node}.{attr}"
    else:
        plug = f"{node}.{attr}[{index}]"
    if hasattr(value, "getElement"):
        flat = [value.getElement(r, c) for r in range(4) for c in range(4)]
    else:
        flat = list(value)
    if len(flat) != 16:
        raise ValueError(
            f"set_matrix expected 16 elements, got {len(flat)} for {plug}"
        )
    cmds.setAttr(plug, *flat, type="matrix")


def _quat_to_euler_xyz_deg(quat: "MQuaternion") -> Tuple[float, float, float]:
    """Convert an MQuaternion into XYZ Euler angles in degrees."""

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
        matrix_like: Union[str, "MMatrix", list],
    ) -> "MMatrix":
        """Convert various matrix representations to MMatrix.

        Parameters:
            matrix_like: Transform node name (str), MMatrix, or 16-element list.

        Returns:
            MMatrix representation.

        Example:
            >>> world_mx = Matrices.to_mmatrix("pCube1")
            >>> # Or from a flat list:
            >>> api_mx = Matrices.to_mmatrix([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])
        """
        # Already an MMatrix
        if isinstance(matrix_like, MMatrix):
            return matrix_like
        # Node name string — get world matrix via cmds
        if isinstance(matrix_like, str):
            return MMatrix(get_matrix(matrix_like, "worldMatrix"))
        # List/tuple of 16 values
        if hasattr(matrix_like, "__len__") and len(matrix_like) == 16:
            return MMatrix(matrix_like)
        raise TypeError(
            f"Cannot convert {type(matrix_like).__name__} to MMatrix. "
            "Expected node name str, MMatrix, or 16-element list."
        )

    @staticmethod
    def local_matrix(node: str) -> "MMatrix":
        """Get a transform's local matrix as MMatrix.

        Parameters:
            node: Transform node name.

        Returns:
            Local matrix as MMatrix.

        Example:
            >>> local_mx = Matrices.local_matrix("pCube1")
        """
        return MMatrix(get_matrix(node, "matrix"))

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

        euler = MEulerRotation(
            math.radians(rotate_euler_deg[0]),
            math.radians(rotate_euler_deg[1]),
            math.radians(rotate_euler_deg[2]),
            _EULER_ORDER.get(rotate_order.lower(), 0),
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
            >>> parent_world = Matrices.to_mmatrix("parent")
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
            >>> child_world = Matrices.to_mmatrix("child")
            >>> parent_world = Matrices.to_mmatrix("parent")
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
            >>> child_local = Matrices.local_matrix("child")
            >>> parent_world = Matrices.to_mmatrix("parent")
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
    def set_offset_parent_matrix(node: str, m: "MMatrix") -> None:
        """Apply a matrix to a node's offsetParentMatrix attribute.

        offsetParentMatrix acts like a built-in parent constraint, allowing you
        to drive transforms without creating buffer groups.

        Parameters:
            node: Transform node name.
            m: MMatrix to set.

        Example:
            >>> world_mx = Matrices.to_mmatrix("driver")
            >>> Matrices.set_offset_parent_matrix("arm_CTL", world_mx)
        """
        set_matrix(node, "offsetParentMatrix", m)

    @staticmethod
    def bake_world_matrix_to_transform(
        node: str,
        m: Union["MMatrix", list],
        reset_offset_parent_matrix: bool = True,
    ) -> None:
        """Set a node's translate, rotate, and scale so its worldMatrix matches the given matrix.

        Parameters:
            node: Transform node name.
            m: Target world matrix (MMatrix or 16-element list).
            reset_offset_parent_matrix: If True, resets offsetParentMatrix to identity first.

        Example:
            >>> target_mx = Matrices.from_srt(translate=(10, 0, 0))
            >>> Matrices.bake_world_matrix_to_transform("pCube1", target_mx)
        """
        # Convert to MMatrix if needed
        if not isinstance(m, MMatrix):
            m = _MatrixMath.to_mmatrix(m)

        # Reset offsetParentMatrix if requested
        if reset_offset_parent_matrix:
            set_matrix(node, "offsetParentMatrix", MMatrix())

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

        cmds.setAttr(f"{node}.translate", t.x, t.y, t.z, type="double3")
        cmds.setAttr(f"{node}.rotate", *rotation_deg, type="double3")
        cmds.setAttr(f"{node}.scale", s[0], s[1], s[2], type="double3")

    @staticmethod
    def freeze_to_offset_parent_matrix(node: str) -> None:
        """Zero a node's translate, rotate, and scale by baking current world transform into offsetParentMatrix.

        This maintains the world position while resetting local TRS values.

        Parameters:
            node: Transform node name.

        Example:
            >>> Matrices.freeze_to_offset_parent_matrix("offset_CTL")
            >>> # Now translate/rotate/scale are zero but world position unchanged
        """
        wm = MMatrix(get_matrix(node, "worldMatrix"))
        parent_inv = MMatrix(get_matrix(node, "parentInverseMatrix"))

        # Compute local matrix: local = world * parent_inverse
        local_m = wm * parent_inv

        # Zero out local TRS
        cmds.setAttr(f"{node}.translate", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{node}.rotate", 0.0, 0.0, 0.0, type="double3")
        cmds.setAttr(f"{node}.scale", 1.0, 1.0, 1.0, type="double3")

        # Bake into offsetParentMatrix
        set_matrix(node, "offsetParentMatrix", local_m)


class _NodeBuilders:
    """Node graph builders for creating matrix-based rig systems."""

    @staticmethod
    def ensure_node(node_type: str, name: Optional[str] = None) -> str:
        """Create a node of the specified type.

        Parameters:
            node_type: Maya node type (e.g., "multMatrix", "decomposeMatrix").
            name: Optional name for the node.

        Returns:
            Created node name string.

        Example:
            >>> mmx = Matrices.ensure_node("multMatrix", name="arm_MMX")
        """
        return cmds.createNode(node_type, name=name) if name else cmds.createNode(node_type)

    @staticmethod
    def build_mult_matrix_chain(
        mats: List[str], name: str = "mmx_chain"
    ) -> Tuple[str, str]:
        """Create a multMatrix node chain that multiplies matrices and decomposes the result.

        Connects matrix attribute plugs in order (right-to-left multiplication).
        Result is decomposed into translate, rotate, scale outputs.

        Parameters:
            mats: List of attribute plug strings that output 4x4 matrices
                  (e.g. ``["driver.worldMatrix[0]", "target.parentInverseMatrix[0]"]``).
            name: Base name for created nodes.

        Returns:
            Tuple of (multMatrix node name, decomposeMatrix node name).

        Example:
            >>> mmx, dcmp = Matrices.build_mult_matrix_chain(
            ...     ["driver.worldMatrix[0]", "target.parentInverseMatrix[0]"],
            ...     name="driver_to_target"
            ... )
            >>> cmds.connectAttr(f"{dcmp}.outputTranslate", "target.t")
        """
        mmx = _NodeBuilders.ensure_node("multMatrix", name=f"{name}_MMX")
        for i, src in enumerate(mats):
            cmds.connectAttr(src, f"{mmx}.matrixIn[{i}]", force=True)

        dcmp = _NodeBuilders.ensure_node("decomposeMatrix", name=f"{name}_DCMP")
        cmds.connectAttr(f"{mmx}.matrixSum", f"{dcmp}.inputMatrix", force=True)

        return mmx, dcmp

    @staticmethod
    def drive_with_offset_parent_matrix(
        driver_world: str,
        driven_ctl: str,
        name: str = "drive_opm",
    ) -> str:
        """Drive a control's offsetParentMatrix from another transform's world matrix.

        This creates a clean parent-like relationship without creating hierarchy.
        Replaces traditional constraint + offset group patterns.

        Parameters:
            driver_world: Source transform node name (uses worldMatrix).
            driven_ctl: Target control node name (driven via offsetParentMatrix).
            name: Base name for created nodes.

        Returns:
            Created multMatrix node name.

        Example:
            >>> Matrices.drive_with_offset_parent_matrix("driver_GRP", "arm_CTL", name="arm_drive")
        """
        mmx = _NodeBuilders.ensure_node("multMatrix", name=f"{name}_MMX")
        cmds.connectAttr(f"{driver_world}.worldMatrix[0]", f"{mmx}.matrixIn[0]", force=True)
        cmds.connectAttr(f"{driven_ctl}.parentInverseMatrix[0]", f"{mmx}.matrixIn[1]", force=True)
        cmds.connectAttr(f"{mmx}.matrixSum", f"{driven_ctl}.offsetParentMatrix", force=True)

        return mmx

    @staticmethod
    def build_space_switch(
        control: str,
        space_parents: List[str],
        attr_owner: Optional[str] = None,
        attr_name: str = "space",
        name: str = "space_switch",
    ) -> str:
        """Create a multi-space switch system using blendMatrix.

        Creates an enum attribute to select between different parent spaces,
        and drives the control's offsetParentMatrix accordingly.

        Parameters:
            control: Control node name to drive.
            space_parents: List of transform node names representing different spaces.
            attr_owner: Optional node name to hold the space switch attribute (defaults to control).
            attr_name: Name for the space switch attribute.
            name: Base name for created nodes.

        Returns:
            Created blendMatrix node name.

        Example:
            >>> spaces = ["world_CTR", "chest_CTL", "head_CTL"]
            >>> Matrices.build_space_switch("hand_CTL", spaces, attr_name="space")
        """
        owner = attr_owner or control
        enum_names = [sp.split("|")[-1].split(":")[-1] for sp in space_parents]

        # Create enum attribute for space selection
        if not cmds.attributeQuery(attr_name, node=owner, exists=True):
            cmds.addAttr(
                owner,
                longName=attr_name,
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
            cmds.connectAttr(f"{sp}.worldMatrix[0]", f"{mmx}.matrixIn[0]", force=True)
            cmds.connectAttr(f"{control}.parentInverseMatrix[0]", f"{mmx}.matrixIn[1]", force=True)
            cmds.connectAttr(f"{mmx}.matrixSum", f"{blnd}.target[{i}].targetMatrix", force=True)

            # First space is active by default
            cmds.setAttr(f"{blnd}.target[{i}].weight", 1.0 if i == 0 else 0.0)

        # Connect blendMatrix output to control
        cmds.connectAttr(f"{blnd}.outputMatrix", f"{control}.offsetParentMatrix", force=True)

        # Create condition nodes to drive blend weights based on enum selection
        for i in range(len(space_parents)):
            cond = _NodeBuilders.ensure_node("condition", name=f"{name}_{i:02d}_COND")
            cmds.connectAttr(f"{owner}.{attr_name}", f"{cond}.firstTerm", force=True)
            cmds.setAttr(f"{cond}.secondTerm", i)
            cmds.setAttr(f"{cond}.operation", 0)  # equal
            cmds.setAttr(f"{cond}.colorIfTrueR", 1.0)
            cmds.setAttr(f"{cond}.colorIfFalseR", 0.0)
            cmds.connectAttr(f"{cond}.outColorR", f"{blnd}.target[{i}].weight", force=True)

        return blnd

    @staticmethod
    def build_aim_matrix(
        source: str,
        target: str,
        up_object: Optional[str] = None,
        primary_axis: Tuple[float, float, float] = (1.0, 0.0, 0.0),
        secondary_axis: Tuple[float, float, float] = (0.0, 1.0, 0.0),
        secondary_mode: str = "align",
        name: str = "aim_mx",
    ) -> str:
        """Create a node-based aim constraint using aimMatrix.

        More robust and predictable than traditional aim constraints.
        Output matrix can be fed into offsetParentMatrix or blend chains.

        Parameters:
            source: Transform node name to orient (provides input matrix).
            target: Transform node name to aim at (primary target).
            up_object: Optional transform node name for up vector (secondary target).
            primary_axis: Axis that points at target (x, y, z).
            secondary_axis: Axis that aligns with up vector.
            secondary_mode: "align" or "aim" for secondary axis behavior.
            name: Base name for created node.

        Returns:
            Created aimMatrix node name.

        Example:
            >>> aim = Matrices.build_aim_matrix(
            ...     source="upperArm_GDE",
            ...     target="lowerArm_GDE",
            ...     up_object="armUp_GDE",
            ...     primary_axis=(1, 0, 0),
            ...     secondary_axis=(0, 1, 0),
            ...     secondary_mode="align"
            ... )
        """
        aim = _NodeBuilders.ensure_node("aimMatrix", name=f"{name}_AIM")

        # Connect inputs
        cmds.connectAttr(f"{source}.worldMatrix[0]", f"{aim}.inputMatrix", force=True)
        cmds.connectAttr(f"{target}.worldMatrix[0]", f"{aim}.primaryTargetMatrix", force=True)
        cmds.setAttr(f"{aim}.primaryInputAxis", *primary_axis, type="double3")

        # Secondary axis setup
        cmds.setAttr(f"{aim}.secondaryMode", 1 if secondary_mode == "align" else 0)  # 1=align, 0=aim
        cmds.setAttr(f"{aim}.secondaryInputAxis", *secondary_axis, type="double3")

        if up_object:
            cmds.connectAttr(f"{up_object}.worldMatrix[0]", f"{aim}.secondaryTargetMatrix", force=True)

        return aim

    @staticmethod
    def build_ikfk_blend(
        ik_mx_attr: str,
        fk_mx_attr: str,
        parent_inv_attr: str,
        out_target_ctl: str,
        switch_attr_owner: str,
        switch_attr: str = "ikFk",
        name: str = "ikfk_blend",
    ) -> str:
        """Create an IK/FK blend system using blendMatrix in local space.

        Blends between IK and FK matrices and drives a control via offsetParentMatrix.
        Creates a 0-1 switch attribute where 0=FK, 1=IK.

        Parameters:
            ik_mx_attr: Plug string outputting IK matrix (e.g., "ikChain_MMX.matrixSum").
            fk_mx_attr: Plug string outputting FK matrix.
            parent_inv_attr: Parent inverse matrix plug string for localization.
            out_target_ctl: Control node name to drive with blended result.
            switch_attr_owner: Node name to hold the IK/FK switch attribute.
            switch_attr: Name for the switch attribute.
            name: Base name for created nodes.

        Returns:
            Created blendMatrix node name.

        Example:
            >>> blnd = Matrices.build_ikfk_blend(
            ...     ik_mx_attr="ikChain_MMX.matrixSum",
            ...     fk_mx_attr="fkChain_MMX.matrixSum",
            ...     parent_inv_attr="wrist_CTL.parentInverseMatrix[0]",
            ...     out_target_ctl="wrist_CTL",
            ...     switch_attr_owner="settings_CTL",
            ...     switch_attr="ikFk"
            ... )
        """
        # Create blendMatrix
        blnd = _NodeBuilders.ensure_node("blendMatrix", name=f"{name}_BLND")
        cmds.connectAttr(fk_mx_attr, f"{blnd}.inputMatrix", force=True)
        cmds.setAttr(f"{blnd}.target[0].weight", 1.0)

        # Localize the blended result by multiplying with parent inverse
        mmx = _NodeBuilders.ensure_node("multMatrix", name=f"{name}_POST_MMX")
        cmds.connectAttr(f"{blnd}.outputMatrix", f"{mmx}.matrixIn[0]", force=True)
        cmds.connectAttr(parent_inv_attr, f"{mmx}.matrixIn[1]", force=True)

        # Create switch attribute if it doesn't exist
        if not cmds.attributeQuery(switch_attr, node=switch_attr_owner, exists=True):
            cmds.addAttr(
                switch_attr_owner,
                longName=switch_attr,
                attributeType="double",
                minValue=0.0,
                maxValue=1.0,
                defaultValue=0.0,
                keyable=True,
            )

        # Set up IK as blend target
        cmds.connectAttr(ik_mx_attr, f"{blnd}.target[1].targetMatrix", force=True)
        cmds.connectAttr(f"{switch_attr_owner}.{switch_attr}", f"{blnd}.target[1].weight", force=True)

        # Inverse weight for FK (when IK increases, FK decreases)
        rev = _NodeBuilders.ensure_node("reverse", name=f"{name}_REV")
        cmds.connectAttr(f"{switch_attr_owner}.{switch_attr}", f"{rev}.inputX", force=True)
        cmds.connectAttr(f"{rev}.outputX", f"{blnd}.target[0].weight", force=True)

        # Drive control
        cmds.connectAttr(f"{mmx}.matrixSum", f"{out_target_ctl}.offsetParentMatrix", force=True)

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
