# !/usr/bin/python
# coding=utf-8
"""Unity bridge engine -- export the Maya selection into a Unity project's Assets/.

The Maya->Unity hand-off, built on the same :class:`pythontk.HandoffBridge` skeleton as the
Blender bridge but with a **copy-to-assets** deliverer instead of a script-launch one: Unity ingests
any file dropped into ``Assets/`` through its own asset pipeline on focus, so the session-safe,
license-free hand-off is simply *copy the FBX into the project* (and optionally launch the editor).
Unlike the Maya<->Blender bridges there is no fresh-instance-launch dance -- dropping a file into
``Assets/`` never disturbs a running editor or unsaved work.

Composition:

* :class:`mayatk.env_utils.handoff_export.MayaExportMixin` -- the Maya selection + FBX export
  (shared with :class:`mayatk.env_utils.blender_bridge.BlenderBridge`).
* :class:`unitytk.CopyToAssetsDeliverer` -- the copy-into-``Assets/`` + optional editor launch
  Strategy (shared with the blendertk Unity bridge; lives next to ``unitytk.UnityLauncher``).
* :class:`pythontk.HandoffBridge` -- the ``send()`` orchestration.

``import maya.cmds`` is deferred (via the mixin) so the engine surface resolves headlessly; the
``unitytk`` import is module-level (pure-Python, no DCC/Qt).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pythontk as ptk

from mayatk.env_utils.handoff_export import MayaExportMixin


_PKG_DIR = Path(__file__).resolve().parent


class UnityBridge(MayaExportMixin, ptk.HandoffBridge):
    """Export the Maya selection and copy it into a Unity project's ``Assets/``.

    Set :attr:`project_path` to the target Unity project (the folder containing ``Assets/``) before
    calling :meth:`send` -- the panel wires this from its 'Unity Project' field. Delivery is the
    :class:`unitytk.CopyToAssetsDeliverer` Strategy.
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
    #: ...and this panel offers no animation knob, so the hand-off exports the
    #: animation and splits the declared shots unless a request says otherwise.
    #: Without it no take was split (``resample_takes`` never ran) and Unity
    #: built one ``Take 001`` clip for a scene that declares its shots.
    include_animation = True

    @staticmethod
    def _deliverer_cls():
        """unitytk's deliverer, imported on use rather than at module scope.

        ``unitytk`` is an optional distribution (``pip install mayatk[unity]``).
        Deferring the import keeps a missing one from raising while the module
        is merely being imported -- which would fire before any UI exists to say
        so -- and lets the panel offer to install it on demand instead
        (:meth:`uitk.bridge.BridgeSlotsBase.ensure_optional_package`).
        """
        from unitytk import CopyToAssetsDeliverer

        return CopyToAssetsDeliverer

    def __init__(self, project_path: Optional[str] = None):
        super().__init__()
        self.project_path = project_path
        self.deliverer = self._deliverer_cls()()

    # ------------------------------------------------------------------ bindings
    def list_template_modes(self):
        return UnityBridge.list_delivery_modes()

    @classmethod
    def params_registry(cls):
        """The panel's parameter registry (``parameters.Parameters``, a
        ``uitk.bridge.ParamRegistry``): what tentacle's Unity Bridge builds its
        rows from, and :meth:`params_defaults` reads. Imported on use, as the
        defaults always were: the registry is uitk's, and the engine surface
        imports without uitk (a headless Blender ships no Qt binding)."""
        from mayatk.env_utils.unity_bridge import parameters as _params

        return _params.Parameters

    def params_defaults(self):
        return self.params_registry().defaults()

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

    @classmethod
    def list_delivery_modes(cls) -> List[Tuple[str, str]]:
        """``[(mode_stem, ""), ...]`` for the panel's delivery combo."""
        return list(cls._deliverer_cls().DELIVERY_MODES)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = UnityBridge(project_path=None)
    try:
        import maya.cmds as cmds

        sel = cmds.ls(selection=True) or []
    except ModuleNotFoundError:
        sel = []
    # bridge.project_path = r"C:/path/to/UnityProject"
    # bridge.send(sel, template="copy_to_assets", mode="send_to")
