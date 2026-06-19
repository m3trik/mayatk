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

from unitytk import CopyToAssetsDeliverer

from mayatk.env_utils.handoff_export import MayaExportMixin


_PKG_DIR = Path(__file__).resolve().parent


# Module-level helper so the slots can populate the combo without a live engine. Single-sources the
# delivery modes from the shared deliverer (the seam for a future ``executeMethod`` mode).
def list_delivery_modes() -> List[Tuple[str, str]]:
    """``[(mode_stem, ""), ...]`` for the panel's delivery combo."""
    return list(CopyToAssetsDeliverer.DELIVERY_MODES)


class UnityBridge(MayaExportMixin, ptk.HandoffBridge):
    """Export the Maya selection and copy it into a Unity project's ``Assets/``.

    Set :attr:`project_path` to the target Unity project (the folder containing ``Assets/``) before
    calling :meth:`send` -- the panel wires this from its 'Unity Project' field. Delivery is the
    :class:`unitytk.CopyToAssetsDeliverer` Strategy.
    """

    payload_prefix = "mtk_to_unity"

    def __init__(self, project_path: Optional[str] = None):
        super().__init__()
        self.project_path = project_path
        self.deliverer = CopyToAssetsDeliverer()

    # ------------------------------------------------------------------ bindings
    def list_template_modes(self):
        return list_delivery_modes()

    def params_defaults(self):
        from mayatk.env_utils.unity_bridge import parameters as _params

        return _params.defaults()

    def _produce(self, objects, request) -> ptk.Payload:
        """Export the FBX (via the mixin) and stamp the default asset name for the deliverer."""
        payload = super()._produce(objects, request)
        payload.extras["default_asset_name"] = self._default_asset_name(objects)
        return payload

    def _default_asset_name(self, objects) -> str:
        """Asset stem from the first selected transform."""
        from mayatk.core_utils._core_utils import leaf_name

        return leaf_name(objects[0])


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
