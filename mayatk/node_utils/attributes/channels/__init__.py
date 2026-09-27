# !/usr/bin/python
# coding=utf-8
"""Channels — Switchboard UI for inspecting and editing Maya attributes."""

import pythontk as ptk
from pythontk.core_utils.module_resolver import lazy_exports

lazy_exports(globals(), {"_channels": "Channels", "channels_slots": "ChannelsSlots"})

ptk.Deprecation.attributes(
    globals(),
    {
        "launch": "mayatk.node_utils.attributes.channels.channels_slots.ChannelsSlots.launch"
    },
    remove_in="0.22.0",
    since="2026-09-26",
)
