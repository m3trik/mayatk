# !/usr/bin/python
# coding=utf-8
"""Reference Manager tool — scene references, assemblies and workspaces.

``_reference_manager.py`` is the engine (:class:`ReferenceManager`,
:class:`AssemblyManager`), ``reference_manager_controller.py`` the panel-facing
controller, ``reference_manager_slots.py`` + ``reference_manager.ui`` the panel.
blendertk's twin is one flat module (``env_utils/reference_manager.py``: its
panel only), so it stays flat.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_reference_manager": ("ReferenceManager", "AssemblyManager"),
        "reference_manager_controller": ("ReferenceManagerController",),
        "reference_manager_slots": ("ReferenceManagerSlots",),
    },
)
