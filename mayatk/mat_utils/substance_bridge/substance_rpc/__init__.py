# !/usr/bin/python
# coding=utf-8
"""Substance Painter RPC -- client, installer, and Painter-side plugin.

Lives inside :mod:`mayatk.mat_utils.substance_bridge` as its
"talk to a running Painter" subset. Mirrors the layout of
:mod:`mayatk.mat_utils.marmoset_bridge.marmoset_rpc`:

* The parent :mod:`substance_bridge` -- file-based handoff. Exports
  selection to FBX and launches a templated Painter session. Safe by
  default; never reaches into a live session uninvited.
* :mod:`substance_bridge.substance_rpc` (this module) -- targets a
  Painter that is *already running* with the ``substance_rpc`` Python
  plugin loaded (``plugin_src/substance_rpc``, installed into Painter's
  user plugin folder by :class:`Installer`; the bridge installs it
  automatically on send).

Stock Painter binds no RPC port of its own (``--enable-remote-scripting``
is a no-op; verified 2026-05-18) -- the plugin is what makes the
``reimport`` / ``render`` / ``bake_lighting`` templates dispatchable.

**Vendored twin -- keep code-identical.** This whole subpackage (client,
installer, README and ``plugin_src/``) is duplicated under
``mayatk/mat_utils/substance_bridge/substance_rpc/`` (the SSoT: edit it
there) and ``blendertk/mat_utils/substance_bridge/substance_rpc/``; mirror
every change into both. Drift -- a changed file, or one present on one
side only -- fails ``extapps/test/test_vendor_sync.py``. The plugin's
``_rpc_core.py`` is in turn a staged copy of
``pythontk.net_utils.rpc.plugin_core`` (``m3trik/scripts/sync_rpc_core.py``).
"""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(
    globals(),
    {"client": ("PainterRpcClient", "DEFAULT_RPC_PORT"), "installer": "Installer"},
)
