# !/usr/bin/python
# coding=utf-8

from typing import Optional
from PySide2 import QtWidgets, QtCore, QtGui
import pymel.core as pm
import pythontk as ptk

# From this package:
from mayatk.ui_utils import UiUtils


class MayaMenuHandler(ptk.LoggingMixin):
    menu_init = {
        "file": ("File", "buildFileMenu()"),
        "edit": ("Edit", "buildEditMenu MayaWindow|mainEditMenu"),
        "create": ("Create", "editMenuUpdate MayaWindow|mainCreateMenu"),
        "select": ("Select", "buildSelectMenu MayaWindow|mainSelectMenu"),
        "modify": ("Modify", "ModObjectsMenu MayaWindow|mainModifyMenu"),
        "display": ("Display", "buildDisplayMenu MayaWindow|mainDisplayMenu"),
        "windows": ("Windows", "buildViewMenu MayaWindow|mainWindowMenu"),
        "mesh": ("Mesh", "PolygonsMeshMenu MayaWindow|mainMeshMenu"),
        "edit_mesh": ("Edit Mesh", "PolygonsBuildMenu MayaWindow|mainEditMeshMenu"),
        "generate": ("Generate", "ModelingGenerateMenu MayaWindow|mainGenerateMenu"),
        "mesh_tools": (
            "Mesh Tools",
            "PolygonsBuildToolsMenu MayaWindow|mainMeshToolsMenu",
        ),
        "mesh_display": (
            "Mesh Display",
            "ModelingMeshDisplayMenu MayaWindow|mainMeshDisplayMenu",
        ),
        "curves": ("Curves", "ModelingCurvesMenu MayaWindow|mainCurvesMenu"),
        "surfaces": ("Surfaces", "ModelingSurfacesMenu MayaWindow|mainSurfacesMenu"),
        "deform": ("Deform", "ChaDeformationsMenu MayaWindow|mainDeformMenu"),
        "animation": ("Animation", "buildAnimationMenu MayaWindow|mainAnimationMenu"),
        "rigging": ("Rigging", "buildRiggingMenu MayaWindow|mainRiggingMenu"),
        "rendering": ("Rendering", "RenRenderMenu MayaWindow|mainRenderMenu"),
        "texturing": ("Texturing", "RenTexturingMenu MayaWindow|mainRenTexturingMenu"),
        "uv": ("UV", "ModelingUVMenu MayaWindow|mainUVMenu"),
        "key": ("Key", "AniKeyMenu MayaWindow|mainKeysMenu"),
        "constraint": (
            "Constraint",
            "AniConstraintsMenu MayaWindow|mainRigConstraintsMenu",
        ),
        "skeleton": ("Skeleton", "ChaSkeletonsMenu MayaWindow|mainRigSkeletonsMenu"),
        "skin": ("Skin", "ChaSkinningMenu MayaWindow|mainRigSkinningMenu"),
        "lighting_shading": (
            "Lighting/Shading",
            "RenShadersMenu MayaWindow|mainShadingMenu",
        ),
        "ncloth": ("nCloth", "DynClothMenu MayaWindow|mainNClothMenu"),
        "nhair": ("nHair", "DynCreateHairMenu MayaWindow|mainHairMenu"),
        "nparticles": ("nParticles", "DynParticlesMenu MayaWindow|mainParticlesMenu"),
        "nconstraint": (
            "nConstraint",
            "NucleusConstraintMenu MayaWindow|mainNConstraintMenu",
        ),
        "fluids": ("Fluids", "DynFluidsMenu MayaWindow|mainFluidsMenu"),
        "fields_solvers": (
            "Fields/Solvers",
            "DynFieldsSolverMenu MayaWindow|mainFieldsSolverMenu",
        ),
        "effects": ("Effects", "DynEffectsMenu MayaWindow|mainDynEffectsMenu"),
        "toon": ("Toon", "buildToonMenu MayaWindow|mainToonMenu"),
        "playback": ("Playback", "AniPlaybackMenu MayaWindow|mainPlaybackMenu"),
        "visualize": ("Visualize", "AniVisualizeMenu MayaWindow|mainVisualizeMenu"),
        "stereo": ("Stereo", "RenStereoMenu MayaWindow|mainStereoMenu"),
        "help": ("Help", "buildHelpMenu MayaWindow|mainHelpMenu"),
        "mash": ("MASH", ""),
        "arnold": ("Arnold", ""),
        "cache": ("Cache", ""),
    }

    def __init__(self):
        super().__init__()
        self.menus = {}

    def initialize_menu(self, menu_key: str):
        """Initializes a Maya menu using the mapped name and initialization command."""
        menu_key = menu_key.lower()
        maya_menu_name, init_command = self.menu_init.get(menu_key, (None, None))
        if maya_menu_name and init_command:
            try:
                pm.mel.eval(init_command)
                self.logger.info(
                    f"Initialized menu '{menu_key}' with command: {init_command}"
                )
            except Exception as e:
                self.logger.error(f"Failed to initialize menu '{menu_key}', Error: {e}")
        else:
            self.logger.warning(
                f"No initialization command found for menu '{menu_key}'"
            )

    def get_menu(self, menu_key: str) -> Optional["QtWidgets.QMenu"]:
        """Retrieves and duplicates a Maya menu by copying its actions.
        Uses the lowercase menu_key for explicit mapping.
        """
        menu_key = menu_key.lower()
        # Check if the menu_key exists in the map
        if menu_key not in self.menu_init:
            self.logger.error(f"Menu '{menu_key}' not found in the mapping.")
            return None

        # Extract the actual Maya menu name
        maya_menu_name, _ = self.menu_init[menu_key]

        # Check if the menu has already been duplicated and stored
        if menu_key in self.menus:
            self.logger.info(f"Returning stored duplicated menu for '{menu_key}'.")
            return self.menus[menu_key]

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
            duplicate_menu.setObjectName(menu_key)

            # Copy actions from the original menu to the new menu
            for action in target_menu.actions():
                duplicate_menu.addAction(action)

            # Store the duplicated menu to prevent garbage collection
            self.menus[menu_key] = duplicate_menu
            self.logger.info(f"Duplicated menu '{menu_key}' and stored it.")
            return duplicate_menu
        else:
            self.logger.error(f"Failed to find menu '{menu_key}'.")
            return None

    def __getattr__(self, menu_key: str) -> Optional["QtWidgets.QMenu"]:
        """Dynamically retrieve and return a Maya menu by its python-friendly name."""
        return self.get_menu(menu_key)

    def create_tool_menu(self, menu_name: str) -> Optional["QtWidgets.QMainWindow"]:
        """Converts a duplicated Maya menu into a floating tool menu."""
        # Retrieve the duplicated menu
        menu = self.get_menu(menu_name)
        if not menu:
            self.logger.error(f"Failed to retrieve the menu '{menu_name}'.")
            return None

        self.logger.info(f"Converting menu '{menu_name}' to a floating tool menu.")

        # Create a new QMainWindow or QWidget as the parent for the floating menu
        main_window = UiUtils.get_main_window()
        tool_window = QtWidgets.QMainWindow(main_window)
        tool_window.setWindowTitle(menu_name)
        tool_window.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowStaysOnTopHint)

        # Set the duplicated menu as the menu of the tool window
        tool_window.setMenuWidget(menu)
        tool_window.resize(menu.sizeHint().width(), menu.sizeHint().height())

        # Store the tool window to prevent garbage collection
        self.menus[menu_name] = tool_window

        return tool_window


# --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    # Safely create and display the duplicated menu
    handler = MayaMenuHandler()
    menu = handler.create_tool_menu("edit_mesh")
    print(repr(menu))
    menu.show()
    cursor_pos = QtGui.QCursor.pos()
    menu.move(cursor_pos - QtCore.QPoint(menu.width() / 2, menu.height() / 2))
