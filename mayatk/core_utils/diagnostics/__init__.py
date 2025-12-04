# !/usr/bin/python
# coding=utf-8
"""Diagnostics and repair helpers for Maya animation curves and meshes."""
from __future__ import annotations
from typing import TYPE_CHECKING
from pythontk.core_utils.module_resolver import bootstrap_package

# From this package:
from .animation import AnimCurveDiagnostics
from .mesh import MeshDiagnostics

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .animation import AnimCurveDiagnostics
    from .mesh import MeshDiagnostics


class Diagnostics(AnimCurveDiagnostics, MeshDiagnostics):
    """Unified diagnostics interface."""

    pass


DEFAULT_INCLUDE = {
    "animation": ["AnimCurveDiagnostics"],
    "mesh": ["MeshDiagnostics"],
}


bootstrap_package(globals(), include=DEFAULT_INCLUDE)
