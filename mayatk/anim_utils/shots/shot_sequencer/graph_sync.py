# !/usr/bin/python
# coding=utf-8
"""Graph editor glue: the shot sequencer's curves in uitk's graph editor (Maya).

:class:`GraphSync` is a collaborator the panel composes beside its controller
(not one more controller mixin): it feeds the sequencer widget's ``"graph"``
editor the visible shot's objects and their animation curves -- read through
:meth:`mayatk.AnimUtils.get_key_curves` -- and commits the editor's edits.

A key's TIME goes through the controller's own shot-aware key path
(``_expand_and_compensate`` + ``_commit_curve_moves`` inside one
``store.scene_edit``), so a graph move grows and ripples shot bounds and moves
the system's claims exactly as a track-row key drag does; its VALUE through
:meth:`AnimUtils.shift_key_values`, first, at the keys' old times.  Tangents,
types, weighting, infinity, deletes and inserts are ``keyTangent`` /
``setAttr`` / ``cutKey`` / ``setKeyframe -insert`` edits.  The shot system's
claimed samples shape the curves but draw no key, so they cannot be edited
from here.  After an edit the controller rebuilds the widget, and the
widget's ``content_rebuilt`` brings the graph back in sync.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.script_job_manager import ScriptJobManager

__all__ = ["GraphSync"]


class GraphSync:
    """Feeds the sequencer's graph editor and commits its edits (module docstring).

    Parameters:
        controller: The panel's ``ShotSequencerController``.
        widget: Its ``SequencerWidget``.
    """

    #: Maya's tangent types, as the graph editor offers them.
    TANGENT_TYPES = (
        ("auto", "Auto"),
        ("spline", "Spline"),
        ("clamped", "Clamped"),
        ("linear", "Linear"),
        ("flat", "Flat"),
        ("step", "Stepped"),
        ("stepnext", "Stepped Next"),
        ("plateau", "Plateau"),
    )
    #: Tangent types Maya refuses as an IN tangent (set on the out side only).
    OUT_ONLY_TYPES = ("step",)
    #: ``pythontk.KeyCurve`` infinity mode -> the animCurve's enum.
    INFINITY = {
        "constant": 0,
        "linear": 1,
        "cycle": 3,
        "cycle_offset": 4,
        "oscillate": 5,
    }
    #: Channel order in the outline; other attributes follow alphabetically.
    CHANNEL_ORDER = tuple(
        f"{a}{x}" for a in ("translate", "rotate", "scale") for x in "XYZ"
    ) + ("visibility",)

    def __init__(self, controller, widget):
        self.controller = controller
        self.widget = widget
        self.editor = widget.editor("graph")
        self._dirty = True
        self._plugs: Dict[str, str] = {}  # curve -> "obj.attr"
        self._owners: Dict[str, Tuple[str, str]] = {}  # curve -> (obj, attr)
        self._connections: List[Tuple[object, object]] = []
        #: Curves Maya reported edited since the last sync (re-read only those).
        self._edited: set = set()
        self._refetch_all = False
        self._outline_cache: Optional[list] = None
        self._follow_queued = False
        self._window: Optional[Tuple[float, float]] = None
        #: Attributes a Live Update drag wrote (handed back on cancel).
        self._live_plugs: set = set()
        self._subscribe()
        self.editor.set_tangent_types(self.TANGENT_TYPES)
        self.editor.set_curve_provider(self._provide)
        for signal, slot in (
            (widget.content_rebuilt, self._on_rebuilt),
            (widget.editor_changed, self._on_editor_changed),
            (self.editor.keys_edited, self.on_keys_edited),
            (self.editor.tangents_edited, self.on_tangents_edited),
            (self.editor.keys_deleted, self.on_keys_deleted),
            (self.editor.keys_inserted, self.on_keys_inserted),
            (self.editor.tangent_type_requested, self.on_tangent_type),
            (self.editor.tangents_broken, self.on_tangents_broken),
            (self.editor.weighted_requested, self.on_weighted),
            (self.editor.infinity_requested, self.on_infinity),
            (self.editor.key_selection_changed, self.on_key_selection),
            (self.editor.key_menu_requested, self.on_key_menu),
            (self.editor.curves_previewed, self._on_preview),
            (self.editor.preview_cancelled, self._on_preview_cancelled),
        ):
            signal.connect(slot)
            self._connections.append((signal, slot))
        controller.register_key_targets(self.editor.name, self.key_targets)
        if widget.active_editor is self.editor:
            self.sync()

    #: Past this many edited curves the burst is a bake: re-read everything.
    EDITED_CAP = 2000

    def _subscribe(self) -> None:
        """Maya's curve edits and selection, through the ScriptJobManager."""
        if cmds is None:
            return
        try:
            import maya.api.OpenMayaAnim as oma
        except ImportError:  # pragma: no cover - no OpenMaya outside Maya
            return
        mgr = ScriptJobManager.instance()
        mgr.subscribe("SelectionChanged", self._on_maya_selection, owner=self)
        for event in ("SceneOpened", "NewSceneOpened"):
            mgr.subscribe(event, self._on_scene_changed, owner=self)
        mgr.add_om_callback(
            oma.MAnimMessage.addAnimCurveEditedCallback,
            self._on_curves_edited,
            owner=self,
        )
        # Off with the widget: a closed panel's subscriptions held it.
        mgr.connect_cleanup(self.widget, owner=self)

    def _on_curves_edited(self, curves, _client_data=None) -> None:
        """Bank the edited curves' names (no Qt work inside a Maya callback)."""
        if self._refetch_all:
            return
        import maya.api.OpenMaya as om2

        try:
            for obj in curves:
                self._edited.add(om2.MFnDependencyNode(obj).name())
        except Exception:  # pragma: no cover - an unreadable payload
            self._refetch_all = True
        if len(self._edited) > self.EDITED_CAP:
            self._edited.clear()
            self._refetch_all = True

    def _on_scene_changed(self) -> None:
        """Another scene reuses curve names: nothing cached is still true."""
        self._edited.clear()
        self._refetch_all = True
        self._outline_cache = None
        self._dirty = True

    def _on_maya_selection(self) -> None:
        if self.widget.active_editor is self.editor and not self._follow_queued:
            from qtpy import QtCore

            self._follow_queued = True
            QtCore.QTimer.singleShot(0, self._follow_selection)

    def _follow_selection(self) -> None:
        """Show the selected objects' curves, as Maya's Graph Editor loads them."""
        self._follow_queued = False
        if cmds is None or not self._connections:  # detached while queued
            return
        if self.widget.active_editor is not self.editor or not self.editor.auto_load:
            return  # another view, or the display pinned
        selected = set(cmds.ls(selection=True, objectsOnly=True, long=True) or [])
        if not selected:
            return
        ids = [
            entry["id"]
            for entry in self._outline_cache or ()  # what this sync gave the editor
            if set(cmds.ls(entry["id"], long=True) or []) & selected
        ]
        if ids:
            self.editor.focus_objects(ids)

    def detach(self) -> None:
        """Disconnect everything (a panel re-init builds a new one)."""
        ScriptJobManager.instance().unsubscribe_all(self)
        if (
            self.controller._key_target_sources.get(self.editor.name)
            == self.key_targets
        ):
            self.controller.register_key_targets(self.editor.name, None)
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # the widget went first
        self._connections = []

    # -- feeding the editor -------------------------------------------------------

    def _on_rebuilt(self) -> None:
        if self.widget.active_editor is self.editor:
            self.sync()
        else:
            self._dirty = True  # rebuilt on the next switch to the graph

    def _on_editor_changed(self, name: str) -> None:
        if name == self.editor.name:
            if self._dirty or self._edited or self._refetch_all:
                self.sync()
            self._follow_selection()

    def sync(self, full: bool = False) -> None:
        """Bring the graph in line with the scene.

        The outline is re-derived (cheap) and re-set only when it changed;
        of the curves, only those Maya reported edited are re-read -- every
        displayed one on *full*, or after a bake-sized burst.
        """
        outline = self.outline()
        if outline != self._outline_cache:
            self._outline_cache = outline
            self.editor.set_outline(outline)
        # What the scope shows is what the graph keeps bright (Dim Outside Shots).
        if self.editor.focus_range != self._window:
            self.editor.focus_range = self._window
        self._flush(full)
        self._dirty = False

    def _flush(self, full: bool = False) -> None:
        """Re-read the curves banked as edited (all shown ones on *full*)."""
        if full or self._refetch_all:
            self.editor.refresh()
        elif self._edited:
            self.editor.invalidate(self._edited)
        self._edited.clear()
        self._refetch_all = False

    def _visible(self) -> Tuple[List[str], Optional[Tuple[float, float]]]:
        """The visible shots' members (the active shot's first, hidden ones
        out) and the frame window those shots span.

        Read from the store, not from the track rows: those are not built
        while the graph shows (``WidgetSyncMixin._rebuild_content``).
        """
        ctl = self.controller
        seq = ctl.sequencer
        active = seq.shot_by_id(ctl.active_shot_id) if seq is not None else None
        if active is None:
            return [], None
        shots = [active] + [s for s in ctl._visible_shots(active) if s is not active]
        out: List[str] = []
        for shot in shots:
            for obj in shot.objects:
                if obj not in out and not seq.is_object_hidden(obj):
                    out.append(obj)
        window = (min(s.start for s in shots), max(s.end for s in shots))
        return out, window

    def outline(self) -> List[dict]:
        """The visible shots' objects keyed inside their window, and their curves."""
        colors = self.widget.attribute_colors
        entries = []
        objects, window = self._visible()
        self._window = window
        for obj in objects:
            pairs = self._curves_of(obj)
            if not pairs:
                continue
            # A member whose keys all sit on one side of the shots on screen
            # draws a flat line there: it stays out.  One keyed on both
            # sides animates through them, a key inside or not.
            if window is not None:
                inside, before, after = AnimUtils.curve_key_spans(
                    [c for c, _a in pairs],
                    [window, (None, window[0]), (window[1], None)],
                )
                if inside is None and (before is None or after is None):
                    continue
            children = []
            for curve, attr in pairs:
                self._plugs[curve] = f"{obj}.{attr}"
                self._owners[curve] = (obj, attr)
                children.append(
                    {"curve_id": curve, "label": attr, "color": colors.get(attr)}
                )
            if children:
                label = obj.rsplit("|", 1)[-1]
                entries.append({"id": obj, "label": label, "children": children})
        return entries

    def _curves_of(self, obj: str) -> List[Tuple[str, str]]:
        """``[(curve, attribute), ...]`` driving *obj* straight from time."""
        if cmds is None or not cmds.objExists(obj):
            return []
        pairs = (
            cmds.listConnections(
                obj,
                source=True,
                destination=False,
                type="animCurve",
                connections=True,
                skipConversionNodes=True,
            )
            or []
        )
        out = {}
        for plug, curve in zip(pairs[::2], pairs[1::2]):
            if cmds.listConnections(f"{curve}.input", source=True, destination=False):
                continue  # set-driven: its keys are driver values, not frames
            out[curve] = plug.split(".", 1)[-1]
        order = {name: i for i, name in enumerate(self.CHANNEL_ORDER)}
        return sorted(
            out.items(), key=lambda item: (order.get(item[1], len(order)), item[1])
        )

    def _provide(self, curve_ids) -> list:
        """KeyCurves for *curve_ids*, the shot system's samples drawn keyless."""
        ledger = self._ledger()
        curves = AnimUtils.get_key_curves(curve_ids)
        for curve in curves:
            claimed = ledger.key_times(curve.curve_id) if ledger is not None else ()
            for h in claimed:  # a handful per curve; the curve's keys bisected
                i = curve.index_at(h)
                if i >= 0:
                    curve.keys[i].hidden = True
            plug = self._plugs.get(curve.curve_id)
            if plug:
                curve.label = plug
        return curves

    # -- commits ------------------------------------------------------------------

    def _ledger(self):
        seq = self.controller.sequencer
        return seq.ledger if seq is not None else None

    def _shot_at(self, t: float, dt: float = 0.0, shots=None) -> Optional[int]:
        """The shot a key at *t* is edited as part of: the one holding it.  On
        a seam two flush shots share, the one a move by *dt* goes into -- the
        later shot for a move right, else the earlier -- so a seam key moved
        into a shot grows none.  *shots*: ``sorted_shots()``, read once by a
        caller asking for many keys."""
        if shots is None:
            seq = self.controller.sequencer
            if seq is None:
                return None
            shots = seq.sorted_shots()
        hits = [s for s in shots if s.start - 1e-3 <= t <= s.end + 1e-3]
        if not hits:
            return None
        return (hits[-1] if dt > 0 else hits[0]).shot_id

    def on_keys_edited(self, edits) -> None:
        """Commit a move / scale / typed stat: ``[(curve, [(t, dt, dv), ...])]``."""
        ctl = self.controller
        seq = ctl.sequencer
        if seq is None or not edits:
            return
        values: Dict[str, list] = {}
        curve_moves: Dict[str, dict] = {}
        shot_extents: Dict[int, tuple] = {}
        shots = seq.sorted_shots()
        for curve, rows in edits:
            shifted = [(t, dv) for t, _dt, dv in rows if abs(dv) > 1e-9]
            if shifted:
                values[curve] = shifted
            pairs = []
            for t, dt, _dv in rows:
                if abs(dt) <= 1e-6:
                    continue
                sid = self._shot_at(t, dt, shots)
                pairs.append((t, t + dt, sid))
                if sid is not None:
                    lo, hi = shot_extents.get(sid, (t + dt, t + dt))
                    shot_extents[sid] = (min(lo, t + dt), max(hi, t + dt))
            if pairs:
                plug = self._plugs.get(curve, curve)
                curve_moves[curve] = {"pairs": pairs, "plug": plug}
        if not values and not curve_moves:
            self._on_preview_cancelled()  # nothing to commit: hand live writes back
            return
        n = sum(len(rows) for _c, rows in edits)
        bounds = self._bounds()
        with self._edit("graphkeys"):
            # Values first, at the keys' old times: the time commit's
            # recreate path snapshots the keys as they then are.
            if values:
                AnimUtils.shift_key_values(values)
            if curve_moves:
                # The graph records no press modifiers, and Shift there
                # constrains an axis: the shot grows over the move whatever a
                # tracks gesture left in the widget's flags (Shift: free).
                from qtpy import QtCore

                self.widget.record_press_modifiers(QtCore.Qt.NoModifier)
                ctl._expand_and_compensate(curve_moves, shot_extents)
                ctl._commit_curve_moves(curve_moves, ledger=seq.ledger)
                # Times moved: the system's claims and gap holds follow.  A
                # value-only edit leaves both alone (they are about times),
                # and skipping the reconcile is most of its cost.
                seq.reconcile_system_edits()
        self._after(
            f"Moved {n} key{'s' if n != 1 else ''} in the graph",
            bounds=self._bounds() != bounds,
            curves=[c for c, _rows in edits],
        )

    def on_tangents_edited(self, rows) -> None:
        """Commit dragged handles: ``[(curve, [(t, side, dt, dv, broken), ...])]``."""
        from mayatk.anim_utils.shots.shot_sequencer.key_menu import KeyMenuMixin

        n = 0
        with self._edit(None, "Graph tangents"):
            for curve, items in rows:
                weighted = bool(cmds.getAttr(f"{curve}.weightedTangents"))
                for t, side, dt, dv, broken in items:
                    angle, weight = KeyMenuMixin.tangent_from_handle(side, dt, dv)
                    flags = {f"{side}Angle": angle}
                    if weighted:
                        flags[f"{side}Weight"] = weight
                    tt = (t - 1e-3, t + 1e-3)
                    if broken:
                        # Unlocked BEFORE the angle, or the other side swings too.
                        cmds.keyTangent(curve, edit=True, time=tt, lock=False)
                    cmds.keyTangent(curve, edit=True, time=tt, **flags)
                    n += 1
        self._after(
            f"Tangents set on {n} key{'s' if n != 1 else ''}",
            bounds=False,
            curves=[c for c, _i in rows],
        )

    def on_keys_deleted(self, groups) -> None:
        """Cut keys: ``[(curve, [time, ...])]``; their claims go with them."""
        seq = self.controller.sequencer
        if seq is None:
            return
        n = 0
        bounds = self._bounds()
        with self._edit("graphdelkeys"):
            release = seq.store.release_replaced()
            for curve, times in groups:
                for t in times:
                    cmds.cutKey(curve, time=(t - 1e-3, t + 1e-3), clear=True)
                    release(curve, [t])
                    n += 1
            seq.reconcile_system_edits()
        self._after(
            f"Deleted {n} key{'s' if n != 1 else ''}",
            bounds=self._bounds() != bounds,
            curves=[c for c, _t in groups],
            outline=True,
        )

    def on_keys_inserted(self, items) -> None:
        """Key ``[(curve, time), ...]`` without changing the curves' shape."""
        seq = self.controller.sequencer
        if seq is None:
            return
        bounds = self._bounds()
        with self._edit("graphinsert"):
            for curve, t in items:
                cmds.setKeyframe(curve, time=t, insert=True)
            seq.reconcile_system_edits()
        self._after(
            f"Inserted {len(items)} key{'s' if len(items) != 1 else ''}",
            bounds=self._bounds() != bounds,
            curves=[c for c, _t in items],
        )

    def on_tangent_type(self, type_id: str, groups) -> None:
        """Set tangent type *type_id* on ``[(curve, [times])]`` *groups* (an
        out-only type, ``step``, on the out side alone)."""
        flags = {"outTangentType": type_id}
        if type_id not in self.OUT_ONLY_TYPES:
            flags["inTangentType"] = type_id
        self._key_tangent_edit(groups, flags, f"{type_id} tangents")

    def on_tangents_broken(self, broken: bool, groups) -> None:
        """Break (or unify) the tangents of ``[(curve, [times])]`` *groups*."""
        self._key_tangent_edit(
            groups, {"lock": not broken}, "broken" if broken else "unified"
        )

    def _key_tangent_edit(self, groups, flags: dict, what: str) -> None:
        n = 0
        with self._edit(None, "Graph tangents"):
            for curve, times in groups:
                # Exact ``(t, t)`` pairs: given several WIDENED ranges,
                # ``keyTangent -e`` silently edits nothing (measured, 2025).
                # The times are Maya's own (read through get_key_curves).
                tt = tuple((t, t) for t in times)
                cmds.keyTangent(curve, edit=True, time=tt, **flags)
                n += len(times)
        self._after(
            f"Tangents {what} on {n} key{'s' if n != 1 else ''}",
            bounds=False,
            curves=[c for c, _t in groups],
        )

    def on_weighted(self, weighted: bool, curves) -> None:
        """Make *curves* weighted-tangent curves, or not."""
        with self._edit(None, "Graph weighting"):
            for curve in curves:
                cmds.keyTangent(curve, edit=True, weightedTangents=bool(weighted))
        self._after(
            f"{'Weighted' if weighted else 'Non-weighted'}: {len(curves)} curves",
            bounds=False,
            curves=list(curves),
        )

    def on_infinity(self, side: str, mode: str, curves) -> None:
        """Set *curves*' ``pre`` / ``post`` (else both) infinity to *mode*,
        a :data:`pythontk.KeyCurve` infinity name (:attr:`INFINITY`)."""
        value = self.INFINITY.get(mode)
        if value is None:
            return
        attrs = {"pre": ("preInfinity",), "post": ("postInfinity",)}.get(
            side, ("preInfinity", "postInfinity")
        )
        with self._edit(None, "Graph infinity"):
            for curve in curves:
                for attr in attrs:
                    cmds.setAttr(f"{curve}.{attr}", value)
        self._after(
            f"{side.title()} infinity: {mode.replace('_', ' ')}",
            bounds=False,
            curves=list(curves),
        )

    def key_targets(self, groups=None) -> list:
        """``[(obj, attr, [times], shot_id), ...]`` for the graph's key
        selection (or *groups*, ``[(curve, [times])]``): the shape the key
        menu and the key shortcuts act on (``KeyMenuMixin._key_targets``)."""
        if groups is None:
            groups = self.editor.selected_keys()
        targets = []
        for curve, times in groups:
            owner = self._owners.get(curve)
            if owner is None or not times:
                continue
            times = sorted(times)
            targets.append(owner + (times, self._shot_at(times[0])))
        return targets

    def on_key_menu(self, menu, groups) -> None:
        """The track view's key edits and Move / Copy to Shot, on the graph's
        selection -- one key menu, whichever view shows it."""
        targets = self.key_targets(groups)
        if not targets:
            return
        n = sum(len(times) for _o, _a, times, _s in targets)
        self.controller._add_selection_key_actions(
            menu, targets, f" ({n})" if n > 1 else ""
        )

    def _on_preview(self, rows) -> None:
        """Live Update: each curve's value at the current frame, as the drag
        shapes it, straight onto its attribute -- what the viewport shows --
        with undo off.  No key changes until the release commits; a cancel
        hands the attributes back to their curves."""
        if cmds is None:
            return
        now = cmds.currentTime(q=True)
        with CoreUtils.undo_disabled():
            for cid, curve in rows:
                plug = self._plugs.get(cid)
                if not plug:
                    continue
                try:
                    cmds.setAttr(plug, curve.evaluate(now))
                except RuntimeError:
                    continue  # locked, or no plain value to set
                self._live_plugs.add(plug)

    def _on_preview_cancelled(self) -> None:
        """A live drag ended with no edit: the attributes read their curves again."""
        if cmds is not None and self._live_plugs:
            cmds.dgdirty(list(self._live_plugs))
            with CoreUtils.undo_disabled():
                cmds.currentTime(cmds.currentTime(q=True), update=True)
        self._live_plugs.clear()

    def on_key_selection(self, groups) -> None:
        """Mirror the graph's key selection into Maya's (not an undo step)."""
        if cmds is None:
            return
        with CoreUtils.undo_disabled():
            AnimUtils.clear_key_selection()
            for curve, times in groups:
                tt = tuple((t, t) for t in times)  # exact pairs, as above
                if tt and cmds.objExists(curve):
                    cmds.selectKey(curve, add=True, time=tt)

    # -- plumbing -----------------------------------------------------------------

    @contextmanager
    def _edit(self, store_label: Optional[str], chunk_label: str = ""):
        """The bracket an edit runs in: guarded against the controller's own
        keyframe callbacks, and ONE undo step -- a shot-aware
        ``store.scene_edit`` (a restore point for the bounds) when
        *store_label* is given, a plain undo chunk otherwise."""
        ctl = self.controller
        was = ctl._syncing
        ctl._syncing = True
        try:
            if store_label is not None and ctl.sequencer is not None:
                bracket = ctl.sequencer.store.scene_edit(store_label)
            else:
                bracket = CoreUtils.undo_chunk(chunk_label or "Graph edit")
            with bracket:
                yield
        finally:
            ctl._syncing = was
            self._dirty = True

    def _bounds(self) -> list:
        seq = self.controller.sequencer
        return [(s.shot_id, s.start, s.end) for s in seq.sorted_shots()] if seq else []

    def _after(
        self, footer: str, bounds: bool = True, curves=None, outline: bool = False
    ) -> None:
        """Bring the panel and the graph back in line with the scene, and report.

        An edit that moved a shot bound rebuilds the panel (the shot lane,
        overlays and combobox follow; the graph re-syncs on the rebuild).
        One that did not re-reads only the curves it touched -- *curves*,
        and any Maya reported edited (a compensation's) -- the rows hidden
        and marked stale until the track editor shows again; *outline*
        re-derives the outline too (a curve cut to no keys is gone).
        """
        ctl = self.controller
        # Every shot's: the graph edits any shot's keys (Adjacent / All), and
        # a cached shot's segments are not collected again.
        ctl._segment_cache.clear()
        ctl._sub_row_cache.clear()
        self._live_plugs.clear()  # the commit re-evaluates them from the keys
        self._edited.update(curves or ())
        if bounds or curves is None:
            ctl._sync_to_widget()
            if bounds:
                ctl._sync_combobox()
        else:
            ctl._content_stale = True
            if outline:
                self.sync()
            else:
                self._flush()
            self._dirty = False
        # A rebuild that returned early raised no ``content_rebuilt``.
        if self._dirty and self.widget.active_editor is self.editor:
            self.sync()
        ctl._set_footer(footer)
