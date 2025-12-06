
with open(r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\runner_debug.log', 'w') as f:
    f.write("DEBUG: STARTING TEST RUNNER SCRIPT\n")

import sys
import os
sys.path.insert(0, r'O:\\Cloud\\Code\\_scripts\\mayatk\\test')
sys.path.insert(0, r'O:\\Cloud\\Code\\_scripts')

import unittest
import importlib.util

# Reload critical modules using ModuleReloader to ensure latest code
try:
    # CRITICAL: Invalidate import caches first
    import importlib
    importlib.invalidate_caches()
    
    # First, clear any cached bytecode
    import os
    import glob
    
    cache_dirs = [
        r'O:\\Cloud\\Code\\_scripts\\pythontk\\pythontk\\img_utils\\__pycache__',
        r'O:\\Cloud\\Code\\_scripts\\mayatk\\mayatk\\mat_utils\\__pycache__',
    ]
    
    for cache_dir in cache_dirs:
        if os.path.exists(cache_dir):
            for pyc_file in glob.glob(os.path.join(cache_dir, '*.pyc')):
                try:
                    os.remove(pyc_file)
                except:
                    pass
    
    from pythontk.core_utils.module_reloader import ModuleReloader
    
    # Create reloader with submodules enabled and verbose output
    reloader = ModuleReloader(include_submodules=True, verbose=2)
    
    # Reload TOP-LEVEL packages first, then subpackages
    packages_to_reload = ['pythontk', 'mayatk']
    total_reloaded = 0
    
    for package_name in packages_to_reload:
        if package_name in sys.modules:
            try:
                print(f"\nReloading {package_name} and all submodules...\n")
                reloaded_modules = reloader.reload(package_name)
                count = len(reloaded_modules)
                total_reloaded += count
                print(f"[OK] Reloaded {count} modules from {package_name}\n")
            except Exception as e:
                print(f"[WARNING] Failed to reload {package_name}: {e}\n")
                import traceback
                traceback.print_exc()
    
    if total_reloaded > 0:
        print(f"[SUCCESS] Total {total_reloaded} module(s) reloaded\n")
    else:
        print("[INFO] No modules needed reloading\n")

        
except ImportError as e:
    print(f"[WARNING] Could not import ModuleReloader: {e}\n")
    print("[INFO] Falling back to basic reload\n")
    # Fallback to basic reload
    import importlib
    importlib.invalidate_caches()
    for module_name in ['pythontk.img_utils.texture_map_factory', 'mayatk.mat_utils.stingray_arnold_shader']:
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
                print(f"[OK] Reloaded {module_name}\n")
            except Exception as e:
                print(f"[WARNING] Failed to reload {module_name}: {e}\n")
except Exception as e:
    print(f"[ERROR] Module reload failed: {e}\n")
    import traceback
    traceback.print_exc()

# Setup results file
output_file = r'O:\\\\Cloud\\\\Code\\\\_scripts\\\\mayatk\\\\test\\\\test_results.txt'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write("="*70 + "\n")
    f.write("MAYATK TEST RESULTS\n")
    f.write("="*70 + "\n\n")

print("\n" + "="*70)
print("MAYATK TEST SUITE")
print("="*70)

test_modules = ['test_stingray_arnold_shader']
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
            rf'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\{module_name}.py'
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
