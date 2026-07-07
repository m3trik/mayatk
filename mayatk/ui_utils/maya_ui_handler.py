# !/usr/bin/python
# coding=utf-8
import sys
import os
from typing import Optional, TYPE_CHECKING
from uitk import Switchboard
from uitk.handlers.ui_handler import UiHandler

try:
    from mayatk.ui_utils import maya_native_menus
except ImportError:
    maya_native_menus = None

if TYPE_CHECKING:
    from qtpy import QtWidgets


class MayaUiHandler(UiHandler):
    """UI Handler for Maya applications.

    Extends the generic UiHandler with Maya-specific menu wrapping
    and discovery of mayatk UI files.
    """

    def __init__(
        self,
        switchboard: Switchboard = None,
        log_level: str = "WARNING",
        **kwargs,
    ) -> None:
        """Initialize Maya UI Handler.

        ``switchboard`` is optional. When omitted, a fresh ``Switchboard``
        is constructed so the handler can be stood up by a shelf script
        without any prior setup. Production callers (e.g. tentacle's
        ``tcl_maya``) still pass an explicit instance to share state
        with the rest of the application.
        """
        self.root_dir = os.path.dirname(sys.modules["mayatk"].__file__)

        if switchboard is None:
            from uitk import Switchboard as _Switchboard

            switchboard = _Switchboard()

        super().__init__(
            switchboard=switchboard,
            ui_root=self.root_dir,
            slot_root=self.root_dir,
            discover_slots=True,
            recursive=True,
            log_level=log_level,
            source_tags={"mayatk"},
            **kwargs,
        )

    @classmethod
    def instance(cls, switchboard: Switchboard = None, **kwargs) -> "MayaUiHandler":
        """Return the MayaUiHandler singleton, bootstrapping if needed.

        Overrides :meth:`UiHandler.instance` so a shelf-style call works
        regardless of prior setup state:

        - **Pre-existing handler** (e.g. tentacle's ``tcl_maya`` already
          ran ``MayaUiHandler.instance(switchboard=tentacle_sb)``): the
          existing instance is returned, even when this call is made
          without a switchboard argument. The base implementation keys
          singletons by ``id(switchboard)``, which would otherwise treat
          ``id(None)`` as a separate slot and try to build a fresh,
          broken handler.
        - **No handler yet, switchboard given**: standard UiHandler path,
          singleton keyed by ``id(switchboard)``.
        - **No handler yet, no switchboard**: ``MayaUiHandler.__init__``
          bootstraps a fresh ``Switchboard`` (see __init__).

        This makes the one-liner pattern reliable from a Maya shelf::

            from mayatk.ui_utils.maya_ui_handler import MayaUiHandler
            MayaUiHandler.instance().editors.show("browser")
        """
        if switchboard is None:
            # SingletonMixin._instances is shared across all subclasses and
            # never pruned, so it can hold handlers from torn-down sessions
            # (their switchboard's C++ object deleted with its parent). Walk
            # newest-first, filtered to our class, and skip — and prune — any
            # handler whose switchboard is dead: returning one would make
            # every subsequent call raise RuntimeError on deleted Qt objects.
            # Newest-first also prefers the production handler (e.g. tentacle's)
            # over an older shelf-bootstrapped one when both are alive.
            for key, inst in list(cls._instances.items()):
                if not isinstance(inst, cls):
                    continue
                sb = getattr(inst, "sb", None)
                # Switchboard's shared liveness probe; treat a missing probe
                # (older uitk) as alive rather than guessing.
                is_alive = getattr(sb, "_widget_is_alive", None) if sb else None
                if is_alive is not None and not is_alive(sb):
                    del cls._instances[key]
            for inst in reversed(list(cls._instances.values())):
                if isinstance(inst, cls):
                    return inst
        return super().instance(switchboard=switchboard, **kwargs)

    def can_resolve(self, name: str) -> bool:
        """Recognise the native Maya menus this handler builds on demand.

        Without this, a nav button whose ``target`` is a native menu (``"key"``)
        — built lazily from ``MENU_MAPPING``, not a ``.ui`` file — reads as
        unresolvable, so the marking menu's click resolution falls back to the
        ``key#submenu`` overlay instead of opening the native menu. Membership
        only; no menu is built here.
        """
        if maya_native_menus and name in maya_native_menus.MayaNativeMenus.MENU_MAPPING:
            return True
        return super().can_resolve(name)

    def get(self, name: str, reload: bool = False, **kwargs) -> "QtWidgets.QMainWindow":
        """Retrieve a UI, checking Maya menus first."""
        # Check if name corresponds to a Maya menu
        if maya_native_menus and name in maya_native_menus.MayaNativeMenus.MENU_MAPPING:
            # The base get() honors ``reload``; map it onto the menu branch's
            # ``overwrite`` so ``get(name, reload=True)`` rebuilds here too
            # instead of silently returning the cached wrapper.
            kwargs.setdefault("overwrite", reload)
            return self._load_maya_ui(menu_key=name, **kwargs)

        return super().get(name, reload=reload, **kwargs)

    def apply_styles(self, ui, style=None):
        """Override to give mayatk-sourced UIs a hide button instead of pin."""
        import copy

        style = copy.deepcopy(style or self.DEFAULT_STYLE)
        try:
            if ui.has_tags(["mayatk"]):
                style["header_buttons"] = ("menu", "collapse", "hide")
        except AttributeError:
            pass
        # Pass pre-built style so the base skips its own deepcopy.
        super().apply_styles(ui, style=style)

    def _load_maya_ui(
        self,
        menu_key: str,
        header: bool = True,
        overwrite: bool = False,
    ) -> Optional["QtWidgets.QMainWindow"]:
        """Load and wrap a Maya menu by key."""
        if not maya_native_menus:
            return None

        if not hasattr(self, "_maya_native_menus"):
            self._maya_native_menus = maya_native_menus.MayaNativeMenus()
        handler = self._maya_native_menus

        if not overwrite:
            cached = self.sb.loaded_ui.peek(menu_key)
            if cached is not None:
                self.logger.debug(f"[{menu_key}] Returning cached Maya UI")
                return cached

        menu_widget = handler.get_menu(menu_key)
        if not menu_widget:
            # Native menu couldn't be built in this Maya version (stale mapping
            # / removed proc / renamed shell). Fall back to the hand-authored
            # '<key>#submenu' overlay when one is registered — tentacle ships
            # these for every Maya menu, so the user still gets a usable menu
            # instead of a broken empty one. This is the same overlay the
            # marking menu uses for an unresolvable target (see can_resolve),
            # engaged here at build time. Degrades to None when no overlay is
            # registered (e.g. a standalone mayatk switchboard).
            overlay = f"{menu_key}#submenu"
            try:
                if self.sb.is_registered_ui(overlay):
                    fallback = self.sb.get_ui(overlay)
                    if fallback is not None:
                        self.logger.debug(
                            f"[{menu_key}] Native menu unavailable; "
                            f"falling back to '{overlay}' overlay."
                        )
                        return fallback
            except Exception as e:  # noqa: BLE001 - fallback must never raise
                self.logger.debug(f"[{menu_key}] Overlay fallback failed: {e}")
            self.logger.debug(f"Could not retrieve Maya menu for '{menu_key}'")
            return None

        # Retrieve Maya Main Window for correct parenting (ensures Z-order on top)
        try:
            from mayatk.ui_utils._ui_utils import UiUtils

            maya_window = UiUtils.get_main_window()
        except ImportError:
            maya_window = None

        self.logger.debug(f"[{menu_key}] Creating MainWindow wrapper for Maya menu")
        ui = self.sb.add_ui(
            widget=menu_widget,
            name=menu_key,
            tags={"maya", "menu"},
            overwrite=overwrite,
            add_footer=False,
            restore_window_size=False,
            parent=maya_window,
        )

        # add_ui just registered the window into loaded_ui — from here on a
        # raise would leave a half-built UI cached (the peek() above returns
        # it forever after). Evict from both caches on any failure and fail
        # soft; the next call rebuilds from scratch.
        try:
            # Add Window flag without clobbering anything MainWindow already set.
            # When a QMainWindow has a parent, Qt treats it as an embedded child;
            # the Window flag keeps it a floating tool window.
            ui.set_flags(Window=True)

            if header:
                ui.header = self.sb.registered_widgets.Header()
                ui.header.setTitle(ui.objectName().upper())
                ui.header.attach_to(ui.centralWidget())
                ui.style.set(ui.header, "dark", "Header")
                self.logger.debug(
                    f"[{menu_key}] Header attached: hasattr(ui, 'header')={hasattr(ui, 'header')}, "
                    f"header.window() is ui: {ui.header.window() is ui}"
                )

            ui.edit_tags(add="maya_menu")
            self.logger.debug(f"[{menu_key}] Maya UI created with tags={ui.tags}")

            # Apply styles (including header buttons) through the normal pipeline.
            # Maya native menus don't have the 'mayatk' tag, so they get the
            # default pin button from DEFAULT_STYLE.
            self.apply_styles(ui)

            # Menu is fully populated (synchronous in get_menu); lock the
            # window to exact content size before it is ever shown.
            menu_widget.fit_to_window()

            return ui
        except Exception as e:
            self.logger.error(
                f"[{menu_key}] Wrapper setup failed after registration; "
                f"evicting the half-built UI: {type(e).__name__}: {e}"
            )
            try:
                del self.sb.loaded_ui[menu_key]
            except Exception:  # noqa: BLE001 - eviction is best-effort
                pass
            handler.menus.pop(menu_key, None)
            ui.deleteLater()
            return None
