# !/usr/bin/python
# coding=utf-8
import sys, os
import importlib
from typing import Optional, Callable, Any, TYPE_CHECKING
import pythontk as ptk
from tentacle import ui
from uitk import Switchboard

if TYPE_CHECKING:
    # Only for type checking; avoids runtime Qt import outside host apps
    from qtpy import QtWidgets

# From this package:
from mayatk.ui_utils import maya_menu_handler


class UiManager(ptk.SingletonMixin, ptk.LoggingMixin):
    """Manages and tracks Switchboard UI instances."""

    UI_REGISTRY: dict[str, dict[str, str]] = {
        "scene_exporter": {
            "ui": "env_utils/scene_exporter/scene_exporter.ui",
            "slot": "env_utils.scene_exporter._scene_exporter.SceneExporterSlots",
        },
        "_shader_templates": {
            "ui": "mat_utils/shader_templates/shader_templates.ui",
            "slot": "mat_utils.shader_templates._shader_templates.ShaderTemplatesSlots",
        },
        "bevel": {
            "ui": "edit_utils/bevel.ui",
            "slot": "edit_utils.bevel.BevelSlots",
        },
        "bridge": {
            "ui": "edit_utils/bridge.ui",
            "slot": "edit_utils.bridge.BridgeSlots",
        },
        "color_manager": {
            "ui": "display_utils/color_manager.ui",
            "slot": "display_utils.color_manager.ColorManagerSlots",
        },
        "cut_on_axis": {
            "ui": "edit_utils/cut_on_axis.ui",
            "slot": "edit_utils.cut_on_axis.CutOnAxisSlots",
        },
        "duplicate_grid": {
            "ui": "edit_utils/duplicate_grid.ui",
            "slot": "edit_utils.duplicate_grid.DuplicateGridSlots",
        },
        "duplicate_linear": {
            "ui": "edit_utils/duplicate_linear.ui",
            "slot": "edit_utils.duplicate_linear.DuplicateLinearSlots",
        },
        "duplicate_radial": {
            "ui": "edit_utils/duplicate_radial.ui",
            "slot": "edit_utils.duplicate_radial.DuplicateRadialSlots",
        },
        "dynamic_pipe": {
            "ui": "edit_utils/dynamic_pipe.ui",
            "slot": "edit_utils.dynamic_pipe.DynamicPipeSlots",
        },
        "hierarchy_manager": {
            "ui": "env_utils/hierarchy_manager/hierarchy_manager.ui",
            "slot": "env_utils.hierarchy_manager.HierarchyManagerSlots",
        },
        "exploded_view": {
            "ui": "display_utils/exploded_view.ui",
            "slot": "display_utils.exploded_view.ExplodedViewSlots",
        },
        "hdr_manager": {
            "ui": "light_utils/hdr_manager.ui",
            "slot": "light_utils.hdr_manager.HdrManagerSlots",
        },
        "image_tracer": {
            "ui": "nurbs_utils/image_tracer.ui",
            "slot": "nurbs_utils.image_tracer.ImageTracerSlots",
        },
        "mirror": {
            "ui": "edit_utils/mirror.ui",
            "slot": "edit_utils.mirror.MirrorSlots",
        },
        "naming": {
            "ui": "edit_utils/naming/naming.ui",
            "slot": "edit_utils.naming.NamingSlots",
        },
        "reference_manager": {
            "ui": "env_utils/reference_manager.ui",
            "slot": "env_utils.reference_manager.ReferenceManagerSlots",
        },
        "shader_templates": {
            "ui": "mat_utils/shader_templates/shader_templates.ui",
            "slot": "mat_utils.shader_templates._shader_templates.ShaderTemplatesSlots",
        },
        "snap": {
            "ui": "edit_utils/snap.ui",
            "slot": "edit_utils.snap.SnapSlots",
        },
        "stingray_arnold_shader": {
            "ui": "mat_utils/stingray_arnold_shader.ui",
            "slot": "mat_utils.stingray_arnold_shader.StingrayArnoldShaderSlots",
        },
        "texture_path_editor": {
            "ui": "mat_utils/texture_path_editor.ui",
            "slot": "mat_utils.texture_path_editor.TexturePathEditorSlots",
        },
        "tube_rig": {
            "ui": "rig_utils/tube_rig.ui",
            "slot": "rig_utils.tube_rig.TubeRigSlots",
        },
        "wheel_rig": {
            "ui": "rig_utils/wheel_rig.ui",
            "slot": "rig_utils.wheel_rig.WheelRigSlots",
        },
    }

    def __init__(
        self, switchboard: Switchboard = None, log_level: str = "WARNING", **kwargs
    ) -> None:
        """Initialize a UiManager with a specific Switchboard instance.

        Parameters:
            switchboard (Switchboard): The Switchboard instance to use.
        """
        self.logger.setLevel(log_level)

        # Filter out singleton_key from kwargs before creating Switchboard
        sb_kwargs = {k: v for k, v in kwargs.items() if k != "singleton_key"}
        self.sb = switchboard or Switchboard(**sb_kwargs)
        # Register the mayatk root directory once
        self.sb.register(ui_location=self.root_dir, slot_location=self.root_dir)

    @classmethod
    def instance(cls, switchboard: Switchboard = None, **kwargs) -> "UiManager":
        kwargs.setdefault("switchboard", switchboard)
        kwargs["singleton_key"] = id(switchboard)
        return super().instance(**kwargs)

    @property
    def root_dir(self) -> str:
        """Return the root directory of the mayatk package."""
        return os.path.dirname(sys.modules["mayatk"].__file__)

    def get(self, name: str, reload: bool = False, **kwargs) -> "QtWidgets.QMainWindow":
        """Retrieve or load a UI or Maya menu by name using the internal registry."""
        # print(f"[UiManager.get] Loading UI: {name}")

        # If reload is requested, skip the cache and force reload
        if not reload and name in self.sb.loaded_ui:
            # print(f"[UiManager.get] Returning cached UI: {name}")
            return self.sb.loaded_ui[name]

        # print(f"[UiManager.get] Loading UI: {name}")
        if name in maya_menu_handler.MayaMenuHandler.MENU_MAPPING:
            return self._load_maya_ui(menu_key=name, **kwargs)

        return self._load_ui(name, reload=reload, **kwargs)

    def _load_ui(
        self, name: str, reload: bool = False, **kwargs
    ) -> "QtWidgets.QMainWindow":
        """Internal method to resolve, register, and load a UI with its slots."""
        # print(f"[UiManager._load_ui] Loading: {name}")
        if name not in self.UI_REGISTRY:
            raise KeyError(f"UI '{name}' not found in internal registry.")

        slot_path = self.UI_REGISTRY[name]["slot"]
        mod_path, class_name = slot_path.rsplit(".", 1)
        full_mod_path = f"mayatk.{mod_path}"
        mod = importlib.import_module(full_mod_path)
        if reload:
            # print(f"[UiManager._load_ui] Reloading module: {full_mod_path}")
            importlib.reload(mod)
        slot_class = getattr(mod, class_name)

        ui_rel_path = self.UI_REGISTRY[name]["ui"]
        ui_path = os.path.join(self.root_dir, ui_rel_path)
        ui_name = ptk.format_path(ui_path, "name")

        # print(f"[UiManager._load_ui] Resolved ui_name: {ui_name}")

        try:
            # print(
            #     f"[UiManager._load_ui] Attempting to get UI via Switchboard: {ui_name}"
            # )
            # If reloading, remove from cache first to force recreation
            if reload and ui_name in self.sb.loaded_ui:
                # print(f"[UiManager._load_ui] Removing cached UI for reload: {ui_name}")
                del self.sb.loaded_ui[ui_name]

            return self.sb.get_ui(ui_name)
        except AttributeError:
            # print(f"[UiManager._load_ui] UI not found in loaded_ui, registering...")
            # Force re-registration if reloading
            if reload:
                # Clear any existing registration to ensure fresh reload
                # print(f"[UiManager._load_ui] Force re-registering for reload: {ui_name}")
                pass  # Switchboard handles re-registration automatically

            self.sb.register(ui_path, slot_class, base_dir=slot_class, validate=2)
            ui = self.sb.get_ui(ui_name)
            # print(f"[UiManager._load_ui] UI created and registered: {ui_name}")
            ui.set_attributes(WA_TranslucentBackground=True)
            ui.set_flags(FramelessWindowHint=True)
            ui.style.set(theme="dark", style_class="translucentBgWithBorder")
            ui.header.config_buttons("menu", "collapse", "hide")
            ui.edit_tags(add="mayatk")
            return ui

    def _load_maya_ui(
        self,
        menu_key: str,
        header: bool = True,
        overwrite: bool = False,
    ) -> Optional["QtWidgets.QMainWindow"]:
        """Internal method to load and wrap a Maya menu by key.

        Parameters:
            menu_key (str): The key matching MayaMenuHandler.MENU_MAPPING.
            header (bool): Whether to add a header bar.
            overwrite (bool): Force overwrite if UI is already registered.

        Returns:
            QtWidgets.QMainWindow or None: The wrapped menu UI, or None if not found.
        """
        # Lazy init handler
        if not hasattr(self, "_maya_menu_handler"):
            self._maya_menu_handler = maya_menu_handler.MayaMenuHandler()
        handler = self._maya_menu_handler

        if not overwrite and menu_key in self.sb.loaded_ui:
            return self.sb.loaded_ui[menu_key]

        menu_widget = handler.get_menu(menu_key)
        if not menu_widget:
            self.sb.logger.warning(f"Could not retrieve Maya menu for '{menu_key}'")
            return None

        ui = self.sb.add_ui(
            widget=menu_widget,
            name=menu_key,
            tags={"maya", "menu"},
            overwrite=overwrite,
            add_footer=False,  # Disable size grip for Maya menus
        )

        if header:
            ui.header = self.sb.registered_widgets.Header()
            ui.header.setTitle(ui.objectName().upper())
            ui.header.attach_to(ui.centralWidget())
            ui.style.set(ui.header, "dark", "Header")
            ui.header.config_buttons("menu", "collapse", "pin")

        ui.set_attributes(WA_TranslucentBackground=True)
        ui.set_flags(FramelessWindowHint=True)
        ui.style.set(theme="dark", style_class="translucentBgWithBorder")
        ui.lock_style = True  # Prevent style changes

        return ui


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.core_utils._core_utils import CoreUtils

    CoreUtils.clear_scrollfield_reporters()

    ui = UiManager.instance().get("scene_exporter", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
