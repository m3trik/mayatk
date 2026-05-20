import os
import re
import subprocess
import tempfile
from pathlib import Path

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ModuleNotFoundError as error:
    print(__file__, error)

import pythontk as ptk

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


class RizomUVBridge(ptk.LoggingMixin):
    def __init__(self, rizom_path=None):
        """Initialize the RizomUV bridge.

        Parameters:
            rizom_path: Explicit path to the RizomUV executable.
                If *None*, ``AppLauncher`` searches PATH / registry
                using the candidates in ``_RIZOM_APP_NAMES``.
        """
        super().__init__()
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
    def rizom_version(self) -> "tuple[int, ...]":
        """Parse the Rizom version from the install directory name.

        Returns a ``(major, minor, patch, ...)`` tuple suitable for direct
        comparison with the gates in
        :data:`mayatk.uv_utils.rizom_bridge.parameters.MIN_VERSIONS`. The
        parsed tuple is padded to at least length 2 (``(2025, 0)`` rather
        than bare ``(2025,)``) so a single-segment install name still
        compares correctly against the registered ``(year, minor)`` gates
        -- Python's lexicographic tuple compare otherwise treats
        ``(2025,)`` as *less than* ``(2022, 0)``.

        Returns ``(0, 0)`` when no version can be extracted -- conservative
        choice that gates *every* version-flagged param off, matching what
        a fresh / unknown Rizom install would need anyway. A debug log is
        emitted so the user can tell why the panel might be missing knobs.
        """
        path = self.rizom_path
        if not path:
            self.logger.debug(
                "rizom_version: no executable resolved yet -> (0, 0)."
            )
            return (0, 0)
        for parent in Path(path).resolve().parents:
            m = re.search(
                r"RizomUV[\s_-]*(\d+(?:\.\d+)*)", parent.name, flags=re.IGNORECASE
            )
            if m:
                parsed = tuple(int(p) for p in m.group(1).split("."))
                # Pad to at least length 2 so '(2025,)' >= '(2022, 0)' works.
                return parsed if len(parsed) >= 2 else parsed + (0,) * (2 - len(parsed))
        self.logger.debug(
            f"rizom_version: could not parse version from {path!r}; "
            f"gating all version-flagged params off -> (0, 0)."
        )
        return (0, 0)

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
            preset: Name of a built-in preset (``"pack"``, ``"unwrap_hard"``,
                    ``"unwrap_organic"``, ``"optimize"``). The corresponding
                    file is loaded from ``scripts/<preset>.lua``. Mutually
                    exclusive with *uv_script*.
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

        self._announce_handoff(preset or "script", len(original_transforms))

    def _import_objects(self):
        """Updated to ensure transform nodes are returned."""
        self.logger.debug(f"Importing objects from: {self.export_path}")

        # Determine file type
        file_ext = Path(self.export_path).suffix.lower()

        # Ensure we have a unique namespace that doesn't conflict
        import_namespace = "RizomUVImport"

        # Remove the namespace if it already exists to ensure clean import
        if cmds.namespace(exists=import_namespace):
            self.logger.debug(f"Removing existing namespace: {import_namespace}")
            cmds.namespace(removeNamespace=import_namespace, mergeNamespaceWithRoot=True)

        # Create a fresh namespace
        cmds.namespace(addNamespace=import_namespace)
        self.logger.debug(f"Created namespace: {import_namespace}")

        try:
            if file_ext == ".fbx":
                # Ensure FBX plugin is loaded first
                if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
                    self.logger.debug("Loading FBX plugin...")
                    cmds.loadPlugin("fbxmaya")

                self.logger.debug("Importing FBX using Maya file command...")

                # Use Maya's file command for reliable namespace import
                import_cmd = f'file -import -type "FBX" -ignoreVersion -mergeNamespacesOnClash false -namespace "{import_namespace}" -options "fbx" -pr "{self.export_path}";'
                self.logger.debug(f"Executing command: {import_cmd}")
                mel.eval(import_cmd)

                # Get all objects in the namespace - try different approaches
                imported_objs = cmds.ls(f"{import_namespace}:*", type="transform") or []
                self.logger.debug(f"Transform objects in namespace: {imported_objs}")

                # If no transforms found, check for any nodes in the namespace
                if not imported_objs:
                    all_namespace_nodes = cmds.ls(f"{import_namespace}:*") or []
                    self.logger.debug(f"All nodes in namespace: {all_namespace_nodes}")

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
                        self.logger.debug(f"Transforms found from shapes: {imported_objs}")

                # If still no objects found in namespace, look for suffix objects anywhere
                if not imported_objs:
                    self.logger.debug(
                        f"No objects found in namespace, searching for suffix '{self._temp_suffix}' anywhere..."
                    )
                    all_transforms = cmds.ls(type="transform") or []
                    suffix_objects = [
                        t
                        for t in all_transforms
                        if leaf_name(t).endswith(self._temp_suffix)
                    ]
                    self.logger.debug(
                        f"Found {len(suffix_objects)} objects with suffix: {suffix_objects}"
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
                self.logger.debug(f"OBJ import returned: {imported_objs}")

        except Exception as e:
            self.logger.warning(f"Import failed: {e}")
            # Final fallback: try without namespace
            try:
                self.logger.debug("Trying import without namespace as final fallback...")
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
                self.logger.debug(
                    f"Fallback without namespace found {len(suffix_objects)} suffix objects: {suffix_objects}"
                )
                imported_objs = suffix_objects

            except Exception as e2:
                self.logger.error(f"Final fallback also failed: {e2}")
                imported_objs = []

        # Filter to get only transform nodes (already filtered for suffix above)
        imported_transforms = (
            NodeUtils.get_transform_node(imported_objs) if imported_objs else []
        )

        self.logger.debug(
            f"Final transform nodes (with suffix '{self._temp_suffix}'): {imported_transforms}"
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
                self.logger.warning(f"Failed to duplicate {orig}: {dup_err}")
        self.logger.debug(
            f"Created {len(duplicates)} duplicates for export with suffix '{self._temp_suffix}'"
        )

        if not duplicates:
            raise RuntimeError("Failed to create any duplicates for export.")

        # Ensure the export directory exists
        export_dir = Path(self.export_path).parent
        export_dir.mkdir(parents=True, exist_ok=True)

        cmds.select(duplicates, replace=True)
        self.logger.info(
            f"Exporting {len(duplicates)} object(s) to "
            f'<a href="action://open?path={self.export_path}">{self.export_path}</a>'
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
            self.logger.debug("FBX export completed successfully")
        except Exception as e:
            raise RuntimeError(
                f"FBX export failed for {len(duplicates)} object(s) -> {self.export_path}: {e}"
            ) from e
        finally:
            # Remove the temporary duplicates from the scene before re-import
            try:
                cmds.delete(duplicates)
                self.logger.debug("Deleted temporary duplicated export nodes.")
            except Exception as cleanup_err:
                self.logger.warning(f"Failed to delete duplicates: {cleanup_err}")

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

        self.logger.info(
            f"Running RizomUV with script "
            f'<a href="action://open?path={self._script_path}">{self._script_path}</a>'
        )
        self.logger.debug(f"Script content:\n{full_script_content}")
        self.logger.debug(f"Export file path: {self.export_path}")

        # Check if export file exists before RizomUV processing
        export_file = Path(self.export_path)
        if export_file.exists():
            self.logger.debug(
                f"Export file exists before RizomUV: {export_file.stat().st_size} bytes"
            )
        else:
            self.logger.warning("Export file does not exist before RizomUV!")

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

        self.logger.debug(f"Executing command: {exe} -cfi {self._script_path}")
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

        self.logger.debug(f"RizomUV return code: {result.returncode}")
        if result.stdout:
            self.logger.debug(f"RizomUV stdout:\n{result.stdout}")
        if result.stderr:
            self.logger.debug(f"RizomUV stderr:\n{result.stderr}")

        if result.returncode != 0:
            # Surface Rizom's actual error in the panel -- the bare exit code
            # is meaningless without it (e.g. 0xC00000FF = access violation
            # could be any of dozens of incompatible field names). Tail the
            # last 2 KB of each stream so a panicking Rizom that dumps MB of
            # crash text doesn't blow up the log.
            tail = lambda s, n=2048: (s or "")[-n:].rstrip()
            stdout_tail = tail(result.stdout)
            stderr_tail = tail(result.stderr)
            ver = self.rizom_version
            msg = [
                f"RizomUV exited with code {result.returncode} "
                f"(version detected: {ver}, script: {self._script_path})."
            ]
            if stdout_tail:
                msg.append(f"--- stdout (tail) ---\n{stdout_tail}")
            if stderr_tail:
                msg.append(f"--- stderr (tail) ---\n{stderr_tail}")
            if not stdout_tail and not stderr_tail:
                msg.append(
                    "(RizomUV produced no captured output -- the process "
                    "likely crashed before flushing. Try running the script "
                    "manually in RizomUV's Script Editor to see the failing "
                    "line.)"
                )
            raise RuntimeError("\n".join(msg))

        if not export_file.exists():
            raise RuntimeError(
                f"RizomUV claimed success but the export file is gone: {export_file}"
            )

        post_mtime = export_file.stat().st_mtime
        post_size = export_file.stat().st_size
        self.logger.debug(
            f"Export file after RizomUV: {post_size} bytes "
            f"(mtime_changed={post_mtime != pre_mtime})"
        )
        if post_mtime == pre_mtime and post_size == pre_size:
            raise RuntimeError(
                "RizomUV exited cleanly but did not modify the FBX. The "
                "Lua script likely errored before reaching ZomSave -- "
                "enable debug logging to see the Lua traceback."
            )

    def _transfer_uvs_and_cleanup(self, imported_objects, original_objects):
        """Transfer UVs from imported objects back to the original objects and clean up."""
        self.logger.debug("Starting UV transfer...")
        self.logger.debug(f"Imported objects: {imported_objects}")
        self.logger.debug(f"Original objects: {original_objects}")
        self.logger.debug(
            f"Number of imported: {len(imported_objects) if imported_objects else 0}"
        )
        self.logger.debug(
            f"Number of original: {len(original_objects) if original_objects else 0}"
        )

        if not imported_objects or not original_objects:
            self.logger.warning("No objects to transfer UVs between!")
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
                self.logger.debug(
                    f"Imported object {imp} not found in export map; skipping."
                )

        self.logger.info(
            f"Transferring UVs to {len(dst_list)} object(s)."
        )

        if not src_list or not dst_list:
            self.logger.warning("No valid mapped object pairs for UV transfer.")
        else:
            # Attempt a batch transfer if lengths match
            if len(src_list) == len(dst_list):
                try:
                    self.logger.debug("Attempting batch UV transfer...")
                    UvUtils.transfer_uvs(src_list, dst_list)
                    self.logger.debug("Batch UV transfer completed successfully!")
                except Exception as batch_err:
                    self.logger.warning(
                        f"Batch UV transfer failed ({batch_err}); attempting pairwise transfers..."
                    )
                    for s, d in zip(src_list, dst_list):
                        try:
                            UvUtils.transfer_uvs([s], [d])
                            self.logger.debug(f"Pairwise UV transfer success: {s} -> {d}")
                        except Exception as pair_err:
                            self.logger.error(
                                f"Pairwise UV transfer failed for {s} -> {d}: {pair_err}"
                            )
            else:
                self.logger.warning(
                    "Source/Destination list length mismatch; skipping batch transfer."
                )
                for s, d in zip(src_list, dst_list):
                    try:
                        UvUtils.transfer_uvs([s], [d])
                        self.logger.debug(f"Pairwise UV transfer success: {s} -> {d}")
                    except Exception as pair_err:
                        self.logger.error(
                            f"Pairwise UV transfer failed for {s} -> {d}: {pair_err}"
                        )

        self.logger.debug("Cleaning up imported objects...")
        cmds.delete(imported_objects)
        cmds.namespace(removeNamespace="RizomUVImport", mergeNamespaceWithRoot=True)
        cmds.select(original_objects)
        self.logger.debug("Cleanup completed.")

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
            self.logger.debug("User script contains ZomLoad/ZomSave; using as-is.")
            return user_script

        from mayatk.uv_utils.rizom_bridge import parameters as _params

        export_path_normalized = str(self.export_path).replace("\\", "/")
        is_fbx = Path(self.export_path).suffix.lower() == ".fbx"
        version = self.rizom_version

        # Strip lines referencing placeholders that the installed Rizom
        # doesn't support -- otherwise the unsupported field hits Rizom and
        # crashes the process (access violation on 2020.1, see MIN_VERSIONS
        # in parameters.py for the gate list).
        user_script = _params.strip_unsupported(user_script, version)

        # Resolve param values: registered defaults, then user overrides.
        merged = _params.defaults()
        merged.update(self._params or {})
        param_context = _params.render_context(merged)

        # User-script substitution happens first so its placeholders see the
        # resolved param values; the wrapper then sees the (already-substituted)
        # user_script as a single block.
        user_script = StrUtils.replace_delimited(user_script, param_context)

        # FBX={UseUVSetNames=true} (nested table) preserves Maya's UV-set
        # name across the round-trip; the bare ``FBX=true`` form is silently
        # dropped, leaving the round-trip on a generic set name that the
        # UV-transfer step can't find. Both forms only exist on newer Rizom
        # (see FBX_USE_UV_SET_NAMES_MIN_VERSION); pre-2022 just relies on
        # file-extension auto-detect and works fine with an empty flag.
        fbx_flag = (
            ", FBX={UseUVSetNames=true}"
            if is_fbx and version >= _params.FBX_USE_UV_SET_NAMES_MIN_VERSION
            else ""
        )

        wrapper = (_TEMPLATE_DIR / "wrapper.lua").read_text(encoding="utf-8")
        full_script = StrUtils.replace_delimited(wrapper, {
            "EXPORT_PATH": export_path_normalized,
            "FBX_FLAG": fbx_flag,
            "USER_SCRIPT": user_script,
        })

        self.logger.debug(f"Constructed full script:\n{full_script}")
        return full_script

    def _prepare_script_file(self, script_contents):
        """Prepare and save the Lua script file for RizomUV, returning the file path."""
        script_filename = Path(tempfile.gettempdir(), "riz_uv_script.lua").as_posix()
        with open(script_filename, "w") as file:
            file.write(script_contents)
        # Convert to a Path object and then get a POSIX-style string
        self._script_path = script_filename
        return script_filename

    def _announce_handoff(self, preset: str, transform_count: int) -> None:
        """Log the final success summary at the end of :meth:`process_with_rizomuv`.

        Mirrors :meth:`mayatk.mat_utils.substance_bridge.SubstanceBridge._announce_handoff`
        in spirit but kept terse: ``_export_objects`` and ``_execute_uv_script``
        already log the FBX + script paths as clickable links during the run.
        Re-linking them here would clutter the panel for a one-shot tool.
        """
        self.logger.info(
            f"RizomUV '{preset}' applied to {transform_count} object(s)."
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # No hardcoded path needed — AppLauncher discovers RizomUV automatically.
    # To override: RizomUVBridge(r"C:/Program Files/Rizom Lab/.../Rizomuv_VS.exe")
    bridge = RizomUVBridge()
    objects = cmds.ls(cmds.ls(selection=True) or [], type="transform") or []

    # Usage examples:
    #   bridge.process_with_rizomuv(objects, preset="pack")
    #   bridge.process_with_rizomuv(objects, preset="unwrap_hard")
    #   bridge.process_with_rizomuv(objects, preset="unwrap_organic")
    #   bridge.process_with_rizomuv(objects, preset="optimize")
    #   bridge.process_with_rizomuv(objects, uv_script="ZomSelect(...)")
    bridge.process_with_rizomuv(objects, preset="pack")
