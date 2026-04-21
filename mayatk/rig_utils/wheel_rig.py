# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Optional, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils.script_job_manager import ScriptJobManager
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

        # Clean up the decomposeMatrix utility node
        decomp_name = f"{self.rig_name}_decompose"
        if pm.objExists(decomp_name):
            pm.delete(decomp_name)
            self.logger.info(f"Deleted decomposeMatrix node: {decomp_name}")

    @CoreUtils.undoable
    def rig_rotation(
        self,
        movement_axis: str = "translateZ",
        rotation_axis: Optional[str] = None,
        wheel_height: float = 1.0,
        wheels: Optional[List["pm.nodetypes.Transform"]] = None,
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

        # Determine how to read the control's position for the expression.
        if use_world_space:
            # Create or reuse a decomposeMatrix node for world-space position.
            # This captures parent movement, not just local translate.
            decomp_name = f"{self.rig_name}_decompose"
            if pm.objExists(decomp_name):
                decomp = pm.PyNode(decomp_name)
            else:
                decomp = pm.createNode("decomposeMatrix", name=decomp_name)
                self.control.attr("worldMatrix[0]").connect(
                    decomp.inputMatrix, force=True
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

        # Populate axis combo box showing both movement and rotation axes
        self.ui.cmb000.clear()
        self.ui.cmb000.addItems(
            [
                "Move X \u2192 Rotate Z",
                "Move Y \u2192 Rotate Y",
                "Move Z \u2192 Rotate X",
            ]
        )
        self.ui.cmb000.setCurrentIndex(2)  # Default to Move Z \u2192 Rotate X

        # Remove redundant UI elements
        for widget_name in ["chk000", "cmb001"]:
            if hasattr(self.ui, widget_name):
                getattr(self.ui, widget_name).deleteLater()

        # 1) update placeholder right away
        mgr = ScriptJobManager.instance()
        mgr.subscribe(
            "SelectionChanged",
            self._on_selection_changed,
            owner=self,
        )
        mgr.connect_cleanup(self.ui, owner=self)
        self.update_rig_name_placeholder()

        # 2) cleanup on actual window close
        self.ui.on_close.connect(self.cleanup)

        # 3) Setup Tooltips
        self.ui.b000.setToolTip(
            "<b>Rig Rotation</b><br>"
            "Select wheel objects, then the driver (last), and click to create or update the rig."
        )

        # 4) World-space mode flag (toggled via header menu)
        self._use_world_space = False

    def header_init(self, widget):
        """Configure header menu with mode toggle and instructions."""
        widget.menu.add("Separator", setTitle="Mode")
        chk_ws = widget.menu.add(
            "QCheckBox",
            setText="World Space (decomposeMatrix)",
            setObjectName="chk_world_space",
            setToolTip=(
                "When checked, wheel rotation reads from a decomposeMatrix\n"
                "node connected to the driver's worldMatrix.  This captures\n"
                "movement from parent transforms, not just local translate.\n\n"
                "When unchecked (default), the expression reads the driver's\n"
                "local translate directly \u2014 simpler and sufficient when the\n"
                "driver itself is being animated."
            ),
            setChecked=False,
        )
        chk_ws.toggled.connect(self._on_world_space_toggled)

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Wheel Rig \u2014 Links wheel rotation to a driver's linear movement.\n\n"
                "Selection Order:\n"
                "  1. Select one or more wheel objects (or locators driving them).\n"
                "  2. Shift-select the driver / control object last.\n"
                "  3. Click 'Rig Rotation'.\n\n"
                "Axis Combo:\n"
                "  Choose which translation axis drives which rotation axis.\n"
                "  e.g. 'Move Z \u2192 Rotate X' means forward Z movement\n"
                "  produces pitch rotation on X.\n\n"
                "Wheel Height:\n"
                "  The diameter used to compute rotation speed.\n"
                "  Use 'Get Wheel Size' (slider option box) to auto-detect\n"
                "  from the selected object's bounding box.\n\n"
                "Modes:\n"
                "  \u2022 Local (default): reads driver's local translate.\n"
                "    Best when the driver itself is animated.\n"
                "  \u2022 World Space: uses a decomposeMatrix node so parent\n"
                "    movement is captured.  Enable via the header menu.\n\n"
                "Re-running:\n"
                "  Running the tool again on the same driver updates\n"
                "  the existing expressions without duplication.\n"
                "  A 'wheelRigId' attribute is stored on the driver for this.\n\n"
                "Attributes added to driver:\n"
                "  \u2022 wheelHeight \u2014 animation-friendly diameter control\n"
                "  \u2022 enableRotation \u2014 on/off toggle (0..1)\n"
                "  \u2022 spinDirection \u2014 flip spin direction (+1 / -1)"
            ),
        )

    def _on_world_space_toggled(self, checked: bool):
        self._use_world_space = checked

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
    def rotation_axis(self) -> Optional[str]:
        """Get the rotation axis that corresponds to the selected movement axis."""
        rot_map = {
            0: "rotateZ",  # Move X -> Rotate Z
            1: "rotateY",  # Move Y -> Rotate Y
            2: "rotateX",  # Move Z -> Rotate X
        }
        return rot_map.get(self.ui.cmb000.currentIndex(), "rotateX")

    def resolve_selection(
        self,
    ) -> Tuple["pm.nodetypes.Transform", List["pm.nodetypes.Transform"]]:
        """Resolve the current selection into control (driver) and wheels.

        The driver is expected to be the **last** selected object.
        All preceding objects are treated as wheel transforms.

        Returns:
            Tuple of (control, list of wheels).
        Raises:
            ValueError if selection is invalid.
        """
        sel = pm.selected(flatten=True)
        if len(sel) < 2:
            raise ValueError(
                "Select one or more wheel objects, then the driver (last)."
            )

        control = NodeUtils.get_transform_node(sel[-1])
        wheels = NodeUtils.get_transform_node(sel[:-1])

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

    def _on_selection_changed(self):
        """Handle selection change: invalidate cached rig and update UI."""
        # Discard the cached WheelRig so the property re-resolves from
        # the current selection next time it is accessed.
        self._wheel_rig = None
        self.update_rig_name_placeholder()

    def update_rig_name_placeholder(self):
        """Update the rig name placeholder based on the driver (last selected)."""
        selected = pm.selected(flatten=True)
        if not selected:
            return

        control = selected[-1]

        default_name = f"{control.name()}_wheel_rig"
        self.ui.txt000.setPlaceholderText(default_name)

    def cleanup(self):
        """Unsubscribe from the centralized ScriptJobManager."""
        ScriptJobManager.instance().unsubscribe_all(self)

    @property
    def wheel_rig(self) -> Optional[WheelRig]:
        """Get or create the wheel rig attached to the selected control.

        Returns None if the current selection is invalid, so the property
        is safe for introspection (e.g. ``inspect.getmembers``).
        """
        try:
            rig = self._wheel_rig
            if rig is None:
                raise AttributeError
            # Validate the cached rig's control still exists in the scene.
            # PyMEL stores DAG paths; if the node was renamed, reparented,
            # or deleted the reference goes stale and .name() will raise.
            try:
                rig.control.name()
            except Exception:
                self._wheel_rig = None
                raise AttributeError
            return rig
        except AttributeError:
            try:
                control, wheels = self.resolve_selection()
            except ValueError:
                return None

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
        wheel_rig = self.wheel_rig
        if not wheel_rig:
            self.sb.message_box(
                "Select one or more wheel objects, then the driver (last)."
            )
            return

        _, wheels = self.resolve_selection()

        wheel_rig.rig_rotation(
            movement_axis=self.movement_axis,
            rotation_axis=self.rotation_axis,
            wheel_height=float(self.ui.s000.text()),
            wheels=wheels,
            use_world_space=self._use_world_space,
        )


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("wheel_rig", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
