import os
import re
import subprocess
from pathlib import Path

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ModuleNotFoundError as error:
    print(__file__, error)

import pythontk as ptk

# From this package:
from mayatk import NodeUtils, UvUtils
from mayatk.core_utils._core_utils import CoreUtils
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


class _RizomUVBridgeInternal(object):
    """Internal helpers for RizomUVBridge."""

    @staticmethod
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


class RizomUVBridge(ptk.LoggingMixin, _RizomUVBridgeInternal):
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
        self._temp = None  # Round-trip temp store (see _temp_store)
        self._lua_path = None  # Store-allocated path the generated Lua is written to
        # Mapping of exported (temporary suffixed) transform short names -> original transform str
        self._export_name_map = {}
        # Suffix applied to temporary duplicate nodes to avoid FBX re-import overwriting originals
        self._temp_suffix = "__RZTMP"
        # UUIDs of the nodes the FBX re-import adds to the scene (transforms,
        # shapes AND the shading network it carries); emptied by cleanup.
        self._import_created: set = set()
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
            self.logger.debug("rizom_version: no executable resolved yet -> (0, 0).")
            return (0, 0)
        version = _RizomUVBridgeInternal._parse_rizom_version(path)
        if version == (0, 0):
            self.logger.debug(
                f"rizom_version: could not parse version from {path!r}; "
                f"gating all version-flagged params off -> (0, 0)."
            )
        return version

    @property
    def _temp_store(self) -> "ptk.TempArtifacts":
        """Lifecycle owner of the round-trip's temp FBX + Lua script.

        Both used to be FIXED ``%TEMP%`` names (``rizomuv_exported.fbx`` /
        ``riz_uv_script.lua``) shared by every process running a bridge -- a
        second Maya, the Blender twin (byte-identical names), and this repo's
        own test suite. RizomUV keeps re-reading the ``-cfi`` script *after*
        launch (the same mtime-watch behaviour the send flow designs around),
        so a concurrent run replacing the script mid-flight left RizomUV
        exiting 0 without ever reaching ``ZomSave`` -- reproduced, and the
        cause of a user-reported "exited cleanly but did not modify the FBX".

        ``"scoped"`` is this flow's exact shape: the run blocks until RizomUV
        exits, so a clean run deletes both payloads while a **failure keeps
        them** (logged) -- which is what makes the no-save error's "open the
        script in RizomUV's Script Editor" advice actionable. Allocation also
        age-sweeps same-prefix leftovers, so crashed runs can't accumulate.
        """
        if self._temp is None:
            # Prefer ~/temp when it exists -- the original export dir, kept
            # because it can have friendlier permissions than %TEMP%.
            home_temp = Path.home() / "temp"
            self._temp = ptk.TempArtifacts(
                "rizom_roundtrip",
                policy="scoped",
                dir=str(home_temp) if home_temp.exists() else None,
            )
        return self._temp

    def _release_temp_payloads(self) -> None:
        """Delete this run's temp payloads and forget the paths they used.

        Forgetting matters: ``cleanup`` *untracks* what it removed, so a
        second run through the same bridge (the panel keeps one per session)
        would rewrite the same now-untracked paths and never clean them
        again. Only the paths the store actually allocated are dropped -- an
        explicitly assigned ``export_path`` was never tracked, so it is never
        removed and never forgotten.
        """
        if self._temp is None:
            return
        removed = self._temp.cleanup()
        for attr in ("_export_path", "_lua_path"):
            path = getattr(self, attr)
            if path is not None and str(path) in removed:
                setattr(self, attr, None)

    @property
    def export_path(self):
        """Lazy initialization of the export path."""
        if self._export_path is None:
            self._export_path = Path(self._temp_store.path(extension=".fbx"))
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

    def process_with_rizomuv(
        self,
        objects,
        uv_script=None,
        preset=None,
        params=None,
        select_objects=None,
        skip_instances=True,
    ):
        """Run the full export -> RizomUV -> re-import workflow.

        One Ctrl+Z reverts the run, and reverts *only* the UV transfer: the
        scaffolding (temp duplicates, the FBX import, the throwaway
        namespace, and the nodes the import drags in) runs with undo
        recording off and is cleaned up explicitly instead.

        Recording the scaffolding is what used to break undo. ``file
        -import`` is NOT undoable while the ``cmds.delete`` that removes the
        imported nodes IS, so undoing the chunk resurrected the imported
        ``*__RZTMP`` transforms plus an empty ``RizomUVImport`` namespace and
        nothing ever removed them again -- reproduced, and the reason the
        two halves are now split. The external RizomUV invocation itself
        modifies a temp FBX on disk only -- nothing scene-state-relevant.

        Parameters:
            objects: Maya transform nodes to process.
            uv_script: Raw Lua string **or** path to a ``.lua`` file.
                       Mutually exclusive with *preset*.
            preset: Name of a built-in preset (``"pack"``, ``"unwrap_hard"``,
                    ``"unwrap_organic"``, ``"unwrap_hybrid"``, ``"optimize"``,
                    ``"pack_into_existing"``). The corresponding file is
                    loaded from ``scripts/<preset>.lua``. Mutually
                    exclusive with *uv_script*.
            params: Optional dict of placeholder overrides
                    (e.g. ``{"ITERATIONS": 25, "WELD_SEAMS": False}``).
                    Keys map to ``__KEY__`` tokens in the script (see
                    ``parameters.PARAMS`` for the registered set).
                    Unknown keys are passed through verbatim.
            select_objects: Subset of *objects* whose islands the script
                    should select in Rizom (rendered into the script's
                    ``PACK_SELECT_NAMES`` token as a Lua table of exported
                    island-group names). Required by presets that operate
                    on a sub-selection -- e.g. ``pack_into_existing`` packs
                    these objects' islands into the gaps left by the rest.
            skip_instances: When True (default), collapse true DAG instances
                    (transforms sharing one shape) to a single representative
                    before export. RizomUV would otherwise unwrap each copy
                    independently and the UV transfer back onto the shared
                    shape is last-write-wins -- wasteful and non-deterministic.
                    The transfer to the representative propagates to every
                    instance automatically (shared shape). Ignored when a
                    preset selects a sub-set of islands (the select-objects
                    mapping needs every named object exported).
        """
        from mayatk.uv_utils.rizom_bridge import parameters as _params

        if not objects:
            raise ValueError("No objects specified for processing.")

        original_transforms = NodeUtils.get_transform_node(objects)
        if not original_transforms:
            raise ValueError("No valid transform nodes supplied for processing.")

        resolved = self._resolve_script(uv_script=uv_script, preset=preset)
        if resolved is not None:
            self.script_path = resolved

        # Preset-level version gate (e.g. pack_into_existing needs the
        # ZomPack WorkingSet field, absent below 2022.2). Fails loudly
        # instead of letting an unsupported field no-op or crash Rizom.
        required = _params.Parameters.preset_min_version(resolved or "")
        if required and self.rizom_version < required:
            raise RuntimeError(
                f"Preset '{preset or 'script'}' requires RizomUV >= "
                f"{'.'.join(map(str, required))}; installed version is "
                f"{'.'.join(map(str, self.rizom_version))} ({self.rizom_path})."
            )

        # Presets that select a sub-set of islands need to know which
        # objects that is -- refuse to render a script whose selection
        # token would otherwise survive as a Lua syntax error.
        needs_selection = bool(resolved) and "__PACK_SELECT_NAMES__" in resolved
        if needs_selection and not select_objects:
            raise ValueError(
                f"Preset '{preset or 'script'}' operates on a sub-selection; "
                "pass select_objects= with the transforms whose islands "
                "should be packed."
            )

        # Collapse true DAG instances to one representative per shared shape
        # (unless a preset needs every named object exported for its
        # island-group selection). The UV transfer to the representative's
        # shape propagates to all instances -- see the docstring.
        if skip_instances and not needs_selection and len(original_transforms) > 1:
            deduped = NodeUtils.filter_duplicate_instances(original_transforms)
            if len(deduped) < len(original_transforms):
                self.logger.info(
                    f"Skipping {len(original_transforms) - len(deduped)} "
                    "instance(s): one representative per shared shape is "
                    "unwrapped; the result applies to all instances."
                )
                original_transforms = deduped

        self._params = params or {}

        chunk_name = f"RizomUV: {preset or 'script'}"
        with CoreUtils.undo_chunk(chunk_name):
            # Scaffolding: kept off the undo queue (see the docstring). The
            # export duplicates are created and deleted inside this block, so
            # nothing it touches outlives the call.
            with CoreUtils.undo_disabled():
                self._export_objects(original_transforms)
                if needs_selection:
                    self._params = dict(self._params)
                    self._params.setdefault(
                        "PACK_SELECT_NAMES", self._select_names_lua(select_objects)
                    )

            self._execute_uv_script()

            with CoreUtils.undo_disabled():
                # Directly work with transforms for imported objects for consistency
                imported_transforms = self._import_objects()
            self._transfer_uvs_and_cleanup(imported_transforms, original_transforms)

        self._announce_handoff(preset or "script", len(original_transforms))

        # The FBX has been consumed and its UVs are on the originals, so both
        # payloads can go. A raise above skips this on purpose: the scoped
        # policy keeps them (logged) so a failure stays debuggable -- the
        # no-save error tells the user to open that very script in RizomUV.
        self._release_temp_payloads()

    def _import_objects(self):
        """Import the RizomUV-processed FBX and return its transform nodes.

        Records every node the import brings into the scene (by UUID, which
        no rename or reparent later in the run can invalidate) for
        :meth:`_cleanup_import` to remove. Deleting the imported *transforms*
        is not enough: the FBX also carries its shading network, and the
        orphaned ``*__RZTMPSG`` shading groups used to accumulate one set per
        run.
        """
        self.logger.debug(f"Importing objects from: {self.export_path}")

        # The "before" snapshot stays local and the tracking set is cleared
        # up front: parking a whole-scene snapshot on the instance would mean
        # a run that dies mid-import leaves `_import_created` holding every
        # node in the scene, one stray cleanup call away from deleting it.
        self._import_created = set()
        before_import = set(cmds.ls(uuid=True) or [])

        import_namespace = self._IMPORT_NAMESPACE

        # Remove the namespace if it already exists to ensure clean import
        if cmds.namespace(exists=import_namespace):
            self.logger.debug(f"Removing existing namespace: {import_namespace}")
            cmds.namespace(
                removeNamespace=import_namespace, mergeNamespaceWithRoot=True
            )

        # Create a fresh namespace
        cmds.namespace(addNamespace=import_namespace)
        self.logger.debug(f"Created namespace: {import_namespace}")

        try:
            # Ensure FBX plugin is loaded first
            from mayatk.env_utils._env_utils import EnvUtils

            if not EnvUtils.is_plugin_loaded("fbxmaya"):
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
                        transforms = (
                            cmds.listRelatives(shape, parent=True, type="transform")
                            or []
                        )
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
                    if CoreUtils.leaf_name(t).endswith(self._temp_suffix)
                ]
                self.logger.debug(
                    f"Found {len(suffix_objects)} objects with suffix: {suffix_objects}"
                )
                imported_objs = suffix_objects

        except Exception as e:
            self.logger.warning(f"Import failed: {e}")
            # Final fallback: try without namespace
            try:
                self.logger.debug(
                    "Trying import without namespace as final fallback..."
                )
                existing_transforms = set(cmds.ls(type="transform") or [])

                mel.eval(
                    f'file -import -type "FBX" -ignoreVersion -options "fbx" -pr "{self.export_path}";'
                )

                new_transforms = set(cmds.ls(type="transform") or [])
                imported_objs = list(new_transforms - existing_transforms)

                # Filter to only those with our suffix
                suffix_objects = [
                    t
                    for t in imported_objs
                    if CoreUtils.leaf_name(t).endswith(self._temp_suffix)
                ]
                self.logger.debug(
                    f"Fallback without namespace found {len(suffix_objects)} suffix objects: {suffix_objects}"
                )
                imported_objs = suffix_objects

            except Exception as e2:
                self.logger.error(f"Final fallback also failed: {e2}")
                imported_objs = []

        # Everything the import added, whatever path got us here -- the
        # shading network included. Resolved back to names only at cleanup.
        self._import_created = set(cmds.ls(uuid=True) or []) - before_import

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
                new_name = f"{CoreUtils.leaf_name(orig)}_{i}{self._temp_suffix}"
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
                self._export_name_map[
                    CoreUtils.short_name(CoreUtils.leaf_name(dup))
                ] = orig
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
                "RizomUV executable not found. Pass rizom_path= or add RizomUV to PATH."
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
            raise RuntimeError(self._no_save_diagnosis(full_script_content))

    def _no_save_diagnosis(self, expected_script: str) -> str:
        """Explain a clean RizomUV exit that never reached ``ZomSave``.

        RizomUV writes nothing to stdout/stderr in ``-cfi`` mode, so there is
        no Lua traceback to enable -- the script itself is the only evidence.
        The first thing worth ruling out is a mid-run overwrite of that
        script (see :meth:`_process_tag` for why that used to happen and how
        the paths now prevent it), so compare what's on disk against what we
        handed RizomUV instead of guessing.
        """
        # Both rendered OS-native: ``export_path`` is a POSIX string (Rizom's
        # Lua wants forward slashes) and ``_script_path`` a Path, so quoting
        # them as-is mixed separators in one message the user has to act on.
        lines = [
            "RizomUV exited cleanly but never wrote the FBX -- the Lua "
            "script stopped before ZomSave.",
            f"Script: {Path(self._script_path)}",
            f"FBX:    {Path(self.export_path)}",
        ]
        try:
            on_disk = Path(self._script_path).read_text(encoding="utf-8")
        except OSError as e:
            lines.append(f"The script file is no longer readable: {e}")
        else:
            if on_disk != expected_script:
                lines.append(
                    "The script on disk no longer matches what the bridge "
                    "wrote -- another process replaced it while RizomUV was "
                    "running (RizomUV re-reads the -cfi file after launch). "
                    "Run one bridge at a time."
                )
        lines.append(
            "RizomUV prints no diagnostics in -cfi mode, so no amount of "
            "debug logging will surface the failing Lua line -- open the "
            "script above in RizomUV's Script Editor and run it there. "
            "Degenerate input UVs (every coordinate collapsed onto one "
            "point) are one known trigger."
        )
        return "\n".join(lines)

    def _transfer_uvs_and_cleanup(self, imported_objects, original_objects):
        """Transfer UVs onto the originals, then tear the import down.

        The transfer is the *only* part of the round-trip that belongs on the
        undo queue -- it is the user-visible result. Everything else (the
        imported nodes, the shading network they carry, the namespace) is
        scaffolding: torn down explicitly, undo-disabled, so a later Ctrl+Z
        can't resurrect it (see :meth:`process_with_rizomuv`).

        The transfer sources from *proxies* rather than the imported meshes
        themselves -- see :meth:`_make_undo_proxies` for why skipping that
        costs a hard crash.
        """
        # Everything from here runs under the ``finally``: the import has
        # already happened, so a failure while pairing or duplicating must
        # still tear it down rather than strand it in the scene.
        proxy_pairs = []
        try:
            pairs = self._pair_imports_with_originals(
                imported_objects, original_objects
            )
            with CoreUtils.undo_disabled():
                pairs = self._detach_sources(pairs)
            proxy_pairs = self._make_undo_proxies(pairs)
            self._transfer_uvs(proxy_pairs)
        finally:
            # Recorded, deliberately: undoing the transfer rebuilds its
            # history node and reconnects it to the source, so the source has
            # to be something undo can bring back. Deleting the proxies here
            # also keeps the whole recorded stretch contiguous, with the
            # undo-disabled teardown strictly after it.
            self._delete_nodes([p for p, _ in proxy_pairs], "undo proxy")
            with CoreUtils.undo_disabled():
                self._cleanup_import(original_objects)

    def _pair_imports_with_originals(self, imported_objects, original_objects):
        """Return ordered ``(imported, original)`` pairs via the export mapping."""
        self.logger.debug(
            f"Starting UV transfer: {len(imported_objects or [])} imported, "
            f"{len(original_objects or [])} original."
        )
        self.logger.debug(f"Imported objects: {imported_objects}")
        self.logger.debug(f"Original objects: {original_objects}")

        if not imported_objects or not original_objects:
            self.logger.warning("No objects to transfer UVs between!")
            return []

        pairs = []
        for imp in imported_objects:
            dst = self._export_name_map.get(CoreUtils.short_name(imp))
            if dst is None:
                self.logger.debug(
                    f"Imported object {imp} not found in export map; skipping."
                )
                continue
            pairs.append((imp, dst))

        if not pairs:
            self.logger.warning("No valid mapped object pairs for UV transfer.")
        return pairs

    def _make_undo_proxies(self, pairs):
        """Duplicate each imported mesh into an undo-RECORDED stand-in.

        ``transferAttributes`` + ``delete -ch`` leave an undo entry that
        rebuilds the history node and its connection back to the source mesh.
        If that source was created outside the undo queue -- which every
        ``file -import`` node is -- undoing the run reconnects to freed
        memory and Maya dies with an access violation (reproduced: a bare
        ``cmds.undo()`` after the transfer is a hard crash, not an error).

        Duplicating with recording ON gives the transfer a source that undo
        itself can restore, so the whole recorded chain is symmetric:
        duplicate -> transfer -> delete-history -> delete-proxy, all four
        undoable, referencing nothing the queue doesn't own.

        The proxy must be born clean, which is what :meth:`_detach_sources`
        is for -- a duplicate inherits its source's parent and shading
        assignment, both of which cleanup deletes. Fixing that up on the
        proxy afterwards is NOT an option: mutating an undo-recorded node
        with recording off silently corrupts the ``duplicate`` command's own
        undo entry, and the proxy then survives the undo as scene garbage
        (reproduced). Hence detach-then-duplicate, never duplicate-then-detach.

        Returns ``(proxy, original)`` pairs. An import that cannot be
        duplicated is SKIPPED rather than transferred from directly: that
        object keeps its old UVs, which the user can see and redo, whereas
        sourcing the transfer from an un-restorable node hands them a Maya
        that dies on the next Ctrl+Z.
        """
        proxy_pairs = []
        for imp, orig in pairs:
            try:
                proxy = cmds.duplicate(imp, returnRootsOnly=True)[0]
            except Exception as e:  # noqa: BLE001
                self.logger.error(
                    f"Could not create an undo proxy for {imp} ({e}); "
                    f"skipping the UV transfer onto {orig} -- transferring "
                    "from the imported node itself would make undoing this "
                    "run crash Maya."
                )
                continue
            proxy_pairs.append((proxy, orig))
        return proxy_pairs

    def _detach_sources(self, pairs):
        """Cut each imported transfer source loose from the import's world.

        A source carries two links that cleanup later deletes: the parent it
        was imported under, and the shading group the FBX brought with it.
        The proxy duplicated from it inherits both, and undoing that proxy's
        deletion reconnects them -- into freed memory, which is a hard crash
        (access violation in ``TdeleteCmd::undoIt``), not an error.

        Reparenting to world and forcing ``initialShadingGroup`` (a default
        node, never deleted) leaves the proxy standing only on nodes that
        outlive the run. Safe to do undo-disabled -- and it must be, because
        these are ``file -import`` nodes, which the undo queue never owned.

        ``relative=True`` on the reparent keeps the source's LOCAL transform
        instead of preserving its world position, so its object-space
        coordinates still line up with the original's. That only matters when
        the transfer falls back to an object-space sample (mismatched
        topology), but it costs nothing to keep exact.

        Returns the pairs with any renamed (reparented) sources updated.
        """
        detached = []
        for src, orig in pairs:
            try:
                if cmds.listRelatives(src, parent=True):
                    src = cmds.parent(src, world=True, relative=True)[0]
                for shape in cmds.listRelatives(src, shapes=True, fullPath=True) or []:
                    cmds.sets(shape, edit=True, forceElement="initialShadingGroup")
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"Could not detach import source {src}: {e}")
            detached.append((src, orig))
        return detached

    def _transfer_uvs(self, pairs):
        """Transfer UVs from each source mesh onto its paired original."""
        if not pairs:
            return

        self.logger.info(f"Transferring UVs to {len(pairs)} object(s).")
        src_list = [s for s, _ in pairs]
        dst_list = [d for _, d in pairs]
        # These pairs are already verified 1:1 correspondences (via
        # _export_name_map), not an unordered pile of meshes -- pass
        # match_by_similarity=False so transfer_uvs applies them
        # directly instead of re-deriving pairing from geometry, which
        # could reject a known-correct pair below `tolerance` or
        # cross-wire two pairs of similar/duplicate geometry.
        try:
            UvUtils.transfer_uvs(src_list, dst_list, match_by_similarity=False)
            self.logger.debug("Batch UV transfer completed successfully!")
        except Exception as batch_err:
            self.logger.warning(
                f"Batch UV transfer failed ({batch_err}); attempting pairwise transfers..."
            )
            for s, d in pairs:
                try:
                    UvUtils.transfer_uvs([s], [d], match_by_similarity=False)
                    self.logger.debug(f"Pairwise UV transfer success: {s} -> {d}")
                except Exception as pair_err:
                    self.logger.error(
                        f"Pairwise UV transfer failed for {s} -> {d}: {pair_err}"
                    )

    def _cleanup_import(self, original_objects):
        """Remove everything the FBX import brought in; restore the selection.

        Call inside :meth:`CoreUtils.undo_disabled` -- this is scaffolding
        teardown, not a user-visible edit.

        Two passes with different reach, in order. The UUID sweep does the
        real work: it covers every node the import created wherever it has
        since ended up, which node names no longer describe (the transfer
        sources get reparented to world, see :meth:`_detach_sources`).
        Dropping the namespace with ``deleteNamespaceContent`` then takes
        anything still inside it -- our own private namespace, so whatever
        is left there is ours by definition. That flag is the point:
        ``mergeNamespaceWithRoot`` (what this used to do) *rehomes* leftovers
        into the user's scene root instead, which is exactly how the orphaned
        ``*__RZTMPSG`` shading groups got loose in the outliner. The
        pre-import removal in :meth:`_import_objects` stays a merge on
        purpose -- that namespace is whatever was already in the scene, not
        ours to delete.
        """
        # Each step guarded individually -- a cleanup failure inside a
        # ``finally`` would otherwise mask the in-flight transfer error.
        self.logger.debug("Cleaning up imported objects...")
        self._delete_import_leftovers()

        try:
            if cmds.namespace(exists=self._IMPORT_NAMESPACE):
                cmds.namespace(
                    removeNamespace=self._IMPORT_NAMESPACE,
                    deleteNamespaceContent=True,
                )
            if original_objects:
                cmds.select(original_objects)
        except Exception as cleanup_err:
            self.logger.warning(f"Post-transfer cleanup failed: {cleanup_err}")
        self.logger.debug("Cleanup completed.")

    def _delete_import_leftovers(self) -> None:
        """Delete the still-present nodes recorded by :meth:`_import_objects`.

        The set is UUID-keyed, so it survives any rename or reparent the run
        inflicted, and it only ever contains nodes that did not exist before
        the import -- a material the FBX *reused* from the scene is not in it
        and is never touched. One ``ls`` resolves the whole set (it takes
        UUIDs exactly like names).
        """
        if not self._import_created:
            return
        try:
            self._delete_nodes(cmds.ls(list(self._import_created)) or [], "import")
        finally:
            self._import_created = set()

    def _delete_nodes(self, nodes, label: str) -> None:
        """Delete *nodes* as one batch, falling back to one at a time.

        The batch is the fast path AND the correct one: deleting a shading
        group can take its material with it, so a per-node loop would trip
        over nodes that are already gone. But a batch is all-or-nothing --
        one bad entry and every node in it survives -- so a failure retries
        individually rather than leaving the whole set behind. Undo state is
        the CALLER's business: this runs recorded or not exactly as the
        caller has it, and that distinction is load-bearing here (proxies
        must be recorded, import teardown must not be).
        """
        if not nodes:
            return
        self.logger.debug(f"Deleting {len(nodes)} {label} node(s).")
        try:
            cmds.delete(nodes)
        except Exception as batch_err:  # noqa: BLE001
            self.logger.debug(
                f"Batch delete of {len(nodes)} {label} node(s) failed "
                f"({batch_err}); retrying individually."
            )
            for node in nodes:
                try:
                    if cmds.objExists(node):
                        cmds.delete(node)
                except Exception as node_err:  # noqa: BLE001
                    self.logger.warning(f"Could not delete {label} {node}: {node_err}")

    def _select_names_lua(self, select_objects) -> str:
        """Render *select_objects* as a Lua table of exported group names.

        Must run after :meth:`_export_objects` -- it resolves each object
        through ``_export_name_map`` to the suffixed duplicate name the FBX
        (and therefore Rizom's imported island groups) actually carries.
        """
        sel_transforms = NodeUtils.get_transform_node(select_objects) or []
        sel_set = set(cmds.ls(sel_transforms, long=True) or [])
        names = [
            dup
            for dup, orig in self._export_name_map.items()
            if (cmds.ls(orig, long=True) or [str(orig)])[0] in sel_set
        ]
        if not names:
            raise ValueError(
                "select_objects did not match any exported object -- "
                "they must be a subset of the objects passed for processing."
            )
        return "{" + ", ".join(f'"{n}"' for n in names) + "}"

    @staticmethod
    def expand_by_materials(objects) -> "tuple[list[str], list[str]]":
        """Expand *objects* to every mesh sharing their assigned materials.

        Companion to the ``pack_into_existing`` preset: the caller selects
        only the NEW meshes; the full set Rizom needs (so the existing
        layout is present as the locked forbidden area) is every mesh that
        uses the same material(s) -- the material defines "the map".

        Returns ``(all_objects, selected_objects)`` as long names, where
        *selected_objects* is the normalized input (the pack subset).
        """
        # Deferred: mat_utils pulls in shading-network helpers the plain
        # round-trip flow never needs.
        from mayatk.mat_utils._mat_utils import MatUtils

        selected = cmds.ls(
            NodeUtils.get_transform_node(objects) or [], long=True
        ) or []
        expanded = set(selected)
        for mat in MatUtils.get_mats(selected, as_strings=True) or []:
            members = MatUtils.find_by_mat_id(mat, shell=True) or []
            expanded.update(cmds.ls(members, long=True) or [])
        return sorted(expanded), selected

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

        # Expand shared includes (__PACK_BLOCK__) so their lines participate
        # in version-stripping + param substitution below.
        user_script = _params.Parameters.expand_includes(user_script)

        # Strip lines referencing placeholders that the installed Rizom
        # doesn't support -- otherwise the unsupported field hits Rizom and
        # crashes the process (access violation on 2020.1, see MIN_VERSIONS
        # in parameters.py for the gate list).
        user_script = _params.Parameters.strip_unsupported(user_script, version)

        # Resolve param values: registered defaults, then user overrides.
        merged = _params.Parameters.defaults()
        merged.update(self._params or {})
        param_context = _params.Parameters.render_context(merged)

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
        full_script = StrUtils.replace_delimited(
            wrapper,
            {
                "EXPORT_PATH": export_path_normalized,
                "FBX_FLAG": fbx_flag,
                "USER_SCRIPT": user_script,
            },
        )

        self.logger.debug(f"Constructed full script:\n{full_script}")
        return full_script

    def _prepare_script_file(self, script_contents) -> Path:
        """Save the Lua script for RizomUV; returns (and stores) its Path.

        ``_script_path`` must stay a ``Path`` -- the public ``script_path``
        property calls ``.as_posix()`` on it.

        One store-allocated path per bridge, reused across the two writes a
        run makes (the raw preset via the ``script_path`` setter, then the
        wrapped script): the second must *replace* the first, and RizomUV is
        handed the path only after both have happened.
        """
        if self._lua_path is None:
            self._lua_path = Path(self._temp_store.path(extension=".lua"))
        self._lua_path.write_text(script_contents, encoding="utf-8")
        self._script_path = self._lua_path
        return self._lua_path

    def _announce_handoff(self, preset: str, transform_count: int) -> None:
        """Log the final success summary at the end of :meth:`process_with_rizomuv`.

        Mirrors :meth:`mayatk.mat_utils.substance_bridge.SubstanceBridge._announce_handoff`
        in spirit but kept terse: ``_export_objects`` and ``_execute_uv_script``
        already log the FBX + script paths as clickable links during the run.
        Re-linking them here would clutter the panel for a one-shot tool.
        """
        self.logger.info(f"RizomUV '{preset}' applied to {transform_count} object(s).")

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
        * Writes to **per-send unique** FBX + Lua paths (``ptk.TempArtifacts``,
          ``detached``). Rizom 2020.1's ``-cfi`` mode watches the script's
          mtime and re-executes on change; a fixed path would let a second
          send clobber a still-open earlier session. The round-trip uses the
          same primitive with the ``scoped`` policy (see :meth:`_temp_store`)
          -- a send can't be scoped, since the session it hands off to may
          outlive Maya and never signals completion.

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

        # Per-send unique paths so prior Rizom sessions (which the -cfi flag
        # keeps watching via mtime) are not disturbed by a subsequent send.
        # ``detached`` is the honest policy: the session may outlive us and
        # there is no completion signal, so nothing here may delete these --
        # allocation age-sweeps the same prefix instead, which is what keeps
        # a send-per-click habit from filling the temp dir forever. Local
        # stores, not kept on self, so the round-trip's export_path /
        # script_path state stays untouched.
        base = Path(self.export_path)
        send_fbx_path = Path(
            ptk.TempArtifacts(
                f"{base.stem}_send", policy="detached", dir=str(base.parent)
            ).path(extension=base.suffix)
        ).as_posix()
        send_script_path = Path(
            ptk.TempArtifacts("riz_send", policy="detached").path(extension=".lua")
        ).as_posix()

        self._export_for_send(original_transforms, send_fbx_path)

        send_script = self._construct_send_script(original_transforms, send_fbx_path)
        Path(send_script_path).write_text(send_script, encoding="utf-8")

        self.logger.info(
            f"Sending to RizomUV with script "
            f'<a href="action://open?path={send_script_path}">{send_script_path}</a>'
        )
        self.logger.debug(f"Send script content:\n{send_script}")

        exe = self.rizom_path
        if not exe:
            raise RuntimeError(
                "RizomUV executable not found. Pass rizom_path= or add RizomUV to PATH."
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
        merged = _params.Parameters.defaults()
        merged.update(self._params or {})
        param_context = _params.Parameters.render_context(merged)

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
