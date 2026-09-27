# !/usr/bin/python
# coding=utf-8
"""Curtain tool — procedural draped cloth.

``_curtain.py`` is the engine (:class:`Rail`, :class:`CurtainMesh`,
:class:`CurtainRig`) over the vendored drape math in ``_curtain_drape.py``
(code-identical with blendertk's copy); ``curtain_slots.py`` +
``curtain.ui`` are the panel; ``presets/`` holds the shipped presets.
"""

import pythontk as ptk
from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_curtain": ("Rail", "CurtainMesh", "CurtainRig"),
        "curtain_slots": ("CurtainSlots",),
    },
)

# The catenary helpers were bound on the old ``curtain`` module as flat aliases.
ptk.Deprecation.attributes(
    globals(),
    {
        "catenary_shape": "pythontk.MathUtils.catenary",
        "sag_profile": "pythontk.MathUtils.catenary_sag",
    },
    remove_in="0.22.0",
    since="2026-09-26",
)
