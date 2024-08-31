# !/usr/bin/python
# coding=utf-8
import os
import re
import ctypes
import shutil
import contextlib
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


class SceneExporterChecksFactory:
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
                self.logger.debug(f"Running check: '{check_name}' with value: {value}")

                try:  # Try calling the check method without arguments
                    if not check_method():
                        self.logger.error(f"Check '{check_name}' failed.")
                        return False

                except TypeError:
                    if not check_method(value):
                        self.logger.error(
                            f"Check '{check_name}' failed with value: {value}"
                        )
                        return False
        return True


class SceneExporterChecks(SceneExporterChecksFactory):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

    def check_hidden_objects_with_keys(self) -> bool:
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
        self.logger.debug("check_hidden_objects_with_keys passed")
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

    def check_objects_below_floor(self, threshold: float = 0) -> bool:
        """Check if any object's bounding box is below the Y-axis by the given threshold."""
        objects_below_threshold = []
        for obj in self.objects:
            # Get the min Y value
            bbox_min = pm.xform(obj, query=True, boundingBox=True)[1]
            if bbox_min < threshold:
                objects_below_threshold.append((obj, bbox_min))

        if objects_below_threshold:
            self.logger.error(
                "Objects with bounding box below the floor threshold found:"
            )
            for obj, bbox_min in objects_below_threshold:
                self.logger.error(f"\tObject: {obj} - Min Y: {bbox_min}")
            self.logger.debug("check_objects_below_floor failed")
            return False

        self.logger.debug("check_objects_below_floor passed")
        return True

    def check_and_delete_visibility_keys(self) -> bool:
        """Check for objects with visibility keys, show them if hidden, and delete the visibility keys."""
        modified_objects = []
        visibility_keys_found = False

        for obj in AnimUtils.filter_objects_with_keys(keys="visibility"):
            visibility_keys_found = True
            # Check if the object is currently hidden
            if not obj.visibility.get():
                obj.visibility.set(True)  # Show the object
                modified_objects.append(obj)

            # Delete visibility keys
            pm.cutKey(obj, attribute="visibility")
            self.logger.info(f"Visibility keys deleted for object: {obj}")

        if modified_objects:
            self.logger.info("Hidden objects with visibility keys found and shown:")
            for obj in modified_objects:
                self.logger.info(f"\tObject: {obj} was hidden and is now shown")
            self.logger.debug(
                "check_and_delete_visibility_keys found hidden objects and made them visible."
            )
            return False

        if visibility_keys_found:
            self.logger.debug(
                "check_and_delete_visibility_keys passed - visibility keys deleted."
            )
            return True

        self.logger.debug(
            "check_and_delete_visibility_keys passed - no visibility keys found."
        )
        return True


class SceneExporterTasksFactory:
    def __init__(self, logger, objects, materials):
        self.logger = logger
        self.objects = objects
        self.materials = materials
        self.checks_manager = SceneExporterChecks(logger)

    @contextlib.contextmanager
    def manage_context(self, **tasks):
        original_states = {}

        # Discover and set temporary states, storing original values
        for task_name, value in tasks.items():
            set_method_name = f"set_temp_{task_name}"
            revert_method_name = f"revert_{task_name}"
            set_method = getattr(self, set_method_name, None)
            revert_method = getattr(self, revert_method_name, None)

            if set_method and revert_method:
                try:
                    original_states[task_name] = set_method(value)
                except Exception as e:
                    self.logger.error(
                        f"Failed to set {task_name} with value {value}: {e}"
                    )
                    raise
        try:
            yield
        finally:  # Revert to original states
            for task_name, original_value in original_states.items():
                revert_method_name = f"revert_{task_name}"
                revert_method = getattr(self, revert_method_name, None)
                if revert_method:
                    try:
                        revert_method(original_value)
                    except Exception as e:
                        self.logger.error(
                            f"Failed to revert {task_name} to original value {original_value}: {e}"
                        )
                        raise

    def run_tasks(self, **tasks) -> bool:
        try:
            # Filter out tasks explicitly set to False or not provided
            tasks_to_run = {
                task_name: value for task_name, value in tasks.items() if value
            }

            with self.manage_context(**tasks_to_run):
                for task_name, value in tasks_to_run.items():
                    task_method = getattr(self, task_name, None)
                    if task_method:
                        self.logger.debug(
                            f"Running task: {task_name} with value: {value}"
                        )
                        try:
                            # Run the task with its value if it expects arguments
                            task_method(value)
                        except TypeError:
                            # Run the task without arguments if it doesn't expect any
                            task_method()
        except Exception as e:
            self.logger.error(f"Error during task execution: {e}")
            return False

        # Run checks after tasks
        check_kwargs = {
            task_name: value
            for task_name, value in tasks.items()
            if not hasattr(self, task_name)
        }
        if not self.checks_manager.run_checks(
            self.objects, self.materials, **check_kwargs
        ):
            self.logger.error("Checks failed during task execution.")
            return False

        return True


