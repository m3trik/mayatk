# !/usr/bin/python
# coding=utf-8
import os

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk


class Project(ptk.HelpMixin):
    """ """

    SCENE_UNIT_VALUES = [
        "millimeter",
        "centimeter",
        "meter",
        "kilometer",
        "inch",
        "foot",
        "yard",
        "mile",
    ]

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
            if ptk.is_valid(f) and "Autosave" not in f:
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
        files = pm.optionVar(query="RecentProjectsList")
        if not files:
            return []

        result = [ptk.format_path(f) for f in reversed(files) if ptk.is_valid(f)]

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
    def get_workspace_scenes(fullPath=True, recursive=False):
        """Get a list of maya scene files from the current workspace directory.

        Parameters:
            fullPath (bool): Return the full path instead of just the filename.
            recursive (bool): Whether to return results from just the root directory.

        Returns:
            (list)
        """
        workspace_dir = str(pm.workspace(q=True, rd=1))  # get current project path.

        files = ptk.get_dir_contents(
            workspace_dir, "filepath", recursive=recursive, inc_files=("*.mb", "*.ma")
        )
        # Replace any backslashes with forward slashes.
        result = [ptk.format_path(f) for f in files]

        if not fullPath:
            result = [f.split("\\")[-1] for f in result]

        return result

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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
