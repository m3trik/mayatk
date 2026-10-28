# !/usr/bin/python
# coding=utf-8
"""Reference Manager tool — scene references, assemblies and workspaces.

``_reference_manager.py`` is the engine (:class:`ReferenceManager`,
:class:`AssemblyManager`); its panel is tentacle's shared Reference Manager
(``slots/tools/reference_manager.py``). blendertk's twin engine is one flat module
(``env_utils/reference_manager.py``).
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_reference_manager": ("ReferenceManager", "AssemblyManager"),
    },
)
