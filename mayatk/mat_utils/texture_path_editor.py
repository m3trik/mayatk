# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    cmds = None

import os

from pythontk.img_utils._img_utils import ImgUtils
from pythontk.img_utils.map_factory import MapFactory
from pythontk.str_utils.fuzzy_matcher import FuzzyMatcher
from uitk.widgets.footer import FooterStatusController

# From this package:
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils


class TexturePathEditorSlots:
    _ROW_SELECTION_COLUMNS = {
        "shader": 0,
        "path": 1,
        "file_node": 2,
    }

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.texture_path_editor
        self._refresh_pending = False  # Flag to debounce refresh calls
        self._footer_controller = self._create_footer_controller()
        self._previous_paths = {}  # node_name -> path before last in-session repath (for tooltips)
        self._browse_in_progress = False  # re-entry guard for row_browse_for_file

    def header_init(self, widget):
        """Initialize the header for the texture path editor."""
        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self.refresh_texture_table)
        widget.menu.add("Separator", setTitle="General")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Open Source Images",
            setToolTip="Open the project's sourceimages directory in the file explorer.",
            setObjectName="open_source_images",
        )
        widget.menu.add("Separator", setTitle="Path Management")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Set Texture Directory",
            setToolTip="Set the texture file paths for all file nodes in the scene.\nPaths will be relative if they reside within the project's sourceimages directory.",
            setObjectName="lbl010",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Find and Copy Textures",
            setToolTip="Search recursively from a source directory for textures used by all file nodes in the scene, then copy them into a destination directory.\n\nNote: Arnold texture nodes are not supported.",
            setObjectName="lbl_find_copy",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Convert to Relative Paths",
            setToolTip="Convert all texture paths in the scene to relative paths based on the project's sourceimages directory.",
            setObjectName="lbl013",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Resolve Missing by Stem",
            setToolTip="For each missing texture, search the project's sourceimages directory for a file with the same name but a different extension and repath the file node.",
            setObjectName="resolve_missing_by_stem",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Resolve Missing by Fuzzy Match",
            setToolTip="For each missing texture, search the project's sourceimages directory for a file with a similar name (e.g. added '_demo' suffix) and repath the file node. Skips ambiguous matches.",
            setObjectName="resolve_missing_by_fuzzy",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Resolve Missing by Texture Name",
            setToolTip="For each missing texture, restrict candidates to files of the same map type (AO/DIFF/NORM/SPEC/etc.) and match on the map-stripped base name. Safest mode for typical texture sets.",
            setObjectName="resolve_missing_by_texture",
        )
        widget.menu.add("Separator", setTitle="Selection")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Select Textures for Selected Objects",
            setToolTip="Select the texture path cells in the table associated with the currently selected objects in the scene.",
            setObjectName="lbl014",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Select Broken Paths",
            setToolTip="Select all rows in the table where the texture file is missing.",
            setObjectName="lbl015",
        )
        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Texture Path Editor — Inspect and fix texture file paths.\n\n"
                "• View all scene texture nodes and their file paths in a table.\n"
                "• Set Texture Directory to repath all file nodes at once.\n"
                "• Find and Copy Textures from external directories\n"
                "  into the project.\n"
                "• Convert paths to relative (based on sourceimages directory).\n"
                "• Select textures for selected objects or highlight broken paths."
            ),
        )

    def open_source_images(self):
        """Open the project's sourceimages directory."""
        path = EnvUtils.get_env_info("sourceimages")
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            cmds.warning(f"Source images directory not found: {path}")

    def lbl010(self):
        """Set Texture Paths for All File Nodes (flattened — drops original subdirs)."""
        texture_dir = self.sb.dir_dialog(
            title="Set Texture Paths for All File Nodes",
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not texture_dir:
            return

        all_file_nodes = cmds.ls(type="file")
        if not all_file_nodes:
            cmds.warning("No file nodes in the scene.")
            return

        om.MGlobal.displayInfo(f"Setting texture paths to: {texture_dir}")
        count = self._set_texture_dir_flat(all_file_nodes, texture_dir)
        om.MGlobal.displayInfo(f"Updated {count}/{len(all_file_nodes)} file nodes.")

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

    def lbl_find_copy(self):
        """Find and Copy Textures (Global)"""
        from maya import cmds

        all_file_nodes = cmds.ls(type="file")
        if not all_file_nodes:
            cmds.warning("No file nodes in the scene.")
            return

        self._find_and_copy_workflow(all_file_nodes)

    def _find_and_copy_workflow(self, file_nodes):
        """Shared workflow for finding and copying textures.

        Parameters:
            file_nodes: List of file node names (strings) or nodes.
        """
        from maya import cmds

        # Preserve namespaces; stripping breaks cmds.getAttr/setAttr.
        node_names = [str(n) for n in file_nodes]

        start_dir = EnvUtils.get_env_info("sourceimages")
        source_dir = self.sb.dir_dialog(
            title="Select a root directory to recursively search for textures:",
            start_dir=start_dir,
        )
        if not source_dir:
            return

        found_textures = MatUtils.find_texture_files(
            file_nodes=node_names, source_dir=source_dir, recursive=True
        )
        if not found_textures:
            cmds.warning("No textures found.")
            return

        dest_dir = self.sb.dir_dialog(
            title="Select destination directory for textures:",
            start_dir=start_dir,
        )
        if not dest_dir:
            return

        MatUtils.move_texture_files(
            found_files=found_textures, new_dir=dest_dir, delete_old=False
        )

        # Filter file nodes to only remap ones that were successfully found and copied
        found_basenames = {os.path.basename(f).lower() for f in found_textures}
        nodes_to_remap = []
        for node_name in node_names:
            try:
                path = cmds.getAttr(f"{node_name}.fileTextureName")
                if path and os.path.basename(path).lower() in found_basenames:
                    nodes_to_remap.append(node_name)
            except Exception:
                continue

        if nodes_to_remap:
            # We manually remap here to ensure paths are flattened (matching the copy operation)
            # using MatUtils.remap_texture_paths would attempt to preserve relative directory structure
            # which breaks if the file was moved from a subdirectory to the root of dest_dir.
            project_sourceimages = EnvUtils.get_env_info("sourceimages")
            sourceimages_name = (
                os.path.basename(project_sourceimages) if project_sourceimages else ""
            )
            if project_sourceimages:
                project_sourceimages = os.path.abspath(project_sourceimages).replace(
                    "\\", "/"
                )

            cmds.undoInfo(openChunk=True, chunkName="Remap Found Textures")
            try:
                count = 0
                for node_name in nodes_to_remap:
                    path = cmds.getAttr(f"{node_name}.fileTextureName")
                    if not path:
                        continue

                    # Create new path based on destination directory + original filename (flattened)
                    filename = os.path.basename(path)
                    new_abs_path = os.path.normpath(
                        os.path.join(dest_dir, filename)
                    ).replace("\\", "/")

                    final_path = new_abs_path

                    # Convert to relative path if inside sourceimages
                    if project_sourceimages and new_abs_path.lower().startswith(
                        project_sourceimages.lower()
                    ):
                        rel = os.path.relpath(
                            new_abs_path, project_sourceimages
                        ).replace("\\", "/")
                        if sourceimages_name and not rel.startswith(
                            sourceimages_name + "/"
                        ):
                            final_path = f"{sourceimages_name}/{rel}"
                        else:
                            final_path = rel

                    cmds.setAttr(
                        f"{node_name}.fileTextureName", final_path, type="string"
                    )
                    count += 1
                om.MGlobal.displayInfo(f"Remapped {count} file nodes.")
            finally:
                cmds.undoInfo(closeChunk=True)
        else:
            cmds.warning("No file nodes matched the copied textures.")

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

    def lbl013(self):
        """Convert to Relative Paths (Global)"""
        all_file_nodes = cmds.ls(type="file")
        if not all_file_nodes:
            cmds.warning("No file nodes in the scene.")
            return
        MatUtils.remap_texture_paths(file_nodes=all_file_nodes)

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

    def resolve_missing_by_stem(self):
        """Resolve missing textures by exact stem match (different extension) in sourceimages."""
        self._resolve_missing_textures(mode="stem")

    def resolve_missing_by_fuzzy(self):
        """Resolve missing textures by fuzzy filename match in sourceimages."""
        self._resolve_missing_textures(mode="fuzzy")

    def resolve_missing_by_texture(self):
        """Resolve missing textures using map-type-aware matching in sourceimages."""
        self._resolve_missing_textures(mode="texture")

    def row_browse_for_file(self, selection=None):
        """Open a file dialog at sourceimages and repath the selected file node to the chosen file."""
        # Re-entry guard: the menu's QPushButton item is wired to this slot via
        # both the menu dispatcher AND QPushButton.clicked (matching object name →
        # slot auto-wire). Modal dialogs occasionally deliver a trailing release
        # event after closing, which retriggers the dispatcher and pops a second
        # dialog. Suppress with a short post-completion guard.
        if getattr(self, "_browse_in_progress", False):
            return
        self._browse_in_progress = True
        try:
            self._do_browse_for_file(selection)
        finally:
            from qtpy.QtCore import QTimer

            QTimer.singleShot(
                250, lambda: setattr(self, "_browse_in_progress", False)
            )

    def _do_browse_for_file(self, selection):
        nodes = self._file_nodes_from_selection(selection)
        if not nodes:
            return
        if len(nodes) > 1:
            cmds.warning("Browse for File: select a single row.")
            return

        node_name = nodes[0]
        sourceimages = EnvUtils.get_env_info("sourceimages") or ""
        try:
            current = cmds.getAttr(f"{node_name}.fileTextureName") or ""
        except Exception:
            current = ""

        start_dir = sourceimages
        if current:
            workspace = EnvUtils.get_env_info("workspace") or ""
            current_abs = (
                current if os.path.isabs(current)
                else os.path.normpath(os.path.join(workspace, current))
            )
            current_dir = os.path.dirname(current_abs)
            if current_dir and os.path.isdir(current_dir):
                start_dir = current_dir

        chosen = self.sb.file_dialog(
            file_types=[
                "*.png", "*.jpg", "*.jpeg", "*.tga", "*.tif", "*.tiff",
                "*.exr", "*.hdr", "*.bmp", "*.psd", "*.iff", "*.tx", "*.*",
            ],
            title=f"Select texture file for {node_name}",
            start_dir=start_dir,
            filter_description="Texture Files",
            allow_multiple=False,
        )
        if not chosen:
            return

        new_path = self._project_relative_converter()(chosen)
        cmds.undoInfo(openChunk=True, chunkName="Browse Texture File")
        try:
            cmds.setAttr(f"{node_name}.fileTextureName", new_path, type="string")
            if current and current != new_path:
                self._previous_paths[node_name] = current
            om.MGlobal.displayInfo(f"{node_name}: '{current}' -> '{new_path}'")
        except Exception as e:
            cmds.warning(f"{node_name}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

        self.ui.tbl000.init_slot()

    def row_resolve_by_stem(self, selection=None):
        nodes = self._file_nodes_from_selection(selection)
        if nodes:
            self._resolve_missing_textures(mode="stem", file_nodes=nodes)

    def row_resolve_by_fuzzy(self, selection=None):
        nodes = self._file_nodes_from_selection(selection)
        if nodes:
            self._resolve_missing_textures(mode="fuzzy", file_nodes=nodes)

    def row_resolve_by_texture(self, selection=None):
        nodes = self._file_nodes_from_selection(selection)
        if nodes:
            self._resolve_missing_textures(mode="texture", file_nodes=nodes)

    def _file_nodes_from_selection(self, selection):
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return []
        nodes = []
        for ctx in contexts:
            nodes.extend(ctx.get("file_nodes") or [])
        return list(dict.fromkeys(nodes))

    def _project_relative_converter(self):
        """Build a closure that converts an absolute path to a project-relative one (under sourceimages)."""
        si = EnvUtils.get_env_info("sourceimages") or ""
        si_abs = os.path.abspath(si).replace("\\", "/") if si else ""
        si_name = os.path.basename(si) if si else ""

        def to_relative(abs_path: str) -> str:
            norm = os.path.normpath(abs_path).replace("\\", "/")
            if si_abs and norm.lower().startswith(si_abs.lower()):
                rel = os.path.relpath(norm, si_abs).replace("\\", "/")
                if si_name and not rel.startswith(si_name + "/"):
                    return f"{si_name}/{rel}"
                return rel
            return norm

        return to_relative

    def _set_texture_dir_flat(self, file_nodes, target_dir: str) -> int:
        """Repath each file node so its texture lives directly under target_dir (basename only).

        Records the prior path in self._previous_paths so the table tooltip can show it.
        Returns the number of nodes actually updated.
        """
        if not file_nodes:
            return 0

        # Preserve namespaces; stripping breaks cmds.getAttr/setAttr.
        node_names = [str(n) for n in file_nodes]
        target_dir_norm = os.path.normpath(target_dir).replace("\\", "/")
        to_relative = self._project_relative_converter()

        count = 0
        cmds.undoInfo(openChunk=True, chunkName="Set Texture Directory")
        try:
            for node_name in node_names:
                try:
                    old_path = cmds.getAttr(f"{node_name}.fileTextureName") or ""
                except Exception:
                    continue
                if not old_path:
                    continue

                new_abs = os.path.normpath(
                    os.path.join(target_dir_norm, os.path.basename(old_path))
                ).replace("\\", "/")
                new_path = to_relative(new_abs)
                if new_path == old_path:
                    continue

                try:
                    cmds.setAttr(
                        f"{node_name}.fileTextureName", new_path, type="string"
                    )
                    self._previous_paths[node_name] = old_path
                    count += 1
                except Exception as e:
                    cmds.warning(f"{node_name}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)
        return count

    def _strategies_for_mode(self, mode: str, index_stems):
        """Build the FuzzyMatcher strategy pipeline for a resolve mode.

        Modes share an "exact" first tier (handles extension-only changes); fuzzy and
        texture modes add progressively looser fallbacks. The texture tier is a custom
        callable that requires same map type and matches on the map-stripped base name.
        """
        if mode == "stem":
            return ["exact"]
        if mode == "fuzzy":
            # use_base_name is intentionally NOT in the pipeline: numbered variants
            # like texture_001 / texture_002 should not auto-fuse.
            return ["exact", "substring", "ratio"]
        if mode == "texture":
            return [
                "exact",
                self._texture_aware_strategy(index_stems),
                "substring",
                "ratio",
            ]
        raise ValueError(f"Unknown resolve mode: {mode!r}")

    def _texture_aware_strategy(self, index_stems):
        """Build a custom strategy that filters by map type then fuzzy-matches the base name.

        For a missing `c130j_..._ao`, this restricts candidates to other `_AO` files,
        so an `_AO` file node can never get repathed to a `_DIFF` / `_NORM` / `_SPEC` file.
        """

        # Pre-compute (base_lower, map_type) for every candidate stem once.
        candidate_meta = []
        for stem in index_stems:
            try:
                map_type = MapFactory.resolve_map_type(stem + ".png", key=True)
            except Exception:
                map_type = None
            try:
                base = ImgUtils.get_base_texture_name(stem + ".png").lower()
            except Exception:
                base = stem
            candidate_meta.append((stem, base, map_type))

        def texture(target, candidates):
            try:
                target_map = MapFactory.resolve_map_type(target + ".png", key=True)
            except Exception:
                target_map = None
            if not target_map:
                return None, 0.0, "no_match"

            try:
                target_base = ImgUtils.get_base_texture_name(target + ".png").lower()
            except Exception:
                target_base = target

            # Filter to same map type; carry parallel lists so we can map back.
            same_map_stems = []
            same_map_bases = []
            for stem, base, map_type in candidate_meta:
                if map_type == target_map:
                    same_map_stems.append(stem)
                    same_map_bases.append(base)

            if not same_map_bases:
                return None, 0.0, "no_match"

            base_match, score, status = FuzzyMatcher.find_unique_match(
                target_base,
                same_map_bases,
                score_threshold=0.5,
                ambiguity_delta=0.05,
                use_base_name=False,
                use_substring=True,
                use_prefix=False,
                use_ratio=False,
            )
            if status == "no_match":
                return None, 0.0, "no_match"
            # Map the base hit back to its original (un-stripped) candidate stem.
            idx = same_map_bases.index(base_match)
            return same_map_stems[idx], score, status

        return texture

    def _resolve_missing_textures(self, mode: str, file_nodes=None):
        if mode not in ("stem", "fuzzy", "texture"):
            raise ValueError(f"Unknown resolve mode: {mode!r}")
        sourceimages = EnvUtils.get_env_info("sourceimages")
        if not sourceimages or not os.path.isdir(sourceimages):
            cmds.warning(f"sourceimages directory not found: {sourceimages}")
            return

        workspace = EnvUtils.get_env_info("workspace") or ""
        if file_nodes is None:
            all_file_nodes = cmds.ls(type="file") or []
        else:
            # Preserve namespaces; stripping breaks cmds.getAttr/setAttr.
            all_file_nodes = [str(n) for n in file_nodes]
        if not all_file_nodes:
            cmds.warning("No file nodes to process.")
            return

        missing = []
        for node in all_file_nodes:
            try:
                path = cmds.getAttr(f"{node}.fileTextureName") or ""
            except Exception:
                continue
            if not path or "<udim>" in path.lower():
                continue
            abs_path = (
                path if os.path.isabs(path)
                else os.path.normpath(os.path.join(workspace, path))
            )
            if os.path.exists(abs_path):
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem:
                missing.append((node, path, stem))

        if not missing:
            om.MGlobal.displayInfo("No missing textures to resolve.")
            return

        # Index sourceimages by stem
        index = []
        for root, _, files in os.walk(sourceimages):
            for f in files:
                stem = os.path.splitext(f)[0]
                if stem:
                    index.append((stem.lower(), os.path.join(root, f)))

        if not index:
            cmds.warning("No files in sourceimages to match against.")
            return

        to_project_relative = self._project_relative_converter()

        # Build a stem-key -> abs-path map (multiple files may share a stem)
        by_stem = {}
        for stem_key, abs_path in index:
            by_stem.setdefault(stem_key, []).append(abs_path)
        index_stems = list(by_stem.keys())

        strategies = self._strategies_for_mode(mode, index_stems)

        resolved = 0
        ambiguous = 0
        no_match = 0

        cmds.undoInfo(openChunk=True, chunkName="Resolve Missing Textures")
        try:
            for node, current_path, stem in missing:
                stem_lower = stem.lower()

                match_name, _score, status, strat_name = (
                    FuzzyMatcher.find_with_fallbacks(
                        stem_lower,
                        index_stems,
                        strategies=strategies,
                        score_threshold=0.6,
                        ambiguity_delta=0.05,
                    )
                )
                if status == "no_match":
                    no_match += 1
                    continue
                if status == "ambiguous":
                    ambiguous += 1
                    cmds.warning(
                        f"{node}: ambiguous {strat_name} match for '{stem}', skipped."
                    )
                    continue

                matches = by_stem.get(match_name) or []
                if not matches:
                    no_match += 1
                    continue
                if len(matches) > 1:
                    ambiguous += 1
                    cmds.warning(
                        f"{node}: '{match_name}' resolves to {len(matches)} files, skipped."
                    )
                    continue
                final_abs = matches[0]

                new_path = to_project_relative(final_abs)
                try:
                    cmds.setAttr(f"{node}.fileTextureName", new_path, type="string")
                    self._previous_paths[node] = current_path
                    resolved += 1
                    om.MGlobal.displayInfo(
                        f"{node}: '{current_path}' -> '{new_path}'"
                    )
                except Exception as e:
                    cmds.warning(f"{node}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

        om.MGlobal.displayInfo(
            f"Resolved {resolved}/{len(missing)} missing "
            f"(no match: {no_match}, ambiguous: {ambiguous})."
        )
        self.ui.tbl000.init_slot()

    def refresh_texture_table(self):
        """Manual refresh trigger from the header refresh button."""
        table = getattr(self.ui, "tbl000", None)
        if not table:
            return
        table.init_slot()

    def tbl000_init(self, widget):
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self.handle_cell_edit)
            if self._footer_controller:
                widget.itemSelectionChanged.connect(self._footer_controller.update)

            # Add context menu items using the existing menu system
            widget.menu.add("Separator", setTitle="Path Management")
            widget.menu.add(
                "QPushButton",
                setText="Set Directory",
                setObjectName="row_set_texture_directory",
                setToolTip="Move or relink the texture for this row to a new directory.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Find and Copy Texture",
                setObjectName="row_find_and_copy_texture",
                setToolTip="Search for this texture in a folder and copy any matches to a destination.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Convert to Relative Path",
                setObjectName="remap_to_relative",
                setToolTip="Convert the selected file node's texture path to a relative path",
            )
            widget.menu.add(
                "QPushButton",
                setText="Resolve Missing by Stem",
                setObjectName="row_resolve_by_stem",
                setToolTip="Search sourceimages for a file with the same name but a different extension and repath this file node.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Resolve Missing by Fuzzy Match",
                setObjectName="row_resolve_by_fuzzy",
                setToolTip="Search sourceimages for a file with a similar name (e.g. added '_demo' suffix) and repath this file node. Skips ambiguous matches.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Resolve Missing by Texture Name",
                setObjectName="row_resolve_by_texture",
                setToolTip="Restrict candidates to the same map type (AO/DIFF/NORM/SPEC/etc.) and match on the map-stripped base name. Safest mode for typical texture sets.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Browse for File...",
                setObjectName="row_browse_for_file",
                setToolTip="Open a file browser starting at sourceimages and pick a file to repath this file node to. Single selection only.",
            )

            widget.menu.add("Separator", setTitle="Selection")
            widget.menu.add(
                "QPushButton",
                setText="Select In Scene",
                setObjectName="select_material",
                setToolTip="Select all scene objects currently assigned to this material",
            )
            widget.menu.add(
                "QPushButton",
                setText="Select File Node",
                setObjectName="select_file_node",
                setToolTip="Select the file node in Maya",
            )
            widget.menu.add(
                "QPushButton",
                setText="Show in Hypershade",
                setObjectName="row_show_in_hypershade",
                setToolTip="Graph the selected file node in the Hypershade editor.",
            )

            widget.menu.add("Separator", setTitle="Edit")
            widget.menu.add(
                "QPushButton",
                setText="Delete File Node",
                setObjectName="delete_file_node",
                setToolTip="Delete the selected file node from Maya",
            )

            def _bind_menu_action(action_name, method, columns=None):
                widget.register_menu_action(
                    action_name,
                    lambda selection, fn=method: fn(selection),
                    columns=columns or self._ROW_SELECTION_COLUMNS,
                )

            _bind_menu_action(
                "row_set_texture_directory", self.row_set_texture_directory
            )
            _bind_menu_action(
                "row_find_and_copy_texture", self.row_find_and_copy_texture
            )

            _bind_menu_action("remap_to_relative", self.remap_to_relative)
            _bind_menu_action("row_resolve_by_stem", self.row_resolve_by_stem)
            _bind_menu_action("row_resolve_by_fuzzy", self.row_resolve_by_fuzzy)
            _bind_menu_action("row_resolve_by_texture", self.row_resolve_by_texture)
            _bind_menu_action("row_browse_for_file", self.row_browse_for_file)
            _bind_menu_action("select_material", self.select_material)
            _bind_menu_action("select_file_node", self.select_file_node)
            _bind_menu_action("row_show_in_hypershade", self.row_show_in_hypershade)
            _bind_menu_action("delete_file_node", self.delete_file_node)

            # Set up centralized scene-change callbacks
            self._setup_scene_change_callback(widget)

        self._refresh_table_content(widget)

    def _setup_scene_change_callback(self, widget):
        """Subscribe to scene-change events via ScriptJobManager."""
        mgr = ScriptJobManager.instance()
        for event in (
            "SceneOpened",
            "NewSceneOpened",
            "SceneImported",
            "workspaceChanged",
        ):
            mgr.subscribe(
                event,
                lambda w=widget: self._on_scene_change(w),
                owner=self,
            )
        mgr.connect_cleanup(widget, owner=self)

    def _on_scene_change(self, widget):
        """Callback function that gets called when the scene changes."""
        # Debounce: if a refresh is already pending, skip this call
        if self._refresh_pending:
            return

        self._refresh_pending = True

        def do_refresh():
            self._refresh_pending = False
            self._previous_paths.clear()
            try:
                # Check if widget is still valid
                try:
                    if not widget.isVisible():
                        # If widget is hidden or destroyed, we might want to cleanup
                        pass
                except RuntimeError:
                    # Widget has been deleted (C++ object deleted)
                    self.cleanup_scene_callbacks()
                    return

                print(
                    "TexturePathEditor: Scene changed, refreshing texture path table..."
                )
                self._refresh_table_content(widget)
            except Exception as e:
                print(f"TexturePathEditor: Error refreshing table on scene change: {e}")

        # Use evalDeferred to coalesce multiple events and ensure scene is ready
        cmds.evalDeferred(do_refresh)

    def _refresh_table_content(self, widget):
        """Refresh the table content with current scene data."""
        # Show wait cursor during refresh for large scenes
        cmds.waitCursor(state=True)
        try:
            widget.setUpdatesEnabled(False)
            widget.clear()
            # Optimization: Request strings only to avoid node creation overhead
            rows = MatUtils.get_file_nodes(
                return_type="shaderName|path|fileNodeName", raw=True
            )
            if not rows:
                rows = [("", "", "No file nodes found")]

            formatted = []
            for shader_name, path, file_node_name in rows:
                # Stash node names in UserRole so handle_cell_edit can recover the
                # old name after the user edits a cell (item.text() is the new value).
                # Column 1 (path) is left as plain text — its column formatter reads
                # UserRole-or-text and would otherwise misinterpret a stashed identifier.
                formatted.append([
                    (shader_name, shader_name),
                    path,
                    (file_node_name, file_node_name),
                ])

            # Populate table (triggers cellChanged if signals not blocked)
            widget.add(formatted, headers=["Shader", "Texture Path", "File Node"])

            # Configure column resizing behavior
            header = widget.horizontalHeader()
            header.setSectionsMovable(False)  # Prevent column reordering
            header.setSectionResizeMode(
                0, self.sb.QtWidgets.QHeaderView.Interactive
            )  # Shader column - manually resizable
            header.setSectionResizeMode(
                1, self.sb.QtWidgets.QHeaderView.Stretch
            )  # Texture Path column - stretches to fill
            header.setSectionResizeMode(
                2, self.sb.QtWidgets.QHeaderView.Interactive
            )  # File Node column - manually resizable

            # Set minimum column widths for usability
            widget.setColumnWidth(0, 200)  # Shader column minimum width
            widget.setColumnWidth(2, 200)  # File Node column minimum width

            # Setup and apply formatting in one pass (avoid double formatting)
            self.setup_formatting(widget)
            # Note: widget.add() already calls apply_formatting(), but we need our
            # custom formatter applied, so we call it once more after setup
            widget.apply_formatting()
        finally:
            widget.setUpdatesEnabled(True)
            cmds.waitCursor(state=False)

        if self._footer_controller:
            self._footer_controller.update()

    def cleanup_scene_callbacks(self):
        """Clean up scene-change subscriptions via ScriptJobManager."""
        ScriptJobManager.instance().unsubscribe_all(self)

    def setup_formatting(self, widget):
        source_root = EnvUtils.get_env_info("workspace")

        # Pre-compute all unique paths and check existence in batch
        # This avoids repeated os.path.exists() calls during formatting
        path_cache = {}

        # Collect all unique paths first
        unique_paths = set()
        for row in range(widget.rowCount()):
            item = widget.item(row, 1)
            if item:
                path = str(item.text()).strip()
                if path:
                    unique_paths.add(path)

        # Batch check file existence (potentially parallelized for network paths)
        def resolve_and_check(path):
            abs_path = (
                os.path.normpath(os.path.join(source_root, path))
                if not os.path.isabs(path)
                else os.path.normpath(path)
            )
            return path, os.path.exists(abs_path), abs_path

        # For small datasets, check inline; for large datasets, use threading
        if len(unique_paths) > 50:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(resolve_and_check, p): p for p in unique_paths
                }
                for future in as_completed(futures):
                    try:
                        path, exists, abs_path = future.result(timeout=5)
                        path_cache[path] = (exists, abs_path)
                    except Exception:
                        path = futures[future]
                        abs_path = (
                            os.path.normpath(os.path.join(source_root, path))
                            if not os.path.isabs(path)
                            else os.path.normpath(path)
                        )
                        path_cache[path] = (False, abs_path)
        else:
            for path in unique_paths:
                _, exists, abs_path = resolve_and_check(path)
                path_cache[path] = (exists, abs_path)

        def format_if_invalid(item, value, row, col, *_):
            path = str(value).strip()

            if path in path_cache:
                exists, abs_path = path_cache[path]
            else:
                # Fallback for any paths not in cache
                abs_path = (
                    os.path.normpath(os.path.join(source_root, path))
                    if not os.path.isabs(path)
                    else os.path.normpath(path)
                )
                exists = os.path.exists(abs_path)
                path_cache[path] = (exists, abs_path)

            widget.format_item(item, key="reset" if exists else "invalid")

            tooltip_lines = [abs_path if exists else f"Missing file:\n{abs_path}"]
            fn_item = widget.item(row, 2)
            fn_name = str(fn_item.text()).strip() if fn_item else ""
            previous = self._previous_paths.get(fn_name) if fn_name else None
            if previous and previous != path:
                tooltip_lines.append(f"Previous: {previous}")
            item.setToolTip("\n\n".join(tooltip_lines))

        widget.set_column_formatter(1, format_if_invalid)

    def _resolve_context(self, shader_name, file_node_data):
        shader_name = str(shader_name).strip() if shader_name else ""
        shader_node = shader_name if shader_name else None

        # Resolve actual node for the file node column payload
        if isinstance(file_node_data, (list, tuple)):
            file_node_data = next(
                (v for v in file_node_data if v and cmds.objExists(str(v))),
                None,
            )

        # OPTIMIZATION: Use file_node_data directly if available instead of
        # calling get_file_nodes which is expensive
        material_file_nodes = []
        if file_node_data:
            material_file_nodes = [file_node_data]
        elif shader_node:
            # Use listHistory directly on the shader - much faster than get_file_nodes
            try:
                history = cmds.ls(cmds.listHistory(shader_node) or [], type="file") or []
                material_file_nodes = list(dict.fromkeys(history))  # unique, ordered
            except Exception:
                material_file_nodes = []

        return {
            "shader_name": shader_name,
            "shader_node": shader_node,
            "file_node": file_node_data,
            "file_nodes": material_file_nodes,
        }

    def _get_selected_contexts(
        self,
        selection=None,
        require_file_nodes: bool = True,
        warn_on_empty: bool = False,
    ):
        table = getattr(self.ui, "tbl000", None)
        if table is None:
            return []

        if selection is None:
            selection = table.get_selection(
                columns=self._ROW_SELECTION_COLUMNS,
                include_current=True,
            )

        if not selection:
            if warn_on_empty:
                cmds.warning("No row selected.")
            return []

        contexts = []
        for entry in selection:
            shader_value = self._selection_value(entry, "shader")
            file_node_value = self._selection_value(entry, "file_node")
            context = self._resolve_context(shader_value, file_node_value)
            if require_file_nodes and not context["file_nodes"]:
                continue
            contexts.append(context)

        if require_file_nodes and not contexts:
            cmds.warning("No valid file nodes found in the selected row(s).")
            return []

        return contexts

    def _selection_value(self, entry, key: str):
        if hasattr(entry, "values"):
            return entry.values.get(key)
        if hasattr(entry, "get"):
            try:
                value = entry.get(key)
                if value is not None:
                    return value
            except TypeError:
                pass
        column = self._ROW_SELECTION_COLUMNS.get(key)
        if column is not None and isinstance(entry, dict):
            return entry.get(column)
        return None

    def _resolve_absolute_texture_path(self, file_node):
        try:
            path_value = cmds.getAttr(f"{file_node}.fileTextureName")
        except Exception:
            return ""
        if not path_value:
            return ""
        project_sourceimages = EnvUtils.get_env_info("sourceimages") or ""
        if os.path.isabs(path_value):
            return os.path.abspath(path_value)
        return os.path.abspath(os.path.join(project_sourceimages, path_value))

    def _remap_context_textures(self, contexts, **kwargs):
        contexts = contexts or []
        file_nodes = []
        shader_names = []
        for context in contexts:
            if not context:
                continue
            nodes = context.get("file_nodes") or []
            if nodes:
                file_nodes.extend(nodes)
            else:
                shader_name = context.get("shader_name")
                if shader_name:
                    shader_names.append(shader_name)

        if file_nodes:
            MatUtils.remap_texture_paths(
                file_nodes=MatUtils._unique_ordered(file_nodes), **kwargs
            )
        elif shader_names:
            MatUtils.remap_texture_paths(materials=shader_names, **kwargs)

    def delete_file_node(self, selection=None):
        """Delete the selected file nodes."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_delete = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_delete.append(file_node)

        if not nodes_to_delete:
            return

        # Deduplicate
        nodes_to_delete = list(set(nodes_to_delete))
        node_names = [n.split('|')[-1].split(':')[-1] for n in nodes_to_delete]

        count = len(nodes_to_delete)
        msg = f"Are you sure you want to delete {count} file node(s)?"
        if count == 1:
            msg = f"Are you sure you want to delete the file node '{node_names[0]}'?"

        reply = self.sb.message_box(msg, "Yes", "No")

        if reply == "Yes":
            try:
                cmds.delete(nodes_to_delete)
                om.MGlobal.displayInfo(f"Deleted {count} file node(s).")
                self.ui.tbl000.init_slot()
            except Exception as e:
                om.MGlobal.displayError(f"Failed to delete file nodes: {str(e)}")

    def select_file_node(self, selection=None):
        """Select the file nodes from the selected rows."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_select = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_select.append(file_node)

        if not nodes_to_select:
            return

        try:
            cmds.select(nodes_to_select, r=True)
            om.MGlobal.displayInfo(f"Selected {len(nodes_to_select)} file node(s).")
        except Exception as e:
            om.MGlobal.displayError(f"Failed to select file nodes: {str(e)}")

    def row_show_in_hypershade(self, selection=None):
        """Graph the selected file node in the Hypershade."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_graph = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_graph.append(file_node)

        if not nodes_to_graph:
            return

        MatUtils.graph_materials(nodes_to_graph)

    def select_material(self, selection=None):
        """Select the materials associated with the selected rows."""
        contexts = self._get_selected_contexts(
            selection,
            require_file_nodes=False,
        )
        if not contexts:
            return

        all_assigned_objects = []
        for context in contexts:
            shader_name = context.get("shader_name")
            if not shader_name:
                continue
            try:
                assigned = MatUtils.find_by_mat_id(shader_name, shell=True)
                if assigned:
                    all_assigned_objects.extend(assigned)
            except Exception as e:
                print(f"Failed to query objects for '{shader_name}': {e}")

        if not all_assigned_objects:
            cmds.warning("No scene objects found for the selected materials.")
            return

        try:
            cmds.select(all_assigned_objects, r=True)
            om.MGlobal.displayInfo(f"Selected objects for {len(contexts)} material(s).")
        except Exception as e:
            om.MGlobal.displayError(f"Failed to select objects: {str(e)}")

    def remap_to_relative(self, selection=None):
        """Remap the selected file nodes' texture paths to relative paths."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        try:
            self._remap_context_textures(contexts)

            om.MGlobal.displayInfo(
                f"Remapped textures for {len(contexts)} item(s) to relative paths."
            )
            self.ui.tbl000.init_slot()
        except Exception as e:
            om.MGlobal.displayError(f"Failed to remap file nodes to relative path: {str(e)}")

    def row_set_texture_directory(self, selection=None):
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return
        target_dir = self.sb.dir_dialog(
            title="Select a directory for these textures:",
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not target_dir:
            return

        file_nodes = []
        for ctx in contexts:
            file_nodes.extend(ctx.get("file_nodes") or [])
        file_nodes = list(dict.fromkeys(file_nodes))

        self._set_texture_dir_flat(file_nodes, target_dir)
        self.ui.tbl000.init_slot()

    def row_find_and_copy_texture(self, selection=None):
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        all_file_nodes = []
        for context in contexts:
            all_file_nodes.extend(context["file_nodes"])

        if not all_file_nodes:
            cmds.warning("No file nodes found in selection.")
            return

        self._find_and_copy_workflow(all_file_nodes)

    def handle_cell_edit(self, row: int, col: int):
        tbl = self.ui.tbl000
        item = tbl.item(row, col)
        if not item:
            return
        new_value = item.text()
        UserRole = self.sb.QtCore.Qt.UserRole

        def _restore_text(target_item, original):
            tbl.blockSignals(True)
            try:
                target_item.setText(original)
            finally:
                tbl.blockSignals(False)

        def _rename_node(label):
            old_name = item.data(UserRole)
            if not old_name:
                _restore_text(item, new_value)
                return
            if new_value == old_name:
                return
            if not cmds.objExists(old_name):
                cmds.warning(f"{label} '{old_name}' no longer exists; cannot rename.")
                _restore_text(item, old_name)
                return
            try:
                actual = cmds.rename(old_name, new_value)
            except Exception as e:
                cmds.warning(f"Failed to rename {label}: {e}")
                _restore_text(item, old_name)
                return
            item.setData(UserRole, actual)
            if actual != new_value:
                _restore_text(item, actual)
            om.MGlobal.displayInfo(f"Renamed {label} '{old_name}' -> '{actual}'")

        if col == 0:  # Shader rename
            _rename_node("shader")

        elif col == 1:  # File path update
            fn_item = tbl.item(row, 2)
            file_node = fn_item.data(UserRole) if fn_item else None
            if not file_node:
                cmds.warning("No file node associated with this row.")
                return
            if not cmds.objExists(file_node):
                cmds.warning(f"File node '{file_node}' no longer exists.")
                return
            try:
                cmds.setAttr(
                    f"{file_node}.fileTextureName", new_value, type="string"
                )
                om.MGlobal.displayInfo(
                    f"{file_node}: texture path -> '{new_value}'"
                )
                tbl.apply_formatting()
                if self._footer_controller:
                    self._footer_controller.update()
            except Exception as e:
                cmds.warning(f"Failed to update texture path: {e}")

        elif col == 2:  # File node rename
            _rename_node("file node")

    def _create_footer_controller(self):
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return None
        return FooterStatusController(
            footer=footer,
            resolver=self._resolve_source_images_path,
            default_text="",
            truncate_kwargs={"length": 96, "mode": "middle"},
        )

    def _resolve_source_images_path(self) -> str:
        return EnvUtils.get_env_info("sourceimages") or ""

    def lbl014(self):
        """Select table rows associated with selected objects."""
        selection = cmds.ls(sl=True, flatten=True)
        if not selection:
            self.sb.message_box("Select object(s) first.")
            return

        # Get materials from selection
        mats = MatUtils.get_mats(selection)
        if not mats:
            cmds.warning("No materials found on selected objects.")
            return

        # OPTIMIZATION: Get file nodes directly via listHistory on materials
        # instead of calling get_file_nodes which rebuilds the entire mapping
        target_node_names = set()
        for mat in mats:
            try:
                file_nodes = cmds.ls(cmds.listHistory(mat) or [], type="file") or []
                for fn in file_nodes:
                    target_node_names.add(fn.split('|')[-1].split(':')[-1])
            except Exception:
                pass

        if not target_node_names:
            cmds.warning("No file nodes found for selected objects.")
            return

        table = self.ui.tbl000
        table.clearSelection()

        selected_count = 0

        for row in range(table.rowCount()):
            # Column 2 contains the file node data
            node_data = table.item_data(row, 2)
            if not node_data:
                continue

            node_name = (
                str(node_data).split("|")[-1].split(":")[-1]
            )

            if node_name in target_node_names:
                # Select the Texture Path cell (Column 1)
                path_item = table.item(row, 1)
                if path_item:
                    path_item.setSelected(True)
                    selected_count += 1

                    # Scroll to the first selected item
                    if selected_count == 1:
                        table.scrollToItem(path_item)

        if selected_count > 0:
            om.MGlobal.displayInfo(f"Selected {selected_count} rows in the table.")

    def lbl015(self):
        """Select Broken Paths"""
        widget = self.ui.tbl000
        source_root = EnvUtils.get_env_info("workspace")

        # Clear current selection
        widget.clearSelection()

        # Select rows with broken paths
        selection_mode = widget.selectionMode()
        widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.MultiSelection)

        rows_to_select = []
        try:
            for row in range(widget.rowCount()):
                item = widget.item(row, 1)
                if not item:
                    continue

                path = str(item.text()).strip()
                if not path:
                    continue

                abs_path = (
                    os.path.normpath(os.path.join(source_root, path))
                    if not os.path.isabs(path)
                    else os.path.normpath(path)
                )

                if not os.path.exists(abs_path):
                    rows_to_select.append(row)

            if rows_to_select:
                for row in rows_to_select:
                    # Select the Texture Path cell (Column 1)
                    path_item = widget.item(row, 1)
                    if path_item:
                        path_item.setSelected(True)

                # Scroll to first selected
                widget.scrollToItem(widget.item(rows_to_select[0], 1))

                om.MGlobal.displayInfo(f"Selected {len(rows_to_select)} broken paths.")
            else:
                om.MGlobal.displayInfo("No broken paths found.")

        except Exception as e:
            cmds.warning(f"Error selecting broken paths: {e}")
        finally:
            widget.setSelectionMode(selection_mode)


# --------------------------------------------------------------------------------------------

# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
