# mayatk Documentation

Welcome to the comprehensive documentation for mayatk - a powerful Maya toolkit for Python automation.

## 📚 Documentation Overview

This documentation suite provides everything you need to use, understand, and contribute to mayatk:

| Document | Purpose | Audience |
|----------|---------|----------|
| [README](README.md) | Package overview and quick start | All users |
| [Getting Started](GETTING_STARTED.md) | Step-by-step setup and basic usage | New users |
| [API Reference](API_REFERENCE.md) | Complete function and class documentation | All users |
| [Examples](EXAMPLES.md) | Real-world usage examples and workflows | All users |
| [Developer Guide](DEVELOPER_GUIDE.md) | Contributing and extending mayatk | Contributors |
| [Changelog](CHANGELOG.md) | Version history and changes | All users |

## 🚀 Quick Navigation

### For New Users
1. Start with [README](README.md) for package overview
2. Follow [Getting Started](GETTING_STARTED.md) for installation and first steps
3. Explore [Examples](EXAMPLES.md) for practical workflows

### For Experienced Users
1. Jump to [API Reference](API_REFERENCE.md) for detailed function documentation
2. Check [Examples](EXAMPLES.md) for advanced techniques
3. Review [Changelog](CHANGELOG.md) for latest features

### For Contributors
1. Read [Developer Guide](DEVELOPER_GUIDE.md) for architecture and contribution guidelines
2. Check [API Reference](API_REFERENCE.md) for current implementation details
3. Review [Changelog](CHANGELOG.md) for recent changes

## 📖 What is mayatk?

mayatk is a comprehensive collection of backend utilities and tools for Autodesk Maya, designed to streamline 3D workflow development and automation. It provides:

- **Comprehensive Maya API Integration**: Seamless integration with Maya's Python (cmds + OpenMaya 2.0) APIs
- **Modular Architecture**: Organized into specialized utility modules for different Maya operations
- **Dynamic Attribute Resolution**: Advanced attribute resolver for accessing package components efficiently
- **Production-Ready Tools**: Battle-tested utilities used in professional 3D production environments

## 🎯 Core Features

### Utility Modules

| Module | Description |
|--------|-------------|
| **core_utils** | Core Maya operations, decorators, and fundamental utilities |
| **edit_utils** | Mesh editing tools, modeling utilities, and geometry operations |
| **node_utils** | Node operations, dependency graph utilities, and connection tools |
| **xform_utils** | Transform utilities, positioning, and coordinate operations |
| **selection_utils** | Selection management and filtering tools |
| **env_utils** | Environment management, scene organization, and hierarchy tools |
| **uv_utils** | UV mapping tools and texture coordinate utilities |
| **rig_utils** | Rigging tools, constraint utilities, and character setup |
| **anim_utils** | Animation utilities, keyframe management, and animation tools |
| **mat_utils** | Material and shader utilities, texture management |
| **cam_utils** | Camera utilities, viewport management, and camera tools |
| **display_utils** | Display layer management, visibility controls, and viewport settings |
| **light_utils** | Lighting utilities, light management, and rendering tools |
| **nurbs_utils** | NURBS surface and curve utilities |
| **ui_utils** | User interface utilities and custom UI components |

### Key Features

- **Dynamic Attribute Access**: Direct access to any class or method from the package
- **Powerful Decorators**: Automatic selection handling and undo queue management
- **Comprehensive Error Handling**: Robust error handling for production environments
- **Performance Optimized**: Efficient operations with caching and optimization
- **Extensible Architecture**: Easy to extend and customize for specific workflows

## 🔧 Installation

### Quick Install
```bash
python -m pip install mayatk
```

### Development Install
```bash
git clone https://github.com/m3trik/mayatk.git
cd mayatk
python -m pip install -e .
```

## 📝 Basic Usage

```python
import mayatk as mtk

# Direct access to utility functions (preferred usage)
result = mtk.is_group("pCube1")
bbox = mtk.get_bounding_box("pCube1", "centroid|size")

# Direct access to decorators (preferred usage)
@mtk.selected
@mtk.undoable
def process_objects(objects):
    for obj in objects:
        mtk.freeze_transforms(obj)

# Direct access to utility classes when needed
selection = mtk.Selection()
vertices = selection.convert_selection("vertices")
```

**Key Point**: mayatk's dynamic attribute resolution system exposes most functions and decorators directly at the package level (`mtk.function_name`), and classes are accessible as `mtk.ClassName()` when needed. This eliminates the need to import from specific modules in most cases.

## 🌟 Popular Workflows

### Modeling Workflow
```python
# Convert selection and perform operations
selection = mtk.Selection()
faces = selection.convert_selection("faces")
mtk.bevel_faces(faces, offset=0.1, segments=2)
```

### Rigging Workflow
```python
# Create constraints and rigging elements
mtk.create_point_constraint("locator1", "pCube1")
ik_handle, effector = mtk.create_ik_chain("joint1", "joint3")
```

