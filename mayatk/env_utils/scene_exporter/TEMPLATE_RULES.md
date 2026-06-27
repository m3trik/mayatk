# Scene Exporter — Export Template Rules

The single source of truth for authoring a Scene Exporter **export template** (by
hand or by handing these rules to an agent). An export template captures the
panel's run configuration — tasks, checks, FBX preset, output format, log
settings — under a name, so a project setup is one click away.

Templates are managed from the **Preset** combo's toolbar in the panel header —
**Refresh** (reload the active template) and **Save** icons, plus a **⋯** menu
(Rename / Open Folder / Delete for your own templates).

---

## Rule 0 — a Saved template *is* the live schema

Don't hand-write a template from scratch. **Configure the panel exactly how you
want it and click Save** — that produces a complete, valid template listing every
key your build supports with a correct value. To author or edit one (or brief an
agent), **start from a Saved template** and change only the values you care about.

This is what keeps the rules a single source of truth: the format below is the
stable contract; the *keys and their valid values* come from the live panel via
Save — never from a hand-maintained list that can drift.

## Rule 1 — the file is plain JSON

```json
{
  "_meta": { "version": 1 },
  "<setting key>": <value>
}
```

- `_meta` is reserved — leave it as-is.
- Every other entry is `setting key → value`.
- **Unknown keys are ignored**, and **omitted keys keep their current value** — so
  a template may safely set only the settings it cares about.

## Rule 2 — values match the control's type

| Control | Value in the template | Examples |
|---|---|---|
| Checkbox (most tasks/checks) | `true` / `false` | `"smart_bake": true`, `"check_duplicate_materials": true` |
| **FBX preset** (`cmb000`) | the preset **name** (filename, no extension) | `"cmb000": "unity_animation"` |
| Text field (`txt002` regex) | a string | `"txt002": "_module->"` |
| Other dropdowns (units, framerate, output format, log level) | the **option's position** (0-based integer) | `"cmb004": 0`  ← FBX |

> Dropdowns other than the FBX preset are stored by position, not label — so the
> reliable way to set them is in the panel, then Save (Rule 0). (If these read as
> opaque, ask for the "dropdowns by name" change — it makes every dropdown value a
> readable string like `"FBX + GLB"`.)

## Rule 3 — what is *not* templated

Machine/scene-specific fields are deliberately excluded and never saved into a
template: **output directory**, **output name**, and the **log output** pane. Set
those per-export.

---

## Illustrative example

Save your own for the exact, complete key set — this just shows the shape:

```json
{
  "_meta": { "version": 1 },
  "cmb000": "unity_animation",
  "smart_bake": true,
  "optimize_keys": true,
  "check_duplicate_materials": true,
  "check_hidden_geometry": true,
  "cmb004": 0,
  "txt002": "_module->"
}
```

## Briefing an agent

> Build a Maya Scene Exporter export template (JSON). I've attached a template I
> Saved from the panel — it lists every valid key for my build. Keep `_meta`
> unchanged. Set checkbox keys to `true`/`false`, set `cmb000` to an FBX preset
> name, set `txt002` to a regex string, and leave the numeric dropdown keys at the
> values in the attached file unless I tell you otherwise. Omit any key I don't
> mention. Return the JSON only.
