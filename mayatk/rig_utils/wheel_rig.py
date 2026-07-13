# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    cmds = None
    om = None

from typing import List, Tuple, Optional, Union

import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt, kbd

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
        stored_name = (
            cmds.getAttr(f"{self.control}.wheelRigId")
            if cmds.attributeQuery("wheelRigId", node=str(self.control), exists=True)
            else None
        )
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
            XformUtils.freeze_transforms(self.control, translate=True)
            XformUtils.freeze_transforms(self.wheels, translate=True)

    @property
    def rig_name(self) -> str:
        return self._rig_name

    @rig_name.setter
    def rig_name(self, name: str):
        self._rig_name = name
        self.logger.debug(f"Rig name set to: {self._rig_name}")

    def get_expressions(
        self, filter_by_rig: bool = False
    ) -> List[object]:
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
            exprs = [e for e in exprs if self.rig_name in e.split('|')[-1].split(':')[-1]]
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
                cmds.addAttr(str(self.control), longName=candidate, k=True, dv=wheel_height, min=0.001)
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
        if not cmds.attributeQuery("enableRotation", node=str(self.control), exists=True):
            cmds.addAttr(str(self.control), longName="enableRotation", k=True, dv=1.0, min=0.0, max=1.0)
        if not cmds.attributeQuery("spinDirection", node=str(self.control), exists=True):
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
        self._init_tooltips()

        # 4) World-space mode flag (toggled via header menu)
        self._use_world_space = False

    def header_init(self, widget):
        """Configure header menu with mode toggle and instructions."""
        widget.menu.add("Separator", setTitle="Mode")
        # Local vs World Space is a two-valued mode, not a modifier \u2014 a combobox
        # names both states; extend with a third space here without a relayout.
        cmb_space = widget.menu.add(
            "QComboBox",
            setObjectName="cmb_space",
            setToolTip=fmt(
                title="Space",
                bullets=[
                    "<b>Local</b> (default) \u2014 reads the driver's local "
                    "translate directly. Simpler, and sufficient when the "
                    "driver itself is animated.",
                    "<b>World Space</b> \u2014 rotation reads a decomposeMatrix on "
                    "the driver's <i>worldMatrix</i>, so movement inherited from "
                    "parent transforms is captured.",
                ],
            ),
        )
        cmb_space.addItems(["Local", "World Space"])
        cmb_space.setCurrentText("Local")  # preserve prior default (checkbox off = local)
        cmb_space.currentTextChanged.connect(self._on_space_changed)

        widget.set_help_text(
            fmt(
                title="Wheel Rig",
                body="Drive wheel rotation from a control's linear movement. "
                "Wheel diameter (Wheel Height) and travel axis determine the "
                "rotation speed.",
                sections=[
                    ("Selection order", [
                        "Select one or more <b>wheel</b> objects "
                        "(or locators driving them).",
                        f"{kbd('Shift')}-select the <b>driver / control</b> "
                        "object last.",
                        "Click <b>Rig Rotation</b>.",
                    ]),
                    ("Settings", [
                        "<b>Axis</b> \u2014 which translation axis drives which "
                        "rotation axis. e.g. <i>Move Z \u2192 Rotate X</i> means "
                        "forward Z movement produces pitch on X.",
                        "<b>Wheel Height</b> \u2014 diameter used to compute "
                        "rotation speed. Use <b>Get Wheel Size</b> (slider "
                        "option box) to auto-detect from the bounding box.",
                    ]),
                    ("Modes", [
                        "<b>Local</b> (default) \u2014 reads the driver's local "
                        "translate. Best when the driver itself is animated.",
                        "<b>World Space</b> \u2014 uses a decomposeMatrix node so "
                        "parent transform movement is captured. Toggle via "
                        "the header menu.",
                    ]),
                    ("Driver attributes added", [
                        "<b>wheelHeight</b> \u2014 animation-friendly diameter control.",
                        "<b>enableRotation</b> \u2014 on/off toggle (0..1).",
                        "<b>spinDirection</b> \u2014 flip direction (+1 / -1).",
                    ]),
                ],
                notes=[
                    "Re-running on the same driver updates the existing "
                    "expression in place (a <i>wheelRigId</i> string attribute "
                    "on the driver acts as the idempotency key \u2014 no duplicates).",
                ],
            )
        )

    def _on_space_changed(self, text: str):
        self._use_world_space = text == "World Space"

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
    ) -> Tuple["object", List["object"]]:
        """Resolve the current selection into control (driver) and wheels.

        The driver is expected to be the **last** selected object.
        All preceding objects are treated as wheel transforms.

        Returns:
            Tuple of (control, list of wheels).
        Raises:
            ValueError if selection is invalid.
        """
        sel = cmds.ls(sl=True, flatten=True)
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
        selected = cmds.ls(sl=True, flatten=True)
        try:
            obj = selected[0]
        except IndexError:
            cmds.warning("Select a single object to determine wheel height.")
            return

        bbox = cmds.xform(obj, q=True, bb=True)
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

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and action."""
        ui = self.ui

        ui.txt000.setToolTip(
            fmt(
                title="Rig Name",
                body="Base name for the expression (and its decomposeMatrix "
                "node, in World Space mode) that this rig creates.",
                notes=["Empty = derived from the driver (control) name."],
            )
        )
        ui.s000.setToolTip(
            fmt(
                title="Wheel Height",
                body="Wheel diameter, used to convert the driver's travel into "
                "rotation — a larger wheel turns slower over the same distance.",
                notes=[
                    "Use the <b>Get Wheel Size</b> option box to auto-detect "
                    "this from the selected object's bounding box.",
                ],
            )
        )
        ui.cmb000.setToolTip(
            fmt(
                title="Movement → Rotation Axis",
                body="Which translation axis drives which rotation axis.",
                rows=[
                    ("Move X → Rotate Z", "sideways X travel rolls on Z"),
                    ("Move Y → Rotate Y", "vertical Y travel spins on Y"),
                    ("Move Z → Rotate X", "forward Z travel pitches on X (default)"),
                ],
            )
        )
        ui.chk010.setToolTip(
            fmt(
                title="Freeze Transforms",
                body="Freezes <b>translation</b> on the driver and wheel(s) "
                "before rigging, so travel is measured from a clean zero.",
                notes=[
                    "Rotation is preserved (needed for mirrored-wheel "
                    "auto-flip).",
                ],
            )
        )
        ui.b000.setToolTip(
            fmt(
                title="Rig Rotation",
                body="Creates (or updates) the expression that spins the "
                "wheel(s) from the driver's movement.",
                steps=[
                    "Select one or more <b>wheel</b> objects.",
                    "<b>Shift</b>-select the <b>driver / control</b> last.",
                    "Press <b>Rig Rotation</b>.",
                ],
                notes=[
                    "Freeze or correctly set transforms first (or tick "
                    "<b>Freeze Transforms</b>).",
                    "Re-running on the same driver updates the existing "
                    "expression in place — no duplicates.",
                ],
            )
        )

    def s000_init(self, widget):
        """Initialize the wheel height slider."""
        widget.option_box.menu.add(
            "QPushButton",
            setText="Get Wheel Size",
            setObjectName="b010",
            setToolTip=fmt(
                title="Get Wheel Size",
                body="Sets <b>Wheel Height</b> from the selected object's "
                "bounding box, based on the current movement axis.",
            ),
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
        selected = cmds.ls(sl=True, flatten=True)
        if not selected:
            return

        control = selected[-1]

        default_name = f"{control.split('|')[-1].split(':')[-1]}_wheel_rig"
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
            if not rig.control or not cmds.objExists(rig.control):
                self._wheel_rig = None
                raise AttributeError
            return rig
        except AttributeError:
            try:
                control, wheels = self.resolve_selection()
            except ValueError:
                return None

            # Check persistent ID on control to recover correct name
            if cmds.attributeQuery("wheelRigId", node=control, exists=True):
                rig_name = cmds.getAttr(f"{control}.wheelRigId")
                self.rig_name = rig_name  # Sync UI
            else:
                rig_name = self.rig_name or f"{control.split('|')[-1].split(':')[-1]}_wheel_rig"

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