### Scene Management
```python
# Organize and clean scene
mtk.organize_outliner()
mtk.clean_scene(remove_unused=True, optimize=True)
hierarchy_manager = mtk.HierarchyManager()
```

## 🧩 Module Deep Dive

### Core Utils
Foundation utilities and decorators:
- Object validation (`is_group`, `get_bounding_box`)
- Decorators (`@selected`, `@undoable`)
- Common operations

### Edit Utils
Comprehensive modeling tools:
- Component conversion (`Selection` class)
- Mesh operations (`bridge_edges`, `bevel_faces`)
- Duplication patterns (`duplicate_linear`, `duplicate_radial`)

### Environment Utils
Scene management and organization:
- Hierarchy management (`HierarchyManager`)
- Object swapping (`ObjectSwapper`)
- Scene cleanup utilities

## 🔍 Finding What You Need

### By Task Type

| Task | Relevant Modules | Key Functions/Classes |
|------|------------------|----------------------|
| **Modeling** | edit_utils, selection_utils | `Selection`, `bevel_faces`, `bridge_edges` |
| **Rigging** | rig_utils, xform_utils | `create_ik_chain`, `create_*_constraint` |
| **Animation** | anim_utils | `set_keyframe`, `delete_keyframes` |
| **Scene Management** | env_utils, core_utils | `HierarchyManager`, `organize_outliner` |
| **UV Mapping** | uv_utils | `unfold_uvs`, `layout_uvs`, `*_projection` |
| **Materials** | mat_utils | `create_material`, `assign_material` |

### By Experience Level

#### Beginner
- Start with [Getting Started](GETTING_STARTED.md)
- Focus on basic functions in [API Reference](API_REFERENCE.md)
- Try simple examples from [Examples](EXAMPLES.md)

#### Intermediate
- Explore advanced workflows in [Examples](EXAMPLES.md)
- Use decorator patterns for efficiency
- Combine multiple utility modules

#### Advanced
- Read [Developer Guide](DEVELOPER_GUIDE.md) for architecture details
- Create custom tools using mayatk as foundation
- Contribute improvements and new features

## 🤝 Community and Support

### Getting Help
- **GitHub Issues**: [Report bugs and request features](https://github.com/m3trik/mayatk/issues)
- **Documentation**: Comprehensive guides and examples
- **Email**: m3trik@outlook.com for direct contact

### Contributing
- Read [Developer Guide](DEVELOPER_GUIDE.md) for contribution guidelines
- Check [API Reference](API_REFERENCE.md) for current implementation
- Review [Changelog](CHANGELOG.md) for recent changes

### Code Examples
All documentation includes practical code examples:
- **Copy-paste ready**: Examples work as-is in Maya
- **Well-commented**: Clear explanations of each step
- **Progressive complexity**: From basic to advanced usage

## 📊 Performance and Best Practices

### Performance Tips
1. **Use decorators**: Leverage `@selected` and `@undoable` for efficiency
2. **Batch operations**: Group multiple operations together
3. **Cache results**: Store expensive calculations
4. **Error handling**: Always validate inputs and handle exceptions

### Best Practices
```python
# Good: Use direct package-level access (preferred)
@mtk.undoable
@mtk.selected
def safe_operation(objects):
    for obj in objects:
        try:
            if mtk.is_group(obj):
                continue
            mtk.freeze_transforms(obj)
        except Exception as e:
            print(f"Error processing {obj}: {e}")

# Good: Validate inputs
def robust_function(objects):
    if not objects:
        return []
    
    valid_objects = [obj for obj in objects if cmds.objExists(obj)]
    return process_objects(valid_objects)
```

## 🗺 Documentation Roadmap

### Current (v0.9.33)
- ✅ Complete API reference
- ✅ Getting started guide
- ✅ Comprehensive examples
- ✅ Developer guide
- ✅ Changelog

### Planned Future Additions
- 📹 Video tutorials
- 🎓 Advanced workshop materials
- 🔧 Tool development templates
- 📚 Extended cookbook
- 🌐 Interactive documentation

## 📄 License and Credits

**License**: MIT License - see [LICENSE](../LICENSE) file for details

**Author**: Ryan Simpson (m3trik@outlook.com)

**Dependencies**: 
- NumPy (numerical operations)
- PyYAML (configuration)
- pythontk (Python utilities)
- uitk (UI toolkit)

---

**Ready to get started?** Choose your path:
- 🆕 New to mayatk? → [Getting Started](GETTING_STARTED.md)
- 📖 Need reference? → [API Reference](API_REFERENCE.md)
- 💡 Want examples? → [Examples](EXAMPLES.md)
- 🔧 Want to contribute? → [Developer Guide](DEVELOPER_GUIDE.md)

*Happy Maya scripting with mayatk!* 🚀
