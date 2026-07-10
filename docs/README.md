[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![PyPI](https://img.shields.io/pypi/v/mayatk.svg)](https://pypi.org/project/mayatk/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Maya](https://img.shields.io/badge/Maya-2025+-orange.svg)](https://www.autodesk.com/products/maya/)
[![Tests](https://img.shields.io/badge/Tests-3725%20passed-brightgreen.svg)](../test/)

# mayatk

<!-- short_description_start -->
*Maya 2025+ tech-art toolkit built on `maya.cmds` + `maya.api.OpenMaya` (no PyMEL) — modeling, animation, materials, rigging, and scene-pipeline automation, plus tool panels and one-click bridges into Marmoset, Substance Painter, RizomUV, Blender, and Unity.*
<!-- short_description_end -->

mayatk is the Maya layer of the `pythontk → uitk → mayatk → tentacle` ecosystem: a library of composable helpers first, with a fleet of [uitk](https://github.com/m3trik/uitk)-based tool panels (Shot Sequencer, Channels, Scene Exporter, …) built on top of it.

## Installation

```bash
pip install mayatk
```

**Requirements:** Maya 2025+ (Python 3.11+).

mayatk also ships [`mayapy-package-manager.bat`](../mayatk/env_utils/mayapy-package-manager.bat) — an interactive Windows menu for installing/updating/backing up packages in Maya's bundled Python interpreter.

---

## Packages

| Package | What it covers |
|---|---|
| `anim_utils` | Animation curves, shots, blendshape animation, playblast, smart bake |
| `audio_utils` | Audio clips, ffmpeg-backed conversion, timeline-keyed events |
| `cam_utils` | Camera utilities and default-camera handling |
| `core_utils` | `CoreUtils`, `Components`, `AutoInstancer`, MASH bridge, diagnostics, preview |
| `display_utils` | Display layers, color management, exploded view |
| `edit_utils` | `Selection`, naming, primitives, snap, bevel, bridge, mirror, duplicate (linear/radial/grid), mesh graph |
| `env_utils` | `MayaConnection`, workspace, namespace sandbox, references, hierarchy manager, FBX, scene exporter |
| `light_utils` | Lighting helpers |
| `mat_utils` | `GameShader`, `RenderOpacity`, `ImageToPlane`, `MatUpdater`, shader templates, Marmoset bridge |
| `node_utils` | `NodeUtils`, `Attributes`, event triggers, [shared scene data nodes](data_nodes.md) |
| `nurbs_utils` | NURBS surfaces, `ImageTracer` |
| `rig_utils` | `Controls`, `ShadowRig` |
| `ui_utils` | `MayaUiHandler`, channel box, native menus, hotkey collision check, node icons |
| `uv_utils` | UV utilities, Rizom bridge |
| `xform_utils` | Transforms, matrices, pivot watcher |

Classes and module-level functions are exposed at the package root via the lazy-loading resolver. Both bare and class-qualified forms work:

```python
import mayatk as mtk

@mtk.undoable                           # bare form — wildcard-exposed
def operation():
    mtk.freeze_transforms("pCube1")

# Class-qualified form — explicit, no risk of collision
mtk.NodeUtils.is_group("group1")
mtk.XformUtils.freeze_transforms("pCube1")
mtk.EnvUtils.SCENE_UNIT_VALUES

sel = mtk.Selection()
```

For the full public surface (auto-generated, refreshed bi-weekly) see [`API_REGISTRY.md`](../API_REGISTRY.md).

---

## Tour

A curated subset — the packages table is the map; these are the standouts.

### Auto-instancing

Convert geometrically identical meshes to instances. Duplicates are matched by geometry signature, so rotated and scaled copies are found regardless of transform or name:

```python
import mayatk as mtk

mtk.auto_instance(tolerance=0.001, require_same_material=True)
```

### Drive Maya from outside

`MayaConnection` launches and remote-controls Maya from any Python process — the backbone of mayatk's own test suite:

```python
from mayatk import MayaConnection

conn = MayaConnection.get_instance()
conn.connect()                      # launches a fresh Maya on a free port
conn.execute("import maya.cmds as cmds; cmds.polySphere()")   # runs in that Maya, not yours
```

By default every `connect()` gets a **new** instance — an artist's open session is never touched (see Session safety below).

### Smart bake

Analyzes what is actually driven — constraints, set-driven keys, expressions, IK — and bakes only those channels, onto an override layer, with a persisted manifest that makes the bake reversible even after save/reopen:

```python
result = mtk.SmartBake(optimize_keys=True).execute()
mtk.SmartBake.restore()             # undo the bake — drivers resume
```

### Scene audit

```python
mtk.SceneAnalyzer.run_audit(adaptive=True)
# → triangle/material budgets, top-offender Pareto, missing-texture impact,
#   instancing stats (format_audit_html for an HTML report)
```

### DCC bridges

One-click hand-offs that export the selection and drive the target app with a templated script: **Marmoset Toolbag** (including a bundled JSON-RPC plugin for live Toolbag scene ops), **Substance 3D Painter**, **RizomUV**, **Blender**, and **Unity**. Built on pythontk's `HandoffBridge` engine (RizomUV uses its own script-driven flow); each ships as a tool panel.

### More highlights

- `Preview` — hermetic operation preview: tools show live results, roll back on cancel, replay on commit.
- `Components` / `MeshGraph` — component islands, border edges, edge paths, A* shortest paths, normal-angle edge selection.
- `ExplodedView` — force-based exploded view (vectorized repulsion), toggleable and reversible.
- `NamespaceSandbox` — import files into disposable namespaces for analysis or object swapping, with guaranteed cleanup.
- `HierarchyManager` — diff a working scene against a reference and repair it: create stubs, quarantine extras, fix fuzzy renames and reparents.
- `TubeRig` / `WheelRig` / `ShadowRig` — rig builders: FK / spline-IK / anchor chains from a mesh centerline, expression-driven wheel rotation, fake contact shadows.
- `ImageTracer` — trace images or Blue Pencil strokes into curves and meshes.
- `DevTools` / `WidgetInspector` — grep Maya's MEL source and globals; walk, snapshot, and diff Maya's live Qt widget tree.
- `StyleSetter` — restyle Maya's scriptable viewport colors to match another DCC.

---

## Bundled UITK Editors

`MayaUiHandler` ships with a one-line entry point for launching the
[uitk](https://github.com/m3trik/uitk) editor windows from a Maya shelf
button or script:

```python
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler
MayaUiHandler.instance().editors.show("browser")
```

| Editor key | Description |
|---|---|
| `browser` | Searchable launcher for every UI registered with the switchboard — tags, filtering, hide lists, launch options, JSON-portable presets. |
| `style` | Theme + QSS variable editor for live restyling of the dark/light themes. |
| `shortcut` | Keyboard-shortcut editor for slots and commands; a focused `global_shortcuts` view also exists. Hosts can wire in Maya-aware collision checking via `mtk.maya_collision_checker`. |

`MayaUiHandler.instance()` is reentrant: if `tentacle` (or any other tool)
already created the handler, the call returns the existing singleton; otherwise
it bootstraps one with a fresh `Switchboard`. The editor window is cached per-handler — clicking the shelf button twice focuses the existing window rather than spawning a duplicate.

---

## Session safety

`MayaConnection.connect()` defaults to `launch=True, force_new_instance=True` — every call launches a fresh Maya on an unused port. The user's open session is never touched. The test runner (`test/run_tests.py`) defaults the same way; only `--reuse` overrides, and you should not pass it. See [CLAUDE.md](../CLAUDE.md) for the full rule.

---

## Guides

- **[Scene data nodes](data_nodes.md)** — the shared `data_internal` / `data_export` two-node model that every tool uses to stash scene-wide metadata and (optionally) embed it in an FBX.
- **[Shot data in the FBX → Unity](shot_export_unity.md)** — exporting Shots as named Unity AnimationClips plus embedded shot metadata, and side-by-side coexistence with Audio events.

Format specs (co-located with the code that consumes them):

- **[Shot-manifest behaviors](../mayatk/anim_utils/shots/shot_manifest/behaviors/BEHAVIOR_FORMAT.md)** — behavior file format for the shot manifest.
- **[Shot-manifest mapping](../mayatk/anim_utils/shots/shot_manifest/mapping/MAPPING_FORMAT.md)** — mapping file format for the shot manifest.
- **[Scene-exporter template rules](../mayatk/env_utils/scene_exporter/TEMPLATE_RULES.md)** — export-template rule syntax.

---

## Links

- **Full API:** [`API_REGISTRY.md`](../API_REGISTRY.md) · [`API_CHANGES.md`](../API_CHANGES.md)
- **Changelog:** [`CHANGELOG.md`](../CHANGELOG.md)
- **Contributor / AI-agent guide:** [`CLAUDE.md`](../CLAUDE.md)
- **PyPI:** https://pypi.org/project/mayatk/
- **Issues:** https://github.com/m3trik/mayatk/issues

## License

MIT — see [LICENSE](../LICENSE).
