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
from pythontk.core_utils.app_launcher import AppLauncher
from pythontk.str_utils._str_utils import StrUtils

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"
_SCRIPT_DIR = _PKG_DIR / "scripts"


# Candidate names AppLauncher will try when no explicit path is given.
_RIZOM_APP_NAMES = ["Rizomuv_VS", "rizomuv", "RizomUV"]


class RizomUVBridge:
    def __init__(self, rizom_path=None):
        """Initialize the RizomUV bridge.

        Parameters:
            rizom_path: Explicit path to the RizomUV executable.
                If *None*, ``AppLauncher`` searches PATH / registry
                using the candidates in ``_RIZOM_APP_NAMES``.
        """
        self._rizom_path = rizom_path
        self._export_path = None  # Default to None, to be set during processing
        self._script_path = None  # Stores the path to the UV script file
        # Mapping of exported (temporary suffixed) transform short names -> original transform PyNode
        self._export_name_map = {}
        # Suffix applied to temporary duplicate nodes to avoid FBX re-import overwriting originals
        self._temp_suffix = "__RZTMP"

    @property
    def rizom_path(self):
        """Resolve the RizomUV executable path.

        If an explicit path was provided at init it is returned directly.
        Otherwise ``AppLauncher.find_app`` is queried for each candidate.
        """
        if self._rizom_path:
            return self._rizom_path

        for name in _RIZOM_APP_NAMES:
            found = AppLauncher.find_app(name)
            if found:
                self._rizom_path = found  # cache for next call
                return found
        return None

    @rizom_path.setter
    def rizom_path(self, value):
        """Set the path to the RizomUV executable (bypasses auto-discovery)."""
        self._rizom_path = value

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

    def process_with_rizomuv(self, objects, uv_script=None, preset=None):
        """Run the full export → RizomUV → re-import workflow.

        Parameters:
            objects: Maya transform nodes to process.
            uv_script: Raw Lua string **or** path to a ``.lua`` file.
                       Mutually exclusive with *preset*.
            preset: Name of a built-in preset (``"pack"``, ``"unwrap"``,
                    ``"minimal"``, ``"auto_uv"``).  The corresponding file
                    is loaded from ``scripts/<preset>.lua``.
                    Mutually exclusive with *uv_script*.
        """
        if not objects:
            raise ValueError("No objects specified for processing.")

        resolved = self._resolve_script(uv_script=uv_script, preset=preset)
        if resolved is not None:
            self.script_path = resolved

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

        # Execute RizomUV via AppLauncher
        exe = self.rizom_path
        if not exe:
            print("Debug: Error - RizomUV executable not found. "
                  "Pass rizom_path= or add RizomUV to your PATH.")
            return

        try:
            print(f"Debug: Executing command: {exe} -cfi {self._script_path}")
            result = AppLauncher.run(
                exe,
                args=["-cfi", self._script_path],
                timeout=120,
            )
            print(f"Debug: RizomUV return code: {result.returncode}")
            if result.stdout:
                print(f"Debug: RizomUV stdout: {result.stdout}")
            if result.stderr:
                print(f"Debug: RizomUV stderr: {result.stderr}")
        except subprocess.TimeoutExpired:
            print("Debug: RizomUV process timed out after 2 minutes")
        except FileNotFoundError as e:
            print(f"Debug: RizomUV not found: {e}")
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

    # -- Script resolution helpers -----------------------------------------

    @staticmethod
    def _resolve_script(uv_script=None, preset=None):
        """Return the Lua body to execute inside the wrapper.

        Accepts a raw string, a file path, or a preset name.  Returns the
        resolved Lua text (or *None* if nothing was supplied).
        """
        if uv_script and preset:
            raise ValueError("Provide either uv_script or preset, not both.")

        if preset:
            lua_path = _SCRIPT_DIR / f"{preset}.lua"
            if not lua_path.is_file():
                raise FileNotFoundError(
                    f"Preset '{preset}' not found.  "
                    f"Expected: {lua_path}\n"
                    f"Available: {[p.stem for p in _SCRIPT_DIR.glob('*.lua')]}"
                )
            return lua_path.read_text(encoding="utf-8")

        if uv_script is not None:
            p = Path(uv_script)
            if p.is_file():
                return p.read_text(encoding="utf-8")
            return uv_script  # raw Lua string

        return None

    def _construct_full_script(self, user_script):
        """Wrap *user_script* inside the ZomLoad / ZomSave / ZomQuit boilerplate.

        If the user script already contains ``ZomLoad`` / ``ZomSave`` /
        ``ZomQuit``, the wrapper is skipped and the script is returned as-is.
        """
        # If the script already handles its own load/save, pass it through
        if "ZomLoad" in user_script and "ZomSave" in user_script:
            print(f"Debug: User script contains ZomLoad/ZomSave; using as-is.")
            return user_script

        export_path_normalized = str(self.export_path).replace("\\", "/")
        is_fbx = Path(self.export_path).suffix.lower() == ".fbx"

        wrapper = (_TEMPLATE_DIR / "wrapper.lua").read_text(encoding="utf-8")
        full_script = StrUtils.replace_delimited(wrapper, {
            "EXPORT_PATH": export_path_normalized,
            "FBX_FLAG": ", FBX=true" if is_fbx else "",
            "USER_SCRIPT": user_script,
        })

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
    # No hardcoded path needed — AppLauncher discovers RizomUV automatically.
    # To override: RizomUVBridge(r"C:/Program Files/Rizom Lab/.../Rizomuv_VS.exe")
    bridge = RizomUVBridge()
    objects = pm.ls(pm.selected(), type="transform")

    # Usage examples:
    #   bridge.process_with_rizomuv(objects, preset="pack")
    #   bridge.process_with_rizomuv(objects, preset="unwrap")
    #   bridge.process_with_rizomuv(objects, preset="minimal")
    #   bridge.process_with_rizomuv(objects, uv_script="ZomSelect(...)")
    bridge.process_with_rizomuv(objects, preset="pack")
