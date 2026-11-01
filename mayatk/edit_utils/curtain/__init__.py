# !/usr/bin/python
# coding=utf-8
"""Curtain tool — procedural draped cloth.

``_curtain.py`` is the engine (:class:`Rail`, :class:`CurtainMesh`,
:class:`CurtainRig`) over the drape math in ``ptk.CurtainDrape`` (the one
blendertk builds from too), and :class:`Curtain`, the tool's API that
tentacle's Curtain panel calls; ``presets/`` holds the shipped presets.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_curtain": ("Curtain", "Rail", "CurtainMesh", "CurtainRig"),
    },
)
