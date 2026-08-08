# !/usr/bin/python
# coding=utf-8
"""Push the Maya selection to a live browser / WebXR preview.

The lightest of the hand-off bridges: there is no target application to
discover or launch, because the target is a browser tab the user already has
open. :class:`pythontk.PreviewDeliverer` converts the exported FBX to GLB and
publishes it to a loopback :class:`pythontk.PreviewServer`; a page already open
-- including one open inside a PC-tethered headset -- picks the new version up
on its next poll.

Nothing here is Maya-specific except the binding itself, which is the point:
:class:`pythontk.PreviewBridge` owns the export defaults and the public
``push`` / ``url`` / ``stop`` surface, :class:`MayaExportMixin` supplies the
selection read and FBX export every Maya-originating bridge shares, and
:class:`~mayatk.env_utils.scene_state.SceneState` owns the sidecar readers --
shared with the Scene Exporter's GLB task, so the preview and the production
deliverable describe the scene identically. Counterpart of blendertk's
``WebXrPreview``.

Example:
    >>> preview = mtk.WebXrPreview()
    >>> preview.push()              # opens a tab on the first call
    >>> preview.push()              # the open tab swaps to the new version
"""
from __future__ import annotations

from typing import Optional

import pythontk as ptk

from mayatk.env_utils.handoff_export import MayaExportMixin
from mayatk.env_utils.scene_state import SceneState


class WebXrPreview(MayaExportMixin, ptk.PreviewBridge):
    """Live browser / WebXR preview of the Maya selection.

    One :class:`pythontk.PreviewDeliverer` is shared by every instance, so the
    server -- and therefore the port and the tab pointed at it -- survives
    across pushes and across panel reopens for the life of the Maya session.
    """

    payload_prefix = "maya_webxr_preview"
    deliverer = ptk.PreviewDeliverer(title="Maya")

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the FBX, then attach the scene sidecar the FBX can't carry.

        Same shape as the Marmoset bridge's producer: the skeleton's FBX
        payload plus a sidecar riding on ``Payload.extras``. The sections come
        from :class:`SceneState` (the shared reader column) and the versioned
        envelope they travel in is built by
        :meth:`pythontk.MeshConvert.build_scene_sidecar` via the bridge's
        ``_attach_sidecar``, so neither can fork against blendertk's twin.
        """
        payload = super()._produce(objects, request)
        if payload is None or not request.params.get("SCENE_SIDECAR", True):
            return payload

        sections = SceneState.read(
            objects, include_textures=request.params.get("EMBED_TEXTURES", True)
        )
        return self._attach_sidecar(payload, sections, source=SceneState.source())
