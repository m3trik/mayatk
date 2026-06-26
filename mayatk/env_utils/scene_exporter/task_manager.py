# !/usr/bin/python
# coding=utf-8
import os
import re
import math
from typing import Optional, Dict, Any, List

try:
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    mel = None
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils.scene_exporter.task_factory import TaskFactory
from mayatk.env_utils.hierarchy_manager.hierarchy_sidecar import HierarchySidecar


class _TaskDataMixin:
    """ """

    @property
    def _has_keyframes(self) -> bool:
        """Check if the current objects have keyframes."""
        if hasattr(self, "_key_times"):
            return bool(self._key_times)
        return bool(self._get_all_keyframes())

    def _get_all_keyframes(self) -> List[float]:
        """Return a sorted list of all unique keyframe times for the specified objects.

        Delegates to ``AnimUtils.get_keyframe_times`` for the actual query and
        caches the result set in ``_key_times`` for downstream consumers.
        """
        if not self.objects:
            return []

        # Filter to objects that still exist (smart_bake may delete
        # constraints/expressions, removing nodes from the scene).
        existing = cmds.ls(self.objects, long=True) or []
        if not existing:
            return []

        times = AnimUtils.get_keyframe_times(existing)
        if times is None:
            return []

        self._key_times = set(times)
        return times

    def _get_all_materials(self) -> List[str]:
        """Return a list of all materials assigned to the specified objects.

        Results are cached per export run. The cache is invalidated when
        ``objects`` is reassigned via ``_initialize_objects``.
        """
        if not hasattr(self, "_cached_materials") or self._cached_materials is None:
            self._cached_materials = MatUtils.filter_materials_by_objects(
                self.objects, as_strings=True
            )
        return self._cached_materials

    def _get_export_file_nodes(self) -> List[str]:
        """Return the deduplicated ``file`` nodes feeding the export materials.

        Walks the shading history of the materials assigned to ``self.objects``
        (filtering any an earlier task may have deleted) and collects the
        connected ``file`` texture nodes.  Shared by the texture-oriented tasks
        and checks so they all scope to exactly the textures that will ship,
        rather than every ``file`` node in the scene.
        """
        materials = [m for m in self._get_all_materials() if cmds.objExists(m)]
        if not materials:
            return []
        history = cmds.listHistory(materials, pruneDagObjects=True) or []
        return list(set(cmds.ls(history, type="file") or []))


