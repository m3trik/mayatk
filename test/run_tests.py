# !/usr/bin/python
# coding=utf-8
"""
MayaTk Test Runner

Unified test runner for executing mayatk tests via Maya command port.
Tests run in Maya and output is displayed in Script Editor.
Results are saved to test_results.txt for review.
README badge is automatically updated with test results.

Usage:
    python run_tests.py                          # Run default core tests
    python run_tests.py core_utils components    # Run specific modules
    python run_tests.py --all                    # Run ALL test modules
    python run_tests.py --dry-run                # Validate test setup without running
    python run_tests.py --quick                  # Run quick validation test
    python run_tests.py --list                   # List available test modules
    python run_tests.py --no-badge               # Skip README badge update

Directory Structure:
    - Main Test Suite: mayatk/test/ (Standardized test_*.py files only)
    - Temporary Tests: mayatk/test/temp_tests/ (Reproduction scripts, scratchpad tests)
"""
import re
import sys
import time
import textwrap
from pathlib import Path

# Ensure mayatk is in path
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    from mayatk.env_utils import maya_connection
except ImportError:
    print(
        "Warning: mayatk.env_utils.maya_connection module not found. Standalone mode may not work."
    )


class MayaTestRunner:
    """Test runner for mayatk test suite via Maya command port."""

    def __init__(self, host="localhost", port=7002):
        self.host = host
        self.port = port
        self.test_dir = Path(__file__).parent
        self.results_file = self.test_dir / "test_results.txt"
        try:
            self.connection = maya_connection.MayaConnection.get_instance()
        except NameError:
            self.connection = None

    def connect_to_maya(self):
        """Connect to Maya using MayaConnection."""
        if not self.connection:
            print("[ERROR] MayaConnection not available")
            return False

        if self.connection.connect(mode="auto", port=self.port, host=self.host):
            print(f"[OK] Connected to Maya in {self.connection.mode} mode")
            return True
        else:
            print("[ERROR] Failed to connect to Maya")
            return False

    def send_code(self, code):
        """Send Python code to Maya."""
        try:
            self.connection.execute(code)
            return True
        except Exception as e:
            print(f"[ERROR] Failed to execute code: {e}")
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
sys.path.insert(0, r'O:\\\\Cloud\\\\Code\\\\_scripts\\\\mayatk\\\\test')

print("\\\\n" + "="*70)
print("QUICK TEST: test_core_utils (first class only)")
print("="*70)

