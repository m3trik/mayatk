import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ModuleNotFoundError as error:
    print(__file__, error)

# From this package:
from mayatk import NodeUtils, UvUtils
from mayatk.core_utils._core_utils import leaf_name, short_name, CoreUtils
from mayatk.env_utils.fbx_utils import FbxUtils
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
        # Mapping of exported (temporary suffixed) transform short names -> original transform str
        self._export_name_map = {}
        # Suffix applied to temporary duplicate nodes to avoid FBX re-import overwriting originals
        self._temp_suffix = "__RZTMP"
        # Per-run placeholder overrides (set by process_with_rizomuv)
        self._params: dict = {}

    @property
    def rizom_path(self):
        """Resolve the RizomUV executable path.

        If an explicit path was provided at init it is returned directly.
        Otherwise ``AppLauncher.find_app`` is queried for each candidate;
        as a final fallback we walk the standard Rizom Lab install dirs
        because the Rizom installer doesn't register the exe with the
        Windows ``App Paths`` registry key (so PATH/registry lookup fails
        even on a normal install).
        """
        if self._rizom_path:
            return self._rizom_path

        for name in _RIZOM_APP_NAMES:
            found = AppLauncher.find_app(name)
            if found:
                self._rizom_path = found  # cache for next call
                return found

        for found in self._scan_rizom_install_dirs():
            self._rizom_path = found
            return found
        return None

    @staticmethod
    def _scan_rizom_install_dirs():
        """Yield candidate Rizomuv_VS.exe paths under the standard install roots.

        Newest install (lexicographically last folder name -- e.g. "RizomUV
        2024.1" beats "RizomUV 2020.1") wins.
        """
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        for root in roots:
            rizom_lab = Path(root) / "Rizom Lab"
            if not rizom_lab.is_dir():
                continue
            for sub in sorted(rizom_lab.iterdir(), reverse=True):
                if not sub.is_dir():
                    continue
                for exe_name in ("Rizomuv_VS.exe", "rizomuv_RS.exe", "rizomuv.exe"):
                    candidate = sub / exe_name
                    if candidate.is_file():
                        yield str(candidate)

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

    def process_with_rizomuv(self, objects, uv_script=None, preset=None, params=None):
        """Run the full export -> RizomUV -> re-import workflow.

        The entire round-trip is wrapped in a Maya undo chunk so a single
        Ctrl+Z reverts the UV transfer, namespace creation, and any
        temporary duplicate cleanup. The external RizomUV invocation
        modifies a temp FBX on disk only -- nothing scene-state-relevant.

        Parameters:
            objects: Maya transform nodes to process.
            uv_script: Raw Lua string **or** path to a ``.lua`` file.
                       Mutually exclusive with *preset*.
            preset: Name of a built-in preset (``"pack"``, ``"unwrap"``,
                    ``"optimize"``).  The corresponding file is loaded
                    from ``scripts/<preset>.lua``. Mutually exclusive
                    with *uv_script*.
            params: Optional dict of placeholder overrides
                    (e.g. ``{"MARGIN": 0.005, "ITERATIONS": 25}``).
                    Keys map to ``__KEY__`` tokens in the script.
                    Unknown keys are passed through verbatim.
        """
        if not objects:
            raise ValueError("No objects specified for processing.")

        resolved = self._resolve_script(uv_script=uv_script, preset=preset)
        if resolved is not None:
            self.script_path = resolved

        self._params = params or {}

        chunk_name = f"RizomUV: {preset or 'script'}"
        with CoreUtils.undo_chunk(chunk_name):
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
        if cmds.namespace(exists=import_namespace):
            print(f"Debug: Removing existing namespace: {import_namespace}")
            cmds.namespace(removeNamespace=import_namespace, mergeNamespaceWithRoot=True)

        # Create a fresh namespace
        cmds.namespace(addNamespace=import_namespace)
        print(f"Debug: Created namespace: {import_namespace}")

        try:
            if file_ext == ".fbx":
                # Ensure FBX plugin is loaded first
                if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
                    print("Debug: Loading FBX plugin...")
                    cmds.loadPlugin("fbxmaya")

                print("Debug: Importing FBX using Maya file command...")

                # Use Maya's file command for reliable namespace import
                import_cmd = f'file -import -type "FBX" -ignoreVersion -mergeNamespacesOnClash false -namespace "{import_namespace}" -options "fbx" -pr "{self.export_path}";'
                print(f"Debug: Executing command: {import_cmd}")
                mel.eval(import_cmd)

                # Get all objects in the namespace - try different approaches
                imported_objs = cmds.ls(f"{import_namespace}:*", type="transform") or []
                print(f"Debug: Transform objects in namespace: {imported_objs}")

                # If no transforms found, check for any nodes in the namespace
                if not imported_objs:
                    all_namespace_nodes = cmds.ls(f"{import_namespace}:*") or []
                    print(f"Debug: All nodes in namespace: {all_namespace_nodes}")

                    # Try to find shapes and get their transforms
                    shape_nodes = cmds.ls(f"{import_namespace}:*", type="mesh") or []
                    if shape_nodes:
                        imported_objs = []
                        for shape in shape_nodes:
                            transforms = cmds.listRelatives(
                                shape, parent=True, type="transform"
                            ) or []
                            if transforms:
                                imported_objs.extend(transforms)
                        print(f"Debug: Transforms found from shapes: {imported_objs}")

                # If still no objects found in namespace, look for suffix objects anywhere
                if not imported_objs:
                    print(
                        f"Debug: No objects found in namespace, searching for suffix '{self._temp_suffix}' anywhere..."
                    )
                    all_transforms = cmds.ls(type="transform") or []
                    suffix_objects = [
                        t
                        for t in all_transforms
                        if leaf_name(t).endswith(self._temp_suffix)
                    ]
                    print(
                        f"Debug: Found {len(suffix_objects)} objects with suffix: {suffix_objects}"
                    )
                    imported_objs = suffix_objects

            else:  # .obj
                imported_objs = cmds.file(
                    self.export_path,
                    i=True,
                    namespace=import_namespace,
                    returnNewNodes=True,
                    type="OBJ",
                ) or []
                print(f"Debug: OBJ import returned: {imported_objs}")

        except Exception as e:
            print(f"Debug: Import failed: {e}")
            # Final fallback: try without namespace
            try:
                print("Debug: Trying import without namespace as final fallback...")
                existing_transforms = set(cmds.ls(type="transform") or [])

                if file_ext == ".fbx":
                    mel.eval(
                        f'file -import -type "FBX" -ignoreVersion -options "fbx" -pr "{self.export_path}";'
                    )
                else:
                    cmds.file(self.export_path, i=True, type="OBJ")

                new_transforms = set(cmds.ls(type="transform") or [])
                imported_objs = list(new_transforms - existing_transforms)

                # Filter to only those with our suffix
                suffix_objects = [
                    t for t in imported_objs if leaf_name(t).endswith(self._temp_suffix)
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
                dup = cmds.duplicate(orig, rr=True, ic=True)[0]
                new_name = f"{leaf_name(orig)}{self._temp_suffix}"
                dup = cmds.rename(dup, new_name)
                # Resolve to full DAG path so cmds.select can disambiguate when
                # two duplicates collapse to the same leaf name in different parents.
                dup_long = cmds.ls(dup, long=True) or []
                if dup_long:
                    dup = dup_long[0]
                duplicates.append(dup)
                # Store mapping using short (namespace-free) name
                self._export_name_map[short_name(new_name)] = orig
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

        cmds.select(duplicates, replace=True)
        print(
            f"Debug: Exporting {len(duplicates)} duplicated objects to: {self.export_path}"
        )

        # Live Maya sessions don't always have fbxmaya on by default;
        # cmds.file(type="FBX export") raises "Invalid file type" without it.
        FbxUtils.load_plugin()

        try:
            cmds.file(
                self.export_path,
                exportSelected=True,
                type="FBX export",
                force=True,
            )
            print("Debug: FBX export completed successfully")
        except Exception as e:
            raise RuntimeError(
                f"FBX export failed for {len(duplicates)} object(s) -> {self.export_path}: {e}"
            ) from e
        finally:
            # Remove the temporary duplicates from the scene before re-import
            try:
                cmds.delete(duplicates)
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

        # Execute RizomUV via AppLauncher.
        exe = self.rizom_path
        if not exe:
            raise RuntimeError(
                "RizomUV executable not found. Pass rizom_path= or add "
                "RizomUV to PATH."
            )

        # Snapshot the export file's pre-run state so we can verify RizomUV
        # actually wrote new UVs back to it. A non-zero exit, a Lua error
        # before ZomSave, or a license/license-server failure all leave the
        # file untouched -- detecting that here lets us raise a meaningful
        # error instead of silently re-importing the original UVs.
        pre_mtime = export_file.stat().st_mtime if export_file.exists() else 0
        pre_size = export_file.stat().st_size if export_file.exists() else 0

        print(f"Debug: Executing command: {exe} -cfi {self._script_path}")
        try:
            result = AppLauncher.run(
                exe,
                args=["-cfi", self._script_path],
                timeout=120,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                "RizomUV did not exit within 120s -- killed."
            ) from e
        except FileNotFoundError as e:
            raise RuntimeError(f"RizomUV executable not runnable: {e}") from e

        print(f"Debug: RizomUV return code: {result.returncode}")
        if result.stdout:
            print(f"Debug: RizomUV stdout:\n{result.stdout}")
        if result.stderr:
            print(f"Debug: RizomUV stderr:\n{result.stderr}")

        if result.returncode != 0:
            raise RuntimeError(
                f"RizomUV exited with code {result.returncode}. "
                f"See 'Debug: RizomUV stdout/stderr' above for the Lua "
                f"error or crash report."
            )

        if not export_file.exists():
            raise RuntimeError(
                f"RizomUV claimed success but the export file is gone: {export_file}"
            )

        post_mtime = export_file.stat().st_mtime
        post_size = export_file.stat().st_size
        print(
            f"Debug: Export file after RizomUV: {post_size} bytes "
            f"(mtime_changed={post_mtime != pre_mtime})"
        )
        if post_mtime == pre_mtime and post_size == pre_size:
            raise RuntimeError(
                "RizomUV exited cleanly but did not modify the FBX. The "
                "Lua script likely errored before reaching ZomSave -- "
                "check the stdout above for a Lua traceback."
            )

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
            short = short_name(imp)
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
        cmds.delete(imported_objects)
        cmds.namespace(removeNamespace="RizomUVImport", mergeNamespaceWithRoot=True)
        cmds.select(original_objects)
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

        from mayatk.uv_utils.rizom_bridge import parameters as _params

        export_path_normalized = str(self.export_path).replace("\\", "/")
        is_fbx = Path(self.export_path).suffix.lower() == ".fbx"

        # Resolve param values: registered defaults, then user overrides.
        merged = _params.defaults()
        merged.update(self._params or {})
        param_context = _params.render_context(merged)

        # User-script substitution happens first so its placeholders see the
        # resolved param values; the wrapper then sees the (already-substituted)
        # user_script as a single block.
        user_script = StrUtils.replace_delimited(user_script, param_context)

        wrapper = (_TEMPLATE_DIR / "wrapper.lua").read_text(encoding="utf-8")
        full_script = StrUtils.replace_delimited(wrapper, {
            "EXPORT_PATH": export_path_normalized,
            "FBX_FLAG": ", FBX=true" if is_fbx else "",
            "USER_SCRIPT": user_script,
        })

        try:
            print(f"Debug: Constructed full script:\n{full_script}")
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(
                "Debug: Constructed full script:\n"
                + full_script.encode(enc, errors="replace").decode(enc, errors="replace")
            )
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
    objects = cmds.ls(cmds.ls(selection=True) or [], type="transform") or []

    # Usage examples:
    #   bridge.process_with_rizomuv(objects, preset="pack")
    #   bridge.process_with_rizomuv(objects, preset="unwrap")
    #   bridge.process_with_rizomuv(objects, preset="minimal")
    #   bridge.process_with_rizomuv(objects, uv_script="ZomSelect(...)")
    bridge.process_with_rizomuv(objects, preset="pack")
