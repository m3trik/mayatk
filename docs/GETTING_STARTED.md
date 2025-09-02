# Getting Started with mayatk

This guide will help you get up and running with mayatk quickly and effectively.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [First Steps](#first-steps)
- [Basic Concepts](#basic-concepts)
- [Common Workflows](#common-workflows)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)

## Prerequisites

Before you begin, ensure you have:

- **Autodesk Maya 2020 or later** installed
- **Python 3.7+** (Maya's Python environment)
- Basic familiarity with Maya's Python API or PyMEL
- Understanding of Maya's object model (transforms, shapes, components)

## Installation

### Option 1: pip install (Recommended)

The easiest way to install mayatk is through pip:

```bash
# Open Maya's Script Editor or a command prompt with Maya's Python
python -m pip install mayatk
```

### Option 2: Development Installation

For development or the latest features:

```bash
git clone https://github.com/m3trik/mayatk.git
cd mayatk
python -m pip install -e .
```

### Option 3: Manual Installation

1. Download the mayatk package
2. Extract to a folder in your Python path
3. Ensure the `mayatk` folder is accessible from Maya's Python environment

### Verify Installation

Test your installation in Maya's Script Editor:

```python
import mayatk as mtk
print(f"mayatk version: {mtk.__version__}")
print("Installation successful!")
```

## First Steps

### 1. Import and Basic Usage

```python
import mayatk as mtk

# Create some test geometry
import pymel.core as pm
cube = pm.polyCube(name="testCube")[0]
sphere = pm.polySphere(name="testSphere")[0]

# Use mayatk functions
print(f"Is cube a group? {mtk.is_group(cube)}")
bbox = mtk.get_bounding_box(cube, "centroid|size")
print(f"Cube bounding box: {bbox}")
```

### 2. Working with Selection

```python
# Select some objects in Maya first
pm.select(["testCube", "testSphere"])

# Create a selection utility instance
selection = mtk.Selection()

# Convert selection to vertices
vertices = selection.convert_selection("vertices")
print(f"Selected vertices: {len(vertices)}")

# Convert to faces
faces = selection.convert_selection("faces")
print(f"Selected faces: {len(faces)}")
```

### Using Decorators

mayatk provides powerful decorators for common operations, accessible directly at the package level:

```python
import mayatk as mtk

# Function that works with current selection automatically
@mtk.selected
def list_selected_objects(objects):
    """Print names of selected objects"""
    for obj in objects:
        print(f"Object: {obj.name()}")

# Call without arguments - uses current selection
list_selected_objects()

# Or pass specific objects
list_selected_objects([cube, sphere])
```

### Undoable Operations

Group multiple operations in Maya's undo queue:

```python
@mtk.undoable
def create_test_scene():
    """Create a test scene - all operations in one undo chunk"""
    # Create objects
    cubes = []
    for i in range(5):
        cube = pm.polyCube(name=f"cube_{i}")[0]
        cube.translateX.set(i * 2)
        cubes.append(cube)
    
    # Group them
    group = pm.group(cubes, name="cube_group")
    return group

# Execute - all operations can be undone with a single Ctrl+Z
test_group = create_test_scene()
```

## Basic Concepts

### 1. Dynamic Attribute Resolution

mayatk uses a sophisticated system that allows direct access to classes and methods:

```python
import mayatk as mtk

# These all work directly:
components = mtk.Components()        # Access Components class
selection = mtk.Selection()          # Access Selection class
result = mtk.get_bounding_box(obj)   # Access utility functions
mtk.freeze_transforms(obj)           # Access transform operations
```

### 2. Module Organization

Understanding the module structure helps you find the right tools:

```python
# Core utilities - fundamental operations
mtk.is_group()
mtk.get_bounding_box()

# Edit utilities - modeling and mesh operations
selection = mtk.Selection()
mtk.bridge_edges()
mtk.bevel_faces()

# Transform utilities - positioning and alignment
mtk.freeze_transforms()
mtk.align_objects()

# Environment utilities - scene management
hierarchy_manager = mtk.HierarchyManager()
swapper = mtk.ObjectSwapper()
```

### 3. Working with Maya Objects

mayatk functions typically accept various input formats:

```python
# String names
mtk.freeze_transforms("pCube1")

# PyMEL objects
cube = pm.PyNode("pCube1")
mtk.freeze_transforms(cube)

# Lists of objects
mtk.freeze_transforms(["pCube1", "pSphere1"])
mtk.freeze_transforms([cube, sphere])
```

## Common Workflows

### 1. Scene Cleanup and Organization

```python
# Clean up the scene
mtk.clean_scene(remove_unused=True, optimize=True)

# Organize outliner
mtk.organize_outliner()

# Freeze transformations on all geometry
geometry = pm.ls(type="mesh", transforms=True)
mtk.freeze_transforms(geometry)
```

### 2. Modeling Workflow

```python
# Select objects in Maya, then:
selection = mtk.Selection()

# Convert to face selection
faces = selection.convert_selection("faces")

# Bevel selected faces
mtk.bevel_faces(faces, offset=0.1, segments=2)

# Convert to edge selection
edges = selection.convert_selection("edges") 

# Bridge edges (select two edge loops first)
edge_loop_1 = edges[:4]  # First 4 edges
edge_loop_2 = edges[4:8]  # Next 4 edges
mtk.bridge_edges(edge_loop_1, edge_loop_2, divisions=3)
```

### 3. Rigging Setup

```python
# Create a simple FK chain
joints = []
for i in range(3):
    joint = pm.joint(name=f"joint_{i}")
    joint.translateX.set(i * 2)
    joints.append(joint)

# Create FK controls
mtk.create_fk_controls(joints)

# Add constraints
mtk.create_point_constraint("locator1", joints[0])
mtk.create_orient_constraint("ctrl_root", joints[0])

# Create IK chain
ik_handle, effector = mtk.create_ik_chain(joints[0], joints[-1])
```

### 4. UV Mapping Workflow

```python
# Select geometry
geometry = pm.selected()

# Apply planar projection
mtk.planar_projection(geometry, projection_type="z")

# Unfold UVs
mtk.unfold_uvs(geometry)

# Layout UV shells
mtk.layout_uvs(geometry, shell_spacing=0.02, tile_spacing=0.05)
```

### 5. Animation Setup

```python
# Create keyframes
objects = pm.selected()

# Set initial keyframe
pm.currentTime(1)
mtk.set_keyframe(objects, attributes=["tx", "ty", "tz", "rx", "ry", "rz"])

# Move to frame 24 and set another keyframe
pm.currentTime(24)
for obj in objects:
    obj.translateX.set(obj.translateX.get() + 5)
mtk.set_keyframe(objects, attributes=["tx", "ty", "tz"])
```

## Best Practices

### 1. Use Decorators Effectively

```python
# Combine decorators for powerful workflows
@mtk.undoable
@mtk.selected
def process_selected_geometry(objects):
    """Process selected geometry with automatic undo grouping"""
    for obj in objects:
        # Freeze transformations
        mtk.freeze_transforms(obj)
        
        # Reset pivot
        pm.xform(obj, centerPivots=True)
        
        # Delete history
        pm.delete(obj, constructionHistory=True)

# Usage: just select objects and call
process_selected_geometry()
```

### 2. Error Handling

```python
def safe_operation():
    """Example of safe mayatk usage"""
    try:
        # Check if objects exist
        if not pm.objExists("pCube1"):
            pm.polyCube(name="pCube1")
        
        # Perform operations
        mtk.freeze_transforms("pCube1")
        result = mtk.get_bounding_box("pCube1")
        
        return result
        
    except Exception as e:
        print(f"Operation failed: {e}")
        return None
```

### 3. Batch Operations

```python
@mtk.undoable
def batch_process_scene():
    """Process all geometry in the scene"""
    # Get all mesh transforms
    geometry = pm.ls(type="mesh", transforms=True)
    
    for obj in geometry:
        # Skip if already processed
        if obj.hasAttr("processed"):
            continue
            
        # Process object
        mtk.freeze_transforms(obj)
        pm.delete(obj, constructionHistory=True)
        
        # Mark as processed
        pm.addAttr(obj, longName="processed", attributeType="bool")
        obj.processed.set(True)
```

### 4. Working with Components

```python
def advanced_selection_workflow():
    """Advanced component selection workflow"""
    components = mtk.Components()
    selection = mtk.Selection()
    
    # Get current selection info
    sel_info = components.get_component_info(selection=True)
    print(f"Selection type: {sel_info['type']}")
    
    # Convert and filter
    if sel_info['type'] == 'faces':
        # Convert to edges
        edges = selection.convert_selection("edges")
        
        # Filter for boundary edges
        boundary_edges = selection.filter_selection("boundary", edges)
        
        # Select boundary edges
        pm.select(boundary_edges)
```

## Troubleshooting

### Common Issues

#### 1. Import Errors

```python
# If you get import errors, check your Python path
import sys
print("Python paths:")
for path in sys.path:
    print(f"  {path}")

# Add mayatk path if needed
sys.path.append("/path/to/mayatk")
```

#### 2. PyMEL Issues

```python
# If PyMEL functions aren't working:
try:
    import pymel.core as pm
    print("PyMEL imported successfully")
except ImportError as e:
    print(f"PyMEL import failed: {e}")
    print("Make sure PyMEL is properly installed in Maya")
```

#### 3. Selection Issues

```python
# If selection-based functions aren't working:
current_selection = pm.selected()
print(f"Current selection: {current_selection}")

if not current_selection:
    print("No objects selected - please select objects first")
```

#### 4. Attribute Resolution Issues

```python
# If dynamic attribute resolution fails:
try:
    components = mtk.Components()
    print("Attribute resolution working")
except AttributeError as e:
    print(f"Attribute resolution failed: {e}")
    print("Try importing the module directly:")
    from mayatk.core_utils.components import Components
    components = Components()
```

### Performance Tips

1. **Batch Operations**: Group multiple operations using the `@undoable` decorator
2. **Selection Caching**: Store selection results to avoid repeated queries
3. **Use Specific Imports**: For performance-critical code, import specific classes directly
4. **Error Handling**: Always handle exceptions to prevent Maya crashes

### Getting Help

- **Documentation**: Check the API reference for detailed function documentation
- **Help Function**: Use Python's help() function on any mayatk class or method
- **Maya Script Editor**: Test functions in Maya's Script Editor first
- **Community**: Check GitHub issues for common problems and solutions

```python
# Get help on any mayatk component
help(mtk.Selection)
help(mtk.get_bounding_box)
```

## Next Steps

Now that you have the basics down, explore:

1. **Advanced Workflows**: Check out specific module documentation
2. **Custom Scripts**: Build your own tools using mayatk as a foundation
3. **Integration**: Combine mayatk with other Maya tools and pipelines
4. **Contributing**: Help improve mayatk by reporting issues or contributing code

Happy Maya scripting with mayatk!
