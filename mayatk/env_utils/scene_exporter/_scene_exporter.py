# !/usr/bin/python
# coding=utf-8
import os
import re
import time
import ctypes
import shutil
import logging
from datetime import datetime
from typing import List, Dict, Optional, Callable, Union, Any

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.env_utils.scene_exporter.task_manager import TaskManager


class SceneExporter(ptk.LoggingMixin):
    def __init__(
        self, log_level: str = "WARNING", log_handler: Optional[object] = None
    ):
        """ """
        self._setup_logging(log_level, log_handler)

        self.task_manager = TaskManager(self.logger)
        self.logger.debug("Task manager initialized in SceneExporter.")

    def _setup_logging(self, log_level: str, log_handler: Optional[object]) -> None:
        """Setup logging configuration."""
        self.logger.setLevel(log_level)
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
        """Initialize objects for the scene, including all descendants that will be exported."""
        if objects is None:
            self.logger.debug(
                "No objects provided. Defaulting to all transforms in the scene."
            )
            objects = pm.selected()
        elif callable(objects):
            self.logger.debug(
                "Callable provided for objects. Resolving objects dynamically."
            )
            objects = objects()
        else:
            self.logger.debug("Static list or query provided for objects. Validating.")

        objs = pm.ls(objects, long=True, flatten=True)
        if hasattr(self, "task_manager"):
            self.task_manager.objects = objs

        self.logger.info(f"{len(objs)} object(s) prepared for export.")
        return objs

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
        start_time = time.time()  # Track export duration
        self.logger.info("Starting export process ...")

        # Set export configuration
        self.export_dir = os.path.abspath(os.path.expandvars(export_dir))

        # Validate export directory exists
        if not os.path.isdir(self.export_dir):
            self.logger.error(f"Export directory does not exist: {self.export_dir}")
            return False

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
        self.logger.debug(f"Generated export path: {self.export_path}")

        if self.create_log_file:
            self._setup_file_logging()

        # Initialize objects
        initialized_objs = self._initialize_objects(objects)
        if not initialized_objs:
            self.logger.error("Export aborted: No objects available for export.")
            return False

        # Apply preset before running tasks
        if self.preset_file:
            self.load_fbx_export_preset(self.preset_file, verify=True)

        # Run tasks and checks
        if tasks:
            tasks_successful = self.task_manager.run_tasks(tasks)
            if not tasks_successful:  # If any tasks failed, return them
                return False

        # Select objects to export
        if export_visible:
            pm.select(self.task_manager.objects, replace=True)
            self.logger.info(
                f"Selected {len(self.task_manager.objects)} objects for export."
            )

        if not pm.selected():
            self.logger.error("No objects to export.")
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

        # After successful export, gather detailed info
        export_info = {
            "output_file": os.path.basename(self.export_path),
            "export_duration": time.time() - start_time,
            "objects_exported": (
                len(objects) if hasattr(self, "objects") and self.objects else 0
            ),
        }

        # Add file size if export was successful
        if os.path.exists(self.export_path):
            file_size = os.path.getsize(self.export_path)
            if file_size > 1024 * 1024:  # > 1MB
                export_info["file_size"] = f"{file_size / (1024*1024):.2f} MB"
            else:
                export_info["file_size"] = f"{file_size / 1024:.2f} KB"

        # Add frame range if animation export
        if hasattr(self, "_animation_range") and self._animation_range:
            export_info["frame_range"] = self._animation_range

        # Add preset info
        if preset_file:
            preset_name = os.path.splitext(os.path.basename(preset_file))[0]
            export_info["preset_used"] = preset_name

        # Return True since tasks already ran successfully before export
        return True

    def generate_export_path(self) -> str:
        """Generate the full export file path."""
        scene_path = pm.sceneName() or "untitled"
        scene_name = os.path.splitext(os.path.basename(scene_path))[0]
        export_name = self.output_name or scene_name
        export_name = export_name.removesuffix(".fbx").removesuffix(".FBX")
        if self.timestamp:
            export_name += f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        export_name = self.format_export_name(export_name)
        return os.path.join(self.export_dir, f"{export_name}.fbx")

    def format_export_name(self, name: str) -> str:
        """Format the export name using a regex pattern and replacement (e.g. 'pattern->replace')."""
        if self.name_regex:
            # Try to find a delimiter
            for delim in ("->", "=>", "|"):
                if delim in self.name_regex:
                    pattern, replacement = self.name_regex.split(delim, 1)
                    break
            else:
                pattern, replacement = self.name_regex, ""
            # Strip whitespace and apply
            pattern = pattern.strip()
            replacement = replacement.strip()
            try:
                return re.sub(pattern, replacement, name)
            except re.error as e:
                self.logger.error(f"Invalid regex pattern: {pattern}. Error: {e}")
                return name
        return name

    def generate_log_file_path(self, export_path: str) -> str:
        """Generate the log file path based on the export path."""
        base_name = os.path.splitext(os.path.basename(export_path))[0]
        return os.path.join(self.export_dir, f"{base_name}.log")

    def setup_file_logging(self, log_file_path: str):
        """Setup file logging to log actions during export."""
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.file_handler = file_handler
        root_logger = logging.getLogger(self.__class__.__name__)
        root_logger.addHandler(self.file_handler)
        self.logger.debug(f"File logging setup complete. Log file: {log_file_path}")

        if self.hide_log_file and os.name == "nt":
            ctypes.windll.kernel32.SetFileAttributesW(log_file_path, 2)

    def close_file_handlers(self):
        """Close and remove file handlers after logging is complete."""
        root_logger = logging.getLogger(self.__class__.__name__)
        handlers = root_logger.handlers[:]
        for handler in handlers:
            if isinstance(handler, logging.FileHandler):
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

    _log_level_options: Dict[str, Any] = {
        "Log Level: DEBUG": 10,
        "Log Level: INFO": 20,
        "Log Level: WARNING": 30,
        "Log Level: ERROR": 40,
    }

    def __init__(self, switchboard, log_level="WARNING"):
        # Initialize the parent SceneExporter class first
        super().__init__(log_level=log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.scene_exporter

        self.ui.txt001.setText("")  # Output Name
        self.ui.txt003.setText("")  # Log Output

        # Initialize the export override button
        self.ui.b009.setEnabled(False)
        self.ui.b009.setChecked(False)
        self.ui.b009.setStyleSheet("QPushButton:checked {background-color: #FF9999;}")

        self.logger.setLevel(log_level)
        self.logger.hide_logger_name(False)  # Hide the logger name in output
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)

    @property
    def workspace(self) -> Optional[str]:
        workspace_path = EnvUtils.get_env_info("workspace")
        if not workspace_path:
            self.logger.error("Workspace directory not found.")
        return workspace_path

    @property
    def presets(self) -> Dict[str, Optional[str]]:
        """Return available presets, using cached values if the preset directory has not changed."""
        # Retrieve the preset directory using settings
        preset_dir = self.ui.settings.value("preset_dir")
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

    def header_init(self, widget):
        """Initialize the header widget."""
        # Add a button to open the hypershade editor.
        widget.menu.setTitle("Global Settings:")
        widget.menu.add(
            "QCheckBox",
            setText="Create Log File",
            setObjectName="b011",
            setChecked=False,
            setToolTip="Export a log file along with the fbx.",
        )
        widget.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb003",  # Renamed from cmb001 to avoid collision
            add=self._log_level_options,
            setCurrentIndex=1,  # Default to INFO
            setToolTip="Set the log level.",
        )

    def cmb000_init(self, widget) -> None:
        """Init Preset"""
        if not widget.is_initialized:
            widget.restore_state = True  # Enable state restore
            widget.refresh_on_show = True  # Call this method on show
            widget.option_box.menu.add(
                "QPushButton",
                setToolTip="Set the preset directory.",
                setText="Set Preset Directory",
                setObjectName="b005",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setToolTip="Open the preset directory.",
                setText="Open Preset Directory",
                setObjectName="b007",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setToolTip="Add an FBX export preset.",
                setText="Add New Preset",
                setObjectName="b003",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setToolTip="Delete the current FBX export preset.",
                setText="Delete Current Preset",
                setObjectName="b004",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setToolTip="Open the FBX export preset editor.",
                setText="Edit Preset",
                setObjectName="b008",
            )

        # Store current selection before refresh
        current_data = widget.currentData() if widget.count() > 0 else None
        current_text = widget.currentText() if widget.count() > 0 else ""

        # Refresh the preset data
        widget.add(self.presets, clear=True)

        # Restore previous selection if it still exists
        if current_data and current_data in self.presets.values():
            # Find the text key for the preset path
            for text, path in self.presets.items():
                if path == current_data:
                    widget.setCurrentText(text)
                    self.logger.debug(f"Restored preset selection: {text}")
                    break
        elif current_text and current_text in self.presets:
            widget.setCurrentText(current_text)
            self.logger.debug(f"Restored preset selection by text: {current_text}")

    def cmb004(self, index, widget) -> None:
        """Update the output directory based on the selected recent directory."""
        selected_dir = widget.items[index]
        if selected_dir and os.path.exists(selected_dir):
            self.ui.txt000.setText(selected_dir)
        else:
            self.logger.error(f"Selected directory does not exist: {selected_dir}")

    def txt000_init(self, widget) -> None:
        """Init Output Directory"""
        widget.option_box.menu.add(
            "QPushButton",
            setToolTip="Set the output directory.",
            setText="Set Output Directory",
            setObjectName="b010",
        )
        widget.option_box.menu.add(
            "QPushButton",
            setToolTip="Open the output directory.",
            setText="Open Output Directory",
            setObjectName="b006",
        )
        # Add the ComboBox for recent output directories
        widget.option_box.menu.add(
            self.sb.registered_widgets.ComboBox,
            setToolTip="Select from the last 10 output directories.",
            setObjectName="cmb004",
        )
        # Load previously saved output directories
        prev_output_dirs = self.get_recent_output_dirs()
        # Add directories to ComboBox with a unified method
        # Access via option_box.menu (not standalone menu)
        self.ui.txt000.option_box.menu.cmb004.add(
            prev_output_dirs, header="Recent Output Dirs:"
        )

    def txt001_init(self, widget) -> None:
        """Init Output Name"""
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox",
            setToolTip="Add a timestamp suffix to the output filename.",
            setText="Timestamp",
            setObjectName="chk004",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setToolTip=(
                "Regex pattern for formatting the output name.\n\n"
                "Format:  PATTERN->REPLACEMENT\n"
                "Examples:\n"
                "  _bar.*->       Remove '_bar' and everything after\n"
                "  (foo|bar)->baz    Replace 'foo' or 'bar' with 'baz'\n"
                "Use standard Python regular expressions. If no '->', everything matching PATTERN is removed."
            ),
            setPlaceholderText="RegEx",
            setObjectName="txt002",
        )

    def cmb001_init(self, widget) -> None:
        """Auto-generate Export Settings UI from task definitions using WidgetComboBox."""
        widget_items = []

        for task_name, params in self.task_manager.task_definitions.items():
            widget_type = params.pop("widget_type", "QCheckBox")
            object_name = self.sb.convert_to_legal_name(task_name)

            # Dynamically resolve the widget class
            widget_class = getattr(self.sb.QtWidgets, widget_type, None)
            if widget_class is None:
                widget_class = getattr(self.sb.registered_widgets, widget_type, None)
                if widget_class is None:
                    raise ValueError(f"Unknown widget type: {widget_type}")

            # Create the widget instance
            created_widget = widget_class()
            self.ui.set_attributes(created_widget, setObjectName=object_name, **params)

            # Add as (widget, label) tuple for the combo box
            widget_items.append((created_widget, task_name))

        # Add all widgets to the combo box with a header
        widget.add(widget_items, header="Tasks", clear=True)

    def cmb002_init(self, widget) -> None:
        """Auto-generate Check Settings UI from check definitions using WidgetComboBox."""
        widget_items = []

        for check_name, params in self.task_manager.check_definitions.items():
            widget_type = params.get("widget_type", "QCheckBox")
            object_name = self.sb.convert_to_legal_name(check_name)

            # Dynamically resolve the widget class
            widget_class = getattr(self.sb.QtWidgets, widget_type, None)
            if widget_class is None:
                widget_class = getattr(self.sb.registered_widgets, widget_type, None)
                if widget_class is None:
                    raise ValueError(f"Unknown widget type: {widget_type}")

            # Create the widget instance
            created_widget = widget_class()

            # Create a copy of params without widget_type for set_attributes
            params_copy = {k: v for k, v in params.items() if k != "widget_type"}
            self.ui.set_attributes(
                created_widget, setObjectName=object_name, **params_copy
            )

            # Add as (widget, label) tuple for the combo box
            widget_items.append((created_widget, check_name))

        # Add all widgets to the combo box with a header
        widget.add(widget_items, header="Validation Checks", clear=True)

    def b000(self) -> None:
        """Export."""
        self.ui.txt003.clear()
        task_params = {}
        check_params = {}

        # Collect task parameters
        for task_name, params in self.task_manager.task_definitions.items():
            widget_type = params.get("widget_type", "QCheckBox")
            object_name = params.get(
                "object_name", self.sb.convert_to_legal_name(task_name)
            )
            value_method = params.get("value_method")

            widget = getattr(self.ui, object_name, None)

            if not value_method:
                value_method = (
                    "isChecked" if widget_type == "QCheckBox" else "currentData"
                )

            if widget and hasattr(widget, value_method):
                value = getattr(widget, value_method)()
                task_params[task_name] = value

        # Collect check parameters
        for check_name, params in self.task_manager.check_definitions.items():
            widget_type = params.get("widget_type", "QCheckBox")
            object_name = params.get(
                "object_name", self.sb.convert_to_legal_name(check_name)
            )
            value_method = params.get("value_method")

            widget = getattr(self.ui, object_name, None)

            if not value_method:
                value_method = (
                    "isChecked" if widget_type == "QCheckBox" else "currentData"
                )

            if widget and hasattr(widget, value_method):
                value = getattr(widget, value_method)()
                check_params[check_name] = value

        override = self.ui.b009.isChecked()

        # Filter parameters based on override
        if override:  # Only run tasks, skip checks
            task_params = {k: v for k, v in task_params.items() if v}
            check_params = {}  # Skip all checks
        else:  # Run both tasks and checks, but only if checked
            task_params = {k: v for k, v in task_params.items() if v}
            check_params = {k: v for k, v in check_params.items() if v}

        # Combine for logging
        all_params = {**task_params, **check_params}
        self.logger.debug(f"Task parameters: {task_params}")
        self.logger.debug(f"Check parameters: {check_params}")

        export_mode = task_params.pop("export_visible_objects", "visible")

        def objects_to_export():
            if export_mode == "visible":
                return DisplayUtils.get_visible_geometry(
                    consider_templated_visible=False, inherit_parent_visibility=True
                )
            elif export_mode == "selected":
                return pm.selected()
            elif export_mode == "all":
                return pm.ls(transforms=True, geometry=True)
            else:
                # Default to visible if unknown mode
                return DisplayUtils.get_visible_geometry(
                    consider_templated_visible=False, inherit_parent_visibility=True
                )

        export_successful = self.perform_export(
            objects=objects_to_export,
            export_dir=self.ui.txt000.text(),
            preset_file=self.ui.cmb000.currentData(),
            export_visible=(
                export_mode != "selected"
            ),  # True unless export mode is "selected"
            output_name=self.ui.txt001.text(),
            name_regex=self.ui.txt002.text(),
            timestamp=self.ui.chk004.isChecked(),
            create_log_file=self.ui.b011.isChecked(),
            log_level=self.ui.cmb003.currentData(),  # Updated from cmb001 to cmb003
            tasks={**task_params, **check_params},  # Pass both to perform_export
        )

        self.ui.b009.setChecked(False)
        self.ui.b009.setEnabled(not export_successful)

        output_dir = self.ui.txt000.text()
        self.save_output_dir(output_dir)

    def b010(self) -> None:
        """Set Output Directory"""
        output_dir = self.sb.dir_dialog(
            title="Select an output directory:", start_dir=self.workspace
        )
        if output_dir:
            self.ui.txt000.setText(output_dir)

    def b003(self) -> None:
        """Add Preset."""
        preset_dir = self.ui.settings.value("preset_dir")
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
        preset_dir = self.ui.settings.value("preset_dir")
        if not preset_dir:
            self.logger.error("Preset directory not set. Please set it first.")
            return
        preset = self.ui.cmb000.currentData()
        if preset:
            preset_file = os.path.join(preset_dir, preset)
            if os.path.exists(preset_file):
                os.remove(preset_file)
                self.logger.success(f"Preset deleted: {preset_file}")
                self.ui.cmb000.init_slot()
            else:
                self.logger.error(f"Preset file does not exist: {preset_file}")

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
        preset_dir = self.ui.settings.value("preset_dir")
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

    def get_recent_output_dirs(self) -> List[str]:
        """Utility method to load recent output directories from QSettings"""
        prev_output_dirs = self.ui.settings.value("prev_output_dirs", [])
        # Filter out the root directory and return the last 10
        return [i for i in prev_output_dirs if not i == "/"][-10:]

    def save_output_dir(self, output_dir: str) -> None:
        """Save the output directory to QSettings, ensuring no duplicates and normalized paths."""
        if output_dir:
            output_dir = ptk.format_path(output_dir)
            prev_output_dirs = self.ui.settings.value("prev_output_dirs", [])
            normalized_prev_dirs = ptk.format_path(prev_output_dirs)
            # print(f"Saving output directory: {output_dir}")
            # print(f"Previous directories: {normalized_prev_dirs}")
            # Remove duplicates while preserving order
            unique_dirs = []
            seen = set()
            for d in normalized_prev_dirs:
                # print(f"Checking directory: {d}")
                if d not in seen:
                    # print(f"Adding unique directory: {d}")
                    seen.add(d)
                    unique_dirs.append(d)

            if output_dir in unique_dirs:
                unique_dirs.remove(output_dir)
            unique_dirs.append(output_dir)
            # print(f"Unique directories after adding: {unique_dirs}")
            unique_dirs = unique_dirs[-10:]
            self.ui.settings.setValue("prev_output_dirs", unique_dirs)

            # Optionally update the ComboBox (access via option_box.menu)
            self.ui.txt000.option_box.menu.cmb004.clear()
            self.ui.txt000.option_box.menu.cmb004.add(
                unique_dirs, header="Recent Output Dirs:"
            )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("scene_exporter", reload=True)
    ui.show(pos="screen", app_exec=True)

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
