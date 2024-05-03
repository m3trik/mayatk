import random

try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)

# from this package:
from mayatk import core_utils


class ColorUtils:
    @staticmethod
    def check_and_create_material(obj):
        """Ensure the object has a shading group and material connected."""
        shading_groups = pm.listConnections(obj, type="shadingEngine")
        if not shading_groups:
            # Create a new material and shading group if not exists
            material = pm.shadingNode("lambert", asShader=True, name=f"{obj}_material")
            shading_group = pm.sets(
                renderable=True, noSurfaceShader=True, empty=True, name=f"{material}_SG"
            )
            pm.connectAttr(f"{material}.outColor", f"{shading_group}.surfaceShader")
            return material
        else:
            return pm.ls(pm.listConnections(shading_groups[0]), materials=True)[0]

    @classmethod
    def set_color_attribute(cls, obj, color, attr_type, force=False):
        """Applies color based on the attribute type specified, optionally overriding attribute locks."""

        def handle_attribute(attribute, value=None, action="set"):
            """Handles attribute modification with optional lock override."""
            locked = pm.getAttr(attribute, lock=True)
            if locked and force:
                pm.setAttr(attribute, lock=False)

            if action == "set":
                if isinstance(value, tuple) and attribute.endswith("Color"):
                    pm.setAttr(attribute, *value, type="double3")
                else:
                    pm.setAttr(attribute, value)

            if locked and force:
                pm.setAttr(attribute, lock=True)

        if attr_type == "outliner":
            handle_attribute(f"{obj}.useOutlinerColor", 1)
            handle_attribute(f"{obj}.outlinerColor", (color[0], color[1], color[2]))
        elif attr_type == "vertex":
            # Direct call, as it's not affected by locking in the same way
            pm.polyColorPerVertex(
                obj, rgb=(color[0], color[1], color[2]), colorDisplayOption=True
            )
        elif attr_type == "material":
            material = cls.check_and_create_material(obj)
            handle_attribute(f"{material}.color", (color[0], color[1], color[2]))
        elif attr_type == "wireframe":
            handle_attribute(f"{obj}.overrideEnabled", 1)
            handle_attribute(f"{obj}.overrideRGBColors", 1)
            handle_attribute(f"{obj}.overrideColorRGB", (color[0], color[1], color[2]))

    @staticmethod
    def get_material_color(obj):
        """Gets the color of the object's material."""
        shading_groups = pm.listConnections(obj, type="shadingEngine")
        if not shading_groups:
            return None  # No material connected
        materials = pm.ls(pm.listConnections(shading_groups[0]), materials=True)
        if not materials:
            return None  # No material found
        color = pm.getAttr(f"{materials[0]}.color")[0]
        return color

    @staticmethod
    def get_wireframe_color(obj, normalize=False):
        """Gets the wireframe color of the given object.

        Parameters:
            obj: The object (or its name) from which to retrieve the wireframe color.
            normalize (bool): If False, scales the normalized color values (0.0 to 1.0) up to 0-255. If True, returns the color normalized.

        Returns:
            A tuple of the RGB values of the wireframe color, either as 8-bit values (default) or normalized if normalize is True. Returns None if the color is not set or the object does not exist.
        """
        # Ensure obj is a PyNode object if not already
        obj = pm.PyNode(obj) if not isinstance(obj, pm.nt.Transform) else obj

        # Check if the drawing overrides (which include wireframe color) are enabled at the transform level
        if not pm.getAttr(f"{obj}.overrideEnabled"):
            return None  # Drawing overrides not enabled, no custom wireframe color set

        # Check if the RGB color is enabled for overrides
        if not pm.getAttr(f"{obj}.overrideRGBColors"):
            return (
                None  # RGB color not used, might be using default color index instead
            )

        # Retrieve and return the RGB values from the transform node directly
        color = pm.getAttr(f"{obj}.overrideColorRGB")

        # Convert normalized values to 8-bit integers if normalize is False
        if not normalize:
            color = tuple(int(c * 255) for c in color)

        return color

    @staticmethod
    def get_vertex_color(obj, vertex_id):
        """Gets the color of a specific vertex on the object."""
        colors = pm.polyColorPerVertex(f"{obj}.vtx[{vertex_id}]", query=True, rgb=True)
        return colors if colors else None

    @staticmethod
    def set_vertex_color(objects, color):
        """Applies the specified color to the object's vertices."""
        for obj in pm.ls(objects, long=True):
            pm.polyColorPerVertex(
                obj, rgb=(color[0], color[1], color[2]), colorDisplayOption=True
            )

    @staticmethod
    def get_color_difference(color1, color2):
        """Calculate the average difference between two RGB colors."""
        return sum(abs(c1 - c2) for c1, c2 in zip(color1, color2)) / 3.0


