# !/usr/bin/python
# coding=utf-8
"""Install the marmoset_rpc plugin into Toolbag's user plugin folder.

Toolbag doesn't expose an env-var for plugin discovery the way Substance
Painter does, so we drop the plugin source into the user plugin folder
at ``%LOCALAPPDATA%\\Marmoset Toolbag <N>\\plugins\\marmoset_rpc``. Toolbag
lists it in its **Plugins** menu (a folder plugin's ``__main__.py``) and runs
it only when a person picks it there: that is what makes the Toolbag they
opened serve. A Toolbag a bridge launches runs the package's own copy instead
(``MarmosetConnection.LAUNCH_ARGS``).

The Toolbag-version-aware destination resolution is the DCC-specific bit
this module owns (:data:`PLUGIN_SPEC`, what every panel's *Install in
Toolbag* takes). The install itself (symlink-first, copytree fallback,
``__pycache__`` filtering, idempotent) is the shared
:class:`pythontk.PluginInstaller`'s.
"""

import dataclasses
import os
import re
from pathlib import Path
from typing import Optional

from pythontk.net_utils.rpc.installer import PluginInstaller, RpcPluginSpec


# Match ``Toolbag <N>`` in either install-path layout. Mirrors
# _marmoset_bridge._TOOLBAG_VERSION_RE -- duplicated rather than imported
# so this module has no dependency on the template-based bridge.
_TOOLBAG_VERSION_RE = re.compile(r"Toolbag\s+(\d+)", re.IGNORECASE)


class _InstallerInternal(object):
    """Internal helpers for Installer."""

    @staticmethod
    def _plugin_source_dir() -> Path:
        """Return the on-disk path to the plugin source (``plugin_src/marmoset_rpc``)."""
        return Path(__file__).resolve().parent / "plugin_src" / "marmoset_rpc"


class Installer(_InstallerInternal):
    """Installer — module namespace."""

    @staticmethod
    def user_plugin_dir(toolbag_exe: Optional[str] = None) -> Optional[Path]:
        """Resolve ``%LOCALAPPDATA%\\Marmoset Toolbag <N>\\plugins``.

        Tier 1: parse the version from *toolbag_exe* and use that.
        Tier 2: scan ``%LOCALAPPDATA%`` for any ``Marmoset Toolbag *`` dir
                (newest by mtime wins, like the log resolver).
        Tier 3: ``None`` -- caller has to install manually.
        """
        local_app = os.environ.get("LOCALAPPDATA")
        if not local_app:
            return None
        base = Path(local_app)

        if isinstance(toolbag_exe, str) and toolbag_exe:
            m = _TOOLBAG_VERSION_RE.search(toolbag_exe)
            if m:
                return base / f"Marmoset Toolbag {m.group(1)}" / "plugins"

        if base.is_dir():
            newest: Optional[Path] = None
            newest_mtime = -1.0
            for sub in base.glob("Marmoset Toolbag *"):
                if sub.is_dir():
                    mt = sub.stat().st_mtime
                    if mt > newest_mtime:
                        newest_mtime = mt
                        newest = sub
            if newest is not None:
                return newest / "plugins"

        return None

    @staticmethod
    def spec(toolbag_exe: Optional[str] = None) -> "RpcPluginSpec":
        """:data:`PLUGIN_SPEC` for the Toolbag *toolbag_exe* names (its version's
        folder); without one, the newest Toolbag's."""
        if not toolbag_exe:
            return PLUGIN_SPEC
        return dataclasses.replace(PLUGIN_SPEC, user_dir=lambda: Installer.user_plugin_dir(toolbag_exe))

    @staticmethod
    def is_installed(toolbag_exe: Optional[str] = None) -> bool:
        """True if the plugin is present at the resolved user plugin dir."""
        return PluginInstaller.status(Installer.spec(toolbag_exe)) in ("current", "stale")

    @staticmethod
    def install(
        toolbag_exe: Optional[str] = None, force: bool = False
    ) -> Optional[Path]:
        """Install the plugin into Toolbag's user plugin folder.

        Returns the final plugin directory (or *None* if no Toolbag install
        could be located). Idempotent: a present install is left alone unless
        *force* is true.
        """
        spec = Installer.spec(toolbag_exe)
        dest = spec.destination()
        return None if dest is None else PluginInstaller.install_plugin(spec.source, dest, force=force)

    @staticmethod
    def uninstall(toolbag_exe: Optional[str] = None) -> bool:
        """Remove the plugin from the user plugin folder.

        Returns True if something was removed.
        """
        return PluginInstaller.uninstall(Installer.spec(toolbag_exe))


#: The plugin in Toolbag, as the shared installer (``pythontk.PluginInstaller``)
#: and every panel's *Install in Toolbag* take it (the newest Toolbag's folder).
PLUGIN_SPEC = RpcPluginSpec(
    name="marmoset_rpc",
    app="Toolbag",
    source=_InstallerInternal._plugin_source_dir(),
    user_dir=lambda: Installer.user_plugin_dir(),  # at call time: LOCALAPPDATA as it is then
    after_install="It is in Toolbag's Plugins menu (restart an open Toolbag to list it): pick it there to serve.",
)
