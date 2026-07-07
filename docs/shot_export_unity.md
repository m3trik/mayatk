# Shot data in the FBX → Unity

> Built on the shared scene-data-node system — see **[data_nodes.md](data_nodes.md)**
> for the `data_internal` / `data_export` two-node model this page assumes.

mayatk's Shots system can publish a self-describing **export view** onto the
shared `data_export` node, so it rides into **any** FBX export (the Scene
Exporter, File ▸ Export, Game Exporter, scripts). Two channels, both plain
string (JSON) attrs on the `data_export` transform:

| Channel (attr) | Shape | Consumed by |
|---|---|---|
| `fbx_takes` | `[{ "name", "start", "end" }, …]` | the FBX exporter, via `FbxUtils.apply_takes` — becomes one **AnimStack (Unity AnimationClip)** per shot |
| `shot_metadata` | `{ "version": 1, "shots": [{ "clip", "description", "objects", "section" }, …] }` | engine-side scripts (this doc) |

**Invariant:** the take `name` and the metadata `clip` are produced from a single
resolution pass, so they are byte-identical — `clip` is the join key from a
metadata record back to its imported clip. Ranges live only in `fbx_takes`
(the clip already owns them); `shot_metadata` carries only the extras a clip
can't hold.

## Maya side

```python
from mayatk.anim_utils.shots._shots import ShotStore
ShotStore.active().publish_export_view()      # write both channels now
# …or set ShotStore.active().auto_publish_export = True to republish on every save
# …or, for true "any export carries shots" with no staleness:
ShotStore.enable_auto_export()                # before-export hook: republish fresh + apply takes
```

### Coexistence with Audio (shared before-export hook)

`enable_auto_export` registers a **preparer** on a shared `FbxUtils` before-export
hook (`FbxUtils.register_export_preparer`); the Audio system registers its own via
`AudioClips.enable_auto_export()`. Both ride out on the **same** `data_export`
GameObject with distinct attrs — Shots' plain `fbx_takes`/`shot_metadata`, Audio's
plain `audio_manifest` (baked from the keyed `audio_clip_*` authoring state on
`data_internal`) — so a scene with both
exports one FBX that Unity imports into both a `ShotMetadataController` and an
`AudioEventController` on the prefab root. The hook is reference-counted: enable
either or both; each runs once per export, fault-isolated. Verified end-to-end by
`unitytk/test/test_shots_audio_sidebyside_integration.py`.

The Scene Exporter task **"Export Shots as Animation Takes"** does this for you
and includes `data_export` in the export set. Clips appear automatically in
Unity (named per take) with **no engine code**. The metadata only needs a small
reader:

## Unity side

A ready-to-use **`ShotMetadataController.cs`** ships in `unitytk/templates/`
(runtime component + an `AssetPostprocessor` that parses `shot_metadata`, attaches
the controller to the prefab root, and joins records to clips by name). Deploy it
alongside `RenderOpacityController.cs`, which provides the shared `ImportAllowlist`.
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
