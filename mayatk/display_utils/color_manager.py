from typing import List, Tuple, Optional
import random

try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)

# from this package:
from mayatk.mat_utils._mat_utils import MatUtils


class ColorUtils:
    @staticmethod
    def assign_material(obj: object, color: Tuple[float, float, float]) -> object:
        """Assigns a material to an object based on the RGB value. Creates the material if it does not exist."""
        # Convert the RGB tuple to a hex string for the material name
        color_name = "_".join(f"{int(c * 255):02X}" for c in color)
        material_name = f"ID_{color_name}"

        # Check if the material already exists
        if not pm.objExists(material_name):
            # Create a new material and shading group
            material = MatUtils.create_mat("lambert", prefix="ID_", name=color_name)
            pm.setAttr(
                f"{material}.color", color[0], color[1], color[2], type="double3"
            )
        else:
            material = pm.PyNode(material_name)

        # Assign the material to the object
        MatUtils.assign_mat(obj, material_name)
        return material

    @classmethod
    def set_color_attribute(
        cls,
        obj: object,
        color: Tuple[float, float, float],
        attr_type: str,
        force: bool = False,
    ) -> None:
        """Applies color based on the attribute type specified, optionally overriding attribute locks."""

        def handle_attribute(
            attribute: pm.general.Attribute,
            value: Optional[Tuple[float, float, float]] = None,
            action: str = "set",
        ) -> None:
            """Handles attribute modification with optional lock override."""
            if not attribute.exists():
                return

            locked = attribute.get(lock=True)
            if locked and force:
                attribute.set(lock=False)

            if action == "set" and value is not None:
                if isinstance(value, tuple) and attribute.type() == "float3":
                    attribute.set(value, type="double3")
                else:
                    attribute.set(value)

            if locked and force:
                attribute.set(lock=True)

        try:
            if attr_type == "outliner":
                if obj.hasAttr("useOutlinerColor"):
                    handle_attribute(obj.attr("useOutlinerColor"), 1)
                else:
                    pm.warning(f"{obj} has no attribute 'useOutlinerColor'.")

                if obj.hasAttr("outlinerColor"):
                    handle_attribute(obj.attr("outlinerColor"), color)
                else:
                    pm.warning(f"{obj} has no attribute 'outlinerColor'.")

            elif attr_type == "vertex":
                shapes = pm.listRelatives(obj, shapes=True, type="mesh")
                if not shapes:
                    pm.warning(
                        f"{obj} does not have a mesh shape for vertex color assignment."
                    )
                    return
                try:
                    pm.polyColorPerVertex(obj, rgb=color, colorDisplayOption=True)
                except RuntimeError as e:
                    pm.warning(f"Error applying vertex color to {obj}: {e}")

            elif attr_type == "material":
                cls.assign_material(obj, color)

            elif attr_type == "wireframe":
                if pm.nodeType(obj) == "lambert":
                    pm.warning(f"{obj} is a material, not a mesh.")
                    return

                if obj.hasAttr("overrideEnabled"):
                    handle_attribute(obj.attr("overrideEnabled"), 1)
                else:
                    pm.warning(f"{obj} has no attribute 'overrideEnabled'.")

                if obj.hasAttr("overrideRGBColors"):
                    handle_attribute(obj.attr("overrideRGBColors"), 1)
                else:
                    pm.warning(f"{obj} has no attribute 'overrideRGBColors'.")

                if obj.hasAttr("overrideColorRGB"):
                    handle_attribute(obj.attr("overrideColorRGB"), color)
                else:
                    pm.warning(f"{obj} has no attribute 'overrideColorRGB'.")

        except Exception as e:
            pm.warning(f"Color assignment failed on {obj}: {e}")

    @staticmethod
    def get_material_color(obj: object) -> Optional[Tuple[float, float, float]]:
        """Gets the color of the object's material."""
        shading_groups = pm.listConnections(obj, type="shadingEngine")
        if not shading_groups:
            return None
        materials = pm.ls(pm.listConnections(shading_groups[0]), materials=True)
        if not materials:
            return None
        return pm.getAttr(f"{materials[0]}.color")[0]

    @staticmethod
    def get_wireframe_color(
        obj: object,
        normalize: bool = False,
    ) -> Optional[Tuple[float, float, float]]:
        """Gets the wireframe color of the given object."""
        if not obj.hasAttr("overrideEnabled") or not obj.attr("overrideEnabled").get():
            return None
        if (
            not obj.hasAttr("overrideRGBColors")
            or not obj.attr("overrideRGBColors").get()
        ):
            return None
        if not obj.hasAttr("overrideColorRGB"):
            return None

        color = obj.attr("overrideColorRGB").get()
        if not normalize:
            color = tuple(int(c * 255) for c in color)
        return color

    @staticmethod
    def get_vertex_color(
        obj: object, vertex_id: int
    ) -> Optional[Tuple[float, float, float]]:
        """Gets the color of a specific vertex on the object."""
        colors = pm.polyColorPerVertex(f"{obj}.vtx[{vertex_id}]", query=True, rgb=True)
        return colors if colors else None

    @staticmethod
    def set_vertex_color(
        objects: List[object], color: Tuple[float, float, float]
    ) -> None:
        """Applies the specified color to the object's vertices."""
        for obj in pm.ls(objects, long=True):
            shapes = pm.listRelatives(obj, shapes=True, type="mesh")
            if shapes:
                try:
                    pm.polyColorPerVertex(obj, rgb=color, colorDisplayOption=True)
                except RuntimeError as e:
                    print(f"Error applying vertex color to {obj}: {e}")

    @staticmethod
    def get_color_difference(
        color1: Tuple[float, float, float], color2: Tuple[float, float, float]
    ) -> float:
        """Calculate the average difference between two RGB colors."""
        return sum(abs(c1 - c2) for c1, c2 in zip(color1, color2)) / 3.0


