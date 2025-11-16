# !/usr/bin/python
# coding=utf-8
"""Diagnostics and repair helpers for Maya animation curves and meshes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pythontk.core_utils.module_resolver import bootstrap_package

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .animation import (
        AnimCurveDiagnostics,
        AnimCurveRepair,
        repair_corrupted_curves,
    )
    from .mesh import MeshDiagnostics, MeshRepair, clean_geometry, get_ngons


DEFAULT_INCLUDE = {
    "animation": ["AnimCurveDiagnostics"],
    "mesh": ["MeshDiagnostics"],
}

DEFAULT_FALLBACKS = {
    "AnimCurveRepair": "mayatk.core_utils.diagnostic.animation",
    "MeshRepair": "mayatk.core_utils.diagnostic.mesh",
    "repair_corrupted_curves": "mayatk.core_utils.diagnostic.animation",
    "clean_geometry": "mayatk.core_utils.diagnostic.mesh",
    "get_ngons": "mayatk.core_utils.diagnostic.mesh",
}


bootstrap_package(
    globals(),
    include=DEFAULT_INCLUDE,
    fallbacks=DEFAULT_FALLBACKS,
)
