# Emissive groups — regions whose glow plays on its own, as materials

A group is materials, authored at the source: its faces wear a copy of their
material made for the group, sharing the base's maps, and the copy's emission
is keyed by the group's weight (0 to 1), so the viewport shows what ships. In
the delivery each copy is a plain material and each animated one a
`render_effects` material entry, its weight a curve on the animated-value
transport. This doc is the cross-package reference: the model, the scene
data, the delivery and what each package owns.

The API is each library's `EmissiveGroups` (mayatk / blendertk
`mat_utils/emissive_groups.py`): pythontk's host-free `EmissiveGroupsModel`
(`pythontk/core_utils/engines/textures/emissive_groups.py`) over the host's
half (`MayaEmissiveGroups`, `BlenderEmissiveGroups`, each built on
`ptk.PureEmissiveGroupsHost`). The panel is tentacle's
(`ui/tools/emissive_groups.ui` + `slots/tools/emissive_groups.py`, one Slots
class for both hosts, reaching the API through `self.tk`).

## The model

- **One copy per material and group**, named `<base>_<group>` (the host's
  unique spelling if that name is taken). A face takes the group's copy of its
  material; a face already in another group moves, and a copy no face wears
  any more is deleted. A face is in one group (it wears one material), and a
  region is whole faces: a region inside a face is made by cutting the face at
  the source.
- **The weight is the copy's own emission.** The copy's emission scalar is its
  base's times the weight: Maya's Standard Surface / aiStandardSurface
  `emission`, openPBR `emissionLuminance`, StingrayPBS `emissive_intensity`
  (`ShaderAttributeMap.EMISSION_WEIGHT_ATTRS`); Blender's Principled BSDF
  `Emission Strength`. A Lambert, Blinn
  or Phong glows by its `incandescence` colour: its copy's incandescence is the
  base's times the weight, keyed per channel, against the base's colour it
  keeps (`emissiveGroupColour`). Refused by name, before anything changes: a
  base with no emission at all (an emission weight of 0, a black emission
  colour or incandescence), a mapped incandescence (no weight can scale it),
  a Blender strength a link drives, a shader with no emission (a Diffuse
  BSDF) and an unlit one (an Emission shader, which the delivery states
  unlit, so a weight on it plays nowhere). Weight 1 is the base's own glow.
- **Keyed or resting.** `key_weight` keys every copy of the group (its
  strength times the weight); a keyed copy is the animation's, so `set_weight`
  leaves it and rests only the un-keyed ones. A copy that joins a keyed group
  plays its curve.
