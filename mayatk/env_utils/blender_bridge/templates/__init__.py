# !/usr/bin/python
# coding=utf-8
"""Blender-side import recipes for the Blender bridge.

Each ``*.py`` here is an executable Blender Python script with ``__KEY__`` placeholders that
:class:`BlenderBridge` substitutes (FBX path + parameter values) before launching Blender with
``--python``. A ``BRIDGE_MODES = (...)`` constant declares the supported modes. Underscore-prefixed
files are ignored by template discovery.
"""
