# !/usr/bin/python
# coding=utf-8
"""Attribute Manager — Switchboard UI for inspecting and editing Maya attributes."""
from mayatk.node_utils.attributes.attribute_manager._attribute_manager import (
    AttributeManager,
)
from mayatk.node_utils.attributes.attribute_manager.attribute_manager_slots import (
    AttributeManagerSlots,
)

__all__ = ["AttributeManager", "AttributeManagerSlots", "launch"]


def launch(sb=None, targets=None, filter=None, search=None):
    """Open the Attribute Manager, optionally pre-targeted.

    Parameters
    ----------
    sb : Switchboard | None
        The caller's switchboard (typically ``self.sb`` from a sibling
        slots context).  When given, the UI is shown via tentacle's
        ``marking_menu`` handler so it integrates with the existing
        UI registry.  When ``None``, a standalone Switchboard is created
        (useful for ``__main__`` testing only — inside tentacle, always
        pass ``sb``).
    targets : list[str] | None
        Node names to pin.  ``None`` clears any existing pin.
    filter : str | None
        :attr:`AttributeManager.FILTER_MAP` key to select on open.
    search : str | None
        Text-filter pattern to pre-populate the search field.  Pass
        ``""`` to clear.
    """
    if sb is None:
        from uitk import Switchboard

        sb = Switchboard(
            ui_source="attribute_manager.ui", slot_source=AttributeManagerSlots
        )
        ui = sb.loaded_ui.attribute_manager
        ui.show(pos="screen")
    else:
        ui = sb.handlers.marking_menu.show("attribute_manager")

    slots = sb.get_slots_instance(ui)
    if slots is not None:
        slots.apply_launch_config(targets=targets, filter=filter, search=search)
    return ui
