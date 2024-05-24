import os
import subprocess
import tempfile
from pathlib import Path

try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)

# From this package:
from mayatk import NodeUtils, Components


class RizomUVBridge:
    def __init__(self, rizom_path):
        self._rizom_path = rizom_path
        self._export_path = None  # Default to None, to be set during processing
        self._script_path = None  # Stores the path to the UV script file

    @property
    def rizom_path(self):
        """Get the path to the RizomUV executable as a POSIX string."""
        return self._rizom_path

    @rizom_path.setter
    def rizom_path(self, value):
        """Set and validate the path to the RizomUV executable."""
        resolved_path = Path(os.path.expandvars(value)).as_posix()
        if not resolved_path.is_file():
            raise ValueError(f"RizomUV executable not found at {resolved_path}.")
        self._rizom_path = resolved_path

    @property
    def export_path(self):
        """Lazy initialization of the export path."""
        if self._export_path is None:
            self._export_path = Path(tempfile.gettempdir(), "rizomuv_exported.obj")
        return self._export_path.as_posix()

    @export_path.setter
    def export_path(self, value):
        if value and not value.lower().endswith(".obj"):
            raise ValueError("The specified export path must end with '.obj'")
        self._export_path = Path(value)

    @property
    def script_path(self):
        """Get the path to the UV script file as a POSIX string."""
        if self._script_path is None:
            raise ValueError("Script path is not set.")
        return self._script_path.as_posix()

    @script_path.setter
    def script_path(self, value):
        """Set the UV script, loading from a file if a path is provided, or saving the content to a file."""
        if Path(value).is_file():
            self._script_path = Path(value)
        else:
            self._script_path = self._prepare_script_file(value)

    def process_with_rizomuv(self, objects, uv_script):
        """Simplified process for the entire workflow."""
        if not objects:
            raise ValueError("No objects specified for processing.")

        if uv_script is not None:
            self.script_path = uv_script

        self._export_objects(objects)
        self._execute_uv_script()

        # Directly work with transforms for imported objects for consistency
        imported_transforms = self._import_objects()
        # Ensure only transforms are passed to the transfer method
        original_transforms = NodeUtils.get_transform_node(objects)
        # self._transfer_uvs_and_cleanup(imported_transforms, original_transforms)

    def _import_objects(self):
        """Updated to ensure transform nodes are returned."""
        imported_objs = pm.importFile(
            self.export_path, namespace="RizomUVImport", returnNewNodes=True
        )
        imported_transforms = NodeUtils.get_transform_node(imported_objs)
        return imported_transforms

    def _export_objects(self, objects):
        """Export specified Maya objects to an OBJ file."""
        pm.select(objects, replace=True)
        pm.exportSelected(self.export_path, type="OBJexport", force=True)

    def _execute_uv_script(self):
        """Run the RizomUV script using the prepared script file path."""
        # Ensure the script content is prepared before execution
        if (
            self._script_path
        ):  # Assuming _script_path is set to a valid path or script content
            user_script_content = Path(
                self._script_path
            ).read_text()  # Reads the script content if _script_path is a file path
        else:
            user_script_content = ""  # Default script content if not provided

        # Construct the full script with dynamic inclusion of ZomLoad, ZomSave, ZomQuit
        full_script_content = self._construct_full_script(user_script_content)

        # Prepare the full script file
        self._script_path = self._prepare_script_file(full_script_content)

        subprocess.call([self.rizom_path, "-cfi", self._script_path], shell=False)

    def _transfer_uvs_and_cleanup(self, imported_objects, original_objects):
        """Transfer UVs from imported objects back to the original objects and clean up."""
        # Assume Components.transfer_uvs and NodeUtils.get_transform_node are implemented
        Components.transfer_uvs(imported_objects, original_objects)
        pm.delete(imported_objects)
        pm.namespace(removeNamespace="RizomUVImport", mergeNamespaceWithRoot=True)
        pm.select(original_objects)

    def _construct_full_script(self, user_script):
        script_parts = []

        # Check and dynamically add ZomLoad if not already included
        if "ZomLoad" not in user_script:
            script_parts.append(
                f'ZomLoad({{File={{Path="{self.export_path}", ImportGroups=true, XYZ=true}}, NormalizeUVW=true}})\n'
            )

        script_parts.append(user_script)

        # Dynamically add ZomSave and ZomQuit if not already included
        if "ZomSave" not in user_script:
            script_parts.append(
                f'ZomSave({{File={{Path="{self.export_path}", UVWProps=true}}, __UpdateUIObjFileName=true}})\n'
            )
        if "ZomQuit" not in user_script:
            script_parts.append("ZomQuit()\n")

        return "".join(script_parts)

    def _prepare_script_file(self, script_contents):
        """Prepare and save the Lua script file for RizomUV, returning the file path."""
        script_filename = Path(tempfile.gettempdir(), "riz_uv_script.lua").as_posix()
        with open(script_filename, "w") as file:
            file.write(script_contents)
        # Convert to a Path object and then get a POSIX-style string
        self._script_path = script_filename
        return script_filename


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    rizom_path = os.path.expandvars(
        r"%ProgramFiles%\Rizom Lab\RizomUV 2020.1\Rizomuv_VS.exe"
    )

    # Initialize the bridge to RizomUV
    bridge = RizomUVBridge(rizom_path)

    # Specify the objects to be exported, ensuring only transform nodes are included
    objects = pm.ls(pm.selected(), type="transform")

    # Define your custom RizomUV script here
    uv_script = """
    ZomSelect({PrimType="Edge", Select=true, ResetBefore=true, ProtectMapName="Protect", FilterIslandVisible=true, Auto={Skeleton={}, Open=true, PipesCutter=true, HandleCutter=true}})
    ZomCut({PrimType="Edge"})
    ZomUnfold({PrimType="Edge", MinAngle=1e-005, Mix=1, Iterations=1, PreIterations=5, StopIfOutOFDomain=false, RoomSpace=0, PinMapName="Pin", ProcessNonFlats=true, ProcessSelection=true, ProcessAllIfNoneSelected=true, ProcessJustCut=true, BorderIntersections=true, TriangleFlips=true})
    ZomIslandGroups({Mode="DistributeInTilesEvenly", MergingPolicy=8322, GroupPath="RootGroup"})
    ZomPack({ProcessTileSelection=false, RecursionDepth=1, RootGroup="RootGroup", Scaling={Mode=2}, Rotate={}, Translate=true, LayoutScalingMode=2})
    """.strip()

    # Process with RizomUV
    bridge.process_with_rizomuv(objects, uv_script)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------

