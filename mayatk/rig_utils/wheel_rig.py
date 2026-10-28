# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    cmds = None
    om = None

from typing import List, Tuple, Optional

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.edit_utils.naming._naming import Naming


class WheelRig(ptk.LoggingMixin):
    """
    Handles basic wheel rigging by linking rotation to linear control movement.

    This class supports re-entrancy by stamping a 'wheelRigId' string attribute onto the control object.
    When initialized with a control that already has this attribute, it will reuse the existing rig name
    to allow for updating parameters on an existing rig without creating duplicate expressions.

    Parameters:
        control (str/obj): The control driving movement.
        wheels (List[str/obj]): Wheel transforms.
        rig_name (str): Optional name for the rig.

    Attributes:
        control (object): The control object.
        wheels (List[object]): Wheel transforms.
        rig_name (str): Name of the rig.
    """

    def __init__(
        self, control, wheels, rig_name: str = None, freeze_transforms: bool = True
    ):
        self.control = NodeUtils.get_transform_node(control)
        self.wheels = NodeUtils.get_transform_node(wheels)

        if not self.control or not all(self.wheels):
            raise ValueError("Invalid control or wheel inputs.")

        # Check for persistent rig ID
        stored_name = self._stored_name(self.control)
        self._rig_name = (
            rig_name or stored_name or Naming.generate_unique_name("wheel_rig")
        )

        # Persist ID
        if not cmds.attributeQuery("wheelRigId", node=str(self.control), exists=True):
            cmds.addAttr(str(self.control), longName="wheelRigId", dataType="string")
        cmds.setAttr(f"{self.control}.wheelRigId", self._rig_name, type="string")

        if freeze_transforms:
            # Freeze translate only — rotation must be preserved so the
            # auto-flip pass can read mirrored-wheel orientation from the
            # world matrix.
            # store=False: rig construction, not a user-reversible freeze.
            XformUtils.freeze_transforms(self.control, translate=True, store=False)
            XformUtils.freeze_transforms(self.wheels, translate=True, store=False)

    @property
    def rig_name(self) -> str:
        return self._rig_name

    @rig_name.setter
    def rig_name(self, name: str):
        self._rig_name = name
        self.logger.debug(f"Rig name set to: {self._rig_name}")

    # ------------------------------------------------------------------
    # The selection (what tentacle's Wheel Rig panel runs)
    # ------------------------------------------------------------------

    @staticmethod
    def _stored_name(control) -> Optional[str]:
        """The rig ID an earlier rig stamped on *control* (``wheelRigId``), or ``None``."""
        node = str(control)
        try:
            if cmds.attributeQuery("wheelRigId", node=node, exists=True):
                return cmds.getAttr(f"{node}.wheelRigId") or None
        except (RuntimeError, ValueError):  # not a node: a component, a stale name
            pass
        return None

    @staticmethod
    def _control_and_wheels() -> Tuple[str, List[str]]:
        """The selection as ``(control, wheels)``: the LAST selected object
        drives, every other selected object is a wheel.

        Raises:
            ValueError: Fewer than two objects are selected, or one is not a
                transform.
        """
        sel = cmds.ls(sl=True, flatten=True) or []
        if len(sel) < 2:
            raise ValueError("Select one or more wheel objects, then the driver (last).")
        control = NodeUtils.get_transform_node(sel[-1])
        wheels = NodeUtils.get_transform_node(sel[:-1])
        if not control or not wheels or not all(wheels):
            raise ValueError(
                "Invalid selection. Make sure all selected objects are valid transforms."
            )
        return control, wheels

    @classmethod
    def default_rig_name(cls, control=None) -> Optional[str]:
        """The name a rig on *control* takes when none is given: the rig ID an
        earlier rig stamped on it (a re-run updates that rig in place), else
        ``<control>_wheel_rig``.

        Parameters:
            control (str/None): The driver. ``None`` (the default) takes the
                selection's: its last selected object.

        Returns:
            str/None: The name, or ``None`` with nothing selected.
        """
        if control is None:
            sel = cmds.ls(sl=True, flatten=True) or []
            if not sel:
                return None
            control = sel[-1]
        return cls._stored_name(control) or f"{CoreUtils.short_name(control)}_wheel_rig"

    @classmethod
    def from_selection(
        cls, rig_name: Optional[str] = None, freeze_transforms: bool = True, **kwargs
    ) -> "WheelRig":
        """Rig the selected wheels to the selected driver as ONE undo step: what
        tentacle's Wheel Rig panel runs.

        The LAST selected object drives; every other selected object is a
        wheel. The rig takes the driver's stored rig ID when an earlier rig
        left one -- a re-run updates that rig in place -- else *rig_name*, else
        ``<driver>_wheel_rig`` (:meth:`default_rig_name`).

        Parameters:
            rig_name (str): The name for a driver that carries no rig ID yet.
            freeze_transforms (bool): Freeze translation on the driver and the
                wheels first (see the constructor).
            **kwargs: :meth:`rig_rotation`'s options (``movement_axis``,
                ``rotation_axis``, ``wheel_height``, ``use_world_space``).

        Returns:
            WheelRig: The rig.

        Raises:
            ValueError: An invalid selection or wheel height -- checked before
                the undo chunk opens, so a refused click leaves no step.
        """
        control, wheels = cls._control_and_wheels()
        if kwargs.get("wheel_height", 1.0) <= 0:
            raise ValueError(f"Invalid wheel height: {kwargs['wheel_height']}")
        name = cls._stored_name(control) or rig_name or cls.default_rig_name(control)
        with CoreUtils.undo_chunk("Wheel Rig"):
            rig = cls(control, wheels, rig_name=name, freeze_transforms=freeze_transforms)
            rig.rig_rotation(wheels=rig.wheels, **kwargs)
        return rig

    @staticmethod
    def wheel_size(obj=None, movement_axis: str = "translateZ") -> Optional[float]:
        """A wheel's diameter from *obj*'s bounding box: its larger extent
        across the two axes the wheel turns in for *movement_axis* (Move X
        rolls on Z: the X/Y extents; Move Y spins on Y: X/Z; Move Z pitches on
        X: Y/Z).

        Parameters:
            obj (str/None): The wheel. ``None`` (the default) takes the first
                selected object.
            movement_axis (str): The driver's travel channel (``translateX`` /
                ``translateY`` / ``translateZ``).

        Returns:
            float/None: The diameter, or ``None`` with nothing to measure.
        """
        if obj is None:
            sel = cmds.ls(sl=True, flatten=True) or []
            if not sel:
                return None
            obj = sel[0]
        bbox = cmds.xform(obj, q=True, bb=True)
        width, height, depth = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
        if "X" in movement_axis:
            return max(width, height)
        if "Y" in movement_axis:
            return max(width, depth)
        return max(height, depth)

    def get_expressions(self, filter_by_rig: bool = False) -> List[object]:
        """Return all expression nodes connected to the control.

        Parameters:
            filter_by_rig (bool): If True, only return expressions whose name contains the rig name.

        Returns:
            List of expression nodes.
        """
        # Expressions read from control, so they are destinations of control's connections.
        exprs = cmds.listConnections(str(self.control), type="expression") or []
        exprs = list(set(exprs))

        if filter_by_rig:
            exprs = [
                e for e in exprs if self.rig_name in e.split("|")[-1].split(":")[-1]
            ]
        return exprs

    def delete_expressions(self, filter_by_rig: bool = True) -> None:
        """Delete expression nodes associated with this rig.

        Parameters:
            filter_by_rig (bool): If True, only delete expressions whose name contains the rig name.
                                If False, delete all expressions under the control.
        """
        exprs = self.get_expressions(filter_by_rig=filter_by_rig)
        if exprs:
            cmds.delete(exprs)
            self.logger.info(
                f"Deleted {len(exprs)} expressions for rig: {self.rig_name}"
            )
        else:
            self.logger.info(f"No expressions found to delete for rig: {self.rig_name}")

        # Clean up the decomposeMatrix utility node
        decomp_name = f"{self.rig_name}_decompose"
        if cmds.objExists(decomp_name):
            cmds.delete(decomp_name)
            self.logger.info(f"Deleted decomposeMatrix node: {decomp_name}")

    @CoreUtils.undoable
    def rig_rotation(
        self,
        movement_axis: str = "translateZ",
        rotation_axis: Optional[str] = None,
        wheel_height: float = 1.0,
        wheels: Optional[List["object"]] = None,
        use_world_space: bool = False,
    ) -> None:
        """
        Rig wheels to rotate based on control movement.

        Parameters:
            movement_axis: Which translate channel drives rotation
                (e.g. "translateZ").
            rotation_axis: Which rotate channel to drive on the wheels.
                Auto-inferred from *movement_axis* when ``None``.
            wheel_height: Diameter used to compute rotation amount.
            wheels: Wheel transforms to rig.  Falls back to ``self.wheels``.
            use_world_space: When ``True``, create a ``decomposeMatrix`` node
                and read world-space position so that parent movement is
                captured.  When ``False`` (default), the expression reads
                the control's local translate directly — simpler and
                sufficient when the control itself is being animated.
        """
        if wheels is None:
            wheels = self.wheels

        if not wheels:
            raise ValueError("No wheels specified. You must pass wheels to rig.")

        if wheel_height <= 0:
            raise ValueError(f"Invalid wheel height: {wheel_height}")

        # Auto-infer rotation axis if not specified
        if not rotation_axis:
            # Standard vehicle dynamics assumption:
            # - Forward Z -> Pitch (Rotate X)
            # - Side X -> Roll (Rotate Z)
            if "Z" in movement_axis:
                rotation_axis = "rotateX"
            elif "X" in movement_axis:
                rotation_axis = "rotateZ"
            elif "Y" in movement_axis:
                rotation_axis = "rotateY"  # Uncommon but possible (vertical scroll)
            else:
                rotation_axis = "rotateX"  # Fallback
            self.logger.info(
                f"Auto-inferred rotation axis: {rotation_axis} from movement: {movement_axis}"
            )

        # Smart Attribute Management for Wheel Height
        # Find or create a wheelHeight attribute that matches the requested height,
        # or create a new one if existing ones differ significantly.
        height_attr_name = "wheelHeight"
        suffix_idx = 0
        epsilon = 0.01

        while True:
            # Construct candidate name (wheelHeight, wheelHeight_1, wheelHeight_2...)
            candidate = (
                "wheelHeight" if suffix_idx == 0 else f"wheelHeight_{suffix_idx}"
            )

            if not cmds.attributeQuery(candidate, node=str(self.control), exists=True):
                # Found a free slot, create it
                cmds.addAttr(
                    str(self.control),
                    longName=candidate,
                    k=True,
                    dv=wheel_height,
                    min=0.001,
                )
                height_attr_name = candidate
                break

            # Attribute exists, check if value matches our target height
            existing_val = cmds.getAttr(f"{self.control}.{candidate}")
            if abs(existing_val - wheel_height) < epsilon:
                # Close enough, reuse this attribute
                # Update it exactly to be sure
                cmds.setAttr(f"{self.control}.{candidate}", wheel_height)
                height_attr_name = candidate
                break

            # Attribute exists but value is different -> Try next index
            suffix_idx += 1

        # Global control attributes (shared across all wheel groups)
        if not cmds.attributeQuery(
            "enableRotation", node=str(self.control), exists=True
        ):
            cmds.addAttr(
                str(self.control),
                longName="enableRotation",
                k=True,
                dv=1.0,
                min=0.0,
                max=1.0,
            )
        if not cmds.attributeQuery(
            "spinDirection", node=str(self.control), exists=True
        ):
            cmds.addAttr(str(self.control), longName="spinDirection", k=True, dv=1.0)

        # Ensure attributes are exposed in the Channel Box
        for attr_name in [height_attr_name, "enableRotation", "spinDirection"]:
            try:
                cmds.setAttr(f"{self.control}.{attr_name}", keyable=True)
            except Exception:
                pass  # Ignore if already keyable or locked logic interferes

        # Helper to get the world-space vector for the chosen rotation axis
        def get_axis_vector(node, axis_name):
            idx = {"rotateX": 0, "rotateY": 1, "rotateZ": 2}.get(axis_name, 0)
            # transform world matrix is 16 floats; rows 0/1/2 are x/y/z axes
            wm = cmds.xform(str(node), q=True, m=True, ws=True)
            row = wm[idx * 4 : idx * 4 + 3]
            return om.MVector(*row)

        # Get the control's reference vector for alignment check
        control_vec = get_axis_vector(self.control, rotation_axis)

        # Determine how to read the control's position for the expression.
        if use_world_space:
            # Create or reuse a decomposeMatrix node for world-space position.
            # This captures parent movement, not just local translate.
            decomp_name = f"{self.rig_name}_decompose"
            if cmds.objExists(decomp_name):
                decomp = decomp_name
            else:
                decomp = cmds.createNode("decomposeMatrix", name=decomp_name)
                cmds.connectAttr(
                    f"{self.control}.worldMatrix[0]",
                    f"{decomp}.inputMatrix",
                    force=True,
                )

            _ws_attr_map = {
                "translateX": f"{decomp_name}.outputTranslateX",
                "translateY": f"{decomp_name}.outputTranslateY",
                "translateZ": f"{decomp_name}.outputTranslateZ",
            }
            distance_attr = _ws_attr_map[movement_axis]
        else:
            # Read from the control's local translate directly.
            distance_attr = f"{self.control}.{movement_axis}"

        for wheel in wheels:
            attr = f"{wheel}.{rotation_axis}"

            # Determine auto-flip based on alignment with control
            # If the wheel is flipped 180 (e.g. right side vs left side), dot product will be negative
            wheel_vec = get_axis_vector(wheel, rotation_axis)
            dot_prod = control_vec * wheel_vec  # MVector dot product via * operator
            auto_flip = -1.0 if dot_prod < 0 else 1.0

            # Disconnect any existing incoming connections
            if cmds.listConnections(attr, source=True, destination=False):
                for src in cmds.listConnections(attr, p=True, s=True, d=False):
                    cmds.disconnectAttr(src, attr)

            # Delete any previous expression node tied to this wheel
            expr_name = f"{self.rig_name}_{wheel.split('|')[-1].split(':')[-1]}_expr"
            if cmds.objExists(expr_name):
                cmds.delete(expr_name)

            # Build new expression
            expr_text = (
                f"float $distance = {distance_attr};\n"
                f"float $height = {self.control}.{height_attr_name};\n"
                f"float $enable = {self.control}.enableRotation;\n"
                f"float $dir = {self.control}.spinDirection;\n"
                f"float $circumference = 3.14159 * $height;\n"
                f"float $auto_flip = {auto_flip};\n"
                f"float $rotation = ($distance / $circumference) * 360 * $dir * $auto_flip;\n"
                f"{wheel}.{rotation_axis} = $rotation * $enable;\n"
            )

            expr_node = cmds.expression(s=expr_text, name=expr_name)

            # Optional: log for debug
            self.logger.debug(
                f"Rigged wheel: {wheel.split('|')[-1].split(':')[-1]} with expression: {expr_node.split('|')[-1].split(':')[-1]}"
            )

        self.logger.info(
            f"Wheel rig rotation updated for wheels: {[w.split('|')[-1].split(':')[-1] for w in wheels]}"
        )
