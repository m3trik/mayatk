# !/usr/bin/python
# coding=utf-8
import os
import sys
from typing import Dict, ClassVar, Optional, Union, Any

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk


class EnvUtils(ptk.HelpMixin):
    """ """

    SCENE_UNIT_VALUES: ClassVar[Dict[str, str]] = {
        "millimeter": "mm",
        "centimeter": "cm",
        "meter": "m",
        "kilometer": "km",
        "inch": "in",
        "foot": "ft",
        "yard": "yd",
        "mile": "mi",
    }

    @staticmethod
    def get_env_info(key):
        """Fetch specific information about the current Maya environment based on the provided key.

        Parameters:
            key (str): The key corresponding to the specific Maya information to fetch.
                       Can be a single key or multiple keys separated by '|'.
        Returns:
            The corresponding information based on the key, or an error message if the key is invalid.
            If multiple keys are provided, returns a list of values in the order of keys.
        """
        if "|" in key:
            return [EnvUtils.get_env_info(k) for k in key.split("|")]

        available_keys = {
            "install_path": lambda: os.environ.get("MAYA_LOCATION"),
            "version": lambda: pm.about(version=True),
            "renderer": lambda: pm.getAttr("defaultRenderGlobals.currentRenderer"),
            "workspace": lambda: pm.workspace(q=True, rd=True),
            "workspace_dir": lambda: ptk.format_path(
                pm.workspace(q=True, rd=True), "dir"
            ),
            "workspace_path": lambda: ptk.format_path(
                pm.workspace(q=True, rd=True), "path"
            ),
            "sourceimages": lambda: os.path.join(
                pm.workspace(q=True, rd=True), "sourceimages"
            ),
            "scene": lambda: pm.sceneName(),
            "scene_name": lambda: ptk.format_path(pm.sceneName(), "name"),
            "scene_path": lambda: ptk.format_path(pm.sceneName(), "path"),
            "scene_modified": lambda: bool(pm.mel.eval("file -q -modified")),
            "user_name": lambda: pm.optionVar(q="PTglobalUserName"),
            "ui_language": lambda: pm.about(uiLanguage=True),
            "os_type": lambda: pm.about(os=True),
            "linear_units": lambda: pm.currentUnit(q=True, fullName=True),
            "time_units": lambda: pm.currentUnit(q=True, t=True),
            "loaded_plugins": lambda: pm.pluginInfo(q=True, listPlugins=True),
            "api_version": lambda: pm.about(api=True),
            "host_name": lambda: pm.about(hostName=True),
            "batch_mode": lambda: pm.about(batch=True),
            "build_dir": lambda: pm.about(buildDirectory=True),
            "build_version": lambda: pm.about(buildVersion=True),
            "build_varient": lambda: pm.about(buildVariant=True),
            "api_version": lambda: pm.about(apiVersion=True),
            "application": lambda: pm.about(application=True),
            "current_frame": lambda: pm.currentTime(q=True),
            "frame_range": lambda: (
                pm.playbackOptions(q=True, min=True),
                pm.playbackOptions(q=True, max=True),
            ),
            "viewport_renderer": lambda: pm.modelEditor(
                "modelPanel4", q=True, rendererName=True
            ),
            "current_camera": lambda: pm.modelEditor(
                "modelPanel4", q=True, camera=True
            ),
            "available_cameras": lambda: pm.listCameras(),
            "active_layers": lambda: [
                layer.name()
                for layer in pm.ls(type="displayLayer")
                if not layer.attr("visibility").isLocked()
            ],
            "current_tool": lambda: pm.currentCtx(),
            "up_axis": lambda: pm.upAxis(q=True, axis=True),
            "maya_uptime": lambda: pm.timerX(),
            "total_polys": lambda: pm.polyEvaluate(scene=True, triangle=True),
            "total_nodes": lambda: len(pm.ls(dag=True)),
        }

        if key not in available_keys:
            raise KeyError(
                "Invalid key. Available keys are: {}".format(
                    ", ".join(available_keys.keys())
                )
            )

        value = available_keys[key]()
        if value is None:
            raise ValueError(f"The value for {key} could not be found.")

        return value

    @staticmethod
    def append_maya_paths(maya_version=None):
        """Appends various Maya-related paths to the system's Python environment and sys.path.
        This function sets environment variables and extends sys.path to include paths
        for Maya's Python API, libraries, and related functionalities. It aims to
        facilitate the integration of Maya with external Python scripts.

        Parameters:
        maya_version (int, str, optional): The version of Maya to add the paths for.
                                          If None, the function will query the version
                                          using PyMel. Defaults to None.
        Raises:
        EnvironmentError: If the MAYA_LOCATION environment variable is not set.

        Example:
        >>> append_maya_paths()
        This will set paths for the current Maya version in use.

        >>> append_maya_paths(2023)
        This will set paths explicitly for Maya version 2023.

        Returns:
        None
        """
        # Query Maya version if not provided
        if maya_version is None:
            maya_version = pm.about(version=True)

        maya_install_path = os.environ.get("MAYA_LOCATION")
        if not maya_install_path:
            raise EnvironmentError("MAYA_LOCATION environment variable not set.")

        # Setting Environment Variables
        os.environ["PYTHONHOME"] = os.path.join(maya_install_path, "Python")
        os.environ["PATH"] = (
            os.path.join(maya_install_path, "bin") + ";" + os.environ["PATH"]
        )

        # List of paths to append
        paths_to_add = [
            os.path.join(maya_install_path, "bin"),
            os.path.join(maya_install_path, "Python"),
            os.path.join(maya_install_path, "Python", str(maya_version), "DLLs"),
            os.path.join(maya_install_path, "Python", str(maya_version), "lib"),
            os.path.join(
                maya_install_path, "Python", str(maya_version), "lib", "lib-tk"
            ),
            os.path.join(
                maya_install_path, "Python", str(maya_version), "lib", "plat-win"
            ),
            os.path.join(
                maya_install_path, "Python", str(maya_version), "lib", "site-packages"
            ),
            os.path.join(
                maya_install_path, "devkit", "other", "pymel", "extras", "modules"
            ),
            os.path.join(
                maya_install_path, "devkit", "other", "pymel", "extras", "completion"
            ),
        ]

        # Append paths only if they are not already in sys.path
        for path in paths_to_add:
            if path not in sys.path:
                sys.path.append(path)

    @staticmethod
    def load_plugin(plugin_name):
        """Loads a specified plugin.
        This method checks if the plugin is already loaded before attempting to load it.

        Parameters:
            plugin_name (str): The name of the plugin to load.

        Examples:
            load_plugin('nearestPointOnMesh')

        Raises:
            ValueError: If the plugin is not found or fails to load.
        """
        if not pm.pluginInfo(plugin_name, query=True, loaded=True):
            try:
                pm.loadPlugin(plugin_name, quiet=True)
            except RuntimeError as e:
                raise ValueError(f"Failed to load plugin {plugin_name}: {e}")

    @staticmethod
    def vray_plugin(load=False, unload=False, query=False):
        """Load/Unload/Query the Maya Vray Plugin.

        Parameters:
            load (bool): Load the VRay plugin.
            unload (bool): Unload the VRay plugin.
            query (bool): Query the status of the VRay plugin.
        """

        def is_loaded(plugin="vrayformaya.mll"):
            return True if pm.pluginInfo(plugin, q=True, loaded=True) else False

        if query:
            return is_loaded()

        vray = ["vrayformaya.mll", "vrayformayapatch.mll"]
        try:
            if load:
                for plugin in vray:
                    if not is_loaded(plugin):
                        pm.loadPlugin(plugin)
            if unload:
                for plugin in vray:
                    if is_loaded(plugin):
                        pm.unloadPlugin(plugin)
        except Exception as error:
            print(error)

    @staticmethod
    def get_recent_files(index=None):
        """Get a list of recent files sorted by modification time.

        Parameters:
            index (slice or int): Return the recent file directory path at the given index or slice.
                    Index 0 would be the most recent file.
                    For example, use index=slice(0, 5) to get the 5 most recent files.
                    If there are only 3 files, it will return those 3 files without throwing an error.
        Returns:
            (list): A list of recent files sorted by last modification time.

        Examples:
            get_recent_files() --> Returns all recent files sorted by modification time
            get_recent_files(0) --> Returns the most recent file
            get_recent_files(slice(0, 5)) --> Returns the 5 most recent files
        """
        files = pm.optionVar(query="RecentFilesList")
        if not files:
            return []

        # Extend file data with modification times and filter invalid or autosave files
        file_data = []
        for f in files:
            if ptk.is_valid(f, "file") and "Autosave" not in f:
                try:
                    mod_time = os.path.getmtime(f)
                    file_data.append((f, mod_time))
                except OSError:
                    continue  # Skip files that cause errors (e.g., not found)

        # Sort files by modification time, most recent first
        file_data.sort(key=lambda x: x[1], reverse=True)

        # Format paths and extract as a list
        result = [ptk.format_path(f[0]) for f in file_data]

        if index is not None:
            try:
                result = result[index]
            except (IndexError, TypeError) as e:
                print(f"Incorrect index or slice: {e}. Returning empty list.")
                return []

        return result

    @staticmethod
    def get_recent_projects(index=None, format="standard"):
        """Get a list of recently set projects.

        Parameters:
            index (slice or int): Return the recent project directory path at the given index or slice.
                    Index 0 would be the most recent project.
                    For example, use index=slice(0, 5) to get the 5 most recent projects.
                    If there are only 3 projects, it will return those 3 projects without throwing an error.
            format (str): Defines the format of the returned paths. Possible options are 'standard', 'timestamp',
                    'standard|timestamp', 'timestamp|standard'. 'standard' returns paths as strings, 'timestamp'
                    returns timestamped paths, 'standard|timestamp' returns a dictionary with standard paths as
                    keys and timestamped paths as values, 'timestamp|standard' does the opposite.

        Returns:
            (list or dict): A list or dictionary of recent projects depending on the 'format' parameter.

        Examples:
            get_recent_projects() --> Returns all recent projects in standard format
            get_recent_projects(0) --> Returns the most recent project in standard format
            get_recent_projects(slice(0, 5)) --> Returns the 5 most recent projects in standard format.
            get_recent_projects(format='timestamp') --> Returns all recent projects in timestamp format.
            get_recent_projects(format='standard|timestamp') --> Returns a dictionary with standard paths as keys and timestamped paths as values.
        """
        dirs = pm.optionVar(query="RecentProjectsList")
        if not dirs:
            return []

        result = [ptk.format_path(d) for d in reversed(dirs) if ptk.is_valid(d, "dir")]
        if index is not None:
            try:
                result = result[index]
            except (IndexError, TypeError):
                print("Incorrect index or slice. Returning empty list.")
                return []

        format = format.split("|")
        if len(format) == 2 and "timestamp" in format and "standard" in format:
            if format[0] == "timestamp":
                result = {ptk.time_stamp(res): res for res in result}
            else:
                result = {res: ptk.time_stamp(res) for res in result}
        elif "timestamp" in format:
            result = [ptk.time_stamp(res) for res in result]
        # else return the standard format

        return result

    @staticmethod
    def find_autosave_directories():
        """Search for and compile a list of existing autosave directories based on
        predefined locations: the current workspace's autosave directory, the autosave
        directory specified in the MAYA_AUTOSAVE_FOLDER environment variable, and the
        user's home directory autosave folder.

        Returns:
            list: A list of strings, each being a path to an existing autosave directory.
        """
        import itertools

        # Directories to check for autosave files
        potential_dirs = [
            os.path.join(
                pm.workspace(q=True, rd=True), "autosave"
            ),  # Workspace autosave
            os.environ.get("MAYA_AUTOSAVE_FOLDER"),  # Environment variable autosave
            os.path.expanduser("~/maya/autosave"),  # Home directory autosave
        ]

        # Split environment autosave paths and filter out non-existing paths
        autosave_dirs = filter(
            os.path.exists,
            itertools.chain.from_iterable(
                (d.split(";") if d else [] for d in potential_dirs)
            ),
        )
        return list(autosave_dirs)

    @classmethod
    def get_recent_autosave(
        cls, filter_time=None, timestamp_format="%Y-%m-%d %H:%M:%S"
    ):
        """Retrieves a list of recent autosave files from Maya autosave directories, optionally filtered by age and sorted.

        Parameters:
            filter_time (int, optional): Maximum age of the autosave files to include, in hours. Files older than
                                         this will be omitted. If None, all autosave files are included.
            timestamp_format (str): The strftime format to use for displaying the file timestamps.
                                    Defaults to '%Y-%m-%d %H:%M:%S'.

        Returns:
            list: A list of tuples, where each tuple contains:
                  (str 'filepath', str 'formatted timestamp')
                  representing each autosave file.
        """
        from glob import glob
        from datetime import datetime

        autosave_dirs = cls.find_autosave_directories()
        files = []
        for dir in autosave_dirs:
            files.extend(
                glob(os.path.join(dir, "*.mb")) + glob(os.path.join(dir, "*.ma"))
            )

        # Get file info including paths and timestamps
        file_info = ptk.get_file_info(
            files, ["filepath", "unixtimestamp"], force_tuples=True
        )

        # Prepare cutoff time for filtering
        cutoff_timestamp = (
            datetime.now().timestamp() - (filter_time * 3600)
            if filter_time is not None
            else None
        )

        # Filter and format in a single step
        recent_files = []
        for filepath, unixtimestamp in file_info:
            if cutoff_timestamp is None or unixtimestamp > cutoff_timestamp:
                formatted_time = datetime.fromtimestamp(unixtimestamp).strftime(
                    timestamp_format
                )
                recent_files.append((filepath, formatted_time))

        # Sort by unixtimestamp without additional conversion
        recent_files.sort(
            key=lambda x: datetime.strptime(x[1], timestamp_format), reverse=True
        )

        return recent_files

    @staticmethod
    @ptk.filter_results
    def find_workspaces(
        root_dir: str,
        return_type: str = "dir",
        ignore_empty: bool = True,
        recursive: bool = True,
    ) -> list:
        """Recursively find Maya workspaces under a root directory.
        A workspace is a folder containing 'workspace.mel'.

        Parameters:
            root_dir (str): Folder to search from.
            return_type (str): 'dir', 'dirname', 'dirname|dir', or 'dir|dirname'.
            ignore_empty (bool): If True, only include workspaces that contain
                                at least one .ma or .mb file inside the 'scenes/' folder.
            recursive (bool): If True, search recursively for scene files within workspaces
                             for validation. If False, only look in the direct 'scenes/' folder.

        Returns:
            list: Filtered results in the requested format.
        """
        from pathlib import Path

        results = []

        # Walk through the root directory
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Check if workspace.mel exists in the directory
            if "workspace.mel" not in filenames:
                continue

            dirpath = ptk.format_path(dirpath)
            dirname = os.path.basename(dirpath)

            if ignore_empty:
                scenes_path = Path(dirpath) / "scenes"

                # Only check for Maya scene files in the 'scenes' folder
                if scenes_path.is_dir():
                    # Search for scene files based on recursive setting
                    if recursive:
                        # Use rglob for recursive search for scene files
                        scene_files = list(scenes_path.rglob("*.ma")) + list(
                            scenes_path.rglob("*.mb")
                        )
                    else:
                        # Use glob for non-recursive search (direct files only)
                        scene_files = list(scenes_path.glob("*.ma")) + list(
                            scenes_path.glob("*.mb")
                        )

                    # If Maya scene files are found, it's a valid workspace
                    if scene_files:
                        results.append((dirname, dirpath))
                        continue  # Exit early after finding the first scene file

            else:
                results.append((dirname, dirpath))

        # Handle return format (dir, dirname, or both)
        if "|" in return_type:
            a, b = return_type.split("|")
            idx = {"dirname": 0, "dir": 1}
            return [(r[idx[a]], r[idx[b]]) for r in results]

        return [r[0] if return_type == "dirname" else r[1] for r in results]

    @staticmethod
    @ptk.filter_results
    def get_workspace_scenes(
        root_dir: Optional[str] = None,
        full_path: bool = True,
        recursive: bool = False,
        omit_autosave: bool = True,
        file_types=["*.ma", "*.mb"],
    ) -> list[str]:
        """Return a list of Maya scene files (.ma/.mb) from the given or current workspace directory.

        Parameters:
            root_dir (Optional[str]): Directory to scan. Defaults to current workspace.
            full_path (bool): If True, returns full paths; else returns file names.
            recursive (bool): Whether to include subdirectories.
            omit_autosave (bool): Exclude autosave files like name.0001.ma
            file_types (list[str]): List of file extensions to include, e.g., ['*.ma', '*.mb'].

        Returns:
            list[str]: Maya scene file paths or names.
        """
        import re

        root_dir = root_dir or str(pm.workspace(q=True, rd=True))

        files = ptk.get_dir_contents(
            root_dir,
            content="filepath" if full_path else "file",
            recursive=recursive,
            inc_files=file_types,
        )

        if omit_autosave:
            autosave_regex = re.compile(r".+\.\d{4}\.(ma|mb)$")
            files = [f for f in files if not autosave_regex.match(os.path.basename(f))]

        return [ptk.format_path(f) for f in files]

    @classmethod
    def find_workspace_using_path(
        cls, scene_path: Optional[str] = None
    ) -> Optional[str]:
        """Determine the workspace directory for a given scene by moving up directory levels until a workspace.mel file is found.

        Parameters:
            scene_path (Optional[str]): The path to the scene file. If None, the current scene path is used.

        Returns:
            Optional[str]: The directory containing the workspace.mel file, or None if not found.
        """
        if scene_path is None:
            scene_path = cls.get_env_info("scene_path")

        # Handle case where scene_path might be empty or None
        if not scene_path or not os.path.isabs(scene_path):
            return None

        dir_path = os.path.dirname(scene_path)
        while dir_path:
            potential_workspace = os.path.join(dir_path, "workspace.mel")
            if os.path.exists(potential_workspace):
                return dir_path
            new_dir_path = os.path.dirname(dir_path)
            if new_dir_path == dir_path:  # Root directory reached
                break
            dir_path = new_dir_path
        return None

    @staticmethod
    def reference_scene(file_path):
        """Reference a Maya scene.

        Parameters:
            file_path (str): The path to the Maya scene file to reference.
        """
        if os.path.exists(file_path):
            pm.system.createReference(file_path)
        else:
            raise FileNotFoundError(f"No such file: '{file_path}'")

    @staticmethod
    def remove_reference(file_path):
        """Remove a reference to a Maya scene.

        Parameters:
            file_path (str): The path to the Maya scene file to remove the reference to.
        """
        ref_node = pm.system.FileReference(file_path)
        if ref_node.isReferenced():
            ref_node.remove()

    @staticmethod
    def is_referenced(file_path):
        """Check if a Maya scene is referenced.

        Parameters:
            file_path (str): The path to the Maya scene file to check.

        Returns:
            (bool): True if the scene is referenced, False otherwise.
        """
        ref_node = pm.system.FileReference(file_path)
        return ref_node.isReferenced()

    @staticmethod
    def get_reference_nodes(file_path):
        """Get the nodes from a referenced Maya scene.

        Parameters:
            file_path (str): The path to the Maya scene file to get the nodes from.

        Returns:
            (list): A list of nodes in the referenced scene.
        """
        ref_node = pm.system.FileReference(file_path)
        if ref_node.isReferenced():
            return ref_node.nodes()
        else:
            return []

    @staticmethod
    def list_references():
        """List all references in the current Maya scene.

        Returns:
            (list): A list of all references in the current Maya scene.
        """
        return [ref.filePath() for ref in pm.system.listReferences()]

    @staticmethod
    def export_scene_as_fbx(file_path: str = None, **fbx_options: Any) -> None:
        """Export the entire Maya scene as an FBX file with flexible MEL command options.

        Parameters:
            file_path (str): The path where the FBX file will be saved. If None, uses the current scene name.
            **fbx_options: Additional FBX export options as MEL commands (e.g., FBXExportIncludeChildren=True).
        """
        # Set comprehensive default FBX export options
        default_options = {
            "FBXExportCameras": False,  # Export cameras
            "FBXExportLights": False,  # Export lights
            "FBXExportSkins": False,  # Export skinning data
            "FBXExportShapes": False,  # Export shape deformers
            "FBXExportSmoothingGroups": True,  # Export smoothing groups
            "FBXExportSmoothMesh": True,  # Export smooth mesh
            "FBXExportHardEdges": True,  # Export hard edges
            "FBXExportTangents": True,  # Export tangent information
            "FBXExportInstances": True,  # Export instance information
            "FBXExportReferencedAssetsContent": False,  # Export referenced assets
            "FBXExportInputConnections": True,  # Export input connections
            "FBXExportUseSceneName": True,  # Use scene name for export
            "FBXExportUpAxis": "y",  # Set up axis
            "FBXExportScaleFactor": 1.0,  # Scale factor for export
            "FBXExportConvertUnitString": "cm",  # Convert units to centimeters
            "FBXExportTriangulate": False,  # Triangulate meshes
            "FBXExportEmbeddedTextures": True,  # Embed textures in the FBX file
            "FBXExportConstraints": False,  # Export constraints
            "FBXExportAnimationOnly": False,  # Export animation only
            "FBXExportApplyConstantKeyReducer": False,  # Apply constant key reducer
            "FBXExportBakeComplexAnimation": False,  # Bake complex animations
            "FBXExportBakeComplexStart": int(
                pm.playbackOptions(q=True, min=True)
            ),  # Start frame for baking
            "FBXExportBakeComplexEnd": int(
                pm.playbackOptions(q=True, max=True)
            ),  # End frame for baking
        }

        # Update default options with user-specified options
        default_options.update(fbx_options)

        # Apply the FBX export options with the correct syntax
        for option, value in default_options.items():
            if isinstance(value, bool) or isinstance(value, int):
                # Use the -v flag for boolean and integer values
                value_str = (
                    "true" if value is True else "false" if value is False else value
                )
                pm.mel.eval(f"{option} -v {value_str}")
            else:
                pm.mel.eval(f"{option} {value}")

        # Determine the file path if not provided
        if not file_path:
            scene_name = pm.sceneName()
            if not scene_name:
                raise ValueError(
                    "Scene has not been saved yet.\nPlease save the scene first, or specify a file path."
                )
            file_path = scene_name.replace(".mb", ".fbx").replace(".ma", ".fbx")

        try:
            # Export the entire scene using FBXExportAll
            pm.mel.eval(f'FBXExport -f "{file_path}"')
            print(f"Scene successfully exported as FBX to {file_path}")
        except Exception as e:
            print(f"Failed to export scene as FBX: {str(e)}")

    @staticmethod
    def sanitize_namespace(namespace: str) -> str:
        """Sanitize the namespace by replacing or removing illegal characters.

        Parameters:
            namespace (str): The namespace string to sanitize

        Returns:
            str: Sanitized namespace containing only valid characters
        """
        import re

        return re.sub(r"[^a-zA-Z0-9_]", "_", namespace)

    @staticmethod
    def resolve_file_path_in_workspaces(
        selected_file: str, workspace_files: dict
    ) -> Optional[str]:
        """Resolve a file name to its full path by searching in workspace files.

        Parameters:
            selected_file (str): The file name to resolve
            workspace_files (dict): Dictionary mapping workspace paths to file lists

        Returns:
            Optional[str]: Full file path if found, None otherwise
        """
        return next(
            (
                fp
                for files in workspace_files.values()
                for fp in files
                if os.path.basename(fp) == selected_file
            ),
            None,
        )

    @classmethod
    def get_workspace_file_cache(cls, workspaces: list, recursive: bool = True) -> dict:
        """Build a cache of workspace files for multiple workspaces.

        Parameters:
            workspaces (list): List of (dirname, workspace_path) tuples
            recursive (bool): Whether to search recursively for scene files

        Returns:
            dict: Dictionary mapping workspace paths to their scene file lists
        """
        workspace_files = {}

        for _, ws_path in workspaces:
            if os.path.isdir(ws_path):
                scenes = cls.get_workspace_scenes(
                    root_dir=ws_path,
                    full_path=True,
                    recursive=recursive,
                    omit_autosave=True,
                )
                workspace_files[ws_path] = scenes

        return workspace_files

    @staticmethod
    def matches_autosave_pattern(filename: str) -> bool:
        """Check if a file matches the Maya autosave pattern.

        Parameters:
            filename (str): The filename to check

        Returns:
            bool: True if the file matches autosave pattern, False otherwise
        """
        import re

        autosave_regex = re.compile(r".+\.\d{4}\.(ma|mb)$")
        return bool(autosave_regex.match(filename))


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
