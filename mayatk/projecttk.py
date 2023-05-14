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
    def getRecentFiles(index=None, timestamp=False):
        """Get a list of recent files.

        Parameters:
            index (int): Return the recent file directory path at the given index. Index 0 would be the most recent file.
            timestamp (bool): Attach a modified timestamp and date to given file path(s).

        Returns:
            (list)(str)
        """
        files = pm.optionVar(query="RecentFilesList")
        result = (
            [File.formatPath(f) for f in list(reversed(files)) if "Autosave" not in f]
            if files
            else []
        )
        try:
            result = result[index]
        except (IndexError, TypeError) as error:
            pass

        if timestamp:  # attach modified timestamp
            result = File.timeStamp(result)

        return result

    @staticmethod
    def getRecentProjects():
        """Get a list of recently set projects.

        Returns:
            (list)
        """
        files = pm.optionVar(query="RecentProjectsList")
        result = [File.formatPath(f) for f in list(reversed(files))]

        return result

    @staticmethod
    def getRecentAutosave(timestamped=False, standard=False):
        """
        This function returns a list of recent Maya autosave files (.mb and .ma), sorted by timestamp.
        It first tries to get the autosave directory from the Maya project's settings,
        then from the 'MAYA_AUTOSAVE_FOLDER' environment variable, and finally checks the '~/maya/autosave' directory.

        Parameters:
            standard (bool): If True, the function will return a list of standard file paths.
            timestamped (bool): If True, the function will return a list of timestamped file paths.

        Returns:
            list or dict: A list of standard file paths, a list of timestamped file paths,
            or a dictionary where keys are timestamped file paths and values are standard file paths,
            depending on the input flags.
        """
        import glob
        import itertools
        from datetime import datetime

        # Try to get the autosave directory from the current Maya project
        autosave_dirs = [str(pm.workspace(query=1, rd=1)) + "autosave"]

        # Try to get the autosave directory from the environment variable
        env_autosave_dir = os.environ.get("MAYA_AUTOSAVE_FOLDER")
        if env_autosave_dir is not None:
            autosave_dirs += env_autosave_dir.split(";")

        # Add the default autosave directory
        autosave_dirs.append(os.path.expanduser("~/maya/autosave"))

        # Get a list of all .mb and .ma files in the autosave directories
        files_dict = {}
        for autosave_dir in autosave_dirs:
            if not os.path.exists(autosave_dir):
                continue

            files = itertools.chain(
                glob.iglob(os.path.join(autosave_dir, "*.mb")),
                glob.iglob(os.path.join(autosave_dir, "*.ma")),
            )

            for file in files:
                timestamp = datetime.fromtimestamp(os.path.getmtime(file)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if standard and timestamped:
                    files_dict[timestamp] = file
                elif standard:
                    files_dict[timestamp] = None
                elif timestamped:
                    files_dict[timestamp] = None

        # Sort the dictionary by keys (timestamps)
        files_dict = dict(sorted(files_dict.items(), reverse=True))

        # Return the results
        if standard and timestamped:
            return files_dict
        elif standard:
            return [v for k, v in files_dict.items() if v is not None]
        elif timestamped:
            return [k for k, v in files_dict.items() if v is not None]
        else:
            return None

    @staticmethod
    def getWorkspaceScenes(fullPath=True):
        """Get a list of maya scene files from the current workspace directory.

        Parameters:
            fullPath (bool): Return the full path instead of just the filename.

        Returns:
            (list)
        """
        workspace_dir = str(pm.workspace(query=1, rd=1))  # get current project path.

        files = File.getDirContents(
            workspace_dir, "filepaths", incFiles=("*.mb", "*.ma")
        )
        # Replace any backslashes with forward slashes.
        result = [File.formatPath(f) for f in files]

        if not fullPath:
            result = [f.split("\\")[-1] for f in result]

        return result

    @staticmethod
    def referenceScene(scene, remove=False, lockReference=False):
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
# def getRecentAutosave(timestamp=False):
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

#     result = File.getDirContents(dir1, "filepaths", incFiles=("*.mb", "*.ma"))
#     if dir2 is not None:  # Add the files from the second directory if it exists
#         result += File.getDirContents(dir2, "filepaths", incFiles=("*.mb", "*.ma"))
#     # # Replace any backslashes with forward slashes and reverse the list.
#     # result = [File.formatPath(f) for f in list(reversed(files))]

#     if timestamp:  # attach modified timestamp
#         result = File.timeStamp(result, sort=True)

#     return result
