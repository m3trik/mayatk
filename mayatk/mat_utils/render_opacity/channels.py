# !/usr/bin/python
# coding=utf-8
"""The per-object render-effect channel table.

One row per effect a transform can carry as a keyable float: its attribute
(created from an ``Attributes`` YAML preset), whether it drives *presence*
(mirrors to ``visibility`` and gates the GLB), an optional sibling colour
attribute, and how each shader type shows it live in the viewport. The glTF
half of the table -- which material property the ramp lands on -- lives in
``pythontk.file_utils.mesh_convert.glb_fades.CHANNELS``, joined by name, so
the two packages cannot each describe the same channel differently.

Adding an effect is adding a row here, a YAML preset beside ``opacity.yaml``,
and a row in pythontk's table. Nothing in the transport changes.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ViewportBinding:
    """How one shader type shows a channel live.

    Attributes:
        plug: Material attribute the channel's float drives.
        fan_out: The plug is a colour; drive its R, G and B from the float.
        color_plug: Material colour attribute the channel's colour attr feeds.
        graph: StingrayPBS only -- the ShaderFX graph mode the plug needs.
        toggle: StingrayPBS only -- a ``use_*`` switch that must be on (1) or
            off (0) for the plug to take effect, as ``(attr, value)``.
    """

    plug: str
    fan_out: bool = False
    color_plug: Optional[str] = None
    graph: Optional[str] = None
    toggle: Optional[Tuple[str, float]] = None


@dataclass(frozen=True)
class ChannelSpec:
    """One per-object render-effect channel."""

    name: str
    preset: str
    default: float
    drives_presence: bool
    color_attr: Optional[str] = None
    material_suffix: str = ""
    viewport: Dict[str, ViewportBinding] = field(default_factory=dict)

    @property
    def track_color_key(self) -> Optional[str]:
        """The ``visibility_tracks`` sibling key carrying this channel's colour."""
        return f"{self.name}_color" if self.color_attr else None


CHANNELS: Dict[str, ChannelSpec] = {
    "opacity": ChannelSpec(
        name="opacity",
        preset="opacity",
        default=1.0,
        drives_presence=True,
        material_suffix="_Fade",
        viewport={
            "StingrayPBS": ViewportBinding(
                "opacity", graph="transparent", toggle=("use_opacity_map", 1.0)
            ),
            "standardSurface": ViewportBinding("opacity", fan_out=True),
            "aiStandardSurface": ViewportBinding("opacity", fan_out=True),
        },
    ),
    "highlight": ChannelSpec(
        name="highlight",
        preset="highlight",
        default=0.0,
        drives_presence=False,
        color_attr="highlightColor",
        material_suffix="_Highlight",
        viewport={
            # The colour is a uniform; the intensity drives the native weight.
            # ``use_emissive_map`` off so the uniform, not a map, is what shows.
            "StingrayPBS": ViewportBinding(
                "emissive_intensity",
                color_plug="emissive",
                toggle=("use_emissive_map", 0.0),
            ),
            "standardSurface": ViewportBinding("emission", color_plug="emissionColor"),
            "aiStandardSurface": ViewportBinding(
                "emission", color_plug="emissionColor"
            ),
        },
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
