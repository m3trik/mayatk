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
from mayatk.node_utils import NodeUtils
from mayatk.anim_utils import AnimUtils
from mayatk.env_utils import EnvUtils
from mayatk.mat_utils import MatUtils
from mayatk.xform_utils import XformUtils
from mayatk.display_utils import DisplayUtils


class SceneExporterTasksFactory:
    """A factory class for managing and executing tasks in a scene export pipeline.

    This class provides mechanisms for running tasks that set temporary states, perform checks,
    or modify the environment during an export process. Tasks are run and, if applicable, reverted.

    Tasks are categorized into two types:
    1. `set_` prefixed tasks: These represent temporary state changes (e.g., setting workspace, units).
    2. `check_` prefixed tasks: These are validation checks that ensure certain conditions are met
       before the export proceeds. If a check method returns `False`, the export process will be aborted.
    - Checks with a `False` value and that do not take arguments will be skipped.
    """

    def __init__(self, logger):
        self.logger = logger

    @contextlib.contextmanager
    def _manage_context(self, tasks: Dict[str, Any]) -> Dict[str, Any]:
        """Manage task states by setting them once and reverting after, returning task results."""
        original_states = {}
        task_results = {}
        self.logger.info(f"Running {len(tasks)} tasks")

        for index, (task_name, value) in enumerate(tasks.items(), start=1):
            method = getattr(self, task_name, None)
            if method:
                self.logger.debug(f"Executing Task #{index}/{len(tasks)}: {task_name}")

                # Handle state changes and reversions
                revert_method_name = (
                    f"revert_{task_name[4:]}" if task_name.startswith("set_") else None
                )
                revert_method = (
                    getattr(self, revert_method_name, None)
                    if revert_method_name
                    else None
                )

                try:
                    # Execute the task and log results centrally
                    original_value = self._execute_task_method(method, task_name, value)
                    task_results[task_name] = original_value

                    # Store revert info if applicable
                    if revert_method:
                        original_states[revert_method_name] = original_value

                except Exception as e:
                    self.logger.error(f"Error during task {task_name}: {e}")
                    raise
            else:
                self.logger.warning(f"Task {task_name} not found. Skipping.")

        yield task_results

        # Revert any changes made during tasks
        for revert_method_name, original_value in reversed(original_states.items()):
            revert_method = getattr(self, revert_method_name, None)
            if revert_method:
                try:
                    revert_method(original_value)
                    self.logger.debug(f"Reverted {revert_method_name}")
                except Exception as e:
                    self.logger.error(f"Error reverting {revert_method_name}: {e}")

        self.logger.info("Task execution completed, states reverted.")

    def _execute_task_method(self, method, task_name: str, value: Any):
        """Execute the task method, handling logging centrally."""
        try:
            if value is not None:
                return method(value)
            return method()
        except TypeError:
            if task_name.startswith("check_") and value is False:
                return None
            return method()

    def run_tasks(self, tasks: Dict[str, Any]) -> bool:
        """Run tasks and checks, returning True if all pass, False if any checks fail."""
        if not tasks:
            self.logger.warning("No tasks provided to run.")
            return True

        all_checks_passed = True

        with self._manage_context(tasks) as task_results:
            for task_name, result in task_results.items():
                if task_name.startswith("check_"):
                    if result is False:
                        self.logger.error(f"Check {task_name} failed.")
                        all_checks_passed = False
                    else:
                        self.logger.info(f"Check {task_name} passed.")
                else:
                    self.logger.info(f"Task {task_name} completed.")

        # Final summary log
        if all_checks_passed:
            self.logger.info("All tasks and checks completed successfully.")
        else:
            self.logger.info("Some checks failed.")

        return all_checks_passed