class ColorManager(ColorUtils):
    @classmethod
    def apply_color(
        cls,
        objects,
        color=None,
        use_material_color=False,
        apply_to_vertex=False,
        apply_to_wireframe=False,
        apply_to_outliner=False,
    ):
        """Applies color based on given criteria to objects."""
        for obj in pm.ls(objects, long=True):
            if color is None:  # Generate a random color if not specified
                color = (random.random(), random.random(), random.random())

            if apply_to_vertex:
                cls.set_color_attribute(obj, color, attr_type="vertex", force=True)
            if apply_to_wireframe:
                cls.set_color_attribute(obj, color, attr_type="wireframe", force=True)
            if apply_to_outliner:
                cls.set_color_attribute(obj, color, attr_type="outliner", force=True)
            if use_material_color:
                cls.set_color_attribute(obj, color, attr_type="material", force=True)

    @classmethod
    def get_objects_by_color(
        cls,
        target_color,
        threshold=0.1,
        check_material_color=False,
        check_vertex_color=False,
        check_wireframe_color=False,
        check_outliner_color=False,
    ):
        """Select objects by color, with optional checks for material, vertex, wireframe, and outliner colors."""
        matching_objects = []

        for obj in pm.ls(geometry=True, type="transform", long=True):
            # Check material color
            if check_material_color:
                for shading_engine in obj.listConnections(type="shadingEngine"):
                    for material in shading_engine.listConnections():
                        if material.hasAttr("color"):
                            material_color = material.color.get()
                            if (
                                cls.get_color_difference(material_color, target_color)
                                <= threshold
                            ):
                                matching_objects.append(obj)
                                continue  # Skip other checks if a match is found

            # Check wireframe color
            if (
                check_wireframe_color
                and obj.overrideEnabled.get()
                and obj.overrideRGBColors.get()
            ):
                wireframe_color = obj.overrideColorRGB.get()
                if cls.get_color_difference(wireframe_color, target_color) <= threshold:
                    matching_objects.append(obj)
                    continue

            # Check outliner color
            if check_outliner_color and obj.useOutlinerColor.get():
                outliner_color = obj.outlinerColor.get()
                if cls.get_color_difference(outliner_color, target_color) <= threshold:
                    matching_objects.append(obj)
                    continue

            # Check vertex color
            if check_vertex_color:
                vtx_colors = pm.polyColorPerVertex(
                    obj, query=True, allVertices=True, rgb=True
                )
                if vtx_colors:
                    average_vtx_color = [
                        sum(c) / len(vtx_colors) for c in zip(*vtx_colors)
                    ]
                    if (
                        cls.get_color_difference(average_vtx_color, target_color)
                        <= threshold
                    ):
                        matching_objects.append(obj)

        return matching_objects

    @classmethod
    def reset_colors(
        cls,
        objects,
        reset_outliner=True,
        reset_wireframe=True,
        reset_vertex=True,
        reset_material=True,
    ):
        """Resets colors to default for given objects, with options to specify which color types to reset."""
        for obj in pm.ls(objects, long=True):
            if reset_outliner:
                # Reset outliner color
                pm.setAttr(f"{obj}.useOutlinerColor", 0)

            if reset_wireframe or reset_vertex:
                # Reset drawing overrides for both wireframe and vertex colors
                pm.setAttr(f"{obj}.overrideEnabled", 0)
                # Additional reset actions for wireframe and vertex colors can be added here if necessary

            if reset_material:
                # Reset material color, if a material is directly connected to the object or its shading group
                shading_groups = pm.listConnections(obj, type="shadingEngine")
                if shading_groups:
                    for sg in shading_groups:
                        materials = pm.ls(pm.listConnections(sg), materials=True)
                        for mat in materials:
                            # Resetting material color to default may vary depending on material type
                            # Here we reset it to a default value, like grey for lambert
                            pm.setAttr(f"{mat}.color", 0.5, 0.5, 0.5, type="double3")

        # For vertex colors, since they're more granular, a specific approach is needed
        if reset_vertex:
            cls.reset_vertex_colors(objects)

    @staticmethod
    def reset_vertex_colors(objects):
        """Resets vertex colors for the given object(s), handling potential errors gracefully."""
        transforms = pm.ls(objects, type="transform", long=True)
        shapes = pm.listRelatives(transforms, children=True, shapes=True)

        if shapes:
            for shape in shapes:
                if pm.nodeType(shape) == "mesh":
                    try:  # Ensure operation on vertices with colorSet management
                        colorSets = pm.polyColorSet(
                            shape, query=True, allColorSets=True
                        )
                        if colorSets:
                            for colorSet in colorSets:
                                pm.polyColorSet(shape, delete=True, colorSet=colorSet)
                    except RuntimeError as e:
                        print(f"Error removing vertex colors from {shape}: {e}")


