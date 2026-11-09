# !/usr/bin/python
# coding=utf-8
"""Substance Painter bridge scripts: the built-in ones, run inside Painter.

Each ``*.py`` sibling is a Python script the ``substance_rpc`` plugin runs in
Painter (``script.start``), its values in ``args``; the person's own live in
``ptk.ScriptTemplate.user_dir("substance")`` and shadow a built-in of their
name. The header the engine reads without running it (``ast``):

* the docstring's first line -- the description the panel shows;
* ``WORKS_ON`` -- ``"new"`` / ``"mesh"`` / ``"project"``: what it runs on,
  which decides the Painter (``SubstanceEngine.pick_painter``);
* ``# Rows: __KEY__ ...`` -- the panel rows it reads; ``# Row: __KEY__ kind
  default "Label" ...`` -- a row of its own (``ScriptTemplate.declared_rows``);
* ``BUILD_MANIFEST``, ``REUSE_RECORDED_EXPORT``, ``FBX_OPTIONS``,
  ``BRIDGE_MODES`` -- the host export's options.

**Vendored copy -- keep byte-identical.** The ``templates/`` folder ships in each host
library's Substance bridge and in the standalone Substance Scripts app; the
Maya library's copy is the source (edit it there, then mirror it). Drift
fails the vendor guard (``test_vendor_sync.py``). A new script goes into every copy.
"""
