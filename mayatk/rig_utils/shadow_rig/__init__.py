# !/usr/bin/python
# coding=utf-8
"""Shadow Rig tool — a projected-shadow rig for engine export.

``_shadow_rig.py`` is the engine (:class:`ShadowRig`), ``shadow_preview.py``
its live viewport overlay (:class:`ShadowPreview`); the panel is tentacle's
(``slots/tools/shadow_rig.py``). Mirrored by blendertk's ``rig_utils/shadow_rig/``.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_shadow_rig": ("ShadowRig",),
        "shadow_preview": ("ShadowPreview",),
    },
)
