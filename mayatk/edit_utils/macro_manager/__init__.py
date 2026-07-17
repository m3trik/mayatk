# !/usr/bin/python
# coding=utf-8
"""Shipped macro binding presets (``presets/``) — the built-in tier of the
``Macros`` preset store.

The bespoke Macro Manager panel that used to live here was retired: the UI is
now the unified uitk ``ShortcutEditor`` launched over the ``Macros``
controller via ``Macros.show_editor()`` (see :mod:`mayatk.edit_utils.macros`).
This package remains only as the anchor for the shipped preset data both that
editor and the headless ``apply_saved_macros`` startup path read.
"""
