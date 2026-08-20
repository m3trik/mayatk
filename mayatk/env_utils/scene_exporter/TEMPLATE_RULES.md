# Scene Exporter — Export Template Rules

The single source of truth for authoring a Scene Exporter **export template** (by
hand or by handing these rules to an agent). An export template captures the
panel's run configuration — tasks, checks, FBX preset, output format, log
settings — under a name, so a project setup is one click away.

Templates are managed from the **Preset** combo at the top-left of the panel —
**Refresh** (reload the active template) and **Save** icons, plus a **⋯** menu
(Rename / Open Folder / Delete for your own templates). The FBX plug-in preset is
a row of the **Settings** combo beside it (and a key inside the template).

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
  a template may safely set only the settings it cares about. Loading logs one
  short warning when the template doesn't cover some of the panel's settings
  (they keep whatever the previous template or session set) — expected for a
  deliberately minimal template; for a full snapshot it means the template
  predates newer settings and a re-save (Rule 0) will capture them. The
  affected setting keys are listed in the debug log.

## Rule 2 — values match the control's type

| Control | Value in the template | Examples |
|---|---|---|
| Checkbox (most tasks/checks) | `true` / `false` | `"smart_bake": true`, `"check_duplicate_materials": true` |
| **FBX preset** (`cmb000`) | the preset **name** (filename, no extension) | `"cmb000": "unity_animation"` |
| Text field (`txt002` regex) | a string | `"txt002": "_module->"` |
| Numeric field (`check_texture_file_size` max size in MB) | a number; `0` disables the check | `"check_texture_file_size": 16` |
| Other dropdowns (units, framerate, output format, log level) | the **option's position** (0-based integer) | `"cmb004": 0`  ← FBX |
| **Texture file type** (`texture_file_type`, a Tasks row) | position: `0` Original, then one per container (PNG … HDR, KTX2 last). The container EVERY texture ships in — scene maps and a GLB's embedded copies alike; each destination clamps what it cannot carry (KTX2 rides the GLB only, with standard PNG/JPEG fallbacks embedded so the GLB re-imports anywhere; a GLB falls back to PNG for anything glTF cannot embed) | `"texture_file_type": 0`  ← Original |
| **Optimize Textures** (`texture_optimize`, a Tasks row) | position: `0` OFF, `1` Optimize (no resize), then Optimize + Max 512/1024/2048/4096/8192, Optimize + Template Budget last | `"texture_optimize": 1`  ← Optimize |
| **Texture template** (`cmb005`, a Tasks row) | position of the map-registry workflow; `0` = As Authored | `"cmb005": 0` |

> Replaced keys: `cmb006` (GLB-only container) is now `texture_file_type`;
> `glb_optimize_textures` is gone — **Optimize Textures** covers the GLB's
> resolution too; and the old `optimize_textures` checkbox + `texture_max_size`
> dropdown pair is now the single `texture_optimize` combo. A template carrying
> `cmb006` still loads (its value is read as the new dial) and the retired flag
> is ignored; a template carrying the old optimize pair trips the "doesn't
> cover N new panel settings" warning instead of silently dropping its size
> ceiling — re-save (Rule 0) to migrate it.

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
