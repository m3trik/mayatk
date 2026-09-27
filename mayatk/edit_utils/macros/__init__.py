# !/usr/bin/python
# coding=utf-8
"""Hotkey macros.

``_macros.py`` holds :class:`Macros` (the ``MacroManager`` binding store
plus the ``*Macros`` categories); ``presets/`` is the shipped, read-only
tier of its preset store. The user tier keeps the historical
``macro_manager`` folder name (``Macros.PRESET_NAME``) so saved bindings
survive; the editor is uitk's ``ShortcutEditor`` via
``Macros.show_editor()``.
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {
        "_macros": (
            "Macros",
            "MacroManager",
            "DisplayMacros",
            "EditMacros",
            "SelectionMacros",
            "UiMacros",
            "AnimationMacros",
        )
    },
)
