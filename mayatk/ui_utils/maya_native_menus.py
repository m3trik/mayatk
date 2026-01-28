# !/usr/bin/python
# coding=utf-8
import time
import traceback
import maya.utils
import functools
from typing import Optional
from qtpy import QtWidgets, QtCore
import pymel.core as pm
import pythontk as ptk
import uitk
from uitk.widgets.mainWindow import MainWindow

# From this package:
from mayatk.ui_utils._ui_utils import UiUtils


class PersistentMenu(QtWidgets.QMenu):
    """A QMenu that ignores attempts to hide it (e.g. from interaction), suitable for embedding."""

    def setVisible(self, visible):
        if not visible:
            return
        super(PersistentMenu, self).setVisible(visible)


class EmbeddedMenuWidget(QtWidgets.QWidget):
    def __init__(self, menu, parent=None):
        super(EmbeddedMenuWidget, self).__init__(parent)
        self.menu = menu
        self.init_ui()

    def init_ui(self):
        # Use a layout to support uitk's automatic header/footer injection
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Prepare the menu for embedding as a regular widget
        self.menu.setParent(self)
        # Using Qt.Widget + FramelessWindowHint helps it behave like a panel
        self.menu.setWindowFlags(QtCore.Qt.Widget | QtCore.Qt.FramelessWindowHint)

        # Apply stylesheet to ensure items look correct and fill space
        # Apply to self (container) to avoid overriding global styles on the menu instance directly
        self.setStyleSheet(
            """
            QMenu::item {
                padding: 1px 20px 1px 20px;
                margin-left: 1px;
                margin-right: 1px;
            }
            """
        )

        # Allow menu to expand
        # Use Ignored for horizontal to force it to fill width regardless of content size
        self.menu.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding
        )

        # Do NOT add menu to layout. We will manage its geometry manually to ensure it fills space.
        # layout.addWidget(self.menu)

        # Add stretch to push footer (injected by MainWindow) to the bottom
        layout.addStretch(1)

        # Set size policies for this container
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

        # Ensure menu is visible (QMenu defaults to hidden)
        self.menu.show()
        # Disable tear-off to prevent redundant header/footer controls
        self.menu.setTearOffEnabled(False)

        # Enable stylesheet background painting
        from qtpy.QtCore import Qt

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.menu.setAttribute(Qt.WA_StyledBackground, True)

    def _get_layout_reserved_space(self):
        """Calculate space reserved by layout items (headers/footers) excluding the menu.

        Returns:
            tuple: (reserved_top, reserved_bottom) heights in pixels
        """
        reserved_top = 0
        reserved_bottom = 0
        layout = self.layout()

        if not layout:
            return reserved_top, reserved_bottom

        half_height = self.height() / 2
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget and widget.isVisible() and widget is not self.menu:
                if item.geometry().y() > half_height:
                    reserved_bottom += item.geometry().height()
                else:
                    reserved_top += item.geometry().height()

        return reserved_top, reserved_bottom

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.menu:
            reserved_top, reserved_bottom = self._get_layout_reserved_space()
            self.menu.setGeometry(
                0,
                reserved_top,
                self.width(),
                self.height() - reserved_bottom - reserved_top,
            )
            self.menu.setMinimumWidth(self.width())

    def minimumSizeHint(self):
        """Allow horizontal contraction while preserving minimum usability."""
        return QtCore.QSize(200, self.sizeHint().height())

    def sizeHint(self):
        """Calculate preferred size based on menu content plus layout overhead."""
        if not self.menu:
            return super().sizeHint()

        self.menu.ensurePolished()
        s_hint = self.menu.sizeHint()

        if s_hint.isValid() and s_hint.height() > 50:
            menu_height = s_hint.height()
            menu_width = max(200, s_hint.width())
        else:
            # Fallback for menus that haven't been populated yet
            action_count = len(self.menu.actions())
            menu_height = max(100, (action_count * 26) + 10)
            menu_width = 200

        # Add overhead from headers/footers
        reserved_top, reserved_bottom = self._get_layout_reserved_space()
        overhead = reserved_top + reserved_bottom

        layout = self.layout()
        if layout:
            margins = layout.contentsMargins()
            overhead += margins.top() + margins.bottom()

        return QtCore.QSize(menu_width, menu_height + overhead)

    def fit_to_window(self):
        """Resize the parent window to fit menu content."""
        self.updateGeometry()
        window = self.window()
        if window:
            if window.layout():
                window.layout().activate()
            # Use sizeHint for preferred size (fits content)
            # minimumSizeHint stays small to allow user resizing
            window.resize(window.sizeHint())


