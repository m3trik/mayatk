import os
import re
import logging
from datetime import datetime
from functools import wraps
from typing import List, Tuple, Dict, Any, Optional, Callable

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

# From this package:
from mayatk import display_utils


class EnvironmentTests:
    def __init__(
        self, objects: Optional[Callable[[], List[str]]] = None, log_level=logging.INFO
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)
        self.object_init_func = objects
        self.initialize_objects()

    def initialize_objects(self):
        if self.object_init_func:
            try:
                self.objects = self.object_init_func()
            except TypeError:
                self.objects = pm.ls(self.object_init_func)
        else:
            self.objects = []

    def find_referenced_objects(self) -> List[str]:
        referenced_objects = pm.ls(self.objects, references=True)
        return referenced_objects

    def collect_material_paths(self) -> List[Tuple[str, str, str]]:
        results = []
        source_images_dir = pm.workspace(q=True, rd=True) + "sourceimages/"
        materials = self.get_materials()

        ignore_files = {
            "diffuse_cube.dds",
            "specular_cube.dds",
            "ibl_brdf_lut.dds",
            "ibl_brdf_lut.png",
        }

        for material in materials:
            file_nodes = pm.listConnections(material, type="file")
            if file_nodes:
                for file_node in file_nodes:
                    file_path = pm.getAttr(f"{file_node}.fileTextureName")
                    if file_path and not any(
                        file_path.endswith(i) for i in ignore_files
                    ):
                        if os.path.isabs(file_path):
                            if file_path.startswith(source_images_dir):
                                relative_path = os.path.relpath(
                                    file_path, source_images_dir
                                )
                                results.append((material, "Relative", relative_path))
                            else:
                                results.append((material, "Absolute", file_path))
                        else:
                            results.append((material, "Relative", file_path))

        return results

    def all_material_paths_relative(self) -> bool:
        results = self.collect_material_paths()
        all_relative = all(
            path_type == "Relative" for material, path_type, path in results
        )
        return all_relative

    def get_material_properties(self, material) -> Dict[str, Any]:
        properties = {"shader_type": material.nodeType(), "attributes": {}}
        common_attrs = [
            "color",
            "transparency",
            "ambientColor",
            "incandescence",
            "specularColor",
        ]
        for attr in common_attrs:
            if material.hasAttr(attr):
                properties["attributes"][attr] = material.attr(attr).get()

        file_textures = pm.listConnections(material, type="file")
        texture_paths = [
            pm.getAttr(f"{file}.fileTextureName") for file in file_textures
        ]
        properties["textures"] = sorted(texture_paths)

        return properties

    def find_duplicate_materials(self) -> List[str]:
        materials = self.get_materials()
        material_properties = [
            self.get_material_properties(material) for material in materials
        ]
        duplicates = []
        for i, mat_props in enumerate(material_properties):
            for j in range(i + 1, len(material_properties)):
                if mat_props == material_properties[j]:
                    duplicates.append(materials[j].name())

        return duplicates

    def check_all_paths_relative(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.check_paths_relative:
                all_paths_relative = self.all_material_paths_relative()
                if not all_paths_relative:
                    self.logger.error("Absolute path(s) found:")
                    for m, s, p in self.collect_material_paths():
                        if s == "Absolute":
                            self.logger.error(f"\t{s, m, p}")
                    return
            return func(self, *args, **kwargs)

        return wrapper

    def check_for_duplicate_materials(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.check_duplicate_materials:
                duplicate_materials = self.find_duplicate_materials()
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
                referenced_objects = self.find_referenced_objects()
                if referenced_objects:
                    self.logger.error("Referenced object(s) found:")
                    for ref in referenced_objects:
                        self.logger.error(f"\t{ref}")
                    return
            return func(self, *args, **kwargs)

        return wrapper

    def filter_materials_by_objects(self, objects: List[str]) -> List[str]:
        """Filter materials assigned to the given objects.

        Args:
            objects (List[str]): List of object names.

        Returns:
            List[str]: List of material names assigned to the given objects.
        """
        assigned_materials = set()
        for obj in objects:
            shading_groups = pm.listConnections(obj, type="shadingEngine")
            for sg in shading_groups:
                materials = pm.listConnections(sg + ".surfaceShader")
                assigned_materials.update(materials)
        return list(assigned_materials)

    def get_materials(self) -> List[str]:
        """Get materials for the specified objects or the entire scene.

        Returns:
            List[str]: List of material names.
        """
        return self.filter_materials_by_objects(self.objects)


class SceneExporter(EnvironmentTests):
    def __init__(
        self,
        objects: Optional[List[str]] = None,
        export_dir: str = None,
        preset: str = None,
        temp_linear_unit: str = None,
        temp_time_unit: str = None,
        check_paths_relative: bool = False,
        check_duplicate_materials: bool = False,
        check_referenced_objects: bool = False,
        name_regex: str = None,
        log_level=logging.INFO,
    ):
        super().__init__(objects=objects, log_level=log_level)
        self.temp_linear_unit = temp_linear_unit
        self.temp_time_unit = temp_time_unit
        self._preset = preset
        self._export_dir = export_dir
        self.check_paths_relative = check_paths_relative
        self.check_duplicate_materials = check_duplicate_materials
        self.check_referenced_objects = check_referenced_objects
        self.name_regex = name_regex

    @property
    def preset(self) -> str:
        if self._preset:
            if os.path.isabs(self._preset):
                return os.path.abspath(os.path.expandvars(self._preset))
            else:
                return os.path.abspath(
                    os.path.expandvars(
                        os.path.join(os.path.dirname(__file__), "presets", self._preset)
                    )
                )
        return None

    @preset.setter
    def preset(self, value: str) -> None:
        self._preset = value

    @property
    def export_dir(self) -> str:
        if self._export_dir:
            return os.path.abspath(os.path.expandvars(self._export_dir))
        return None

    @export_dir.setter
    def export_dir(self, value: str) -> None:
        self._export_dir = value

    def _temporary_scene_units(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            original_linear_unit = pm.currentUnit(q=True, linear=True)
            if self.temp_linear_unit:
                pm.currentUnit(linear=self.temp_linear_unit)
            try:
                return func(*args, **kwargs)
            finally:
                pm.currentUnit(linear=original_linear_unit)

        return wrapper

    def _temporary_framerate(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            original_time_unit = pm.currentUnit(q=True, time=True)
            if self.temp_time_unit:
                pm.currentUnit(time=self.temp_time_unit)
            try:
                return func(*args, **kwargs)
            finally:
                pm.currentUnit(time=original_time_unit)

        return wrapper

    def _apply_preset(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if self.preset:
                self.load_fbx_export_preset(self.preset)
                self.verify_fbx_preset()
            return func(*args, **kwargs)

        return wrapper

    def load_fbx_export_preset(self, preset_path: str):
        preset_path_escaped = preset_path.replace("\\", "/")
        try:
            pm.mel.eval(f'FBXLoadExportPresetFile -f "{preset_path_escaped}"')
            self.logger.info(f"Loaded FBX export preset from {preset_path_escaped}.")
        except RuntimeError as e:
            self.logger.error(f"Failed to load FBX export preset: {e}")
            raise RuntimeError(f"Failed to load FBX export preset: {e}")

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

    def batch_export(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, batch: List[str] = None, *args, **kwargs):
            if batch:
                for scene_path in batch:
                    if os.path.exists(scene_path):
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
        formatted_name = (
            f"{self.format_export_name(scene_name)}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            or f"untitled_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        )
        export_name = f"{formatted_name}.fbx"
        return os.path.join(self.export_dir, export_name)

    @EnvironmentTests.check_all_paths_relative
    @EnvironmentTests.check_for_duplicate_materials
    @EnvironmentTests.check_for_referenced_objects
    @batch_export
    def export(self, export_path: str = None, file_format: str = "FBX export") -> None:
        pm.select(self.objects, r=True)

        if not export_path:
            current_scene_path = pm.sceneName()
            export_path = self.generate_export_path(current_scene_path)

        @self._temporary_scene_units
        @self._temporary_framerate
        @self._apply_preset
        def inner_export():
            if not pm.selected():
                pm.warning("No geometry selected to export.")
                self.logger.warning("No geometry selected to export.")
                return

            self.logger.info(
                f"Starting export: {os.path.splitext(os.path.basename(export_path))[0]}"
            )

            try:
                pm.exportSelected(export_path, type=file_format, force=True)
                self.logger.info(f"File exported: {export_path}")
            except Exception as e:
                self.logger.error(f"Failed to export geometry: {e}")
                raise RuntimeError(f"Failed to export geometry: {e}")

        inner_export()


if __name__ == "__main__":
    preset = r"unity_animation.fbxexportpreset"
    # export_dir = r"%userprofile%/Desktop/test"
    export_dir = r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder\PRODUCTION\AF\F-15E\Exports for Unity\Fuel_Tank_Buildup\Module_03"
    # export_dir = r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder\PRODUCTION\AF\F-15E\Exports for Unity\Fuel_Tank_Buildup\Module_03\TANK_3B_00\Revisions_5_31_2024"
    name_regex = r"_module->"

    exporter = SceneExporter(
        objects=display_utils.DisplayUtils.get_visible_geometry,
        export_dir=export_dir,
        preset=preset,
        temp_linear_unit="m",
        temp_time_unit="ntsc",
        check_duplicate_materials=True,
        check_referenced_objects=True,
        check_paths_relative=True,
        name_regex=name_regex,
        log_level=logging.DEBUG,
    )

    exporter.export()

    # batch = [
    #     r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder\PRODUCTION\AF\F-15E\PRODUCTION\Maya\Fuel_Tanks_Build\scenes\modules\FT3A_TANK\FT3A_TANK_module.ma",
    #     r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder\PRODUCTION\AF\F-15E\PRODUCTION\Maya\Fuel_Tanks_Build\scenes\modules\FT3A_BOOST_PUMP_CANISTER\FT3A_BOOST_PUMP_CANISTER_module.ma",
    #     r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder\PRODUCTION\AF\F-15E\PRODUCTION\Maya\Fuel_Tanks_Build\scenes\modules\FT3A_BLADDER_UNROLLED\FT3A_BLADDER_UNROLLED_module.ma",
    # ]
    # exporter.export(batch=batch)


#  Deprecated:

# class EnvironmentTests:
#     def __init__(self, log_level=logging.INFO):
#         self.logger = logging.getLogger(self.__class__.__name__)
#         self.logger.setLevel(log_level)

#     def find_referenced_objects(self) -> List[str]:
#         referenced_objects = pm.ls(references=True)
#         return referenced_objects

#     def collect_material_paths(self) -> List[tuple]:
#         results = []
#         source_images_dir = pm.workspace(q=True, rd=True) + "sourceimages/"
#         materials = pm.ls(materials=True)

#         ignore_files = {
#             "diffuse_cube.dds",
#             "specular_cube.dds",
#             "ibl_brdf_lut.dds",
#             "ibl_brdf_lut.png",
#         }

#         for material in materials:
#             file_nodes = pm.listConnections(material, type="file")
#             if file_nodes:
#                 for file_node in file_nodes:
#                     file_path = pm.getAttr(f"{file_node}.fileTextureName")
#                     if file_path and not any(
#                         file_path.endswith(i) for i in ignore_files
#                     ):
#                         if os.path.isabs(file_path):
#                             if file_path.startswith(source_images_dir):
#                                 relative_path = os.path.relpath(
#                                     file_path, source_images_dir
#                                 )
#                                 results.append((material, "Relative", relative_path))
#                             else:
#                                 results.append((material, "Absolute", file_path))
#                         else:
#                             results.append((material, "Relative", file_path))

#         return results

#     def all_material_paths_relative(self) -> bool:
#         results = self.collect_material_paths()
#         all_relative = all(
#             path_type == "Relative" for material, path_type, path in results
#         )
#         return all_relative

#     def get_material_properties(self, material) -> Dict[str, any]:
#         properties = {"shader_type": material.nodeType(), "attributes": {}}
#         common_attrs = [
#             "color",
#             "transparency",
#             "ambientColor",
#             "incandescence",
#             "specularColor",
#         ]
#         for attr in common_attrs:
#             if material.hasAttr(attr):
#                 properties["attributes"][attr] = material.attr(attr).get()

#         file_textures = pm.listConnections(material, type="file")
#         texture_paths = [
#             pm.getAttr(f"{file}.fileTextureName") for file in file_textures
#         ]
#         properties["textures"] = sorted(texture_paths)

#         return properties

#     def find_duplicate_materials(self) -> List[str]:
#         materials = pm.ls(materials=True)
#         material_properties = [
#             self.get_material_properties(material) for material in materials
#         ]
#         duplicates = []
#         for i, mat_props in enumerate(material_properties):
#             for j in range(i + 1, len(material_properties)):
#                 if mat_props == material_properties[j]:
#                     duplicates.append(materials[j].name())

#         return duplicates

#     def check_all_paths_relative(func):
#         @wraps(func)
#         def wrapper(self, *args, **kwargs):
#             if self.check_paths_relative:
#                 all_paths_relative = self.all_material_paths_relative()
#                 if not all_paths_relative:
#                     self.logger.error("Absolute path(s) found:")
#                     for m, s, p in self.collect_material_paths():
#                         if s == "Absolute":
#                             self.logger.error(f"\t{s, m, p}")
#                     return
#             return func(self, *args, **kwargs)

#         return wrapper

#     def check_for_duplicate_materials(func):
#         @wraps(func)
#         def wrapper(self, *args, **kwargs):
#             if self.check_duplicate_materials:
#                 duplicate_materials = self.find_duplicate_materials()
#                 if duplicate_materials:
#                     self.logger.error("Duplicate material(s) found:")
#                     for i in duplicate_materials:
#                         self.logger.error(f"\t{i}")
#                     return
#             return func(self, *args, **kwargs)

#         return wrapper

#     def check_for_referenced_objects(func):
#         @wraps(func)
#         def wrapper(self, *args, **kwargs):
#             if self.check_referenced_objects:
#                 referenced_objects = self.find_referenced_objects()
#                 if referenced_objects:
#                     self.logger.error("Referenced object(s) found:")
#                     for ref in referenced_objects:
#                         self.logger.error(f"\t{ref}")
#                     return
#             return func(self, *args, **kwargs)

#         return wrapper


# class SceneExporter(EnvironmentTests):
#     def __init__(
#         self,
#         export_dir: str = None,
#         preset: str = None,
#         temp_linear_unit: str = None,
#         temp_time_unit: str = None,
#         check_paths_relative: bool = False,
#         check_duplicate_materials: bool = False,
#         check_referenced_objects: bool = False,
#         name_regex: str = None,
#         log_level=logging.INFO,
#     ):
#         super().__init__(log_level=log_level)
#         self.temp_linear_unit = temp_linear_unit
#         self.temp_time_unit = temp_time_unit
#         self._preset = preset
#         self._export_dir = export_dir
#         self.check_paths_relative = check_paths_relative
#         self.check_duplicate_materials = check_duplicate_materials
#         self.check_referenced_objects = check_referenced_objects
#         self.name_regex = name_regex

#     @property
#     def preset(self) -> str:
#         if self._preset:
#             if os.path.isabs(self._preset):
#                 return os.path.abspath(os.path.expandvars(self._preset))
#             else:
#                 return os.path.abspath(
#                     os.path.expandvars(
#                         os.path.join(os.path.dirname(__file__), "presets", self._preset)
#                     )
#                 )
#         return None

#     @preset.setter
#     def preset(self, value: str) -> None:
#         self._preset = value

#     @property
#     def export_dir(self) -> str:
#         if self._export_dir:
#             return os.path.abspath(os.path.expandvars(self._export_dir))
#         return None

#     @export_dir.setter
#     def export_dir(self, value: str) -> None:
#         self._export_dir = value

#     def _temporary_scene_units(self, func: Callable) -> Callable:
#         @wraps(func)
#         def wrapper(*args, **kwargs):
#             original_linear_unit = pm.currentUnit(q=True, linear=True)
#             if self.temp_linear_unit:
#                 pm.currentUnit(linear=self.temp_linear_unit)
#             try:
#                 return func(*args, **kwargs)
#             finally:
#                 pm.currentUnit(linear=original_linear_unit)

#         return wrapper

#     def _temporary_framerate(self, func: Callable) -> Callable:
#         @wraps(func)
#         def wrapper(*args, **kwargs):
#             original_time_unit = pm.currentUnit(q=True, time=True)
#             if self.temp_time_unit:
#                 pm.currentUnit(time=self.temp_time_unit)
#             try:
#                 return func(*args, **kwargs)
#             finally:
#                 pm.currentUnit(time=original_time_unit)

#         return wrapper

#     def _temporary_preset(self, func: Callable) -> Callable:
#         @wraps(func)
#         def wrapper(*args, **kwargs):
#             original_preset = self.preset
#             if self.preset:
#                 try:
#                     preset_path_escaped = self.preset.replace("\\", "/")
#                     pm.mel.eval(f'FBXLoadExportPresetFile -f "{preset_path_escaped}"')
#                     self.logger.info(f"Loaded FBX export preset from {self.preset}.")
#                 except Exception as e:
#                     self.logger.error(f"Failed to load FBX export preset: {e}")
#                     raise RuntimeError(f"Failed to load FBX export preset: {e}")

#             try:
#                 return func(*args, **kwargs)
#             finally:
#                 if original_preset:
#                     try:
#                         preset_path_escaped = original_preset.replace("\\", "/")
#                         pm.mel.eval(
#                             f'FBXLoadExportPresetFile -f "{preset_path_escaped}"'
#                         )
#                         self.logger.debug(
#                             f"Restored FBX export preset from {original_preset}."
#                         )
#                     except Exception as e:
#                         self.logger.error(f"Failed to restore FBX export preset: {e}")

#         return wrapper

#     def batch_export(func: Callable) -> Callable:
#         @wraps(func)
#         def wrapper(self, batch: List[str] = None, *args, **kwargs):
#             if batch:
#                 for scene_path in batch:
#                     if os.path.exists(scene_path):
#                         try:
#                             pm.openFile(scene_path, force=True)
#                         except RuntimeError as e:
#                             self.logger.error(
#                                 f"Failed to open file: {scene_path}. Error: {e}"
#                             )
#                             continue
#                         func(self, *args, **kwargs)
#                     else:
#                         self.logger.error(f"Scene file not found: {scene_path}")
#             else:
#                 func(self, *args, **kwargs)

#         return wrapper

#     def format_export_name(self, name: str) -> str:
#         if self.name_regex:
#             pattern, replacement = self.name_regex.split("->")
#             return re.sub(pattern, replacement, name)
#         return name

#     def generate_export_path(self, scene_path: str) -> str:
#         scene_name = os.path.splitext(os.path.basename(scene_path))[0]
#         formatted_name = self.format_export_name(scene_name)
#         export_name = f"{formatted_name}.fbx"
#         return os.path.join(self.export_dir, export_name)

#     @EnvironmentTests.check_all_paths_relative
#     @EnvironmentTests.check_for_duplicate_materials
#     @EnvironmentTests.check_for_referenced_objects
#     @batch_export
#     def export_visible(self, file_format: str = "FBX export") -> None:
#         visible_geometry = display_utils.DisplayUtils.get_visible_geometry()
#         pm.select(visible_geometry, r=True)
#         self.export_selected(file_format=file_format)

#     def export_selected(
#         self, export_path: str = None, file_format: str = "FBX export"
#     ) -> None:
#         if not export_path:
#             current_scene_path = pm.sceneName()
#             export_path = self.generate_export_path(current_scene_path)

#         @self._temporary_scene_units
#         @self._temporary_framerate
#         @self._temporary_preset
#         def inner_export():
#             if not pm.selected():
#                 pm.warning("No geometry selected to export.")
#                 self.logger.warning("No geometry selected to export.")
#                 return

#             self.logger.info(
#                 f"Starting export: {os.path.splitext(os.path.basename(export_path))[0]}"
#             )

#             try:
#                 pm.exportSelected(export_path, type=file_format, force=True)
#                 self.logger.info(f"File exported: {export_path}")
#             except Exception as e:
#                 self.logger.error(f"Failed to export geometry: {e}")
#                 raise RuntimeError(f"Failed to export geometry: {e}")

#         inner_export()
