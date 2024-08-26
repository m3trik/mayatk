# !/usr/bin/python
# coding=utf-8
import os
import re
import ctypes
import shutil
from datetime import datetime
from typing import List, Dict, Optional, Callable, Union, Any

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.core_utils import CoreUtils
from mayatk.anim_utils import AnimUtils
from mayatk.env_utils import EnvUtils
from mayatk.mat_utils import MatUtils
from mayatk.display_utils import DisplayUtils


class SceneExporterChecks:
    def __init__(self, logger):
        self.logger = logger

    def run_checks(self, objects, materials, **kwargs) -> bool:
        """Run checks based on the provided kwargs.
        Checks are run in the order they are specified in the kwargs.
        """
        self.objects = objects
        self.materials = materials
        self.material_paths = MatUtils.collect_material_paths(
            self.materials,
            include_material=True,
            include_path_type=True,
            nested_as_unit=True,
        )

        for check_name, value in kwargs.items():
            check_method = getattr(self, check_name, None)

            if check_method:
                self.logger.debug(f"Running check: {check_name} with value: {value}")

                try:
                    # Try calling the check method without arguments
                    if not check_method():
                        self.logger.error(
                            f"Check {check_name} failed without arguments."
                        )
                        return False

                except TypeError:
                    # If TypeError occurs, call the method with the provided value
                    self.logger.debug(
                        f"Retrying check {check_name} with argument {value}."
                    )
                    if not check_method(value):
                        self.logger.error(
                            f"Check {check_name} failed with value: {value}"
                        )
                        return False

            else:
                self.logger.warning(f"No check method found for {check_name}")

        return True

    def check_absolute_paths(self) -> bool:
        all_relative = True
        for mat, typ, pth in self.material_paths:
            if typ == "Absolute":
                if all_relative:
                    all_relative = False
                    self.logger.error("Absolute path(s) found:")
                self.logger.error(f"\t{typ} path - {mat.name()} - {pth}")
        if not all_relative:
            self.logger.debug("check_absolute_paths failed due to absolute paths")
            return False
        self.logger.debug("check_absolute_paths passed")
        return True

    def check_duplicate_materials(self) -> bool:
        duplicate_mapping = MatUtils.find_materials_with_duplicate_textures(
            self.materials
        )
        if duplicate_mapping:
            self.logger.error("Duplicate material(s) found:")
            for original, duplicates in duplicate_mapping.items():
                for duplicate in duplicates:
                    self.logger.error(f"\tDuplicate: {duplicate} -> {original}")
            return False
        self.logger.debug("check_duplicate_materials passed")
        return True

    def check_referenced_objects(self) -> bool:
        referenced_objects = pm.ls(self.objects, references=True)
        if referenced_objects:
            self.logger.error("Referenced object(s) found:")
            for ref in referenced_objects:
                self.logger.error(f"\t{ref}")
            return False
        self.logger.debug("check_referenced_objects passed")
        return True

    def check_hidden_geometry_with_keys(self) -> bool:
        hidden_geometry = [
            node
            for node in AnimUtils.filter_objects_with_keys(keys="visibility")
            if not node.visibility.get()
        ]
        if hidden_geometry:
            self.logger.error(
                "Hidden geometry with visibility keys set to False found:"
            )
            for geom in hidden_geometry:
                self.logger.error(f"\t{geom}")
            return False
        self.logger.debug("check_hidden_geometry_with_keys passed")
        return True

    def check_framerate(self, target_framerate: Optional[str]) -> bool:
        """Check if the scene's current framerate matches the target framerate."""
        if target_framerate:
            current_time_unit = pm.currentUnit(query=True, time=True)
            if current_time_unit != target_framerate:
                return False
            self.logger.debug(
                f"Framerate check passed: Scene framerate matches the target framerate ({target_framerate})."
            )
        return True


