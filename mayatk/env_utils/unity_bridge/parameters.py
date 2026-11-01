# !/usr/bin/python
# coding=utf-8
"""The Maya export rows of the Unity hand-off.

The rows the Unity Workflow app (extapps' ``unity_workflow``) shows for its Current
Scene source when Maya opened it with this engine: the Scope and the knobs that
drive the Maya-side FBX export (read by
:class:`mayatk.env_utils.handoff_export.MayaExportMixin`). The delivery's rows --
the Assets subfolder, the asset name, the Editor launch and version, Manage Unity
Scripts -- are the app's, and so is the delivery that reads them. Never
substituted into a template: the delivery copies the FBX into the project.

Mirrors :mod:`mayatk.env_utils.blender_bridge.parameters` in shape. This copy is
the SSoT: blendertk's ``env_utils.unity_bridge.parameters`` is the same text, host
names in prose aside, and ``extapps/test/test_vendor_sync.py`` fails the moment
the two differ. Edit here, then mirror.
"""

from __future__ import annotations

from uitk.bridge import AttributeSpec, ParamRegistry, Parameters as _BridgeParams


# Display order is iteration order over this dict.
PARAMS: "dict[str, AttributeSpec]" = {
    "SCOPE": _BridgeParams.scope_spec(),
    "INCLUDE_MATERIALS": AttributeSpec(
        key="INCLUDE_MATERIALS",
        label="Include Materials",
        kind="bool",
        default=True,
        section="Export",
        tooltip=(
            "Carry materials across. When off, the selection is exported with only\n"
            "the default shader (materials stripped Maya-side); geometry only."
        ),
    ),
    "EMBED_TEXTURES": AttributeSpec(
        key="EMBED_TEXTURES",
        label="Embed Textures",
        kind="bool",
        default=True,
        section="Export",
        tooltip=(
            "Embed the texture files inside the FBX. Unity binds an embedded map\n"
            "only once it is extracted beside the model (Inspector > Materials >\n"
            "Extract Textures): unitytk's import scripts extract the maps they wire\n"
            "and the files its channels name; Unity's stock import extracts nothing.\n"
            "Off relies on the textures already living in the project."
        ),
    ),
    "TRIANGULATE": AttributeSpec(
        key="TRIANGULATE",
        label="Triangulate",
        kind="bool",
        default=False,
        section="Export",
        tooltip="Triangulate meshes on export.",
    ),
}


class Parameters(ParamRegistry):
    """Parameters — module namespace.

    Declared as data: :class:`uitk.bridge.ParamRegistry` supplies
    ``referenced_keys`` / ``defaults`` / ``render_context`` (Python literals)
    over :data:`PARAMS`, and the Unity Workflow app builds its Current Scene
    rows from it (``UnityBridge.params_registry()``).
    """

    PARAMS = PARAMS
