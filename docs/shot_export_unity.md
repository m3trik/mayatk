# Shot data in the FBX → Unity

> Built on the shared scene-data-node system — see **[data_nodes.md](data_nodes.md)**
> for the `data_internal` / `data_export` two-node model this page assumes.

mayatk's Shots system can publish a self-describing **export view** onto the
shared `data_export` node, so it rides into **any** FBX export (the Scene
Exporter, File ▸ Export, Game Exporter, scripts). One record, a plain string
(JSON) attr on the `data_export` transform:

| Channel (attr) | Shape | Consumed by |
|---|---|---|
| `animation` | `{ "version": 1, "fps", "clip_mode", "shots": [{ "clip", "start", "end", "description" }, …] }` | engine-side scripts (this doc); each clip's `start`/`end` is the take the FBX exporter realizes (`FbxUtils.apply_takes_from_node`) — one **AnimStack (Unity AnimationClip)** per shot |

**Invariant:** each clip's name and range come from a single resolution pass,
so the take and its metadata cannot disagree — `clip` is the take name and the
join key from a metadata record back to its imported clip. `description` is
left out when empty; a shot's members stay in the scene's own store. Every
reader takes the take list through
`ptk.SceneRecords.declared_takes`: the clips' own ranges. The record adds
`fps` — the rate every frame number in it is counted in, which a consumer that
did not author the scene cannot otherwise know — and `clip_mode`, the Animation
Clips mode the export declared (`full` realizes no take: the FBX ships one
whole-timeline clip, and the clip ranges describe windows inside it).

**Every take is an exact slice of the animation.** Maya's take split restricts
each curve to a take's window before writing it, so a plainly keyed curve with
no key inside a shot used to give that shot no channel (the node played its
rest pose for the whole shot in Unity) and a take's keys stopped at its last
in-window key. `FbxUtils.apply_takes` therefore arms Resample All
(`FbxUtils.RESAMPLE_ALL`) for the write. Every animated curve is keyed on every
frame of the bake range first, and each take then carries every channel across
its whole window (measured in Unity 6000.3, before and after:
`unitytk/test/test_shot_metadata_integration.py`). A GLB-only export does not
resample: its intermediate FBX's takes are discarded.
`ptk.ExportVerifier.check_fbx_take_channels` (the Scene Exporter's *Verify The
Written File*) fails a file whose takes are not exact slices.

**The Animation Clips mode reaches the FBX.** *Shots + Full Sequence* ships the
shot takes and Maya's whole-timeline `Take 001`. *Shots Only* drops `Take 001`
from the written FBX (after any GLB conversion, which cuts its clips from it).
*Full Sequence Only* splits nothing and ships `Take 001` alone, with the shots
as windows inside it.

**The shot name IS the clip name, verbatim.** `ShotStore.NAME_PATTERN`
(`[A-Za-z0-9_]+`, unique among the scene's shots ignoring case — Unity joins
clips case-insensitively) is the set every carrier keeps unchanged, so the
store refuses anything else (`ShotStore.name_error` gives the reason; every
name field in the Shots panel, the Sequencer and its dialogs shows it) rather
than letting the export respell it. Only a name held from before that rule is
still respelled on export, and the export logs each `old -> new` when it does.
Descriptions ride as JSON strings and are never refused or rewritten.

