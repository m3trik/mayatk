# !/usr/bin/python
# coding=utf-8
"""Articulated Rig tool -- rig a prop of rigid parts on hinge, swivel, ball and
slide joints (a desk lamp, a magnifier arm, a boom).

``_articulated_rig.py`` is the engine (:class:`ArticulatedRig`),
``grab_tool.py`` the viewport grab (:class:`ArticulatedRigGrab`), and
``articulated_rig_slots.py`` + ``articulated_rig.ui`` the panel. The math --
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
        "articulated_rig_slots": ("ArticulatedRigSlots",),
    },
)
