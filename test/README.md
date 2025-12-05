# Maya Test Infrastructure

This directory contains unit tests for the mayatk package and utilities for running them in Maya.

## Quick Start

### Windows PowerShell Script (Easiest)

Simply use the provided Windows script for all common operations:

```powershell
# From the test directory:
cd O:\Cloud\Code\_scripts\mayatk\test

# Run default core tests (8 modules)
.\test.ps1

# List all available test modules
.\test.ps1 -List

# Run quick validation test
.\test.ps1 -Quick

# Run ALL test modules (17 modules)
.\test.ps1 -All

# Run specific modules
.\test.ps1 core components
.\test.ps1 edit xform mat

# View last test results
.\test.ps1 -Results

# Monitor tests in real-time
.\test.ps1 -Watch

# Test Maya connection
.\test.ps1 -Connect

# Get help
.\test.ps1 -Help
```

Or use the batch file wrapper:
```cmd
test.bat -List
test.bat core edit
```

### Python Direct (Advanced)

Use the Python runner directly for more control:

```powershell
# Run default core tests
python run_tests.py

# Run specific modules
python run_tests.py core_utils components

# Run ALL test modules
python run_tests.py --all

# List available tests
python run_tests.py --list

# Quick validation test
python run_tests.py --quick

# Dry run (validate without running)
python run_tests.py --dry-run
```

## Overview

The test infrastructure supports execution via Maya's command port:

1. **Remote execution** - Run tests from terminal/IDE while Maya is running
2. **Unified test runner** - Single runner for all test modules
3. **Real-time monitoring** - See progress in Maya Script Editor and terminal
4. **Results tracking** - Results saved to `test_results.txt`

## Setup

### One-Time Maya Setup

In Maya's Script Editor, run:
```python
import mayatk
mayatk.ensure_command_ports()
```

This opens the Python command port on port 7002 (default).

```python
import unittest
import sys

sys.path.insert(0, r'O:\Cloud\Code\_scripts\mayatk\test')

# Run all tests
loader = unittest.TestLoader()
suite = loader.discover(start_dir=r'O:\Cloud\Code\_scripts\mayatk\test', pattern='*_test.py')
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)
```

## Test Files

- `*_test.py` - Individual test modules
- `maya_test_runner.py` - Remote test execution utility
- `setup_maya_for_tests.py` - Maya setup helper for remote testing
- `run_tests.py` - Legacy direct test runner

## Command Port Details

### What is a Command Port?

Maya's command port allows external applications to send Python/MEL commands to a running Maya instance via TCP/IP sockets.

### Opening Ports Manually

```python
import pymel.core as pm

# Open Python command port
pm.commandPort(name=':7002', sourceType='python')

# Open MEL command port
pm.commandPort(name=':7001', sourceType='mel')
```

Or using mayatk:
```python
import mayatk
mayatk.openPorts(python=':7002', mel=':7001')
```

### Closing Ports

```python
pm.commandPort(name=':7002', close=True)
pm.commandPort(name=':7001', close=True)
```

## Architecture

### Module Resolver & Lazy Loading

The package uses a custom module resolver (`pythontk.core_utils.module_resolver`) that enables:

- **Lazy loading**: Modules are imported only when accessed
- **Centralized configuration**: All imports managed through root `__init__.py`
- **Empty subpackage __init__ files**: Subpackages don't need complex initialization

Example configuration in `mayatk/__init__.py`:
```python
DEFAULT_INCLUDE = {
    # Expose all classes from legacy modules
    "_core_utils": "*",
    
    # Expose specific classes from nested modules
    "core_utils.diagnostics.mesh": "MeshDiagnostics",
    "core_utils.diagnostics.animation": "AnimCurveDiagnostics",
    
    # Expose functions
    "env_utils.command_port": ["openPorts"],
}

bootstrap_package(globals(), include=DEFAULT_INCLUDE)
```

### No Fallbacks Policy

The module resolver **no longer supports fallbacks**. If a module import fails, the error must be fixed at the source rather than masked with a fallback mapping. This ensures:

- Clear error messages pointing to the real problem
- No hidden import failures
- Easier debugging and maintenance

## Writing Tests

### Basic Test Structure

```python
import unittest
import pymel.core as pm
import mayatk as mtk

class MyFeatureTest(unittest.TestCase):
    """Tests for MyFeature functionality"""
    
    def setUp(self):
        """Create test scene"""
        pm.mel.file(new=True, force=True)
        # Setup test objects
        
    def tearDown(self):
        """Clean up test scene"""
        # Delete test objects
        pass
        
    def test_basic_functionality(self):
        """Test basic feature works"""
        result = mtk.some_function()
        self.assertIsNotNone(result)
```

### Best Practices

1. **Clean scene state**: Use `setUp()` and `tearDown()` to manage scene state
2. **Descriptive names**: Test names should clearly indicate what they test
3. **Isolated tests**: Each test should be independent
4. **Docstrings**: Document what each test verifies
5. **Assertions**: Use appropriate assertion methods (`assertEqual`, `assertIsNotNone`, etc.)

## Troubleshooting

### "Connection refused" when running remote tests

**Cause**: Maya's command port is not open or is on a different port.

**Solution**: Run `setup_maya_for_tests.py` in Maya to open the command port.

### Import errors in tests

**Cause**: Test directory or mayatk not in Python path.

**Solution**: Ensure paths are added:
```python
import sys
sys.path.insert(0, r'O:\Cloud\Code\_scripts')
sys.path.insert(0, r'O:\Cloud\Code\_scripts\mayatk\test')
```

### Tests hang indefinitely

**Cause**: Maya is waiting for input or a modal dialog is open.

**Solution**: 
- Close any open dialogs in Maya
- Ensure tests don't create modal dialogs
- Use `pm.mel.file(new=True, force=True)` to avoid "save changes" prompts

### Module not found errors

**Cause**: Module resolver configuration is incorrect or module doesn't exist.

**Solution**: 
1. Check that the module exists at the specified path
2. Verify `DEFAULT_INCLUDE` mapping in `mayatk/__init__.py`
3. Ensure no typos in module/class names

## Development Workflow

1. **Write test** - Create or update test file
2. **Setup Maya** - Run `setup_maya_for_tests.py` in Maya (if not already done)
3. **Run tests** - Execute `maya_test_runner.py` from IDE
4. **Fix issues** - Address any failures
5. **Verify** - Re-run tests to confirm fixes
6. **Commit** - Commit working tests with implementation

## Additional Resources

- [unittest documentation](https://docs.python.org/3/library/unittest.html)
- [PyMEL documentation](https://help.autodesk.com/view/MAYAUL/2024/ENU/)
- [Maya Command Port documentation](https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=__CommandsPython_commandPort_html)

---

**Note**: This test infrastructure requires Maya to be running for test execution. For pure Python functionality, consider creating separate unit tests that don't require Maya.
