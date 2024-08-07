# !/usr/bin/python
# coding=utf-8
import os
import re
import ctypes
import shutil
from datetime import datetime
from functools import wraps
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


class SceneExporterMixin:
    @staticmethod
    def check_all_paths_relative(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            all_relative = True
            if self.check_absolute_paths:
                for mat, typ, pth in self.material_paths:
                    if typ == "Absolute":
                        if all_relative:
                            all_relative = False
                            self.logger.error("Absolute path(s) found:")
                        self.logger.error(f"\t{typ} path - {mat.name()} - {pth}")
                if not all_relative:
                    self.logger.debug(
                        "check_all_paths_relative failed due to absolute paths"
                    )
                    return
            self.logger.debug("check_all_paths_relative passed")
            return func(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def check_for_duplicate_materials(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.check_duplicate_materials:
                duplicate_mapping = MatUtils.find_materials_with_duplicate_textures(
                    self.materials
                )
                if duplicate_mapping:
                    self.logger.error("Duplicate material(s) found:")
                    for original, duplicates in duplicate_mapping.items():
                        for duplicate in duplicates:
                            self.logger.error(
                                f"\tDuplicate: {duplicate} -> Original: {original}"
                            )
                    return
            self.logger.debug("check_for_duplicate_materials passed")
            return func(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def check_for_referenced_objects(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.check_referenced_objects:
                referenced_objects = pm.ls(self.objects, references=True)
                if referenced_objects:
                    self.logger.error("Referenced object(s) found:")
                    for ref in referenced_objects:
                        self.logger.error(f"\t{ref}")
                    return
            self.logger.debug("check_for_referenced_objects passed")
            return func(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def check_hidden_geometry_with_keys(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.check_hidden_geometry_with_keys:
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
                    return
                self.logger.debug("check_hidden_geometry_with_keys passed")
            return func(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def temporary_workspace(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            original_workspace = pm.workspace(query=True, rootDirectory=True)
            new_workspace = EnvUtils.find_workspace_using_path()
            if (
                self.temp_workspace
                and new_workspace
                and not new_workspace == original_workspace
            ):
                pm.workspace(new_workspace, openWorkspace=True)
            self.logger.info(
                f"Setting workspace to: {pm.workspace(query=True, rootDirectory=True)}"
            )
            try:
                return func(self, *args, **kwargs)
            finally:
                self.logger.debug(f"Reverting workspace to {original_workspace}")
                pm.workspace(original_workspace, openWorkspace=True)

        return wrapper

    @staticmethod
    def temporary_scene_units(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            original_linear_unit = pm.currentUnit(query=True, linear=True)
            if self.temp_linear_unit:
                self.logger.info(
                    f"Setting linear unit from {original_linear_unit} to {self.temp_linear_unit}"
                )
                pm.currentUnit(linear=self.temp_linear_unit)
            try:
                return func(self, *args, **kwargs)
            finally:
                self.logger.debug(f"Reverting linear unit to {original_linear_unit}")
                pm.currentUnit(linear=original_linear_unit)

        return wrapper

    @staticmethod
    def temporary_framerate(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            original_time_unit = pm.currentUnit(query=True, time=True)
            if self.temp_time_unit:
                self.logger.info(
                    f"Setting time unit from {original_time_unit} to {self.temp_time_unit}"
                )
                if original_time_unit != self.temp_time_unit:
                    self.logger.warning(
                        f"Scene framerate ({original_time_unit}) is different from the export framerate ({self.temp_time_unit}), which may result in timing issues."
                    )
                pm.currentUnit(time=self.temp_time_unit)
            try:
                return func(self, *args, **kwargs)
            finally:
                self.logger.debug(f"Reverting time unit to {original_time_unit}")
                pm.currentUnit(time=original_time_unit)

        return wrapper

    @staticmethod
    def apply_preset(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.preset:
                self.logger.debug(f"Applying preset: {self.preset}")
                self.load_fbx_export_preset(self.preset)
                self.verify_fbx_preset()
            self.logger.debug("apply_preset passed")
            return func(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def initialize_params(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, **kwargs):
            self.initialize(**kwargs)
            return func(self)

        return wrapper

    @staticmethod
    def batch_export(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, batch: Optional[List[str]] = None, *args, **kwargs):
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
                self.logger.debug("batch_export passed")
                return func(self, *args, **kwargs)

        return wrapper


class SceneExporter(SceneExporterMixin, ptk.LoggingMixin):
    def __init__(self, **kwargs):
        self.initialize(**kwargs)
        self.logger.debug("SceneExporter initialization complete.")

    def initialize(
        self,
        objects: Optional[List[str]] = None,
        export_dir: Optional[str] = None,
        preset: Optional[str] = None,
        output_name: Optional[str] = None,
        name_regex: Optional[str] = None,
        timestamp: Optional[bool] = None,
        temp_linear_unit: Optional[str] = None,
        temp_time_unit: Optional[str] = None,
        temp_workspace: Optional[bool] = None,
        convert_to_relative_paths: bool = False,
        delete_unused_materials: bool = False,
        exclude_materials: List[str] = [],
        check_absolute_paths: bool = False,
        check_duplicate_materials: bool = False,
        reassign_duplicate_materials: bool = False,
        check_hidden_geometry_with_keys: bool = False,
        check_referenced_objects: bool = False,
        log_level: str = "WARNING",
        create_log_file: Optional[bool] = None,
        hide_log_file: Optional[bool] = None,
        log_handler: Optional[object] = None,
    ):
        self._export_dir = export_dir
        self._preset = preset
        self.output_name = output_name
        self.name_regex = name_regex
        self.timestamp = timestamp
        self.temp_linear_unit = temp_linear_unit
        self.temp_time_unit = temp_time_unit
        self.temp_workspace = temp_workspace
        self.check_absolute_paths = check_absolute_paths
        self.check_duplicate_materials = check_duplicate_materials
        self.reassign_duplicate_materials = reassign_duplicate_materials
        self.check_hidden_geometry_with_keys = check_hidden_geometry_with_keys
        self.check_referenced_objects = check_referenced_objects
        self.convert_to_relative_paths = convert_to_relative_paths
        self.delete_unused_materials = delete_unused_materials
        self.exclude_materials = exclude_materials

        self.create_log_file = create_log_file
        self.hide_log_file = hide_log_file

        self.logger.setLevel(log_level)
        self.logger.propagate = True
        if log_handler:
            self.logger.addHandler(log_handler)

        if objects is not None:
            self.logger.debug("Calling initialize_objects from initialize")
            self.initialize_objects(objects)

        self.logger.debug("SceneExporter initialized with new parameters.")

    def initialize_objects(self, objects):
        self.objects = objects() if callable(objects) else pm.ls(objects)
        self.logger.debug(f"Objects initialized: {self.objects}")
        self.materials = MatUtils.filter_materials_by_objects(self.objects)
        self.logger.debug(f"Materials initialized: {self.materials}")

        if self.convert_to_relative_paths:
            self.logger.debug("Converting absolute paths to relative")
            MatUtils.convert_to_relative_paths(self.materials)

        self.material_paths = MatUtils.collect_material_paths(
            self.materials,
            include_material=True,
            include_path_type=True,
            exc=self.exclude_materials,
            nested_as_unit=True,
        )

        if self.reassign_duplicate_materials:
            self.logger.debug("Reassigning duplicate materials")
            MatUtils.reassign_duplicates(self.materials)

        if self.delete_unused_materials:
            self.logger.debug("Deleting unused materials")
            pm.mel.hyperShadePanelMenuCommand("hyperShadePanel1", "deleteUnusedNodes")

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

    @SceneExporterMixin.initialize_params
    @SceneExporterMixin.batch_export
    @SceneExporterMixin.temporary_workspace
    @SceneExporterMixin.temporary_scene_units
    @SceneExporterMixin.temporary_framerate
    @SceneExporterMixin.check_all_paths_relative
    @SceneExporterMixin.check_for_duplicate_materials
    @SceneExporterMixin.check_hidden_geometry_with_keys
    @SceneExporterMixin.check_for_referenced_objects
    @SceneExporterMixin.apply_preset
    def export(self, **kwargs) -> None:
        file_format = kwargs.get("file_format", "FBX export")
        export_path = self.generate_export_path()
        self.logger.info(f"Generated export path: {export_path}")

        if self.create_log_file:
            log_file_path = self.generate_log_file_path(export_path)
            self.logger.info(f"Generating log file path: {log_file_path}")
            self.setup_file_logging(log_file_path)

        pm.select(self.objects, replace=True)
        self.logger.debug(f"Selected objects for export: {self.objects}")

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
        finally:
            if self.create_log_file:
                self.close_file_handlers()

    def load_fbx_export_preset(self, preset_path: str):
        preset_path_escaped = preset_path.replace("\\", "/")
        self.logger.debug(f"Loading FBX export preset from {preset_path_escaped}")
        try:
            pm.mel.eval(f'FBXLoadExportPresetFile -f "{preset_path_escaped}"')
            self.logger.info(f"Loaded FBX export preset from {preset_path_escaped}.")
        except RuntimeError as e:
            self.logger.error(f"Failed to load FBX export preset: {e}")
            raise RuntimeError(f"Failed to load FBX export preset: {e}")

    def format_export_name(self, name: str) -> str:
        if self.name_regex:
            pattern, replacement = self.name_regex.split("->")
            return re.sub(pattern, replacement, name)
        return name

    def generate_export_path(self) -> str:
        scene_path = pm.sceneName()
        scene_name = os.path.splitext(os.path.basename(scene_path))[0]
        export_name = self.output_name or scene_name
        if self.timestamp:
            export_name += f"_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        export_name = self.format_export_name(export_name)
        return os.path.join(self.export_dir, f"{export_name}.fbx")

    def generate_log_file_path(self, export_path: str) -> str:
        base_name = os.path.splitext(os.path.basename(export_path))[0]
        return os.path.join(self.export_dir, f"{base_name}.log")

    def setup_file_logging(self, log_file_path: str):
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
        root_logger = self.logging.getLogger(self.__class__.__name__)
        handlers = root_logger.handlers[:]
        for handler in handlers:
            if isinstance(handler, self.logging.FileHandler):
                handler.close()
                root_logger.removeHandler(handler)
                self.logger.debug("File handler closed and removed.")


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
        items = {f"Override Time Unit: {key}": value for key, value in items.items()}
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

        self.export(
            objects=pm.selected() or DisplayUtils.get_visible_geometry,
            export_dir=self.ui.txt000.text(),
            preset=self.ui.cmb000.currentData(),
            output_name=self.ui.txt001.text(),
            name_regex=self.ui.txt002.text(),
            timestamp=self.ui.chk004.isChecked(),
            temp_linear_unit=self.ui.cmb001.currentData(),
            temp_time_unit=self.ui.cmb002.currentData(),
            temp_workspace=self.ui.chk006.isChecked(),
            create_log_file=self.ui.chk005.isChecked(),
            convert_to_relative_paths=self.ui.chk007.isChecked(),
            delete_unused_materials=self.ui.chk008.isChecked(),
            check_absolute_paths=self.ui.chk003.isChecked(),
            check_duplicate_materials=self.ui.chk001.isChecked(),
            reassign_duplicate_materials=self.ui.chk009.isChecked(),
            check_hidden_geometry_with_keys=self.ui.chk010.isChecked(),
            check_referenced_objects=self.ui.chk002.isChecked(),
            exclude_materials=["*.dds", "ibl_brdf_lut.png"],
            log_level=self.ui.cmb003.currentData(),
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