class SceneExporterTasks:
    def __init__(self, logger, objects, materials):
        self.logger = logger
        self.objects = objects
        self.materials = materials  # Store the filtered materials
        self.checks_manager = SceneExporterChecks(logger)

    def run_tasks(self, **tasks) -> bool:
        """Run tasks based on the provided task names in **tasks.
        Tasks are run in the order they are specified in the tasks.
        """
        check_kwargs = {}

        for task_name, value in tasks.items():
            task_method = getattr(self, task_name, None)

            if task_method:
                self.logger.debug(f"Running task: {task_name} with value: {value}")

                try:
                    # Try calling the task method without arguments
                    task_method()
                except TypeError:
                    # If TypeError occurs, call the method with the provided value
                    self.logger.debug(
                        f"Retrying task {task_name} with argument {value}."
                    )
                    task_method(value)

            else:
                check_kwargs[task_name] = value

        # After running tasks, pass remaining kwargs to run_checks using checks_manager
        if not self.checks_manager.run_checks(
            self.objects, self.materials, **check_kwargs
        ):
            self.logger.error("Checks failed during task execution.")
            return False

        return True

    def convert_to_relative_paths(self):
        """Convert absolute material paths to relative paths."""
        self.logger.debug("Converting absolute paths to relative")
        MatUtils.convert_to_relative_paths(self.materials)
        self.logger.debug("Path conversion completed.")

    def reassign_duplicate_materials(self):
        """Reassign duplicate materials in the scene."""
        self.logger.debug("Reassigning duplicate materials")
        MatUtils.reassign_duplicates(self.materials)
        self.logger.debug("Reassignment completed.")

    def delete_unused_materials(self):
        """Delete unused materials from the scene."""
        self.logger.debug("Deleting unused materials")
        pm.mel.hyperShadePanelMenuCommand("hyperShadePanel1", "deleteUnusedNodes")
        self.logger.debug("Unused materials deleted.")

    def set_temp_workspace(self):
        """Manage temporary workspace changes."""
        original_workspace = pm.workspace(query=True, rootDirectory=True)
        new_workspace = EnvUtils.find_workspace_using_path()
        if new_workspace and not new_workspace == original_workspace:
            pm.workspace(new_workspace, openWorkspace=True)
        self.logger.info(
            f"Setting workspace to: {pm.workspace(query=True, rootDirectory=True)}"
        )
        self.logger.debug(f"Reverting workspace to {original_workspace}")
        pm.workspace(original_workspace, openWorkspace=True)

    def set_temp_linear_unit(self, temp_linear_unit=None):
        """Manage temporary changes to scene units."""
        original_linear_unit = pm.currentUnit(query=True, linear=True)
        if temp_linear_unit:
            self.logger.info(
                f"Setting linear unit from {original_linear_unit} to {temp_linear_unit}"
            )
            pm.currentUnit(linear=temp_linear_unit)
        self.logger.debug(f"Reverting linear unit to {original_linear_unit}")
        pm.currentUnit(linear=original_linear_unit)

    def apply_preset(self, preset=None):
        """Apply a preset for export."""
        if preset:
            self.logger.debug(f"Applying preset: {preset}")
            self.load_fbx_export_preset(preset)
            self.verify_fbx_preset()
        self.logger.debug("apply_preset passed")

    def load_fbx_export_preset(self, preset_path: str):
        preset_path_escaped = preset_path.replace("\\", "/")
        self.logger.debug(f"Loading FBX export preset from {preset_path_escaped}")
        try:
            pm.mel.eval(f'FBXLoadExportPresetFile -f "{preset_path_escaped}"')
            self.logger.info(f"Loaded FBX export preset from {preset_path_escaped}.")
        except RuntimeError as e:
            self.logger.error(f"Failed to load FBX export preset: {e}")
            raise RuntimeError(f"Failed to load FBX export preset: {e}")

    def verify_fbx_preset(self):
        settings = [
            "FBXExportBakeComplexAnimation",
            "FBXExportBakeComplexStart",
            "FBXExportBakeComplexEnd",
            "FBXExportBakeComplexStep",
            "FBXExportSmoothingGroups",
            "FBXExportHardEdges",
            "FBXExportTangents",
            "FBXExportSmoothMesh",
            "FBXExportInstances",
            "FBXExportReferencedAssetsContent",
            "FBXExportAnimationOnly",
            "FBXExportSkins",
            "FBXExportShapes",
            "FBXExportConstraints",
            "FBXExportCameras",
            "FBXExportLights",
            "FBXExportEmbeddedTextures",
            "FBXExportInputConnections",
            "FBXExportTriangulate",
            "FBXExportUseSceneName",
            "FBXExportBakeResampleAnimation",
            "FBXExportFileVersion",
        ]
        results = {}
        try:
            for setting in settings:
                value = pm.mel.eval(f"{setting} -q")
                results[setting] = value
                self.logger.info(f"{setting} is set to: {value}")
        except RuntimeError as e:
            self.logger.info(f"Error querying FBX settings: {e}")
        return results


