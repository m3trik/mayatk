# !/usr/bin/python
# coding=utf-8
from typing import Optional
from qtpy import QtWidgets
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
        self.scale_by_percentage(50)

    def scale_by_percentage(self, percentage: float):
        """Scales the window and its content by a percentage using the layout system.

        Optionally sets the new size as fixed.
        """
        if percentage <= 0:
            raise ValueError("Percentage must be greater than 0")

        # Normalizing percentage: 100% means no change, <100% shrinks, >100% enlarges
        scale_factor = percentage / 100

        # Adjust the size of the entire window
        current_size = self.size()
        new_width = int(current_size.width() * scale_factor)
        new_height = int(current_size.height() * scale_factor)

        # Resize the window
        self.resize(new_width, new_height)

        # Ensure the layout resizes properly
        self.layout().update()  # Make sure the layout recalculates its size


class MayaMenuHandler(ptk.LoggingMixin):
    menu_init = {
        "animation": (
            "Animation",
            "buildAnimationMenu MayaWindow|mainAnimationMenu",
        ),
        "arnold": ("Arnold", ""),  # Specific Arnold initialization
        "cache": (
            "Cache",
            "NucleusCacheMenu MayaWindow|mainCacheMenu",
        ),
        "constrain": (
            "Constrain",
            "AniConstraintsMenu MayaWindow|mainRigConstraintsMenu",
        ),
        "control": (
            "Control",
            "ChaControlsMenu MayaWindow|mainRigControlMenu",
        ),
        "create": (
            "Create",
            "editMenuUpdate MayaWindow|mainCreateMenu",
        ),
        "curves": (
            "Curves",
            "ModelingCurvesMenu MayaWindow|mainCurvesMenu",
        ),
        "deform": (
            "Deform",
            "ChaDeformationsMenu MayaWindow|mainRigDeformationsMenu",
        ),
        "display": (
            "Display",
            "buildDisplayMenu MayaWindow|mainDisplayMenu",
        ),
        "edit": ("Edit", "buildEditMenu MayaWindow|mainEditMenu"),
        "edit_mesh": (
            "Edit Mesh",
            "PolygonsBuildMenu MayaWindow|mainEditMeshMenu",
        ),
        "effects": (
            "Effects",
            "DynEffectsMenu MayaWindow|mainDynEffectsMenu",
        ),
        "fields_solvers": (
            "Fields/Solvers",
            "DynFieldsSolverMenu MayaWindow|mainFieldsSolverMenu",
        ),
        "file": ("File", "buildFileMenu MayaWindow|mainFileMenu"),
        "fluids": (
            "Fluids",
            "DynFluidsMenu MayaWindow|mainFluidsMenu",
        ),
        "generate": (
            "Generate",
            "ModelingGenerateMenu MayaWindow|mainGenerateMenu",
        ),
        "help": ("Help", "buildHelpMenu MayaWindow|mainHelpMenu"),
        "key": ("Key", "AniKeyMenu MayaWindow|mainKeysMenu"),
        "lighting_shading": (
            "Lighting/Shading",
            "RenShadersMenu MayaWindow|mainShadingMenu",
        ),
        "mash": ("MASH", ""),  # Specific initialization might be needed
        "mesh": (
            "Mesh",
            "PolygonsMeshMenu MayaWindow|mainMeshMenu",
        ),
        "mesh_display": (
            "Mesh Display",
            "ModelingMeshDisplayMenu MayaWindow|mainMeshDisplayMenu",
        ),
        "mesh_tools": (
            "Mesh Tools",
            "PolygonsBuildToolsMenu MayaWindow|mainMeshToolsMenu",
        ),
        "modify": (
            "Modify",
            "ModObjectsMenu MayaWindow|mainModifyMenu",
        ),
        "ncloth": (
            "nCloth",
            "DynClothMenu MayaWindow|mainNClothMenu",
        ),
        "nconstraint": (
            "nConstraint",
            "NucleusConstraintMenu MayaWindow|mainNConstraintMenu",
        ),
        "nhair": (
            "nHair",
            "DynCreateHairMenu MayaWindow|mainHairMenu",
        ),
        "nparticles": (
            "nParticles",
            "DynParticlesMenu MayaWindow|mainParticlesMenu",
        ),
        "playback": (
            "Playback",
            "AniPlaybackMenu MayaWindow|mainPlaybackMenu",
        ),
        "rendering": (
            "Rendering",
            "RenRenderMenu MayaWindow|mainRenderMenu",
        ),
        "rigging": (
            "Rigging",
            "buildRiggingMenu MayaWindow|mainRiggingMenu",
        ),
        "select": (
            "Select",
            "buildSelectMenu MayaWindow|mainSelectMenu",
        ),
        "skeleton": (
            "Skeleton",
            "ChaSkeletonsMenu MayaWindow|mainRigSkeletonsMenu",
        ),
        "skin": (
            "Skin",
            "ChaSkinningMenu MayaWindow|mainRigSkinningMenu",
        ),
        "stereo": (
            "Stereo",
            "RenStereoMenu MayaWindow|mainStereoMenu",
        ),
        "surfaces": (
            "Surfaces",
            "ModelingSurfacesMenu MayaWindow|mainSurfacesMenu",
        ),
        "texturing": (
            "Texturing",
            "RenTexturingMenu MayaWindow|mainRenTexturingMenu",
        ),
        "toon": ("Toon", "buildToonMenu MayaWindow|mainToonMenu"),
        "uv": ("UV", "ModelingUVMenu MayaWindow|mainUVMenu"),
        "visualize": (
            "Visualize",
            "AniVisualizeMenu MayaWindow|mainVisualizeMenu",
        ),
        "windows": (
            "Windows",
            "buildViewMenu MayaWindow|mainWindowMenu",
        ),
    }

    def __init__(self, log_level: str = "WARNING"):
        super().__init__()
        self.menus = {}
        self.logger.setLevel(log_level)

        # Get the current menu set before iterating
        original_menu_set = pm.mel.menuSet(q=True, label=True)
        self.logger.debug(f"Original menu set: {original_menu_set}")

        # Get all available menu sets
        all_menu_sets = pm.mel.menuSet(q=True, allMenuSets=True)
        self.logger.debug(f"All menu sets: {all_menu_sets}")

        # Iterate over all menu sets to initialize them
        for menu_set in all_menu_sets:
            self.logger.debug(f"Switching to menu set: {menu_set}")
            pm.mel.eval(f'setMenuMode("{menu_set}")')

        # Restore the original menu set after initialization
        self.logger.debug(f"Restoring original menu set: {original_menu_set}")
        pm.mel.eval(f'setMenuMode("{original_menu_set}")')

    def __getattr__(self, menu_key: str) -> Optional[QtWidgets.QMenu]:
        """Retrieve Maya menu if menu_key is a valid menu name."""
        if menu_key in self.menu_init:
            return self.get_menu(menu_key)
        else:  # Default behavior for other attribute accesses
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{menu_key}'"
            )

    def initialize_menu(self, menu_key: str):
        """Initializes a Maya menu using the mapped name and initialization command."""
        menu_key = menu_key.lower()
        maya_menu_name, init_command = self.menu_init.get(menu_key, (None, None))

        if not (maya_menu_name and init_command):
            self.logger.warning(
                f"No initialization command found for menu '{menu_key}'"
            )
            return

        # Directly run the initialization command without switching menu sets
        self.logger.debug(f"Running command: {init_command}")
        pm.mel.eval(init_command)
        self.logger.debug(f"Initialized menu '{menu_key}' with command: {init_command}")

    def get_menu(self, menu_key: str) -> Optional["EmbeddedMenuWidget"]:
        """Retrieves, duplicates, and wraps a Maya menu into an EmbeddedMenuWidget."""
        import traceback

        menu_key = menu_key.lower()

        # Debug info to track where get_menu is being called from
        self.logger.debug(f"Accessing get_menu for '{menu_key}'")
        self.logger.debug(f"Current self.menus: {self.menus}")
        self.logger.debug("Call stack:")
        for line in traceback.format_stack():
            self.logger.debug(line.strip())

        if menu_key in self.menus:
            self.logger.debug(f"Returning stored embedded menu widget for '{menu_key}'")
            return self.menus[menu_key]

        # Proceed with menu initialization and duplication if necessary
        maya_menu_name, _ = self.menu_init.get(menu_key, (None, None))
        if not maya_menu_name:
            self.logger.error(f"Menu '{menu_key}' not found in the mapping.")
            return None

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
            # Duplicate the menu and wrap it
            duplicate_menu = QtWidgets.QMenu(maya_menu_name, main_window)
            duplicate_menu.setObjectName(maya_menu_name)

            for action in target_menu.actions():
                duplicate_menu.addAction(action)

            embedded_menu_widget = EmbeddedMenuWidget(duplicate_menu)
            embedded_menu_widget.setObjectName(menu_key)

            # Store the menu to prevent duplicate initialization
            self.menus[menu_key] = embedded_menu_widget
            self.logger.debug(f"Duplicated and stored menu '{menu_key}'.")

            return embedded_menu_widget
        else:
            self.logger.error(f"Failed to find menu '{menu_key}'.")
            return None


# --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    handler = MayaMenuHandler()
    menu = handler.get_menu("skin")
    print(repr(menu))
    menu.show()
