# Scene records (`DataNodes`)

Every piece of scene-wide metadata a tool keeps -- the shot store, an audio
manifest, a lightmap manifest, the keyed-visibility tracks -- is a **scene
record**. The model has three parts, and each lives in exactly one place:

| Part | Where | What it decides |
|---|---|---|
| **The declaration** | `ptk.SceneRecords` (`pythontk/core_utils/scene_records.py`) | a record's key, scope, version, kind, owner, readers and description -- once, for both DCCs, the GLB readers and the Unity gate |
| **The store** | `mtk.DataNodes` (this module) / `btk.DataNodes` | where a record's text lives in the scene -- two carrier nodes, strings only |
| **The producers** | `FbxUtils.PRODUCERS` (one row per record per DCC) | how a record is computed from the live scene -- returned, never written |

Encoding, the version envelope, tolerant decoding and clear-on-empty belong to
the record (`RecordSpec.load` / `save` / `make`); the store knows only
`read` / `write` / `values` per scope, so the two DCC mirrors cannot diverge on
semantics. Nothing outside `ptk.SceneRecords` spells a channel name.

```python
import pythontk as ptk
from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.node_utils.data_nodes import DataNodes

ptk.SceneRecords.LIGHTMAPS.load(DataNodes)             # payload dict, or None
ptk.SceneRecords.LIGHTMAPS.save(DataNodes, payload)    # falsy payload CLEARS
FbxUtils.publish_authored({ptk.SceneRecords.LIGHTMAPS: payload})  # + handoff
```

## The two carriers

| Node | Scope | Type | Role |
|---|---|---|---|
| `data_internal` | `ptk.Scope.PRIVATE` | `network` | Scene-persistent state that must never ship. A `network` node never serialises into an FBX, so the guarantee is structural. |
| `data_export` | `ptk.Scope.DELIVERABLE` | locked, viewport-invisible, Outliner-hidden `transform` (zero-scale `locator` shape) | The in-band metadata surface: its string attrs ride into the FBX as user properties, into a GLB as node extras, and Unity's importers read them. |

Implementation details that matter:

- **Both are created on demand, outside the undo queue.** A carrier created
  inside a tool's undo chunk was deleted by that chunk's undo, with every
  record other tools had written since (measured 2026-09-15).
- **`data_internal` has a permanent keep-alive input** (`time1.message` into a
  message attr). Maya deletes a `network` node when the source of its only
  input is deleted, and a keyed audio-track enum's curve was that input: cutting
  the last key took the carrier and every record on it (measured 2026-09-18).
  The keep-alive attr is hidden from `values` and `dump`.
- **Names are locked, the nodes are not**, so records stay writable.
  `ensure_export` heals a pre-existing plain transform to the full protection
  set (locator shape, locked channels, locked name, `hiddenInOutliner`), so
  *Optimize Scene Size* never deletes it and it never draws an Outliner row.
- **A duplicate carrier short name resolves to the root.** An imported copy of
  `data_export` under a group makes every bare-name plug query ambiguous
  (`getAttr` silently returns a *list*); every read and write resolves the
  **shallowest DAG path**, ties broken lexically.
- **Shipping is plural.** A referenced module publishes onto its own
  `NS:data_export`; `get_export_nodes()` returns every carrier so an export
  set includes them all (a single-carrier resolver once shipped an assembly
  whose whole lightmap manifest lived one namespace away, and it previewed
  unlit).

