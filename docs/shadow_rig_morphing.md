# Shadow Rig — a source-responsive rig type (design review)

The **Projected** rig draws one silhouette — the target's geometry projected
onto the ground through the source — and re-places that texture live through
the projection model (anchor, bearing, reach, perspective growth, fade). The
*shape* is exact only for the direction it was drawn from: a light that swings
round the object keeps stretching the old outline, and only **Recalculate
Silhouette** redraws it. This doc reviews how a second rig type — shipped as
**Horizon** in the panel's `Rig:` combo, listed as *Morphing* while it was
planned — can follow a moving light at runtime, in Unity and in the WebXR
viewer, without a re-render. It weighs the proposal (an SDF atlas the engine
blends) against the alternatives and ends with the plan of record and the
contracts the three sides are built against.

Owners: pythontk (the bake), mayatk / blendertk (`rig_utils/shadow_rig.py`,
the panel), unitytk (`templates/ShadowPlaneController.cs`), pythontk's WebXR
viewer (`net_utils/preview/viewer.html`). The projected rig's physics lives in
`pythontk.ShadowProjection`; everything below reuses it.

**Revision 2.** Two pipeline claims are corrected against the code (Unity
binds an embedded texture as a sub-asset whose import settings cannot be
pinned; the viewer already rebinds disguised data textures from a manifest).
The recommendation moves from a plain horizon map to a **coverage-aware**
one: the plain map interpolates elevation but *crossfades* azimuth, which
ghosts thin members — exactly what R3 forbids. The patch-versus-quad question
is settled (the projected rig's own quad, placed by the engine). The bake is
reformulated on two height fields and its cost is measured, not estimated.

**Revision 3.** The review becomes a plan of record: every open call is
adopted with its recommended default, the phases carry acceptance
criteria mapped to R1–R8, and the current state of multi-object,
atlas and instancing support is recorded so the first phase is
unambiguous.

## Requirements

| # | Requirement | Why |
|:--|:--|:--|
| R1 | Rides the existing export routes unchanged in kind: FBX → Unity (template importer, embedded textures, the `shadow_metadata` channel on the `data_export` carrier) and FBX → GLB → WebXR (FBX2glTF + scene sidecar, the viewer shim). | The rig exists for engines that cannot afford real-time shadows; a bespoke export path defeats it. |
| R2 | The outline follows the light at **runtime** — a light the engine animates or the user drags — not only the DCC animation. | Baking a direction only replays the DCC; the point is response. |
| R3 | No ghosting: no double exposure at any light position, and no pop when the light crosses a sample boundary. | A crossfade of two silhouettes is a double exposure, not an intermediate shape. |
| R4 | Penumbra from the source's size (an area light's plate, the sun's half-degree disc). | The projected rig already draws it; a live rig cannot lose it. |
| R5 | Mobile-cheap: one plane, a few texture fetches, no second draw of the target, and a texture budget a headset can hold for tens of props. | WebXR on a headset. |
| R6 | Degrades gracefully: an engine without the custom shader shows the projected silhouette. | Every viewer that opens the GLB. |
| R7 | The DCC shows the artist something true. | The panel is the authoring surface. |
| R8 | Bake time in seconds, not minutes, at the panel's resolutions. | Preview refreshes on every option change. |

## What the routes carry today

- **Texture**: the silhouette PNG is the plane material's base-colour texture, so *Embed Textures* ships it in the FBX and the GLB sidecar's base-colour / alpha-mode sections rebuild the material for the viewer. Unity's import binds an embedded texture to the created material as a **sub-asset**; the template importer looks the record's texture up in the project by name, falls back to that bound sub-asset, and rewires the material to a pipeline-correct unlit-transparent shader (`ConfigureUnlitTransparent`: URP / HDRP / built-in). Import settings can be pinned only on a **loose** PNG, and only on its first import: `OnPreprocessTexture` recognises the `_shadow` stem and sets alpha-is-transparency, sRGB and mipmaps. A sub-asset gets Unity's defaults (sRGB, platform compression) with no hook.
- **Records**: `shadow_metadata` on the `data_export` carrier — v1 was `{name, texture, intensity}` per plane, and v2 (below) adds what an engine needs to place the plane itself; records join to nodes by name, like the lightmap channel, and the reader warns on a newer schema (`WarnIfNewer`).
- **Animation**: the fade is the plane's keyable `opacity` custom attribute — FBX user property (the `RenderOpacity` importer turns it into a controller) and, on the GLB route, a `KHR_animation_pointer` ramp the viewer shim resolves. The controller itself is import-time only: at runtime nothing reads the source or moves the plane.
- **The source**: a locator or a real light. A real light exports on the FBX route; on the GLB route FBX2glTF keeps its transform as a plain node (this pipeline writes no `KHR_lights_punctual`), and a locator is a plain node on both. Either way the engine has the source's transform at runtime, by name — nothing about the light needs baking.
- **The carrier precedent**: the lightmap web export already ships a data texture disguised in a real glTF slot (`LightmapWebExport.CARRIERS`: occlusion or emissive) plus a scene-extras manifest (`lightmap_web`: materials, carrier, encoding, uv) that the viewer's `applyLightmaps` reads to rebind the texture, set its colour space from the manifest and clear the slot it arrived in. On the Unity side the lightmap arrives as a loose file and `PinLightmapImportSettings` forces linear (sRGB off) and per-platform formats. That is the pattern a second shadow texture reuses.
- **Multiple objects, atlas, instances**: the selection is always one
  combined plane per source, there is no atlas, and every plane is its
  own mesh — see *Plan of record* below.

## Options

| | A · Projected (today) | B · View atlas (nearest, or crossfade) | C · SDF atlas morph (the proposal) | D · Horizon map, coverage-aware | E · Runtime projection of a proxy | F · Engine shadows | G · Height-field ray march |
|:--|:--|:--|:--|:--|:--|:--|:--|
| Shape at an unsampled azimuth | stretched build-time outline | nearest view pops every 360°/N; crossfade double-exposes | SDF lerp morphs wide shapes, pinches thin ones | **exact lateral edges** (the bin stores where the occluder is); far edge lerped between bins | exact | exact | exact |
| Thin members (legs, rails) | as drawn, build direction only | pop or double exposure | pinch between views | exact while one member per bin per texel; adaptive K | exact | exact | exact at height-field resolution |
| Elevation | model stretch (approximate) | needs elevation rings | rings, or the model stretch | **continuous** — the map is the elevation function | exact | exact | exact |
| Positional (perspective) light | model factors | one direction per shadow | one direction per shadow + model factors | **per texel** | exact | exact | exact |
| Penumbra | baked blur | baked blur, blended | shader widens the edge by row (a height proxy) | **physical**: the source's disc against the blocked rectangle, elevation and azimuth | multi-draw or blur pass | soft shadow maps | blocker-distance heuristic, or extra rays |
| Any number of lights from one bake | no — one texture per source direction | yes (per target) | yes | yes | yes | yes | yes |
| Texture per prop (RGBA8 in VRAM) | 1 × R² (64–256 KB) | N × R² alpha (N = 16: 1 MB at 256²) | as B | K × R² (K = 16: 1 MB at 128², 4 MB at 256²) | none (a proxy mesh) | none | two height fields (8–32 KB) |
| Shader cost / pixel | 1 fetch | 1–2 fetches | 2–3 fetches + lerp + smoothstep | 2 fetches + ~30 ALU | a second draw + stencil, blur for softness | depth pass + PCF | 12–24 fetches (a march) |
| Bake cost | 1 raster (70 ms) | N rasters | N rasters + distance transform | two height fields + a reduction: **0.7 s at 256²** (measured, below) | none | none | the same height fields (ms) |
| Rigid runtime motion of the target | keys, or the model port | keys + model port | keys + model port | **object-space** — moves with it | exact | exact | object-space |
| Deforming targets | no | no | no | no | **yes** | yes | no |
| Fallback without the shader | itself | one view | the projected silhouette | the projected silhouette | the projected silhouette | n/a | the projected silhouette |

**B** fails R3 as a crossfade; as *nearest view with a short crossfade* it is
the cheapest thing that follows the light at all — one atlas of views, one
fetch, a brief double exposure every 360°/N — and stays on the table only if
the maintainer accepts that pop. **F** is the honest baseline: on desktop
Unity a shadow-casting light beats every baked option; the rig is for WebXR,
mobile and unlit scenes. **E** is the only option that handles a *deforming*
target, and is right for one; for a static prop it re-draws the mesh every
frame to produce a hard-edged shadow that still needs a blur pass — the cost
the plane rig exists to avoid. **G** marches the two height fields D's bake
already produces: exact in every direction at 6–12× D's shader cost, so it is
the escalation for a prop D's reference test rejects (railings, wire chairs,
foliage), not the default. **C** is dominated by D: the same K, a worse answer
in azimuth, and no way to express where within a view's bin the shape sits.

