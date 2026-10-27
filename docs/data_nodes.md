# Scene data nodes (`DataNodes`)

Every piece of scene-wide metadata a tool keeps -- the shot store, a lightmap
manifest, the keyed render effects -- is a **scene record**: one JSON string,
declared once, stored on one of two carrier nodes. A **private** record stays
in the scene; a **deliverable** record rides every export.

| Part | Where | What it decides |
|---|---|---|
| **The declaration** | `ptk.SceneRecords` ([`scene_records.py`](https://github.com/m3trik/pythontk/blob/main/pythontk/core_utils/engines/scene_export/scene_records.py)) | a record's key, scope, version, kind, owner, readers and description -- once, for both DCCs, the GLB build and the Unity importers |
| **The store** | `mtk.DataNodes` (this page) / `btk.DataNodes` ([Blender](https://github.com/m3trik/blendertk/blob/main/docs/data_nodes.md)) | where a record's text lives -- strings only |
| **The producers** | `FbxUtils.PRODUCERS` (one row per deliverable per DCC) | how a deliverable is computed from the live scene -- returned, never written |

Encoding, the version envelope, tolerant decoding and clear-on-empty belong to
the record (`RecordSpec.load` / `save` / `make`), so the two DCC stores cannot
diverge on semantics. Nothing outside `ptk.SceneRecords` spells a record key.

```python
import pythontk as ptk
from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.node_utils.data_nodes import DataNodes

ptk.SceneRecords.LIGHTMAPS.load(DataNodes)             # payload dict, or None
ptk.SceneRecords.LIGHTMAPS.save(DataNodes, payload)    # falsy payload CLEARS
FbxUtils.publish_authored({ptk.SceneRecords.LIGHTMAPS: payload})  # + handoff
```

## The two nodes

```text
data_internal              network node, name locked            PRIVATE -- never exported
├─ <record key>            string attr: the record's JSON       one per private record
├─ audio_clip_<track_id>   enum attr "off:on", keyed            Audio Clips' authoring curves
└─ keepAlive               message attr <- time1.message        hidden from values() / dump()

data_export                transform, name + channels locked,   DELIVERABLE -- rides every export
│                          hidden in the Outliner
├─ data_exportShape        locator, zero scale (never drawn)
├─ <record key>            string attr: the record's JSON       one per deliverable record
└─ emissiveGroup_<name>    float attr 0-1, keyable              each emissive group's weight
```

- **A record is one string attribute named exactly for its key.** An
  *enveloped* record -- every deliverable, plus `lightmap_probe` and
  `lightmap_bake_state` -- is a JSON object whose `version` comes first; a
  *bare* one is its payload as-is (a mapping or a list), versioned by its
  declaration alone.
- **Clearing keeps the attribute, empty** (`""` reads as absent), and a clear
  never creates a node. `remove` deletes the attribute (what `migrate` does to
  an old key).
- **A reader reads one version.** `RecordSpec.load` ignores a newer version
  than it knows (warned); the deliverable readers (Unity, the GLB build)
  refuse any version but the declared one -- the file is re-exported.
- **The other attributes are not records**: `read` skips them; `values` and
  `dump` include them.

Both nodes are created on demand **outside the undo queue** (a tool's undo
would otherwise delete the carrier with every record written since).
`keepAlive` is a permanent input: Maya deletes a `network` node whose only
input's source goes (cutting an audio track's last key once took every record
with it). Names are locked, nodes are not; `ensure_export` heals a plain
`data_export` to the full protection set, so *Optimize Scene Size* never
deletes it. A duplicate short name resolves to the shallowest DAG path. A
referenced module keeps its own `NS:data_export`:
`get_export_nodes()` returns every carrier so an export ships them all, and a
reader binds the scene's own copy of a record first
(`ptk.FbxMedia.reader_records`).

## What each record holds

Generated from `ptk.SceneRecords.describe()` by
`m3trik/scripts/sync_scene_records.py` -- edit the declaration, never these
tables. *Version* `(bare)`: no envelope. *Kind*: `authored` records are
republished at authoring time and by every export pipeline; `derived` ones are
refreshed before every write, hand-offs included; a `transient` one exists for
one write and is cleared after it. *Reads*: the records its producer reads.
*Crosses*: what another scene's copy becomes beside this one's
([Crossing into another scene](#crossing-into-another-scene)). Every
deliverable's fields, in the FBX and the GLB:
[`DELIVERABLE_FORMAT.md`](https://github.com/m3trik/pythontk/blob/main/pythontk/core_utils/engines/scene_export/DELIVERABLE_FORMAT.md).

<!-- scene-records:begin -->
**`data_export`** -- the deliverable records:

| Record | Version | Kind | Owner | Reads | Read by | GLB block | Holds |
|---|---|---|---|---|---|---|---|
| `materials` | 1 | derived, transient | Export | -- | unity, glb | -- | every material as glTF 2.0 states it: its factors, the embedded map behind each, alpha mode, double-sidedness and unlit |
| `animation` | 1 | derived | Shots | -- | unity, glb, verifier | v2 | shot definitions -- per clip its frame range ('start'/'end', the take the clip is cut from) and any description; the scene fps and the declared clip mode; the clip name is the join key to the imported animation clip |
| `audio` | 2 | derived | Audio Clips | `animation` | unity | -- | audio events with the frames they fire on, scoped to their clip, and 'fps', the rate those frames count at |
| `lightmaps` | 2 | authored | Lightmap Baker | -- | unity, glb | v2 | per-object baked-lightmap records: map file name, uvIndex, scaleOffset, and the object's scene hierarchy (which tells apart objects that share a name); and the bake's reflection probe, when it captured one: its HDR's file name, capture point and projection box; and the lights the maps were baked from, for the highlights a lightmap cannot hold -- every length times unit_scale is metres |
| `shadows` | 2 | authored | Shadow Rig | -- | unity, glb | v2 | projected-shadow planes: per plane its node name, type, silhouette texture and intensity, the source and contact it follows and the projection model's constants, and, when packed or baked, its atlas rect and horizon map -- every length times unit_scale is metres |
| `articulation` | 1 | authored | Articulated Rig | -- | unity, glb | v1 | articulated rigs: per rig its joints (node name, parent, rest translate and orient, rotate order, channels with limits and grab weights) and the parts a hand grabs, each with the joint it rides |
| `emissive_groups` | 2 | authored | Emissive Groups | -- | unity | -- | named emissive material groups and their weights |
| `render_effects` | 1 | derived | Render Effects | `animation` | glb, verifier | -- | each node's keyed render effects: 'visibility' as stepped on/off frames, the authored 'opacity' ramp, and the 'highlight' ramp with its colour stops ('highlight_color', 'highlight_color_dim'); with 'fps' and each take's first/last authored frame ('clip_span'); on a shot scene keying none, the exporter's measured whole-timeline clip origin alone |
| `handoff` | 3 | derived | Export | -- | unity, glb | -- | the standalone-reader contract: the approved scene part by part and the lighting it was approved under |

**`data_internal`** -- the private records:

| Record | Version | Owner | Crosses | Holds |
|---|---|---|---|---|
| `shot_store` | 1 (bare) | Shots | domain merge, hand-off | the shot store's full app state: its shots (id, name, start, end, objects, metadata, locked, description), the hidden and pinned objects, markers, locked gaps, the manifest's edits and the panel's settings |
| `key_stash` | 1 (bare) | Key Stash | domain merge | the clip manifest of parked keys: per clip its id, label, objects, parked curves, source shot and metadata |
| `smart_bake_sessions` | 2 (bare) | SmartBake | unites | LIFO stack of bake-session restore manifests, oldest first, each under its 'id': what the bake replaced, to put it back |
| `hierarchy_baseline` | 1 (bare) | Hierarchy check | keeps its own | the export hierarchy baseline (a HierarchyBaseline record): the exported node paths, their hash and count, and the scene file that recorded them |
| `emissive_registry` | 1 (bare) | Emissive Groups | domain merge, hand-off | the group registry: per group its slot, default weight and keyed attr, the membership encoding and the retired slots |
| `render_effects_bindings` | 1 (bare) | Render Effects | unites | legacy, Maya only: the bindings the retired viewport material mode left, per material channel its object and the authored values -- read to restore them, then cleared |
| `texture_combine` | 1 (bare) | Texture Combine | unites | texture combines by name: per mesh its original UVs and face materials, the source maps read, the settings and overrides |
| `light_rigs` | 1 (bare) | Light Rigs | unites | light rigs by group: the rig applied, its fingerprint, the plugs it overrides and the rig a swap replaced |
| `audio_file_map` | 1 (bare) | Audio Clips | unites | track id to audio file path |
| `lightmap_dirs` | 1 (bare) | Lightmap Baker | unites, hand-off | lightmap file name to the folder it was written to |
| `lightmap_writers` | 1 (bare) | Lightmap Baker | unites, hand-off | lightmap file name to the scene file that wrote it |
| `lightmap_probe` | 1 | Lightmap Baker | keeps its own | the reflection probe the lightmap bake captured: file name, capture point, projection box and unit_scale |
| `lightmap_bake_state` | 1 | Lightmap Baker | keeps its own | per object the state its lightmap was baked against (transform, bounds, geometry and UV fingerprints, material, atlas cell) and the lighting each bake saw (a BakeBaseline record) |
<!-- scene-records:end -->

## What an export carries

### FBX

| In the file | What |
|---|---|
| the `data_export` node | each non-empty deliverable record as a string user property (its JSON). `handoff` is stamped last, from what the node then holds, so it describes the rest; `materials` exists for that write alone |
| `data_export.emissiveGroup_<name>` | each keyed group's weight curve (FBX animates only an exported node's attributes) |
| each object with a keyed render effect | `opacity` / `highlight` (and `highlightColor` / `highlightColorDim`) properties, their curves on a child `<object>__opacity` / `<object>__highlight` node's `scale.x` -- staged for the write, removed after |
| embedded media | every file a record names: material maps, lightmaps, the reflection probe and its highlight copies, shadow maps, each audio event's file |

Never carried: `data_internal` (a `network` node never serialises into an
FBX). The Scene Exporter and hand-offs narrow the `export_scoped` records
(`materials`, `lightmaps`, `shadows`) to the nodes the write carries; the
scene keeps its whole copy.

**The finishing passes.** The Scene Exporter and the Unity bridge run
`FbxUtils.finish_deliverable` (`ptk.MeshConvert.finish_fbx_deliverable`) on
the written file: embed the media above, give an instance whose pivots differ
from its siblings' a mesh of its own, drop StingrayPBS's environment maps,
drop each baked object's `lightmapInfo` marker (its `lightmaps` entry states
it whole) and the carrier's empty channels, and restate `handoff`. A plain
**File ▸ Export** runs none of them: it ships the markers and the empty
channels, and embeds nothing a record names.

### GLB

A GLB is built from the FBX (`ptk.GlbPipeline`: the Scene Exporter's GLB
output, the WebXR preview). Each record is restated in glTF's own terms, then
the FBX's copies leave the node extras (`MeshConvert.strip_fbx_records`) --
the file states each thing once:

| Record | In the GLB |
|---|---|
| `animation` | the animations cut per shot (as the clip mode says), and `extras.animation`: every shot as the record states it, whatever clips the file holds -- a shot with no clip of its own (a `full` file) plays its window of the whole-timeline clip -- and each clip's `zero_frame` |
| `materials` | the glTF materials themselves |
| `lightmaps` | each lightmap in its material's texture slot, the probe in a bufferView, and `extras.lightmaps` |
| `shadows`, `articulation` | `extras.shadows`, `extras.articulation`, bound to node and texture indices |
| `render_effects` | animation channels: a STEP `scale` for visibility, `KHR_animation_pointer` for opacity and highlight |
| `handoff` | `extras.handoff`, in the GLB's addresses |
| `audio`, `emissive_groups` | nothing -- Unity reads them from the FBX |

### USD

The Scene Exporter's USD output writes `data_export` as a prim with its
string attributes; that every consumer receives them is not yet verified (the
run says so once).

Nothing rides beside any file. The Scene Exporter keeps its own record of each
export -- the hierarchy it checks the next one against and a `data_export`
snapshot -- in a per-user store (`ptk.SceneDataSidecarBase`): the exporter's
history, never part of the deliverable.

### Getting the carrier into the export set

Only an **export-all** picks it up by itself (`Visible` collects geometry;
`Selected` ships the user's picks):

1. **Scene Exporter** (recommended) -- the default-on *Export Scene Data Node*
   task publishes the records and adds every carrier, in every export mode.
2. **A hand-off whose consumer reads the records** -- `MayaExportMixin`'s
   `include_data_export`: on for `WebXrPreview` and `UnityBridge`, off by
   default. The carrier joins the set (never the strip-materials duplicate)
   and is never *created* just to ship.
3. **File ▸ Export Selection** -- select `data_export` yourself.

## How records are written

**A producer returns, the snapshot commits once.** Each `PRODUCERS` row names
a classmethod that reads the scene and returns the record -- or `None`, which
CLEARS it (deleting the last shot must not leave its takes riding into the
next export): `animation` `ShotStore.produce_export_records`, `audio`
`AudioClips`, `render_effects` `RenderEffects`, `shadows` `ShadowRig`,
`articulation` `ArticulatedRig`, `emissive_groups` `EmissiveGroups` (tentacle's tool,
registered through its entry points: `FbxUtils.producers()` merges registered rows),
`lightmaps` `LightmapRecords`, `materials` `MaterialRecords` (each
`export_record(ctx)`). `ptk.ExportSnapshot.assemble` orders them by `after`
and hands each the `ptk.ExportContext` -- the exporter's decisions as inputs
(`clip_mode`, `clip_span`, `rendering`, `scope`), and, through
`ctx.record(spec, DataNodes)`, what the producers before it returned. `commit`
writes every record in one pass, then stamps `handoff`. Publishing twice with
one context produces the same records.

| Path | Call | Refreshes |
|---|---|---|
| Scene Exporter | the *Export Scene Data Node* task: `FbxUtils.publish(ctx)` with the run's decisions (one publish before the write when the task is off) | every record |
| Any FBX export | the `kBeforeExport` hook; authoring a shot, an audio track or an articulated rig, and the shadow preview, opt in (`FbxUtils.enable_export_producer`; `ShotStore` / `AudioClips.disable_auto_export()` opts out) | the opted-in records -- it never adds the carrier to the set |
| Hand-off bridge | `FbxUtils.export_prepared(export_context(mode=HANDOFF, scope=...), stagers=...)` | the `derived` records only -- a bridge is not the authority on an authored one |
| Authoring time | `FbxUtils.publish_authored({spec: payload})` from the tool | the tool's own records |

**Stagers** change the scene for a write and undo it after; they produce no
record. `FbxUtils.STAGERS`: `render_effects` (the curve proxies above) and
`lightmap_folder_hints` (a one-way migration). Session stagers join through
`register_export_stager(name, prepare, finish)` -- the shadow preview stands
down for the write. The Scene Exporter stages from its publishing task
(`FbxUtils.stage()`, its finish a deferred restore) so its checks see what the
write will; the write's bracket stages again and finishes after -- so every
`prepare` and `finish` is idempotent, and every phase keeps the selection.
Outside a bracket, `publish` runs the session stagers' `prepare` alone.

**Old keys migrate once.** A renamed record names its earlier keys
(`RecordSpec.renamed_from`), and retired deliverables are listed in
`ptk.SceneRecords.RETIRED`; `DataNodes.migrate()` moves the one and removes the
other in every commit and when a scene opens (`kAfterOpen`: installed by
`MayaUiHandler`, or in mayapy and batch by the private carrier's first touch).
No reader reads an old key.

**Paths and writer stamps.** A `paths` record (`audio_file_map`,
`lightmap_dirs`, `lightmap_writers`, the baseline's `scene`) spells each path
relative to the scene file's project (`DataNodes.project_root()`; absolute
only on another drive or while unsaved), re-spelled by every route that moves
it (a save elsewhere or a copy: `install_path_rebase`; a hand-off; a module
merge). A Save As copy carries every record verbatim, so a record that must
tell the copy from its source stamps its writer (`RecordSpec.stamps`,
`DataNodes.writer_stamp()`) and asks `DataNodes.written_here(stamp)`; a first
save stamps every `""` with the file it writes.

**Files written since the last save.** The same save hook, after the
re-spelling, commits `ptk.PendingFiles`: a tool that writes files the saved
scene reads (the Lightmap Baker, [Saved with the scene](lightmap_baker.md#saved-with-the-scene))
writes them to a session folder, and the save of the open scene moves the ones
it still reads home -- a Save As whose source is still on disk without
writing over a file the source reads. An autosave or a `.ma` export is a copy
and commits nothing; an open or a new scene (`kAfterOpen` / `kAfterNew`) drops
them.

## Crossing into another scene

A record also declares what it becomes when another scene's copy arrives
beside this scene's (`RecordSpec.merge`, `portable`) -- one engine,
`ptk.RecordTransfer`, whatever the route:

| Route | Entry point | What happens |
|---|---|---|
| A referenced module is imported | Reference Manager *Unlink and Import* (`ReferenceManager.import_references(scene_data=...)`) | The module's carriers merge into this scene's (`DataNodes.merge_carriers`) or go (`discard_carriers`). The panel asks only when a merge would keep something (`merge_plan(...).is_empty`): **Yes** merges, **No** drops, **Cancel** leaves the reference linked. Names are respelled to where the import put each node. |
| A DCC hand-off | the Blender bridge's *Include Scene Data* (`INCLUDE_SCENE_DATA`) | Every `portable` record rides the sidecar -- the shot store under `shots`, the rest under `records` (`DataNodes.transfer_sections`) -- and lands through the same rules (`receive_sections`). |

The rules, by *Crosses*:

- **re-derived** (every deliverable): the merged scene publishes it afresh
  (`FbxUtils.publish(only=...)`) -- the other copy spells names as its own
  scene did. A transient record is not produced outside a write.
- **keeps its own**: it describes its own scene; the other copy is dropped.
- **unites**: entries combine by identity, this scene's winning a collision; a
  LIFO stack keeps this scene's newest on top.
- **domain merge**: a codec in `ptk.SceneRecords.CODECS` -- the shot store
  renumbers incoming shots (a clashing name arrives numbered), the key stash
  gives parked clips fresh ids and follows their shots, the emissive registry
  re-slots a group whose slot is taken (and logs the binding to re-wire).

Each crossing returns a `ptk.TransferContext` whose `notes` are logged:
nothing is renamed or dropped silently. What a record keeps **beside** itself
belongs to its DCC owner, a row in `DataNodes.OWNERS` (shot store, key stash,
SmartBake sessions, emissive registry) whose optional hooks the engine calls:
`transfer_out` / `transfer_in` (the emissive groups send their face
membership), `merge_carrier` / `discard_carrier` (a discarded stash's parked
curves are deleted). The carrier's non-record attributes -- an audio track's
keyed enum, a group's keyed weight -- move to this scene's carrier with their
curves.

## Adding a record

1. **Declare it** in `ptk.SceneRecords`: key, scope, version, owner, a one-line
   description, `kind` (`derived` if computed from curves an artist edits
   between pushes), `after` and `consumers`.
2. **Deliverable?** Write a pure `export_record(cls, ctx)` -- read the scene,
   return `spec.make(payload)` or `None` -- add one `FbxUtils.PRODUCERS` row
   per DCC, and publish at authoring time with `FbxUtils.publish_authored`.
3. **Private?** `spec.load(DataNodes)` / `spec.save(DataNodes, payload)` is the
   whole API. Declare its crossing: `merge` (`UNION` / `CODEC` / `OWN`),
   `merge_key` for a list, `respell=False` for a payload naming no scene node,
   `portable=True` to ride a hand-off, `paths=True` for path values. State kept
   beside it? One `DataNodes.OWNERS` row per DCC.
4. **Read by Unity?** Add the channel to `UnitytkSettings.cs` and `"unity"` to
   `consumers`, then read it as an FBX user property on the `data_export`
   GameObject -- see
   [unitytk's templates README](https://github.com/m3trik/unitytk/blob/main/unitytk/templates/README.md).
5. Run `python m3trik/scripts/sync_scene_records.py`: these tables,
   `DELIVERABLE_FORMAT.md`, the generated JS and C# readers, and the gates
   (Unity's channels, the two DCCs' producer tables).

> See **[shot_export_unity.md](shot_export_unity.md)** for the Shots → FBX →
> Unity contract (the clip-name join, the C# reader, coexistence with Audio).

## API quick reference

| Member | Purpose |
|---|---|
| `ptk.SceneRecords.<RECORD>` | the declaration (`.key`, `.scope`, `.version`, `.kind`); `.load(DataNodes, default)`, `.save(DataNodes, payload)`, `.make(payload)`, `.is_present(DataNodes)`, `.clear(DataNodes)` |
| `FbxUtils.publish_authored({spec: payload})` | commit records in hand (authoring time), `handoff` restamped; no stager runs |
| `FbxUtils.publish(ctx=None, only=None)` | assemble every producer's record and commit once; returns the `ptk.ExportSnapshot` |
| `FbxUtils.export_context(mode=PIPELINE, **decisions)` | a context with this scene's provenance; decisions `clip_mode`, `clip_span`, `rendering`, `scope` |
| `FbxUtils.export_prepared(ctx=None, only=None, stagers=None, plan=None)` / `stage(names=None)` | the bracket: stage, publish (given a context or `only`), finish after the block; `stage` alone runs every `prepare` now |
| `FbxUtils.enable_export_producer(spec)` / `register_export_stager(name, prepare, finish)` | the session opt-ins |
| `FbxUtils.finish_deliverable(file_path, glb=None)` | the finishing passes on a written FBX |
| `DataNodes.read` / `write` / `remove` / `values` / `discard_if_empty` | the store contract (`ptk.SceneStoreBase`), per scope; a falsy `text` clears and never creates |
| `DataNodes.dump(decode=True)` / `format_dump()` / `dump_export_nodes()` | what the two carriers hold, by carrier; every deliverable carrier, by node |
| `DataNodes.ensure_internal()` / `ensure_export()` | get-or-create each node (idempotent, healing) |
| `DataNodes.get_internal_node(create=True)` / `get_export_node(create=True)` / `get_export_nodes()` | resolve a carrier (`create=False`: never create); the plural is for shipping |
| `DataNodes.transfer_sections(spell, objects)` / `receive_sections(manifest, resolve=None, **adapters)` | the portable records as hand-off sections, and landing them |
| `DataNodes.carriers_in(namespace)` / `merge_plan(carriers)` / `merge_carriers(carriers, rename=None)` / `discard_carriers(carriers)` | another scene's carriers: find them, preview a merge, merge or drop them |
| `DataNodes.project_root()` / `rebase_paths(old, new)` / `install_path_rebase()` / `writer_stamp()` / `written_here(stamp)` | path spelling and writer stamps |
| `DataNodes.OWNERS` | record key -> the DCC class keeping state beside it (its crossing hooks) |

Legacy: pre-`DataNodes` audio carriers and the single-enum `audio_trigger`
schema migrate through `mayatk.audio_utils.migrate.migrate_legacy_triggers`; a
record still stored as a retired `mirror_attr` proxy pair is healed on its
next write (a plain attribute replaces it), clear or remove (both dropped).
