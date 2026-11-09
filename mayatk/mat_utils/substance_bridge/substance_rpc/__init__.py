# !/usr/bin/python
# coding=utf-8
"""Substance Painter RPC -- client, installer, and Painter-side plugin.

Lives inside the Substance bridge package (``substance_bridge``) as its
"talk to a running Painter" subset. Mirrors the layout of
the Marmoset bridge's ``marmoset_rpc``:

* The parent :mod:`substance_bridge` -- file-based handoff. Exports
  selection to FBX and launches a templated Painter session. Safe by
  default; never reaches into a live session uninvited.
* :mod:`substance_bridge.substance_rpc` (this module) -- reaches a
  Painter running the ``substance_rpc`` Python plugin
  (``plugin_src/substance_rpc``): one the bridge launched (it loads the
  plugin from :meth:`Installer.launch_root`), or the person's own once
  they installed it (*Install in Painter*, :class:`Installer`) and ticked
  it in Painter's Python menu. Each serving Painter is found by its
  instance record (:meth:`PainterRpcClient.painters`).

Stock Painter binds no RPC port of its own (``--enable-remote-scripting``
is a no-op; verified 2026-05-18) -- the plugin is what runs a script in
Painter (``script.start``).

**Vendored copy -- keep byte-identical.** This whole subpackage (client, installer, README and ``plugin_src/``) ships in each host
library's Substance bridge and in the standalone Substance Scripts app; the
Maya library's copy is the source (edit it there, then mirror it). Drift
fails the vendor guard (``test_vendor_sync.py``). The plugin's
``_rpc_core.py`` is in turn a staged copy of
``pythontk.net_utils.rpc.plugin_core`` (``m3trik/scripts/sync_rpc_core.py``).
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {"client": "PainterRpcClient", "installer": "Installer"},
)
