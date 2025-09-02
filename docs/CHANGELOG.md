# Changelog

All notable changes to the mayatk project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Comprehensive documentation suite including API reference, getting started guide, examples, and developer guide
- Enhanced README with detailed feature descriptions and usage examples

### Changed
- Improved documentation structure and organization

## [0.9.33] - 2024-XX-XX

### Added
- Dynamic attribute resolution system for efficient package component access
- Support for nested module paths in the include dictionary
- Enhanced hierarchy manager with diff results and repair actions
- Object swapper utilities for scene management
- Comprehensive decorator system for common Maya operations

### Features
- **Core Utils**: Fundamental Maya operations and decorators
  - `@selected` decorator for automatic selection handling
  - `@undoable` decorator for Maya undo queue integration
  - Bounding box utilities with multiple return types
  - Object type validation functions

- **Edit Utils**: Advanced mesh editing and modeling tools
  - Bridge operations between edge loops
  - Bevel operations with customizable parameters
  - Mirror geometry across multiple axes
  - Duplicate operations (linear, radial, grid patterns)
  - Selection conversion and filtering utilities

- **Node Utils**: Dependency graph and node management
  - Node creation and connection utilities
  - Attribute management (get/set multiple attributes)
  - Connection analysis and manipulation

- **Transform Utils (XForm Utils)**: Transform and coordinate operations
  - Freeze and reset transformations
  - Object alignment utilities
  - Coordinate space conversions

- **Environment Utils**: Scene management and organization
  - Hierarchy comparison and management
  - Object swapping with connection preservation
  - Scene cleanup and optimization tools
  - Outliner organization utilities

- **UV Utils**: UV mapping and texture coordinate tools
  - UV unfolding and layout operations
  - Multiple projection types (planar, cylindrical, spherical)
  - Shell spacing and optimization

- **Rigging Utils**: Character rigging and constraint tools
  - Constraint creation (point, orient, parent)
  - IK/FK chain utilities
  - Rigging helper functions

- **Animation Utils**: Animation and keyframe management
  - Keyframe creation and deletion
  - Animation curve utilities
  - Time range operations

- **Material Utils**: Material and shader management
  - Material creation and assignment
  - Shader network utilities

- **Additional Modules**:
  - **Camera Utils**: Camera and viewport management
  - **Display Utils**: Display layer and visibility controls
  - **Light Utils**: Lighting and rendering utilities
  - **NURBS Utils**: NURBS surface and curve operations
  - **Selection Utils**: Advanced selection management
  - **UI Utils**: User interface components and utilities

### Technical Improvements
- Optimized module loading with caching system
- Enhanced error handling and validation
- Performance improvements in attribute resolution
- Better integration with PyMEL and Maya API

### Dependencies
- Python 3.7+
- Autodesk Maya 2020+
- PyMEL
- NumPy 2.3.1
- PyYAML 6.0.2
- QtPy
- pythontk 0.7.28
- uitk 1.0.29

## [0.9.32] - Previous Release

### Added
- Initial implementation of core utility modules
- Basic Maya API integration
- Foundation for dynamic attribute resolution

### Changed
- Improved module organization structure

## [0.9.31] - Previous Release

### Added
- Core utilities for Maya operations
- Basic selection and transform utilities

## Previous Versions

Earlier versions focused on establishing the core framework and basic Maya integration. Key milestones included:

- **0.9.x Series**: Development of core utility modules
- **0.8.x Series**: Initial Maya API integration
- **0.7.x Series**: Foundation and architecture development

---

## Version History Summary

| Version | Release Date | Key Features |
|---------|-------------|--------------|
| 0.9.33  | Current     | Dynamic attribute resolution, comprehensive utilities |
| 0.9.32  | Previous    | Core utilities, Maya API integration |
| 0.9.31  | Previous    | Basic selection and transform utilities |

## Migration Guide

### From 0.9.32 to 0.9.33

The dynamic attribute resolution system allows for more direct access to package components:

```python
# Old way (still works)
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.edit_utils.selection import Selection

# New way (recommended)
import mayatk as mtk
core_utils = mtk.CoreUtils()
selection = mtk.Selection()
```

### Breaking Changes

#### Version 0.9.33
- No breaking changes - fully backward compatible

#### Version 0.9.32
- Module reorganization may require import path updates
- Some function signatures were standardized

## Deprecation Notices

### Planned for Future Versions
- Legacy module access patterns will remain supported but direct attribute access is preferred
- Some internal APIs may be refactored for better performance

## Contributors

- **Ryan Simpson** (m3trik) - Primary maintainer and developer
- Community contributors - See GitHub contributors page

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

*For detailed information about specific features and usage, see the [API Reference](API_REFERENCE.md) and [Examples](EXAMPLES.md).*
