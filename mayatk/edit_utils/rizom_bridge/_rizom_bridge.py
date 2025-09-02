import os
import subprocess
import tempfile
from pathlib import Path

try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)

# From this package:
from mayatk import NodeUtils, UvUtils


class RizomUVBridge:
    def __init__(self, rizom_path):
        self._rizom_path = rizom_path
        self._export_path = None  # Default to None, to be set during processing
        self._script_path = None  # Stores the path to the UV script file
        # Mapping of exported (temporary suffixed) transform short names -> original transform PyNode
        self._export_name_map = {}
        # Suffix applied to temporary duplicate nodes to avoid FBX re-import overwriting originals
        self._temp_suffix = "__RZTMP"

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
            # Try using a different temp directory that might have better permissions
            temp_dir = (
                Path.home() / "temp"
                if (Path.home() / "temp").exists()
                else Path(tempfile.gettempdir())
            )
            self._export_path = temp_dir / "rizomuv_exported.fbx"
        return self._export_path.as_posix()

    @export_path.setter
    def export_path(self, value):
        if value and not (
            value.lower().endswith(".obj") or value.lower().endswith(".fbx")
        ):
            raise ValueError("The specified export path must end with '.obj' or '.fbx'")
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
        self._transfer_uvs_and_cleanup(imported_transforms, original_transforms)

    def _import_objects(self):
        """Updated to ensure transform nodes are returned."""
        print(f"Debug: Importing objects from: {self.export_path}")

        # Determine file type
        file_ext = Path(self.export_path).suffix.lower()

        # Ensure we have a unique namespace that doesn't conflict
        import_namespace = "RizomUVImport"

        # Remove the namespace if it already exists to ensure clean import
        if pm.namespace(exists=import_namespace):
            print(f"Debug: Removing existing namespace: {import_namespace}")
            pm.namespace(removeNamespace=import_namespace, mergeNamespaceWithRoot=True)

        # Create a fresh namespace
        pm.namespace(addNamespace=import_namespace)
        print(f"Debug: Created namespace: {import_namespace}")

        try:
            if file_ext == ".fbx":
                # Ensure FBX plugin is loaded first
                if not pm.pluginInfo("fbxmaya", query=True, loaded=True):
                    print("Debug: Loading FBX plugin...")
                    pm.loadPlugin("fbxmaya")

                print("Debug: Importing FBX using Maya file command...")

                # Use Maya's file command for reliable namespace import
                import_cmd = f'file -import -type "FBX" -ignoreVersion -mergeNamespacesOnClash false -namespace "{import_namespace}" -options "fbx" -pr "{self.export_path}";'
                print(f"Debug: Executing command: {import_cmd}")
                pm.mel.eval(import_cmd)

                # Get all objects in the namespace - try different approaches
                imported_objs = pm.ls(f"{import_namespace}:*", type="transform")
                print(f"Debug: Transform objects in namespace: {imported_objs}")

                # If no transforms found, check for any nodes in the namespace
                if not imported_objs:
                    all_namespace_nodes = pm.ls(f"{import_namespace}:*")
                    print(f"Debug: All nodes in namespace: {all_namespace_nodes}")

                    # Try to find shapes and get their transforms
                    shape_nodes = pm.ls(f"{import_namespace}:*", type="mesh")
                    if shape_nodes:
                        imported_objs = []
                        for shape in shape_nodes:
                            transforms = pm.listRelatives(
                                shape, parent=True, type="transform"
                            )
                            if transforms:
                                imported_objs.extend(transforms)
                        print(f"Debug: Transforms found from shapes: {imported_objs}")

                # If still no objects found in namespace, look for suffix objects anywhere
                if not imported_objs:
                    print(
                        f"Debug: No objects found in namespace, searching for suffix '{self._temp_suffix}' anywhere..."
                    )
                    all_transforms = pm.ls(type="transform")
                    suffix_objects = [
                        t
                        for t in all_transforms
                        if t.nodeName().endswith(self._temp_suffix)
                    ]
                    print(
                        f"Debug: Found {len(suffix_objects)} objects with suffix: {suffix_objects}"
                    )
                    imported_objs = suffix_objects

            else:  # .obj
                imported_objs = pm.importFile(
                    self.export_path,
                    namespace=import_namespace,
                    returnNewNodes=True,
                    type="OBJ",
                )
                print(f"Debug: OBJ import returned: {imported_objs}")

        except Exception as e:
            print(f"Debug: Import failed: {e}")
            # Final fallback: try without namespace
            try:
                print("Debug: Trying import without namespace as final fallback...")
                existing_transforms = set(pm.ls(type="transform"))

                if file_ext == ".fbx":
                    pm.mel.eval(
                        f'file -import -type "FBX" -ignoreVersion -options "fbx" -pr "{self.export_path}";'
                    )
                else:
                    pm.importFile(self.export_path, type="OBJ")

                new_transforms = set(pm.ls(type="transform"))
                imported_objs = list(new_transforms - existing_transforms)

                # Filter to only those with our suffix
                suffix_objects = [
                    t for t in imported_objs if t.nodeName().endswith(self._temp_suffix)
                ]
                print(
                    f"Debug: Fallback without namespace found {len(suffix_objects)} suffix objects: {suffix_objects}"
                )
                imported_objs = suffix_objects

            except Exception as e2:
                print(f"Debug: Final fallback also failed: {e2}")
                imported_objs = []

        # Filter to get only transform nodes (already filtered for suffix above)
        imported_transforms = (
            NodeUtils.get_transform_node(imported_objs) if imported_objs else []
        )

        print(
            f"Debug: Final transform nodes (with suffix '{self._temp_suffix}'): {imported_transforms}"
        )

        return imported_transforms

    def _export_objects(self, objects):
        """Export specified Maya objects to an FBX (preferred) or OBJ file after duplicating with a unique suffix.

        Strategy:
        1. Duplicate each original transform and append a temp suffix so names are unique.
        2. Export only the duplicated (suffixed) transforms so re-import will not overwrite originals.
        3. Delete the duplicates locally (their geometry lives inside the exported file now).
        4. Later, on import, we detect suffixed names and map them back to originals for UV transfer.
        """
        # Reset mapping each run
        self._export_name_map = {}

        original_transforms = NodeUtils.get_transform_node(objects)
        if not original_transforms:
            raise ValueError("No valid transform nodes supplied for export.")

        duplicates = []
        for orig in original_transforms:
            try:
                dup = pm.duplicate(orig, rr=True, ic=True)[0]
                new_name = f"{orig.nodeName()}{self._temp_suffix}"
                dup = pm.rename(dup, new_name)
                duplicates.append(dup)
                # Store mapping using short (namespace-free) name
                self._export_name_map[new_name.split(":")[-1]] = orig
            except Exception as dup_err:
                print(f"Debug: Failed to duplicate {orig}: {dup_err}")
        print(
            f"Debug: Created {len(duplicates)} duplicates for export with suffix '{self._temp_suffix}'"
        )

        if not duplicates:
            raise RuntimeError("Failed to create any duplicates for export.")

        # Ensure the export directory exists
        export_dir = Path(self.export_path).parent
        export_dir.mkdir(parents=True, exist_ok=True)

        pm.select(duplicates, replace=True)
        print(
            f"Debug: Exporting {len(duplicates)} duplicated objects to: {self.export_path}"
        )

        try:
            # Try FBX export first
            pm.exportSelected(self.export_path, type="FBX export", force=True)
            print("Debug: FBX export completed successfully")
        except Exception as e:
            print(f"Debug: FBX export failed: {e}")
            # Fallback to OBJ in a different location
            try:
                obj_path = str(Path(self.export_path).with_suffix(".obj"))
                print(f"Debug: Trying OBJ export to: {obj_path}")
                pm.exportSelected(
                    obj_path,
                    type="OBJ",
                    force=True,
                    options="groups=1;ptgroups=1;materials=1;smoothing=1;normals=1",
                )
                # Update the export path to the successful export
                self._export_path = Path(obj_path)
                print("Debug: OBJ export completed successfully")
            except Exception as obj_error:
                # Last resort - try exporting to Maya's project directory
                project_dir = pm.workspace(query=True, rootDirectory=True)
                fallback_path = Path(project_dir) / "rizomuv_temp.fbx"
                try:
                    print(
                        f"Debug: Trying FBX export to project directory: {fallback_path}"
                    )
                    pm.exportSelected(str(fallback_path), type="FBX export", force=True)
                    self._export_path = fallback_path
                    print("Debug: Fallback FBX export completed successfully")
                except Exception as final_error:
                    raise RuntimeError(
                        f"All export attempts failed. FBX: {e}, OBJ: {obj_error}, Fallback: {final_error}"
                    )
        finally:
            # Remove the temporary duplicates from the scene before re-import
            try:
                pm.delete(duplicates)
                print("Debug: Deleted temporary duplicated export nodes.")
            except Exception as cleanup_err:
                print(f"Debug: Failed to delete duplicates: {cleanup_err}")

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

        print(f"Debug: About to execute RizomUV with script: {self._script_path}")
        print(f"Debug: Script content:\n{full_script_content}")
        print(f"Debug: Export file path: {self.export_path}")

        # Check if export file exists before RizomUV processing
        export_file = Path(self.export_path)
        if export_file.exists():
            print(
                f"Debug: Export file exists before RizomUV: {export_file.stat().st_size} bytes"
            )
        else:
            print("Debug: Warning - Export file does not exist before RizomUV!")

        # Execute RizomUV with improved error handling
        try:
            print(
                f"Debug: Executing command: {self.rizom_path} -cfi {self._script_path}"
            )
            result = subprocess.run(
                [self.rizom_path, "-cfi", self._script_path],
                shell=False,
                capture_output=True,
                text=True,
                timeout=120,
            )  # 2 minute timeout
            print(f"Debug: RizomUV return code: {result.returncode}")
            if result.stdout:
                print(f"Debug: RizomUV stdout: {result.stdout}")
            if result.stderr:
                print(f"Debug: RizomUV stderr: {result.stderr}")
        except subprocess.TimeoutExpired:
            print("Debug: RizomUV process timed out after 2 minutes")
        except Exception as e:
            print(f"Debug: Error executing RizomUV: {e}")

        # Check if export file was modified by RizomUV
        if export_file.exists():
            print(
                f"Debug: Export file exists after RizomUV: {export_file.stat().st_size} bytes"
            )
        else:
            print("Debug: Warning - Export file does not exist after RizomUV!")

    def _transfer_uvs_and_cleanup(self, imported_objects, original_objects):
        """Transfer UVs from imported objects back to the original objects and clean up."""
        print(f"Debug: Starting UV transfer...")
        print(f"Debug: Imported objects: {imported_objects}")
        print(f"Debug: Original objects: {original_objects}")
        print(
            f"Debug: Number of imported: {len(imported_objects) if imported_objects else 0}"
        )
        print(
            f"Debug: Number of original: {len(original_objects) if original_objects else 0}"
        )

        if not imported_objects or not original_objects:
            print("Debug: No objects to transfer UVs between!")
            return

        # Build ordered source/destination lists using the export mapping
        src_list = []
        dst_list = []
        for imp in imported_objects:
            short = imp.nodeName().split(":")[-1]
            if short in self._export_name_map:
                dst = self._export_name_map[short]
                src_list.append(imp)
                dst_list.append(dst)
            else:
                print(
                    f"Debug: Imported object {imp} not found in export map; skipping."
                )

        print(
            f"Debug: Prepared {len(src_list)} source objects and {len(dst_list)} destination objects for UV transfer."
        )

        if not src_list or not dst_list:
            print("Debug: No valid mapped object pairs for UV transfer.")
        else:
            # Attempt a batch transfer if lengths match
            if len(src_list) == len(dst_list):
                try:
                    print("Debug: Attempting batch UV transfer...")
                    UvUtils.transfer_uvs(src_list, dst_list)
                    print("Debug: Batch UV transfer completed successfully!")
                except Exception as batch_err:
                    print(
                        f"Debug: Batch UV transfer failed ({batch_err}); attempting pairwise transfers..."
                    )
                    for s, d in zip(src_list, dst_list):
                        try:
                            UvUtils.transfer_uvs([s], [d])
                            print(f"Debug: Pairwise UV transfer success: {s} -> {d}")
                        except Exception as pair_err:
                            print(
                                f"Debug: Pairwise UV transfer failed for {s} -> {d}: {pair_err}"
                            )
            else:
                print(
                    "Debug: Source/Destination list length mismatch; skipping batch transfer."
                )
                for s, d in zip(src_list, dst_list):
                    try:
                        UvUtils.transfer_uvs([s], [d])
                        print(f"Debug: Pairwise UV transfer success: {s} -> {d}")
                    except Exception as pair_err:
                        print(
                            f"Debug: Pairwise UV transfer failed for {s} -> {d}: {pair_err}"
                        )

        print("Debug: Cleaning up imported objects...")
        pm.delete(imported_objects)
        pm.namespace(removeNamespace="RizomUVImport", mergeNamespaceWithRoot=True)
        pm.select(original_objects)
        print("Debug: Cleanup completed.")

    def _construct_full_script(self, user_script):
        script_parts = []

        # Determine file extension for RizomUV script
        file_ext = Path(self.export_path).suffix.lower()

        # Convert path to forward slashes for RizomUV Lua scripts (works on both Windows and Unix)
        export_path_normalized = str(self.export_path).replace("\\", "/")

        # Check and dynamically add ZomLoad if not already included
        if "ZomLoad" not in user_script:
            if file_ext == ".fbx":
                script_parts.append(
                    f'ZomLoad({{File={{Path="{export_path_normalized}", ImportGroups=true, XYZ=true, FBX=true}}, NormalizeUVW=true}})\n'
                )
            else:  # .obj
                script_parts.append(
                    f'ZomLoad({{File={{Path="{export_path_normalized}", ImportGroups=true, XYZ=true}}, NormalizeUVW=true}})\n'
                )

        script_parts.append(user_script)

        # Dynamically add ZomSave and ZomQuit if not already included
        if "ZomSave" not in user_script:
            if file_ext == ".fbx":
                script_parts.append(
                    f'ZomSave({{File={{Path="{export_path_normalized}", UVWProps=true, FBX=true}}, __UpdateUIObjFileName=true}})\n'
                )
            else:  # .obj
                script_parts.append(
                    f'ZomSave({{File={{Path="{export_path_normalized}", UVWProps=true}}, __UpdateUIObjFileName=true}})\n'
                )
        if "ZomQuit" not in user_script:
            script_parts.append("ZomQuit()\n")

        full_script = "".join(script_parts)
        print(f"Debug: Constructed full script:\n{full_script}")
        return full_script

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

    # =========================================================================
    # UV SCRIPT OPTIONS - Choose one based on your needs:
    # =========================================================================

    # OPTION 1: PACK EXISTING UVs
    # Use this when you already have good UVs and just want to pack them efficiently
    uv_script_pack_only = """
    -- Select all UV islands
    ZomSelect({PrimType="Island", Select=true, ResetBefore=true})
    
    -- Create island groups for better packing organization
    ZomIslandGroups({Mode="DistributeInTilesEvenly", MergingPolicy=8322, GroupPath="RootGroup"})
    
    -- Pack with high quality settings
    ZomPack({
        ProcessTileSelection=false, 
        RecursionDepth=2, 
        RootGroup="RootGroup", 
        Scaling={Mode=2}, 
        Rotate={Step=90}, 
        Translate=true, 
        LayoutScalingMode=2,
        Margin=2,
        Quality=1
    })
    """.strip()

    # OPTION 2: AUTO UNWRAP FOR HARD SURFACE OBJECTS
    # Use this for hard surface models that need automatic unwrapping
    uv_script_auto_unwrap = """
    -- Clear any existing seams and select all
    ZomSelect({PrimType="Edge", Select=true, ResetBefore=true})
    ZomClear({PrimType="Edge"})
    
    -- Auto-detect hard edges for seams (good for hard surface modeling)
    ZomSelect({
        PrimType="Edge", 
        Select=true, 
        ResetBefore=true, 
        Auto={
            HardEdge=true,          -- Detect hard edges automatically
            HandleCutter=true,      -- Handle cylindrical cuts
            PipesCutter=true,       -- Handle pipe-like geometry
            Skeleton={}             -- Basic skeleton detection
        },
        FilterAngle=30              -- Angle threshold for hard edges (30 degrees)
    })
    
    -- Mark selected edges as seams
    ZomCut({PrimType="Edge"})
    
    -- Unfold the UV islands with high quality settings
    ZomUnfold({
        PrimType="Edge", 
        MinAngle=1e-005, 
        Mix=1, 
        Iterations=3,               -- More iterations for better quality
        PreIterations=10,           -- More pre-iterations
        StopIfOutOFDomain=false, 
        RoomSpace=0.01,             -- Small room space for tight packing
        PinMapName="Pin", 
        ProcessNonFlats=true, 
        ProcessSelection=true, 
        ProcessAllIfNoneSelected=true, 
        ProcessJustCut=true, 
        BorderIntersections=true, 
        TriangleFlips=true
    })
    
    -- Optimize UV islands (straighten edges, etc.)
    ZomSelect({PrimType="Island", Select=true, ResetBefore=true})
    ZomOptimize({
        PrimType="Island",
        OptimizeStretch=true,
        OptimizeAngle=true,
        MinAngle=5,
        MaxIterations=50
    })
    
    -- Group islands for efficient packing
    ZomIslandGroups({
        Mode="DistributeInTilesEvenly", 
        MergingPolicy=8322, 
        GroupPath="RootGroup"
    })
    
    -- Pack with high quality settings
    ZomPack({
        ProcessTileSelection=false, 
        RecursionDepth=2,           -- Higher recursion for better packing
        RootGroup="RootGroup", 
        Scaling={Mode=2}, 
        Rotate={Step=90},           -- Allow 90-degree rotations
        Translate=true, 
        LayoutScalingMode=2,
        Margin=2,                   -- 2-pixel margin between islands
        Quality=1                   -- High quality packing
    })
    """.strip()

    # =========================================================================
    # CHOOSE YOUR SCRIPT:
    # =========================================================================

    # OPTION 3: MINIMAL TEST SCRIPT - for debugging
    # Simple script that should definitely work to test if RizomUV is functioning
    uv_script_minimal_test = """
    -- Select all polygons
    ZomSelect({PrimType="Polygon", Select=true, ResetBefore=true})
    
    -- Simple auto-unwrap using built-in seam detection
    ZomIslandGroups({Mode="DistributeInTilesEvenly", MergingPolicy=8322, GroupPath="RootGroup"})
    
    -- Basic pack operation
    ZomPack({ProcessTileSelection=false, RecursionDepth=1, RootGroup="RootGroup", Scaling={Mode=2}, Translate=true})
    """.strip()

    # For packing existing UVs only:
    # uv_script = uv_script_pack_only

    # For auto-unwrapping hard surface objects:
    # uv_script = uv_script_auto_unwrap

    # =========================================================================
    # IMPROVED SCRIPTS WITH EXPLICIT COMMANDS:
    # =========================================================================

    # NEW OPTION 1: SIMPLE UV PACK - Explicit step-by-step
    uv_script_simple_pack = """
    -- Select all UV islands explicitly
    ZomSelect({PrimType="Island", WorkingSet="", Select=true, ResetBefore=true})
    
    -- Simple pack operation with minimal settings
    ZomPack({
        ProcessTileSelection=false,
        RecursionDepth=1,
        Scaling={Mode=2},
        Rotate={Step=90},
        Translate=true,
        Margin=2
    })
    """.strip()

    # NEW OPTION 2: BASIC AUTO UNWRAP - Simplified for reliability
    uv_script_simple_unwrap = """
    -- Clear existing seams first
    ZomSelect({PrimType="Edge", Select=true, ResetBefore=true})
    ZomClear({PrimType="Edge"})
    
    -- Auto-select hard edges as seams with conservative settings
    ZomSelect({
        PrimType="Edge", 
        Select=true, 
        ResetBefore=true,
        Auto={HardEdge=true}
    })
    
    -- Cut the seams
    ZomCut({PrimType="Edge"})
    
    -- Unfold with basic settings
    ZomUnfold({
        PrimType="Edge", 
        MinAngle=1e-005, 
        Mix=1, 
        Iterations=1,
        PreIterations=3,
        StopIfOutOFDomain=false, 
        RoomSpace=0.01,
        ProcessAllIfNoneSelected=true
    })
    
    -- Select all islands and pack
    ZomSelect({PrimType="Island", Select=true, ResetBefore=true})
    ZomPack({
        ProcessTileSelection=false,
        RecursionDepth=1,
        Scaling={Mode=2},
        Rotate={Step=90},
        Translate=true,
        Margin=2
    })
    """.strip()

    # NEW OPTION 3: ULTRA MINIMAL - Just for testing if RizomUV works at all
    uv_script_ultra_minimal = """
-- Just select everything and pack - simplest possible operation
ZomSelect({PrimType="Island", Select=true, ResetBefore=true})
ZomPack({ProcessTileSelection=false, Translate=true})
""".strip()

    # NEW OPTION 4: ABSOLUTE MINIMAL - Test if RizomUV can load/save at all
    uv_script_load_save_only = """
-- Do absolutely nothing except load and save
""".strip()

    # For packing existing UVs only:
    uv_script = uv_script_simple_pack

    # For auto-unwrapping hard surface objects:
    # uv_script = uv_script_simple_unwrap

    # For ultra minimal testing (use this first to verify RizomUV is working):
    # uv_script = uv_script_ultra_minimal

    # For absolute minimal testing (just load and save):
    # uv_script = uv_script_load_save_only

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
