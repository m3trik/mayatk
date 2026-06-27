# !/usr/bin/python
# coding=utf-8
"""Logic tests for the Macro Manager panel slots.

Covers the preset capture/restore round-trip for *categories* — including
unbound macros, whose re-categorization has nowhere to live except a saved
preset. The slot methods under test depend only on ``_bindings`` /
``_available`` / ``controller``, so they're exercised on a bare instance
(``object.__new__``) with a stub controller — no Switchboard / Qt UI.
"""
import unittest

from mayatk.edit_utils.macro_manager.macro_manager_slots import MacroManagerSlots


class _StubController:
    """Only the surface the export/import/blank methods touch."""

    _CATS = {"m_bound": "Display", "m_unbound": "Edit", "m_other": "Display"}

    @classmethod
    def macro_category(cls, name):
        return cls._CATS.get(name, "")

    @staticmethod
    def clear_hotkey(name, key=None):
        pass

    @staticmethod
    def apply_bindings(data):
        pass


def _slots(available, bindings):
    slots = object.__new__(MacroManagerSlots)
    slots._available = dict(available)
    slots._bindings = {k: dict(v) for k, v in bindings.items()}
    slots.controller = _StubController
    return slots


class TestExportBindings(unittest.TestCase):
    def test_captures_bound_and_category_overrides_only(self):
        """Bound macros + category overrides are saved; default-cat unbound
        macros are omitted (regression: only bound macros were saved, so an
        unbound macro's category change was silently dropped on save)."""
        slots = _slots(
            {"m_bound": "", "m_unbound": "", "m_other": ""},
            {
                "m_bound": {"key": "ctl+i", "cat": "Display"},
                "m_unbound": {"key": "", "cat": "Selection"},  # override (default Edit)
                "m_other": {"key": "", "cat": "Display"},  # at default -> omit
            },
        )
        out = slots._export_bindings()
        self.assertEqual(set(out), {"m_bound", "m_unbound"})
        self.assertEqual(out["m_unbound"], {"key": "", "cat": "Selection"})
        self.assertEqual(out["m_bound"], {"key": "ctl+i", "cat": "Display"})


class TestBlankBindings(unittest.TestCase):
    def test_keeps_mixin_default_categories(self):
        slots = _slots({"m_bound": "", "m_unbound": ""}, {})
        blank = slots._blank_bindings()
        self.assertEqual(blank["m_bound"], {"key": "", "cat": "Display"})
        self.assertEqual(blank["m_unbound"], {"key": "", "cat": "Edit"})


class TestImportBindings(unittest.TestCase):
    def test_restores_unbound_override_and_keeps_defaults(self):
        slots = _slots(
            {"m_bound": "", "m_unbound": "", "m_other": ""},
            {
                "m_bound": {"key": "ctl+i", "cat": "Display"},
                "m_unbound": {"key": "", "cat": "Edit"},
                "m_other": {"key": "", "cat": "Display"},
            },
        )
        slots._import_bindings({"m_unbound": {"key": "", "cat": "Selection"}})
        # The saved override is restored…
        self.assertEqual(slots._bindings["m_unbound"]["cat"], "Selection")
        # …and a macro absent from the preset keeps its default (not blank).
        self.assertEqual(slots._bindings["m_other"]["cat"], "Display")

    def test_keyless_entry_without_cat_falls_back_to_default(self):
        slots = _slots(
            {"m_bound": ""}, {"m_bound": {"key": "ctl+i", "cat": "Display"}}
        )
        slots._import_bindings({"m_bound": {"key": "ctl+i"}})  # no cat field
        self.assertEqual(slots._bindings["m_bound"]["cat"], "Display")


if __name__ == "__main__":
    unittest.main(verbosity=2)
