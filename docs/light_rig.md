# Light Rigs

Save the lights in a scene as a named **rig**, swap the scene between rigs, and restore any one of them exactly.
Engine: `mtk.LightRig` ([`light_utils/light_rig.py`](../mayatk/light_utils/light_rig.py)); panel: tentacle's Light Rigs tool (`slots/tools/light_rig.py` + `ui/tools/light_rig.ui`), opened from its Lighting menu (**Light Rigs**).

## What a rig is

A rig is **the lights under one scene group** (the rig group, e.g. `MOODY_LIGHTS`), plus the **plugs it overrides** on nodes it does not own -- office lights turned off, a material's emission.

- Applying a rig replaces the lights under its group and sets its overrides. The group keeps its parent, so a rig parked under a group the exporter skips (`TEMP`) stays there.
- A light outside the group is never deleted.
- An override wins wherever the rig is applied: on a driven plug (an office's emission fed by its texture's alpha, where the rig was made with it constant) the connection is broken and reported, and **Swap Back** and undo reconnect it. A locked plug is left alone and reported.
- One undo reverts the whole swap.

## The library

Rigs are JSON presets in `ptk.LightRigPayload.library()` (`UserConfig.user_config_root()/light_rigs`, under no package), so every scene, host and tool -- the look match included -- shares one library. An older `mayatk/light_rigs` folder is copied in on first use.

Each rig's sidecar holds its metadata:

| Key | Holds |
|---|---|
| `description` | Free text |
| `parent` | The rig it was saved from |
| `group` | The rig group |
| `lights` | Light count |
| `read_only` | Locked: it cannot be saved over, renamed or deleted |

## The scene record

Which rig each group holds lives in the scene's private `ptk.SceneRecords.LIGHT_RIGS` record (on `data_internal`), never on the group. A node attribute would ride every FBX as a user property. Per group, the record holds:

| Key | Holds |
|---|---|
| `rig` | The rig's name |
| `print` | Its fingerprint when it went in: what *edited since* compares |
| `extras` | The overrides and carried sections it went in with |
| `previous` | The rig the last swap replaced: what **Swap Back** returns to |

The record belongs to its scene, so Swap Back never rebuilds another scene's rig.

## Payload (version 1)

```json
{"version": 1,
 "group": "MOODY_LIGHTS",
 "lights": [{"name": "L_KEY", "type": "spotLight",
             "pos": [0, 120, 80], "rot": [-35, 0, 0],
             "scale": [1, 1, 1], "rotate_order": 0, "visible": false,
             "attrs": {"intensity": 3.0, "color": [1.0, 0.8, 0.6], "aiExposure": 12, "coneAngle": 40},
             "textures": {"color": {"file": "C:/maps/key.exr", "colorSpace": "Raw", "plug": "outColor"}},
             "user_attrs": {"geoLightSource": {"type": "string", "value": "LAMP_A"}},
             "filters": [{"type": "aiGobo", "attrs": {"rotate[0]": 30},
                          "textures": {"slidemap": {"file": "C:/maps/tri.png", "colorSpace": "Raw"}}}]}],
 "existing": {"LIGHT_A_areaLight": {"intensity": 0.0}},
 "materials": {"MAT_ROOM": {"emissive_intensity": 0.05}},
 "fixtures": [{"name": "DESK_LAMP", "mode": "lamp",
               "meshes": ["DESK_LAMP_bulb"], "lamps": {"DESK_LAMP_light": 4.0}}]}
```

- **Units: centimetres and degrees**, whatever units the scene works in -- `pos`, `aim`, `radius`/`aiRadius` and any length an attribute or override holds; `rot`, `cone`, `penumbra`, `gobo_rotate` and any angle (`ptk.LightRigPayload`). An area light's `scale` scales its plate, 2 cm square. `capture`, `apply`, `compare` and `save` run under `mtk.Units.working_as()`, so a rig saved in a metre scene lands in a centimetre one where it was, and the scene keeps its own units. A driver an override took over is reconnected in the scene's own units: Maya sizes the unit conversion it inserts from them.
- **`capture`** records each light's world placement and *what differs from a fresh light of its type*: any attribute, a multi's elements (an `aiGobo`'s `rotate[0]`), user attributes, file textures, and `aiFilters` light filters.
- **Other top-level keys** (`viewer`, `source`, ...) are carried through untouched, for the tools that write them.
- **A newer `version`** is refused.
- **`fixtures`** (`ptk.LightRigFixture`): how each light fixture of the scene lights the room, exactly one way -- `glow` (its emission is seen and lights nothing), `emissive` (its emission lights the room) or `lamp` (its lamps light it, each at its intensity). `normalize` turns a mode into `existing` overrides on the fixture's meshes (`aiVisibleInDiffuseReflection` / `aiVisibleInDiffuseTransmission`) and lamps (on at their intensity, or 0); an override the rig names outright wins.
- **An Arnold bridge's emission** overridden by a rig lands on the material the bridge follows (`ArnoldBridge.authored_plug`), what the export ships. Where the rig names that material too, its own value wins and the difference is reported.

**Look-match spelling.** A light may be written the way the look-match tool writes it:

- `type`: `spot`, `point`, `area`, `aiArea`, `directional`, `photometric`.
- Placement: `aim` (a point the light's -Z faces); an explicit `rot` wins over it.
- Attributes: `cone`, `penumbra`, `dropoff`, `exposure`, `radius`, `spread`, `normalize`, `samples`, `decay`, `specular`, `diffuse`, `volume`, `roundness`, `camera`, `color`.
- Colour temperature: `kelvin` sets Arnold's colour temperature when no `color` is given.
- Gobo: `gobo` and `gobo_rotate` build an `aiGobo` filter.
- Ignored notes: `kelvin_note`, `up`, `note`, `notes`.

`LightRig.normalize` turns this spelling into the form above. An unknown key is refused by name. The spelling is pythontk's contract (`ptk.LightRigLight`); `LightRig.TYPES` and `LightRig.FRIENDLY` map it onto Maya's names.

## API (scripts and agents)

```python
import mayatk as mtk

rig = mtk.LightRig(group="MOODY_LIGHTS")
rig.save("moody_v1", description="first pass")   # the scene's rig -> the library
rig.names()                                       # saved rigs
report = rig.apply("moody_v2")                    # swap; report["skipped"] says what was left alone
rig.compare("moody_v1")                           # what applying it would change, one line each
rig.applied()                                     # ("moody_v2", edited_since)
rig.swap_back()                                   # back to moody_v1; again to toggle
rig.capture()                                     # the scene's rig as a payload
mtk.LightRig.diff(a, b)                           # payload vs payload; orientations compared, not Euler triples
```

`apply` also takes a payload dict (`rig.apply(payload, snapshot=False)` for a build that keeps the current swap-back state).

## Panel

The dropdown is the library: picking a rig only shows it.

| Control | Does |
|---|---|
| **Apply** | Puts the picked rig in the scene |
| **Swap Back** | Returns to the rig that Apply replaced |
| **＋** | Saves the scene's rig (name, description, group) |
| **☰** | Rename, duplicate, edit description, lock or unlock, compare with the scene, delete. A double-click renames |

The footer names the rig in the scene and flags it when it has been edited since.

Blender has no twin yet (BACKLOG: *Light Rigs has no blendertk twin*); tentacle hides the button there.
