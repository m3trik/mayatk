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
and a row in pythontk's table. Nothing in the transport changes -- neither the
FBX curve-proxy transport nor the Blender hand-off, which reads this table
through ``RenderEffects.channel_records`` to carry the channel across.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from pythontk import ColorStops


@dataclass(frozen=True)
class ChannelSpec:
    """One per-object render-effect channel."""

    name: str
    preset: str
    default: float
    drives_presence: bool
    color_stops: Optional[ColorStops] = None
    #: Suffix the retired viewport material mode gave its duplicates
    #: (``X_Fade``); kept so ``OpacityMaterialMode.remove`` can heal a
    #: scene saved with that preview on. New code never creates one.
    material_suffix: str = ""

    @property
    def color_attr(self) -> Optional[str]:
        """Deprecated read-through of the HIGH stop's attribute.

        Kept for one release. A caller that wants every stop wants
        ``color_stops.keys``; one that wants a specific end wants
        :meth:`stop_attr`.
        """
        return self.color_stops.hi if self.color_stops else None

    def stop_attr(self, stop: str = "hi") -> Optional[str]:
        """The Maya attribute holding one end of this channel's colour ramp.

        Parameters:
            stop: ``"hi"`` (the colour at a sample of 1.0) or ``"lo"`` (at 0.0).

        Returns:
            The attribute name, or ``None`` when this channel has no colour --
            or no such end, which is how a one-stop channel answers ``"lo"``.

        Raises:
            ValueError: For a name that is neither end.
        """
        if stop not in ("hi", "lo"):
            raise ValueError(f"Unknown colour stop {stop!r}; expected 'hi' or 'lo'.")
        if self.color_stops is None:
            return None
        return self.color_stops.hi if stop == "hi" else self.color_stops.lo

    @property
    def track_color_stops(self) -> Optional[ColorStops]:
        """The published ``visibility_tracks`` keys, one per stop.

        The Maya attributes and the published keys are two namespaces for one
        concept -- ``highlightColorDim`` here, ``highlight_color_dim`` in the
        glTF carrier -- joined by NAME rather than by a shared constant, which
        is the same join the two channel tables already rest on. The drift that
        buys is guarded by a test asserting these equal pythontk's own stop
        keys, because a convention held in two files and nothing else is one
        rename away from publishing a key no reader looks for.
        """
        if self.color_stops is None:
            return None
        return ColorStops(
            f"{self.name}_color",
            f"{self.name}_color_dim" if self.color_stops.lo else None,
        )

    @property
    def track_color_key(self) -> Optional[str]:
        """Deprecated read-through of the HIGH stop's published key."""
        stops = self.track_color_stops
        return stops.hi if stops else None

    @property
    def attrs(self) -> Tuple[str, ...]:
        """The transform attributes this channel owns: the keyable channel and,
        for a coloured one, the three leaves of its colour compound -- every
        plug an anim curve of this channel can land on (what the shot system
        reads as content beside the transform channels)."""
        leaves = (
            tuple(f"{attr}{c}" for attr in self.color_stops.keys for c in "RGB")
            if self.color_stops
            else ()
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
        color_stops=ColorStops("highlightColor", "highlightColorDim"),
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
