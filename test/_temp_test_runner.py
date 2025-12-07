
import sys
import os
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

# Reload critical modules using ModuleReloader to ensure latest code
pass

# Setup results file
output_file = r'O:/Cloud/Code/_scripts/mayatk/test/test_results.txt'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write("="*70 + "\n")
    f.write("MAYATK TEST RESULTS\n")
    f.write("="*70 + "\n\n")

print("\n" + "="*70)
print("MAYATK TEST SUITE")
print("="*70)

test_modules = ['test_debug_history']
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
