# !/usr/bin/python
# coding=utf-8
import sys
import os
from typing import Optional, TYPE_CHECKING
from uitk import Switchboard
from uitk.handlers.ui_handler import UiHandler

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
        switchboard: Switchboard,
        log_level: str = "WARNING",
        **kwargs,
    ) -> None:
        """Initialize Maya UI Handler."""
        self.root_dir = os.path.dirname(sys.modules["mayatk"].__file__)

        super().__init__(
            switchboard=switchboard,
            ui_root=self.root_dir,
            slot_root=self.root_dir,
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
            self.logger.debug(f"[{menu_key}] Returning cached Maya UI")
            return self.sb.loaded_ui[menu_key]

        menu_widget = handler.get_menu(menu_key)
        if not menu_widget:
            self.sb.logger.warning(f"Could not retrieve Maya menu for '{menu_key}'")
            return None

        # Retrieve Maya Main Window for correct parenting (ensures Z-order on top)
        try:
            from mayatk.ui_utils._ui_utils import UiUtils

            maya_window = UiUtils.get_main_window()
        except ImportError:
            maya_window = None

        self.logger.debug(f"[{menu_key}] Creating MainWindow wrapper for Maya menu")
        ui = self.sb.add_ui(
            widget=menu_widget,
            name=menu_key,
            tags={"maya", "menu"},
            overwrite=overwrite,
            add_footer=False,
            parent=maya_window,
        )

        # Force floating behavior.
        # When a QMainWindow has a parent, Qt treats it as an embedded child by default.
        # We must set the Window flag to keep it a floating tool window.
        from qtpy import QtCore

        ui.setWindowFlags(QtCore.Qt.Window)

        if header:
            self.logger.debug(
                f"[{menu_key}] Adding header with config_buttons=('menu', 'collapse', 'pin')"
            )
            ui.header = self.sb.registered_widgets.Header(
                config_buttons=("menu", "collapse", "pin"),
            )
            ui.header.setTitle(ui.objectName().upper())
            ui.header.attach_to(ui.centralWidget())
            ui.style.set(ui.header, "dark", "Header")
            self.logger.debug(
                f"[{menu_key}] Header attached: hasattr(ui, 'header')={hasattr(ui, 'header')}, "
                f"header.window() is ui: {ui.header.window() is ui}"
            )

        ui.edit_tags(add="maya_menu")
        self.logger.debug(f"[{menu_key}] Maya UI created with tags={ui.tags}")

        return ui
