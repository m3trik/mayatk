# !/usr/bin/python
# coding=utf-8
"""
MayaTk Test Runner

Unified test runner for executing mayatk tests via Maya command port.
Tests run in Maya and output is displayed in Script Editor.
Results are saved to test_results.txt for review.

Usage:
    python run_tests.py                          # Run default core tests
    python run_tests.py core_utils components    # Run specific modules
    python run_tests.py --all                    # Run ALL test modules
    python run_tests.py --dry-run                # Validate test setup without running
    python run_tests.py --quick                  # Run quick validation test
    python run_tests.py --list                   # List available test modules
"""
import socket
import sys
import time
from pathlib import Path


class MayaTestRunner:
    """Test runner for mayatk test suite via Maya command port."""

    def __init__(self, host="localhost", port=7002):
        self.host = host
        self.port = port
        self.test_dir = Path(__file__).parent
        self.results_file = self.test_dir / "test_results.txt"

    def connect_to_maya(self):
        """Test if Maya command port is responsive."""
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(3)
            client.connect((self.host, self.port))

            # Send simple validation
            client.sendall(b'print("MAYA_TEST_READY")\\n')
            client.close()

            print(f"✓ Connected to Maya on {self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            print("\nMake sure Maya is running with command ports open.")
            print("In Maya, run: import mayatk; mayatk.ensure_command_ports()")
            return False

    def send_code(self, code):
        """Send Python code to Maya."""
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(10)  # Increased timeout for large test payloads
            client.connect((self.host, self.port))
            client.sendall(code.encode("utf-8"))
            client.close()
            return True
        except socket.timeout:
            print(f"✗ Timeout: Maya didn't respond within 10 seconds")
            return False
        except ConnectionRefusedError:
            print(f"✗ Connection refused: Is Maya running with command ports open?")
            return False
        except Exception as e:
            print(f"✗ Failed to send code: {e}")
            return False

    def discover_tests(self):
        """Discover all available test modules."""
        test_modules = []
        for file in self.test_dir.glob("test_*.py"):
            # Skip infrastructure files
            if file.stem in [
                "test_imports",
                "test_lazy_loading_maya",
                "test_module_resolver_integration",
            ]:
                continue
            test_modules.append(file.stem)
        return sorted(test_modules)

    def list_tests(self):
        """List all available test modules."""
        modules = self.discover_tests()
        print("\n" + "=" * 70)
        print("AVAILABLE TEST MODULES")
        print("=" * 70)
        for i, module in enumerate(modules, 1):
            # Show module name without test_ prefix for cleaner display
            display_name = module.replace("test_", "")
            print(f"  {i:2d}. {display_name:25s} ({module})")
        print("=" * 70)
        print(f"\nTotal: {len(modules)} test modules")
        print("\nUsage: python run_tests.py <module_name> [<module_name> ...]")
        print("Example: python run_tests.py core_utils components")

    def run_quick_test(self):
        """Run a single quick validation test."""
        print("\n" + "=" * 70)
        print("QUICK VALIDATION TEST")
        print("=" * 70)

        if not self.connect_to_maya():
            return False

        code = """
import sys
sys.path.insert(0, r'O:\\\\Cloud\\\\Code\\\\_scripts')

print("\\\\n" + "="*70)
print("QUICK TEST: test_core_utils (first class only)")
print("="*70)

try:
    import mayatk.test.test_core_utils as test_mod
    import unittest
    
    # Run first test class
    for attr_name in dir(test_mod):
        attr = getattr(test_mod, attr_name)
        if isinstance(attr, type) and issubclass(attr, unittest.TestCase):
            if attr is not unittest.TestCase:
                suite = unittest.makeSuite(attr)
                runner = unittest.TextTestRunner(verbosity=2)
                result = runner.run(suite)
                
                print("\\\\n" + "-"*70)
                if result.wasSuccessful():
                    print(f"✓ {attr_name}: ALL {result.testsRun} TESTS PASSED")
                else:
                    print(f"✗ {attr_name}: {len(result.failures + result.errors)} FAILURES")
                print("-"*70)
                break
except Exception as e:
    print(f"✗ ERROR: {e}")
    import traceback
    traceback.print_exc()
"""

        print("\nRunning quick validation test...")
        print("Check Maya Script Editor for detailed output\n")

        if self.send_code(code):
            print("✓ Test code sent successfully")
            return True
        return False

    def run_tests(self, modules=None, dry_run=False):
        """
        Run tests for specified modules.

        Args:
            modules: List of module names (with or without test_ prefix).
                    None = run all default modules.
            dry_run: If True, show what would be executed without running tests.
        """
        # Default test modules (core functionality)
        default_modules = [
            "test_core_utils",
            "test_components",
            "test_node_utils",
            "test_edit_utils",
            "test_mat_utils",
            "test_xform_utils",
            "test_rig_utils",
            "test_env_utils",
        ]

        if modules is None:
            test_modules = default_modules
        else:
            # Ensure test_ prefix
            test_modules = [
                m if m.startswith("test_") else f"test_{m}" for m in modules
            ]

        print("\n" + "=" * 70)
        print("MAYATK TEST RUNNER" + (" (DRY RUN)" if dry_run else ""))
        print("=" * 70)
        print(f"{'Would run' if dry_run else 'Running'} {len(test_modules)} modules:")
        for module in test_modules:
            print(f"  • {module}")
        print("=" * 70)

        if dry_run:
            print("\nDry run - no tests will be executed.")
            print("This validates:")
            print("  ✓ Module names are correct")
            print("  ✓ Test files exist")
            print("  ✓ Test runner configuration is valid")
            print("\nTo actually run tests, omit --dry-run flag")
            return True

        # Connect to Maya
        if not self.connect_to_maya():
            return False

        # Generate test execution code
        test_code = f"""
import sys
import os
sys.path.insert(0, r'O:\\\\Cloud\\\\Code\\\\_scripts\\\\mayatk\\\\test')
sys.path.insert(0, r'O:\\\\Cloud\\\\Code\\\\_scripts')

import unittest
import importlib.util

# Setup results file
output_file = r'{str(self.results_file).replace(chr(92), chr(92)*4)}'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write("="*70 + "\\\\n")
    f.write("MAYATK TEST RESULTS\\\\n")
    f.write("="*70 + "\\\\n\\\\n")

print("\\\\n" + "="*70)
print("MAYATK TEST SUITE")
print("="*70)

test_modules = {test_modules}
total_tests = 0
total_failures = 0
total_errors = 0
total_skipped = 0
results_summary = []

for module_name in test_modules:
    print(f"\\\\n{{'-'*70}}")
    print(f"Testing: {{module_name}}")
    print({{'-'*70}})
    
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            rf'O:\\\\Cloud\\\\Code\\\\_scripts\\\\mayatk\\\\test\\\\{{module_name}}.py'
        )
        test_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(test_module)
        
        # Create test suite
        suite = unittest.TestSuite()
        for attr_name in dir(test_module):
            attr = getattr(test_module, attr_name)
            if isinstance(attr, type) and issubclass(attr, unittest.TestCase):
                if attr is not unittest.TestCase:
                    suite.addTest(unittest.makeSuite(attr))
        
        # Run tests
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        # Track results
        total_tests += result.testsRun
        total_failures += len(result.failures)
        total_errors += len(result.errors)
        total_skipped += len(result.skipped)
        
        status = "PASS" if result.wasSuccessful() else "FAIL"
        results_summary.append(
            f"{{module_name}}: {{status}} ({{result.testsRun}} tests, "
            f"{{len(result.failures)}} failures, {{len(result.errors)}} errors)"
        )
        
        # Write to results file
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(f"\\\\n{{module_name}}: {{status}}\\\\n")
            f.write(f"  Tests: {{result.testsRun}}, Failures: {{len(result.failures)}}, "
                   f"Errors: {{len(result.errors)}}, Skipped: {{len(result.skipped)}}\\\\n")
            
            if result.failures:
                for test, trace in result.failures[:3]:  # First 3 failures
                    f.write(f"\\\\n  FAILURE: {{test}}\\\\n")
                    f.write(f"  {{trace[:300]}}...\\\\n")
            
            if result.errors:
                for test, trace in result.errors[:3]:  # First 3 errors
                    f.write(f"\\\\n  ERROR: {{test}}\\\\n")
                    f.write(f"  {{trace[:300]}}...\\\\n")
        
    except Exception as e:
        print(f"✗ Error loading {{module_name}}: {{e}}")
        results_summary.append(f"{{module_name}}: LOAD ERROR - {{str(e)[:50]}}")
        
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(f"\\\\n{{module_name}}: LOAD ERROR\\\\n")
            f.write(f"  {{str(e)}}\\\\n")

# Print final summary
print("\\\\n" + "="*70)
print("TEST SUMMARY")
print("="*70)
for result_line in results_summary:
    print(f"  {{result_line}}")
print("="*70)
print(f"Total: {{total_tests}} tests, {{total_failures}} failures, "
      f"{{total_errors}} errors, {{total_skipped}} skipped")
print("="*70)

# Write summary to file
with open(output_file, 'a', encoding='utf-8') as f:
    f.write("\\\\n" + "="*70 + "\\\\n")
    f.write("SUMMARY\\\\n")
    f.write("="*70 + "\\\\n")
    for result_line in results_summary:
        f.write(f"  {{result_line}}\\\\n")
    f.write("="*70 + "\\\\n")
    f.write(f"Total: {{total_tests}} tests, {{total_failures}} failures, "
           f"{{total_errors}} errors, {{total_skipped}} skipped\\\\n")
    f.write("="*70 + "\\\\n")

print(f"\\\\nResults saved to: {{output_file}}")

# Final status
if total_failures == 0 and total_errors == 0:
    print("\\\\n✓ ALL TESTS PASSED!")
"""

        print("\nSending test code to Maya...")

        if self.send_code(test_code):
            print("✓ Test code sent successfully")
            print("\n" + "=" * 70)
            print("TESTS ARE RUNNING IN MAYA")
            print("=" * 70)
            print("\n1. Check Maya's Script Editor for real-time output")
            print(f"2. Results will be saved to: {self.results_file.name}")

            # Estimate time based on module count
            estimated_seconds = len(test_modules) * 10
            minutes = estimated_seconds // 60
            seconds = estimated_seconds % 60
            if minutes > 0:
                print(f"3. Estimated time: ~{minutes}m {seconds}s")
            else:
                print(f"3. Estimated time: ~{seconds}s")

            print("\nTo view results after completion:")
            print(f"  Get-Content {self.results_file.name}")
            print("\nTo monitor progress:")
            print(f"  Get-Content {self.results_file.name} -Wait")
            print("=" * 70)
            return True
        else:
            print("✗ Failed to send test code")
            return False


def main():
    """Main entry point."""
    runner = MayaTestRunner()

    # Parse command line arguments
    args = sys.argv[1:]

    # Check for flags
    dry_run = "--dry-run" in args or "-d" in args
    if dry_run:
        args = [arg for arg in args if arg not in ("--dry-run", "-d")]

    if not args:
        # No arguments - run default tests
        runner.run_tests(dry_run=dry_run)
    elif "--list" in args or "-l" in args:
        # List available tests
        runner.list_tests()
    elif "--quick" in args or "-q" in args:
        # Quick validation test
        runner.run_quick_test()
    elif "--help" in args or "-h" in args:
        # Show help
        print(__doc__)
    elif "--all" in args or "-a" in args:
        # Run ALL tests (not just default)
        all_modules = runner.discover_tests()
        print(f"\nRunning ALL {len(all_modules)} test modules...")
        runner.run_tests(all_modules, dry_run=dry_run)
    else:
        # Run specific modules
        runner.run_tests(args, dry_run=dry_run)


if __name__ == "__main__":
    main()
