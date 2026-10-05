# Lightmap Baker — Arnold lighting into engine lightmaps

The Lightmap Baker bakes a Maya scene's lighting with Arnold into lightmaps and
wires them for the engine in one step. It does real lightmapping: every mesh
keeps its full PBR material and texture UVs. The lightmap is a separate HDR
texture, sampled on a second UV set and multiplied with the albedo by the
engine (Unity, glTF / the WebXR preview).

The engine is `mtk.LightmapBaker`
([`light_utils/lightmap_baker/lightmap_baker.py`](../mayatk/light_utils/lightmap_baker/lightmap_baker.py)).
It builds on `mtk.TextureBaker`, the generic Arnold bake primitive in
[`mat_utils/texture_baker.py`](../mayatk/mat_utils/texture_baker.py).
What a bake leaves in the scene is `mtk.LightmapRecords`
([`lightmap_records.py`](../mayatk/light_utils/lightmap_baker/lightmap_records.py)):
the markers, the export manifest, and where the maps are on disk, on top of
pythontk's generic `FileDependencies`. The panel (`LightmapBakerSlots` in
`lightmap_baker_slots.py`, with `lightmap_baker.ui`) sits in the same folder.
blendertk ships a Cycles twin; see [Blender twin](#blender-twin).

## Panel

Open it from tentacle's **Lighting ▸ Lightmap Baker**, or with
`marking_menu.show("lightmap_baker")`. From top to bottom:

| Control | What it does |
|:---|:---|
| **Scope** | What to bake: the **Selected** meshes, every **Visible** mesh, or the whole **Scene**, every copy of an instanced mesh included. Its **light** button is *Include Environment*: on, the HDRI skydome lights the bake; off hides the `aiSkyDomeLight` for the bake and restores it afterwards. |
| **Exclude** | *Set From Selection* makes the selection the scene's [Exclude set](#exclude-set): objects that get no map of their own but still cast shadows and bounce light onto everything that bakes. Its icons select the set or clear it. The label shows how many meshes are excluded. |
| **Packing** | **Atlas by Material** (the default): one shared map per material group. **Per-Object**: one map per object at the full Resolution. See [Packing](#packing). Its **camera** button is *Reflection Probe*: on (the default), the bake also captures the room it lit as an HDR, for what a lightmap cannot hold. See [Reflection probe](#reflection-probe). |
| **Processor** | Which one Arnold renders the bake on. **Auto** uses the GPU wherever Arnold has one, else the CPU; **GPU** and **CPU** force it. A machine setting, so it sits above the Quality group and no preset stores it. |
| **Resolution** | Map size in pixels (256–4096). It also sets the gutter width. Its **filter** button is *Denoise*: each map is cleaned at the size it ships — the object's own map, or its atlas cell. |
| **Samples** | Arnold camera (AA) samples per texel, squared. Its button is *Adaptive Sampling*: on a GPU bake the extra samples go only where the map is noisy. It greys out when the Processor is CPU. See [Sampling](#sampling). |
| **GI Samples** | Indirect diffuse samples, squared. See [Sampling](#sampling). |
| **Bounces** | Arnold's GI Diffuse Depth. 0 is direct light only; 2–3 light a room's corners and ceiling. |
| **Output Directory** | Where maps are written. Empty uses the project's *sourceimages*. A relative path resolves under it; an absolute path is used as-is. The **image** toggle beside the field saves each map [beside its material's textures](#where-the-maps-land). |
| **Name** | Affix around each map's name. A leading `_` is a suffix (`_Lightmap`), a trailing `_` a prefix. |
| **Preset** | The panel's bake settings under a name. See [Presets](#presets). |
| **Reset to Defaults** | Puts every setting on the panel back, switches included. **Shift+Click** makes the current values your defaults; **Ctrl+Shift+Click** forgets those and returns to the shipped ones. The Exclude set lives in the scene, not on the panel, so a reset leaves it standing. |
| **Bake Lightmaps** | Runs the bake. See [What a bake does](#what-a-bake-does). |

A switch rides the option box of the control it qualifies rather than a
checkbox row of its own — the environment is part of what Scope gathers,
adaptive sampling is how the Samples are spent, denoise is what the map ships
at that Resolution, and the reflection probe is what the bake ships beside the
maps Packing lays out. Click a button to flip it; its tooltip says what each state
does, and it takes a muted tint while off.

Resolution through Bounces sit in a **Quality** group — exactly the dials a
preset stores — and Output Directory and Name in an **Output** group.

The last three controls share a group at the bottom of the panel — the same
action block the
[WebXR preview](https://github.com/m3trik/pythontk/blob/main/docs/webxr_preview.md)
panel ends with, so the two are worked the same way: pick a preset, then run.

The header menu adds **Revert to Source** (see [Revert](#revert)) and
**Open Sourceimages Folder**. The header's **?** holds a short version of this
page.

## What a bake does

The panel's **Bake Lightmaps** and a script's `LightmapBaker.bake` run the
same steps:

1. Resolve the Scope to meshes and take away the Exclude set
   (`LightmapBaker.bake_targets`). Meshes Arnold renders nothing of are left
   out too, and named in the Script Editor: hidden or templated by their own
   flags, their shape's, an ancestor's, level-of-detail visibility or a
   display layer. The footer counts them apart from the excluded ones.
2. Check that the scene can bake (`LightmapBaker.preflight`), before anything
   in it changes. Every lightmap renders with Arnold, so mtoa is loaded here
   if it isn't yet; a machine without it is refused. Lights the tool authored
   are upgraded to per-area emission. A scene whose lights are all hidden or
   at intensity 0 is refused, and the Script Editor lists every light. A scene
   with no lights at all still bakes, because emissive materials can light it.
3. Make sure each mesh has a lightmap UV set (`UvUtils.create_lightmap_uvs`).
   A valid existing set is reused under its own name (`UV2`, `lightmap`, …),
   never repacked. A marker from before atlases were bound by rect is
   migrated first. Its squeezed lightmap UVs are restored and the rect moves
   into its engine binding, so it still reads its own cell of the old atlas.
4. Bake white-card irradiance with Arnold: a true-white Lambert rides each
   shape as Arnold's `-shader` override. Its neighbours keep their real
   materials, so bounce light and colour bleed are correct. Only a mesh's
   light onto itself is bounced off white.
5. Dilate the seams from the texels the UV layout covers, denoise, and write
   each map as a half-float EXR.
6. Place each map (see [Where the maps land](#where-the-maps-land)), then
   record it (`LightmapRecords.commit`): a JSON marker (`lightmapInfo`) on
   each transform, and a scene manifest on the `data_export` node that rides
   the FBX (see [Scene data nodes](data_nodes.md)). The maps the objects read
   before, and that nothing reads now, are set aside (see
   [When the maps move](#when-the-maps-move)).
7. Measure the finished maps (`LightmapBaker.bake_verdict`). A bake that
   comes back essentially unlit, or blown out, is still recorded, because it
   is a faithful render of the scene. The panel's footer warns, and the
   Script Editor lists the scene's lights.
8. Capture the room as a [reflection probe](#reflection-probe)
   (`LightmapBaker.bake_probe`), unless its switch is off.

Nothing is reverted first. An object keeps its lightmap until its new map is
written. So objects the bake doesn't finish (cancelled, or failed) keep the
map they had, and the footer says how many there are. Excluded objects are
never touched.

Nothing about the material or its UVs changes, so the engine does the
compositing. A per-object map works in any engine as-is: the mesh's second UV
set samples it directly. For Unity's native lightmap slots, add unitytk's
`LightmapMetadataController.cs` to the project once. It reads the manifest on
import and binds everything. The WebXR preview and the GLB export carry the
maps automatically; see pythontk's
[Live WebXR preview](https://github.com/m3trik/pythontk/blob/main/docs/webxr_preview.md).

Export with the Scene Exporter (or Export All). A plain Export Selection of
just the meshes leaves out the hidden `data_export` node, and with it the
engine wiring.

## Packing

**Atlas by Material** — the default — gives one shared EXR per primary
material. Each object gets a rect weighted by its surface area. The rect is the
engine binding (Unity `lightmapScaleOffset` / glTF `KHR_texture_transform`),
stored per transform, so every instance of a shared mesh keeps its own rect and
its own lighting. UVs are never edited. It is what an engine wants: fewer
textures, no per-object naming collisions, and the texels spent where the
surface area is.

**Per-Object** is the opt-out: every object gets its own map at the full
Resolution. Right for a hero asset that earns one, or a small selection.

The atlas layout is planned before any ray is traced. Each object renders at
2× the size of its cell (never above a full map), then shrinks into the cell,
and that shrink averages out Arnold's sampling noise. Measured at the same
ray budget per shipped texel, rendering above the cell matches spending those
rays as camera samples. 2× is the trade the tiers make: on a production floor
at four bounces, 4× took 75 s for 0.39% shadow mottle and 2× 22 s for 0.52%.

## Reflection probe

A lightmap is light with no direction: what a matte surface shows. A metal
has no matte part -- all it shows is what it reflects -- so a baked metal
carries nothing of the bake, and an object the bake leaves out (one that
moves) is lit by nothing of the room. So a bake also captures the room as a
reflection probe (`LightmapBaker.bake_probe`): an HDR Arnold renders from one
point with the bake's own settings, written beside the maps as
`<scene>_Probe.exr`, half the Resolution wide (256-1024 px).

- **Where it looks from.** Placed by `ProbePlacement`
  (`light_utils/lightmap_baker/_probe_placement.py`) so it holds in any scene,
  not only a furnished room:
  - over the objects that move in the room -- the visible meshes no bake
    marks -- else the room's middle, else (an open scene: a ground, a
    courtyard) over the baked objects that are not its ground. What moves
    outside the baked area does not pull it there;
  - at half the room's height, never more than 2 m above its floor -- eye
    level in a hall as in a room, and outdoors;
  - in open air. A point that sees the inside of a shell all round with no
    light in view is inside a solid (the car at the bake's middle, a column
    at the room's, a dark cabinet), and the probe steps out sideways (up only
    when no side lets out, never down) until the solid fills less than an
    eighth of its view. A lit room built inside out is still a room: a light
    in view says so;
  - at least 0.3 m from any surface it sees (a shelf over the probe would
    otherwise fill the top of the panorama).

  The visible meshes no bake marks are see-through to it, since a probe that
  carried them would show them where they no longer are -- they still shade
  the room. Unbaked meshes that surround the bake (a sky dome, a ground or
  terrain nobody baked) are not moving objects: they stay in its view, and
  are walls.
- **What it reflects onto.** The room's box, measured face by face against
  what the render sees: rays cast in a cone along each axis, each wall the
  farthest plane a tenth of the hits that face it squarely land on -- so a
  table under the probe is furniture, not the floor -- measured again from
  open air 1 m and 2.5 m around the probe, each face keeping its farthest
  wall (the column beside the probe, the car roof under it). A face whose
  cone mostly meets nothing is open and reads as distant, while the others
  still project: a courtyard's walls and floor, a bare ground's ground. With
  every face open the probe is read as distant.
- **What it renders.** Every light is visible to it -- an Arnold light too --
  and the scene's imagers (exposure, tonemap, denoise) are not applied: the
  maps get none, and the probe is read beside them, at their level. With
  **Include Environment** off, the sky dome is left out of it as it is out of
  the maps. An unbaked instance of a baked mesh stays in its view: the render
  can only hide a mesh, and with it every instance.
- **Z-up scenes** get no probe (logged): its deliverables read it in Y-up
  axes.
- **Where it goes.** The scene manifest carries it like a map (dependencies,
  search folders and a repath follow it), the GLB export embeds it, and the
  [WebXR preview](https://github.com/m3trik/pythontk/blob/main/docs/webxr_preview.md)
  lights with it: baked metals reflect the room instead of a stock studio, and
  unbaked objects are lit by the room (its Environment window switches it off
  and on, and draws where it was captured). In Unity, unitytk's **Apply Scene
  Lightmaps** puts it up as a box-projected Reflection Probe under the model:
  copy `<scene>_Probe.exr` into the project beside the lightmaps. Measured on a production soldering
  table against Arnold's render of it: a metal housing from 0.12 of Arnold's
  luminance to 0.62, its trays from 0.4 to 0.89-1.07, the unbaked magnifier's
  parts from up to 2.8x too bright to 0.65-1.45x.

Turn it off with the **camera** button on Packing (`reflection_probe=False`).
To capture it again without re-baking, run
`LightmapBaker.from_preset("desktop").bake_probe()` and save. Reverting the
last bake drops it.

A cancelled bake captures no probe, and a probe that fails is logged without
losing the bake, which is committed by then. A `<scene>_Probe.exr` held open
(a viewer, a sync client) costs its name, not the render: the probe lands
beside it as `<scene>_Probe_1.exr`. A re-bake into another folder sets the old
probe aside, as it does a superseded map.

## Sampling

On the **CPU**, every camera sample traces **GI Samples** bounce rays.

Arnold's **GPU** ignores GI samples, so the panel spends that budget as
camera samples instead:

- **Adaptive Sampling on** (the default — the button on the Samples field):
  every texel gets **Samples**. Noisy texels (shadows, contact) get more, up
  to Samples × GI Samples. Measured on four production floors at Samples 4,
  GI Samples 4: 73 s adaptive, against 381 s for giving every
  texel the full budget (Samples 16). Shadow noise was 1.31% against 1.06%.
- **Adaptive Sampling off**: every texel gets the full Samples × GI Samples.
  This gives the cleanest map and the slowest bake.

So a preset gives the same shadow quality on either processor. The switch
greys out while the Processor is CPU, which spends its samples the other way.

**Denoise** (the button on the Resolution field) removes grain (texel-to-texel
noise). It cannot remove blotches several texels wide; only more samples fix
those.

## Presets

The Preset combo is uitk's preset template:

- **Save** (the disk icon) stores the current settings under a name you type.
- The **⋯** menu renames, deletes, or opens the preset folder.
- A **\*** after the name means a setting has changed since the preset loaded.
- The built-ins (**preview**, **mobile**, **desktop**, **hero**) are italic
  and read-only.

Presets live in one store, `LightmapBaker.preset_store()`: the shipped JSON in
[`presets/`](../mayatk/light_utils/lightmap_baker/presets) plus a per-user
tier. `LightmapBaker.from_preset(name)` reads the same store, so a preset
saved in the panel is also a headless bake recipe.

| Key | Panel control | Built-ins store it |
|:---|:---|:---|
| `resolution` | Resolution | yes |
| `samples` | Samples | yes |
| `gi_samples` | GI Samples | yes |
| `gi_depth` | Bounces | yes |
| `adaptive` | the Samples field's switch | — |
| `include_environment` | the Scope field's switch | — |
| `denoise` | the Resolution field's switch | — |
| `beside_textures` | the Output Directory's image toggle | — |
| `reflection_probe` | the Packing field's camera toggle | — |
| `packing` | Packing (panel only; `from_preset` ignores it) | — |

Loading a preset writes only the keys it stores. So picking a built-in moves
the quality dials and leaves the switches as you set them.

Scope, Exclude, Processor, Output Directory and Name are never saved. They
belong to a scene or a machine; a preset that forced one machine's GPU would
fail on the next machine.

| Built-in | Resolution | Samples | GI Samples | Bounces |
|:---|:---|:---|:---|:---|
| preview | 256 | 2 | 2 | 2 |
| mobile (the default) | 1024 | 4 | 2 | 4 |
| desktop | 2048 | 4 | 4 | 6 |
| hero | 4096 | 6 | 4 | 8 |

The JSON files in
[`presets/`](../mayatk/light_utils/lightmap_baker/presets) are the source of
these numbers.

**mobile** was named **quest**. `from_preset("quest")` still builds it, with
a deprecation notice, until mayatk 0.21.0, and the panel moves a selection
saved as **quest** onto **mobile**.

## Exclude set

The Exclude set is a plain `objectSet` named `lightmapBaker_exclude`
(`mayatk.mat_utils.bake_sets.LightmapExcludeSet`). It saves with the scene and
shows in the Outliner.

Excluded objects get no map of their own, but they stay in the render: Arnold
still traces them, so they cast shadows and bounce light onto everything that
does bake. A group in the set excludes every mesh under it; faces exclude
their mesh.

The workflow reads the set itself (`LightmapBaker.bake_targets`). A bake from
the panel, from a script, or from a preset all skip the same objects, and so
does the Blender bridge's lightmap bake: an excluded mesh in the send crosses
and shadows the Cycles bake like any other, but gets no map and is never wired
on the way back. Only the send crosses, though, so an excluded object outside
it casts no shadow there.

A bake never touches an excluded object, so a map it already has survives. For example, bake a hero prop at **desktop**, exclude it, then
re-bake the room at **mobile**: the prop keeps its map.

The row mirrors the Marmoset bridge's Bake Source row, and both sets share
one base (`BakeSet`).

## Where the maps land

A map is named after its material's **texture set**: the base name its
material maps share, e.g. `Crate_Wood_01` from `Crate_Wood_01_BaseColor.png`.
The Name affix is added to that (`Crate_Wood_01_Lightmap.exr`). An object
without a texture set is named after itself. A texture set votes on its name
only through files named as map types (`_BaseColor`, `_Normal`, …): the
majority wins, and an unsuffixed image names nothing.

Names are unique within a bake. Each marker also claims the file it reads
(`LightmapRecords.claims`), and a bake never takes a name another object
claims. So a re-bake overwrites its own earlier map, but never a map another
object still uses. Re-baking one crate of ten that share a texture set, or a
room around an excluded object that shares one, gives the new map the next
free name (`_1`, `_2`, …) instead of writing over theirs. Both packings follow
this rule.

- **Default**: every map goes to the Output Directory.
- **Beside material textures** (the image toggle, `beside_textures=True`):
  each map is saved in the folder its texture set lives in. An atlas goes to
  its material group's folder. The Output Directory takes only the objects
  whose material has no texture folder that exists on this machine; a stale
  path never gets a folder created for it. Maps are baked into a scratch
  folder and then moved, so a failed bake leaves nothing in a texture folder.
  A material that several projects share writes its map into that shared
  folder.

Each marker records its map's folder, relative to the project where it can
be. The Scene Exporter, the Texture Path Editor and the WebXR preview find
the maps through the markers wherever they were written
(`LightmapRecords.lightmap_dependencies`, `.search_dirs`,
`.heal_lightmap_paths`). The file work underneath is pythontk's
`FileDependencies`: resolve each map by its recorded folder first, then by a
search, and gather the maps into one folder.

## When the maps move

Re-bake after changing the Output Directory, Beside Material Textures, the
Name affix or the Packing, and the objects get new files. The old ones are
set aside once nothing reads them (`LightmapRecords.superseding`), and the
footer says how many. A same-place re-bake just writes over its own maps.

Set aside, never deleted: each goes to the Recycle Bin (the trash on Linux
and macOS), or, where the drive has none -- a network share, a removable
drive -- into a `_superseded` folder beside it. A scene cannot see what
another scene file reads, so a map another file still uses (a Save As
copy's source, a copy made in Explorer) is one restore away. The texture
searches never look inside `_superseded` (pythontk's
`FileUtils.move_to_trash`, `FileDependencies.set_aside`).

Left behind, an old map was more than clutter. A tool that finds maps by
name can pick up the stale copy, and beside the textures a leftover keeps
its name taken, so going back to it wrote `_1`.

Only this scene's own maps are set aside. Each commit records which scene
file wrote the map, and a map is kept when:

- another object still reads it: an excluded object, one the bake didn't
  finish, or one outside the Scope;
- another scene file wrote it and is still there, such as the source of a
  Save As copy;
- it was baked before the scene recorded its writers (a scene's first bake
  after the change starts recording);
- a referenced object reads it, since the referenced file may name it too.

A scene saved under a new name, with the old file gone, still owns what it
wrote, and so does a scene baked before its first save: the first save stamps
the file it writes on every map baked while it was unsaved. Revert moves
nothing.

## Revert

**Revert to Source** removes the lightmap wiring: each object's marker and its
entry in the export manifest. It acts on the selected objects, or on every
baked object when nothing is selected.

It first shows what it will do, with the object count, and waits for **Ok**.
The materials and texture UVs were never changed, and the EXR files stay on
disk. The lightmap UV set also stays, and the next bake reuses it. One Undo
restores the wiring: a revert is one undo chunk.

## Scripting

```python
import maya.cmds as cmds
import mayatk as mtk

baker = mtk.LightmapBaker.from_preset("mobile", device="AUTO")
result = baker.bake(cmds.ls(selection=True), output_dir="D:/bakes")
# packing="per_object" gives one map per object instead of an atlas per material.

if result.refused:  # nothing was baked; no map or marker changed
    print(result.refused)
else:
    print(result.files)  # the distinct maps written, already recorded
    if result.verdict:  # an unlit or blown-out bake
        print(result.verdict)
```

`bake` is the panel's workflow, from the Exclude set through the verdict. It
returns a `LightmapBakeResult`, the same shape in blendertk:

| Field | What it holds |
|:---|:---|
| `maps` | `{object: map path}` for every map written and recorded. |
| `rects` | `{object: [scaleX, scaleY, offsetX, offsetY]}`, each object's engine binding (the identity for a map of its own). |
| `excluded` | Objects the Exclude set left out. |
| `hidden` | Objects left out because Arnold renders nothing of them (hidden or templated). Always empty in blendertk, which bakes hidden objects. |
| `unbaked` | Objects asked for that the bake didn't finish. They keep the map they had. |
| `retired` | Map files the bake superseded and set aside (the Recycle Bin, or a `_superseded` folder beside them). |
| `refused` | Why nothing was baked, as a sentence, or `None`. |
| `verdict` | A warning about the maps' level, or `None`. |

`files` and `folders` list the distinct maps and the folders they landed in.
The steps stay public for a custom flow: `bake_targets`, `preflight`,
`bake_atlas` / `bake_separated`, `LightmapRecords.commit` and `bake_verdict`.

Constructor switches mirror the panel: `adaptive`, `include_environment`,
`denoise`, `beside_textures` and `reflection_probe`. `bake(..., intensity=math.pi)` writes a
Unity-native-light calibration into the texels, once. The default, 1.0,
matches the Maya render.

The record's methods moved from `LightmapBaker` to `LightmapRecords`. The
baker's spellings (`lightmap_dependencies`, `search_dirs`,
`heal_lightmap_paths`, `relocate_lightmaps`, `repath_lightmaps`,
`normalize_lightmap_paths`, `export_record`, `refresh_export_metadata`) and
`commit_lightmap`'s `intensity` and `uv_rects` parameters still work. Each one
warns and is removed in 0.20.0.

## Blender twin

blendertk's `LightmapBaker` and its panel bake with Cycles and mirror this
one control for control: the same layout, the preset template (its presets
store `bounces` for `gi_depth`), the Exclude set (a stamped collection that
changes nothing about what renders), Beside Material Textures, Bounces,
Adaptive Sampling, the switches on the fields they qualify (bar the
Reflection Probe: Cycles captures none yet), Reset to
Defaults and the confirmed Revert. The engine matches too: `bake()` with its
preflight and verdict, `bake_targets`, the file claims, setting superseded
maps aside (a linked object's map is kept, as a referenced one's is here), a
linked library's own folder record (as a referenced module's is read here),
the legacy migration, and `LightmapRecords`. It returns the same
`LightmapBakeResult`, whose two copies `check_dcc_twins.py` keeps identical.

The differences are the renderer's. Cycles has no GI Samples: one sample count
covers every ray. Its adaptive sampling works on the CPU and the GPU alike, so
the switch is never greyed out, and it stops a clean texel early instead of
adding samples to a noisy one. A hidden mesh bakes (it is shown for its own
bake) instead of being skipped. Blender's preflight refuses a scene whose
lights are all hidden from the render or at zero energy, unless the world
lights the bake. The parity ledger
([`tentacle/docs/parity_map.py`](../../tentacle/docs/parity_map.py)) records
each difference.
