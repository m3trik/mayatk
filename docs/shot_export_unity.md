# Shot data in the FBX → Unity

> Built on the shared scene-data-node system — see **[data_nodes.md](data_nodes.md)**
> for the `data_internal` / `data_export` two-node model this page assumes.

mayatk's Shots system can publish a self-describing **export view** onto the
shared `data_export` node, so it rides into **any** FBX export (the Scene
Exporter, File ▸ Export, Game Exporter, scripts). One record, a plain string
(JSON) attr on the `data_export` transform:

| Channel (attr) | Shape | Consumed by |
|---|---|---|
| `animation` | `{ "version": 3, "shots": [{ "clip", "description", "metadata" }, …], "fps" }` | engine-side scripts (this doc); each `clip` names the take the FBX exporter realizes for its shot (`FbxUtils.apply_takes_from_node`) — one **AnimStack (Unity AnimationClip)** per shot |

**One take per shot.** Every export splits the timeline into one take per
shot, named after it and spanning its range, and ships no other: Maya's
whole-timeline `Take 001` leaves a file with shots right after the write
(`FbxUtils.drop_undeclared_takes`), before anything reads it. A scene with no
shots ships Maya's one whole-timeline take, and its record states the rate
alone (`{ "version": 3, "shots": [], "fps" }`); an export whose write carries no
animation -- its Animation include group off, or a scene that animates
nothing -- ships no record. There is no mode to
choose: the Scene Exporter's former *Animation Clips* row is gone, and a preset
or panel state that still carries it loads with the key ignored (the log says
so once).

