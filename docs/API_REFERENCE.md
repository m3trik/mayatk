# API Reference

This document provides a comprehensive reference for all modules and classes in the mayatk package.

## Table of Contents

- [Core Utils](#core-utils)
- [Edit Utils](#edit-utils)
- [Node Utils](#node-utils)
- [Transform Utils](#transform-utils)
- [Selection Utils](#selection-utils)
- [Environment Utils](#environment-utils)
- [UV Utils](#uv-utils)
- [Rigging Utils](#rigging-utils)
- [Material Utils](#material-utils)
- [Animation Utils](#animation-utils)
- [Camera Utils](#camera-utils)
- [Display Utils](#display-utils)
- [Light Utils](#light-utils)
- [NURBS Utils](#nurbs-utils)
- [UI Utils](#ui-utils)

## Core Utils

The core utilities provide fundamental Maya operations and decorators, accessible directly at the package level through mayatk's dynamic attribute resolution.

### Decorators

#### `@mtk.selected`
Automatically passes the current selection to a function if no objects are provided.

```python
import mayatk as mtk

@mtk.selected
def process_objects(objects):
    """Process the given objects or current selection if objects=None"""
    for obj in objects:
        print(f"Processing: {obj}")

# Usage
process_objects()  # Uses current selection
process_objects(["pCube1", "pSphere1"])  # Uses specified objects
```

#### `@mtk.undoable`
Places a function's operations into Maya's undo queue as a single chunk.

```python
@mtk.undoable
def batch_operation():
    """All operations will be grouped in Maya's undo queue"""
    # Multiple Maya operations here
    pm.polyCube()
    pm.polySphere()
    pm.polyCylinder()
```

### Core Functions

#### `get_bounding_box(objects, return_type='min|max')`
Get bounding box information for Maya objects.

**Parameters:**
- `objects` (str|list): Object(s) to get bounding box for
- `return_type` (str): What to return - 'min|max', 'centroid', 'size', 'centroid|size'

**Returns:**
- tuple: Bounding box information based on return_type

```python
# Get min/max coordinates
bbox = mtk.get_bounding_box("pCube1")
# Returns: ((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0))

# Get centroid and size
center_size = mtk.get_bounding_box("pCube1", "centroid|size")
# Returns: ((0.0, 0.0, 0.0), (2.0, 2.0, 2.0))
```

#### `is_group(node)`
Check if a node is a group (transform with no shape children).

**Parameters:**
- `node` (str|PyNode): Node to check

**Returns:**
- bool: True if node is a group, False otherwise

```python
result = mtk.is_group("group1")  # Returns: True or False
```

## Edit Utils

Mesh editing and modeling utilities.

### Classes

#### `Selection`
Advanced selection management and conversion utilities.

##### Methods

###### `convert_selection(component_type, objects=None)`
Convert current selection to different component types.

**Parameters:**
- `component_type` (str): Target component type ('vertices', 'edges', 'faces', 'objects')
- `objects` (list, optional): Objects to convert, uses selection if None

**Returns:**
- list: Converted selection

```python
selection = mtk.Selection()

# Convert to vertices
vertices = selection.convert_selection("vertices")

# Convert to faces
faces = selection.convert_selection("faces")

# Convert specific objects to edges
edges = selection.convert_selection("edges", ["pCube1", "pSphere1"])
```

###### `filter_selection(filter_type, objects=None)`
Filter selection based on criteria.

**Parameters:**
- `filter_type` (str): Filter criteria
- `objects` (list, optional): Objects to filter

**Returns:**
- list: Filtered selection

### Modeling Functions

#### `bridge_edges(edge_list_1, edge_list_2, **kwargs)`
Bridge between two edge lists.

**Parameters:**
- `edge_list_1` (list): First edge selection
- `edge_list_2` (list): Second edge selection
- `**kwargs`: Additional bridge options

```python
mtk.bridge_edges(
    ["pCube1.e[0:3]"], 
    ["pCube1.e[8:11]"],
    divisions=2
)
```

#### `bevel_faces(faces, offset=0.1, segments=1, **kwargs)`
Bevel the specified faces.

**Parameters:**
- `faces` (list): Face components to bevel
- `offset` (float): Bevel offset distance
- `segments` (int): Number of bevel segments

```python
mtk.bevel_faces(
    ["pCube1.f[0:5]"],
    offset=0.2,
    segments=3
)
```

#### `mirror_geometry(objects, axis='x', merge_threshold=0.001)`
Mirror geometry across specified axis.

**Parameters:**
- `objects` (str|list): Objects to mirror
- `axis` (str): Mirror axis ('x', 'y', 'z')
- `merge_threshold` (float): Distance threshold for merging vertices

```python
mtk.mirror_geometry("pCube1", axis="x", merge_threshold=0.001)
```

## Node Utils

Node and dependency graph operations.

### Node Management

#### `create_node(node_type, name=None, **kwargs)`
Create a new Maya node.

**Parameters:**
- `node_type` (str): Type of node to create
- `name` (str, optional): Name for the new node
- `**kwargs`: Additional node creation parameters

**Returns:**
- PyNode: Created node

```python
# Create a transform node
transform = mtk.create_node("transform", name="myGroup")

# Create a material
material = mtk.create_node("lambert", name="myMaterial")
```

#### `get_connections(node, incoming=True, outgoing=True, **kwargs)`
Get node connections.

**Parameters:**
- `node` (str|PyNode): Node to query
- `incoming` (bool): Include incoming connections
- `outgoing` (bool): Include outgoing connections

**Returns:**
- dict: Connection information

```python
connections = mtk.get_connections("pCube1", incoming=True, outgoing=True)
```

### Attribute Operations

#### `set_attributes(node, attributes)`
Set multiple attributes on a node.

**Parameters:**
- `node` (str|PyNode): Target node
- `attributes` (dict): Attribute name/value pairs

```python
mtk.set_attributes("pCube1", {
    "translateX": 5.0,
    "rotateY": 45.0,
    "scaleZ": 2.0
})
```

#### `get_attributes(node, attributes)`
Get multiple attribute values from a node.

**Parameters:**
- `node` (str|PyNode): Source node
- `attributes` (list): List of attribute names

**Returns:**
- dict: Attribute name/value pairs

```python
attrs = mtk.get_attributes("pCube1", ["translateX", "rotateY", "scaleZ"])
# Returns: {"translateX": 0.0, "rotateY": 0.0, "scaleZ": 1.0}
```

## Transform Utils (XForm Utils)

Transform and coordinate operations.

### Transform Operations

#### `freeze_transforms(objects, translate=True, rotate=True, scale=True)`
Freeze transformations on objects.

**Parameters:**
- `objects` (str|list): Objects to freeze
- `translate` (bool): Freeze translation
- `rotate` (bool): Freeze rotation  
- `scale` (bool): Freeze scale

```python
mtk.freeze_transforms("pCube1")
mtk.freeze_transforms(["pCube1", "pSphere1"], scale=False)
```

#### `reset_transforms(objects, translate=True, rotate=True, scale=True)`
Reset transformations to default values.

**Parameters:**
- `objects` (str|list): Objects to reset
- `translate` (bool): Reset translation
- `rotate` (bool): Reset rotation
- `scale` (bool): Reset scale

```python
mtk.reset_transforms("pCube1")
```

#### `align_objects(objects, target=None, axis='x', mode='min')`
Align objects to each other or a target.

**Parameters:**
- `objects` (list): Objects to align
- `target` (str|PyNode, optional): Target object for alignment
- `axis` (str): Alignment axis
- `mode` (str): Alignment mode ('min', 'max', 'center')

```python
# Align objects to each other
mtk.align_objects(["pCube1", "pSphere1"], axis="x", mode="center")

# Align to specific target
mtk.align_objects(["pCube1", "pSphere1"], target="locator1", axis="y")
```

## UV Utils

UV mapping and texture coordinate utilities.

### UV Operations

#### `unfold_uvs(objects, **kwargs)`
Unfold UVs for specified objects.

**Parameters:**
- `objects` (str|list): Objects to unfold UVs for
- `**kwargs`: Additional unfold options

```python
mtk.unfold_uvs("pCube1")
mtk.unfold_uvs(["pCube1", "pSphere1"])
```

#### `layout_uvs(objects, shell_spacing=0.02, tile_spacing=0.05)`
Layout UV shells for objects.

**Parameters:**
- `objects` (str|list): Objects to layout UVs for
- `shell_spacing` (float): Spacing between UV shells
- `tile_spacing` (float): Spacing between UV tiles

```python
mtk.layout_uvs(["pCube1", "pSphere1"], shell_spacing=0.02)
```

### UV Projection

#### `planar_projection(objects, projection_type='z', **kwargs)`
Apply planar UV projection.

**Parameters:**
- `objects` (str|list): Objects to project
- `projection_type` (str): Projection direction ('x', 'y', 'z')

```python
mtk.planar_projection("pCube1", projection_type="z")
```

#### `cylindrical_projection(objects, **kwargs)`
Apply cylindrical UV projection.

**Parameters:**
- `objects` (str|list): Objects to project
- `**kwargs`: Additional projection options

```python
mtk.cylindrical_projection("pCylinder1")
```

## Environment Utils

Scene management and hierarchy tools.

### Classes

#### `HierarchyManager`
Advanced hierarchy comparison and management.

##### Methods

###### `compare_hierarchies(hierarchy_a, hierarchy_b)`
Compare two scene hierarchies and identify differences.

**Parameters:**
- `hierarchy_a` (dict): First hierarchy structure
- `hierarchy_b` (dict): Second hierarchy structure

**Returns:**
- DiffResult: Comparison results

```python
hierarchy_manager = mtk.HierarchyManager()
diff_result = hierarchy_manager.compare_hierarchies(scene_a, scene_b)
```

#### `ObjectSwapper`
Utilities for swapping objects in scenes.

##### Methods

###### `swap_objects(old_object, new_object, **kwargs)`
Swap one object for another while preserving connections.

**Parameters:**
- `old_object` (str): Object to replace
- `new_object` (str): Replacement object

```python
swapper = mtk.ObjectSwapper()
swapper.swap_objects("old_chair", "new_chair")
```

### Scene Organization

#### `organize_outliner()`
Organize the Maya outliner by grouping similar objects.

```python
mtk.organize_outliner()
```

#### `clean_scene(remove_unused=True, optimize=True)`
Clean up the Maya scene.

**Parameters:**
- `remove_unused` (bool): Remove unused nodes
- `optimize` (bool): Optimize scene graph

```python
mtk.clean_scene(remove_unused=True, optimize=True)
```

## Animation Utils

Animation and keyframe utilities.

### Keyframe Operations

#### `set_keyframe(objects, attributes=None, time=None)`
Set keyframes on objects.

**Parameters:**
- `objects` (str|list): Objects to keyframe
- `attributes` (list, optional): Specific attributes to key
- `time` (float, optional): Time to set keyframe

```python
mtk.set_keyframe("pCube1")
mtk.set_keyframe("pCube1", attributes=["tx", "ty", "tz"])
```

#### `delete_keyframes(objects, time_range=None)`
Delete keyframes from objects.

**Parameters:**
- `objects` (str|list): Objects to remove keyframes from
- `time_range` (tuple, optional): Time range to clear

```python
mtk.delete_keyframes("pCube1")
mtk.delete_keyframes("pCube1", time_range=(1, 24))
```

## Rigging Utils

Character rigging and constraint utilities.

### Constraints

#### `create_point_constraint(target, constrained, **kwargs)`
Create a point constraint.

**Parameters:**
- `target` (str|list): Target object(s)
- `constrained` (str): Object to constrain
- `**kwargs`: Additional constraint options

```python
mtk.create_point_constraint("locator1", "pCube1")
mtk.create_point_constraint(["joint1", "joint2"], "pCube1", weight=0.5)
```

#### `create_orient_constraint(target, constrained, **kwargs)`
Create an orient constraint.

**Parameters:**
- `target` (str|list): Target object(s)
- `constrained` (str): Object to constrain

```python
mtk.create_orient_constraint("joint1", "pCube1")
```

### Rigging Tools

#### `create_ik_chain(start_joint, end_joint, solver='ikRPsolver')`
Create an IK chain between joints.

**Parameters:**
- `start_joint` (str): Starting joint
- `end_joint` (str): End joint
- `solver` (str): IK solver type

**Returns:**
- tuple: (ik_handle, effector)

```python
ik_handle, effector = mtk.create_ik_chain("joint1", "joint3")
```

## Material Utils

Material and shader utilities.

### Material Operations

#### `create_material(material_type='lambert', name=None)`
Create a new material.

**Parameters:**
- `material_type` (str): Type of material to create
- `name` (str, optional): Material name

**Returns:**
- PyNode: Created material

```python
material = mtk.create_material("blinn", name="myBlinn")
```

#### `assign_material(material, objects)`
Assign material to objects.

**Parameters:**
- `material` (str|PyNode): Material to assign
- `objects` (str|list): Objects to assign material to

```python
mtk.assign_material("lambert1", ["pCube1", "pSphere1"])
```

---

This API reference covers the core functionality of mayatk. For more detailed information about specific modules, refer to their individual documentation files or use Python's help() function on any class or method.