- **Tint and states.** A tint multiplies a copy's emission colour (white: no
  tint): emission is colour x tint x weight x strength. A state is a named
  tint and weight (`DEFAULT_STATES`: an indicator's *off*, *green*, *blue*,
  *amber*, *red*; `set_states` stores a group's own, as data). `set_tint` /
  `key_tint` rest or key the tint; `set_state` / `key_state` rest or key a
  state, keyed stepped (a state switches, it never fades); on a group with no
  states of its own they store its defaults in the same undo step, and
  `key_states({group: state})` keys several groups in one. The copy keeps the
  base's colour (`emissiveGroupColour`) and its colour channels are that colour
  times the tint, keyed per channel, so the copy stays in step while tinted
  and a changed base colour re-keys the tint against the new one on `sync`.
  Tintable: an unmapped emission colour on a shader with an emission weight
  (Maya `ShaderAttributeMap.constant_attr`: `emissionColor`, StingrayPBS
  `emissive`; Blender's Principled `Emission Color`). Refused by name: a mapped
  colour, one with a black channel (no tint lights it), and a Lambert, Blinn
  or Phong (its colour is its weight's); such a group's default states are its
  weight's (`WEIGHT_STATES`: *off*, *on*). A white base colour shows each
  state's tint as named.
- **In step with the base.** A copy compares its values and maps with its
  base's (everything but the emission scalar and its tag). A changed base
  reaches its copies when the tool syncs them: `sync()` (the panel's **Sync**),
  and every export runs it as a check: the stager warns which copies are out
  of step and ships them as they stand, never a swap at export. A changed base
  emission strength rescales the copy's emission and keys, its weight kept. A
  copy whose base is gone is reported and kept.
- **One undo step per operation** (Maya: one undo chunk; Blender: one undo
  step), selection in when no faces are given, and every operation takes an
  optional `ctx` (`ptk.OpContext`) it asks before each change.

## Scene data

A copy is tagged on its own data: `emissiveGroup` (the group),
`emissiveGroupBase` (the base) and `emissiveGroupStrength` (the base's
emission strength the copy was keyed against); a tinted copy adds
`emissiveGroupColour` (the base colour its tint multiplies) and one with
states `emissiveGroupStates` (JSON, `{name: {"tint", "weight"}}`).

| Host | Copy | Tags | Keys |
|:---|:---|:---|:---|
| Maya | `duplicate -inputConnections` of the base shader (its maps shared), its own shading group (the base's displacement and volume inputs shared) | attributes on the shader; the base a message connection, so a rename keeps the link | the emission attribute's anim curve |
| Blender | `Material.copy()` (its images shared), its node tree's action made its own | custom properties on the copy's node tree (Blender's FBX exporter writes a material's custom properties into the file, an ID pointer as a memory address); the base an ID pointer | the node tree's action |

Nothing is stored on the scene's carriers. The `emissive_registry` record key
is the hand-off's (version 2): what crosses a bridge for the groups, per copy
its group, base, strength and emission keys (`transfer_out` / `transfer_in`),
since an interchange file carries the materials and their faces but never the
tags or the material animation.

## The delivery

- **The copy is a plain material** in the `materials` record, like any other,
  and in the GLB a material of its own (one primitive per material and group).
- **An animated copy** is a `render_effects` material entry
  (`EmissiveGroupsModel.render_effect_materials`): `{"material", "channels",
  "emission_color", "emission_strength", "tint"?, "states"?}` -- the channels
  its keys animate (`emission`, its weight; `tint_r`, `tint_g`, `tint_b`, its
  tint), the colour its weight scales (white over a map; a colour shader's
  incandescence at strength 1; a tinted copy's base colour), its base's
  strength, the tint the delivered material shows (so a reader recovers its
  resting weight) and its named states. `RenderEffects`'s
  producer publishes it beside its node tracks, and its stager stages the
  copy's weight on a `<material>__emission` proxy, and a keyed tint on
  `<material>__tint_r/g/b` ones, under the export's carrier node, per take
  (`ptk.SceneRecords.TRANSPORT`), so every writer that ships the record ships
  the curves. **A scoped write carries only the copies a face of
  a mesh it ships wears**, its record and its proxies alike: the stager reads
  the write's context (`FbxUtils.write_context`: the bracket's, else the one
  its publish ran under), and a writer that stages before it publishes its
  scoped records (a Scene Exporter, Blender's hand-off) has the staging
  narrowed by the publish (`FbxUtils.HOOKS["staging_scope"]` ->
  `RenderEffects.scope_export`). A copy no face wears is in no file.
- **The GLB** plays it as the clips' `/materials/<i>/emissiveFactor`
  `KHR_animation_pointer` channels -- one per material, its weight and tint
  curves joined on one time line, weight x tint per channel -- a peak above 1
  in `KHR_materials_emissive_strength`; the WebXR viewer and any glTF reader
  play it as a plain material's animated emission.

## The old tool's scenes

Before 2026-11-07 a group was a mask over a slot: an `emissiveGroups` colour
set or an `_EMask` texture, a slot registry on the internal carrier, keyable
weights on the export carrier and the `emissive_groups` record. That record
is retired (`ptk.SceneRecords.RETIRED`; the FBX records check names it with
its fix), and a scene the old tool authored converts in one action:
`EmissiveGroups.convert_legacy()` (the panel's header menu, **Convert Old
Groups**). Face-level groups convert exactly, at their default weight, a
keyed weight becoming the copies' keys; a face in two groups stays in the
lower slot's, and a group that used the mask texture is reported by name with
its fix (cut the faces at the source), never guessed. Then the stores, the
weights, the colour set and both records go.

## Panel

**Materials ▸ Tools ▸ Materials (scene) ▸ Emissive Groups** in tentacle (both
DCCs). A name field and **Add** (an empty name makes the first free
`group_<n>`; the name stays, so a second Add extends that group); a table
(Group, Weight, State, Materials, Faces) whose **Weight** column scrub- and
click-edits like a channel-box field, and whose **State** dropdown rests the
group in a state (a group with none of its own takes the indicator states);
a keyed group's edit or pick keys it at the current frame. **Remove**,
**Select**, **Check**; **Sync**, whose option box offers *Report Only*. The
table's context menu adds *Remove Selected Faces*, *All On* / *All Off* (one
undo step, a keyed group named and left to its curve), *Key Weights @
Current Frame* and *Key States @ Current Frame* (the highlighted group, or
every group with no row highlighted) and *Remove Keys*; the header menu,
*Convert Old Groups*.

## Unity

unitytk's importer reads the `render_effects` material entries through the
one material writer (its old EmissiveGroup feature, which read the retired
record, is deleted): an animated copy plays its weight and tint curves on the
material's emission (`EmissionTrack.weight`, `.tint`), resting at the tint
the record states and the weight the delivered material shows; its states
ride the track, and `EmissionTrack.SetState(name)` switches one at runtime.