class SceneExporterTasks(SceneExporterTasksFactory):
    def __init__(self, objects, logger):
        super().__init__(logger)

        self.objects = objects
        self.logger = logger

    def set_workspace(self):
        """Manage temporary workspace change."""
        original_workspace = pm.workspace(query=True, rootDirectory=True)
        new_workspace = EnvUtils.find_workspace_using_path()
        if new_workspace and new_workspace != original_workspace:
            pm.workspace(new_workspace, openWorkspace=True)
        return original_workspace

    def revert_workspace(self, original_workspace):
        """Revert to the original workspace."""
        pm.workspace(original_workspace, openWorkspace=True)

    def set_linear_unit(self, linear_unit):
        """Manage temporary linear unit change."""
        original_linear_unit = pm.currentUnit(query=True, linear=True)
        if linear_unit:
            pm.currentUnit(linear=linear_unit)
        # Ensure that the original value is returned
        self.logger.debug(f"Original linear unit: {original_linear_unit}")
        return original_linear_unit

    def revert_linear_unit(self, original_linear_unit):
        """Revert to the original linear unit."""
        pm.currentUnit(linear=original_linear_unit)

    def convert_to_relative_paths(self):
        """Convert absolute material paths to relative paths."""
        self.logger.debug("Converting absolute paths to relative")
        materials = MatUtils.filter_materials_by_objects(self.objects)
        MatUtils.convert_to_relative_paths(materials)
        self.logger.debug("Path conversion completed.")

    def reassign_duplicate_materials(self):
        """Reassign duplicate materials in the scene."""
        self.logger.debug("Reassigning duplicate materials")
        materials = MatUtils.filter_materials_by_objects(self.objects)
        MatUtils.reassign_duplicate_materials(materials)
        self.logger.debug("Reassignment completed.")

    def delete_unused_materials(self):
        """Delete unused materials from the scene."""
        self.logger.debug("Deleting unused materials")
        pm.mel.hyperShadePanelMenuCommand("hyperShadePanel1", "deleteUnusedNodes")
        self.logger.debug("Unused materials deleted.")

    def delete_environment_nodes(self) -> None:
        """Delete all environment file nodes from the scene."""
        env_keywords = ["diffuse_cube", "specular_cube", "ibl_brdf_lut"]
        file_nodes = pm.ls(type="file")

        env_file_nodes = [
            node
            for node in file_nodes
            if node.hasAttr("fileTextureName")
            and any(
                keyword in node.fileTextureName.get().lower()
                for keyword in env_keywords
            )
        ]

        if env_file_nodes:
            pm.delete(env_file_nodes)
            self.logger.info(
                f"Deleted {len(env_file_nodes)} environment texture nodes."
            )
        else:
            self.logger.info("No environment file nodes found.")

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

    def check_root_default_transforms(self) -> bool:
        """Check if all root group nodes have default transforms (translate, rotate, and scale)."""
        root_nodes = pm.ls(self.objects, assemblies=True)
        has_non_default_transforms = False
        tolerance = 1e-5  # Small tolerance for floating-point comparisons

        for node in root_nodes:
            # Check if translate, rotate, and scale attributes are at their default values
            translate = node.translate.get()
            rotate = node.rotate.get()
            scale = node.scale.get()

            # Assuming the expected defaults: translate and rotate should be (0, 0, 0), scale should be (1, 1, 1)
            if (
                not all(abs(val) < tolerance for val in translate)
                or not all(abs(val) < tolerance for val in rotate)
                or not all(abs(val - 1) < tolerance for val in scale)
            ):

                if not has_non_default_transforms:
                    self.logger.error("Non-default root group nodes found:")
                    has_non_default_transforms = True

                self.logger.error(
                    f"\tNode: {node}, Translate: {translate}, Rotate: {rotate}, Scale: {scale}"
                )

        if has_non_default_transforms:
            return False

        self.logger.debug("check_root_default_transforms passed")
        return True

    def check_absolute_paths(self) -> bool:
        """Check if any absolute material paths are present in the scene."""
        all_relative = True
        materials = MatUtils.filter_materials_by_objects(self.objects)
        material_paths = MatUtils.collect_material_paths(
            materials,
            include_material=True,
            include_path_type=True,
            nested_as_unit=True,
        )
        for mat, typ, pth in material_paths:
            if typ == "Absolute":
                if all_relative:
                    all_relative = False
                    self.logger.error("Absolute path(s) found:")
                self.logger.error(f"\t{typ} path - {mat.name()} - {pth}")
        if not all_relative:
            return False
        return True

    def check_duplicate_materials(self) -> bool:
        """Check if any duplicate materials are present in the scene."""
        materials = MatUtils.filter_materials_by_objects(self.objects)
        duplicate_mapping = MatUtils.find_materials_with_duplicate_textures(materials)
        if duplicate_mapping:
            for original, duplicates in duplicate_mapping.items():
                for duplicate in duplicates:
                    self.logger.error(f"\tDuplicate: {duplicate} -> {original}")
            return False
        return True

    def check_referenced_objects(self) -> bool:
        """Check if any referenced objects are present in the scene."""
        referenced_objects = pm.ls(self.objects, references=True)
        if referenced_objects:
            for ref in referenced_objects:
                self.logger.error(f"\t{ref}")
            return False
        return True

    def check_hidden_objects_with_keys(self) -> bool:
        """Check if any hidden objects with visibility keys set to False are present in the scene."""
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

    def check_objects_below_floor(self) -> bool:
        """Check if any object's geometry is below the floor plane (Y=0)."""
        # Use the general method to check objects against this plane with boolean return type
        objects_below_threshold = XformUtils.check_objects_against_plane(
            self.objects,
            plane_point=(0, 0, 0),
            plane_normal=(0, 1, 0),
            return_type="bool",
        )
        # Log results
        has_objects_below = False
        for obj, is_below in objects_below_threshold:
            if is_below:
                if not has_objects_below:
                    self.logger.error(
                        "Objects with geometry below the floor threshold found:"
                    )
                    has_objects_below = True
                self.logger.error(f"\tObject: {obj} - Below Floor: {is_below}")

        if has_objects_below:
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

    def check_untied_keyframes(self) -> bool:
        """Check if there are any untied keyframes on the specified objects."""
        untied_keyframes_found = False

        for obj in self.objects:
            keyed_attrs = pm.keyframe(obj, query=True, name=True)
            if keyed_attrs:
                all_keyframes = pm.keyframe(obj, query=True, timeChange=True)
                if not all_keyframes:
                    continue

                start_frame, end_frame = min(all_keyframes), max(all_keyframes)

                for attr in keyed_attrs:
                    # Check if keyframes are tied at start and end
                    start_key = pm.keyframe(
                        attr, time=(start_frame,), query=True, timeChange=True
                    )
                    end_key = pm.keyframe(
                        attr, time=(end_frame,), query=True, timeChange=True
                    )

                    if not start_key or not end_key:
                        if not untied_keyframes_found:
                            untied_keyframes_found = True
                            self.logger.error("Untied keyframes found on attributes:")
                        self.logger.error(f"\t{attr} on {obj}")

        if not untied_keyframes_found:
            self.logger.debug("All keyframes are tied.")
        return not untied_keyframes_found

    def tie_all_keyframes(self):
        """Use AnimUtils to tie all keyframes for the specified objects."""
        self.logger.info("Tying keyframes for all objects.")
        AnimUtils.tie_keyframes(self.objects, absolute=True)
        self.logger.info("Keyframes have been tied.")


