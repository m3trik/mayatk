# !/usr/bin/python
# coding=utf-8
"""Reference Manager controller — the panel-facing layer over the engine.

:class:`ReferenceManagerController` extends :class:`ReferenceManager` with the
UI logic the panel drives (file-list state, workspace switching, foreign-scene
conversion, scratch twins of scenes opened "as new").
"""

import html
import os
from functools import wraps
from typing import Optional

try:
    import maya.cmds as cmds
except ImportError:
    pass
import pythontk as ptk

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.usd import UsdReadRefused, UsdUtils
from mayatk.env_utils.reference_manager._reference_manager import (
    AssemblyManager,
    ReferenceManager,
)


# Scratch twins of foreign scenes opened "as new" (see ReferenceManagerController
# .open_scene): one per source under the system temp dir, age-swept, discarded on
# close/next open while still untouched. Process-wide, like the open scene itself; lazily
# built (no temp-dir lookup at import). Mirror of blendertk's.
_OPENED_SCRATCH_PREFIX = "mtk_opened"
_opened_scratches = None


def _scratch_twins():
    """The panel's ``ptk.ScratchTwins`` store (``<temp>/mtk_opened_<hash>/<stem>_<ext>.ma``)."""
    global _opened_scratches
    if _opened_scratches is None:
        _opened_scratches = ptk.ScratchTwins(_OPENED_SCRATCH_PREFIX, extension=".ma")
    return _opened_scratches


