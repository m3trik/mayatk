# mayatk Examples

This document provides practical examples of using mayatk in real-world Maya scenarios.

## Table of Contents

- [Basic Operations](#basic-operations)
- [Modeling Workflows](#modeling-workflows)
- [Rigging Examples](#rigging-examples)
- [Animation Tools](#animation-tools)
- [Scene Management](#scene-management)
- [UV Mapping](#uv-mapping)
- [Advanced Techniques](#advanced-techniques)
- [Custom Tools](#custom-tools)

## Basic Operations

### Object Information and Validation

```python
import mayatk as mtk
import pymel.core as pm

# Create test objects
cube = pm.polyCube(name="testCube")[0]
group = pm.group(empty=True, name="testGroup")
sphere = pm.polySphere(name="testSphere")[0]

# Check object types
print(f"Is cube a group? {mtk.is_group(cube)}")        # False
print(f"Is group a group? {mtk.is_group(group)}")      # True

# Get bounding box information
bbox_min_max = mtk.get_bounding_box(cube)
print(f"Cube bbox (min|max): {bbox_min_max}")

bbox_center_size = mtk.get_bounding_box(cube, "centroid|size")
print(f"Cube center and size: {bbox_center_size}")

# Get bounding box for multiple objects
multi_bbox = mtk.get_bounding_box([cube, sphere], "centroid")
print(f"Combined center: {multi_bbox}")
```

### Working with Selection

```python
# Create and select some geometry
cubes = []
for i in range(3):
    cube = pm.polyCube(name=f"cube_{i}")[0]
    cube.translateX.set(i * 3)
    cubes.append(cube)

pm.select(cubes)

# Use selection decorator
@mtk.selected
def print_object_info(objects):
    """Print information about selected objects"""
    for obj in objects:
        bbox = mtk.get_bounding_box(obj, "size")
        print(f"{obj.name()}: size = {bbox}")

# Call with current selection
print_object_info()

# Call with specific objects
print_object_info([cubes[0]])
```

## Modeling Workflows

### Bridge and Bevel Operations

```python
# Create a cube and prepare for modeling
cube = pm.polyCube(name="modelingCube", subdivisions=[4, 4, 4])[0]

# Select face components and convert to edges
pm.select(f"{cube}.f[20:23]")  # Select some faces

selection = mtk.Selection()
edges = selection.convert_selection("edges")
print(f"Converted to {len(edges)} edges")

# Bevel the faces
faces = selection.convert_selection("faces")
mtk.bevel_faces(faces, offset=0.2, segments=2)

# Bridge between edge loops (manual selection required)
# Select two edge loops manually, then:
# mtk.bridge_edges(first_loop, second_loop, divisions=3)
```

### Mirroring and Duplication

```python
# Create source geometry
source = pm.polyCylinder(name="sourceGeometry")[0]
source.translateX.set(2)
source.rotateZ.set(15)

# Mirror across X axis
mtk.mirror_geometry(source, axis="x", merge_threshold=0.001)

# Duplicate in patterns
mtk.duplicate_linear(source, instances=5, offset=(0, 2, 0))
mtk.duplicate_radial(source, instances=8, angle=360)
```

### Advanced Mesh Operations

```python
@mtk.undoable
def create_detailed_mesh():
    """Create a detailed mesh using various mayatk operations"""
    
    # Start with basic geometry
    mesh = pm.polyCube(name="detailedMesh", subdivisions=[6, 6, 6])[0]
    
    # Select random faces for detail work
    face_count = mesh.numFaces()
    import random
    random_faces = random.sample(range(face_count), k=min(10, face_count//3))
    
    face_list = [f"{mesh}.f[{i}]" for i in random_faces]
    pm.select(face_list)
    
    # Bevel selected faces
    mtk.bevel_faces(face_list, offset=0.1, segments=1)
    
    # Convert to vertices and apply some deformation
    selection = mtk.Selection()
    vertices = selection.convert_selection("vertices")
    
    # Apply random displacement
    for vertex in vertices[:len(vertices)//2]:  # Only half the vertices
        pm.move(
            random.uniform(-0.2, 0.2),
            random.uniform(-0.2, 0.2), 
            random.uniform(-0.2, 0.2),
            vertex,
            relative=True
        )
    
    return mesh

# Create detailed mesh
detailed_mesh = create_detailed_mesh()
```

## Rigging Examples

### Basic Joint Setup

```python
@mtk.undoable
def create_arm_rig():
    """Create a basic arm rig with FK and IK"""
    
    # Clear selection
    pm.select(clear=True)
    
    # Create joint chain
    joints = []
    positions = [(0, 0, 0), (0, -3, 0), (0, -6, 0), (0, -8, 0)]
    
    for i, pos in enumerate(positions):
        joint = pm.joint(name=f"arm_joint_{i+1}", position=pos)
        joints.append(joint)
    
    # Orient joints
    pm.joint(joints[0], edit=True, orientJoint="xyz", secondaryAxisOrient="yup")
    
    # Create FK controls
    mtk.create_fk_controls(joints[:-1])  # Don't control end joint
    
    # Create IK handle
    ik_handle, effector = mtk.create_ik_chain(joints[0], joints[-1])
    
    # Create IK control
    ik_ctrl = pm.circle(name="arm_ik_ctrl", normal=(0, 1, 0))[0]
    ik_ctrl.translateY.set(-8)
    
    # Constrain IK handle to control
    mtk.create_point_constraint(ik_ctrl, ik_handle)
    
    return {
        'joints': joints,
        'ik_handle': ik_handle,
        'ik_control': ik_ctrl
    }

# Create the rig
arm_rig = create_arm_rig()
print(f"Created arm rig with {len(arm_rig['joints'])} joints")
```

### Constraint Examples

```python
# Create objects for constraint examples
target = pm.spaceLocator(name="constraintTarget")
target.translateX.set(3)

constrained = pm.polyCube(name="constrainedObject")[0]

# Point constraint
point_constraint = mtk.create_point_constraint(target, constrained)

# Orient constraint with multiple targets
target2 = pm.spaceLocator(name="constraintTarget2")
target2.translateZ.set(3)

orient_constraint = mtk.create_orient_constraint([target, target2], constrained)

# Parent constraint
parent_constraint = mtk.create_parent_constraint(target, constrained)
```

## Animation Tools

### Keyframe Management

```python
# Create animated objects
animated_objects = []
for i in range(3):
    obj = pm.polyCube(name=f"animatedCube_{i}")[0]
    obj.translateX.set(i * 2)
    animated_objects.append(obj)

# Set up animation
@mtk.undoable
def create_bounce_animation():
    """Create a bouncing animation"""
    
    for frame in [1, 12, 24]:
        pm.currentTime(frame)
        
        for i, obj in enumerate(animated_objects):
            if frame == 1:
                # Start position
                obj.translateY.set(0)
                obj.rotateY.set(0)
            elif frame == 12:
                # Peak of bounce
                obj.translateY.set(3 + i * 0.5)
                obj.rotateY.set(180)
            else:
                # End position
                obj.translateY.set(0)
                obj.rotateY.set(360)
            
            # Set keyframes
            mtk.set_keyframe(obj, attributes=["ty", "ry"])

# Create the animation
create_bounce_animation()

# Delete keyframes in a range (optional cleanup)
# mtk.delete_keyframes(animated_objects[0], time_range=(12, 24))
```

### Animation Utilities

```python
def setup_character_animation(character_rig):
    """Setup animation on a character rig"""
    
    # Get all controls (assuming they're named with '_ctrl' suffix)
    controls = [node for node in pm.ls() if '_ctrl' in node.name()]
    
    # Set initial pose
    pm.currentTime(1)
    mtk.set_keyframe(controls)
    
    # Create action pose
    pm.currentTime(24)
    
    # Modify some controls
    for ctrl in controls:
        if 'arm' in ctrl.name():
            ctrl.rotateX.set(ctrl.rotateX.get() + 45)
        elif 'leg' in ctrl.name():
            ctrl.rotateZ.set(ctrl.rotateZ.get() + 30)
    
    # Set keyframes for the pose
    mtk.set_keyframe(controls)
    
    return controls

# Usage with the arm rig from previous example
if 'arm_rig' in locals():
    animated_controls = setup_character_animation(arm_rig)
```

## Scene Management

### Hierarchy Organization

```python
# Create a messy scene
objects = []
for i in range(10):
    obj_type = ["polyCube", "polySphere", "polyCylinder"][i % 3]
    obj = getattr(pm, obj_type)(name=f"object_{i}")[0]
    obj.translate.set([
        (i % 3) * 2,
        (i // 3) * 2,
        (i % 2) * 2
    ])
    objects.append(obj)

# Organize the scene
@mtk.undoable
def organize_scene():
    """Organize scene objects into logical groups"""
    
    # Group by object type
    cubes = [obj for obj in objects if "Cube" in obj.name()]
    spheres = [obj for obj in objects if "Sphere" in obj.name()]
    cylinders = [obj for obj in objects if "Cylinder" in obj.name()]
    
    groups = []
    if cubes:
        cube_group = pm.group(cubes, name="Cubes_GRP")
        groups.append(cube_group)
    
    if spheres:
        sphere_group = pm.group(spheres, name="Spheres_GRP")
        groups.append(sphere_group)
    
    if cylinders:
        cylinder_group = pm.group(cylinders, name="Cylinders_GRP")
        groups.append(cylinder_group)
    
    # Create master group
    if groups:
        master_group = pm.group(groups, name="GEOMETRY_GRP")
        return master_group

# Organize the scene
master_group = organize_scene()

# Use mayatk's organization tools
mtk.organize_outliner()
mtk.clean_scene(remove_unused=True, optimize=True)
```

### Hierarchy Manager Example

```python
# Create two different scene hierarchies for comparison
def create_hierarchy_a():
    """Create first hierarchy version"""
    root = pm.group(empty=True, name="sceneA_root")
    
    geo_group = pm.group(empty=True, name="geometry", parent=root)
    rig_group = pm.group(empty=True, name="rig", parent=root)
    
    # Add some geometry
    cube = pm.polyCube(name="cube_A")[0]
    sphere = pm.polySphere(name="sphere_A")[0]
    pm.parent([cube, sphere], geo_group)
    
    return root

def create_hierarchy_b():
    """Create second hierarchy version"""
    root = pm.group(empty=True, name="sceneB_root")
    
    geo_group = pm.group(empty=True, name="geometry", parent=root)
    rig_group = pm.group(empty=True, name="rig", parent=root)
    lights_group = pm.group(empty=True, name="lights", parent=root)  # New group
    
    # Add some geometry (different objects)
    cube = pm.polyCube(name="cube_B")[0]
    cylinder = pm.polyCylinder(name="cylinder_B")[0]  # Different object
    pm.parent([cube, cylinder], geo_group)
    
    return root

# Create hierarchies
hierarchy_a = create_hierarchy_a()
hierarchy_b = create_hierarchy_b()

# Compare hierarchies using HierarchyManager
hierarchy_manager = mtk.HierarchyManager()

# This would require the actual hierarchy data structures
# hierarchy_diff = hierarchy_manager.compare_hierarchies(
#     hierarchy_a_data, 
#     hierarchy_b_data
# )
```

### Object Swapping

```python
# Object swapping example
def demonstrate_object_swapping():
    """Demonstrate object swapping functionality"""
    
    # Create original object with some properties
    original = pm.polyCube(name="original_object")[0]
    original.translateX.set(3)
    original.rotateY.set(45)
    
    # Create connections (material, constraints, etc.)
    material = pm.shadingNode("lambert", asShader=True, name="test_material")
    pm.select(original)
    pm.hyperShade(assign=material)
    
    # Create replacement object
    replacement = pm.polySphere(name="replacement_object")[0]
    replacement.translateX.set(3)  # Match position
    replacement.rotateY.set(45)    # Match rotation
    
    # Use ObjectSwapper
    swapper = mtk.ObjectSwapper()
    swapper.swap_objects(original, replacement)
    
    return replacement

# Demonstrate swapping
swapped_object = demonstrate_object_swapping()
```

## UV Mapping

### Complete UV Workflow

```python
@mtk.undoable
def complete_uv_workflow(objects):
    """Complete UV mapping workflow"""
    
    for obj in objects:
        print(f"Processing UVs for: {obj}")
        
        # Apply appropriate projection based on object type
        if "Cube" in obj.name():
            mtk.planar_projection(obj, projection_type="z")
        elif "Sphere" in obj.name():
            mtk.spherical_projection(obj)
        elif "Cylinder" in obj.name():
            mtk.cylindrical_projection(obj)
        else:
            # Default to planar
            mtk.planar_projection(obj, projection_type="y")
        
        # Unfold UVs for better layout
        mtk.unfold_uvs(obj)
        
        # Layout UV shells
        mtk.layout_uvs(obj, shell_spacing=0.02, tile_spacing=0.05)

# Create test objects
uv_objects = []
uv_objects.append(pm.polyCube(name="uvCube")[0])
uv_objects.append(pm.polySphere(name="uvSphere")[0])
uv_objects.append(pm.polyCylinder(name="uvCylinder")[0])

# Apply UV workflow
complete_uv_workflow(uv_objects)
```

### UV Optimization

```python
def optimize_uvs_for_texturing(objects, texture_resolution=1024):
    """Optimize UVs for specific texture resolution"""
    
    for obj in objects:
        # Get object's relative size in scene
        bbox_size = mtk.get_bounding_box(obj, "size")
        object_volume = bbox_size[0] * bbox_size[1] * bbox_size[2]
        
        # Calculate appropriate UV space based on object size
        # Larger objects get more UV space
        uv_scale_factor = min(1.0, object_volume / 10.0)  # Adjust divisor as needed
        
        # Apply UV layout with custom spacing
        shell_spacing = 0.01 * (1.0 / uv_scale_factor)
        mtk.layout_uvs(obj, shell_spacing=shell_spacing)
        
        print(f"Optimized UVs for {obj}: scale factor = {uv_scale_factor:.2f}")

# Optimize UVs for our test objects
optimize_uvs_for_texturing(uv_objects, texture_resolution=2048)
```

## Advanced Techniques

### Custom Decorator Creation

```python
def with_timing(func):
    """Custom decorator to time function execution"""
    import time
    from functools import wraps
    
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            end_time = time.time()
            print(f"{func.__name__} took {end_time - start_time:.3f} seconds")
    
    return wrapper

# Combine with mayatk decorators
@with_timing
@mtk.undoable
@mtk.selected
def optimized_batch_process(objects):
    """Process objects with timing and undo grouping"""
    for obj in objects:
        mtk.freeze_transforms(obj)
        pm.delete(obj, constructionHistory=True)
        
        # Add custom attribute
        if not obj.hasAttr("optimized"):
            pm.addAttr(obj, longName="optimized", attributeType="bool")
            obj.optimized.set(True)

# Usage: select objects and run
optimized_batch_process()
```

### Batch Processing with Progress

```python
def batch_process_with_progress(objects, operation_func):
    """Process objects with progress indication"""
    
    total = len(objects)
    processed = 0
    
    for i, obj in enumerate(objects):
        try:
            operation_func(obj)
            processed += 1
            
            # Update progress
            progress = (i + 1) / total * 100
            print(f"Progress: {progress:.1f}% ({i + 1}/{total}) - {obj.name()}")
            
        except Exception as e:
            print(f"Failed to process {obj.name()}: {e}")
    
    print(f"Completed: {processed}/{total} objects processed successfully")

# Example operation
def cleanup_object(obj):
    """Clean up a single object"""
    mtk.freeze_transforms(obj)
    pm.delete(obj, constructionHistory=True)
    
    # Reset pivot
    pm.xform(obj, centerPivots=True)

# Usage
test_objects = pm.ls(type="transform")[:10]  # First 10 transforms
batch_process_with_progress(test_objects, cleanup_object)
```

### Error Recovery and Validation

```python
def safe_modeling_operation(objects, operation_name="modeling"):
    """Safely perform modeling operations with rollback capability"""
    
    # Store initial state
    initial_selection = pm.selected()
    
    try:
        # Begin undo chunk
        pm.undoInfo(openChunk=True, chunkName=operation_name)
        
        success_count = 0
        for obj in objects:
            try:
                # Validate object
                if not pm.objExists(obj):
                    print(f"Warning: Object {obj} does not exist, skipping")
                    continue
                
                if not hasattr(obj, 'getShape') or not obj.getShape():
                    print(f"Warning: Object {obj} has no geometry, skipping")
                    continue
                
                # Perform operations
                mtk.freeze_transforms(obj)
                pm.delete(obj, constructionHistory=True)
                
                success_count += 1
                
            except Exception as e:
                print(f"Failed to process {obj}: {e}")
                # Continue with other objects
                continue
        
        print(f"Successfully processed {success_count}/{len(objects)} objects")
        
    except Exception as e:
        print(f"Critical error in {operation_name}: {e}")
        # Rollback by closing undo chunk and undoing
        pm.undoInfo(closeChunk=True)
        pm.undo()
        raise
        
    finally:
        # Always close undo chunk
        pm.undoInfo(closeChunk=True)
        
        # Restore selection
        pm.select(initial_selection, replace=True)

# Usage
safe_objects = [obj for obj in pm.ls(type="transform") if obj.getShape()]
safe_modeling_operation(safe_objects[:5], "cleanup_geometry")
```

## Custom Tools

### Scene Analysis Tool

```python
class SceneAudit:
    """Custom tool using mayatk for scene analysis"""
    
    def __init__(self):
        self.components = mtk.Components()
        self.selection = mtk.Selection()
    
    def analyze_scene(self):
        """Analyze the current Maya scene"""
        report = {
            'objects': {},
            'materials': {},
            'lights': {},
            'cameras': {},
            'geometry_stats': {}
        }
        
        # Analyze geometry
        meshes = pm.ls(type="mesh", transforms=True)
        report['objects']['meshes'] = len(meshes)
        
        # Get geometry statistics
        total_vertices = 0
        total_faces = 0
        
        for mesh in meshes:
            try:
                shape = mesh.getShape()
                verts = shape.numVertices()
                faces = shape.numFaces()
                
                total_vertices += verts
                total_faces += faces
                
                # Check for issues
                bbox_size = mtk.get_bounding_box(mesh, "size")
                if any(size > 1000 for size in bbox_size):
                    print(f"Warning: Large object detected: {mesh} (size: {bbox_size})")
                
            except Exception as e:
                print(f"Error analyzing {mesh}: {e}")
        
        report['geometry_stats']['total_vertices'] = total_vertices
        report['geometry_stats']['total_faces'] = total_faces
        
        # Analyze materials
        materials = pm.ls(materials=True)
        report['materials']['count'] = len(materials)
        
        # Analyze lights
        lights = pm.ls(type="light", transforms=True)
        report['lights']['count'] = len(lights)
        
        # Analyze cameras
        cameras = pm.ls(type="camera", transforms=True)
        report['cameras']['count'] = len(cameras)
        
        return report
    
    def print_report(self, report):
        """Print a formatted scene report"""
        print("\n" + "="*50)
        print("SCENE ANALYSIS REPORT")
        print("="*50)
        
        print(f"Mesh Objects: {report['objects']['meshes']}")
        print(f"Total Vertices: {report['geometry_stats']['total_vertices']:,}")
        print(f"Total Faces: {report['geometry_stats']['total_faces']:,}")
        print(f"Materials: {report['materials']['count']}")
        print(f"Lights: {report['lights']['count']}")
        print(f"Cameras: {report['cameras']['count']}")
        
        # Performance suggestions
        if report['geometry_stats']['total_faces'] > 100000:
            print("\nPerformance Warning: High polygon count detected")
        
        if report['materials']['count'] > 50:
            print("Performance Warning: High material count detected")

# Usage
analyzer = SceneAudit()
scene_report = analyzer.analyze_scene()
analyzer.print_report(scene_report)
```

### Auto-Rigger Tool

```python
class AutoRigger:
    """Simplified auto-rigging tool using mayatk"""
    
    def __init__(self):
        self.joints = []
        self.controls = []
        self.constraints = []
    
    @mtk.undoable
    def create_simple_biped_rig(self, character_mesh):
        """Create a simple biped rig"""
        
        # Get character bounds for joint placement
        bbox = mtk.get_bounding_box(character_mesh, "centroid|size")
        center, size = bbox
        
        # Create spine joints
        spine_positions = [
            (center[0], center[1] - size[1]*0.3, center[2]),  # Hip
            (center[0], center[1], center[2]),                # Waist
            (center[0], center[1] + size[1]*0.3, center[2]),  # Chest
            (center[0], center[1] + size[1]*0.45, center[2])  # Neck
        ]
        
        spine_joints = self.create_joint_chain("spine", spine_positions)
        
        # Create arm joints
        arm_l_positions = [
            (center[0] + size[0]*0.3, center[1] + size[1]*0.3, center[2]),   # Shoulder
            (center[0] + size[0]*0.6, center[1] + size[1]*0.1, center[2]),   # Elbow
            (center[0] + size[0]*0.9, center[1] + size[1]*0.1, center[2])    # Wrist
        ]
        
        arm_l_joints = self.create_joint_chain("arm_L", arm_l_positions)
        
        # Mirror for right arm
        arm_r_joints = self.mirror_joint_chain(arm_l_joints, "arm_R")
        
        # Create leg joints
        leg_l_positions = [
            (center[0] + size[0]*0.1, center[1] - size[1]*0.3, center[2]),   # Hip
            (center[0] + size[0]*0.1, center[1] - size[1]*0.6, center[2]),   # Knee
            (center[0] + size[0]*0.1, center[1] - size[1]*0.9, center[2])    # Ankle
        ]
        
        leg_l_joints = self.create_joint_chain("leg_L", leg_l_positions)
        leg_r_joints = self.mirror_joint_chain(leg_l_joints, "leg_R")
        
        # Create controls and constraints
        self.create_controls_for_joints(spine_joints + arm_l_joints + arm_r_joints + leg_l_joints + leg_r_joints)
        
        # Bind skin
        if character_mesh:
            all_joints = spine_joints + arm_l_joints + arm_r_joints + leg_l_joints + leg_r_joints
            pm.skinCluster(all_joints, character_mesh, name=f"{character_mesh}_skinCluster")
        
        return {
            'spine': spine_joints,
            'arm_L': arm_l_joints,
            'arm_R': arm_r_joints,
            'leg_L': leg_l_joints,
            'leg_R': leg_r_joints
        }
    
    def create_joint_chain(self, name_prefix, positions):
        """Create a joint chain at specified positions"""
        pm.select(clear=True)
        joints = []
        
        for i, pos in enumerate(positions):
            joint = pm.joint(name=f"{name_prefix}_{i+1}", position=pos)
            joints.append(joint)
        
        # Orient joints
        if len(joints) > 1:
            pm.joint(joints[0], edit=True, orientJoint="xyz", secondaryAxisOrient="yup")
        
        self.joints.extend(joints)
        return joints
    
    def mirror_joint_chain(self, source_joints, new_prefix):
        """Mirror a joint chain across the YZ plane"""
        mirrored_joints = []
        
        for joint in source_joints:
            # Get original position
            pos = joint.getTranslation(space="world")
            
            # Mirror X position
            mirrored_pos = (-pos[0], pos[1], pos[2])
            
            # Create mirrored joint
            pm.select(clear=True)
            mirrored_joint = pm.joint(
                name=joint.name().replace("_L", "_R").replace("arm", new_prefix.split("_")[0]),
                position=mirrored_pos
            )
            mirrored_joints.append(mirrored_joint)
        
        return mirrored_joints
    
    def create_controls_for_joints(self, joints):
        """Create controls for joints"""
        for joint in joints:
            # Create control curve
            ctrl = pm.circle(name=f"{joint.name()}_ctrl", normal=(0, 1, 0))[0]
            
            # Match joint position
            joint_pos = joint.getTranslation(space="world")
            ctrl.setTranslation(joint_pos, space="world")
            
            # Create constraint
            constraint = mtk.create_orient_constraint(ctrl, joint)
            
            self.controls.append(ctrl)
            self.constraints.append(constraint)

# Usage
auto_rigger = AutoRigger()

# Select a character mesh first
character = pm.selected()[0] if pm.selected() else None
if character:
    rig_data = auto_rigger.create_simple_biped_rig(character)
    print(f"Created rig with {len(auto_rigger.joints)} joints and {len(auto_rigger.controls)} controls")
else:
    print("Please select a character mesh first")
```

These examples demonstrate the power and flexibility of mayatk in various Maya workflows. Each example can be adapted and extended for specific production needs.