class SceneExporter(ptk.LoggingMixin):
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

        # Log only the total count of initialized objects
        self.logger.info(f"Initialized {len(initialized_objects)} objects for export.")

        return initialized_objects

    def perform_export(
        self,
        export_dir: str,
        objects: Optional[Union[List[str], Callable]] = None,
        preset_file: Optional[str] = None,
        output_name: Optional[str] = None,
        export_visible: bool = True,
        file_format: Optional[str] = "FBX export",
        create_log_file: bool = False,
        timestamp: bool = False,
        name_regex: Optional[str] = None,
        log_level: str = "WARNING",
        hide_log_file: Optional[bool] = None,
        log_handler: Optional[object] = None,
        tasks: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, bool]]:
        """Perform the export operation, including initialization and task management."""
        self.logger.info("Starting export process ...")

        # Set export configuration
        self.export_dir = os.path.abspath(os.path.expandvars(export_dir))
        self.preset_file = preset_file  # Ensure the setter is called
        self.output_name = output_name
        self.name_regex = name_regex
        self.timestamp = timestamp
        self.create_log_file = create_log_file
        self.hide_log_file = hide_log_file

        # Setup logging
        self._setup_logging(log_level, log_handler)

        # Generate the export path
        self.export_path = self.generate_export_path()
        self.logger.info(f"Generated export path: {self.export_path}")

        if self.create_log_file:
            self._setup_file_logging()

        # Initialize objects
        self.objects = self._initialize_objects(objects)

        # Log task manager initialization
        self.logger.debug("Initializing tasks manager in SceneExporter.")
        self.tasks_manager = SceneExporterTasks(self.objects, self.logger)

        # Apply preset before running tasks
        if self.preset_file:
            self.load_fbx_export_preset(self.preset_file, verify=True)

        # Run tasks and checks
        if tasks:
            tasks_successful = self.tasks_manager.run_tasks(tasks)
            if not tasks_successful:  # If any tasks failed, return them
                self.logger.error("Aborting export due to task or check failure.")
                return False

        # Select objects to export
        if export_visible:
            pm.select(self.tasks_manager.objects, replace=True)
            self.logger.info(
                f"Selected {len(self.tasks_manager.objects)} objects for export."
            )

        if not pm.selected():
            pm.warning("No objects to export.")
            self.logger.warning("No objects to export.")
            return False

        # Perform the actual export
        try:
            pm.exportSelected(self.export_path, type=file_format, force=True)
            self.logger.info(f"File exported: {self.export_path}")
        except Exception as e:
            self.logger.error(f"Failed to export objects: {e}")
            raise RuntimeError(f"Failed to export objects: {e}")
        finally:
            if self.create_log_file:
                self.close_file_handlers()

        return True  # Indicate that all tasks passed and export completed

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

    def load_fbx_export_preset(
        self, preset_file: str = None, verify: bool = False
    ) -> Optional[dict]:
        """Load an FBX export preset and optionally verify it.

        Parameters:
            preset_file (str, optional): The path to the preset file to be loaded.
            verify (bool, optional): If True, verifies the loaded FBX preset. Defaults to False.

        Returns:
            Optional[dict]: A dictionary of FBX settings and their current values if verification is performed, otherwise None.
        """
        if preset_file:
            self.logger.debug(f"Loading FBX export preset: {preset_file}")
            preset_path_escaped = preset_file.replace("\\", "/")

            try:
                pm.mel.eval(f'FBXLoadExportPresetFile -f "{preset_path_escaped}"')
                self.logger.info(
                    f"Loaded FBX export preset from {preset_path_escaped}."
                )
            except RuntimeError as e:
                self.logger.error(f"Failed to load FBX export preset: {e}")
                raise RuntimeError(f"Failed to load FBX export preset: {e}")

        # If verify is True, call the verify_fbx_preset method
        if verify:
            return self.verify_fbx_preset()

        return None

    def verify_fbx_preset(self) -> dict:
        """Verify a set of predefined FBX export settings and log their values.

        Returns:
            dict: A dictionary of FBX export settings and their current values.
        """
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

        for setting in settings:
            try:
                value = pm.mel.eval(f"{setting} -q")
                results[setting] = value
                self.logger.info(f"{setting} is set to: {value}")
            except RuntimeError as e:
                self.logger.error(f"Error querying {setting}: {e}")

        return results


