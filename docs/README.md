# MAYATK (Maya Toolkit)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/Version-0.11.13-blue.svg)](https://pypi.org/project/mayatk/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Maya](https://img.shields.io/badge/Maya-2025+-orange.svg)](https://www.autodesk.com/products/maya/)
[![Tests](https://img.shields.io/badge/Tests-33%20passed%202%20failed-orange.svg)](test/)

<!-- short_description_start -->
*mayatk is a collection of utility functions and helper classes for Autodesk Maya, providing convenience wrappers and common workflow patterns for Maya scripting.*
<!-- short_description_end -->

## Overview

mayatk provides a comprehensive set of production-ready utilities for Maya automation, organized into specialized modules for different aspects of 3D workflow development.

## Installation

```bash
pip install mayatk
```

**Requirements:**
- Python 3.11+
- Autodesk Maya 2025+

mayatk ships [`mayapy-package-manager.bat`](../mayatk/env_utils/mayapy-package-manager.bat) — an interactive Windows menu for installing, updating, and backing up packages in Maya's bundled Python interpreter.

## Package Structure

### Core Modules

| Module | Description |
|--------|-------------|
| **core_utils** | Core Maya operations, decorators, scene management |
| **edit_utils** | Mesh editing, modeling, geometry operations |
| **node_utils** | Node operations, dependency graph, connections |
| **xform_utils** | Transform utilities, positioning, coordinates |
| **env_utils** | Environment management, scene hierarchy |

### Specialized Modules

| Module | Description |
|--------|-------------|
| **uv_utils** | UV mapping and texture coordinate tools |
| **rig_utils** | Rigging, constraints, character setup |
| **anim_utils** | Animation, keyframe management |
| **mat_utils** | Materials, shaders, texture management |
| **cam_utils** | Camera utilities and viewport management |
| **display_utils** | Display layers, visibility, viewport settings |
| **light_utils** | Lighting utilities and rendering tools |
| **nurbs_utils** | NURBS surfaces and curve operations |
| **ui_utils** | User interface components and utilities |



## Bundled UITK Editors

`MayaUiHandler` ships with a one-line entry point for launching the
[uitk](https://github.com/m3trik/uitk) editor windows from a Maya shelf
button or script:

```python
from mayatk.ui_utils.maya_ui_handler import MayaUiHandler
MayaUiHandler.instance().editors.show("browser")
```

| Editor key | Description |
|------------|-------------|
| `browser`  | Searchable launcher for every UI registered with the switchboard — supports tagging, filtering by name/tag, hide lists, launch options, and JSON-portable presets. |
| `style`    | Theme + QSS variable editor for live restyling of the dark/light themes. |
| `hotkey`   | Keyboard-shortcut editor for slots registered with the switchboard. |

`MayaUiHandler.instance()` is reentrant:

- If `tentacle` (or any other tool) already created the handler, the
  call returns the existing singleton — opening the editor on top of
  the same switchboard the rest of the application is using.
- If nothing has set up a handler yet, the call bootstraps one with a
  fresh `Switchboard`, so the shelf button is the only line of code
  needed.

The editor is cached per-handler — clicking the shelf button twice
focuses the existing window rather than spawning a duplicate; closing
it via the OS close button + reopening rebuilds transparently.

## License

MIT License - See [LICENSE](../LICENSE) file for details

## Links

- **PyPI:** https://pypi.org/project/mayatk/
- **Documentation:** [Full Documentation](index.md)
- **Issues:** https://github.com/m3trik/mayatk/issues

