# Shot data in the FBX → Unity

> Built on the shared scene-data-node system — see **[data_nodes.md](data_nodes.md)**
> for the `data_internal` / `data_export` two-node model this page assumes.

mayatk's Shots system can publish a self-describing **export view** onto the
shared `data_export` node, so it rides into **any** FBX export (the Scene
Exporter, File ▸ Export, Game Exporter, scripts). One record, a plain string
(JSON) attr on the `data_export` transform:

| Channel (attr) | Shape | Consumed by |
|---|---|---|
| `shot_metadata` | `{ "version": 1, "fps", "clip_mode", "shots": [{ "clip", "start", "end", "description", "objects", "section" }, …] }` | engine-side scripts (this doc); each clip's `start`/`end` is the take the FBX exporter realizes (`FbxUtils.apply_takes_from_node`) — one **AnimStack (Unity AnimationClip)** per shot |

**Invariant:** each clip's name and range come from a single resolution pass,
so the take and its metadata cannot disagree — `clip` is the take name and the
join key from a metadata record back to its imported clip. `description` and
`section` are left out when empty; `objects` is always present (Unity's
`ShotRecord` reads it as an array). Every reader takes the take list through
`ptk.SceneRecords.declared_takes`: the clips' own ranges, else the `fbx_takes`
list a file written before the ranges moved onto the clips carries instead (no
longer written; the next shots publish clears it from a scene). The record adds
`fps` — the rate every frame number in it is counted in, which a consumer that
did not author the scene cannot otherwise know — and `clip_mode`, the Animation
Clips mode the export declared (`full` realizes no take: the FBX ships one
whole-timeline clip, and the clip ranges describe windows inside it).

**The shot name IS the clip name, verbatim.** `ShotStore.NAME_PATTERN`
(`[A-Za-z0-9_]+`, unique among the scene's shots ignoring case — Unity joins
clips case-insensitively) is the set every carrier keeps unchanged, so the
store refuses anything else (`ShotStore.name_error` gives the reason; every
name field in the Shots panel, the Sequencer and its dialogs shows it) rather
than letting the export respell it. Only a name held from before that rule is
still respelled on export, and the export logs each `old -> new` when it does.
Descriptions, sections and object lists ride as JSON strings and are never
refused or rewritten.

**The record declares the scene as it is now.** A shot is *stale* when every
object it names is gone from the scene and nothing is keyed inside its frames
-- what a scene saved from another keeps of shots whose animation it deleted.
The record leaves stale shots out (a take of one would carry nothing, and a GLB
cannot carry an empty clip at all), drops each member the scene no longer
holds, and the bake range spans only what it declares; clip names are resolved
over every shot first, so the ones that ship keep theirs. The export log names
what it left out, with an **Open Shots** link. The store keeps them: **Delete
Stale Shots** (the Shots window's *All Shots* group, or the Sequencer's
shot-list menu; `ShotStore.remove_stale_shots()`) drops their records without
touching a key or moving a shot, where *Delete Shot* cuts a shot's keys and
closes the gap behind it. A shot that names no members is never stale --
nothing tells it from a hold.

## The GLB deliverable

The same record survives the FBX → glTF conversion (`MeshConvert.fbx_to_glb`
passes `--user-properties`, so it arrives as a node extra on `data_export`), and
each declared take becomes a **glTF animation** named by clip. Three things about
that are worth knowing, all probe-measured on Maya 2025 + FBX2glTF 0.13.1:

- Maya's exporter keeps its whole-timeline `Take 001` **alongside** the split
  takes, and it converts first — so `animations[0]` is the entire timeline, not
  shot 1. (The split is therefore additive: turning it on never costs you the
  continuous clip.)
- Every clip's own keyframe times are **rebased to zero**, so a shot authored at
  frames 20–30 and one at 1–10 both start at t=0.
- The channels ride as JSON *strings* nested under
  `extras.fromFBX.userProperties`, i.e. JSON inside JSON.

`MeshConvert.apply_glb_animations` (automatic on every conversion) resolves all
three into `extras.animation_web`, decoded and joined to the clips:

```json
{ "version": 1, "fps": 30.0, "default_clip": "SHOT_A",
  "clips": [{ "name": "Take 001", "animation": 0, "duration": 0.966667, "declared": false },
            { "name": "SHOT_A", "animation": 1, "duration": 0.3, "declared": true,
              "start_frame": 1, "end_frame": 10, "offset": 0.033333,
              "description": "…", "objects": ["…"] }] }
```