### Why a plain horizon map is not enough

A horizon map stores, per ground texel and per azimuth bin, the elevation
interval the occluder blocks. Between bins the shader blends the two
neighbouring intervals. That blend is right in **elevation** — the far edge of
a box's shadow lengthens and shortens smoothly as the light rises and falls —
but in **azimuth** it is a crossfade. Take a thin pole and a light half way
between bins k and k+1: the texels that see the pole in bin k get its
interval at weight ½, the texels that see it in bin k+1 likewise, and the
texels along the true bearing get nothing from either. With the bake sampling
a ray per bin that is two faint ghost lines along the bin directions; with a
bake taking the whole wedge it is two fans at half alpha. Either is a double
exposure — R3 — and the fix is not a larger K: two chair legs 0.4 m apart
subtend 22.5° at one metre, so K = 16 merges them from a metre out and K = 64
from four.

The missing information is *where within the bin the occluder sits*. Store it.

### D — the coverage-aware horizon map

Per ground texel `t` and per azimuth bin `k` of K, four 8-bit values:

- `φa, φb` — the occluder's **azimuth extent** within the bin as seen from
  `t` (the bin's 22.5° at 0.09° steps). This is exact geometry, not a sample:
  the lateral edges of the shadow come out where the light's bearing enters
  and leaves `[φa, φb]`, so a thin pole casts one line on the true bearing.
- `lo, hi` — the **elevation interval** the occluder blocks along the ray at
  the coverage's centre `(φa + φb)/2` (0° to 90° at 0.35° steps): `[0, θ]`
  outside a grounded footprint, `[θ, 90°]` under an overhang, `[θ1, θ2]` for
  a slab seen edge-on. Sampled on the coverage centre rather than the bin
  centre so it always lands on the occluder.

The shader, per pixel (fragment and source in the target's frame):

```
L      = sourcePos - fragPos                  (or -sourceDir for a sun)
e, φ   = elevation and bearing of L;  ρ = angular radius of the source
                                        (asin(size / 2 / |L|), or the sun's 0.27°)
k      = bin containing φ;  A = tile(k, uv);  B = tile(k ± 1, on the side of A's
                                        coverage centre that φ lies, uv)
covφ   = overlap([φ-ρ, φ+ρ], [A.φa, A.φb] ∪ B's) / 2ρ       -- the lateral penumbra
t      = (φ - A.mid) / (B.mid - A.mid),  mid = (φa + φb) / 2  -- φ between the two samples
lohi   = both non-empty ? lerp(A.lohi, B.lohi, t)  : the non-empty one   -- hold, never fade
cove   = overlap([e-ρ, e+ρ], lohi) / 2ρ                      -- the far-edge penumbra
alpha  = intensity · covφ · cove
```

Two fetches and about thirty ALU. An empty bin is all zeros (`φa = φb`,
`lo = hi`): an *overlap* of zero, never a smoothstep that leaks. Tiles are
sampled bilinear with the UV clamped to the tile's inner half texel, no
mipmaps, so filtering never crosses into a neighbouring bin. The interval is
continuous across footprint and overhang edges — outside a grounded box it
tends to `[0, 90°]` at the wall and under it is `[0, 90°]`; outside a table
top `[atan(h/d_far), atan((h+t)/d_near)]` closes to `[atan(h/d_far), 90°]`
at the edge, which is what it is beneath — so bilinear
filtering is safe, and at the outer silhouette the all-zero empty code blends
an interval down to nothing over one texel: a soft edge where the penumbra
already is. Elevation near the zenith is stable: every bin agrees at 90°.

What one interval per bin cannot say: two members at *different azimuths in
the same bin* (two legs from a metre away at K = 16) merge into their hull —
one wider, correctly placed shadow instead of two — and two blocking layers
with a gap on the *same* ray (a lamp's base and its shade, a stool's footrest
ring) over-block the gap. Tables, chairs, sofas and shelves with solid sides
are one contiguous interval per ray and come out exact. A jump in `hi`
between neighbouring bins (a chair's seat in one bin, its back in the next)
is lerped across the bin: a bevelled corner instead of a step. All of these
are bounded by K, and the reference test below measures them per prop.

**Bake — two height fields, not a triangle sweep.** Rasterize the target
from above and from below into `z_top` and `z_bot` over its footprint (a
z-buffer variant of the rasterizer's triangle fill; 32² to 64²). Then, per
texel: the coverage `[φa, φb]` per bin from the footprint outline's angular
extremes within the wedge, and `[lo, hi]` by marching the coverage-centre ray
through the two fields (`hi = max atan(z_top / d)`, `lo = min atan(z_bot /
d)`). Measured on a chair-sized 32² footprint at K = 16, dense per-texel
reduction over the whole footprint, numpy 2.4, one desktop:

| Texels | Naive dense | Distance-adaptive pooling, bin skipping | + 8 numpy threads |
|:--|--:|--:|--:|
| 128² | 3.6 s | 0.63 s | **0.27 s** |
| 256² | 22 s | 2.1 s | **0.73 s** |
| 256², 64² footprint | — | — | 2.7 s |

83 % of (texel, bin) pairs are empty — a far texel sees the whole prop in two
or three bins — so skipping the bins a texel chunk never touches and pooling
the height fields conservatively (max `z_top`, min `z_bot`) for far texels is
what turns 22 s into under a second. The coverage is never pooled: it comes
from the footprint's outline pixels (about a hundred for a 32² mask), so the
shadow's lateral edges keep the mask's resolution at any distance; the ray
march for `[lo, hi]` visits fewer samples than the measured wedge pass. The
bake is cached by a hash of the
target's geometry and runs on Create, Rebuild and Recalculate — never on a
preview toggle (R8).

**Patch parametrization.** The map must cover the reach cap (`radius +
maxStretch × height`: 5.7 m for a 0.9 m chair at the default 6), and a
uniform square over that is 4.5 cm texels at 256² — coarsest exactly where
the shadow is sharpest. A **log-polar** patch around the contact (bearing
around the object × log-distance) puts the texels where the contact is: 256
× 128 texels from an eighth of the footprint radius to the reach cap gives
1.2 cm at 0.3 m, 4 cm at 1 m, 12 cm at 3 m and 23 cm at the cap — half the
texel count of 256², and the far field is where the penumbra is widest
anyway. (The inner ring must sit well inside the footprint: a leg's shadow
crosses the ground under a chair, and a texel inside `r_min` clamps to the
ring — measured, half a pole's shadow was lost at half the radius.)
The bearing axis wraps, so the bake writes a duplicated seam column and the
shader takes `fract` of the bearing. The uniform square with a lower
`maxStretch` for the type is the simpler alternative; the mapping lives in
two functions shared by the bake and the shader either way.

**Encoding and size.** One RGBA8 tile per bin, K tiles per rig, in a
lossless PNG; PNG lands at roughly half the raw size on these smooth fields.
The alpha channel is *data* — the import flags below keep it so.

**Reference and oracle.** A numpy `alpha(texel, light)` of the map is the
reference every shader is pinned to. Its own oracle is `rasterize_shadow` at
random source positions (70 ms per direction for 2 000 triangles at 256²,
measured): at bin-centre bearings the umbra must match within a texel; at
random bearings the test *reports* the fraction of texels that disagree and
the far-edge error, and asserts the bound. That same number drives an
**adaptive K**: bake at 16, measure, double while the error exceeds the
threshold — a prop-shaped default instead of a global one.

## Recommendation

Build the shared plumbing first and choose the representation second — the
choice then costs a bake and a shader, not a pipeline. Recommended
representation: **D, the coverage-aware horizon map**. It answers R2–R5 with
the physics rather than with sampling density, its bake is measured under a
second at 256², and the one thing it cannot express (two members in a bin) is
bounded by K and measured per prop. **G** is the documented escalation for
props that fail that measurement; it marches the same two height fields, so
it costs a shader, not a bake. **B** as nearest-view is the cheap fallback if
a pop is acceptable. **C** is retired: dominated on every row.

### The plumbing (shared by every runtime rig)

1. **The stamp.** A `shadowRigType` attribute on the plane (`projected`
   today) that `from_plane` reads, so every Utility action dispatches to the
   right engine per rig.
2. **Transport and binding are two things.** The second texture rides the
   DCC material's *emission* slot at weight 0 — that is what makes Embed
   Textures ship it in the FBX and FBX2glTF carry it as the GLB's
   `emissiveTexture`. Binding is by record, not by slot: the DCC also writes
   the PNG loose beside the FBX (as lightmaps are), the Unity importer finds
   it by the record's name and pins its import settings on first import by a
   `_horizon` stem (sRGB **off**, alpha-is-transparency **off**, no mipmaps,
   clamp, uncompressed or a measured format), and on the GLB route a manifest
   entry — the `lightmap_web` shape, on `shadow_metadata` — tells the viewer
   which material's `emissiveMap` is a map, to set `NoColorSpace`, no
   mipmaps and clamp, and to clear the slot. The projected silhouette stays in
   base colour, which is the fallback (R6). Two constraints: the plane's GLB
   material must stay a PBR material (three.js drops `emissiveTexture` for
   `KHR_materials_unlit`; this pipeline never writes it), and the embedded
   sub-asset copy is never the one bound, because its settings cannot be
   pinned.
3. **`shadow_metadata` v2**: per record `type`, `source` (the node the
   engine reads the light from), `source_type` (`point` / `directional`),
   `source_size` in world units or `source_angle`, `follow_source`, the
   projection-model inputs (`contact`, `radius`, `height`, `max_stretch`),
   the atlas rect, and a representation block (`horizon`: K, tile size and
   grid, patch mapping and its parameters in the target's frame, the
   encoding's version and empty code). Version-gated the way the readers
   already are.
4. **The engine places the quad from the source, for both rigs.** A port of
   `ShadowProjection.model` and `ShadowModel.placement` (under a hundred
   lines in C# and in the shim) turns the source's object-space position or
   direction into the quad's transform per frame. That alone upgrades every
   existing **projected** rig: its direction and length follow a runtime
   light, and only its outline stays build-time. The DCC bake of the plane's
   keys and fade stays as the fallback (R6) and as the record of the
   authored animation; `follow_source` decides which wins when both exist.
5. **Shaders**: a URP / HDRP / built-in variant beside the unlit fallback,
   and a `ShaderMaterial` in the viewer shim, both fed the same uniforms so
   the numpy reference pins them.

### The DCC side

- The panel's `Rig:` combo dispatches through `ShadowRigSlots.RIG_BUILDERS`;
  the new type is one row plus one item.
- The horizon rig **is the projected rig plus a stamp and a second
  texture**: the same tight bearing-aligned quad the expression already
  places, the same contact and source links, keys, Bake, Restore, Delete,
  Apply Source and Rebuild. Recalculate re-rasters the silhouette (fallback
  and preview) and re-bakes the map only when the geometry hash changed.
- Preview, v1: the projected silhouette — exact at the current source
  position after Recalculate, which is what the artist keys anyway. v2: a
  live preview shader (Maya: an `.ogsfx` for VP2 Core Profile, a `.fx` for
  DirectX 11; Blender: a `gpu.shader.create_from_info` viewport overlay) so
  the viewport shows the runtime look. Neither DCC can do it in a node graph
  — the occupancy mask needs a bitwise test and an integer texel fetch, and
  ShaderFX and EEVEE nodes have neither.
- One bake serves any number of sources: a target lit by three lights has
  three planes reading one map.

### Atlasing and instancing (both types)

Yes to both, in two levels, on the lightmap baker's pattern
(`LightmapBaker.pack_atlas`: one atlas per material, an area-weighted,
gutter-inset, texel-snapped rect per object — per *instance* — carried as a
per-instance `scaleOffset` binding and applied at sample time, Unity
`lightmapScaleOffset` / glTF `KHR_texture_transform`; the packer is pythontk
`ImgUtils.compute_atlas_layout`, a squarified treemap, with
`atlas_pixel_rects` / `inset_atlas_rects` / `snap_atlas_rects` /
`assemble_atlas`).

Two properties make the shadow rigs a better fit than most: the plane is
already a **unit quad** whose transform does all the placement, so every
shadow plane can be an instance of one mesh; and a rig's tile is a fixed
N × N square whatever its canvas aspect (the canvas rect is in world units and
is absorbed by the plane's scale), so **Recalculate rewrites a tile in place
and never repacks**. A horizon map's block of K tiles packs the same way, one
block per rig.

- **Level 1 — shared atlas.** One atlas texture and one material per rig type
  per scene (or per shadow set the panel builds together). A tile rect per rig
  is stamped on the plane (`atlasRect`) and written into `shadow_metadata` v2
  (`atlas: {texture, rect}`). Each DCC plane keeps its own four UVs pointing
  at its tile, so every fallback viewer renders correctly with no transform
  at all; the material count drops to one per type and draw calls batch by
  material (Unity's SRP batcher / static batching; the viewer merges by
  material). This level is independent of the representation choice and
  pays off for the Projected rig alone: it can land first.
- **Level 2 — instanced.** The planes share one mesh (Maya `instance`,
  Blender linked mesh data). The tile rect rides as a per-plane custom
  attribute and in the metadata, and the engine applies it per instance:
  Unity through a `MaterialPropertyBlock` (`_ShadowST`, the
  `lightmapScaleOffset` idea on our own instancing-enabled shader) so all
  shadows of a type draw in one call; the viewer through an `InstancedMesh`
  with a per-instance rect attribute the shim builds from the nodes sharing
  the plane mesh. glTF cannot carry a per-node texture transform on a shared
  material, so the rect lives in the node's `extras`, and a viewer without the
  shim would show the whole atlas on every instance: Level 2 is an **export
  option** for controller-equipped targets, Level 1 the default and the
  fallback.
- **Budget.** Fifty props at 256² tiles is one 2048² atlas for the Projected
  rig. A horizon block is K tiles, so fifty props at K = 8 and 128² are 26 MB
  of RGBA8 — the horizon rig is for the props whose outline matters (an
  L-shaped sofa, a chair the light circles), the projected rig with runtime
  placement is the bulk option. The packer reports the atlas size before
  anything is baked, as the baker's does.
- **Panel.** An `Atlas:` option (Off / Shared / Instanced) beside the rig
  type, and a **Pack Atlas** Utility action that packs or repacks every rig of
  a type — the mirror of the baker's `pack_atlas`.

## Sampling and budgets

| Knob | Default | Range | Note |
|:--|:--|:--|:--|
| Azimuth bins K (D) | 16 (22.5°), adaptive | 8–64 | Doubled while the reference error exceeds the threshold; K tiles, so VRAM doubles with it |
| Tile resolution | 256 × 128 log-polar, or 256² | 128–512 | Same combo as the projected rig's texture resolution |
| Patch mapping | log-polar, half the footprint radius → the reach cap | uniform square | Uniform needs a lower `maxStretch` for the type |
| Source angular radius | from `source_size` / distance, or `source_angle` | — | 0.27° for the sun; sets both penumbrae |
| Views (B / C, ring) | 16 at the build elevation | 8–16 | Plus 3 elevation rings (15°, 40°, 70°) for a hemisphere: 36–48 |

| Texture per prop (RGBA8, raw) | Size |
|:--|--:|
| Projected, 256² (alpha-only R8) | 256 KB (64 KB) |
| Horizon, K = 8, 128² | 0.5 MB |
| Horizon, K = 16, 128² | 1 MB |
| Horizon, K = 16, 256 × 128 log-polar | 2 MB |
| Horizon, K = 16, 256² | 4 MB |
| Horizon, K = 32, 128² | 2 MB |

## Plan of record

**Status, 2026-09-04: phases 1 to 5 are built and verified, and phase 6's viewport previews are built and pixel-verified in both DCCs** (the section *The DCC previews* under *Engines*); of phase 6 only the height-field march remains unbuilt, and no prop family has failed the reference bound to need it. Earlier: pythontk carries the bake and the reference (`ShadowHorizon`, `HorizonMap`, `ImgUtils.rasterize_height_fields`) and the packer (`ShadowAtlas`); mayatk builds both rig types, per-object planes and the shared atlases, and publishes `shadow_metadata` v2; unitytk ships `ShadowPlane.shader` and a runtime controller that places the quad from the source node; pythontk's `MeshConvert.apply_glb_shadows` and the packaged `shadow_rig` viewer script do the same on the GLB route. Phase 6 (the DCC viewport preview shaders, and the height-field march for a prop family that fails the reference bound) is the remainder.

Revision 3 adopts the recommended answer for every call the review left open.
Each is an attribute, an option or a constant, so any of them can be flipped
later without touching the phases.

| Call | Adopted | Where it lives |
|:--|:--|:--|
| Representation | Coverage-aware horizon map | `pythontk.ShadowHorizon` |
| Bins | K = 32 (measured: 16 doubles the error at every tile size), doubled to 64 while the tolerant reference error exceeds 5 % | `ShadowHorizon.DEFAULT_BINS`, `bake_adaptive` |
| Patch mapping | Log-polar, an eighth of the footprint radius → the reach cap; a 256 × 64 tile (64 radial rows score within half a point of 128; 256 bearing columns keep a 5 cm pole's shadow at 3 m at alpha 0.81 where 128 smear it to 0.30) over a 128-pixel footprint (a 5 cm leg stays 5 cm; 32 pixels made it 13 cm) | `ShadowHorizon.range_for`, `DEFAULT_SIZE`, `DEFAULT_FOOTPRINT`, `HorizonMap.uv` |
| Mobile texture format | RGBA8 uncompressed, pinned per platform the way the lightmap importer pins its formats; block formats only after the reference test measures them | `ShadowPlaneImporter.OnPreprocessTexture`, `_horizon` stem |
| Carrier | Emission-slot transport; loose PNG plus record binding; a manifest on `shadow_metadata` for the viewer | DCC engines, `ShadowPlaneImporter`, viewer shim |
| Runtime placement | `followSource` on the plane, default on: the engine places the quad from the source when the record names one and the node exists, else the baked keys play | plane attribute → record `follow_source` |
| Planes per selection | `Combined` (today's behaviour) or `Per object` | panel `Planes:` combo, `ShadowRig.create_per_object` |
| Atlas | `Shared` when two or more planes of a type exist, `Off` below that, `Instanced` as an export option | panel `Atlas:` combo, `ShadowRig.pack_atlas` |
| Label | *Horizon* — shipped; the combo listed it as *Morphing* while planned | `RIG_BUILDERS` |

### Multiple objects, atlas, instances — both types

Today the selection is always **one combined plane per source**: `create`
takes a list of targets and rasterizes them together, `create_for_sources`
fans that out over sources, the `Include Children` box only widens what is
rasterized. There is **no atlas** — every rig writes its own PNG and its own
material — and every plane is its own `polyPlane` / mesh datablock. Per-object
planes, the shared atlas and instancing are phase 1 below, and they are one
mechanism for both rig types: a rig's tile is a fixed square (the projected
rig's canvas is absorbed by the plane's scale; the horizon rig's map is
object-space and never touches the quad's UVs), so a horizon rig packs a
block of K tiles the way a projected rig packs one.

- **Per object**: one rig per selected transform, each with its own contact,
  quad, tile and record; a table with props on it stays a Combined rig.
  Per object × per source is N × M planes.
- **Shared atlas** (Level 1): one atlas PNG and one material per rig type per
  scene. The projected tile is addressed by the plane's own UVs, so it is
  fallback-safe; the horizon block by a per-plane rect the shader reads.
  Recalculate rewrites a tile in place, delete clears it, and the packer
  repacks only when a rig is added.
- **Instanced** (Level 2): the planes share one mesh (Maya `instance`,
  Blender linked mesh data) and the per-plane rect rides a custom attribute
  and the record, applied per instance by the engine (`MaterialPropertyBlock`;
  `InstancedMesh`). An export option, because a viewer without the shim shows
  the whole atlas on each instance.

### Phases

Each phase ships on its own and lands behind the previous one's tests.

1. **Per-object planes, shared atlas, instances — projected rig.** mayatk /
   blendertk `shadow_rig.py` and `.ui` (`Planes:` and `Atlas:` combos, a
   **Pack Atlas** Utility action) on pythontk's existing atlas helpers;
   `shadow_metadata` v2 `atlas`; the Unity importer rewires once per shared
   material; nothing in the viewer for Level 1. Tests: N props → N rigs, one
   material and one atlas per type; Recalculate rewrites its tile and leaves
   every other tile's bytes unchanged; delete clears the tile; the FBX and
   GLB integration harnesses show N planes with the right tiles; parity
   sweep clean. Meets R1 and R6 unchanged; fifty planes are one 2048² PNG.
2. **Runtime plumbing — both rigs.** The `shadowRigType` stamp; record v2
   (`type`, `source`, `source_type`, `source_size` / `source_angle`,
   `follow_source`, `contact`, `radius`, `height`, `max_stretch`, `atlas`);
   the model port — `ShadowPlaneController` gains a runtime `Update` (today
   it is import-time only) and the shim a per-frame placement — from
   `ShadowProjection.model` and `ShadowModel.placement`; the carrier — the
   importer's `_horizon` pinning and the viewer's manifest rebind. Tests:
   an FBX carrying a `_horizon`-stem PNG in its emission slot imports with
   the pinned flags and the viewer reads it back off the material as
   `NoColorSpace`, so the carrier is proven before anything depends on it;
   after the
   source moves at runtime the quad's transform matches `placement` within
   1e-3 in the Unity test project and the viewer harness; without the
   controller the plane stays where the keys put it. Meets R2 for placement,
   R6, R1 — and every existing projected rig follows a runtime light.
3. **`pythontk.ShadowHorizon`.** `ShadowHorizon.bake(meshes, contact,
   radius, height, *, up=1, bins=16, size=256, mapping="logpolar",
   max_stretch=None, adaptive=True, threshold=0.02) -> HorizonMap`;
   `HorizonMap.alpha(points, light=None, *, direction=None, source_size=0.0,
   source_angle=None)` — the numpy reference; `HorizonMap.to_rgba()` /
   `from_rgba(pixels, params)`; `ImgUtils.rasterize_height_fields(meshes,
   size, *, up)` for the top and bottom z-buffers, beside `rasterize_shadow`.
   Tests in `test_shadow_horizon.py`, on box, table, chair and thin-pole
   fixtures: bin-centre umbra within one texel of `rasterize_shadow`; random
   bearings disagree on at most 2 % of shadow texels for the box and table
   and 5 % for the chair at its adaptive K; the pole casts one shadow on the
   true bearing at every tested bearing; far-edge error at most two texels;
   penumbra width within 0.35° plus one texel of the projected penumbra;
   the chair bakes in at most 3 s at 256 × 128 on 8 threads; output bytes
   are hash-stable. Meets R3, R4, R8 in the reference.
4. **The Horizon rig in both DCCs.** `create(..., rig_type="horizon")` bakes
   on Create, Rebuild and Recalculate behind a geometry-hash cache, writes
   `<name>_horizon.png` beside the silhouette, binds it to the emission slot
   at weight 0 and writes the `horizon` block; `RIG_BUILDERS["Horizon"]`; the
   preview is the silhouette. Tests: Maya engine and panel, Blender, parity
   0 untriaged; a second Recalculate without a geometry change does not
   re-bake. Meets R7 (first version) and R1.
5. **Engine shaders.** *Shipped as* Unity `ShadowPlane.shader` — one
   legacy-CG SubShader, no `LightMode` tag and no pipeline includes, which
   covers Built-in **and** URP's `SRPDefaultUnlit` pass; HDRP keeps the
   importer's `HDRP/Unlit` rewire and so renders the silhouette (see Risks).
   The controller sets the source, frame, tile rect, bins and mapping per
   frame through a `MaterialPropertyBlock`, instanced for Level 2; the
   viewer gets a `ShaderMaterial` and an `InstancedMesh` for Level 2. Tests:
   rendered engine pixels against `HorizonMap.alpha` in both engines (the
   numbers are under *Engines* below); a fifty-prop atlas at K = 8 and 128²
   at most 26 MB. Meets R2–R5 in the engines.
6. **Preview shaders; G on demand.** A Maya `.ogsfx` (and a `.fx` for
   DirectX 11) and a Blender `gpu.shader.create_from_info` overlay for the
   runtime look (R7 second version), both assembling the shared body at run
   time; the height-field march as a second shader on the same data if a prop
   family fails phase 3's bound. **Not** EEVEE material nodes: they have no
   bitwise test and no integer texel fetch, so the occupancy mask cannot be
   read in a node graph at all.

### Criteria coverage

| Criterion | Met by | Proven by |
|:--|:--|:--|
| R1 · routes unchanged | phases 1–2: record v2, emission transport, loose PNG | the FBX and GLB integration harnesses, extended |
| R2 · runtime response | phase 2 (placement), phase 5 (outline) | source moved at runtime; transform and pixels against the reference |
| R3 · no ghosting | coverage per bin; hold, never fade | the random-bearing bound and the thin-pole fixture |
| R4 · penumbra | the disc's overlap in both axes | the penumbra-width test |
| R5 · mobile | two fetches and about thirty ALU; the VRAM table | a static shader-cost count; the fifty-prop assertion |
| R6 · fallback | silhouette in base colour; the baked keys | the controller-less import test |
| R7 · DCC truth | silhouette preview, then the viewport shader | the panel tests |
| R8 · bake time | 0.73 s at 256², measured | the 3 s bake test |

## Contracts (implementation)

What the code maps showed changes three things above: the transport needs no
material slot (both routes already bind **loose files by name** — the Unity
importer searches the project for the record's texture, and the GLB
conversion has appliers that bind loose textures into the file, the lightmap
precedent); fades constrain sharing (a GLB fade targets a *material index*
and the viewer drives one mesh per material, so planes keep **their own
material** in the file — the DCC keeps per-plane shaders sharing one atlas
texture, and the engines consolidate and instance from the records); and the
DCC never instances planes — instancing is an engine-side optimisation
derived from the records, so R6 stays intact. Everything below is what the
three work streams (pythontk + DCCs, Unity, viewer) build against.

### `shadow_metadata` v2

```json
{"version": 2, "unit_scale": 0.01,
 "planes": [{
   "name": "Box_shadow", "type": "projected" | "horizon",
   "texture": "Box_shadow.png", "intensity": 1.0,
   "source": "shadow_source", "source_type": "point" | "directional",
   "source_size": 0.0, "source_angle": 0.0, "follow_source": true,
   "contact": "Box_contact_loc", "ground": 0.0,
   "radius": 1.4142, "height": 2.0, "max_stretch": 6.0,
   "canvas": [-1.0, 1.0, -0.5, 0.5],
   "atlas": {"texture": "shadow_atlas_projected.png", "rect": [sx, sy, ox, oy]},
   "horizon": {"texture": "Box_horizon.png", "bins": 16, "tile": [256, 128],
               "layout": [4, 4], "mapping": "logpolar", "r_min": 0.7, "r_max": 5.7,
               "frame_a": [1, 0, 0], "frame_b": [0, 0, 1], "encoding": 1,
               "max_stretch": 6.0, "layers": 2, "rect": [1, 1, 0, 0]}}]}
```

- Lengths (`source_size`, `ground`, `radius`, `height`, `r_min`, `r_max`) are
  DCC units; `unit_scale` is metres per DCC unit (Maya cm → 0.01, Blender
  1.0) so an engine that imported in metres multiplies. Node fields are leaf
  names, joined the way the records already join. `source_angle` is the
  angular *diameter* in radians (a sun: 0.0093). `canvas` is the stamp the
  expression reads. `atlas` and `horizon` are absent (Unity: their `texture`
  is empty) when not in use.
- The **model** an engine evaluates per frame is `ShadowProjection.model`
  with `contact` = the contact node's world position, `light` = the source
  node's world position (or `direction` = the way a directional source
  shines), `ground`, `radius`, `height`, `max_stretch`, then
  `ShadowModel.placement(canvas)` → the quad's centre, extent along the
  bearing, extent across; yaw = `atan2(bearing.x, bearing.z)` as the Maya
  expression writes it. Applied only while `follow_source` and both nodes
  resolve; otherwise the imported keys play.
- Atlas rects are the lightmap convention: `[scaleX, scaleY, offsetX,
  offsetY]`, `uv' = uv * scale + offset`, origin bottom-left (Unity); the
  GLB pass flips them for glTF's top-left origin with `flip_rect_v`.

### The horizon map

- **Frame**: the contact node's local frame; origin at the node, up = its
  local Y as exported, bearing zero along `frame_a`, bearing increasing
  toward `frame_b`. The DCC writes the vectors in **FBX/glTF axes**
  (right-handed Y-up): Maya `a = (1,0,0)`, `b = (0,0,1)`; Blender
  `a = (1,0,0)`, `b = (0,0,-1)` (its exporter maps local +Y to FBX −Z).
  Unity converts FBX axes to its own by its import rule, pinned by the
  end-to-end test.
- **Mapping** (`logpolar`): for a ground point at local horizontal `(x, z)`
  with `r = hypot(x, z)`, `θ = atan2(z, x) mod 2π`: `u = θ / 2π`,
  `v = ln(r / r_min) / ln(r_max / r_min)` clamped to `[0, 1]`; `r > r_max`
  casts nothing. `r_min` = an eighth of the footprint radius (a leg's or an
  overhang's shadow crosses the ground under the object; measured, half a
  pole's shadow was lost at half the radius), `r_max` = the reach cap
  `radius + max_stretch × height`. The bearing axis wraps: sample `u` with
  `fract`.
- **Texels**: per bin `k` one tile of `tile = [W, H]` texels; column `x`
  spans `θ ∈ [x, x+1) · 2π / W`; `v` runs along rows with the PNG's **top
  row at `r_min`**. Engines that flip on import (Unity) sample `1 − v`; a
  glTF loader (top-left origin) samples `v`.
- **Two layers per bin** (`horizon.layers = 2`). A table top spans a whole
  bin while its legs are thin, so one interval per bin was measured at 15 to
  20 % disagreement on table and chair fixtures, and more bins did not help.
  The bake splits the footprint into **grounded** columns (touching the
  ground: legs, walls, boxes) and **floating** ones (overhangs: tops, seats)
  and stores each as its own coverage and interval; the shader evaluates
  both and takes their union.
- **Channels** (RGBA8, all 0 = empty): tile `k` (`0 ≤ k < bins`) is bin
  `k`'s grounded layer and tile `bins + k` its floating layer. Elevations
  are stored as **cotangents**: `value = cot(angle) / max_stretch` clamped
  to `[0, 1]`, so `0` is the zenith and `1` the reach-cap elevation
  `atan(1 / max_stretch)` and everything below it — the block's own
  `horizon.max_stretch`, which is the scale the map was baked with and
  not the record's live `max_stretch` (the placement cap, which the
  artist can retune without re-baking; decoding with it would mis-read
  every length) — a shadow boundary
  moves as `cot(elevation)`, so 8-bit degrees put a grazing overhang's
  edge tens of centimetres off while the cotangent keeps it at
  `height × max_stretch / 255`. Floating: `R = cot(lo)`, `G = cot(hi)`
  sampled along the ray at the bin's **coverage centre** (the midpoint of
  its first and last set sub-bins). Grounded (`lo` is always the ground):
  `R = cot(hi)` of the **first run** of set sub-bins, `G = cot(hi)` of the
  later runs (`0` with one run) — two legs in one bin keep their own shadow
  lengths. `B` and `A` together are a **16-bit occupancy mask** over 16
  sub-bins of the bin: sub-bin `j` covers bearings `s ∈ [j/16, (j+1)/16)`
  of the bin, `B` bit `i` (value `2^i`) is sub-bin `i`, `A` bit `i` is
  sub-bin `8 + i`; a set bit means the layer's occluder lies at that
  bearing as seen from the texel. A hull `[φa, φb]` was measured to merge
  two legs whenever they share a bin — at four metres that happens even
  with 64 bins — and shadow the ground between them; the mask keeps them
  apart at 0.7° with 32 bins.
- **Layout**: `2 × bins` tiles in a grid, `cols = ceil(sqrt(2 × bins))`,
  `rows = ceil(2 × bins / cols)`, tile `t` at column `t mod cols`, row
  `t div cols` (row 0 at the top of the PNG); `horizon.layout = [cols,
  rows]`. `horizon.rect` is the block's rect inside the type atlas
  (`[1,1,0,0]` when not atlased). A tile is **point-fetched** at the four
  texels around `(u, v)` (`texelFetch` / `Load`; `x` wraps by the tile
  width, `y` clamps to the tile's rows), no mipmaps, never sRGB-decoded
  (Maya's OpenGL path decodes any 8-bit texture a `GLSLShader` samples, so
  its preview binds a 16-bit promotion of the map -- the preview table):
  `lo, hi` are the bilinear blend of `R, G`, the coverage is the bilinear
  blend of each texel's boolean "the bit at the light's sub-bin is set"
  (with a source disc, the fraction of the disc's sub-bin span that is
  set). Sixteen loads per fragment for a point source — four texels × the
  bin and its interval neighbour × two layers — and twenty-four for a disc,
  which also reads the bin's other neighbour in case its span straddles the
  edge.
- **Shader** (per fragment, in the contact frame; `L` = source − fragment,
  or `−direction` scaled far away):

```
e = atan2(L.y, hypot(L.x, L.z));  φ = atan2(dot(L, b), dot(L, a)) mod 2π
ρ = asin(min(1, source_size / 2 / |L|))  or  source_angle / 2
k = floor(φ / step);  s = φ / step − k                 (position within bin k)
A = the four texels of tile k around (u, v);  N = the nearest of them
covφ = Σ_taps w_tap · fraction of [s − ρ, s + ρ]·16 covered by set bits of A_tap
                                                        (ρ = 0: the bit at floor(s·16))
mid_k = midpoint of N's first and last set sub-bins (a bin fraction)
side = s > mid_k ? +1 : −1;   B = the four texels of tile k + side;  mid_B likewise
t = both covered ? clamp((s − mid_k) / ((mid_B + side) − mid_k), 0, 1) : (A covered ? 0 : 1)
cot_k, cot_B = R blended over the COVERED taps, G over the taps with 2+ RUNS, × max_stretch
floating:  [cot_lo, cot_hi] = lerp(cot_k, cot_B, t)
grounded:  cot_lo = max_stretch;  run = number of 0→1 transitions in N's mask up to bit floor(s·16)
           cot_hi = (run >= 2 && G_k > 0) ? G_k : lerp(R_k, R_B, t)   (later runs hold their own top)
c = [cot(e + ρ), cot(e − ρ)];   cove = overlap(c, [cot_hi, cot_lo]) / width(c)
α_layer = covφ · cove;   an all-zero mask at every tap → 0
alpha = intensity · opacity · (1 − (1 − α_grounded) · (1 − α_floating))
```

  This is **one text, not a specification**: it is
  `pythontk/geo_utils/shadow_horizon.glsl`, which every engine and both DCCs
  run (see *The shared shader body* below), and `HorizonMap.alpha` beside it
  is the numeric oracle they are pinned to. Edit the `.glsl` and the `.py`
  together; edit a mirror and `sync_shadow_shaders.py --check` fails.

  The block runs once per layer and the union is the shadow. Only bins `k`
  and `k + side` are read for the interval; the coverage walks `k − 1`, `k`
  and `k + 1` so a disc straddling a bin edge is not clipped. `overlap` is the length of
  the intersection of two intervals; a zero-width interval gives 0, never a
  smoothstep. With `ρ = 0` both overlaps become point tests. Measured
  against the exact projection at random sources (one-texel-tolerant
  disagreement, `samples = 12`, `size = 192`): box 1.51 %, table 2.81 %,
  chair 5.28 % at the defaults — measured after the tap-blending
  corrections below, and down from 1.52 / 2.91 / 5.59 % before them.
  Sampling at the first sub-bin and lerping to `k + 1`, or degrees instead
  of cotangents, or one interval per bin, each measured at 15–20 % on the
  furniture fixtures.

  Three tap-blending rules **the reference itself** had wrong. Each shader
  had diverged from it independently — the HLSL was right about the first
  and the GLSL wrong, both were right about the other two — so no two of the
  three agreed, and the oracle was the odd one out. Fixed in `HorizonMap`
  and pinned by `test_shadow_horizon.TestTapBlending`:

  - **The later run's top blends only over taps that HAVE a second run.**
    `G` is the second run's top and is written `0` on a one-run texel, so
    blending it over every *covered* tap drags the top toward the zenith
    and lengthens the shadow. (The pseudo-code below said
    `cot_hi = run == 1 ? lerp(R_k, R_B, t) : G_k`, which is the broken
    form.)
  - **`nearest` breaks a weight tie toward the higher texel.** The nearest
    tap alone decides the grounded run index, so at a dead tie the choice
    flips which branch runs — not just a rounded value.
  - **Coverage counts every tap, not only the nearest.** The rule is "an
    all-zero mask at *every* tap → 0"; gating on the nearest tap zeroes a
    texel its neighbours cover.

### The projected map and the atlas

- The silhouette PNG is unchanged (top row = the light-side edge). A plane's
  UVs stay the polyPlane / quad defaults **remapped into its atlas rect**
  when packed, so a fallback viewer renders the tile with no transform. The
  DCC material per plane keeps its own shader and opacity chain and points
  its file node at the atlas PNG.
- `ShadowAtlas` (pythontk): equal-size square tiles in a grid, one atlas PNG
  per rig type per scene (`shadow_atlas_projected.png`,
  `shadow_atlas_horizon.png`), a gutter of 2 texels inset with
  `inset_atlas_rects`, rects published through `snap_atlas_rects`;
  `write_tile` rewrites one tile in place (Recalculate never repacks);
  `pack` repacks when a rig is added or removed. Mixed tile sizes take the
  largest cell.
- **Panel**: `Planes:` (Combined / Per object), `Atlas:` (Auto / Off / On)
  where Auto packs once two rigs of a type exist, and a **Pack Atlas**
  Utility action. Instancing has no DCC option: the engines instance planes
  that share a type and an atlas.

### Engines

- **Unity**: `ShadowPlaneImporter` rewires every plane of a model to one
  shared material per rig type on the package's `ShadowPlane.shader` (a
  legacy CG unlit-transparent shader with `multi_compile_instancing`, so it
  renders in Built-in and URP; HDRP keeps today's `HDRP/Unlit` rewire and
  the silhouette), pins the `_horizon` PNG (sRGB off, alpha-is-transparency
  off, no mipmaps, clamp, RGBA32 per platform) and swaps each plane's mesh
  for the first plane's so GPU instancing batches them. `ShadowPlaneController`
  gains the runtime: it resolves the source and contact by name, evaluates
  the model in `LateUpdate`, places the transform, and writes the per-plane
  block (`_Rect`, `_Mode`, `_Intensity`, `_HorizonRect`, `_HorizonParams`,
  `_HorizonRange`, `_MaxStretch`, and the frame as `_Origin` / `_AxisA` /
  `_AxisB` / `_AxisUp` with `_Source` / `_SourceRadius`);
  `RenderOpacityController` keeps writing `_BaseColor.a` into the same
  block. The horizon evaluation itself is `ShadowPlaneHorizon.hlsl`, a
  generated mirror that rides the controller through the deployer's
  longest-stem rule.
- **Viewer**: a Python pass `MeshConvert.apply_glb_shadows(edit,
  search_dirs)` in `fbx_to_glb` binds each record's textures from the loose
  files (the horizon PNG as a texture with a clamp / linear / no-mip
  sampler) and writes root `extras.shadow_web` = the v2 payload plus, per
  plane, the glTF `node` index of the plane, source and contact and the
  `texture` index of each map, and top-left rects. The packaged script
  `preview/scripts/shadow_rig.js` (a built-in `PreviewServer` script) reads
  it on `load`, builds one `ShaderMaterial` per plane (both types in one
  shader, `uMode`), merges planes that share a type and atlas and carry no
  fade track into an `InstancedMesh` with per-instance rect, frame and
  opacity attributes, and on `frame` evaluates the model and sets the
  placement and the source uniforms. Textures it creates are excluded from
  `disposeModel` the way `scene.environment` is.
### The shared shader body — one text, every consumer

`pythontk/geo_utils/shadow_horizon.glsl` **is** the evaluation above. There
is no second implementation of it: the WebXR viewer's GLSL and Unity's HLSL
are generated mirrors of that one file, spliced between markers by
`m3trik/scripts/sync_shadow_shaders.py` (`--check` is the drift gate,
`pythontk/test/test_sync_shadow_shaders.py` the suite pin), and Maya and
Blender assemble it at run time from `ShadowHorizon.shader_source(language)`.

The split is by capability, not by taste: a consumer that **is** a Python
process when it needs the shader assembles it and carries nothing; one that
is not — a browser, a Unity project — carries a mirror. That is
CODE_STANDARD §6's sanctioned duplicate, invoked exactly twice.

Three things stay per-engine because they cannot be anything else:

| Per-engine | Why |
|:--|:--|
| `SH_Fetch(col, row, xi, yi)` | glTF's top-left texture origin, Unity's **two** row flips (tile grid *and* `Load`) and Maya's own convention are irreducible. The body computes the grid cell and never a texture address. |
| the uniform block | three-in-one instancing props, `ShaderMaterial` uniforms, `.ogsfx` blocks and Blender push constants have nothing in common but their values. |
| the language prologue | `#define SH_HLSL` swaps `vec3`/`float3`, `mix`/`lerp` and `atan`/`atan2`. A *language*, never an engine — `shader_source("unity")` raises. |

The frame reaches the shader as an **origin and three world-space axes**
(`ShAlpha(g, worldPos, origin, axisA, axisB, axisUp, source, sourceSize)`),
not a world-to-contact matrix: every binding is then a `vec3`, which a Maya
`.ogsfx` uniform, a Blender push constant and a Unity instanced property are
all proven to carry, and Unity's instancing buffer loses fifteen floats per
plane. `source.w` picks position (1) or the direction the source **shines**
(0); `sourceSize` carries **full** widths, halved in the shader and nowhere
else.

Two rules the shared body enforces that the shipped shaders each broke
differently:

- **The fragment is projected onto the ground plane before `L` is formed.**
  The bake marches from height 0, while every rig lifts its plane by its own
  `GROUND_OFFSET` — so forming `L` from the fragment measures against a plane
  the map was never baked on. The body replaces the height with the map's
  `ground`, exactly as `HorizonMap.alpha` does.
- **The coverage integral walks the whole three-bin window** (`k − 1`, `k`,
  `k + 1` — 48 sub-bins). The viewer's GLSL inspected three sub-bins, which
  clipped any penumbra wider than that.

### The DCC previews (phase 6)

Both DCCs show a **Horizon** rig's shadow the way the engines will -- R7 met
for the horizon type -- from the same `shadow_horizon.glsl` body, assembled at
run time by `ShadowHorizon.shader_source()` behind a per-DCC host prologue.
Same name (`ShadowPreview`: `attach` / `detach` / `toggle` /
`prepare_for_export`, the panel's **Live Horizon Preview** box), same
behaviour, a different mechanism where the DCCs leave no choice:

| | Maya (`mayatk/rig_utils/shadow_preview.py`) | Blender (`blendertk/rig_utils/shadow_preview.py`) |
|:--|:--|:--|
| Mechanism | a hardware **material**: `dx11Shader` + `.fx` on DirectX 11, `GLSLShader` + `.ogsfx` on OpenGL Core Profile, the effect written beside the horizon PNG so a saved scene rebinds it; legacy OpenGL (Pixel Shader 4) refused with the message that says so. Both bind the map's **16-bit promotion** (`<map>_preview16.png`, written beside it, refreshed after a re-bake): VP2's OpenGL path sRGB-decodes any 8-bit texture a `GLSLShader` samples -- Raw colour space, colour management and `MayaGammaCorrection` change nothing (measured: R came back as the piecewise sRGB decode of its byte, A untouched) -- and a 16-bit texture has no sRGB format to decode | a `gpu.shader.create_from_info` **overlay** in a `SpaceView3D` `POST_VIEW` handler (EEVEE nodes cannot bit-test a data map); the plane hidden while it stands in; refused headless, where the GPU module has no backend |
| What it borrows | the plane's shading-group *membership* only -- the real network stays wired and `_plane_shading_groups` reads the snapshot, so the silhouette's file node, the opacity chain and the export record are the same with it on | the plane's viewport visibility only; nothing the record reads |
| The frame | `O` / `A` / `B` / `Up` driven live by `decomposeMatrix` + `vectorProduct` nodes off the contact's world matrix; up is the contact's `+Y` | the contact's **local** frame (`X`, `Y`, up `Z` -- not the record's exporter-axes `HORIZON_FRAME`) through `matrix_world`, refilled per draw |
| Export | the `"shadow"` preparer detaches every preview, then republishes | the `"shadow"` preparer (`FbxUtils.register_export_preparer`, a session registry mirroring mayatk's, new) stands it down, then republishes |
| Verified, drawn | `test/shadow_preview_device_check.py`: one **fresh** GUI Maya per device via `MAYA_VP2_DEVICE_OVERRIDE`, playblast alpha vs `HorizonMap.alpha` on 8474 pixels -- **DirectX 11: mean \|d\| 0.0001, p98 0.001, 0.02 % over 0.05; OpenGL Core Profile: mean \|d\| 0.0001, p98 0.001, 0.00 % over 0.05**, 33 of 33 checks each (before the 16-bit texture, OpenGL drew the grounded layer only: the sRGB decode pushed the floating layer's `[G, R]` band under every source elevation while the grounded layer's one-sided band survived) | `test/shadow_preview_gui_check.py` (windowed): 16132 pixels in display space (overlays blend in linear light, the viewport encodes) -- **mean \|d\| 0.0012, p98 0.004, 0.00 % over 0.05**, umbra 1.016 x the reference's |
| Verified, headless | `test/test_shadow_preview.py` (16): the device classifier, both effect texts, the accessor guard, the preparer, the 16-bit promotion (every sample the map's byte times 257, refreshed only after a re-bake) | `test/test_shadow_preview.py` (12): the frame math, the uniform block, the refusal, the preparer |

Facts each of these cost a launch to learn: Maya's `texelFetch` / `Load`
reads the PNG **top-down** on both devices (Unity's `Load` is the other way
round), an `.ogsfx` sampler needs explicit `TEXTURE_MIN/MAG_FILTER` or the
texture is incomplete, a Blender `GPUUniformBuf` must outlive `batch.draw`
(a freed one samples as zeros -- a running overlay that drew nothing), and a
Blender overlay is read back through the viewport's display transform.

- **Reference**: `HorizonMap.alpha(points, light, ...)` in pythontk is the
  oracle both engine shaders are pinned to — really pinned, as of
  2026-09-03, and by rendered pixels rather than by inspection:

  | Engine | Harness | Measured against `HorizonMap.alpha` |
  |:--|:--|:--|
  | GLSL, positional | headless Edge through Playwright, the real `viewer.html` (`pythontk/test/test_shadow_web.py`) | 48 ground points — 22 in shadow, 18 in the penumbra — worst \|shader − reference\| **0.0138** |
  | GLSL, directional | same | 54 points — 34 in shadow, 29 in the penumbra — worst **0.0189** |
  | HLSL (Unity) | Unity batch-mode render (`unitytk/test/test_shadow_plane_runtime_integration.py`) | 7143 rendered pixels, 1306 in shadow: mean \|d\| **0.0008** (point source) / **0.0002** (disc), p98 0.002 |

  A directional source is its own row because it is its own evaluation: one
  bearing and one elevation for the whole plane instead of a per-fragment
  pair, and a `w = 0` uniform the shader negates — a sign error there is
  invisible to every positional test. Nothing rendered that path before; the
  viewer's directional coverage was the *projected* rig's placement, and
  Unity's horizon tests are all positional. Its fixture must also sit **off**
  a bin centre: a directional source gives every fragment the same `s` at
  once, so a centred bearing puts `s` at exactly 0.5 — a dead tie in `side`
  and a boundary in `floor(s · 16)`, decided by whether the GPU's float32
  `atan2` lands a hair either side of numpy's float64.

  Until then this section claimed a pin neither test provided. The viewer's
  compared against a hand-written analytic pole answer at `delta = 0.1`, and
  Unity's against `HorizonFixture` — a numpy transcription of
  `ShadowPlane.shader` itself, so it pinned the shader to a copy of the
  shader. That transcription is deleted; its *bake* half stays, because a map
  with two runs per bin and a floating disc is a fixture worth having, and it
  now feeds `HorizonMap.from_rgba`. The viewer's analytic answer stays too,
  demoted to a **second** assertion: the reference is checked against the
  closed form in the same pass, so a wrong reference cannot pass by agreement
  alone.

## Risks

- Two members in one bin merge into their hull; two layers on one ray
  over-block the gap; a jump in height between bins bevels. Bounded by K,
  measured per prop by the reference test, escalated to G when a prop family
  needs it.
- The data map must survive both importers as data: Unity pins flags only on
  a loose file's first import, and the sub-asset copy cannot be pinned; the
  viewer must rebind from the manifest before anything samples. The phase-2
  integration tests are the guard, and the lightmap carrier is the precedent.
- Platform texture compression on mobile is off by policy until measured; the
  VRAM table is what that policy costs.
- A deforming target is out of scope for every baked type; the doc says so,
  and option E is the answer when it is needed.
