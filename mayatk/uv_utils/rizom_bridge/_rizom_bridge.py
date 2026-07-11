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
# Install-dir fallback: the Rizom installer doesn't register the exe with the
# Windows App-Paths key, so PATH/registry lookup misses a normal install. Newest
# ``Rizom Lab\<version>`` folder wins (shared scan via ``AppLauncher.resolve_app_path``).
_RIZOM_SCAN_GLOBS = (
    r"{program_files}\Rizom Lab\*\Rizomuv_VS.exe",
    r"{program_files}\Rizom Lab\*\rizomuv_RS.exe",
    r"{program_files}\Rizom Lab\*\rizomuv.exe",
)

# Version segment inside a Rizom install-dir name. Anchored on a 4-digit
# year (every supported release is year-versioned) so it survives the
# naming variants: "RizomUV 2020.1", "RizomUV_2022", "RizomUV VS RS 2022.2".
_VERSION_RE = re.compile(r"(\d{4}(?:\.\d+)*)")


def _parse_rizom_version(exe_path) -> "tuple[int, ...]":
    """Parse ``(major, minor, ...)`` from *exe_path*'s install-dir name.

    Walks the path's parents looking for a folder whose name mentions
    Rizom and contains a year-anchored version. The result is padded to
    at least length 2 (``(2020, 1)`` / ``(2022, 0)``) so single-segment
    names still compare correctly against the ``(year, minor)`` gates in
    :data:`parameters.MIN_VERSIONS` -- Python's lexicographic tuple
    compare otherwise treats ``(2025,)`` as *less than* ``(2022, 0)``.

    Returns ``(0, 0)`` when nothing parses.
    """
    for parent in Path(exe_path).resolve().parents:
        if "rizom" not in parent.name.lower():
            continue
        matches = _VERSION_RE.findall(parent.name)
        if matches:
            parsed = tuple(int(p) for p in matches[-1].split("."))
            return parsed if len(parsed) >= 2 else parsed + (0,) * (2 - len(parsed))
    return (0, 0)


