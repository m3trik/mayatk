# Texture Combine

**Nav**: [← docs](README.md) · engine [`TextureCombine`](../mayatk/uv_utils/texture_combine/texture_combine.py) · panel [`TextureCombineSlots`](../mayatk/uv_utils/texture_combine/texture_combine_slots.py) · planner [`ptk.UvCombine`](https://github.com/m3trik/pythontk/blob/main/pythontk/geo_utils/uv/combine.py)

Merge several materials into **one atlas and one material, in place**: the meshes keep their geometry, their texture UV set is repacked so every source material's islands share one 0-1 layout, and they wear one material whose maps are resampled from every source into that layout. Nothing is duplicated and nothing is lost. The originals are recorded in the scene, so a combine can be restored exactly, or re-combined when a source map is revised.

Open it from the tentacle UV panel's **Texture Combine...** button (Maya), or `marking_menu.show("texture_combine")`. The same window's **Transfer** mode is the UV / texture transfer tool.

## Combine mode

1. **Select the meshes** (or their group). Every instance of a selected shape is included, picked or not: instances share the UVs a combine moves.
2. The **Sources** table lists each source material:

   | Column | Meaning |
   |:---|:---|
   | Maps | the source's resolution and map count |
   | Scale | an override on its islands' size: `0.33`, `1/3` or `33%`. Mouse wheel nudges it, Shift for coarse steps |
   | Share | its estimated fraction of the atlas |
   | Res | the resolution it effectively keeps at the chosen atlas size. Amber means it keeps under 35% of its source resolution; four maps sharing one map of their size keep about half each, which is expected |

   **+ Mesh Override** / **+ Island Override** add rows that set a scale for one mesh, or for the islands under selected faces. The most specific row wins: island, then mesh, then material. The numbers follow every edit; nothing is packed until you run.
3. **Layout**
   - **Sizing: Texel Density** gives every island the same world-space texel density, times its Scale.
   - **Sizing: Keep Map Proportions** keeps each source map's islands at the sizes they have relative to each other in that map, so the texel-density choices made there survive. The maps are then sized against each other by **Normalize**:
     - **Texel Density**: equal average world density per map.
     - **Map Resolution**: a 4K map's islands twice the linear size of a 2K map's.
   - **Stacked islands stay stacked.** Islands of one material that overlap (a wheel used four times, mirrored halves) share texels in the source, and they move as one piece, so the atlas never pays twice for them.
   - **Atlas** sets the size. **Spacing** is the gutter between islands. **Rotate Islands** lets the packer turn islands where that packs tighter.
4. **Output / Material** name the result: the combine, its maps (`<name>_<Channel>.png` under `sourceimages/uv_transfer` unless a folder is given) and its material. The material is a copy of the dominant source material by default, or rebuilt on a named shader type, with an optional affix.
5. **Combine.**

### Lightmaps

A committed lightmap is bound per object (map + atlas rect), so a combine never breaks one. But the Lightmap Baker atlases by material, so with **Lightmaps** on:

- If the combined objects own their atlas outright, it is copied to `<name>_Lightmap` and re-committed (lossless; when another object already reads that name, the copy takes `<name>_Lightmap_1`).
- If they read several atlases, or share one with other objects, their cells are cut out pixel-exact and re-packed into one `<name>_Lightmap` atlas. A map nobody reads any more is set aside, never deleted.

Objects with no lightmap are listed in the report. The next bake gives them one in the same atlas, since it groups by the combined material.

### Revisions: the Combined Set picker

A combine is stored in the scene (`texture_combine` on the private `data_internal` node, which never exports). It holds the original UVs and face materials, the source maps with their file stamps, and the settings and overrides. Pick a stored combine from **Combined Set**; its row shows how many source maps were revised since. Its option box offers:

| Verb | What it does |
|:---|:---|
| Select | its meshes |
| Re-combine | re-read the revised source maps into the **same** layout (no re-pack), replacing the combined maps and material in place |
| Restore | the original UVs and materials, exactly (undoable) |
| Forget | drop the record, leaving the scene as it is |

**Update** (the main button with a stored combine picked) re-packs it with the table's current settings and overrides.

Why the combined layout goes *into* the texture UV set rather than beside it: an exporter ships every UV set, and a second one is `TEXCOORD_1`, the lightmap channel. The original layout lives in the record instead (Maya stores UVs as float32, and so does the record), so the export stays plug-and-play.

## Transfer mode

Moves UVs **or** textures from a source mesh onto targets. The two are alternatives, not options that compose.

- **Transfer: UV Set** copies the source layout onto each target. Exact for identical topology, sampled by proximity otherwise.
- **Transfer: Textures** re-maps the source's textures into each target's own layout by exact texel correspondence: no rays, no cage, no bleed. It needs identical topology.
- **Transfer: Auto** decides per run from the source's materials.

**Source** picks which meshes the transfer reads from:

| Source | Which meshes |
|:---|:---|
| Auto | read from the selection |
| First Selected Mesh | the first pick |
| All But Last Selected | the last pick is the target of all the others |
| Stored Meshes | the meshes captured with the `+` icon |
| UV Sets On Same Mesh | one mesh, textures moved between two of its sets |

**Targets** (Rest of Selection / Similar in Selection / Similar in Scene) applies to First Selected. **Include Lightmaps** carries each source's committed lightmap too.

With **Assign Result** on, the **Shader** row says what the targets end up wearing:

| Shader | Result |
|:---|:---|
| Same as source | a copy of the source material covering most of the layout, wired to the new maps |
| Same as target | a copy of the target's own material, wired to the new maps |
| A named type | a copy rebuilt on that shader (Stingray PBS / Standard Surface / OpenPBR) |
| Overwrite target | **no copy**: each target material is re-used and keeps its name, and each map it reads is rewritten where it is, in its own format, with that material's islands only. A channel it had no map for is written fresh (Output Name) and wired in |

Whatever the row, the result samples the UV set its maps were written for.

Overwrite target never creates, copies or reassigns a material, and it leaves alone anything that rewriting would change outside the run. The report says what it skipped:

- A material it can't reuse is skipped, with nothing written for it. That covers Maya's default shader, faces with no material, and a material another object wears (an instance of a target counts as the target). A run with nothing to overwrite stops with the reason.
- A map file gets a fresh file instead when a source or another material reads it, when it packs several channels or is read through tiling or an offset, or when its format can't be written (EXR, `.tx`) or the file is read-only.
- UV Sets On Same Mesh reuses the mesh's own material. Its old maps are the source, so they're kept and new ones are wired in.

Ctrl+Z restores the scene, not the overwritten files.

```python
mtk.TextureTransfer().transfer(target, source, assign=True, assign_from="in_place")
```

## Scripting

```python
import mayatk as mtk

tc = mtk.TextureCombine()
result = tc.combine(
    ["|KEYS_GRP", "|SOCKET_WRENCH_GRP"],
    name="tools",
    sizing="proportional",
    normalize="density",
    map_size=4096,
    rows=[{"kind": "material", "target": "toolmat_MAT", "scale": 1 / 3}],
)
tc.stale("tools")      # source maps revised since
tc.recombine("tools")  # re-read them into the same layout
tc.update("tools", map_size=2048)
tc.restore("tools")    # originals back, record dropped
```

`TextureCombine.gather` + `plan` give the table's numbers without packing (`ptk.UvCombine.plan`). The planner is DCC-agnostic.

Blender: blendertk mirrors the engine and the panel (`btk.TextureCombine`, opened from the Blender UV panel the same way). Its Material row offers Same as source / Same as target / Overwrite target in Transfer mode only: a Blender material has no shader type to rebuild a combine on.
