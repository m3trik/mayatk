# !/usr/bin/python
# coding=utf-8
"""Convenience facade exposing repair helpers at ``mayatk.repair``."""

import inspect
from typing import Callable, Dict

from mayatk.core_utils.repair import AnimCurveRepair, MeshRepair, Repair, repair


__all__ = [
    "AnimCurveRepair",
    "MeshRepair",
    "Repair",
    "repair",
]


def _export_public_methods(instance: Repair) -> Dict[str, Callable]:
    exports: Dict[str, Callable] = {}
    for name, member in inspect.getmembers(instance, predicate=callable):
        if name.startswith("_"):
            continue
        exports[name] = member
    return exports


_METHOD_EXPORTS = _export_public_methods(repair)
globals().update(_METHOD_EXPORTS)
__all__.extend(sorted(_METHOD_EXPORTS))
