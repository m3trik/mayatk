# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Optional, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.edit_utils.naming import Naming


class WheelRig(ptk.LoggingMixin):
    """
    Handles basic wheel rigging by linking rotation to linear control movement.

    Parameters:
        control (str/obj): The control driving movement.
        wheels (List[str/obj]): Wheel transforms.
        rig_name (str): Optional name for the rig.

    Attributes:
        control (pm.nodetypes.Transform): The control object.
        wheels (List[pm.nodetypes.Transform]): Wheel transforms.
        rig_name (str): Name of the rig.
    """

    def __init__(
        self, control, wheels, rig_name: str = None, freeze_transforms: bool = True
    ):
        self._rig_name = rig_name or Naming.generate_unique_name("wheel_rig")
        self.control = NodeUtils.get_transform_node(control)
        self.wheels = NodeUtils.get_transform_node(wheels)

        if not self.control or not all(self.wheels):
            raise ValueError("Invalid control or wheel inputs.")

        if freeze_transforms:
            XformUtils.freeze_transforms(self.control)
            XformUtils.freeze_transforms(self.wheels)

        self.control.rig = self  # Allow access via control.rig

    @property
    def rig_name(self) -> str:
        return self._rig_name

    @rig_name.setter
    def rig_name(self, name: str):
        self._rig_name = name
        self.logger.debug(f"Rig name set to: {self._rig_name}")

    def get_expressions(
        self, filter_by_rig: bool = False
    ) -> List[pm.nodetypes.Expression]:
        """Return all expression nodes under the control.

        Parameters:
            filter_by_rig (bool): If True, only return expressions whose name contains the rig name.

        Returns:
            List of expression nodes.
        """
        exprs = pm.listRelatives(self.control, type="expression", ad=True) or []
        if filter_by_rig:
            exprs = [e for e in exprs if self.rig_name in e.name()]
        return exprs

    def delete_expressions(self, filter_by_rig: bool = True) -> None:
        """Delete expression nodes associated with this rig.

        Parameters:
            filter_by_rig (bool): If True, only delete expressions whose name contains the rig name.
                                If False, delete all expressions under the control.
        """
        exprs = self.get_expressions(filter_by_rig=filter_by_rig)
        if exprs:
            pm.delete(exprs)
            self.logger.info(
                f"Deleted {len(exprs)} expressions for rig: {self.rig_name}"
            )
        else:
            self.logger.info(f"No expressions found to delete for rig: {self.rig_name}")

    @CoreUtils.undoable
    def rig_rotation(
        self,
        movement_axis: str = "translateZ",
        rotation_axis: str = "rotateX",
        wheel_height: float = 1.0,
        invert_rotation: bool = False,
        wheels: Optional[List["pm.nodetypes.Transform"]] = None,
    ) -> None:
        """
        Rig wheels to rotate based on control movement.
        Only rigs specified wheels. Raises if none are passed.

        Args:
            movement_axis (str): Axis of control movement.
            rotation_axis (str): Axis of wheel rotation.
            wheel_height (float): Wheel height (used to compute circumference).
            invert_rotation (bool): Invert the rotation direction.
            wheels (Optional[List[pm.nodetypes.Transform]]): Specific wheels to rig.
        """
        if wheels is None:
            raise ValueError("No wheels specified. You must pass wheels to rig.")

        radius = wheel_height / 2.0
        if radius <= 0:
            raise ValueError(f"Invalid wheel height: {wheel_height}")

        circumference = 2 * 3.14159 * radius
        sign = -1 if invert_rotation else 1

        for wheel in wheels:
            attr = wheel.attr(rotation_axis)

            # Disconnect any existing incoming connections
            if attr.isConnected():
                for src in attr.listConnections(p=True, s=True, d=False):
                    pm.disconnectAttr(src, attr)

            # Delete any previous expression node tied to this wheel
            expr_name = f"{self.rig_name}_{wheel.name()}_expr"
            if pm.objExists(expr_name):
                pm.delete(expr_name)

            # Build new expression
            expr_text = (
                f"float $distance = {self.control}.{movement_axis};\n"
                f"float $rotation = ($distance / {circumference}) * 360 * {sign};\n"
                f"{wheel}.{rotation_axis} = $rotation;\n"
            )

            expr_node = pm.expression(s=expr_text, name=expr_name)

            # Optional: log for debug
            self.logger.debug(
                f"Rigged wheel: {wheel.name()} with expression: {expr_node.name()}"
            )

        self.logger.info(
            f"Wheel rig rotation updated for wheels: {[w.name() for w in wheels]}"
        )


class WheelRigSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        # Use the actual UI name loaded from the wheel_rig.ui file
        # Avoid creating a placeholder UI ("wheel_rig_slots") which has no widgets
        self.ui = self.sb.loaded_ui.wheel_rig

        # 1) update placeholder right away
        self._selection_job = pm.scriptJob(
            event=["SelectionChanged", self.update_rig_name_placeholder],
            protected=True,
        )
        self.update_rig_name_placeholder()

        # 2) cleanup on actual window close
        self.ui.on_close.connect(self.cleanup)

    @property
    def rig_name(self) -> str:
        """Get the rig name from the text box."""
        return self.ui.txt000.text()

    @rig_name.setter
    def rig_name(self, name: str):
        self.ui.txt000.setText(name)

    @property
    def movement_axis(self) -> str:
        """Get the movement axis from the combo box."""
        axis_map = {
            0: "translateX",
            1: "translateY",
            2: "translateZ",
        }
        return axis_map.get(self.ui.cmb000.currentIndex(), "translateZ")

    @property
    def rotation_axis(self) -> str:
        """Get the rotation axis from the combo box."""
        axis_map = {
            0: "rotateX",
            1: "rotateY",
            2: "rotateZ",
        }
        return axis_map.get(self.ui.cmb001.currentIndex(), "rotateX")

    def resolve_selection(
        self,
    ) -> Tuple["pm.nodetypes.Transform", List["pm.nodetypes.Transform"]]:
        """Resolve the current selection into control and wheels.

        Returns:
            Tuple of (control, list of wheels).
        Raises:
            ValueError if selection is invalid.
        """
        # Note: "wheels" here can be any transform you wish to rotate (e.g. locators),
        # not necessarily the wheel geometry itself. The wheel geo can be driven by
        # these transforms downstream if you don't want to rotate the mesh directly.
        sel = pm.selected(flatten=True)
        if len(sel) < 2:
            raise ValueError("Select a control followed by one or more wheel objects.")

        control = NodeUtils.get_transform_node(sel[0])
        wheels = NodeUtils.get_transform_node(sel[1:])

        if not control or not all(wheels):
            raise ValueError(
                "Invalid selection. Make sure all selected objects are valid transforms."
            )

        return control, wheels

    def set_wheel_height(self):
        """Get the wheel height from the selected object's bounding box."""
        selected = pm.selected(flatten=True)
        try:
            obj = selected[0]
        except IndexError:
            pm.warning("Select a single object to determine wheel height.")
            return

        bbox = pm.xform(obj, q=True, bb=True)
        height = bbox[4] - bbox[1]

        self.ui.s000.setValue(height)

    def s000_init(self, widget):
        """Initialize the wheel height slider."""
        widget.menu.add(
            "QPushButton",
            setText="Get Height",
            setObjectName="b010",
            setToolTip="Determine wheel height using the selected object's bounding box.",
        )
        widget.menu.b010.clicked.connect(self.set_wheel_height)

    def update_rig_name_placeholder(self):
        """Update the rig name placeholder based on first selected object."""
        selected = pm.selected(flatten=True)
        if not selected:
            return

        selected.sort(key=lambda x: x.name().lower())  # Sort alphabetically
        control = selected[0]

        default_name = f"{control.name()}_wheel_rig"
        self.ui.txt000.setPlaceholderText(default_name)

    def cleanup(self):
        """Kill the scriptJob if it exists."""
        if hasattr(self, "_selection_job") and pm.scriptJob(exists=self._selection_job):
            pm.scriptJob(kill=self._selection_job, force=True)

    @property
    def wheel_rig(self) -> Optional[WheelRig]:
        """Get or create the wheel rig attached to the selected control."""
        try:
            return self._wheel_rig
        except AttributeError:
            control, wheels = self.resolve_selection()
            rig_name = self.rig_name or f"{control.name()}_wheel_rig"

            existing = getattr(control, "rig", None)
            if isinstance(existing, WheelRig):
                # Reuse and rename if needed
                existing.rig_name = rig_name
                self._wheel_rig = existing
                print(f"Reusing existing wheel rig: {self._wheel_rig.rig_name}")
            else:
                # Create new and attach to control.rig via WheelRig.__init__
                self._wheel_rig = WheelRig(
                    control,
                    wheels,
                    rig_name=rig_name,
                    freeze_transforms=self.ui.chk010.isChecked(),
                )
                print(f"Created new wheel rig: {self._wheel_rig.rig_name}")

            return self._wheel_rig

    @CoreUtils.undoable
    def b000(self):
        """Create or update Wheel Rig."""
        wheel_rig = self.wheel_rig
        if not wheel_rig:
            return

        _, wheels = self.resolve_selection()
        wheel_rig.rig_rotation(
            movement_axis=self.movement_axis,
            rotation_axis=self.rotation_axis,
            wheel_height=self.ui.s000.value(),
            invert_rotation=self.ui.chk000.isChecked(),
            wheels=wheels,
        )


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("wheel_rig", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
