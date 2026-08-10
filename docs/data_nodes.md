# Scene data nodes (`DataNodes`)

`mayatk.node_utils.data_nodes.DataNodes` is the **single, shared place** every
tool stashes scene-wide metadata that has to survive a save *and* (optionally)
ride out inside an FBX. Shots, audio events, and anything you add all write to
the **same** two nodes instead of each littering the scene with its own carrier
transform.

## Why two nodes

| Node | Type | Role |
|---|---|---|
| `data_internal` | `network` | **Single source of truth.** Every tool authors its attributes here. A `network` node never serialises into an FBX, so authoring state stays in the `.ma`/`.mb` and out of game-engine exports. |
| `data_export` | locked, viewport-invisible, Outliner-hidden `transform` (zero-scale `locator` shape) | **The FBX export surface.** This is the only node that travels into an FBX; downstream importers (Unity, etc.) read its user properties. |

The split keeps *authored state* (internal) cleanly separated from the
*export projection* (export): tools author on `data_internal`, and only the
regenerated artifacts they publish for the engine land on `data_export`.

Implementation details that matter:

- **`data_internal`** has its **name locked** (no accidental rename) but stays
  otherwise unlocked so tools can freely add/write attributes.
- **`data_export`** is a transform whose nine transform channels are all
  locked + hidden. It carries a **zero-scale locator shape** — it draws nothing
  in the viewport, and *Optimize Scene Size* won't delete it as an "empty"
  transform.
- **`data_export` never appears in the Outliner.** Both the transform and its
  locator shape are flagged `hiddenInOutliner`
  (`DisplayUtils.set_hidden_in_outliner`) — it's pipeline plumbing, not user
  content. The flag is display-only: `ls`, `select`, export sets and the FBX
  write still see the node, so nothing downstream changes. `ensure_export()`
  re-applies it on every call, so scenes authored before the flag existed heal
  the first time any producer writes a channel (already-hidden = no write, no
  panel redraw).
