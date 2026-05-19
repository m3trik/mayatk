# !/usr/bin/python
# coding=utf-8
"""Substance Painter bridge templates.

Each ``*.py`` sibling is a template descriptor consumed by
:class:`mayatk.mat_utils.substance_bridge.SubstanceBridge`. Templates declare:

* ``BRIDGE_MODES`` -- supported modes (``"send_to"``, ``"roundtrip"``).
* ``LAUNCH_ARGS`` -- list of Painter command-line args (with ``__KEY__``
  placeholders substituted from the rendered context).
* ``RPC_SCRIPT`` -- JavaScript body sent via Painter's
  ``--enable-remote-scripting`` JSON-RPC endpoint after launch
  (empty string = no RPC step).
* ``BUILD_MANIFEST`` -- if True, a :class:`MatManifest` JSON is written
  next to the FBX and its path is exposed as ``__MANIFEST_PATH__``.

Templates are parsed via :mod:`ast` (literals only, no execution), so the
files can contain placeholder tokens that would otherwise be syntax errors
inside a JS string.
"""
