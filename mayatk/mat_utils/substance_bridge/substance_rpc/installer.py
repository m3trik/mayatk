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
**Python ▸ Reload Plugins Folder** in a running Painter). If Painter
doesn't auto-enable it, tick ``substance_rpc`` once in the **Python**
menu -- Painter remembers the choice.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from pythontk.net_utils.rpc.installer import PluginInstaller


PLUGIN_NAME = "substance_rpc"


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
        resolves to where Painter actually looks. Fallback (and
        non-Windows): ``~/Documents``.
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
        fallback = Path(os.path.expanduser("~")) / "Documents"
        return fallback if fallback.is_dir() else None


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
        dest_root = Installer.user_plugin_dir()
        if not dest_root:
            return False
        return PluginInstaller.is_plugin_installed(dest_root / PLUGIN_NAME)

    @staticmethod
    def is_current() -> bool:
        """True if the installed plugin matches the one this package ships.

        A copytree install (any machine without Developer Mode) is a
        snapshot, so an install from an older mayatk can be missing ops the
        bridge now dispatches -- which fails as an unknown-op error at
        invoke time, i.e. the knob silently doing nothing.
        """
        dest_root = Installer.user_plugin_dir()
        if not dest_root:
            return False
        return PluginInstaller.is_plugin_current(
            _InstallerInternal._plugin_source_dir(), dest_root / PLUGIN_NAME
        )

    @staticmethod
    def install(force: bool = False) -> Optional[Path]:
        """Install the plugin into Painter's user plugin folder.

        Returns the final plugin directory (or *None* if no destination
        could be resolved). An install that already matches the shipped
        source is left alone; one that has drifted is rebuilt. *force*
        rebuilds even a matching install.
        """
        dest_root = Installer.user_plugin_dir()
        if not dest_root:
            return None
        return PluginInstaller.install_plugin(
            plugin_src=_InstallerInternal._plugin_source_dir(),
            dest=dest_root / PLUGIN_NAME,
            force=force,
        )

    @staticmethod
    def uninstall() -> bool:
        """Remove the plugin from the user plugin folder.

        Returns True if something was removed.
        """
        dest_root = Installer.user_plugin_dir()
        if not dest_root:
            return False
        return PluginInstaller.uninstall_plugin(dest_root / PLUGIN_NAME)
