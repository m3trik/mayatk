# !/usr/bin/python
# coding=utf-8
"""Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.

The DCC-agnostic base lives upstream in :mod:`uitk.bridge.slots`
(re-exported through :mod:`uitk.bridge`). This thin subclass injects
what every Maya bridge shares: the Scope resolution (``BridgeHost``'s).

A blank Output Dir means "the bridge's own rule" (uitk's own
``default_output_dir``, ``""``, which this keeps): hand-off intermediates go to a ``TempArtifacts`` scratch per
send, and what is kept goes to a folder of the SCENE's project (a bake's
maps to ``sourceimages/baked``, a mesh Painter re-reads to ``cache/substance``,
a saved Toolbag scene to ``data/marmoset``) -- never the scene folder, where
the hand-off silted up the project. blendertk's base means the same.

The Rizom and Blender bridge slots subclass this instead of inheriting
from ``BridgeSlotsBase`` directly, so the rule lives in one place. A bridge
that has moved to tentacle (Unity, Marmoset, Substance) sits on tentacle's
``HostBridge`` instead, over the same ``BridgeHost`` API; the Bake Source row
(``BakeSourceSet``) lives there, once for both hosts.
"""

from __future__ import annotations

from uitk.bridge import BridgeSlotsBase


class MayaBridgeSlotsBase(BridgeSlotsBase):
    """Adds the Maya-side Scope resolution to :class:`BridgeSlotsBase`."""

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
