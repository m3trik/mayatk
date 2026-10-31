# !/usr/bin/python
# coding=utf-8
"""Unity bridge engine -- the Maya half of the Unity Workflow app.

The Unity Workflow app (extapps' ``unity_workflow``) is the one Unity panel; inside Maya,
tentacle opens it with this class as its engine (``ui.slots.engine = mtk.UnityBridge``), and
the app's Current Scene source sends through it. This half resolves the app's Scope, exports
it -- the FBX with the scene records (the ``data_export`` carrier) and the curve-proxy
transport the Unity importers rebind -- finishes it as the Scene Exporter finishes its FBX,
and hands the file to the delivery the app attached (``deliverer=``: unitytk's copy into
``Assets/`` and optional Editor launch). The delivery, the project and the launch are the
app's; nothing here names the Unity side.

Composition:

* :class:`mayatk.env_utils.handoff_export.MayaExportMixin` -- the Maya selection + FBX export
  (shared with :class:`mayatk.env_utils.blender_bridge.BlenderBridge`).
* :class:`pythontk.HandoffBridge` -- the ``send()`` orchestration (``scope_objects``, the
  delivery's preflight before the export) over the attached :class:`pythontk.Deliverer`.

``import maya.cmds`` is deferred (via the mixin) so the engine surface resolves headlessly.
"""

from __future__ import annotations

from typing import Optional

import pythontk as ptk

from mayatk.env_utils.handoff_export import BridgeHost, MayaExportMixin


class UnityBridge(MayaExportMixin, ptk.HandoffBridge):
    """Export the Maya scope for a Unity project, through the delivery the caller attaches.

    The Unity Workflow app builds it with its own deliverer (``UnityBridge(deliverer=...)``)
    and sets :attr:`project_path` (the folder containing ``Assets/``) from its 'Unity
    Project' field before calling :meth:`send`; the produced payload -- the FBX, and its
    ``default_asset_name`` -- is what that deliverer copies in. A send with no delivery
    attached refuses before it exports.
    """

    payload_prefix = "mtk_to_unity"
    #: Unity IS the consumer of the in-band metadata, so the carrier ships.
    #:
    #: Unlike a DCC hand-off, this FBX is not an intermediate -- it lands in ``Assets/``
    #: as the deliverable, and unitytk's ``LightmapMetadataController`` binds Unity's
    #: native lightmap slots (``renderer.lightmapIndex`` / ``lightmapScaleOffset``) by
    #: reading ``lightmaps`` off ``data_export``. Without the carrier that
    #: controller finds nothing and the import is silently unlit -- on the platform the
    #: bake targets first.
    include_data_export = True
    #: unitytk's ``RenderEffectsImporter`` rebinds the ``<node>__<channel>``
    #: curve proxies, so this hand-off stages them exactly like the Scene
    #: Exporter's FBX.
    export_stagers = ("render_effects",)
    #: Unity builds a model's AnimationClips from the FBX's takes, so each must
    #: be an exact slice: the split resamples every curve, or a node keyed only
    #: outside a shot plays its rest pose through it (``FbxUtils.apply_takes``).
    resample_takes = True
    #: ...and the app offers no animation knob, so the hand-off exports the
    #: animation and splits the declared shots unless a request says otherwise.
    #: Without it no take was split (``resample_takes`` never ran) and Unity
    #: built one ``Take 001`` clip for a scene that declares its shots.
    include_animation = True

    def __init__(
        self,
        project_path: Optional[str] = None,
        deliverer: Optional[ptk.Deliverer] = None,
    ):
        super().__init__()
        self.project_path = project_path
        self.deliverer = deliverer

    # ------------------------------------------------------------------ bindings
    @classmethod
    def params_registry(cls):
        """The export rows the Unity Workflow app shows for its Current Scene
        source (``parameters.Parameters``, a ``uitk.bridge.ParamRegistry``), and
        what :meth:`params_defaults` reads. Imported on use, as the defaults always
        were: the registry is uitk's, and the engine surface imports without uitk
        (a headless Blender ships no Qt binding)."""
        from mayatk.env_utils.unity_bridge import parameters as _params

        return _params.Parameters

    def params_defaults(self):
        return self.params_registry().defaults()

    @staticmethod
    def install_package(spec: str) -> None:
        """Install the pip requirement *spec* where this Maya session imports it
        from (``BridgeHost.install_package``): the route the app takes for its
        optional unitytk when Maya opened it.

        Parameters:
            spec: A requirement, e.g. ``unitytk>=0.1.0``.
        """
        BridgeHost.install_package(spec)

    def _produce(self, objects, request) -> ptk.Payload:
        """Export the FBX (via the mixin), embed what it depends on, and stamp
        the default asset name for the deliverer.

        The FBX lands in ``Assets/`` as the deliverable, so it is finished as
        the Scene Exporter's is (:meth:`FbxUtils.finish_deliverable`, which
        never raises and resolves the scene's map folders inside its own
        guard): every file its records name and the audio its events play
        inside it, the probe's highlight copies, a mesh per pivot set. The
        deliverer embeds only the files a record names by file name, and an
        audio event names its track, which only this scene can resolve.
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        payload = super()._produce(objects, request)
        FbxUtils.finish_deliverable(payload.primary, logger=self.logger)
        payload.extras["default_asset_name"] = self._default_asset_name(objects)
        return payload

    def _default_asset_name(self, objects) -> str:
        """Asset stem from the first selected transform."""
        from mayatk.core_utils._core_utils import CoreUtils

        return CoreUtils.leaf_name(objects[0])
