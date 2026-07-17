# !/usr/bin/python
# coding=utf-8
import maya.cmds as cmds
import maya.mel as mel
from typing import Optional
from qtpy import QtWidgets
import pythontk as ptk
from uitk.widgets.mainWindow import MainWindow

# Shared menu-embed widgets (moved to uitk so blendertk's harvested menus reuse
# them); re-exported here because both are long-standing members of this
# module's public surface.
from uitk.widgets.embeddedMenu import EmbeddedMenuWidget, PersistentMenu  # noqa: F401

# From this package:
from mayatk.ui_utils._ui_utils import UiUtils


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

        try:
            orig_menu_set = cmds.menuSet(q=True, label=True)
        except RuntimeError:
            # Batch / standalone Maya has no menu sets — and no native menus
            # to clone. Bail cleanly instead of raising out of the panel open.
            self.logger.debug(f"No menu sets in this session; skipping '{menu_key}'.")
            return None
        self.logger.debug(
            f"Switching menu mode to '{target_menu_set}' (original: {orig_menu_set})"
        )
        cmds.setMenuMode(target_menu_set)
        # Everything from here runs with the menu mode switched; the finally
        # guarantees the user's original menu set comes back on EVERY exit —
        # a stuck mode silently swaps the whole main-window menu bar.
        try:
            try:
                mel.eval(init_command)
            except Exception as e:
                # The init command is Maya-version-specific. Several mappings are
                # stale on newer Maya — build procs renamed (``buildToonMenu``
                # gone), arguments changed (``buildHelpMenu`` now takes none), or
                # the target menu shell no longer exists (``mainCacheMenu``). A
                # raise here means the native menu can't be built in this Maya, so
                # bail immediately and let the caller fall back to the hand-authored
                # ``<key>#submenu`` overlay. Previously this only warned, then spun
                # a doomed 15-attempt ``processEvents`` populate loop — that loop,
                # not the error itself, was the bulk of the multi-second stall when
                # a broken menu was requested (e.g. toon ~10s, help ~5s).
                self.logger.debug(f"Native menu '{menu_key}' unavailable: {e}")
                return None
            cmds.refresh()

            placeholder_menu = PersistentMenu(maya_menu_name, UiUtils.get_main_window())
            placeholder_widget = EmbeddedMenuWidget(placeholder_menu)
            # Suffixed, never the bare menu_key: MainWindow binds every registered
            # child's objectName as an attribute, and keys like 'render'/'create'
            # match QWidget methods. Lookups go through self.menus, not objectName.
            placeholder_widget.setObjectName(f"{menu_key}_menu")

            try:
                populated = self._populate_menu(
                    menu_key, maya_menu_name, placeholder_widget
                )
            except Exception as e:
                self.logger.debug(f"Native menu '{menu_key}' populate failed: {e}")
                populated = False
            if not populated:
                # Stale shell / dead main window / zero actions — never cache or
                # return an empty wrapper (the ``menu_key in self.menus`` fast
                # path would hand it back forever). The caller falls back to the
                # hand-authored ``<key>#submenu`` overlay instead.
                placeholder_widget.deleteLater()
                return None

            # Cache only a successfully populated menu.
            self.menus[menu_key] = placeholder_widget
            return placeholder_widget
        finally:
            try:
                cmds.setMenuMode(orig_menu_set)
            except Exception:
                pass

    def _populate_menu(
        self,
        menu_key: str,
        maya_menu_name: str,
        placeholder_widget: "EmbeddedMenuWidget",
    ) -> bool:
        """Synchronously copy actions from Maya's source menu into the wrapper.

        Returns True when at least one action was copied. Menu-mode restore is
        owned by :meth:`get_menu`'s ``finally`` — not here — so no exit path
        can leave Maya switched into the target menu set.
        """
        main_window = UiUtils.get_main_window()
        if main_window is None:
            self.logger.debug(f"No Maya main window; cannot populate '{menu_key}'.")
            return False
        menu_bar = main_window.menuBar()

        target_menu = None
        previous_action_count = -1
        stable_iterations = 0
        required_stable_iterations = 2
        # Working menus populate synchronously right after the init command, so
        # they stabilize within ~3 passes. A higher cap only burned wall-clock
        # pumping ``processEvents`` for menus that never populate; the dead ones
        # are now caught earlier (init raises -> get_menu bails), so keep this
        # tight as a backstop for the genuinely-async case.
        max_attempts = 6
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

        if target_menu and previous_action_count > 0:
            placeholder_widget.menu.clear()
            for action in target_menu.actions():
                placeholder_widget.menu.addAction(action)
            self.logger.debug(
                f"Populated menu '{menu_key}' with {previous_action_count} actions."
            )
            placeholder_widget.menu.lower()
            placeholder_widget._raise_layout_widgets()
            return True

        self.logger.debug(
            f"Native menu '{menu_key}' did not populate after {attempt} attempts."
        )
        return False

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
