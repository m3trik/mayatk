# !/usr/bin/python
# coding=utf-8
"""Channels — the Maya attribute engine behind tentacle's shared Channels panel
(``slots/tools/channels.py``)."""

from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(globals(), {"_channels": "Channels"})
