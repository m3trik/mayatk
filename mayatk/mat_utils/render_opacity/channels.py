# !/usr/bin/python
# coding=utf-8
"""The per-object render-effect channel table.

One row per effect a transform can carry as a keyable float: its attribute
(created from an ``Attributes`` YAML preset), whether it drives *presence*
(mirrors to ``visibility`` and gates the GLB) and an optional sibling colour
attribute. Nothing here shows the channel in the viewport: lookdev is the
WebXR push, which shows the deliverable itself (see ``material_mode.py`` for
why the in-scene preview was retired). The glTF
half of the table -- which material property the ramp lands on -- lives in
``pythontk.file_utils.mesh_convert.glb_fades.CHANNELS``, joined by name, so
the two packages cannot each describe the same channel differently.

Adding an effect is adding a row here, a YAML preset beside ``opacity.yaml``,
and a row in pythontk's table. Nothing in the transport changes.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ChannelSpec:
    """One per-object render-effect channel."""

    name: str
    preset: str
    default: float
    drives_presence: bool
    color_attr: Optional[str] = None
    #: Suffix the retired viewport material mode gave its duplicates
    #: (``X_Fade``); kept so ``OpacityMaterialMode.remove`` can heal a
    #: scene saved with that preview on. New code never creates one.
    material_suffix: str = ""

    @property
    def track_color_key(self) -> Optional[str]:
        """The ``visibility_tracks`` sibling key carrying this channel's colour."""
        return f"{self.name}_color" if self.color_attr else None

    @property
    def attrs(self) -> Tuple[str, ...]:
        """The transform attributes this channel owns: the keyable channel and,
        for a coloured one, the three leaves of its colour compound -- every
        plug an anim curve of this channel can land on (what the shot system
        reads as content beside the transform channels)."""
        leaves = (
            tuple(f"{self.color_attr}{c}" for c in "RGB") if self.color_attr else ()
        )
        return (self.name,) + leaves


CHANNELS: Dict[str, ChannelSpec] = {
    "opacity": ChannelSpec(
        name="opacity",
        preset="opacity",
        default=1.0,
        drives_presence=True,
        material_suffix="_Fade",
    ),
    "highlight": ChannelSpec(
        name="highlight",
        preset="highlight",
        default=0.0,
        drives_presence=False,
        color_attr="highlightColor",
        material_suffix="_Highlight",
    ),
}

OPACITY = CHANNELS["opacity"]
HIGHLIGHT = CHANNELS["highlight"]

#: The one channel that gates presence (mirrors to ``visibility``).
PRESENCE = next(spec for spec in CHANNELS.values() if spec.drives_presence)


def spec_for(channel) -> ChannelSpec:
    """Resolve a name or spec to a :class:`ChannelSpec`.

    Raises:
        KeyError: For a name not in :data:`CHANNELS`.
    """
    if isinstance(channel, ChannelSpec):
        return channel
    if channel not in CHANNELS:
        raise KeyError(
            f"Unknown render-effect channel {channel!r}. Known: {', '.join(CHANNELS)}."
        )
    return CHANNELS[channel]
