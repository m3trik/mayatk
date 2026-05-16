# !/usr/bin/python
# coding=utf-8
"""Backwards-compatibility shim.

``MatManifest`` lives at :mod:`mayatk.mat_utils.mat_manifest`.
Re-exported here so legacy ``from mayatk.mat_utils.marmoset_bridge.manifest
import MatManifest`` imports still resolve.
"""
from mayatk.mat_utils.mat_manifest import MatManifest  # noqa: F401

__all__ = ["MatManifest"]
