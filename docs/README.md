# MAYATK (Maya Toolkit)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/Version-0.9.47-blue.svg)](https://pypi.org/project/mayatk/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Maya](https://img.shields.io/badge/Maya-2025+-orange.svg)](https://www.autodesk.com/products/maya/)

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
- PyMEL (included with Maya)

## Quick Start

```python
import mayatk as mtk

# Access utilities directly
selected = mtk.get_selected()
mtk.create_instance(obj)
mtk.freeze_transforms(objects)

# Or use specific modules
from mayatk import CoreUtils, EditUtils, NodeUtils

# Work with transforms
CoreUtils.snap_to_position(source, target)
EditUtils.merge_vertices(mesh, threshold=0.001)
NodeUtils.connect_attr(source, target, attr_name)
```

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

## Key Features

- **Dynamic Attribute Access** - Direct access to utilities from package level
- **Automatic Undo Support** - Built-in decorators for undo queue management
- **Selection Handling** - Automatic selection preservation and restoration
- **PyMEL Integration** - Seamless integration with Maya's Python API
- **Production Tested** - Battle-tested in professional 3D production

## Documentation

- **[Getting Started](GETTING_STARTED.md)** - Installation and basic usage
- **[API Reference](API_REFERENCE.md)** - Complete function documentation
- **[Examples](EXAMPLES.md)** - Real-world usage examples
- **[Developer Guide](DEVELOPER_GUIDE.md)** - Contributing and extending
- **[Changelog](CHANGELOG.md)** - Version history

## Example Workflows

### Mesh Operations
```python
import mayatk as mtk

# Combine meshes and clean up
result = mtk.combine_meshes(mesh_list)
mtk.delete_history(result)
mtk.center_pivot(result)
```

### Transform Management
```python
# Freeze transforms while preserving position
mtk.freeze_transforms(objects, preserve_normals=True)

# Align objects
mtk.align_objects(source, target, axes=['x', 'y'])
```

### Scene Organization
```python
# Group and organize
group = mtk.group_objects(objects, name="my_group")
mtk.parent_objects(children, parent)
mtk.create_display_layer(objects, "geometry_layer")
```

## License

MIT License - See [LICENSE](../LICENSE) file for details

## Links

- **PyPI:** https://pypi.org/project/mayatk/
- **Documentation:** [Full Documentation](index.md)
- **Issues:** https://github.com/m3trik/mayatk/issues

