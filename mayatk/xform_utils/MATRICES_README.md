# Matrices Module - Maya Matrix Utilities

Clean, modular matrix utilities for Maya rigging and animation.

## Quick Start

```python
from mayatk.xform_utils import matrices

# Access via matrices.Matrices.<method>()
matrices.Matrices.from_srt(translate=(10, 0, 0))
matrices.Matrices.drive_with_offset_parent_matrix(driver, control)
matrices.Matrices.build_space_switch(control, [world, chest, head])
```

## Import Pattern

The `matrices` module follows the same pattern as `components` in `core_utils`:

```python
# Similar to:
from mayatk.core_utils import components
components.Components.get_components(obj, 'vtx')

# Use matrices like:
from mayatk.xform_utils import matrices
matrices.Matrices.to_mmatrix(node)
```

This provides:
- Clear namespace separation
- IDE autocomplete support  
- Consistent with mayatk conventions
- Clean, readable code

## Core Features

### Pure Math Operations (No Nodes Created)
- `to_mmatrix(node)` - Get world matrix
- `local_matrix(node)` - Get local matrix
- `from_srt(t, r, s)` - Compose matrix from SRT
- `decompose(mx)` - Break down to SRT components
- `inverse(mx)` - Matrix inversion
- `mult(*mats)` - Right-to-left multiplication

### DAG Transform Utilities
- `set_offset_parent_matrix(node, mx)` - Apply matrix to offsetParentMatrix
- `bake_world_matrix_to_transform(node, mx)` - Set TRS from matrix
- `freeze_to_offset_parent_matrix(node)` - Zero TRS, maintain world position

### Node Graph Builders
- `build_mult_matrix_chain(attrs, name)` - Matrix multiply + decompose
- `drive_with_offset_parent_matrix(driver, control, name)` - Direct drive
- `build_space_switch(control, spaces, ...)` - Multi-space switching
- `build_aim_matrix(source, target, up, ...)` - Node-based aiming
- `build_ikfk_blend(ik, fk, ...)` - IK/FK blending

## Usage Examples

### Direct Drive
```python
driver = pm.PyNode("driver_GRP")
ctl = pm.PyNode("arm_CTL")
matrices.Matrices.drive_with_offset_parent_matrix(driver, ctl, name="arm_drive")
```

### Space Switch
```python
hand = pm.PyNode("hand_CTL")
spaces = [pm.PyNode("world"), pm.PyNode("chest"), pm.PyNode("head")]
matrices.Matrices.build_space_switch(hand, spaces, attr_name="space")
# hand.space attribute switches between spaces (0=world, 1=chest, 2=head)
```

### Freeze Transforms
```python
ctl = pm.PyNode("offset_CTL")
matrices.Matrices.freeze_to_offset_parent_matrix(ctl)
# Now ctl.t, ctl.r, ctl.s are zero but world position unchanged
```

### Pure Math
```python
# Compose
mx = matrices.Matrices.from_srt(
    translate=(10, 5, 0),
    rotate_euler_deg=(0, 45, 0),
    scale=(2, 2, 2)
)

# Decompose
t, r, s = matrices.Matrices.decompose(mx)

# Operations
mx_inv = matrices.Matrices.inverse(mx)
result = matrices.Matrices.mult(mx1, mx2)  # Right-to-left
```

## Key Concepts

### offsetParentMatrix
Modern alternative to buffer groups and parent constraints:
```python
# Old: Create offset group, parent constraint
# New: Drive via offsetParentMatrix
matrices.Matrices.drive_with_offset_parent_matrix(driver, control)
```

### Matrix Multiplication Order
Right-to-left: `A * B` means "apply B first, then A"
```python
# Child in world space
world_mx = matrices.Matrices.mult(child_local, parent_world)
```

### Space Conversion
```python
# Object from space A to space B:
# result = object_in_A * inverse(A_world) * B_world
```

## Design Principles

- **DRY**: No code duplication
- **Separation of Concerns**: Pure math, DAG ops, node builders are distinct
- **Minimal**: Only essential functionality (~600 lines)
- **Modular**: Easy to extend and compose

## Performance Tips

1. Matrix nodes evaluate faster than constraints
2. Use offsetParentMatrix to eliminate buffer groups
3. Keep hierarchies flat for faster DAG traversal
4. Matrix connections are more predictable than constraint evaluation

## See Also

- `matrices_examples.py` - Working code examples
- [Rigging Dojo Article](https://www.riggingdojo.com/2025/07/17/mastering-matrices-for-3d-animation-and-rigging/)

## Exception

`MatricesError` - Base exception for matrix operations
