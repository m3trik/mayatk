# !/usr/bin/python
# coding=utf-8
"""Maya event lifecycle and hydration for Audio Clips.

Provides ``CallbacksMixin`` — manages Maya scriptJobs, OpenMaya
attribute-changed callbacks on the canonical carrier, and the
scene-load rehydration pipeline.

Single-scope model
------------------
With per-track attrs on :data:`~mayatk.audio_utils.CARRIER_NODE` there
is no longer a selection-driven "target" concept.  Everything is
scene-wide, so this module registers exactly ONE
``MNodeMessage.addAttributeChangedCallback`` on the carrier and fans
out to ``_schedule_deferred_sync`` when any ``audio_clip_*`` attr
changes.

Responsibilities:

- ``SceneOpened`` / ``NewSceneOpened`` → re-hydrate combo + re-register
  carrier callback.
- ``timeChanged`` → update the combo to reflect the currently-active
  track at the playhead (via :func:`audio_utils.tracks_on_at_frame`).
- ``MNodeMessage`` on the carrier → deferred sync when any track attr
  is edited.
"""
import logging

try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError:
    pass

from mayatk.audio_utils._audio_utils import AudioUtils as _audio_utils
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips


class CallbacksMixin:
    """Maya event lifecycle and hydration for single-scope audio."""

    # ------------------------------------------------------------------
    # Time-changed callback — combo reflects active track at playhead
    # ------------------------------------------------------------------

    def _on_time_changed(self):
        """Update the combo to show the track currently 'on' at the playhead.

        With N track attrs there is no single "current value".  This
        callback computes the set of tracks whose last key at-or-before
        the playhead has ``value=1`` and selects the first one in the
        combo.  Keeps the combo in sync with audible state during scrub.
        """
        if self._syncing_combo:
            return

        try:
            if not self.ui or not self.ui.isVisible():
                return
        except RuntimeError:
            return

        try:
            frame = cmds.currentTime(query=True)
        except Exception:
            return

        on_tracks = _audio_utils.tracks_on_at_frame(frame)
        current_tid = on_tracks[0] if on_tracks else None

        if current_tid == self._last_active_tid:
            return
        self._last_active_tid = current_tid

        cmb = self.ui.cmb000
        self._syncing_combo = True
        try:
            cmb.blockSignals(True)
            if current_tid:
                idx = cmb.findText(current_tid)
                cmb.setCurrentIndex(idx if idx >= 0 else -1)
            else:
                cmb.setCurrentIndex(-1)
            cmb.blockSignals(False)
            cmb.repaint()
        finally:
            self._syncing_combo = False

    # ------------------------------------------------------------------
    # ScriptJob lifecycle
    # ------------------------------------------------------------------

    def _ensure_sync_job(self):
        """Subscribe to scene and selection events via ScriptJobManager.

        Persistent subscriptions (``SceneOpened`` / ``NewSceneOpened``)
        re-create ephemeral subscriptions (``timeChanged``) and
        reinstall the carrier callback after a scene switch.
        """
        mgr = ScriptJobManager.instance()

        if not getattr(self, "_scene_subs_installed", False):
            mgr.subscribe("SceneOpened", self._on_scene_opened, owner=self)
            mgr.subscribe("NewSceneOpened", self._on_scene_opened, owner=self)
            mgr.connect_cleanup(self.ui, owner=self)
            self._scene_subs_installed = True

        if not getattr(self, "_time_token", None):
            self._time_token = mgr.subscribe(
                "timeChanged",
                self._on_time_changed,
                owner=self,
                ephemeral=True,
            )

        # Run initial hydration now.
        cmds.evalDeferred(self._hydrate_from_carrier)

    def _on_scene_opened(self):
        """Reset stale state and re-hydrate after a scene change."""
        log = logging.getLogger(__name__)
        log.debug("_on_scene_opened: resetting state for new scene")

        self._last_active_tid = None
        self._time_token = None
        self._attr_callback_ids = []

        self._ensure_sync_job()

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    def _hydrate_from_carrier(self):
        """Populate combo from tracks on the canonical carrier."""
        try:
            if not self.ui or not self.ui.isVisible():
                return
        except RuntimeError:
            return

        try:
            tracks = _audio_utils.list_tracks()
            file_map = _audio_utils.load_file_map()

            self._refresh_combo(tracks)

            if tracks:
                self.ui.footer.setText(
                    f"{len(tracks)} track(s) on {_audio_utils.CARRIER_NODE}"
                )
            else:
                self.ui.footer.setText("No audio tracks — browse to add.")

            # Offer migration if legacy data is detected.
            self._offer_legacy_migration()

            self._register_carrier_callback()

            # Activate composite if one exists.
            AudioClips.sync(composite=False, activate=False)  # lazy attach
            comp = AudioClips._find_composite_node()
            if comp:
                AudioClips.set_active(comp)
        except Exception:
            logging.getLogger(__name__).warning(
                "_hydrate_from_carrier error", exc_info=True
            )

    def _offer_legacy_migration(self):
        """Prompt to migrate legacy ``EventTriggers`` data if detected."""
        try:
            legacy = _audio_utils.detect_legacy()
        except Exception:
            return
        if not legacy:
            return

        from qtpy.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self.ui,
            "Migrate Legacy Audio Data",
            f"Found legacy 'audio_trigger' data on {len(legacy)} "
            "object(s).\n\n"
            "Migrate to the new per-track schema now?\n"
            "(Keyframes and file paths will be preserved.)",
            QMessageBox.Yes,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            migrated = _audio_utils.migrate_legacy_triggers(legacy)
            self.ui.footer.setText(f"Migrated {len(migrated)} legacy track(s).")
            # Re-hydrate to pick up newly migrated tracks.
            self._refresh_combo(_audio_utils.list_tracks())
            AudioClips.sync()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "migrate_legacy_triggers failed", exc_info=True
            )
            QMessageBox.warning(
                self.ui,
                "Migration Failed",
                f"Could not migrate legacy data:\n\n{exc}",
            )

    # ------------------------------------------------------------------
    # OpenMaya attribute-changed callback on the carrier
    # ------------------------------------------------------------------

    def _register_carrier_callback(self):
        """Install a single ``MNodeMessage`` on the canonical carrier.

        Fires ``_on_carrier_attr_changed`` when any ``audio_clip_*``
        attr is edited.  Re-called after scene change.
        """
        self._cleanup_attr_callbacks()

        carrier = _audio_utils.CARRIER_NODE
        if not cmds.objExists(carrier):
            return

        try:
            import maya.api.OpenMaya as om2

            sel = om2.MSelectionList()
            sel.add(carrier)
            mobj = sel.getDependNode(0)

            _RELEVANT = (
                om2.MNodeMessage.kAttributeSet
                | om2.MNodeMessage.kConnectionBroken
                | om2.MNodeMessage.kAttributeAdded
                | om2.MNodeMessage.kAttributeRemoved
            )
            _ATTR_PREFIX = _audio_utils.ATTR_PREFIX

            def _on_attr_changed(msg, plug, other_plug, *args):
                try:
                    if self._syncing_combo:
                        return
                    if not (msg & _RELEVANT):
                        return
                    if not self.ui or not self.ui.isVisible():
                        return

                    attr_name = plug.partialName(useLongNames=True)
                    if not attr_name.startswith(_ATTR_PREFIX):
                        return

                    self._schedule_deferred_sync()
                except RuntimeError:
                    pass
                except Exception:
                    logging.getLogger(__name__).debug(
                        "_on_attr_changed error", exc_info=True
                    )

            cb_id = om2.MNodeMessage.addAttributeChangedCallback(mobj, _on_attr_changed)
            self._attr_callback_ids.append(cb_id)
        except Exception:
            logging.getLogger(__name__).debug(
                "_register_carrier_callback failed", exc_info=True
            )

    def remove_callbacks(self):
        """Remove all subscriptions and OpenMaya callbacks owned by this instance."""
        self._cleanup_attr_callbacks()
        ScriptJobManager.instance().unsubscribe_all(self)
        self._scene_subs_installed = False
        self._time_token = None

    def _cleanup_attr_callbacks(self):
        ids = self._attr_callback_ids
        if not ids:
            return
        try:
            import maya.api.OpenMaya as om2

            for cb_id in ids:
                try:
                    om2.MMessage.removeCallback(cb_id)
                except Exception:
                    pass
        except ImportError:
            pass
        self._attr_callback_ids = []

    # ------------------------------------------------------------------
    # Deferred sync coalescing
    # ------------------------------------------------------------------

    def _schedule_deferred_sync(self):
        """Coalesce a full ``AudioClips.sync()`` on next idle tick."""
        if getattr(self, "_deferred_sync_pending", False):
            return
        self._deferred_sync_pending = True
        try:
            cmds.evalDeferred(self._run_deferred_sync, lowestPriority=True)
        except Exception:
            # Maya not ready — drop the flag so a subsequent edit retries.
            self._deferred_sync_pending = False

    def _run_deferred_sync(self):
        """Idle-tick handler — refresh combo + rebuild composite."""
        self._deferred_sync_pending = False
        try:
            if not self.ui or not self.ui.isVisible():
                return
        except RuntimeError:
            return

        try:
            AudioClips.sync()
            self._refresh_combo(_audio_utils.list_tracks())
        except Exception:
            logging.getLogger(__name__).debug("_run_deferred_sync error", exc_info=True)