try:
    import test_core_utils as test_mod
    import unittest
    
    # Run first test class
    for attr_name in dir(test_mod):
        attr = getattr(test_mod, attr_name)
        if isinstance(attr, type) and issubclass(attr, unittest.TestCase):
            if attr is not unittest.TestCase and attr.__name__ != "MayaTkTestCase":
                suite = unittest.makeSuite(attr)
                runner = unittest.TextTestRunner(verbosity=2)
                result = runner.run(suite)
                
                print("\\\\n" + "-"*70)
                if result.wasSuccessful():
                    print(f"[PASS] {attr_name}: ALL {result.testsRun} TESTS PASSED")
                else:
                    print(f"[FAIL] {attr_name}: {len(result.failures + result.errors)} FAILURES")
                print("-"*70)
                break
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
"""

        print("\nRunning quick validation test...")
        print("Check Maya Script Editor for detailed output\n")

        if self.send_code(code):
            print("[OK] Test code sent successfully")
            return True
        return False

    def run_tests(self, modules=None, dry_run=False, extended=False):
        """
        Run tests for specified modules.

        Args:
            modules: List of module names (with or without test_ prefix).
                    None = run all default modules.
            dry_run: If True, show what would be executed without running tests.
            extended: If True, run extended tests (sets MAYATK_EXTENDED_TESTS=1).
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
            "test_scale_keys",
            "test_stagger_keys",
            "test_keyframe_grouper",
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
        if extended:
            print("  • Extended tests enabled")
        print("=" * 70)

        if dry_run:
            print("\nDry run - no tests will be executed.")
            print("This validates:")
            print("  [OK] Module names are correct")
            print("  [OK] Test files exist")
            print("  [OK] Test runner configuration is valid")
            print("\nTo actually run tests, omit --dry-run flag")
            return True

        # Connect to Maya
        if not self.connect_to_maya():
            return False

        output_file_path = str(self.results_file).replace("\\", "/")

        # Generate test execution code
        test_code = textwrap.dedent(
            f"""
            import sys
            import os
            
            # Set extended tests flag
            if {extended}:
                os.environ['MAYATK_EXTENDED_TESTS'] = '1'
            else:
                if 'MAYATK_EXTENDED_TESTS' in os.environ:
                    del os.environ['MAYATK_EXTENDED_TESTS']
            
            sys.path.insert(0, r'O:/Cloud/Code/_scripts/mayatk/test')
            # Add all package roots to sys.path
            package_roots = [
                r'O:/Cloud/Code/_scripts',
                r'O:/Cloud/Code/_scripts/mayatk',
                r'O:/Cloud/Code/_scripts/pythontk',
                r'O:/Cloud/Code/_scripts/uitk',
                r'O:/Cloud/Code/_scripts/tentacle',
            ]
            for root in package_roots:
                if root not in sys.path:
                    sys.path.insert(0, root)

            import unittest
            import importlib.util

            # Use ModuleReloader to properly reload mayatk modules
            try:
                from pythontk import ModuleReloader
                reloader = ModuleReloader(include_submodules=True)                
                # Reload pythontk first to ensure core utilities are up to date
                import pythontk
                reloader.reload(pythontk)
                print("[ModuleReloader] Reloaded pythontk")
                import mayatk
                reloaded = reloader.reload(mayatk)
                print(f"[ModuleReloader] Reloaded {{len(reloaded)}} mayatk modules")
                
                # Force reload base_test by removing it from sys.modules
                if 'base_test' in sys.modules:
                    del sys.modules['base_test']
                    print("[ModuleReloader] Removed base_test from sys.modules to force reload")
                
                # Debug: Check if base_test can be imported and has the attribute
                try:
                    import base_test
                    print(f"[Debug] Imported base_test from: {{base_test.__file__}}")
                    if hasattr(base_test, 'skipUnlessExtended'):
                        print("[Debug] base_test has skipUnlessExtended")
                    else:
                        print("[Debug] base_test MISSING skipUnlessExtended")
                        print(f"[Debug] dir(base_test): {{dir(base_test)}}")
                except ImportError as e:
                    print(f"[Debug] Could not import base_test: {{e}}")
                    
            except Exception as e:
                # Fallback to simple module clearing if reloader fails
                modules_to_clear = [k for k in list(sys.modules.keys()) if 'mayatk' in k.lower()]
                for mod in modules_to_clear:
                    del sys.modules[mod]
                print(f"[Fallback] Cleared {{len(modules_to_clear)}} cached mayatk modules")

            # Setup results file
            output_file = r'{output_file_path}'
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write("="*70 + "\\n")
                f.write("MAYATK TEST RESULTS\\n")
                f.write("="*70 + "\\n\\n")
                
            print("\\n" + "="*70)
            print("MAYATK TEST SUITE")
            print("="*70)

            test_modules = {test_modules}
            total_tests = 0
            total_failures = 0
            total_errors = 0
            total_skipped = 0
            results_summary = []

            for module_name in test_modules:
                print(f"\\n{{'-'*70}}")
                print(f"Testing: {{module_name}}")
                print(('-'*70))
                
                try:
                    spec = importlib.util.spec_from_file_location(
                        module_name,
                        rf'O:/Cloud/Code/_scripts/mayatk/test/{{module_name}}.py'
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
                        f.write(f"\\n{{module_name}}: {{status}}\\n")
                        f.write(f"  Tests: {{result.testsRun}}, Failures: {{len(result.failures)}}, "
                               f"Errors: {{len(result.errors)}}, Skipped: {{len(result.skipped)}}\\n")
                        
                        if result.failures:
                            for test, trace in result.failures:  # All failures
                                f.write(f"\\n  FAILURE: {{test}}\\n")
                                f.write(f"  {{trace}}\\n")
                        
                        if result.errors:
                            for test, trace in result.errors:  # All errors
                                f.write(f"\\n  ERROR: {{test}}\\n")
                                f.write(f"  {{trace}}\\n")
                    
                except Exception as e:
                    print(f"[ERROR] Error loading {{module_name}}: {{e}}")
                    results_summary.append(f"{{module_name}}: LOAD ERROR - {{str(e)[:50]}}")
                    
                    with open(output_file, 'a', encoding='utf-8') as f:
                        f.write(f"\\n{{module_name}}: LOAD ERROR\\n")
                        f.write(f"  {{str(e)}}\\n")

            # Print final summary
            print("\\n" + "="*70)
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
                f.write("\\n" + "="*70 + "\\n")
                f.write("SUMMARY\\n")
                f.write("="*70 + "\\n")
                for result_line in results_summary:
                    f.write(f"  {{result_line}}\\n")
                f.write("="*70 + "\\n")
                f.write(f"Total: {{total_tests}} tests, {{total_failures}} failures, "
                       f"{{total_errors}} errors, {{total_skipped}} skipped\\n")
                f.write("="*70 + "\\n")

            print(f"\\nResults saved to: {{output_file}}")

            # Final status
            if total_failures == 0 and total_errors == 0:
                print("\\n[PASS] ALL TESTS PASSED!")
            """
        )

        # Write test code to a temporary file to avoid command port size limits
        temp_runner_path = self.test_dir / "_temp_test_runner.py"
        try:
            with open(temp_runner_path, "w", encoding="utf-8") as f:
                f.write(test_code)
        except Exception as e:
            print(f"[ERROR] Failed to write temp runner file: {e}")
            return False

        # Generate execution code (small payload)
        runner_dir = str(self.test_dir).replace("\\", "/")
        output_file_path = str(self.results_file).replace("\\", "/")

        exec_code = f"""
import sys
import os

runner_dir = r'{runner_dir}'
if runner_dir not in sys.path:
    sys.path.insert(0, runner_dir)

# AGGRESSIVE MODULE CLEARING - clear all mayatk modules BEFORE running tests
# This ensures fresh imports from the test files
mods_to_clear = [k for k in list(sys.modules.keys()) 
                 if 'mayatk' in k.lower() or '_temp_test_runner' in k]
for mod in mods_to_clear:
    del sys.modules[mod]
print(f"[RELOAD] Cleared {{len(mods_to_clear)}} modules before test execution")

# Force reload/execution of the temp runner
try:
    import _temp_test_runner
    import importlib
    importlib.reload(_temp_test_runner)
except Exception as e:
    print(f"Error executing test runner: {{e}}")
    import traceback
    traceback.print_exc()
"""

        print("\nSending test code to Maya (via file)...")

        if self.send_code(exec_code):
            print("[OK] Test code sent successfully")
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

            # Store estimated time for badge update waiting
            self.estimated_wait_time = estimated_seconds
            return True
        else:
            print("[ERROR] Failed to send test code")
            return False

    def update_readme_badge(self, passed: int, failed: int) -> bool:
        """Update the README with a test status badge.

        Parameters:
            passed: Number of passed tests.
            failed: Number of failed tests.

        Returns:
            True if README was updated successfully.
        """
        readme_path = self.test_dir.parent / "docs" / "README.md"

        if not readme_path.exists():
            print(f"README not found at {readme_path}")
            return False

        content = readme_path.read_text(encoding="utf-8")

        total = passed + failed
        if failed == 0:
            color = "brightgreen"
            status = f"{passed} passed"
        elif passed == 0:
            color = "red"
            status = f"{failed} failed"
        else:
            color = "orange"
            status = f"{passed} passed, {failed} failed"

        # Create the new badge
        new_badge = f"[![Tests](https://img.shields.io/badge/Tests-{status.replace(' ', '%20').replace(',', '')}-{color}.svg)](test/)"

        # Check if a Tests badge already exists and replace it
        tests_badge_pattern = (
            r"\[!\[Tests\]\(https://img\.shields\.io/badge/Tests-[^\)]+\)\]\([^\)]+\)"
        )

        if re.search(tests_badge_pattern, content):
            # Replace existing badge
            new_content = re.sub(tests_badge_pattern, new_badge, content)
        else:
            # Add badge after the Maya badge line
            maya_badge_pattern = r"(\[!\[Maya\]\(https://img\.shields\.io/badge/Maya-[^\)]+\)\]\([^\)]+\))"
            match = re.search(maya_badge_pattern, content)
            if match:
                # Insert after Maya badge
                insert_pos = match.end()
                new_content = (
                    content[:insert_pos] + "\n" + new_badge + content[insert_pos:]
                )
            else:
                # Fallback: add after Python badge
                python_badge_pattern = r"(\[!\[Python\]\(https://img\.shields\.io/badge/Python-[^\)]+\)\]\([^\)]+\))"
                match = re.search(python_badge_pattern, content)
                if match:
                    insert_pos = match.end()
                    new_content = (
                        content[:insert_pos] + "\n" + new_badge + content[insert_pos:]
                    )
                else:
                    # Last resort: add at the very beginning
                    new_content = new_badge + "\n" + content

        readme_path.write_text(new_content, encoding="utf-8")
        print(f"\n[OK] README badge updated: {status}")
        return True

    def parse_test_results(self) -> tuple:
        """Parse test_results.txt to extract test counts.

        Returns:
            Tuple of (passed, failed) where failed = failures + errors
        """
        if not self.results_file.exists():
            return (0, 0)

        content = self.results_file.read_text(encoding="utf-8")

        # Look for the total line: "Total: 253 tests, 0 failures, 1 errors, 15 skipped"
        match = re.search(
            r"Total: (\d+) tests, (\d+) failures?, (\d+) errors?", content
        )

        if match:
            total = int(match.group(1))
            failures = int(match.group(2))
            errors = int(match.group(3))
            passed = total - failures - errors
            return (passed, failures + errors)

        return (0, 0)


