# MAYATK (Maya Toolkit)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/Version-0.9.47-blue.svg)](https://pypi.org/project/mayatk/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Maya](https://img.shields.io/badge/Maya-2025+-orange.svg)](https://www.autodesk.com/products/maya/)

<!-- short_description_start -->
*mayatk is a collection of utility functions and helper classes for Autodesk Maya, providing convenience wrappers and common workflow patterns for Maya scripting.*
<!-- short_description_end -->

## Instance Separator

The new `mayatk.core_utils.instance_separator.InstanceSeparator` class wraps the
payload discovery logic that powers the auto instancer. It lets you inspect a
selection (or a supplied node list) and understand which meshes can be
instanced before you modify the scene.

```python
from mayatk.core_utils import InstanceSeparator

separator = InstanceSeparator(
	tolerance=0.99,
	require_same_material=False,
	split_shells=True,  # auto-separate multi-shell meshes
	rebuild_instances=True,
	template_position_tolerance=0.25,
	template_rotation_tolerance=7.5,
)
result = separator.separate()  # Uses the current Maya selection by default

for group in result.instantiable_groups:
	print(
		f"Prototype {group.prototype.transform} has {len(group.members)} duplicates"
	)

for assembly_group in result.instantiable_assembly_groups:
	print(
		f"Assembly {assembly_group.prototype.source_transform} has {len(assembly_group.members)} duplicates"
	)
```

Feed the `result.groups` back into `AutoInstancer` (or your own tool) to carry
out the actual instancing, or let `InstanceSeparator` rebuild duplicate
assemblies directly (enabled by default). Use `result.unique_groups` or
`result.unique_assemblies` to flag geometry that still needs manual cleanup.
Tune `template_position_tolerance` / `template_rotation_tolerance` if parts sit
farther apart or have mirrored orientations.

