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
            "buildAnimationMenu MayaWindow|mainAnimationMenu; setMenuMode animationMenuSet",
        ),
        "arnold": ("Arnold", ""),  # This might require specific Arnold initialization
        "cache": (
            "Cache",
            "NucleusCacheMenu MayaWindow|mainCacheMenu; setMenuMode dynamicsMenuSet",
        ),
        "constrain": (
            "Constrain",
            "AniConstraintsMenu MayaWindow|mainRigConstraintsMenu; setMenuMode riggingMenuSet",
        ),
        "control": (
            "Control",
            "ChaControlsMenu MayaWindow|mainRigControlMenu; setMenuMode riggingMenuSet",
        ),
        "create": (
            "Create",
            "editMenuUpdate MayaWindow|mainCreateMenu; setMenuMode commonMenuSet",
        ),
        "curves": (
            "Curves",
            "ModelingCurvesMenu MayaWindow|mainCurvesMenu; setMenuMode modelingMenuSet",
        ),
        "deform": (
            "Deform",
            "ChaDeformationsMenu MayaWindow|mainRigDeformationsMenu; setMenuMode riggingMenuSet",
        ),
        "display": (
            "Display",
            "buildDisplayMenu MayaWindow|mainDisplayMenu; setMenuMode commonMenuSet",
        ),
        "edit": (
            "Edit",
            "buildEditMenu MayaWindow|mainEditMenu; setMenuMode commonMenuSet",
        ),
        "edit_mesh": (
            "Edit Mesh",
            "PolygonsBuildMenu MayaWindow|mainEditMeshMenu; setMenuMode modelingMenuSet",
        ),
        "effects": (
            "Effects",
            "DynEffectsMenu MayaWindow|mainDynEffectsMenu; setMenuMode dynamicsMenuSet",
        ),
        "fields_solvers": (
            "Fields/Solvers",
            "DynFieldsSolverMenu MayaWindow|mainFieldsSolverMenu; setMenuMode dynamicsMenuSet",
        ),
        "file": (
            "File",
            "buildFileMenu MayaWindow|mainFileMenu; setMenuMode commonMenuSet",
        ),
        "fluids": (
            "Fluids",
            "DynFluidsMenu MayaWindow|mainFluidsMenu; setMenuMode dynamicsMenuSet",
        ),
        "generate": (
            "Generate",
            "ModelingGenerateMenu MayaWindow|mainGenerateMenu; setMenuMode modelingMenuSet",
        ),
        "help": (
            "Help",
            "buildHelpMenu MayaWindow|mainHelpMenu; setMenuMode commonMenuSet",
        ),
        "key": (
            "Key",
            "AniKeyMenu MayaWindow|mainKeysMenu; setMenuMode animationMenuSet",
        ),
        "lighting_shading": (
            "Lighting/Shading",
            "RenShadersMenu MayaWindow|mainShadingMenu; setMenuMode renderingMenuSet",
        ),
        "mash": ("MASH", ""),  # Specific initialization might be needed
        "mesh": (
            "Mesh",
            "PolygonsMeshMenu MayaWindow|mainMeshMenu; setMenuMode modelingMenuSet",
        ),
        "mesh_display": (
            "Mesh Display",
            "ModelingMeshDisplayMenu MayaWindow|mainMeshDisplayMenu; setMenuMode modelingMenuSet",
        ),
        "mesh_tools": (
            "Mesh Tools",
            "PolygonsBuildToolsMenu MayaWindow|mainMeshToolsMenu; setMenuMode modelingMenuSet",
        ),
        "modify": (
            "Modify",
            "ModObjectsMenu MayaWindow|mainModifyMenu; setMenuMode commonMenuSet",
        ),
        "ncloth": (
            "nCloth",
            "DynClothMenu MayaWindow|mainNClothMenu; setMenuMode dynamicsMenuSet",
        ),
        "nconstraint": (
            "nConstraint",
            "NucleusConstraintMenu MayaWindow|mainNConstraintMenu; setMenuMode dynamicsMenuSet",
        ),
        "nhair": (
            "nHair",
            "DynCreateHairMenu MayaWindow|mainHairMenu; setMenuMode dynamicsMenuSet",
        ),
        "nparticles": (
            "nParticles",
            "DynParticlesMenu MayaWindow|mainParticlesMenu; setMenuMode dynamicsMenuSet",
        ),
        "playback": (
            "Playback",
            "AniPlaybackMenu MayaWindow|mainPlaybackMenu; setMenuMode animationMenuSet",
        ),
        "rendering": (
            "Rendering",
            "RenRenderMenu MayaWindow|mainRenderMenu; setMenuMode renderingMenuSet",
        ),
        "rigging": (
            "Rigging",
            "buildRiggingMenu MayaWindow|mainRiggingMenu; setMenuMode riggingMenuSet",
        ),
        "select": (
            "Select",
            "buildSelectMenu MayaWindow|mainSelectMenu; setMenuMode commonMenuSet",
        ),
        "skeleton": (
            "Skeleton",
            "ChaSkeletonsMenu MayaWindow|mainRigSkeletonsMenu; setMenuMode riggingMenuSet",
        ),
        "skin": (
            "Skin",
            "ChaSkinningMenu MayaWindow|mainRigSkinningMenu; setMenuMode riggingMenuSet",
        ),
        "stereo": (
            "Stereo",
            "RenStereoMenu MayaWindow|mainStereoMenu; setMenuMode renderingMenuSet",
        ),
        "surfaces": (
            "Surfaces",
            "ModelingSurfacesMenu MayaWindow|mainSurfacesMenu; setMenuMode modelingMenuSet",
        ),
        "texturing": (
            "Texturing",
            "RenTexturingMenu MayaWindow|mainRenTexturingMenu; setMenuMode renderingMenuSet",
        ),
        "toon": (
            "Toon",
            "buildToonMenu MayaWindow|mainToonMenu; setMenuMode renderingMenuSet",
        ),
        "uv": (
            "UV",
            "ModelingUVMenu MayaWindow|mainUVMenu; setMenuMode modelingMenuSet",
        ),
        "visualize": (
            "Visualize",
            "AniVisualizeMenu MayaWindow|mainVisualizeMenu; setMenuMode animationMenuSet",
        ),
        "windows": (
            "Windows",
            "buildViewMenu MayaWindow|mainWindowMenu; setMenuMode commonMenuSet",
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
        maya_menu_name, init_command = self.menu_init.get(menu_key, (None, None))
        if maya_menu_name and init_command:
            try:
                pm.mel.eval(init_command)
                self.logger.debug(
                    f"Initialized menu '{menu_key}' with command: {init_command}"
                )
            except Exception as e:
                self.logger.error(f"Failed to initialize menu '{menu_key}', Error: {e}")
        else:
            self.logger.warning(
                f"No initialization command found for menu '{menu_key}'"
            )

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

        # Extract the actual Maya menu name
        maya_menu_name, _ = self.menu_init[menu_key]

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
    # Safely create and display the duplicated menu
    handler = MayaMenuHandler()
    menu = handler.create_tool_menu("edit_mesh")
    print(repr(menu))
    menu.show()
    cursor_pos = QtGui.QCursor.pos()
    menu.move(cursor_pos - QtCore.QPoint(menu.width() / 2, menu.height() / 2))
