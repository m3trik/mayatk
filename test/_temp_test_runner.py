
import sys
import os

# Set extended tests flag
if False:
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
    print(f"[ModuleReloader] Reloaded {len(reloaded)} mayatk modules")

    # Force reload base_test by removing it from sys.modules
    if 'base_test' in sys.modules:
        del sys.modules['base_test']
        print("[ModuleReloader] Removed base_test from sys.modules to force reload")

    # Debug: Check if base_test can be imported and has the attribute
    try:
        import base_test
        print(f"[Debug] Imported base_test from: {base_test.__file__}")
        if hasattr(base_test, 'skipUnlessExtended'):
            print("[Debug] base_test has skipUnlessExtended")
        else:
            print("[Debug] base_test MISSING skipUnlessExtended")
            print(f"[Debug] dir(base_test): {dir(base_test)}")
    except ImportError as e:
        print(f"[Debug] Could not import base_test: {e}")

except Exception as e:
    # Fallback to simple module clearing if reloader fails
    modules_to_clear = [k for k in list(sys.modules.keys()) if 'mayatk' in k.lower()]
    for mod in modules_to_clear:
        del sys.modules[mod]
    print(f"[Fallback] Cleared {len(modules_to_clear)} cached mayatk modules")

# Setup results file
output_file = r'o:/Cloud/Code/_scripts/mayatk/test/test_results.txt'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write("="*70 + "\n")
    f.write("MAYATK TEST RESULTS\n")
    f.write("="*70 + "\n\n")

print("\n" + "="*70)
print("MAYATK TEST SUITE")
print("="*70)

test_modules = ['test_xform_utils']
total_tests = 0
total_failures = 0
total_errors = 0
total_skipped = 0
results_summary = []

for module_name in test_modules:
    print(f"\n{'-'*70}")
    print(f"Testing: {module_name}")
    print(('-'*70))

    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            rf'O:/Cloud/Code/_scripts/mayatk/test/{module_name}.py'
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
            f"{module_name}: {status} ({result.testsRun} tests, "
            f"{len(result.failures)} failures, {len(result.errors)} errors)"
        )

        # Write to results file
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{module_name}: {status}\n")
            f.write(f"  Tests: {result.testsRun}, Failures: {len(result.failures)}, "
                   f"Errors: {len(result.errors)}, Skipped: {len(result.skipped)}\n")

            if result.failures:
                for test, trace in result.failures:  # All failures
                    f.write(f"\n  FAILURE: {test}\n")
                    f.write(f"  {trace}\n")

            if result.errors:
                for test, trace in result.errors:  # All errors
                    f.write(f"\n  ERROR: {test}\n")
                    f.write(f"  {trace}\n")

    except Exception as e:
        print(f"[ERROR] Error loading {module_name}: {e}")
        results_summary.append(f"{module_name}: LOAD ERROR - {str(e)[:50]}")

        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{module_name}: LOAD ERROR\n")
            f.write(f"  {str(e)}\n")

# Print final summary
print("\n" + "="*70)
print("TEST SUMMARY")
print("="*70)
for result_line in results_summary:
    print(f"  {result_line}")
print("="*70)
print(f"Total: {total_tests} tests, {total_failures} failures, "
      f"{total_errors} errors, {total_skipped} skipped")
print("="*70)

# Write summary to file
with open(output_file, 'a', encoding='utf-8') as f:
    f.write("\n" + "="*70 + "\n")
    f.write("SUMMARY\n")
    f.write("="*70 + "\n")
    for result_line in results_summary:
        f.write(f"  {result_line}\n")
    f.write("="*70 + "\n")
    f.write(f"Total: {total_tests} tests, {total_failures} failures, "
           f"{total_errors} errors, {total_skipped} skipped\n")
    f.write("="*70 + "\n")

print(f"\nResults saved to: {output_file}")

# Final status
if total_failures == 0 and total_errors == 0:
    print("\n[PASS] ALL TESTS PASSED!")