class _TaskActionsMixin(_TaskDataMixin):
    """ """

    def set_workspace(self, enable=True):
        """Manage temporary workspace change."""
        original_workspace = cmds.workspace(query=True, rootDirectory=True)

        if enable:
            new_workspace = EnvUtils.find_workspace_using_path()
            if new_workspace and new_workspace != original_workspace:
                cmds.workspace(new_workspace, openWorkspace=True)
                self.logger.debug(
                    f"Changed workspace from {original_workspace} to {new_workspace}"
                )
            elif not new_workspace:
                self.logger.warning(
                    "No workspace.mel found in scene path hierarchy "
                    f"\u2014 using current workspace: {original_workspace}"
                )
            else:
                self.logger.debug("Workspace already matches scene path.")

        return original_workspace

    def revert_workspace(self, original_workspace):
        """Revert to the original workspace."""
        cmds.workspace(original_workspace, openWorkspace=True)
        self.logger.debug(f"Reverted workspace to: {original_workspace}")

    def set_linear_unit(self, linear_unit):
        """Manage temporary linear unit change."""
        original_linear_unit = cmds.currentUnit(query=True, linear=True)

        if linear_unit and linear_unit != "OFF":
            cmds.currentUnit(linear=linear_unit)
            self.logger.debug(
                f"Changed linear unit from {original_linear_unit} to {linear_unit}"
            )
        else:
            self.logger.debug(f"Linear unit change skipped (value: {linear_unit})")

        return original_linear_unit

    def revert_linear_unit(self, original_linear_unit):
        """Revert to the original linear unit."""
        cmds.currentUnit(linear=original_linear_unit)
        self.logger.debug(f"Reverted linear unit to: {original_linear_unit}")

    def convert_to_relative_paths(self):
        """Copy external textures into sourceimages, then convert paths to relative.

        A project-relative texture path only resolves if the file physically
        lives under ``sourceimages``.  Any texture stored elsewhere is first
        copied in (via ``MatUtils.copy_textures_to_sourceimages``); without
        that step, remapping it to a relative path would point at a file that
        isn't there and silently break the material on import.  Textures
        already under sourceimages are left in place.

        The copy is a real, persistent asset consolidation — it intentionally
        survives the post-export scene restore (the perform_export undo chunk
        only rolls back the node *path* edits below, not the files on disk).
        """
        self.logger.debug("Converting absolute paths to relative")
        materials = self._get_all_materials()

        # Stage the actual files under sourceimages before remapping so the
        # resulting relative paths resolve instead of breaking the links.
        copied = MatUtils.copy_textures_to_sourceimages(materials=materials)
        if copied:
            self.logger.info(
                f"Copied {len(copied)} external texture(s) into sourceimages "
                "before relative-path conversion."
            )

        # Pass silent=True and as_strings=True to avoid om.MGlobal.displayInfo
        # and cmds.node overhead.  Do NOT disable undo here — the
        # perform_export undo chunk needs to capture these changes so
        # the scene can be restored after export.
        MatUtils.remap_texture_paths(materials, silent=True, as_strings=True)
        self.logger.debug("Path conversion completed.")

    def reassign_duplicate_materials(self):
        """Reassign duplicate materials in the scene."""
        self.logger.debug("Reassigning duplicate materials")
        materials = self._get_all_materials()
        MatUtils.reassign_duplicate_materials(materials, delete=True)
        # Invalidate the materials cache since duplicates were deleted
        self._cached_materials = None
        self.logger.debug("Reassignment completed.")

    def resolve_invalid_texture_paths(self):
        """Attempt to resolve missing texture paths using workspace and sourceimages lookup.

        Scoped to materials assigned to the export objects. Uses
        ``MatUtils.resolve_path`` which checks env-var expansion,
        workspace-relative resolution, sourceimages directory, and
        basename-in-sourceimages as fallbacks.
        """
        file_nodes = self._get_export_file_nodes()
        if not file_nodes:
            self.logger.debug(
                "No export texture file nodes found. Skipping texture path resolution."
            )
            return

        resolved_count = 0
        unresolved = []

        for node in file_nodes:
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                continue

            expanded = os.path.expandvars(path)
            # Handle UDIM patterns
            check_path = (
                expanded.replace("<UDIM>", "1001") if "<UDIM>" in expanded else expanded
            )
            if os.path.exists(check_path):
                continue  # Path is already valid

            resolved = MatUtils.resolve_path(path)
            if resolved:
                cmds.setAttr(f"{node}.fileTextureName", resolved, type="string")
                resolved_count += 1
                self.logger.info(f"Resolved texture: {node} -> {resolved}")
            else:
                unresolved.append(f"{node} -> {path}")

        if resolved_count:
            self.logger.info(f"Resolved {resolved_count} broken texture path(s).")
        if unresolved:
            self.logger.warning(
                f"{len(unresolved)} texture path(s) could not be resolved:"
            )
            for entry in unresolved:
                self.logger.warning(f"  {entry}")
        if not resolved_count and not unresolved:
            self.logger.debug("All texture paths are valid.")

    def smart_bake(self):
        """Pre-bake constrained and driven channels before export.

        Uses SmartBake to detect objects with constraints, driven keys,
        expressions, IK, motion paths, and blend shapes, then bakes only
        those specific channels onto an override animation layer.
        FBX export with FBXExportBakeComplexAnimation samples the final
        evaluated output, so the override layer produces correct results
        without deleting driver nodes.  After export, the layer is deleted
        to restore the original scene state non-destructively.
        """
        from mayatk.anim_utils.smart_bake import SmartBake

        self.logger.info("Analyzing scene for bake requirements...")
        baker = SmartBake(
            objects=self.objects,
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=False,  # Handled by the separate optimize_keys task in _task_config()
            use_override_layer=True,  # Non-destructive: bake to override layer
            delete_inputs=False,  # Keep constraints — layer overrides them
        )

        analysis = baker.analyze()
        if not any(a.requires_bake for a in analysis.values()):
            self.logger.info(
                "No constrained/driven objects found. Skipping smart bake."
            )
            return

        # Log what will be baked
        bake_count = sum(1 for a in analysis.values() if a.requires_bake)
        self.logger.info(f"Found {bake_count} objects requiring bake.")

        result = baker.bake(analysis)

        # Store layer names and curves for cleanup after export
        if result.override_layer:
            self._bake_override_layer = result.override_layer
        # Build detailed log message
        log_parts = [
            f"Smart bake completed: {result.baked_count} objects baked",
            f"range {result.time_range[0]}-{result.time_range[1]}",
        ]
        if result.override_layer:
            log_parts.append(f"layer '{result.override_layer}'")
        if result.optimized:
            log_parts.append(f"{len(result.optimized)} objects optimized")

        self.logger.info(", ".join(log_parts) + ".")

        # Refresh self.objects (no deletions expected, but re-validate)
        self.objects = cmds.ls(self.objects, long=True) or []

        # Invalidate keyframe cache since we added new keys
        if hasattr(self, "_key_times"):
            delattr(self, "_key_times")

    def optimize_keys(self):
        """Optimize baked animation keys."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping optimization.")
            return

        self.logger.info("Optimizing baked animation keys...")
        # Optimizes base-layer curves only.  Override-layer curves from
        # smart_bake are optimized internally by SmartBake.optimize_keys.
        AnimUtils.optimize_keys(self.objects, recursive=True, quiet=True)
        self.logger.info("Optimization completed.")

    def set_bake_animation_range(self):
        """Set the animation export range to the first and last keyframes of the specified objects if baking is enabled."""
        all_keyframes = self._get_all_keyframes()
        if not all_keyframes:
            self.logger.debug("No keyframes found. Skipping frame range setting.")
            return

        if not mel.eval("FBXExportBakeComplexAnimation -q"):
            self.logger.info(
                "Baking complex animation is disabled. Skipping frame range setting."
            )
            return

        first_key, last_key = all_keyframes[0], all_keyframes[-1]
        mel.eval(f"FBXExportBakeComplexStart -v {int(first_key)}")
        mel.eval(f"FBXExportBakeComplexEnd -v {int(last_key)}")

        self.logger.info(
            f"Set animation range to start: {int(first_key)}, end: {int(last_key)}"
        )

    def tie_all_keyframes(self):
        """Use AnimUtils to tie all keyframes for the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping tie operation.")
            return

        self.logger.info("Tying keyframes for all objects.")

        # Optimization: Pass cached keyframe range to avoid re-querying
        custom_range = None
        if hasattr(self, "_key_times") and self._key_times:
            # _key_times is a set, need to sort it to get min/max
            sorted_times = sorted(self._key_times)
            custom_range = (sorted_times[0], sorted_times[-1])

        AnimUtils.tie_keyframes(self.objects, absolute=True, custom_range=custom_range)
        self.logger.info("Keyframes have been tied.")

    def snap_keys_to_frame(self):
        """Snap all keyframes to the nearest whole frame."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping snap operation.")
            return

        self.logger.info("Snapping keyframes to nearest whole frame.")
        AnimUtils.snap_keys_to_frames(self.objects)
        self.logger.info("Keyframes have been snapped.")

    def create_glb(self, fbx_path: Optional[str] = None, announce: bool = True):
        """Convert an exported FBX to a GLB sidecar via pythontk's MeshConvert.

        Runs after the FBX has been written; ``perform_export`` invokes this
        explicitly rather than as part of the pre-export task pipeline.

        Parameters:
            fbx_path: FBX to convert. Defaults to ``self.export_path`` (the
                FBX-alongside case). The GLB-only path passes the temp FBX so the
                ``.glb`` lands beside it (then gets moved into the output dir).
            announce: When True, log the resulting path. The GLB-only path sets
                this False and logs the final (moved) path itself.

        Returns:
            The created ``.glb`` path, or ``None`` if conversion failed.
        """
        self.logger.info("Converting FBX to GLB...")
        try:
            glb_path = ptk.MeshConvert.fbx_to_glb(
                fbx_path or self.export_path,
                overwrite=True,
                auto_install=True,
                prompt=False,
            )
        except (FileNotFoundError, RuntimeError) as e:
            self.logger.error(f"GLB conversion failed: {e}")
            return None

        if announce:
            self.logger.success(f"GLB created: {glb_path}")
        return glb_path

    def export_data_node(self):
        """Include the shared ``data_export`` carrier in the export (default on).

        ``data_export`` is the single hidden node every metadata system stamps
        (Shots → ``shot_metadata`` + ``fbx_takes``; Audio → ``audio_manifest``;
        …).  Because it's hidden, the ``visible`` / ``selected`` export modes
        omit it and the metadata silently wouldn't ship.  This refreshes the
        carrier from the live producers, then appends it to the export set so the
        data rides into the FBX regardless of export mode — independent of any
        one subsystem, so a scene with only audio still carries its manifest.
        """
        self._refresh_scene_data_node()
        self._include_data_export_node()
        self._log_data_node_summary()

    def _log_data_node_summary(self):
        """Log what metadata actually shipped on ``data_export``.

        Makes a silently-empty export distinguishable from a populated one — the
        single most useful signal that the carrier reached the FBX with content.
        Reads the channels generically; no-ops when the carrier is absent.  Pure
        logging convenience — fully best-effort so it can never abort the export.
        """
        try:
            import json
            from mayatk.node_utils.data_nodes import DataNodes

            if not cmds.objExists(DataNodes.EXPORT):
                return

            parts = []
            meta_raw = DataNodes.get_export_string(DataNodes.SHOT_METADATA)
            if meta_raw:
                n_shots = len(json.loads(meta_raw).get("shots", []))
                if n_shots:
                    parts.append(f"{n_shots} shot(s)")

            # ``audio_manifest`` is the Audio channel's wire name (whitespace-
            # joined ``frame:label`` events); read directly to stay decoupled.
            if cmds.attributeQuery("audio_manifest", node=DataNodes.EXPORT, exists=True):
                manifest = cmds.getAttr(f"{DataNodes.EXPORT}.audio_manifest") or ""
                n_audio = len(manifest.split())
                if n_audio:
                    parts.append(f"{n_audio} audio event(s)")

            if parts:
                self.logger.info("Embedded on data_export: " + ", ".join(parts) + ".")
        except Exception:  # a summary must never break the export it describes
            self.logger.debug("data_export summary skipped.", exc_info=True)

    def _include_data_export_node(self):
        """Append the ``data_export`` carrier to the export set.

        Idempotent: a no-op when the node is absent (nothing to ship) or already
        in the set.  Shared by :meth:`export_data_node` and
        :meth:`apply_declared_takes`.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        if not cmds.objExists(DataNodes.EXPORT):
            self.logger.debug("No data_export node in scene — nothing to include.")
            return
        export_node = cmds.ls(DataNodes.EXPORT, long=True)[0]
        if export_node not in (self.objects or []):
            self.objects = list(self.objects or []) + [export_node]
            self.logger.info("data_export carrier added to the export set.")

    def _refresh_scene_data_node(self):
        """Refresh ``data_export`` channels from the live metadata producers.

        Each producer no-ops when it has nothing to write (no shots / no audio
        carrier), so a metadata-free scene leaves no node behind.  Guarded per
        producer so an absent or erroring subsystem never blocks the export.
        """
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            ShotStore.refresh_export_view()
        except Exception:
            self.logger.debug("Shots data-node refresh skipped.", exc_info=True)
        try:
            from mayatk.audio_utils.audio_clips._audio_clips import AudioClips

            AudioClips.prepare_for_export()
        except Exception:
            self.logger.debug("Audio data-node refresh skipped.", exc_info=True)

    def apply_declared_takes(self):
        """Export each shot as a named Unity clip, plus embed shot metadata.

        Publishes the active shot store's export view onto the shared
        ``data_export`` node, ensures that node is in the export selection (so
        the metadata rides along inside the FBX), then realizes the declared
        takes into FBX export state.  The apply step is shot-agnostic — it acts
        on whatever takes the scene declares.  Runs after
        ``set_bake_animation_range`` so its union range wins.
        """
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.env_utils.fbx_utils import FbxUtils

        store = ShotStore.active()
        if not store.shots:
            self.logger.debug("No shots defined. Skipping animation takes.")
            return

        # Republish so the channels reflect the live store, then make sure the
        # carrier node travels with the selection.
        store.publish_export_view()
        self._include_data_export_node()

        count = FbxUtils.apply_takes_from_node()
        self.logger.info(
            f"Animation takes: {count} clip(s) from {len(store.shots)} shot(s); "
            "shot metadata embedded on data_export."
        )


