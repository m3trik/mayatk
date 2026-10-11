# !/usr/bin/python
# coding=utf-8
"""Local Edits for the Shot Manifest panel: change the table, never the sheet.

Qt glue over :class:`pythontk.ManifestEdits`, where the rules live: steps
renamed, re-described, hidden or added; objects renamed, hidden, moved or
re-behaviored -- each recorded against the sheet's own identities and saved on
the scene's ``ShotStore.manifest_edits``, one layer per source.  The edits are
the manifest's own revision of its sheet, so they always apply and Build
follows them; **View Original Sheet** (header menu) shows the sheet as delivered
beside them for comparison -- read-only, Build off -- and changes nothing.

The controller drives it at five seams: loading steps (:meth:`load` +
:meth:`apply`), a double-click (:meth:`begin_edit`), a committed cell
(:meth:`commit_edit`), its context menu (:meth:`add_menu_actions`) and the
header menu (:meth:`add_header_options`); the table calls :meth:`decorate`
after it is drawn. Removing the feature is removing this module and those
calls.

Host protocol (the panel controller): ``ui.tbl_steps``, ``sb``, ``logger``,
``_settings``, ``_steps`` / ``_sheet_steps``, ``_source`` / ``_csv_path``,
``_active_store()``, ``_pairing()``, ``_manifest()``, ``_edit_store()``,
``_step_is_built()``, ``_step_is_locked()`` / ``_locked_step_ids()``,
``_populate_table()``,
``_save_tree_state()`` / ``_restore_tree_state()``, ``_refresh_ranges()``,
``_apply_assessment()``, ``_last_results``, ``_reassess_soon()``,
``_update_build_button()`` and ``_set_footer()``.

A locked step (its shot locked: final) takes no edit until it is unlocked; a
shot's member shown under its step (origin ``"member"``) is the scene's, never
an edit's.
"""

from typing import Callable, List, Optional, Tuple

import pythontk as ptk

from .manifest_data import COL_DESC, COL_STEP, ERROR_COLOR


