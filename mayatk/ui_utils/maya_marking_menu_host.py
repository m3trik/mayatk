# !/usr/bin/python
# coding=utf-8
"""What a uitk marking menu needs from Maya: :class:`MayaMarkingMenuHost`.

Maya is a Qt application, so most of ``uitk.MarkingMenuHost`` keeps the plain-Qt answers
(``uitk.PureMarkingMenuHost``): its viewport keystrokes reach the menu's application shortcut,
Qt sees its mouse, and its windows stack as Qt windows. What Maya answers differently:

* **main_window** -- Maya's main window, the parent every tool window shows over;
* **shortcut_window_name** -- ``"MayaWindow"``, the always-up owner of application shortcuts;
* **ui_handler** -- ``MayaUiHandler``, which wraps Maya's native menus;
* **preload_only_from_host_loop** -- Maya's event loop is native, so the menu's warm-up waits
  out any script pumping events (a tick fired inside a test's ``processEvents`` built a start
  menu there and faulted Maya);
* **defer** -- ``maya.utils.executeDeferred`` (``userSetup.py`` runs before the UI exists),
  and a quiet no-op in batch / ``maya.standalone``, which has no GUI Qt to build on;
* **attach** -- the hotkey-collision checker on the shortcut editors.
"""

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

from uitk import PureMarkingMenuHost


class MayaMarkingMenuHost(PureMarkingMenuHost):
    """Maya's answers to ``uitk.MarkingMenuHost`` (see the module docstring)."""

    preload_only_from_host_loop = True
    shortcut_window_name = "MayaWindow"

    #: The shortcut editors whose builds carry Maya's hotkey-collision checker.
    COLLISION_CHECKED_EDITORS = ("shortcut_editor", "global_shortcuts")

    @property
    def ui_handler(self):
        """``MayaUiHandler``: wraps Maya's native menus, shows the tool windows."""
        from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

        return MayaUiHandler

    @staticmethod
    def is_batch() -> bool:
        """True under ``maya -batch`` / ``maya.standalone`` (no GUI, no real Qt)."""
        try:
            return bool(cmds.about(batch=True))
        except Exception:  # cmds unavailable/uninitialized — assume interactive
            return False

    def defer(self, fn):
        """Run *fn* on Maya's idle queue — ``userSetup.py`` runs before the UI exists.

        A batch/standalone session is a deliberate, quiet no-op (returns None). The same
        ``userSetup.py`` also runs under ``mayapy`` / ``maya -batch``, so raising there would
        break every headless run — and there is nothing to build anyway: batch has no GUI Qt
        (QWidget construction on Maya's non-GUI stub takes the interpreter down natively —
        0xC0000409, no traceback) and no idle queue to defer onto. Checked up front rather
        than relying on which branch below happens to fire: ``mayapy`` ships ``maya.utils``
        *without* ``executeDeferred`` until ``standalone.initialize()`` has run, so the
        fallback's meaning flips mid-session.

        Returns:
            *fn*'s result when it ran now (an interactive session with no idle queue),
            else None.
        """
        if self.is_batch():
            return None
        try:
            from maya.utils import executeDeferred
        except (
            ImportError
        ):  # no idle queue to defer onto — interactive but unusual; run now.
            return fn()
        executeDeferred(fn)
        return None

    def main_window(self):
        """Maya's main window (a ``QMainWindow``), or None (the menu logs a failure)."""
        from mayatk.ui_utils._ui_utils import UiUtils

        return UiUtils.get_main_window()

    def attach(self, menu) -> None:
        """Register Maya's hotkey-collision checker on the bundled shortcut editors
        whenever one is built — lazily, so the editor module stays unimported until the
        user opens it."""
        from mayatk.ui_utils.hotkey_collisions import HotkeyCollisions

        editors = getattr(menu.sb, "editors", None)
        add_hook = getattr(editors, "add_post_build_hook", None)
        if not callable(add_hook):
            return
        for name in self.COLLISION_CHECKED_EDITORS:
            add_hook(
                name,
                lambda editor: editor.add_collision_checker(
                    HotkeyCollisions.maya_collision_checker
                ),
            )


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