class _TaskChecksMixin(_TaskDataMixin):
    """ """

    _LOD_SUFFIX_REGEX = re.compile(r"_lod\d*$", re.IGNORECASE)
    _MAX_LISTED_OBJECTS = 25
    _DEFAULT_FLOOR_TOLERANCE = 0.5

    def _obj_link(self, node: str, action: str = "reveal") -> str:
        """Return a clickable log link for a Maya scene node.

        Parameters:
            node:   Full or short DAG path (used as both label and param).
            action: ``"select"`` or ``"reveal"`` (default).
        """
        short = node.rsplit("|", 1)[-1]
        return self.logger.log_link(short, action, node=node)

    def _truncate_obj_entries(
        self, entries: List[str], limit: Optional[int] = None
    ) -> List[str]:
        """Cap per-object log entries with a summary tail when the list is long.

        Returns the entries unchanged when ``len(entries) <= limit``; otherwise
        returns the first ``limit`` entries followed by ``"... and N more (omitted)"``.
        """
        cap = self._MAX_LISTED_OBJECTS if limit is None else limit
        if len(entries) <= cap:
            return entries
        remaining = len(entries) - cap
        return entries[:cap] + [f"... and {remaining} more (omitted)"]

    def check_geometry_lod_suffix(self) -> tuple:
        """Check for geometry whose names end with '_LOD' or '_LOD' followed by digits.

        Returns:
            tuple: (status: bool, messages: list)

        Notes:
            - This check is informational. It returns True regardless, and lists any matches.
            - Suffix examples matched: '_LOD', '_LOD0', '_LOD1', '_LOD02', etc. (case-insensitive)
        """
        messages: List[str] = []

        if not self.objects:
            return True, messages

        matches = {}
        for obj in self.objects:
            # Check if geometry (has shapes)
            # Use cmds for speed
            shapes = cmds.listRelatives(obj, shapes=True)
            if not shapes:
                continue

            name = obj.split("|")[-1]
            if self._LOD_SUFFIX_REGEX.search(name):
                matches.setdefault(name, obj)

        if matches:
            messages.append("Geometry with LOD suffix detected (informational):")
            for n in sorted(matches):
                link = self._obj_link(matches[n], "reveal")
                messages.append(f"  - {link}")

        return True, messages

    def ignore_groups(self, names: str) -> None:
        """Exclude top-level groups matching *names* (case-insensitive) and all
        their descendants from the export object list.

        Parameters:
            names: Comma-separated group names to exclude (e.g. ``"temp, proxy"``).
        """
        if not self.objects or not names:
            return

        # Parse comma-separated names, strip whitespace, lowercase for matching
        target_names = {n.strip().lower() for n in names.split(",") if n.strip()}
        if not target_names:
            return

        # Find top-level groups whose short name matches any target
        root_nodes = cmds.ls(self.objects, assemblies=True, long=True) or []
        matched_roots = [
            node for node in root_nodes if node.split("|")[-1].lower() in target_names
        ]

        if not matched_roots:
            self.logger.debug(f"No top-level groups matching {target_names} found.")
            return

        # Gather the matched roots and all their descendants
        exclude = set(matched_roots)
        for root in matched_roots:
            descendants = (
                cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
            )
            exclude.update(descendants)

        original_count = len(self.objects)
        self.objects = [obj for obj in self.objects if obj not in exclude]
        removed = original_count - len(self.objects)

        for root in matched_roots:
            self.logger.info(f"Ignoring group: {root}")
        self.logger.info(
            f"Excluded {removed} object(s) under {len(matched_roots)} group(s) from export."
        )

    def exclude_hdr(self) -> None:
        """Remove Arnold HDR environment lights (``aiSkyDomeLight``) from the export set.

        The HDR skydome is image-based scene lighting, not deliverable
        geometry, so it should not ride into a game-engine FBX. In the
        'All Scene Objects' mode the skydome transform is otherwise picked up
        by ``cmds.ls(transforms=True)``; this strips the skydome transform(s)
        and their shapes back out of ``self.objects``.

        A no-op when mtoa is unloaded (no skydome can exist) or the export set
        contains none.
        """
        if not self.objects:
            return

        # Guard the plugin first: querying ``cmds.ls(type="aiSkyDomeLight")``
        # for an unregistered type emits an "Unknown object type" warning, and
        # without mtoa loaded no skydome can exist anyway.
        try:
            if not cmds.pluginInfo("mtoa", query=True, loaded=True):
                return
        except Exception:
            return

        skydomes = cmds.ls(type="aiSkyDomeLight", long=True) or []
        if not skydomes:
            return

        exclude = set()
        for shape in skydomes:
            exclude.add(shape)
            exclude.update(
                cmds.listRelatives(shape, parent=True, fullPath=True) or []
            )

        original_count = len(self.objects)
        self.objects = [obj for obj in self.objects if obj not in exclude]
        removed = original_count - len(self.objects)
        if removed:
            self.logger.info(
                f"Excluded {removed} HDR environment node(s) (aiSkyDomeLight) from export."
            )
        else:
            self.logger.debug("No HDR skydome in the export set — nothing to exclude.")

    def check_root_default_transforms(self) -> tuple:
        """Check if all root group nodes have default transforms."""
        log_messages = []
        box_logged = False
        tolerance = 1e-5
        has_non_default_transforms = False

        # self.objects contains only geometry transforms (never assemblies),
        # so we walk up each object's DAG path to find the root ancestor.
        root_groups = set()
        for obj in self.objects:
            # Long path: "|root|child|...|geo" — the root is segment [1]
            parts = obj.split("|")
            if len(parts) > 2:
                root_long = "|" + parts[1]
                root_groups.add(root_long)

        root_nodes = cmds.ls(list(root_groups), long=True) or []

        for node in root_nodes:
            if not NodeUtils.is_group(node):
                continue

            translate = cmds.getAttr(f"{node}.translate")[0]
            rotate = cmds.getAttr(f"{node}.rotate")[0]
            scale = cmds.getAttr(f"{node}.scale")[0]

            if (
                not all(abs(val) < tolerance for val in translate)
                or not all(abs(val) < tolerance for val in rotate)
                or not all(abs(val - 1) < tolerance for val in scale)
            ):
                if not box_logged:
                    log_messages.append(
                        f"Root level group nodes found with non-default transforms:"
                    )
                    box_logged = True

                has_non_default_transforms = True
                link = self._obj_link(node)
                log_messages.append(
                    f"Node: {link}, Translate: {translate}, Rotate: {rotate}, Scale: {scale}"
                )

        if has_non_default_transforms:
            return (
                False,
                log_messages,
            )  # Failed, log the nodes with non-default transforms

        return True, log_messages  # All checks passed, no non-default transforms

    def check_absolute_paths(self) -> tuple:
        """Check if any absolute material paths are present in the scene."""
        all_relative = True
        log_messages = []

        materials = self._get_all_materials()
        material_paths = MatUtils.collect_material_paths(
            materials,
            inc_mat_name=True,
            inc_path_type=True,
            nested_as_unit=True,
        )

        for mat, typ, pth in material_paths:
            if typ == "Absolute":
                all_relative = False
                mat_name = str(mat).split("|")[-1].split(":")[-1]
                link = self._obj_link(mat_name, "select")
                log_messages.append(f"Absolute path - {link} - {pth}")

        return all_relative, log_messages

    def check_valid_paths(self) -> tuple:
        """Check if all file paths (textures, references, etc.) exist on disk."""
        import os

        # We can accept relative paths if they resolve relative to project
        log_messages = []
        all_valid = True

        # 1. Texture Paths
        # Use cmds to avoid nodes
        file_nodes = cmds.ls(type="file") or []
        for node in file_nodes:
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                # Some empty file nodes might exist?
                continue

            expanded_path = os.path.expandvars(path)

            # If absolute check directly
            if os.path.isabs(expanded_path):
                if not os.path.exists(expanded_path):
                    all_valid = False
                    link = self._obj_link(node, "select")
                    log_messages.append(f"Missing Texture: {link} -> {path}")
            else:
                # If relative, try to resolve
                workspace_root = cmds.workspace(query=True, rootDirectory=True)

                # Check common relative locations
                possible_paths = [
                    os.path.join(workspace_root, expanded_path),
                    os.path.join(workspace_root, "sourceimages", expanded_path),
                    os.path.abspath(expanded_path),  # Relative to current working dir
                ]

                found = False
                for p in possible_paths:
                    if os.path.exists(p):
                        found = True
                        break

                if not found:
                    all_valid = False
                    link = self._obj_link(node, "select")
                    log_messages.append(f"Missing Texture (Relative): {link} -> {path}")

        # 2. Reference Paths
        references = cmds.ls(references=True) or []
        for ref in references:
            try:
                # withoutCopyNumber=True gets actual file path
                path = cmds.referenceQuery(ref, filename=True, withoutCopyNumber=True)
                if path:
                    expanded_path = os.path.expandvars(path)
                    if not os.path.exists(expanded_path):
                        all_valid = False
                        link = self._obj_link(ref, "select")
                        log_messages.append(f"Missing Reference: {link} -> {path}")
            except Exception:
                continue

        if all_valid:
            log_messages.append("All checked paths exist on disk.")

        return all_valid, log_messages

    def check_texture_file_size(self, max_size_mb: Optional[float] = 16.0) -> tuple:
        """Check that no export texture exceeds a maximum on-disk file size.

        Oversized source textures bloat the exported asset and usually signal an
        un-downsized authoring map (e.g. an 8K master) that shouldn't ship to a
        game engine.  Scoped to the textures feeding the export materials, so it
        only flags maps that will actually travel with the FBX.

        Parameters:
            max_size_mb: Maximum allowed texture size in megabytes.  ``None``,
                ``0``, or ``"OFF"`` disables the check (returns pass).  Defaults
                to 16 MB.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not max_size_mb or str(max_size_mb).upper() == "OFF":
            return True, []

        try:
            limit_mb = float(max_size_mb)
        except (TypeError, ValueError):
            self.logger.warning(
                f"Invalid max texture size '{max_size_mb}'. Skipping size check."
            )
            return True, []
        limit_bytes = limit_mb * 1024 * 1024

        offenders: List[str] = []
        seen_paths = set()

        for node in self._get_export_file_nodes():
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                continue

            # Resolve to the on-disk file via MatUtils.resolve_path so
            # project-relative paths still resolve — the default-on
            # convert_to_relative_paths task runs before checks and rewrites
            # texture paths to workspace-relative form.  Missing files are the
            # domain of check_valid_paths, so an unresolved path is skipped.
            resolved = MatUtils.resolve_path(path)
            if not resolved:
                continue

            # Collapse the UDIM token to the first tile for the size probe
            # (resolve_path may return a path that still carries it).
            probe = (
                resolved.replace("<UDIM>", "1001") if "<UDIM>" in resolved else resolved
            )
            if probe in seen_paths:
                continue
            seen_paths.add(probe)

            if not os.path.isfile(probe):
                continue

            size = os.path.getsize(probe)
            if size > limit_bytes:
                link = self._obj_link(node, "select")
                offenders.append(
                    f"  - {link} -> {os.path.basename(probe)} "
                    f"({size / (1024 * 1024):.2f} MB)"
                )

        if offenders:
            header = [f"{len(offenders)} texture(s) exceed the {limit_mb:g} MB limit:"]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []

    def check_duplicate_locator_names(self) -> tuple:
        """Check for duplicate locator short names among the specified objects.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages = []
        # Use cmds for speed
        # Get all shapes of type locator from self.objects (which are transforms)
        locator_shapes = (
            cmds.listRelatives(self.objects, shapes=True, type="locator", fullPath=True)
            or []
        )
        if not locator_shapes:
            return True, log_messages

        locator_transforms = (
            cmds.listRelatives(locator_shapes, parent=True, fullPath=True) or []
        )

        seen = {}
        duplicates = set()
        for loc in locator_transforms:
            name = loc.split("|")[-1]
            if name in seen:
                duplicates.add(name)
            else:
                seen[name] = loc

        if duplicates:
            for name in sorted(duplicates):
                # Short names may be ambiguous; link uses the first full path
                full_path = seen.get(name, name)
                link = self._obj_link(full_path, "reveal")
                log_messages.append(f"Duplicate locator name: {link}")
            return False, log_messages
        return True, log_messages

    def check_duplicate_materials(self) -> tuple:
        """Check if any duplicate materials are present in the scene."""
        log_messages = []

        materials = self._get_all_materials()
        duplicate_mapping = MatUtils.find_materials_with_duplicate_textures(materials)

        if duplicate_mapping:
            for original, duplicates in duplicate_mapping.items():
                for duplicate in duplicates:
                    dup_link = self._obj_link(str(duplicate), "select")
                    orig_link = self._obj_link(str(original), "select")
                    log_messages.append(f"Duplicate: {dup_link} -> {orig_link}")
            return False, log_messages  # Failed, log the duplicates

        return True, log_messages  # All checks passed, no duplicates found

    def check_referenced_objects(self) -> tuple:
        """Check if any referenced objects are present in the scene."""
        log_messages = []
        # Check all referenced objects in the scene, not just the selected objects
        referenced_objects = cmds.ls(references=True) or []

        if referenced_objects:
            for ref in referenced_objects:
                link = self._obj_link(ref, "select")
                log_messages.append(f"Referenced Object: {link}")
            return False, log_messages  # Failed, log the referenced objects

        return True, log_messages  # All checks passed, no referenced objects found

    def check_framerate(self, target_framerate: Optional[str]) -> tuple:
        """Check if the scene's current framerate matches the target framerate."""
        if not target_framerate or str(target_framerate).upper() == "OFF":
            return True, []

        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping framerate check.")
            return True, []

        current_time_unit = cmds.currentUnit(query=True, time=True)
        if current_time_unit != target_framerate:
            return False, [
                f"Framerate mismatch: Current time unit is {current_time_unit}, expected {target_framerate}."
            ]

        return True, []

    def check_objects_below_floor(
        self, tolerance: float = _DEFAULT_FLOOR_TOLERANCE
    ) -> tuple:
        """Check if any object's geometry is below the floor plane (Y=0).

        Args:
            tolerance: Allowable distance (in scene units) beneath the plane
                before failing.  The UI exposes this as a checkbox, so enabling
                the check passes ``True``; that is treated as "use the default
                tolerance" rather than coerced to ``1.0``.  An explicit ``None``
                still means a strict ``0.0``.
        """
        offenders: List[str] = []

        # ``True`` (checkbox enabled) is a bool, not a real distance — honor the
        # documented default instead of float(True) == 1.0.
        if tolerance is True:
            tolerance = self._DEFAULT_FLOOR_TOLERANCE
        tolerance = 0.0 if tolerance is None else max(0.0, float(tolerance))
        limit = -tolerance

        for obj in self.objects:
            # Check if geometry (has shapes)
            shapes = cmds.listRelatives(obj, shapes=True)
            if not shapes:
                continue

            bbox = cmds.xform(obj, query=True, ws=True, bb=True)
            if not bbox:
                continue

            ymin = bbox[1]
            if ymin < limit:
                link = self._obj_link(obj)
                offenders.append(
                    f"Object: {link} - Below Floor: True (Y-min: {ymin:.3f})"
                )

        if offenders:
            header = [
                f"{len(offenders)} object(s) below floor "
                f"(tolerance: {tolerance:.3f} unit{'s' if tolerance != 1 else ''})"
            ]
            return False, header + self._truncate_obj_entries(offenders)

        return True, []  # All checks passed, no objects below the floor

    def check_overlapping_duplicate_mesh(self) -> tuple:
        """Check if there are any duplicate overlapping geometry objects in the current selection.

        Parameters:
            select (bool): Select any found duplicate objects.
            verbose (bool): Print found duplicates to the console.

        Returns:
            tuple: (status: bool, messages: list)
        """
        duplicates = EditUtils.get_overlapping_duplicates(objects=self.objects)
        if duplicates:
            messages = [
                f"Overlapping duplicate object: {self._obj_link(obj)}"
                for obj in duplicates
            ]
            return False, messages  # Failed, duplicates found
        return True, []  # Passed, no duplicates

    def check_hidden_geometry(self) -> tuple:
        """Check if any geometry objects are hidden."""
        hidden_objects = []
        # Define what we consider "geometry"
        geometry_types = {"mesh", "nurbsSurface", "subdiv"}

        for obj in self.objects:
            # Check if geometry (has shapes)
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
            if not shapes:
                continue

            # Check if any shape is actually geometry
            is_geometry = False
            for shape in shapes:
                if cmds.nodeType(shape) in geometry_types:
                    is_geometry = True
                    break

            if not is_geometry:
                continue

            # Check visibility
            if not cmds.getAttr(f"{obj}.visibility"):
                hidden_objects.append(obj)

        if hidden_objects:
            return False, [
                f"Hidden geometry detected: {self._obj_link(obj)}"
                for obj in hidden_objects
            ]
        return True, []

    def check_untied_keyframes(self) -> tuple:
        """Check if there are any untied keyframes on the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping untied keyframe check.")
            return True, []

        log_messages = []
        untied_keyframes_found = False

        # Optimization: Get all connections at once to avoid N calls to listConnections
        # connections=True returns [source, dest, source, dest...]
        # plugs=True returns [obj.plug, curve.output, ...]
        connections = (
            cmds.listConnections(
                self.objects,
                type="animCurve",
                source=True,
                destination=False,
                connections=True,
                plugs=True,
            )
            or []
        )

        # Parse into a dict: obj_name -> set(curves)
        obj_curves = {}
        for i in range(0, len(connections), 2):
            obj_plug = connections[i]  # e.g. "pCube1.translateX"
            curve_plug = connections[i + 1]  # e.g. "animCurveTL1.output"

            obj_name = obj_plug.split(".")[0]
            curve_name = curve_plug.split(".")[0]

            if obj_name not in obj_curves:
                obj_curves[obj_name] = set()
            obj_curves[obj_name].add(curve_name)

        for obj, curves in obj_curves.items():
            if not curves:
                continue

            # Get start/end for each curve
            curve_data = []
            min_start = float("inf")
            max_end = float("-inf")

            for curve in curves:
                # findKeyframe on a curve is fast
                s = cmds.findKeyframe(curve, which="first")
                e = cmds.findKeyframe(curve, which="last")
                curve_data.append((curve, s, e))

                if s < min_start:
                    min_start = s
                if e > max_end:
                    max_end = e

            # Check for mismatches
            obj_link = self._obj_link(obj)
            for curve, s, e in curve_data:
                if s > min_start:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj_link} (Start {s} != {min_start})"
                    )
                if e < max_end:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj_link} (End {e} != {max_end})"
                    )

        if untied_keyframes_found:
            return False, log_messages  # Failed, log untied keyframes

        return True, log_messages  # All checks passed, no untied keyframes

    def check_floating_point_keys(self) -> tuple:
        """Check if there are any floating point keyframes on the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping floating point key check.")
            return True, []

        log_messages = []
        offenders = []

        # Optimization: Iterate curves instead of objects
        # This is much faster than querying keyframes per object
        all_curves = (
            cmds.listConnections(
                self.objects, type="animCurve", source=True, destination=False
            )
            or []
        )
        all_curves = list(set(all_curves))

        for curve in all_curves:
            times = cmds.keyframe(curve, query=True, timeChange=True)
            if not times:
                continue

            for t in times:
                if not math.isclose(t, round(t), abs_tol=1e-4):
                    # Find object name
                    conn = cmds.listConnections(
                        curve, plugs=True, destination=True, source=False
                    )
                    obj_name = conn[0].split(".")[0] if conn else curve
                    offenders.append(f"{obj_name} (frame {t:.3f})")
                    break

        # Remove duplicates
        offenders = sorted(list(set(offenders)))

        if offenders:
            log_messages.append("Floating point keys found on:")
            for offender in offenders:
                # offender format: "objName (frame N.NNN)" — link the object part
                name = offender.split(" (frame")[0]
                link = self._obj_link(name, "select")
                detail = offender[len(name) :]
                log_messages.append(f"  - {link}{detail}")
            return False, log_messages

        return True, log_messages

    # ------------------------------------------------------------------
    # Hierarchy diff check — delegates to HierarchySidecar
    # ------------------------------------------------------------------

    # Backward-compatible aliases so existing call-sites still work.
    _manifest_path_for = staticmethod(HierarchySidecar.manifest_path_for)
    _diff_report_path_for = staticmethod(HierarchySidecar.diff_report_path_for)
    _build_clean_path_set = staticmethod(HierarchySidecar.build_clean_path_set)
    _get_top_level = staticmethod(HierarchySidecar.get_top_level)
    rename_hierarchy_sidecar = HierarchySidecar.rename

    def _build_full_hierarchy_set(self) -> set:
        """Build a clean path set including all descendants of ``self.objects``."""
        return HierarchySidecar.build_full_path_set(self.objects)

    def _sidecar_kwargs(self) -> dict:
        """Return sidecar path-derivation kwargs based on versioning state.

        When SceneExporter has set ``_version_format`` (i.e. the ``version``
        UI field is non-empty), sidecar paths route through the base stem so
        every version in a series shares one manifest.
        """
        return {"base_stem": bool(getattr(self, "_version_format", ""))}

    def write_hierarchy_manifest(self) -> None:
        """Write a sidecar JSON manifest of the exported hierarchy paths.

        Only writes when the manifest already exists (maintaining it for
        future checks) or the check was enabled in the current run.
        """
        export_path = getattr(self, "export_path", None)
        if not export_path or not self.objects:
            return

        sk = self._sidecar_kwargs()

        # Symmetric with check_hierarchy_vs_existing_fbx: when versioning
        # is active, promote any legacy `_v\d+` sidecar to the base-stem
        # name so subsequent writes find it via the "manifest already
        # exists" condition below.
        if sk["base_stem"]:
            HierarchySidecar.ensure_base_name(export_path)

        manifest_path = HierarchySidecar.manifest_path_for(export_path, **sk)

        check_ran = getattr(self, "_hierarchy_check_ran", False)
        if not check_ran and not os.path.exists(manifest_path):
            return

        paths = HierarchySidecar.build_full_path_set(self.objects)
        if HierarchySidecar.write_manifest(export_path, paths, **sk) is None:
            self.logger.debug("Could not write hierarchy manifest")

    def check_hierarchy_vs_existing_fbx(self) -> tuple:
        """Check export objects against the hierarchy manifest of the previous export.

        Compares namespace-stripped DAG paths of the current export objects
        against the sidecar ``.hierarchy.json`` written during the last
        successful export to the same path.  Detects missing or extra nodes
        that would indicate accidental structural changes.
        """
        self._hierarchy_check_ran = True

        export_path = getattr(self, "export_path", None)
        if not export_path:
            return True, []

        sk = self._sidecar_kwargs()

        # When versioning is active, migrate any legacy per-version sidecar
        # to the base-stem name so the diff baseline carries forward.
        if sk["base_stem"]:
            HierarchySidecar.ensure_base_name(export_path)

        manifest_path = HierarchySidecar.manifest_path_for(export_path, **sk)

        if not os.path.exists(manifest_path):
            if os.path.exists(export_path):
                return True, [
                    "No hierarchy manifest found for existing FBX. "
                    "A manifest will be created after this export."
                ]
            return True, []

        current_paths = HierarchySidecar.build_full_path_set(self.objects)

        match, missing, extra = HierarchySidecar.compare(
            export_path, current_paths, **sk
        )

        if match:
            HierarchySidecar.clean_stale_diff(export_path, **sk)
            return True, []

        messages = []

        # Detect reparenting patterns for a cleaner summary
        reparented = HierarchySidecar.detect_reparenting(missing, extra)

        diff_path = HierarchySidecar.write_diff_report(
            export_path, missing, extra, reparented=reparented, **sk
        )

        if reparented:
            for root, new_parent, count in reparented:
                messages.append(
                    f"Reparenting detected: '{root}' moved under '{new_parent}' "
                    f"({count} node(s) affected)"
                )
            # Report any remaining missing/extra not explained by reparenting
            explained_missing = set()
            explained_extra = set()
            for root, new_parent, _ in reparented:
                for p in missing:
                    if p.split("|")[0] == root:
                        explained_missing.add(p)
                        explained_extra.add(f"{new_parent}|{p}")
                explained_extra.add(new_parent)
            remaining_missing = [p for p in missing if p not in explained_missing]
            remaining_extra = [p for p in extra if p not in explained_extra]
        else:
            remaining_missing = missing
            remaining_extra = extra

        if remaining_missing:
            top_missing = HierarchySidecar.get_top_level(remaining_missing)
            messages.append(
                f"{len(remaining_missing)} node(s) in previous export but missing now "
                f"({len(top_missing)} top-level):"
            )
            for p in top_missing[:20]:
                messages.append(f"  − {p}")
            if len(top_missing) > 20:
                messages.append(f"  … and {len(top_missing) - 20} more")

        if remaining_extra:
            top_extra = HierarchySidecar.get_top_level(remaining_extra)
            messages.append(
                f"{len(remaining_extra)} new node(s) not in previous export "
                f"({len(top_extra)} top-level):"
            )
            for p in top_extra[:20]:
                messages.append(f"  + {p}")
            if len(top_extra) > 20:
                messages.append(f"  … and {len(top_extra) - 20} more")

        if diff_path:
            link = self.logger.log_link(
                "Open full diff report", "open", filepath=diff_path
            )
            messages.append(link)

        return False, messages