class ManifestEditor:
    """One panel's Local Edits: its edit layer, its cell editors, and the
    original-sheet view."""

    #: Settings key of the retired Local Edits on/off option (2026-10-10: the
    #: edits always apply); the controller drops it from the user's settings.
    RETIRED_SETTING = "local_edits"

    def __init__(self, host):
        self.host = host
        self.edits = ptk.ManifestEdits()
        #: Showing the sheet as delivered (:meth:`view_original`): a view of
        #: this session, never saved -- a panel opens on the manifest.
        self.viewing_original = False
        self._chk = None
        self._btn_reset = None
        # The cell editor begin_edit opened: {"data", "column", "item", "shown"}.
        self._pending: Optional[dict] = None
        tree = host.ui.tbl_steps
        tree.itemDelegate().closeEditor.connect(self._on_editor_closed)

    # ---- the original-sheet view ---------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether the edits are in force -- applied and editable: always,
        but while the original sheet is viewed (:meth:`view_original`)."""
        return not self.viewing_original

    def add_header_options(self, menu) -> None:
        """Add View Original Sheet and Reset Local Edits to *menu*."""
        self._chk = menu.add(
            "QCheckBox",
            setText="View Original Sheet",
            setObjectName="chk_view_original_sheet",
            setChecked=False,
            setToolTip=(
                "Show the sheet as it was delivered -- without your local edits\n"
                "-- to compare it with your manifest. Read-only while on: nothing\n"
                "can be edited and Build is off. Turn it off to return; nothing\n"
                "is lost either way."
            ),
        )
        self._chk.restore_state = False  # a view of this session, not a preference
        self._chk.toggled.connect(self.view_original)
        self._btn_reset = menu.add(
            "QPushButton",
            setText="Reset Local Edits",
            setObjectName="btn_reset_local_edits",
            setToolTip=(
                "Drop every local edit of this sheet: the manifest returns to the\n"
                "sheet as delivered. Shots already built keep their changes until\n"
                "the next Build."
            ),
        )
        self._btn_reset.released.connect(self.reset)
        self._sync_header()

    def _sync_header(self) -> None:
        """Enable Reset while there are edits to drop and they are in force."""
        if self._btn_reset is None:
            return
        n = self.edits.count()
        self._btn_reset.setEnabled(self.enabled and n > 0)
        self._btn_reset.setText(
            f"Reset Local Edits ({n})" if n else "Reset Local Edits"
        )

    def view_original(self, on: bool = True) -> None:
        """Show the sheet as delivered (*on*), or the manifest with its edits.

        A view for comparing, not a mode to work in: while it shows, nothing
        edits and Build is off; turning it off restores every edit -- nothing
        was dropped.  A showing assessment is judged again over the steps now
        shown.
        """
        on = bool(on)
        if on == self.viewing_original:
            return
        self.viewing_original = on
        if self._chk is not None and self._chk.isChecked() != on:
            self._chk.blockSignals(True)
            self._chk.setChecked(on)
            self._chk.blockSignals(False)
        host = self.host
        self.refresh()  # the rows it now shows, drawn whatever Assess can reach
        if host._last_results:
            host._reassess_soon()  # it judged the other steps
        host._update_build_button()
        n = self.edits.count()
        if on:
            hidden = f" ({n} local edit(s) hidden)" if n else ""
            host._set_footer(
                f"Viewing the original sheet{hidden}: read-only, and Build is off "
                "until you turn it off."
            )
        else:
            applied = f": {n} local edit(s) applied" if n else ""
            host._set_footer(f"Back to your manifest{applied}.")

    def refuse_viewing(self, to: str = "edit") -> bool:
        """Say so and return ``True`` while the original sheet is viewed:
        nothing edits or builds there (*to*: what was asked)."""
        if not self.viewing_original:
            return False
        self.host._set_footer(
            "Viewing the original sheet: turn off View Original Sheet (header "
            f"menu) to {to}.",
            color=ERROR_COLOR,
        )
        return True

    def reset(self, confirm: bool = True) -> None:
        """Drop every edit of the current source, after asking."""
        n = self.edits.count()
        if not n:
            return
        if confirm:
            answer = self.host.sb.message_box(
                f"<b>Reset {n} local edit(s)?</b><br>The manifest returns to "
                "the sheet as delivered; the next Build applies it.",
                "Yes",
                "No",
            )
            if answer != "Yes":
                return
        self.edits.clear()
        self._commit(f"Reset {n} local edit(s): the sheet as delivered.")

    # ---- the layer -----------------------------------------------------------

    @property
    def source_key(self) -> str:
        """The key this source's edits are saved under: the sheet's path, or
        ``""`` for the scene's own shots and detected steps."""
        return self.host._csv_path if self.host._source == "csv" else ""

    def load(self) -> None:
        """Read the current source's edits from the scene."""
        store = self.host._active_store()
        data = store.manifest_edits.get(self.source_key) if store is not None else None
        self.edits = ptk.ManifestEdits.from_dict(data)
        self._sync_header()

    def save(self) -> None:
        """Write the current source's edits to the scene."""
        store = self.host._active_store()
        if store is None:
            return
        key = self.source_key
        if self.edits.has_edits:
            store.manifest_edits[key] = self.edits.to_dict()
        elif store.manifest_edits.pop(key, None) is None:
            return  # nothing saved, nothing to drop
        store.mark_dirty()

    def apply(self, steps: List[ptk.BuilderStep]) -> List[ptk.BuilderStep]:
        """*steps* with the edits applied -- as delivered while the original
        sheet is viewed."""
        return self.edits.apply(steps) if self.enabled else steps

    def stale(self) -> List[str]:
        """The edits the loaded sheet gives no place to (``ManifestEdits.stale``)."""
        return self.edits.stale(self.host._sheet_steps)

    def refresh(self) -> None:
        """Re-apply the edits to the loaded steps and redraw the table."""
        host = self.host
        state = host._save_tree_state()
        host._steps = self.apply(host._sheet_steps)
        host._populate_table()
        if host._steps:
            host._refresh_ranges()
        if host._last_results:
            host._apply_assessment(host._last_results)
        host._restore_tree_state(state)
        self._sync_header()

    def _commit(self, footer: str = "", then: Optional[Callable] = None) -> None:
        """Save the edits; redraw on the next event-loop turn (an edit commits
        from inside the tree's own signal), then run *then*."""
        from qtpy.QtCore import QTimer

        self.save()
        self._sync_header()

        def _redraw():
            self.refresh()
            self._mark_edited()
            if footer:
                self.host._set_footer(footer)
            if then is not None:
                then()

        QTimer.singleShot(0, _redraw)

    def _mark_edited(self) -> None:
        """A Build would now change something the last Assess did not see."""
        self.host._edited_since_assess = True
        self.host._update_build_button()

    def _refuse(self, problem: str) -> None:
        """Say why an edit was not taken, and redraw the cell as it was."""
        from qtpy.QtCore import QTimer

        self.host._set_footer(problem, color=ERROR_COLOR)
        QTimer.singleShot(0, self.refresh)

    def _locked(self, step) -> bool:
        """Whether *step* (a ``BuilderStep``) is locked: its shot is final, so
        its row takes no edit until it is unlocked."""
        return isinstance(step, ptk.BuilderStep) and self.host._step_is_locked(
            step.step_id
        )

    def refuse_locked(self, step) -> bool:
        """Say so and return ``True`` when *step* is locked (:meth:`_locked`)."""
        if not self._locked(step):
            return False
        self.host._set_footer(
            f"'{step.shot_name}' is locked: unlock it (right-click) to edit it.",
            color=ERROR_COLOR,
        )
        return True

    @staticmethod
    def _editable(obj) -> bool:
        """Whether *obj* is an object row Local Edits may change: one the
        sheet lists (or an edit put there) -- a shot's member shown under its
        step (origin ``"member"``) is the scene's, not the sheet's."""
        return isinstance(obj, ptk.BuilderObject) and obj.origin != "member"

    # ---- cell editing --------------------------------------------------------

    def begin_edit(self, item, column: int) -> bool:
        """Open an editor on a step's name or description, or an object's
        name; ``False`` when the cell is not Local Edits'.  ``True`` with no
        editor while the original sheet is viewed (the footer says why)."""
        from qtpy.QtCore import Qt

        data = item.data(0, Qt.UserRole)
        if isinstance(data, ptk.BuilderStep) and column == COL_STEP:
            text = data.shot_name
        elif isinstance(data, ptk.BuilderStep) and column == COL_DESC:
            text = data.description
        elif self._editable(data) and column == COL_DESC:
            text = data.name  # the full name, whatever the display form
        else:
            return False
        step = (
            data
            if isinstance(data, ptk.BuilderStep)
            else item.parent().data(0, Qt.UserRole)
        )
        if self.refuse_viewing() or self.refuse_locked(step):
            return True
        tree = self.host.ui.tbl_steps
        self._pending = {
            "data": data,
            "column": column,
            "item": item,
            "shown": item.text(column),
        }
        tree.blockSignals(True)
        try:
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            item.setText(column, text)
        finally:
            tree.blockSignals(False)
        tree.editItem(item, column)
        return True

    def _on_editor_closed(self, *_args) -> None:
        """A cancelled editor: put the cell's display form back."""
        pending, self._pending = self._pending, None
        if pending is None:
            return
        tree = self.host.ui.tbl_steps
        tree.blockSignals(True)
        try:
            pending["item"].setText(pending["column"], pending["shown"])
        except RuntimeError:
            pass  # the table was redrawn meanwhile
        finally:
            tree.blockSignals(False)

    def commit_edit(self, item, column: int) -> bool:
        """Record the edit a cell editor committed; ``False`` when the change
        was not one :meth:`begin_edit` opened (a range, a repaint)."""
        from qtpy.QtCore import Qt

        pending = self._pending
        data = item.data(0, Qt.UserRole)
        if pending is None or pending["data"] is not data:
            return False
        if pending["column"] != column:
            return False
        text = item.text(column)
        if isinstance(data, ptk.BuilderStep) and column == COL_STEP:
            if text.strip() == data.shot_name:
                return True  # a repaint of the open cell, not the commit
            self._pending = None
            self._rename_step(data, text.strip())
        elif isinstance(data, ptk.BuilderStep):
            if text == data.description:
                return True
            self._pending = None
            self.edits.set_step(
                data.step_id,
                "description",
                text,
                sheet=data.sheet.get("description", data.description),
            )
            self._commit(f"Edited the description of '{data.shot_name}'.")
        else:
            if text.strip() == data.name:
                return True
            self._pending = None
            self._rename_object(item.parent().data(0, Qt.UserRole), data, text.strip())
        return True

    def _name_problem(self, step, name: str) -> Optional[str]:
        """Why *step* cannot be named *name*, or ``None``."""
        if not name:
            return "A step needs a name."
        folded = name.casefold()
        for other in self.host._steps + self.host._sheet_steps:
            if other.step_id == step.step_id:
                continue
            if folded in (other.step_id.casefold(), other.shot_name.casefold()):
                return f"'{name}' already names step '{other.shot_name}'."
        store = self.host._active_store()
        if store is not None:
            shot = self.host._pairing().shots.get(step.step_id)
            refused = store.name_error(
                name, shot_id=shot.shot_id if shot is not None else None
            )
            if refused:
                return refused
        return None

    def _rename_step(self, step, name: str) -> None:
        problem = self._name_problem(step, name)
        if problem:
            self._refuse(f"Not renamed: {problem}")
            return
        old = step.shot_name
        self.edits.set_step(
            step.step_id, "name", name, sheet=step.sheet.get("name", step.shot_name)
        )
        built = self.host._step_is_built(step.step_id)
        self._commit(
            f"Renamed '{old}' to '{name}'"
            + (" -- Build renames its shot." if built else ".")
        )

    def _rename_object(self, step, obj, name: str) -> None:
        if any(o is not obj and o.name == name for o in step.objects):
            self._refuse(f"Not renamed: '{step.shot_name}' already lists '{name}'.")
            return
        home, sheet_name = ptk.ManifestEdits.anchor(step, obj)
        self.edits.set_object(home, sheet_name, "name", name)
        self._commit(
            f"'{step.shot_name}' now lists '{name}' (was '{obj.name}')"
            f"{self._scene_note(name)}."
        )

    def _scene_note(self, name: str) -> str:
        """`` -- not in the scene`` (or ambiguous) for a name the store cannot
        resolve to one object; empty when it can."""
        store = self.host._active_store()
        state = store.resolve_member(name)[1] if store is not None else "found"
        if state == "missing":
            return " -- not in the scene"
        if state == "ambiguous":
            return " -- several scene objects have that name"
        return ""

    def set_behaviors(self, step_id: str, obj, behaviors: List[str]) -> bool:
        """Record *obj*'s ticked *behaviors* in step *step_id*; ``False``
        (nothing recorded) while the original sheet is viewed.  The table redraws on the
        next event-loop turn, after the behavior menu that asked has closed."""
        step = next((s for s in self.host._steps if s.step_id == step_id), None)
        if step is None or self.refuse_viewing() or self.refuse_locked(step):
            return False
        home, sheet_name = ptk.ManifestEdits.anchor(step, obj)
        sheet = obj.sheet.get("behaviors", obj.behaviors)
        self.edits.set_object(home, sheet_name, "behaviors", behaviors, sheet=sheet)
        self._commit(f"Edited the behaviors of '{obj.name}' in '{step.shot_name}'.")
        return True

    # ---- the context menu ----------------------------------------------------

    def add_menu_actions(self, menu, item, selected: List[str] = ()) -> None:
        """Add Local Edits' actions for the row at *item* (``None``: empty
        space) to *menu*; *selected* is the selected steps' IDs. Nothing while
        the original sheet is viewed."""
        if not self.enabled:
            return
        from qtpy.QtCore import Qt

        data = item.data(0, Qt.UserRole) if item is not None else None
        menu.addSeparator()
        if isinstance(data, ptk.BuilderObject):
            step = item.parent().data(0, Qt.UserRole)
            if self._editable(data) and not self._locked(step):
                self._object_actions(menu, step, data)
        elif isinstance(data, ptk.BuilderStep) and not self._locked(data):
            # The selection when the clicked step is in it, else that step.
            chosen = list(selected) if data.step_id in selected else [data.step_id]
            self._step_actions(menu, data, chosen)
        if isinstance(data, ptk.BuilderStep):
            after = data
        elif isinstance(data, ptk.BuilderObject):
            after = item.parent().data(0, Qt.UserRole)
        else:
            after = self.host._steps[-1] if self.host._steps else None
        menu.addAction(
            f"Add Step After '{after.shot_name}'" if after else "Add Step",
            lambda: self.add_step(after.step_id if after else ""),
        )
        hidden = self.edits.hidden_steps()
        if hidden:
            sub = menu.addMenu(f"Show Hidden Steps ({len(hidden)})")
            for sid in hidden:
                label = self.edits.step_record(sid).get("name", sid)
                sub.addAction(label, lambda s=sid: self.hide_steps([s], False))

    def _step_actions(self, menu, step, selected: List[str]) -> None:
        name = step.shot_name
        built = self.host._pairing().shots
        hideable = [s for s in selected if s not in built]
        if len(selected) > 1:
            act = menu.addAction(
                f"Hide {len(hideable)} Steps", lambda: self.hide_steps(hideable)
            )
        else:
            act = menu.addAction(f"Hide '{name}'", lambda: self.hide_steps(hideable))
        if not hideable:
            act.setEnabled(False)
            act.setToolTip("A built step stays: Build never removes a shot.")
        if self.edits.is_added(step.step_id):
            menu.addAction(f"Remove '{name}'", lambda: self.remove_step(step))
        if self.edits.step_record(step.step_id):
            menu.addAction(f"Revert '{name}'", lambda: self.revert_step(step))
        hidden = self.edits.hidden_objects(step.step_id)
        if hidden:
            sub = menu.addMenu(f"Show Hidden Objects ({len(hidden)})")
            for home, sheet_name in hidden:
                label = self.edits.object_record(home, sheet_name).get(
                    "name", sheet_name
                )
                sub.addAction(
                    label,
                    lambda h=home, n=sheet_name: self.hide_object(h, n, False),
                )

    def _object_actions(self, menu, step, obj) -> None:
        name = obj.name
        home, sheet_name = ptk.ManifestEdits.anchor(step, obj)
        menu.addAction(
            f"Hide '{name}'", lambda: self.hide_object(home, sheet_name, True)
        )
        sub = menu.addMenu(f"Move '{name}' To")
        locked = self.host._locked_step_ids()  # final: nothing moves into one
        for target in self.host._steps:
            if target is step:
                continue
            act = sub.addAction(
                target.shot_name,
                lambda t=target: self.move_object(step, obj, t),
            )
            if target.step_id in locked or any(o.name == name for o in target.objects):
                act.setEnabled(False)
        menu.addAction(f"Rename All '{name}'…", lambda: self.rename_all(name))
        if obj.sheet or self.edits.object_record(home, sheet_name):
            menu.addAction(
                f"Revert '{name}'",
                lambda: self.revert_object(home, sheet_name, name),
            )

    # ---- the actions ---------------------------------------------------------

    def add_step(self, after: str) -> str:
        """Add a step after step *after* (``""``: at the top) and open its
        name for editing. Returns its ID."""
        taken = [s.step_id for s in self.host._sheet_steps]
        taken += [s.shot_name for s in self.host._steps]
        step_id = self.edits.add_step(after, taken=taken)
        self._commit(
            f"Added step '{step_id}': name it, then move objects into it.",
            then=lambda: self._edit_step_name(step_id),
        )
        return step_id

    def add_shot(self, shot, after: str) -> Optional[str]:
        """Take a shot no step pairs with into the manifest: a step of its own
        right after step *after*, its ID the shot's name (numbered when a
        step has it), bound to the shot -- a shot split off or added in the
        Shot Sequencer becomes part of the plan without touching the sheet.
        Its members stay (Build keeps a shot's members it never built).  One
        undo step for the binding.  Returns the step's ID, ``None`` while
        the original sheet is viewed."""
        host = self.host
        if self.refuse_viewing():
            return None
        taken = [s.step_id for s in host._sheet_steps]
        taken += [n for s in host._steps for n in (s.step_id, s.shot_name)]
        step_id = self.edits.add_step(after, taken=taken, step_id=shot.name)
        with host._edit_store("manifest_add_shot") as store:
            host._manifest(store).bind(shot, step_id)
        self._commit(f"Added '{shot.name}' to the manifest as step '{step_id}'.")
        return step_id

    def _edit_step_name(self, step_id: str) -> None:
        items = self.host._step_items([step_id])
        if items:
            tree = self.host.ui.tbl_steps
            tree.scrollToItem(items[0])
            tree.setCurrentItem(items[0])
            self.begin_edit(items[0], COL_STEP)

    def remove_step(self, step) -> None:
        """Remove an added step; objects moved into it go back where the
        sheet lists them. Its shot, if built, stays (Build never removes one)."""
        if not self.edits.remove_step(step.step_id):
            return
        built = self.host._step_is_built(step.step_id)
        self._commit(
            f"Removed step '{step.shot_name}'"
            + ("; its shot stays -- remove it from the list below." if built else ".")
        )

    def hide_steps(self, step_ids: List[str], hidden: bool = True) -> None:
        """Hide (or show) steps. Only an unbuilt step hides: Build never
        removes a shot, so a built step stays listed."""
        if hidden:
            built = self.host._pairing().shots
            step_ids = [s for s in step_ids if s not in built]
        for sid in step_ids:
            self.edits.set_step(sid, "hidden", hidden)
        if step_ids:
            verb = "Hid" if hidden else "Showed"
            self._commit(f"{verb} {len(step_ids)} step(s).")

    def hide_object(self, home: str, sheet_name: str, hidden: bool = True) -> None:
        """Hide (or show) an object: a hidden one leaves its step, and Build
        takes it out of the step's shot along with the keys Build gave it."""
        self.edits.set_object(home, sheet_name, "hidden", hidden)
        verb = "Hid" if hidden else "Showed"
        self._commit(f"{verb} '{sheet_name}'.")

    def move_object(self, step, obj, target) -> None:
        """Move *obj* from *step* to *target* (to the end of its objects)."""
        if any(o.name == obj.name for o in target.objects):
            self._refuse(f"Not moved: '{target.shot_name}' already lists '{obj.name}'.")
            return
        home, sheet_name = ptk.ManifestEdits.anchor(step, obj)
        self.edits.set_object(home, sheet_name, "step", target.step_id)
        self._commit(
            f"Moved '{obj.name}' from '{step.shot_name}' to '{target.shot_name}'."
        )

    def rename_all(self, name: str, new: Optional[str] = None) -> int:
        """Point every step listing *name* at *new* (asked for when
        ``None``). Returns how many objects were renamed."""
        if new is None:
            new = self.host.sb.input_dialog(
                title="Rename All",
                label=f"Every step listing '{name}' will list:",
                text=name,
                parent=self.host.ui.tbl_steps,
                validate=lambda text: bool(text.strip()),
                error_text="An object needs a name.",
            )
        new = (new or "").strip()
        if not new or new == name:
            return 0
        locked = self.host._locked_step_ids()  # final: renamed nowhere
        steps = [s for s in self.host._steps if s.step_id not in locked]
        clashes = [
            s.shot_name
            for s in steps
            if any(o.name == name for o in s.objects)
            and any(o.name == new for o in s.objects)
        ]
        if clashes:
            self._refuse(f"Not renamed: {', '.join(clashes)} already list(s) '{new}'.")
            return 0
        n = self.edits.rename_all(steps, name, new)
        self._commit(
            f"Renamed '{name}' to '{new}' in {n} place(s){self._scene_note(new)}."
        )
        return n

    def revert_step(self, step) -> None:
        """Drop *step*'s own edits (name, description); an added step stays."""
        if self.edits.revert_step(step.step_id):
            self._commit(f"Reverted '{step.shot_name}' to the sheet.")

    def revert_object(self, home: str, sheet_name: str, name: str = "") -> None:
        """Drop an object's edits: the sheet's name, step and behaviors."""
        if self.edits.revert_object(home, sheet_name):
            self._commit(f"Reverted '{name or sheet_name}' to the sheet.")

    # ---- the marks -----------------------------------------------------------

    def decorate(self) -> None:
        """Mark the edited cells -- italic, the sheet's value in the tooltip --
        while the edits are in force. Idempotent: the table and the assessment
        both call it after they draw."""
        if not self.enabled:
            return
        from qtpy.QtCore import Qt

        tree = self.host.ui.tbl_steps
        names = {s.step_id: s.shot_name for s in self.host._steps}
        tree.blockSignals(True)
        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                step = parent.data(0, Qt.UserRole)
                if not isinstance(step, ptk.BuilderStep):
                    continue
                for col, note in self._step_notes(step):
                    self._mark(parent, col, note)
                for j in range(parent.childCount()):
                    child = parent.child(j)
                    obj = child.data(0, Qt.UserRole)
                    if not isinstance(obj, ptk.BuilderObject):
                        continue
                    for note in self._object_notes(obj, names):
                        self._mark(child, COL_DESC, note)
        finally:
            tree.blockSignals(False)

    def _step_notes(self, step) -> List[Tuple[int, str]]:
        notes = []
        if self.edits.is_added(step.step_id):
            notes.append((COL_STEP, "Added in Local Edits -- not in the sheet."))
        if "name" in step.sheet:
            notes.append((COL_STEP, f"Sheet: {step.sheet['name']}"))
        if "description" in step.sheet:
            sheet = step.sheet["description"] or "(empty)"
            notes.append((COL_DESC, f"Sheet: {sheet}"))
        hidden = self.edits.hidden_objects(step.step_id)
        if hidden:
            listed = ", ".join(name for _home, name in hidden)
            notes.append((COL_STEP, f"Hidden here: {listed}"))
        return notes

    @staticmethod
    def _object_notes(obj, names: dict) -> List[str]:
        notes = []
        if "name" in obj.sheet:
            notes.append(f"Sheet: {obj.sheet['name']}")
        if "step" in obj.sheet:
            home = obj.sheet["step"]
            notes.append(f"Moved here from '{names.get(home, home)}'.")
        if "behaviors" in obj.sheet:
            sheet = ", ".join(obj.sheet["behaviors"]) or "none"
            notes.append(f"Sheet behaviors: {sheet}")
        return notes

    @staticmethod
    def _mark(item, column: int, note: str) -> None:
        font = item.font(column)
        if not font.italic():
            font.setItalic(True)
            item.setFont(column, font)
        tip = item.toolTip(column)
        if note not in tip:
            item.setToolTip(column, f"{tip}\n{note}" if tip else note)
