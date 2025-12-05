# Module Resolver Integration Testing

## Overview

A comprehensive, package-agnostic testing framework for validating `module_resolver` lazy loading implementations across Python packages.

## Architecture

### Framework Location
`test.test_module_resolver.ModuleResolverValidator`

### Test Implementation
Each package using module resolver can create a simple test file that imports and uses the validator:

```python
from test.test_module_resolver import ModuleResolverValidator

validator = ModuleResolverValidator('your_package_name')
success = validator.run_all_tests(verbose=True)
```

## Test Suite

The validator runs **6 comprehensive tests**:

### 1. Package Structure ✅
- Verifies package directory exists
- Checks all subpackages have `__init__.py`
- Discovers and validates package hierarchy

### 2. Circular Import Prevention ✅
- Dynamically discovers subpackages and implementation modules
- Builds regex patterns to detect problematic imports:
  - `from package import subpackage` (should import from `_*.py`)
  - `from package.subpackage import Class` (should import from implementation)
  - `module.ClassName.method()` (accessing class via module attribute)
  - `import package.subpackage` (direct subpackage imports)
- Scans all Python files for violations
- Distinguishes between lazy-loaded classes and safe regular modules

### 3. Lazy Loading Configuration ✅
- Verifies `DEFAULT_INCLUDE` exists in package `__init__.py`
- Checks for `bootstrap_package()` call
- Validates configuration structure

### 4. Runtime Import ✅
- Tests actual package import
- Verifies `PACKAGE_RESOLVER` attribute exists
- Checks `CLASS_TO_MODULE` mapping is populated
- Reports number of exposed classes

### 5. Lazy Class Access ✅
- Tests that classes in `CLASS_TO_MODULE` are accessible
- Validates lazy loading actually works
- Tests sample of classes (first 10)

### 6. Minimal Subpackage Inits ✅
- Ensures subpackage `__init__.py` files are minimal
- Flags bloated init files (>10 non-comment lines)
- Promotes proper lazy loading architecture

## Usage

### Standalone Validation
```bash
# Command line
python -c "from test.test_module_resolver import validate_package; validate_package('mayatk')"

# Or using the test file
python mayatk/test/test_module_resolver_integration.py --standalone
```

### In Unittest
```python
import unittest
from pythontk.test.test_module_resolver import ModuleResolverValidator

class TestMyPackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = ModuleResolverValidator('mypackage')
    
    def test_no_circular_imports(self):
        result = self.validator.test_circular_imports()
        self.assertTrue(result.passed)
    
    def test_all_validations(self):
        all_passed = self.validator.run_all_tests(verbose=True)
        self.assertTrue(all_passed)

if __name__ == "__main__":
    unittest.main()
```

### In Maya (for mayatk)
```python
# In Maya's Script Editor
exec(open(r'O:\Cloud\Code\_scripts\mayatk\test\test_module_resolver_integration.py').read())
```

## Example Output

```
======================================================================
MODULE RESOLVER VALIDATION: mayatk
======================================================================

✅ PASS: Package Structure
  Package structure is valid
    • Found 14 subpackages: anim_utils, cam_utils, core_utils, display_utils, edit_utils...

✅ PASS: Circular Import Prevention
  No circular import issues found
    • Scanned 88 files
    • Used 22 pattern checks

✅ PASS: Lazy Loading Configuration
  DEFAULT_INCLUDE configuration found
    • DEFAULT_INCLUDE has 47 entries

✅ PASS: Runtime Import
  Package imports successfully
    • Cleared 0 cached submodules
    • ✅ PACKAGE_RESOLVER attribute found
    • ✅ CLASS_TO_MODULE has 68 entries

✅ PASS: Lazy Class Access
  All classes accessible via lazy loading
    • Tested 10 classes: 10 accessible, 0 failed

✅ PASS: Minimal Subpackage Inits
  All subpackages have minimal __init__.py
    • All 14 subpackages have minimal __init__.py files

======================================================================
RESULTS: 6/6 tests passed
======================================================================
```

## Benefits

1. **Zero Maintenance** - Auto-discovers package structure
2. **Package Agnostic** - Works with any module_resolver-based package
3. **Comprehensive** - Tests structure, imports, configuration, and runtime
4. **Smart Detection** - Distinguishes between problematic and safe patterns
5. **Detailed Feedback** - Clear pass/fail with actionable details

## Packages Using This Framework

- ✅ `mayatk` - Maya toolkit (6/6 tests passing)
- ✅ `pythontk` - Python utilities
- ✅ `uitk` - UI toolkit  
- ✅ `metashape_workflow` - Metashape automation

## Migration Notes

### Removed Fallbacks
All packages updated to remove `fallbacks` parameter from `bootstrap_package()` calls:
- Old: `bootstrap_package(globals(), include=DEFAULT_INCLUDE, fallbacks=DEFAULT_FALLBACKS)`
- New: `bootstrap_package(globals(), include=DEFAULT_INCLUDE)`

Fallbacks masked import errors instead of fixing them at source. All problematic imports should now be resolved by:
1. Importing from implementation modules (`_*.py` files)
2. Adding missing entries to `DEFAULT_INCLUDE`

## Files

- `pythontk/test/test_module_resolver.py` - Framework implementation
- `mayatk/test/test_module_resolver_integration.py` - Mayatk test wrapper
- `mayatk/test/scan_circular_imports.py` - Standalone circular import scanner (legacy)
