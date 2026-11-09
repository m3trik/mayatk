# !/usr/bin/python
# coding=utf-8
"""Unity bridge engine -- the Maya half of the Unity Scripts app.

The Unity Scripts app (extapps' ``unity_scripts``) is the one Unity panel; inside Maya,
tentacle opens it with this class as its engine (``ui.slots.engine = mtk.UnityBridge``), and
the app's Current Scene source sends through it. This half resolves the app's Scope and
exports it with the Scene Exporter, as a fresh Scene Exporter panel would (its tasks, checks,
records and finishing pass; :attr:`UnityBridge.SCENE_EXPORT_OVERRIDES` aside), so what Unity
gets is the Scene Exporter's own deliverable, then hands the file to the delivery the app
attached (``deliverer=``: the app's, copying into the project's ``Assets/`` and running the
chosen script). The delivery, the project and the script are the app's; nothing here names
the Unity side.

Composition:

* :class:`mayatk.env_utils.handoff_export.MayaExportMixin` -- the Maya scope resolution
  (shared with :class:`mayatk.env_utils.blender_bridge.BlenderBridge`).
* :class:`mayatk.env_utils.scene_exporter.SceneExporter` -- the export, through
  ``ptk.SceneExportHandoff``, which runs it as a fresh panel would.
* :class:`pythontk.HandoffBridge` -- the ``send()`` orchestration (``scope_objects``, the
  delivery's preflight before the export) over the attached :class:`pythontk.Deliverer`.

``import maya.cmds`` is deferred (via the mixin) so the engine surface resolves headlessly.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import pythontk as ptk

from mayatk.env_utils.handoff_export import MayaExportMixin


class UnityBridge(ptk.SceneExportHandoff, MayaExportMixin, ptk.HandoffBridge):
    """Export the Maya scope for a Unity project, through the delivery the caller attaches.

    The Unity Scripts app builds it with its own deliverer (``UnityBridge(deliverer=...)``)
    and sets :attr:`project_path` (the folder containing ``Assets/``) from its 'Unity
    Project' field before calling :meth:`send`; the produced payload -- the FBX, and its
    ``default_asset_name`` -- is what that deliverer copies in. A send with no delivery
    attached refuses before it exports.
    """

    payload_prefix = "mtk_to_unity"
    #: The run consumes its staging folder: the deliverer copies the FBX into the
    #: project inside the send, after which nothing reads it again.
    scoped_scratch_modes = ("",)
    #: The Scene Exporter rows this hand-off changes from a fresh panel's: the
    #: open scene's workspace stays the person's.
    SCENE_EXPORT_OVERRIDES = {"set_workspace": False}
    #: The exporter the send runs, imported on use.
    SCENE_EXPORTER = "mayatk.env_utils.scene_exporter._scene_exporter:SceneExporter"
    # What the old hand-off export needed spelled out -- the ``data_export``
    # carrier, the curve-proxy staging, one exact take per shot, animation
    # unless asked otherwise -- the Scene Exporter does as its own defaults, so
    # this class declares none of it.

    def __init__(
        self,
        project_path: Optional[str] = None,
        deliverer: Optional[ptk.Deliverer] = None,
        decide_check_failure: Optional[Callable[[str, List[str], List[str]], str]] = None,
    ):
        """
        Parameters:
            project_path: The Unity project (the folder holding ``Assets/``).
            deliverer: The delivery the app attaches.
            decide_check_failure: What to do when a Scene Exporter check fails
                mid-export (``ptk.SceneExporterBase.decide_check_failure``'s
                seam): a panel's dialog. ``None``: the exporter's own, which
                asks the console and stops when nobody answers.
        """
        super().__init__()
        self.project_path = project_path
        self.deliverer = deliverer
        self.decide_check_failure = decide_check_failure

    # ------------------------------------------------------------------ bindings
    @classmethod
    def params_registry(cls):
        """The export rows the Unity Scripts app shows for its Current Scene
        source (``parameters.Parameters``, a ``uitk.bridge.ParamRegistry``), and
        what :meth:`params_defaults` reads. Imported on use, as the defaults always
        were: the registry is uitk's, and the engine surface imports without uitk
        (a headless Blender ships no Qt binding)."""
        from mayatk.env_utils.unity_bridge import parameters as _params

        return _params.Parameters

    def params_defaults(self):
        return self.params_registry().defaults()

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """The scope as the Scene Exporter writes it (``ptk.SceneExportHandoff``)."""
        return self.produce_scene_export(objects, request)

    def _default_asset_name(self, objects) -> str:
        """Asset stem from the first selected transform."""
        from mayatk.core_utils._core_utils import CoreUtils

        return CoreUtils.leaf_name(objects[0])
