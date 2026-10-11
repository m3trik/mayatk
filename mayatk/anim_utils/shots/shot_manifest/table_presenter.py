# !/usr/bin/python
# coding=utf-8
"""The Shot Manifest tree's rows: populating them (``ShotPairing.rows``: steps,
the members of their shots, the shots no step pairs with), formatting, colour
tags, assessment colouring and the behavior-label widgets.  The Start / End
cells are :mod:`.range_column`'s.

Mixed into :class:`~.shot_manifest_controller.ShotManifestController`.

Shared text: blendertk carries this module identical
(``m3trik/scripts/check_dcc_twins.py``); mayatk's is the one edited, then
copied over.  Only ``manifest_host.py`` differs between the two.
"""

import pythontk as ptk
from pythontk import BuilderObject, BuilderStep, StepStatus

from .behaviors import Behaviors
from .manifest_data import (
    BEHAVIOR_STATUS_COLORS,
    COL_BEHAVIORS,
    COL_DESC,
    COL_END,
    COL_START,
    COL_STEP,
    ERROR_COLOR,
    HEADERS,
    PASTEL_STATUS,
    STEP_ICON_COLOR,
    ManifestData,
)
from .manifest_host import ManifestHost

#: Tooltip line for an object the sheet's asset column did not list
#: (``BuilderObject.origin``).
_ORIGIN_NOTES = {
    "description": "Named in the step's description -- the asset column lists none.",
    "shot": "Auto-filled from the scene -- not listed in the sheet.",
    # The members of a shot shown under its row (``_add_member_rows``).
    "member": "In the step's shot, not listed in the manifest.",
    "orphan_member": "A member of this shot.",
}

#: Object statuses about a behavior (counted on the step's Behaviors cell).
_BEHAVIOR_ISSUES = (
    "missing_behavior",
    "behavior_conflict",
    "unknown_behavior",
    "stale_behavior",
)


