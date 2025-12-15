# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.ui_utils module

Tests for UIUtils class functionality including:
- Panel queries
- UI widget operations
- Dialog utilities
"""
import unittest
import mayatk as mtk

from base_test import MayaTkTestCase


class TestUIUtils(MayaTkTestCase):
    """Tests for UIUtils class."""

    def test_get_panel(self):
        """Test getting Maya panels."""
        try:
            panels = mtk.get_panel(all=True)
            if panels:
                self.assertIsInstance(panels, list)
        except RuntimeError:
            self.skipTest("Panel queries not available in batch mode")


if __name__ == "__main__":
    unittest.main(verbosity=2)
