# !/usr/bin/python
# coding=utf-8
"""Subpackage — see root ``mayatk.__init__`` for public API.

The lightmap baker lives in its own subpackage because it ships bundled data
(the read-only ``presets/`` JSON tier loaded by
:meth:`LightmapBaker.preset_store`); keeping the logic, its ``.ui`` panel, and
that data co-located keeps the feature self-contained.
"""
