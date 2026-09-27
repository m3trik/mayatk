# !/usr/bin/python
# coding=utf-8
"""Key context menus and key-selection edits.

Provides :class:`KeyMenuMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. The key menu
(tangent types, break/unify, Move to Shot), dragged tangent handles, and the
edits run over a key selection: simplify, thin, snap, invert, align, copy /
paste and stash.
"""

from qtpy import QtWidgets

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.core_utils._core_utils import CoreUtils


class KeyMenuMixin:
    """Key context menus, tangent edits and drags, and the key-selection edits."""

    # -- key context menu ---------------------------------------------------

    #: Tangent types a key's context menu offers, in the Graph Editor's order:
    #: ``(label, keyTangent type)``.
    _TANGENT_TYPES = (
        ("Auto", "auto"),
        ("Spline", "spline"),
        ("Clamped", "clamped"),
        ("Linear", "linear"),
        ("Flat", "flat"),
        ("Step", "step"),
        ("Plateau", "plateau"),
    )

    def _key_targets(self, widget, key_groups: list) -> list:
        """``[(obj, attr, [times], shot_id), ...]`` for a key selection.

        One entry per writable sub-row clip in *key_groups* (the payload of
        ``key_selection_changed`` / ``key_menu_requested``); read-only clips
        and object-level rows (no attribute) contribute nothing.
        """
        targets = []
        for group in key_groups:
            clip = widget.get_clip(group["clip_id"])
            if clip is None or clip.data.get("read_only"):
                continue
            obj = clip.data.get("obj")
            attr = clip.data.get("attr_name")
            times = sorted(group.get("times") or [])
            if not obj or not attr or not times:
                continue
            targets.append((obj, attr, times, clip.data.get("shot_id")))
        return targets

    @staticmethod
    def _key_targets_to_sequences(targets: list) -> list:
        """The key-level sequence dicts ``move_sequences_to_shot`` takes."""
        return [
            {
                "kind": "anim",
                "obj": obj,
                "attr": attr,
                "times": list(times),
                "start": times[0],
                "end": times[-1],
            }
            for obj, attr, times, _sid in targets
        ]

    def on_key_menu(self, menu, key_groups: list) -> None:
        """Add the key actions to a key's context menu.

        Mirrors the Graph Editor's own right-click: a Tangents submenu that
        sets both sides, In/Out submenus for one side, Break / Unify, and --
        the sequencer's own -- Move to Shot, which sends exactly the selected
        keys, per attribute, as sequences.  It also carries the Animation
        panel's key edits (:meth:`_add_key_edit_actions`), which belong to a
        key SELECTION: they were briefly offered per SHOT, where "remove the
        intermediate keys" meant every member's every attribute across the
        whole span -- a far bigger edit than the words promise.  The widget
        appends Delete.
        """
        if cmds is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        targets = self._key_targets(widget, key_groups)
        if not targets:
            return
        from qtpy import QtWidgets

        n = sum(len(t) for _o, _a, t, _s in targets)
        suffix = f" ({n})" if n > 1 else ""

        # Explicit parents: an ``addMenu(str)`` wrapper can go stale once this
        # frame drops it (PySide 6.11), and the actions bind to the submenu.
        for label, sides in (
            ("Tangents", ("in", "out")),
            ("In Tangent", ("in",)),
            ("Out Tangent", ("out",)),
        ):
            sub = QtWidgets.QMenu(label, menu)
            menu.addMenu(sub)
            for name, tangent in self._TANGENT_TYPES:
                act = sub.addAction(name)
                act.triggered.connect(
                    lambda _checked=False, t=tangent, s=sides: self._set_key_tangents(
                        targets, t, s
                    )
                )
        act_break = menu.addAction(f"Break Tangents{suffix}")
        act_break.triggered.connect(lambda: self._lock_key_tangents(targets, False))
        act_unify = menu.addAction(f"Unify Tangents{suffix}")
        act_unify.triggered.connect(lambda: self._lock_key_tangents(targets, True))

        self._add_key_edit_actions(menu, targets, suffix)

        if self.sequencer:
            seqs = self._key_targets_to_sequences(targets)
            shots = self.sequencer.sorted_shots()
            if seqs and len(shots) > 1:
                menu.addSeparator()
                move_menu = QtWidgets.QMenu(f"Move to Shot{suffix}", menu)
                menu.addMenu(move_menu)
                self._populate_move_to_shot(move_menu, seqs, noun="key")

    def _set_key_tangents(self, targets: list, tangent: str, sides=("in", "out")):
        """Set the tangent type on the selected keys (one or both sides)."""
        kwargs = {}
        if "in" in sides:
            kwargs["inTangentType"] = tangent
        if "out" in sides:
            kwargs["outTangentType"] = tangent
        side = "" if len(sides) == 2 else f" {sides[0]}"
        self._edit_key_tangents(targets, kwargs, f"{tangent}{side}")

    def _lock_key_tangents(self, targets: list, lock: bool) -> None:
        """Break (``lock=False``) or unify the selected keys' tangents."""
        self._edit_key_tangents(
            targets, {"lock": lock}, "unified" if lock else "broken"
        )

    @staticmethod
    def tangent_from_handle(side: str, dt: float, dv: float) -> tuple:
        """``(angle_degrees, weight)`` of the tangent a dragged handle stands for.

        The inverse of how the preview places its control points
        (``SegmentCollector.build_curve_preview``): an OUT handle sits
        ``weight * (cos a, sin a)`` after its key and an IN handle the same
        distance BEFORE it, so the IN vector points back along the tangent
        and is flipped before its angle is read.  The weight is the handle's
        length in curve units, which is what a weighted curve stores.
        """
        import math

        if side == "in":
            dt, dv = -dt, -dv
        return math.degrees(math.atan2(dv, dt)), math.hypot(dt, dv)

    def on_keys_tangent_dragged(self, groups: list, side: str, broken: bool) -> None:
        """Write the tangents a dragged handle asks for on the keys' curves.

        *groups* is the whole gesture -- ``[(clip_id, [(time, dt, dv), ...]),
        ...]``, one handle VECTOR per key the drag carried, since a tangent
        drag carries the key SELECTION unless the user held Ctrl.  Angle
        always; weight too when the curve carries weighted tangents
        (``weightedTangents``), since only then does the handle's length mean
        anything -- an unweighted curve's control point sits a third of the
        span out whatever the drag did.  Maya turns the edited side ``fixed``
        and, on a unified key, swings the other side with it; *broken* (the
        Alt drag) unlocks the key first so it stops doing that.

        One command per curve per distinct set of flags, all in one undo
        chunk: the drag is one gesture and takes one Ctrl+Z.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        # Keyed by row, not by time alone: two clips can be the same frame on
        # different objects, and each carries its own vector.
        vectors = {}
        for clip_id, entries in groups:
            clip = widget.get_clip(clip_id)
            if clip is None:
                continue
            row = (clip.data.get("obj"), clip.data.get("attr_name"))
            for time, dt, dv in entries:
                vectors[row + (round(float(time), 6),)] = (dt, dv)
        targets = self._key_targets(
            widget,
            [
                {"clip_id": clip_id, "times": [t for t, _dt, _dv in entries]}
                for clip_id, entries in groups
            ],
        )
        if not targets:
            return

        weighted = {}  # curve -> weightedTangents, asked once per curve

        def resolve(obj, attr, curve, time):
            vector = vectors.get((obj, attr, round(float(time), 6)))
            if vector is None:
                return {}
            angle, weight = self.tangent_from_handle(side, *vector)
            kwargs = {}
            if broken:
                # Unlocked FIRST: a unified key swings its other side with
                # whatever it is told next, which is the whole point of Alt.
                kwargs["lock"] = False
            kwargs["inAngle" if side == "in" else "outAngle"] = angle
            if curve not in weighted:
                weighted[curve] = cmds.getAttr(f"{curve}.weightedTangents")
            if weighted[curve]:
                kwargs["inWeight" if side == "in" else "outWeight"] = weight
            return kwargs

        what = f"{side} handle {'broken' if broken else 'dragged'}"
        self._edit_key_tangents(targets, resolve, what)

    def _edit_key_tangents(self, targets: list, kwargs, what: str) -> None:
        """One ``keyTangent`` edit per curve over the selected times, undoable.

        *kwargs* is the edit: the dict every time takes, or a resolver
        ``(obj, attr, curve, time) -> dict`` when each key gets its own -- a
        handle drag that carried a selection writes a different angle per
        key.  Times that resolve to the same flags still go out as ONE
        command, so the dict case costs exactly what it always did, and a
        key the resolver has nothing for is skipped.

        The rebuild that follows retires every key dot, so the selection is
        put back by object/attribute/time afterwards -- the user is looking
        at the handles they just changed, and they must stay selected to
        show them.
        """
        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import (
            curves_for_attr,
        )

        widget = self._get_sequencer_widget()
        if widget is None or not targets:
            return
        resolve = kwargs if callable(kwargs) else lambda *_a: kwargs
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        n = 0
        was_syncing = self._syncing
        self._syncing = True  # own cmds edits must not arm the keyframe debounce
        try:
            with CoreUtils.undo_chunk("Key tangents"):
                for obj, attr, times, _sid in targets:
                    for crv in curves_for_attr(obj, attr):
                        curve = str(crv)
                        batched = {}
                        for t in times:
                            flags = resolve(obj, attr, curve, t)
                            if flags:
                                batched.setdefault(tuple(flags.items()), []).append(t)
                        for flags, batch in batched.items():
                            tt = tuple((t, t) for t in batch)
                            try:
                                cmds.keyTangent(
                                    curve, edit=True, time=tt, **dict(flags)
                                )
                            except RuntimeError:
                                continue  # e.g. a weight on an unweighted curve
                            n += len(batch)
        finally:
            self._syncing = was_syncing
        self._sub_row_cache.clear()
        self._sync_to_widget(shot_id=shot_id)
        widget.select_keys(
            [
                {"data": {"obj": obj, "attr_name": attr}, "times": list(times)}
                for obj, attr, times, _sid in targets
            ]
        )
        self._set_footer(f"Tangents {what} on {n} key{'s' if n != 1 else ''}")

    # -- key-selection edits (tentacle's Animation panel, per selection) -----

    #: The key edits offered under the key menu's Edit row, as
    #: ``(label, method name)``.  Declared rather than inlined so the two
    #: forks can be read side by side: these five are spelled identically in
    #: both -- a test on each side pins the LIST, so a row added to one
    #: fork and not the other fails on the side that drifted -- unlike
    #: the tangent rows above them.
    _KEY_EDITS = (
        ("Simplify", "_simplify_selected_keys"),
        ("Remove Intermediate Keys", "_thin_selected_keys"),
        ("Snap Fractional Keys", "_snap_selected_keys"),
        ("Invert Keys", "_invert_selected_keys"),
        ("Align Keys", "_align_selected_keys"),
    )

    def _add_key_edit_actions(self, menu, targets, suffix) -> None:
        """Append the stash and Edit rows to the key menu.

        Everything here is scoped to the keys actually SELECTED -- the
        objects they belong to and the span they cover -- which is what
        makes them safe to offer at all: the same verbs applied to a whole
        shot silently reached every member's every attribute.

        Copy and Paste are NOT rows: they are the panel's ``Ctrl+C`` /
        ``Ctrl+V`` (see ``_copy_keys_shortcut``), because they are the one
        pair here that every editor already binds a key for.
        """
        menu.addSeparator()
        act_store = menu.addAction(f"Store Keys{suffix}")
        act_store.triggered.connect(lambda: self._stash_key_targets(targets))

        edit = QtWidgets.QMenu("Edit", menu)
        menu.addMenu(edit)
        for label, method in self._KEY_EDITS:
            act = edit.addAction(label)
            act.triggered.connect(
                lambda _checked=False, m=method: getattr(self, m)(targets)
            )

    @staticmethod
    def _target_objects(targets: list) -> list:
        """*targets*' objects, de-duplicated, in the order they appear."""
        return list(dict.fromkeys(obj for obj, _a, _t, _s in targets))

    @staticmethod
    def _target_attributes(targets: list) -> list:
        """*targets*' attribute names, de-duplicated, in the order they appear.

        The sequencer's key selection is always attribute-level
        (:meth:`_key_targets` drops object rows), so this is the scope a
        Channel Box highlight would express -- said outright.
        """
        return list(dict.fromkeys(attr for _o, attr, _t, _s in targets))

    @staticmethod
    def _target_curves(targets: list) -> list:
        """The anim curves behind *targets*' (object, attribute) pairs.

        The attribute-level scope: an edit handed these reaches only the
        highlighted channels, where one handed :meth:`_target_objects`
        reaches every curve those objects carry.
        """
        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import (
            curves_for_attr,
        )

        curves = []
        for obj, attr, _times, _sid in targets:
            curves.extend(str(c) for c in curves_for_attr(obj, attr))
        return list(dict.fromkeys(curves))

    @staticmethod
    def _target_span(targets: list) -> tuple:
        """The frame range *targets* covers, end to end."""
        times = [t for _o, _a, ts, _s in targets for t in ts]
        return (min(times), max(times))

    def _key_scene_edit(self, label: str, fn, shot_id=None):
        """Run *fn* as ONE undoable scene edit, reconcile, rebuild.

        The bracket every key edit needs and none of them should re-state:
        the store's ``scene_edit`` (a named undo chunk plus a boundary
        restore point), then ``reconcile_system_edits`` because an edit that
        adds, moves or removes keys may have touched a sample the shot
        system authored.  ``_syncing`` is held throughout so our own writes
        do not re-arm the keyframe debounce and rebuild underneath us.

        Returns whatever *fn* returned, or ``None`` with no sequencer.
        """
        if self.sequencer is None:
            return None
        was_syncing = self._syncing
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit(label):
                result = fn()
                self.sequencer.reconcile_system_edits()
        finally:
            self._syncing = was_syncing
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget(shot_id=shot_id)
        return result

    def _key_selection_edit(self, targets, label: str, fn):
        """Run ``fn(objects, span)`` over a key selection; ``(ran, result)``.

        Asserts the Graph Editor selection first
        (:meth:`_select_target_keys`): several of these read it rather than
        taking a range.

        Two return values because these engine calls disagree about what to
        report -- a count, a bool, nothing at all -- so "it ran" cannot be
        read off the result.
        """
        if not targets or self.sequencer is None:
            return False, None
        self._select_target_keys(targets)
        objects = self._target_objects(targets)
        span = self._target_span(targets)
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        result = self._key_scene_edit(label, lambda: fn(objects, span), shot_id=shot_id)
        return True, result

    def _simplify_selected_keys(self, targets) -> None:
        """Drop the selected keys that carry neither a value nor a shape.

        What ``optimize_keys`` does, minus the part a context-menu edit must
        not do, aimed at a key SELECTION instead of a whole scene.  Both of
        its middle passes run, in its order, over the selected attributes'
        curves and, within them, the selected keys:

        1. the FLAT pass (``get_redundant_flat_keys``) -- the interior keys
           of a run that all hold one value.  This is the one that matters on
           real footage: a hold is usually spelled with ``step`` out-tangents,
           and a stepped curve is exactly what the reducer below will not
           touch.  Measured on a production assembly, the reducer alone left
           every redundant hold key standing on 8 of 9 channels.
        2. the SHAPE pass (``simplify_curve`` / ``filterCurve keyReducer``)
           -- keys whose absence moves the curve less than the tolerance.

        Neither reaches the attributes beside the one the user highlighted,
        and each leaves the curve outside the selection byte-identical,
        tangents included.  ``optimize_keys`` itself is deliberately NOT the
        call even though it names these passes: it may delete whole static
        curves, which an edit inside :meth:`_key_scene_edit`'s undo chunk
        cannot afford.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        curves = self._target_curves(targets)

        def _run(_objects, span):
            flat = AnimUtils.get_redundant_flat_keys(
                curves, remove=True, selected_only=True, time_range=span
            )
            shaped = AnimUtils.simplify_curve(
                curves, selected_only=True, time_range=span
            )
            return sum(len(times) for _c, times in flat), len(shaped)

        ran, counts = self._key_selection_edit(targets, "simplifykeys", _run)
        if not ran:
            return
        n_flat, n_shaped = counts or (0, 0)
        if n_flat or n_shaped:
            parts = []
            if n_flat:
                parts.append(f"{n_flat} flat key{'s' if n_flat != 1 else ''}")
            if n_shaped:
                parts.append(f"{n_shaped} curve{'s' if n_shaped != 1 else ''} reduced")
            self._set_footer("Simplified: " + ", ".join(parts))
        else:
            self._set_footer(
                "Nothing to simplify — every selected key carries value or shape"
            )

    def _thin_selected_keys(self, targets) -> None:
        """Keep only the first and last key of each selected attribute.

        The attributes are passed outright rather than left to the Channel
        Box highlight this panel mirrors into: the strip must narrow to the
        sub-rows the user selected even if the highlight never landed.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        attrs = self._target_attributes(targets)
        ran, n = self._key_selection_edit(
            targets,
            "thinkeys",
            lambda objects, span: AnimUtils.remove_intermediate_keys(
                objects, time_range=span, attributes=attrs
            ),
        )
        if ran:
            self._set_footer(
                f"Removed {n or 0} intermediate key{'s' if n != 1 else ''}"
            )

    def _snap_selected_keys(self, targets) -> None:
        """Pull the selected keys off fractional frames onto whole ones."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        ran, n = self._key_selection_edit(
            targets,
            "snapkeys",
            lambda objects, span: AnimUtils.snap_keys_to_frames(
                objects, selected_only=True, time_range=span
            ),
        )
        if ran:
            self._set_footer(
                f"Snapped {n or 0} key{'s' if n != 1 else ''} to whole frames"
            )

    def _invert_selected_keys(self, targets) -> None:
        """Mirror the selected keys in time, in place."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        ran, _ = self._key_selection_edit(
            targets,
            "invertkeys",
            # time=None mirrors within the selection's own range rather than
            # placing a reversed COPY somewhere -- the sequencer's keys are
            # already where the animator put them.
            lambda objects, _span: AnimUtils.invert_keys(objects),
        )
        if ran:
            n = sum(len(t) for _o, _a, t, _s in targets)
            self._set_footer(f"Inverted {n} key{'s' if n != 1 else ''}")

    def _align_selected_keys(self, targets) -> None:
        """Line the selected keys up on the earliest one's frame."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        ran, ok = self._key_selection_edit(
            targets,
            "alignkeys",
            lambda objects, _span: AnimUtils.align_selected_keyframes(objects),
        )
        if ran:
            self._set_footer("Aligned the selected keys" if ok else "Nothing to align")

    def _selected_key_targets(self) -> list:
        """The key menu's ``targets`` for whatever is selected right now.

        What a SHORTCUT has to resolve for itself: a menu is handed its
        groups, a key press is not.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            return []
        return self._key_targets(widget, widget.selected_keys())

    def _copy_keys_shortcut(self) -> None:
        """Ctrl+C over the sequencer: copy the selected keys."""
        self._copy_selected_keys(self._selected_key_targets())

    def _paste_keys_shortcut(self) -> None:
        """Ctrl+V over the sequencer: paste them at the playhead."""
        self._paste_selected_keys(self._selected_key_targets())

    def _copy_selected_keys(self, targets) -> None:
        """Copy the selected keys (times, values, tangents) for a later paste."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        if not targets:
            return
        self._select_target_keys(targets)
        data = AnimUtils.copy_keys(
            self._target_objects(targets), mode="selected", tangent_detail=True
        )
        if not data:
            self._set_footer("Nothing to copy")
            return
        self._copied_keys = data
        n = sum(
            len(v) if isinstance(v, list) else 1
            for attrs in data.values()
            for v in attrs.values()
        )
        self._set_footer(f"Copied {n} key{'s' if n != 1 else ''}")

    def _paste_selected_keys(self, targets) -> None:
        """Paste the copied keys onto the selection at the current frame."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        if not self._copied_keys:
            self._set_footer("Nothing copied yet \u2014 use Copy Keys first")
            return
        objects = self._target_objects(targets)
        if not objects:
            self._set_footer("Select some keys to paste onto")
            return
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        # target_time is passed, not defaulted: mayatk's default IS the current
        # frame but blendertk's is the buffer's own frames, and the two panels
        # have to mean the same thing.
        now = self._current_time()
        n = self._key_scene_edit(
            "pastekeys",
            lambda: AnimUtils.paste_keys(
                objects, copied_data=self._copied_keys, target_time=now
            ),
            shot_id=shot_id,
        )
        self._set_footer(
            f"Pasted onto {n or 0} object{'s' if n != 1 else ''}"
            if n
            else "Nothing pasted \u2014 no matching attributes on the selection"
        )

    def _stash_key_targets(self, targets: list) -> None:
        """Park the selected keys in the key stash.

        The key-selection twin of :meth:`_stash_clip_keys`: same store, same
        loop (:meth:`_run_stash`), scoped to the selected times of each
        attribute instead of a whole clip's span.
        """
        if cmds is None or not targets:
            return
        jobs = []
        for obj, attr, times, shot_id in targets:
            full = self._resolve_full_name(obj)
            if not cmds.objExists(full):
                continue
            jobs.append(
                (
                    full,
                    [attr],
                    min(times),
                    max(times),
                    None if shot_id == -1 else shot_id,
                )
            )
        self._run_stash(jobs)
