# !/usr/bin/python
# coding=utf-8

from typing import Optional
from PySide2 import QtWidgets, QtCore, QtGui
import pymel.core as pm
import pythontk as ptk

# From this package:
from mayatk.ui_utils import UiUtils


class EmbeddedMenuWidget(QtWidgets.QWidget):
    def __init__(self, menu, parent=None):
        super(EmbeddedMenuWidget, self).__init__(parent)
        self.menu = menu
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create a QWidgetAction to host the menu
        menu_action = QtWidgets.QWidgetAction(self)
        menu_action.setDefaultWidget(self.menu)

        # Create a toolbar to hold the menu action
        toolbar = QtWidgets.QToolBar()
        toolbar.addAction(menu_action)

        layout.addWidget(toolbar)


class MayaMenuHandler(ptk.LoggingMixin):
    menu_init = {
        "animation": (
            "Animation",
            "buildAnimationMenu MayaWindow|mainAnimationMenu",
            "animationMenuSet",
        ),
        "arnold": (
            "Arnold",
            "",
            "",
        ),  # This might require specific Arnold initialization
        "cache": (
            "Cache",
            "NucleusCacheMenu MayaWindow|mainCacheMenu",
            "dynamicsMenuSet",
        ),
        "constrain": (
            "Constrain",
            "AniConstraintsMenu MayaWindow|mainRigConstraintsMenu",
            "riggingMenuSet",
        ),
        "control": (
            "Control",
            "ChaControlsMenu MayaWindow|mainRigControlMenu",
            "riggingMenuSet",
        ),
        "create": (
            "Create",
            "editMenuUpdate MayaWindow|mainCreateMenu",
            "commonMenuSet",
        ),
        "curves": (
            "Curves",
            "ModelingCurvesMenu MayaWindow|mainCurvesMenu",
            "modelingMenuSet",
        ),
        "deform": (
            "Deform",
            "ChaDeformationsMenu MayaWindow|mainRigDeformationsMenu",
            "riggingMenuSet",
        ),
        "display": (
            "Display",
            "buildDisplayMenu MayaWindow|mainDisplayMenu",
            "commonMenuSet",
        ),
        "edit": ("Edit", "buildEditMenu MayaWindow|mainEditMenu", "commonMenuSet"),
        "edit_mesh": (
            "Edit Mesh",
            "PolygonsBuildMenu MayaWindow|mainEditMeshMenu",
            "modelingMenuSet",
        ),
        "effects": (
            "Effects",
            "DynEffectsMenu MayaWindow|mainDynEffectsMenu",
            "dynamicsMenuSet",
        ),
        "fields_solvers": (
            "Fields/Solvers",
            "DynFieldsSolverMenu MayaWindow|mainFieldsSolverMenu",
            "dynamicsMenuSet",
        ),
        "file": ("File", "buildFileMenu MayaWindow|mainFileMenu", "commonMenuSet"),
        "fluids": (
            "Fluids",
            "DynFluidsMenu MayaWindow|mainFluidsMenu",
            "dynamicsMenuSet",
        ),
        "generate": (
            "Generate",
            "ModelingGenerateMenu MayaWindow|mainGenerateMenu",
            "modelingMenuSet",
        ),
        "help": ("Help", "buildHelpMenu MayaWindow|mainHelpMenu", "commonMenuSet"),
        "key": ("Key", "AniKeyMenu MayaWindow|mainKeysMenu", "animationMenuSet"),
        "lighting_shading": (
            "Lighting/Shading",
            "RenShadersMenu MayaWindow|mainShadingMenu",
            "renderingMenuSet",
        ),
        "mash": ("MASH", "", ""),  # Specific initialization might be needed
        "mesh": ("Mesh", "PolygonsMeshMenu MayaWindow|mainMeshMenu", "modelingMenuSet"),
        "mesh_display": (
            "Mesh Display",
            "ModelingMeshDisplayMenu MayaWindow|mainMeshDisplayMenu",
            "modelingMenuSet",
        ),
        "mesh_tools": (
            "Mesh Tools",
            "PolygonsBuildToolsMenu MayaWindow|mainMeshToolsMenu",
            "modelingMenuSet",
        ),
        "modify": (
            "Modify",
            "ModObjectsMenu MayaWindow|mainModifyMenu",
            "commonMenuSet",
        ),
        "ncloth": (
            "nCloth",
            "DynClothMenu MayaWindow|mainNClothMenu",
            "dynamicsMenuSet",
        ),
        "nconstraint": (
            "nConstraint",
            "NucleusConstraintMenu MayaWindow|mainNConstraintMenu",
            "dynamicsMenuSet",
        ),
        "nhair": (
            "nHair",
            "DynCreateHairMenu MayaWindow|mainHairMenu",
            "dynamicsMenuSet",
        ),
        "nparticles": (
            "nParticles",
            "DynParticlesMenu MayaWindow|mainParticlesMenu",
            "dynamicsMenuSet",
        ),
        "playback": (
            "Playback",
            "AniPlaybackMenu MayaWindow|mainPlaybackMenu",
            "animationMenuSet",
        ),
        "rendering": (
            "Rendering",
            "RenRenderMenu MayaWindow|mainRenderMenu",
            "renderingMenuSet",
        ),
        "rigging": (
            "Rigging",
            "buildRiggingMenu MayaWindow|mainRiggingMenu",
            "riggingMenuSet",
        ),
        "select": (
            "Select",
            "buildSelectMenu MayaWindow|mainSelectMenu",
            "commonMenuSet",
        ),
        "skeleton": (
            "Skeleton",
            "ChaSkeletonsMenu MayaWindow|mainRigSkeletonsMenu",
            "riggingMenuSet",
        ),
        "skin": (
            "Skin",
            "ChaSkinningMenu MayaWindow|mainRigSkinningMenu",
            "riggingMenuSet",
        ),
        "stereo": (
            "Stereo",
            "RenStereoMenu MayaWindow|mainStereoMenu",
            "renderingMenuSet",
        ),
        "surfaces": (
            "Surfaces",
            "ModelingSurfacesMenu MayaWindow|mainSurfacesMenu",
            "modelingMenuSet",
        ),
        "texturing": (
            "Texturing",
            "RenTexturingMenu MayaWindow|mainRenTexturingMenu",
            "renderingMenuSet",
        ),
        "toon": ("Toon", "buildToonMenu MayaWindow|mainToonMenu", "renderingMenuSet"),
        "uv": ("UV", "ModelingUVMenu MayaWindow|mainUVMenu", "modelingMenuSet"),
        "visualize": (
            "Visualize",
            "AniVisualizeMenu MayaWindow|mainVisualizeMenu",
            "animationMenuSet",
        ),
        "windows": (
            "Windows",
            "buildViewMenu MayaWindow|mainWindowMenu",
            "commonMenuSet",
        ),
    }

    def __init__(self):
        super().__init__()
        self.menus = {}

    def __getattr__(self, menu_key: str) -> Optional["QtWidgets.QMenu"]:
        """Dynamically retrieve and return a Maya menu by its python-friendly name."""
        return self.get_menu(menu_key)

    def initialize_menu(self, menu_key: str):
        """Initializes a Maya menu using the mapped name and initialization command."""
        menu_key = menu_key.lower()
        maya_menu_name, init_command, required_menu_set = self.menu_init.get(
            menu_key, (None, None, None)
        )

        if not (maya_menu_name and init_command):
            self.logger.warning(
                f"No initialization command found for menu '{menu_key}'"
            )
            return

        # Get the current menu set before switching
        original_menu_set = pm.mel.menuSet(q=True, label=True)
        self.logger.debug(f"Current menu set: {original_menu_set}")

        # Ensure the required menu set exists before switching
        all_menu_sets = pm.mel.menuSet(q=True, allMenuSets=True)
        if required_menu_set and required_menu_set in all_menu_sets:
            self.logger.debug(f"Switching to temp menu set: {required_menu_set}")
            pm.mel.eval(f'setMenuMode("{required_menu_set}")')

            # Manually hide the current menu set and show the new set's menus
            menus_in_required_set = pm.mel.menuSet(q=True, menuArray=True)
            if menus_in_required_set:
                for menu in menus_in_required_set:
                    pm.menu(menu, edit=True, visible=True)

            current_menu_set = pm.mel.menuSet(q=True, label=True)
            self.logger.debug(f"Switched to menu set: {current_menu_set}")
        else:
            self.logger.warning(
                f"Menu set '{required_menu_set}' does not exist. Skipping switch."
            )

        # Build the menu
        self.logger.debug(f"Running command: {init_command}")
        pm.mel.eval(init_command)
        self.logger.debug(f"Initialized menu '{menu_key}' with command: {init_command}")

        # Place the delay here, after the menu set switch and menu visibility adjustments
        QtCore.QTimer.singleShot(
            100,
            lambda: self._finalize_menu_init(menus_in_required_set, original_menu_set),
        )

    def _finalize_menu_init(self, menus_in_required_set, original_menu_set):
        """Finalize the menu initialization process by restoring the original menu set."""
        # Restore the original menu set using setMenuMode
        self.logger.debug(f"Restoring original menu set: {original_menu_set}")
        pm.mel.eval(f'setMenuMode("{original_menu_set}")')

        # Manually hide the temporary set's menus and restore the original set's menus
        if menus_in_required_set:
            for menu in menus_in_required_set:
                pm.menu(menu, edit=True, visible=False)

        menus_in_original_set = pm.mel.menuSet(q=True, menuArray=True)
        if menus_in_original_set:
            for menu in menus_in_original_set:
                pm.menu(menu, edit=True, visible=True)

    def get_menu(self, menu_key: str) -> Optional["EmbeddedMenuWidget"]:
        """Retrieves, duplicates, and wraps a Maya menu into an EmbeddedMenuWidget."""
        menu_key = menu_key.lower()
        # Check if the menu_key exists in the map
        if menu_key not in self.menu_init:
            self.logger.error(f"Menu '{menu_key}' not found in the mapping.")
            return None

        # Check if the menu has already been wrapped and stored
        if menu_key in self.menus:
            self.logger.debug(
                f"Returning stored embedded menu widget for '{menu_key}'."
            )
            return self.menus[menu_key]

        # Extract the actual Maya menu name, init_command, and menu set (ignore command and set here)
        maya_menu_name, _, _ = self.menu_init[menu_key]

        # Initialize the Maya menu if necessary
        self.initialize_menu(menu_key)

        # Search for the target menu by name in the main menu bar
        main_window = UiUtils.get_main_window()
        menu_bar = main_window.menuBar()
        target_menu = None

        for action in menu_bar.actions():
            if action.text() == maya_menu_name:
                target_menu = action.menu()
                break

        if target_menu:
            # Duplicate the menu
            duplicate_menu = QtWidgets.QMenu(maya_menu_name, main_window)
            duplicate_menu.setObjectName(maya_menu_name)

            # Copy actions from the original menu to the new menu
            for action in target_menu.actions():
                duplicate_menu.addAction(action)

            # Wrap the duplicate menu in an EmbeddedMenuWidget
            embedded_menu_widget = EmbeddedMenuWidget(duplicate_menu)
            embedded_menu_widget.setObjectName(menu_key)

            # Store the embedded menu widget to prevent garbage collection
            self.menus[menu_key] = embedded_menu_widget
            self.logger.debug(
                f"Duplicated menu '{menu_key}', wrapped it in EmbeddedMenuWidget, and stored it."
            )
            return embedded_menu_widget
        else:
            self.logger.error(f"Failed to find menu '{menu_key}'.")
            return None


# --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    handler = MayaMenuHandler()
    menu = handler.get_menu("constrain")
    print(repr(menu))
    menu.show()
