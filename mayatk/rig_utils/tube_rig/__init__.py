# !/usr/bin/python
# coding=utf-8
"""Tube Rig tool — rig a tube-shaped mesh (hose, cable, piston, tail).

``_tube_rig.py`` is the engine (:class:`TubeRig`), ``strategies.py`` the build
strategies and :class:`TubeRigBundle`, ``tube_path.py`` the centerline
geometry (:class:`TubePath`); ``tube_rig_slots.py`` + ``tube_rig.ui`` are the
panel. Mirrored by blendertk's ``rig_utils/tube_rig/``.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_tube_rig": ("TubeRig",),
        "strategies": (
            "TubeRigBundle",
            "TubeStrategy",
            "FKChainStrategy",
            "SplineIKStrategy",
            "AnchorStrategy",
        ),
        "tube_path": ("TubePath",),
        "tube_rig_slots": ("TubeRigSlots", "RigModeConfig", "RIG_MODES"),
    },
)
