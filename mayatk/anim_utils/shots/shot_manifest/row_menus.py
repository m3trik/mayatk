# !/usr/bin/python
# coding=utf-8
"""The Shot Manifest rows' context menus and what they do: a step row's (colour
tags, open its shot, Lock / Unlock it or its section, Pair with Shot), an
object row's (reveal, copy, re-apply its behaviors, its Render Effects pages,
its audio clip), and a "not in doc" shot's (open, reveal its members, Add to
the Manifest, Pair with Step, Remove Shot).  Local Edits add their own.

Shared text: blendertk carries this module identical
(``m3trik/scripts/check_dcc_twins.py``); mayatk's is the one edited, then
copied over.  Only ``manifest_host.py`` differs between the two.
"""

from typing import Dict, List, Tuple

import pythontk as ptk
from pythontk import BuilderObject, BuilderStep

from .behaviors import Behaviors
from .manifest_data import COL_END, COL_START, ERROR_COLOR, ManifestData
from .manifest_host import ManifestHost


class RowMenusMixin:
    """The rows' context menus, mixed into ``ShotManifestController``.

    Uses the controller's ``ui.tbl_steps``, ``sb``, ``_editor``, ``_row_tags``,
    ``_steps``, ``_user_ranges``, ``_source``, ``_store_cls()``,
    ``_active_store()``, ``_manifest()``, ``_pairing()``, ``_orphan_shots()``,
    ``_is_built``, ``_step_items()``, ``_step_index()``, ``_cascade_from()``,
    ``_refresh_ranges()``, ``_set_range_to_current_frame()``,
    ``_reapply_behavior()``, ``_edit_store()``, ``_redraw()`` and
    ``_set_footer()``.
    """

    def _remove_orphan(self, shot) -> None:
        """Remove one shot no doc step pairs with -- the only way a shot leaves
        the store from this panel: explicit, one at a time, undoable.  The
        table redraws once, keeping its expansion and assessment, like every
        other store edit made from a row's menu (:meth:`_edit_store`)."""
        if self._active_store() is None:
            return
        with self._edit_store("manifest_remove_shot") as store:
            store.remove_shot(shot.shot_id)
        self._redraw(reassess=True)
        self._set_footer(f"Removed shot '{shot.name}'; its keys stay in the scene.")

    def _on_rows_tagged(self, rows, slot) -> None:
        """The user picked a colour (or none) for step rows: keep it on the
        scene's store, under the loaded sheet (``ManifestTags``)."""
        from qtpy.QtCore import Qt

        tree = self.ui.tbl_steps
        step_ids = [
            step.step_id
            for step in (tree.itemFromIndex(i).data(0, Qt.UserRole) for i in rows)
            if isinstance(step, BuilderStep)
        ]
        store = self._active_store()
        if store is not None and step_ids:
            ptk.ManifestTags.assign(store, self._editor.source_key, step_ids, slot)

    def _show_item_menu(self, pos) -> None:
        """Show a context menu for the clicked tree item."""
        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QMenu

        tree = self.ui.tbl_steps
        item = tree.itemAt(pos)

        # Right-click on empty space -- Local Edits' Add Step / Show Hidden
        if item is None:
            menu = QMenu(tree)
            self._editor.add_menu_actions(menu, None)
            if any(not a.isSeparator() for a in menu.actions()):
                menu.exec_(tree.viewport().mapToGlobal(pos))
            menu.deleteLater()  # parented to the tree: else kept per right-click
            return

        # Resolve to parent step row
        is_child = item.parent() is not None
        step_item = item.parent() if is_child else item
        step_data = step_item.data(0, Qt.UserRole)
        if not isinstance(step_data, BuilderStep):
            if getattr(step_data, "shot_id", None) is not None:  # "not in doc" row
                self._show_shot_menu(item, step_data, pos)
            return

        # Collect all selected parent step IDs for multi-selection actions
        selected_step_ids = []
        for sel_item in tree.selectedItems():
            parent_item = (
                sel_item.parent() if sel_item.parent() is not None else sel_item
            )
            sel_data = parent_item.data(0, Qt.UserRole)
            if (
                isinstance(sel_data, BuilderStep)
                and sel_data.step_id not in selected_step_ids
            ):
                selected_step_ids.append(sel_data.step_id)

        # Pre-compute the pairing once for all guards in this menu.
        try:
            built_names = set(self._pairing().shots)
        except Exception:
            built_names = set()
        any_built = bool(built_names)

        menu = QMenu(tree)
        menu.setToolTipsVisible(True)
        if not is_child:
            # Colour swatches: every selected step when the clicked one is
            # among them, else the clicked one.
            tag_ids = (
                selected_step_ids
                if step_data.step_id in selected_step_ids
                else [step_data.step_id]
            )
            self._row_tags.add_to_menu(menu, self._step_items(tag_ids))
            menu.addSeparator()
        act_open = menu.addAction(f"Open '{step_data.shot_name}' in Shot Sequencer")
        act_open_shots = menu.addAction(f"Open '{step_data.shot_name}' in Shots")
        if not any_built:
            act_open.setEnabled(False)
            act_open.setToolTip("Build shots first")
            act_open_shots.setEnabled(False)
            act_open_shots.setToolTip("Build shots first")
        if not is_child:
            self._add_step_shot_actions(menu, step_data, selected_step_ids)

        # Range column actions (parent rows, pre-build only)
        act_set_frame = None
        act_auto_fill = None
        act_clear_range = None
        column = tree.columnAt(pos.x())
        step_is_built = step_data.step_id in built_names
        if not is_child and not step_is_built and column in (COL_START, COL_END):
            menu.addSeparator()
            act_set_frame = menu.addAction("Set Start to Current Frame")
            act_auto_fill = menu.addAction("Auto-fill from Gaps")
            if step_data.step_id in self._user_ranges:
                act_clear_range = menu.addAction("Clear Range")

        # Object-level actions (child rows only)
        act_outliner = None
        act_copy = None
        act_reapply = None
        act_audio = None
        effect_actions = {}
        if is_child:
            obj_data = item.data(0, Qt.UserRole)
            obj_name = (
                getattr(obj_data, "name", None)
                if isinstance(obj_data, BuilderObject)
                else None
            )
            if obj_name:
                # Every selected object row when the clicked one is among
                # them: a name several nodes share is fixed in the scene.
                obj_names = [obj_name]
                if item.isSelected():
                    obj_names = list(
                        dict.fromkeys(
                            o.name
                            for o in (
                                s.data(0, Qt.UserRole) for s in tree.selectedItems()
                            )
                            if isinstance(o, BuilderObject) and o.name
                        )
                    )
                many = len(obj_names) > 1
                menu.addSeparator()
                act_outliner = menu.addAction(
                    f"Show {len(obj_names)} Objects in Outliner"
                    if many
                    else f"Show '{obj_name}' in Outliner"
                )
                act_copy = menu.addAction(
                    f"Copy {len(obj_names)} Names to Clipboard"
                    if many
                    else f"Copy '{obj_name}' to Clipboard"
                )
                if self._is_built and obj_data.behaviors:
                    names = ", ".join(
                        ManifestData.fmt_behavior(b) for b in obj_data.behaviors
                    )
                    act_reapply = menu.addAction(f"Apply [{names}]")
                # The panels that say HOW its behaviors are keyed, opened on
                # this object alone (the recipe there is the build's).
                for channel, verb in self._effect_pages(obj_data):
                    action = menu.addAction(f"{verb} '{obj_name}'\u2026")
                    effect_actions[action] = channel
                if obj_data.kind == "audio":
                    act_audio = menu.addAction(f"Open '{obj_name}' in Audio Clips")

        # Local Edits: rename / hide / move / add (actions carry their own
        # callbacks, so the dispatch below never sees them).
        self._editor.add_menu_actions(menu, item, selected_step_ids)

        chosen = menu.exec_(tree.viewport().mapToGlobal(pos))
        menu.deleteLater()  # the actions stay valid until the event loop runs
        if chosen is act_open:
            self._open_in_shot_sequencer(step_data.step_id)
        elif chosen is act_open_shots:
            self._open_in_shots(step_data.step_id)
        elif chosen is not None and chosen is act_outliner:
            self._show_in_outliner(obj_names)
        elif chosen is not None and chosen is act_copy:
            self._copy_names(obj_names)
        elif chosen is act_reapply and act_reapply is not None:
            self._reapply_behavior(step_data.step_id, obj_data)
        elif chosen is not None and chosen in effect_actions:
            self._open_effect(step_data.step_id, obj_data, effect_actions[chosen])
        elif chosen is act_audio and act_audio is not None:
            self._open_audio_clip(obj_name)
        elif chosen is act_set_frame and act_set_frame is not None:
            self._set_range_to_current_frame(step_item, step_data.step_id)
        elif chosen is act_auto_fill and act_auto_fill is not None:
            step_idx = self._step_index(step_data.step_id)
            # Clear user ranges from clicked step onward so they re-resolve
            self._user_ranges.pop(step_data.step_id, None)
            self._cascade_from(step_idx)
            self._refresh_ranges(from_step_idx=step_idx)
        elif chosen is act_clear_range and act_clear_range is not None:
            self._user_ranges.pop(step_data.step_id, None)
            self._refresh_ranges()

    def _show_shot_menu(self, item, shot, pos) -> None:
        """The context menu of a "not in doc" row (*item* the row or one of
        its member rows): open the shot, reveal or copy its members, take it
        into the manifest -- a step of its own, or the shot of a step that has
        none -- or remove it."""
        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QMenu

        tree = self.ui.tbl_steps
        after = self._row_above(item)  # now: a redraw may delete the row
        menu = QMenu(tree)
        menu.setToolTipsVisible(True)
        name = shot.name
        menu.addAction(
            f"Open '{name}' in Shot Sequencer",
            lambda: self._open_shot_in_sequencer(shot),
        )
        menu.addAction(
            f"Open '{name}' in Shots", lambda: self._open_shot_in_shots(shot)
        )
        obj = item.data(0, Qt.UserRole) if item.parent() is not None else None
        names = [obj.name] if isinstance(obj, BuilderObject) else list(shot.objects)
        if names:
            menu.addSeparator()
            leaf = str(names[0]).split("|")[-1]
            many = len(names) > 1
            menu.addAction(
                f"Show {len(names)} Members in Outliner"
                if many
                else f"Show '{leaf}' in Outliner",
                lambda: self._show_in_outliner(names),
            )
            menu.addAction(
                f"Copy {len(names)} Names to Clipboard"
                if many
                else f"Copy '{leaf}' to Clipboard",
                lambda: self._copy_names(names),
            )
        menu.addSeparator()
        act_add = menu.addAction(
            f"Add '{name}' to the Manifest",
            lambda: self._editor.add_shot(shot, after),
        )
        if self._editor.enabled:
            act_add.setToolTip(
                "A step of its own, named after the shot and bound to it, right\n"
                "where the table lists it -- a Local Edit, never written to the\n"
                "sheet. Its members stay."
            )
        else:
            act_add.setEnabled(False)
            act_add.setToolTip(
                "Viewing the original sheet: turn off View Original Sheet (header "
                "menu) to add it."
            )
        built = self._pairing().shots
        self._add_pair_menu(
            menu,
            f"Pair '{name}' with Step",
            [s for s in self._steps if s.step_id not in built],
            lambda step: self._pair_shot(shot, step),
            "Every step has a shot.",
        )
        menu.addSeparator()
        act_remove = menu.addAction(
            f"Remove Shot '{name}'", lambda: self._remove_orphan(shot)
        )
        act_remove.setToolTip(
            "No doc step pairs with this shot. Removes its record; "
            "its keys stay in the scene."
        )
        menu.exec_(tree.viewport().mapToGlobal(pos))
        menu.deleteLater()  # parented to the tree: else kept per right-click

    def _add_step_shot_actions(self, menu, step, selected: List[str]) -> None:
        """A step row's shot actions: Lock / Unlock it (the selection when the
        step is in it) and its section, or -- a step with no shot -- Pair it
        with a shot no step pairs with."""
        pairing = self._pairing()
        shot = pairing.shots.get(step.step_id)
        if shot is None and self._source != "csv":
            return  # only a sheet leaves a shot unpaired
        menu.addSeparator()
        if shot is None:
            self._add_pair_menu(
                menu,
                f"Pair '{step.shot_name}' with Shot",
                self._orphan_shots(pairing),
                lambda target: self._pair_shot(target, step),
                "No shot is free: every shot pairs with a step.",
            )
            return
        chosen = list(selected) if step.step_id in selected else [step.step_id]
        built = [sid for sid in chosen if sid in pairing.shots]
        locked = shot.locked
        verb = "Unlock" if locked else "Lock"
        label = f"'{step.shot_name}'" if len(built) == 1 else f"{len(built)} Steps"
        act = menu.addAction(
            f"{verb} {label}", lambda: self._set_locked(built, not locked)
        )
        act.setToolTip(
            "Locked: final -- Build never changes its shot, and its row takes no\n"
            "Local Edits. Unlock to revise it, then lock it again."
        )
        if step.section:
            section = [s.step_id for s in self._steps if s.section == step.section]
            for verb, value in (("Lock", True), ("Unlock", False)):
                menu.addAction(
                    f"{verb} Section '{step.section}'",
                    lambda v=value: self._set_locked(section, v),
                )

    def _add_pair_menu(self, menu, title, candidates, pair, empty_tip) -> None:
        """A *title* submenu with an entry per candidate (steps or shots),
        calling ``pair(candidate)``; steps go under their sections once they
        are many.  Disabled with *empty_tip* when there are none."""
        sub = menu.addMenu(title)
        sub.setToolTipsVisible(True)
        if not candidates:
            sub.setEnabled(False)
            sub.menuAction().setToolTip(empty_tip)
            return
        sub.menuAction().setToolTip(
            "Make that the step's shot: Build then updates the shot from the step\n"
            "and keeps its name."
        )
        sections: Dict[str, list] = {}
        for c in candidates:
            sections.setdefault(getattr(c, "section", "") or "", []).append(c)
        grouped = len(candidates) > 15 and len(sections) > 1
        for section, items in sections.items():
            target = sub.addMenu(f"Section {section}") if grouped else sub
            for c in items:
                label = getattr(c, "shot_name", None) or c.name
                desc = (getattr(c, "description", "") or "").strip()
                if len(desc) > 48:
                    desc = desc[:47] + "…"
                if desc:
                    label += "  —  " + desc
                target.addAction(label, lambda c=c: pair(c))

    def _row_above(self, item) -> str:
        """The ID of the step row nearest above *item*'s top-level row, ``""``
        when none is -- where a step added from that row goes."""
        from qtpy.QtCore import Qt

        tree = self.ui.tbl_steps
        top = item.parent() or item
        for i in range(tree.indexOfTopLevelItem(top) - 1, -1, -1):
            data = tree.topLevelItem(i).data(0, Qt.UserRole)
            if isinstance(data, BuilderStep):
                return data.step_id
        return ""

    def _pair_shot(self, shot, step) -> None:
        """Make *shot* *step*'s shot (``ShotManifest.bind``): one undo step;
        Build then updates it from the step and keeps its name."""
        with self._edit_store("manifest_pair_shot") as store:
            self._manifest(store).bind(shot, step.step_id)
        self._redraw(reassess=True)
        self._set_footer(
            f"'{shot.name}' is now the shot of step '{step.shot_name}' -- Build "
            "updates it from the step."
        )

    def _set_locked(self, step_ids, locked: bool) -> None:
        """Lock (or unlock) the shots of *step_ids* -- final: Build leaves a
        locked shot as it is and its row takes no Local Edits.  One undo step;
        a step with no shot has nothing to lock."""
        pairing = self._pairing()
        shots = [pairing.shots[sid] for sid in step_ids if sid in pairing.shots]
        changed = [shot for shot in shots if bool(shot.locked) != locked]
        if changed:
            with self._edit_store("manifest_lock") as store:
                for shot in changed:
                    store.update_shot(shot.shot_id, locked=locked)
            self._redraw(reassess=True)
        verb = "lock" if locked else "unlock"
        footer = f"{verb.title()}ed {len(changed)} shot(s)."
        unbuilt = len(step_ids) - len(shots)
        if unbuilt:
            footer += f" {unbuilt} step(s) have no shot to {verb}."
        self._set_footer(footer)

    @staticmethod
    def _copy_names(names) -> None:
        """Put *names* on the clipboard, one per line."""
        from qtpy.QtWidgets import QApplication

        QApplication.clipboard().setText("\n".join(names))

    @staticmethod
    def _reveal_targets(obj_names, find) -> Tuple[List[str], List[str]]:
        """``(nodes, notes)``: every scene node *obj_names* answer to (*find*:
        name -> nodes), in order, and a footer note for each name answering to
        several (all selected, so the duplicates can be found and renamed) or
        to none."""
        nodes, missing, shared = [], [], []
        for name in obj_names:
            hits = find(name)
            if not hits:
                missing.append(f"'{name}'")
            elif len(hits) > 1:
                shared.append(f"'{name}' ({len(hits)})")
            nodes.extend(h for h in hits if h not in nodes)
        notes = []
        if shared:
            notes.append(f"Several nodes share {', '.join(shared)}: all selected.")
        if missing:
            notes.append(f"{', '.join(missing)} not found in scene.")
        return nodes, notes

    def _show_in_outliner(self, obj_names) -> None:
        """Select every node *obj_names* answer to and reveal them in the
        host's Outliner (``ManifestHost.find_nodes`` / ``reveal``); what could
        not be (missing, shared, unrevealable) is said on the footer
        (:meth:`_reveal_targets`)."""
        if not ManifestHost.available():
            return
        store = self._active_store()
        nodes, notes = self._reveal_targets(
            obj_names, lambda name: ManifestHost.find_nodes(name, store)
        )
        if nodes:
            notes += ManifestHost.reveal(nodes)
        if notes:
            self._set_footer(" ".join(notes), color=ERROR_COLOR)

    def _open_in_shot_sequencer(self, step_id: str) -> None:
        """Open the Shot Sequencer UI and navigate to the shot matching *step_id*.

        The sequencer controller lazily wraps ``ShotStore.active()`` via
        its ``sequencer`` property — no manual wiring needed here.
        """
        store = self._store_cls().active()
        if not store.shots:
            self._set_footer("Build shots first before opening the sequencer.")
            return
        self._open_shot_in_sequencer(self._pairing().shots.get(step_id))

    def _open_shot_in_sequencer(self, target) -> None:
        """Open the Shot Sequencer on shot *target* (``None``: as it is)."""
        self.sb.handlers.marking_menu.show("shot_sequencer")

        seq_slots = self.sb.get_slots_instance("shot_sequencer")
        if seq_slots is None:
            return

        controller = getattr(seq_slots, "controller", None)
        if controller is None:
            return

        # Clear stale session state so prior shifted-out keys
        # and cached segments don't suppress the new display.
        controller._shifted_out_keys.clear()
        controller._segment_cache.clear()
        controller._sync_combobox()

        cmb = getattr(seq_slots.ui, "cmb_shot", None)
        if cmb is not None and target is not None:
            for i in range(cmb.count()):
                shot_id = cmb.itemData(i)
                if shot_id == target.shot_id:
                    cmb.blockSignals(True)
                    cmb.setCurrentIndex(i)
                    cmb.blockSignals(False)
                    controller._sync_to_widget(shot_id, frame=True)
                    controller._update_shot_nav_state()
                    break

    #: The Render Effects page each recipe effect is keyed from, and the verb
    #: its row entry reads with.
    EFFECT_PAGES = {
        "fade_in": ("opacity", "Fade"),
        "fade_out": ("opacity", "Fade"),
        "pulse": ("highlight", "Highlight"),
    }

    @classmethod
    def _effect_pages(cls, obj) -> list:
        """``[(channel, verb)]`` -- the Render Effects pages *obj*'s behaviors
        are keyed from, each once, in behavior order."""
        pages = []
        for behavior in obj.behaviors or ():
            page = cls.EFFECT_PAGES.get(Behaviors.effect_of(behavior))
            if page is not None and page not in pages:
                pages.append(page)
        return pages

    def _open_effect(self, step_id: str, obj, channel: str) -> None:
        """Open Render Effects focused on *obj*'s *channel* effect.

        The picker hides and the header names the object and its step; the
        object is selected. Once its step is built, the panel's Key re-applies
        the object's behaviors where the build places them
        (:meth:`_reapply_behavior`) -- the recipe the page edits is the one
        that keys them.
        """
        store = self._active_store()
        node = store.resolve_member(obj.name)[0] if store is not None else obj.name
        leaf = str(node).split("|")[-1].split(":")[-1]
        self.sb.handlers.marking_menu.show("render_effects")
        slots = self.sb.get_slots_instance("render_effects")
        if slots is None or not hasattr(slots, "focus"):
            return
        apply = None
        if self._pairing().shots.get(step_id) is not None:

            def apply():
                # Raises why not: Render Effects reports it, not "Re-applied".
                self._reapply_behavior(step_id, obj, raise_errors=True)
                return f"Re-applied {leaf}'s behaviors in {step_id}."

        slots.focus(
            channel,
            [node],
            title=f"{leaf} \u00b7 {step_id}",
            apply=apply,
            apply_text=f"Apply to '{leaf}' in {step_id}" if apply else "",
        )

    def _open_audio_clip(self, name: str) -> None:
        """Open Audio Clips on *name*'s track."""
        self.sb.handlers.marking_menu.show("audio_clips")
        slots = self.sb.get_slots_instance("audio_clips")
        if slots is not None and hasattr(slots, "select_track"):
            slots.select_track(name)

    def _open_in_shots(self, step_id: str) -> None:
        """Open the Shots editor UI and navigate to the shot matching *step_id*."""
        store = self._store_cls().active()
        if not store.shots:
            self._set_footer("Build shots first before opening the shots editor.")
            return

        shot = self._pairing().shots.get(step_id)
        if shot is None:
            self._set_footer(f"No shot pairs with '{step_id}' yet.")
            return
        self._open_shot_in_shots(shot)

    def _open_shot_in_shots(self, shot) -> None:
        """Open the Shots editor on *shot*."""
        store = self._store_cls().active()
        self.sb.handlers.marking_menu.show("shots")

        # set_active_shot fires ActiveShotChanged which the ShotsController
        # listener handles — it syncs the combobox and editor fields.
        store.set_active_shot(shot.shot_id)
