# Shot Sequencer

A timeline editor for per-shot animation. Shots sit on the ruler's shot lane,
each animated object is a track, and its keys show as clips, as rows of keys, or
as curves in a graph editor. Every edit is undoable (Ctrl+Z), and the scene
stays the source of truth: the sequencer reads it and writes back to it.

The same panel ships in Maya (`mayatk`) and Blender (`blendertk`); the few
differences are listed under [Maya and Blender](#maya-and-blender).

**Contents:** [Getting started](#getting-started) · [Views](#views) ·
[Shots](#shots) · [Clips and keys](#clips-and-keys) · [Shot edges and
gaps](#shot-edges-and-gaps) · [Graph view](#graph-view) · [Ruler, tracks and
markers](#ruler-tracks-and-markers) · [Keyboard](#keyboard) · [Header
menu](#header-menu)

## Getting started

1. Click **+** beside the shot dropdown to create a shot (or build shots from a
   sheet in the Shot Manifest).
2. Pick a shot in the dropdown, or step with **◄ / ►**. The shot's objects
   load as tracks, the playback range follows it, and the timeline frames it.
3. Drag clips to retime animation; drag a shot's edges on the ruler to resize
   the shot.

## Views

Two buttons beside the shot dropdown choose what is shown:

- **Scope** cycles **Current → Adjacent → All**: the shots on screen.
- **View** cycles **Standard → Compact → Graph**:
  - **Standard** — each object a clip; expand a track (double-click its header)
    for one row per animated attribute, its keys drawn as a small value curve.
  - **Compact** — narrower rows, keys as flat ticks, and a collapsed object's
    keys as ticks along its row's bottom edge.
  - **Graph** — the keys as curves in value space, edited as in Maya's Graph
    Editor (see [Graph view](#graph-view)).

The **Options** box across the top of the track list holds the active view's
own settings, grouped under titles (Standard and Compact: *Display* > *Show
Internal Holds*; Graph: see below).

## Shots

- **Dropdown** — select a shot. Right-click it for Edit Shot, Generate Next
  Shot, Delete Shot and Delete Shots (Stale / Empty / All). Double-click it to
  edit the name, start, end and description in place.
- **◄ / ►** — previous / next shot. **+** — append a new shot.
- **Shot lane** (the band under the ruler) — right-click a shot for Edit, New
  Shot (before / after), Split Here (or at the current time), Merge (previous /
  next), Move To (re-slot it among the shots), Add Frames, Trim Empty Space
  (leading / trailing) and Delete (the shot, its keys and its frames). The
  selected shot is tinted, with a rule along the top of the lane and ticks at
  its bounds.
- **Ctrl+Shift+Click** anywhere on the timeline switches to the shot under the
  cursor.

## Clips and keys

| Gesture | Effect |
|:---|:---|
| Drag a clip | Move its keys in time; later keys ripple |
| Drag a clip's edge | Scale its keys (several selected clips scale as one) |
| Shift+Drag | Cross shot bounds without moving them |
| Ctrl while dragging | Snap to whole frames |
| Shift with keys selected | A scale box: drag an end to retime; Alt scales about the playhead |
| Drag a key's handle | Reshape every selected key's tangent |
| Ctrl / Shift / Alt + handle drag | The grabbed key only / every selected key gets this exact tangent / break the tangent |
| Right-click | Lock / Unlock, Store Keys, Retrieve Stored Keys, Move or Copy to Shot; on a key: tangent types, Break / Unify, the key edits under Edit |

A drag that lands on a frame already carrying keys shows a guide; *Snap to
Keys* in the header menu also pulls the drag onto it.

## Shot edges and gaps

Dragging a shot's edge changes the shot; only Shift retimes its keys:

| Gesture | Effect |
|:---|:---|
| Drag an edge | Move the bound; the neighbouring shots move with their keys to keep the gaps |
| Ctrl+Drag | Move the bound and no key: the shot grows over the keys beyond it (a neighbour's bound gives way) or shrinks and leaves its keys |
| Shift+Drag | Retime: the shot's keys scale into the new span |
| Alt+Drag | Resize about the shot's centre |
| Drag the ruler band | Move the shot with its keys |

A gap's edge belongs to the shot beyond it: drag it to slide that shot, Ctrl
moves that bound only, Shift retimes it. Drag a gap's body to slide the gap;
right-click it to lock it. The caps before the first shot and after the last
are those shots' own bounds.

Turn on *Shortcut Overlay* in the header menu to keep this legend in the
timeline's corner, with the group under the pointer lit.

## Graph view

The graph replaces the track rows with curves; the track list becomes a curve
outliner (object > attribute, with a filter). Shot lane, ruler, playhead and
markers stay.

| Gesture | Effect |
|:---|:---|
| Click / drag a box | Select keys (Shift toggles, Ctrl removes, Ctrl+Shift adds) |
| Click a curve | Select its keys (same modifiers) |
| Drag a key, or Middle-drag | Move in time and value |
| Shift+Drag | Move along the axis the drag starts on |
| Ctrl+Drag | Snap time to whole frames |
| Drag the selection frame's edge / corner | Scale time or value about the far edge / both (Alt: about the centre) |
| Double-click a curve | Insert a key there |
| Drag a handle | Reshape the tangent (Alt breaks it; Shift gives every selected key this tangent) |
| Alt+Middle-drag | Pan (Alt+Shift: along one axis) |
| Alt+Right-drag | Zoom each axis (Shift: one) |
| Ctrl+Alt+Drag | Zoom to the box (right to left zooms out) |
| Wheel | Zoom (Ctrl: time only, Shift: value only) |
| A / F | Frame all / the selected keys (none selected: the shot) |
| I / Delete | Key the curves at the playhead / delete the selected keys |

A drag shows its frame, value or scale factor beside the cursor; Escape cancels
it. Right-click for tangent types, Break / Unify, weighting (Maya), pre / post
infinity, Delete Keys, Key at Playhead, framing, and the key edits the track
rows offer (copy / paste, Euler Filter, ...).

The Graph view opens framed on the shot, as F frames it; A frames every key.
Its options (the Options box, or the right-click menu):

- *Display* — **Normalize** (each curve drawn in [-1, 1]), **Show Infinity**
  (the curves past their ends, dashed), **Dim Outside Shots** (veil the time
  outside the shots in the scope).
- *Behavior* — **Auto Load Selected** (show the selected objects' curves; off
  pins the display), **Live Update** (show a drag in the scene as it happens,
  before the release commits it).

## Ruler, tracks and markers

- **Ruler** — click or drag to move the playhead, double-click to add a
  marker, wheel to zoom, middle-drag to pan.
- **Tracks** — double-click a track header to expand its attribute rows.
  Right-click for hide, delete, or reveal in the Outliner.
- **Markers** — M, or double-click the ruler, adds one. Drag to move;
  right-click to edit the note, colour or style.
- **Audio** — discovered from the scene's audio (see below); Move to Shot
  groups audio with the animation.
- Right-click the ruler, or the tracks clear of any shot, for the timeline's
  own menu (markers and display toggles).

## Keyboard

| Key | Action |
|:---|:---|
| ← / → | Previous / next key |
| Shift+← / → | Step one frame |
| Home / End | Start / end |
| F | Frame the shot (Graph: the selected keys, else the shot) |
| M | Add a marker |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo |
| Delete | Delete the selected keys, or the selected shot when nothing is selected |
| Ctrl+C / Ctrl+V | Copy the selected keys / paste them at the playhead |

Header menu > **Shortcuts…** lists and rebinds them.

## Header menu

- **Snap** — the frame interval clip edges snap to (0 = free).
- **Snap to Keys** — pull clip and key drags onto frames that carry keys.
- **Extend to Keys** + **Extend Distance** — grow the current shot to cover
  keys set outside it, within that many frames (never into a neighbouring
  shot).
- **Shortcut Overlay** — Off, On, or On Modifier (while Ctrl, Shift or Alt is
  held).
- **Playback Range** — Off, Follows Scope, or Locked to Shot.
- **Track Order** — Visible (objects of the shots on screen) or Global (every
  object, so tracks never reorder between shots).
- **Select Members on Load**, **Frame on Shot Change**, **Playhead to Shot
  Start** — what changing shots also does.
- **Attribute Colors**, **Shortcuts…**, **Shots…** (shot generation, gap and
  editing settings).

The header's refresh button rebuilds the sequencer from the scene.

## Maya and Blender

| | Maya | Blender |
|:---|:---|:---|
| Audio tracks | Maya audio nodes | Sequencer sound strips |
| Tangent types | Maya's (Auto, Spline, Clamped, Linear, Flat, Stepped, Plateau, ...) | Handle types and interpolation modes |
| Weighted tangents | Weighted / Non-Weighted in the graph menu | Always weighted (a handle carries its length) |
