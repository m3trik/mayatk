# Maya Test Execution - Quick Reference
# ======================================

# ============================================================================
# IN MAYA - ONE-TIME SETUP
# ============================================================================

# Open command port for remote test execution
import mayatk

mayatk.openPorts(python=":7002")

# Or use the detailed setup script:
import sys

sys.path.insert(0, r"O:\Cloud\Code\_scripts\mayatk\test")
import setup_maya_for_tests

setup_maya_for_tests.setup()


# ============================================================================
# FROM IDE/TERMINAL - RUN TESTS REMOTELY
# ============================================================================

# Run all tests:
# python O:\Cloud\Code\_scripts\mayatk\test\maya_test_runner.py

# Run specific tests:
# python maya_test_runner.py core_utils_test.py mat_utils_test.py


# ============================================================================
# IN MAYA - RUN TESTS DIRECTLY
# ============================================================================

import unittest
import sys

sys.path.insert(0, r"O:\Cloud\Code\_scripts\mayatk\test")

# Run all tests
loader = unittest.TestLoader()
suite = loader.discover(
    start_dir=r"O:\Cloud\Code\_scripts\mayatk\test", pattern="*_test.py"
)
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

# Print summary
print("\n" + "=" * 70)
print(f"Tests run: {result.testsRun}")
print(f"Failures: {len(result.failures)}")
print(f"Errors: {len(result.errors)}")
print(f"Skipped: {len(result.skipped)}")
print("=" * 70)


# ============================================================================
# VERIFYING LAZY LOADING & NO FALLBACKS
# ============================================================================

import mayatk

# These should work (properly configured in DEFAULT_INCLUDE)
from mayatk import MeshDiagnostics  # ✓
from mayatk import AnimCurveDiagnostics  # ✓
from mayatk import CoreUtils  # ✓

# These should fail with clear AttributeError (no fallbacks to mask issues)
# from mayatk import NonExistentClass  # ✗ AttributeError: module mayatk has no attribute 'NonExistentClass'


# ============================================================================
# MANAGING COMMAND PORTS
# ============================================================================

import pymel.core as pm

# Open ports
pm.commandPort(name=":7002", sourceType="python")
pm.commandPort(name=":7001", sourceType="mel")

# Close ports
pm.commandPort(name=":7002", close=True)
pm.commandPort(name=":7001", close=True)

# Check if port is open (will raise RuntimeError if already open)
try:
    pm.commandPort(name=":7002", sourceType="python")
except RuntimeError:
    print("Port :7002 already open")


# ============================================================================
# EXAMPLE TEST CASE
# ============================================================================

import unittest
import pymel.core as pm
import mayatk as mtk


class ExampleTest(unittest.TestCase):
    """Example test demonstrating best practices"""

    def setUp(self):
        """Create clean test scene"""
        pm.mel.file(new=True, force=True)
        self.test_obj = pm.polyCube(name="test_cube")[0]

    def tearDown(self):
        """Clean up test scene"""
        if pm.objExists(self.test_obj):
            pm.delete(self.test_obj)

    def test_object_creation(self):
        """Verify test object was created"""
        self.assertTrue(pm.objExists("test_cube"))
        self.assertIsNotNone(self.test_obj)

    def test_mayatk_import(self):
        """Verify mayatk classes are accessible"""
        self.assertIsNotNone(mtk.MeshDiagnostics)
        self.assertIsNotNone(mtk.AnimCurveDiagnostics)


# ============================================================================
# NOTES
# ============================================================================

# 1. Module Resolver Changes:
#    - Removed fallbacks mechanism (fix problems at source, not mask them)
#    - All imports managed through root __init__.py DEFAULT_INCLUDE
#    - Subpackage __init__.py files can be minimal/empty
#
# 2. Test Infrastructure:
#    - Remote execution via command port (recommended)
#    - Direct execution in Maya (alternative)
#    - setup_maya_for_tests.py for easy Maya setup
#    - maya_test_runner.py for IDE/terminal execution
#
# 3. Best Practices:
#    - Use setUp/tearDown for scene management
#    - Keep tests isolated and independent
#    - Use descriptive test names
#    - Document what each test verifies
#    - Always clean up created objects