class ReferenceManagerController(ReferenceManager, ptk.LoggingMixin):
    """Controller that bridges Maya reference functionality with UI interactions.

    This class extends ReferenceManager with UI-specific logic including:
    - Table widget management and item formatting
    - File selection and reference synchronization
    - Directory and workspace management
    - UI state management and signal blocking
    - Item editing and rename functionality

    UI Integration:
    - Manages table selection sync with Maya references
    - Handles file filtering and display name stripping
    - Controls workspace combo box updates
    - Manages current scene file highlighting and disabling

    Usage:
    - Select files in the table to add them as references
    - Double-click file names to rename display text
    """

    #: Max file names listed verbatim in the delete confirmation (the rest fold
    #: into an "...and N more" line).
    DELETE_PROMPT_MAX_NAMES = 10

    def __init__(self, slot, log_level="WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.slot = slot
        self.sb = slot.sb
        self.ui = slot.ui

        self._last_dir_valid = None
        self._updating_directory = False  # Flag to prevent cascading UI events
        self._editing_item = None  # Track which item is being edited
        self._context_menu_row = (
            None  # Row index captured at right-click for row-scoped context actions
        )
        self._warned_scene_placeholder_typo = False
        self._workspace_history_max = (
            50  # Max entries in per-directory workspace memory
        )
        self.logger.debug("ReferenceManagerController initialized.")

    def _get_workspace_history(self) -> dict:
        """Load the per-directory workspace selection history from settings."""
        return self.ui.settings.value("workspace_history") or {}

    def _save_workspace_selection(self, root_dir: str, workspace_name: str):
        """Remember which workspace was last selected for a given root directory."""
        history = self._get_workspace_history()
        key = os.path.normcase(os.path.normpath(root_dir))
        history[key] = workspace_name
        # Trim to max size, keeping the most-recently-added entries
        if len(history) > self._workspace_history_max:
            history = dict(list(history.items())[-self._workspace_history_max :])
        self.ui.settings.setValue("workspace_history", history)

    def _restore_workspace_index(self, widget) -> bool:
        """Try to set the combo box to the last-used workspace for the current directory.

        Returns True if a saved selection was restored.
        """
        root_dir = self.ui.txt000.text().strip()
        if not root_dir:
            return False
        key = os.path.normcase(os.path.normpath(root_dir))
        history = self._get_workspace_history()
        saved_name = history.get(key)
        if saved_name:
            for i in range(widget.count()):
                if widget.itemText(i) == saved_name:
                    widget.setCurrentIndex(i)
                    self.logger.debug(
                        f"_restore_workspace_index: Restored '{saved_name}' at index {i}"
                    )
                    return True
        return False

    def _normalize_subfolder_structure_pattern(self, pattern: str) -> str:
        """Normalize and validate the user-entered folder structure pattern.

        Notes:
            - `{scenes}` is the supported placeholder.
            - `{scene}` is a common typo; we warn (once) and auto-correct to `{scenes}`.
        """
        pattern = (pattern or "").strip()
        if "{scene}" in pattern:
            if not self._warned_scene_placeholder_typo:
                self._warned_scene_placeholder_typo = True
                msg = (
                    "Folder Structure: '{scene}' is not a supported placeholder. "
                    "Did you mean '{scenes}'? Auto-correcting for this operation."
                )
                try:
                    cmds.warning(msg)
                except Exception:
                    pass
                self.logger.warning(msg)
            pattern = pattern.replace("{scene}", "{scenes}")
        return pattern

    def _scenes_folder(self) -> str:
        """The workspace's ``scene`` file-rule folder — the ``{scenes}`` placeholder
        value. Falls back to the literal ``"scenes"`` off a marked workspace or on
        error. Shared by Save, the folder-structure filter, and the live preview."""
        try:
            return cmds.workspace(fileRuleEntry="scene") or "scenes"
        except Exception:
            return "scenes"

    def _is_workspace_structure(self, folder) -> bool:
        """True if *folder* is the browsed workspace or its ``{scenes}`` root, or
        holds either: the project's own structure, never one scene's folder however
        it is named, so Rename never moves it and Delete never removes it. The name
        test alone let a scene named for its folder claim it -- ``scenes_final.ma``
        loose in the scenes root, ``proj_v01.ma`` at the root of project ``proj``
        -- even with no other scene below to hold it back."""
        workspace = self.current_working_dir
        if not workspace:
            return False
        folder = os.path.normcase(os.path.abspath(folder))
        inside = os.path.join(folder, "")
        for root in (workspace, os.path.join(workspace, self._scenes_folder())):
            root = os.path.normcase(os.path.abspath(root))
            if root == folder or root.startswith(inside):
                return True
        return False

    def _naming_menu(self):
        """The header menu — home of the naming fields (case / suffix / folder
        structure), which Save, Rename, Delete and the list filters all read.
        ``None`` until the header is built."""
        header = getattr(getattr(self.slot, "ui", None), "header", None)
        return getattr(header, "menu", None)

    def _naming_options(self):
        """``(case_style, suffix, structure_pattern)`` as set in the header menu's
        Naming section — the single read every consumer (save / rename / delete / filters /
        previews) goes through; defaults when the menu isn't built yet. Both text
        fields come back trimmed (blendertk's filter read always trimmed them)."""
        menu = self._naming_menu()
        case_w = getattr(menu, "cmb_case_style", None) if menu else None
        suffix_w = getattr(menu, "txt_suffix", None) if menu else None
        txt = getattr(menu, "txt_subfolder_structure", None) if menu else None
        return (
            case_w.currentText() if case_w is not None else "None",
            suffix_w.text().strip() if suffix_w is not None else "",
            txt.text().strip() if txt is not None else "",
        )

    def _default_save_name(self, case_style: str, suffix: str) -> str:
        """The name Save would prepopulate: the open scene's base, less every trailing
        *suffix* (so it isn't double-appended), case-formatted. Empty with no scene.

        EVERY trailing suffix: Save strips one from whatever its prompt returns, so a
        prefill still ending in it -- a ``_v01_v01`` the old Rename doubled -- would
        save under another name than the one both previews show.

        ``splitext``, not ``split(".")[0]`` (the old spelling): a dotted scene name
        like ``hero.rig.ma`` must prepopulate ``hero.rig``, not ``hero`` — only the
        extension comes off (the spelling blendertk's twin always used)."""
        current_scene = cmds.file(q=True, sceneName=True) or ""
        if not current_scene:
            return ""
        base = os.path.splitext(os.path.basename(current_scene))[0]
        stripped = ptk.StrUtils.strip_suffix(base, [suffix])
        while stripped != base:
            base, stripped = stripped, ptk.StrUtils.strip_suffix(stripped, [suffix])
        return self._format_name(base, case_style, suffix="")

    def _resolve_save_target(
        self, name: str, case_style: str, suffix: str, pattern: str
    ) -> str:
        """The absolute ``.ma`` path Save To Workspace would write for *name* under
        the current workspace with the given naming options — shared by
        :meth:`save_scene` and the Save button's live tooltip, so the preview can't
        drift from the real save. *pattern* arrives normalized (each caller handles
        the ``{scene}`` typo its own way: Save warns once, the previews annotate).

        Raises:
            ValueError: Invalid workspace, or an invalid placeholder pattern.
        """
        workspace = self.current_working_dir
        if not workspace or not os.path.isdir(workspace):
            raise ValueError("Current workspace directory is invalid.")
        target_dir = workspace
        if pattern:
            try:
                rel = ptk.StrUtils.replace_placeholders(
                    pattern,
                    scenes=self._scenes_folder(),
                    name=self._format_name(name, case_style, suffix=""),
                    workspace=os.path.basename(workspace),
                    suffix=suffix,
                )
            except ValueError as e:
                raise ValueError(f"Invalid folder structure pattern: {e}")
            target_dir = os.path.join(workspace, rel)
        # normpath: the pattern's own separators ride through the join, and the
        # path is user-facing (the Save button's tooltip previews it verbatim).
        return os.path.normpath(
            os.path.join(
                target_dir, self._format_name(name, case_style, suffix) + ".ma"
            )
        )

    def _save_scene_preview(self) -> str:
        """Live tooltip for the footer Save button — the exact path Save would
        write for the current scene under the current naming options."""
        case_style, suffix, structure_text = self._naming_options()
        notes = []
        if "{scene}" in structure_text:
            notes.append("<b>{scene}</b> is not valid — did you mean <b>{scenes}</b>?")
        pattern = structure_text.replace("{scene}", "{scenes}")
        name = self._default_save_name(case_style, suffix) or "<scene name>"
        try:
            path = self._resolve_save_target(name, case_style, suffix, pattern)
        except ValueError as e:
            path = None
            notes.append(html.escape(str(e)))
        return self.sb.tooltip.fmt(
            title="Save To Workspace",
            body="Save the current scene into the workspace using the header "
            "menu's Naming options: case / suffix / folder structure.",
            rows=[("saves →", f"<b>{html.escape(path)}</b>")] if path else None,
            notes=notes or None,
        )

    def _folder_structure_preview(self) -> str:
        """Live tooltip for ``txt_subfolder_structure`` — resolve the placeholders
        against the current workspace + scene so the hover shows the real Save dir.

        Side-effect-free (unlike :meth:`_normalize_subfolder_structure_pattern`,
        which warns): the ``{scene}`` typo is corrected locally and surfaced as a
        note instead of logging on every hover."""
        case_style, suffix, pattern = self._naming_options()

        workspace = self.current_working_dir or ""
        workspace_name = os.path.basename(workspace) if workspace else "<workspace>"

        # {name} = the current scene's base (what Save would prepopulate), formatted
        # the same way — so the preview reflects the real Save output.
        name_val = self._default_save_name(case_style, suffix) or "<scene name>"

        notes = ["e.g. {scenes} · {scenes}/{name} · {scenes}/{name}/versions"]
        if "{scene}" in pattern:
            notes.append("<b>{scene}</b> is not valid — did you mean <b>{scenes}</b>?")
        resolve_pattern = pattern.replace("{scene}", "{scenes}")

        context = {
            "scenes": self._scenes_folder(),
            "name": name_val,
            "workspace": workspace_name,
            "suffix": suffix,
        }
        # Final absolute save dir, mirroring save_scene's os.path.join(workspace, …).
        try:
            rel = ptk.StrUtils.replace_placeholders(resolve_pattern, **context)
            final = os.path.join(workspace, rel) if workspace else rel
        except ValueError:
            final = None

        # Fold the field's help text into the live tooltip (binding replaces the
        # static setToolTip): purpose + what each placeholder means + its value.
        return self.sb.tooltip.placeholder_preview(
            resolve_pattern,
            context,
            title="Folder Structure",
            body="Where scenes live, panel-wide: <b>Save To Workspace</b> writes "
            "here, <b>Filter by Folder Structure</b> matches against it, and with "
            "<b>{name}</b> Rename and Delete keep each scene's own folder in step.",
            descriptions={
                "scenes": "workspace scenes folder (workspace.mel)",
                "name": "scene name — excludes the suffix",
                "workspace": "workspace folder name",
                "suffix": "the Suffix field above",
            },
            final=final,
            final_label="save dir →",
            notes=notes,
        )

    def _wire_structure_tooltip(self, menu) -> None:
        """Bind the live folder-structure preview once the menu widget's ``.tooltip``
        proxy is stamped. Menu registration is deferred (coalesced next-tick drain),
        so we bind on the following tick, with a small bounded retry as insurance."""
        from qtpy import QtCore

        def _bind(attempts_left=5):
            txt = getattr(menu, "txt_subfolder_structure", None)
            proxy = getattr(txt, "tooltip", None) if txt is not None else None
            if proxy is not None:
                proxy.bind(self._folder_structure_preview)
            elif attempts_left > 0:
                QtCore.QTimer.singleShot(0, lambda: _bind(attempts_left - 1))

        QtCore.QTimer.singleShot(0, _bind)

    @property
    def current_working_dir(self):
        # Use the parent class implementation but add logging
        working_dir = super().current_working_dir
        self.logger.debug(f"Getting current_working_dir: {working_dir}")
        return working_dir

    @current_working_dir.setter
    def current_working_dir(self, value):
        self.logger.debug(f"Setting current_working_dir to: {value}")

        # Validate directory first
        if not os.path.isdir(value):
            self.logger.warning(
                f"Invalid directory set as current_working_dir: {value}"
            )
            # Still set it for consistency, but it will be corrected by the parent property getter
            self._current_working_dir = value
            return

        old_value = getattr(self, "_current_working_dir", None)

        # Use parent class setter logic
        if os.path.isdir(value):
            self._current_working_dir = value
            # Only invalidate if the directory actually changed
            if old_value != value:
                self.logger.debug(
                    f"Directory changed from {old_value} to {value}, invalidating workspace files"
                )
                self.invalidate_workspace_files()
                # Don't call refresh_file_list here to avoid circular calls
                # Let the calling code handle the refresh timing
            else:
                self.logger.debug("Directory unchanged, no invalidation needed")

    def block_table_selection_method(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            t = self.ui.tbl000
            t.blockSignals(True)
            self.logger.debug(f"Blocking signals for method: {method.__name__}")
            try:
                return method(self, *args, **kwargs)
            finally:
                t.blockSignals(False)
                self.logger.debug(f"Unblocking signals for method: {method.__name__}")

        return wrapper

    def prepare_item_for_edit(self, item):
        """Prepare an item for editing by showing the full filename."""
        if item.column() != 0:  # Files column is at index 0
            return

        # Store the current editing item
        self._editing_item = item

        # Get the full filename for editing
        full_filename = item.data(self.sb.QtCore.Qt.UserRole + 1)
        if full_filename:
            item.setText(full_filename)
            self.logger.debug(
                f"Prepared item for edit with full filename: {full_filename}"
            )

    def restore_item_display(self, item):
        """Restore the item to its display name after editing."""
        if item.column() != 0:  # Files column is at index 0
            return

        # Clear the editing item tracker
        if self._editing_item == item:
            self._editing_item = None

        # Restore the display name
        display_name = item.data(self.sb.QtCore.Qt.UserRole + 2)
        if display_name:
            item.setText(display_name)
            self.logger.debug(f"Restored item display name: {display_name}")

    def is_item_being_edited(self, item):
        """Check if an item is currently being edited."""
        return self._editing_item == item

    def _format_table_item(self, item, file_path: str) -> None:
        """Apply enable/disable state based on whether the file is the current scene."""
        norm_fp = os.path.normcase(os.path.normpath(file_path))
        _scene = cmds.file(q=True, sceneName=True) or ""
        current_scene = os.path.normcase(os.path.normpath(_scene)) if _scene else ""
        is_current_scene = norm_fp == current_scene

        if is_current_scene:
            # Keep item enabled (for rename/delete/open) but not selectable
            # (prevents accidental reference toggling via click-selection).
            # handle_item_selection already filters out the current scene.
            item.setFlags(
                (item.flags() | self.sb.QtCore.Qt.ItemIsEnabled)
                & ~self.sb.QtCore.Qt.ItemIsSelectable
            )
            item.setToolTip(
                f"{os.path.basename(file_path)}\n"
                f"Current scene file - cannot be referenced\n{file_path}"
            )
            # Apply current style (italic + orange) and mark as styled
            self.ui.tbl000.format_item(item, key="current", italic=True)
            item.setData(
                self.sb.QtCore.Qt.UserRole + 10, True
            )  # Mark as current-styled
        else:
            # Re-enable the item if it was previously disabled
            item.setFlags(
                item.flags()
                | (self.sb.QtCore.Qt.ItemIsSelectable | self.sb.QtCore.Qt.ItemIsEnabled)
            )
            # Only reset color if this item was previously styled as current scene
            was_current = item.data(self.sb.QtCore.Qt.UserRole + 10)
            if was_current:
                self.ui.tbl000.format_item(item, key="reset", italic=False)
                item.setData(self.sb.QtCore.Qt.UserRole + 10, False)  # Clear the marker

    @staticmethod
    def _is_foreign(path):
        """True if *path* is a foreign (Blender) scene — a cross-DCC row that must be baked
        through the blender_bridge before it can be referenced."""
        # Deferred: the slots module imports this one (the extension table is
        # the panel's, ``ReferenceManagerSlots.FOREIGN_EXTENSIONS``).
        from mayatk.env_utils.reference_manager.reference_manager_slots import (
            ReferenceManagerSlots,
        )

        return (
            bool(path)
            and os.path.splitext(path)[1].lower()
            in ReferenceManagerSlots.FOREIGN_EXTENSIONS
        )

    @staticmethod
    def _bake_source_key(referenced_path):
        """Normalized path of the foreign scene *referenced_path* was baked from, or None.

        A foreign row references a cached ``.ma`` bake, so mapping a live reference back
        to the source row the user sees is the only way that row can read as referenced
        (and be un-referenced by a second click).
        """
        if not referenced_path:
            return None
        try:
            from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport
        except ImportError:  # bridge unavailable — no row can be bake-backed
            return None
        source = BlenderSceneImport.bake_source(referenced_path)
        return os.path.normcase(os.path.normpath(source)) if source else None

    def handle_item_selection(self):
        t = self.ui.tbl000
        selected_items = [
            t.item(idx.row(), 0)  # Files column is at index 0
            for idx in t.selectedIndexes()
            if idx.column() == 0 and t.item(idx.row(), 0)  # Files column is at index 0
        ]

        # Filter out disabled items (current scene) from selection data
        selected_data = set()
        _scene = cmds.file(q=True, sceneName=True) or ""
        current_scene = os.path.normpath(_scene) if _scene else ""

        # Clear selection of any disabled items (current scene) immediately
        items_to_deselect = []

        for item in selected_items:
            file_path = item.data(self.sb.QtCore.Qt.UserRole)
            norm_fp = os.path.normpath(file_path) if file_path else ""

            # Skip if this is the current scene file (disabled item)
            if norm_fp == current_scene:
                self.logger.debug(
                    f"Skipping current scene file in selection: {file_path}"
                )
                # Mark item for deselection
                items_to_deselect.append(item)
                continue

            # Skip if item is disabled (shouldn't be selectable)
            if not (item.flags() & self.sb.QtCore.Qt.ItemIsSelectable):
                self.logger.debug(f"Skipping disabled item in selection: {file_path}")
                items_to_deselect.append(item)
                continue

            selected_data.add((item.text(), file_path))

        # Deselect disabled items immediately to provide visual feedback
        for item in items_to_deselect:
            item.setSelected(False)

        current_references = self.current_references

        # Diff the selection against current references by normalized file
        # path (which is unique) rather than by display text.  Two files
        # sharing a display name in different folders -- or rendered
        # identically once "Hide Extension" is on -- must each be referenced,
        # not collapse to a single arbitrary entry.
        current_refs_by_path = {
            os.path.normcase(os.path.normpath(ref.path)): ref
            for ref in current_references
        }
        selected_by_path = {
            os.path.normcase(os.path.normpath(file_path)): (text, file_path)
            for text, file_path in selected_data
        }

        paths_to_add = set(selected_by_path) - set(current_refs_by_path)
        # Only a LISTED file can be deselected: a reference whose file has no row here
        # (another workspace, a filtered-out type) is left alone. A foreign row
        # references its BAKE (a cached .ma, never a row) and is intentionally
        # non-selectable, so the bake-backed exclusion keeps an unrelated selection
        # change from silently un-referencing every foreign row — those are toggled by
        # their own icon. Mirror of blendertk's ``_on_selection_changed``.
        listed = set()
        for row in range(t.rowCount()):
            item = t.item(row, 0)
            file_path = item.data(self.sb.QtCore.Qt.UserRole) if item else None
            if file_path:
                listed.add(os.path.normcase(os.path.normpath(file_path)))
        paths_to_remove = {
            p
            for p in (set(current_refs_by_path) & listed) - set(selected_by_path)
            if not self._bake_source_key(current_refs_by_path[p].path)
        }

        self.logger.debug(
            f"Selected paths to add: {paths_to_add}, to remove: {paths_to_remove}"
        )

        for norm_path in paths_to_remove:
            ref = current_refs_by_path[norm_path]
            self.logger.debug(f"Removing reference for namespace: {ref.namespace}")
            self.remove_references(ref.namespace)

        for norm_path in paths_to_add:
            # The display text seeds the namespace (add_reference sanitizes it);
            # Maya makes it unique per reference, so same-named files coexist.
            namespace, file_path = selected_by_path[norm_path]
            self.logger.debug(
                f"Adding reference for namespace: {namespace}, file_path: {file_path}"
            )
            success = self.add_reference(namespace, file_path)
            if not success:
                for item in selected_items:
                    item_fp = item.data(self.sb.QtCore.Qt.UserRole)
                    if (
                        item_fp
                        and os.path.normcase(os.path.normpath(item_fp)) == norm_path
                    ):
                        item.setSelected(False)
                        break

        # Sync reference action icons to current state
        self._sync_reference_icons()

    @block_table_selection_method
    def sync_selection_to_references(self):
        """Sync the table selection to match current scene references."""
        t = self.ui.tbl000
        t.blockSignals(True)
        try:
            t.clearSelection()
            current_references = self.current_references
            _scene = cmds.file(q=True, sceneName=True) or ""
            current_scene = os.path.normcase(os.path.normpath(_scene)) if _scene else ""

            # Create a mapping from file paths to namespaces for current references
            ref_path_to_namespace = {
                os.path.normcase(os.path.normpath(ref.path)): ref.namespace
                for ref in current_references
            }

            self.logger.debug(
                f"Syncing selection to current references: {[ref.namespace for ref in current_references]}"
            )
            self.logger.debug(
                f"Reference path to namespace mapping: {ref_path_to_namespace}"
            )

            for row in range(t.rowCount()):
                item = t.item(row, 0)  # Files column is at index 0
                if item:
                    file_path = item.data(self.sb.QtCore.Qt.UserRole)
                    norm_fp = (
                        os.path.normcase(os.path.normpath(file_path))
                        if file_path
                        else ""
                    )

                    # Check if this file path corresponds to a current reference
                    if norm_fp in ref_path_to_namespace:
                        # Don't select the current scene file even if it's somehow referenced
                        if norm_fp != current_scene and (
                            item.flags() & self.sb.QtCore.Qt.ItemIsSelectable
                        ):
                            item.setSelected(True)
                            namespace = ref_path_to_namespace[norm_fp]
                            self.logger.debug(
                                f"Selected item for reference: {item.text()} (namespace: {namespace})"
                            )
                        else:
                            self.logger.debug(
                                f"Skipped selecting disabled/current scene item: {item.text()}"
                            )
        finally:
            t.blockSignals(False)

    def update_current_dir(self, text: Optional[str] = None):
        # Prevent cascading updates during directory changes
        if self._updating_directory:
            self.logger.debug(
                "update_current_dir: Already updating directory, skipping"
            )
            return

        self._updating_directory = True
        try:
            text = text or self.ui.txt000.text()
            new_dir = os.path.normpath(text.strip())

            is_valid = os.path.isdir(new_dir)
            # Compare *normalized* paths. ``current_working_dir`` often comes
            # from Maya (``cmds.workspace(q=True, rd=True)``) with forward
            # slashes and a trailing separator, so a raw ``!=`` against the
            # ``os.path.normpath``-ed input reads as "changed" on every startup
            # and fires a redundant ``_update_workspace_combo()`` (the combo is
            # already populated by ``cmb000_init``) — which double-logged the
            # "No workspaces" warning.
            current = self.current_working_dir or ""
            changed = os.path.normcase(new_dir) != os.path.normcase(
                os.path.normpath(current)
            )

            self.logger.debug(
                f"update_current_dir: new_dir='{new_dir}', current='{self.current_working_dir}', is_valid={is_valid}, changed={changed}, recursive={self.recursive_search}"
            )

            # Visual feedback (tooltip/action color) is owned by the
            # widget's set_validator("dir") wiring in txt000_init.

            revalidate = is_valid and (changed or self._last_dir_valid is False)
            self._last_dir_valid = is_valid

            if revalidate:
                self.logger.debug(
                    "update_current_dir: Revalidating and updating current working dir."
                )
                # Update the current working directory first
                self.current_working_dir = new_dir
                # Update the workspace combo box with the new directory
                self._update_workspace_combo()
            elif not is_valid:
                self.logger.debug(
                    "update_current_dir: Directory is not valid, clearing workspace combo box."
                )
                self.ui.cmb000.clear()
                # Clear the file list as well since directory is invalid
                self.ui.tbl000.setRowCount(0)
                # Still update the working dir even if invalid for consistency
                self.current_working_dir = new_dir
                self._update_workspace_footer()
            else:
                self.logger.debug(
                    "update_current_dir: No revalidation needed (directory unchanged and was already valid)"
                )
        finally:
            self._updating_directory = False

    def set_workspace(self, workspace_path: str, invalidate: bool = True) -> bool:
        """Set the current workspace for browsing and refresh the file list.

        This manages the tool's internal state only.  Maya's scene workspace
        is intentionally **not** changed here — that only happens on scene
        open (see ``open_scene``).

        Parameters:
            workspace_path: Path to the workspace directory
            invalidate: Whether to invalidate the file cache when refreshing

        Returns:
            True if workspace was set successfully, False otherwise
        """
        if not workspace_path or not os.path.isdir(workspace_path):
            self.logger.warning(
                f"set_workspace: Invalid workspace path: {workspace_path}"
            )
            return False

        # Check if the workspace is already set to the requested path
        current_workspace = self.current_working_dir
        is_same_workspace = current_workspace and os.path.normcase(
            os.path.normpath(workspace_path)
        ) == os.path.normcase(os.path.normpath(current_workspace))

        if not is_same_workspace:
            self.logger.debug(f"set_workspace: Setting workspace to: {workspace_path}")
            self.current_working_dir = workspace_path
        else:
            self.logger.debug(
                f"set_workspace: Workspace already set to {workspace_path}"
            )

        # Remember this selection for next time this directory is loaded
        if not is_same_workspace:
            root_dir = self.ui.txt000.text().strip()
            if root_dir:
                workspace_name = os.path.basename(workspace_path)
                self._save_workspace_selection(root_dir, workspace_name)

        # Refresh file list
        self.refresh_file_list(invalidate=invalidate)
        self._update_workspace_footer()
        return True

    def _update_workspace_footer(self):
        """Display the active workspace name in the footer."""
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return
        workspace = self.current_working_dir or ""
        name = os.path.basename(workspace.rstrip("/\\"))
        footer.setStatusText(f"Workspace: {name}" if name else "")

    def set_maya_project(self):
        """Set Maya's project (workspace) to the browsed workspace — the explicit
        counterpart of the automatic set on scene open (:meth:`open_scene`), for
        when the project should switch without opening anything.

        Browsing alone never touches Maya's project (see :meth:`set_workspace`);
        this is the one deliberate way to commit the combo's selection to it.
        """
        workspace = self.current_working_dir
        if not workspace or not os.path.isdir(workspace):
            self.sb.message_box("Select a valid workspace first.")
            return False
        try:
            current = cmds.workspace(q=True, rd=True)
            if os.path.normcase(os.path.normpath(current)) == os.path.normcase(
                os.path.normpath(workspace)
            ):
                self.logger.debug("Maya project already set to this workspace.")
            else:
                cmds.workspace(workspace, openWorkspace=True)
                self.logger.info(f"Set Maya project to: {workspace}")
        except Exception as e:
            self.sb.message_box(
                f"Failed to set the Maya project:<br>{html.escape(str(e))}"
            )
            return False
        footer = getattr(self.ui, "footer", None)
        if footer is not None:
            name = os.path.basename(workspace.rstrip("/\\"))
            footer.setStatusText(f"Maya project set: {name}", level="success")
            self.sb.defer_with_timer(self._update_workspace_footer, ms=3000)
        return True

    def _update_workspace_combo(self, root_dir=None):
        """Repopulate the workspace combo box and select the best match.

        Selection priority:
            1. In-memory previous selection (same combo path still present)
            2. Persisted per-directory workspace history
            3. First item

        This is the single source of truth for populating cmb000.
        """
        root_dir = root_dir or self.current_working_dir
        self.logger.debug(f"_update_workspace_combo: root_dir={root_dir}")

        widget = self.ui.cmb000

        if not root_dir or not os.path.isdir(root_dir):
            widget.clear()
            self.ui.tbl000.setRowCount(0)
            return

        workspaces = self.find_available_workspaces(root_dir)

        # Block signals to prevent cascading events
        widget.blockSignals(True)
        try:
            # Capture current selection before clearing
            current_index = widget.currentIndex()
            current_path = (
                widget.itemData(current_index) if current_index >= 0 else None
            )

            widget.clear()
            widget.add(workspaces)

            if workspaces:
                restored = False
                # 1. Try to keep the in-memory selection if it still exists
                if current_path:
                    for i in range(widget.count()):
                        if widget.itemData(i) == current_path:
                            widget.setCurrentIndex(i)
                            self.logger.debug(
                                f"_update_workspace_combo: Restored in-memory selection at index {i}"
                            )
                            restored = True
                            break

                # 2. Try persisted per-directory history
                if not restored:
                    restored = self._restore_workspace_index(widget)

                # 3. Fall back to first item
                if not restored:
                    widget.setCurrentIndex(0)
                    self.logger.debug(
                        "_update_workspace_combo: Defaulted to first workspace"
                    )

                self.logger.debug(
                    f"_update_workspace_combo: {len(workspaces)} workspaces, selected index {widget.currentIndex()}"
                )
            else:
                self.logger.warning(
                    f"_update_workspace_combo: No workspaces in {root_dir}"
                )
        finally:
            widget.blockSignals(False)

        # Signals were blocked, so trigger workspace load manually
        if widget.count() > 0 and widget.currentIndex() >= 0:
            selected_workspace_path = widget.itemData(widget.currentIndex())
            self.logger.debug(
                f"_update_workspace_combo: Setting workspace to: {selected_workspace_path}"
            )
            self.set_workspace(selected_workspace_path, invalidate=True)
        else:
            self.ui.tbl000.setRowCount(0)

    def refresh_file_list(self, invalidate=False):
        """Refresh the file list for the table widget."""
        # Row indices change on refresh — invalidate any captured context-menu row
        self._context_menu_row = None
        # Use internal method for the table operations that need signal blocking
        self._refresh_file_list_internal(invalidate)

        # Ensure references are properly selected after table update (outside signal blocking)
        self.sync_selection_to_references()

    @block_table_selection_method
    def _refresh_file_list_internal(self, invalidate=False):
        """Internal method that refreshes the file list with signals blocked."""
        if invalidate:
            self.logger.debug("Invalidating workspace files cache.")
            self.invalidate_workspace_files()

        index = self.ui.cmb000.currentIndex()
        workspace_path = self.ui.cmb000.itemData(index)

        # If no workspace is selected, try to use current_working_dir as fallback
        if workspace_path is None:
            if index == -1 and self.ui.cmb000.count() > 0:
                # Combo box was just repopulated but currentIndex is still -1
                # This can happen during initialization, so just return without warning
                self.logger.debug(
                    "No workspace selected yet (combobox initializing) - skipping refresh"
                )
                return
            else:
                self.logger.warning("No workspace selected in combo box.")
                return

        self.logger.debug(f"Refreshing file list for workspace: {workspace_path}")

        if not workspace_path or not os.path.isdir(workspace_path):
            self.slot.logger.warning(
                f"[refresh_file_list] Invalid workspace: {workspace_path}"
            )
            return

        file_list = self.workspace_files.get(workspace_path, [])

        # Include Types — the native scan caches every NATIVE_EXTENSIONS type, so a toggle
        # only re-filters (no cache invalidation). Unchecking .mb replaces the old
        # "Hide Binary Files" checkbox; .fbx and USD are native (referenced through the
        # FBX plugin / mayaUsd's translator).
        header_menu = self.slot.ui.header.menu
        included = self.slot._included_extensions()
        file_list = [f for f in file_list if os.path.splitext(f)[1].lower() in included]

        # The Naming fields feed the suffix / folder-structure filters.
        _case_style, suffix_text, structure_text = self._naming_options()

        # Check for filter by suffix setting
        filter_suffix = getattr(header_menu, "chk_filter_suffix", None)
        if filter_suffix and filter_suffix.isChecked() and suffix_text:
            filtered_list = []
            for f in file_list:
                name_without_ext = os.path.splitext(os.path.basename(f))[0]
                if name_without_ext.endswith(suffix_text):
                    filtered_list.append(f)
            file_list = filtered_list

        # Check for filter by folder structure setting
        filter_structure = getattr(header_menu, "chk_filter_folder_structure", None)
        if filter_structure and filter_structure.isChecked():
            # Determine the pattern to use
            pattern = self._normalize_subfolder_structure_pattern(structure_text)

            if pattern:
                # Resolve the {scenes} placeholder to the workspace's scenes folder
                scenes_folder = self._scenes_folder()

                filtered_list = []
                # Create a copy of file_list to iterate over
                for f in list(file_list):
                    try:
                        # Get relative path of the file's directory
                        rel_dir = os.path.relpath(os.path.dirname(f), workspace_path)
                    except ValueError:
                        continue

                    base_name = os.path.splitext(os.path.basename(f))[0]

                    # Strip suffix if present and defined
                    if suffix_text and base_name.endswith(suffix_text):
                        name_for_path = base_name[: -len(suffix_text)]
                    else:
                        name_for_path = base_name

                    workspace_name = os.path.basename(workspace_path)
                    try:
                        expected_rel_dir = ptk.StrUtils.replace_placeholders(
                            pattern,
                            scenes=scenes_folder,
                            name=name_for_path,
                            workspace=workspace_name,
                            suffix=suffix_text,
                        )
                    except ValueError:
                        # Handle invalid format string (e.g. single '{' or '}')
                        # Just skip filtering for this file if pattern is invalid
                        continue

                    # Normalize paths for comparison (handle case sensitivity on Windows)
                    rel_dir_norm = os.path.normcase(os.path.normpath(rel_dir))
                    expected_rel_dir_norm = os.path.normcase(
                        os.path.normpath(expected_rel_dir)
                    )

                    # Check if rel_dir ends with expected_rel_dir (handling path separators)
                    # We split by separator to ensure we match full directory names
                    # This allows matching even if the file is deeper in the structure (e.g. inside 'scenes')
                    rel_parts = rel_dir_norm.split(os.sep)
                    exp_parts = expected_rel_dir_norm.split(os.sep)

                    if (
                        len(rel_parts) >= len(exp_parts)
                        and rel_parts[-len(exp_parts) :] == exp_parts
                    ):
                        filtered_list.append(f)
                file_list = filtered_list

        filter_text = self.ui.txt001.text().strip()

        # Check if filtering is enabled via option box action toggle
        filter_enabled = self._filter_enabled

        # Check if ignore case is enabled via checkbox
        ignore_case = getattr(self.ui, "chk_ignore_case", None)
        ignore_case = (
            ignore_case.isChecked() if ignore_case else True
        )  # Default to True if checkbox doesn't exist

        # Determine filter target from combobox
        cmb_filter_target = getattr(self.ui, "cmb_filter_target", None)
        filter_target = (
            cmb_filter_target.currentText() if cmb_filter_target else "Filter: All"
        )
        include_files = filter_target in ("Filter: All", "Filter: Files")
        include_notes = filter_target in ("Filter: All", "Filter: Notes")

        # Store filter state for post-population row visibility in update_table
        self._active_filter_text = filter_text if filter_enabled else ""
        self._active_ignore_case = ignore_case
        self._active_include_files = include_files
        self._active_include_notes = include_notes

        # Identify and include external references
        current_refs = self.current_references
        external_refs_paths = []

        # Get all files in current workspace to check against (unfiltered)
        full_workspace_files = self.workspace_files.get(workspace_path, [])
        full_workspace_files_set = set(
            os.path.normcase(os.path.normpath(f)) for f in full_workspace_files
        )

        for ref in current_refs:
            try:
                path = os.path.normcase(os.path.normpath(ref.path))
                # If path is not in the current workspace, it's external
                if path not in full_workspace_files_set:
                    # Avoid duplicates in external list
                    if path not in [
                        os.path.normcase(os.path.normpath(p))
                        for p in external_refs_paths
                    ]:
                        external_refs_paths.append(ref.path)
            except Exception:
                continue

        # Prepend external references to the file list
        if external_refs_paths:
            self.logger.debug(
                f"Adding {len(external_refs_paths)} external references to table."
            )
            file_list = external_refs_paths + file_list

        if not file_list:
            self.logger.warning(f"No scene files found in workspace: {workspace_path}")
        else:
            self.logger.debug(f"Found {len(file_list)} scenes to populate in table.")

        # Check display settings (suffix_text already read with the naming options above)
        hide_suffix = getattr(header_menu, "chk_hide_suffix", None)
        hide_suffix_enabled = hide_suffix.isChecked() if hide_suffix else False

        hide_extension = getattr(header_menu, "chk_hide_extension", None)
        hide_extension_enabled = hide_extension.isChecked() if hide_extension else False

        # Cross-DCC: also list the workspace's foreign scenes for each checked foreign type
        # (.blend). A foreign row's reference icon bakes it through the blender_bridge (headless
        # convert -> cached .ma) and references the result. Discovery uses the importer's own
        # scan, filtered to the checked extensions (mirror of the Blender panel's .ma/.mb listing).
        foreign_ext = {e for e in self.slot.FOREIGN_EXTENSIONS if e in included}
        if foreign_ext:
            from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

            existing = {os.path.normcase(os.path.normpath(p)) for p in file_list}
            for p in BlenderSceneImport.find_scenes(
                workspace_path, recursive=self.recursive_search
            ):
                if (
                    os.path.splitext(p)[1].lower() in foreign_ext
                    and os.path.normcase(os.path.normpath(p)) not in existing
                ):
                    file_list.append(p)

        # Generate file names, marking external references (a foreign-DCC origin is NOT tagged —
        # the user can reveal the extension to spot a .blend row, so a redundant "(Blender)" suffix
        # is omitted; the Blender panel likewise drops its "(Maya)" tag).
        file_names = []
        for f in file_list:
            name = self._display_name(
                f,
                hide_extension_enabled,
                suffix_text if hide_suffix_enabled else "",
            )

            if f in external_refs_paths:
                # Try to find the workspace name for the external reference
                try:
                    workspace_path = EnvUtils.find_workspace_using_path(f)
                    if workspace_path:
                        workspace_name = os.path.basename(workspace_path)
                        name = f"{name} ({workspace_name})"
                    else:
                        name = f"{name} (External)"
                except Exception:
                    name = f"{name} (External)"
            file_names.append(name)

        self.logger.debug(f"Updating table with {len(file_names)} files.")
        self.update_table(file_names, file_list)

    @block_table_selection_method
    def update_table(self, file_names, file_list):
        t = self.ui.tbl000
        t.setUpdatesEnabled(False)  # optimization: prevent repaints during update
        sorting_enabled = t.isSortingEnabled()
        t.setSortingEnabled(False)
        try:
            # Update row count to match new list size
            # This handles removal (truncation) and addition (extension) automatically
            t.setRowCount(len(file_names))

            _scene = cmds.file(q=True, sceneName=True) or ""
            current_scene = os.path.normcase(os.path.normpath(_scene)) if _scene else ""

            # Build a set of referenced file paths for fast lookup, plus a
            # mapping of paths to active display mode (off/reference/template).
            ref_path_set = set()
            display_mode_by_path = {}
            for ref in self.current_references:
                try:
                    norm = os.path.normcase(os.path.normpath(ref.path))
                except Exception:
                    continue
                mode = self.get_reference_display_mode(ref)
                # A foreign row references its BAKE, so key the reference by the source
                # scene the user sees too — otherwise the row reads as unreferenced.
                for key in filter(None, (norm, self._bake_source_key(ref.path))):
                    ref_path_set.add(key)
                    # If multiple refs share a path, prefer any non-off mode
                    if mode != "off" or key not in display_mode_by_path:
                        display_mode_by_path[key] = mode

            for row, (scene_name, file_path) in enumerate(zip(file_names, file_list)):
                item = t.item(row, 0)  # Files column is at index 0
                if not item:
                    # Get the full filename without stripping for rename functionality
                    full_filename = os.path.basename(file_path)
                    item = self.sb.QtWidgets.QTableWidgetItem(scene_name)
                    item.setFlags(item.flags() | self.sb.QtCore.Qt.ItemIsEditable)
                    t.setItem(row, 0, item)  # Files column is at index 0

                    # Store both the full file path and the full filename for rename functionality
                    item.setData(
                        self.sb.QtCore.Qt.UserRole, file_path
                    )  # Full file path
                    item.setData(
                        self.sb.QtCore.Qt.UserRole + 1, full_filename
                    )  # Full filename for rename
                    item.setData(
                        self.sb.QtCore.Qt.UserRole + 2, scene_name
                    )  # Display name

                item.setText(scene_name)
                # Update data attributes
                item.setData(self.sb.QtCore.Qt.UserRole, file_path)
                item.setData(
                    self.sb.QtCore.Qt.UserRole + 1, os.path.basename(file_path)
                )
                item.setData(self.sb.QtCore.Qt.UserRole + 2, scene_name)
                # The display label can hide the suffix/extension (and carries an
                # "(External)" tag), and a long name elides in the column, so the
                # tooltip always names the file in full.  Set before the branch
                # below: _format_table_item overrides it for the current scene, and
                # items are reused across refreshes -- a stale tooltip would linger.
                item.setToolTip(os.path.basename(file_path))

                is_foreign = self._is_foreign(file_path)
                if is_foreign:
                    # A foreign (Blender) row references through a BAKE, so it carries the
                    # same reference-toggle states as a native row. It must still stay out
                    # of the selection->reference sync (that path references the row's own
                    # path, which Maya cannot open), so the name stays non-selectable and
                    # non-editable — renaming the source would orphan its bake sidecar.
                    item.setFlags(
                        (item.flags() | self.sb.QtCore.Qt.ItemIsEnabled)
                        & ~self.sb.QtCore.Qt.ItemIsSelectable
                        & ~self.sb.QtCore.Qt.ItemIsEditable
                    )
                else:
                    self._format_table_item(item, file_path)
                    # Items are reused across refreshes; a row that previously held a
                    # non-editable foreign (.blend) file can be repurposed for a native
                    # scene after the toggle is turned off, so re-assert editability here
                    # (natives are renameable — only the foreign branch clears it).
                    item.setFlags(item.flags() | self.sb.QtCore.Qt.ItemIsEditable)

                # Set action column states. Currentness matches the Open toggle exactly (native
                # file, or a foreign row's open scratch bake) — reuse the single `current_scene`
                # snapshot via the slot's _is_current (single source of truth for currentness).
                # Short-circuit when nothing is open: no row is current, and it keeps the pure-Qt
                # update_table unit tests (which mock cmds.file -> "") off the live slot method.
                norm_fp = os.path.normcase(os.path.normpath(file_path))
                is_current = bool(current_scene) and self.slot._is_current(
                    file_path, current_scene
                )
                is_referenced = norm_fp in ref_path_set

                # Reference action column (index 1)
                if is_referenced:
                    t.actions.set(row, 1, "referenced")
                else:
                    t.actions.set(row, 1, "unreferenced")

                # Open action column (index 2) — a foreign row bakes + opens as a new scene
                # (see open_scene), so it carries the same visible/clickable Open states as a
                # native row (is_current is always False for a foreign row).
                t.actions.set(row, 2, "current" if is_current else "default")

                # Display mode action column (index 3): off/reference/template/unavailable
                if is_referenced:
                    disp_state = display_mode_by_path.get(norm_fp, "off")
                else:
                    disp_state = "unavailable"
                t.actions.set(row, 3, disp_state)

                # Column 4: Notes (Metadata)
                item_notes = t.item(row, 4)
                if not item_notes:
                    item_notes = self.sb.QtWidgets.QTableWidgetItem()
                    item_notes.setFlags(
                        item_notes.flags() | self.sb.QtCore.Qt.ItemIsEditable
                    )
                    t.setItem(row, 4, item_notes)

                # Store file path in notes item too for easy access during edit
                item_notes.setData(self.sb.QtCore.Qt.UserRole, file_path)

                try:  # Fetch metadata (Comments)
                    ptk.Metadata.enable_sidecar = True
                    ptk.Metadata.sidecar_only = True
                    metadata = ptk.Metadata.get(file_path, "Comments")
                    comments = metadata.get("Comments") or ""
                    if item_notes.text() != comments:
                        item_notes.setText(comments)
                except Exception:
                    pass

            # Apply table formatting
            t.apply_formatting()

            # Post-population text filter: hide rows where neither filename
            # nor notes match.  This runs AFTER metadata is fetched so notes
            # are available for matching.
            filter_text = getattr(self, "_active_filter_text", "")
            ignore_case = getattr(self, "_active_ignore_case", True)
            include_files = getattr(self, "_active_include_files", True)
            include_notes = getattr(self, "_active_include_notes", True)
            if filter_text:
                self.logger.debug(
                    f"Post-filter: applying '{filter_text}' to {t.rowCount()} rows"
                    f" (include_files={include_files}, include_notes={include_notes})"
                )

                # Split filter into individual patterns
                filter_patterns = [filter_text]
                for delim in (",", ";"):
                    expanded = []
                    for p in filter_patterns:
                        expanded.extend(s.strip() for s in p.split(delim) if s.strip())
                    filter_patterns = expanded

                for row in range(t.rowCount()):
                    item = t.item(row, 0)
                    notes_item = t.item(row, 4)
                    filename = (
                        os.path.basename(item.data(self.sb.QtCore.Qt.UserRole) or "")
                        if item
                        else ""
                    )
                    notes_text = notes_item.text() if notes_item else ""

                    name_match = (
                        self._text_matches_filter(
                            filename, filter_patterns, ignore_case
                        )
                        if include_files
                        else False
                    )
                    notes_match = (
                        self._matches_notes_filter(notes_text, filter_text, ignore_case)
                        if include_notes
                        else False
                    )
                    visible = name_match or notes_match
                    t.setRowHidden(row, not visible)
                    self.logger.debug(
                        f"  Row {row}: file='{filename}' notes='{notes_text}' "
                        f"name_match={name_match} notes_match={notes_match} "
                        f"visible={visible}"
                    )
            else:
                # No filter active — ensure all rows are visible
                for row in range(t.rowCount()):
                    t.setRowHidden(row, False)
        finally:
            t.setSortingEnabled(sorting_enabled)
            t.setUpdatesEnabled(True)  # Restore updates

    def _sync_reference_icons(self):
        """Update the reference (col 1) and display-mode (col 3) action icons for every row."""
        t = self.ui.tbl000
        path_to_refs = {}
        for ref in self.current_references:
            try:
                key = os.path.normcase(os.path.normpath(ref.path))
            except Exception:
                continue
            # Also key by the bake's source so a foreign row tracks its own reference.
            for k in filter(None, (key, self._bake_source_key(ref.path))):
                path_to_refs.setdefault(k, []).append(ref)

        for row in range(t.rowCount()):
            item = t.item(row, 0)
            if not item:
                continue
            file_path = item.data(self.sb.QtCore.Qt.UserRole)
            if not file_path:
                continue
            norm_fp = os.path.normcase(os.path.normpath(file_path))
            matched = path_to_refs.get(norm_fp, [])
            if matched:
                t.actions.set(row, 1, "referenced")
                # Prefer any non-off mode if refs disagree
                mode = "off"
                for r in matched:
                    m = self.get_reference_display_mode(r)
                    if m != "off":
                        mode = m
                        break
                t.actions.set(row, 3, mode)
            else:
                t.actions.set(row, 1, "unreferenced")
                t.actions.set(row, 3, "unavailable")

    def open_scene(self, file_path: str, set_workspace: bool = True):
        """Open a scene file, optionally setting the workspace to match the file.

        A USD row opens through mayaUsd's translator, read the way ``add_reference``
        reads it (:meth:`UsdUtils.live_read_options`) — a stage the reader would crash on
        is refused before the session is touched.

        Parameters:
            file_path (str): Path to the scene file to open
            set_workspace (bool): If True, sets the Maya workspace to the workspace
                                containing the opened file — for a foreign row, the
                                one containing its SOURCE scene (the scratch twin
                                lives in temp). Default is True.
        """
        self.logger.debug(f"Opening scene: {file_path}")

        # A foreign (Blender) scene has no Maya file to open, so bake it to a .ma (headless
        # Blender + mayapy) and open a throwaway copy as a new scene — the 'open' counterpart of
        # the link icon's bake-and-reference, and the mirror of blendertk's _open_foreign_as_new.
        # The scratch copy keeps the cached bake (reused for referencing) unedited.
        if self._is_foreign(file_path):
            baked = self.slot._bake_foreign_path(
                file_path
            )  # cached .ma (or None + its own error)
            if not baked:
                return False
            # Deterministic scratch twin so a second Open click resolves this row as
            # 'current' and closes it (see the slot's _is_current / _foreign_scratch_path);
            # the cached bake itself is opened only when the copy can't be written.
            try:
                scratch = _scratch_twins().create(file_path, baked)
            except OSError:
                scratch = baked
            # The scratch lives in temp, but the SCENE belongs to the source's project: set
            # the workspace from the source path (mirror of blendertk's pin), so textures /
            # scene dir / Save Scene resolve as the Blender coworker's do — not to temp.
            workspace_source = file_path
            file_path = scratch
        else:
            workspace_source = file_path

        if not os.path.exists(file_path):
            self.slot.logger.error(f"Scene file not found: {file_path}")
            self.sb.message_box(f"Scene file not found:<br>{file_path}")
            return False

        read = {}
        if self._is_usd(file_path):
            try:
                read = UsdUtils.live_read_options(file_path)
            except RuntimeError as e:
                return self._refuse_live_read(file_path, e)

        try:
            cmds.file(file_path, open=True, force=True, **read)
            conform = UsdUtils.stage_conform(file_path) if read else None
            if conform:  # a USD row in another unit or up axis
                roots = [
                    r
                    for r in UsdUtils.top_transforms(cmds.ls(assemblies=True))
                    if not any(
                        cmds.camera(s, q=True, startupCamera=True)
                        for s in cmds.listRelatives(r, shapes=True, type="camera") or []
                    )
                ]
                UsdUtils.conform_roots(
                    roots,
                    conform,
                    f"{os.path.splitext(os.path.basename(file_path))[0]}_conform",
                )
            # Loading a scene that carries references resolves/applies reference edits during
            # the open, which leaves Maya's scene 'modified' flag set even though the user made
            # no edits. That stale flag makes an immediate close/reference toggle falsely prompt
            # "unsaved changes — close anyway?" (via _confirm_discard_unsaved). We just loaded a
            # pristine file, so clear the load-time dirt now; interactive Maya applies some
            # reference edits deferred (after file() returns), so a second clear is scheduled on
            # idle at end-of-open — see the evalDeferred below. A genuine user edit re-sets the
            # flag and the discard guard still fires for real work.
            cmds.file(modified=False)
            self.logger.info(f"Opened scene: {file_path}")
            # The previous scene is gone: drop any untouched foreign scratch twin it was.
            _scratch_twins().discard_except(file_path)
        except Exception as e:
            self.logger.error(f"Failed to open scene: {e}")
            self.sb.message_box(
                f"Failed to open scene:<br>{file_path}<br><br>Error:<br>{e}"
            )
            return False

        # Set workspace based on the opened file's location (a foreign row: its source's)
        if set_workspace:
            try:
                new_workspace = EnvUtils.find_workspace_using_path(workspace_source)
                if new_workspace:
                    current_workspace = cmds.workspace(q=True, rd=True)
                    if os.path.normcase(
                        os.path.normpath(current_workspace)
                    ) != os.path.normcase(os.path.normpath(new_workspace)):
                        cmds.workspace(new_workspace, openWorkspace=True)
                        self.logger.info(f"Set workspace to: {new_workspace}")
                    else:
                        self.logger.debug("Workspace already correct")
                else:
                    self.logger.warning(f"No workspace found for: {workspace_source}")
            except Exception as e:
                self.logger.error(f"Failed to set workspace: {e}")

        # Second, deferred clear: interactive Maya applies deferred reference edits (and fires the
        # panel's own SceneOpened scriptJob refresh) AFTER this method returns, both of which re-set
        # the 'modified' flag on the just-loaded pristine scene. Re-clearing on the next idle — at
        # lowest priority so it runs after those callbacks — stops the discard guard from falsely
        # prompting on the following close/reference toggle. No-op in batch/standalone (no idle
        # loop), where the synchronous clear above already suffices.
        try:
            cmds.evalDeferred(lambda: cmds.file(modified=False), lowestPriority=True)
        except Exception as e:  # noqa: BLE001 — deferring is best-effort; the sync clear stands
            self.logger.debug(f"Deferred modified-clear could not be scheduled: {e}")

        return True

    def _refuse_live_read(self, file_path, error):
        """The engine's report, plus a message box: a click did nothing, so the user
        must learn why. Escaped — pxr's errors quote prim paths like ``</>``, which the
        box would read as markup — and naming Unlink and Import only for a stage the
        import CAN read (:class:`UsdReadRefused`); a damaged layer reads for neither."""
        super()._refuse_live_read(file_path, error)
        text = html.escape(str(error))
        if isinstance(error, UsdReadRefused):
            text += (
                "<br><br>Right-click the row and choose <b>Unlink and Import</b> "
                "to bring it in as local data instead."
            )
        self.sb.message_box(text)
        return False

    def new_scene(self):
        """Discard the current file and start an empty scene (Maya's ``file -new``).

        The 'close' counterpart of :meth:`open_scene` — Maya has no null document, so closing a
        scene means replacing it with a fresh empty one. Callers guard unsaved changes first
        (the slot's ``_confirm_discard_unsaved``); ``force=True`` here then skips Maya's own
        prompt. True on success. Mirror of blendertk's ``EnvUtils.new_scene``.
        """
        try:
            cmds.file(new=True, force=True)
            self.logger.info("Started a new (empty) scene.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to start a new scene: {e}")
            self.sb.message_box(f"Failed to close the scene:<br>{html.escape(str(e))}")
            return False

    @block_table_selection_method
    def unreference_all(self):
        self.logger.debug("Unreferencing all references.")
        failed = self.remove_references()
        self.refresh_file_list()
        # refresh_file_list now properly syncs selection after signals are unblocked
        if failed:
            # The table (just refreshed) shows these still selected, so saying nothing
            # would read as "Unreference All did nothing" — the exact confusion the
            # file-less reference node used to cause before it was screened out.
            names = "<br>".join(sorted(ref.label for ref in failed))
            self.sb.message_box(
                f"{len(failed)} reference(s) could not be removed:<br>{names}"
            )

    # The namespace button beside Unlink and Import All: STATE INDEX -> namespace_mode,
    # plus the glyph and tooltip each state shows. A click cycles in place — a
    # nested popup inside the header menu would fight it for the grab. uitk
    # persists the state by INDEX, so the order is APPEND-ONLY; index 0 (Remove)
    # is the long-standing default and the fallback.
    _UNLINK_NAMESPACE_STATES = (
        (
            "remove",
            "merge",
            "Namespace: Remove — merged into the scene; every node loses the prefix.",
        ),
        ("keep", "tag", "Namespace: Keep — every imported node stays prefixed."),
        (
            "root",
            "branch",
            "Namespace: Keep On Root — only the asset's top-level node(s) keep the "
            "prefix; everything below is merged into the scene.",
        ),
    )
    # Named in the confirm prompt so the choice is never a hidden setting.
    _UNLINK_MODE_LABELS = {
        "remove": "namespaces are <b>removed</b> — every node loses its prefix",
        "keep": "namespaces are <b>kept</b> on every imported node",
        "root": "namespaces are kept on the <b>top-level node(s) only</b>",
    }

    def _add_unlink_namespace_action(self, button):
        """Give *button* the cycling namespace-mode action (applies to BOTH unlinks)."""
        button.option_box.set_action(
            tooltip="Namespace handling on unlink",
            states=[
                {
                    "icon": icon,
                    "tooltip": f"{tooltip}\nApplies to every unlink. Click to cycle.",
                }
                for _mode, icon, tooltip in self._UNLINK_NAMESPACE_STATES
            ],
        )

    def _unlink_namespace_mode(self) -> str:
        """The namespace handling picked beside Unlink and Import All.

        Falls back to ``"remove"`` — the long-standing behaviour — whenever the menu
        isn't built yet (an early call).
        """
        from uitk.widgets.optionBox.options.action import ActionOption

        menu = getattr(getattr(self.ui, "header", None), "menu", None)
        button = getattr(menu, "btn_unlink_import_all", None) if menu else None
        action = button.option_box.find_option(ActionOption) if button else None
        index = action.current_state if action is not None else 0
        return self._UNLINK_NAMESPACE_STATES[
            index % len(self._UNLINK_NAMESPACE_STATES)
        ][0]

    @block_table_selection_method
    def unlink_all(self):
        self.logger.debug("Unlink all operation triggered.")
        mode = self._unlink_namespace_mode()
        if (
            self.sb.message_box(
                "<b>Warning:</b> The unlink operation is not undoable.<br>"
                f"On import, {self._UNLINK_MODE_LABELS[mode]}.<br>"
                "Do you want to proceed?",
                "Yes",
                "No",
            )
            != "Yes"
        ):
            self.logger.debug("Unlink operation cancelled by user.")
            return

        self.import_references(namespace_mode=mode, scene_data=self._ask_scene_data)
        self.refresh_file_list()
        self.logger.info(
            f"Unlinked all references (namespace: {mode}) and refreshed file list."
        )
        # refresh_file_list now properly syncs selection after signals are unblocked

    @block_table_selection_method
    def unlink_references(self, namespaces):
        """Unlink specific references."""
        if not namespaces:
            return

        count = len(namespaces)
        mode = self._unlink_namespace_mode()
        msg = (
            f"Unlink {count} reference(s)?<br>"
            f"On import, {self._UNLINK_MODE_LABELS[mode]}."
        )
        if self.sb.message_box(msg, "Yes", "No") != "Yes":
            return

        self.import_references(
            namespaces=namespaces,
            namespace_mode=mode,
            scene_data=self._ask_scene_data,
        )
        self.refresh_file_list()
        self.logger.info(f"Unlinked {count} references (namespace: {mode}).")

    def _ask_scene_data(self, summary, namespace):
        """The unlink question for a reference that brings scene data of its
        own (``import_references``' *scene_data*): merge it, drop it, or leave
        the reference linked.  Asked only when a merge would keep something."""
        lines = "".join(f"<br>&nbsp;&nbsp;&bull; {line}" for line in summary)
        answer = self.sb.message_box(
            f"<hl>{namespace}</hl> brings scene data of its own:{lines}<br><br>"
            "<b>Yes</b> merges it into this scene's -- nothing is lost, and anything "
            "renamed or re-slotted on the way is logged.<br>"
            "<b>No</b> drops it with the reference's data nodes.<br>"
            "<b>Cancel</b> leaves this reference linked.",
            "Yes",
            "No",
            "Cancel",
        )
        return {"Yes": "merge", "No": "discard"}.get(answer)

    @block_table_selection_method
    def convert_to_assembly(self):
        self.logger.debug("Convert to assembly operation triggered.")
        user_choice = self.sb.message_box(
            "<b>Warning:</b> The convert to assembly operation is not undoable.<br>Do you want to proceed?",
            "Yes",
            "No",
        )
        if user_choice == "Yes":
            self.logger.info("Converting references to assemblies.")
            AssemblyManager.convert_references_to_assemblies()
        else:
            self.logger.debug("Convert to assembly operation cancelled by user.")

    def _format_name(self, name, case_style="None", suffix=""):
        """Format a filename with case style and suffix."""
        # Strip 'Case: ' prefix if present
        if case_style and case_style.startswith("Case: "):
            case_style = case_style[6:]  # Remove 'Case: ' prefix

        if case_style and case_style != "None":
            try:
                name = ptk.StrUtils.set_case(name, case_style)
            except Exception as e:
                self.logger.warning(f"Failed to set case style {case_style}: {e}")

        if suffix:
            name += suffix

        return name

    def save_scene(self):
        """Save the current scene to the workspace, prompting for a name.

        The naming options (case / suffix / folder structure) come from the header
        menu's Naming section; the target path comes from
        :meth:`_resolve_save_target` — the same computation the button's live
        tooltip previews.
        """
        case_style, suffix, structure_text = self._naming_options()

        # Pre-populate with the current scene's name (suffix stripped so it isn't
        # double-appended, case formatting applied).
        default_name = self._default_save_name(case_style, suffix)

        name = self.sb.input_dialog("Save Scene", "Enter name for scene:", default_name)
        # The conventions append the suffix; one typed into the name would double.
        name = ptk.StrUtils.strip_suffix(name or "", [suffix])
        if not name:
            return

        pattern = self._normalize_subfolder_structure_pattern(structure_text)
        try:
            new_path = self._resolve_save_target(name, case_style, suffix, pattern)
        except ValueError as e:
            self.sb.message_box(html.escape(str(e)))
            return

        target_dir = os.path.dirname(new_path)
        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir)
            except OSError as e:
                self.sb.message_box(
                    f"Failed to create directory: {html.escape(str(e))}"
                )
                return

        if os.path.exists(new_path):
            if (
                self.sb.message_box(
                    f"File exists:<br>{new_path}<br>Overwrite?", "Yes", "No"
                )
                != "Yes"
            ):
                return

        try:
            cmds.file(rename=new_path)
            cmds.file(save=True, type="mayaAscii")
            self.logger.info(f"Saved scene to: {new_path}")
            self.refresh_file_list(invalidate=True)
        except Exception as e:
            self.sb.message_box(f"Failed to save scene: {html.escape(str(e))}")

    def _save_open_scene(self, path):
        """Flush the open scene to *path* so its unsaved edits reach disk.

        True when the file on disk is up to date (nothing to save, or the save succeeded);
        False (with the failure reported) when it isn't — the caller must then abort. Used by
        the rename flow (renaming a file out from under an unsaved session loses the edits at
        the next save) and by the unsaved-changes prompt's Save.
        """
        if not cmds.file(q=True, modified=True):
            return True
        try:
            file_type = EnvUtils.SCENE_SAVE_TYPES[os.path.splitext(path)[1].lower()]
            cmds.file(save=True, type=file_type)
            self.logger.info(f"Saved the open scene: {path}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save the open scene: {e}")
            self.sb.message_box(
                f"Failed to save the open scene:<br>{html.escape(str(e))}"
            )
            return False

    def _rename_scene_file(self, old_path, new_path, folder=None):
        """Rename a scene file on disk, carrying everything keyed to its old name: the sidecar
        metadata, Maya's incremental-save folder, and — with *folder* — the per-scene folder that
        holds it. Returns the final path, or None if the rename was aborted.

        Renaming the **open** scene is save-then-reopen: unsaved edits are flushed to the old
        file first (saving afterwards would just re-create the old name), and the renamed file is
        re-opened at the end. Without the reopen the session keeps pointing at a filename that no
        longer exists, so the next save silently resurrects the old file and the panel shows two
        scenes where the user renamed one.

        *folder* is the new name for the per-scene folder ({name} in the subfolder structure); it
        is applied only when the folder is the scene's alone: named for the file's base (which may
        carry a suffix the folder doesn't), not the project's own structure
        (:meth:`_is_workspace_structure`) and holding no other scene (:meth:`_holds_other_scenes`)
        — a folder that can't be renamed is reported, since the scene itself was. ``OSError``
        from the file rename propagates; a failed sidecar / increments move is logged and the
        file rename stands.
        """
        # Only Maya's own scene types round-trip through save-then-reopen. An .fbx or USD row
        # can also be the open scene (Maya opens both through their translators), but saving
        # it would write Maya scene data over it — those rename on disk only.
        ext = os.path.splitext(old_path)[1].lower()
        is_open = ext in EnvUtils.SCENE_SAVE_TYPES and self.slot._is_current(old_path)
        if is_open and not self._save_open_scene(old_path):
            return None

        os.rename(old_path, new_path)
        self.logger.info(f"Renamed {old_path} to {new_path}")

        old_sidecar = old_path + ".metadata.json"
        if os.path.exists(old_sidecar):
            new_sidecar = new_path + ".metadata.json"
            try:
                os.rename(old_sidecar, new_sidecar)
                self.logger.info(f"Renamed sidecar {old_sidecar} to {new_sidecar}")
            except OSError as e:
                self.logger.warning(f"Failed to rename sidecar: {e}")

        # Maya's Incremental Save keeps its versions in a folder beside the scene, keyed by the
        # scene's FILENAME. Left behind it detaches from the scene it belongs to — and a later
        # scene that reuses the old name silently inherits that history — so it moves along.
        old_increments = self._increments_dir(old_path)
        if os.path.isdir(old_increments):
            new_increments = self._increments_dir(new_path)
            try:
                os.rename(old_increments, new_increments)
                self.logger.info(
                    f"Moved incremental saves {old_increments} to {new_increments}"
                )
            except OSError as e:
                self.logger.warning(f"Failed to move the incremental-save folder: {e}")

        final_path = new_path
        if folder:
            old_dir = os.path.dirname(old_path)
            parent_dir_name = os.path.basename(old_dir)
            old_base = os.path.splitext(os.path.basename(old_path))[0]
            # The folder is the scene's own when it is named for it -- case-
            # insensitively, as Delete's ("hero_v01.ma" in "Hero") -- is not the
            # project's own structure, AND holds no other scene: the name test
            # alone let a loose "scenes_final.ma" claim the scenes root. The file
            # (and its increments) already moved.
            if (
                old_base.lower().startswith(parent_dir_name.lower())
                and not self._is_workspace_structure(old_dir)
                and not self._holds_other_scenes(old_dir, new_path)
            ):
                new_folder_path = os.path.join(os.path.dirname(old_dir), folder)
                skipped = None
                if os.path.exists(new_folder_path) and not ptk.FileUtils.is_same_file(
                    new_folder_path, old_dir
                ):
                    skipped = f"{new_folder_path} already exists"
                else:
                    try:
                        os.rename(old_dir, new_folder_path)
                        self.logger.info(
                            f"Renamed folder {old_dir} to {new_folder_path}"
                        )
                        final_path = os.path.join(
                            new_folder_path, os.path.basename(new_path)
                        )
                    except OSError as e:
                        skipped = str(e)
                if skipped:
                    # Report it: the scene itself WAS renamed, so staying silent leaves the file
                    # sitting under the old scene's folder with nothing to explain the mismatch.
                    self.logger.warning(f"Cannot rename folder {old_dir}: {skipped}")
                    self.sb.message_box(
                        f"Renamed the scene, but its folder kept the old name:<br>{skipped}"
                    )
            else:
                self.logger.debug(
                    f"Folder '{parent_dir_name}' is not '{old_base}''s alone (named "
                    "for another scene, or holds others); skipping its rename"
                )

        if is_open:
            # Re-open last, after any folder move, so the session lands on the final path.
            self.open_scene(final_path)

        return final_path

    def rename_scene(self):
        """Rename the scene file at the right-clicked row."""
        t = self.ui.tbl000
        row = self._context_menu_row
        if row is None or not (0 <= row < t.rowCount()):
            self.sb.message_box("No scene selected.")
            return
        item = t.item(row, 0)
        if item is None:
            self.sb.message_box("No scene selected.")
            return

        old_path = item.data(self.sb.QtCore.Qt.UserRole)
        if not old_path or not os.path.exists(old_path):
            self.sb.message_box("File not found.")
            return

        old_name = os.path.basename(old_path)
        old_base, ext = os.path.splitext(old_name)

        # Naming options; {name} in the folder structure means
        # per-scene folders, renamed alongside the file. The conventions append
        # the suffix, so neither the prefill nor the answer may carry it.
        case_style, suffix, structure_text = self._naming_options()
        use_folder = "{name}" in structure_text
        prefill = ptk.StrUtils.strip_suffix(old_base, [suffix])
        answer = self.sb.input_dialog("Rename Scene", "Enter new name:", prefill)
        # "Unchanged" is what was TYPED: "hero_v01" for an unsuffixed "hero.ma"
        # strips back to the prefill, yet asks for the suffix.
        if not answer or answer == prefill:
            return
        new_base = ptk.StrUtils.strip_suffix(answer, [suffix])
        if not new_base:
            return

        formatted_name = self._format_name(new_base, case_style, suffix)
        new_filename = formatted_name + ext
        if new_filename == old_name:
            return  # the conventions give back the name it already has

        old_dir = os.path.dirname(old_path)
        new_path = os.path.join(old_dir, new_filename)

        # A case-only rename's target "exists" on a case-insensitive file system:
        # it is the file itself.
        if os.path.exists(new_path) and not ptk.FileUtils.is_same_file(
            new_path, old_path
        ):
            self.sb.message_box(f"Target file exists: {new_path}")
            return

        # The per-scene folder ({name} in the structure) is renamed to the base without the
        # suffix — the file may carry one the folder doesn't.
        new_folder_name = (
            self._format_name(new_base, case_style, suffix="") if use_folder else None
        )

        try:
            if self._rename_scene_file(old_path, new_path, folder=new_folder_name):
                self.refresh_file_list(invalidate=True)
        except Exception as e:
            self.sb.message_box(f"Rename failed: {html.escape(str(e))}")

    @classmethod
    def _delete_prompt(cls, paths, permanent=()) -> str:
        """Confirmation text for deleting *paths*.

        Names each file in full: the row label can hide the suffix/extension, so a
        count alone ("Delete 1 file(s)?") gives no way to confirm WHICH file is about
        to be removed. And it says where each goes: the platform's trash
        (``ptk.FileUtils.trash_name``), or, for *permanent* -- the ones no trash
        would take (a network share, a removable drive) -- gone for good, which
        it says cannot be undone.

        Parameters:
            paths (list): Full paths of the files queued for deletion.
            permanent (list): Those of *paths* that would be deleted for good.

        Returns:
            str: HTML prompt naming the file(s).
        """
        trash = ptk.FileUtils.trash_name()
        gone = {os.path.normcase(os.path.abspath(p)) for p in permanent}
        marks = [os.path.normcase(os.path.abspath(p)) in gone for p in paths]
        names = [os.path.basename(p) for p in paths]
        if len(names) == 1:
            if marks[0]:
                return (
                    f"Delete <hl>{names[0]}</hl> permanently?<br>The {trash} "
                    "will not take it, so this cannot be undone."
                )
            return f"Move <hl>{names[0]}</hl> to the {trash}?"
        mixed = any(marks) and not all(marks)
        shown = list(zip(names, marks))[: cls.DELETE_PROMPT_MAX_NAMES]
        listed = "<br>".join(
            f"&bull; {n}" + (f" (permanently: no {trash})" if mark and mixed else "")
            for n, mark in shown
        )
        if len(names) > len(shown):
            listed += f"<br>&bull; ...and {len(names) - len(shown)} more"
        if all(marks):
            return (
                f"Delete {len(names)} file(s) permanently?<br>{listed}<br>The "
                f"{trash} will not take them, so this cannot be undone."
            )
        if mixed:
            return (
                f"Delete {len(names)} file(s)?<br>{listed}<br>The rest go to the "
                f"{trash}; the marked ones cannot be undone."
            )
        return f"Move {len(names)} file(s) to the {trash}?<br>{listed}"

    def delete_scene(self):
        """Delete the scene file at the right-clicked row: to the Recycle Bin
        (``ptk.FileUtils.move_to_trash``), or -- a drive with none, confirmed as
        permanent -- for good. A trash that refuses it after all asks again,
        as permanent, before anything is lost."""
        t = self.ui.tbl000
        row = self._context_menu_row
        if row is None or not (0 <= row < t.rowCount()):
            self.sb.message_box("No scene selected.")
            return
        item = t.item(row, 0)
        if item is None:
            self.sb.message_box("No scene selected.")
            return
        path = item.data(self.sb.QtCore.Qt.UserRole)
        files_to_delete = [path] if path and os.path.exists(path) else []

        if not files_to_delete:
            return

        permanent = [p for p in files_to_delete if not ptk.FileUtils.can_trash(p)]
        prompt = self._delete_prompt(files_to_delete, permanent)
        if self.sb.message_box(prompt, "Yes", "No") != "Yes":
            return

        # {name} in the folder structure = per-scene folders,
        # removed with their last scene below.
        _case_style, _suffix, structure_text = self._naming_options()
        use_folder = "{name}" in structure_text

        refused = [
            p
            for p in files_to_delete
            if self._remove_scene_file(p, p in permanent, use_folder) is None
        ]
        if refused and (
            self.sb.message_box(self._delete_prompt(refused, refused), "Yes", "No")
            == "Yes"
        ):
            for path in refused:
                self._remove_scene_file(path, True, use_folder)

        self.refresh_file_list()

    def _discard(self, path: str, permanent: bool) -> Optional[bool]:
        """*path* to the trash, or -- *permanent* -- deleted. True when it is
        gone, ``None`` when the trash would not take it (it is untouched)."""
        if permanent:
            os.remove(path)
            return True
        return None if ptk.FileUtils.move_to_trash(path) is None else True

    def _remove_scene_file(
        self, path: str, permanent: bool, use_folder: bool
    ) -> Optional[bool]:
        """Remove one scene (:meth:`delete_scene`) with its metadata sidecar,
        and its per-scene folder once that is empty.

        Returns:
            True when it is gone; ``None`` when the trash would not take it
            (nothing touched); False when it could not be removed (logged).
        """
        try:
            if self._discard(path, permanent) is None:
                return None
            where = (
                "Deleted" if permanent else f"Moved to the {ptk.FileUtils.trash_name()}"
            )
            self.logger.info(f"{where}: {path}")

            # The file's metadata sidecar goes with it (ptk.Metadata's
            # "<file>.metadata.json" convention), the same way, so it neither
            # outlives the scene it describes -- especially when sibling scenes
            # keep the folder alive below -- nor misses a restore of it.
            sidecar = path + ".metadata.json"
            if os.path.exists(sidecar):
                try:
                    if self._discard(sidecar, permanent) is None:
                        self.logger.warning(
                            f"Kept sidecar {sidecar}: no trash takes it."
                        )
                except OSError as e:
                    self.logger.warning(f"Could not remove sidecar {sidecar}: {e}")

            if use_folder:
                parent_dir = os.path.dirname(path)
                # The per-scene folder goes only when ALL of these hold (a
                # permanent delete cannot be taken back, and a restore from the
                # Recycle Bin puts the scene back where it was):
                # 1. Ownership: the folder is named for this scene (the
                #    per-scene layout of the {name} structure). Case-
                #    insensitive: Windows filenames -- "Env_v1.ma" in
                #    folder "env" is scene-owned.
                # 2. Not the project's own structure: "scenes_final.ma"
                #    loose in a scenes root named "scenes" passes (1), and
                #    must never take the root (or the workspace) with it.
                # 3. Empty, now the scene and its sidecar are gone. Anything
                #    left keeps it -- another version ("Env_v2.ma"), a
                #    subfolder, and every file that is not a Maya scene (an
                #    FBX or USD row of this same panel, a playblast): the
                #    prompt named one file, so rmdir, never rmtree.
                file_base = os.path.splitext(os.path.basename(path))[0].lower()
                parent_name = os.path.basename(os.path.normpath(parent_dir)).lower()
                owns_folder = bool(parent_name) and file_base.startswith(parent_name)
                try:
                    leftovers = os.listdir(parent_dir)
                except OSError:
                    leftovers = None  # cannot inspect: leave the folder alone

                if (
                    owns_folder
                    and leftovers == []
                    and not self._is_workspace_structure(parent_dir)
                ):
                    try:
                        os.rmdir(parent_dir)
                        self.logger.info(f"Deleted empty scene folder: {parent_dir}")
                    except OSError as e:
                        self.logger.warning(
                            f"Could not remove scene folder {parent_dir}: {e}"
                        )
                else:
                    self.logger.debug(
                        f"Keeping folder '{parent_dir}'; not scene-owned, the "
                        f"project's own structure, not empty, or it could not "
                        f"be inspected (owns={owns_folder}, left={leftovers})."
                    )

        except Exception as e:
            self.logger.error(f"Delete failed for {path}: {e}")
            return False
        return True
