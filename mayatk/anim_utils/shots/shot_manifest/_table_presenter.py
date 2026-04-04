# !/usr/bin/python
# coding=utf-8
"""Tree-widget presentation mixin for the Shot Manifest controller.

Groups every method that directly reads or writes QTreeWidget items:
table population, formatting, range auto-fill display, collision
painting, assessment colouring, and behavior-label widgets.

Mixed into :class:`ShotManifestController` via MRO.
"""
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    BuilderStep,
    BuilderObject,
)
from mayatk.anim_utils.shots.shot_manifest.behaviors import list_behaviors
from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
    BEHAVIOR_STATUS_COLORS,
    ERROR_COLOR,
    HEADERS,
    STEP_ICON_COLOR,
    PASTEL_STATUS,
    COL_STEP,
    COL_SECTION,
    COL_DESC,
    COL_BEHAVIORS,
    COL_START,
    COL_END,
    fmt_behavior,
    format_behavior_html,
    short_name,
    try_load_maya_icons,
)


class ManifestTableMixin:
    """Presentation methods for the manifest tree widget.

    Expects the host class to provide:

    - ``self.ui``  – the loaded UI with ``tbl_steps`` tree widget.
    - ``self._steps``  – current list of :class:`BuilderStep`.
    - ``self._user_ranges``  – dict of user-entered range overrides.
    - ``self._last_resolved``  – last resolved range list.
    - ``self._last_results``  – last assessment result list.
    - ``self._is_built``  – whether shots have been built.
    - ``self._resolve_ranges()``  – range resolution entry point.
    - ``self._update_build_button()``  – button-state refresh.
    - ``self._set_footer(text, *, color)``  – footer label helper.
    - ``self._settings``  – :class:`SettingsManager` instance.
    """

    # -- display settings --------------------------------------------------

    @property
    def _use_short_names(self) -> bool:
        """Whether to display leaf-only names instead of full DAG paths."""
        settings = getattr(self, "_settings", None)
        if settings is None:
            return True
        return not settings.value("long_names", False)

    # -- tree state save / restore -----------------------------------------

    def _save_tree_state(self):
        """Return expansion state and scroll position for later restore."""
        tree = self.ui.tbl_steps
        expanded = set()
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.isExpanded():
                expanded.add(item.text(0))  # step_id column
        scroll_val = tree.verticalScrollBar().value()
        return expanded, scroll_val

    def _restore_tree_state(self, state):
        """Re-expand items and restore scroll position from *state*."""
        expanded, scroll_val = state
        tree = self.ui.tbl_steps
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.text(0) in expanded:
                item.setExpanded(True)
        tree.verticalScrollBar().setValue(scroll_val)

    # -- behavior label widgets --------------------------------------------

    def _make_behavior_label(self, obj, tree, child_item, choices) -> None:
        """Create a Label with checkable menu for multi-behavior selection."""
        from uitk.widgets.label import Label
        from qtpy.QtCore import Qt

        label = Label()
        label.configure_menu(trigger_button="left")
        label.setTextFormat(Qt.RichText)

        checkboxes = []
        for raw_name in choices:
            if not raw_name:
                continue
            display = fmt_behavior(raw_name)
            chk = label.menu.add(
                "QCheckBox",
                setText=display,
                setChecked=(raw_name in obj.behaviors),
            )
            chk.toggled.connect(
                lambda checked, o=obj, lbl=label, cbs=checkboxes: (
                    self._on_behaviors_changed(o, lbl, cbs)
                )
            )
            chk.setProperty("behavior_raw", raw_name)
            checkboxes.append(chk)

        label.setText(format_behavior_html(obj.behaviors))
        tree.setItemWidget(child_item, COL_BEHAVIORS, label)

    def _on_behaviors_changed(self, obj, label, checkboxes) -> None:
        """Update BuilderObject.behaviors when checkboxes change."""
        obj.behaviors = [
            chk.property("behavior_raw") for chk in checkboxes if chk.isChecked()
        ]
        label.setText(format_behavior_html(obj.behaviors))
        self._update_build_button()

    def _reapply_behavior(self, step_id: str, obj: BuilderObject) -> None:
        """Re-apply all behaviors for a single object on its built shot."""
        try:
            from mayatk.anim_utils.shots._shots import ShotStore
            from mayatk.anim_utils.shots.shot_manifest.behaviors import apply_behavior

            shot = next(
                (s for s in ShotStore.active().shots if s.name == step_id), None
            )
            if shot is None:
                self._set_footer(
                    f"Shot '{step_id}' not found in store.", color=ERROR_COLOR
                )
                return

            for b in obj.behaviors:
                apply_behavior(obj.name, b, shot.start, shot.end)

            # Re-assess so the UI reflects the fixed state.
            # assess() sets its own footer summary.
            self.assess()
        except Exception as exc:
            self.logger.error("Apply behavior failed: %s", exc)
            self._set_footer(f"Error: {exc}", color=ERROR_COLOR)

    # -- table population --------------------------------------------------

    def _populate_table(self) -> None:
        """Fill the TreeWidget with parsed steps and expandable object rows."""
        tree = self.ui.tbl_steps
        tree.clear()
        tree.setHeaderLabels(HEADERS)
        tree.setColumnCount(len(HEADERS))

        behavior_choices = list(list_behaviors())

        for step in self._steps:
            section = (
                f"{step.section}: {step.section_title}"
                if step.section_title
                else step.section
            )

            parent = tree.create_item(
                [step.step_id, section, step.display_text, "", "", ""],
                data=step,
            )
            # Child rows: object name in Description column, behavior label
            for obj in step.objects:
                display = short_name(obj.name) if self._use_short_names else obj.name
                child = tree.create_item(
                    ["", "", display, "", "", ""],
                    data=obj,
                    parent=parent,
                )
                if display != obj.name:
                    child.setToolTip(COL_DESC, obj.name)
                self._make_behavior_label(obj, tree, child, behavior_choices)

        # Restrict editability: only Range column on parent rows, pre-build
        from qtpy.QtCore import Qt as _Qt

        editable = not self._is_built
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            if editable:
                parent.setFlags(parent.flags() | _Qt.ItemIsEditable)
            else:
                parent.setFlags(parent.flags() & ~_Qt.ItemIsEditable)
            for j in range(parent.childCount()):
                child = parent.child(j)
                child.setFlags(child.flags() & ~_Qt.ItemIsEditable)

        # Restore user-entered range values that survive table rebuilds
        self._restore_user_ranges(tree)

        self._apply_formatting(tree)
        tree.set_stretch_column(2)  # Stretch "Description" column
        tree.restore_column_state()  # Persist user header changes

    # -- formatting --------------------------------------------------------

    def _apply_formatting(self, tree) -> None:
        """Set column/row tints, behavior colors, icons, and column widths."""
        from qtpy.QtGui import QColor

        content_col = COL_DESC

        # Row tints via delegate (fillRect bypasses Maya QSS stripping).
        tree._child_row_color = QColor(0, 0, 0, 55)

        # Column tints — darken Step and Behaviors columns
        tree.clear_column_tints()
        tree.set_column_tint(COL_STEP, QColor(0, 0, 0, 45))
        tree.set_column_tint(COL_BEHAVIORS, QColor(0, 0, 0, 45))

        # Icons: step icon on parents, type-coded icon on child Content column
        node_icons_cls = try_load_maya_icons()
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            tree.set_item_icon(parent, "step", color=STEP_ICON_COLOR)
            for j in range(parent.childCount()):
                child = parent.child(j)
                obj_name = child.text(content_col)
                if not obj_name:
                    continue
                maya_icon = (
                    node_icons_cls.get_icon(obj_name) if node_icons_cls else None
                )
                if maya_icon is not None:
                    child.setIcon(content_col, maya_icon)
                else:
                    # Neutral grey before assessment; assessment will repaint
                    # with the actual status color if there's a problem.
                    tree.set_item_type_icon(
                        child, "close", column=content_col, color=STEP_ICON_COLOR
                    )

        # Column widths
        from qtpy.QtWidgets import QHeaderView

        header = tree.header()
        header.setMinimumSectionSize(60)
        header.resizeSection(COL_STEP, 140)
        header.resizeSection(COL_BEHAVIORS, 110)
        header.resizeSection(COL_START, 55)
        header.resizeSection(COL_END, 55)

        # Run registered formatters
        tree.apply_formatting()

    # -- range display (auto-fill / revert / restore) ----------------------

    def _auto_fill_ranges(self, resolved=None) -> list:
        """Auto-fill the Range column using resolved ranges.

        User-entered values are preserved; auto-filled values appear dim
        and italic.

        Parameters
        ----------
        resolved
            Pre-computed resolved ranges.  When ``None``,
            :meth:`_resolve_ranges` is called internally.

        Returns
        -------
        list
            The resolved ranges list (for reuse by collision validation).
        """
        if resolved is None:
            resolved = self._resolve_ranges()
        if not resolved:
            return resolved

        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush, QFont

        tree = self.ui.tbl_steps
        dim = QBrush(QColor(PASTEL_STATUS["locked"][0]))
        step_map = {r[0]: r for r in resolved}

        tree.blockSignals(True)
        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                step_data = parent.data(0, Qt.UserRole)
                if not isinstance(step_data, BuilderStep):
                    continue
                entry = step_map.get(step_data.step_id)
                if entry is None:
                    continue
                step_id, start, end, is_user = entry
                parent.setText(COL_START, f"{start:.0f}")
                if end is not None:
                    parent.setText(COL_END, f"{end:.0f}")
                else:
                    parent.setText(COL_END, "")
                for col in (COL_START, COL_END):
                    font = parent.font(col)
                    if not is_user:
                        parent.setForeground(col, dim)
                        font.setItalic(True)
                    else:
                        font.setItalic(False)
                    parent.setFont(col, font)
        finally:
            tree.blockSignals(False)
        return resolved

    def _validate_range_collisions(self, resolved=None) -> int:
        """Check adjacent ranges for ordering violations and color conflicts.

        Resets Start/End column foreground on all items, then recolors
        collision participants in pastel red.

        Returns the number of collisions found.
        """
        if resolved is None:
            resolved = self._resolve_ranges()
        if len(resolved) < 2:
            return 0

        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        tree = self.ui.tbl_steps
        c_fg, c_bg = PASTEL_STATUS["collision"]
        collision_fg = QBrush(QColor(c_fg))
        collision_bg = QBrush(QColor(c_bg))
        dim = QBrush(QColor(PASTEL_STATUS["locked"][0]))
        collisions = 0

        # Build a map of step_id → tree item for quick lookup
        item_map: dict = {}
        resolved_map: dict = {}
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep):
                item_map[step_data.step_id] = parent
        for r in resolved:
            resolved_map[r[0]] = r

        # Block signals to prevent _on_item_changed from firing
        # recursively while we update foregrounds/tooltips.
        tree.blockSignals(True)
        try:
            # First pass: reset foreground, background, and tooltip for all range cells
            for sid, item in item_map.items():
                entry = resolved_map.get(sid)
                is_user = entry[3] if entry else False
                brush = QBrush() if is_user else dim
                for col in (COL_START, COL_END):
                    item.setForeground(col, brush)
                    item.setBackground(col, QBrush())
                    item.setToolTip(col, "")

            # Second pass: mark collision items
            for i in range(len(resolved) - 1):
                curr_id, curr_start, curr_end, _ = resolved[i]
                next_id, next_start, _, _ = resolved[i + 1]
                effective_end = curr_end if curr_end is not None else curr_start
                if effective_end > next_start:
                    collisions += 1
                    for sid in (curr_id, next_id):
                        item = item_map.get(sid)
                        if item is not None:
                            for col in (COL_START, COL_END):
                                item.setForeground(col, collision_fg)
                                item.setBackground(col, collision_bg)
                                item.setToolTip(
                                    col,
                                    "Range collision: overlaps with adjacent step",
                                )
        finally:
            tree.blockSignals(False)

        return collisions

    def _revert_range_cell(self, item, step_id: str) -> None:
        """Revert Start/End cells to their last resolved values after a rejected edit."""
        tree = self.ui.tbl_steps
        tree.blockSignals(True)
        for entry in self._last_resolved:
            if entry[0] == step_id:
                _, s, e, _ = entry
                item.setText(COL_START, f"{s:.0f}")
                item.setText(COL_END, f"{e:.0f}" if e is not None else "")
                break
        else:
            item.setText(COL_START, "")
            item.setText(COL_END, "")
        tree.blockSignals(False)

    def _restore_user_ranges(self, tree) -> None:
        """Write ``_user_ranges`` values back into Start/End cells after a table rebuild."""
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        dim = QBrush(QColor(PASTEL_STATUS["locked"][0]))
        tree.blockSignals(True)
        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                step_data = parent.data(0, Qt.UserRole)
                if not isinstance(step_data, BuilderStep):
                    continue
                user_range = self._user_ranges.get(step_data.step_id)
                if user_range is None:
                    # Auto-filled values appear dim (set by assess/auto-fill later)
                    if parent.text(COL_START):
                        parent.setForeground(COL_START, dim)
                        parent.setForeground(COL_END, dim)
                    continue
                start, end = user_range
                parent.setText(COL_START, f"{start:.0f}")
                if end is not None:
                    parent.setText(COL_END, f"{end:.0f}")
                else:
                    parent.setText(COL_END, "")
        finally:
            tree.blockSignals(False)

    # -- assessment display ------------------------------------------------

    def _apply_assessment(self, results: list) -> None:
        """Walk tree items and apply pastel colors + tooltips from results."""
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        tree = self.ui.tbl_steps
        col_count = tree.columnCount()
        content_col = COL_DESC
        beh_col = COL_BEHAVIORS

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
                names = [
                    o.name
                    for o in step_status.objects
                    if o.status == "missing_behavior"
                ]
                parent.setToolTip(0, f"Missing behavior keys: {', '.join(names)}")

            if step_status.shrinkable_frames > 0:
                existing_tip = parent.toolTip(0) or ""
                shrink_tip = f"{step_status.shrinkable_frames:.0f}f unused"
                parent.setToolTip(
                    0, f"{existing_tip}\n{shrink_tip}" if existing_tip else shrink_tip
                )

            # Recolor step icon and step text to reflect status
            fg_hex, _ = PASTEL_STATUS.get(step_status.status, (None, None))
            icon_color = fg_hex or STEP_ICON_COLOR
            tree.set_item_icon(parent, "step", color=icon_color)
            if fg_hex:
                parent.setForeground(COL_STEP, QBrush(QColor(fg_hex)))
            else:
                parent.setForeground(COL_STEP, QBrush())

            # Color parent behavior column if any child has a behavior issue
            beh_issues = [
                o for o in step_status.objects if o.status == "missing_behavior"
            ]
            if beh_issues:
                b_fg, b_bg = PASTEL_STATUS["missing_behavior"]
                if b_fg:
                    parent.setForeground(beh_col, QBrush(QColor(b_fg)))
                if b_bg:
                    parent.setBackground(beh_col, QBrush(QColor(b_bg)))
                parent.setText(
                    beh_col,
                    f"{len(beh_issues)} missing",
                )
                lines = [
                    f"{o.name}  \u2192  {', '.join(fmt_behavior(b) for b in (o.broken_behaviors or o.behaviors))}"
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
                        if obj_st.status == "missing_object":
                            beh_widget.setText(
                                format_behavior_html(
                                    obj_st.behaviors,
                                    status_color=BEHAVIOR_STATUS_COLORS.get("error"),
                                )
                            )
                        else:
                            beh_widget.setText(
                                format_behavior_html(
                                    obj_st.behaviors,
                                    broken=obj_st.broken_behaviors,
                                )
                            )

                if obj_st.status == "valid":
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
                    child.setToolTip(content_col, "Object not found in Maya")
                elif obj_st.status == "missing_behavior":
                    broken = ", ".join(
                        fmt_behavior(b) for b in obj_st.broken_behaviors
                    ) or ", ".join(fmt_behavior(b) for b in obj_st.behaviors)
                    child.setToolTip(content_col, f"Missing keys: [{broken}]")
                elif obj_st.status == "user_animated" and obj_st.key_range:
                    child.setToolTip(
                        content_col,
                        f"User-animated: keys {obj_st.key_range[0]:.0f}-{obj_st.key_range[1]:.0f}",
                    )

            # Additional objects (in shot but not in CSV)
            if step_status.additional_objects:
                a_fg, a_bg = PASTEL_STATUS.get("additional", (None, None))
                node_icons_cls = try_load_maya_icons()
                for extra_name in step_status.additional_objects:
                    display = (
                        short_name(extra_name) if self._use_short_names else extra_name
                    )
                    extra_item = tree.create_item(
                        ["", "", display, "scene", ""],
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
                    if node_icons_cls:
                        maya_icon = node_icons_cls.get_icon(extra_name)
                        if maya_icon is not None:
                            extra_item.setIcon(content_col, maya_icon)

    # -- expand helpers ----------------------------------------------------

    def expand_missing(self) -> None:
        """Expand all step rows that have missing objects, behaviors, or additional objects."""
        from qtpy.QtCore import Qt

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
            if r.additional_objects:
                issues.append(f"additional objects: {', '.join(r.additional_objects)}")
            if issues:
                problem_ids.add(r.step_id)
                lines.append(f"  {r.step_id}: {'; '.join(issues)}")

        if not problem_ids:
            self._set_footer("No issues found.")
            return

        print(f"\n--- Expand Missing ({len(lines)} steps) ---")
        for line in lines:
            print(line)
        print("---")

        tree = self.ui.tbl_steps
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep) and step_data.step_id in problem_ids:
                parent.setExpanded(True)

    def expand_extra(self) -> None:
        """Expand all step rows that have scene-discovered extra objects."""
        from qtpy.QtCore import Qt

        if not self._last_results:
            self._set_footer("Assess or build first to detect extra objects.")
            return

        extra_ids = {r.step_id for r in self._last_results if r.additional_objects}
        if not extra_ids:
            self._set_footer("No extra objects found.")
            return

        tree = self.ui.tbl_steps
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep) and step_data.step_id in extra_ids:
                parent.setExpanded(True)