`declared` marks the clips a shot asked for, `offset` places a clip on the
authoring timeline in seconds, and `default_clip` is the one a player should open
on. The glTF's own `animations` order is left exactly as written. The bundled
WebXR preview (`pythontk`'s `net_utils/preview/viewer.html`) reads this block for its clip
picker; `MeshConvert.verify_glb` reports the counts to a recipient.

## Maya side

```python
from mayatk.anim_utils.shots._shots import ShotStore
ShotStore.active().publish_export_view()      # publish the shot record now
# …or set ShotStore.active().auto_publish_export = True to republish on every save
# …or, for true "any export carries shots" with no staleness:
ShotStore.enable_auto_export()                # before-export hook: republish fresh + apply takes
```

### Coexistence with Audio (shared before-export hook)

`enable_auto_export` opts the shot record into a shared `FbxUtils` before-export
hook (`FbxUtils.enable_export_producer`); the Audio system opts its own in via
`AudioClips.enable_auto_export()`. Both ride out on the **same** `data_export`
GameObject with distinct attrs — Shots' plain `shot_metadata`, Audio's
JSON `audio_manifest` (baked from the keyed `audio_clip_*` authoring state on
`data_internal`) — so a scene with both
exports one FBX that Unity imports into both a `ShotMetadataController` and an
`AudioEventController` on the prefab root. The hook is reference-counted: enable
either or both; each runs once per export, fault-isolated, **in the records'
declared dependency order (`audio_manifest` reads `shot_metadata`, so shots
first)** — the audio producer reads the takes the shots producer just built and
scopes each event to its take (`clip` + take-relative `frame`), so every audio
event fires only in its own AnimationClip (a `full` export realizes no take, so
its events ship unscoped against the one whole-timeline clip). Verified end-to-end by
`unitytk/test/test_shots_audio_sidebyside_integration.py`.

The Scene Exporter's **Animation Clips** row (either shot-bearing mode) does this
for you and includes `data_export` in the export set. Clips appear automatically in
Unity (named per take) with **no engine code**. The metadata only needs a small
reader:

## Unity side

A ready-to-use **`ShotMetadataController.cs`** ships in `unitytk/templates/`
(runtime component + an `AssetPostprocessor` that parses `shot_metadata`, attaches
the controller to the prefab root, and joins records to clips by name). Deploy it
as part of the full compile-coupled set — `UnitytkSettings.cs` provides the shared
`ImportGate`/`CarrierImport` (`unitytk.TemplateDeployer.deploy_package(project_root)`
writes the whole set as the embedded `com.m3trik.unitytk` UPM package).
Verified end-to-end by `unitytk/test/test_shot_metadata_integration.py`.

The minimal read, for reference — clips import natively; Maya exports the
`shot_metadata` attr as an FBX *user property* on the `data_export` GameObject:

```csharp
using UnityEditor;
using UnityEngine;

class ShotMetadataPostprocessor : AssetPostprocessor
{
    [System.Serializable] class ShotRec {
        public string clip, description, section;
        public float start, end;
        public string[] objects;
    }
    [System.Serializable] class ShotMeta { public int version; public ShotRec[] shots; }

    // Fired per GameObject that carries FBX user properties.
    void OnPostprocessGameObjectWithUserProperties(
        GameObject go, string[] names, object[] values)
    {
        for (int i = 0; i < names.Length; i++)
        {
            if (names[i] != "shot_metadata") continue;
            var meta = JsonUtility.FromJson<ShotMeta>((string)values[i]);
            foreach (var s in meta.shots)
                Debug.Log($"[shot] clip={s.clip}  desc={s.description}  section={s.section}");
            // Join to clips by name: AnimationClip whose name == s.clip.
        }
    }
}
```

> `objects` are leaf names (per-shot membership the whole-scene takes can't
> express). The `data_export` GameObject is a hidden, zero-scale locator carrier;
> strip it after reading if you don't want it in the scene.

## Limitations

- **Metadata is selection-dependent.** The carrier is a hidden node, so it
  exports automatically only with *export-all*. The Scene Exporter handles every
  mode for you via its default-on **"Export Scene Data Node"** option (refreshes
  the carrier from shots *and* audio, then adds it to the export set). A native
  File ▸ Export Selection still requires you to include `data_export` yourself.
- **Naming strategy is scene-global** (`ShotStore.clip_name_strategy`,
  `"name"` default or `"sequence"`), resolved when the view is published.
