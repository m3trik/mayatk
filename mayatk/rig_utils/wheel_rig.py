# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Optional, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.node_utils import NodeUtils
from mayatk.xform_utils import XformUtils
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

    @CoreUtils.undoable
    def rig_rotation(
        self,
        movement_axis: str = "translateZ",
        rotation_axis: str = "rotateX",
        wheel_height: float = 1.0,
        invert_rotation: bool = False,
    ) -> None:
        """
        Create a rotation expression for the wheels based on control movement.

        Parameters:
            movement_axis (str): Axis of control movement (default: "translateZ").
                valid options: "translateX", "translateY", "translateZ".
            rotation_axis (str): Axis of wheel rotation (default: "rotateX").
                valid options: "rotateX", "rotateY", "rotateZ".
            wheel_height (float): Height of the wheels (default: 1.0).
            invert_rotation (bool): Invert rotation direction (default: False).
        """
        radius = wheel_height / 2.0
        circumference = 2 * 3.14159 * radius
        sign = -1 if invert_rotation else 1

        expr = f"""
        float $distance = {self.control}.{movement_axis};
        float $rotation = ($distance / {circumference}) * 360 * {sign};
        """
        for wheel in self.wheels:
            expr += f"{wheel}.{rotation_axis} = $rotation;\n"

        pm.expression(s=expr)
        self.logger.info(f"Wheel rig created for: {self.rig_name}")


class WheelRigSlots:
    def __init__(self, **kwargs):
        self.sb = kwargs.get("switchboard")
        self.ui = self.sb.loaded_ui.wheel_rig

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

    @property
    def wheel_rig(self) -> Optional[WheelRig]:
        """Get the wheel rig from the control object."""
        try:
            return self._wheel_rig
        except AttributeError:
            try:
                sel = pm.selected(flatten=True)
                control, wheels = sel[0], sel[1:]
            except IndexError:
                self.sb.message_box(
                    "Select a control followed by one or more wheel objects."
                )
                return

            if not self.rig_name:
                self.rig_name = f"{control.name()}_wheel_rig"

            wheel_rig = WheelRig(
                control,
                wheels,
                rig_name=self.rig_name,
                freeze_transforms=self.ui.chk010.isChecked(),
            )
            self._wheel_rig = wheel_rig
            return wheel_rig

    @CoreUtils.undoable
    def b000(self):
        """Create Wheel Rig."""
        wheel_rig = self.wheel_rig
        if not wheel_rig:
            return

        wheel_rig.rig_rotation(
            movement_axis=self.movement_axis,
            rotation_axis=self.rotation_axis,
            wheel_height=self.ui.s000.value(),
            invert_rotation=self.ui.chk000.isChecked(),
        )

        self.sb.message_box(f"Wheel rig created: {self.wheel_rig.rig_name}")


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("wheel_rig", reload=True)
    ui.header.config_buttons(hide_button=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
