# !/usr/bin/python
# coding=utf-8
import sys
import os
from typing import Optional, TYPE_CHECKING
from uitk import Switchboard
from uitk.handlers.ui_handler import UiHandler
import mayatk

try:
    from mayatk.ui_utils import maya_native_menus
except ImportError:
    maya_native_menus = None

if TYPE_CHECKING:
    from qtpy import QtWidgets


class MayaUiHandler(UiHandler):
    """UI Handler for Maya applications.

    Extends the generic UiHandler with Maya-specific menu wrapping
    and discovery of mayatk UI files.
    """

    def __init__(
        self,
        switchboard: Optional[Switchboard] = None,
        log_level: str = "WARNING",
        **kwargs,
    ) -> None:
        """Initialize Maya UI Handler."""
        self.root_dir = os.path.dirname(sys.modules["mayatk"].__file__)

        super().__init__(
            ui_root=self.root_dir,
            slot_root=self.root_dir,
            switchboard=switchboard,
            discover_slots=True,
            recursive=True,
            log_level=log_level,
            **kwargs,
        )

    def get(self, name: str, reload: bool = False, **kwargs) -> "QtWidgets.QMainWindow":
        """Retrieve a UI, checking Maya menus first."""
        # Check if name corresponds to a Maya menu
        if maya_native_menus and name in maya_native_menus.MayaNativeMenus.MENU_MAPPING:
            return self._load_maya_ui(menu_key=name, **kwargs)

        return super().get(name, reload=reload, **kwargs)

    def _load_maya_ui(
        self,
        menu_key: str,
        header: bool = True,
        overwrite: bool = False,
    ) -> Optional["QtWidgets.QMainWindow"]:
        """Load and wrap a Maya menu by key."""
        if not maya_native_menus:
            return None

        if not hasattr(self, "_maya_native_menus"):
            self._maya_native_menus = maya_native_menus.MayaNativeMenus()
        handler = self._maya_native_menus

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
            add_footer=False,
        )

        if header:
            ui.header = self.sb.registered_widgets.Header()
            ui.header.setTitle(ui.objectName().upper())
            ui.header.attach_to(ui.centralWidget())
            ui.style.set(ui.header, "dark", "Header")

        menu_style = {
            **self.DEFAULT_STYLE,
            "header_buttons": ("menu", "collapse", "pin"),
        }
        ui.edit_tags(add="maya_menu")

        return ui
