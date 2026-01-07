# !/usr/bin/python
# coding=utf-8
"""Diagnostics and repair helpers for Maya scenes, animation curves, and meshes."""
from __future__ import annotations

# Lazy-loaded via parent package (mayatk.core_utils or mayatk root)
# No explicit imports needed - bootstrap_package handles attribute resolution

from .transform_diag import TransformDiagnostics

fix_non_orthogonal_axes = TransformDiagnostics.fix_non_orthogonal_axes