class SceneExporter(ptk.LoggingMixin):
    def initialize(
        self,
        objects: Optional[Union[List[str], Callable]] = None,
        export_dir: Optional[str] = None,
        preset: Optional[str] = None,
        output_name: Optional[str] = None,
        name_regex: Optional[str] = None,
        timestamp: Optional[bool] = None,
        log_level: str = "WARNING",
        create_log_file: Optional[bool] = None,
        hide_log_file: Optional[bool] = None,
        log_handler: Optional[object] = None,
        **kwargs: Dict[str, Any],
    ):
        self._export_dir = export_dir
        self._preset = preset
        self.output_name = output_name
        self.name_regex = name_regex
        self.timestamp = timestamp
        self.create_log_file = create_log_file
        self.hide_log_file = hide_log_file

        self.logger.setLevel(log_level)
        self.logger.propagate = True
        if log_handler:
            self.logger.addHandler(log_handler)

        self.export_path = self.generate_export_path()
        self.logger.info(f"Generated export path: {self.export_path}")

        if self.create_log_file:
            log_file_path = self.generate_log_file_path(self.export_path)
            self.logger.info(f"Generating log file path: {log_file_path}")
            self.setup_file_logging(log_file_path)

        # Initialize objects and materials before setting up the task manager
        self.objects = self.initialize_objects(objects)
        self.materials = MatUtils.filter_materials_by_objects(self.objects)

        self.logger.debug("Initializing tasks manager in SceneExporter.")
        self.tasks_manager = SceneExporterTasks(
            self.logger, self.objects, self.materials
        )
        if not self.tasks_manager.run_tasks(**kwargs):
            self.logger.error("Export aborted due to failed tasks or checks.")
            return

        self.logger.debug("SceneExporter initialized with new parameters.")

    def initialize_objects(self, objects):
        """Initialize objects for the scene."""
        if objects is None:
            self.logger.debug("No objects specified; defaulting to all scene objects.")
            objects = pm.ls(transforms=True)
        elif callable(objects):
            self.logger.debug("Initializing objects using a callable function.")
            objects = objects()
        else:
            self.logger.debug("Initializing objects using provided list or query.")

        initialized_objects = pm.ls(objects)
        self.logger.debug(f"Objects initialized: {initialized_objects}")
        return initialized_objects

    def export(self, **kwargs) -> None:
        """Perform the export operation after running tasks and checks."""
        self.initialize(**kwargs)
        self.logger.debug("Starting export process.")

        pm.select(self.tasks_manager.objects, replace=True)
        self.logger.debug(f"Selected objects for export: {self.tasks_manager.objects}")

        if not pm.selected():
            pm.warning("No geometry selected to export.")
            self.logger.warning("No geometry selected to export.")
            return

        try:
            file_format = kwargs.get("file_format", "FBX export")
            pm.exportSelected(self.export_path, type=file_format, force=True)
            self.logger.info(f"File exported: {self.export_path}")
        except Exception as e:
            self.logger.error(f"Failed to export geometry: {e}")
            raise RuntimeError(f"Failed to export geometry: {e}")
        finally:
            if self.create_log_file:
                self.close_file_handlers()

    def generate_export_path(self) -> str:
        """Generate the full export file path."""
        scene_path = pm.sceneName() or "untitled"
        scene_name = os.path.splitext(os.path.basename(scene_path))[0]
        export_name = self.output_name or scene_name
        if self.timestamp:
            export_name += f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        export_name = self.format_export_name(export_name)
        return os.path.join(self.export_dir, f"{export_name}.fbx")

    def format_export_name(self, name: str) -> str:
        """Format the export name using a regex if provided."""
        if self.name_regex:
            pattern, replacement = self.name_regex.split("->")
            return re.sub(pattern, replacement, name)
        return name

    def generate_log_file_path(self, export_path: str) -> str:
        """Generate the log file path based on the export path."""
        base_name = os.path.splitext(os.path.basename(export_path))[0]
        return os.path.join(self.export_dir, f"{base_name}.log")

    def setup_file_logging(self, log_file_path: str):
        """Setup file logging to log actions during export."""
        file_handler = self.logging.FileHandler(log_file_path)
        file_handler.setFormatter(
            self.logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.file_handler = file_handler
        root_logger = self.logging.getLogger(self.__class__.__name__)
        root_logger.addHandler(self.file_handler)
        self.logger.debug(f"File logging setup complete. Log file: {log_file_path}")

        if self.hide_log_file and os.name == "nt":
            ctypes.windll.kernel32.SetFileAttributesW(log_file_path, 2)

    def close_file_handlers(self):
        """Close and remove file handlers after logging is complete."""
        root_logger = self.logging.getLogger(self.__class__.__name__)
        handlers = root_logger.handlers[:]
        for handler in handlers:
            if isinstance(handler, self.logging.FileHandler):
                handler.close()
                root_logger.removeHandler(handler)
                self.logger.debug("File handler closed and removed.")

    @property
    def preset(self) -> str:
        """Get the preset path."""
        if self._preset:
            self.logger.debug(f"Accessing preset: {self._preset}")
            if os.path.isabs(self._preset):
                resolved_path = os.path.abspath(os.path.expandvars(self._preset))
                self.logger.debug(f"Resolved absolute preset path: {resolved_path}")
                return resolved_path
            else:
                resolved_path = os.path.abspath(
                    os.path.expandvars(
                        os.path.join(os.path.dirname(__file__), "presets", self._preset)
                    )
                )
                self.logger.debug(f"Resolved relative preset path: {resolved_path}")
                return resolved_path
        self.logger.warning("Preset is not set.")
        return None

    @preset.setter
    def preset(self, value: str) -> None:
        """Set the preset path."""
        self.logger.debug(f"Setting preset to: {value}")
        self._preset = value

    @property
    def export_dir(self) -> str:
        """Get the export directory."""
        if self._export_dir:
            return os.path.abspath(os.path.expandvars(self._export_dir))
        return None

    @export_dir.setter
    def export_dir(self, value: str) -> None:
        """Set the export directory."""
        self.logger.debug(f"Setting export directory to: {value}")
        self._export_dir = value


class SceneExporterSlots(SceneExporter):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PRESET_DIR = os.path.join(BASE_DIR, "presets")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sb = self.switchboard()
        self.ui = self.sb.scene_exporter

        self.ui.txt003.setText(
            "Any selected objects will be exported. If there is no selection, all visible objects in the scene will be exported."
        )
        self.logging.setup_logging_redirect(self.ui.txt003)

        self.ui.set_persistent_value("PRESET_DIR", owner=self, default=self.PRESET_DIR)

    @property
    def workspace(self) -> Optional[str]:
        workspace_path = CoreUtils.get_maya_info("workspace")
        if not workspace_path:
            self.logger.error("Workspace directory not found.")
        return workspace_path

    @property
    def presets(self) -> Dict[str, str]:
        return {
            os.path.splitext(f)[0]: f
            for f in os.listdir(self.PRESET_DIR)
            if f.endswith(".fbxexportpreset")
        }

    def cmb000_init(self, widget) -> None:
        widget.refresh = True
        if not widget.is_initialized:
            widget.menu.add(
                "QPushButton",
                setToolTip="Set the preset directory.",
                setText="Set Preset Directory",
                setObjectName="b005",
            )
            widget.menu.add(
                "QPushButton",
                setToolTip="Open the preset directory.",
                setText="Open Preset Directory",
                setObjectName="b007",
            )
            widget.menu.add(
                "QPushButton",
                setToolTip="Add an FBX export preset.",
                setText="Add New Preset",
                setObjectName="b003",
            )
            widget.menu.add(
                "QPushButton",
                setToolTip="Delete the current FBX export preset.",
                setText="Delete Current Preset",
                setObjectName="b004",
            )
        widget.add(self.presets, clear=True)

    def txt000_init(self, widget) -> None:
        widget.menu.add(
            "QPushButton",
            setToolTip="Set the output directory.",
            setText="Set Output Directory",
            setObjectName="b002",
        )
        widget.menu.add(
            "QPushButton",
            setToolTip="Open the output directory.",
            setText="Open Output Directory",
            setObjectName="b006",
        )

    def txt001_init(self, widget) -> None:
        widget.menu.add(
            "QCheckBox",
            setToolTip="Add a timestamp suffix to the output filename.",
            setText="Timestamp",
            setObjectName="chk004",
        )
        widget.menu.add(
            "QLineEdit",
            setToolTip="Regex pattern for formatting the output name.",
            setText=r"_module->",
            setPlaceholderText="RegEx",
            setObjectName="txt002",
        )

    def b000_init(self, widget) -> None:
        """Export Settings"""
        widget.menu.setTitle("Export Settings")
        widget.menu.mode = "popup"
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for duplicate materials.",
            setText="Check for duplicate materials.",
            setObjectName="chk001",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Reassign any duplicate materials to a single material.",
            setText="Reassign Duplicate Materials",
            setObjectName="chk009",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Delete unassigned material nodes.",
            setText="Delete Unused Materials",
            setObjectName="chk008",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for hidden geometry with visibility keys set to False.",
            setText="Check for Hidden Keyed Geometry",
            setObjectName="chk010",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for referenced objects.",
            setText="Check for referenced objects.",
            setObjectName="chk002",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for absolute paths.",
            setText="Check for absolute paths.",
            setObjectName="chk003",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Convert absolute paths to relative paths.",
            setText="Convert to Relative Paths",
            setObjectName="chk007",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Determine the workspace directory from the scene path.",
            setText="Auto Set Workspace",
            setObjectName="chk006",
        )
        widget.menu.add(
            self.sb.ComboBox,
            setToolTip="Temporary linear unit to be used during export.",
            setObjectName="cmb001",
        )
        items = ptk.insert_into_dict(EnvUtils.SCENE_UNIT_VALUES, "OFF", None)
        items = {f"Override Linear Unit: {key}": value for key, value in items.items()}
        widget.menu.cmb001.add(items)
        widget.menu.add(
            self.sb.ComboBox,
            setToolTip="Temporary time unit to be used during export.",
            setObjectName="cmb002",
        )
        inverted_values = {
            f"{value} fps": key for key, value in AnimUtils.FRAME_RATE_VALUES.items()
        }
        items = ptk.insert_into_dict(inverted_values, "OFF", None)
        items = {f"Check Time Unit: {key}": value for key, value in items.items()}
        widget.menu.cmb002.add(items)
        widget.menu.add(
            "QCheckBox",
            setToolTip="Export a log file along with the fbx.",
            setText="Create Log File",
            setObjectName="chk005",
        )
        widget.menu.add(
            self.sb.ComboBox,
            setToolTip="Set the log level.",
            setObjectName="cmb003",
        )
        items = {
            "Log Level: DEBUG": 10,  # DEBUG
            "Log Level: INFO": 20,  # INFO
            "Log Level: WARNING": 30,  # WARNING
            "Log Level: ERROR": 40,  # ERROR
        }
        widget.menu.cmb003.add(items)

    def b001(self) -> None:
        """Export"""
        self.ui.txt003.clear()
        self.logger.info("Starting export process ..")

        # Prepare task and check parameters
        task_params = {
            "set_temp_linear_unit": self.ui.cmb001.currentData(),
            "set_temp_workspace": self.ui.chk006.isChecked(),
            "convert_to_relative_paths": self.ui.chk007.isChecked(),
            "delete_unused_materials": self.ui.chk008.isChecked(),
        }

        check_params = {
            "check_framerate": self.ui.cmb002.currentData(),
            "check_absolute_paths": self.ui.chk003.isChecked(),
            "check_duplicate_materials": self.ui.chk001.isChecked(),
            "check_referenced_objects": self.ui.chk002.isChecked(),
            "check_hidden_geometry_with_keys": self.ui.chk010.isChecked(),
        }

        # Call export with parameters and tasks/checks
        self.export(
            objects=pm.selected() or DisplayUtils.get_visible_geometry,
            export_dir=self.ui.txt000.text(),
            preset=self.ui.cmb000.currentData(),
            output_name=self.ui.txt001.text(),
            name_regex=self.ui.txt002.text(),
            timestamp=self.ui.chk004.isChecked(),
            create_log_file=self.ui.chk005.isChecked(),
            log_level=self.ui.cmb003.currentData(),
            **task_params,  # Task-related parameters
            **check_params,  # Check-related parameters
        )

    def b002(self) -> None:
        """Set Output Directory"""
        output_dir = self.sb.dir_dialog(
            title="Select an output directory:", start_dir=self.workspace
        )
        if output_dir:
            self.ui.txt000.setText(output_dir)

    def b003(self) -> None:
        """Add Preset"""
        fbx_presets = self.sb.file_dialog(
            file_types="*.fbxexportpreset",
            title="Select an FBX export preset:",
            start_dir=self.workspace,
        )
        if fbx_presets:
            for preset in fbx_presets:
                shutil.copy(preset, self.PRESET_DIR)
            self.ui.cmb000.init_slot()
            filename_without_ext = os.path.splitext(os.path.basename(preset))[0]
            self.ui.cmb000.setCurrentText(filename_without_ext)

    def b004(self) -> None:
        """Remove Preset"""
        preset = self.ui.cmb000.currentData()
        if preset:
            preset_file = os.path.join(self.PRESET_DIR, preset)
            os.remove(preset_file)
            self.logger.info(f"Preset deleted: {preset_file}")
            self.ui.cmb000.init_slot()

    def b005(self) -> None:
        """Set Preset Directory"""
        preset_dir = self.sb.dir_dialog(
            title="Select a directory containing export presets:",
            start_dir=self.PRESET_DIR,
        )
        if preset_dir:
            self.PRESET_DIR = preset_dir
            self.ui.cmb000.init_slot()

    def b006(self) -> None:
        """Open Output Directory"""
        output_dir = self.ui.txt000.text()
        if os.path.exists(output_dir):
            os.startfile(output_dir)

    def b007(self) -> None:
        """Open Preset Directory"""
        preset_dir = self.PRESET_DIR
        if os.path.exists(preset_dir):
            os.startfile(preset_dir)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "scene_exporter.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=SceneExporterSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(minimize_button=True, hide_button=True)
    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------


