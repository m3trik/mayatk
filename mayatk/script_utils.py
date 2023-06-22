# !/usr/bin/python
# coding=utf-8
import sys, os
import importlib
import inspect

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)


class Script:
    """ """

    MAYA_VERSION = "2023"  # Module level variable for Maya version

    DOCS_URLS = {  # URLs for various documentation
        "maya_mel": f"http://help.autodesk.com/cloudhelp/{MAYA_VERSION}/ENU/Maya-Tech-Docs/Commands/",
        "maya_python": f"http://help.autodesk.com/cloudhelp/{MAYA_VERSION}/ENU/Maya-Tech-Docs/CommandsPython/",
        "pymel": f"http://download.autodesk.com/global/docs/maya{MAYA_VERSION}/en_us/PyMel/search.html?q=",
    }

    @staticmethod
    def search_documentation(keyword, doc_type):
        """Search MEL, Python, or PyMel documentation."""
        url = f"{YourClassName.DOCS_URLS[doc_type]}{keyword}"
        pm.showHelp(url, absolute=True)

    @staticmethod
    def open_mel_command_ref():
        """Open Maya MEL commands list."""
        pm.showHelp(
            f"http://download.autodesk.com/us/maya/{MAYA_VERSION}help/Commands/index.html",
            absolute=True,
        )

    @staticmethod
    def get_mel_globals(keyword=None, ignore_case=True):
        """Get global MEL variables."""
        variables = [
            v
            for v in sorted(pm.mel.eval("env"))
            if not keyword
            or (
                v.count(keyword)
                if not ignore_case
                else v.lower().count(keyword.lower())
            )
        ]
        return variables

    @staticmethod
    def list_ui_objects():
        """List all UI objects."""
        ui_objects = {
            "windows": pm.lsUI(windows=True),
            "panels": pm.lsUI(panels=True),
            "editors": pm.lsUI(editors=True),
            "menus": pm.lsUI(menus=True),
            "menuItems": pm.lsUI(menuItems=True),
            "controls": pm.lsUI(controls=True),
            "controlLayouts": pm.lsUI(controlLayouts=True),
            "contexts": pm.lsUI(contexts=True),
        }
        for category, objects in ui_objects.items():
            print(f"{category}:\n{objects}\n")


# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------