class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin):
    """Contains all task-related UI definitions for the Scene Exporter."""

    # Explicit execution order for export tasks.  Tasks not listed here
    # are appended at the end in alphabetical order.  This prevents the
    # alphabetical-sort default from running tasks in the wrong sequence
    # (e.g. set_bake_animation_range before smart_bake, or
    # delete_unused_materials before reassign_duplicate_materials).
    TASK_ORDER = [
        # Phase 1 — Environment setup
        "set_workspace",
        "set_linear_unit",
        # Phase 2 — Object filtering
        "ignore_groups",
        "exclude_hdr",
        # Phase 3 — Material cleanup (reassign THEN resolve THEN convert)
        "reassign_duplicate_materials",
        "resolve_invalid_texture_paths",
        "convert_to_relative_paths",
        # Phase 4 — Animation (bake THEN optimize THEN snap/tie THEN set range)
        "smart_bake",
        "optimize_keys",
        "snap_keys_to_frame",
        "tie_all_keyframes",
        "set_bake_animation_range",
        "export_data_node",
        "apply_declared_takes",
    ]

    _frame_rate_options: Dict[str, Any] = {
        (
            f"Check Scene FPS: {k}"
            if v is None
            else (
                f"Check Scene FPS: {v:g} fps"
                if any(c.isdigit() for c in k)
                else f"Check Scene FPS: {k} ({v:g} fps)"
            )
        ): (k if v is not None else None)
        for k, v in ptk.insert_into_dict(ptk.VidUtils.FRAME_RATES, "OFF", None).items()
    }

    _scene_unit_options: Dict[str, Any] = {
        f"Set Linear Unit: {k}": v
        for k, v in ptk.insert_into_dict(
            EnvUtils.SCENE_UNIT_VALUES, "OFF", None
        ).items()
    }

    # Max texture file-size thresholds (MB). OFF disables the check; the
    # remaining entries map a label to the megabyte limit passed to
    # check_texture_file_size.  Default selection is set in check_definitions.
    _texture_size_options: Dict[str, Any] = {
        "Check Max Texture Size: OFF": None,
        "Check Max Texture Size: 4 MB": 4,
        "Check Max Texture Size: 8 MB": 8,
        "Check Max Texture Size: 16 MB": 16,
        "Check Max Texture Size: 32 MB": 32,
        "Check Max Texture Size: 64 MB": 64,
        "Check Max Texture Size: 128 MB": 128,
    }

    def __init__(self, logger):
        super().__init__(logger)

        self.logger = logger
        self._objects = None
        self._cached_materials = None

    @property
    def objects(self):
        return self._objects

    @objects.setter
    def objects(self, value):
        """Invalidate the materials cache whenever objects change."""
        self._objects = value
        self._cached_materials = None

    _export_mode_options: Dict[str, Any] = {
        "Export: All Scene Objects": "all",
        "Export: All Visible Objects": "visible",
        "Export: Selected Objects Only": "selected",
    }

    @property
    def task_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the task definitions for the UI."""
        return {
            "sep_general": {
                "widget_type": "Separator",
                "title": "General",
            },
            "export_visible_objects": {
                "widget_type": "ComboBox",
                "setToolTip": "Choose what objects to export:\n- All Visible Objects: Export all visible geometry in the scene\n- Selected Objects Only: Export only currently selected objects\n- All Scene Objects: Export all objects regardless of visibility or selection",
                "add": self._export_mode_options,
                "value_method": "currentData",
            },
            "export_data_node": {
                "widget_type": "QCheckBox",
                "setText": "Export Scene Data Node",
                "setToolTip": (
                    "Include the shared data_export carrier in the export so its "
                    "embedded metadata (Shots' shot_metadata, Audio's "
                    "audio_manifest, …) ships in the FBX.\nThe carrier is a hidden "
                    "node, so the 'Visible'/'Selected' export modes would "
                    "otherwise omit it.  Refreshed from the live scene at export; "
                    "no-ops when there's no metadata to carry."
                ),
                "setChecked": True,
            },
            "set_linear_unit": {
                "widget_type": "ComboBox",
                "setToolTip": "Linear unit to be used during export.",
                "add": self._scene_unit_options,
            },
            "set_workspace": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Workspace",
                "setToolTip": "Determine the workspace directory from the scene path.",
                "setChecked": True,
            },
            "exclude_hdr": {
                "widget_type": "QCheckBox",
                "setText": "Exclude HDR Environment",
                "setToolTip": (
                    "Exclude the Arnold HDR environment light (aiSkyDomeLight) "
                    "from the export.\nThe skydome is image-based scene lighting, "
                    "not deliverable geometry — in 'All Scene Objects' mode it "
                    "would otherwise ride into the FBX.\nNo-op when the scene has "
                    "no skydome."
                ),
                "setChecked": True,
            },
            "sep_materials": {
                "widget_type": "Separator",
                "title": "Materials",
            },
            "reassign_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Reassign Duplicate Materials",
                "setToolTip": "Reassign any duplicate materials to a single material.",
                "setChecked": True,
            },
            "convert_to_relative_paths": {
                "widget_type": "QCheckBox",
                "setText": "Convert To Relative Paths",
                "setToolTip": (
                    "Convert absolute texture paths to project-relative paths.\n"
                    "External textures are first copied into sourceimages (if "
                    "not already there) so the relative paths still resolve — "
                    "otherwise converting to relative would break the links."
                ),
                "setChecked": True,
            },
            "resolve_invalid_texture_paths": {
                "widget_type": "QCheckBox",
                "setText": "Resolve Invalid Texture Paths",
                "setToolTip": "Attempt to resolve missing texture paths using workspace and sourceimages directory lookup.",
                "setChecked": True,
            },
            "sep_anim": {
                "widget_type": "Separator",
                "title": "Animation",
            },
            "smart_bake": {
                "widget_type": "QCheckBox",
                "setText": "Smart Bake",
                "setToolTip": "Intelligently bake constraints, driven keys, expressions, IK, motion paths, and blend shapes to keyframes.\nAuto-detects time range from drivers, deletes driver nodes after baking.",
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "QCheckBox",
                "setText": "Optimize Keys",
                "setToolTip": "Remove static curves and redundant flat keys from all exported objects.\nAlso controls key optimization inside Smart Bake.\nPreserves stepped tangent types.",
                "setChecked": True,
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Tie All Keyframes",
                "setToolTip": "Tie all keyframes on the specified objects.",
                "setChecked": True,
            },
            "snap_keys_to_frame": {
                "widget_type": "QCheckBox",
                "setText": "Snap Keys To Frame",
                "setToolTip": "Snap all keyframes to the nearest whole frame.",
                "setChecked": False,
            },
            "set_bake_animation_range": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Bake Animation Range",
                "setToolTip": "Set the animation export range to the first and last keyframes of the specified objects.\nThis will override the preset value, and is only applicable if baking is enabled.",
                "setChecked": True,
            },
            "apply_declared_takes": {
                "widget_type": "QCheckBox",
                "setText": "Export Shots as Animation Takes",
                "setToolTip": (
                    "Split the timeline into one named Unity AnimationClip per "
                    "shot (via FBX takes), and embed shot metadata (description, "
                    "objects, section) on the data_export node for engine-side "
                    "scripts.\nRequires shots defined in the Shots system."
                ),
                "setChecked": False,
            },
            "sep_hierarchy": {
                "widget_type": "Separator",
                "title": "Hierarchy",
            },
            "ignore_groups": {
                "widget_type": "QLineEdit",
                "setPlaceholderText": "Group names to ignore (comma-separated)",
                "setToolTip": "Comma-separated names of top-level groups to exclude from export (case-insensitive).\nExample: temp, proxy\nLeave empty to skip.",
                "setText": "temp",
                "value_method": "text",
            },
            "sep_output": {
                "widget_type": "Separator",
                "title": "Output",
            },
            # NOTE: `version` is a UI-only field — consumed by SceneExporter
            # (pop'd before run_tasks), never executed by the task pipeline.
            # The output format (FBX / GLB / FBX+GLB) is the same kind of UI-only
            # field, but it lives in its own `cmb004` Format combo rather than the
            # task list, so it isn't defined here.
            "version": {
                "widget_type": "QLineEdit",
                "setPlaceholderText": "{stem}_v{n:03d}  — empty disables",
                "setToolTip": (
                    "Version format for the export filename. Placeholders:\n"
                    "  {stem}  output basename\n"
                    "  {n:NNd} version number (zero-padded, NN digits)\n"
                    "  {date}  YYYY-MM-DD\n"
                    "  {user}  OS username (embeds dev identity — beware shared exports)\n"
                    "  {scene} Maya scene basename (requires saved scene)\n"
                    "Extension is handled automatically — do not include {ext}.\n"
                    "Use a '_v<N>' suffix (e.g. '_v{n:03d}') so the hierarchy "
                    "diff baseline can carry across versions."
                ),
                "setText": "",  # off by default — opt-in
                "value_method": "text",
            },
        }

    @property
    def check_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the check definitions for the UI."""
        return {
            "sep_general": {
                "widget_type": "Separator",
                "title": "General",
            },
            "check_framerate": {
                "widget_type": "ComboBox",
                "setToolTip": "Check the scene framerate against the target framerate.",
                "add": self._frame_rate_options,
            },
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "setText": "Check For Referenced Objects.",
                "setToolTip": "Check for referenced objects.",
                "setChecked": True,
            },
            "sep_hierarchy": {
                "widget_type": "Separator",
                "title": "Hierarchy & Naming",
            },
            "check_geometry_lod_suffix": {
                "widget_type": "QCheckBox",
                "setText": "Check Geometry LOD Suffix (_LODx)",
                "setToolTip": "Detect geometry named with LOD suffixes ending in '_LOD' or '_LOD' followed by digits (e.g., _LOD, _LOD1, _LOD02). This is informational.",
                "setChecked": True,
            },
            "check_duplicate_locator_names": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Locator Names",
                "setToolTip": "Check for duplicate locator names.",
                "setChecked": True,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "setText": "Check Root Default Transforms",
                "setToolTip": "Check for default transforms on root group nodes.\nTranslate, rotate, and scale should be (0, 0, 0) and (1, 1, 1) respectively.",
                "setChecked": True,
            },
            "check_hierarchy_vs_existing_fbx": {
                "widget_type": "QCheckBox",
                "setText": "Check Hierarchy vs Existing FBX",
                "setToolTip": (
                    "Compare the current export hierarchy against the previous export.\n"
                    "Detects missing or extra nodes that may indicate accidental changes.\n"
                    "Uses a lightweight sidecar manifest — no FBX reimport required."
                ),
                "setChecked": False,
            },
            "sep_geometry": {
                "widget_type": "Separator",
                "title": "Geometry",
            },
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "setText": "Check For Hidden Geometry.",
                "setToolTip": "Check for hidden geometry that will be exported.",
                "setChecked": True,
            },
            "check_overlapping_duplicate_mesh": {
                "widget_type": "QCheckBox",
                "setText": "Check For Overlapping Duplicates",
                "setToolTip": "Check for overlapping duplicate geometry.",
                "setChecked": True,
            },
            "check_objects_below_floor": {
                "widget_type": "QCheckBox",
                "setText": "Check For Objects Below Floor.",
                "setToolTip": (
                    "Check for geometry dipping below Y=0. A default 0.5 unit "
                    "tolerance is applied so shallow penetrations (e.g. tires) "
                    "do not immediately fail. Override by calling the check with a "
                    "'tolerance' keyword argument."
                ),
                "setChecked": True,
            },
            "sep_materials": {
                "widget_type": "Separator",
                "title": "Materials",
            },
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Materials.",
                "setToolTip": "Check for duplicate materials.",
                "setChecked": True,
            },
            "check_absolute_paths": {
                "widget_type": "QCheckBox",
                "setText": "Check For Absolute Paths.",
                "setToolTip": "Check for absolute paths.",
                "setChecked": True,
            },
            "check_valid_paths": {
                "widget_type": "QCheckBox",
                "setText": "Check For Valid Paths.",
                "setToolTip": "Check if all file paths (textures, references) exist on disk.",
                "setChecked": True,
            },
            "check_texture_file_size": {
                "widget_type": "ComboBox",
                "add": self._texture_size_options,
                "setCurrentIndex": 3,  # Default to 16 MB
                "setToolTip": (
                    "Fail the export when any texture feeding the export "
                    "materials exceeds the selected size on disk.\nFlags "
                    "un-downsized authoring maps (e.g. an 8K master) that would "
                    "bloat the shipped asset.\nSet to OFF to disable."
                ),
                "value_method": "currentData",
            },
            "sep_anim": {
                "widget_type": "Separator",
                "title": "Animation",
            },
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Check For Untied Keyframes",
                "setToolTip": "Check for untied keyframes on the specified objects.",
                "setChecked": True,
            },
            "check_floating_point_keys": {
                "widget_type": "QCheckBox",
                "setText": "Check For Floating Point Keys",
                "setToolTip": "Check for keyframes that are not on whole frames.",
                "setChecked": True,
            },
        }

    @property
    def definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return all definitions combined for backward compatibility."""
        return {**self.task_definitions, **self.check_definitions}


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
