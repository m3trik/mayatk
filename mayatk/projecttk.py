# !/usr/bin/python
# coding=utf-8
import os

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

from pythontk import File


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
            File.format_path(f)
            for f in reversed(files)
            if File.is_valid(f) and "Autosave" not in f
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
                result = {File.time_stamp(res): res for res in result}
            else:
                result = {res: File.time_stamp(res) for res in result}
        elif "timestamp" in format:
            result = [File.time_stamp(res) for res in result]
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

        result = [File.format_path(f) for f in reversed(files) if File.is_valid(f)]

        if index is not None:
            try:
                result = result[index]
            except (IndexError, TypeError):
                print(f"Incorrect index or slice. Returning empty list.")
                return []

        format = format.split("|")
        if len(format) == 2 and "timestamp" in format and "standard" in format:
            if format[0] == "timestamp":
                result = {File.time_stamp(res): res for res in result}
            else:
                result = {res: File.time_stamp(res) for res in result}
        elif "timestamp" in format:
            result = [File.time_stamp(res) for res in result]
        # else return the standard format

        return result

    @staticmethod
    def get_recent_autosave(index=None, format="standard"):
        """
        Returns a list of recent Maya autosave files (.mb and .ma), sorted by timestamp.

        Parameters:
            index (slice or int): Return the recent autosave file directory path at the given index or slice.
                    Index 0 would be the most recent autosave file.
                    For example, use index=slice(0, 5) to get the 5 most recent autosave files.
                    If there are only 3 autosave files, it will return those 3 autosave files without throwing an error.
            format (str): Defines the format of the returned paths. Possible options are 'standard', 'timestamp',
                    'standard|timestamp', 'timestamp|standard'. 'standard' returns paths as strings, 'timestamp'
                    returns timestamped paths, 'standard|timestamp' returns a dictionary with standard paths as
                    keys and timestamped paths as values, 'timestamp|standard' does the opposite.

        Returns:
            (list or dict): A list or dictionary of recent autosave files depending on the 'format' parameter.

        Examples:
            get_recent_autosave() --> Returns all recent autosave files in standard format
            get_recent_autosave(0) --> Returns the most recent autosave file in standard format
            get_recent_autosave(slice(0, 5)) --> Returns the 5 most recent autosave files in standard format.
            get_recent_autosave(format='timestamp') --> Returns all recent autosave files in timestamp format.
            get_recent_autosave(format='standard|timestamp') --> Returns a dictionary with standard paths as keys and timestamped paths as values.
        """
        import glob
        import itertools

        autosave_dirs = [str(pm.workspace(query=1, rd=1)) + "autosave"]
        env_autosave_dir = os.environ.get("MAYA_AUTOSAVE_FOLDER")
        if env_autosave_dir is not None:
            autosave_dirs += env_autosave_dir.split(";")
        autosave_dirs.append(os.path.expanduser("~/maya/autosave"))

        result = []
        for autosave_dir in autosave_dirs:
            if not os.path.exists(autosave_dir):
                continue

        files = itertools.chain(
            glob.iglob(os.path.join(autosave_dir, "*.mb")),
            glob.iglob(os.path.join(autosave_dir, "*.ma")),
        )

        for file in files:
            result.append(File.format_path(file))

        if index is not None:
            try:
                result = result[index]
            except (IndexError, TypeError):
                print(f"Incorrect index or slice. Returning empty list.")
                return []

        format = format.split("|")
        if len(format) == 2 and "timestamp" in format and "standard" in format:
            if format[0] == "timestamp":
                result = {File.time_stamp(res): res for res in result}
            else:
                result = {res: File.time_stamp(res) for res in result}
        elif "timestamp" in format:
            result = [File.time_stamp(res) for res in result]
        # else return the standard format

        return result

    @staticmethod
    def get_workspace_scenes(fullPath=True):
        """Get a list of maya scene files from the current workspace directory.

        Parameters:
            fullPath (bool): Return the full path instead of just the filename.

        Returns:
            (list)
        """
        workspace_dir = str(pm.workspace(query=1, rd=1))  # get current project path.

        files = File.get_dir_contents(
            workspace_dir, "filepaths", inc_files=("*.mb", "*.ma")
        )
        # Replace any backslashes with forward slashes.
        result = [File.format_path(f) for f in files]

        if not fullPath:
            result = [f.split("\\")[-1] for f in result]

        return result

    @staticmethod
    def reference_scene(scene, remove=False, lockReference=False):
        """Create a reference to a Maya scene.

        Parameters:
            remove (bool): Remove a previously referenced scene.
        """
        if remove:  # unload reference.
            # refNode = pm.referenceQuery(scene, referenceNode=True)
            pm.mel.file(scene, removeReference=True)

        else:  # load reference.
            # ex. 'sceneName' from 'sceneName.mb'
            namespace = scene.split("\\")[-1].rstrip(".mb").rstrip(".ma")
            pm.mel.file(
                scene,
                reference=True,
                namespace=namespace,
                groupReference=True,
                lockReference=lockReference,
                loadReferenceDepth="topOnly",
                force=True,
            )


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------


# @staticmethod
# def get_recent_autosave(timestamp=False):
#     """Get a list of autosave files.

#     Parameters:
#         timestamp (bool): Attach a modified timestamp and date to given file path(s).

#     Returns:
#         (list)
#     """
# dir1 = str(pm.workspace(query=1, rd=1)) + "autosave"  # current project path.
# # get autosave dir path from env variable.
# dir2 = os.environ.get("MAYA_AUTOSAVE_FOLDER")
# if dir2 is not None:  # Check if the environment variable exists
#     dir2 = dir2.split(";")[0]

#     result = File.get_dir_contents(dir1, "filepaths", inc_files=("*.mb", "*.ma"))
#     if dir2 is not None:  # Add the files from the second directory if it exists
#         result += File.get_dir_contents(dir2, "filepaths", inc_files=("*.mb", "*.ma"))
#     # # Replace any backslashes with forward slashes and reverse the list.
#     # result = [File.format_path(f) for f in list(reversed(files))]

#     if timestamp:  # attach modified timestamp
#         result = File.time_stamp(result, sort=True)

#     return result