Blender uses the structurally equivalent primitives: a scene ID property group
for PRIVATE and an Empty for DELIVERABLE -- see
[blendertk's data_nodes.md](https://github.com/m3trik/blendertk/blob/main/docs/data_nodes.md).

## Records in use

Generated from `ptk.SceneRecords.describe()` by
`m3trik/scripts/sync_scene_records.py` -- edit the declaration, never this
table. The same script fails CI when the channels unitytk's importers read
(`UnitytkSettings.cs`) differ from the records declared with the `unity`
reader, and when the two DCCs' `FbxUtils.PRODUCERS` name different records
outside its divergence ledger.

<!-- scene-records:begin -->
| Record | Carrier | Version | Kind | Owner | Reads | Read by | Crosses | Holds |
|---|---|---|---|---|---|---|---|---|
| `shot_metadata` | `data_export` | 1 | authored | Shots | -- | unity, glb, verifier | re-derived | shot definitions -- per clip its frame range ('start'/'end', the take the clip is cut from), objects, and any description and section; the scene fps and the declared clip mode; the clip name is the join key to the imported animation clip |
| `fbx_takes` | `data_export` | 1 (bare) | authored | Shots | -- | glb, verifier | re-derived | the take list an older file carries, one per shot -- superseded by shot_metadata's per-clip ranges and no longer written -- *legacy: superseded by `shot_metadata`* |
| `audio_manifest` | `data_export` | 2 | authored | Audio Clips | `shot_metadata` | unity | re-derived | audio events with the frames they fire on, scoped to their clip |
| `lightmap_metadata` | `data_export` | 1 | authored | Lightmap Baker | -- | unity, glb | re-derived | per-object baked-lightmap records: map file name, uvIndex, intensity, scaleOffset, and the object's scene hierarchy (which tells apart objects that share a name) |
| `shadow_metadata` | `data_export` | 2 | authored | Shadow Rig | -- | unity, glb | re-derived | projected-shadow planes: per plane, the plane node name, its silhouette texture file name, and the authored intensity |
| `emissive_groups` | `data_export` | 1 | authored | Emissive Groups | -- | unity | re-derived | named emissive material groups and their weights |
| `visibility_tracks` | `data_export` | 1 | derived | Render Effects | `shot_metadata` | glb, verifier | re-derived | keyed visibility per node, as stepped on/off frames, with the authored opacity ramp and each take's first/last authored frame |
| `handoff` | `data_export` | 1 | derived | Export | -- | -- | re-derived | the standalone-reader contract: what each channel present on the carrier holds |
| `shot_store` | `data_internal` | 1 (bare) | authored | Shots | -- | -- | domain merge, hand-off | the shot store's full app state |
| `key_stash` | `data_internal` | 1 (bare) | authored | Key Stash | -- | -- | domain merge | the clip manifest of parked keys |
| `smart_bake_sessions` | `data_internal` | 2 (bare) | authored | SmartBake | -- | -- | unites | LIFO stack of bake-session restore manifests |
| `hierarchy_baseline` | `data_internal` | 1 (bare) | authored | Hierarchy check | -- | -- | keeps its own | the export hierarchy baseline (a HierarchyBaseline record) |
| `emissive_groups` | `data_internal` | 1 (bare) | authored | Emissive Groups | -- | -- | domain merge, hand-off | the group registry: slots, defaults, encoding |
| `render_effects_bindings` | `data_internal` | 1 (bare) | authored | Render Effects | -- | -- | unites | the viewport material bindings a preview drives, so a suspend and rebind round-trips |
| `audio_file_map` | `data_internal` | 1 (bare) | authored | Audio Clips | -- | -- | unites | track id to audio file path |
| `lightmap_dirs` | `data_internal` | 1 (bare) | authored | Lightmap Baker | -- | -- | unites, hand-off | lightmap file name to the folder it was written to |
| `lightmap_writers` | `data_internal` | 1 (bare) | authored | Lightmap Baker | -- | -- | unites, hand-off | lightmap file name to the scene file that wrote it |
<!-- scene-records:end -->

Beside the records, two tool-owned attribute families live on the carriers:
the audio tool's keyed `audio_clip_<track_id>` enums on `data_internal` (the
authoring state behind `audio_manifest`), and the emissive groups' keyable
`emissiveGroup_<name>` floats on `data_export` (FBX can only animate attrs of
an exported node). They are not records: `read` does not return them, while
`values` and `dump` do.

> See **[shot_export_unity.md](shot_export_unity.md)** for the full Shots → FBX →
> Unity contract (the clip-name join invariant, the C# reader, and side-by-side
> coexistence with Audio).

## Producing and publishing

**A producer returns, the snapshot writes.** Each DCC's `FbxUtils.PRODUCERS`
maps a record spec to a classmethod `export_record(ctx) -> ptk.Record | None`
that reads the scene and returns the record -- or `None` when there is nothing
to say, which CLEARS the stored record (deleting the last shot must not leave
the previous takes riding into the next export). `ptk.ExportSnapshot.assemble`
orders the producers by the records' declared dependencies (`after`), hands
each the `ptk.ExportContext`, and `commit` writes every record in one pass,
then stamps the `handoff` block from what the carrier now holds.

**Decisions are inputs, never patches.** `ExportContext` carries what the
exporter decided -- the Animation Clips mode, the clip span measured from the
keys the write will carry -- and the producers read it. A reader of another
record reads it through `ctx.record(spec, DataNodes)`: the value produced
earlier in the same assembly, else the stored one. Publishing twice with one
context produces the same records; the pipeline this replaced patched the clip
origin and mode on after the producers, and a second producer run overwrote
the patch (three exports shipped the wrong origin while logging the right one).

