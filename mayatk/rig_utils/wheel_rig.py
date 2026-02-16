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
        control (pm.nodetypes.Transform): The control object.
        wheels (List[pm.nodetypes.Transform]): Wheel transforms.
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
        stored_name = (
            self.control.getAttr("wheelRigId")
            if self.control.hasAttr("wheelRigId")
            else None
        )
        self._rig_name = (
            rig_name or stored_name or Naming.generate_unique_name("wheel_rig")
        )

        # Persist ID
        if not self.control.hasAttr("wheelRigId"):
            self.control.addAttr("wheelRigId", dt="string")
        self.control.wheelRigId.set(self._rig_name)

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
        """Return all expression nodes connected to the control.

        Parameters:
            filter_by_rig (bool): If True, only return expressions whose name contains the rig name.

        Returns:
            List of expression nodes.
        """
        # Expressions read from control, so they are destinations of control's connections.
        exprs = self.control.listConnections(type="expression") or []
        exprs = list(set(exprs))

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
        rotation_axis: Optional[str] = None,
        wheel_height: float = 1.0,
        wheels: Optional[List["pm.nodetypes.Transform"]] = None,
    ) -> None:
        """
        Rig wheels to rotate based on control movement.
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

            if not self.control.hasAttr(candidate):
                # Found a free slot, create it
                self.control.addAttr(candidate, k=True, dv=wheel_height, min=0.001)
                height_attr_name = candidate
                break

            # Attribute exists, check if value matches our target height
            existing_val = self.control.attr(candidate).get()
            if abs(existing_val - wheel_height) < epsilon:
                # Close enough, reuse this attribute
                # Update it exactly to be sure
                self.control.attr(candidate).set(wheel_height)
                height_attr_name = candidate
                break

            # Attribute exists but value is different -> Try next index
            suffix_idx += 1

        # Global control attributes (shared across all wheel groups)
        if not self.control.hasAttr("enableRotation"):
            self.control.addAttr("enableRotation", k=True, dv=1.0, min=0.0, max=1.0)
        if not self.control.hasAttr("spinDirection"):
            self.control.addAttr("spinDirection", k=True, dv=1.0)

        # Ensure attributes are exposed in the Channel Box
        for attr_name in [height_attr_name, "enableRotation", "spinDirection"]:
            try:
                self.control.attr(attr_name).setKeyable(True)
            except Exception:
                pass  # Ignore if already keyable or locked logic interferes

        # Helper to get the world-space vector for the chosen rotation axis
        def get_axis_vector(node, axis_name):
            idx = {"rotateX": 0, "rotateY": 1, "rotateZ": 2}.get(axis_name, 0)
            # transform.getMatrix(ws=True) returns a matrix where rows 0,1,2 are x,y,z axes
            return pm.dt.Vector(node.getMatrix(ws=True)[idx][:3])

        # Get the control's reference vector for alignment check
        control_vec = get_axis_vector(self.control, rotation_axis)

        for wheel in wheels:
            attr = wheel.attr(rotation_axis)

            # Determine auto-flip based on alignment with control
            # If the wheel is flipped 180 (e.g. right side vs left side), dot product will be negative
            wheel_vec = get_axis_vector(wheel, rotation_axis)
            dot_prod = control_vec.dot(wheel_vec)
            auto_flip = -1.0 if dot_prod < 0 else 1.0

            # Disconnect any existing incoming connections
            if attr.isConnected():
                for src in attr.listConnections(p=True, s=True, d=False):
                    pm.disconnectAttr(src, attr)

            # Delete any previous expression node tied to this wheel
            expr_name = f"{self.rig_name}_{wheel.name()}_expr"
            if pm.objExists(expr_name):
                pm.delete(expr_name)

            # Build new expression
            # Incorporated auto_flip constant to handle symmetry automatically
            # Uses the specifically resolved height_attr_name
            expr_text = (
                f"float $distance = {self.control}.{movement_axis};\n"
                f"float $height = {self.control}.{height_attr_name};\n"
                f"float $enable = {self.control}.enableRotation;\n"
                f"float $dir = {self.control}.spinDirection;\n"
                f"float $circumference = 3.14159 * $height;\n"
                f"float $auto_flip = {auto_flip};\n"
                f"float $rotation = ($distance / $circumference) * 360 * $dir * $auto_flip;\n"
                f"{wheel}.{rotation_axis} = $rotation * $enable;\n"
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

        # Populate movement axis combo box (remove negative options)
        self.ui.cmb000.clear()
        self.ui.cmb000.addItems(
            ["Movement Axis: X", "Movement Axis: Y", "Movement Axis: Z"]
        )
        self.ui.cmb000.setCurrentIndex(2)  # Default to Z

        # Remove redundant UI elements
        for widget_name in ["chk000", "cmb001"]:
            if hasattr(self.ui, widget_name):
                getattr(self.ui, widget_name).deleteLater()

        # 1) update placeholder right away
        self._selection_job = pm.scriptJob(
            event=["SelectionChanged", self.update_rig_name_placeholder],
            protected=True,
        )
        self.update_rig_name_placeholder()

        # 2) cleanup on actual window close
        self.ui.on_close.connect(self.cleanup)

        # 3) Setup Tooltips
        self.ui.b000.setToolTip(
            "<p><b>Rig Rotation Behavior:</b></p>"
            "<p>1. Select <b>Control</b> object first.</p>"
            "<p>2. Shift-Select one or more <b>Wheel</b> objects.</p>"
            "<p>3. Run to create or update the rig.</p>"
            "<br>"
            "<p><b>Features:</b></p>"
            "<ul>"
            "<li>Adds <tt>wheelHeight</tt> and <tt>enableRotation</tt> attributes to Control for animation.</li>"
            "<li>Stores <tt>wheelRigId</tt> on Control to allow unsafe updates/re-running without duplication.</li>"
            "<li>Automatically detects and updates existing expressions if run again on the same control.</li>"
            "</ul>"
        )

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
        # Determine dimension based on inferred rotation axis
        # Move Z -> Rotate X -> Diameter is Y or Z (Height/Depth)
        # Move X -> Rotate Z -> Diameter is X or Y (Width/Height)
        # Move Y -> Rotate Y -> Diameter is X or Z

        # We will use the max dimension perpendicular to the rotation axis to ensure we get diameter
        move_axis = self.movement_axis

        if "X" in move_axis:  # Moving X, Rotating Z
            # Perpendiculars are X and Y.
            # bbox size: X=(3-0), Y=(4-1), Z=(5-2)
            width = bbox[3] - bbox[0]
            height = bbox[4] - bbox[1]
            wheel_size = max(width, height)

        elif "Y" in move_axis:  # Moving Y, Rotating Y
            # Perpendiculars X and Z
            width = bbox[3] - bbox[0]
            depth = bbox[5] - bbox[2]
            wheel_size = max(width, depth)

        else:  # Moving Z, Rotating X (Default)
            # Perpendiculars Y and Z
            height = bbox[4] - bbox[1]
            depth = bbox[5] - bbox[2]
            wheel_size = max(height, depth)

        self.ui.s000.setText(str(round(wheel_size, 3)))

    def s000_init(self, widget):
        """Initialize the wheel height slider."""
        widget.option_box.menu.add(
            "QPushButton",
            setText="Get Wheel Size",
            setObjectName="b010",
            setToolTip="Determine wheel diameter from the selected object's bounding box,\nbased on the current movement axis.",
        )
        widget.option_box.menu.b010.clicked.connect(self.set_wheel_height)

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

            # Check persistent ID on control to recover correct name
            if control.hasAttr("wheelRigId"):
                rig_name = control.wheelRigId.get()
                self.rig_name = rig_name  # Sync UI
            else:
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
        try:
            wheel_rig = self.wheel_rig
        except ValueError as e:
            self.sb.message_box(str(e))
            return

        if not wheel_rig:
            return

        _, wheels = self.resolve_selection()

        wheel_rig.rig_rotation(
            movement_axis=self.movement_axis,
            wheel_height=float(self.ui.s000.text()),
            wheels=wheels,
        )


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.mayatk_ui_manager import UiManager

    ui = UiManager.instance().get("wheel_rig", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