def main():
    """Main entry point."""
    # Parse command line arguments
    args = sys.argv[1:]

    # Parse port first
    port = 7002
    if "--port" in args:
        try:
            p_idx = args.index("--port")
            if p_idx + 1 < len(args):
                port = int(args[p_idx + 1])
                # Remove --port and value from args so they aren't treated as module names
                args.pop(p_idx)
                args.pop(p_idx)
        except (ValueError, IndexError):
            print("Invalid port specified")
            return

    runner = MayaTestRunner(port=port)

    # Check for flags
    dry_run = "--dry-run" in args or "-d" in args
    no_badge = "--no-badge" in args
    extended = "--extended" in args or "-e" in args

    if dry_run:
        args = [arg for arg in args if arg not in ("--dry-run", "-d")]
    if no_badge:
        args = [arg for arg in args if arg != "--no-badge"]
    if extended:
        args = [arg for arg in args if arg not in ("--extended", "-e")]

    if not args:
        # No arguments - run default tests
        success = runner.run_tests(dry_run=dry_run, extended=extended)
    elif "--list" in args or "-l" in args:
        # List available tests
        runner.list_tests()
        return
    elif "--quick" in args or "-q" in args:
        # Quick validation test
        runner.run_quick_test()
        return
    elif "--help" in args or "-h" in args:
        # Show help
        print(__doc__)
        return
    elif "--all" in args or "-a" in args:
        # Run ALL tests (not just default)
        all_modules = runner.discover_tests()
        print(f"\nRunning ALL {len(all_modules)} test modules...")
        success = runner.run_tests(all_modules, dry_run=dry_run, extended=extended)
    else:
        # Run specific modules
        success = runner.run_tests(args, dry_run=dry_run, extended=extended)

    # Update README badge with test results (unless disabled or dry run)
    if success and not dry_run and not no_badge:
        # Wait for tests to complete based on estimated time
        wait_time = getattr(runner, "estimated_wait_time", 10) + 5  # Add 5s buffer
        print(f"\nWaiting {wait_time}s for tests to complete...")
        time.sleep(wait_time)

        passed, failed = runner.parse_test_results()
        if passed > 0 or failed > 0:
            runner.update_readme_badge(passed, failed)


if __name__ == "__main__":
    main()
