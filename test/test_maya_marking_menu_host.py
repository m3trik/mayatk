# !/usr/bin/python
# coding=utf-8
"""Tests for ``mayatk.MayaMarkingMenuHost`` — what a uitk marking menu needs from Maya.

Pinned here: it is a ``uitk.MarkingMenuHost``; it names Maya's main window as the shortcut
owner and ``MayaUiHandler`` as the UI handler; its preload advances only from Maya's own loop;
``defer`` is a quiet no-op in batch / ``maya.standalone`` (there is no GUI Qt to build on --
the reason the launcher used to check first) and runs on the idle queue otherwise; ``attach``
puts Maya's hotkey-collision checker on the shortcut editors. Mock-only: no Maya scene state.
"""

import unittest
from unittest import mock

from mayatk.ui_utils.maya_marking_menu_host import MayaMarkingMenuHost


class TestMayaMarkingMenuHost(unittest.TestCase):
    def setUp(self):
        self.host = MayaMarkingMenuHost()

    def test_it_is_a_marking_menu_host(self):
        from uitk import MarkingMenuHost, PureMarkingMenuHost

        self.assertIsInstance(self.host, MarkingMenuHost)
        self.assertIsInstance(self.host, PureMarkingMenuHost)

    def test_the_package_root_publishes_it(self):
        import mayatk as mtk

        self.assertIs(mtk.MayaMarkingMenuHost, MayaMarkingMenuHost)

    def test_maya_answers(self):
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        self.assertEqual(self.host.shortcut_window_name, "MayaWindow")
        self.assertIs(self.host.ui_handler, MayaUiHandler)
        self.assertTrue(self.host.preload_only_from_host_loop)

    def test_batch_defers_nothing_and_builds_nothing(self):
        built = []
        with mock.patch.object(
            MayaMarkingMenuHost, "is_batch", staticmethod(lambda: True)
        ):
            self.assertIsNone(self.host.defer(lambda: built.append(True)))
        self.assertEqual(built, [])

    def test_an_interactive_session_defers_onto_the_idle_queue(self):
        import maya.utils

        built, queued = [], []
        with (
            mock.patch.object(
                MayaMarkingMenuHost, "is_batch", staticmethod(lambda: False)
            ),
            mock.patch.object(
                maya.utils, "executeDeferred", side_effect=queued.append, create=True
            ),
        ):
            self.assertIsNone(self.host.defer(lambda: built.append(True)))
        self.assertEqual(built, [], "the build waits for the idle queue")
        self.assertEqual(len(queued), 1)
        queued[0]()
        self.assertEqual(built, [True])

    def test_attach_puts_the_collision_checker_on_the_shortcut_editors(self):
        from mayatk.ui_utils.hotkey_collisions import HotkeyCollisions

        hooks = {}
        editors = mock.Mock()
        editors.add_post_build_hook.side_effect = lambda name, fn: hooks.__setitem__(
            name, fn
        )
        menu = mock.Mock(sb=mock.Mock(editors=editors))
        self.host.attach(menu)
        self.assertEqual(set(hooks), set(MayaMarkingMenuHost.COLLISION_CHECKED_EDITORS))
        editor = mock.Mock()
        hooks["shortcut_editor"](editor)
        editor.add_collision_checker.assert_called_once_with(
            HotkeyCollisions.maya_collision_checker
        )


if __name__ == "__main__":
    unittest.main()
