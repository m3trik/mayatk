# !/usr/bin/python
# coding=utf-8
import maya.cmds as cmds
import maya.mel as mel
from typing import Optional
from qtpy import QtWidgets, QtCore
import pythontk as ptk
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
    """Embeds a Maya QMenu into a sizeable widget that fits content exactly.

    Native Maya menus have fixed-height action rows, so the wrapper is
    rigid-fit to content (no resize handle, no dead space).
    """

    # Per-row pixel estimate for action / separator rows when QMenu's own
    # geometry is unavailable (e.g. before first show, in offscreen tests).
    _ACTION_ROW_PX = 26
    _SEPARATOR_PX = 8
    _MIN_WIDTH = 200
    _EMPTY_HEIGHT_FLOOR = 100

    def __init__(self, menu, parent=None):
        super(EmbeddedMenuWidget, self).__init__(parent)
        self.menu = menu
        self.init_ui()

    def init_ui(self):
        # Layout exists so uitk's header attach_to() can insert at index 0.
        # The QMenu is positioned manually (not added to layout) because
        # QMenu-in-layout misbehaves with item painting and popup logic.
        # 2 px contents margin so the parent QSS border (translucentBgWithBorder)
        # is visible around layout-managed children — without it the header
        # sits flush against the painted border.
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.menu.setParent(self)
        self.menu.setWindowFlags(QtCore.Qt.Widget | QtCore.Qt.FramelessWindowHint)
        self.menu.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding
        )

        # Stretch keeps any later header pinned to the top while the manually
        # positioned QMenu fills the remaining height.
        layout.addStretch(1)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred
        )

        self.menu.show()
        self.menu.setTearOffEnabled(False)

        # Required for the parent QSS class (translucentBgWithBorder) to
        # actually paint background/border on this plain QWidget — without
        # this the rule is matched but no painting happens.
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.menu.setAttribute(QtCore.Qt.WA_StyledBackground, True)

    def _reserved_top(self):
        """Height of layout widgets above the menu (e.g. attached header)."""
        layout = self.layout()
        if not layout:
            return 0
        total = 0
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w and w is not self.menu:
                hint = w.sizeHint()
                if hint.isValid() and hint.height() > 0:
                    total += hint.height()
                elif w.height() > 0:
                    total += w.height()
        return total

    def _menu_content_height(self):
        """Sum of action geometries; falls back to per-row estimate."""
        actions = self.menu.actions()
        if not actions:
            return 0

        self.menu.ensurePolished()
        total = 0
        for action in actions:
            if not action.isVisible():
                continue
            rect = self.menu.actionGeometry(action)
            if rect.isValid() and rect.height() > 0:
                total += rect.height()
            else:
                total += (
                    self._SEPARATOR_PX
                    if action.isSeparator()
                    else self._ACTION_ROW_PX
                )
        margins = self.menu.contentsMargins()
        total += margins.top() + margins.bottom()
        return total

    def content_size(self):
        """Exact size needed for header + populated menu, no dead space."""
        self.menu.ensurePolished()

        menu_hint = self.menu.sizeHint()
        width = max(self._MIN_WIDTH, menu_hint.width() if menu_hint.isValid() else 0)

        height = self._menu_content_height()
        height += self._reserved_top()

        layout = self.layout()
        if layout:
            lm = layout.contentsMargins()
            width += lm.left() + lm.right()
            height += lm.top() + lm.bottom()

        # Floor when menu is empty so the wrapper is still visible during
        # the (now rare) window-shown-before-populate race.
        return QtCore.QSize(width, max(height, self._EMPTY_HEIGHT_FLOOR))

    def sizeHint(self):
        return self.content_size()

    def minimumSizeHint(self):
        return self.content_size()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.menu:
            return
        layout = self.layout()
        if layout:
            lm = layout.contentsMargins()
            left, top, right, bottom = lm.left(), lm.top(), lm.right(), lm.bottom()
        else:
            left = top = right = bottom = 0
        reserved_top = self._reserved_top() + top
        menu_x = left
        menu_w = max(0, self.width() - left - right)
        menu_y = reserved_top
        menu_h = max(0, self.height() - reserved_top - bottom)
        self.menu.setGeometry(menu_x, menu_y, menu_w, menu_h)
        self.menu.setMinimumWidth(menu_w)
        self.menu.lower()
        self._raise_layout_widgets()

    def _raise_layout_widgets(self):
        layout = self.layout()
        if not layout:
            return
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w and w is not self.menu:
                w.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        if self.menu:
            self.menu.lower()
            self._raise_layout_widgets()

    def fit_to_window(self):
        """Resize and lock the parent window to exact content size."""
        self.updateGeometry()
        window = self.window()
        if not window or window is self:
            return

        if window.layout():
            window.layout().activate()

        target = self.content_size()

        # Account for window chrome (header/footer added by MainWindow).
        # adjustSize would compute this for us, but we want a precise lock —
        # so derive chrome by comparing existing window size against this
        # widget's current size, then add it to the target.
        cw = window.centralWidget() if hasattr(window, "centralWidget") else None
        if cw is self and window.size().isValid() and self.size().isValid():
            chrome_w = max(0, window.width() - self.width())
            chrome_h = max(0, window.height() - self.height())
            target = QtCore.QSize(target.width() + chrome_w, target.height() + chrome_h)

        window.setMinimumSize(target)
        window.setMaximumSize(target)
        window.resize(target)


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
        """Retrieve a Maya menu, populated synchronously, and return its wrapper.

        Synchronous population is important so that callers (e.g. the marking
        menu) can fit-to-content the wrapping window before it is ever shown.
        Deferred population caused the window to first appear at the empty
        floor size, then resize a frame later.
        """
        menu_key = menu_key.lower()
        if menu_key in self.menus:
            return self.menus[menu_key]

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

        orig_menu_set = cmds.menuSet(q=True, label=True)
        self.logger.debug(
            f"Switching menu mode to '{target_menu_set}' (original: {orig_menu_set})"
        )
        cmds.setMenuMode(target_menu_set)
        try:
            mel.eval(init_command)
        except Exception as e:
            self.logger.warning(f"Menu init command for '{menu_key}' raised: {e}")
        cmds.refresh()

        placeholder_menu = PersistentMenu(maya_menu_name, UiUtils.get_main_window())
        placeholder_widget = EmbeddedMenuWidget(placeholder_menu)
        placeholder_widget.setObjectName(menu_key)
        self.menus[menu_key] = placeholder_widget

        self._populate_menu(menu_key, maya_menu_name, orig_menu_set, placeholder_widget)
        return placeholder_widget

    def _populate_menu(
        self,
        menu_key: str,
        maya_menu_name: str,
        orig_menu_set: str,
        placeholder_widget: "EmbeddedMenuWidget",
    ) -> None:
        """Synchronously copy actions from Maya's source menu into the wrapper."""
        main_window = UiUtils.get_main_window()
        menu_bar = main_window.menuBar()

        target_menu = None
        previous_action_count = -1
        stable_iterations = 0
        required_stable_iterations = 2
        max_attempts = 15
        attempt = 0

        while attempt < max_attempts:
            attempt += 1

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
                    stable_iterations = 0

                previous_action_count = current_action_count

                if stable_iterations >= required_stable_iterations:
                    self.logger.debug(
                        f"Menu '{maya_menu_name}' stabilized with {current_action_count} actions."
                    )
                    break
                self.logger.debug(
                    f"Waiting for menu '{maya_menu_name}' to stabilize... "
                    f"Currently {current_action_count} actions."
                )
            else:
                self.logger.debug(f"Menu '{maya_menu_name}' not found yet...")

            QtWidgets.QApplication.processEvents()

        cmds.setMenuMode(orig_menu_set)
        self.logger.debug(f"Restored original menu mode: {orig_menu_set}")

        if target_menu and previous_action_count > 0:
            placeholder_widget.menu.clear()
            for action in target_menu.actions():
                placeholder_widget.menu.addAction(action)
            self.logger.debug(
                f"Populated menu '{menu_key}' with {previous_action_count} actions."
            )
            placeholder_widget.menu.lower()
            placeholder_widget._raise_layout_widgets()
        else:
            self.logger.warning(
                f"Failed to fully initialize menu '{menu_key}' after {attempt} attempts."
            )

    def display_menu(self, menu_key: str):
        """Displays the specified Maya menu in a standalone window."""
        widget = self.get_menu(menu_key)
        if not widget:
            return

        window = MainWindow(
            name=f"MayaMenu_{menu_key}",
            switchboard_instance=None,
            central_widget=widget,
            add_footer=False,
            restore_window_size=False,
        )
        window.set_flags(
            Window=True,
            FramelessWindowHint=True,
            WindowStaysOnTopHint=True,
        )
        window.set_attributes(WA_TranslucentBackground=True)
        window.setProperty("class", "translucentBgWithBorder")

        widget.fit_to_window()
        window.show(pos="screen", app_exec=True)
        return window
