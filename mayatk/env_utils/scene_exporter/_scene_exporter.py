import os
import re
import shutil
import logging
from datetime import datetime
from functools import wraps
from typing import List, Optional, Callable

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


class EnvironmentTests:
    def __init__(
        self,
        objects: Optional[Callable[[], List[str]]] = None,
        log_level=logging.INFO,
        log_handler: Optional[logging.Handler] = None,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)
        if log_handler:
            self.logger.addHandler(log_handler)
        self.logger.propagate = True
        self.object_init_func = objects
        self.initialize_objects()
        self.materials = MatUtils.filter_materials_by_objects(self.objects)
        ignore = [
            "diffuse_cube.dds",
            "specular_cube.dds",
            "ibl_brdf_lut.dds",
            "ibl_brdf_lut.png",
        ]
        self.material_paths = MatUtils.collect_material_paths(
            self.materials, ignore=ignore
        )

    def initialize_objects(self):
        if self.object_init_func:
            try:
                self.objects = self.object_init_func()
            except TypeError:
                self.objects = pm.ls(self.object_init_func)
        else:
            self.objects = []

    def verify_fbx_preset(self):
        # List of all FBX export settings to verify
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

    def all_material_paths_relative(self) -> bool:
        all_relative = all(
            path_type == "Relative" for material, path_type, path in self.material_paths
        )
        return all_relative

    def check_all_paths_relative(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.check_paths_relative:
                all_paths_relative = self.all_material_paths_relative()
                if not all_paths_relative:
                    self.logger.error("Absolute path(s) found:")
                    for m, s, p in self.material_paths:
                        if s == "Absolute":
                            self.logger.error(f"\t{s, m, p}")
                    return
            return func(self, *args, **kwargs)

        return wrapper

    def check_for_duplicate_materials(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.check_duplicate_materials:
                materials = self.materials
                duplicate_materials = MatUtils.find_duplicate_materials(materials)
                if duplicate_materials:
                    self.logger.error("Duplicate material(s) found:")
                    for i in duplicate_materials:
                        self.logger.error(f"\t{i}")
                    return
            return func(self, *args, **kwargs)

        return wrapper

    def check_for_referenced_objects(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.check_referenced_objects:
                referenced_objects = pm.ls(self.objects, references=True)
                if referenced_objects:
                    self.logger.error("Referenced object(s) found:")
                    for ref in referenced_objects:
                        self.logger.error(f"\t{ref}")
                    return
            return func(self, *args, **kwargs)

        return wrapper


class SceneExporter(EnvironmentTests):
    def __init__(
        self,
        objects: Optional[List[str]] = None,
        export_dir: str = None,
        preset: str = None,
        output_name: str = None,
        name_regex: str = None,
        timestamp: bool = False,
        temp_linear_unit: str = None,
        temp_time_unit: str = None,
        check_paths_relative: bool = False,
        check_duplicate_materials: bool = False,
        check_referenced_objects: bool = False,
        log_level=logging.INFO,
        log_handler: Optional[logging.Handler] = None,
    ):
        """
        Parameters:
            objects (Optional[List[str]]): List of objects to export. Defaults to None.
            export_dir (str): Directory where exports will be saved. Defaults to None.
            preset (str): Path to the export preset file. Defaults to None.
            output_name (str): Base name for the output files. Defaults to None.
            name_regex (str): Regex pattern for formatting the output name. Defaults to None.
            timestamp (bool): If True, append a timestamp to the output name. Defaults to False.
            temp_linear_unit (str): Temporary linear unit for the scene during export. Defaults to None.
            temp_time_unit (str): Temporary time unit for the scene during export. Defaults to None.
            check_paths_relative (bool): If True, check if all paths are relative. Defaults to False.
            check_duplicate_materials (bool): If True, check for duplicate materials. Defaults to False.
            check_referenced_objects (bool): If True, check for referenced objects. Defaults to False.
            log_level (int): Logging level. Defaults to logging.INFO.
        """
        super().__init__(objects=objects, log_level=log_level, log_handler=log_handler)
        self.logger.setLevel(log_level)
        if log_handler:
            self.logger.addHandler(log_handler)
        self.logger.debug("Initializing SceneExporter with provided parameters.")
        self.temp_linear_unit = temp_linear_unit
        self.temp_time_unit = temp_time_unit
        self._preset = preset
        self.output_name = output_name
        self.name_regex = name_regex
        self.timestamp = timestamp
        self._export_dir = export_dir
        self.check_paths_relative = check_paths_relative
        self.check_duplicate_materials = check_duplicate_materials
        self.check_referenced_objects = check_referenced_objects
        self.logger.debug("SceneExporter initialization complete.")

    def setup_file_logging(self, log_file_path: str):
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)
        self.logger.debug(f"File logging setup complete. Log file: {log_file_path}")

    @property
    def preset(self) -> str:
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
        self.logger.debug(f"Setting preset to: {value}")
        self._preset = value

    @property
    def export_dir(self) -> str:
        if self._export_dir:
            return os.path.abspath(os.path.expandvars(self._export_dir))
        return None

    @export_dir.setter
    def export_dir(self, value: str) -> None:
        self.logger.debug(f"Setting export directory to: {value}")
        self._export_dir = value

    def _temporary_scene_units(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            original_linear_unit = pm.currentUnit(q=True, linear=True)
            if self.temp_linear_unit:
                self.logger.info(
                    f"Temp setting linear unit from {original_linear_unit} to {self.temp_linear_unit}"
                )
                pm.currentUnit(linear=self.temp_linear_unit)
            try:
                return func(*args, **kwargs)
            finally:
                self.logger.debug(f"Reverting linear unit to {original_linear_unit}")
                pm.currentUnit(linear=original_linear_unit)

        return wrapper

    def _temporary_framerate(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            original_time_unit = pm.currentUnit(q=True, time=True)
            if self.temp_time_unit:
                self.logger.info(
                    f"Temp setting time unit from {original_time_unit} to {self.temp_time_unit}"
                )
                if original_time_unit != self.temp_time_unit:
                    self.logger.warning(
                        f"Scene framerate ({original_time_unit}) is different from the temporary framerate ({self.temp_time_unit}), which may result in timing issues."
                    )
                pm.currentUnit(time=self.temp_time_unit)
            try:
                return func(*args, **kwargs)
            finally:
                self.logger.debug(f"Reverting time unit to {original_time_unit}")
                pm.currentUnit(time=original_time_unit)

        return wrapper

    def _apply_preset(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if self.preset:
                self.logger.debug(f"Applying preset: {self.preset}")
                self.load_fbx_export_preset(self.preset)
                self.verify_fbx_preset()
            return func(*args, **kwargs)

        return wrapper

    def load_fbx_export_preset(self, preset_path: str):
        preset_path_escaped = preset_path.replace("\\", "/")
        self.logger.debug(f"Loading FBX export preset from {preset_path_escaped}")
        try:
            pm.mel.eval(f'FBXLoadExportPresetFile -f "{preset_path_escaped}"')
            self.logger.info(f"Loaded FBX export preset from {preset_path_escaped}.")
        except RuntimeError as e:
            self.logger.error(f"Failed to load FBX export preset: {e}")
            raise RuntimeError(f"Failed to load FBX export preset: {e}")

    def batch_export(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, batch: List[str] = None, *args, **kwargs):
            if batch:
                for scene_path in batch:
                    if os.path.exists(scene_path):
                        self.logger.info(f"Opening scene file: {scene_path}")
                        try:
                            pm.openFile(scene_path, force=True)
                            self.initialize_objects()  # Reinitialize objects
                        except RuntimeError as e:
                            self.logger.error(
                                f"Failed to open file: {scene_path}. Error: {e}"
                            )
                            continue
                        func(self, *args, **kwargs)
                    else:
                        self.logger.error(f"Scene file not found: {scene_path}")
            else:
                func(self, *args, **kwargs)

        return wrapper

    def format_export_name(self, name: str) -> str:
        if self.name_regex:
            pattern, replacement = self.name_regex.split("->")
            return re.sub(pattern, replacement, name)
        return name

    def generate_export_path(self, scene_path: str) -> str:
        scene_name = os.path.splitext(os.path.basename(scene_path))[0]
        export_name = self.output_name or scene_name
        if self.timestamp:
            export_name += f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        export_name = self.format_export_name(export_name)
        return os.path.join(self.export_dir, f"{export_name}.fbx")

    def generate_log_file_path(self, export_path: str) -> str:
        base_name = os.path.splitext(os.path.basename(export_path))[0]
        return os.path.join(self.export_dir, f"{base_name}.log")

    @EnvironmentTests.check_all_paths_relative
    @EnvironmentTests.check_for_duplicate_materials
    @EnvironmentTests.check_for_referenced_objects
    @batch_export
    def export(self, export_path: str = None, file_format: str = "FBX export") -> None:
        if not export_path:
            current_scene_path = pm.sceneName()
            export_path = self.generate_export_path(current_scene_path)
            self.logger.info(f"Generated export path: {export_path}")

        log_file_path = self.generate_log_file_path(export_path)
        self.logger.info(f"Generating log file path: {log_file_path}")

        self.setup_file_logging(log_file_path)

        pm.select(self.objects, r=True)
        self.logger.debug(f"Selected objects for export: {self.objects}")

        @self._temporary_scene_units
        @self._temporary_framerate
        @self._apply_preset
        def inner_export():
            if not pm.selected():
                pm.warning("No geometry selected to export.")
                self.logger.warning("No geometry selected to export.")
                return
            try:
                pm.exportSelected(export_path, type=file_format, force=True)
                self.logger.info(f"File exported: {export_path}")
            except Exception as e:
                self.logger.error(f"Failed to export geometry: {e}")
                raise RuntimeError(f"Failed to export geometry: {e}")

        inner_export()


class TextEditHandler(logging.Handler):
    def __init__(self, widget: object):
        super().__init__()
        self.widget = widget

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        level = record.levelname
        color = self.get_color(level)
        formatted_msg = f'<span style="color:{color}">{msg}</span>'
        self.widget.append(formatted_msg)

    def get_color(self, level: str) -> str:
        colors = {
            "DEBUG": "gray",
            "INFO": "white",
            "WARNING": "#FFFF99",  # pastel yellow
            "ERROR": "#FF9999",  # pastel red
            "CRITICAL": "#CC6666",  # dark pastel red
        }
        return colors.get(level, "white")


class SceneExporterSlots(SceneExporter):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PRESET_DIR = os.path.join(BASE_DIR, "presets")

    def __init__(self):
        self.sb = self.switchboard()
        self.ui = self.sb.scene_exporter

        self.ui.txt003.setText(
            "Any selected objects will be exported.  If there is no selection, all visible objects in the scene will be exported."
        )
        # Initialize logging first
        self.setup_logging_redirect(self.ui.txt003)

        # Call super after logging is set up to ensure log handler is available during initialization
        super().__init__()

    def setup_logging_redirect(self, widget: object):
        handler = TextEditHandler(widget)
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))

        # Set up a root logger to capture all logging
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

        # Ensure handlers are not duplicated
        if len(root_logger.handlers) > 1:
            root_logger.handlers = [handler]

    def setup_file_logging(self, log_file_path: str):
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

    @property
    def workspace(self):
        workspace_path = CoreUtils.get_maya_info("workspace")
        if not workspace_path:
            self.logger.error("Workspace directory not found.")
        return workspace_path

    @property
    def presets(self):
        return {
            os.path.splitext(f)[0]: f
            for f in os.listdir(self.PRESET_DIR)
            if f.endswith(".fbxexportpreset")
        }

    def cmb000_init(self, widget):
        widget.refresh = True
        if not widget.is_initialized:
            # Set the preset directory.
            widget.menu.add(
                "QPushButton",
                setToolTip="Set the preset directory.",
                setText="Set Preset Directory",
                setObjectName="b005",
            )
            # Add a button to open the preset directory.
            widget.menu.add(
                "QPushButton",
                setToolTip="Open the preset directory.",
                setText="Open Preset Directory",
                setObjectName="b007",
            )
            # Add a button to add an FBX export preset.
            widget.menu.add(
                "QPushButton",
                setToolTip="Add an FBX export preset.",
                setText="Add Preset",
                setObjectName="b003",
            )
            # Add a button to remove an FBX export preset.
            widget.menu.add(
                "QPushButton",
                setToolTip="Delete the current FBX export preset.",
                setText="Delete Current Preset",
                setObjectName="b004",
            )
        widget.add(self.presets, clear=True)

    def txt000_init(self, widget):
        # Add a button to select an output directory.
        widget.menu.add(
            "QPushButton",
            setToolTip="Set the output directory.",
            setText="Set Output Directory",
            setObjectName="b002",
        )
        # Add a button to open the output directory.
        widget.menu.add(
            "QPushButton",
            setToolTip="Open the output directory.",
            setText="Open Output Directory",
            setObjectName="b008",
        )

    def txt001_init(self, widget):
        # Add a button to optionally add a timestamp suffix.
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

    def b000(self):
        """Export"""
        print("temp_linear_unit", self.ui.cmb001.currentData())
        print("temp_time_unit", self.ui.cmb002.currentData())
        kwargs = {
            "objects": pm.selected() or DisplayUtils.get_visible_geometry,
            "export_dir": self.ui.txt000.text(),
            "preset": self.ui.cmb000.currentData(),
            "output_name": self.ui.txt001.text(),
            "name_regex": self.ui.txt002.text(),
            "timestamp": self.ui.chk004.isChecked(),
            "temp_linear_unit": self.ui.cmb001.currentData(),
            "temp_time_unit": self.ui.cmb002.currentData(),
            "check_duplicate_materials": self.ui.chk001.isChecked(),
            "check_referenced_objects": self.ui.chk002.isChecked(),
            "check_paths_relative": self.ui.chk003.isChecked(),
        }
        exporter = SceneExporter(**kwargs)
        self.ui.txt003.clear()
        self.logger.info("Starting export process..")
        exporter.export()

    def b001_init(self, widget):
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
            self.sb.ComboBox,
            setToolTip="Temporary linear unit to be used during export.",
            setObjectName="cmb001",
        )
        items = ptk.insert_into_dict(EnvUtils.SCENE_UNIT_VALUES, "OFF", None)
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
        widget.menu.cmb002.add(items)

    def b002(self):
        """Set Output Directory"""
        output_dir = self.sb.dir_dialog(
            title="Select an output directory:",
            start_dir=self.workspace,
        )
        if output_dir:
            self.ui.txt000.setText(output_dir)

    def b003(self):
        """Add Preset"""
        fbx_presets = self.sb.file_dialog(
            file_types="*.fbxexportpreset",
            title="Select an FBX export preset:",
            start_dir=self.workspace,
        )
        if fbx_presets:
            for preset in fbx_presets:
                shutil.copy(preset, self.PRESET_DIR)  # copy the file
            self.ui.cmb000.init_slot()  # refresh the combobox
            filename_with_ext = os.path.basename(preset)  # This will be 'file.txt'
            filename_without_ext = os.path.splitext(filename_with_ext)[0]
            self.ui.cmb000.setCurrentText(filename_without_ext)  # select the new preset

    def b004(self):
        """Remove Preset"""
        preset = self.ui.cmb000.currentData()
        if preset:
            preset_file = os.path.join(self.PRESET_DIR, preset)
            os.remove(preset_file)  # remove the file
            self.logger.info(f"Preset deleted: {preset_file}")
            self.ui.cmb000.init_slot()  # refresh the combobox

    def b005(self):
        """Set Preset Directory"""
        preset_dir = self.sb.dir_dialog(
            title="Select the preset directory:",
            start_dir=self.PRESET_DIR,
        )
        if preset_dir:
            self.PRESET_DIR = preset_dir
            self.ui.cmb000.init_slot()

    def b006(self):
        """Open Log File"""
        output_dir = self.ui.txt000.text()
        log_file = os.path.join(output_dir, "scene_export.log")
        if os.path.exists(log_file):
            os.startfile(log_file)

    def b007(self):
        """Open Preset Directory"""
        preset_dir = os.startfile(self.PRESET_DIR)
        if os.path.exists(preset_dir):
            os.startfile(preset_dir)

    def b008(self):
        """Open Output Directory"""
        output_dir = self.ui.txt000.text()
        if os.path.exists(output_dir):
            os.startfile(output_dir)


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
#     check_paths_relative=True,
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