class RizomUVBridge(ptk.LoggingMixin):
    # Namespace the round-trip FBX is imported into; created fresh per run
    # and removed again during cleanup.
    _IMPORT_NAMESPACE = "RizomUVImport"

    def __init__(self, rizom_path=None, timeout=600):
        """Initialize the RizomUV bridge.

        Parameters:
            rizom_path: Explicit path to the RizomUV executable.
                If *None*, ``AppLauncher`` searches PATH / registry
                using the candidates in ``_RIZOM_APP_NAMES``.
            timeout: Max seconds to wait for the headless round-trip run
                before killing RizomUV. Simple meshes finish in seconds;
                dense meshes with high pack mutations can take minutes.
        """
        super().__init__()
        self._rizom_path = rizom_path
        self.timeout = timeout
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
        Otherwise discovery runs through the shared
        :meth:`pythontk.AppLauncher.resolve_app_path`: ``AppLauncher.find_app``
        for each candidate name, then a scan of the standard Rizom Lab install
        dirs (the installer doesn't register the exe with the Windows ``App
        Paths`` registry key, so PATH/registry lookup misses a normal install;
        newest ``Rizom Lab\\<version>`` folder wins).
        """
        if self._rizom_path:
            return self._rizom_path

        found = AppLauncher.resolve_app_path(
            app_names=_RIZOM_APP_NAMES,
            scan_globs=_RIZOM_SCAN_GLOBS,
        )
        if found:
            self._rizom_path = found  # cache for next call
        return found

    @rizom_path.setter
    def rizom_path(self, value):
        """Set the path to the RizomUV executable (bypasses auto-discovery)."""
        self._rizom_path = value

    @property
    def rizom_version(self) -> "tuple[int, ...]":
        """The installed Rizom version, parsed from the install-dir name.

        Delegates to :func:`_parse_rizom_version`; see there for the
        comparison semantics. Returns ``(0, 0)`` when no version can be
        extracted -- conservative choice that gates *every*
        version-flagged param off, matching what a fresh / unknown Rizom
        install would need anyway. A debug log is emitted so the user can
        tell why the panel might be missing knobs.
        """
        path = self.rizom_path
        if not path:
            self.logger.debug(
                "rizom_version: no executable resolved yet -> (0, 0)."
            )
            return (0, 0)
        version = _parse_rizom_version(path)
        if version == (0, 0):
            self.logger.debug(
                f"rizom_version: could not parse version from {path!r}; "
                f"gating all version-flagged params off -> (0, 0)."
            )
        return version

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
        # FBX only: the exporter, wrapper flags (UseUVSetNames) and the
        # namespace re-import are all FBX-shaped. The old '.obj' option was
        # a trap -- the export step always wrote FBX data regardless of the
        # extension, so an .obj path produced a file Rizom couldn't parse.
        if value and not value.lower().endswith(".fbx"):
            raise ValueError("The specified export path must end with '.fbx'")
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
                    (e.g. ``{"ITERATIONS": 25, "WELD_SEAMS": False}``).
                    Keys map to ``__KEY__`` tokens in the script (see
                    ``parameters.PARAMS`` for the registered set).
                    Unknown keys are passed through verbatim.
        """
        if not objects:
            raise ValueError("No objects specified for processing.")

        original_transforms = NodeUtils.get_transform_node(objects)
        if not original_transforms:
            raise ValueError("No valid transform nodes supplied for processing.")

        resolved = self._resolve_script(uv_script=uv_script, preset=preset)
        if resolved is not None:
            self.script_path = resolved

        self._params = params or {}

        chunk_name = f"RizomUV: {preset or 'script'}"
        with CoreUtils.undo_chunk(chunk_name):
            self._export_objects(original_transforms)
            self._execute_uv_script()

            # Directly work with transforms for imported objects for consistency
            imported_transforms = self._import_objects()
            self._transfer_uvs_and_cleanup(imported_transforms, original_transforms)

        self._announce_handoff(preset or "script", len(original_transforms))

    def _import_objects(self):
        """Import the RizomUV-processed FBX and return its transform nodes."""
        self.logger.debug(f"Importing objects from: {self.export_path}")

        import_namespace = self._IMPORT_NAMESPACE

        # Remove the namespace if it already exists to ensure clean import
        if cmds.namespace(exists=import_namespace):
            self.logger.debug(f"Removing existing namespace: {import_namespace}")
            cmds.namespace(removeNamespace=import_namespace, mergeNamespaceWithRoot=True)

        # Create a fresh namespace
        cmds.namespace(addNamespace=import_namespace)
        self.logger.debug(f"Created namespace: {import_namespace}")

        try:
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

        except Exception as e:
            self.logger.warning(f"Import failed: {e}")
            # Final fallback: try without namespace
            try:
                self.logger.debug("Trying import without namespace as final fallback...")
                existing_transforms = set(cmds.ls(type="transform") or [])

                mel.eval(
                    f'file -import -type "FBX" -ignoreVersion -options "fbx" -pr "{self.export_path}";'
                )

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
        """Export specified Maya objects to an FBX file after duplicating with a unique suffix.

        Strategy:
        1. Duplicate each original transform and append an indexed temp suffix
           so leaf names are globally unique -- two originals sharing a leaf
           name under different parents (``|grpA|mesh`` / ``|grpB|mesh``)
           would otherwise collapse to the same map key and cross-wire the
           UV transfer on re-import.
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
        for i, orig in enumerate(original_transforms):
            try:
                dup = cmds.duplicate(orig, rr=True, ic=True)[0]
                new_name = f"{leaf_name(orig)}_{i}{self._temp_suffix}"
                dup = cmds.rename(dup, new_name)
                # Resolve to full DAG path so cmds.select can disambiguate when
                # two duplicates collapse to the same leaf name in different parents.
                dup_long = cmds.ls(dup, long=True) or []
                if dup_long:
                    dup = dup_long[0]
                duplicates.append(dup)
                # Key on the name cmds.rename actually RETURNED, not the one
                # requested — a stale *__RZTMP survivor from a crashed run
                # makes Maya uniquify the rename (…RZTMP1), and a map keyed on
                # the request would silently skip that object's UV transfer
                # on re-import. Short (namespace-free) to match import-side
                # lookups.
                self._export_name_map[short_name(leaf_name(dup))] = orig
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
            user_script_content = Path(self._script_path).read_text(encoding="utf-8")
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

        self.logger.debug(f"Executing command: {exe} -cfi {self.script_path}")
        try:
            result = AppLauncher.run(
                exe,
                args=["-cfi", self.script_path],
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"RizomUV did not exit within {self.timeout}s -- killed. "
                f"For dense meshes, raise RizomUVBridge(timeout=...)."
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
            def tail(s, n=2048):
                return (s or "")[-n:].rstrip()

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
        """Transfer UVs from imported objects back to the original objects and clean up.

        Cleanup (delete imports, drop the import namespace, restore the
        selection) runs in a ``finally`` so a failed transfer never leaves
        the temporary import nodes in the scene.
        """
        self.logger.debug(
            f"Starting UV transfer: {len(imported_objects or [])} imported, "
            f"{len(original_objects or [])} original."
        )
        self.logger.debug(f"Imported objects: {imported_objects}")
        self.logger.debug(f"Original objects: {original_objects}")

        try:
            if not imported_objects or not original_objects:
                self.logger.warning("No objects to transfer UVs between!")
                return

            # Build ordered (source, destination) pairs using the export mapping
            pairs = []
            for imp in imported_objects:
                dst = self._export_name_map.get(short_name(imp))
                if dst is None:
                    self.logger.debug(
                        f"Imported object {imp} not found in export map; skipping."
                    )
                    continue
                pairs.append((imp, dst))

            if not pairs:
                self.logger.warning("No valid mapped object pairs for UV transfer.")
                return

            self.logger.info(f"Transferring UVs to {len(pairs)} object(s).")
            src_list = [s for s, _ in pairs]
            dst_list = [d for _, d in pairs]
            try:
                UvUtils.transfer_uvs(src_list, dst_list)
                self.logger.debug("Batch UV transfer completed successfully!")
            except Exception as batch_err:
                self.logger.warning(
                    f"Batch UV transfer failed ({batch_err}); attempting pairwise transfers..."
                )
                for s, d in pairs:
                    try:
                        UvUtils.transfer_uvs([s], [d])
                        self.logger.debug(f"Pairwise UV transfer success: {s} -> {d}")
                    except Exception as pair_err:
                        self.logger.error(
                            f"Pairwise UV transfer failed for {s} -> {d}: {pair_err}"
                        )
        finally:
            # Each step guarded individually -- a cleanup failure inside a
            # ``finally`` would otherwise mask the in-flight transfer error.
            self.logger.debug("Cleaning up imported objects...")
            if imported_objects:
                try:
                    cmds.delete(imported_objects)
                except Exception as cleanup_err:
                    self.logger.warning(
                        f"Failed to delete imported nodes: {cleanup_err}"
                    )
            try:
                if cmds.namespace(exists=self._IMPORT_NAMESPACE):
                    cmds.namespace(
                        removeNamespace=self._IMPORT_NAMESPACE,
                        mergeNamespaceWithRoot=True,
                    )
                if original_objects:
                    cmds.select(original_objects)
            except Exception as cleanup_err:
                self.logger.warning(f"Post-transfer cleanup failed: {cleanup_err}")
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

        If the user script already contains ``ZomLoad`` / ``ZomSave``, the
        wrapper is skipped -- but version-stripping and placeholder
        substitution still run, so a custom script can use the registered
        ``__KEY__`` tokens and stay safe on older Rizom.
        """
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

        # If the script handles its own load/save, pass it through (already
        # stripped + substituted above).
        if "ZomLoad" in user_script and "ZomSave" in user_script:
            self.logger.debug("User script contains ZomLoad/ZomSave; using as-is.")
            return user_script

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

    def _prepare_script_file(self, script_contents) -> Path:
        """Save the Lua script for RizomUV; returns (and stores) its Path.

        ``_script_path`` must stay a ``Path`` -- the public ``script_path``
        property calls ``.as_posix()`` on it.
        """
        script_path = Path(tempfile.gettempdir(), "riz_uv_script.lua")
        script_path.write_text(script_contents, encoding="utf-8")
        self._script_path = script_path
        return script_path

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

    # ------------------------------------------------------------------
    # One-way send (open in RizomUV without re-importing UVs)
    # ------------------------------------------------------------------

    def send_to_rizomuv(self, objects, params=None):
        """Export *objects* and open them in a fresh RizomUV session.

        One-way: RizomUV launches detached with the file loaded (and any
        collected textures bound via ``ZomLoadTexture``); Maya returns
        control immediately. The user saves manually inside RizomUV when
        they're done. No UV transfer back into the Maya scene.

        Distinct from :meth:`process_with_rizomuv` in four ways:

        * Uses ``templates/send_wrapper.lua`` (no ``ZomSave``/``ZomQuit``)
          so RizomUV stays open after the load script runs.
        * Skips the duplicate/suffix dance that the round-trip needs --
          we never re-import, so the FBX can carry the original names.
        * Launches RizomUV detached so Maya isn't blocked while the
          artist works in RizomUV.
        * Writes to **per-send unique** FBX + Lua paths. Rizom 2020.1's
          ``-cfi`` mode watches the script's mtime and re-executes on
          change; a fixed path would let a second send clobber a still-
          open earlier session. Each send gets its own files so prior
          sessions stay untouched.

        Parameters:
            objects: Maya transform nodes to export.
            params: Optional dict of overrides; recognized keys are
                ``LOAD_UVS``, ``LOAD_UVW_PROPS``, ``IMPORT_GROUPS``
                (substituted into the load wrapper as Lua booleans) and
                ``LOAD_TEXTURES`` (Python-side toggle controlling whether
                we scan the selection's shading networks and inject
                ``ZomLoadTexture`` calls into the load script).
        """
        if not objects:
            raise ValueError("No objects specified for sending.")

        original_transforms = NodeUtils.get_transform_node(objects)
        if not original_transforms:
            raise ValueError("No valid transform nodes supplied for sending.")

        self._params = params or {}

        # Per-send unique paths so prior Rizom sessions (which the -cfi
        # flag keeps watching via mtime) are not disturbed by a subsequent
        # send. Local variables -- intentionally not stored on self -- so
        # the round-trip flow's export_path / script_path state is also
        # untouched.
        send_tag = self._make_send_tag()
        send_fbx_path = self._make_send_fbx_path(send_tag)
        send_script_path = self._make_send_script_path(send_tag)

        self._export_for_send(original_transforms, send_fbx_path)

        send_script = self._construct_send_script(
            original_transforms, send_fbx_path
        )
        Path(send_script_path).write_text(send_script, encoding="utf-8")

        self.logger.info(
            f"Sending to RizomUV with script "
            f'<a href="action://open?path={send_script_path}">{send_script_path}</a>'
        )
        self.logger.debug(f"Send script content:\n{send_script}")

        exe = self.rizom_path
        if not exe:
            raise RuntimeError(
                "RizomUV executable not found. Pass rizom_path= or add "
                "RizomUV to PATH."
            )

        # Detached launch: Rizom stays open for the artist; Maya returns
        # control immediately. With ``-cfi`` Rizom runs the script on
        # startup and stays in the GUI (no ZomQuit means the session
        # doesn't terminate when the script finishes).
        proc = AppLauncher.launch(
            exe,
            args=["-cfi", send_script_path],
            detached=True,
        )
        if proc is None:
            raise RuntimeError(f"Failed to launch RizomUV: {exe}")

        self._announce_send(len(original_transforms))

    @staticmethod
    def _make_send_tag() -> str:
        """Compact unique suffix for per-send file paths.

        Nanosecond resolution -- two sends triggered back-to-back from
        the same Python interpreter are guaranteed distinct, and across
        Maya instances the collision window is effectively zero.
        """
        import time

        return f"{time.time_ns():x}"

    def _make_send_fbx_path(self, send_tag: str) -> str:
        """Return a unique-per-send FBX path under the export dir."""
        base = Path(self.export_path)
        return (base.parent / f"{base.stem}_send_{send_tag}{base.suffix}").as_posix()

    @staticmethod
    def _make_send_script_path(send_tag: str) -> str:
        """Return a unique-per-send Lua path under the system temp dir."""
        return Path(tempfile.gettempdir(), f"riz_send_{send_tag}.lua").as_posix()

    def _export_for_send(self, original_transforms, export_path):
        """Export *original_transforms* directly to FBX at *export_path*.

        The round-trip's :meth:`_export_objects` duplicates and suffixes
        each transform so the FBX re-import can't clobber the originals.
        One-way send never re-imports, so we skip the rename and write the
        FBX with the user's original node names -- nicer for the artist
        when they save out of RizomUV.
        """
        Path(export_path).parent.mkdir(parents=True, exist_ok=True)

        cmds.select(original_transforms, replace=True)
        self.logger.info(
            f"Exporting {len(original_transforms)} object(s) to "
            f'<a href="action://open?path={export_path}">{export_path}</a>'
        )

        # Live Maya sessions don't always have fbxmaya loaded by default.
        FbxUtils.load_plugin()

        try:
            cmds.file(
                export_path,
                exportSelected=True,
                type="FBX export",
                force=True,
            )
            self.logger.debug("FBX export completed successfully")
        except Exception as e:
            raise RuntimeError(
                f"FBX export failed for {len(original_transforms)} object(s) -> {export_path}: {e}"
            ) from e

    def _construct_send_script(self, original_transforms, export_path):
        """Render ``send_wrapper.lua`` with load options + texture loads.

        *export_path* is inlined into ``ZomLoad`` -- supplied explicitly
        (not pulled off ``self.export_path``) so each send rendering is
        bound to the per-send FBX it just wrote.
        """
        from mayatk.uv_utils.rizom_bridge import parameters as _params

        export_path_normalized = str(export_path).replace("\\", "/")
        is_fbx = Path(export_path).suffix.lower() == ".fbx"
        version = self.rizom_version

        # Resolve param values: registered defaults, then user overrides.
        merged = _params.defaults()
        merged.update(self._params or {})
        param_context = _params.render_context(merged)

        # ``LOAD_TEXTURES`` is a Python-side toggle (controls whether we
        # build the ZomLoadTexture block) -- the rendered Lua literal
        # would only ever land in the script's leading comment, so pop
        # it from the substitution context to avoid polluting it.
        load_textures = bool(merged.get("LOAD_TEXTURES", True))
        param_context.pop("LOAD_TEXTURES", None)

        texture_loads = ""
        if load_textures:
            texture_loads = self._collect_texture_loads(original_transforms)

        # Mirrors the round-trip wrapper's gating: the nested
        # FBX={UseUVSetNames=true} field only exists on newer Rizom; below
        # the gate, we emit an empty flag and let Rizom auto-detect the
        # format from the file extension.
        fbx_flag = (
            ", FBX={UseUVSetNames=true}"
            if is_fbx and version >= _params.FBX_USE_UV_SET_NAMES_MIN_VERSION
            else ""
        )

        wrapper = (_TEMPLATE_DIR / "send_wrapper.lua").read_text(encoding="utf-8")
        substitutions = {
            "EXPORT_PATH": export_path_normalized,
            "FBX_FLAG": fbx_flag,
            "TEXTURE_LOADS": texture_loads,
            **param_context,
        }
        full_script = StrUtils.replace_delimited(wrapper, substitutions)
        self.logger.debug(f"Constructed send script:\n{full_script}")
        return full_script

    def _collect_texture_loads(self, original_transforms):
        """Return Lua ``ZomLoadTexture`` calls for textures on *original_transforms*.

        Walks each transform's shading network via
        :meth:`mayatk.mat_utils.MatUtils.get_texture_paths`, drops paths
        that don't exist on disk (so a stale ``fileTextureName`` doesn't
        silently fail inside the ``pcall`` wrapper), and emits one
        ``ZomLoadTexture`` per remaining unique path. Each call is wrapped
        in ``pcall`` so an older Rizom that doesn't recognize the command
        fails soft -- the FBX still loads, just without textures.
        Returns the empty string when no textures resolve (degrades to a
        blank ``__TEXTURE_LOADS__`` substitution).
        """
        # Deferred so a missing/circular mat_utils import never blocks
        # the round-trip flow that doesn't need textures.
        try:
            from mayatk.mat_utils._mat_utils import MatUtils
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                f"Texture collection skipped (could not import MatUtils): {e}"
            )
            return ""

        try:
            paths = MatUtils.get_texture_paths(
                objects=original_transforms,
                absolute=True,
            )
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Texture collection failed: {e}")
            return ""

        # Order-preserving dedupe -- shared shading networks report the same
        # file once per assignment.
        unique_paths = list(dict.fromkeys(paths))
        existing = [p for p in unique_paths if p and os.path.isfile(p)]
        missing_count = len(unique_paths) - len(existing)
        if missing_count:
            self.logger.warning(
                f"Skipping {missing_count} texture(s) whose source files don't exist."
            )
        if not existing:
            self.logger.debug("No textures resolved for send-to-Rizom.")
            return ""

        self.logger.info(f"Binding {len(existing)} texture(s) in RizomUV.")
        lines = []
        for path in existing:
            normalized = str(path).replace("\\", "/")
            lines.append(
                f'pcall(function() ZomLoadTexture({{File={{Path="{normalized}"}}}}) end)'
            )
        return "\n".join(lines)

    def _announce_send(self, transform_count: int) -> None:
        """Log the one-way send summary (parallel to :meth:`_announce_handoff`)."""
        self.logger.info(
            f"Sent {transform_count} object(s) to RizomUV (interactive session)."
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
    #   bridge.send_to_rizomuv(objects)  # one-way: open in Rizom, no roundtrip
    bridge.process_with_rizomuv(objects, preset="pack")
