# Developer Guide

This guide is for developers who want to contribute to or extend the mayatk package.

## Table of Contents

- [Development Setup](#development-setup)
- [Architecture Overview](#architecture-overview)
- [Code Organization](#code-organization)
- [Contributing Guidelines](#contributing-guidelines)
- [Testing](#testing)
- [Documentation](#documentation)
- [Release Process](#release-process)

## Development Setup

### Prerequisites

- Python 3.7+
- Autodesk Maya 2020+
- Git
- Code editor (VS Code recommended)

### Clone and Setup

```bash
# Clone the repository
git clone https://github.com/m3trik/mayatk.git
cd mayatk

# Create virtual environment (optional but recommended)
python -m venv mayatk_dev
source mayatk_dev/bin/activate  # On Windows: mayatk_dev\Scripts\activate

# Install in development mode
python -m pip install -e ".[dev]"

# Install development dependencies
python -m pip install -r requirements-dev.txt
```

### Development Dependencies

Create a `requirements-dev.txt` file with development tools:

```txt
pytest>=6.0
pytest-cov>=2.0
black>=21.0
flake8>=3.8
mypy>=0.800
sphinx>=4.0
sphinx-rtd-theme>=1.0
pre-commit>=2.0
```

## Architecture Overview

### Dynamic Attribute Resolution System

The heart of mayatk is its dynamic attribute resolution system in `__init__.py`:

```python
# Key components:
CLASS_TO_MODULE = {}      # Maps class names to module names
METHOD_TO_MODULE = {}     # Maps method names to (module, class) tuples
CLASS_METHOD_TO_MODULE = {} # Maps class methods to (module, class) tuples
```

#### How It Works

1. **Discovery Phase**: `build_dictionaries()` scans all modules and builds mapping dictionaries
2. **Resolution Phase**: `__getattr__()` intercepts attribute access and dynamically imports/returns the correct object
3. **Caching**: Imported modules are cached in `IMPORTED_MODULES` for performance

#### Adding New Modules

To add a new module to the dynamic resolution system:

```python
# In mayatk/__init__.py, update the include dictionary:
include = {
    # ... existing modules ...
    "new_module": ["*"],  # Expose all classes
    # OR
    "new_module": ["SpecificClass1", "SpecificClass2"],  # Expose specific classes
    # OR for nested modules
    "module.submodule": ["ClassName"],
}
```

### Module Structure

Each utility module follows this pattern:

```
module_name/
├── __init__.py           # Module initialization
├── _module_name.py       # Main utility class
├── specific_tool.py      # Specific tools
├── specific_tool.ui      # UI files (if applicable)
└── submodule/           # Submodules (if needed)
    ├── __init__.py
    └── submodule_file.py
```

## Code Organization

### Base Classes and Mixins

All utility classes inherit from `pythontk.HelpMixin`:

```python
import pythontk as ptk

class MyUtils(ptk.HelpMixin):
    """Base class for Maya utilities"""
    
    def __init__(self):
        super().__init__()
```

### Decorators

mayatk provides several important decorators:

#### `@CoreUtils.selected`
Automatically passes current selection if None provided:

```python
@CoreUtils.selected
def my_function(objects):
    """Function automatically receives selection if objects=None"""
    for obj in objects:
        # Process object
        pass
```

#### `@CoreUtils.undoable`
Groups operations in Maya's undo queue:

```python
@CoreUtils.undoable
def batch_operation():
    """All operations grouped in single undo chunk"""
    # Multiple Maya operations
    pass
```

### Error Handling

Use consistent error handling patterns:

```python
def robust_function(objects):
    """Example of robust error handling"""
    
    if not objects:
        return []
    
    results = []
    for obj in objects:
        try:
            # Validate object exists
            if not pm.objExists(obj):
                print(f"Warning: Object {obj} does not exist")
                continue
            
            # Perform operation
            result = some_operation(obj)
            results.append(result)
            
        except Exception as e:
            print(f"Error processing {obj}: {e}")
            continue
    
    return results
```

## Contributing Guidelines

### Code Style

Follow PEP 8 with these specific guidelines:

#### Naming Conventions
- **Classes**: PascalCase (`MyUtilClass`)
- **Functions/Methods**: snake_case (`my_function`)
- **Constants**: UPPER_SNAKE_CASE (`MAX_ITERATIONS`)
- **Private methods**: Leading underscore (`_internal_method`)

#### Documentation
Use Google-style docstrings:

```python
def my_function(param1: str, param2: int = 10) -> bool:
    """Brief description of function.
    
    Longer description if needed. Explain what the function does,
    any important behavior, and when to use it.
    
    Args:
        param1: Description of first parameter
        param2: Description of second parameter with default value
        
    Returns:
        Description of return value
        
    Raises:
        ValueError: When param1 is invalid
        RuntimeError: When Maya operation fails
        
    Example:
        >>> result = my_function("test", 20)
        >>> print(result)
        True
    """
    # Implementation
    return True
```

### Git Workflow

1. **Fork** the repository
2. **Create feature branch**: `git checkout -b feature/my-new-feature`
3. **Make changes** with clear, focused commits
4. **Write tests** for new functionality
5. **Update documentation** as needed
6. **Submit pull request** with clear description

#### Commit Messages

Use conventional commit format:

```
type(scope): brief description

Longer description if needed

- List specific changes
- Each change on new line

Closes #issue_number
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

### Code Review Process

All contributions go through code review:

1. **Automated checks**: Style, tests, documentation
2. **Manual review**: Code quality, architecture fit, functionality
3. **Testing**: Verify functionality in Maya environment
4. **Documentation**: Ensure adequate documentation

## Testing

### Test Structure

```
test/
├── conftest.py           # Pytest configuration
├── test_core_utils.py    # Core utilities tests
├── test_edit_utils.py    # Edit utilities tests
├── test_integration.py   # Integration tests
└── fixtures/            # Test data and fixtures
```

### Writing Tests

Use pytest with Maya-specific fixtures:

```python
import pytest
import pymel.core as pm
import mayatk as mtk

class TestCoreUtils:
    """Test core utility functions"""
    
    def setup_method(self):
        """Setup before each test"""
        # Clear scene
        pm.newFile(force=True)
        
        # Create test objects
        self.test_cube = pm.polyCube(name="test_cube")[0]
        self.test_sphere = pm.polySphere(name="test_sphere")[0]
    
    def test_is_group(self):
        """Test is_group function"""
        # Test with non-group object
        assert not mtk.is_group(self.test_cube)
        
        # Test with group
        group = pm.group(empty=True, name="test_group")
        assert mtk.is_group(group)
    
    def test_get_bounding_box(self):
        """Test bounding box calculation"""
        bbox = mtk.get_bounding_box(self.test_cube)
        assert isinstance(bbox, tuple)
        assert len(bbox) == 2  # min and max
        
        # Test with specific return type
        center_size = mtk.get_bounding_box(self.test_cube, "centroid|size")
        assert len(center_size) == 2
    
    @pytest.mark.parametrize("obj_type,expected", [
        ("transform", False),
        ("mesh", False),
        ("group", True)
    ])
    def test_parametrized_is_group(self, obj_type, expected):
        """Parametrized test for is_group"""
        if obj_type == "group":
            obj = pm.group(empty=True)
        else:
            obj = pm.createNode(obj_type)
        
        assert mtk.is_group(obj) == expected
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=mayatk

# Run specific test file
pytest test/test_core_utils.py

# Run specific test
pytest test/test_core_utils.py::TestCoreUtils::test_is_group

# Run with verbose output
pytest -v
```

### Maya Testing Environment

For testing in Maya, use mayapy:

```bash
# Run tests with Maya's Python
mayapy -m pytest test/

# Or create a test runner script
mayapy test_runner.py
```

## Documentation

### Documentation Structure

```
docs/
├── README.md            # Main documentation
├── API_REFERENCE.md     # Complete API reference
├── GETTING_STARTED.md   # Getting started guide
├── EXAMPLES.md          # Usage examples
├── DEVELOPER_GUIDE.md   # This file
└── generated/          # Auto-generated docs
    ├── index.html
    └── modules/
```

### Building Documentation

Using Sphinx for automatic documentation generation:

```bash
# Install documentation dependencies
pip install sphinx sphinx-rtd-theme

# Generate API documentation
sphinx-apidoc -o docs/generated mayatk

# Build HTML documentation
cd docs
make html
```

### Documentation Standards

- **Every public function/class** must have docstrings
- **Examples** for complex functions
- **Type hints** where appropriate
- **Update README** for major changes

## Release Process

### Version Management

mayatk uses semantic versioning (MAJOR.MINOR.PATCH):

- **MAJOR**: Breaking changes
- **MINOR**: New features, backward compatible
- **PATCH**: Bug fixes, backward compatible

Update version in:
- `mayatk/__init__.py` (`__version__`)
- `setup.py`
- Documentation badges

### Release Checklist

1. **Update version numbers**
2. **Update CHANGELOG.md**
3. **Run full test suite**
4. **Update documentation**
5. **Create git tag**: `git tag v0.9.34`
6. **Push tag**: `git push origin v0.9.34`
7. **Build and upload to PyPI**

### Building for PyPI

```bash
# Clean previous builds
rm -rf build/ dist/ *.egg-info/

# Build source and wheel distributions
python setup.py sdist bdist_wheel

# Upload to PyPI (test first)
twine upload --repository-url https://test.pypi.org/legacy/ dist/*

# Upload to PyPI
twine upload dist/*
```

## Advanced Development Topics

### Performance Optimization

#### Module Loading
- Use lazy imports where possible
- Cache expensive operations
- Minimize Maya API calls in loops

```python
# Good: Cache Maya operations
shapes = [obj.getShape() for obj in objects]
for shape in shapes:
    if shape:
        process_shape(shape)

# Bad: Repeated Maya calls
for obj in objects:
    shape = obj.getShape()  # Maya call each iteration
    if shape:
        process_shape(shape)
```

#### Memory Management
- Clean up temporary objects
- Use context managers for resources
- Avoid circular references

### Debugging

#### Maya Script Editor
Use Maya's Script Editor for interactive debugging:

```python
import mayatk as mtk
import pymel.core as pm

# Enable debug output
import logging
logging.basicConfig(level=logging.DEBUG)

# Test functions interactively
result = mtk.some_function()
print(f"Result: {result}")
```

#### VS Code Debugging
Configure VS Code for Maya debugging:

```json
// .vscode/launch.json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Maya Debug",
            "type": "python",
            "request": "attach",
            "port": 7720,
            "host": "localhost"
        }
    ]
}
```

### Integration with Other Tools

#### pythontk Integration
mayatk leverages pythontk for common utilities:

```python
import pythontk as ptk

# Use pythontk utilities
file_contents = ptk.get_file_contents("path/to/file")
requirements = ptk.update_requirements()
```

#### uitk Integration
For UI development, integrate with uitk:

```python
import uitk

class MyTool(uitk.MainWindow):
    """Custom tool using uitk and mayatk"""
    
    def __init__(self):
        super().__init__()
        self.setup_ui()
    
    def setup_ui(self):
        # Create UI using uitk
        # Use mayatk for Maya operations
        pass
```

## Community and Support

### Getting Help

- **GitHub Issues**: Report bugs and request features
- **Discussions**: General questions and community support
- **Email**: Direct contact for sensitive issues

### Contributing Ideas

- **New utility modules** for specific Maya workflows
- **Performance improvements** to existing code
- **Better error handling** and user feedback
- **Additional examples** and documentation
- **Integration tools** with other Maya packages

### Code of Conduct

- Be respectful and constructive
- Help others learn and grow
- Focus on what's best for the community
- Acknowledge contributions from others

---

Thank you for contributing to mayatk! Your contributions help make Maya scripting more accessible and powerful for the entire community.
