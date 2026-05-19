# !/usr/bin/python
# coding=utf-8
"""Install the marmoset_rpc plugin into Toolbag's user plugin folder.

Toolbag doesn't expose an env-var for plugin discovery the way Substance
Painter does, so we drop the plugin source into the user plugin folder
at ``%LOCALAPPDATA%\\Marmoset Toolbag <N>\\plugins\\marmoset_rpc`` and
let Toolbag find it on next launch.

The Toolbag-version-aware destination resolution is the DCC-specific bit
this module owns. The actual install strategy (symlink-first, copytree
fallback, ``__pycache__`` filtering, idempotent) lives generically in
:mod:`pythontk.net_utils.rpc.installer`.
"""
import os
import re
from pathlib import Path
from typing import Optional

from pythontk.net_utils.rpc.installer import (
    install_plugin,
    uninstall_plugin,
    is_plugin_installed,
)


# Match ``Toolbag <N>`` in either install-path layout. Mirrors
# _marmoset_bridge._TOOLBAG_VERSION_RE -- duplicated rather than imported
# so this module has no dependency on the template-based bridge.
_TOOLBAG_VERSION_RE = re.compile(r"Toolbag\s+(\d+)", re.IGNORECASE)


def _plugin_source_dir() -> Path:
    """Return the on-disk path to the plugin source (``plugin_src/marmoset_rpc``)."""
    return Path(__file__).resolve().parent / "plugin_src" / "marmoset_rpc"


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


def is_installed(toolbag_exe: Optional[str] = None) -> bool:
    """True if the plugin is present at the resolved user plugin dir."""
    dest_root = user_plugin_dir(toolbag_exe)
    if not dest_root:
        return False
    return is_plugin_installed(dest_root / "marmoset_rpc")


def install(
    toolbag_exe: Optional[str] = None, force: bool = False
) -> Optional[Path]:
    """Install the plugin into Toolbag's user plugin folder.

    Returns the final plugin directory (or *None* if no Toolbag install
    could be located). Idempotent: a present install is left alone unless
    *force* is true.
    """
    dest_root = user_plugin_dir(toolbag_exe)
    if not dest_root:
        return None
    return install_plugin(
        plugin_src=_plugin_source_dir(),
        dest=dest_root / "marmoset_rpc",
        force=force,
    )


def uninstall(toolbag_exe: Optional[str] = None) -> bool:
    """Remove the plugin from the user plugin folder.

    Returns True if something was removed.
    """
    dest_root = user_plugin_dir(toolbag_exe)
    if not dest_root:
        return False
    return uninstall_plugin(dest_root / "marmoset_rpc")