class SceneExporterSlots(SceneExporter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sb = self.switchboard()
        self.ui = self.sb.scene_exporter

        self.logging.setup_logging_redirect(self.ui.txt003)

        self.ui.txt001.setText("")  # Output Name
        self.ui.txt003.setText("")  # Log Output

        # Initialize the export override button
        self.ui.b009.setEnabled(False)
        self.ui.b009.setChecked(False)
        self.ui.b009.setStyleSheet("QPushButton:checked {background-color: #FF9999;}")

    @property
    def workspace(self) -> Optional[str]:
        workspace_path = EnvUtils.get_maya_info("workspace")
        if not workspace_path:
            self.logger.error("Workspace directory not found.")
        return workspace_path

    @property
    def presets(self) -> Dict[str, Optional[str]]:
        """Return available presets, using cached values if the preset directory has not changed."""
        # Retrieve the preset directory using restore_settings
        preset_dir = self.ui.restore_settings("preset_dir")
        last_checked_dir = getattr(self, "_preset_dir_last_checked", None)

        # Only refresh the cached presets if the preset directory changes
        if preset_dir != last_checked_dir:
            self.logger.debug(f"Preset directory: {preset_dir}")
            setattr(self, "_preset_dir_last_checked", preset_dir)

            if not preset_dir or not os.path.exists(preset_dir):
                self.logger.error(
                    f"Preset directory not set or does not exist: {preset_dir}"
                )
            else:
                try:
                    presets = {
                        "None": None,
                        **{
                            os.path.splitext(f)[0]: os.path.join(preset_dir, f)
                            for f in os.listdir(preset_dir)
                            if f.endswith(".fbxexportpreset")
                        },
                    }
                    setattr(self, "_cached_presets", presets)
                except Exception as e:
                    self.logger.error(f"Error accessing preset directory: {e}")
                    setattr(self, "_cached_presets", {"None": None})

        # Return the cached presets
        return getattr(self, "_cached_presets", {"None": None})

    def cmb000_init(self, widget) -> None:
        """Init Preset"""
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
        """Init Output Directory"""
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
        # Add the ComboBox for recent output directories
        widget.menu.add(
            self.sb.ComboBox,
            setToolTip="Select from the last 10 output directories.",
            setObjectName="cmb004",
        )
        # Load previously saved output directories
        prev_output_dirs = self.get_recent_output_dirs()
        # Add directories to ComboBox with a unified method
        self.ui.txt000.menu.cmb004.add(prev_output_dirs, header="Recent Output Dirs:")

    def txt001_init(self, widget) -> None:
        """Init Output Name"""
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
        """Init Export Settings"""
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
            setToolTip="Delete environment file nodes.\nEnvironment nodes are defined as: 'diffuse_cube', 'specular_cube', 'ibl_brdf_lut'",
            setText="Delete Environment Nodes",
            setObjectName="chk015",
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
            setToolTip="Check for default transforms on root group nodes.\nTranslate, rotate, and scale should be (0, 0, 0) and (1, 1, 1) respectively.",
            setText="Check Root Default Transforms",
            setObjectName="chk016",
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
            setToolTip="Check for untied keyframes on the specified objects.",
            setText="Check For Untied Keyframes",
            setObjectName="chk017",
        )
        widget.menu.add(
            "QCheckBox",
            setToolTip="Tie all keyframes on the specified objects.",
            setText="Tie All Keyframes",
            setObjectName="chk018",
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
            "set_workspace": self.ui.chk006.isChecked(),
            "set_linear_unit": self.ui.cmb001.currentData(),
            "check_framerate": self.ui.cmb002.currentData(),
            "set_bake_animation_range": self.ui.chk014.isChecked(),
            "delete_environment_nodes": self.ui.chk015.isChecked(),
            "delete_unused_materials": self.ui.chk008.isChecked(),
            "convert_to_relative_paths": self.ui.chk007.isChecked(),
            "check_absolute_paths": self.ui.chk003.isChecked(),
            "reassign_duplicate_materials": self.ui.chk009.isChecked(),
            "check_duplicate_materials": self.ui.chk001.isChecked(),
            "check_referenced_objects": self.ui.chk002.isChecked(),
            "check_and_delete_visibility_keys": self.ui.chk013.isChecked(),
            "check_hidden_objects_with_keys": self.ui.chk010.isChecked(),
            "tie_all_keyframes": self.ui.chk018.isChecked(),
            "check_untied_keyframes": self.ui.chk017.isChecked(),
            "check_root_default_transforms": self.ui.chk016.isChecked(),
            "check_objects_below_floor": self.ui.chk011.isChecked(),
        }

        if self.ui.b009.isChecked():  # Override checks
            task_params = {
                k: v for k, v in task_params.items() if not k.startswith("check_")
            }
        self.logger.debug(f"Task parameters: {task_params}")

        objects_to_export = lambda: (
            DisplayUtils.get_visible_geometry(
                consider_templated_visible=False, inherit_parent_visibility=True
            )
            if self.ui.chk012.isChecked()
            else pm.selected()
        )

        # Call export with parameters and tasks/checks
        export_successful = self.perform_export(
            objects=objects_to_export,
            export_dir=self.ui.txt000.text(),
            preset_file=self.ui.cmb000.currentData(),
            export_visible=self.ui.chk012.isChecked(),
            output_name=self.ui.txt001.text(),
            name_regex=self.ui.txt002.text(),
            timestamp=self.ui.chk004.isChecked(),
            create_log_file=self.ui.chk005.isChecked(),
            log_level=self.ui.cmb003.currentData(),
            tasks=task_params,  # Task-related parameters
        )
        self.ui.b009.setChecked(False)
        self.ui.b009.setEnabled(not export_successful)

        # Get the current output directory from the UI
        output_dir = self.ui.txt000.text()
        # Save the output directory
        self.save_output_dir(output_dir)

    def b002(self) -> None:
        """Set Output Directory"""
        output_dir = self.sb.dir_dialog(
            title="Select an output directory:", start_dir=self.workspace
        )
        if output_dir:
            self.ui.txt000.setText(output_dir)

    def b003(self) -> None:
        """Add Preset."""
        preset_dir = self.ui.restore_settings("preset_dir")
        if not preset_dir:
            self.logger.error("Preset directory not set. Please set it first.")
            return
        fbx_presets = self.sb.file_dialog(
            file_types="*.fbxexportpreset",
            title="Select an FBX export preset:",
            start_dir=self.workspace,
        )
        if fbx_presets:
            for preset in fbx_presets:
                shutil.copy(preset, preset_dir)
            self.ui.cmb000.init_slot()
            filename_without_ext = os.path.splitext(os.path.basename(preset))[0]
            self.ui.cmb000.setCurrentText(filename_without_ext)

    def b004(self) -> None:
        """Remove Preset."""
        preset_dir = self.ui.restore_settings("preset_dir")
        if not preset_dir:
            self.logger.error("Preset directory not set. Please set it first.")
            return
        preset = self.ui.cmb000.currentData()
        if preset:
            preset_file = os.path.join(preset_dir, preset)
            if os.path.exists(preset_file):
                os.remove(preset_file)
                self.logger.info(f"Preset deleted: {preset_file}")
                self.ui.cmb000.init_slot()
            else:
                self.logger.warning(f"Preset file does not exist: {preset_file}")

    def b005(self) -> None:
        """Set Preset Directory."""
        preset_dir = self.sb.dir_dialog(
            title="Select a directory containing export presets:"
        )
        if preset_dir:
            self.ui.store_settings("preset_dir", preset_dir)
            self.ui.cmb000.init_slot()
            self.logger.info(f"Preset directory set to: {preset_dir}")

    def b006(self) -> None:
        """Open Output Directory"""
        output_dir = self.ui.txt000.text()
        if os.path.exists(output_dir):
            os.startfile(output_dir)

    def b007(self) -> None:
        """Open Preset Directory."""
        preset_dir = self.ui.restore_settings("preset_dir")
        if preset_dir and os.path.exists(preset_dir):
            os.startfile(preset_dir)
        else:
            self.logger.error(
                "Preset directory is not set or does not exist. Please set it first."
            )

    def b008(self) -> None:
        """Edit Preset"""
        # Load the preset.
        self.load_fbx_export_preset(self.ui.cmb000.currentData())

        # Reset the layout to ensure it updates.
        pm.mel.refresh()
        pm.mel.FBXUICallBack(-1, "updateUIWithProperties")

        if not pm.window("gameExporterWindow", exists=True):
            try:
                pm.mel.FBXUICallBack(-1, "editExportPresetInNewWindow", "fbx")
            except Exception as e:
                self.logger.error(f"Failed to open the FBX export preset editor: {e}")

    def cmb004(self, index, widget) -> None:
        """Update the output directory based on the selected recent directory."""
        selected_dir = widget.items[index]
        if selected_dir and os.path.exists(selected_dir):
            self.ui.txt000.setText(selected_dir)
        else:
            self.logger.warning(f"Selected directory does not exist: {selected_dir}")

    def get_recent_output_dirs(self) -> List[str]:
        """Utility method to load recent output directories from QSettings"""
        prev_output_dirs = self.ui.settings.value("prev_output_dirs", [])
        # Filter out the root directory and return the last 10
        return [i for i in prev_output_dirs if not i == "/"][-10:]

    def save_output_dir(self, output_dir: str) -> None:
        """Utility method to save the output directory to QSettings"""
        if output_dir:
            prev_output_dirs = self.ui.settings.value("prev_output_dirs", [])

            # Add new directory if it's not already in the list
            if output_dir not in prev_output_dirs:
                prev_output_dirs.append(output_dir)

            # Keep only the last 10 directories
            prev_output_dirs = prev_output_dirs[-10:]

            # Save the updated list
            self.ui.settings.setValue("prev_output_dirs", prev_output_dirs)
            # Optionally update the ComboBox
            self.ui.txt000.menu.cmb004.clear()
            self.ui.txt000.menu.cmb004.add(
                prev_output_dirs, header="Recent Output Dirs:"
            )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "scene_exporter.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=SceneExporterSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configure_buttons(minimize_button=True, hide_button=True)
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
