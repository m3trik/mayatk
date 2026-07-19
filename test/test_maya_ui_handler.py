# !/usr/bin/python
# coding=utf-8
"""Unit tests for ``MayaUiHandler.can_resolve`` — the native-menu resolution hook.

Regression guard for the marking-menu nav-click resolver. A nav button whose
``target`` is a native Maya menu (e.g. ``"key"``) is built lazily from
``MayaNativeMenus.MENU_MAPPING``, not from a ``.ui`` file — so it is NOT a
registered file stem. uitk's ``ui_name_resolves`` consults this ``can_resolve``
hook to decide whether a click should open the native menu (resolvable) or fall
back to the ``key#submenu`` overlay (unresolvable). The regression (commit
2cc9858) narrowed resolution to file stems only, so native menus wrongly fell
back to their submenu overlay and never opened on release. This pins the mayatk
side of the fix: ``can_resolve`` must report ``MENU_MAPPING`` membership while
still delegating real ``.ui`` stems to the inherited base hook.

Pure membership logic — no Maya runtime needed (``maya.cmds`` is mocked by the
package ``conftest``); ``__init__`` is bypassed so no slot/UI discovery runs.
"""
import types
import unittest

from mayatk.ui_utils.maya_ui_handler import MayaUiHandler
from mayatk.ui_utils.maya_native_menus import MayaNativeMenus


class TestMayaUiHandlerCanResolve(unittest.TestCase):
    def setUp(self):
        # can_resolve only touches self.sb.is_registered_ui (via the base) and
        # the module-level MENU_MAPPING — so bypass the heavy __init__.
        self.handler = object.__new__(MayaUiHandler)
        self._registered = {"some_ui_file"}
        self.handler.sb = types.SimpleNamespace(
            is_registered_ui=lambda n: n in self._registered
        )

    def test_key_menu_resolves(self):
        """The reported regression case: the native 'key' menu must resolve."""
        self.assertIn("key", MayaNativeMenus.MENU_MAPPING)
        self.assertTrue(self.handler.can_resolve("key"))

    def test_any_native_menu_name_resolves(self):
        """Every MENU_MAPPING name resolves without building anything."""
        for menu_name in MayaNativeMenus.MENU_MAPPING:
            self.assertTrue(
                self.handler.can_resolve(menu_name),
                f"native menu {menu_name!r} should resolve",
            )

    def test_unregistered_non_menu_does_not_resolve(self):
        """An unknown name (neither file stem nor native menu) is unresolvable."""
        self.assertFalse(self.handler.can_resolve("definitely_not_a_thing"))

    def test_registered_file_stem_resolves_via_base(self):
        """A registered .ui stem resolves through the inherited base hook."""
        self.assertTrue(self.handler.can_resolve("some_ui_file"))

    def test_submenu_suffix_strips_to_registered_base(self):
        """'<stem>#submenu' resolves via the base's '#' stripping."""
        self.assertTrue(self.handler.can_resolve("some_ui_file#submenu"))

    def test_empty_name_does_not_resolve(self):
        self.assertFalse(self.handler.can_resolve(""))


class TestMayaUiHandlerLogLinkRegistration(unittest.TestCase):
    """Lock the log-link dependency-inversion wiring MayaUiHandler.__init__ does.

    That registration is wrapped in try/except (never block UI startup), so a
    drifted import path would be swallowed silently. This exercises the exact
    import + registration the handler performs, so a move of
    ``UiUtils.dispatch_log_link`` fails loudly here (the blendertk suite covers
    the full-construction path; MayaUiHandler.__init__ needs a GUI Maya).
    """

    def test_dispatch_log_link_registers_with_uitk(self):
        from uitk.bridge.slots import _LOG_LINK_HANDLERS, register_log_link_handler
        from mayatk.ui_utils._ui_utils import UiUtils

        saved = list(_LOG_LINK_HANDLERS)
        try:
            _LOG_LINK_HANDLERS.clear()
            register_log_link_handler(UiUtils.dispatch_log_link)
            self.assertIn(UiUtils.dispatch_log_link, _LOG_LINK_HANDLERS)
        finally:
            _LOG_LINK_HANDLERS[:] = saved


if __name__ == "__main__":
    unittest.main()
