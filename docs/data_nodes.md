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
| `data_export` | locked, hidden `transform` (zero-scale `locator` shape) | **The FBX export surface.** This is the only node that travels into an FBX; downstream importers (Unity, etc.) read its user properties. |

The split keeps *authored state* (internal) cleanly separated from the
*export projection* (export): tools author on `data_internal`, and only the
regenerated artifacts they publish for the engine land on `data_export`.

Implementation details that matter:

- **`data_internal`** has its **name locked** (no accidental rename) but stays
  otherwise unlocked so tools can freely add/write attributes.
- **`data_export`** is a hidden transform whose nine transform channels are all
  locked + hidden. It carries a **zero-scale locator shape** purely so
  *Optimize Scene Size* won't delete it as an "empty" transform.
- Because `data_export` is **hidden**, the Scene Exporter's `Visible`/`Selected`
  modes omit it by default — see [Getting it into the FBX](#getting-it-into-the-fbx).

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
| `lightmap_metadata` | plain string (`set_export_string`) | Lightmap Baker — `commit_lightmap` / `revert_lightmap` | engine-side scripts (Unity `LightmapMetadataController`) |

`FBX_TAKES` (`"fbx_takes"`) and `SHOT_METADATA` (`"shot_metadata"`) are name
constants on `DataNodes`. Audio's authoring state — the keyed `audio_clip_<id>`
enums and the shared `audio_file_map` — lives on `data_internal` and is **not**
exported; only the baked `audio_manifest` projection is. (Pre-2026-07 scenes
carried `audio_manifest` as a proxied attr pair; `prepare_for_export` heals
them to the plain channel in place.)

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

`data_export` is hidden, so it only auto-exports with **export-all**. Three ways
to make sure it ships:

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
3. **Native File ▸ Export Selection** — include `data_export` in your selection
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

## Adding your own metadata

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
