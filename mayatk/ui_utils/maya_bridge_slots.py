# !/usr/bin/python
# coding=utf-8
"""Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.

The DCC-agnostic base lives upstream in :mod:`uitk.bridge.slots`
(re-exported through :mod:`uitk.bridge`). This thin subclass injects
the one piece every Maya bridge needs: a sensible Output Dir fallback
sourced from :class:`mayatk.env_utils.EnvUtils` (scene dir, then
workspace) when the user leaves the field blank.

Marmoset, Substance, and Rizom slots all subclass this instead of
inheriting from ``BridgeSlotsBase`` directly, so the fallback lives
in one place.
"""
from __future__ import annotations

from uitk.bridge import BridgeSlotsBase

from mayatk.env_utils._env_utils import EnvUtils


class MayaBridgeSlotsBase(BridgeSlotsBase):
    """Adds a Maya-flavored ``default_output_dir`` to :class:`BridgeSlotsBase`."""

    def default_output_dir(self) -> str:
        """Scene-dir then workspace fallback for an empty Output Dir field."""
        return EnvUtils.default_artifact_dir()
