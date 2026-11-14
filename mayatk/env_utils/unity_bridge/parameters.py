# !/usr/bin/python
# coding=utf-8
"""The Maya export rows of the Unity hand-off.

The rows the Unity Scripts app (extapps' ``unity_scripts``) shows for its
Current Scene source when Maya opened it with this engine: the Scope. The
export itself is the Scene Exporter's, as a fresh Scene Exporter panel runs it
(``UnityBridge._produce``), so its own rows and FBX options are not restated
here. The delivery's rows -- the Assets subfolder, the asset name -- and the
scripts' are the app's. Never substituted into a template.

This copy is the SSoT: blendertk's ``env_utils.unity_bridge.parameters`` is the
same text, host names in prose aside, and ``extapps/tests/test_vendor_sync.py``
fails the moment the two differ. Edit here, then mirror.
"""

from __future__ import annotations

from uitk.bridge import AttributeSpec, ParamRegistry, Parameters as _BridgeParams


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    "SCOPE": _BridgeParams.scope_spec(),
}


class Parameters(ParamRegistry):
    """Parameters — module namespace.

    Declared as data: :class:`uitk.bridge.ParamRegistry` supplies
    ``referenced_keys`` / ``defaults`` / ``render_context`` (Python literals)
    over :data:`PARAMS`, and the Unity Scripts app builds its Current Scene
    rows from it (``UnityBridge.params_registry()``).
    """

    PARAMS = PARAMS