class SceneExporterTasks(SceneExporterTasksFactory):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def set_temp_workspace(self):
        """Manage temporary workspace change."""
        original_workspace = pm.workspace(query=True, rootDirectory=True)
        new_workspace = EnvUtils.find_workspace_using_path()
        if new_workspace and not new_workspace == original_workspace:
            pm.workspace(new_workspace, openWorkspace=True)
        return original_workspace

    def revert_workspace(self, original_workspace):
        """Revert to the original workspace."""
        pm.workspace(original_workspace, openWorkspace=True)

    def set_temp_linear_unit(self, linear_unit):
        """Manage temporary linear unit change."""
        original_linear_unit = pm.currentUnit(query=True, linear=True)
        if linear_unit:
            pm.currentUnit(linear=linear_unit)
        return original_linear_unit

    def revert_linear_unit(self, original_linear_unit):
        """Revert to the original linear unit."""
        pm.currentUnit(linear=original_linear_unit)

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

    def set_bake_animation_range(self):
        """Set the animation export range to the first and last keyframes of the specified objects if baking is enabled."""
        # Check if baking animation is enabled
        if not pm.mel.eval("FBXExportBakeComplexAnimation -q"):
            self.logger.info(
                "Baking complex animation is disabled. Skipping frame range setting."
            )
            return

        # Gather all key times from the specified objects
        all_key_times = [
            time
            for obj in pm.ls(self.objects)
            for time in pm.keyframe(obj, query=True, timeChange=True) or []
        ]

        if not all_key_times:
            self.logger.warning(
                "No keyframes found in specified objects. Skipping frame range setting."
            )
            return

        # Determine the first and last keyframes
        first_key, last_key = min(all_key_times), max(all_key_times)

        # Set the start and end frames for baking complex animation
        pm.mel.eval(f"FBXExportBakeComplexStart -v {int(first_key)}")
        pm.mel.eval(f"FBXExportBakeComplexEnd -v {int(last_key)}")

        self.logger.info(
            f"Set animation range to start: {int(first_key)}, end: {int(last_key)}"
        )


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
    ) -> bool:
        self._export_dir = export_dir
        self.preset = preset  # Ensure the setter is called
        self.output_name = output_name
        self.name_regex = name_regex
        self.timestamp = timestamp
        self.create_log_file = create_log_file
        self.hide_log_file = hide_log_file

        self._setup_logging(log_level, log_handler)
        self.export_path = self.generate_export_path()
        self.logger.info(f"Generated export path: {self.export_path}")

        if self.create_log_file:
            self._setup_file_logging()

        self.objects = self._initialize_objects(objects)
        self.materials = MatUtils.filter_materials_by_objects(self.objects)

        self.logger.debug("Initializing tasks manager in SceneExporter.")
        self.tasks_manager = SceneExporterTasks(
            self.logger, self.objects, self.materials
        )

        if self.preset:  # Apply the preset before running tasks
            self.apply_preset(self.preset)

        if not self.tasks_manager.run_tasks(**kwargs):
            return False
        return True

    def _setup_logging(self, log_level: str, log_handler: Optional[object]) -> None:
        """Setup logging configuration."""
        self.logger.setLevel(log_level)
        self.logger.propagate = True
        if log_handler:
            self.logger.addHandler(log_handler)

    def _setup_file_logging(self) -> None:
        """Setup file logging."""
        log_file_path = self.generate_log_file_path(self.export_path)
        self.logger.info(f"Generating log file path: {log_file_path}")
        self.setup_file_logging(log_file_path)

    def _initialize_objects(
        self, objects: Optional[Union[List[str], Callable]]
    ) -> List:
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
        self.logger.info("Starting export process ..")

        if not self.initialize(**kwargs):
            self.logger.error("Aborting export.")
            return

        pm.select(self.tasks_manager.objects, replace=True)
        self.logger.debug(f"Selected objects for export: {self.tasks_manager.objects}")

        if not pm.selected():
            pm.warning("No objects to export.")
            self.logger.warning("No objects to export.")
            return

        try:
            file_format = kwargs.get("file_format", "FBX export")
            pm.exportSelected(self.export_path, type=file_format, force=True)
            self.logger.info(f"File exported: {self.export_path}")
        except Exception as e:
            self.logger.error(f"Failed to export objects: {e}")
            raise RuntimeError(f"Failed to export objects: {e}")
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
            widget.menu.add(
                "QPushButton",
                setToolTip="Open the FBX export preset editor.",
                setText="Edit Preset",
                setObjectName="b008",
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
        widget.menu.setTitle("EXPORT SETTINGS:")
        widget.menu.mode = "popup"
        widget.menu.add(
            "QCheckBox",
            setToolTip="Export all visible objects regardless of the current selection.\nIf unchecked, only the selected objects will be exported.",
            setText="Export All Visible Objects",
            setObjectName="chk012",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for duplicate materials.",
            setText="Check For Duplicate Materials.",
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
            setToolTip="Delete visibility keys from the exported objects.\nIf the object is hidden, it will be set to visible.",
            setText="Delete Visibility Keys",
            setObjectName="chk013",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for hidden geometry with visibility keys set to False.",
            setText="Check For Hidden Keyed Geometry",
            setObjectName="chk010",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for referenced objects.",
            setText="Check For Referenced Objects.",
            setObjectName="chk002",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for objects with a bounding box having a negative Y value.",
            setText="Check For Objects Below Floor.",
            setObjectName="chk011",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Check for absolute paths.",
            setText="Check For Absolute Paths.",
            setObjectName="chk003",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Convert absolute paths to relative paths.",
            setText="Convert To Relative Paths",
            setObjectName="chk007",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Determine the workspace directory from the scene path.",
            setText="Auto Set Workspace",
            setObjectName="chk006",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Set the animation export range to the first and last keyframes of the specified objects.\nThis will override the preset value, and is only applicable if baking is enabled.",
            setText="Auto Set Bake Animation Range",
            setObjectName="chk014",
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
        # Prepare task and check parameters
        task_params = {
            "set_temp_linear_unit": self.ui.cmb001.currentData(),
            "set_temp_workspace": self.ui.chk006.isChecked(),
            "set_bake_animation_range": self.ui.chk014.isChecked(),
            "convert_to_relative_paths": self.ui.chk007.isChecked(),
            "delete_unused_materials": self.ui.chk008.isChecked(),
        }
        check_params = {
            "check_framerate": self.ui.cmb002.currentData(),
            "check_absolute_paths": self.ui.chk003.isChecked(),
            "check_duplicate_materials": self.ui.chk001.isChecked(),
            "check_referenced_objects": self.ui.chk002.isChecked(),
            "check_and_delete_visibility_keys": self.ui.chk013.isChecked(),
            "check_hidden_objects_with_keys": self.ui.chk010.isChecked(),
            "check_objects_below_floor": self.ui.chk011.isChecked(),
        }

        objects_to_export = (
            DisplayUtils.get_visible_geometry
            if self.ui.chk012.isChecked()
            else pm.selected()
        )

        # Call export with parameters and tasks/checks
        self.export(
            objects=objects_to_export,
            export_dir=self.ui.txt000.text(),
            preset=self.ui.cmb000.currentData(),
            export_visible=self.ui.chk012.isChecked(),
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

    def b008(self) -> None:
        """Edit Preset"""
        pm.mel.FBXUICallBack(-1, "editExportPresetInNewWindow", "fbx")


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
