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
pythontk's generic `FileDependencies`. The panel is tentacle's
(`slots/tools/lightmap_baker.py` with `ui/tools/lightmap_baker.ui`): one Slots
class over this engine and blendertk's Cycles twin; see
[Blender twin](#blender-twin).

## Panel

Open it from tentacle's **Lighting ▸ Lightmap Baker**, or with
`marking_menu.show("lightmap_baker")`. A picker under the header switches
between two pages: **Bake** -- what a new bake covers and how it renders --
and **[Manage](#manage-page)** -- the maps the scene already reads. The run
button sits below both. The Bake page, from top to bottom:

| Control | What it does |
|:---|:---|
| **Scope** | What to bake: the **Selected** meshes, every **Visible** mesh, or the whole **Scene**, every copy of an instanced mesh included. Every Scope bakes fresh with the panel's settings; re-baking what changed is the [Manage page](#manage-page)'s. Its three buttons say what lights the bake (see [Which lights light the bake](#which-lights-light-the-bake)): the **light** is *Include Scene Lighting* (on: every scene light; off: only the Scope's own), the **sphere** is *Include Environment* (on: the HDRI skydome lights the bake; off hides the `aiSkyDomeLight` for the bake and restores it afterwards), and the **eye** is *Include Hidden Lights* (off by default: on switches the hidden lights on for the bake). |
| **Exclude** | *Set From Selection* makes the selection the scene's [Exclude set](#exclude-set): objects that get no map of their own but still cast shadows and bounce light onto everything that bakes. Its icons select the set or clear it. The label shows how many meshes are excluded. |
| **Isolate** | *Set From Selection* makes the selection the scene's [Isolate set](#isolate-set): moving parts (a locker door, a lid) that bake in a pass of their own and stay out of every other object's render. Same icons and count as Exclude. |
| **Packing** | **Atlas by Material** (the default): one shared map per material group. **Per-Object**: one map per object at the full Resolution. See [Packing](#packing). Its **camera** button is *Reflection Probe*: on (the default), the bake also captures the room it lit as an HDR, for what a lightmap cannot hold. See [Reflection probe](#reflection-probe). |
| **Processor** | Which one Arnold renders the bake on. **Auto** uses the GPU wherever Arnold has one, else the CPU; **GPU** and **CPU** force it. A machine setting, so it sits above the Quality group and no preset stores it. |
| **Resolution** | Map size in pixels (256–4096). It also sets the gutter width. Its **filter** button is *Denoise*: each map is cleaned at the size it ships — the object's own map, or its atlas cell. |
| **Samples** | Arnold camera (AA) samples per texel, squared. Its button is *Adaptive Sampling*: on a GPU bake the extra samples go only where the map is noisy. It greys out when the Processor is CPU. See [Sampling](#sampling). |
| **GI Samples** | Indirect diffuse samples, squared. See [Sampling](#sampling). |
| **Bounces** | Arnold's GI Diffuse Depth. 0 is direct light only; 2–3 light a room's corners and ceiling. |
| **Output Directory** | Where maps are written. Empty writes to *sourceimages/lightmaps*. A relative path resolves under *sourceimages*; an absolute path is used as-is. The **image** toggle beside the field saves each map [beside its material's textures](#where-the-maps-land); the **open** button opens the folder the maps went to: where the last bake wrote, else this field's folder, else *sourceimages*. |
| **Name** | Affix around each map's name. A leading `_` is a suffix (`_Lightmap`), a trailing `_` a prefix. |
| **Preset** | The panel's bake settings under a name. See [Presets](#presets). |
| **Reset to Defaults** | Puts every setting on the panel back, switches included. **Shift+Click** makes the current values your defaults; **Ctrl+Shift+Click** forgets those and returns to the shipped ones. The Exclude set lives in the scene, not on the panel, so a reset leaves it standing. |
| **Bake Lightmaps** | Runs the bake. See [What a bake does](#what-a-bake-does). |

A switch rides the option box of the control it qualifies rather than a
checkbox row of its own — what lights the bake is part of what Scope gathers,
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

The header's menu holds the [Bake Report](#bake-report); every operation on
the bakes the scene holds is the Manage page's. The header's **?** holds a
short version of this page.

### Manage page

A table of every lightmap the scene reads, the reflection probe included:
its file, how many objects read it, and its state. A state names the map's
condition; a row's tooltip says what it means and which button fixes it,
with the map's folder, size, which scene file wrote it, and why each of its
readers re-bakes.

| State | Means | Fix |
|:---|:---|:---|
| up to date | The map matches the scene as baked. | Nothing to do. |
| N changed | N of its objects changed since the bake. | **Fix All**, or right-click: **Re-Bake**: they go back into their own cells, or the atlas is rebuilt when their cells no longer fit (the tooltip says why). A map another scene file wrote is copied for this scene first. |
| group changed | Nothing in it changed, but its material group's atlas is rebuilt (an object new to the group, a different resolution or folder...). | **Fix All**, or right-click: **Re-Bake**. |
| split | One material's objects read several maps where one atlas would do (see [Duplicate materials](#duplicate-materials)); the tooltip names the materials -- two or more is a duplicate still in the scene. Each extra map is one more texture to load, and its objects can't batch with the rest. Not a re-bake target. | **Fix All**, or right-click: **Combine…** -- no render. |
| untracked | Baked before change tracking, or brought in from another file: changes to its objects go unseen. A bake of other objects leaves it untracked. | **Fix All**, or right-click: **Start Tracking**, records them as they stand, no render -- or **Re-Bake** if they may have changed since the map was baked. |
| missing | The file is on disk nowhere -- tracked or not. | **Fix All**, or right-click: **Re-Bake** bakes it again -- or put the file back. When its objects are hidden or excluded no re-bake renders them: **Fix All**, or right-click: **Revert**, takes the lightmap off them -- or show them and re-bake. |

Highlighting rows selects their objects, so the page acts on the scene
selection like every Maya tool. The footer counts the rows by state, read
again each time the page shows: gold when something needs fixing, red when a
map is missing. Every footer message is one short line, in the same colors;
the detail is in the tooltips, the confirmations and the Script Editor.

The page's verbs are its table's right-click menu and two footer controls,
shown with the Manage page only: the Preset, **Reset to Defaults** and the
run button are the Bake page's, beside the settings they act on. A re-bake
renders at the Bake page's Quality.

| Where | Control | What it does |
|:---|:---|:---|
| Table (right-click a row) | **Re-Bake** | Re-bakes the selected objects -- a highlighted map's, or what Select picked -- as they were baked. See [Re-baking what changed](#re-baking-what-changed). |
| | **Combine…** | Packs a *split* row's maps into one atlas from the lighting they hold -- no render -- and merges its duplicate materials. Asks the atlas size. See [Duplicate materials](#duplicate-materials). |
| | **Start Tracking** | Records the selected untracked objects as baked, as they stand -- no render -- so a change from here on reads as changed. Enabled on an *untracked* row. For maps known to match the scene; **Re-Bake** one that may not. |
| | **Revert…** | Removes the selected objects' lightmap wiring, once confirmed. See [Revert](#revert). |
| | **Open Folder** | Opens the highlighted map's folder. |
| Footer | **Select** | Highlights the maps in a state -- Changed, Split, Untracked, Missing, All -- and selects their objects, for the right-click menu. It clears the selection first, so a state with no maps leaves nothing selected. *Changed* selects exactly what a re-bake would redo, not every reader of their maps. |
| | **Fix All** | Once confirmed, leaves no row needing a fix: reverts a missing map no re-bake renders (its objects hidden or excluded), combines each split, starts tracking the untracked as they stand -- confirming vouches for those maps; Cancel and **Re-Bake** them if they may be out of date -- then re-bakes what changed, a missing map among it. A split whose group has a changed member re-packs with the re-bake instead. The render runs last: cancelling it keeps the rest. |
| Header | **Bake Report** | Opens the full [Bake Report](#bake-report): the whole scene, not a row. |

### Duplicate materials

An atlas is one per material group, and a group is what its material
renders, not the node that holds it. Two groups that render alike are one
group and bake one atlas:

- **Duplicate materials**: two materials with one set of textures, an
  imported copy (`Crate` and `Crate1`). The match is by texture, not name:
  `MatUtils.find_materials_with_duplicate_textures` compares texture content,
  placement and settings, so a renamed copy is found and two variants of one
  name with their own textures are not.
- **One material in two shading groups** (Maya): the same shaders wired into
  two shading groups, as an FBX import leaves them. The material list shows
  one material.

A bake never edits the materials themselves: they stay as authored, and
what the exporter reads is unchanged. Each object samples its own cell of
the atlas, so sharing one changes no object's lighting. (Combine, below, is
the one step that merges duplicates, and only when asked.)

A scene baked before this grouping can still read two maps for one group
(`Crate_Lightmap.exr` and `Crate_Lightmap_1.exr`): both rows read *split*.
Nothing about it needs a render -- each object's lighting is in the maps
already -- so **Combine…** (or **Fix All**) packs them:

1. The group is laid out as a fresh bake lays it (`LightmapBaker.atlas_plan`):
   one texel density, gutters, block-aligned cells.
2. Each object's lighting is cut from the map it reads, through its own
   binding, and placed in its new cell the way a bake places a tile:
   coverage-weighted, seams stitched, gutters filled.
3. The objects are bound to the new atlas. Each keeps when, and under what
   lighting, it was baked, so nothing reads as changed.

It runs under the footer's progress, one object at a time; a cancel writes
nothing. The atlas size is asked: from the largest map the split reads, doubling until
every object keeps its cell's detail (the production room: 2048 px keeps 56%,
4096 px all of it). The duplicate materials are merged
(`MatUtils.reassign_duplicate_materials`) -- they are what split the maps --
last, once every object is cut: a cancel, or a map that cannot be read
(that object keeps its map, named in the log), merges nothing.

The new atlas is this scene's. A map another scene file wrote, one a
referenced module reads, or one older than the writer record is never written
over: the atlas takes a name of its own and the old file stays on disk.
Otherwise it keeps the group's name, and, like every bake, reaches the folder
when the scene is saved ([Saved with the scene](#saved-with-the-scene)); the
map this scene wrote that nothing reads any more goes to the Recycle Bin then.
A **Re-Bake** of either row's objects mends a split too, re-packing the whole
group, at the cost of the render.

## Which lights light the bake

Three switches on the Scope field, the same in the Arnold baker, the Cycles
baker (blendertk) and the Blender bridge's bake:

| Switch | Default | What it does |
|:---|:---|:---|
| **Include Scene Lighting** | on | Every light in the scene lights the bake, wherever it sits. Off: only the lights in the Scope — selected, under what is selected, every visible one (Visible), every one (Scene). The other lights are switched off for the bake (their transform's visibility) and switched back on after. |
| **Include Environment** | on | The HDRI skydome (`aiSkyDomeLight`) lights the bake. It answers to this switch alone, in the Scope or not. |
| **Include Hidden Lights** | off | The hidden lights of that set are switched on for the bake and hidden again after — a light under a hidden group too: the group is shown and everything else in it is switched off, so only the light joins the render. A light at intensity 0 stays out. One whose visibility is locked or driven (a display layer, an animation) is named in the Script Editor and left off. |

The refusal for an all-lights-off scene and the light list the Script Editor
prints read the same set: with Include Scene Lighting off, a light outside
the Scope does not count; with Include Hidden Lights on, a hidden light the
bake can switch on does. The reflection probe is lit the same way as the maps.

## What a bake does

The panel's **Bake Lightmaps** and a script's `LightmapBaker.bake` run the
same steps:

1. Resolve the Scope to meshes and take away the Exclude set
   (`LightmapBaker.bake_targets`). Meshes Arnold renders nothing of are left
   out too, and named in the Script Editor: hidden or templated by their own
   flags, their shape's, an ancestor's, level-of-detail visibility or a
   display layer. The footer counts them apart from the excluded ones. A
   visible mesh that is neither baked now, baked before, nor excluded moves,
   and stays out of the render ([What the render holds](#what-the-render-holds)).
2. Check that the scene can bake (`LightmapBaker.preflight`), before anything
   in it changes. Every lightmap renders with Arnold, so mtoa is loaded here
   if it isn't yet; a machine without it is refused. Lights the tool authored
   are upgraded to per-area emission. A scene whose lights are all hidden or
   at intensity 0 is refused, and the Script Editor lists every light. A scene
   with no lights at all still bakes, because emissive materials can light it.
   Both read the lights the bake renders with ([Which lights light the bake](#which-lights-light-the-bake)).
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
   the FBX (see [Scene data nodes](data_nodes.md)). The maps wait in the
   session until the scene is saved ([Saved with the scene](#saved-with-the-scene));
   the ones the objects read before, and that nothing reads now, are set aside
   then (see [When the maps move](#when-the-maps-move)).
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
material. Each object gets a rect: its cell in the atlas. The rect is the
engine binding (Unity `lightmapScaleOffset` / glTF `KHR_texture_transform`),
stored per transform, so every instance of a shared mesh keeps its own rect and
its own lighting. UVs are never edited. It is what an engine wants: fewer
textures, no per-object naming collisions, and the texels spent where the
surface area is.

**Per-Object** is the opt-out: every object gets its own map at the full
Resolution. Right for a hero asset that earns one, or a small selection.

The atlas layout is planned before any ray is traced, and it is **packed**
(`ptk.ImgUtils.pack_atlas_rects` over `ptk.RectPacker`):

- **One texel density for the group.** Each cell is sized from the object's
  world surface area over its layout's island coverage, at the largest
  density at which the whole group fits. A mesh twice the size gets twice the
  texels, and a sparse layout keeps its islands' density.
- **Each cell in its surface's shape.** A cell takes the shape its layout's
  box has on the mesh, so texels come out square on the surface:
  `ptk.ImgUtils.uv_axis_scale` measures how much surface each UV axis spans
  (over `UvUtils.get_uv_world_triangles`). A layout covering part of its
  square gets a cell of that part's shape. A layout stretched to fill its
  square whatever the mesh's shape gets the mesh's shape back. Every wall
  and baseboard of a production room is laid out that way: a 131 × 394 cm wall
  in a square layout gets a 1:3 cell, and a 13 × 395 cm baseboard a thin
  strip. Before 2026-10-10 a squarified treemap bent cells to tile the square,
  and nothing undid a layout's stretch. On that room the texels ran 2.4:1
  (median) and 17:1 on the baseboards: 4 texels/cm up a baseboard and 0.25
  along it, where the cabinets' shadows fall. Now every cell is 1–1.05:1 at
  one density (1.04 texels/cm at 2048). A cell too long for the atlas at the
  group's density (a 30 m corridor wall) folds toward square at its own area,
  so it alone gives up its shape, not everyone's density.
- **A gutter at every resolution.** Every cell keeps the gutter (2 px, or
  Resolution / 256) from every neighbour and from the atlas edge, and no cell
  is under 4 px. Every padded cell lies on whole 4 × 4 blocks, the blocks
  BC6H (Unity's HDR lightmaps) and UASTC / ASTC (the web deliverable's KTX2)
  encode, so no block mixes two objects. The tiled layout's gutter shrank with
  its cell: at 256 px neighbours met at 3 px and a cell under a texel vanished.
- **Copies in name order.** Copies of one mesh take equal cells in name
  order, every bake. Maya measures their areas in single precision, and the
  tiled layout ordered cells by that noise, so the same room's baseboards
  took different cells in each bake.

A group too big to fit even at 4 px per cell falls back to the tiled layout,
with a warning: bake it at a higher Resolution, or split it across materials.

Each object then renders above its cell and shrinks into it, and that shrink
averages out Arnold's sampling noise: 2² render texels per cell texel (never
above a full map). RTT renders one square frame, so an elongated cell is sized
from its area, never under its long side: a 1:3 wall renders what a square cell
of its area does, and a baseboard's strip renders at its long side. Measured at the same ray budget per shipped texel, rendering
above the cell matches spending those rays as camera samples. 2× is the trade
the tiers make: on a production floor at four bounces, 4× took 75 s for 0.39%
shadow mottle and 2× 22 s for 0.52%.

## Reflection probe

A lightmap is light with no direction: what a matte surface shows. A metal
has no matte part -- all it shows is what it reflects -- so a baked metal
carries nothing of the bake, and an object the bake leaves out (one that
moves) is lit by nothing of the room. So a bake also captures the room as a
reflection probe (`LightmapBaker.bake_probe`): an HDR Arnold renders from one
point with the bake's own settings, written beside the maps as
`<scene>_Probe.exr`, half the Resolution wide (256-1024 px).

- **Where it looks from.** Placed by `ProbePlacement`
  (`light_utils/lightmap_baker/_probe_placement.py`, Maya's rays over the
  host-free `ptk.ProbePlacement` blendertk's Cycles bake shares) so it holds
  in any scene, not only a furnished room:
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
    in view says so. A piece of furniture that fills half the view from
    inside its own bounds holds the probe even with its front open and the
    room's lights showing through (a cabinet over the parts that move in
    it): the probe leaves by the opening -- never through a wall into the
    gap behind -- and stands off the same way;
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
  maps get none, and the probe is read beside them, at their level. It is lit
  as the maps are: with **Include Environment** off the sky dome is left out of
  it, with **Include Scene Lighting** off so are the lights outside the Scope,
  and **Include Hidden Lights** switches the hidden ones on for it too. An unbaked instance of a baked mesh stays in its view: the render
  can only hide a mesh, and with it every instance.
- **Z-up scenes** get no probe (logged): its deliverables read it in Y-up
  axes.
- **Where it goes.** The scene manifest carries it like a map (dependencies,
  search folders and a repath follow it), the GLB export embeds it, and the
  [WebXR preview](https://github.com/m3trik/pythontk/blob/main/docs/webxr_preview.md)
  lights with it: baked metals reflect the room instead of a stock studio, and
  unbaked objects are lit by the room (its Environment menu switches it off
  and on, and draws where it was captured). In Unity, unitytk's **Apply Scene
  Lightmaps** puts it up as a box-projected Reflection Probe under the model:
  the Scene Exporter embeds `<scene>_Probe.exr` in the FBX with the lightmaps, and the importer extracts them beside it. Measured on a production soldering
  table against Arnold's render of it: a metal housing from 0.12 of Arnold's
  luminance to 0.62, its trays from 0.4 to 0.89-1.07, the unbaked magnifier's
  parts from up to 2.8x too bright to 0.65-1.45x.
- **Highlights.** A light hanging inside the room (over a bench) reflects off
  the probe as if it hung on the wall behind it, so its highlight misses the
  metals. The GLB export draws the bake's lights into copies of the probe, each
  placed for a group of receivers -- the room's shell keeps the probe as
  captured -- and the lights themselves never ship
  ([Highlights](https://github.com/m3trik/pythontk/blob/main/docs/webxr_preview.md#highlights-the-bakes-lights-drawn-into-the-probe)).
  They are the lights the bake used, as the bake record holds them: a light
  Include Scene Lighting left out gets no highlight, and a hidden one Include
  Hidden Lights switched on keeps its highlight though it is hidden again.
  An object baked before the record held its lights falls back to every
  light that is on at export.

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
| `include_scene_lights` | the Scope field's light button | — |
| `include_environment` | the Scope field's sphere button | — |
| `include_hidden_lights` | the Scope field's eye button | — |
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
a deprecation notice, until mayatk 0.30.0, and the panel moves a selection
saved as **quest** onto **mobile**.

## Exclude set

The Exclude set is a plain `objectSet` named `lightmapBaker_exclude`
(`mayatk.mat_utils.bake_sets.LightmapExcludeSet`). It saves with the scene and
shows in the Outliner.

Excluded objects get no map of their own, but they stay in the render: Arnold
still traces them, so they cast shadows and bounce light onto everything that
does bake. A group in the set excludes every mesh under it; faces exclude
their mesh.

### What the render holds

A bake renders the static scene: what it bakes, what was baked before (any
object with a lightmap), and the Exclude set. Any other visible mesh is taken
to move -- an interactive prop that is never baked -- and is out of the render
for the bake, then switched back on. It casts no shadow and bounces no light
into the maps: a shadow it left would stay on the cart, the table or the floor
wherever the prop went. The Script Editor names each one. To keep a static
object's shadow without baking it, add it to the Exclude set. The reflection
probe leaves the same meshes out of its render.

Only a mesh's own shape is switched off, never its transform, so a baked mesh
parented under a moving one still renders. An unbaked instance of a mesh that
renders stays in, since hiding it would hide its twin too.

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

## Isolate set

The Isolate set is a plain `objectSet` named `lightmapBaker_isolate`
(`mayatk.mat_utils.bake_sets.LightmapIsolateSet`), for parts that move against
what is baked around them: a locker door on its cabinet, a lid, a drawer.

An isolated object still gets a map, lit by the static scene at the pose it has
when you bake. But it bakes in a pass of its own, and every other pass -- and
the reflection probe -- renders without it, so the cabinet keeps no
door-shaped shadow wherever the door swings. No isolated part shades another
either. A member brings everything parented under it (the door's glass), and
isolated objects still pack into their material's atlas. Taking a door out of
the bake's scope is not the same thing: once it has a map it counts as static
and shadows the rest, unless it is in this set.

## Bake set

Retired 2026-10-22: the panel no longer saves a Bake set. The Manage page
selects each map's objects and re-bakes them the way they were baked, which
is what selecting the set and baking again approximated (and only while the
panel's settings had not moved). `mayatk.mat_utils.bake_sets.LightmapBakeSet`
still resolves, with a deprecation notice, until mayatk 0.30.0; a set a scene
already holds is left as it is.

## Re-baking what changed

Every bake records, per object, what its map was baked against: its world
matrix and bounds, fingerprints of its points and of its lightmap UVs, its
materials, whether it renders, its atlas cell, the lighting the bake
rendered with, and the rule that chose those lights: the three light
switches and, with Include Scene Lighting off, the Scope's lights by name.
The record is the scene's own (`data_internal`), never on the marker that
ships.

The Manage page's footer **Select** -> *Changed* selects what a re-bake of
the changes would redo, read from it (`LightmapBaker.stale_report`), and
highlights their maps; **Re-Bake** (right-click a map) then redoes exactly
that, and **Fix All** does both. Scripted, `LightmapBaker.rebake_changed()`. What counts:

- **Changed objects**: moved, deformed, remodelled, lightmap UVs re-laid,
  material regrouped, shown or hidden, or the map missing.
- **Neighbours**: every baked object near enough to be shaded by a change --
  a changed object, or a static one that arrived, left or changed (the
  Exclude set's included). Near enough is the changed object's own size, at
  least 1% and at most 25% of the room's. An Isolate member's changes reach
  no neighbour.
- **The lighting**: each map's lights are read again by the rule its bake
  recorded, so a light moved, re-coloured, dimmed, switched, renamed or
  deleted, one added where the rule reaches (anywhere with Include Scene
  Lighting on), the HDRI or the Bounces stales everything that light lit --
  the whole room when every light lit it. A bake recorded before rules were
  reads as a lighting change once.

The Manage page puts the count in the footer, read again each time the
page shows; each map's row names its state, and its tooltip says why and how
to fix it. Nothing bakes until **Re-Bake**.
Each object re-bakes the way it was baked -- atlas or per object, lit by the
lights its bake used, into the folder its map is in -- whatever Packing, the
light switches and the Output Directory say, so the next dry run finds
nothing. The Output Directory takes only a map that has no file yet.

An atlas re-bake writes the changed objects back into the cells they own in
their material's atlas. Every other object's texels stay exactly as they
were, the seams with them are matched from the new side, and each material
keeps one atlas at one texel density. A Selected or Visible bake of part of
an atlas group does the same. The whole group re-bakes into a fresh layout
instead, once, when an object no longer fits its cell (it grew or shrank by
more than 10% on an axis), joins the group, or when the atlas was baked
before cells were recorded, split by an earlier partial bake, or sits in
another folder than the bake writes to; the confirmation names the atlas,
why, and how many more objects re-bake with it. A member that cannot bake now
(hidden, excluded) keeps the old atlas, and the rest of its group takes the
next free name. An atlas baked through the Blender bridge records its cells
too; one baked by a Blender whose blendertk predates them re-packs its group
the first time.

Re-Bake asks first when the answer is not obvious:

- **Keep / Move**: the Output Directory is filled in, and the maps the
  re-bake rewrites live elsewhere. Keep writes each back where it is; Move
  writes them to the Output Directory, which re-packs every atlas it touches
  whole there.
- **Bake All / Cancel**: an atlas re-packs whole (every member renders
  again), named with why, or the Bounces differ from the ones the maps were
  baked with (every map re-bakes). A fresh Maya's panel holds its default
  dials, not the preset the maps were baked with -- load that preset first,
  and only what changed re-bakes.

A Save As copy shares its source's maps until it re-bakes. Its first
re-bake writes the changed cells into a copy of the source's atlas under the
next free name (`<atlas>_1.exr`), and every object in the copy then reads
that copy; the source's file is never written. Later re-bakes write in place
into the copy. A copy saved into another project re-bakes into its own
project's Output Directory, not the source project's folder.

A visible mesh that is neither baked nor in the Exclude set is out of every
bake's render, so adding one stales nothing. The footer counts these
(`2 visible meshes out of the bake`), and the Bake Report lists them: bake them,
or add them to the Exclude set to have them cast shadow and bounce without a
map of their own.

### Re-Bake a map

**Re-Bake** on a highlighted map's row re-bakes its objects the way each was
baked -- its packing, its lights, its folder -- whether or not anything
changed: for what the record cannot see, an emissive material's strength or
an albedo change, or a noisy render. Each atlas member is written back into
its own cell, asking first as above. Objects with no lightmap yet are left
out (Bake bakes them). Scripted: `LightmapBaker.rebake(objects)`, its dry run
`rebake_report(objects)`.

### Bake Report

**Bake Report** (the header menu) opens a read-only report of every lightmap the
scene reads: its file and folder, how many objects read it, its size, which
scene file wrote it (another one, for a map a Save As copy still shares with
its source), and its state, as the Manage table names it; *Needs
attention* then says what each state not up to date means and how to fix
it. Below that: each object a re-bake of the changes would redo, why and
how (in place or re-pack), and the meshes out of the bake -- the table's
detail, for the objects no row shows. Object counts and
names select what they name; a folder opens. A map written since the scene
was last saved shows the folder it reaches at the save, marked *on save* in
italics (its name is italic on the Manage page too), and the report says why:
it waits in a temp folder until then ([Saved with the scene](#saved-with-the-scene)).
The window reads itself again on its header's refresh button, and on every save, open or new
scene while it is open; so does the Manage page. Scripted:
`LightmapBaker.bake_breakdown()` returns the same report as a
`ptk.ReportDoc` (`to_html()` / `to_text()`).

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
this rule. In an atlas, part of a group is written back into its own cells
instead, so the material keeps one atlas ([Re-baking what changed](#re-baking-what-changed)).

A scene saved from another (Save As) carries its source's markers, so they
name the source's maps as this scene's own. The writer record says which
scene file wrote each map (`SceneRecords.LIGHTMAP_WRITERS`): a map another
scene file wrote is never written over -- the copy's map takes the next free
name, and its atlas is never written in place -- so baking a moody copy
cannot change what the original shows. The same holds for:

- a map a referenced object reads that this scene did not write: it is the
  module's, read by every scene that references it;
- a file already in the folder under the atlas's name that this scene
  neither reads nor wrote: another scene's whose material shares the texture
  set. The atlas takes the next free name.

Find & Move (Texture Path Editor) copies such a map instead of moving it.

A measurement bake (`LightmapBaker(record=False)`: light layers, a rig's
portability check) writes its maps and probe into the folder it is given and
records nothing: no marker re-pointed, no map set aside, no atlas cell
written in place.

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

## Saved with the scene

A bake, a Re-Bake, a Combine, a lightmap transfer, Texture Combine's re-pack
and a Blender bridge bake change maps the saved scene file reads, under the same names. Until
the scene is saved, those changes stay out of the folder:

- Each map is written to a session folder in temp, under the name it will
  have (`LightmapRecords._stage`, pythontk's `PendingFiles`). The scene's
  folder record points there, so the panel, the Scene Exporter, the Texture
  Path Editor and the WebXR preview all read the new map. Writing a cell in
  place copies the atlas there first.
- **Save** moves each map the scene still reads into its folder, replacing
  the old file under its own name -- no `_1` -- and then sets aside the maps
  nothing reads any more ([When the maps move](#when-the-maps-move)). A map
  an undo took back is dropped instead.
- **Not saved** (File > New, opening a scene, quitting, a crash): the folder
  is as the saved scene left it, and nothing is left behind -- the session
  folder goes with the session, or with temp's next sweep after a crash.
- **Save As** leaves the first file its maps. A map whose name is taken on
  disk goes to the new file under the next free name, the new file owns
  every map it saves, and nothing is set aside: the first file still reads
  it all.

An autosave or an export to a `.ma` is a copy, not a save: it moves nothing.
A script calling a bake mechanism alone (`bake_separated`, `bake_atlas`,
`bake_probe`) records nothing, so its maps go straight into the folder it
names, as a measurement bake's do.
If a map cannot be written to its folder (held open by another program), the
save logs it, the scene keeps reading it from the session, and the next save
tries again.

## When the maps move

Re-bake after changing the Output Directory, Beside Material Textures, the
Name affix or the Packing, and the objects get new files. The old ones are
set aside once nothing reads them (`LightmapRecords.superseding`), when the
scene is saved, and the footer says how many. A same-place re-bake just
replaces its own maps.

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
the file it writes on every map baked while it was unsaved. Its next save also
writes the new name over the old one in the writer record, so a file that
later reappears at the old name (a Save As back to it, a restored backup)
does not take the maps over. A scene on a drive that is offline is not
"gone": its maps stay its own. Revert moves nothing, and a reverted scene's
own atlas stays its own to bake over.

## Revert

**Revert…** (right-click a map on the Manage page) removes the selected objects' lightmap wiring: each
object's marker and its entry in the export manifest. It acts on the selected
objects -- highlight rows to select their readers. With nothing selected it
asks nothing: a whole-scene revert is every row highlighted.

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
| `retired` | Map files the bake superseded, set aside when the scene is saved (the Recycle Bin, or a `_superseded` folder beside them). |
| `refused` | Why nothing was baked, as a sentence, or `None`. |
| `verdict` | A warning about the maps' level, or `None`. |
| `in_place` | Objects written back into their own atlas cells; every other object's texels were kept. |
| `repacked` | `{atlas file name: why}` for the atlases whose whole group re-baked into a fresh layout. |
| `report` | For `rebake_changed`: what it found changed and why (`ptk.StaleReport`: `summary()`, `lines()`). |

`files` and `folders` list the distinct maps and the folders they landed in.

```python
print(baker.stale_report().summary())  # what changed since the last bake; bakes nothing
result = baker.rebake_changed(output_dir="D:/bakes")  # re-bake it, in place where it fits
```
The steps stay public for a custom flow: `bake_targets`, `preflight`,
`bake_atlas` / `bake_separated`, `LightmapRecords.commit` and `bake_verdict`.

Constructor switches mirror the panel: `adaptive`, `include_scene_lights`,
`include_environment`, `include_hidden_lights`, `denoise`, `beside_textures`
and `reflection_probe`. With `include_scene_lights=False`, the lights in the
objects handed to `bake` (or under them) are the Scope's. `bake(..., intensity=math.pi)` writes a
Unity-native-light calibration into the texels, once. The default, 1.0,
matches the Maya render.

The record's methods live on `LightmapRecords` (`lightmap_dependencies`,
`search_dirs`, `heal_lightmap_paths`, `relocate_lightmaps`,
`repath_lightmaps`, `normalize_lightmap_paths`, `export_record`,
`refresh_export_metadata`); the baker's old spellings of them, and
`commit_lightmap`'s `intensity` and `uv_rects` parameters, were removed in
0.28.0. Pass `intensity` to `bake`.

## Blender twin

blendertk's `LightmapBaker` bakes with Cycles under the same panel
(tentacle's one Slots class drives both engines): the same layout, the preset template (its presets
store `bounces` for `gi_depth`), the Bake and Manage pages, the Exclude and
Isolate sets (stamped collections that change nothing about what renders),
the packed atlas (`ptk.ImgUtils.pack_atlas_rects`,
the one planner both use), Beside Material Textures, Bounces,
Adaptive Sampling, the switches on the fields they qualify, the
Reflection Probe, Reset to
Defaults and the confirmed Revert. The engine matches too: `bake()` with its
preflight and verdict, `bake_targets`, the file claims, setting superseded
maps aside (a linked object's map is kept, as a referenced one's is here), a
linked library's own folder record (as a referenced module's is read here),
the legacy migration, and `LightmapRecords`. It returns the same
`LightmapBakeResult`, whose two copies `check_dcc_twins.py` keeps identical.

The differences are the renderer's. Cycles has no GI Samples (the panel hides
that dial): one sample count covers every ray. Its adaptive sampling works on the CPU and the GPU alike, so
the switch is never greyed out, and it stops a clean texel early instead of
adding samples to a noisy one. A hidden mesh bakes (it is shown for its own
bake) instead of being skipped. Blender's preflight refuses a scene whose
lights are all hidden from the render or at zero energy, unless the world
lights the bake. The parity ledger
([`tentacle/docs/parity_map.py`](../../tentacle/docs/parity_map.py)) records
each difference.
