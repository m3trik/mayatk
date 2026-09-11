# mayatk — API Changes

_Diff vs the last release (origin/main @ 7318df3)._

## Added (6)

- `anim_utils/shots/shot_sequencer/clip_motion.py::ClipMotionMixin.on_clips_batch_resized(self, resizes) -> None`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.get_color(cls, obj, spec: ChannelSpec = HIGHLIGHT) -> Optional[Tuple[float, float, float]]`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.set_color(cls, objects, color, spec: ChannelSpec = HIGHLIGHT) -> List[str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.channel_colors(cls, objects=None, channel='highlight') -> Dict[str, Tuple]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.objects_with_channel(cls, channel='highlight') -> List[str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.set_channel_color(cls, objects=None, color=None, channel='highlight') -> List[str]`
