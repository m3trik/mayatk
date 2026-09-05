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

    A thin driver: each button resolves the active store and calls one method
    on it; the clip list repaints from the store's change events, so a stash
    made from the Shot Sequencer's clip menu shows up here without any wiring
    between the two panels.
    """

    SOURCES = ("Selected Keys", "Timeline Selection", "Playback Range")
    RETRIEVE_AT = ("Original Frames", "Current Time")

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.key_stash
        self._bound_store: Optional[KeyStash] = None
        # Deferred: child widgets aren't wired onto self.ui until
        # register_children runs after __init__ returns.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    # ---- setup -----------------------------------------------------------

    def _initialize_ui(self) -> None:
        self.ui.cmb000.add(list(self.SOURCES))
        self.ui.cmb001.add(list(self.RETRIEVE_AT))
        self.ui.tree000.setHeaderLabels(["Clip", "Objects", "Range", "Stored"])
        self.ui.tree000.itemSelectionChanged.connect(self._sync_buttons)
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
        for col in range(4):
            tree.resizeColumnToContents(col)
        self._sync_buttons()

    def _selected_clip_id(self) -> Optional[int]:
        from qtpy import QtCore

        items = self.ui.tree000.selectedItems()
        if not items:
            return None
        return items[0].data(0, QtCore.Qt.UserRole)

    def _sync_buttons(self) -> None:
        clip_id = self._selected_clip_id()
        has_clip = clip_id is not None
        self.ui.b001.setEnabled(has_clip)
        self.ui.b003.setEnabled(has_clip)
        previewing = self._bound_store is not None and self._bound_store.is_previewing()
        self.ui.b002.setEnabled(has_clip or previewing)
        self.ui.b002.blockSignals(True)
        self.ui.b002.setChecked(previewing)
        self.ui.b002.blockSignals(False)

    def _footer(self, msg: str, level: str = "info") -> None:
        self.ui.footer.setText(msg, level=level)
        getattr(self.logger, level if level != "success" else "info")(msg)

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

    def b002(self) -> None:
        """Preview (toggle)"""
        store = self.store
        if not self.ui.b002.isChecked():
            store.end_preview()
            self._footer("Preview ended.")
            return
        clip_id = self._selected_clip_id()
        if clip_id is None:
            self.ui.b002.setChecked(False)
            self._footer("Select a stored clip to preview.", "warning")
            return
        try:
            store.preview(clip_id, in_context=self.ui.chk000.isChecked())
        except (KeyError, ValueError, RuntimeError) as exc:
            self.ui.b002.setChecked(False)
            self._footer(str(exc), "error")
            return
        self._footer("Previewing — scrub the range; uncheck Preview to end.")

    def b003(self) -> None:
        """Drop"""
        from qtpy import QtWidgets

        clip_id = self._selected_clip_id()
        if clip_id is None:
            self._footer("Select a stored clip to drop.", "warning")
            return
        clip = self.store.get_clip(clip_id)
        answer = QtWidgets.QMessageBox.question(
            self.ui,
            "Drop stored keys",
            f"Delete '{clip.label}' ({clip.key_count} keys) for good?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.store.drop(clip_id)
        self._footer(f"Dropped '{clip.label}'.")
