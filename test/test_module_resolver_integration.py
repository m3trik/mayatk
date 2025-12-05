#!/usr/bin/env python
# coding=utf-8
"""
Test mayatk's module resolver integration using pythontk's validation framework.
"""
import sys
import unittest
from pathlib import Path

try:
    from test.test_module_resolver import ModuleResolverValidator
except ImportError as e:
    print(f"Could not import ModuleResolverValidator: {e}")
    print("This test requires pythontk to be available")
    sys.exit(1)


class TestMayatkModuleResolver(unittest.TestCase):
    """Test mayatk's module resolver implementation."""

    @classmethod
    def setUpClass(cls):
        """Set up the validator once for all tests."""
        package_path = Path(__file__).parent.parent
        cls.validator = ModuleResolverValidator("mayatk", package_path)

    def test_package_structure(self):
        """Test that mayatk has valid package structure."""
        result = self.validator.test_package_structure()
        self.assertTrue(
            result.passed, f"{result.message}\n" + "\n".join(result.details)
        )

    def test_no_circular_imports(self):
        """Test that mayatk has no circular import patterns."""
        result = self.validator.test_circular_imports()
        self.assertTrue(
            result.passed, f"{result.message}\n" + "\n".join(result.details)
        )

    def test_lazy_loading_configured(self):
        """Test that mayatk has proper lazy loading configuration."""
        result = self.validator.test_lazy_loading_config()
        self.assertTrue(
            result.passed, f"{result.message}\n" + "\n".join(result.details)
        )

    def test_runtime_import_works(self):
        """Test that mayatk can be imported at runtime."""
        result = self.validator.test_runtime_import()
        self.assertTrue(
            result.passed, f"{result.message}\n" + "\n".join(result.details)
        )

    def test_lazy_class_access_works(self):
        """Test that classes are accessible via lazy loading."""
        result = self.validator.test_lazy_class_access()
        self.assertTrue(
            result.passed, f"{result.message}\n" + "\n".join(result.details)
        )

    def test_subpackage_inits_minimal(self):
        """Test that subpackage __init__ files are minimal."""
        result = self.validator.test_minimal_subpackage_inits()
        self.assertTrue(
            result.passed, f"{result.message}\n" + "\n".join(result.details)
        )

    def test_all_validations(self):
        """Run all validations together and check overall result."""
        all_passed = self.validator.run_all_tests(verbose=True)
        self.assertTrue(all_passed, "Some module resolver validations failed")


def run_validation_standalone():
    """Run validation outside of unittest framework."""
    package_path = Path(__file__).parent.parent
    validator = ModuleResolverValidator("mayatk", package_path)
    success = validator.run_all_tests(verbose=True)
    return success


if __name__ == "__main__":
    # Can be run standalone or via unittest
    if "--standalone" in sys.argv:
        success = run_validation_standalone()
        sys.exit(0 if success else 1)
    else:
        unittest.main()
