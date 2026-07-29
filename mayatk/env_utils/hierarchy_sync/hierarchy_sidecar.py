# !/usr/bin/python
# coding=utf-8
"""Deprecated location — kept as an import alias for one release.

The hierarchy sidecar was generalized into the scene-data sidecar (hierarchy
baseline + ``data_export`` channel snapshot in one ``.scene_data.json``
manifest).  Import :class:`SceneDataSidecar` from
:mod:`mayatk.env_utils.hierarchy_sync.scene_data_sidecar` instead.
"""
from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
    SceneDataSidecar as HierarchySidecar,
)

__all__ = ["HierarchySidecar"]