| Path | Call | Refreshes |
|---|---|---|
| Scene Exporter | the `export_data_node` task: `FbxUtils.publish(ctx)` once, with the run's clip mode and span (the write falls back to one publish when the task is off) | every record |
| Any FBX export (File ▸ Export, Game Exporter) | the session hook: `FbxUtils.enable_export_producer(spec)` opts a record in; authoring a shot or an audio track does it | the opted-in records |
| Hand-off bridge | `FbxUtils.export_prepared(export_context(mode=HANDOFF), stagers=...)` | only `derived` records -- a bridge is not the authority on an authored record (a full refresh once wiped a lightmap manifest the scene's markers no longer described) |
| Authoring time | `FbxUtils.publish_authored({spec: payload})` from the tool (`ptk.ExportSnapshot.publish` with this scene's provenance) | the tool's own records |

**Stagers** mutate the scene for a write and undo it after, and produce no
record: `FbxUtils.STAGERS` (the render-effects curve-proxy transport) and the
session stagers `register_export_stager(name, prepare, finish)` adds (the
shadow preview stands down for the write and is re-attached after it). Every
phase runs with the selection preserved, since the selection IS the export set
of a selected-only write, and a bracket that fails to open finishes what it
staged. `FbxUtils.stage()` runs every
`prepare` without opening a bracket -- the Scene Exporter stages from its
publishing task, so the checks after it and the hierarchy baseline the write
records see the same nodes -- and the bracket stages again and finishes after
the write; the task also stages that finish as a deferred restore, so a run that
stops before the bracket (a declined check, a cancel) leaves nothing staged, and
every `finish` must be idempotent too. Producers always see the staged scene (outside a bracket, `publish`
runs the session stagers first), so every `prepare` must be idempotent.

**A legacy record follows its successor.** `fbx_takes` is the take list a scene
published before each `shot_metadata` clip carried its own range; it is no
longer written, and any commit in which the shots producer ran clears it, so an
older scene loses it at its next shots publish. Until then it still reads:
`ptk.SceneRecords.declared_takes` falls back to it when no clip carries a range.

**A Save As copy carries every record verbatim.** Nothing in the scene data
tells the copy from its source, so a record that must -- which scene file wrote
the lightmaps a re-bake may delete (`lightmap_writers`), which scene recorded
the hierarchy baseline -- stamps its writer (`DataNodes.writer_stamp`: the scene
file spelled from its own project, `""` while unsaved) and asks
`DataNodes.written_here(stamp)`: this file, or one that is gone (the scene was
renamed or moved), is this scene's; another file still on disk is a Save As
source's. The stamp is a path, declared as one (`RecordSpec.paths`; the
baseline's `paths=("scene",)` names its one path beside a path set and a hash),
so a save into another project re-spells it and the copy still names its
source. The Scene Exporter's hierarchy check sets a source's baseline aside,
and the copy's first export records its own.

## Crossing into another scene

A record also says what it becomes when **another scene's copy arrives beside
this scene's** -- the *Crosses* column above, declared once on the record
(`RecordSpec.merge`, `portable`) and applied by one engine,
`ptk.RecordTransfer`, whatever the route:

| Route | Entry point | What happens |
|---|---|---|
| A referenced module is imported | Reference Manager *Unlink and Import* (`ReferenceManager.import_references(scene_data=...)`) | The module's carriers -- read by nothing once local -- merge into this scene's (`DataNodes.merge_carriers`) or go (`discard_carriers`). The panel asks only when a merge would keep something (`DataNodes.merge_plan(...).is_empty`): **Yes** merges, **No** drops, **Cancel** leaves the reference linked. Names are respelled to where the import put each node (a clash digit, a kept namespace). |
| A DCC hand-off | the Blender bridge's *Include Scene Data* (`INCLUDE_SCENE_DATA`; was `INCLUDE_SHOTS`) | Every `portable` record rides the sidecar -- the shot store under `shots`, the rest keyed under `records` (`DataNodes.transfer_sections`) -- and lands on the far side through the same rules (`receive_sections`). |
| A pre-group Blender file | blendertk's fold | See [blendertk's data_nodes.md](https://github.com/m3trik/blendertk/blob/main/docs/data_nodes.md). |

The rules, by `Merge`:

- **re-derived** (every deliverable): never combined as data -- the other copy
  spells names as its own scene did -- so the merged scene publishes it afresh
  (`FbxUtils.publish(only=...)`).
- **keeps its own** (the hierarchy baseline): it describes its own scene.
- **unites**: entries combine by identity, this scene's winning a collision;
  a LIFO stack keeps this scene's newest on top.
- **domain merge**: a codec in `ptk.SceneRecords.CODECS` -- the shot store
  renumbers incoming shots (a clashing name arrives numbered, and says so), the
  key stash gives parked clips fresh ids and follows their shots, the emissive
  registry re-slots a group whose slot is taken (an engine binding to the old
  slot needs re-wiring, and the log says which).

Nothing is renamed or dropped silently: each crossing returns a
`ptk.TransferContext` whose `notes` are logged. What a record keeps **beside**
itself -- a membership set, a parked curve, a keyed attribute, an in-memory
store -- belongs to its DCC owner, one row in `DataNodes.OWNERS` whose optional
hooks the engine calls: `transfer_out` / `transfer_in` (the hand-off payload:
the emissive groups send their face membership with each member's face count),
`merge_carrier` / `discard_carrier` (the parked curves a stash registered on an
older carrier re-register here; a discarded stash's curves are deleted). The
carrier's own non-record attributes -- an audio track's keyed enum, a group's
keyed weight -- move to this scene's carrier with their curves.

## Getting it into the FBX

Only **export-all** picks the carrier up automatically (`Visible` collects
geometry only; `Selected` ships the user's picks). Four ways to make sure it
ships:

1. **Scene Exporter** (recommended) -- the default-on **"Export Scene Data
   Node"** task publishes the records and adds every carrier to the export set,
   in every export mode.
2. **Any-export hook** -- a subsystem that opted in (authoring a shot or an
   audio track does it; `ShotStore.enable_auto_export()` /
   `AudioClips.enable_auto_export()` explicitly, `disable_auto_export()` opts
   out) has its record republished by the shared, reference-counted
   `kBeforeExport` hook, so it rides into **any** FBX export with no staleness
   window.
3. **A hand-off bridge that reads the metadata** -- `MayaExportMixin`
   exposes `include_data_export`, and a bridge whose *consumer* parses the
   records turns it on: `WebXrPreview` (its GLB conversion binds
   `lightmap_metadata` via `ptk.MeshConvert.apply_glb_lightmaps`) and
   `UnityBridge` (its FBX lands in `Assets/`). The carrier joins the export set
   but never the strip-materials duplication, and is never *created* just to
   ship. Off by default: to a bridge that only wants geometry it is a stray
   empty in the target's outliner. Mirrored in blendertk, where the flag
   additionally forces `use_custom_props` and `EMPTY` in `object_types`.
4. **Native File ▸ Export Selection** -- include `data_export` in your
   selection yourself.

## API quick reference

| Member | Purpose |
|---|---|
| `ptk.SceneRecords.<RECORD>` | the declaration (`.key`, `.scope`, `.version`, `.kind`); `.load(DataNodes, default)`, `.save(DataNodes, payload)`, `.make(payload)`, `.is_present(DataNodes)`, `.clear(DataNodes)` |
| `FbxUtils.publish_authored({spec: payload})` | commit records in hand (authoring time), handoff restamped with this scene's provenance; no stager runs |
| `FbxUtils.publish(ctx=None, only=None)` | assemble every producer's record and commit once; returns the `ptk.ExportSnapshot` |
| `FbxUtils.export_context(mode, clip_mode=, clip_span=)` | a context with this scene's provenance |
| `FbxUtils.export_prepared(ctx=None, only=None, stagers=None)` / `stage(names=None)` | the bracket: stage, publish (given a context or `only`), finish after the block; `stage` alone runs every `prepare` now. `only` takes record specs or keys; stager names go in `stagers` |
| `FbxUtils.enable_export_producer(spec)` / `register_export_stager(name, prepare, finish)` | the session opt-ins |
| `DataNodes.read(scope, key)` / `write(scope, key, text)` / `values(scope)` | the store contract (`ptk.SceneStoreBase`); a falsy `text` clears and never creates |
| `DataNodes.dump(decode=True)` / `format_dump()` | every value the carriers hold, grouped by node -- the sidecar snapshot and tentacle's *Scene Metadata* viewer |
| `DataNodes.ensure_internal()` / `ensure_export()` | get-or-create each node (idempotent, healing) |
| `DataNodes.get_internal_node(create=True)` / `get_export_node(create=True)` / `get_export_nodes()` | resolve a carrier without creating it; the plural is for shipping |
| `DataNodes.transfer_sections(spell, objects)` / `receive_sections(manifest, resolve, **adapters)` | the portable records as a hand-off sidecar's sections, and landing them (inherited from `ptk.SceneStoreBase`) |
| `DataNodes.carriers_in(namespace)` / `merge_plan(carriers)` / `merge_carriers(carriers, rename, source_path_base=)` / `discard_carriers(carriers)` | another scene's carriers: find them, ask what a merge would keep, merge or drop them |
| `DataNodes.project_root()` / `project_root_of(path)` / `rebase_paths(old, new)` / `install_path_rebase()` | the project the `paths=True` records are spelled from (the scene file's, `None` while unsaved), and re-spelling them when a save moves the scene |
| `DataNodes.OWNERS` | record key -> the DCC class keeping state beside it (its crossing hooks) |

Legacy audio migration (pre-`DataNodes` `audio_events*` carriers and the old
single-enum `audio_trigger` schema) lives in `mayatk.audio_utils.migrate`
(`migrate_legacy_triggers`); old scenes carrying the retired `mirror_attr`
proxy pair are healed by the store itself: a `DataNodes.write` (or clear) that
finds a proxied channel replaces it with a plain one and drops its private
source, on every record path.

## Boundary: the scene sidecar (sections vs records)

Scene data that travels with a deliverable is either a **record** (this doc)
or a **sidecar section**, split by what the data *is*:

| | Scene record (this doc) | Scene-sidecar section |
|---|---|---|
| Carries | **tool-authored semantics** layered on the scene (shots, audio events, lightmap/shadow/emissive-group manifests) | **repairs for FBX translation loss** -- what the exporter mistranslates about the scene's literal content (modern-shader base colour / emissive / metallic-roughness today) |
| Written | by producers, at authoring/export time | derived read-only from the live scene at push/export time |
| Read by | engine-side scripts (Unity controllers), GLB appliers | `pythontk.MeshConvert` GLB appliers; downstream tools |
| Scope | scene-wide | the exported subset of one push/export |

The sidecar's homes: readers are `mtk.SceneState` / `btk.SceneState`
(`env_utils/scene_state.py`); the applier registry, envelope schema
(`build_scene_sidecar`) and embed/read ops live on `pythontk.MeshConvert`.
Carriers are dumb and interchangeable: records ride the FBX as user properties
(publishing one is a **scene edit**, so a preview push that must leave the
scene untouched carries sidecar sections instead); a GLB conversion applies the
sections, embeds the envelope in the glTF root `extras`, and keeps the records
as node extras (`fbx_to_glb` passes `--user-properties`). One rule keeps the
carriers honest: a datum has **one home per deliverable** -- a record must
never be duplicated into the sidecar.

## Adding a record

> First check the boundary above: data that *repairs FBX translation loss* is a
> sidecar **section** -- one reader on each DCC's `SceneState` plus one applier
> row on `pythontk.MeshConvert.SIDECAR_APPLIERS` -- not a record.

1. **Declare it** in `ptk.SceneRecords`: key, scope, version, owner, one-line
   description, `kind` (`derived` if it is computed from curves an artist edits
   between pushes), `after` (the records its producer reads) and `consumers`.
2. **Deliverable?** Write `export_record(cls, ctx)` on your tool -- pure: read
   the scene, return `spec.make(payload)` or `None` -- and add one row to
   `FbxUtils.PRODUCERS` in each DCC that produces it. Publish at authoring time
   with `FbxUtils.publish_authored({spec: record})`.
3. **Private?** `spec.load(DataNodes)` / `spec.save(DataNodes, payload)` is the
   whole API -- and declare how another scene's copy combines with this one's
   (`merge=Merge.UNION` / `CODEC` / `OWN`; `merge_key` for a list, `respell=False`
   for a payload that names no scene node). Crosses a hand-off too?
   `portable=True`. Its values are file or folder paths? `paths=True`: each is
   stored relative to the project the scene FILE lives in
   (`ptk.FileUtils.portable_path` from `DataNodes.project_root()`; `../`
   chains included, absolute only on another drive or while unsaved), and
   every route that moves the record re-spells it -- a save into another
   project (the save hook `DataNodes.install_path_rebase`, installed by
   `MayaUiHandler`), a hand-off (sent absolute, landed from the receiving
   scene's project) and a module merge (from the module's project).
   Keeps state beside the record? One row in each DCC's
   `DataNodes.OWNERS` with the hooks it needs ([Crossing into another
   scene](#crossing-into-another-scene)).
4. **Read by Unity?** Add the channel to `UnitytkSettings.cs` and `"unity"` to
   the record's `consumers`, then read it as an FBX user property on the
   `data_export` GameObject -- see
   [unitytk's templates README](https://github.com/m3trik/unitytk/blob/main/unitytk/templates/README.md).
5. Run `python m3trik/scripts/sync_scene_records.py` (the table above and the
   Unity gate).
