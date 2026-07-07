# !/usr/bin/python
# coding=utf-8
"""Unit tests for MayaUiHandler bootstrap and caching behavior (mocked Maya).

Runs under plain pytest with this dir's conftest (mocked ``maya.cmds``) — no
Maya or Qt event loop needed; everything is exercised through stubs in the
established ``__new__`` + hand-set-attrs pattern.

Covers three hazards in the shelf-bootstrap / native-menu path:

* ``instance()`` (no-arg): ``SingletonMixin._instances`` is shared across all
  subclasses and never pruned, so it can hold handlers from torn-down
  sessions. The no-arg call must skip handlers whose switchboard's C++
  object is dead (and prune them), preferring the newest live handler —
  otherwise a stale handler shadows the live one and every call on it hits
  RuntimeError on deleted Qt objects.
* ``get(name, reload=True)`` for a native-menu key must map ``reload`` onto
  ``_load_maya_ui(overwrite=...)`` — the base ``get`` honors ``reload``; the
  menu branch silently dropped it.
* ``_load_maya_ui``: ``sb.add_ui`` registers the wrapper into ``loaded_ui``
  immediately; a failure in the post-registration steps (flags / header /
  styles) must evict the half-built UI from ``loaded_ui`` AND the native-menus
  cache instead of leaving it to be returned by ``peek`` forever after.
"""
import unittest
from unittest.mock import MagicMock, patch

from conftest import mock_cmds  # noqa: F401 - installs the maya mocks

from mayatk.ui_utils.maya_ui_handler import MayaUiHandler


class _FakeSB:
    """Stand-in for Switchboard's liveness-probe contract.

    Mirrors ``Switchboard._widget_is_alive`` (uitk/switchboard/widgets.py):
    probe ``objectName()``, treat RuntimeError/AttributeError as dead.
    """

    def __init__(self, alive=True):
        self._alive_flag = alive

    def objectName(self):
        if not self._alive_flag:
            raise RuntimeError("Internal C++ object (Switchboard) already deleted.")
        return "switchboard"

    @staticmethod
    def _widget_is_alive(instance):
        try:
            instance.objectName()
            return True
        except (RuntimeError, AttributeError):
            return False


class _InstancesSandbox(unittest.TestCase):
    """Snapshot/restore the shared SingletonMixin registry around each test."""

    def setUp(self):
        self._saved = dict(MayaUiHandler._instances)
        MayaUiHandler._instances.clear()

    def tearDown(self):
        MayaUiHandler._instances.clear()
        MayaUiHandler._instances.update(self._saved)

    def fabricate(self, alive=True):
        # object.__new__ bypasses SingletonMixin.__new__ — a plain
        # MayaUiHandler.__new__ would return the cached singleton, making
        # every fabricated handler the same object.
        handler = object.__new__(MayaUiHandler)
        handler.sb = _FakeSB(alive=alive)
        MayaUiHandler._instances[("test-key", id(handler))] = handler
        return handler


class TestInstanceLiveness(_InstancesSandbox):
    def test_skips_dead_switchboard_handler(self):
        self.fabricate(alive=False)  # older, torn-down session
        live = self.fabricate(alive=True)
        self.assertIs(
            MayaUiHandler.instance(),
            live,
            "no-arg instance() must skip a handler whose sb is dead",
        )

    def test_prefers_newest_live_handler(self):
        self.fabricate(alive=True)  # older bootstrap handler
        newest = self.fabricate(alive=True)  # production handler created later
        self.assertIs(
            MayaUiHandler.instance(),
            newest,
            "no-arg instance() must prefer the most recently created live handler",
        )

    def test_prunes_dead_entries(self):
        dead = self.fabricate(alive=False)
        live = self.fabricate(alive=True)
        MayaUiHandler.instance()
        self.assertNotIn(
            dead,
            MayaUiHandler._instances.values(),
            "dead handlers must be pruned from the singleton registry",
        )
        self.assertIn(live, MayaUiHandler._instances.values())


class TestGetReloadPassthrough(unittest.TestCase):
    def test_reload_maps_to_overwrite_for_native_menu(self):
        handler = object.__new__(MayaUiHandler)
        with patch.object(handler, "_load_maya_ui", return_value=MagicMock()) as load:
            handler.get("edit", reload=True)
        load.assert_called_once_with(menu_key="edit", overwrite=True)

    def test_default_get_does_not_overwrite(self):
        handler = object.__new__(MayaUiHandler)
        with patch.object(handler, "_load_maya_ui", return_value=MagicMock()) as load:
            handler.get("edit")
        load.assert_called_once_with(menu_key="edit", overwrite=False)


class _FakeLoadedUi(dict):
    def peek(self, key):
        return self.get(key)


class TestLoadMayaUiEviction(unittest.TestCase):
    def _make_handler(self, fail_step="set_flags"):
        handler = object.__new__(MayaUiHandler)

        broken_ui = MagicMock(name="half_built_ui")
        getattr(broken_ui, fail_step).side_effect = RuntimeError("boom mid-wrap")

        sb = MagicMock(name="sb")
        sb.loaded_ui = _FakeLoadedUi()

        def add_ui(widget=None, name=None, **kwargs):
            sb.loaded_ui[name] = broken_ui
            return broken_ui

        sb.add_ui.side_effect = add_ui
        handler.sb = sb

        menu_widget = MagicMock(name="menu_widget")
        native = MagicMock(name="native_menus")
        native.get_menu.return_value = menu_widget
        native.menus = {"edit": menu_widget}
        handler._maya_native_menus = native

        return handler, sb, broken_ui, native

    def test_failure_after_add_ui_evicts_and_returns_none(self):
        handler, sb, broken_ui, native = self._make_handler()

        result = handler._load_maya_ui(menu_key="edit")

        self.assertIsNone(result, "a failed wrap must not hand back a half-built UI")
        self.assertNotIn(
            "edit",
            sb.loaded_ui,
            "the half-built UI must be evicted from loaded_ui (peek cache)",
        )
        self.assertNotIn(
            "edit",
            native.menus,
            "the native-menus cache must be evicted so the next call rebuilds",
        )
        broken_ui.deleteLater.assert_called_once()


if __name__ == "__main__":
    unittest.main()
