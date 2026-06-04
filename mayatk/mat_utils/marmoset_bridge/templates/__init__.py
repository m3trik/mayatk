# !/usr/bin/python
# coding=utf-8
"""Marmoset Toolbag bridge templates.

Each ``*.py`` sibling is a Toolbag-side script with ``__KEY__`` placeholders,
rendered by :class:`mayatk.mat_utils.marmoset_bridge.MarmosetEngine` (token
substitution via ``StrUtils.replace_delimited``) and run inside Toolbag via
``toolbag.exe -run``. Each declares its supported modes in a ``BRIDGE_MODES``
tuple (``"send_to"``, ``"roundtrip"``).

The rendered script picks up the shared Toolbag-side helpers
(:mod:`.._toolbag_helpers`) via a ``sys.path`` insert of the package
directory, substituted in as ``__TOOLBAG_HELPERS_DIR__``.

This ``__init__`` only marks ``templates/`` as a package so the scripts ship
in a wheel build; :func:`MarmosetEngine.list_templates` skips it (underscore-
prefixed stems are not user-visible templates).
"""
