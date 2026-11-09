# !/usr/bin/python
# coding=utf-8
"""Install the substance_rpc plugin into Painter's user plugin folder.

Painter discovers Python plugins in
``<Documents>\\Adobe\\Adobe Substance 3D Painter\\python\\plugins`` (or
wherever ``SUBSTANCE_PAINTER_PLUGINS_PATH`` points). The
Painter-specific destination resolution is the DCC-specific bit this
module owns; the install strategy (symlink-first, copytree fallback,
``__pycache__`` filtering, idempotent) lives generically in
:mod:`pythontk.net_utils.rpc.installer`.

A freshly installed plugin is picked up on Painter's next launch (or via
**Python ▸ Reload Plugins Folder** in a running Painter). Painter starts a
``plugins/`` module at launch only when its own switch for it reads ``on``
(default off), so the install sets that switch the first time; the user can
still turn it off in Painter's **Python** menu, and that choice stands.

The install is the person's act (a panel's *Install in Painter*): it is how
the Painter they open serves scripts, so a bridge never makes it on its own --
a send only refreshes an install already there (:meth:`Installer.refresh`).
A Painter a bridge launches needs no install: it loads the plugin from
:meth:`Installer.launch_root` (``SUBSTANCE_PAINTER_PLUGINS_PATH``), touching
nothing of the person's.

**Vendored copy -- keep byte-identical.** This file ships in each host
library's Substance bridge and in the standalone Substance Scripts app; the
Maya library's copy is the source (edit it there, then mirror it). Drift
fails the vendor guard (``test_vendor_sync.py``).
"""

import os
import sys
from pathlib import Path
from typing import Optional

import pythontk as ptk
from pythontk.net_utils.rpc.installer import PluginInstaller, RpcPluginSpec


PLUGIN_NAME = "substance_rpc"
#: The launch root's copy: its own name, so a person's installed ``substance_rpc``
#: (which Painter finds first) cannot shadow it; it answers to ``substance_rpc``
#: in the Painter it serves (the plugin's ``_answer_to_the_plugins_name``).
LAUNCHED_NAME = "substance_rpc_launched"


class _InstallerInternal(object):
    """Internal helpers for Installer."""

    @staticmethod
    def _plugin_source_dir() -> Path:
        """Return the on-disk path to the plugin source (``plugin_src/substance_rpc``)."""
        return Path(__file__).resolve().parent / "plugin_src" / PLUGIN_NAME

    @staticmethod
    def _documents_dir() -> Optional[Path]:
        """Resolve the user's Documents folder, honouring OneDrive/redirects.

        Windows: ``SHGetKnownFolderPath(FOLDERID_Documents)`` -- the same
        API Painter itself uses, so a redirected Documents folder still
        resolves to where Painter actually looks. Linux: the
        ``XDG_DOCUMENTS_DIR`` entry of ``user-dirs.dirs``, which Qt (and so
        Painter) reads -- a localized desktop names it e.g. ``~/Dokumente``.
        Fallback: ``~/Documents``.
        """
        if sys.platform == "win32":
            try:
                import ctypes
                import ctypes.wintypes
                from uuid import UUID

                class _GUID(ctypes.Structure):
                    _fields_ = [
                        ("Data1", ctypes.wintypes.DWORD),
                        ("Data2", ctypes.wintypes.WORD),
                        ("Data3", ctypes.wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8),
                    ]

                    def __init__(self, uuid_str):
                        super().__init__()
                        u = UUID(uuid_str)
                        self.Data1, self.Data2, self.Data3 = (
                            u.time_low,
                            u.time_mid,
                            u.time_hi_version,
                        )
                        for i, b in enumerate(u.bytes[8:]):
                            self.Data4[i] = b

                folderid_documents = _GUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")
                out = ctypes.c_wchar_p()
                res = ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(folderid_documents), 0, None, ctypes.byref(out)
                )
                if res == 0 and out.value:
                    path = Path(out.value)
                    ctypes.windll.ole32.CoTaskMemFree(out)
                    return path
            except Exception:  # noqa: BLE001 -- fall through to expanduser
                pass
        home = os.path.expanduser("~")
        if sys.platform.startswith("linux"):
            config = ptk.UserConfig.xdg_home("config")
            try:
                with open(
                    os.path.join(config, "user-dirs.dirs"), encoding="utf-8"
                ) as f:
                    for line in f:
                        key, _, value = line.strip().partition("=")
                        if key == "XDG_DOCUMENTS_DIR" and value.strip('"'):
                            docs = Path(value.strip('"').replace("$HOME", home))
                            if docs.is_dir():
                                return docs
            except OSError:  # no user-dirs file: the default below
                pass
        fallback = Path(home) / "Documents"
        return fallback if fallback.is_dir() else None


#: Painter's own settings store, and the switch that makes it start a
#: ``plugins/`` module at launch (``resources/python/startup/
#: painter_plugins_ui.py``: ``launch_at_start``, default off).
PAINTER_SETTINGS = ("Adobe", "Adobe Substance 3D Painter")
LAUNCH_AT_START_KEY = f"python_plugins/{PLUGIN_NAME}/launch_at_start"


