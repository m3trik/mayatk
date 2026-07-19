from __future__ import annotations

from typing import List, Tuple, Optional
import random

try:
    import maya.cmds as cmds
except ModuleNotFoundError as error:
    print(__file__, error)

from uitk.widgets.mixins.tooltip_mixin import fmt, kbd
# from this package:
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils.attributes._attributes import Attributes


class ColorUtils:
    @staticmethod
    def assign_material(obj: str, color: Tuple[float, float, float]) -> str:
        """Assigns a material to an object based on the RGB value. Creates the material if it does not exist."""
        # Convert the RGB tuple to a hex string for the material name
        color_name = "_".join(f"{int(c * 255):02X}" for c in color)
        material_name = f"ID_{color_name}"

        # Check if the material already exists
        if not cmds.objExists(material_name):
            # Create a new material and shading group
            material = MatUtils.create_mat("lambert", prefix="ID_", name=color_name)
            cmds.setAttr(
                f"{material}.color", color[0], color[1], color[2], type="double3"
            )
        else:
            material = material_name

        # Assign the material to the object
        MatUtils.assign_mat(obj, material_name)
        return material

    @classmethod
    def set_color_attribute(
        cls,
        obj: str,
        color: Tuple[float, float, float],
        attr_type: str,
        force: bool = False,
    ) -> None:
        """Applies color based on the attribute type specified, optionally overriding attribute locks."""
        try:
            if attr_type == "outliner":
                if Attributes.has_attr(obj, "useOutlinerColor"):
                    Attributes.set_plug(f"{obj}.useOutlinerColor", 1, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'useOutlinerColor'.")

                if Attributes.has_attr(obj, "outlinerColor"):
                    Attributes.set_plug(f"{obj}.outlinerColor", color, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'outlinerColor'.")

            elif attr_type == "vertex":
                shapes = cmds.listRelatives(obj, shapes=True, type="mesh")
                if not shapes:
                    cmds.warning(
                        f"{obj} does not have a mesh shape for vertex color assignment."
                    )
                    return
                try:
                    cmds.polyColorPerVertex(obj, rgb=color, colorDisplayOption=True)
                except RuntimeError as e:
                    cmds.warning(f"Error applying vertex color to {obj}: {e}")

            elif attr_type == "material":
                cls.assign_material(obj, color)

            elif attr_type == "wireframe":
                if cmds.nodeType(obj) == "lambert":
                    cmds.warning(f"{obj} is a material, not a mesh.")
                    return

                if Attributes.has_attr(obj, "overrideEnabled"):
                    Attributes.set_plug(f"{obj}.overrideEnabled", 1, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'overrideEnabled'.")

                if Attributes.has_attr(obj, "overrideRGBColors"):
                    Attributes.set_plug(f"{obj}.overrideRGBColors", 1, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'overrideRGBColors'.")

                if Attributes.has_attr(obj, "overrideColorRGB"):
                    Attributes.set_plug(f"{obj}.overrideColorRGB", color, force=force)
                else:
                    cmds.warning(f"{obj} has no attribute 'overrideColorRGB'.")

        except Exception as e:
            cmds.warning(f"Color assignment failed on {obj}: {e}")

    @staticmethod
    def get_material_color(obj: str) -> Optional[Tuple[float, float, float]]:
        """Gets the color of the object's material."""
        shading_groups = cmds.listConnections(obj, type="shadingEngine") or []
        if not shading_groups:
            return None
        connected = cmds.listConnections(shading_groups[0]) or []
        materials = cmds.ls(connected, materials=True) or []
        if not materials:
            return None
        return cmds.getAttr(f"{materials[0]}.color")[0]

    @staticmethod
    def get_wireframe_color(
        obj: str,
        normalize: bool = False,
    ) -> Optional[Tuple[float, float, float]]:
        """Gets the wireframe color of the given object."""
        if not Attributes.has_attr(obj, "overrideEnabled") or not cmds.getAttr(
            f"{obj}.overrideEnabled"
        ):
            return None
        if not Attributes.has_attr(obj, "overrideRGBColors") or not cmds.getAttr(
            f"{obj}.overrideRGBColors"
        ):
            return None
        if not Attributes.has_attr(obj, "overrideColorRGB"):
            return None

        color = cmds.getAttr(f"{obj}.overrideColorRGB")[0]
        if not normalize:
            color = tuple(int(c * 255) for c in color)
        return color

    @staticmethod
    def get_vertex_color(
        obj: str, vertex_id: int
    ) -> Optional[Tuple[float, float, float]]:
        """Gets the color of a specific vertex on the object."""
        colors = cmds.polyColorPerVertex(
            f"{obj}.vtx[{vertex_id}]", query=True, rgb=True
        )
        return colors if colors else None

    @staticmethod
    def set_vertex_color(
        objects: List[str], color: Tuple[float, float, float]
    ) -> None:
        """Applies the specified color to the object's vertices."""
        for obj in cmds.ls(objects, long=True) or []:
            shapes = cmds.listRelatives(obj, shapes=True, type="mesh")
            if shapes:
                try:
                    cmds.polyColorPerVertex(obj, rgb=color, colorDisplayOption=True)
                except RuntimeError as e:
                    print(f"Error applying vertex color to {obj}: {e}")

    @staticmethod
    def get_color_difference(
        color1: Tuple[float, float, float], color2: Tuple[float, float, float]
    ) -> float:
        """Calculate the average difference between two RGB colors."""
        return sum(abs(c1 - c2) for c1, c2 in zip(color1, color2)) / 3.0


class ColorId(ColorUtils):
    @classmethod
    def apply_color(
        cls,
        objects: List[str],
        color: Optional[Tuple[float, float, float]] = None,
        apply_to_material: bool = False,
        apply_to_vertex: bool = False,
        apply_to_wireframe: bool = False,
        apply_to_outliner: bool = False,
    ) -> None:
        """Applies color based on given criteria to objects."""
        if color is None:
            color = (random.random(), random.random(), random.random())
        for obj in cmds.ls(objects, long=True) or []:
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
    ) -> List[str]:
        """Select objects by color, with optional checks for material, vertex, wireframe, and outliner colors."""
        matching_objects = []

        candidates = cmds.ls(geometry=True, long=True) or []
        # Walk to transforms
        transforms = []
        seen = set()
        for shape in candidates:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            for p in parents:
                if p not in seen:
                    seen.add(p)
                    transforms.append(p)

        for obj in transforms:
            matched = False
            if check_material_color and not matched:
                shading_engines = cmds.listConnections(obj, type="shadingEngine") or []
                for shading_engine in shading_engines:
                    if matched:
                        break
                    materials = cmds.listConnections(shading_engine) or []
                    for material in materials:
                        if Attributes.has_attr(material, "color"):
                            mat_color = cmds.getAttr(f"{material}.color")[0]
                            if (
                                cls.get_color_difference(mat_color, target_color)
                                <= threshold
                            ):
                                matched = True
                                break
            if (
                check_wireframe_color
                and not matched
                and Attributes.has_attr(obj, "overrideEnabled")
                and cmds.getAttr(f"{obj}.overrideEnabled")
                and Attributes.has_attr(obj, "overrideRGBColors")
                and cmds.getAttr(f"{obj}.overrideRGBColors")
                and Attributes.has_attr(obj, "overrideColorRGB")
            ):
                wireframe_color = cmds.getAttr(f"{obj}.overrideColorRGB")[0]
                if cls.get_color_difference(wireframe_color, target_color) <= threshold:
                    matched = True
            if (
                check_outliner_color
                and not matched
                and Attributes.has_attr(obj, "useOutlinerColor")
                and cmds.getAttr(f"{obj}.useOutlinerColor")
                and Attributes.has_attr(obj, "outlinerColor")
            ):
                outliner_color = cmds.getAttr(f"{obj}.outlinerColor")[0]
                if cls.get_color_difference(outliner_color, target_color) <= threshold:
                    matched = True
            if check_vertex_color and not matched:
                if cmds.listRelatives(obj, shapes=True, type="mesh"):
                    try:
                        vtx_colors = cmds.polyColorPerVertex(
                            f"{obj}.vtx[*]", query=True, rgb=True
                        )
                    except RuntimeError:
                        vtx_colors = None
                    if vtx_colors:
                        # vtx_colors is flat [r,g,b,r,g,b,...]; group into triples
                        triples = list(zip(vtx_colors[0::3], vtx_colors[1::3], vtx_colors[2::3]))
                        if triples:
                            average_vtx_color = [sum(c) / len(triples) for c in zip(*triples)]
                            if (
                                cls.get_color_difference(average_vtx_color, target_color)
                                <= threshold
                            ):
                                matched = True
            if matched:
                matching_objects.append(obj)

        return matching_objects

    @classmethod
    def reset_colors(
        cls,
        objects: List[str],
        reset_outliner: bool = True,
        reset_wireframe: bool = True,
        reset_vertex: bool = True,
        reset_material: bool = True,
    ) -> None:
        """Resets colors to default for given objects, with options to specify which color types to reset."""
        for obj in cmds.ls(objects, long=True) or []:
            if reset_outliner:
                if Attributes.has_attr(obj, "useOutlinerColor"):
                    cmds.setAttr(f"{obj}.useOutlinerColor", 0)
                else:
                    cmds.warning(f"{obj} has no attribute 'useOutlinerColor'.")

            if reset_wireframe:
                if Attributes.has_attr(obj, "overrideEnabled"):
                    cmds.setAttr(f"{obj}.overrideEnabled", 0)
                else:
                    cmds.warning(f"{obj} has no attribute 'overrideEnabled'.")

            if reset_material:
                mats = MatUtils.get_mats(obj)
                MatUtils.assign_mat(obj, "lambert1")
                for mat in mats:
                    MatUtils.is_connected(mat, delete=True)

        if reset_vertex:
            cls.reset_vertex_colors(objects)

    @staticmethod
    def reset_vertex_colors(objects: List[str]) -> None:
        """Resets vertex colors for the given object(s), handling potential errors gracefully."""
        transforms = cmds.ls(objects, type="transform", long=True) or []
        shapes = cmds.listRelatives(transforms, children=True, shapes=True) or []

        for shape in shapes:
            if cmds.nodeType(shape) == "mesh":
                try:
                    color_sets = cmds.polyColorSet(
                        shape, query=True, allColorSets=True
                    )
                    if color_sets:
                        for color_set in color_sets:
                            cmds.polyColorSet(shape, delete=True, colorSet=color_set)
                except RuntimeError as e:
                    print(f"Error removing vertex colors from {shape}: {e}")

    # Desaturated defaults so swatches aren't all white on first launch.
    DEFAULT_SWATCH_COLORS = [
        (180, 120, 120),  # muted red
        (180, 150, 120),  # muted orange
        (180, 180, 120),  # muted yellow
        (120, 180, 120),  # muted green
        (120, 180, 160),  # muted teal
        (120, 180, 180),  # muted cyan
        (120, 150, 180),  # muted blue
        (120, 120, 180),  # muted indigo
        (150, 120, 180),  # muted purple
        (180, 120, 180),  # muted magenta
        (180, 120, 150),  # muted pink
        (160, 160, 160),  # muted gray
    ]


class ColorIdSlots(ColorId):
    # Storage key intentionally kept as the legacy "color_manager" (the tool
    # was renamed Color Manager -> Color ID): it keys existing saved palettes
    # and the PresetManager legacy-migration table, and is never user-visible.
    _PRESET_DIR = "mayatk/color_manager"
    _DEFAULT_PRESET = "default"

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.color_id

        self.button_grp = self.sb.create_button_groups(self.ui, "chk000-11")
        buttons = self.button_grp.buttons()

        # Migrate away from a prior bug that wrote "#ffffff" over every
        # swatch's saved color (uitk colorSwatch.loadColor used to fall
        # back to white when nothing was stored, then auto-saved it).
        # Clear those legacy values so _initialColor's pastel takes hold.
        for button in buttons:
            key = f"colorSwatch/{button.objectName()}/color"
            if str(self.ui.settings.value(key, "")).lower() == "#ffffff":
                self.ui.settings.clear(key)

        for i, button in enumerate(buttons):
            button._initialColor = self.sb.QtGui.QColor(
                *ColorId.DEFAULT_SWATCH_COLORS[
                    i % len(ColorId.DEFAULT_SWATCH_COLORS)
                ]
            )
            button.keep_square = True  # square swatches that track column width
            button.settings = self.ui.settings
        self.ui.chk000.setChecked(True)

    # ── Preset I/O ─────────────────────────────────────────────────────────

    def _export_swatch_colors(self) -> dict:
        """``PresetManager.metadata_provider`` — capture current swatch colors."""
        return {
            "swatches": [
                btn.color.name() for btn in self.button_grp.buttons()
            ]
        }

    def _import_swatch_colors(self, meta: dict) -> None:
        """``PresetManager.on_metadata_loaded`` — apply colors from a preset."""
        colors = (meta or {}).get("swatches") or []
        for btn, hex_color in zip(self.button_grp.buttons(), colors):
            btn.color = self.sb.QtGui.QColor(hex_color)

    @staticmethod
    def _hex_from_rgb(rgb) -> str:
        r, g, b = rgb
        return f"#{int(r):02X}{int(g):02X}{int(b):02X}"

    def _ensure_default_preset(self, presets) -> None:
        """Write the factory-default preset on first use if it's missing."""
        if presets.exists(self._DEFAULT_PRESET):
            return
        original = presets.metadata_provider
        presets.metadata_provider = lambda: {
            "swatches": [
                self._hex_from_rgb(rgb)
                for rgb in ColorId.DEFAULT_SWATCH_COLORS
            ]
        }
        try:
            presets.save(self._DEFAULT_PRESET)
        finally:
            presets.metadata_provider = original

    def header_init(self, widget):
        """Configure header help text and preset combobox."""
        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.set_help_text(
            fmt(
                title="Color ID",
                body="Assign colors to scene objects through any combination "
                "of four channels: material, outliner tint, wireframe "
                "override, and vertex colors.",
                steps=[
                    "Click a palette swatch to pick the active color (right-"
                    "click a swatch to change its color).",
                    "Enable the channels to apply via <b>Material</b>, "
                    "<b>Outliner</b>, <b>Wireframe</b>, <b>Vertex</b> "
                    "checkboxes.",
                    "Select objects and press <b>Apply</b>.",
                    "Use <b>Select By Color</b> to find scene objects "
                    "matching the active color across the enabled channels.",
                ],
                sections=[
                    ("Other actions", [
                        "<b>Reset Colors</b> — clear assignments on the "
                        f"current selection (or every geometry node with "
                        f"{kbd('Ctrl')}-click).",
                        "<b>Remove Vertex Colors</b> — clear vertex-color "
                        "data without touching other channels.",
                    ]),
                    ("Presets", [
                        "The header menu's preset combo saves / restores "
                        "swatch palettes. Use <b>Save</b> to capture the "
                        "current colors; pick a preset to restore them.",
                    ]),
                ],
            )
        )
        # Preset combobox — swatches aren't standard widgets, so colors
        # are carried in metadata rather than per-widget value reads.
        widget.menu.add_presets = True
        widget.menu.presets.preset_dir = self._PRESET_DIR
        widget.menu.presets.metadata_provider = self._export_swatch_colors
        widget.menu.presets.on_metadata_loaded = self._import_swatch_colors
        self._ensure_default_preset(widget.menu.presets)

    @property
    def selected_objects(self) -> List[str]:
        """Return the currently selected objects, or an empty list if no objects are selected."""
        objects = cmds.ls(selection=True) or []
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
            objects = cmds.ls(geometry=True, long=True) or []
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
        ColorId.apply_color(objects, color=self.target_color, **kwargs)

    def b002(self) -> None:
        """Select objects by the currently selected color."""
        if not self.target_color:
            print("No color was selected.")
            return

        found_objects = self.get_objects_by_color(
            self.target_color,
            check_wireframe_color=self.ui.chk012.isChecked(),
            check_vertex_color=self.ui.chk015.isChecked(),
            check_outliner_color=self.ui.chk013.isChecked(),
            check_material_color=self.ui.chk014.isChecked(),
        )
        if found_objects:
            cmds.select(found_objects)
        else:
            cmds.select(clear=True)

    def b003(self) -> None:
        """Pick up the selected object's wireframe color into the active color button (eyedropper)."""
        objects = self.selected_objects
        if not objects:
            return
        if len(objects) > 1:
            self.sb.message_box("Please select exactly one object.")
            return

        selected_object = objects[0]
        wireframe_color = self.get_wireframe_color(selected_object)
        if wireframe_color is None:
            print("Failed to retrieve wireframe color.")
            return

        self.selected_button.color = wireframe_color


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("color_id", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
