# Shot Manifest

The Shot Manifest checks a scene's shots against a build sheet (the steps a
document expects, each with its objects and behaviors) and builds the shot
structure for the initial setup: shots, their members, behavior keys and audio.
The panel's `?` help covers using it; this page holds the rules any change to
it keeps. The engine is pythontk's `ShotManifest`; mayatk and blendertk bind it
to their scenes, and the [Shot Sequencer](shot_sequencer.md) edits what it
builds.

## What it owns

Ownership decides every feature: the sheet owns the plan, the manifest owns
what it generates (shots, their membership, its own behavior keys, which the
shot ledger records as authored), and artists own the scene's content and
their keys. Before adding a feature, ask which side of that line it writes.

- **It never creates, renames or deletes scene objects.** A missing object is
  a finding for an artist, never a placeholder. An object's name in the table
  says which scene object a step means; editing it never renames the object.
- **The panel's Build never removes a shot.** tentacle's Shot Manifest panel
  builds with `remove_missing=False`, so a shot no step pairs with is listed
  and removed by hand, one at a time. That is the panel's choice, not the
  engine's: `ShotManifest.sync` and `update` default to `remove_missing=True`
  and remove such a shot for a caller that keeps the default.
- **It never keys over an animator's keys**, and never guesses which step goes
  with which stretch of time on an unbuilt scene: steps pair through
  `ShotManifest.pair`, and placing steps onto animated regions by order is
  asked first.
- **The sheet is never written.** The user's edits (names, descriptions,
  objects, behaviors, hidden, moved and added steps) are an overlay,
  `ptk.ManifestEdits`, keyed by the sheet's own IDs and saved with the scene;
  they always apply. A step's ID stays its shot binding, so renaming a step
  renames its shot. A locked step takes no edits and Build passes it by.
  Comparing to the original sheet is a read-only view, never saved.
- **Reconcile both ways, with explicit verbs**: Build (manifest to scene), Add
  to the Manifest and Pair with Step (scene to manifest), Export (the manifest
  out, to a file or the clipboard, never into the loaded sheet).
- **A showing assessment stays current by itself.** An edit, a sheet read
  again, a hide toggle or a shot edit re-runs it (debounced), so no path leaves
  stale results up or needs another Assess.
