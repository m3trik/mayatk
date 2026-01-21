# !/usr/bin/python
# coding=utf-8
import sys, os
from typing import Optional, TYPE_CHECKING
import pythontk as ptk
from uitk import Switchboard
from uitk.controllers.ui_manager import UiManager as BaseUiManager
import mayatk

try:
    from mayatk.ui_utils import maya_menu_handler
except ImportError:
    # Handle case where mayatk is not fully setup or running outside maya context
    maya_menu_handler = None

if TYPE_CHECKING:
    from qtpy import QtWidgets


class UiManager(BaseUiManager):
    """Manages and tracks Switchboard UI instances for Maya.

    This class is a thin layer over the generic uitk UiManager,
    adding Maya-specific menu handling and styling.
    """

    def __init__(
        self,
        switchboard: Optional[Switchboard] = None,
        log_level: str = "WARNING",
        **kwargs,
    ) -> None:
        """Initialize Maya UiManager."""
        # Calculate root dynamically
        self.root_dir = os.path.dirname(sys.modules["mayatk"].__file__)

        # Initialize base with recursive discovery and convention-based roots
        super().__init__(
            ui_root=self.root_dir,
            slot_root=self.root_dir,
            switchboard=switchboard,
            discover_slots=True,  # Enable slot discovery for dynamic resolution
            recursive=True,
            log_level=log_level,
            **kwargs,
        )

    def get(self, name: str, reload: bool = False, **kwargs) -> "QtWidgets.QMainWindow":
        """Retrieve a UI, inspecting Maya menus first."""

        # 1. Check Maya Menus
        # If the name corresponds to a known Maya menu, load/wrap that instead of a .ui file
        if maya_menu_handler and name in maya_menu_handler.MayaMenuHandler.MENU_MAPPING:
            return self._load_maya_ui(menu_key=name, **kwargs)

        # 2. Get from Switchboard (Base implementation)
        # This handles cached UIs and loading new ones from discovered .ui files
        return super().get(name, reload=reload, **kwargs)

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
        if not maya_menu_handler:
            return None

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

        # Apply standard styling with menu-specific header buttons
        menu_style = {
            **self.DEFAULT_STYLE,
            "header_buttons": ("menu", "collapse", "pin"),
        }
        ui.edit_tags(add="maya_menu")

        return ui


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils._ui_utils import UiUtils

    UiUtils.clear_scrollfield_reporters()

    ui = UiManager.instance().get("scene_exporter", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)