#  Deprecated --

# def getAllChildren(nodes, typ="transform"):
#     """Get all child objects of the given nodes.

#     Parameters:
#         nodes (str)(obj)(list) = Parent nodes.
#         typ (str) = List all relatives of the specified type.

#     Return:
#         (list)

#     ex. call: getAllChildren(node, typ='mesh')
#     """
#     result = set()
#     children = set(pm.listRelatives(nodes, fullPath=True, typ=typ) or [])
#     while children:
#         result.update(children)
#         children = (
#             set(pm.listRelatives(children, fullPath=True, typ=typ) or []) - result
#         )

#     return list(result)


# def getSelectionMask():
#     """ """
#     masks = ["mc", "vertex", "edge", "facet", "polymeshUV", "meshUVShell"]

#     if pm.selectMode(query=1, object=1):
#         return "object"

#     else:
#         for mask in masks:
#             if pm.selectType(query=1, **{mask: True}):
#                 return mask


# def setSelectionMask(mask):
#     """ """
#     try:
#         pm.selectMode(**{mask: True})
#     except TypeError:
#         pm.selectMode(component=True)
#         pm.selectType(**{mask: True})


# def sendToRizom(objects, script="", longLineCheck=True, *args):
#     """
#     Parameters:
#         objects (bool) = The Polygon objects to send.
#         longLineCheck (bool) = Intermediate Rizom fix for long lines.

#     Return:
#         (list)
#     """
#     pm.undoInfo(openChunk=1)

#     origSel = objects
#     mask = getSelectionMask()

#     objects = pm.ls(objects, geometry=True, allPaths=True, dag=True)

#     export_filename = "{}{}__temp__.obj".format(tempfile.gettempdir(), os.sep)
#     pm.mel.file(
#         export_filename,
#         f=1,
#         pr=1,
#         typ="OBJexport",
#         es=1,
#         op="groups=1;ptgroups=1;materials=1;smoothing=1;normals=1",
#     )

#     script = """
#         ZomLoad({{File={{Path="odfilepath", ImportGroups=true, XYZ=true}}, NormalizeUVW=true}})
#         --U3dSymmetrySet({{Point={{0, 0, 0}}, Normal={{1, 0, 0}}, Threshold=0.01, Enabled=true, UPos=0.5, LocalMode=false}})
#         \n{}\n
#         ZomSave({{File={{Path="odfilepath", UVWProps=true}}, __UpdateUIObjFileName=true}})
#         ZomQuit()
#         """.format(
#         script
#     ).replace(
#         "odfilepath", export_filename.replace("\\", "/")
#     )

#     script_filename = "{}{}riz.lua".format(tempfile.gettempdir(), os.sep)
#     f = open(script_filename, "w")
#     f.write(script)
#     f.close()

#     cmd = '"{}" -cfi "{}{}riz.lua"'.format(rizomPath, tempfile.gettempdir(), os.sep)
#     subprocess.call(cmd, shell=False)

#     if longLineCheck:
#         f = open(export_filename, "r")
#         lines = f.readlines()
#         f.close()

#     f = open(export_filename, "w")
#     for line in lines:
#         if not line.startswith("#ZOMPROPERTIES"):
#             f.write(line)
#     f.close()

#     _importedOBJs = pm.mel.file(
#         export_filename,
#         i=1,
#         typ="OBJ",
#         returnNewNodes=1,
#         pr=1,
#         options="mo=1",
#         namespace="ODRIZUV",
#         mergeNamespacesOnClash=1,
#     )
#     importedOBJs = pm.ls(_importedOBJs, geometry=True, objectsOnly=True, shapes=False)

#     setSelectionMask(mask)
#     pm.select(origSel, add=True)

#     Components.transfer_uvs(importedOBJs, objects)

#     pm.delete(importedOBJs, constructionHistory=1)
#     transforms = CoreUtils.get_transform_node(importedOBJs)
#     pm.delete(transforms)

#     pm.undoInfo(closeChunk=1)
