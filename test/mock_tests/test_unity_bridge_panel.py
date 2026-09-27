# !/usr/bin/python
# coding=utf-8
"""Unity Bridge panel helpers that need no Maya (mocked ``maya.cmds``, this dir's conftest)."""

import sys
import types
import unittest
from unittest import mock


class TestEditorVersionCombo(unittest.TestCase):
    """The Editor combo lists installed Unity versions, newest first."""

    def test_versions_rank_numerically_not_as_strings(self):
        """A string sort ranked ``2022.3.9f1`` above ``2022.3.10f1`` ('9' > '1'),
        so the "newest" at the top of the list was an older Editor."""
        from mayatk.env_utils.unity_bridge.unity_bridge_slots import UnityBridgeSlots

        # The real key, carried by a stand-in module (unitytk is optional).
        try:
            from unitytk.launcher import UnityFinder as _RealFinder
        except ImportError:
            self.skipTest("unitytk is not installed (the optional 'unity' extra)")

        finder = types.SimpleNamespace(
            find_editors=lambda: {
                "2022.3.9f1": "a",
                "2022.3.10f1": "b",
                "6000.0.2f1": "c",
            },
            version_sort_key=_RealFinder.version_sort_key,
        )
        added = []
        combo = types.SimpleNamespace(addItem=lambda text, data: added.append(text))
        host = types.SimpleNamespace(_param_widgets={"UNITY_VERSION": combo})
        with mock.patch.dict(
            sys.modules, {"unitytk": types.SimpleNamespace(UnityFinder=finder)}
        ):
            UnityBridgeSlots._populate_unity_versions(host)
        self.assertEqual(added, ["6000.0.2f1", "2022.3.10f1", "2022.3.9f1"])


class TestModeCombo(unittest.TestCase):
    """The template combo picks a MODE (Copy to Project / Manage Unity Scripts)."""

    def test_combo_offers_no_template_management(self):
        """``template_dir`` is the package dir, a stand-in for the no-op description
        lookup, so the combo's Refresh Templates row re-scanned nothing and Open
        Templates Folder revealed a folder of .py source. extapps' Unity panel opted
        out on 2026-09-10 (uitk ``TEMPLATE_MENU``); this twin kept both rows."""
        from mayatk.env_utils.unity_bridge.unity_bridge_slots import UnityBridgeSlots

        self.assertFalse(UnityBridgeSlots.TEMPLATE_MENU)


if __name__ == "__main__":
    unittest.main()