**The record holds what a clip cannot.** Each shot's name comes from a single
resolution pass, so the take and its record entry cannot disagree -- `clip` is
the take name and the join key from a record entry back to its imported clip.
`description` and `metadata` are left out when empty. The metadata is the Shot
Manifest's alone: the keys it writes from what its sheet says
(`ShotManifest.MANIFEST_METADATA` and the sheet's pass-through columns, handed
to the store by each Build as `ShotStore.shipped_metadata`; a scene saved before
a Build said, its manifest fields alone, `ShotStore.MANIFEST_FIELDS`), every value JSON
(a value JSON cannot hold ships as its string form, warned). The tools' own
bookkeeping on a shot (Assess's `object_status`, a split's `step_link`, the
build's records) and its members stay in the scene's own store. The record states no
frames: the takes are the timing. `fps` (required) is the scene's frame rate
(`currentTimeUnitToFPS`, the FBX's own time mode), for a reader of a format that
holds none -- a glTF, whose animations are in seconds; Unity reads the FBX's
time mode and ignores it, and `ptk.ExportVerifier.check_animation_rate` holds the
two equal. At export time the take list is
the shot store's (`ShotStore.declared_takes`, read through
`FbxUtils.declared_takes`); after the write, the file's own
(`ptk.FbxFile.take_windows`). A reader refuses an older record (version 1's frame
ranges and clip mode, version 2's index-bound GLB block) with its cure:
re-export the file.

**Every take is an exact slice of the animation.** Maya's take split restricts
each curve to a take's window before writing it, so a plainly keyed curve with
no key inside a shot used to give that shot no channel (the node played its
rest pose for the whole shot in Unity) and a take's keys stopped at its last
in-window key. `FbxUtils.apply_takes` therefore arms Resample All
(`FbxUtils.RESAMPLE_ALL`) for the write, a GLB-only export's intermediate
included (its takes are the GLB's clips). Every animated curve is keyed on
every frame of the bake range first, and each take then carries every channel
across its whole window (measured in Unity 6000.3, before and after).
`ptk.ExportVerifier.check_fbx_take_channels` (the Scene Exporter's *Verify The
Written File*) fails a file whose takes are not exact slices, and
`check_fbx_takes` one carrying a take no shot names.

**The shot name IS the clip name, verbatim.** `ShotStore.NAME_PATTERN`
(`[A-Za-z0-9_]+`, unique among the scene's shots ignoring case — Unity joins
a shot to its take ignoring case) is the set every carrier keeps unchanged, so the
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
`extras.fromFBX.userProperties`). The converter makes each take one glTF
animation of the same name -- one per shot, in timeline order -- and the build
copies the record verbatim into the root `extras.animation`
(`MeshConvert.apply_glb_animations`, automatic on every conversion) and removes
the FBX's copy: one layout, two containers, nothing bound to a glTF index:

```json
{ "version": 3,
  "shots": [{ "clip": "SHOT_A", "description": "…" },
            { "clip": "SHOT_B", "metadata": { "section": "B" } }],
  "fps": 24.0 }
```

`shots` is the record's list as it stands, whatever clips the file holds. A
shot plays the glTF animation its `clip` names, and the clips played in turn are
the whole sequence; `fps` is the rate glTF cannot hold. The keyed visibility and the authored fades and highlights
ride the takes as curve proxies, so each plays in its clip by construction
(the conversion reads them off the FBX itself, per take). The bundled WebXR preview reads
the block for its clip picker; `MeshConvert.verify_glb` reports the counts to
a recipient.

## Maya side

```python
from mayatk.anim_utils.shots._shots import ShotStore
ShotStore.active().publish_export_view()      # publish the shot record now
# …or set ShotStore.active().auto_publish_export = True to republish on every save
# …or, for true "any export carries shots" with no staleness:
ShotStore.enable_auto_export()                # before-export hook: republish fresh + apply takes
# (a File > Export then ships one take per shot: the hook's after-export step
#  drops Take 001, as the Scene Exporter and the hand-offs do)
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
exports one FBX that Unity imports into both its shots and the audio cues
its clips play. The hook is reference-counted: enable
either or both; each runs once per export, fault-isolated, **in the records'
declared dependency order (`audio` reads `animation`, so shots
first)** — the audio producer cuts each sound into cues per clip
(`ptk.AudioUtils.clip_cues`): per shot take, in seconds from the take's start,
with its offset into the sound and its stop, so a sound across a cut stops at
the cut and the next clip plays it on from there. A scene with no shots cues
its one take over the window the written file declares for it. Maya's writer
declares the playback range whatever the take holds, and Unity cuts its clip
by that window, so after every FBX write of a shotless scene (the Scene
Exporter's, `FbxUtils.export`'s, a File > Export the hook sees) the file is
measured: its take's window is rewritten to the keys it holds
(`ptk.FbxMedia.fit_take_windows`, an ancestor the plug-in carried along
included) and its `audio` record is cut again over that window
(`FbxUtils.drop_undeclared_takes`). The scene's range never moves. A write
that skips that step (the Game Exporter's `FBXExport`, which fires no scene
event) ships the playback range as its window, and the cues stamped before it
count from there too. The cues are the source's; what a Unity user adds in the
model importer's Events tab is Unity's. Verified by
`test/test_audio_clips_export.py` (`TestAudioClipsRealExport`: real Scene
Exporter, `FbxUtils.export` and raw `cmds.file` writes read back with
`ptk.FbxFile`).

Every Scene Exporter run does this for you and includes `data_export` in the
export set. Clips appear automatically in
Unity (named per take) with **no engine code**. The record only needs a small
reader:

## Unity side

A ready-to-use **`ShotList`** component ships in unitytk's `com.m3trik.unitytk/Features/Shots/Runtime/`
(`ShotList.cs`, Add Component ▸ unitytk ▸ Shots; its importer, `ShotsImporter` in `Editor/`, is an
`AssetPostprocessor` that reads `animation`, writes the entries onto the model root at every import, and joins
each shot to every clip cut from its take). Deploy it with the core it compiles against — the kernel's `Imports.IsDelivery` gates the
import, and the carrier feature's `CarrierImport` (`Features/Carrier/Editor/`, served
through the `IRecordSource` contract) reads the record: install the whole set as the
`com.m3trik.unitytk` package with Unity's Package Manager (one `Packages/manifest.json`
line; the package's README gives it).
Verified end-to-end by unitytk's Shots sample tests (`ShotsSampleTests`) on the
Maya-made sample, and on a Blender-made delivery of the same scene and on one
exported from a Maya assembly by the workspace's end-to-end suite
(`m3trik/test/unity_e2e/test_sample_twins.py`): one clip per shot, named after
it, as long as the shot and in its place, joined to its description.

Each record entry names its clip; the importer resolves it to the clip itself
(one per take) and warns when a record names a clip the file does not carry.
There are no frames to read: a clip plays in seconds from its shot's start.

The minimal read, for reference — clips import natively; Maya exports the
`animation` attr as an FBX *user property* on the `data_export` GameObject:

```csharp
using UnityEditor;
using UnityEngine;

class ShotRecordPostprocessor : AssetPostprocessor
{
    [System.Serializable] class ShotRec { public string clip, description; }
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
            // Join by take: every clip whose takeName == s.clip, ignoring case.
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
