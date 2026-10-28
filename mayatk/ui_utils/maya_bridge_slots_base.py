# !/usr/bin/python
# coding=utf-8
"""Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.

The DCC-agnostic base lives upstream in :mod:`uitk.bridge.slots`
(re-exported through :mod:`uitk.bridge`). This thin subclass injects
what every Maya bridge shares: the Scope resolution (``BridgeHost``'s) and
the Bake Source row (its three actions and live tooltip, over
``BakeSourceSet``).

A blank Output Dir means "the bridge's own rule" (uitk's own
``default_output_dir``, ``""``, which this keeps): hand-off intermediates go to a ``TempArtifacts`` scratch per
send, and what is kept goes to a folder of the SCENE's project (a bake's
maps to ``sourceimages/baked``, a mesh Painter re-reads to ``cache/substance``,
a saved Toolbag scene to ``data/marmoset``) -- never the scene folder, where
the hand-off silted up the project. blendertk's base means the same.

The Marmoset, Substance, Rizom and Blender bridge slots subclass this
instead of inheriting from ``BridgeSlotsBase`` directly, so the rule lives in
one place. A bridge that has moved to tentacle (Unity) sits on tentacle's
``HostBridge`` instead, over the same ``BridgeHost`` / ``BakeSourceSet`` API.
"""

from __future__ import annotations

from uitk.bridge import BridgeSlotsBase
from pythontk import TooltipFormat


class MayaBridgeSlotsBase(BridgeSlotsBase):
    """Adds the Maya-side Scope resolution and the Bake Source row to
    :class:`BridgeSlotsBase`."""

    # ------------------------------------------------------- Bake Source row
    #: Row every bridge that consumes :class:`mayatk.mat_utils.BakeSourceSet`
    #: declares (marmoset, substance). Named here so the row's actions and live
    #: tooltip are written once instead of once per bridge -- the set itself is
    #: already the one cross-tool definition, and its row should be too. Bridges
    #: without the row (Rizom, Blender) fall through untouched.
    BAKE_SOURCE_KEY = "BAKE_SOURCE_SET"

    #: What defining the set changes for THIS bridge's sends, appended to the
    #: Set From Selection log line: the Substance send ships the set with every
    #: send, the Marmoset one only with a bake (and it retires the suffix
    #: fallback), so a shared sentence would promise one bridge's behaviour of
    #: the other. Mirror of blendertk's.
    BAKE_SOURCE_DEFINED_NOTE = "Sends now ship it as the companion bake source."
    #: What clearing the set falls back to for this bridge ("" = nothing to say).
    BAKE_SOURCE_CLEARED_NOTE = ""

    @staticmethod
    def _bake_source_set():
        """The scene's :class:`mayatk.mat_utils.bake_sets.BakeSourceSet`, imported
        on use (mirror of blendertk's hook)."""
        from mayatk.mat_utils.bake_sets import BakeSourceSet

        return BakeSourceSet

    def live_param_tooltip_blocks(self):
        """Make the Bake Source row report the scene's CURRENT members.

        The set lives in the scene, not the panel, so it changes under an open
        panel -- a new scene, a redefine from the other bridge, a delete in the
        Outliner. A build-time tooltip would describe the set the panel opened
        on, which is exactly the case the user is trying to check.

        Registered as a BLOCK, not a whole tooltip: the row's three buttons
        each carry their own description, and the member list has to reach the
        one the user is actually hovering when they capture a selection -- not
        just the label off to its left.

        Extends the base's mapping rather than replacing it, so a bridge that
        makes one of its OWN rows live keeps this one (and vice versa) -- the
        hook is a registry, and a subclass that has to remember to merge is a
        subclass that will forget.
        """
        tips = dict(super().live_param_tooltip_blocks() or {})
        params = getattr(self.params_module, "PARAMS", {}) or {}
        if self.BAKE_SOURCE_KEY in params:
            tips[self.BAKE_SOURCE_KEY] = self._bake_source_tooltip
        return tips

    def _bake_source_tooltip(self) -> str:
        """The Bake Source set's live member list (appended to each hover target)."""
        try:
            names = self._bake_source_set().member_names()
        except Exception:  # noqa: BLE001 -- a tooltip must never raise into Qt
            return ""
        return TooltipFormat.stored_items(
            names,
            noun="object(s) in this scene's set",
            # What an EMPTY set means differs per bridge (substance ships
            # nothing; marmoset falls back to the name suffixes), and each
            # registry's own tooltip already says which -- this block reports
            # the state, not the consequence.
            empty_text="No bake source defined in this scene.",
        )

    def set_bake_source_from_selection(self) -> None:
        """Store the current selection as the scene's bake source.

        Defining the set IS the opt-in: the sends that read it (see
        :attr:`BAKE_SOURCE_DEFINED_NOTE`) export it as the companion
        ``<name>_source.fbx``. There is no second checkbox to tick -- the
        pairing this replaced could be silently half-on (a set defined, the
        box left clear), which reads as the tool ignoring you.
        """
        bake_set = self._bake_source_set()
        members = bake_set.define()
        # Rows that key off the set (the Marmoset suffix fallback) re-grey now,
        # not on the next panel show.
        self._refresh_param_enablement()
        if not members:
            self.bridge.logger.warning(
                "Nothing selected; the bake-source set was cleared."
            )
            return
        self.bridge.logger.info(
            f"Bake Source set: {len(members)} object(s) -> {bake_set.SET_NAME}. "
            f"{self.BAKE_SOURCE_DEFINED_NOTE}"
        )

    def select_bake_source(self) -> None:
        """Select the bake-source set's members (hidden ones included)."""
        bake_set = self._bake_source_set()
        members = bake_set.members()
        # Also resyncs the suffix rows if the set was deleted outside the panel.
        self._refresh_param_enablement()
        if not members:
            self.bridge.logger.warning("This scene has no bake-source set.")
            return
        selected = bake_set.select()
        self.bridge.logger.info(f"Selected {len(selected)} bake-source object(s).")

    def clear_bake_source(self) -> None:
        """Delete the bake-source set node; its members are left alone."""
        bake_set = self._bake_source_set()
        if not bake_set.exists():
            self.bridge.logger.warning("This scene has no bake-source set.")
            return
        bake_set.clear()
        self._refresh_param_enablement()
        self.bridge.logger.info(
            " ".join(
                s
                for s in ("Bake-source set cleared.", self.BAKE_SOURCE_CLEARED_NOTE)
                if s
            )
        )

    # ------------------------------------------------------------------ scope
    def resolve_scope_objects(self, scope: str):
        """Objects to export for the chosen ``SCOPE`` param.

        :meth:`mayatk.BridgeHost.resolve_scope`, with this panel's bridge as the
        whole-scene hook -- the one resolver every Maya bridge shares, and the
        one tentacle's bridge panels call (``mtk.BridgeHost``). ``getattr``, not
        ``self.bridge``: a panel that has not built its bridge yet has no
        ``.bridge`` at all, and the documented fallback (the scene's meshes) is
        for exactly that.
        """
        from mayatk.env_utils.handoff_export import BridgeHost

        return BridgeHost.resolve_scope(scope, getattr(self, "bridge", None))
