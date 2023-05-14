# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

# from pythontk import Str, Iter

# from this package:
# from mayatk import coretk


class File:
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
            [
                ptk.File.formatPath(f)
                for f in list(reversed(files))
                if "Autosave" not in f
            ]
            if files
            else []
        )
        try:
            result = result[index]
        except (IndexError, TypeError) as error:
            pass

        if timestamp:  # attach modified timestamp
            result = ptk.File.timeStamp(result)

        return result

    @staticmethod
    def getRecentProjects():
        """Get a list of recently set projects.

        Returns:
            (list)
        """
        files = pm.optionVar(query="RecentProjectsList")
        result = [ptk.File.formatPath(f) for f in list(reversed(files))]

        return result

    @staticmethod
    def getRecentAutosave(timestamp=False):
        """Get a list of autosave files.

        Parameters:
            timestamp (bool): Attach a modified timestamp and date to given file path(s).

        Returns:
            (list)
        """
        dir1 = str(pm.workspace(query=1, rd=1)) + "autosave"  # current project path.
        dir2 = os.environ.get("MAYA_AUTOSAVE_FOLDER").split(";")[
            0
        ]  # get autosave dir path from env variable.

        files = ptk.File.getDirContents(
            dir1, "filepaths", incFiles=("*.mb", "*.ma")
        ) + ptk.File.getDirContents(dir2, "filepaths", incFiles=("*.mb", "*.ma"))
        result = [
            ptk.File.formatPath(f) for f in list(reversed(files))
        ]  # Replace any backslashes with forward slashes and reverse the list.

        if timestamp:  # attach modified timestamp
            result = ptk.File.timeStamp(result, sort=True)

        return result

    @staticmethod
    def getWorkspaceScenes(fullPath=True):
        """Get a list of maya scene files from the current workspace directory.

        Parameters:
            fullPath (bool): Return the full path instead of just the filename.

        Returns:
            (list)
        """
        workspace_dir = str(pm.workspace(query=1, rd=1))  # get current project path.

        files = ptk.File.getDirContents(
            workspace_dir, "filepaths", incFiles=("*.mb", "*.ma")
        )
        result = [
            ptk.File.formatPath(f) for f in files
        ]  # Replace any backslashes with forward slashes.

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
            namespace = (
                scene.split("\\")[-1].rstrip(".mb").rstrip(".ma")
            )  # ex. 'sceneName' from 'sceneName.mb'
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