class ManifestTableMixin:
    """Presentation methods for the manifest tree widget.

    Expects the host class to provide:

    - ``self.ui``  – the loaded UI with ``tbl_steps`` tree widget.
    - ``self._steps`` / ``self._sheet_steps``  – the steps shown, and the
      source's own.
    - ``self._last_results``  – last assessment result list.
    - ``self._is_built``  – whether shots have been built.
    - ``self._update_build_button()``  – button-state refresh.
    - ``self._set_footer(text, *, color)``  – footer label helper.
    - ``self._settings``  – :class:`SettingsManager` instance.
    - ``self._editor``  – the :class:`ManifestEditor` (Local Edits).
    - ``self._row_tags``  – the tree's ``RowTags`` (colour strip).
    - ``self._active_store()`` / ``self._active_mapping``  – the scene's
      store and the effective mapping template.
    - ``self._rows()``  – the table's ``(step, shot)`` rows (``ShotPairing.rows``).
    - ``self._locked_step_ids()``  – the steps whose shot is locked.
    - the Start / End column's ``_restore_user_ranges()`` /
      ``_refresh_timing()`` (``RangeColumnMixin`` / ``StoreFollowMixin``).
    """

    @property
    def _use_short_names(self) -> bool:
        """Whether to display leaf-only names instead of full DAG paths."""
        settings = getattr(self, "_settings", None)
        if settings is None:
            return True
        return not settings.value("long_names", False)

    @staticmethod
    def _row_key(item):
        """What a top-level row is across redraws: its step's ID (a rename
        changes the text, never the ID), else its text."""
        from qtpy.QtCore import Qt

        data = item.data(0, Qt.UserRole)
        return data.step_id if isinstance(data, BuilderStep) else item.text(0)

    def _row_icon(self, item) -> str:
        """The icon of a top-level row: a lock for a step whose shot is
        locked (final: Build leaves it, Local Edits refuse it), else a step."""
        from qtpy.QtCore import Qt

        data = item.data(0, Qt.UserRole)
        locked = getattr(self, "_locked_rows", ())
        if isinstance(data, BuilderStep) and data.step_id in locked:
            return "lock"
        return "step"

    def _save_tree_state(self):
        """Return expansion state and scroll position for later restore."""
        tree = self.ui.tbl_steps
        expanded = set()
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.isExpanded():
                expanded.add(self._row_key(item))
        scroll_val = tree.verticalScrollBar().value()
        return expanded, scroll_val

    def _restore_tree_state(self, state):
        """Re-expand items and restore scroll position from *state*."""
        expanded, scroll_val = state
        tree = self.ui.tbl_steps
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if self._row_key(item) in expanded:
                item.setExpanded(True)
        tree.verticalScrollBar().setValue(scroll_val)

    def _color_behavior_label(self, obj, label, step_id: str = None) -> None:
        """Set the label HTML and tooltip using the latest assessment data.

        Looks up the object in ``_last_results`` and colours broken
        behaviors accordingly.  Falls back to plain formatting when no
        assessment data is available.

        Parameters:
            step_id: When given, only that step's assessment is
                consulted — the same object can appear in several steps
                with different statuses (e.g. fade_in verified in A01,
                fade_out broken in A05), so a first-match scan would
                colour this row from another step's result.
        """
        broken: list = []
        stale: list = []
        status_color = None
        obj_st = ptk.StepStatus.find_object(
            getattr(self, "_last_results", None) or [], obj.name, step_id
        )
        if obj_st is not None:
            if obj_st.status == "missing_object":
                status_color = BEHAVIOR_STATUS_COLORS.get("error")
            else:
                broken = list(obj_st.broken_behaviors or [])
                stale = list(obj_st.stale_behaviors or [])
        off = self._disabled_behaviors()
        label.setText(
            ManifestData.format_behavior_html(
                obj.behaviors,
                broken=broken,
                status_color=status_color,
                stale=stale,
                disabled=off,
            )
        )
        # Build a per-behavior status tooltip
        if obj_st is not None and obj.behaviors:
            broken_set = set(obj_st.broken_behaviors or [])
            lines = []
            for b in obj.behaviors:
                display = ManifestData.fmt_behavior(b)
                if b in off:
                    lines.append(f"\u2013 {display}  (turned off -- header menu)")
                    continue
                if b in stale:
                    lines.append(
                        f"\u21bb {display}  (keyed under an older effect "
                        "recipe -- Build re-keys it)"
                    )
                    continue
                if obj_st.status == "missing_object":
                    lines.append(f"\u2716 {display}  (object missing)")
                elif b in broken_set:
                    lines.append(f"\u2716 {display}  (not verified)")
                else:
                    lines.append(f"\u2714 {display}")
            label.setToolTip("\n".join(lines))
        elif obj.behaviors:
            label.setToolTip(
                "\n".join(ManifestData.fmt_behavior(b) for b in obj.behaviors)
            )
        else:
            label.setToolTip("")

    def _make_behavior_label(
        self, obj, tree, child_item, choices, step_id: str = None
    ) -> None:
        """Create a clickable label for the Behaviors cell.

        The menu lists all behaviors available for this object's *kind*,
        plus any behaviours already assigned (so they remain toggle-able
        even when no YAML declares that kind).
        """
        from uitk.widgets.label import Label
        from qtpy.QtCore import Qt

        label = Label()
        label.setTextFormat(Qt.RichText)

        # Merge kind-filtered choices with the object's existing behaviors
        # so assigned behaviors are always present in the menu.
        merged = list(dict.fromkeys(list(choices) + list(obj.behaviors)))

        if not merged:
            tree.setItemWidget(child_item, COL_BEHAVIORS, label)
            return

        def _show_menu():
            from uitk.widgets.menu import Menu

            if self._editor.refuse_viewing():
                return
            step = next((s for s in self._steps if s.step_id == step_id), None)
            if self._editor.refuse_locked(step):
                return

            menu = Menu(
                parent=label,
                position="cursorPos",
                add_header=False,
                add_footer=False,
                hide_on_leave=True,
                fixed_item_height=20,
                match_parent_width=False,
            )
            cbs = []
            for raw_name in merged:
                if not raw_name:
                    continue
                display = ManifestData.fmt_behavior(raw_name)
                chk = menu.add("QCheckBox", setText=display)
                chk.setChecked(raw_name in obj.behaviors)
                chk.setProperty("behavior_raw", raw_name)
                cbs.append(chk)
            menu.on_hidden.connect(
                lambda: self._on_behaviors_changed(obj, cbs, step_id)
            )
            # One menu per click, parented to the label: else every click
            # left a hidden menu behind until the table was next drawn.
            menu.on_hidden.connect(menu.deleteLater)
            menu.show()

        self._color_behavior_label(obj, label, step_id=step_id)
        label.clicked.connect(_show_menu)
        tree.setItemWidget(child_item, COL_BEHAVIORS, label)

    def _on_behaviors_changed(self, obj, checkboxes, step_id=None) -> None:
        """Record the ticked behaviors as a Local Edit of *obj*
        (``ManifestEditor.set_behaviors``): the table redraws with them, and
        a Build is due -- it applies them, or releases the unticked ones' keys."""
        behaviors = [
            chk.property("behavior_raw") for chk in checkboxes if chk.isChecked()
        ]
        if behaviors != obj.behaviors:
            self._editor.set_behaviors(step_id, obj, behaviors)

    def _reapply_behavior(
        self, step_id: str, obj: BuilderObject, raise_errors: bool = False
    ) -> bool:
        """Re-apply every behavior of one object on its paired shot, as one
        undo step -- ``ShotManifest.reapply_object``: its previous keys out,
        the new ones recorded as the manifest's own.

        Parameters:
            raise_errors: Also raise why nothing was re-applied, for a caller
                that reports it itself (Render Effects' focused Key).

        Returns:
            Whether it was re-applied.  Why not goes on the footer: no shot
            pairs with the step yet, the scene holds no object of that name
            (or several), or an error.
        """
        try:
            store = self._active_store()
            shot = self._pairing().shots.get(step_id)
            if store is None or shot is None:
                problem = f"No shot pairs with '{step_id}' yet -- build first."
            else:
                with store.scene_edit("manifest_reapply"):
                    applied = self._manifest(store).reapply_object(shot, obj)
                if applied:
                    problem = None
                elif store.resolve_member(obj.name)[1] == "ambiguous":
                    problem = (
                        "Nothing re-applied: several scene objects are named "
                        f"'{obj.name}'."
                    )
                else:
                    problem = f"Nothing re-applied: '{obj.name}' is not in the scene."
            if problem is None:
                # Re-assess so the UI reflects the fixed state.  Skip the
                # selected-keys guard -- we just applied known behaviors and
                # only need a status refresh.
                self.assess(skip_key_check=True)
                return True
        except Exception as exc:
            self.logger.error("Apply behavior failed: %s", exc)
            self._set_footer(f"Error: {exc}", color=ERROR_COLOR)
            if raise_errors:
                raise
            return False
        self._set_footer(problem, color=ERROR_COLOR)
        if raise_errors:
            raise RuntimeError(problem)
        return False

    def _populate_table(self) -> None:
        """Fill the tree with the manifest's rows (``ShotPairing.rows``): a
        step row per step -- its objects, then the members of its shot it does
        not list -- and a "not in doc" row per shot no step pairs with, its
        members under it, where the timeline has it."""
        tree = self.ui.tbl_steps
        tree.clear()
        tree.setHeaderLabels(HEADERS)
        tree.setColumnCount(len(HEADERS))
        pairing = self._pairing()
        # The steps whose shot is locked: their rows draw a lock (_row_icon).
        self._locked_rows = self._locked_step_ids(pairing)

        _kind_cache: dict = {}

        for step, shot in self._rows(pairing):
            if step is None:
                self._add_shot_row(tree, shot)
                continue
            section = (
                f"{step.section}: {step.section_title}"
                if step.section_title
                else step.section
            )

            parent = tree.create_item(
                [step.shot_name, section, step.display_text, "", "", ""],
                data=step,
            )
            if pairing.how.get(step.step_id) == "order":
                parent.setToolTip(
                    COL_STEP,
                    f"Paired by timeline order with shot "
                    f"'{pairing.shots[step.step_id].name}'; Build records the pairing.",
                )
            # Child rows: object name in Description column, behavior label
            for obj in step.objects:
                display = (
                    ManifestHost.leaf_name(obj.name)
                    if self._use_short_names
                    else obj.name
                )
                child = tree.create_item(
                    ["", "", display, "", "", ""],
                    data=obj,
                    parent=parent,
                )
                if obj.kind == "audio":
                    font = child.font(COL_DESC)
                    font.setItalic(True)
                    for c in range(tree.columnCount()):
                        child.setFont(c, font)
                if display != obj.name:
                    child.setToolTip(COL_DESC, obj.name)
                origin = _ORIGIN_NOTES.get(obj.origin)
                if origin:
                    child.setToolTip(COL_DESC, f"{obj.name}\n{origin}")
                if obj.kind not in _kind_cache:
                    _kind_cache[obj.kind] = list(
                        Behaviors.list_behaviors(kind=obj.kind)
                    )
                self._make_behavior_label(
                    obj, tree, child, _kind_cache[obj.kind], step.step_id
                )
            if shot is not None:
                self._add_member_rows(
                    tree, parent, shot, listed=[o.name for o in step.objects]
                )

        # Restrict editability: only Range column on parent rows, and only
        # for steps that aren't built yet — per row, not all-or-nothing,
        # so new steps added to a partially built CSV stay editable.
        from qtpy.QtCore import Qt as _Qt

        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step = parent.data(0, _Qt.UserRole)
            editable = (
                isinstance(step, BuilderStep) and step.step_id not in pairing.shots
            )
            if editable:
                parent.setFlags(parent.flags() | _Qt.ItemIsEditable)
            else:
                parent.setFlags(parent.flags() & ~_Qt.ItemIsEditable)
            for j in range(parent.childCount()):
                child = parent.child(j)
                child.setFlags(child.flags() & ~_Qt.ItemIsEditable)

        self._shown_pairing = self._pairing_key(pairing)

        # Restore user-entered range values that survive table rebuilds,
        # then the built rows' live ranges: the store is their truth.
        self._restore_user_ranges(tree)
        self._refresh_timing()

        self._apply_formatting(tree)
        self._apply_row_tags(tree)
        tree.set_stretch_column(2)  # Stretch "Description" column
        tree.restore_column_state()  # Persist user header changes
        self._editor.decorate()

    def _add_shot_row(self, tree, shot):
        """An italic, non-editable "not in doc" row for *shot* -- a shot no
        step pairs with -- its members under it; its context menu adds it to
        the manifest, pairs it with a step, or removes it."""
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QBrush, QColor

        fg, _bg = PASTEL_STATUS.get("not_in_doc", (None, None))
        tip = StepStatus.HELP["not_in_doc"]
        row = tree.create_item(
            [
                shot.name,
                "",
                shot.description or "",
                "",
                f"{shot.start:.0f}",
                f"{shot.end:.0f}",
            ],
            data=shot,
        )
        row.setFlags(row.flags() & ~Qt.ItemIsEditable)
        font = row.font(COL_STEP)
        font.setItalic(True)
        for c in range(tree.columnCount()):
            row.setFont(c, font)
            row.setToolTip(c, tip)
            if fg:
                row.setForeground(c, QBrush(QColor(fg)))
        self._add_member_rows(tree, row, shot, in_step=False)
        return row

    def _add_member_rows(self, tree, parent, shot, listed=(), in_step=True) -> None:
        """A child row under *parent* per member of *shot* not among *listed*
        (names, compared as ``ShotStore.member_key`` does): the shot's own
        objects, shown before any Assess -- italic, never editable; under a
        step (*in_step*) in the ``additional`` colours: in the shot, not in
        the step.

        Each row's data is a ``BuilderObject`` of origin ``"member"``: the
        object rows' own actions (Outliner, Copy) reach it, Local Edits do
        not -- the sheet does not list it.
        """
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QBrush, QColor

        key = ptk.ShotStore.member_key
        seen = {key(n) for n in listed}
        a_fg, a_bg = (
            PASTEL_STATUS.get("additional", (None, None)) if in_step else (None, None)
        )
        tip = _ORIGIN_NOTES["member" if in_step else "orphan_member"]
        for name in shot.objects:
            if key(name) in seen:
                continue
            seen.add(key(name))
            display = ManifestHost.leaf_name(name) if self._use_short_names else name
            child = tree.create_item(
                ["", "", display, "", "", ""],
                data=BuilderObject(name=name, origin="member"),
                parent=parent,
            )
            child.setFlags(child.flags() & ~Qt.ItemIsEditable)
            font = child.font(COL_DESC)
            font.setItalic(True)
            for c in range(tree.columnCount()):
                child.setFont(c, font)
                if a_fg:
                    child.setForeground(c, QBrush(QColor(a_fg)))
                if a_bg:
                    child.setBackground(c, QBrush(QColor(a_bg)))
            child.setToolTip(COL_DESC, f"{name}\n{tip}" if display != name else tip)

    def _step_items(self, step_ids) -> list:
        """The step rows of *step_ids*, in table order."""
        from qtpy.QtCore import Qt

        wanted = set(step_ids)
        tree = self.ui.tbl_steps
        items = []
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            step = item.data(0, Qt.UserRole)
            if isinstance(step, BuilderStep) and step.step_id in wanted:
                items.append(item)
        return items

    def _apply_row_tags(self, tree) -> None:
        """Colour the step rows: the user's tags (the scene's, for the loaded
        sheet) over the template's automatic ``color_by``; an object row
        shows its step's (``RowTags`` inheritance).  A "not in doc" row takes
        the automatic colour of the step row above it: it sits inside that
        span of the timeline, and a gap in the strip there read as a section
        boundary."""
        from qtpy.QtCore import Qt

        tags = self._row_tags
        user = ptk.ManifestTags.read(self._active_store(), self._editor.source_key)
        rule = (self._active_mapping or {}).get("color_by", "none")
        auto = ptk.ManifestTags.auto(
            [*self._sheet_steps, *self._steps], rule, tags.slots
        )
        above = None
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            step = item.data(0, Qt.UserRole)
            if not isinstance(step, BuilderStep):
                if above:
                    tags.set_tag([item], above, auto=True)
                continue
            if step.step_id in user:
                tags.set_tag([item], user[step.step_id])
            above = auto.get(step.step_id)
            if above:
                tags.set_tag([item], above, auto=True)

    def _apply_formatting(self, tree) -> None:
        """Set column/row tints, behavior colors, icons, and column widths."""
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor

        content_col = COL_DESC

        # Row tints via delegate (fillRect bypasses the host's QSS stripping).
        tree._child_row_color = QColor(0, 0, 0, 55)

        # Column tints — darken Step and Behaviors columns
        tree.clear_column_tints()
        tree.set_column_tint(COL_STEP, QColor(0, 0, 0, 45))
        tree.set_column_tint(COL_BEHAVIORS, QColor(0, 0, 0, 45))

        # Icons: step icon on parents, type-coded icon on child Content column
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            tree.set_item_icon(parent, self._row_icon(parent), color=STEP_ICON_COLOR)
            for j in range(parent.childCount()):
                child = parent.child(j)
                obj_name = child.text(content_col)
                if not obj_name:
                    continue
                obj_data = child.data(0, Qt.UserRole)
                icon = ManifestHost.object_icon(obj_data, obj_name)
                if icon is not None:
                    child.setIcon(content_col, icon)
                else:
                    # Neutral grey before assessment; assessment will repaint
                    # with the actual status color if there's a problem.
                    tree.set_item_type_icon(
                        child, "close", column=content_col, color=STEP_ICON_COLOR
                    )

        # Column widths
        header = tree.header()
        header.setMinimumSectionSize(60)
        header.resizeSection(COL_STEP, 140)
        header.resizeSection(COL_BEHAVIORS, 110)
        header.resizeSection(COL_START, 55)
        header.resizeSection(COL_END, 55)

        # Run registered formatters
        tree.apply_formatting()

    def _apply_assessment(self, results: list) -> None:
        """Walk tree items and apply pastel colors + tooltips from results."""
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        tree = self.ui.tbl_steps
        col_count = tree.columnCount()
        content_col = COL_DESC
        beh_col = COL_BEHAVIORS
        # Fresh results answer for every Local Edit made before them.
        self._edited_since_assess = False

        status_map = {r.step_id: r for r in results}

        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if not isinstance(step_data, BuilderStep):
                continue
            step_status = status_map.get(step_data.step_id)
            if step_status is None:
                continue

            # Parent tooltip
            if step_status.status == "missing_shot":
                parent.setToolTip(0, "Shot not built in sequencer")
            elif step_status.status == "missing_object":
                names = [o.name for o in step_status.objects if not o.exists]
                parent.setToolTip(0, f"Missing: {', '.join(names)}")
            elif step_status.status == "missing_behavior":
                lines = []
                for o in step_status.objects:
                    if o.status != "missing_behavior":
                        continue
                    broken = ", ".join(
                        ManifestData.fmt_behavior(b)
                        for b in (o.broken_behaviors or o.behaviors)
                    )
                    lines.append(f"{o.name}: {broken}")
                parent.setToolTip(0, "Unverified behaviors:\n" + "\n".join(lines))
            elif step_status.status in StepStatus.HELP:
                parent.setToolTip(0, StepStatus.HELP[step_status.status])

            if step_status.shrinkable_frames > 0:
                existing_tip = parent.toolTip(0) or ""
                shrink_tip = f"{step_status.shrinkable_frames:.0f}f unused"
                parent.setToolTip(
                    0, f"{existing_tip}\n{shrink_tip}" if existing_tip else shrink_tip
                )
            if step_status.dropped_behaviors:
                existing_tip = parent.toolTip(0) or ""
                dropped = ", ".join(
                    f"{name} \u2192 {ManifestData.fmt_behavior(b)}"
                    for name, b in step_status.dropped_behaviors
                )
                dropped_tip = (
                    f"Keys of dropped behaviors -- Build removes them: {dropped}"
                )
                parent.setToolTip(
                    0, f"{existing_tip}\n{dropped_tip}" if existing_tip else dropped_tip
                )

            # Recolor step icon and step text to reflect status
            fg_hex, _ = PASTEL_STATUS.get(step_status.status, (None, None))
            icon_color = fg_hex or STEP_ICON_COLOR
            tree.set_item_icon(parent, self._row_icon(parent), color=icon_color)
            if fg_hex:
                parent.setForeground(COL_STEP, QBrush(QColor(fg_hex)))
            else:
                parent.setForeground(COL_STEP, QBrush())

            # Color parent behavior column if any child has a behavior issue
            beh_issues = [
                o for o in step_status.objects if o.status in _BEHAVIOR_ISSUES
            ]
            if beh_issues:
                only_stale = all(o.status == "stale_behavior" for o in beh_issues)
                b_fg, b_bg = PASTEL_STATUS[
                    "stale_behavior" if only_stale else "missing_behavior"
                ]
                if b_fg:
                    parent.setForeground(beh_col, QBrush(QColor(b_fg)))
                if b_bg:
                    parent.setBackground(beh_col, QBrush(QColor(b_bg)))
                only_missing = all(o.status == "missing_behavior" for o in beh_issues)
                word = (
                    "to re-key"
                    if only_stale
                    else ("missing" if only_missing else "to check")
                )
                parent.setText(beh_col, f"{len(beh_issues)} {word}")
                lines = [
                    f"{o.name}  \u2192  {', '.join(ManifestData.fmt_behavior(b) for b in (o.broken_behaviors or o.stale_behaviors or o.behaviors))}"
                    for o in beh_issues
                ]
                parent.setToolTip(beh_col, "\n".join(lines))

            # Color child rows — only problem statuses
            obj_status_map = {o.name: o for o in step_status.objects}
            for j in range(parent.childCount()):
                child = parent.child(j)
                child_data = child.data(0, Qt.UserRole)
                if not isinstance(child_data, BuilderObject):
                    continue
                obj_st = obj_status_map.get(child_data.name)
                if obj_st is None:
                    continue

                # Refresh behavior label to highlight broken behaviors
                if obj_st.behaviors:
                    beh_widget = tree.itemWidget(child, beh_col)
                    if beh_widget is not None:
                        self._color_behavior_label(
                            child_data, beh_widget, step_id=step_status.step_id
                        )

                if obj_st.status == "valid":
                    # Re-resolve icon: initial formatting may have set a
                    # fallback X because the node didn't exist yet
                    # (common for audio clips before build).
                    icon = ManifestHost.object_icon(child_data, child.text(content_col))
                    if icon is not None:
                        child.setIcon(content_col, icon)
                    continue

                c_fg, c_bg = PASTEL_STATUS.get(obj_st.status, (None, None))
                if c_fg:
                    brush = QBrush(QColor(c_fg))
                    for c in range(col_count):
                        child.setForeground(c, brush)
                if c_bg:
                    bg = QBrush(QColor(c_bg))
                    for c in range(col_count):
                        child.setBackground(c, bg)

                if obj_st.status == "missing_object":
                    child.setToolTip(
                        content_col, f"Object not found in {ManifestHost.APP}"
                    )
                elif obj_st.status == "missing_behavior":
                    lines = []
                    for b in obj_st.broken_behaviors or obj_st.behaviors:
                        desc = ""
                        try:
                            desc = Behaviors.load_behavior(b).get("description", "")
                        except Exception:
                            pass
                        entry = ManifestData.fmt_behavior(b)
                        if desc:
                            entry += f" \u2014 {desc}"
                        lines.append(entry)
                    child.setToolTip(content_col, "Unverified:\n" + "\n".join(lines))
                elif obj_st.status in StepStatus.HELP:
                    child.setToolTip(content_col, StepStatus.HELP[obj_st.status])
                elif obj_st.status == "user_animated" and obj_st.key_range:
                    child.setToolTip(
                        content_col,
                        f"User-animated: keys {obj_st.key_range[0]:.0f}-{obj_st.key_range[1]:.0f}",
                    )

            # Additional objects (in shot but not in CSV): the shot's members
            # are listed already (_add_member_rows); what animates in its
            # range without being one is added.
            key = ptk.ShotStore.member_key
            listed = {
                key(parent.child(j).data(0, Qt.UserRole).name)
                for j in range(parent.childCount())
                if isinstance(parent.child(j).data(0, Qt.UserRole), BuilderObject)
            }
            extras = [n for n in step_status.additional_objects if key(n) not in listed]
            if extras:
                a_fg, a_bg = PASTEL_STATUS.get("additional", (None, None))
                for extra_name in extras:
                    display = (
                        ManifestHost.leaf_name(extra_name)
                        if self._use_short_names
                        else extra_name
                    )
                    extra_item = tree.create_item(
                        ["", "", display, "", "", ""],
                        parent=parent,
                    )
                    tip = "Unexpected: object is in the shot but not listed in the manifest CSV."
                    if display != extra_name:
                        tip = f"{extra_name}\n{tip}"
                    extra_item.setToolTip(content_col, tip)
                    # Italic font to visually distinguish from CSV objects
                    font = extra_item.font(content_col)
                    font.setItalic(True)
                    for c in range(col_count):
                        extra_item.setFont(c, font)
                    if a_fg:
                        brush = QBrush(QColor(a_fg))
                        for c in range(col_count):
                            extra_item.setForeground(c, brush)
                    if a_bg:
                        bg = QBrush(QColor(a_bg))
                        for c in range(col_count):
                            extra_item.setBackground(c, bg)
                    icon = ManifestHost.object_icon(None, extra_name)
                    if icon is not None:
                        extra_item.setIcon(content_col, icon)

        self._editor.decorate()

    def expand_missing(self) -> None:
        """Expand all step rows that have missing objects, behaviors, or additional objects."""
        if not self._last_results:
            self._set_footer("Build first to detect issues.")
            return

        problem_ids = set()
        lines: list[str] = []
        for r in self._last_results:
            issues: list[str] = []
            if r.status == "missing_shot":
                issues.append("shot not built")
            elif r.status == "missing_object":
                missing = [o.name for o in r.objects if not o.exists]
                if missing:
                    issues.append(f"missing objects: {', '.join(missing)}")
            elif r.status == "missing_behavior":
                no_beh = [o.name for o in r.objects if o.status == "missing_behavior"]
                if no_beh:
                    issues.append(f"missing behaviors: {', '.join(no_beh)}")
            elif r.status == "stale_behavior":
                stale = [o.name for o in r.objects if o.status == "stale_behavior"]
                if stale:
                    issues.append(f"keyed under an older recipe: {', '.join(stale)}")
            if r.dropped_behaviors:
                issues.append(
                    "dropped behaviors' keys: "
                    + ", ".join(f"{name} {b}" for name, b in r.dropped_behaviors)
                )
            if r.additional_objects:
                issues.append(f"additional objects: {', '.join(r.additional_objects)}")
            if issues:
                problem_ids.add(r.step_id)
                lines.append(f"  {r.step_id}: {'; '.join(issues)}")

        if not problem_ids:
            self._set_footer("No issues found.")
            return

        self.logger.info("Expand Missing (%d steps):\n%s", len(lines), "\n".join(lines))
        self._set_footer(f"{len(problem_ids)} step(s) with issues expanded.")

        for item in self._step_items(problem_ids):
            item.setExpanded(True)

    def expand_extra(self) -> None:
        """Expand all step rows that have scene-discovered extra objects."""
        if not self._last_results:
            self._set_footer("Assess or build first to detect extra objects.")
            return

        extra_ids = {r.step_id for r in self._last_results if r.additional_objects}
        if not extra_ids:
            self._set_footer("No extra objects found.")
            return

        for item in self._step_items(extra_ids):
            item.setExpanded(True)