class ColorManager(ColorUtils):
    @classmethod
    def apply_color(
        cls,
        objects: List[object],
        color: Optional[Tuple[float, float, float]] = None,
        apply_to_material: bool = False,
        apply_to_vertex: bool = False,
        apply_to_wireframe: bool = False,
        apply_to_outliner: bool = False,
    ) -> None:
        """Applies color based on given criteria to objects."""
        for obj in pm.ls(objects, long=True):
            if color is None:
                color = (random.random(), random.random(), random.random())
            if apply_to_vertex:
                cls.set_color_attribute(obj, color, attr_type="vertex", force=True)
            if apply_to_wireframe:
                cls.set_color_attribute(obj, color, attr_type="wireframe", force=True)
            if apply_to_outliner:
                cls.set_color_attribute(obj, color, attr_type="outliner", force=True)
            if apply_to_material:
                cls.set_color_attribute(obj, color, attr_type="material", force=True)

    @classmethod
    def get_objects_by_color(
        cls,
        target_color: Tuple[float, float, float],
        threshold: float = 0.1,
        check_material_color: bool = False,
        check_vertex_color: bool = False,
        check_wireframe_color: bool = False,
        check_outliner_color: bool = False,
    ) -> List[object]:
        """Select objects by color, with optional checks for material, vertex, wireframe, and outliner colors."""
        matching_objects = []

        for obj in pm.ls(geometry=True, type="transform", long=True):
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
                                continue
            if (
                check_wireframe_color
                and obj.overrideEnabled.get()
                and obj.overrideRGBColors.get()
            ):
                wireframe_color = obj.overrideColorRGB.get()
                if cls.get_color_difference(wireframe_color, target_color) <= threshold:
                    matching_objects.append(obj)
                    continue
            if check_outliner_color and obj.useOutlinerColor.get():
                outliner_color = obj.outlinerColor.get()
                if cls.get_color_difference(outliner_color, target_color) <= threshold:
                    matching_objects.append(obj)
                    continue
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
        objects: List[object],
        reset_outliner: bool = True,
        reset_wireframe: bool = True,
        reset_vertex: bool = True,
        reset_material: bool = True,
    ) -> None:
        """Resets colors to default for given objects, with options to specify which color types to reset."""
        for obj in pm.ls(objects, long=True):
            if reset_outliner:
                if obj.hasAttr("useOutlinerColor"):
                    obj.attr("useOutlinerColor").set(0)
                else:
                    pm.warning(f"{obj} has no attribute 'useOutlinerColor'.")

            if reset_wireframe or reset_vertex:
                if obj.hasAttr("overrideEnabled"):
                    obj.attr("overrideEnabled").set(0)
                else:
                    pm.warning(f"{obj} has no attribute 'overrideEnabled'.")

            if reset_material:
                mats = MatUtils.get_mats(obj)
                MatUtils.assign_mat(obj, "lambert1")
                for mat in mats:
                    MatUtils.is_connected(mat, delete=True)

        if reset_vertex:
            cls.reset_vertex_colors(objects)

    @staticmethod
    def reset_vertex_colors(objects: List[object]) -> None:
        """Resets vertex colors for the given object(s), handling potential errors gracefully."""
        transforms = pm.ls(objects, type="transform", long=True)
        shapes = pm.listRelatives(transforms, children=True, shapes=True)

        if shapes:
            for shape in shapes:
                if pm.nodeType(shape) == "mesh":
                    try:
                        colorSets = pm.polyColorSet(
                            shape, query=True, allColorSets=True
                        )
                        if colorSets:
                            for colorSet in colorSets:
                                pm.polyColorSet(shape, delete=True, colorSet=colorSet)
                    except RuntimeError as e:
                        print(f"Error removing vertex colors from {shape}: {e}")