- The Scene Exporter's `Visible` mode collects **geometry only** and `Selected`
  ships just the user's picks, so neither includes the carrier by default — see
  [Getting it into the FBX](#getting-it-into-the-fbx).

Both nodes are created on demand and idempotently:

```python
from mayatk.node_utils.data_nodes import DataNodes

DataNodes.ensure_internal()   # -> "data_internal"   (create if missing)
DataNodes.ensure_export()     # -> "data_export"     (create if missing)
```

## Putting data on the export surface

### `set_export_string` / `get_export_string` — plain export channels

For values that are **regenerated from live state at export time** (JSON blobs,
baked wire strings) rather than authored and edited. These are written as a
plain `string` attr **directly on `data_export`** — they don't belong on the
`data_internal` SSoT because they're derived artifacts, and a plain attr
sidesteps any proxy-string-export ambiguity. The value rides out as an FBX user
property.

```python
DataNodes.set_export_string(DataNodes.FBX_TAKES, json.dumps(takes))
raw = DataNodes.get_export_string(DataNodes.FBX_TAKES)   # None if absent/empty
```

An **empty value clears** the channel: the attr is set to `""` when it already
exists, and nothing is created otherwise — a producer can always call
`set_export_string(attr, "")` without leaving an empty carrier behind
(matching the blendertk mirror's semantics).

## Scene-persistent state that never exports

The second mechanism, for tool state that must survive a save but must **never**
ride into an FBX: `set_internal_string` / `get_internal_string` write plain
string attrs on `data_internal` (a `network` node — structurally incapable of
serialising into an FBX).

```python
DataNodes.set_internal_string("smart_bake_sessions", json.dumps(stack))
raw = DataNodes.get_internal_string("smart_bake_sessions")  # None if absent/empty
```

> **Retired: `mirror_attr` (proxied authored state).** A third mechanism —
> authoring an attr on `data_internal` with a Maya proxy aliasing it on
> `data_export` — was removed once its only producer (the audio manifest)
> migrated to a regenerated export channel. Old scenes still carrying the
> proxy pair are healed in place by `AudioClips.prepare_for_export` (see
> `_drop_legacy_manifest_proxy`). Removing it also closed an API-parity delta:
> `btk.DataNodes` never had a proxy concept (Blender has no attr-proxy DG
> feature).

## Export channels in use

Everything below lands on the **one** `data_export` GameObject in the imported
FBX. Attr names are distinct, so producers compose without collision.

| Channel (attr) | Mechanism | Producer | Consumer |
|---|---|---|---|
| `fbx_takes` | plain string (`set_export_string`) | Shots — `ShotStore.publish_export_view` | `FbxUtils.apply_takes_from_node` → one **AnimStack / Unity AnimationClip** per shot |
| `shot_metadata` | plain string (`set_export_string`) | Shots — `ShotStore.publish_export_view` | engine-side scripts (Unity `ShotMetadataController`) |
| `audio_manifest` | plain string (`set_export_string`) | Audio — `AudioClips.prepare_for_export` | engine-side scripts (Unity `AudioEventController`) |
| `lightmap_metadata` | plain string (`set_export_string`) | Lightmap Baker — `refresh_export_metadata` (also republished by `commit_lightmap` / `revert_lightmap`) | engine-side scripts (Unity `LightmapMetadataController`) |
| `shadow_metadata` | plain string (`set_export_string`) | Shadow Rig — `ShadowRig.refresh_export_metadata` | engine-side scripts (Unity `ShadowPlaneController`) |
| `emissive_groups` | plain string (`set_export_string`) + per-group keyable `emissiveGroup_<name>` floats (the one authored-state exception — FBX can only carry animation on attrs of the exported node itself) | Emissive Groups — `EmissiveGroups.refresh_export_metadata` | engine-side scripts (Unity `EmissiveGroupController`) |

`FBX_TAKES` (`"fbx_takes"`) and `SHOT_METADATA` (`"shot_metadata"`) are name
constants on `DataNodes`. Audio's authoring state — the keyed `audio_clip_<id>`
enums and the shared `audio_file_map` — lives on `data_internal` and is **not**
exported; only the baked `audio_manifest` projection is. (Pre-2026-07 scenes
carried `audio_manifest` as a proxied attr pair; `prepare_for_export` heals
them to the plain channel in place.)

`audio_manifest` is versioned JSON (v2):
`{"version": 2, "events": [{"clip", "frame", "name"}, …]}`. The bake reads the
published `fbx_takes` channel and assigns each event to every take whose range
contains it, with `frame` rebased to that take's start — so `clip` is the same
join key `shot_metadata` uses and each event fires only in its own Unity
AnimationClip (the export hook runs the shots preparer before audio's, so the
takes are always fresh). With no takes the events ship unscoped (`clip: ""`)
rebased to `playbackOptions min`. Events outside every take are dropped with a
warning at bake. Pre-v2 FBX carried a flat `"12:footstep 24:jump"` wire string;
Unity's importer keeps a legacy parser for those files.

> `fbx_takes` is consumed **inside Maya** (`apply_takes_from_node` realizes it
> into AnimStacks before the export), and then *also* rides out as a raw user
> property. That duplication is intentional: the JSON is the machine-readable
> record of which clips the FBX declares — trivially greppable by CI/pipeline
> validators without an FBX SDK parse — and costs a few hundred bytes. Unity
> itself never reads it; it joins `shot_metadata.clip` to the imported
> AnimationClip names.

## Internal channels in use

State on `data_internal` only — persists with the scene, never exports:

| Channel (attr) | Owner | Content |
|---|---|---|
| `audio_clip_<track_id>` | Audio — `AudioUtils.ensure_track_attr` | keyed `off:on` enum per track (the authoring state behind `audio_manifest`) |
| `audio_file_map` | Audio — `AudioUtils.set_path` | JSON `{track_id: wav_path}` |
| `smart_bake_sessions` | SmartBake — `BakeSessionStore` | JSON LIFO stack of restore manifests |
| `smart_bake_stash` | SmartBake — `stash_curve` | message-multi registry pinning stashed animCurve duplicates |
| `shot_store` | Shots — `MayaScenePersistence` | full `ShotStore.to_dict()` app state (scenes predating the consolidation used a dedicated `shotStore` node, folded in on first load) |

> See **[shot_export_unity.md](shot_export_unity.md)** for the full Shots → FBX →
> Unity contract (the clip-name join invariant, the C# reader, and side-by-side
> coexistence with Audio).

## Getting it into the FBX

Only **export-all** picks the carrier up automatically (`Visible` collects
geometry only; `Selected` ships the user's picks). Four ways to make sure it
ships:

1. **Scene Exporter** (recommended) — the default-on **"Export Scene Data Node"**
   option calls `FbxUtils.run_export_preparers()` (every registered session
   preparer plus every known producer) and adds the carrier to the export set,
   so the metadata ships in **every** export mode (`All`/`Visible`/`Selected`).
2. **Any-export hook** — producers register before-export *preparers* on a
   shared, reference-counted `FbxUtils` `kBeforeExport`/`kAfterExport` hook, so
   the data rides into **any** FBX export (File ▸ Export, Game Exporter, a raw
   `cmds.file`) — republished fresh with no staleness window. Registration is
   **automatic on authoring** (saving a store with shots / creating an audio
   track); `ShotStore.enable_auto_export()` / `AudioClips.enable_auto_export()`
   opt in explicitly and `disable_auto_export()` opts out for the session.
3. **A hand-off bridge that reads the metadata** — `MayaExportMixin` exposes
   `include_data_export`, and a bridge whose *consumer* parses these channels
   turns it on: `WebXrPreview` (its GLB conversion binds `lightmap_metadata` via
   `ptk.MeshConvert.apply_glb_lightmaps`) and `UnityBridge` (its FBX lands in
   `Assets/`, where unitytk's `LightmapMetadataController` reads it). The carrier
   joins the export set but never the strip-materials duplication, and is never
   *created* just to ship — an unbaked scene gains no stray node. Off by default:
   to a bridge that only wants geometry it is a stray empty in the target's
   outliner. Mirrored in blendertk, where the flag additionally forces
   `use_custom_props` and `EMPTY` in `object_types` (Blender's exporter drops
   both by default, which would ship the Empty holding nothing).
4. **Native File ▸ Export Selection** — include `data_export` in your selection
   yourself.

## API quick reference

| Member | Purpose |
|---|---|
| `DataNodes.INTERNAL` / `.EXPORT` | node names (`"data_internal"` / `"data_export"`) |
| `DataNodes.FBX_TAKES` / `.SHOT_METADATA` | export-channel name constants |
| `ensure_internal()` / `ensure_export()` | get-or-create each node (idempotent) |
| `set_export_string(attr, value)` | write a plain string channel on export (empty value clears, never creates) |
| `get_export_string(attr) -> str \| None` | read a string channel (None if absent/empty) |
| `set_internal_string(attr, value)` | write a scene-persistent, never-exported channel |
| `get_internal_string(attr) -> str \| None` | read an internal channel (None if absent/empty) |

Legacy audio migration (pre-`DataNodes` `audio_events*` carriers and the old
single-enum `audio_trigger` schema) lives in `mayatk.audio_utils.migrate`
(`migrate_legacy_triggers`), which converts straight to the current per-track
schema.

## Boundary: the scene sidecar (sections vs channels)

Scene data that travels with a deliverable is **named sections**, each with
one reader per DCC and one applier per target; `DataNodes` channels and
sidecar sections are the same concept on different carriers, split by what
the data *is*:

| | `DataNodes` channel (this doc) | Scene-sidecar section |
|---|---|---|
| Carries | **tool-authored semantics** layered on the scene (shots, audio events, lightmap/shadow/emissive-group manifests) | **repairs for FBX translation loss** — what the exporter mistranslates about the scene's literal content (modern-shader base colour / emissive today; lights, environment tomorrow) |
| Written | by tools, at authoring/export time | derived read-only from the live scene at push/export time |
| Read by | engine-side scripts (Unity controllers) | `pythontk.MeshConvert` GLB appliers; downstream tools |
| Scope | scene-wide | the exported subset of one push/export |

The grid's homes: readers are `mtk.SceneState` / `btk.SceneState`
(`env_utils/scene_state.py`, mirror pair); the applier registry, envelope
schema (`build_scene_sidecar`) and embed/read ops live on
`pythontk.MeshConvert`; every `fbx_to_glb` caller (WebXR preview, both Scene
Exporters' GLB paths, tentacle's quick-export) threads the envelope through
`sidecar=`. Adding a section = one reader per DCC + one applier row. Adding a
channel = the steps below.

Carriers are dumb and interchangeable:

- **FBX** — channels ride inside as user properties on `data_export` (this
  doc). Sections do not ride the FBX: publishing a channel is a **scene
  edit** (node creation, attr writes, an undo entry, a dirtied scene), and a
  preview push must leave the scene untouched.
- **GLB** — the conversion applies the sections *and* embeds the envelope +
  outcome summary in the glTF root `extras`
  (`MeshConvert.apply_scene_sidecar` / `read_scene_sidecar`), and
  `fbx_to_glb` passes `--user-properties` by default so the `data_export`
  channels survive into per-node glTF `extras` (measured, FBX2glTF v0.13.1).
  A GLB deliverable is therefore fully self-describing — no side files.
- **`.scene.json`** — the envelope written beside a preview payload
  (`PreviewBridge._attach_sidecar`); an inspection/debug artifact, no longer
  the primary handoff (the GLB embed is).

One rule keeps the carriers honest: a section/channel has **one home per
deliverable** — a `DataNodes` channel must never be duplicated into the
sidecar, or the two copies become a staleness bug waiting to disagree.

## Adding your own metadata

> First check the boundary above: data that *repairs FBX translation loss*
> (rather than layering tool semantics on the scene) is a sidecar **section**
> — one reader on each DCC's `SceneState` (`env_utils/scene_state.py`) plus
> one applier row on `pythontk.MeshConvert.SIDECAR_APPLIERS` — not a channel
> here.

1. **Derived, regenerated-at-export blob** → `set_export_string(attr, json)` from
   a no-arg "publish/prepare" method on your tool, then add the producer to
   `FbxUtils._KNOWN_PRODUCERS` (picked up by the Scene Exporter automatically)
   and/or register it via `FbxUtils.register_export_preparer("<name>", fn)` for
   the any-export session hook.
2. **Authored, edited-over-time state** → author it on `data_internal`
   (`set_internal_string`, or your own keyed attrs on the node — see Audio's
   `audio_clip_<id>` enums), and publish an export projection via step 1.
3. **Scene-persistent but never exported** → `set_internal_string(attr, json)`.
4. Pick an attr name that doesn't collide with the channels above.
5. On the engine side, read it as an FBX user property on the `data_export`
   GameObject — see the consumer catalog in
   [unitytk's templates README](../../unitytk/unitytk/templates/README.md).
6. Porting the producer to Blender? Mirror it through `btk.DataNodes` — see
   [blendertk's data_nodes.md](../../blendertk/docs/data_nodes.md) for the
   custom-property divergence and the export requirements.