class Installer(_InstallerInternal):
    """Installer — module namespace."""

    @staticmethod
    def user_plugin_dir() -> Optional[Path]:
        """Resolve Painter's Python plugins folder.

        Tier 1: ``SUBSTANCE_PAINTER_PLUGINS_PATH`` -- Adobe's documented
                override. Accepts either the plugins folder itself or its
                ``python`` parent (a ``plugins`` component is appended
                when the path doesn't already end in one).
        Tier 2: ``<Documents>\\Adobe\\Adobe Substance 3D Painter\\python\\plugins``.
        Tier 3: ``None`` -- caller has to install manually.
        """
        env = os.environ.get("SUBSTANCE_PAINTER_PLUGINS_PATH")
        if env:
            path = Path(env)
            return path if path.name.lower() == "plugins" else path / "plugins"

        docs = _InstallerInternal._documents_dir()
        if docs is None:
            return None
        return docs / "Adobe" / "Adobe Substance 3D Painter" / "python" / "plugins"

    @staticmethod
    def is_installed() -> bool:
        """True if the plugin is present at the resolved user plugin dir.

        Presence only -- it says nothing about *which* version is there.
        Use :meth:`is_current` to decide whether an install needs a refresh.
        """
        return PluginInstaller.status(PLUGIN_SPEC) in ("current", "stale")

    @staticmethod
    def is_current() -> bool:
        """True if the installed plugin matches the one this package ships.

        A copytree install (any machine without Developer Mode) is a
        snapshot, so an install from an older release can be missing ops the
        bridge now dispatches -- which fails as an unknown-op error at
        invoke time, i.e. the knob silently doing nothing.
        """
        return PluginInstaller.status(PLUGIN_SPEC) == "current"

    @staticmethod
    def install(force: bool = False) -> Optional[Path]:
        """Install the plugin into Painter's user plugin folder.

        Returns the final plugin directory (or *None* if no destination
        could be resolved). An install that already matches the shipped
        source is left alone; one that has drifted is rebuilt. *force*
        rebuilds even a matching install. Painter's launch-at-start switch is
        the person's install's alone (:meth:`enable_launch_at_start`, a
        panel's *Install in Painter*), never a refresh's.
        """
        dest = PLUGIN_SPEC.destination()
        return None if dest is None else PluginInstaller.install_plugin(PLUGIN_SPEC.source, dest, force=force)

    @staticmethod
    def refresh() -> Optional[Path]:
        """Bring an install that is there up to date (a copy predating this
        package serves stale ops); never install one that is not. The plugin
        folder, or ``None`` when nothing is installed or it is current."""
        return PluginInstaller.refresh(PLUGIN_SPEC)

    @staticmethod
    def launch_root() -> Path:
        """A Painter plugin root holding the plugin as a startup module
        (:data:`LAUNCHED_NAME`), for a Painter a bridge launches
        (``SUBSTANCE_PAINTER_PLUGINS_PATH``): the cache's ``substance_rpc/launch``,
        refreshed when the source changed. A copy left there under the plugin's
        own name (before :data:`LAUNCHED_NAME`) goes: it would serve beside it."""
        root = ptk.UserConfig.cache_dir(PLUGIN_NAME, "launch", create=True)
        stale = root / "startup" / PLUGIN_NAME
        if stale.exists():
            PluginInstaller.uninstall_plugin(stale)
        PluginInstaller.install_plugin(
            plugin_src=_InstallerInternal._plugin_source_dir(),
            dest=root / "startup" / LAUNCHED_NAME,
        )
        return root

    @staticmethod
    def launch_env(env: Optional[dict] = None) -> dict:
        """*env* (else this process's) with :meth:`launch_root` first on
        ``SUBSTANCE_PAINTER_PLUGINS_PATH``. A set ``SUBSTANCE_PAINTER_STARTUP_PLUGINS``
        replaces Painter's whole startup list, so it gains the plugin, first (ahead
        of Painter's Python menu, which starts a person's ticked install)."""
        env = dict(os.environ if env is None else env)
        root = str(Installer.launch_root())
        existing = env.get("SUBSTANCE_PAINTER_PLUGINS_PATH", "")
        env["SUBSTANCE_PAINTER_PLUGINS_PATH"] = f"{root}{os.pathsep}{existing}" if existing else root
        startup = env.get("SUBSTANCE_PAINTER_STARTUP_PLUGINS")
        if startup is not None:  # Painter splits it on ":" (not os.pathsep)
            names = [name for name in startup.split(":") if name and name != LAUNCHED_NAME]
            env["SUBSTANCE_PAINTER_STARTUP_PLUGINS"] = ":".join([LAUNCHED_NAME] + names)
        return env

    @staticmethod
    def painter_settings():
        """Painter's own QSettings store, or ``None`` without Qt."""
        try:
            from qtpy import QtCore
        except Exception:
            return None
        return QtCore.QSettings(*PAINTER_SETTINGS)

    @staticmethod
    def enable_launch_at_start() -> bool:
        """Turn on Painter's switch to start the plugin at launch, unless the
        user has set it either way: without it a fresh install's first send
        found no server (Painter starts a ``plugins/`` module only when the
        switch reads ``on``). Returns True when this call set it."""
        settings = Installer.painter_settings()
        if settings is None or settings.contains(LAUNCH_AT_START_KEY):
            return False
        settings.setValue(LAUNCH_AT_START_KEY, "on")
        settings.sync()
        return True

    @staticmethod
    def uninstall() -> bool:
        """Remove the plugin from the user plugin folder.

        Returns True if something was removed.
        """
        return PluginInstaller.uninstall(PLUGIN_SPEC)


#: The plugin in Painter, as the shared installer (``pythontk.PluginInstaller``)
#: and every panel's *Install in Painter* take it.
PLUGIN_SPEC = RpcPluginSpec(
    name=PLUGIN_NAME,
    app="Painter",
    source=_InstallerInternal._plugin_source_dir(),
    # Read at call time, so ``SUBSTANCE_PAINTER_PLUGINS_PATH`` (and a test's
    # patch) is where it looks.
    user_dir=lambda: Installer.user_plugin_dir(),
    enable=lambda: Installer.enable_launch_at_start(),
    after_install=(
        "In an open Painter, use Python > Reload Plugins Folder; it is in Painter's "
        "Python menu, ticked (untick it there to switch it off)."
    ),
)
