# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shot Manifest panel: the ``.ui``'s widgets routed to
:class:`~.shot_manifest_controller.ShotManifestController`, which holds the
panel's logic (one module per job; see its docstring).

Shared text: blendertk carries this module identical
(``m3trik/scripts/check_dcc_twins.py``); mayatk's is the one edited, then
copied over.  Only ``manifest_host.py`` differs between the two.
"""

import pythontk as ptk

from .shot_manifest_controller import ShotManifestController


class ShotManifestSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shot_manifest

        self.controller = ShotManifestController(self)

    # ---- header ----------------------------------------------------------

    def header_init(self, widget):
        """Header menu is configured once in controller.__init__."""
        pass

    def btn_expand_missing(self):
        """Expand all step rows that have missing objects or behaviors."""
        self.controller.expand_missing()

    def btn_expand_extra(self):
        """Expand all step rows that have scene-discovered extra objects."""
        self.controller.expand_extra()

    def btn_settings(self):
        """Open the shared shots settings panel."""
        self.sb.handlers.marking_menu.show("shots")

    # ---- buttons ---------------------------------------------------------

    def b002(self):
        """Assess the steps against the scene."""
        self.controller.assess()

    def b003(self):
        """Build shots from loaded steps (or auto-detect from scene)."""
        self.controller.build()

    def b004(self):
        """Export the manifest's rows (the option menu says how)."""
        self.controller.export()