class MayaNativeMenus(ptk.LoggingMixin):
    """Handles Maya's menu retrieval and embedding into UI components."""

    MENU_MAPPING = {
        "arnold": {
            "maya_name": "Arnold",
            "command": 'if (!`pluginInfo -q -l "mtoa"`) loadPlugin "mtoa";',
            "menu_set": "renderingMenuSet",
        },
        "cache": {
            "maya_name": "Cache",
            "command": "NucleusCacheMenu MayaWindow|mainCacheMenu",
            "menu_set": "dynamicsMenuSet",
        },
        "constrain": {
            "maya_name": "Constrain",
            "command": "AniConstraintsMenu MayaWindow|mainRigConstraintsMenu",
            "menu_set": "riggingMenuSet",
        },
        "control": {
            "maya_name": "Control",
            "command": "ChaControlsMenu MayaWindow|mainRigControlMenu",
            "menu_set": "riggingMenuSet",
        },
        "create": {
            "maya_name": "Create",
            "command": "editMenuUpdate MayaWindow|mainCreateMenu",
            "menu_set": "commonMenuSet",
        },
        "curves": {
            "maya_name": "Curves",
            "command": "ModelingCurvesMenu MayaWindow|mainCurvesMenu",
            "menu_set": "modelingMenuSet",
        },
        "deform": {
            "maya_name": "Deform",
            "command": "ChaDeformationsMenu MayaWindow|mainRigDeformationsMenu",
            "menu_set": "riggingMenuSet",
        },
        "display": {
            "maya_name": "Display",
            "command": "buildDisplayMenu MayaWindow|mainDisplayMenu",
            "menu_set": "commonMenuSet",
        },
        "edit": {
            "maya_name": "Edit",
            "command": "buildEditMenu MayaWindow|mainEditMenu",
            "menu_set": "commonMenuSet",
        },
        "edit_mesh": {
            "maya_name": "Edit Mesh",
            "command": "PolygonsBuildMenu MayaWindow|mainEditMeshMenu",
            "menu_set": "modelingMenuSet",
        },
        "effects": {
            "maya_name": "Effects",
            "command": "DynEffectsMenu MayaWindow|mainDynEffectsMenu",
            "menu_set": "dynamicsMenuSet",
        },
        "fields_solvers": {
            "maya_name": "Fields/Solvers",
            "command": "DynFieldsSolverMenu MayaWindow|mainFieldsSolverMenu",
            "menu_set": "dynamicsMenuSet",
        },
        "file": {
            "maya_name": "File",
            "command": "buildFileMenu MayaWindow|mainFileMenu",
            "menu_set": "commonMenuSet",
        },
        "fluids": {
            "maya_name": "Fluids",
            "command": "DynFluidsMenu MayaWindow|mainFluidsMenu",
            "menu_set": "dynamicsMenuSet",
        },
        "generate": {
            "maya_name": "Generate",
            "command": "ModelingGenerateMenu MayaWindow|mainGenerateMenu",
            "menu_set": "modelingMenuSet",
        },
        "help": {
            "maya_name": "Help",
            "command": "buildHelpMenu MayaWindow|mainHelpMenu",
            "menu_set": "commonMenuSet",
        },
        "key": {
            "maya_name": "Key",
            "command": "AniKeyMenu MayaWindow|mainKeysMenu",
            "menu_set": "animationMenuSet",
        },
        "lighting_shading": {
            "maya_name": "Lighting/Shading",
            "command": "RenShadersMenu MayaWindow|mainShadingMenu",
            "menu_set": "renderingMenuSet",
        },
        "mash": {
            "maya_name": "MASH",
            "command": 'if (!`pluginInfo -q -l "MASH"`) loadPlugin "MASH";',
            "menu_set": "animationMenuSet",
        },
        "mesh": {
            "maya_name": "Mesh",
            "command": "PolygonsMeshMenu MayaWindow|mainMeshMenu",
            "menu_set": "modelingMenuSet",
        },
        "mesh_display": {
            "maya_name": "Mesh Display",
            "command": "ModelingMeshDisplayMenu MayaWindow|mainMeshDisplayMenu",
            "menu_set": "modelingMenuSet",
        },
        "mesh_tools": {
            "maya_name": "Mesh Tools",
            "command": "PolygonsBuildToolsMenu MayaWindow|mainMeshToolsMenu",
            "menu_set": "modelingMenuSet",
        },
        "modify": {
            "maya_name": "Modify",
            "command": "ModObjectsMenu MayaWindow|mainModifyMenu",
            "menu_set": "commonMenuSet",
        },
        "ncloth": {
            "maya_name": "nCloth",
            "command": "DynClothMenu MayaWindow|mainNClothMenu",
            "menu_set": "dynamicsMenuSet",
        },
        "nconstraint": {
            "maya_name": "nConstraint",
            "command": "NucleusConstraintMenu MayaWindow|mainNConstraintMenu",
            "menu_set": "dynamicsMenuSet",
        },
        "nhair": {
            "maya_name": "nHair",
            "command": "DynCreateHairMenu MayaWindow|mainHairMenu",
            "menu_set": "dynamicsMenuSet",
        },
        "nparticles": {
            "maya_name": "nParticles",
            "command": "DynParticlesMenu MayaWindow|mainParticlesMenu",
            "menu_set": "dynamicsMenuSet",
        },
        "playback": {
            "maya_name": "Playback",
            "command": "AniPlaybackMenu MayaWindow|mainPlaybackMenu",
            "menu_set": "animationMenuSet",
        },
        "render": {
            "maya_name": "Render",
            "command": "RenRenderMenu MayaWindow|mainRenderMenu",
            "menu_set": "renderingMenuSet",
        },
        "select": {
            "maya_name": "Select",
            "command": "buildSelectMenu MayaWindow|mainSelectMenu",
            "menu_set": "commonMenuSet",
        },
        "skeleton": {
            "maya_name": "Skeleton",
            "command": "ChaSkeletonsMenu MayaWindow|mainRigSkeletonsMenu",
            "menu_set": "riggingMenuSet",
        },
        "skin": {
            "maya_name": "Skin",
            "command": "ChaSkinningMenu MayaWindow|mainRigSkinningMenu",
            "menu_set": "riggingMenuSet",
        },
        "stereo": {
            "maya_name": "Stereo",
            "command": "RenStereoMenu MayaWindow|mainStereoMenu",
            "menu_set": "renderingMenuSet",
        },
        "surfaces": {
            "maya_name": "Surfaces",
            "command": "ModelingSurfacesMenu MayaWindow|mainSurfacesMenu",
            "menu_set": "modelingMenuSet",
        },
        "texturing": {
            "maya_name": "Texturing",
            "command": "RenTexturingMenu MayaWindow|mainRenTexturingMenu",
            "menu_set": "renderingMenuSet",
        },
        "toon": {
            "maya_name": "Toon",
            "command": "buildToonMenu MayaWindow|mainToonMenu",
            "menu_set": "renderingMenuSet",
        },
        "uv": {
            "maya_name": "UV",
            "command": "ModelingUVMenu MayaWindow|mainUVMenu",
            "menu_set": "modelingMenuSet",
        },
        "visualize": {
            "maya_name": "Visualize",
            "command": "AniVisualizeMenu MayaWindow|mainVisualizeMenu",
            "menu_set": "animationMenuSet",
        },
        "windows": {
            "maya_name": "Windows",
            "command": "buildViewMenu MayaWindow|mainWindowMenu",
            "menu_set": "commonMenuSet",
        },
    }

    def __init__(self, log_level: str = "WARNING"):
        super().__init__()
        self.menus = {}
        self.logger.setLevel(log_level)

    def get_menu(self, menu_key: str) -> Optional[QtWidgets.QWidget]:
        """Retrieves a Maya menu and embeds it in a UI component."""
        menu_key = menu_key.lower()
        if menu_key in self.menus:
            return self.menus[menu_key]

        # Retrieve menu details from MENU_MAPPING
        menu_data = self.MENU_MAPPING.get(menu_key)
        if not menu_data:
            self.logger.error(f"No mapping found for menu '{menu_key}'")
            return None

        maya_menu_name = menu_data["maya_name"]
        init_command = menu_data["command"]
        target_menu_set = menu_data["menu_set"]

        if not (maya_menu_name and init_command):
            self.logger.error(f"No initialization command found for menu '{menu_key}'")
            return None

        orig_menu_set = pm.menuSet(q=True, label=True)

        self.logger.debug(
            f"Switching menu mode to '{target_menu_set}' (original: {orig_menu_set})"
        )
        pm.setMenuMode(target_menu_set)
        pm.mel.eval(init_command)
        pm.refresh()

        # Create a placeholder menu UI
        placeholder_menu = PersistentMenu(maya_menu_name, UiUtils.get_main_window())
        placeholder_widget = EmbeddedMenuWidget(placeholder_menu)
        placeholder_widget.setObjectName(menu_key)
        self.menus[menu_key] = placeholder_widget

        # Defer actual menu population
        maya.utils.executeDeferred(
            lambda: self.deferred_duplicate_menu(
                menu_key, maya_menu_name, orig_menu_set, placeholder_widget
            )
        )

        return placeholder_widget

    def display_menu(self, menu_key: str):
        """Displays the specified Maya menu in a standalone window."""
        widget = self.get_menu(menu_key)
        if not widget:
            return

        window = MainWindow(
            name=f"MayaMenu_{menu_key}",
            switchboard_instance=None,
            central_widget=widget,
            add_footer=True,
            restore_window_size=False,
        )
        window.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
        )
        window.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        # Apply standard border styling
        window.setProperty("class", "translucentBgWithBorder")

        window.show(pos="screen", app_exec=True)
        return window

    def deferred_duplicate_menu(
        self,
        menu_key: str,
        maya_menu_name: str,
        orig_menu_set: str,
        placeholder_widget: EmbeddedMenuWidget,
    ):
        """Properly deferred function to duplicate and populate a Maya menu in UI."""

        def _populate_menu():
            main_window = UiUtils.get_main_window()
            menu_bar = main_window.menuBar()

            target_menu = None
            previous_action_count = -1
            stable_iterations = 0
            required_stable_iterations = 2  # Require 2 stable checks
            max_attempts = 15  # Prevent infinite loops
            attempt = 0

            while attempt < max_attempts:
                attempt += 1

                # Locate the target menu
                for action in menu_bar.actions():
                    if action.text() == maya_menu_name:
                        target_menu = action.menu()
                        break

                if target_menu:
                    current_action_count = len(target_menu.actions())

                    if (
                        current_action_count == previous_action_count
                        and current_action_count > 0
                    ):
                        stable_iterations += 1
                    else:
                        stable_iterations = 0  # Reset if count changes

                    previous_action_count = current_action_count

                    if stable_iterations >= required_stable_iterations:
                        self.logger.debug(
                            f"Menu '{maya_menu_name}' stabilized with {current_action_count} actions."
                        )
                        break
                    else:
                        self.logger.debug(
                            f"Waiting for menu '{maya_menu_name}' to stabilize... "
                            f"Currently {current_action_count} actions."
                        )
                else:
                    self.logger.debug(f"Menu '{maya_menu_name}' not found yet...")

                # Process UI events (prevents freezing)
                QtWidgets.QApplication.processEvents()

            # Restore the original menu mode
            pm.setMenuMode(orig_menu_set)
            self.logger.debug(f"Restored original menu mode: {orig_menu_set}")

            if target_menu and previous_action_count > 0:
                placeholder_widget.menu.clear()
                for action in target_menu.actions():
                    placeholder_widget.menu.addAction(action)
                self.logger.debug(
                    f"Populated menu '{menu_key}' with {previous_action_count} actions."
                )

                # Resize the hosting window to accommodate new items
                QtCore.QTimer.singleShot(50, placeholder_widget.fit_to_window)
            else:
                self.logger.warning(
                    f"Failed to fully initialize menu '{menu_key}' after {attempt} attempts."
                )

        # Properly defer execution using Maya's deferred queue
        maya.utils.executeDeferred(_populate_menu)


# --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    handler = MayaMenuHandler()
    handler.display_menu("skin")
