# !/usr/bin/python
# coding=utf-8
"""Material manifest convenience re-export.

``MatManifest`` lives at :mod:`mayatk.mat_utils.mat_manifest`; this shim
mirrors :mod:`mayatk.mat_utils.marmoset_bridge.manifest` so templates that
want to wire Painter texture sets to Maya materials can import from a
predictable place inside the bridge subpackage.
"""
from mayatk.mat_utils.mat_manifest import MatManifest  # noqa: F401

__all__ = ["MatManifest"]
