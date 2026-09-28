# Articulated Rig

A rig for props of **rigid parts on joints** -- a desk lamp, a magnifier arm, a
microphone boom, a monitor arm: parts that turn on hinges, swivels and balls and
slide in and out of each other, and that a **hand grabs and moves** at runtime
in Unity or a WebXR headset, as well as an animator keys in Maya.

[← mayatk docs](README.md) · engine `mtk.ArticulatedRig`
(`rig_utils/articulated_rig/`) · the math `ptk.ArticulationModel`,
`ptk.ArticulationAnalysis` (pythontk `geo_utils/articulation/`) · the runtimes
unitytk `ArticulatedRigController` and the WebXR preview's `articulated_rig`
script ([WebXR preview](https://github.com/m3trik/pythontk/blob/main/docs/webxr_preview.md)).

## The idea: one set of numbers everywhere

Every joint has a few **channels** -- a hinge's angle, a slide's offset, a
ball's three angles -- and everything reads and writes exactly those:

```
Maya FK controls (keyed)  ─┐
Maya Grab Tool            ─┤                   ┌─ Unity: Animator clip / hand grab
                           ├─► joint channels ◄┤
the export's baked clip   ─┘                   └─ WebXR: animation mixer / hand grab
```

A control sits in its joint's rest frame, so its `rz` IS the hinge angle -- no
conversion anywhere. What an animator keys is what an engine plays and what a
grab writes, and the runtimes solve a grab with ports of the same solver the
Maya Grab Tool runs (`ptk.ArticulationModel`, pinned by
`ptk.ArticulationConformance`'s golden cases in every port).

## Using it

**Panel:** tentacle ▸ Rigging ▸ Quick Rig ▸ **Articulated Rig**
(`MayaUiHandler.instance().get("articulated_rig")`).

1. Select the prop's group (the analysis descends through single-child
   wrappers like `GRP > LOC > asset` to the level holding the parts), or its
   parts root first with **Parts: Selection Order**.
2. **Analyze Selection.** The table proposes one joint per moving part and says
   why (below). Correct any joint's **Type** there.
3. **Build Rig.** Pose it with the controls or the **Grab Tool** (drag a part in
   the viewport; one undo per drag; keyed on release with Auto Key on).

**After the build** -- each an edit of the plan and a rebuild that carries the
animation across, one undo step each:

| Action | What it does |
|:---|:---|
| **Split Off Selected** | Gives the selected parts a joint of their own (a telescope left out at build time). The new joint starts at 0, which changes no pose -- every key already made still holds. |
| **Fold Selected Part's Joint** | Folds a joint's link back into the one it hangs off. Its own curves are dropped (warned); everyone else's stay. |
| Type in the table | Retypes a built joint; curves on the channels both types share are kept. |
| **Set Limits From Pose** | Pose a joint to where it physically stops, select its control, press: each non-zero channel's value becomes that side's limit. **Clear Limits** frees them. |
| **Rebuild** / **Remove Rig** | Rebuild from the plan (animation kept) / remove, handing every part back exactly as it was. |

From code:

```python
import mayatk as mtk

plan = mtk.ArticulatedRig.analyze("MAGNIFYING_GLASS")           # a proposal, world space
rig = mtk.ArticulatedRig.create(plan["links"], plan["joints"])  # or joints=None: propose
rig.insert_joint(["LEG_4"], joint_type="slide")                 # post-rig telescope
rig.set_limits("LEG_2", "rz", -60, 95)
rig.pose_to("MAG_GLASS", target=(10, 150, -20))                # land a part on a point
rig.teardown()
```

## What the analysis reads

A **part** is one mesh or group that moves on its own; its **shells** are its
connected pieces of geometry (a tube, its end housings, a wing knob). Where two
parts touch, the shells there say how they move:

| Joint | Channels | Evidence |
|:---|:---|:---|
| **slide** | `tx` | the two parts' bodies are tubes on one axis, one inside the other, overlapping; its travel is the overlap (20% stays inserted at full extension; it retracts up to 80% of its exposed length) |
| **ball** | `rx ry rz` | the smallest round shell where they meet |
| **hinge** | `rz` | a small shell standing off the arm's plane where they meet (the knob or bolt it turns on), else where the two bodies' axes cross |
| **swivel** | `rx` | a stub of one part inside a housing of the other, on one axis |
| **universal** | `rx rz` (order `zyx`: the swivel outermost) | a swivel with a hinge knob on it (a clamp base's turn and tilt) |

An arm whose parts lie in one plane hinges about **the plane's normal**. On the
production magnifier every knob stands 3.4 cm off the plane, while a wing knob's
own long axis is its span and lies IN the plane -- so the plane, not the knob,
gives the axis. Authored pivots are not used: on the production magnifier four
of six sat at bounding-box centres.

Rotations are left unbounded (geometry cannot say how far a knob lets an arm
fold); set them with **Set Limits From Pose**.

## The rig

```
<asset parent>
├─ <parts of the root link>              ← stay where they were
└─ <name>_RIG                            ← articulatedRigData (the plan, in its own space)
   ├─ <name>_controls_GRP                ← rig helpers: dropped at export
   │   └─ <name>_<part>_CTRL_GRP > <name>_<part>_CTRL > …   (nested FK)
   └─ <name>_<part>_jnt > <name>_<part>_jnt > …             ← the skeleton that ships
       └─ <the part>                     ← parented under its joint
```

- **Joints:** X runs down the link, Z is the hinge axis; the rest pose is in
  `jointOrient`, so every channel reads 0 at rest. `segmentScaleCompensate` is
  off (a joint never scales). Every joint carries a part, which is what keeps
  it through the export's rig-helper sweep.
- **Controls → joints:** a rotation channel is wired straight in; a slide goes
  through `multiplyDivide` + `plusMinusAverage`
  (`translate = rest + axis × tx`, the axis the joint's rest X in its parent's
  space). The bake walks both. Utility nodes, controller tags and the controls
  set are owned by message connection (`articulatedRigNodes`), so a teardown
  deletes exactly those however renamed.
- **Parts** are reparented under their joints and their own channels captured
  first; the teardown restores parent and every channel exactly. A part
  something already drives (keys, a constraint) is refused.

## What ships

**The skeleton and the parts** -- ordinary node animation any engine plays. The
controls are Maya controllers, so the export's **Exclude Rig Helpers** sweep
drops them with their groups (`RigGraphExtractor.machinery` seeds every
controller-tagged transform).

**The `articulation` record** (`ptk.SceneRecords.ARTICULATION`, v1, on the
`data_export` carrier; produced by `ArticulatedRig.export_record`, published at
build and on every edit, opted into every FBX export):

```json
{"version": 1,
 "rigs": [{"name": "magnifying_glass",
           "joints": [{"name": "magnifying_glass_LEG_1_jnt", "parent": null,
                       "t": [x, y, z], "q": [x, y, z, w], "rotate_order": "zyx",
                       "channels": [{"channel": "rx", "min": null, "max": null, "weight": 1.0},
                                    {"channel": "rz", "min": -30, "max": 60, "weight": 1.0}]}, …],
           "grab": [{"node": "LEG_1", "joint": 0}, …]}]}
```

- `t` / `q`: the joint's rest translate and orient **in its parent's space**
  (the root joints' parent is `<name>_RIG`: rig space). A joint poses as
  `rotation = q ⊗ Euler(rotate_order; rx, ry, rz)`,
  `translation = t + q·(tx, ty, tz)` -- Maya's `[R][JO][T]`, and the local TRS
  the FBX hop delivers.
- Channels in degrees and in the parent's units; `null` limits are unbounded;
  `weight` is how readily a grab moves that channel against the others.
- **No `unit_scale`:** a runtime measures its own unit against the rest
  translations of the joints that cannot slide (`ArticulationModel.scale_of`),
  which covers an FBX unit conversion and a scaled prop alike (measured on the
  magnifier's GLB: 0.01, centimetres to metres, under a ×2.47 asset node).
- `grab`: the parts a hand takes, each by name UNDER its joint's node (a
  production assembly repeats part names).

The GLB conversion binds it as root `extras.articulation_web`: the record with a
glTF node index on every joint and grab entry
(`ptk.MeshConvert.apply_glb_articulation`, run by `fbx_to_glb`).

## The runtimes

**WebXR preview** -- the packaged `articulated_rig` script turns on by itself
for a GLB carrying `articulation_web`. Every grabbed part is registered with the
page's `viewer.grab`: the mouse (press on a part, drag in the camera-facing
plane, wheel to push or pull; the orbit never sees the press), a controller's
grip or trigger, or a tracked hand's pinch takes hold. A grab on a link hanging
off a ball takes the hand's turn; with a mouse the link keeps the rotation it
was taken with. The released pose holds while the clip plays on, until
**Return to animation** or the next Play. The rig panel has a slider per
channel, the joints' values, **Reset** and **Joint axes**. The model in the
script is exported by name (`import { ArticulationModel } from
'./articulated_rig.js'`, no three.js) for a production app to vendor.

**Unity** -- unitytk's `ArticulatedRigController` (component `articulated_rig`):
the importer attaches it from the record and adds a convex collider to each
grabbed part without one; a grab API independent of any input system
(`BeginGrab` / `UpdateGrab` / `EndGrab` / `ReturnToAnimation`, `grabRotation`
Hand / Keep / Free), `ArticulatedRigPointerGrab` for the mouse, and an XR
Interaction Toolkit 3 adapter (`ArticulatedRigXR/`) compiled only when that
package is present. A held pose is re-applied in `LateUpdate`, after the
Animator. Measured on the magnifier's real Maya export in Unity 6000.3 and
2022.3: the rest pose through the model matches the imported transforms to
5e-6, and a hand grab matches pythontk's solve to 1e-6. See unitytk's
templates README.

**Handedness:** a glTF keeps Maya's; Unity's FBX importer mirrors X, so the
Unity runtime converts at its boundary -- positions `(x, y, z) → (-x, y, z)`,
rotations `(x, y, z, w) → (x, -y, -z, w)` -- and runs the model in Maya's
convention.

## Grab semantics

The solver (`ptk.ArticulationModel.solve`) moves only the channels between the
root and the held link, by weighted damped least squares, never leaving a
limit; a target out of reach leaves the point as near as the limits allow. A
held link on a **ball** is split the way a wrist is: the ball takes the hand's
rotation (clamped), the chain above places the ball's centre, and a last
position solve closes whatever the limits left open (measured over 160 cold
starts: 4 missed by more than 1% of the reach, all pinned against a limit). A
grab followed frame by frame starts each solve from the last state, so a
channel moves as little as it can.

## Tests

- pythontk `test/test_articulation.py` -- the model, the analysis on the
  magnifier's shape, the conformance document.
- pythontk `test/test_articulated_rig_web.py` -- the GLB pass, auto-activation,
  and the real page in headless Edge: the JS model against every conformance
  case, a grab against the Python solve, a real mouse drag, a headset grip on
  synthetic input, a slider; `ARTICULATED_RIG_LIVE_GLB` runs it on a real
  export.
- mayatk `test/test_articulated_rig.py` -- build, teardown, Maya against the
  model, the grab and the Grab Tool, limits, undo, the post-rig edits carrying
  animation, and a refused edit leaving the rig and its keys standing.
- mayatk `test/test_articulated_rig_panel.py` (the runner's GUI pass) -- the
  panel acted on as a user does: Analyze, a row's type combo before and after
  the build, Split Off Selected, Set Limits From Pose, Remove, the Grab button.
- mayatk `test/articulated_rig_live_check.py` -- a real prop through the real
  export (`ARTICULATED_RIG_LIVE_SCENE`); writes the GLB the web test loads.
- unitytk `test/test_articulated_rig_runtime.py` -- the C# model against the
  conformance cases, the controller, the importer and the Animator override in
  batch Unity; `ARTICULATED_RIG_LIVE_FBX` imports a real export (the FBX the
  mayatk live check writes beside its GLB).

## Not yet

- A **blendertk twin** (`.claude/BACKLOG.md`: *ArticulatedRig has no blendertk
  twin*); the pure half is already shared.
- **Physics** (a joint driven by a physics engine rather than the kinematic
  solver) -- the channels map onto revolute / prismatic / spherical joints, so
  a physics mode would read the same record.
- **Closed loops** (a parallelogram lamp arm) and passive struts (a gas spring
  between two links -- `TelescopeRig` is the primitive for one).
