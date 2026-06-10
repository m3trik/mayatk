[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/Version-0.12.39-blue.svg)](https://pypi.org/project/mayatk/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Maya](https://img.shields.io/badge/Maya-2025+-orange.svg)](https://www.autodesk.com/products/maya/)
[![Tests](https://img.shields.io/badge/Tests-497%20passed-brightgreen.svg)](test/)

# mayatk

<!-- short_description_start -->
*A collection of utility classes and helper functions for Autodesk Maya — modeling, rigging, animation, materials, scene management, and UI tooling. Built on `maya.cmds` + `maya.api.OpenMaya` (no PyMEL).*
<!-- short_description_end -->

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
| `hotkey` | Keyboard-shortcut editor for slots registered with the switchboard. |

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

---

## Links

- **Full API:** [`API_REGISTRY.md`](../API_REGISTRY.md) · [`API_CHANGES.md`](../API_CHANGES.md)
- **Changelog:** [`CHANGELOG.md`](../CHANGELOG.md)
- **Contributor / AI-agent guide:** [`CLAUDE.md`](../CLAUDE.md)
- **PyPI:** https://pypi.org/project/mayatk/
- **Issues:** https://github.com/m3trik/mayatk/issues

## License

MIT — see [LICENSE](../LICENSE).
