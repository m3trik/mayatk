# !/usr/bin/python
# coding=utf-8
"""Macro Manager — a uitk panel for assigning hotkeys and categories to the
``mayatk`` macros, with save/load preset support.

The panel is a thin consumer of the ``MacroManager`` management API
(:mod:`mayatk.edit_utils.macros`); all binding/persistence logic lives there so
it is equally usable headlessly (e.g. the ``userSetup`` startup path).
"""