# -----------------------------------------------------------------------------


class ColorManagerSlots(ColorManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.sb = self.switchboard()
        self.ui = self.sb.color_manager

        # Assuming create_button_groups returns a QButtonGroup
        self.button_grp = self.sb.create_button_groups(self.ui, "chk000-11")
        for button in self.button_grp.buttons():
            button.settings = self.ui.settings
        self.ui.chk000.setChecked(True)

    @property
    def selected_objects(self):
        """Return the currently selected objects, or an empty list if no objects are selected."""
        objects = pm.selected()
        if not objects:
            self.sb.message_box("No objects selected.")
        return objects

    @property
    def selected_button(self):
        """Return the currently selected button in the button group."""
        for button in self.button_grp.buttons():
            if button.isChecked():
                return button
        return None

    @property
    def target_color(self):
        """Return the color of the selected button, or None if no button is selected."""
        selected_btn = self.selected_button
        if selected_btn:
            color = selected_btn.color
            if isinstance(color, self.sb.QtGui.QColor):
                color = (color.redF(), color.greenF(), color.blueF())
            return color
        return None

    def b000(self):
        """Reset Colors"""
        # Check if the Alt key is down
        if self.sb.app.keyboardModifiers() == self.sb.QtCore.Qt.ControlModifier:
            objects = pm.ls(geometry=True, type="transform", long=True)
        else:
            objects = self.selected_objects
        if not objects:
            return
        self.reset_colors(objects)

    def b001(self):
        """Apply selected color to selected objects."""
        objects = self.selected_objects
        if not objects or not self.target_color:
            return  # handle_no_selection will log the necessary warning

        apply_to_wireframe = self.ui.chk012.isChecked()
        apply_to_outliner = self.ui.chk013.isChecked()

        ColorManager.apply_color(
            objects,
            color=self.target_color,
            apply_to_wireframe=apply_to_wireframe,
            apply_to_outliner=apply_to_outliner,
        )

    def b002(self):
        """Select objects by the currently selected color."""
        if not self.target_color:
            print("No color was selected.")
            return

        found_objects = self.get_objects_by_color(
            self.target_color, check_wireframe_color=True, check_outliner_color=True
        )
        pm.select(found_objects)  # Select all matching objects

    def b003(self):
        if len(self.selected_objects) > 1:
            self.sb.message_box("Please select exactly one object.")
            return

        # Fetch the wireframe color of the selected object
        selected_object = self.selected_objects[0]
        wireframe_color = self.get_wireframe_color(selected_object)
        # Ensure the wireframe color was successfully retrieved
        if wireframe_color is None:
            print("Failed to retrieve wireframe color.")
            return

        # Apply the wireframe color to the selected button
        self.selected_button.color = wireframe_color


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = core_utils.CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "color_manager.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=ColorManagerSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(
        Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True
    )
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(minimize_button=True, hide_button=True)

    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
