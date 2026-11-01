# !/usr/bin/python
# coding=utf-8
"""Articulated Rig tool -- rig a prop of rigid parts on hinge, swivel, ball and
slide joints (a desk lamp, a magnifier arm, a boom).

``_articulated_rig.py`` is the engine (:class:`ArticulatedRig`),
``_solver_expression.py`` writes its end control's solve as a MEL expression,
``grab_tool.py`` the viewport grab (:class:`ArticulatedRigGrab`); the panel is
tentacle's (``slots/tools/articulated_rig.py``). The math --
the joint model, the grab solver and the geometry rules -- is pythontk's
(``ptk.ArticulationModel``, ``ptk.ArticulationAnalysis``), shared with the
Unity and WebXR runtimes.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_articulated_rig": ("ArticulatedRig",),
        "grab_tool": ("ArticulatedRigGrab",),
    },
)