class ColorManagerSlots(ColorManager):
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.color_manager

        self.button_grp = self.sb.create_button_groups(self.ui, "chk000-11")
        for button in self.button_grp.buttons():
            button.settings = self.ui.settings
        self.ui.chk000.setChecked(True)

    @property
    def selected_objects(self) -> List[object]:
        """Return the currently selected objects, or an empty list if no objects are selected."""
        objects = pm.selected()
        if not objects:
            self.sb.message_box("No objects selected.")
        return objects

    @property
    def selected_button(self) -> Optional[object]:
        """Return the currently selected button in the button group."""
        for button in self.button_grp.buttons():
            if button.isChecked():
                return button
        return None

    @property
    def target_color(self) -> Optional[Tuple[float, float, float]]:
        """Return the color of the selected button, or None if no button is selected."""
        selected_btn = self.selected_button
        if selected_btn:
            color = selected_btn.color
            if isinstance(color, self.sb.QtGui.QColor):
                color = (color.redF(), color.greenF(), color.blueF())
            return color
        return None

    def b000(self) -> None:
        """Reset Colors"""
        if self.sb.app.keyboardModifiers() == self.sb.QtCore.Qt.ControlModifier:
            objects = pm.ls(geometry=True, type="transform", long=True)
        else:
            objects = self.selected_objects
        if not objects:
            return
        self.reset_colors(objects)

    def b001(self) -> None:
        """Apply selected color to selected objects."""
        objects = self.selected_objects
        if not objects or not self.target_color:
            return

        kwargs = {
            "apply_to_wireframe": self.ui.chk012.isChecked(),
            "apply_to_vertex": self.ui.chk015.isChecked(),
            "apply_to_outliner": self.ui.chk013.isChecked(),
            "apply_to_material": self.ui.chk014.isChecked(),
        }
        ColorManager.apply_color(objects, color=self.target_color, **kwargs)

    def b002(self) -> None:
        """Select objects by the currently selected color."""
        if not self.target_color:
            print("No color was selected.")
            return

        found_objects = self.get_objects_by_color(
            self.target_color, check_wireframe_color=True, check_outliner_color=True
        )
        pm.select(found_objects)

    def b003(self) -> None:
        if len(self.selected_objects) > 1:
            self.sb.message_box("Please select exactly one object.")
            return

        selected_object = self.selected_objects[0]
        wireframe_color = self.get_wireframe_color(selected_object)
        if wireframe_color is None:
            print("Failed to retrieve wireframe color.")
            return

        self.selected_button.color = wireframe_color


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("color_manager", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