**Stale shots ship unless you leave them out.** A shot is *stale* when every
object it names is gone from the scene and nothing is keyed inside its frames
-- what a scene saved from another keeps of shots whose animation it deleted.
An export declares it like any other shot (its take holds still) and names it
in the export log, with an **Open Shots** link. **Leave Stale Shots Out of
Exports** (the option box of *Delete Shots*; `ShotStore.drop_stale_shots`, saved
with the scene) leaves them out of the record, the takes and the bake range;
clip names are resolved over every shot first, so the ones that ship keep
theirs. **Delete Shots** with its *Stale* scope (the Shots window's *All Shots*
group, or the Sequencer's shot-list menu; `ShotStore.remove_stale_shots()`)
drops their records without touching a key or moving a shot, where *Delete
Shot* cuts a shot's keys and closes the gap behind it. A shot that names no
members is never stale -- nothing tells it from a hold.

## The GLB deliverable

A GLB is built from the FBX (`ptk.GlbPipeline`), which carries the record as
a node extra on `data_export` (JSON inside JSON, under
`extras.fromFBX.userProperties`). The build cuts each shot's clip from the
whole-timeline animation (`MeshConvert.apply_glb_clips`), as the Animation
Clips mode says: *Shots + Full Sequence* keeps the whole timeline beside the
shots as `FULL_SEQUENCE`, *Shots Only* drops it, *Full Sequence Only* cuts no
shot. It then states the shots in `extras.animation`
(`MeshConvert.apply_glb_animations`, automatic on every conversion) and removes
the FBX's copy:

```json
{ "version": 2, "fps": 30.0, "clip_mode": "both", "default_clip": "SHOT_A",
  "shots": [{ "clip": "SHOT_A", "start": 1, "end": 10, "description": "…" },
            { "clip": "SHOT_B", "start": 20, "end": 30 }],
  "clips": [{ "name": "SHOT_A", "animation": 0, "zero_frame": 1.0 },
            { "name": "SHOT_B", "animation": 1, "zero_frame": 20.0 },
            { "name": "FULL_SEQUENCE", "animation": 2, "zero_frame": 1.0 }] }
```

`shots` is the record's list as it stands, whatever clips the file holds. A
shot plays the clip its `clip` names; one without a clip of its own (a *Full
Sequence Only* file, or a take the build could not cut) plays its window
`start`..`end` of the whole-timeline clip -- the one no shot names, as Unity's
`TryGetSequenceWindow` does. Every clip is rebased to t=0, and its `zero_frame`
is the authoring frame t=0 sits on: frame f is `(f - zero_frame) / fps`
seconds into it. `default_clip` is the one to open on; the glTF's own
`animations` order is left as written. The bundled WebXR preview reads the
block for its clip picker and names the shot under the playhead on the
whole-timeline clip; `MeshConvert.verify_glb` reports the counts to a
recipient.

## Maya side

```python
from mayatk.anim_utils.shots._shots import ShotStore
ShotStore.active().publish_export_view()      # publish the shot record now
# …or set ShotStore.active().auto_publish_export = True to republish on every save
# …or, for true "any export carries shots" with no staleness:
ShotStore.enable_auto_export()                # before-export hook: republish fresh + apply takes
```

A hand-off -- the Unity bridge, the WebXR preview -- needs none of these: the
shot record is a *derived* record, so every send republishes it from the store
before its write.

### Coexistence with Audio (shared before-export hook)

`enable_auto_export` opts the shot record into a shared `FbxUtils` before-export
hook (`FbxUtils.enable_export_producer`); the Audio system opts its own in via
`AudioClips.enable_auto_export()`. Both ride out on the **same** `data_export`
GameObject with distinct attrs — Shots' plain `animation`, Audio's
JSON `audio` (baked from the keyed `audio_clip_*` authoring state on
`data_internal`) — so a scene with both
exports one FBX that Unity imports into both a `ShotMetadataController` and an
`AudioEventController` on the prefab root. The hook is reference-counted: enable
either or both; each runs once per export, fault-isolated, **in the records'
declared dependency order (`audio` reads `animation`, so shots
first)** — the audio producer reads the takes the shots producer just built and
scopes each event to its take (`clip` + take-relative `frame`), so every audio
event fires in its own AnimationClip -- and, in a Shots + Full Sequence file, at
its shot's window in the whole-timeline clip, which unitytk places from the shot
record (a `full` export realizes no take, so
its events ship unscoped against the one whole-timeline clip). Verified end-to-end by
`unitytk/test/test_shots_audio_sidebyside_integration.py`.

The Scene Exporter's **Animation Clips** row (either shot-bearing mode) does this
for you and includes `data_export` in the export set. Clips appear automatically in
Unity (named per take) with **no engine code**. The metadata only needs a small
reader:

## Unity side

A ready-to-use **`ShotMetadataController.cs`** ships in `unitytk/templates/ShotMetadata/`
(runtime component + an `AssetPostprocessor` that parses `animation`, attaches
the controller to the prefab root, and joins records to clips by name). Deploy it
as part of the full compile-coupled set — `UnitytkSettings.cs` provides the shared
`ImportGate`/`CarrierImport` (`unitytk.TemplateDeployer.deploy_package(project_root)`
writes the whole set as the embedded `com.m3trik.unitytk` UPM package).
Verified end-to-end by `unitytk/test/test_shot_metadata_integration.py`:
every frame of every shot clip against Maya, in each Animation Clips mode.

Each `ShotRecord` carries its `start`/`end` frames, and the controller carries
the record's `fps` and `clipMode`, plus `sequenceClip` / `sequenceFirstFrame`:
the clip cut over the whole-timeline take (the one take no shot names), when
the file has one. `TryGetSequenceWindow(clip, out start, out end)` gives a
shot's window in seconds inside that clip, which is how a Full Sequence file is
played shot by shot. The importer warns when a record names a clip the file
does not carry, as when an FBX is written without its shot takes.

The minimal read, for reference — clips import natively; Maya exports the
`animation` attr as an FBX *user property* on the `data_export` GameObject:

```csharp
using UnityEditor;
using UnityEngine;

class ShotMetadataPostprocessor : AssetPostprocessor
{
    [System.Serializable] class ShotRec {
        public string clip, description;
        public float start, end;
    }
    [System.Serializable] class ShotMeta { public int version; public ShotRec[] shots; }

    // Fired per GameObject that carries FBX user properties.
    void OnPostprocessGameObjectWithUserProperties(
        GameObject go, string[] names, object[] values)
    {
        for (int i = 0; i < names.Length; i++)
        {
            if (names[i] != "animation") continue;
            var meta = JsonUtility.FromJson<ShotMeta>((string)values[i]);
            foreach (var s in meta.shots)
                Debug.Log($"[shot] clip={s.clip}  desc={s.description}");
            // Join to clips by name: AnimationClip whose name == s.clip.
        }
    }
}
```

> The `data_export` GameObject is a hidden, zero-scale locator carrier; strip it
> after reading if you don't want it in the scene.

## Limitations

- **Write binary FBX.** Unity's ASCII FBX reader drops the whole scene, or
  truncates the string, once a `data_export` channel passes about 6 KB
  (measured in batch Unity 6000.3; binary carried 300 KB intact), and the
  handoff record alone is past that. The Scene Exporter writes binary whenever
  the carrier ships, even over an ASCII preset.

- **Metadata is selection-dependent.** The carrier is a hidden node, so it
  exports automatically only with *export-all*. The Scene Exporter handles every
  mode for you via its default-on **"Export Scene Data Node"** option (refreshes
  the carrier from shots *and* audio, then adds it to the export set). A native
  File ▸ Export Selection still requires you to include `data_export` yourself.
- **Naming strategy is scene-global** (`ShotStore.clip_name_strategy`,
  `"name"` default or `"sequence"`), resolved when the view is published.
