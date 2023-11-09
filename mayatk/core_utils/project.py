# !/usr/bin/python
# coding=utf-8
import os

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk


class Project:
    """ """

    @staticmethod
    def get_recent_files(index=None, format="standard"):
        """
        Get a list of recent files.

        Parameters:
            index (slice or int): Return the recent file directory path at the given index or slice.
                    Index 0 would be the most recent file.
                    For example, use index=slice(0, 5) to get the 5 most recent files.
                    If there are only 3 files, it will return those 3 files without throwing an error.
            format (str): Defines the format of the returned paths. Possible options are 'standard', 'timestamp',
                    'standard|timestamp', 'timestamp|standard'. 'standard' returns paths as strings, 'timestamp'
                    returns timestamped paths, 'standard|timestamp' returns a dictionary with standard paths as
                    keys and timestamped paths as values, 'timestamp|standard' does the opposite.

        Returns:
            (list or dict): A list or dictionary of recent files depending on the 'format' parameter.

        Examples:
            get_recent_files() --> Returns all recent files in standard format
            get_recent_files(0) --> Returns the most recent file in standard format
            get_recent_files(slice(0, 5)) --> Returns the 5 most recent files in standard format.
            get_recent_files(format='timestamp') --> Returns all recent files in timestamp format.
            get_recent_files(format='standard|timestamp') --> Returns a dictionary with standard paths as keys and timestamped paths as values.
        """
        files = pm.optionVar(query="RecentFilesList")
        if not files:
            return []

        result = [
            ptk.format_path(f)
            for f in reversed(files)
            if ptk.is_valid(f) and "Autosave" not in f
        ]

        if index is not None:
            try:
                result = result[index]
            except (IndexError, TypeError):
                print(f"Incorrect index or slice. Returning empty list.")
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
    def get_recent_projects(index=None, format="standard"):
        """
        Get a list of recently set projects.

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
        """
        Search for and compile a list of existing autosave directories based on
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
    def get_recent_autosave(cls, index=None, format="standard"):
        """
        Retrieves a list or dictionary of recent Maya autosave files, optionally filtered
        by an index or a slice, and formatted according to the specified output format.

        Parameters:
            index (slice|int|None): If provided, specifies the subset of autosave files to
                return. Can be an integer for a specific file, or a slice object for a range.
                Defaults to None, which returns all autosave files.
            format (str): Determines the format of the returned paths. Options are 'standard',
                'timestamp', 'standard|timestamp', and 'timestamp|standard'. 'standard' returns
                paths as strings, 'timestamp' returns paths with timestamps, and the combined
                formats return dictionaries with the paths formatted as specified.

        Returns:
            list|dict: Depending on the 'format' parameter, a list of file paths, a list of
                timestamped file paths, or a dictionary with file paths as keys and their
                timestamped counterparts as values, or vice versa.

        Raises:
            IndexError: If an integer index is out of range.
            TypeError: If the index is neither an integer nor a slice.
        """
        import itertools
        import glob

        autosave_dirs = cls.find_autosave_directories()
        result = []

        for autosave_dir in autosave_dirs:
            files = itertools.chain(
                glob.iglob(os.path.join(autosave_dir, "*.mb")),
                glob.iglob(os.path.join(autosave_dir, "*.ma")),
            )

            for file in files:
                result.append(ptk.format_path(file))

        if index is not None:
            try:
                result = result[index]
            except (IndexError, TypeError):
                print("Incorrect index or slice. Returning empty list.")
                return []

        format_parts = format.split("|")
        if (
            len(format_parts) == 2
            and "timestamp" in format_parts
            and "standard" in format_parts
        ):
            result = (
                {res: ptk.time_stamp(res) for res in result}
                if format_parts[0] == "standard"
                else {ptk.time_stamp(res): res for res in result}
            )
        elif "timestamp" in format_parts:
            result = [ptk.time_stamp(res) for res in result]

        return result

    @staticmethod
    def get_workspace_scenes(fullPath=True):
        """Get a list of maya scene files from the current workspace directory.

        Parameters:
            fullPath (bool): Return the full path instead of just the filename.

        Returns:
            (list)
        """
        workspace_dir = str(pm.workspace(q=True, rd=1))  # get current project path.

        files = ptk.get_dir_contents(
            workspace_dir, "filepath", inc_files=("*.mb", "*.ma")
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
