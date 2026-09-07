# !/usr/bin/python
# coding=utf-8
"""Slots for the Key Stash panel (key_stash.ui)."""

from typing import Optional

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.anim_utils.key_stash._key_stash import KeyStash


class KeyStashSlots(ptk.LoggingMixin):
    """Controller wiring key_stash.ui to the :class:`KeyStash` store.

    A thin driver: each control resolves the active store and calls one
    method on it; the clip list repaints from the store's change events, so a
    stash made from the Shot Sequencer's clip menu shows up here without any
    wiring between the two panels.
    """

    SOURCES = ("Selected Keys", "Timeline Selection", "Playback Range")
    RETRIEVE_AT = ("Original Frames", "Current Time")

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[Key Stash] ")
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.key_stash
        self._bound_store: Optional[KeyStash] = None
        self._initialized = False
        # Deferred: child widgets aren't wired onto self.ui until
        # register_children runs after __init__ returns.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    # ---- setup -----------------------------------------------------------

    def header_init(self, widget) -> None:
        """Configure header buttons, the refresh action and the help text."""
        widget.config_buttons("refresh", "collapse", "pin")
        widget.refresh_requested.connect(self.refresh_from_scene)
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Key Stash",
                body="Park keys out of the working animation and bring them "
                "back later. A stored clip is inert: it does not evaluate, "
                "export or bake, and it survives save and reopen.",
                steps=[
                    "Pick a <b>Source</b>: the Graph Editor key selection, a "
                    "drag-selected time slider range, or the playback range "
                    "(the last two act on the selected objects).",
                    "Click <b>Store Keys</b>. The keys leave a plain gap; the "
                    "neighbours interpolate across it.",
                    "Select a clip in the list. <b>Preview</b> plays it on its "
                    "objects through a temporary override layer; "
                    "<b>In Context</b> keeps the scene animation outside the "
                    "clip's range.",
                    "<b>Retrieve</b> puts the keys back (on their original "
                    "frames, or starting at the current time) and forgets the "
                    "clip. <b>Drop</b> deletes them for good.",
                ],
                notes=[
                    "Double-click a clip to select its objects.",
                    "Refresh (header) re-reads the scene: clips whose stash "
                    "nodes were deleted or undone away are pruned.",
                ],
            )
        )

    def _initialize_ui(self) -> None:
        """Populate the combos and bind the store (deferred from __init__)."""
        if self._initialized:
            return
        self._initialized = True
        self.ui.cmb000.add(list(self.SOURCES))
        self.ui.cmb000.current_text_prefix = "Source:  "
        self.ui.cmb001.add(list(self.RETRIEVE_AT))
        self.ui.cmb001.current_text_prefix = "Retrieve At:  "
        tree = self.ui.tree000
        tree.itemSelectionChanged.connect(self._sync_buttons)
        tree.itemDoubleClicked.connect(self._select_clip_objects)
        KeyStash.add_invalidation_listener(self._on_store_invalidated)
        self.refresh()

    @property
    def store(self) -> KeyStash:
        """The active store, (re)bound to this panel's change listener."""
        store = KeyStash.active()
        if store is not self._bound_store:
            if self._bound_store is not None:
                self._bound_store.remove_listener(self._on_store_changed)
            store.add_listener(self._on_store_changed)
            self._bound_store = store
        return store

    def _on_store_invalidated(self, _event) -> None:
        self._bound_store = None
        self.refresh()

    def _on_store_changed(self, _event) -> None:
        self.refresh()

    # ---- view ------------------------------------------------------------

    def refresh(self) -> None:
        """Repaint the clip list from the store."""
        from qtpy import QtCore, QtWidgets

        store = self.store
        tree = self.ui.tree000
        selected = self._selected_clip_id()
        tree.clear()
        for clip in store.clips:
            label = clip.label
            if store.is_previewing(clip.clip_id):
                label += "  (previewing)"
            objects = ", ".join(o.rsplit("|", 1)[-1] for o in clip.objects)
            span = ""
            if clip.start is not None:
                span = (
                    f"{clip.start:g}"
                    if clip.start == clip.end
                    else f"{clip.start:g} - {clip.end:g}"
                )
            stored = clip.created.replace("T", " ")[:16]
            item = QtWidgets.QTreeWidgetItem([label, objects, span, stored])
            item.setData(0, QtCore.Qt.UserRole, clip.clip_id)
            item.setToolTip(0, f"{clip.key_count} keys on {len(clip.curves)} curves")
            item.setToolTip(1, "\n".join(clip.objects))
            tree.addTopLevelItem(item)
            if clip.clip_id == selected:
                item.setSelected(True)
        for col in range(tree.columnCount()):
            tree.resizeColumnToContents(col)
        count = len(store.clips)
        plural = "" if count == 1 else "s"
        self.ui.footer.setDefaultStatusText(
            f"{count} stored clip{plural}" if count else "No stored clips"
        )
        self._sync_buttons()

    def refresh_from_scene(self) -> None:
        """Header refresh: prune clips whose stash nodes are gone, then repaint."""
        gone = self.store.reconcile()
        self.refresh()
        if gone:
            self._footer(
                f"Pruned {len(gone)} clip(s) whose keys are gone from the scene.",
                "warning",
            )
        else:
            self._footer("Clip list is in sync with the scene.")

    def _selected_clip_id(self) -> Optional[int]:
        from qtpy import QtCore

        items = self.ui.tree000.selectedItems()
        if not items:
            return None
        return items[0].data(0, QtCore.Qt.UserRole)

    def _sync_buttons(self) -> None:
        clip_id = self._selected_clip_id()
        has_clip = clip_id is not None
        store = self._bound_store
        previewing = store is not None and store.is_previewing()
        self.ui.b001.setEnabled(has_clip)
        self.ui.b003.setEnabled(has_clip)
        self.ui.chk001.setEnabled(has_clip or previewing)
        # In Context is read when a preview starts; a running one won't re-read it.
        self.ui.chk000.setEnabled(not previewing)
        self._set_checked_silently(self.ui.chk001, previewing)

    @staticmethod
    def _set_checked_silently(widget, checked: bool) -> None:
        widget.blockSignals(True)
        try:
            widget.setChecked(checked)
        finally:
            widget.blockSignals(False)

    def _footer(self, msg: str, level: str = "info") -> None:
        self.ui.footer.setText(msg, level=level)
        getattr(self.logger, level if level != "success" else "info")(msg)

    def _select_clip_objects(self, item, _column: int = 0) -> None:
        """Double-click on a clip: select the clip's objects that still exist."""
        from qtpy import QtCore

        clip = self.store.get_clip(item.data(0, QtCore.Qt.UserRole))
        if clip is None:
            return
        present = [o for o in clip.objects if cmds.objExists(o)]
        if not present:
            self._footer("None of the clip's objects exist in the scene.", "warning")
            return
        cmds.select(present, replace=True)
        self._footer(f"Selected {len(present)} object(s) of '{clip.label}'.")

    # ---- slots -----------------------------------------------------------

    def b000(self) -> None:
        """Store Keys"""
        store = self.store
        source = self.ui.cmb000.currentText()
        try:
            if source == "Selected Keys":
                clip = store.stash(selected_keys=True)
            else:
                objects = cmds.ls(selection=True, long=True)
                if not objects:
                    self._footer("Select the object(s) whose keys to store.", "warning")
                    return
                if source == "Timeline Selection":
                    rng = AnimUtils.get_timeline_selection()
                    if rng is None:
                        self._footer(
                            "Drag-select a range on the time slider first.", "warning"
                        )
                        return
                else:
                    rng = (
                        cmds.playbackOptions(query=True, minTime=True),
                        cmds.playbackOptions(query=True, maxTime=True),
                    )
                clip = store.stash(objects=objects, time_range=rng)
        except ValueError as exc:
            self._footer(str(exc), "warning")
            return
        except RuntimeError as exc:
            self._footer(f"Store failed: {exc}".rstrip(), "error")
            return
        if clip is None:
            self._footer("No keys found to store.", "warning")
            return
        self._footer(f"Stored {clip.key_count} keys as '{clip.label}'.", "success")

    def b001(self) -> None:
        """Retrieve"""
        clip_id = self._selected_clip_id()
        if clip_id is None:
            self._footer("Select a stored clip to retrieve.", "warning")
            return
        at = None
        if self.ui.cmb001.currentText() == "Current Time":
            at = cmds.currentTime(query=True)
        try:
            restored = self.store.retrieve(clip_id, at=at)
        except RuntimeError as exc:
            # A scene-side refusal (locked or referenced target, a paste with
            # nowhere to land) must reach the panel, not the Script Editor.
            self._footer(f"Retrieve failed: {exc}".rstrip(), "error")
            return
        if restored:
            self._footer(f"Retrieved {restored} keys.", "success")
        else:
            self._footer(
                "Nothing retrieved — the clip's objects are gone (see Script Editor).",
                "warning",
            )

    def chk001(self, checked: bool) -> None:
        """Preview (toggle)"""
        store = self.store
        if not checked:
            if store.end_preview():
                self._footer("Preview ended.")
            return
        clip_id = self._selected_clip_id()
        if clip_id is None:
            self._set_checked_silently(self.ui.chk001, False)
            self._footer("Select a stored clip to preview.", "warning")
            return
        try:
            store.preview(clip_id, in_context=self.ui.chk000.isChecked())
        except (KeyError, ValueError, RuntimeError) as exc:
            self._set_checked_silently(self.ui.chk001, False)
            self._footer(str(exc), "error")
            return
        self._footer("Previewing — scrub the range; uncheck Preview to end.")

    def b003(self) -> None:
        """Drop"""
        clip_id = self._selected_clip_id()
        if clip_id is None:
            self._footer("Select a stored clip to drop.", "warning")
            return
        clip = self.store.get_clip(clip_id)
        if clip is None:
            self.refresh()
            return
        answer = self.sb.message_box(
            f"Delete <b>{clip.label}</b> ({clip.key_count} keys) for good?",
            "Yes",
            "No",
        )
        if answer != "Yes":
            return
        self.store.drop(clip_id)
        self._footer(f"Dropped '{clip.label}'.")