# preset = "unity_animation"
# # export_dir = r"%userprofile%/Desktop/test"
# name_regex = r"_module->"

# exporter = SceneExporter(
#     objects=DisplayUtils.get_visible_geometry,
#     export_dir=export_dir,
#     preset=f"{preset}.fbxexportpreset",
#     temp_linear_unit="m",
#     temp_time_unit="ntsc",
#     check_duplicate_materials=True,
#     check_referenced_objects=True,
#     check_absolute_paths=True,
#     name_regex=name_regex,
#     log_level=logging.DEBUG,
# )

# exporter.export()

# batch = [
#     r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder\PRODUCTION\AF\F-15E\PRODUCTION\Maya\Fuel_Tanks_Build\scenes\modules\FT3A_TANK\FT3A_TANK_module.ma",
#     r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder\PRODUCTION\AF\F-15E\PRODUCTION\Maya\Fuel_Tanks_Build\scenes\modules\FT3A_BOOST_PUMP_CANISTER\FT3A_BOOST_PUMP_CANISTER_module.ma",
#     r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder\PRODUCTION\AF\F-15E\PRODUCTION\Maya\Fuel_Tanks_Build\scenes\modules\FT3A_BLADDER_UNROLLED\FT3A_BLADDER_UNROLLED_module.ma",
# ]
# exporter.export(batch=batch)
