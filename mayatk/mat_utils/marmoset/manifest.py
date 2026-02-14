# !/usr/bin/python
# coding=utf-8
"""Backwards-compatibility shim.

``MatManifest`` now lives at :mod:`mayatk.mat_utils.mat_manifest`.
This module re-exports the class so existing ``from
mayatk.mat_utils.marmoset.manifest import MatManifest`` still works.
"""
from mayatk.mat_utils.mat_manifest import MatManifest  # noqa: F401

__all__ = ["MatManifest"]
